"""
render_range_breakout_report.py
===============================
H1 RANGE BREAKOUT -> M5 LXPB RETEST. Rules set by the user 2026-09-25:

  1. RANGE. Every patterns-pure find_range candidate on the H1 series (inner
     and outer alike). Its box is the point-in-time box (find_range re-run on
     the bars up to the last H1 close) while find_range still tracks it, and
     the last box after a break or a cancel. A merged range stops at the merge
     (its outer range carries on).
  2. EDGES. Each box edge takes at most ONE trade and stays valid until it
     has taken it -- however long that is.
  3. BREAKOUT. An M5 candle that opens inside the box, closes beyond the edge
     and is strong: range >= --bo-range-atr x ATR(21) of the M5 series as of
     the previous close, body >= --bo-body of its range. It ARMS the edge.
  4. FAILED BREAK. If an H1 candle closes back inside the box after the
     breakout (the H1 candle holding the breakout candle included) before the
     trade fills, the break failed: that edge is finished, no trade. The
     watch starts at the range's own H1 break candle on that side when that
     comes first: an H1 break made of weak M5 candles arms nothing, but an H1
     close back inside after it still finishes the edge (user, 2026-09-25).
     The same goes for ANY H1 close beyond an edge, not just the range's own
     break: a range that broke down and later closed above its top and back
     inside has spent its top edge too (a month-old range took a trade that
     way, 2026-01-09). A failure never touches the opposite edge.
  5. ENTRY. The first retest (M5 level ledger, plain-P0s TRACKED) of an M5
     LLPB (breakout below -> short) / LHPB (breakout above -> long) that
     FORMED during the range (from its first candle up to the breakout) and
     whose P1 is a breakout candle of that edge. Filled as a plain limit at
     the level's price on real ticks (render_ss_confl_finetune_report.
     find_alt_fill, no chasing).
  6. STOP. One tick beyond that level's own P1 candle: above its high for a
     short, below its low for a long. A live toggle in the report (off by
     default, threshold 2.5 / 3 / 4pt) instead walks back from P1 to the most
     recent earlier same-type P1 candle giving at least the threshold, when
     the P1 stop is tighter than that (_walk_back_stop).
  7. TARGET. If the retest came within N hours of P1 (N is a live control in
     the report, default --quick-hours): the lowest low (short) / highest
     high (long) since P1 -- the M5 candles from P1 (included), then the real
     trades inside the retest candle before the touch. Otherwise ss_m5_confl2's opposite-M5-zigzag target, and failing
     that its swing-extreme target -- called, not restated. Both brackets are
     precomputed and tick-resolved for every trade, so changing N in the
     browser only picks between them.
  8. ONE TRADE PER LEVEL. Nested / overlapping ranges that pick the same M5
     level give one trade, kept under the range that started first.
  9. ONE TRADE AT A TIME (live toggle, on by default). In fill order, a
     trade filling while an earlier counted trade is still open is NO TRADE
     (another trade on); its edge is still spent.

Everything downstream of the trade itself -- tick-accurate exit resolution
(render_stop_target_report.resolve_trades via ss_m5_confl2's
_resolve_target_mode), MAE/MFE, trade management, liquidity / volume-spike /
H1-bias / trap tags, the row layout, the chart stack (D1, H1, M5, 1s trio,
bid/ask volume, 1-minute, footprints) and the live filter panel -- is
ss_m5_confl2's own code, called rather than copied. This module supplies the
trades, and patches the rendered page only where this strategy differs
(title, write-up, summary boxes, the target-rule row, neutral filter
defaults, profit factor and $ PnL in the stats strip).

Hard rules of ss_m5_confl2 that are NOT part of this strategy are off by
default and can be switched on: --eod-flat, --news-pull-window.

Usage:
    python range_breakout/render_range_breakout_report.py --start 2026-01-01 --end 2026-12-31 \\
        --output public/reports/range_breakout/2026.html --workers 4
    python range_breakout/render_range_breakout_report.py --max-rows 5 --output <tmp>.html
"""
import os
import sys
import gzip
import time
import pickle
import shutil
import argparse
import bisect
import multiprocessing as mp

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for _p in (_REPO, os.path.join(_REPO, "ss_m5_confl2"), os.path.join(_REPO, "lfg")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import render_labels_report as R                 # noqa: E402  (puts patterns_pure on sys.path)
import render_stop_target_report as SR           # noqa: E402
import render_ss_confl_finetune_report as SF      # noqa: E402
import analyze_breakout_exits_1min as M           # noqa: E402
import lxpb_levels_cache as LC                    # noqa: E402
import trade_management as TM                     # noqa: E402
import liquidity as LQ                            # noqa: E402
import volume_spike as VS                         # noqa: E402
import render_m5_confl2_report as C2              # noqa: E402
import lfg_study as LF                            # noqa: E402  (point-in-time find_range boxes)
from find_ATR import findATR                      # noqa: E402

PT = "America/Los_Angeles"
H1 = pd.Timedelta(hours=1)
TICK = R.TICK_SIZE_DEFAULT
BO_RANGE_ATR_DEFAULT = 1.5
BO_BODY_DEFAULT = 0.5
QUICK_HOURS_DEFAULT = 3.0
WIDEN_PTS = (2.5, 3.0, 4.0)      # "widen a P1 stop tighter than this" choices in the page
FILL_WINDOW_HOURS = 1.0          # tick search for the fill, from the ledger's retest candle
RANGE_LOOKBACK = pd.Timedelta(days=365)   # an edge can stay armed for months
MODE_QUICK = "since-p1"
MODE_STRUCT = "structure"
BOX_COLOR = "#fbbf24"
R_DOLLARS = 100.0
MES_COMMISSION_RT = 0.61
OUT_DIR = os.path.join(_REPO, "public", "reports", "range_breakout")


# --------------------------------------------------------------------------
# Data (lazy: spawned worker processes import this module)
# --------------------------------------------------------------------------

_CACHE = {}


def _h1():
    if "h1" not in _CACHE:
        _CACHE["h1"] = R._display_h1()
    return _CACHE["h1"]


def _m5():
    if "m5" not in _CACHE:
        _CACHE["m5"] = LC.m5_bars_continuous()
    return _CACHE["m5"]


def _ledger(tracked):
    key = "lt" if tracked else "lu"
    if key not in _CACHE:
        _CACHE[key] = LC.m5_levels(verbose=False, plain_p0=(LC.PLAIN_P0_TRACKED if tracked
                                                             else LC.PLAIN_P0_UNTRACKED))
    return _CACHE[key]


def _m5_arrays():
    """M5 OHLC as arrays, plus each bar's range / ATR(21) as of the previous
    close and its body share. ATR is patterns-pure findATR's own formula as a
    running series, checked against findATR itself."""
    if "arr" in _CACHE:
        return _CACHE["arr"]
    m5, h = _m5(), _h1()
    tr = pd.concat([m5.high - m5.low, (m5.high - m5.close.shift()).abs(),
                    (m5.low - m5.close.shift()).abs()], axis=1).max(axis=1).iloc[1:]
    atr = tr.ewm(alpha=1 / 21, adjust=False).mean()
    for k in (500, 5000, 50000):
        if abs(atr.iloc[k - 2] - findATR(m5.iloc[:k])) > 1e-9:
            raise RuntimeError("running ATR series disagrees with patterns-pure findATR")
    atr_prev = atr.shift().reindex(m5.index).to_numpy()
    o, hi, lo, c = (m5[x].to_numpy() for x in ("open", "high", "low", "close"))
    rng = hi - lo
    body = np.where(rng > 0, np.abs(c - o) / np.where(rng > 0, rng, 1), 0.0)
    _CACHE["arr"] = dict(t=m5.index, o=o, h=hi, l=lo, c=c, rng_atr=rng / atr_prev, body=body,
                         hpos=h.index.searchsorted(m5.index, side="right") - 1,
                         h_close=h.close.to_numpy())
    return _CACHE["arr"]


# --------------------------------------------------------------------------
# Setups: ranges, edges, breakouts, failures, entry levels
# --------------------------------------------------------------------------

def _box_arrays(r, n_h):
    """(j0, hi[], lo[]): the box for every H1 candle from the first tradable
    one to the end of data -- point-in-time while find_range tracks the
    range, its last box after a break or cancel, NaN after a merge."""
    j0 = r["js"][0]
    hi = np.full(n_h - j0, np.nan)
    lo = np.full(n_h - j0, np.nan)
    for j, (bh, bl) in r["boxes"].items():
        hi[j - j0], lo[j - j0] = bh, bl
    last = r["js"][-1] - j0
    if r["status"] in ("broken", "cancelled"):
        hi[last + 1:], lo[last + 1:] = r["hi"], r["lo"]
    return j0, hi, lo


def _walk_edge(r, side, j0, bhi, blo, ra, bd, levels):
    """One edge of one range -> a setup dict. outcome: 'trade', 'failed',
    'never broken' or 'armed' (broken, no retest yet)."""
    a, h = _m5_arrays(), _h1()
    is_long = side == "up"
    lt = "LHPB" if is_long else "LLPB"
    i0 = a["t"].searchsorted(h.index[j0])
    hp = a["hpos"][i0:] - j0
    ok = (hp >= 0) & (hp < len(bhi))
    hp = np.clip(hp, 0, len(bhi) - 1)
    ehi, elo = bhi[hp], blo[hp]
    o, c = a["o"][i0:], a["c"][i0:]
    beyond = (c > ehi) if is_long else (c < elo)
    strong = (a["rng_atr"][i0:] >= ra) & (a["body"][i0:] >= bd)
    cand = np.flatnonzero(ok & ~np.isnan(ehi) & (o >= elo) & (o <= ehi) & beyond & strong)
    s = dict(range=r, side=side, level_type=lt, is_long=is_long, outcome="never broken")
    # The failed-break watch starts at whichever comes first: the H1 candle
    # holding the first strong M5 breakout, the range's own H1 break candle on
    # this side (a gap break included), or ANY H1 close beyond this edge -- a
    # break on weak M5 candles still takes price through the edge, so an H1
    # close back inside after it finishes the edge too. That covers the edge
    # the range did not break on: after breaking down, price that later
    # closes above the top and comes back inside has spent the top edge.
    hc = a["h_close"][j0:j0 + len(bhi)]
    with np.errstate(invalid="ignore"):
        past = np.flatnonzero((hc > bhi) if is_long else (hc < blo))
    brks = ([r["e"]] if r["status"] == "broken" and r["break_dir"] == side else []) + \
           ([j0 + past[0]] if len(past) else [])
    starts = ([a["hpos"][i0 + cand[0]]] if len(cand) else []) + brks
    if not starts:
        return s
    if brks:
        s["t_break"] = h.index[min(brks)]
    fail_t = None
    for j in range(min(starts), len(h)):
        bh, bl = bhi[j - j0], blo[j - j0]
        if np.isnan(bh):
            fail_t = h.index[j]          # merged away
            break
        if bl <= a["h_close"][j] <= bh:
            fail_t = h.index[j] + H1
            break
    s["fail_t"] = fail_t
    if fail_t is not None:
        cand = cand[a["t"][i0 + cand] < fail_t]
    if not len(cand):
        if fail_t is not None:
            s["outcome"] = "failed"      # H1 broke, came back inside before any strong M5 breakout
        return s
    k1 = i0 + cand[0]
    s.update(t_bo=a["t"][k1], edge=float(ehi[cand[0]] if is_long else elo[cand[0]]),
             box=(float(ehi[cand[0]]), float(elo[cand[0]])),
             bo_rng_atr=float(a["rng_atr"][k1]), bo_body=float(a["body"][k1]))
    bo_times = a["t"][i0 + cand]
    lv = levels[(levels["type"] == lt) & levels["breakout_time"].isin(bo_times)
                & (levels["formation_time"] >= r["start"]) & (levels["fate"] == "retested")]
    if fail_t is not None:
        lv = lv[lv["retest_time"] < fail_t]
    if lv.empty:
        s["outcome"] = "failed" if fail_t is not None else "armed"
        return s
    lv = lv[lv["retest_time"] == lv["retest_time"].min()]
    s.update(outcome="trade", level=lv.sort_values("price").iloc[-1 if is_long else 0])
    return s


def find_setups(start, end, ra, bd):
    """(trades, edge_counts): every trade whose entry level was retested in
    [start, end], in retest order, and how every range edge in scope ended.
    Nested / overlapping ranges often break on the same M5 candle and so pick
    the same M5 level: that is ONE trade, kept under the range that started
    first (the other copies are counted as 'duplicate')."""
    h = _h1()
    levels = _ledger(True)
    levels = levels[levels["breakout_time"].notna()]
    ranges = LF.ranges_with_boxes(h, start - RANGE_LOOKBACK)
    trades, counts = [], {}
    for r in ranges:
        j0, bhi, blo = _box_arrays(r, len(h))
        for side in ("down", "up"):
            s = _walk_edge(r, side, j0, bhi, blo, ra, bd, levels)
            if s["outcome"] == "trade":
                if start <= s["level"]["retest_time"] <= end:
                    trades.append(s)
                    counts["trade"] = counts.get("trade", 0) + 1
            elif s["outcome"] != "never broken" and start <= s.get("t_bo", s.get("t_break")) <= end:
                counts[s["outcome"]] = counts.get(s["outcome"], 0) + 1
            elif s["outcome"] == "never broken" and start <= r["confirm"] <= end:
                counts["never broken"] = counts.get("never broken", 0) + 1
    trades.sort(key=lambda s: (s["level"]["retest_time"], s["range"]["start"]))
    seen, kept = set(), []
    for s in trades:
        lv = s["level"]
        key = (lv["type"], float(lv["price"]), lv["formation_time"])
        if key in seen:
            counts["duplicate"] = counts.get("duplicate", 0) + 1
            continue
        seen.add(key)
        kept.append(s)
    return kept, counts


# --------------------------------------------------------------------------
# One trade -> one ss_m5_confl2-shaped result
# --------------------------------------------------------------------------

def _since_p1_target(lv, is_long, fill_price, touch):
    """(price, info) for the extreme since P1 up to the fill: the M5 candles
    from P1 (included) to the retest candle, then the real trades inside the
    retest candle BEFORE the touch -- a new low (short) / high (long) made in
    that candle on the way into the retest counts. (None, None) if not
    favourable."""
    m5 = _m5()
    retest = pd.Timestamp(lv["retest_time"])
    w = m5[(m5.index >= lv["breakout_time"]) & (m5.index < retest)]
    best_t, best = None, None
    if not w.empty:
        best_t = w["high"].idxmax() if is_long else w["low"].idxmin()
        best = float(w.at[best_t, "high"] if is_long else w.at[best_t, "low"])
    ticks = R._ticks_for_window(retest, touch) if touch > retest else None
    if ticks is not None and not ticks.empty:
        ticks = ticks[(ticks.index >= retest) & (ticks.index < touch)]
    if ticks is not None and not ticks.empty:
        offset, _ = R._offset_for_ts(retest)
        px = ticks["Close"]
        t = px.idxmax() if is_long else px.idxmin()
        v = float(px.loc[t]) + offset
        if best is None or (v > best if is_long else v < best):
            best_t, best = t, v
    if best is None or ((best - fill_price) if is_long else (fill_price - best)) < TICK:
        return None, None
    return best, {"src": "extreme_since_p1", "level": None, "pivot_time": best_t,
                  "pivot_price": best, "pivot_kind": "high" if is_long else "low"}


def _walk_back_stop(lt, is_long, fill_price, p1_time, min_pts):
    """(stop, row, 'm5_wide_p1'): walking back from this level's own P1, the
    most recent EARLIER M5 P1 (breakout) candle of a same-type level within
    +/-DYNAMIC_STOP_RADIUS_PTS of the fill, in the WIDER_STOP_LOOKBACK before
    P1, whose extreme puts the stop at least `min_pts` from the fill (one
    tick beyond it). None if there is none. ss_m5_confl2's _wider_stop_p1,
    anchored at P1 instead of at the fill so it only ever walks back."""
    ledger = _ledger(False)
    p1 = pd.Timestamp(p1_time)
    bt = ledger["breakout_time"]
    cand = ledger[(ledger["type"] == lt) & (bt < p1) & (bt >= p1 - C2.WIDER_STOP_LOOKBACK)
                  & ((ledger["price"] - fill_price).abs() <= C2.DYNAMIC_STOP_RADIUS_PTS)]
    if cand.empty:
        return None
    ext = "breakout_low" if is_long else "breakout_high"
    stops = cand[ext] + (-TICK if is_long else TICK)
    ok = (stops <= fill_price - min_pts) if is_long else (stops >= fill_price + min_pts)
    cand = cand[ok.to_numpy()]
    if cand.empty:
        return None
    latest = cand[cand["breakout_time"] == cand["breakout_time"].max()]
    best = latest.loc[latest[ext].idxmin() if is_long else latest[ext].idxmax()]
    return float(best[ext] + (-TICK if is_long else TICK)), best, "m5_wide_p1"


def _structure_target(lt, is_long, fill_price, touch, lv):
    ledger = _ledger(False)
    px, info = C2._opposite_m5_zz_target(ledger, lt, fill_price, is_long, touch,
                                         lv["breakout_time"], lv["retest_time"])
    if px is None:
        px, info = C2._swing_extreme_target(lt, fill_price, is_long, touch,
                                            lv["breakout_time"], lv["retest_time"])
    return px, info


def pick_mode(modes, p1_p2_minutes, quick_hours, widen_pts=None):
    """Mode key '<target>|<stop>' for one row. Target: the since-P1 extreme
    when the retest came within `quick_hours` of P1, else the structure
    target. Stop: the P1-candle stop, or with `widen_pts` set and the P1
    stop closer than that, the walked-back one ('w<pts>') when one exists.
    Mirrored in the page's JS (pickTargetMode)."""
    has = lambda t: f"{t}|p1" in modes
    if has(MODE_QUICK) and p1_p2_minutes <= quick_hours * 60:
        tgt = MODE_QUICK
    elif has(MODE_STRUCT):
        tgt = MODE_STRUCT
    else:
        return None
    if widen_pts is not None and f"{tgt}|w{widen_pts:g}" in modes:
        return f"{tgt}|w{widen_pts:g}"
    return f"{tgt}|p1"


def process_setup(i, s, args):
    lv = s["level"]
    r = s["range"]
    lt, is_long = s["level_type"], s["is_long"]
    own = float(lv["price"])
    retest_time = pd.to_datetime(lv["retest_time"], utc=True)
    res = {
        "i": i, "row": lv, "level_type": lt, "is_long": is_long,
        "seg_idx": R._contract_index_for(retest_time),
        "group_n": 1, "cluster_size": 1,
        "cluster_members": [s["box"][1], s["box"][0]],      # shown as the H1 range column
        "cluster_member_formations": [pd.Timestamp(lv["formation_time"])],
        "own_price": own, "alt_price": own, "alt_source": "own",
        "alt_formation_time": lv["formation_time"], "alt_end_time": lv["retest_time"],
        "entry_m5_level": None, "improved": False, "swerve": None, "crest_refine": None,
        "dyn_tags": [], "filled": False,
        "setup": {"range_id": r["id"], "range_start": r["start"], "range_confirm": r["confirm"],
                  "range_end": r["end"],
                  "range_status": r["status"], "inner": r["outer"] is not None,
                  "side": s["side"], "box": s["box"], "edge": s["edge"], "t_bo": s["t_bo"],
                  "bo_rng_atr": s["bo_rng_atr"], "bo_body": s["bo_body"]},
    }
    res["p1_range_ratio_by_window"] = C2._m5_p1_range_ratio_by_window(lv["breakout_time"])
    k = C2.M5_RANGE_RATIO_WINDOW_DEFAULT - 1
    res["p1_range_ratio"] = (res["p1_range_ratio_by_window"][k]
                             if 0 <= k < len(res["p1_range_ratio_by_window"]) else None)
    res["pre_p1_er_by_k"] = C2._pre_p1_er_by_k(lv["breakout_time"])
    res["pre_p1_box_by_n"] = C2._pre_p1_box_by_n(lv["breakout_time"])
    res["fresh_visits"] = C2._fresh_price_visits(lv["formation_time"], own, is_long)
    res["p1_p2_day_gap"] = TM.trading_day_gap(lv["breakout_time"], lv["retest_time"])
    res["p1_p2_h1_gap"] = TM.h1_bar_gap(lv["breakout_time"], lv["retest_time"])
    res["p1_p2_minutes"] = (retest_time - pd.to_datetime(lv["breakout_time"], utc=True)
                            ).total_seconds() / 60.0

    touch, fill_price = SF.find_alt_fill(retest_time, own, is_long, lt, FILL_WINDOW_HOURS,
                                         pegged=False)
    if touch is None:
        res["fail_reason"] = "unfilled_within_window"
        return res
    if args.news_pull_window and SF._in_news_pull_window(touch):
        res.update(fail_reason="news_pull_blocked", touch_time_alt=touch)
        return res
    if args.eod_flat and TM.entry_blocked(touch):
        res.update(fail_reason="eod_entry_blocked", touch_time_alt=touch)
        return res
    res["fill_price"] = fill_price
    chase = fill_price - own if is_long else own - fill_price
    res["chased_pts"] = max(0.0, chase)
    res["price_improvement_pts"] = max(0.0, -chase)

    bars = SF.build_minute_bars(touch)
    if bars is None or bars.empty:
        res["fail_reason"] = "no_tick_data_after_fill"
        return res
    if args.liquidity_gate:
        blocked, liq = LQ.gate(touch, window_minutes=args.liq_window_minutes,
                               wide_spread_share_max=args.liq_wide_spread_share)
        res["liquidity"] = liq
        if blocked:
            res["dyn_tags"].append("low_liquidity")
    if args.volume_spike_check:
        spiked, vspike = VS.detect(touch, lt, core_window_seconds=args.volume_spike_core_seconds,
                                   baseline_window_seconds=args.volume_spike_baseline_seconds,
                                   ratio_threshold=args.volume_spike_ratio,
                                   min_peak=args.volume_spike_min_peak)
        res["volume_spike"] = vspike
        if spiked:
            res["dyn_tags"].append("volume-spike")

    # STOP: one tick beyond the level's own P1 candle; the walked-back
    # variants (a live toggle in the page) only where the P1 stop is tighter
    # than their threshold and an older P1 gives room.
    stop_price = (float(lv["breakout_low"]) - TICK) if is_long else (float(lv["breakout_high"]) + TICK)
    stop_pts = (fill_price - stop_price) if is_long else (stop_price - fill_price)
    if stop_pts <= 0:
        res["fail_reason"] = "degenerate_stop"
        return res
    stops = {"p1": (stop_price, lv, "m5_p1")}
    for w in WIDEN_PTS:
        if stop_pts < w:
            wide = _walk_back_stop(lt, is_long, fill_price, lv["breakout_time"], w)
            if wide is not None:
                stops[f"w{w:g}"] = wide
    res.update(stop_price=stop_price, stop_pts=stop_pts, stop_m5_level=lv.to_dict(),
               stop_source="m5_p1")

    ledger = _ledger(False)
    targets = {}
    px, info = _since_p1_target(lv, is_long, fill_price, touch)
    if px is not None:
        targets[MODE_QUICK] = (px, info)
    px, info = _structure_target(lt, is_long, fill_price, touch, lv)
    if px is not None:
        targets[MODE_STRUCT] = (px, info)
    if not targets:
        res["fail_reason"] = "no_target"
        return res
    res["modes"] = {}
    for tgt, (t_px, t_info) in targets.items():
        for skey, (s_px, s_row, s_src) in stops.items():
            s_pts = abs(s_px - fill_price)
            m = C2._resolve_target_mode(t_px, t_info, fill_price, s_pts, lt, is_long,
                                        touch, bars, ledger, args)
            m.update(stop_key=skey, stop_price=s_px, stop_pts=s_pts,
                     stop_row=s_row.to_dict(), stop_source=s_src)
            res["modes"][f"{tgt}|{skey}"] = m
    active = pick_mode(res["modes"], res["p1_p2_minutes"], args.quick_hours)
    # No rule applies at the default N: still shipped (a different N in the
    # browser can apply one); the page shows it as NO TARGET until then.
    res["default_no_target"] = active is None
    active = active or next(iter(res["modes"]))
    res.update(filled=True, touch_time_alt=touch)
    res["approach_by_k"] = C2._approach_by_k(touch, fill_price, is_long)
    res["m5_sfp_offsets"] = C2._m5_sfp_offsets(touch, is_long)
    res["qx_path"] = C2._quick_exit_path(touch, fill_price, is_long)
    C2._apply_mode(res, active)
    return res


# --------------------------------------------------------------------------
# Charts: ss_m5_confl2's stack, plus the H1 range box on the M5 pane
# --------------------------------------------------------------------------

def _add_range_box(chart_m5, res):
    if chart_m5 is None or not chart_m5.get("candles"):
        return
    st = res["setup"]
    hi, lo = st["box"]
    t0 = R._to_epoch_utc(pd.Timestamp(st["range_start"]))
    times = [c["time"] for c in chart_m5["candles"]]
    for px, name in ((hi, "range high"), (lo, "range low")):
        pts = [{"time": t, "value": px} for t in times if t >= t0]
        if pts:
            title = f"H1 {name} {px:.2f}"
            chart_m5.setdefault("rays", []).append({
                "points": pts, "color": BOX_COLOR, "lineWidth": 1, "lineStyle": 1,
                "priceLabel": True, "title": title, "label": title})
    # Every mode carries its own stop ray too (the stop differs between the
    # P1 and walked-back variants); chartForMode swaps it in with the target.
    tmpl = next((r for r in chart_m5.get("rays", []) if r["title"].startswith("stop")), None)
    for mode, view in (chart_m5.get("modeViews") or {}).items():
        m = res["modes"][mode]
        if tmpl is None:
            break
        start = R._to_epoch_utc(pd.Timestamp(m["stop_row"]["breakout_time"]))
        pts = [{"time": t, "value": m["stop_price"]} for t in times if t >= start] or \
              [{"time": t, "value": m["stop_price"]} for t in times[-2:]]
        title = (f"stop {m['stop_price']:.2f} (-{SR._fmt_pts(m['stop_pts'])}pt)"
                 + (" walked back" if m["stop_key"] != "p1" else ""))
        view["stopRay"] = {**tmpl, "points": pts, "title": title, "label": title}
    snap = C2._snapper(chart_m5)
    t = snap(st["t_bo"]) if snap else None
    if t is not None:
        chart_m5["markers"].append({
            "time": t, "position": "belowBar" if res["is_long"] else "aboveBar",
            "color": BOX_COLOR, "shape": "square",
            "text": f"BO {st['bo_rng_atr']:.1f}xATR {st['bo_body'] * 100:.0f}%"})
        chart_m5["markers"].sort(key=lambda m: m["time"])
    chart_m5["title"] += (f"  |  H1 range {R._to_pt_str(st['range_start'])} (confirmed "
                          f"{R._to_pt_str(st['range_confirm'])}) box {lo:.2f}-{hi:.2f}")


RANGE_H1_PAD_BARS = 24       # H1 candles loaded before the range's first candle, at least
RANGE_FOCUS_PAD_BARS = 8     # opening zoom: H1 candles either side of range start .. entry
RANGE_FOCUS_PRICE_PAD = 0.15  # opening zoom: price margin, as a share of the fitted span
RANGE_UP_COLOR, RANGE_DOWN_COLOR = "#fde68a", "#d97706"


def _add_range_h1(chart_h1, res):
    """The H1 pane always shows the whole range: widened back to a few
    candles before its first candle when the default window starts later,
    its candles (first to last) tinted amber, start / end / breakout
    markers, and the box high / low as price-labelled rays from the first
    candle to the breakout. The range's last candle is the one before the
    candle that ended it (break / cancel / merge), or the one before the
    breakout candle's H1 candle if the range was still open then."""
    if chart_h1 is None or not chart_h1.get("candles"):
        return
    st, h = res["setup"], _h1()
    hi, lo = st["box"]
    j_start = h.index.get_loc(pd.Timestamp(st["range_start"]))
    j_bo = h.index.searchsorted(pd.Timestamp(st["t_bo"]), side="right") - 1
    j_end = j_bo - 1
    if pd.notna(st.get("range_end")):
        j_end = min(j_end, h.index.get_loc(pd.Timestamp(st["range_end"])) - 1)
    j_end = max(j_end, j_start)
    candles = chart_h1["candles"]
    lo_t = h.index[max(0, j_start - RANGE_H1_PAD_BARS)]
    first = pd.Timestamp(candles[0]["time"], unit="s", tz="UTC")
    if lo_t < first:
        extra = h[(h.index >= lo_t) & (h.index < first)]
        candles[:0] = [{"time": R._to_epoch_utc(t), "open": float(b.open), "high": float(b.high),
                        "low": float(b.low), "close": float(b.close)} for t, b in extra.iterrows()]
    e0, e1 = R._to_epoch_utc(h.index[j_start]), R._to_epoch_utc(h.index[j_end])
    e_bo = R._to_epoch_utc(h.index[j_bo])
    for c in candles:
        if e0 <= c["time"] <= e1:
            col = RANGE_UP_COLOR if c["close"] >= c["open"] else RANGE_DOWN_COLOR
            c.update(color=col, borderColor=col, wickColor=col)
    times = [c["time"] for c in candles]
    for px, name in ((hi, "range high"), (lo, "range low")):
        title = f"{name} {px:.2f}"
        chart_h1.setdefault("rays", []).append({
            "points": [{"time": t, "value": px} for t in times if e0 <= t <= max(e1, e_bo)],
            "color": BOX_COLOR, "lineWidth": 2, "lineStyle": 0, "priceLabel": True,
            "title": title, "label": title})
    hm = lambda e: pd.Timestamp(e, unit="s", tz="UTC").tz_convert(PT).strftime("%H:%M")
    chart_h1["markers"] += [
        {"time": e0, "position": "aboveBar", "color": BOX_COLOR, "shape": "arrowDown",
         "text": f"range start {hm(e0)}"},
        {"time": e1, "position": "belowBar", "color": BOX_COLOR, "shape": "arrowUp",
         "text": f"range end {hm(e1)}"},
        {"time": e_bo, "position": "belowBar" if res["is_long"] else "aboveBar",
         "color": BOX_COLOR, "shape": "square", "text": "BO"}]
    chart_h1["markers"].sort(key=lambda m: m["time"])
    # Opening zoom (focusPane in the page): candles from a few before the
    # range to a few after the entry, prices fitted to the box plus entry and
    # stop. Everything else stays loaded: scroll, or drag the price axis.
    entry_e = R._to_epoch_utc(pd.Timestamp(res.get("touch_time_alt") or res["row"]["retest_time"]))
    i0 = bisect.bisect_left(times, e0)
    i1 = max(bisect.bisect_right(times, max(e1, e_bo, entry_e)) - 1, i0)
    prices = [hi, lo] + ([res["fill_price"], res["stop_price"]] if res["filled"] else [])
    span = max(prices) - min(prices)
    chart_h1["focus"] = {"from": i0 - RANGE_FOCUS_PAD_BARS, "to": i1 + RANGE_FOCUS_PAD_BARS,
                         "lo": min(prices) - RANGE_FOCUS_PRICE_PAD * span,
                         "hi": max(prices) + RANGE_FOCUS_PRICE_PAD * span}
    ts = lambda e: R._to_pt_str(pd.Timestamp(e, unit="s", tz="UTC"))
    chart_h1["title"] = (f"H1  |  {ts(times[0])} → {ts(times[-1])}  |  range {ts(e0)} → {ts(e1)} "
                         f"(amber candles), high {hi:.2f} / low {lo:.2f}")


def build_row(i, s, args):
    res = process_setup(i, s, args)
    if res["filled"]:
        stack, fp = C2.build_chart_stack_for_row(res, m5_only=args.m5_charts_only)
    else:
        stack, fp = C2._build_unfilled_chart_stack(res, args)
    _add_range_box(stack.get("m5"), res)
    _add_range_h1(stack.get("h1"), res)
    return res, stack, fp


def _run_chunk(spec):
    out = {}
    for pos, s in zip(spec["positions"], spec["setups"]):
        out[pos] = build_row(pos, s, spec["args"])
    with open(spec["out_path"], "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)


def process_all(setups, args):
    n = len(setups)
    if args.workers <= 1 or n <= 1:
        rows = []
        for i, s in enumerate(setups):
            lv = s["level"]
            print(f"  [{i + 1}/{n}] {lv['type']} {float(lv['price']):.2f} retest "
                  f"{R._to_pt_str(lv['retest_time'])}", flush=True)
            rows.append(build_row(i, s, args))
        return rows
    naive = [{"retest_time": pd.Timestamp(s["level"]["retest_time"]).tz_convert("UTC").tz_localize(None)}
             for s in setups]
    chunks = M.chunk_indices_by_contract(naive, args.workers)
    cache_dir = os.path.join(_HERE, "data", "parallel_chunks")
    os.makedirs(cache_dir, exist_ok=True)
    specs = [{"positions": pos, "setups": [setups[p] for p in pos], "args": args,
              "out_path": os.path.join(cache_dir, f"chunk{c:02d}_{os.getpid()}.pkl"),
              "label": f"chunk{c:02d}"} for c, pos in enumerate(chunks)]
    print(f"[parallel] {len(specs)} contract-pure chunks, {args.workers} concurrent", flush=True)
    ctx = mp.get_context("spawn")
    running, pending, collected, failed = [], list(specs), {}, []
    while pending or running:
        while pending and len(running) < args.workers:
            spec = pending.pop(0)
            p = ctx.Process(target=_run_chunk, args=(spec,), daemon=False)
            p.start()
            running.append((p, spec))
        time.sleep(2.0)
        for p, spec in list(running):
            if p.is_alive():
                continue
            running.remove((p, spec))
            if p.exitcode != 0 or not os.path.exists(spec["out_path"]):
                failed.append((spec["label"], p.exitcode))
                continue
            with open(spec["out_path"], "rb") as f:
                collected.update(pickle.load(f))
            os.remove(spec["out_path"])
            print(f"[parallel] finished {spec['label']} ({len(collected)}/{n})", flush=True)
    if failed:
        raise RuntimeError(f"parallel chunk(s) failed: {failed}")
    return [collected[i] for i in range(n)]


# --------------------------------------------------------------------------
# Page: ss_m5_confl2's renderer, patched where this strategy differs
# --------------------------------------------------------------------------

def _target_title(info):
    if info and info.get("src") == "extreme_since_p1":
        kind = "highest high" if info["pivot_kind"] == "high" else "lowest low"
        return (f"Retest within N hours of P1: the {kind} since P1 (P1 candle included, up to "
                f"the candle before the retest), {info['pivot_price']:.2f} at "
                f"{R._to_pt_str(info['pivot_time'])}")
    return _C2_TARGET_TITLE(info)


_C2_TARGET_TITLE = C2._target_title
_C2_MODE_PAYLOAD = C2._mode_payload


def _stop_title(m, fill_price, is_long):
    row = m["stop_row"]
    ext = "breakout_low" if is_long else "breakout_high"
    side = "below" if is_long else "above"
    base = (f"One tick {side} the P1 candle {R._to_pt_str(row['breakout_time'])} of M5 "
            f"{row['type']} {row['price']:.2f} ({ext.replace('breakout_', '')} {row[ext]:.2f})")
    if m["stop_key"] == "p1":
        return "This level's own P1 candle. " + base
    return (f"Walked back: the P1-candle stop was under {m['stop_key'][1:]}pt from the fill, so the "
            f"stop moved to the most recent earlier P1 candle giving at least that. " + base)


def _mode_payload(res, mode):
    """ss_m5_confl2's per-mode payload, computed with THIS mode's own stop,
    plus the stop / MES cells the page swaps along with it."""
    m = res["modes"][mode]
    p = _C2_MODE_PAYLOAD(dict(res, stop_pts=m["stop_pts"]), mode)
    contracts, comm, flag = C2._mes_sizing(m["stop_pts"])
    _, contracts_html, comm_text, mes_title = C2._mes_cells(m["stop_pts"])
    p.update(stopCell=f'{m["stop_price"]:.2f}<span class="src-tag m5">{m["stop_source"]}</span>',
             stopTitle=_stop_title(m, res["fill_price"], res["is_long"]),
             risk=f'{m["stop_pts"]:.6f}', contracts=str(contracts), comm=f"{comm:.2f}",
             mesFlag=flag, contractsHtml=contracts_html, commText=comm_text, mesTitle=mes_title)
    return p


def _patch_once(html, old, new, what):
    n = html.count(old)
    if n != 1:
        raise RuntimeError(f"page patch '{what}': expected 1 match, found {n}")
    return html.replace(old, new)


def _num_filter_any(html, target, selected_op):
    old = (f'<select class="f-num-op" data-target="{target}">\n'
           f'      <option value="any">any</option>')
    html = _patch_once(html, old, old.replace('value="any">', 'value="any" selected>'),
                       f"{target} default")
    start = html.index(f'<select class="f-num-op" data-target="{target}">')
    end = html.index("</select>", start)
    block = html[start:end]
    sel = f'<option value="{selected_op}" selected>'
    if block.count(sel) != 1:
        raise RuntimeError(f"page patch '{target}': default option not found")
    return html[:start] + block.replace(sel, f'<option value="{selected_op}">') + html[end:]


PICK_JS_OLD = """    let pick = null;
    on.forEach(m => {
      const p = modes[m];
      if (p && (pick === null || p.dist > modes[pick].dist)) pick = m;
    });"""
PICK_JS_NEW = "    let pick = pickTargetMode(tr, modes);"
PICK_JS_APPLY_OLD = "    const p = modes[pick];\n    tr.classList.remove('no-target-row');"
PICK_JS_APPLY_NEW = "    const p = modes[pick];\n    applyStopCells(tr, p);\n    tr.classList.remove('no-target-row');"
PICK_JS_FN = """<script>
// Range breakout: every row ships '<target>|<stop>' modes, all resolved on
// ticks. Target: the since-P1 extreme when this level's own P1 -> retest gap
// is within N hours, else the structure target (opposite M5 zigzag, else
// swing extreme). Stop: the P1 candle, or -- with widening on and the P1
// stop tighter than the threshold -- the walked-back earlier P1 ('w<pts>')
// when one exists. Mirrors pick_mode() in render_range_breakout_report.py.
function pickTargetMode(tr, modes) {
  const el = document.getElementById('f-quick-hours');
  let h = el ? parseFloat(el.value) : __QUICK_HOURS__;
  if (!Number.isFinite(h)) h = __QUICK_HOURS__;
  const mins = parseFloat(tr.dataset.mingap);
  let tgt = null;
  if (modes['since-p1|p1'] && Number.isFinite(mins) && mins <= h * 60) tgt = 'since-p1';
  else if (modes['structure|p1']) tgt = 'structure';
  if (tgt === null) return null;
  const on = document.getElementById('f-widen-on');
  const w = document.getElementById('f-widen-pts');
  if (on && on.checked && w && modes[tgt + '|w' + w.value]) return tgt + '|w' + w.value;
  return tgt + '|p1';
}
function applyStopCells(tr, p) {
  if (p.stopCell === undefined) return;
  setCell(tr, '.stop-cell', p.stopCell, 'stop-cell');
  const sc = tr.querySelector('.stop-cell');
  if (sc) sc.title = p.stopTitle;
  setCell(tr, '.contracts-cell', p.contractsHtml, 'contracts-cell');
  setCell(tr, '.comm-cell', p.commText, 'comm-cell');
  ['.contracts-cell', '.comm-cell'].forEach(s => { const c = tr.querySelector(s); if (c) c.title = p.mesTitle; });
  tr.dataset.contracts = p.contracts; tr.dataset.comm = p.comm;
  tr.dataset.mesFlag = p.mesFlag; tr.dataset.risk = p.risk;
}
// One trade at a time (recomputeDynStats): rows in fill order, unfilled ones
// last; and the label a blocked row gets (applyTargetModes rewrites the
// outcome cell on every recompute, so it clears itself when no longer blocked).
function rowsByFill() {
  const rows = Array.from(document.querySelectorAll('#lvl-table tbody tr.lvl-row'));
  const t = tr => { const v = parseFloat(tr.dataset.fill); return Number.isFinite(v) ? v : Infinity; };
  return rows.sort((a, b) => t(a) - t(b) || a.dataset.idx - b.dataset.idx);
}
function markOverlap(tr, openIdx) {
  tr.classList.add('overlap-row');
  const oc = tr.querySelector('.outcome-cell');
  if (!oc) return;
  oc.className = 'outcome-cell';
  const lbl = oc.querySelector('.outcome-label');
  if (lbl) lbl.innerHTML = 'NO TRADE (another trade on: #' + openIdx + ')';
  const mgb = oc.querySelector('.mgmt-badge');
  if (mgb) mgb.innerHTML = '';
}
</script>
"""
FOCUS_JS_OLD = "_centerLogicalRange(chart, el, cd.candles.length);"
FOCUS_JS_NEW = "_centerLogicalRange(chart, el, cd.candles.length); focusPane(chart, series, cd);"
FOCUS_JS_FN = """<script>
// H1 pane opening zoom on the range (cd.focus, from _add_range_h1): the
// candle window from a few bars before the range to a few after the entry,
// and the price scale fitted to the range box plus entry and stop through
// the series' autoscale. Every candle stays loaded, so scrolling works as
// usual; dragging the price axis switches autoscale off and zooms out
// freely, and double-clicking the axis snaps back to this fit.
function focusPane(chart, series, cd) {
  const f = cd && cd.focus;
  if (!f) return;
  chart.timeScale().setVisibleLogicalRange({ from: f.from, to: f.to });
  series.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: f.lo, maxValue: f.hi } }) });
}
</script>
"""
OVERLAP_CSS = """<style>
tr.lvl-row.overlap-row td { color:var(--text-faint); font-style:italic; }
.overlap-row .contracts-cell > *, .overlap-row .comm-cell { visibility:hidden; }
</style>
"""
CHART_JS_OLD_BASE = "      rays: m5.rays.filter(r => !(r.title || '').startsWith('target')),"
CHART_JS_NEW_BASE = "      rays: m5.rays.filter(r => !/^(target|stop)/.test(r.title || '')),"
CHART_JS_OLD_SWAP = "  m5.rays = m5._base.rays.concat(v ? [v.ray] : []);"
CHART_JS_NEW_SWAP = ("  m5.rays = m5._base.rays.concat(v ? [v.ray].concat(v.stopRay ? [v.stopRay] : [])"
                     " : []);")


def _patch_page(path, args, trades, counts, setups_n):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    ra, bd = args.bo_range_atr, args.bo_body
    title = f"H1 range breakout &rarr; M5 LXPB retest ({args.start} &ndash; {args.end})"
    html = _patch_once(html, "<title>M5-native SS Confl report</title>",
                       f"<title>Range breakout retest {args.start} - {args.end}</title>", "title")
    html = _patch_once(html, f"<h1>M5-native SS Confl. &ge; {args.ss_confl_min} strategy report</h1>",
                       f"<h1>{title}</h1>", "h1")

    # Summary boxes: the scan's own numbers instead of SS-confluence ones.
    old_boxes = (
        f'  <div class="box"><strong>{setups_n}</strong>SS Confl &ge; {args.ss_confl_min}</div>\n'
        f'  <div class="box"><strong>{setups_n}</strong>confluence clusters</div>\n'
        f'  <div class="box"><strong>&plusmn;{SR._fmt_pts(args.m5_confluence_points)}pt</strong>M5 confluence radius</div>\n'
        f'  <div class="box"><strong>{args.min_r:.2f}</strong>min R (below: tagged, not skipped)</div>\n')
    edge_boxes = "".join(
        f'  <div class="box"><strong>{counts.get(k, 0)}</strong>{label}</div>\n'
        for k, label in (("trade", "edges traded"), ("failed", "breaks failed (H1 back inside)"),
                         ("armed", "broken, no retest yet"), ("never broken", "ranges edges never broken"),
                         ("duplicate", "edges sharing another range's trade (merged)")))
    new_boxes = (f'  <div class="box"><strong>&ge;{ra:g}&times; / {bd * 100:.0f}%</strong>'
                 f'breakout candle (M5 ATR / body)</div>\n'
                 f'  <div class="box"><strong>${R_DOLLARS:,.0f}</strong>1R (MES, '
                 f'${MES_COMMISSION_RT} round trip)</div>\n' + edge_boxes)
    html = _patch_once(html, old_boxes, new_boxes, "summary boxes")
    html = _patch_once(html, f'<div class="box"><strong>0</strong>/{len(trades)} entry improved over own level</div>\n',
                       "", "improved box")

    # Neutral filter defaults: none of ss_m5_confl2's own defaults belong to this strategy.
    html = _num_filter_any(html, "rr", "gte")
    html = _num_filter_any(html, "h1gap", "gte")
    html = _num_filter_any(html, "er", "lte")
    html = _num_filter_any(html, "p1ratio", "gte")

    # Target rules row -> the N-hours control.
    start = html.index('<span class="filter-label" title="Which TARGET RULE(S)')
    end = html.index("</div>", start)
    html = html[:start] + (
        '<span class="filter-label" title="Live, no regen. If this level\'s own P1 (the breakout '
        'candle) to its retest is within N hours, the target is the lowest low (short) / highest '
        'high (long) since P1, P1 candle included, up to the candle before the retest. Otherwise it '
        'is the opposite M5 level after the last zigzag pivot (ss_m5_confl2\'s rule), and if there is '
        'none, the swing extreme. Both brackets are resolved on ticks for every trade, so changing N '
        'only picks between them. The charts draw the bracket the page was generated with.">'
        'Target rule</span>\n'
        '    <span class="filter-sublabel">retest within</span>\n'
        f'    <input type="number" id="f-quick-hours" value="{args.quick_hours:g}" min="0" step="0.5">\n'
        '    <span class="filter-sublabel">h of P1 &rarr; low/high since P1 (up to the touch); '
        'later &rarr; opposite M5 zigzag level, else swing extreme</span>\n  </div>\n'
        '  <div class="filter-row">\n'
        '    <span class="filter-label" title="Live, no regen. Off: the stop is one tick beyond this '
        'level\'s own P1 candle. On: when that stop is closer to the fill than the threshold, walk '
        'back from P1 to the most recent earlier M5 P1 (breakout) candle of a same-type level within '
        f'{C2.DYNAMIC_STOP_RADIUS_PTS:g}pt of the fill (up to {C2.WIDER_STOP_LOOKBACK.total_seconds() / 3600:g}h '
        'before P1) whose extreme gives at least the threshold, and tuck the stop one tick beyond it. '
        'Trades with no such candle keep the P1 stop. Every variant is resolved on ticks up front, '
        'so this swaps stop, R, outcome, PnL, MES size and commission in place.">Stop</span>\n'
        '    <label class="chip"><input type="checkbox" id="f-widen-on"> widen P1 stops tighter than</label>\n'
        '    <select id="f-widen-pts">'
        + "".join(f'<option value="{w:g}"{" selected" if w == 3.0 else ""}>{w:g}</option>' for w in WIDEN_PTS)
        + '</select>\n'
        '    <span class="filter-sublabel">pt by walking back to an earlier P1 candle</span>\n  </div>\n'
        '  <div class="filter-row">\n'
        '    <span class="filter-label" title="Live, no regen. On: only one position at a time. Walking '
        'the trades in fill order, a trade that fills while an earlier counted trade is still open '
        '(its exit under the current target / stop / management / quick-exit settings) is NO TRADE '
        '(another trade on) and leaves the stats. A trade hidden by any filter does not count, so it '
        'never blocks a later one. A trade that never hit stop or target holds the slot for the '
        f'whole {SF.HORIZON_HOURS}h tick window. The edge it came from is still spent.">Position</span>\n'
        '    <label class="chip"><input type="checkbox" id="f-one-at-a-time" checked> one trade at a '
        'time</label>\n  ') + html[end:]
    html = _patch_once(html, PICK_JS_OLD, PICK_JS_NEW, "target pick JS")
    html = _patch_once(html, PICK_JS_APPLY_OLD, PICK_JS_APPLY_NEW, "stop cells JS")
    html = _patch_once(html, CHART_JS_OLD_BASE, CHART_JS_NEW_BASE, "chart base rays JS")
    html = _patch_once(html, CHART_JS_OLD_SWAP, CHART_JS_NEW_SWAP, "chart swap rays JS")
    html = _patch_once(html, "const mgmtToggleCb = document.getElementById('mgmt-thrust-trail');",
                       "['f-quick-hours', 'f-widen-on', 'f-widen-pts', 'f-one-at-a-time'].forEach(id => {\n"
                       "  const el = document.getElementById(id);\n"
                       "  if (el) { el.addEventListener('input', recomputeDynStats); "
                       "el.addEventListener('change', recomputeDynStats); }\n"
                       "});\n"
                       "const mgmtToggleCb = document.getElementById('mgmt-thrust-trail');",
                       "N-hours / widen listeners")
    anchor = "<script>\n// D1 pane above the M5 chart"
    html = _patch_once(html, anchor,
                       PICK_JS_FN.replace("__QUICK_HOURS__", f"{args.quick_hours:g}") + anchor,
                       "pick function")

    # Profit factor and $ PnL beside the stats the strip already carries.
    html = _patch_once(html, "let n = 0, wins = 0, sumR = 0, sumPnl = 0, sumComm = 0,",
                       "let grossWinR = 0, grossLossR = 0, sumUsd = 0;\n"
                       "  const oneAtATime = _setupOn('f-one-at-a-time');\n"
                       "  let openUntil = -Infinity, openIdx = null, overlapN = 0;\n"
                       "  let n = 0, wins = 0, sumR = 0, sumPnl = 0, sumComm = 0,", "stats vars")

    # One trade at a time: walk the rows in fill order (not table order, which
    # the user can sort); see the Position row's tooltip for the rule.
    html = _patch_once(html, "  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {\n"
                             "    const tags = (tr.dataset.dynTags",
                       "  rowsByFill().forEach(tr => {\n"
                       "    const tags = (tr.dataset.dynTags", "fill-order walk")
    html = _patch_once(html, "    if (qxOn && qx) { rVal = qx.r; pnl = qx.pnl; outcome = 'quick_exit'; }\n",
                       "    if (qxOn && qx) { rVal = qx.r; pnl = qx.pnl; outcome = 'quick_exit'; }\n"
                       "    let overlap = false;\n"
                       "    tr.classList.remove('overlap-row');\n"
                       "    const fillT = parseFloat(tr.dataset.fill);\n"
                       "    if (oneAtATime && !hidden && !isNoTradeRow && Number.isFinite(fillT)) {\n"
                       "      if (fillT < openUntil) {\n"
                       "        overlap = true;\n"
                       "        overlapN += 1;\n"
                       "        markOverlap(tr, openIdx);\n"
                       "      } else {\n"
                       "        let sec = (qxOn && qx) ? Math.round(_setupNum('f-qx-sec', 30))\n"
                       "          : parseFloat(useRow ? tr.dataset.mgmtExitSec : tr.dataset.exitSec);\n"
                       f"        if (!Number.isFinite(sec)) sec = {SF.HORIZON_HOURS * 3600};\n"
                       "        openUntil = fillT + sec;\n"
                       "        openIdx = tr.dataset.idx;\n"
                       "      }\n"
                       "    }\n", "overlap check")
    html = _patch_once(html, "    const bucket = isNoTradeRow ? 'no_trade'",
                       "    const bucket = (isNoTradeRow || overlap) ? 'no_trade'", "overlap bucket")
    html = _patch_once(html, "    if (hidden || outcomeHidden || isNoTradeRow) return;",
                       "    if (hidden || outcomeHidden || isNoTradeRow || overlap) return;", "overlap skip")
    html = _patch_once(html, "  setText('sum-trades', String(n));\n",
                       "  setText('sum-trades', String(n));\n"
                       "  setText('sum-overlap', String(overlapN));\n", "overlap count")
    html = _patch_once(html, "</head><body>", OVERLAP_CSS + "</head><body>", "overlap css")
    # First render and every resize of a pane (both in _renderPane).
    if html.count(FOCUS_JS_OLD) != 2:
        raise RuntimeError(f"page patch 'pane focus': expected 2 matches, found {html.count(FOCUS_JS_OLD)}")
    html = html.replace(FOCUS_JS_OLD, FOCUS_JS_NEW)
    html = _patch_once(html, anchor, FOCUS_JS_FN + anchor, "pane focus function")
    html = _patch_once(html, "      sumR += rVal;\n",
                       "      sumR += rVal;\n"
                       "      if (rVal > 0) grossWinR += rVal; else grossLossR -= rVal;\n"
                       "      const nc = parseFloat(tr.dataset.contracts);\n"
                       f"      if (!isNaN(pnl) && !isNaN(nc)) sumUsd += pnl * nc * {C2.MES_POINT_VALUE:g};\n",
                       "stats sums")
    html = _patch_once(html, "  setText('sum-max-win-mae', maxWinMae.toFixed(2));\n",
                       "  setText('sum-max-win-mae', maxWinMae.toFixed(2));\n"
                       "  setText('sum-pf', grossLossR > 0 ? (grossWinR / grossLossR).toFixed(2) : '-');\n"
                       "  const fmtUsd = v => (v < 0 ? '-$' : '+$') + Math.abs(v).toLocaleString('en-US', {maximumFractionDigits: 0});\n"
                       "  setText('sum-gross-usd', fmtUsd(sumUsd));\n"
                       "  setText('sum-net-usd', fmtUsd(sumUsd - sumComm));\n"
                       "  setText('sum-net-r', ((sumUsd - sumComm) / " f"{R_DOLLARS:g}" ").toFixed(1));\n",
                       "stats text")
    comm_box = 'commissions (MES)</div>'
    html = _patch_once(html, comm_box,
                       comm_box + '<div class="box"><strong id="sum-overlap">-</strong>no trade: another '
                       'trade on</div>'
                       '<div class="box"><strong id="sum-pf">-</strong>profit factor</div>'
                       '<div class="box"><strong id="sum-gross-usd">-</strong>gross $ (MES)</div>'
                       '<div class="box"><strong id="sum-net-usd">-</strong>net $ after commissions</div>'
                       '<div class="box"><strong id="sum-net-r">-</strong>net R after commissions</div>',
                       "stats boxes")

    # Column heads re-used for this strategy's own data.
    start = html.index('<th class="left" title="Distinct M5 prices merged')
    end = html.index("</th>", start) + len("</th>")
    html = html[:start] + ('<th class="left" title="The H1 find_range box at the breakout '
                           '(low, high). The M5 pane draws it as two amber rays from the range\'s '
                           'first candle; BO marks the breakout candle.">H1 range (low, high)</th>'
                           ) + html[end:]

    lead_start = html.index('<p class="lead">M5-native strategy:')
    lead_end = html.index("</p>", lead_start) + len("</p>")
    html = html[:lead_start] + _lead_html(args) + html[lead_end:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _lead_html(args):
    eod = ("Open trades are flattened before 12:45 PT and no entry is taken from then until the "
           "Globex reopen (--eod-flat)." if args.eod_flat else
           "No end-of-day flat: a trade runs until its stop or target, up to the 72h tick window "
           "(NO-HIT after that).")
    return f"""<p class="lead">H1 RANGE BREAKOUT &rarr; M5 LXPB RETEST (rules set 2026-09-25).
RANGE: every patterns-pure find_range candidate on H1, inner or outer; its box is the point-in-time
box while find_range tracks it and its last box after a break or cancel. EDGES: each box edge takes at
most one trade and stays valid until it has taken it. BREAKOUT: an M5 candle that opens inside the box
and closes beyond the edge, with a range of at least {args.bo_range_atr:g}&times; the M5 ATR(21) and a
body of at least {args.bo_body * 100:.0f}% of its range, arms the edge. FAILED BREAK: an H1 candle closing
back inside the box before the fill ends that edge with no trade, watched from the breakout or from the
range's own H1 break candle on that side, whichever comes first (an H1 break made only of weak M5
candles arms nothing, but a close back inside after it still ends the edge); the other edge is
unaffected. ENTRY:
the first retest of an M5 LLPB (break below, short) / LHPB (break above, long) that formed during the
range and was broken by a breakout candle of that edge, filled as a plain limit at the level on real
ticks. STOP: one tick beyond that level's own P1 (breakout) candle; a live Stop toggle in the Trades tab
instead walks back to an earlier P1 candle when that stop is tighter than a threshold. TARGET: if the
retest came within N hours of P1 (a live control in the Trades tab, default {args.quick_hours:g}h), the
lowest low (short) / highest high (long) since P1 -- M5 candles from P1, plus the real trades inside the
retest candle before the touch; otherwise ss_m5_confl2's opposite-M5-level-after-the-zigzag-pivot target,
else its swing-extreme target. Nested ranges picking the same M5 level make one trade. ONE TRADE AT A
TIME (the Position toggle, on by default): a trade that fills while an earlier one is still open is
NO TRADE (another trade on). Exits, MAE/MFE and everything in the charts are resolved on real ticks by
the same code as the ss_m5_confl2 reports. {eod} Sizing: 1R = ${R_DOLLARS:,.0f} on MES
(${C2.MES_POINT_VALUE:g}/pt), contracts rounded down, ${MES_COMMISSION_RT} per contract round trip on
top of the risk.</p>"""


def render(args):
    start = pd.Timestamp(args.start, tz=PT).tz_convert("UTC")
    end = pd.Timestamp(args.end, tz=PT).tz_convert("UTC") + pd.Timedelta(days=1)
    setups, counts = find_setups(start, end, args.bo_range_atr, args.bo_body)
    print(f"{len(setups)} range-breakout retests in [{args.start}, {args.end}]; edges: {counts}",
          flush=True)
    if args.max_rows is not None:
        setups = setups[:args.max_rows]
    rows = process_all(setups, args)
    results = [r for r, _, _ in rows]
    chart_stacks = [c for _, c, _ in rows]
    fps = [f for _, _, f in rows]
    for fn in (C2._apply_globex_open_filter, C2._apply_h1_p0_confluence,
               C2._apply_h1_spike_confluence, C2._apply_h1_bias, C2._apply_trap_variants):
        results = fn(results)

    C2.R_DOLLARS = R_DOLLARS
    C2.MES_COMMISSION_RT = MES_COMMISSION_RT
    C2.MES_MAX_STOP_PTS = R_DOLLARS / C2.MES_POINT_VALUE
    C2._target_title = _target_title
    C2._mode_payload = _mode_payload
    filled = [r for r in results if r["filled"]]
    skipped = [r for r in results if not r["filled"]]
    reason_counts = {}
    for r in skipped:
        reason_counts[r.get("fail_reason", "?")] = reason_counts.get(r.get("fail_reason", "?"), 0) + 1
    stats, improved_n, max_win_mae, max_loss_mfe, gapped, pctile_html = C2._compute_report_stats(filled)
    charts, rows_html = [], []
    for idx, res in enumerate(results):
        chart_entry, row_html = C2._render_row(idx, res, chart_stacks, fps)
        if res["filled"]:     # fill instant, for the one-trade-at-a-time walk in the page
            fill = f'{pd.Timestamp(res["touch_time_alt"]).tz_convert("UTC").timestamp():.3f}'
            row_html = _patch_once(row_html, f'data-idx="{idx}" data-key=',
                                   f'data-idx="{idx}" data-fill="{fill}" data-key=', "fill attr")
        charts.append(chart_entry)
        rows_html.append(row_html)
    C2._finish_report(args, results, results, results, filled, skipped, reason_counts,
                      stats, improved_n, max_win_mae, max_loss_mfe, gapped, pctile_html,
                      rows_html, charts, SR._fmt_pts(args.m5_confluence_points), args.output,
                      storage_suffix="_range_breakout")
    _patch_page(args.output, args, filled, counts, len(results))
    if args.gzip:
        with open(args.output, "rb") as src, gzip.open(args.output + ".gz", "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
        print(f"gzip -> {args.output}.gz")


def _args_from_cli(argv=None):
    p = argparse.ArgumentParser(description="H1 range breakout -> M5 LXPB retest report")
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-12-31")
    p.add_argument("--bo-range-atr", type=float, default=BO_RANGE_ATR_DEFAULT,
                   help="breakout candle range >= this x M5 ATR(21) (default %(default)s)")
    p.add_argument("--bo-body", type=float, default=BO_BODY_DEFAULT,
                   help="breakout candle body >= this share of its range (default %(default)s)")
    p.add_argument("--quick-hours", type=float, default=QUICK_HOURS_DEFAULT,
                   help="default N for the target rule; live in the page (default %(default)s)")
    p.add_argument("--eod-flat", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--news-pull-window", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--liquidity-gate", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--volume-spike-check", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--max-rows", type=int, default=None, help="smoke test: first N trades only")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--m5-charts-only", action="store_true")
    p.add_argument("--gzip", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--output", default=None)
    a = p.parse_args(argv)
    # ss_m5_confl2 settings its shared code reads; the entry-side ones do not
    # apply to this strategy (plain limit at the level, no swerve/crest/peg).
    a.min_r = 0.0
    a.ss_confl_min = 1
    a.m5_confluence_points = C2.M5_CONFLUENCE_N_POINTS_DEFAULT
    a.default_target_modes = C2.DEFAULT_TARGET_MODES_BOTH
    a.zz_threshold_pts = C2.ZZ_THRESHOLD_PTS_DEFAULT
    a.zz_min_bars = C2.ZZ_MIN_BARS_DEFAULT
    a.swerve, a.crest_refine = False, False
    a.swerve_tol_pts, a.swerve_max_move_pts = C2.SWERVE_TOL_PTS_DEFAULT, C2.SWERVE_MAX_MOVE_PTS_DEFAULT
    a.liq_window_minutes = C2.LIQ_WINDOW_MINUTES_DEFAULT
    a.liq_wide_spread_share = C2.LIQ_WIDE_SPREAD_SHARE_DEFAULT
    a.volume_spike_core_seconds = C2.VOL_SPIKE_CORE_SECONDS_DEFAULT
    a.volume_spike_baseline_seconds = C2.VOL_SPIKE_BASELINE_SECONDS_DEFAULT
    a.volume_spike_ratio = C2.VOL_SPIKE_RATIO_DEFAULT
    a.volume_spike_min_peak = C2.VOL_SPIKE_MIN_PEAK_DEFAULT
    a.max_alt_fill_hours = FILL_WINDOW_HOURS
    a.pegged_entry, a.peg_step, a.peg_cap = False, C2.PEG_STEP_DEFAULT, C2.PEG_CAP_DEFAULT
    a.peg_target, a.peg_target_step, a.peg_target_cap = False, C2.PEG_STEP_DEFAULT, C2.PEG_CAP_DEFAULT
    a.output = a.output or os.path.join(OUT_DIR, f"{a.start[:4]}.html")
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    return a


if __name__ == "__main__":
    render(_args_from_cli())
