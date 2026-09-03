"""HTML report for the spike-only-beyond-ATR LXPB strategy.

Deliberately a *skin* over ``lxpb-v2/render_labels_report.py`` rather than a
new report: the page reuses that module's ``CSS``, ``JS_TEMPLATE``,
``FEATURES``, ``compute_hints`` and ``build_1s_trio_chart`` verbatim, so the
expandable H1 + 1s trio (price/bid/ask) + 1min + footprint chart stack, the
crosshair legends, the PT (America/Los_Angeles) tooltips and axis labels, the
localStorage-backed hand-labeling, the CSV export/import and the filter chips
all behave exactly like ``lxpb_strong_breakout_report.html``.

What actually differs:

* the *rows* are this strategy's trades (spike-formation LXPB retests) rather
  than the strong-breakout subset;
* the H1 chart is extended past the retest to the resolved exit bar and
  carries the trade's own price lines -- structural stop, FTA target, the
  moving ATR boundary and the day extreme it is measured from -- on top of
  the level/confluence lines the labeling report already draws;
* extra columns carry the bracket (stop/target/R:R), the ATR-boundary test
  and the resolved outcome, and two extra filter chip rows (ATR verdict,
  outcome) are wired into the inherited ``applyFilters``.

Strategy definition and CLI flags: see ``analyze_spike_atr_strategy.py``.

Usage
-----
    python render_spike_atr_report.py                    # qualifying trades only
    python render_spike_atr_report.py --all-spikes       # every spike retest, PASS/FAIL shown
    python render_spike_atr_report.py --no-ticks         # fast, H1 charts only
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(os.path.dirname(_HERE), "lxpb-v2"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

import analyze_spike_atr_strategy as X
import render_labels_report as R
import render_stop_target_report as S

DEFAULT_OUTPUT = os.path.join(_HERE, "spike_atr_trades_report.html")

BOUNDARY_COLOR = "#fb923c"     # the moving ATR boundary the stop must clear
DAY_EXTREME_COLOR = "#94a3b8"  # day high/low so far, the boundary's anchor
TARGET_COLOR = S.EXIT_WIN_COLOR
STOP_COLOR = S.EXIT_LOSS_COLOR

OUTCOME_LABEL = {"target": "WIN", "stop": "LOSS", "no_hit": "NO-HIT", "no_data": "NO DATA"}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def build_trade_chart(h1_df, pos_by_ts, row, resolved, hints):
    """render_labels_report.build_row_chart's window/markers/confluence lines,
    extended forward to the resolved exit bar (as render_stop_target_report
    does) and annotated with this trade's structural bracket and the ATR
    boundary it had to clear."""
    level_type = row["type"]
    price = float(row["price"])
    is_long = level_type == "LHPB"
    form_pos = pos_by_ts[row["formation_time"]]
    breakout_pos = pos_by_ts[row["breakout_time"]]
    retest_pos = pos_by_ts[row["retest_time"]]
    n_bars = len(h1_df)

    exit_time = resolved["exit_time"]
    if exit_time is not None:
        # h1_df's index is tz-naive (see lxpb.load_ohlc_data) while exit_time
        # is tz-aware UTC off the 1-min tick series -- same instant, so just
        # drop the tz label before comparing.
        exit_naive = exit_time.tz_localize(None) if exit_time.tzinfo is not None else exit_time
        exit_pos = min(int(h1_df.index.searchsorted(exit_naive, side="right")) - 1, n_bars - 1)
        exit_pos = max(exit_pos, retest_pos)
    else:
        exit_pos = retest_pos

    retest_after = max(S.BARS_AFTER_MIN, (exit_pos - retest_pos) + S.BARS_AFTER_EXIT)
    segments = [
        (form_pos - R.BARS_BEFORE, form_pos + R.CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - R.CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + R.CONTEXT_BARS_AFTER_BREAKOUT),
        (retest_pos - R.CONTEXT_BARS_BEFORE_RETEST, retest_pos + retest_after),
    ]
    merged = R._merge_segments(segments, n_bars, R.MAX_MERGE_GAP)

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
    r_val = resolved["r"]
    if outcome == "target":
        exit_text = f"WIN {r_val:+.2f}R"
        exit_color, exit_shape = S.EXIT_WIN_COLOR, ("arrowUp" if is_long else "arrowDown")
        exit_pos_label = "aboveBar" if is_long else "belowBar"
    elif outcome == "stop":
        exit_text = "LOSS -1.00R"
        exit_color, exit_shape = S.EXIT_LOSS_COLOR, ("arrowDown" if is_long else "arrowUp")
        exit_pos_label = "belowBar" if is_long else "aboveBar"
    else:
        exit_text = OUTCOME_LABEL[outcome]
        exit_color, exit_shape = "#9ca3af", "circle"
        exit_pos_label = "inBar"

    markers = [
        {"time": R._to_epoch_utc(row["formation_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P0_COLOR, "shape": "circle", "text": "P0 form (spike)"},
        {"time": R._to_epoch_utc(row["breakout_time"]),
         "position": "belowBar" if is_long else "aboveBar",
         "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
         "shape": "arrowUp" if is_long else "arrowDown", "text": "P1 breakout"},
        {"time": R._to_epoch_utc(row["retest_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P2_COLOR, "shape": "circle", "text": "P2 retest (entry)"},
        {"time": R._to_epoch_utc(h1_df.index[exit_pos]),
         "position": exit_pos_label, "color": exit_color, "shape": exit_shape, "text": exit_text},
    ] + skip_markers
    markers.sort(key=lambda m: m["time"])

    stop_price = float(row["stop_loss"])
    target_price = float(row["fta"])
    stop_pts = abs(price - stop_price)
    target_pts = abs(target_price - price)
    confluence_color = R.CONFLUENCE_COLOR_LHPB if is_long else R.CONFLUENCE_COLOR_LLPB

    price_lines = [
        {"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
         "title": f"{level_type} {price:.2f} (entry)"},
        {"price": target_price, "color": TARGET_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"FTA target {target_price:.2f} (+{target_pts:.2f}pt)"},
        {"price": stop_price, "color": STOP_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"stop {stop_price:.2f} (-{stop_pts:.2f}pt, beyond breakout bar)"},
    ]
    if row.get("atr_boundary") is not None and row["atr_boundary"] == row["atr_boundary"]:
        extreme = float(row["day_high_sofar"] if is_long else row["day_low_sofar"])
        price_lines.append({
            "price": float(row["atr_boundary"]), "color": BOUNDARY_COLOR,
            "lineWidth": 1, "lineStyle": 3,
            "title": f"ATR boundary {float(row['atr_boundary']):.2f} "
                     f"({'high' if is_long else 'low'} so far {'-' if is_long else '+'} "
                     f"{float(row['atr']):.2f} ATR)",
        })
        price_lines.append({
            "price": extreme, "color": DAY_EXTREME_COLOR, "lineWidth": 1, "lineStyle": 3,
            "title": f"day {'high' if is_long else 'low'} so far {extreme:.2f}",
        })
    for p in hints["confluence_prices"]:
        price_lines.append({
            "price": p, "color": confluence_color, "lineWidth": 1, "lineStyle": 2,
            "title": f"{level_type} {p:.2f}",
        })

    title = (f"{level_type} {price:.2f}  |  formed {R._to_pt_str(row['formation_time'])}  "
             f"broke {R._to_pt_str(row['breakout_time'])}  retest {R._to_pt_str(row['retest_time'])}"
             f"  |  {OUTCOME_LABEL[outcome]}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"

    return {"title": title, "candles": candles, "markers": markers,
            "priceLines": price_lines, "precision": 2}


def _relabel_fta(chart):
    """build_1s_trio_chart draws row['fta'] as a line titled 'fta ...'. Here
    the FTA *is* the target, so say so."""
    if not chart:
        return
    for pl in chart.get("priceLines", []):
        if pl.get("title", "").startswith("fta "):
            pl["title"] = "FTA target " + pl["title"][4:]


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def build_rows(h1_df, rows_df, all_broken, resolved_list, n_ticks, tick_size,
               pad_seconds=R.PAD_SECONDS_DEFAULT,
               one_min_pad_minutes=R.ONE_MIN_PAD_MINUTES_DEFAULT,
               include_footprint=True, include_ticks=True):
    """render_labels_report.build_rows, but with this strategy's chart and
    the resolved-trade fields merged into each row's metadata. The 1s trio is
    centred on the *actual fill instant* (`touch_time` from the 1-min walk
    forward) rather than the naive any-side H1 touch."""
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    rng = h1_df["high"] - h1_df["low"]
    avg_range_20 = rng.rolling(R.AVG_RANGE_WINDOW).mean().shift(1).to_dict()

    rows_df = rows_df.reset_index(drop=True)
    cluster_size = rows_df.groupby("retest_time")["retest_time"].transform("size")
    cluster_rank = (rows_df.groupby("retest_time")["formation_time"]
                    .rank(ascending=False, method="first").astype(int))
    cluster_ord = pd.factorize(rows_df["retest_time"])[0]

    rows_meta, charts, footprints = [], [], []
    n_rows = len(rows_df)
    for i, row in rows_df.iterrows():
        resolved = resolved_list[i]
        hints = R.compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken, n_ticks, tick_size)
        h1_chart = build_trade_chart(h1_df, pos_by_ts, row, resolved, hints)

        trio_chart = None
        if include_ticks:
            trio_chart = R.build_1s_trio_chart(
                row, pad_seconds, one_min_pad_minutes, include_footprint,
                touch_time_override=resolved.get("touch_time"))
        if trio_chart is not None:
            _relabel_fta(trio_chart["trio"])
            _relabel_fta(trio_chart["oneMin"])
            chart = {"h1": h1_chart, "trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
            fp = {"narrow": trio_chart.get("footprintNarrowHtml"),
                  "wide": trio_chart.get("footprintWideHtml")}
        else:
            chart = {"h1": h1_chart, "trio": None, "oneMin": None}
            note = "(tick charts disabled)" if not include_ticks else "(no tick data in this window)"
            fp = {"narrow": f"<p class='note'>{note}</p>",
                  "wide": f"<p class='note'>{note}</p>"} if include_footprint else None
        charts.append(chart)
        footprints.append(fp)
        if (i + 1) % 5 == 0 or (i + 1) == n_rows:
            print(f"  built charts for {i + 1}/{n_rows} rows")

        is_long = row["type"] == "LHPB"
        entry = float(row["entry_price"])
        stop_pts = abs(entry - float(row["stop_loss"]))
        target_pts = abs(float(row["fta"]) - entry)
        has_atr = row["atr"] is not None and row["atr"] == row["atr"]

        rows_meta.append({
            "idx": i,
            "key": f"{row['type']}|{row['price']:.2f}|{R._to_epoch_utc(row['formation_time'])}",
            "type": row["type"], "price": float(row["price"]),
            "formation_time": R._to_pt_str(row["formation_time"]),
            "breakout_time": R._to_pt_str(row["breakout_time"]),
            "retest_time": R._to_pt_str(row["retest_time"]),
            "entry_time": (R._to_pt_str(resolved["touch_time"])
                           if resolved.get("touch_time") is not None
                           else R._to_pt_str(row["retest_time"])),
            "entry_price": entry,
            "fta": float(row["fta"]),
            "stop_loss": float(row["stop_loss"]),
            "stop_pts": stop_pts, "target_pts": target_pts,
            "rr": (target_pts / stop_pts) if stop_pts else None,
            "atr": float(row["atr"]) if has_atr else None,
            "day_extreme": (float(row["day_high_sofar"] if is_long else row["day_low_sofar"])
                            if has_atr else None),
            "atr_boundary": float(row["atr_boundary"]) if has_atr else None,
            "entry_atr_margin": float(row["entry_atr_margin"]) if has_atr else None,
            "stop_atr_margin": float(row["stop_atr_margin"]) if has_atr else None,
            "beyond_atr": bool(row["beyond_atr"]),
            "beyond_atr_leg": row["beyond_atr_leg"],
            "outcome": resolved["outcome"], "r": resolved["r"],
            "exit_time": (R._to_pt_str(resolved["exit_time"])
                          if resolved["exit_time"] is not None else None),
            "hints": hints,
            "cluster_size": int(cluster_size.iloc[i]), "cluster_rank": int(cluster_rank.iloc[i]),
            "cluster_ord": int(cluster_ord[i]),
        })
    return rows_meta, charts, footprints


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

EXTRA_CSS = """
<style>
  .pill { display:inline-block; padding:1px 7px; border-radius:9px; font-size:11px; font-weight:600; }
  .pill.pass { background:#14532d; color:#bbf7d0; }
  .pill.fail { background:#3f3f46; color:#d4d4d8; }
  .pill.win  { background:#14532d; color:#bbf7d0; }
  .pill.loss { background:#7f1d1d; color:#fecaca; }
  .pill.flat { background:#3f3f46; color:#d4d4d8; }
  td.num-pos { color:#4ade80; }
  td.num-neg { color:#f87171; }
  .funnel { margin:10px 0 4px; font-size:12px; color:#a1a1aa; }
  .funnel code { color:#fcd34d; }
</style>
"""


def _fmt(v, nd=2, signed=False):
    if v is None or v != v:
        return '<span class="text-faint">-</span>'
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


def build_page(rows_meta, title, n_ticks, tick_size, funnel, stats, params, footprints=None):
    n_lhpb = sum(1 for r in rows_meta if r["type"] == "LHPB")
    n_llpb = len(rows_meta) - n_lhpb
    n_pass = sum(1 for r in rows_meta if r["beyond_atr"])
    n_unique_bars = len({r["retest_time"] for r in rows_meta})
    n_clustered_rows = sum(1 for r in rows_meta if r.get("cluster_size", 1) >= 2)
    wr = stats["win_rate"]
    wr_str = f"{wr * 100:.1f}%" if wr == wr else "-"

    header = f"""
<h1>{title}</h1>
<p class="lead"><b>spike-only-beyond-ATR.</b> Each row is a completed LXPB retest whose
<b>formation bar is a spike</b> (patterns-pure <code>find_shooting_star</code> for LHPB /
<code>find_hammer</code> for LLPB). Entry is the level itself on the Phase&nbsp;2 retest; the
<b>stop is beyond the breakout candle</b> (its low for LHPB, its high for LLPB) and the
<b>target is the FTA</b> (first trouble area &mdash; the running extreme carved out between
breakout and retest). A level is only traded when <b>price is beyond the moving daily-ATR
boundary &mdash; either already at entry, or by the time it would reach the stop</b>:
ATR({X.ATR_PERIOD}) over D1 candles built by <code>calculateD1W1</code> that closed
before the trade's own session, subtracted from the <b>high of the trading day so far</b>
(LHPB) or added to the <b>low so far</b> (LLPB), measured strictly point-in-time &mdash; only
bars already closed plus the retest bar's open and the level price, never the retest bar's own
high/low. Either way the market has to exceed a full ATR of daily range, measured from the
extreme it has already printed, before the trade can be stopped out. (For a normally-oriented
bracket the stop leg subsumes the entry leg, so the <b>Beyond ATR?</b> pill usually reads
<code>BEYOND @stop</code>; both margins are shown so you can see which leg carried it.)</p>
<p class="lead">Phase 0 = formation (spike), Phase 1 = breakout, Phase 2 = retest/entry, plus a
4th marker at the resolved exit &mdash; all on the expandable H1 chart, which spans
{R.BARS_BEFORE} bars before Phase 0 through the exit bar (+{S.BARS_AFTER_EXIT}). Gold line = this
row's level/entry, green dashed = FTA target, red dashed = structural stop, orange dotted = the
ATR boundary, grey dotted = the day extreme it is measured from, and blue/red dashed = other
same-type LXPB levels within {n_ticks} ticks ({n_ticks * tick_size:.2f} pts) already broken out by
the retest time. Outcomes come from a 1-minute walk-forward with the exact fill pinned on 1s
real ticks (look-ahead free: nothing before the fill instant can resolve the trade). All times
are <b>PT</b>. The <b>Cluster</b> column/blue banding flags rows sharing the same retest H1 bar
&mdash; {n_clustered_rows} of {len(rows_meta)} rows here, collapsing to {n_unique_bars} unique
retest bars. Feature checkboxes and the "Valid" verdict are pre-checked from computed defaults
(grey hint text explains why); labels persist in this browser's localStorage and export/import
as CSV.</p>
<div class="funnel">
  Window <code>{params['start']} .. {params['end']}</code> &middot;
  data <code>{os.path.basename(params['data'])}</code> &middot;
  ATR mult <code>{params['atr_mult']}</code> &middot;
  ATR D1 window <code>{params['atr_d1_window'] or 'all closed D1 bars'}</code><br>
  Funnel: {funnel['rows']} completed retests in window ({funnel['gap_rows_dropped']} gap rows
  dropped repo-wide) &rarr; {funnel['spike']} spike formations &rarr;
  {funnel['has_fta_and_stop']} with an FTA target + structural stop &rarr;
  {funnel['nonzero_bracket']} with a &ge;1-tick bracket on both legs &rarr;
  {funnel['oriented_bracket']} correctly oriented
  ({funnel['inverted_bracket_dropped']} inverted dropped) &rarr;
  <b>{funnel['beyond_atr']} beyond the ATR boundary at entry or at the stop</b>.
</div>
<div class="summary">
  <div class="box true"><strong id="sum-total">{len(rows_meta)}</strong>Trades</div>
  <div class="box"><strong id="sum-shown">{len(rows_meta)}</strong>Shown</div>
  <div class="box"><strong>{n_lhpb}</strong>LHPB (long)</div>
  <div class="box"><strong>{n_llpb}</strong>LLPB (short)</div>
  <div class="box"><strong>{n_pass}</strong>Beyond ATR</div>
  <div class="box"><strong>{stats['wins']}</strong>Wins</div>
  <div class="box"><strong>{stats['losses']}</strong>Losses</div>
  <div class="box"><strong>{stats['no_hit']}</strong>No-hit</div>
  <div class="box"><strong>{wr_str}</strong>Win rate</div>
  <div class="box"><strong>{_fmt(stats['avg_R'], 3, True)}</strong>Avg R</div>
  <div class="box"><strong>{_fmt(stats['total_R'], 2, True)}</strong>Total R</div>
  <div class="box"><strong>{_fmt(stats['avg_stop_pts'])}</strong>Avg stop pts</div>
  <div class="box"><strong>{_fmt(stats['avg_target_pts'])}</strong>Avg target pts</div>
  <div class="toolbar">
    <button class="btn" onclick="exportCsv()">&#11015; Export labels CSV</button>
    <label class="btn" for="import-file">&#11014; Import labels CSV</label>
    <input type="file" id="import-file" accept=".csv" class="hidden" onchange="importCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved labels in this browser?')) clearAll();">&#128465; Clear all</button>
  </div>
</div>
"""

    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Type</span>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LHPB" checked> LHPB (long)</label>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LLPB" checked> LLPB (short)</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">ATR boundary</span>
    <label class="chip"><input type="checkbox" class="f-cb f-atr" value="beyond" checked> Beyond ATR</label>
    <label class="chip"><input type="checkbox" class="f-cb f-atr" value="within" checked> Within ATR</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Outcome</span>
    <label class="chip"><input type="checkbox" class="f-cb f-out" value="target" checked> Win</label>
    <label class="chip"><input type="checkbox" class="f-cb f-out" value="stop" checked> Loss</label>
    <label class="chip"><input type="checkbox" class="f-cb f-out" value="no_hit" checked> No-hit</label>
    <label class="chip"><input type="checkbox" class="f-cb f-out" value="no_data" checked> No data</label>
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
  <div class="filter-row">
    <span class="filter-label">Breakout Strength</span>
    <label class="chip"><input type="checkbox" class="f-cb f-feat" data-feat="phase1_wide_breakout" value="on" checked> Strong</label>
    <label class="chip"><input type="checkbox" class="f-cb f-feat" data-feat="phase1_wide_breakout" value="off" checked> Not Strong</label>
  </div>
</div>
"""

    feature_ths = "".join(f'<th>{f["label"]}</th>' for f in R.FEATURES)
    thead = f"""
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Type</th><th class="left">Formed (spike)</th>
  <th>Level / entry</th><th class="left">Breakout</th><th class="left">Retest</th>
  <th class="left">Entry fill</th>
  <th>Stop</th><th>FTA target</th><th>Stop pts</th><th>Target pts</th><th>R:R</th>
  <th>ATR({X.ATR_PERIOD})</th><th>Day extreme</th><th>ATR boundary</th>
  <th>Entry margin</th><th>Stop margin</th>
  <th>Beyond ATR?</th><th>Outcome</th><th>R</th><th class="left">Exit</th>
  <th class="left">Cluster</th><th>Reviewed</th><th>Valid</th>
  {feature_ths}
  <th class="left">Misc notes</th><th class="expand-th">&#9654;</th>
</tr></thead>
<tbody>
"""
    n_cols = 24 + len(R.FEATURES) + 2

    rows_html = []
    for r in rows_meta:
        type_cls = "type-lhpb" if r["type"] == "LHPB" else "type-llpb"
        cluster_size = r.get("cluster_size", 1)
        band_cls = f" band-{r.get('cluster_ord', 0) % 2}" if cluster_size >= 2 else ""
        cluster_cell = (f'<span class="cluster-badge" title="This bar simultaneously retested '
                        f'{cluster_size} distinct LXPB levels; rank {r["cluster_rank"]} of '
                        f'{cluster_size} by how recently each was formed">{r["cluster_rank"]}/{cluster_size}</span>'
                        if cluster_size >= 2 else '<span class="text-faint">-</span>')
        feature_cells = "".join(
            f'<td><input type="checkbox" class="feat-cb" data-feat="{f["id"]}" '
            f'data-default="{1 if r["hints"]["defaults"].get(f["id"]) else 0}">'
            f'<span class="hint">{f["hint_fmt"].format(v=r["hints"].get(f["hint_key"]))}</span></td>'
            for f in R.FEATURES
        )
        fp = (footprints[r["idx"]] if footprints else None) or {}
        fp_narrow_html = fp.get("narrow")
        fp_wide_html = fp.get("wide")
        fp_section = (
            f'<div class="chart-row-2col footprint-outer-row">'
            f'<div class="footprint-pair">'
            f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
            f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
            f'</div>'
            f'<div class="chart-cell footprint-placeholder">'
            f'<div class="chart-title">(reserved for a future chart)</div></div>'
            f'</div>'
            if fp_narrow_html or fp_wide_html else ""
        )

        atr_pass = r["beyond_atr"]
        leg = r["beyond_atr_leg"]
        atr_pill = (f'<span class="pill {"pass" if atr_pass else "fail"}" '
                    f'title="{"price is already beyond the ATR boundary at entry" if leg == "entry" else ("price would be beyond the ATR boundary by the time it reached the stop" if leg == "stop" else "neither the entry nor the stop clears the ATR boundary")}">'
                    f'{("BEYOND @" + leg) if atr_pass else "within"}</span>')

        def _margin_cell(v):
            cls = "" if v is None or v != v else (" num-pos" if v > 0 else " num-neg")
            return f'<td class="{cls.strip()}">{_fmt(v, 2, True)}</td>'

        out_cls = {"target": "win", "stop": "loss"}.get(r["outcome"], "flat")
        r_val = r["r"]
        r_cls = "" if r_val is None else (" num-pos" if r_val > 0 else " num-neg")

        rows_html.append(f"""
<tr class="lvl-row {type_cls}{band_cls}" data-idx="{r['idx']}" data-key="{r['key']}" data-type="{r['type']}"
    data-atr="{'beyond' if atr_pass else 'within'}" data-outcome="{r['outcome']}"
    onclick="toggleChart({r['idx']})">
  <td class="left">{r['idx']}</td><td class="left type-cell">{r['type']}</td>
  <td class="left">{r['formation_time']}</td><td>{r['price']:.2f}</td>
  <td class="left">{r['breakout_time']}</td><td class="left">{r['retest_time']}</td>
  <td class="left">{r['entry_time']}</td>
  <td>{r['stop_loss']:.2f}</td><td>{r['fta']:.2f}</td>
  <td>{_fmt(r['stop_pts'])}</td><td>{_fmt(r['target_pts'])}</td><td>{_fmt(r['rr'])}</td>
  <td>{_fmt(r['atr'])}</td><td>{_fmt(r['day_extreme'])}</td><td>{_fmt(r['atr_boundary'])}</td>
  {_margin_cell(r['entry_atr_margin'])}{_margin_cell(r['stop_atr_margin'])}
  <td>{atr_pill}</td>
  <td><span class="pill {out_cls}">{OUTCOME_LABEL[r['outcome']]}</span></td>
  <td class="{r_cls.strip()}">{_fmt(r_val, 3, True)}</td>
  <td class="left">{r['exit_time'] or '<span class="text-faint">-</span>'}</td>
  <td class="left">{cluster_cell}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" data-default="{1 if r['hints']['valid_default'] else 0}"></td>
  {feature_cells}
  <td class="left" onclick="event.stopPropagation();"><textarea class="misc-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{r['idx']}"
      onclick="event.stopPropagation();toggleChart({r['idx']})">&#9654;</button></td>
</tr>
<tr class="chart-row hidden{band_cls}" data-idx="{r['idx']}" id="chart-row-{r['idx']}">
  <td colspan="{n_cols}"><div class="chart-stack">
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
    {fp_section}
  </div></td>
</tr>
""")

    return header, filter_panel, thead, "".join(rows_html), "</tbody></table>"


# The inherited applyFilters only knows Type/Status/Validity/Breakout-Strength.
# Rather than fork the whole JS (and risk the chart/tooltip code drifting from
# render_labels_report's), splice the two extra chip groups into it. Both
# anchors are asserted so an upstream edit fails loudly here instead of
# silently producing a report whose filters do nothing.
_FILTER_ANCHOR_DECL = (
    "  const featOn = Array.from(document.querySelectorAll("
    "'.f-feat[data-feat=\"phase1_wide_breakout\"]:checked')).map(c => c.value);"
)
_FILTER_ANCHOR_SHOW = (
    "    const show = typeOn.includes(tr.dataset.type) && statusOk && validOk && featOk;"
)


def build_js(charts, rows_meta):
    js = R.JS_TEMPLATE
    for anchor, replacement in (
        (_FILTER_ANCHOR_DECL, _FILTER_ANCHOR_DECL +
         "\n  const atrOn = Array.from(document.querySelectorAll('.f-atr:checked')).map(c => c.value);"
         "\n  const outOn = Array.from(document.querySelectorAll('.f-out:checked')).map(c => c.value);"),
        (_FILTER_ANCHOR_SHOW,
         "    const show = typeOn.includes(tr.dataset.type) && statusOk && validOk && featOk\n"
         "                 && atrOn.includes(tr.dataset.atr) && outOn.includes(tr.dataset.outcome);"),
    ):
        if anchor not in js:
            raise RuntimeError(
                "render_labels_report.JS_TEMPLATE changed -- applyFilters anchor no longer "
                f"found, cannot splice the ATR/outcome filters in:\n{anchor}")
        js = js.replace(anchor, replacement, 1)
    return (js.replace("__CHARTS_JSON__", json.dumps(charts))
              .replace("__ROWS_JSON__", json.dumps(rows_meta))
              .replace("__FEATURES_JSON__", json.dumps(R.FEATURES)))


def render(args):
    h1_df, rows_df, all_broken, funnel = X.load_strategy_rows(
        data_path=args.data, symbol=args.symbol, start=args.start, end=args.end,
        atr_mult=args.atr_mult, atr_d1_window=args.atr_d1_window,
        qualifying_only=not args.all_spikes, include_gaps=args.include_gaps)

    print(f"Loaded {len(h1_df)} H1 bars ({h1_df.index.min()} -> {h1_df.index.max()})")
    for k, v in funnel.items():
        print(f"  {k:<20} {v}")
    if rows_df.empty:
        raise SystemExit("No rows selected -- relax --atr-mult, widen the date range, "
                         "or pass --all-spikes.")

    trades = X.build_trades(rows_df)
    print(f"Resolving {len(trades)} trades on 1-minute bars + 1s exit pinning...")
    _series, resolved_list = X.resolve(trades, cache_path=args.cache)
    stats = X.summarize(trades, resolved_list)
    print(f"  wins={stats['wins']} losses={stats['losses']} no_hit={stats['no_hit']} "
          f"no_data={stats['no_data']} avg_R={stats['avg_R']:.3f}")

    rows_meta, charts, footprints = build_rows(
        h1_df, rows_df, all_broken, resolved_list, args.n_ticks, args.tick_size,
        pad_seconds=args.pad_seconds, one_min_pad_minutes=args.one_min_pad_minutes,
        include_footprint=not args.no_footprint, include_ticks=not args.no_ticks)

    params = {"start": args.start, "end": args.end, "data": args.data,
              "atr_mult": args.atr_mult, "atr_d1_window": args.atr_d1_window}
    header, filter_panel, thead, rows_html, tbody_close = build_page(
        rows_meta, args.title, args.n_ticks, args.tick_size, funnel, stats, params, footprints)
    js = build_js(charts, rows_meta)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{args.title}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
{R.CSS}
{EXTRA_CSS}
</head><body>
{header}
{filter_panel}
{thead}
{rows_html}
{tbody_close}
{js}
</body></html>
"""
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    parser = X._add_cli_args(argparse.ArgumentParser(
        description="HTML report for the spike-only-beyond-ATR LXPB strategy"))
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--title", default="LXPB spike-only-beyond-ATR -- Jul-Aug 2026 retests")
    parser.add_argument("--all-spikes", action="store_true",
                        help="render every spike retest with a BEYOND/within ATR verdict, not "
                             "just the qualifying trades")
    parser.add_argument("--n-ticks", type=int, default=R.N_TICKS_DEFAULT,
                        help="confluence radius in ticks (default 20)")
    parser.add_argument("--tick-size", type=float, default=R.TICK_SIZE_DEFAULT)
    parser.add_argument("--pad-seconds", type=int, default=R.PAD_SECONDS_DEFAULT,
                        help="+/- context (seconds) around the exact 1s fill instant")
    parser.add_argument("--one-min-pad-minutes", type=int, default=R.ONE_MIN_PAD_MINUTES_DEFAULT)
    parser.add_argument("--no-footprint", action="store_true")
    parser.add_argument("--no-ticks", action="store_true",
                        help="skip the 1s/1min/footprint charts (fast H1-only smoke test)")
    parser.add_argument("--cache", default=X.CACHE_1MIN_PATH,
                        help="1-minute bar cache CSV. Its keys are index-based, so any run "
                             "with a DIFFERENT trade selection (e.g. --all-spikes) should use "
                             "its own file -- see analyze_breakout_exits_1min.build_or_load_1min_series")
    render(parser.parse_args())
