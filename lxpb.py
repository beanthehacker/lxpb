"""
LXPB H1 Detection
-----------------
Detects LHPB (Last High Pre-Breakout) and LLPB (Last Low Pre-Breakout) retests
on H1 OHLC data and outputs each completed retest with its entry price, FTA
target, stop loss, and spike/swing classification.

This is the canonical LXPB detection algorithm, ported from
D:\\daily-analysis\\lxpb-h1-apr2026\\lxpb_h1_detect.py (kept as the source of
truth there; this file is a synced copy for use in this repo/its tests).

Usage:
    python lxpb.py --data data/es-h1-continuous-backadjusted.csv
    python lxpb.py --data data/nq-h1-4apr2021-11apr2025.csv --output retests.csv
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd


MIN_HOURS_BEFORE_RETEST = 4


def load_ohlc_data(csv_path: str) -> pd.DataFrame:
    """Load OHLC data from CSV (epoch-seconds `time` column); return a
    DataFrame indexed by datetime, sorted, with open/high/low/close columns."""
    ohlc = pd.read_csv(csv_path, usecols=["time", "open", "high", "low", "close"])
    ohlc["time"] = pd.to_datetime(ohlc["time"], unit="s")
    ohlc = ohlc.set_index("time").sort_index()
    return ohlc


def is_shootingstar(o: float, h: float, l: float, c: float) -> bool:
    """Single-bar shooting-star pattern (small body, long upper wick, tiny lower wick)."""
    body_size = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_length = h - l
    if total_length == 0:
        return False
    body_to_wick_ratio = 0.3
    small_body = body_size <= (total_length * body_to_wick_ratio)
    long_upper_wick = upper_wick > (total_length * 0.5)
    small_lower_wick = lower_wick < (total_length * 0.2)
    return small_body and long_upper_wick and small_lower_wick


def is_hammer(o: float, h: float, l: float, c: float) -> bool:
    """Single-bar hammer pattern (small body, long lower wick, tiny upper wick)."""
    body_size = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_length = h - l
    if total_length == 0:
        return False
    body_to_wick_ratio = 0.3
    small_body = body_size <= (total_length * body_to_wick_ratio)
    long_lower_wick = lower_wick > (total_length * 0.5)
    small_upper_wick = upper_wick < (total_length * 0.2)
    return small_body and long_lower_wick and small_upper_wick


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
    """
    return {
        "touch_lv0": [],
        "touch_lv1": [],
        "retests": [],
        "pending_swing": [],
        "prev_high": None,
        "prev_low": None,
    }


def advance_one_bar(state: dict, bar) -> None:
    """Advance LXPB state by one OHLC bar (namedtuple from itertuples(index=True)).

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
    (or gaps past it) qualifies once MIN_HOURS_BEFORE_RETEST has
    elapsed — no open/close directional condition is required (unlike
    breakout, which requires a body cross).

    Spike/swing classification: `is_spike` (hammer for LHPB, shooting
    star for LLPB) is single-bar and finalized immediately at
    formation. `is_swing` needs the bar *after* formation, so it is
    finalized here in Phase 0 — one bar after the level was created,
    always before that same level's earliest possible breakout (Phase
    2 of this same call).
    """
    # Phase 0: finalize is_swing for levels formed on the previous bar,
    # using this bar as the look-ahead ("next") bar.
    for lv in state["pending_swing"]:
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
            if (bar.Index - lv["breakout_time"]) >= pd.Timedelta(hours=MIN_HOURS_BEFORE_RETEST):
                fta       = lv["running_fta"]
                stop_loss = lv["breakout_low"] if lv["type"] == "LHPB" else lv["breakout_high"]
                public = {k: v for k, v in lv.items() if k != "running_fta"}
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
    #   bar range contains level but close didn't pass → discard
    #   bar entirely on before-side → no interaction, keep
    keep = []
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
        else:  # LLPB
            if bar.low > price:
                keep.append(lv)
                continue
            if bar.high < price:
                # Gap-down — bar entirely below level.
                broke = True
            else:
                broke = bar.close < price
        if broke:
            state["touch_lv1"].append({
                **lv,
                "breakout_time":  bar.Index,
                "breakout_open":  bar.open,
                "breakout_high":  bar.high,
                "breakout_low":   bar.low,
                "breakout_close": bar.close,
                "running_fta":    np.nan,
            })
        # else: bar range contained the level but close didn't pass — discard
    state["touch_lv0"] = keep

    # Phase 1: register this bar's high/low as new zero-touch levels.
    # is_spike is a single-bar pattern, finalized now. is_swing needs the
    # bar after formation, so it starts as None (pending) unless this is
    # the very first bar (no look-back bar exists → not a swing, per the
    # original is_swing_high/is_swing_low boundary behavior).
    is_first_bar = state["prev_high"] is None
    lhpb = {
        "type": "LHPB", "price": bar.high, "formation_time": bar.Index,
        "is_spike": is_hammer(bar.open, bar.high, bar.low, bar.close),
        "is_swing": False if is_first_bar else None,
    }
    llpb = {
        "type": "LLPB", "price": bar.low, "formation_time": bar.Index,
        "is_spike": is_shootingstar(bar.open, bar.high, bar.low, bar.close),
        "is_swing": False if is_first_bar else None,
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
    state["prev_high"], state["prev_low"] = bar.high, bar.low

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


_INTERNAL_KEYS = {"running_fta", "_prev_high", "_prev_low"}


def _strip_internal(rows: list) -> list:
    return [{k: v for k, v in r.items() if k not in _INTERNAL_KEYS} for r in rows]


def detect_lxpb_h1(ohlc_h1: pd.DataFrame):
    """
    Run the 3-phase (+ swing pre-pass) LXPB state machine over H1 bars.

    Returns
    -------
    touch_lv0 : zero-touch levels  (formed, not yet broken out)
    touch_lv1 : one-touch levels   (broken out, awaiting retest)
    retests   : completed retests  (one-touch level returned to after MIN_HOURS)
    """
    state = new_state()
    for bar in ohlc_h1.itertuples(index=True):
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
