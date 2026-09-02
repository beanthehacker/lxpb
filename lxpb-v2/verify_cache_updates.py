"""Prove every incremental cache path equals a from-scratch rebuild.

The cache may only take a shortcut if the shortcut is indistinguishable from
recomputing. This exercises each route `_reconcile` can take:

  hit                 same bars again
  extend              bars appended (levels open at the cut may now break/die)
  reanchor            contract rollover: all prices shift by a constant
  reanchor + extend   a rollover and new bars arriving together
  rebuild             a mid-series bar was revised

and compares the result against `build_ledger` run over the same final bars,
column for column.
"""
import sys
import numpy as np
import pandas as pd

import lxpb_levels_cache as LC
import render_labels_report as R

OHLC = ["open", "high", "low", "close"]


def canon(df):
    d = df.sort_values(["formation_time", "type"]).reset_index(drop=True)
    return d[[c for c in LC.COLUMNS if c not in ("timeframe", "contract")]]


def same(a, b, label):
    a, b = canon(a), canon(b)
    if a.shape != b.shape:
        print(f"  {label}: SHAPE {a.shape} vs {b.shape}")
        return 1
    bad = []
    for c in a.columns:
        x, y = a[c], b[c]
        if x.dtype.kind == "f":
            if not np.allclose(x.fillna(-9e9), y.fillna(-9e9), atol=1e-6):
                bad.append(c)
        elif not x.equals(y):
            bad.append(c)
    if bad:
        print(f"  {label}: MISMATCH in {bad}")
        for c in bad[:2]:
            d = a[c] != b[c]
            print(f"     {c}: first diff at row {int(np.argmax(d.values))}  "
                  f"cached={a[c][d].head(2).tolist()}  fresh={b[c][d].head(2).tolist()}")
        return 1
    print(f"  {label}: identical ({len(a):,} levels)")
    return 0


def scratch(bars, name):
    df, _ = LC.build_ledger(bars, "H1", "")
    return df


bad = 0
full = R._display_h1()
CUT = len(full) - 400
DELTA = 137.25

shifted = full.assign(**{c: full[c] + DELTA for c in OHLC})

print(f"H1 bars: {len(full):,}   cut at {CUT:,}   rollover delta {DELTA:+.2f}\n")

# --- seed the cache from the truncated series -----------------------------
print("[seed] build from bars[:CUT]")
LC._reconcile("_t_h1", full.iloc[:CUT], "H1", "", rebuild=True, verbose=True)

print("\n[hit] same bars again -- must not rebuild")
d, m, _ = LC._reconcile("_t_h1", full.iloc[:CUT], "H1", "", rebuild=False, verbose=True)
print(f"  resolved={m['resolved']}  (expect 'hit')")
bad += 0 if m["resolved"] == "hit" else 1
bad += same(d, scratch(full.iloc[:CUT], "s"), "hit")

print("\n[extend] append the last 400 bars")
d, m, _ = LC._reconcile("_t_h1", full, "H1", "", rebuild=False, verbose=True)
print(f"  resolved={m['resolved']}  (expect 'extend')")
bad += 0 if m["resolved"] == "extend" else 1
bad += same(d, scratch(full, "s"), "extend")

# --- rollover on top of the extended ledger -------------------------------
print("\n[reanchor] same bars, every price shifted (rollover)")
d, m, _ = LC._reconcile("_t_h1", shifted, "H1", "", rebuild=False, verbose=True)
cur = LC._to_current_scale(d, m, shifted, True, "H1")
bad += same(cur, scratch(shifted, "s"), "reanchor")

# --- rollover AND new bars at once ----------------------------------------
print("\n[reanchor+extend] rollover arriving together with new bars")
LC._reconcile("_t_h1b", full.iloc[:CUT], "H1", "", rebuild=True, verbose=False)
d, m, _ = LC._reconcile("_t_h1b", shifted, "H1", "", rebuild=False, verbose=True)
print(f"  resolved={m['resolved']}  (expect 'extend')")
bad += 0 if m["resolved"] == "extend" else 1
cur = LC._to_current_scale(d, m, shifted, True, "H1")
bad += same(cur, scratch(shifted, "s"), "reanchor+extend")

# --- mid-series revision must NOT be served from cache --------------------
print("\n[rebuild] a bar revised in the middle, count and endpoints unchanged")
rev = full.copy()
rev.iloc[len(rev) // 2, rev.columns.get_loc("high")] += 25.0
d, m, _ = LC._reconcile("_t_h1", rev, "H1", "", rebuild=False, verbose=True)
print(f"  resolved={m['resolved']}  (expect 'rebuild')")
bad += 0 if m["resolved"] == "rebuild" else 1
bad += same(d, scratch(rev, "s"), "rebuild")

for n in ("_t_h1", "_t_h1b"):
    for p in LC._cache_paths(n):
        try:
            __import__("os").remove(p)
        except OSError:
            pass

print("\nRESULT:", "ALL PATHS EXACT" if bad == 0 else f"{bad} PROBLEM(S)")
sys.exit(1 if bad else 0)
