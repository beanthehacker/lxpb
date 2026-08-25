"""
Does the ES stop/target grid (analyze_breakout_exits.py's 72 strong-breakout
trades) perform better -- higher win rate, less negative MAE -- specifically
on the subset of trades where NQ ALSO retests one of its OWN nearby LXPB
levels at (roughly) the same time as the ES retest, and does the answer
change depending on how tightly "same time" is defined (same H1 bar, same
clock minute, same clock second)?

Coincidence definitions (progressively tighter):
  - h1 : NQ has its own completed detect_lxpb_h1 retest (any type/level) on
         the EXACT SAME H1 bar (`retest_time`) as the ES trade's retest bar.
         Computed straight from NQ's own H1 OHLC via lxpb.py's state
         machine -- same approach as the previous ES<->NQ correlation pass.
  - m1 : only evaluated for h1-coincident bars. Pinpoints, within that same
         hour, the actual 1-minute bar where (a) ES price first touches its
         own entry_price and (b) NQ price first touches ITS matched level's
         entry_price (real ticks from the local .scid files, resampled to
         1min -- ES from the existing repo contract-splicing helpers in
         render_labels_report.py, NQ from the single front-month
         F.US.ENQU26.scid file, which alone covers the whole
         2026-07-01..2026-08-24 window so no roll-splicing is needed).
         "Coincides at the m1 level" = the two touch-minutes are the same
         clock minute (|diff| <= 1 minute).
  - 1s : only evaluated for m1-coincident bars (else 1s ticks for a whole
         hour would be fetched for nothing). Escalates to real 1-second
         ticks for the touch-minute(s) on both sides and finds the exact
         first-touch second for each; "coincides at the 1s level" = the two
         touches are within 1 second of each other.

For each coincidence flag, split the 72 strong-breakout trades into
YES/NO groups and re-run the H1-bar-walk stop/target grid
(analyze_breakout_exits.stop_target_grid) separately on each group, for a
representative set of stop/target pairs (including the user-cited 2/12,
plus the grid's own top total_R combos) -- reporting win_rate, avg_R and
mean MAE per group so the two groups are directly comparable.

Caveats (small samples -- exploratory, not statistically powered):
  - Only 72 ES trades total; h1-coincident subset is ~17-30ish bars-worth,
    m1/1s-coincident subsets are necessarily smaller still.
  - "NQ's own nearby level" is whichever NQ retest row shares the ES trade's
    H1 bar; if NQ has >1 simultaneous retest on that bar, the one with the
    EARLIEST 1-min touch is used (most likely genuinely coincident one).
"""
import os
import sys
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, r"D:\acheron\AcheronUtils")
sys.path.insert(0, r"D:\lxpb")

import analyze_breakout_exits as A  # noqa: E402
import render_labels_report as R  # noqa: E402
import lxpb as L  # noqa: E402
from scidReader import get_scid_df  # noqa: E402

NQ_H1_PATH = os.path.join(_HERE, "data", "24aug-CME_MINI_NQ1!, 60.csv")
NQ_SCID_PATH = r"D:\SC\Data\F.US.ENQU26.scid"

STOPS_TARGETS = [(2, 12), (2, 6), (3, 12), (4, 12), (6, 12), (2, 8), (4, 16), (6, 20)]

_NQ_TICKS_CACHE = None


def _nq_ticks():
    """Whole ENQU26 scid file, loaded once, tz-converted to UTC. Single
    contract covers 2026-05-28..2026-08-25, i.e. the entire strong-breakout
    trade window (2026-07-01..2026-08-24) -- no roll-splicing needed."""
    global _NQ_TICKS_CACHE
    if _NQ_TICKS_CACHE is None:
        df = get_scid_df(NQ_SCID_PATH)
        df.index = df.index.tz_convert("UTC")
        _NQ_TICKS_CACHE = df
    return _NQ_TICKS_CACHE


def _nq_ticks_window(lo_utc, hi_utc):
    df = _nq_ticks()
    sl = df.loc[(df.index >= lo_utc) & (df.index < hi_utc)]
    return sl if not sl.empty else None


def touched(bar_low, bar_high, price, is_long_side_from_above):
    """Generic touch check (level between low/high, direction-agnostic)."""
    return bar_low <= price <= bar_high


def first_touch_minute(ticks, entry_price):
    """1-min-resampled OHLC from real ticks; returns (minute_timestamp,
    resampled_bars) of the first minute whose [low, high] range contains
    entry_price, or (None, bars) if never touched in this window."""
    bars = ticks.resample("1min").agg({"High": "max", "Low": "min"})
    hit = bars[(bars["Low"] <= entry_price) & (bars["High"] >= entry_price)]
    return (hit.index[0] if not hit.empty else None), bars


def first_touch_second(ticks_minute, entry_price):
    """Real 1s ticks already restricted to a single minute; first tick whose
    High/Low range contains entry_price."""
    hit = ticks_minute[(ticks_minute["Low"] <= entry_price) & (ticks_minute["High"] >= entry_price)]
    return hit.index[0] if not hit.empty else None


def build_coincidence_flags(trades, nq_h1_df, nq_retests):
    """Returns a DataFrame indexed like `trades` (one row per ES trade) with
    h1_coincide / m1_coincide / 1s_coincide bool columns + diagnostic time
    deltas."""
    nq_by_bar = {ts: g for ts, g in nq_retests.groupby("retest_time")}
    rows = []
    for t in trades:
        rt = pd.Timestamp(t["retest_time"])
        rt_utc = pd.Timestamp(rt, tz="UTC")
        row = {"retest_time": rt, "h1_coincide": False, "m1_coincide": False,
               "1s_coincide": False, "m1_diff_min": np.nan, "1s_diff_sec": np.nan}
        nq_rows = nq_by_bar.get(rt)
        if nq_rows is None or nq_rows.empty:
            rows.append(row)
            continue
        row["h1_coincide"] = True

        # ES precise touch minute (real ticks, same helper used elsewhere in repo)
        hour_end = rt_utc + pd.Timedelta(hours=1)
        es_ticks = R._ticks_for_window(rt_utc, hour_end)
        nq_ticks_hr = _nq_ticks_window(rt_utc, hour_end)
        if es_ticks is None or nq_ticks_hr is None:
            rows.append(row)
            continue
        offset, _sym = R._offset_for_ts(rt_utc)
        es_raw_entry = t["entry"] - offset
        es_minute, _ = first_touch_minute(es_ticks, es_raw_entry)
        if es_minute is None:
            rows.append(row)
            continue

        # try every simultaneous NQ retest candidate on this bar, keep the
        # one whose own touch-minute is CLOSEST to the ES touch-minute.
        best_diff_min, best_nq_minute, best_nq_price = None, None, None
        for _, nq_row in nq_rows.iterrows():
            nq_price = float(nq_row["entry_price"])
            nq_minute, _ = first_touch_minute(nq_ticks_hr, nq_price)
            if nq_minute is None:
                continue
            diff_min = abs((nq_minute - es_minute).total_seconds()) / 60.0
            if best_diff_min is None or diff_min < best_diff_min:
                best_diff_min, best_nq_minute, best_nq_price = diff_min, nq_minute, nq_price
        if best_diff_min is None:
            rows.append(row)
            continue
        row["m1_diff_min"] = best_diff_min
        row["m1_coincide"] = best_diff_min <= 1.0

        if row["m1_coincide"]:
            # escalate to 1s ticks for the touch minute(s) on each side
            es_min_ticks = es_ticks.loc[(es_ticks.index >= es_minute) & (es_ticks.index < es_minute + pd.Timedelta(minutes=1))]
            nq_min_ticks = nq_ticks_hr.loc[(nq_ticks_hr.index >= best_nq_minute) & (nq_ticks_hr.index < best_nq_minute + pd.Timedelta(minutes=1))]
            es_sec = first_touch_second(es_min_ticks, es_raw_entry)
            nq_sec = first_touch_second(nq_min_ticks, best_nq_price)
            if es_sec is not None and nq_sec is not None:
                diff_sec = abs((nq_sec - es_sec).total_seconds())
                row["1s_diff_sec"] = diff_sec
                row["1s_coincide"] = diff_sec <= 1.0
        rows.append(row)
    return pd.DataFrame(rows)


def grid_subset_report(trades, flags, flag_col, stops_targets):
    yes_idx = flags.index[flags[flag_col]].tolist()
    no_idx = flags.index[~flags[flag_col]].tolist()
    yes_trades = [trades[i] for i in yes_idx]
    no_trades = [trades[i] for i in no_idx]
    print(f"\n=== Split by `{flag_col}`: YES n={len(yes_trades)}  NO n={len(no_trades)} ===")
    if not yes_trades:
        print("  (no trades in YES group -- skipping)")
        return
    yes_mae, _, _ = A.mae_mfe_dd_stats(yes_trades)
    no_mae, _, _ = A.mae_mfe_dd_stats(no_trades) if no_trades else (pd.Series(dtype=float),) * 1
    print(f"  Mean MAE: YES={yes_mae.mean():.2f}  NO={(no_mae.mean() if len(no_mae) else float('nan')):.2f}")
    for stop, target in stops_targets:
        g_yes = A.stop_target_grid(yes_trades, [stop], [target]).iloc[0]
        line = (f"  stop={stop:>2} target={target:>3}  "
                f"YES: n={g_yes['n']:>2} win_rate={g_yes['win_rate']:.2f} avg_R={g_yes['avg_R']:>6.2f}")
        if no_trades:
            g_no = A.stop_target_grid(no_trades, [stop], [target]).iloc[0]
            line += (f"   |   NO: n={g_no['n']:>2} win_rate={g_no['win_rate']:.2f} avg_R={g_no['avg_R']:>6.2f}")
        print(line)


def main():
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= R.WIDE_BREAKOUT_RATIO_THRESHOLD)].reset_index(drop=True)
    trades = A.simulate(h1_df, pos_by_ts, strong)
    print(f"Strong-breakout ES trades: {len(trades)} (unique bars: {strong['retest_time'].nunique()})")

    nq_h1 = L.load_ohlc_data(NQ_H1_PATH)
    _, _, nq_retests = L.detect_lxpb_h1(nq_h1)
    print(f"NQ H1 retests (full series): {len(nq_retests)} rows / {nq_retests['retest_time'].nunique()} unique bars")

    print("Building coincidence flags (h1 -> m1 -> 1s escalation, real ticks)...")
    flags = build_coincidence_flags(trades, nq_h1, nq_retests)
    print(flags[["h1_coincide", "m1_coincide", "1s_coincide"]].sum())
    print(f"\nMean m1 diff (minutes) when h1-coincident: "
          f"{flags.loc[flags['h1_coincide'], 'm1_diff_min'].mean():.2f}")
    print(f"Mean 1s diff (seconds) when m1-coincident: "
          f"{flags.loc[flags['m1_coincide'], '1s_diff_sec'].mean():.2f}")

    for col in ["h1_coincide", "m1_coincide", "1s_coincide"]:
        grid_subset_report(trades, flags, col, STOPS_TARGETS)

    out_path = os.path.join(_HERE, "data", "nq_coincidence_flags.csv")
    flags.to_csv(out_path, index=False)
    print(f"\nFlags saved -> {out_path}")


if __name__ == "__main__":
    main()
