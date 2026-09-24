"""
trap_variants.py
================
The daily-analysis trapVariants (`E:\\daily-analysis\\trapVariants`,
engine `patterns-pure/find_trap.py`) moved ONE TIMEFRAME DOWN, for ES on the
continuous TradingView series (`render_labels_report._display_h1` /
`_display_m5`; nothing here resamples .scid into bars).

Two families, each the exact rule set of its daily-analysis original with
every timeframe shifted down one step:

  H1 COROLLARY OF THE D1 VARIANTS (V1, V2, V2.5, V3, V4)
      Upstream: D1 levels, D1 wide/inside context, D1 pinbar trigger.
      Here:     H1 levels, H1 wide/inside context, H1 pinbar trigger.
  M5 COROLLARY OF THE H1 VARIANTS (V6, V7, V8, V9)
      Upstream: D1 levels, D1 wide/inside context, H1 pinbar/engulfing trigger.
      Here:     H1 levels, H1 wide/inside context, M5 pinbar/engulfing trigger.

V5 and V6.5/V7.5 are disabled upstream and are not ported. V10/V11 (idf1) are
a different playbook and are not ported either.

THE VARIANTS (bullish written out; bearish is the mirror)
---------------------------------------------------------
Trigger bar = the bar the trap fires on. "Sweeps the level" (SFP) = its low
trades under the level and it closes back above it. "Wick past" = the trigger's
low is under the wide bar's low. "Wide" = range > WIDE_ATR_MULT x ATR.

  V1   prev bar wide; trigger is a hammer that itself sweeps the level.
  V3   V1 with an inside bar between: wide, inside, sweeping hammer.
  V2   prev bar wide AND it swept the level; trigger is a hammer.
  V4   V2 with an inside bar between: wide sweep, inside, hammer.
  V2.5 any of the 3 bars before the trigger swept the level (no shape test on
       it, but its low is under the trigger's low); trigger is a hammer; and
       the target must have been formed at/after that sweep bar.
  (V1-V4 cascade V3 > V1 > V4 > V2 > V2.5, as upstream.)

  V9   H1 context (prev H1 wide, or prev-prev wide + prev inside); an M5
       bullish engulfing that itself sweeps an H1 level.
  V7   same context; an M5 hammer that itself sweeps an H1 level.
  V8   the wide H1 bar swept the H1 level; M5 trigger is a bullish engulfing.
  V6   the wide H1 bar swept the H1 level; M5 trigger is a hammer.
  (V6-V9 cascade V9 > V7 > V8 > V6, as upstream.)

Every variant also needs: the level within NEAR_ATR_MULT x ATR of the
trigger's close and untouched from its formation up to the sweep, and a TARGET
(see below). The variant string is upstream's own format,
`<swept>.<target>.<context>.<trigger>`, with the timeframe tags shifted
(`wideD1` -> `wideH1`, `pinBarSfpH1` -> `pinBarSfpM5`, ...).

Candles: hammer / shooting star are patterns-pure's `find_hammer` /
`find_shooting_star` (the vendored copy -- see "Spike candles" in CLAUDE.md).
No level is attached to the candle, so neither LHPB/LLPB pairing applies: a
hammer is bullish, a shooting star bearish, as upstream. Engulfing, inside bar,
liquidity pool, equal highs/lows, swings and targets are the vendored
`find_engulfing_*`, `find_inside_bar`, `find_LP`, `find_eqh_eql`,
`find_swings`, `find_targets`, unmodified.

LEVELS AND TARGETS
------------------
Level pool (upstream `_collect_levels`, one step down): H1 liquidity pools,
then equal highs/lows, then ZigZag swings, newest first within each kind,
built on the last LEVEL_POOL_BARS CLOSED H1 bars before the trigger.

Target (upstream `_get_target`): `find_targets` with H1 in the D1 slot and
completed D1 sessions in the W1 slot, so its sources shift down too and are
RENAMED to say so: IHH/IHL = an inside H1 bar's high/low (upstream IDH/IDL),
IDH/IDL = an inside DAY's high/low (upstream IWH/IWL); LPH/LPL and EQH/EQL are
H1 clusters. The nearest target on the trade's side within NEAR_ATR_MULT x ATR
of the trigger close wins. As upstream: V1-V4 skip the trigger bar's own
inside-bar target; V6-V9 drop targets already touched earlier in the trigger's
own hour and an inside-bar target a later H1 close has gone through.

ATR is Wilder ATR(ATR_PERIOD = 24) on H1 -- the LTF trapVariants convention
upstream, and what the user asked for -- over the H1 bars closed at the
trigger. The ZigZag inside the level detectors keeps its own library default
(ATR 21): `find_LP` calls `find_swings` with no way to pass a period, and the
swing pool uses the same default so the two agree.

LIFE OF A TRAP
--------------
Point-in-time throughout: a trap exists once its trigger bar has CLOSED.

  phase 1  the next CONFIRMATION_WINDOW (23) bars of the trigger's timeframe:
           the first close above the running high (trigger high, raised by
           each bar) CONFIRMS it (phase 2); a low under the trigger's low
           kills it first (checked before the close, as upstream). No
           confirmation in 23 bars -> expired.
  phase 2  live for up to LIVE_AFTER_CONFIRM (23) more bars, until the
           trigger's low breaks or the target is reached.

Upstream's D1 variants have no confirmation step; the H1 corollary adds the
same 23-bar step as the H1 variants use (H1 is far noisier than D1). Reaching
the target kills a trap in either phase (the move is done); upstream does not
track phase 2 at all. `traps_at` adds the still-forming bar: every M5 bar
closed by the query instant, plus the query price, is checked against the
trigger's low and the target, so nothing after the query is ever read.

This module is a REVIEW aid: its only consumer is the ss_m5_confl2 Bias
column, and nothing here changes which trades that strategy takes.
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import render_labels_report as R     # noqa: E402  (also puts patterns_pure/ on sys.path)

from find_ATR import findATR                                  # noqa: E402
from find_LP import find_LP                                   # noqa: E402
from find_eqh_eql import find_eqh_eql                         # noqa: E402
from find_swings import find_swings                           # noqa: E402
from find_inside_bar import find_inside_bar                   # noqa: E402
from find_engulfing_bullish import find_engulfing_bullish     # noqa: E402
from find_engulfing_bearish import find_engulfing_bearish     # noqa: E402
from find_targets import find_targets                         # noqa: E402
import find_targets as _find_targets_mod                      # noqa: E402

ATR_PERIOD = 24               # H1 ATR period (LTF trapVariants convention)
ATR_WINDOW = 100              # closed H1 bars fed to findATR (Wilder settles well inside this)
WIDE_ATR_MULT = 0.7           # upstream wide_threshold
NEAR_ATR_MULT = 2.5           # upstream threshold: level and target distance from the close
BOXED_RANGE_PTS = 2.5         # upstream ES1! config: 10 ticks x 0.25
LEVEL_POOL_BARS = 500         # closed H1 bars the level pool / targets are built on
CONFIRMATION_WINDOW = 23      # upstream _CONFIRMATION_WINDOW, in the trigger's own bars
LIVE_AFTER_CONFIRM = 23       # bars a confirmed trap stays live (local decision)
LEAD_IN = pd.Timedelta(days=7)  # scan this far before the first query (covers a trap's max life)

H1 = pd.Timedelta(hours=1)
M5 = pd.Timedelta(minutes=5)
_PT = "America/Los_Angeles"

# find_targets' source names, one timeframe down (see the module docstring).
_SOURCE_RENAME = {"IDH": "IHH", "IDL": "IHL", "IWH": "IDH", "IWL": "IDL"}
_INSIDE_BAR_SOURCES = ("IHH", "IHL")


# --------------------------------------------------------------------------
# Series helpers
# --------------------------------------------------------------------------

def _session_key(index):
    """ES trading date of each instant (session 15:00 PT prior day -> 14:00
    PT): shifting PT forward 9h lands a whole session on its closing date.
    Same rule as ss_m5_confl2's `_display_d1`."""
    pt = pd.DatetimeIndex(index).tz_convert(_PT)
    return (pt + pd.Timedelta(hours=9)).normalize().tz_localize(None)


def _d1_from_h1(h1):
    """Daily bars from the H1 series, indexed by each session's LAST H1 bar
    (so 'bars after this day' in find_targets starts at the next session),
    with a `key` column holding the trading date."""
    key = _session_key(h1.index)
    g = h1.groupby(key)
    d1 = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                       "low": g["low"].min(), "close": g["close"].last()})
    last = pd.Series(h1.index, index=key).groupby(level=0).last()
    d1["key"] = d1.index
    d1.index = pd.DatetimeIndex(last.loc[d1["key"]].to_numpy()).tz_convert("UTC")
    return d1


def _utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _atr_at(h1, p):
    """ATR(24) over the H1 bars closed before position p."""
    return float(findATR(h1.iloc[max(0, p - ATR_WINDOW):p], period=ATR_PERIOD))


def _sfps(bar, level, is_low):
    if is_low:
        return float(bar["low"]) < level < float(bar["close"])
    return float(bar["close"]) < level < float(bar["high"])


def _unswept_between(data, level_ts, bar_ts, level, is_low):
    """No bar strictly between `level_ts` and `bar_ts` reached `level`."""
    a = int(data.index.searchsorted(level_ts, side="right"))
    b = int(data.index.searchsorted(bar_ts, side="left"))
    if a >= b:
        return True
    if is_low:
        return not (data["low"].to_numpy()[a:b] <= level).any()
    return not (data["high"].to_numpy()[a:b] >= level).any()


# Per-scan memo for the two cluster detectors. The scan asks them about the
# same trailing H1 window many times over (the level pool of one bar is the
# target frame of the previous one, and every M5 bar of an hour shares one),
# directly and through find_targets. They are pure functions of their frame,
# so a memo keyed on the frame's span and contents changes no answer.
# `scan` clears it on entry.
_MEMO = {}


def _memo(fn):
    def wrapped(data, boxed_range):
        if data.empty:
            return fn(data, boxed_range)
        key = (fn.__name__, data.index[0], data.index[-1], len(data), float(boxed_range),
               float(data["high"].sum()), float(data["low"].sum()))
        if key not in _MEMO:
            _MEMO[key] = fn(data, boxed_range)
        return _MEMO[key].copy()
    wrapped.__name__ = fn.__name__
    return wrapped


find_LP = _memo(find_LP)
find_eqh_eql = _memo(find_eqh_eql)
# find_targets looks both up in its own module namespace; point it at the
# memoised ones (the vendored file itself is untouched).
_find_targets_mod.find_LP = find_LP
_find_targets_mod.find_eqh_eql = find_eqh_eql


def _collect_levels(src):
    """Upstream `_collect_levels`: (bull, bear) level lists, LP > EQ > swing,
    newest first within each."""
    bull, bear = [], []
    for df, kind in ((find_LP(src, BOXED_RANGE_PTS), "LP"),
                     (find_eqh_eql(src, BOXED_RANGE_PTS), "EQ")):
        for _, r in df.iterrows():
            is_high = bool(r["isHigh"])
            prices = list(r["constituent_prices"])
            entry = {"swept_type": kind,
                     "level": float(max(prices) if is_high else min(prices)),
                     "level_ts": max(r["constituent_timestamps"])}
            (bear if is_high else bull).append(entry)
    sh, sl = find_swings(src)
    if len(src):
        # Upstream drops the last-bar-exception swing (XAGUSD 2026-03-25 bug).
        sh = sh.loc[sh.index != src.index[-1]]
        sl = sl.loc[sl.index != src.index[-1]]
    for ts, row in sl.iterrows():
        bull.append({"swept_type": "swing", "level": float(row["low"]), "level_ts": ts})
    for ts, row in sh.iterrows():
        bear.append({"swept_type": "swing", "level": float(row["high"]), "level_ts": ts})
    prio = {"LP": 0, "EQ": 1, "swing": 2}
    bull.sort(key=lambda e: (prio[e["swept_type"]], -e["level_ts"].value))
    bear.sort(key=lambda e: (prio[e["swept_type"]], -e["level_ts"].value))
    return bull, bear


def _raw_targets(h1_win, d1_done, direction):
    """find_targets one timeframe down, sources renamed; DataFrame with
    tgt_ts / price / source / constituents (possibly empty)."""
    if h1_win.empty or d1_done.empty:
        return pd.DataFrame(columns=["tgt_ts", "price", "source", "constituents"])
    raw = find_targets(h1_win, d1_done[["open", "high", "low", "close"]],
                       "long" if direction == "bullish" else "short",
                       BOXED_RANGE_PTS, atr=0)
    if raw.empty:
        return pd.DataFrame(columns=["tgt_ts", "price", "source", "constituents"])
    out = raw.reset_index().rename(columns={"index": "tgt_ts"})
    out["source"] = out["source"].replace(_SOURCE_RENAME)
    return out


def _pick_target(tgts, close, direction, threshold, exclude_ts=None, pre_trigger=None):
    """Upstream `_get_target`'s selection: nearest target on the trade's side
    within `threshold` of `close`, optionally skipping the trigger bar's own
    inside-bar target and any target `pre_trigger` bars already touched.
    Returns (source, price, ts, constituents) or None."""
    if tgts.empty:
        return None
    if exclude_ts is not None:
        tgts = tgts[~(tgts["source"].isin(_INSIDE_BAR_SOURCES) & (tgts["tgt_ts"] == exclude_ts))]
    if direction == "bullish":
        c = tgts[(tgts["price"] > close) & (tgts["price"] - close <= threshold)]
    else:
        c = tgts[(tgts["price"] < close) & (close - tgts["price"] <= threshold)]
    if pre_trigger is not None and not pre_trigger.empty and not c.empty:
        if direction == "bullish":
            c = c[c["price"] > float(pre_trigger["high"].max())]
        else:
            c = c[c["price"] < float(pre_trigger["low"].min())]
    if c.empty:
        return None
    best = c.loc[c["price"].idxmin()] if direction == "bullish" else c.loc[c["price"].idxmax()]
    return (str(best["source"]), float(best["price"]), best["tgt_ts"], list(best["constituents"]))


def _emit(tf, vnum, direction, variant, e, tgt, ts, bar, dur):
    return {"tf": tf, "vnum": vnum, "direction": direction, "variant": variant,
            "trigger_ts": ts, "close_ts": ts + dur,
            "high": float(bar["high"]), "low": float(bar["low"]),
            "close": float(bar["close"]),
            "swept_type": e["swept_type"], "swept_level": float(e["level"]),
            "swept_level_ts": e["level_ts"],
            "magnet_source": tgt[0], "magnet_price": tgt[1], "magnet_ts": tgt[2]}


# --------------------------------------------------------------------------
# H1 corollary of the D1 variants (V1, V2, V2.5, V3, V4)
# --------------------------------------------------------------------------

def _try_h1_d1style(h1, i, direction, levels, tgts_raw, threshold, wide_thr, inside_ts):
    """Upstream `_try_d1` for the H1 bar at position i. `levels` is the pool
    built on the H1 bars before it; `tgts_raw` its raw targets."""
    is_bull = direction == "bullish"
    cur_ts, cur = h1.index[i], h1.iloc[i]
    prev_ts, prev = h1.index[i - 1], h1.iloc[i - 1]
    prev2_ts, prev2 = h1.index[i - 2], h1.iloc[i - 2]
    close = float(cur["close"])
    tgt = _pick_target(tgts_raw, close, direction, threshold, exclude_ts=cur_ts)
    if tgt is None:
        return None
    nearby = [e for e in levels if abs(e["level"] - close) <= threshold]
    if not nearby:
        return None
    win = h1.iloc[max(0, i - LEVEL_POOL_BARS):i + 1]
    prev_wide = float(prev["high"]) - float(prev["low"]) > wide_thr
    prev2_wide = float(prev2["high"]) - float(prev2["low"]) > wide_thr
    prev_inside = prev_ts in inside_ts
    if is_bull:
        past_prev = float(cur["low"]) < float(prev["low"])
        past_prev2 = float(cur["low"]) < float(prev2["low"])
    else:
        past_prev = float(cur["high"]) > float(prev["high"])
        past_prev2 = float(cur["high"]) > float(prev2["high"])

    def fire(vnum, variant, e):
        return _emit("H1", vnum, direction, f'{e["swept_type"]}.{tgt[0]}.{variant}',
                     e, tgt, cur_ts, cur, H1)

    if prev2_wide and prev_inside and past_prev2:                       # V3
        for e in nearby:
            if _sfps(cur, e["level"], is_bull) and \
                    _unswept_between(win, e["level_ts"], cur_ts, e["level"], is_bull):
                return fire("V3", "wideH1.insideH1.pinBarSfpH1", e)
    if prev_wide and past_prev:                                         # V1
        for e in nearby:
            if _sfps(cur, e["level"], is_bull) and \
                    _unswept_between(win, e["level_ts"], cur_ts, e["level"], is_bull):
                return fire("V1", "wideH1.pinBarSfpH1", e)
    if prev2_wide and prev_inside and past_prev2:                       # V4
        for e in nearby:
            if _sfps(prev2, e["level"], is_bull) and \
                    _unswept_between(win, e["level_ts"], prev2_ts, e["level"], is_bull):
                return fire("V4", "wideSfpH1.insideH1.pinBarH1", e)
    if prev_wide and past_prev:                                         # V2
        for e in nearby:
            if _sfps(prev, e["level"], is_bull) and \
                    _unswept_between(win, e["level_ts"], prev_ts, e["level"], is_bull):
                return fire("V2", "wideSfpH1.pinBarH1", e)

    # V2.5 -- upstream's reverse-freshness check: some in-range target (not
    # the trigger's own inside bar) was formed at/after the sweep bar.
    def fresh_target(sfp_ts):
        c = tgts_raw
        if c.empty:
            return False
        if is_bull:
            c = c[(c["price"] > close) & (c["price"] - close <= threshold)]
        else:
            c = c[(c["price"] < close) & (close - c["price"] <= threshold)]
        c = c[~(c["source"].isin(_INSIDE_BAR_SOURCES) & (c["tgt_ts"] == cur_ts))]
        return any(sfp_ts <= ts <= cur_ts for cs in c["constituents"] for ts, _ in cs)

    for n in (1, 2, 3):
        if i - n < 0:
            break
        sfp_ts, sfp_bar = h1.index[i - n], h1.iloc[i - n]
        if is_bull and float(sfp_bar["low"]) >= float(cur["low"]):
            continue
        if not is_bull and float(sfp_bar["high"]) <= float(cur["high"]):
            continue
        forward = any(sfp_ts <= ts <= cur_ts for ts, _ in tgt[3])
        if not (forward or fresh_target(sfp_ts)):
            continue
        for e in nearby:
            if e["level_ts"] >= sfp_ts or not _sfps(sfp_bar, e["level"], is_bull):
                continue
            if not _unswept_between(win, e["level_ts"], sfp_ts, e["level"], is_bull):
                continue
            return fire("V2.5", f"Sfp@-{n}H1.pinBarH1", e)
    return None


def _scan_h1_corollary(h1, d1, masks, lo, hi):
    out = []
    for i in range(max(lo, ATR_PERIOD + 2, 3), hi):
        dirs = [d for d, key in (("bullish", "h1_hammer"), ("bearish", "h1_star"))
                if masks[key][i]]
        if not dirs:
            continue
        atr = _atr_at(h1, i + 1)
        if atr <= 0:
            continue
        prior = h1.iloc[max(0, i - LEVEL_POOL_BARS):i]
        if len(prior) < 30:
            continue
        bull_lv, bear_lv = _collect_levels(prior)
        # Completed sessions only: upstream's W1 slot drops the running week.
        d1_done = d1[d1["key"] < _session_key([h1.index[i]])[0]]
        win = h1.iloc[max(0, i + 1 - LEVEL_POOL_BARS):i + 1]
        for direction in dirs:
            tgts = _raw_targets(win, d1_done, direction)
            t = _try_h1_d1style(h1, i, direction,
                                bull_lv if direction == "bullish" else bear_lv, tgts,
                                NEAR_ATR_MULT * atr, WIDE_ATR_MULT * atr, masks["h1_inside"])
            if t:
                out.append(t)
    return out


# --------------------------------------------------------------------------
# M5 corollary of the H1 variants (V6, V7, V8, V9)
# --------------------------------------------------------------------------

def _scan_m5_corollary(h1, d1, m5, masks, t0, t1):
    out = []
    m5_win = m5.loc[(m5.index >= t0) & (m5.index < t1)]
    if m5_win.empty:
        return out
    hours = m5_win.index.floor("h")
    trig = (masks["m5_hammer"] | masks["m5_star"] | masks["m5_bueng"] | masks["m5_beeng"])
    trig_win = trig.reindex(m5_win.index, fill_value=False).to_numpy()
    for hour in pd.unique(hours[trig_win]):
        p = int(h1.index.searchsorted(hour))          # closed H1 bars: h1.iloc[:p]
        if p < max(ATR_PERIOD + 2, 30):
            continue
        atr = _atr_at(h1, p)
        if atr <= 0:
            continue
        wide_thr, threshold = WIDE_ATR_MULT * atr, NEAR_ATR_MULT * atr
        prev_ts, prev = h1.index[p - 1], h1.iloc[p - 1]
        prev2_ts, prev2 = h1.index[p - 2], h1.iloc[p - 2]
        prev_wide = float(prev["high"]) - float(prev["low"]) > wide_thr
        prev2_wide = float(prev2["high"]) - float(prev2["low"]) > wide_thr
        prev_inside = prev_ts in masks["h1_inside"]
        if not (prev_wide or (prev2_wide and prev_inside)):
            continue
        pool = h1.iloc[max(0, p - LEVEL_POOL_BARS):p]
        levels = dict(zip(("bullish", "bearish"), _collect_levels(pool)))
        d1_done = d1[d1["key"] < _session_key([hour])[0]]
        hour_bars = m5.iloc[int(m5.index.searchsorted(hour)):
                            int(m5.index.searchsorted(hour + H1))]
        tgt_cache = {}
        for j in range(len(hour_bars)):
            cur_ts, cur = hour_bars.index[j], hour_bars.iloc[j]
            close = float(cur["close"])
            for direction in ("bullish", "bearish"):
                is_bull = direction == "bullish"
                pin = cur_ts in (masks["m5_hammer_ts"] if is_bull else masks["m5_star_ts"])
                eng = cur_ts in (masks["m5_bueng_ts"] if is_bull else masks["m5_beeng_ts"])
                if not (pin or eng):
                    continue
                lv = levels[direction]
                if not lv:
                    continue
                if direction not in tgt_cache:
                    tgt_cache[direction] = _raw_targets(pool, d1_done, direction)
                tgt = _pick_target(tgt_cache[direction], close, direction, threshold,
                                   pre_trigger=hour_bars.iloc[:j])
                # Upstream's inside-bar target validity gate.
                if tgt is not None and tgt[0] in _INSIDE_BAR_SOURCES and tgt[2] in pool.index:
                    ib = pool.loc[tgt[2]]
                    after = pool.loc[pool.index > tgt[2]]
                    if is_bull and (after["close"] < float(ib["low"])).any():
                        tgt = None
                    elif not is_bull and (after["close"] > float(ib["high"])).any():
                        tgt = None
                if tgt is None:
                    continue
                if is_bull:
                    past_prev = float(cur["low"]) < float(prev["low"])
                    past_prev2 = float(cur["low"]) < float(prev2["low"])
                else:
                    past_prev = float(cur["high"]) > float(prev["high"])
                    past_prev2 = float(cur["high"]) > float(prev2["high"])
                eng_label = "buEng" if is_bull else "beEng"
                t = _try_m5_h1style(cur_ts, cur, direction, lv, tgt, threshold, pool, m5,
                                    pin, eng, eng_label, prev_ts, prev, prev2_ts, prev2,
                                    prev_wide, prev2_wide, prev_inside, past_prev, past_prev2)
                if t:
                    out.append(t)
    return out


def _try_m5_h1style(cur_ts, cur, direction, lv, tgt, threshold, pool, m5, pin, eng,
                    eng_label, prev_ts, prev, prev2_ts, prev2, prev_wide, prev2_wide,
                    prev_inside, past_prev, past_prev2):
    """Upstream `_try_h1_all`'s V9 > V7 > V8 > V6 cascade for one M5 bar."""
    is_bull = direction == "bullish"
    close = float(cur["close"])

    def fire(vnum, variant, e):
        return _emit("M5", vnum, direction, f'{e["swept_type"]}.{tgt[0]}.{variant}',
                     e, tgt, cur_ts, cur, M5)

    def self_sweep(e):
        lvl = e["level"]
        return (abs(lvl - close) <= threshold and _sfps(cur, lvl, is_bull)
                and _unswept_between(pool, e["level_ts"], cur_ts, lvl, is_bull)
                and _unswept_between(m5, e["level_ts"] + H1, cur_ts, lvl, is_bull))

    def context():
        if prev_wide and past_prev:
            return "wideH1"
        if prev2_wide and prev_inside and past_prev2:
            return "wideH1.insideH1"
        return None

    for vnum, ok, suffix in (("V9", eng, f"{eng_label}SfpM5"), ("V7", pin, "pinBarSfpM5")):
        if not ok:
            continue
        for e in lv:
            if not self_sweep(e):
                continue
            ctx = context()
            if ctx is None:
                continue
            return fire(vnum, f"{ctx}.{suffix}", e)

    for vnum, ok, suffix in (("V8", eng, f"{eng_label}M5"), ("V6", pin, "pinBarM5")):
        if not ok:
            continue
        for ctx_ok, wide_ts, wide_bar, ctx in (
                (prev_wide and past_prev, prev_ts, prev, "wideSfpH1"),
                (prev2_wide and prev_inside and past_prev2, prev2_ts, prev2, "wideSfpH1.insideH1")):
            if not ctx_ok:
                continue
            for e in lv:
                lvl = e["level"]
                if abs(lvl - close) > threshold or not _sfps(wide_bar, lvl, is_bull):
                    continue
                if not _unswept_between(pool, e["level_ts"], wide_ts, lvl, is_bull):
                    continue
                return fire(vnum, f"{ctx}.{suffix}", e)
    return None


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def _lifecycle(t, bars, dur):
    """Adds confirm_ts / end_ts / end_reason to trap `t` from the bars of its
    own timeframe (see the module docstring). end_ts None = still live at the
    end of the data."""
    is_bull = t["direction"] == "bullish"
    pos = int(bars.index.searchsorted(t["close_ts"]))
    fwd = bars.iloc[pos:pos + CONFIRMATION_WINDOW + LIVE_AFTER_CONFIRM]
    hi, lo, magnet = t["high"], t["low"], t["magnet_price"]
    run_hi, run_lo = hi, lo
    t.update(confirm_ts=None, end_ts=None, end_reason=None)
    since_confirm = 0
    for k, (ts, b) in enumerate(fwd.iterrows()):
        bh, bl, bc = float(b["high"]), float(b["low"]), float(b["close"])
        end = ts + dur
        if (bl < lo) if is_bull else (bh > hi):
            t.update(end_ts=end, end_reason="stopped")
            return t
        if (bh >= magnet) if is_bull else (bl <= magnet):
            t.update(end_ts=end, end_reason="target")
            return t
        if t["confirm_ts"] is None:
            if (bc > run_hi) if is_bull else (bc < run_lo):
                t["confirm_ts"] = end
            elif k + 1 >= CONFIRMATION_WINDOW:
                t.update(end_ts=end, end_reason="expired")
                return t
            run_hi, run_lo = max(run_hi, bh), min(run_lo, bl)
        else:
            since_confirm += 1
            if since_confirm >= LIVE_AFTER_CONFIRM:
                t.update(end_ts=end, end_reason="aged")
                return t
    return t


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def _masks(h1, m5):
    hm = R._pp_find_hammer(h1, atr=0.0).index
    st = R._pp_find_shooting_star(h1, atr=0.0).index
    m5_ham = R._pp_find_hammer(m5, atr=0.0).index
    m5_star = R._pp_find_shooting_star(m5, atr=0.0).index
    m5_bu = find_engulfing_bullish(m5).index
    m5_be = find_engulfing_bearish(m5).index
    return {
        "h1_hammer": h1.index.isin(hm), "h1_star": h1.index.isin(st),
        "h1_inside": set(find_inside_bar(h1).index),
        "m5_hammer": pd.Series(m5.index.isin(m5_ham), index=m5.index),
        "m5_star": pd.Series(m5.index.isin(m5_star), index=m5.index),
        "m5_bueng": pd.Series(m5.index.isin(m5_bu), index=m5.index),
        "m5_beeng": pd.Series(m5.index.isin(m5_be), index=m5.index),
        "m5_hammer_ts": set(m5_ham), "m5_star_ts": set(m5_star),
        "m5_bueng_ts": set(m5_bu), "m5_beeng_ts": set(m5_be),
    }


SCAN_CHUNK = pd.Timedelta(days=14)   # trigger span handed to one worker
SCAN_WORKERS = 4                      # repo-wide cap on parallel processes


def _scan_span(c0, c1, h1=None, m5=None):
    """Traps whose trigger bar opens in [c0, c1), with their lifecycles. Each
    trap depends only on bars up to its trigger (detection) and after it
    (lifecycle), all read from the full series, so any split of the range
    into spans gives the same traps as one pass."""
    h1 = R._display_h1() if h1 is None else h1
    m5 = R._display_m5() if m5 is None else m5
    _MEMO.clear()
    masks = _masks(h1, m5)
    d1 = _d1_from_h1(h1)
    lo, hi = int(h1.index.searchsorted(c0)), int(h1.index.searchsorted(c1))
    traps = [_lifecycle(t, h1, H1) for t in _scan_h1_corollary(h1, d1, masks, lo, hi)]
    traps += [_lifecycle(t, m5, M5) for t in _scan_m5_corollary(h1, d1, m5, masks, c0, c1)]
    _MEMO.clear()
    return traps


def scan(start, end, h1=None, m5=None, verbose=True, workers=SCAN_WORKERS):
    """Every trap (both families) whose trigger bar opens in [start - LEAD_IN,
    end], each with its lifecycle. List of dicts, sorted by trigger close.

    Split into SCAN_CHUNK spans over up to `workers` processes, each loading
    the display series itself -- so a caller passing its own `h1`/`m5` gets a
    single in-process pass instead."""
    t0, t_end = _utc(start) - LEAD_IN, _utc(end) + M5
    spans = []
    c = t0
    while c < t_end:
        spans.append((c, min(c + SCAN_CHUNK, t_end)))
        c += SCAN_CHUNK
    if h1 is not None or m5 is not None or workers <= 1 or len(spans) == 1:
        traps = [t for c0, c1 in spans for t in _scan_span(c0, c1, h1, m5)]
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(workers, len(spans))) as ex:
            traps = [t for part in ex.map(_scan_span, *zip(*spans)) for t in part]
    traps.sort(key=lambda t: (t["close_ts"], t["tf"], t["direction"]))
    if verbose:
        n_h1 = sum(t["tf"] == "H1" for t in traps)
        print(f"  [traps] {n_h1} H1-corollary + {len(traps) - n_h1} M5-corollary trap(s) "
              f"triggered in [{t0}, {t_end})", flush=True)
    return traps


def traps_at(traps, ts, price=None, m5=None):
    """The traps from `scan` still LIVE at instant `ts` (see the module
    docstring), each a copy with `phase` (1 or 2) and `label`. `price`, the
    caller's own price at `ts` (e.g. a fill), also counts as traded."""
    m5 = R._display_m5() if m5 is None else m5
    ts = _utc(ts)
    out = []
    for t in traps:
        if t["close_ts"] > ts:
            break
        if t["end_ts"] is not None and t["end_ts"] <= ts:
            continue
        is_bull = t["direction"] == "bullish"
        # M5 bars opening at/after the trigger's close and closed by `ts`.
        seen = m5.iloc[int(m5.index.searchsorted(t["close_ts"])):
                       int(m5.index.searchsorted(ts - M5, side="right"))]
        extra = [float(price)] if price is not None else []
        lo = min([float(seen["low"].min())] * bool(len(seen)) + extra, default=np.inf)
        hi = max([float(seen["high"].max())] * bool(len(seen)) + extra, default=-np.inf)
        if is_bull and (lo < t["low"] or hi >= t["magnet_price"]):
            continue
        if not is_bull and (hi > t["high"] or lo <= t["magnet_price"]):
            continue
        phase = 2 if t["confirm_ts"] is not None and t["confirm_ts"] <= ts else 1
        out.append({**t, "phase": phase,
                    "label": f'{t["vnum"]}\u00b7{t["tf"]}' + ("\u2713" if phase == 2 else "")})
    out.sort(key=lambda t: t["close_ts"], reverse=True)
    return out
