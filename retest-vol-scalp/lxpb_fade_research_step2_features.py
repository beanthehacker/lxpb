"""
LXPB fade/mean-reversion research -- STEP 2: order-flow (1s) features + labels.

Reads lxpb_fade_levels.csv (STEP 1's structural features, one row per
in-window non-gap completed H1 LXPB retest) and ../lxpb-es-vol/ES_full_1s.csv
(real scid-derived 1-second OHLCV + BidVolume/AskVolume/Trades/Delta,
2026-05-28 .. 2026-08-17 -- the only span with genuine order-flow data),
and for each retest:

  1. Locates the exact 1-second bar within the retest's H1 hour where
     price actually touched entry_price (lxpb.py's own touch rule, at 1s
     resolution) -- the real trade-entry instant.
  2. Computes PRE-TOUCH order-flow features (no lookahead) over several
     backward windows (10s/30s/60s/120s/300s):
       - vol_spike_z_<w>       : z-score of that window's total Volume vs
                                 a trailing 20-window-of-`w` baseline
                                 ("volume spike coming into the retest")
       - same_side_vol_ratio_<w>: for the continuation direction, the
                                 fraction of volume that is same-side
                                 (BidVolume for LONG/LHPB, AskVolume for
                                 SHORT/LLPB) -- i.e. is the aggressor flow
                                 already leaning the way the trade needs?
       - momentum_pts_<w>      : signed price change over the window,
                                 projected onto the trade direction
                                 ("momentum coming into the level")
       - trades_count_<w>      : sum of the Trades column ("no. of trades")
       - upticks_<w>/downticks_<w>: count of 1s bars with positive/
                                 negative Delta (Ask-Bid volume imbalance,
                                 the closest proxy to tick direction
                                 available from scid bid/ask volume)
       - cum_delta_<w>         : signed sum of Delta, projected onto trade
                                 direction (net aggressor pressure)
       - range_compression_<w> : mean 1s bar range in the window vs a
                                 longer (600s) baseline (volatility
                                 contraction into the level)
       - preconsolidation_secs : of the preceding 30s, how many were
                                 already within CONSOL_TICKS of the level
                                 ("time already spent at the level" measured
                                 strictly before the official touch, to
                                 avoid outcome leakage)
  3. Computes forward MFE / MAE-before-peak (continuation-direction trade,
     entry = entry_price) at several bounded horizons (5/15/30/60 min),
     plus a simple continuous quality score `quality_30m = mfe_30m -
     mae_before_peak_30m` used as the primary regression target.

Output: lxpb_fade_features.csv.
"""
import os

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS_CSV = os.path.join(_HERE, "lxpb_fade_levels.csv")
CSV_1S = os.path.join(os.path.dirname(_HERE), "lxpb-es-vol", "ES_full_1s.csv")
CACHE_1S_PARQUET = os.path.join(_HERE, "_cache_es_full_1s.parquet")
OUT_CSV = os.path.join(_HERE, "lxpb_fade_features.csv")

TICK = 0.25
CONSOL_TICKS = 6           # +/- ticks counted as "at the level" for preconsolidation_secs
BACK_WINDOWS_S = [10, 30, 60, 120, 300]
BASELINE_WINDOW_S = 600    # long baseline for vol z-score / range compression
MFE_HORIZONS_S = {"5m": 300, "15m": 900, "30m": 1800, "60m": 3600}
TOUCH_SEARCH_HOURS = 1     # search only within the retest's own H1 hour, per lxpb.py semantics


def load_1s():
    if os.path.exists(CACHE_1S_PARQUET):
        print(f"Loading cached 1s data from {CACHE_1S_PARQUET} ...")
        df = pd.read_parquet(CACHE_1S_PARQUET)
    else:
        print(f"Loading {CSV_1S} (first run, will cache to parquet) ...")
        df = pd.read_csv(CSV_1S, parse_dates=["Time_PT"])
        df["time_utc"] = df["Time_PT"].dt.tz_convert("UTC").dt.tz_localize(None)
        df = df.sort_values("time_utc").reset_index(drop=True)
        df.to_parquet(CACHE_1S_PARQUET)
    print(f"  {len(df)} 1s bars, {df['time_utc'].min()} -> {df['time_utc'].max()}")
    return df


def find_touch_index(highs, lows, pos_by_time, retest_time, entry_price, hours=TOUCH_SEARCH_HOURS):
    """First 1s index within [retest_time, retest_time+hours) whose range
    contains entry_price (lxpb.py's own H1-resolution touch, refined to 1s)."""
    i0 = pos_by_time.get(retest_time)
    if i0 is None:
        # retest_time may fall between 1s bars if data has gaps (thin overnight
        # liquidity) -- fall back to nearest bar at/after retest_time.
        return None
    i1 = min(i0 + hours * 3600, len(highs))
    seg_h = highs[i0:i1]
    seg_l = lows[i0:i1]
    touched = (seg_l <= entry_price) & (entry_price <= seg_h)
    if not touched.any():
        return None
    return i0 + int(np.argmax(touched))


def backward_features(idx, direction, closes, highs, lows, opens, vol, bidvol, askvol, trades, delta):
    feats = {}
    baseline_start = max(0, idx - BASELINE_WINDOW_S)
    base_vol = vol[baseline_start:idx]
    base_range = (highs[baseline_start:idx] - lows[baseline_start:idx])
    base_vol_per10 = None
    for w in BACK_WINDOWS_S:
        s = max(0, idx - w + 1)
        seg_vol = vol[s:idx + 1]
        seg_bid = bidvol[s:idx + 1]
        seg_ask = askvol[s:idx + 1]
        seg_trades = trades[s:idx + 1]
        seg_delta = delta[s:idx + 1]
        seg_close = closes[s:idx + 1]
        seg_range = highs[s:idx + 1] - lows[s:idx + 1]

        total_vol = float(seg_vol.sum())
        # baseline: mean/std of trailing non-overlapping w-second sums over BASELINE_WINDOW_S
        if len(base_vol) >= w:
            n_chunks = len(base_vol) // w
            chunk_sums = base_vol[-n_chunks * w:].reshape(n_chunks, w).sum(axis=1)
            mu, sigma = chunk_sums.mean(), chunk_sums.std()
            vol_z = (total_vol - mu) / sigma if sigma > 1e-9 else 0.0
        else:
            vol_z = np.nan
        feats[f"vol_spike_z_{w}s"] = vol_z

        same_side = seg_bid.sum() if direction == 1 else seg_ask.sum()
        feats[f"same_side_vol_ratio_{w}s"] = float(same_side) / total_vol if total_vol > 0 else np.nan

        raw_move = float(seg_close[-1] - seg_close[0]) if len(seg_close) > 1 else 0.0
        feats[f"momentum_pts_{w}s"] = direction * raw_move

        feats[f"trades_count_{w}s"] = float(seg_trades.sum())
        feats[f"upticks_{w}s"] = int((seg_delta > 0).sum())
        feats[f"downticks_{w}s"] = int((seg_delta < 0).sum())
        feats[f"cum_delta_{w}s"] = direction * float(seg_delta.sum())

        mean_range = float(seg_range.mean()) if len(seg_range) else np.nan
        base_mean_range = float(base_range.mean()) if len(base_range) else np.nan
        feats[f"range_compression_{w}s"] = (mean_range / base_mean_range) if base_mean_range and base_mean_range > 1e-9 else np.nan

    # preconsolidation: of the preceding 30s (strictly before touch second), how many
    # were already within CONSOL_TICKS of... (level price passed in via closure below)
    return feats


def preconsolidation_secs(idx, level_price, highs, lows, window_s=30, tol_ticks=CONSOL_TICKS):
    s = max(0, idx - window_s)
    seg_h = highs[s:idx]
    seg_l = lows[s:idx]
    tol = tol_ticks * TICK
    near = (seg_l <= level_price + tol) & (seg_h >= level_price - tol)
    return int(near.sum())


def mfe_mae(idx, direction, entry, highs, lows, n, horizon_s):
    end = min(idx + 1 + horizon_s, n)
    if idx + 1 >= end:
        return np.nan, np.nan, np.nan
    seg_h = highs[idx + 1:end]
    seg_l = lows[idx + 1:end]
    if direction == 1:
        fav = seg_h - entry
        adv = entry - seg_l
    else:
        fav = entry - seg_l
        adv = seg_h - entry
    peak_idx = int(np.argmax(fav))
    mfe = float(fav[peak_idx])
    mae_before_peak = float(np.max(adv[:peak_idx + 1]))
    return mfe, mae_before_peak, peak_idx + 1


def main():
    levels = pd.read_csv(LEVELS_CSV, parse_dates=["formation_time", "breakout_time", "retest_time"])
    levels = levels[~(levels["is_gap_breakout"] | levels["is_gap_retest"])].reset_index(drop=True)
    print(f"Non-gap in-window retests: {len(levels)}")

    df1s = load_1s()
    highs = df1s["High"].to_numpy(float)
    lows = df1s["Low"].to_numpy(float)
    opens = df1s["Open"].to_numpy(float)
    closes = df1s["Close"].to_numpy(float)
    vol = df1s["Volume"].to_numpy(float)
    bidvol = df1s["BidVolume"].to_numpy(float)
    askvol = df1s["AskVolume"].to_numpy(float)
    trades = df1s["Trades"].to_numpy(float)
    delta = df1s["Delta"].to_numpy(float)
    n = len(df1s)
    pos_by_time = pd.Series(np.arange(n), index=df1s["time_utc"]).to_dict()

    rows = []
    n_no_touch = 0
    for _, r in levels.iterrows():
        idx = find_touch_index(highs, lows, pos_by_time, r["retest_time"], r["entry_price"])
        if idx is None:
            n_no_touch += 1
            continue
        direction = int(r["direction"])
        entry = float(r["entry_price"])

        feats = backward_features(idx, direction, closes, highs, lows, opens, vol, bidvol, askvol, trades, delta)
        feats["preconsolidation_secs_30s"] = preconsolidation_secs(idx, entry, highs, lows)

        out_row = {
            "type": r["type"], "price": r["price"], "direction": direction,
            "formation_time": r["formation_time"], "breakout_time": r["breakout_time"],
            "retest_time": r["retest_time"], "touch_time_utc": df1s["time_utc"].iloc[idx],
            "entry_price": entry,
            "is_spike": r["is_spike"], "is_swing": r["is_swing"],
            "hours_breakout_to_retest": r["hours_breakout_to_retest"],
            "level_age_hours": r["level_age_hours"],
            "breakout_range_ratio": r["breakout_range_ratio"],
            "breakout_body_ratio": r["breakout_body_ratio"],
            "prior_touches_2yr": r["prior_touches_2yr"],
            "prior_touches_all": r["prior_touches_all"],
            "confluence_count": r["confluence_count"],
            **feats,
        }
        for label, hs in MFE_HORIZONS_S.items():
            mfe, mae, t2peak = mfe_mae(idx, direction, entry, highs, lows, n, hs)
            out_row[f"mfe_{label}"] = mfe
            out_row[f"mae_before_peak_{label}"] = mae
            out_row[f"time_to_peak_s_{label}"] = t2peak
        out_row["quality_30m"] = out_row["mfe_30m"] - out_row["mae_before_peak_30m"] if pd.notna(out_row.get("mfe_30m")) else np.nan
        rows.append(out_row)

    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False)
    print(f"Touch not found for {n_no_touch} retests (thin/no 1s data in that hour) -- excluded.")
    print(f"Saved {len(out)} rows -> {OUT_CSV}")


if __name__ == "__main__":
    main()
