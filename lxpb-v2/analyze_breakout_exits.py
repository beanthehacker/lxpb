"""
One-off research script: for "strong breakout" LXPB retests (phase1_wide_breakout
default-true, i.e. breakout bar range >= WIDE_BREAKOUT_RATIO_THRESHOLD x its own
trailing-20-bar avg range), evaluate:

  1. A stop/target grid search (fixed $ /pt stop-loss and take-profit placed at
     entry +/- N points, walked forward bar-by-bar from the retest bar).
  2. A pure time-based exit grid search (hold N H1 bars after entry, exit at
     that bar's close, no stop/target at all).
  3. A breakout-strength threshold sweep (does raising the "strong" bar improve
     the edge, using the single best fixed exit from #1/#2).

For every trade (regardless of exit rule) also reports MAE (max adverse
excursion), MFE (max favorable excursion), and max drawdown-from-peak-reached
(useful for a later trailing stop) over a fixed lookahead horizon.

Not wired into render_labels_report.py -- disposable analysis, matches that
script's row selection (data/24aug-CME_MINI_ES1!, 60.csv, gap-excluded,
2026-07-01..2026-08-31 retests) so the "72 strong breakouts" figure lines up
with what's shown/filterable in lxpb_labels_report.html.
"""
import os
import sys
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_labels_report as R  # noqa: E402
import lxpb as L  # noqa: E402

DATA_PATH = os.path.join(_HERE, "data", "24aug-CME_MINI_ES1!, 60.csv")
# Every H1 export the reports display/trade off, oldest first. Kept in
# render_labels_report so the tick-chart offset calibration (which must know
# exactly which price scale is on screen -- see its DISPLAY_H1_PATHS comment)
# and the strategy itself can never drift onto different files.
DATA_PATHS = list(R.DISPLAY_H1_PATHS)
HORIZON_BARS = 72  # max H1 bars (3 days) looked ahead for MAE/MFE/drawdown + stop/target sim
TICK = 0.25

# Defaults preserve this script's original, published row selection (the
# "72 strong breakouts" figure quoted in the module docstring). Callers that
# want a different span (e.g. the full-calendar-year report) pass explicit
# start/end/limit -- they must NOT be changed here.
DEFAULT_START = "2026-07-01"
DEFAULT_END = "2026-08-31"
DEFAULT_LIMIT = 300
_DEFAULT = object()  # sentinel: distinguishes "not passed" from limit=None ("no cap")


def load_merged_h1(paths=None):
    """H1 series merged newest-wins across several TradingView exports.

    Each export covers a different (overlapping) span; concatenating them
    oldest-first and dropping duplicate timestamps keeping the LAST gives a
    single continuous series that starts as early as the oldest export and
    ends as late as the newest one. TradingView's ES1! is already
    roll-spliced/continuous, so no back-adjustment is applied here.

    All exports merged here MUST share one splice vintage -- a re-export
    recomputes every roll spread, so mixing vintages would put the same
    historical bar at two different absolute prices (see
    render_labels_report.DISPLAY_H1_PATHS)."""
    paths = paths or DATA_PATHS
    frames = [L.load_ohlc_data(p) for p in paths if os.path.exists(p)]
    if not frames:
        raise FileNotFoundError(f"none of the H1 exports exist: {paths}")
    if len(frames) == 1:
        return frames[0]
    merged = pd.concat(frames)
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def load_strong_breakout_rows(start=None, end=None, limit=_DEFAULT, h1_df=None):
    """Rows for the strong-breakout sample, over an arbitrary date span.

    `start`/`end`/`limit` default to this script's original published
    selection (2026-07-01..2026-08-31, newest 300). Pass an explicit span
    (and `limit=None` for "no cap") to widen it, e.g. a full calendar year.
    `h1_df` lets a caller supply an already-merged/longer H1 series instead
    of the single default export."""
    start = DEFAULT_START if start is None else start
    end = DEFAULT_END if end is None else end
    limit = DEFAULT_LIMIT if limit is _DEFAULT else limit
    if h1_df is None:
        h1_df = L.load_ohlc_data(DATA_PATH)
    _touch_lv0, touch_lv1_df, retests_df = L.detect_lxpb_h1(h1_df)
    retests_df, _n_gap = R.filter_gap_rows(h1_df, retests_df)
    retests_df = retests_df[(retests_df["retest_time"] >= pd.Timestamp(start)) &
                             (retests_df["retest_time"] <= pd.Timestamp(end))]
    retests_df = retests_df.sort_values("retest_time", ascending=False)
    if limit is not None:
        retests_df = retests_df.head(limit)
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)

    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    rng = (h1_df["high"] - h1_df["low"])
    avg_range_20 = rng.rolling(R.AVG_RANGE_WINDOW).mean().shift(1).to_dict()
    all_broken = R.build_all_broken_out(retests_df, touch_lv1_df)

    ratios = []
    for _, row in retests_df.iterrows():
        breakout_range = float(row["breakout_high"] - row["breakout_low"])
        baseline = avg_range_20.get(row["breakout_time"])
        ratios.append((breakout_range / baseline) if baseline and baseline > 0 else None)
    retests_df = retests_df.assign(range_ratio=ratios)
    return h1_df, pos_by_ts, retests_df


def simulate(h1_df, pos_by_ts, retests_df, horizon=HORIZON_BARS):
    """For every row, pull `horizon` forward H1 bars (starting at the retest
    bar itself, since that's where entry_price was actually touched) and
    return per-bar arrays of (high, low, close) offsets from entry, signed
    so positive = favorable, negative = adverse, for both directions."""
    trades = []
    n_bars = len(h1_df)
    for _, row in retests_df.iterrows():
        is_long = row["type"] == "LHPB"
        entry = float(row["entry_price"])
        start = pos_by_ts[row["retest_time"]]
        end = min(start + horizon, n_bars)
        window = h1_df.iloc[start:end]
        if window.empty:
            continue
        highs = window["high"].to_numpy(float)
        lows = window["low"].to_numpy(float)
        closes = window["close"].to_numpy(float)
        opens = window["open"].to_numpy(float)
        if is_long:
            fav = highs - entry
            adv = lows - entry
        else:
            fav = entry - lows
            adv = entry - highs
        trades.append({
            "type": row["type"], "entry": entry, "is_long": is_long,
            "retest_time": row["retest_time"], "range_ratio": row["range_ratio"],
            "opens": opens, "highs": highs, "lows": lows, "closes": closes,
            "fav": fav, "adv": adv, "n": len(window),
        })
    return trades


def mae_mfe_dd_stats(trades):
    maes, mfes, dds = [], [], []
    for t in trades:
        mfe = float(np.max(t["fav"]))
        mae = float(np.min(t["adv"]))  # negative or zero
        # drawdown-from-peak: peak favorable excursion reached so far, minus
        # the worst point reached AFTER that peak (only meaningful once
        # price has moved favorably at all).
        running_peak = np.maximum.accumulate(t["fav"])
        dd = float(np.max(running_peak - t["fav"]))
        maes.append(mae)
        mfes.append(mfe)
        dds.append(dd)
    return pd.Series(maes), pd.Series(mfes), pd.Series(dds)


def stop_target_grid(trades, stops, targets):
    rows = []
    for stop in stops:
        for target in targets:
            r_list = []
            wins = losses = none_hit = 0
            for t in trades:
                fav, adv = t["fav"], t["adv"]
                hit_stop = np.argmax(adv <= -stop) if np.any(adv <= -stop) else None
                hit_target = np.argmax(fav >= target) if np.any(fav >= target) else None
                if hit_stop is None and hit_target is None:
                    none_hit += 1
                    # mark-to-close at horizon end, in R terms
                    r_list.append(fav[-1] / stop if fav[-1] >= 0 else adv[-1] / stop)
                    continue
                if hit_target is not None and (hit_stop is None or hit_target <= hit_stop):
                    wins += 1
                    r_list.append(target / stop)
                else:
                    losses += 1
                    r_list.append(-1.0)
            r_arr = np.array(r_list)
            n = len(r_arr)
            rows.append({
                "stop": stop, "target": target, "n": n,
                "win_rate": wins / n if n else np.nan,
                "avg_R": r_arr.mean() if n else np.nan,
                "total_R": r_arr.sum() if n else np.nan,
                "wins": wins, "losses": losses, "no_hit": none_hit,
            })
    return pd.DataFrame(rows)


def time_exit_grid(trades, hold_bars_list):
    rows = []
    for nb in hold_bars_list:
        pnls = []
        for t in trades:
            if t["n"] <= nb:
                continue  # not enough forward data for this horizon
            pnl_pts = t["fav"][nb] if False else None
            # signed PnL in points at bar `nb` close (fav/adv already signed
            # favorable-positive; use close price directly for clarity)
            entry = t["entry"]
            close_n = t["closes"][nb]
            pnl = (close_n - entry) if t["is_long"] else (entry - close_n)
            pnls.append(pnl)
        s = pd.Series(pnls, dtype=float)
        rows.append({
            "hold_bars": nb, "n": len(s),
            "win_rate": (s > 0).mean() if len(s) else np.nan,
            "mean_pts": s.mean() if len(s) else np.nan,
            "median_pts": s.median() if len(s) else np.nan,
            "std_pts": s.std() if len(s) else np.nan,
            "sharpe_like": (s.mean() / s.std()) if len(s) and s.std() > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def threshold_sweep(h1_df, pos_by_ts, retests_df, thresholds, stop, target):
    rows = []
    for thr in thresholds:
        sub = retests_df[retests_df["range_ratio"].notna() & (retests_df["range_ratio"] >= thr)]
        trades = simulate(h1_df, pos_by_ts, sub)
        if not trades:
            rows.append({"threshold": thr, "n": 0})
            continue
        grid = stop_target_grid(trades, [stop], [target])
        r = grid.iloc[0]
        rows.append({"threshold": thr, "n": len(trades), "win_rate": r["win_rate"],
                     "avg_R": r["avg_R"], "total_R": r["total_R"]})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    h1_df, pos_by_ts, retests_df = load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    print(f"Total retests in scope: {len(retests_df)}; strong breakouts (ratio >= "
          f"{R.WIDE_BREAKOUT_RATIO_THRESHOLD}): {len(strong)}")

    trades = simulate(h1_df, pos_by_ts, strong)
    print(f"Trades with forward data: {len(trades)}\n")

    mae, mfe, dd = mae_mfe_dd_stats(trades)
    print("=== MAE / MFE / max drawdown-from-peak (points), over "
          f"{HORIZON_BARS}-bar horizon ===")
    print(pd.DataFrame({"MAE": mae, "MFE": mfe, "DD_from_peak": dd}).describe(
        percentiles=[.1, .25, .5, .75, .9]).round(2))

    print("\n=== Stop/Target grid search (fixed points, walked forward bar-by-bar) ===")
    stops = [2, 3, 4, 6, 8, 10, 12, 16, 20]
    targets = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]
    grid = stop_target_grid(trades, stops, targets)
    top = grid.sort_values("total_R", ascending=False).head(12)
    print(top.to_string(index=False))
    print("\nBest by avg_R (min n=20):")
    print(grid[grid["n"] >= 20].sort_values("avg_R", ascending=False).head(8).to_string(index=False))
    print("\nBest by avg_R, realistic stop>=6pts (noise/spread-robust):")
    print(grid[grid["stop"] >= 6].sort_values("avg_R", ascending=False).head(8).to_string(index=False))

    print("\n=== Time-based exit grid (hold N H1 bars, no stop/target) ===")
    hold_list = [1, 2, 3, 4, 6, 8, 12, 16, 24, 36, 48, 72]
    te = time_exit_grid(trades, hold_list)
    print(te.round(3).to_string(index=False))

    best_row = grid.sort_values("total_R", ascending=False).iloc[0]
    best_stop, best_target = best_row["stop"], best_row["target"]
    print(f"\n=== Breakout-strength threshold sweep (fixed stop={best_stop}, target={best_target}) ===")
    thr_sweep = threshold_sweep(h1_df, pos_by_ts, retests_df,
                                 [1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0], best_stop, best_target)
    print(thr_sweep.round(3).to_string(index=False))
