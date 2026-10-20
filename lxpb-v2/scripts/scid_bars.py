"""Sierra Chart .scid ticks -> PT OHLC bars, for one-off checks.

Reads through AcheronUtils' `scidReader.get_scid_df` (path from
$ACHERON_UTILS, default E:\\acheron\\AcheronUtils). Raw per-contract prices, no
continuous offset; see "Data convention" in ../CLAUDE.md.

    python scid_bars.py <file.scid> <start PT> <end PT> [rule] [tail_mb] > out.csv
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.environ.get("ACHERON_UTILS", r"E:\acheron\AcheronUtils"))
from scidReader import get_scid_df  # noqa: E402

PT = "America/Los_Angeles"


def bars(path, start, end, rule="5min", tail_mb=400, tz=PT):
    """Time bars in `tz`, labelled by their start; only bars that traded.

    Only the last `tail_mb` MB of the file are read; raises if that does not
    reach back to `start`. Each record's trade price is its Close (tick
    records carry Open 0 and bid/ask in Low/High).
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    start = start.tz_localize(tz) if start.tzinfo is None else start
    end = end.tz_localize(tz) if end.tzinfo is None else end
    t = get_scid_df(path, limitsize=int(tail_mb * 2**20))
    t.index = t.index.tz_convert(tz)
    if t.index[0] > start:
        raise ValueError(f"last {tail_mb} MB start at {t.index[0]}, after {start}; raise tail_mb")
    px = t.loc[(t.index >= start) & (t.index < end), ["Close", "Volume"]]
    g = px.resample(rule, label="left", closed="left")
    out = g["Close"].ohlc()
    out.columns = ["Open", "High", "Low", "Close"]
    out["Volume"] = g["Volume"].sum()
    return out.dropna()


if __name__ == "__main__":
    path, start, end = sys.argv[1:4]
    rule = sys.argv[4] if len(sys.argv) > 4 else "5min"
    tail = float(sys.argv[5]) if len(sys.argv) > 5 else 400
    bars(path, start, end, rule, tail).to_csv(sys.stdout, lineterminator="\n")
