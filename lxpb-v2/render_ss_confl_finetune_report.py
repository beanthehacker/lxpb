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
     level + its same-side H1 confluent levels (within
     +/-`--h1-confluence-points`, default 5.25pt) + any same-side M5
     levels within +/-M5_CONFLUENCE_N_POINTS=5.0pt. Both use
     lxpb_levels_cache.find_confluent_levels on their respective ledgers.
     The H1 radius applies to SS qualification, clustering and entry
     selection, not just deduplication. It is independent of the original
     stop/target reports' 2.5pt radius; the M5 entry radius is unchanged.
     The fine-tuned entry is the MOST EXTREME price in that group: the
     HIGHEST for an LLPB (short -- retest approaches from below, so a
     resting sell further up is strictly a better price if it fills) and the
     LOWEST for an LHPB (long -- retest approaches from above, so a resting
     buy further down is strictly better). When the subject's own H1 price
     is already the extreme, this is a no-op (alt entry == baseline entry).
     Entry candidates must still be live immediately before the cluster's
     first H1 retest, not merely at its earlier H1 breakout. M5 candidates
     must also have formed by that H1 breakout bar, as in the M5 pane.

  2. VERIFY THE FILL. A confluence member's own OWN breakout being confirmed
     by the subject's retest does NOT mean today's specific retest move
     actually swept far enough into the zone to reach the extreme price too.
     `find_alt_fill` re-scans real ticks, forward-only from the REFINED
     H1 level's own retest candle START, not the original cluster anchor's
     retest or the refined level's exact intrabar touch, for the
     first tick where the CORRECT aggressor side (bid-side for a long's
     resting buy, ask-side for a short's resting sell -- same fill-realism
     convention as analyze_breakout_exits_1min._find_trade_touch_time)
     touches or gaps through the extreme price, bounded to
     `--max-alt-fill-hours` (default 3h -- the M5 confluence zone itself is
     only +/-M5_CONFLUENCE_N_POINTS=5.0pt wide, so a much longer search
     would just be reaching for an unrelated later move). If it is never
     touched in that window, or the chosen H1 level has not yet been
     retested/consumed, the fine-tuned order is marked UNFILLED and excluded
     from the fine-tuned population's stats (a resting order that never
     fills is not a trade, not a loss).
     With pegging enabled, a replacement activates on the next tick record.
     If it crosses the opposing quote, it executes there rather than
     continuing to wait for a passive fill and chasing another tick.

  3. FINE-TUNE THE EXITS. The target is the MOST RECENTLY FORMED (P0)
     opposite-type M5 level on the favourable side of the actual fill:
     LHPB below an LLPB short, LLPB above an LHPB long. Its own breakout
     candle (P1) must have broken at least TWO distinct same-type P0 levels.
     Historical peers count toward this shared-P1 filter even if they have
     since been consumed, but the selected target itself must still be live.
     Only candidates 1..20 points from the fill qualify (inclusive). Select
     the newest eligible P0, NOT the nearest price or the newest P1. If
     none qualifies, use `--fallback-target` (default 8 points).

     The stop uses LIVE same-side M5 levels within +/-10 points of the
     actual fine-tuned fill. For an LLPB short, select the HIGHEST high
     among those levels' breakout candles and stop one tick above it.
     For an LHPB long, select the LOWEST breakout-candle low and stop one
     tick below it. The radius applies to the LEVEL prices, not the
     candle extremes or the eventual stop distance. If no protective
     candle stop qualifies, use `--fallback-stop` (default 4 points;
     `--stop` is retained as an alias). Both exit sources are displayed.

     Both searches use the M5 ledger state immediately before the fill's
     M5 bar: only completed candles can confirm a breakout or consumption.
     No current-bar high/low or later lifecycle event can leak into the
     bracket. There is no fixed formation lookback.

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
     one of the cluster's own members contribute once to the entry pool,
     rather than being added again through another member's confluence
     search. The table's
     Merged H1 levels column lists the distinct member prices, extreme-first,
     rather than the remaining external same-side count. A single-member
     trade lists its own H1 price. The H1 Entry tooltip also lists the merged
     prices when a cluster has more than one member. The radius limits each
     link, not the total cluster span: connected chains can span more than
     that radius and can include different breakout/retest bars.

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
  - No lookback bound on how far back a confluent H1/M5 level may have
    formed relative to the trade's own P1 (breakout) bar -- confluence is
    judged purely on liveness (confirmed breakout, not yet retested), same
    as render_stop_target_report.py (see its CONFLUENCE_N_POINTS comment
    for why an earlier fixed 100-bar cutoff was removed: it excluded
    genuinely live structure formed further back).
  - H1 c1..cN overlays show only same-side H1 support unconsumed immediately
    before the trade's P2, including other merged H1 members. M5 structure
    stays on the M5 pane.
  - An unfilled fine-tuned entry is excluded from stats rather than falling
    back to the baseline price -- see point 2 above.

Usage:
    python render_ss_confl_finetune_report.py
    python render_ss_confl_finetune_report.py --ss-confl-min 2 --fallback-stop 4 --fallback-target 8
    python render_ss_confl_finetune_report.py --h1-confluence-points 2.5  # previous H1 zone
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
DEFAULT_STOP = 4.0  # fallback only; a qualifying M5 breakout candle supplies the stop
DEFAULT_FALLBACK_TARGET = 8.0
DEFAULT_BASELINE_STOP = 2.0
DEFAULT_BASELINE_TARGET = 8.0
MAX_ALT_FILL_HOURS_DEFAULT = 3.0
MIN_DYNAMIC_TARGET_PTS = 1.0   # sanity floor: an opposite M5 level nearer than this to
                               # the fine-tuned entry isn't a usable target (no room)
MAX_DYNAMIC_TARGET_PTS = 20.0  # no qualifying M5 target in this band -> fixed 8pt fallback
DYNAMIC_STOP_RADIUS_PTS = 10.0
H1_CONFLUENCE_N_POINTS = 5.25  # smallest link joining the Jan-20 7014.25/7009.00 H1 groups
M5_CONFLUENCE_N_POINTS = 5.0
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

def _same_side_confluence(ledger, row, n_points):
    """Apply the existing same-side/P1 filter before the nearby-level query."""
    same_side = LC.same_side_live_confluence(ledger, row["type"], row["breakout_time"])
    return LC.find_confluent_levels(
        same_side, row["type"], float(row["price"]), row["formation_time"],
        row["retest_time"], n_points)


def select_candidates(ss_confl_min, start=None, end=None, limit=A._DEFAULT, merged=False,
                      h1_confluence_points=H1_CONFLUENCE_N_POINTS):
    """Same H1 selection as render_stop_target_report._select_rows, filtered
    down to rows whose same-side H1 confluence count meets `ss_confl_min`.
    Returns (h1_df, pos_by_ts, strong, candidates) where `candidates` is a
    list of dicts (row index `i`, the row itself, its same-side H1
    support). `h1_confluence_points` defines that support for qualification,
    clustering and entry selection alike. There is no formation lookback;
    the earlier fixed-bar cutoff is documented in render_stop_target_report.py's
    CONFLUENCE_N_POINTS comment. `merged` (see that report's --merged/--full-year)
    merges every TradingView H1 export instead of using only the single
    default one, for spans running past that export's last bar."""
    if not np.isfinite(h1_confluence_points) or h1_confluence_points < 0:
        raise ValueError("H1 confluence radius must be finite and non-negative")
    h1_df, pos_by_ts, strong, _trades = SR._select_rows(start, end, limit, merged=merged)
    ledger_h1 = LC.h1_levels()
    candidates = []
    for i in range(len(strong)):
        row_d = strong.iloc[i]
        same_side_h1 = _same_side_confluence(ledger_h1, row_d, h1_confluence_points)
        if len(same_side_h1) < ss_confl_min:
            continue
        candidates.append({
            "i": i, "row": row_d, "same_side_h1": same_side_h1,
        })
    return h1_df, pos_by_ts, strong, candidates


def m5_confluence_for_row(row_d):
    """Same query as select_candidates' H1 side, run on the M5 ledger for
    whichever contract was front-month at this trade's retest. Returns
    (m5_ledger, same_side_m5), both empty (not None) when the contract has
    no M5 data. Keep the full ledger for the independent exit searches."""
    m5_ledger = LC.m5_levels_for_ts(row_d["retest_time"])
    if m5_ledger is None or m5_ledger.empty:
        empty = pd.DataFrame()
        return (m5_ledger if m5_ledger is not None else empty), empty
    return m5_ledger, _same_side_confluence(m5_ledger, row_d, M5_CONFLUENCE_N_POINTS)


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
    """The cluster's representative row for qualification and baseline: the
    EARLIEST-retesting member (tie-broken by earliest formation, then
    lowest candidate index). Entry selection uses this first setup; the
    fill window instead starts at the selected refined H1 level's retest."""
    def sort_key(cand):
        row_d = cand["row"]
        return (pd.Timestamp(row_d["retest_time"]), pd.Timestamp(row_d["formation_time"]), cand["i"])
    return min(cluster, key=sort_key)


def _live_before_retest(levels, row, formed_by=None):
    if levels.empty:
        return levels.copy()
    levels = levels.copy()
    # Rebuilding a frame from ledger rows loses the timezone when every
    # value in a lifecycle column is NaT.
    for column in ("formation_time", "breakout_time", "death_time"):
        levels[column] = pd.to_datetime(levels[column], utc=True)
    as_of = pd.Timestamp(row["retest_time"]) - pd.Timedelta(nanoseconds=1)
    return LC.levels_live_as_of(
        levels, as_of, level_type=row["type"], formed_by=formed_by)


def h1_chart_confluence(cluster, level_price, level_formation_time):
    """Same-side H1 support still live immediately before the trade's P2.

    Include supporting members of a merged cluster, but not the H1 level
    already drawn in yellow or the separately drawn original P0. M5 levels
    belong on the M5 pane. Query just
    before P2 so levels retested on this same bar still show their
    confluence, while levels consumed on an earlier bar are excluded.
    """
    confluent = pd.concat([c["same_side_h1"] for c in cluster], ignore_index=True)
    if confluent.empty:
        return confluent
    row = cluster_anchor(cluster)["row"]
    confluent = _live_before_retest(confluent, row)
    confluent = confluent.drop_duplicates(["type", "price", "formation_time"])
    is_own = pd.Series(False, index=confluent.index)
    for price, formed in [(level_price, level_formation_time),
                          (row["price"], row["formation_time"])]:
        own_key = _level_key(row["type"], price, formed)
        is_own |= ((confluent["type"] == own_key[0]) &
                   (confluent["price"].round(2) == own_key[1]) &
                   (confluent["formation_time"] == own_key[2]))
    return confluent.loc[~is_own].reset_index(drop=True)


def cluster_confluence(cluster):
    """Union every member's own H1/M5 confluence search (each still centered
    on that member's own price -- a confluence zone is only meaningful
    relative to a level) into one combined pool, then pick the fine-tuned
    entry as the extreme of the WHOLE pool -- not a single member's own
    local window. Each cluster member's own level contributes once through
    the member-price pool, not again through another member's support pool.

    Returns a dict: alt_price/alt_source/group_n (the fine-tuned entry),
    alt_formation_time/alt_end_time (the specific level that SUPPLIED
    alt_price's own lifespan -- see below), h1_price/h1_source/
    h1_formation_time/h1_end_time (the closest H1-NATIVE level in the same
    pool -- own+h1 only, excluding m5 -- always used for the H1 chart's own
    level ray regardless of which pool alt_price itself came from, since the
    H1 pane should always show a genuine H1 LXPB structure with a real
    formation-to-retest span; alt_price/alt_formation_time/alt_end_time
    remain what the trade actually enters at, shown as a separate "entry"
    line when it differs), h1_retest_time (the selected H1 level's actual
    retest/consumption bar, or None if still open), and m5_ledger (the full
    M5 ledger for the entry setup's contract).

    alt_formation_time/h1_formation_time are always that specific level's
    own formation bar. External alt_end_time endpoints remain clipped to
    the first setup for display. The selected H1 ray ends at its actual
    retest/consumption bar; if still open, its displayed endpoint remains
    the first setup, but that is NOT a substitute fill-window anchor."""
    anchor_row = cluster_anchor(cluster)["row"]
    level_type = anchor_row["type"]
    cluster_retest_time = anchor_row["retest_time"]

    own_keys = set()
    own_prices, own_starts, own_ends = [], [], []
    for cand in cluster:
        row_d = cand["row"]
        own_keys.add(_level_key(row_d["type"], row_d["price"], row_d["formation_time"]))
        if (row_d["formation_time"] >= cluster_retest_time or
                row_d["breakout_time"] > cluster_retest_time):
            continue
        own_prices.append(float(row_d["price"]))
        own_starts.append(row_d["formation_time"])
        own_ends.append(row_d["retest_time"])

    seen_same_h1, seen_same_m5 = {}, {}
    m5_ledger = None

    for cand in cluster:
        row_d = cand["row"]
        for _, r in cand["same_side_h1"].iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_same_h1.setdefault(k, r)

        ledger, same_side_m5 = m5_confluence_for_row(row_d)
        if m5_ledger is None and ledger is not None and not ledger.empty:
            m5_ledger = ledger
        for _, r in same_side_m5.iterrows():
            k = _level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_same_m5.setdefault(k, r)

    def _naive(t):
        """Ledger timestamps (unlike row_d's, which come straight from
        h1_df's own naive index) are tz-aware UTC -- strip that label (same
        instant) so they compare directly against h1_df's naive index, same
        convention build_trade_chart's own confluence-ray code uses."""
        return t.tz_localize(None) if getattr(t, "tzinfo", None) is not None else t

    def _ext_end(r):
        """This external level's own end for ray-drawing: its own death (if
        known) clipped to the original entry-selection snapshot. This is
        display metadata, not the refined H1 fill-window trigger."""
        death = r["death_time"]
        if pd.isna(death):
            return cluster_retest_time
        return min(_naive(death), cluster_retest_time)

    # P1-live supports are useful for the SS filter, but a resting entry
    # cannot be based on a level consumed before this trade's P2.
    live_h1 = _live_before_retest(pd.DataFrame(list(seen_same_h1.values())), anchor_row)
    live_m5 = _live_before_retest(
        pd.DataFrame(list(seen_same_m5.values())), anchor_row,
        formed_by=(pd.Timestamp(anchor_row["breakout_time"]) + pd.Timedelta(hours=1)
                   - pd.Timedelta(nanoseconds=1)))
    cutoff = pd.to_datetime(cluster_retest_time, utc=True)
    same_h1_list = [r for _, r in live_h1.iterrows()
                    if pd.notna(r["breakout_time"]) and r["breakout_time"] < cutoff]
    same_m5_list = [r for _, r in live_m5.iterrows()
                    if pd.notna(r["breakout_time"]) and r["breakout_time"] < cutoff]
    prices = (own_prices + [float(r["price"]) for r in same_h1_list]
                         + [float(r["price"]) for r in same_m5_list])
    sources = (["own"] * len(own_prices) + ["h1"] * len(same_h1_list) + ["m5"] * len(same_m5_list))
    starts = (own_starts + [_naive(r["formation_time"]) for r in same_h1_list]
                         + [_naive(r["formation_time"]) for r in same_m5_list])
    ends = own_ends + [_ext_end(r) for r in same_h1_list] + [_ext_end(r) for r in same_m5_list]
    idx = int(np.argmax(prices)) if level_type == "LLPB" else int(np.argmin(prices))

    # The H1 chart always anchors its yellow level ray to the closest H1-
    # NATIVE LXPB level in the group -- own_prices + same_h1_list only, never
    # an M5 level -- regardless of which pool the fine-tuned entry (alt_price
    # above) actually came from. The strategy may enter on an M5-sourced
    # price, but the level being traded is always an H1 structure, so the
    # chart should never show a chart-window ray sourced from an M5 level
    # (formed/died at sub-hour granularity, often far from any H1 candle in
    # the anchor's own window -- see the "H1 yellow ray floats away from P0"
    # bug this fixes). own_prices is never empty (every cluster member's own
    # row is H1), so this always resolves to a real H1 level.
    h1_prices = own_prices + [float(r["price"]) for r in same_h1_list]
    h1_sources = ["own"] * len(own_prices) + ["h1"] * len(same_h1_list)
    h1_starts = own_starts + [_naive(r["formation_time"]) for r in same_h1_list]
    # These supports are already broken, so death is their first retest/
    # consumption bar, including an early touch that was not a trade signal.
    h1_retests = own_ends + [
        _naive(r["death_time"]) if pd.notna(r["death_time"]) else None
        for r in same_h1_list]
    h1_idx = int(np.argmax(h1_prices)) if level_type == "LLPB" else int(np.argmin(h1_prices))
    h1_retest_time = h1_retests[h1_idx]

    return {
        "alt_price": prices[idx], "alt_source": sources[idx], "group_n": len(prices),
        "alt_formation_time": starts[idx], "alt_end_time": ends[idx],
        "entry_m5_level": (same_m5_list[idx - len(own_prices) - len(same_h1_list)].to_dict()
                           if sources[idx] == "m5" else None),
        "h1_price": h1_prices[h1_idx], "h1_source": h1_sources[h1_idx],
        "h1_formation_time": h1_starts[h1_idx],
        "h1_end_time": h1_retest_time if h1_retest_time is not None else cluster_retest_time,
        "h1_retest_time": h1_retest_time,
        "m5_ledger": m5_ledger if m5_ledger is not None else pd.DataFrame(),
    }


# --------------------------------------------------------------------------
# Fill / touch-time search (real ticks, forward-only, bounded)
# --------------------------------------------------------------------------

def _scan_alt_fill(ticks, raw_alt, is_long, pegged=False, peg_step=None, peg_cap=None):
    """Chronological raw-tick fill scan. Replacements activate on the next
    record, never on the record that caused the cancel/replace.

    Sierra single-trade records carry ask/bid in High/Low and the actual
    traded price in Close. A repriced order crossing the opposite quote
    is marketable: it fills at that quote without waiting for a passive
    aggressor-side print. Otherwise it rests at its limit. Queue position
    and cancel/replace latency beyond this next-record rule are not modeled.
    """
    current = raw_alt
    replace_pending = False
    if pegged:
        peg_step = PEG_STEP_DEFAULT if peg_step is None else peg_step
        peg_cap = PEG_CAP_DEFAULT if peg_cap is None else peg_cap
        if (not np.isfinite(peg_step) or peg_step <= 0 or
                not np.isfinite(peg_cap) or peg_cap < 0):
            raise ValueError("Peg step must be positive and peg cap nonnegative")
        for value in (peg_step, peg_cap):
            if not np.isclose(value / R.TICK_SIZE_DEFAULT,
                              round(value / R.TICK_SIZE_DEFAULT)):
                raise ValueError("Peg step and cap must be whole ES ticks")
        if (ticks["Trades"] > 1).any():
            raise ValueError("Pegged fills require single-trade bid/ask records, not aggregated bars")
        worst = raw_alt + peg_cap if is_long else raw_alt - peg_cap
    if not ticks.index.is_monotonic_increasing:
        ticks = ticks.sort_index(kind="stable")
    for r in ticks.itertuples():
        if replace_pending:
            quote = float(r.High if is_long else r.Low)
            if not np.isfinite(quote) or r.Low > r.High:
                raise ValueError(f"Invalid bid/ask quote at {r.Index}")
            marketable = current >= quote if is_long else current <= quote
            if marketable:
                return r.Index, quote
            replace_pending = False
        right_side = (r.BidVolume > 0) if is_long else (r.AskVolume > 0)
        traded_through = (r.Close <= current) if is_long else (r.Close >= current)
        if right_side and traded_through:
            return r.Index, current
        touched = r.Low <= current <= r.High
        if pegged and touched and not right_side and current != worst:
            current = (min(current + peg_step, worst) if is_long
                       else max(current - peg_step, worst))
            replace_pending = True
    return None, None


def find_alt_fill(window_start, alt_price, is_long, level_type, max_hours,
                   pegged=False, peg_step=None, peg_cap=None):
    """First fill in [refined H1 retest candle start, start + max_hours).

    Passive limits require a correct-side trade at/through their price.
    Pegged replacements may instead execute against the opposite quote;
    see _scan_alt_fill. No fill is (None, None), never a fabricated touch.
    """
    if is_long != (level_type == "LHPB"):
        raise ValueError("Entry direction does not match the LXPB level type")
    window_start = pd.to_datetime(window_start, utc=True)
    if pd.isna(window_start):
        raise ValueError("A refined H1 retest is required to start the fill window")
    if not np.isfinite(max_hours) or max_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")
    hi = window_start + pd.Timedelta(hours=max_hours)
    ticks = R._ticks_for_window(window_start, hi)
    if ticks is None or ticks.empty:
        return None, None
    offset, _sym = R._offset_for_ts(window_start)
    win = ticks.loc[(ticks.index >= window_start) & (ticks.index < hi)]
    touch, raw_fill = _scan_alt_fill(
        win, alt_price - offset, is_long, pegged, peg_step, peg_cap)
    return (touch, raw_fill + offset) if raw_fill is not None else (None, None)



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


def build_fill_window_chart(window_start, alt_price, level_type, max_hours, fail_reason):
    """1-minute price-action pane covering the WHOLE fill-search window
    (the refined H1 retest candle through +max_hours, plus half an hour of
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
    window_start = pd.to_datetime(window_start, utc=True)
    if pd.isna(window_start):
        raise ValueError("Cannot draw a fill window before the refined H1 retest")
    hi = window_start + pd.Timedelta(hours=max_hours) + pd.Timedelta(minutes=30)
    ticks = R._ticks_for_window(window_start, hi)
    if ticks is None or ticks.empty:
        return None
    ticks = ticks.loc[(ticks.index >= window_start) & (ticks.index < hi)]
    offset, _sym = R._offset_for_ts(window_start)
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
    title = (f"Fill window (1min)  |  {level_type} resting {alt_price:.2f}  |  refined H1 retest "
            f"{R._to_pt_str(window_start)}  &rarr;  +{max_hours:.1f}h  |  "
            f"{fail_reason or 'unfilled'}")
    return {"title": title, "candles": candles, "markers": [], "priceLines": price_lines, "precision": 2}


def _live_m5_before_entry(m5_ledger, level_type, touch_time, min_breakout_levels=1):
    """Live, confirmed structure from completed M5 bars only.

    Ledger event times label bar STARTS, so querying at the exact tick
    would expose the rest of its unfinished M5 candle. Shared-P1 counts
    describe the historical breakout and are computed before filtering
    out peers that have since died.
    """
    if m5_ledger is None or m5_ledger.empty:
        return pd.DataFrame()
    as_of = pd.to_datetime(touch_time, utc=True).floor("5min") - pd.Timedelta(nanoseconds=1)
    confirmed = m5_ledger[(m5_ledger["type"] == level_type) &
                          (m5_ledger["breakout_time"] <= as_of)]
    if min_breakout_levels > 1:
        counts = confirmed.groupby("breakout_time")["formation_time"].transform("nunique")
        confirmed = confirmed[counts >= min_breakout_levels]
    return LC.levels_live_as_of(confirmed, as_of)


def dynamic_target(m5_ledger, level_type, alt_price, is_long, touch_time_alt):
    """Newest live opposite P0, with >=2 P0s sharing its completed P1.

    Candidates must be 1..20 points in the fill's favourable direction.
    Returns (price, ledger_row), or (None, None) for the fixed fallback.
    """
    opposite_type = "LLPB" if level_type == "LHPB" else "LHPB"
    cand = _live_m5_before_entry(m5_ledger, opposite_type, touch_time_alt,
                                 min_breakout_levels=2)
    if cand.empty:
        return None, None
    distance = (cand["price"] - alt_price) if is_long else (alt_price - cand["price"])
    cand = cand[(distance >= MIN_DYNAMIC_TARGET_PTS) & (distance <= MAX_DYNAMIC_TARGET_PTS)]
    if cand.empty:
        return None, None
    best_row = cand.loc[cand["formation_time"].idxmax()]
    return float(best_row["price"]), best_row


def dynamic_stop(m5_ledger, level_type, alt_price, is_long, touch_time_alt):
    """Protective extreme of live same-side P1 candles, plus one ES tick.

    The inclusive +/-10pt radius is measured from the actual fill to each
    level, not to its P1 candle's high/low. No shared-P1 filter is required.
    Returns (stop_price, ledger_row), or (None, None) for the fixed fallback.
    """
    cand = _live_m5_before_entry(m5_ledger, level_type, touch_time_alt)
    if cand.empty:
        return None, None
    cand = cand[(cand["price"] - alt_price).abs() <= DYNAMIC_STOP_RADIUS_PTS]
    if cand.empty:
        return None, None
    extreme = "breakout_low" if is_long else "breakout_high"
    if not np.isfinite(cand[extreme]).all():
        raise ValueError(f"Non-finite M5 {extreme} for a confirmed stop candidate")
    stops = cand[extreme] + (-R.TICK_SIZE_DEFAULT if is_long else R.TICK_SIZE_DEFAULT)
    cand = cand[(stops < alt_price) if is_long else (stops > alt_price)]
    if cand.empty:
        return None, None
    best_idx = cand[extreme].idxmin() if is_long else cand[extreme].idxmax()
    return float(stops.loc[best_idx]), cand.loc[best_idx]


# --------------------------------------------------------------------------
# Per-cluster processing (one cluster = one trade, see cluster_confluence)
# --------------------------------------------------------------------------

def process_cluster(cluster, args):
    anchor = cluster_anchor(cluster)
    row_d = anchor["row"]
    level_type = row_d["type"]
    is_long = level_type == "LHPB"

    conf = cluster_confluence(cluster)
    h1_retest_time = conf["h1_retest_time"]
    window_start = (pd.to_datetime(h1_retest_time, utc=True)
                    if h1_retest_time is not None else None)
    window_end = (window_start + pd.Timedelta(hours=args.max_alt_fill_hours)
                  if window_start is not None else None)
    alt_price, alt_source, group_n = conf["alt_price"], conf["alt_source"], conf["group_n"]
    own_price = float(row_d["entry_price"])
    # Every cluster member's own H1 price, extreme-first, for the H1 Entry
    # tooltip and Merged H1 levels column -- the anchor's own price is shown
    # in H1 Entry, but a merged cluster came from more than one original level.
    member_prices = sorted({float(c["row"]["entry_price"]) for c in cluster},
                           reverse=(level_type == "LLPB"))

    result = {
        "i": anchor["i"], "row": row_d, "level_type": level_type, "is_long": is_long,
        "group_n": group_n,
        "cluster_size": len(cluster), "cluster_members": member_prices,
        "own_price": own_price, "alt_price": alt_price, "alt_source": alt_source,
        "alt_formation_time": conf["alt_formation_time"], "alt_end_time": conf["alt_end_time"],
        "entry_m5_level": conf["entry_m5_level"],
        "h1_price": conf["h1_price"], "h1_source": conf["h1_source"],
        "h1_formation_time": conf["h1_formation_time"], "h1_end_time": conf["h1_end_time"],
        "fill_window_start": window_start, "fill_window_end": window_end,
        "improved": abs(alt_price - own_price) > 1e-9,
        "chart_confluent_h1": h1_chart_confluence(
            cluster, conf["h1_price"], conf["h1_formation_time"]),
        "filled": False,
    }

    if window_start is None:
        result["fail_reason"] = "refined_h1_not_retested"
        return result
    touch_time_alt, fill_price = find_alt_fill(
        window_start, alt_price, is_long, level_type, args.max_alt_fill_hours,
        pegged=args.pegged_entry, peg_step=args.peg_step, peg_cap=args.peg_cap)
    if touch_time_alt is None:
        result["fail_reason"] = "unfilled_within_window"
        return result
    result["fill_price"] = fill_price
    chase = fill_price - alt_price if is_long else alt_price - fill_price
    result["chased_pts"] = max(0.0, chase)
    result["price_improvement_pts"] = max(0.0, -chase)
    result["improved"] = fill_price < own_price if is_long else fill_price > own_price

    bars = build_minute_bars(touch_time_alt)
    if bars is None or bars.empty:
        result["fail_reason"] = "no_tick_data_after_fill"
        return result

    # Everything downstream (target search, bracket, PnL/R) is relative to
    # the price ACTUALLY paid (fill_price), not the originally-quoted
    # alt_price -- identical when pegging is off (fill_price == alt_price).
    m5_ledger = conf["m5_ledger"]
    fill_contract = R._contract_index_for(touch_time_alt)
    if fill_contract != R._contract_index_for(pd.to_datetime(row_d["retest_time"], utc=True)):
        m5_ledger = LC.m5_levels(fill_contract)
    target_price, target_row = dynamic_target(m5_ledger, level_type, fill_price, is_long,
                                              touch_time_alt)
    if target_price is None:
        target_pts = args.fallback_target
        target_price_disp = fill_price + target_pts if is_long else fill_price - target_pts
        target_source = "fallback_fixed"
    else:
        target_pts = abs(target_price - fill_price)
        target_price_disp = target_price
        target_source = "m5_opposite"
    stop_price, stop_row = dynamic_stop(m5_ledger, level_type, fill_price, is_long,
                                        touch_time_alt)
    if stop_price is None:
        stop_price = fill_price - args.stop if is_long else fill_price + args.stop
        stop_source = "fallback_fixed"
    else:
        stop_source = "m5_breakout"
    stop_pts = abs(stop_price - fill_price)

    trade = {"type": level_type, "entry": fill_price, "is_long": is_long,
             "retest_time": touch_time_alt.tz_convert("UTC").tz_localize(None),
             "stop_dist": stop_pts, "target_dist": target_pts}
    resolved = SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]
    # resolve_trades already computes favorable/adverse/giveback/entry_gapped
    # internally (via the same _compute_excursion/_compute_giveback this
    # report's charts rely on) -- read them straight off `resolved` rather
    # than re-deriving them a second time from the same bars.

    result.update({
        "filled": True, "touch_time_alt": touch_time_alt,
        "target_price": target_price_disp, "target_pts": target_pts, "target_source": target_source,
        "target_m5_level": target_row.to_dict() if target_row is not None else None,
        "stop_pts": stop_pts, "stop_price": stop_price, "stop_source": stop_source,
        "stop_m5_level": stop_row.to_dict() if stop_row is not None else None,
        "resolved": resolved,
        "favorable_pts": resolved.get("favorable_pts"), "adverse_pts": resolved.get("adverse_pts"),
        "giveback_pts": resolved.get("giveback_pts"),
        "entry_gapped": resolved.get("entry_gapped", False),
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
# and the same status filter panel + excursion-percentile summary --
# reusing SR.CSS/SR.JS/SR.build_trade_chart/SR.build_m5_chart/
# R.build_1s_trio_chart verbatim so this report never re-implements any of
# that rendering. Extra columns beyond the base layout are this strategy's
# own: Merged H1 levels (member prices),
# H1 Entry vs fine-tuned Entry (with a source tag h1/m5/own), Target
# Src (m5_opposite vs fallback_fixed), a Stop source badge, R (reward:risk on offer at entry)
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
td.merged-h1-levels { max-width:220px; white-space:normal; }
</style>
"""

# Chart rendering (H1/M5/1s-trio/bid/ask/1min panes, ray hover tooltips,
# toggleChart), and Reviewed/Valid/Replayed/Notes persistence + CSV
# export/import + the status filter panel are ALL reused verbatim
# from render_stop_target_report.JS -- it already keys off the same
# th1-/ch1-/tm5-/cm5-/tc-/cc-/tb-/cb-/ta-/ca-/t1m-/c1m- element ids and
# discovers available filter controls, so nothing here needs its own copy.
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


def _fill_window_description(res):
    if res["fill_window_start"] is None:
        return f"refined H1 {res['h1_price']:.2f} not retested; fill window not started"
    return (f"fill window from refined H1 {res['h1_price']:.2f} retest candle: "
            f"{R._to_pt_str(res['fill_window_start'])} to {R._to_pt_str(res['fill_window_end'])}")


def _annotate_fill_window(chart, res):
    if chart is None:
        return
    chart["title"] += f"  |  {_fill_window_description(res)}"
    if res["fill_window_start"] is not None:
        start = R._to_epoch_utc(res["fill_window_start"])
        if any(c["time"] == start for c in chart["candles"]):
            chart["markers"].append({
                "time": start, "position": "inBar", "color": R.P2_COLOR,
                "shape": "circle", "text": "Refined H1 retest / window start",
            })
            chart["markers"].sort(key=lambda m: m["time"])


def build_chart_stack_for_row(h1_df, pos_by_ts, res):
    """Full per-row chart stack -- same four panes + footprint tables as
    render_stop_target_report._build_records, built from the FINE-TUNED
    entry/stop/target/resolved outcome instead of the original H1 level's
    own fixed bracket. Returns (chart_stack, fp) exactly like that
    function's own per-row locals."""
    row_d = res["row"]
    alt_price = res["fill_price"]  # the price ACTUALLY paid (== alt_price unless pegged)
    resolved = res["resolved"]
    stop_pts, target_pts = res["stop_pts"], res["target_pts"]

    row_for_chart = row_d.copy()
    row_for_chart["price"] = alt_price
    chart_h1 = SR.build_trade_chart(h1_df, pos_by_ts, row_for_chart, None, resolved,
                                    stop_pts, target_pts, confluent=res["chart_confluent_h1"],
                                    level_price=res["h1_price"],
                                    ray_formation_time=res["h1_formation_time"],
                                    ray_end_time=res["h1_end_time"])
    chart_h1["title"] += (f"  |  entry fine-tuned via {res['alt_source']} "
                          f"({res['group_n']} in group)  |  target: {res['target_source']}"
                          f"  |  stop: {res['stop_source']}")
    if res.get("chased_pts", 0.0) > 1e-9:
        chart_h1["title"] += (f"  |  pegged fill: chased {res['chased_pts']:.2f}pt "
                              f"off {res['alt_price']:.2f} to {alt_price:.2f}")
    elif res.get("price_improvement_pts", 0.0) > 1e-9:
        chart_h1["title"] += (f"  |  quote price improvement: "
                              f"{res['alt_price']:.2f} to {alt_price:.2f}")
    chart_m5 = SR.build_m5_chart(
        row_for_chart, resolved, stop_pts, target_pts,
        level_price=res["h1_price"], entry_level=res["entry_m5_level"],
        fill_window=(res["fill_window_start"], res["fill_window_end"]))
    if chart_m5 is not None:
        chart_m5["title"] += (f"  |  target: {res['target_source']}"
                              f"  |  stop: {res['stop_source']}")
    _annotate_fill_window(chart_h1, res)
    _annotate_fill_window(chart_m5, res)
    execution_charts, fp = build_execution_charts(res)
    return {"h1": chart_h1, "m5": chart_m5, **execution_charts}, fp


def build_execution_charts(res):
    """Fill-centered panes and footprints, also reusable for chart-only refreshes."""
    row_d = res["row"]
    alt_price = res["fill_price"]
    is_long = res["is_long"]
    resolved = res["resolved"]
    stop_pts, target_pts = res["stop_pts"], res["target_pts"]

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
        if abs(res["alt_price"] - alt_price) > 1e-9:
            for pane in (trio_chart["trio"], trio_chart["oneMin"]):
                if pane is not None:
                    pane["priceLines"].append({
                        "price": res["alt_price"], "color": R.LEVEL_COLOR,
                        "lineWidth": 1, "lineStyle": 2,
                        "title": f"planned entry {res['alt_price']:.2f}",
                    })
        chart_stack = {"trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
        fp = {"narrow": trio_chart.get("footprintNarrowHtml"),
              "wide": trio_chart.get("footprintWideHtml")}
    else:
        chart_stack = {"trio": None, "oneMin": None}
        fp = {"narrow": "<p class='note'>(no tick data around the actual fill)</p>",
              "wide": "<p class='note'>(no tick data around the actual fill)</p>"}
    return chart_stack, fp


def build_unfilled_chart_stack(h1_df, pos_by_ts, res, args):
    """Chart stack for a row whose fine-tuned entry never filled -- there is
    no resolved trade (no touch_time/exit/outcome) to build the usual
    stack from, but a reviewer still wants to SEE the price action rather
    than just read "UNFILLED". Reuses the H1/M5 panes with a stub resolved
    dict (no fabricated touch or exit), the refined H1 window as M5 context,
    and the fallback stop/target as nominal reference
    lines (the real bracket was never computed since there was no fill to
    search dynamic exits from). The 1s trio/1min pane is
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
                                    args.stop, args.fallback_target, confluent=res["chart_confluent_h1"],
                                    level_price=res["h1_price"],
                                    ray_formation_time=res["h1_formation_time"],
                                    ray_end_time=res["h1_end_time"])
    for m in chart_h1["markers"]:
        if m.get("text") == "NO DATA":
            m["text"] = "UNFILLED"
    chart_h1["title"] += (f"  |  entry fine-tuned via {res['alt_source']} "
                          f"({res['group_n']} in group)  |  UNFILLED -- "
                          f"{res.get('fail_reason', '')}  (stop/target lines are nominal, "
                          f"never actually computed since there was no fill)")
    chart_m5 = SR.build_m5_chart(
        row_for_chart, resolved_stub, args.stop, args.fallback_target,
        level_price=res["h1_price"], entry_level=res["entry_m5_level"],
        fill_window=((res["fill_window_start"], res["fill_window_end"])
                     if res["fill_window_start"] is not None else None))
    _annotate_fill_window(chart_h1, res)
    _annotate_fill_window(chart_m5, res)

    fill_window = (build_fill_window_chart(
        res["fill_window_start"], alt_price, level_type,
        args.max_alt_fill_hours, res.get("fail_reason"))
        if res["fill_window_start"] is not None else None)
    chart_stack = {"h1": chart_h1, "m5": chart_m5, "trio": None, "oneMin": fill_window}
    note = "<p class='note'>(entry never filled -- no tick-level touch to build a footprint from)</p>"
    fp = {"narrow": note, "wide": note}
    return chart_stack, fp


N_COLS = 23  # keep in sync with `head` below and every colspan in this section


def render(args):
    if (not np.isfinite(args.stop) or args.stop <= 0 or
            not np.isfinite(args.fallback_target) or args.fallback_target <= 0):
        raise ValueError("Fallback stop and target distances must be finite and positive")
    if not np.isfinite(args.max_alt_fill_hours) or args.max_alt_fill_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")
    # args.limit is already the correctly-resolved tri-state by the time it
    # gets here (A._DEFAULT sentinel / concrete int / None-for-no-cap) -- see
    # __main__ below. Do NOT re-derive it from `args.limit is None` here: for
    # --full-year that value IS a real "no cap" None, and re-converting it
    # back to the sentinel would silently reinstate the default-300 cap.
    h1_confluence_points = getattr(args, "h1_confluence_points", H1_CONFLUENCE_N_POINTS)
    h1_df, pos_by_ts, strong, candidates = select_candidates(
        args.ss_confl_min, args.start, args.end, args.limit, merged=args.merged,
        h1_confluence_points=h1_confluence_points)
    h1_radius = SR._fmt_pts(h1_confluence_points)
    m5_radius = SR._fmt_pts(M5_CONFLUENCE_N_POINTS)
    print(f"{len(strong)} strong-breakout trades selected; "
          f"{len(candidates)} have SS Confl >= {args.ss_confl_min} "
          f"(H1 +/-{h1_radius}pt; M5 +/-{m5_radius}pt)", flush=True)
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

    win_mae_rows = [r for r in filled
                   if r["resolved"]["outcome"] == "target" and r.get("adverse_pts") is not None]
    loss_mfe_rows = [r for r in filled
                    if r["resolved"]["outcome"] == "stop" and r.get("favorable_pts") is not None]
    win_mae_values = [r["adverse_pts"] for r in win_mae_rows]
    loss_mfe_values = [r["favorable_pts"] for r in loss_mfe_rows]
    max_win_mae = max(win_mae_values) if win_mae_values else 0.0
    max_loss_mfe = max(loss_mfe_values) if loss_mfe_values else 0.0
    gapped_entries = sum(1 for r in filled if r.get("entry_gapped"))
    dynamic_stops = sum(1 for r in filled if r["stop_source"] == "m5_breakout")
    excursion_groups = [
        ("MFE &mdash; losing trades", "ran this far in favour before hitting stop",
         "favorable_pts", loss_mfe_rows),
        ("MAE &mdash; winning trades", "heat taken before reaching target",
         "adverse_pts", win_mae_rows),
        ("Max DD &mdash; all trades", "handed back from the best price the open position reached",
         "giveback_pts", [r for r in filled if r.get("giveback_pts") is not None]),
        ("Max DD &mdash; winning trades", "handed back before the winner reached target",
         "giveback_pts", [r for r in filled
          if r["resolved"]["outcome"] == "target" and r.get("giveback_pts") is not None]),
    ]
    pctile_html = SR.excursion_percentile_html(
        [(label, note, [r[field] for r in population])
         for label, note, field, population in excursion_groups],
        stop=None, group_stops=[[r["stop_pts"] for r in population]
                                for _, _, _, population in excursion_groups])

    charts = []
    rows_html = []
    for idx, res in enumerate(results):
        row_d = res["row"]
        level_type = res["level_type"]
        type_cls = "type-lhpb" if res["is_long"] else "type-llpb"
        retest_str = (R._to_pt_str(res["fill_window_start"])
                      if res["fill_window_start"] is not None else "-")
        retest_title = (f"{_fill_window_description(res)}; original H1 retest: "
                        f"{R._to_pt_str(row_d['retest_time'])}")
        members_str = ", ".join(f"{p:.2f}" for p in res["cluster_members"])
        if res.get("cluster_size", 1) > 1:
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
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left" title="{retest_title}">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
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
        target_level = res["target_m5_level"]
        target_title = (
            f"Newest eligible M5 {target_level['type']} P0: "
            f"{R._to_pt_str(target_level['formation_time'])}; shared P1: "
            f"{R._to_pt_str(target_level['breakout_time'])}"
            if target_level is not None else
            f"No qualifying live shared-P1 M5 target; fixed {res['target_pts']:.2f}pt fallback")
        stop_src_cls = "src-tag m5" if res["stop_source"] == "m5_breakout" else "src-tag"
        stop_level = res["stop_m5_level"]
        extreme = "breakout_low" if res["is_long"] else "breakout_high"
        stop_title = (
            f"Live M5 {stop_level['type']} {stop_level['price']:.2f}, "
            f"P0 {R._to_pt_str(stop_level['formation_time'])}; "
            f"P1 {R._to_pt_str(stop_level['breakout_time'])}, "
            f"{extreme} {stop_level[extreme]:.2f}; one tick "
            f"{'below' if res['is_long'] else 'above'}"
            if stop_level is not None else
            f"No qualifying protective M5 breakout candle; fixed {res['stop_pts']:.2f}pt fallback")
        chase_pts = res.get("chased_pts", 0.0)
        chase_flag = (f'<span class="src-tag chase" title="Pegged/chasing limit: original '
                      f'quote {res["alt_price"]:.2f} did not fill passively; '
                      f'the repriced order filled {chase_pts:.2f}pt closer to market at '
                      f'{res["fill_price"]:.2f}.">chased {chase_pts:.2f}pt '
                      f'&rarr; {res["fill_price"]:.2f}</span>'
                      if chase_pts > 1e-9 else "")
        if res.get("price_improvement_pts", 0.0) > 1e-9:
            chase_flag = (f'<span class="src-tag chase" title="Repriced limit received '
                          f'a better opposing quote on arrival.">filled '
                          f'{res["fill_price"]:.2f} '
                          f'({res["price_improvement_pts"]:.2f}pt better)</span>')
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
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left" title="{retest_title}">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{h1_entry_cell}</td>
  <td>{res['alt_price']:.2f}{gap_flag}<span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td title="{stop_title}">{res['stop_price']:.2f}<span class="{stop_src_cls}">{res['stop_source']}</span></td>
  <td>{res['target_price']:.2f}</td>
  <td><span class="{target_src_cls}" title="{target_title}">{res['target_source']}</span></td>
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
        f"fine-tuned price). A replacement takes effect on the next tick record and fills "
        f"against the opposing bid/ask if marketable; otherwise it rests at its new limit. "
        f"Queue position and additional cancel/replace latency are not modeled."
        if args.pegged_entry else
        "Entry is a plain static limit order (no chasing).")
    summary_html = f"""
<div class="summary">
  <div class="box"><strong>{len(strong)}</strong>strong-breakout trades</div>
  <div class="box"><strong>{len(candidates)}</strong>SS Confl &ge; {args.ss_confl_min}</div>
  <div class="box"><strong>{len(clusters)}</strong>confluence clusters</div>
  <div class="box"><strong>&plusmn;{h1_radius}pt</strong>H1 confluence radius</div>
  <div class="box"><strong>&plusmn;{m5_radius}pt</strong>M5 entry radius</div>
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
  <div class="box"><strong>{dynamic_stops}</strong>M5 breakout-candle stops</div>
  <div class="box"><strong>{len(filled) - dynamic_stops}</strong>fixed {SR._fmt_pts(args.stop)}pt fallback stops</div>
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
LHPB/long) among the subject's own H1 level and its same-side H1+M5 confluent levels still
unconsumed immediately before the cluster's ORIGINAL first H1 retest (H1 search radius
&plusmn;{h1_radius}pt; M5 search radius &plusmn;{m5_radius}pt; M5 levels must have formed
by the H1 breakout bar). The H1 radius controls SS qualification, clustering and
entry selection. Clusters are transitive: the radius limits each link, not the
total cluster span, and linked levels can have different breakout/retest bars. (Own
Entry vs Entry columns; the src tag shows which member supplied the extreme: own/h1/m5),
verified filled on real ticks from the REFINED H1 level's own retest candle START
(not its exact tick touch). The window is [start, start + {args.max_alt_fill_hours:g}h);
it does not begin at the original cluster anchor's retest. The Refined H1 retest column
shows this start; hover for the original H1 retest and window end.
If that H1 level has not yet been retested/consumed, the window has not started.
An entry never reached in its window is UNFILLED and excluded from every stat here,
not counted as a loss.
{peg_lead_sentence}
Target = the MOST RECENTLY FORMED (P0) live opposite-type M5 level on the favourable side,
not the closest price or the newest breakout. Its P1 candle must have broken at least
two distinct same-type P0 levels (historical peers count even if since consumed).
Candidates must be {MIN_DYNAMIC_TARGET_PTS:g}&ndash;{MAX_DYNAMIC_TARGET_PTS:g} points from
the actual fill, inclusive; otherwise use the fixed {args.fallback_target:g}pt fallback
(Target Src column). Stop = one tick above the HIGHEST breakout-candle high for LLPB shorts,
or one tick below the LOWEST breakout-candle low for LHPB longs, among live same-side M5
levels within &plusmn;{DYNAMIC_STOP_RADIUS_PTS:g} points of the actual fill. The radius
limits level prices, not the stop distance. No qualifying protective candle means a
fixed {args.stop:g}pt fallback (Stop badge). Both searches use only completed M5 candles
and the live ledger state immediately before the fill's M5 bar, with no fixed lookback.
Hover the source badges for the selected P0/P1 details. R uses each trade's own stop distance. Baseline
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

    head = (f"<th class=\"left\">#</th><th class=\"left\">Type</th>"
            f"<th class=\"left\">Refined H1 retest</th>"
            f"<th class=\"left\" title=\"Distinct H1 prices merged into this trade, "
            f"extreme-first: highest for LLPB, lowest for LHPB. "
            f"Single-level trades show their own H1 price.\">Merged H1 levels</th>"
            f"<th title=\"The level's original H1 entry price, before fine-tuning to the "
            f"confluence group's extreme price\">H1 Entry</th><th>Entry</th>"
            f"<th class=\"left\">Entry (touch) time</th>"
            f"<th title=\"Live same-side M5 breakout-candle extreme plus one tick; "
            f"+/-{DYNAMIC_STOP_RADIUS_PTS:g}pt level search, {args.stop:g}pt fallback\">Stop</th>"
            f"<th title=\"Newest eligible opposite M5 P0 sharing P1 with another P0\">Target</th>"
            f"<th>Target Src</th>"
            f"<th title=\"Reward:risk on offer for THIS trade's own bracket at entry "
            f"(target pts / stop pts) -- fixed once entry/stop/target are picked, independent "
            f"of whether the trade goes on to win or lose\">R</th>"
            f"<th>Outcome</th><th class=\"left\">Exit time</th><th>Exit px</th>"
            f"<th title=\"Realized profit/loss in points (signed): +target pts on a win, "
            f"-stop pts on a loss\">PnL</th>"
            f"<th>MAE (win)</th><th>MFE (loss)</th><th>Max DD</th>"
            f"<th>Reviewed</th><th>Valid</th><th>Replayed</th>"
            f"<th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")

    title_suffix = getattr(args, "title_suffix", None) or ""
    storage_key = (f"lxpb_ss_confl{args.ss_confl_min}_finetune_review_v1"
                   + ("_fy2026" if getattr(args, "full_year", False) else ""))
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>SS Confl fine-tune report{title_suffix}</title>
{CSS}
</head><body>
<h1>SS Confl. &ge; {args.ss_confl_min} fine-tuned entry/exit report{title_suffix}</h1>
{summary_html}
{filter_panel}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{head}</tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table></div>
{JS.replace("__CHARTS_JSON__", json.dumps(charts))
   .replace("__STORAGE_KEY__", storage_key)}
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
                     "target (newest opposite M5 P0 with shared P1) / "
                     "stop (same-side M5 breakout-candle extreme) strategy report")
    parser.add_argument("--ss-confl-min", type=int, default=SS_CONFL_MIN_DEFAULT)
    parser.add_argument("--h1-confluence-points", type=float, default=H1_CONFLUENCE_N_POINTS,
                        help="H1 price radius for SS qualification, clustering and entry "
                             f"selection (default {H1_CONFLUENCE_N_POINTS}pt). "
                             f"M5 entry search stays at {M5_CONFLUENCE_N_POINTS}pt.")
    parser.add_argument("--fallback-stop", "--stop", dest="stop", type=float, default=DEFAULT_STOP,
                        help="Fixed stop distance only when no protective live M5 "
                             f"breakout-candle stop qualifies (default {DEFAULT_STOP}pt).")
    parser.add_argument("--fallback-target", type=float, default=DEFAULT_FALLBACK_TARGET)
    parser.add_argument("--baseline-stop", type=float, default=DEFAULT_BASELINE_STOP)
    parser.add_argument("--baseline-target", type=float, default=DEFAULT_BASELINE_TARGET)
    parser.add_argument("--max-alt-fill-hours", type=float, default=MAX_ALT_FILL_HOURS_DEFAULT,
                        help="Fill-window duration from the refined H1 level's own retest "
                             f"candle start, not its exact tick touch (default {MAX_ALT_FILL_HOURS_DEFAULT:g}h).")
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
    parser.add_argument("--merged", action="store_true",
                        help="merge every TradingView H1 export (newest wins) instead of "
                             "using only the single default one -- needed for spans that "
                             "run past the default export's last bar")
    parser.add_argument("--full-year", action="store_true",
                        help="shorthand for --start 2026-01-01 --end 2026-12-31 --limit none --merged")
    parser.add_argument("--max-rows", type=int, default=None,
                        help="process only the first N SS-Confl-qualifying rows (smoke test)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    limit = A._DEFAULT  # sentinel: "not specified" -> load_strong_breakout_rows' own default (300)
    if args.limit is not None:
        limit = None if str(args.limit).lower() in ("none", "0", "all") else int(args.limit)
    args.title_suffix = None
    if args.full_year:
        args.start = args.start or "2026-01-01"
        args.end = args.end or "2026-12-31"
        limit = None  # explicit no-cap, NOT the sentinel -- see render()'s comment
        args.merged = True
        args.title_suffix = " (2026 full year)"
    args.limit = limit
    default_name = f"ss_confl{args.ss_confl_min}_finetune_report.html"
    if args.full_year:
        default_name = f"ss_confl{args.ss_confl_min}_finetune_report_2026_full_year.html"
    args.output = args.output or os.path.join(_HERE, "public", "reports", default_name)
    render(args)
