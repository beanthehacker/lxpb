"""
render_cross_bar_report.py
==========================
CROSS BAR REPORT: every cross bar (patterns_pure `find_cross_bar`: an H1 bar that causes an SFP and is also a spike or an
engulfing bar) on continuous ES, one row per cross bar, with two panes per row in the M1 fade report's page layout:
the H1 pane on top and the M5 pane under it, plus Reviewed / Valid / Notes per row (the M1 report's review store).
Each pane shows 100 candles either side of the cross bar, and 50 either side of the retest. The cross bar's own LEVEL is its high for a bullish one (hammer /
bullish engulfing) and its low for a bearish one (shooting star / bearish engulfing); "retest" = after an H1 bar has CLOSED
beyond that level (above the high / below the low), the first later H1 bar that trades back to it. When the retest's 50
candles do not join the cross bar's 100 (less than 10 candles apart), the bars in between are skipped with a "[... skipped]"
marker; a level not retested yet just shows the 100 after.
Candles stay grey; the swept swing is a dashed amber ray from its own bar to the right edge, the retest level a thin blue
ray, the cross bar an arrow on its H1 candle and a dotted start/end pair on the M5 pane, with the M5 candle that made the
sweep marked.

Panes 3 and 4 (M5-LXPB, M2-LXPB): the nearest LXPB level of the cross bar's own direction (LHPB for a bullish one, LLPB for a
bearish one) lying beyond the swept swing price (above it for bullish, below it for bearish) whose P0 (formation candle) starts from
1 h before the cross bar to 1 h after it ended (retested or not): its P0, P1 (breakout) and FIRST retest, if any, are marked,
windowed around the level's own life: 100 candles either side of its P0 together with the cross bar's hour, 50 either side of its P1
and 50 either side of its first retest, with the bars between skipped where those stretches are more than 10 candles apart. Levels come from lxpb.py's state machine (every P0 kind) run over the M5 / M2 bars rolled up from the 1-minute ticks.

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
LEVEL_BEFORE = pd.Timedelta(hours=1)    # an LXPB level counts when its P0 (formation bar) starts this long before the cross bar ...
LEVEL_AFTER = pd.Timedelta(hours=2)     # ... up to this long after the cross bar's start (= 1 h after the bar has ended)
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


def retest_of(h1, pos, bullish):
    """(level, breakout bar start, retest bar start) of the cross bar at H1 position `pos`. The level is the bar's high
    (bullish) / low (bearish). Breakout: the first later H1 bar that CLOSES beyond it (above the high / below the low).
    Retest: the first bar after that which trades back to the level (low <= level / high >= level). None = not yet."""
    level = float(h1["high"].iat[pos] if bullish else h1["low"].iat[pos])
    after = h1.iloc[pos + 1:]
    beyond = ((after["close"] > level) if bullish else (after["close"] < level)).to_numpy()
    if not beyond.any():
        return level, None, None
    k = int(beyond.argmax())
    rest = after.iloc[k + 1:]
    touch = ((rest["low"] <= level) if bullish else (rest["high"] >= level)).to_numpy()
    return level, after.index[k], (rest.index[int(touch.argmax())] if touch.any() else None)


def _pane(segments, cross_t, swing_t, swing_price, level, bullish, label, extra_markers=(), vlines=(), cross_marker=True,
          extra_rays=(), show_level=True):
    """One candle pane over one or two window segments (a skipped stretch between them gets a marker): the swept
    swing and the cross bar's own level as rays, the swing bar and the cross bar marked."""
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
        {"color": RETEST_COLOR, "lineWidth": 1, "lineStyle": 0, "title": "retest level", "priceLabel": True,
         "label": f"cross bar's {'high' if bullish else 'low'} {level:.2f} (the level to retest)",
         "points": [{"time": _snap(times, cross_t), "value": level}, {"time": times[-1], "value": level}]},
    ][: 2 if show_level else 1] + list(extra_rays)
    return {"title": f"{label}  |  {_pt(bars.index[0])} \u2192 {_pt(bars.index[-1])}"
                     + (f"  {' '.join(skipped)}" if skipped else ""),
            "candles": candles, "markers": markers, "priceLines": [], "rays": rays, "vlines": list(vlines),
            "precision": 2, "dateOnly": False}


def _plan(spans, n):
    """Candle position ranges (inclusive, clipped to 0..n-1) to show: each span (lo, hi, k) is widened by k candles either
    side, then ranges less than MERGE_GAP candles apart are one window; the bars between the others are skipped."""
    rngs = sorted((max(0, lo - k), min(n - 1, hi + k)) for lo, hi, k in spans)
    out = [list(rngs[0])]
    for a, b in rngs[1:]:
        if a <= out[-1][1] + MERGE_GAP:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(r) for r in out]


def build_example(h1, m5, row, level, bo_t, rt_t):
    """(chart dict: main = H1 pane, ctx = M5 pane) for one cross bar `row`, with its level and retest."""
    t, bull = row.Index, row.bullish
    five = pd.Timedelta(minutes=5)
    h1_end = t + pd.Timedelta(hours=1)
    color = BULL if bull else BEAR
    pos = h1.index.get_loc(t)

    # ---- H1
    rp = h1.index.get_loc(rt_t) if rt_t is not None else None
    h1_segs = [h1.iloc[lo: hi + 1] for lo, hi in _plan([(pos, pos, NEAR)] + ([(rp, rp, SPLIT)] if rp is not None else []), len(h1))]
    h1_marks = []
    for ts, text, up in ((bo_t, "breakout", bull), (rt_t, "retest", not bull)):
        if ts is not None and any(sg.index[0] <= ts <= sg.index[-1] for sg in h1_segs):
            h1_marks.append({"time": int(ts.timestamp()), "position": "aboveBar" if up else "belowBar",
                             "color": RETEST_COLOR, "shape": "circle", "text": text})
    h1_pane = _pane(h1_segs, t, row.swing_time, row.swing_price, level, bull, "H1", extra_markers=h1_marks)

    # ---- M5: the retest M5 candle is the first one of the retest hour that trades at the level
    rt5 = None
    if rt_t is not None:
        hour = m5.loc[rt_t: rt_t + pd.Timedelta(hours=1) - five]
        hit = hour.loc[(hour["low"] <= level) if bull else (hour["high"] >= level)]
        rt5 = hit.index[0] if len(hit) else None
    p0 = int(m5.index.searchsorted(t))
    p1 = int(m5.index.searchsorted(h1_end)) - 1
    m5_segs = [m5.iloc[lo: hi + 1] for lo, hi in
               _plan([(p0, p1, NEAR)] + ([(int(m5.index.get_loc(rt5)),) * 2 + (SPLIT,)] if rt5 is not None else []), len(m5))]
    inside = m5.loc[t: h1_end - five]
    sweep_t = inside["low"].idxmin() if bull else inside["high"].idxmax()
    sweep_px = inside.at[sweep_t, "low" if bull else "high"]
    marks = [{"time": int(sweep_t.timestamp()), "position": "belowBar" if bull else "aboveBar", "color": color,
              "shape": "circle", "text": f"sweep {'low' if bull else 'high'} {sweep_px:.2f}"}]
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {_pt(t, False)}"},
          {"time": int((h1_end - five).timestamp()), "color": color, "label": "bar end"}]
    if rt5 is not None:
        marks.append({"time": int(rt5.timestamp()), "position": "aboveBar" if bull else "belowBar",
                      "color": RETEST_COLOR, "shape": "circle", "text": "retest"})
        vl.append({"time": int(rt5.timestamp()), "color": RETEST_COLOR, "label": f"retest {_pt(rt5, False)}"})
    m5_pane = _pane(m5_segs, t, row.swing_time, row.swing_price, level, bull, "M5", extra_markers=marks, vlines=vl,
                    cross_marker=False)
    return {"main": h1_pane, "ctx": m5_pane}


def ledger(bars):
    """Every P0 (a level price closed through) of lxpb.py's state machine over `bars`, with its first retest time
    (NaT while still waiting). All P0 kinds. Levels never interact, so one run serves every cross bar."""
    _, waiting, retested = lxpb.detect_lxpb_h1(bars[["open", "high", "low", "close"]], p0_kinds=lxpb.ALL_P0_KINDS)
    return pd.concat([retested, waiting]).drop(columns=["er_closes"], errors="ignore").reset_index(drop=True)


def pick_level(led, t, swept_price, bullish):
    """The level row, or None. The level: a P0 (broken-out level) of the cross bar's direction (LHPB for a bullish bar,
    LLPB for a bearish one), beyond the swept price (above / below it), its own P0 candle starting from 1 h before the cross
    bar to 1 h after it ended; the NEAREST to the swept price, retested or not (retest_time NaT = not retested yet)."""
    c = led[(led["type"] == ("LHPB" if bullish else "LLPB"))
            & (led["formation_time"] >= t - LEVEL_BEFORE) & (led["formation_time"] < t + LEVEL_AFTER)
            & ((led["price"] > swept_price) if bullish else (led["price"] < swept_price))]
    c = c.assign(dist=(c["price"] - swept_price).abs()).sort_values(["dist", "formation_time"])
    return None if c.empty else c.iloc[0]


def level_pane(bars, minutes, label, lv, row, vlines_color):
    """The M5 / M2 pane of the LXPB level `lv`: P0, P1 and the first retest marked, the level as a ray from its P0."""
    t, bull = row.Index, row.bullish
    dt = pd.Timedelta(minutes=minutes)
    h1_end = t + pd.Timedelta(hours=1)
    rt = lv["retest_time"] if pd.notna(lv["retest_time"]) else None

    # surrounded: the level's P0 together with the cross bar's hour (100 either side), its P1 and its first retest (50 each)
    pos = bars.index.searchsorted
    f, bo = int(pos(lv["formation_time"])), int(pos(lv["breakout_time"]))
    spans = [(min(int(pos(t)), f), max(int(pos(h1_end)) - 1, f), NEAR), (bo, bo, SPLIT)]
    if rt is not None:
        spans.append((int(pos(rt)),) * 2 + (SPLIT,))
    segs = [bars.iloc[lo: hi + 1] for lo, hi in _plan(spans, len(bars))]
    lvl_up = lv["type"] == "LHPB"        # an LHPB sits on a bar's high: P0 text above the bar, the retest touches from above
    price = float(lv["price"])
    marks = []
    for ts, text, above in ((lv["formation_time"], f"P0 {lv['type']} {price:.2f}", lvl_up),
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
    pane = _pane(segs, t, row.swing_time, row.swing_price, 0.0, bull, f"{label}-LXPB", extra_markers=marks, vlines=vl,
                 cross_marker=False, extra_rays=[ray], show_level=False)
    return pane


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
    "<th title=\"The cross bar's own level: its high for a bullish one, its low for a bearish one\">Retest level</th>"
    "<th class=\"left\" title=\"First H1 bar to trade back to the level after an H1 bar closed beyond it (above the high / below the low)\">Retested</th>"
    "<th class=\"left\" title=\"Nearest M5 LXPB level (P0) of the bar's direction beyond the swept price, its P0 candle from 1 h before to 1 h after the cross bar\">M5 LXPB level</th>"
    "<th class=\"left\" title=\"The same on M2 bars\">M2 LXPB level</th>"
    "<th>Open</th><th>High</th><th>Low</th><th>Close</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 18


def _row_html(i, row, level, bo_t, rt_t, cells):
    cls = "type-lhpb" if row.bullish else "type-llpb"
    key = f"{row.Index.tz_convert('UTC').strftime('%Y-%m-%dT%H')}_{'bull' if row.bullish else 'bear'}"
    retest_cell = _pt(rt_t) if rt_t is not None else ("no breakout yet" if bo_t is None else "not retested yet")
    return f"""
<tr class="lvl-row is-valid {cls}" data-idx="{i}" data-key="{key}" onclick="toggleChart({i})" style="cursor:pointer">
  <td class="left">{i + 1}</td>
  <td class="left">{_pt(row.Index)}</td>
  <td class="left" style="color:{BULL if row.bullish else BEAR}">{'Bullish' if row.bullish else 'Bearish'}</td>
  <td class="left">{_confirmation(row)}</td>
  <td>{row.swing_price:.2f}<span class="src-tag"> swing {'low' if row.bullish else 'high'}</span></td>
  <td class="left">{_pt(row.swing_time)}</td>
  <td>{level:.2f}<span class="src-tag"> bar {'high' if row.bullish else 'low'}</span></td>
  <td class="left">{retest_cell}</td>
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
        level, bo_t, rt_t = retest_of(h1, h1.index.get_loc(row.Index), row.bullish)
        chart = build_example(h1, tf['l5'][2], row, level, bo_t, rt_t)
        cells = {}
        for key, (name, mins, bars) in tf.items():
            lv = pick_level(led[key], row.Index, row.swing_price, row.bullish)
            cells[key] = level_cell(lv, name)
            chart[key] = None if lv is None else level_pane(bars, mins, name, lv, row, BULL if row.bullish else BEAR)
        charts.append(chart)
        trs.append(_row_html(i, row, level, bo_t, rt_t, cells))
    n_bull = int(rows["bullish"].sum())
    title = "ES H1 cross bars" + (f" ({label})" if label else "")
    storage_key = "lxpb_cross_bar_review_v1_" + "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{M.CSS}
</head><body>
<h1>{title}</h1>
<p class="lead">A CROSS BAR is an H1 bar that causes an SFP (patterns_pure <code>find_sfp</code>: it sweeps a confirmed, still-untested
fractal swing and closes back on the near side) AND is also a spike or an engulfing bar. Bullish: a swing LOW swept and the bar is
a hammer or a bullish engulfing bar. Bearish: a swing HIGH swept and the bar is a shooting star or a bearish engulfing bar. All of it
is existing detectors (<code>find_cross_bar</code> only combines them). Click a row for the H1 pane (top) and M5 pane (under it), 100
candles either side of the cross bar. The dashed amber ray is the swept swing from its own bar to the right edge; the blue ray is the
cross bar's own level (high for bullish, low for bearish). Retest = after an H1 bar closed beyond that level, the first later H1 bar that
trades back to it. The retest always has 50 candles either side of it; when that is
well past the cross bar's 100 the bars in between are skipped. The arrow is the cross bar; on M5 the dotted pair spans the cross bar's hour and the green /
red circle is the M5 candle that made the sweep. Times are PT.</p>
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
    old_msg = "'  |  (no bars covering this trade)'"
    assert old_msg in mid, "M1 report chart JS changed: re-check the empty-pane message"
    mid = mid.replace(old_msg, "'  |  (no LXPB level of this direction beyond the swept price with its P0 within 1 h of the cross bar)'")
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
    h1 = load_es("1h", tz=PT).loc[a.since:]
    rows = collect(h1)
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
