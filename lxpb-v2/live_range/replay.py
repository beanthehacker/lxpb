"""Replay .scid ticks through the live engine, as if streaming.

  python scid_only/run_scid_only.py live_range/replay.py --from 2026-09-28 --to 2026-10-12 \\
      --warmup-days 4 --out /tmp/live_range_out

Everything before --from only warms the books up (nothing is armed). Output: events.jsonl (every
order armed / amended / cancelled, fill, exit, episode and level event), trades.csv, and a summary.
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import render_labels_report as R
from live_range.engine import Engine, Params, PT


def ticks(lo, hi):
    """(times_ns int64 array, prices float64 array) over [lo, hi) on the continuous scale, a tick kept
    only when its price differs from the last kept one or it opens a new minute (so no bar close
    is ever late)."""
    rolls = [r for r in R._own_roll() if r is not None and lo < r < hi]
    edges = [lo] + rolls + [hi]
    T, P = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        offset, sym = R._offset_for_ts(a)
        w = R._scid_window(sym, a, b)
        if w is None or w.empty:
            continue
        T.append(w.index.asi8)
        P.append(np.round(w["Close"].to_numpy(np.float64) + offset, 4))
    if not T:
        return np.empty(0, np.int64), np.empty(0)
    t, p = np.concatenate(T), np.concatenate(P)
    slot = t // 60_000_000_000
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = (p[1:] != p[:-1]) | (slot[1:] != slot[:-1])
    return t[keep], p[keep]


def verdicts(lo, hi, freq):
    """{bar time ns: (hammer, shooting_star)} for the tick bars of [lo, hi), judged by patterns-pure over
    the whole frame at once (a bar's verdict only reads that bar and the one before it)."""
    sys.path.insert(0, os.path.join(os.path.dirname(R.__file__), ".."))
    import lxpb
    bars = R._tick_bars_spanning(lo, hi, freq)[["open", "high", "low", "close"]]
    ham = bars.index.isin(lxpb.find_hammer(bars, atr=0.0).index)
    star = bars.index.isin(lxpb.find_shooting_star(bars, atr=0.0).index)
    return {t: (bool(a), bool(b)) for t, a, b in zip(bars.index.asi8.tolist(), ham, star)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", default="2026-09-28")
    ap.add_argument("--to", dest="hi", default=None)
    ap.add_argument("--warmup-days", type=float, default=4.0)
    ap.add_argument("--bar-minutes", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk-hours", type=float, default=24.0)
    a = ap.parse_args()
    lo = pd.Timestamp(a.lo, tz="UTC")
    hi = pd.Timestamp(a.hi, tz="UTC") if a.hi else pd.Timestamp(R._scid_bounds("EPZ26")[1]).floor("1min")
    start = lo - pd.Timedelta(days=a.warmup_days)
    t0 = time.time()
    v5 = verdicts(start, hi, "5min")
    vtb = verdicts(start, hi, f"{a.bar_minutes}min")
    print(f"verdicts for {len(v5):,} M5 and {len(vtb):,} M{a.bar_minutes} bars in {time.time() - t0:.1f}s")
    eng = Engine(Params(bar_minutes=a.bar_minutes), arm_from_ns=lo.value, verdicts5=v5, verdicts_tb=vtb)
    print(f"replay {start} -> {hi} (armed from {lo}), M{a.bar_minutes} trading bars")
    t0, n_ticks = time.time(), 0
    cur = start
    step = pd.Timedelta(hours=a.chunk_hours)
    while cur < hi:
        nxt = min(cur + step, hi)
        T, P = ticks(cur, nxt)
        on_tick = eng.on_tick
        for t, px in zip(T.tolist(), P.tolist()):
            on_tick(t, px)
        n_ticks += len(T)
        print(f"  {cur:%m-%d %H:%M} +{len(T):>7,} ticks  trades {len(eng.trades):>3}  working orders {len(eng.orders):>2}  "
              f"{time.time() - t0:6.1f}s", flush=True)
        cur = nxt
    print(f"{n_ticks:,} ticks in {time.time() - t0:.1f}s")
    report(eng, a.out)


def _fmt(ns):
    return pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(PT).strftime("%m-%d %H:%M:%S")


def report(eng, out):
    rows = []
    for tr in eng.trades:
        ex = tr["exit"]
        rows.append({
            "id": tr["id"], "entry_time_pt": _fmt(tr["t"]), "side": tr["side"], "level_type": tr["type"],
            "playbooks": "+".join(sorted({pb for _, _, pb in tr["serves"]})),
            "boxes": ",".join(f"{b}{'^' if d == 'up' else 'v'}" for b, d in tr["boxes"]),
            "entry": tr["price"], "stop": tr["stop"], "target": tr["target"], "stop_src": tr["stop_src"],
            "r_planned": round(tr["r_planned"], 2),
            "exit_time_pt": _fmt(ex["t"]) if ex else "", "exit": ex["price"] if ex else "", "why": ex["why"] if ex else "open",
            "pnl_pts": round(tr.get("pnl", float("nan")), 2) if ex else "", "r": round(tr.get("r", float("nan")), 2) if ex else "",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        print("no trades")
    else:
        print(df.to_string(index=False))
        done = df[df["why"] != "open"]
        if len(done):
            r = done["r"].astype(float)
            print(f"\n{len(done)} closed trades  win rate {(r > 0).mean():.0%}  avg R {r.mean():.2f}  total R {r.sum():.1f}")
            for pb, g in done.groupby("playbooks"):
                print(f"  {pb:<18} n={len(g):>3}  win {(g['r'].astype(float) > 0).mean():.0%}  avg R {g['r'].astype(float).mean():.2f}")
    kinds = {}
    for ev in eng.events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    print("events:", dict(sorted(kinds.items())))
    why = {}
    for ev in eng.events:
        if ev["kind"] == "order_cancelled":
            why[ev["why"]] = why.get(ev["why"], 0) + 1
    print("order cancels:", dict(sorted(why.items(), key=lambda kv: -kv[1])))
    if out:
        os.makedirs(out, exist_ok=True)
        df.to_csv(os.path.join(out, "trades.csv"), index=False)
        with open(os.path.join(out, "events.jsonl"), "w") as f:
            for ev in eng.events:
                f.write(json.dumps(ev, default=str) + "\n")
        print("wrote", out)


if __name__ == "__main__":
    main()
