"""Equivalence check: the incremental BoxBook against find_range_box_zz run over the same bars.

Run through scid_only/run_scid_only.py (ticks only):
  python scid_only/run_scid_only.py live_range/check_boxes.py --from 2026-09-28 --to 2026-10-06
"""
import argparse
import sys

import numpy as np
import pandas as pd

import lxpb_levels_cache as LC
import render_labels_report as R
from find_range_box_zz import find_range_box_zz
from live_range.boxes import BoxBook, M5_NS
from live_range.levels import LevelBook

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="lo", default="2026-09-28")
ap.add_argument("--to", dest="hi", default="2026-10-06")
a = ap.parse_args()
lo, hi = pd.Timestamp(a.lo, tz="UTC"), pd.Timestamp(a.hi, tz="UTC")
m1 = R._tick_bars_spanning(lo, hi, "1min")
m5 = R._tick_bars_spanning(lo, hi, "5min")
print(f"{len(m1)} M1 bars, {len(m5)} M5 bars")

# the same M5 ledger the batch reads (plain P0s untracked, as the reports read it), restricted to this window's bars
ledger = LC.m5_levels(plain_p0=LC.PLAIN_P0_UNTRACKED, verbose=False)
batch = find_range_box_zz(m5, lower_data=m1, levels=ledger)

book, lv5 = BoxBook(), LevelBook("m5")
m1t = m1.index.asi8
m1v = m1[["open", "high", "low", "close"]].to_numpy(float)
k = 0
episodes_live = []
for ts, row in m5.iterrows():
    t = ts.value
    while k < len(m1t) and m1t[k] < t + M5_NS:
        book.on_m1_close(int(m1t[k]), *m1v[k]); k += 1
    lv5.on_bar(t, row.open, row.high, row.low, row.close)
    book.on_m5_close(t, row.open, row.high, row.low, row.close, lv5)
    for kind, payload in book.drain():
        if kind == "episode_open":
            episodes_live.append((payload["box_id"], payload["direction"], pd.Timestamp(payload["breakout_time"], unit="ns", tz="UTC")))

def tsn(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x) else pd.Timestamp(x).value

bad = 0
mine = {b["id"]: b for b in book.boxes}
print(f"batch boxes {len(batch)}, live boxes {len(book.boxes)}")
for _, r in batch.iterrows():
    b = mine.get(int(r["range_id"]))
    if b is None:
        bad += 1; print("missing live box", r["range_id"]); continue
    pairs = [("status", r["status"], b["status"]), ("start", tsn(r["start_time"]), b["start_t"]),
             ("high", float(r["high"]), float(b["high"])), ("low", float(r["low"]), float(b["low"])),
             ("n", int(r["n_pivots"]), b["n"]), ("confirm", tsn(r["confirm_time"]), b["confirm_t"])]
    if r["status"] == "broken":
        pairs += [("break_dir", r["break_dir"], b["break_dir"]),
                  ("end", tsn(r["end_time"]), m5.index[b["end_j"]].value if b["end_j"] is not None else None)]
    for name, x, y in pairs:
        if x != y:
            bad += 1; print("box", r["range_id"], name, x, y)
print("box mismatches:", bad)

# episodes: batch breakout times per (box, direction) against live
want = {}
for _, r in batch.iterrows():
    if r["status"] == "merged":
        continue
    for e in r["episodes"] or []:
        want[(int(r["range_id"]), "up" if e["direction"] == "up" else "below")] = tsn(e["breakout_time"])
got = {(bid, d): t.value for bid, d, t in episodes_live}
only_b = {k: v for k, v in want.items() if k not in got}
kept = {int(r['range_id']) for _, r in batch.iterrows() if r['status'] != 'merged'}
only_l = {k: v for k, v in got.items() if k not in want and k[0] in kept}
diff = {k: (want[k], got[k]) for k in want if k in got and want[k] != got[k]}
print(f"episodes batch {len(want)} live {len(got)}; only batch {len(only_b)}, only live {len(only_l)}, different time {len(diff)}")
for name, d in (("only batch", only_b), ("only live", only_l), ("diff", diff)):
    for k_, v in list(d.items())[:8]:
        print(" ", name, k_, v)
