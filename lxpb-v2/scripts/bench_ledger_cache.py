"""Scaling benchmark for lxpb_levels_cache.build_ledger().

Measures wall-clock time and bars/s across increasing prefixes of the
continuous M5 series, calling build_ledger() directly (bypassing the
on-disk cache/_reconcile) so it always measures a fresh, full build. Useful
for checking whether a change to lxpb.py or lxpb_levels_cache.py actually
fixed the scaling problem documented in docs/PERF_LEDGER_CACHE.md -- a
healthy result is roughly constant bars/s across sizes; a degrading bars/s
as n grows means something is re-scanning a list that grows with n (the
touch_lv0/touch_lv1 pattern that motivated that doc).

Usage:
    python scripts/bench_ledger_cache.py
    python scripts/bench_ledger_cache.py 5000 20000 50000
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lxpb_levels_cache as LC


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [5_000, 20_000, 50_000, 100_000]
    bars = LC.m5_bars_continuous()
    print(f"total bars available: {len(bars):,}")
    for n in sizes:
        n = min(n, len(bars))
        sl = bars.iloc[:n]
        t0 = time.time()
        df, _ = LC.build_ledger(sl, "M5")
        dt = time.time() - t0
        print(f"n={n:>7,}  levels={len(df):>7,}  seconds={dt:8.2f}  bars/s={n / dt:8.1f}")


if __name__ == "__main__":
    main()
