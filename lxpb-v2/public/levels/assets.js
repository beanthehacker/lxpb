const rendered = {};
const FIXED_BAR_SPACING = 6;
const RAY_HOVER_PTS = 1.5;   // vertical tolerance (points) for hovering a level ray
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
    // Wheel over a chart scrolls the page, not the chart; drag-pan, axis-drag
    // rescale and pinch-zoom stay.
    handleScroll: { mouseWheel:false, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false },
    handleScale: { mouseWheel:false, axisPressedMouseMove:{time:true, price:true},
                   axisDoubleClickReset:true, pinch:true },
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
  _renderPane('ch1-' + i, 'th1-' + i, cd);
}
function _renderM5(i, cd) {
  _renderPane('cm5-' + i, 'tm5-' + i, cd);
}
function _renderPane(elId, titleId, cd) {
  const el = document.getElementById(elId);
  const titleEl = document.getElementById(titleId);
  if (!el || !titleEl) return;
  const baseTitle = cd.title;
  // Split the legend: the descriptive part truncates, the OHLC readout is
  // pinned right and never clipped. These panes are half-width now, so a
  // single nowrap+ellipsis line would cut the OHLC off on hover.
  titleEl.classList.add('chart-title-split');
  titleEl.textContent = '';
  const baseEl = document.createElement('span');
  baseEl.className = 'ct-base';
  baseEl.textContent = baseTitle;
  baseEl.title = baseTitle;
  const ohlcEl = document.createElement('span');
  ohlcEl.className = 'ct-ohlc';
  titleEl.appendChild(baseEl);
  titleEl.appendChild(ohlcEl);
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false, width: el.clientWidth || 800, height: el.clientHeight || 320,
  }));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  // Level rays: flat line segments spanning formation -> retest. They carry
  // no drawn label (a chart with several of them turns into unreadable
  // overlapping text) -- the details go in the hover tooltip below.
  const rayInfo = [];
  (cd.rays || []).forEach(r => {
    const rs = chart.addLineSeries({
      color: r.color, lineWidth: r.lineWidth || 1,
      lineStyle: (r.lineStyle == null ? 0 : r.lineStyle),
      priceLineVisible: false, lastValueVisible: !!r.priceLabel,
      title: r.title || '',
      crosshairMarkerVisible: false, pointMarkersVisible: false,
    });
    rs.setData(r.points);
    rayInfo.push({ series: rs, label: r.label });
  });
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
    // Only the ray(s) actually under the cursor -- a ray has a point on every
    // bar it spans, so "has a value at this time" means the cursor is inside
    // its formation..retest span; the price test picks the one being pointed at.
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
