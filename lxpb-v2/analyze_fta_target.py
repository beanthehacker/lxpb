"""
Instead of a fixed-points target (the STOPS x TARGETS grid in
analyze_breakout_exits_1min.py), use each trade's own FTA level (lxpb.py's
"First Trouble Area" -- the first meaningful price obstacle in the
direction of the trade: min(low)/max(high) of bars strictly between the
breakout and retest bars, tracked as a running min/max -- see
D:\daily-analysis\lxpb-h1-apr2026\README.md for the canonical definition)
as a per-trade, level-derived target price. Re-walks forward at
1-minute resolution (escalating to real 1s ticks for same-minute stop/target
ties), reusing the exact same infra as analyze_breakout_exits_1min.py
(including its 1-minute tick cache) so results are apples-to-apples with
that grid.

For each candidate fixed stop in STOPS, reports win_rate / avg_R / total_R
using target = |fta - entry| (skips trades with no FTA recorded -- i.e. the
retest bar was the very first bar after the breakout bar, so no running_fta
was ever set), and prints the average target size in points so the R
multiple this implies can be compared to the fixed-target grid.
"""
import os
import numpy as np
import pandas as pd

import analyze_breakout_exits as A
import analyze_breakout_exits_1min as M
import render_labels_report as R

STOPS = [2, 3, 4, 6, 8, 10, 12, 16, 20]


def _entry_touch_idx(bars, raw_entry, is_long):
    """Index of the first 1-min bar in `bars` that actually touches
    raw_entry. The retest H1 bar (bars[0:60] roughly) usually approaches
    from the opposite side (e.g. for a long/support retest, price is
    trading ABOVE entry for the early minutes of that hour before dipping
    down to touch it) -- scanning stop/target hits from bar 0 would count
    price action that happened BEFORE the trade was ever entered (a
    look-ahead bug, especially bad for a small level-derived target like
    FTA that can sit below where price was trading pre-touch). Returns
    None if no bar in the whole forward window ever touches entry (should
    not happen for the retest's own hour, but guard anyway)."""
    highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    touched = (lows <= raw_entry) if is_long else (highs >= raw_entry)
    idx = np.flatnonzero(touched)
    return int(idx[0]) if idx.size else None


def load_trades_with_fta():
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    n_bars = len(h1_df)
    trades = []
    for _, row in strong.iterrows():
        is_long = row["type"] == "LHPB"
        entry = float(row["entry_price"])
        start = pos_by_ts[row["retest_time"]]
        if start >= n_bars:
            continue
        fta = row["fta"]
        stop_loss = row["stop_loss"]
        has_fta = fta is not None and fta == fta
        target_dist = abs(float(fta) - entry) if has_fta else None
        stop_dist = abs(entry - float(stop_loss)) if stop_loss == stop_loss else None
        trades.append({
            "type": row["type"], "entry": entry, "is_long": is_long,
            "retest_time": row["retest_time"], "range_ratio": row["range_ratio"],
            "has_fta": has_fta, "target_dist": target_dist, "stop_dist": stop_dist,
        })
    return trades


def stop_target_grid_1min_variable_target(trades, series_by_idx, stops, target_key="target_dist"):
    """Same walk-forward/1s-escalation logic as M.stop_target_grid_1min, but
    `target` is per-trade (trades[i][target_key]) instead of a fixed grid
    value; `stop` is still swept over the fixed STOPS list (unless
    stop_key is used, see stop_target_1min_fully_variable below)."""
    rows = []
    for stop in stops:
        r_list = []
        wins = losses = none_hit = escalations = unresolved_ties = skipped_no_target = 0
        target_pts = []
        for i, t in enumerate(trades):
            target = t.get(target_key)
            if target is None:
                skipped_no_target += 1
                continue
            target_pts.append(target)
            bars = series_by_idx.get(i)
            entry_adj = t["entry"]
            is_long = t["is_long"]
            if bars is None or bars.empty:
                continue
            offset, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
            raw_entry = entry_adj - offset
            touch_idx = _entry_touch_idx(bars, raw_entry, is_long)
            if touch_idx is None:
                skipped_no_target += 1
                continue
            highs = bars["high"].to_numpy(float)[touch_idx:]
            lows = bars["low"].to_numpy(float)[touch_idx:]
            if is_long:
                stop_price, target_price = raw_entry - stop, raw_entry + target
                stop_hit = lows <= stop_price
                target_hit = highs >= target_price
            else:
                stop_price, target_price = raw_entry + stop, raw_entry - target
                stop_hit = highs >= stop_price
                target_hit = lows <= target_price
            s_idx = np.flatnonzero(stop_hit)
            tg_idx = np.flatnonzero(target_hit)
            hs = s_idx[0] if s_idx.size else None
            ht = tg_idx[0] if tg_idx.size else None

            outcome = None
            if hs is None and ht is None:
                none_hit += 1
                last = bars["close"].iloc[-1] + offset
                pnl = (last - entry_adj) if is_long else (entry_adj - last)
                r_list.append(pnl / stop)
                continue
            if hs is not None and ht is not None and hs == ht:
                escalations += 1
                minute_start = bars.index[touch_idx + hs]
                resolved = M._resolve_ambiguous_minute(sym, minute_start, offset, entry_adj,
                                                        stop, target, is_long)
                if resolved is None:
                    unresolved_ties += 1
                    outcome = "target"
                else:
                    outcome = resolved
            elif hs is not None and (ht is None or hs < ht):
                outcome = "stop"
            else:
                outcome = "target"

            if outcome == "target":
                wins += 1
                r_list.append(target / stop)
            else:
                losses += 1
                r_list.append(-1.0)
        r_arr = np.array(r_list)
        n = len(r_arr)
        rows.append({
            "stop": stop, "n": n, "skipped_no_fta": skipped_no_target,
            "avg_target_pts": np.mean(target_pts) if target_pts else np.nan,
            "win_rate": wins / n if n else np.nan,
            "avg_R": r_arr.mean() if n else np.nan,
            "total_R": r_arr.sum() if n else np.nan,
            "wins": wins, "losses": losses, "no_hit": none_hit,
            "ambiguous_minutes": escalations, "unresolved_ties": unresolved_ties,
        })
    return pd.DataFrame(rows)


def stop_target_1min_fully_variable(trades, series_by_idx):
    """Both stop AND target are the natural, level-derived values from
    lxpb.py (stop_loss / fta) -- zero grid search, fully structural."""
    r_list = []
    wins = losses = none_hit = escalations = unresolved_ties = skipped = 0
    stop_pts, target_pts = [], []
    for i, t in enumerate(trades):
        if not t["has_fta"] or t["stop_dist"] is None or t["stop_dist"] <= 0:
            skipped += 1
            continue
        stop = t["stop_dist"]
        target = t["target_dist"]
        stop_pts.append(stop)
        target_pts.append(target)
        bars = series_by_idx.get(i)
        entry_adj = t["entry"]
        is_long = t["is_long"]
        if bars is None or bars.empty:
            continue
        offset, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
        raw_entry = entry_adj - offset
        touch_idx = _entry_touch_idx(bars, raw_entry, is_long)
        if touch_idx is None:
            skipped += 1
            continue
        highs = bars["high"].to_numpy(float)[touch_idx:]
        lows = bars["low"].to_numpy(float)[touch_idx:]
        if is_long:
            stop_price, target_price = raw_entry - stop, raw_entry + target
            stop_hit = lows <= stop_price
            target_hit = highs >= target_price
        else:
            stop_price, target_price = raw_entry + stop, raw_entry - target
            stop_hit = highs >= stop_price
            target_hit = lows <= target_price
        s_idx = np.flatnonzero(stop_hit)
        tg_idx = np.flatnonzero(target_hit)
        hs = s_idx[0] if s_idx.size else None
        ht = tg_idx[0] if tg_idx.size else None
        if hs is None and ht is None:
            none_hit += 1
            last = bars["close"].iloc[-1] + offset
            pnl = (last - entry_adj) if is_long else (entry_adj - last)
            r_list.append(pnl / stop)
            continue
        if hs is not None and ht is not None and hs == ht:
            escalations += 1
            minute_start = bars.index[touch_idx + hs]
            resolved = M._resolve_ambiguous_minute(sym, minute_start, offset, entry_adj,
                                                    stop, target, is_long)
            outcome = resolved if resolved is not None else "target"
            if resolved is None:
                unresolved_ties += 1
        elif hs is not None and (ht is None or hs < ht):
            outcome = "stop"
        else:
            outcome = "target"
        if outcome == "target":
            wins += 1
            r_list.append(target / stop)
        else:
            losses += 1
            r_list.append(-1.0)
    r_arr = np.array(r_list)
    n = len(r_arr)
    return {
        "n": n, "skipped": skipped,
        "avg_stop_pts": np.mean(stop_pts) if stop_pts else np.nan,
        "avg_target_pts": np.mean(target_pts) if target_pts else np.nan,
        "win_rate": wins / n if n else np.nan,
        "avg_R": r_arr.mean() if n else np.nan,
        "total_R": r_arr.sum() if n else np.nan,
        "wins": wins, "losses": losses, "no_hit": none_hit,
        "ambiguous_minutes": escalations, "unresolved_ties": unresolved_ties,
    }


if __name__ == "__main__":
    trades = load_trades_with_fta()
    n_no_fta = sum(1 for t in trades if not t["has_fta"])
    print(f"Trades: {len(trades)} strong breakouts; {n_no_fta} have no FTA recorded "
          f"(retest bar immediately followed breakout bar)")

    series_by_idx = M.build_or_load_1min_series(trades)
    n_missing = sum(1 for v in series_by_idx.values() if v is None)
    print(f"1min series available for {len(series_by_idx) - n_missing}/{len(trades)} trades")

    print("\n=== Fixed stop (grid) + variable target = FTA level (entry-touch-anchored) ===")
    grid_fta = stop_target_grid_1min_variable_target(trades, series_by_idx, STOPS)
    print(grid_fta.round(3).to_string(index=False))

    # Apples-to-apples, same touch-anchored logic: fixed stop/target grid,
    # restricted to the same has_fta trades, for direct comparison per stop.
    fta_ok_idx = [i for i, t in enumerate(trades) if t["has_fta"]]
    trades_ok = [trades[i] for i in fta_ok_idx]
    series_ok = {new_i: series_by_idx.get(old_i) for new_i, old_i in enumerate(fta_ok_idx)}
    TARGETS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]
    grid_rows = []
    for target_val in TARGETS:
        trades_fixed = [{**t, "target_dist": target_val} for t in trades_ok]
        g = stop_target_grid_1min_variable_target(trades_fixed, series_ok, STOPS)
        g.insert(1, "target", target_val)
        grid_rows.append(g)
    grid_fixed = pd.concat(grid_rows, ignore_index=True)
    print(f"\n=== Best FIXED target per stop (same {len(trades_ok)}-trade has-FTA subset, "
          "by total_R, touch-anchored) ===")
    best_fixed_per_stop = (grid_fixed.sort_values("total_R", ascending=False)
                            .groupby("stop", as_index=False).first())
    print(best_fixed_per_stop[["stop", "target", "n", "win_rate", "avg_R", "total_R",
                                "wins", "losses", "no_hit"]]
          .sort_values("stop").round(3).to_string(index=False))

    print("\n=== Fully-variable: stop = lxpb.py stop_loss, target = FTA (zero grid search) ===")
    fully_var = stop_target_1min_fully_variable(trades, series_by_idx)
    print(pd.Series(fully_var).round(3))
