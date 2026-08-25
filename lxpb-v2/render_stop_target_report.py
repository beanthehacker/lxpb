"""
Per-trade H1 chart report for a single stop/target combination, styled like
lxpb_labels_report.html (same dark theme, expandable-row table, H1
candlestick chart with lightweight-charts) but showing trade OUTCOME
instead of hand-labeling checkboxes.

Uses the exact same 99-trade "strong breakout" sample and 1-minute-resolved
exit logic as analyze_breakout_exits_1min.py / exit_analysis_report.html
(escalating to real 1s ticks only for the rare ambiguous minute where a
single 1-min bar's own H/L range covers BOTH stop and target) -- so a
trade's outcome here always matches its row in the "1-MINUTE-RESOLVED" grid
in exit_analysis_report.html for the same stop/target.

Each row's H1 chart spans the same formation/breakout/retest context
windows as render_labels_report.py's build_row_chart, extended forward to
also include the exit bar, with:
  - gold line  = the LXPB level itself (entry price)
  - green line = target price
  - red line   = stop price
  - P0/P1/P2 markers (formation/breakout/retest, i.e. entry) exactly as in
    the labels report, plus an EXIT marker (green up-arrow = target hit /
    win, red down-arrow = stop hit / loss) at the resolved exit bar.

Usage:
    python render_stop_target_report.py --stop 2 --target 8
    python render_stop_target_report.py --stop 2 --target 8 --output my_report.html
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_labels_report as R  # noqa: E402
import analyze_breakout_exits as A  # noqa: E402
import analyze_breakout_exits_1min as M  # noqa: E402

DEFAULT_STOP = 2.0
DEFAULT_TARGET = 8.0

BARS_BEFORE = 8
CONTEXT_BARS_AFTER_FORMATION = 3
CONTEXT_BARS_BEFORE_BREAKOUT = 3
CONTEXT_BARS_AFTER_BREAKOUT = 6
CONTEXT_BARS_BEFORE_RETEST = 6
BARS_AFTER_MIN = 8       # same floor as render_labels_report's BARS_AFTER
BARS_AFTER_EXIT = 3      # extra bars of context shown past the exit bar
MAX_MERGE_GAP = 15

EXIT_WIN_COLOR = "#4ade80"
EXIT_LOSS_COLOR = "#f87171"


def resolve_trades(trades, series_by_idx, stop, target):
    """Per-trade version of analyze_breakout_exits_1min.stop_target_grid_1min
    -- same exact walk-forward/ambiguous-minute-escalation logic, but
    returns full per-trade detail (outcome, R, exit bar time) instead of
    only aggregate grid stats."""
    out = []
    for i, t in enumerate(trades):
        bars = series_by_idx.get(i)
        entry_adj = t["entry"]
        is_long = t["is_long"]
        if bars is None or bars.empty:
            out.append({"outcome": "no_data", "r": None, "exit_time": None})
            continue
        offset, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
        raw_entry = entry_adj - offset
        highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
        if is_long:
            stop_price, target_price = raw_entry - stop, raw_entry + target
            stop_hit = lows <= stop_price
            target_hit = highs >= target_price
        else:
            stop_price, target_price = raw_entry + stop, raw_entry - target
            stop_hit = highs >= stop_price
            target_hit = lows <= target_price
        s_idx = np.flatnonzero(stop_hit)
        tg_idx = np.flatnonzero(target_hit)
        hs = s_idx[0] if s_idx.size else None
        ht = tg_idx[0] if tg_idx.size else None

        if hs is None and ht is None:
            last_time = bars.index[-1]
            out.append({
                "outcome": "no_hit", "r": None, "exit_time": last_time,
                "exit_price": entry_adj,
            })
            continue

        if hs is not None and ht is not None and hs == ht:
            minute_start = bars.index[hs]
            resolved = M._resolve_ambiguous_minute(sym, minute_start, offset, entry_adj,
                                                    stop, target, is_long)
            outcome = resolved if resolved is not None else "target"  # old optimistic tie-break
            exit_idx = hs
        elif hs is not None and (ht is None or hs < ht):
            outcome, exit_idx = "stop", hs
        else:
            outcome, exit_idx = "target", ht

        exit_time = bars.index[exit_idx]
        if outcome == "target":
            r = target / stop
            exit_price_adj = entry_adj + target if is_long else entry_adj - target
        else:
            r = -1.0
            exit_price_adj = entry_adj - stop if is_long else entry_adj + stop
        out.append({"outcome": outcome, "r": r, "exit_time": exit_time, "exit_price": exit_price_adj})
    return out


def build_trade_chart(h1_df, pos_by_ts, row, trade, resolved, stop, target):
    level_type = row["type"]
    price = float(row["price"])
    is_long = level_type == "LHPB"
    form_pos = pos_by_ts[row["formation_time"]]
    breakout_pos = pos_by_ts[row["breakout_time"]]
    retest_pos = pos_by_ts[row["retest_time"]]
    n_bars = len(h1_df)

    exit_time = resolved["exit_time"]
    if exit_time is not None:
        # h1_df's index is tz-naive (epoch-seconds -> naive UTC, see
        # lxpb.load_ohlc_data), but exit_time comes from the 1-min tick
        # series (tz-aware UTC, see render_labels_report._ticks_for_window)
        # -- strip the tz label (same instant, just re-represented) before
        # comparing against h1_df's naive index.
        exit_time_naive = exit_time.tz_localize(None) if exit_time.tzinfo is not None else exit_time
        exit_pos = min(int(h1_df.index.searchsorted(exit_time_naive, side="right")) - 1, n_bars - 1)
        exit_pos = max(exit_pos, retest_pos)
    else:
        exit_pos = retest_pos

    retest_after = max(BARS_AFTER_MIN, (exit_pos - retest_pos) + BARS_AFTER_EXIT)
    segments = [
        (form_pos - BARS_BEFORE, form_pos + CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + CONTEXT_BARS_AFTER_BREAKOUT),
        (retest_pos - CONTEXT_BARS_BEFORE_RETEST, retest_pos + retest_after),
    ]
    merged = R._merge_segments(segments, n_bars, MAX_MERGE_GAP)

    parts, skip_markers = [], []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": R._to_epoch_utc(h1_df.index[s]),
                "position": "inBar", "color": "#9ca3af", "shape": "square",
                "text": f"[{gap} bars skipped]",
            })
        parts.append(h1_df.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)

    candles = [{
        "time": R._to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in window.iterrows()]

    outcome = resolved["outcome"]
    win = outcome == "target"
    exit_marker_time = h1_df.index[exit_pos]
    if outcome == "target":
        exit_text = f"WIN +{target/stop:.2f}R"
        exit_color, exit_shape = EXIT_WIN_COLOR, ("arrowUp" if is_long else "arrowDown")
        exit_pos_label = "aboveBar" if is_long else "belowBar"
    elif outcome == "stop":
        exit_text = "LOSS -1.00R"
        exit_color, exit_shape = EXIT_LOSS_COLOR, ("arrowDown" if is_long else "arrowUp")
        exit_pos_label = "belowBar" if is_long else "aboveBar"
    else:
        exit_text = "NO-HIT" if outcome == "no_hit" else "NO DATA"
        exit_color, exit_shape = "#9ca3af", "circle"
        exit_pos_label = "inBar"
    markers = [
        {"time": R._to_epoch_utc(row["formation_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P0_COLOR, "shape": "circle", "text": "P0 form"},
        {"time": R._to_epoch_utc(row["breakout_time"]),
         "position": "belowBar" if is_long else "aboveBar",
         "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
         "shape": "arrowUp" if is_long else "arrowDown", "text": "P1 breakout"},
        {"time": R._to_epoch_utc(row["retest_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P2_COLOR, "shape": "circle", "text": "P2 retest (entry)"},
        {"time": R._to_epoch_utc(exit_marker_time),
         "position": exit_pos_label, "color": exit_color, "shape": exit_shape, "text": exit_text},
    ] + skip_markers
    markers.sort(key=lambda m: m["time"])

    target_price = price + target if is_long else price - target
    stop_price = price - stop if is_long else price + stop
    price_lines = [
        {"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
         "title": f"{level_type} {price:.2f} (entry)"},
        {"price": target_price, "color": EXIT_WIN_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"target {target_price:.2f} (+{target:.0f}pt)"},
        {"price": stop_price, "color": EXIT_LOSS_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"stop {stop_price:.2f} (-{stop:.0f}pt)"},
    ]

    title = (f"{level_type} {price:.2f}  |  formed {R._to_pt_str(row['formation_time'])}  "
             f"broke {R._to_pt_str(row['breakout_time'])}  retest {R._to_pt_str(row['retest_time'])}  "
             f"exit {R._to_pt_str(exit_marker_time)}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"

    return {"title": title, "candles": candles, "markers": markers,
            "priceLines": price_lines, "precision": 2}


CSS = R.CSS + """
<style>
td.good { color:#4ade80; }
td.bad { color:#f87171; }
.table-wrap { max-height:none; }
.expand-th { width:28px; }
</style>
"""

JS = """
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const CHARTS = __CHARTS_JSON__;
const rendered = {};
const FIXED_BAR_SPACING = 6;
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });

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
function _centerLogicalRange(chart, el, nBars) {
  if (!nBars) return;
  const barsVisible = Math.max(1, el.clientWidth / FIXED_BAR_SPACING);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}
function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  if (!el || !titleEl) return;
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false, width: el.clientWidth || 800, height: el.clientHeight || 320,
  }));
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
  chart.timeScale().applyOptions({ barSpacing: FIXED_BAR_SPACING });
  _centerLogicalRange(chart, el, cd.candles.length);
  const ro = new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) {
      chart.resize(r.width, r.height);
      chart.timeScale().applyOptions({ barSpacing: FIXED_BAR_SPACING });
      _centerLogicalRange(chart, el, cd.candles.length);
    }
  });
  ro.observe(el);
}
function _renderTrio(i, cd) {
  const elC = document.getElementById('cc-' + i);
  const elB = document.getElementById('cb-' + i);
  const elA = document.getElementById('ca-' + i);
  const titleElC = document.getElementById('tc-' + i);
  const titleElB = document.getElementById('tb-' + i);
  const titleElA = document.getElementById('ta-' + i);
  if (!elC || !elB || !elA) return;
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
function _renderOneMin(i, cd) {
  const el = document.getElementById('c1m-' + i);
  const titleEl = document.getElementById('t1m-' + i);
  if (!el || !titleEl) return;
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
function _renderStack(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  if (cd.h1) _renderH1(i, cd.h1);
  if (cd.trio) _renderTrio(i, cd.trio);
  if (cd.oneMin) _renderOneMin(i, cd.oneMin);
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
</script>
"""


def render(stop, target, output_path):
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    trades = A.simulate(h1_df, pos_by_ts, strong)
    strong = strong.reset_index(drop=True)
    assert len(strong) == len(trades), (
        f"strong ({len(strong)}) / trades ({len(trades)}) count mismatch -- "
        "simulate() must have dropped a row (near end of data); positional "
        "alignment with `strong` below would be wrong.")

    series_by_idx = M.build_or_load_1min_series(trades)
    resolved_list = resolve_trades(trades, series_by_idx, stop, target)

    wins = sum(1 for r in resolved_list if r["outcome"] == "target")
    losses = sum(1 for r in resolved_list if r["outcome"] == "stop")
    no_hits = sum(1 for r in resolved_list if r["outcome"] == "no_hit")
    r_values = [r["r"] for r in resolved_list if r["r"] is not None]
    win_rate = wins / len(r_values) if r_values else 0.0
    avg_r = float(np.mean(r_values)) if r_values else 0.0
    total_r = float(np.sum(r_values)) if r_values else 0.0

    charts, rows_html = [], []
    n_trades = len(trades)
    for i, (trade, resolved) in enumerate(zip(trades, resolved_list)):
        row_d = strong.iloc[i]
        chart = build_trade_chart(h1_df, pos_by_ts, row_d, trade, resolved, stop, target)

        level_type = row_d["type"]
        price = float(row_d["price"])
        is_long = level_type == "LHPB"
        target_price = price + target if is_long else price - target
        stop_price = price - stop if is_long else price + stop

        # Real-tick 1s/1min/footprint charts, same real-tick machinery as
        # lxpb_labels_report.html. build_1s_trio_chart reads "fta"/"stop_loss"
        # as its target/stop price lines -- override those two fields (in
        # entry_price's own adjusted scale, not the level's "price") with
        # THIS combo's stop/target so the extra charts show the same
        # stop=2/target=8-style bracket as the H1 chart, not lxpb.py's
        # original fta/stop_loss.
        entry_price_adj = float(row_d["entry_price"])
        row_for_trio = row_d.copy()
        row_for_trio["fta"] = entry_price_adj + target if is_long else entry_price_adj - target
        row_for_trio["stop_loss"] = entry_price_adj - stop if is_long else entry_price_adj + stop
        trio_chart = R.build_1s_trio_chart(row_for_trio, R.PAD_SECONDS_DEFAULT,
                                            R.ONE_MIN_PAD_MINUTES_DEFAULT, True)
        if trio_chart is not None:
            chart_stack = {"h1": chart, "trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
            fp = {"narrow": trio_chart.get("footprintNarrowHtml"), "wide": trio_chart.get("footprintWideHtml")}
        else:
            chart_stack = {"h1": chart, "trio": None, "oneMin": None}
            fp = {"narrow": "<p class='note'>(no tick data in this window)</p>",
                  "wide": "<p class='note'>(no tick data in this window)</p>"}
        charts.append(chart_stack)
        if (i + 1) % 10 == 0 or (i + 1) == n_trades:
            print(f"  built charts for {i + 1}/{n_trades} rows")
        outcome = resolved["outcome"]
        r_val = resolved["r"]
        outcome_cls = "good" if outcome == "target" else ("bad" if outcome == "stop" else "")
        outcome_label = {"target": "WIN", "stop": "LOSS", "no_hit": "NO-HIT", "no_data": "NO DATA"}[outcome]
        r_str = f"{r_val:+.2f}" if r_val is not None else "-"
        exit_str = R._to_pt_str(resolved["exit_time"]) if resolved["exit_time"] is not None else "-"
        type_cls = "type-lhpb" if is_long else "type-llpb"

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
<tr class="lvl-row {type_cls}" data-idx="{i}" onclick="toggleChart({i})">
  <td class="left">{i}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{R._to_pt_str(row_d['retest_time'])}</td>
  <td>{price:.2f}</td><td>{stop_price:.2f}</td><td>{target_price:.2f}</td>
  <td class="{outcome_cls}">{outcome_label}</td><td class="{outcome_cls}">{r_str}</td>
  <td class="left">{exit_str}</td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}"
      onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="10"><div class="chart-stack">
    <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{i}"></div><div class="chart-ph" id="ch1-{i}"></div></div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{i}"></div><div class="chart-ph" id="cc-{i}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{i}">Bid Volume</div><div class="chart-ph" id="cb-{i}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{i}">Ask Volume</div><div class="chart-ph" id="ca-{i}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{i}"></div><div class="chart-ph" id="c1m-{i}"></div></div>
      </div>
    </div>
    {fp_section}
  </div></td>
</tr>
""")

    header = f"""
<h1>LXPB Strong-Breakout Trades &mdash; Stop {stop:.0f} / Target {target:.0f}</h1>
<p class="lead">Same {len(trades)} "strong breakout" LXPB retests as exit_analysis_report.html's
1-minute-resolved grid, walked forward with a fixed stop={stop:.0f}pt / target={target:.0f}pt bracket
(real 1-minute bars from local .scid ticks, escalating to real 1-second ticks only when a single
minute's own H/L range covers BOTH levels at once). Entry = the level's own retest price (P2, no
slippage modeled). Click a row to expand its H1 chart: gold line = entry level, green dashed =
target, red dashed = stop; P0/P1/P2 markers mark formation/breakout/retest, and a 4th green/red
arrow marks the resolved exit bar.</p>
<div class="summary">
  <div class="box"><strong>{len(trades)}</strong>trades</div>
  <div class="box"><strong>{wins}</strong>wins</div>
  <div class="box"><strong>{losses}</strong>losses</div>
  <div class="box"><strong>{no_hits}</strong>no-hit</div>
  <div class="box"><strong>{win_rate*100:.1f}%</strong>win rate</div>
  <div class="box"><strong>{avg_r:.2f}</strong>avg R</div>
  <div class="box"><strong>{total_r:.1f}</strong>total R</div>
</div>
"""
    thead = """
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Type</th><th class="left">Retest (entry) time</th>
  <th>Entry</th><th>Stop</th><th>Target</th><th>Outcome</th><th>R</th>
  <th class="left">Exit time</th><th class="expand-th">\u25b6</th>
</tr></thead>
<tbody>
"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>LXPB Stop {stop:.0f} / Target {target:.0f} Trades</title>
{CSS}
</head><body>
{header}
<div class="table-wrap">{thead}
{''.join(rows_html)}
</tbody></table></div>
{JS.replace("__CHARTS_JSON__", json.dumps(charts))}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}  ({len(trades)} trades, {wins}W/{losses}L/{no_hits}NH, "
          f"win rate {win_rate*100:.1f}%, avg_R {avg_r:.2f}, total_R {total_r:.1f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-trade H1 chart report for one stop/target combo")
    parser.add_argument("--stop", type=float, default=DEFAULT_STOP)
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    out = args.output or os.path.join(_HERE, f"stop{args.stop:.0f}_target{args.target:.0f}_trades_report.html")
    render(args.stop, args.target, out)
