"""
Stop/target optimization + feature analysis for the 73 full-history
LXPB+absorption signals (lxpb_volume_strat_triggers_full.csv).

Four analyses:

 1. MFE/MAE profiling -- for every trade, the maximum favorable excursion
    (MFE, biggest paper profit reached at ANY point going forward, no
    stop/target applied) and the maximum adverse excursion that occurred
    BEFORE that peak (MAE-before-peak, i.e. the worst drawdown a trader
    would have had to sit through to actually capture the eventual peak).
    This directly answers "which trades go up 8-10-12 points, and what
    stop size would have been needed to not get shaken out first".

 2. Fixed stop x fixed target grid search (points, NOT tied to the
    impulse extreme) -- win rate / total R / expectancy for every
    combination in a stop grid x target grid, to find the best-performing
    pair empirically rather than assuming the impulse-extreme stop is
    optimal.

 3. Stop-reference comparison -- "impulse extreme" (current tested_price,
    i.e. the burst's own Run_Low/Run_High) vs "cluster extreme" (the
    FARTHEST level in the confluence stack: lowest LHPB / highest LLPB),
    each with a small tick buffer, backtested against the same fixed
    target grid.

 4. Volume / follow-through fine-tuning -- correlates vol_z, raw volume,
    run_delta_sum, confluence_count, and short-horizon follow-through
    (net directional move + volume in the N seconds right after the
    trigger) against whether a trade goes on to be a "big winner" (MFE
    >= BIG_WIN_PTS), via simple tercile/threshold splits.
"""
import os
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
TICK = 0.25
TRIGGERS_CSV = os.path.join(_HERE, "lxpb_volume_strat_triggers_full.csv")
CSV_1S = os.path.join(_HERE, "ES_full_1s.csv")

BIG_WIN_PTS = 8.0  # threshold used to classify a trade as a "nice winner"
FOLLOWUP_WINDOWS_S = [5, 10, 30]
# Realistic near-term horizons to bound MFE/MAE search and the grid/stop-ref
# backtests -- letting a "1s absorption scalp" trade run unbounded to the
# END OF THE WHOLE DATASET (weeks later) just measures market drift, not
# the setup's actual outcome, so every horizon below is capped in seconds.
MFE_HORIZONS_S = [300, 900, 1800, 3600, 14400]   # 5m, 15m, 30m, 1h, 4h
GRID_HORIZON_S = 3600    # 1h cap for the stop/target grid search + stop-ref comparison


def _load():
    df = pd.read_csv(CSV_1S, index_col="Time_PT", parse_dates=True)
    trig = pd.read_csv(TRIGGERS_CSV, parse_dates=["time_pt"])
    pos_by_ts = {ts: i for i, ts in enumerate(df.index)}
    return df, trig, pos_by_ts


# ---------------------------------------------------------------------------
# 1. MFE / MAE-before-peak profiling
# ---------------------------------------------------------------------------
def _mfe_mae_at_horizon(highs, lows, i0, n, direction, entry, horizon_s):
    end = min(i0 + 1 + horizon_s, n)
    if i0 + 1 >= end:
        return None
    seg_high = highs[i0 + 1:end]
    seg_low = lows[i0 + 1:end]
    if direction == 1:
        fav = seg_high - entry
        adv = entry - seg_low
    else:
        fav = entry - seg_low
        adv = seg_high - entry
    peak_idx = int(np.argmax(fav))
    mfe = fav[peak_idx]
    mae_before_peak = float(np.max(adv[:peak_idx + 1]))
    return mfe, mae_before_peak, peak_idx + 1


def profile_mfe_mae(df, trig, pos_by_ts, horizon_s=3600):
    """MFE/MAE-before-peak bounded to `horizon_s` seconds forward (default
    1h) -- an absorption scalp's near-term outcome, not multi-week drift."""
    highs = df["High"].to_numpy(float)
    lows = df["Low"].to_numpy(float)
    n = len(df)
    rows = []
    for _, t in trig.iterrows():
        i0 = pos_by_ts.get(t["time_pt"])
        if i0 is None:
            continue
        direction = 1 if t["direction"] == "LONG" else -1
        entry = float(t["trigger_close"])
        res = _mfe_mae_at_horizon(highs, lows, i0, n, direction, entry, horizon_s)
        if res is None:
            continue
        mfe, mae_before_peak, time_to_peak = res
        rows.append({
            "time_pt": t["time_pt"], "direction": t["direction"],
            "entry": entry, "level_type": t["level_type"],
            "confluence_count": t["confluence_count"], "vol_z": t["vol_z"],
            "mfe_pts": round(mfe, 2), "mae_before_peak_pts": round(mae_before_peak, 2),
            "time_to_peak_s": time_to_peak,
        })
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(_HERE, "mfe_mae_profile.csv"), index=False)

    print("=" * 78)
    print(f"1. MFE / MAE-before-peak profile, bounded to {horizon_s}s ({horizon_s/60:.0f} min) forward")
    print("=" * 78)
    print(out[["time_pt", "direction", "mfe_pts", "mae_before_peak_pts",
               "time_to_peak_s"]].sort_values("mfe_pts", ascending=False).to_string(index=False))

    big = out[out["mfe_pts"] >= BIG_WIN_PTS]
    print(f"\n'Big winner' trades (MFE >= {BIG_WIN_PTS} pts within {horizon_s/60:.0f} min): {len(big)}/{len(out)}")
    if len(big):
        print(f"\nAmong big winners, MAE-before-peak (i.e. the stop size actually "
              f"needed to survive to the peak):")
        print(big["mae_before_peak_pts"].describe())
        pct90 = big["mae_before_peak_pts"].quantile(0.90)
        print(f"90th percentile MAE-before-peak among big winners: {pct90:.2f} pts "
              f"({pct90/TICK:.0f} ticks) -- a stop this size would have caught "
              f"~90% of the big winners without being shaken out early.")
    print()
    return out


def mfe_across_horizons(df, trig, pos_by_ts):
    """Quick summary of how many trades reach BIG_WIN_PTS at each of several
    horizons, to see how much of the earlier (unbounded) MFE numbers were
    actually just long-run market drift rather than a near-term outcome."""
    highs = df["High"].to_numpy(float)
    lows = df["Low"].to_numpy(float)
    n = len(df)
    print("=" * 78)
    print("Sanity check: 'big winner' rate and median MFE across several bounded horizons")
    print("=" * 78)
    for h in MFE_HORIZONS_S:
        mfes = []
        for _, t in trig.iterrows():
            i0 = pos_by_ts.get(t["time_pt"])
            if i0 is None:
                continue
            direction = 1 if t["direction"] == "LONG" else -1
            entry = float(t["trigger_close"])
            res = _mfe_mae_at_horizon(highs, lows, i0, n, direction, entry, h)
            if res is None:
                continue
            mfes.append(res[0])
        mfes = np.array(mfes)
        print(f"  horizon={h:>6}s ({h/60:>5.0f}m): median MFE={np.median(mfes):6.2f} pts, "
              f"% reaching {BIG_WIN_PTS}pts = {(mfes >= BIG_WIN_PTS).mean():.1%}, "
              f"% reaching 3pts = {(mfes >= 3).mean():.1%}")
    print()


# ---------------------------------------------------------------------------
# 2. Fixed stop x fixed target grid search
# ---------------------------------------------------------------------------
def _simulate_fixed(highs, lows, i0, n, direction, entry, stop_pts, target_pts, horizon_s=None):
    stop = entry - direction * stop_pts
    target = entry + direction * target_pts
    end = n if horizon_s is None else min(i0 + 1 + horizon_s, n)
    if i0 + 1 >= end:
        return "open", None
    seg_high = highs[i0 + 1:end]
    seg_low = lows[i0 + 1:end]
    if direction == 1:
        stop_hit = seg_low <= stop
        target_hit = seg_high >= target
    else:
        stop_hit = seg_high >= stop
        target_hit = seg_low <= target
    s_idx = int(np.argmax(stop_hit)) if stop_hit.any() else None
    t_idx = int(np.argmax(target_hit)) if target_hit.any() else None
    if s_idx is None and t_idx is None:
        return "open", None
    if s_idx is not None and (t_idx is None or s_idx <= t_idx):
        return "stop", -stop_pts
    return "target", target_pts


def grid_search(df, trig, pos_by_ts):
    highs = df["High"].to_numpy(float)
    lows = df["Low"].to_numpy(float)
    n = len(df)

    stop_grid = [0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    target_grid = [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0]

    trades = []
    for _, t in trig.iterrows():
        i0 = pos_by_ts.get(t["time_pt"])
        if i0 is None:
            continue
        trades.append((i0, 1 if t["direction"] == "LONG" else -1, float(t["trigger_close"])))

    results = []
    for stop_pts in stop_grid:
        for target_pts in target_grid:
            pnls = []
            wins = losses = opens = 0
            for i0, direction, entry in trades:
                outcome, pnl_pts = _simulate_fixed(highs, lows, i0, n, direction, entry, stop_pts, target_pts,
                                                    horizon_s=GRID_HORIZON_S)
                if outcome == "open":
                    opens += 1
                    continue
                pnls.append(pnl_pts)
                if outcome == "target":
                    wins += 1
                else:
                    losses += 1
            closed = wins + losses
            if closed == 0:
                continue
            total_pts = sum(pnls)
            avg_R = np.mean([p / stop_pts for p in pnls])
            results.append({
                "stop_pts": stop_pts, "target_pts": target_pts, "rr": round(target_pts / stop_pts, 2),
                "closed": closed, "wins": wins, "losses": losses, "open": opens,
                "win_rate": round(wins / closed, 3), "total_pts": round(total_pts, 2),
                "avg_R": round(avg_R, 3), "total_R": round(avg_R * closed, 2),
            })
    out = pd.DataFrame(results)
    out.to_csv(os.path.join(_HERE, "stop_target_grid.csv"), index=False)

    print("=" * 78)
    print(f"2. Fixed stop x fixed target grid search (points, capped at {GRID_HORIZON_S/60:.0f}min horizon)")
    print("=" * 78)
    print("\nTop 10 combos by total R:")
    print(out.sort_values("total_R", ascending=False).head(10).to_string(index=False))
    print("\nTop 10 combos by avg R per trade (min 20 closed trades):")
    print(out[out["closed"] >= 20].sort_values("avg_R", ascending=False).head(10).to_string(index=False))
    print()
    return out


# ---------------------------------------------------------------------------
# 3. Stop-reference comparison: impulse extreme vs cluster extreme
# ---------------------------------------------------------------------------
def stop_reference_comparison(df, trig, pos_by_ts):
    highs = df["High"].to_numpy(float)
    lows = df["Low"].to_numpy(float)
    n = len(df)
    target_grid = [5.0, 8.0, 10.0, 12.0]
    buffer_ticks = 1

    trades = []
    for _, t in trig.iterrows():
        i0 = pos_by_ts.get(t["time_pt"])
        if i0 is None:
            continue
        direction = 1 if t["direction"] == "LONG" else -1
        entry = float(t["trigger_close"])
        impulse_extreme = float(t["tested_price"])
        prices = [float(p) for p in str(t["confluence_prices"]).split(";")]
        cluster_extreme = min(prices) if direction == 1 else max(prices)
        trades.append((i0, direction, entry, impulse_extreme, cluster_extreme))

    print("=" * 78)
    print("3. Stop reference comparison: impulse extreme vs cluster (farthest-level) extreme")
    print("=" * 78)

    rows = []
    for ref_name, ref_getter in [("impulse_extreme", lambda tr: tr[3]), ("cluster_extreme", lambda tr: tr[4])]:
        for target_pts in target_grid:
            pnls_R = []
            wins = losses = opens = 0
            risk_list = []
            for tr in trades:
                i0, direction, entry, impulse_extreme, cluster_extreme = tr
                ref_price = ref_getter(tr)
                stop = ref_price - direction * buffer_ticks * TICK
                risk = abs(entry - stop)
                if risk <= 0:
                    continue
                risk_list.append(risk)
                stop_pts = risk
                outcome, pnl_pts = _simulate_fixed(highs, lows, i0, n, direction, entry, stop_pts, target_pts,
                                                    horizon_s=GRID_HORIZON_S)
                if outcome == "open":
                    opens += 1
                    continue
                pnls_R.append(pnl_pts / risk)
                if outcome == "target":
                    wins += 1
                else:
                    losses += 1
            closed = wins + losses
            if closed == 0:
                continue
            rows.append({
                "stop_ref": ref_name, "target_pts": target_pts,
                "avg_risk_pts": round(np.mean(risk_list), 2),
                "closed": closed, "wins": wins, "losses": losses, "open": opens,
                "win_rate": round(wins / closed, 3),
                "avg_R": round(np.mean(pnls_R), 3), "total_R": round(sum(pnls_R), 2),
            })
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    print()
    return out


# ---------------------------------------------------------------------------
# 4. Volume / follow-through fine-tuning
# ---------------------------------------------------------------------------
def volume_followthrough_analysis(df, trig, pos_by_ts, mfe_profile):
    closes = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)

    feat_rows = []
    merged = trig.merge(mfe_profile[["time_pt", "mfe_pts", "mae_before_peak_pts"]], on="time_pt")
    for _, t in merged.iterrows():
        i0 = pos_by_ts.get(t["time_pt"])
        if i0 is None:
            continue
        direction = 1 if t["direction"] == "LONG" else -1
        entry = float(t["trigger_close"])
        feat = {
            "time_pt": t["time_pt"], "vol_z": t["vol_z"], "volume": t["volume"],
            "run_delta_sum": abs(t["run_delta_sum"]), "confluence_count": t["confluence_count"],
            "mfe_pts": t["mfe_pts"], "big_win": t["mfe_pts"] >= BIG_WIN_PTS,
        }
        for w in FOLLOWUP_WINDOWS_S:
            j = min(i0 + w, n - 1)
            feat[f"followup_move_{w}s"] = direction * (closes[j] - entry)
            feat[f"followup_vol_{w}s"] = float(np.sum(vol[i0 + 1:j + 1]))
        feat_rows.append(feat)
    fdf = pd.DataFrame(feat_rows)
    fdf.to_csv(os.path.join(_HERE, "volume_followthrough_features.csv"), index=False)

    print("=" * 78)
    print("4. Volume / follow-through analysis vs. big-winner classification "
          f"(MFE >= {BIG_WIN_PTS} pts)")
    print("=" * 78)
    print(f"\nBig winners: {fdf['big_win'].sum()}/{len(fdf)}\n")

    def tercile_table(col):
        try:
            fdf["_t"] = pd.qcut(fdf[col], 3, labels=["low", "mid", "high"], duplicates="drop")
        except ValueError:
            print(f"  {col}: not enough distinct values for terciles, skipping")
            return
        g = fdf.groupby("_t", observed=True)["big_win"].agg(["mean", "count"])
        print(f"-- {col} tercile vs big-winner rate --")
        print(g.rename(columns={"mean": "big_win_rate"}).to_string())
        print()

    for col in ["vol_z", "volume", "run_delta_sum", "confluence_count"]:
        tercile_table(col)
    for w in FOLLOWUP_WINDOWS_S:
        tercile_table(f"followup_move_{w}s")
        tercile_table(f"followup_vol_{w}s")

    print("-- Simple correlations with mfe_pts --")
    corr_cols = ["vol_z", "volume", "run_delta_sum", "confluence_count"] + \
                [f"followup_move_{w}s" for w in FOLLOWUP_WINDOWS_S] + \
                [f"followup_vol_{w}s" for w in FOLLOWUP_WINDOWS_S]
    print(fdf[corr_cols + ["mfe_pts"]].corr()["mfe_pts"].sort_values(ascending=False).to_string())
    print()
    return fdf


if __name__ == "__main__":
    df, trig, pos_by_ts = _load()
    print(f"Loaded {len(df)} 1s bars, {len(trig)} signals.\n")
    mfe_across_horizons(df, trig, pos_by_ts)
    mfe_profile = profile_mfe_mae(df, trig, pos_by_ts, horizon_s=3600)
    grid_search(df, trig, pos_by_ts)
    stop_reference_comparison(df, trig, pos_by_ts)
    volume_followthrough_analysis(df, trig, pos_by_ts, mfe_profile)
