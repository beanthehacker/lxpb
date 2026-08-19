"""
Diagnostic: how much does the base LXPB algorithm's "consume on any touch"
rule (see ../README.md: "Level consumption on any touch is intentional...
this prevents the same level from firing multiple times") suppress
legitimate stacked-confluence signal for our absorption strategy?

Builds a full HISTORY of every touch_lv1 (broken-out) level ever created,
independent of whether/when it was later silently consumed by a
non-qualifying touch (< MIN_HOURS_BEFORE_RETEST) or a qualifying retest.
Then re-scores every trigger in lxpb_volume_strat_triggers.csv against
this "ever existed nearby" definition and compares against the strict
"currently active only" definition already in that CSV.
"""
import os
import sys
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import lxpb as L  # noqa: E402  -- ../lxpb.py: canonical H1 LXPB detector
from lxpb import load_ohlc_data  # noqa: E402

TICK = 0.25
N_TICKS = 20
STACK_THRESHOLD = 2
L.MIN_HOURS_BEFORE_RETEST = 2

# Ported from daily-analysis (was: r"D:\daily-analysis\data\ES1!-H1.csv").
H1_CSV = os.path.join(_HERE, "..", "data", "es-h1-4apr2021-11apr2025.csv")
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers.csv")


def _key(lv):
    return (lv["type"], lv["price"], lv["formation_time"])


def build_history(h1_csv_path):
    """Run the state machine bar-by-bar, logging every touch_lv1 level ever
    created (history[key] = {..., consumed_at, consumed_reason}) regardless
    of later silent consumption. Since the history-aware confluence model
    only cares about formation_time <= query_time (consumption is ignored
    by definition), no per-bar snapshot is needed -- just the flat log."""
    df = load_ohlc_data(h1_csv_path)
    state = L.new_state()
    history = {}          # key -> dict with formation/breakout + consumed_* fields

    prev_lv1_keys = set()
    for bar in df.itertuples(index=True):
        L.advance_one_bar(state, bar)

        cur_lv1_keys = {_key(lv) for lv in state["touch_lv1"]}
        # newly created touch_lv1 entries this bar -> add to history
        for lv in state["touch_lv1"]:
            k = _key(lv)
            if k not in history:
                history[k] = {**lv, "consumed_at": None, "consumed_reason": None}
        # entries that disappeared this bar -> mark consumed (informational only)
        disappeared = prev_lv1_keys - cur_lv1_keys
        for k in disappeared:
            if k in history and history[k]["consumed_at"] is None:
                history[k]["consumed_at"] = bar.Index
                elapsed_h = (bar.Index - history[k]["breakout_time"]).total_seconds() / 3600.0
                touched = bar.low <= history[k]["price"] <= bar.high
                history[k]["consumed_reason"] = (
                    "valid_retest" if (touched and elapsed_h >= L.MIN_HOURS_BEFORE_RETEST)
                    else "silent_consume"
                )
        prev_lv1_keys = cur_lv1_keys

    return history


def history_confluence_at(history, ts_utc_naive, level_type, price,
                           n_ticks=N_TICKS, threshold=STACK_THRESHOLD):
    """Count same-type levels within n_ticks of price among ALL levels ever
    formed (breakout_time <= ts_utc_naive) regardless of later consumption."""
    tol = n_ticks * TICK
    hits = [lv for lv in history.values()
            if lv["type"] == level_type and abs(lv["price"] - price) <= tol
            and lv["breakout_time"] <= ts_utc_naive]
    return len(hits) >= threshold, len(hits), hits


def main():
    print("Building full touch_lv1 HISTORY (2h retest rule) ...")
    history = build_history(H1_CSV)
    print(f"Total distinct LHPB/LLPB levels ever broken out: {len(history)}")
    n_silent = sum(1 for v in history.values() if v["consumed_reason"] == "silent_consume")
    n_valid = sum(1 for v in history.values() if v["consumed_reason"] == "valid_retest")
    n_alive = sum(1 for v in history.values() if v["consumed_at"] is None)
    print(f"  consumed by silent touch (<2h, discarded): {n_silent}")
    print(f"  consumed by valid retest (>=2h):            {n_valid}")
    print(f"  still alive at end of data:                 {n_alive}")

    trig = pd.read_csv(TRIGGERS_CSV, parse_dates=["time_pt"])
    new_rows = []
    for _, row in trig.iterrows():
        ts_utc_naive = row["time_pt"].tz_convert("UTC").tz_localize(None)
        passes, count, levels = history_confluence_at(
            history, ts_utc_naive, row["level_type"], row["tested_price"])
        new_rows.append({
            **row.to_dict(),
            "hist_confluence_count": count,
            "hist_confluence_prices": ";".join(f"{lv['price']:.2f}" for lv in sorted(levels, key=lambda x: x['price'])),
            "hist_true_positive": bool(passes),
        })
    out = pd.DataFrame(new_rows)
    out.to_csv(os.path.join(_HERE, "lxpb_volume_strat_triggers_history_compare.csv"), index=False)

    flips = out[(~out["true_positive"]) & (out["hist_true_positive"])]
    print(f"\nStrict (currently-active) true positives: {int(out['true_positive'].sum())}")
    print(f"History-aware (ever-existed) true positives: {int(out['hist_true_positive'].sum())}")
    print(f"Triggers that FLIP false->true under history-aware confluence: {len(flips)}")
    cols = ["time_pt", "direction", "tested_price", "level_type",
            "confluence_count", "confluence_prices",
            "hist_confluence_count", "hist_confluence_prices"]
    print(flips[cols].to_string())


if __name__ == "__main__":
    main()
