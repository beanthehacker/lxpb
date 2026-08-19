"""
LXPB confluence layer for the absorption + volume strategy.

Wraps the H1 LXPB state machine (../lxpb.py) with:
  - MIN_HOURS_BEFORE_RETEST relaxed from 4h -> 2h (per strategy spec).
  - A "stacked levels" confluence counter: how many currently-active
    same-type levels (LHPB for long / LLPB for short) sit within N ticks
    of a given price.

Only `touch_lv1` (broken-out, awaiting-retest) levels are used for
confluence, not `touch_lv0` (never-touched levels). Rationale: a
zero-touch LHPB is just an untested historical swing high still sitting
*above* price with no confirmation it will act as support; only once
price has closed back above it (breakout -> touch_lv1) does it plausibly
flip into support-on-retest. Same logic mirrored for LLPB/resistance.

Confluence scorers, from oldest/slowest to current default:
  - `confluence_at` (legacy): uses the touch_lv1 set as of just before the
    H1 bar containing the query time, frozen for the WHOLE hour. This
    misses intra-hour consumption -- if price touches a level early in
    the hour, a later trigger in the same hour still "sees" it as valid.
  - `consumption_aware_confluence_at` (legacy 1s-per-bar version): valid
    levels for an hour are still exactly the official H1 output, but
    within the hour a level is immediately consumed the instant
    1-second price actually touches it. Correct, but requires
    `build_1s_consumption_state`, which materializes a full dict snapshot
    for EVERY 1-second bar -- with ~50-300 touch_lv1 levels active at any
    time and 1.3M+ 1s bars in a month of data, this took ~19 minutes and
    could not be reused across runs.
  - `level_intervals_confluence_at` (current default, used by
    combine_and_scan.py): same exact consumption semantics, but computed
    once into a compact per-level interval table (`build_level_intervals`)
    -- one row per level occurrence with a `valid_from`/`consumed_at`
    timestamp pair, instead of a dict per 1s bar. Querying confluence at
    an arbitrary timestamp is then a simple vectorized pandas filter.
    The interval table (and the underlying H1 snapshots) are transparently
    disk-cached via `lxpb_cache.py`, keyed off the H1/1s CSV file
    fingerprints, so repeated runs against the same data recompute
    nothing.
"""
import os
import sys
import bisect
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import lxpb as L  # noqa: E402  -- ../lxpb.py: canonical H1 LXPB detector
from lxpb import load_ohlc_data  # noqa: E402

TICK = 0.25
MIN_HOURS_BEFORE_RETEST = 2          # relaxed from the 4h default
N_TICKS_DEFAULT = 20
STACK_THRESHOLD_DEFAULT = 2

# lxpb.advance_one_bar reads this name as a module global on every call, so
# overriding it here changes the retest-firing rule for all downstream use
# of L.advance_one_bar without touching the original file.
L.MIN_HOURS_BEFORE_RETEST = MIN_HOURS_BEFORE_RETEST


def build_h1_snapshots(h1_csv_path):
    """
    Run the LXPB state machine bar-by-bar over the full H1 history.

    Returns
    -------
    retests_df : DataFrame of completed retests (2h rule applied)
    snapshots  : list of (bar_time, touch_lv0_copy, touch_lv1_copy), one
                 entry per H1 bar, captured BEFORE that bar is processed
                 (i.e. exactly what an intraday event inside that bar
                 would have seen -- no lookahead).
    bar_times  : list of bar_time values (same order as snapshots), for
                 use with bisect in `snapshot_for_time`.
    """
    df = load_ohlc_data(h1_csv_path)
    state = L.new_state()
    snapshots = []
    for bar in df.itertuples(index=True):
        snapshots.append((
            bar.Index,
            [dict(lv) for lv in state["touch_lv0"]],
            [dict(lv) for lv in state["touch_lv1"]],
        ))
        L.advance_one_bar(state, bar)
    bar_times = [s[0] for s in snapshots]
    retests_df = pd.DataFrame(state["retests"])
    return retests_df, snapshots, bar_times


def snapshot_for_time(snapshots, bar_times, ts_utc_naive):
    """Return the (touch_lv0, touch_lv1) snapshot for the H1 bar that
    contains `ts_utc_naive` (the last bar_time <= ts_utc_naive)."""
    idx = bisect.bisect_right(bar_times, ts_utc_naive) - 1
    if idx < 0:
        return [], []
    return snapshots[idx][1], snapshots[idx][2]


def confluence_at(snapshots, bar_times, ts_utc_naive, level_type, price,
                   n_ticks=N_TICKS_DEFAULT, threshold=STACK_THRESHOLD_DEFAULT):
    """
    Stacked-LXPB confluence filter at a given intraday timestamp/price.

    `level_type` is 'LHPB' for long (sell-absorption / bullish) triggers
    and 'LLPB' for short (buy-absorption / bearish) triggers.

    Returns (passes: bool, count: int, levels: list[dict]) using only
    touch_lv1 (broken-out, awaiting-retest) levels of the matching type.
    """
    _, lv1 = snapshot_for_time(snapshots, bar_times, ts_utc_naive)
    tol = n_ticks * TICK
    hits = [lv for lv in lv1 if lv["type"] == level_type and abs(lv["price"] - price) <= tol]
    return len(hits) >= threshold, len(hits), hits


def _level_key(lv):
    return (lv["type"], lv["price"], lv["formation_time"])


def build_1s_consumption_state(df_1s, snapshots, bar_times):
    """
    LEGACY / SLOW: superseded by `build_level_intervals` (below), which
    produces identical consumption semantics from a single forward pass
    without materializing a dict snapshot for every 1-second bar. Kept
    only for reference; combine_and_scan.py no longer calls this.

    Walk the 1-second bars in chronological order and track, for each
    timestamp, which of the H1-valid ("touch_lv1 as of just before the
    current H1 hour") levels have ALREADY been touched by 1s price action
    earlier in that same H1 hour and must therefore be treated as
    consumed/invalid from that point on.
    """
    df = df_1s.sort_index()
    active_before = {}          # ts -> valid-levels-dict snapshot (pre this bar)
    consumed_keys = set()       # keys consumed so far within the current H1 bin
    cur_bin_idx = None
    base_levels = {}

    for ts, row in df.iterrows():
        ts_utc_naive = ts.tz_convert("UTC").tz_localize(None)
        idx = bisect.bisect_right(bar_times, ts_utc_naive) - 1
        if idx != cur_bin_idx:
            # crossed into a new H1 hour -> fresh base set, fresh consumption
            cur_bin_idx = idx
            lv1 = [] if idx < 0 else snapshots[idx][2]
            base_levels = {_level_key(lv): lv for lv in lv1}
            consumed_keys = set()

        still_valid = {k: v for k, v in base_levels.items() if k not in consumed_keys}
        active_before[ts] = still_valid

        # now apply this bar's own touch (affects bars AFTER this one only)
        lo, hi = row["Low"], row["High"]
        for k, lv in still_valid.items():
            if lo <= lv["price"] <= hi:
                consumed_keys.add(k)

    return active_before


def consumption_aware_confluence_at(active_before, ts, level_type, price,
                                     n_ticks=N_TICKS_DEFAULT,
                                     threshold=STACK_THRESHOLD_DEFAULT):
    """LEGACY: paired with build_1s_consumption_state above; superseded by
    level_intervals_confluence_at. Same scoring as confluence_at, but
    against the 1s-consumption-aware still-valid level set for this exact
    timestamp."""
    lv1 = list(active_before.get(ts, {}).values())
    tol = n_ticks * TICK
    hits = [lv for lv in lv1 if lv["type"] == level_type and abs(lv["price"] - price) <= tol]
    return len(hits) >= threshold, len(hits), hits


def build_level_intervals(df_1s, snapshots, bar_times):
    """
    Fast replacement for build_1s_consumption_state: a single forward pass
    over the session's 1-second bars that produces a compact interval
    table -- one row per level occurrence -- instead of a dict snapshot
    per bar. Semantically identical consumption rule (a level is valid
    from the start of the H1 hour it first appears in the official
    touch_lv1 output, until the instant 1s price actually touches it, or
    until the H1 algorithm itself drops it from touch_lv1 for any other
    reason e.g. a gap-over retest/breakout that never technically
    "touched" the level at 1s resolution either).

    Only currently-OPEN levels are tracked with a live numpy array so the
    per-bar touch check is one vectorized comparison instead of a Python
    dict comprehension over every touch_lv1 level -- this is what makes
    it dramatically faster than build_1s_consumption_state on large
    (multi-day) 1s datasets.

    Returns a DataFrame with columns: type, price, formation_time,
    breakout_time, valid_from, consumed_at (naive UTC timestamps,
    matching `bar_times`; consumed_at is NaT if the level was never
    touched/dropped within the available 1s data).
    """
    df = df_1s.sort_index()
    idx = df.index
    ts_utc_naive = idx.tz_convert("UTC").tz_localize(None)
    bar_times_idx = pd.DatetimeIndex(bar_times)
    bin_idx = bar_times_idx.searchsorted(ts_utc_naive, side="right") - 1

    lows = df["Low"].to_numpy(dtype=float)
    highs = df["High"].to_numpy(dtype=float)
    ts_arr = ts_utc_naive.to_numpy()
    n = len(df)

    finished = []            # completed interval rows
    open_by_key = {}         # level_key -> row dict (still open)
    active_keys = []         # parallel to active_prices, for vectorized checks
    active_prices = np.empty(0, dtype=float)

    def _rebuild_active_arrays():
        nonlocal active_keys, active_prices
        active_keys = list(open_by_key.keys())
        active_prices = np.array([open_by_key[k]["price"] for k in active_keys], dtype=float)

    cur_bin = None
    for i in range(n):
        b = bin_idx[i]
        if b != cur_bin:
            cur_bin = b
            lv1 = [] if b < 0 else snapshots[b][2]
            new_keys = {_level_key(lv): lv for lv in lv1}
            hour_ts = ts_arr[i]

            # Present in the old open set but absent from this hour's
            # official snapshot -> the H1 algorithm itself consumed it
            # (touch or gap-over) without necessarily 1s-touching it in
            # our tracking; close the interval here.
            for k in list(open_by_key.keys()):
                if k not in new_keys:
                    row = open_by_key.pop(k)
                    row["consumed_at"] = hour_ts
                    finished.append(row)

            # New in this hour's snapshot and not already tracked ->
            # open a fresh interval starting at this hour's boundary.
            for k, lv in new_keys.items():
                if k not in open_by_key:
                    open_by_key[k] = {
                        "type": lv["type"], "price": lv["price"],
                        "formation_time": lv["formation_time"],
                        "breakout_time": lv.get("breakout_time"),
                        "valid_from": hour_ts, "consumed_at": None,
                    }
            _rebuild_active_arrays()

        if active_prices.size:
            lo, hi = lows[i], highs[i]
            touched = (active_prices >= lo) & (active_prices <= hi)
            if touched.any():
                ts_i = ts_arr[i]
                for k in [k for k, t in zip(active_keys, touched) if t]:
                    row = open_by_key.pop(k)
                    row["consumed_at"] = ts_i
                    finished.append(row)
                _rebuild_active_arrays()

    for row in open_by_key.values():
        row["consumed_at"] = None
        finished.append(row)

    out = pd.DataFrame(finished)
    if not out.empty:
        out["valid_from"] = pd.to_datetime(out["valid_from"])
        out["consumed_at"] = pd.to_datetime(out["consumed_at"])
        out = out.sort_values("valid_from").reset_index(drop=True)
    return out


def level_intervals_confluence_at(intervals_df, ts, level_type, price,
                                   n_ticks=N_TICKS_DEFAULT,
                                   threshold=STACK_THRESHOLD_DEFAULT):
    """
    Current default confluence scorer, used by combine_and_scan.py.
    Semantically identical to consumption_aware_confluence_at, but backed
    by the compact interval table from build_level_intervals (a
    vectorized pandas filter) instead of a dict-per-bar lookup.
    """
    if intervals_df.empty:
        return False, 0, []
    ts = pd.Timestamp(ts)
    ts_naive = ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts
    tol = n_ticks * TICK
    # NOTE: >= (not >) on consumed_at -- a level is still "active as of
    # ts" if it gets consumed BY the bar AT ts itself, matching the legacy
    # build_1s_consumption_state semantics where active_before[ts]
    # captures state just before ts's own touch is applied (its own
    # consuming touch only removes it from active_before[ts+1] onward).
    # This matters for burst-window-anchored lookups: if the window-start
    # bar is itself the one that consumes a level (e.g. the first bar of
    # a multi-second absorption burst), that level must still count as
    # confluence for later bars in the same burst.
    mask = (
        (intervals_df["type"] == level_type)
        & (intervals_df["valid_from"] <= ts_naive)
        & (intervals_df["consumed_at"].isna() | (intervals_df["consumed_at"] >= ts_naive))
        & ((intervals_df["price"] - price).abs() <= tol)
    )
    hits = intervals_df.loc[mask].to_dict("records")
    return len(hits) >= threshold, len(hits), hits
