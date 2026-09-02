"""Validate the LXPB level ledger cache.

Three phases, all of which must pass before trusting `lxpb_levels_cache`:

1. **Live-set equivalence** -- the ledger is only useful if
   `levels_live_as_of(ledger, T)` returns EXACTLY what
   `detect_lxpb_h1(bars[:T])` would have reported as `touch_lv0 + touch_lv1`.
   The machine is replayed from scratch at many cut points and compared, on
   both H1 and M5 (the two timeframes the cache serves).

2. **Retest-set equivalence** -- the ledger must reproduce the completed-retest
   set exactly, since that is the trade population every other script in the
   repo depends on.

3. **Report regression** -- the two `build_m5_chart` rows that exposed the M5
   bugs (see ../AGENTS.md) must still render the levels a human verified by
   hand off the charts.

    python verify_levels_cache.py              # all three phases
    python verify_levels_cache.py --quick      # skip the slow rebuilds
    python verify_levels_cache.py --skip-report

Phases 1-2 pass `rebuild=True` on purpose: they must test the *builder*, not
whatever happens to be sitting in the Parquet cache.
"""
import sys
import numpy as np
import pandas as pd

import lxpb_levels_cache as LC
import render_labels_report as R

L = R.L

QUICK = "--quick" in sys.argv
SKIP_REPORT = "--skip-report" in sys.argv


def direct_live_at(bars, cut_pos):
    """Ground truth: run the machine over bars[:cut_pos] and read its buckets."""
    state = L.new_state()
    for bar in bars.iloc[:cut_pos].itertuples(index=True):
        L.advance_one_bar(state, bar)
    out = {}
    for lv in state["touch_lv0"]:
        out[(lv["type"], lv["formation_time"])] = ("formed", float(lv["price"]))
    for lv in state["touch_lv1"]:
        out[(lv["type"], lv["formation_time"])] = ("broken", float(lv["price"]))
    return out


def ledger_live_at(ledger, as_of):
    d = LC.levels_live_as_of(ledger, as_of)
    return {(r["type"], pd.Timestamp(r["formation_time"])): (r["stage"], float(r["price"]))
            for _, r in d.iterrows()}


def compare(bars, ledger, label, n_cuts=25, seed=7):
    rng = np.random.default_rng(seed)
    n = len(bars)
    # Spread cut points over the whole series, plus the extremes.
    cuts = sorted(set(
        [2, 3, n // 2, n - 1, n]
        + list(rng.integers(5, n, size=n_cuts).tolist())
    ))
    bad = 0
    for cp in cuts:
        # State after processing bars[:cp] == state at the close of bar cp-1.
        as_of = bars.index[cp - 1]
        want = direct_live_at(bars, cp)
        got = ledger_live_at(ledger, as_of)
        if want != got:
            bad += 1
            only_w = {k: v for k, v in want.items() if got.get(k) != v}
            only_g = {k: v for k, v in got.items() if want.get(k) != v}
            print(f"  MISMATCH at cut {cp} ({as_of}):")
            for k, v in list(only_w.items())[:5]:
                print(f"     direct-only  {k} {v}   ledger={got.get(k)}")
            for k, v in list(only_g.items())[:5]:
                print(f"     ledger-only  {k} {v}   direct={want.get(k)}")
    print(f"{label}: {len(cuts)} cut points, {len(cuts)-bad} exact, {bad} mismatched")
    return bad


def _norm(df):
    """Comparable tuples, with timestamps normalised to tz-naive UTC.

    NaN is never equal to itself, so float fields are stringified -- otherwise
    the ~120 H1 retests whose `fta` is NaN (breakout and retest separated by a
    weekend gap, so no intervening bar ever updated running_fta) would report
    as both missing AND extra."""
    def ts(v):
        t = pd.Timestamp(v)
        return t.tz_localize(None) if t.tzinfo is not None else t

    def f(v):
        return "nan" if pd.isna(v) else f"{float(v):.4f}"

    return {(r["type"], ts(r["formation_time"]), f(r["price"]),
             ts(r["retest_time"]), f(r["fta"]), f(r["stop_loss"]))
            for _, r in df.iterrows()}


def compare_retests(bars, ledger, label):
    _lv0, _lv1, rt = L.detect_lxpb_h1(bars)
    led = LC.retests(ledger)
    if rt.empty and led.empty:
        print(f"{label} retests: both empty")
        return 0
    a, b = _norm(rt), _norm(led)
    missing, extra = a - b, b - a
    print(f"{label} retests: detect_lxpb_h1={len(a)}  ledger={len(b)}  "
          f"missing={len(missing)}  extra={len(extra)}")
    for x in list(missing)[:3]:
        print("     missing:", x)
    for x in list(extra)[:3]:
        print("     extra  :", x)
    return len(missing) + len(extra)


# Rows from stop2_target8 reports whose M5 levels were read off the charts by
# hand. Row 1 caught the M5_MAX_SPAN_DAYS window bug (9-day breakout->retest
# gap made the formation filter unsatisfiable); row 59 caught dead `retested`
# levels being drawn as live.
REPORT_CASES = [
    ("row 1  LLPB 7568.00", "2026-07-01", "2026-07-02", "LLPB", 7568.00,
     {7569.25, 7569.50, 7576.00, 7576.50, 7579.25, 7584.00}),
    ("row 59 LHPB 6984.25", "2026-01-19", "2026-01-23", "LHPB", 6984.25,
     {6983.75, 6981.75, 6976.25, 6971.25}),
]


def check_report_rows():
    import render_stop_target_report as RS
    import analyze_breakout_exits_1min as M
    stop, target = 2.0, 8.0
    bad = 0
    for label, start, end, ltype, lprice, expect in REPORT_CASES:
        _h1, _pos, strong, trades = RS._select_rows(start, end, None, None)
        want = next((i for i in range(len(strong))
                     if strong.iloc[i]["type"] == ltype
                     and abs(float(strong.iloc[i]["price"]) - lprice) < 0.01), None)
        if want is None:
            print(f"{label}: TRADE NOT FOUND")
            bad += 1
            continue
        series = M.build_or_load_1min_series(
            [trades[want]], cache_path=f"data/_verify_{want}.csv")
        resolved = RS.resolve_trades([trades[want]], series, stop, target)[0]
        m5 = RS.build_m5_chart(strong.iloc[want], resolved, stop, target)
        if m5 is None:
            print(f"{label}: build_m5_chart returned None")
            bad += 1
            continue
        got = {round(float(x["label"].split()[2]), 2) for x in m5.get("rays", [])}
        ok = got == expect
        bad += 0 if ok else 1
        print(f"{label}: {'OK' if ok else 'MISMATCH'}  {sorted(got)}")
        if not ok:
            print(f"     expected {sorted(expect)}")
            print(f"     missing {sorted(expect - got)}  extra {sorted(got - expect)}")
    return bad


bad = 0

print("=== H1 ===")
h1_bars = R._display_h1()
h1 = LC.h1_levels(bars=h1_bars, rebuild=not QUICK)
LC._print_stats(h1, "H1 ledger")
bad += compare(h1_bars, h1, "H1 live-set", n_cuts=8 if QUICK else 25)
bad += compare_retests(h1_bars, h1, "H1")

print("\n=== M5 (EPU26) ===")
seg = R._contract_index_for(pd.Timestamp("2026-07-01 15:00", tz="UTC"))
m5_bars = LC.m5_bars_for_contract(seg)
print(f"M5 bars: {len(m5_bars):,}  {m5_bars.index[0]} -> {m5_bars.index[-1]}")
m5 = LC.m5_levels(seg, rebuild=not QUICK)
LC._print_stats(m5, "M5 ledger")
bad += compare(m5_bars, m5, "M5 live-set", n_cuts=5 if QUICK else 12)
bad += compare_retests(m5_bars, m5, "M5")

if not SKIP_REPORT:
    print("\n=== build_m5_chart regression ===")
    bad += check_report_rows()

print("\nRESULT:", "ALL EXACT" if bad == 0 else f"{bad} PROBLEM(S)")
sys.exit(1 if bad else 0)
