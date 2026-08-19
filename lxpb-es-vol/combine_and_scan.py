"""
LXPB-zone-first scan: only evaluate the 1s order-flow absorption pattern at
prices that currently sit inside a valid H1 LXPB stacked-level confluence
zone. Absorption bursts elsewhere are structurally meaningless for this
strategy and are never computed/reported as candidates at all -- there is
no "false positive" bucket, because a location with no confluence was
never in scope to begin with.

Rules (per strategy spec):
  - MIN_HOURS_BEFORE_RETEST relaxed to 2h (see lxpb_confluence.py).
  - Valid LXPB levels for an hour = official H1 touch_lv1 output as of
    immediately before that hour starts (the H1 algorithm's own
    consume-on-touch history up to that point is respected as-is).
  - Within the hour, a level is immediately consumed the instant
    1-second price actually touches it -- it can no longer count as
    confluence later in the same hour (see
    lxpb_confluence.level_intervals_confluence_at /
    build_level_intervals).
  - Confluence zone: >= STACK_THRESHOLD (default 2) same-type LxPB levels
    (LHPB for long / LLPB for short) within N_TICKS (default 20 ticks =
    5.0 pts on ES) of a given price.

Pipeline order (logically zone-first, not absorption-first -- see the note
on evaluation order below):
  1. Compute features for every 1s bar (Run_Low/Run_High etc; needed
     regardless of the zone, just to know each bar's rolling extremes),
     and flag_events (the raw volume/delta absorption pattern), both
     vectorized/cheap over the whole dataset.
  2. Check whether Run_Low sits in a valid LHPB zone / Run_High sits in a
     valid LLPB zone (`level_intervals_confluence_at`) -- this is the
     "is this price/time even a candidate at all" gate, conceptually
     independent of the volume/delta pattern. The confluence lookup is
     anchored to the START of the current RUN_LEN-bar absorption run
     window (not the individual bar), so a level consumed by an EARLIER
     bar inside the SAME burst still counts as confluence for a later
     bar's trigger in that same burst -- multi-second absorption bursts
     (e.g. a huge-volume bar immediately followed by the rejection bar)
     are one event, not two independent ones (see `_window_start_ts`).
     NOTE (performance only, not semantics): this zone check is the
     expensive per-bar step, so for large multi-day scans it is only
     evaluated on bars that already passed the cheap absorption-pattern
     check in step 1 -- `zone AND absorption` gives an identical final
     result regardless of which side of the AND is evaluated first, so
     restricting the expensive lookup to absorption-pattern candidates
     only changes runtime, not which bars end up flagged.
  3. A LONG signal requires SellAbsorption AND a valid LHPB zone; a SHORT
     signal requires BuyAbsorption AND a valid LLPB zone.
  4. Final validity gate: at least one of the confluence-set levels must
     actually have been TESTED by this burst's price action -- i.e. sit
     within TOUCH_TOL_TICKS ticks of the run's extreme (Run_Low for
     long / Run_High for short). A level merely sitting somewhere inside
     the wider N_TICKS confluence zone, without price ever actually
     approaching it, does not make the burst a real test of that zone
     (see TOUCH_TOL_TICKS below).
  5. The output CSV contains ONLY confirmed signals (no false-positive
     rows at all).

Caching: the two most expensive stages (H1 LXPB state machine over the
full multi-year H1 history, and the 1s-resolution level-consumption
scan) are transparently disk-cached via lxpb_cache.py, keyed off the
input CSV files' (path, mtime, size). Re-running scan() against the same
1s CSV / H1 CSV recomputes nothing.
"""
import os
import bisect
import pandas as pd

from absorption_backtest import build_features, flag_events, RUN_LEN, TICK
import lxpb_confluence as C
import lxpb_cache

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV_1S = os.path.join(_HERE, "ES_20260813_0800-0930_PT_1s.csv")
# Ported from daily-analysis (was: r"D:\daily-analysis\data\ES1!-H1.csv").
# Points at this repo's own local ES H1 history instead of the original
# external/absolute path.
H1_CSV = os.path.join(_HERE, "..", "data", "es-h1-4apr2021-11apr2025.csv")
OUT_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers.csv")

N_TICKS = 20
STACK_THRESHOLD = 2
# A confluence-zone level only counts as an actual "test" of that level if
# the burst's price extreme (Run_Low/Run_High) came within this many ticks
# of it -- either by touching it outright (consumed) or by coming close
# without technically crossing it. Being merely inside the wider N_TICKS
# stacking zone is not enough on its own.
TOUCH_TOL_TICKS = 2


def _h1_bin(ts, bar_times):
    ts_naive = ts.tz_convert("UTC").tz_localize(None)
    return bisect.bisect_right(bar_times, ts_naive) - 1


def _window_start_ts(idx_list, i, bar_times):
    """Timestamp of the earliest bar in the RUN_LEN-bar window ending at
    position i, capped so it never crosses into a different H1 hour (the
    consumption-state base-level set resets at each H1 boundary, so
    reaching across one would incorrectly resurrect/borrow another
    hour's level set)."""
    j = max(0, i - (RUN_LEN - 1))
    ts, ts_start = idx_list[i], idx_list[j]
    if _h1_bin(ts_start, bar_times) != _h1_bin(ts, bar_times):
        return ts
    return ts_start


def scan(csv_1s_path=CSV_1S, out_csv_path=OUT_CSV, verbose=True):
    df = pd.read_csv(csv_1s_path, index_col="Time_PT", parse_dates=True)
    df = build_features(df)

    if verbose:
        print(f"Loaded {len(df)} 1s bars from {csv_1s_path}")
    intervals_df, retests_df, snapshots, bar_times = lxpb_cache.get_level_intervals(
        df, csv_1s_path, H1_CSV, verbose=verbose)

    # --- Step 2 first (cheap, vectorized): find every bar with the raw
    # order-flow absorption pattern, regardless of LXPB zone. This is only
    # a performance reordering for large multi-day scans -- logically the
    # final signal is still "zone AND absorption AND touch-tolerance", an
    # AND of independent conditions, so evaluating the cheap vectorized
    # condition first and restricting the expensive per-bar LXPB
    # confluence lookup (Step 1) to just those candidate bars produces an
    # IDENTICAL result to checking the zone on every bar first, just much
    # faster once the dataset is more than a session or two. ---
    df = flag_events(df)
    candidate_mask = df["SellAbsorption"] | df["BuyAbsorption"]
    n_candidates = int(candidate_mask.sum())
    if verbose:
        print(f"  Raw absorption-pattern bars (pre zone-gate): {n_candidates} / {len(df)}")

    # --- Step 1: zone-first LXPB confluence gate, evaluated only for the
    # (small) set of candidate bars found above. The confluence lookup is
    # anchored to the START of the RUN_LEN-bar window ending at this bar
    # (see _window_start_ts), so a level consumed by an earlier bar of the
    # SAME burst is still credited to this bar -- a multi-second
    # absorption burst is one event. ---
    if verbose:
        print("Scoring LXPB confluence zones for candidate bars ...")
    idx_list = df.index
    pos_by_ts = {ts: i for i, ts in enumerate(idx_list)}
    candidate_positions = [pos_by_ts[ts] for ts in df.index[candidate_mask]]

    touch_tol = TOUCH_TOL_TICKS * TICK
    n_dropped_no_zone = 0
    n_dropped_no_touch = 0
    rows = []
    for i in candidate_positions:
        ts = idx_list[i]
        row = df.iloc[i]
        lookup_ts = _window_start_ts(idx_list, i, bar_times)
        is_long = bool(row["SellAbsorption"])
        level_type = "LHPB" if is_long else "LLPB"
        tested_price = row["Run_Low"] if is_long else row["Run_High"]
        zone_ok, count, levels = C.level_intervals_confluence_at(
            intervals_df, lookup_ts, level_type, tested_price, n_ticks=N_TICKS, threshold=STACK_THRESHOLD)
        if not zone_ok:
            n_dropped_no_zone += 1
            continue

        # --- Step 4: at least one confluence level must actually have
        # been tested (within TOUCH_TOL_TICKS) by this burst's price
        # extreme -- otherwise the burst never really interacted with the
        # zone, it just happened to occur somewhere inside the wider
        # N_TICKS stacking radius. ---
        nearest_dist = min(abs(lv["price"] - tested_price) for lv in levels)
        if nearest_dist > touch_tol:
            n_dropped_no_touch += 1
            continue

        rows.append({
            "time_pt": ts,
            "direction": "LONG" if is_long else "SHORT",
            "trigger_close": row["Close"],
            "tested_price": tested_price,
            "volume": row["Volume"],
            "delta": row["Delta"],
            "vol_z": round(row["Vol_Z"], 2),
            "run_delta_sum": row["Run_DeltaSum"],
            "reject_ticks": row["RejectUp_Ticks"] if is_long else row["RejectDown_Ticks"],
            "level_type": level_type,
            "confluence_count": count,
            "confluence_prices": ";".join(f"{lv['price']:.2f}" for lv in sorted(levels, key=lambda x: x['price'])),
            "nearest_level_ticks": round(nearest_dist / TICK, 2),
        })

    cols = ["time_pt", "direction", "trigger_close", "tested_price", "volume", "delta",
            "vol_z", "run_delta_sum", "reject_ticks", "level_type", "confluence_count",
            "confluence_prices", "nearest_level_ticks"]
    out = pd.DataFrame(rows, columns=cols)
    if not out.empty:
        out = out.sort_values("time_pt").reset_index(drop=True)
    out.to_csv(out_csv_path, index=False)

    if verbose:
        print(f"  Candidates dropped (no valid LXPB zone at trigger time): {n_dropped_no_zone}")
    print(f"  Bursts dropped (zone+absorption fired but no level tested within "
          f"{TOUCH_TOL_TICKS} ticks): {n_dropped_no_touch}")
    print(f"\nConfirmed LXPB-zone + absorption signals: {len(out)} "
          f"(N={N_TICKS} ticks, stack thresh={STACK_THRESHOLD})")
    print(f"Saved -> {out_csv_path}")
    if verbose or len(out) <= 50:
        print(out.to_string())
    return out


if __name__ == "__main__":
    scan()
