"""
render_ss_confl_finetune_report.py
====================================
New strategy report (not a modification of any existing one): among the same
"strong breakout" H1 LXPB retests used throughout this repo (see
render_stop_target_report.py / stop2_target8_trades_report.html), filter to
trades with SS Confl. >= `--ss-confl-min` (same-side confluence: other H1
levels of the SAME type, still un-retested as of this trade's own P1
breakout bar -- see lxpb_levels_cache.same_side_live_confluence), then:

  1. FINE-TUNE THE ENTRY. Build a "confluence group" = the subject's own H1
     level + its same-side H1 confluent levels (within +/-2.5pt, same
     radius as stop2_target8_trades_report.html's own Confl. column -- see
     lxpb_levels_cache.find_confluent_levels) + any same-side M5 levels in
     a WIDER +/-M5_CONFLUENCE_N_POINTS=5.0pt zone (M5 structure is much
     denser/finer-grained than H1, so the same 2.5pt radius used for H1
     would miss genuinely nearby M5 levels; found via the same
     find_confluent_levels query, just run a second time on the M5 ledger
     for the trade's own contract with the wider radius).
     The fine-tuned entry is the MOST EXTREME price in that group: the
     HIGHEST for an LLPB (short -- retest approaches from below, so a
     resting sell further up is strictly a better price if it fills) and the
     LOWEST for an LHPB (long -- retest approaches from above, so a resting
     buy further down is strictly better). When the subject's own H1 price
     is already the extreme, this is a no-op (alt entry == baseline entry).

  2. VERIFY THE FILL. A confluence member's own OWN breakout being confirmed
     by the subject's retest does NOT mean today's specific retest move
     actually swept far enough into the zone to reach the extreme price too.
     `_find_alt_fill` re-scans real 1s ticks, forward-only from the
     subject's own H1 retest hour (never backward -- a resting order can
     only be filled once price arrives, so this cannot look ahead), for the
     first tick where the CORRECT aggressor side (bid-side for a long's
     resting buy, ask-side for a short's resting sell -- same fill-realism
     convention as analyze_breakout_exits_1min._find_trade_touch_time)
     touches or gaps through the extreme price, bounded to
     `--max-alt-fill-hours` (default 3h -- the M5 confluence zone itself is
     only +/-M5_CONFLUENCE_N_POINTS=5.0pt wide, so a much longer search
     would just be reaching for an unrelated later move). If it is never
     touched in that window the fine-tuned order is marked UNFILLED and
     excluded
     from the fine-tuned population's stats (a resting order that never
     fills is not a trade, not a loss).

  3. FINE-TUNE THE EXIT. Instead of a fixed point target, the target is the
     CLOSEST-in-price opposite-type M5 LXPB level, on the favourable side of
     the fine-tuned entry, among candidates whose OWN M5 breakout bar (P1)
     is SHARED with at least one other M5 level of the same (opposite)
     type, i.e. >=2 M5 levels of that type broke out on the exact same M5
     bar (a stronger structural confirmation than a lone breakout --
     concretely: an LLPB/short's target candidates are M5 LHPB levels above
     entry; an LHPB/long's target candidates are M5 LLPB levels below
     entry; among those sharing a breakout bar with another same-type
     level, pick whichever price is NEAREST to the entry -- since price can
     only reach a target by moving continuously in the trade's favour, the
     nearest qualifying level is necessarily the one reached first, with no
     need to scan forward through price action to determine that), among
     M5 levels with a CONFIRMED breakout at or before the fine-tuned fill
     time (no look-ahead -- only structure that already existed when the
     trade opened can be used as a target). If no qualifying M5 level
     exists, or the nearest one is closer than `MIN_DYNAMIC_TARGET_PTS` to
     the entry (no room), this falls back to a fixed `--fallback-target`
     point target (default 8.0, matching stop2_target8_trades_report.html)
     and is flagged as such in the report so fallback rows can be told
     apart from genuine M5-target rows.

  Stop stays a fixed `--stop` point distance (default 2.0) from the
  fine-tuned entry -- this report only tests fine-tuning the entry and the
  target, not the stop.

  4. DE-DUPLICATE MUTUALLY-CONFLUENT LEVELS INTO ONE TRADE. Several distinct
     H1 levels of the same type can be mutually same-side-confluent with
     each other (each qualifies in the others' SS Confl. set), and a single
     H1 candle can retest/sweep all of them at once. Taken independently
     that produces one candidate row per level -- but in live trading a
     confluence zone is ONE resting order, decided once, not re-evaluated
     per member; treating each member as its own trade double/triple-counts
     the same physical fill in the stats. `cluster_candidates` groups
     candidate rows into connected components (union-find over "my own
     level appears in your same-side confluence set, or vice versa"), and
     `cluster_confluence` recomputes the fine-tuned entry from the UNION of
     every cluster member's own confluence search (each still centered on
     that member's own price -- a confluence zone is only meaningful
     relative to a level) rather than a single member's own local
     +/-CONFLUENCE_N_POINTS window. This also fixes "near miss" cases where
     one member's own window was too narrow to reach a price a neighboring
     member's window could see, not just exact duplicates. Levels that are
     one of the cluster's own members are excluded from the Confl./SS Confl.
     counts (they are the trade itself, not external supporting structure)
     but still compete for the extreme-price pick like any other candidate.
     The table's H1 Entry cell shows a dotted-underline tag with a hover
     tooltip listing every merged member's own price when a cluster has
     more than one member.

Both legs reuse the exact tick-accurate resolution machinery the rest of
this repo depends on (render_stop_target_report.resolve_trades /
_compute_excursion, which pin every exit to the exact second via
analyze_breakout_exits_1min._pin_exact_exit's `not_before`-guarded scan) --
nothing here re-implements stop/target/MAE/MFE resolution from scratch, so
this report cannot reintroduce the look-ahead bug classes documented in
those modules.

Each row also carries a BASELINE column: the same subject trade resolved at
the ORIGINAL H1 entry price with a fixed stop=`--baseline-stop`/
target=`--baseline-target` (default 2/8, i.e. exactly
stop2_target8_trades_report.html's own trade) -- a same-population,
side-by-side comparison of "fine-tuned" vs "as originally reported".

Design choices worth flagging explicitly (this script tests one reading of
an ambiguous strategy idea -- these are the specific calls made, revisit if
they don't match the intended strategy):
  - The M5 confluence-zone search for entry fine-tuning shares its
    min_formation_time lookback bound with the H1 search (same absolute
    cutoff timestamp, not a separately-tuned M5 lookback).
  - An unfilled fine-tuned entry is excluded from stats rather than falling
    back to the baseline price -- see point 2 above.

Usage:
    python render_ss_confl_finetune_report.py
    python render_ss_confl_finetune_report.py --ss-confl-min 2 --stop 2 --fallback-target 8
    python render_ss_confl_finetune_report.py --max-rows 5   # quick smoke test
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_labels_report as R          # noqa: E402
import analyze_breakout_exits as A        # noqa: E402
import analyze_breakout_exits_1min as M   # noqa: E402
import lxpb_levels_cache as LC            # noqa: E402
import render_stop_target_report as SR    # noqa: E402

SS_CONFL_MIN_DEFAULT = 1
DEFAULT_STOP = 2.0
DEFAULT_FALLBACK_TARGET = 8.0
DEFAULT_BASELINE_STOP = 2.0
DEFAULT_BASELINE_TARGET = 8.0
MAX_ALT_FILL_HOURS_DEFAULT = 3.0
MIN_DYNAMIC_TARGET_PTS = 1.0   # sanity floor: an opposite M5 level nearer than this to
                               # the fine-tuned entry isn't a usable target (no room)
M5_CONFLUENCE_N_POINTS = 5.0   # WIDER than SR.CONFLUENCE_N_POINTS (2.5, H1-to-H1): M5
                               # levels are finer-grained/denser than H1 ones, so a
                               # narrower same radius as H1 confluence misses genuinely
                               # nearby M5 structure (see module docstring point 1).
PEG_STEP_DEFAULT = R.TICK_SIZE_DEFAULT  # 0.25pt -- one ES tick per re-quote
PEG_CAP_DEFAULT = 1.0                   # max total chase distance from the fine-tuned
                                        # alt_price before a peg just sits as a plain
                                        # limit for the rest of the fill window
HORIZON_HOURS = A.HORIZON_BARS  # 72h forward window, same as the rest of the repo

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# --------------------------------------------------------------------------
# Selection: same "strong breakout" trades, filtered to SS Confl >= threshold
# --------------------------------------------------------------------------

def select_candidates(ss_confl_min, start=None, end=None, limit=A._DEFAULT):
    """Same H1 selection as render_stop_target_report._select_rows, filtered
    down to rows whose same-side H1 confluence count meets `ss_confl_min`.
    Returns (h1_df, pos_by_ts, strong, candidates) where `candidates` is a
    list of dicts (row index `i`, the row itself, its H1 confluent/same-side
    frames, and the min_formation_time lookback bound used to compute them
    -- shared with the M5 side so "how far back confluence may reach" means
    the same absolute cutoff on both timeframes)."""
    h1_df, pos_by_ts, strong, _trades = SR._select_rows(start, end, limit)
    ledger_h1 = LC.h1_levels()
    candidates = []
    for i in range(len(strong)):
        row_d = strong.iloc[i]
        breakout_pos = pos_by_ts[row_d["breakout_time"]]
        lookback_pos = max(0, breakout_pos - SR.CONFLUENCE_LOOKBACK_BARS)
        min_formation_time = h1_df.index[lookback_pos]
        confluent_h1 = LC.find_confluent_levels(
            ledger_h1, row_d["type"], float(row_d["price"]), row_d["formation_time"],
            row_d["retest_time"], SR.CONFLUENCE_N_POINTS, min_formation_time=min_formation_time)
        same_side_h1 = LC.same_side_live_confluence(confluent_h1, row_d["type"], row_d["breakout_time"])
        if len(same_side_h1) < ss_confl_min:
            continue
        candidates.append({
            "i": i, "row": row_d, "confluent_h1": confluent_h1, "same_side_h1": same_side_h1,
            "min_formation_time": min_formation_time,
        })
    return h1_df, pos_by_ts, strong, candidates


def m5_confluence_for_row(row_d, min_formation_time):
    """Same query as select_candidates' H1 side, run on the M5 ledger for
    whichever contract was front-month at this trade's retest. Returns
    (m5_ledger, confluent_m5, same_side_m5); m5_ledger/confluent_m5/
    same_side_m5 are all empty (not None) when the contract has no M5 data."""
    m5_ledger = LC.m5_levels_for_ts(row_d["retest_time"])
    if m5_ledger is None or m5_ledger.empty:
        empty = pd.DataFrame()
        return (m5_ledger if m5_ledger is not None else empty), empty, empty
    confluent_m5 = LC.find_confluent_levels(
        m5_ledger, row_d["type"], float(row_d["price"]), row_d["formation_time"],
        row_d["retest_time"], M5_CONFLUENCE_N_POINTS, min_formation_time=min_formation_time)
    same_side_m5 = LC.same_side_live_confluence(confluent_m5, row_d["type"], row_d["breakout_time"])
    return m5_ledger, confluent_m5, same_side_m5


def extreme_group_entry(row_d, same_side_h1, same_side_m5):
    """The fine-tuned entry: the most extreme (highest for LLPB, lowest for
    LHPB) price among the subject's own H1 level and every same-side H1/M5
    confluent level. Returns (price, source, group_size) where `source` is
    "own" / "h1" / "m5" -- which member supplied the extreme.

    NOTE: this single-row version is superseded by cluster_confluence()
    below for the actual report (see module docstring) -- kept only because
    it is the simplest correct building block to reason about; a cluster of
    >1 candidate row calls the same logic per member and unions the results
    rather than duplicating this function's body."""
    level_type = row_d["type"]
    prices = [float(row_d["price"])]
    sources = ["own"]
    for _, r in same_side_h1.iterrows():
        prices.append(float(r["price"])); sources.append("h1")
    for _, r in same_side_m5.iterrows():
        prices.append(float(r["price"])); sources.append("m5")
    idx = int(np.argmax(prices)) if level_type == "LLPB" else int(np.argmin(prices))
    return prices[idx], sources[idx], len(prices)


# --------------------------------------------------------------------------
# Confluence clustering -- collapse duplicate trades
# --------------------------------------------------------------------------
#
# A mutually-confluent group of same-type H1 levels is ONE resting order in
# live trading, not N. When a single H1 candle sweeps through several
# confluent levels at once, `select_candidates` (correctly, per its own
# per-level definition of "strong breakout") still emits one candidate row
# per level -- and since extreme_group_entry usually converges every member
# on the same fine-tuned price, those rows used to report the identical
# trade 2-4x over in the stats (e.g. rows 18/19/20 in the original ss_confl2
# report: three rows, one real trade). This section groups candidate rows
# into clusters (connected components of "my own level is in your
# same-side confluence set, or vice versa") and cluster_confluence() below
# computes the fine-tuned entry from the WHOLE cluster's combined
# confluence, not from a single member's own +/-CONFLUENCE_N_POINTS window
# -- fixing not just the exact duplicates but also the near-miss case (row
# 18's own window was too narrow to reach the M5 level 19/20 could see;
# unioning the cluster fixes that too, instead of just picking an arbitrary
# member as "the" trade).

def _level_key(level_type, price, formation_time):
    """Stable identity for one ledger level -- the same (type, price,
    formation_time) triple find_confluent_levels itself uses to exclude a
    subject's own row. Used here to de-duplicate a level that shows up via
    more than one cluster member's own confluence search."""
    ft = pd.Timestamp(formation_time)
    ft = ft.tz_localize("UTC") if ft.tzinfo is None else ft.tz_convert("UTC")
    return (level_type, round(float(price), 2), ft)


def cluster_candidates(candidates):
    """Group candidate rows into confluence clusters via union-find: two
    candidate rows join the same cluster if either one's own H1 level
    appears in the other's same_side_h1 confluence set. Returns a list of
    clusters (each a list of candidate dicts, singletons included), ordered
    by each cluster's lowest member index."""
    n = len(candidates)
    key_to_idx = {}
    for idx, cand in enumerate(candidates):
        row_d = cand["row"]
        key_to_idx[_level_key(row_d["type"], row_d["price"], row_d["formation_time"])] = idx

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for idx, cand in enumerate(candidates):
        for _, r in cand["same_side_h1"].iterrows():
            other = key_to_idx.get(_level_key(r["type"], r["price"], r["formation_time"]))
            if other is not None:
                union(idx, other)

    groups = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)
    ordered = sorted(groups.values(), key=min)
    return [[candidates[m] for m in members] for members in ordered]


def cluster_anchor(cluster):
    """The cluster's representative row for display/search purposes: the
    EARLIEST-retesting member (tie-broken by earliest formation, then
    lowest candidate index) -- the oldest, first-forming level the cluster
    grew around, and also the safest (earliest, so never-look-ahead) start
    point for find_alt_fill's forward tick scan."""
    def sort_key(cand):
        row_d = cand["row"]
        return (pd.Timestamp(row_d["retest_time"]), pd.Timestamp(row_d["formation_time"]), cand["i"])
    return min(cluster, key=sort_key)


def cluster_confluence(cluster):
    """Union every member's own H1/M5 confluence search (each still centered
    on that member's own price -- a confluence zone is only meaningful
    relative to a level) into one combined pool, then pick the fine-tuned
    entry as the extreme of the WHOLE pool -- not a single member's own
    local window. Entries matching one of the cluster's OWN member levels
    are excluded from the Confl./SS Confl. counts and the broader chart-ray
    pools (they are the trade itself, not external supporting structure),
    but still compete for the extreme-price pick like any other member.

    Returns a dict: alt_price/alt_source/group_n (the fine-tuned entry),
    confl_h1_n/ss_confl_n (external-confluence counts), confluent_h1/
    confluent_m5 (deduped broader pools for chart rays), and m5_ledger (one
    member's full M5 ledger -- same contract for the whole cluster -- for
    dynamic_target's own opposite-type search)."""
    level_type = cluster[0]["row"]["type"]

    own_keys = set()
    own_prices = []
    for cand in cluster:
        row_d = cand["row"]
        own_keys.add(_level_key(row_d["type"], row_d["price"], row_d["formation_time"]))
        own_prices.append(float(row_d["price"]))

    seen_h1, seen_m5 = {}, {}            # broader pools (any type) -> ledger row, for chart rays
    seen_same_h1, seen_same_m5 = {}, {}  # narrower same-type-live pools -> price, for Confl. counts
    m5_ledger = None

    for cand in cluster:
        row_d = cand["row"]
        for _, r in cand["confluent_h1"].iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_h1.setdefault(k, r)
        for _, r in cand["same_side_h1"].iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_same_h1.setdefault(k, float(r["price"]))

        ledger, confluent_m5, same_side_m5 = m5_confluence_for_row(row_d, cand["min_formation_time"])
        if m5_ledger is None and ledger is not None and not ledger.empty:
            m5_ledger = ledger
        for _, r in confluent_m5.iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_m5.setdefault(k, r)
        for _, r in same_side_m5.iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_same_m5.setdefault(k, float(r["price"]))

    prices = own_prices + list(seen_same_h1.values()) + list(seen_same_m5.values())
    sources = (["own"] * len(own_prices) + ["h1"] * len(seen_same_h1) + ["m5"] * len(seen_same_m5))
    idx = int(np.argmax(prices)) if level_type == "LLPB" else int(np.argmin(prices))

    confluent_h1 = pd.DataFrame(list(seen_h1.values())) if seen_h1 else pd.DataFrame()
    confluent_m5 = pd.DataFrame(list(seen_m5.values())) if seen_m5 else pd.DataFrame()

    return {
        "alt_price": prices[idx], "alt_source": sources[idx], "group_n": len(prices),
        "confl_h1_n": len(seen_h1), "ss_confl_n": len(seen_same_h1),
        "confluent_h1": confluent_h1, "confluent_m5": confluent_m5,
        "m5_ledger": m5_ledger if m5_ledger is not None else pd.DataFrame(),
    }


# --------------------------------------------------------------------------
# Fill / touch-time search (real ticks, forward-only, bounded)
# --------------------------------------------------------------------------

def find_alt_fill(row_d, alt_price, is_long, level_type, max_hours,
                   pegged=False, peg_step=None, peg_cap=None):
    """First tick (forward from the subject's own H1 retest hour, bounded to
    `max_hours`) where the correct-aggressor-side print touches or gaps
    through the resting price -- see module docstring point 2. Returns
    (touch_time, fill_price) -- (None, None) if never filled within the
    window (an honest "unfilled", unlike the naive touch-time helpers
    elsewhere in this repo which fall back to a fake hour-start touch).

    If `pegged` is False (default), the resting price is fixed at
    `alt_price` for the whole scan (a plain passive limit order) and
    `fill_price == alt_price` always.

    If `pegged` is True, simulates a peg-to-market / chasing limit order:
    the resting price starts at `alt_price` and, every time a tick trades
    AT the current resting price but on the WRONG side (i.e. the level is
    genuinely being printed through, just not by a counterparty willing to
    trade against our resting order), the order is cancelled and re-quoted
    `peg_step` closer to the market -- never worse than `peg_cap` total
    away from the original `alt_price`. This mirrors what a trader would
    do manually (or via Sierra Chart ACSIL cancel/replace automation --
    Sierra Chart has NO native peg-to-market order type for CME futures,
    see module docstring) when the level is trading but their exact resting
    price keeps getting missed by the correct side. Once the walk reaches
    `peg_cap`, it just sits there as a plain limit for the remainder of the
    window. `fill_price` reflects the ACTUAL (possibly chased/worse) price
    reached, not necessarily the original `alt_price`."""
    retest_time = pd.Timestamp(row_d["retest_time"], tz="UTC")
    hi = retest_time + pd.Timedelta(hours=max_hours)
    ticks = R._ticks_for_window(retest_time, hi)
    if ticks is None or ticks.empty:
        return None, None
    offset, _sym = R._offset_for_ts(retest_time)
    raw_alt = alt_price - offset
    win = ticks.loc[ticks.index >= retest_time]

    current = raw_alt
    if pegged:
        peg_step = PEG_STEP_DEFAULT if peg_step is None else peg_step
        peg_cap = PEG_CAP_DEFAULT if peg_cap is None else peg_cap
        worst = raw_alt - peg_cap if level_type == "LLPB" else raw_alt + peg_cap
    for ts, r in win.iterrows():
        right_side = (r.BidVolume > 0) if is_long else (r.AskVolume > 0)
        touched = r.Low <= current <= r.High
        gap_over = (r.High < current) if level_type == "LHPB" else (r.Low > current)
        if (touched or gap_over) and right_side:
            return ts, current + offset
        if pegged and touched and current != worst:
            current = (max(current - peg_step, worst) if level_type == "LLPB"
                       else min(current + peg_step, worst))
    return None, None



def baseline_touch_time(row_d, is_long):
    """Tick-accurate touch time for the subject's OWN (unmodified) H1 level
    -- reuses analyze_breakout_exits_1min._find_trade_touch_time exactly, so
    the baseline column always matches stop2_target8_trades_report.html's
    own resolution for the same trade."""
    t = {"retest_time": row_d["retest_time"], "entry": float(row_d["entry_price"]),
         "is_long": is_long, "type": row_d["type"]}
    return M._find_trade_touch_time(t)


def build_minute_bars(touch_time, horizon_hours=HORIZON_HOURS):
    """1-minute OHLC from real ticks, anchored at `touch_time`, same
    construction as analyze_breakout_exits_1min.build_or_load_1min_series
    but built fresh each call (no CSV cache -- this report's per-price/
    per-touch-time keys aren't a stable fit for that cache's index-based
    keying, see that function's own cache-collision warning). In-process
    tick caching (render_labels_report._load_contract) still makes repeat
    calls within one run cheap after the first contract load."""
    hi_needed = touch_time + pd.Timedelta(hours=horizon_hours)
    ticks = R._ticks_for_window(touch_time, hi_needed)
    if ticks is None or ticks.empty:
        return None
    bars = ticks.resample("1min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars = bars.dropna(subset=["Close"])
    bars.columns = ["open", "high", "low", "close"]
    bars.index.name = "time"
    bars.attrs["touch_time"] = touch_time
    return bars


def build_fill_window_chart(row_d, alt_price, level_type, max_hours, fail_reason):
    """1-minute price-action pane covering the WHOLE fill-search window
    (the subject's own retest_time through +max_hours, plus half an hour of
    trailing context) for an entry that never filled -- lets a human
    reviewer actually see what price did (e.g. touched the level on the
    wrong side, never got close, or the data simply ran out) instead of
    just a bare "UNFILLED" label with no way to inspect it. Shows the
    intended resting price as a horizontal line; there is no resolved trade
    to annotate so no exit/outcome markers are drawn. Returns None if there
    is no tick data at all in the window (same "no data" fallback every
    other pane here uses).

    Ticks come back from R._ticks_for_window in RAW per-contract terms (see
    find_alt_fill/resolve_trades' own offset handling above) -- this pane
    is a chart, not a fill search, so it converts to the same continuous/
    display scale as `alt_price` and every other pane in this report."""
    retest_time = pd.Timestamp(row_d["retest_time"], tz="UTC")
    hi = retest_time + pd.Timedelta(hours=max_hours) + pd.Timedelta(minutes=30)
    ticks = R._ticks_for_window(retest_time, hi)
    if ticks is None or ticks.empty:
        return None
    offset, _sym = R._offset_for_ts(retest_time)
    bars = ticks.resample("1min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars = bars.dropna(subset=["Close"])
    if bars.empty:
        return None
    candles = [{"time": R._to_epoch_utc(t), "open": float(r.Open) + offset,
               "high": float(r.High) + offset, "low": float(r.Low) + offset,
               "close": float(r.Close) + offset} for t, r in bars.iterrows()]
    price_lines = [{"price": alt_price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
                   "title": f"{level_type} {alt_price:.2f} (fine-tuned entry -- never filled)"}]
    title = (f"Fill window (1min)  |  {level_type} resting {alt_price:.2f}  |  retest "
            f"{R._to_pt_str(retest_time)}  &rarr;  +{max_hours:.1f}h  |  "
            f"{fail_reason or 'unfilled'}")
    return {"title": title, "candles": candles, "markers": [], "priceLines": price_lines, "precision": 2}


def dynamic_target(m5_ledger, level_type, alt_price, is_long, touch_time_alt):
    """The CLOSEST-in-price opposite-type M5 level to the entry, restricted
    to candidates whose OWN M5 breakout bar (P1) is SHARED with at least
    one other M5 level of the same (opposite) type -- i.e. >=2 M5
    <opposite_type> levels broke out on that EXACT M5 bar, a stronger
    structural confirmation than a lone breakout. A confirmed breakout at
    or before `touch_time_alt` is required (no look-ahead on the
    candidate's own formation) -- since a "shared P1 bar" pair always
    shares the identical breakout_time, this can be checked before or
    after the sharing test with the same result.

    Selection is by price proximity, NOT by which bar happens to touch it
    first: since price only reaches the target by moving continuously in
    the trade's favorable direction, the qualifying level nearest to the
    entry price is necessarily the one reached FIRST once price does move
    that way -- so this needs no forward scan of M5 bars at all, and is
    fully determinable at the moment of entry (no dependence on price
    action before OR after the fill, unlike a "first touched" scan which
    can pick a farther level over a nearer one if price happened to punch
    straight through the nearer one and only re-tag it later on a bounce).
    Returns (target_price, ledger_row) or (None, None) if none qualifies
    (caller falls back to a fixed point target)."""
    if m5_ledger is None or m5_ledger.empty:
        return None, None
    opposite_type = "LLPB" if level_type == "LHPB" else "LHPB"
    same_type = m5_ledger[(m5_ledger["type"] == opposite_type) & m5_ledger["breakout_time"].notna()]
    if same_type.empty:
        return None, None
    same_type = same_type[same_type["breakout_time"] <= touch_time_alt]
    if same_type.empty:
        return None, None
    shared_counts = same_type.groupby("breakout_time").size()
    shared_bars = shared_counts[shared_counts >= 2].index
    if shared_bars.empty:
        return None, None
    cand = same_type[same_type["breakout_time"].isin(shared_bars)]
    cand = cand[(cand["price"] > alt_price) if is_long else (cand["price"] < alt_price)]
    cand = cand[(cand["price"] - alt_price).abs() >= MIN_DYNAMIC_TARGET_PTS]
    if cand.empty:
        return None, None
    dist = (cand["price"] - alt_price).abs()
    best_row = cand.loc[dist.idxmin()]
    return float(best_row["price"]), best_row


# --------------------------------------------------------------------------
# Per-cluster processing (one cluster = one trade, see cluster_confluence)
# --------------------------------------------------------------------------

def process_cluster(cluster, args):
    anchor = cluster_anchor(cluster)
    row_d = anchor["row"]
    level_type = row_d["type"]
    is_long = level_type == "LHPB"

    conf = cluster_confluence(cluster)
    alt_price, alt_source, group_n = conf["alt_price"], conf["alt_source"], conf["group_n"]
    own_price = float(row_d["entry_price"])
    # Every cluster member's own H1 price, extreme-first, for the H1 Entry
    # column's hover tooltip -- the anchor's own price is shown in the cell
    # itself but a merged cluster came from more than one original level.
    member_prices = sorted({float(c["row"]["entry_price"]) for c in cluster},
                           reverse=(level_type == "LLPB"))

    result = {
        "i": anchor["i"], "row": row_d, "level_type": level_type, "is_long": is_long,
        "ss_confl": conf["ss_confl_n"], "group_n": group_n,
        "cluster_size": len(cluster), "cluster_members": member_prices,
        "own_price": own_price, "alt_price": alt_price, "alt_source": alt_source,
        "improved": abs(alt_price - own_price) > 1e-9,
        "confluent_h1": conf["confluent_h1"], "confluent_m5": conf["confluent_m5"],
        "confluent_combined": pd.concat([conf["confluent_h1"], conf["confluent_m5"]], ignore_index=True)
                              if not conf["confluent_m5"].empty else conf["confluent_h1"],
        "filled": False,
    }

    touch_time_alt, fill_price = find_alt_fill(
        row_d, alt_price, is_long, level_type, args.max_alt_fill_hours,
        pegged=args.pegged_entry, peg_step=args.peg_step, peg_cap=args.peg_cap)
    if touch_time_alt is None:
        result["fail_reason"] = "unfilled_within_window"
        return result
    result["fill_price"] = fill_price
    result["chased_pts"] = abs(fill_price - alt_price)

    bars = build_minute_bars(touch_time_alt)
    if bars is None or bars.empty:
        result["fail_reason"] = "no_tick_data_after_fill"
        return result

    # Everything downstream (target search, bracket, PnL/R) is relative to
    # the price ACTUALLY paid (fill_price), not the originally-quoted
    # alt_price -- identical when pegging is off (fill_price == alt_price).
    target_price, _target_row = dynamic_target(conf["m5_ledger"], level_type, fill_price, is_long,
                                               touch_time_alt)
    if target_price is None:
        target_pts = args.fallback_target
        target_price_disp = fill_price + target_pts if is_long else fill_price - target_pts
        target_source = "fallback_fixed"
    else:
        target_pts = abs(target_price - fill_price)
        target_price_disp = target_price
        target_source = "m5_opposite"
    stop_pts = args.stop

    trade = {"type": level_type, "entry": fill_price, "is_long": is_long,
             "retest_time": row_d["retest_time"], "stop_dist": stop_pts, "target_dist": target_pts}
    resolved = SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]
    # resolve_trades already computes favorable/adverse/giveback/entry_gapped
    # internally (via the same _compute_excursion/_compute_giveback this
    # report's charts rely on) -- read them straight off `resolved` rather
    # than re-deriving them a second time from the same bars.

    result.update({
        "filled": True, "touch_time_alt": touch_time_alt,
        "target_price": target_price_disp, "target_pts": target_pts, "target_source": target_source,
        "stop_pts": stop_pts, "resolved": resolved,
        "favorable_pts": resolved.get("favorable_pts"), "adverse_pts": resolved.get("adverse_pts"),
        "giveback_pts": resolved.get("giveback_pts"),
        "entry_gapped": resolved.get("entry_gapped", False),
        "stop_price": (fill_price - stop_pts) if is_long else (fill_price + stop_pts),
    })

    base_touch = baseline_touch_time(row_d, is_long)
    base_bars = build_minute_bars(base_touch)
    if base_bars is not None and not base_bars.empty:
        base_trade = {"type": level_type, "entry": own_price, "is_long": is_long,
                      "retest_time": row_d["retest_time"],
                      "stop_dist": args.baseline_stop, "target_dist": args.baseline_target}
        result["baseline_resolved"] = SR.resolve_trades([base_trade], {0: base_bars}, stop=None, target=None)[0]
    else:
        result["baseline_resolved"] = None
    return result


# --------------------------------------------------------------------------
# HTML report -- same layout as stop2_target8_trades_report.html
# (render_stop_target_report.render/_build_records): full H1 + M5 + 1s-trio +
# bid/ask-volume + 1min + footprint chart stack per row, the same gapped-
# entry tooltip and confluence-ray hover tooltips, the same Reviewed/Valid/
# Replayed/Notes columns persisted to localStorage with CSV export/import,
# and the same numeric/status filter panel + excursion-percentile summary --
# reusing SR.CSS/SR.JS/SR.build_trade_chart/SR.build_m5_chart/
# R.build_1s_trio_chart verbatim so this report never re-implements any of
# that rendering. Extra columns beyond the base layout are this strategy's
# own: Confl./SS Confl. (the confluence-group filter this report selects
# on), H1 Entry vs fine-tuned Entry (with a source tag h1/m5/own), Target
# Src (m5_opposite vs fallback_fixed), R (reward:risk on offer at entry)
# and PnL (realized points). Baseline (same subject trade at its original
# H1 price/stop2/target8) is still computed for the summary stats boxes
# but is no longer shown as its own table column.
# --------------------------------------------------------------------------

CSS = SR.CSS + """
<style>
.src-tag { font-size:0.75em; color:var(--text-dim); margin-left:4px; }
.src-tag.m5 { color:#38bdf8; }
.src-tag.h1 { color:#3b82f6; }
.src-tag.own { color:var(--text-dim); }
.src-tag.chase { color:#f59e0b; margin-left:6px; cursor:help; }
.unfilled-row td { color:var(--text-faint); font-style:italic; }
/* Keep the Type cell neutral (same as every other column) instead of the
   base report's bull/bear red-green coloring -- this report's whole point
   is comparing LLPB/LHPB trades side by side, not flagging direction. */
tr.lvl-row.type-lhpb td.type-cell, tr.lvl-row.type-llpb td.type-cell {
  color:var(--text); font-weight:normal;
}
/* H1 Entry cell for a merged confluence cluster (>1 physical H1 level
   collapsed into one trade, see cluster_candidates/cluster_confluence) --
   dotted underline hints that hovering shows the merged members. */
.cluster-tag { border-bottom:1px dotted var(--text-dim); cursor:help; }
</style>
"""

# Chart rendering (H1/M5/1s-trio/bid/ask/1min panes, ray hover tooltips,
# toggleChart), and Reviewed/Valid/Replayed/Notes persistence + CSV
# export/import + the numeric/status filter panel are ALL reused verbatim
# from render_stop_target_report.JS -- it already keys off the same
# th1-/ch1-/tm5-/cm5-/tc-/cc-/tb-/cb-/ta-/ca-/t1m-/c1m- element ids and the
# same data-confl/data-ssconfl row attributes this report's rows below emit,
# so nothing here needs its own copy of that logic.
JS = SR.JS


def _outcome_label(resolved):
    if resolved is None:
        return "NO DATA", ""
    o = resolved.get("outcome")
    if o == "target":
        return "WIN", "good"
    if o == "stop":
        return "LOSS", "bad"
    if o == "no_hit":
        return "NO-HIT", ""
    return "NO DATA", ""


def _stats_block(rows, key_outcome, key_r):
    vals_r = [r[key_outcome][key_r] for r in rows if r.get(key_outcome) is not None
              and r[key_outcome].get(key_r) is not None]
    wins = sum(1 for r in rows if r.get(key_outcome) and r[key_outcome].get("outcome") == "target")
    losses = sum(1 for r in rows if r.get(key_outcome) and r[key_outcome].get("outcome") == "stop")
    n = len(vals_r)
    return {
        "n": n, "wins": wins, "losses": losses,
        "win_rate": (wins / n * 100.0) if n else 0.0,
        "avg_r": (float(np.mean(vals_r)) if vals_r else 0.0),
        "total_r": (float(np.sum(vals_r)) if vals_r else 0.0),
    }


def build_chart_stack_for_row(h1_df, pos_by_ts, res):
    """Full per-row chart stack -- same four panes + footprint tables as
    render_stop_target_report._build_records, built from the FINE-TUNED
    entry/stop/target/resolved outcome instead of the original H1 level's
    own fixed bracket. Returns (chart_stack, fp) exactly like that
    function's own per-row locals."""
    row_d = res["row"]
    alt_price = res["fill_price"]  # the price ACTUALLY paid (== alt_price unless pegged)
    is_long = res["is_long"]
    resolved = res["resolved"]
    stop_pts, target_pts = res["stop_pts"], res["target_pts"]

    row_for_chart = row_d.copy()
    row_for_chart["price"] = alt_price
    chart_h1 = SR.build_trade_chart(h1_df, pos_by_ts, row_for_chart, None, resolved,
                                    stop_pts, target_pts, confluent=res["confluent_combined"])
    chart_h1["title"] += (f"  |  entry fine-tuned via {res['alt_source']} "
                          f"({res['group_n']} in group)  |  target: {res['target_source']}")
    if res.get("chased_pts", 0.0) > 1e-9:
        chart_h1["title"] += (f"  |  pegged fill: chased {res['chased_pts']:.2f}pt "
                              f"off {res['alt_price']:.2f} to {alt_price:.2f}")
    chart_m5 = SR.build_m5_chart(row_for_chart, resolved, stop_pts, target_pts)

    # build_1s_trio_chart reads "entry_price"/"fta"/"stop_loss" (not
    # "price") for its own entry/target/stop price lines -- override those
    # three fields (in the same adjusted scale as row_d["price"]) with the
    # fine-tuned entry and THIS combo's stop/target, same convention
    # _build_records uses to keep its trio/1min panes showing the same
    # bracket as the H1 pane.
    row_for_trio = row_d.copy()
    row_for_trio["entry_price"] = alt_price
    row_for_trio["fta"] = alt_price + target_pts if is_long else alt_price - target_pts
    row_for_trio["stop_loss"] = alt_price - stop_pts if is_long else alt_price + stop_pts
    trio_chart = R.build_1s_trio_chart(row_for_trio, R.PAD_SECONDS_DEFAULT,
                                       R.ONE_MIN_PAD_MINUTES_DEFAULT, True,
                                       touch_time_override=resolved.get("touch_time"))
    if trio_chart is not None:
        SR._relabel_fta_as_target(trio_chart["trio"])
        SR._relabel_fta_as_target(trio_chart["oneMin"])
        chart_stack = {"h1": chart_h1, "m5": chart_m5,
                       "trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
        fp = {"narrow": trio_chart.get("footprintNarrowHtml"),
              "wide": trio_chart.get("footprintWideHtml")}
    else:
        chart_stack = {"h1": chart_h1, "m5": chart_m5, "trio": None, "oneMin": None}
        fp = {"narrow": "<p class='note'>(no tick data in this window)</p>",
              "wide": "<p class='note'>(no tick data in this window)</p>"}
    return chart_stack, fp


def build_unfilled_chart_stack(h1_df, pos_by_ts, res, args):
    """Chart stack for a row whose fine-tuned entry never filled -- there is
    no resolved trade (no touch_time/exit/outcome) to build the usual
    stack from, but a reviewer still wants to SEE the price action rather
    than just read "UNFILLED". Reuses the H1/M5 panes with a stub resolved
    dict (outcome/exit/touch all None -- both build_trade_chart and
    build_m5_chart already handle a None exit_time/touch_time by falling
    back to retest_time) and args.stop/args.fallback_target as nominal
    reference lines (the real stop/target were never computed since there
    was no fill to search a dynamic target from). The 1s trio/1min pane is
    replaced by build_fill_window_chart, a 1-minute pane spanning the WHOLE
    fill-search window (not the usual +/-45s/+/-20min around a real touch,
    which doesn't exist here) so the reviewer can see exactly what price
    did during the search and why the fine-tuned entry was never reached
    by the correct side. No footprint tables (those need a precise tick-
    level touch instant this row never had)."""
    row_d = res["row"]
    level_type = res["level_type"]
    alt_price = res["alt_price"]
    resolved_stub = {"outcome": "no_data", "exit_time": None, "r": None, "touch_time": None}

    row_for_chart = row_d.copy()
    row_for_chart["price"] = alt_price
    chart_h1 = SR.build_trade_chart(h1_df, pos_by_ts, row_for_chart, None, resolved_stub,
                                    args.stop, args.fallback_target, confluent=res["confluent_combined"])
    for m in chart_h1["markers"]:
        if m.get("text") == "NO DATA":
            m["text"] = "UNFILLED"
    chart_h1["title"] += (f"  |  entry fine-tuned via {res['alt_source']} "
                          f"({res['group_n']} in group)  |  UNFILLED -- "
                          f"{res.get('fail_reason', '')}  (stop/target lines are nominal, "
                          f"never actually computed since there was no fill)")
    chart_m5 = SR.build_m5_chart(row_for_chart, resolved_stub, args.stop, args.fallback_target)

    fill_window = build_fill_window_chart(row_d, alt_price, level_type,
                                          args.max_alt_fill_hours, res.get("fail_reason"))
    chart_stack = {"h1": chart_h1, "m5": chart_m5, "trio": None, "oneMin": fill_window}
    note = "<p class='note'>(entry never filled -- no tick-level touch to build a footprint from)</p>"
    fp = {"narrow": note, "wide": note}
    return chart_stack, fp


N_COLS = 24  # keep in sync with `head` below and every colspan in this section


def render(args):
    h1_df, pos_by_ts, strong, candidates = select_candidates(
        args.ss_confl_min, args.start, args.end,
        (A._DEFAULT if args.limit is None else args.limit))
    print(f"{len(strong)} strong-breakout trades selected; "
          f"{len(candidates)} have SS Confl >= {args.ss_confl_min}", flush=True)
    if args.max_rows is not None:
        candidates = candidates[:args.max_rows]
        print(f"--max-rows: processing only the first {len(candidates)}", flush=True)

    clusters = cluster_candidates(candidates)
    n_merged = len(candidates) - len(clusters)
    if n_merged:
        print(f"{len(candidates)} candidate rows collapse into {len(clusters)} distinct "
              f"confluence clusters ({n_merged} duplicate row(s) merged)", flush=True)

    results = []
    for n, cluster in enumerate(clusters, start=1):
        anchor = cluster_anchor(cluster)
        row_d = anchor["row"]
        tag = f"  [cluster of {len(cluster)}]" if len(cluster) > 1 else ""
        print(f"  [{n}/{len(clusters)}] row {anchor['i']} {row_d['type']} "
              f"{float(row_d['price']):.2f} retest {R._to_pt_str(row_d['retest_time'])}{tag}", flush=True)
        results.append(process_cluster(cluster, args))

    filled = [r for r in results if r["filled"]]
    unfilled = [r for r in results if not r["filled"]]
    ft_stats = _stats_block(filled, "resolved", "r")
    base_stats = _stats_block(filled, "baseline_resolved", "r")
    improved_n = sum(1 for r in filled if r["improved"])

    win_mae_values = [r["adverse_pts"] for r in filled
                      if r["resolved"]["outcome"] == "target" and r.get("adverse_pts") is not None]
    loss_mfe_values = [r["favorable_pts"] for r in filled
                       if r["resolved"]["outcome"] == "stop" and r.get("favorable_pts") is not None]
    max_win_mae = max(win_mae_values) if win_mae_values else 0.0
    max_loss_mfe = max(loss_mfe_values) if loss_mfe_values else 0.0
    gapped_entries = sum(1 for r in filled if r.get("entry_gapped"))
    pctile_html = SR.excursion_percentile_html([
        ("MFE &mdash; losing trades", "ran this far in favour before hitting stop", loss_mfe_values),
        ("MAE &mdash; winning trades", "heat taken before reaching target", win_mae_values),
        ("Max DD &mdash; all trades", "handed back from the best price the open position reached",
         [r["giveback_pts"] for r in filled if r.get("giveback_pts") is not None]),
        ("Max DD &mdash; winning trades", "handed back before the winner reached target",
         [r["giveback_pts"] for r in filled
          if r["resolved"]["outcome"] == "target" and r.get("giveback_pts") is not None]),
    ], args.stop)

    charts = []
    rows_html = []
    for idx, res in enumerate(results):
        row_d = res["row"]
        level_type = res["level_type"]
        type_cls = "type-lhpb" if res["is_long"] else "type-llpb"
        confl_h1_n = len(res["confluent_h1"])
        retest_str = R._to_pt_str(row_d["retest_time"])
        if res.get("cluster_size", 1) > 1:
            members_str = ", ".join(f"{p:.2f}" for p in res["cluster_members"])
            h1_entry_title = (f' title="{res["cluster_size"]} mutually-confluent H1 levels '
                              f'merged into this one trade: {members_str}"')
            h1_entry_cell = f'<span class="cluster-tag"{h1_entry_title}>{res["own_price"]:.2f}\u2020</span>'
        else:
            h1_entry_cell = f'{res["own_price"]:.2f}'
        if not res["filled"]:
            chart_stack, fp = build_unfilled_chart_stack(h1_df, pos_by_ts, res, args)
            charts.append(chart_stack)
            fp_narrow_html = fp.get("narrow")
            fp_wide_html = fp.get("wide")
            fp_section = (
                f'<div class="chart-row-2col footprint-outer-row">'
                f'<div class="footprint-pair">'
                f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
                f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
                f'</div></div>'
            )
            rows_html.append(f"""
<tr class="lvl-row unfilled-row" data-idx="{idx}"
    data-confl="{confl_h1_n}" data-ssconfl="{res['ss_confl']}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td>{confl_h1_n}</td><td>{res['ss_confl']}</td>
  <td>{h1_entry_cell}</td>
  <td colspan="17">UNFILLED &mdash; {res.get('fail_reason', '')}</td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-row-2col">
      <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{idx}"></div><div class="chart-ph" id="ch1-{idx}"></div></div>
      <div class="chart-cell chart-h1"><div class="chart-title" id="tm5-{idx}"></div><div class="chart-ph" id="cm5-{idx}"></div></div>
    </div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{idx}"></div><div class="chart-ph" id="cc-{idx}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{idx}">Bid Volume</div><div class="chart-ph" id="cb-{idx}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{idx}">Ask Volume</div><div class="chart-ph" id="ca-{idx}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{idx}"></div><div class="chart-ph" id="c1m-{idx}"></div></div>
      </div>
    </div>
    {fp_section}
  </div></td>
</tr>""")
            continue

        resolved = res["resolved"]
        outcome_label, outcome_cls = _outcome_label(resolved)
        r_val = resolved.get("r")
        # R available at entry = reward:risk on offer for this trade's own
        # bracket, independent of how it actually resolved (target_pts and
        # stop_pts are both already fixed once the fine-tuned entry/target
        # are picked -- unlike resolved["r"], which collapses to -1.0 on a
        # loss, this stays the same number whether the trade wins or loses).
        rr_avail = res["target_pts"] / res["stop_pts"] if res["stop_pts"] else None
        rr_str = f"{rr_avail:.2f}" if rr_avail is not None else "-"
        # Realized PnL in points: resolved["r"] is already gain/stop_pts for
        # every outcome (including the variable "candle" case), so
        # multiplying back out gives the actual points won/lost without
        # re-deriving it from exit_price a second time. None (shown "-") for
        # no_hit/no_data, which have no realized exit.
        pnl_pts = (r_val * res["stop_pts"]) if r_val is not None else None
        pnl_str = format(pnl_pts, '+.2f') if pnl_pts is not None else "-"
        pnl_cls = "good" if (pnl_pts is not None and pnl_pts > 0) else (
            "bad" if (pnl_pts is not None and pnl_pts < 0) else "")
        exit_str = R._to_pt_str(resolved["exit_time"]) if resolved.get("exit_time") is not None else "-"
        exit_px = resolved.get("exit_price")
        exit_px_str = (f"{exit_px:.2f}" if resolved.get("outcome") != "no_hit"
                       and exit_px is not None else "-")
        entry_touch_str = R._to_pt_str(res["touch_time_alt"])
        mae_str = f"{res['adverse_pts']:.2f}" if res.get("adverse_pts") is not None else "-"
        mfe_str = f"{res['favorable_pts']:.2f}" if res.get("favorable_pts") is not None else "-"
        gb_str = f"{res['giveback_pts']:.2f}" if res.get("giveback_pts") is not None else "-"
        src_cls = f"src-tag {res['alt_source']}"
        improved_flag = " &uarr;" if res["improved"] else ""
        gap_flag = ('<span class="gap-flag" title="Entry price never traded between touch '
                    'and exit -- price gapped through the level, so this fill was not '
                    'actually available.">\u26a0</span>' if res.get("entry_gapped") else "")
        target_src_cls = "src-tag m5" if res["target_source"] == "m5_opposite" else "src-tag"
        chase_pts = res.get("chased_pts", 0.0)
        chase_flag = (f'<span class="src-tag chase" title="Pegged/chasing limit: original '
                      f'quote {res["alt_price"]:.2f} went unfilled at the right side, so the '
                      f'order re-quoted {chase_pts:.2f}pt toward the market to '
                      f'{res["fill_price"]:.2f}.">chased {chase_pts:.2f}pt '
                      f'&rarr; {res["fill_price"]:.2f}</span>'
                      if chase_pts > 1e-9 else "")
        entry_px_str = f"{res['fill_price']:.2f}"

        # Stable per-row key for the Reviewed/Replayed/Notes localStorage
        # store -- same identity scheme as render_stop_target_report's own
        # row_key (type + entry price + touch time), keyed to THIS report's
        # own fine-tuned entry so it never collides with the baseline
        # stop2_target8 report's saved notes for the same underlying trade.
        row_key = f"{level_type}_{res['alt_price']:.2f}_{entry_touch_str}".replace(" ", "_")

        chart_stack, fp = build_chart_stack_for_row(h1_df, pos_by_ts, res)
        charts.append(chart_stack)
        fp_narrow_html = fp.get("narrow")
        fp_wide_html = fp.get("wide")
        fp_section = (
            f'<div class="chart-row-2col footprint-outer-row">'
            f'<div class="footprint-pair">'
            f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
            f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
            f'</div></div>'
        )

        rows_html.append(f"""
<tr class="lvl-row {type_cls}" data-idx="{idx}" data-key="{row_key}"
    data-confl="{confl_h1_n}" data-ssconfl="{res['ss_confl']}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td>{confl_h1_n}</td><td>{res['ss_confl']}</td>
  <td>{h1_entry_cell}</td>
  <td>{res['alt_price']:.2f}{gap_flag}<span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td>{res['stop_price']:.2f}</td>
  <td>{res['target_price']:.2f}</td>
  <td><span class="{target_src_cls}">{res['target_source']}</span></td>
  <td>{rr_str}</td>
  <td class="{outcome_cls}">{outcome_label}</td>
  <td class="left">{exit_str}</td><td>{exit_px_str}</td>
  <td class="{pnl_cls}">{pnl_str}</td>
  <td class="bad">{mae_str}</td><td class="good">{mfe_str}</td><td>{gb_str}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb"></td>
  <td class="replayed-cell" onclick="event.stopPropagation();"><input type="checkbox" class="replayed-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-row-2col">
      <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{idx}"></div><div class="chart-ph" id="ch1-{idx}"></div></div>
      <div class="chart-cell chart-h1"><div class="chart-title" id="tm5-{idx}"></div><div class="chart-ph" id="cm5-{idx}"></div></div>
    </div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{idx}"></div><div class="chart-ph" id="cc-{idx}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{idx}">Bid Volume</div><div class="chart-ph" id="cb-{idx}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{idx}">Ask Volume</div><div class="chart-ph" id="ca-{idx}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{idx}"></div><div class="chart-ph" id="c1m-{idx}"></div></div>
      </div>
    </div>
    {fp_section}
  </div></td>
</tr>""")

    peg_lead_sentence = (
        f"Entry is a pegged/chasing limit order (re-quotes {args.peg_step:.2f}pt closer to "
        f"market, up to {args.peg_cap:.2f}pt total, on every wrong-side touch of the resting "
        f'price -- see the &quot;chased&quot; badge when this differs from the original '
        f"fine-tuned price)." if args.pegged_entry else
        "Entry is a plain static limit order (no chasing).")
    summary_html = f"""
<div class="summary">
  <div class="box"><strong>{len(strong)}</strong>strong-breakout trades</div>
  <div class="box"><strong>{len(candidates)}</strong>SS Confl &ge; {args.ss_confl_min}</div>
  <div class="box"><strong>{len(unfilled)}</strong>fine-tuned entry unfilled</div>
  <div class="box"><strong>{improved_n}</strong>/{len(filled)} entry improved over baseline</div>
  <div class="box true"><strong>{ft_stats['win_rate']:.1f}%</strong>fine-tuned win rate ({ft_stats['n']})</div>
  <div class="box"><strong>{ft_stats['avg_r']:.2f}</strong>fine-tuned avg R</div>
  <div class="box"><strong>{ft_stats['total_r']:.1f}</strong>fine-tuned total R</div>
  <div class="box"><strong>{base_stats['win_rate']:.1f}%</strong>baseline win rate (same {base_stats['n']} rows)</div>
  <div class="box"><strong>{base_stats['avg_r']:.2f}</strong>baseline avg R</div>
  <div class="box"><strong>{base_stats['total_r']:.1f}</strong>baseline total R</div>
  <div class="box"><strong>{max_win_mae:.2f}</strong>max MAE (win)</div>
  <div class="box"><strong>{max_loss_mfe:.2f}</strong>max MFE (loss)</div>
  <div class="box"><strong>{gapped_entries}</strong>gapped entry</div>
  <div class="box"><strong id="sum-shown">{len(filled)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>
  <div class="box"><strong id="sum-replayed">0</strong>replayed</div>
  <div class="toolbar">
    <button class="btn" onclick="exportReviewCsv()">\u2b07 Export notes CSV</button>
    <label class="btn" for="import-review-file">\u2b06 Import notes CSV</label>
    <input type="file" id="import-review-file" accept=".csv" class="hidden" onchange="importReviewCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved Reviewed/Valid/Replayed/Notes in this browser for this report?')) clearAllReview();">\U0001f5d1 Clear all</button>
  </div>
</div>
<p class="lead">Fine-tuned entry = most extreme price (highest for LLPB/short, lowest for
LHPB/long) among the subject's own H1 level and its same-side H1+M5 confluent levels (Own
Entry vs Entry columns; the src tag shows which member supplied the extreme: own/h1/m5),
verified filled on real ticks (forward-only, &le;{args.max_alt_fill_hours}h -- an entry never
reached in that window is UNFILLED and excluded from every stat here, not counted as a loss).
{peg_lead_sentence}
Target = the CLOSEST-in-price opposite-type M5 level on the favourable side, restricted to
candidates whose own M5 breakout bar is SHARED with at least one other same-type M5 level (&ge;2
M5 levels breaking out on the exact same bar), among those with a confirmed breakout by fill
time, else a fixed {args.fallback_target:.1f}pt
fallback (Target Src column). Stop is always a fixed
{args.stop:.1f}pt from the fine-tuned entry -- only entry and target are fine-tuned here. Baseline
= same subject trade at its ORIGINAL H1 price, stop={args.baseline_stop:.1f}/
target={args.baseline_target:.1f} (matches stop{SR._fmt_pts(args.baseline_stop)}_target
{SR._fmt_pts(args.baseline_target)}_trades_report.html). Charts/markers/price-lines/tooltips,
MAE/MFE/Max DD definitions, and the Reviewed/Valid/Replayed/Notes columns below all follow
render_stop_target_report.py's own conventions exactly (see that module and this one's
docstring for full detail and design-choice caveats).</p>
{pctile_html}
"""

    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Confl.</span>
    <select class="f-num-op" data-target="confl">
      <option value="any" selected>any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="confl" value="0" min="0" step="1">
  </div>
  <div class="filter-row">
    <span class="filter-label">SS Confl.</span>
    <select class="f-num-op" data-target="ssconfl">
      <option value="any" selected>any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="ssconfl" value="0" min="0" step="1">
  </div>
  <div class="filter-row">
    <span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="reviewed" checked> Reviewed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Valid</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="not_valid" checked> Not valid</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="valid" checked> Valid</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Replayed</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-replay" value="not_replayed" checked> Not replayed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-replay" value="replayed" checked> Replayed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Notes</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="no_notes" checked> No notes</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="has_notes" checked> Has notes</label>
  </div>
</div>
"""

    head = (f"<th class=\"left\">#</th><th class=\"left\">Type</th><th class=\"left\">Retest (H1)</th>"
            f"<th title=\"All H1 levels within {SR.CONFLUENCE_N_POINTS:.1f}pt (same construction as "
            f"stop2_target8_trades_report.html's own Confl. column)\">Confl.</th>"
            f"<th title=\"Same-side confluence: other H1 levels of the SAME type, still "
            f"un-retested as of this trade's own P1 breakout bar -- what this report filters on\">"
            f"SS Confl.</th>"
            f"<th title=\"The level's original H1 entry price, before fine-tuning to the "
            f"confluence group's extreme price\">H1 Entry</th><th>Entry</th>"
            f"<th class=\"left\">Entry (touch) time</th>"
            f"<th>Stop</th><th>Target</th><th>Target Src</th>"
            f"<th title=\"Reward:risk on offer for THIS trade's own bracket at entry "
            f"(target pts / stop pts) -- fixed once entry/target are picked, independent "
            f"of whether the trade goes on to win or lose\">R</th>"
            f"<th>Outcome</th><th class=\"left\">Exit time</th><th>Exit px</th>"
            f"<th title=\"Realized profit/loss in points (signed): +target pts on a win, "
            f"-stop pts on a loss\">PnL</th>"
            f"<th>MAE (win)</th><th>MFE (loss)</th><th>Max DD</th>"
            f"<th>Reviewed</th><th>Valid</th><th>Replayed</th>"
            f"<th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>SS Confl fine-tune report</title>
{CSS}
</head><body>
<h1>SS Confl. &ge; {args.ss_confl_min} fine-tuned entry/exit report</h1>
{summary_html}
{filter_panel}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{head}</tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table></div>
{JS.replace("__CHARTS_JSON__", json.dumps(charts))
   .replace("__STORAGE_KEY__", f"lxpb_ss_confl{args.ss_confl_min}_finetune_review_v1")}
</body></html>
"""
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved -> {args.output}")
    print(f"Fine-tuned: {ft_stats['n']} trades, win rate {ft_stats['win_rate']:.1f}%, "
          f"avg_R {ft_stats['avg_r']:.2f}, total_R {ft_stats['total_r']:.1f}")
    print(f"Baseline (same rows): {base_stats['n']} trades, win rate {base_stats['win_rate']:.1f}%, "
          f"avg_R {base_stats['avg_r']:.2f}, total_R {base_stats['total_r']:.1f}")
    print(f"Unfilled fine-tuned entries: {len(unfilled)}; entry improved over baseline: "
          f"{improved_n}/{len(filled)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SS Confl >= N fine-tuned entry (confluence-group extreme price) / "
                     "exit (closest-price, shared-P1-bar opposite M5 level) strategy report")
    parser.add_argument("--ss-confl-min", type=int, default=SS_CONFL_MIN_DEFAULT)
    parser.add_argument("--stop", type=float, default=DEFAULT_STOP)
    parser.add_argument("--fallback-target", type=float, default=DEFAULT_FALLBACK_TARGET)
    parser.add_argument("--baseline-stop", type=float, default=DEFAULT_BASELINE_STOP)
    parser.add_argument("--baseline-target", type=float, default=DEFAULT_BASELINE_TARGET)
    parser.add_argument("--max-alt-fill-hours", type=float, default=MAX_ALT_FILL_HOURS_DEFAULT)
    parser.add_argument("--pegged-entry", action=argparse.BooleanOptionalAction, default=True,
                        help="Simulate a peg-to-market/chasing limit order for the "
                             "fine-tuned entry instead of a plain static limit: on every "
                             "wrong-aggressor-side touch of the resting price, re-quote "
                             "--peg-step closer to market, up to --peg-cap total. ON by "
                             "default (models a live trader/ACSIL cancel-replace chasing "
                             "an unfilled fine-tuned entry); pass --no-pegged-entry for the "
                             "plain static-limit fill model the baseline stop2_target8 "
                             "report uses.")
    parser.add_argument("--peg-step", type=float, default=PEG_STEP_DEFAULT,
                        help=f"Re-quote increment in points for --pegged-entry (default "
                             f"{PEG_STEP_DEFAULT} = one ES tick).")
    parser.add_argument("--peg-cap", type=float, default=PEG_CAP_DEFAULT,
                        help=f"Max total chase distance in points from the fine-tuned "
                             f"entry for --pegged-entry (default {PEG_CAP_DEFAULT}).")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", default=None,
                        help="max rows, newest-first (int, or 'none'/omit for the default 300)")
    parser.add_argument("--max-rows", type=int, default=None,
                        help="process only the first N SS-Confl-qualifying rows (smoke test)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if args.limit is not None:
        args.limit = None if str(args.limit).lower() in ("none", "0", "all") else int(args.limit)
    args.output = args.output or os.path.join(
        _HERE, f"ss_confl{args.ss_confl_min}_finetune_report.html")
    render(args)
