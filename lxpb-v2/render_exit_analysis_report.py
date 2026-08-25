"""
Standalone HTML report for the "strong breakout" LXPB exit-parameter research
(stop/target grid search, time-based exit grid, breakout-strength threshold
sweep, MAE/MFE/max-drawdown context) -- reuses the exact row selection and
simulation logic from analyze_breakout_exits.py (same module, imported, not
duplicated) so the numbers always match that script's console output.

Usage:
    python render_exit_analysis_report.py [--output exit_analysis_report.html]

Disposable build artifact -- regenerate any time the underlying data/params
change; not wired into render_labels_report.py or its own report.
"""
import os
import argparse
import numpy as np
import pandas as pd

import analyze_breakout_exits as A
import analyze_breakout_exits_1min as M

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(_HERE, "exit_analysis_report.html")

STOPS = [2, 3, 4, 6, 8, 10, 12, 16, 20]
TARGETS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]
HOLD_BARS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 36, 48, 72]
THRESHOLDS = [1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0]


def stop_target_grid_detailed(trades, stops, targets):
    """Like analyze_breakout_exits.stop_target_grid, but also tracks the
    MAE/MFE/max-drawdown-from-peak actually realized UP TO each trade's own
    exit bar (not the fixed full-horizon numbers), since a tight stop
    trade's real risk profile is very different from a wide-target trade
    that rides the full horizon."""
    rows = []
    for stop in stops:
        for target in targets:
            r_list, maes, mfes, dds = [], [], [], []
            wins = losses = none_hit = 0
            for t in trades:
                fav, adv = t["fav"], t["adv"]
                stop_idxs = np.flatnonzero(adv <= -stop)
                target_idxs = np.flatnonzero(fav >= target)
                hit_stop = stop_idxs[0] if stop_idxs.size else None
                hit_target = target_idxs[0] if target_idxs.size else None
                if hit_stop is None and hit_target is None:
                    exit_idx = len(fav) - 1
                    none_hit += 1
                    r_list.append(fav[-1] / stop if fav[-1] >= 0 else adv[-1] / stop)
                elif hit_target is not None and (hit_stop is None or hit_target <= hit_stop):
                    exit_idx = hit_target
                    wins += 1
                    r_list.append(target / stop)
                else:
                    exit_idx = hit_stop
                    losses += 1
                    r_list.append(-1.0)
                fav_slice, adv_slice = fav[:exit_idx + 1], adv[:exit_idx + 1]
                maes.append(float(adv_slice.min()))
                mfes.append(float(fav_slice.max()))
                running_peak = np.maximum.accumulate(fav_slice)
                dds.append(float((running_peak - fav_slice).max()))
            r_arr = np.array(r_list)
            n = len(r_arr)
            rows.append({
                "stop": stop, "target": target, "rr": round(target / stop, 2), "n": n,
                "win_rate": wins / n if n else np.nan,
                "avg_R": r_arr.mean() if n else np.nan,
                "total_R": r_arr.sum() if n else np.nan,
                "wins": wins, "losses": losses, "no_hit": none_hit,
                "avg_mae": np.mean(maes) if maes else np.nan,
                "avg_mfe": np.mean(mfes) if mfes else np.nan,
                "avg_dd": np.mean(dds) if dds else np.nan,
            })
    return pd.DataFrame(rows)


def df_to_html_rows(df, fmt, highlight_col=None, highlight_n_col=None, min_n=0):
    """fmt: dict col -> format string (or callable). highlight_col: column
    whose max value gets a 'best-row' CSS class (optionally restricted to
    rows with n >= min_n via highlight_n_col)."""
    best_idx = None
    if highlight_col is not None:
        cand = df[df[highlight_n_col] >= min_n] if highlight_n_col else df
        if not cand.empty:
            best_idx = cand[highlight_col].idxmax()
    rows_html = []
    for idx, row in df.iterrows():
        cls = "best-row" if idx == best_idx else ""
        cells = []
        for col in df.columns:
            v = row[col]
            f = fmt.get(col, "{}")
            try:
                text = f(v) if callable(f) else f.format(v)
            except (ValueError, TypeError):
                text = str(v)
            extra_cls = ""
            if col == "win_rate" and pd.notna(v):
                extra_cls = " good" if v >= 0.6 else (" bad" if v < 0.45 else "")
            if col in ("avg_R", "total_R", "mean_pts") and pd.notna(v):
                extra_cls = " good" if v > 0 else (" bad" if v < 0 else "")
            cells.append(f'<td class="{extra_cls.strip()}">{text}</td>')
        rows_html.append(f'<tr class="{cls}">{"".join(cells)}</tr>')
    return "\n".join(rows_html)


CSS = """
<style>
:root { --bg:#111316; --surface:#1c1f24; --surface2:#22262d; --border:#2e333b;
        --text:#d4d8df; --text-dim:#6b7280; --text-faint:#444c58;
        --bull:#4ade80; --bear:#f87171; --accent:#60a5fa; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       padding:16px 24px; max-width:1500px; margin:0 auto;
       background:var(--bg); color:var(--text); }
h1 { font-size:1.5em; margin:0 0 4px; color:#e8eaed; }
h2 { font-size:1.15em; margin:28px 0 8px; color:#e8eaed; border-bottom:1px solid var(--border); padding-bottom:4px; }
p.lead { color:var(--text-dim); margin:0 0 12px; font-size:0.87em; }
p.note { color:var(--text-dim); font-size:0.85em; margin:4px 0 12px; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0; }
.summary .box { padding:8px 14px; background:var(--surface); border:1px solid var(--border);
                border-radius:6px; font-size:0.82em; line-height:1.3; }
.summary .box strong { display:block; font-size:1.3em; color:#e8eaed; }
table { border-collapse:collapse; width:100%; font-size:0.83em; margin-bottom:6px; }
th, td { padding:5px 9px; text-align:right; border-bottom:1px solid var(--border); white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { background:var(--surface2); color:var(--text-dim); position:sticky; top:0; cursor:pointer; user-select:none; }
th:hover { color:var(--accent); }
tr:hover td { background:#1a1d22; }
tr.best-row td { background:#16321f !important; font-weight:600; }
td.good { color:var(--bull); }
td.bad { color:var(--bear); }
.table-wrap { max-height:520px; overflow-y:auto; border:1px solid var(--border); border-radius:6px; }
.findings { background:var(--surface); border:1px solid var(--border); border-radius:6px;
            padding:14px 18px; margin:10px 0 24px; }
.findings li { margin:6px 0; line-height:1.45; font-size:0.9em; }
.findings b { color:#e8eaed; }
code { background:#0a0c0e; padding:1px 5px; border-radius:3px; font-size:0.9em; }
</style>
"""

SORT_JS = """
<script>
function sortTable(tableId, colIdx, numeric) {
  const table = document.getElementById(tableId);
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const asc = table.dataset.sortCol == colIdx && table.dataset.sortDir !== 'asc';
  rows.sort((a, b) => {
    let x = a.cells[colIdx].innerText.replace('%','').replace(/,/g,'');
    let y = b.cells[colIdx].innerText.replace('%','').replace(/,/g,'');
    if (numeric) { x = parseFloat(x) || -Infinity; y = parseFloat(y) || -Infinity; }
    if (x < y) return asc ? -1 : 1;
    if (x > y) return asc ? 1 : -1;
    return 0;
  });
  rows.forEach(r => tbody.appendChild(r));
  table.dataset.sortCol = colIdx;
  table.dataset.sortDir = asc ? 'asc' : 'desc';
}
function attachSort(tableId, numericCols) {
  const table = document.getElementById(tableId);
  Array.from(table.tHead.rows[0].cells).forEach((th, i) => {
    th.onclick = () => sortTable(tableId, i, numericCols.includes(i));
  });
}
</script>
"""


def render(output_path):
    """Renders only the 1-minute-resolved stop/target grid section (all other
    sections -- H1-walk grid, bias comparison, MAE/MFE, time-exit, threshold
    sweep, data-quality caveats/findings -- were dropped per request)."""
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    trades = A.simulate(h1_df, pos_by_ts, strong)

    # --- 1-minute-resolved grid (real ticks; escalates to 1s only when a
    # single 1-min bar's own H/L range covers BOTH stop and target) -- see
    # analyze_breakout_exits_1min.py docstring. Only trades whose forward
    # window is covered by the local .scid capture get a result; that
    # capture currently ends well before "today", so recent retests are
    # excluded from this grid, NOT because they're literally in the future.
    series_by_idx = M.build_or_load_1min_series(trades)
    covered_idx = [i for i in range(len(trades)) if series_by_idx.get(i) is not None]
    covered_trades = [trades[i] for i in covered_idx]
    grid_1min = M.stop_target_grid_1min(trades, series_by_idx, STOPS, TARGETS)

    date_lo = retests_df["retest_time"].min()
    date_hi = retests_df["retest_time"].max()

    grid_1min_thead = "".join(f"<th>{c}</th>" for c in
                               ["Stop", "Target", "R:R", "N", "Win %", "Avg R", "Total R",
                                "Wins", "Losses", "No-Hit", "Ambig. min.", "Unresolved"])
    grid_1min_sorted = grid_1min.sort_values("total_R", ascending=False).reset_index(drop=True)
    grid_1min_fmt = {
        "stop": "{:.0f}", "target": "{:.0f}", "rr": "{:.2f}", "n": "{:.0f}",
        "win_rate": lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-", "avg_R": "{:.2f}", "total_R": "{:.1f}",
        "wins": "{:.0f}", "losses": "{:.0f}", "no_hit": "{:.0f}",
        "ambiguous_minutes": "{:.0f}", "unresolved_ties": "{:.0f}",
    }
    grid_1min_body = df_to_html_rows(grid_1min_sorted, grid_1min_fmt, "total_R")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>LXPB Strong-Breakout Exit Analysis</title>
{CSS}
</head><body>
<h1>LXPB Strong-Breakout Exit-Parameter Analysis</h1>
<p class="lead">Retest scope: {len(retests_df)} completed, gap-excluded LXPB retests,
{date_lo:%Y-%m-%d} &rarr; {date_hi:%Y-%m-%d} (data: <code>data\\24aug-CME_MINI_ES1!, 60.csv</code>,
H1 bars). Filtered to <b>{len(strong)} "strong breakout"</b> retests (breakout-bar range &ge;
{A.R.WIDE_BREAKOUT_RATIO_THRESHOLD:.1f}x its own trailing-20-bar average range -- the "P1 Wide Breakout"
default in <code>lxpb_labels_report.html</code>). Every trade is simulated forward from the retest touch bar
for up to {A.HORIZON_BARS} H1 bars ({A.HORIZON_BARS // 24}d); entry is the level's own retest price, no
commission/slippage modeled.</p>

<h2>Stop / Target grid search &mdash; 1-MINUTE-RESOLVED (primary; {len(covered_trades)} tick-covered trades, click a header to sort)</h2>
<p class="note">Real 1-minute bars from local .scid ticks, escalating to real 1-second ticks only for the
{int(grid_1min['ambiguous_minutes'].sum())} instances where a single minute's own H/L range covered BOTH
the stop and target simultaneously (of those, {int(grid_1min['unresolved_ties'].sum())} still couldn't be
resolved even at 1s and fell back to the old optimistic "target wins" tie-break). "R" = target/stop ratio;
rows with stop &lt; 6pt are noise-prone (ES spread/slippage) and shown for completeness only.</p>
<div class="table-wrap"><table id="grid1min-table"><thead><tr>{grid_1min_thead}</tr></thead>
<tbody>{grid_1min_body}</tbody></table></div>

{SORT_JS}
<script>
attachSort('grid1min-table', [0,1,2,3,4,5,6,7,8,9,10,11]);
</script>
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render LXPB strong-breakout exit analysis HTML report")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output HTML path")
    args = parser.parse_args()
    render(args.output)
