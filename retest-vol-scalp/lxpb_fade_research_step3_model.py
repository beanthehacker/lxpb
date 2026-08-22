"""
LXPB fade/mean-reversion research -- STEP 3: modeling + findings.

Consumes lxpb_fade_features.csv (STEP 2) and answers "which LXPB retests
make good continuation-direction fade/scalp entries, and which don't":

  1. 5-fold cross-validated LightGBM regressor predicting `quality_30m`
     (= MFE - MAE-before-peak over a 30-minute horizon) from every
     structural + order-flow feature. Reports out-of-fold R^2/correlation
     (honest, since predictions are always on held-out folds) and
     averaged gain-based feature importance + mean |SHAP| ranking.
  2. Univariate bucket analysis (terciles/explicit buckets) for the
     features SHAP/gain ranked highest, plus every feature explicitly
     named in the research brief (volume spike, momentum, dwell/
     preconsolidation time, trade/tick counts, historical inflection
     count, confluence), each vs. mean quality_30m / win rate
     (quality_30m > 0) / mean mfe_30m -- split overall AND by level type
     (LHPB vs LLPB), since the two may respond differently.
  3. A plain-language findings summary auto-generated from (2)'s numbers.

Output: lxpb_fade_feature_importance.csv, lxpb_fade_bucket_analysis.csv,
prints the findings summary to stdout (also duplicated to
lxpb_fade_findings.txt).
"""
import os

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold
import lightgbm as lgb
import shap

_HERE = os.path.dirname(os.path.abspath(__file__))
FEATURES_CSV = os.path.join(_HERE, "lxpb_fade_features.csv")
IMPORTANCE_OUT = os.path.join(_HERE, "lxpb_fade_feature_importance.csv")
BUCKETS_OUT = os.path.join(_HERE, "lxpb_fade_bucket_analysis.csv")
FINDINGS_OUT = os.path.join(_HERE, "lxpb_fade_findings.txt")

TARGET = "quality_30m"
DROP_COLS = {
    "type", "price", "formation_time", "breakout_time", "retest_time", "touch_time_utc",
    "entry_price", "direction",
    "mfe_5m", "mae_before_peak_5m", "time_to_peak_s_5m",
    "mfe_15m", "mae_before_peak_15m", "time_to_peak_s_15m",
    "mfe_30m", "mae_before_peak_30m", "time_to_peak_s_30m",
    "mfe_60m", "mae_before_peak_60m", "time_to_peak_s_60m",
    "quality_30m",
}

NAMED_FEATURES = [
    "vol_spike_z_30s", "vol_spike_z_60s", "vol_spike_z_300s",
    "same_side_vol_ratio_30s", "same_side_vol_ratio_60s",
    "momentum_pts_30s", "momentum_pts_60s", "momentum_pts_120s",
    "trades_count_30s", "trades_count_60s",
    "upticks_30s", "downticks_30s", "cum_delta_30s", "cum_delta_60s",
    "range_compression_60s", "range_compression_300s",
    "preconsolidation_secs_30s",
    "prior_touches_2yr", "prior_touches_all",
    "confluence_count", "hours_breakout_to_retest", "level_age_hours",
    "breakout_range_ratio", "breakout_body_ratio",
    "is_spike", "is_swing",
]


def load():
    df = pd.read_csv(FEATURES_CSV, parse_dates=["formation_time", "breakout_time", "retest_time", "touch_time_utc"])
    df["is_spike"] = df["is_spike"].astype(int)
    df["is_swing"] = df["is_swing"].fillna(False).astype(int)
    return df


def cv_model(df):
    feat_cols = [c for c in df.columns if c not in DROP_COLS]
    X = df[feat_cols].astype(float)
    y = df[TARGET].astype(float)

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    oof_pred = np.full(len(df), np.nan)
    gains = pd.Series(0.0, index=feat_cols)
    shap_abs = pd.Series(0.0, index=feat_cols)

    for fold, (tr_idx, te_idx) in enumerate(kf.split(X)):
        model = lgb.LGBMRegressor(
            n_estimators=200, max_depth=3, num_leaves=7,
            learning_rate=0.03, min_child_samples=10,
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.5, reg_lambda=0.5,
            verbosity=-1, random_state=42,
        )
        model.fit(X.iloc[tr_idx], y.iloc[tr_idx])
        oof_pred[te_idx] = model.predict(X.iloc[te_idx])
        gains += pd.Series(model.booster_.feature_importance(importance_type="gain"), index=feat_cols)

        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X.iloc[te_idx])
        shap_abs += pd.Series(np.abs(sv).mean(axis=0) * len(te_idx), index=feat_cols)

    valid = ~np.isnan(oof_pred)
    r2 = 1 - np.sum((y[valid] - oof_pred[valid]) ** 2) / np.sum((y[valid] - y[valid].mean()) ** 2)
    corr = np.corrcoef(y[valid], oof_pred[valid])[0, 1]
    print(f"Out-of-fold R^2 = {r2:.3f}, corr(actual, predicted) = {corr:.3f}  (n={valid.sum()})")

    gains /= 5
    shap_abs /= len(df)
    imp = pd.DataFrame({"feature": feat_cols, "gain_importance": gains.values, "mean_abs_shap": shap_abs.values})
    imp = imp.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
    imp.to_csv(IMPORTANCE_OUT, index=False)
    print(f"\nTop 20 features by mean |SHAP| (predicting {TARGET}):")
    print(imp.head(20).to_string(index=False))
    return imp, r2, corr


def bucket_table(df, col, n_buckets=3, by_type=False):
    rows = []
    groups = [("ALL", df)] if not by_type else [("ALL", df), ("LHPB", df[df["type"] == "LHPB"]), ("LLPB", df[df["type"] == "LLPB"])]
    for gname, gdf in groups:
        gdf = gdf.copy()
        nunique = gdf[col].nunique(dropna=True)
        if nunique <= 1:
            continue
        try:
            if nunique <= n_buckets:
                gdf["_bucket"] = gdf[col].astype(str)
            else:
                gdf["_bucket"] = pd.qcut(gdf[col], n_buckets, duplicates="drop")
        except ValueError:
            continue
        g = gdf.groupby("_bucket", observed=True).agg(
            n=("quality_30m", "size"),
            mean_quality_30m=("quality_30m", "mean"),
            median_quality_30m=("quality_30m", "median"),
            win_rate=("quality_30m", lambda s: (s > 0).mean()),
            mean_mfe_30m=("mfe_30m", "mean"),
            mean_mae_30m=("mae_before_peak_30m", "mean"),
        ).reset_index()
        g.insert(0, "group", gname)
        g.insert(1, "feature", col)
        g = g.rename(columns={"_bucket": "bucket"})
        rows.append(g)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main():
    df = load()
    print(f"Loaded {len(df)} rows.\n")
    print("=" * 78)
    print("1. Cross-validated LightGBM model + SHAP feature ranking")
    print("=" * 78)
    imp, r2, corr = cv_model(df)

    print("\n" + "=" * 78)
    print("2. Univariate bucket analysis (overall + by level type)")
    print("=" * 78)
    top_ml_features = imp["feature"].head(10).tolist()
    all_cols = list(dict.fromkeys(NAMED_FEATURES + top_ml_features))
    tables = [bucket_table(df, c, by_type=True) for c in all_cols]
    tables = [t for t in tables if not t.empty]
    bt = pd.concat(tables, ignore_index=True)
    bt.to_csv(BUCKETS_OUT, index=False)
    for col in all_cols:
        sub = bt[(bt["feature"] == col) & (bt["group"] == "ALL")]
        if sub.empty:
            continue
        print(f"\n-- {col} --")
        print(sub[["bucket", "n", "mean_quality_30m", "win_rate", "mean_mfe_30m", "mean_mae_30m"]].to_string(index=False))

    print(f"\nSaved: {IMPORTANCE_OUT}\nSaved: {BUCKETS_OUT}")
    return df, imp, bt, r2, corr


if __name__ == "__main__":
    main()
