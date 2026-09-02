"""
Cross-timeframe entry refinement: for each of the 99 "strong breakout" H1
LXPB retests used throughout this repo (see analyze_breakout_exits.py /
exit_analysis_report.html), check whether the SAME real-world swing point
is *independently* re-detected by the exact same LXPB algorithm
(lxpb.detect_lxpb_h1 -- it is timeframe-agnostic, driven purely by wall-
clock time deltas, not bar counts) run on 5-minute bars instead of hourly
ones (data/25Aug-CME_MINI_ES1!, 5.csv, added 2026-08-25).

Why this can help: an H1 bar's "level" price is, by construction, always
equal to the high or low of exactly one of its twelve 5-minute sub-bars,
so the LEVEL PRICE itself rarely differs. What DOES differ is the ENTRY
TIME: today's H1-only reports treat a level as "touched" the instant its
H1 retest bar merely overlaps the price -- i.e. anywhere in that 60-minute
bar -- which silently ignores how much the market moved (against or for
the trade) in the part of that hour BEFORE the real touch. Escalating to
the same-day M5 series and re-running the identical detect_lxpb_h1 phase
0-4 state machine on it pins that same retest to the specific 5-minute bar
where the level was actually touched/gapped-over, giving a strictly later
(or equal), more precise entry instant -- without needing to touch the
slow real-tick .scid pipeline used by render_stop_target_report.py.

Verified up front: the M5 file is already on the EXACT SAME back-adjusted
price scale as this repo's H1 file (data/24aug-CME_MINI_ES1!, 60.csv) --
spot-checked an H1 bar against its twelve M5 sub-bars and the open/high/
low/close matched exactly, no per-bar offset needed (both are TradingView
CME_MINI_ES1! continuous exports). M5 coverage is 2026-06-24 -> 2026-08-25,
comfortably spanning the 2026-07-01..2026-08-31 H1 sample window.

Matching rule (find_m5_match): for a given H1 retest row, search the (gap-
excluded) M5-detected retests for the same `type` whose OWN formation_time
falls inside the H1 level's formation hour (same swing point) AND whose
OWN retest_time falls inside the H1 level's retest hour (same real-world
touch event) -- i.e. the M5 algorithm's independent phase-1..3 lifecycle
must line up with the H1 one on both ends. Among any candidates (usually
0 or 1), the one with the closest level price is picked. No match means
the M5-scale algorithm did not independently confirm a full breakout+
retest lifecycle lining up with the H1 one in the same two hours (e.g. the
H1 hourly extreme wasn't itself registered/retested as its own M5 level in
time, or MIN_HOURS_BEFORE_RETEST pushed the M5 retest into a different
hour) -- these rows fall back to the plain H1 entry for the "adopt this
system" figures, and are reported separately from the matched-pair figures.

Comparison design (isolates entry TIMING, not simulation resolution): both
the "H1 baseline" and "M5 fine-tuned" variants are walked forward using the
SAME M5 forward bars and the SAME simple bar-level stop/target resolution
(np.argmax tie-break, same convention as analyze_breakout_exits.py's
stop_target_grid -- no 1s tick escalation here, this is a fast, purely
M5-bar comparison) -- baseline starts at the first M5 bar of the H1 retest
hour (what an H1-only view implicitly assumes: entry at the top of the
hour), fine-tuned starts at the actual matched M5 retest bar. Any
difference in outcome is therefore attributable ONLY to entry timing/price
refinement, not to a change in exit-detection resolution.

Usage:
    python analyze_m5_finetune_entries.py
"""
import os
import sys
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)
sys.path.insert(0, _REPO_ROOT)  # lxpb.py lives at the repo root, not in lxpb-v2
import lxpb as L  # noqa: E402
import render_labels_report as R  # noqa: E402
import analyze_breakout_exits as A  # noqa: E402

M5_PATH = os.path.join(_HERE, "data", "25Aug-CME_MINI_ES1!, 5.csv")
M5_HORIZON_BARS = 72 * 12  # same 3-day (72 H1 bars) horizon as analyze_breakout_exits, in M5 bars
STOPS = [2, 3, 4, 6, 8, 10, 12, 16, 20]
TARGETS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 20)


def load_m5_lxpb():
    m5_df = L.load_ohlc_data(M5_PATH)
    _t0, _t1, m5_retests = L.detect_lxpb_h1(m5_df)
    m5_retests, n_gap = R.filter_gap_rows(m5_df, m5_retests)
    print(f"M5 series: {len(m5_df)} bars ({m5_df.index.min()} -> {m5_df.index.max()}); "
          f"{len(m5_retests) + n_gap} completed M5 retests detected, {n_gap} gap-excluded, "
          f"{len(m5_retests)} kept")
    return m5_df, m5_retests


def find_m5_match(h1_row, m5_retests_by_type):
    """See module docstring for the matching rule.

    IMPORTANT (revised after review -- the original version required the M5
    level's OWN formation_time to fall inside the H1 level's formation HOUR,
    which is too narrow: the true finer-grained swing point an H1 level's
    price corresponds to can just as well have formed on the H1 BREAKOUT
    bar itself (a fresh intra-breakout-hour extreme), or well BEFORE the H1
    formation bar entirely (an older swing that the market simply revisits;
    the H1 state machine re-registers every bar's high/low every hour
    regardless, so an old swing can get "re-discovered" as a later hour's
    nominal formation_time even though the real turning point is much
    older). Anchoring on formation_time alone also risks the opposite
    failure mode: a similar PRICE from a totally unrelated, temporally
    distant swing (support/resistance levels get revisited for unrelated
    reasons) can be pulled in as a false match.

    Fixed by anchoring on the two events that must, by definition, belong
    to the SAME real breakout/retest cycle as the H1 row -- the M5
    candidate's own breakout_time must land inside the H1 breakout bar's
    hour, and its own retest_time must land inside the H1 retest bar's
    hour -- while leaving formation_time unconstrained (any time at or
    before the H1 breakout hour, including well before the H1 formation
    bar, or during the H1 formation/breakout bars themselves). Multiple
    M5 candidates can still satisfy both windows (e.g. several M5 bars
    breaking out in the same hour); the one with the closest level price
    to the H1 entry_price is picked."""
    same_type = m5_retests_by_type.get(h1_row["type"])
    if same_type is None or same_type.empty:
        return None
    break_lo = h1_row["breakout_time"]
    break_hi = break_lo + pd.Timedelta(hours=1)
    retest_lo = h1_row["retest_time"]
    retest_hi = retest_lo + pd.Timedelta(hours=1)
    cand = same_type[
        (same_type["formation_time"] <= break_hi) &
        (same_type["breakout_time"] >= break_lo) & (same_type["breakout_time"] < break_hi) &
        (same_type["retest_time"] >= retest_lo) & (same_type["retest_time"] < retest_hi)
    ]
    if cand.empty:
        return None
    price_diff = (cand["price"] - h1_row["entry_price"]).abs()
    return cand.loc[price_diff.sort_values().index[0]]


def simulate_from(m5_df, pos_by_ts_m5, entry_price, is_long, start_ts, horizon_bars=M5_HORIZON_BARS):
    """Same fav/adv (signed, favorable-positive) forward-window convention
    as analyze_breakout_exits.simulate, but on M5 bars from an arbitrary
    start instant (not necessarily an existing H1 retest row)."""
    start = pos_by_ts_m5.get(start_ts)
    if start is None:
        return None
    end = min(start + horizon_bars, len(m5_df))
    window = m5_df.iloc[start:end]
    if window.empty:
        return None
    highs = window["high"].to_numpy(float)
    lows = window["low"].to_numpy(float)
    closes = window["close"].to_numpy(float)
    fav = (highs - entry_price) if is_long else (entry_price - lows)
    adv = (lows - entry_price) if is_long else (entry_price - highs)
    return {"fav": fav, "adv": adv, "closes": closes, "n": len(window)}


def resolve_stop_target(trade, stop, target):
    """Same simple bar-level tie-break convention as
    analyze_breakout_exits.stop_target_grid (argmax of the first bar whose
    fav/adv crosses the threshold; ties within the same bar favor whichever
    index is smaller, defaulting to target on an exact tie since
    hit_target <= hit_stop). Returns (outcome, r, mae) where mae is this
    trade's own min(adv) up to (and including) the resolving bar -- the
    worst adverse excursion actually lived through before the outcome was
    decided (not the whole horizon, which would credit/blame price action
    that happened after the trade was already closed)."""
    fav, adv = trade["fav"], trade["adv"]
    hit_stop = int(np.argmax(adv <= -stop)) if np.any(adv <= -stop) else None
    hit_target = int(np.argmax(fav >= target)) if np.any(fav >= target) else None
    if hit_stop is None and hit_target is None:
        last = trade["n"] - 1
        mae = float(np.min(adv))
        r = (fav[-1] / stop) if fav[-1] >= 0 else (adv[-1] / stop)
        return "no_hit", r, mae
    if hit_target is not None and (hit_stop is None or hit_target <= hit_stop):
        mae = float(np.min(adv[:hit_target + 1]))
        return "target", target / stop, mae
    mae = float(np.min(adv[:hit_stop + 1]))
    return "stop", -1.0, mae


def build_pairs():
    """Returns list of dicts, one per H1 strong-breakout trade, each with
    both a `baseline` (H1 entry, M5-walked) and `finetuned` (M5-matched
    entry, or None if unmatched) simulate_from() result plus metadata."""
    h1_df, pos_by_ts_h1, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= R.WIDE_BREAKOUT_RATIO_THRESHOLD)].reset_index(drop=True)

    m5_df, m5_retests = load_m5_lxpb()
    pos_by_ts_m5 = {ts: i for i, ts in enumerate(m5_df.index)}
    m5_retests_by_type = {t: g for t, g in m5_retests.groupby("type")}

    pairs = []
    n_matched = 0
    for _, row in strong.iterrows():
        is_long = row["type"] == "LHPB"
        baseline = simulate_from(m5_df, pos_by_ts_m5, float(row["entry_price"]), is_long, row["retest_time"])
        if baseline is None:
            continue
        m5_match = find_m5_match(row, m5_retests_by_type)
        finetuned = None
        if m5_match is not None:
            finetuned = simulate_from(m5_df, pos_by_ts_m5, float(m5_match["entry_price"]),
                                       is_long, m5_match["retest_time"])
            if finetuned is not None:
                n_matched += 1
        pairs.append({
            "type": row["type"], "retest_time": row["retest_time"],
            "h1_entry": float(row["entry_price"]),
            "m5_entry": float(m5_match["entry_price"]) if m5_match is not None else None,
            "m5_retest_time": m5_match["retest_time"] if m5_match is not None else None,
            "delay_minutes": ((m5_match["retest_time"] - row["retest_time"]).total_seconds() / 60.0)
                             if m5_match is not None else None,
            "baseline": baseline, "finetuned": finetuned,
        })
    print(f"\n{len(pairs)} H1 strong-breakout trades with M5 forward data; "
          f"{n_matched} ({n_matched / len(pairs) * 100:.1f}%) matched to an independently-confirmed M5 retest")
    return pairs


def grid_compare(pairs, stops, targets, matched_only=True):
    rows = []
    for stop in stops:
        for target in targets:
            b_out, b_r, b_mae_w = [], [], []
            f_out, f_r, f_mae_w = [], [], []
            for p in pairs:
                if matched_only and p["finetuned"] is None:
                    continue
                bo, br, bmae = resolve_stop_target(p["baseline"], stop, target)
                b_out.append(bo); b_r.append(br)
                if bo == "target":
                    b_mae_w.append(bmae)
                if p["finetuned"] is not None:
                    fo, fr, fmae = resolve_stop_target(p["finetuned"], stop, target)
                    f_out.append(fo); f_r.append(fr)
                    if fo == "target":
                        f_mae_w.append(fmae)
            n_b = len(b_r)
            n_f = len(f_r)
            b_wins = sum(1 for o in b_out if o == "target")
            f_wins = sum(1 for o in f_out if o == "target")
            rows.append({
                "stop": stop, "target": target, "n": n_b,
                "base_win%": b_wins / n_b * 100 if n_b else np.nan,
                "ft_win%": f_wins / n_f * 100 if n_f else np.nan,
                "base_avgR": np.mean(b_r) if n_b else np.nan,
                "ft_avgR": np.mean(f_r) if n_f else np.nan,
                "base_totR": np.sum(b_r) if n_b else np.nan,
                "ft_totR": np.sum(f_r) if n_f else np.nan,
                "base_MAE_win_med": np.median(b_mae_w) if b_mae_w else np.nan,
                "ft_MAE_win_med": np.median(f_mae_w) if f_mae_w else np.nan,
                "base_MAE_win_mean": np.mean(b_mae_w) if b_mae_w else np.nan,
                "ft_MAE_win_mean": np.mean(f_mae_w) if f_mae_w else np.nan,
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pairs = build_pairs()

    matched = [p for p in pairs if p["finetuned"] is not None]
    delays = pd.Series([p["delay_minutes"] for p in matched])
    price_diffs = pd.Series([abs(p["m5_entry"] - p["h1_entry"]) for p in matched])
    print("\n=== Matched-pair entry refinement stats ===")
    print(f"Entry delay vs H1 top-of-hour (minutes): "
          f"median={delays.median():.1f}, mean={delays.mean():.1f}, max={delays.max():.1f}")
    print(f"Entry price shift (points, |m5 - h1|): "
          f"median={price_diffs.median():.2f}, mean={price_diffs.mean():.2f}, max={price_diffs.max():.2f}, "
          f"n_nonzero={(price_diffs > 0).sum()}")

    print("\n=== Stop/Target grid comparison (MATCHED PAIRS ONLY, M5-walked both ways) ===")
    grid = grid_compare(pairs, STOPS, TARGETS, matched_only=True)
    grid = grid.assign(totR_delta=grid["ft_totR"] - grid["base_totR"])
    print(grid.sort_values("totR_delta", ascending=False).head(15).round(2).to_string(index=False))

    print("\n=== Same grid, sorted by MAE-for-winners improvement (more negative MAE = worse) ===")
    grid = grid.assign(mae_delta=grid["ft_MAE_win_mean"] - grid["base_MAE_win_mean"])
    print(grid.sort_values("mae_delta", ascending=False).head(15).round(2).to_string(index=False))

    print("\n=== Reference row: stop=2 / target=2 (matches exit_analysis_report.html's headline row) ===")
    ref = grid[(grid["stop"] == 2) & (grid["target"] == 2)]
    print(ref.round(2).to_string(index=False))

    print(f"\nOverall totR_delta summary (all stop/target combos): "
          f"mean={grid['totR_delta'].mean():.2f}, median={grid['totR_delta'].median():.2f}, "
          f"positive in {(grid['totR_delta'] > 0).sum()}/{len(grid)} combos")
