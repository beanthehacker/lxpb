"""Replay .scid ticks through the live engine, as if streaming.

  python scid_only/run_scid_only.py live_range/replay.py --from 2026-09-28 --to 2026-10-12 \\
      --warmup-days 4 --out /tmp/live_range_out

Everything before --from only warms the books up (nothing is armed). Output: events.jsonl (every
order armed / amended / cancelled, fill, exit, episode and level event), trades.csv, and a summary.

M1 fade playbook only: go_with rows (the `playbooks` column) are not part of the fade playbook or its report; leave them
out of any fade count or total (see live_range/engine.py).
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
    """(times_ns, prices, bids, asks, sides) over [lo, hi) on the continuous scale. `sides` is +1 when a
    buyer lifted the offer, -1 when a seller hit the bid. A record is kept only when its price, quotes or
    side differ from the last kept one, or it opens a new minute (so no bar close is ever late)."""
    rolls = [r for r in R._own_roll() if r is not None and lo < r < hi]
    edges = [lo] + rolls + [hi]
    T, P, B, A, S = [], [], [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        offset, sym = R._offset_for_ts(a)
        w = R._scid_window(sym, a, b)
        if w is None or w.empty:
            continue
        T.append(w.index.asi8)
        P.append(np.round(w["Close"].to_numpy(np.float64) + offset, 4))
        B.append(np.round(w["Low"].to_numpy(np.float64) + offset, 4))
        A.append(np.round(w["High"].to_numpy(np.float64) + offset, 4))
        S.append(np.where(w["AskVolume"].to_numpy() > 0, 1, np.where(w["BidVolume"].to_numpy() > 0, -1, 0)).astype(np.int8))
    if not T:
        e = np.empty(0)
        return np.empty(0, np.int64), e, e, e, np.empty(0, np.int8)
    t, p, bd, ak, sd = (np.concatenate(x) for x in (T, P, B, A, S))
    slot = t // 60_000_000_000
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = ((p[1:] != p[:-1]) | (bd[1:] != bd[:-1]) | (ak[1:] != ak[:-1]) | (sd[1:] != sd[:-1])
                | (slot[1:] != slot[:-1]))
    return t[keep], p[keep], bd[keep], ak[keep], sd[keep]


def verdicts(lo, hi, freq):
    """{bar time ns: (hammer, shooting_star)} for the tick bars of [lo, hi), judged by patterns-pure over
    the whole frame at once (a bar's verdict only reads that bar and the one before it)."""
    sys.path.insert(0, os.path.join(os.path.dirname(R.__file__), ".."))
    import lxpb
    bars = R._tick_bars_spanning(lo, hi, freq)[["open", "high", "low", "close"]]
    ham = bars.index.isin(lxpb.find_hammer(bars, atr=0.0).index)
    star = bars.index.isin(lxpb.find_shooting_star(bars, atr=0.0).index)
    return {t: (bool(a), bool(b)) for t, a, b in zip(bars.index.asi8.tolist(), ham, star)}


def build_engine(lo, hi, warmup_days, params, h1_days=25.0, engine_cls=Engine):
    """An engine for the stream [lo - warmup_days, hi) armed from `lo`, with `h1_days` of closed H1 and M5 bars from BEFORE
    the stream seeded for the H1 range boxes behind the range merge (the stream itself builds H1 from then on)."""
    start = lo - pd.Timedelta(days=warmup_days)
    v5 = verdicts(start, hi, "5min")
    vtb = verdicts(start, hi, f"{params.bar_minutes}min")
    eng = engine_cls(params, arm_from_ns=lo.value, verdicts5=v5, verdicts_tb=vtb)
    if params.box_merge_overlap > 0 and h1_days > 0:
        s0 = start.floor("1h")
        cols = ["open", "high", "low", "close"]
        eng.seed_h1(R._tick_bars_spanning(s0 - pd.Timedelta(days=h1_days), s0, "60min")[cols],
                    R._tick_bars_spanning(s0 - pd.Timedelta(days=h1_days), s0, "5min")[cols])
    return eng, start


def feed(eng, start, hi, chunk_hours=24.0, progress=False):
    """Stream the ticks of [start, hi) through the engine."""
    t0, n_ticks, cur = time.time(), 0, start
    step = pd.Timedelta(hours=chunk_hours)
    while cur < hi:
        nxt = min(cur + step, hi)
        T, P, B, A, S = ticks(cur, nxt)
        on_tick = eng.on_tick
        for t, px, bd, ak, sd in zip(T.tolist(), P.tolist(), B.tolist(), A.tolist(), S.tolist()):
            on_tick(t, px, bd, ak, sd)
        n_ticks += len(T)
        if progress:
            print(f"  {cur:%m-%d %H:%M} +{len(T):>7,} ticks  trades {len(eng.trades):>3}  working orders {len(eng.orders):>2}  "
                  f"{time.time() - t0:6.1f}s", flush=True)
        cur = nxt
    return n_ticks, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", default="2026-09-28")
    ap.add_argument("--to", dest="hi", default=None)
    ap.add_argument("--warmup-days", type=float, default=4.0)
    ap.add_argument("--bar-minutes", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--chunk-hours", type=float, default=24.0)
    ap.add_argument("--no-peg", action="store_true", help="plain limit orders (no chasing)")
    ap.add_argument("--no-manage", action="store_true", help="no stop trail / RR-floor exit")
    ap.add_argument("--min-plain-wick", type=float, default=1.0, help="plain-P0 minimum rejection wick, points (0 = off)")
    ap.add_argument("--box-lookback-days", type=float, default=0.0,
                    help="range boxes older than this are not seen (the report's detector gets 5 days of M5 history); 0 = no expiry")
    ap.add_argument("--bo-range-atr", type=float, default=0.0,
                    help="the reports' strong-breakout gate on every level's own P1 (1.5 in the reports; 0 = off)")
    ap.add_argument("--decision-s", type=float, default=1.0,
                    help="seconds between the algo's decisions (production gets data once per second); 0 = every tape record")
    ap.add_argument("--seat-rank", default="spike_first", choices=["spike_first", "nearest"])
    ap.add_argument("--box-merge-overlap", type=float, default=0.5,
                    help="M5 boxes overlapping by >= this share of the smaller box inside one known open H1 range box share "
                         "their seats (point in time); 0 = off")
    ap.add_argument("--box-merge-tol", type=float, default=2.0, help="points an M5 box edge may poke out of the H1 box")
    ap.add_argument("--h1-days", type=float, default=25.0, help="days of H1 / M5 history before the stream seeded for the H1 boxes")
    ap.add_argument("--fade2", action="store_true",
                    help="also trade fade 2 (a box's second-side breakout); OFF by default, only fade 1 is traded")
    ap.add_argument("--fade-target", default="range_edge", choices=["range_edge", "zigzag"],
                    help="fade target: the faded range's edge (default) or the most recent zigzag pivot since P1")
    a = ap.parse_args()
    lo = pd.Timestamp(a.lo, tz="UTC")
    hi = pd.Timestamp(a.hi, tz="UTC") if a.hi else pd.Timestamp(R._scid_bounds("EPZ26")[1]).floor("1min")
    t0 = time.time()
    eng, start = build_engine(lo, hi, a.warmup_days, Params(
        bar_minutes=a.bar_minutes, pegged=not a.no_peg, manage=not a.no_manage, seat_rank=a.seat_rank,
        min_plain_wick=a.min_plain_wick, box_lookback_days=a.box_lookback_days, bo_range_atr=a.bo_range_atr,
        decision_s=a.decision_s, box_merge_overlap=a.box_merge_overlap, box_merge_tol=a.box_merge_tol, fade2=a.fade2, target_mode=a.fade_target),
        h1_days=a.h1_days)
    print(f"engine ready in {time.time() - t0:.1f}s; replay {start} -> {hi} (armed from {lo}), M{a.bar_minutes} trading bars")
    n_ticks, secs = feed(eng, start, hi, a.chunk_hours, progress=True)
    print(f"{n_ticks:,} ticks in {secs:.1f}s")
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
            "r_planned": round(tr["r_planned"], 2), "level": tr["level_price"],
            "chased": round(abs(tr["price"] - tr["level_price"]), 2), "trails": tr["trails"],
            "decided": tr["decided"]["price"], "tags": " ".join(tr["tags"]),
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
            hi_r = done[~done["tags"].fillna("").str.contains("r_below_min")]
            if len(hi_r):
                rr_ = hi_r["r"].astype(float)
                print(f"  planned R >= 1 only (the report's default R filter): {len(hi_r)} trades  win rate "
                      f"{(rr_ > 0).mean():.0%}  avg R {rr_.mean():.2f}  total R {rr_.sum():.1f}")
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
