"""
Export ES 1-second bars for an arbitrary date/time range in Pacific Time to
CSV. Generalized version of export_es_1s_pt.py (which stays as-is, hardcoded
to the original 2026-08-13 08:00-09:30 PT ad-hoc window) -- this one is used
for larger multi-day scans, e.g. the "whole of August 2026" backtest.

Usage: python export_es_1s_range.py <start_pt> <end_pt> <out_csv>
  start_pt/end_pt: "YYYY-MM-DD HH:MM:SS" in America/Los_Angeles
"""
import os
import sys
import pandas as pd

sys.path.insert(0, r"D:\acheron\AcheronUtils")  # scidReader.py lives there
from scidReader import get_scid_df

SCID_PATH = r"D:\acheron\AcheronUtils\F.US.EPU26.scid"


def export(start_pt, end_pt, out_path):
    df = get_scid_df(SCID_PATH)  # index tz = America/Chicago
    df.index = df.index.tz_convert("America/Los_Angeles")

    start = pd.Timestamp(start_pt, tz="America/Los_Angeles")
    end = pd.Timestamp(end_pt, tz="America/Los_Angeles")

    df = df.loc[(df.index >= start) & (df.index <= end)]
    if df.empty:
        print(f"No trades found between {start} and {end}.")
        return None

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
    bars["Open"] = bars["Close"].shift(1)
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars[["Volume", "Trades", "BidVolume", "AskVolume", "Delta"]] = bars[
        ["Volume", "Trades", "BidVolume", "AskVolume", "Delta"]
    ].fillna(0)

    bars = bars.loc[(bars.index >= start) & (bars.index <= end)]
    bars.index.name = "Time_PT"
    bars.to_csv(out_path)
    print(f"Wrote {len(bars)} rows to {out_path}")
    print(bars.head())
    print(bars.tail())
    return bars


if __name__ == "__main__":
    start_pt, end_pt, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    export(start_pt, end_pt, out_path)
