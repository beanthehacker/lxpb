"""
charts.py -- per-row M5 and H1 chart specs for the upcoming-levels dashboard.

The specs are the SAME JSON shape the ss_m5_confl2 report ships to its browser
(`{title, candles, markers, priceLines, rays, precision}`) and are drawn by the
report's own renderer (`_renderPane`, lifted out of render_stop_target_report.JS
by build.py), so panes, rays, markers and hover tooltips behave and look the
same. Colours and window constants come from the report modules, not from here.

What differs from a report row is only that there is no P2 yet: the pane's
right edge is the latest completed bar ("NOW"), every ray runs from its source
candle to that edge, and there is no target, fill or exit.

  original entry  gold ray   the level's own price, from its P0 candle
                             (the report's "Own" column / gold LEVEL ray)
  refined entry   blue ray   the fine-tuned entry, from the P0 candle of the
                             level that supplied it (report's "entry" ray);
                             drawn only when it differs from the original
  stop            red dashed from the candle it is built on (spike P0 or P1 thrust)
  other live M5 same-side levels within SR.M5_NEAR_PTS of the entry: light-blue
                             rays, solid = broken out, dashed = unbroken
"""
import bisect

import numpy as np
import pandas as pd

import lxpb_levels_cache as LC
import render_labels_report as R
import render_stop_target_report as SR

NOW_COLOR = "#9ca3af"
SWERVE_COLOR = "#86efac"                      # same green the report uses for the swerve
M5_BARS_BEFORE_NOW = 2 * SR.M5_BARS_BEFORE_RETEST   # report's own M5 pane depth, before P2
H1_BARS_MIN, H1_BARS_MAX = 120, 400


def _epoch(ts):
    return R._to_epoch_utc(ts)


def _snapper(times):
    """`snap(ts)` -> chart bar time at or before `ts`, else None (report convention)."""
    def snap(ts):
        i = bisect.bisect_right(times, _epoch(ts)) - 1
        return times[i] if i >= 0 else None
    return snap


def _rays_and_lines(res, snap, times, last_t, near_price):
    """Rays for original entry / refined entry / stop (+ the swerve's planned
    entry and the latest close as plain price lines). Points sit only on
    bars present in the window, so a ray follows a compressed axis."""
    is_long = res["is_long"]
    lvl_type = res["level_type"]

    def pts(price, start_ts):
        start = snap(start_ts) if start_ts is not None else None
        start = times[0] if start is None else start
        return [{"time": t, "value": price} for t in times if t >= start]

    def fmt(ts):
        return R._to_pt_str(ts)

    own, entry, stop = res["own_price"], res["alt_price"], res["stop_price"]
    rays = []
    same = abs(own - entry) < 1e-9
    rays.append({
        "points": pts(own, res["own_formed"]), "color": R.LEVEL_COLOR, "lineWidth": 2,
        "lineStyle": 0, "priceLabel": True,
        "title": f"M5 {lvl_type} {own:.2f}" + (" (entry)" if same else " (original entry)"),
        "label": f"M5 {lvl_type} {own:.2f} &middot; original entry"
                 + (" = refined entry" if same else "")
                 + f" &middot; formed {fmt(res['own_formed'])}"})
    if not same:
        rays.append({
            "points": pts(entry, res["alt_formation_time"]), "color": R.ENTRY_COLOR,
            "lineWidth": 2, "lineStyle": 0, "priceLabel": True,
            "title": f"entry {entry:.2f}",
            "label": f"refined entry {entry:.2f} &middot; via {res['alt_source']} level formed "
                     f"{fmt(res['alt_formation_time'])} &middot; {abs(entry - own):.2f}pt "
                     f"{'below' if is_long else 'above'} the original"})
    rays.append({
        "points": pts(stop, res["stop_from_ts"]), "color": SR.EXIT_LOSS_COLOR, "lineWidth": 1,
        "lineStyle": 2, "priceLabel": True,
        "title": f"stop {stop:.2f} (-{SR._fmt_pts(abs(entry - stop))}pt)",
        "label": f"stop {stop:.2f} &middot; {res['stop_title']}"})

    lines = [{"price": res["last_price"], "color": NOW_COLOR, "lineWidth": 1, "lineStyle": 3,
              "title": f"last close {res['last_price']:.2f}"}]
    sw = res.get("swerve")
    if sw:
        lines.append({"price": sw["planned_price"], "color": SWERVE_COLOR, "lineWidth": 1,
                      "lineStyle": 2,
                      "title": ("planned entry -- swerve blocked, not taken" if sw["blocked"]
                                else f"planned entry (swerved to {sw['price']:.2f})")})
    return rays, lines


def _markers(res, snap, last_t):
    is_long = res["is_long"]
    marks = [{"time": last_t, "position": "belowBar" if is_long else "aboveBar",
              "color": R.P2_COLOR, "shape": "circle",
              "text": f"NOW {res['last_price']:.2f}"}]
    p0, p1 = snap(res["own_formed"]), snap(res["own_p1"])
    if p0 is not None:
        marks.append({"time": p0, "position": "aboveBar" if is_long else "belowBar",
                      "color": R.P0_COLOR, "shape": "circle", "text": "P0"})
    if p1 is not None:
        marks.append({"time": p1, "position": "belowBar" if is_long else "aboveBar",
                      "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
                      "shape": "arrowUp" if is_long else "arrowDown", "text": "P1"})
    if abs(res["alt_price"] - res["own_price"]) > 1e-9:
        t = snap(res["alt_formation_time"])
        if t is not None:
            marks.append({"time": t, "position": "aboveBar" if is_long else "belowBar",
                          "color": R.LEVEL_COLOR, "shape": "circle",
                          "text": f"M5 entry level {res['alt_price']:.2f}"})
    for ts, px in (res.get("swerve") or {}).get("swings", []):
        t = snap(ts)
        if t is not None:
            marks.append({"time": t, "position": "belowBar" if is_long else "aboveBar",
                          "color": SWERVE_COLOR, "shape": "circle", "text": f"swing {px:.2f}"})
    return marks


def m5_spec(res, m5, ledger, last_price):
    """The M5 pane: the report's own compressed-window treatment (context
    around every ray's start, the rest skipped) up to the latest bar."""
    n = len(m5)
    idx = m5.index
    last_pos = n - 1
    level_type, is_long = res["level_type"], res["is_long"]

    live = LC.levels_live_as_of(ledger, idx[-1], level_type=level_type,
                                near_price=res["alt_price"], near_pts=SR.M5_NEAR_PTS)
    own_keys = {(round(res["own_price"], 2), pd.Timestamp(res["own_formed"])),
                (round(res["alt_price"], 2), pd.Timestamp(res["alt_formation_time"]))}
    near, seen = [], set()
    for _, lv in live.iterrows():
        key = (round(float(lv["price"]), 2), pd.Timestamp(lv["formation_time"]))
        if key in seen or key in own_keys:
            continue
        seen.add(key)
        near.append(lv)

    def pos(ts):
        return min(max(int(idx.searchsorted(pd.Timestamp(ts), side="right")) - 1, 0), n - 1)

    segments = [(last_pos - M5_BARS_BEFORE_NOW, last_pos)]
    sources = [res["own_formed"], res["own_p1"], res["alt_formation_time"], res["stop_from_ts"]]
    sources += [t for t, _ in (res.get("swerve") or {}).get("swings", [])]
    for ts in sources:
        p = pos(ts)
        segments.append((p - SR.M5_CTX_EXTRA_TIME, p + SR.M5_CTX_EXTRA_TIME))
    for lv in near:
        p = pos(lv["formation_time"])
        segments.append((p - SR.M5_CTX_BEFORE_FORMATION, p + SR.M5_CTX_AFTER_FORMATION))
    merged = R._merge_segments(segments, n, SR.M5_MAX_MERGE_GAP)

    parts, skip_marks = [], []
    for s, e, gap in merged:
        if gap:
            skip_marks.append({"time": _epoch(idx[s]), "position": "aboveBar",
                               "color": "#9ca3af", "shape": "square",
                               "text": f"[{gap} bars skipped]"})
        parts.append(m5.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    candles = [{"time": _epoch(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in window.iterrows()]
    times = [c["time"] for c in candles]
    snap = _snapper(times)

    rays, lines = _rays_and_lines(res, snap, times, times[-1], res["alt_price"])
    wt = window.index
    blue = []
    for lv in near:
        mask = wt >= pd.Timestamp(lv["formation_time"])
        p = [{"time": _epoch(t), "value": float(lv["price"])} for t in wt[mask]]
        if not p:
            continue
        blue.append({
            "points": p, "color": SR.M5_COLOR, "lineWidth": 1,
            "lineStyle": 0 if lv["stage"] == "broken" else 2, "priceLabel": False, "title": "",
            "label": (f"M5 {lv['type']} {float(lv['price']):.2f} &middot; formed "
                      f"{R._to_pt_str(lv['formation_time'])} &middot; {lv['stage']} &middot; "
                      f"{float(lv['dist']):.2f}pt from entry")})
    markers = _markers(res, snap, times[-1]) + skip_marks
    markers.sort(key=lambda m: m["time"])
    title = (f"M5  |  {len(blue)} other live M5 {level_type} ray(s) within "
             f"{SR.M5_NEAR_PTS:.0f}pt of entry (solid = broken, dashed = unbroken; hover for "
             f"details)  |  {R._to_pt_str(window.index[0])} → {R._to_pt_str(window.index[-1])}"
             f"  |  original {res['own_price']:.2f}"
             + ("" if abs(res["own_price"] - res["alt_price"]) < 1e-9
                else f" → refined entry {res['alt_price']:.2f}")
             + f"  |  entry via {res['alt_source']} ({res['group_n']} in group)")
    if sum(g for _, _, g in merged):
        title += f"  [{sum(g for _, _, g in merged)} bars compressed out of view]"
    return {"title": title, "candles": candles, "markers": markers, "priceLines": lines,
            "rays": blue + rays, "precision": 2}


def h1_spec(res, h1, last_price):
    """The H1 pane: contiguous recent hourly bars reaching back to the earliest
    source candle (bounded), with the same three rays."""
    n = len(h1)
    idx = h1.index
    earliest = min(pd.Timestamp(res["own_formed"]), pd.Timestamp(res["alt_formation_time"]),
                   pd.Timestamp(res["stop_from_ts"]))
    start = int(idx.searchsorted(earliest, side="right")) - 1 - 10
    start = max(0, min(start, n - H1_BARS_MIN), n - H1_BARS_MAX)
    window = h1.iloc[start:]
    candles = [{"time": _epoch(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in window.iterrows()]
    times = [c["time"] for c in candles]
    snap = _snapper(times)
    rays, lines = _rays_and_lines(res, snap, times, times[-1], res["alt_price"])
    marks = [m for m in _markers(res, snap, times[-1]) if not m["text"].startswith("swing")]
    marks.sort(key=lambda m: m["time"])
    title = (f"H1  |  rays start at the hourly bar holding their source M5 candle  |  "
             f"{R._to_pt_str(window.index[0])} → {R._to_pt_str(window.index[-1])}")
    return {"title": title, "candles": candles, "markers": marks, "priceLines": lines,
            "rays": rays, "precision": 2}
