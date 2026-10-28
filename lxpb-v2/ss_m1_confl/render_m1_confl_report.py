"""
render_m1_confl_report.py
=========================
M1 FADE REPORT, ENGINE ONLY (user, 2026-10-16). The trades are the live engine's (live_range/): range first, the M1 LXPB
ledger only as a cache lookup of the levels a box's fade seat can trade. The older retest-first pipeline (candidate
selection, confluence clustering, find_alt_fill fills, stop / target pickers, the tag-afterwards range-box classification)
was DELETED; its flags are accepted and ignored (REMOVED_FLAGS) so old regen commands still run. Nothing in this file
decides, fills, stops or targets a trade. This report is the FADE playbook only: go-with is not part of it.

What it does, in order
  1. ENGINE. The window's Sierra Chart ticks are replayed through live_range (replay.build_engine / feed) as a production
     feed would deliver them: decisions once per second (--decision-s), fills of resting orders and stop / target hits
     on the real tick. Warm-up (--warmup-days trading days) only builds the books; trades are those armed in the window.
     Rules in force: H1-backed range merge (--box-merge-overlap, default 0.5), fade 2 off (--fade2 turns it on),
     no news-spike check. See lxpb-v2/CLAUDE.md and live_range/README.md.
  2. ROWS. engine_rows.py turns each FILLED fade trade (go-with trades are dropped) into the row dict the page and charts
     read: entry level, cluster, swerve, stop (+ its source), target (the last confirmed zigzag swing since P1), the
     engine's exit, MAE / MFE / Max DD from the tape, the trail log, and the range boxes behind the seat (edges, times,
     zigzag pivots).
  3. CHARTS. M1 trading pane, M5 context pane, D1 / H1 panes (arrow only) as before; the boxes are drawn with their rays,
     start / breakout lines and numbered pivots. Chart building, the page CSS/JS shell and the review store come from
     render_stop_target_report; those are shared with other reports, so this file works around them.
  4. PAGE. One row per trade with Reviewed / Valid / Notes; live Exclude / Only chips for the tags; win = a trade that
     made money (target, trailed stop, RR-floor or end-of-day exit alike).

Usage (the output goes to public/reports/ss_m1_confl/ unless --output is given):
    python render_m1_confl_report.py --since-day 2026-09-28 --warmup-days 5
    python scid_only/run_scid_only.py ss_m1_confl/render_m1_confl_report.py ...  # no TradingView exports
"""
import os
import re
import sys
import json
import bisect
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
import trade_management as TM                     # noqa: E402
import range_box_tags as RB                       # noqa: E402  (this folder; find_range_box_zz via patterns_pure)
import engine_rows as ER                       # noqa: E402  (this folder: the engine's trades as report rows)
from live_range import replay as RP           # noqa: E402
from live_range.engine import Params          # noqa: E402

DYNAMIC_STOP_RADIUS_PTS = SF.DYNAMIC_STOP_RADIUS_PTS
MIN_R_DEFAULT = 1.0
BAR_MIN = 1  # bar size in minutes (--bar-minutes); main sets it before anything runs
BAR_FREQ = "1min"
P1_BAR_WIDTH = pd.Timedelta(minutes=1)  # this strategy's own P1 is one bar of BAR_MIN minutes, not H1/M5
CANDIDATE_COLOR = "#7dd3fc"  # light blue -- every C1..Cn marker (dot + label), chosen or not;
                             # plain-P0s are distinguished by their " pl." text suffix, not color

# Options of the old forked report that are gone (see main): accepted and ignored.
REMOVED_FLAGS = ("--range-box-only", "--m5-charts-only", "--atr-boundary-mult", "--crest-refine",
                 "--volume-spike", "--no-volume-spike",
                 # the old pipeline (2026-10-16: the engine decides the trades; see the module docstring)
                 "--ss-confl-min", "--p0-kinds", "--plain-p0-min-wick-pts", "--bo-range-atr", "--bo-body", "--bo-atr-period", "--run-in-mult", "--run-in-baseline-bars", "--m1-confluence-points", "--confl-untested-until", "--target-mode", "--zz-atr-mult", "--zz-threshold-pts", "--zz-min-bars", "--swerve", "--swerve-tol-pts", "--swerve-lookback-hours", "--swerve-max-move-pts", "--swerve-swing-k", "--news-spike-check", "--news-lookback-seconds", "--news-shock-range-pts", "--news-shock-spread-ticks", "--liquidity-gate", "--liq-window-minutes", "--liq-wide-spread-share", "--eod-flat", "--max-alt-fill-hours", "--pegged-entry", "--peg-step", "--peg-cap", "--peg-target", "--peg-target-step", "--peg-target-cap", "--news-pull-window", "--range-lookback-days", "--range-procs", "--playbook", "--max-rows", "--workers",
                 "--no-swerve", "--no-pegged-entry", "--no-eod-flat", "--no-news-pull-window", "--no-peg-target",
                 "--no-news-spike-check", "--no-liquidity-gate")

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


# A stop closer than this to the fill is too tight to be a real invalidation
# point; _wider_stop_p1 moves it to an older P1 candle instead.
MIN_STOP_PTS = 3.0


# --------------------------------------------------------------------------
# Target selection -- ZIGZAG-BASED RULES (m5_structure.zigzag_pivots)
#
# Both rules read the trade's own P1..fill window (see _window_bounds): the
# swing-extreme rule takes the last confirmed zigzag pivot's own price, the
# opposite-M1-zigzag-anchor rule takes the farthest untested opposite-type M1
# P0 formed after that pivot. The same up-to-50pt favourable band bounds both.
# --------------------------------------------------------------------------

ZIGZAG_TARGET_MODES = ("opposite-zz", "swing-extreme")   # the rules _pick_targets computes while a trade is resolved
RANGE_EDGE_MODE = "range-edge"   # the fade's range box edge; needs the boxes, so _add_range_edge_targets adds it afterwards
TARGET_MODES = ZIGZAG_TARGET_MODES + (RANGE_EDGE_MODE,)   # every rule a row can carry




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
# to the nearest OTHER live same-side M1 LXPB level within
# `--swerve-max-move-pts`, skipping any candidate that has the same problem.
#
# If no such level exists the trade is not taken (tag `swerve_blocked`);
# it is still processed and charted at its ORIGINAL entry so the report can
# show what the untaken trade would have done -- see the dynamic-filter
# convention in _apply_globex_open_filter's docstring.
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Per-cluster processing (one cluster = one trade)
# --------------------------------------------------------------------------

def _annotate_p0_p1_p2(chart_main, row_for_chart, is_long):
    """Explicit P0 (formation) / P1 (breakout) markers on the M1 pane, and
    an explicit "P2" tag on its existing entry marker -- the same P0/P1/P2
    convention render_stop_target_report.build_trade_chart draws on the H1
    pane in every other report. This strategy has no H1 pane (see module
    docstring): the M1 pane IS the primary structural chart here, so it
    needs the same annotation build_m5_chart never had to provide when it
    was only ever a companion pane to an H1-anchored row elsewhere.

    build_m5_chart's own window can compress out bars between formation/
    breakout/retest (it was only ever built to guarantee the entry region
    and each M1 confluence ray's own span, not this subject level's own
    P0/P1), so each marker snaps to the nearest bar AT OR BEFORE its real
    time -- same convention that function's own entry-bar marker already
    uses -- and is silently skipped if that time is before the pane's own
    first bar. Mutates `chart_main` in place; no-op if it is None."""
    if chart_main is None or not chart_main["candles"]:
        return
    times = [c["time"] for c in chart_main["candles"]]

    def snap(ts):
        target = R._to_epoch_utc(pd.Timestamp(ts))
        i = bisect.bisect_right(times, target) - 1
        return times[i] if i >= 0 else None

    for m in chart_main["markers"]:
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
    chart_main["markers"].extend(new_markers)
    chart_main["markers"].sort(key=lambda m: m["time"])


def _p1_sibling_group(level_type, breakout_time):
    """Every M1 level (any fate) sharing (level_type, breakout_time) in the
    one continuous M1 ledger -- i.e. the full field of P0 candidates one
    breakout bar broke at once, plain-P0s included (see lxpb.py's P0
    kinds), not just the spike/swing P0s this report trades. Sorted by
    formation_time. Returns None if fewer than 2 candidates
    shared this P1 (nothing else to annotate or protect from chart
    compression)."""
    ledger = LC.m5_levels(verbose=False, plain_p0=LC.PLAIN_P0_UNTRACKED)
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


def _annotate_candidates(chart_main, row_for_chart, is_long, group, cluster_member_formations):
    """Mark every OTHER M1 P0 candidate in `group` (see _p1_sibling_group),
    labeled C1/C2/... in formation-time order -- the whole field the
    winning P0 was chosen from, not just the winner. The winning P0 itself
    (already marked "P0" by _annotate_p0_p1_p2, called first) gets its own
    C-number folded into that same marker's text instead of a second
    overlapping dot.

    A confluence cluster can merge >1 mutually-confluent M1 level into ONE
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
    Mutates chart_main in place; no-op if chart_main or group is None."""
    if chart_main is None or not chart_main["candles"] or group is None:
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

    times = [c["time"] for c in chart_main["candles"]]

    def snap(ts):
        target = R._to_epoch_utc(pd.Timestamp(ts))
        i = bisect.bisect_right(times, target) - 1
        return times[i] if i >= 0 else None

    n_group = len(group)
    new_markers = []
    for n, (_, lv) in enumerate(group.iterrows(), start=1):
        formation = pd.Timestamp(lv["formation_time"])
        if formation == own_formation:
            for m in chart_main["markers"]:
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
        plain = lv["p0_kind"] == LC.L.P0_PLAIN
        new_markers.append({
            "time": t, "position": "belowBar" if is_long else "aboveBar",
            "color": CANDIDATE_COLOR, "shape": "circle",
            "text": f"C{n}" + (" pl." if plain else ""),
        })
    chart_main["markers"].extend(new_markers)
    chart_main["markers"].sort(key=lambda m: m["time"])


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


def _annotate_mgmt_events(chart_main, res, is_long):
    """Mark fired trade-management events on the M1 pane (see _mgmt_markers).
    No-op if chart_main is None or nothing fired for this trade."""
    snap = _snapper(chart_main)
    if snap is None:
        return
    new_markers = _mgmt_markers(snap, res.get("mgmt"), is_long)
    if new_markers:
        chart_main["markers"].extend(new_markers)
        chart_main["markers"].sort(key=lambda m: m["time"])


def _snapper(chart_main):
    """A `snap(ts) -> chart bar time` helper for one M1 chart, or None if the
    chart has no candles. Same at-or-before convention every other annotator
    in this module uses (the pane is compressed, so an arbitrary instant may
    not have its own bar)."""
    if chart_main is None or not chart_main["candles"]:
        return None
    times = [c["time"] for c in chart_main["candles"]]

    def snap(ts):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        i = bisect.bisect_right(times, R._to_epoch_utc(ts)) - 1
        return times[i] if i >= 0 else None
    return snap


SWERVE_COLOR = "#86efac"        # the swing that moved the entry, and the planned entry it left


def _annotate_swerve(chart_main, res, is_long):
    """Mark the swing(s) that triggered the swerve rule and, when the entry
    actually moved, the planned entry price it was moved off -- see
    _swerve_entry. No-op when the rule never triggered for this trade."""
    sw = res.get("swerve")
    snap = _snapper(chart_main)
    if not sw or snap is None:
        return
    chart_main.setdefault("priceLines", []).append({
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
        chart_main["markers"].extend(new_markers)
        chart_main["markers"].sort(key=lambda m: m["time"])


def _target_ray_start(info, entry_t):
    """Epoch start of a target's ray: its opposite-M1 level's formation, else
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
    key = "formation_time" if res.get("stop_source") == "p0_spike" else "breakout_time"
    t = lvl.get(key)
    return None if t is None or pd.isna(t) else pd.Timestamp(t)


def _entry_ray_start_time(res):
    """The candle the entry price is built on: the entry level's own P0
    (own / m5 confluence / swerved-to level) or, for a crest-refined entry,
    the crest candle it was pushed from. None if unknown."""
    t = res.get("alt_formation_time")
    return None if t is None or pd.isna(t) else pd.Timestamp(t)


def _target_ray_start_times(res):
    """Every target rule's ray-start instant for this trade, so the M1 pane's
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


def _rayify_trade_lines(chart_main, res, row_for_chart):
    """Turn the M1 pane's full-width target / stop / entry / own-level price
    lines into rays: each starts at the candle it is built on and runs to the
    right edge, with its price (and title) on the y-axis.

    Starts: own level -> its P0 (formation) candle; entry -> the entry level's
    P0 (or the crest candle when refined); stop -> the spike P0 candle or the
    P1 thrust candle it was taken from; target -> its opposite-M1 level's
    formation, else its zigzag pivot candle; each falling back to the entry
    candle (the pane's ENTRY/PLANNED marker) when unknown. A start compressed out of the
    pane snaps to the first bar at or after it; one before the pane's first
    bar starts at the first bar. Other price lines (swerve's planned
    entry) are left as they are."""
    if chart_main is None or not chart_main["candles"]:
        return
    times = [c["time"] for c in chart_main["candles"]]
    entry_t = next((m["time"] for m in chart_main["markers"]
                    if m.get("text", "").startswith(("P2 ENTRY", "P2 PLANNED"))), times[0])
    target_t = _target_ray_start(res.get("target_info"), entry_t)
    own_t = R._to_epoch_utc(row_for_chart["formation_time"])
    stop_ts, entry_ts = _stop_ray_start_time(res), _entry_ray_start_time(res)
    stop_t = R._to_epoch_utc(stop_ts) if stop_ts is not None else entry_t
    entry_line_t = R._to_epoch_utc(entry_ts) if entry_ts is not None else entry_t
    starts = (("target", target_t), ("stop", stop_t), ("entry", entry_line_t),
              (f"M{BAR_MIN} {res['level_type']}", own_t))

    kept = []
    for pl in chart_main.get("priceLines", []):
        start = next((t for prefix, t in starts if pl["title"].startswith(prefix)), None)
        if start is None:
            kept.append(pl)
            continue
        pts = [{"time": t, "value": pl["price"]} for t in times if t >= start]
        chart_main.setdefault("rays", []).append({
            "points": pts, "color": pl["color"], "lineWidth": pl["lineWidth"],
            "lineStyle": pl["lineStyle"], "priceLabel": True, "title": pl["title"],
            "label": pl["title"],
        })
    chart_main["priceLines"] = kept


def _add_mode_views(chart_main, res, is_long):
    """Ship every target rule's own version of what the rule changes on the
    M1 pane, as chart_main['modeViews'][mode] = {ray, markers}: the
    target ray and the exit / trade-management markers. The browser (see
    chartForMode in JS) strips the default rule's versions of those from the
    pane -- target ray titled 'target', markers WIN/LOSS/CANDLE/'Stop ->'/RR FLOOR EXIT --
    and puts the ticked rule's in, so the pane follows the target-rule
    checkboxes the same way the row's cells do. Entry, stop and the candles
    never change with the rule. Needs _rayify_trade_lines to have run (it
    supplies the target ray's colour and style)."""
    snap = _snapper(chart_main)
    if snap is None or not res.get("modes"):
        return
    tmpl = next((r for r in chart_main.get("rays", []) if r["title"].startswith("target")), None)
    if tmpl is None:
        return
    times = [c["time"] for c in chart_main["candles"]]
    entry_t = next((m["time"] for m in chart_main["markers"]
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
    chart_main["modeViews"] = views
    chart_main["activeMode"] = res["active_mode"]


def _build_context_chart(bars, label, entry_t, exit_t, lo, hi, levels, is_long,
                         date_only=False, anchor=None, keep=None, touch_prices=(), arrow_only=False):
    """One plain candle pane (no LXPB levels) over bars[lo:hi], with the
    trade's own entry/stop/target/level lines and entry/exit markers. `levels`
    is a list of (price, color, lineStyle, title). Returns None when the bars
    do not cover the entry. `anchor` maps an instant to the bar time that
    contains it (D1 needs it; H1 snaps to the last bar at or before).

    `keep` -- optional (start, end) UTC pairs: only the bars inside one of them are drawn, and
    each stretch left out becomes a "[N bars skipped]" marker. The spans are the market structure
    the trade is decided on; bars that reached one of `touch_prices` (a tick) are kept too, two
    either side. keep=None draws every bar of [lo, hi]. `arrow_only` draws just the candles and one small
    arrow (no text, lines or tags) on the bar the trade is taken on."""
    if bars is None or bars.empty or entry_t < bars.index[0] or entry_t > bars.index[-1] + pd.Timedelta(days=1):
        return None
    win = bars.loc[lo:hi]
    if win.empty:
        return None
    skipped = []   # (candle position the gap precedes, bars left out)
    if keep is not None:
        idx = win.index
        mask = np.zeros(len(win), dtype=bool)
        for s0, s1 in keep:
            mask |= (idx >= s0) & (idx <= s1)
        lows, highs = win["low"].to_numpy(float), win["high"].to_numpy(float)
        for tp in touch_prices:
            hit = (lows <= tp + 0.25) & (highs >= tp - 0.25)
            mask |= np.convolve(hit.astype(int), np.ones(5, dtype=int), mode="same") > 0
        mask[:2] = mask[-2:] = True
        run = 0
        for k, kept in enumerate(mask):
            if kept:
                if run and k:
                    skipped.append((int(mask[:k].sum()), run))
                run = 0
            else:
                run += 1
        win = win[mask]
    candles = [{"time": R._to_epoch_utc(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in win.iterrows()]
    times = [c["time"] for c in candles]

    def snap(ts):
        if anchor is not None:
            ts = anchor(ts)
        pos = bisect.bisect_right(times, int(pd.Timestamp(ts).timestamp())) - 1
        return times[max(pos, 0)]

    if arrow_only:
        return {"title": label, "candles": candles, "priceLines": [], "rays": [], "precision": 2,
                "dateOnly": date_only, "minimal": True,
                "markers": [{"time": snap(entry_t), "position": "belowBar" if is_long else "aboveBar",
                             "color": R.ENTRY_COLOR, "shape": "arrowUp" if is_long else "arrowDown",
                             "text": "", "size": 2}]}
    markers = [{"time": snap(entry_t), "position": "belowBar" if is_long else "aboveBar",
                "color": R.ENTRY_COLOR, "shape": "circle", "text": "entry"}]
    if exit_t is not None:
        markers.append({"time": snap(exit_t), "position": "aboveBar" if is_long else "belowBar",
                        "color": "#fbbf24", "shape": "square", "text": "exit"})
    for pos, n in skipped:
        markers.append({"time": times[pos], "position": "aboveBar", "color": "#9ca3af", "shape": "square",
                        "text": f"[{n} bars skipped]"})
    markers.sort(key=lambda m: m["time"])
    lines = [{"price": p, "color": c, "lineWidth": 1, "lineStyle": s, "title": t}
             for p, c, s, t in levels]
    return {"title": f"{label}  |  {R._to_pt_str(win.index[0])} → {R._to_pt_str(win.index[-1])}"
                     + (f"  [{sum(n for _, n in skipped)} bars skipped]" if skipped else ""),
            "candles": candles, "markers": markers, "priceLines": lines, "rays": [],
            "precision": 2, "dateOnly": date_only}


CTX_M5_HOURS_BEFORE = 8
CTX_M5_HOURS_AFTER = 2


def _trade_context(res, filled):
    """(is_long, entry_t, exit_t, end_t, levels, touch_prices) of a trade for the context panes:
    UTC instants, and the level/entry/stop/target lines (price, colour, style, title)."""
    is_long = res["is_long"] if "is_long" in res else res["level_type"] == "LHPB"
    if filled:
        entry_t = pd.Timestamp(res["touch_time_alt"]).tz_convert("UTC")
        exit_t = res["resolved"].get("exit_time")
        fill, stop_pts, tgt_pts = res["fill_price"], res["stop_pts"], res["target_pts"]
        stop_px = fill - stop_pts if is_long else fill + stop_pts
        tgt_px = fill + tgt_pts if is_long else fill - tgt_pts
        levels = [(res["own_price"], R.LEVEL_COLOR, 0, f"level {res['own_price']:.2f}"),
                  (fill, R.ENTRY_COLOR, 0, f"entry {fill:.2f}"),
                  (stop_px, SR.EXIT_LOSS_COLOR, 2, "stop"),
                  (tgt_px, SR.EXIT_WIN_COLOR, 2, "target")]
        touch_prices = (fill, stop_px, tgt_px)
    else:
        entry_t = pd.Timestamp(res["row"]["retest_time"])
        entry_t = entry_t.tz_localize("UTC") if entry_t.tzinfo is None else entry_t.tz_convert("UTC")
        exit_t = None
        levels = [(res["own_price"], R.LEVEL_COLOR, 0, f"level {res['own_price']:.2f}")]
        touch_prices = ()
    if exit_t is not None:
        exit_t = pd.Timestamp(exit_t)
        exit_t = exit_t.tz_localize("UTC") if exit_t.tzinfo is None else exit_t.tz_convert("UTC")
    end_t = exit_t if exit_t is not None else entry_t
    return is_long, entry_t, exit_t, end_t, levels, touch_prices


# How much of a range box is kept as every candle in each pane. A longer box (a box can be days or weeks
# old) keeps the stretch before its breakout whole, plus a few bars around its start and around every
# crest/trough of the box (the structure the range is made of); only the bars in between are skipped.
MAIN_KEEP_WHOLE = pd.Timedelta(hours=36)   # M1 trading pane
CTX_KEEP_WHOLE = pd.Timedelta(days=4)      # M5 context pane
MAIN_PIVOT_PAD = pd.Timedelta(minutes=12)  # around each pivot in the M1 pane (the pivot is an M5 bar)
CTX_PIVOT_PAD = pd.Timedelta(minutes=60)


def _structure_keep(spans, whole, pad):
    """(start, end) UTC pairs to keep from range-box `spans` [(start, end, pivots)]: a span no longer than
    `whole` entirely; a longer one as its last `whole`, plus `pad` either side of its start and of each pivot."""
    out = []
    for s0, s1, pivots in spans:
        if s1 - s0 <= whole:
            out.append((s0, s1))
            continue
        out.append((s1 - whole, s1))
        out.append((s0 - pad, s0 + pad))
        for pv in pivots or ():
            t = pd.Timestamp(pv["time"])
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            out.append((t - pad, t + 5 * pad / 12))   # pivot bar is 5 minutes long
    return out


def _build_context_charts(res, filled, range_spans=()):
    """The REAL M5 context pane (R._display_m5(), never the M1 series the strategy trades on)
    around the trade, or None when the M5 bars do not cover it. Filled trades get
    entry/stop/target lines and an exit marker; unfilled rows get only the level and the
    retest instant. `range_spans` [(start, end, pivots)] are the drawn range boxes: the pane reaches
    back to the earliest and keeps their structure (_structure_keep), skipping the rest."""
    is_long, entry_t, exit_t, end_t, levels, touch_prices = _trade_context(res, filled)
    m5 = R._display_m5()  # the REAL M5 series -- never the patched M1 one (see LC.m5_bars_continuous)
    lo = entry_t - pd.Timedelta(hours=CTX_M5_HOURS_BEFORE)
    hi = end_t + pd.Timedelta(hours=CTX_M5_HOURS_AFTER)
    keep = None
    if range_spans:
        lo = min(lo, min(s0 for s0, _, _ in range_spans) - pd.Timedelta(minutes=30))
        keep = [(entry_t - pd.Timedelta(hours=CTX_M5_HOURS_BEFORE), hi)] + \
            _structure_keep(range_spans, CTX_KEEP_WHOLE, CTX_PIVOT_PAD)
    return _build_context_chart(m5, "M5", entry_t, exit_t, lo, hi, levels, is_long,
                                keep=keep, touch_prices=touch_prices)


CTX_H1_BARS_BEFORE = 150
CTX_H1_BARS_AFTER = 30
CTX_D1_DAYS_BEFORE = 90
CTX_D1_DAYS_AFTER = 10
_D1_CACHE = None


def _display_d1():
    """Daily bars aggregated from the H1 series (R._display_h1), one per ES trading day. A session runs
    15:00 PT (prior calendar day) to 14:00 PT, so shifting an H1 bar's PT time forward 9h lands every
    bar of a session on that session's closing PT date. Each bar's `time` is noon PT of that date so the
    browser's PT date label reads the trading date. Cached."""
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
    """The D1 bar `time` (noon PT of the trading date, see _display_d1) of the session containing `ts`."""
    pt = pd.Timestamp(ts).tz_convert("America/Los_Angeles") + pd.Timedelta(hours=9)
    return pd.Timestamp(f"{pt.date()} 12:00", tz="America/Los_Angeles").tz_convert("UTC")


def _build_htf_charts(res, filled, range_spans=()):
    """{'h1': ..., 'd1': ...} higher-timeframe panes of a trade (None where the bars do not cover it): every
    bar, and one small arrow on the bar the trade is taken on, with no labels, lines or text. They reach
    back to the earliest drawn range box (H1 at most 60 days)."""
    is_long, entry_t, exit_t, end_t, levels, _ = _trade_context(res, filled)
    earliest = min((s0 for s0, _, _ in range_spans), default=None)
    lo_h1 = entry_t - pd.Timedelta(hours=CTX_H1_BARS_BEFORE)
    lo_d1 = entry_t - pd.Timedelta(days=CTX_D1_DAYS_BEFORE)
    if earliest is not None:
        lo_h1 = min(lo_h1, earliest - pd.Timedelta(hours=12))
        lo_d1 = min(lo_d1, earliest - pd.Timedelta(days=3))
    h1 = _build_context_chart(
        R._display_h1(), "H1", entry_t, exit_t, lo_h1,
        end_t + pd.Timedelta(hours=CTX_H1_BARS_AFTER), levels, is_long, arrow_only=True)
    d1 = _build_context_chart(
        _display_d1(), "D1", entry_t, exit_t, lo_d1, end_t + pd.Timedelta(days=CTX_D1_DAYS_AFTER),
        levels, is_long, date_only=True, anchor=_d1_bar_time, arrow_only=True)
    return {"h1": h1, "d1": d1}


# Bars always kept on each side of P0 and of P2 (retest) in the M5 pane; the rest may be compressed.
P0_P2_PAD_BARS = 100


def _row_for_chart(res):
    row_for_chart = res["row"].copy()
    row_for_chart["price"] = res["fill_price"]
    # build_m5_chart does `pd.Timestamp(row["retest_time"], tz="UTC")` (and
    # likewise for breakout_time), which raises on an already tz-aware
    # value -- unlike the H1 reference report's naive-epoch-UTC timestamps,
    # the M5 ledger's time columns are tz-aware UTC (see build_ledger's
    # _TIME_COLS conversion), so strip tz here (same absolute instant).
    for col in ("retest_time", "breakout_time"):
        ts = pd.Timestamp(row_for_chart[col])
        if ts.tzinfo is not None:
            row_for_chart[col] = ts.tz_convert("UTC").tz_localize(None)
    return row_for_chart


def _drop_level_rays(chart):
    """The M1 pane's other live M1 LXPB levels (the blue solid/dotted rays above and below the trade,
    SR.M5_COLOR) are not wanted on this report: only the trade's own entry-level ray and its
    other lines stay. Their formation bars still count for the pane's window."""
    if chart:
        chart["rays"] = [r for r in chart.get("rays", []) if r.get("color") != SR.M5_COLOR]


def _build_price_panes(res, range_spans=()):
    """{'main': M1 pane, 'ctx': M5 pane, 'h1', 'd1'} of a filled trade. `range_spans` are
    (start, end, pivots) per drawn range box: their structure is kept in the M1 and M5 panes
    (_structure_keep) and the H1/D1 panes reach back to the earliest."""
    resolved = res["resolved"]
    stop_pts, target_pts = res["stop_pts"], res["target_pts"]
    row_for_chart = _row_for_chart(res)
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
    chart_main = SR.build_m5_chart(
        row_for_chart, resolved, stop_pts, target_pts,
        level_price=res["own_price"], entry_level=res["entry_m5_level"],
        p1_bar_width=P1_BAR_WIDTH, p1_label=f"M{BAR_MIN}", bar_label=f"M{BAR_MIN}",
        # This M5 pane IS the primary structural chart here (no H1 pane
        # exists), and now also carries the C1/C2/... candidate markers
        # (_annotate_candidates) -- double the shared defaults so the
        # wider, full-row pane (see chart-row-solo below) actually shows
        # more market structure instead of just more empty space.
        bars_before_retest=2 * SR.M5_BARS_BEFORE_RETEST,
        bars_after_exit=2 * SR.M5_BARS_AFTER_EXIT,
        p0_p2_pad_bars=P0_P2_PAD_BARS,
        extra_context_times=extra_context_times, plain_p0=res["plain_p0"],
        protect_spans=_structure_keep(range_spans, MAIN_KEEP_WHOLE, MAIN_PIVOT_PAD),
        touch_prices=_trade_context(res, True)[5])
    if chart_main is not None:
        chart_main["title"] += (f"  |  R {res['r_multiple']:.2f}  |  entry via "
                              f"{res['alt_source']} ({res['group_n']} in group)")
        _annotate_p0_p1_p2(chart_main, row_for_chart, res["is_long"])
        _annotate_candidates(chart_main, row_for_chart, res["is_long"], sibling_group,
                            res["cluster_member_formations"])
        _annotate_mgmt_events(chart_main, res, res["is_long"])
        _annotate_swerve(chart_main, res, res["is_long"])
        _rayify_trade_lines(chart_main, res, row_for_chart)
        _drop_level_rays(chart_main)
        _add_mode_views(chart_main, res, res["is_long"])
    return {"main": chart_main, "ctx": _build_context_charts(res, filled=True, range_spans=range_spans),
            **_build_htf_charts(res, True, range_spans)}


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



# (slot, ray/marker colour, chart-title prefix). On the chart itself the colour says which
# playbook, so the rays and markers just read "range ...". The candles are never recoloured.
RANGE_DRAW = (("from", "#fde68a", "fade"),)
RANGE_SPAN_PAD = pd.Timedelta(minutes=15)


def _range_spans(res):
    """(start, end, pivots) UTC triples of every box drawn for the trade: each box's start..breakout (or the
    touch, for a box with no break bar), padded a little, with the box's own crests and troughs."""
    rbx = res.get("range_box") or {}
    touch = pd.Timestamp(res["touch_time_alt"]).tz_convert("UTC")
    spans = []
    for key, *_ in RANGE_DRAW:
        for b in rbx.get(key, ()):
            ends = [pd.Timestamp(t) for t in (b.get("break_time"), b.get("strong_time"))
                    if t is not None and pd.notna(t)]
            end = max(ends) if ends else touch
            start = pd.Timestamp(b["start"])
            spans.append((start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC"),
                          (end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC"))
                          + RANGE_SPAN_PAD, b.get("pivots") or []))
    return spans


def _mark_range_box(chart, b, color, label, letter, tagged):
    """High/low rays from the box start to the pane's last bar, plus a dotted vertical line at
    the box start and at its breakout (when the box has one in view). Display only: the candles keep
    their own colours. `letter` names the box when the trade has several (A, B, ...), so
    the short chart labels tell them apart; `tagged` is the set of prices that already
    carry an axis tag (boxes sharing an edge price get one tag, not two on top of each other)."""
    if chart is None or not chart.get("candles"):
        return
    times = [c["time"] for c in chart["candles"]]
    e0 = R._to_epoch_utc(b["start"])
    bt = b.get("break_time")
    # The breakout is marked on the bar that graded STRONG (the episode's), not the box's
    # zigzag break bar, which can be an earlier close outside the box that never graded.
    st = b.get("strong_time")
    st = bt if st is None or pd.isna(st) else st
    e1 = R._to_epoch_utc(st) if st is not None and pd.notna(st) else times[-1]
    hi, lo = b["high"], b["low"]
    up = b.get("edge") == "upper"
    way = "up" if up else "down"
    hm = lambda e: pd.Timestamp(e, unit="s", tz="UTC").tz_convert("America/Los_Angeles").strftime("%H:%M")
    name = f"range {letter}" if letter else "range"
    broke = f"broke {way} {R._to_pt_str(st)}" if st is not None and pd.notna(st) else "still open"
    for px, edge in ((hi, "high"), (lo, "low")):
        pts = [{"time": t, "value": px} for t in times if t >= e0]
        if pts:
            first = (id(chart), px) not in tagged
            tagged.add((id(chart), px))
            # The axis tag prints the price itself; the hover text adds the box's own times.
            chart.setdefault("rays", []).append({
                "points": pts, "color": color, "lineWidth": 1, "lineStyle": 1,
                "priceLabel": first, "title": f"{name} {edge}",
                "label": f"{name} {edge} {px:.2f} (box from {R._to_pt_str(b['start'])}, {broke})"})
    # Start and breakout: a dotted vertical line through the pane with a label on a lane along its
    # top (drawn by the page script), so nothing sits on the candles or hides a wick.
    pre = f"{letter} " if letter else ""
    nth = {1: " 1st", 2: " 2nd"}.get(b.get("fade_no"), "")   # 1st breakout of the box, or the 2nd (other side first)
    lines = [(e0, f"{pre}start {hm(e0)}", False),
             (e1, f"{pre}broke {way} {hm(e1)}{nth}", True)]
    pt = b.get("prior_time")
    if b.get("fade_no") == 2 and pt is not None and pd.notna(pt):
        lines.insert(1, (R._to_epoch_utc(pt), f"{pre}1st broke {b['prior_dir']} {hm(R._to_epoch_utc(pt))}", True))
    # How far from a bar an instant may be and still sit on it: 5 minutes, or the pane's own bar for H1/D1.
    tol = max(300, int(pd.Series(times).diff().median())) if len(times) > 2 else 300
    for e, text, is_end in lines:
        if is_end and (st is None or pd.isna(st)):
            continue
        if is_end or tol > 300:   # the bar holding the instant, whatever the pane's bar size
            i = bisect.bisect_right(times, e) - 1
            ok = 0 <= i and e - times[i] < tol
        elif e < times[0] - tol:   # the box began before this pane's first bar: label it at the left edge
            i, ok = 0, True
            text = (f"{pre}\u25c4 start "
                    f"{pd.Timestamp(e, unit='s', tz='UTC').tz_convert('America/Los_Angeles'):%m/%d %H:%M}")
        else:
            i = bisect.bisect_left(times, e)
            ok = i < len(times) and times[i] < e + 300
        if not is_end and tol > 300 and not ok and e < times[0]:   # H1/D1: box began before the pane
            i, ok = 0, True
            text = (f"{pre}\u25c4 start "
                    f"{pd.Timestamp(e, unit='s', tz='UTC').tz_convert('America/Los_Angeles'):%m/%d %H:%M}")
        if ok:
            chart.setdefault("vlines", []).append({"time": times[i], "color": color, "label": text})
    chart["title"] += (f"  |  {label} {name} {R._to_pt_str(b['start'])} -> "
                       f"{R._to_pt_str(st) if st is not None and pd.notna(st) else 'open'}, "
                       f"high {hi:.2f} / low {lo:.2f}")


def _mark_range_pivots(chart, boxes):
    """Label the zigzag crests (Cr) and troughs (Tr) behind the range boxes on the trading pane,
    numbered in order within each box (Cr1 Tr2 Cr3 ...; the pivot that broke the box adds
    "brk"; "Cr", not "C", because C1/4 already names a retest cluster), so the box's edges can be checked by eye. `boxes` is [(letter, colour, box)].
    A crest sits above the candle that made its high and a trough below the candle that made
    its low (the pivot is an M5 extreme, so it is the pane candle inside that M5 bar with the
    same high/low); small squares, so no candle or wick is covered. A pivot two boxes share
    gets one marker carrying both names."""
    if chart is None or not chart.get("candles"):
        return
    candles = chart["candles"]
    times = [c["time"] for c in candles]
    found = {}   # (candle time, kind) -> [colour, [names]]
    for letter, color, b in boxes:
        for n, p in enumerate(b.get("pivots") or (), 1):
            crest = p["kind"] == "crest"
            e = R._to_epoch_utc(p["time"])
            i = bisect.bisect_left(times, e)
            inside = []
            while i < len(times) and times[i] < e + 300:
                inside.append(candles[i])
                i += 1
            if not inside:
                continue   # the pane does not reach this pivot
            c = (max(inside, key=lambda k: (k["high"], -k["time"])) if crest
                 else min(inside, key=lambda k: (k["low"], k["time"])))   # first candle at the extreme
            name = f"{'Cr' if crest else 'Tr'}{n}{' brk' if p.get('brk') else ''}"
            if letter:
                name = f"{letter}:{name}"
            slot = found.setdefault((c["time"], crest), [color, []])
            slot[1].append(name)
    markers = chart.setdefault("markers", [])
    for (t, crest), (color, names) in found.items():
        markers.append({"time": t, "position": "aboveBar" if crest else "belowBar",
                        "color": color, "shape": "square", "text": " ".join(names)})
    markers.sort(key=lambda m: m["time"])


def _draw_range_boxes(results, chart_stacks):
    """Mark the boxes behind each tag (res['range_box']) on the main pane
    and on the M5 context pane. A trade with several boxes names them A, B, ... in
    start order: the chart labels stay short, the hover text has the times."""
    for res, stack in zip(results, chart_stacks):
        rbx = res.get("range_box")
        if not rbx or not stack:
            continue
        boxes = sorted(((b["start"], key, color, label, b)
                        for key, color, label in RANGE_DRAW for b in rbx.get(key, ())),
                       key=lambda t: (t[0], t[1]))
        letters = [chr(ord("A") + k) for k in range(len(boxes))] if len(boxes) > 1 else [None]
        tagged = set()
        for (_, _, color, label, b), letter in zip(boxes, letters):
            for pane in ("main", "ctx"):
                _mark_range_box(stack.get(pane), b, color, label, letter, tagged)
        _mark_range_pivots(stack.get("main"),
                           [(letter, color, b) for (_, _, color, _, b), letter in zip(boxes, letters)])


N_COLS = 25  # keep in sync with `head` below and every colspan in this section

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
        "news_pull_blocked": "NO TRADE (retest during news-pull blackout)",
        "weak_run_in": "NO TRADE (run-in into touch too weak)",
        "no_target": "NO TRADE (no target under any rule)",
        "no_tick_data_after_fill": "NO DATA after fill",
        "no_stop": "NO TRADE (no qualifying M1 breakout-candle stop)",
        "degenerate_stop": "NO TRADE (degenerate stop)",
    }.get(reason, reason.replace("_", " "))


def _target_title(info):
    """Tooltip for the Target cell, per target source (see _pick_targets)."""
    if not info:
        return "no target info"
    level = info.get("level")
    if info.get("src") == "range_edge":
        return (f"Range edge: the {info['edge']} of the range box this trade fades, "
                f"{info['pivot_price']:.2f} (box {info['box_low']:.2f}-{info['box_high']:.2f} from "
                f"{R._to_pt_str(info['pivot_time'])}"
                + (f"; nearest of {info['boxes']} boxes" if info["boxes"] > 1 else "") + ")")
    if info.get("src") == "swing_extreme":
        kind = info["pivot_kind"]
        return (f"Most recent confirmed zigzag {kind} (P1&hellip;P2): "
                f"{info['pivot_price']:.2f} at {R._to_pt_str(info['pivot_time'])} "
                f"-- the pivot's own price is the target")
    return (f"Farthest live M{BAR_MIN} {level['type']} P0 after the zigzag pivot: "
            f"{R._to_pt_str(level['formation_time'])}; pivot "
            f"{info['pivot_price']:.2f} at {R._to_pt_str(info['pivot_time'])}")


def _is_win(res):
    r = res["resolved"].get("r")
    return r is not None and r > 1e-9


def _stats_block(rows):
    """n, wins (trades that made money), losses, win rate, avg R and total R of the engine's closed trades."""
    vals = [r["resolved"]["r"] for r in rows if r["resolved"].get("r") is not None]
    wins = sum(1 for r in rows if _is_win(r))
    n = len(vals)
    return {"n": n, "wins": wins, "losses": n - wins, "win_rate": (wins / n * 100.0) if n else 0.0,
            "avg_r": (float(np.mean(vals)) if vals else 0.0), "total_r": (float(np.sum(vals)) if vals else 0.0)}


def _outcome_label(resolved):
    """(label, css class) of an engine exit; coloured by the sign of the R it returned."""
    o, r = resolved.get("outcome"), resolved.get("r")
    cls = "" if r is None or abs(r) < 1e-9 else ("good" if r > 0 else "bad")
    return {"target": ("WIN", "good"), "stop": ("LOSS", "bad"), "trail_stop": ("TRAIL STOP", cls),
            "rr_floor": ("RR FLOOR", cls), "eod_flat": ("EOD FLAT", cls), "no_hit": ("OPEN", "")}.get(o, ("NO DATA", ""))


def _compute_report_stats(filled):
    """(stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
    pctile_html) for a population of FILLED results -- the server-computed
    BASELINE (dynamic-filter-tagged trades, e.g. globex_eth_open, are still
    counted here; excluding them is a live, client-side toggle -- see the
    'Dynamic filters' block in _finish_report's JS)."""
    stats = _stats_block(filled)
    improved_n = sum(1 for r in filled if r["improved"])

    win_mae_rows = [r for r in filled if _is_win(r) and r.get("adverse_pts") is not None]
    loss_mfe_rows = [r for r in filled if not _is_win(r) and r.get("favorable_pts") is not None]
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
         "giveback_pts", [r for r in filled if _is_win(r) and r.get("giveback_pts") is not None]),
    ]
    pctile_html = SR.excursion_percentile_html(
        [(label, note, [r[field] for r in population])
         for label, note, field, population in excursion_groups],
        stop=None, group_stops=[[r["stop_pts"] for r in population]
                                for _, _, _, population in excursion_groups])
    return stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries, pctile_html


def _run_engine(args):
    """Replay the window's ticks through the live engine (the only thing that decides the fade trades)."""
    lo = pd.Timestamp(args.start, tz="UTC")
    hi = pd.Timestamp(args.end, tz="UTC")
    bars_lo = args.bars_lo_utc
    scid_end = pd.Timestamp(R._scid_bounds(R.CONTRACTS[-1][0])[1]).floor("1min")
    hi = min(hi, scid_end)
    params = Params(bar_minutes=BAR_MIN, decision_s=args.decision_s, box_merge_overlap=args.box_merge_overlap,
                    box_merge_tol=args.box_merge_tol, fade2=bool(args.fade2),
                    target_mode=args.fade_target, box_detector=args.box_detector)
    warm_days = (lo - bars_lo) / pd.Timedelta(days=1)
    eng, start = RP.build_engine(lo, hi, warm_days, params, h1_days=args.h1_days)
    print(f"[engine] replay {start} -> {hi} (armed from {lo}), M{BAR_MIN} trading bars, decisions every "
          f"{args.decision_s:g}s, range merge >= {args.box_merge_overlap:.0%}, fade 2 {'ON' if args.fade2 else 'off'}", flush=True)
    # One progress line per trading day (PT date, %, ETA, memory, trades so far), and the trades closed so far
    # rewritten after every day beside the output, so a long run can be followed and its results read mid-run.
    n_ticks, secs = RP.feed(eng, start, hi, progress=True, checkpoint=(args.output + ".trades.jsonl") if args.output else None)
    print(f"[engine] {n_ticks:,} ticks in {secs:.0f}s: {len(eng.trades)} trades "
          f"({sum(1 for t in eng.trades if all(pb == 'fade' for _, _, pb in t['serves']))} fade)", flush=True)
    return eng


def render(args):
    eng = _run_engine(args)
    ledger = LC.m5_levels(verbose=False, plain_p0=LC.PLAIN_P0_TRACKED)
    results = ER.engine_results(eng, ledger, args.min_r)
    for res in results:
        res["plain_p0"] = LC.PLAIN_P0_TRACKED
    print(f"{len(results)} fade trades from the engine in [{args.start}, {args.end}]", flush=True)
    chart_stacks = []
    for res in results:
        chart_stacks.append(_build_price_panes(res, _range_spans(res)))
    _draw_range_boxes(results, chart_stacks)
    tag_counts = {}
    for r in results:
        for tag in list(r.get("dyn_tags", ())) + r["modes"][r["active_mode"]]["dyn_tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for tag, n in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
        print(f"{n} trade(s) tagged '{tag}'", flush=True)

    filled = list(results)
    stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries, pctile_html = _compute_report_stats(filled)
    charts = []
    rows_html = []
    for idx, res in enumerate(results):
        chart_entry, row_html = _render_row(idx, res, chart_stacks)
        charts.append(chart_entry)
        rows_html.append(row_html)

    _finish_report(args, results, filled, stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
                   pctile_html, rows_html, charts, args.output)


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
    outcome_label, outcome_cls = _outcome_label(resolved)
    r_val = resolved.get("r")
    pnl_pts = (r_val * res["stop_pts"]) if r_val is not None else None
    exit_px = resolved.get("exit_price")

    def fmt(v):
        return f"{v:.2f}" if v is not None else "-"

    return {
        "dist": m["target_pts"],
        "tgt": (f'{m["target_price"]:.2f}<span class="src-tag m5">'
                f'{info.get("src", "opposite_zz")}</span>'),
        "tgtTitle": _target_title(info),
        "rr": f'{m["r_multiple"]:.2f}',
        "rrVal": f'{m["r_multiple"]:.6f}',
        "outcomeLabel": outcome_label,
        "outcomeCls": outcome_cls,
        "modeTagBadges": _mode_tag_badges(res, mode),
        "mgmtBadge": _mgmt_badge(res, mode),
        "exit": (_pt_str_stacked(resolved["exit_time"])
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
        # Seconds from the fill to this rule's exit (baseline / managed): the
        # quick-exit check only acts on a trade still open at its second.
    }


ENGINE_TARGET_TEXT = {
    "range_edge": "the edge of the faded range, the range LOW for a long (LHPB below the range), the range HIGH for a short "
                  "(LLPB above it); the H1 box edges for a merged range (src range_edge)",
    "zigzag": "the most recent confirmed zigzag trough (long) / crest (short) since the level's P1 (src swing_extreme)",
    "opposite_zz": "the farthest standing opposite-type P0 formed after the most recent confirmed zigzag crest (long) / "
                   "trough (short) since P1 (src opposite_zz)",
}


def _target_chips(page_rule):
    """The "Target rules" row of the filter panel, as the old M1/M2 reports had it (same three rules, labels and
    live behaviour): every trade ships each rule's own bracket (the engine follows the same fill and stop with every
    rule's target, plain and managed, engine `_views`), so ticking / unticking a rule re-resolves the rows and the stats
    in place. Several ticked: each row uses the rule with the FARTHER target. A row whose ticked rules have no target
    becomes NO TARGET (dimmed, out of the stats). Ticked on load: --page-target (default opposite-zz, user 2026-10-28),
    which may differ from the rule the engine traded with (--fade-target)."""
    chips = [("opposite_zz", "opposite-zz", f"Opposite M{BAR_MIN} level, zigzag anchor (P1&hellip;fill)"),
             ("zigzag", "swing-extreme", "Swing extreme: last zigzag trough/crest (P1&hellip;P2)"),
             ("range_edge", "range-edge", "Range edge: faded box high (LLPB) / low (LHPB)")]
    out = [r"""    <span class="filter-label" title="Which TARGET RULE(S) each trade exits on -- live, in the
browser, with no Python regen. Every trade ships with EVERY rule's brackets and outcomes
precomputed (the engine follows the same fill and stop under each rule), so unticking a rule
re-resolves each row against whichever others are still on: a trade can become a NO TARGET row
(dimmed, dropped from the stats) if the rule it was using was the only one that found a target.
With several ticked, each trade uses whichever rule offers the FARTHER target. Untick all and no
trade has a target at all. The trades themselves are the engine's, taken with its own rule
(range edge); ticked on load: opposite-zz. Another rule only changes how the same trades would have ended.">Target rules</span>"""]
    for rule, mode, label in chips:
        on = " checked" if rule == page_rule else ""
        out.append(f'    <label class="chip"><input type="checkbox" class="f-target-mode" data-mode="{mode}"{on}>\n'
                   f'      {label}</label>')
    return "\n".join(out)


def _attr_json(obj):
    """`obj` as JSON safe to sit inside a double-quoted HTML attribute."""
    return (json.dumps(obj).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _row_tag_badges(res, dyn_tags, level_type, entry_touch_str):
    """Badge HTML for the ROW-level dynamic-filter tags (globex_eth_open,
    swerved, swerve_blocked, crest_refined, spike_confl,
    fading_bias) -- the ones that
    don't depend on which target rule is active, so they render once and
    never get rewritten by applyTargetModes() (unlike _mode_tag_badges'
    r_below_min/eod_flat). Lives in the Tags column (see _render_row).

    Shared by both the filled and unfilled row paths: swerve_blocked,
    crest_refined and fading_bias can land on either (swerve/crest_refine are
    decided before the fill-window search even runs, and the bias is read off
    the H1 candles before the retest), while globex_eth_open only ever tags a
    FILLED result (its own apply_* filter skips unfilled rows), so that branch is
    simply never reached when dyn_tags came from an unfilled row's tags alone."""
    out = ""
    if "plain_p0" in dyn_tags:
        out += ('<span class="dyn-tag-badge" title="Dynamic filter ‘plain_p0’: the level '
                'this trade retests is a plain-P0 -- neither a spike nor a consolidating swing '
                'at its formation. Only an all-P0-kinds report trades these. Informational; '
                'not excluded from the headline stats by default.">PLAIN-P0</span>')
    if "no_confl" in dyn_tags:
        out += ('<span class="dyn-tag-badge" title="Dynamic filter ‘no_confl’: no other '
                'same-side M1 level was live within the confluence radius at this level&#39;s '
                'own P1 (SS Confl 0), so an SS Confl &ge; 1 report would not have taken it. '
                'Informational; not excluded from the headline stats by default.">NO CONFL</span>')
    if "globex_eth_open" in dyn_tags:
        out += (f'<span class="dyn-tag-badge" title="Dynamic filter '
                f'‘globex_eth_open’: this fill landed at {entry_touch_str}, inside the '
                f'daily Globex/ETH reopen window (15:00-15:05 PT) -- excluded by default. '
                f'Toggle the Dynamic filters checkbox in the panel above to include '
                f'it.">GLOBEX OPEN</span>')
    if "swerved" in dyn_tags:
        sw = res["swerve"]
        sw_str = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
        out += (f'<span class="dyn-tag-badge swerve-tag-badge" title="Dynamic filter '
                f'‘swerved’: a confirmed M{BAR_MIN} swing sat on the planned entry '
                f'{sw["planned_price"]:.2f} ({sw_str}), so the order was moved to the '
                f'next live M{BAR_MIN} {level_type} level at {sw["price"]:.2f}.">'
                f'SWERVED {sw["planned_price"]:.2f}&rarr;{sw["price"]:.2f}</span>')
    for tag, key, label, meaning in (
            (RB.TAG_FROM_RANGE, "from", "FROM RANGE",
             "this trade fades a fresh breakout out of this range (the run-in leg into the level is the one that broke it), not retested yet"),):
        if tag in dyn_tags:
            boxes = (res.get("range_box") or {}).get(key) or []
            detail = "; ".join(
                f'{b["low"]:.2f}-{b["high"]:.2f} from {R._to_pt_str(b["start"])} '
                f'({b["state"] or b["status"]}'
                f'{", " + b["breakout"] + " breakout" if b["breakout"] else ""}'
                + (f'; H1 range box holding {b["merged_n"]} merged M5 boxes, the breakout box itself was '
                   f'{b["m5_low"]:.2f}-{b["m5_high"]:.2f}' if b.get("h1") else "") + ")"
                for b in boxes[:4])
            out += (f'<span class="dyn-tag-badge range-box-tag-badge" title="Dynamic filter '
                    f'‘{tag}’: {meaning} (find_range_box_zz on M5, 1-minute lower '
                    f'timeframe, as the engine saw it at the fill): {detail}. Purely informational; not '
                    f'excluded from the headline stats by default.">{label}</span>')
    for tag, label in ((RB.TAG_FADE1, "FADE 1"), (RB.TAG_FADE2, "FADE 2"), (RB.TAG_FADE_MIXED, "FADE 1+2")):
        if tag in dyn_tags:
            parts = []
            for b in (res.get("range_box") or {}).get("from") or []:
                if b["fade_no"] == 2:
                    parts.append(f'{b["low"]:.2f}-{b["high"]:.2f}: second side &mdash; it had already broken '
                                 f'{b["prior_dir"]} at {R._to_pt_str(b["prior_time"])}, then this {"up" if b["edge"] == "upper" else "down"}'
                                 f' breakout at {R._to_pt_str(b["strong_time"])}')
                else:
                    parts.append(f'{b["low"]:.2f}-{b["high"]:.2f}: first breakout of the box, at '
                                 f'{R._to_pt_str(b["strong_time"])}')
            what = {RB.TAG_FADE2: "fades the SECOND-side strong breakout of a range: price had broken out one edge, "
                                  "reversed and broke out of the other",
                    RB.TAG_FADE1: "fades the FIRST strong breakout of a range (no earlier strong breakout of either edge)",
                    RB.TAG_FADE_MIXED: "fades several overlapping ranges, some at their FIRST strong breakout and some at "
                                       "their SECOND-side breakout (see each box)"}[tag]
            out += (f'<span class="dyn-tag-badge range-box-tag-badge" title="Dynamic filter &lsquo;{tag}&rsquo;: '
                    f'this trade {what}. Per box: {"; ".join(parts)}.">{label}</span>')
    return out



def _pt_str_stacked(ts):
    """Display-only variant of R._to_pt_str: date on one line, HH:MM:SS PT
    on the next (see the .time-stacked CSS rule), so the M5 retest / Entry
    (touch) time / Exit time columns are as wide as the longer of those two
    lines instead of the whole string. Never use this for row_key or a
    title= tooltip -- both need the plain one-line string R._to_pt_str
    already gives."""
    date, clock = R._to_pt_str(ts).split(" ", 1)
    return f'{date}<br><span class="time-part">{clock}</span>'


def _render_row(idx, res, chart_stacks):
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
        retest_str_display = _pt_str_stacked(row_d["retest_time"])
        members_str = ", ".join(f"{p:.2f}" for p in res["cluster_members"])
        if res.get("cluster_size", 1) > 1:
            entry_title = (f' title="{res["cluster_size"]} mutually-confluent M{BAR_MIN} levels '
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
            entry_touch_str_display = (_pt_str_stacked(touch_time_alt)
                                        if touch_time_alt is not None else "-")
            # No target modes here (never filled, so nothing to swap on a
            # target-rule toggle) -- just whichever row-level tags applied
            # before the fill-window search ran (see _row_tag_badges: e.g.
            # swerve_blocked/crest_refined can land on an unfilled row,
            # globex_eth_open never does).
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
                       f'<span class="entry-info">'
                       f'<span class="src-tag {res["alt_source"]}">{res["alt_source"]}</span>'
                       f'</span>')
            if res.get("stop_price") is not None:
                stop_cell = (f'{res["stop_price"]:.2f}'
                            f'<span class="src-tag m5">{res.get("stop_source", "")}</span>')
            else:
                stop_cell = "-"

            chart_stack = chart_stacks[idx]

            row_html = f"""
<tr class="lvl-row unfilled-row is-valid {type_cls}" data-idx="{idx}" data-key="{row_key}"
    data-dyn-tags="{base_tags_attr}" data-base-tags="{base_tags_attr}"
    onclick="toggleChart({idx})">
  <td class="left">{idx}</td>
  <td class="tags-cell">{tags_cell}</td>
  <td class="left type-cell">{level_type}</td>
  <td class="left time-stacked retest-cell">{retest_str_display}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td class="entry-cell">{alt_cell}</td>
  <td class="left time-stacked entry-touch-cell">{entry_touch_str_display}</td>
  <td class="stop-cell">{stop_cell}</td>
  <td class="tgt-cell">-</td>
  <td class="rr-cell">-</td>
  <td class="outcome-cell"><span class="outcome-label">{_fail_reason_label(reason)}{rr_note}</span></td>
  <td class="left time-stacked exit-cell">-</td><td class="exitpx-cell">-</td>
  <td class="pnl-cell">-</td>
  <td class="contracts-cell">-</td><td class="comm-cell">-</td>
  <td class="mae-cell">-</td><td class="mfe-cell">-</td><td class="gb-cell">-</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="done-cell" onclick="event.stopPropagation();"><input type="checkbox" class="done-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">▶</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-main-{idx}"></div><div class="chart-ph" id="c-main-{idx}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-ctx-{idx}"></div><div class="chart-ph" id="c-ctx-{idx}"></div></div>
    <div class="chart-half-row">
      <div class="chart-cell chart-half"><div class="chart-title" id="t-d1-{idx}"></div><div class="chart-ph" id="c-d1-{idx}"></div></div>
      <div class="chart-cell chart-half"><div class="chart-title" id="t-h1-{idx}"></div><div class="chart-ph" id="c-h1-{idx}"></div></div>
    </div>
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
        entry_touch_str_display = _pt_str_stacked(res["touch_time_alt"])
        src_cls = f"src-tag {res['alt_source']}"
        improved_flag = " &uarr;" if res["improved"] else ""
        stop_level = res["stop_m5_level"]
        stop_source = res.get("stop_source", "thrust")
        if stop_source == "p0_spike":
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
            stop_title = (f"Live M{BAR_MIN} {stop_level['type']} {stop_level['price']:.2f}, "
                         f"P0 {R._to_pt_str(stop_level['formation_time'])}; "
                         f"P1 {R._to_pt_str(stop_level['breakout_time'])}, "
                         f"{extreme} {stop_level[extreme]:.2f}; one tick "
                         f"{'below' if res['is_long'] else 'above'}")
            if stop_source == "wide_p1":
                stop_title = (f"Widened: the normal stop was under {MIN_STOP_PTS:g} pts from the "
                              f"fill, so the stop moved to the most recent older P1 candle that "
                              f"gives at least that. " + stop_title)
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
        ek = res.get("entry_kind") or {}
        ek_wick = ek.get("wick")
        lvl_attrs = (f'data-lvl-spike="{int(bool(ek.get("spike")))}" data-lvl-swing="{int(bool(ek.get("swing")))}" '
                     f'data-lvl-wick="{"" if ek_wick is None else f"{ek_wick:.2f}"}"')
        ek_title = (f"Entry level: {ek.get('p0_kind') or 'P0 kind unknown'}; formation candle's rejection wick "
                    f"({'upper' if level_type == 'LHPB' else 'lower'}) "
                    f"{'n/a' if ek_wick is None else f'{ek_wick:.2f} pt'}")
        if ek.get("spike"):
            row_badges += f'<span class="dyn-tag-badge lvl-kind-badge" title="{ek_title}">SPK</span>'
        if ek.get("swing"):
            row_badges += f'<span class="dyn-tag-badge lvl-kind-badge" title="{ek_title}">SWG</span>'
        if ek_wick is not None:
            row_badges += f'<span class="dyn-tag-badge lvl-kind-badge" title="{ek_title}">W{ek_wick:.2f}</span>'
        tags_cell = (f'<span class="row-badges">{row_badges}</span>'
                    f'<span class="mode-tag-badges">{act["modeTagBadges"]}</span>')
        modes_attr = _attr_json(payloads)
        mes_attrs, mes_contracts_html, mes_comm_text, mes_title = _mes_cells(res["stop_pts"])

        chart_stack = chart_stacks[idx]

        row_html = f"""
<tr class="lvl-row is-valid {type_cls}" data-idx="{idx}" data-key="{row_key}"
    data-dyn-tags="{dyn_tags_attr}" data-base-tags="{base_tags_attr}"
    data-r="{act['r']}" data-rr="{act['rrVal']}" data-pnl-pts="{act['pnlPts']}" data-outcome="{act['outcome']}"
    data-mgmt-r="{act['mgmtR']}" data-mgmt-pnl-pts="{act['mgmtPnl']}"
    data-mgmt-outcome="{act['mgmtOutcome']}" data-mgmt-fired="{act['mgmtFired']}"
    data-mode="{active_mode}" data-modes="{modes_attr}" {mes_attrs} {lvl_attrs}
    onclick="toggleChart({idx})">
  <td class="left">{idx}</td>
  <td class="tags-cell">{tags_cell}</td>
  <td class="left type-cell">{level_type}</td>
  <td class="left time-stacked retest-cell">{retest_str_display}</td>
  <td class="left merged-h1-levels">{members_str}</td>
  <td>{own_cell}</td>
  <td class="entry-cell">{res['alt_price']:.2f}<span class="entry-info"><span class="gap-slot">{act['gap']}</span><span class="{src_cls}">{res['alt_source']}{improved_flag}</span>{chase_flag}</span></td>
  <td class="left time-stacked entry-touch-cell">{entry_touch_str_display}</td>
  <td class="stop-cell" title="{stop_title}">{res['stop_price']:.2f}<span class="src-tag m5">{stop_source}</span></td>
  <td class="tgt-cell" title="{act['tgtTitle']}">{act['tgt']}</td>
  <td class="rr-cell">{act['rr']}</td>
  <td class="outcome-cell {act['outcomeCls']}"><span class="outcome-label">{act['outcomeLabel']}</span><span class="mgmt-badge">{act['mgmtBadge']}</span></td>
  <td class="left time-stacked exit-cell">{act['exit']}</td><td class="exitpx-cell">{act['exitPx']}</td>
  <td class="pnl-cell {act['pnlCls']}">{act['pnl']}</td>
  <td class="contracts-cell" title="{mes_title}">{mes_contracts_html}</td><td class="comm-cell" title="{mes_title}">{mes_comm_text}</td>
  <td class="mae-cell bad">{act['mae']}</td><td class="mfe-cell good">{act['mfe']}</td><td class="gb-cell">{act['gb']}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="done-cell" onclick="event.stopPropagation();"><input type="checkbox" class="done-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{idx}"
      onclick="event.stopPropagation();toggleChart({idx})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{idx}" id="chart-row-{idx}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-main-{idx}"></div><div class="chart-ph" id="c-main-{idx}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-ctx-{idx}"></div><div class="chart-ph" id="c-ctx-{idx}"></div></div>
    <div class="chart-half-row">
      <div class="chart-cell chart-half"><div class="chart-title" id="t-d1-{idx}"></div><div class="chart-ph" id="c-d1-{idx}"></div></div>
      <div class="chart-cell chart-half"><div class="chart-title" id="t-h1-{idx}"></div><div class="chart-ph" id="c-h1-{idx}"></div></div>
    </div>
  </div></td>
</tr>"""
        return chart_stack, row_html


def _finish_report(args, results, filled, stats, improved_n, max_win_mae, max_loss_mfe, gapped_entries,
                   pctile_html, rows_html, charts, output_path, title_suffix="", storage_suffix=""):
    fade_only = True        # the M1 report is the fade playbook only; go-with is not part of it
    total_pnl_pts = sum(r["resolved"]["r"] * r["stop_pts"] for r in filled
                       if r["resolved"].get("r") is not None)
    total_comm = sum(_mes_sizing(r["stop_pts"])[1] for r in filled
                     if r["resolved"].get("r") is not None)
    summary_html = f"""
<div class="summary">
  <div class="box"><strong>{len(filled)}</strong>fade trades (engine)</div>
  <div class="box"><strong>{args.box_merge_overlap:.0%}</strong>range merge (0 = off)</div>
  <div class="box"><strong>{'on' if args.fade2 else 'off'}</strong>fade 2</div>
  <div class="box"><strong>{args.min_r:.2f}</strong>min R (below: tagged, not skipped)</div>
  <div class="box"><strong id="sum-avg-r">{stats['avg_r']:.2f}</strong>avg R</div>
  <div class="box"><strong>{improved_n}</strong>/{len(filled)} entry improved over own level</div>
  <div class="box"><strong>{gapped_entries}</strong>gapped entry</div>
  <div class="box"><strong id="sum-shown">{len(filled)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>
  <div class="toolbar">
    <button class="btn" onclick="exportReviewCsv()">\u2b07 Export notes CSV</button>
    <label class="btn" for="import-review-file">\u2b06 Import notes CSV</label>
    <input type="file" id="import-review-file" accept=".csv" class="hidden" onchange="importReviewCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved Reviewed/Valid/Notes in this browser for this report?')) clearAllReview();">\U0001f5d1 Clear all</button>
  </div>
</div>
"""
    # The strategy write-up and the excursion percentiles are reference material, not something to scroll past on
    # the way to the trades, so they live in their own tab (see SR.tab_bar_html / SR.TABS_JS).
    pctile_tab_html = f"""
<p class="lead">M{BAR_MIN} FADE PLAYBOOK, decided by the live engine (live_range/) and nothing else. The ticks of
[{args.start}, {args.end}] (UTC) are replayed through the engine as a production feed would deliver them: the algo decides once
every {args.decision_s:g} second(s) from a one-second snapshot (arm, pull and re-quote orders, gates, RR-floor exit), while fills of
resting orders and stop / target hits happen on the real tick. RANGE FIRST: an M5 range box (find_range_box_zz) with a strong
breakout waiting for its retest; the M{BAR_MIN} LXPB ledger is only a cache lookup of the levels that box's fade seat can trade.
FADE: an LLPB above the top edge of a box that broke UP / an LHPB below the bottom edge of a box that broke DOWN, when the breakout
is the run-in leg's own. Only FADE 1 (the box's first strong breakout) is traded{'' if not args.fade2 else ' and fade 2 is switched on here'}.
RANGE MERGE: M5 boxes overlapping by at least {args.box_merge_overlap:.0%} of the smaller box that sit inside one known, unbroken H1
range box (find_range_box_zz on closed H1 bars) are one range and share their seats: one trade per merged range, direction and
playbook. ENTRY: every live level is judged on its own (its own price beyond the range edge, its own run-in); its cluster is only
searched for a better (more extreme) price that also qualifies on its own (src tag own / cluster); a level with a confirmed swing on
it is skipped. STOP: one tick beyond the entry level's own spike
candle (src p0_spike) or beyond the live thrust candle (thrust), widened to the last older P1 candle when closer than the minimum
(wide_p1). TARGET (the engine's own; on load the page shows opposite-zz, and the Target rules checkboxes switch between all three): {ENGINE_TARGET_TEXT[args.fade_target]}. A bracket below R
{args.min_r:g} is still taken and tagged r_below_min. The stop trails when a P0 group breaks out past it and an RR-floor exit closes a
trade whose remaining reward / risk falls to 0.2 (both shown as MGMT); no position is carried past 12:44 PT (EOD FLAT); the 05:30 PT
news-pull window and the 15:00-15:05 PT reopen are blackouts. There is no news-spike check. Win = a trade that made money.
Charts/markers/price-lines/tooltips, MAE/MFE/Max DD definitions and the Reviewed/Valid/Notes columns follow
render_stop_target_report.py's conventions. See the Dynamic filters row of the Trades tab for live exclusion toggles.</p>
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
Defaults to any (every row shown); pick &ge; 0.5, say, to hide thin-R rows.">R</span>
    <select class="f-num-op" data-target="rr">
      <option value="any" selected>any</option>
      <option value="gte">&ge;</option>
      <option value="gt">&gt;</option>
      <option value="eq">=</option>
      <option value="lte">&le;</option>
      <option value="lt">&lt;</option>
    </select>
    <input type="number" class="f-num-val" data-target="rr" value="0.5" min="0" max="5" step="0.1">
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
radios are independent, so several can be on at once and show rows carrying ANY of them -- click
a radio again to turn it back off). To add another dynamic filter: tag qualifying results with an entry in
res['dyn_tags'] (Python side) and add one more chip-stack (Exclude checkbox + Only radio) here
with class f-dyn-exclude/f-dyn-isolate and data-tag matching that tag -- see
_apply_globex_open_filter's docstring in render_m5_confl2_report.py for the full
convention.">Dynamic filters</span>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="globex_eth_open">
        Exclude Globex/ETH open fills (15:00-15:05 PT)</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged globex_eth_open, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="globex_eth_open"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="eod_flat">
        Exclude end-of-day flats</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged eod_flat, hiding every other
row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="eod_flat"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip" title="Exclude every trade that fades a strong breakout whose range has not been retested yet: an LLPB short with such an upward breakout range below its level, or an LHPB long with such a downward breakout range above it (find_range_box_zz on M5, 1-minute lower timeframe, as of the touch).">
        <input type="checkbox" class="f-dyn-exclude" data-tag="breakout-from-range">
        Exclude breakout-from-range trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged breakout-from-range, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="breakout-from-range"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip" title="Exclude every fade of a range box's FIRST strong breakout (no earlier strong breakout of either edge).">
        <input type="checkbox" class="f-dyn-exclude" data-tag="fade1">
        Exclude fade1 trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged fade1, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="fade1"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip" title="Exclude every fade of a range box's SECOND-side breakout: price had already broken out one edge, reversed, and broke out of the other.">
        <input type="checkbox" class="f-dyn-exclude" data-tag="fade2">
        Exclude fade2 trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged fade2, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="fade2"> only</label>
    </div>
    <div class="chip-stack">
      <label class="chip" title="Exclude every trade that fades several overlapping range boxes at different stages: some at their first strong breakout, some at their second-side breakout.">
        <input type="checkbox" class="f-dyn-exclude" data-tag="fade-mixed">
        Exclude fade1+2 (mixed) trades</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged fade-mixed, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="fade-mixed"> only</label>
    </div>
__P0_KIND_CHIPS__
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live filters on the ENTRY level (the level the order sat on). Spike: its P0 kind is
spike-P0 or spike+swing-P0. Swing: swing-P0 or spike+swing-P0 (a swing AND consolidating at the breakout). Large wick: the
P0 candle's rejection wick (upper for an LHPB, lower for an LLPB) is at least the points given (1 pt = the engine's
plain-P0 entry rule). Each one: any / yes / no. Combine ALL = every set condition must hold (AND); ANY = at least one
(OR). Stats recompute from the rows left. Badges SPK / SWG / W(wick pts) in the Tags column.">Entry level</span>
    <label class="chip">Spike <select class="f-lvl" data-attr="spike"><option value="any">any</option><option value="1">yes</option><option value="0">no</option></select></label>
    <label class="chip">Swing <select class="f-lvl" data-attr="swing"><option value="any">any</option><option value="1">yes</option><option value="0">no</option></select></label>
    <label class="chip">Large wick &ge; <input type="number" class="f-lvl-wick-pts" value="1" step="0.25" min="0" style="width:4em">
      pt <select class="f-lvl" data-attr="wick"><option value="any">any</option><option value="1">yes</option><option value="0">no</option></select></label>
    <label class="chip">Combine <select id="f-lvl-combine"><option value="and">ALL (and)</option><option value="or">ANY (or)</option></select></label>
  </div>
  <div class="filter-row">
__TARGET_CHIPS__
  </div>
  <div class="filter-row">
    <span class="filter-label" title="Live, in-browser toggle for the plug-n-play trade-management
rules in trade_management.py (symmetric across direction): rule 1 trails the stop to one tick
beyond a qualifying M1 breakout candle's own extreme (below the low for a long, above
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
        f"the one offering the FARTHER target. opposite_zz: the farthest standing opposite M{BAR_MIN} P0 (highest for a "
        "long, lowest for a short) formed after the most recent confirmed zigzag crest (long) / trough (short) since P1. "
        "swing_extreme: the most recent confirmed zigzag trough (long) / crest (short) since P1, at its own price. "
        "range_edge: the faded range's LOW for an LHPB long / HIGH for an LLPB short (H1 box edges for a merged range; "
        "the nearest one when the trade fades several). Every rule 0.25-50 pts from the fill.")
    head = (f"<th class=\"left\">Trade Id</th>"
            f"<th class=\"left\" title=\"Every dynamic-filter tag this row carries, in one "
            f"place: globex_eth_open, swerved/swerve_blocked, "
            f"breakout-from-range (fade1 / fade2 / fade-mixed), {'' if fade_only else 'retest-into-range, '}range-slot-lost "
            f"(row-level, constant across target rules) plus r_below_min/eod_flat (the "
            f"ACTIVE target rule's own -- these swap along with Target/R/Outcome when you "
            f"toggle a Target rules checkbox above). Hover a badge for its own detail; the "
            f"matching Dynamic filters checkbox/radio above still drives Exclude/Only on "
            f"it.\">Tags</th>"
            f"<th class=\"left\">Type</th>"
            f"<th class=\"left\">M{BAR_MIN} retest</th>"
            f"<th class=\"left\" title=\"Distinct M{BAR_MIN} prices merged into this trade, "
            f"extreme-first: highest for LLPB, lowest for LHPB. "
            f"Single-level trades show their own M{BAR_MIN} price.\">Merged levels</th>"
            f"<th title=\"The level's original M{BAR_MIN} entry price, before fine-tuning to the "
            f"confluence group's extreme price\">Own</th>"
            f"<th>Entry</th>"
            f"<th class=\"left\">Entry (touch) time</th>"
            f"<th title=\"If the entry level's own P0 was a spike candle (hammer for LHPB / "
            f"shooting star for LLPB), one tick beyond THAT candle's own low/high "
            f"(p0_spike). Otherwise, one tick beyond the live same-side M{BAR_MIN} breakout-candle "
            f"extreme (thrust); +/-{DYNAMIC_STOP_RADIUS_PTS:g}pt level search. "
            f"No trade if neither qualifies\">Stop</th>"
            f"<th title=\"{target_col_title}; "
            f"no trade if none qualifies\">Target</th>"
            f"<th title=\"Reward:risk on offer for THIS trade's own bracket at entry "
            f"(target pts / stop pts) -- fixed once entry/stop/target are picked, independent "
            f"of whether the trade goes on to win or lose. Below {args.min_r:g} the trade is "
            f"still taken and shown, tagged r_below_min and excluded from the headline stats "
            f"by default\">R</th>"
            f"<th>Outcome</th>"
            f"<th class=\"left\">Exit time</th><th>Exit px</th>"
            f"<th title=\"Realized profit/loss in points (signed): +target pts on a win, "
            f"-stop pts on a loss\">PnL</th>"
            f"<th title=\"MES contracts for 1R = ${R_DOLLARS:,.0f}: floor(${R_DOLLARS:,.0f} / "
            f"(stop pts x ${MES_POINT_VALUE:g})). Commission is NOT in the sizing. HIGH SIZE = "
            f"&ge;{MES_HIGH_CONTRACTS} contracts; STOP &gt;200 = 0 contracts fit. Flagged, not "
            f"capped or skipped\">MES</th>"
            f"<th title=\"Round-trip commission = contracts x ${MES_COMMISSION_RT} per MES. "
            f"Reported only; R and PnL are gross of it\">Comm.</th>"
            f"<th>MAE (win)</th><th>MFE (loss)</th><th>Max DD</th>"
            f"<th>Reviewed</th><th>Valid</th>"
            f"<th title=\"Done with this trade's feedback -- purely a review aid, "
            f"dims the row, no effect on stats or filters\">Done</th>"
            f"<th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")

    # The report is the fade playbook only; a labelled run keeps its own review notes.
    variant = ["fade playbook only, engine"]
    storage_suffix = "_fade" + storage_suffix
    label = (getattr(args, "label", "") or "").strip()
    if label:
        variant.append(label)
        storage_suffix = "_" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") + storage_suffix
    if variant:
        title_suffix = f" ({', '.join(variant)})" + title_suffix
    p0_kind_chips = ""
    for tag, label in (("plain_p0", "Exclude plain-P0 trades"), ("no_confl", "Exclude no-confluence trades (cluster of 1)")):
        p0_kind_chips += f"""    <div class="chip-stack">
      <label class="chip"><input type="checkbox" class="f-dyn-exclude" data-tag="{tag}">
        {label}</label>
      <label class="chip chip-iso" title="Only: show ONLY rows tagged {tag}, hiding every
other row -- overrides every Exclude box. Click again to turn off.">
        <input type="radio" class="f-dyn-isolate" data-tag="{tag}"> only</label>
    </div>
"""
    storage_key = (f"lxpb_m1_confl0_review_v1{storage_suffix}" if BAR_MIN == 1
                   else f"lxpb_m{BAR_MIN}_confl0_review_v1{storage_suffix}")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>M{BAR_MIN} range-box report{title_suffix}</title>
{CSS}
</head><body>
<h1>M{BAR_MIN} range-box trades{title_suffix}</h1>
<p class="lead">M{BAR_MIN} range-box FADE trades, taken by the live engine (live_range/) from Sierra Chart ticks: range first, then
the LXPB level its fade seat trades. The M5 chart above the M{BAR_MIN} chart is real M5 data; it, the D1 / H1 panes and the range
boxes are the only higher timeframes in this report. Only the fade playbook is shown; go-with is not part of it.</p>
{SR.tab_bar_html([("trades", "Trades"), ("pctile", "Excursion percentiles")])}
<div class="tab-panel" id="tab-trades">
{summary_html}
{filter_panel.replace("__P0_KIND_CHIPS__", p0_kind_chips).replace("__TARGET_CHIPS__", _target_chips(args.page_target))}
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
    print(f"Skipped: 0 (the engine's trades are the report)")


CSS = SR.CSS + """
<style>
.src-tag { font-size:0.75em; color:var(--text-dim); margin-left:4px; }
.src-tag.m5, .src-tag.cluster { color:#38bdf8; }
.src-tag.own { color:var(--text-dim); }
.src-tag.chase { color:#f59e0b; margin-left:6px; cursor:help; }
.unfilled-row td { color:var(--text-faint); font-style:italic; }
tr.lvl-row.type-lhpb td.type-cell, tr.lvl-row.type-llpb td.type-cell {
  color:var(--text); font-weight:normal;
}
.cluster-tag { border-bottom:1px dotted var(--text-dim); cursor:help; }
td.merged-h1-levels { max-width:220px; white-space:normal; overflow-wrap:break-word; }
td.tags-cell { max-width:200px; white-space:normal; overflow-wrap:break-word; }
/* Outcome can carry a full sentence (e.g. "NO TRADE (no target under any
   rule)") instead of just WIN/LOSS, so let it wrap rather than force the
   column to that sentence's own width. */
td.outcome-cell { max-width:130px; white-space:normal; overflow-wrap:break-word; }
/* Columns that only ever hold a short number/count but carry a much longer,
   multi-word header label (the H1 confluence
   count, excursion columns): wrap the HEADER instead, so the column stays
   pinned to the data's own width rather than the label's. Data cells are
   untouched -- still single-line, sized to their own (short) content. */
#lvl-table thead th:nth-child(1),   /* Trade Id */
#lvl-table thead th:nth-child(18),  /* MAE (win) */
#lvl-table thead th:nth-child(19),  /* MFE (loss) */
#lvl-table thead th:nth-child(20) { /* Max DD */
  white-space:normal; overflow-wrap:break-word; max-width:60px;
}
/* Safari (iPad) clamps a cell to its max-width even when nowrap content is wider and
   lets the content spill over the next column, which reads as merged columns; Chrome
   never narrows a column below its content. So nowrap cells carry no max-width, and
   every wrapping cell may break a long word (overflow-wrap:break-word) so nothing is wider
   than its clamp. */
/* M5 retest / Entry (touch) time / Exit time: date and HH:MM:SS PT stack on
   two lines (see _pt_str_stacked) instead of running the whole
   "YYYY-MM-DD HH:MM:SS PT" string onto one, so the column is only as wide
   as the longer of the two (~11 chars) rather than all ~22. */
td.time-stacked { white-space:nowrap; line-height:1.3; }
td.time-stacked .time-part { display:block; }
/* Stop / Target: the src-tag (p0_spike, opposite_zz, swing_extreme,
   ...) drops to its own line under the price instead of running inline,
   so the column is only as wide as the longer of price vs. tag rather
   than both combined. */
td.stop-cell, td.tgt-cell {
  max-width:95px; white-space:normal; overflow-wrap:break-word; line-height:1.3;
}
td.stop-cell .src-tag, td.tgt-cell .src-tag {
  display:block; margin-left:0; margin-top:1px;
}
/* Entry: same idea -- the price stays on its own line, everything else
   (own/chase source tag, the improved-fill arrow, the gap-never-traded
   flag, the chased/repriced note) drops to a line below it instead of
   running the whole row inline after the price. */
td.entry-cell {
  max-width:150px; white-space:normal; overflow-wrap:break-word; line-height:1.3;
}
td.entry-cell .entry-info { display:block; margin-top:1px; }
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
.swerve-tag-badge { background:#1e3a2f; color:#86efac; }
.range-box-tag-badge { background:#1f2a44; color:#93c5fd; }
/* Notes box: this report is reviewed with long, written-out notes per trade,
   so it ships far larger than the shared 160x34 default in
   render_stop_target_report.CSS (left alone, for every other report) and
   resizes in both directions rather than only vertically. */
textarea.trade-note { width:360px; height:150px; resize:both; }
th.sortable-th { cursor:pointer; user-select:none; }
th.sortable-th:hover { text-decoration:underline; }
/* Done checkbox: purely "I'm finished with this trade's feedback" -- dims
   the whole row so reviewed trades visually recede, independent of the
   Reviewed/Valid workflow (own doneStore, see JS below). */
tr.lvl-row.is-done { opacity:0.35; }
tr.lvl-row.is-done:hover { opacity:0.6; }
/* CHART LAYOUT, for any screen (an 11-inch iPad Air is 1180x820 CSS px landscape, 820x1180 portrait).
   The chart stack is exactly one screen wide (--stack-w, set from the visible width by the script
   below; the viewport meta tag makes that the device width) and stays in view while the table is
   panned sideways. Each pane is as wide as the stack and about 60% of the screen's height, the
   two stacked one above the other. In-chart text is ~13px on screen (chartFont() in the script). The M5 and M1
   candles keep their own default grey: ranges are drawn with rays and markers only. */
.chart-row > td { padding:0; }
.chart-stack { width:var(--stack-w, calc(100vw - 16px)); position:sticky; left:8px;
               margin-left:-16px; padding:8px 0; }
.chart-pane { height:clamp(340px, max(60vh, calc(var(--stack-w, 1000px) * 0.3)), 760px); }
/* D1 and H1 below the two full-width panes, side by side at half the width each (one column when the
   screen is too narrow for two panes of at least 360px). */
.chart-half-row { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap:8px; }
.chart-half { min-width:0; height:clamp(300px, max(42vh, calc(var(--stack-w, 1000px) * 0.24)), 520px); }
/* Full screen (the button on every pane's title bar; Esc or the same button leaves): the pane fills the
   screen. The browser's own fullscreen is asked for too; this fixed layer is what works where it is refused. */
.fs-btn { grid-column:3; justify-self:end; background:#1b2330; color:#e5e7eb; border:1px solid #334155;
          border-radius:4px; padding:0 9px; font-size:var(--chart-font, 14px); line-height:1.5; cursor:pointer; }
.fs-btn:hover { background:#273449; }
.chart-cell.fs { position:fixed; inset:0; z-index:100000; width:100vw !important; height:100vh !important;
                 height:100dvh !important; border-radius:0; margin:0; overscroll-behavior:contain; touch-action:none; }
.chart-cell:fullscreen { width:100vw; height:100vh; border-radius:0; }
body.fs-open { overflow:hidden; }
.chart-title { font-size:var(--chart-font, 13px); }
.chart-title-split .ct-ohlc { font-size:var(--chart-font, 13px); }
.chart-title-split + .chart-ph { height:calc(100% - 31px); }
.vline-layer { position:absolute; inset:0; pointer-events:none; overflow:hidden; z-index:3; }
.vline { position:absolute; top:0; width:0; border-left:1px dotted; opacity:0.75; }
.vline-tag { position:absolute; white-space:nowrap; font-family:'Courier New', monospace; padding:0 3px;
             background:rgba(0,0,0,0.55); border-radius:2px; }
.pane-tip { font-size:var(--chart-font, 14px); line-height:1.4; padding:6px 10px; }
</style>
"""
# Two edits to the shared review JS, for this report only (each asserted, so a change
# upstream fails loudly instead of silently skipping):
#   * applyReviewFilters ANDs a "Replayed" chip test into every row. This report has no
#     Replayed column or filter, so with no .f-review-replay chip on the page it must pass.
#   * Valid is ON by default (a row with no saved state is valid; untick the trades that
#     are not), where the shared code starts every row not-valid.
_SHARED_JS = SR.JS
for _old, _new, _n in (
        ("const replayOk = replayOn.includes(isReplayed ? 'replayed' : 'not_replayed');",
         "const replayOk = !document.querySelector('.f-review-replay') "
         "|| replayOn.includes(isReplayed ? 'replayed' : 'not_replayed');", 1),
        ("if (vcb) vcb.checked = !!state.valid;",
         "if (vcb) vcb.checked = state.valid !== false;", 1),
        ("tr.classList.toggle('is-valid', !!state.valid);",
         "tr.classList.toggle('is-valid', state.valid !== false);", 2)):
    assert _SHARED_JS.count(_old) == _n, f"shared review JS changed: re-check {_old!r}"
    _SHARED_JS = _SHARED_JS.replace(_old, _new)
JS = _SHARED_JS + """
<script>
// ---------------------------------------------------------------------
// Charts. Two panes per trade, each as wide as the screen: the M5 context pane (c-ctx-N) above the
// trading-bar pane (c-main-N). This report draws them itself (the shared _renderPane is replaced
// here) for three reasons:
//   * the candles always fill the pane's width: the bar spacing follows the pane's width and
//     the number of bars (capped so a short window does not turn into fat blocks), instead of
//     the shared fixed 6px that left a wide pane mostly empty;
//   * the in-chart text is 13px, enough to read on a tablet and small enough that the axis
//     tags of one trade do not pile on each other;
//   * axis tags (stop, target, entry, level, range high/low) and candle-marker texts that
//     would land on top of each other are thinned out: the most important one stays, and the
//     full text of a ray shows on hover.
// ---------------------------------------------------------------------
// In-chart text size in CSS px. A browser that shrinks a wide page to fit the screen (iPad Safari)
// shows every CSS px smaller, so the size grows by the same factor: it reads as ~13px on screen
// at any zoom, up to a 22px cap.
function chartFont() { return Math.max(13, Math.min(22, Math.round(13 / (_baseScale || 1)))); }
const MAX_BAR_SPACING = 20, MIN_BAR_SPACING = 1;
function tagGapPx() { return chartFont() + 7; }   // least vertical distance between two axis tags
// Keep the most informative tag when two collide: entry, stop, target, level, then range edges.
function _tagPriority(title) {
  const t = (title || '').toLowerCase();
  if (t.startsWith('entry')) return 0;
  if (t.startsWith('stop')) return 1;
  if (t.startsWith('target')) return 2;
  if (t.startsWith('range')) return 4;
  return 3;
}
// The axis tag prints the price itself, so the title is only the word(s) naming the line.
function _tagText(title) {
  return (title || '').replace(/\\s*\\(\\+?-?[\\d.]+pt\\)/g, '').replace(/\\s*\\d{3,5}\\.\\d+/g, '').trim();
}
// Candle markers (entry, exit, result, P0/P1/P2, range start/breakout...): keep the text of the
// most informative marker when two would overlap sideways on the same side of the bar; the
// shape of the dropped one stays. Markers at the very same bar stack vertically, so they are fine.
function _markerPriority(text) {
  const t = text || '';
  if (/^([A-Z]:)?(Cr|Tr)\\d/.test(t)) return 0.5;   // range zigzag pivots: what the user checks the box by
  if (/^(WIN|LOSS|CANDLE|exit)/.test(t)) return 0;
  if (/^(P2 |entry)/i.test(t)) return 1;
  if (/^(RR FLOOR|Stop)/.test(t)) return 2;
  if (/^(range|start|broke|[A-Z][ \u2191\u2193])/.test(t)) return 3;
  return 4;
}
function _thinMarkers(chart, series, markers) {
  if (!markers || !markers.length) return;
  const perChar = chartFont() * 0.62;       // width of one monospace character
  const used = {};
  const order = markers.map((m, i) => ({ m, i, x: chart.timeScale().timeToCoordinate(m.time) }));
  order.sort((a, b) => _markerPriority(a.m.text) - _markerPriority(b.m.text) || a.i - b.i);
  const out = new Array(markers.length);
  order.forEach(o => {
    let text = o.m.text || '';
    if (text && o.x != null) {
      const half = text.length * perChar / 2 + 4;
      const list = used[o.m.position] = used[o.m.position] || [];
      const clash = list.some(u => Math.abs(u.x - o.x) >= 1 && o.x + half > u.l && o.x - half < u.r);
      if (clash) text = '';
      else list.push({ x: o.x, l: o.x - half, r: o.x + half });
    }
    out[o.i] = Object.assign({}, o.m, { text });
  });
  series.setMarkers(out);
}
function _fitBars(chart, nBars) {
  if (!nBars) return;
  const ts = chart.timeScale();
  const w = Math.max(60, ts.width());
  const spacing = Math.min(MAX_BAR_SPACING, Math.max(MIN_BAR_SPACING, w / (nBars + 3)));
  const pad = Math.max(0, (w / spacing - nBars) / 2);
  ts.setVisibleLogicalRange({ from: -pad - 1, to: nBars - 1 + pad + 1 });
}
// Price-axis drag with a finger. The chart library only reacts to a mouse on the right-hand price axis; a
// finger there just scrolls the page. So a touch that starts on the axis strip stretches / squeezes the
// price range instead (drag down = candles taller, drag up = shorter, around the centre of the view) and a
// double tap on the strip returns to the auto-fit. Every series in the pane is given the same fixed range
// while a stretch is active, so the level rays cannot widen it again.
function _touchPriceAxis(chart, el, series, otherSeries, onChange) {
  const all = [series].concat(otherSeries);
  let manual = null;                       // {mid, half} while stretched, else auto-fit
  const provider = (orig) => {
    if (!manual) return orig();
    return { priceRange: { minValue: manual.mid - manual.half, maxValue: manual.mid + manual.half } };
  };
  const refresh = () => { all.forEach(s => s.applyOptions({ autoscaleInfoProvider: provider })); onChange(); };
  const inAxis = (t) => {
    const r = el.getBoundingClientRect(), ps = chart.priceScale('right');
    const x = t.clientX - r.left, y = t.clientY - r.top;
    return x >= r.width - ps.width() - 4 && x <= r.width && y >= 0 && y <= r.height - chart.timeScale().height();
  };
  let drag = null, lastTap = 0;
  el.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1 || !inAxis(e.touches[0])) return;
    e.preventDefault(); e.stopPropagation();
    const now = Date.now();
    if (now - lastTap < 350) { manual = null; drag = null; lastTap = 0; refresh(); return; }
    lastTap = now;
    if (!manual) {
      const plotH = el.clientHeight - chart.timeScale().height();
      const top = series.coordinateToPrice(0), bot = series.coordinateToPrice(plotH);
      if (top == null || bot == null) return;
      // the visible range holds the data range plus the scale margins (8 % each side)
      manual = { mid: (top + bot) / 2, half: Math.abs(top - bot) * 0.84 / 2 };
      refresh();
    }
    drag = { y: e.touches[0].clientY, half: manual.half };
  }, { capture: true, passive: false });
  el.addEventListener('touchmove', (e) => {
    if (!drag || !manual) return;
    e.preventDefault(); e.stopPropagation();
    const plotH = Math.max(1, el.clientHeight - chart.timeScale().height());
    const dy = e.touches[0].clientY - drag.y;
    manual.half = Math.max(1e-6, drag.half * Math.exp(-dy / (plotH * 0.5)));
    refresh();
  }, { capture: true, passive: false });
  const end = (e) => { if (drag) { e.stopPropagation(); drag = null; } };
  el.addEventListener('touchend', end, true);
  el.addEventListener('touchcancel', end, true);
}
function _renderPane(elId, titleId, cd) {
  const el = document.getElementById(elId);
  const titleEl = document.getElementById(titleId);
  if (!el || !titleEl) return;
  titleEl.classList.add('chart-title-split');
  titleEl.textContent = '';
  const baseEl = document.createElement('span');
  baseEl.className = 'ct-base';
  baseEl.textContent = _shortLabel(cd.title);
  baseEl.title = cd.title;
  const ohlcEl = document.createElement('span');
  ohlcEl.className = 'ct-ohlc';
  titleEl.appendChild(baseEl);
  titleEl.appendChild(ohlcEl);
  const fsBtn = document.createElement('button');
  fsBtn.type = 'button';
  fsBtn.className = 'fs-btn';
  fsBtn.textContent = '\u26f6';
  fsBtn.title = 'Full screen';
  fsBtn.addEventListener('click', (e) => { e.stopPropagation(); _toggleFs(el.closest('.chart-cell'), fsBtn); });
  fsBtn.addEventListener('dblclick', (e) => e.stopPropagation());
  titleEl.appendChild(fsBtn);
  const opts = _baseOpts(cd.dateOnly ? timeFmtD : timeFmtH1);
  opts.layout.fontSize = chartFont();
  const chart = LightweightCharts.createChart(el, Object.assign(opts, {
    autoSize: false, width: el.clientWidth || 800, height: el.clientHeight || 320,
  }));
  const series = _addCandles(chart, cd.precision);
  if (cd.minimal) series.applyOptions({ lastValueVisible: false, priceLineVisible: false });
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  const tags = [];   // every axis tag that may be thinned: {price, prio, show(bool)}
  (cd.priceLines || []).forEach(pl => {
    const line = series.createPriceLine(Object.assign({}, pl, { title: _tagText(pl.title) }));
    tags.push({ price: pl.price, prio: _tagPriority(pl.title),
                show: v => line.applyOptions({ axisLabelVisible: v }) });
  });
  // Level rays: flat line segments from the candle a level is built on to the right edge. The
  // hover tooltip (below) carries each ray's full text.
  const rayInfo = [];
  (cd.rays || []).forEach(r => {
    const rs = chart.addLineSeries({
      color: r.color, lineWidth: r.lineWidth || 1,
      lineStyle: (r.lineStyle == null ? 0 : r.lineStyle),
      priceLineVisible: false, lastValueVisible: !!r.priceLabel,
      title: _tagText(r.title),
      crosshairMarkerVisible: false, pointMarkersVisible: false,
    });
    rs.setData(r.points);
    rayInfo.push({ series: rs, label: r.label });
    if (r.priceLabel && r.points.length) {
      tags.push({ price: r.points[0].value, prio: _tagPriority(r.title),
                  show: v => rs.applyOptions({ lastValueVisible: v }) });
    }
  });
  function thinTags() {
    const pos = [];
    tags.forEach(t => {
      const y = series.priceToCoordinate(t.price);
      if (y != null) pos.push({ t, y });
      else t.show(false);
    });
    pos.sort((a, b) => a.t.prio - b.t.prio || a.y - b.y);
    const kept = [];
    pos.forEach(o => {
      const clash = kept.some(y => Math.abs(y - o.y) < tagGapPx());
      o.t.show(!clash);
      if (!clash) kept.push(o.y);
    });
  }
  // Range start / breakout: dotted vertical lines with a label on a lane along the top of the
  // plot, over a layer that ignores the pointer, so candles and wicks stay visible and clickable.
  const vlines = (cd.vlines || []).map(v => {
    const line = document.createElement('div');
    line.className = 'vline';
    line.style.borderLeftColor = v.color;
    const tag = document.createElement('div');
    tag.className = 'vline-tag';
    tag.style.color = v.color;
    tag.textContent = v.label;
    return { v, line, tag };
  });
  let vlayer = null;
  if (vlines.length) {
    vlayer = document.createElement('div');
    vlayer.className = 'vline-layer';
    vlines.forEach(o => { vlayer.appendChild(o.line); vlayer.appendChild(o.tag); });
    el.appendChild(vlayer);
  }
  function placeVlines() {
    if (!vlayer) return;
    const ts = chart.timeScale();
    const plotW = ts.width(), plotH = Math.max(0, el.clientHeight - ts.height());
    const perChar = chartFont() * 0.6, rowH = chartFont() + 6;
    const lanes = [];     // right edge of the last label placed on each lane
    vlines.map(o => ({ o, x: ts.timeToCoordinate(o.v.time) }))
      .sort((a, b) => (a.x == null) - (b.x == null) || a.x - b.x)
      .forEach(({ o, x }) => {
        const visible = x != null && x >= 0 && x <= plotW;
        o.line.style.display = o.tag.style.display = visible ? 'block' : 'none';
        if (!visible) return;
        const w = o.v.label.length * perChar + 10;
        const flip = x + 3 + w > plotW;                 // not enough room to the right: label on the left
        const left = flip ? x - 3 - w : x + 3;
        let lane = lanes.findIndex(right => left > right + 4);   // first lane this label clears
        if (lane === -1) lane = lanes.length;
        lanes[lane] = left + w;
        o.line.style.left = Math.round(x) + 'px';
        o.line.style.height = plotH + 'px';
        o.tag.style.left = Math.round(left) + 'px';
        o.tag.style.top = (2 + lane * rowH) + 'px';
        o.tag.style.fontSize = (chartFont() - 1) + 'px';
      });
  }
  let thinQueued = false;
  function queueThin() {
    if (thinQueued) return;
    thinQueued = true;
    requestAnimationFrame(() => { thinQueued = false; thinTags(); _thinMarkers(chart, series, cd.markers); placeVlines(); });
  }
  let tip = null;
  if (rayInfo.length) {
    tip = document.createElement('div');
    tip.className = 'pane-tip';
    el.appendChild(tip);
  }
  const prec = cd.precision || 2;
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      ohlcEl.textContent = 'O ' + d.open.toFixed(prec)
        + '  H ' + d.high.toFixed(prec) + '  L ' + d.low.toFixed(prec)
        + '  C ' + d.close.toFixed(prec);
    } else { ohlcEl.textContent = ''; }
    if (!tip) return;
    if (!param.point || !param.time) { tip.style.display = 'none'; return; }
    const cursorPrice = series.coordinateToPrice(param.point.y);
    const hits = [];
    rayInfo.forEach(ri => {
      const v = param.seriesData.get(ri.series);
      if (v && v.value != null && cursorPrice != null
          && Math.abs(v.value - cursorPrice) <= RAY_HOVER_PTS) hits.push(ri.label);
    });
    if (!hits.length) { tip.style.display = 'none'; return; }
    tip.innerHTML = hits.map(h => '<div>' + h + '</div>').join('');
    tip.style.display = 'block';
    let x = param.point.x + 14, y = param.point.y + 14;
    if (x + tip.offsetWidth > el.clientWidth) x = param.point.x - tip.offsetWidth - 14;
    if (y + tip.offsetHeight > el.clientHeight) y = param.point.y - tip.offsetHeight - 14;
    tip.style.left = Math.max(0, x) + 'px';
    tip.style.top = Math.max(0, y) + 'px';
  });
  _fitBars(chart, cd.candles.length);
  queueThin();
  _touchPriceAxis(chart, el, series, rayInfo.map(r => r.series), queueThin);
  // The visible price range moves when the chart is panned or its axis dragged: re-thin then.
  chart.timeScale().subscribeVisibleLogicalRangeChange(queueThin);
  el.addEventListener('pointerup', queueThin);
  el.addEventListener('touchend', queueThin);
  new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) {
      chart.resize(r.width, r.height);
      _fitBars(chart, cd.candles.length);
      queueThin();
    }
  }).observe(el);
}
const _PANE_NAME = { main: 'M1', ctx: 'M5', d1: 'D1', h1: 'H1' };
function _renderChartPane(i, key, cd) {
  const title = document.getElementById('t-' + key + '-' + i);
  if (cd) _renderPane('c-' + key + '-' + i, 't-' + key + '-' + i, cd);
  else if (title) title.textContent = _PANE_NAME[key] + '  |  (no bars covering this trade)';
}
// Full screen for one pane: the browser's fullscreen where it allows it, plus a fixed full-window layer
// (.fs) that does the same where it does not. Esc, the same button, or leaving browser fullscreen ends it;
// the pane's ResizeObserver redraws the chart at each size.
function _exitFs() {
  document.querySelectorAll('.chart-cell.fs').forEach(c => {
    c.classList.remove('fs');
    const b = c.querySelector('.fs-btn');
    if (b) { b.textContent = '\u26f6'; b.title = 'Full screen'; }
  });
  document.body.classList.remove('fs-open');
  const ex = document.exitFullscreen || document.webkitExitFullscreen;
  if (ex && (document.fullscreenElement || document.webkitFullscreenElement)) {
    try { const p = ex.call(document); if (p && p.catch) p.catch(() => {}); } catch (e) { /* ignore */ }
  }
}
function _toggleFs(cell, btn) {
  if (!cell) return;
  if (cell.classList.contains('fs')) { _exitFs(); return; }
  _exitFs();
  cell.classList.add('fs');
  document.body.classList.add('fs-open');
  btn.textContent = '\u2715';
  btn.title = 'Exit full screen';
  // On a touch device (an iPad with a trackpad included) only the fixed full-window layer is used: Safari's
  // own fullscreen ends on a scroll / swipe, and then the pane would drop back out while it is being used.
  const touchy = (navigator.maxTouchPoints || 0) > 0;
  const rq = touchy ? null : (cell.requestFullscreen || cell.webkitRequestFullscreen);
  if (rq) { try { const p = rq.call(cell); if (p && p.catch) p.catch(() => {}); } catch (e) { /* ignore */ } }
}
document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement) _exitFs(); });
document.addEventListener('webkitfullscreenchange', () => { if (!document.webkitFullscreenElement) _exitFs(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') _exitFs(); });
_renderStack = function(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  _renderChartPane(i, 'main', cd.main);
  _renderChartPane(i, 'ctx', cd.ctx);
  _renderChartPane(i, 'd1', cd.d1);
  _renderChartPane(i, 'h1', cd.h1);
};
// The chart stack is exactly as wide as what is on screen, whatever the browser did with the page:
// at 1:1 that is the device width; when the browser shrinks the wide table to fit (iPad Safari), it
// is the whole fitted page. The width in CSS px that the screen shows at the page's own starting
// zoom (_baseScale, measured after load and after a rotation) is the visual viewport times its
// current scale over that base, so pinch-zooming does not change it. Minus a small gutter.
let _baseScale = null;
function _layoutStackWidth() {
  const vv = window.visualViewport;
  const w = (vv && _baseScale) ? vv.width * vv.scale / _baseScale : document.documentElement.clientWidth;
  const root = document.documentElement.style;
  root.setProperty('--stack-w', Math.max(300, Math.floor(w) - 16) + 'px');
  root.setProperty('--chart-font', chartFont() + 'px');
}
function _measureBaseScale() {
  _baseScale = window.visualViewport ? window.visualViewport.scale : 1;
  _layoutStackWidth();
}
_layoutStackWidth();
window.addEventListener('DOMContentLoaded', _measureBaseScale);
window.addEventListener('load', () => { _measureBaseScale(); setTimeout(_measureBaseScale, 600); });
window.addEventListener('orientationchange', () => setTimeout(_measureBaseScale, 600));
window.addEventListener('resize', _layoutStackWidth);
// ---------------------------------------------------------------------
// Dynamic filters -- see _apply_globex_open_filter's own docstring in
// render_m5_confl2_report.py for the full authoring convention (this is
// the intentionally-generic, tag-agnostic half of it). Each
// f-dyn-exclude checkbox's data-tag names a tag a Python-side filter may
// have added to a row's data-dyn-tags (space-separated -- a row can carry
// more than one). Checking an Exclude box hides every row carrying that
// tag; picking the f-dyn-isolate RADIO stacked under it instead hides
// every row NOT carrying that tag (any Isolate radio picked takes
// priority over every Exclude box). The f-dyn-isolate radios have no
// shared name, so several tags can be isolated at once (a row shows if it
// carries ANY of them) -- see the click handler below, which unchecks a
// radio again when it's clicked while already selected (native radios
// can't self-clear). Both act via the SAME .dyn-hidden
// class, kept deliberately separate from the review-workflow filters'
// .hidden class above (applyReviewFilters, in the shared JS) so the two
// systems never fight over one class; a row is invisible if EITHER is
// set -- and recomputes the win rate / avg R / total R / total PnL
// summary boxes from only the remaining (not hidden) trades. To add
// another dynamic filter: tag qualifying results with one more entry in
// res['dyn_tags'] (Python side) and add one more <div class="chip-stack">
// holding an <input class="f-dyn-exclude" data-tag="..."> checkbox and an
// <input type="radio" class="f-dyn-isolate"
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
  const m5 = cd && cd.main;
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
    const el = document.getElementById('c-main-' + idx);
    if (el) { el.innerHTML = ''; _renderChartPane(idx, 'main', m5); }
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
      setCell(tr, '.exit-cell', '-', 'left time-stacked exit-cell');
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
    setCell(tr, '.exit-cell', p.exit, 'left time-stacked exit-cell');
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
// The review-workflow filters (Status / Valid / Notes) are only
// ever applied by the shared applyReviewFilters, which hides rows but knows
// nothing of the stats -- so a row hidden by them used to stay in every
// total. reviewFilterOk mirrors those three tests for the stats loop below,
// and the wrapper re-runs the stats whenever the shared code re-applies them
// (a filter chip, a row's own Reviewed/Valid box, a note, an import).
// The ticked chips are read ONCE per pass (reviewFilterState) and handed to
// every row: reading them per row meant several whole-page scans per row, which
// is seconds per click on a big report.
function reviewFilterState() {
  const on = sel => Array.from(document.querySelectorAll(sel + ':checked')).map(c => c.value);
  return { status: on('.f-review-status'), valid: on('.f-review-valid'),
           notes: on('.f-review-notes') };
}
function reviewFilterOk(tr, st) {
  const note = tr.querySelector('.trade-note');
  const hasNotes = note ? note.value.trim().length > 0 : false;
  return st.status.includes(tr.classList.contains('is-reviewed') ? 'reviewed' : 'unreviewed')
    && st.valid.includes(tr.classList.contains('is-valid') ? 'valid' : 'not_valid')
    && st.notes.includes(hasNotes ? 'has_notes' : 'no_notes');
}
const _applyReviewFiltersBase = applyReviewFilters;
let _inDynStats = false;
applyReviewFilters = function() {
  _applyReviewFiltersBase();
  if (!_inDynStats) recomputeDynStats();
};
// Entry-level filters: spike / swing / large wick, each any / yes / no,
// combined with ALL (and) or ANY (or). Reads data-lvl-* on the row.
function entryLevelOk(tr) {
  const wickPts = parseFloat((document.querySelector('.f-lvl-wick-pts') || {}).value);
  const conds = [];
  document.querySelectorAll('.f-lvl').forEach(sel => {
    if (sel.value === 'any') return;
    const a = sel.dataset.attr;
    let has;
    if (a === 'wick') {
      const w = parseFloat(tr.dataset.lvlWick);
      has = !isNaN(w) && w >= (isNaN(wickPts) ? 0 : wickPts);
    } else {
      has = tr.dataset['lvl' + a[0].toUpperCase() + a.slice(1)] === '1';
    }
    conds.push(sel.value === '1' ? has : !has);
  });
  if (!conds.length) return true;
  const comb = (document.getElementById('f-lvl-combine') || {}).value;
  return comb === 'or' ? conds.some(Boolean) : conds.every(Boolean);
}
function recomputeDynStats() {
  applyTargetModes();
  const excludeTags = activeDynExcludeTags();
  const isolateTags = activeDynIsolateTags();
  const outcomeOn = activeOutcomeBuckets();
  const reviewState = reviewFilterState();
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
    const reviewHidden = !reviewFilterOk(tr, reviewState);
    const hidden = reviewHidden || rrHidden || !entryLevelOk(tr) || (isolateTags.length > 0
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
      : (isNaN(rVal) ? 'no_trade' : (rVal > 0 ? 'win' : 'loss'));
    const outcomeHidden = !outcomeOn.includes(bucket);
    tr.classList.toggle('outcome-hidden', outcomeHidden);
    if (chartRow) chartRow.classList.toggle('outcome-hidden', outcomeHidden);
    if (hidden || outcomeHidden || isNoTradeRow) return;
    if (!isNaN(rVal)) {
      n += 1;
      sumR += rVal;
      const comm = parseFloat(tr.dataset.comm);   // fixed by the stop, same under every rule
      if (!isNaN(comm)) sumComm += comm;
      if (rVal > 0) {          // a win is a trade that made money (target, trailed stop, RR-floor or EOD exit alike)
        wins += 1;
        const mae = cellNum(tr, '.mae-cell');
        if (!isNaN(mae) && mae > maxWinMae) maxWinMae = mae;
      } else {
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
  // notes filters plus its own independent .hidden pass over the
  // SAME R >= filter (numFilterOk, data-target "rr") against tr.dataset.rr,
  // which applyTargetModes() just rewrote for whichever target rule is now
  // ticked -- re-run it so that pass doesn't go stale either.
  _inDynStats = true;
  try { applyReviewFilters(); } finally { _inDynStats = false; }
}
document.querySelectorAll('.f-dyn-exclude, .f-target-mode, .f-outcome').forEach(cb => cb.addEventListener('change', recomputeDynStats));
document.querySelectorAll('.f-lvl, #f-lvl-combine').forEach(el => el.addEventListener('change', recomputeDynStats));
document.querySelectorAll('.f-lvl-wick-pts').forEach(el => el.addEventListener('input', recomputeDynStats));
// The shared code bound its own review-chip listeners to the ORIGINAL
// applyReviewFilters before the wrapper above existed, so those chips need
// their own stats trigger.
document.querySelectorAll('.f-review-status, .f-review-valid, .f-review-notes')
  .forEach(cb => cb.addEventListener('change', recomputeDynStats));
// The f-dyn-isolate radios have NO shared name, so each is its own group and
// several Only tags can be on at once (rows carrying ANY picked tag show).
// A native radio can't uncheck itself by being clicked again, so remember
// per radio whether it was on before this click and, if so, clear it. This
// runs on 'click' rather than 'change' because unchecking programmatically
// (radio.checked = false) does not fire 'change'.
document.querySelectorAll('.f-dyn-isolate').forEach(rb => {
  rb.addEventListener('click', () => {
    if (rb.dataset.wasOn === '1') {
      rb.checked = false;
      rb.dataset.wasOn = '';
    } else {
      rb.dataset.wasOn = '1';
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
const mgmtToggleCb = document.getElementById('mgmt-thrust-trail');
if (mgmtToggleCb) mgmtToggleCb.addEventListener('change', recomputeDynStats);
recomputeDynStats();

// Multi-select column sort. Click a sortable header to sort by it alone
// (descending first, click again to flip); Shift+click adds it as a further
// key, or flips its direction if already a key. Reads each cell's CURRENT
// text, so R / PnL / MAE / MFE / Max DD sort by whatever the ticked target
// rules and management toggle currently show. Empty / '-' cells always sink
// to the bottom. A trade's chart row travels with its trade row.
const SORT_COLS = [0, 10, 14, 17, 18, 19];  // Trade Id, R, PnL, MAE, MFE, Max DD
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

// ---------------------------------------------------------------------
// Done checkbox -- separate from the Reviewed/Valid review
// workflow above: it only means "I'm finished looking at this trade's
// feedback" and dims the row, so it gets its own store (same report key,
// suffixed) rather than folding into reviewRowState, which would force
// every OTHER report sharing that shared widget to carry this field too.
// No filter, no summary count -- just persistence and the grey-out.
// ---------------------------------------------------------------------
const doneStore = new RowStore(REVIEW_STORAGE_KEY + '_done');
async function initDone() {
  await doneStore.init();
  document.querySelectorAll('#lvl-table tbody tr.lvl-row').forEach(tr => {
    const cb = tr.querySelector('.done-cb');
    if (!cb) return;
    const state = doneStore.get(tr.dataset.key);
    cb.checked = !!state.done;
    tr.classList.toggle('is-done', !!state.done);
    cb.addEventListener('change', () => {
      doneStore.set(tr.dataset.key, { done: cb.checked });
      tr.classList.toggle('is-done', cb.checked);
    });
  });
}
initDone();
</script>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="M1 variant of the M5-native ss_m5_confl2 strategy (same "
                     "selection/entry/stop/target/management logic, unmodified -- see the "
                     "module docstring): trade M1 LXPB retests with SS M1 confluence >= N, "
                     "entry fine-tuned to the confluence group's extreme price at the "
                     "breakout bar, stop above/below the thrust candle, target the nearest "
                     "qualifying opposite M1 level, no trade if either is missing or R < 1. "
                     "The M1 series is built straight from Sierra Chart ticks for --start/--end "
                     "(no TradingView M1 export exists), so keep that window short and recent.")
    parser.add_argument("--min-r", type=float, default=MIN_R_DEFAULT,
                        help=f"Reward:risk (target pts / stop pts) below which a trade is "
                             f"TAGGED 'r_below_min' (default {MIN_R_DEFAULT}). The trade is "
                             f"still taken, charted and resolved; the report's dynamic filter "
                             f"excludes those rows from the headline stats by default.")
    parser.add_argument("--start", default=None,
                        help="(M1 variant) overridden below from --trading-days -- kept only so "
                             "the many downstream reads of args.start still have a value.")
    parser.add_argument("--end", default=None, help="(M1 variant) see --start.")
    parser.add_argument("--trading-days", type=int, default=3,
                        help="How many of the most recent trading days (Globex/ETH reopen to "
                             "reopen -- trade_management.trading_day_label) the M1 series and "
                             "the retest selection both cover, ending now. There is no "
                             "TradingView M1 export, so this window is built straight from "
                             "Sierra Chart ticks (render_labels_report._tick_bars_spanning) -- "
                             "keep it short and inside the current front month (default 3).")
    parser.add_argument("--since-day", default=None,
                        help="YYYY-MM-DD trading-day label (Globex/ETH reopen to reopen) the "
                             "window starts at, instead of the last --trading-days. The "
                             "window runs from that day's 15:00 PT reopen the evening before "
                             "up to now.")
    parser.add_argument("--until-day", default=None,
                        help="YYYY-MM-DD trading-day label the window ENDS with (inclusive: it runs to "
                             "that day's 15:00 PT reopen), instead of up to now. With --since-day this "
                             "is how a fixed month is rebuilt.")
    parser.add_argument("--warmup-days", type=int, default=0,
                        help="trading days of M1 bars/levels BEFORE the window start, so levels "
                             "formed just before it are known. Only trades in the window are "
                             "listed. 0 = none (default %(default)s).")
    parser.add_argument("--decision-s", type=float, default=1.0,
                        help="seconds between the engine's decisions (production gets data once per second); "
                             "0 = every tape record")
    parser.add_argument("--box-merge-overlap", type=float, default=0.5,
                        help="engine range merge: M5 boxes overlapping by >= this share of the smaller box inside one "
                             "known, unbroken H1 range box share their seats; 0 = off (default %(default)g)")
    parser.add_argument("--box-merge-tol", type=float, default=2.0,
                        help="points an M5 box edge may poke out of the H1 box (default %(default)g)")
    parser.add_argument("--h1-days", type=float, default=25.0,
                        help="days of H1 / M5 history before the window seeded for the H1 range boxes (default %(default)g)")
    parser.add_argument("--page-target", default="opposite_zz", choices=["range_edge", "zigzag", "opposite_zz"],
                        help="target rule ticked when the page loads (user, 2026-10-28: opposite-zz). Only what the page shows on load; the engine trades with --fade-target")
    parser.add_argument("--fade-target", default="range_edge", choices=["range_edge", "zigzag", "opposite_zz"],
                        help="fade target: the faded range's edge (default; range low for a long, range high for a short) "
                             "or the old most recent zigzag pivot since the level's P1")
    parser.add_argument("--fade2", action="store_true",
                        help="also keep fade 2 (a box's second-side breakout: the other edge had already broken out "
                             "strongly). OFF by default: only fade 1 is traded, tagged and drawn.")
    parser.add_argument("--box-detector", default="zz", choices=["zz", "trading_range"],
                        help="M5 range boxes from find_range_box_zz (default) or find_trading_range "
                             "(live_range/trbook.py; chart squares are its confirming touches, not zigzag pivots)")
    parser.add_argument("--label", default="",
                        help="short text appended to the page title (e.g. 'Sep 2026, whole month'); it "
                             "also gives the report its own saved review notes.")
    parser.add_argument("--bar-minutes", type=int, default=1,
                        help="bar size in minutes for the main series (1 = the M1 report, 2 = the M2 variant); "
                             "everything else is unchanged (default %(default)s)")
    parser.add_argument("--output", default=None,
                        help="output path (default public/reports/ss_m1_confl/sep_range_box.html)")
    args, unknown = parser.parse_known_args()
    # Flags of the old forked report that no longer exist. Accepted and ignored so an older
    # regen command keeps working; any other unknown flag is still an error.
    for tok in unknown:
        if tok.startswith("--") and not tok.startswith(REMOVED_FLAGS):
            parser.error(f"unrecognized arguments: {tok}")
    if unknown:
        print(f"ignoring options that no longer exist: {' '.join(unknown)}", flush=True)
    BAR_MIN = args.bar_minutes
    BAR_FREQ = f"{BAR_MIN}min"
    P1_BAR_WIDTH = pd.Timedelta(minutes=BAR_MIN)
    args.output = args.output or os.path.join(
        _REPO_ROOT, "public", "reports", ("ss_m1_confl" if args.bar_minutes == 1 else f"ss_m{args.bar_minutes}_confl"),
        "sep_range_box.html"
    )

    # -------------------------------------------------------------------
    # M1 window + bars, then swap the LXPB state machine's data source.
    #
    # trading_day_label/_trading_day_calendar are read here FIRST, while
    # LC.m5_bars_continuous still means the real M5 series -- they warm
    # trade_management's own module-level trading-day-calendar cache
    # against the real history, which matters because that cache, once
    # warm, is never rebuilt: anything that calls TM.trading_day_label
    # later in this same process (the EOD-flat rule, in particular) must
    # keep seeing the real calendar, not one derived from 3 days of M1
    # bars. Only AFTER that is LC.m5_bars_continuous/m5_levels rebound --
    # every one of this (unmodified, copied) script's own call sites below
    # this point, plus trade_management.py's, reads M1 through them from
    # here on, which is the whole swap; see m1_levels in
    # lxpb_levels_cache.py and the module docstring above for what does
    # and doesn't change in the data pipeline underneath it.
    cal = TM._trading_day_calendar()
    now_utc = pd.Timestamp.now(tz="UTC")
    today_label = TM.trading_day_label(now_utc)
    labels = cal[cal <= today_label]
    if args.since_day:
        first_label = pd.Timestamp(args.since_day)
        if first_label not in labels:
            raise RuntimeError(f"--since-day {args.since_day} is not a trading-day label on record")
        args.trading_days = int((labels >= first_label).sum())
    else:
        if len(labels) < args.trading_days:
            raise RuntimeError(
                f"only {len(labels)} trading days on record, need --trading-days={args.trading_days}")
        first_label = labels[-args.trading_days]
    lo_utc = (pd.Timestamp(first_label) - pd.Timedelta(days=1) + TM.SESSION_REOPEN_PT
              ).tz_localize("America/Los_Angeles").tz_convert("UTC")
    hi_utc = now_utc
    if args.until_day:
        last_label = pd.Timestamp(args.until_day)
        if last_label not in labels:
            raise RuntimeError(f"--until-day {args.until_day} is not a trading-day label on record")
        if last_label < first_label:
            raise RuntimeError("--until-day is before the first day of the window")
        hi_utc = min(now_utc, (last_label + TM.SESSION_REOPEN_PT
                               ).tz_localize("America/Los_Angeles").tz_convert("UTC"))
        args.trading_days = int(((labels >= first_label) & (labels <= last_label)).sum())
    args.start = lo_utc.tz_localize(None).isoformat()
    args.end = hi_utc.tz_localize(None).isoformat()
    print(f"[m1] window: {args.trading_days} trading days = "
          f"{lo_utc.tz_convert('America/Los_Angeles'):%a %Y-%m-%d %H:%M} PT -> "
          f"{hi_utc.tz_convert('America/Los_Angeles'):%a %Y-%m-%d %H:%M} PT", flush=True)

    # Warm-up: M1 bars (hence levels) from a few trading days before the window, so a level formed
    # just before it can still be retested inside it. Candidates start at args.start, not here.
    bars_lo_utc = lo_utc
    if args.warmup_days > 0:
        warm_label = labels[max(0, labels.get_loc(first_label) - args.warmup_days)]
        bars_lo_utc = (pd.Timestamp(warm_label) - pd.Timedelta(days=1) + TM.SESSION_REOPEN_PT
                       ).tz_localize("America/Los_Angeles").tz_convert("UTC")
        print(f"[m1] warm-up from {bars_lo_utc.tz_convert('America/Los_Angeles'):%a %Y-%m-%d %H:%M} PT "
              f"({args.warmup_days} trading days before the window)", flush=True)
    m1_bars = R._tick_bars_spanning(bars_lo_utc, hi_utc, BAR_FREQ)
    if m1_bars.empty:
        raise RuntimeError(f"no M1 bars built from ticks over [{lo_utc}, {hi_utc})")
    R._report_series_gaps(m1_bars, "M1 (tick-only, last-N-trading-days)")
    print(f"[m1] {len(m1_bars):,} M1 bars, {m1_bars.index[0]} -> {m1_bars.index[-1]}", flush=True)

    def _m1_bars_continuous():
        return m1_bars

    def _m1_levels(rebuild=False, verbose=True, *, plain_p0):
        # The ledger is only a lookup for the chart code (the engine builds its own levels from the same ticks).
        return LC.m1_levels(m1_bars, rebuild=rebuild, verbose=verbose, plain_p0=plain_p0,
                            cache_name=("m1_levels_recent" if BAR_MIN == 1 else f"m{BAR_MIN}_levels_recent"))

    args.bars_lo_utc = bars_lo_utc

    LC.m5_bars_continuous = _m1_bars_continuous
    LC.m5_levels = _m1_levels
    # render_stop_target_report.build_m5_chart (the main chart pane) reads
    # its candles from SR._m5_bars, a module-level alias BOUND to the
    # original LC.m5_bars_continuous function object at SR's own import
    # time (render_stop_target_report.py:1021) -- reassigning the LC
    # attribute above does not change what that alias already points to,
    # so without this line the main pane's own candles stay the real M5
    # series even though every level/ledger lookup inside the same chart
    # (LC.m5_levels, called fresh) correctly sees M1. SR._m5_bars is the
    # ONLY such pre-bound alias in the codebase (checked every other
    # LC.m5_bars_continuous/m5_levels call site -- all call through the
    # module attribute fresh, so they already pick up the swap above).
    SR._m5_bars = _m1_bars_continuous

    render(args)
