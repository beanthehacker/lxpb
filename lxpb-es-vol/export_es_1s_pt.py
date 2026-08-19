"""
Export ES 1-second bars for a given date/time window in Pacific Time to CSV.

Reads F.US.EPU26.scid (from the AcheronUtils repo), resamples to 1-second
bars, and filters to Aug 13, 2026 08:00:00 - 09:30:00 America/Los_Angeles.
"""
import os
import sys
import pandas as pd

sys.path.insert(0, r"D:\acheron\AcheronUtils")  # scidReader.py lives there
from scidReader import get_scid_df

_HERE = os.path.dirname(os.path.abspath(__file__))
SCID_PATH = r"D:\acheron\AcheronUtils\F.US.EPU26.scid"
OUT_PATH = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")

DATE = "2026-08-13"
START_TIME_PT = "08:00:00"
END_TIME_PT = "09:30:00"


def main():
    df = get_scid_df(SCID_PATH)  # index tz = America/Chicago

    # Convert to Pacific Time for filtering/output
    df.index = df.index.tz_convert("America/Los_Angeles")

    start = pd.Timestamp(f"{DATE} {START_TIME_PT}", tz="America/Los_Angeles")
    end = pd.Timestamp(f"{DATE} {END_TIME_PT}", tz="America/Los_Angeles")

    # Widen the raw slice slightly so resample bins at the edges are complete
    df = df.loc[(df.index >= start) & (df.index <= end)]

    if df.empty:
        print(f"No trades found between {start} and {end}.")
        return

    bars = (
        df.resample("1s")
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
                "Trades": "sum",
                "BidVolume": "sum",
                "AskVolume": "sum",
            }
        )
    )
    bars["Delta"] = bars["AskVolume"] - bars["BidVolume"]
    bars["Close"] = bars["Close"].ffill()
    # Raw scid Open field is unreliable (sentinel values); recompute as prior
    # bar's Close per this codebase's established convention (scidResampler.py)
    bars["Open"] = bars["Close"].shift(1)
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars[["Volume", "Trades", "BidVolume", "AskVolume", "Delta"]] = bars[
        ["Volume", "Trades", "BidVolume", "AskVolume", "Delta"]
    ].fillna(0)

    bars = bars.loc[(bars.index >= start) & (bars.index <= end)]
    bars.index.name = "Time_PT"
    bars.to_csv(OUT_PATH)
    print(f"Wrote {len(bars)} rows to {OUT_PATH}")
    print(bars.head())
    print(bars.tail())


if __name__ == "__main__":
    main()
