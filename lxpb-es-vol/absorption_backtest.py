"""
Refined micro-absorption detector + simple rule-based reversal backtest.

Adds two things the coarse 300s-z-score version missed for short, sharp
"micro-absorption" bursts like 08:58:09-11:
  1. A LOCAL (60s) rolling baseline instead of 300s, so the detector adapts
     to the current volatility regime rather than the whole session.
  2. A "delta-run" feature: sum of Delta over a rolling 3-second window that
     is entirely one-sided (all bars same sign) -> captures a short climax of
     persistent aggressive flow into a level, even if no single 1s bar alone
     is an extreme outlier.
  3. A "rejection tail" feature: for a down-run, how far the Close recovers
     off the run's Low (in ticks) -> the classic Wyckoff "spring"/absorption
     tell (aggressive selling probes lower, but sellers can't hold price down).

Then runs a toy backtest: on each flagged event, fade the exhausted flow
(buy after sell-absorption, sell after buy-absorption), stop beyond the
run's extreme, target = 2x the stop distance, timeout after 120s.
"""
import numpy as np
import pandas as pd

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")
TICK = 0.25

LOCAL_WINDOW = 60
RUN_LEN = 3
VOL_Z_THRESH = 1.5
RUN_DELTA_MIN = 60          # abs sum of a 3s one-sided delta run
REJECTION_MIN_TICKS = 1     # close must recover >= this many ticks off the run low/high


def build_features(df):
    df = df.copy()
    df["Range"] = df["High"] - df["Low"]
    df["CVD"] = df["Delta"].cumsum()

    roll = df["Volume"].rolling(LOCAL_WINDOW, min_periods=20)
    df["Vol_Z"] = (df["Volume"] - roll.mean()) / roll.std()

    # 3s rolling delta run-sum, and whether all 3 bars share the run's sign
    df["Run_DeltaSum"] = df["Delta"].rolling(RUN_LEN).sum()
    same_sign = (
        (df["Delta"] > 0).rolling(RUN_LEN).sum().isin([0, RUN_LEN])
    )
    df["Run_OneSided"] = same_sign

    df["Run_Low"] = df["Low"].rolling(RUN_LEN).min()
    df["Run_High"] = df["High"].rolling(RUN_LEN).max()
    # rejection = how many ticks Close sits above the run Low (sell-absorption)
    # or below the run High (buy-absorption)
    df["RejectUp_Ticks"] = (df["Close"] - df["Run_Low"]) / TICK
    df["RejectDown_Ticks"] = (df["Run_High"] - df["Close"]) / TICK
    return df


def flag_events(df):
    bullish = (
        df["Run_OneSided"] & (df["Run_DeltaSum"] < -RUN_DELTA_MIN)
        & (df["Vol_Z"] > VOL_Z_THRESH)
        & (df["RejectUp_Ticks"] >= REJECTION_MIN_TICKS)
    )
    bearish = (
        df["Run_OneSided"] & (df["Run_DeltaSum"] > RUN_DELTA_MIN)
        & (df["Vol_Z"] > VOL_Z_THRESH)
        & (df["RejectDown_Ticks"] >= REJECTION_MIN_TICKS)
    )
    df["SellAbsorption"] = bullish   # aggressive selling absorbed -> bullish
    df["BuyAbsorption"] = bearish    # aggressive buying absorbed -> bearish
    return df


def backtest(df, target_mult=2.0, timeout_s=120):
    trades = []
    i = 0
    n = len(df)
    times = df.index
    highs, lows, closes = df["High"].values, df["Low"].values, df["Close"].values
    run_low, run_high = df["Run_Low"].values, df["Run_High"].values
    sell_abs, buy_abs = df["SellAbsorption"].values, df["BuyAbsorption"].values

    last_exit_idx = -1
    for i in range(len(df)):
        if i <= last_exit_idx:
            continue
        direction = None
        if sell_abs[i]:
            direction = 1
            entry = closes[i]
            stop = run_low[i] - TICK
        elif buy_abs[i]:
            direction = -1
            entry = closes[i]
            stop = run_high[i] + TICK
        else:
            continue

        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = entry + direction * target_mult * risk

        outcome, exit_price, exit_i = "timeout", closes[min(i + timeout_s, n - 1)], min(i + timeout_s, n - 1)
        for j in range(i + 1, min(i + 1 + timeout_s, n)):
            if direction == 1:
                if lows[j] <= stop:
                    outcome, exit_price, exit_i = "stop", stop, j
                    break
                if highs[j] >= target:
                    outcome, exit_price, exit_i = "target", target, j
                    break
            else:
                if highs[j] >= stop:
                    outcome, exit_price, exit_i = "stop", stop, j
                    break
                if lows[j] <= target:
                    outcome, exit_price, exit_i = "target", target, j
                    break

        pnl_ticks = direction * (exit_price - entry) / TICK
        trades.append({
            "time": times[i], "direction": "LONG" if direction == 1 else "SHORT",
            "entry": entry, "stop": stop, "target": target,
            "outcome": outcome, "exit_price": exit_price,
            "pnl_ticks": pnl_ticks, "hold_s": exit_i - i,
        })
        last_exit_idx = exit_i
    return pd.DataFrame(trades)


def main():
    df = pd.read_csv(CSV_PATH, index_col="Time_PT", parse_dates=True)
    df = build_features(df)
    df = flag_events(df)

    print("--- 08:58:05 to 08:58:15 refined features ---")
    print(df.between_time("08:58:05", "08:58:15")[
        ["Close", "Volume", "Delta", "Vol_Z", "Run_DeltaSum", "Run_OneSided",
         "RejectUp_Ticks", "SellAbsorption", "BuyAbsorption"]].round(2).to_string())

    trades = backtest(df)
    print(f"\nTotal signals traded: {len(trades)}")
    if len(trades):
        print(trades.round(2).to_string())
        wins = (trades["pnl_ticks"] > 0).sum()
        print(f"\nWin rate: {wins}/{len(trades)} = {wins/len(trades):.1%}")
        print(f"Avg PnL (ticks): {trades['pnl_ticks'].mean():.2f}  "
              f"Total PnL (ticks): {trades['pnl_ticks'].sum():.1f}  "
              f"Expectancy per trade (ticks): {trades['pnl_ticks'].mean():.2f}")
    trades.to_csv(os.path.join(_HERE, "ES_20260813_absorption_backtest_trades.csv"), index=False)


if __name__ == "__main__":
    main()
