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
    python verify_levels_cache.py --finetune-only       # zones, charts, orders, exits, R, table
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
            "stop_source": "fallback_fixed",
            "entry_m5_level": m5.iloc[0].to_dict(),
            "h1_price": 100.0, "h1_formation_time": row["formation_time"],
            "h1_end_time": row["retest_time"], "fail_reason": "unfilled_within_window",
            "fill_window_start": p2, "fill_window_end": p2 + pd.Timedelta(hours=3),
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
            assert any(m["text"] == "Refined H1 retest / window start"
                       and m["time"] == int(p2.timestamp()) for m in chart["markers"])
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
        assert F.find_alt_fill(times[0], 7788.5, False, "LLPB", 1,
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
    cluster = [{"i": 0, "row": row, "same_side_h1": h1}]
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, m5)):
        plan = F.cluster_confluence(cluster)
    assert not {"confluent_h1", "confluent_m5", "confl_h1_n", "ss_confl_n"} & plan.keys()
    assert (plan["alt_price"], plan["alt_source"], plan["h1_price"]) == (101.5, "m5", 100.5)
    assert plan["entry_m5_level"]["formation_time"] == m5.iloc[1]["formation_time"]
    assert plan["group_n"] == 3
    stale = m5.iloc[[0, 2]]
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, stale)):
        plan = F.cluster_confluence(cluster)
    assert (plan["alt_price"], plan["alt_source"], plan["entry_m5_level"]) == (100.5, "h1", None)
    open_m5 = m5.iloc[[1]].assign(death_time=pd.NaT)
    with patch.object(F, "m5_confluence_for_row", return_value=(m5, open_m5)):
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
        target_levels.loc[0, "death_time"] = p2 - pd.Timedelta(minutes=5)
        assert F.dynamic_target(target_levels, side, 100.0, is_long, p2) == (None, None)
    assert F.DEFAULT_FALLBACK_TARGET == 8.0
    print("Fine-tune entries: P2 liveness, one-tick pegging, cap, offsets, 20pt target band OK")
    return 0


def check_finetune_fill_windows():
    from types import SimpleNamespace
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    p0 = pd.Timestamp("2025-12-21", tz="UTC")
    p1 = pd.Timestamp("2025-12-31 20:00", tz="UTC")
    original = pd.Timestamp("2026-01-01 23:00", tz="UTC")
    refined = original + pd.Timedelta(hours=3)
    end = refined + pd.Timedelta(hours=3)
    early = original + pd.Timedelta(minutes=20)
    late = refined + pd.Timedelta(hours=2, minutes=14, seconds=36, microseconds=696001)
    args = SimpleNamespace(
        stop=4.0, fallback_target=8.0, baseline_stop=2.0, baseline_target=8.0,
        max_alt_fill_hours=3.0, pegged_entry=False, peg_step=0.25, peg_cap=1.0)
    empty = pd.DataFrame()
    rolled_ledger = pd.DataFrame({"contract": ["later"]})
    offset = 10.0

    for side in ("LLPB", "LHPB"):
        is_long = side == "LHPB"
        sign = -1 if is_long else 1
        entry = 100.0 + sign * 3

        def ticks(times):
            raw = entry - offset
            return pd.DataFrame({
                "Open": raw, "High": raw + (0.25 if is_long else 0),
                "Low": raw - (0 if is_long else 0.25), "Close": raw,
                "Trades": 1, "BidVolume": int(is_long), "AskVolume": int(not is_long),
            }, index=pd.DatetimeIndex(times))

        data = ticks([early, refined - pd.Timedelta(microseconds=1), late, end])
        with patch.object(F.R, "_ticks_for_window", return_value=data) as fetch, \
             patch.object(F.R, "_offset_for_ts", return_value=(offset, "EPH26")) as scale:
            for start in (refined, refined.tz_localize(None),
                          refined.tz_convert("America/Los_Angeles")):
                assert F.find_alt_fill(start, entry, is_long, side, 3) == (late, entry)
                fetch.assert_called_with(refined, end)
                scale.assert_called_with(refined)
            assert F.find_alt_fill(original, entry, is_long, side, 3) == (early, entry)

        for stamp, expected in (
                (refined - pd.Timedelta(microseconds=1), (None, None)),
                (refined, (refined, entry)),
                (end - pd.Timedelta(microseconds=1), (end - pd.Timedelta(microseconds=1), entry)),
                (end, (None, None))):
            with patch.object(F.R, "_ticks_for_window", return_value=ticks([stamp])), \
                 patch.object(F.R, "_offset_for_ts", return_value=(offset, "EPH26")):
                assert F.find_alt_fill(refined, entry, is_long, side, 3) == expected

        for source in ("member", "external"):
            row = {
                "type": side, "price": 100.0, "entry_price": 100.0,
                "formation_time": p0.tz_localize(None),
                "breakout_time": p1.tz_localize(None), "retest_time": original.tz_localize(None),
            }
            refined_row = dict(
                row, price=100.0 + sign * 2, entry_price=100.0 + sign * 2,
                formation_time=(p0 + pd.Timedelta(days=1)).tz_localize(None),
                retest_time=refined.tz_localize(None))
            external = pd.DataFrame([dict(
                refined_row, formation_time=p0 + pd.Timedelta(days=1),
                breakout_time=p1, retest_time=refined, death_time=refined, fate="retested")])
            cluster = [{"i": 0, "row": row,
                        "same_side_h1": external if source == "external" else empty}]
            if source == "member":
                cluster.append({"i": 1, "row": refined_row, "same_side_h1": empty})
            m5 = pd.DataFrame([{
                "type": side, "price": entry, "formation_time": p1 - pd.Timedelta(minutes=5),
                "breakout_time": p1, "death_time": late.floor("5min"), "fate": "retested",
            }])
            with patch.object(F, "m5_confluence_for_row", return_value=(m5, m5)):
                plan = F.cluster_confluence(cluster)
            assert plan["h1_retest_time"] == refined.tz_localize(None)
            assert plan["h1_end_time"] == refined.tz_localize(None)
            assert plan["alt_end_time"] == row["retest_time"], "Clipped display endpoints are not triggers"
            assert plan["h1_price"] == refined_row["price"] and plan["alt_price"] == entry

            for rolled in (False, True):
                base_touch = original + pd.Timedelta(minutes=1)
                resolved = {"outcome": "target", "r": 2.0, "touch_time": late,
                            "exit_time": late + pd.Timedelta(minutes=1)}
                base_resolved = dict(resolved, r=4.0, touch_time=base_touch)
                with patch.object(F, "m5_confluence_for_row", return_value=(m5, m5)), \
                     patch.object(F.R, "_ticks_for_window", return_value=data), \
                     patch.object(F.R, "_offset_for_ts", return_value=(offset, "EPH26")), \
                     patch.object(F.R, "_contract_index_for",
                                  side_effect=lambda ts: int(rolled and ts >= refined)), \
                     patch.object(F.LC, "m5_levels", return_value=rolled_ledger) as load_ledger, \
                     patch.object(F, "dynamic_target", return_value=(None, None)) as target, \
                     patch.object(F, "dynamic_stop", return_value=(None, None)) as stop, \
                     patch.object(F, "build_minute_bars", return_value=pd.DataFrame({"open": [entry]})) as bars, \
                     patch.object(F, "baseline_touch_time", return_value=base_touch) as baseline, \
                     patch.object(F.SR, "resolve_trades",
                                  side_effect=[[resolved], [base_resolved]]) as resolve:
                    result = F.process_cluster(cluster, args)
                assert result["filled"] and result["touch_time_alt"] == late
                assert result["fill_window_start"] == refined and result["fill_window_end"] == end
                assert result["fill_price"] == entry
                assert row["retest_time"] == original.tz_localize(None)
                assert F.cluster_anchor(cluster)["i"] == 0
                assert bars.call_args_list[0].args == (late,)
                assert bars.call_args_list[1].args == (base_touch,)
                baseline.assert_called_once_with(row, is_long)
                fine_trade = resolve.call_args_list[0].args[0][0]
                base_trade = resolve.call_args_list[1].args[0][0]
                assert fine_trade["retest_time"] == late.tz_localize(None)
                assert base_trade["retest_time"] == row["retest_time"]
                assert base_trade["entry"] == 100 and base_trade["stop_dist"] == 2
                # The M5 ledger is one continuous series now (see the
                # "continuous contracts only" convention in CLAUDE.md), so a
                # fill drifting into a later contract than the original
                # retest (rolled=True) no longer needs -- or triggers -- a
                # reload onto a different per-contract ledger: `m5` already
                # covers it, and LC.m5_levels is never called from here.
                assert target.call_args.args[0] is m5 and stop.call_args.args[0] is m5
                load_ledger.assert_not_called()

        pending = [dict(cluster[0], same_side_h1=external.assign(
            death_time=pd.NaT, retest_time=pd.NaT, fate="open_awaiting_retest"))]
        with patch.object(F, "m5_confluence_for_row", return_value=(m5, m5)), \
             patch.object(F, "find_alt_fill") as search, \
             patch.object(F, "baseline_touch_time") as baseline:
            result = F.process_cluster(pending, args)
        assert not result["filled"] and result["fail_reason"] == "refined_h1_not_retested"
        assert result["fill_window_start"] is None and result["fill_window_end"] is None
        assert result["h1_price"] == refined_row["price"], "Do not choose a different H1 using hindsight"
        search.assert_not_called()
        baseline.assert_not_called()
        with patch.object(F.SR, "build_trade_chart", return_value={
                "title": "H1", "candles": [], "markers": []}), \
             patch.object(F.SR, "build_m5_chart", return_value=None), \
             patch.object(F, "build_fill_window_chart") as chart_window:
            charts, _ = F.build_unfilled_chart_stack(pd.DataFrame(), {}, result, args)
        chart_window.assert_not_called()
        assert charts["oneMin"] is None and "fill window not started" in charts["h1"]["title"]

        chart_end = end + pd.Timedelta(minutes=30)
        chart_ticks = ticks([original, refined, late, end + pd.Timedelta(minutes=20), chart_end])
        with patch.object(F.R, "_ticks_for_window", return_value=chart_ticks) as fetch, \
             patch.object(F.R, "_offset_for_ts", return_value=(offset, "EPH26")):
            chart = F.build_fill_window_chart(refined, entry, side, 3, "unfilled_within_window")
        fetch.assert_called_once_with(refined, chart_end)
        assert chart["candles"][0]["time"] == int(refined.timestamp())
        assert chart["candles"][-1]["time"] == int((end + pd.Timedelta(minutes=20)).timestamp())
        assert all(c["close"] == entry for c in chart["candles"])
        assert "refined H1 retest" in chart["title"] and R._to_pt_str(refined) in chart["title"]

    for start, hours in ((None, 3), (pd.NaT, 3), (refined, 0), (refined, -1),
                         (refined, np.nan), (refined, np.inf)):
        try:
            F.find_alt_fill(start, 100.0, False, "LLPB", hours)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid fill window accepted: {start}, {hours}")
    print("Fine-tune windows: refined H1 candle, full half-open interval, missing triggers, baseline and roll OK")
    return 0


def check_finetune_exits():
    import render_ss_confl_finetune_report as F

    touch = pd.Timestamp("2026-07-01 10:02:30.123456", tz="UTC")
    entry_bar = touch.floor("5min")
    p1_old, p1_new = entry_bar - pd.Timedelta(hours=1), entry_bar - pd.Timedelta(minutes=10)
    formed = pd.Timestamp("2026-06-01", tz="UTC")

    def frame(records):
        df = pd.DataFrame(records)
        for col in ("formation_time", "breakout_time", "death_time"):
            df[col] = pd.to_datetime(df[col], utc=True)
        return df

    for side in ("LLPB", "LHPB"):
        is_long = side == "LHPB"
        sign = 1 if is_long else -1
        opposite = "LLPB" if is_long else "LHPB"

        def level(distance, age, p1, type_=opposite, death=pd.NaT, extreme=3.0):
            return {
                "type": type_, "price": 100.0 + sign * distance,
                "formation_time": formed + pd.Timedelta(days=age),
                "breakout_time": p1, "death_time": death,
                "breakout_high": 100.0 + extreme, "breakout_low": 100.0 - extreme,
            }

        targets = frame([
            level(2, 1, p1_new),                          # nearest; newest P1
            level(8, 5, p1_old),                          # newest eligible P0
            level(12, 4, p1_old, death=p1_new),            # dead peer still confirms shared P1
            level(3, 0, p1_new),
            level(4, 6, p1_new + pd.Timedelta(minutes=5)), # lone P1 is ineligible
            level(1.5, 7, entry_bar),                     # unfinished breakout
            level(1.75, 8, entry_bar),
        ])
        original = targets.copy(deep=True)
        for ts in (touch, touch.tz_localize(None), touch.tz_convert("America/Los_Angeles")):
            price, selected = F.dynamic_target(targets, side, 100.0, is_long, ts)
            assert price == 100.0 + sign * 8
            assert selected["formation_time"] == targets.iloc[1]["formation_time"]
        for distance in (1.0, 20.0, 0.75, 20.25, -8.0):
            changed = targets.copy()
            changed.loc[1, "price"] = 100.0 + sign * distance
            expected = distance if 1 <= distance <= 20 else 2.0
            assert F.dynamic_target(changed, side, 100.0, is_long, touch)[0] == 100 + sign * expected
        for death, expected in ((p1_new, 2.0), (entry_bar, 8.0)):
            changed = targets.copy()
            changed.loc[1, "death_time"] = death
            assert F.dynamic_target(changed, side, 100.0, is_long, touch)[0] == 100 + sign * expected
        changed = targets.copy()
        changed.loc[2, "type"] = side
        assert F.dynamic_target(changed, side, 100.0, is_long, touch)[0] == 100 + sign * 2
        duplicate = pd.concat([targets.iloc[[1]], targets.iloc[[1]]], ignore_index=True)
        assert F.dynamic_target(duplicate, side, 100.0, is_long, touch) == (None, None)
        partial = targets.iloc[[5, 6]]
        assert F.dynamic_target(partial, side, 100.0, is_long,
                                entry_bar + pd.Timedelta(minutes=5) - pd.Timedelta(nanoseconds=1)) == (None, None)
        assert F.dynamic_target(partial, side, 100.0, is_long,
                                entry_bar + pd.Timedelta(minutes=5))[0] == 100 + sign * 1.75
        pd.testing.assert_frame_equal(targets, original)

        stops = frame([
            level(-1, 2, p1_old, side, extreme=3),
            level(-2, 3, p1_new, side, extreme=7),
            level(10, -100, p1_old, side, extreme=16),     # oldest P0, opposite radius edge
            level(-10, 4, p1_new, side, extreme=14),
            level(10.25, 5, p1_old, side, extreme=50),     # outside radius
            level(0, 6, p1_old, side, death=p1_new, extreme=60),
            level(0, 7, pd.NaT, side, extreme=70),
            level(0, 8, entry_bar, side, extreme=80),
            level(0, 9, p1_old, opposite, extreme=90),
        ])
        original = stops.copy(deep=True)
        price, selected = F.dynamic_stop(stops, side, 100.0, is_long, touch)
        assert price == 100.0 - sign * 16.25
        assert selected["formation_time"] == stops.iloc[2]["formation_time"]
        assert abs(price - 100.0) > F.DYNAMIC_STOP_RADIUS_PTS, "Radius must not cap stop width"
        for index, distance in ((2, 16.25), (3, 14.25)):
            assert F.dynamic_stop(stops.iloc[[index]], side, 100.0, is_long, touch)[0] == 100 - sign * distance
        assert F.dynamic_stop(stops.iloc[4:], side, 100.0, is_long, touch) == (None, None)
        consumed_this_bar = stops.iloc[[2]].assign(death_time=entry_bar)
        assert F.dynamic_stop(consumed_this_bar, side, 100.0, is_long, touch)[0] == price
        extreme_col = "breakout_low" if is_long else "breakout_high"
        for unprotective in (100.0 + sign * 0.25, 100.0 + sign * 0.5):
            changed = stops.iloc[[0]].copy()
            changed[extreme_col] = unprotective
            assert F.dynamic_stop(changed, side, 100.0, is_long, touch) == (None, None)
        for invalid in (np.nan, np.inf, -np.inf):
            changed = stops.iloc[[0]].copy()
            changed[extreme_col] = invalid
            try:
                F.dynamic_stop(changed, side, 100.0, is_long, touch)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid confirmed breakout extreme silently accepted")
        for missing in (None, pd.DataFrame()):
            assert F.dynamic_stop(missing, side, 100.0, is_long, touch) == (None, None)
            assert F.dynamic_target(missing, side, 100.0, is_long, touch) == (None, None)
        pd.testing.assert_frame_equal(stops, original)
    assert F.DEFAULT_STOP == 4.0
    print("Fine-tune exits: newest P0/shared P1, live completed bars, +/-10pt extremes and one tick OK")
    return 0


def check_finetune_brackets():
    from types import SimpleNamespace
    from unittest.mock import patch
    import render_ss_confl_finetune_report as F

    touch = pd.Timestamp("2026-07-01 10:02:30.123456", tz="UTC")
    args = SimpleNamespace(stop=F.DEFAULT_STOP, fallback_target=8.0, baseline_stop=2.0,
                           baseline_target=8.0, max_alt_fill_hours=3.0,
                           pegged_entry=True, peg_step=0.25, peg_cap=1.0)
    empty = pd.DataFrame()
    for side in ("LLPB", "LHPB"):
        is_long = side == "LHPB"
        sign = 1 if is_long else -1
        fill = 100.0 + sign * 0.25
        row = {"type": side, "price": 100.0, "entry_price": 100.0,
               "formation_time": pd.Timestamp("2026-06-01"),
               "breakout_time": pd.Timestamp("2026-06-10"),
               "retest_time": touch.floor("h").tz_localize(None)}
        cluster = [{"i": 0, "row": row, "same_side_h1": empty}]
        ledger = pd.DataFrame([
            {"type": type_, "price": fill + sign * distance,
             "formation_time": pd.Timestamp("2026-06-20", tz="UTC") + pd.Timedelta(days=i),
             "breakout_time": touch.floor("5min") - pd.Timedelta(minutes=10),
             "death_time": pd.NaT, "breakout_high": fill + 12, "breakout_low": fill - 12}
            for i, (type_, distance) in enumerate([
                ("LLPB" if is_long else "LHPB", 2),
                ("LLPB" if is_long else "LHPB", 8),
                (side, 10),  # within 10 of FILL, but 10.25 from the planned entry
            ])
        ])
        ledger["death_time"] = pd.to_datetime(ledger["death_time"], utc=True)
        offset = 12.75
        bars = pd.DataFrame({"open": [fill - offset], "close": [fill - offset],
                             "high": [fill - offset + 25], "low": [fill - offset - 25]},
                            index=[touch.floor("min")])
        bars.attrs["touch_time"] = touch
        for use_dynamic in (True, False):
            active = ledger if use_dynamic else empty
            stop_pts = 12.25 if use_dynamic else 4.0
            for outcome in ("target", "stop"):
                def pin(_sym, _minute, _offset, entry, stop, target, long, not_before=None):
                    assert not_before == touch
                    move = target if outcome == "target" else -stop
                    return outcome, touch + pd.Timedelta(seconds=1), entry + (move if long else -move)

                with patch.object(F, "m5_confluence_for_row", return_value=(active, empty)), \
                     patch.object(F, "find_alt_fill", return_value=(touch, fill)), \
                     patch.object(F, "build_minute_bars", return_value=bars), \
                     patch.object(F, "baseline_touch_time", return_value=touch), \
                     patch.object(F.R, "_offset_for_ts", return_value=(offset, "EPU26")), \
                     patch.object(F.M, "_pin_exact_exit", side_effect=pin) as exact, \
                     patch.object(F.SR, "_compute_excursion", return_value=(1.0, 0.5, True)), \
                     patch.object(F.SR, "_compute_giveback", return_value=0.75), \
                     patch.object(F.SR, "resolve_trades", wraps=F.SR.resolve_trades) as resolve:
                    result = F.process_cluster(cluster, args)
                assert result["filled"] and result["fill_price"] == fill
                assert result["alt_price"] == 100 and result["chased_pts"] == 0.25
                assert result["stop_pts"] == stop_pts
                assert result["stop_price"] == fill - sign * stop_pts
                assert result["target_price"] == fill + sign * 8
                assert result["stop_source"] == ("m5_breakout" if use_dynamic else "fallback_fixed")
                assert result["target_source"] == ("m5_opposite" if use_dynamic else "fallback_fixed")
                assert (result["stop_m5_level"] is not None) == use_dynamic
                assert (result["target_m5_level"] is not None) == use_dynamic
                assert resolve.call_args_list[0].args[0][0]["stop_dist"] == stop_pts
                assert resolve.call_args_list[1].args[0][0]["stop_dist"] == 2.0
                assert exact.call_args_list[0].args[4:6] == (stop_pts, 8.0)
                expected_r = 8.0 / stop_pts if outcome == "target" else -1.0
                assert result["resolved"]["r"] == expected_r
                assert result["resolved"]["exit_time"] > result["resolved"]["touch_time"]
                assert result["resolved"]["exit_price"] == (
                    result["target_price"] if outcome == "target" else result["stop_price"])
    print("Fine-tune brackets: actual-fill radius, variable resolution/R, 4pt fallback, baseline 2/8 OK")
    return 0


def check_excursion_r():
    import re
    import render_stop_target_report as SR

    groups = [("sample", "", [2.0, 8.0])]

    def r_row(html):
        return re.search(r'<tr class="pctile-r">.*?</tr>', html).group(0)

    dynamic = SR.excursion_percentile_html(groups, None, group_stops=[[0.5, 8.0]])
    got = re.findall(r"<td>([0-9.]+)</td>", r_row(dynamic))
    expected = [f"{v:.2f}" for v in np.percentile([4.0, 1.0], SR.EXCURSION_PCTILES)] + ["2.50", "4.00"]
    assert got == expected, (got, expected)
    fixed = SR.excursion_percentile_html(groups, 2.0)
    equivalent = SR.excursion_percentile_html(groups, None, group_stops=[[2.0, 2.0]])
    assert r_row(fixed) == r_row(equivalent), "Fixed-stop report behavior must stay unchanged"
    for invalid in ([], [[2.0]], [[0.0, 2.0]], [[-1.0, 2.0]], [[np.nan, 2.0]]):
        try:
            SR.excursion_percentile_html(groups, None, group_stops=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid excursion risks accepted: {invalid}")
    print("Excursion R: per-trade normalization precedes percentiles; scalar stop behavior unchanged")
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
        first = ledger.iloc[[0]]
        query_ledger = pd.concat([
            ledger,
            first.assign(formation_time=first["formation_time"] + pd.Timedelta(minutes=10),
                         death_time=p1.tz_localize("UTC")),
            first.assign(formation_time=first["formation_time"] + pd.Timedelta(minutes=20),
                         breakout_time=pd.NaT),
            first.assign(formation_time=first["formation_time"] + pd.Timedelta(minutes=30),
                         death_time=pd.NaT),
            first.assign(formation_time=p2.tz_localize("UTC"),
                         breakout_time=p2.tz_localize("UTC") + pd.Timedelta(hours=1),
                         death_time=pd.NaT),
        ], ignore_index=True)
        row = strong.iloc[0]
        for radius in (F.H1_CONFLUENCE_N_POINTS, F.M5_CONFLUENCE_N_POINTS):
            historical = LC.find_confluent_levels(
                query_ledger, side, row["price"], row["formation_time"], p2, radius)
            expected = LC.same_side_live_confluence(historical, side, p1)
            with patch.object(F.LC, "find_confluent_levels",
                              wraps=F.LC.find_confluent_levels) as query:
                actual = F._same_side_confluence(query_ledger, row, radius)
            pd.testing.assert_frame_equal(actual, expected)
            searched = query.call_args.args[0]
            assert (searched["type"] == side).all()
            assert (searched["death_time"].isna() |
                    (searched["death_time"] > p1.tz_localize("UTC"))).all()
        with patch.object(F.LC, "m5_levels_for_ts", return_value=query_ledger):
            full_ledger, support = F.m5_confluence_for_row(row)
        assert full_ledger is query_ledger, "Exit searches must retain opposite/historical M5 levels"
        pd.testing.assert_frame_equal(support, expected)
        for missing in (None, pd.DataFrame()):
            with patch.object(F.LC, "m5_levels_for_ts", return_value=missing):
                full_ledger, support = F.m5_confluence_for_row(row)
            assert full_ledger.empty and support.empty
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
        assert all("confluent_h1" not in c for c in widened)

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
           "stop_pts": 3.75, "target_pts": 1.5, "resolved": {"touch_time": late_fill}}
    with patch.object(R, "_ticks_for_window", side_effect=fetch), \
         patch.object(R, "_offset_for_ts", return_value=(12.75, "EPU26")), \
         patch.object(R, "build_footprint", return_value={}), \
         patch.object(R, "_footprint_html", side_effect=lambda fp, price, label: label):
        panes, fp = F.build_execution_charts(res)
    for pane in panes.values():
        assert any(pl["title"] == "target 111.25" for pl in pane["priceLines"])
        assert any(pl["title"] == "stop 116.50" for pl in pane["priceLines"])
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
            "cluster_size": len(prices), "cluster_members": prices,
            "own_price": prices[0], "alt_price": 999.0, "fill_price": 999.0,
            "h1_price": prices[0],
            "fill_window_start": p2 + pd.Timedelta(hours=3) if i != 4 else None,
            "fill_window_end": p2 + pd.Timedelta(hours=6) if i != 4 else None,
            "alt_source": "m5", "improved": True, "filled": filled,
            "resolved": {"outcome": "target", "r": 4.0,
                         "exit_time": p2 + pd.Timedelta(hours=3, minutes=2), "exit_price": 1007.0},
            "baseline_resolved": {"outcome": "stop", "r": -1.0},
            "stop_pts": 2.0, "target_pts": 8.0,
            "stop_price": 997.0, "target_price": 1007.0,
            "target_source": "fallback_fixed", "touch_time_alt": p2 + pd.Timedelta(hours=3, minutes=1),
            "target_m5_level": None, "stop_source": "fallback_fixed", "stop_m5_level": None,
            "adverse_pts": 0.25, "giveback_pts": 1.0,
        })

    candidates = [c for cluster in clusters for c in cluster]
    args = SimpleNamespace(
        ss_confl_min=1, start=None, end=None, limit=None, merged=True, max_rows=None,
        stop=4.0, fallback_target=8.0, baseline_stop=2.0, baseline_target=8.0,
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
    for row, (_, prices, filled), result in zip(rows, cases, results):
        cells = re.findall(r'<td\b([^>]*)>(.*?)</td>', row, re.S)
        assert 'class="left merged-h1-levels"' in cells[3][0]
        assert cells[3][1] == ", ".join(f"{p:.2f}" for p in prices)
        expected_retest = (R._to_pt_str(result["fill_window_start"])
                           if result["fill_window_start"] is not None else "-")
        assert cells[2][1] == expected_retest
        assert "original H1 retest" in cells[2][0]
        if result["fill_window_end"] is not None:
            assert R._to_pt_str(result["fill_window_end"]) in cells[2][0]
        else:
            assert "fill window not started" in cells[2][0]
        assert "999.00" not in cells[3][1]
        assert ('class="lvl-row unfilled-row"' in row) == (not filled)
        widths = [re.search(r'colspan="(\d+)"', attrs) for attrs, _ in cells]
        assert sum(int(w.group(1)) if w else 1 for w in widths) == F.N_COLS
    assert ">Merged H1 levels</th>" in html
    assert ">Refined H1 retest</th>" in html
    assert F.N_COLS == 23
    assert ">Confl.</th>" not in html and 'data-confl=' not in html
    assert 'data-ssconfl=' not in html and 'data-target="ssconfl"' not in html
    assert 'data-target="confl"' not in html and 'class="f-num-op"' not in html
    assert "lxpb_ss_confl1_finetune_review_v1_fy2026" in html
    assert "MOST RECENTLY FORMED (P0)" in html and "fixed 4pt fallback" in html
    assert "Each trade is divided by its own initial stop distance" in html
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
        window_start = p2 + pd.Timedelta(hours=3)
        window_end = window_start + pd.Timedelta(hours=3)
        unresolved = {"outcome": "no_data", "touch_time": None, "exit_time": None}
        with patch.object(SR, "_m5_bars", return_value=bars), \
             patch.object(SR.LC, "m5_levels", return_value=ledger):
            unfilled = SR.build_m5_chart(
                row, unresolved, 4.0, 8.0, level_price=100.0,
                entry_level=ledger.iloc[0].to_dict(), fill_window=(window_start, window_end))
        assert any(m["text"] == f"PLANNED {fill:.2f}" and m["time"] == int(window_start.timestamp())
                   for m in unfilled["markers"])
        assert unfilled["candles"][-1]["time"] >= int(window_end.timestamp())
        assert not any(m["text"].startswith(("ENTRY ", "WIN ", "LOSS ")) for m in unfilled["markers"])
        assert row["retest_time"] == p2.tz_localize(None)
    print("M5 entry charts: exact planned P0, fill price and all eligible blue levels OK")
    return 0


if any(flag in sys.argv for flag in ("--h1-confluence-only", "--finetune-only", "--report-only")):
    bad = 0
    if "--h1-confluence-only" in sys.argv or "--finetune-only" in sys.argv:
        bad += check_h1_confluence_charts()
    if "--finetune-only" in sys.argv:
        bad += check_finetune_zones()
        bad += check_finetune_orders()
        bad += check_finetune_fill_windows()
        bad += check_finetune_exits()
        bad += check_finetune_brackets()
        bad += check_excursion_r()
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

print("\n=== M5 (continuous) ===")
m5_bars = LC.m5_bars_continuous()
print(f"M5 bars: {len(m5_bars):,}  {m5_bars.index[0]} -> {m5_bars.index[-1]}")
m5 = LC.m5_levels(rebuild=not QUICK)
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
    bad += check_finetune_exits()
    bad += check_finetune_brackets()
    bad += check_excursion_r()
    bad += check_execution_chart_windows()
    bad += check_m5_entry_charts()
    bad += check_finetune_table()

print("\nRESULT:", "ALL EXACT" if bad == 0 else f"{bad} PROBLEM(S)")
sys.exit(1 if bad else 0)
