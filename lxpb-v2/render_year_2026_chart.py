"""
Single-pane, whole-calendar-year 2026 ES H1 candlestick chart (TradingView's
own ES1! continuous-contract export, dark theme, lightweight-charts --
styled like render_labels_report.py's H1 panes but as one standalone,
pannable/zoomable chart spanning the full year instead of per-trade
context windows).

TradingView's ES1! export is ALREADY a back-adjusted, jump-free continuous
series (TradingView does its own roll-splicing internally -- see
data/build_es_h1_2026_backadjusted.py's docstring, which independently
reverse-engineered and confirmed TradingView's exact roll rule/offsets
against this same export), so no manual contract-splicing is needed here,
unlike the .scid-tick-based reconstruction that module does for real 1s
tick data.

Combines the two TradingView H1 exports currently in data/ so the chart is
as current as possible:
  - "24aug-CME_MINI_ES1!, 60.csv"  (2025-07-23 -> 2026-08-24)
  - "1sep-CME_MINI_ES1!, 60.csv"   (2026-06-15 -> 2026-09-02, newer/fresher)
On their overlap (2026-06-15 -> 2026-08-24) the newer "1sep" export's bars
win (pandas `duplicated(keep="last")` after concatenating older-then-newer).

Usage:
    python render_year_2026_chart.py
    python render_year_2026_chart.py --output my_2026_chart.html
"""
import os
import sys
import json
import argparse
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_labels_report as R  # noqa: E402  (reused only for its CSS/dark theme)
import lxpb as L  # noqa: E402

_DATA_DIR = os.path.join(_HERE, "data")
OLD_CSV = os.path.join(_DATA_DIR, "24aug-CME_MINI_ES1!, 60.csv")
NEW_CSV = os.path.join(_DATA_DIR, "1sep-CME_MINI_ES1!, 60.csv")

YEAR_START = pd.Timestamp("2026-01-01")


def load_merged_2026():
    """Merge the two TradingView exports (older-then-newer so the newer
    file's bars win on any overlapping timestamp -- see module docstring),
    then clip to calendar-year 2026 only."""
    old = L.load_ohlc_data(OLD_CSV)
    new = L.load_ohlc_data(NEW_CSV)
    combined = pd.concat([old, new]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined[(combined.index >= YEAR_START)]


CSS = R.CSS + """
<style>
body { max-width:none; }
.chart-wrap { height:calc(100vh - 120px); min-height:600px; background:#000;
              border:1px solid var(--border); border-radius:6px; overflow:hidden; }
</style>
"""

JS = """
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const CANDLES = __CANDLES_JSON__;
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });

const el = document.getElementById('chart');
const titleEl = document.getElementById('chart-title');
const baseTitle = titleEl.textContent;
const chart = LightweightCharts.createChart(el, {
  autoSize: true,
  layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
            fontFamily:"'Courier New', monospace", fontSize:10 },
  grid: { vertLines:{visible:false}, horzLines:{color:'#1a1a1a'} },
  crosshair: { mode: 0 },
  rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.06, bottom:0.06} },
  timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:false,
    tickMarkFormatter: (t) => timeFmt.format(new Date(t * 1000)) },
  localization: { timeFormatter: (t) => timeFmt.format(new Date(t * 1000)) },
});
const series = chart.addCandlestickSeries({
  upColor:'#DDDDD0', downColor:'#888888',
  borderUpColor:'#DDDDD0', borderDownColor:'#888888',
  wickUpColor:'#DDDDD0', wickDownColor:'#888888',
  priceFormat: { type:'price', precision:2, minMove:0.25 },
});
series.setData(CANDLES);
chart.subscribeCrosshairMove((param) => {
  const d = param.seriesData && param.seriesData.get(series);
  if (d && d.open != null) {
    titleEl.textContent = baseTitle + '  |  O ' + d.open.toFixed(2)
      + '  H ' + d.high.toFixed(2) + '  L ' + d.low.toFixed(2) + '  C ' + d.close.toFixed(2);
  } else { titleEl.textContent = baseTitle; }
});
chart.timeScale().fitContent();
new ResizeObserver((entries) => {
  const r = entries[0].contentRect;
  if (r.width > 0 && r.height > 0) chart.resize(r.width, r.height);
}).observe(el);
</script>
"""


def render(output_path):
    df = load_merged_2026()
    candles = [{
        "time": R._to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in df.iterrows()]

    start_str = df.index.min().strftime("%Y-%m-%d")
    end_str = df.index.max().strftime("%Y-%m-%d")
    title = f"ES1! (TradingView continuous, backadjusted) H1 -- {start_str} to {end_str} ({len(candles)} bars)"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>ES 2026 Full-Year H1 Chart</title>
{CSS}
</head><body>
<h1>LXPB ES1! -- Whole of 2026, H1</h1>
<p class="lead">Continuous-contract H1 candles for calendar year 2026, merged from TradingView's
own ES1! exports (already back-adjusted/roll-spliced by TradingView -- see module docstring);
newer export's bars win on any overlapping timestamp. Scroll/drag to pan, wheel/pinch to zoom.</p>
<div id="chart-title" class="lead" style="margin-bottom:6px;">{title}</div>
<div class="chart-wrap"><div id="chart" style="width:100%;height:100%;"></div></div>
{JS.replace("__CANDLES_JSON__", json.dumps(candles))}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}  ({len(candles)} H1 bars, {start_str} -> {end_str})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Whole-calendar-year 2026 ES H1 chart")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    out = args.output or os.path.join(_HERE, "public", "reports", "lxpb_2026_full_year_chart.html")
    render(out)
