"""
LXPB H1 Detection
-----------------
Detects LHPB (Last High Pre-Breakout) and LLPB (Last Low Pre-Breakout) retests
on H1 OHLC data and outputs each completed retest with its entry price, FTA
target, stop loss, and spike/swing classification.

This is the canonical LXPB detection algorithm, ported from
D:\\daily-analysis\\lxpb-h1-apr2026\\lxpb_h1_detect.py (kept as the source of
truth there; this file is a synced copy for use in this repo/its tests).

Hammer / shooting-star (spike) detection is NOT defined here: it is
patterns-pure's own find_hammer / find_shooting_star, from this repo's vendored
copy in lxpb-v2/patterns_pure -- the repo-wide rule (see lxpb-v2/CLAUDE.md).
Feed advance_one_bar through iter_bars(), which attaches that verdict per bar.

Usage:
    python lxpb.py --data data/es-h1-continuous-backadjusted.csv
    python lxpb.py --data data/nq-h1-4apr2021-11apr2025.csv --output retests.csv
"""

import os
import sys
import argparse
import importlib.util
import numpy as np
import pandas as pd


MIN_BARS_BEFORE_RETEST = 1  # bar-count gap, not clock time -- see advance_one_bar's own
                            # docstring for why: a fixed hour count silently meant
                            # "skip ~1 bar" on H1 but "skip ~12 bars" on M5.

# Candidate gate, rule 4 (consolidation vs trend -- see advance_one_bar's own
# docstring): Kaufman's Efficiency Ratio over a small window of bar closes
# straddling the candidate's own formation bar. ER = |net change| / (sum of
# |close-to-close| changes) -- near 1.0 for an uninterrupted directional
# push (no give-back), near 0.0 for a choppy, range-bound consolidation.
# BEFORE/AFTER counts are bars strictly before/after formation (the
# formation bar's own close is always included); AFTER is a maximum, not a
# requirement -- a candidate that breaks out before AFTER bars have elapsed
# is scored on whatever closes it actually has.
ER_BARS_BEFORE = 2
ER_BARS_AFTER = 2
ER_CONSOLIDATION_MAX = 0.5  # ER below this counts as "consolidating enough"


def load_ohlc_data(csv_path: str) -> pd.DataFrame:
    """Load OHLC data from CSV (epoch-seconds `time` column); return a
    DataFrame indexed by datetime, sorted, with open/high/low/close columns."""
    ohlc = pd.read_csv(csv_path, usecols=["time", "open", "high", "low", "close"])
    ohlc["time"] = pd.to_datetime(ohlc["time"], unit="s")
    ohlc = ohlc.set_index("time").sort_index()
    return ohlc


# The repo's one hammer / shooting-star definition: patterns-pure's own
# functions, loaded by FILE PATH from the vendored copy so no sys.path order
# can substitute another copy. Both need the previous bar as well as the bar
# itself -- body <= 35% of range, long wick > 50%, opposite wick <= 25%, AND a
# hammer closes at/above the previous bar's low (a shooting star at/below its
# high). Never restate those thresholds anywhere; refresh the vendored copy.
#
# PAIRING -- this detector's own, and deliberate: an LHPB (the bar's HIGH) is a
# spike if its bar is a HAMMER, an LLPB (the bar's LOW) if it is a SHOOTING
# STAR. The labels / cluster-selection / spike-atr reports use the opposite
# "rejection" pairing (LHPB = shooting star). Both are intended; never swap
# either one without the user deciding, since this pairing drives the
# candidate gate and so every ledger. See "Spike candles" in lxpb-v2/CLAUDE.md.
PATTERNS_PURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "patterns_pure")   # vendored copy: lxpb-v2/levels_runtime/vendor/ -> lxpb-v2/patterns_pure
SPIKE_PATTERN_FILES = tuple(os.path.join(PATTERNS_PURE_DIR, f)
                            for f in ("find_hammer.py", "find_shooting_star.py"))


def _load_pattern(path, name):
    spec = importlib.util.spec_from_file_location(f"_lxpb_pp_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


find_hammer = _load_pattern(SPIKE_PATTERN_FILES[0], "find_hammer")
find_shooting_star = _load_pattern(SPIKE_PATTERN_FILES[1], "find_shooting_star")


def iter_bars(state: dict, ohlc: pd.DataFrame):
    """`ohlc`'s bars in the form advance_one_bar takes (itertuples rows), each
    carrying patterns-pure's verdict for that bar: `pp_hammer`,
    `pp_shooting_star`.

    Computed over the whole frame in one pass (per-bar DataFrame calls would
    be far too slow on M5). The first bar is judged against the last bar
    `state` has already processed, so a resumed state (lxpb_levels_cache's
    extend route) gets the same verdicts as a from-scratch run; a fresh
    state has no previous bar, so its first bar is never a spike -- exactly
    patterns-pure's own shift(1) on a series' first row."""
    frame = ohlc[["open", "high", "low", "close"]]
    if frame.empty:
        return iter(())
    resumed = state["prev_high"] is not None
    if resumed:
        # Only the previous bar's high/low enter the close check; its own
        # verdict (NaN open/close -> never a match) is dropped below.
        prior = pd.DataFrame({"open": [np.nan], "high": [state["prev_high"]],
                              "low": [state["prev_low"]], "close": [np.nan]},
                             index=frame.index[:1] - pd.Timedelta(1, "ns"))
        frame = pd.concat([prior, frame])
    hammer = frame.index.isin(find_hammer(frame, atr=0.0).index)
    star = frame.index.isin(find_shooting_star(frame, atr=0.0).index)
    if resumed:
        hammer, star = hammer[1:], star[1:]
    return ohlc.assign(pp_hammer=hammer, pp_shooting_star=star).itertuples(index=True)


def _efficiency_ratio(closes):
    """Kaufman's Efficiency Ratio over a sequence of closes: |net change| /
    (sum of |close-to-close| changes). 1.0 = a straight, uninterrupted
    move (trend); 0.0 = perfectly round-tripped (maximal chop). Returns
    None if there are fewer than 2 closes to compare (not enough data to
    say anything -- callers should treat that as "don't know", not as
    evidence of either a trend or a consolidation)."""
    if len(closes) < 2:
        return None
    diffs = [abs(closes[i + 1] - closes[i]) for i in range(len(closes) - 1)]
    total = sum(diffs)
    if total == 0:
        return 0.0  # perfectly flat closes -- maximally consolidating
    return abs(closes[-1] - closes[0]) / total


def new_state() -> dict:
    """Empty LXPB state — three lists of plain-typed dicts.

    `touch_lv1` entries carry a `running_fta` float that is updated
    incrementally each bar so retest emission needs no history slice.
    `running_fta` is stripped from the public touch_lv1 / retests output.

    `pending_swing` holds the (at most two) levels formed on the bar
    immediately prior to the current one. Swing classification needs a
    look-ahead bar (the bar *after* formation), which in a streaming
    per-bar loop only becomes available on the very next call — so
    `is_swing` starts as `None` and is finalized in Phase 0 of the next
    `advance_one_bar` call, before that level can possibly break out.
    `prev_high`/`prev_low` cache the bar before the current one so a
    newly formed level always has its look-back side on hand too.

    `recent_closes` holds the last ER_BARS_BEFORE closes from bars
    STRICTLY BEFORE the one currently being processed (updated at the end
    of every `advance_one_bar` call) -- the rolling window a newly formed
    level snapshots from, plus its own formation bar's close, to seed its
    `er_closes` (see Phase 1's own comment for why this lives on the
    level rather than being recomputed later from scratch).

    `pending_er` holds every level whose `er_closes` hasn't yet reached
    ER_BARS_BEFORE+1+ER_BARS_AFTER entries -- i.e. every level still
    accumulating its own efficiency-ratio window. A level drops out (by
    reaching the cap, or by breaking out) within a few bars of formation
    regardless of how long it then sits in touch_lv0 waiting to break, so
    this stays small even over a huge bar history where touch_lv0 itself
    does not -- see the loop that walks it in Phase 2's own comment for
    why that distinction matters.

    `bar_count` increments once per `advance_one_bar` call, independent of
    the bars' own timestamps -- a level's own breakout stamps the count at
    that moment (`breakout_bar_count`, Phase 2), so Phase 3 can require
    "at least one full bar between breakout and retest" by comparing two
    small integers instead of a clock-time gap that would mean a different
    number of bars on every timeframe (see MIN_BARS_BEFORE_RETEST).
    """
    return {
        "touch_lv0": [],
        "touch_lv1": [],
        "retests": [],
        "pending_swing": [],
        "pending_er": [],
        "recent_closes": [],
        "prev_high": None,
        "prev_low": None,
        "bar_count": 0,
    }


def advance_one_bar(state: dict, bar) -> tuple:
    """Advance LXPB state by one OHLC bar -- a row from iter_bars(state, ohlc),
    which carries patterns-pure's hammer/shooting-star verdict for it.

    Returns (finalized_swing, gated_dropped):
      finalized_swing -- the levels whose `is_swing` was just finalized
        this call (state['pending_swing'] as it stood before Phase 0
        cleared it). An external observer that snapshots a level's fields
        the moment it first sees it (e.g. lxpb_levels_cache.py) captures
        is_swing while it is still the pending `None`, one bar before the
        real value lands here -- callers that care about the correct
        value need to know exactly when it becomes final so they can
        patch whatever they already recorded.
      gated_dropped -- levels that broke out this bar (Phase 2) but were
        NOT promoted to touch_lv1 because they failed the candidate
        gate (see Phase 2 below): price closed through them, but they
        weren't a real pre-breakout extreme, so they're not tracked
        further. An external observer needs this too, to distinguish
        "broke but not a real candidate" from "never broke at all"
        (FATE_DISCARDED_NO_CLOSE) -- both look identical from outside
        (level leaves touch_lv0, never appears in touch_lv1) without it.

    Order: phase 0 (finalize pending swing) → phase 3 (retest) → phase 2
    (breakout) → phase 1 (register) → phase 4 (running-FTA update). Phase
    4 updates running_fta only for levels whose FTA window strictly
    contains this bar (breakout_time < bar.Index), which excludes the
    just-broken-out level from being counted against its own FTA.

    Gap handling: both breakout (phase 2) and retest (phase 3) treat a
    bar whose range is entirely on the "after" side of an LxPB price as
    consuming the level — i.e. an LHPB gets broken out by a bar that
    opens above and stays above (gap-up), and gets retested by a bar
    that gaps clean down past it. The bar never traded at the level,
    but price has moved past it, so the level transitions state. For
    retests under gap-over, the recorded `retest_*` OHLC reflects the
    actual gap bar; `entry_price` is the level (synthetic — no bar
    actually wicked there). Consumers can detect the synthetic case
    via `not (retest_low <= entry_price <= retest_high)`.

    Retest is direction-agnostic: any bar whose range overlaps the level
    (or gaps past it) qualifies once MORE THAN MIN_BARS_BEFORE_RETEST bars
    have elapsed since the breakout bar (strict >, compared as integer bar
    counts via `bar_count`/`breakout_bar_count`, not clock time -- so the
    breakout bar's immediate next bar can never itself be the retest — at
    least one full bar sits in between, on ANY timeframe this runs over) —
    no open/close directional condition is required (unlike breakout,
    which requires a body cross).

    Spike/swing classification: `is_spike` (hammer for LHPB, shooting
    star for LLPB -- this detector's deliberate pairing, the opposite of
    the labels reports'; both patterns-pure's, see iter_bars) only needs the
    formation bar and the bar before it, so it is finalized immediately
    at formation. `is_swing` needs the bar *after* formation, so it is
    finalized here in Phase 0 — one bar after the level was created,
    always before that same level's earliest possible breakout (Phase
    2 of this same call).

    Candidate gate (Phase 2): a level only gets promoted to touch_lv1
    (tracked as a live breakout awaiting retest) if:
      `is_spike` (its own formation candle was a hammer/shooting star --
        a self-contained reversal that doesn't need the NEXT bar's
        confirmation, since a lower-timeframe view of that one candle is
        itself a low-high-low / high-low-high pattern), OR
      `is_swing` (a genuine local extreme against its immediate neighbor
        bars) AND its own neighborhood is CONSOLIDATING rather than
        trending -- `_efficiency_ratio(lv["er_closes"])` below
        ER_CONSOLIDATION_MAX (see that function and the module-level
        constants' own comments). is_swing alone is not enough: a razor-
        thin 1-bar dip inside an otherwise clean, one-directional run can
        satisfy the immediate-neighbor test purely by chance (its low
        beats the bars on either side by a few ticks) while the wider
        few bars around it show no real two-sided rejection at all --
        the efficiency ratio catches that by scoring the actual
        closes, not just endpoints.
    A level that breaks out satisfying neither is not a real pre-breakout
    extreme -- e.g. one bar in the middle of the same directional move
    that produces the breakout, or one bar still inside the immediately
    preceding opposite move -- so it is silently dropped rather than
    tracked as a P0. Each of these is an independent, additive check,
    not a ranking that picks a single "best" candidate among several
    that still pass -- more may be added the same way later.
    """
    state["bar_count"] += 1

    # Phase 0: finalize is_swing for levels formed on the previous bar,
    # using this bar as the look-ahead ("next") bar. Keep the pre-clear
    # list so the caller can patch anything it already snapshotted with
    # the old (pending) is_swing value.
    finalized_swing = state["pending_swing"]
    for lv in finalized_swing:
        if lv["type"] == "LHPB":
            lv["is_swing"] = lv["price"] >= lv.pop("_prev_high") and lv["price"] >= bar.high
            del lv["_prev_low"]
        else:  # LLPB
            lv["is_swing"] = lv["price"] <= lv.pop("_prev_low") and lv["price"] <= bar.low
            del lv["_prev_high"]
    state["pending_swing"] = []

    # Phase 3: check one-touch levels for retests (touched OR gapped over)
    keep = []
    for lv in state["touch_lv1"]:
        price = lv["price"]
        touched = bar.low <= price <= bar.high
        if lv["type"] == "LHPB":
            # LHPB retests come from above — a bar entirely below the
            # level has gapped past it.
            gap_over = bar.high < price
        else:  # LLPB
            # LLPB retests come from below — a bar entirely above the
            # level has gapped past it.
            gap_over = bar.low > price
        if touched or gap_over:
            # Strict ">" (not ">="): the immediately-next bar after the
            # breakout bar (bar_count elapsed == 1) can never itself be the
            # retest -- at least MIN_BARS_BEFORE_RETEST full bars must sit
            # in between breakout and retest bar (elapsed must exceed it).
            # Compared as integer bar counts, not clock time, so this means
            # the same thing -- "at least one full bar in between" -- on
            # every timeframe this runs over.
            if (state["bar_count"] - lv["breakout_bar_count"]) > MIN_BARS_BEFORE_RETEST:
                fta       = lv["running_fta"]
                stop_loss = lv["breakout_low"] if lv["type"] == "LHPB" else lv["breakout_high"]
                public = {k: v for k, v in lv.items()
                         if k not in ("running_fta", "breakout_bar_count")}
                state["retests"].append({
                    **public,
                    "retest_time":  bar.Index,
                    "retest_open":  bar.open,
                    "retest_high":  bar.high,
                    "retest_low":   bar.low,
                    "retest_close": bar.close,
                    "entry_price":  price,
                    "fta":          fta,
                    "stop_loss":    stop_loss,
                })
            # else: touched/gap-over but MIN_HOURS not elapsed — silently
            # consume so the level doesn't re-fire on a later bar.
        else:
            keep.append(lv)
    state["touch_lv1"] = keep

    # Phase 2: check zero-touch levels for breakouts. Unified rule:
    #   bar entirely beyond level on after-side → gap breakout
    #   bar range contains level + close on after-side → standard breakout
    #   bar range contains level, close didn't pass, but the touch was an
    #     EXACT tag (bar's own high/low == price, not an overshoot) → not a
    #     real failed-breakout attempt (inconclusive), keep pending
    #   bar range contains level, close didn't pass, and the bar clearly
    #     overshot the level (high > price for LHPB / low < price for LLPB)
    #     then rejected → genuine failed breakout, discard
    #   bar entirely on before-side → no interaction, keep
    keep = []
    gated_dropped = []
    broke_out_ids = set()
    for lv in state["touch_lv0"]:
        price = lv["price"]
        if lv["type"] == "LHPB":
            if bar.high < price:
                # Entirely below — no interaction.
                keep.append(lv)
                continue
            if bar.low > price:
                # Gap-up — bar entirely above level. close > price is
                # guaranteed (close >= low > price).
                broke = True
            else:
                # Range contains the level.
                broke = bar.close > price
                if not broke and bar.high == price:
                    # Exact tag (no overshoot) — inconclusive, not a real
                    # failed breakout. Stays pending.
                    keep.append(lv)
                    continue
        else:  # LLPB
            if bar.low > price:
                keep.append(lv)
                continue
            if bar.high < price:
                # Gap-down — bar entirely below level.
                broke = True
            else:
                broke = bar.close < price
                if not broke and bar.low == price:
                    keep.append(lv)
                    continue
        if broke:
            broke_out_ids.add(id(lv))
            er = _efficiency_ratio(lv.get("er_closes", ()))
            consolidating = er is None or er < ER_CONSOLIDATION_MAX
            if lv["is_spike"] or (lv["is_swing"] and consolidating):
                state["touch_lv1"].append({
                    **lv,
                    "breakout_time":  bar.Index,
                    "breakout_bar_count": state["bar_count"],
                    "breakout_open":  bar.open,
                    "breakout_high":  bar.high,
                    "breakout_low":   bar.low,
                    "breakout_close": bar.close,
                    "running_fta":    np.nan,
                    "er_score":       er,
                })
            else:
                # Candidate gate failed: price closed through it, but it
                # was never a real pre-breakout extreme. Not tracked
                # further -- see this function's own docstring. Still
                # stamped with breakout_* (same shape as a touch_lv1
                # entry) so a caller can record WHAT it broke against
                # even though it's not being tracked for a retest.
                gated_dropped.append({
                    **lv,
                    "breakout_time":  bar.Index,
                    "breakout_open":  bar.open,
                    "breakout_high":  bar.high,
                    "breakout_low":   bar.low,
                    "breakout_close": bar.close,
                    "er_score":       er,
                })
        # else: bar range contained the level but close didn't pass — discard
    state["touch_lv0"] = keep

    # Every level still growing its own er_closes window gets this bar's
    # close appended, up to the AFTER cap -- the candidate gate's
    # efficiency-ratio window (see this function's own docstring) grows
    # forward exactly as far as the level survives, stopping once it has
    # ER_BARS_AFTER post-formation closes. Walks `state["pending_er"]` (a
    # short list of only the still-growing levels), NOT the full
    # touch_lv0 -- touch_lv0 can hold thousands of long-dormant levels on
    # a big M5 history, and nearly all of them stopped needing appends
    # within a couple of bars of formation, so scanning all of touch_lv0
    # here every single bar would cost O(bars * |touch_lv0|) for work
    # that only ever touches a handful of recently-formed entries.
    # broke_out_ids excludes levels that left touch_lv0 THIS bar (Phase 2,
    # above) -- their own er_closes was already read at the exact gate
    # check above and must not keep growing after the fact (this list and
    # the new touch_lv1/gated_dropped dicts share the same list object via
    # the `**lv` spread, so appending here would silently corrupt an
    # already-decided, already-recorded entry).
    er_cap = ER_BARS_BEFORE + 1 + ER_BARS_AFTER
    still_pending = []
    for lv in state["pending_er"]:
        if id(lv) in broke_out_ids:
            continue
        lv["er_closes"].append(bar.close)
        if len(lv["er_closes"]) < er_cap:
            still_pending.append(lv)
    state["pending_er"] = still_pending

    # Phase 1: register this bar's high/low as new zero-touch levels.
    # is_spike is patterns-pure's verdict, already on the bar (iter_bars)
    # and final now. is_swing needs the
    # bar after formation, so it starts as None (pending) unless this is
    # the very first bar (no look-back bar exists → not a swing, per the
    # original is_swing_high/is_swing_low boundary behavior). er_closes
    # seeds from the rolling recent_closes buffer plus this bar's own
    # close -- ER_BARS_BEFORE prior closes and the formation bar's own,
    # ready for the loop above to extend forward on later bars.
    try:
        is_hammer, is_shooting_star = bar.pp_hammer, bar.pp_shooting_star
    except AttributeError:
        raise TypeError("advance_one_bar needs bars from lxpb.iter_bars(state, ohlc), "
                        "which carry patterns-pure's hammer/shooting-star verdict") from None
    is_first_bar = state["prev_high"] is None
    er_seed = state["recent_closes"] + [bar.close]
    # Detector pairing (see the PAIRING note above iter_bars): LHPB = hammer,
    # LLPB = shooting star. Deliberately NOT the labels reports' pairing.
    lhpb = {
        "type": "LHPB", "price": bar.high, "formation_time": bar.Index,
        "is_spike": is_hammer,
        "is_swing": False if is_first_bar else None,
        "er_closes": list(er_seed),
    }
    llpb = {
        "type": "LLPB", "price": bar.low, "formation_time": bar.Index,
        "is_spike": is_shooting_star,
        "is_swing": False if is_first_bar else None,
        "er_closes": list(er_seed),
    }
    if not is_first_bar:
        lhpb["_prev_high"] = state["prev_high"]
        lhpb["_prev_low"]  = state["prev_low"]
        llpb["_prev_high"] = state["prev_high"]
        llpb["_prev_low"]  = state["prev_low"]
        state["pending_swing"].append(lhpb)
        state["pending_swing"].append(llpb)
    state["touch_lv0"].append(lhpb)
    state["touch_lv0"].append(llpb)
    if len(er_seed) < er_cap:
        state["pending_er"].append(lhpb)
        state["pending_er"].append(llpb)
    state["prev_high"], state["prev_low"] = bar.high, bar.low
    state["recent_closes"] = (state["recent_closes"] + [bar.close])[-ER_BARS_BEFORE:]

    # Phase 4: update running_fta for levels whose FTA window contains this bar.
    # FTA window is (breakout_time, retest_time) — exclusive both ends. Excluding
    # the breakout bar means `breakout_time < bar.Index`; the retest bar is
    # excluded automatically because retested levels have already been moved out
    # of touch_lv1 in phase 3.
    # Pure-Python min/max with explicit NaN handling (NaN is the only float that
    # is unequal to itself) is ~10× faster than np.fmin/fmax on scalars here.
    b_idx, b_low, b_high = bar.Index, bar.low, bar.high
    for lv in state["touch_lv1"]:
        if lv["breakout_time"] < b_idx:
            cur = lv["running_fta"]
            if lv["type"] == "LHPB":
                lv["running_fta"] = b_low if cur != cur or b_low < cur else cur
            else:  # LLPB
                lv["running_fta"] = b_high if cur != cur or b_high > cur else cur

    return finalized_swing, gated_dropped


_INTERNAL_KEYS = {"running_fta", "_prev_high", "_prev_low", "er_closes", "breakout_bar_count"}


def _strip_internal(rows: list) -> list:
    return [{k: v for k, v in r.items() if k not in _INTERNAL_KEYS} for r in rows]


def detect_lxpb_h1(ohlc_h1: pd.DataFrame):
    """
    Run the 3-phase (+ swing pre-pass) LXPB state machine over H1 bars.

    Returns
    -------
    touch_lv0 : zero-touch levels  (formed, not yet broken out)
    touch_lv1 : one-touch levels   (broken out, awaiting retest)
    retests   : completed retests  (one-touch level returned to after MIN_BARS_BEFORE_RETEST)
    """
    state = new_state()
    for bar in iter_bars(state, ohlc_h1):
        advance_one_bar(state, bar)

    return (
        pd.DataFrame(_strip_internal(state["touch_lv0"])),
        pd.DataFrame(_strip_internal(state["touch_lv1"])),
        pd.DataFrame(state["retests"]),
    )


# Backward-compatible alias for the old repo API name.
lxpb_analysis = detect_lxpb_h1


def print_levels(label: str, df: pd.DataFrame, cols: list):
    print(f"\n{'─'*70}")
    print(f"  {label}  ({len(df)} rows)")
    print('─'*70)
    if df.empty:
        print("  (none)")
    else:
        print(df[cols].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect LXPB retests on H1 data")
    parser.add_argument("--data", required=True, help="Path to H1 OHLC CSV (time,open,high,low,close)")
    parser.add_argument("--output", default=None, help="Optional CSV output path for retests")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"Error: {args.data} not found.")
        sys.exit(1)

    print(f"Loading {args.data} ...")
    df = load_ohlc_data(args.data)
    print(f"  {len(df)} H1 bars  ({df.index.min()} -> {df.index.max()})")

    touch_lv0, touch_lv1, retests = detect_lxpb_h1(df)

    print_levels(
        "ZERO-TOUCH levels  (formed, not yet broken out)",
        touch_lv0,
        ["type", "formation_time", "price", "is_spike", "is_swing"],
    )
    print_levels(
        "ONE-TOUCH levels  (broken out, awaiting retest)",
        touch_lv1,
        ["type", "formation_time", "price", "is_spike", "is_swing",
         "breakout_time", "breakout_open", "breakout_close"],
    )
    print_levels(
        "RETESTS  (completed)",
        retests,
        ["type", "formation_time", "price", "is_spike", "is_swing", "breakout_time", "retest_time",
         "entry_price", "fta", "stop_loss"],
    )

    if args.output:
        retests.to_csv(args.output, index=False, float_format="%.2f")
        print(f"\nRetests saved to {args.output}")
