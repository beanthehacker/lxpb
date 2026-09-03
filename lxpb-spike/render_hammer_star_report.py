"""
Per-trade H1 chart report for the hammer/shooting-star + strong-breakout
LXPB strategy (see analyze_hammer_star_strategy.py), styled identically to
../lxpb-v2/stop2_target2_trades_report.html (render_stop_target_report.py):
same dark theme, expandable table rows, H1 candlestick chart + 1s
price/bid/ask trio + 1min context + tick footprint chart stack.

Reuses ../lxpb-v2's render_stop_target_report.py wholesale for the chart
building / CSS / JS (build_trade_chart, CSS, JS, _relabel_fta_as_target)
and render_labels_report.py for the real-tick 1s/1min/footprint charts
(build_1s_trio_chart) -- only the trade SELECTION (this strategy's hammer/
shooting-star + strong-breakout filter, fixed stop=3/target=9 bracket)
differs from that module's own `render()`.

Usage:
    python render_hammer_star_report.py
    python render_hammer_star_report.py --stop 3 --target 9 --output stop3_target9_trades_report.html
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_LXPB_V2 = os.path.abspath(os.path.join(_HERE, os.pardir, "lxpb-v2"))
for _p in (_LXPB_V2, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import render_labels_report as R  # noqa: E402
import render_stop_target_report as S  # noqa: E402
import analyze_breakout_exits_1min as M  # noqa: E402
import analyze_hammer_star_strategy as H  # noqa: E402

DEFAULT_OUTPUT = os.path.join(_HERE, "stop3_target9_trades_report.html")


def render(stop, target, output_path, start=H.DEFAULT_START, end=H.DEFAULT_END):
    h1_df, rows_df, _all_broken, funnel = H.load_strategy_rows(start=start, end=end)
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}

    trades = H.build_trades(rows_df, stop, target)
    series_by_idx = M.build_or_load_1min_series(trades, cache_path=H.CACHE_1MIN_PATH)
    resolved_list = S.resolve_trades(trades, series_by_idx, stop, target)

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
        row_d = rows_df.iloc[i]
        chart = S.build_trade_chart(h1_df, pos_by_ts, row_d, trade, resolved, stop, target)

        level_type = row_d["type"]
        price = float(row_d["price"])
        is_long = level_type == "LHPB"
        target_price = price + target if is_long else price - target
        stop_price = price - stop if is_long else price + stop

        entry_price_adj = float(row_d["entry_price"])
        row_for_trio = row_d.copy()
        row_for_trio["fta"] = entry_price_adj + target if is_long else entry_price_adj - target
        row_for_trio["stop_loss"] = entry_price_adj - stop if is_long else entry_price_adj + stop
        trio_chart = R.build_1s_trio_chart(row_for_trio, R.PAD_SECONDS_DEFAULT,
                                            R.ONE_MIN_PAD_MINUTES_DEFAULT, True,
                                            touch_time_override=resolved.get("touch_time"))
        if trio_chart is not None:
            S._relabel_fta_as_target(trio_chart["trio"])
            S._relabel_fta_as_target(trio_chart["oneMin"])
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
        entry_str = R._to_pt_str(resolved["touch_time"]) if resolved.get("touch_time") is not None \
            else R._to_pt_str(row_d["retest_time"])
        type_cls = "type-lhpb" if is_long else "type-llpb"
        pattern_label = "Hammer" if is_long else "Shooting Star"

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
  <td class="left">{pattern_label}</td>
  <td class="left">{entry_str}</td>
  <td>{price:.2f}</td><td>{stop_price:.2f}</td><td>{target_price:.2f}</td>
  <td>{row_d['range_ratio']:.2f}</td>
  <td class="{outcome_cls}">{outcome_label}</td><td class="{outcome_cls}">{r_str}</td>
  <td class="left">{exit_str}</td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}"
      onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="11"><div class="chart-stack">
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
<h1>LXPB Hammer/Shooting-Star + Strong-Breakout Trades &mdash; Stop {stop:.0f} / Target {target:.0f}</h1>
<p class="lead">H1 LXPB retests ({start} .. {end}) where the level's FORMATION bar is a genuine
pattern for its type &mdash; <b>LHPB valid only if formation is a HAMMER</b>, <b>LLPB valid only if
formation is a SHOOTING STAR</b> (per <code>D:\\daily-analysis\\patterns-pure</code>'s
<code>find_hammer</code> / <code>find_shooting_star</code>, applied to the formation bar) &mdash; AND
the level's original breakout bar was a strong impulse (breakout-bar range &ge;
{R.WIDE_BREAKOUT_RATIO_THRESHOLD:.1f}&times; its trailing 20-bar average range, same
<code>breakout_range_ratio</code> definition as
<code>../retest-vol-scalp/lxpb_fade_report.html</code>'s "Breakout strength" section).
Funnel: {funnel['rows']} completed retests in window (+{funnel['gap_rows_dropped']} gap rows dropped)
&rarr; {funnel['valid_formation']} valid hammer/shooting-star formations &rarr;
{funnel['strong_breakout']} also strong-breakout &mdash; the {len(trades)} trades below.
Entry is anchored to the real 1-second-tick instant the level was actually FILLABLE within the
retest H1 bar (aggressor-side fill, not just any-side touch); exits are pinned to the exact
second/price via real 1s ticks, with a fixed stop={stop:.0f}pt / target={target:.0f}pt bracket.
Click a row to expand its H1 chart: gold line = entry level, green dashed = target, red dashed =
stop; P0/P1/P2 markers mark formation/breakout/retest, and a 4th green/red arrow marks the resolved
exit bar.</p>
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
  <th class="left">#</th><th class="left">Type</th><th class="left">Formation</th>
  <th class="left">Entry (touch) time</th>
  <th>Entry</th><th>Stop</th><th>Target</th><th>BO ratio</th><th>Outcome</th><th>R</th>
  <th class="left">Exit time</th><th class="expand-th">\u25b6</th>
</tr></thead>
<tbody>
"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>LXPB Hammer/Star Stop {stop:.0f} / Target {target:.0f} Trades</title>
{S.CSS}
</head><body>
{header}
<div class="table-wrap">{thead}
{''.join(rows_html)}
</tbody></table></div>
{S.JS.replace("__CHARTS_JSON__", json.dumps(charts))}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}  ({len(trades)} trades, {wins}W/{losses}L/{no_hits}NH, "
          f"win rate {win_rate*100:.1f}%, avg_R {avg_r:.2f}, total_R {total_r:.1f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hammer/shooting-star + strong-breakout LXPB trades report")
    parser.add_argument("--stop", type=float, default=H.STOP_DEFAULT)
    parser.add_argument("--target", type=float, default=H.TARGET_DEFAULT)
    parser.add_argument("--start", default=H.DEFAULT_START)
    parser.add_argument("--end", default=H.DEFAULT_END)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    out = args.output or os.path.join(
        _HERE, f"stop{args.stop:.0f}_target{args.target:.0f}_trades_report.html")
    render(args.stop, args.target, out, args.start, args.end)
