"""
render_cross_bar_report.py
==========================
CROSS BAR REPORT: every cross bar (patterns_pure `find_cross_bar`: an H1 bar that causes an SFP and is also a spike or an
engulfing bar) on continuous ES, one row per cross bar, four panes per row in the M1 fade report's page layout, plus Reviewed /
Valid / Notes per row (the M1 report's review store). Detection reads the whole H1 history (a swing from before --since can be
the one a bar sweeps); only cross bars from --since on are listed.

1. H1: 100 candles before the cross bar, ending AT it (no later bars). The swept swing is a dashed amber ray; when it lies
   further back, 50 candles round it are shown with the bars between skipped ("[... skipped]").
2. M5: the same view, 100 candles before the bar's hour, ending with the hour's last candle.
   Both draw the OLDER swings next to the swept one (dotted amber): same-side confirmed pivots within 7 pt of the swept price
   (either side) with no bar between them and the cross bar reaching past the swept price.
3. M5-LXPB / 4. M2-LXPB: the nearest LXPB level of the bar's direction (LHPB for a bullish one, LLPB for a bearish one) beyond the
   swept price whose P0 starts INSIDE the cross bar's H1 hour; its P0, P1 and first retest are marked, the pane runs from 100
   candles before P0 to 5 candles past the retest (to the last bar if not retested), skipping only stretches over 120 candles.
   The other levels of that type with a P0 in the same hour are dotted rays behind the candles. Levels come from lxpb.py's state
   machine (every P0 kind) over M5 / M2 bars rolled up from the 1-minute ticks.
Every pane lists its annotations in a strip above the plot (marker text can hide behind candles). Candles stay grey.

Data: E:\\data_source (es_data.load_es): H1 bars for the detector, 1-minute ticks rolled up to M5 for the lower pane. Times
are Pacific (PT) everywhere. The chart code, CSS, page shell and review store are the M1 fade report's
(ss_m1_confl/render_m1_confl_report.py CSS and JS), reused as is.

Usage:
    python cross_bar/render_cross_bar_report.py --last 1 --output <file>.html     # newest bullish + newest bearish (sample)
    python cross_bar/render_cross_bar_report.py --at "2026-10-26 16:00,2026-09-18 09:00" --output <file>.html   # chosen ones (PT)
    python cross_bar/render_cross_bar_report.py --all --since 2026-01-01 --output <file>.html
"""
import os
import sys
import json
import argparse
import bisect
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
for _p in (os.path.dirname(_REPO_ROOT), _REPO_ROOT, os.path.join(_REPO_ROOT, "ss_m1_confl"), os.path.join(_REPO_ROOT, "patterns_pure"), r"E:\data_source"):
    sys.path.insert(0, _p)
from es_data import load_es                       # noqa: E402
from find_cross_bar import find_cross_bar         # noqa: E402
from find_sfp_fractal import _pivots, SFP_SWING_K as SWING_K   # noqa: E402
import lxpb                                       # noqa: E402  (repo root: the LXPB state machine)
import render_m1_confl_report as M                # noqa: E402

ROW_STORE_JS = os.path.join(_REPO_ROOT, "public", "js", "row-store.js")
PT = "America/Los_Angeles"
NEAR = 100                          # candles shown either side of the cross bar (H1 and M5)
SPLIT = 50                          # candles either side of the cross bar AND of its retest when the bars between are skipped
MERGE_GAP = 10                      # ... unless fewer than this many candles would be skipped: then one unbroken window
BULL, BEAR = "#4ade80", "#f87171"
SWING_COLOR = "#fbbf24"
RETEST_COLOR = "#60a5fa"
LXPB_COLOR = "#c084fc"
LEVEL_GAP = 120                         # LXPB panes: skip only stretches longer than this many candles
LEVEL_TFS = (("l5", "M5", 5), ("l2", "M2", 2))   # pane key, label, bar minutes


def _pt(ts, with_date=True):
    return pd.Timestamp(ts).tz_convert(PT).strftime("%a %Y-%m-%d %H:%M" if with_date else "%H:%M") + " PT"


def _candles(bars):
    return [{"time": int(t.timestamp()), "open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close)} for t, r in bars.iterrows()]


def _snap(times, ts):
    """The candle time at or before `ts` (first candle when ts is before the window)."""
    pos = bisect.bisect_right(times, int(pd.Timestamp(ts).timestamp())) - 1
    return times[max(pos, 0)]


def _confirmation(row):
    kind = "hammer" if row.bullish else "shooting star"
    eng = f"{'bullish' if row.bullish else 'bearish'} engulfing"
    return f"{kind} + {eng}" if row.spike and row.engulfing else (kind if row.spike else eng)


def _elapsed(a, b):
    mins = int((b - a).total_seconds() // 60)
    d, rem = divmod(mins, 1440)
    return (f"{d}d " if d else "") + f"{rem // 60}h"


OLDER_PTS = 7.0       # an older swing this close to the swept price (either side of it) is drawn too


def older_swings(h1, pos, swing_t, swing_price, bullish, k=SWING_K):
    """Older same-side swings next to the swept one: confirmed before the cross bar at H1 position `pos`, within OLDER_PTS of
    the swept price (above or below it), and with NO bar between that older pivot and the cross bar reaching past the swept
    price (above it for a swept high, below it for a swept low). Returns [(pivot time, price, cross bar went past it)],
    oldest first."""
    side = "low" if bullish else "high"
    ext = h1[side].to_numpy(float)
    sp = h1.index.get_loc(swing_t)
    out = []
    for p in _pivots(ext, k, bullish):
        p = int(p)
        price = float(ext[p])
        if p >= sp or p + k >= pos:
            continue
        if not 0 < abs(price - swing_price) <= OLDER_PTS:
            continue
        between = ext[p + 1: pos]
        if (between.min() >= swing_price) if bullish else (between.max() <= swing_price):
            hit = ext[pos] < price if bullish else ext[pos] > price
            out.append((h1.index[p], price, bool(hit)))
    return out


def _pane(segments, cross_t, swing_t, swing_price, bullish, label, extra_markers=(), vlines=(), cross_marker=True,
          extra_rays=(), older=(), notes=()):
    """One candle pane over one or two window segments (a skipped stretch between them gets a marker): the swept
    swing (and any older untested swings next to it) as rays to the last candle, the swing bars and the cross bar marked."""
    bars = pd.concat(segments)
    candles = _candles(bars)
    times = [c["time"] for c in candles]
    color = BULL if bullish else BEAR
    markers = list(extra_markers)
    if cross_marker:
        markers.append({"time": _snap(times, cross_t), "position": "belowBar" if bullish else "aboveBar", "color": color,
                        "shape": "arrowUp" if bullish else "arrowDown", "text": "cross bar", "size": 2})
    if any(sg.index[0] <= swing_t <= sg.index[-1] for sg in segments):
        markers.append({"time": _snap(times, swing_t), "position": "belowBar" if bullish else "aboveBar",
                        "color": SWING_COLOR, "shape": "circle", "text": f"swing {'low' if bullish else 'high'}"})
    for ot, op, hit in older:
        if any(sg.index[0] <= ot <= sg.index[-1] for sg in segments):
            markers.append({"time": _snap(times, ot), "position": "belowBar" if bullish else "aboveBar",
                            "color": SWING_COLOR, "shape": "circle", "text": "older"})
    skipped = []
    for a, b in zip(segments, segments[1:]):
        txt = f"[{_elapsed(a.index[-1], b.index[0])} skipped]"
        skipped.append(txt)
        markers.append({"time": int(b.index[0].timestamp()), "position": "aboveBar", "color": "#9ca3af",
                        "shape": "square", "text": txt})
    markers.sort(key=lambda m: m["time"])
    side = "low" if bullish else "high"
    start = _snap(times, max(swing_t, bars.index[0]))
    rays = [
        {"color": SWING_COLOR, "lineWidth": 1, "lineStyle": 2, "title": "swept level", "priceLabel": True,
         "label": f"swept swing {side} {swing_price:.2f} (swing bar {_pt(swing_t)})",
         "points": [{"time": start, "value": float(swing_price)}, {"time": times[-1], "value": float(swing_price)}]},
    ] + [
        {"color": SWING_COLOR, "lineWidth": 1, "lineStyle": 3, "title": "older swing", "priceLabel": True,
         "label": f"older swing {side} {op:.2f} (bar {_pt(ot)})" + (", cross bar went past it" if hit else ""),
         "points": [{"time": _snap(times, max(ot, bars.index[0])), "value": float(op)}, {"time": times[-1], "value": float(op)}]}
        for ot, op, hit in older] + list(extra_rays)
    all_notes = [{"color": SWING_COLOR, "text": f"swept swing {side} {swing_price:.2f}, bar {_pt(swing_t)}"}] + [
        {"color": SWING_COLOR, "text": f"older swing {side} {op:.2f}, bar {_pt(ot)}" + (" (cross bar went past it)" if hit else "")}
        for ot, op, hit in older] + list(notes)
    return {"notes": all_notes, "title": f"{label}  |  {_pt(bars.index[0])} \u2192 {_pt(bars.index[-1])}"
                     + (f"  {' '.join(skipped)}" if skipped else ""),
            "candles": candles, "markers": markers, "priceLines": [], "rays": rays, "vlines": list(vlines),
            "precision": 2, "dateOnly": False}


def _plan(spans, n, merge_gap=MERGE_GAP):
    """Candle position ranges (inclusive, clipped to 0..n-1) to show: each span (lo, hi, k) is widened by k candles either
    side, then ranges less than MERGE_GAP candles apart are one window; the bars between the others are skipped."""
    rngs = sorted((max(0, lo - k), min(n - 1, hi + k)) for lo, hi, k in spans)
    out = [list(rngs[0])]
    for a, b in rngs[1:]:
        if a <= out[-1][1] + merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(r) for r in out]


def build_example(h1, m5, row, older):
    """(chart dict: main = H1 pane, ctx = M5 pane) for one cross bar `row`. Both stop at the cross bar's hour: no later bars."""
    t, bull = row.Index, row.bullish
    five = pd.Timedelta(minutes=5)
    h1_end = t + pd.Timedelta(hours=1)
    color = BULL if bull else BEAR
    pos = h1.index.get_loc(t)

    # ---- H1: 100 candles before the bar, 50 round the swept swing and each older one when they lie further back; ends AT the bar
    spans = [(pos, pos, NEAR)] + [(h1.index.get_loc(x),) * 2 + (SPLIT,) for x in [row.swing_time] + [o[0] for o in older]]
    h1_segs = [h1.iloc[lo: hi + 1] for lo, hi in _plan(spans, pos + 1)]
    h1_pane = _pane(h1_segs, t, row.swing_time, row.swing_price, bull, "H1", older=older)

    # ---- M5: the same view, 100 candles before the hour, ends with the hour's last candle
    p0 = int(m5.index.searchsorted(t))
    p1 = int(m5.index.searchsorted(h1_end)) - 1
    m5_segs = [m5.iloc[lo: hi + 1] for lo, hi in _plan([(p0, p1, NEAR)], p1 + 1)]
    inside = m5.loc[t: h1_end - five]
    sweep_t = inside["low"].idxmin() if bull else inside["high"].idxmax()
    sweep_px = inside.at[sweep_t, "low" if bull else "high"]
    marks = [{"time": int(sweep_t.timestamp()), "position": "belowBar" if bull else "aboveBar", "color": color,
              "shape": "circle", "text": f"sweep {'low' if bull else 'high'} {sweep_px:.2f}"}]
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {_pt(t, False)}"},
          {"time": int((h1_end - five).timestamp()), "color": color, "label": "bar end"}]
    m5_pane = _pane(m5_segs, t, row.swing_time, row.swing_price, bull, "M5", extra_markers=marks, vlines=vl,
                    cross_marker=False, older=older)
    return {"main": h1_pane, "ctx": m5_pane}


def ledger(bars):
    """Every P0 (a level price closed through) of lxpb.py's state machine over `bars`, with its first retest time
    (NaT while still waiting). All P0 kinds. Levels never interact, so one run serves every cross bar."""
    _, waiting, retested = lxpb.detect_lxpb_h1(bars[["open", "high", "low", "close"]], p0_kinds=lxpb.ALL_P0_KINDS)
    return pd.concat([retested, waiting]).drop(columns=["er_closes"], errors="ignore").reset_index(drop=True)


def pick_level(led, t, swept_price, bullish):
    """The level row, or None. The level: a P0 (broken-out level) of the cross bar's direction (LHPB for a bullish bar,
    LLPB for a bearish one), beyond the swept price (above / below it), its own P0 candle starting inside the cross bar's
    H1 hour; the NEAREST to the swept price, retested or not (retest_time NaT = not retested yet)."""
    c = led[(led["type"] == ("LHPB" if bullish else "LLPB"))
            & (led["formation_time"] >= t) & (led["formation_time"] < t + pd.Timedelta(hours=1))
            & ((led["price"] > swept_price) if bullish else (led["price"] < swept_price))]
    c = c.assign(dist=(c["price"] - swept_price).abs()).sort_values(["dist", "formation_time"])
    return None if c.empty else c.iloc[0]


def level_pane(bars, minutes, label, lv, row, vlines_color, led):
    """The M5 / M2 pane of the LXPB level `lv`: P0, P1 and the first retest marked, the level as a ray from its P0."""
    t, bull = row.Index, row.bullish
    dt = pd.Timedelta(minutes=minutes)
    h1_end = t + pd.Timedelta(hours=1)
    rt = lv["retest_time"] if pd.notna(lv["retest_time"]) else None

    # shown: 100 candles round the level's P0 together with the cross bar's hour, 50 round its P1, and everything up to
    # its first retest (+5 candles); a level not retested yet runs to the last bar. Only stretches over LEVEL_GAP are skipped.
    pos = bars.index.searchsorted
    f, bo = int(pos(lv["formation_time"])), int(pos(lv["breakout_time"]))
    spans = [(min(int(pos(t)), f), max(int(pos(h1_end)) - 1, f), NEAR), (bo, bo, SPLIT)]
    if rt is not None:
        r = int(pos(rt))
        spans.append((r, r, 5))
        n = min(len(bars), r + 6)
    else:
        spans.append((len(bars) - 1, len(bars) - 1, NEAR))
        n = len(bars)
    segs = [bars.iloc[lo: hi + 1] for lo, hi in _plan(spans, n, LEVEL_GAP)]
    lvl_up = lv["type"] == "LHPB"        # an LHPB sits on a bar's high: P0 text above the bar, the retest touches from above
    price = float(lv["price"])
    marks = []
    for ts, text, above in ((lv["formation_time"], "P0", lvl_up),
                            (lv["breakout_time"], "P1", lvl_up),
                            (rt, "retest", not lvl_up)):
        if ts is not None and any(sg.index[0] <= ts <= sg.index[-1] for sg in segs):
            marks.append({"time": int(ts.timestamp()), "position": "aboveBar" if above else "belowBar",
                          "color": LXPB_COLOR, "shape": "circle", "text": text})
    vl = [{"time": int(t.timestamp()), "color": vlines_color, "label": f"H1 cross bar {_pt(t, False)}"},
          {"time": int((h1_end - dt).timestamp()), "color": vlines_color, "label": "bar end"}]
    if rt is not None:
        vl.append({"time": int(rt.timestamp()), "color": LXPB_COLOR, "label": f"retest {_pt(rt, False)}"})
    all_t = [int(b.index[0].timestamp()) for b in segs]
    ray_from = max(int(lv["formation_time"].timestamp()), all_t[0])
    last = int(segs[-1].index[-1].timestamp())
    ray = {"color": LXPB_COLOR, "lineWidth": 1, "lineStyle": 0, "title": "LXPB level", "priceLabel": True,
           "label": (f"{label} {lv['type']} {price:.2f} ({lv['p0_kind']}): P0 {_pt(lv['formation_time'])}, "
                     f"P1 {_pt(lv['breakout_time'])}, " + (f"first retest {_pt(rt)}" if rt is not None else "not retested yet")),
           "points": [{"time": ray_from, "value": price}, {"time": last, "value": price}]}
    notes = [{"color": LXPB_COLOR, "text": f"{label} {lv['type']} {price:.2f} ({lv['p0_kind']})"},
             {"color": LXPB_COLOR, "text": f"P0 {_pt(lv['formation_time'])}"},
             {"color": LXPB_COLOR, "text": f"P1 {_pt(lv['breakout_time'])}"},
             {"color": LXPB_COLOR, "text": f"first retest {_pt(rt)}" if rt is not None else "not retested yet"}]
    pane = _pane(segs, t, row.swing_time, row.swing_price, bull, f"{label}-LXPB", extra_markers=marks, vlines=vl,
                 cross_marker=False, extra_rays=[ray], notes=notes)
    bg = _other_levels(led, lv, pane["candles"], t, bull)
    pane["bgRays"] = bg
    if bg:
        pane["notes"].append({"color": "#8b8b8b", "text": f"dotted: {len(bg)} other {label} {'LHPB' if bull else 'LLPB'} levels with their P0 inside the cross bar hour, P0 to first retest"})
    return pane


def _other_levels(led, lv, candles, t, bullish):
    """The OTHER levels of the chosen one's type (LHPB for a bullish bar, LLPB for a bearish one) whose P0 starts inside the
    cross bar's H1 hour, any price, each a dotted ray behind the candles from its P0 to its first retest (or the last candle)."""
    times = [c["time"] for c in candles]
    first, last = times[0], times[-1]
    keep = led[(led["type"] == ("LHPB" if bullish else "LLPB"))
               & (led["formation_time"] >= t) & (led["formation_time"] < t + pd.Timedelta(hours=1))
               & ~((led["formation_time"] == lv["formation_time"]) & (led["price"] == lv["price"]))]
    out = []
    for o in keep.itertuples():
        a, b = int(o.formation_time.timestamp()), (int(o.retest_time.timestamp()) if pd.notna(o.retest_time) else last)
        at = lambda x: times[max(bisect.bisect_right(times, x) - 1, 0)]
        out.append({"color": LXPB_COLOR + "99", "lineWidth": 1, "lineStyle": 1,
                    "points": [{"time": at(max(a, first)), "value": float(o.price)},
                               {"time": at(min(b, last)), "value": float(o.price)}]})
    return out


def level_cell(lv, label):
    if lv is None:
        return f'<span class="src-tag">no {label} level</span>'
    retest = f"retest {_pt(lv['retest_time'])}" if pd.notna(lv["retest_time"]) else "not retested yet"
    return (f"{lv['price']:.2f} {lv['type']}<span class=\"src-tag\"> {lv['p0_kind']}</span><br>"
            f"<span class=\"src-tag\">P0 {_pt(lv['formation_time'], False)} &middot; {retest}</span>")


def collect(h1, k=2):
    bull, bear = find_cross_bar(h1, k)
    frames = []
    for df, is_bull in ((bull, True), (bear, False)):
        if len(df):
            frames.append(df.assign(bullish=is_bull))
    return pd.concat(frames).sort_index() if frames else pd.DataFrame()


FILTERS_HTML = """
<div class="filter-panel">
  <div class="filter-row"><span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="reviewed" checked> Reviewed</label></div>
  <div class="filter-row"><span class="filter-label">Valid</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="not_valid" checked> Not valid</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="valid" checked> Valid</label></div>
  <div class="filter-row"><span class="filter-label">Notes</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="no_notes" checked> No notes</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="has_notes" checked> Has notes</label></div>
</div>"""

TOOLBAR_HTML = """
  <div class="toolbar">
    <button class="btn" onclick="exportReviewCsv()">\u2b07 Export notes CSV</button>
    <label class="btn" for="import-review-file">\u2b06 Import notes CSV</label>
    <input type="file" id="import-review-file" accept=".csv" class="hidden" onchange="importReviewCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved Reviewed/Valid/Notes in this browser for this report?')) clearAllReview();">\U0001f5d1 Clear all</button>
  </div>"""

HEAD_HTML = (
    "<th class=\"left\">#</th><th class=\"left\">Cross bar (H1 start)</th><th class=\"left\">Type</th>"
    "<th class=\"left\" title=\"Which spike / engulfing detector the SFP bar also satisfies\">Confirmed by</th>"
    "<th title=\"The swing price the bar swept and closed back across (find_sfp)\">Swept level</th>"
    "<th class=\"left\">Swing bar</th>"
    "<th class=\"left\" title=\"Nearest M5 LXPB level (P0) of the bar's direction beyond the swept price, its P0 candle inside the cross bar's H1 hour\">M5 LXPB level</th>"
    "<th class=\"left\" title=\"The same on M2 bars\">M2 LXPB level</th>"
    "<th>Open</th><th>High</th><th>Low</th><th>Close</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 16


def _row_html(i, row, older, cells):
    cls = "type-lhpb" if row.bullish else "type-llpb"
    key = f"{row.Index.tz_convert('UTC').strftime('%Y-%m-%dT%H')}_{'bull' if row.bullish else 'bear'}"
    more = f"<br><span class=\"src-tag\">+{len(older)} older within {OLDER_PTS:g} pt</span>" if older else ""
    return f"""
<tr class="lvl-row is-valid {cls}" data-idx="{i}" data-key="{key}" onclick="toggleChart({i})" style="cursor:pointer">
  <td class="left">{i + 1}</td>
  <td class="left">{_pt(row.Index)}</td>
  <td class="left" style="color:{BULL if row.bullish else BEAR}">{'Bullish' if row.bullish else 'Bearish'}</td>
  <td class="left">{_confirmation(row)}</td>
  <td>{row.swing_price:.2f}<span class="src-tag"> swing {'low' if row.bullish else 'high'}</span>{more}</td>
  <td class="left">{_pt(row.swing_time)}</td>
  <td class="left">{cells['l5']}</td>
  <td class="left">{cells['l2']}</td>
  <td>{row.open:.2f}</td><td>{row.high:.2f}</td><td>{row.low:.2f}</td><td>{row.close:.2f}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-main-{i}"></div><div class="chart-ph" id="c-main-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-ctx-{i}"></div><div class="chart-ph" id="c-ctx-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l5-{i}"></div><div class="chart-ph" id="c-l5-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l2-{i}"></div><div class="chart-ph" id="c-l2-{i}"></div></div>
  </div></td>
</tr>"""


def render(rows, h1, output, label, since):
    # one LXPB ledger per timeframe, started a month before the first cross bar so its levels have formed
    lo = pd.Timestamp(since, tz=PT) - pd.Timedelta(days=30)
    tf = {key: (name, mins, load_es(f"{mins}min", start=lo, tz=PT)) for key, name, mins in LEVEL_TFS}
    led = {key: ledger(bars) for key, (_, _, bars) in tf.items()}
    charts, trs = [], []
    for i, row in enumerate(rows.itertuples()):
        older = older_swings(h1, h1.index.get_loc(row.Index), row.swing_time, row.swing_price, row.bullish)
        chart = build_example(h1, tf['l5'][2], row, older)
        cells = {}
        for key, (name, mins, bars) in tf.items():
            lv = pick_level(led[key], row.Index, row.swing_price, row.bullish)
            cells[key] = level_cell(lv, name)
            chart[key] = None if lv is None else level_pane(bars, mins, name, lv, row, BULL if row.bullish else BEAR, led[key])
        charts.append(chart)
        trs.append(_row_html(i, row, older, cells))
    n_bull = int(rows["bullish"].sum())
    title = "ES H1 cross bars" + (f" ({label})" if label else "")
    storage_key = "lxpb_cross_bar_review_v1_" + "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{M.CSS}
{NOTES_CSS}
</head><body>
<h1>{title}</h1>
<p class="lead">A CROSS BAR is an H1 bar that causes an SFP (patterns_pure <code>find_sfp</code>: it sweeps a confirmed, still-untested
fractal swing and closes back on the near side) AND is also a spike or an engulfing bar. Bullish: a swing LOW swept and the bar is
a hammer or a bullish engulfing bar. Bearish: a swing HIGH swept and the bar is a shooting star or a bearish engulfing bar. All of it
is existing detectors (<code>find_cross_bar</code> only combines them). Click a row: H1 and M5 panes end at the cross bar (amber dashed =
the swept swing, dotted amber = older swings within 7 pt), then the M5-LXPB and M2-LXPB panes show the nearest LXPB level of the
bar's direction beyond the swept price whose P0 formed inside the bar's hour, with its P1 and first retest (solid purple); other
same-type levels formed in that hour are dotted behind the candles. Times are PT.</p>
<div class="summary">
  <div class="box"><strong>{len(rows)}</strong>cross bars</div>
  <div class="box"><strong>{n_bull}</strong>bullish</div>
  <div class="box"><strong>{len(rows) - n_bull}</strong>bearish</div>
  <div class="box"><strong id="sum-shown">{len(rows)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>{TOOLBAR_HTML}
</div>
{FILTERS_HTML}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{HEAD_HTML}</tr></thead>
<tbody>{"".join(trs)}
</tbody></table></div>
{_js(charts, storage_key)}
</body></html>
"""
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved {output}: {len(rows)} cross bars ({n_bull} bullish)")


# The review store keeps rows on the site's /api/rows (Postgres). A copy opened from disk has no such API, so the
# page also mirrors every row to this browser's localStorage and reads it back when the API is unreachable.
_LOCAL_FALLBACK_JS = """
(function () {
  const LS = 'rowstore_local_';
  const init0 = RowStore.prototype.init, set0 = RowStore.prototype.set;
  RowStore.prototype.init = async function () {
    await init0.call(this);
    if (!Object.keys(this.cache).length) {
      try { this.cache = JSON.parse(localStorage.getItem(LS + this.reportKey) || '{}'); } catch (e) { this.cache = {}; }
    }
    return this.cache;
  };
  RowStore.prototype.set = function (key, fields, opts) {
    set0.call(this, key, fields, opts);
    try { localStorage.setItem(LS + this.reportKey, JSON.stringify(this.cache)); } catch (e) { /* ignore */ }
  };
})();
"""

# The marker / ray texts can sit behind candles, so each pane also lists them in a strip above the plot.
NOTES_JS = """
  if (cd.notes && cd.notes.length) {
    const nd = document.createElement('div');
    nd.className = 'pane-notes';
    cd.notes.forEach(n => {
      const sp = document.createElement('span');
      sp.style.color = n.color;
      sp.textContent = '\\u25cf ' + n.text;
      nd.appendChild(sp);
    });
    el.parentNode.insertBefore(nd, el);
    // the library's rule sizes the plot as 100% - 31px only when it directly follows the title: do it here
    const fit = () => { el.style.height = 'calc(100% - ' + (31 + nd.offsetHeight) + 'px)'; };
    fit();
    window.addEventListener('resize', fit);
  }
"""

# Other LXPB levels: dotted line series made BEFORE the candle series, so the candles are drawn over them.
BG_JS = """
  (cd.bgRays || []).forEach(r => {
    const bs = chart.addLineSeries({ color: r.color, lineWidth: r.lineWidth || 1, lineStyle: r.lineStyle,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, pointMarkersVisible: false });
    bs.setData(r.points);
  });
  """

NOTES_CSS = """<style>
.pane-notes { display: flex; flex-wrap: wrap; gap: 2px 18px; padding: 4px 10px; font: 12px/1.3 Courier New, monospace;
  background: #0b0b0b; border-bottom: 1px solid #222; }
</style>"""

_OWN_JS = """
function _renderStack(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  _renderChartPane(i, 'main', cd.main);
  _renderChartPane(i, 'ctx', cd.ctx);
  _renderChartPane(i, 'l5', cd.l5);
  _renderChartPane(i, 'l2', cd.l2);
}
function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.textContent = opening ? '\\u25bc' : '\\u25b6'; }
  if (opening && !rendered[i]) { _renderStack(i); rendered[i] = true; }
}
window.addEventListener('load', () => { if (CHARTS.length <= 2) { toggleChart(0); } });
</script>
"""


def _js(charts, storage_key):
    """The M1 report's chart machinery (shared helpers up to its pane code, then its two-pane renderer) and its
    Reviewed / Valid / Notes store (the shared review JS, Valid on by default), with a localStorage fallback."""
    shared = M._SHARED_JS
    pre = shared[:shared.index("function _centerLogicalRange")]
    mid_start = M.JS.index("function chartFont()")
    mid_end = M.JS.rindex("// ----", 0, M.JS.index("// Dynamic filters -- see"))
    mid = M.JS[mid_start:mid_end]
    a = mid.index("_renderStack = function(i) {")
    b = mid.index("};\n", a) + 3
    mid = mid[:a] + mid[b:]
    old_names = "const _PANE_NAME = { main: 'M1', ctx: 'M5', d1: 'D1', h1: 'H1' };"
    assert old_names in mid, "M1 report chart JS changed: re-check _PANE_NAME"
    mid = mid.replace(old_names, "const _PANE_NAME = { main: 'H1', ctx: 'M5', l5: 'M5-LXPB', l2: 'M2-LXPB' };")
    old_candles = "const series = _addCandles(chart, cd.precision);"
    assert old_candles in mid, "M1 report chart JS changed: re-check the candle series line"
    mid = mid.replace(old_candles, BG_JS + old_candles, 1)
    old_fs = "titleEl.appendChild(fsBtn);"
    assert old_fs in mid, "M1 report chart JS changed: re-check the fullscreen button hook"
    mid = mid.replace(old_fs, old_fs + NOTES_JS, 1)
    old_msg = "'  |  (no bars covering this trade)'"
    assert old_msg in mid, "M1 report chart JS changed: re-check the empty-pane message"
    mid = mid.replace(old_msg, "'  |  (no LXPB level of this direction beyond the swept price with its P0 formed inside the cross bar hour)'")
    marker = '<script src="/js/row-store.js"></script>'
    review = shared[shared.index(marker):]
    with open(ROW_STORE_JS, encoding="utf-8") as f:
        row_store = f.read()
    review = review.replace(marker, "<script>\n" + row_store + "\n" + _LOCAL_FALLBACK_JS + "</script>")
    return (pre + mid + _OWN_JS + review).replace("__CHARTS_JSON__", json.dumps(charts)).replace("__STORAGE_KEY__", storage_key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01", help="first H1 bar (UTC date) the detector reads")
    ap.add_argument("--last", type=int, default=0, help="only the newest N bullish and N bearish cross bars")
    ap.add_argument("--at", default="", help="only the cross bars starting at these PT times, e.g. '2026-10-26 16:00,2026-09-18 09:00'")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    if not (a.all or a.last or a.at):
        ap.error("pass --all, --last N or --at")
    h1 = load_es("1h", tz=PT)        # the whole history: a swing from before --since can be the one a bar sweeps
    rows = collect(h1)
    rows = rows.loc[rows.index >= pd.Timestamp(a.since, tz=PT)]
    if a.at:
        want = [pd.Timestamp(x.strip(), tz=PT) for x in a.at.split(",")]
        rows = rows.loc[rows.index.isin(want)]
        if len(rows) != len(want):
            ap.error(f"not cross bars: {[str(w) for w in want if w not in rows.index]}")
    elif a.last:
        rows = pd.concat([g.tail(a.last) for _, g in rows.groupby("bullish")]).sort_index()
    rows = rows.sort_index(ascending=False)
    render(rows, h1, a.output, a.label or ("sample" if (a.last or a.at) else f"since {a.since}"), a.since)


if __name__ == "__main__":
    main()
