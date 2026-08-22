"""
LXPB fade/mean-reversion research -- STEP 1: structural (H1) feature build.

Runs ../lxpb.py's canonical `detect_lxpb_h1` over the FULL back-adjusted H1
history (2015-present), then for every completed retest whose retest_time
falls inside the window covered by ../lxpb-es-vol/ES_full_1s.csv
(2026-05-28 .. 2026-08-17, the only span with real scid-derived 1s
bid/ask/trade data) computes structural/historical features that do not
need 1s data:

  - is_spike, is_swing                (already in lxpb.py's output)
  - hours_breakout_to_retest          (dwell time broken-out, awaiting retest)
  - level_age_hours                   (formation -> retest)
  - breakout_range_ratio              (breakout bar range vs trailing 20-bar avg range)
  - breakout_body_ratio               (|close-open| / range of breakout bar)
  - prior_touches_2yr / prior_touches_all
                                       ("inflection point from the past" --
                                       how many H1 bars in the lookback
                                       window before formation already
                                       traded within N ticks of this exact
                                       price, i.e. was this price zone
                                       already an important pivot?)
  - confluence_count                  (other same-type levels still
                                       broken-out/awaiting-retest, within
                                       N ticks, at the moment of this retest --
                                       captured via a per-bar touch_lv1
                                       snapshot while replaying the state
                                       machine, since lxpb.py itself only
                                       exposes the FINAL touch_lv1 state)
  - is_gap_breakout / is_gap_retest   (repo-wide convention: gap instances
                                       have no real 1s touch to check
                                       order flow at, so are excluded
                                       downstream)

Direction convention (per user decision): CONTINUATION -- LHPB retest =
LONG (level flipped resistance->support), LLPB retest = SHORT (level
flipped support->resistance). This is the same convention already baked
into lxpb.py's own FTA/stop-loss columns.

Output: lxpb_fade_levels.csv (one row per in-window completed retest).
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _REPO_ROOT)
import lxpb as L  # noqa: E402

TICK = 0.25
CONFLUENCE_TICKS = 20
PRIOR_TOUCH_TICKS = 4
PRIOR_TOUCH_LOOKBACK_BARS = 24 * 365 * 2   # ~2 years of H1 bars
H1_CSV = os.path.join(_REPO_ROOT, "data", "es-h1-continuous-backadjusted.csv")
WINDOW_START = pd.Timestamp("2026-05-28 00:00:00")
WINDOW_END = pd.Timestamp("2026-08-17 21:00:00")   # end of ES_full_1s.csv coverage
OUT_CSV = os.path.join(_HERE, "lxpb_fade_levels.csv")


def run_with_snapshots(df):
    """Re-implements detect_lxpb_h1's loop but also captures, per bar, the
    list of (type, price) still in touch_lv1 immediately after that bar's
    full processing -- needed to compute confluence_count at any later
    retest without re-deriving lxpb.py's internal state."""
    state = L.new_state()
    snapshots = {}
    for bar in df.itertuples(index=True):
        L.advance_one_bar(state, bar)
        snapshots[bar.Index] = [(lv["type"], lv["price"]) for lv in state["touch_lv1"]]
    touch_lv0 = pd.DataFrame(L._strip_internal(state["touch_lv0"]))
    touch_lv1 = pd.DataFrame(L._strip_internal(state["touch_lv1"]))
    retests = pd.DataFrame(state["retests"])
    return touch_lv0, touch_lv1, retests, snapshots


def confluence_count(snapshots, retest_time, lv_type, lv_price, n_ticks=CONFLUENCE_TICKS):
    others = snapshots.get(retest_time, [])
    tol = n_ticks * TICK
    return sum(1 for t, p in others if t == lv_type and abs(p - lv_price) <= tol and p != lv_price)


def prior_touches(highs, lows, idx_by_time, formation_time, price, lookback_bars, n_ticks=PRIOR_TOUCH_TICKS):
    """Count H1 bars strictly BEFORE formation_time, within lookback_bars,
    whose range came within n_ticks of `price` -- i.e. how many times
    price already reacted to/traded through this exact level before it
    was even registered as an LXPB level."""
    j = idx_by_time.get(formation_time)
    if j is None:
        return np.nan
    i0 = max(0, j - lookback_bars)
    if i0 >= j:
        return 0
    tol = n_ticks * TICK
    seg_low = lows[i0:j]
    seg_high = highs[i0:j]
    touched = (seg_low <= price + tol) & (seg_high >= price - tol)
    return int(touched.sum())


def main():
    print(f"Loading H1 data from {H1_CSV} ...")
    df = L.load_ohlc_data(H1_CSV)
    print(f"  {len(df)} bars, {df.index.min()} -> {df.index.max()}")

    print("Running LXPB state machine with per-bar touch_lv1 snapshots ...")
    touch_lv0, touch_lv1, retests, snapshots = run_with_snapshots(df)
    print(f"  total completed retests (all history): {len(retests)}")

    retests["retest_time"] = pd.to_datetime(retests["retest_time"])
    retests["formation_time"] = pd.to_datetime(retests["formation_time"])
    retests["breakout_time"] = pd.to_datetime(retests["breakout_time"])

    in_window = retests[(retests["retest_time"] >= WINDOW_START) & (retests["retest_time"] <= WINDOW_END)].copy()
    print(f"  in-window ({WINDOW_START.date()} .. {WINDOW_END.date()}): {len(in_window)}")

    # Gap flags (repo-wide convention -- see lxpb_retest_vol_scalp.py docstring)
    in_window["is_gap_breakout"] = np.where(
        in_window["type"] == "LHPB",
        in_window["breakout_low"] > in_window["price"],
        in_window["breakout_high"] < in_window["price"],
    )
    in_window["is_gap_retest"] = ~(
        (in_window["retest_low"] <= in_window["entry_price"]) & (in_window["entry_price"] <= in_window["retest_high"])
    )
    n_gap = (in_window["is_gap_breakout"] | in_window["is_gap_retest"]).sum()
    print(f"  gap instances (excluded downstream): {n_gap}")

    # Precompute arrays for prior-touch lookback and breakout-range-ratio.
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    opens = df["open"].to_numpy(float)
    closes = df["close"].to_numpy(float)
    idx_by_time = {t: i for i, t in enumerate(df.index)}
    ranges = highs - lows
    avg_range_20 = pd.Series(ranges).rolling(20).mean().to_numpy()

    rows = []
    for _, r in in_window.iterrows():
        lv_type, price = r["type"], r["price"]
        formation_time, breakout_time, retest_time = r["formation_time"], r["breakout_time"], r["retest_time"]

        hours_breakout_to_retest = (retest_time - breakout_time).total_seconds() / 3600.0
        level_age_hours = (retest_time - formation_time).total_seconds() / 3600.0

        j_breakout = idx_by_time.get(breakout_time)
        if j_breakout is not None and j_breakout > 0 and not np.isnan(avg_range_20[j_breakout]):
            breakout_range = ranges[j_breakout]
            breakout_range_ratio = breakout_range / avg_range_20[j_breakout] if avg_range_20[j_breakout] > 0 else np.nan
            breakout_body_ratio = abs(closes[j_breakout] - opens[j_breakout]) / breakout_range if breakout_range > 0 else np.nan
        else:
            breakout_range_ratio = np.nan
            breakout_body_ratio = np.nan

        pt_2yr = prior_touches(highs, lows, idx_by_time, formation_time, price, PRIOR_TOUCH_LOOKBACK_BARS)
        pt_all = prior_touches(highs, lows, idx_by_time, formation_time, price, len(df))

        conf = confluence_count(snapshots, retest_time, lv_type, price)

        rows.append({
            "type": lv_type, "price": price,
            "formation_time": formation_time, "breakout_time": breakout_time, "retest_time": retest_time,
            "is_spike": r["is_spike"], "is_swing": r["is_swing"],
            "entry_price": r["entry_price"], "fta": r["fta"], "stop_loss": r["stop_loss"],
            "breakout_open": r["breakout_open"], "breakout_high": r["breakout_high"],
            "breakout_low": r["breakout_low"], "breakout_close": r["breakout_close"],
            "retest_open": r["retest_open"], "retest_high": r["retest_high"],
            "retest_low": r["retest_low"], "retest_close": r["retest_close"],
            "is_gap_breakout": bool(r["is_gap_breakout"]), "is_gap_retest": bool(r["is_gap_retest"]),
            "hours_breakout_to_retest": hours_breakout_to_retest,
            "level_age_hours": level_age_hours,
            "breakout_range_ratio": breakout_range_ratio,
            "breakout_body_ratio": breakout_body_ratio,
            "prior_touches_2yr": pt_2yr,
            "prior_touches_all": pt_all,
            "confluence_count": conf,
            "direction": 1 if lv_type == "LHPB" else -1,   # continuation convention
        })

    out = pd.DataFrame(rows).sort_values("retest_time").reset_index(drop=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nSaved {len(out)} rows -> {OUT_CSV}")
    print(f"  non-gap rows: {(~(out['is_gap_breakout'] | out['is_gap_retest'])).sum()}")


if __name__ == "__main__":
    main()
