"""
LXPB 2026 retest report -- H1 LXPB context chart PLUS a synced 1s
candles/bid-volume/ask-volume chart at the exact moment of each retest.

Data sources
------------
- H1 levels/retests: `data/es-h1-2026-backadjusted.csv`, the back-adjusted,
  jump-free 2026-only continuous contract built by
  `data/build_es_h1_2026_backadjusted.py` (see repo README / commit history)
  -- this file only contains 2026 bars, so every retest `detect_lxpb_h1`
  finds on it is automatically a 2026 retest.
- 1s bid/ask volume: real ticks read directly from the local Sierra Chart
  `.scid` files (`D:\\SC\\Data\\F.US.EP{H26,M26,U26}.scid`) via
  `D:\\acheron\\AcheronUtils\\scidReader.py`, picking whichever contract was
  actually front-month at each retest's timestamp using the SAME
  reverse-engineered roll-switch instants (`roll_switch_utc`) that
  `build_es_h1_2026_backadjusted.py` uses -- so the 1s data always comes
  from the real traded contract, not an arbitrary/fixed one.

For each retest, the H1 bar it completed on only pins the event to a whole
hour; this script finds the actual 1-second bar within that hour where
price really touched (or gapped clean past) the level -- the same
touched/gap_over rule `lxpb.py`'s Phase 3 applies at H1 resolution -- and
centers the 1s chart on that exact instant (+/- `--pad-seconds`).

The H1 context chart (formation -> breakout -> retest, with markers and
confluence price-lines) is built by re-using
`render_labels_report.build_row_chart` unchanged, so both label-review
tools stay visually consistent.

Usage:
    python render_lxpb_retest_1s_report.py [--limit 200] [--order desc]
                                            [--pad-seconds 150]
"""
import os
import sys
import json
import argparse
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_DATA_DIR = os.path.join(_HERE, "data")
for _p in (_REPO_ROOT, _HERE, _DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lxpb as L  # noqa: E402
import build_es_h1_2026_backadjusted as B26  # noqa: E402
from render_labels_report import (  # noqa: E402
    build_all_broken_out, build_row_chart,
    CONFLUENCE_COLOR_LHPB, CONFLUENCE_COLOR_LLPB,
)

sys.path.insert(0, r"D:\acheron\AcheronUtils")  # scidReader.py lives there
from scidReader import get_scid_df  # noqa: E402

DEFAULT_DATA = os.path.join(_DATA_DIR, "es-h1-2026-backadjusted.csv")
DEFAULT_OUTPUT = os.path.join(_HERE, "lxpb_retest_1s_report.html")
SCID_DIR = r"D:\SC\Data"

PAD_SECONDS_DEFAULT = 45      # +/- context around the exact 1s touch instant
                               # (tight enough that the retest candle itself is
                               # clearly visible, not lost in a wide window)
ONE_MIN_PAD_MINUTES_DEFAULT = 20  # +/- 1-minute candles shown next to the 1s chart
VOL_THRESHOLD_DEFAULT = 300    # same-side volume (Bid for LHPB, Ask for LLPB) @ the exact retest 1s bar
N_TICKS_DEFAULT = 20
TICK_SIZE_DEFAULT = 0.25

BID_COLOR = "#f87171"
ASK_COLOR = "#4ade80"
LEVEL_COLOR = "#fcd34d"

_CONTRACT_CACHE = {}
_OWN_ROLL_CACHE = None


# ---------------------------------------------------------------------------
# Contract splicing (reuses build_es_h1_2026_backadjusted's roll-switch rule)
# ---------------------------------------------------------------------------

def _own_roll():
    """own_roll[i] = exact UTC instant CONTRACTS[i] rolls OUT and
    CONTRACTS[i+1] becomes front-month (same as build_es_h1_2026_backadjusted's
    `own_roll`), memoized."""
    global _OWN_ROLL_CACHE
    if _OWN_ROLL_CACHE is None:
        _OWN_ROLL_CACHE = [B26.roll_switch_utc(y, m) for _, y, m in B26.CONTRACTS]
    return _OWN_ROLL_CACHE


def _segment_for(i):
    roll = _own_roll()
    n = len(B26.CONTRACTS)
    start = roll[i - 1] if i > 0 else None
    end = roll[i] if i < n - 1 else None
    return start, end


def _contract_index_for(ts_utc):
    """Which CONTRACTS[i] was actually front-month (i.e. which .scid file's
    RAW, unadjusted prices are what really traded) at ts_utc."""
    n = len(B26.CONTRACTS)
    for i in range(n):
        start, end = _segment_for(i)
        if (start is None or ts_utc >= start) and (end is None or ts_utc < end):
            return i
    return n - 1


def _offset_for_ts(ts_utc):
    """entry_price (and every other H1 level/candle) comes from
    es-h1-2026-backadjusted.csv, i.e. `raw_price_in_that_bar's_own_contract
    + TV_GROUND_TRUTH_OFFSETS[that_contract]` (see
    build_es_h1_2026_backadjusted.py). To compare/plot a level against RAW
    .scid ticks from whichever contract is front-month AT ts_utc, the level
    must be converted into THAT contract's raw terms: subtract THAT
    contract's own offset (not the level's formation-bar contract's
    offset) -- offset is a per-segment constant that translates between
    "back-adjusted/continuous" and "that segment's own real traded"
    price, and it is defined by which contract is front-month at ts_utc,
    not by where/when the level itself first formed."""
    i = _contract_index_for(ts_utc)
    sym = B26.CONTRACTS[i][0]
    return B26.TV_GROUND_TRUTH_OFFSETS[sym], sym


def _load_contract(symbol):
    if symbol not in _CONTRACT_CACHE:
        path = os.path.join(SCID_DIR, f"F.US.{symbol}.scid")
        print(f"  loading {path} (first use of {symbol}) ...")
        df = get_scid_df(path)
        df.index = df.index.tz_convert("UTC")
        _CONTRACT_CACHE[symbol] = df
    return _CONTRACT_CACHE[symbol]


def _ticks_for_window(lo_utc, hi_utc):
    """Real ticks covering [lo_utc, hi_utc) from whichever contract(s) were
    front-month across that span -- almost always a single contract; only
    spans two when the window straddles an actual roll instant."""
    parts = []
    for i, (sym, _, _) in enumerate(B26.CONTRACTS):
        seg_start, seg_end = _segment_for(i)
        lo = max(lo_utc, seg_start) if seg_start is not None else lo_utc
        hi = min(hi_utc, seg_end) if seg_end is not None else hi_utc
        if lo >= hi:
            continue
        df = _load_contract(sym)
        sl = df.loc[(df.index >= lo) & (df.index < hi)]
        if not sl.empty:
            parts.append(sl)
    if not parts:
        return None
    return pd.concat(parts).sort_index()


def _resample_1s(ticks):
    """Same 1s resample convention as export_es_1s_pt.py/export_es_1s_range.py:
    Open recomputed as prior bar's Close (raw scid Open is unreliable)."""
    bars = ticks.resample("1s").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
        "Volume": "sum", "Trades": "sum", "BidVolume": "sum", "AskVolume": "sum",
    })
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Close"].shift(1)
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars[["Volume", "Trades", "BidVolume", "AskVolume"]] = bars[
        ["Volume", "Trades", "BidVolume", "AskVolume"]
    ].fillna(0)
    return bars.dropna(subset=["Close"])


def _find_touch_time(bars_1s, hour_start, hour_end, entry_price, level_type):
    """First 1s bar within [hour_start, hour_end) whose range actually
    touches entry_price, or gaps clean past it in the level's retest
    direction -- the same touched/gap_over rule lxpb.py's Phase 3 applies
    at H1 resolution, applied here at 1s resolution to pin the REAL
    instant, within the retest H1 bar, the level was hit."""
    win = bars_1s.loc[(bars_1s.index >= hour_start) & (bars_1s.index < hour_end)]
    for t, r in win.iterrows():
        touched = r.Low <= entry_price <= r.High
        gap_over = (r.High < entry_price) if level_type == "LHPB" else (r.Low > entry_price)
        if touched or gap_over:
            return t
    return hour_start


def _resample_1min(bars_1s):
    """1-minute candles derived from the already-resampled 1s bars (exact --
    max/min/sum over 1s bars is equivalent to computing straight from raw
    ticks, just reusing work already done for the 1s trio)."""
    bars = bars_1s.resample("1min").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    })
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    return bars.dropna(subset=["Close"])


def build_1s_chart(row, pad_seconds, one_min_pad_minutes):
    level_type = row["type"]
    adjusted_entry_price = float(row["entry_price"])
    hour_start = pd.Timestamp(row["retest_time"], tz="UTC")
    hour_end = hour_start + pd.Timedelta(hours=1)
    # Wide enough to cover both the 1s pad AND the 1-min chart's pad (the
    # 1-min chart reuses this same tick pull -- resampled twice -- instead
    # of re-fetching .scid ticks a second time).
    outer_pad = pd.Timedelta(seconds=max(300, pad_seconds + 60, (one_min_pad_minutes + 2) * 60))

    # entry_price is a BACK-ADJUSTED price (from es-h1-2026-backadjusted.csv);
    # the .scid ticks we're about to pull are RAW/unadjusted. Convert the
    # level into the raw terms of whichever contract is actually front-month
    # at the retest hour before comparing/plotting against those ticks --
    # see _offset_for_ts's docstring for why it's the RETEST segment's
    # offset, not the level's formation-bar segment's offset.
    offset, contract_sym = _offset_for_ts(hour_start)
    entry_price = adjusted_entry_price - offset

    ticks = _ticks_for_window(hour_start - outer_pad, hour_end + outer_pad)
    if ticks is None or ticks.empty:
        return None
    bars = _resample_1s(ticks)
    if bars.empty:
        return None

    touch_time = _find_touch_time(bars, hour_start, hour_end, entry_price, level_type)
    lo = touch_time - pd.Timedelta(seconds=pad_seconds)
    hi = touch_time + pd.Timedelta(seconds=pad_seconds)
    window = bars.loc[(bars.index >= lo) & (bars.index <= hi)]
    if window.empty:
        return None

    # Bid/Ask volume of the EXACT 1s bar the retest touched on -- used by
    # the "Vol @ retest" filter (Bid for LHPB/LONG, Ask for LLPB/SHORT).
    if touch_time in bars.index:
        touch_bar = bars.loc[touch_time]
        touch_bid_vol = float(touch_bar["BidVolume"])
        touch_ask_vol = float(touch_bar["AskVolume"])
    else:
        touch_bid_vol = touch_ask_vol = 0.0

    candles = [{
        "time": int(t.timestamp()),
        "open": float(r.Open), "high": float(r.High),
        "low": float(r.Low), "close": float(r.Close),
    } for t, r in window.iterrows()]
    bid = [{"time": int(t.timestamp()), "value": float(r.BidVolume), "color": BID_COLOR}
           for t, r in window.iterrows()]
    ask = [{"time": int(t.timestamp()), "value": float(r.AskVolume), "color": ASK_COLOR}
           for t, r in window.iterrows()]

    is_long = level_type == "LHPB"  # LHPB retest -> LONG, LLPB retest -> SHORT (see combine_and_scan.py)
    marker = {
        "time": int(touch_time.timestamp()),
        "position": "belowBar" if is_long else "aboveBar",
        "color": "#60a5fa" if is_long else "#fbbf24",
        "shape": "arrowUp" if is_long else "arrowDown",
        "text": "RETEST",
    }
    price_line = {
        "price": entry_price, "color": LEVEL_COLOR, "lineWidth": 1,
        "lineStyle": 2,
        "title": f"{level_type} {adjusted_entry_price:.2f}",
    }
    pt = touch_time.tz_convert("America/Los_Angeles")

    # 1-minute candles around the retest -- "next to" the 1s trio, standalone
    # (own crosshair legend, not pan/zoom-synced to the 1s trio), so a
    # reviewer can see a bit more before/after context than the tight 1s
    # window without losing the precise touch-instant view.
    bars_1min = _resample_1min(bars)
    lo_1m = touch_time - pd.Timedelta(minutes=one_min_pad_minutes)
    hi_1m = touch_time + pd.Timedelta(minutes=one_min_pad_minutes)
    window_1m = bars_1min.loc[(bars_1min.index >= lo_1m) & (bars_1min.index <= hi_1m)]
    one_min = {
        "title": f"1min -- +/-{one_min_pad_minutes}min around retest  |  level {adjusted_entry_price:.2f}",
        "candles": [{
            "time": int(t.timestamp()),
            "open": float(r.Open), "high": float(r.High),
            "low": float(r.Low), "close": float(r.Close),
        } for t, r in window_1m.iterrows()],
        "markers": [dict(marker)],
        "priceLines": [dict(price_line)],
        "precision": 2,
    }

    return {
        "title": f"1s @ retest -- {pt.strftime('%Y-%m-%d %H:%M:%S')} PT "
                  f"({'LONG' if is_long else 'SHORT'})  |  level {adjusted_entry_price:.2f}",
        "candles": candles, "bid": bid, "ask": ask,
        "markers": [marker], "priceLines": [price_line],
        "precision": 2,
        "oneMin": one_min,
        "touch_bid_volume": touch_bid_vol,
        "touch_ask_volume": touch_ask_vol,
    }


def _confluence_hints(all_broken, level_type, price, retest_time, n_ticks, tick_size):
    """Minimal stand-in for render_labels_report.compute_hints -- build_row_chart
    only reads hints["confluence_prices"], so that's all this computes."""
    tol = n_ticks * tick_size
    same_type = all_broken[all_broken["type"] == level_type]
    nearby = same_type[
        (same_type["breakout_time"] <= retest_time)
        & ((same_type["price"] - price).abs() <= tol)
        & ((same_type["price"] - price).abs() > 1e-9)
    ]
    return {"confluence_prices": sorted(float(p) for p in nearby["price"].tolist())}


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
tr.trig-row.dir-long td.dir-cell  { color:var(--bull); font-weight:600; }
tr.trig-row.dir-short td.dir-cell { color:var(--bear); font-weight:600; }
tr.trig-row.vol-pass td.vol-cell { color:var(--accent); font-weight:600; }
td.mono-small { font-family:ui-monospace,monospace; font-size:0.9em; color:var(--text-dim); }
.expand-cell { text-align:center; }
.expand-btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
              border-radius:4px; padding:2px 8px; cursor:pointer; }
.expand-btn.open { background:#1e3a5f; color:#7bb4f5; border-color:#1d3a5c; }
tr.detail-row td.detail-cell { background:#0a0c0e; padding:10px 12px; border-top:none; }
.chart-stack { display:flex; flex-direction:column; gap:8px; }
.chart-h1 { height:220px; }
.chart-row-2col { display:grid; grid-template-columns: 1fr 1fr; gap:8px; align-items:start; }
.chart-col-1s { display:grid; grid-template-rows: 300px 100px 100px; gap:8px; }
.chart-col-1m .chart-cell { height:516px; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px; overflow:hidden; }
.chart-title { color:#cccccc; padding:5px 8px; font-size:0.75em;
               font-family:ui-monospace,monospace; background:#0a0a0a;
               border-bottom:1px solid #1f1f1f; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 24px); width:100%; }
.hidden { display:none !important; }
</style>
"""


def build_page(rows_meta, title, n_ticks, tick_size, pad_seconds, one_min_pad_minutes, vol_threshold):
    n_long = sum(1 for r in rows_meta if r["direction"] == "LONG")
    n_short = len(rows_meta) - n_long
    n_pass = sum(1 for r in rows_meta if r["vol_pass"])

    header = f"""
<h1>{title}</h1>
<p class="lead">Each row is a completed 2026 LXPB retest (H1 back-adjusted, jump-free
ES continuous contract, `data/es-h1-2026-backadjusted.csv`). All prices/chart labels are
in ADJUSTED (back-adjusted continuous) terms; the RAW price + contract actually traded at
retest time is shown once per row in the "Raw (Contract)" table column instead of being
repeated on every chart. On expand: H1 context (formation -&gt; breakout -&gt; retest,
gold/blue/red confluence lines within {n_ticks} ticks) on top, centered horizontally
(fixed candle width, blank space left/right if there aren't enough bars to fill the row);
below it, side-by-side, 1s candles + Bid Volume + Ask Volume (real ticks spliced from the
local .scid files, contract chosen per-retest by the same roll-switch rule as the H1
series) zoomed to +/-{pad_seconds}s around the exact second price touched (or gapped past)
the level so the retesting candle itself is clearly visible -- all three 1s panes share
pan/zoom and crosshair -- and a standalone 1min candle chart +/-{one_min_pad_minutes}min
around the retest for broader before/after context, with other nearby same-type LXPB
levels drawn as unlabeled background lines (no formation/breakout markers -- it's a
zoomed-in view of the retest itself). "Vol @ Retest" filter below shows only retests where
the SAME-SIDE volume (Bid for LHPB/LONG, Ask for LLPB/SHORT) on the exact 1s bar the
retest touched on exceeds {vol_threshold}.</p>
<div class="summary">
  <div class="box true"><strong>{len(rows_meta)}</strong>2026 retests</div>
  <div class="box"><strong>{n_long}</strong>Long (LHPB)</div>
  <div class="box"><strong>{n_short}</strong>Short (LLPB)</div>
  <div class="box true"><strong>{n_pass}</strong>Pass Vol&gt;{vol_threshold} @ retest</div>
</div>
"""

    filter_panel = f"""
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Direction</span>
    <label class="chip"><input type="checkbox" class="f-cb f-dir" value="LONG" checked> LONG (LHPB)</label>
    <label class="chip"><input type="checkbox" class="f-cb f-dir" value="SHORT" checked> SHORT (LLPB)</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Vol @ Retest</span>
    <label class="chip"><input type="checkbox" class="f-cb f-vol" value="pass" checked> Bid&gt;{vol_threshold} (LHPB) / Ask&gt;{vol_threshold} (LLPB)</label>
    <label class="chip"><input type="checkbox" class="f-cb f-vol" value="other" checked> Below threshold</label>
  </div>
</div>
"""

    thead = """
<table id="trig-table">
<thead><tr>
  <th class="left">#</th><th class="left">Retest (PT)</th><th class="left">Dir</th>
  <th class="left">Type</th><th>Entry Price</th><th class="left">Raw (Contract)</th>
  <th class="left">Formed</th>
  <th class="left">Breakout</th><th>FTA</th><th>Stop</th>
  <th>BidVol@R</th><th>AskVol@R</th>
  <th class="expand-th">▶</th>
</tr></thead>
<tbody>
"""

    rows_html = []
    for r in rows_meta:
        dir_cls = "dir-long" if r["direction"] == "LONG" else "dir-short"
        vol_cls = "vol-pass" if r["vol_pass"] else "vol-other"
        vol_data = "pass" if r["vol_pass"] else "other"
        fta_str = f"{r['fta']:.2f}" if r["fta"] is not None else "-"
        raw_str = f"{r['entry_price_raw']:.2f} ({r['contract_sym']})"
        rows_html.append(f"""
<tr class="trig-row {dir_cls} {vol_cls}" data-idx="{r['idx']}" data-dir="{r['direction']}" data-vol="{vol_data}" onclick="toggleChart({r['idx']})">
  <td class="left">{r['idx']}</td><td class="left">{r['retest_pt']}</td>
  <td class="left dir-cell">{r['direction']}</td><td class="left">{r['type']}</td>
  <td>{r['entry_price']:.2f}</td><td class="left mono-small">{raw_str}</td>
  <td class="left">{r['formation_time']}</td>
  <td class="left">{r['breakout_time']}</td><td>{fta_str}</td><td>{r['stop_loss']:.2f}</td>
  <td class="vol-cell">{r['touch_bid_volume']:.0f}</td><td class="vol-cell">{r['touch_ask_volume']:.0f}</td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{r['idx']}" onclick="event.stopPropagation();toggleChart({r['idx']})">▶</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{r['idx']}" data-dir="{r['direction']}" id="chart-row-{r['idx']}">
  <td colspan="13"><div class="chart-stack" data-cid="{r['idx']}">
    <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{r['idx']}"></div><div class="chart-ph" id="ch1-{r['idx']}"></div></div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{r['idx']}"></div><div class="chart-ph" id="cc-{r['idx']}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{r['idx']}">Bid Volume</div><div class="chart-ph" id="cb-{r['idx']}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{r['idx']}">Ask Volume</div><div class="chart-ph" id="ca-{r['idx']}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{r['idx']}"></div><div class="chart-ph" id="c1m-{r['idx']}"></div></div>
      </div>
    </div>
  </div></td>
</tr>
""")

    tbody_close = "</tbody></table>"
    return header, filter_panel, thead, "".join(rows_html), tbody_close


# JS charting engine (H1 context + synced 1s candles/bid/ask trio) is the
# same lightweight-charts@4 pattern established in lxpb-es-vol/render_report.py
# (dark theme, monochrome candles matching trapVariants/v25_2026.html, lazy
# per-row render on expand, crosshair + pan/zoom sync across the 1s trio).
JS_TEMPLATE = """
<script>
const CHARTS = __CHARTS_JSON__;
const rendered = {};
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });
// Fixed bar spacing for the H1 context chart -- same fix as
// render_labels_report.py's _renderChart: autoSize/fitContent stretches
// however many candles exist out to fill the FULL container width, which
// on a wide monitor makes a handful of H1 bars look absurdly wide/thin.
// A fixed spacing keeps candles a consistent, readable width and just
// leaves blank space (or lets the reviewer scroll) instead.
const FIXED_BAR_SPACING = 6;

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

// Center the candles horizontally (blank/black space evenly on BOTH left
// and right when there aren't enough bars to fill the container at
// FIXED_BAR_SPACING) instead of lightweight-charts' default right-aligned
// scroll position.
function _centerLogicalRange(chart, el, nBars) {
  if (!nBars) return;
  const barsVisible = Math.max(1, el.clientWidth / FIXED_BAR_SPACING);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}

function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  // autoSize NOT used here (see FIXED_BAR_SPACING comment above) -- sized
  // manually + fixed bar spacing, with our own ResizeObserver so a real
  // container resize (row opened, window resized) re-applies both without
  // fighting the reviewer's own pan/zoom.
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false,
    width: el.clientWidth || 800,
    height: el.clientHeight || 320,
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
  _renderTrio(i, cd);
  if (cd.oneMin) _renderOneMin(i, cd.oneMin);
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
  const volOn = Array.from(document.querySelectorAll('.f-vol:checked')).map(c => c.value);
  document.querySelectorAll('.trig-row').forEach(function(tr) {
    const show = dirOn.includes(tr.dataset.dir) && volOn.includes(tr.dataset.vol);
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


def render(data_path=DEFAULT_DATA, output_path=DEFAULT_OUTPUT, title=None,
           limit=0, order="desc", pad_seconds=PAD_SECONDS_DEFAULT,
           n_ticks=N_TICKS_DEFAULT, tick_size=TICK_SIZE_DEFAULT,
           one_min_pad_minutes=ONE_MIN_PAD_MINUTES_DEFAULT,
           vol_threshold=VOL_THRESHOLD_DEFAULT):
    h1_df = L.load_ohlc_data(data_path)
    touch_lv0, touch_lv1, retests_df = L.detect_lxpb_h1(h1_df)

    # Data file only covers 2026, so every retest is already a 2026 retest --
    # this filter is just a defensive/explicit guard, per the requirement.
    retests_df = retests_df[retests_df["retest_time"].dt.year == 2026].copy()

    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    # Confluence overlay universe: ALL 2026 broken-out levels (gap or not),
    # same as before -- gap exclusion below only changes which retests are
    # RENDERED as report rows, not what counts as a nearby confluence level.
    all_broken = build_all_broken_out(retests_df, touch_lv1)

    # Exclude gap instances from the rendered rows (per user request) --
    # lxpb.py's own consuming/invalidation logic (advance_one_bar) is
    # untouched; this is purely a report-display filter applied after
    # detection. Two distinct gap cases, both real state transitions:
    #   - gap BREAKOUT: the level's breakout bar opened/stayed entirely
    #     beyond the level (bar.low > price for LHPB, bar.high < price for
    #     LLPB) -- see lxpb.py Phase 2's gap-breakout branch.
    #   - gap RETEST (gap-over): the retest bar gapped clean past the level
    #     instead of actually touching it -- detectable via
    #     NOT(retest_low <= entry_price <= retest_high), per lxpb.py's own
    #     docstring for Phase 3's gap-over branch.
    gap_breakout = (
        ((retests_df["type"] == "LHPB") & (retests_df["breakout_low"] > retests_df["price"]))
        | ((retests_df["type"] == "LLPB") & (retests_df["breakout_high"] < retests_df["price"]))
    )
    gap_retest = ~(
        (retests_df["retest_low"] <= retests_df["entry_price"])
        & (retests_df["entry_price"] <= retests_df["retest_high"])
    )
    n_before_gap = len(retests_df)
    retests_df = retests_df[~(gap_breakout | gap_retest)].copy()
    print(f"Excluding {n_before_gap - len(retests_df)} gap-breakout/gap-retest "
          f"instances from the report ({n_before_gap} -> {len(retests_df)}).")

    retests_df = retests_df.sort_values("retest_time", ascending=(order == "asc"))
    if limit:
        retests_df = retests_df.head(limit)
    retests_df = retests_df.reset_index(drop=True)

    if title is None:
        n = len(retests_df)
        title = f"LXPB -- 2026 Retests, H1 + 1s Bid/Ask Volume @ Retest ({n} shown)"

    rows_meta = []
    chart_map = {}
    n_built = 0
    for i, row in retests_df.iterrows():
        hints = _confluence_hints(all_broken, row["type"], float(row["price"]),
                                   row["retest_time"], n_ticks, tick_size)
        h1_chart = build_row_chart(h1_df, pos_by_ts, row, hints)

        # H1 candles/price-lines are all in ADJUSTED (back-adjusted continuous)
        # terms -- the H1 chart's own price SCALE must stay adjusted to line
        # up with its back-adjusted candles. The corresponding RAW price +
        # contract (same for all 3 charts on this row) is NOT repeated on
        # every chart -- it's shown once, in the results table row instead.
        retest_ts_utc = pd.Timestamp(row["retest_time"], tz="UTC")
        offset, contract_sym = _offset_for_ts(retest_ts_utc)
        entry_price_raw = float(row["entry_price"]) - offset

        chart1s = build_1s_chart(row, pad_seconds, one_min_pad_minutes)
        if chart1s is None:
            print(f"  [skip 1s data] #{i} {row['type']} {row['retest_time']} -- no scid ticks found")
            chart1s = {"title": "(no 1s data found)", "candles": [], "bid": [], "ask": [],
                       "markers": [], "priceLines": [], "precision": 2,
                       "oneMin": None, "touch_bid_volume": 0.0, "touch_ask_volume": 0.0}
        else:
            n_built += 1
            # Nearby same-type LXPB levels, shown as thin unlabeled background
            # lines on the 1min chart too (same confluence tolerance/colors as
            # the H1 chart) -- formation/breakout markers are intentionally
            # NOT added here; the 1min chart is a zoomed-in view of the retest
            # itself, not the level's whole history.
            confluence_color = CONFLUENCE_COLOR_LHPB if row["type"] == "LHPB" else CONFLUENCE_COLOR_LLPB
            for p in hints["confluence_prices"]:
                chart1s["oneMin"]["priceLines"].append({
                    "price": p, "color": confluence_color, "lineWidth": 1,
                    "lineStyle": 2, "title": "",
                })
        chart1s["h1"] = h1_chart
        chart_map[i] = chart1s

        direction = "LONG" if row["type"] == "LHPB" else "SHORT"
        touch_bid_volume = chart1s["touch_bid_volume"]
        touch_ask_volume = chart1s["touch_ask_volume"]
        vol_pass = (
            (direction == "LONG" and touch_bid_volume > vol_threshold)
            or (direction == "SHORT" and touch_ask_volume > vol_threshold)
        )
        retest_pt = row["retest_time"].tz_localize("UTC").tz_convert("America/Los_Angeles")
        rows_meta.append({
            "idx": i, "type": row["type"], "direction": direction,
            "entry_price": float(row["entry_price"]),
            "entry_price_raw": entry_price_raw, "contract_sym": contract_sym,
            "formation_time": str(row["formation_time"]),
            "breakout_time": str(row["breakout_time"]),
            "retest_pt": retest_pt.strftime("%Y-%m-%d %H:%M %Z"),
            "fta": float(row["fta"]) if row["fta"] == row["fta"] else None,
            "stop_loss": float(row["stop_loss"]),
            "touch_bid_volume": touch_bid_volume,
            "touch_ask_volume": touch_ask_volume,
            "vol_pass": vol_pass,
        })

    print(f"Built 1s bid/ask windows for {n_built}/{len(retests_df)} retests.")

    header, filter_panel, thead, rows, tbody_close = build_page(
        rows_meta, title, n_ticks, tick_size, pad_seconds, one_min_pad_minutes, vol_threshold)

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
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {len(rows_meta)} retests -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LXPB 2026 retest report: H1 context + 1s bid/ask volume @ retest")
    parser.add_argument("--data", default=DEFAULT_DATA, help="H1 back-adjusted 2026 OHLC CSV")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--title", default=None, help="Report <h1> title")
    parser.add_argument("--limit", type=int, default=0, help="Max retests to render (0 = no cap, all 2026 retests)")
    parser.add_argument("--order", choices=["desc", "asc"], default="desc",
                         help="desc = most recent retests first")
    parser.add_argument("--pad-seconds", type=int, default=PAD_SECONDS_DEFAULT,
                         help="+/- seconds of 1s context around the exact retest touch instant")
    parser.add_argument("--n-ticks", type=int, default=N_TICKS_DEFAULT,
                         help="Confluence radius (ticks) for the H1 chart's nearby-level overlay")
    parser.add_argument("--tick-size", type=float, default=TICK_SIZE_DEFAULT)
    parser.add_argument("--one-min-pad-minutes", type=int, default=ONE_MIN_PAD_MINUTES_DEFAULT,
                         help="+/- minutes of 1min candles shown next to the 1s chart")
    parser.add_argument("--vol-threshold", type=float, default=VOL_THRESHOLD_DEFAULT,
                         help="Same-side volume (Bid for LHPB, Ask for LLPB) @ retest 1s bar for the pass filter")
    args = parser.parse_args()
    render(args.data, args.output, args.title, args.limit, args.order,
           args.pad_seconds, args.n_ticks, args.tick_size,
           args.one_min_pad_minutes, args.vol_threshold)
