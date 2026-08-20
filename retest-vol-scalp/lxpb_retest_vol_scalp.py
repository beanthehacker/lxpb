"""
LXPB H1 retest + 1-second RAW volume-threshold scalp: signal build, parallel
stop/target grid search, and an HTML trade report.

Strategy (per spec)
--------------------
1. H1 LXPB retest via ../../lxpb.py's canonical `detect_lxpb_h1` (default
   4h MIN_HOURS_BEFORE_RETEST rule, unmodified).
2. Gap instances excluded -- mirrors the filter introduced in commit
   6bd75bb / ../../label-review/render_lxpb_retest_1s_report.py:
     - gap BREAKOUT: the level's own breakout bar opened/stayed entirely
       beyond the level (bar.low > price for LHPB, bar.high < price for
       LLPB) -- lxpb.py Phase 2's gap-breakout branch.
     - gap RETEST (gap-over): the retest bar gapped clean past the level
       instead of actually trading at it -- detected via
       NOT(retest_low <= entry_price <= retest_high), per lxpb.py's own
       Phase 3 gap-over docstring.
   Both are real state transitions in lxpb.py's own state machine (not
   bugs) -- excluded here only because there is no actual 1-second touch
   to check volume at.
3. On the exact 1-second bar the retest actually touches the level:
   signal fires if
     LHPB (long)  and BidVolume > VOL_THRESHOLD
     LLPB (short) and AskVolume > VOL_THRESHOLD
   A flat, ABSOLUTE threshold (default 300 contracts) -- deliberately no
   z-score / rolling baseline, per spec.
4. Entry = entry_price (the level itself) -- the touch bar's range
   already contains it by construction.
5. Stop/target grid-searched in ticks (stop 1..N ticks, target 4..M
   ticks -- 1 tick = 0.25, 4 ticks = 1pt -- both stepped 1 tick at a
   time), parallelized across worker processes.

Scope (per request): only June, July, August 2026 retests are scored in
this run. Earlier .scid history (older quarterly contracts) can be added
in a follow-up run once those files are available/relevant.

Data sources
------------
- H1 levels/retests: ../../label-review/data/es-h1-2026-backadjusted.csv
  (2026-only, back-adjusted, jump-free continuous contract -- see that
  file's builder script, ../../label-review/data/build_es_h1_2026_backadjusted.py,
  for the reverse-engineered TradingView roll-timing/offset methodology).
- 1s Bid/AskVolume + price: real ticks read directly from the local
  Sierra Chart .scid files for whichever contract (EPM26/EPU26 -- EPH26
  is never needed since it rolled out in March 2026, well before this
  script's June-Aug scope) was actually front-month at each point in
  time, using the SAME reverse-engineered roll-switch instants
  (`roll_switch_utc`/`CONTRACTS`/`TV_GROUND_TRUTH_OFFSETS`) that
  build_es_h1_2026_backadjusted.py uses to build the H1 series --
  resampled to 1s and back-adjusted (each contract-segment's own offset
  added to its OHLC) into ONE continuous, jump-free 1s series for the
  whole June-Aug window. This means `entry_price` (already back-adjusted)
  can be compared directly against these 1s bars' High/Low with no
  further raw-price conversion needed, even for a trade whose forward
  simulation window straddles the actual M26->U26 roll instant
  (2026-06-15 22:00 UTC).
  This mirrors label-review/render_lxpb_retest_1s_report.py's contract-
  splicing helpers (`_contract_index_for`, `_offset_for_ts`,
  `_ticks_for_window`, `_find_touch_time`) but is reimplemented standalone
  here (continuous/back-adjusted rather than per-contract/raw) so this
  strategy/backtest script has no dependency on that chart-rendering
  module or its own external `daily-analysis` pattern-detection imports.
"""
import os
import sys
import json
import argparse
import multiprocessing as mp

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_LABEL_REVIEW_DATA_DIR = os.path.join(_REPO_ROOT, "label-review", "data")
for _p in (_REPO_ROOT, _LABEL_REVIEW_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lxpb as L  # noqa: E402 -- ../../lxpb.py, canonical H1 LXPB detector
import build_es_h1_2026_backadjusted as B26  # noqa: E402

sys.path.insert(0, r"D:\acheron\AcheronUtils")  # scidReader.py lives there
from scidReader import get_scid_df  # noqa: E402

SCID_DIR = r"D:\SC\Data"
H1_CSV = os.path.join(_LABEL_REVIEW_DATA_DIR, "es-h1-2026-backadjusted.csv")
TICK = 0.25
VOL_THRESHOLD_DEFAULT = 300     # flat BidVolume/AskVolume threshold @ the exact retest 1s bar
MONTHS_DEFAULT = (6, 7, 8)      # June, July, August 2026 -- rest of the .scid history TBD
YEAR = 2026

STOP_TICKS_GRID = list(range(1, 21))       # 1..20 ticks (0.25 .. 5.00 pts)
TARGET_TICKS_GRID = list(range(4, 81))     # 4..80 ticks (1.00 .. 20.00 pts), 1-tick steps
GRID_HORIZON_S = 3600                       # 1h forward cap for the grid search (scalp, not swing)
N_WORKERS_DEFAULT = 4

SIGNALS_OUT = os.path.join(_HERE, "lxpb_retest_vol_scalp_signals.csv")
GRID_OUT_PREFIX = os.path.join(_HERE, "lxpb_retest_vol_scalp_grid")
REPORT_OUT = os.path.join(_HERE, "lxpb_retest_vol_scalp_report.html")

# EPH26 (March 2026 contract) is intentionally excluded -- it rolled out
# in March 2026, entirely before this script's June-Aug 2026 scope.
CONTRACTS_NEEDED = [c for c in B26.CONTRACTS if c[0] != "EPH26"]


# ---------------------------------------------------------------------------
# Contract splicing -> ONE continuous, back-adjusted 1s series
# ---------------------------------------------------------------------------
_OWN_ROLL_CACHE = None


def _own_roll():
    """own_roll[i] = exact UTC instant CONTRACTS_NEEDED[i] rolls OUT and
    CONTRACTS_NEEDED[i+1] becomes front month (same rule as
    build_es_h1_2026_backadjusted.py's `own_roll`), memoized."""
    global _OWN_ROLL_CACHE
    if _OWN_ROLL_CACHE is None:
        _OWN_ROLL_CACHE = [B26.roll_switch_utc(y, m) for _, y, m in CONTRACTS_NEEDED]
    return _OWN_ROLL_CACHE


def _segment_for(i):
    roll = _own_roll()
    n = len(CONTRACTS_NEEDED)
    start = roll[i - 1] if i > 0 else None
    end = roll[i] if i < n - 1 else None
    return start, end


def _load_contract(symbol, verbose=True):
    path = os.path.join(SCID_DIR, f"F.US.{symbol}.scid")
    if verbose:
        print(f"  Loading {path} ...")
    df = get_scid_df(path)
    df.index = df.index.tz_convert("UTC")
    return df


def _resample_1s(ticks):
    """Same 1s resample convention as export_es_1s_range.py /
    label-review/render_lxpb_retest_1s_report.py's `_resample_1s`: Open
    recomputed as the prior bar's Close (raw scid Open is unreliable)."""
    bars = ticks.resample("1s").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
        "Volume": "sum", "Trades": "sum", "BidVolume": "sum", "AskVolume": "sum",
    })
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Close"].shift(1)
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    bars[["Volume", "Trades", "BidVolume", "AskVolume"]] = bars[
        ["Volume", "Trades", "BidVolume", "AskVolume"]
    ].fillna(0)
    return bars.dropna(subset=["Close"])


def build_continuous_1s(lo_utc, hi_utc, verbose=True):
    """Back-adjusted, jump-free 1s OHLCV+Bid/AskVolume bars covering
    [lo_utc, hi_utc), built the same way build_es_h1_2026_backadjusted.py
    builds its H1 series: per-contract-segment raw ticks resampled to 1s,
    each segment's own TV_GROUND_TRUTH_OFFSETS constant added to
    Open/High/Low/Close (volume columns are untouched -- real traded
    volume, not affected by price back-adjustment), then concatenated.
    Directly comparable against `entry_price` (also back-adjusted) even
    across the M26->U26 roll instant."""
    parts = []
    for i, (sym, _, _) in enumerate(CONTRACTS_NEEDED):
        seg_start, seg_end = _segment_for(i)
        lo = max(lo_utc, seg_start) if seg_start is not None else lo_utc
        hi = min(hi_utc, seg_end) if seg_end is not None else hi_utc
        if lo >= hi:
            continue
        raw = _load_contract(sym, verbose=verbose)
        sl = raw.loc[(raw.index >= lo) & (raw.index < hi)]
        if sl.empty:
            continue
        bars = _resample_1s(sl)
        bars[["Open", "High", "Low", "Close"]] += B26.TV_GROUND_TRUTH_OFFSETS[sym]
        bars["contract"] = sym
        parts.append(bars)
        if verbose:
            print(f"    {sym} segment: {bars.index.min()} .. {bars.index.max()} ({len(bars)} 1s bars)")
    if not parts:
        return None
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


# ---------------------------------------------------------------------------
# Step 1-2: H1 retests, gap-excluded, month-filtered
# ---------------------------------------------------------------------------
def build_retests(h1_csv_path=H1_CSV, months=MONTHS_DEFAULT, year=YEAR, verbose=True):
    h1_df = L.load_ohlc_data(h1_csv_path)
    _, _, retests = L.detect_lxpb_h1(h1_df)
    n_total = len(retests)

    # Gap exclusion -- identical to label-review/render_lxpb_retest_1s_report.py.
    gap_breakout = (
        ((retests["type"] == "LHPB") & (retests["breakout_low"] > retests["price"]))
        | ((retests["type"] == "LLPB") & (retests["breakout_high"] < retests["price"]))
    )
    gap_retest = ~(
        (retests["retest_low"] <= retests["entry_price"])
        & (retests["entry_price"] <= retests["retest_high"])
    )
    retests = retests[~(gap_breakout | gap_retest)].copy()
    n_after_gap = len(retests)

    retests = retests[
        (retests["retest_time"].dt.year == year) & (retests["retest_time"].dt.month.isin(months))
    ].copy().reset_index(drop=True)

    if verbose:
        print(f"H1 retests: {n_total} total -> {n_after_gap} after excluding gap-breakout/gap-retest "
              f"-> {len(retests)} in scope ({year}, months={list(months)}).")
    return retests


# ---------------------------------------------------------------------------
# Step 3-4: exact 1s touch + raw volume-threshold signal
# ---------------------------------------------------------------------------
def _find_touch_pos(idx_arr, lows, highs, lo_pos, hi_pos, entry_price, level_type):
    """First bar position in [lo_pos, hi_pos) whose range actually touches
    entry_price, or gaps clean past it in the retest direction -- same
    touched/gap_over rule lxpb.py's Phase 3 applies at H1 resolution,
    applied here at 1s resolution."""
    seg_low = lows[lo_pos:hi_pos]
    seg_high = highs[lo_pos:hi_pos]
    touched = (seg_low <= entry_price) & (seg_high >= entry_price)
    gap_over = (seg_high < entry_price) if level_type == "LHPB" else (seg_low > entry_price)
    hit = touched | gap_over
    if not hit.any():
        return None
    return lo_pos + int(np.argmax(hit))


def build_signals(retests, bars_1s, vol_threshold=VOL_THRESHOLD_DEFAULT, verbose=True):
    # DatetimeIndex.to_numpy() on a tz-aware index yields an object array of
    # Timestamps (not a comparable datetime64 array) -- drop tz first (index
    # is already UTC, so this is a lossless reinterpretation as naive-UTC).
    idx_arr = bars_1s.index.tz_convert(None).to_numpy()
    lows = bars_1s["Low"].to_numpy(float)
    highs = bars_1s["High"].to_numpy(float)
    bidvol = bars_1s["BidVolume"].to_numpy(float)
    askvol = bars_1s["AskVolume"].to_numpy(float)
    n = len(bars_1s)

    rows = []
    n_no_touch = 0
    for r in retests.itertuples(index=False):
        # retest_time is naive-UTC (see lxpb.py's epoch-seconds convention);
        # bars_1s.index is UTC tz-aware -- localize before comparing.
        hs = pd.Timestamp(r.retest_time, tz="UTC")
        he = hs + pd.Timedelta(hours=1)
        lo_pos = int(np.searchsorted(idx_arr, hs.to_datetime64(), side="left"))
        hi_pos = int(np.searchsorted(idx_arr, he.to_datetime64(), side="left"))
        if lo_pos >= hi_pos:
            n_no_touch += 1
            continue
        pos = _find_touch_pos(idx_arr, lows, highs, lo_pos, hi_pos, r.entry_price, r.type)
        if pos is None:
            n_no_touch += 1
            continue
        bv, av = bidvol[pos], askvol[pos]
        passes = (r.type == "LHPB" and bv > vol_threshold) or (r.type == "LLPB" and av > vol_threshold)
        rows.append({
            "type": r.type, "direction": "LONG" if r.type == "LHPB" else "SHORT",
            "formation_time": r.formation_time, "breakout_time": r.breakout_time,
            "retest_time": r.retest_time, "entry_price": r.entry_price,
            "fta": r.fta, "stop_loss": r.stop_loss,
            "touch_time_utc": bars_1s.index[pos], "pos": pos,
            "bid_volume": bv, "ask_volume": av,
            "passes_vol_filter": bool(passes),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("touch_time_utc").reset_index(drop=True)
    if verbose:
        n_pass = int(out["passes_vol_filter"].sum()) if not out.empty else 0
        print(f"1s touch resolution: {len(retests)} retests -> {len(out)} had a real 1s touch "
              f"({n_no_touch} no-touch/out-of-data-range) -> {n_pass} pass the raw volume "
              f"threshold (>{vol_threshold}).")
    out.to_csv(SIGNALS_OUT, index=False)
    return out


# ---------------------------------------------------------------------------
# Step 5: parallel stop x target grid search
# ---------------------------------------------------------------------------
def _simulate_one(highs, lows, pos, n, direction, entry, stop_pts, target_pts, horizon_s):
    stop = entry - direction * stop_pts
    target = entry + direction * target_pts
    end = n if horizon_s is None else min(pos + 1 + horizon_s, n)
    if pos + 1 >= end:
        return "open", None, None
    seg_high = highs[pos + 1:end]
    seg_low = lows[pos + 1:end]
    if direction == 1:
        stop_hit = seg_low <= stop
        target_hit = seg_high >= target
    else:
        stop_hit = seg_high >= stop
        target_hit = seg_low <= target
    s_idx = int(np.argmax(stop_hit)) if stop_hit.any() else None
    t_idx = int(np.argmax(target_hit)) if target_hit.any() else None
    if s_idx is None and t_idx is None:
        return "open", None, None
    if s_idx is not None and (t_idx is None or s_idx <= t_idx):
        # Same-bar tie resolved pessimistically -> stop wins.
        return "stop", -stop_pts, pos + 1 + s_idx
    return "target", target_pts, pos + 1 + t_idx


def _grid_worker(args):
    (stop_ticks_chunk, target_ticks_grid, trades, highs, lows, n, horizon_s) = args
    results = []
    for stop_ticks in stop_ticks_chunk:
        stop_pts = stop_ticks * TICK
        for target_ticks in target_ticks_grid:
            target_pts = target_ticks * TICK
            pnls = []
            wins = losses = opens = 0
            for pos, direction, entry in trades:
                outcome, pnl_pts, _exit_pos = _simulate_one(highs, lows, pos, n, direction, entry,
                                                              stop_pts, target_pts, horizon_s)
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
            avg_R = float(np.mean([p / stop_pts for p in pnls]))
            results.append({
                "stop_ticks": stop_ticks, "target_ticks": target_ticks,
                "stop_pts": round(stop_pts, 2), "target_pts": round(target_pts, 2),
                "rr": round(target_pts / stop_pts, 3),
                "closed": closed, "wins": wins, "losses": losses, "open": opens,
                "win_rate": round(wins / closed, 4), "total_pts": round(total_pts, 2),
                "avg_R": round(avg_R, 4), "total_R": round(avg_R * closed, 2),
            })
    return results


def grid_search(signals, bars_1s, n_workers=N_WORKERS_DEFAULT, horizon_s=GRID_HORIZON_S,
                 stop_grid=STOP_TICKS_GRID, target_grid=TARGET_TICKS_GRID, verbose=True):
    trades_df = signals[signals["passes_vol_filter"]]
    if trades_df.empty:
        print("No signals pass the volume filter -- skipping grid search.")
        return pd.DataFrame(), []

    highs = bars_1s["High"].to_numpy(float)
    lows = bars_1s["Low"].to_numpy(float)
    n = len(bars_1s)
    trades = [
        (int(row.pos), 1 if row.type == "LHPB" else -1, float(row.entry_price))
        for row in trades_df.itertuples(index=False)
    ]

    if verbose:
        print(f"Grid search: {len(trades)} trades, {len(stop_grid)} stops x {len(target_grid)} "
              f"targets = {len(stop_grid) * len(target_grid)} combos, {n_workers} workers, "
              f"horizon={horizon_s}s")

    chunks = [list(c) for c in np.array_split(stop_grid, n_workers) if len(c)]
    tasks = [(chunk, target_grid, trades, highs, lows, n, horizon_s) for chunk in chunks]

    if len(chunks) > 1:
        with mp.Pool(processes=len(chunks)) as pool:
            all_results = pool.map(_grid_worker, tasks)
    else:
        all_results = [_grid_worker(tasks[0])]

    rows = [r for chunk_results in all_results for r in chunk_results]
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["stop_ticks", "target_ticks"]).reset_index(drop=True)
    return out, trades


def simulate_trades_for_combo(trades_df, bars_1s, stop_ticks, target_ticks, horizon_s=GRID_HORIZON_S):
    """Per-trade outcome detail (for the HTML report) at one specific
    stop/target combo."""
    highs = bars_1s["High"].to_numpy(float)
    lows = bars_1s["Low"].to_numpy(float)
    n = len(bars_1s)
    stop_pts, target_pts = stop_ticks * TICK, target_ticks * TICK

    rows = []
    for row in trades_df.itertuples(index=False):
        pos, direction, entry = int(row.pos), (1 if row.type == "LHPB" else -1), float(row.entry_price)
        outcome, pnl_pts, exit_pos = _simulate_one(highs, lows, pos, n, direction, entry,
                                                    stop_pts, target_pts, horizon_s)
        exit_time = bars_1s.index[exit_pos] if exit_pos is not None else None
        rows.append({
            "type": row.type, "direction": row.direction,
            "formation_time": row.formation_time, "breakout_time": row.breakout_time,
            "retest_time": row.retest_time, "touch_time_utc": row.touch_time_utc,
            "entry_price": entry,
            "stop_price": round(entry - direction * stop_pts, 2),
            "target_price": round(entry + direction * target_pts, 2),
            "bid_volume": row.bid_volume, "ask_volume": row.ask_volume,
            "outcome": outcome, "exit_time_utc": exit_time,
            "pnl_pts": None if pnl_pts is None else round(pnl_pts, 2),
            "r_mult": None if pnl_pts is None else round(pnl_pts / stop_pts, 3),
            "pos": pos, "exit_pos": exit_pos,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Per-trade charts (H1 context + 1s candles/Bid/Ask trio + 1min context) --
# same lightweight-charts@4 visual pattern as
# ../../label-review/render_lxpb_retest_1s_report.py, with stop/target
# price lines and a win/loss-colored exit marker added on top.
# ---------------------------------------------------------------------------
CHART_PAD_SECONDS_1S = 180          # +/- context around the touch instant
CHART_PAD_MINUTES_1MIN = 20         # +/- context for the standalone 1min chart
CHART_PAD_HOURS_H1_BEFORE = 8       # H1 context before the formation bar
CHART_PAD_HOURS_H1_AFTER = 4        # H1 context after the retest bar
CHART_MAX_WINDOW_SECONDS = 1800     # cap on how far the 1s window extends to reach a late exit

ENTRY_COLOR = "#60a5fa"
STOP_COLOR = "#f87171"
TARGET_COLOR = "#4ade80"
BID_COLOR_C = "#f87171"
ASK_COLOR_C = "#4ade80"


def _resample_1min_from_1s(bars):
    out = bars.resample("1min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    return out.dropna(subset=["Close"])


def build_trade_chart(row, h1_df, bars_1s):
    """Build the embeddable chart-data dict for one simulated trade (a row
    from simulate_trades_for_combo's output, as a namedtuple/Series)."""
    is_long = row.type == "LHPB"
    entry, stop, target = float(row.entry_price), float(row.stop_price), float(row.target_price)
    touch_time = pd.Timestamp(row.touch_time_utc)
    pos = int(row.pos)
    exit_pos = None if pd.isna(row.exit_pos) else int(row.exit_pos)
    n = len(bars_1s)

    # --- 1s candles + Bid/Ask volume trio ---------------------------------
    lo_pos = max(0, pos - CHART_PAD_SECONDS_1S)
    if exit_pos is not None:
        hi_pos = max(pos + CHART_PAD_SECONDS_1S, exit_pos + 30)
        hi_pos = min(hi_pos, pos + CHART_MAX_WINDOW_SECONDS)
    else:
        hi_pos = pos + CHART_PAD_SECONDS_1S
    hi_pos = min(n - 1, hi_pos)
    window = bars_1s.iloc[lo_pos:hi_pos + 1]

    candles = [{"time": int(t.timestamp()), "open": float(r.Open), "high": float(r.High),
                "low": float(r.Low), "close": float(r.Close)} for t, r in window.iterrows()]
    bid = [{"time": int(t.timestamp()), "value": float(r.BidVolume), "color": BID_COLOR_C}
           for t, r in window.iterrows()]
    ask = [{"time": int(t.timestamp()), "value": float(r.AskVolume), "color": ASK_COLOR_C}
           for t, r in window.iterrows()]

    price_lines = [
        {"price": entry, "color": ENTRY_COLOR, "lineWidth": 1, "lineStyle": 2, "title": f"entry {entry:.2f}"},
        {"price": stop, "color": STOP_COLOR, "lineWidth": 1, "lineStyle": 2, "title": f"stop {stop:.2f}"},
        {"price": target, "color": TARGET_COLOR, "lineWidth": 1, "lineStyle": 2, "title": f"target {target:.2f}"},
    ]
    markers = [{
        "time": int(touch_time.timestamp()),
        "position": "belowBar" if is_long else "aboveBar",
        "color": ENTRY_COLOR, "shape": "arrowUp" if is_long else "arrowDown", "text": "ENTRY",
    }]
    if exit_pos is not None:
        exit_time = bars_1s.index[exit_pos]
        win = row.outcome == "target"
        markers.append({
            "time": int(exit_time.timestamp()),
            "position": "aboveBar" if is_long else "belowBar",
            "color": TARGET_COLOR if win else STOP_COLOR,
            "shape": "circle", "text": row.outcome.upper(),
        })

    pt = touch_time.tz_convert("America/Los_Angeles")
    chart_1s = {
        "title": f"1s @ retest -- {pt.strftime('%Y-%m-%d %H:%M:%S')} PT "
                  f"({'LONG' if is_long else 'SHORT'})  |  entry {entry:.2f}  outcome {row.outcome}",
        "candles": candles, "bid": bid, "ask": ask,
        "markers": markers, "priceLines": price_lines, "precision": 2,
    }

    # --- standalone 1min context chart ------------------------------------
    lo_1m = touch_time - pd.Timedelta(minutes=CHART_PAD_MINUTES_1MIN)
    hi_1m = touch_time + pd.Timedelta(minutes=CHART_PAD_MINUTES_1MIN)
    slice_1m_src = bars_1s.loc[(bars_1s.index >= lo_1m) & (bars_1s.index <= hi_1m)]
    bars_1min = _resample_1min_from_1s(slice_1m_src)
    one_min = {
        "title": f"1min -- +/-{CHART_PAD_MINUTES_1MIN}min around retest  |  entry {entry:.2f}",
        "candles": [{"time": int(t.timestamp()), "open": float(r.Open), "high": float(r.High),
                     "low": float(r.Low), "close": float(r.Close)} for t, r in bars_1min.iterrows()],
        "markers": [dict(m) for m in markers],
        "priceLines": [dict(pl) for pl in price_lines],
        "precision": 2,
    }

    # --- H1 context chart (formation -> breakout -> retest) --------------
    h1_lo = pd.Timestamp(row.formation_time) - pd.Timedelta(hours=CHART_PAD_HOURS_H1_BEFORE)
    h1_hi = pd.Timestamp(row.retest_time) + pd.Timedelta(hours=CHART_PAD_HOURS_H1_AFTER)
    h1_window = h1_df.loc[(h1_df.index >= h1_lo) & (h1_df.index <= h1_hi)]
    # h1_df.index is naive-UTC (see lxpb.py's epoch-seconds convention) --
    # localize before .timestamp() (naive Timestamp.timestamp() otherwise
    # assumes the SYSTEM's local tz, silently shifting every H1 bar).
    h1_candles = [{"time": int(t.tz_localize("UTC").timestamp()), "open": float(r.open), "high": float(r.high),
                   "low": float(r.low), "close": float(r.close)} for t, r in h1_window.iterrows()]
    h1_retest_marker = {
        "time": int(pd.Timestamp(row.retest_time, tz="UTC").timestamp()),
        "position": "belowBar" if is_long else "aboveBar",
        "color": ENTRY_COLOR, "shape": "arrowUp" if is_long else "arrowDown", "text": "RETEST",
    }
    h1 = {
        "title": f"H1 context -- {row.type} formed {pd.Timestamp(row.formation_time)} "
                  f"-> retest {pd.Timestamp(row.retest_time)}",
        "candles": h1_candles, "markers": [h1_retest_marker], "priceLines": price_lines,
        "precision": 2,
    }

    return {"h1": h1, "trio": chart_1s, "oneMin": one_min}





# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
_CSS = """
:root { --bg:#0f1115; --surface:#171a21; --border:#2a2f3a; --text:#e8eaed;
        --muted:#9aa4b2; --bull:#4ade80; --bear:#f87171; --accent:#fcd34d; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,Segoe UI,Roboto,sans-serif;
       margin:0; padding:20px 28px 60px; }
h1 { font-size:1.5em; margin:0 0 4px; }
h2 { font-size:1.15em; margin:28px 0 10px; border-bottom:1px solid var(--border); padding-bottom:6px; }
.subtitle { color:var(--muted); margin-bottom:18px; font-size:0.92em; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0 22px; }
.summary .box { padding:10px 16px; background:var(--surface); border:1px solid var(--border);
                 border-radius:8px; min-width:120px; }
.summary .box .label { color:var(--muted); font-size:0.78em; text-transform:uppercase; letter-spacing:.03em; }
.summary .box strong { display:block; font-size:1.5em; margin-top:2px; }
.summary .box.good strong { color:var(--bull); }
.summary .box.bad strong { color:var(--bear); }
table { border-collapse:collapse; width:100%; margin-bottom:18px; font-size:0.86em; }
th, td { padding:5px 9px; border-bottom:1px solid var(--border); text-align:right; white-space:nowrap; }
th { color:var(--muted); font-weight:600; text-align:right; position:sticky; top:0; background:var(--bg); }
td.left, th.left { text-align:left; }
tr:hover td { background:#1c2029; }
tr.best { outline:2px solid var(--accent); }
.win { color:var(--bull); font-weight:600; }
.loss { color:var(--bear); font-weight:600; }
.open { color:var(--muted); }
.note { color:var(--muted); font-size:0.85em; margin:6px 0 16px; }
code { background:#1c2029; padding:1px 5px; border-radius:4px; }

/* --- per-trade chart expand/collapse (lightweight-charts@4) --- */
.expand-cell { text-align:center; }
.expand-btn { background:var(--surface); color:var(--text); border:1px solid var(--border);
              border-radius:4px; padding:2px 8px; cursor:pointer; }
.expand-btn.open { background:#1e3a5f; color:#7bb4f5; border-color:#1d3a5c; }
tr.trade-row { cursor:pointer; }
.chart-stack { display:flex; flex-direction:column; gap:8px; padding:8px 0; }
.chart-h1 { height:220px; }
.chart-row-2col { display:grid; grid-template-columns: 1fr 1fr; gap:8px; align-items:start; }
.chart-col-1s { display:grid; grid-template-rows: 300px 100px 100px; gap:8px; }
.chart-col-1m .chart-cell { height:516px; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px; overflow:hidden; }
.chart-title { color:#cccccc; padding:5px 8px; font-size:0.75em;
               font-family:ui-monospace,monospace; background:#0a0a0a;
               border-bottom:1px solid #1f1f1f; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 24px); width:100%; }
.hidden { display:none !important; }
"""


def _outcome_class(o):
    return {"target": "win", "stop": "loss", "open": "open"}.get(o, "")


def _fmt_ts(ts):
    if ts is None or pd.isna(ts):
        return "-"
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _grid_table_html(df, id_attr, highlight_key=None):
    if df.empty:
        return "<p class='note'>(no combos)</p>"
    cols = ["stop_ticks", "target_ticks", "stop_pts", "target_pts", "rr",
            "closed", "wins", "losses", "open", "win_rate", "total_pts", "avg_R", "total_R"]
    head = "".join(f"<th>{c}</th>" for c in cols)
    int_cols = {"stop_ticks", "target_ticks", "closed", "wins", "losses", "open"}
    body_rows = []
    for _, r in df.iterrows():
        key = (r["stop_ticks"], r["target_ticks"])
        cls = " class='best'" if highlight_key is not None and key == highlight_key else ""
        def _cell(c):
            if c == "win_rate":
                return f"<td>{r[c]:.1%}</td>"
            if c in int_cols:
                return f"<td>{int(r[c])}</td>"
            return f"<td>{r[c]}</td>"
        tds = "".join(_cell(c) for c in cols)
        body_rows.append(f"<tr{cls}>{tds}</tr>")
    return (f"<table id='{id_attr}'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>")


def _trades_table_html(trades_df, charts=None):
    """`charts` (optional): list of build_trade_chart() dicts, same length/order
    as trades_df, used to embed per-trade expandable H1 + 1s + 1min charts."""
    if trades_df.empty:
        return "<p class='note'>(no trades)</p>", {}
    rows_html = []
    charts_json = {}
    for i, (_, r) in enumerate(trades_df.iterrows()):
        cls = _outcome_class(r["outcome"])
        pnl = "-" if r["pnl_pts"] is None else f"{r['pnl_pts']:+.2f}"
        rmult = "-" if r["r_mult"] is None else f"{r['r_mult']:+.2f}"
        has_chart = charts is not None and charts[i] is not None
        expand_cell = (
            f"<td class='expand-cell'><button class='expand-btn' data-idx='{i}' "
            f"onclick='event.stopPropagation();toggleChart({i})'>&#9654;</button></td>"
            if has_chart else "<td></td>"
        )
        row_onclick = f" onclick='toggleChart({i})'" if has_chart else ""
        rows_html.append(
            f"<tr class='trade-row'{row_onclick}>"
            f"<td class='left'>{_fmt_ts(r['retest_time'])}</td>"
            f"<td class='left'>{r['type']}</td>"
            f"<td class='left'>{r['direction']}</td>"
            f"<td class='left'>{_fmt_ts(r['touch_time_utc'])}</td>"
            f"<td>{r['entry_price']:.2f}</td>"
            f"<td>{r['stop_price']:.2f}</td>"
            f"<td>{r['target_price']:.2f}</td>"
            f"<td>{r['bid_volume']:.0f}</td>"
            f"<td>{r['ask_volume']:.0f}</td>"
            f"<td class='left {cls}'>{r['outcome']}</td>"
            f"<td class='left'>{_fmt_ts(r['exit_time_utc'])}</td>"
            f"<td class='{cls}'>{pnl}</td>"
            f"<td class='{cls}'>{rmult}</td>"
            f"{expand_cell}"
            "</tr>"
        )
        if has_chart:
            charts_json[i] = charts[i]
            rows_html.append(f"""
<tr class="chart-row hidden" id="chart-row-{i}">
  <td colspan="14"><div class="chart-stack">
    <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{i}"></div><div class="chart-ph" id="ch1-{i}"></div></div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{i}"></div><div class="chart-ph" id="cc-{i}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{i}">Bid Volume</div><div class="chart-ph" id="cb-{i}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{i}">Ask Volume</div><div class="chart-ph" id="ca-{i}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{i}"></div><div class="chart-ph" id="c1m-{i}"></div></div>
      </div>
    </div>
  </div></td>
</tr>""")
    head = ("<th class='left'>Retest Time (H1)</th><th class='left'>Type</th>"
            "<th class='left'>Dir</th><th class='left'>Touch Time (1s, UTC)</th>"
            "<th>Entry</th><th>Stop</th><th>Target</th><th>BidVol@R</th><th>AskVol@R</th>"
            "<th class='left'>Outcome</th><th class='left'>Exit Time (UTC)</th>"
            "<th>PnL (pts)</th><th>R</th><th>&#9654;</th>")
    table_html = f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"
    return table_html, charts_json


def build_report_html(signals, grid_df, best_row, best_trades_df, months, vol_threshold,
                       horizon_s, min_closed, h1_df=None, bars_1s=None):
    n_retests = len(signals)
    n_pass = int(signals["passes_vol_filter"].sum()) if not signals.empty else 0
    n_long = int(((signals["type"] == "LHPB") & signals["passes_vol_filter"]).sum()) if not signals.empty else 0
    n_short = int(((signals["type"] == "LLPB") & signals["passes_vol_filter"]).sum()) if not signals.empty else 0

    top_by_totalR = grid_df[grid_df["closed"] >= min_closed].sort_values("total_R", ascending=False).head(15)
    top_by_avgR = grid_df[grid_df["closed"] >= min_closed].sort_values("avg_R", ascending=False).head(15)
    top_by_totalpts = grid_df[grid_df["closed"] >= min_closed].sort_values("total_pts", ascending=False).head(15)
    top_by_totalpts_practical = grid_df[(grid_df["closed"] >= min_closed) & (grid_df["stop_ticks"] >= 4)] \
        .sort_values("total_pts", ascending=False).head(15)
    best_key = (best_row["stop_ticks"], best_row["target_ticks"]) if best_row is not None else None

    charts_json = {}
    if best_row is not None:
        charts = None
        if h1_df is not None and bars_1s is not None and not best_trades_df.empty:
            charts = [build_trade_chart(row, h1_df, bars_1s)
                      for row in best_trades_df.itertuples(index=False)]
        trades_table, charts_json = _trades_table_html(best_trades_df, charts)
        summary_boxes = f"""
      <div class="box good"><div class="label">Best Stop / Target</div>
        <strong>{best_row['stop_ticks']:.0f}t / {best_row['target_ticks']:.0f}t</strong>
        ({best_row['stop_pts']:.2f}pt / {best_row['target_pts']:.2f}pt, RR {best_row['rr']:.2f})</div>
      <div class="box"><div class="label">Closed Trades</div><strong>{best_row['closed']:.0f}</strong></div>
      <div class="box good"><div class="label">Win Rate</div><strong>{best_row['win_rate']:.1%}</strong></div>
      <div class="box good"><div class="label">Avg R / Trade</div><strong>{best_row['avg_R']:+.3f}</strong></div>
      <div class="box good"><div class="label">Total R</div><strong>{best_row['total_R']:+.1f}</strong></div>
      <div class="box good"><div class="label">Total PnL (pts)</div><strong>{best_row['total_pts']:+.1f}</strong></div>
"""
        trade_log_section = (
            f"<h2>Best combo trade log (stop={best_row['stop_ticks']:.0f}t, "
            f"target={best_row['target_ticks']:.0f}t)</h2>"
            f"<p class='note'>Click a row (or the &#9654; button) to expand its H1 context + 1s "
            f"candles/Bid/Ask-volume + 1min chart, with entry/stop/target price lines and a "
            f"win/loss exit marker.</p>"
            + trades_table
            + f"<p class='note'>Min {min_closed} closed trades required for a combo to be eligible as \"best\".</p>"
        )
    else:
        summary_boxes = "<div class='box bad'><div class='label'>Best Combo</div><strong>none (0 trades)</strong></div>"
        trade_log_section = "<p class='note'>No combo reached the minimum closed-trade threshold.</p>"

    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>LXPB Retest + Volume Scalp -- {', '.join(str(m) for m in months)} 2026</title>
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
<style>{_CSS}</style></head>
<body>
<h1>LXPB H1 Retest + 1s Raw Volume-Threshold Scalp</h1>
<div class="subtitle">
  Months scored: {', '.join(str(m) for m in months)} 2026 &nbsp;|&nbsp;
  Signal: LHPB retest + BidVolume &gt; {vol_threshold} (long), or LLPB retest + AskVolume &gt; {vol_threshold} (short),
  at the exact 1s touch bar &nbsp;|&nbsp; Gap-breakout / gap-retest instances excluded &nbsp;|&nbsp;
  Grid-search horizon: {horizon_s}s ({horizon_s/60:.0f}min) &nbsp;|&nbsp; Entry = level (entry_price)
</div>

<div class="summary">
  <div class="box"><div class="label">H1 Retests In Scope</div><strong>{n_retests}</strong></div>
  <div class="box good"><div class="label">Pass Vol Filter</div><strong>{n_pass}</strong></div>
  <div class="box"><div class="label">Long / Short</div><strong>{n_long} / {n_short}</strong></div>
  {summary_boxes}
</div>

{trade_log_section}


<p class="note"><strong>Caveat on "Total R" ranking:</strong> avg_R/total_R normalize P&amp;L by the
stop distance (R = pts&nbsp;/&nbsp;stop_pts), so a 1-tick stop can rank at the top purely because its
denominator is tiny -- even when its raw point P&amp;L is lower than a wider-stop combo and its win
rate is low (here: ~15%, 7/48 winners). A 1-tick (0.25pt) stop is also at or below ES's typical
bid/ask spread, so it is unlikely to be fillable/tradable as modeled with zero slippage. The
"Total Points" tables below (unnormalized, so not subject to this artifact) are a better guide to
which stop/target is actually practical; the "stop&nbsp;&ge;&nbsp;4 ticks" variant additionally
excludes stops tighter than a typical spread.</p>

<h2>Top 15 combos by Total R</h2>
{_grid_table_html(top_by_totalR, "grid-total-r", best_key)}

<h2>Top 15 combos by Avg R per trade</h2>
{_grid_table_html(top_by_avgR, "grid-avg-r", best_key)}

<h2>Top 15 combos by Total Points (unnormalized P&amp;L)</h2>
{_grid_table_html(top_by_totalpts, "grid-total-pts", best_key)}

<h2>Top 15 combos by Total Points, stop &ge; 4 ticks (practical/spread-aware)</h2>
{_grid_table_html(top_by_totalpts_practical, "grid-total-pts-practical", best_key)}

</body>
<script>
const CHARTS = {json.dumps(charts_json)};
{_CHART_JS_TEMPLATE}
</script>
</html>"""
    return body


# JS charting engine -- same lightweight-charts@4 pattern (dark theme, lazy
# per-row render on expand, crosshair + pan/zoom sync across the 1s trio) as
# ../../label-review/render_lxpb_retest_1s_report.py, adapted for a single
# best-combo trade log instead of a full retest catalogue.
_CHART_JS_TEMPLATE = r"""
const rendered = {};
const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });
const FIXED_BAR_SPACING = 6;

function _baseOpts(tickFmt) {
  return {
    autoSize: true,
    layout: { background:{type:'solid', color:'#000000'}, textColor:'#cccccc',
              fontFamily:"'Courier New', monospace", fontSize:9 },
    grid: { vertLines:{visible:false}, horzLines:{visible:false} },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor:'#333', scaleMargins:{top:0.08, bottom:0.08} },
    timeScale: { borderColor:'#333', timeVisible:true, secondsVisible:true,
      tickMarkFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
    localization: { timeFormatter: (t) => tickFmt.format(new Date(t * 1000)) },
  };
}

function _addCandles(chart, precision) {
  return chart.addCandlestickSeries({
    upColor:'#DDDDD0', downColor:'#888888',
    borderUpColor:'#DDDDD0', borderDownColor:'#888888',
    wickUpColor:'#DDDDD0', wickDownColor:'#888888',
    priceFormat: { type:'price', precision: precision, minMove: 0.25 },
  });
}

function _centerLogicalRange(chart, el, nBars) {
  if (!nBars) return;
  const barsVisible = Math.max(1, el.clientWidth / FIXED_BAR_SPACING);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}

function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false, width: el.clientWidth || 800, height: el.clientHeight || 320,
  }));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  const prec = cd.precision || 2;
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      titleEl.textContent = baseTitle + '  |  O ' + d.open.toFixed(prec)
        + '  H ' + d.high.toFixed(prec) + '  L ' + d.low.toFixed(prec)
        + '  C ' + d.close.toFixed(prec);
    } else { titleEl.textContent = baseTitle; }
  });
  chart.timeScale().applyOptions({ barSpacing: FIXED_BAR_SPACING });
  _centerLogicalRange(chart, el, cd.candles.length);
  const ro = new ResizeObserver((entries) => {
    const r = entries[0].contentRect;
    if (r.width > 0 && r.height > 0) {
      chart.resize(r.width, r.height);
      chart.timeScale().applyOptions({ barSpacing: FIXED_BAR_SPACING });
      _centerLogicalRange(chart, el, cd.candles.length);
    }
  });
  ro.observe(el);
}

function _renderTrio(i, cd) {
  const elC = document.getElementById('cc-' + i);
  const elB = document.getElementById('cb-' + i);
  const elA = document.getElementById('ca-' + i);
  const titleElC = document.getElementById('tc-' + i);
  const titleElB = document.getElementById('tb-' + i);
  const titleElA = document.getElementById('ta-' + i);
  const baseTitleC = cd.title;
  const baseTitleB = 'Bid Volume';
  const baseTitleA = 'Ask Volume';
  titleElC.textContent = baseTitleC;
  titleElB.textContent = baseTitleB;
  titleElA.textContent = baseTitleA;

  const chartC = LightweightCharts.createChart(elC, _baseOpts(timeFmt));
  const seriesC = _addCandles(chartC, cd.precision);
  seriesC.setData(cd.candles);
  if (cd.markers && cd.markers.length) seriesC.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => seriesC.createPriceLine(pl));

  const chartB = LightweightCharts.createChart(elB, _baseOpts(timeFmt));
  const seriesB = chartB.addHistogramSeries({ priceFormat:{type:'volume'} });
  seriesB.setData(cd.bid);

  const chartA = LightweightCharts.createChart(elA, _baseOpts(timeFmt));
  const seriesA = chartA.addHistogramSeries({ priceFormat:{type:'volume'} });
  seriesA.setData(cd.ask);

  const cMap = {}; (cd.candles || []).forEach(b => cMap[b.time] = b);
  const bMap = {}; (cd.bid || []).forEach(b => bMap[b.time] = b.value);
  const aMap = {}; (cd.ask || []).forEach(b => aMap[b.time] = b.value);
  const prec = cd.precision || 2;

  const panes = [
    { chart: chartC, series: seriesC, titleEl: titleElC, base: baseTitleC },
    { chart: chartB, series: seriesB, titleEl: titleElB, base: baseTitleB },
    { chart: chartA, series: seriesA, titleEl: titleElA, base: baseTitleA },
  ];

  function updateLegends(time) {
    const c = time != null ? cMap[time] : null;
    titleElC.textContent = baseTitleC + (c ? ('  |  O ' + c.open.toFixed(prec)
      + '  H ' + c.high.toFixed(prec) + '  L ' + c.low.toFixed(prec)
      + '  C ' + c.close.toFixed(prec)) : '');
    const b = time != null ? bMap[time] : null;
    titleElB.textContent = baseTitleB + (b != null ? ('  |  ' + b) : '');
    const a = time != null ? aMap[time] : null;
    titleElA.textContent = baseTitleA + (a != null ? ('  |  ' + a) : '');
  }

  let syncingCH = false;
  panes.forEach((p, idx) => {
    p.chart.subscribeCrosshairMove((param) => {
      if (syncingCH) return;
      syncingCH = true;
      const time = (param && param.time != null) ? param.time : null;
      updateLegends(time);
      panes.forEach((other, j) => {
        if (j === idx) return;
        if (time == null) { other.chart.clearCrosshairPosition(); return; }
        let val = null;
        if (other.series === seriesC) val = cMap[time] ? cMap[time].close : null;
        else if (other.series === seriesB) val = bMap[time];
        else val = aMap[time];
        if (val != null) other.chart.setCrosshairPosition(val, time, other.series);
        else other.chart.clearCrosshairPosition();
      });
      syncingCH = false;
    });
  });

  let syncingRange = false;
  panes.forEach((p, idx) => {
    p.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || syncingRange) return;
      syncingRange = true;
      panes.forEach((other, j) => { if (j !== idx) other.chart.timeScale().setVisibleLogicalRange(range); });
      syncingRange = false;
    });
  });
  chartC.timeScale().fitContent();
}

function _renderOneMin(i, cd) {
  const el = document.getElementById('c1m-' + i);
  const titleEl = document.getElementById('t1m-' + i);
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  const chart = LightweightCharts.createChart(el, _baseOpts(timeFmtH1));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  const prec = cd.precision || 2;
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      titleEl.textContent = baseTitle + '  |  O ' + d.open.toFixed(prec)
        + '  H ' + d.high.toFixed(prec) + '  L ' + d.low.toFixed(prec)
        + '  C ' + d.close.toFixed(prec);
    } else { titleEl.textContent = baseTitle; }
  });
  chart.timeScale().fitContent();
}

function _renderStack(i) {
  const cd = CHARTS[i];
  if (!cd) return;
  if (cd.h1) _renderH1(i, cd.h1);
  _renderTrio(i, cd.trio);
  if (cd.oneMin) _renderOneMin(i, cd.oneMin);
}

function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.innerHTML = opening ? '&#9660;' : '&#9654;'; }
  if (opening && !rendered[i]) { _renderStack(i); rendered[i] = true; }
}
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LXPB H1 retest + 1s raw-volume-threshold scalp: signal build, grid search, HTML report")
    parser.add_argument("--h1-csv", default=H1_CSV)
    parser.add_argument("--months", default="6,7,8", help="Comma-separated months (2026) to score")
    parser.add_argument("--vol-threshold", type=float, default=VOL_THRESHOLD_DEFAULT)
    parser.add_argument("--horizon-s", type=int, default=GRID_HORIZON_S)
    parser.add_argument("--workers", type=int, default=N_WORKERS_DEFAULT)
    parser.add_argument("--min-closed", type=int, default=15,
                         help="Minimum closed trades for a stop/target combo to be eligible as 'best'")
    parser.add_argument("--best-by", choices=["total_R", "avg_R", "total_pts"], default="total_pts",
                         help="Ranking metric for the highlighted 'best' combo/trade log "
                              "(default total_pts -- unnormalized P&L, not skewed by tiny stops)")
    parser.add_argument("--output", default=REPORT_OUT)
    args = parser.parse_args()

    months = tuple(int(m) for m in args.months.split(","))

    retests = build_retests(args.h1_csv, months=months, verbose=True)
    if retests.empty:
        print("No retests in scope -- nothing to do.")
        sys.exit(0)

    # Pull ONE continuous 1s series covering every in-scope retest hour plus
    # enough trailing room for the grid search's forward simulation horizon.
    lo = pd.Timestamp(retests["retest_time"].min(), tz="UTC")
    hi = pd.Timestamp(retests["retest_time"].max(), tz="UTC") + pd.Timedelta(hours=1, seconds=args.horizon_s + 60)
    print(f"\nBuilding continuous back-adjusted 1s series for [{lo}, {hi}) ...")
    bars_1s = build_continuous_1s(lo, hi)
    if bars_1s is None or bars_1s.empty:
        print("No 1s data available for the requested range.")
        sys.exit(1)
    print(f"  {len(bars_1s)} continuous 1s bars, {bars_1s.index.min()} .. {bars_1s.index.max()}")

    signals = build_signals(retests, bars_1s, vol_threshold=args.vol_threshold, verbose=True)

    grid_df, trades = grid_search(signals, bars_1s, n_workers=args.workers, horizon_s=args.horizon_s,
                                   verbose=True)

    best_row, best_trades_df = None, pd.DataFrame()
    if not grid_df.empty:
        elig = grid_df[grid_df["closed"] >= args.min_closed]
        if not elig.empty:
            best_row = elig.sort_values(args.best_by, ascending=False).iloc[0]
            trades_df = signals[signals["passes_vol_filter"]]
            best_trades_df = simulate_trades_for_combo(
                trades_df, bars_1s, int(best_row["stop_ticks"]), int(best_row["target_ticks"]),
                horizon_s=args.horizon_s)
            print(f"\nBest combo (by {args.best_by}, min {args.min_closed} closed): "
                  f"stop={best_row['stop_ticks']:.0f}t target={best_row['target_ticks']:.0f}t  "
                  f"win_rate={best_row['win_rate']:.1%} avg_R={best_row['avg_R']:+.3f} "
                  f"total_R={best_row['total_R']:+.1f}")
        else:
            print(f"\nNo combo reaches --min-closed={args.min_closed} closed trades.")

    if not grid_df.empty:
        grid_df.to_csv(f"{GRID_OUT_PREFIX}.csv", index=False)
        print(f"Saved full grid -> {GRID_OUT_PREFIX}.csv")

    html = build_report_html(signals, grid_df, best_row, best_trades_df, months,
                              args.vol_threshold, args.horizon_s, args.min_closed,
                              h1_df=L.load_ohlc_data(args.h1_csv), bars_1s=bars_1s)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved HTML report -> {args.output}")
