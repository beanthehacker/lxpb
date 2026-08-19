"""
Backtest the LXPB-zone-first + volume-absorption trigger signals produced by
combine_and_scan.py: on each confirmed signal, enter a market order (assume
zero slippage) in the trigger direction at the trigger bar's Close, stop
just beyond the absorption burst's own tested extreme (Run_Low for LONG /
Run_High for SHORT, i.e. the `tested_price` column, +/- STOP_BUFFER_TICKS),
and evaluate 3 target variants independently on every trade:

  R3    -- 3x the trade's own stop distance (variable target, fixed R:R = 3)
  PTS5  -- fixed 5.0 points (20 ticks) target regardless of stop distance
  PTS3  -- fixed 3.0 points (12 ticks) target regardless of stop distance

Each trade is simulated forward through 1s bars (no timeout -- runs until
stop or target is hit, or the 1s dataset ends, in which case it's marked
"open"/unresolved). Same-bar tie-break (a single 1s bar's High/Low range
contains both the stop and the target) is resolved pessimistically: STOP
wins. Trades are NOT mutually exclusive/skip-while-open -- each signal is
scored independently, since the R-normalized results below aren't a
position-sizing/portfolio simulation, just a per-signal edge check.
"""
import os
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
TICK = 0.25
STOP_BUFFER_TICKS = 1   # small buffer beyond the absorption burst's own tested extreme

TARGET_VARIANTS = {
    "R3":   {"kind": "R",     "mult": 3.0},
    "PTS5": {"kind": "FIXED", "points": 5.0},
    "PTS3": {"kind": "FIXED", "points": 3.0},
}


def _simulate(highs, lows, i0, n, direction, stop, target):
    """Vectorized forward scan from the bar AFTER i0 to the end of the
    dataset. Returns (outcome, exit_price, exit_i) where outcome is
    'target', 'stop', or 'open' (neither hit before data ends)."""
    if i0 + 1 >= n:
        return "open", None, i0
    seg_high = highs[i0 + 1:]
    seg_low = lows[i0 + 1:]
    if direction == 1:
        stop_hit = seg_low <= stop
        target_hit = seg_high >= target
    else:
        stop_hit = seg_high >= stop
        target_hit = seg_low <= target

    stop_idx = int(np.argmax(stop_hit)) if stop_hit.any() else None
    target_idx = int(np.argmax(target_hit)) if target_hit.any() else None

    if stop_idx is None and target_idx is None:
        return "open", None, n - 1
    if stop_idx is not None and (target_idx is None or stop_idx <= target_idx):
        # Tie (both in the same bar) resolved pessimistically -> stop wins.
        return "stop", stop, i0 + 1 + stop_idx
    return "target", target, i0 + 1 + target_idx


def run(triggers_csv_path, csv_1s_path, out_csv_path, verbose=True):
    df = pd.read_csv(csv_1s_path, index_col="Time_PT", parse_dates=True)
    trig_df = pd.read_csv(triggers_csv_path, parse_dates=["time_pt"])

    times = df.index
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)
    n = len(df)
    pos_by_ts = {ts: i for i, ts in enumerate(times)}

    rows = []
    skipped = 0
    for _, trig in trig_df.iterrows():
        ts = trig["time_pt"]
        i0 = pos_by_ts.get(ts)
        if i0 is None:
            skipped += 1
            continue

        direction = 1 if trig["direction"] == "LONG" else -1
        entry = float(trig["trigger_close"])
        tested_price = float(trig["tested_price"])
        stop = (tested_price - STOP_BUFFER_TICKS * TICK if direction == 1
                else tested_price + STOP_BUFFER_TICKS * TICK)
        risk = abs(entry - stop)
        if risk <= 0:
            skipped += 1
            continue

        row = {
            "time_pt": ts, "direction": trig["direction"], "entry": entry,
            "stop": stop, "risk_ticks": round(risk / TICK, 2),
            "level_type": trig["level_type"],
            "confluence_count": trig["confluence_count"],
        }
        for name, cfg in TARGET_VARIANTS.items():
            if cfg["kind"] == "R":
                target = entry + direction * cfg["mult"] * risk
            else:
                target = entry + direction * cfg["points"]
            outcome, exit_price, exit_i = _simulate(highs, lows, i0, n, direction, stop, target)
            if outcome == "open":
                pnl_ticks = r_mult = hold_s = None
            else:
                pnl_ticks = direction * (exit_price - entry) / TICK
                r_mult = (pnl_ticks * TICK) / risk
                hold_s = exit_i - i0
            row[f"{name}_target"] = round(target, 2)
            row[f"{name}_outcome"] = outcome
            row[f"{name}_pnl_ticks"] = None if pnl_ticks is None else round(pnl_ticks, 2)
            row[f"{name}_R"] = None if r_mult is None else round(r_mult, 3)
            row[f"{name}_hold_s"] = hold_s
        rows.append(row)

    out = pd.DataFrame(rows)
    out.to_csv(out_csv_path, index=False)

    if verbose:
        print(f"Loaded {len(trig_df)} triggers, {skipped} skipped (not found in 1s data / zero risk), "
              f"{len(out)} simulated.")
        print(f"Saved -> {out_csv_path}\n")
        for name in TARGET_VARIANTS:
            closed = out[out[f"{name}_outcome"] != "open"]
            n_open = (out[f"{name}_outcome"] == "open").sum()
            wins = (closed[f"{name}_outcome"] == "target").sum()
            losses = (closed[f"{name}_outcome"] == "stop").sum()
            n_closed = len(closed)
            win_rate = wins / n_closed if n_closed else float("nan")
            avg_R = closed[f"{name}_R"].mean() if n_closed else float("nan")
            avg_pnl_ticks = closed[f"{name}_pnl_ticks"].mean() if n_closed else float("nan")
            total_R = closed[f"{name}_R"].sum() if n_closed else 0.0
            cfg = TARGET_VARIANTS[name]
            desc = (f"3R (target=3x stop distance)" if cfg["kind"] == "R"
                    else f"fixed {cfg['points']:.0f} pts ({cfg['points']/TICK:.0f} ticks)")
            print(f"--- {name}: {desc} ---")
            print(f"  Closed: {n_closed} (wins {wins}, losses {losses}), open/unresolved: {n_open}")
            print(f"  Win rate: {win_rate:.1%}")
            print(f"  Avg R per trade: {avg_R:+.3f}   Total R: {total_R:+.2f}")
            print(f"  Avg PnL (ticks): {avg_pnl_ticks:+.2f}")
            print()
    return out


if __name__ == "__main__":
    import sys
    triggers_csv = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "lxpb_volume_strat_triggers_august.csv")
    csv_1s = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_HERE, "ES_202608_full_1s.csv")
    out_csv = sys.argv[3] if len(sys.argv) > 3 else os.path.join(_HERE, "lxpb_strategy_backtest_trades.csv")
    run(triggers_csv, csv_1s, out_csv)
