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
import hashlib
import numpy as np
import pandas as pd

import analyze_breakout_exits as A
import analyze_breakout_exits_1min as M

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(_HERE, "public", "reports", "exit_analysis_report.html")

STOPS = [2, 3, 4, 6, 8, 10, 12, 16, 20]
TARGETS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]
HOLD_BARS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 36, 48, 72]
THRESHOLDS = [1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0]


def parse_levels(spec):
    """`--stops` / `--targets` value -> list of floats.

    Accepts an inclusive range "lo:hi:step" (e.g. "2:10:0.5") or an explicit
    comma list ("2,3,4.5"). Values are rounded to 4dp so a float-accumulated
    2.9999999 can never split into its own grid row."""
    spec = str(spec).strip()
    if ":" in spec:
        parts = [float(p) for p in spec.split(":")]
        if len(parts) != 3:
            raise ValueError(f"range must be lo:hi:step, got {spec!r}")
        lo, hi, step = parts
        if step <= 0:
            raise ValueError(f"step must be > 0, got {step}")
        n = int(round((hi - lo) / step))
        if abs(lo + n * step - hi) > 1e-9:
            raise ValueError(f"{spec!r}: hi is not lo + k*step")
        return [round(lo + k * step, 4) for k in range(n + 1)]
    return [round(float(p), 4) for p in spec.split(",") if p.strip()]


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
// Multi-column sort: plain click sorts by that column alone (toggles asc/
// desc on repeat clicks of the same sole column); shift+click ADDS that
// column as the next secondary/tertiary/... sort key without resetting
// the ones already chosen (shift+click again on an already-added column
// just toggles its own direction in place, keeping its priority order).
const _sortState = {};
const _SORT_SUPERSCRIPTS = {1:'\u00b9',2:'\u00b2',3:'\u00b3',4:'\u2074',5:'\u2075',6:'\u2076',7:'\u2077'};
function _renderSortHeaders(tableId) {
  const table = document.getElementById(tableId);
  const spec = _sortState[tableId] || [];
  Array.from(table.tHead.rows[0].cells).forEach((th, i) => {
    if (th.dataset.label === undefined) th.dataset.label = th.textContent;
    const priority = spec.findIndex(s => s.col === i);
    if (priority === -1) {
      th.textContent = th.dataset.label;
    } else {
      const arrow = spec[priority].dir === 'asc' ? '\u25b2' : '\u25bc';
      const sup = spec.length > 1 ? (_SORT_SUPERSCRIPTS[priority + 1] || ('^' + (priority + 1))) : '';
      th.textContent = th.dataset.label + ' ' + arrow + sup;
    }
  });
}
function sortTable(tableId, colIdx, numeric, additive) {
  const table = document.getElementById(tableId);
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  let spec = _sortState[tableId] || [];
  if (additive) {
    const existing = spec.find(s => s.col === colIdx);
    if (existing) {
      existing.dir = existing.dir === 'asc' ? 'desc' : 'asc';
    } else {
      spec = spec.concat([{col: colIdx, dir: 'desc', numeric}]);
    }
  } else {
    const onlyThis = spec.length === 1 && spec[0].col === colIdx;
    spec = [{col: colIdx, dir: (onlyThis && spec[0].dir === 'desc') ? 'asc' : 'desc', numeric}];
  }
  _sortState[tableId] = spec;
  function val(row, s) {
    const v = row.cells[s.col].innerText.replace('%','').replace(/,/g,'');
    return s.numeric ? (parseFloat(v) || -Infinity) : v;
  }
  rows.sort((a, b) => {
    for (const s of spec) {
      const x = val(a, s), y = val(b, s);
      let cmp = 0;
      if (x < y) cmp = -1;
      else if (x > y) cmp = 1;
      if (cmp !== 0) return s.dir === 'asc' ? cmp : -cmp;
    }
    return 0;
  });
  rows.forEach(r => tbody.appendChild(r));
  _renderSortHeaders(tableId);
}
function attachSort(tableId, numericCols) {
  const table = document.getElementById(tableId);
  Array.from(table.tHead.rows[0].cells).forEach((th, i) => {
    th.dataset.label = th.textContent;
    th.onclick = (ev) => sortTable(tableId, i, numericCols.includes(i), ev.shiftKey);
  });
}
</script>
"""


def render(output_path, start=None, end=None, limit=A._DEFAULT, merged=False,
           workers=1, grid_end=None, stops=None, targets=None):
    """Renders only the 1-minute-resolved stop/target grid section (all other
    sections -- H1-walk grid, bias comparison, MAE/MFE, time-exit, threshold
    sweep, data-quality caveats/findings -- were dropped per request)."""
    stops = list(STOPS if stops is None else stops)
    targets = list(TARGETS if targets is None else targets)
    print(f"Grid: {len(stops)} stops x {len(targets)} targets = "
          f"{len(stops) * len(targets)} combos", flush=True)
    h1_df = A.load_merged_h1() if merged else None
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows(
        start=start, end=end, limit=limit, h1_df=h1_df)
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    trades = A.simulate(h1_df, pos_by_ts, strong)
    print(f"Selected {len(trades)} strong-breakout trades", flush=True)

    # --- 1-minute-resolved grid (real ticks; escalates to 1s only when a
    # single 1-min bar's own H/L range covers BOTH stop and target) -- see
    # analyze_breakout_exits_1min.py docstring. Only trades whose forward
    # window is covered by the local .scid capture get a result; that
    # capture currently ends well before "today", so recent retests are
    # excluded from this grid, NOT because they're literally in the future.
    #
    # The default single-file 1-min cache is keyed by each trade's position
    # within the trade list, so it is only valid for this script's ORIGINAL
    # canonical selection. Any widened span therefore gets its own
    # selection-tagged chunk cache files instead of poisoning that one.
    sel_tag = f"{start or A.DEFAULT_START}_{end or A.DEFAULT_END}_{'m' if merged else 's'}"
    sel_tag = sel_tag.replace("-", "").replace(":", "").replace(" ", "")
    work_dir = os.path.join(_HERE, "data", "1min_chunks")
    is_default_sel = (start is None and end is None and limit is A._DEFAULT and not merged)

    if workers > 1 and len(trades) > 1:
        chunks = M.chunk_indices_by_contract(trades, workers)
        cache_paths = [os.path.join(
            work_dir,
            f"1min_{sel_tag}_"
            f"{hashlib.sha1(','.join(map(str, idxs)).encode()).hexdigest()[:10]}.csv")
            for idxs in chunks]
        print(f"[parallel] {len(chunks)} contract-pure chunks, {workers} concurrent: "
              + ", ".join(f"c{k:02d}={len(c)}" for k, c in enumerate(chunks)), flush=True)
        series_by_idx = M.build_1min_series_parallel(trades, chunks, cache_paths,
                                                     work_dir, n_workers=workers)
    else:
        cache_path = None if is_default_sel else os.path.join(work_dir, f"1min_{sel_tag}_all.csv")
        if cache_path:
            os.makedirs(work_dir, exist_ok=True)
        series_by_idx = M.build_or_load_1min_series(trades, cache_path=cache_path)

    # The grid may be restricted to a shorter span than the selection above
    # (e.g. build the 1-min series once for the whole year, but report only
    # through Aug 31). Dropping whole trades is exact -- each trade's series
    # and resolution are independent of every other's.
    grid_idx = list(range(len(trades)))
    if grid_end is not None:
        cutoff = pd.Timestamp(grid_end)
        grid_idx = [i for i in grid_idx
                    if pd.Timestamp(trades[i]["retest_time"]) <= cutoff]
        print(f"Grid restricted to retests <= {cutoff:%Y-%m-%d}: "
              f"{len(grid_idx)}/{len(trades)} trades", flush=True)

    grid_trades = [trades[i] for i in grid_idx]
    grid_series = {j: series_by_idx.get(gi) for j, gi in enumerate(grid_idx)}
    covered_trades = [t for j, t in enumerate(grid_trades) if grid_series.get(j) is not None]

    if workers > 1 and len(grid_trades) > 1:
        # Split by TRADES (contract-pure), not by stop value: see
        # stop_target_grid_1min_trade_parallel -- a stop-split worker would
        # have to load every contract the span touches.
        grid_chunks = M.chunk_indices_by_contract(grid_trades, workers)
        grid_1min = M.stop_target_grid_1min_trade_parallel(
            grid_trades, grid_series, stops, targets, grid_chunks, work_dir,
            n_workers=workers)
    else:
        grid_1min = M.stop_target_grid_1min_parallel(grid_trades, grid_series,
                                                     stops, targets, n_workers=4)

    grid_times = {t["retest_time"] for t in grid_trades}
    grid_rows = strong[strong["retest_time"].isin(grid_times)]
    date_lo = grid_rows["retest_time"].min()
    date_hi = grid_rows["retest_time"].max()
    data_desc = ("merged TradingView H1 exports (24aug + 1sep)" if merged
                 else "data\\24aug-CME_MINI_ES1!, 60.csv")
    n_retests = len(retests_df) if grid_end is None else len(grid_rows)

    grid_1min_thead = "".join(f"<th>{c}</th>" for c in
                               ["Stop", "Target", "R:R", "N", "Win %", "Avg R", "Total R",
                                "Wins", "Losses", "No-Hit", "1s escalations", "Forced no-fill"])
    grid_1min_sorted = grid_1min.sort_values("total_R", ascending=False).reset_index(drop=True)
    # A half-point grid must not be rendered with "{:.0f}" -- 2.5 would print
    # as "2" and sit next to the real 2.0 row as an apparent duplicate.
    _st_fmt = ("{:.0f}" if all(float(v).is_integer() for v in stops + targets)
               else "{:.1f}")
    grid_1min_fmt = {
        "stop": _st_fmt, "target": _st_fmt, "rr": "{:.2f}", "n": "{:.0f}",
        "win_rate": lambda v: f"{v*100:.1f}%" if pd.notna(v) else "-", "avg_R": "{:.2f}", "total_R": "{:.1f}",
        "wins": "{:.0f}", "losses": "{:.0f}", "no_hit": "{:.0f}",
        "escalations_1s": "{:.0f}", "forced_no_hit": "{:.0f}",
    }
    grid_1min_body = df_to_html_rows(grid_1min_sorted, grid_1min_fmt, "total_R")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>LXPB Strong-Breakout Exit Analysis</title>
{CSS}
</head><body>
<h1>LXPB Strong-Breakout Exit-Parameter Analysis</h1>
<p class="lead">Retest scope: {n_retests} completed, gap-excluded LXPB retests,
{date_lo:%Y-%m-%d} &rarr; {date_hi:%Y-%m-%d} (data: <code>{data_desc}</code>,
H1 bars). Filtered to <b>{len(grid_rows)} "strong breakout"</b> retests (breakout-bar range &ge;
{A.R.WIDE_BREAKOUT_RATIO_THRESHOLD:.1f}x its own trailing-20-bar average range -- the "P1 Wide Breakout"
default in <code>lxpb_labels_report.html</code>). Every trade is simulated forward from the retest touch bar
for up to {A.HORIZON_BARS} H1 bars ({A.HORIZON_BARS // 24}d); entry is the level's own retest price, no
commission/slippage modeled.</p>

<h2>Stop / Target grid search &mdash; 1-MINUTE-RESOLVED (primary; {len(covered_trades)} tick-covered trades, {len(stops)}&times;{len(targets)} = {len(stops) * len(targets)} combos, click a header to sort, shift+click to add a secondary sort key)</h2>
<p class="note">Real 1-minute bars from local .scid ticks; the actual resolving minute (whichever of
stop/target is touched first) is escalated to real 1-second ticks ({int(grid_1min['escalations_1s'].sum())}
escalations across the whole grid) to pin the exact crossing and enforce fill realism: a TARGET is a resting
LIMIT order and only counts with a qualifying opposite-side print (a LONG's target needs an ask-side/buyer
print, a SHORT's needs a bid-side/seller print), while a STOP is a market/stop order that fills on any side.
When a candidate minute's naive OHLC touch has no qualifying fill, the scan advances to the next candidate
minute instead of crediting an unreal fill ({int(grid_1min['forced_no_hit'].sum())} instances forced all the
way to no-hit this way). "R" = target/stop ratio; rows with stop &lt; 6pt are noise-prone (ES spread/slippage)
and shown for completeness only.</p>
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
    parser.add_argument("--start", default=None,
                        help="first retest date (default: the original 2026-07-01)")
    parser.add_argument("--end", default=None,
                        help="last retest date (default: the original 2026-08-31)")
    parser.add_argument("--limit", default=None,
                        help="max rows, newest-first (int, or 'none' for no cap)")
    parser.add_argument("--merged", action="store_true",
                        help="merge every TradingView H1 export (newest wins)")
    parser.add_argument("--full-year", action="store_true",
                        help="shorthand for --start 2026-01-01 --end 2026-12-31 --limit none "
                             "--merged --grid-end 2026-08-31 (the 1-min series is built for the "
                             "whole selection so its cache matches the full-year trades report's, "
                             "while the grid itself is reported through Aug 31)")
    parser.add_argument("--grid-end", default=None,
                        help="restrict the GRID to retests on/before this date, without "
                             "changing the selection the 1-min cache is keyed to")
    parser.add_argument("--workers", type=int, default=1,
                        help="concurrent contract-pure chunk processes for both the 1-min "
                             "series build and the stop/target grid")
    parser.add_argument("--stops", default=None,
                        help="stop grid: inclusive range 'lo:hi:step' (e.g. '2:10:0.5') "
                             "or a comma list; default is the built-in STOPS")
    parser.add_argument("--targets", default=None,
                        help="target grid: same syntax as --stops (e.g. '1:20:0.5'); "
                             "default is the built-in TARGETS")
    args = parser.parse_args()

    start, end, merged, grid_end = args.start, args.end, args.merged, args.grid_end
    limit = A._DEFAULT
    if args.limit is not None:
        limit = None if str(args.limit).lower() in ("none", "0", "all") else int(args.limit)
    out = args.output
    if args.full_year:
        start = start or "2026-01-01"
        end = end or "2026-12-31"
        limit, merged = None, True
        grid_end = grid_end or "2026-08-31"
        if out == DEFAULT_OUTPUT:
            out = os.path.join(_HERE, "public", "reports", "exit_analysis_report_2026_full_year.html")
    render(out, start=start, end=end, limit=limit, merged=merged,
           workers=args.workers, grid_end=grid_end,
           stops=parse_levels(args.stops) if args.stops else None,
           targets=parse_levels(args.targets) if args.targets else None)

