"""
render_cross_bar_report.py
==========================
CROSS BAR REPORT: every cross bar (patterns_pure `find_cross_bar`: an H1 bar that causes an SFP and is also a spike or an
engulfing bar) on continuous ES, one row per cross bar, with two panes per row in the M1 fade report's page layout:
the H1 pane on top and the M5 pane under it, plus Reviewed / Valid / Notes per row (the M1 report's review store).
Each pane shows 100 candles either side of the cross bar. The cross bar's own LEVEL is its high for a bullish one (hammer /
bullish engulfing) and its low for a bearish one (shooting star / bearish engulfing); "retest" = after an H1 bar has CLOSED
beyond that level (above the high / below the low), the first later H1 bar that trades back to it. When the retest is not
inside the 100 candles after the cross bar, the bars in between are skipped and the pane shows 50 either side of the cross
bar and 50 either side of the retest, with a "[... skipped]" marker; a level not retested yet just shows the 100 after.
Candles stay grey; the swept swing is a dashed amber ray from its own bar to the right edge, the retest level a thin blue
ray, the cross bar an arrow on its H1 candle and a dotted start/end pair on the M5 pane, with the M5 candle that made the
sweep marked.

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
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "ss_m1_confl"), os.path.join(_REPO_ROOT, "patterns_pure"), r"E:\data_source"):
    sys.path.insert(0, _p)
from es_data import load_es                       # noqa: E402
from find_cross_bar import find_cross_bar         # noqa: E402
import render_m1_confl_report as M                # noqa: E402

ROW_STORE_JS = os.path.join(_REPO_ROOT, "public", "js", "row-store.js")
PT = "America/Los_Angeles"
NEAR = 100                          # candles shown either side of the cross bar (H1 and M5)
SPLIT = 50                          # candles either side of the cross bar AND of its retest when the bars between are skipped
BULL, BEAR = "#4ade80", "#f87171"
SWING_COLOR = "#fbbf24"
RETEST_COLOR = "#60a5fa"


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


def _pane(segments, cross_t, swing_t, swing_price, level, bullish, label, extra_markers=(), vlines=(), cross_marker=True):
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
    ]
    return {"title": f"{label}  |  {_pt(bars.index[0])} \u2192 {_pt(bars.index[-1])}"
                     + (f"  {' '.join(skipped)}" if skipped else ""),
            "candles": candles, "markers": markers, "priceLines": [], "rays": rays, "vlines": list(vlines),
            "precision": 2, "dateOnly": False}


def _m5(lo, hi):
    m1 = load_es("1m", start=lo, end=hi, tz=PT)
    return m1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def build_example(h1, row, level, bo_t, rt_t):
    """(chart dict: main = H1 pane, ctx = M5 pane) for one cross bar `row`, with its level and retest."""
    t, bull = row.Index, row.bullish
    five = pd.Timedelta(minutes=5)
    h1_end = t + pd.Timedelta(hours=1)
    color = BULL if bull else BEAR
    pos = h1.index.get_loc(t)
    n = len(h1)

    def h1_seg(c, k):
        return h1.iloc[max(0, c - k): min(n, c + k + 1)]

    # ---- H1: 100 either side; a retest beyond that -> 50 either side of the cross bar and of the retest
    rp = h1.index.get_loc(rt_t) if rt_t is not None else None
    split = rp is not None and rp > pos + NEAR
    h1_segs = [h1_seg(pos, SPLIT), h1_seg(rp, SPLIT)] if split else [h1_seg(pos, NEAR)]
    h1_marks = []
    for ts, text, up in ((bo_t, "breakout", bull), (rt_t, "retest", not bull)):
        if ts is not None and any(sg.index[0] <= ts <= sg.index[-1] for sg in h1_segs):
            h1_marks.append({"time": int(ts.timestamp()), "position": "aboveBar" if up else "belowBar",
                             "color": RETEST_COLOR, "shape": "circle", "text": text})
    h1_pane = _pane(h1_segs, t, row.swing_time, row.swing_price, level, bull, "H1", extra_markers=h1_marks)

    # ---- M5: same rule in M5 candles; the retest M5 candle is the first one of the retest hour that trades at the level
    rt5 = None
    if rt_t is not None:
        hour = _m5(rt_t, rt_t + pd.Timedelta(hours=1))
        hit = hour.loc[(hour["low"] <= level) if bull else (hour["high"] >= level)]
        rt5 = hit.index[0] if len(hit) else None
    near_end = h1_end + NEAR * five
    if rt5 is not None and rt5 > near_end:
        m5_segs = [_m5(t - SPLIT * five, h1_end + SPLIT * five), _m5(rt5 - SPLIT * five, rt5 + (SPLIT + 1) * five)]
    else:
        m5_segs = [_m5(t - NEAR * five, near_end)]
    inside = m5_segs[0].loc[t: h1_end - five]
    sweep_t = inside["low"].idxmin() if bull else inside["high"].idxmax()
    sweep_px = inside.at[sweep_t, "low" if bull else "high"]
    marks = [{"time": int(sweep_t.timestamp()), "position": "belowBar" if bull else "aboveBar", "color": color,
              "shape": "circle", "text": f"sweep {'low' if bull else 'high'} {sweep_px:.2f}"}]
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {_pt(t, False)}"},
          {"time": int((h1_end - five).timestamp()), "color": color, "label": "bar end"}]
    if rt5 is not None and any(sg.index[0] <= rt5 <= sg.index[-1] for sg in m5_segs):
        marks.append({"time": int(rt5.timestamp()), "position": "aboveBar" if bull else "belowBar",
                      "color": RETEST_COLOR, "shape": "circle", "text": "retest"})
        vl.append({"time": int(rt5.timestamp()), "color": RETEST_COLOR, "label": f"retest {_pt(rt5, False)}"})
    m5_pane = _pane(m5_segs, t, row.swing_time, row.swing_price, level, bull, "M5", extra_markers=marks, vlines=vl,
                    cross_marker=False)
    return {"main": h1_pane, "ctx": m5_pane}


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
    "<th>Open</th><th>High</th><th>Low</th><th>Close</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 16


def _row_html(i, row, level, bo_t, rt_t):
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
  </div></td>
</tr>"""


def render(rows, h1, output, label):
    charts, trs = [], []
    for i, row in enumerate(rows.itertuples()):
        level, bo_t, rt_t = retest_of(h1, h1.index.get_loc(row.Index), row.bullish)
        charts.append(build_example(h1, row, level, bo_t, rt_t))
        trs.append(_row_html(i, row, level, bo_t, rt_t))
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
trades back to it. A retest more than 100 candles after the cross bar is shown by skipping the bars in between: 50 either side of
the cross bar and 50 either side of the retest. The arrow is the cross bar; on M5 the dotted pair spans the cross bar's hour and the green /
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
    mid = mid.replace(old_names, "const _PANE_NAME = { main: 'H1', ctx: 'M5' };")
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
    render(rows, h1, a.output, a.label or ("sample" if (a.last or a.at) else f"since {a.since}"))


if __name__ == "__main__":
    main()
