"""HTML report of recent ranges from patterns_pure/find_trading_range (ES M5, PT).

One card per range, most recent first, plus a card for the range forming at
the last bar (a live candidate, if any). Each card: candles around the range,
the box from its start to its breakout, dotted lines at start / confirmed /
breakout, squares on the touches that confirmed it (T top, B bottom, ½ =
half swing), dashed blue outlines for the boxes a merged range is made of.

    python range_pure_report.py <bars.csv> <out.html> [--sessions N] [--lower <m1.csv>] ['{"param": value}']

<bars.csv> (and the --lower M1 bars that grade breakouts strong / weak) from
scid_bars.py. The last bar is dropped (it may still be forming).
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "patterns_pure"))
import find_trading_range as ftr  # noqa: E402

PT = "America/Los_Angeles"
YELLOW, ORANGE, BLUE, GREY = "#FFD166", "#FF9F1C", "#5AA9FF", "#AAAAAA"
BEFORE, AFTER = 48, 36  # context bars around a range


def _ts(t):
    return int(pd.Timestamp(t).timestamp())


def _hm(t):
    return f"{pd.Timestamp(t).tz_convert(PT):%m/%d %H:%M}"


def _marks(touches, color):
    out, nt, nb = [], 0, 0
    for k, (t, edge, half) in enumerate(touches, 1):
        out.append(dict(time=_ts(t), kind="crest" if edge == "top" else "trough",
                        text=f"{'T' if edge == 'top' else 'B'}{k}{'½' if half else ''}"))
    return out


def _candles(bars, i0, i1):
    b = bars.iloc[max(0, i0):i1 + 1]
    return [dict(time=_ts(t), open=o, high=h, low=l, close=c)
            for t, o, h, l, c in zip(b.index, b.Open, b.High, b.Low, b.Close)]


def range_card(bars, r, p):
    idx = bars.index
    i0 = idx.get_loc(r.start_time) - BEFORE
    i1 = idx.get_loc(r.break_confirm_time if r.status == "broken" else idx[-1]) + AFTER
    n_half = sum(1 for _, _, h in r.confirm_touches if h)
    tops = sum(1 for _, e, _ in r.confirm_touches if e == "top")
    bots = len(r.confirm_touches) - tops
    sub = [f"confirmed {_hm(r.confirm_time)} on touch {len(r.confirm_touches)} "
           f"({tops} top / {bots} bottom{f', {n_half} half' if n_half else ''}), "
           f"box then {r.confirm_low:.2f}-{r.confirm_high:.2f}",
           f"{r.n_bars} bars, {r.height_tr:.1f}x mean bar range, drift {r.drift:.2f}"]
    if r.merged:
        sub.append(f"{r.merged + 1} boxes merged")
    vlines = [dict(time=_ts(r.start_time), color=YELLOW, label=f"start {_hm(r.start_time)[6:]}"),
              dict(time=_ts(r.confirm_time), color=ORANGE,
                   label=f"confirmed {_hm(r.confirm_time)[6:]}")]
    if r.status == "broken":
        grade = f" {r.break_grade}" if r.break_grade else ""
        what = "failed breakout" if getattr(r, "break_type", None) == "failed" else "broken"
        sub.append(f"{what} {r.break_dir}{grade} {_hm(r.break_confirm_time)}"
                   + (f" (first close out {_hm(r.break_time)})"
                      if r.break_time != r.break_confirm_time else ""))
        vlines.append(dict(time=_ts(r.break_confirm_time), color=YELLOW,
                           label=f"{'failed' if what != 'broken' else 'broke'} {r.break_dir}{grade} "
                                 f"{_hm(r.break_confirm_time)[6:]}"))
    else:
        sub.append("ACTIVE (not broken at the last bar)")
    draws = [dict(name="range", color=YELLOW, hi=r.high, lo=r.low, t0=_ts(r.start_time),
                  t1=_ts(r.break_time) if r.status == "broken" else None, faint=False,
                  vlines=vlines, pivots=_marks(r.confirm_touches, YELLOW))]
    for k, m in enumerate(r.members):
        draws.append(dict(name=chr(65 + k), color=BLUE, hi=m["high"], lo=m["low"],
                          t0=_ts(m["start_time"]), t1=_ts(m["end_time"]), faint=True,
                          vlines=[], pivots=[]))
    return dict(section="ranges", tf="M5",
                title=f"Range #{r.id}: {r.low:.2f} - {r.high:.2f} ({r.height:.2f} pt), "
                      f"from {_hm(r.start_time)} PT",
                sub=", ".join(sub), candles=_candles(bars, i0, i1), draws=draws)


def candidate_card(bars, c, p):
    idx = bars.index
    s, j = idx.get_loc(c.start_time), len(idx) - 1
    th, tl, marks = ftr._touches(bars.High.values[s:j + 1], bars.Low.values[s:j + 1], c.high,
                                 c.low, p["touch_frac"], p["max_half_touches"], where=True)
    touches = [(idx[s + i], e, h) for i, e, h in marks]
    need = (f"needs {p['min_total_touches']} touches with {p['min_touches']}+ per edge; "
            f"has {th} top / {tl} bottom")
    draws = [dict(name="candidate", color=GREY, hi=c.high, lo=c.low, t0=_ts(c.start_time),
                  t1=None, faint=False, pivots=_marks(touches, GREY),
                  vlines=[dict(time=_ts(c.start_time), color=GREY,
                               label=f"start {_hm(c.start_time)[6:]}")])]
    return dict(section="live", tf="M5",
                title=f"Forming (candidate, not a range yet): {c.low:.2f} - {c.high:.2f} "
                      f"({c.high - c.low:.2f} pt), from {_hm(c.start_time)} PT",
                sub=f"seen since {_hm(c.first_seen)}, {need}",
                candles=_candles(bars, s - BEFORE, j), draws=draws)


def main():
    args = sys.argv[1:]
    bars = pd.read_csv(args[0], index_col=0, parse_dates=True)
    bars.index = pd.DatetimeIndex(bars.index).tz_convert(PT)
    bars = bars.iloc[:-1]
    step = bars.index.to_series().diff().median()
    lower = None
    if "--lower" in args:
        lower = pd.read_csv(args[args.index("--lower") + 1], index_col=0, parse_dates=True)
        lower.index = pd.DatetimeIndex(lower.index).tz_convert(PT)
        lower = lower[lower.index < bars.index[-1] + step]
    out = args[1]
    n_sess = int(args[args.index("--sessions") + 1]) if "--sessions" in args else 5
    params = next((json.loads(a) for a in args if a.startswith("{")), {})
    p = {**ftr.DEFAULTS, **params}
    res = ftr.find_trading_range(bars, return_all=True, lower_data=lower, **params)
    ranges, cands = res["ranges"], res["candidates"]

    day = (bars.index + pd.Timedelta(hours=9)).normalize()
    first = bars.index[day >= sorted(set(day))[-n_sess]][0]
    recent = ranges[ranges.end_time >= first].iloc[::-1]
    cards = [range_card(bars, r, p) for r in recent.itertuples()]
    live = cands[cands.outcome == "live"]
    if len(live):
        cards.insert(0, candidate_card(bars, live.iloc[-1], p))

    asof = f"{bars.index[-1]:%a %m/%d %H:%M}"
    shown = ", ".join(f"{k} {v}" for k, v in p.items()) + (
        "; breakouts graded strong / weak on M1 (find_range's grader)" if lower is not None
        else "; no lower data: break_tr / confirm_bars rule")
    html = TEMPLATE.replace("__CARDS__", json.dumps(cards)).replace("__ASOF__", asof) \
        .replace("__PARAMS__", shown).replace("__FROM__", f"{first:%a %m/%d %H:%M}") \
        .replace("__N__", str(len(recent)))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{len(recent)} ranges since {first}, live candidate: {bool(len(live))} -> {out}")


TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recent trading ranges</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
body { background:#000; color:#ccc; font-family:'Courier New', monospace; margin:8px; font-size:13px; }
h1 { font-size:16px; margin:4px 0 2px; } h2 { font-size:15px; margin:22px 0 6px; border-bottom:1px solid #333; }
.note { color:#999; margin:2px 0 8px; max-width:1400px; }
.card { margin:0 0 18px; }
.ct { color:#eee; font-weight:bold; margin:2px 0; } .cs { color:#999; margin:0 0 4px; }
.pane { position:relative; width:100%; height:clamp(340px, max(55vh, 0.3 * 100vw), 700px); border:1px solid #222; }
.pane canvas.ov { position:absolute; left:0; top:0; pointer-events:none; z-index:3; }
.ohlc { position:absolute; left:6px; top:4px; z-index:4; color:#ccc; font-size:12px; pointer-events:none; }
</style></head><body>
<h1>Recent M5 trading ranges (find_trading_range, bar by bar, no look-ahead), closed bars to __ASOF__ PT</h1>
<div class="note">__N__ ranges ending since __FROM__ PT, most recent first. Yellow = the range: high / low rays, shaded
from its start to its breakout. Dotted lines: start, <span style="color:#FF9F1C">confirmed</span> (the bar it became
known as a range), breakout. Squares = the edge touches that confirmed it, in order (T top, B bottom, ½ = half swing:
price only reached the midline before retesting the same edge). Dashed blue = the boxes a merged range is made of.
Grey = a range still forming at the last bar. Drag to pan, drag an axis to rescale, double-click an axis to reset. Times PT.<br>
Parameters: __PARAMS__</div>
<div id="live"></div>
<h2>Confirmed ranges</h2><div id="ranges"></div>
<script>
const CARDS = __CARDS__;
const PT_TZ = 'America/Los_Angeles';
const fmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ, month:'2-digit', day:'2-digit',
  hour:'2-digit', minute:'2-digit', hour12:false });
function baseOpts() {
  return {
    layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
              fontFamily:"'Courier New', monospace", fontSize:12 },
    grid: { vertLines:{visible:false}, horzLines:{visible:false} },
    crosshair: { mode: 0 },
    handleScroll: { mouseWheel:false, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false },
    handleScale: { mouseWheel:false, axisPressedMouseMove:{time:true, price:true}, axisDoubleClickReset:true, pinch:true },
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.1, bottom:0.08} },
    timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:false,
      tickMarkFormatter: (t) => fmt.format(new Date(t * 1000)) },
    localization: { timeFormatter: (t) => fmt.format(new Date(t * 1000)) },
  };
}
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}
function snapIdx(times, t) {
  let lo = 0, hi = times.length - 1, r = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (times[m] <= t) { r = m; lo = m + 1; } else hi = m - 1; }
  return r;
}
function render(cd, host) {
  const card = document.createElement('div'); card.className = 'card';
  card.innerHTML = `<div class="ct"></div><div class="cs"></div>`;
  card.querySelector('.ct').textContent = cd.title;
  card.querySelector('.cs').textContent = cd.sub;
  const el = document.createElement('div'); el.className = 'pane'; card.appendChild(el);
  host.appendChild(card);
  const chart = LightweightCharts.createChart(el, Object.assign(baseOpts(), { width: el.clientWidth, height: el.clientHeight }));
  const s = chart.addCandlestickSeries({ upColor:'#DDDDD0', downColor:'#888888', borderUpColor:'#DDDDD0',
    borderDownColor:'#888888', wickUpColor:'#DDDDD0', wickDownColor:'#888888',
    priceFormat:{ type:'price', precision:2, minMove:0.25 }, priceLineVisible:false, lastValueVisible:false });
  s.setData(cd.candles);
  const times = cd.candles.map(c => c.time);
  const last = times[times.length - 1];
  const markers = [];
  cd.draws.forEach(d => {
    if (d.faint) return;
    const i0 = Math.max(0, snapIdx(times, d.t0));
    [['high', d.hi], ['low', d.lo]].forEach(([edge, px]) => {
      const ls = chart.addLineSeries({ color: d.color, lineWidth: 1, lineStyle: 2,
        priceLineVisible:false, lastValueVisible:true, title: `${d.name} ${edge}`, crosshairMarkerVisible:false });
      ls.setData(times.slice(i0).map(t => ({ time: t, value: px })));
    });
    d.pivots.forEach(p => {
      const i = snapIdx(times, p.time);
      if (i < 0) return;
      markers.push({ time: times[i], position: p.kind === 'crest' ? 'aboveBar' : 'belowBar',
                     color: d.color, shape: 'square', text: p.text });
    });
  });
  markers.sort((a, b) => a.time - b.time);
  s.setMarkers(markers);
  chart.timeScale().fitContent();
  const cv = document.createElement('canvas'); cv.className = 'ov'; el.appendChild(cv);
  const ohlc = document.createElement('div'); ohlc.className = 'ohlc'; el.appendChild(ohlc);
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = el.clientWidth, h = el.clientHeight - 28;
    cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + 'px'; cv.style.height = h + 'px';
    const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const ts = chart.timeScale();
    const plotW = w - chart.priceScale('right').width();
    const X = t => { const i = snapIdx(times, t); return i < 0 ? null : ts.timeToCoordinate(times[i]); };
    g.font = "12px 'Courier New', monospace";
    let lane = 0;
    cd.draws.forEach(d => {
      const x0 = d.t0 < times[0] ? 0 : X(d.t0);
      const x1 = d.t1 != null ? X(d.t1) : ts.timeToCoordinate(last);
      const y0 = s.priceToCoordinate(d.hi), y1 = s.priceToCoordinate(d.lo);
      if (x0 != null && x1 != null && y0 != null && y1 != null) {
        g.fillStyle = hexA(d.color, d.faint ? 0.05 : 0.12);
        g.fillRect(Math.max(0, x0), y0, Math.min(plotW, x1) - Math.max(0, x0), y1 - y0);
        if (d.faint) {
          g.strokeStyle = hexA(d.color, 0.9); g.lineWidth = 1; g.setLineDash([4, 3]);
          g.strokeRect(Math.max(0, x0) + 0.5, y0 + 0.5, Math.min(plotW, x1) - Math.max(0, x0), y1 - y0);
          g.setLineDash([]); g.fillStyle = d.color;
          g.fillText(d.name, Math.max(0, x0) + 2, y0 - 3);
        }
      }
      d.vlines.forEach(v => {
        const x = v.time < times[0] ? 0 : X(v.time);
        if (x == null || x > plotW) return;
        g.strokeStyle = hexA(v.color, 0.85); g.setLineDash([2, 3]); g.lineWidth = 1;
        g.beginPath(); g.moveTo(x + 0.5, 16); g.lineTo(x + 0.5, h); g.stroke(); g.setLineDash([]);
        g.fillStyle = v.color;
        g.fillText(v.label, Math.min(x + 3, plotW - 150), 30 + 14 * (lane++ % 4));
      });
    });
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(draw));
  chart.subscribeCrosshairMove(p => {
    const c = p && p.seriesData && p.seriesData.get(s);
    ohlc.textContent = c ? `${cd.tf} ${fmt.format(new Date(p.time * 1000))}  O ${c.open.toFixed(2)} H ${c.high.toFixed(2)} L ${c.low.toFixed(2)} C ${c.close.toFixed(2)}` : cd.tf;
  });
  ohlc.textContent = cd.tf;
  new ResizeObserver(() => { chart.resize(el.clientWidth, el.clientHeight); requestAnimationFrame(draw); }).observe(el);
  setTimeout(draw, 50);
}
CARDS.forEach(cd => render(cd, document.getElementById(cd.section)));
</script></body></html>
"""

if __name__ == "__main__":
    main()
