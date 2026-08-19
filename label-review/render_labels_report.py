"""
LXPB hand-labeling report.

Renders one expandable row per completed LXPB retest (from
`lxpb.detect_lxpb_h1`), each with an H1 candlestick chart spanning from a
few bars before the level's formation (Phase 0) through a few bars after
its retest (Phase 2), lightweight-charts markers for Phase 0 (formation)
/ Phase 1 (breakout) / Phase 2 (retest), and price-lines for other
nearby same-type LXPB levels so a human reviewer can visually judge
confluence clusters.

Human-eval feature checkboxes (persisted client-side to localStorage, and
exportable/importable as CSV so labels survive across sessions/machines).
Each checkbox is PRE-CHECKED with a computed default (see `defaults` in
compute_hints) to speed up labeling -- the reviewer's tick is always the
final ground truth and can flip any default:
  - phase0_spike         : formation bar is a genuine spike             (hint/default: patterns-pure
                            find_hammer/find_shooting_star on the formation bar -- NOT lxpb.py's own
                            is_hammer/is_shootingstar, per explicit instruction to source spike
                            detection from D:\\daily-analysis\\patterns-pure)
  - phase1_wide_breakout : breakout bar is unusually wide-ranging       (hint: range / 20-bar avg range;
                            default: ratio >= WIDE_BREAKOUT_RATIO_THRESHOLD)
  - confluence_cluster   : a real cluster of nearby same-type levels    (hint: count within N_TICKS;
                            default: count >= CONFLUENCE_MIN_COUNT)
  - large_wick           : LHPB upper wick / LLPB lower wick is large   (hint: wick % of formation bar
                            range; default: patterns-pure candle_utils.has_large_upper/lower_wick,
                            40% threshold)
  - fast_retest          : retest came quickly, no slow drift           (hint: H1 bars from breakout to
                            retest; default: bars <= FAST_RETEST_MAX_BARS)
  - reviewed             : row has been looked at (tracking only, not a "feature")
  - valid                : overall verdict for this LXPB level -- defaults to checked/"valid" if ANY
                            of the 5 qualities above default-true, unchecked/"invalid" otherwise. This
                            is the primary signal intended to later fine-tune lxpb.py's detection
                            thresholds once real hand-labels are collected and exported.
  - misc                 : freeform notes textarea

To add another checkbox feature later, add one entry to the FEATURES list
in JS_TEMPLATE (id/label/hint) plus its default rule in compute_hints's
`defaults` dict -- no other JS changes needed; the table, localStorage
persistence, and CSV export/import are all driven off the FEATURES list.

Usage:
    python render_labels_report.py --data ../data/es-h1-4apr2021-11apr2025.csv --limit 300
    python render_labels_report.py --data ../data/es-h1-4apr2021-11apr2025.csv --all --output all_labels.html
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import lxpb as L  # noqa: E402

# Spike (and large-wick) detection is sourced from patterns-pure -- the
# more rigorous/"source of truth" pattern library -- rather than
# reimplementing hammer/shooting-star/wick logic locally.
_PATTERNS_PURE = r"D:\daily-analysis\patterns-pure"
if _PATTERNS_PURE not in sys.path:
    sys.path.insert(0, _PATTERNS_PURE)

from find_hammer import find_hammer as _pp_find_hammer  # noqa: E402
from find_shooting_star import find_shooting_star as _pp_find_shooting_star  # noqa: E402
from candle_utils import (  # noqa: E402
    has_large_upper_wick as _pp_has_large_upper_wick,
    has_large_lower_wick as _pp_has_large_lower_wick,
)

# ../data/es-h1-2015-14aug2026.csv is a TradingView back-adjusted continuous contract --
# its historical absolute price levels drift by hundreds of points every time it's
# re-exported (verified) and are not real traded prices. label-review instead uses its own
# locally-built, non-back-adjusted, contract-tagged continuous series (see
# data/build_es_h1_continuous.py); this only affects label-review, not other tools/tests
# in this monorepo that still read ../data/es-h1-2015-14aug2026.csv directly.
DEFAULT_DATA = os.path.join(_HERE, "data", "es-h1-continuous.csv")
DEFAULT_OUTPUT = os.path.join(_HERE, "lxpb_labels_report.html")

BARS_BEFORE = 8     # H1 bars of context shown before Phase 0 (formation)
BARS_AFTER = 8      # H1 bars of context shown after Phase 2 (retest)
# A level can sit unbroken for months (formation -> breakout) and/or take
# months to get retested (breakout -> retest). build_row_chart shows a
# short context window around EACH phase and merges/concatenates them --
# gaps larger than MAX_MERGE_GAP bars are compressed out (with a small
# "[N bars skipped]" marker) instead of rendering the full multi-year span.
CONTEXT_BARS_AFTER_FORMATION = 3    # bars shown right after Phase 0
CONTEXT_BARS_BEFORE_BREAKOUT = 3    # bars shown right before Phase 1
CONTEXT_BARS_AFTER_BREAKOUT = 6     # bars shown right after Phase 1
CONTEXT_BARS_BEFORE_RETEST = 6      # bars shown right before Phase 2
MAX_MERGE_GAP = 15   # gaps <= this many bars are merged into one continuous cluster
N_TICKS_DEFAULT = 20
TICK_SIZE_DEFAULT = 0.25
AVG_RANGE_WINDOW = 20  # trailing bars used for the wide-breakout hint's baseline

# Thresholds used to compute each feature's DEFAULT checkbox state (a
# starting guess pre-filled from the hint metrics; the reviewer's tick is
# the ground truth, and can flip any of these). Chosen from this dataset's
# own hint-value distributions (see label-review/README.md); revisit if
# defaults look miscalibrated once real hand-labels come back.
WIDE_BREAKOUT_RATIO_THRESHOLD = 2.0   # breakout range >= 2x trailing-20-bar avg range
CONFLUENCE_MIN_COUNT = 3              # >=3 nearby same-type levels = a real "cluster"
FAST_RETEST_MAX_BARS = 6              # <=6 H1 bars breakout->retest = "fast" (~ dataset median)

LEVEL_COLOR = "#fcd34d"     # this row's own level (gold)
CONFLUENCE_COLOR_LHPB = "#60a5fa"  # other same-type nearby levels (blue)
CONFLUENCE_COLOR_LLPB = "#f87171"  # other same-type nearby levels (red)
P0_COLOR = "#fcd34d"
P1_COLOR_UP = "#4ade80"
P1_COLOR_DOWN = "#f87171"
P2_COLOR = "#a78bfa"


def _to_epoch_utc(ts):
    """int unix seconds for a naive Timestamp that represents UTC wall
    clock (H1 bar times from lxpb.load_ohlc_data are naive UTC)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def build_all_broken_out(retests_df, touch_lv1_df):
    """Every level that ever broke out (completed retest OR still awaiting
    one at the end of the data), used only for the confluence overlay --
    matches lxpb-es-vol/lxpb_confluence.py's rationale that only
    broken-out (touch_lv1) levels count as confluence, never untested
    touch_lv0 swing points."""
    cols = ["type", "price", "breakout_time"]
    parts = []
    if not retests_df.empty:
        parts.append(retests_df[cols])
    if not touch_lv1_df.empty:
        parts.append(touch_lv1_df[cols])
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True)


def is_spike_pp(h1_df, pos_by_ts, level_type, formation_time):
    """Spike classification for the formation bar, sourced from
    patterns-pure (find_hammer / find_shooting_star) rather than lxpb.py's
    own simplified is_hammer/is_shootingstar -- LHPB spikes look like a
    shooting star (rejection of higher prices), LLPB spikes look like a
    hammer (rejection of lower prices). Mirrors the exact 2-row slicing
    convention used by patterns-pure/lxpb_quality_gate.py so the
    shift(1)-based confirmation check has a valid previous bar."""
    fi = pos_by_ts[formation_time]
    slice_2 = h1_df.iloc[max(0, fi - 1):fi + 1]
    if level_type == "LHPB":
        matched = _pp_find_shooting_star(slice_2, atr=0.0)
    else:
        matched = _pp_find_hammer(slice_2, atr=0.0)
    return formation_time in matched.index


def compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken, n_ticks, tick_size):
    """Numeric/boolean hints shown next to each checkbox, plus a per-feature
    `defaults` dict (pre-fills each feature checkbox's initial state based
    on the same metrics) -- the reviewer's tick is always the ground
    truth; these are only starting guesses to speed up labeling."""
    level_type = row["type"]
    price = row["price"]

    formation_bar = h1_df.loc[row["formation_time"]]
    total_range = float(formation_bar["high"] - formation_bar["low"])
    if level_type == "LHPB":
        wick = float(formation_bar["high"] - max(formation_bar["open"], formation_bar["close"]))
        large_wick = _pp_has_large_upper_wick(formation_bar)
    else:
        wick = float(min(formation_bar["open"], formation_bar["close"]) - formation_bar["low"])
        large_wick = _pp_has_large_lower_wick(formation_bar)
    wick_pct = (wick / total_range * 100.0) if total_range > 0 else 0.0

    is_spike = is_spike_pp(h1_df, pos_by_ts, level_type, row["formation_time"])

    breakout_range = float(row["breakout_high"] - row["breakout_low"])
    baseline = avg_range_20.get(row["breakout_time"])
    range_ratio = (breakout_range / baseline) if baseline and baseline > 0 else None

    bars_to_retest = pos_by_ts[row["retest_time"]] - pos_by_ts[row["breakout_time"]]

    tol = n_ticks * tick_size
    same_type = all_broken[all_broken["type"] == level_type]
    nearby = same_type[
        (same_type["breakout_time"] <= row["retest_time"])
        & ((same_type["price"] - price).abs() <= tol)
        & ((same_type["price"] - price).abs() > 1e-9)  # exclude this level itself
    ]
    confluence_count = int(len(nearby))

    defaults = {
        "phase0_spike": bool(is_spike),
        "phase1_wide_breakout": range_ratio is not None and range_ratio >= WIDE_BREAKOUT_RATIO_THRESHOLD,
        "confluence_cluster": confluence_count >= CONFLUENCE_MIN_COUNT,
        "large_wick": bool(large_wick),
        "fast_retest": bars_to_retest <= FAST_RETEST_MAX_BARS,
    }

    return {
        "is_spike": bool(is_spike),
        "wick_pct": round(wick_pct, 1),
        "range_ratio": round(range_ratio, 2) if range_ratio is not None else None,
        "bars_to_retest": int(bars_to_retest),
        "confluence_count": confluence_count,
        "confluence_prices": sorted(float(p) for p in nearby["price"].tolist()),
        "defaults": defaults,
        "valid_default": any(defaults.values()),
    }


def _merge_segments(segments, n_bars, max_gap):
    """Sort/clip/merge (start, end) inclusive bar-index segments; adjacent
    segments separated by <= max_gap bars are merged into one (no visible
    skip), larger gaps are kept as separate clusters. Returns a list of
    (start, end, gap_before) tuples, gap_before=0 for the first segment."""
    clipped = sorted((max(0, s), min(n_bars - 1, e)) for s, e in segments)
    merged = []
    for s, e in clipped:
        if merged and s - merged[-1][1] - 1 <= max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    prev_end = None
    for s, e in merged:
        gap = 0 if prev_end is None else max(0, s - prev_end - 1)
        out.append((s, e, gap))
        prev_end = e
    return out


def build_row_chart(h1_df, pos_by_ts, row, hints):
    level_type = row["type"]
    price = float(row["price"])
    form_pos = pos_by_ts[row["formation_time"]]
    breakout_pos = pos_by_ts[row["breakout_time"]]
    retest_pos = pos_by_ts[row["retest_time"]]
    n_bars = len(h1_df)

    # Candidate context windows around each of the 3 phases. Any pair of
    # these can be arbitrarily far apart -- a level can sit unbroken for
    # months (formation -> breakout) and/or take months to get retested
    # (breakout -> retest) -- so segments are merged generically rather
    # than assuming only the breakout->retest gap can be large.
    segments = [
        (form_pos - BARS_BEFORE, form_pos + CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + CONTEXT_BARS_AFTER_BREAKOUT),
        (retest_pos - CONTEXT_BARS_BEFORE_RETEST, retest_pos + BARS_AFTER),
    ]
    merged = _merge_segments(segments, n_bars, MAX_MERGE_GAP)

    parts = []
    skip_markers = []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": _to_epoch_utc(h1_df.index[s]),
                "position": "inBar", "color": "#9ca3af", "shape": "square",
                "text": f"[{gap} bars skipped]",
            })
        parts.append(h1_df.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)

    candles = [{
        "time": _to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in window.iterrows()]

    is_lhpb = level_type == "LHPB"
    markers = [
        {
            "time": _to_epoch_utc(row["formation_time"]),
            "position": "aboveBar" if is_lhpb else "belowBar",
            "color": P0_COLOR, "shape": "circle", "text": "P0 form",
        },
        {
            "time": _to_epoch_utc(row["breakout_time"]),
            "position": "belowBar" if is_lhpb else "aboveBar",
            "color": P1_COLOR_UP if is_lhpb else P1_COLOR_DOWN,
            "shape": "arrowUp" if is_lhpb else "arrowDown",
            "text": "P1 breakout",
        },
        {
            "time": _to_epoch_utc(row["retest_time"]),
            "position": "aboveBar" if is_lhpb else "belowBar",
            "color": P2_COLOR, "shape": "circle", "text": "P2 retest",
        },
    ] + skip_markers
    markers.sort(key=lambda m: m["time"])

    confluence_color = CONFLUENCE_COLOR_LHPB if is_lhpb else CONFLUENCE_COLOR_LLPB
    price_lines = [{
        "price": price, "color": LEVEL_COLOR, "lineWidth": 2,
        "lineStyle": 0, "title": f"{level_type} {price:.2f} (this level)",
    }]
    for p in hints["confluence_prices"]:
        price_lines.append({
            "price": p, "color": confluence_color, "lineWidth": 1,
            "lineStyle": 2, "title": f"{level_type} {p:.2f}",
        })

    title = (f"{level_type} {price:.2f}  |  formed {row['formation_time']}  "
             f"broke {row['breakout_time']}  retest {row['retest_time']}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"

    return {
        "title": title,
        "candles": candles, "markers": markers, "priceLines": price_lines,
        "precision": 2,
    }


def build_rows(h1_df, retests_df, touch_lv1_df, n_ticks, tick_size):
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    rng = (h1_df["high"] - h1_df["low"])
    avg_range_20 = rng.rolling(AVG_RANGE_WINDOW).mean().shift(1)
    avg_range_20 = avg_range_20.to_dict()

    all_broken = build_all_broken_out(retests_df, touch_lv1_df)

    rows_meta = []
    charts = []
    for i, row in retests_df.reset_index(drop=True).iterrows():
        hints = compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken, n_ticks, tick_size)
        chart = build_row_chart(h1_df, pos_by_ts, row, hints)
        charts.append(chart)

        key = f"{row['type']}|{row['price']:.2f}|{_to_epoch_utc(row['formation_time'])}"
        rows_meta.append({
            "idx": i, "key": key,
            "type": row["type"], "price": float(row["price"]),
            "formation_time": str(row["formation_time"]),
            "breakout_time": str(row["breakout_time"]),
            "retest_time": str(row["retest_time"]),
            "entry_price": float(row["entry_price"]),
            "fta": float(row["fta"]) if row["fta"] == row["fta"] else None,
            "stop_loss": float(row["stop_loss"]),
            "hints": hints,
        })
    return rows_meta, charts


CSS = """
<style>
:root { --bg:#111316; --surface:#1c1f24; --surface2:#22262d; --border:#2e333b;
        --text:#d4d8df; --text-dim:#6b7280; --text-faint:#444c58;
        --bull:#4ade80; --bear:#f87171; --accent:#60a5fa; --done:#16321f; --invalid:#3a1414; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       padding:16px 24px; max-width:1700px; margin:0 auto;
       background:var(--bg); color:var(--text); }
h1 { font-size:1.4em; margin:0 0 4px; color:#e8eaed; }
p.lead { color:var(--text-dim); margin:0 0 12px; font-size:0.87em; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0; align-items:center; }
.summary .box { padding:8px 14px; background:var(--surface);
                border:1px solid var(--border); border-radius:6px;
                font-size:0.82em; line-height:1.3; }
.summary .box strong { display:block; font-size:1.4em; color:#e8eaed; }
.summary .box.true strong { color:var(--bull); }
.toolbar { display:flex; gap:8px; margin-left:auto; }
.btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
       border-radius:5px; padding:6px 12px; cursor:pointer; font-size:0.82em; }
.btn:hover { border-color:var(--accent); }
.filter-panel { background:var(--surface); border:1px solid var(--border);
                border-radius:6px; padding:10px 14px; margin-bottom:14px; }
.filter-row { display:flex; align-items:center; gap:10px; margin:4px 0; }
.filter-label { color:var(--text-dim); width:80px; font-size:0.85em; }
.chip { background:var(--surface2); border:1px solid var(--border);
        border-radius:14px; padding:3px 10px; cursor:pointer; user-select:none;
        font-size:0.85em; }
.chip input { margin-right:4px; }
table { width:100%; border-collapse:collapse; font-size:0.8em;
        background:var(--surface); border:1px solid var(--border);
        border-radius:6px; }
th, td { border-bottom:1px solid var(--border); padding:5px 7px;
         text-align:center; white-space:nowrap; vertical-align:top; }
th { background:var(--surface2); font-weight:600; color:var(--text-dim);
     letter-spacing:.02em; font-size:0.82em;
     position:sticky; top:0; z-index:2; }
td.left, th.left { text-align:left; }
tr.lvl-row:hover td { background:rgba(255,255,255,.03); cursor:pointer; }
tr.lvl-row.type-lhpb td.type-cell { color:var(--bull); font-weight:600; }
tr.lvl-row.type-llpb td.type-cell { color:var(--bear); font-weight:600; }
tr.lvl-row.is-reviewed td { background:var(--done); }
tr.lvl-row.is-invalid td { background:var(--invalid); }
.hint { color:var(--text-faint); font-size:0.85em; display:block; }
.expand-cell { text-align:center; }
.expand-btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
              border-radius:4px; padding:2px 8px; cursor:pointer; }
.expand-btn.open { background:#1e3a5f; color:#7bb4f5; border-color:#1d3a5c; }
tr.detail-row td.detail-cell { background:#0a0c0e; padding:10px 12px; border-top:none; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px;
              overflow:hidden; height:340px; }
.chart-title { color:#cccccc; padding:5px 8px; font-size:0.75em;
               font-family:ui-monospace,monospace; background:#0a0a0a;
               border-bottom:1px solid #1f1f1f; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 24px); width:100%; }
textarea.misc-note { width:140px; height:34px; resize:vertical; background:var(--surface2);
                      color:var(--text); border:1px solid var(--border); border-radius:4px;
                      font-size:0.9em; padding:3px 5px; }
.hidden { display:none !important; }
</style>
"""


# Every checkbox column is driven off this single list -- add an entry here
# (and nowhere else) to add a new hand-eval feature.
FEATURES = [
    {"id": "phase0_spike", "label": "P0 Spike", "hint_key": "is_spike",
     "hint_fmt": "auto: {v}"},
    {"id": "phase1_wide_breakout", "label": "P1 Wide Breakout", "hint_key": "range_ratio",
     "hint_fmt": "range/avg={v}x"},
    {"id": "confluence_cluster", "label": "Confluence Cluster", "hint_key": "confluence_count",
     "hint_fmt": "{v} nearby"},
    {"id": "large_wick", "label": "Large Wick", "hint_key": "wick_pct",
     "hint_fmt": "wick={v}%"},
    {"id": "fast_retest", "label": "Fast Retest", "hint_key": "bars_to_retest",
     "hint_fmt": "{v} bars"},
]


def build_page(rows_meta, title, n_ticks, tick_size):
    n_lhpb = sum(1 for r in rows_meta if r["type"] == "LHPB")
    n_llpb = len(rows_meta) - n_lhpb

    header = f"""
<h1>{title}</h1>
<p class="lead">Each row is a completed LXPB retest. Phase 0 = formation bar (level created),
Phase 1 = breakout bar, Phase 2 = retest bar -- marked with dots/arrows on the expandable H1
chart, which spans {BARS_BEFORE} bars before Phase 0 through {BARS_AFTER} bars after Phase 2.
Gold line = this row's own level; blue/red dashed lines = other same-type LXPB levels within
{n_ticks} ticks ({n_ticks * tick_size:.2f} pts) broken out by the retest time, for judging
confluence clusters. Each feature checkbox (and the overall "Valid" verdict) is pre-checked
from a computed default -- the small grey hint text explains why -- but the reviewer's tick is
final and can flip any of them. Labels persist in this browser's localStorage and can be
exported/imported as CSV (top-right buttons).</p>
<div class="summary">
  <div class="box true"><strong id="sum-total">{len(rows_meta)}</strong>Levels</div>
  <div class="box"><strong>{n_lhpb}</strong>LHPB</div>
  <div class="box"><strong>{n_llpb}</strong>LLPB</div>
  <div class="box"><strong id="sum-reviewed">0</strong>Reviewed</div>
  <div class="box"><strong id="sum-invalid">0</strong>Marked Invalid</div>
  <div class="toolbar">
    <button class="btn" onclick="exportCsv()">⬇ Export labels CSV</button>
    <label class="btn" for="import-file">⬆ Import labels CSV</label>
    <input type="file" id="import-file" accept=".csv" class="hidden" onchange="importCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved labels in this browser?')) clearAll();">🗑 Clear all</button>
  </div>
</div>
"""

    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Type</span>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LHPB" checked> LHPB</label>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LLPB" checked> LLPB</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="reviewed" checked> Reviewed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Validity</span>
    <label class="chip"><input type="checkbox" class="f-cb f-valid" value="valid" checked> Valid</label>
    <label class="chip"><input type="checkbox" class="f-cb f-valid" value="invalid" checked> Invalid</label>
  </div>
</div>
"""

    feature_ths = "".join(f'<th>{f["label"]}</th>' for f in FEATURES)
    thead = f"""
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Type</th><th class="left">Formed</th>
  <th>Price</th><th class="left">Breakout</th><th class="left">Retest</th>
  <th>FTA</th><th>Stop</th><th>Reviewed</th><th>Valid</th>
  {feature_ths}
  <th class="left">Misc notes</th><th class="expand-th">▶</th>
</tr></thead>
<tbody>
"""

    rows_html = []
    for r in rows_meta:
        type_cls = "type-lhpb" if r["type"] == "LHPB" else "type-llpb"
        fta_str = f"{r['fta']:.2f}" if r["fta"] is not None else "-"
        feature_cells = "".join(
            f'<td><input type="checkbox" class="feat-cb" data-feat="{f["id"]}" '
            f'data-default="{1 if r["hints"]["defaults"].get(f["id"]) else 0}">'
            f'<span class="hint">{f["hint_fmt"].format(v=r["hints"].get(f["hint_key"]))}</span></td>'
            for f in FEATURES
        )
        rows_html.append(f"""
<tr class="lvl-row {type_cls}" data-idx="{r['idx']}" data-key="{r['key']}" data-type="{r['type']}"
    onclick="toggleChart({r['idx']})">
  <td class="left">{r['idx']}</td><td class="left type-cell">{r['type']}</td>
  <td class="left">{r['formation_time']}</td><td>{r['price']:.2f}</td>
  <td class="left">{r['breakout_time']}</td><td class="left">{r['retest_time']}</td>
  <td>{fta_str}</td><td>{r['stop_loss']:.2f}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" data-default="{1 if r['hints']['valid_default'] else 0}"></td>
  {feature_cells}
  <td class="left" onclick="event.stopPropagation();"><textarea class="misc-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{r['idx']}"
      onclick="event.stopPropagation();toggleChart({r['idx']})">▶</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{r['idx']}" id="chart-row-{r['idx']}">
  <td colspan="{10 + len(FEATURES) + 2}">
    <div class="chart-cell"><div class="chart-title" id="tc-{r['idx']}"></div>
      <div class="chart-ph" id="cc-{r['idx']}"></div></div>
  </td>
</tr>
""")

    tbody_close = "</tbody></table>"
    return header, filter_panel, thead, "".join(rows_html), tbody_close


JS_TEMPLATE = """
<script>
const CHARTS = __CHARTS_JSON__;
const ROWS = __ROWS_JSON__;
const FEATURES = __FEATURES_JSON__;
const STORAGE_KEY = 'lxpb_labels_v1';
const rendered = {};
// Fixed candle width in pixels so charts with few candles show blank
// space on either side instead of stretching each candle to fill the
// whole row. Kept intentionally tight/close together; the reviewer can
// still manually drag/scroll-zoom the time axis afterward (see
// _renderChart -- we manage chart sizing ourselves instead of fighting
// the user's own zoom with a continuous watchdog).
const FIXED_BAR_SPACING = 6;

function loadStore() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveStore(store) { localStorage.setItem(STORAGE_KEY, JSON.stringify(store)); }

function rowState(tr) {
  const state = { reviewed: tr.querySelector('.reviewed-cb').checked,
                  valid: tr.querySelector('.valid-cb').checked,
                  misc: tr.querySelector('.misc-note').value };
  FEATURES.forEach(f => {
    const cb = tr.querySelector('.feat-cb[data-feat="' + f.id + '"]');
    state[f.id] = cb ? cb.checked : false;
  });
  return state;
}

function applyDefaults(tr) {
  // Pre-fill each checkbox from its computed default (see `defaults` /
  // `valid_default` in compute_hints) -- only a starting guess; any saved
  // localStorage state (applied right after this) always wins.
  const validCb = tr.querySelector('.valid-cb');
  validCb.checked = validCb.dataset.default === '1';
  tr.querySelectorAll('.feat-cb').forEach(cb => { cb.checked = cb.dataset.default === '1'; });
  tr.classList.toggle('is-invalid', !validCb.checked);
}

function applyRowState(tr, state) {
  if (!state) return;
  if (state.reviewed !== undefined) tr.querySelector('.reviewed-cb').checked = !!state.reviewed;
  if (state.valid !== undefined) tr.querySelector('.valid-cb').checked = !!state.valid;
  if (state.misc !== undefined) tr.querySelector('.misc-note').value = state.misc || '';
  FEATURES.forEach(f => {
    if (state[f.id] === undefined) return;
    const cb = tr.querySelector('.feat-cb[data-feat="' + f.id + '"]');
    if (cb) cb.checked = !!state[f.id];
  });
  tr.classList.toggle('is-reviewed', !!tr.querySelector('.reviewed-cb').checked);
  tr.classList.toggle('is-invalid', !tr.querySelector('.valid-cb').checked);
}

function persistRow(tr) {
  const store = loadStore();
  store[tr.dataset.key] = rowState(tr);
  saveStore(store);
  tr.classList.toggle('is-reviewed', !!store[tr.dataset.key].reviewed);
  tr.classList.toggle('is-invalid', !store[tr.dataset.key].valid);
  updateSummary();
}

function updateSummary() {
  const total = document.querySelectorAll('.lvl-row').length;
  const reviewed = document.querySelectorAll('.lvl-row.is-reviewed').length;
  const invalid = document.querySelectorAll('.lvl-row.is-invalid').length;
  document.getElementById('sum-reviewed').textContent = reviewed;
  document.getElementById('sum-invalid').textContent = invalid;
}

function initRows() {
  const store = loadStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .feat-cb, .misc-note').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr));
  });
  updateSummary();
}

function csvEscape(v) {
  v = (v === null || v === undefined) ? '' : String(v);
  return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

function exportCsv() {
  const store = loadStore();
  const featCols = FEATURES.map(f => f.id);
  const header = ['key', 'idx', 'type', 'price', 'formation_time', 'breakout_time',
                   'retest_time', 'reviewed', 'valid'].concat(featCols).concat(['misc']);
  const lines = [header.join(',')];
  ROWS.forEach(r => {
    const st = store[r.key] || {};
    const row = [r.key, r.idx, r.type, r.price, r.formation_time, r.breakout_time,
                 r.retest_time, st.reviewed ? 1 : 0, st.valid ? 1 : 0]
      .concat(featCols.map(f => (st[f] ? 1 : 0)))
      .concat([st.misc || '']);
    lines.push(row.map(csvEscape).join(','));
  });
  const blob = new Blob([lines.join('\\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'lxpb_labels.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function parseCsvLine(line) {
  const out = []; let cur = ''; let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQ) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') { inQ = false; }
      else { cur += c; }
    } else {
      if (c === '"') inQ = true;
      else if (c === ',') { out.push(cur); cur = ''; }
      else cur += c;
    }
  }
  out.push(cur);
  return out;
}

function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const store = loadStore();
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      const key = rec.key;
      if (!key) continue;
      const state = { misc: rec.misc || '' };
      if (header.includes('reviewed')) state.reviewed = rec.reviewed === '1';
      if (header.includes('valid')) state.valid = rec.valid === '1';
      FEATURES.forEach(f => { if (header.includes(f.id)) state[f.id] = rec[f.id] === '1'; });
      store[key] = state;
    }
    saveStore(store);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, store[tr.dataset.key]));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}

function clearAll() {
  localStorage.removeItem(STORAGE_KEY);
  document.querySelectorAll('.lvl-row').forEach(tr => applyDefaults(tr));
  updateSummary();
}

function _baseOpts() {
  return {
    layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
              fontFamily:"'Courier New', monospace", fontSize:9 },
    grid: { vertLines:{visible:false}, horzLines:{visible:false} },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.08, bottom:0.08} },
    timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:false },
  };
}

function _renderChart(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  const el = document.getElementById('cc-' + i);
  const titleEl = document.getElementById('tc-' + i);
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  // autoSize is intentionally NOT used here: its internal ResizeObserver
  // re-fits the visible range to exactly fill the container width on
  // every resize (including right after this row is un-hidden), which
  // would either stretch our fixed candle width back out, or (if fought
  // with a watchdog on visible-range-change) also clobber the reviewer's
  // own manual drag/scroll-zoom on the time axis. Instead we size the
  // chart ourselves via our own ResizeObserver below, so a real container
  // resize (row opened, window resized) and a user zoom gesture are two
  // clearly distinct events and never fight each other.
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(), {
    width: el.clientWidth || 800,
    height: el.clientHeight || 320,
  }));
  const series = chart.addCandlestickSeries({
    upColor:'#DDDDD0', downColor:'#888888',
    borderUpColor:'#DDDDD0', borderDownColor:'#888888',
    wickUpColor:'#DDDDD0', wickDownColor:'#888888',
    priceFormat: { type:'price', precision: cd.precision || 2, minMove: 0.25 },
  });
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
  const ro = new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) {
      chart.resize(r.width, r.height);
      chart.timeScale().applyOptions({ barSpacing: FIXED_BAR_SPACING });
    }
  });
  ro.observe(el);
}


function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.textContent = opening ? '▼' : '▶'; }
  if (opening && !rendered[i]) { _renderChart(i); rendered[i] = true; }
}

function applyFilters() {
  const typeOn = Array.from(document.querySelectorAll('.f-type:checked')).map(c => c.value);
  const statusOn = Array.from(document.querySelectorAll('.f-status:checked')).map(c => c.value);
  const validOn = Array.from(document.querySelectorAll('.f-valid:checked')).map(c => c.value);
  document.querySelectorAll('.lvl-row').forEach(function(tr) {
    const isReviewed = tr.classList.contains('is-reviewed');
    const isInvalid = tr.classList.contains('is-invalid');
    const statusOk = statusOn.includes(isReviewed ? 'reviewed' : 'unreviewed');
    const validOk = validOn.includes(isInvalid ? 'invalid' : 'valid');
    const show = typeOn.includes(tr.dataset.type) && statusOk && validOk;
    tr.classList.toggle('hidden', !show);
    if (!show) {
      const cr = document.getElementById('chart-row-' + tr.dataset.idx);
      if (cr) cr.classList.add('hidden');
    }
  });
}
document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));

initRows();
</script>
"""


def render(data_path, output_path, title, n_ticks, tick_size, start=None, end=None,
           limit=300, order="desc"):
    h1_df = L.load_ohlc_data(data_path)
    _touch_lv0, touch_lv1_df, retests_df = L.detect_lxpb_h1(h1_df)

    if start:
        retests_df = retests_df[retests_df["retest_time"] >= pd.Timestamp(start)]
    if end:
        retests_df = retests_df[retests_df["retest_time"] <= pd.Timestamp(end)]

    retests_df = retests_df.sort_values("retest_time", ascending=(order == "asc"))
    if limit:
        retests_df = retests_df.head(limit)
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)

    print(f"Loaded {len(h1_df)} H1 bars ({h1_df.index.min()} -> {h1_df.index.max()})")
    print(f"Completed retests total: touch_lv1 open={len(touch_lv1_df)}; "
          f"rendering {len(retests_df)} rows")

    rows_meta, charts = build_rows(h1_df, retests_df, touch_lv1_df, n_ticks, tick_size)

    header, filter_panel, thead, rows_html, tbody_close = build_page(
        rows_meta, title, n_ticks, tick_size)

    js = (JS_TEMPLATE
          .replace("__CHARTS_JSON__", json.dumps(charts))
          .replace("__ROWS_JSON__", json.dumps(rows_meta))
          .replace("__FEATURES_JSON__", json.dumps(FEATURES)))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
{CSS}
</head><body>
{header}
{filter_panel}
{thead}
{rows_html}
{tbody_close}
{js}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate LXPB hand-labeling HTML report")
    parser.add_argument("--data", default=DEFAULT_DATA, help="H1 OHLC CSV path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--title", default="LXPB Hand-Labeling Report",
                         help="Report page title")
    parser.add_argument("--n-ticks", type=int, default=N_TICKS_DEFAULT,
                         help="Confluence radius in ticks (default 20)")
    parser.add_argument("--tick-size", type=float, default=TICK_SIZE_DEFAULT,
                         help="Instrument tick size (default 0.25, ES)")
    parser.add_argument("--start", default="2026-01-01",
                         help="Only retests on/after this date (default: 2026-01-01, "
                              "i.e. only 2026 retests -- formation/breakout can predate this)")
    parser.add_argument("--end", default=None, help="Only retests on/before this date")
    parser.add_argument("--limit", type=int, default=300,
                         help="Max number of rows (0 = all). Selects the most "
                              "recent N by default (see --order).")
    parser.add_argument("--order", choices=["asc", "desc"], default="desc",
                         help="Which end of the (optionally date-filtered) retest "
                              "history --limit keeps: 'desc'=most recent (default), "
                              "'asc'=earliest")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"Error: {args.data} not found.")
        sys.exit(1)

    render(args.data, args.output, args.title, args.n_ticks, args.tick_size,
           start=args.start, end=args.end, limit=args.limit, order=args.order)
