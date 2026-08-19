"""
Generates a single explainer HTML page (`strategy_explainer.html`) that
walks through the LXPB + volume-absorption strategy concept-by-concept,
with real annotated example trades (entry / stop / target price lines) in
the SAME lightweight-charts layout as `render_report.py` (H1 context chart
+ synced 1s candles/bid-volume/ask-volume trio).

Reuses render_report.py's data builders (build_chart_data,
build_h1_context_data) and JS chart engine (_baseOpts/_addCandles/_renderH1/
_renderTrio) unmodified -- this script only adds extra price lines (entry,
stop variants, target) on top of the confluence-level lines those builders
already produce, and wraps everything in an article-style page instead of
the expandable-table dashboard layout.
"""
import os
import sys
import json
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import lxpb_confluence as C  # noqa: E402
import lxpb_cache  # noqa: E402
import combine_and_scan as CS  # noqa: E402
import render_report as RR  # noqa: E402

CSV_1S = os.path.join(_HERE, "ES_full_1s.csv")
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers_full.csv")
OUT_HTML = os.path.join(_HERE, "strategy_explainer.html")
TICK = 0.25

ENTRY_COLOR = "#60a5fa"
STOP_TIGHT_COLOR = "#f87171"    # impulse-extreme stop (current, ~1 tick beyond burst extreme)
STOP_WIDE_COLOR = "#fbbf24"     # cluster-extreme stop (beyond the farthest confluence level)
TARGET_COLOR = "#4ade80"

# Chosen worked examples (time_pt must exactly match a row in TRIGGERS_CSV),
# picked from the stop/target optimization study to illustrate specific
# concepts (see the write-up text built around each one below).
EXAMPLES = [
    {
        "key": "big_winner",
        "ts": "2026-06-24 12:07:06-07:00",
        "target_pts": 6.0,
        "caption": "Textbook case: tight drawdown, large follow-through",
    },
    {
        "key": "needs_room",
        "ts": "2026-07-23 00:11:03-07:00",
        "target_pts": 6.0,
        "caption": "Real winner that a sub-1pt stop would have missed",
    },
    {
        "key": "false_signal",
        "ts": "2026-06-19 03:22:26-07:00",
        "target_pts": 6.0,
        "caption": "Zone + absorption fired, but the move never came",
    },
]


def _build_example(df1s, h1_df, bar_times_h1, intervals_df, trig_row, idx, target_pts):
    idx_list = df1s.index
    pos_by_ts = {ts: i for i, ts in enumerate(idx_list)}
    pos = pos_by_ts.get(trig_row["time_pt"])
    lookup_ts = CS._window_start_ts(idx_list, pos, bar_times_h1)
    lookup_ts_utc_naive = lookup_ts.tz_convert("UTC").tz_localize(None)
    cd = RR.build_chart_data(df1s, h1_df, bar_times_h1, intervals_df, trig_row, idx, lookup_ts_utc_naive)

    direction = 1 if trig_row["direction"] == "LONG" else -1
    entry = float(trig_row["trigger_close"])
    impulse_extreme = float(trig_row["tested_price"])
    conf_prices = [float(p) for p in str(trig_row["confluence_prices"]).split(";")]
    cluster_extreme = min(conf_prices) if direction == 1 else max(conf_prices)

    stop_tight = impulse_extreme - direction * TICK
    stop_wide = cluster_extreme - direction * TICK
    target = entry + direction * target_pts

    extra_lines = [
        {"price": entry, "color": ENTRY_COLOR, "lineWidth": 2, "lineStyle": 0, "title": f"ENTRY {entry:.2f}"},
        {"price": stop_tight, "color": STOP_TIGHT_COLOR, "lineWidth": 2, "lineStyle": 2,
         "title": f"STOP (impulse) {stop_tight:.2f}"},
        {"price": stop_wide, "color": STOP_WIDE_COLOR, "lineWidth": 2, "lineStyle": 2,
         "title": f"STOP (cluster) {stop_wide:.2f}"},
        {"price": target, "color": TARGET_COLOR, "lineWidth": 2, "lineStyle": 2,
         "title": f"TARGET +{target_pts:.0f}pt {target:.2f}"},
    ]
    cd["priceLines"] = extra_lines + cd["priceLines"]
    cd["_meta"] = {
        "entry": round(entry, 2), "stop_tight": round(stop_tight, 2), "stop_wide": round(stop_wide, 2),
        "target": round(target, 2), "risk_tight": round(abs(entry - stop_tight), 2),
        "risk_wide": round(abs(entry - stop_wide), 2), "direction": trig_row["direction"],
    }
    return cd


CSS_EXTRA = """
<style>
article { max-width: 980px; margin: 0 auto; }
article h2 { color:#e8eaed; font-size:1.15em; margin:36px 0 8px; border-bottom:1px solid var(--border); padding-bottom:6px; }
article h3 { color:#cbd5e1; font-size:0.98em; margin:20px 0 6px; }
article p { line-height:1.55; color:var(--text); font-size:0.92em; }
article ul { line-height:1.55; color:var(--text); font-size:0.92em; }
article code { background:#0a0c0e; padding:1px 5px; border-radius:3px; font-size:0.9em; }
.example-block { margin:14px 0 28px; }
.example-caption { font-size:0.85em; color:var(--text-dim); margin-bottom:6px; }
.legend-row { display:flex; gap:16px; flex-wrap:wrap; font-size:0.8em; margin:6px 0 10px; }
.legend-row span { display:flex; align-items:center; gap:5px; }
.legend-swatch { width:14px; height:3px; display:inline-block; }
.meta-table { font-size:0.82em; margin:8px 0 16px; border-collapse:collapse; }
.meta-table td { padding:2px 10px 2px 0; color:var(--text-dim); }
.meta-table td.v { color:#e8eaed; font-weight:600; }
.refs { font-size:0.82em; color:var(--text-dim); }
.refs a { color:var(--accent); }
</style>
"""

LEGEND_HTML = """
<div class="legend-row">
  <span><span class="legend-swatch" style="background:#60a5fa"></span>Entry (market order)</span>
  <span><span class="legend-swatch" style="background:#f87171"></span>Stop -- impulse extreme (current, ~1 tick beyond burst low/high)</span>
  <span><span class="legend-swatch" style="background:#fbbf24"></span>Stop -- cluster extreme (beyond farthest confluence level)</span>
  <span><span class="legend-swatch" style="background:#4ade80"></span>Target (+6pt example)</span>
  <span><span class="legend-swatch" style="background:#fcd34d"></span>LXPB/LLPB confluence levels</span>
</div>
"""


def example_block_html(ex, cd):
    m = cd["_meta"]
    return f"""
<div class="example-block">
  <div class="example-caption"><strong>{ex['caption']}</strong> &mdash; {cd['title']}</div>
  {LEGEND_HTML}
  <table class="meta-table"><tr>
    <td>Direction</td><td class="v">{m['direction']}</td>
    <td>Entry</td><td class="v">{m['entry']}</td>
    <td>Stop (impulse)</td><td class="v">{m['stop_tight']} ({m['risk_tight']}pt risk)</td>
    <td>Stop (cluster)</td><td class="v">{m['stop_wide']} ({m['risk_wide']}pt risk)</td>
    <td>Target</td><td class="v">{m['target']}</td>
  </tr></table>
  <div class="chart-stack" data-cid="{ex['key']}">
    <div class="chart-cell"><div class="chart-title" id="th1-{ex['key']}"></div><div class="chart-ph" id="ch1-{ex['key']}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="tc-{ex['key']}"></div><div class="chart-ph" id="cc-{ex['key']}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="tb-{ex['key']}">Bid Volume</div><div class="chart-ph" id="cb-{ex['key']}"></div></div>
    <div class="chart-cell"><div class="chart-title" id="ta-{ex['key']}">Ask Volume</div><div class="chart-ph" id="ca-{ex['key']}"></div></div>
  </div>
</div>
"""


ARTICLE_INTRO = """
<article>
<h1>LXPB + Volume-Absorption Strategy — Explained</h1>
<p class="lead">A walkthrough of every concept in this pipeline, with real ES 1-second
examples annotated with entry/stop/target, plus how this compares to what quant/prop
order-flow traders actually use.</p>

<h2>1. What is "absorption"?</h2>
<p>Absorption is when one side (aggressive buyers or sellers hitting the market) throws a
burst of one-sided volume at a price, but the price barely moves or snaps back -- meaning a
passive counter-party (often a large resting order or institutional participant) is
"absorbing" that aggression without giving ground. In footprint/order-flow terminology this
is the classic <strong>high-volume-no-progress</strong> bar, closely related to what Wyckoff
called "stopping volume" at climax points.</p>
<p>Our detector (<code>absorption_backtest.flag_events</code>) looks for a 3-second rolling
window (<code>RUN_LEN=3</code>) where: (a) delta is one-sided the whole window
(<code>Run_OneSided</code>), (b) the summed delta exceeds a minimum imbalance
(<code>Run_DeltaSum</code>, &gt;60 contracts net), (c) volume is a statistical outlier vs the
trailing 60s (<code>Vol_Z &gt; 1.5</code>), and (d) price actually rejects off the run's own
low/high by at least 1 tick on the close (<code>RejectUp/DownTicks</code>) -- i.e. the
aggression got absorbed AND price started to turn, not just stall.</p>

<h2>2. Why gate it with LXPB confluence first?</h2>
<p>A high-volume rejection bar by itself is common and mostly noise -- it needs to happen
<em>somewhere structurally meaningful</em> to be worth trading. LXPB (Last-X-Pre-Breakout)
levels are H1 swing pivots that broke out and flipped role (resistance-that-broke-out becomes
support = LHPB, for longs; support-that-broke-out becomes resistance = LLPB, for shorts). This
pipeline only evaluates the absorption pattern where price sits inside a <strong>stack of
&ge;2 same-type levels within 20 ticks</strong> of each other (a "confluence zone"), and only
counts a level while it hasn't yet been touched/consumed by 1-second price action in that same
hour. This is directionally identical to trading footprint absorption "at a key
support/resistance", just with a rules-based definition of what counts as key S/R instead of
eyeballing a chart.</p>

<h2>3. Entry / stop / target mechanics</h2>
<p>Entry is a market order (assumed zero slippage) at the trigger bar's close, in the
direction absorption implies (SellAbsorption -&gt; LONG, BuyAbsorption -&gt; SHORT). Two stop
placements were compared:</p>
<ul>
<li><strong>Impulse-extreme stop</strong> (originally used): 1 tick beyond the absorption
burst's own 3-bar rolling low/high. Very tight (~0.77pt average) -- it assumes the exact
moment of absorption IS the price extreme, so any further move against you invalidates the
read.</li>
<li><strong>Cluster-extreme stop</strong>: 1 tick beyond the <em>farthest</em> level in the
whole confluence stack (not just the one nearest price). Wider (~3.4pt average) -- treats the
whole stacked zone as one support/resistance band, so a wick through the near level while
still inside the zone doesn't invalidate the trade.</li>
</ul>
<p>Below, each example marks BOTH stops plus a +6pt target (near the empirical
best-performing target from the grid search in section 5) so you can see exactly how much
room each approach gives before the setup is invalidated.</p>
"""

SECTION_4 = """
<h2>4. MFE / MAE: "which trades work, and what stop do they need?"</h2>
<p><strong>MFE</strong> (Maximum Favorable Excursion) is the biggest paper profit the trade
ever reaches looking forward; <strong>MAE-before-peak</strong> is the worst drawdown that
happened on the way to that peak -- i.e. the stop size you'd have actually needed to sit
through the noise and capture the move. Across the 73 full-history signals, bounded to a
realistic 1-hour horizon (not the whole multi-week dataset -- an early version of this
analysis mistakenly let trades run for weeks and reported meaningless 100+pt "MFEs"):</p>
<ul>
<li>64% of signals reach +8pts within 1h; 86% reach +3pts.</li>
<li>Among the 47 "big winner" trades (MFE&ge;8pts), median MAE-before-peak is 3.5pts, 90th
percentile is 12pts -- the current ~0.77pt impulse stop is far tighter than what most real
winners need to breathe.</li>
</ul>
"""

SECTION_5 = """
<h2>5. Grid search: finding a better stop/target</h2>
<p>A brute-force search over fixed stop sizes (0.75-5pts) x fixed targets (3-15pts), capped at
a 1h horizon, found the best total-R combos cluster around <strong>stop 1.5-2pts / target
6pts</strong> (R:R&asymp;3-4, win rate 27-34%) and <strong>stop 4pts / target 10pts</strong>
(win rate 37%, most trades resolved within the hour) -- both clearly outperform the original
sub-1pt impulse-extreme stop. Separately, using the <strong>cluster-extreme</strong> stop
reference (farther, ~3.4pt average risk) instead of the impulse extreme roughly triples the
win rate (35-48% vs 4-15%) at similar total R, i.e. a much less noise-chopped ride, for anyone
who prefers fewer, larger stop-outs over many small ones.</p>
"""

SECTION_6 = """
<h2>6. Volume fine-tuning: what actually predicts a winner?</h2>
<p>Checked whether the burst's own volume/delta size predicts a bigger eventual move -- and
whether the raw <code>Volume</code>/<code>Trades</code> ratio in the .scid data (average
trade size per second) reveals institutional block trades or iceberg orders, a classic
quant/order-flow lead (see references below).</p>
<ul>
<li><strong>vol_z</strong> (how anomalous the burst's volume is vs trailing average): weak
<em>negative</em> correlation with eventual MFE (-0.23) -- a bigger spike is not itself
better, and can even mean more chaotic/exhaustive (less informative) flow.</li>
<li><strong>run_delta_sum</strong> (net one-sided imbalance size): mild positive predictor.</li>
<li><strong>Average trade size</strong> (Volume/Trades on the burst's own bar, a simple
block-trade/iceberg proxy): median 1.06 contracts/trade across all active bars, and even the
largest bursts only reach ~3.0 -- there is <em>no</em> anomalous large-lot signature
separating winners from losers in this dataset. This "obvious lead" didn't pan out here: -0.13
correlation with MFE, non-monotonic tercile split (68% / 54% / 71% big-win rate). Either this
symbol/feed doesn't expose large block prints distinctly (retail-sized order flow throughout),
or the edge here isn't coming from institutional footprint at all.</li>
<li><strong>By far the strongest signal</strong>: whether price is still moving favorably in
the 30 seconds <em>after</em> entry (<code>followup_move_30s</code>, +0.39 correlation,
monotonic tercile split 41% / 76% / 83% big-win rate). This is a genuine confirmation filter
(costs a little entry price, but meaningfully improves selection) -- much more informative
than anything measurable at the trigger bar itself.</li>
</ul>
"""

SECTION_7 = """
<h2>7. How this compares to what quants/prop traders actually do</h2>
<p>This pipeline is a rules-based version of well-established discretionary order-flow
concepts, cross-checked against current practice:</p>
<ul>
<li><strong>Footprint charts &amp; delta divergence</strong> -- retail/prop order-flow
platforms (Bookmap, ATAS, Quantower, ProjectX) visualize per-price bid/ask volume and
cumulative delta (CVD); "absorption" there is exactly our high-volume/no-progress bar, and
"delta divergence" (price makes a new extreme but delta doesn't confirm) is a close cousin of
our rejection-tick condition. <a href="https://gocharting.com/features/orderflow"
target="_blank">GoCharting: Order Flow &amp; Footprint</a>, <a
href="https://algostorm.com/footprint-charts/" target="_blank">AlgoStorm: Footprint Charts
Guide</a>.</li>
<li><strong>Iceberg/block detection</strong> -- institutional desks watch for repeated
absorption at the same price with unusually large individual trade sizes (hidden size
reloading). We tested this directly (section 6) and found no such signature in this
particular symbol/feed.</li>
<li><strong>VPIN (Volume-Synchronized Probability of Informed Trading)</strong>, Easley/L&oacute;pez
de Prado/O'Hara -- an academic/institutional metric that buckets trades by fixed volume (not
clock time) and tracks the rolling buy/sell imbalance per bucket, originally built to flag
"toxic" order flow ahead of the 2010 Flash Crash. Conceptually the same family as our
<code>run_delta_sum</code>/<code>vol_z</code> features, but volume-clocked rather than
1s-clocked -- a natural next iteration would be to rebucket by fixed volume instead of fixed
time. <a href="https://microalphas.com/vpin/" target="_blank">VPIN overview</a>.</li>
<li><strong>Wyckoff Method</strong> -- "Spring"/"Upthrust" + "Sign of Strength/Weakness" at
the edge of a trading range is the original manual version of "absorption at a structural
level, confirmed by follow-through", which is exactly what sections 4-6 above are trying to
quantify (MFE/MAE = did the follow-through actually happen, and how much room did it need).</li>
</ul>
<p class="refs">Research pulled 2026-08-17 via web search; see the linked pages for full
detail on each technique.</p>
</article>
"""


def render():
    df1s = pd.read_csv(CSV_1S, index_col="Time_PT", parse_dates=True)
    trig_df = pd.read_csv(TRIGGERS_CSV, parse_dates=["time_pt"])
    intervals_df, _retests_df, _snapshots, bar_times_h1 = lxpb_cache.get_level_intervals(
        df1s, CSV_1S, RR.H1_CSV, verbose=False)
    h1_df = C.load_ohlc_data(RR.H1_CSV)

    chart_map = {}
    example_html_parts = []
    for ex in EXAMPLES:
        ts = pd.Timestamp(ex["ts"])
        match = trig_df[trig_df["time_pt"] == ts]
        if match.empty:
            raise SystemExit(f"No trigger found for {ex['ts']} -- check EXAMPLES list")
        trig_row = match.iloc[0]
        cd = _build_example(df1s, h1_df, bar_times_h1, intervals_df, trig_row, ex["key"], ex["target_pts"])
        chart_map[ex["key"]] = cd
        example_html_parts.append((ex["key"], example_block_html(ex, cd)))

    charts_json = json.dumps(chart_map).replace("</", "<\\/")
    js = RR.JS_TEMPLATE.replace("__CHARTS_JSON__", charts_json)
    # Auto-render every example chart-stack on load (no expand/collapse needed
    # for a short explainer page -- reuses _renderStack from render_report.py).
    js = js.replace(
        "document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));",
        "document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));\n"
        "Object.keys(CHARTS).forEach(k => _renderStack(k));"
    )

    body_parts = [ARTICLE_INTRO]
    order = {"big_winner": 0, "needs_room": 1, "false_signal": 2}
    example_html_parts.sort(key=lambda kv: order[kv[0]])
    body_parts.append(example_html_parts[0][1])
    body_parts.append(SECTION_4)
    body_parts.append(example_html_parts[1][1])
    body_parts.append(SECTION_5)
    body_parts.append(SECTION_6)
    body_parts.append(example_html_parts[2][1])
    body_parts.append(SECTION_7)
    article = "\n".join(body_parts)

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>LXPB + Volume-Absorption Strategy — Explained</title>
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
{RR.CSS}
{CSS_EXTRA}
</head><body>
{article}
{js}
</body></html>
"""
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {OUT_HTML}")


if __name__ == "__main__":
    render()
