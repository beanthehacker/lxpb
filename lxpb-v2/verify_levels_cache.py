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
   hand off the charts. H1 fine-tune overlays must contain only unconsumed,
   same-side H1 support, never the broader H1/M5 history.

    python verify_levels_cache.py              # all three phases
    python verify_levels_cache.py --quick      # skip the slow rebuilds
    python verify_levels_cache.py --skip-report
    python verify_levels_cache.py --h1-confluence-only  # synthetic chart cases, no tick loading
    python verify_levels_cache.py --finetune-only       # zones, charts, entries, orders, table
    python verify_levels_cache.py --report-only         # the two real-data M5 chart cases

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


def check_h1_confluence_charts():
    from types import SimpleNamespace
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    p1 = pd.Timestamp("2026-01-10", tz="UTC")
    p2 = pd.Timestamp("2026-01-11", tz="UTC")
    args = SimpleNamespace(stop=2.0, fallback_target=8.0, max_alt_fill_hours=3)
    bars = pd.DataFrame(
        {"open": 100.0, "high": 103.0, "low": 97.0, "close": 100.0},
        index=pd.date_range("2026-01-01", "2026-01-13", freq="h"))
    pos = {t: i for i, t in enumerate(bars.index)}

    def identity(df):
        return set(zip(df["type"], df["price"], df["formation_time"]))

    for side in ("LLPB", "LHPB"):
        opposite = "LHPB" if side == "LLPB" else "LLPB"

        def level(price, formed, death, type_=side):
            return {
                "type": type_, "price": price,
                "formation_time": pd.Timestamp(formed, tz="UTC"),
                "breakout_time": p1, "death_time": death,
                "fate": "open_awaiting_retest" if pd.isna(death) else "retested",
                "dist": abs(price - 100.0),
            }

        levels = pd.DataFrame([
            level(100.0, "2026-01-01", p2),                # yellow H1 level
            level(100.0, "2026-01-02", p2),                # same price, distinct P0
            level(99.75, "2026-01-03", pd.NaT),            # still open
            level(99.50, "2025-10-01", p2 + pd.Timedelta(days=1)),
            level(99.25, "2026-01-04", p2 - pd.Timedelta(hours=1)),
            level(99.0, "2026-01-05", p1),                # consumed before P1
            level(100.25, "2026-01-06", pd.NaT, opposite),
            level(100.50, "2026-01-11 01:00", pd.NaT),     # formed after P2
        ])
        row = {
            "type": side, "price": 100.0, "entry_price": 100.0,
            "formation_time": pd.Timestamp("2026-01-01"),
            "breakout_time": p1.tz_localize(None),
            "retest_time": p2.tz_localize(None),
        }
        other = dict(row, formation_time=pd.Timestamp("2026-01-02"))
        cluster = [
            {"i": 0, "row": row, "same_side_h1": levels.iloc[1:].copy()},
            {"i": 1, "row": other, "same_side_h1": levels.drop(index=1)},
        ]
        got = F.h1_chart_confluence(cluster, 100.0, row["formation_time"])
        assert identity(got) == identity(levels.iloc[[1, 2, 3]]), got
        assert len(got) == 3, "Shared supporting levels must be deduplicated by identity"
        dead_cluster = [dict(cluster[0], same_side_h1=levels.iloc[[4, 5]])]
        assert F.h1_chart_confluence(dead_cluster, 100.0, row["formation_time"]).empty
        empty_cluster = [dict(cluster[0], same_side_h1=levels.iloc[:0])]
        assert F.h1_chart_confluence(empty_cluster, 100.0, row["formation_time"]).empty

        m5 = pd.DataFrame([level(101.0, "2026-01-01 00:05", pd.NaT)])
        entry = 98.0 if side == "LHPB" else 102.0
        res = {
            "row": row, "level_type": side, "is_long": side == "LHPB",
            "alt_price": entry, "fill_price": entry, "alt_source": "m5",
            "group_n": 5, "stop_pts": 2.0, "target_pts": 8.0,
            "target_source": "fallback_fixed", "chart_confluent_h1": got,
            "entry_m5_level": m5.iloc[0].to_dict(),
            "confluent_combined": pd.concat([levels, m5], ignore_index=True),
            "h1_price": 100.0, "h1_formation_time": row["formation_time"],
            "h1_end_time": row["retest_time"], "fail_reason": "unfilled_within_window",
            "resolved": {"outcome": "target", "r": 4.0,
                         "touch_time": p2, "exit_time": p2 + pd.Timedelta(hours=1)},
        }
        with patch.object(F.SR, "build_m5_chart", return_value=None), \
             patch.object(F.R, "build_1s_trio_chart", return_value=None), \
             patch.object(F, "build_fill_window_chart", return_value=None):
            filled, _ = F.build_chart_stack_for_row(bars, pos, res)
            unfilled, _ = F.build_unfilled_chart_stack(bars, pos, res, args)
        for stack in (filled, unfilled):
            chart = stack["h1"]
            labels = [ray["label"].split()[:3] for ray in chart["rays"][1:]]
            assert labels == [[f"c{i}", side, f"{r.price:.2f}"]
                              for i, r in enumerate(got.itertuples(), start=1)], labels
            assert "3 confluent level(s)" in chart["title"], chart["title"]
            assert chart["rays"][0]["points"][0]["value"] == 100.0
            assert any(line["title"] == f"entry {entry:.2f}" for line in chart["priceLines"])
        separate = F.SR.build_trade_chart(
            bars, pos, dict(row, price=entry), None, res["resolved"], 2.0, 8.0,
            level_price=100.5, ray_formation_time=pd.Timestamp("2026-01-02"),
            ray_end_time=row["retest_time"])
        assert any(m["text"] == "P0" for m in separate["markers"])
        assert any(m["text"] == "H1 entry level 100.50" for m in separate["markers"])
        assert separate["rays"][0]["points"][0]["value"] == 100.5
        assert separate["rays"][0]["priceLabel"]
        assert separate["rays"][1]["label"].startswith("P0 H1")
        assert separate["rays"][1]["points"][0]["value"] == 100.0
        print(f"H1 {side} confluence: live H1 only; filled/unfilled charts OK")
    return 0


def check_finetune_orders():
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    times = pd.date_range("2026-08-07 12:53:06.943", periods=5, freq="us", tz="UTC")

    def ticks(records):
        frame = pd.DataFrame(records, columns=["Low", "High", "Close", "BidVolume", "AskVolume"],
                             index=times[:len(records)])
        frame["Trades"] = 1
        return frame

    short = ticks([
        (7778.25, 7778.50, 7778.25, 1, 0),
        (7778.25, 7778.50, 7778.25, 2, 0),
        (7778.00, 7778.25, 7778.00, 1, 0),
        (7778.25, 7778.50, 7778.25, 0, 1),  # quote at limit, trade below it
        (7778.25, 7778.50, 7778.50, 0, 1),
    ])
    assert F._scan_alt_fill(short, 7778.50, False, True) == (times[1], 7778.25)
    assert F._scan_alt_fill(short.iloc[:1], 7778.50, False, True) == (None, None)
    assert F._scan_alt_fill(short, 7778.50, False) == (times[4], 7778.50)
    assert F._scan_alt_fill(short, 7778.50, False, True, peg_cap=0) == (times[4], 7778.50)
    long = ticks([(100.0, 100.25, 100.25, 0, 1), (100.0, 100.25, 100.25, 0, 2)])
    assert F._scan_alt_fill(long, 100.0, True, True) == (times[1], 100.25)
    away = ticks([(100.0, 100.25, 100.0, 1, 0),
                  (99.5, 99.75, 99.5, 1, 0),
                  (99.75, 100.0, 100.0, 0, 1)])
    assert F._scan_alt_fill(away, 100.25, False, True) == (times[2], 100.0)
    capped = ticks([(99.0, 100.0, 99.0, 1, 0)] * 5)
    assert F._scan_alt_fill(capped, 100.0, False, True, peg_cap=0.5) == (None, None)
    improved = ticks([(100.0, 100.25, 100.0, 1, 0), (100.5, 100.75, 100.5, 1, 0)])
    assert F._scan_alt_fill(improved, 100.25, False, True) == (times[1], 100.5)
    for step, cap in [(0, 1), (0.1, 1), (0.25, -1), (0.25, 0.1)]:
        try:
            F._scan_alt_fill(short, 7778.5, False, True, step, cap)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid peg parameters accepted: {step}, {cap}")
    with patch.object(F.R, "_ticks_for_window", return_value=short), \
         patch.object(F.R, "_offset_for_ts", return_value=(10.0, "EPU26")):
        assert F.find_alt_fill({"retest_time": times[0]}, 7788.5, False, "LLPB", 1,
                               pegged=True) == (times[1], 7788.25)

    p0, p1, p2 = (pd.Timestamp(t, tz="UTC") for t in
                  ("2026-01-01", "2026-01-10", "2026-01-11"))
    row = {"type": "LLPB", "price": 100.0, "entry_price": 100.0,
           "formation_time": p0.tz_localize(None), "breakout_time": p1.tz_localize(None),
           "retest_time": p2.tz_localize(None)}

    def level(price, formation, death=pd.NaT):
        return {"type": "LLPB", "price": price, "formation_time": formation,
                "breakout_time": p1, "death_time": death, "fate": "retested",
                "dist": abs(price - 100.0)}

    h1 = pd.DataFrame([level(103.0, p0 + pd.Timedelta(days=2), p2 - pd.Timedelta(hours=1)),
                       level(100.5, p0 + pd.Timedelta(days=3))])
    m5 = pd.DataFrame([level(102.0, p1 - pd.Timedelta(hours=1), p1 + pd.Timedelta(minutes=20)),
                       level(101.5, p1 - pd.Timedelta(minutes=5), p2),
                       level(104.0, p1 + pd.Timedelta(hours=1))])
    cluster = [{"i": 0, "row": row, "confluent_h1": h1, "same_side_h1": h1}]
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, m5, m5)):
        plan = F.cluster_confluence(cluster)
    assert (plan["alt_price"], plan["alt_source"], plan["h1_price"]) == (101.5, "m5", 100.5)
    assert plan["entry_m5_level"]["formation_time"] == m5.iloc[1]["formation_time"]
    assert plan["group_n"] == 3
    stale = m5.iloc[[0, 2]]
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, stale, stale)):
        plan = F.cluster_confluence(cluster)
    assert (plan["alt_price"], plan["alt_source"], plan["entry_m5_level"]) == (100.5, "h1", None)
    open_m5 = m5.iloc[[1]].assign(death_time=pd.NaT)
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, open_m5, open_m5)):
        assert F.cluster_confluence(cluster)["alt_price"] == 101.5

    for side in ("LLPB", "LHPB"):
        is_long = side == "LHPB"
        sign = 1 if is_long else -1
        opposite = "LLPB" if is_long else "LHPB"
        target_levels = pd.DataFrame([
            dict(level(100.0 + sign * distance, p0 + pd.Timedelta(hours=i)),
                 type=opposite)
            for i, distance in enumerate((20.25, 21.0))
        ])
        target_levels["death_time"] = pd.to_datetime(target_levels["death_time"], utc=True)
        assert F.dynamic_target(target_levels, side, 100.0, is_long, p2) == (None, None)
        target_levels.loc[0, "price"] = 100.0 + sign * 20.0
        price, _ = F.dynamic_target(target_levels, side, 100.0, is_long, p2)
        assert price == 100.0 + sign * 20.0
        target_levels.loc[0, "death_time"] = p2
        assert F.dynamic_target(target_levels, side, 100.0, is_long, p2) == (None, None)
    assert F.DEFAULT_FALLBACK_TARGET == 8.0
    print("Fine-tune entries: P2 liveness, one-tick pegging, cap, offsets, 20pt target band OK")
    return 0


def check_finetune_zones():
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    p1 = pd.Timestamp("2026-01-20 06:00")
    p2 = pd.Timestamp("2026-01-20 15:00")
    formations = pd.to_datetime([
        "2026-01-20 04:00", "2026-01-20 05:00",
        "2026-01-19 15:00", "2026-01-20 00:00",
        "2026-01-18 10:00", "2026-01-18 11:00",
    ])
    assert F.H1_CONFLUENCE_N_POINTS == 5.25
    assert F.M5_CONFLUENCE_N_POINTS == 5.0
    assert F.SR.CONFLUENCE_N_POINTS == 2.5, "Original stop/target reports keep their own radius"

    for side in ("LLPB", "LHPB"):
        sign = 1 if side == "LLPB" else -1
        prices = [7008.0 + sign * distance for distance in (6.25, 6.5, 0.0, 1.0, 30.0, 31.0)]
        strong = pd.DataFrame({
            "type": side, "price": prices, "formation_time": formations,
            "breakout_time": p1, "retest_time": p2,
        })
        ledger = strong.assign(death_time=p2, fate="retested")
        for column in ("formation_time", "breakout_time", "death_time"):
            ledger[column] = pd.to_datetime(ledger[column], utc=True)
        opposite = ledger.iloc[[0]].assign(type="LHPB" if side == "LLPB" else "LLPB")
        ledger = pd.concat([ledger, opposite], ignore_index=True)
        selection = (pd.DataFrame(), {}, strong, [])
        with patch.object(F.SR, "_select_rows", return_value=selection), \
             patch.object(F.LC, "h1_levels", return_value=ledger):
            _, _, _, previous = F.select_candidates(1, h1_confluence_points=2.5)
            _, _, _, short = F.select_candidates(1, h1_confluence_points=5.0)
            _, _, _, widened = F.select_candidates(1)
            _, _, _, strict_previous = F.select_candidates(2, h1_confluence_points=2.5)
            _, _, _, strict_widened = F.select_candidates(2)

        def members(candidates):
            return {frozenset(c["i"] for c in group) for group in F.cluster_candidates(candidates)}

        separate = {frozenset((0, 1)), frozenset((2, 3)), frozenset((4, 5))}
        assert members(previous) == members(short) == separate
        assert members(widened) == {frozenset((0, 1, 2, 3)), frozenset((4, 5))}
        assert strict_previous == []
        assert {c["i"] for c in strict_widened} == {0, 3}
        assert all((c["same_side_h1"]["type"] == side).all() for c in widened)

        m5 = ledger.iloc[[0, 1]].copy()
        m5["price"] = [7008.0 + sign * distance for distance in (9.25, 4.75)]
        m5["formation_time"] = pd.to_datetime(["2026-01-20 05:55", "2026-01-20 00:35"], utc=True)
        group = next(g for g in F.cluster_candidates(widened) if g[0]["i"] == 0)
        with patch.object(F.LC, "m5_levels_for_ts", return_value=m5):
            plan = F.cluster_confluence(group)
            old_plans = [F.cluster_confluence(g) for g in F.cluster_candidates(previous)[:2]]
        assert plan["h1_price"] == prices[1]
        assert plan["alt_price"] == 7008.0 + sign * 9.25
        assert plan["alt_source"] == "m5"
        assert [p["alt_price"] for p in old_plans] == [7008.0 + sign * d for d in (9.25, 4.75)]

        # Radius zero still recognizes a distinct level at the same price.
        duplicate = ledger.iloc[[0]].assign(formation_time=ledger.iloc[0]["formation_time"]
                                           + pd.Timedelta(minutes=15))
        with patch.object(F.SR, "_select_rows", return_value=selection), \
             patch.object(F.LC, "h1_levels", return_value=pd.concat([ledger, duplicate])):
            _, _, _, exact = F.select_candidates(1, h1_confluence_points=0.0)
        assert [c["i"] for c in exact] == [0]

    with patch.object(F.SR, "_select_rows") as select:
        for radius in (-0.25, np.nan, np.inf, -np.inf):
            try:
                F.select_candidates(1, h1_confluence_points=radius)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Invalid H1 radius accepted: {radius}")
        select.assert_not_called()
    print("Fine-tune zones: inclusive 5.25pt H1 link, SS qualification, entry union and M5 5pt OK")
    return 0


def check_execution_chart_windows():
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    hour = pd.Timestamp("2026-08-19 12:00", tz="UTC")
    late_fill = pd.Timestamp("2026-08-19 14:58:45.123456", tz="UTC")
    row = {"type": "LLPB", "entry_price": 112.75, "fta": 110.75,
           "stop_loss": 114.75, "retest_time": hour.tz_localize(None)}
    ticks = pd.DataFrame(
        {"Open": 100.0, "High": 100.25, "Low": 99.75, "Close": 100.0,
         "Volume": 3, "Trades": 1, "BidVolume": 1, "AskVolume": 2},
        index=pd.date_range(hour - pd.Timedelta(hours=2),
                            hour + pd.Timedelta(hours=5), freq="s"))

    def fetch(lo, hi):
        return R._slice_sorted(ticks, lo, hi).copy()

    cases = [
        (late_fill, 45, 20),
        (late_fill.tz_localize(None), 45, 20),
        (late_fill.tz_convert("America/Los_Angeles"), 45, 20),
        (hour + pd.Timedelta(minutes=64, seconds=17, microseconds=250000), 45, 20),
        (hour - pd.Timedelta(minutes=90) + pd.Timedelta(microseconds=123456), 45, 20),
        (late_fill, 1500, 1),
        (hour + pd.Timedelta(minutes=5), 45, 20),
    ]
    for override, seconds, minutes in cases:
        touch = pd.to_datetime(override, utc=True)
        pad = pd.Timedelta(seconds=max(300, seconds + 60, (minutes + 2) * 60))
        with patch.object(R, "_ticks_for_window", side_effect=fetch) as load, \
             patch.object(R, "_offset_for_ts", return_value=(12.75, "EPU26")) as offset, \
             patch.object(R, "_find_touch_time") as find, \
             patch.object(R, "build_footprint", return_value={}) as footprint, \
             patch.object(R, "_footprint_html", side_effect=lambda fp, price, label: label):
            charts = R.build_1s_trio_chart(row, seconds, minutes, touch_time_override=override)
        assert charts is not None, override
        load.assert_called_once_with(touch - pad, touch + pad)
        offset.assert_called_once_with(touch)
        find.assert_not_called()
        expected_1s = pd.date_range(
            (touch - pd.Timedelta(seconds=seconds)).ceil("s"),
            (touch + pd.Timedelta(seconds=seconds)).floor("s"), freq="s")
        expected_1m = pd.date_range(
            (touch - pd.Timedelta(minutes=minutes)).ceil("min"),
            (touch + pd.Timedelta(minutes=minutes)).floor("min"), freq="min")
        assert [b["time"] for b in charts["trio"]["candles"]] == [
            int(t.timestamp()) for t in expected_1s]
        assert [b["time"] for b in charts["oneMin"]["candles"]] == [
            int(t.timestamp()) for t in expected_1m]
        assert charts["trio"]["markers"][0]["time"] == int(touch.timestamp())
        assert charts["oneMin"]["markers"][0]["time"] == int(touch.floor("min").timestamp())
        assert all(b["close"] == 112.75 for b in charts["trio"]["candles"])
        assert len(charts["trio"]["bid"]) == len(expected_1s)
        assert len(charts["trio"]["ask"]) == len(expected_1s)
        assert charts["footprintNarrowHtml"] == "Narrow" and charts["footprintWideHtml"] == "Wide"
        assert [call.args for call in footprint.call_args_list] == [
            (touch, 100.0, R.FOOTPRINT_PRE_SECONDS, post, 12.75)
            for post in (R.FOOTPRINT_NARROW_POST_SECONDS, R.FOOTPRINT_WIDE_POST_SECONDS)]

    with patch.object(R, "_ticks_for_window", side_effect=fetch) as load, \
         patch.object(R, "_offset_for_ts", return_value=(12.75, "EPU26")) as offset, \
         patch.object(R, "_find_touch_time", wraps=R._find_touch_time) as find:
        charts = R.build_1s_trio_chart(row, include_footprint=False)
    load.assert_called_once_with(hour - pd.Timedelta(minutes=22), hour + pd.Timedelta(minutes=82))
    offset.assert_called_once_with(hour)
    assert find.call_count == 1
    assert find.call_args.args[1:] == (hour, hour + pd.Timedelta(hours=1), 100.0, "LLPB")
    assert charts["trio"]["markers"][0]["time"] == int(hour.timestamp())
    assert charts["touch_bid_volume"] == 1.0 and charts["touch_ask_volume"] == 2.0

    for missing in (None, ticks.iloc[:0]):
        with patch.object(R, "_ticks_for_window", return_value=missing), \
             patch.object(R, "_offset_for_ts", return_value=(12.75, "EPU26")), \
             patch.object(R, "build_footprint") as footprint:
            assert R.build_1s_trio_chart(row, touch_time_override=late_fill) is None
        footprint.assert_not_called()
    with patch.object(R, "_ticks_for_window") as load:
        try:
            R.build_1s_trio_chart(row, touch_time_override=pd.NaT)
        except ValueError:
            pass
        else:
            raise AssertionError("NaT fill override accepted")
        load.assert_not_called()

    res = {"row": row, "fill_price": 112.75, "alt_price": 113.0, "is_long": False,
           "stop_pts": 2.0, "target_pts": 1.5, "resolved": {"touch_time": late_fill}}
    with patch.object(R, "_ticks_for_window", side_effect=fetch), \
         patch.object(R, "_offset_for_ts", return_value=(12.75, "EPU26")), \
         patch.object(R, "build_footprint", return_value={}), \
         patch.object(R, "_footprint_html", side_effect=lambda fp, price, label: label):
        panes, fp = F.build_execution_charts(res)
    for pane in panes.values():
        assert any(pl["title"] == "target 111.25" for pl in pane["priceLines"])
        assert any(pl["title"] == "planned entry 113.00" for pl in pane["priceLines"])
    assert fp == {"narrow": "Narrow", "wide": "Wide"}
    assert row["fta"] == 110.75 and row["entry_price"] == 112.75
    print("Execution charts: exact fill-centered fetches, full context, offsets and footprints OK")
    return 0


def check_finetune_table():
    import re
    from contextlib import redirect_stdout
    from io import StringIO
    from types import SimpleNamespace
    from unittest.mock import mock_open, patch
    import render_ss_confl_finetune_report as F

    p0 = pd.Timestamp("2026-01-01 00:00")
    p2 = pd.Timestamp("2026-01-02 00:00")
    cases = [
        ("LLPB", [101.25, 100.00], True),
        ("LHPB", [200.00, 202.50], True),
        ("LHPB", [300.00, 301.50], False),
        ("LLPB", [400.00], True),
        ("LHPB", [500.00], False),
    ]
    clusters, results = [], []
    for i, (side, prices, filled) in enumerate(cases):
        cluster = [
            {"i": i * 10 + j, "row": {
                "type": side, "price": price, "formation_time": p0 + pd.Timedelta(hours=j),
                "retest_time": p2}}
            for j, price in enumerate(prices)
        ]
        clusters.append(cluster)
        results.append({
            "i": cluster[0]["i"], "row": cluster[0]["row"],
            "level_type": side, "is_long": side == "LHPB",
            "ss_confl": 0, "cluster_size": len(prices), "cluster_members": prices,
            "own_price": prices[0], "alt_price": 999.0, "fill_price": 999.0,
            "alt_source": "m5", "improved": True, "filled": filled,
            "confluent_h1": pd.DataFrame({"price": [998.0]}),
            "resolved": {"outcome": "target", "r": 4.0,
                         "exit_time": p2 + pd.Timedelta(minutes=1), "exit_price": 1007.0},
            "baseline_resolved": {"outcome": "stop", "r": -1.0},
            "stop_pts": 2.0, "target_pts": 8.0,
            "stop_price": 997.0, "target_price": 1007.0,
            "target_source": "fallback_fixed", "touch_time_alt": p2,
            "adverse_pts": 0.25, "giveback_pts": 1.0,
        })

    candidates = [c for cluster in clusters for c in cluster]
    args = SimpleNamespace(
        ss_confl_min=1, start=None, end=None, limit=None, merged=True, max_rows=None,
        stop=2.0, fallback_target=8.0, baseline_stop=2.0, baseline_target=8.0,
        pegged_entry=False, max_alt_fill_hours=3.0, full_year=True, output="unused.html")
    output = mock_open()
    with patch.object(F, "select_candidates", return_value=(
            pd.DataFrame(), {}, pd.DataFrame([c["row"] for c in candidates]), candidates)), \
         patch.object(F, "cluster_candidates", return_value=clusters), \
         patch.object(F, "process_cluster", side_effect=results), \
         patch.object(F, "build_chart_stack_for_row", return_value=({}, {})), \
         patch.object(F, "build_unfilled_chart_stack", return_value=({}, {})), \
         patch.object(F, "open", output, create=True), redirect_stdout(StringIO()):
        F.render(args)
    html = "".join(call.args[0] for call in output().write.call_args_list)
    rows = re.findall(r'<tr class="lvl-row\b[^>]*>.*?</tr>', html, re.S)
    assert len(rows) == len(cases)
    for row, (_, prices, filled) in zip(rows, cases):
        cells = re.findall(r'<td\b([^>]*)>(.*?)</td>', row, re.S)
        assert cells[3][1] == "1", "External Confl. count must stay unchanged"
        assert 'class="left merged-h1-levels"' in cells[4][0]
        assert cells[4][1] == ", ".join(f"{p:.2f}" for p in prices)
        assert "998.00" not in cells[4][1] and "999.00" not in cells[4][1]
        assert ('class="lvl-row unfilled-row"' in row) == (not filled)
        widths = [re.search(r'colspan="(\d+)"', attrs) for attrs, _ in cells]
        assert sum(int(w.group(1)) if w else 1 for w in widths) == F.N_COLS
    assert ">Merged H1 levels</th>" in html
    assert 'data-ssconfl=' not in html and 'data-target="ssconfl"' not in html
    assert html.count('data-target="confl"') == 2
    assert "lxpb_ss_confl1_finetune_review_v1_fy2026" in html
    print("Fine-tune table: merged/single H1 prices, both sides, filled/unfilled rows OK")
    return 0


def check_m5_entry_charts():
    from unittest.mock import patch
    import render_stop_target_report as SR

    p0 = pd.Timestamp("2026-01-02", tz="UTC")
    p1 = pd.Timestamp("2026-01-10", tz="UTC")
    p2 = pd.Timestamp("2026-01-11", tz="UTC")
    bars = pd.DataFrame(
        {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0},
        index=pd.date_range("2026-01-01", "2026-01-13", freq="5min", tz="UTC"))
    for side in ("LLPB", "LHPB"):
        sign = 1 if side == "LLPB" else -1
        planned, fill = 100.0 + sign, 100.0 + sign * 0.75
        ledger = pd.DataFrame([
            {"type": side, "price": price, "formation_time": formation,
             "breakout_time": breakout, "death_time": death}
            for price, formation, breakout, death in [
                (planned, p0, p1, p2),
                (100.0 + sign * 0.5, p0 + pd.Timedelta(days=1), p1, pd.NaT),
                (100.0 + sign * 0.5, p0 + pd.Timedelta(days=2), p1, pd.NaT),
                (100.0 - sign, p0 + pd.Timedelta(days=3), pd.NaT, pd.NaT),
                (100.0 + sign * 2, p0, p1, p2 - pd.Timedelta(minutes=5)),
                (100.0 + sign * 3, p1 + pd.Timedelta(hours=1), p1, pd.NaT),
            ]
        ])
        row = {"type": side, "price": fill, "breakout_time": p1.tz_localize(None),
               "retest_time": p2.tz_localize(None)}
        resolved = {"outcome": "target", "touch_time": p2 + pd.Timedelta(minutes=2),
                    "exit_time": p2 + pd.Timedelta(minutes=10)}
        with patch.object(SR, "_m5_bars", return_value=bars), \
             patch.object(SR.LC, "m5_levels", return_value=ledger):
            chart = SR.build_m5_chart(row, resolved, 2.0, 8.0, level_price=100.0,
                                      entry_level=ledger.iloc[0].to_dict())
        selected = [r for r in chart["rays"] if r.get("priceLabel")]
        blue = [r for r in chart["rays"] if r["color"] == SR.M5_COLOR]
        assert len(selected) == 1 and len(blue) == 3
        assert selected[0]["points"][0] == {"time": int(p0.timestamp()), "value": planned}
        assert selected[0]["points"][-1]["time"] == int(p2.timestamp())
        assert "(planned entry)" in selected[0]["label"]
        assert any(m["text"] == f"ENTRY {fill:.2f}" for m in chart["markers"])
        assert any(m["text"] == f"M5 entry level {planned:.2f}" for m in chart["markers"])
        assert any(pl["title"] == f"H1 {side} 100.00" for pl in chart["priceLines"])
        assert any(pl["title"] == f"entry {fill:.2f}" for pl in chart["priceLines"])
    print("M5 entry charts: exact planned P0, fill price and all eligible blue levels OK")
    return 0


if any(flag in sys.argv for flag in ("--h1-confluence-only", "--finetune-only", "--report-only")):
    bad = 0
    if "--h1-confluence-only" in sys.argv or "--finetune-only" in sys.argv:
        bad += check_h1_confluence_charts()
    if "--finetune-only" in sys.argv:
        bad += check_finetune_zones()
        bad += check_finetune_orders()
        bad += check_execution_chart_windows()
        bad += check_m5_entry_charts()
        bad += check_finetune_table()
    if "--report-only" in sys.argv:
        bad += check_report_rows()
    sys.exit(bad)


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
    print("\n=== H1 confluence chart regression ===")
    bad += check_h1_confluence_charts()
    bad += check_finetune_zones()
    bad += check_finetune_orders()
    bad += check_execution_chart_windows()
    bad += check_m5_entry_charts()
    bad += check_finetune_table()

print("\nRESULT:", "ALL EXACT" if bad == 0 else f"{bad} PROBLEM(S)")
sys.exit(1 if bad else 0)
