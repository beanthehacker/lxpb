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

  4. STOP ABOVE/BELOW THE THRUST (BREAKOUT) CANDLE. Live same-side M5
     levels within +/-10pt of the actual fill; the stop is one tick beyond
     the most protective breakout-candle extreme among them (highest high
     for a short, lowest low for a long) --
     render_ss_confl_finetune_report.dynamic_stop, unchanged.

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
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))   # this strategy's own folder (also the default output dir)
_REPO_ROOT = os.path.dirname(_HERE)                  # lxpb-v2/, where the shared report modules live
sys.path.insert(0, _REPO_ROOT)
import render_labels_report as R                 # noqa: E402
import render_stop_target_report as SR           # noqa: E402
import render_ss_confl_finetune_report as SF      # noqa: E402
import lxpb_levels_cache as LC                    # noqa: E402

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

def select_candidates(ss_confl_min, start, end, confluence_points):
    """M5-native candidate rows across every contract segment with tick data
    on disk. Returns a list of dicts (candidate index `i`, the row itself,
    its own same-side M5 confluence set, and the segment's M5 ledger --
    kept per-candidate since dynamic_target/dynamic_stop need the full
    ledger, not just the confluence subset)."""
    if not np.isfinite(confluence_points) or confluence_points < 0:
        raise ValueError("M5 confluence radius must be finite and non-negative")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)  # end date inclusive
    candidates = []
    for seg_idx, sym in LC._all_segments():
        m5_ledger = LC.m5_levels(seg_idx, verbose=False)
        if m5_ledger is None or m5_ledger.empty:
            continue
        seg_retests = LC.retests(m5_ledger)
        seg_retests = seg_retests[(seg_retests["retest_time"] >= start_ts) &
                                  (seg_retests["retest_time"] < end_ts)]
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

    stop_price, stop_row = SF.dynamic_stop(m5_ledger, level_type, fill_price, is_long,
                                           touch_time_alt)
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
    result["r_multiple"] = r_multiple
    if r_multiple < args.min_r:
        result["fail_reason"] = f"r_below_{args.min_r:g}"
        return result

    trade = {"type": level_type, "entry": fill_price, "is_long": is_long,
             "retest_time": touch_time_alt.tz_convert("UTC").tz_localize(None),
             "stop_dist": stop_pts, "target_dist": target_pts}
    resolved = SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]
    result.update({
        "filled": True, "touch_time_alt": touch_time_alt, "resolved": resolved,
        "favorable_pts": resolved.get("favorable_pts"), "adverse_pts": resolved.get("adverse_pts"),
        "giveback_pts": resolved.get("giveback_pts"), "entry_gapped": resolved.get("entry_gapped", False),
    })
    return result


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
    execution_charts, fp = SF.build_execution_charts({**res, "row": row_for_chart})
    return {"m5": chart_m5, **execution_charts}, fp


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


def render(args):
    if not np.isfinite(args.min_r) or args.min_r < 0:
        raise ValueError("--min-r must be finite and non-negative")
    if not np.isfinite(args.max_alt_fill_hours) or args.max_alt_fill_hours <= 0:
        raise ValueError("Fill-window hours must be finite and positive")

    candidates = select_candidates(args.ss_confl_min, args.start, args.end,
                                   args.m5_confluence_points)
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

    results = []
    for n, cluster in enumerate(clusters, start=1):
        anchor = SF.cluster_anchor(cluster)
        row_d = anchor["row"]
        tag = f"  [cluster of {len(cluster)}]" if len(cluster) > 1 else ""
        print(f"  [{n}/{len(clusters)}] row {anchor['i']} {row_d['type']} "
              f"{float(row_d['price']):.2f} retest {R._to_pt_str(row_d['retest_time'])}{tag}", flush=True)
        results.append(process_cluster(cluster, args))

    filled = [r for r in results if r["filled"]]
    skipped = [r for r in results if not r["filled"]]
    reason_counts = {}
    for r in skipped:
        reason_counts[r.get("fail_reason", "?")] = reason_counts.get(r.get("fail_reason", "?"), 0) + 1
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

    charts = []
    rows_html = []
    for idx, res in enumerate(results):
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
            charts.append(None)
            reason = res.get("fail_reason", "")
            rr_note = (f' (R {res["r_multiple"]:.2f})' if reason.startswith("r_below_")
                       and res.get("r_multiple") is not None else "")
            rows_html.append(f"""
<tr class="lvl-row unfilled-row {type_cls}" data-idx="{idx}">
  <td class="left">{res['i']}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td colspan="{N_COLS - 5}">{_fail_reason_label(reason)}{rr_note}</td>
</tr>""")
            continue

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

        chart_stack, fp = build_chart_stack_for_row(res)
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
  <td class="left">{retest_str}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td>{res['alt_price']:.2f}{gap_flag}<span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</td>
  <td class="left">{entry_touch_str}</td>
  <td title="{stop_title}">{res['stop_price']:.2f}<span class="src-tag m5">m5_thrust</span></td>
  <td title="{target_title}">{res['target_price']:.2f}<span class="src-tag m5">m5_opposite</span></td>
  <td>{rr_avail:.2f}</td>
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
    reason_html = "".join(
        f'<div class="box"><strong>{n}</strong>{reason}</div>'
        for reason, n in sorted(reason_counts.items(), key=lambda kv: -kv[1]))
    summary_html = f"""
<div class="summary">
  <div class="box"><strong>{len(candidates)}</strong>SS Confl &ge; {args.ss_confl_min}</div>
  <div class="box"><strong>{len(clusters)}</strong>confluence clusters</div>
  <div class="box"><strong>&plusmn;{radius}pt</strong>M5 confluence radius</div>
  <div class="box"><strong>{args.min_r:.2f}</strong>min R required</div>
  <div class="box true"><strong>{stats['win_rate']:.1f}%</strong>win rate ({stats['n']})</div>
  <div class="box"><strong>{stats['avg_r']:.2f}</strong>avg R</div>
  <div class="box"><strong>{stats['total_r']:.1f}</strong>total R</div>
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
shows own/m5). {peg_lead_sentence} STOP = one tick above the HIGHEST breakout-candle high
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
render_ss_confl_finetune_report.py for full detail).</p>
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
            f"<th class=\"left\">M5 retest</th>"
            f"<th class=\"left\" title=\"Distinct M5 prices merged into this trade, "
            f"extreme-first: highest for LLPB, lowest for LHPB. "
            f"Single-level trades show their own M5 price.\">Merged M5 levels</th>"
            f"<th title=\"The level's original M5 entry price, before fine-tuning to the "
            f"confluence group's extreme price\">Own</th><th>Entry</th>"
            f"<th class=\"left\">Entry (touch) time</th>"
            f"<th title=\"Live same-side M5 breakout-candle extreme plus one tick "
            f"(above/below the thrust candle); +/-{DYNAMIC_STOP_RADIUS_PTS:g}pt level "
            f"search; no trade if none qualifies\">Stop</th>"
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

    storage_key = f"lxpb_m5_confl{args.ss_confl_min}_review_v1"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>M5-native SS Confl report</title>
{CSS}
</head><body>
<h1>M5-native SS Confl. &ge; {args.ss_confl_min} strategy report</h1>
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
</style>
"""
JS = SR.JS


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
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    args.output = args.output or os.path.join(_HERE, "ss_m5_confl2_report.html")
    render(args)
