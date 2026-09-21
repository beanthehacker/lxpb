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
     of the SAME type, within +/-`--m5-confluence-points` (default 10.0pt),
     confirmed-broken-out and not yet retested as of the subject's own P1
     breakout bar -- lxpb_levels_cache.same_side_live_confluence, same
     definition the H1 report uses, just run on the M5 ledger instead of the
     H1 one) is >= `--ss-confl-min` (default 1; module/report name "confl2"
     predates this default and no longer describes it).

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

  5. TARGET = TWO RULES, BOTH READING THE LEVEL'S OWN P1..FILL WINDOW
     (`_pick_targets`). Both ask the same question -- what did the market
     build while this level was waiting to be retested -- and both take only
     structure lying wholly after the level's breakout candle and wholly
     before the candle it fills in, up to 25 points away on the favourable side:

       * OPPOSITE M5 LEVEL, ZIGZAG ANCHOR (`_opposite_m5_zz_target`, src tag
         m5_opposite_zz): find the most recent CONFIRMED zigzag TROUGH (short,
         LLPB) or CREST (long, LHPB) in the window -- m5_structure.zigzag_pivots,
         a >=`--zz-threshold-pts` (default 3pt) reversal off bar
         highs/lows, confirmed on a later bar at least `--zz-min-bars`
         (default 3) after the extreme, OR a >=5pt reversal confirmed at
         least 2 bars after it (m5_structure.ZIGZAG_FAST_*). That pivot is where the latest leg
         into the retest began. The target is then the FARTHEST untested
         opposite-type M5 P0 formed at or after that pivot (the pivot's own bar counts): the LOWEST
         price for a short, the HIGHEST for a long -- with no shared-P1
         confluence requirement (any single P0 qualifies).
       * SWING EXTREME (`_swing_extreme_target`, src tag swing_extreme): the
         most recent CONFIRMED zigzag TROUGH for a long (LHPB), CREST for a
         short (LLPB), formed between P1 and P2 (just before the retest); the
         pivot's own price is the target. No pivot there = no target. Only
         that single pivot is tried; if it is outside the up-to-25pt favourable
         band the rule finds nothing.

     BOTH rules run for every trade, whatever --target-mode says, and each is
     a LIVE CHECKBOX in the report: unticking one re-resolves every row
     against the other (or demotes it to a dimmed NO TARGET row if none are
     on) with no regen, because each rule's whole outcome is precomputed per
     row (see _mode_payload). With both on, a trade takes whichever rule
     offers the FARTHER target. --target-mode only sets which boxes start
     ticked; `--target-mode both` (the default) starts both ticked.

  6. NO FALLBACKS, BUT SUB-MIN-R TRADES ARE MEASURED, NOT DISCARDED. Unlike
     the H1 report (which falls back to a fixed stop/target when no
     qualifying M5 structure exists), THIS strategy has no fixed bracket at
     all: if step 4 or step 5 finds nothing, there is no trade. A bracket
     whose reward:risk (target points / stop points, fixed at entry) comes
     out below `--min-r` (default 1.0) IS still taken, resolved and charted
     -- tagged `r_below_min`, and dropped from the headline stats by the
     report's own R filter, which defaults to &ge; 1 (see the "R" row in
     the filter panel). The target is only knowable just before entry, so
     what those setups actually did is worth being able to look at.

  7. TRADE BLOCKS AND ENTRY ADJUSTMENTS. Three further rules, each of which
     TAGS its trades for the report's live dynamic filters rather than
     silently deleting them (see _apply_globex_open_filter's docstring for
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

       * VOLUME SPIKE (volume_spike.py, on by default). Reads the real 1s
         tape for a same-side stop-run around the fill: BidVolume for an
         LHPB (long) fill, AskVolume for an LLPB (short) one. Unlike a flat
         absolute cutoff (which means something very different overnight
         than during RTH), every second in a +/-30s core window around the
         fill is compared against that SAME fill's own +/-10min baseline
         (mean same-side volume/sec, core window excluded) -- a second
         QUALIFIES when it is both >= 8x that baseline and > 50 contracts
         outright, and the row is tagged `volume-spike` when at least one
         does. A stop run is rarely one clean print, so every qualifying
         second's offset from the fill is kept (not just the busiest one),
         shown range-compressed in the badge (`+12s-+15s,+19s`; see
         volume_spike.format_offsets). Purely informational -- unlike the
         other tags here it does not exclude the row from the headline
         stats by default; it is a Dynamic filter chip like the others so
         it can still be excluded/isolated for review, and the CLOSEST
         qualifying second's offset (`data-vspikeoffs`, absolute value) is
         a live numeric filter too, to compare a tight +/-10s read against
         a wider +/-30s one without a regen.

       * ANTI-BIAS (h1_bias.py, informational). The Bias column lists every
         H1 directional bias still LIVE at this trade's own P2 (retest) --
         plain candle context, no level involved: a hammer (bullish) or a
         shooting star (bearish) by patterns-pure, and an "sfp"
         (patterns-pure's find_sfp), a candle that sweeps an H1 swing low and
         closes back above it (bullish) or sweeps a swing high and closes
         back below it (bearish) -- where the swing swept has to be both
         confirmed and still untested, so one swing yields at most one sfp.
         Each one shows as `<pattern>@<H1 candles back>`
         (`hammer@-1`), counted from the H1 candle the retest sits in --
         which is still forming, so only already-closed candles can carry a
         bias. Every bias is short-lived and h1_bias.py owns exactly how
         long: all four die when the very next candle thrusts through them,
         or when a later candle takes out the bias candle's own low (bullish)
         / high (bearish), or on age -- 3 closed candles for a hammer/star, 4
         for an sfp, the only thing that differs between them).
         A trade whose own direction FADES at least one live bias -- a short
         under a live bullish bias, a long under a live bearish one -- is
         tagged `anti_bias`. Like `volume-spike` this is purely a Dynamic
         filter chip, NOT excluded from the headline stats by default.

       * END OF DAY (trade_management.py rule 3). No position is carried
         overnight: an open trade is flattened at market before 12:45 PT
         (outcome `eod_flat`), and a fill that would have landed at or after
         12:45 PT is no trade at all (`eod_entry_blocked`). This one is a
         hard rule, not a filter -- it is in the baseline and the managed
         numbers both.

       * WEAK P1 BREAKOUT (`_m5_p1_range_ratio_by_window`). The subject
         level's OWN P1 (breakout) candle range, divided by its trailing
         w-bar M5 average range -- the same range-ratio calc and
         `WIDE_BREAKOUT_RATIO_THRESHOLD` (2x) cutoff render_labels_report.py
         uses to flag a 'wide breakout' H1 P1 for the strong-breakout
         sample, just run on M5 bars. A thin ratio here is a thin thrust
         that barely out-ranged the recent tape, as distinct from a genuine
         impulsive break. This is its own row in the filter panel (the "P1
         range ratio" row, not a Dynamic filters tag): the window w
         (default `M5_RANGE_RATIO_WINDOW_DEFAULT`/20, 1..
         `M5_RANGE_RATIO_MAX_WINDOW`/50) AND the cutoff itself (default
         `WIDE_M5_BREAKOUT_RATIO_THRESHOLD`/2x, 0.1x..3x) are both LIVE in
         the browser, through the same generic op/value numeric-filter
         mechanism (f-num-op/f-num-val, data-target "p1ratio") as R and the
         Pre-P1 structure filter -- every row ships its whole w=1..50 ratio
         array (no ratio math client-side, just array indexing), and its
         own "P1 range ratio" column shows the reading for whichever window
         is currently picked, so the cutoff can be tuned against real
         numbers instead of blind. Defaults to &ge; 2x (only ratio &ge; 2x
         shown): on the 2026 data this lifts the default view to +0.55R
         avg / +67.3R total over 62 trades (it did NOT hold on 2025
         out-of-sample, so widen the filter to any to see the unfiltered
         numbers).

       * CREST REFINE (`_crest_refine_entry`, opt-in via `--crest-refine`,
         OFF by default). Runs AFTER the swerve rule, on whatever entry is
         currently planned. Scores how many standard deviations faster than
         usual price fell (long) / rose (short) from its own most recently
         CONFIRMED swing extreme (m5_structure.swing_pivots, the same
         fractal pivot the swerve rule uses) on the way into that entry --
         the rate (points per 5-minute bar since the pivot was confirmed)
         against its own trailing `--crest-refine-baseline` z-score. Below
         `--crest-refine-z` the approach is ordinary and nothing changes.
         At or above it, the entry is pushed further favourable by
         `--crest-refine-alpha` of the crest-to-planned distance already
         observed, capped at `--crest-refine-cap-pts` (tag `crest_refined`).
         Since the refined price is a synthetic offset rather than a
         specific ledger level, the stop step (4) falls through to the
         thrust-candle rule for these trades rather than a spike-P0
         override.

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
    python render_m5_confl2_report.py --start 2026-01-01 --end 2026-12-31 --output 2026.html
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
import volume_spike as VS                         # noqa: E402
import h1_bias as HB                              # noqa: E402

SS_CONFL_MIN_DEFAULT = 1
M5_CONFLUENCE_N_POINTS_DEFAULT = 10.0  # same-side M5 confluence radius: selection + entry refinement
MIN_DYNAMIC_TARGET_PTS = 0.25   # one tick: no real floor, just strictly favourable (SF is 1)
MAX_DYNAMIC_TARGET_PTS = 25.0   # own band (SF is 20); every target rule here shares it
DYNAMIC_STOP_RADIUS_PTS = SF.DYNAMIC_STOP_RADIUS_PTS
MAX_ALT_FILL_HOURS_DEFAULT = SF.MAX_ALT_FILL_HOURS_DEFAULT
MIN_R_DEFAULT = 1.0
TARGET_MODE_DEFAULT = "both"   # both target rules on by default (see _pick_targets)
ZZ_THRESHOLD_PTS_DEFAULT = MS.ZIGZAG_THRESHOLD_DEFAULT   # reversal that confirms a zigzag leg
ZZ_MIN_BARS_DEFAULT = MS.ZIGZAG_MIN_BARS_DEFAULT          # bars required after the extreme to confirm
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
# CREST REFINE (opt-in, OFF by default) -- see _crest_refine_entry. Scores how
# many standard deviations faster than usual price fell (long) / rose (short)
# from its own most recently CONFIRMED swing extreme on the way into the
# planned entry; an outlier-fast approach pushes the entry further favourable
# by a fraction of the crest-to-planned distance already observed.
CREST_REFINE_DEFAULT = False
CREST_REFINE_SWING_K_DEFAULT = MS.SWING_K_DEFAULT     # same fractal pivot the swerve rule uses
CREST_REFINE_BASELINE_DEFAULT = "150D"                # trailing window the z-score is measured against
CREST_REFINE_Z_THRESHOLD_DEFAULT = 3.0                # below this the approach is ordinary -- no change
CREST_REFINE_ALPHA_DEFAULT = 0.15                     # fraction of the crest-to-planned distance added
CREST_REFINE_CAP_PTS_DEFAULT = 10.0                   # furthest the entry may be pushed
LIQ_WINDOW_MINUTES_DEFAULT = LQ.WINDOW_MINUTES_DEFAULT
LIQ_WIDE_SPREAD_SHARE_DEFAULT = LQ.WIDE_SPREAD_SHARE_MAX_DEFAULT
VOL_SPIKE_CORE_SECONDS_DEFAULT = VS.CORE_WINDOW_SECONDS_DEFAULT
VOL_SPIKE_BASELINE_SECONDS_DEFAULT = VS.BASELINE_WINDOW_SECONDS_DEFAULT
VOL_SPIKE_RATIO_DEFAULT = VS.RATIO_THRESHOLD_DEFAULT
VOL_SPIKE_MIN_PEAK_DEFAULT = VS.MIN_PEAK_CONTRACTS_DEFAULT
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

def select_candidates(ss_confl_min, start, end, confluence_points):
    """M5-native candidate rows over the one continuous M5 ledger (see the
    "continuous contracts only" convention in CLAUDE.md -- LC.m5_levels()
    now spans every contract rollover in one state-machine run, so a P0
    formed on one contract can be retested by a later contract's bars).
    Returns a list of dicts (candidate index `i`, the row itself, its own
    same-side M5 confluence set, the shared ledger -- kept per-candidate
    since dynamic_target/dynamic_stop need the full ledger, not just the
    confluence subset -- and `seg_idx`, which RAW contract's own ticks
    cover this candidate's own instant, still needed for chart/tick work
    even though level detection itself no longer cares)."""
    if not np.isfinite(confluence_points) or confluence_points < 0:
        raise ValueError("M5 confluence radius must be finite and non-negative")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)  # end date inclusive
    candidates = []
    m5_ledger = LC.m5_levels(verbose=False)
    if m5_ledger is None or m5_ledger.empty:
        return candidates
    retests = LC.retests(m5_ledger)
    retests = retests[(retests["retest_time"] >= start_ts) & (retests["retest_time"] < end_ts)]
    for _, row_d in retests.iterrows():
        same_side_m5 = SF._same_side_confluence(m5_ledger, row_d, confluence_points)
        if len(same_side_m5) < ss_confl_min:
            continue
        seg_idx = R._contract_index_for(pd.Timestamp(row_d["retest_time"]))
        sym = R.CONTRACTS[seg_idx][0]
        candidates.append({
            "row": row_d, "same_side_m5": same_side_m5,
            "m5_ledger": m5_ledger, "seg_idx": seg_idx, "sym": sym,
        })
    candidates.sort(key=lambda c: pd.Timestamp(c["row"]["retest_time"]))
    for i, cand in enumerate(candidates):
        cand["i"] = i
    return candidates


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
# Target selection -- ZIGZAG-BASED RULES (m5_structure.zigzag_pivots)
#
# Both rules read the trade's own P1..fill window (see _window_bounds): the
# swing-extreme rule takes the last confirmed zigzag pivot's own price, the
# opposite-M5-zigzag-anchor rule takes the farthest untested opposite-type M5
# P0 formed after that pivot. The same up-to-25pt favourable band bounds both.
# --------------------------------------------------------------------------

TARGET_MODES = ("opposite-m5-zz", "swing-extreme")   # every rule _pick_targets computes
DEFAULT_TARGET_MODES_BOTH = TARGET_MODES              # what "--target-mode both" starts ticked


def _window_bounds(touch_time, p1_time, p2_time=None):
    """(after, cutoff) -- the exclusive instants a target's own structure has
    to sit between: after the level's P1 (breakout) and before the last
    completed M5 candle ahead of the FILL. The window runs to the fill, not
    to P2: a pegged/chasing entry can fill hours after the retest, and what
    the market built in those hours is just as much "what happened while
    this level was waiting" as anything before P2 -- it is all visible at
    the moment the order fills. `p2_time` is accepted so callers stay
    unchanged but no longer bounds anything."""
    return (None if p1_time is None else pd.Timestamp(p1_time)), MS.entry_cutoff(touch_time)


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
    lxpb.py's own consumption rule directly against the bars: ANY bar after
    the breakout bar that touches or gaps past the price kills it, the
    immediately-next bar included. (lxpb.py phase 3 silently consumes a
    touch inside the MIN_BARS_BEFORE_RETEST gap -- ledger fate
    'consumed_early' -- so that gap only decides whether a touch is a
    retest TRADE, never whether the level is still standing.) The ledger
    never recorded this for a candidate that failed the gate.

    The bar that first kills a candidate depends only on the candidate and the
    bars, never on `as_of`, so it is found once (_gated_kill_ns) and every
    later query just compares against it: the same gated candidates come up
    again for trade after trade, and replaying months of bars for each one
    was the single largest per-trade cost."""
    if bars.index.is_monotonic_increasing:
        kill = _gated_kill_ns(bars, level_type, price, breakout_time)
        return kill is None or kill > pd.Timestamp(as_of).value
    window = bars[(bars.index > breakout_time) & (bars.index <= as_of)]
    touched = (window["low"] <= price) & (window["high"] >= price)
    if level_type == "LHPB":
        gap_over = window["high"] < price
    else:
        gap_over = window["low"] > price
    return not (touched | gap_over).any()


_GATED_KILL_BARS = None   # (bars, times_ns, lows, highs) the memo below belongs to
_GATED_KILL_NS = {}


def _gated_kill_ns(bars, level_type, price, breakout_time):
    """UTC-ns time of the first bar that touches or gaps past a gated-dropped
    candidate under _target_candidate_still_live's rule -- any bar after its
    breakout bar -- or None if no
    bar in `bars` ever does. The candidate is live as of T exactly when this
    is None or later than T. Memoised per candidate; `bars` must be sorted."""
    global _GATED_KILL_BARS
    if _GATED_KILL_BARS is None or _GATED_KILL_BARS[0] is not bars:
        _GATED_KILL_BARS = (bars, bars.index.asi8, bars["low"].to_numpy(float),
                            bars["high"].to_numpy(float))
        _GATED_KILL_NS.clear()
    key = (level_type, price, pd.Timestamp(breakout_time).value)
    if key not in _GATED_KILL_NS:
        _, times, lows, highs = _GATED_KILL_BARS
        start = int(np.searchsorted(times, key[2], side="right"))
        lo, hi = lows[start:], highs[start:]
        touched = (lo <= price) & (hi >= price)
        gap_over = (hi < price) if level_type == "LHPB" else (lo > price)
        hit = np.flatnonzero(touched | gap_over)
        _GATED_KILL_NS[key] = int(times[start + hit[0]]) if hit.size else None
    return _GATED_KILL_NS[key]


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
    than a fixed number of points from the fill (up-to-25pt for a target,
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
    # Pre-narrowed with LC.select_levels -- real P0s to the ones still alive
    # at as_of, gated candidates to the distance band -- and then run through
    # exactly the same filters as the full-ledger fallback, so the rows kept
    # are identical.
    near = {"price": price, "pts": max_pts} if price is not None and max_pts is not None else {}
    nongated = LC.select_levels(m5_ledger, level_type, confirmed_by=as_of, alive_at=as_of,
                                min_breakout_levels=min_breakout_levels, gated=False)
    if nongated is not None:
        live = LC.levels_live_as_of(nongated, as_of)
        gated = LC.select_levels(m5_ledger, level_type, confirmed_by=as_of,
                                 min_breakout_levels=min_breakout_levels, gated=True, **near)
    else:
        confirmed = m5_ledger[(m5_ledger["type"] == level_type) &
                             (m5_ledger["breakout_time"] <= as_of)]
        if min_breakout_levels > 1:
            counts = confirmed.groupby("breakout_time")["formation_time"].transform("nunique")
            confirmed = confirmed[counts >= min_breakout_levels]
        is_gated = confirmed["fate"] == "gated_dropped"
        live = LC.levels_live_as_of(confirmed[~is_gated], as_of)
        gated = confirmed[is_gated]
    if near:
        gated = gated[(gated["price"] - price).abs() <= max_pts]
    if gated.empty:
        return live
    bars = LC.m5_bars_continuous()
    still_live = np.fromiter(
        (_target_candidate_still_live(bars, level_type, p, pd.Timestamp(b), as_of)
         for p, b in zip(gated["price"].to_numpy(float), gated["breakout_time"])),
        dtype=bool, count=len(gated))
    live_gated = gated[still_live].copy()
    live_gated["stage"] = "broken"
    return pd.concat([live, live_gated], ignore_index=True)


def _opposite_m5_zz_target(m5_ledger, level_type, price, is_long, touch_time,
                           p1_time=None, p2_time=None,
                           zigzag_threshold=ZZ_THRESHOLD_PTS_DEFAULT,
                           zigzag_min_bars=ZZ_MIN_BARS_DEFAULT):
    """(target_price, target_info) under the OPPOSITE-M5-ZZ rule, or
    (None, None).

    Step 1: the most recent CONFIRMED zigzag pivot where the latest leg into
    the retest began: a TROUGH for a short (LLPB), a CREST for a long
    (LHPB) -- m5_structure's standard threshold zigzag
    (`zigzag_pivots`/`most_recent_pivot`), formed and confirmed after P1 and
    before the fill's own candle. That pivot is where the latest leg into the
    retest started.

    Step 2: of the opposite-type M5 P0s that are still untested ('live',
    `_live_m5_target_candidates`, no shared-P1 requirement) as of the entry
    cutoff and formed at or after that pivot (the pivot's own bar counts) -- i.e. inside the
    latest leg into the retest -- take the one FARTHEST from the entry: the LOWEST
    price for a short, the HIGHEST for a long. Still bounded
    MIN..MAX_DYNAMIC_TARGET_PTS on the favourable side, the same sanity
    floor/ceiling every rule here uses; ties go to the earliest-formed.

    Pairing: an opposite-TYPE ledger level (LHPB for an LLPB trade); no
    hammer/shooting-star test is involved."""
    after, cutoff = _window_bounds(touch_time, p1_time, p2_time)
    pivots = MS.zigzag_pivots(threshold_pts=zigzag_threshold, min_bars=zigzag_min_bars)
    pivot_time, pivot_price = MS.most_recent_pivot(
        pivots, "high" if is_long else "low", cutoff, after=after)
    if pivot_time is None:
        return None, None
    opposite_type = "LLPB" if level_type == "LHPB" else "LHPB"
    cand = _live_m5_target_candidates(m5_ledger, opposite_type, touch_time,
                                      min_breakout_levels=1,
                                      price=price, max_pts=MAX_DYNAMIC_TARGET_PTS)
    if cand.empty:
        return None, None
    formed = pd.to_datetime(cand["formation_time"], utc=True)
    cand = cand[formed >= pivot_time]
    if cand.empty:
        return None, None
    distance = (cand["price"] - price) if is_long else (price - cand["price"])
    cand = cand[(distance >= MIN_DYNAMIC_TARGET_PTS) & (distance <= MAX_DYNAMIC_TARGET_PTS)]
    if cand.empty:
        return None, None
    cand = cand.assign(_formed=pd.to_datetime(cand["formation_time"], utc=True))
    cand = cand.sort_values(["price", "_formed"], ascending=[not is_long, True])
    best = cand.iloc[0].drop("_formed")
    return float(best["price"]), {"src": "m5_opposite_zz", "level": best.to_dict(),
                                  "pivot_time": pivot_time, "pivot_price": pivot_price}


def _swing_extreme_target(level_type, price, is_long, touch_time,
                          p1_time=None, p2_time=None,
                          zigzag_threshold=ZZ_THRESHOLD_PTS_DEFAULT,
                          zigzag_min_bars=ZZ_MIN_BARS_DEFAULT):
    """(target_price, target_info) under the SWING-EXTREME rule, or
    (None, None).

    The target is a zigzag pivot's OWN price: for an LHPB (long) the most
    recent CONFIRMED TROUGH, for an LLPB (short) the most recent CONFIRMED
    CREST, that formed strictly between the level's P1 and its P2 (the
    retest) -- the last swing just before the retest. Confirmation
    (m5_structure.zigzag_pivots / most_recent_pivot) must land before the
    fill's candle so the pivot is knowable at entry. No M5 level is involved.
    If no such pivot exists there is no target, and only that single most
    recent pivot is tried: if it is not MIN..MAX_DYNAMIC_TARGET_PTS away on
    the favourable side (above a long's fill, below a short's) the rule
    finds nothing rather than reaching back to an older pivot.

    Pairing: none -- no spike/hammer test; `level_type` only picks the pivot
    kind."""
    after, cutoff = _window_bounds(touch_time, p1_time, p2_time)
    pivots = MS.zigzag_pivots(threshold_pts=zigzag_threshold, min_bars=zigzag_min_bars)
    if p2_time is not None:
        pivots = pivots[pivots["time"] < pd.Timestamp(p2_time)]
    kind = "low" if is_long else "high"
    pivot_time, pivot_price = MS.most_recent_pivot(pivots, kind, cutoff, after=after)
    if pivot_time is None:
        return None, None
    distance = (pivot_price - price) if is_long else (price - pivot_price)
    if not (MIN_DYNAMIC_TARGET_PTS <= distance <= MAX_DYNAMIC_TARGET_PTS):
        return None, None
    return float(pivot_price), {"src": "swing_extreme", "level": None,
                                "pivot_time": pivot_time, "pivot_price": pivot_price,
                                "pivot_kind": kind}


def _pick_targets(m5_ledger, level_type, price, is_long, touch_time, args,
                  p1_time=None, p2_time=None):
    """{mode: (target_price, target_info)} for EVERY target rule that
    produces one for this trade -- keys from TARGET_MODES; a missing key
    means that rule found nothing.

    BOTH rules are always computed, whatever --target-mode says,
    because each one is a live in-browser toggle in the report: switching a
    rule off can demote a trade to a no-trade, and switching it back on has
    to restore that trade's whole bracket with no Python regen.
    --target-mode only picks which rules are ON BY DEFAULT in the rendered
    page.

    With several rules on and more than one producing a target, the
    FARTHEST target wins -- the rule asking for more room. See
    _active_mode."""
    out = {}
    px, info = _opposite_m5_zz_target(m5_ledger, level_type, price, is_long,
                                      touch_time, p1_time, p2_time,
                                      zigzag_threshold=args.zz_threshold_pts,
                                      zigzag_min_bars=args.zz_min_bars)
    if px is not None:
        out["opposite-m5-zz"] = (px, info)
    px, info = _swing_extreme_target(level_type, price, is_long, touch_time,
                                     p1_time, p2_time,
                                     zigzag_threshold=args.zz_threshold_pts,
                                     zigzag_min_bars=args.zz_min_bars)
    if px is not None:
        out["swing-extreme"] = (px, info)
    return out


def _active_mode(targets, price, enabled=TARGET_MODES):
    """Which of `targets` (a _pick_targets dict) a row actually trades under
    the given set of ENABLED rules: the one whose target sits FARTHEST from
    the fill, or None when no enabled rule found one (a real no-trade)."""
    live = {m: t for m, t in targets.items() if m in enabled}
    if not live:
        return None
    return max(live, key=lambda m: abs(live[m][0] - price))


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
# convention in _apply_globex_open_filter's docstring.
# --------------------------------------------------------------------------

def _swerve_entry(m5_ledger, level_type, is_long, conf, row_d, args, hi_ts=None):
    """Swerve-rule verdict for one cluster, as a dict (or None when the rule
    is off). MUTATES `conf` in place when the entry actually moves, so every
    downstream user of conf -- the fill scan, the spike-P0 stop override
    (_entry_level_row), the M5 chart's own entry-level ray -- follows the
    moved entry rather than the planned one.

    Keys: swings (the (time, price) pivots that triggered it), moved (bool),
    planned_price, price (the entry in force afterwards), level (the ledger
    row moved to, if any), blocked (bool -- triggered but nowhere to move).

    The window searched is the level's OWN wait -- from its P1 breakout bar
    to the point the entry is actually known to be filled, since that is
    exactly the span over which price could have come back towards the
    entry and turned away before the order was in. `hi_ts` carries that
    upper bound in (default the ledger's own retest instant, when the
    caller has no better one); `--swerve-lookback-hours` caps the lower
    bound off the ledger's retest either way, for the levels that sit
    broken out for weeks before anything comes back. Passing the entry's
    OWN tick-level fill touch time as `hi_ts` (rather than the ledger
    retest) is what lets this rule see a swing that only formed during a
    pegged/chasing wait for a passive fill -- see process_cluster's own
    probe call, which resolves that touch time before swerve runs."""
    if not args.swerve:
        return None
    planned = float(conf["alt_price"])
    retest_time = pd.to_datetime(row_d["retest_time"], utc=True)
    hi_ts = retest_time if hi_ts is None else pd.to_datetime(hi_ts, utc=True)
    breakout_time = pd.to_datetime(row_d["breakout_time"], utc=True)
    lo_ts = max(breakout_time, retest_time - pd.Timedelta(hours=args.swerve_lookback_hours))
    swings = MS.swings_near(planned, args.swerve_tol_pts, lo_ts, hi_ts,
                            is_low=is_long, k=args.swerve_swing_k)
    if not swings:
        return None

    out = {"swings": swings, "moved": False, "blocked": False,
           "planned_price": planned, "price": planned, "level": None}
    cand = SF._live_m5_before_entry(m5_ledger, level_type, hi_ts)
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
        if MS.swings_near(price, args.swerve_tol_pts, lo_ts, hi_ts,
                          is_low=is_long, k=args.swerve_swing_k):
            continue   # same problem one level down -- keep stepping
        out.update({"moved": True, "price": price, "level": lv.to_dict()})
        conf["alt_price"] = price
        conf["alt_source"] = "m5"
        conf["entry_m5_level"] = lv.to_dict()
        conf["alt_formation_time"] = pd.Timestamp(lv["formation_time"])
        conf["alt_end_time"] = (hi_ts if pd.isna(lv["death_time"])
                                else min(pd.Timestamp(lv["death_time"]), hi_ts))
        return out
    out["blocked"] = True
    return out


# --------------------------------------------------------------------------
# "CREST REFINE" -- push the entry further on an outlier-fast approach
# (opt-in, OFF by default -- see CREST_REFINE_DEFAULT and the module
# docstring's own CREST REFINE section)
# --------------------------------------------------------------------------

_CREST_REFINE_TABLES = {}


def _crest_refine_table(k, baseline, is_long_side):
    """{M5 bar time -> value} dicts (rate/z/crest_price/crest_time) for one
    side, over the whole continuous M5 series (LC.m5_bars_continuous).

    `rate` is how many points below (long side) / above (short side) the
    bar's own most recently CONFIRMED swing extreme it sits, divided by
    5-minute bars elapsed since that pivot was confirmed -- the same
    fractal swing_pivots (m5_structure.py) the swerve rule uses, confirmed
    `k` bars later same as there. `z` is rate's own trailing z-score
    against its rolling mean/std over `baseline` (a pandas time-offset
    string, e.g. '150D'), the window ending strictly before the bar itself
    (closed='left') so a bar's own value never inflates its own baseline --
    same "never inflate your own baseline" shape as
    _m5_p1_range_ratio_by_window's own trailing-average exclusion, just via
    the rolling window's own `closed` arg since this one is time-offset, not
    bar-count. NaN wherever no pivot has been confirmed yet or the baseline
    window isn't yet full. Lazily built and cached per (k, baseline, side)."""
    key = (k, baseline, is_long_side)
    if key in _CREST_REFINE_TABLES:
        return _CREST_REFINE_TABLES[key]
    bars = LC.m5_bars_continuous()
    sh_t, sh_p, sl_t, sl_p = MS.swing_pivots(k=k)
    piv_t, piv_p = (sh_t, sh_p) if is_long_side else (sl_t, sl_p)
    if len(piv_t) == 0:
        out = {"rate": {}, "z": {}, "crest_price": {}, "crest_time": {}}
        _CREST_REFINE_TABLES[key] = out
        return out
    confirm_lag = pd.Timedelta(minutes=5 * k)
    piv_times = pd.DatetimeIndex(piv_t, tz="UTC")
    confirm_times = piv_times + confirm_lag
    order = confirm_times.argsort()
    confirm_times = confirm_times[order]
    piv_times = piv_times[order]
    piv_prices = np.asarray(piv_p)[order]

    pos = confirm_times.searchsorted(bars.index, side="right") - 1
    valid = pos >= 0
    crest_price = pd.Series(np.nan, index=bars.index)
    crest_time = pd.Series(pd.NaT, index=bars.index, dtype="datetime64[ns, UTC]")
    crest_price.iloc[valid] = piv_prices[pos[valid]]
    crest_time.iloc[valid] = piv_times[pos[valid]]

    elapsed_bars = (bars.index.to_series() - crest_time).dt.total_seconds() / 300.0
    elapsed_bars = elapsed_bars.where(elapsed_bars > 0)
    close = bars["close"]
    rate = ((crest_price - close) if is_long_side else (close - crest_price)) / elapsed_bars

    mu = rate.rolling(baseline, closed="left").mean()
    sigma = rate.rolling(baseline, closed="left").std()
    z = (rate - mu) / sigma

    out = {"rate": rate.to_dict(), "z": z.to_dict(),
           "crest_price": crest_price.to_dict(), "crest_time": crest_time.to_dict()}
    _CREST_REFINE_TABLES[key] = out
    return out


def _crest_refine_entry(is_long, conf, row_d, args):
    """Crest-to-trough approach-speed override for one cluster (or None when
    the rule is off or doesn't fire). MUTATES `conf` in place when it fires,
    same convention as _swerve_entry -- every downstream user (the fill
    scan, the spike-P0 stop lookup via _entry_level_row, the M5 chart's own
    entry-level ray) follows the refined price rather than the planned one.

    Runs on whatever price is CURRENTLY planned in `conf` (post-swerve, if
    swerve fired), scores its approach for outlier speed (_crest_refine_table),
    and if it's an outlier, pushes the entry further favourable by a
    fraction of the crest-to-planned distance already observed.

    Sets conf['alt_source'] = 'crest_refine' and conf['entry_m5_level'] =
    None: the refined price is a synthetic offset, not a specific ledger
    level, so _entry_level_row finds no match and the stop step falls
    through to the thrust-candle rule rather than a spike-P0 override that
    no longer applies to this price.

    Keys: z, crest_price, crest_time, distance_pts (crest-to-planned,
    before refinement), refine_pts (points added), planned_price, price
    (the entry in force afterwards)."""
    if not args.crest_refine:
        return None
    tables = _crest_refine_table(args.crest_refine_swing_k, args.crest_refine_baseline, is_long)
    retest_time = pd.to_datetime(row_d["retest_time"], utc=True)
    z = tables["z"].get(retest_time)
    if z is None or not np.isfinite(z) or z < args.crest_refine_z:
        return None
    crest_price = tables["crest_price"].get(retest_time)
    crest_time = tables["crest_time"].get(retest_time)
    if crest_price is None or pd.isna(crest_price) or pd.isna(crest_time):
        return None
    planned = float(conf["alt_price"])
    distance = (crest_price - planned) if is_long else (planned - crest_price)
    if distance <= 0:
        return None
    n_pts = min(args.crest_refine_alpha * distance, args.crest_refine_cap_pts)
    if n_pts <= 0:
        return None
    new_price = planned - n_pts if is_long else planned + n_pts

    out = {"z": float(z), "crest_price": float(crest_price), "crest_time": pd.Timestamp(crest_time),
           "distance_pts": float(distance), "refine_pts": float(n_pts),
           "planned_price": planned, "price": new_price}
    conf["alt_price"] = new_price
    conf["alt_source"] = "crest_refine"
    conf["entry_m5_level"] = None
    conf["alt_formation_time"] = pd.Timestamp(crest_time)
    conf["alt_end_time"] = retest_time
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


M5_AVG_RANGE_WINDOW = R.AVG_RANGE_WINDOW                        # same trailing-bar count as the H1 hint
WIDE_M5_BREAKOUT_RATIO_THRESHOLD = R.WIDE_BREAKOUT_RATIO_THRESHOLD  # same 2x cutoff, run on M5 bars

M5_RANGE_RATIO_MAX_WINDOW = 50           # largest trailing-bar window the live control may select
M5_RANGE_RATIO_WINDOW_DEFAULT = M5_AVG_RANGE_WINDOW  # window the report starts with (20)

PRE_P1_ER_MAX_K = 60                                  # largest lookback the live k control may select
PRE_P1_ER_K_DEFAULT = 10                              # k the report starts with
PRE_P1_ER_MAX_DEFAULT = R.L.ER_CONSOLIDATION_MAX      # 0.5 -- lxpb.py's own "consolidating enough" cutoff


def _m5_p1_range_ratio_by_window(breakout_time, max_window=M5_RANGE_RATIO_MAX_WINDOW):
    """This level's own P1 breakout-bar (high-low) range divided by the
    trailing w-bar M5 average range (the average EXCLUDES the bar itself, so
    a level's own P1 thrust can never inflate its own baseline), for every
    window w = 1..max_window -- the M5-timeframe run of the same calc
    render_labels_report.compute_range_ratio_col does for the H1 'wide
    breakout' hint (WIDE_BREAKOUT_RATIO_THRESHOLD), just with the window
    itself picked per-call instead of fixed.

    Shipped as the WHOLE array (not one fixed reading), same reasoning as
    _pre_p1_er_by_k, so the report's live 'P1 range ratio' filter window
    control can pick any w in the browser -- see applyP1RatioWindow() in JS
    -- without restating the ratio formula there; only array indexing
    happens client-side. Indexed [0] -> w=1, [1] -> w=2, ..., None where the
    continuous M5 series doesn't reach back that far."""
    breakout_time = pd.to_datetime(breakout_time, utc=True)
    pos = _m5_bar_position_table().get(breakout_time)
    if pos is None:
        return [None] * max_window
    rng = (LC.m5_bars_continuous()["high"] - LC.m5_bars_continuous()["low"]).to_numpy(float)
    bar_range = rng[pos]
    out = []
    for w in range(1, max_window + 1):
        if w > pos:
            out.append(None)
            continue
        avg = rng[pos - w:pos].mean()
        out.append(float(bar_range / avg) if avg > 0 else None)
    return out


_M5_BAR_POS = None


def _m5_bar_position_table():
    """{M5 bar time -> its integer position in LC.m5_bars_continuous()}.
    Backs _pre_p1_er_by_k's and _m5_p1_range_ratio_by_window's slices into
    the series; lazily built once per process."""
    global _M5_BAR_POS
    if _M5_BAR_POS is None:
        _M5_BAR_POS = {t: i for i, t in enumerate(LC.m5_bars_continuous().index)}
    return _M5_BAR_POS


def _pre_p1_er_by_k(breakout_time, max_k=PRE_P1_ER_MAX_K):
    """Kaufman efficiency ratio (R.L._efficiency_ratio -- net move / total
    path traveled; 0 = round-tripped chop, 1 = a straight run) of the M5
    closes immediately preceding this level's own P1 breakout bar, for
    every lookback k = 2..max_k. Same measure lxpb.py's own
    candidate gate uses, aimed
    backward from a breakout instead of forward from a level's own
    formation. Returned as a list indexed [0] -> k=2, [1] -> k=3, ...,
    with None where the continuous M5 series doesn't reach back that far.

    Shipped as the WHOLE array (not one fixed reading) so the report's live
    'Pre-P1 structure' filter can pick any k in the browser -- see the
    filter panel and applyPreP1Er() in JS -- without restating the ratio
    formula there; only array indexing happens client-side."""
    breakout_time = pd.to_datetime(breakout_time, utc=True)
    pos = _m5_bar_position_table().get(breakout_time)
    if pos is None:
        return [None] * (max_k - 1)
    closes = LC.m5_bars_continuous()["close"].to_numpy(float)[max(0, pos - max_k):pos]
    n = len(closes)
    return [R.L._efficiency_ratio(list(closes[-k:])) if k <= n else None
            for k in range(2, max_k + 1)]


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
    # Probe the pre-swerve entry's own tick-level fill touch time so the
    # swerve rule can see swings that only formed during a pegged/chasing
    # wait, not just up to the ledger's own retest instant -- see
    # _swerve_entry's docstring. Reused below as the actual fill, unmodified,
    # if neither swerve nor crest-refine ends up moving the entry.
    retest_time = pd.to_datetime(row_d["retest_time"], utc=True)
    planned_before = float(conf["alt_price"])
    probe_touch = probe_fill = None
    if args.swerve:
        probe_touch, probe_fill = SF.find_alt_fill(
            retest_time, planned_before, is_long, level_type, args.max_alt_fill_hours,
            pegged=args.pegged_entry, peg_step=args.peg_step, peg_cap=args.peg_cap)
    swerve = _swerve_entry(m5_ledger, level_type, is_long, conf, row_d, args,
                           hi_ts=probe_touch if probe_touch is not None else retest_time)
    crest_refine = _crest_refine_entry(is_long, conf, row_d, args)
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
        "crest_refine": crest_refine,
        "dyn_tags": [],
        "filled": False,
    }
    if swerve is not None:
        result["dyn_tags"].append("swerved" if swerve["moved"] else "swerve_blocked")
    if crest_refine is not None:
        result["dyn_tags"].append("crest_refined")

    # P1 breakout-bar strength: this SUBJECT level's own breakout candle
    # (row_d, not the entry level -- entry refinement/swerve can move the
    # fill onto a different M5 level, but the retest signal being traded is
    # still row_d's own P1) measured against its trailing M5-bar average
    # range, same calc/threshold as the H1 report's phase1_wide_breakout
    # hint. Shipped as a plain number (its own "P1 range ratio" column and
    # data-p1-ratio-by-window), not a boolean tag: the report's numeric P1
    # range ratio filter (same op/value mechanism as R) lets the threshold
    # AND the window be picked live in the browser instead of being fixed
    # here.
    p1_range_ratio_by_window = _m5_p1_range_ratio_by_window(row_d["breakout_time"])
    result["p1_range_ratio_by_window"] = p1_range_ratio_by_window
    default_idx = M5_RANGE_RATIO_WINDOW_DEFAULT - 1
    result["p1_range_ratio"] = (p1_range_ratio_by_window[default_idx]
                                if 0 <= default_idx < len(p1_range_ratio_by_window) else None)

    # Structure just before P1: see _pre_p1_er_by_k. Ships the whole
    # k=2..PRE_P1_ER_MAX_K array; the report's live Pre-P1 structure filter
    # picks k and a cutoff in the browser (no regen).
    result["pre_p1_er_by_k"] = _pre_p1_er_by_k(row_d["breakout_time"])

    # P1->P2 gap: how many Globex/ETH reopen-to-reopen trading days
    # (TM.trading_day_label) separate this level's own breakout (P1) from
    # its retest (P2) -- 0 when both fall in the same session-to-session
    # window, 1 when the retest is the very next trading day, etc. Shipped
    # as a plain number (data-daygap), not a boolean tag: the report's
    # numeric daygap filter (same op/value mechanism as R) lets the
    # threshold N be picked live in the browser instead of being fixed here.
    p1_p2_day_gap = TM.trading_day_gap(row_d["breakout_time"], row_d["retest_time"])
    result["p1_p2_day_gap"] = p1_p2_day_gap

    # P1->P2 gap, H1 candles: how many whole H1 candles CLOSE strictly
    # between P1 and P2 (TM.h1_bar_gap) -- 0 when both fall in the same H1
    # bar or in two back-to-back ones (nothing closes between them), 1 when
    # exactly one H1 bar's close sits between them, etc.
    p1_p2_h1_gap = TM.h1_bar_gap(row_d["breakout_time"], row_d["retest_time"])
    result["p1_p2_h1_gap"] = p1_p2_h1_gap

    # P1->P2 gap, minutes: breakout_time/retest_time are themselves real M5
    # bar timestamps (not day- or hour-bucketed), so the elapsed minutes
    # between them is exact from a plain subtraction -- no scid/M1 lookup
    # needed, since there's no coarser precision here to refine away.
    p1_p2_minutes = (pd.to_datetime(row_d["retest_time"], utc=True)
                      - pd.to_datetime(row_d["breakout_time"], utc=True)).total_seconds() / 60.0
    result["p1_p2_minutes"] = p1_p2_minutes

    # Neither swerve nor crest-refine moved the entry off the price the probe
    # above already resolved a fill for -- reuse it rather than re-scanning
    # the same ticks. (No probe ran at all when --no-swerve; always rescan then.)
    if args.swerve and abs(alt_price - planned_before) <= 1e-9:
        touch_time_alt, fill_price = probe_touch, probe_fill
    else:
        touch_time_alt, fill_price = SF.find_alt_fill(
            retest_time, alt_price, is_long, level_type, args.max_alt_fill_hours,
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

    # Volume spike (volume_spike.py): same-side stop-run on the real 1s
    # tape around the fill -- purely informational, tags rather than
    # excludes (see the module docstring's VOLUME SPIKE bullet).
    if args.volume_spike_check:
        spiked, vspike = VS.detect(touch_time_alt, level_type,
                                    core_window_seconds=args.volume_spike_core_seconds,
                                    baseline_window_seconds=args.volume_spike_baseline_seconds,
                                    ratio_threshold=args.volume_spike_ratio,
                                    min_peak=args.volume_spike_min_peak)
        result["volume_spike"] = vspike
        if spiked:
            result["dyn_tags"].append("volume-spike")

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
    peg_kwargs = dict(peg_target=args.peg_target, peg_target_step=args.peg_target_step,
                      peg_target_cap=args.peg_target_cap)
    resolved = (TM.resolve_with_eod(trade, bars, **peg_kwargs) if args.eod_flat
                else SR.resolve_trades([trade], {0: bars}, stop=None, target=None,
                                       **peg_kwargs)[0])
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
    same_bar = LC.select_levels(ledger, level_type,
                                breakout_from=breakout_time, breakout_to=breakout_time)
    if same_bar is not None:
        ledger = same_bar
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


def _mgmt_markers(snap, mgmt, is_long):
    """Markers for one trade's fired trade-management events (rule 1 stop
    trail, rule 2 RR-floor exit -- see trade_management.py), each snapped by
    `snap` to the nearest bar at-or-before its own time. [] if nothing fired."""
    if not mgmt or not mgmt.get("fired"):
        return []
    out = []
    for trigger_time, new_stop_price in mgmt.get("trail_events") or []:
        # trigger_time is the thrust candle's own CLOSE (= the next bar's
        # open, see trade_management.thrust_trail_events) -- step back one
        # bar so the marker lands on the thrust candle itself, the one
        # responsible for the event, not the candle after it.
        t = snap(trigger_time - P1_BAR_WIDTH)
        if t is not None:
            out.append({
                "time": t, "position": "belowBar" if is_long else "aboveBar",
                "color": "#22d3ee", "shape": "arrowUp" if is_long else "arrowDown",
                "text": f"Stop → {new_stop_price:.2f}",
            })
    if mgmt.get("rr_floor_fired") and mgmt.get("exit_time") is not None:
        t = snap(mgmt["exit_time"])
        if t is not None:
            out.append({
                "time": t, "position": "aboveBar" if is_long else "belowBar",
                "color": "#fb923c", "shape": "circle", "text": "RR FLOOR EXIT",
            })
    return out


def _annotate_mgmt_events(chart_m5, res, is_long):
    """Mark fired trade-management events on the M5 pane (see _mgmt_markers).
    No-op if chart_m5 is None or nothing fired for this trade."""
    snap = _snapper(chart_m5)
    if snap is None:
        return
    new_markers = _mgmt_markers(snap, res.get("mgmt"), is_long)
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


SWERVE_COLOR = "#86efac"        # the swing that moved the entry, and the planned entry it left


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


def _target_ray_start(info, entry_t):
    """Epoch start of a target's ray: its opposite-M5 level's formation, else
    the swing-extreme rule's own zigzag pivot candle, else the entry candle."""
    info = info or {}
    level = info.get("level")
    if level is not None and level.get("formation_time") is not None:
        return R._to_epoch_utc(level["formation_time"])
    if info.get("pivot_time") is not None:
        return R._to_epoch_utc(info["pivot_time"])
    return entry_t


def _stop_ray_start_time(res):
    """The candle the stop price is built on: the spike P0 candle for the
    spike-P0 stop (its low/high), else the P1 breakout (thrust) candle of the
    level whose extreme is the stop. None if unknown."""
    lvl = res.get("stop_m5_level") or {}
    key = "formation_time" if res.get("stop_source") == "m5_p0_spike" else "breakout_time"
    t = lvl.get(key)
    return None if t is None or pd.isna(t) else pd.Timestamp(t)


def _entry_ray_start_time(res):
    """The candle the entry price is built on: the entry level's own P0
    (own / m5 confluence / swerved-to level) or, for a crest-refined entry,
    the crest candle it was pushed from. None if unknown."""
    t = res.get("alt_formation_time")
    return None if t is None or pd.isna(t) else pd.Timestamp(t)


def _target_ray_start_times(res):
    """Every target rule's ray-start instant for this trade, so the M5 pane's
    window keeps those candles instead of compressing them out."""
    infos = [res.get("target_info")] + [m.get("target_info") for m in (res.get("modes") or {}).values()]
    out = []
    for info in infos:
        info = info or {}
        level = info.get("level")
        t = level.get("formation_time") if level is not None else None
        t = t if t is not None else info.get("pivot_time")
        if t is not None:
            out.append(pd.Timestamp(t))
    out.extend(t for t in (_stop_ray_start_time(res), _entry_ray_start_time(res)) if t is not None)
    return out


def _rayify_trade_lines(chart_m5, res, row_for_chart):
    """Turn the M5 pane's full-width target / stop / entry / own-level price
    lines into rays: each starts at the candle it is built on and runs to the
    right edge, with its price (and title) on the y-axis.

    Starts: own level -> its P0 (formation) candle; entry -> the entry level's
    P0 (or the crest candle when refined); stop -> the spike P0 candle or the
    P1 thrust candle it was taken from; target -> its opposite-M5 level's
    formation, else its zigzag pivot candle; each falling back to the entry
    candle (the pane's ENTRY/PLANNED marker) when unknown. A start compressed out of the
    pane snaps to the first bar at or after it; one before the pane's first
    bar starts at the first bar. Other price lines (swerve's planned
    entry) are left as they are."""
    if chart_m5 is None or not chart_m5["candles"]:
        return
    times = [c["time"] for c in chart_m5["candles"]]
    entry_t = next((m["time"] for m in chart_m5["markers"]
                    if m.get("text", "").startswith(("P2 ENTRY", "P2 PLANNED"))), times[0])
    target_t = _target_ray_start(res.get("target_info"), entry_t)
    own_t = R._to_epoch_utc(row_for_chart["formation_time"])
    stop_ts, entry_ts = _stop_ray_start_time(res), _entry_ray_start_time(res)
    stop_t = R._to_epoch_utc(stop_ts) if stop_ts is not None else entry_t
    entry_line_t = R._to_epoch_utc(entry_ts) if entry_ts is not None else entry_t
    starts = (("target", target_t), ("stop", stop_t), ("entry", entry_line_t),
              (f"M5 {res['level_type']}", own_t))

    kept = []
    for pl in chart_m5.get("priceLines", []):
        start = next((t for prefix, t in starts if pl["title"].startswith(prefix)), None)
        if start is None:
            kept.append(pl)
            continue
        pts = [{"time": t, "value": pl["price"]} for t in times if t >= start]
        chart_m5.setdefault("rays", []).append({
            "points": pts, "color": pl["color"], "lineWidth": pl["lineWidth"],
            "lineStyle": pl["lineStyle"], "priceLabel": True, "title": pl["title"],
            "label": pl["title"],
        })
    chart_m5["priceLines"] = kept


def _add_mode_views(chart_m5, res, is_long):
    """Ship every target rule's own version of what the rule changes on the
    M5 pane, as chart_m5['modeViews'][mode] = {ray, markers}: the
    target ray and the exit / trade-management markers. The browser (see
    chartForMode in JS) strips the default rule's versions of those from the
    pane -- target ray titled 'target', markers WIN/LOSS/CANDLE/'Stop ->'/RR FLOOR EXIT --
    and puts the ticked rule's in, so the pane follows the target-rule
    checkboxes the same way the row's cells do. Entry, stop and the candles
    never change with the rule. Needs _rayify_trade_lines to have run (it
    supplies the target ray's colour and style)."""
    snap = _snapper(chart_m5)
    if snap is None or not res.get("modes"):
        return
    tmpl = next((r for r in chart_m5.get("rays", []) if r["title"].startswith("target")), None)
    if tmpl is None:
        return
    times = [c["time"] for c in chart_m5["candles"]]
    entry_t = next((m["time"] for m in chart_m5["markers"]
                    if m.get("text", "").startswith(("P2 ENTRY", "P2 PLANNED"))), times[0])
    views = {}
    for mode, m in res["modes"].items():
        info = m["target_info"] or {}
        start = _target_ray_start(info, entry_t)
        title = f"target {m['target_price']:.2f} (+{SR._fmt_pts(m['target_pts'])}pt)"
        view = {"ray": {**tmpl, "title": title, "label": title,
                        "points": [{"time": t, "value": m["target_price"]}
                                   for t in times if t >= start]},
                "markers": []}
        resolved = m["resolved"]
        outcome, exit_time = resolved.get("outcome"), resolved.get("exit_time")
        t = snap(exit_time) if exit_time is not None else None
        if t is not None and outcome in ("target", "stop", "candle"):
            win = outcome == "target"
            above = is_long if win else not is_long
            if outcome == "target":
                text, color = f"WIN +{m['r_multiple']:.2f}R", SR.EXIT_WIN_COLOR
            elif outcome == "stop":
                text, color = "LOSS -1.00R", SR.EXIT_LOSS_COLOR
            else:
                r_val = resolved.get("r")
                text = f"CANDLE {r_val:+.2f}R" if r_val is not None else "CANDLE"
                color = SR.EXIT_CANDLE_COLOR
            view["markers"].append({
                "time": t, "position": "aboveBar" if above else "belowBar", "color": color,
                "shape": ("arrowUp" if is_long else "arrowDown") if win
                         else ("arrowDown" if is_long else "arrowUp"),
                "text": text})
        view["markers"].extend(_mgmt_markers(snap, m["mgmt"], is_long))
        views[mode] = view
    chart_m5["modeViews"] = views
    chart_m5["activeMode"] = res["active_mode"]


M5_ONLY_NOTE = "<p class='note'>(tick panes and footprints skipped: --m5-charts-only)</p>"

# Context panes above the M5 chart (see _build_context_charts).
CTX_H1_BARS_BEFORE = 150
CTX_H1_BARS_AFTER = 30
CTX_D1_DAYS_BEFORE = 90
CTX_D1_DAYS_AFTER = 10
_D1_CACHE = None


def _display_d1():
    """Daily bars aggregated from the TradingView continuous H1 export (never
    from .scid), one bar per ES trading day. A session runs 15:00 PT (prior
    calendar day) to 14:00 PT, so shifting an H1 bar's PT time forward 9h
    lands every bar of a session on that session's closing PT date. Each bar's
    `time` is noon PT of that date so the browser's PT date label reads the
    trading date. Cached -- the H1 series is static."""
    global _D1_CACHE
    if _D1_CACHE is None:
        h1 = R._display_h1()
        pt = h1.index.tz_convert("America/Los_Angeles")
        day = (pt + pd.Timedelta(hours=9)).normalize().tz_localize(None)
        g = h1.groupby(day)
        d1 = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                           "low": g["low"].min(), "close": g["close"].last()})
        d1.index = pd.DatetimeIndex(
            [pd.Timestamp(f"{d.date()} 12:00", tz="America/Los_Angeles") for d in d1.index]
        ).tz_convert("UTC")
        _D1_CACHE = d1
    return _D1_CACHE


def _d1_bar_time(ts):
    """The D1 bar `time` (noon PT of the trading date, see _display_d1) of the
    session containing instant `ts`."""
    pt = pd.Timestamp(ts).tz_convert("America/Los_Angeles") + pd.Timedelta(hours=9)
    return pd.Timestamp(f"{pt.date()} 12:00", tz="America/Los_Angeles").tz_convert("UTC")


def _build_context_chart(bars, label, entry_t, exit_t, lo, hi, levels, is_long,
                         date_only=False, anchor=None):
    """One plain candle pane (no LXPB levels) over bars[lo:hi], with the
    trade's own entry/stop/target/level lines and entry/exit markers. `levels`
    is a list of (price, color, lineStyle, title). Returns None when the bars
    do not cover the entry. `anchor` maps an instant to the bar time that
    contains it (D1 needs it; H1 snaps to the last bar at or before)."""
    if bars is None or bars.empty or entry_t < bars.index[0] or entry_t > bars.index[-1] + pd.Timedelta(days=1):
        return None
    win = bars.loc[lo:hi]
    if win.empty:
        return None
    candles = [{"time": R._to_epoch_utc(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in win.iterrows()]
    times = [c["time"] for c in candles]

    def snap(ts):
        if anchor is not None:
            ts = anchor(ts)
        pos = bisect.bisect_right(times, int(pd.Timestamp(ts).timestamp())) - 1
        return times[max(pos, 0)]

    markers = [{"time": snap(entry_t), "position": "belowBar" if is_long else "aboveBar",
                "color": R.ENTRY_COLOR, "shape": "circle", "text": "entry"}]
    if exit_t is not None:
        markers.append({"time": snap(exit_t), "position": "aboveBar" if is_long else "belowBar",
                        "color": "#fbbf24", "shape": "square", "text": "exit"})
    markers.sort(key=lambda m: m["time"])
    lines = [{"price": p, "color": c, "lineWidth": 1, "lineStyle": s, "title": t}
             for p, c, s, t in levels]
    return {"title": f"{label}  |  {R._to_pt_str(win.index[0])} → {R._to_pt_str(win.index[-1])}",
            "candles": candles, "markers": markers, "priceLines": lines, "rays": [],
            "precision": 2, "dateOnly": date_only}


def _build_context_charts(res, filled):
    """{'h1': ..., 'd1': ...} panes for the row. Both come from the
    TradingView H1 export (D1 aggregated from it), on the same back-adjusted
    scale as the M5 pane. Filled trades get entry/stop/target lines and an
    exit marker; unfilled rows get only the level and the M5 retest instant."""
    is_long = res["is_long"] if "is_long" in res else res["level_type"] == "LHPB"
    if filled:
        entry_t = pd.Timestamp(res["touch_time_alt"]).tz_convert("UTC")
        exit_t = res["resolved"].get("exit_time")
        fill, stop_pts, tgt_pts = res["fill_price"], res["stop_pts"], res["target_pts"]
        levels = [(res["own_price"], R.LEVEL_COLOR, 0, f"level {res['own_price']:.2f}"),
                  (fill, R.ENTRY_COLOR, 0, f"entry {fill:.2f}"),
                  (fill - stop_pts if is_long else fill + stop_pts,
                   SR.EXIT_LOSS_COLOR, 2, "stop"),
                  (fill + tgt_pts if is_long else fill - tgt_pts,
                   SR.EXIT_WIN_COLOR, 2, "target")]
    else:
        entry_t = pd.Timestamp(res["row"]["retest_time"])
        entry_t = entry_t.tz_localize("UTC") if entry_t.tzinfo is None else entry_t.tz_convert("UTC")
        exit_t = None
        levels = [(res["own_price"], R.LEVEL_COLOR, 0, f"level {res['own_price']:.2f}")]
    if exit_t is not None:
        exit_t = pd.Timestamp(exit_t)
        exit_t = exit_t.tz_localize("UTC") if exit_t.tzinfo is None else exit_t.tz_convert("UTC")
    end_t = exit_t if exit_t is not None else entry_t

    h1 = R._display_h1()
    h1_chart = _build_context_chart(
        h1, "H1", entry_t, exit_t,
        entry_t - pd.Timedelta(hours=CTX_H1_BARS_BEFORE),
        end_t + pd.Timedelta(hours=CTX_H1_BARS_AFTER), levels, is_long)
    d1_chart = _build_context_chart(
        _display_d1(), "D1 (session 15:00 PT prior day → 14:00 PT, labelled by close date)",
        entry_t, exit_t,
        entry_t - pd.Timedelta(days=CTX_D1_DAYS_BEFORE),
        end_t + pd.Timedelta(days=CTX_D1_DAYS_AFTER), levels, is_long, date_only=True, anchor=_d1_bar_time)
    return {"h1": h1_chart, "d1": d1_chart}


def build_chart_stack_for_row(res, m5_only=False):
    """D1 + H1 context panes (_build_context_charts) + M5 + 1s-trio + 1min +
    footprint chart stack for a filled, in-R trade. Reuses
    render_ss_confl_finetune_report.build_execution_charts verbatim (it
    never assumed an H1-anchored row) and render_stop_target_report.
    build_m5_chart with this strategy's own 5-minute P1 bar width.

    m5_only skips every tick-built pane (1s trio, 1-minute, footprints).
    Only the charts go -- the trade itself was still filled and resolved on
    real ticks by process_cluster."""
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
    extra_context_times.extend(_target_ray_start_times(res))
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
        _annotate_swerve(chart_m5, res, res["is_long"])
        _rayify_trade_lines(chart_m5, res, row_for_chart)
        _add_mode_views(chart_m5, res, res["is_long"])
    ctx = _build_context_charts(res, filled=True)
    if m5_only:
        return {**ctx, "m5": chart_m5, "trio": None, "oneMin": None}, {"narrow": M5_ONLY_NOTE,
                                                                        "wide": M5_ONLY_NOTE}
    execution_charts, fp = SF.build_execution_charts({**res, "row": row_for_chart})
    return {**ctx, "m5": chart_m5, **execution_charts}, fp


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

    ctx = _build_context_charts(res, filled=False)
    if getattr(args, "m5_charts_only", False):
        return {**ctx, "m5": chart_m5, "trio": None, "oneMin": None}, {"narrow": M5_ONLY_NOTE,
                                                                        "wide": M5_ONLY_NOTE}
    fill_window = SF.build_fill_window_chart(
        window_start, alt_price, level_type, args.max_alt_fill_hours,
        res.get("fail_reason"))
    if fill_window is not None:
        # build_fill_window_chart's own title says "refined H1 retest" --
        # right for its native H1-finetune caller, wrong here (this
        # strategy has no H1 leg at all; window_start IS the M5 retest).
        fill_window["title"] = fill_window["title"].replace(
            "refined H1 retest", "M5 retest")
    chart_stack = {**ctx, "m5": chart_m5, "trio": None, "oneMin": fill_window}
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
            chart_stack, fp = build_chart_stack_for_row(
                res, m5_only=getattr(args, "m5_charts_only", False))
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
                chart_stack, fp = build_chart_stack_for_row(
                    res, m5_only=getattr(args, "m5_charts_only", False))
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


def _apply_globex_open_filter(results):
    """Mutates and returns `results` in place.

    DYNAMIC FILTER CONVENTION -- read this before adding another one. This
    does NOT remove trades from the report or from the server-computed
    baseline stats. It only TAGS a qualifying result by appending to
    res['dyn_tags'] (a list; a row can carry more than one dynamic-filter
    tag). _render_row turns each tag into a `data-dyn-tags` attribute (plus
    data-r/data-pnl-pts/data-outcome for live recompute) on that row's
    <tr>, and the report's JS (see the 'Dynamic filters' block appended to
    JS below _finish_report) lets the user toggle an Exclude checkbox per
    tag, plus an Only (isolate) RADIO stacked under it -- all the Only
    radios share one <input name>, so the browser itself enforces at most
    one isolated tag at a time (clicking an already-selected radio unchecks
    it again, see the click handler in JS) -- IN THE BROWSER to hide/show
    those rows and recompute win rate / avg R / total R / total PnL live,
    with NO Python regen required. This is deliberately generic: to add a
    new dynamic filter, (1) tag qualifying results with one more entry in
    dyn_tags (their own detection logic, wherever that lives), (2) add one
    <div class="chip-stack"> holding one <label class="chip"> checkbox with
    class f-dyn-exclude and one <label class="chip chip-iso"> radio with
    class f-dyn-isolate (same shared name="f-dyn-isolate-radio" as the
    others), both data-tag="<your tag>", to the filter panel in
    _finish_report. Nothing else needs to change -- the JS's
    recomputeDynStats() is tag-agnostic.

    This particular filter tags, doesn't remove: a filled result whose
    actual fill (touch_time_alt) landed in [15:00, 15:05) Pacific time --
    the daily Globex/ETH reopen (6pm ET), when the M5 stop/target structure
    this strategy trades against isn't reliable across the reopen gap --
    gets 'globex_eth_open' appended to res['dyn_tags']. Ships checked by
    default in _finish_report's filter panel: on the full [2026-07-01,
    2026-08-31] dataset it tags 6 of 167 baseline trades, all 6 losers (0
    wins dropped) -- excluding them takes total R from -0.8 to +5.2, so the
    default view already reflects that."""
    for res in results:
        if not res["filled"]:
            continue
        if _in_globex_open_window(res["touch_time_alt"]):
            res.setdefault("dyn_tags", []).append("globex_eth_open")
    return results


H1_CONFL_RADIUS_PTS = 10.0  # this strategy is M5-only; this is a cross-timeframe REVIEW aid, not a rule input
H1_CONFL_HOUR_TOLERANCE = pd.Timedelta(hours=1)  # see _apply_h1_p0_confluence: how far before the M5 retest's own hour an H1 P0 may have already died and still count


def _h1_p0_kind(level_type, is_spike, is_swing):
    """hammer for LHPB / shooting star for LLPB (lxpb.py's is_spike, judged by
    patterns-pure: LHPB=bar.high+find_hammer, LLPB=bar.low+find_shooting_star --
    same mapping render_m5_confl2_report.py already uses for its own
    m5_p0_spike stop tooltip). 'swing' when not a spike but still a genuine
    local extreme; 'other' otherwise (e.g. a level whose is_swing was never
    finalized, or that qualified as neither -- these never became real M5
    P0 candidates under lxpb.py's own gate, but H1 tracks every registered
    level regardless, and this column is a raw structural check, not a
    replay of the gate)."""
    if is_spike:
        return "hammer" if level_type == "LHPB" else "shooting star"
    if is_swing:
        return "swing"
    return "other"


def _apply_h1_p0_confluence(results):
    """Mutates and returns `results` in place. Same dynamic-filter
    convention as _apply_globex_open_filter (see that function's docstring)
    -- for every FILLED result, looks up every H1 level of the SAME LXPB
    TYPE as this trade (an LHPB M5 trade only ever confluences with H1
    LHPB, never LLPB, and vice versa -- the two types are opposite-direction
    structure, not interchangeable S/R), within +/-H1_CONFL_RADIUS_PTS of
    the actual fill price (res['fill_price']), that was STILL UNTESTED
    (not yet dead) up to around the clock hour this M5 trade itself
    retested in -- this column is meant to answer 'was the H1 version of
    this exact LXPB pattern still live when M5 confirmed the same setup',
    i.e. price-is-fractal confluence, not merely 'was there some H1 level
    near here at some point during a multi-week span'.

    Concretely: an H1 P0 qualifies if it had already FORMED by this
    trade's own P2 (row_d['retest_time'] -- it must exist for there to be
    anything to call confluence) AND it did not die (react/retest) more
    than H1_CONFL_HOUR_TOLERANCE before P2's own hour. P2's hour is
    floored (e.g. a 09:35 PT retest floors to 09:00 PT) and the tolerance
    is subtracted from that floor, so a 1hr tolerance means dying any time
    at or after 08:00 PT still counts -- dying right in the 09:00 PT hour
    (matching P2's own hour) is the tightest, cleanest case, but the
    tolerance exists because the H1 candle containing the exact retest
    minute is an arbitrary boundary to demand exact alignment against. An
    H1 P0 that already died hours or days before that floor is unrelated
    stale structure and excluded even if it is the closest price match --
    that was the whole failure mode of the previous P1->P2-window-overlap
    version of this check, which happily matched H1 P0s that died weeks
    before this M5 trade's own retest merely because they had still been
    alive somewhere earlier in the (often multi-week) P1->P2 span. A level
    that dies AFTER the tolerance window, or never dies at all (still
    fully open), also still counts -- there is no upper bound on how late
    the H1 reaction may come, since 'still untested near the M5 retest
    hour' is exactly satisfied by a level that stays untested even longer.
    Deliberately ANY fate otherwise --
    gated_dropped/discarded_no_close H1 levels still show, since this
    column is a raw structural check, not a replay of lxpb.py's candidate
    gate (see _h1_p0_kind's own docstring for that same point re: kind).

    A candidate H1 P0 is further required to have at least one H1 candle
    between its OWN "start" and its own reaction (death) -- an immediate
    next-bar snap-back (0 candles between) reads as a wick round-trip, not
    a level the market spent any time respecting, so it is too weak to
    count as confluence. "Start" is breakout_time (P1) when the level
    actually closed through -- covering fate=retested (death_time ==
    retest_time == P2, so this is exactly the P1->P2 gap) and
    fate=consumed_early (broke out, then got touched/gapped-past again
    before MIN_BARS_BEFORE_RETEST bars passed -- death_time is that early
    touch, not a formal retest_time, so checking retest_time alone missed
    this fate entirely: an earlier version of this rule kept every
    consumed_early level unchecked no matter how instantly it snapped
    back). "Start" falls back to formation_time when there was no
    breakout at all (fate=discarded_no_close: touched but never closed
    through, so breakout_time is NaT -- checking that fate's OWN
    formation-to-death gap is the only way to catch an immediate
    touch-and-die there; e.g. a level formed one H1 bar and touched/died
    on the very next is 0 candles apart even though it never had a P1 in
    lxpb.py's strict sense). Checked by BAR POSITION in R._display_h1(),
    not wall-clock time: a weekend/holiday close sits between two truly
    ADJACENT bars with no separating candle, and a wall-clock gap
    threshold (e.g. >=2h) would wrongly count that closure as 'a candle in
    between'. A level still fully open (death_time null) has no reaction
    yet to check and passes through unaffected.

    This strategy is M5-only (see the module docstring -- 'drops H1
    entirely'); this is purely a review aid answering 'was there H1
    structure sitting near where this trade entered', not a strategy input.
    Stores the list (nearest first, [] if none) as res['h1_p0_confl'] for
    _render_row's own column (a plain count, with the full detail in that
    cell's tooltip -- see _h1_confl_cell)."""
    h1_ledger = LC.h1_levels(verbose=False)
    h1_bar_pos = {ts: i for i, ts in enumerate(R._display_h1().index)}
    for res in results:
        if not res["filled"]:
            continue
        row_d = res["row"]
        p2 = pd.Timestamp(row_d["retest_time"])
        as_of = pd.Timestamp(res["touch_time_alt"])
        window_low = p2.floor("h") - H1_CONFL_HOUR_TOLERANCE
        same_type = h1_ledger[h1_ledger["type"] == res["level_type"]]
        untested_near_retest_hour = ((same_type["formation_time"] <= p2) &
                                     (same_type["death_time"].isna() | (same_type["death_time"] >= window_low)))
        same_type = same_type[untested_near_retest_hour]
        own_start = same_type["breakout_time"].where(same_type["breakout_time"].notna(),
                                                      same_type["formation_time"])
        own_start_pos = own_start.map(h1_bar_pos)
        own_death_pos = same_type["death_time"].map(h1_bar_pos)
        has_candle_between = (same_type["death_time"].isna() |
                              own_start_pos.isna() | own_death_pos.isna() |
                              ((own_death_pos - own_start_pos) >= 2))
        same_type = same_type[has_candle_between]
        # assign(dist=...) BEFORE filtering, not after: assigning a
        # non-empty Series onto an already-filtered (possibly zero-row)
        # frame pathologically reindexes to the Series' own length,
        # backfilling every original column with NaN instead of staying
        # empty -- a real pandas gotcha, not a hypothetical one (caught it
        # producing 199 all-NaN confluence rows here on a genuinely-out-
        # of-range trade whose nearest same-type H1 P0 was 15.75pt away,
        # just outside the 10pt radius).
        same_type = same_type.assign(dist=(same_type["price"] - res["fill_price"]).abs())
        near = same_type[same_type["dist"] <= H1_CONFL_RADIUS_PTS].sort_values("dist")
        confl = [{"type": r["type"], "price": float(r["price"]),
                  "formation_time": r["formation_time"],
                  "kind": _h1_p0_kind(r["type"], r["is_spike"], r["is_swing"]),
                  "dist": float(r["dist"]),
                  "dead_by_fill": bool(pd.notna(r["death_time"]) and r["death_time"] <= as_of)}
                 for _, r in near.iterrows()]
        res["h1_p0_confl"] = confl
    return results


SPIKE_CONFL_RADIUS_PTS = 10.0  # +/- band around the refined entry price for the H1 spike-confluence tag


def _apply_h1_spike_confluence(results):
    """Mutates and returns `results`. Dynamic-filter convention as
    _apply_globex_open_filter: a FILLED trade gets the row tag 'spike_confl'
    when an H1 SPIKE P0 of its own type sits within +/-SPIKE_CONFL_RADIUS_PTS
    of its REFINED entry price (res['alt_price'] -- after confluence/swerve/
    crest refinement, before any fill chase).

    Pairing (DETECTOR, see CLAUDE.md "Spike candles"): the H1 ledger's own
    is_spike, i.e. patterns-pure -- an H1 LHPB on a hammer (long trades), an
    H1 LLPB on a shooting star (short trades); the level price is the
    hammer's high / shooting star's low.

    The H1 P0 must have been UNTESTED up to the hour before the trade's
    retest (P2): formed by P2, and either never died or died no earlier than
    P2's hour floor minus H1_CONFL_HOUR_TOLERANCE (retest 01:15 -> still
    untested through 12:00). Same liveness test as _apply_h1_p0_confluence.
    Ships res['spike_confl'] = list of {kind, time, price, dist, death_time}
    nearest first ([] if none) for the badge tooltip."""
    h1_ledger = LC.h1_levels(verbose=False)
    spikes = h1_ledger[h1_ledger["is_spike"].astype(bool)]
    for res in results:
        if not res["filled"]:
            continue
        p2 = pd.Timestamp(res["row"]["retest_time"])
        window_low = p2.floor("h") - H1_CONFL_HOUR_TOLERANCE
        cand = spikes[(spikes["type"] == res["level_type"]) &
                      (spikes["formation_time"] <= p2) &
                      (spikes["death_time"].isna() | (spikes["death_time"] >= window_low))]
        cand = cand.assign(dist=(cand["price"] - float(res["alt_price"])).abs())
        near = cand[cand["dist"] <= SPIKE_CONFL_RADIUS_PTS].sort_values("dist")
        res["spike_confl"] = [{"kind": "hammer" if res["is_long"] else "shooting star",
                               "time": r["formation_time"], "price": float(r["price"]),
                               "dist": float(r["dist"]), "death_time": r["death_time"]}
                              for _, r in near.iterrows()]
        if res["spike_confl"]:
            res.setdefault("dyn_tags", []).append("spike_confl")
    return results


def _apply_h1_bias(results):
    """Mutates and returns `results` in place. Same dynamic-filter convention
    as _apply_globex_open_filter (see that function's docstring).

    For EVERY result -- filled or not, since the bias is read off the H1
    candles before the retest and so exists whether or not the entry ever
    filled -- stores res['h1_bias']: every H1 bias still live at this
    trade's own P2 (retest) instant, nearest candle first (h1_bias.py, which
    owns the whole definition: which candles make a bias, how long each one
    lasts and what kills it early). A trade whose own direction FADES at
    least one of them (a short under a live bullish bias, a long under a
    live bearish one) also gets the row tag 'anti_bias'.

    The bias is anchored on the RETEST instant, not the fill: the H1 candle
    the retest sits in is offset 0 and is still forming, so only candles that
    had already CLOSED by then can be a bias. Purely a review aid -- this
    strategy is M5-only (see the module docstring) and nothing here changes
    which trades it takes; 'anti_bias' ships UNCHECKED, so the default view
    still includes these trades."""
    h1_bars = R._display_h1()
    for res in results:
        biases = HB.biases_at(pd.Timestamp(res["row"]["retest_time"]), h1_bars)
        res["h1_bias"] = biases
        if HB.fades(biases, res["is_long"]):
            res.setdefault("dyn_tags", []).append("anti_bias")
    return results


N_COLS = 32  # keep in sync with `head` below and every colspan in this section

# MES position sizing / commissions. R stays a fixed $1,000 and the stop is not
# widened for costs: contracts = floor(R_DOLLARS / (stop pts x MES_POINT_VALUE)),
# and the round-trip commission is only reported (Commission column + chip), never
# folded into R or the sizing. Reference only -- nothing here changes which trades
# are taken or their R / PnL.
MES_POINT_VALUE = 5.0          # $ per point per MES contract
MES_COMMISSION_RT = 0.84       # $ round trip per MES contract
R_DOLLARS = 1000.0             # 1R
MES_HIGH_CONTRACTS = 50        # flag (not cap) sizes at/above this (stop <= 4 pts)
MES_MAX_STOP_PTS = R_DOLLARS / MES_POINT_VALUE   # 200 pts: beyond this 1 contract risks > 1R


def _mes_sizing(stop_pts):
    """(contracts, commission $, flag) for a trade with this stop distance.
    flag is '' , 'high' (>= MES_HIGH_CONTRACTS contracts) or 'wide' (stop over
    200 pts -> 0 whole contracts fit in 1R; left at 0, flagged for review)."""
    contracts = int(R_DOLLARS // (stop_pts * MES_POINT_VALUE))
    flag = ("wide" if stop_pts > MES_MAX_STOP_PTS
            else ("high" if contracts >= MES_HIGH_CONTRACTS else ""))
    return contracts, contracts * MES_COMMISSION_RT, flag


def _mes_cells(stop_pts):
    """(data attrs, contracts-cell inner HTML, commission-cell text, title) for a
    filled row. The browser blanks these on NO TARGET rows and restores them."""
    contracts, comm, flag = _mes_sizing(stop_pts)
    badge = {"": "",
             "high": f'<span class="mes-flag" title="Very high size: {contracts} '
                     f'contracts (stop {stop_pts:.2f} pts). Flagged for review, not capped.">'
                     f'HIGH SIZE</span>',
             "wide": f'<span class="mes-flag" title="Stop {stop_pts:.2f} pts is over '
                     f'{MES_MAX_STOP_PTS:g}: not even 1 MES contract fits in 1R '
                     f'(${R_DOLLARS:,.0f}). Sized 0, flagged for review.">STOP &gt;200</span>'}[flag]
    title = (f"${R_DOLLARS:,.0f} / ({stop_pts:.2f} pts x ${MES_POINT_VALUE:g}) rounded down = "
             f"{contracts} MES; x ${MES_COMMISSION_RT} round trip = ${comm:.2f}")
    attrs = (f'data-contracts="{contracts}" data-comm="{comm:.2f}" '
             f'data-mes-flag="{flag}"')
    return attrs, f"{contracts}{badge}", f"${comm:.2f}", title


def _fail_reason_label(reason):
    if not reason:
        return ""
    if reason.startswith("r_below_"):
        return f"NO TRADE (R below {reason.split('_below_', 1)[1]})"
    return {
        "unfilled_within_window": "UNFILLED (entry never reached)",
        "eod_entry_blocked": "NO TRADE (entry blocked -- end of day)",
        "no_target": "NO TRADE (no target under any rule)",
        "no_tick_data_after_fill": "NO DATA after fill",
        "no_m5_stop": "NO TRADE (no qualifying M5 breakout-candle stop)",
        "degenerate_stop": "NO TRADE (degenerate stop)",
    }.get(reason, reason.replace("_", " "))


def _target_title(info):
    """Tooltip for the Target cell, per target source (see _pick_targets)."""
    if not info:
        return "no target info"
    level = info.get("level")
    if info.get("src") == "swing_extreme":
        kind = "crest" if info["pivot_kind"] == "high" else "trough"
        return (f"Most recent confirmed zigzag {kind} (P1&hellip;P2): "
                f"{info['pivot_price']:.2f} at {R._to_pt_str(info['pivot_time'])} "
                f"-- the pivot's own price is the target")
    return (f"Farthest live M5 {level['type']} P0 after the zigzag pivot: "
            f"{R._to_pt_str(level['formation_time'])}; pivot "
            f"{info['pivot_price']:.2f} at {R._to_pt_str(info['pivot_time'])}")


def _compute_report_stats(filled):
    """(stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
    pctile_html) for a population of FILLED results -- the server-computed
    BASELINE (dynamic-filter-tagged trades, e.g. globex_eth_open, are still
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
        args.default_target_modes = DEFAULT_TARGET_MODES_BOTH
    if not getattr(args, "zz_threshold_pts", None):
        args.zz_threshold_pts = ZZ_THRESHOLD_PTS_DEFAULT
    if not getattr(args, "zz_min_bars", None):
        args.zz_min_bars = ZZ_MIN_BARS_DEFAULT
    if not np.isfinite(args.min_r) or args.min_r < 0:
        raise ValueError("--min-r must be finite and non-negative")
    if not np.isfinite(args.max_alt_fill_hours) or args.max_alt_fill_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")

    candidates = select_candidates(
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

    results, chart_stacks, fps = process_clusters(clusters, args)
    results = _apply_globex_open_filter(results)
    results = _apply_h1_p0_confluence(results)
    results = _apply_h1_spike_confluence(results)
    results = _apply_h1_bias(results)
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


def _mode_tag_badges(res, mode):
    """The dynamic-filter-tag badges belonging to ONE target rule's own
    outcome: its sub-min-R tag and its end-of-day flat tag. Both differ
    between the rules on the same row, so they are swapped (along with the
    rest of the mode-dependent cells, see _mode_payload) into the Tags
    column's own '.mode-tag-badges' span -- see applyTargetModes() in JS."""
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
    return out


def _mgmt_badge(res, mode):
    """The trade-management summary badge for ONE target rule's own
    outcome -- unlike _mode_tag_badges, this isn't a dynamic-filter tag (no
    Exclude/Only pair drives it), so it stays in the Outcome cell rather
    than the Tags column."""
    m = res["modes"][mode]
    mgmt = m["mgmt"] or {}
    if not mgmt.get("fired"):
        return ""
    bits = []
    if mgmt.get("trail_events"):
        bits.append(f"stop trailed x{len(mgmt['trail_events'])}")
    if mgmt.get("rr_floor_fired"):
        bits.append("RR-floor exit")
    mgmt_r_str = f"{mgmt['r']:+.2f}R" if mgmt.get("r") is not None else "?"
    return (f'<span class="dyn-tag-badge mgmt-tag-badge" '
            f'title="Trade management ({", ".join(bits)}) would change this '
            f'trade to {mgmt_r_str} ({mgmt.get("outcome")}). Toggle the Trade '
            f'management checkbox in the panel above to use it in the summary '
            f'stats.">MGMT {mgmt_r_str}</span>')


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
                f'{info.get("src", "m5_opposite_zz")}</span>'),
        "tgtTitle": _target_title(info),
        "rr": f'{m["r_multiple"]:.2f}',
        "rrVal": f'{m["r_multiple"]:.6f}',
        "outcomeLabel": outcome_label,
        "outcomeCls": outcome_cls,
        "modeTagBadges": _mode_tag_badges(res, mode),
        "mgmtBadge": _mgmt_badge(res, mode),
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


def _row_tag_badges(res, dyn_tags, level_type, entry_touch_str):
    """Badge HTML for the ROW-level dynamic-filter tags (globex_eth_open,
    low_liquidity, swerved, swerve_blocked, crest_refined, spike_confl,
    anti_bias, volume-spike) -- the ones that
    don't depend on which target rule is active, so they render once and
    never get rewritten by applyTargetModes() (unlike _mode_tag_badges'
    r_below_min/eod_flat). Lives in the Tags column (see _render_row).

    Shared by both the filled and unfilled row paths: swerve_blocked,
    crest_refined and anti_bias can land on either (swerve/crest_refine are
    decided before the fill-window search even runs, and the bias is read off
    the H1 candles before the retest), while globex_eth_open and
    low_liquidity only ever tag a FILLED result (their own apply_* filters
    skip unfilled rows), so those two branches are simply never reached
    when dyn_tags came from an unfilled row's tags alone."""
    out = ""
    if "globex_eth_open" in dyn_tags:
        out += (f'<span class="dyn-tag-badge" title="Dynamic filter '
                f'‘globex_eth_open’: this fill landed at {entry_touch_str}, inside the '
                f'daily Globex/ETH reopen window (15:00-15:05 PT) -- excluded by default. '
                f'Toggle the Dynamic filters checkbox in the panel above to include '
                f'it.">GLOBEX OPEN</span>')
    if "low_liquidity" in dyn_tags:
        out += (f'<span class="dyn-tag-badge liq-tag-badge" title="Dynamic filter '
                f'‘low_liquidity’: the tape was measurably illiquid at this fill '
                f'({LQ.describe(res.get("liquidity"))}) -- a news/thin-book window. '
                f'The strategy skips these; they are left out of the headline stats by '
                f'default.">NEWS / THIN</span>')
    if "swerved" in dyn_tags:
        sw = res["swerve"]
        sw_str = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
        out += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                f'‘swerved’: a confirmed M5 swing sat on the planned entry '
                f'{sw["planned_price"]:.2f} ({sw_str}), so the order was moved to the '
                f'next live M5 {level_type} level at {sw["price"]:.2f}.">'
                f'SWERVED {sw["planned_price"]:.2f}&rarr;{sw["price"]:.2f}</span>')
    if "swerve_blocked" in dyn_tags:
        sw = res["swerve"]
        sw_str = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
        out += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                f'‘swerve_blocked’: a confirmed M5 swing sat on the planned entry '
                f'{sw["planned_price"]:.2f} ({sw_str}) and no other live M5 {level_type} '
                f'level was available to move to, so this trade is NOT taken. It is '
                f'shown at its original entry so it can still be reviewed, and left out '
                f'of the headline stats by default.">SWERVE BLOCKED</span>')
    if "crest_refined" in dyn_tags:
        cr = res["crest_refine"]
        out += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                f'‘crest_refined’ (--crest-refine): the approach into '
                f'{cr["planned_price"]:.2f} from its own most recent confirmed swing '
                f'extreme {cr["crest_price"]:.2f} scored z={cr["z"]:.1f} against its own '
                f'trailing baseline -- an outlier-fast move -- so the entry was pushed '
                f'{cr["refine_pts"]:.2f}pt further to {cr["price"]:.2f}.">CREST REFINED '
                f'{cr["planned_price"]:.2f}&rarr;{cr["price"]:.2f}</span>')
    if "spike_confl" in dyn_tags:
        sc = res.get("spike_confl") or []
        detail = "; ".join(f'{c["kind"]} {c["price"]:.2f} ({c["dist"]:.2f}pt away), H1 bar {R._to_pt_str(c["time"])}'
                           for c in sc[:4])
        kind = "hammer" if level_type == "LHPB" else "shooting star"
        out += (f'<span class="dyn-tag-badge spike-confl-tag-badge" title="Dynamic filter '
                f'‘spike_confl’: an H1 {kind} P0 (patterns-pure), still untested until the hour '
                f'before this retest, with its {"high" if level_type == "LHPB" else "low"} within '
                f'&plusmn;{SPIKE_CONFL_RADIUS_PTS:g}pt of the refined entry '
                f'{res["alt_price"]:.2f}: {detail}. Purely informational; use the Only radio to '
                f'isolate these trades.">SPIKE CONFL</span>')
    if "anti_bias" in dyn_tags:
        faded = HB.fades(res.get("h1_bias") or [], level_type == "LHPB")
        detail = "; ".join(_bias_detail(b) for b in faded)
        out += (f'<span class="dyn-tag-badge anti-bias-tag-badge" title="Dynamic filter '
                f'‘anti_bias’: this {"long" if level_type == "LHPB" else "short"} is fading '
                f'{len(faded)} live {"bearish" if level_type == "LHPB" else "bullish"} H1 '
                f'bias{"es" if len(faded) != 1 else ""} -- {detail}. '
                f'See the Bias column for every live bias on this row. '
                f'Purely informational; not excluded from the headline stats by default.">'
                f'ANTI-BIAS</span>')
    if "volume-spike" in dyn_tags:
        side_word = "bid" if level_type == "LHPB" else "ask"
        vs = res.get("volume_spike") or {}
        offs_label = vs.get("offsets_label") or "0s"
        out += (f'<span class="dyn-tag-badge vol-spike-tag-badge" title="Dynamic filter '
                f'‘volume-spike’: unusual same-side ({side_word}) volume on the real '
                f'1s tape around this fill ({VS.describe(vs)}) -- worth reviewing for a stop '
                f'run. Purely informational; not excluded from the headline stats by default. '
                f'The CLOSEST qualifying second’s offset is also its own live numeric '
                f'filter (Volume spike offset, data-vspikeoffs) so a tight +/-10s read can be '
                f'compared against a wider one.">VOL SPIKE {offs_label}</span>')
    return out


def _h1_confl_cell(confl):
    """(cell_html, title) for the 'H1 P0 confl' column from
    res['h1_p0_confl'] (see _apply_h1_p0_confluence) -- a plain count of
    qualifying same-type H1 P0s, with no tooltip when the count is 0, else
    each one's own detail ('<price> <TYPE> <kind>', nearest first -- type
    is always the trade's own type, see _apply_h1_p0_confluence -- shown
    anyway for clarity) in the tooltip."""
    if not confl:
        return "0", ""
    title = "; ".join(
        f'{c["type"]} {c["price"]:.2f} {c["kind"]}, P0 {R._to_pt_str(c["formation_time"])}, '
        f'{c["dist"]:.2f}pt from fill'
        + (" (already retested by fill)" if c["dead_by_fill"] else "")
        for c in confl)
    return str(len(confl)), title


_BIAS_KIND_WORDS = {"hammer": "hammer", "star": "shooting star",
                    "sfp": "swing-failure sweep"}


def _bias_detail(b):
    """One live H1 bias, spelled out for a tooltip."""
    extreme = "low" if b["side"] == "bull" else "high"
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(-b["offset"], f'{-b["offset"]}th')
    swept = ""
    if b["kind"] == "sfp" and b["swing_price"] is not None:
        swept = (f', sweeping the untested swing {extreme} {b["swing_price"]:.2f} '
                 f'set at {R._to_pt_str(b["swing_time"])}')
    return (f'{b["label"]}: {_BIAS_KIND_WORDS[b["kind"]]} on the H1 candle at '
            f'{R._to_pt_str(b["time"])} ({extreme} {b["price"]:.2f}){swept}, '
            f'{"bullish" if b["side"] == "bull" else "bearish"}; '
            f'lives {b["max_age"]} closed H1 candle(s), this is its {ordinal}')


def _bias_cell(res):
    """(cell_html, title) for the 'Bias' column from res['h1_bias'] (see
    _apply_h1_bias) -- the live bullish biases then the live bearish ones,
    each as its own '<kind>@<offset>' metadata string, nearest H1 candle
    first. '-' with no tooltip when the row carries none."""
    biases = res.get("h1_bias") or []
    if not biases:
        return "-", ""
    bull, bear = HB.summarize(biases)
    out = ""
    if bull:
        out += f'<span class="bias-bull">&#9650; {" ".join(bull)}</span>'
    if bear:
        out += f'<span class="bias-bear">&#9660; {" ".join(bear)}</span>'
    return out, "; ".join(_bias_detail(b) for b in biases)


def _render_row(idx, res, chart_stacks, fps):
    """Builds (chart_entry_for_json, row_html) for one result row. A FILLED
    row carrying dynamic-filter tags (res['dyn_tags'], e.g. 'globex_eth_open' --
    see _apply_globex_open_filter's own docstring for the full convention)
    gets a data-dyn-tags attribute plus data-r/data-pnl-pts/data-outcome
    and a small badge, so the report's own JS can hide/show it and
    recompute win rate / avg R / total R / total PnL live from a filter
    checkbox -- no Python regen needed to explore excluding it."""
    if True:
        row_d = res["row"]
        level_type = res["level_type"]
        type_cls = "type-lhpb" if res["is_long"] else "type-llpb"
        retest_str = R._to_pt_str(row_d["retest_time"])
        gap_val = res.get("p1_p2_day_gap")
        gap_cell = (f'<span title="P1 (breakout) {R._to_pt_str(row_d["breakout_time"])} '
                   f'&rarr; P2 (retest) {retest_str}, {gap_val} trading day(s) apart '
                   f'(Globex/ETH reopen boundary, 15:00 PT)">{gap_val}</span>'
                   if gap_val is not None else '-')
        daygap_attr = gap_val if gap_val is not None else ""
        h1gap_val = res.get("p1_p2_h1_gap")
        h1gap_cell = (f'<span title="P1 (breakout) {R._to_pt_str(row_d["breakout_time"])} '
                     f'&rarr; P2 (retest) {retest_str}, {h1gap_val} H1 candle(s) close in between">'
                     f'{h1gap_val}</span>'
                     if h1gap_val is not None else '-')
        h1gap_attr = h1gap_val if h1gap_val is not None else ""
        mingap_val = res.get("p1_p2_minutes")
        mingap_cell = (f'<span title="P1 (breakout) {R._to_pt_str(row_d["breakout_time"])} '
                      f'&rarr; P2 (retest) {retest_str}, {mingap_val:.0f} minute(s) apart">'
                      f'{mingap_val:.0f}</span>'
                      if mingap_val is not None else '-')
        mingap_attr = f"{mingap_val:.2f}" if mingap_val is not None else ""
        vspike = res.get("volume_spike")
        vspikeoffs_attr = (str(min(abs(o) for o in vspike["offsets"]))
                           if "volume-spike" in (res.get("dyn_tags") or []) and vspike
                           else "")
        er_by_k = res.get("pre_p1_er_by_k") or []
        er_by_k_attr = _attr_json(er_by_k)
        er_default = (er_by_k[PRE_P1_ER_K_DEFAULT - 2]
                     if len(er_by_k) >= PRE_P1_ER_K_DEFAULT - 1 else None)
        prep1er_cell = f"{er_default:.2f}" if er_default is not None else "-"
        p1_ratio_by_window = res.get("p1_range_ratio_by_window") or []
        p1_ratio_by_window_attr = _attr_json(p1_ratio_by_window)
        p1_ratio_default = res.get("p1_range_ratio")
        p1ratio_cell = f"{p1_ratio_default:.2f}x" if p1_ratio_default is not None else "-"
        members_str = ", ".join(f"{p:.2f}" for p in res["cluster_members"])
        if res.get("cluster_size", 1) > 1:
            entry_title = (f' title="{res["cluster_size"]} mutually-confluent M5 levels '
                           f'merged into this one trade: {members_str}"')
            own_cell = f'<span class="cluster-tag"{entry_title}>{res["own_price"]:.2f}\u2020</span>'
        else:
            own_cell = f'{res["own_price"]:.2f}'
        # Same on both row paths: the bias is read off the H1 candles before
        # the retest, so an unfilled row has one just as a filled one does.
        bias_cell, bias_title = _bias_cell(res)

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
            # No target modes here (never filled, so nothing to swap on a
            # target-rule toggle) -- just whichever row-level tags applied
            # before the fill-window search ran (see _row_tag_badges: e.g.
            # swerve_blocked/crest_refined can land on an unfilled row,
            # globex_eth_open/low_liquidity never do).
            base_tags = list(res.get("dyn_tags") or [])
            # Shipped as data-dyn-tags on the unfilled row too, so Exclude /
            # Only act on these rows exactly as they do on a filled one. They
            # are never counted either way -- recomputeDynStats() drops every
            # .unfilled-row from the stats before it reads any tag -- this is
            # only about whether a tagged no-trade row stays visible.
            base_tags_attr = " ".join(base_tags)
            tags_cell = (f'<span class="row-badges">'
                        f'{_row_tag_badges(res, base_tags, level_type, entry_touch_str)}</span>')
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
    data-dyn-tags="{base_tags_attr}" data-base-tags="{base_tags_attr}"
    data-daygap="{daygap_attr}" data-h1gap="{h1gap_attr}" data-mingap="{mingap_attr}"
    data-vspikeoffs="{vspikeoffs_attr}"
    data-er-by-k="{er_by_k_attr}" data-p1-ratio-by-window="{p1_ratio_by_window_attr}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td>
  <td class="tags-cell">{tags_cell}</td>
  <td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="daygap-cell">{gap_cell}</td>
  <td class="h1gap-cell">{h1gap_cell}</td>
  <td class="mingap-cell">{mingap_cell}</td>
  <td class="prep1er-cell">{prep1er_cell}</td>
  <td class="p1ratio-cell">{p1ratio_cell}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td class="h1-confl-cell">-</td>
  <td class="bias-cell" title="{bias_title}">{bias_cell}</td>
  <td>{alt_cell}</td>
  <td class="left">{entry_touch_str}</td>
  <td>{stop_cell}</td>
  <td class="tgt-cell">-</td>
  <td class="rr-cell">-</td>
  <td class="outcome-cell"><span class="outcome-label">{_fail_reason_label(reason)}{rr_note}</span></td>
  <td class="left exit-cell">-</td><td class="exitpx-cell">-</td>
  <td class="pnl-cell">-</td>
  <td class="contracts-cell">-</td><td class="comm-cell">-</td>
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
    <div class="chart-row-2col">
      <div class="chart-cell chart-h1"><div class="chart-title" id="td1-{idx}"></div><div class="chart-ph" id="cd1-{idx}"></div></div>
      <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{idx}"></div><div class="chart-ph" id="ch1-{idx}"></div></div>
    </div>
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
        row_badges = _row_tag_badges(res, dyn_tags, level_type, entry_touch_str)
        tags_cell = (f'<span class="row-badges">{row_badges}</span>'
                    f'<span class="mode-tag-badges">{act["modeTagBadges"]}</span>')
        modes_attr = _attr_json(payloads)
        h1_confl_cell, h1_confl_title = _h1_confl_cell(res.get("h1_p0_confl"))
        mes_attrs, mes_contracts_html, mes_comm_text, mes_title = _mes_cells(res["stop_pts"])

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
    data-dyn-tags="{dyn_tags_attr}" data-base-tags="{base_tags_attr}" data-daygap="{daygap_attr}"
    data-h1gap="{h1gap_attr}" data-mingap="{mingap_attr}" data-vspikeoffs="{vspikeoffs_attr}"
    data-er-by-k="{er_by_k_attr}"
    data-p1-ratio-by-window="{p1_ratio_by_window_attr}"
    data-r="{act['r']}" data-rr="{act['rrVal']}" data-pnl-pts="{act['pnlPts']}" data-outcome="{act['outcome']}"
    data-mgmt-r="{act['mgmtR']}" data-mgmt-pnl-pts="{act['mgmtPnl']}"
    data-mgmt-outcome="{act['mgmtOutcome']}" data-mgmt-fired="{act['mgmtFired']}"
    data-mode="{active_mode}" data-modes="{modes_attr}" {mes_attrs}
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td>
  <td class="tags-cell">{tags_cell}</td>
  <td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="daygap-cell">{gap_cell}</td>
  <td class="h1gap-cell">{h1gap_cell}</td>
  <td class="mingap-cell">{mingap_cell}</td>
  <td class="prep1er-cell">{prep1er_cell}</td>
  <td class="p1ratio-cell">{p1ratio_cell}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td class="h1-confl-cell" title="{h1_confl_title}">{h1_confl_cell}</td>
  <td class="bias-cell" title="{bias_title}">{bias_cell}</td>
  <td>{res['alt_price']:.2f}<span class="gap-slot">{act['gap']}</span><span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td title="{stop_title}">{res['stop_price']:.2f}<span class="src-tag m5">{stop_source}</span></td>
  <td class="tgt-cell" title="{act['tgtTitle']}">{act['tgt']}</td>
  <td class="rr-cell">{act['rr']}</td>
  <td class="outcome-cell {act['outcomeCls']}"><span class="outcome-label">{act['outcomeLabel']}</span><span class="mgmt-badge">{act['mgmtBadge']}</span></td>
  <td class="left exit-cell">{act['exit']}</td><td class="exitpx-cell">{act['exitPx']}</td>
  <td class="pnl-cell {act['pnlCls']}">{act['pnl']}</td>
  <td class="contracts-cell" title="{mes_title}">{mes_contracts_html}</td><td class="comm-cell" title="{mes_title}">{mes_comm_text}</td>
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
    <div class="chart-row-2col">
      <div class="chart-cell chart-h1"><div class="chart-title" id="td1-{idx}"></div><div class="chart-ph" id="cd1-{idx}"></div></div>
      <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{idx}"></div><div class="chart-ph" id="ch1-{idx}"></div></div>
    </div>
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
        f"P1 AND ITS FILL (wholly after its breakout candle, wholly before the candle it fills in) and both "
        f"bounded to {MIN_DYNAMIC_TARGET_PTS:g}&ndash;{MAX_DYNAMIC_TARGET_PTS:g} points from "
        f"the fill on the favourable side. (1) OPPOSITE M5 LEVEL, ZIGZAG ANCHOR: "
        f"the most recent CONFIRMED zigzag trough (short) / crest (long) in that window (a "
        f"&ge;{args.zz_threshold_pts:g}pt reversal off bar highs/lows, confirmed "
        f"&ge;{args.zz_min_bars} bars after the extreme, or a &ge;{MS.ZIGZAG_FAST_THRESHOLD_DEFAULT:g}pt "
        f"reversal &ge;{MS.ZIGZAG_FAST_MIN_BARS_DEFAULT} bars after it), then the FARTHEST live opposite-type "
        f"M5 P0 formed after it (lowest for a short, highest for a long), no shared-P1 confluence required "
        f"(src tag m5_opposite_zz). (2) SWING EXTREME: the most recent confirmed zigzag "
        f"trough (long) / crest (short) formed between P1 and P2, with the pivot's own price as the target "
        f"(src tag swing_extreme). Both are precomputed for every trade and both are "
        f"live checkboxes in the Target rules row of the panel above: untick one and every row "
        f"re-resolves against the other, or becomes a dimmed NO TARGET row "
        f"if neither is on. With both ticked (the default here: "
        f"{' + '.join(args.default_target_modes)}) a trade takes whichever rule offers the "
        f"FARTHER target.")
    reason_html = "".join(
        f'<div class="box"><strong>{n}</strong>{reason}</div>'
        for reason, n in sorted(reason_counts.items(), key=lambda kv: -kv[1]))
    total_pnl_pts = sum(r["resolved"]["r"] * r["stop_pts"] for r in filled
                       if r["resolved"].get("r") is not None)
    total_comm = sum(_mes_sizing(r["stop_pts"])[1] for r in filled
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
        (f"${total_comm:,.2f}", "commissions (MES)", "sum-total-comm", False),
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
    <span class="filter-label" title="Reward:risk on offer at entry (the R column: target pts /
stop pts, fixed regardless of win or loss). Reads tr.dataset.rr, which applyTargetModes() keeps
in sync with whichever target rule is ticked above -- the same generic op/value numeric-filter
mechanism (f-num-op/f-num-val, data-target) already used for Confl./SS Confl. in
render_stop_target_report.py's shared review panel (see numFilterOk/applyReviewFilters there).
Unlike those, this one is also a DYNAMIC filter: changing it recomputes win rate / avg R / total
R / total PnL above live, the same as the Dynamic filters row below, in addition to hiding rows.
Defaults to &ge; 0.5 (only R &ge; 0.5 shown); pick any to show every row.">R</span>
    <select class="f-num-op" data-target="rr">
      <option value="any">any</option>
      <option value="gte" selected>&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="rr" value="0.5" min="0" max="5" step="0.1">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="How many Globex/ETH reopen-to-reopen trading days (15:00 PT
boundary) separate this level's own P1 (breakout) from its P2 (retest): 0 when both fall in the
same session-to-session window, 1 when the retest is the very next trading day, etc. -- see
trading_day_gap in trade_management.py. Reads tr.dataset.daygap, the SAME generic op/value
numeric-filter mechanism (f-num-op/f-num-val, data-target) as R above, and is likewise a DYNAMIC
filter: changing it recomputes win rate / avg R / total R / total PnL above live, not just which
rows are shown. Defaults to any (no filtering) -- pick, e.g., &lt; 1 to keep only same-day
retests, or &le; 2 to allow retests up to 2 trading days later.">P1&rarr;P2 gap</span>
    <select class="f-num-op" data-target="daygap">
      <option value="any" selected>any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="daygap" value="1" min="0" step="1">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="How many whole H1 candles CLOSE strictly between this
level's own P1 (breakout) and its P2 (retest) -- see h1_bar_gap in trade_management.py. 0
when both fall inside the same H1 bar or in two back-to-back bars (nothing closes between
them), 1 when exactly one H1 bar's close sits between them, etc. Reads tr.dataset.h1gap, the
SAME generic op/value numeric-filter mechanism (f-num-op/f-num-val, data-target) as R and the
P1&rarr;P2 gap above, and is likewise a DYNAMIC filter: changing it recomputes win rate /
avg R / total R / total PnL above live, not just which rows are shown. Defaults to &ge; 3
-- pick any to show every row.">P1&rarr;P2 gap (H1)</span>
    <select class="f-num-op" data-target="h1gap">
      <option value="any">any</option>
      <option value="gte" selected>&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="h1gap" value="3" min="0" step="1">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Minutes elapsed between this level's own P1 (breakout)
and its P2 (retest) -- a plain subtraction of their own M5 bar timestamps. Reads
tr.dataset.mingap, the SAME generic op/value numeric-filter mechanism (f-num-op/f-num-val,
data-target) as R and the two gap filters above, and is likewise a DYNAMIC filter: changing
it recomputes win rate / avg R / total R / total PnL above live, not just which rows are
shown. Defaults to any (no filtering).">P1&rarr;P2 gap (min)</span>
    <select class="f-num-op" data-target="mingap">
      <option value="any" selected>any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="mingap" value="30" min="0" step="5">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Kaufman efficiency ratio (R._efficiency_ratio: net move /
total path traveled over k M5 closes; 0 = round-tripped chop, 1 = a straight run) of the k M5
closes immediately BEFORE this level's own P1 (breakout) bar -- same measure lxpb.py's own
candidate gate uses (lxpb.ER_CONSOLIDATION_MAX = 0.5),
aimed backward from the breakout instead of forward from a level's own formation. A trade that
breaks out of a trending/grinding run rather than a contained, overlapping range has a HIGH ER
here (no structure); a trade that breaks out of real consolidation has a LOW one. BOTH k and the
cutoff below are live in the browser -- changing either recomputes tr.dataset.er from the row's
own precomputed k=2..__PRE_P1_MAX_K__ array (no ratio math client-side, no regen) and reads back
through the SAME generic op/value numeric-filter mechanism (f-num-op/f-num-val, data-target) as
R and the three gap filters above. Likewise a DYNAMIC filter: changing k or the cutoff recomputes
win rate / avg R / total R / total PnL live, not just which rows are shown. Defaults to &le;
__PRE_P1_ER_MAX__ with k=__PRE_P1_K__ pre-filled (real consolidation just before P1) -- pick any
to show every row.">Pre-P1 structure</span>
    <span class="filter-sublabel">k=</span>
    <input type="number" id="f-prep1-k" value="__PRE_P1_K__" min="2" max="__PRE_P1_MAX_K__" step="1">
    <select class="f-num-op" data-target="er">
      <option value="any">any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte" selected>&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="er" value="__PRE_P1_ER_MAX__" min="0" max="1" step="0.05">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser toggle -- no Python regen needed. Win =
outcome-cell reads WIN under the target rule / trade-management state currently ticked above.
Loss = every other row that DID resolve to a numeric R (LOSS, EOD FLAT, RR FLOOR, CANDLE, all
scored by the sign of their R). No trade = unfilled rows (Python never found a valid stop/target)
plus rows currently showing NO TARGET (rule off) because the ticked target rule(s) above found
nothing for them, and any filled row that never resolved (NO-HIT). This reads the SAME per-row
outcome recomputeDynStats() already uses for the win-rate/avg-R summary above, so it always
agrees with those numbers.">Outcome</span>
    <label class="chip"><input type="checkbox" class="f-cb f-outcome" value="win" checked> Win</label>
    <label class="chip"><input type="checkbox" class="f-cb f-outcome" value="loss" checked> Loss</label>
    <label class="chip"><input type="checkbox" class="f-cb f-outcome" value="no_trade"> No trade</label>
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser trade-exclusion toggles -- no Python regen
needed. Checking Exclude hides those rows AND recomputes win rate / avg R / total R / total PnL
above from only the remaining (not excluded) trades. Picking the Only radio under it instead
hides every OTHER row (any Only radio picked takes priority over every Exclude box; all the Only
radios share one group, so picking one clears any other -- click the same radio again to turn it
back off). To add another dynamic filter: tag qualifying results with an entry in
res['dyn_tags'] (Python side) and add one more chip-stack (Exclude checkbox + Only radio) here
with class f-dyn-exclude/f-dyn-isolate and data-tag matching that tag -- see
_apply_globex_open_filter's docstring in render_m5_confl2_report.py for the full
convention.">Dynamic filters</span>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="globex_eth_open">
        Exclude Globex/ETH open fills (15:00-15:05 PT)</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged globex_eth_open, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="globex_eth_open"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="low_liquidity">
        Exclude news / thin-book entries</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged low_liquidity, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="low_liquidity"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="swerve_blocked">
        Exclude swerve-blocked (not taken)</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged swerve_blocked, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="swerve_blocked"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="eod_flat">
        Exclude end-of-day flats</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged eod_flat, hiding every other
row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="eod_flat"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="volume-spike">
        Exclude volume-spike fills</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged volume-spike, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="volume-spike"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="spike_confl">
        Exclude H1 spike-confluence trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged spike_confl (an H1 hammer P0 for LHPB longs / shooting star P0 for LLPB shorts, untested until the retest hour minus 1h, within +/-10pt of the refined entry), hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="spike_confl"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="anti_bias">
        Exclude anti-bias trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged anti_bias (the trade's own
direction fades at least one H1 bias that was still live at its retest -- a short under a live
bullish bias, a long under a live bearish one; see the Bias column), hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate" data-tag="anti_bias"> only</label>
    </div>
  </div>
  <div class="filter-row">
    <span class="filter-label" title="How many seconds before(-)/after(+) the fill the CLOSEST
qualifying volume-spike second landed (0 = the exact fill second; a row can have several
qualifying seconds -- see the badge -- this is only the nearest one to the fill) -- only rows
tagged volume-spike carry a value here, so this only narrows THAT set, same as every other row
here being ANDed together. Reads tr.dataset.vspikeoffs (the ABSOLUTE offset), the SAME generic
op/value numeric-filter mechanism (f-num-op/f-num-val, data-target) as R and the gap filters
above, and is likewise a DYNAMIC filter: changing it recomputes win rate / avg R / total R /
total PnL above live. Defaults to any (no filtering) -- pick, e.g., &le;10 to see only spikes
within 10s of the fill vs &le;30 for the full core window.">Volume spike offset (|s| from fill)</span>
    <select class="f-num-op" data-target="vspikeoffs">
      <option value="any" selected>any</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
      <option value="eq">=</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="vspikeoffs" value="10" min="0" step="1">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="This level's OWN P1 (breakout) candle range, divided by its
trailing w-bar M5 average range (the average EXCLUDES the bar itself) -- the same range-ratio
calc render_labels_report.py uses to flag a 'wide breakout' H1 P1, just run on M5 bars. A thin
ratio here is a thin thrust that barely out-ranged the recent tape. BOTH the window w and the
cutoff below are live in the browser -- changing either recomputes tr.dataset.p1ratio from the
row's own precomputed w=1..__WEAKP1_MAX_WINDOW__ array (data-p1-ratio-by-window; no ratio math
client-side, no regen) and its own 'P1 range ratio' column, and reads back through the SAME
generic op/value numeric-filter mechanism (f-num-op/f-num-val, data-target) as R and the Pre-P1
structure filter above. Likewise a DYNAMIC filter: changing the window or the cutoff recomputes
win rate / avg R / total R / total PnL live, not just which rows are shown. Defaults to &ge;
__WIDE_RATIO__x with window=__WEAKP1_WINDOW__ pre-filled -- pick, e.g., &lt; __WIDE_RATIO__x to
keep only the thin, unconvincing breakouts instead.">P1 range ratio</span>
    <span class="filter-sublabel">window=</span>
    <input type="number" id="f-weakp1-window" value="__WEAKP1_WINDOW__" min="1" max="__WEAKP1_MAX_WINDOW__" step="1">
    <select class="f-num-op" data-target="p1ratio">
      <option value="any">any</option>
      <option value="gte" selected>&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="p1ratio" value="__WIDE_RATIO__" min="0.1" max="3" step="0.1">
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Which TARGET RULE(S) each trade exits on -- live, in the
browser, with no Python regen. Every trade ships with EVERY rule's brackets and outcomes
precomputed, so unticking a rule re-resolves each row against whichever others are still on:
a trade can become a NO TARGET row (dimmed, dropped from the stats) if the rule it was using
was the only one that found a target. With several ticked, each trade uses whichever rule offers
the FARTHER target. Untick all and no trade has a target at all. The charts below always draw
the bracket the page was GENERATED with -- regen to chart a different default.">Target rules</span>
    <label class="chip"><input type="checkbox" class="f-target-mode" data-mode="opposite-m5-zz" __OPPZZ_CHECKED__>
      Opposite M5 level, zigzag anchor (P1&hellip;fill)</label>
    <label class="chip"><input type="checkbox" class="f-target-mode" data-mode="swing-extreme" __SWING_CHECKED__>
      Swing extreme: last zigzag trough/crest (P1&hellip;P2)</label>
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
        "Whichever target rule is ticked in the Target rules row above, and with several ticked "
        "the one offering the FARTHER target. m5_opposite_zz: the farthest live opposite M5 P0 (lowest for a "
        "short, highest for a long; no shared-P1 confluence required) formed after the most recent "
        "confirmed zigzag trough (short) / crest (long) in that window. swing_extreme: the "
        "most recent confirmed zigzag trough (long) / crest (short) formed between P1 and P2, at its own price.")
    head = (f"<th class=\"left\">Trade Id</th>"
            f"<th class=\"left\" title=\"Every dynamic-filter tag this row carries, in one "
            f"place: globex_eth_open, low_liquidity, swerved/swerve_blocked, crest_refined, "
            f"spike_confl, anti_bias, volume-spike "
            f"(row-level, constant across target rules) plus r_below_min/eod_flat (the "
            f"ACTIVE target rule's own -- these swap along with Target/R/Outcome when you "
            f"toggle a Target rules checkbox above). Hover a badge for its own detail; the "
            f"matching Dynamic filters checkbox/radio above still drives Exclude/Only on "
            f"it.\">Tags</th>"
            f"<th class=\"left\">Type</th>"
            f"<th class=\"left\">M5 retest</th>"
            f"<th title=\"Whole trading days between this level's own P1 (breakout) and its "
            f"P2 (retest), under the Globex/ETH reopen boundary (15:00 PT / 18:00 ET): 0 when "
            f"both fall in the same reopen-to-reopen session, 1 when the retest is the very "
            f"next trading day, etc.\">P1&rarr;P2 (d)</th>"
            f"<th title=\"Whole H1 candles that CLOSE strictly between this level's own P1 "
            f"(breakout) and its P2 (retest) (trade_management.py's h1_bar_gap): 0 when both "
            f"fall inside the same H1 bar or in two back-to-back bars, 1 when exactly one H1 "
            f"bar's close sits between them, etc.\">P1&rarr;P2 (H1)</th>"
            f"<th title=\"Minutes between this level's own P1 (breakout) and its P2 (retest), "
            f"a plain subtraction of their own M5 bar timestamps.\">P1&rarr;P2 (min)</th>"
            f"<th title=\"Kaufman efficiency ratio (net move / total path traveled; 0 = "
            f"round-tripped chop, 1 = a straight run) of the k M5 closes immediately BEFORE "
            f"this level's own P1 (breakout) bar -- same measure lxpb.py's own "
            f"candidate gate uses "
            f"(R._efficiency_ratio), aimed backward from the breakout instead of forward from "
            f"a level's own formation. k (default {PRE_P1_ER_K_DEFAULT}) and the filter cutoff "
            f"below are both live in the Pre-P1 structure filter above -- no regen "
            f"needed.\">Pre-P1 ER</th>"
            f"<th title=\"This level's own P1 (breakout) candle range, divided by its "
            f"trailing w-bar M5 average range (the average EXCLUDES the bar itself) -- the "
            f"same range-ratio calc render_labels_report.py uses to flag a 'wide breakout' "
            f"H1 P1, just run on M5 bars. A thin ratio is a thin thrust that barely "
            f"out-ranged the recent tape. The window w (default "
            f"{M5_RANGE_RATIO_WINDOW_DEFAULT}) and the filter cutoff below are both live in "
            f"the P1 range ratio filter above -- no regen needed.\">P1 range ratio</th>"
            f"<th class=\"left\" title=\"Distinct M5 prices merged into this trade, "
            f"extreme-first: highest for LLPB, lowest for LHPB. "
            f"Single-level trades show their own M5 price.\">Merged M5 levels</th>"
            f"<th title=\"The level's original M5 entry price, before fine-tuning to the "
            f"confluence group's extreme price\">Own</th>"
            f"<th title=\"Every H1 P0 of THIS TRADE'S OWN LXPB TYPE (LHPB trade -> H1 LHPB "
            f"only, LLPB -> LLPB only), within +/-{H1_CONFL_RADIUS_PTS:g}pt of the actual fill "
            f"price, that had already formed and was STILL UNTESTED up to around the clock "
            f"hour of this trade's own P2 (retest) -- +/-{H1_CONFL_HOUR_TOLERANCE.seconds // 3600}hr "
            f"tolerance on dying early, no limit on dying late or never. Fractal confluence: "
            f"was the H1 version of this exact pattern still live when M5 confirmed it. "
            f"Also requires at least one H1 candle "
            f"between the H1 P0's OWN start (its breakout bar, or its formation bar if it "
            f"never closed through) and its own death -- an immediate next-bar snap-back is "
            f"a wick round-trip, not real confluence. Any fate otherwise. This "
            f"strategy is M5-only (no H1 input); purely a review aid. Shows the COUNT of "
            f"qualifying H1 P0s (0 if none); hover for each one's own price/kind/formation "
            f"time.\">H1 P0 confl (&plusmn;{H1_CONFL_RADIUS_PTS:g}pt)</th>"
            f"<th class=\"left\" title=\"Every H1 directional bias still LIVE at this trade's "
            f"own P2 (retest), as its own '&lt;pattern&gt;@&lt;H1 candles back&gt;' tag -- "
            f"bullish ones (&#9650;) first, then bearish (&#9660;), nearest candle first. "
            f"A bias is plain candle context, not a level: a hammer (bullish) or shooting "
            f"star (bearish) by patterns-pure, and an sfp (patterns-pure's find_sfp) -- a "
            f"candle that sweeps an H1 swing low and closes back above it (bullish), or "
            f"sweeps a swing high and closes back below it (bearish). The swing swept must "
            f"be the most recent {HB.SWING_K}-bar fractal pivot that is BOTH confirmed and "
            f"still UNTESTED -- no bar since it formed has reached it. A swing traded "
            f"through once is spent, so one swing yields at most one sfp. "
            f"Offset 0 is the H1 candle the retest itself sits in; it is still forming, so "
            f"only already-CLOSED candles can carry a bias and '@-1' is the last closed one. "
            f"All four die the same ways: the very next candle thrusts it through, or a "
            f"later candle trades one tick past its own low (bullish) / high (bearish), or "
            f"it goes stale -- after {HB.HAMMER_BIAS_MAX_AGE} closed candles for a "
            f"hammer/star, {HB.SFP_BIAS_MAX_AGE} for an sfp, the only difference between "
            f"them. "
            f"A trade fading one of these is tagged anti_bias. This strategy is M5-only "
            f"(no H1 input); purely a review aid. Hover for each bias's own detail.\">Bias</th>"
            f"<th>Entry</th>"
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
            f"<th title=\"MES contracts for 1R = ${R_DOLLARS:,.0f}: floor(${R_DOLLARS:,.0f} / "
            f"(stop pts x ${MES_POINT_VALUE:g})). Commission is NOT in the sizing. HIGH SIZE = "
            f"&ge;{MES_HIGH_CONTRACTS} contracts; STOP &gt;200 = 0 contracts fit. Flagged, not "
            f"capped or skipped\">MES</th>"
            f"<th title=\"Round-trip commission = contracts x ${MES_COMMISSION_RT} per MES. "
            f"Reported only; R and PnL are gross of it\">Comm.</th>"
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
{filter_panel.replace("__WIDE_RATIO__", f"{WIDE_M5_BREAKOUT_RATIO_THRESHOLD:g}")
   .replace("__PRE_P1_K__", f"{PRE_P1_ER_K_DEFAULT:g}")
   .replace("__PRE_P1_MAX_K__", f"{PRE_P1_ER_MAX_K:g}")
   .replace("__PRE_P1_ER_MAX__", f"{PRE_P1_ER_MAX_DEFAULT:g}")
   .replace("__WEAKP1_WINDOW__", f"{M5_RANGE_RATIO_WINDOW_DEFAULT:g}")
   .replace("__WEAKP1_MAX_WINDOW__", f"{M5_RANGE_RATIO_MAX_WINDOW:g}")
   .replace("__OPPZZ_CHECKED__",
            "checked" if "opposite-m5-zz" in args.default_target_modes else "")
   .replace("__SWING_CHECKED__",
            "checked" if "swing-extreme" in args.default_target_modes else "")}
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
td.h1-confl-cell { max-width:220px; white-space:normal; cursor:help; }
td.bias-cell { max-width:150px; white-space:normal; cursor:help; }
/* Bullish / bearish groups inside the Bias cell (see _bias_cell). Stacked,
   so a row carrying both reads as two lines rather than one run-on string. */
.bias-bull, .bias-bear { display:block; font-size:0.86em; white-space:nowrap; }
.bias-bull { color:#6ee7b7; }
.bias-bear { color:#fda4af; }
td.tags-cell { max-width:200px; white-space:normal; }
/* Dynamic filters (see _apply_globex_open_filter's docstring): a row tagged
   res['dyn_tags'] gets data-dyn-tags plus this badge; the matching
   f-dyn-exclude checkbox hides it via .dyn-hidden (kept separate from the
   review-workflow filters' own .hidden class so the two systems never
   fight over one class -- either one hides the row, independently). */
tr.lvl-row.dyn-hidden, tr.chart-row.dyn-hidden { display:none !important; }
/* Outcome filter (Win/Loss/No trade chips): same independent-hide pattern
   as .dyn-hidden above, toggled in recomputeDynStats() from whichever
   outcome bucket a row currently falls in. */
tr.lvl-row.outcome-hidden, tr.chart-row.outcome-hidden { display:none !important; }
.dyn-tag-badge { display:inline-block; margin-left:6px; padding:1px 6px; font-size:0.72em;
                border-radius:3px; background:#4a3010; color:#fbbf24; cursor:help; }
/* Exclude checkbox + Only radio, stacked as one unit per dynamic-filter tag
   (see _apply_globex_open_filter's docstring) instead of sitting side by
   side as two separate chips. */
.chip-stack { display:flex; flex-direction:column; align-items:flex-start; gap:3px; }
/* MES sizing flags; NO TARGET rows are not trades, so hide their sizing/commission. */
.mes-flag { margin-left:5px; padding:0 5px; border-radius:3px; font-size:0.72em; font-weight:600;
            background:#fde8c8; color:#8a4b00; white-space:nowrap; }
.no-target-row .contracts-cell > *, .no-target-row .comm-cell { visibility:hidden; }
.chip-iso { opacity:0.7; font-size:0.78em; padding:1px 9px 1px 7px; }
.chip-iso:hover { opacity:1; }
.chip-iso input[type="radio"] { margin-right:5px; }
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
.vol-spike-tag-badge { background:#3f2d0e; color:#fdba74; }
.spike-confl-tag-badge { background:#0e3a2f; color:#6ee7b7; }
.anti-bias-tag-badge { background:#3a1030; color:#f0abfc; }
/* Notes box: this report is reviewed with long, written-out notes per trade,
   so it ships far larger than the shared 160x34 default in
   render_stop_target_report.CSS (left alone, for every other report) and
   resizes in both directions rather than only vertically. */
textarea.trade-note { width:360px; height:150px; resize:both; }
th.sortable-th { cursor:pointer; user-select:none; }
th.sortable-th:hover { text-decoration:underline; }
</style>
"""
JS = SR.JS + """
<script>
// D1 pane above the M5 chart (H1 reuses the shared _renderH1 / th1- / ch1- ids).
const _renderStackBase = _renderStack;
_renderStack = function(i) {
  _renderStackBase(i);
  const cd = CHARTS[i];
  if (!cd) return;
  if (cd.d1) _renderPane('cd1-' + i, 'td1-' + i, cd.d1);
  else { const t = document.getElementById('td1-' + i); if (t) t.textContent = 'D1  |  (no bars covering this trade)'; }
  if (!cd.h1) { const t = document.getElementById('th1-' + i); if (t) t.textContent = 'H1  |  (no bars covering this trade)'; }
};
// ---------------------------------------------------------------------
// Dynamic filters -- see _apply_globex_open_filter's own docstring in
// render_m5_confl2_report.py for the full authoring convention (this is
// the intentionally-generic, tag-agnostic half of it). Each
// f-dyn-exclude checkbox's data-tag names a tag a Python-side filter may
// have added to a row's data-dyn-tags (space-separated -- a row can carry
// more than one). Checking an Exclude box hides every row carrying that
// tag; picking the f-dyn-isolate RADIO stacked under it instead hides
// every row NOT carrying that tag (any Isolate radio picked takes
// priority over every Exclude box). All f-dyn-isolate radios share one
// name="f-dyn-isolate-radio" group, so the browser itself enforces at
// most one isolated tag at a time -- see the click handler below, which
// unchecks the radio again when it's clicked while already selected
// (native radios can't self-clear). Both act via the SAME .dyn-hidden
// class, kept deliberately separate from the review-workflow filters'
// .hidden class above (applyReviewFilters, in the shared JS) so the two
// systems never fight over one class; a row is invisible if EITHER is
// set -- and recomputes the win rate / avg R / total R / total PnL
// summary boxes from only the remaining (not hidden) trades. To add
// another dynamic filter: tag qualifying results with one more entry in
// res['dyn_tags'] (Python side) and add one more <div class="chip-stack">
// holding an <input class="f-dyn-exclude" data-tag="..."> checkbox and an
// <input type="radio" name="f-dyn-isolate-radio" class="f-dyn-isolate"
// data-tag="..."> to the filter panel -- recomputeDynStats() below needs
// no changes for a new tag, it reads whatever tags are present.
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
// The M5 pane's own target-dependent parts (see _add_mode_views): swap the
// default rule's target ray / exit + management
// markers for the ticked rule's, and redraw the pane if it is already open.
function chartForMode(idx, mode) {
  const cd = CHARTS[idx];
  const m5 = cd && cd.m5;
  if (!m5 || !m5.modeViews) return;
  if (!m5._base) {
    m5._base = {
      rays: m5.rays.filter(r => !(r.title || '').startsWith('target')),
      markers: m5.markers.filter(m => !/^(WIN|LOSS|CANDLE|RR FLOOR EXIT|Stop →)/.test(m.text || '')),
    };
    m5._shown = m5.activeMode;
  }
  if (m5._shown === mode) return;
  m5._shown = mode;
  const v = m5.modeViews[mode];
  m5.rays = m5._base.rays.concat(v ? [v.ray] : []);
  m5.markers = m5._base.markers.concat(v ? v.markers : []).sort((a, b) => a.time - b.time);
  if (rendered[idx]) {
    const el = document.getElementById('cm5-' + idx);
    if (el) { el.innerHTML = ''; _renderM5(idx, m5); }
  }
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
      tr.dataset.r = ''; tr.dataset.rr = ''; tr.dataset.pnlPts = ''; tr.dataset.outcome = '';
      tr.dataset.mgmtR = ''; tr.dataset.mgmtPnlPts = ''; tr.dataset.mgmtOutcome = '';
      tr.dataset.mgmtFired = '0';
      setCell(tr, '.tgt-cell', '-', 'tgt-cell');
      const tgt = tr.querySelector('.tgt-cell');
      if (tgt) tgt.title = 'No target under the target rules currently ticked';
      setCell(tr, '.rr-cell', '-', 'rr-cell');
      setCell(tr, '.outcome-cell',
              '<span class="outcome-label">NO TARGET (rule off)</span>'
              + '<span class="mgmt-badge"></span>',
              'outcome-cell');
      const noTgtMtb = tr.querySelector('.tags-cell .mode-tag-badges');
      if (noTgtMtb) noTgtMtb.innerHTML = '';
      setCell(tr, '.exit-cell', '-', 'left exit-cell');
      setCell(tr, '.exitpx-cell', '-', 'exitpx-cell');
      setCell(tr, '.pnl-cell', '-', 'pnl-cell');
      setCell(tr, '.mae-cell', '-', 'mae-cell');
      setCell(tr, '.mfe-cell', '-', 'mfe-cell');
      setCell(tr, '.gb-cell', '-', 'gb-cell');
      setCell(tr, '.gap-slot', '');
      chartForMode(tr.dataset.idx, '');
      return;
    }
    const p = modes[pick];
    tr.classList.remove('no-target-row');
    tr.dataset.mode = pick;
    tr.dataset.dynTags = (baseTags + ' ' + (p.tags || '')).trim();
    tr.dataset.r = p.r; tr.dataset.rr = p.rrVal; tr.dataset.pnlPts = p.pnlPts; tr.dataset.outcome = p.outcome;
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
      const mgb = outcomeCell.querySelector('.mgmt-badge');
      if (mgb) mgb.innerHTML = p.mgmtBadge;
    }
    const mtb = tr.querySelector('.tags-cell .mode-tag-badges');
    if (mtb) mtb.innerHTML = p.modeTagBadges;
    setCell(tr, '.exit-cell', p.exit, 'left exit-cell');
    setCell(tr, '.exitpx-cell', p.exitPx, 'exitpx-cell');
    setCell(tr, '.pnl-cell', p.pnl, 'pnl-cell ' + (p.pnlCls || ''));
    setCell(tr, '.mae-cell', p.mae, 'mae-cell bad');
    setCell(tr, '.mfe-cell', p.mfe, 'mfe-cell good');
    setCell(tr, '.gb-cell', p.gb, 'gb-cell');
    setCell(tr, '.gap-slot', p.gap);
    chartForMode(tr.dataset.idx, pick);
  });
}
function activeOutcomeBuckets() {
  return Array.from(document.querySelectorAll('.f-outcome:checked')).map(cb => cb.value);
}
// ---------------------------------------------------------------------
// Pre-P1 structure filter -- see _pre_p1_er_by_k's docstring in
// render_m5_confl2_report.py. Each row ships its WHOLE k=2..max array
// (tr.dataset.erByK) rather than one fixed reading, so the ratio formula
// itself never has to be restated here -- this only re-indexes that array
// for whichever k the f-prep1-k control currently holds, the same "swap a
// live-picked field" trick applyTargetModes uses for tr.dataset.rr, and
// writes the result into tr.dataset.er for the generic op/value numeric
// filter (f-num-op/f-num-val, data-target "er") to read.
// ---------------------------------------------------------------------
function applyPreP1Er() {
  const kEl = document.getElementById('f-prep1-k');
  let k = kEl ? parseInt(kEl.value, 10) : NaN;
  if (!Number.isFinite(k) || k < 2) k = 2;
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    let er = null;
    if (tr.dataset.erByK) {
      const arr = JSON.parse(tr.dataset.erByK);
      const v = arr[k - 2];
      if (v !== null && v !== undefined) er = v;
    }
    tr.dataset.er = er === null ? '' : String(er);
    setCell(tr, '.prep1er-cell', er === null ? '-' : er.toFixed(2), 'prep1er-cell');
  });
}
// ---------------------------------------------------------------------
// P1 range ratio window -- see _m5_p1_range_ratio_by_window's docstring in
// render_m5_confl2_report.py. Each row ships its WHOLE w=1..max ratio
// array (tr.dataset.p1RatioByWindow) rather than one fixed reading, same
// "ship the array, index client-side" trick as applyPreP1Er above -- and,
// like that filter, writes the result into tr.dataset.p1ratio for the
// generic op/value numeric filter (f-num-op/f-num-val, data-target
// "p1ratio") to read, plus the row's own "P1 range ratio" column.
// ---------------------------------------------------------------------
function applyP1RatioWindow() {
  const wEl = document.getElementById('f-weakp1-window');
  let w = wEl ? parseInt(wEl.value, 10) : NaN;
  if (!Number.isFinite(w) || w < 1) w = 1;
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    let ratio = null;
    if (tr.dataset.p1RatioByWindow) {
      const arr = JSON.parse(tr.dataset.p1RatioByWindow);
      const v = arr[w - 1];
      if (v !== null && v !== undefined) ratio = v;
    }
    tr.dataset.p1ratio = ratio === null ? '' : String(ratio);
    setCell(tr, '.p1ratio-cell', ratio === null ? '-' : ratio.toFixed(2) + 'x', 'p1ratio-cell');
  });
}
function recomputeDynStats() {
  applyTargetModes();
  applyPreP1Er();
  applyP1RatioWindow();
  const excludeTags = activeDynExcludeTags();
  const isolateTags = activeDynIsolateTags();
  const outcomeOn = activeOutcomeBuckets();
  const mgmtCb = document.getElementById('mgmt-thrust-trail');
  const useMgmt = !!(mgmtCb && mgmtCb.checked);
  let n = 0, wins = 0, sumR = 0, sumPnl = 0, sumComm = 0, maxWinMae = 0, maxLossMfe = 0;
  // MAE/MFE live only in their cells, and applyTargetModes (called above) has
  // already rewritten those for whichever target rule is ticked, so reading
  // the cell is reading the excursion of the bracket actually in force.
  const cellNum = (tr, sel) => {
    const c = tr.querySelector(sel);
    return c ? parseFloat(c.textContent) : NaN;
  };
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    const tags = (tr.dataset.dynTags || '').split(' ').filter(Boolean);
    // The R >= filter (f-num-op/f-num-val, data-target "rr") is a dynamic
    // filter too: it folds into `hidden` here, alongside the tag-based
    // filters, so changing the R threshold live recomputes the headline
    // stats below instead of only hiding rows via applyReviewFilters.
    const rrHidden = !numFilterOk(tr, 'rr');
    // The P1->P2 gap filter (f-num-op/f-num-val, data-target "daygap") is a
    // dynamic filter too, same reasoning as rrHidden above -- tr.dataset.daygap
    // is a static number (unlike rr, it never changes with target mode).
    const daygapHidden = !numFilterOk(tr, 'daygap');
    // Same reasoning for the H1-candle and minutes P1->P2 gap filters
    // (data-target "h1gap" / "mingap") -- also static numbers, so they fold
    // into `hidden` here alongside daygap rather than only being handled by
    // applyReviewFilters' generic numeric-filter pass.
    const h1gapHidden = !numFilterOk(tr, 'h1gap');
    const mingapHidden = !numFilterOk(tr, 'mingap');
    // The Pre-P1 structure filter (f-num-op/f-num-val, data-target "er") is a
    // dynamic filter too, same reasoning as rrHidden above -- tr.dataset.er
    // was just rewritten by applyPreP1Er() for whichever k is now ticked.
    const erHidden = !numFilterOk(tr, 'er');
    // The P1 range ratio filter (f-num-op/f-num-val, data-target "p1ratio")
    // is a dynamic filter too, same reasoning as erHidden above --
    // tr.dataset.p1ratio was just rewritten by applyP1RatioWindow() for
    // whichever window is now ticked.
    const p1ratioHidden = !numFilterOk(tr, 'p1ratio');
    // The volume-spike offset filter (data-target "vspikeoffs") is a dynamic
    // filter too, same reasoning as p1ratioHidden -- tr.dataset.vspikeoffs is
    // a static number (the tagged row's own peak-second offset, absolute
    // value) set once at render time, never rewritten by a live control.
    const vspikeoffsHidden = !numFilterOk(tr, 'vspikeoffs');
    const hidden = rrHidden || daygapHidden || h1gapHidden || mingapHidden || erHidden || p1ratioHidden || vspikeoffsHidden || (isolateTags.length > 0
      ? !tags.some(t => isolateTags.includes(t))
      : (excludeTags.length > 0 && tags.some(t => excludeTags.includes(t))));
    tr.classList.toggle('dyn-hidden', hidden);
    const chartRow = document.getElementById('chart-row-' + tr.dataset.idx);
    if (chartRow) chartRow.classList.toggle('dyn-hidden', hidden);
    const isNoTradeRow = tr.classList.contains('unfilled-row')
      || tr.classList.contains('no-target-row');
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
    // Win/Loss/No trade bucket -- same per-row outcome the stats below
    // fold into win rate, so this filter always agrees with those numbers.
    const bucket = isNoTradeRow ? 'no_trade'
      : (isNaN(rVal) ? 'no_trade' : (outcome === 'target' ? 'win' : 'loss'));
    const outcomeHidden = !outcomeOn.includes(bucket);
    tr.classList.toggle('outcome-hidden', outcomeHidden);
    if (chartRow) chartRow.classList.toggle('outcome-hidden', outcomeHidden);
    if (hidden || outcomeHidden || isNoTradeRow) return;
    if (!isNaN(rVal)) {
      n += 1;
      sumR += rVal;
      const comm = parseFloat(tr.dataset.comm);   // fixed by the stop, same under every rule
      if (!isNaN(comm)) sumComm += comm;
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
  setText('sum-total-comm', '$' + sumComm.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}));
  setText('sum-max-win-mae', maxWinMae.toFixed(2));
  setText('sum-max-loss-mfe', maxLossMfe.toFixed(2));
  // applyReviewFilters (shared JS) re-applies the review-status/valid/
  // replayed/notes filters plus its own independent .hidden pass over the
  // SAME R >= filter (numFilterOk, data-target "rr") against tr.dataset.rr,
  // which applyTargetModes() just rewrote for whichever target rule is now
  // ticked -- re-run it so that pass doesn't go stale either.
  applyReviewFilters();
}
document.querySelectorAll('.f-dyn-exclude, .f-target-mode, .f-outcome').forEach(cb => cb.addEventListener('change', recomputeDynStats));
// The f-dyn-isolate radios share one name, so the browser already enforces
// "at most one checked" -- but a native radio can't uncheck itself by being
// clicked again, so track which one was checked before this click and,
// if it's the SAME one, clear it back to "no isolation" ourselves. This
// runs on 'click' rather than 'change' because unchecking programmatically
// (radio.checked = false) does not fire 'change', so a plain 'change'
// listener could never observe the turned-off state.
let lastIsolateRadio = null;
document.querySelectorAll('.f-dyn-isolate').forEach(rb => {
  rb.addEventListener('click', () => {
    if (lastIsolateRadio === rb) {
      rb.checked = false;
      lastIsolateRadio = null;
    } else {
      lastIsolateRadio = rb;
    }
    recomputeDynStats();
  });
});
// The R >= filter (data-target "rr") is folded into recomputeDynStats' own
// stats loop above, so it needs to trigger a recompute -- not just
// applyReviewFilters (added below by the shared .f-num-op/.f-num-val
// listener) -- whenever its op or value changes.
document.querySelectorAll('.f-num-op[data-target="rr"], .f-num-val[data-target="rr"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
// Same reasoning for the P1->P2 gap filter (data-target "daygap"): it also
// folds into recomputeDynStats' own stats loop above, not just
// applyReviewFilters, so it needs the same recompute trigger as rr.
document.querySelectorAll('.f-num-op[data-target="daygap"], .f-num-val[data-target="daygap"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
// Same reasoning for the H1-candle and minutes P1->P2 gap filters
// (data-target "h1gap" / "mingap"): they also fold into recomputeDynStats'
// own stats loop above, not just applyReviewFilters.
document.querySelectorAll('.f-num-op[data-target="h1gap"], .f-num-val[data-target="h1gap"], '
  + '.f-num-op[data-target="mingap"], .f-num-val[data-target="mingap"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
// The Pre-P1 structure filter (data-target "er") also folds into
// recomputeDynStats' own stats loop above, not just applyReviewFilters, and
// its k control (f-prep1-k) changes which value tr.dataset.er even IS
// (applyPreP1Er), so both need the same recompute trigger as rr/daygap.
document.querySelectorAll('.f-num-op[data-target="er"], .f-num-val[data-target="er"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
const prep1KEl = document.getElementById('f-prep1-k');
if (prep1KEl) prep1KEl.addEventListener('input', recomputeDynStats);
// The P1 range ratio filter (data-target "p1ratio") also folds into
// recomputeDynStats' own stats loop above, not just applyReviewFilters, and
// its window control (f-weakp1-window) changes which value tr.dataset.p1ratio
// even IS (applyP1RatioWindow), so both need the same recompute trigger as
// rr/er.
document.querySelectorAll('.f-num-op[data-target="p1ratio"], .f-num-val[data-target="p1ratio"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
// The volume-spike offset filter (data-target "vspikeoffs") also folds into
// recomputeDynStats' own stats loop above, not just applyReviewFilters, same
// as p1ratio/rr.
document.querySelectorAll('.f-num-op[data-target="vspikeoffs"], .f-num-val[data-target="vspikeoffs"]')
  .forEach(el => el.addEventListener('input', recomputeDynStats));
const weakp1WEl = document.getElementById('f-weakp1-window');
if (weakp1WEl) weakp1WEl.addEventListener('input', recomputeDynStats);
const mgmtToggleCb = document.getElementById('mgmt-thrust-trail');
if (mgmtToggleCb) mgmtToggleCb.addEventListener('change', recomputeDynStats);
recomputeDynStats();

// Multi-select column sort. Click a sortable header to sort by it alone
// (descending first, click again to flip); Shift+click adds it as a further
// key, or flips its direction if already a key. Reads each cell's CURRENT
// text, so R / PnL / MAE / MFE / Max DD sort by whatever the ticked target
// rules and management toggle currently show. Empty / '-' cells always sink
// to the bottom. A trade's chart row travels with its trade row.
const SORT_COLS = [0, 4, 5, 6, 7, 8, 11, 17, 21, 24, 25, 26];  // Trade Id, P1->P2 (d/H1/min), Pre-P1 ER, P1 range ratio, H1 P0 confl, R, PnL, MAE, MFE, Max DD
const SUPERS = ['¹', '²', '³', '⁴', '⁵', '⁶', '⁷', '⁸', '⁹', '¹⁰'];
let sortSpec = [];
(function () {
  const table = document.getElementById('lvl-table');
  if (!table) return;
  const tbody = table.tBodies[0];
  const origOrder = new Map();
  Array.from(tbody.querySelectorAll('tr.lvl-row')).forEach((tr, i) => origOrder.set(tr, i));
  const heads = Array.from(table.tHead.rows[0].cells);
  function cellNum(tr, col) {
    const v = parseFloat(tr.cells[col].innerText.replace(/,/g, ''));
    return Number.isNaN(v) ? null : v;
  }
  function renderHeaders() {
    SORT_COLS.forEach(c => {
      const th = heads[c];
      const k = sortSpec.findIndex(s => s.col === c);
      th.textContent = th.dataset.label + (k === -1 ? '' :
        ' ' + (sortSpec[k].dir === 'asc' ? '▲' : '▼') + (sortSpec.length > 1 ? SUPERS[k] : ''));
    });
  }
  function applySort() {
    const pairs = Array.from(tbody.querySelectorAll('tr.lvl-row')).map(tr => ({
      tr, chart: document.getElementById('chart-row-' + tr.dataset.idx),
      vals: sortSpec.map(s => cellNum(tr, s.col)), o: origOrder.get(tr)}));
    pairs.sort((a, b) => {
      for (let i = 0; i < sortSpec.length; i++) {
        const x = a.vals[i], y = b.vals[i];
        if (x === y) continue;
        if (x === null) return 1;
        if (y === null) return -1;
        return sortSpec[i].dir === 'asc' ? x - y : y - x;
      }
      return a.o - b.o;
    });
    pairs.forEach(p => { tbody.appendChild(p.tr); if (p.chart) tbody.appendChild(p.chart); });
    renderHeaders();
  }
  SORT_COLS.forEach(c => {
    const th = heads[c];
    th.dataset.label = th.textContent;
    th.classList.add('sortable-th');
    th.addEventListener('click', ev => {
      const cur = sortSpec.find(s => s.col === c);
      if (ev.shiftKey) {
        if (cur) cur.dir = cur.dir === 'asc' ? 'desc' : 'asc';
        else sortSpec.push({col: c, dir: 'desc'});
      } else {
        const only = sortSpec.length === 1 && cur;
        sortSpec = [{col: c, dir: only && cur.dir === 'desc' ? 'asc' : 'desc'}];
      }
      applySort();
    });
  });
})();
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
    parser.add_argument("--target-mode",
                        choices=("both", "opposite-m5-zz", "swing-extreme"),
                        default=TARGET_MODE_DEFAULT,
                        help="Which target rule(s) are ON BY DEFAULT in the rendered page. "
                             "Both rules are ALWAYS computed and both are live "
                             "checkboxes in the report itself, so this only sets the starting "
                             "state. both (default): both start ticked; whichever ticked rule "
                             "offers the FARTHER target wins per trade. opposite-m5-zz: the "
                             "farthest live opposite-type M5 P0 (no shared-P1 confluence "
                             "required) formed after the most recent confirmed zigzag trough "
                             "(short) / crest (long) in that window -- see --zz-threshold-pts "
                             "/ --zz-min-bars. swing-extreme: the most recent confirmed zigzag "
                             "trough (long) / crest (short) formed between P1 and P2, at its own price.")
    parser.add_argument("--zz-threshold-pts", type=float, default=ZZ_THRESHOLD_PTS_DEFAULT,
                        help=f"Point reversal off bar highs/lows that confirms a new zigzag leg "
                             f"for the opposite-m5-zz target rule (default "
                             f"{ZZ_THRESHOLD_PTS_DEFAULT:g}).")
    parser.add_argument("--zz-min-bars", type=int, default=ZZ_MIN_BARS_DEFAULT,
                        help=f"Bars required after a zigzag leg's own extreme bar before it can "
                             f"confirm, for the opposite-m5-zz target rule (default "
                             f"{ZZ_MIN_BARS_DEFAULT}).")
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
    parser.add_argument("--crest-refine", action=argparse.BooleanOptionalAction,
                        default=CREST_REFINE_DEFAULT,
                        help="Push the entry further favourable when the approach into it is an "
                             "outlier-fast crest-to-trough move (see the CREST REFINE section). "
                             "OFF by default.")
    parser.add_argument("--crest-refine-swing-k", type=int, default=CREST_REFINE_SWING_K_DEFAULT,
                        help=f"Bars required on each side of the swing pivot used as the crest "
                             f"(default {CREST_REFINE_SWING_K_DEFAULT}, same as --swerve-swing-k).")
    parser.add_argument("--crest-refine-baseline", type=str, default=CREST_REFINE_BASELINE_DEFAULT,
                        help=f"Trailing window (pandas offset string) the approach-speed z-score "
                             f"is measured against (default {CREST_REFINE_BASELINE_DEFAULT!r}).")
    parser.add_argument("--crest-refine-z", type=float, default=CREST_REFINE_Z_THRESHOLD_DEFAULT,
                        help=f"Z-score at or above which the approach counts as an outlier and "
                             f"triggers the refinement (default {CREST_REFINE_Z_THRESHOLD_DEFAULT:g}).")
    parser.add_argument("--crest-refine-alpha", type=float, default=CREST_REFINE_ALPHA_DEFAULT,
                        help=f"Fraction of the observed crest-to-planned-entry distance added to "
                             f"the entry when triggered (default {CREST_REFINE_ALPHA_DEFAULT:g}).")
    parser.add_argument("--crest-refine-cap-pts", type=float, default=CREST_REFINE_CAP_PTS_DEFAULT,
                        help=f"Furthest the entry may be pushed by this rule (default "
                             f"{CREST_REFINE_CAP_PTS_DEFAULT:g}pt).")
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
    parser.add_argument("--volume-spike-check", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Tag trades whose fill sat near an unusual same-side (bid for "
                             "LHPB, ask for LLPB) 1s volume spike -- see volume_spike.py. "
                             "Purely informational (tags, does not exclude). ON by default.")
    parser.add_argument("--volume-spike-core-seconds", type=float,
                        default=VOL_SPIKE_CORE_SECONDS_DEFAULT,
                        help=f"+/- seconds around the fill searched for the peak same-side "
                             f"volume (default {VOL_SPIKE_CORE_SECONDS_DEFAULT:g}s).")
    parser.add_argument("--volume-spike-baseline-seconds", type=float,
                        default=VOL_SPIKE_BASELINE_SECONDS_DEFAULT,
                        help=f"+/- seconds around the fill (core window excluded) this trade's "
                             f"own 'normal' same-side volume/sec is measured over (default "
                             f"{VOL_SPIKE_BASELINE_SECONDS_DEFAULT:g}s).")
    parser.add_argument("--volume-spike-ratio", type=float, default=VOL_SPIKE_RATIO_DEFAULT,
                        help=f"Peak same-side volume must be at least this many times the "
                             f"baseline mean to count as a spike (default "
                             f"{VOL_SPIKE_RATIO_DEFAULT:g}x).")
    parser.add_argument("--volume-spike-min-peak", type=float,
                        default=VOL_SPIKE_MIN_PEAK_DEFAULT,
                        help=f"A qualifying second's same-side volume must also be strictly "
                             f"more than this many contracts outright (default "
                             f"{VOL_SPIKE_MIN_PEAK_DEFAULT:g}).")
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
    parser.add_argument("--peg-target", action=argparse.BooleanOptionalAction, default=False,
                        help="Simulate a peg-to-market/chasing limit order for the TARGET "
                             "instead of a plain static one: on every wrong-side touch (price "
                             "reaches the target but only the wrong side trades there) it "
                             "re-quotes closer to market, same mechanism as --pegged-entry, "
                             "applied to the exit leg. OFF by default -- changes realized R on "
                             "every trade whose target was only ever wrong-side-touched, so "
                             "opt in deliberately rather than silently shifting headline stats.")
    parser.add_argument("--peg-target-step", type=float, default=PEG_STEP_DEFAULT,
                        help=f"Re-quote increment in points for --peg-target (default "
                             f"{PEG_STEP_DEFAULT} = one ES tick, same as --peg-step).")
    parser.add_argument("--peg-target-cap", type=float, default=PEG_CAP_DEFAULT,
                        help=f"Max total chase distance in points from the target for "
                             f"--peg-target (default {PEG_CAP_DEFAULT}, same as --peg-cap).")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--max-rows", type=int, default=None,
                        help="process only the first N SS-Confl-qualifying candidates (smoke test)")
    parser.add_argument("--workers", type=int, default=1,
                        help="concurrent chunk processes for the per-cluster tick-backed work "
                             "(find_alt_fill/resolve_trades/chart building); chunks are "
                             "contract-pure so each worker only memory-maps one .scid contract "
                             "at a time (default 1 = serial).")
    parser.add_argument("--m5-charts-only", action="store_true",
                        help="build only the M5 pane per row -- no 1s trio, 1-minute, bid/ask "
                             "volume or footprint panes. Fills and exits are still resolved "
                             "on real ticks; only the charts are skipped.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    args.default_target_modes = (DEFAULT_TARGET_MODES_BOTH if args.target_mode == "both"
                                 else (args.target_mode,))
    args.output = args.output or os.path.join(
        _REPO_ROOT, "public", "reports", "ss_m5_confl2", "jul-aug.html"
    )
    render(args)
