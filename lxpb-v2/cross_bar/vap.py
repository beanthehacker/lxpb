"""Volume at price for the cross bar trade charts' volume profiles, from the Sierra Chart .scid ticks.

Every trade's price and size (the .scid record's Close and Volume) summed per (time bucket, price tick), on the continuous
scale: each contract's ticks while it was front month (E:\\data_source es/rolls.csv), raised by that contract's offset, so
the prices match load_es and the charts. A bucket holds the trades with bucket start <= trade time < bucket end (a trade at
09:30:00.700 is in the 09:30 minute), the same stamping as the 1-minute bars; per-minute totals equal load_es("1m")'s
volume (checked by `check`).

Read straight from the memory-mapped .scid (fixed 40-byte records in time order): a window is found by binary search on the
record times, never np.searchsorted on the mapped column (that copies the whole column).
"""
import os

import numpy as np
import pandas as pd

TICK = 0.25
SCID_DIR = os.environ.get("ES_SCID_DIR", r"D:\SierraChart-daytrading\Data")
ROLLS = os.environ.get("ES_ROLLS", r"E:\data_source\es\rolls.csv")
_HEADER = 56
_SHIFT_US = 2209161600000000                # Sierra epoch (1899-12-30) -> unix, microseconds
_REC = np.dtype([("Time", "<u8"), ("Open", "<f4"), ("High", "<f4"), ("Low", "<f4"), ("Close", "<f4"),
                 ("Trades", "<i4"), ("Volume", "<i4"), ("BidVolume", "<i4"), ("AskVolume", "<i4")])
_MM = {}


def _rolls():
    r = pd.read_csv(ROLLS)
    r["a"] = pd.to_datetime(r["front_start_utc"]).dt.tz_localize("UTC")
    r["b"] = pd.to_datetime(r["front_end_utc"]).dt.tz_localize("UTC")
    return r


def _mm(sym):
    if sym not in _MM:
        _MM[sym] = np.memmap(os.path.join(SCID_DIR, f"F.US.{sym}.scid"), dtype=_REC, offset=_HEADER, mode="r")
    return _MM[sym]


def _first_at_or_after(tm, raw_us):
    lo, hi = 0, len(tm)
    while lo < hi:
        mid = (lo + hi) // 2
        if int(tm[mid]) < raw_us:
            lo = mid + 1
        else:
            hi = mid
    return lo


def minutes(start, end):
    """Volume at price per minute for start <= t < end: DataFrame [t (minute start, unix s), tick (adjusted price / 0.25),
    vol, ask (volume lifting the ask)], sorted by t then tick."""
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    out = []
    for r in _rolls().itertuples():
        a = max(start, r.a)
        b = min(end, r.b) if pd.notna(r.b) else end
        if a >= b:
            continue
        mm = _mm(r.contract)
        tm = mm["Time"]
        i = _first_at_or_after(tm, a.value // 1000 + _SHIFT_US)
        j = _first_at_or_after(tm, b.value // 1000 + _SHIFT_US)
        if j <= i:
            continue
        rec = np.asarray(mm[i:j])
        rec = rec[~np.isnan(rec["Close"]) & (rec["Volume"] > 0)]
        t = (rec["Time"].astype(np.int64) - _SHIFT_US) // 60_000_000 * 60
        tk = np.rint(rec["Close"].astype(np.float64) / TICK).astype(np.int64) + int(round(r.offset / TICK))
        t0, k0 = int(t.min()), int(tk.min())
        key = (t - t0) // 60 * 65536 + (tk - k0)
        u, inv = np.unique(key, return_inverse=True)
        out.append(pd.DataFrame({"t": t0 + (u // 65536) * 60, "tick": k0 + u % 65536,
                                 "vol": np.bincount(inv, weights=rec["Volume"]).astype(np.int64),
                                 "ask": np.bincount(inv, weights=rec["AskVolume"]).astype(np.int64)}))
    if not out:
        return pd.DataFrame(columns=["t", "tick", "vol", "ask"])
    return pd.concat(out, ignore_index=True).sort_values(["t", "tick"], kind="stable").reset_index(drop=True)


_DAY_MEMO = {}


def buckets(start, end, sec=60):
    """`minutes` in buckets of `sec` seconds, read one UTC day at a time (a long range never holds all its ticks at once);
    whole days are memoised in this process, so rows sharing days read them once."""
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    out, d = [], start.normalize()
    while d < end:
        e = d + pd.Timedelta(days=1)
        k = (d.value, sec)
        if k not in _DAY_MEMO:
            v = minutes(d, e)
            _DAY_MEMO[k] = v if sec == 60 else coarsen(v, sec)
        v = _DAY_MEMO[k]
        out.append(v[(v["t"] >= start.value // 10**9) & (v["t"] < end.value // 10**9)])   # range ends on bucket edges
        d = e
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["t", "tick", "vol", "ask"])


def coarsen(v, sec):
    """The same volume at price in buckets of `sec` seconds (sec a multiple of 60, buckets on the epoch grid)."""
    g = v.assign(t=v["t"] // sec * sec).groupby(["t", "tick"], sort=True)[["vol", "ask"]].sum().reset_index()
    return g


def pack(v, dur):
    """Buckets for the page: flat [start step (minutes since the previous bucket), low tick step (since the previous bucket's
    low), row count n, then n volumes from the low tick up, one per tick (0 where nothing traded)]. `dur` = bucket seconds."""
    rows, pt, pl = [], None, None
    v = v.astype({"t": np.int64, "tick": np.int64, "vol": np.int64})
    if len(v):
        t = v["t"].to_numpy()
        cut = np.flatnonzero(np.diff(t)) + 1
        for g_t, g_k, g_v in zip(np.split(t, cut), np.split(v["tick"].to_numpy(), cut), np.split(v["vol"].to_numpy(), cut)):
            lo, n = int(g_k[0]), int(g_k[-1] - g_k[0] + 1)
            dense = np.zeros(n, dtype=np.int64)
            dense[g_k - lo] = g_v
            tt = int(g_t[0])
            rows += [0 if pt is None else (tt - pt) // 60, 0 if pl is None else lo - pl, n] + dense.tolist()
            if pt is None:
                t0, b0 = tt, lo
            pt, pl = tt, lo
        return {"t0": t0, "b": b0, "dur": dur, "r": rows}
    return {"t0": 0, "b": 0, "dur": dur, "r": []}


def check(start, end):
    """Per-minute volume equals load_es('1m') (same trades, same minutes)."""
    from es_data import load_es
    v = minutes(start, end)
    m1 = load_es("1m", start=start, end=end)
    a = v.groupby("t")["vol"].sum()
    b = pd.Series(m1["volume"].to_numpy(np.int64), index=m1.index.asi8 // 1_000_000_000)
    b = b[(b.index >= a.index.min()) & (b.index <= a.index.max())]
    same = a.reindex(b.index).fillna(-1).astype(np.int64).eq(b).mean()
    hi = v.groupby("t")["tick"].max() * TICK
    lo = v.groupby("t")["tick"].min() * TICK
    px = (hi.reindex(b.index).to_numpy() == m1["high"].to_numpy()[-len(b):]).mean() if len(b) else float("nan")
    return dict(minutes=len(b), vol_equal=float(same), high_equal=float(px), lo_rows=len(lo))
