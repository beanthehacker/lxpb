"""
Which LXPB level do you take when a single retest move hits SEVERAL of them?
===========================================================================

A large retest bar -- or a 2-3 bar directional move (down into LHPB
supports, up into LLPB resistances) -- routinely sweeps through several
broken-out LXPB levels at once. Only one of them is worth trading. This
module builds, per such "retest event", the full candidate set and scores
each candidate on four hand-specified features:

  (a) recency to breakout bar : H1 bars from the level's FORMATION bar to
      its own BREAKOUT bar. LXPB literally means "Last High/Low Pre-
      Breakout", so a high that formed 1 bar before the breakout is a much
      more literal LXPB than one that formed 40 bars before it. Reported
      as raw bars + a within-cluster 0..1 `recency_score` (1 = the most
      recent-to-its-breakout level in this cluster). `bars_breakout_to_event`
      (breakout -> retest) is ALSO reported so the reviewer can tell us if
      "recency" was meant that way instead.
  (b) is_swing (patterns-pure): the level price is a confirmed ATR-ZigZag
      swing pivot, via `patterns_pure/find_swings.find_swings` -- run
      point-in-time on a trailing window ENDING at the event's first bar,
      so no post-event information leaks in. lxpb.py's own cruder 3-bar
      `is_swing` is carried alongside as `is_swing_lxpb` for comparison.
  (c) is_spike (patterns-pure): the formation bar is a rejection spike --
      `find_shooting_star` for LHPB (level = a bar HIGH, so rejection of
      higher prices) and `find_hammer` for LLPB. NOTE this is the opposite
      mapping to lxpb.py's own `is_spike` field (carried along as
      `is_spike_lxpb`); the patterns-pure mapping is the one consistent
      with feature (d) and with render_labels_report.is_spike_pp.
  (d) large wick: LHPB formation bar's UPPER wick (LLPB: LOWER wick) is
      >= 40% of the bar's range -- patterns-pure `candle_utils`.

Why the candidate set is NOT just "levels that got retested"
------------------------------------------------------------
Ranking only the levels that were actually retested would be rigged: the
DEEPEST level touched by a move sits, mechanically, right at the reversal
point, so it almost always shows the biggest bounce and the smallest
adverse excursion -- "always take the deepest" would look unbeatable.

It isn't, because a level that price never reaches is a MISSED trade: if
the move reverts off a shallower level in front of it, the deeper limit
order simply never fills. So each event's candidate set here is the
point-in-time set of ALL levels that had already broken out and were
still awaiting a retest as of the event's first bar (reconstructed by
replaying `lxpb.advance_one_bar` and snapshotting `state["touch_lv1"]`),
restricted to the price band the move could plausibly have reached:

    LHPB (down move):  event_low - MISS_BAND_PTS  <=  price  <=  event_high
    LLPB (up move):    event_low  <=  price  <=  event_high + MISS_BAND_PTS

Candidates inside the move's own range get filled; candidates in the
`MISS_BAND_PTS` tail beyond the move's extreme are exactly the "price
reverted in front of me" misses. Every selection rule is then scored on
the same footing: expectancy = 0 R when the rule's pick never filled.

1-second ground truth
---------------------
Fills, exits and bounce measurements all come from REAL 1s .scid ticks
(same `D:\\SC\\Data` files / roll-splicing helpers as
render_labels_report.py), walked strictly FORWARD from each candidate's
own fill instant -- so the pre-entry look-ahead bug documented in
../AGENTS.md (a whole clock-minute re-scanned from :00) cannot occur
here: this module never re-fetches a minute, it slices one tick array per
event and indexes forward from the fill.

Fill realism matches analyze_breakout_exits_1min.py: a LONG entry is a
resting BUY, filled only by a BID-side (seller-initiated) print; a LONG
target is a resting SELL, filled only by an ASK-side print; a stop is a
stop/market order and fills on any side (symmetric for shorts).

Usage:
    python analyze_retest_cluster_selection.py
    python analyze_retest_cluster_selection.py --stop 2 --target 2 --move-bars 3
    python analyze_retest_cluster_selection.py --start 2026-07-01 --end 2026-08-31 --limit 40
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")   # lxpb.py + build_es_h1_2026_backadjusted.py live there
_VENDORED_PP = os.path.join(_HERE, "patterns_pure")
# The vendored patterns-pure copy goes on sys.path FIRST, before
# render_labels_report is imported (which puts the external
# D:\daily-analysis\patterns-pure ahead of it), so `find_hammer` &co. always
# resolve to this repo's own vendored, byte-identical copies. See
# patterns_pure/README.md.
for _p in (_DATA_DIR, _REPO_ROOT, _HERE, _VENDORED_PP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from find_swings import find_swings  # noqa: E402
from find_ATR import findATR  # noqa: E402
from find_hammer import find_hammer  # noqa: E402
from find_shooting_star import find_shooting_star  # noqa: E402
from candle_utils import (  # noqa: E402
    has_large_upper_wick,
    has_large_lower_wick,
)

import lxpb as L  # noqa: E402
import render_labels_report as R  # noqa: E402

# Default H1 series: the same TradingView ES1! 60m export the existing
# strong-breakout / stop-target reports use, so rows here line up with
# stop2_target2_trades_report.html. It carries ~13 months of history
# (2025-07 -> 2026-08), plenty of lookback for the ZigZag swing pass, and it
# is one of DISPLAY_H1_PATHS, so it shares the splice vintage the .scid
# offsets are measured against (R._offset_for_ts) -- which is what makes
# adjusted -> raw tick conversion valid.
DEFAULT_DATA = os.path.join(_HERE, "data", "24aug-CME_MINI_ES1!, 60.csv")
DEFAULT_CANDIDATES_CSV = os.path.join(_HERE, "data", "cluster_selection_candidates.csv")

START_DEFAULT = "2026-07-01"
END_DEFAULT = "2026-08-31"

MOVE_BARS_DEFAULT = 3        # a retest event is 1 large bar, or up to this many bars of directional move
MISS_BAND_PTS_DEFAULT = 10.0  # pending levels this far BEYOND the move's extreme are in-play-but-missed
MIN_CLUSTER_SIZE_DEFAULT = 2  # only events offering a real choice between >=2 levels
MIN_RETESTED_DEFAULT = 2      # ...and where >=2 of them were really swept ("retested at once")

SWING_LOOKBACK_BARS = 750    # trailing H1 bars fed to find_swings (point-in-time, ends at event start)
SWING_ATR_MULT = 0.75        # patterns-pure find_swings default
SWING_ATR_PERIOD = 21        # project-wide ATR convention
WICK_THRESHOLD = 0.40        # patterns-pure candle_utils default

STOP_DEFAULT = 2.0
TARGET_DEFAULT = 2.0
HORIZON_HOURS_DEFAULT = 6    # forward tick window a filled candidate is resolved over
BOUNCE_SECONDS_DEFAULT = 300  # 1s bounce/MFE/MAE measurement window after the fill
BREAK_PTS_DEFAULT = 2.0      # a level is "broken" once price trades this far through it
TICK_PRE_PAD_MINUTES = 30    # ticks fetched BEFORE the event, for chart context only (never for fills)

# Selection-rule feature weights (all equal by default -- retune once the
# reviewer's exported labels come back).
WEIGHTS_DEFAULT = {"recency": 1.0, "swing": 1.0, "spike": 1.0, "wick": 1.0}


def parse_weights(spec):
    """`recency=1,swing=2,spike=0,wick=1.5` -> weight dict (unnamed keys
    keep their default). Lets the four features be reweighted from the CLI
    once the reviewer's labels say which of them actually carry signal."""
    w = dict(WEIGHTS_DEFAULT)
    if not spec:
        return w
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad --weights entry {part!r}; expected name=value")
        name, _, val = part.partition("=")
        name = name.strip().lower()
        if name not in w:
            raise ValueError(f"unknown weight {name!r}; expected one of {sorted(w)}")
        w[name] = float(val)
    return w

_SWING_CACHE = {}


# ---------------------------------------------------------------------------
# Loading / event detection
# ---------------------------------------------------------------------------

def load_context(data_path=DEFAULT_DATA, start=START_DEFAULT, end=END_DEFAULT):
    """H1 bars + gap-filtered completed retests inside [start, end]."""
    h1_df = L.load_ohlc_data(data_path)
    _lv0, _lv1, retests_df = L.detect_lxpb_h1(h1_df)
    retests_df, n_gap = R.filter_gap_rows(h1_df, retests_df)
    if start:
        retests_df = retests_df[retests_df["retest_time"] >= pd.Timestamp(start)]
    if end:
        retests_df = retests_df[retests_df["retest_time"] <= pd.Timestamp(end)]
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    print(f"Loaded {len(h1_df)} H1 bars ({h1_df.index.min()} -> {h1_df.index.max()});  "
          f"{len(retests_df)} gap-free retests in window ({n_gap} gap rows dropped overall)")
    return h1_df, pos_by_ts, retests_df


def _is_directional_run(h1_df, level_type, i0, i1):
    """Do bars i0..i1 form one continuous move in the retest direction?
    LHPB levels are retested from ABOVE, so their multi-bar retest move is
    a sequence of lower lows; LLPB is the mirror image (higher highs)."""
    if i1 <= i0:
        return True
    if level_type == "LHPB":
        lows = h1_df["low"].to_numpy()[i0:i1 + 1]
        return bool(np.all(np.diff(lows) <= 0) and lows[-1] < lows[0])
    highs = h1_df["high"].to_numpy()[i0:i1 + 1]
    return bool(np.all(np.diff(highs) >= 0) and highs[-1] > highs[0])


def find_retest_events(h1_df, pos_by_ts, retests_df, move_bars=MOVE_BARS_DEFAULT):
    """Group completed retests into retest EVENTS: one H1 bar that swept
    several levels at once, or a run of up to `move_bars` consecutive bars
    all moving in the retest direction. Returns a list of event dicts."""
    events = []
    for level_type in ("LHPB", "LLPB"):
        sub = retests_df[retests_df["type"] == level_type].copy()
        if sub.empty:
            continue
        sub["pos"] = sub["retest_time"].map(pos_by_ts)
        sub = sub.sort_values(["pos", "price"], ascending=[True, level_type == "LLPB"])
        run = []
        for _, row in sub.iterrows():
            if not run:
                run = [row]
                continue
            start_pos, last_pos, p = run[0]["pos"], run[-1]["pos"], row["pos"]
            joins = (p == last_pos) or (
                (p - start_pos) <= move_bars - 1
                and _is_directional_run(h1_df, level_type, start_pos, p))
            if joins:
                run.append(row)
            else:
                events.append(_event_from_run(h1_df, level_type, run))
                run = [row]
        if run:
            events.append(_event_from_run(h1_df, level_type, run))
    events.sort(key=lambda e: (e["start_pos"], e["type"]))
    for k, e in enumerate(events):
        e["event_id"] = k
    return events


def _event_from_run(h1_df, level_type, run):
    start_pos = int(run[0]["pos"])
    end_pos = int(max(r["pos"] for r in run))
    seg = h1_df.iloc[start_pos:end_pos + 1]
    return {
        "type": level_type,
        "is_long": level_type == "LHPB",
        "start_pos": start_pos,
        "end_pos": end_pos,
        "n_bars": end_pos - start_pos + 1,
        "start_time": h1_df.index[start_pos],
        "end_time": h1_df.index[end_pos],
        "event_high": float(seg["high"].max()),
        "event_low": float(seg["low"].min()),
        "retested_keys": {_level_key(r) for r in run},
        "n_retested": len(run),
    }


def _level_key(lv):
    return (str(lv["type"]), round(float(lv["price"]), 4),
            pd.Timestamp(lv["formation_time"]).value)


# ---------------------------------------------------------------------------
# Point-in-time set of levels awaiting a retest
# ---------------------------------------------------------------------------

def snapshot_pending(h1_df, positions):
    """Replay lxpb's state machine and snapshot `state["touch_lv1"]` (levels
    already broken out, still awaiting a retest) as of the OPEN of each
    requested bar position -- i.e. strictly before that bar is processed, so
    the snapshot contains no information from the event itself."""
    want = set(int(p) for p in positions)
    out = {}
    state = L.new_state()
    for i, bar in enumerate(h1_df.itertuples(index=True)):
        if i in want:
            out[i] = L._strip_internal(state["touch_lv1"])
        L.advance_one_bar(state, bar)
    return out


def _has_structural_gap(h1_df, pos_by_ts, lv):
    """Formation/breakout halves of render_labels_report._is_gap_lxpb (the
    retest half is N/A for a level that was never retested). Keeps the
    candidate universe consistent with the gap-filtered retest rows the
    events themselves were built from."""
    price = float(lv["price"])
    if not (lv["breakout_low"] <= price <= lv["breakout_high"]):
        return True
    fi = pos_by_ts.get(pd.Timestamp(lv["formation_time"]))
    if fi is not None and fi > 0:
        prev_bar = h1_df.iloc[fi - 1]
        cur_bar = h1_df.iloc[fi]
        if (cur_bar["low"] > prev_bar["high"]) or (cur_bar["high"] < prev_bar["low"]):
            return True
    return False


# ---------------------------------------------------------------------------
# Features (a) recency, (b) swing, (c) spike, (d) wick
# ---------------------------------------------------------------------------

def swing_pivots(h1_df, anchor_pos, lookback=SWING_LOOKBACK_BARS,
                 atr_mult=SWING_ATR_MULT, atr_period=SWING_ATR_PERIOD):
    """patterns-pure ATR-ZigZag pivots over the `lookback` H1 bars ENDING at
    `anchor_pos` (inclusive) -- point-in-time, so nothing after the event's
    first bar can influence whether a level counts as a swing. Cached per
    anchor because every candidate in an event shares the same anchor (they
    must all be judged on identical information)."""
    key = (int(anchor_pos), int(lookback), float(atr_mult), int(atr_period))
    if key in _SWING_CACHE:
        return _SWING_CACHE[key]
    lo = max(0, anchor_pos - lookback + 1)
    window = h1_df.iloc[lo:anchor_pos + 1]
    highs, lows = find_swings(window, atr_mult=atr_mult, atr_period=atr_period)
    result = (highs, lows)
    _SWING_CACHE[key] = result
    return result


def is_swing_pp(h1_df, level_type, formation_time, price, anchor_pos, **kw):
    """True if this level's own formation bar is a confirmed ZigZag pivot AND
    the pivot's extreme IS the level price (an LHPB is the formation bar's
    high, so it only counts as a swing high if that bar is the pivot)."""
    highs, lows = swing_pivots(h1_df, anchor_pos, **kw)
    pivots = highs if level_type == "LHPB" else lows
    if pivots.empty or formation_time not in pivots.index:
        return False
    col = "high" if level_type == "LHPB" else "low"
    return bool(abs(float(pivots.loc[formation_time, col]) - float(price)) < 1e-9)


def is_spike_pp(h1_df, pos_by_ts, level_type, formation_time):
    """patterns-pure spike on the formation bar: shooting star for an LHPB
    (a bar HIGH rejecting higher prices), hammer for an LLPB. Sliced 2 bars
    deep so the library's own shift(1) confirmation has a previous bar,
    mirroring render_labels_report.is_spike_pp / patterns-pure's
    lxpb_quality_gate.py convention."""
    fi = pos_by_ts[formation_time]
    slice_2 = h1_df.iloc[max(0, fi - 1):fi + 1]
    matched = (find_shooting_star(slice_2, atr=0.0) if level_type == "LHPB"
               else find_hammer(slice_2, atr=0.0))
    return bool(formation_time in matched.index)


def wick_metrics(h1_df, level_type, formation_time):
    bar = h1_df.loc[formation_time]
    total = float(bar["high"] - bar["low"])
    if level_type == "LHPB":
        wick = float(bar["high"] - max(bar["open"], bar["close"]))
        large = bool(has_large_upper_wick(bar, WICK_THRESHOLD))
    else:
        wick = float(min(bar["open"], bar["close"]) - bar["low"])
        large = bool(has_large_lower_wick(bar, WICK_THRESHOLD))
    return large, round((wick / total * 100.0) if total > 0 else 0.0, 1)


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------

def build_event_candidates(h1_df, pos_by_ts, event, pending, miss_band_pts=MISS_BAND_PTS_DEFAULT):
    """All levels the event's move could plausibly have filled: everything
    still awaiting a retest as of the event's first bar, priced inside the
    move's own range, PLUS a `miss_band_pts` tail just beyond the move's
    extreme (levels price reverted in front of -- the missed trades that
    stop "always take the deepest" from looking free)."""
    level_type = event["type"]
    if level_type == "LHPB":
        lo_band = event["event_low"] - miss_band_pts
        hi_band = event["event_high"]
    else:
        lo_band = event["event_low"]
        hi_band = event["event_high"] + miss_band_pts

    anchor_pos = event["start_pos"]
    cands = []
    for lv in pending:
        if lv["type"] != level_type:
            continue
        price = float(lv["price"])
        if not (lo_band <= price <= hi_band):
            continue
        if _has_structural_gap(h1_df, pos_by_ts, lv):
            continue
        formation_time = pd.Timestamp(lv["formation_time"])
        breakout_time = pd.Timestamp(lv["breakout_time"])
        f_pos, b_pos = pos_by_ts[formation_time], pos_by_ts[breakout_time]
        large_wick, wick_pct = wick_metrics(h1_df, level_type, formation_time)
        seg = h1_df.iloc[event["start_pos"]:event["end_pos"] + 1]
        touched_h1 = bool(((seg["low"] <= price) & (seg["high"] >= price)).any())
        cands.append({
            "event_id": event["event_id"],
            "type": level_type,
            "is_long": event["is_long"],
            "price": price,
            "formation_time": formation_time,
            "breakout_time": breakout_time,
            "recency_bars": int(b_pos - f_pos),
            "bars_breakout_to_event": int(anchor_pos - b_pos),
            "bars_formation_to_event": int(anchor_pos - f_pos),
            "is_swing_pp": is_swing_pp(h1_df, level_type, formation_time, price, anchor_pos),
            "is_swing_lxpb": bool(lv.get("is_swing")),
            "is_spike_pp": is_spike_pp(h1_df, pos_by_ts, level_type, formation_time),
            "is_spike_lxpb": bool(lv.get("is_spike")),
            "large_wick": large_wick,
            "wick_pct": wick_pct,
            "touched_h1": touched_h1,
            "was_retested": _level_key(lv) in event["retested_keys"],
            "breakout_high": float(lv["breakout_high"]),
            "breakout_low": float(lv["breakout_low"]),
        })

    # depth_rank: 1 = the level the move reaches FIRST (shallowest), n = the
    # deepest/last-reachable one. Reversal-point bias lives on this axis, so
    # it is reported explicitly rather than hidden.
    cands.sort(key=lambda c: -c["price"] if level_type == "LHPB" else c["price"])
    for k, c in enumerate(cands):
        c["depth_rank"] = k + 1
        c["n_candidates"] = len(cands)
    return cands


def score_candidates(cands, weights=None):
    """Within-cluster feature scoring. `recency_score` is min-max normalised
    inside the cluster (1.0 = the level formed closest to its own breakout
    bar), the other three are 0/1. Marks exactly one `selected` candidate."""
    weights = weights or WEIGHTS_DEFAULT
    if not cands:
        return cands
    rec = np.array([c["recency_bars"] for c in cands], dtype=float)
    lo, hi = rec.min(), rec.max()
    span = hi - lo
    for c, r in zip(cands, rec):
        c["recency_score"] = 1.0 if span <= 0 else float((hi - r) / span)
        c["score"] = round(
            weights["recency"] * c["recency_score"]
            + weights["swing"] * float(c["is_swing_pp"])
            + weights["spike"] * float(c["is_spike_pp"])
            + weights["wick"] * float(c["large_wick"]), 4)
    best = max(cands, key=lambda c: (c["score"], -c["recency_bars"], -c["depth_rank"]))
    for c in cands:
        c["selected"] = c is best
        c["recency_rank"] = 0
    for k, c in enumerate(sorted(cands, key=lambda c: (c["recency_bars"], c["depth_rank"]))):
        c["recency_rank"] = k + 1
    return cands


# ---------------------------------------------------------------------------
# Real 1-second tick resolution (fill, bounce, stop/target outcome)
# ---------------------------------------------------------------------------

def _empty_1s():
    return {
        "filled": False, "fill_time": None, "fill_order": None,
        "mfe": np.nan, "mae": np.nan, "immediate_bounce": np.nan,
        "held": None, "secs_to_peak": np.nan,
        "bid_vol_at_fill": np.nan, "ask_vol_at_fill": np.nan,
        "outcome": "missed", "r": 0.0, "exit_time": None, "exit_price": None,
    }


def measure_event_1s(event, cands, stop=STOP_DEFAULT, target=TARGET_DEFAULT,
                     horizon_hours=HORIZON_HOURS_DEFAULT,
                     bounce_seconds=BOUNCE_SECONDS_DEFAULT,
                     break_pts=BREAK_PTS_DEFAULT):
    """Fill / bounce / stop-target outcome for every candidate of one event,
    from real 1s ticks. One tick slice is loaded per event and every
    candidate is resolved by indexing FORWARD from its own fill tick, so no
    pre-entry tick can ever resolve a trade (cf. ../AGENTS.md)."""
    for c in cands:
        c.update(_empty_1s())

    start_utc = pd.Timestamp(event["start_time"], tz="UTC")
    fill_deadline = pd.Timestamp(event["end_time"], tz="UTC") + pd.Timedelta(hours=1)
    hi_utc = fill_deadline + pd.Timedelta(hours=horizon_hours)
    # Ticks are fetched from BEFORE the event purely so the 1min context
    # chart has "before" bars to show. Fill scanning below still starts at
    # `start_idx` (the event's own first bar) -- a level must not be able to
    # count a touch that happened before the retest move even began.
    ticks = R._ticks_for_window(start_utc - pd.Timedelta(minutes=TICK_PRE_PAD_MINUTES), hi_utc)
    if ticks is None or ticks.empty:
        event["has_ticks"] = False
        return cands
    event["has_ticks"] = True

    offset, sym = R._offset_for_ts(start_utc)
    event["offset"] = offset
    event["contract"] = sym

    t_index = ticks.index
    lows = ticks["Low"].to_numpy(float)
    highs = ticks["High"].to_numpy(float)
    bidv = ticks["BidVolume"].to_numpy(float)
    askv = ticks["AskVolume"].to_numpy(float)
    n = len(ticks)
    start_idx = int(t_index.searchsorted(start_utc, side="left"))
    deadline_idx = int(t_index.searchsorted(fill_deadline, side="left"))

    bars_1s = R._resample_1s(ticks)
    event["bars_1s"] = bars_1s

    is_long = event["is_long"]
    fills = []
    for c in cands:
        raw = c["price"] - offset
        # Approaching from the correct side, "price reached the level" is
        # simply low <= level (long) / high >= level (short) -- this also
        # covers lxpb's gap-over case (a print entirely through the level).
        # The resting-order side must actually trade for a fill: a resting
        # BUY needs a bid-side (seller-initiated) print.
        if is_long:
            reach = (lows <= raw) & (bidv > 0)
        else:
            reach = (highs >= raw) & (askv > 0)
        idx = np.flatnonzero(reach[start_idx:deadline_idx])
        if idx.size == 0:
            continue
        i0 = start_idx + int(idx[0])
        c["filled"] = True
        c["fill_time"] = t_index[i0]
        fills.append((i0, c))

        stop_price = raw - stop if is_long else raw + stop
        tgt_price = raw + target if is_long else raw - target
        if is_long:
            s_hits = np.flatnonzero(lows[i0:n] <= stop_price)
            t_hits = np.flatnonzero((highs[i0:n] >= tgt_price) & (askv[i0:n] > 0))
        else:
            s_hits = np.flatnonzero(highs[i0:n] >= stop_price)
            t_hits = np.flatnonzero((lows[i0:n] <= tgt_price) & (bidv[i0:n] > 0))
        s_i = int(s_hits[0]) if s_hits.size else None
        t_i = int(t_hits[0]) if t_hits.size else None
        # Tie inside one tick record resolves to the STOP (conservative).
        if t_i is not None and (s_i is None or t_i < s_i):
            c["outcome"], c["r"] = "target", float(target / stop)
            c["exit_time"] = t_index[i0 + t_i]
            c["exit_price"] = c["price"] + target if is_long else c["price"] - target
        elif s_i is not None:
            c["outcome"], c["r"] = "stop", -1.0
            c["exit_time"] = t_index[i0 + s_i]
            c["exit_price"] = c["price"] - stop if is_long else c["price"] + stop
        else:
            c["outcome"], c["r"] = "no_hit", 0.0

        _measure_bounce(c, bars_1s, raw, is_long, bounce_seconds, break_pts)

    for order, (_, c) in enumerate(sorted(fills, key=lambda x: x[0])):
        c["fill_order"] = order + 1

    # Trim the retained 1s series to what the renderer can actually show
    # (every fill happens before `fill_deadline`; charts pad at most ~20min
    # past it) -- the full horizon slice is only needed for stop/target
    # resolution above, and keeping all of it for every event would hold
    # hundreds of MB of 1s bars alive for no benefit.
    keep_hi = fill_deadline + pd.Timedelta(seconds=bounce_seconds) + pd.Timedelta(minutes=25)
    event["bars_1s"] = bars_1s.loc[(bars_1s.index >= start_utc - pd.Timedelta(minutes=TICK_PRE_PAD_MINUTES))
                                   & (bars_1s.index <= keep_hi)]
    return cands


def _measure_bounce(c, bars_1s, raw_level, is_long, bounce_seconds, break_pts):
    """1s reaction at the level, measured forward from the fill second:

    mfe / mae        - best & worst excursion from the level over the whole
                       `bounce_seconds` window (mfe is the reversal-point-
                       biased number; kept for reference).
    immediate_bounce - how far price bounced BEFORE trading `break_pts`
                       through the level. This is the comparable "did THIS
                       level produce a reaction" number: a shallow level
                       that visibly held for a while, then broke, still
                       scores, and the deepest level gets no automatic
                       credit just for sitting at the reversal.
    held             - price never traded `break_pts` through the level.
    """
    lo = c["fill_time"]
    hi = lo + pd.Timedelta(seconds=bounce_seconds)
    win = bars_1s.loc[(bars_1s.index >= lo.floor("s")) & (bars_1s.index <= hi)]
    if win.empty:
        return
    highs = win["High"].to_numpy(float)
    lows = win["Low"].to_numpy(float)
    if is_long:
        fav, adv = highs - raw_level, raw_level - lows
        broke = lows <= raw_level - break_pts
    else:
        fav, adv = raw_level - lows, highs - raw_level
        broke = highs >= raw_level + break_pts
    c["mfe"] = round(float(np.max(fav)), 2)
    c["mae"] = round(float(np.max(adv)), 2)
    peak_i = int(np.argmax(fav))
    c["secs_to_peak"] = int((win.index[peak_i] - win.index[0]).total_seconds())
    b_idx = np.flatnonzero(broke)
    if b_idx.size == 0:
        c["held"] = True
        c["immediate_bounce"] = c["mfe"]
    else:
        c["held"] = False
        upto = int(b_idx[0])
        c["immediate_bounce"] = round(float(np.max(fav[:upto])) if upto > 0 else 0.0, 2)
    at_fill = bars_1s.index.get_indexer([lo.floor("s")])
    if at_fill[0] >= 0:
        c["bid_vol_at_fill"] = float(bars_1s["BidVolume"].iloc[at_fill[0]])
        c["ask_vol_at_fill"] = float(bars_1s["AskVolume"].iloc[at_fill[0]])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def build_clusters(data_path=DEFAULT_DATA, start=START_DEFAULT, end=END_DEFAULT,
                   move_bars=MOVE_BARS_DEFAULT, miss_band_pts=MISS_BAND_PTS_DEFAULT,
                   min_cluster_size=MIN_CLUSTER_SIZE_DEFAULT,
                   min_retested=MIN_RETESTED_DEFAULT, limit=0,
                   stop=STOP_DEFAULT, target=TARGET_DEFAULT,
                   horizon_hours=HORIZON_HOURS_DEFAULT,
                   bounce_seconds=BOUNCE_SECONDS_DEFAULT,
                   break_pts=BREAK_PTS_DEFAULT, weights=None, with_ticks=True):
    """Full pipeline -> (h1_df, pos_by_ts, events, candidates_df).

    `events` keeps its per-event tick artefacts (`bars_1s`, `offset`) so the
    renderer can draw 1s charts without refetching."""
    h1_df, pos_by_ts, retests_df = load_context(data_path, start, end)
    events = find_retest_events(h1_df, pos_by_ts, retests_df, move_bars)
    print(f"Grouped into {len(events)} retest events "
          f"({sum(1 for e in events if e['n_retested'] >= 2)} swept >=2 levels at once)")

    snaps = snapshot_pending(h1_df, [e["start_pos"] for e in events])

    kept, all_cands = [], []
    for e in events:
        if e["n_retested"] < min_retested:
            continue
        cands = build_event_candidates(h1_df, pos_by_ts, e, snaps[e["start_pos"]], miss_band_pts)
        if len(cands) < min_cluster_size:
            continue
        score_candidates(cands, weights)
        e["candidates"] = cands
        kept.append(e)
    if limit:
        kept = kept[-limit:]
    print(f"{len(kept)} events with >={min_retested} levels swept and >={min_cluster_size} in-play "
          f"candidates (median {int(np.median([len(e['candidates']) for e in kept])) if kept else 0} "
          f"candidates/event)")

    for k, e in enumerate(kept):
        for c in e["candidates"]:
            c.update(_empty_1s())
        if with_ticks:
            measure_event_1s(e, e["candidates"], stop, target, horizon_hours,
                             bounce_seconds, break_pts)
            if (k + 1) % 10 == 0 or (k + 1) == len(kept):
                print(f"  resolved 1s ticks for {k + 1}/{len(kept)} events")
        all_cands.extend(e["candidates"])

    df = pd.DataFrame(all_cands)
    return h1_df, pos_by_ts, kept, df


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

RULES = {
    "score (all 4 features)": lambda cs: max(cs, key=lambda c: (c["score"], -c["recency_bars"], -c["depth_rank"])),
    "recency only": lambda cs: min(cs, key=lambda c: (c["recency_bars"], c["depth_rank"])),
    "swing only": lambda cs: max(cs, key=lambda c: (c["is_swing_pp"], -c["recency_bars"])),
    "spike only": lambda cs: max(cs, key=lambda c: (c["is_spike_pp"], -c["recency_bars"])),
    "large wick only": lambda cs: max(cs, key=lambda c: (c["large_wick"], -c["recency_bars"])),
    "shallowest (first reached)": lambda cs: min(cs, key=lambda c: c["depth_rank"]),
    "deepest (last reachable)": lambda cs: max(cs, key=lambda c: c["depth_rank"]),
}


def rule_table(events):
    """Expectancy of each one-pick-per-event selection rule, on equal terms:
    a pick that never filled scores 0 R (the missed trade), not NaN."""
    rows = []
    for name, pick in RULES.items():
        picks = [pick(e["candidates"]) for e in events if e["candidates"]]
        if not picks:
            continue
        rs = np.array([p["r"] for p in picks], float)
        filled = np.array([bool(p["filled"]) for p in picks])
        wins = np.array([p["outcome"] == "target" for p in picks])
        rows.append({
            "rule": name, "n_events": len(picks),
            "fill_rate": round(float(filled.mean()), 3),
            "win_rate_of_filled": round(float(wins[filled].mean()), 3) if filled.any() else np.nan,
            "avg_R": round(float(rs.mean()), 3),
            "total_R": round(float(rs.sum()), 2),
            "avg_bounce": round(float(np.nanmean([p["immediate_bounce"] for p in picks])), 2)
            if filled.any() else np.nan,
        })
    # Random baseline: expected value of picking uniformly at random.
    rand_r, rand_fill = [], []
    for e in events:
        cs = e["candidates"]
        if not cs:
            continue
        rand_r.append(float(np.mean([c["r"] for c in cs])))
        rand_fill.append(float(np.mean([bool(c["filled"]) for c in cs])))
    if rand_r:
        rows.append({"rule": "random pick (baseline)", "n_events": len(rand_r),
                     "fill_rate": round(float(np.mean(rand_fill)), 3),
                     "win_rate_of_filled": np.nan,
                     "avg_R": round(float(np.mean(rand_r)), 3),
                     "total_R": round(float(np.sum(rand_r)), 2), "avg_bounce": np.nan})
    return pd.DataFrame(rows)


FEATURE_COLS = [("is_swing_pp", "swing (patterns-pure)"),
                ("is_spike_pp", "spike (patterns-pure)"),
                ("large_wick", "large wick"),
                ("most_recent", "most recent to breakout")]


def feature_table(df, with_ticks=True):
    """Per-feature split over ALL candidates: does the feature separate the
    levels that actually filled and bounced from the ones that didn't?

    With `with_ticks=False` nothing was measured against real ticks, so the
    outcome columns are dropped rather than reported as a misleading run of
    zeroes and NaNs."""
    d = df.copy()
    d["most_recent"] = d["recency_rank"] == 1
    rows = []
    for col, label in FEATURE_COLS:
        for val in (True, False):
            sel = d[d[col] == val]
            if sel.empty:
                continue
            row = {"feature": label, "value": val, "n": len(sel)}
            if with_ticks:
                filled = sel[sel["filled"]]
                row.update({
                    "fill_rate": round(float(sel["filled"].mean()), 3),
                    "avg_bounce": round(float(filled["immediate_bounce"].mean()), 2) if len(filled) else np.nan,
                    "held_rate": round(float(filled["held"].mean()), 3) if len(filled) else np.nan,
                    "win_rate_of_filled": round(float((filled["outcome"] == "target").mean()), 3) if len(filled) else np.nan,
                    "avg_R": round(float(sel["r"].mean()), 3),
                })
            else:
                row["share"] = round(len(sel) / len(d), 3)
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--start", default=START_DEFAULT)
    ap.add_argument("--end", default=END_DEFAULT)
    ap.add_argument("--move-bars", type=int, default=MOVE_BARS_DEFAULT)
    ap.add_argument("--miss-band-pts", type=float, default=MISS_BAND_PTS_DEFAULT)
    ap.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE_DEFAULT,
                    help="min in-play candidates (filled OR missed) an event must offer")
    ap.add_argument("--min-retested", type=int, default=MIN_RETESTED_DEFAULT,
                    help="min levels the move must actually have swept")
    ap.add_argument("--limit", type=int, default=0, help="keep only the N most recent events")
    ap.add_argument("--stop", type=float, default=STOP_DEFAULT)
    ap.add_argument("--target", type=float, default=TARGET_DEFAULT)
    ap.add_argument("--horizon-hours", type=float, default=HORIZON_HOURS_DEFAULT)
    ap.add_argument("--bounce-seconds", type=int, default=BOUNCE_SECONDS_DEFAULT)
    ap.add_argument("--break-pts", type=float, default=BREAK_PTS_DEFAULT)
    ap.add_argument("--output", default=DEFAULT_CANDIDATES_CSV)
    ap.add_argument("--weights", default="",
                    help="reweight the four features, e.g. 'recency=1,swing=2,spike=0,wick=1.5'")
    ap.add_argument("--no-ticks", action="store_true",
                    help="skip the real-1s pass (no fills/bounces/outcomes) -- "
                         "fast structure-and-features-only run")
    args = ap.parse_args()

    _h1, _pos, events, df = build_clusters(
        args.data, args.start, args.end, args.move_bars, args.miss_band_pts,
        args.min_cluster_size, args.min_retested, args.limit, args.stop, args.target,
        args.horizon_hours, args.bounce_seconds, args.break_pts,
        weights=parse_weights(args.weights), with_ticks=not args.no_ticks)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)
    if args.no_ticks:
        print(f"\n=== Candidates: {len(df)} across {len(events)} events "
              f"(--no-ticks: no fills/bounces/outcomes measured) ===")
    else:
        print(f"\n=== Candidates: {len(df)} across {len(events)} events "
              f"({int(df['filled'].sum())} filled, {int((~df['filled']).sum())} never reached) ===")
        print("\n--- Selection rules (one pick per event; an unfilled pick scores 0 R) ---")
        print(rule_table(events).to_string(index=False))
    print("\n--- Feature splits over all candidates ---")
    print(feature_table(df, with_ticks=not args.no_ticks).to_string(index=False))

    keep = [c for c in df.columns if c not in ("bars_1s",)]
    df[keep].to_csv(args.output, index=False)
    print(f"\nPer-candidate table -> {args.output}")


if __name__ == "__main__":
    main()
