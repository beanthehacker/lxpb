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

  5. TARGET = THE NEAREST-QUALIFYING OPPOSITE M5 LEVEL. The newest live
     opposite-type M5 level (P1 shared by >=2 P0s), 1..20 points from the
     fill -- render_ss_confl_finetune_report.dynamic_target, unchanged.

  6. NO FALLBACKS. Unlike the H1 report (which falls back to a fixed
     stop/target when no qualifying M5 structure exists), THIS strategy has
     no fixed bracket at all: if step 4 or step 5 finds nothing, there is no
     trade. And if a bracket IS found but its reward:risk (target points /
     stop points, fixed at entry, independent of how the trade resolves) is
     below `--min-r` (default 1.0), there is still no trade -- a sub-1R
     setup is skipped outright rather than taken and marked a probable
     loser.

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

SS_CONFL_MIN_DEFAULT = 2
M5_CONFLUENCE_N_POINTS_DEFAULT = 5.0  # same-side M5 confluence radius: selection + entry refinement
MIN_DYNAMIC_TARGET_PTS = SF.MIN_DYNAMIC_TARGET_PTS
MAX_DYNAMIC_TARGET_PTS = SF.MAX_DYNAMIC_TARGET_PTS
DYNAMIC_STOP_RADIUS_PTS = SF.DYNAMIC_STOP_RADIUS_PTS
MAX_ALT_FILL_HOURS_DEFAULT = SF.MAX_ALT_FILL_HOURS_DEFAULT
MIN_R_DEFAULT = 1.0
PEG_STEP_DEFAULT = SF.PEG_STEP_DEFAULT
PEG_CAP_DEFAULT = SF.PEG_CAP_DEFAULT
P1_BAR_WIDTH = pd.Timedelta(minutes=5)  # this strategy's own P1 is an M5 bar, not H1

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
    lxpb_levels_cache.retests's own MIN_HOURS_BEFORE_RETEST gate -- see
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
    """M5-native candidate rows across every contract segment with tick data
    on disk. Returns (candidates, seg_departed): candidates is a list of
    dicts (candidate index `i`, the row itself, its own same-side M5
    confluence set, and the segment's M5 ledger -- kept per-candidate since
    dynamic_target/dynamic_stop need the full ledger, not just the
    confluence subset); seg_departed is {seg_idx: DataFrame} of every
    'departed' M5 level (see _seg_departed_levels -- clean retests AND
    consumed_early) in [start, end) for that segment, BEFORE the
    ss_confl_min filter -- kept separately so the p1_reacted dynamic filter (see
    _p1_group_reaction_cutoffs) can see a P1 group's full membership,
    including P0s that don't themselves clear ss_confl_min or were never a
    tradeable retest at all."""
    if not np.isfinite(confluence_points) or confluence_points < 0:
        raise ValueError("M5 confluence radius must be finite and non-negative")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)  # end date inclusive
    candidates = []
    seg_departed = {}
    for seg_idx, sym in LC._all_segments():
        m5_ledger = LC.m5_levels(seg_idx, verbose=False)
        if m5_ledger is None or m5_ledger.empty:
            continue
        seg_retests = LC.retests(m5_ledger)
        seg_retests = seg_retests[(seg_retests["retest_time"] >= start_ts) &
                                  (seg_retests["retest_time"] < end_ts)]
        departed = _seg_departed_levels(m5_ledger, start_ts, end_ts)
        if not departed.empty:
            seg_departed[seg_idx] = departed
        if seg_retests.empty:
            continue
        for _, row_d in seg_retests.iterrows():
            same_side_m5 = SF._same_side_confluence(m5_ledger, row_d, confluence_points)
            if len(same_side_m5) < ss_confl_min:
                continue
            candidates.append({
                "row": row_d, "same_side_m5": same_side_m5,
                "m5_ledger": m5_ledger, "seg_idx": seg_idx, "sym": sym,
            })
    candidates.sort(key=lambda c: pd.Timestamp(c["row"]["retest_time"]))
    for i, cand in enumerate(candidates):
        cand["i"] = i
    return candidates, seg_departed


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


def _dynamic_stop_m5(seg_idx, level_type, alt_price, is_long, entry_level_info,
                     m5_ledger, touch_time_alt):
    """(stop_price, stop_row, stop_source) for one trade.

    entry_level_info['is_spike'] -> stop = one tick beyond THAT level's own
    P0 candle's low/high (read from the cached whole-contract M5 series,
    LC.m5_bars_for_contract -- same source build_m5_chart's own M5 rays
    use). No fallback to the thrust-candle rule if this can't be computed
    (P0 bar missing from the cached series, or the resulting stop lands on
    the wrong side of the fill) -- that trade is a real "no trade"
    (no_m5_stop), not a silent revert to the old behaviour, since a silent
    fallback would defeat the point of making this an override.

    Otherwise: unchanged SF.dynamic_stop (protective extreme of the P1
    thrust candle among live same-side levels within +/-10pt of the fill).

    stop_row is always a pd.Series (like SF.dynamic_stop's own return) so
    callers can .to_dict() either path uniformly."""
    if entry_level_info is not None and entry_level_info.get("is_spike"):
        all_bars = LC.m5_bars_for_contract(seg_idx)
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
    stop_price, stop_row = SF.dynamic_stop(m5_ledger, level_type, alt_price, is_long, touch_time_alt)
    if stop_price is None:
        return None, None, None
    return stop_price, stop_row, "m5_thrust"


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
    alt_price, alt_source, group_n = conf["alt_price"], conf["alt_source"], conf["group_n"]

    result = {
        "i": anchor["i"], "row": row_d, "level_type": level_type, "is_long": is_long,
        "seg_idx": cluster[0]["seg_idx"],
        "group_n": group_n, "cluster_size": len(cluster), "cluster_members": member_prices,
        "own_price": own_price, "alt_price": alt_price, "alt_source": alt_source,
        "alt_formation_time": conf["alt_formation_time"], "alt_end_time": conf["alt_end_time"],
        "entry_m5_level": conf["entry_m5_level"],
        "improved": abs(alt_price - own_price) > 1e-9,
        "filled": False,
    }

    window_start = pd.to_datetime(row_d["retest_time"], utc=True)
    touch_time_alt, fill_price = SF.find_alt_fill(
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

    bars = SF.build_minute_bars(touch_time_alt)
    if bars is None or bars.empty:
        result["fail_reason"] = "no_tick_data_after_fill"
        return result

    target_price, target_row = SF.dynamic_target(m5_ledger, level_type, fill_price, is_long,
                                                  touch_time_alt)
    if target_price is None:
        result["fail_reason"] = "no_m5_target"
        return result
    target_pts = abs(target_price - fill_price)

    entry_level_info = _entry_level_row(cluster, conf)
    stop_price, stop_row, stop_source = _dynamic_stop_m5(
        cluster[0]["seg_idx"], level_type, fill_price, is_long, entry_level_info,
        m5_ledger, touch_time_alt)
    if stop_price is None:
        result["fail_reason"] = "no_m5_stop"
        return result
    stop_pts = abs(stop_price - fill_price)
    if stop_pts <= 0:
        result["fail_reason"] = "degenerate_stop"
        return result

    r_multiple = target_pts / stop_pts
    result["target_price"] = target_price
    result["target_pts"] = target_pts
    result["target_m5_level"] = target_row.to_dict()
    result["stop_price"] = stop_price
    result["stop_pts"] = stop_pts
    result["stop_m5_level"] = stop_row.to_dict()
    result["stop_source"] = stop_source
    result["r_multiple"] = r_multiple
    if r_multiple < args.min_r:
        result["fail_reason"] = f"r_below_{args.min_r:g}"
        return result

    trade = {"type": level_type, "entry": fill_price, "is_long": is_long,
             "retest_time": touch_time_alt.tz_convert("UTC").tz_localize(None),
             "stop_dist": stop_pts, "target_dist": target_pts}
    resolved = SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]
    managed = TM.resolve_managed_trade(trade, bars, cluster[0]["seg_idx"], level_type,
                                       ledger=m5_ledger)
    result.update({
        "filled": True, "touch_time_alt": touch_time_alt, "resolved": resolved,
        "favorable_pts": resolved.get("favorable_pts"), "adverse_pts": resolved.get("adverse_pts"),
        "giveback_pts": resolved.get("giveback_pts"), "entry_gapped": resolved.get("entry_gapped", False),
        "mgmt": _mgmt_summary(resolved, managed, stop_pts),
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
        t = snap(trigger_time)
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
    chart_m5 = SR.build_m5_chart(
        row_for_chart, resolved, stop_pts, target_pts,
        level_price=res["own_price"], entry_level=res["entry_m5_level"],
        p1_bar_width=P1_BAR_WIDTH, p1_label="M5")
    if chart_m5 is not None:
        chart_m5["title"] += (f"  |  R {res['r_multiple']:.2f}  |  entry via "
                              f"{res['alt_source']} ({res['group_n']} in group)")
        _annotate_p0_p1_p2(chart_m5, row_for_chart, res["is_long"])
        _annotate_mgmt_events(chart_m5, res, res["is_long"])
    execution_charts, fp = SF.build_execution_charts({**res, "row": row_for_chart})
    return {"m5": chart_m5, **execution_charts}, fp


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
    process boundary (see process_clusters) to avoid pickling a whole
    contract's ledger once per candidate; reload it once per distinct
    segment here instead (cheap -- LC.m5_levels reads the cached parquet,
    not raw ticks) and re-attach it, exactly what select_candidates itself
    hands process_cluster in the serial (--workers=1) path."""
    args = spec["args"]
    ledger_by_seg = {}
    out = {}
    for pos, cluster in zip(spec["positions"], spec["clusters"]):
        for cand in cluster:
            seg_idx = cand["seg_idx"]
            if seg_idx not in ledger_by_seg:
                ledger_by_seg[seg_idx] = LC.m5_levels(seg_idx, verbose=False)
            cand["m5_ledger"] = ledger_by_seg[seg_idx]
        res = process_cluster(cluster, args)
        if res["filled"]:
            chart_stack, fp = build_chart_stack_for_row(res)
        else:
            chart_stack, fp = None, None
        out[pos] = (res, chart_stack, fp)
    with open(spec["out_path"], "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)


def process_clusters(clusters, args):
    """Run process_cluster + chart building for every cluster, in original
    order. Serial when args.workers <= 1 (or there's nothing to split);
    otherwise splits into contract-pure chunks (_chunk_clusters_by_contract)
    and runs each chunk in its own child process, polling like
    render_stop_target_report._build_records_parallel. Returns (results,
    chart_stacks, fps) -- three lists in original cluster order;
    chart_stacks[i]/fps[i] are None for an unfilled cluster."""
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
                chart_stack, fp = None, None
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


def _resolve_raw_retest(seg_idx, m5_ledger, row_d):
    """(outcome, touch_time) for one RAW 'departed' M5 level -- a clean
    retest OR a consumed_early death (see _seg_departed_levels) -- not
    fine-tuned, not fill-window-searched: row_d['touch_time'] itself is the
    touch. outcome is from _naive_bracket_touch (a raw OHLC touch race, no
    fill-quality requirement -- see its own docstring for why that differs
    from a real trade's own resolution). Returns (None, None) if no
    qualifying target/stop exists or there's no tick data to check
    against, same 'no trade' cases process_cluster itself would hit.
    dynamic_target/_dynamic_stop_m5 still pick the SAME bracket a real
    trade on this P0 alone would have used -- only the touch-vs-fill
    distinction differs."""
    level_type = row_d["type"]
    is_long = level_type == "LHPB"
    price = float(row_d["price"])
    touch_time = pd.Timestamp(row_d["touch_time"])
    if touch_time.tzinfo is None:
        touch_time = touch_time.tz_localize("UTC")

    target_price, _ = SF.dynamic_target(m5_ledger, level_type, price, is_long, touch_time)
    if target_price is None:
        return None, None
    stop_price, _, _ = _dynamic_stop_m5(
        seg_idx, level_type, price, is_long, row_d.to_dict(), m5_ledger, touch_time)
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


def _p1_group_reaction_cutoffs(relevant_keys, seg_departed):
    """{(seg_idx, type, breakout_time): earliest reacting touch_time} for
    every relevant_keys entry whose FULL P1 group (every DEPARTED M5 level
    -- clean retest or consumed_early, see _seg_departed_levels -- sharing
    that type+breakout_time in this segment, including P0s below
    ss_confl_min or that never became a tradeable retest at all) has >=2
    members. Walks each such group in touch_time order and resolves every
    member with _resolve_raw_retest until one reaches outcome=='target',
    which sets that group's cutoff; a group with no reacting member is
    absent from the returned dict (never filtered). Single-member groups
    have no OTHER P0 to react on their behalf, so they are skipped without
    ever touching tick data."""
    keys_by_seg = {}
    for seg_idx, level_type, breakout_time in relevant_keys:
        keys_by_seg.setdefault(seg_idx, set()).add((level_type, breakout_time))

    cutoffs = {}
    for seg_idx, keys in keys_by_seg.items():
        departed = seg_departed.get(seg_idx)
        if departed is None or departed.empty:
            continue
        m5_ledger = LC.m5_levels(seg_idx, verbose=False)
        for (level_type, breakout_time), group in departed.groupby(["type", "breakout_time"]):
            key3 = (level_type, pd.Timestamp(breakout_time))
            if key3 not in keys or len(group) < 2:
                continue
            group = group.sort_values("touch_time")
            for _, row_d in group.iterrows():
                outcome, touch_time = _resolve_raw_retest(seg_idx, m5_ledger, row_d)
                if outcome == "target":
                    cutoffs[(seg_idx, level_type, key3[1])] = touch_time
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
    user toggle a checkbox per tag IN THE BROWSER to hide/show those rows
    and recompute win rate / avg R / total R / total PnL live, with NO
    Python regen required. This is deliberately generic: to add a new
    dynamic filter, (1) tag qualifying results with one more entry in
    dyn_tags (their own detection logic, wherever that lives), (2) add one
    <label class="chip"> checkbox with class f-dyn-exclude and
    data-tag="<your tag>" to the filter panel in _finish_report. Nothing
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
        "no_tick_data_after_fill": "NO DATA after fill",
        "no_m5_target": "NO TRADE (no qualifying M5 opposite target)",
        "no_m5_stop": "NO TRADE (no qualifying M5 breakout-candle stop)",
        "degenerate_stop": "NO TRADE (degenerate stop)",
    }.get(reason, reason.replace("_", " "))


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
    if not np.isfinite(args.min_r) or args.min_r < 0:
        raise ValueError("--min-r must be finite and non-negative")
    if not np.isfinite(args.max_alt_fill_hours) or args.max_alt_fill_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")

    candidates, seg_departed = select_candidates(
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
    p1_cutoffs = _p1_group_reaction_cutoffs(relevant_keys, seg_departed)

    results, chart_stacks, fps = process_clusters(clusters, args)
    results = _apply_p1_reaction_filter(results, p1_cutoffs)
    n_tagged = sum(1 for r in results if "p1_reacted" in r.get("dyn_tags", ()))
    print(f"p1_reacted: {len(p1_cutoffs)} P1 group(s) had an already-reacted "
          f"sibling P0; {n_tagged} trade(s) tagged 'p1_reacted' (still shown/counted by "
          f"default -- toggle the Dynamic filters checkbox in the report to exclude "
          f"them)", flush=True)
    results = _apply_globex_open_filter(results)
    n_globex_tagged = sum(1 for r in results if "globex_eth_open" in r.get("dyn_tags", ()))
    print(f"{n_globex_tagged} trade(s) tagged 'globex_eth_open' (excluded from the report's "
          f"default view -- untick the Dynamic filters checkbox to include them)", flush=True)

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
            reason = res.get("fail_reason", "")
            rr_note = (f' (R {res["r_multiple"]:.2f})' if reason.startswith("r_below_")
                       and res.get("r_multiple") is not None else "")
            row_html = f"""
<tr class="lvl-row unfilled-row {type_cls}" data-idx="{idx}">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td colspan="{N_COLS - 5}">{_fail_reason_label(reason)}{rr_note}</td>
</tr>"""
            return None, row_html

        resolved = res["resolved"]
        outcome_label, outcome_cls = SF._outcome_label(resolved)
        r_val = resolved.get("r")
        rr_avail = res["r_multiple"]
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
        target_level = res["target_m5_level"]
        target_title = (f"Newest eligible M5 {target_level['type']} P0: "
                        f"{R._to_pt_str(target_level['formation_time'])}; shared P1: "
                        f"{R._to_pt_str(target_level['breakout_time'])}")
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

        dyn_tags = res.get("dyn_tags") or []
        dyn_tags_attr = " ".join(dyn_tags)
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
        outcome_for_js = resolved.get("outcome") or ""
        r_for_js = "" if r_val is None else f"{r_val:.6f}"
        pnl_for_js = "" if pnl_pts is None else f"{pnl_pts:.6f}"

        mgmt = res.get("mgmt") or {}
        mgmt_r_for_js = "" if mgmt.get("r") is None else f"{mgmt['r']:.6f}"
        mgmt_pnl_for_js = "" if mgmt.get("pnl_pts") is None else f"{mgmt['pnl_pts']:.6f}"
        mgmt_outcome_for_js = mgmt.get("outcome") or ""
        mgmt_fired_for_js = "1" if mgmt.get("fired") else "0"
        mgmt_badge = ""
        if mgmt.get("fired"):
            mgmt_bits = []
            if mgmt.get("trail_events"):
                mgmt_bits.append(f"stop trailed x{len(mgmt['trail_events'])}")
            if mgmt.get("rr_floor_fired"):
                mgmt_bits.append("RR-floor exit")
            mgmt_r_str = f"{mgmt['r']:+.2f}R" if mgmt.get("r") is not None else "?"
            mgmt_badge = (f'<span class="dyn-tag-badge mgmt-tag-badge" '
                          f'title="Trade management ({", ".join(mgmt_bits)}) would change this '
                          f'trade to {mgmt_r_str} ({mgmt.get("outcome")}). Toggle the Trade '
                          f'management checkbox in the panel above to use it in the summary '
                          f'stats.">MGMT {mgmt_r_str}</span>')

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
    data-dyn-tags="{dyn_tags_attr}" data-r="{r_for_js}" data-pnl-pts="{pnl_for_js}"
    data-outcome="{outcome_for_js}"
    data-mgmt-r="{mgmt_r_for_js}" data-mgmt-pnl-pts="{mgmt_pnl_for_js}"
    data-mgmt-outcome="{mgmt_outcome_for_js}" data-mgmt-fired="{mgmt_fired_for_js}"
    onclick="toggleChart({idx})">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td>{res['alt_price']:.2f}{gap_flag}<span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td title="{stop_title}">{res['stop_price']:.2f}<span class="src-tag m5">{stop_source}</span></td>
  <td title="{target_title}">{res['target_price']:.2f}<span class="src-tag m5">m5_opposite</span></td>
  <td>{rr_avail:.2f}</td>
  <td class="{outcome_cls}">{outcome_label}{dyn_badges}{mgmt_badge}</td>
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
  <div class="box"><strong>{args.min_r:.2f}</strong>min R required</div>
  <div class="box true"><strong id="sum-win-rate">{stats['win_rate']:.1f}%</strong>win rate
    <span id="sum-win-rate-n">({stats['n']})</span></div>
  <div class="box"><strong id="sum-avg-r">{stats['avg_r']:.2f}</strong>avg R</div>
  <div class="box"><strong id="sum-total-r">{stats['total_r']:.1f}</strong>total R</div>
  <div class="box"><strong id="sum-total-pnl">{total_pnl_pts:+.1f}</strong>total PnL (pts)</div>
  <div class="box"><strong>{improved_n}</strong>/{len(filled)} entry improved over own level</div>
  <div class="box"><strong>{max_win_mae:.2f}</strong>max MAE (win)</div>
  <div class="box"><strong>{max_loss_mfe:.2f}</strong>max MFE (loss)</div>
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
&plusmn;{DYNAMIC_STOP_RADIUS_PTS:g} points of the actual fill. TARGET = the MOST RECENTLY
FORMED (P0) live opposite-type M5 level on the favourable side, {MIN_DYNAMIC_TARGET_PTS:g}
&ndash;{MAX_DYNAMIC_TARGET_PTS:g} points from the fill, whose P1 candle broke at least two
distinct same-type P0 levels. NO FALLBACKS: if no qualifying stop or target exists, or if the
resulting reward:risk (target pts / stop pts, fixed at entry) is below {args.min_r:g}, there
is no trade at all (see the summary boxes above for the skip-reason breakdown) -- this differs
from render_ss_confl_finetune_report.py, whose H1-anchored strategy always falls back to a
fixed stop/target. Both searches use only completed M5 candles and the live ledger state
immediately before the fill's M5 bar, with no fixed lookback. Charts/markers/price-lines/
tooltips, MAE/MFE/Max DD definitions, and the Reviewed/Valid/Replayed/Notes columns below all
follow render_stop_target_report.py's own conventions exactly (see that module and
render_ss_confl_finetune_report.py for full detail). See the Dynamic filters row in the panel
above for trade-exclusion toggles you can flip live in the browser, no regen required.</p>
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
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser trade-exclusion toggles -- no Python regen
needed. Checking one hides those rows AND recomputes win rate / avg R / total R / total PnL
above from only the remaining (not excluded) trades. To add another dynamic filter: tag
qualifying results with an entry in res['dyn_tags'] (Python side) and add one more checkbox
here with class f-dyn-exclude and data-tag matching that tag -- see
_apply_p1_reaction_filter's docstring in render_m5_confl2_report.py for the full
convention.">Dynamic filters</span>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="p1_reacted">
      Exclude P1-already-reacted</label>
    <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="globex_eth_open" checked>
      Exclude Globex/ETH open fills (15:00-15:05 PT)</label>
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser toggle for the plug-n-play trade-management
rules in trade_management.py (symmetric across direction): rule 1 trails the stop to one tick
beyond a qualifying large-body M5 thrust candle's own extreme (below the low for a long, above
the high for a short); rule 2 exits at market whenever remaining reward/remaining risk (using
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
            f"<th title=\"Newest eligible opposite M5 P0 sharing P1 with another P0; "
            f"no trade if none qualifies\">Target</th>"
            f"<th title=\"Reward:risk on offer for THIS trade's own bracket at entry "
            f"(target pts / stop pts) -- fixed once entry/stop/target are picked, independent "
            f"of whether the trade goes on to win or lose. No trade if below "
            f"{args.min_r:g}\">R</th>"
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
.mgmt-tag-badge { background:#0e3a4a; color:#67e8f9; }
</style>
"""
JS = SR.JS + """
<script>
// ---------------------------------------------------------------------
// Dynamic filters -- see _apply_p1_reaction_filter's own docstring in
// render_m5_confl2_report.py for the full authoring convention (this is
// the intentionally-generic, tag-agnostic half of it). Each
// f-dyn-exclude checkbox's data-tag names a tag a Python-side filter may
// have added to a row's data-dyn-tags (space-separated -- a row can
// carry more than one). Checking a box hides every row carrying that
// tag -- via its OWN .dyn-hidden class, kept deliberately separate from
// the review-workflow filters' .hidden class above (applyReviewFilters,
// in the shared JS) so the two systems never fight over one class; a row
// is invisible if EITHER is set -- and recomputes the win rate / avg R /
// total R / total PnL summary boxes from only the remaining (not
// excluded) trades. To add another dynamic filter: tag qualifying
// results with one more entry in res['dyn_tags'] (Python side) and add
// one more <input class="f-dyn-exclude" data-tag="..."> checkbox to the
// filter panel -- recomputeDynStats() below needs no changes for a new
// tag, it reads whatever tags are present.
// ---------------------------------------------------------------------
function activeDynExcludeTags() {
  return Array.from(document.querySelectorAll('.f-dyn-exclude:checked')).map(cb => cb.dataset.tag);
}
function recomputeDynStats() {
  const excludeTags = activeDynExcludeTags();
  const mgmtCb = document.getElementById('mgmt-thrust-trail');
  const useMgmt = !!(mgmtCb && mgmtCb.checked);
  let n = 0, wins = 0, sumR = 0, sumPnl = 0;
  document.querySelectorAll('#lvl-table tbody tr.lvl-row:not(.unfilled-row)').forEach(tr => {
    const tags = (tr.dataset.dynTags || '').split(' ').filter(Boolean);
    const excluded = excludeTags.length > 0 && tags.some(t => excludeTags.includes(t));
    tr.classList.toggle('dyn-hidden', excluded);
    const chartRow = document.getElementById('chart-row-' + tr.dataset.idx);
    if (chartRow) chartRow.classList.toggle('dyn-hidden', excluded);
    if (excluded) return;
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
      if (outcome === 'target') wins += 1;
    }
    if (!isNaN(pnl)) sumPnl += pnl;
  });
  const winRate = n ? (wins / n * 100) : 0;
  const avgR = n ? (sumR / n) : 0;
  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  setText('sum-win-rate', winRate.toFixed(1) + '%');
  setText('sum-win-rate-n', '(' + n + ')');
  setText('sum-avg-r', avgR.toFixed(2));
  setText('sum-total-r', sumR.toFixed(1));
  setText('sum-total-pnl', (sumPnl >= 0 ? '+' : '') + sumPnl.toFixed(1));
}
document.querySelectorAll('.f-dyn-exclude').forEach(cb => cb.addEventListener('change', recomputeDynStats));
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
                        help=f"Minimum reward:risk (target pts / stop pts) required to take "
                             f"the trade at all (default {MIN_R_DEFAULT}).")
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
    args.output = args.output or os.path.join(
        _REPO_ROOT, "public", "reports", "ss_m5_confl2", "ss_m5_confl2_report.html"
    )
    render(args)
