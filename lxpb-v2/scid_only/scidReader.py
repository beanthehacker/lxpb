"""Read a Sierra Chart intraday `.scid` file into a DataFrame -- the cloud fallback for `scidReader`.

The repo's reader lives in the user's private `acheron` repo (`AcheronUtils/scidReader.py`) and is
imported as `from scidReader import get_scid_df`. A cloud session started without that repo has no
`scidReader` to import, so this module provides the same function, written from Sierra Chart's
published `.scid` layout, and gives the same frame (checked against the acheron reader on whole
files, 2026-10-15). When `/home/user/acheron` is checked out, that copy is used instead
(`scid_only/run_scid_only.py`, `.claude/hooks/session-start.sh`).

Layout: a 56-byte header, then fixed 40-byte records:
    int64   DateTime   microseconds since 1899-12-30 00:00 UTC
    float32 Open, High, Low, Close
    uint32  NumTrades, TotalVolume, BidVolume, AskVolume
(read as signed int32, as the acheron reader does). The frame has columns Open, High, Low, Close,
Trades, Volume, BidVolume, AskVolume, indexed by `Time` in America/Chicago; rows with a NaN field
or a time outside (0, 2705466561000000] after the epoch shift are dropped.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HEADER_BYTES = 56
RECORD_BYTES = 40
EPOCH_SHIFT_US = 2209161600000000     # 1899-12-30 -> 1970-01-01, microseconds
TIME_MAX_US = 2705466561000000
RECORD = np.dtype([
    ("Time", "<u8"), ("Open", "<f4"), ("High", "<f4"), ("Low", "<f4"), ("Close", "<f4"),
    ("Trades", "<i4"), ("Volume", "<i4"), ("BidVolume", "<i4"), ("AskVolume", "<i4"),
])


def get_scid_df(filename, limitsize=sys.maxsize):
    """Every record of `filename` (or only the last `limitsize` bytes' worth of whole records) as a
    DataFrame indexed by time (America/Chicago)."""
    path = Path(filename)
    assert path.exists(), "file not found"
    size = path.stat().st_size
    offset = HEADER_BYTES if size < limitsize else size - (limitsize // RECORD_BYTES) * RECORD_BYTES
    df = pd.DataFrame(data=np.memmap(path, dtype=RECORD, offset=offset, mode="r"), copy=False)
    df.dropna(inplace=True)
    df["Time"] = df["Time"] - EPOCH_SHIFT_US
    df.drop(df[(df.Time < 1) | (df.Time > TIME_MAX_US)].index, inplace=True)
    df.set_index("Time", inplace=True)
    df.index = pd.to_datetime(df.index, unit="us").tz_localize(tz="utc").tz_convert("America/Chicago")
    return df
