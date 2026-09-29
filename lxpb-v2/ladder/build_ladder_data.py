"""Export one ES replay window for the ladder game: real ticks plus the LXPB
levels live during it.

    python ladder/build_ladder_data.py --date 2026-09-28 --start 09:20 --end 10:10

Writes public/ladder/es-<date>.js, which sets `window.LADDER_DATA`, so the page
works both when served (/ladder) and when opened straight from disk.

Ticks
-----
Each Sierra tick record carries the trade price (Close), the best ask (High) and
best bid (Low) at that moment, and the aggressor side (AskVolume > 0 = buyer
lifted the offer). Prices are mapped onto the continuous (TradingView ES1!) scale
by the contract's measured offset, the same scale the level ledgers use. Ticks are
merged when they share millisecond, price, side, bid and ask, then delta-encoded
as a flat integer array, 5 ints per event:

    dt_ms, d_price_ticks, volume, side (+1 buy / -1 sell), bid_ask_code

bid_ask_code = (price - bid)/tick * 16 + (ask - price)/tick, both clamped to 0..15.

Levels
------
Every H1 and M5 level from the level ledgers (full ledger: every P0 tracked to
its retest) that is known before the window ends, still alive when it starts and
within reach of the window's prices. Times are when the replay may SHOW each
event, never earlier:

    knownMs  the formation bar's CLOSE (the ledger stamps the bar's start, but the
             bar's high/low is only final at its close)
    breakMs  the breakout bar's close (a breakout is a close through the level)
    deathMs  retest / early touch: the first tick inside the ledger's death bar
             that trades at or through the level from the retest side (LHPB from
             above, LLPB from below -- lxpb.py's touched-or-gapped-over rule);
             a no-close discard: that bar's close
    untrackedDeathMs  a plain-P0's end in the standard population, where
             plain-P0s stop at their own breakout (breakout bar's close)

The page shows the standard population by default and the tracked one on a toggle.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import render_labels_report as R  # noqa: E402
import lxpb_levels_cache as LC  # noqa: E402

TICK = 0.25
LEVEL_MARGIN_PTS = 15            # levels this far beyond the window's range still show
BAR = {"H1": pd.Timedelta(hours=1), "M5": pd.Timedelta(minutes=5)}


def _ticks(lo, hi):
    """Continuous-scale tick records over [lo, hi), each contract segment from
    its own front month at its own measured offset."""
    rolls = [r for r in R._own_roll() if r is not None and lo < r < hi]
    parts = []
    for a, b in zip([lo] + rolls, rolls + [hi]):
        offset, sym = R._offset_for_ts(a)
        w = R._scid_window(sym, a, b)
        if w is None:
            w = R._slice_sorted(R._load_contract(sym), a, b)
        w = w[(w["Volume"] > 0)].copy()
        for c in ("Close", "High", "Low"):
            w[c] = w[c].astype("float64") + offset
        parts.append(w)
    return pd.concat(parts)


def _ms(ts):
    return None if pd.isna(ts) else int(pd.Timestamp(ts).value // 1_000_000)


def _touch_ms(lv, bar):
    """First tick in the level's death bar that trades at/through it from the
    retest side, or None if no tick does (bar data and ticks disagree)."""
    a = lv["death_time"]
    w = _ticks(a, a + bar)
    px = w["Close"].to_numpy()
    hit = px <= lv["price"] if lv["type"] == "LHPB" else px >= lv["price"]
    if not hit.any():
        return None
    return _ms(w.index[int(np.argmax(hit))])


def _levels(lo, hi, px_lo, px_hi):
    out, fallbacks = [], 0
    for tf, fn in (("H1", LC.h1_levels), ("M5", LC.m5_levels)):
        bar = BAR[tf]
        lv = fn(plain_p0=LC.PLAIN_P0_TRACKED, verbose=False)
        known = lv["formation_time"] + bar
        alive = lv["death_time"].isna() | (lv["death_time"] + bar > lo)
        near = lv["price"].between(px_lo - LEVEL_MARGIN_PTS, px_hi + LEVEL_MARGIN_PTS)
        for _i, r in lv[(known < hi) & alive & near].iterrows():
            fate = r["fate"]
            death_ms, death_kind = None, None
            if fate in (LC.FATE_RETESTED, LC.FATE_CONSUMED_EARLY):
                death_kind = "retest" if fate == LC.FATE_RETESTED else "early"
                death_ms = _touch_ms(r, bar)
                if death_ms is None:
                    fallbacks += 1
                    death_ms = _ms(r["death_time"] + bar)
            elif fate == LC.FATE_DISCARDED_NO_CLOSE:
                death_kind, death_ms = "noclose", _ms(r["death_time"] + bar)
            if death_ms is not None and death_ms < _ms(lo):
                continue                                    # died before the replay starts
            brk = r["breakout_time"]
            plain = r["p0_kind"] == "plain-P0"
            out.append({
                "tf": tf, "type": r["type"], "price": round(float(r["price"]), 2),
                "kind": r["p0_kind"], "plain": bool(plain),
                "formedPT": pd.Timestamp(r["formation_time"]).tz_convert("America/Los_Angeles").strftime("%m-%d %H:%M"),
                "knownMs": _ms(r["formation_time"] + bar),
                "breakMs": None if pd.isna(brk) else _ms(brk + bar),
                "deathMs": death_ms, "deathKind": death_kind,
                "untrackedDeathMs": _ms(brk + bar) if plain else None,
            })
    return out, fallbacks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="session date, YYYY-MM-DD")
    ap.add_argument("--start", required=True, help="PT clock time, HH:MM")
    ap.add_argument("--end", required=True, help="PT clock time, HH:MM (exclusive)")
    ap.add_argument("--output", default=None)
    a = ap.parse_args()

    tz = "America/Los_Angeles"
    lo = pd.Timestamp(f"{a.date} {a.start}", tz=tz).tz_convert("UTC")
    hi = pd.Timestamp(f"{a.date} {a.end}", tz=tz).tz_convert("UTC")
    offset, sym = R._offset_for_ts(lo)
    w = _ticks(lo, hi)
    if not len(w):
        raise SystemExit("no ticks in window")

    ms = ((w.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
    px = np.rint(w["Close"].to_numpy() / TICK).astype(np.int64)
    ask = np.rint(w["High"].to_numpy() / TICK).astype(np.int64)
    bid = np.rint(w["Low"].to_numpy() / TICK).astype(np.int64)
    side = np.where(w["AskVolume"].to_numpy() > 0, 1, -1)
    code = np.clip(px - bid, 0, 15) * 16 + np.clip(ask - px, 0, 15)

    df = pd.DataFrame({"ms": ms.astype(np.int64), "px": px, "side": side,
                       "code": code, "vol": w["Volume"].to_numpy().astype(np.int64)})
    # Merge only consecutive runs, so trade order within a millisecond is kept.
    key = df[["ms", "px", "side", "code"]]
    run = (key != key.shift()).any(axis=1).cumsum()
    g = df.groupby(run, sort=False).agg(ms=("ms", "first"), px=("px", "first"), side=("side", "first"),
                                        code=("code", "first"), vol=("vol", "sum"))

    dt = np.diff(g["ms"].to_numpy(), prepend=lo.value // 1_000_000)
    dp = np.diff(g["px"].to_numpy(), prepend=g["px"].iloc[0])
    flat = np.column_stack([dt, dp, g["vol"], g["side"], g["code"]]).ravel().tolist()

    px_lo, px_hi = float(g["px"].min() * TICK), float(g["px"].max() * TICK)
    levels, fallbacks = _levels(lo, hi, px_lo, px_hi)

    out = {
        "symbol": "ES", "contract": sym, "offset": offset, "tick": TICK, "pointValue": 50,
        "date": a.date, "tz": "PT",
        "startMs": int(lo.value // 1_000_000), "endMs": int(hi.value // 1_000_000),
        "firstPrice": float(g["px"].iloc[0] * TICK),
        "lo": px_lo, "hi": px_hi,
        "totalVolume": int(g["vol"].sum()), "events": len(g),
        "stride": 5, "data": flat,
        "levels": levels,
    }
    path = a.output or os.path.join(HERE, "..", "public", "ladder", f"es-{a.date}.js")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("window.LADDER_DATA=")
        json.dump(out, f, separators=(",", ":"))
        f.write(";\n")
    in_win = [l for l in levels if l["deathMs"] and l["deathMs"] < out["endMs"]]
    print(f"{len(w)} ticks -> {len(g)} events, {px_lo}-{px_hi}, vol {out['totalVolume']}, "
          f"{sym} {offset:+.2f}pt; {len(levels)} levels ({sum(l['tf'] == 'H1' for l in levels)} H1), "
          f"{sum(l['deathKind'] == 'retest' for l in in_win)} retested in window"
          f"{f', {fallbacks} touch(es) not found in ticks (bar close used)' if fallbacks else ''}; "
          f"{os.path.getsize(path) / 1e6:.2f} MB -> {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
