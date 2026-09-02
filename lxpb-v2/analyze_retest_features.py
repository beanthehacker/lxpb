"""
Exploratory feature analysis for the 72 "strong breakout" LXPB retests (42
unique H1 retest bars) -- tests specific hypotheses raised in review:

  a) approach_strength : does a fast/strong directional move INTO the level
                          over the last few H1 bars (vs a slow drift) predict
                          a better outcome?
  b) swerve             : does a "near miss" (price got close to the level,
                          pulled away, then came back later) before the real
                          retest predict a WORSE outcome than a level that
                          was approached directly?
  c) is_spike            : does a spike-shaped formation bar (patterns-pure
                          hammer/shooting-star, same hint used for the
                          phase0_spike checkbox in lxpb_labels_report.html)
                          outperform a plain swing/range formation bar?
  d) cluster composition : within a confluence cluster (multiple distinct
                          LXPB levels retested by the SAME H1 bar), does the
                          MOST RECENTLY FORMED level in the cluster
                          outperform older members? Do spike/large-wick
                          members outperform swing members within a cluster?

Reuses render_labels_report.compute_hints() for is_spike/large_wick/
confluence/fast_retest so these numbers are identical to what the
labeling report itself shows (same hint functions, same thresholds) --
not reimplemented separately. Outcome metrics (MFE/MAE/PnL@6h) are chosen
because they have NO same-bar ordering ambiguity (pure min/max/close-price
lookups), so they're not affected by the H1-bar-walk look-ahead bias
already documented for the stop/target grid in analyze_breakout_exits.py.

CAVEAT: 72 rows collapse to 42 unique retest bars (many rows share a bar
via confluence) -- every "n=72" statistic below really rests on an
effective sample closer to ~42 independent events; group splits here are
exploratory, not statistically powered.
"""
import os
import sys
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import analyze_breakout_exits as A  # noqa: E402
import render_labels_report as R  # noqa: E402
import lxpb as L  # noqa: E402

pd.set_option("display.width", 180)
pd.set_option("display.max_columns", 30)

APPROACH_LOOKBACK_BARS = 3   # H1 bars (incl. the retest bar itself) used for approach_strength
SWERVE_NEAR_FRAC = 0.5       # near-miss if closest approach < this fraction of avg 20-bar range
HOLD_BARS_OUTCOME = 6        # matches "edge front-loaded, don't hold past ~6-8h" finding


def load_full_context():
    """Same loading/filtering as analyze_breakout_exits.load_strong_breakout_rows,
    but also keeps touch_lv1_df + the pre-strong-filter retests_df, needed for
    compute_hints' confluence-overlay ('all_broken') argument."""
    h1_df = L.load_ohlc_data(A.DATA_PATH)
    _touch_lv0, touch_lv1_df, retests_df = L.detect_lxpb_h1(h1_df)
    retests_df, _n_gap = R.filter_gap_rows(h1_df, retests_df)
    retests_df = retests_df[(retests_df["retest_time"] >= pd.Timestamp("2026-07-01")) &
                             (retests_df["retest_time"] <= pd.Timestamp("2026-08-31"))]
    retests_df = retests_df.sort_values("retest_time", ascending=False).head(300)
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)

    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    rng = (h1_df["high"] - h1_df["low"])
    avg_range_20 = rng.rolling(R.AVG_RANGE_WINDOW).mean().shift(1).to_dict()
    all_broken = R.build_all_broken_out(retests_df, touch_lv1_df)

    ratios = []
    for _, row in retests_df.iterrows():
        breakout_range = float(row["breakout_high"] - row["breakout_low"])
        baseline = avg_range_20.get(row["breakout_time"])
        ratios.append((breakout_range / baseline) if baseline and baseline > 0 else None)
    retests_df = retests_df.assign(range_ratio=ratios)
    return h1_df, pos_by_ts, avg_range_20, all_broken, retests_df


def approach_strength(h1_df, pos_by_ts, avg_range_20, row):
    """Signed, ATR-normalized directional move over the last
    APPROACH_LOOKBACK_BARS H1 bars ending at the retest bar's own close,
    positive = a fast/strong move IN THE TRADE'S DIRECTION (down into an
    LHPB support retest, up into an LLPB resistance retest); near zero or
    negative = a slow drift / chop / even a move away right before the
    touch."""
    idx = pos_by_ts[row["retest_time"]]
    b_idx = pos_by_ts[row["breakout_time"]]
    start = max(idx - (APPROACH_LOOKBACK_BARS - 1), b_idx + 1)
    n_bars = idx - start + 1
    open_start = float(h1_df["open"].iloc[start])
    close_end = float(h1_df["close"].iloc[idx])
    net_move = close_end - open_start
    strength = -net_move if row["type"] == "LHPB" else net_move
    baseline = avg_range_20.get(row["retest_time"])
    if not baseline or baseline <= 0 or n_bars <= 0:
        return np.nan
    return strength / (baseline * n_bars)


def swerve_flag_and_dist(h1_df, pos_by_ts, avg_range_20, row):
    """True if, somewhere in the FTA window (breakout_time, retest_time)
    BEFORE the actual retest bar, price came within SWERVE_NEAR_FRAC of a
    typical bar's range of the level, then pulled away again for at least
    one more bar before the real retest happened later (a "near miss and
    return", as opposed to one continuous glide straight into the level).
    Every bar in this window is guaranteed (by lxpb.py's own state machine)
    to have NOT touched/gapped the level, so 'distance to level' is always
    a well-defined positive number for the level's own side."""
    b_idx = pos_by_ts[row["breakout_time"]]
    r_idx = pos_by_ts[row["retest_time"]]
    win_start, win_end = b_idx + 1, r_idx  # [win_start, win_end) excludes the retest bar itself
    if win_end - win_start < 2:
        return False, np.nan  # not enough bars for a "near miss, then pull away" pattern
    window = h1_df.iloc[win_start:win_end]
    price = row["price"]
    if row["type"] == "LHPB":
        dist = (window["low"] - price).to_numpy(float)
    else:
        dist = (price - window["high"]).to_numpy(float)
    idx_min = int(np.argmin(dist))
    min_dist = float(dist[idx_min])
    baseline = avg_range_20.get(row["retest_time"])
    if not baseline or baseline <= 0:
        return False, min_dist
    near_miss = min_dist < SWERVE_NEAR_FRAC * baseline
    pulled_away_after = idx_min != len(dist) - 1  # at least 1 more bar before the real retest
    return bool(near_miss and pulled_away_after), min_dist


def outcome_metrics(trades):
    mae, mfe, _dd = A.mae_mfe_dd_stats(trades)
    pnl_hold = []
    for t in trades:
        if t["n"] <= HOLD_BARS_OUTCOME:
            pnl_hold.append(np.nan)
            continue
        entry, close_n = t["entry"], t["closes"][HOLD_BARS_OUTCOME]
        pnl_hold.append((close_n - entry) if t["is_long"] else (entry - close_n))
    return mae.to_numpy(), mfe.to_numpy(), np.array(pnl_hold)


def group_report(df, group_col, label):
    print(f"\n--- {label} (grouped by `{group_col}`) ---")
    g = df.groupby(group_col, dropna=False).agg(
        n=("pnl_hold", "size"),
        win_rate_hold=("pnl_hold", lambda s: (s > 0).mean()),
        mean_pnl_hold=("pnl_hold", "mean"),
        mean_mfe=("mfe", "mean"),
        mean_mae=("mae", "mean"),
    ).round(2)
    print(g.to_string())
    return g


def main():
    h1_df, pos_by_ts, avg_range_20, all_broken, retests_df = load_full_context()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)].reset_index(drop=True)
    print(f"Strong breakout rows: {len(strong)}  |  unique retest bars: {strong['retest_time'].nunique()}")

    trades = A.simulate(h1_df, pos_by_ts, strong)
    # simulate() silently drops rows with an empty forward window -- realign.
    kept_retest_times = {t["retest_time"] for t in trades}
    strong = strong[strong["retest_time"].isin(kept_retest_times)].reset_index(drop=True)
    mae, mfe, pnl_hold = outcome_metrics(trades)

    rows = []
    for i, row in strong.iterrows():
        hints = R.compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken,
                                 R.N_TICKS_DEFAULT, R.TICK_SIZE_DEFAULT)
        appr = approach_strength(h1_df, pos_by_ts, avg_range_20, row)
        swerve, swerve_dist = swerve_flag_and_dist(h1_df, pos_by_ts, avg_range_20, row)
        level_age_bars = pos_by_ts[row["retest_time"]] - pos_by_ts[row["formation_time"]]
        rows.append({
            "retest_time": row["retest_time"], "type": row["type"], "price": row["price"],
            "formation_time": row["formation_time"], "range_ratio": row["range_ratio"],
            "is_spike": hints["is_spike"], "large_wick": hints["defaults"]["large_wick"],
            "wick_pct": hints["wick_pct"], "confluence_count": hints["confluence_count"],
            "fast_retest": hints["defaults"]["fast_retest"], "bars_to_retest": hints["bars_to_retest"],
            "level_age_bars": level_age_bars,
            "approach_strength": appr, "swerve": swerve, "swerve_dist": swerve_dist,
            "mae": mae[i], "mfe": mfe[i], "pnl_hold": pnl_hold[i],
        })
    df = pd.DataFrame(rows)

    # Cluster membership: rows sharing the same retest_time bar.
    df["cluster_size"] = df.groupby("retest_time")["retest_time"].transform("size")
    df["cluster_rank"] = df.groupby("retest_time")["formation_time"] \
        .rank(ascending=False, method="first").astype(int)  # 1 = most recently formed in the cluster
    df["is_most_recent_in_cluster"] = df["cluster_rank"] == 1

    n_clusters = int((df["cluster_size"] >= 2).groupby(df["retest_time"]).any().sum())
    print(f"Rows in a size>=2 cluster: {int((df['cluster_size'] >= 2).sum())} across "
          f"{n_clusters} clustered bars\n")

    # --- (a) approach strength ---------------------------------------------
    valid_a = df.dropna(subset=["approach_strength", "pnl_hold"])
    df["approach_bucket"] = pd.cut(
        df["approach_strength"], bins=[-np.inf, valid_a["approach_strength"].median(), np.inf],
        labels=["slow/drift (below median)", "fast/strong (above median)"])
    group_report(df, "approach_bucket", "(a) Approach strength into the level")
    if len(valid_a) >= 5:
        corr_mfe = valid_a["approach_strength"].corr(valid_a["mfe"])
        corr_pnl = valid_a["approach_strength"].corr(valid_a["pnl_hold"])
        print(f"Pearson corr(approach_strength, MFE) = {corr_mfe:.2f} | "
              f"corr(approach_strength, pnl@{HOLD_BARS_OUTCOME}h) = {corr_pnl:.2f}  (n={len(valid_a)})")

    # --- (b) swerve ----------------------------------------------------------
    group_report(df, "swerve", "(b) Swerve (near-miss then pulled away before the real retest)")

    # --- (c) spike vs non-spike formation -------------------------------------
    group_report(df, "is_spike", "(c) Spike-shaped formation bar (phase0_spike hint)")
    group_report(df, "large_wick", "(c-bis) Large-wick formation bar (large_wick hint)")

    # --- (d) cluster composition ----------------------------------------------
    clustered = df[df["cluster_size"] >= 2]
    if not clustered.empty:
        group_report(clustered, "is_most_recent_in_cluster",
                      "(d) Within confluence clusters: most-recently-formed level vs older members")
        print("\n(d) Within-cluster is_spike / large_wick composition by recency rank:")
        print(clustered.groupby("cluster_rank")[["is_spike", "large_wick"]].mean().round(2).to_string())
    else:
        print("\n(d) No size>=2 clusters found in this sample.")

    valid_age = df.dropna(subset=["pnl_hold"])
    corr_age = valid_age["level_age_bars"].corr(valid_age["pnl_hold"])
    print(f"\nPearson corr(level_age_bars [formation->retest], pnl@{HOLD_BARS_OUTCOME}h) "
          f"= {corr_age:.2f}  (n={len(valid_age)})")

    df.to_csv(os.path.join(_HERE, "data", "retest_feature_analysis.csv"), index=False)
    print(f"\nFull per-row feature table -> data\\retest_feature_analysis.csv ({len(df)} rows)")


if __name__ == "__main__":
    main()
