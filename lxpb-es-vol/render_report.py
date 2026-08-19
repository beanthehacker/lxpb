"""
Render a v25-style ("trapVariants/scans-tmp-files-etc/v25_2026.html") dark-theme
HTML dashboard for the LXPB-zone-first + volume-absorption strategy, using the
SAME charting library as that reference page: TradingView lightweight-charts@4
(loaded from the same CDN), not Plotly -- and the same candle color scheme
(#DDDDD0 up / #888888 down, no grid lines, Courier New 9px, borderColor #333)
and same subscribeCrosshairMove-driven title/legend pattern as v25's _renderOne.

Per confirmed signal, 4 lightweight-charts instances are shown, stacked
vertically: an H1 context chart on top (see below), then 1s candles / bid
volume / ask volume underneath. The bottom 3 (1s candles/bid/ask) share one
x-axis and are synced on BOTH pan/zoom (timeScale().subscribeVisibleLogicalRangeChange)
AND crosshair position/hover values (chart.subscribeCrosshairMove +
chart.setCrosshairPosition), so hovering any one of the three shows the
same instant on all three plus O/H/L/C or volume value in each pane's title
-- mirrors v25's per-chart hover legend, extended to sync across panes.

The H1 context chart shows the tested LXPB level's own lifetime: from a few
H1 bars before the level's FORMATION bar (the swing pivot that later broke
out and became this LHPB/LLPB level), through the H1 bar containing this
row's 1s absorption/retest event, plus H1_CANDLES_AFTER extra bars afterward
so you can see whether the setup played out. It is independent of the 1s
trio (different timeframe, not pan/zoom or crosshair synced to it), with its
own hover legend.

Charts render lazily on row-expand, same as v25's toggleExpand/_renderChartsForKey.
"""
import os
import sys
import json
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import lxpb_confluence as C  # noqa: E402 -- also sets up sys.path for ../lxpb.py
import lxpb_cache  # noqa: E402
import combine_and_scan as CS  # noqa: E402 -- reuse _window_start_ts burst-anchoring logic

CSV_1S = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers.csv")
OUT_HTML = os.path.join(_HERE, "lxpb_volume_strat_report.html")
TITLE = "LXPB + Volume-Absorption Strategy — 2026-08-13 08:00-09:30 PT (ES)"
# Ported from daily-analysis (was: r"D:\daily-analysis\data\ES1!-H1.csv").
H1_CSV = os.path.join(_HERE, "..", "data", "es-h1-4apr2021-11apr2025.csv")

WINDOW_S = 90          # seconds of context on each side of a trigger (1s panes)
N_TICKS = 20
STACK_THRESHOLD = 2
H1_CANDLES_BEFORE = 6  # "a few more candles" before the level's formation bar
H1_CANDLES_AFTER = 30  # future H1 bars past the retest, to see the outcome

BID_COLOR = "#f87171"
ASK_COLOR = "#4ade80"
LEVEL_COLOR = "#fcd34d"
# Candle colors matched to trapVariants/scans-tmp-files-etc/v25_2026.html's
# _renderOne (monochrome, not green/red).
CANDLE_UP = "#DDDDD0"
CANDLE_DOWN = "#888888"


def _to_epoch_utc(ts):
    """int unix seconds for either a tz-aware pandas Timestamp (converted
    as-is) or a naive Timestamp that already represents UTC wall-clock
    (H1 bar times from load_ohlc_data -- localize, don't convert)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _nearest_confluence_price(trig):
    conf_prices = [float(p) for p in str(trig.get("confluence_prices") or "").split(";") if p]
    if not conf_prices:
        return None
    tested = float(trig["tested_price"])
    return min(conf_prices, key=lambda p: abs(p - tested))


def _find_level_formation_time(intervals_df, level_type, price, ts_utc_naive):
    """Formation time of the specific confluence-set level nearest to the
    tested price, active at the trigger's timestamp (see
    lxpb_confluence.level_intervals_confluence_at for the same valid_from/
    consumed_at semantics)."""
    if price is None or intervals_df.empty:
        return None
    tol = 1e-6
    mask = (
        (intervals_df["type"] == level_type)
        & ((intervals_df["price"] - price).abs() <= tol)
        & (intervals_df["valid_from"] <= ts_utc_naive)
        & (intervals_df["consumed_at"].isna() | (intervals_df["consumed_at"] >= ts_utc_naive))
    )
    sub = intervals_df.loc[mask]
    return None if sub.empty else sub.iloc[0]["formation_time"]


def build_h1_context_data(h1_df, bar_times, intervals_df, trig, lookup_ts_utc_naive):
    """H1 candles from ~H1_CANDLES_BEFORE bars before the tested level's
    formation bar through the trigger's own H1 bar plus H1_CANDLES_AFTER
    future bars (clamped to available H1 history).

    `lookup_ts_utc_naive` is the SAME burst-window-anchored timestamp
    combine_and_scan.py used to validate this trigger's confluence (see
    combine_and_scan._window_start_ts) -- not simply the trigger's own
    timestamp. Using the trigger's own timestamp here would miss levels
    that were consumed by an EARLIER bar of the same multi-second burst
    (e.g. the 08:59:11 signal's 7798.00 level, consumed one second
    earlier at 08:59:10 by the same absorption event)."""
    ts = trig["time_pt"]
    level_type = trig["level_type"]
    nearest_price = _nearest_confluence_price(trig)
    formation_time = _find_level_formation_time(intervals_df, level_type, nearest_price, lookup_ts_utc_naive)

    bar_times_idx = pd.DatetimeIndex(bar_times)
    ts_utc_naive = ts.tz_convert("UTC").tz_localize(None)
    trig_pos = int(bar_times_idx.searchsorted(ts_utc_naive, side="right")) - 1
    if formation_time is not None:
        form_pos = int(bar_times_idx.searchsorted(pd.Timestamp(formation_time), side="right")) - 1
    else:
        form_pos = max(0, trig_pos - H1_CANDLES_BEFORE)

    lo = max(0, form_pos - H1_CANDLES_BEFORE)
    hi = min(len(h1_df) - 1, max(trig_pos, form_pos) + H1_CANDLES_AFTER)
    window = h1_df.iloc[lo:hi + 1]

    candles = [{
        "time": _to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in window.iterrows()]

    markers = []
    if formation_time is not None:
        markers.append({
            "time": _to_epoch_utc(formation_time),
            "position": "belowBar", "color": LEVEL_COLOR, "shape": "circle",
            "text": f"{level_type} FORM",
        })
    is_long = trig["direction"] == "LONG"
    if 0 <= trig_pos < len(h1_df):
        markers.append({
            "time": _to_epoch_utc(h1_df.index[trig_pos]),
            "position": "belowBar" if is_long else "aboveBar",
            "color": "#60a5fa" if is_long else "#fbbf24",
            "shape": "arrowUp" if is_long else "arrowDown",
            "text": "RETEST",
        })

    price_lines = []
    if nearest_price is not None:
        price_lines.append({
            "price": nearest_price, "color": LEVEL_COLOR, "lineWidth": 1,
            "lineStyle": 2, "title": f"{level_type} {nearest_price:.2f}",
        })

    form_str = (pd.Timestamp(formation_time).strftime("%Y-%m-%d %H:%M UTC")
                if formation_time is not None else "?")
    return {
        "title": f"H1 context  |  {level_type} formed {form_str}  ->  retest "
                  f"{ts.strftime('%Y-%m-%d %H:%M:%S')} PT",
        "candles": candles, "markers": markers, "priceLines": price_lines,
        "precision": 2,
    }


def build_chart_data(df1s, h1_df, bar_times_h1, intervals_df, trig, idx, lookup_ts_utc_naive):
    ts = trig["time_pt"]
    lo = ts - pd.Timedelta(seconds=WINDOW_S)
    hi = ts + pd.Timedelta(seconds=WINDOW_S)
    window = df1s.loc[(df1s.index >= lo) & (df1s.index <= hi)]

    candles = [{
        "time": int(t.timestamp()),
        "open": float(r.Open), "high": float(r.High),
        "low": float(r.Low), "close": float(r.Close),
    } for t, r in window.iterrows()]
    bid = [{"time": int(t.timestamp()), "value": float(r.BidVolume), "color": BID_COLOR}
           for t, r in window.iterrows()]
    ask = [{"time": int(t.timestamp()), "value": float(r.AskVolume), "color": ASK_COLOR}
           for t, r in window.iterrows()]

    is_long = trig["direction"] == "LONG"
    marker = {
        "time": int(ts.timestamp()),
        "position": "belowBar" if is_long else "aboveBar",
        "color": "#60a5fa" if is_long else "#fbbf24",
        "shape": "arrowUp" if is_long else "arrowDown",
        "text": "ABSORPTION",
    }

    conf_prices = [p for p in str(trig.get("confluence_prices") or "").split(";") if p]
    price_lines = [{
        "price": float(p), "color": LEVEL_COLOR, "lineWidth": 1,
        "lineStyle": 2,  # LightweightCharts.LineStyle.Dashed
        "title": f"{trig['level_type']} {float(p):.2f}",
    } for p in conf_prices]

    return {
        "title": f"#{idx}  {ts}  {trig['direction']}  |  confluence="
                  f"{trig['confluence_count']} ({trig['level_type']})",
        "candles": candles, "bid": bid, "ask": ask,
        "markers": [marker], "priceLines": price_lines,
        "precision": 2,
        "h1": build_h1_context_data(h1_df, bar_times_h1, intervals_df, trig, lookup_ts_utc_naive),
    }


CSS = """
<style>
:root { --bg:#111316; --surface:#1c1f24; --surface2:#22262d; --border:#2e333b;
        --text:#d4d8df; --text-dim:#6b7280; --text-faint:#444c58;
        --bull:#4ade80; --bear:#f87171; --accent:#60a5fa; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       padding:16px 24px; max-width:1700px; margin:0 auto;
       background:var(--bg); color:var(--text); }
h1 { font-size:1.4em; margin:0 0 4px; color:#e8eaed; }
p.lead { color:var(--text-dim); margin:0 0 12px; font-size:0.87em; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0; }
.summary .box { padding:8px 14px; background:var(--surface);
                border:1px solid var(--border); border-radius:6px;
                font-size:0.82em; line-height:1.3; }
.summary .box strong { display:block; font-size:1.4em; color:#e8eaed; }
.summary .box.true strong { color:var(--bull); }
.filter-panel { background:var(--surface); border:1px solid var(--border);
                border-radius:6px; padding:10px 14px; margin-bottom:14px; }
.filter-row { display:flex; align-items:center; gap:10px; margin:4px 0; }
.filter-label { color:var(--text-dim); width:80px; font-size:0.85em; }
.chip { background:var(--surface2); border:1px solid var(--border);
        border-radius:14px; padding:3px 10px; cursor:pointer; user-select:none;
        font-size:0.85em; }
.chip input { margin-right:4px; }
table { width:100%; border-collapse:collapse; font-size:0.82em;
        background:var(--surface); border:1px solid var(--border);
        border-radius:6px; overflow:hidden; table-layout:auto; }
th, td { border-bottom:1px solid var(--border); padding:6px 8px;
         text-align:right; white-space:nowrap; vertical-align:top; }
th { background:var(--surface2); font-weight:600; color:var(--text-dim);
     letter-spacing:.02em; font-size:0.85em; text-align:right; }
td.left, th.left { text-align:left; }
tr.trig-row:hover td { background:rgba(255,255,255,.03); cursor:pointer; }
tr.trig-row.dir-long td:nth-child(3)  { color:var(--bull); font-weight:600; }
tr.trig-row.dir-short td:nth-child(3) { color:var(--bear); font-weight:600; }
.expand-cell { text-align:center; }
.expand-btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
              border-radius:4px; padding:2px 8px; cursor:pointer; }
.expand-btn.open { background:#1e3a5f; color:#7bb4f5; border-color:#1d3a5c; }
tr.detail-row td.detail-cell { background:#0a0c0e; padding:10px 12px; border-top:none; }
.chart-stack { display:grid; grid-template-rows: 220px 320px 110px 110px; gap:8px; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px; overflow:hidden; }
.chart-title { color:#cccccc; padding:5px 8px; font-size:0.75em;
               font-family:ui-monospace,monospace; background:#0a0a0a;
               border-bottom:1px solid #1f1f1f; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 24px); width:100%; }
.hidden { display:none !important; }
</style>
"""


def build_page(triggers_df, title):
    n_long = int((triggers_df["direction"] == "LONG").sum())
    n_short = len(triggers_df) - n_long

    header = f"""
<h1>{title}</h1>
<p class="lead">Zone-first pipeline: absorption is only evaluated at prices sitting inside a
valid H1 LXPB stacked-level confluence zone (retest min-hours=2, stack threshold={STACK_THRESHOLD},
N={N_TICKS} ticks, levels consumed the instant 1s price touches them). Every row is a confirmed
signal. Click a row to expand/collapse its candles / bid volume / ask volume charts
(mouse wheel to zoom, drag to pan -- lightweight-charts default controls).</p>
<div class="summary">
  <div class="box true"><strong>{len(triggers_df)}</strong>Confirmed signals</div>
  <div class="box"><strong>{n_long}</strong>Long</div>
  <div class="box"><strong>{n_short}</strong>Short</div>
</div>
"""

    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Direction</span>
    <label class="chip"><input type="checkbox" class="f-cb f-dir" value="LONG" checked> LONG</label>
    <label class="chip"><input type="checkbox" class="f-cb f-dir" value="SHORT" checked> SHORT</label>
  </div>
</div>
"""

    thead = """
<table id="trig-table">
<thead><tr>
  <th class="left">#</th><th class="left">Time (PT)</th><th class="left">Dir</th>
  <th>Close</th><th>Tested Px</th><th>Volume</th><th>Delta</th><th>Vol_Z</th>
  <th>RunΔSum</th><th>Reject(ticks)</th><th class="left">Level</th>
  <th>Confl#</th><th class="left">Confluent Levels</th><th class="expand-th">▶</th>
</tr></thead>
<tbody>
"""

    rows_html = []
    for i, trig in triggers_df.reset_index(drop=True).iterrows():
        dir_cls = "dir-long" if trig["direction"] == "LONG" else "dir-short"
        rows_html.append(f"""
<tr class="trig-row {dir_cls}" data-idx="{i}" data-dir="{trig['direction']}" onclick="toggleChart({i})">
  <td class="left">{i}</td><td class="left">{trig['time_pt']}</td><td class="left">{trig['direction']}</td>
  <td>{trig['trigger_close']:.2f}</td><td>{trig['tested_price']:.2f}</td>
  <td>{int(trig['volume'])}</td><td>{int(trig['delta'])}</td><td>{trig['vol_z']:.2f}</td>
  <td>{trig['run_delta_sum']:.0f}</td><td>{trig['reject_ticks']:.1f}</td>
  <td class="left">{trig['level_type']}</td><td>{trig['confluence_count']}</td>
  <td class="left">{trig['confluence_prices']}</td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">▶</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" data-dir="{trig['direction']}" id="chart-row-{i}">
  <td colspan="14"><div class="chart-stack" data-cid="{i}">
    <div class="chart-cell"><div class="chart-title" id="th1-{i}"></div><div class="chart-ph" id="ch1-{i}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="tc-{i}"></div><div class="chart-ph" id="cc-{i}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="tb-{i}">Bid Volume</div><div class="chart-ph" id="cb-{i}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="ta-{i}">Ask Volume</div><div class="chart-ph" id="ca-{i}"></div></div>
  </div></td>
</tr>
""")

    tbody_close = "</tbody></table>"
    return header, filter_panel, thead, "".join(rows_html), tbody_close


JS_TEMPLATE = """
<script>
const CHARTS = __CHARTS_JSON__;
const rendered = {};
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });

// Shared chart look, matched to trapVariants/scans-tmp-files-etc/v25_2026.html's
// _renderOne: black bg, Courier New 9px, no gridlines, mode:0 crosshair.
function _baseOpts(tickFmt) {
  return {
    autoSize: true,
    layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
              fontFamily:"'Courier New', monospace", fontSize:9 },
    grid: { vertLines:{visible:false}, horzLines:{visible:false} },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.08, bottom:0.08} },
    timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:true,
      tickMarkFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
    localization: { timeFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
  };
}

function _addCandles(chart, precision) {
  return chart.addCandlestickSeries({
    upColor:'#DDDDD0', downColor:'#888888',
    borderUpColor:'#DDDDD0', borderDownColor:'#888888',
    wickUpColor:'#DDDDD0', wickDownColor:'#888888',
    priceFormat: { type:'price', precision: precision, minMove: 0.25 },
  });
}

function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  const chart = LightweightCharts.createChart(el, _baseOpts(timeFmtH1));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  const prec = cd.precision || 2;
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      titleEl.textContent = baseTitle + '  |  O ' + d.open.toFixed(prec)
        + '  H ' + d.high.toFixed(prec) + '  L ' + d.low.toFixed(prec)
        + '  C ' + d.close.toFixed(prec);
    } else { titleEl.textContent = baseTitle; }
  });
  chart.timeScale().fitContent();
}

// The 1s trio (candles/bid/ask) shares one x-axis: sync BOTH pan/zoom and
// crosshair position/hover-legend across all three, same spirit as v25's
// per-chart subscribeCrosshairMove legend, extended cross-pane.
function _renderTrio(i, cd) {
  const elC = document.getElementById('cc-' + i);
  const elB = document.getElementById('cb-' + i);
  const elA = document.getElementById('ca-' + i);
  const titleElC = document.getElementById('tc-' + i);
  const titleElB = document.getElementById('tb-' + i);
  const titleElA = document.getElementById('ta-' + i);
  const baseTitleC = cd.title;
  const baseTitleB = 'Bid Volume';
  const baseTitleA = 'Ask Volume';
  titleElC.textContent = baseTitleC;
  titleElB.textContent = baseTitleB;
  titleElA.textContent = baseTitleA;

  const chartC = LightweightCharts.createChart(elC, _baseOpts(timeFmt));
  const seriesC = _addCandles(chartC, cd.precision);
  seriesC.setData(cd.candles);
  if (cd.markers && cd.markers.length) seriesC.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => seriesC.createPriceLine(pl));

  const chartB = LightweightCharts.createChart(elB, _baseOpts(timeFmt));
  const seriesB = chartB.addHistogramSeries({ priceFormat:{type:'volume'} });
  seriesB.setData(cd.bid);

  const chartA = LightweightCharts.createChart(elA, _baseOpts(timeFmt));
  const seriesA = chartA.addHistogramSeries({ priceFormat:{type:'volume'} });
  seriesA.setData(cd.ask);

  const cMap = {}; (cd.candles || []).forEach(b => cMap[b.time] = b);
  const bMap = {}; (cd.bid || []).forEach(b => bMap[b.time] = b.value);
  const aMap = {}; (cd.ask || []).forEach(b => aMap[b.time] = b.value);
  const prec = cd.precision || 2;

  const panes = [
    { chart: chartC, series: seriesC, titleEl: titleElC, base: baseTitleC },
    { chart: chartB, series: seriesB, titleEl: titleElB, base: baseTitleB },
    { chart: chartA, series: seriesA, titleEl: titleElA, base: baseTitleA },
  ];

  function updateLegends(time) {
    const c = time != null ? cMap[time] : null;
    titleElC.textContent = baseTitleC + (c ? ('  |  O ' + c.open.toFixed(prec)
      + '  H ' + c.high.toFixed(prec) + '  L ' + c.low.toFixed(prec)
      + '  C ' + c.close.toFixed(prec)) : '');
    const b = time != null ? bMap[time] : null;
    titleElB.textContent = baseTitleB + (b != null ? ('  |  ' + b) : '');
    const a = time != null ? aMap[time] : null;
    titleElA.textContent = baseTitleA + (a != null ? ('  |  ' + a) : '');
  }

  // Crosshair sync: hovering one pane sets the same time's crosshair on
  // the other two (lightweight-charts setCrosshairPosition/clearCrosshairPosition)
  // and updates all three legends together.
  let syncingCH = false;
  panes.forEach((p, idx) => {
    p.chart.subscribeCrosshairMove((param) => {
      if (syncingCH) return;
      syncingCH = true;
      const time = (param && param.time != null) ? param.time : null;
      updateLegends(time);
      panes.forEach((other, j) => {
        if (j === idx) return;
        if (time == null) { other.chart.clearCrosshairPosition(); return; }
        let val = null;
        if (other.series === seriesC) val = cMap[time] ? cMap[time].close : null;
        else if (other.series === seriesB) val = bMap[time];
        else val = aMap[time];
        if (val != null) other.chart.setCrosshairPosition(val, time, other.series);
        else other.chart.clearCrosshairPosition();
      });
      syncingCH = false;
    });
  });

  // Sync pan/zoom across the 3 stacked panes (candles/bid/ask share one x-axis).
  let syncingRange = false;
  panes.forEach((p, idx) => {
    p.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncingRange) return;
      syncingRange = true;
      panes.forEach((other, j) => { if (j !== idx) other.chart.timeScale().setVisibleLogicalRange(range); });
      syncingRange = false;
    });
  });
  chartC.timeScale().fitContent();
}

function _renderStack(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  if (cd.h1) _renderH1(i, cd.h1);
  _renderTrio(i, cd);
}

function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.textContent = opening ? '▼' : '▶'; }
  if (opening && !rendered[i]) { _renderStack(i); rendered[i] = true; }
}

function applyFilters() {
  const dirOn = Array.from(document.querySelectorAll('.f-dir:checked')).map(c => c.value);
  document.querySelectorAll('.trig-row').forEach(function(tr) {
    const show = dirOn.includes(tr.dataset.dir);
    tr.classList.toggle('hidden', !show);
    if (!show) {
      const cr = document.getElementById('chart-row-' + tr.dataset.idx);
      if (cr) cr.classList.add('hidden');
    }
  });
}
document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));
</script>
"""


def render(csv_1s_path=CSV_1S, triggers_csv_path=TRIGGERS_CSV, out_html_path=OUT_HTML, title=TITLE):
    df1s = pd.read_csv(csv_1s_path, index_col="Time_PT", parse_dates=True)
    triggers_df = pd.read_csv(triggers_csv_path, parse_dates=["time_pt"])

    # Reuses the same disk cache as combine_and_scan.py (lxpb_cache.py) --
    # H1 snapshots + 1s level-consumption intervals recompute nothing if
    # this exact (1s CSV, H1 CSV) pair was already scanned.
    intervals_df, _retests_df, _snapshots, bar_times_h1 = lxpb_cache.get_level_intervals(
        df1s, csv_1s_path, H1_CSV, verbose=False)
    h1_df = C.load_ohlc_data(H1_CSV)

    header, filter_panel, thead, rows, tbody_close = build_page(triggers_df, title)

    # Burst-window-anchored lookup timestamp per trigger, identical to what
    # combine_and_scan.py used to validate this exact signal's confluence
    # (see combine_and_scan._window_start_ts) -- reused here so the H1
    # context chart's "level formation" lookup credits a level consumed by
    # an EARLIER bar of the same absorption burst, same as the scan itself.
    idx_list = df1s.index
    pos_by_ts = {ts: i for i, ts in enumerate(idx_list)}
    chart_map = {}
    for i, trig in triggers_df.reset_index(drop=True).iterrows():
        pos = pos_by_ts.get(trig["time_pt"])
        if pos is not None:
            lookup_ts = CS._window_start_ts(idx_list, pos, bar_times_h1)
            lookup_ts_utc_naive = lookup_ts.tz_convert("UTC").tz_localize(None)
        else:
            lookup_ts_utc_naive = trig["time_pt"].tz_convert("UTC").tz_localize(None)
        chart_map[i] = build_chart_data(df1s, h1_df, bar_times_h1, intervals_df, trig, i, lookup_ts_utc_naive)

    # Escape "</" so embedded JSON (e.g. any stray "</script>"-like text in
    # titles) can't prematurely close this <script> block.
    charts_json = json.dumps(chart_map).replace("</", "<\\/")
    js = JS_TEMPLATE.replace("__CHARTS_JSON__", charts_json)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
{CSS}
</head><body>
{header}
{filter_panel}
{thead}
{rows}
{tbody_close}
{js}
</body></html>
"""
    with open(out_html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {len(triggers_df)} triggers -> {out_html_path}")


if __name__ == "__main__":
    render()
