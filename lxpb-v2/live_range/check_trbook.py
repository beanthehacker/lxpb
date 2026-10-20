"""Live TradingRangeBook vs batch patterns_pure.find_trading_range on the same bars.

    python scid_only/run_scid_only.py live_range/check_trbook.py --from 2026-10-13 --to 2026-10-17 --warmup-days 2

Replays the ticks through the engine with box_detector="trading_range", then runs the batch
detector over the engine's own M5 bars with its M1 bars as lower data, and compares every
confirm / merge / break (time, box, edges). Also lists the boxes, renewals and episodes.
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_range  # noqa: E402,F401  (puts patterns_pure on sys.path)
from find_trading_range import find_trading_range  # noqa: E402
from live_range.engine import Params, PT  # noqa: E402
from live_range import replay as RP  # noqa: E402


def frame(s):
    return pd.DataFrame({"open": s.o, "high": s.h, "low": s.l, "close": s.c},
                        index=pd.to_datetime(s.t, unit="ns", utc=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--warmup-days", type=float, default=2.0)
    a = ap.parse_args()
    lo, hi = pd.Timestamp(a.lo, tz="UTC"), pd.Timestamp(a.hi, tz="UTC")
    eng, start = RP.build_engine(lo, hi, a.warmup_days, Params(box_detector="trading_range"))
    RP.feed(eng, start, hi)
    book = eng.box
    live = [dict(e, time=pd.Timestamp(e["time"], unit="ns", tz="UTC")) for e in book.tr.events]
    batch = find_trading_range(frame(book.m5), return_all=True, lower_data=frame(book.m1))["events"]
    batch = [dict(r, time=pd.Timestamp(r["time"])) for r in batch.to_dict("records")]
    same = live == batch
    print(f"tracker events: live {len(live)}, batch {len(batch)}: {'IDENTICAL' if same else 'DIFFER'}")
    if not same:
        for x, y in zip(live, batch):
            if x != y:
                print("  first difference:\n   live ", x, "\n   batch", y)
                break
    fmt = lambda ns: pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(PT).strftime("%m-%d %H:%M")
    print("\nboxes (id gen root: start / confirmed / status):")
    for b in book.boxes:
        brk = f" {b['break_dir']} {b.get('break_grade')} {fmt(b['break_t'])}" if b["break_t"] else ""
        print(f"  {b['id']:>3} g{b['gen']} r{b['root']:<3} {fmt(b['start_t'])} / {fmt(b['confirm_t'])}  "
              f"{b['seed_low']:.2f}-{b['seed_high']:.2f}  {b['status']}{brk}"
              + (f"  -> renewed as {b['renewed_into']}" if b.get("renewed_into") is not None else ""))
    eps = [e for r in [*book.readers.values()] for e in r.episodes]
    kinds = {}
    for ev in eng.events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    print("\nengine events:", {k: v for k, v in sorted(kinds.items()) if "episode" in k or "box" in k or "merge" in k})
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
