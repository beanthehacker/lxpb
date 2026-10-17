"""SIDE STUDY: replay a day through the live engine twice, with the H1-backed range merge off and on, and compare.

  python scid_only/run_scid_only.py box_merge_study/h1_merge_engine.py --day 2026-10-16 [--overlap 0.5] [--fade2]

The merge itself now lives in the engine (`Params.box_merge_overlap`, 0 = off; see live_range/engine.py and README):
M5 range boxes overlapping by >= that share of the smaller box inside one known, unbroken H1 range box share their seats.
This script only runs the engine with the rule off and on and prints both trade lists and the links made.

M1 FADE PLAYBOOK ONLY (user, 2026-10-16): go-with is not part of it. The engine still takes go-with trades; they are
listed but marked, and every total and the SAME / DIFFER verdict count fade trades only.
Fade 2 is off by default in the engine (only fade 1 is traded); `--fade2` turns it back on for both runs.
"""
import argparse

import pandas as pd

import render_labels_report as R
from live_range import replay as RP
from live_range.engine import Params

PT = "America/Los_Angeles"


def pt(ns):
    return pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(PT).strftime("%m-%d %H:%M:%S")


def is_fade(tr):
    return all(pb == "fade" for _, _, pb in tr["serves"])


def run(lo, hi, warmup_days, overlap, tol, h1_days, fade2, decision_s=1.0):
    eng, start = RP.build_engine(lo, hi, warmup_days, Params(bar_minutes=1, decision_s=decision_s, box_merge_overlap=overlap,
                                                             box_merge_tol=tol, fade2=fade2), h1_days=h1_days)
    RP.feed(eng, start, hi)
    return eng


def show(name, eng):
    print(f"\n== {name}  (go-with trades are listed but not counted)")
    for tr in eng.trades:
        ex = tr["exit"]
        print(f"  {'' if is_fade(tr) else '[go-with, not counted] '}trade {tr['id']} {pt(tr['t'])} {tr['side']:<4} {tr['type']} "
              f"entry {tr['price']:.2f} stop {tr['stop']:.2f} target {tr['target']:.2f} serves {sorted(tr['serves'])} -> "
              f"{(pt(ex['t']) + ' ' + ex['why'] + ' ' + format(tr.get('pnl', float('nan')), '+.2f') + 'pt ' + format(tr.get('r', float('nan')), '+.2f') + 'R') if ex else 'open'}")
    done = [tr for tr in eng.trades if tr["exit"] and is_fade(tr)]
    print(f"  {len(done)} closed FADE trades, total R {sum(tr.get('r', 0.0) for tr in done):+.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-16")
    ap.add_argument("--warmup-days", type=float, default=5.0)
    ap.add_argument("--overlap", type=float, default=0.5)
    ap.add_argument("--contain-tol", type=float, default=2.0)
    ap.add_argument("--h1-days", type=float, default=25.0)
    ap.add_argument("--fade2", action="store_true")
    a = ap.parse_args()
    day = pd.Timestamp(a.day)
    lo = (day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)).tz_localize(PT).tz_convert("UTC")
    hi = min(pd.Timestamp(R._scid_bounds("EPZ26")[1]).floor("1min"), lo + pd.Timedelta(hours=21))
    base = run(lo, hi, a.warmup_days, 0.0, a.contain_tol, a.h1_days, a.fade2)
    show("merge OFF (every M5 box its own seat)", base)
    mer = run(lo, hi, a.warmup_days, a.overlap, a.contain_tol, a.h1_days, a.fade2)
    show(f"merge ON: M5 boxes overlapping >= {a.overlap:.0%} inside one known H1 range share a seat", mer)
    print("\nlinks made (time, box, box, overlap, H1 box):")
    for t, x, y, f, hb in mer.merge_links:
        print(f"  {pt(t)}  {x} + {y}  overlap {f:.0%}  H1 {hb}")
    key = lambda e: [(tr["t"], tr["price"]) for tr in e.trades if is_fade(tr)]
    print("\nSAME FADE TRADES" if key(base) == key(mer) else "\nFADE TRADES DIFFER")


if __name__ == "__main__":
    main()
