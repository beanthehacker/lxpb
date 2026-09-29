"""Export one ES replay window from the raw .scid tick file for the ladder game.

    python ladder/build_ladder_data.py --date 2026-09-28 --start 09:20 --end 10:10

Writes public/ladder/es-<date>.js, which sets `window.LADDER_DATA`, so the page
works both when served (/ladder) and when opened straight from disk.

Each Sierra tick record carries the trade price (Close), the best ask (High) and
best bid (Low) at that moment, and the aggressor side (AskVolume > 0 = buyer
lifted the offer). Ticks are merged when they share millisecond, price, side,
bid and ask, then delta-encoded as a flat integer array, 5 ints per event:

    dt_ms, d_price_ticks, volume, side (+1 buy / -1 sell), bid_ask_code

bid_ask_code = (price - bid)/tick * 16 + (ask - price)/tick, both clamped to 0..15.

Prices are the raw front-month contract (ESZ26 today). It is the live segment
of TradingView's ES1! continuous series, so no roll offset applies.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

SCID_DIR = r"D:\SC\Data"
HEADER = 56
SHIFT_US = 2209161600000000          # Sierra epoch (1899-12-30) -> unix, microseconds
TICK = 0.25
REC = np.dtype([
    ("Time", "<u8"), ("Open", "<f4"), ("High", "<f4"), ("Low", "<f4"),
    ("Close", "<f4"), ("Trades", "<i4"), ("Volume", "<i4"),
    ("BidVolume", "<i4"), ("AskVolume", "<i4"),
])
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    ap.add_argument("--start", required=True, help="PT clock time, HH:MM")
    ap.add_argument("--end", required=True, help="PT clock time, HH:MM (exclusive)")
    ap.add_argument("--symbol", default="EPZ26")
    ap.add_argument("--output", default=None)
    a = ap.parse_args()

    tz = "America/Los_Angeles"
    lo = pd.Timestamp(f"{a.date} {a.start}", tz=tz)
    hi = pd.Timestamp(f"{a.date} {a.end}", tz=tz)
    mm = np.memmap(os.path.join(SCID_DIR, f"F.US.{a.symbol}.scid"), dtype=REC, offset=HEADER, mode="r")
    t = mm["Time"]
    i0 = np.searchsorted(t, lo.value // 1000 + SHIFT_US)
    i1 = np.searchsorted(t, hi.value // 1000 + SHIFT_US)
    r = np.asarray(mm[i0:i1])
    r = r[~np.isnan(r["Close"]) & (r["Volume"] > 0)]
    if not len(r):
        raise SystemExit("no ticks in window")

    ms = (r["Time"].astype(np.int64) - SHIFT_US) // 1000
    px = np.rint(r["Close"] / TICK).astype(np.int64)
    ask = np.rint(r["High"] / TICK).astype(np.int64)
    bid = np.rint(r["Low"] / TICK).astype(np.int64)
    side = np.where(r["AskVolume"] > 0, 1, -1)
    code = np.clip(px - bid, 0, 15) * 16 + np.clip(ask - px, 0, 15)

    df = pd.DataFrame({"ms": ms, "px": px, "side": side, "code": code, "vol": r["Volume"].astype(np.int64)})
    # Merge only consecutive runs, so trade order within a millisecond is kept.
    key = df[["ms", "px", "side", "code"]]
    run = (key != key.shift()).any(axis=1).cumsum()
    g = df.groupby(run, sort=False).agg(ms=("ms", "first"), px=("px", "first"), side=("side", "first"),
                                        code=("code", "first"), vol=("vol", "sum"))

    dt = np.diff(g["ms"].to_numpy(), prepend=lo.value // 1_000_000)
    dp = np.diff(g["px"].to_numpy(), prepend=g["px"].iloc[0])
    flat = np.column_stack([dt, dp, g["vol"], g["side"], g["code"]]).ravel().tolist()

    out = {
        "symbol": "ES", "contract": a.symbol, "tick": TICK, "pointValue": 50,
        "date": a.date, "tz": "PT",
        "startMs": int(lo.value // 1_000_000), "endMs": int(hi.value // 1_000_000),
        "firstPrice": float(g["px"].iloc[0] * TICK),
        "lo": float(g["px"].min() * TICK), "hi": float(g["px"].max() * TICK),
        "totalVolume": int(g["vol"].sum()), "events": len(g),
        "stride": 5, "data": flat,
    }
    path = a.output or os.path.join(HERE, "..", "public", "ladder", f"es-{a.date}.js")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("window.LADDER_DATA=")
        json.dump(out, f, separators=(",", ":"))
        f.write(";\n")
    print(f"{len(r)} ticks -> {len(g)} events, {out['lo']}-{out['hi']}, vol {out['totalVolume']}, "
          f"{os.path.getsize(path) / 1e6:.2f} MB -> {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
