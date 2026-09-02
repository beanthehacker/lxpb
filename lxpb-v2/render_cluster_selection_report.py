"""
Hand-check report for LXPB retest-cluster level SELECTION.
==========================================================

One expandable row per *candidate* level of every retest event found by
`analyze_retest_cluster_selection.py` -- i.e. per level that a single
large retest bar (or 2-3 bar directional move) could have filled -- so the
four selection features can be eyeballed against real price action and
marked right or wrong. Same look/feel as `stop2_target2_trades_report.html`
(dark theme, expandable H1 chart + real-tick 1s trio + 1min context), with
the trade-outcome columns replaced by feature columns and a per-feature
"did the code identify this correctly?" checkbox grid.

Rows are BANDED BY EVENT so a whole cluster reads as one block: the same
move, the several levels it swept, plus the levels just beyond its extreme
that it never reached (`MISSED` -- the trades you'd have lost by always
reaching for the deepest level).

What each expanded row shows
----------------------------
1. **H1 chart** -- formation (P0) -> breakout (P1) -> the retest event
   (P2), gap-compressed exactly like render_labels_report.build_row_chart.
   Feature evidence is drawn ON the chart so it can be checked visually:
   - the P0 marker's text spells out this level's features
     (`rec 2b | SWING | SPIKE | wick 61%`);
   - every ATR-ZigZag pivot `find_swings` confirmed in the window is
     marked (cyan `sh`/`sl`), so "is this level a swing?" is verifiable
     rather than a bare boolean;
   - the P1 marker states the recency gap in bars;
   - gold solid price line = this candidate; the other candidates in the
     same cluster are dashed and labelled with their depth rank and what
     actually happened to them (`#3 7664.25 stop`, `#6 7648.00 MISSED`).
2. **1s candles + Bid/Ask volume** -- centred on this candidate's real
   fill instant (or, for a MISSED candidate, on the moment price came
   CLOSEST to it, so you can see how far short the move stopped). All
   cluster levels are drawn here too, plus this candidate's stop/target.
3. **1min context** -- +/-20min around the same instant.

Labels persist in localStorage and export/import as CSV (buttons top
right). The exported CSV carries BOTH the computed feature values and
your verdicts, so handing it back is enough to retune the logic.

Usage:
    python render_cluster_selection_report.py
    python render_cluster_selection_report.py --limit 25 --stop 2 --target 2
    python render_cluster_selection_report.py --min-retested 2 --move-bars 3
"""
import os
import sys
import json
import argparse

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analyze_retest_cluster_selection as A  # noqa: E402
import render_labels_report as R  # noqa: E402

DEFAULT_OUTPUT = os.path.join(_HERE, "lxpb_cluster_selection_report.html")

BARS_BEFORE = 8
CONTEXT_BARS_AFTER_FORMATION = 3
CONTEXT_BARS_BEFORE_BREAKOUT = 3
CONTEXT_BARS_AFTER_BREAKOUT = 6
CONTEXT_BARS_BEFORE_EVENT = 6
BARS_AFTER_EVENT = 10
MAX_MERGE_GAP = 15

PAD_SECONDS_DEFAULT = 45
ONE_MIN_PAD_MINUTES_DEFAULT = 20

SWING_MARK_COLOR = "#22d3ee"
FILLED_LINE_COLOR = "#94a3b8"
MISSED_LINE_COLOR = "#5b6472"
WIN_COLOR = "#4ade80"
LOSS_COLOR = "#f87171"

# The hand-check grid. Every one of these is "the CODE got this right",
# defaulting to checked -- untick the ones the code got wrong. `pick_ok`
# is the exception: it defaults to checked only on the row the scoring
# rule actually selected, so ticking it elsewhere says "this is the level
# I would have taken instead".
CHECKS = [
    {"id": "recency_ok", "label": "Recency ok", "title":
     "(a) recency to breakout bar -- bars from this level's formation bar to its own breakout bar"},
    {"id": "swing_ok", "label": "Swing ok", "title":
     "(b) is this level a genuine swing level? (patterns-pure find_swings ATR ZigZag pivot)"},
    {"id": "spike_ok", "label": "Spike ok", "title":
     "(c) is the formation bar a rejection spike? (patterns-pure shooting star / hammer)"},
    {"id": "wick_ok", "label": "Wick ok", "title":
     "(d) LHPB upper wick / LLPB lower wick large enough on the formation bar?"},
    {"id": "pick_ok", "label": "Right pick", "title":
     "Is this the level you would actually have taken out of this cluster?"},
]

OUTCOME_LABEL = {"target": "WIN", "stop": "LOSS", "no_hit": "NO-HIT", "missed": "MISSED"}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _feature_text(c):
    bits = [f"rec {c['recency_bars']}b"]
    if c["is_swing_pp"]:
        bits.append("SWING")
    if c["is_spike_pp"]:
        bits.append("SPIKE")
    bits.append(("WICK " if c["large_wick"] else "wick ") + f"{c['wick_pct']:.0f}%")
    return " | ".join(bits)


def _candidate_line_title(c, is_self):
    state = OUTCOME_LABEL.get(c["outcome"], c["outcome"])
    if is_self:
        return f"#{c['depth_rank']} {c['price']:.2f} <- THIS LEVEL ({state})"
    return f"#{c['depth_rank']} {c['price']:.2f} {state}"


def build_h1_chart(h1_df, pos_by_ts, event, cand, swing_pivots):
    level_type = cand["type"]
    is_long = cand["is_long"]
    price = float(cand["price"])
    form_pos = pos_by_ts[cand["formation_time"]]
    breakout_pos = pos_by_ts[cand["breakout_time"]]
    n_bars = len(h1_df)

    segments = [
        (form_pos - BARS_BEFORE, form_pos + CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + CONTEXT_BARS_AFTER_BREAKOUT),
        (event["start_pos"] - CONTEXT_BARS_BEFORE_EVENT, event["end_pos"] + BARS_AFTER_EVENT),
    ]
    merged = R._merge_segments(segments, n_bars, MAX_MERGE_GAP)

    parts, skip_markers = [], []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": R._to_epoch_utc(h1_df.index[s]), "position": "inBar",
                "color": "#9ca3af", "shape": "square", "text": f"[{gap} bars skipped]"})
        parts.append(h1_df.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)
    window_times = set(window.index)

    candles = [{"time": R._to_epoch_utc(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in window.iterrows()]

    markers = [
        {"time": R._to_epoch_utc(cand["formation_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P0_COLOR, "shape": "circle", "text": "P0 " + _feature_text(cand)},
        {"time": R._to_epoch_utc(cand["breakout_time"]),
         "position": "belowBar" if is_long else "aboveBar",
         "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
         "shape": "arrowUp" if is_long else "arrowDown",
         "text": f"P1 breakout (+{cand['recency_bars']}b after P0)"},
        {"time": R._to_epoch_utc(event["start_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P2_COLOR, "shape": "circle",
         "text": f"P2 retest move ({event['n_bars']} bar{'s' if event['n_bars'] > 1 else ''}, "
                 f"{event['n_retested']} levels swept)"},
    ] + skip_markers

    # Every ZigZag pivot the swing pass confirmed, so feature (b) is
    # visually checkable instead of a bare boolean. The level's own
    # formation bar is skipped -- its P0 marker already says SWING.
    pivot_highs, pivot_lows = swing_pivots
    for pivots, is_high in ((pivot_highs, True), (pivot_lows, False)):
        if pivots is None or pivots.empty:
            continue
        for t in pivots.index:
            if t not in window_times or t == cand["formation_time"]:
                continue
            markers.append({
                "time": R._to_epoch_utc(t),
                "position": "aboveBar" if is_high else "belowBar",
                "color": SWING_MARK_COLOR, "shape": "circle",
                "text": "sh" if is_high else "sl"})
    markers.sort(key=lambda m: m["time"])

    price_lines = [{"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
                    "title": _candidate_line_title(cand, True)}]
    for other in event["candidates"]:
        if other is cand:
            continue
        price_lines.append({
            "price": float(other["price"]),
            "color": FILLED_LINE_COLOR if other["filled"] else MISSED_LINE_COLOR,
            "lineWidth": 1, "lineStyle": 2 if other["filled"] else 3,
            "title": _candidate_line_title(other, False)})

    title = (f"{level_type} {price:.2f}  |  P0 {R._to_pt_str(cand['formation_time'])}  "
             f"P1 {R._to_pt_str(cand['breakout_time'])}  "
             f"P2 event {R._to_pt_str(event['start_time'])}  |  {_feature_text(cand)}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"
    return {"title": title, "candles": candles, "markers": markers,
            "priceLines": price_lines, "precision": 2}


def _reference_instant(event, cand):
    """Where to centre this candidate's 1s/1min charts: its real fill
    instant, or -- for a candidate price never reached -- the second at
    which price came CLOSEST to it inside the event's fill window, so the
    reviewer can see exactly how far short the move stopped."""
    if cand["fill_time"] is not None:
        return cand["fill_time"], None
    bars = event.get("bars_1s")
    if bars is None or bars.empty:
        return None, None
    lo = pd.Timestamp(event["start_time"], tz="UTC")
    hi = pd.Timestamp(event["end_time"], tz="UTC") + pd.Timedelta(hours=1)
    win = bars.loc[(bars.index >= lo) & (bars.index < hi)]
    if win.empty:
        return None, None
    raw = cand["price"] - event.get("offset", 0.0)
    dist = (win["Low"].to_numpy(float) - raw) if cand["is_long"] else (raw - win["High"].to_numpy(float))
    i = int(np.argmin(dist))
    return win.index[i], round(float(dist[i]), 2)


def build_tick_charts(event, cand, stop, target, pad_seconds, one_min_pad_minutes):
    bars = event.get("bars_1s")
    if bars is None or bars.empty:
        return None
    ref, miss_by = _reference_instant(event, cand)
    if ref is None:
        return None
    offset = event.get("offset", 0.0)
    is_long = cand["is_long"]
    price = float(cand["price"])

    lo = ref - pd.Timedelta(seconds=pad_seconds)
    hi = ref + pd.Timedelta(seconds=pad_seconds)
    win = bars.loc[(bars.index >= lo) & (bars.index <= hi)]
    if win.empty:
        return None

    def _candles(frame):
        return [{"time": int(t.timestamp()), "open": float(r.Open) + offset,
                 "high": float(r.High) + offset, "low": float(r.Low) + offset,
                 "close": float(r.Close) + offset} for t, r in frame.iterrows()]

    price_lines = [{"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
                    "title": _candidate_line_title(cand, True)}]
    if cand["filled"]:
        price_lines += [
            {"price": price + target if is_long else price - target, "color": WIN_COLOR,
             "lineWidth": 1, "lineStyle": 2, "title": f"target +{target:.0f}"},
            {"price": price - stop if is_long else price + stop, "color": LOSS_COLOR,
             "lineWidth": 1, "lineStyle": 2, "title": f"stop -{stop:.0f}"},
        ]
    for other in event["candidates"]:
        if other is cand:
            continue
        price_lines.append({
            "price": float(other["price"]),
            "color": FILLED_LINE_COLOR if other["filled"] else MISSED_LINE_COLOR,
            "lineWidth": 1, "lineStyle": 2 if other["filled"] else 3,
            "title": _candidate_line_title(other, False)})

    if cand["filled"]:
        mark_text, mark_color = "FILL", R.ENTRY_COLOR
    else:
        mark_text, mark_color = f"CLOSEST ({miss_by:+.2f}pt short)", MISSED_LINE_COLOR
    marker = {"time": int(ref.timestamp()),
              "position": "belowBar" if is_long else "aboveBar",
              "color": mark_color, "shape": "arrowUp" if is_long else "arrowDown",
              "text": mark_text}

    pt = ref.tz_convert("America/Los_Angeles")
    state = OUTCOME_LABEL.get(cand["outcome"], cand["outcome"])
    trio = {
        "title": (f"1s @ {'fill' if cand['filled'] else 'closest approach'} -- "
                  f"{pt.strftime('%Y-%m-%d %H:%M:%S')} PT ({'LONG' if is_long else 'SHORT'})  |  "
                  f"level {price:.2f}  |  {state}"),
        "candles": _candles(win),
        "bid": [{"time": int(t.timestamp()), "value": float(r.BidVolume), "color": R.BID_COLOR}
                for t, r in win.iterrows()],
        "ask": [{"time": int(t.timestamp()), "value": float(r.AskVolume), "color": R.ASK_COLOR}
                for t, r in win.iterrows()],
        "markers": [marker], "priceLines": price_lines, "precision": 2,
    }

    bars_1m = R._resample_1min_from_1s(bars)
    win_1m = bars_1m.loc[(bars_1m.index >= ref - pd.Timedelta(minutes=one_min_pad_minutes))
                         & (bars_1m.index <= ref + pd.Timedelta(minutes=one_min_pad_minutes))]
    one_min = {
        "title": f"1min -- +/-{one_min_pad_minutes}min  |  level {price:.2f}  |  "
                 f"cluster of {cand['n_candidates']} candidates",
        "candles": _candles(win_1m),
        "markers": [dict(marker, time=int(ref.floor('min').timestamp()))],
        "priceLines": [dict(pl) for pl in price_lines], "precision": 2,
    }
    return {"trio": trio, "oneMin": one_min, "miss_by": miss_by}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = R.CSS + """
<style>
td.good { color:#4ade80; font-weight:600; }
td.bad { color:#f87171; font-weight:600; }
td.muted { color:#6b7280; }
td.feat-yes { color:#4ade80; font-weight:600; }
td.feat-no { color:#4b5563; }
tr.lvl-row.is-pick td { box-shadow: inset 3px 0 0 #fcd34d; }
tr.lvl-row.is-missed td.state-cell { color:#6b7280; font-style:italic; }
.table-wrap { overflow-x:auto; }
.expand-th { width:28px; }
.stat-tables { display:flex; gap:14px; flex-wrap:wrap; margin:10px 0 18px; }
.stat-tables > div { flex:1 1 420px; min-width:380px; }
.stat-tables h3 { font-size:0.9em; margin:0 0 6px; color:#e8eaed; font-weight:600; }
.stat-tables table { font-size:0.76em; }
.stat-tables td, .stat-tables th { white-space:nowrap; }
.assump { background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--accent);
          border-radius:6px; padding:10px 14px; margin:10px 0 16px; font-size:0.82em;
          color:var(--text-dim); line-height:1.55; }
.assump b { color:var(--text); }
.assump code { color:#7bb4f5; }
/* This report's H1 panel carries far more annotation than the labeling
   report's (feature marker text, swing pivots, one price line per level in
   the cluster), so it needs more vertical room to stay readable. */
.chart-h1 { height:516px; }
</style>
"""


def _df_to_html(df, cls=""):
    if df is None or df.empty:
        return "<p class='note'>(no rows)</p>"
    head = "".join(f"<th>{c}</th>" for c in df.columns)
    body = "".join(
        "<tr>" + "".join(
            f"<td class='left'>{v}</td>" if isinstance(v, str) else
            f"<td>{'' if (isinstance(v, float) and np.isnan(v)) else v}</td>"
            for v in row) + "</tr>"
        for row in df.itertuples(index=False))
    return f"<table class='{cls}'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _fmt(v, spec="{:.2f}", dash="-"):
    if v is None:
        return dash
    if isinstance(v, float) and np.isnan(v):
        return dash
    return spec.format(v)


def _weights_text(args):
    w = A.parse_weights(getattr(args, "weights", ""))
    return ", ".join(f"{k}={v:g}" for k, v in w.items())


def build_page(rows_meta, events, df, args):
    n_filled = int(df["filled"].sum())
    n_missed = len(df) - n_filled
    n_events = len(events)

    header = f"""
<h1>LXPB Cluster Selection &mdash; which level do you take when a move sweeps several?</h1>
<p class="lead">{len(rows_meta)} candidate levels across {n_events} retest events
({n_filled} actually filled at 1s, <b>{n_missed} never reached</b> &mdash; price reverted in front of
them). Rows are grouped and colour-banded by event; within an event they are ordered by
<b>depth</b> (#1 = the level the move reaches first). Every number in the feature columns is what
the code computed &mdash; tick/untick the <b>Recency / Swing / Spike / Wick</b> boxes to say whether
the code identified that feature <em>correctly</em>, and <b>Right pick</b> to say which level you
would actually have taken. <b>All four feature boxes start ticked</b> (and <b>Right pick</b> starts
ticked on the row the scoring rule chose), so you only have to <em>untick what the code got
wrong</em> &mdash; then tick <b>Reviewed</b> so the export can tell "approved" from "not looked at
yet". Export the CSV and hand it back to retune the logic.</p>
<div class="summary">
  <div class="box true"><strong id="sum-total">{len(rows_meta)}</strong>Candidates</div>
  <div class="box"><strong>{n_events}</strong>Events</div>
  <div class="box"><strong>{n_filled}</strong>Filled</div>
  <div class="box"><strong>{n_missed}</strong>Never reached</div>
  <div class="box"><strong id="sum-reviewed">0</strong>Reviewed</div>
  <div class="box"><strong id="sum-flagged">0</strong>Flagged wrong</div>
  <div class="toolbar">
    <button class="btn" onclick="exportCsv()">&#11015; Export labels CSV</button>
    <label class="btn" for="import-file">&#11014; Import labels CSV</label>
    <input type="file" id="import-file" accept=".csv" class="hidden" onchange="importCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved labels in this browser?')) clearAll();">&#128465; Clear all</button>
  </div>
</div>
<div class="assump">
<b>Assumptions baked into this build &mdash; correct me on any of them.</b><br>
<b>Retest event</b> = one H1 bar, or up to <code>{args.move_bars}</code> consecutive bars moving
continuously in the retest direction (lower lows into LHPB supports, higher highs into LLPB
resistances). Only events that swept &ge; <code>{args.min_retested}</code> levels are shown.<br>
<b>(a) Recency</b> is read as <em>bars from the level's own FORMATION bar to its own BREAKOUT
bar</em> (LXPB = "Last High/Low <em>Pre-Breakout</em>", so 1 bar = a textbook LXPB). The
alternative reading &mdash; breakout&rarr;retest distance &mdash; is shown in grey under each
value so you can tell me if that is what you meant.<br>
<b>(b) Swing</b> = <code>patterns_pure/find_swings</code> ATR ZigZag pivot (atr_mult
{A.SWING_ATR_MULT}, ATR {A.SWING_ATR_PERIOD}) run on the {A.SWING_LOOKBACK_BARS} bars
<em>ending at the event's first bar</em> (point-in-time, no look-ahead), requiring the pivot's own
high/low to BE the level price. lxpb.py's cruder 3-bar flag is shown in grey underneath.<br>
<b>(c) Spike</b> = patterns-pure <code>find_shooting_star</code> for LHPB / <code>find_hammer</code>
for LLPB on the formation bar. Note lxpb.py's own <code>is_spike</code> uses the opposite mapping;
it is shown in grey underneath.<br>
<b>(d) Wick</b> = LHPB upper wick / LLPB lower wick &ge; {A.WICK_THRESHOLD:.0%} of the formation
bar's range (patterns-pure <code>candle_utils</code>).<br>
<b>Score</b> = recency (min-max normalised <em>within the cluster</em>, 1.0 = the level that formed
closest to its own breakout relative to its peers) + 1 per feature present, at weights
<code>{_weights_text(args)}</code>. Equal weights are a placeholder &mdash; tell me via
<b>Right pick</b> which level you'd really take and I can refit them.<br>
<b>Candidate set</b> deliberately includes levels the move <em>never reached</em> (within
<code>{args.miss_band_pts:g}pt</code> beyond its extreme). Ranking only the levels that were
actually hit would rig the comparison &mdash; the deepest level sits at the reversal by
construction, so it always shows the best bounce. Counting misses as 0&nbsp;R is what makes
"always take the deepest" pay for the trades it never gets filled on.<br>
<b>Outcome</b> = real 1s ticks, stop <code>{args.stop:g}pt</code> / target
<code>{args.target:g}pt</code>, resting-order fill realism (long entry needs a bid-side print,
long target an ask-side print, stops fill on any side), walked strictly forward from the fill.
<b>Bounce</b> = points price rallied off the level <em>before</em> trading
<code>{args.break_pts:g}pt</code> through it, so a shallow level that genuinely held still scores.
</div>
"""

    stats = f"""
<div class="stat-tables">
  <div><h3>Selection rules &mdash; one pick per event, an unfilled pick scores 0&nbsp;R</h3>
  {_df_to_html(A.rule_table(events))}</div>
  <div><h3>Feature splits across all {len(df)} candidates</h3>
  {_df_to_html(A.feature_table(df))}</div>
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
    <span class="filter-label">Fill</span>
    <label class="chip"><input type="checkbox" class="f-cb f-fill" value="filled" checked> Filled</label>
    <label class="chip"><input type="checkbox" class="f-cb f-fill" value="missed" checked> Never reached</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Row</span>
    <label class="chip"><input type="checkbox" class="f-cb f-pick" value="pick" checked> Scoring rule's pick</label>
    <label class="chip"><input type="checkbox" class="f-cb f-pick" value="other" checked> Other candidates</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="reviewed" checked> Reviewed</label>
  </div>
</div>
"""

    check_ths = "".join(f'<th title="{c["title"]}">{c["label"]}</th>' for c in CHECKS)
    n_cols = 16 + len(CHECKS) + 2  # 15 data cols + Reviewed + checks + notes + expand
    thead = f"""
<div class="table-wrap">
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Event</th><th class="left">Type</th>
  <th>Level</th><th>Depth</th>
  <th title="(a) H1 bars from this level's formation bar to its own breakout bar">Recency (a)</th>
  <th title="(b) patterns-pure find_swings ATR ZigZag pivot">Swing (b)</th>
  <th title="(c) patterns-pure shooting star (LHPB) / hammer (LLPB) on the formation bar">Spike (c)</th>
  <th title="(d) LHPB upper wick / LLPB lower wick as % of formation-bar range">Wick (d)</th>
  <th title="Weighted sum of the four features, normalised within the cluster">Score</th>
  <th class="left">Fill (1s)</th>
  <th title="Points price bounced off the level before trading through it">Bounce</th>
  <th title="Max favourable / adverse excursion over the bounce window">MFE/MAE</th>
  <th>Outcome</th><th>R</th>
  <th>Reviewed</th>
  {check_ths}
  <th class="left">Misc notes</th><th class="expand-th">&#9654;</th>
</tr></thead>
<tbody>
"""

    rows_html = []
    for r in rows_meta:
        c = r["c"]
        band = f" band-{r['band']}"
        cls = f"type-{'lhpb' if c['type'] == 'LHPB' else 'llpb'}{band}"
        if c["selected"]:
            cls += " is-pick"
        if not c["filled"]:
            cls += " is-missed"

        out = c["outcome"]
        out_cls = "good" if out == "target" else ("bad" if out == "stop" else "muted")
        r_str = f"{c['r']:+.2f}" if c["filled"] else "0.00"
        fill_str = R._to_pt_str(c["fill_time"]) if c["fill_time"] is not None else "MISSED"
        fill_hint = (f"#{c['fill_order']} of the cluster" if c["fill_order"]
                     else f"closest {_fmt(r['miss_by'], '{:+.2f}')}pt")

        checks_html = "".join(
            f'<td onclick="event.stopPropagation();">'
            f'<input type="checkbox" class="chk-cb" data-chk="{ch["id"]}" '
            f'data-default="{1 if r["defaults"][ch["id"]] else 0}"></td>'
            for ch in CHECKS)

        rows_html.append(f"""
<tr class="lvl-row {cls}" data-idx="{r['idx']}" data-key="{r['key']}" data-type="{c['type']}"
    data-fill="{'filled' if c['filled'] else 'missed'}" data-pick="{'pick' if c['selected'] else 'other'}"
    onclick="toggleChart({r['idx']})">
  <td class="left">{r['idx']}</td>
  <td class="left"><span class="cluster-badge" title="Event {c['event_id']}: {r['event_desc']}"
      >E{c['event_id']}&middot;{c['depth_rank']}/{c['n_candidates']}</span></td>
  <td class="left type-cell">{c['type']}</td>
  <td>{c['price']:.2f}</td>
  <td>{c['depth_rank']}<span class="hint">{'shallowest' if c['depth_rank'] == 1 else ('deepest' if c['depth_rank'] == c['n_candidates'] else '')}</span></td>
  <td>{c['recency_bars']}b<span class="hint">bo&rarr;retest {c['bars_breakout_to_event']}b</span></td>
  <td class="{'feat-yes' if c['is_swing_pp'] else 'feat-no'}">{'YES' if c['is_swing_pp'] else 'no'}
      <span class="hint">lxpb: {'y' if c['is_swing_lxpb'] else 'n'}</span></td>
  <td class="{'feat-yes' if c['is_spike_pp'] else 'feat-no'}">{'YES' if c['is_spike_pp'] else 'no'}
      <span class="hint">lxpb: {'y' if c['is_spike_lxpb'] else 'n'}</span></td>
  <td class="{'feat-yes' if c['large_wick'] else 'feat-no'}">{c['wick_pct']:.0f}%
      <span class="hint">{'large' if c['large_wick'] else 'small'}</span></td>
  <td>{c['score']:.2f}{'<span class="hint">PICK</span>' if c['selected'] else ''}</td>
  <td class="left state-cell">{fill_str}<span class="hint">{fill_hint}</span></td>
  <td>{_fmt(c['immediate_bounce'])}<span class="hint">{'held' if c['held'] else ('broke' if c['held'] is False else '')}</span></td>
  <td>{_fmt(c['mfe'])} / {_fmt(c['mae'])}</td>
  <td class="{out_cls}">{OUTCOME_LABEL.get(out, out)}</td>
  <td class="{out_cls}">{r_str}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  {checks_html}
  <td class="left" onclick="event.stopPropagation();"><textarea class="misc-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{r['idx']}"
      onclick="event.stopPropagation();toggleChart({r['idx']})">&#9654;</button></td>
</tr>
<tr class="chart-row hidden{band}" data-idx="{r['idx']}" id="chart-row-{r['idx']}">
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
  </div></td>
</tr>
""")
    return header + stats + filter_panel + thead + "".join(rows_html) + "</tbody></table></div>"


JS_TEMPLATE = """
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const CHARTS = __CHARTS_JSON__;
const ROWS = __ROWS_JSON__;
const CHECKS = __CHECKS_JSON__;
const STORAGE_KEY = 'lxpb_cluster_selection_v1';
const rendered = {};
const FIXED_BAR_SPACING = 6;
const H1_MAX_BAR_SPACING = 22;

/* ---------------- labeling / persistence ---------------- */
function loadStore() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveStore(s) { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); }

function rowState(tr) {
  const st = { reviewed: tr.querySelector('.reviewed-cb').checked,
               misc: tr.querySelector('.misc-note').value };
  CHECKS.forEach(c => {
    const cb = tr.querySelector('.chk-cb[data-chk="' + c.id + '"]');
    st[c.id] = cb ? cb.checked : false;
  });
  return st;
}
function applyDefaults(tr) {
  tr.querySelector('.reviewed-cb').checked = false;
  tr.querySelectorAll('.chk-cb').forEach(cb => { cb.checked = cb.dataset.default === '1'; });
  markFlags(tr);
}
function applyRowState(tr, st) {
  if (!st) return;
  if (st.reviewed !== undefined) tr.querySelector('.reviewed-cb').checked = !!st.reviewed;
  if (st.misc !== undefined) tr.querySelector('.misc-note').value = st.misc || '';
  CHECKS.forEach(c => {
    if (st[c.id] === undefined) return;
    const cb = tr.querySelector('.chk-cb[data-chk="' + c.id + '"]');
    if (cb) cb.checked = !!st[c.id];
  });
  markFlags(tr);
}
/* A row is "flagged wrong" when any of the four FEATURE checks (not
   pick_ok, which is a preference not a correctness verdict) has been
   unticked away from its computed default. */
function markFlags(tr) {
  let flagged = false;
  tr.querySelectorAll('.chk-cb').forEach(cb => {
    if (cb.dataset.chk === 'pick_ok') return;
    if (cb.dataset.default === '1' && !cb.checked) flagged = true;
  });
  tr.classList.toggle('is-reviewed', !!tr.querySelector('.reviewed-cb').checked);
  tr.classList.toggle('is-invalid', flagged);
}
function persistRow(tr) {
  const store = loadStore();
  store[tr.dataset.key] = rowState(tr);
  saveStore(store);
  markFlags(tr);
  updateSummary();
}
function updateSummary() {
  document.getElementById('sum-reviewed').textContent =
    document.querySelectorAll('.lvl-row.is-reviewed').length;
  document.getElementById('sum-flagged').textContent =
    document.querySelectorAll('.lvl-row.is-invalid').length;
}
function initRows() {
  const store = loadStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .chk-cb, .misc-note').forEach(el => {
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
/* The export carries the COMPUTED feature values next to the reviewer's
   verdicts, so a single returned file is enough to retune the logic. */
const EXPORT_COMPUTED = ['event_id', 'type', 'price', 'formation_time', 'breakout_time',
  'depth_rank', 'n_candidates', 'recency_bars', 'bars_breakout_to_event', 'is_swing_pp',
  'is_swing_lxpb', 'is_spike_pp', 'is_spike_lxpb', 'large_wick', 'wick_pct', 'score',
  'selected', 'filled', 'fill_time', 'immediate_bounce', 'mfe', 'mae', 'held', 'outcome', 'r'];
function exportCsv() {
  const store = loadStore();
  const chkCols = CHECKS.map(c => c.id);
  const header = ['key', 'idx'].concat(EXPORT_COMPUTED).concat(['reviewed']).concat(chkCols).concat(['misc']);
  const lines = [header.join(',')];
  ROWS.forEach(r => {
    const st = store[r.key] || {};
    const row = [r.key, r.idx].concat(EXPORT_COMPUTED.map(k => r[k]))
      .concat([st.reviewed ? 1 : 0])
      .concat(chkCols.map(k => (st[k] ? 1 : 0)))
      .concat([st.misc || '']);
    lines.push(row.map(csvEscape).join(','));
  });
  const blob = new Blob([lines.join('\\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'lxpb_cluster_selection_labels.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
function parseCsvLine(line) {
  const out = []; let cur = ''; let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQ) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') { inQ = false; }
      else { cur += ch; }
    } else {
      if (ch === '"') inQ = true;
      else if (ch === ',') { out.push(cur); cur = ''; }
      else cur += ch;
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
      if (!rec.key) continue;
      const st = { misc: rec.misc || '' };
      if (header.includes('reviewed')) st.reviewed = rec.reviewed === '1';
      CHECKS.forEach(c => { if (header.includes(c.id)) st[c.id] = rec[c.id] === '1'; });
      store[rec.key] = st;
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
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    tr.querySelector('.misc-note').value = '';
  });
  updateSummary();
}

/* ---------------- filters ---------------- */
function checkedVals(sel) {
  return new Set(Array.from(document.querySelectorAll(sel))
    .filter(cb => cb.checked).map(cb => cb.value));
}
function applyFilters() {
  const types = checkedVals('.f-type'), fills = checkedVals('.f-fill'),
        picks = checkedVals('.f-pick'), status = checkedVals('.f-status');
  let shown = 0;
  document.querySelectorAll('.lvl-row').forEach(tr => {
    const reviewed = tr.classList.contains('is-reviewed') ? 'reviewed' : 'unreviewed';
    const ok = types.has(tr.dataset.type) && fills.has(tr.dataset.fill)
            && picks.has(tr.dataset.pick) && status.has(reviewed);
    tr.classList.toggle('hidden', !ok);
    const cr = document.getElementById('chart-row-' + tr.dataset.idx);
    if (cr && !ok) cr.classList.add('hidden');
    if (ok) shown++;
  });
  document.getElementById('sum-total').textContent = shown;
}

/* ---------------- charts ---------------- */
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
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.10, bottom:0.10} },
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
/* The H1 window is compressed down to a few dozen bars, so a fixed narrow
   bar spacing would strand them in a tiny clump mid-panel and pile all the
   feature/swing marker text on top of itself. Widen the bars to fill the
   panel instead (capped so a short window doesn't become absurd). */
function _fitH1(chart, el, nBars) {
  if (!nBars) return;
  const w = el.clientWidth || 900;
  const spacing = Math.max(FIXED_BAR_SPACING, Math.min(H1_MAX_BAR_SPACING, (w * 0.94) / nBars));
  chart.timeScale().applyOptions({ barSpacing: spacing });
  const barsVisible = Math.max(1, w / spacing);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}
function _legend(titleEl, base, series, chart, prec) {
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      titleEl.textContent = base + '  |  O ' + d.open.toFixed(prec) + '  H ' + d.high.toFixed(prec)
        + '  L ' + d.low.toFixed(prec) + '  C ' + d.close.toFixed(prec);
    } else { titleEl.textContent = base; }
  });
}
function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  if (!el || !titleEl) return;
  titleEl.textContent = cd.title;
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false, width: el.clientWidth || 900, height: el.clientHeight || 220 }));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  _legend(titleEl, cd.title, series, chart, cd.precision || 2);
  _fitH1(chart, el, cd.candles.length);
  const ro = new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) {
      chart.resize(r.width, r.height);
      _fitH1(chart, el, cd.candles.length);
    }
  });
  ro.observe(el);
}
function _renderTrio(i, cd) {
  const elC = document.getElementById('cc-' + i), elB = document.getElementById('cb-' + i),
        elA = document.getElementById('ca-' + i);
  const tC = document.getElementById('tc-' + i), tB = document.getElementById('tb-' + i),
        tA = document.getElementById('ta-' + i);
  if (!elC || !elB || !elA) return;
  const baseC = cd.title, baseB = 'Bid Volume', baseA = 'Ask Volume';
  tC.textContent = baseC; tB.textContent = baseB; tA.textContent = baseA;

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
  const panes = [ {chart:chartC, series:seriesC}, {chart:chartB, series:seriesB},
                  {chart:chartA, series:seriesA} ];

  function updateLegends(time) {
    const c = time != null ? cMap[time] : null;
    tC.textContent = baseC + (c ? ('  |  O ' + c.open.toFixed(prec) + '  H ' + c.high.toFixed(prec)
      + '  L ' + c.low.toFixed(prec) + '  C ' + c.close.toFixed(prec)) : '');
    const b = time != null ? bMap[time] : null;
    tB.textContent = baseB + (b != null ? ('  |  ' + b) : '');
    const a = time != null ? aMap[time] : null;
    tA.textContent = baseA + (a != null ? ('  |  ' + a) : '');
  }
  let syncCH = false;
  panes.forEach((p, idx) => {
    p.chart.subscribeCrosshairMove((param) => {
      if (syncCH) return;
      syncCH = true;
      const time = (param && param.time != null) ? param.time : null;
      updateLegends(time);
      panes.forEach((o, j) => {
        if (j === idx) return;
        if (time == null) { o.chart.clearCrosshairPosition(); return; }
        let val = null;
        if (o.series === seriesC) val = cMap[time] ? cMap[time].close : null;
        else if (o.series === seriesB) val = bMap[time];
        else val = aMap[time];
        if (val != null) o.chart.setCrosshairPosition(val, time, o.series);
        else o.chart.clearCrosshairPosition();
      });
      syncCH = false;
    });
  });
  let syncR = false;
  panes.forEach((p, idx) => {
    p.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncR) return;
      syncR = true;
      panes.forEach((o, j) => { if (j !== idx) o.chart.timeScale().setVisibleLogicalRange(range); });
      syncR = false;
    });
  });
  chartC.timeScale().fitContent();
}
function _renderOneMin(i, cd) {
  const el = document.getElementById('c1m-' + i);
  const titleEl = document.getElementById('t1m-' + i);
  if (!el || !titleEl) return;
  titleEl.textContent = cd.title;
  const chart = LightweightCharts.createChart(el, _baseOpts(timeFmtH1));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  _legend(titleEl, cd.title, series, chart, cd.precision || 2);
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

document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));
initRows();
applyFilters();
</script>
"""


def render(args):
    h1_df, pos_by_ts, events, df = A.build_clusters(
        data_path=args.data, start=args.start, end=args.end, move_bars=args.move_bars,
        miss_band_pts=args.miss_band_pts, min_cluster_size=args.min_cluster_size,
        min_retested=args.min_retested, limit=args.limit, stop=args.stop, target=args.target,
        horizon_hours=args.horizon_hours, bounce_seconds=args.bounce_seconds,
        break_pts=args.break_pts, weights=A.parse_weights(getattr(args, "weights", "")))
    if df.empty:
        print("No candidates matched -- nothing to render.")
        return

    rows_meta, charts, json_rows = [], [], []
    idx = 0
    n_events = len(events)
    for band, event in enumerate(events):
        pivots = A.swing_pivots(h1_df, event["start_pos"])
        event_desc = (f"{event['type']} {event['n_bars']}-bar move "
                      f"{R._to_pt_str(event['start_time'])} .. {R._to_pt_str(event['end_time'])}, "
                      f"{event['n_retested']} levels swept, {len(event['candidates'])} in play")
        for c in event["candidates"]:
            h1_chart = build_h1_chart(h1_df, pos_by_ts, event, c, pivots)
            tick = build_tick_charts(event, c, args.stop, args.target,
                                     args.pad_seconds, args.one_min_pad_minutes)
            charts.append({"h1": h1_chart,
                           "trio": tick["trio"] if tick else None,
                           "oneMin": tick["oneMin"] if tick else None})
            key = (f"{c['type']}|{c['price']:.2f}|"
                   f"{R._to_epoch_utc(c['formation_time'])}|E{c['event_id']}")
            rows_meta.append({
                "idx": idx, "key": key, "c": c, "band": band % 2,
                "event_desc": event_desc,
                "miss_by": tick["miss_by"] if tick else None,
                "defaults": {"recency_ok": True, "swing_ok": True, "spike_ok": True,
                             "wick_ok": True, "pick_ok": bool(c["selected"])},
            })
            json_rows.append({
                "key": key, "idx": idx, "event_id": c["event_id"], "type": c["type"],
                "price": round(c["price"], 2),
                "formation_time": R._to_pt_str(c["formation_time"]),
                "breakout_time": R._to_pt_str(c["breakout_time"]),
                "depth_rank": c["depth_rank"], "n_candidates": c["n_candidates"],
                "recency_bars": c["recency_bars"],
                "bars_breakout_to_event": c["bars_breakout_to_event"],
                "is_swing_pp": int(c["is_swing_pp"]), "is_swing_lxpb": int(c["is_swing_lxpb"]),
                "is_spike_pp": int(c["is_spike_pp"]), "is_spike_lxpb": int(c["is_spike_lxpb"]),
                "large_wick": int(c["large_wick"]), "wick_pct": c["wick_pct"],
                "score": c["score"], "selected": int(c["selected"]), "filled": int(c["filled"]),
                "fill_time": R._to_pt_str(c["fill_time"]) if c["fill_time"] is not None else "",
                "immediate_bounce": _fmt(c["immediate_bounce"], "{:.2f}", ""),
                "mfe": _fmt(c["mfe"], "{:.2f}", ""), "mae": _fmt(c["mae"], "{:.2f}", ""),
                "held": "" if c["held"] is None else int(c["held"]),
                "outcome": c["outcome"], "r": round(float(c["r"]), 2),
            })
            idx += 1
        if (band + 1) % 5 == 0 or (band + 1) == n_events:
            print(f"  built charts for {band + 1}/{n_events} events ({idx} rows)")

    body = build_page(rows_meta, events, df, args)
    js = (JS_TEMPLATE
          .replace("__CHARTS_JSON__", json.dumps(charts, separators=(",", ":")))
          .replace("__ROWS_JSON__", json.dumps(json_rows, separators=(",", ":")))
          .replace("__CHECKS_JSON__", json.dumps(CHECKS, separators=(",", ":"))))

    html = (f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
            f"<title>LXPB Cluster Selection Review</title>{CSS}</head><body>\n"
            f"{body}\n{js}\n</body></html>")
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(args.output) / 1e6
    print(f"\nWrote {len(rows_meta)} candidate rows across {n_events} events "
          f"-> {args.output} ({size_mb:.1f} MB)")

    print("\n--- Selection rules (one pick per event; an unfilled pick scores 0 R) ---")
    print(A.rule_table(events).to_string(index=False))
    print("\n--- Feature splits over all candidates ---")
    print(A.feature_table(df).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(
        description="Hand-check report for LXPB retest-cluster level selection",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=A.DEFAULT_DATA)
    ap.add_argument("--start", default=A.START_DEFAULT)
    ap.add_argument("--end", default=A.END_DEFAULT)
    ap.add_argument("--move-bars", type=int, default=A.MOVE_BARS_DEFAULT,
                    help="max consecutive H1 bars that count as ONE retest move")
    ap.add_argument("--miss-band-pts", type=float, default=A.MISS_BAND_PTS_DEFAULT,
                    help="how far beyond the move's extreme a pending level still counts as in-play")
    ap.add_argument("--min-cluster-size", type=int, default=A.MIN_CLUSTER_SIZE_DEFAULT)
    ap.add_argument("--min-retested", type=int, default=A.MIN_RETESTED_DEFAULT)
    ap.add_argument("--limit", type=int, default=0, help="keep only the N most recent events")
    ap.add_argument("--stop", type=float, default=A.STOP_DEFAULT)
    ap.add_argument("--target", type=float, default=A.TARGET_DEFAULT)
    ap.add_argument("--horizon-hours", type=float, default=A.HORIZON_HOURS_DEFAULT)
    ap.add_argument("--bounce-seconds", type=int, default=A.BOUNCE_SECONDS_DEFAULT)
    ap.add_argument("--break-pts", type=float, default=A.BREAK_PTS_DEFAULT)
    ap.add_argument("--pad-seconds", type=int, default=PAD_SECONDS_DEFAULT)
    ap.add_argument("--one-min-pad-minutes", type=int, default=ONE_MIN_PAD_MINUTES_DEFAULT)
    ap.add_argument("--weights", default="",
                    help="reweight the four features, e.g. 'recency=1,swing=2,spike=0,wick=1.5'")
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    render(ap.parse_args())


if __name__ == "__main__":
    main()
