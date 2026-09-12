"""
render_m5_confl2_report.py
===========================
New strategy report: an M5-NATIVE analogue of render_ss_confl_finetune_report.py's
"SS Confl >= N" idea. Where that report starts from H1 LXPB retests (and only
uses M5 structure to fine-tune the entry/exit of an H1-anchored trade), this
one drops H1 entirely -- the trade signal, the entry refinement, the stop and
the target are ALL M5 LXPB structure:

  1. SELECT. Every M5 LXPB retest (lxpb_levels_cache.retests(m5_ledger), the
     M5-timeframe equivalent of the H1 "strong breakout" sample) within
     [--start, --end), for every contract segment with tick data on disk.
     Keep only retests whose SAME-SIDE M5 confluence count (other M5 levels
     of the SAME type, within +/-`--m5-confluence-points` (default 5.0pt),
     confirmed-broken-out and not yet retested as of the subject's own P1
     breakout bar -- lxpb_levels_cache.same_side_live_confluence, same
     definition the H1 report uses, just run on the M5 ledger instead of the
     H1 one) is >= `--ss-confl-min` (default 2, hence "confl2").

  2. DE-DUPLICATE MUTUALLY-CONFLUENT LEVELS INTO ONE TRADE. Exactly
     render_ss_confl_finetune_report.cluster_candidates's own logic (union-
     find over "my own level appears in your same-side confluence set, or
     vice versa"), run on the M5-only candidate pool.

  3. FINE-TUNE THE ENTRY, AT THE BREAKOUT BAR. The entry is the MOST EXTREME
     price among the cluster's own member prices and every member's own
     same-side M5 confluence pool: the HIGHEST for an LLPB (short) and the
     LOWEST for an LHPB (long) -- a resting order further from price is
     strictly better if it still fills. Support levels must still be alive
     immediately before the cluster's own retest (unconsumed), same
     liveness rule as the H1 report. The chosen price is then verified with
     a real forward-only tick scan from the cluster's own retest bar
     (render_ss_confl_finetune_report.find_alt_fill, pegged by default --
     see that module for the fill-realism rules); an entry never reached
     within `--max-alt-fill-hours` is UNFILLED and excluded from every stat,
     not counted as a loss.

  4. STOP -- SPIKE-P0 OVERRIDE, ELSE THE THRUST (BREAKOUT) CANDLE. If the
     level that supplied the entry (see step 3) was ITSELF formed on a spike
     candle -- lxpb.py's own `is_spike`, already cached in the M5 ledger: a
     hammer for an LHPB, a shooting star for an LLPB -- the stop is one tick
     beyond THAT P0 candle's own low (long) / high (short) (`_dynamic_stop_m5`,
     src tag m5_p0_spike): the wick that DEFINED the level is a sharper
     invalidation point than the breakout candle, and price re-entering past
     it means the rejection itself failed. No fallback to the thrust-candle
     rule for these trades -- if the P0 bar isn't in the cached M5 series or
     the resulting stop lands on the wrong side of the fill, there is no
     trade (no_m5_stop), same as any other missing-stop case.
     Otherwise (src tag m5_thrust): live same-side M5 levels within +/-10pt
     of the actual fill; the stop is one tick beyond the most protective
     breakout-candle extreme among them (highest high for a short, lowest
     low for a long) -- render_ss_confl_finetune_report.dynamic_stop,
     unchanged.

  5. TARGET = TWO RULES, BOTH READING THE LEVEL'S OWN P1..P2 WINDOW
     (`_pick_targets`). Both ask the same question -- what did the market
     build while this level was waiting to be retested -- and both take
     only structure lying wholly after the level's breakout candle and
     wholly before its retest bar, 1..20 points away on the favourable side:

       * CONSOLIDATION AREA (m5_structure.py, src tags consol_p0 /
         consol_edge): the nearest congestion area in that window -- where
         the market last spent real time on its way away from this level.
         If the area still holds an UNTESTED opposite-type M5 P0 -- one
         whose own price sits inside the area's band and that has never
         been retested -- that P0's own price is the target; otherwise the
         target is the area's NEAR edge, its LOW for a long and its HIGH
         for a short.
       * OPPOSITE M5 LEVEL (`_opposite_m5_target`, src tag m5_opposite):
         the most recently formed live opposite-type M5 level whose own P1
         broke >=2 same-type P0s -- the original rule
         (render_ss_confl_finetune_report.dynamic_target), plus the P1..P2
         window.

     BOTH rules run for every trade, whatever --target-mode says, and each
     is a LIVE CHECKBOX in the report: unticking one re-resolves every row
     against the other (or demotes it to a dimmed NO TARGET row) with no
     regen, because each rule's whole outcome is precomputed per row (see
     _mode_payload). With both on -- the default -- a trade takes whichever
     rule offers the FARTHER target. --target-mode only sets which boxes
     start ticked.

  6. NO FALLBACKS, BUT SUB-MIN-R TRADES ARE MEASURED, NOT DISCARDED. Unlike
     the H1 report (which falls back to a fixed stop/target when no
     qualifying M5 structure exists), THIS strategy has no fixed bracket at
     all: if step 4 or step 5 finds nothing, there is no trade. A bracket
     whose reward:risk (target points / stop points, fixed at entry) comes
     out below `--min-r` (default 1.0) IS still taken, resolved and charted
     -- tagged `r_below_min`, and dropped from the headline stats by the
     report's own dynamic filter, which ships checked. The target is only
     knowable just before entry, so what those setups actually did is worth
     being able to look at.

  7. TRADE BLOCKS AND ENTRY ADJUSTMENTS. Three further rules, each of which
     TAGS its trades for the report's live dynamic filters rather than
     silently deleting them (see _apply_p1_reaction_filter's docstring for
     that convention):

       * SWERVED (`_swerve_entry`). A confirmed M5 swing low (long) or
         swing high (short) within `--swerve-tol-pts` of the planned entry,
         formed while the level waited between its own P1 and P2, means the
         order is resting on a price the market already turned away from
         once and that everyone else can see as well; it is moved past the
         swing to the nearest other live same-side M5 level within
         `--swerve-max-move-pts` (tag `swerved`). With no such level
         available the trade is NOT taken (tag `swerve_blocked`, excluded by
         default) but is still resolved and charted at its original entry.

       * NEWS / THIN BOOK (liquidity.py). The quoted spread measured off
         the real tape in the minutes before the fill decides whether the
         book had thinned out past anything normal trade produces -- which
         catches scheduled releases (FOMC/NFP/CPI/PPI) and unscheduled
         headline shocks alike, and re-enables trading by itself once the
         spread comes back in. Tag `low_liquidity`, excluded by default.

       * END OF DAY (trade_management.py rule 3). No position is carried
         overnight: an open trade is flattened at market before 12:45 PT
         (outcome `eod_flat`), and a fill that would have landed at or after
         12:45 PT is no trade at all (`eod_entry_blocked`). This one is a
         hard rule, not a filter -- it is in the baseline and the managed
         numbers both.

Every filled, in-R trade is resolved with the exact same tick-accurate
machinery the rest of this repo depends on
(render_stop_target_report.resolve_trades / _compute_excursion, which pin
every exit to the exact second via
analyze_breakout_exits_1min._pin_exact_exit's `not_before`-guarded scan) --
nothing here re-implements stop/target/MAE/MFE resolution from scratch.

There is no H1 chart pane (there is no H1 level in this strategy) -- each row
shows the M5 chart, the 1s tick trio + bid/ask volume, the 1-minute pane, and
tick-level footprint tables, exactly as render_stop_target_report.py builds
them, via render_ss_confl_finetune_report.build_execution_charts (entirely
generic -- it never assumed an H1-anchored row) and
render_stop_target_report.build_m5_chart's new `p1_bar_width`/`p1_label`
params (default H1/1h; passed here as M5/5min so the chart's own "formed by
P1" cutoff matches this strategy's own P1, a 5-minute bar, instead of
silently reusing an hour-wide H1 fudge factor).

Usage:
    python render_m5_confl2_report.py
    python render_m5_confl2_report.py --ss-confl-min 2 --min-r 1.0
    python render_m5_confl2_report.py --start 2026-01-01 --end 2026-12-31 --output ss_m5_confl2_report_2026_full_year.html
    python render_m5_confl2_report.py --max-rows 5   # quick smoke test
"""
import os
import sys
import json
import time
import pickle
import bisect
import argparse
import multiprocessing as mp
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))   # this strategy's own folder (also the default output dir)
_REPO_ROOT = os.path.dirname(_HERE)                  # lxpb-v2/, where the shared report modules live
sys.path.insert(0, _REPO_ROOT)
import render_labels_report as R                 # noqa: E402
import render_stop_target_report as SR           # noqa: E402
import render_ss_confl_finetune_report as SF      # noqa: E402
import analyze_breakout_exits_1min as M           # noqa: E402
import lxpb_levels_cache as LC                    # noqa: E402
import trade_management as TM                     # noqa: E402
import m5_structure as MS                         # noqa: E402
import liquidity as LQ                            # noqa: E402

SS_CONFL_MIN_DEFAULT = 2
M5_CONFLUENCE_N_POINTS_DEFAULT = 5.0  # same-side M5 confluence radius: selection + entry refinement
MIN_DYNAMIC_TARGET_PTS = SF.MIN_DYNAMIC_TARGET_PTS
MAX_DYNAMIC_TARGET_PTS = SF.MAX_DYNAMIC_TARGET_PTS
DYNAMIC_STOP_RADIUS_PTS = SF.DYNAMIC_STOP_RADIUS_PTS
MAX_ALT_FILL_HOURS_DEFAULT = SF.MAX_ALT_FILL_HOURS_DEFAULT
MIN_R_DEFAULT = 1.0
TARGET_MODE_DEFAULT = "both"   # both target rules on by default (see _pick_targets)
CONSOL_MIN_BARS_DEFAULT = MS.MIN_BARS_DEFAULT
CONSOL_MAX_HEIGHT_DEFAULT = MS.MAX_HEIGHT_PTS_DEFAULT
CONSOL_MAX_ER_DEFAULT = MS.MAX_ER_DEFAULT
SWERVE_TOL_PTS_DEFAULT = 1.0          # a swing this close to the planned entry triggers the move
SWERVE_LOOKBACK_HOURS_DEFAULT = 24.0  # how far back before the retest swings are looked for
# Furthest a swerved entry may be moved. This MUST exceed
# --m5-confluence-points to be able to do anything at all: the planned entry
# is already the most extreme price in a pool built from every live same-side
# level within that radius, so the nearest same-side level beyond it is
# always at least one radius away. Measured over Jul-Aug 2026 the nearest one
# is a median 30pt away, so most swerves block rather than move -- which is
# the rule working, not failing.
SWERVE_MAX_MOVE_PTS_DEFAULT = 10.0
SWERVE_SWING_K_DEFAULT = MS.SWING_K_DEFAULT
LIQ_WINDOW_MINUTES_DEFAULT = LQ.WINDOW_MINUTES_DEFAULT
LIQ_WIDE_SPREAD_SHARE_DEFAULT = LQ.WIDE_SPREAD_SHARE_MAX_DEFAULT
PEG_STEP_DEFAULT = SF.PEG_STEP_DEFAULT
PEG_CAP_DEFAULT = SF.PEG_CAP_DEFAULT
P1_BAR_WIDTH = pd.Timedelta(minutes=5)  # this strategy's own P1 is an M5 bar, not H1
CANDIDATE_COLOR = "#7dd3fc"  # light blue -- every C1..Cn marker (dot + label), chosen or not;
                             # suppressed ones are distinguished by their " ✕" text suffix, not color

# Matches analyze_breakout_exits.DEFAULT_START/END -- the same Jul-Aug 2026
# span the base (non-full-year) ss_confl2 H1 report uses.
DEFAULT_START = "2026-07-01"
DEFAULT_END = "2026-08-31"

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# --------------------------------------------------------------------------
# Selection: every M5 retest with same-side M5 confluence >= threshold
# --------------------------------------------------------------------------

_DEPARTED_FATES = (LC.FATE_RETESTED, LC.FATE_CONSUMED_EARLY)


def _seg_departed_levels(m5_ledger, start_ts, end_ts):
    """Every M5 level that left its 'awaiting retest' watch window in
    [start_ts, end_ts): fate 'retested' (a clean retest, entry_price/
    stop_loss populated) OR 'consumed_early' (price touched/gapped past the
    level too soon after ITS OWN P1 to count as a clean retest, per
    lxpb_levels_cache.retests's own MIN_BARS_BEFORE_RETEST gate -- see
    lxpb_levels_cache.py's own fate table). A consumed_early level was
    never a tradeable retest in this strategy's own selection logic, but
    price DID reach its price and move on, which is exactly the kind of
    reaction the p1_reacted dynamic filter needs to detect: a level dying this way
    right after its own breakout is if anything a SHARPER rejection than a
    clean retest hours later. Adds a single unified 'touch_time' column
    (retest_time for a clean retest, death_time for consumed_early, since
    consumed_early rows never populate retest_time/retest_open/etc)."""
    d = m5_ledger[m5_ledger["fate"].isin(_DEPARTED_FATES)].copy()
    d["touch_time"] = d["retest_time"].where(d["fate"] == LC.FATE_RETESTED, d["death_time"])
    return d[(d["touch_time"] >= start_ts) & (d["touch_time"] < end_ts)]


def select_candidates(ss_confl_min, start, end, confluence_points):
    """M5-native candidate rows over the one continuous M5 ledger (see the
    "continuous contracts only" convention in CLAUDE.md -- LC.m5_levels()
    now spans every contract rollover in one state-machine run, so a P0
    formed on one contract can be retested by a later contract's bars).
    Returns (candidates, departed): candidates is a list of dicts
    (candidate index `i`, the row itself, its own same-side M5 confluence
    set, the shared ledger -- kept per-candidate since dynamic_target/
    dynamic_stop need the full ledger, not just the confluence subset --
    and `seg_idx`, which RAW contract's own ticks cover this candidate's
    own instant, still needed for chart/tick work even though level
    detection itself no longer cares); departed is every 'departed' M5
    level (see _seg_departed_levels -- clean retests AND consumed_early)
    in [start, end), BEFORE the ss_confl_min filter -- kept separately so
    the p1_reacted dynamic filter (see _p1_group_reaction_cutoffs) can see
    a P1 group's full membership, including P0s that don't themselves
    clear ss_confl_min or were never a tradeable retest at all."""
    if not np.isfinite(confluence_points) or confluence_points < 0:
        raise ValueError("M5 confluence radius must be finite and non-negative")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)  # end date inclusive
    candidates = []
    m5_ledger = LC.m5_levels(verbose=False)
    if m5_ledger is None or m5_ledger.empty:
        return candidates, pd.DataFrame()
    retests = LC.retests(m5_ledger)
    retests = retests[(retests["retest_time"] >= start_ts) & (retests["retest_time"] < end_ts)]
    departed = _seg_departed_levels(m5_ledger, start_ts, end_ts)
    for _, row_d in retests.iterrows():
        same_side_m5 = SF._same_side_confluence(m5_ledger, row_d, confluence_points)
        if len(same_side_m5) < ss_confl_min:
            continue
        seg_idx = R._contract_index_for(pd.Timestamp(row_d["retest_time"]))
        sym = R.B26.CONTRACTS[seg_idx][0]
        candidates.append({
            "row": row_d, "same_side_m5": same_side_m5,
            "m5_ledger": m5_ledger, "seg_idx": seg_idx, "sym": sym,
        })
    candidates.sort(key=lambda c: pd.Timestamp(c["row"]["retest_time"]))
    for i, cand in enumerate(candidates):
        cand["i"] = i
    return candidates, departed


# --------------------------------------------------------------------------
# Confluence clustering -- collapse duplicate trades (same idea as
# render_ss_confl_finetune_report.cluster_candidates, keyed on same_side_m5
# instead of same_side_h1; both are otherwise identical union-find logic)
# --------------------------------------------------------------------------

def cluster_candidates(candidates):
    n = len(candidates)
    key_to_idx = {}
    for idx, cand in enumerate(candidates):
        row_d = cand["row"]
        key_to_idx[SF._level_key(row_d["type"], row_d["price"], row_d["formation_time"])] = idx

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
        for _, r in cand["same_side_m5"].iterrows():
            other = key_to_idx.get(SF._level_key(r["type"], r["price"], r["formation_time"]))
            if other is not None:
                union(idx, other)

    groups = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)
    ordered = sorted(groups.values(), key=min)
    return [[candidates[m] for m in members] for members in ordered]


def cluster_confluence(cluster):
    """M5-only analogue of render_ss_confl_finetune_report.cluster_confluence:
    union every member's own same-side M5 confluence pool (each still
    centered on that member's own price) with the cluster's own member
    prices, then pick the fine-tuned entry as the extreme of the whole pool.

    Returns a dict: alt_price/alt_source ("own"/"m5")/group_n, alt_formation_time/
    alt_end_time (the specific level that supplied alt_price), and
    entry_m5_level (the ledger row that supplied alt_price, or None when the
    cluster's own un-refined price is already the extreme)."""
    anchor_row = SF.cluster_anchor(cluster)["row"]
    level_type = anchor_row["type"]
    cluster_retest_time = anchor_row["retest_time"]

    own_keys = set()
    own_prices, own_starts, own_ends = [], [], []
    for cand in cluster:
        row_d = cand["row"]
        own_keys.add(SF._level_key(row_d["type"], row_d["price"], row_d["formation_time"]))
        if (row_d["formation_time"] >= cluster_retest_time or
                row_d["breakout_time"] > cluster_retest_time):
            continue
        own_prices.append(float(row_d["price"]))
        own_starts.append(row_d["formation_time"])
        own_ends.append(row_d["retest_time"])

    seen_same_m5 = {}
    for cand in cluster:
        for _, r in cand["same_side_m5"].iterrows():
            k = SF._level_key(r["type"], r["price"], r["formation_time"])
            if k not in own_keys:
                seen_same_m5.setdefault(k, r)

    # P1-live supports are useful for the SS filter, but a resting entry
    # cannot be based on a level consumed before this trade's own P2/retest.
    live_m5 = SF._live_before_retest(pd.DataFrame(list(seen_same_m5.values())), anchor_row)
    cutoff = pd.to_datetime(cluster_retest_time, utc=True)
    same_m5_list = [r for _, r in live_m5.iterrows()
                    if pd.notna(r["breakout_time"]) and r["breakout_time"] < cutoff]

    def _ext_end(r):
        death = r["death_time"]
        if pd.isna(death):
            return cluster_retest_time
        return min(pd.Timestamp(death), pd.Timestamp(cluster_retest_time))

    prices = own_prices + [float(r["price"]) for r in same_m5_list]
    sources = ["own"] * len(own_prices) + ["m5"] * len(same_m5_list)
    starts = own_starts + [pd.Timestamp(r["formation_time"]) for r in same_m5_list]
    ends = own_ends + [_ext_end(r) for r in same_m5_list]
    idx = int(np.argmax(prices)) if level_type == "LLPB" else int(np.argmin(prices))

    return {
        "alt_price": prices[idx], "alt_source": sources[idx], "group_n": len(prices),
        "alt_formation_time": starts[idx], "alt_end_time": ends[idx],
        "entry_m5_level": (same_m5_list[idx - len(own_prices)].to_dict()
                           if sources[idx] == "m5" else None),
    }


# --------------------------------------------------------------------------
# Stop selection -- spike-P0 override
#
# render_ss_confl_finetune_report.dynamic_stop (unchanged, still used for
# every other trade) always stops beyond the P1 THRUST/breakout candle. But
# when the level that actually supplied the entry was itself formed on a
# spike candle -- lxpb.py's own is_spike: a hammer for an LHPB, a shooting
# star for an LLPB, already computed and cached in the M5 ledger -- that
# P0 candle's own wick is a sharper invalidation point than the breakout
# candle: price re-entering past the wick that DEFINED the level means the
# rejection the level was built on has itself failed, which can happen
# well inside the thrust candle's own (often much wider) range. For those
# trades ONLY, this overrides the stop to one tick beyond the P0 spike
# candle's own low (LHPB/long) or high (LLPB/short) instead.
# --------------------------------------------------------------------------

def _entry_level_row(cluster, conf):
    """Ledger info for the SPECIFIC level that supplied the fine-tuned
    entry price (conf['alt_price']) -- an m5-confluence level
    (conf['entry_m5_level'], already a ledger-row dict) when alt_source is
    'm5', or the matching cluster member's own row when alt_source is
    'own'. This is the level whose OWN P0 candle is checked for
    is_spike -- not the cluster's arbitrary anchor row, which may be a
    different member entirely."""
    if conf["alt_source"] == "m5":
        return conf["entry_m5_level"]
    for cand in cluster:
        row_d = cand["row"]
        if abs(float(row_d["price"]) - conf["alt_price"]) < 1e-9:
            return row_d.to_dict()
    return None


def _dynamic_stop_m5(level_type, alt_price, is_long, entry_level_info,
                     m5_ledger, touch_time_alt):
    """(stop_price, stop_row, stop_source) for one trade.

    entry_level_info['is_spike'] -> stop = one tick beyond THAT level's own
    P0 candle's low/high (read from the continuous M5 series,
    LC.m5_bars_continuous -- same source build_m5_chart's own M5 rays
    use). No fallback to the thrust-candle rule if this can't be computed
    (P0 bar missing from the cached series, or the resulting stop lands on
    the wrong side of the fill) -- that trade is a real "no trade"
    (no_m5_stop), not a silent revert to the old behaviour, since a silent
    fallback would defeat the point of making this an override.

    Otherwise: _dynamic_stop_m5_thrust (protective extreme of the P1 thrust
    candle among live same-side levels within +/-10pt of the fill) --
    SF.dynamic_stop's own rule, re-implemented locally so the candidate
    pool can include gated-dropped candles too (see that function's own
    docstring for why; same reasoning as the target rules).

    stop_row is always a pd.Series (like SF.dynamic_stop's own return) so
    callers can .to_dict() either path uniformly."""
    if entry_level_info is not None and entry_level_info.get("is_spike"):
        all_bars = LC.m5_bars_continuous()
        formation_time = pd.Timestamp(entry_level_info["formation_time"])
        if formation_time.tzinfo is None:
            formation_time = formation_time.tz_localize("UTC")
        if all_bars is not None and formation_time in all_bars.index:
            bar = all_bars.loc[formation_time]
            extreme = float(bar["low"]) if is_long else float(bar["high"])
            stop_price = (extreme - R.TICK_SIZE_DEFAULT if is_long
                         else extreme + R.TICK_SIZE_DEFAULT)
            beyond_fill = (stop_price < alt_price) if is_long else (stop_price > alt_price)
            if beyond_fill:
                stop_row = pd.Series({
                    "type": level_type, "price": float(entry_level_info["price"]),
                    "formation_time": formation_time,
                    "p0_low": float(bar["low"]), "p0_high": float(bar["high"]),
                })
                return stop_price, stop_row, "m5_p0_spike"
        return None, None, None
    stop_price, stop_row = _dynamic_stop_m5_thrust(m5_ledger, level_type, alt_price,
                                                   is_long, touch_time_alt)
    if stop_price is None:
        return None, None, None
    return stop_price, stop_row, "m5_thrust"


def _dynamic_stop_m5_thrust(m5_ledger, level_type, alt_price, is_long, touch_time_alt):
    """Same rule as SF.dynamic_stop (protective extreme of the P1 thrust
    candle among live same-side levels within +/-DYNAMIC_STOP_RADIUS_PTS of
    the fill, plus one tick) -- re-implemented here, rather than calling
    SF.dynamic_stop (left unchanged there for the H1 report), for one
    reason: the candidate pool comes from _live_m5_target_candidates
    instead of SF._live_m5_before_entry, so a thrust candle that failed the
    entry candidate gate still qualifies to protect a stop. A stop is a
    price the market broke through and hasn't come back to since, same as
    a target -- it doesn't need to have looked like a genuine turn at the
    time any more than a target does (see _target_candidate_still_live's
    own docstring)."""
    cand = _live_m5_target_candidates(m5_ledger, level_type, touch_time_alt,
                                      price=alt_price, max_pts=DYNAMIC_STOP_RADIUS_PTS)
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
# Target selection -- PREVIOUS CONSOLIDATION AREAS (m5_structure.py)
#
# The original rule (SF.dynamic_target, still available via
# --target-mode opposite-m5) took the newest live opposite-type M5 level
# whose P1 broke >= 2 same-type P0s, 1..20pt away. It is a single PRICE,
# with no notion of whether anything is actually resting there now.
#
# The consolidation rule instead exits where the market last spent real
# time: the nearest congestion area in the trade's own favourable
# direction (see m5_structure.consolidation_areas for the definition),
# restricted to areas built BETWEEN THIS LEVEL'S OWN P1 AND P2 -- wholly
# after the breakout candle and wholly before the retest bar. That is the
# stall price actually made after breaking away from the level, and the
# retest is the market turning back towards it; congestion from before P1
# belongs to a move this level had no part in, and congestion after P2 is
# not yet there to aim at. If
# that area still holds an UNTESTED opposite-type M5 P0 -- one whose own
# price sits inside the area's band and that has never been retested --
# that P0's own price is the target (src tag consol_p0). Otherwise the target
# is the area's NEAR EDGE: its LOW for a long and its HIGH for a short (src tag
# consol_edge): the first price of the zone the trade reaches, not the far
# side it may never get through. The same 1..20pt band bounds both.
# --------------------------------------------------------------------------

TARGET_MODES = ("consolidation", "opposite-m5")


def _window_bounds(touch_time, p1_time, p2_time):
    """(after, cutoff) -- the level's own P1..P2 window, as the exclusive
    instants a target's own structure has to sit between. The window ENDS at
    P2, not at the fill: entry can be hours of fill-window searching after
    the retest, and structure built in those hours is not what the level
    broke away from. `touch_time` still bounds it for the raw-retest caller,
    where the touch IS the P2."""
    end = touch_time if p2_time is None else min(pd.Timestamp(p2_time), pd.Timestamp(touch_time))
    return (None if p1_time is None else pd.Timestamp(p1_time)), MS.entry_cutoff(end)


def _target_candidate_still_live(bars, level_type, price, breakout_time, as_of):
    """Whether an opposite-type M5 candle that FAILED lxpb.py's entry
    candidate gate (fate 'gated_dropped' -- neither a spike nor a
    consolidating swing) has nonetheless gone untouched since its own
    breakout, as of `as_of`.

    A gated-dropped candidate's death_time is stamped equal to its own
    breakout_time (see lxpb_levels_cache.py's fate table) because nothing
    tracks it forward once it fails that gate -- the gate exists to keep
    noise out of ENTRY selection, where a level needs to have looked like a
    real turn AT THE TIME to justify trading its retest. A TARGET doesn't
    need that: a price the market broke through and never came back to is
    still somewhere price could go, whether or not that original break
    looked convincing. So for a gated-dropped candidate only, this replays
    lxpb.py's own retest rule (touched or gapped past, strictly more than
    R.L.MIN_BARS_BEFORE_RETEST bars after the breakout bar -- the same
    "immediately-next bar can never itself be the retest" rule real P0s
    get) directly against the bars, since the ledger never recorded
    whether that actually happened for a candidate that failed the gate."""
    window = bars[(bars.index > breakout_time) & (bars.index <= as_of)]
    skip = R.L.MIN_BARS_BEFORE_RETEST
    if len(window) <= skip:
        return True
    window = window.iloc[skip:]
    touched = (window["low"] <= price) & (window["high"] >= price)
    if level_type == "LHPB":
        gap_over = window["high"] < price
    else:
        gap_over = window["low"] > price
    return not (touched | gap_over).any()


def _live_m5_target_candidates(m5_ledger, level_type, touch_time, min_breakout_levels=1,
                               price=None, max_pts=None):
    """Same query as SF._live_m5_before_entry (still-standing, completed-bar
    opposite-type candidates as of `touch_time`), except a candidate that
    failed lxpb.py's entry candidate gate is not disqualified for that
    reason alone -- see _target_candidate_still_live's own docstring for
    why targets and entries need different standards here. Every other
    fate (real P0s, already accurately tracked to retest/death in the
    ledger) is resolved exactly as SF._live_m5_before_entry does.

    `price`/`max_pts`: every caller immediately throws out anything more
    than a fixed number of points from the fill (1..20pt for a target,
    +/-DYNAMIC_STOP_RADIUS_PTS for a stop) -- pass them here so a
    gated-dropped candidate that could never qualify on distance alone is
    dropped BEFORE its own untouched-since-breakout check runs, not after.
    That check replays real bars per candidate, and the gated-dropped pool
    is the majority of this ledger's whole history (every candle that ever
    broke out and failed the entry gate, tens of thousands of rows) -- most
    of them formed at some unrelated price months away from this trade, so
    skipping the distance-blind ones first is the difference between
    checking a handful of candidates and checking nearly all of history for
    every single trade."""
    if m5_ledger is None or m5_ledger.empty:
        return pd.DataFrame()
    as_of = pd.to_datetime(touch_time, utc=True).floor("5min") - pd.Timedelta(nanoseconds=1)
    confirmed = m5_ledger[(m5_ledger["type"] == level_type) &
                         (m5_ledger["breakout_time"] <= as_of)]
    if min_breakout_levels > 1:
        counts = confirmed.groupby("breakout_time")["formation_time"].transform("nunique")
        confirmed = confirmed[counts >= min_breakout_levels]
    is_gated = confirmed["fate"] == "gated_dropped"
    live = LC.levels_live_as_of(confirmed[~is_gated], as_of)
    gated = confirmed[is_gated]
    if price is not None and max_pts is not None:
        gated = gated[(gated["price"] - price).abs() <= max_pts]
    if gated.empty:
        return live
    bars = LC.m5_bars_continuous()
    still_live = gated.apply(
        lambda r: _target_candidate_still_live(bars, level_type, float(r["price"]),
                                               pd.Timestamp(r["breakout_time"]), as_of),
        axis=1)
    live_gated = gated[still_live].copy()
    live_gated["stage"] = "broken"
    return pd.concat([live, live_gated], ignore_index=True)


def _opposite_m5_target(m5_ledger, level_type, price, is_long, touch_time,
                        p1_time=None, p2_time=None):
    """(target_price, target_info) under the OPPOSITE-M5 rule, or
    (None, None).

    The original rule (SF.dynamic_target, left unchanged there because the
    H1 report still uses it): the most recently FORMED live opposite-type M5
    level, 1..20pt away on the favourable side, whose own P1 candle broke at
    least two distinct same-type P0s. "Live" is _live_m5_target_candidates's
    own query -- broken out on a completed candle before the entry bar,
    never retested since, WITHOUT also requiring the candidate to have
    passed the entry candidate gate (see that function's own docstring) --
    a target only needs an untouched-since price, not a confirmed swing.

    Re-implemented here rather than called, for one reason: this report adds
    the SAME P1..P2 window the consolidation rule uses. The opposite level's
    own P0 must have formed strictly after this level's P1 and strictly
    before its P2, so both target rules answer the same question -- what did
    the market build while this level was waiting to be retested -- and
    differ only in what they look for there."""
    opposite_type = "LLPB" if level_type == "LHPB" else "LHPB"
    cand = _live_m5_target_candidates(m5_ledger, opposite_type, touch_time,
                                      min_breakout_levels=2,
                                      price=price, max_pts=MAX_DYNAMIC_TARGET_PTS)
    if cand.empty:
        return None, None
    after, cutoff = _window_bounds(touch_time, p1_time, p2_time)
    formed = pd.to_datetime(cand["formation_time"], utc=True)
    in_window = formed < cutoff
    if after is not None:
        in_window &= formed > pd.Timestamp(after)
    cand = cand[in_window]
    if cand.empty:
        return None, None
    distance = (cand["price"] - price) if is_long else (price - cand["price"])
    cand = cand[(distance >= MIN_DYNAMIC_TARGET_PTS) & (distance <= MAX_DYNAMIC_TARGET_PTS)]
    if cand.empty:
        return None, None
    best = cand.loc[cand["formation_time"].idxmax()]
    return float(best["price"]), {"src": "m5_opposite", "level": best.to_dict(), "area": None}


def _pick_targets(m5_ledger, level_type, price, is_long, touch_time, args,
                  p1_time=None, p2_time=None):
    """{mode: (target_price, target_info)} for EVERY target rule that
    produces one for this trade -- keys from TARGET_MODES; a missing key
    means that rule found nothing.

    BOTH rules are always computed, whatever --target-mode says, because each
    one is a live in-browser toggle in the report: switching a rule off can
    demote a trade to a no-trade, and switching it back on has to restore
    that trade's whole bracket with no Python regen. --target-mode only picks
    which rules are ON BY DEFAULT in the rendered page.

    With both rules on and both producing a target, the FARTHEST target wins
    -- the rule asking for more room. See _active_mode."""
    out = {}
    px, info = _opposite_m5_target(m5_ledger, level_type, price, is_long,
                                   touch_time, p1_time, p2_time)
    if px is not None:
        out["opposite-m5"] = (px, info)
    px, info = _consolidation_target(m5_ledger, level_type, price, is_long,
                                     touch_time, args, p1_time, p2_time)
    if px is not None:
        out["consolidation"] = (px, info)
    return out


def _active_mode(targets, price, enabled=TARGET_MODES):
    """Which of `targets` (a _pick_targets dict) a row actually trades under
    the given set of ENABLED rules: the one whose target sits FARTHEST from
    the fill, or None when no enabled rule found one (a real no-trade)."""
    live = {m: t for m, t in targets.items() if m in enabled}
    if not live:
        return None
    return max(live, key=lambda m: abs(live[m][0] - price))


def _consolidation_target(m5_ledger, level_type, price, is_long, touch_time, args,
                          p1_time=None, p2_time=None):
    """(target_price, target_info) under the CONSOLIDATION-AREA rule, or
    (None, None). `p1_time`/`p2_time` are the subject level's own breakout
    and retest -- the window an area has to fall inside (see the section
    comment above). They are optional only so a caller without a level of its
    own can omit the bound; every caller here passes them.

    target_info carries 'src' plus whichever of 'area'/'level' that source
    used, so the row and the chart can both show WHERE the target came
    from."""
    opposite_type = "LLPB" if level_type == "LHPB" else "LHPB"
    # "Untested still" == broken out and never retested since, as of the last
    # completed M5 candle before entry -- _live_m5_target_candidates's own
    # query (min_breakout_levels=1: the shared-P1 requirement belongs to the
    # opposite-M5 rule, not to this one, where the consolidation area itself
    # is the evidence that the price matters). Also does not require the
    # candidate to have passed the entry candidate gate -- see that
    # function's own docstring.
    live_opposite = _live_m5_target_candidates(m5_ledger, opposite_type, touch_time,
                                               price=price, max_pts=MAX_DYNAMIC_TARGET_PTS)
    areas = consolidation_areas_for(args)
    after, cutoff = _window_bounds(touch_time, p1_time, p2_time)
    return MS.consolidation_target(areas, live_opposite, price, is_long, cutoff,
                                   MIN_DYNAMIC_TARGET_PTS, MAX_DYNAMIC_TARGET_PTS,
                                   after=after)


def consolidation_areas_for(args):
    """m5_structure.consolidation_areas under this run's own CLI settings
    (memoised there, so this is one walk per process)."""
    return MS.consolidation_areas(min_bars=args.consol_min_bars,
                                  max_height=args.consol_max_height,
                                  max_er=args.consol_max_er)


# --------------------------------------------------------------------------
# "SWERVED" entry -- step past a swing sitting on the planned entry
#
# A confirmed swing low (for a LONG) or swing high (for a SHORT) within
# +/-`--swerve-tol-pts` of the fine-tuned entry -- formed while the level was
# waiting to be retested, i.e. between its own P1 and its P2 -- is a price
# the market has already turned away from once, and one every other
# participant can see too, so a resting order there is at the back of a
# long queue and in front of whatever is hunting it. When that happens the
# order is moved PAST the swing -- lower for a long, higher for a short --
# to the nearest OTHER live same-side M5 LXPB level within
# `--swerve-max-move-pts`, skipping any candidate that has the same problem.
#
# If no such level exists the trade is not taken (tag `swerve_blocked`);
# it is still processed and charted at its ORIGINAL entry so the report can
# show what the untaken trade would have done -- see the dynamic-filter
# convention in _apply_p1_reaction_filter's docstring.
# --------------------------------------------------------------------------

def _swerve_entry(m5_ledger, level_type, is_long, conf, row_d, args):
    """Swerve-rule verdict for one cluster, as a dict (or None when the rule
    is off). MUTATES `conf` in place when the entry actually moves, so every
    downstream user of conf -- the fill scan, the spike-P0 stop override
    (_entry_level_row), the M5 chart's own entry-level ray -- follows the
    moved entry rather than the planned one.

    Keys: swings (the (time, price) pivots that triggered it), moved (bool),
    planned_price, price (the entry in force afterwards), level (the ledger
    row moved to, if any), blocked (bool -- triggered but nowhere to move).

    The window searched is the level's OWN wait -- from its P1 breakout bar
    to its retest -- since that is exactly the span over which price could
    have come back towards the entry and turned away without reaching it.
    `--swerve-lookback-hours` caps it, for the levels that sit broken out
    for weeks before anything comes back."""
    if not args.swerve:
        return None
    planned = float(conf["alt_price"])
    retest_time = pd.to_datetime(row_d["retest_time"], utc=True)
    breakout_time = pd.to_datetime(row_d["breakout_time"], utc=True)
    lo_ts = max(breakout_time, retest_time - pd.Timedelta(hours=args.swerve_lookback_hours))
    swings = MS.swings_near(planned, args.swerve_tol_pts, lo_ts, retest_time,
                            is_low=is_long, k=args.swerve_swing_k)
    if not swings:
        return None

    out = {"swings": swings, "moved": False, "blocked": False,
           "planned_price": planned, "price": planned, "level": None}
    cand = SF._live_m5_before_entry(m5_ledger, level_type, retest_time)
    if cand.empty:
        out["blocked"] = True
        return out
    beyond = (cand["price"] < planned) if is_long else (cand["price"] > planned)
    cand = cand[beyond & ((cand["price"] - planned).abs() <= args.swerve_max_move_pts)]
    if cand.empty:
        out["blocked"] = True
        return out
    # Nearest first: an order moved further than it has to be is a worse
    # entry, not a better one.
    cand = cand.assign(_dist=(cand["price"] - planned).abs()).sort_values("_dist")
    for _, lv in cand.iterrows():
        price = float(lv["price"])
        if MS.swings_near(price, args.swerve_tol_pts, lo_ts, retest_time,
                          is_low=is_long, k=args.swerve_swing_k):
            continue   # same problem one level down -- keep stepping
        out.update({"moved": True, "price": price, "level": lv.to_dict()})
        conf["alt_price"] = price
        conf["alt_source"] = "m5"
        conf["entry_m5_level"] = lv.to_dict()
        conf["alt_formation_time"] = pd.Timestamp(lv["formation_time"])
        conf["alt_end_time"] = (retest_time if pd.isna(lv["death_time"])
                                else min(pd.Timestamp(lv["death_time"]), retest_time))
        return out
    out["blocked"] = True
    return out


GLOBEX_OPEN_START_PT = pd.Timedelta(hours=15)
GLOBEX_OPEN_END_PT = pd.Timedelta(hours=15, minutes=5)


def _in_globex_open_window(ts_utc):
    """True if ts_utc's Pacific-time-of-day falls in [15:00, 15:05) -- the
    daily Globex/ETH reopen (6pm ET), when the M5 stop/target structure this
    strategy trades against isn't reliable across the reopen gap. Used by
    _apply_globex_open_filter to TAG (not remove) a filled trade's own fill
    time -- see that function's docstring for the dynamic-filter
    convention this participates in."""
    pt = pd.Timestamp(ts_utc).tz_convert("America/Los_Angeles")
    tod = pd.Timedelta(hours=pt.hour, minutes=pt.minute)
    return GLOBEX_OPEN_START_PT <= tod < GLOBEX_OPEN_END_PT


# --------------------------------------------------------------------------
# Per-cluster processing (one cluster = one trade)
# --------------------------------------------------------------------------

def process_cluster(cluster, args):
    anchor = SF.cluster_anchor(cluster)
    row_d = anchor["row"]
    level_type = row_d["type"]
    is_long = level_type == "LHPB"
    # Every candidate in a cluster was matched via the SAME segment's own
    # confluence pool, so they all share one ledger.
    m5_ledger = cluster[0]["m5_ledger"]
    own_price = float(row_d["entry_price"])
    member_prices = sorted({float(c["row"]["entry_price"]) for c in cluster},
                           reverse=(level_type == "LLPB"))

    conf = cluster_confluence(cluster)
    swerve = _swerve_entry(m5_ledger, level_type, is_long, conf, row_d, args)
    alt_price, alt_source, group_n = conf["alt_price"], conf["alt_source"], conf["group_n"]

    cluster_member_formations = sorted(
        pd.Timestamp(c["row"]["formation_time"]) for c in cluster)

    result = {
        "i": anchor["i"], "row": row_d, "level_type": level_type, "is_long": is_long,
        "seg_idx": cluster[0]["seg_idx"],
        "group_n": group_n, "cluster_size": len(cluster), "cluster_members": member_prices,
        "cluster_member_formations": cluster_member_formations,
        "own_price": own_price, "alt_price": alt_price, "alt_source": alt_source,
        "alt_formation_time": conf["alt_formation_time"], "alt_end_time": conf["alt_end_time"],
        "entry_m5_level": conf["entry_m5_level"],
        "improved": abs(alt_price - own_price) > 1e-9,
        "swerve": swerve,
        "dyn_tags": [],
        "filled": False,
    }
    if swerve is not None:
        result["dyn_tags"].append("swerved" if swerve["moved"] else "swerve_blocked")

    window_start = pd.to_datetime(row_d["retest_time"], utc=True)
    touch_time_alt, fill_price = SF.find_alt_fill(
        window_start, alt_price, is_long, level_type, args.max_alt_fill_hours,
        pegged=args.pegged_entry, peg_step=args.peg_step, peg_cap=args.peg_cap)
    if touch_time_alt is None:
        result["fail_reason"] = "unfilled_within_window"
        return result
    # End-of-day flat, entry half (trade_management.py rule 3): a resting
    # order is CANCELLED at the cutoff, so a fill that would have landed
    # inside the blocked window is not a trade at all -- it is never re-sent
    # after the reopen either, since by then the retest that justified it is
    # hours old.
    if args.eod_flat and TM.entry_blocked(touch_time_alt):
        result["fail_reason"] = "eod_entry_blocked"
        result["touch_time_alt"] = touch_time_alt
        return result
    result["fill_price"] = fill_price
    chase = fill_price - alt_price if is_long else alt_price - fill_price
    result["chased_pts"] = max(0.0, chase)
    result["price_improvement_pts"] = max(0.0, -chase)
    result["improved"] = fill_price < own_price if is_long else fill_price > own_price

    bars = SF.build_minute_bars(touch_time_alt)
    if bars is None or bars.empty:
        result["fail_reason"] = "no_tick_data_after_fill"
        return result

    # Liquidity gate (liquidity.py): measured off the real tape in the
    # minutes before this fill, so scheduled releases (FOMC/NFP/CPI/PPI) and
    # unscheduled headline shocks are both caught, and trading re-enables by
    # itself once the quoted spread comes back in. Tags rather than skips --
    # the row and its charts stay in the report behind a dynamic filter (on
    # by default) so a "skipped for news" trade can still be reviewed.
    if args.liquidity_gate:
        blocked, liq = LQ.gate(touch_time_alt, window_minutes=args.liq_window_minutes,
                               wide_spread_share_max=args.liq_wide_spread_share)
        result["liquidity"] = liq
        if blocked:
            result["dyn_tags"].append("low_liquidity")

    # The STOP is target-rule-agnostic, so it is resolved once, before any
    # target: a trade with no qualifying stop is not a trade under either
    # rule, and there is nothing for the browser toggles to switch between.
    entry_level_info = _entry_level_row(cluster, conf)
    stop_price, stop_row, stop_source = _dynamic_stop_m5(
        level_type, fill_price, is_long, entry_level_info,
        m5_ledger, touch_time_alt)
    if stop_price is None:
        result["fail_reason"] = "no_m5_stop"
        return result
    stop_pts = abs(stop_price - fill_price)
    if stop_pts <= 0:
        result["fail_reason"] = "degenerate_stop"
        return result
    result["stop_price"] = stop_price
    result["stop_pts"] = stop_pts
    result["stop_m5_level"] = stop_row.to_dict()
    result["stop_source"] = stop_source

    targets = _pick_targets(m5_ledger, level_type, fill_price, is_long, touch_time_alt,
                            args, p1_time=row_d["breakout_time"],
                            p2_time=row_d["retest_time"])
    active = _active_mode(targets, fill_price, enabled=args.default_target_modes)
    if active is None:
        result["fail_reason"] = "no_target"
        return result
    # EVERY rule that found a target is resolved, not just the active one:
    # the report's target-rule checkboxes swap a row between them live (see
    # _pick_targets), which needs each rule's own outcome precomputed.
    result["modes"] = {mode: _resolve_target_mode(t_price, t_info, fill_price, stop_pts,
                                                  level_type, is_long, touch_time_alt,
                                                  bars, m5_ledger, args)
                       for mode, (t_price, t_info) in targets.items()}
    result["active_mode"] = active
    result.update({"filled": True, "touch_time_alt": touch_time_alt})
    _apply_mode(result, active)
    return result


def _resolve_target_mode(target_price, target_info, fill_price, stop_pts, level_type,
                         is_long, touch_time_alt, bars, m5_ledger, args):
    """One target rule's whole outcome for one trade: its bracket, its
    baseline resolution, its managed resolution, and the dynamic-filter tags
    that depend on the target (r_below_min, eod_flat -- both differ per rule,
    unlike the row-level tags in res['dyn_tags'])."""
    target_pts = abs(target_price - fill_price)
    r_multiple = target_pts / stop_pts
    trade = {"type": level_type, "entry": fill_price, "is_long": is_long,
             "retest_time": touch_time_alt.tz_convert("UTC").tz_localize(None),
             "stop_dist": stop_pts, "target_dist": target_pts}
    resolved = (TM.resolve_with_eod(trade, bars) if args.eod_flat
                else SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0])
    managed = TM.resolve_managed_trade(trade, bars, level_type, ledger=m5_ledger,
                                       eod=args.eod_flat)
    tags = []
    # A sub-min-R bracket is not skipped outright: the target is only knowable
    # just before entry, and seeing what those trades actually did is the
    # point of measuring them. They are TAGGED instead, and the report's own
    # dynamic filter drops them from the headline stats by default -- untick
    # it to fold them back in.
    if r_multiple < args.min_r:
        tags.append("r_below_min")
    if resolved.get("outcome") == "eod_flat":
        tags.append("eod_flat")
    return {"target_price": target_price, "target_pts": target_pts,
            "target_info": target_info, "r_multiple": r_multiple,
            "resolved": resolved, "mgmt": _mgmt_summary(resolved, managed, stop_pts),
            "dyn_tags": tags}


def _apply_mode(result, mode):
    """Copy one target rule's outcome (result['modes'][mode]) into the row's
    own top-level keys, so every consumer -- the charts, the server-side
    stats, _render_row -- reads the ACTIVE rule without knowing there is more
    than one. The per-rule dicts stay in result['modes'] for the browser."""
    m = result["modes"][mode]
    resolved = m["resolved"]
    result["active_mode"] = mode
    result.update({
        "target_price": m["target_price"], "target_pts": m["target_pts"],
        "target_info": m["target_info"], "r_multiple": m["r_multiple"],
        "resolved": resolved, "mgmt": m["mgmt"],
        "favorable_pts": resolved.get("favorable_pts"),
        "adverse_pts": resolved.get("adverse_pts"),
        "giveback_pts": resolved.get("giveback_pts"),
        "entry_gapped": resolved.get("entry_gapped", False),
    })
    return result


def _mgmt_summary(resolved, managed, stop_pts):
    """Per-row trade-management summary for the report's dynamic toggle
    (see the 'Trade management' checkbox in _finish_report): mirrors the
    baseline (unmanaged) r/outcome/pnl/exit_time when the rules never
    actually changed anything for this trade (managed is None -- a short
    trade, rule 1 is long-only -- or fired no events), else the managed
    values. `fired` tells the JS whether swapping to these values would
    even change anything (kept explicit rather than relying on float
    equality between baseline and managed R)."""
    if not managed or not (managed.get("trail_events") or managed.get("rr_floor_fired")):
        r = resolved.get("r")
        return {"r": r, "outcome": resolved.get("outcome"), "exit_time": resolved.get("exit_time"),
                "pnl_pts": (r * stop_pts) if r is not None else None, "fired": False,
                "trail_events": [], "rr_floor_fired": False}
    r = managed.get("r")
    return {"r": r, "outcome": managed.get("outcome"), "exit_time": managed.get("exit_time"),
            "pnl_pts": (r * stop_pts) if r is not None else None, "fired": True,
            "trail_events": managed.get("trail_events") or [],
            "rr_floor_fired": managed.get("rr_floor_fired", False)}


def _annotate_p0_p1_p2(chart_m5, row_for_chart, is_long):
    """Explicit P0 (formation) / P1 (breakout) markers on the M5 pane, and
    an explicit "P2" tag on its existing entry marker -- the same P0/P1/P2
    convention render_stop_target_report.build_trade_chart draws on the H1
    pane in every other report. This strategy has no H1 pane (see module
    docstring): the M5 pane IS the primary structural chart here, so it
    needs the same annotation build_m5_chart never had to provide when it
    was only ever a companion pane to an H1-anchored row elsewhere.

    build_m5_chart's own window can compress out bars between formation/
    breakout/retest (it was only ever built to guarantee the entry region
    and each M5 confluence ray's own span, not this subject level's own
    P0/P1), so each marker snaps to the nearest bar AT OR BEFORE its real
    time -- same convention that function's own entry-bar marker already
    uses -- and is silently skipped if that time is before the pane's own
    first bar. Mutates `chart_m5` in place; no-op if it is None."""
    if chart_m5 is None or not chart_m5["candles"]:
        return
    times = [c["time"] for c in chart_m5["candles"]]

    def snap(ts):
        target = R._to_epoch_utc(pd.Timestamp(ts))
        i = bisect.bisect_right(times, target) - 1
        return times[i] if i >= 0 else None

    for m in chart_m5["markers"]:
        text = m.get("text", "")
        if text.startswith("ENTRY") or text.startswith("PLANNED"):
            m["text"] = "P2 " + text

    p0_t = snap(row_for_chart["formation_time"])
    p1_t = snap(row_for_chart["breakout_time"])
    new_markers = []
    if p0_t is not None:
        new_markers.append({
            "time": p0_t, "position": "aboveBar" if is_long else "belowBar",
            "color": R.P0_COLOR, "shape": "circle", "text": "P0",
        })
    if p1_t is not None:
        new_markers.append({
            "time": p1_t, "position": "belowBar" if is_long else "aboveBar",
            "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
            "shape": "arrowUp" if is_long else "arrowDown", "text": "P1",
        })
    chart_m5["markers"].extend(new_markers)
    chart_m5["markers"].sort(key=lambda m: m["time"])


def _p1_sibling_group(level_type, breakout_time):
    """Every M5 level (any fate) sharing (level_type, breakout_time) in the
    one continuous M5 ledger -- i.e. the full field of P0 candidates one
    breakout bar broke at once, including ones lxpb.py's own candidate
    gate silently dropped (fate 'gated_dropped' -- see lxpb.py's own
    advance_one_bar docstring), not just the ones that became tradeable
    P0s. Sorted by formation_time. Returns None if fewer than 2 candidates
    shared this P1 (nothing else to annotate or protect from chart
    compression)."""
    ledger = LC.m5_levels(verbose=False)
    if ledger is None or ledger.empty:
        return None
    breakout_time = pd.Timestamp(breakout_time)
    if breakout_time.tzinfo is None:
        breakout_time = breakout_time.tz_localize("UTC")
    group = ledger[(ledger["type"] == level_type) &
                   (pd.to_datetime(ledger["breakout_time"], utc=True) == breakout_time)]
    if len(group) < 2:
        return None
    return group.sort_values("formation_time")


def _annotate_candidates(chart_m5, row_for_chart, is_long, group, cluster_member_formations):
    """Mark every OTHER M5 P0 candidate in `group` (see _p1_sibling_group),
    labeled C1/C2/... in formation-time order -- the whole field the
    winning P0 was chosen from, not just the winner. The winning P0 itself
    (already marked "P0" by _annotate_p0_p1_p2, called first) gets its own
    C-number folded into that same marker's text instead of a second
    overlapping dot.

    A confluence cluster can merge >1 mutually-confluent M5 level into ONE
    trade (see cluster_candidates/cluster_confluence): when that happens,
    `row_for_chart` (built from cluster_anchor's row -- the EARLIEST-
    retesting member) is only ONE of those levels, and another member --
    e.g. the one whose price actually became the fill via alt_price's
    most-extreme-of-cluster rule -- can independently show up in this
    SAME P1 group. Without checking `cluster_member_formations` (every
    member's own formation_time, not just the anchor's), that other
    member would be mislabeled as a rejected/unchosen sibling candidate
    when it's actually already part of THIS trade -- so it gets
    "(in trade)" appended instead of being flagged suppressed or plain.
    Mutates chart_m5 in place; no-op if chart_m5 or group is None."""
    if chart_m5 is None or not chart_m5["candles"] or group is None:
        return

    # Matched on formation_time ALONE, not price: row_for_chart["price"] has
    # already been overwritten with the trade's actual FILL price by the
    # caller (build_chart_stack_for_row), which can differ from the P0's own
    # raw ledger price (most-extreme-of-cluster selection, pegged chasing).
    # formation_time is untouched and unique per type within one P1 group
    # (one bar can only register one LLPB / one LHPB), so it alone is exact.
    own_formation = pd.Timestamp(row_for_chart["formation_time"])
    if own_formation.tzinfo is None:
        own_formation = own_formation.tz_localize("UTC")
    member_formations = {pd.Timestamp(t).tz_localize("UTC") if pd.Timestamp(t).tzinfo is None
                         else pd.Timestamp(t) for t in cluster_member_formations}

    times = [c["time"] for c in chart_m5["candles"]]

    def snap(ts):
        target = R._to_epoch_utc(pd.Timestamp(ts))
        i = bisect.bisect_right(times, target) - 1
        return times[i] if i >= 0 else None

    n_group = len(group)
    new_markers = []
    for n, (_, lv) in enumerate(group.iterrows(), start=1):
        formation = pd.Timestamp(lv["formation_time"])
        if formation == own_formation:
            for m in chart_m5["markers"]:
                if m.get("text") == "P0":
                    m["text"] = f"P0 (C{n}/{n_group})"
            continue
        t = snap(lv["formation_time"])
        if t is None:
            continue
        if formation in member_formations:
            new_markers.append({
                "time": t, "position": "belowBar" if is_long else "aboveBar",
                "color": CANDIDATE_COLOR, "shape": "circle",
                "text": f"C{n} (in trade)",
            })
            continue
        suppressed = lv["fate"] == "gated_dropped"
        new_markers.append({
            "time": t, "position": "belowBar" if is_long else "aboveBar",
            "color": CANDIDATE_COLOR, "shape": "circle",
            "text": f"C{n}" + (" ✕" if suppressed else ""),
        })
    chart_m5["markers"].extend(new_markers)
    chart_m5["markers"].sort(key=lambda m: m["time"])


def _annotate_mgmt_events(chart_m5, res, is_long):
    """Mark fired trade-management events (rule 1 stop trail, rule 2
    RR-floor exit -- see trade_management.py) on the M5 pane, snapped to
    the nearest bar at-or-before their own time (same convention
    _annotate_p0_p1_p2 uses). No-op if chart_m5 is None or nothing fired
    for this trade."""
    mgmt = res.get("mgmt") or {}
    if chart_m5 is None or not chart_m5["candles"] or not mgmt.get("fired"):
        return
    times = [c["time"] for c in chart_m5["candles"]]

    def snap(ts):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        target = R._to_epoch_utc(ts)
        i = bisect.bisect_right(times, target) - 1
        return times[i] if i >= 0 else None

    new_markers = []
    for trigger_time, new_stop_price in mgmt.get("trail_events") or []:
        # trigger_time is the thrust candle's own CLOSE (= the next bar's
        # open, see trade_management.thrust_trail_events) -- step back one
        # bar so the marker lands on the thrust candle itself, the one
        # responsible for the event, not the candle after it.
        t = snap(trigger_time - P1_BAR_WIDTH)
        if t is not None:
            new_markers.append({
                "time": t, "position": "belowBar" if is_long else "aboveBar",
                "color": "#22d3ee", "shape": "arrowUp" if is_long else "arrowDown",
                "text": f"Stop → {new_stop_price:.2f}",
            })
    if mgmt.get("rr_floor_fired") and mgmt.get("exit_time") is not None:
        t = snap(mgmt["exit_time"])
        if t is not None:
            new_markers.append({
                "time": t, "position": "aboveBar" if is_long else "belowBar",
                "color": "#fb923c", "shape": "circle", "text": "RR FLOOR EXIT",
            })
    if new_markers:
        chart_m5["markers"].extend(new_markers)
        chart_m5["markers"].sort(key=lambda m: m["time"])


def _snapper(chart_m5):
    """A `snap(ts) -> chart bar time` helper for one M5 chart, or None if the
    chart has no candles. Same at-or-before convention every other annotator
    in this module uses (the pane is compressed, so an arbitrary instant may
    not have its own bar)."""
    if chart_m5 is None or not chart_m5["candles"]:
        return None
    times = [c["time"] for c in chart_m5["candles"]]

    def snap(ts):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        i = bisect.bisect_right(times, R._to_epoch_utc(ts)) - 1
        return times[i] if i >= 0 else None
    return snap


CONSOL_ZONE_COLOR = "#a78bfa"   # the target's own consolidation area (violet)
SWERVE_COLOR = "#86efac"        # the swing that moved the entry, and the planned entry it left


def _annotate_target_zone(chart_m5, res):
    """Draw the consolidation area the target came from as two dashed price
    lines (its high and its low) -- see _pick_targets. The target price line
    itself is already drawn by build_m5_chart; these show the ZONE it sits
    in, which is what makes a consol_edge target readable as an edge rather
    than an arbitrary price. No-op for --target-mode opposite-m5, which has
    no zone.

    Price lines, not markers on the area's own bars: the area now falls
    inside the level's own P1..P2 span, but the pane is anchored on the
    trade and compressed, so the area's bars may still be missing from it.
    Each line's title carries the area's own start time and length, which
    is the part a reviewer needs."""
    info = res.get("target_info") or {}
    area = info.get("area")
    if chart_m5 is None or not area:
        return
    for key in ("high", "low"):
        chart_m5.setdefault("priceLines", []).append({
            "price": area[key], "color": CONSOL_ZONE_COLOR, "lineWidth": 1, "lineStyle": 2,
            "title": (f"consolidation {key} ({area['n_bars']} M5 bars from "
                      f"{R._to_pt_str(area['start_time'])})"),
        })


def _annotate_swerve(chart_m5, res, is_long):
    """Mark the swing(s) that triggered the swerve rule and, when the entry
    actually moved, the planned entry price it was moved off -- see
    _swerve_entry. No-op when the rule never triggered for this trade."""
    sw = res.get("swerve")
    snap = _snapper(chart_m5)
    if not sw or snap is None:
        return
    chart_m5.setdefault("priceLines", []).append({
        "price": sw["planned_price"], "color": SWERVE_COLOR, "lineWidth": 1, "lineStyle": 2,
        "title": ("planned entry -- swerve blocked, not taken" if sw["blocked"]
                  else f"planned entry (swerved to {sw['price']:.2f})"),
    })
    new_markers = []
    for ts, px in sw["swings"]:
        t = snap(ts)
        if t is not None:
            new_markers.append({
                "time": t, "position": "belowBar" if is_long else "aboveBar",
                "color": SWERVE_COLOR, "shape": "circle",
                "text": f"swing {px:.2f}"})
    if new_markers:
        chart_m5["markers"].extend(new_markers)
        chart_m5["markers"].sort(key=lambda m: m["time"])


def build_chart_stack_for_row(res):
    """M5 + 1s-trio + 1min + footprint chart stack for a filled, in-R trade
    -- no H1 pane exists in this strategy. Reuses
    render_ss_confl_finetune_report.build_execution_charts verbatim (it
    never assumed an H1-anchored row) and render_stop_target_report.
    build_m5_chart with this strategy's own 5-minute P1 bar width."""
    row_d = res["row"]
    alt_price = res["fill_price"]
    resolved = res["resolved"]
    stop_pts, target_pts = res["stop_pts"], res["target_pts"]

    row_for_chart = row_d.copy()
    row_for_chart["price"] = alt_price
    # build_m5_chart does `pd.Timestamp(row["retest_time"], tz="UTC")` (and
    # likewise for breakout_time), which raises on an already tz-aware
    # value -- unlike the H1 reference report's naive-epoch-UTC timestamps,
    # the M5 ledger's time columns are tz-aware UTC (see build_ledger's
    # _TIME_COLS conversion), so strip tz here (same absolute instant).
    for col in ("retest_time", "breakout_time"):
        ts = pd.Timestamp(row_for_chart[col])
        if ts.tzinfo is not None:
            row_for_chart[col] = ts.tz_convert("UTC").tz_localize(None)
    # Fetched BEFORE build_m5_chart (not just for _annotate_candidates
    # afterward) so every sibling candidate's own formation bar can be
    # passed in as extra_context_times -- otherwise build_m5_chart's own
    # compression can land one inside a "[N bars skipped]" gap before it
    # ever gets a marker, making the real price action around it
    # unreviewable (see extra_context_times's own docstring).
    sibling_group = _p1_sibling_group(row_for_chart["type"], row_for_chart["breakout_time"])
    # The breakout bar and the retest bar get explicit protection too, not
    # just each candidate's own formation bar -- retest is already the
    # anchor of the always-shown entry region, but making it explicit here
    # costs nothing and removes any doubt per this trade's own review need.
    extra_context_times = [row_for_chart["breakout_time"], row_for_chart["retest_time"]]
    if sibling_group is not None:
        extra_context_times.extend(sibling_group["formation_time"])
    chart_m5 = SR.build_m5_chart(
        row_for_chart, resolved, stop_pts, target_pts,
        level_price=res["own_price"], entry_level=res["entry_m5_level"],
        p1_bar_width=P1_BAR_WIDTH, p1_label="M5",
        # This M5 pane IS the primary structural chart here (no H1 pane
        # exists), and now also carries the C1/C2/... candidate markers
        # (_annotate_candidates) -- double the shared defaults so the
        # wider, full-row pane (see chart-row-solo below) actually shows
        # more market structure instead of just more empty space.
        bars_before_retest=2 * SR.M5_BARS_BEFORE_RETEST,
        bars_after_exit=2 * SR.M5_BARS_AFTER_EXIT,
        extra_context_times=extra_context_times)
    if chart_m5 is not None:
        chart_m5["title"] += (f"  |  R {res['r_multiple']:.2f}  |  entry via "
                              f"{res['alt_source']} ({res['group_n']} in group)")
        _annotate_p0_p1_p2(chart_m5, row_for_chart, res["is_long"])
        _annotate_candidates(chart_m5, row_for_chart, res["is_long"], sibling_group,
                            res["cluster_member_formations"])
        _annotate_mgmt_events(chart_m5, res, res["is_long"])
        _annotate_target_zone(chart_m5, res)
        _annotate_swerve(chart_m5, res, res["is_long"])
    execution_charts, fp = SF.build_execution_charts({**res, "row": row_for_chart})
    return {"m5": chart_m5, **execution_charts}, fp


def _build_unfilled_chart_stack(res, args):
    """Best-effort chart stack for a row that never became a trade, whatever
    the fail_reason -- the report always ships every row's charts, not just
    filled ones (a NO TRADE row still needs to be reviewable, same principle
    as render_ss_confl_finetune_report's own unfilled rows).

    alt_price/level_type/own_price/entry_m5_level and row_d['retest_time']
    are all set before process_cluster's very first early return, so this
    works identically no matter which stage failed. Reuses
    render_stop_target_report.build_m5_chart with a stub 'resolved' (no
    fabricated touch or exit -- same convention as build_unfilled_chart_stack
    in render_ss_confl_finetune_report.py) for the structural M5 pane, and
    SF.build_fill_window_chart for a 1-minute pane spanning the whole
    fill-search window so a reviewer can see what price actually did.

    No stop/target price lines: unlike the H1 finetune report, this
    strategy has no fixed fallback stop/target to draw as 'nominal' lines,
    and neither was ever computed for a no-trade row -- inventing one would
    be exactly the kind of fabricated bracket CLAUDE.md warns against, so
    the lines are stripped instead. No trio/footprint either: both need a
    real tick-level touch instant, which a no-trade row never had."""
    row_d = res["row"]
    level_type = res["level_type"]
    alt_price = res["alt_price"]
    window_start = pd.to_datetime(row_d["retest_time"], utc=True)
    window_end = window_start + pd.Timedelta(hours=args.max_alt_fill_hours)
    resolved_stub = {"outcome": "no_data", "exit_time": None, "r": None, "touch_time": None}

    row_for_chart = row_d.copy()
    row_for_chart["price"] = alt_price
    for col in ("retest_time", "breakout_time"):
        ts = pd.Timestamp(row_for_chart[col])
        if ts.tzinfo is not None:
            row_for_chart[col] = ts.tz_convert("UTC").tz_localize(None)
    chart_m5 = SR.build_m5_chart(
        row_for_chart, resolved_stub, 1.0, 1.0,
        level_price=res["own_price"], entry_level=res["entry_m5_level"],
        p1_bar_width=P1_BAR_WIDTH, p1_label="M5",
        bars_before_retest=2 * SR.M5_BARS_BEFORE_RETEST,
        bars_after_exit=2 * SR.M5_BARS_AFTER_EXIT,
        fill_window=(window_start, window_end))
    if chart_m5 is not None:
        chart_m5["priceLines"] = []
        chart_m5["title"] += (f"  |  entry via {res['alt_source']} "
                              f"({res['group_n']} in group)  |  "
                              f"{_fail_reason_label(res.get('fail_reason'))} "
                              f"(no stop/target -- never computed)")

    fill_window = SF.build_fill_window_chart(
        window_start, alt_price, level_type, args.max_alt_fill_hours,
        res.get("fail_reason"))
    if fill_window is not None:
        # build_fill_window_chart's own title says "refined H1 retest" --
        # right for its native H1-finetune caller, wrong here (this
        # strategy has no H1 leg at all; window_start IS the M5 retest).
        fill_window["title"] = fill_window["title"].replace(
            "refined H1 retest", "M5 retest")
    chart_stack = {"m5": chart_m5, "trio": None, "oneMin": fill_window}
    note = "<p class='note'>(no trade -- no tick-level touch to build a footprint from)</p>"
    fp = {"narrow": note, "wide": note}
    return chart_stack, fp


# --------------------------------------------------------------------------
# Parallel per-cluster processing -- process_cluster/build_chart_stack_for_row
# are the .scid-tick-backed heavy lifting (find_alt_fill's real-tick scan,
# build_minute_bars, resolve_trades, the 1s-trio/footprint panes); with
# --workers > 1 this fans them out across real child processes so a full
# multi-month regen can use every core on the machine instead of walking
# clusters one at a time. Real processes (not threads), same rationale as
# render_stop_target_report._build_records_parallel: each child's resident
# .scid contract cache (render_labels_report._load_contract, ~2-3GB) is
# freed when it exits, and CPython threads wouldn't parallelize this
# pandas/IO-bound work anyway (GIL).
# --------------------------------------------------------------------------

def _chunk_clusters_by_contract(clusters, n_chunks):
    """Contract-pure chunking of cluster POSITIONS, reusing
    analyze_breakout_exits_1min.chunk_indices_by_contract (the same utility
    render_stop_target_report._chunk_indices uses for the H1 report) so a
    worker only ever memory-maps one .scid contract. cluster_candidates
    only ever unions candidates drawn from the SAME segment's own M5
    ledger (select_candidates keys same_side_m5 off that one segment), so
    every cluster is already single-contract -- this never needs to split
    one cluster's own members across workers.

    chunk_indices_by_contract does `pd.Timestamp(t["retest_time"], tz="UTC")`,
    which raises on an already tz-aware value -- unlike the H1 report's
    naive-epoch-UTC row times, the M5 ledger's retest_time is tz-aware UTC
    (see build_ledger's _TIME_COLS conversion), so strip tz here first
    (same absolute instant), same convention build_chart_stack_for_row
    already uses for this exact mismatch."""
    def _naive_retest(c):
        ts = pd.Timestamp(SF.cluster_anchor(c)["row"]["retest_time"])
        return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts
    pseudo_trades = [{"retest_time": _naive_retest(c)} for c in clusters]
    return M.chunk_indices_by_contract(pseudo_trades, n_chunks)


def _run_cluster_chunk_subprocess(spec):
    """Child-process entry point: run process_cluster + chart building for
    this chunk's clusters and pickle the (result, chart_stack, fp) triples
    out, keyed by each cluster's ORIGINAL position in the full `clusters`
    list -- chunks can finish in any order, so the parent needs the real
    position to reassemble the report.

    `m5_ledger` was stripped from every candidate before crossing the
    process boundary (see process_clusters) to avoid pickling the whole
    (now continuous, whole-history) ledger once per candidate; reload it
    once per child process here instead (cheap -- LC.m5_levels reads the
    cached parquet, not raw ticks) and re-attach it, exactly what
    select_candidates itself hands process_cluster in the serial
    (--workers=1) path. One shared ledger for every candidate in this
    chunk now -- there is only ever one M5 ledger (see the "continuous
    contracts only" convention in CLAUDE.md), not one per contract
    segment."""
    args = spec["args"]
    m5_ledger = LC.m5_levels(verbose=False)
    out = {}
    for pos, cluster in zip(spec["positions"], spec["clusters"]):
        for cand in cluster:
            cand["m5_ledger"] = m5_ledger
        res = process_cluster(cluster, args)
        if res["filled"]:
            chart_stack, fp = build_chart_stack_for_row(res)
        else:
            chart_stack, fp = _build_unfilled_chart_stack(res, args)
        out[pos] = (res, chart_stack, fp)
    with open(spec["out_path"], "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)


def process_clusters(clusters, args):
    """Run process_cluster + chart building for every cluster, in original
    order. Serial when args.workers <= 1 (or there's nothing to split);
    otherwise splits into contract-pure chunks (_chunk_clusters_by_contract)
    and runs each chunk in its own child process, polling like
    render_stop_target_report._build_records_parallel. Returns (results,
    chart_stacks, fps) -- three lists in original cluster order. An
    unfilled cluster's chart_stacks[i]/fps[i] come from
    _build_unfilled_chart_stack rather than build_chart_stack_for_row (no
    resolved trade to chart, but still real market structure -- see that
    function's own docstring), built in the SAME process as this cluster's
    own tick scan (process_cluster's find_alt_fill) so it reuses the warm
    render_labels_report._load_contract cache instead of reloading .scid
    data cold in the parent after a worker exits."""
    n = len(clusters)
    if args.workers <= 1 or n <= 1:
        results, chart_stacks, fps = [], [], []
        for i, cluster in enumerate(clusters, start=1):
            anchor = SF.cluster_anchor(cluster)
            row_d = anchor["row"]
            tag = f"  [cluster of {len(cluster)}]" if len(cluster) > 1 else ""
            print(f"  [{i}/{n}] row {anchor['i']} {row_d['type']} "
                  f"{float(row_d['price']):.2f} retest {R._to_pt_str(row_d['retest_time'])}{tag}",
                  flush=True)
            res = process_cluster(cluster, args)
            if res["filled"]:
                chart_stack, fp = build_chart_stack_for_row(res)
            else:
                chart_stack, fp = _build_unfilled_chart_stack(res, args)
            results.append(res)
            chart_stacks.append(chart_stack)
            fps.append(fp)
        return results, chart_stacks, fps

    chunks = _chunk_clusters_by_contract(clusters, args.workers)
    cache_dir = os.path.join(_HERE, "data", "parallel_chunks")
    os.makedirs(cache_dir, exist_ok=True)
    specs = []
    for c, positions in enumerate(chunks):
        chunk_clusters = []
        for p in positions:
            light_cluster = []
            for cand in clusters[p]:
                light = dict(cand)
                light["m5_ledger"] = None  # reloaded in the child, see _run_cluster_chunk_subprocess
                light_cluster.append(light)
            chunk_clusters.append(light_cluster)
        specs.append({
            "positions": positions, "clusters": chunk_clusters, "args": args,
            "out_path": os.path.join(cache_dir, f"chunk{c:02d}_{os.getpid()}.pkl"),
            "label": f"chunk{c:02d}",
        })
    print(f"[parallel] {len(specs)} contract-pure chunks, {args.workers} concurrent: "
          + ", ".join(f"{s['label']}={len(s['positions'])}" for s in specs), flush=True)

    ctx = mp.get_context("spawn")
    running, pending, collected, failed = [], list(specs), {}, []
    while pending or running:
        while pending and len(running) < args.workers:
            spec = pending.pop(0)
            p = ctx.Process(target=_run_cluster_chunk_subprocess, args=(spec,), daemon=False)
            p.start()
            print(f"[parallel] started {spec['label']} pid={p.pid} "
                  f"({len(spec['positions'])} clusters)", flush=True)
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
            print(f"[parallel] finished {spec['label']} "
                  f"({len(collected)}/{n} clusters done so far)", flush=True)
    if failed:
        raise RuntimeError(f"parallel chunk(s) failed: {failed}")

    results, chart_stacks, fps = [None] * n, [None] * n, [None] * n
    for pos, (res, chart_stack, fp) in collected.items():
        results[pos] = res
        chart_stacks[pos] = chart_stack
        fps[pos] = fp
    return results, chart_stacks, fps


# --------------------------------------------------------------------------
# The p1_reacted dynamic filter (always computed, tags -- see the "Dynamic
# filters" convention in _apply_p1_reaction_filter's own docstring below):
# a single P1 (breakout) bar can
# be shared by several DIFFERENT P0 levels of the same type (dynamic_target's
# own "P1 shared by >=2 P0s" rule already leans on this) even when those P0s
# are too far apart in price to be confluence-clustered into one trade (see
# cluster_candidates -- that union-find only merges within
# +/-args.m5_confluence_points). Each such P0 still gets retested
# independently, at its own later time. If ANY P0 sharing that P1 -- not
# just the ones that clear ss_confl_min and become a trade in THIS report --
# already bounced all the way back to the opposite M5 level, the P1 group's
# "breakout continuation" thesis has already played out once, so a later
# trade on a DIFFERENT P0 under the same P1 is not a fresh, independent
# signal. Detecting that requires resolving EVERY raw P0 in the group (its
# own un-fine-tuned price is the entry, and the retest bar IS the touch by
# definition of `retests`, so there is no fill-window search here) with the
# exact same target/stop rules real trades use, not just the subset that
# happens to also be an SS-confl-qualifying candidate.
# --------------------------------------------------------------------------

def _naive_bracket_touch(bars, entry_adj, is_long, stop_pts, target_pts, offset):
    """Whichever of stop/target price is touched FIRST by a bar's own
    high/low, in bar order -- a raw 'did price get there' fact, unlike
    SR.resolve_trades' full fill-realism pipeline (which additionally
    requires TARGET, a resting limit order, to see a qualifying
    OPPOSITE-side print -- a same-side print merely brushing the price
    doesn't count there). The p1_reacted dynamic filter cares whether the market
    actually reacted off this P0 and ran to the opposite level, not
    whether a specific resting order would have filled there, so this
    intentionally skips that refinement. Returns 'target', 'stop', or
    'no_hit'."""
    raw_entry = entry_adj - offset
    highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    if is_long:
        stop_price, target_price = raw_entry - stop_pts, raw_entry + target_pts
        stop_hit, target_hit = lows <= stop_price, highs >= target_price
    else:
        stop_price, target_price = raw_entry + stop_pts, raw_entry - target_pts
        stop_hit, target_hit = highs >= stop_price, lows <= target_price
    s_idx = np.flatnonzero(stop_hit)
    t_idx = np.flatnonzero(target_hit)
    s0 = s_idx[0] if s_idx.size else None
    t0 = t_idx[0] if t_idx.size else None
    if t0 is not None and (s0 is None or t0 <= s0):
        return "target"
    if s0 is not None:
        return "stop"
    return "no_hit"


def _resolve_raw_retest(m5_ledger, row_d, args):
    """(outcome, touch_time) for one RAW 'departed' M5 level -- a clean
    retest OR a consumed_early death (see _seg_departed_levels) -- not
    fine-tuned, not fill-window-searched: row_d['touch_time'] itself is the
    touch. outcome is from _naive_bracket_touch (a raw OHLC touch race, no
    fill-quality requirement -- see its own docstring for why that differs
    from a real trade's own resolution). Returns (None, None) if no
    qualifying target/stop exists or there's no tick data to check
    against, same 'no trade' cases process_cluster itself would hit.
    _pick_targets/_dynamic_stop_m5 still pick the SAME bracket a real trade
    on this P0 alone would have used (including this run's own
    --target-mode) -- only the touch-vs-fill distinction differs."""
    level_type = row_d["type"]
    is_long = level_type == "LHPB"
    price = float(row_d["price"])
    touch_time = pd.Timestamp(row_d["touch_time"])
    if touch_time.tzinfo is None:
        touch_time = touch_time.tz_localize("UTC")

    targets = _pick_targets(m5_ledger, level_type, price, is_long, touch_time, args,
                            p1_time=row_d["breakout_time"], p2_time=touch_time)
    mode = _active_mode(targets, price, enabled=args.default_target_modes)
    if mode is None:
        return None, None
    target_price = targets[mode][0]
    stop_price, _, _ = _dynamic_stop_m5(
        level_type, price, is_long, row_d.to_dict(), m5_ledger, touch_time)
    if stop_price is None:
        return None, None
    stop_pts = abs(stop_price - price)
    target_pts = abs(target_price - price)
    if stop_pts <= 0:
        return None, None

    bars = SF.build_minute_bars(touch_time)
    if bars is None or bars.empty:
        return None, None
    offset, _ = R._offset_for_ts(touch_time)
    outcome = _naive_bracket_touch(bars, price, is_long, stop_pts, target_pts, offset)
    return outcome, touch_time


def _p1_group_reaction_cutoffs(relevant_keys, departed, args):
    """{(seg_idx, type, breakout_time): earliest reacting touch_time} for
    every relevant_keys entry whose FULL P1 group (every DEPARTED M5 level
    -- clean retest or consumed_early, see _seg_departed_levels -- sharing
    that type+breakout_time, including P0s below ss_confl_min or that
    never became a tradeable retest at all) has >=2 members. Walks each
    such group in touch_time order and resolves every member with
    _resolve_raw_retest until one reaches outcome=='target', which sets
    that group's cutoff; a group with no reacting member is absent from
    the returned dict (never filtered). Single-member groups have no
    OTHER P0 to react on their behalf, so they are skipped without ever
    touching tick data.

    `seg_idx` is kept in relevant_keys/the returned dict's key purely for
    the caller's own lookup convenience (_apply_p1_reaction_filter keys
    off it too) -- level detection itself is one continuous ledger now,
    so it is never used here to pick which ledger to query. A given
    breakout_time deterministically implies one seg_idx (R._contract_index_for
    of an absolute timestamp), so every relevant_keys entry sharing a
    (type, breakout_time) already shares the same seg_idx too -- deduping
    on the 2-tuple below is exact, not an approximation."""
    if departed is None or departed.empty:
        return {}
    keys = {(level_type, breakout_time) for _, level_type, breakout_time in relevant_keys}
    m5_ledger = LC.m5_levels(verbose=False)
    if m5_ledger is None or m5_ledger.empty:
        return {}

    cutoffs = {}
    seg_idx_by_key = {(level_type, breakout_time): seg_idx
                      for seg_idx, level_type, breakout_time in relevant_keys}
    for (level_type, breakout_time), group in departed.groupby(["type", "breakout_time"]):
        key2 = (level_type, pd.Timestamp(breakout_time))
        if key2 not in keys or len(group) < 2:
            continue
        group = group.sort_values("touch_time")
        for _, row_d in group.iterrows():
            outcome, touch_time = _resolve_raw_retest(m5_ledger, row_d, args)
            if outcome == "target":
                cutoffs[(seg_idx_by_key[key2], level_type, key2[1])] = touch_time
                break
    return cutoffs


def _apply_p1_reaction_filter(results, cutoffs):
    """Mutates and returns `results` in place.

    DYNAMIC FILTER CONVENTION -- read this before adding another one. This
    does NOT remove trades from the report or from the server-computed
    baseline stats. It only TAGS each originally-filled result whose own P1
    group (seg_idx, type, breakout_time) has a reaction cutoff STRICTLY
    earlier than its own retest_time -- i.e. a DIFFERENT P0 sharing that P1
    already reacted first -- by appending 'p1_reacted' to
    res['dyn_tags'] (a list; a row can carry more than one dynamic-filter
    tag) and setting res['p1_reaction_cutoff']. _render_row turns each tag
    into a `data-dyn-tags` attribute (plus data-r/data-pnl-pts/data-outcome
    for live recompute) on that row's <tr>, and the report's JS (see the
    'Dynamic filters' block appended to JS below _finish_report) lets the
    user toggle an Exclude or an Only (isolate) checkbox per tag IN THE
    BROWSER to hide/show those rows and recompute win rate / avg R /
    total R / total PnL live, with NO Python regen required. This is
    deliberately generic: to add a new dynamic filter, (1) tag qualifying
    results with one more entry in dyn_tags (their own detection logic,
    wherever that lives), (2) add one <label class="chip"> checkbox with
    class f-dyn-exclude and one more with class f-dyn-isolate, both
    data-tag="<your tag>", to the filter panel in _finish_report. Nothing
    else needs to change -- the JS's recomputeDynStats() is tag-agnostic."""
    for res in results:
        if not res["filled"]:
            continue
        row_d = res["row"]
        key = (res["seg_idx"], row_d["type"], pd.Timestamp(row_d["breakout_time"]))
        cutoff = cutoffs.get(key)
        if cutoff is not None and pd.Timestamp(row_d["retest_time"]) > cutoff:
            res.setdefault("dyn_tags", []).append("p1_reacted")
            res["p1_reaction_cutoff"] = cutoff
    return results


def _apply_globex_open_filter(results):
    """Mutates and returns `results` in place. Same dynamic-filter
    convention as _apply_p1_reaction_filter (see that function's docstring)
    -- tags, doesn't remove: a filled result whose actual fill
    (touch_time_alt) landed in [15:00, 15:05) Pacific time -- the daily
    Globex/ETH reopen (6pm ET), when the M5 stop/target structure this
    strategy trades against isn't reliable across the reopen gap -- gets
    'globex_eth_open' appended to res['dyn_tags']. Unlike p1_reacted's
    checkbox (unchecked by default, an open hypothesis), this one ships
    checked by default in _finish_report's filter panel: on the full
    [2026-07-01, 2026-08-31] dataset it tags 6 of 167 baseline trades, all
    6 losers (0 wins dropped) -- excluding them takes total R from -0.8 to
    +5.2, so the default view already reflects that."""
    for res in results:
        if not res["filled"]:
            continue
        if _in_globex_open_window(res["touch_time_alt"]):
            res.setdefault("dyn_tags", []).append("globex_eth_open")
    return results


N_COLS = 22  # keep in sync with `head` below and every colspan in this section


def _fail_reason_label(reason):
    if not reason:
        return ""
    if reason.startswith("r_below_"):
        return f"NO TRADE (R below {reason.split('_below_', 1)[1]})"
    return {
        "unfilled_within_window": "UNFILLED (entry never reached)",
        "eod_entry_blocked": "NO TRADE (entry blocked -- end of day)",
        "no_target": "NO TRADE (no target under either rule)",
        "no_tick_data_after_fill": "NO DATA after fill",
        "no_m5_stop": "NO TRADE (no qualifying M5 breakout-candle stop)",
        "degenerate_stop": "NO TRADE (degenerate stop)",
    }.get(reason, reason.replace("_", " "))


def _target_title(info):
    """Tooltip for the Target cell, per target source (see _pick_targets)."""
    if not info:
        return "no target info"
    area, level = info.get("area"), info.get("level")
    if info.get("src") == "m5_opposite":
        return (f"Newest eligible M5 {level['type']} P0: "
                f"{R._to_pt_str(level['formation_time'])}; shared P1: "
                f"{R._to_pt_str(level['breakout_time'])}")
    where = (f"Consolidation area (P1&hellip;P2) {R._to_pt_str(area['start_time'])} &rarr; "
             f"{R._to_pt_str(area['end_time'])} ({area['n_bars']} M5 bars, "
             f"{area['low']:.2f}-{area['high']:.2f}, ER {area['er']:.2f})")
    if info.get("src") == "consol_p0":
        return (f"{where}; still holds an untested M5 {level['type']} P0 "
                f"{float(level['price']):.2f} formed "
                f"{R._to_pt_str(level['formation_time'])} -- that P0 is the target")
    return f"{where}; no untested opposite P0 left inside it, so the target is its near edge"


def _compute_report_stats(filled):
    """(stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
    pctile_html) for a population of FILLED results -- the server-computed
    BASELINE (dynamic-filter-tagged trades, e.g. p1_reacted, are still
    counted here; excluding them is a live, client-side toggle -- see the
    'Dynamic filters' block in _finish_report's JS)."""
    stats = SF._stats_block(filled, "resolved", "r")
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
    return stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries, pctile_html


def render(args):
    # Which target rules start ticked (see _pick_targets). Normally set by
    # __main__ from --target-mode; defaulted here so an importing caller can
    # hand render() a plain namespace.
    if not getattr(args, "default_target_modes", None):
        args.default_target_modes = TARGET_MODES
    if not np.isfinite(args.min_r) or args.min_r < 0:
        raise ValueError("--min-r must be finite and non-negative")
    if not np.isfinite(args.max_alt_fill_hours) or args.max_alt_fill_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")

    candidates, departed = select_candidates(
        args.ss_confl_min, args.start, args.end, args.m5_confluence_points)
    radius = SR._fmt_pts(args.m5_confluence_points)
    print(f"{len(candidates)} M5 retests have SS Confl >= {args.ss_confl_min} "
          f"(+/-{radius}pt) in [{args.start}, {args.end}]", flush=True)
    if args.max_rows is not None:
        candidates = candidates[:args.max_rows]
        print(f"--max-rows: processing only the first {len(candidates)}", flush=True)

    clusters = cluster_candidates(candidates)
    n_merged = len(candidates) - len(clusters)
    if n_merged:
        print(f"{len(candidates)} candidate rows collapse into {len(clusters)} distinct "
              f"confluence clusters ({n_merged} duplicate row(s) merged)", flush=True)

    relevant_keys = set()
    for cluster in clusters:
        anchor_row = SF.cluster_anchor(cluster)["row"]
        relevant_keys.add((cluster[0]["seg_idx"], anchor_row["type"],
                          pd.Timestamp(anchor_row["breakout_time"])))
    print(f"p1_reacted: checking {len(relevant_keys)} distinct P1 group(s) "
          f"for an already-reacted sibling P0...", flush=True)
    p1_cutoffs = _p1_group_reaction_cutoffs(relevant_keys, departed, args)

    results, chart_stacks, fps = process_clusters(clusters, args)
    results = _apply_p1_reaction_filter(results, p1_cutoffs)
    n_tagged = sum(1 for r in results if "p1_reacted" in r.get("dyn_tags", ()))
    print(f"p1_reacted: {len(p1_cutoffs)} P1 group(s) had an already-reacted "
          f"sibling P0; {n_tagged} trade(s) tagged 'p1_reacted' (still shown/counted by "
          f"default -- toggle the Dynamic filters checkbox in the report to exclude "
          f"them)", flush=True)
    results = _apply_globex_open_filter(results)
    tag_counts = {}
    for r in results:
        if not r["filled"]:
            continue
        # Row-level tags plus the ACTIVE target rule's own (r_below_min and
        # eod_flat live per rule now -- see _resolve_target_mode).
        tags = list(r.get("dyn_tags", ())) + r["modes"][r["active_mode"]]["dyn_tags"]
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for tag, n in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
        if tag == "p1_reacted":
            continue  # already reported above, with its own P1-group detail
        print(f"{n} filled trade(s) tagged '{tag}' -- toggle its Dynamic filters checkbox "
              f"in the report to include/exclude it", flush=True)

    filled = [r for r in results if r["filled"]]
    skipped = [r for r in results if not r["filled"]]
    reason_counts = {}
    for r in skipped:
        reason_counts[r.get("fail_reason", "?")] = reason_counts.get(r.get("fail_reason", "?"), 0) + 1
    stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries, pctile_html = \
        _compute_report_stats(filled)

    charts = []
    rows_html = []
    for idx, res in enumerate(results):
        chart_entry, row_html = _render_row(idx, res, chart_stacks, fps)
        charts.append(chart_entry)
        rows_html.append(row_html)

    _finish_report(args, results, clusters, candidates, filled, skipped, reason_counts,
                   stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
                   pctile_html, rows_html, charts, radius, args.output)


GAP_FLAG_HTML = ('<span class="gap-flag" title="Entry price never traded between touch '
                 'and exit -- price gapped through the level, so this fill was not '
                 'actually available.">⚠</span>')


def _mode_badges(res, mode):
    """The badges belonging to ONE target rule's own outcome: its sub-min-R
    tag, its end-of-day flat tag, and its trade-management summary. All three
    differ between the rules on the same row, so they are swapped along with
    the rest of the cells (see _mode_payload)."""
    m = res["modes"][mode]
    out = ""
    if "r_below_min" in m["dyn_tags"]:
        out += (f'<span class="dyn-tag-badge" title="Dynamic filter '
                f'‘r_below_min’: the bracket on offer just before entry was only '
                f'R {m["r_multiple"]:.2f}. The trade is still shown and resolved; it is left '
                f'out of the headline stats by default.">R &lt; MIN</span>')
    if "eod_flat" in m["dyn_tags"]:
        out += ('<span class="dyn-tag-badge" title="This trade was still open at '
                '12:44 PT and was flattened at market under the end-of-day rule '
                '(trade_management.py rule 3) instead of reaching its stop or '
                'target.">EOD FLAT</span>')
    mgmt = m["mgmt"] or {}
    if mgmt.get("fired"):
        bits = []
        if mgmt.get("trail_events"):
            bits.append(f"stop trailed x{len(mgmt['trail_events'])}")
        if mgmt.get("rr_floor_fired"):
            bits.append("RR-floor exit")
        mgmt_r_str = f"{mgmt['r']:+.2f}R" if mgmt.get("r") is not None else "?"
        out += (f'<span class="dyn-tag-badge mgmt-tag-badge" '
                f'title="Trade management ({", ".join(bits)}) would change this '
                f'trade to {mgmt_r_str} ({mgmt.get("outcome")}). Toggle the Trade '
                f'management checkbox in the panel above to use it in the summary '
                f'stats.">MGMT {mgmt_r_str}</span>')
    return out


def _mode_payload(res, mode):
    """Everything about a row that CHANGES when it switches target rule, as
    one JSON-able blob per rule: the cell HTML the browser swaps in, and the
    numbers recomputeDynStats() reads back off the row afterwards. The stop,
    the entry and the fill are rule-independent and are never touched.

    'dist' is what decides which rule wins when both are on (the FARTHEST
    target -- see _active_mode; the browser applies the same rule)."""
    m = res["modes"][mode]
    resolved = m["resolved"]
    mgmt = m["mgmt"] or {}
    info = m["target_info"] or {}
    outcome_label, outcome_cls = SF._outcome_label(resolved)
    r_val = resolved.get("r")
    pnl_pts = (r_val * res["stop_pts"]) if r_val is not None else None
    exit_px = resolved.get("exit_price")

    def fmt(v):
        return f"{v:.2f}" if v is not None else "-"

    return {
        "dist": m["target_pts"],
        "tgt": (f'{m["target_price"]:.2f}<span class="src-tag m5">'
                f'{info.get("src", "m5_opposite")}</span>'),
        "tgtTitle": _target_title(info),
        "rr": f'{m["r_multiple"]:.2f}',
        "outcomeLabel": outcome_label,
        "outcomeCls": outcome_cls,
        "modeBadges": _mode_badges(res, mode),
        "exit": (R._to_pt_str(resolved["exit_time"])
                 if resolved.get("exit_time") is not None else "-"),
        "exitPx": (f"{exit_px:.2f}" if resolved.get("outcome") != "no_hit"
                   and exit_px is not None else "-"),
        "pnl": format(pnl_pts, "+.2f") if pnl_pts is not None else "-",
        "pnlCls": ("good" if (pnl_pts is not None and pnl_pts > 0)
                   else ("bad" if (pnl_pts is not None and pnl_pts < 0) else "")),
        "mae": fmt(resolved.get("adverse_pts")),
        "mfe": fmt(resolved.get("favorable_pts")),
        "gb": fmt(resolved.get("giveback_pts")),
        "gap": GAP_FLAG_HTML if resolved.get("entry_gapped") else "",
        "r": "" if r_val is None else f"{r_val:.6f}",
        "pnlPts": "" if pnl_pts is None else f"{pnl_pts:.6f}",
        "outcome": resolved.get("outcome") or "",
        "mgmtR": "" if mgmt.get("r") is None else f"{mgmt['r']:.6f}",
        "mgmtPnl": "" if mgmt.get("pnl_pts") is None else f"{mgmt['pnl_pts']:.6f}",
        "mgmtOutcome": mgmt.get("outcome") or "",
        "mgmtFired": "1" if mgmt.get("fired") else "0",
        "tags": " ".join(m["dyn_tags"]),
    }


def _attr_json(obj):
    """`obj` as JSON safe to sit inside a double-quoted HTML attribute."""
    return (json.dumps(obj).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _render_row(idx, res, chart_stacks, fps):
    """Builds (chart_entry_for_json, row_html) for one result row. A FILLED
    row carrying dynamic-filter tags (res['dyn_tags'], e.g. 'p1_reacted' --
    see _apply_p1_reaction_filter's own docstring for the full convention)
    gets a data-dyn-tags attribute plus data-r/data-pnl-pts/data-outcome
    and a small badge, so the report's own JS can hide/show it and
    recompute win rate / avg R / total R / total PnL live from a filter
    checkbox -- no Python regen needed to explore excluding it."""
    if True:
        row_d = res["row"]
        level_type = res["level_type"]
        type_cls = "type-lhpb" if res["is_long"] else "type-llpb"
        retest_str = R._to_pt_str(row_d["retest_time"])
        members_str = ", ".join(f"{p:.2f}" for p in res["cluster_members"])
        if res.get("cluster_size", 1) > 1:
            entry_title = (f' title="{res["cluster_size"]} mutually-confluent M5 levels '
                           f'merged into this one trade: {members_str}"')
            own_cell = f'<span class="cluster-tag"{entry_title}>{res["own_price"]:.2f}\u2020</span>'
        else:
            own_cell = f'{res["own_price"]:.2f}'

        if not res["filled"]:
            # No trade, for whatever reason -- still a row the user wants to
            # review, not a dead end: every column is shown (real value where
            # process_cluster actually got far enough to compute one, '-'
            # where it never could), the row is still clickable for its
            # charts (chart_stacks[idx]/fps[idx] come from
            # _build_unfilled_chart_stack, not None), and the review
            # checkboxes/notes still work. Only .unfilled-row's CSS (faint
            # + italic) marks it as not-a-trade -- nothing here is disabled.
            reason = res.get("fail_reason", "")
            rr_note = (f' (R {res["r_multiple"]:.2f})' if reason.startswith("r_below_")
                       and res.get("r_multiple") is not None else "")
            row_key = f"{level_type}_{res['alt_price']:.2f}_{retest_str}".replace(" ", "_")
            touch_time_alt = res.get("touch_time_alt")
            entry_touch_str = R._to_pt_str(touch_time_alt) if touch_time_alt is not None else "-"
            alt_cell = (f'{res["alt_price"]:.2f}'
                       f'<span class="src-tag {res["alt_source"]}">{res["alt_source"]}</span>')
            if res.get("stop_price") is not None:
                stop_cell = (f'{res["stop_price"]:.2f}'
                            f'<span class="src-tag m5">{res.get("stop_source", "")}</span>')
            else:
                stop_cell = "-"

            chart_stack, fp = chart_stacks[idx], fps[idx]
            fp_narrow_html = fp.get("narrow")
            fp_wide_html = fp.get("wide")
            fp_section = (
                f'<div class="chart-row-2col footprint-outer-row">'
                f'<div class="footprint-pair">'
                f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
                f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
                f'</div></div>'
            )

            row_html = f"""
<tr class="lvl-row unfilled-row {type_cls}" data-idx="{idx}" data-key="{row_key}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td>{alt_cell}</td>
  <td class="left">{entry_touch_str}</td>
  <td>{stop_cell}</td>
  <td class="tgt-cell">-</td>
  <td class="rr-cell">-</td>
  <td class="outcome-cell"><span class="outcome-label">{_fail_reason_label(reason)}{rr_note}</span></td>
  <td class="left exit-cell">-</td><td class="exitpx-cell">-</td>
  <td class="pnl-cell">-</td>
  <td class="mae-cell">-</td><td class="mfe-cell">-</td><td class="gb-cell">-</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb"></td>
  <td class="replayed-cell" onclick="event.stopPropagation();"><input type="checkbox" class="replayed-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">▶</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-row-2col chart-row-solo">
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
</tr>"""
            return chart_stack, row_html

        # One payload per target rule that found a target: the page ships
        # every rule's outcome and the browser swaps between them live (see
        # the 'Target rules' block in JS). The server renders the ACTIVE
        # rule's cells, so the page is correct before any JS runs.
        payloads = {mode: _mode_payload(res, mode) for mode in res["modes"]}
        active_mode = res["active_mode"]
        act = payloads[active_mode]
        entry_touch_str = R._to_pt_str(res["touch_time_alt"])
        src_cls = f"src-tag {res['alt_source']}"
        improved_flag = " &uarr;" if res["improved"] else ""
        stop_level = res["stop_m5_level"]
        stop_source = res.get("stop_source", "m5_thrust")
        if stop_source == "m5_p0_spike":
            p0_extreme = "p0_low" if res["is_long"] else "p0_high"
            spike_kind = "hammer" if res["is_long"] else "shooting star"
            stop_title = (f"Entry level's OWN P0 was a spike candle ({spike_kind}): "
                         f"{stop_level['type']} {stop_level['price']:.2f}, "
                         f"P0 {R._to_pt_str(stop_level['formation_time'])}, "
                         f"P0 {p0_extreme.replace('p0_', '')} {stop_level[p0_extreme]:.2f}; "
                         f"one tick {'below' if res['is_long'] else 'above'} THAT candle "
                         f"(not the P1 thrust candle)")
        else:
            extreme = "breakout_low" if res["is_long"] else "breakout_high"
            stop_title = (f"Live M5 {stop_level['type']} {stop_level['price']:.2f}, "
                         f"P0 {R._to_pt_str(stop_level['formation_time'])}; "
                         f"P1 {R._to_pt_str(stop_level['breakout_time'])}, "
                         f"{extreme} {stop_level[extreme]:.2f}; one tick "
                         f"{'below' if res['is_long'] else 'above'}")
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

        row_key = f"{level_type}_{res['alt_price']:.2f}_{entry_touch_str}".replace(" ", "_")

        # Row-level tags (the same under every target rule) vs the active
        # rule's own tags: the browser recombines the two halves when it
        # switches rule, so they ship separately.
        base_tags = list(res.get("dyn_tags") or [])
        dyn_tags = base_tags + res["modes"][active_mode]["dyn_tags"]
        dyn_tags_attr = " ".join(dyn_tags)
        base_tags_attr = " ".join(base_tags)
        dyn_badges = ""
        if "p1_reacted" in dyn_tags:
            cutoff_str = R._to_pt_str(res["p1_reaction_cutoff"])
            dyn_badges += (f'<span class="dyn-tag-badge" title="Dynamic filter ‘p1_reacted’: '
                          f'a different P0 sharing this trade’s own P1 already reacted to its '
                          f'opposite M5 level at {cutoff_str}. Toggle the Dynamic filters checkbox '
                          f'in the panel above to exclude this trade.">P1 REACTED</span>')
        if "globex_eth_open" in dyn_tags:
            dyn_badges += (f'<span class="dyn-tag-badge" title="Dynamic filter '
                          f'‘globex_eth_open’: this fill landed at {entry_touch_str}, inside the '
                          f'daily Globex/ETH reopen window (15:00-15:05 PT) -- excluded by default. '
                          f'Toggle the Dynamic filters checkbox in the panel above to include '
                          f'it.">GLOBEX OPEN</span>')
        if "low_liquidity" in dyn_tags:
            dyn_badges += (f'<span class="dyn-tag-badge liq-tag-badge" title="Dynamic filter '
                          f'‘low_liquidity’: the tape was measurably illiquid at this fill '
                          f'({LQ.describe(res.get("liquidity"))}) -- a news/thin-book window. '
                          f'The strategy skips these; they are left out of the headline stats by '
                          f'default.">NEWS / THIN</span>')
        if "swerved" in dyn_tags:
            sw = res["swerve"]
            sw_str = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
            dyn_badges += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                          f'‘swerved’: a confirmed M5 swing sat on the planned entry '
                          f'{sw["planned_price"]:.2f} ({sw_str}), so the order was moved to the '
                          f'next live M5 {level_type} level at {sw["price"]:.2f}.">'
                          f'SWERVED {sw["planned_price"]:.2f}&rarr;{sw["price"]:.2f}</span>')
        if "swerve_blocked" in dyn_tags:
            sw = res["swerve"]
            sw_str = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
            dyn_badges += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                          f'‘swerve_blocked’: a confirmed M5 swing sat on the planned entry '
                          f'{sw["planned_price"]:.2f} ({sw_str}) and no other live M5 {level_type} '
                          f'level was available to move to, so this trade is NOT taken. It is '
                          f'shown at its original entry so it can still be reviewed, and left out '
                          f'of the headline stats by default.">SWERVE BLOCKED</span>')
        modes_attr = _attr_json(payloads)

        chart_stack, fp = chart_stacks[idx], fps[idx]
        fp_narrow_html = fp.get("narrow")
        fp_wide_html = fp.get("wide")
        fp_section = (
            f'<div class="chart-row-2col footprint-outer-row">'
            f'<div class="footprint-pair">'
            f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
            f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
            f'</div></div>'
        )

        row_html = f"""
<tr class="lvl-row {type_cls}" data-idx="{idx}" data-key="{row_key}"
    data-dyn-tags="{dyn_tags_attr}" data-base-tags="{base_tags_attr}"
    data-r="{act['r']}" data-pnl-pts="{act['pnlPts']}" data-outcome="{act['outcome']}"
    data-mgmt-r="{act['mgmtR']}" data-mgmt-pnl-pts="{act['mgmtPnl']}"
    data-mgmt-outcome="{act['mgmtOutcome']}" data-mgmt-fired="{act['mgmtFired']}"
    data-mode="{active_mode}" data-modes="{modes_attr}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td>{res['alt_price']:.2f}<span class="gap-slot">{act['gap']}</span><span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td title="{stop_title}">{res['stop_price']:.2f}<span class="src-tag m5">{stop_source}</span></td>
  <td class="tgt-cell" title="{act['tgtTitle']}">{act['tgt']}</td>
  <td class="rr-cell">{act['rr']}</td>
  <td class="outcome-cell {act['outcomeCls']}"><span class="outcome-label">{act['outcomeLabel']}</span><span class="row-badges">{dyn_badges}</span><span class="mode-badges">{act['modeBadges']}</span></td>
  <td class="left exit-cell">{act['exit']}</td><td class="exitpx-cell">{act['exitPx']}</td>
  <td class="pnl-cell {act['pnlCls']}">{act['pnl']}</td>
  <td class="mae-cell bad">{act['mae']}</td><td class="mfe-cell good">{act['mfe']}</td><td class="gb-cell">{act['gb']}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb"></td>
  <td class="replayed-cell" onclick="event.stopPropagation();"><input type="checkbox" class="replayed-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-row-2col chart-row-solo">
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
</tr>"""
        return chart_stack, row_html


def _finish_report(args, results, clusters, candidates, filled, skipped, reason_counts,
                   stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
                   pctile_html, rows_html, charts, radius, output_path,
                   title_suffix="", storage_suffix=""):
    peg_lead_sentence = (
        f"Entry is a pegged/chasing limit order (re-quotes {args.peg_step:.2f}pt closer to "
        f"market, up to {args.peg_cap:.2f}pt total, on every wrong-side touch of the resting "
        f'price -- see the &quot;chased&quot; badge when this differs from the original '
        f"fine-tuned price). A replacement takes effect on the next tick record and fills "
        f"against the opposing bid/ask if marketable; otherwise it rests at its new limit. "
        f"Queue position and additional cancel/replace latency are not modeled."
        if args.pegged_entry else
        "Entry is a plain static limit order (no chasing).")
    target_lead_sentence = (
        f"TARGET = two rules, both reading only what the market built BETWEEN THIS LEVEL'S OWN "
        f"P1 AND P2 (wholly after its breakout candle, wholly before its retest bar) and both "
        f"bounded to {MIN_DYNAMIC_TARGET_PTS:g}&ndash;{MAX_DYNAMIC_TARGET_PTS:g} points from "
        f"the fill on the favourable side. (1) CONSOLIDATION AREA: the nearest congestion area "
        f"in that window (a run of &ge;{args.consol_min_bars} M5 bars inside a "
        f"&le;{args.consol_max_height:g}pt band with efficiency ratio &lt; "
        f"{args.consol_max_er:g}) -- the untested opposite-type M5 P0 still sitting inside it "
        f"(src tag consol_p0) if there is one, else the area's near edge, its low for a long "
        f"and its high for a short (src tag consol_edge). (2) OPPOSITE M5 LEVEL: the most "
        f"recently formed live opposite-type M5 level whose P1 candle broke at least two "
        f"distinct same-type P0s (src tag m5_opposite). Both are precomputed for every trade "
        f"and both are live checkboxes in the Target rules row of the panel above: untick one "
        f"and every row re-resolves against the other, or becomes a dimmed NO TARGET row if "
        f"that was its only one. With both ticked (the default here: "
        f"{' + '.join(args.default_target_modes)}) a trade takes whichever rule offers the "
        f"FARTHER target.")
    reason_html = "".join(
        f'<div class="box"><strong>{n}</strong>{reason}</div>'
        for reason, n in sorted(reason_counts.items(), key=lambda kv: -kv[1]))
    total_pnl_pts = sum(r["resolved"]["r"] * r["stop_pts"] for r in filled
                       if r["resolved"].get("r") is not None)
    summary_html = f"""
<div class="summary">
  <div class="box"><strong>{len(candidates)}</strong>SS Confl &ge; {args.ss_confl_min}</div>
  <div class="box"><strong>{len(clusters)}</strong>confluence clusters</div>
  <div class="box"><strong>&plusmn;{radius}pt</strong>M5 confluence radius</div>
  <div class="box"><strong>{args.min_r:.2f}</strong>min R (below: tagged, not skipped)</div>
  <div class="box"><strong id="sum-avg-r">{stats['avg_r']:.2f}</strong>avg R</div>
  <div class="box"><strong>{improved_n}</strong>/{len(filled)} entry improved over own level</div>
  <div class="box"><strong>{gapped_entries}</strong>gapped entry</div>
  {reason_html}
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
"""
    # The strategy write-up and the excursion percentiles are reference
    # material, not something to scroll past on the way to the trades, so they
    # live in their own tab (see SR.tab_bar_html / SR.TABS_JS).
    pctile_tab_html = f"""
<p class="lead">M5-native strategy: the trade signal, entry, stop and target are ALL M5 LXPB
structure -- there is no H1 level anywhere in this report. SELECT: every M5 LXPB retest in
[{args.start}, {args.end}] with SAME-SIDE M5 confluence (other M5 levels of the SAME type,
confirmed-broken-out and not yet retested as of the subject's own P1 breakout bar) &ge;
{args.ss_confl_min}, radius &plusmn;{radius}pt. Mutually-confluent M5 levels swept by the
same bar are merged into one trade (Merged M5 levels column). ENTRY (refined AT THE BREAKOUT
BAR): the most extreme price (highest for LLPB/short, lowest for LHPB/long) among the
cluster's own member prices and every member's own same-side M5 confluence pool, all still
unconsumed immediately before the cluster's own retest (Own column; the src tag on Entry
shows own/m5). {peg_lead_sentence} STOP: if the level that supplied the entry was ITSELF
formed on a spike candle (lxpb.py's own is_spike -- a hammer for an LHPB / shooting star for
an LLPB), one tick beyond THAT P0 candle's own low/high (src tag m5_p0_spike) -- that
candle's own wick is a sharper invalidation point than the breakout candle for these trades.
Otherwise (src tag m5_thrust), one tick above the HIGHEST breakout-candle high
for LLPB shorts, or one tick below the LOWEST breakout-candle low for LHPB longs (i.e. above/
below the thrust candle), among live same-side M5 levels within
&plusmn;{DYNAMIC_STOP_RADIUS_PTS:g} points of the actual fill. {target_lead_sentence} NO FALLBACKS: if no qualifying
stop or target exists there is no trade at all (see the summary boxes above for the
skip-reason breakdown in the Trades tab) -- this differs from render_ss_confl_finetune_report.py, whose
H1-anchored strategy always falls back to a fixed stop/target. A bracket below R
{args.min_r:g} IS taken, resolved and charted, tagged r_below_min and excluded from the
headline stats by default. ENTRY AND TRADE BLOCKS: a confirmed M5 swing within
{args.swerve_tol_pts:g}pt of the planned entry, formed between the level's own P1 breakout and
its retest, moves the order to the next live same-side M5 level within
{args.swerve_max_move_pts:g}pt (SWERVED), or blocks the trade if there is none
(SWERVE BLOCKED, shown at the original entry); a fill whose preceding
{args.liq_window_minutes:g} minutes of tape show more than
{args.liq_wide_spread_share * 100:g}% of quotes wider than one tick is a news/thin-book entry
(NEWS / THIN); and no position is carried past the end of the day -- open trades are flattened
at market before 12:45 PT (EOD FLAT) and no entry is taken from then until the Globex reopen. Both searches use only completed M5 candles and the live ledger state
immediately before the fill's M5 bar, with no fixed lookback. Charts/markers/price-lines/
tooltips, MAE/MFE/Max DD definitions, and the Reviewed/Valid/Replayed/Notes columns below all
follow render_stop_target_report.py's own conventions exactly (see that module and
render_ss_confl_finetune_report.py for full detail). See the Dynamic filters row of the
Trades tab for trade-exclusion toggles you can flip live in the browser, no regen required.</p>
{pctile_html}
"""

    # Trade-level numbers, printed against the table they describe. Every one
    # of them is rewritten by recomputeDynStats as the dynamic filters, target
    # rules and trade-management toggle change which rows count, so what the
    # strip shows always matches the rows on screen.
    stats_bar_html = SR.trade_stats_bar_html([
        (f"{stats['n']}", "trades taken", "sum-trades", False),
        (f"{stats['win_rate']:.1f}%", "win rate", "sum-win-rate", True),
        (f"{stats['wins']}", "wins", "sum-wins", False),
        (f"{stats['losses']}", "losses", "sum-losses", False),
        (f"{stats['total_r']:.1f}", "total R", "sum-total-r", False),
        (f"{total_pnl_pts:+.1f}", "total PnL (pts)", "sum-total-pnl", False),
        (f"{max_win_mae:.2f}", "max MAE (win)", "sum-max-win-mae", False),
        (f"{max_loss_mfe:.2f}", "max MFE (loss)", "sum-max-loss-mfe", False),
    ])

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
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser trade-exclusion toggles -- no Python regen
needed. Checking Exclude hides those rows AND recomputes win rate / avg R / total R / total PnL
above from only the remaining (not excluded) trades. Checking Only instead hides every OTHER
row (any Only checked takes priority over every Exclude box, and multiple Only boxes union
together). To add another dynamic filter: tag qualifying results with an entry in
res['dyn_tags'] (Python side) and add one more Exclude/Only checkbox pair here with class
f-dyn-exclude/f-dyn-isolate and data-tag matching that tag -- see _apply_p1_reaction_filter's
docstring in render_m5_confl2_report.py for the full convention.">Dynamic filters</span>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="p1_reacted">
      Exclude P1-already-reacted</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="p1_reacted">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="globex_eth_open" checked>
      Exclude Globex/ETH open fills (15:00-15:05 PT)</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="globex_eth_open">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="r_below_min" checked>
      Exclude R below __MIN_R__</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="r_below_min">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="low_liquidity" checked>
      Exclude news / thin-book entries</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="low_liquidity">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="swerve_blocked" checked>
      Exclude swerve-blocked (not taken)</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="swerve_blocked">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="swerved">
      Exclude swerved entries</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="swerved">
      Only</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="eod_flat">
      Exclude end-of-day flats</label>
    <label class="chip chip-iso"><input type="checkbox" class="f-dyn-isolate" data-tag="eod_flat">
      Only</label>
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Which TARGET RULE each trade exits on -- live, in the
browser, with no Python regen. Every trade ships with BOTH rules' brackets and outcomes
precomputed, so unticking a rule re-resolves each row against whatever the other rule offered:
a trade can become a NO TARGET row (dimmed, dropped from the stats) if the rule it was using
was the only one that found a target. With both ticked, each trade uses whichever rule offers
the FARTHER target. Untick both and no trade has a target at all. The charts below always draw
the bracket the page was GENERATED with -- regen to chart a different default.">Target rules</span>
    <label class="chip"><input type="checkbox" class="f-target-mode" data-mode="consolidation" __CONSOL_CHECKED__>
      Consolidation area (P1&hellip;P2)</label>
    <label class="chip"><input type="checkbox" class="f-target-mode" data-mode="opposite-m5" __OPP_CHECKED__>
      Opposite M5 level (P1&hellip;P2)</label>
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser toggle for the plug-n-play trade-management
rules in trade_management.py (symmetric across direction): rule 1 trails the stop to one tick
beyond a qualifying M5 breakout candle's own extreme (below the low for a long, above
the high for a short) once it closes past enough still-live sibling P0s; rule 2 exits at market whenever remaining reward/remaining risk (using
whatever the CURRENT stop is, post-trail) drops to 0.2 or below. Every filled trade already has
both baseline and managed outcomes precomputed -- this checkbox swaps win rate / avg R / total R
/ total PnL above to the managed numbers instantly, no regen required. Rows where management
actually changed the outcome carry an 'MGMT +-N.NNR' badge next to Outcome regardless of whether
the box is checked.">Trade management</span>
    <label class="chip"><input type="checkbox" id="mgmt-thrust-trail">
      Apply thrust-trail + RR-floor management</label>
  </div>
</div>
"""

    target_col_title = (
        "Whichever target rule is ticked in the Target rules row above, and with both ticked "
        "the one offering the FARTHER target. consol_p0: an untested opposite-type M5 P0 still "
        "sitting inside a consolidation area built between this level's own P1 and P2. "
        "consol_edge: that area's near edge instead -- low for a long, high for a short. "
        "m5_opposite: the newest live opposite M5 level sharing its P1 with another P0, formed "
        "in the same P1..P2 window.")
    head = (f"<th class=\"left\">#</th><th class=\"left\">Type</th>"
            f"<th class=\"left\">M5 retest</th>"
            f"<th class=\"left\" title=\"Distinct M5 prices merged into this trade, "
            f"extreme-first: highest for LLPB, lowest for LHPB. "
            f"Single-level trades show their own M5 price.\">Merged M5 levels</th>"
            f"<th title=\"The level's original M5 entry price, before fine-tuning to the "
            f"confluence group's extreme price\">Own</th><th>Entry</th>"
            f"<th class=\"left\">Entry (touch) time</th>"
            f"<th title=\"If the entry level's own P0 was a spike candle (hammer for LHPB / "
            f"shooting star for LLPB), one tick beyond THAT candle's own low/high "
            f"(m5_p0_spike). Otherwise, one tick beyond the live same-side M5 breakout-candle "
            f"extreme (m5_thrust); +/-{DYNAMIC_STOP_RADIUS_PTS:g}pt level search. "
            f"No trade if neither qualifies\">Stop</th>"
            f"<th title=\"{target_col_title}; "
            f"no trade if none qualifies\">Target</th>"
            f"<th title=\"Reward:risk on offer for THIS trade's own bracket at entry "
            f"(target pts / stop pts) -- fixed once entry/stop/target are picked, independent "
            f"of whether the trade goes on to win or lose. Below {args.min_r:g} the trade is "
            f"still taken and shown, tagged r_below_min and excluded from the headline stats "
            f"by default\">R</th>"
            f"<th>Outcome</th><th class=\"left\">Exit time</th><th>Exit px</th>"
            f"<th title=\"Realized profit/loss in points (signed): +target pts on a win, "
            f"-stop pts on a loss\">PnL</th>"
            f"<th>MAE (win)</th><th>MFE (loss)</th><th>Max DD</th>"
            f"<th>Reviewed</th><th>Valid</th><th>Replayed</th>"
            f"<th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")

    storage_key = f"lxpb_m5_confl{args.ss_confl_min}_review_v1{storage_suffix}"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>M5-native SS Confl report{title_suffix}</title>
{CSS}
</head><body>
<h1>M5-native SS Confl. &ge; {args.ss_confl_min} strategy report{title_suffix}</h1>
{SR.tab_bar_html([("trades", "Trades"), ("pctile", "Excursion percentiles")])}
<div class="tab-panel" id="tab-trades">
{summary_html}
{filter_panel.replace("__MIN_R__", f"{args.min_r:g}")
   .replace("__CONSOL_CHECKED__",
            "checked" if "consolidation" in args.default_target_modes else "")
   .replace("__OPP_CHECKED__",
            "checked" if "opposite-m5" in args.default_target_modes else "")}
{stats_bar_html}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{head}</tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table></div>
</div>
<div class="tab-panel tab-hidden" id="tab-pctile">
{pctile_tab_html}
</div>
{JS.replace("__CHARTS_JSON__", json.dumps(charts))
   .replace("__STORAGE_KEY__", storage_key)}
{SR.TABS_JS}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nSaved -> {output_path}")
    print(f"{stats['n']} trades taken, win rate {stats['win_rate']:.1f}%, "
          f"avg_R {stats['avg_r']:.2f}, total_R {stats['total_r']:.1f}")
    print(f"Skipped ({len(skipped)}): " +
          ", ".join(f"{reason}={n}" for reason, n in sorted(reason_counts.items())))


CSS = SR.CSS + """
<style>
.src-tag { font-size:0.75em; color:var(--text-dim); margin-left:4px; }
.src-tag.m5 { color:#38bdf8; }
.src-tag.own { color:var(--text-dim); }
.src-tag.chase { color:#f59e0b; margin-left:6px; cursor:help; }
.unfilled-row td { color:var(--text-faint); font-style:italic; }
tr.lvl-row.type-lhpb td.type-cell, tr.lvl-row.type-llpb td.type-cell {
  color:var(--text); font-weight:normal;
}
.cluster-tag { border-bottom:1px dotted var(--text-dim); cursor:help; }
td.merged-h1-levels { max-width:220px; white-space:normal; }
/* Dynamic filters (see _apply_p1_reaction_filter's docstring): a row tagged
   res['dyn_tags'] gets data-dyn-tags plus this badge; the matching
   f-dyn-exclude checkbox hides it via .dyn-hidden (kept separate from the
   review-workflow filters' own .hidden class so the two systems never
   fight over one class -- either one hides the row, independently). */
tr.lvl-row.dyn-hidden, tr.chart-row.dyn-hidden { display:none !important; }
.dyn-tag-badge { display:inline-block; margin-left:6px; padding:1px 6px; font-size:0.72em;
                border-radius:3px; background:#4a3010; color:#fbbf24; cursor:help; }
.chip-iso { margin-left:-4px; opacity:0.8; font-size:0.9em; }
/* This strategy has no H1 pane -- the M5 pane is the only thing in its own
   chart-row-2col row (see build_chart_stack_for_row), so it should fill
   the row instead of sitting in the grid's first 1fr column with an empty
   second column beside it. */
.chart-row-2col.chart-row-solo { grid-template-columns: 1fr; }
.mgmt-tag-badge { background:#0e3a4a; color:#67e8f9; }
/* A row whose target rule is switched off in the browser (see applyTargetModes
   in JS): still listed, still clickable for its charts, but it is not a trade
   under the rules currently ticked, so it is dimmed and left out of the
   stats -- the same treatment an unfilled row gets from Python. */
tr.lvl-row.no-target-row td { color:var(--text-faint); font-style:italic; }
.liq-tag-badge { background:#3f1d2e; color:#fda4af; }
.swerve-tag-badge { background:#1e3a2f; color:#86efac; }
/* Notes box: this report is reviewed with long, written-out notes per trade,
   so it ships far larger than the shared 160x34 default in
   render_stop_target_report.CSS (left alone, for every other report) and
   resizes in both directions rather than only vertically. */
textarea.trade-note { width:360px; height:150px; resize:both; }
</style>
"""
JS = SR.JS + """
<script>
// ---------------------------------------------------------------------
// Dynamic filters -- see _apply_p1_reaction_filter's own docstring in
// render_m5_confl2_report.py for the full authoring convention (this is
// the intentionally-generic, tag-agnostic half of it). Each
// f-dyn-exclude/f-dyn-isolate checkbox's data-tag names a tag a
// Python-side filter may have added to a row's data-dyn-tags
// (space-separated -- a row can carry more than one). Checking an
// Exclude box hides every row carrying that tag; checking an Isolate
// ("Only") box instead hides every row NOT carrying that tag (any
// Isolate box checked takes priority over every Exclude box, and
// multiple checked Isolate boxes union together) -- both act via the
// SAME .dyn-hidden class, kept deliberately separate from the
// review-workflow filters' .hidden class above (applyReviewFilters, in
// the shared JS) so the two systems never fight over one class; a row is
// invisible if EITHER is set -- and recomputes the win rate / avg R /
// total R / total PnL summary boxes from only the remaining (not
// hidden) trades. To add another dynamic filter: tag qualifying results
// with one more entry in res['dyn_tags'] (Python side) and add one more
// <input class="f-dyn-exclude" data-tag="..."> / <input
// class="f-dyn-isolate" data-tag="..."> checkbox pair to the filter
// panel -- recomputeDynStats() below needs no changes for a new tag, it
// reads whatever tags are present.
// ---------------------------------------------------------------------
function activeDynExcludeTags() {
  return Array.from(document.querySelectorAll('.f-dyn-exclude:checked')).map(cb => cb.dataset.tag);
}
function activeDynIsolateTags() {
  return Array.from(document.querySelectorAll('.f-dyn-isolate:checked')).map(cb => cb.dataset.tag);
}
// ---------------------------------------------------------------------
// Target rules -- the OTHER kind of live toggle. A dynamic filter only
// hides rows; a target rule CHANGES them, so each row ships every rule's
// own bracket and outcome in data-modes (see _mode_payload in
// render_m5_confl2_report.py) and this swaps the affected cells in place:
// target, R, outcome, exit, PnL, MAE/MFE/giveback, the gap flag, the
// target-dependent badges, and the data-* numbers recomputeDynStats()
// reads back afterwards. Entry, fill and stop never change -- they do not
// depend on the target.
//
// With both rules ticked a row trades whichever offers the FARTHER target
// (the same rule _active_mode applies server-side). With the row's only
// available rule unticked it becomes a NO TARGET row: dimmed, and dropped
// from the stats exactly like a Python-side no-trade.
// ---------------------------------------------------------------------
function activeTargetModes() {
  return Array.from(document.querySelectorAll('.f-target-mode:checked')).map(cb => cb.dataset.mode);
}
function setCell(tr, sel, html, cls) {
  const td = tr.querySelector(sel);
  if (!td) return;
  td.innerHTML = html;
  if (cls !== undefined) td.className = cls;
}
function applyTargetModes() {
  const on = activeTargetModes();
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    if (!tr.dataset.modes) return;          // unfilled row: no bracket at all
    const modes = JSON.parse(tr.dataset.modes);
    let pick = null;
    on.forEach(m => {
      const p = modes[m];
      if (p && (pick === null || p.dist > modes[pick].dist)) pick = m;
    });
    const baseTags = tr.dataset.baseTags || '';
    if (pick === null) {
      tr.classList.add('no-target-row');
      tr.dataset.mode = '';
      tr.dataset.dynTags = baseTags;
      tr.dataset.r = ''; tr.dataset.pnlPts = ''; tr.dataset.outcome = '';
      tr.dataset.mgmtR = ''; tr.dataset.mgmtPnlPts = ''; tr.dataset.mgmtOutcome = '';
      tr.dataset.mgmtFired = '0';
      setCell(tr, '.tgt-cell', '-', 'tgt-cell');
      const tgt = tr.querySelector('.tgt-cell');
      if (tgt) tgt.title = 'No target under the target rules currently ticked';
      setCell(tr, '.rr-cell', '-', 'rr-cell');
      setCell(tr, '.outcome-cell',
              '<span class="outcome-label">NO TARGET (rule off)</span>'
              + '<span class="row-badges"></span><span class="mode-badges"></span>',
              'outcome-cell');
      setCell(tr, '.exit-cell', '-', 'left exit-cell');
      setCell(tr, '.exitpx-cell', '-', 'exitpx-cell');
      setCell(tr, '.pnl-cell', '-', 'pnl-cell');
      setCell(tr, '.mae-cell', '-', 'mae-cell');
      setCell(tr, '.mfe-cell', '-', 'mfe-cell');
      setCell(tr, '.gb-cell', '-', 'gb-cell');
      setCell(tr, '.gap-slot', '');
      return;
    }
    const p = modes[pick];
    tr.classList.remove('no-target-row');
    tr.dataset.mode = pick;
    tr.dataset.dynTags = (baseTags + ' ' + (p.tags || '')).trim();
    tr.dataset.r = p.r; tr.dataset.pnlPts = p.pnlPts; tr.dataset.outcome = p.outcome;
    tr.dataset.mgmtR = p.mgmtR; tr.dataset.mgmtPnlPts = p.mgmtPnl;
    tr.dataset.mgmtOutcome = p.mgmtOutcome; tr.dataset.mgmtFired = p.mgmtFired;
    setCell(tr, '.tgt-cell', p.tgt, 'tgt-cell');
    const tgt = tr.querySelector('.tgt-cell');
    if (tgt) tgt.title = p.tgtTitle;
    setCell(tr, '.rr-cell', p.rr, 'rr-cell');
    const outcomeCell = tr.querySelector('.outcome-cell');
    if (outcomeCell) {
      outcomeCell.className = 'outcome-cell ' + (p.outcomeCls || '');
      const lbl = outcomeCell.querySelector('.outcome-label');
      if (lbl) lbl.innerHTML = p.outcomeLabel;
      const mb = outcomeCell.querySelector('.mode-badges');
      if (mb) mb.innerHTML = p.modeBadges;
    }
    setCell(tr, '.exit-cell', p.exit, 'left exit-cell');
    setCell(tr, '.exitpx-cell', p.exitPx, 'exitpx-cell');
    setCell(tr, '.pnl-cell', p.pnl, 'pnl-cell ' + (p.pnlCls || ''));
    setCell(tr, '.mae-cell', p.mae, 'mae-cell bad');
    setCell(tr, '.mfe-cell', p.mfe, 'mfe-cell good');
    setCell(tr, '.gb-cell', p.gb, 'gb-cell');
    setCell(tr, '.gap-slot', p.gap);
  });
}
function recomputeDynStats() {
  applyTargetModes();
  const excludeTags = activeDynExcludeTags();
  const isolateTags = activeDynIsolateTags();
  const mgmtCb = document.getElementById('mgmt-thrust-trail');
  const useMgmt = !!(mgmtCb && mgmtCb.checked);
  let n = 0, wins = 0, sumR = 0, sumPnl = 0, maxWinMae = 0, maxLossMfe = 0;
  // MAE/MFE live only in their cells, and applyTargetModes (called above) has
  // already rewritten those for whichever target rule is ticked, so reading
  // the cell is reading the excursion of the bracket actually in force.
  const cellNum = (tr, sel) => {
    const c = tr.querySelector(sel);
    return c ? parseFloat(c.textContent) : NaN;
  };
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    const tags = (tr.dataset.dynTags || '').split(' ').filter(Boolean);
    const hidden = isolateTags.length > 0
      ? !tags.some(t => isolateTags.includes(t))
      : (excludeTags.length > 0 && tags.some(t => excludeTags.includes(t)));
    tr.classList.toggle('dyn-hidden', hidden);
    const chartRow = document.getElementById('chart-row-' + tr.dataset.idx);
    if (chartRow) chartRow.classList.toggle('dyn-hidden', hidden);
    if (hidden || tr.classList.contains('unfilled-row')
        || tr.classList.contains('no-target-row')) return;
    // Trade management (see trade_management.py): every filled long row
    // already carries a precomputed managed outcome in data-mgmt-* --
    // fired='1' means the rules actually changed something for that row.
    // Unfired rows fall through to baseline either way, so this swap is
    // safe even without the fired check, but keeping it explicit avoids
    // depending on baseline/managed floats matching bit-for-bit.
    const useRow = useMgmt && tr.dataset.mgmtFired === '1';
    const rVal = parseFloat(useRow ? tr.dataset.mgmtR : tr.dataset.r);
    const pnl = parseFloat(useRow ? tr.dataset.mgmtPnlPts : tr.dataset.pnlPts);
    const outcome = useRow ? tr.dataset.mgmtOutcome : tr.dataset.outcome;
    if (!isNaN(rVal)) {
      n += 1;
      sumR += rVal;
      if (outcome === 'target') {
        wins += 1;
        const mae = cellNum(tr, '.mae-cell');
        if (!isNaN(mae) && mae > maxWinMae) maxWinMae = mae;
      } else if (outcome === 'stop') {
        const mfe = cellNum(tr, '.mfe-cell');
        if (!isNaN(mfe) && mfe > maxLossMfe) maxLossMfe = mfe;
      }
    }
    if (!isNaN(pnl)) sumPnl += pnl;
  });
  const winRate = n ? (wins / n * 100) : 0;
  const avgR = n ? (sumR / n) : 0;
  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  setText('sum-trades', String(n));
  setText('sum-win-rate', winRate.toFixed(1) + '%');
  setText('sum-wins', String(wins));
  setText('sum-losses', String(n - wins));
  setText('sum-avg-r', avgR.toFixed(2));
  setText('sum-total-r', sumR.toFixed(1));
  setText('sum-total-pnl', (sumPnl >= 0 ? '+' : '') + sumPnl.toFixed(1));
  setText('sum-max-win-mae', maxWinMae.toFixed(2));
  setText('sum-max-loss-mfe', maxLossMfe.toFixed(2));
}
document.querySelectorAll('.f-dyn-exclude, .f-dyn-isolate, .f-target-mode').forEach(cb => cb.addEventListener('change', recomputeDynStats));
const mgmtToggleCb = document.getElementById('mgmt-thrust-trail');
if (mgmtToggleCb) mgmtToggleCb.addEventListener('change', recomputeDynStats);
recomputeDynStats();
</script>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="M5-native strategy: trade M5 LXPB retests with SS M5 confluence >= N, "
                     "entry fine-tuned to the confluence group's extreme price at the "
                     "breakout bar, stop above/below the thrust candle, target the nearest "
                     "qualifying opposite M5 level, no trade if either is missing or R < 1.")
    parser.add_argument("--ss-confl-min", type=int, default=SS_CONFL_MIN_DEFAULT)
    parser.add_argument("--m5-confluence-points", type=float, default=M5_CONFLUENCE_N_POINTS_DEFAULT,
                        help=f"M5 price radius for SS qualification, clustering and entry "
                             f"selection (default {M5_CONFLUENCE_N_POINTS_DEFAULT}pt).")
    parser.add_argument("--min-r", type=float, default=MIN_R_DEFAULT,
                        help=f"Reward:risk (target pts / stop pts) below which a trade is "
                             f"TAGGED 'r_below_min' (default {MIN_R_DEFAULT}). The trade is "
                             f"still taken, charted and resolved; the report's dynamic filter "
                             f"excludes those rows from the headline stats by default.")
    parser.add_argument("--target-mode", choices=("both", "consolidation", "opposite-m5"),
                        default=TARGET_MODE_DEFAULT,
                        help="Which target rule(s) are ON BY DEFAULT in the rendered page. "
                             "Both rules are ALWAYS computed and both are live checkboxes in "
                             "the report itself, so this only sets the starting state. "
                             "both (default): whichever rule offers the FARTHER target wins "
                             "per trade. consolidation: the nearest consolidation area built "
                             "between this level's own P1 and P2 -- an untested opposite-type "
                             "M5 P0 inside it if there is one, else the area's near edge (low "
                             "for a long, high for a short). opposite-m5: the newest live "
                             "opposite-type M5 level whose P1 broke >=2 same-type P0s and "
                             "whose own P0 formed inside the same P1..P2 window.")
    parser.add_argument("--consol-min-bars", type=int, default=CONSOL_MIN_BARS_DEFAULT,
                        help=f"Minimum M5 bars in a consolidation area (default "
                             f"{CONSOL_MIN_BARS_DEFAULT} = 30 minutes).")
    parser.add_argument("--consol-max-height", type=float, default=CONSOL_MAX_HEIGHT_DEFAULT,
                        help=f"Tallest price band a consolidation area may span, in points "
                             f"(default {CONSOL_MAX_HEIGHT_DEFAULT:g}).")
    parser.add_argument("--consol-max-er", type=float, default=CONSOL_MAX_ER_DEFAULT,
                        help=f"Efficiency-ratio ceiling for a consolidation area -- above this "
                             f"the run is a trend, not congestion (default "
                             f"{CONSOL_MAX_ER_DEFAULT:g}, lxpb.py's own ER_CONSOLIDATION_MAX).")
    parser.add_argument("--swerve", action=argparse.BooleanOptionalAction, default=True,
                        help="Move the entry past a confirmed swing sitting on it (see the "
                             "SWERVED section). ON by default.")
    parser.add_argument("--swerve-tol-pts", type=float, default=SWERVE_TOL_PTS_DEFAULT,
                        help=f"How close (points) a confirmed swing low/high has to be to the "
                             f"planned entry to trigger the move (default "
                             f"{SWERVE_TOL_PTS_DEFAULT:g}).")
    parser.add_argument("--swerve-lookback-hours", type=float,
                        default=SWERVE_LOOKBACK_HOURS_DEFAULT,
                        help=f"Cap on how far back before the retest swings are looked for "
                             f"(default {SWERVE_LOOKBACK_HOURS_DEFAULT:g}h). The search starts "
                             f"at the level's own P1 breakout bar, so this only binds on "
                             f"levels that waited longer than that to be retested.")
    parser.add_argument("--swerve-max-move-pts", type=float, default=SWERVE_MAX_MOVE_PTS_DEFAULT,
                        help=f"Furthest a swerved entry may be moved from the planned one "
                             f"(default {SWERVE_MAX_MOVE_PTS_DEFAULT:g}pt).")
    parser.add_argument("--swerve-swing-k", type=int, default=SWERVE_SWING_K_DEFAULT,
                        help=f"Bars required on each side of a swing pivot (default "
                             f"{SWERVE_SWING_K_DEFAULT}).")
    parser.add_argument("--liquidity-gate", action=argparse.BooleanOptionalAction, default=True,
                        help="Tag trades whose entry landed in a measurably illiquid tape "
                             "(news/thin book -- see liquidity.py). ON by default.")
    parser.add_argument("--liq-window-minutes", type=float, default=LIQ_WINDOW_MINUTES_DEFAULT,
                        help=f"Tape window measured before each fill (default "
                             f"{LIQ_WINDOW_MINUTES_DEFAULT:g} min).")
    parser.add_argument("--liq-wide-spread-share", type=float,
                        default=LIQ_WIDE_SPREAD_SHARE_DEFAULT,
                        help=f"Share of quotes wider than one tick at or above which the tape "
                             f"counts as illiquid (default {LIQ_WIDE_SPREAD_SHARE_DEFAULT:g}).")
    parser.add_argument("--eod-flat", action=argparse.BooleanOptionalAction, default=True,
                        help="End-of-day flat (trade_management.py rule 3): close any open "
                             "position before 12:45 PT and take no entry from then until the "
                             "Globex reopen. ON by default, and applied to the baseline "
                             "resolution as well as the managed one.")
    parser.add_argument("--max-alt-fill-hours", type=float, default=MAX_ALT_FILL_HOURS_DEFAULT,
                        help="Fill-window duration from the cluster's own M5 retest candle "
                             f"start (default {MAX_ALT_FILL_HOURS_DEFAULT:g}h).")
    parser.add_argument("--pegged-entry", action=argparse.BooleanOptionalAction, default=True,
                        help="Simulate a peg-to-market/chasing limit order for the fine-tuned "
                             "entry instead of a plain static limit. ON by default.")
    parser.add_argument("--peg-step", type=float, default=PEG_STEP_DEFAULT,
                        help=f"Re-quote increment in points for --pegged-entry (default "
                             f"{PEG_STEP_DEFAULT} = one ES tick).")
    parser.add_argument("--peg-cap", type=float, default=PEG_CAP_DEFAULT,
                        help=f"Max total chase distance in points from the fine-tuned entry "
                             f"for --pegged-entry (default {PEG_CAP_DEFAULT}).")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--max-rows", type=int, default=None,
                        help="process only the first N SS-Confl-qualifying candidates (smoke test)")
    parser.add_argument("--workers", type=int, default=1,
                        help="concurrent chunk processes for the per-cluster tick-backed work "
                             "(find_alt_fill/resolve_trades/chart building); chunks are "
                             "contract-pure so each worker only memory-maps one .scid contract "
                             "at a time (default 1 = serial).")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    args.default_target_modes = (TARGET_MODES if args.target_mode == "both"
                                 else (args.target_mode,))
    args.output = args.output or os.path.join(
        _REPO_ROOT, "public", "reports", "ss_m5_confl2", "ss_m5_confl2_report.html"
    )
    render(args)
