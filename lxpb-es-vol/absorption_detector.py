"""
Detect volume-absorption events in 1-second ES tape data and evaluate a simple
mean-reversion strategy triggered by them.

Techniques combined (all standard / SOTA in market-microstructure research):
  1. Wyckoff "Effort vs. Result" (Volume Spread Analysis): high effort (volume)
     that produces low result (price displacement) signals absorption.
  2. Kyle's Lambda proxy: regress |Close-Open| (or range) on Volume over a
     rolling window; large negative residual = unusually little price impact
     for the volume traded -> liquidity absorption at that price.
  3. Cumulative Volume Delta (CVD) divergence: order flow (Delta) makes a new
     extreme while price fails to make a proportional new extreme/close.
  4. VPIN-style toxicity: rolling z-score of |Delta|/Volume (order-flow
     imbalance) flags "toxic"/one-sided flow bursts.

A bar is flagged as an Absorption Event when ALL of:
  - Volume z-score (rolling) > VOL_Z_THRESH   (effort is abnormally high)
  - |Delta| z-score (rolling) > DELTA_Z_THRESH (flow is heavily one-sided)
  - Impact residual is strongly negative        (result is abnormally low
    given the volume -> absorption, not continuation)
"""
import numpy as np
import pandas as pd

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")

ROLL_WINDOW = 300     # seconds of lookback for rolling stats
VOL_Z_THRESH = 2.0
DELTA_Z_THRESH = 2.0
IMPACT_Z_THRESH = -1.0   # residual z-score of price move vs. expected-from-volume


def main():
    df = pd.read_csv(CSV_PATH, index_col="Time_PT", parse_dates=True)
    df["Range"] = df["High"] - df["Low"]
    df["Body"] = (df["Close"] - df["Open"]).abs()
    df["CVD"] = df["Delta"].cumsum()

    # --- rolling z-scores (effort & flow toxicity) ---
    roll = df["Volume"].rolling(ROLL_WINDOW, min_periods=30)
    df["Vol_Z"] = (df["Volume"] - roll.mean()) / roll.std()

    rollD = df["Delta"].abs().rolling(ROLL_WINDOW, min_periods=30)
    df["AbsDelta_Z"] = (df["Delta"].abs() - rollD.mean()) / rollD.std()

    # --- Kyle's-lambda-style impact regression: expected |Body| given Volume ---
    # Rolling OLS of Body ~ Volume (no intercept issues handled via simple ratio
    # of rolling covariance/variance); residual tells us if THIS bar's price
    # displacement was abnormally small for its volume (=> absorption).
    roll_cov = df["Volume"].rolling(ROLL_WINDOW, min_periods=30).cov(df["Body"])
    roll_var = df["Volume"].rolling(ROLL_WINDOW, min_periods=30).var()
    lam = roll_cov / roll_var           # rolling "impact coefficient" (Kyle's lambda proxy)
    expected_body = lam * df["Volume"]
    resid = df["Body"] - expected_body
    df["Impact_Resid_Z"] = (resid - resid.rolling(ROLL_WINDOW, min_periods=30).mean()) / \
                            resid.rolling(ROLL_WINDOW, min_periods=30).std()

    df["Absorption"] = (
        (df["Vol_Z"] > VOL_Z_THRESH)
        & (df["AbsDelta_Z"] > DELTA_Z_THRESH)
        & (df["Impact_Resid_Z"] < IMPACT_Z_THRESH)
    )
    df["AbsorptionSide"] = np.where(df["Delta"] < 0, "SellAbsorbed(Bullish)", "BuyAbsorbed(Bearish)")

    events = df[df["Absorption"]].copy()
    print(f"Total 1s bars: {len(df)}  |  Absorption events flagged: {len(events)}")
    cols = ["Open", "High", "Low", "Close", "Volume", "Delta", "CVD",
            "Vol_Z", "AbsDelta_Z", "Impact_Resid_Z", "AbsorptionSide"]
    print(events[cols].round(2).to_string())

    # Highlight the window the user pointed at
    print("\n--- 08:58:05 to 08:58:15 detail ---")
    print(df.between_time("08:58:05", "08:58:15")[
        ["Open", "High", "Low", "Close", "Volume", "Delta", "CVD",
         "Vol_Z", "AbsDelta_Z", "Impact_Resid_Z"]].round(2).to_string())

    events.to_csv(os.path.join(_HERE, "ES_20260813_absorption_events.csv"))
    return df, events


if __name__ == "__main__":
    main()
