"""
LXPB hand-labeling report.

Renders one expandable row per completed LXPB retest (from
`lxpb.detect_lxpb_h1`), each with an H1 candlestick chart spanning from a
few bars before the level's formation (Phase 0) through a few bars after
its retest (Phase 2), lightweight-charts markers for Phase 0 (formation)
/ Phase 1 (breakout) / Phase 2 (retest), and price-lines for other
nearby same-type LXPB levels so a human reviewer can visually judge
confluence clusters.

Human-eval feature checkboxes (persisted client-side to localStorage, and
exportable/importable as CSV so labels survive across sessions/machines).
Each checkbox is PRE-CHECKED with a computed default (see `defaults` in
compute_hints) to speed up labeling -- the reviewer's tick is always the
final ground truth and can flip any default:
  - phase0_spike         : formation bar is a genuine spike             (hint/default: patterns-pure
                            find_hammer/find_shooting_star on the formation bar -- NOT lxpb.py's own
                            is_hammer/is_shootingstar, per explicit instruction to source spike
                            detection from D:\\daily-analysis\\patterns-pure)
  - phase1_wide_breakout : breakout bar is unusually wide-ranging       (hint: range / 20-bar avg range;
                            default: ratio >= WIDE_BREAKOUT_RATIO_THRESHOLD)
  - confluence_cluster   : a real cluster of nearby same-type levels    (hint: count within N_TICKS;
                            default: count >= CONFLUENCE_MIN_COUNT)
  - large_wick           : LHPB upper wick / LLPB lower wick is large   (hint: wick % of formation bar
                            range; default: patterns-pure candle_utils.has_large_upper/lower_wick,
                            40% threshold)
  - fast_retest          : retest came quickly, no slow drift           (hint: H1 bars from breakout to
                            retest; default: bars <= FAST_RETEST_MAX_BARS)
  - reviewed             : row has been looked at (tracking only, not a "feature")
  - valid                : overall verdict for this LXPB level -- defaults to checked/"valid" if ANY
                            of the 5 qualities above default-true, unchecked/"invalid" otherwise. This
                            is the primary signal intended to later fine-tune lxpb.py's detection
                            thresholds once real hand-labels are collected and exported.
  - misc                 : freeform notes textarea

To add another checkbox feature later, add one entry to the FEATURES list
in JS_TEMPLATE (id/label/hint) plus its default rule in compute_hints's
`defaults` dict -- no other JS changes needed; the table, localStorage
persistence, and CSV export/import are all driven off the FEATURES list.

Usage:
    python render_labels_report.py --data ../data/es-h1-continuous-backadjusted.csv --limit 300
    python render_labels_report.py --data ../data/es-h1-continuous-backadjusted.csv --all --output all_labels.html
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")
for _p in (_REPO_ROOT, _DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lxpb as L  # noqa: E402

# Spike (and large-wick) detection is sourced from patterns-pure -- the
# more rigorous/"source of truth" pattern library -- rather than
# reimplementing hammer/shooting-star/wick logic locally.
_PATTERNS_PURE = r"D:\daily-analysis\patterns-pure"
if _PATTERNS_PURE not in sys.path:
    sys.path.insert(0, _PATTERNS_PURE)

from find_hammer import find_hammer as _pp_find_hammer  # noqa: E402
from find_shooting_star import find_shooting_star as _pp_find_shooting_star  # noqa: E402
from candle_utils import (  # noqa: E402
    has_large_upper_wick as _pp_has_large_upper_wick,
    has_large_lower_wick as _pp_has_large_lower_wick,
)

# 1s/1min real-tick charts + tick-level volume-by-price footprint, spliced
# from the local Sierra Chart .scid files using the SAME reverse-engineered
# roll-switch/offset rule (`build_es_h1_2026_backadjusted.py`'s `CONTRACTS`/
# `roll_switch_utc`/`TV_GROUND_TRUTH_OFFSETS`) as
# render_lxpb_retest_1s_report.py and ../retest-vol-scalp/lxpb_retest_vol_scalp.py
# -- reimplemented standalone here (rather than imported) so this module has
# no import-order dependency on either sibling script, mirroring the same
# "reimplemented standalone" choice lxpb_retest_vol_scalp.py's own docstring
# makes. Only valid for retests within B26.CONTRACTS' 2026 coverage -- the
# default --start=2026-01-01 keeps every row in range; older/out-of-range
# retests simply render with a "(no tick data in this window)" placeholder.
import build_es_h1_2026_backadjusted as B26  # noqa: E402

sys.path.insert(0, r"D:\acheron\AcheronUtils")  # scidReader.py lives there
from scidReader import get_scid_df  # noqa: E402

# ../data/es-h1-continuous-backadjusted.csv is the whole-monorepo canonical, back-adjusted,
# jump-free continuous ES H1 series (see data/build_es_h1_continuous.py at repo root),
# used consistently by lxpb.py/lxpb-es-vol/label-review. It is built by taking the frozen,
# internally-consistent TradingView back-adjusted export (data/es-h1-2015-14aug2026.csv) as
# the historical base and extending it with fresh, real front-month .scid data (which
# always carries a +0 offset as the current contract) -- so it stays accurate without ever
# needing a fresh TradingView re-export (which is what caused the original drift bug: each
# re-export recalculates ALL history relative to a new anchor date).
#
# lxpb-v2 is a self-contained set of scripts living directly under
# D:\lxpb, with NO local data/ folder of its own -- ALL data (this
# canonical CSV, plus the 2026-only tick-splicing helper/CSV used for
# real-tick 1s/1min/footprint charts) lives in the single shared
# ..\data folder (D:\lxpb\data), the repo-wide single source of truth.
DEFAULT_DATA = os.path.join(_DATA_DIR, "es-h1-continuous-backadjusted.csv")
DEFAULT_OUTPUT = os.path.join(_HERE, "public", "reports", "lxpb_labels_report.html")

BARS_BEFORE = 8     # H1 bars of context shown before Phase 0 (formation)
BARS_AFTER = 8      # H1 bars of context shown after Phase 2 (retest)
# A level can sit unbroken for months (formation -> breakout) and/or take
# months to get retested (breakout -> retest). build_row_chart shows a
# short context window around EACH phase and merges/concatenates them --
# gaps larger than MAX_MERGE_GAP bars are compressed out (with a small
# "[N bars skipped]" marker) instead of rendering the full multi-year span.
CONTEXT_BARS_AFTER_FORMATION = 3    # bars shown right after Phase 0
CONTEXT_BARS_BEFORE_BREAKOUT = 3    # bars shown right before Phase 1
CONTEXT_BARS_AFTER_BREAKOUT = 6     # bars shown right after Phase 1
CONTEXT_BARS_BEFORE_RETEST = 6      # bars shown right before Phase 2
MAX_MERGE_GAP = 15   # gaps <= this many bars are merged into one continuous cluster
N_TICKS_DEFAULT = 20
TICK_SIZE_DEFAULT = 0.25
AVG_RANGE_WINDOW = 20  # trailing bars used for the wide-breakout hint's baseline

# Thresholds used to compute each feature's DEFAULT checkbox state (a
# starting guess pre-filled from the hint metrics; the reviewer's tick is
# the ground truth, and can flip any of these). Chosen from this dataset's
# own hint-value distributions (see label-review/README.md); revisit if
# defaults look miscalibrated once real hand-labels come back.
WIDE_BREAKOUT_RATIO_THRESHOLD = 2.0   # breakout range >= 2x trailing-20-bar avg range
CONFLUENCE_MIN_COUNT = 3              # >=3 nearby same-type levels = a real "cluster"
FAST_RETEST_MAX_BARS = 6              # <=6 H1 bars breakout->retest = "fast" (~ dataset median)


def compute_range_ratio_col(h1_df, retests_df):
    """breakout-bar range / trailing-20-bar avg range for every row -- same
    calc as compute_hints' `range_ratio` hint and analyze_breakout_exits.py's
    "strong breakout" filter, factored out here so --strong-only can apply
    it as a server-side row filter (not just the client-side checkbox)."""
    rng = (h1_df["high"] - h1_df["low"])
    avg_range_20 = rng.rolling(AVG_RANGE_WINDOW).mean().shift(1).to_dict()
    ratios = []
    for _, row in retests_df.iterrows():
        breakout_range = float(row["breakout_high"] - row["breakout_low"])
        baseline = avg_range_20.get(row["breakout_time"])
        ratios.append((breakout_range / baseline) if baseline and baseline > 0 else None)
    return ratios

LEVEL_COLOR = "#fcd34d"     # this row's own level (gold)
CONFLUENCE_COLOR_LHPB = "#60a5fa"  # other same-type nearby levels (blue)
CONFLUENCE_COLOR_LLPB = "#f87171"  # other same-type nearby levels (red)
P0_COLOR = "#fcd34d"
P1_COLOR_UP = "#4ade80"
P1_COLOR_DOWN = "#f87171"
P2_COLOR = "#a78bfa"

# --- 1s/1min real-tick charts + footprint (see import block above) --------
SCID_DIR = r"D:\SC\Data"
PAD_SECONDS_DEFAULT = 45          # +/- context around the exact 1s touch instant
ONE_MIN_PAD_MINUTES_DEFAULT = 20  # +/- 1-minute candles shown next to the 1s trio
BID_COLOR = "#f87171"
ASK_COLOR = "#4ade80"
ENTRY_COLOR = "#60a5fa"   # this level's entry price (1s/1min/footprint price line)
TARGET_COLOR = "#4ade80"  # fta (First Trouble Area -- lxpb's profit-target price) line
STOP_COLOR = "#f87171"    # stop_loss price line
FOOTPRINT_PRE_SECONDS = 20          # both footprint windows start 20s BEFORE touch
FOOTPRINT_NARROW_POST_SECONDS = 5   # narrow window ends 5s AFTER touch
FOOTPRINT_WIDE_POST_SECONDS = 100   # wide window ends 100s AFTER touch

_CONTRACT_CACHE = {}
_OWN_ROLL_CACHE = None
_FOOTPRINT_CONTRACT_CACHE = {}


def _to_epoch_utc(ts):
    """int unix seconds for a naive Timestamp that represents UTC wall
    clock (H1 bar times from lxpb.load_ohlc_data are naive UTC)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _to_pt_str(ts):
    """Format a naive-UTC (or tz-aware) bar timestamp as PT wall-clock time
    for ALL human-facing display -- table cells, CSV export, and H1 chart
    titles -- so every time shown anywhere in this report (including the
    1s/1min chart axes/tooltips and footprint headers, which already used
    America/Los_Angeles) is consistently PT, never a mix of PT and naive
    UTC. Internal-only computation (epoch keys, bar lookups, tick window
    boundaries) is unaffected -- this is purely a display formatter."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("America/Los_Angeles").strftime("%Y-%m-%d %H:%M:%S PT")


# ---------------------------------------------------------------------------
# Contract splicing for real 1s/footprint ticks (mirrors
# render_lxpb_retest_1s_report.py's helpers of the same name -- reimplemented
# standalone here, see the import block's docstring above).
# ---------------------------------------------------------------------------

def _own_roll():
    global _OWN_ROLL_CACHE
    if _OWN_ROLL_CACHE is None:
        _OWN_ROLL_CACHE = [B26.roll_switch_utc(y, m) for _, y, m in B26.CONTRACTS]
    return _OWN_ROLL_CACHE


def _segment_for(i):
    roll = _own_roll()
    n = len(B26.CONTRACTS)
    start = roll[i - 1] if i > 0 else None
    end = roll[i] if i < n - 1 else None
    return start, end


def _contract_index_for(ts_utc):
    """Which CONTRACTS[i] was actually front-month (i.e. which .scid file's
    RAW, unadjusted prices are what really traded) at ts_utc."""
    n = len(B26.CONTRACTS)
    for i in range(n):
        start, end = _segment_for(i)
        if (start is None or ts_utc >= start) and (end is None or ts_utc < end):
            return i
    return n - 1


# --- back-adjustment splice-vintage calibration -----------------------------
# B26.TV_GROUND_TRUTH_OFFSETS converts raw .scid prices into the price scale of
# ONE specific TradingView export: OFFSET_CALIB_H1, the frozen file those
# constants were measured against. That distinction matters because ES1! is not
# a real contract -- TradingView splices the quarterly chain (H26 -> M26 -> U26)
# into one series and shifts every OLDER segment by that roll's spread so the
# chart has no gap. Those spreads are RECOMPUTED on every export, so re-exporting
# silently moves the absolute price of historical bars: between the 14aug and the
# 24aug/1jan/2sep exports the 2026-01-20 15:00 close moved 7009.50 -> 7007.75
# (-1.75 across the whole H26 segment, -5.75 across M26, 0.00 for the live U26).
#
# Feeding a newer export to code still holding the old constants therefore
# mis-scales every .scid-derived pane, AND -- because this same offset converts
# entry/stop/target into raw tick terms -- resolves exits at the wrong price.
# Rather than hard-code a second set of constants (which the NEXT export would
# stale in exactly the same way), measure the shift between the calibration
# export and whatever series is actually displayed, and add it. Exports sharing
# the calibration vintage measure 0.00 and make this a no-op.
#
# All DISPLAY_H1_PATHS must share one vintage (verified: pairwise 0.00 across
# every overlap); they are merged newest-wins, oldest file first.
DISPLAY_H1_PATHS = [
    os.path.join(_HERE, "data", "1jan2026-CME_MINI_ES1!, 60.csv"),
    os.path.join(_HERE, "data", "24aug-CME_MINI_ES1!, 60.csv"),
    os.path.join(_HERE, "data", "2sep-CME_MINI_ES1!, 60.csv"),
]
OFFSET_CALIB_H1 = os.path.join(_DATA_DIR, "es-h1-2015-14aug2026.csv")
# Only measure inside B26's own contract coverage: _segment_for(0) deliberately
# extends back to None, but pre-2026 bars belong to contracts B26 does not model
# (U25/Z25...), each carrying its own unrelated splice shift.
_VINTAGE_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")
# A splice shift is constant across a segment by construction, but a few bars
# legitimately differ -- single-bar data revisions, and the ~1 session either
# side of a roll that two vintages can time differently -- so require a dominant
# mode rather than perfect agreement.
_VINTAGE_MIN_MODE_SHARE = 0.95
_VINTAGE_MIN_OVERLAP = 100
_VINTAGE_DELTAS = None


def _seg_closes(df, lo, hi):
    s = df["close"]
    s = s[s.index >= lo]
    return s if hi is None else s[s.index < hi]


def _load_h1_csv(path):
    """A TradingView H1 export as a UTC-aware OHLC frame (load_ohlc_data
    hands back a naive-UTC index; the segment maths below compares against
    tz-aware roll instants)."""
    df = L.load_ohlc_data(path).copy()
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df


def _display_h1():
    """DISPLAY_H1_PATHS merged newest-wins -- the price scale the reports
    actually show and trade off."""
    frames = [_load_h1_csv(p) for p in DISPLAY_H1_PATHS if os.path.exists(p)]
    if not frames:
        raise RuntimeError(
            "cannot calibrate .scid back-adjustment offsets: none of the display H1 "
            f"exports exist: {DISPLAY_H1_PATHS}")
    merged = pd.concat(frames) if len(frames) > 1 else frames[0]
    return merged[~merged.index.duplicated(keep="last")].sort_index()


def _vintage_deltas():
    """Per-contract shift (display export - calibration export), measured.

    Also the promised guard: a splice shift is a CONSTANT across a whole
    contract segment by construction, so if the measured difference is not
    overwhelmingly one single value the two files are not comparable (e.g.
    one is unadjusted, or the segment/roll boundaries no longer hold) and
    the offsets cannot be trusted -- fail loudly rather than silently
    mis-scale every tick chart and exit resolution."""
    global _VINTAGE_DELTAS
    if _VINTAGE_DELTAS is not None:
        return _VINTAGE_DELTAS
    if not os.path.exists(OFFSET_CALIB_H1):
        raise RuntimeError(
            "cannot calibrate .scid back-adjustment offsets: the export "
            f"TV_GROUND_TRUTH_OFFSETS was measured against is missing: {OFFSET_CALIB_H1}")
    disp, calib = _display_h1(), _load_h1_csv(OFFSET_CALIB_H1)
    out = {}
    for i, (sym, _y, _m) in enumerate(B26.CONTRACTS):
        seg_start, seg_end = _segment_for(i)
        lo = max(seg_start, _VINTAGE_EPOCH) if seg_start is not None else _VINTAGE_EPOCH
        a, b = _seg_closes(disp, lo, seg_end), _seg_closes(calib, lo, seg_end)
        common = a.index.intersection(b.index)
        if len(common) < _VINTAGE_MIN_OVERLAP:
            raise RuntimeError(
                f"cannot calibrate {sym}'s back-adjustment offset: only {len(common)} "
                f"H1 bars overlap between {DISPLAY_H1_PATHS} and {OFFSET_CALIB_H1} in "
                f"[{lo}, {seg_end}) -- need >= {_VINTAGE_MIN_OVERLAP}.")
        diff = (a.loc[common] - b.loc[common]).round(4)
        delta = float(diff.mode().iloc[0])
        share = float((diff == delta).mean())
        if share < _VINTAGE_MIN_MODE_SHARE:
            raise RuntimeError(
                f"{sym}: the difference between the display H1 export and the "
                f"offset-calibration export is not a constant splice shift "
                f"({share:.1%} of {len(common)} bars are {delta:+.2f}, "
                f"{diff.nunique()} distinct values) -- the two files are not the "
                "same kind of series, so TV_GROUND_TRUTH_OFFSETS cannot be "
                f"corrected onto it.\n  display: {DISPLAY_H1_PATHS}\n  calib:   "
                f"{OFFSET_CALIB_H1}")
        out[sym] = delta
    _VINTAGE_DELTAS = out
    if any(v != 0.0 for v in out.values()):
        print("  .scid back-adjustment re-calibrated onto the display H1 export's "
              "splice vintage: " + ", ".join(
                  f"{s}{B26.TV_GROUND_TRUTH_OFFSETS[s]:+.2f}{d:+.2f}="
                  f"{B26.TV_GROUND_TRUTH_OFFSETS[s] + d:+.2f}" for s, d in out.items()))
    return out


def _offset_for_ts(ts_utc):
    """Converts a back-adjusted/continuous price into whichever contract is
    actually front-month AT ts_utc's raw terms -- see
    render_lxpb_retest_1s_report.py's `_offset_for_ts` docstring for why
    it's the retest segment's own offset, not the level's formation-bar
    segment's offset.

    The constant is corrected onto the price scale of the H1 export actually
    being displayed (see DISPLAY_H1_PATHS) -- a no-op when that export shares
    TV_GROUND_TRUTH_OFFSETS' own splice vintage."""
    i = _contract_index_for(ts_utc)
    sym = B26.CONTRACTS[i][0]
    return B26.TV_GROUND_TRUTH_OFFSETS[sym] + _vintage_deltas()[sym], sym


def _load_contract(symbol):
    if symbol not in _CONTRACT_CACHE:
        path = os.path.join(SCID_DIR, f"F.US.{symbol}.scid")
        print(f"  loading {path} (first use of {symbol}) ...")
        df = get_scid_df(path)
        df.index = df.index.tz_convert("UTC")
        _CONTRACT_CACHE[symbol] = df
    return _CONTRACT_CACHE[symbol]


def _ticks_for_window(lo_utc, hi_utc):
    """Real ticks covering [lo_utc, hi_utc) from whichever contract(s) were
    front-month across that span -- almost always a single contract; only
    spans two when the window straddles an actual roll instant."""
    parts = []
    for i, (sym, _, _) in enumerate(B26.CONTRACTS):
        seg_start, seg_end = _segment_for(i)
        lo = max(lo_utc, seg_start) if seg_start is not None else lo_utc
        hi = min(hi_utc, seg_end) if seg_end is not None else hi_utc
        if lo >= hi:
            continue
        df = _load_contract(sym)
        sl = _slice_sorted(df, lo, hi)
        if not sl.empty:
            parts.append(sl)
    if not parts:
        return None
    if len(parts) == 1:
        # .copy() keeps this function's original contract of handing back a
        # standalone frame -- the fast path slices the cached contract, and
        # callers must never be able to write through into that cache.
        return parts[0].copy()
    return pd.concat(parts).sort_index()


def _slice_sorted(df, lo, hi):
    """Rows of `df` in [lo, hi) -- identical to df.loc[(idx >= lo) & (idx < hi)]
    but via searchsorted on the (chronological) tick index.

    The boolean-mask form materialises two ~50M-element arrays per call, and
    every chart/footprint/exit-pin in these reports calls it at least once per
    trade, so on a full-year run that dominates the wall clock. Falls back to
    the mask if a contract's index ever turns out not to be monotonic."""
    idx = df.index
    if not idx.is_monotonic_increasing:
        return df.loc[(idx >= lo) & (idx < hi)]
    a = int(idx.searchsorted(lo, side="left"))
    b = int(idx.searchsorted(hi, side="left"))
    return df.iloc[a:b]


def _resample_1s(ticks):
    """Same 1s resample convention as export_es_1s_pt.py/export_es_1s_range.py:
    Open recomputed as prior bar's Close (raw scid Open is unreliable)."""
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


def _find_touch_time(bars_1s, hour_start, hour_end, entry_price, level_type):
    """First 1s bar within [hour_start, hour_end) whose range actually
    touches entry_price, or gaps clean past it in the level's retest
    direction -- lxpb.py's Phase 3 touched/gap_over rule, applied at 1s
    resolution to pin the REAL instant, within the retest H1 bar, the
    level was hit."""
    win = bars_1s.loc[(bars_1s.index >= hour_start) & (bars_1s.index < hour_end)]
    for t, r in win.iterrows():
        touched = r.Low <= entry_price <= r.High
        gap_over = (r.High < entry_price) if level_type == "LHPB" else (r.Low > entry_price)
        if touched or gap_over:
            return t
    return hour_start


def _resample_1min_from_1s(bars_1s):
    bars = bars_1s.resample("1min").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
    })
    bars["Close"] = bars["Close"].ffill()
    bars["Open"] = bars["Open"].fillna(bars["Close"])
    bars["High"] = bars["High"].fillna(bars["Close"])
    bars["Low"] = bars["Low"].fillna(bars["Close"])
    return bars.dropna(subset=["Close"])


def _footprint_load_contract(symbol):
    """Cached raw-tick loader for footprint building -- shares
    _CONTRACT_CACHE's underlying files via _load_contract."""
    if symbol not in _FOOTPRINT_CONTRACT_CACHE:
        _FOOTPRINT_CONTRACT_CACHE[symbol] = _load_contract(symbol)
    return _FOOTPRINT_CONTRACT_CACHE[symbol]


def build_footprint(touch_time_utc, entry_price_raw, pre_s, post_s, offset=0.0):
    """Tick-level volume-by-price footprint for
    [touch_time_utc - pre_s, touch_time_utc + post_s]: every raw .scid trade
    record's Close (the actual traded price) rounded to the nearest tick,
    with BidVolume/AskVolume summed per price level. `entry_price_raw` is
    only used to select which contract is front-month at touch_time_utc
    (via _offset_for_ts having already been called upstream) -- ticks
    themselves are always read in RAW terms (real traded prices), then
    shifted by `offset` (this row's back-adjustment offset, same one
    applied to entry_price/fta/stop_loss and the 1s/1min/H1 candles) so the
    displayed price ladder is in the SAME back-adjusted/continuous terms as
    every other price shown in the report -- never a mix of raw and
    adjusted numbers for the same instant."""
    lo = touch_time_utc - pd.Timedelta(seconds=pre_s)
    hi = touch_time_utc + pd.Timedelta(seconds=post_s)
    _, sym = _offset_for_ts(touch_time_utc)
    raw = _footprint_load_contract(sym)
    ticks = raw.loc[(raw.index >= lo) & (raw.index <= hi)]
    if ticks.empty:
        return None
    price = np.round(ticks["Close"].to_numpy(float) / TICK_SIZE_DEFAULT) * TICK_SIZE_DEFAULT
    df = pd.DataFrame({
        "price": price,
        "bid": ticks["BidVolume"].to_numpy(float),
        "ask": ticks["AskVolume"].to_numpy(float),
    })
    agg = df.groupby("price", as_index=False).sum()

    # The touch instant is found (upstream, in _find_touch_time) from each
    # tick's own Low/High actually crossing entry_price, NOT from a trade
    # necessarily printing (Close-ing) exactly there -- so a naive
    # Close-only price grid can entirely omit the retested price row even
    # though price genuinely traded through it a moment earlier/later
    # within the same window. Build the row grid from every tick's raw
    # Low..High range instead, so every price level actually touched gets
    # a row (0 volume if no trade Closed there), guaranteeing the level
    # itself always appears.
    lo_price = np.round(ticks["Low"].to_numpy(float).min() / TICK_SIZE_DEFAULT) * TICK_SIZE_DEFAULT
    hi_price = np.round(ticks["High"].to_numpy(float).max() / TICK_SIZE_DEFAULT) * TICK_SIZE_DEFAULT
    full_grid = pd.DataFrame({
        "price": np.round(np.arange(lo_price, hi_price + TICK_SIZE_DEFAULT / 2, TICK_SIZE_DEFAULT), 2)
    })
    agg = full_grid.merge(agg, on="price", how="left").fillna({"bid": 0.0, "ask": 0.0})

    agg["price"] = agg["price"] + offset  # raw traded price -> back-adjusted/continuous terms
    agg = agg.sort_values("price", ascending=False)
    agg["total"] = agg["bid"] + agg["ask"]
    agg["delta"] = agg["ask"] - agg["bid"]
    poc_price = (float(agg.loc[agg["total"].idxmax(), "price"])
                 if not agg.empty and agg["total"].max() > 0 else None)
    return {
        "sym": sym, "lo": lo, "hi": hi, "pre_s": pre_s, "post_s": post_s,
        "rows": agg.to_dict("records"),
        "total_bid": float(agg["bid"].sum()), "total_ask": float(agg["ask"].sum()),
        "total_vol": float(agg["total"].sum()), "poc_price": poc_price,
    }


def _footprint_html(fp, entry_price, label=""):
    """4-column bid/price/ask footprint block (Delta | Bid | Ask | Price,
    left to right -- matches a standard footprint/order-flow layout) --
    rendered as static HTML/CSS since this isn't a native lightweight-charts
    series type. Ported verbatim (styling/markup) from
    ../retest-vol-scalp/lxpb_retest_vol_scalp.py's `_footprint_html`."""
    if fp is None or not fp["rows"]:
        return "<p class='note'>(no tick data in this window)</p>"
    max_vol = max((r["bid"] for r in fp["rows"]), default=0)
    max_vol = max(max_vol, max((r["ask"] for r in fp["rows"]), default=0), 1)
    max_abs_delta = max((abs(r["delta"]) for r in fp["rows"]), default=1) or 1
    lo_pt = fp["lo"].tz_convert("America/Los_Angeles")
    hi_pt = fp["hi"].tz_convert("America/Los_Angeles")
    rows_html = []
    for r in fp["rows"]:
        price = r["price"]
        delta = r["delta"]
        is_entry = abs(price - entry_price) < TICK_SIZE_DEFAULT / 2
        is_poc = fp["poc_price"] is not None and abs(price - fp["poc_price"]) < TICK_SIZE_DEFAULT / 2
        cls = " fp-entry" if is_entry else ""
        cls += " fp-poc" if is_poc else ""
        bid_w = round(100 * r["bid"] / max_vol, 1)
        ask_w = round(100 * r["ask"] / max_vol, 1)
        delta_w = round(100 * abs(delta) / max_abs_delta, 1)
        delta_cls = "fp-delta-neg" if delta < 0 else ("fp-delta-pos" if delta > 0 else "fp-delta-flat")
        rows_html.append(
            f"<div class='fp-row{cls}'>"
            f"<div class='fp-delta {delta_cls}'><span class='fp-bar fp-bar-delta' style='width:{delta_w}%'></span>"
            f"<span class='fp-val'>{delta:+.0f}</span></div>"
            f"<div class='fp-bid'><span class='fp-val'>{r['bid']:.0f}</span>"
            f"<span class='fp-bar' style='width:{bid_w}%'></span></div>"
            f"<div class='fp-ask'><span class='fp-bar fp-bar-ask' style='width:{ask_w}%'></span>"
            f"<span class='fp-val'>{r['ask']:.0f}</span></div>"
            f"<div class='fp-price'>{price:.2f}{' &#9679;' if is_poc else ''}{' &#8592;' if is_entry else ''}</div>"
            "</div>"
        )
    label_prefix = f"{label}  |  " if label else ""
    header = (
        f"<div class='fp-header'>{label_prefix}{fp['sym']}  |  {lo_pt.strftime('%H:%M:%S')}-"
        f"{hi_pt.strftime('%H:%M:%S')} PT (-{fp['pre_s']}s/+{fp['post_s']}s)  |  "
        f"Vol {fp['total_vol']:.0f} (B {fp['total_bid']:.0f}/A {fp['total_ask']:.0f}, "
        f"{fp['total_ask'] - fp['total_bid']:+.0f})  |  POC {fp['poc_price']:.2f}</div>"
    )
    return f"<div class='footprint-wrap'>{header}{''.join(rows_html)}</div>"


def build_1s_trio_chart(row, pad_seconds=PAD_SECONDS_DEFAULT,
                         one_min_pad_minutes=ONE_MIN_PAD_MINUTES_DEFAULT,
                         include_footprint=True, touch_time_override=None):
    """Real-tick 1s candles + Bid/Ask volume trio, a standalone 1min context
    chart, and (opt-in) a tick-level volume-by-price footprint -- all
    centered on the exact touch/fill instant. Without an override, find
    that instant within the row's retest H1 bar. Returns None if no .scid
    tick data is available around the touch/fill.

    `touch_time_override`, if given, is used verbatim instead of this
    function's own naive "either side touched" _find_touch_time call --
    for callers (e.g. render_stop_target_report.py) that already computed
    a more precise, fill-realistic touch_time themselves (requiring the
    correct bid/ask aggressor side for a resting order to actually fill),
    so the chart's RETEST marker/window/footprint centering matches the
    same instant already shown in that caller's own table/summary. It also
    anchors the tick fetch and contract offset: a fine-tuned order can
    fill hours after the original H1 retest."""
    level_type = row["type"]
    adjusted_entry_price = float(row["entry_price"])
    hour_start = pd.Timestamp(row["retest_time"], tz="UTC")
    hour_end = hour_start + pd.Timedelta(hours=1)
    outer_pad = pd.Timedelta(seconds=max(300, pad_seconds + 60, (one_min_pad_minutes + 2) * 60))

    touch_time = None
    if touch_time_override is not None:
        touch_time = pd.to_datetime(touch_time_override, utc=True)
        if pd.isna(touch_time):
            raise ValueError("touch_time_override must be a valid timestamp")
        fetch_start, fetch_end = touch_time - outer_pad, touch_time + outer_pad
    else:
        fetch_start, fetch_end = hour_start - outer_pad, hour_end + outer_pad

    offset, contract_sym = _offset_for_ts(touch_time if touch_time is not None else hour_start)
    entry_price = adjusted_entry_price - offset
    fta = row["fta"]
    stop_loss = row["stop_loss"]
    target_price = (float(fta) - offset) if fta is not None and fta == fta else None
    stop_price = (float(stop_loss) - offset) if stop_loss is not None and stop_loss == stop_loss else None

    ticks = _ticks_for_window(fetch_start, fetch_end)
    if ticks is None or ticks.empty:
        return None
    bars = _resample_1s(ticks)
    if bars.empty:
        return None

    if touch_time is None:
        touch_time = _find_touch_time(bars, hour_start, hour_end, entry_price, level_type)
    lo = touch_time - pd.Timedelta(seconds=pad_seconds)
    hi = touch_time + pd.Timedelta(seconds=pad_seconds)
    window = bars.loc[(bars.index >= lo) & (bars.index <= hi)]
    if window.empty:
        return None

    # Candles/bid/ask are built from RAW tick prices above (needed to find
    # the true touch instant against entry_price's own raw-terms value),
    # but everything DISPLAYED must be back-adjusted/continuous -- the
    # SAME rule the H1 chart and this row's own entry_price/fta/stop_loss
    # already follow -- so every number/hover-legend/price-line/footprint
    # row in this report is in one consistent price frame, never a mix of
    # a contract's raw traded price and the continuous back-adjusted one.
    candles = [{"time": int(t.timestamp()), "open": float(r.Open) + offset, "high": float(r.High) + offset,
                "low": float(r.Low) + offset, "close": float(r.Close) + offset} for t, r in window.iterrows()]
    bid = [{"time": int(t.timestamp()), "value": float(r.BidVolume), "color": BID_COLOR}
           for t, r in window.iterrows()]
    ask = [{"time": int(t.timestamp()), "value": float(r.AskVolume), "color": ASK_COLOR}
           for t, r in window.iterrows()]

    is_long = level_type == "LHPB"
    price_lines = [{"price": adjusted_entry_price, "color": ENTRY_COLOR, "lineWidth": 1, "lineStyle": 2,
                     "title": f"entry {adjusted_entry_price:.2f}"}]
    if stop_price is not None:
        price_lines.append({"price": float(stop_loss), "color": STOP_COLOR, "lineWidth": 1, "lineStyle": 2,
                             "title": f"stop {float(stop_loss):.2f}"})
    if target_price is not None:
        price_lines.append({"price": float(fta), "color": TARGET_COLOR, "lineWidth": 1, "lineStyle": 2,
                             "title": f"fta {float(fta):.2f}"})
    marker = {
        "time": int(touch_time.timestamp()),
        "position": "belowBar" if is_long else "aboveBar",
        "color": ENTRY_COLOR, "shape": "arrowUp" if is_long else "arrowDown", "text": "RETEST",
    }
    pt = touch_time.tz_convert("America/Los_Angeles")
    trio = {
        "title": f"1s @ retest -- {pt.strftime('%Y-%m-%d %H:%M:%S')} PT "
                  f"({'LONG' if is_long else 'SHORT'})  |  entry {adjusted_entry_price:.2f}",
        "candles": candles, "bid": bid, "ask": ask,
        "markers": [marker], "priceLines": price_lines, "precision": 2,
    }

    bars_1min = _resample_1min_from_1s(bars)
    lo_1m = touch_time - pd.Timedelta(minutes=one_min_pad_minutes)
    hi_1m = touch_time + pd.Timedelta(minutes=one_min_pad_minutes)
    window_1m = bars_1min.loc[(bars_1min.index >= lo_1m) & (bars_1min.index <= hi_1m)]
    # The RETEST marker's time must exactly match one of THIS chart's own
    # candle timestamps or lightweight-charts snaps it to the next bar --
    # touch_time is second-precision but 1min candles are floored to the
    # minute, so the marker needs its own minute-floored copy here (the 1s
    # trio chart above keeps the original second-precision marker, since
    # its own candles are also second-precision).
    marker_1min = dict(marker, time=int(touch_time.floor("min").timestamp()))
    one_min = {
        "title": f"1min -- +/-{one_min_pad_minutes}min around retest  |  entry {adjusted_entry_price:.2f}",
        "candles": [{"time": int(t.timestamp()), "open": float(r.Open) + offset, "high": float(r.High) + offset,
                     "low": float(r.Low) + offset, "close": float(r.Close) + offset} for t, r in window_1m.iterrows()],
        "markers": [marker_1min],
        "priceLines": [dict(pl) for pl in price_lines],
        "precision": 2,
    }

    result = {
        "trio": trio, "oneMin": one_min,
        "touch_bid_volume": float(bars.loc[touch_time, "BidVolume"]) if touch_time in bars.index else 0.0,
        "touch_ask_volume": float(bars.loc[touch_time, "AskVolume"]) if touch_time in bars.index else 0.0,
    }
    if include_footprint:
        fp_narrow = build_footprint(touch_time, entry_price, FOOTPRINT_PRE_SECONDS, FOOTPRINT_NARROW_POST_SECONDS, offset)
        fp_wide = build_footprint(touch_time, entry_price, FOOTPRINT_PRE_SECONDS, FOOTPRINT_WIDE_POST_SECONDS, offset)
        result["footprintNarrowHtml"] = _footprint_html(fp_narrow, adjusted_entry_price, label="Narrow")
        result["footprintWideHtml"] = _footprint_html(fp_wide, adjusted_entry_price, label="Wide")
    return result


def _is_gap_lxpb(h1_df, pos_by_ts, row):
    """True if a genuine price gap was involved in this level's
    formation, breakout, or retest, rather than price actually trading
    through it:

    - formation gap: the formation bar's own H1 range doesn't overlap the
      immediately preceding bar's range at all (a real session/weekend
      price gap right at the instant the swing level was created).
    - breakout gap: the breakout bar consumed the level without ever
      trading at it -- a "gap-over" breakout per lxpb.py's
      advance_one_bar docstring, detected the same way it recommends:
      `not (breakout_low <= price <= breakout_high)`.
    - retest gap: same idea for the retest bar, using entry_price (the
      level) against the retest bar's own range:
      `not (retest_low <= entry_price <= retest_high)`.

    Any one of these makes the level's chart show a synthetic instant
    (open/high/low/close of a bar that jumped past the level, not one
    that actually touched it), so by default these rows are excluded
    from the labeling report -- pass --include-gaps to keep them."""
    price = row["price"]
    gap_breakout = not (row["breakout_low"] <= price <= row["breakout_high"])
    gap_retest = not (row["retest_low"] <= row["entry_price"] <= row["retest_high"])

    gap_formation = False
    fi = pos_by_ts.get(row["formation_time"])
    if fi is not None and fi > 0:
        prev_bar = h1_df.iloc[fi - 1]
        cur_bar = h1_df.loc[row["formation_time"]]
        gap_formation = (cur_bar["low"] > prev_bar["high"]) or (cur_bar["high"] < prev_bar["low"])

    return bool(gap_formation or gap_breakout or gap_retest)


def filter_gap_rows(h1_df, retests_df):
    """Drop any retest row where _is_gap_lxpb is True. Returns
    (filtered_df, n_dropped)."""
    if retests_df.empty:
        return retests_df, 0
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    gap_mask = retests_df.apply(lambda r: _is_gap_lxpb(h1_df, pos_by_ts, r), axis=1)
    n_gap = int(gap_mask.sum())
    return retests_df.loc[~gap_mask], n_gap


def build_all_broken_out(retests_df, touch_lv1_df):
    """Every level that ever broke out (completed retest OR still awaiting
    one at the end of the data), used only for the confluence overlay --
    matches lxpb-es-vol/lxpb_confluence.py's rationale that only
    broken-out (touch_lv1) levels count as confluence, never untested
    touch_lv0 swing points."""
    cols = ["type", "price", "breakout_time"]
    parts = []
    if not retests_df.empty:
        parts.append(retests_df[cols])
    if not touch_lv1_df.empty:
        parts.append(touch_lv1_df[cols])
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True)


def is_spike_pp(h1_df, pos_by_ts, level_type, formation_time):
    """Spike classification for the formation bar, sourced from
    patterns-pure (find_hammer / find_shooting_star) rather than lxpb.py's
    own simplified is_hammer/is_shootingstar -- LHPB spikes look like a
    shooting star (rejection of higher prices), LLPB spikes look like a
    hammer (rejection of lower prices). Mirrors the exact 2-row slicing
    convention used by patterns-pure/lxpb_quality_gate.py so the
    shift(1)-based confirmation check has a valid previous bar."""
    fi = pos_by_ts[formation_time]
    slice_2 = h1_df.iloc[max(0, fi - 1):fi + 1]
    if level_type == "LHPB":
        matched = _pp_find_shooting_star(slice_2, atr=0.0)
    else:
        matched = _pp_find_hammer(slice_2, atr=0.0)
    return formation_time in matched.index


def compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken, n_ticks, tick_size):
    """Numeric/boolean hints shown next to each checkbox, plus a per-feature
    `defaults` dict (pre-fills each feature checkbox's initial state based
    on the same metrics) -- the reviewer's tick is always the ground
    truth; these are only starting guesses to speed up labeling."""
    level_type = row["type"]
    price = row["price"]

    formation_bar = h1_df.loc[row["formation_time"]]
    total_range = float(formation_bar["high"] - formation_bar["low"])
    if level_type == "LHPB":
        wick = float(formation_bar["high"] - max(formation_bar["open"], formation_bar["close"]))
        large_wick = _pp_has_large_upper_wick(formation_bar)
    else:
        wick = float(min(formation_bar["open"], formation_bar["close"]) - formation_bar["low"])
        large_wick = _pp_has_large_lower_wick(formation_bar)
    wick_pct = (wick / total_range * 100.0) if total_range > 0 else 0.0

    is_spike = is_spike_pp(h1_df, pos_by_ts, level_type, row["formation_time"])

    breakout_range = float(row["breakout_high"] - row["breakout_low"])
    baseline = avg_range_20.get(row["breakout_time"])
    range_ratio = (breakout_range / baseline) if baseline and baseline > 0 else None

    bars_to_retest = pos_by_ts[row["retest_time"]] - pos_by_ts[row["breakout_time"]]

    tol = n_ticks * tick_size
    same_type = all_broken[all_broken["type"] == level_type]
    nearby = same_type[
        (same_type["breakout_time"] <= row["retest_time"])
        & ((same_type["price"] - price).abs() <= tol)
        & ((same_type["price"] - price).abs() > 1e-9)  # exclude this level itself
    ]
    confluence_count = int(len(nearby))

    defaults = {
        "phase0_spike": bool(is_spike),
        "phase1_wide_breakout": range_ratio is not None and range_ratio >= WIDE_BREAKOUT_RATIO_THRESHOLD,
        "confluence_cluster": confluence_count >= CONFLUENCE_MIN_COUNT,
        "large_wick": bool(large_wick),
        "fast_retest": bars_to_retest <= FAST_RETEST_MAX_BARS,
    }

    return {
        "is_spike": bool(is_spike),
        "wick_pct": round(wick_pct, 1),
        "range_ratio": round(range_ratio, 2) if range_ratio is not None else None,
        "bars_to_retest": int(bars_to_retest),
        "confluence_count": confluence_count,
        "confluence_prices": sorted(float(p) for p in nearby["price"].tolist()),
        "defaults": defaults,
        "valid_default": any(defaults.values()),
    }


def _merge_segments(segments, n_bars, max_gap):
    """Sort/clip/merge (start, end) inclusive bar-index segments; adjacent
    segments separated by <= max_gap bars are merged into one (no visible
    skip), larger gaps are kept as separate clusters. Returns a list of
    (start, end, gap_before) tuples, gap_before=0 for the first segment."""
    clipped = sorted((max(0, s), min(n_bars - 1, e)) for s, e in segments)
    merged = []
    for s, e in clipped:
        if merged and s - merged[-1][1] - 1 <= max_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = []
    prev_end = None
    for s, e in merged:
        gap = 0 if prev_end is None else max(0, s - prev_end - 1)
        out.append((s, e, gap))
        prev_end = e
    return out


def build_row_chart(h1_df, pos_by_ts, row, hints):
    level_type = row["type"]
    price = float(row["price"])
    form_pos = pos_by_ts[row["formation_time"]]
    breakout_pos = pos_by_ts[row["breakout_time"]]
    retest_pos = pos_by_ts[row["retest_time"]]
    n_bars = len(h1_df)

    # Candidate context windows around each of the 3 phases. Any pair of
    # these can be arbitrarily far apart -- a level can sit unbroken for
    # months (formation -> breakout) and/or take months to get retested
    # (breakout -> retest) -- so segments are merged generically rather
    # than assuming only the breakout->retest gap can be large.
    segments = [
        (form_pos - BARS_BEFORE, form_pos + CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + CONTEXT_BARS_AFTER_BREAKOUT),
        (retest_pos - CONTEXT_BARS_BEFORE_RETEST, retest_pos + BARS_AFTER),
    ]
    merged = _merge_segments(segments, n_bars, MAX_MERGE_GAP)

    parts = []
    skip_markers = []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": _to_epoch_utc(h1_df.index[s]),
                "position": "inBar", "color": "#9ca3af", "shape": "square",
                "text": f"[{gap} bars skipped]",
            })
        parts.append(h1_df.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)

    candles = [{
        "time": _to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in window.iterrows()]

    is_lhpb = level_type == "LHPB"
    markers = [
        {
            "time": _to_epoch_utc(row["formation_time"]),
            "position": "aboveBar" if is_lhpb else "belowBar",
            "color": P0_COLOR, "shape": "circle", "text": "P0 form",
        },
        {
            "time": _to_epoch_utc(row["breakout_time"]),
            "position": "belowBar" if is_lhpb else "aboveBar",
            "color": P1_COLOR_UP if is_lhpb else P1_COLOR_DOWN,
            "shape": "arrowUp" if is_lhpb else "arrowDown",
            "text": "P1 breakout",
        },
        {
            "time": _to_epoch_utc(row["retest_time"]),
            "position": "aboveBar" if is_lhpb else "belowBar",
            "color": P2_COLOR, "shape": "circle", "text": "P2 retest",
        },
    ] + skip_markers
    markers.sort(key=lambda m: m["time"])

    confluence_color = CONFLUENCE_COLOR_LHPB if is_lhpb else CONFLUENCE_COLOR_LLPB
    price_lines = [{
        "price": price, "color": LEVEL_COLOR, "lineWidth": 2,
        "lineStyle": 0, "title": f"{level_type} {price:.2f} (this level)",
    }]
    for p in hints["confluence_prices"]:
        price_lines.append({
            "price": p, "color": confluence_color, "lineWidth": 1,
            "lineStyle": 2, "title": f"{level_type} {p:.2f}",
        })

    title = (f"{level_type} {price:.2f}  |  formed {_to_pt_str(row['formation_time'])}  "
             f"broke {_to_pt_str(row['breakout_time'])}  retest {_to_pt_str(row['retest_time'])}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"

    return {
        "title": title,
        "candles": candles, "markers": markers, "priceLines": price_lines,
        "precision": 2,
    }


def build_rows(h1_df, retests_df, touch_lv1_df, n_ticks, tick_size,
               pad_seconds=PAD_SECONDS_DEFAULT, one_min_pad_minutes=ONE_MIN_PAD_MINUTES_DEFAULT,
               include_footprint=True):
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    rng = (h1_df["high"] - h1_df["low"])
    avg_range_20 = rng.rolling(AVG_RANGE_WINDOW).mean().shift(1)
    avg_range_20 = avg_range_20.to_dict()

    all_broken = build_all_broken_out(retests_df, touch_lv1_df)

    # Confluence-bar clustering: rows sharing the exact same retest_time bar
    # (as opposed to compute_hints' price-proximity "confluence_count", these
    # are levels that were ALL retested by the SAME H1 bar at once). Used to
    # badge/band grouped rows in the table so a reviewer can see at a glance
    # which rows are really one market event touching several levels.
    retests_df = retests_df.reset_index(drop=True)
    cluster_size = retests_df.groupby("retest_time")["retest_time"].transform("size")
    cluster_rank = (retests_df.groupby("retest_time")["formation_time"]
                     .rank(ascending=False, method="first").astype(int))
    cluster_ord = pd.factorize(retests_df["retest_time"])[0]  # 0-based distinct-bar index, in row order

    rows_meta = []
    charts = []
    footprints = []
    n_rows = len(retests_df)
    for i, row in retests_df.iterrows():
        hints = compute_hints(h1_df, pos_by_ts, avg_range_20, row, all_broken, n_ticks, tick_size)
        h1_chart = build_row_chart(h1_df, pos_by_ts, row, hints)
        # Real-tick 1s/1min/footprint charts, spliced from local .scid
        # files -- see build_1s_trio_chart's docstring for the (2026-only)
        # tick-data coverage caveat; rows outside that coverage simply fall
        # back to an empty trio/oneMin (H1-only) plus a "(no tick data)"
        # footprint placeholder, rather than failing the whole report.
        trio_chart = build_1s_trio_chart(row, pad_seconds, one_min_pad_minutes, include_footprint)
        if trio_chart is not None:
            chart = {"h1": h1_chart, "trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
            fp = {"narrow": trio_chart.get("footprintNarrowHtml"), "wide": trio_chart.get("footprintWideHtml")}
        else:
            chart = {"h1": h1_chart, "trio": None, "oneMin": None}
            fp = {"narrow": "<p class='note'>(no tick data in this window)</p>",
                  "wide": "<p class='note'>(no tick data in this window)</p>"} if include_footprint else None
        charts.append(chart)
        footprints.append(fp)
        if (i + 1) % 25 == 0 or (i + 1) == n_rows:
            print(f"  built charts for {i + 1}/{n_rows} rows")

        key = f"{row['type']}|{row['price']:.2f}|{_to_epoch_utc(row['formation_time'])}"
        rows_meta.append({
            "idx": i, "key": key,
            "type": row["type"], "price": float(row["price"]),
            "formation_time": _to_pt_str(row["formation_time"]),
            "breakout_time": _to_pt_str(row["breakout_time"]),
            "retest_time": _to_pt_str(row["retest_time"]),
            "entry_price": float(row["entry_price"]),
            "fta": float(row["fta"]) if row["fta"] == row["fta"] else None,
            "stop_loss": float(row["stop_loss"]),
            "hints": hints,
            "cluster_size": int(cluster_size.iloc[i]), "cluster_rank": int(cluster_rank.iloc[i]),
            "cluster_ord": int(cluster_ord[i]),
        })
    return rows_meta, charts, footprints


CSS = """
<style>
:root { --bg:#111316; --surface:#1c1f24; --surface2:#22262d; --border:#2e333b;
        --text:#d4d8df; --text-dim:#6b7280; --text-faint:#444c58;
        --bull:#4ade80; --bear:#f87171; --accent:#60a5fa; --done:#16321f; --invalid:#3a1414; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       padding:16px 24px; max-width:1700px; margin:0 auto;
       background:var(--bg); color:var(--text); }
h1 { font-size:1.4em; margin:0 0 4px; color:#e8eaed; }
p.lead { color:var(--text-dim); margin:0 0 12px; font-size:0.87em; }
.summary { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0; align-items:center; }
.summary .box { padding:8px 14px; background:var(--surface);
                border:1px solid var(--border); border-radius:6px;
                font-size:0.82em; line-height:1.3; }
.summary .box strong { display:block; font-size:1.4em; color:#e8eaed; }
.summary .box.true strong { color:var(--bull); }
.toolbar { display:flex; gap:8px; margin-left:auto; }
.btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
       border-radius:5px; padding:6px 12px; cursor:pointer; font-size:0.82em; }
.btn:hover { border-color:var(--accent); }
.filter-panel { background:var(--surface); border:1px solid var(--border);
                border-radius:6px; padding:10px 14px; margin-bottom:14px; }
.filter-row { display:flex; align-items:center; gap:10px; margin:4px 0; }
.filter-label { color:var(--text-dim); width:80px; font-size:0.85em; }
.chip { background:var(--surface2); border:1px solid var(--border);
        border-radius:14px; padding:3px 10px; cursor:pointer; user-select:none;
        font-size:0.85em; }
.chip input { margin-right:4px; }
table { width:100%; border-collapse:collapse; font-size:0.8em;
        background:var(--surface); border:1px solid var(--border);
        border-radius:6px; }
th, td { border-bottom:1px solid var(--border); padding:5px 7px;
         text-align:center; white-space:nowrap; vertical-align:top; }
th { background:var(--surface2); font-weight:600; color:var(--text-dim);
     letter-spacing:.02em; font-size:0.82em;
     position:sticky; top:0; z-index:2; }
td.left, th.left { text-align:left; }
tr.lvl-row:hover td { background:rgba(255,255,255,.03); cursor:pointer; }
tr.lvl-row.type-lhpb td.type-cell { color:var(--bull); font-weight:600; }
tr.lvl-row.type-llpb td.type-cell { color:var(--bear); font-weight:600; }
tr.lvl-row.band-1 td, tr.chart-row.band-1 td { background:rgba(96,165,250,0.055); }
tr.lvl-row.is-reviewed td { background:var(--done); }
tr.lvl-row.is-invalid td { background:var(--invalid); }
.cluster-badge { background:var(--surface2); border:1px solid var(--accent); color:var(--accent);
                 border-radius:4px; padding:1px 6px; font-size:0.82em; font-weight:600; cursor:help; }
.text-faint { color:var(--text-faint); }
.hint { color:var(--text-faint); font-size:0.85em; display:block; }
.expand-cell { text-align:center; }
.expand-btn { background:var(--surface2); color:var(--text); border:1px solid var(--border);
              border-radius:4px; padding:2px 8px; cursor:pointer; }
.expand-btn.open { background:#1e3a5f; color:#7bb4f5; border-color:#1d3a5c; }
tr.detail-row td.detail-cell { background:#0a0c0e; padding:10px 12px; border-top:none; }
.chart-stack { display:flex; flex-direction:column; gap:8px; padding:8px 0; }
.chart-h1 { height:516px; }
.chart-row-2col { display:grid; grid-template-columns: 1fr 1fr; gap:8px; align-items:start; }
.chart-col-1s { display:grid; grid-template-rows: 300px 100px 100px; gap:8px; }
.chart-col-1m .chart-cell { height:516px; }
.chart-cell { background:#000; border:1px solid var(--border); border-radius:5px;
              overflow:hidden; }
.chart-title { color:#cccccc; padding:5px 8px; font-size:0.75em;
               font-family:ui-monospace,monospace; background:#0a0a0a;
               border-bottom:1px solid #1f1f1f; white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
.chart-ph { height:calc(100% - 24px); width:100%; }
textarea.misc-note { width:140px; height:34px; resize:vertical; background:var(--surface2);
                      color:var(--text); border:1px solid var(--border); border-radius:4px;
                      font-size:0.9em; padding:3px 5px; }
.hidden { display:none !important; }

/* --- tick volume-by-price footprint (compact, 2-up, left half only) --- */
.footprint-outer-row { margin-top:0; }
.footprint-pair { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
.footprint-placeholder { display:flex; align-items:center; justify-content:center;
                          border-style:dashed; opacity:0.4; }
.footprint-cell { padding:0; width:100%; }
.footprint-wrap { max-height:170px; overflow-y:auto; font-family:ui-monospace,monospace; font-size:0.62em; width:100%; }
.fp-header { color:var(--text-dim); padding:3px 6px; background:#0a0a0a; border-bottom:1px solid #1f1f1f;
             position:sticky; top:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.fp-row { display:grid; grid-template-columns: 42px 1fr 1fr 44px; align-items:center; border-bottom:1px solid #1a1d24; }
.fp-row.fp-poc { background:#26210a; }
.fp-row.fp-entry { outline:1px solid var(--accent); outline-offset:-1px; }
.fp-delta { display:flex; justify-content:flex-end; align-items:center; gap:2px; padding:0 3px; position:relative; }
.fp-bar-delta { height:7px; border-radius:1px; }
.fp-delta-neg .fp-bar-delta, .fp-delta-neg .fp-val { color:var(--bear); }
.fp-delta-neg .fp-bar-delta { background:var(--bear); }
.fp-delta-pos .fp-bar-delta, .fp-delta-pos .fp-val { color:var(--bull); }
.fp-delta-pos .fp-bar-delta { background:var(--bull); }
.fp-delta-flat .fp-bar-delta { background:var(--text-dim); }
.fp-bid { display:flex; justify-content:flex-end; align-items:center; gap:2px; padding:0 3px; position:relative; }
.fp-ask { display:flex; justify-content:flex-start; align-items:center; gap:2px; padding:0 3px; position:relative; }
.fp-bar { height:7px; background:#9aa4b2; border-radius:1px; }
.fp-bar-ask { background:#3b9ee5; }
.fp-val { color:var(--text); min-width:22px; text-align:right; }
.fp-ask .fp-val { text-align:left; }
.fp-price { text-align:center; color:#e8eaed; font-weight:600; background:#12141a; padding:0 2px; white-space:nowrap; }
.note { color:var(--text-dim); font-size:0.85em; margin:6px 0; }
</style>
"""


# Every checkbox column is driven off this single list -- add an entry here
# (and nowhere else) to add a new hand-eval feature.
FEATURES = [
    {"id": "phase0_spike", "label": "P0 Spike", "hint_key": "is_spike",
     "hint_fmt": "auto: {v}"},
    {"id": "phase1_wide_breakout", "label": "P1 Wide Breakout", "hint_key": "range_ratio",
     "hint_fmt": "range/avg={v}x"},
    {"id": "confluence_cluster", "label": "Confluence Cluster", "hint_key": "confluence_count",
     "hint_fmt": "{v} nearby"},
    {"id": "large_wick", "label": "Large Wick", "hint_key": "wick_pct",
     "hint_fmt": "wick={v}%"},
    {"id": "fast_retest", "label": "Fast Retest", "hint_key": "bars_to_retest",
     "hint_fmt": "{v} bars"},
]


def build_page(rows_meta, title, n_ticks, tick_size, footprints=None):
    n_lhpb = sum(1 for r in rows_meta if r["type"] == "LHPB")
    n_llpb = len(rows_meta) - n_lhpb
    n_unique_bars = len({r["retest_time"] for r in rows_meta})
    n_clustered_rows = sum(1 for r in rows_meta if r.get("cluster_size", 1) >= 2)

    header = f"""
<h1>{title}</h1>
<p class="lead">Each row is a completed LXPB retest. Phase 0 = formation bar (level created),
Phase 1 = breakout bar, Phase 2 = retest bar -- marked with dots/arrows on the expandable H1
chart, which spans {BARS_BEFORE} bars before Phase 0 through {BARS_AFTER} bars after Phase 2.
Gold line = this row's own level; blue/red dashed lines = other same-type LXPB levels within
{n_ticks} ticks ({n_ticks * tick_size:.2f} pts) broken out by the retest time, for judging
confluence clusters. The <b>Cluster</b> column/blue row-banding flags rows that share the exact
same retest H1 bar (rank/size, most-recently-formed level = rank 1) -- {n_clustered_rows} of
{len(rows_meta)} rows here belong to such a cluster, collapsing to {n_unique_bars} unique retest
bars overall. Each feature checkbox (and the overall "Valid" verdict) is pre-checked
from a computed default -- the small grey hint text explains why -- but the reviewer's tick is
final and can flip any of them. Labels persist server-side and sync across devices; also
exportable/importable as CSV (top-right buttons).</p>
<div class="summary">
  <div class="box true"><strong id="sum-total">{len(rows_meta)}</strong>Levels</div>
  <div class="box"><strong id="sum-shown">{len(rows_meta)}</strong>Shown</div>
  <div class="box"><strong>{n_lhpb}</strong>LHPB</div>
  <div class="box"><strong>{n_llpb}</strong>LLPB</div>
  <div class="box"><strong>{n_unique_bars}</strong>Unique retest bars</div>
  <div class="box"><strong id="sum-reviewed">0</strong>Reviewed</div>
  <div class="box"><strong id="sum-invalid">0</strong>Marked Invalid</div>
  <div class="toolbar">
    <button class="btn" onclick="exportCsv()">⬇ Export labels CSV</button>
    <label class="btn" for="import-file">⬆ Import labels CSV</label>
    <input type="file" id="import-file" accept=".csv" class="hidden" onchange="importCsv(event)">

    <button class="btn" onclick="if(confirm('Clear ALL saved labels in this browser?')) clearAll();">🗑 Clear all</button>
  </div>
</div>
"""

    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Type</span>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LHPB" checked> LHPB</label>
    <label class="chip"><input type="checkbox" class="f-cb f-type" value="LLPB" checked> LLPB</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-status" value="reviewed" checked> Reviewed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Validity</span>
    <label class="chip"><input type="checkbox" class="f-cb f-valid" value="valid" checked> Valid</label>
    <label class="chip"><input type="checkbox" class="f-cb f-valid" value="invalid" checked> Invalid</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Breakout Strength</span>
    <label class="chip"><input type="checkbox" class="f-cb f-feat" data-feat="phase1_wide_breakout" value="on" checked> Strong</label>
    <label class="chip"><input type="checkbox" class="f-cb f-feat" data-feat="phase1_wide_breakout" value="off" checked> Not Strong</label>
  </div>
</div>
"""

    feature_ths = "".join(f'<th>{f["label"]}</th>' for f in FEATURES)
    thead = f"""
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Type</th><th class="left">Formed</th>
  <th>Price</th><th class="left">Breakout</th><th class="left">Retest</th>
  <th>FTA</th><th>Stop</th><th class="left">Cluster</th><th>Reviewed</th><th>Valid</th>
  {feature_ths}
  <th class="left">Misc notes</th><th class="expand-th">▶</th>
</tr></thead>
<tbody>
"""

    rows_html = []
    for r in rows_meta:
        type_cls = "type-lhpb" if r["type"] == "LHPB" else "type-llpb"
        fta_str = f"{r['fta']:.2f}" if r["fta"] is not None else "-"
        cluster_size = r.get("cluster_size", 1)
        band_cls = f" band-{r.get('cluster_ord', 0) % 2}" if cluster_size >= 2 else ""
        cluster_cell = (f'<span class="cluster-badge" title="This bar simultaneously retested '
                         f'{cluster_size} distinct LXPB levels; rank {r["cluster_rank"]} of '
                         f'{cluster_size} by how recently each was formed">{r["cluster_rank"]}/{cluster_size}</span>'
                         if cluster_size >= 2 else '<span class="text-faint">-</span>')
        feature_cells = "".join(
            f'<td><input type="checkbox" class="feat-cb" data-feat="{f["id"]}" '
            f'data-default="{1 if r["hints"]["defaults"].get(f["id"]) else 0}">'
            f'<span class="hint">{f["hint_fmt"].format(v=r["hints"].get(f["hint_key"]))}</span></td>'
            for f in FEATURES
        )
        fp = (footprints[r["idx"]] if footprints else None) or {}
        fp_narrow_html = fp.get("narrow")
        fp_wide_html = fp.get("wide")
        fp_section = (
            f'<div class="chart-row-2col footprint-outer-row">'
            f'<div class="footprint-pair">'
            f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
            f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
            f'</div>'
            f'<div class="chart-cell footprint-placeholder">'
            f'<div class="chart-title">(reserved for a future chart)</div></div>'
            f'</div>'
            if fp_narrow_html or fp_wide_html else ""
        )
        rows_html.append(f"""
<tr class="lvl-row {type_cls}{band_cls}" data-idx="{r['idx']}" data-key="{r['key']}" data-type="{r['type']}"
    onclick="toggleChart({r['idx']})">
  <td class="left">{r['idx']}</td><td class="left type-cell">{r['type']}</td>
  <td class="left">{r['formation_time']}</td><td>{r['price']:.2f}</td>
  <td class="left">{r['breakout_time']}</td><td class="left">{r['retest_time']}</td>
  <td>{fta_str}</td><td>{r['stop_loss']:.2f}</td>
  <td class="left">{cluster_cell}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" data-default="{1 if r['hints']['valid_default'] else 0}"></td>
  {feature_cells}
  <td class="left" onclick="event.stopPropagation();"><textarea class="misc-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{r['idx']}"
      onclick="event.stopPropagation();toggleChart({r['idx']})">▶</button></td>
</tr>
<tr class="chart-row hidden{band_cls}" data-idx="{r['idx']}" id="chart-row-{r['idx']}">
  <td colspan="{11 + len(FEATURES) + 2}"><div class="chart-stack">
    <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{r['idx']}"></div><div class="chart-ph" id="ch1-{r['idx']}"></div></div>
    <div class="chart-row-2col">
      <div class="chart-col-1s">
        <div class="chart-cell"><div class="chart-title" id="tc-{r['idx']}"></div><div class="chart-ph" id="cc-{r['idx']}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="tb-{r['idx']}">Bid Volume</div><div class="chart-ph" id="cb-{r['idx']}"></div></div>
        <div class="chart-cell"><div class="chart-title" id="ta-{r['idx']}">Ask Volume</div><div class="chart-ph" id="ca-{r['idx']}"></div></div>
      </div>
      <div class="chart-col-1m">
        <div class="chart-cell"><div class="chart-title" id="t1m-{r['idx']}"></div><div class="chart-ph" id="c1m-{r['idx']}"></div></div>
      </div>
    </div>
    {fp_section}
  </div></td>
</tr>
""")

    tbody_close = "</tbody></table>"
    return header, filter_panel, thead, "".join(rows_html), tbody_close


JS_TEMPLATE = """
<script src="/js/row-store.js"></script>
<script>
const CHARTS = __CHARTS_JSON__;
const ROWS = __ROWS_JSON__;
const FEATURES = __FEATURES_JSON__;
const STORAGE_KEY = 'lxpb_labels_v1';
// Labels persist server-side (Postgres, via /api/rows) instead of browser
// localStorage, so they sync across devices.
const labelStore = new RowStore(STORAGE_KEY);
const rendered = {};
// Fixed candle width in pixels so charts with few candles show blank
// space on either side instead of stretching each candle to fill the
// whole row. Kept intentionally tight/close together; the reviewer can
// still manually drag/scroll-zoom the time axis afterward (see
// _renderChart -- we manage chart sizing ourselves instead of fighting
// the user's own zoom with a continuous watchdog).
const FIXED_BAR_SPACING = 6;

function rowState(tr) {
  const state = { reviewed: tr.querySelector('.reviewed-cb').checked,
                  valid: tr.querySelector('.valid-cb').checked,
                  misc: tr.querySelector('.misc-note').value };
  FEATURES.forEach(f => {
    const cb = tr.querySelector('.feat-cb[data-feat="' + f.id + '"]');
    state[f.id] = cb ? cb.checked : false;
  });
  return state;
}

function applyDefaults(tr) {
  // Pre-fill each checkbox from its computed default (see `defaults` /
  // `valid_default` in compute_hints) -- only a starting guess; any saved
  // localStorage state (applied right after this) always wins.
  const validCb = tr.querySelector('.valid-cb');
  validCb.checked = validCb.dataset.default === '1';
  tr.querySelectorAll('.feat-cb').forEach(cb => { cb.checked = cb.dataset.default === '1'; });
  tr.classList.toggle('is-invalid', !validCb.checked);
}

function applyRowState(tr, state) {
  if (!state) return;
  if (state.reviewed !== undefined) tr.querySelector('.reviewed-cb').checked = !!state.reviewed;
  if (state.valid !== undefined) tr.querySelector('.valid-cb').checked = !!state.valid;
  if (state.misc !== undefined) tr.querySelector('.misc-note').value = state.misc || '';
  FEATURES.forEach(f => {
    if (state[f.id] === undefined) return;
    const cb = tr.querySelector('.feat-cb[data-feat="' + f.id + '"]');
    if (cb) cb.checked = !!state[f.id];
  });
  tr.classList.toggle('is-reviewed', !!tr.querySelector('.reviewed-cb').checked);
  tr.classList.toggle('is-invalid', !tr.querySelector('.valid-cb').checked);
}

function persistRow(tr, opts) {
  const state = rowState(tr);
  labelStore.set(tr.dataset.key, state, opts);
  tr.classList.toggle('is-reviewed', !!state.reviewed);
  tr.classList.toggle('is-invalid', !state.valid);
  updateSummary();
}

function updateSummary() {
  const total = document.querySelectorAll('.lvl-row').length;
  const reviewed = document.querySelectorAll('.lvl-row.is-reviewed').length;
  const invalid = document.querySelectorAll('.lvl-row.is-invalid').length;
  document.getElementById('sum-reviewed').textContent = reviewed;
  document.getElementById('sum-invalid').textContent = invalid;
}

async function initRows() {
  await labelStore.init();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyDefaults(tr);
    applyRowState(tr, labelStore.get(tr.dataset.key));
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .feat-cb').forEach(el => {
      el.addEventListener('change', () => persistRow(tr));
    });
    tr.querySelector('.misc-note').addEventListener('input', () => persistRow(tr, { debounceMs: 500 }));
  });
  updateSummary();
  applyFilters();
}

function csvEscape(v) {
  v = (v === null || v === undefined) ? '' : String(v);
  return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

function exportCsv() {
  const store = labelStore.getAll();
  const featCols = FEATURES.map(f => f.id);
  const header = ['key', 'idx', 'type', 'price', 'formation_time', 'breakout_time',
                   'retest_time', 'reviewed', 'valid'].concat(featCols).concat(['misc']);
  const lines = [header.join(',')];
  ROWS.forEach(r => {
    const st = store[r.key] || {};
    const row = [r.key, r.idx, r.type, r.price, r.formation_time, r.breakout_time,
                 r.retest_time, st.reviewed ? 1 : 0, st.valid ? 1 : 0]
      .concat(featCols.map(f => (st[f] ? 1 : 0)))
      .concat([st.misc || '']);
    lines.push(row.map(csvEscape).join(','));
  });
  const blob = new Blob([lines.join('\\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'lxpb_labels.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

function parseCsvLine(line) {
  const out = []; let cur = ''; let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQ) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') { inQ = false; }
      else { cur += c; }
    } else {
      if (c === '"') inQ = true;
      else if (c === ',') { out.push(cur); cur = ''; }
      else cur += c;
    }
  }
  out.push(cur);
  return out;
}

function importCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseCsvLine(lines[0]);
    const entries = {};
    for (let i = 1; i < lines.length; i++) {
      const cols = parseCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      const key = rec.key;
      if (!key) continue;
      const state = { misc: rec.misc || '' };
      if (header.includes('reviewed')) state.reviewed = rec.reviewed === '1';
      if (header.includes('valid')) state.valid = rec.valid === '1';
      FEATURES.forEach(f => { if (header.includes(f.id)) state[f.id] = rec[f.id] === '1'; });
      entries[key] = state;
    }
    await labelStore.bulkSet(entries);
    document.querySelectorAll('.lvl-row').forEach(tr => applyRowState(tr, labelStore.get(tr.dataset.key)));
    updateSummary();
    evt.target.value = '';
    alert('Imported labels from ' + file.name);
  };
  reader.readAsText(file);
}

async function clearAll() {
  await labelStore.clearAll();
  document.querySelectorAll('.lvl-row').forEach(tr => applyDefaults(tr));
  updateSummary();
}

const PT_TZ = 'America/Los_Angeles';
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false });
const timeFmtH1 = new Intl.DateTimeFormat('en-US', { timeZone: PT_TZ,
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false });

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

// Center the candles horizontally (blank/black space evenly on BOTH left
// and right when there aren't enough bars to fill the container at
// FIXED_BAR_SPACING) instead of lightweight-charts' default right-aligned
// scroll position.
function _centerLogicalRange(chart, el, nBars) {
  if (!nBars) return;
  const barsVisible = Math.max(1, el.clientWidth / FIXED_BAR_SPACING);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}

function _renderH1(i, cd) {
  const el = document.getElementById('ch1-' + i);
  const titleEl = document.getElementById('th1-' + i);
  if (!el || !titleEl) return;
  const baseTitle = cd.title;
  titleEl.textContent = baseTitle;
  // autoSize is intentionally NOT used here: its internal ResizeObserver
  // re-fits the visible range to exactly fill the container width on
  // every resize (including right after this row is un-hidden), which
  // would either stretch our fixed candle width back out, or (if fought
  // with a watchdog on visible-range-change) also clobber the reviewer's
  // own manual drag/scroll-zoom on the time axis. Instead we size the
  // chart ourselves via our own ResizeObserver below, so a real container
  // resize (row opened, window resized) and a user zoom gesture are two
  // clearly distinct events and never fight each other.
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false,
    width: el.clientWidth || 800,
    height: el.clientHeight || 320,
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
  if (!elC || !elB || !elA) return;
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
  if (!el || !titleEl) return;
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
  if (cd.trio) _renderTrio(i, cd.trio);
  if (cd.oneMin) _renderOneMin(i, cd.oneMin);
}

function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.textContent = opening ? '▼' : '▶'; }
  if (opening && !rendered[i]) { _renderStack(i); rendered[i] = true; }
}

function applyFilters() {
  const typeOn = Array.from(document.querySelectorAll('.f-type:checked')).map(c => c.value);
  const statusOn = Array.from(document.querySelectorAll('.f-status:checked')).map(c => c.value);
  const validOn = Array.from(document.querySelectorAll('.f-valid:checked')).map(c => c.value);
  const featOn = Array.from(document.querySelectorAll('.f-feat[data-feat="phase1_wide_breakout"]:checked')).map(c => c.value);
  let shown = 0;
  document.querySelectorAll('.lvl-row').forEach(function(tr) {
    const isReviewed = tr.classList.contains('is-reviewed');
    const isInvalid = tr.classList.contains('is-invalid');
    const statusOk = statusOn.includes(isReviewed ? 'reviewed' : 'unreviewed');
    const validOk = validOn.includes(isInvalid ? 'invalid' : 'valid');
    const featCb = tr.querySelector('.feat-cb[data-feat="phase1_wide_breakout"]');
    const featOk = !featCb || featOn.includes(featCb.checked ? 'on' : 'off');
    const show = typeOn.includes(tr.dataset.type) && statusOk && validOk && featOk;
    tr.classList.toggle('hidden', !show);
    if (show) shown++;
    if (!show) {
      const cr = document.getElementById('chart-row-' + tr.dataset.idx);
      if (cr) cr.classList.add('hidden');
    }
  });
  document.getElementById('sum-shown').textContent = shown;
}
document.querySelectorAll('.f-cb').forEach(cb => cb.addEventListener('change', applyFilters));
document.addEventListener('change', function(e) {
  if (e.target.classList.contains('feat-cb') && e.target.dataset.feat === 'phase1_wide_breakout') applyFilters();
});

initRows();
</script>
"""


def render(data_path, output_path, title, n_ticks, tick_size, start=None, end=None,
           limit=300, order="desc", pad_seconds=PAD_SECONDS_DEFAULT,
           one_min_pad_minutes=ONE_MIN_PAD_MINUTES_DEFAULT, include_footprint=True,
           exclude_gaps=True, strong_only=False):
    h1_df = L.load_ohlc_data(data_path)
    _touch_lv0, touch_lv1_df, retests_df = L.detect_lxpb_h1(h1_df)

    if start:
        retests_df = retests_df[retests_df["retest_time"] >= pd.Timestamp(start)]
    if end:
        retests_df = retests_df[retests_df["retest_time"] <= pd.Timestamp(end)]

    if exclude_gaps:
        retests_df, n_gap = filter_gap_rows(h1_df, retests_df)
        if n_gap:
            print(f"Excluding {n_gap} gap-related LXPB retests (gap during formation/"
                  f"breakout/retest) by default -- pass --include-gaps to keep them")

    if strong_only:
        ratios = compute_range_ratio_col(h1_df, retests_df)
        retests_df = retests_df.assign(range_ratio=ratios)
        retests_df = retests_df[retests_df["range_ratio"].notna() &
                                 (retests_df["range_ratio"] >= WIDE_BREAKOUT_RATIO_THRESHOLD)]
        n_unique_bars = retests_df["retest_time"].nunique()
        print(f"--strong-only: kept {len(retests_df)} rows (range_ratio >= "
              f"{WIDE_BREAKOUT_RATIO_THRESHOLD}) across {n_unique_bars} unique retest bars")

    retests_df = retests_df.sort_values("retest_time", ascending=(order == "asc"))
    if limit:
        retests_df = retests_df.head(limit)
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)

    print(f"Loaded {len(h1_df)} H1 bars ({h1_df.index.min()} -> {h1_df.index.max()})")
    print(f"Completed retests total: touch_lv1 open={len(touch_lv1_df)}; "
          f"rendering {len(retests_df)} rows")

    rows_meta, charts, footprints = build_rows(
        h1_df, retests_df, touch_lv1_df, n_ticks, tick_size,
        pad_seconds=pad_seconds, one_min_pad_minutes=one_min_pad_minutes,
        include_footprint=include_footprint)

    header, filter_panel, thead, rows_html, tbody_close = build_page(
        rows_meta, title, n_ticks, tick_size, footprints)

    js = (JS_TEMPLATE
          .replace("__CHARTS_JSON__", json.dumps(charts))
          .replace("__ROWS_JSON__", json.dumps(rows_meta))
          .replace("__FEATURES_JSON__", json.dumps(FEATURES)))

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
{CSS}
</head><body>
{header}
{filter_panel}
{thead}
{rows_html}
{tbody_close}
{js}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate LXPB hand-labeling HTML report")
    parser.add_argument("--data", default=DEFAULT_DATA, help="H1 OHLC CSV path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--title", default="LXPB Hand-Labeling Report",
                         help="Report page title")
    parser.add_argument("--n-ticks", type=int, default=N_TICKS_DEFAULT,
                         help="Confluence radius in ticks (default 20)")
    parser.add_argument("--tick-size", type=float, default=TICK_SIZE_DEFAULT,
                         help="Instrument tick size (default 0.25, ES)")
    parser.add_argument("--start", default="2026-07-01",
                         help="Only retests on/after this date (default: 2026-07-01, "
                              "i.e. only Jul-Aug 2026 retests -- formation/breakout can predate this)")
    parser.add_argument("--end", default="2026-08-31", help="Only retests on/before this date")
    parser.add_argument("--limit", type=int, default=300,
                         help="Max number of rows (0 = all). Selects the most "
                              "recent N by default (see --order).")
    parser.add_argument("--order", choices=["asc", "desc"], default="desc",
                         help="Which end of the (optionally date-filtered) retest "
                              "history --limit keeps: 'desc'=most recent (default), "
                              "'asc'=earliest")
    parser.add_argument("--pad-seconds", type=int, default=PAD_SECONDS_DEFAULT,
                         help="+/- context (seconds) around the exact 1s touch instant (default 45)")
    parser.add_argument("--one-min-pad-minutes", type=int, default=ONE_MIN_PAD_MINUTES_DEFAULT,
                         help="+/- context (minutes) for the standalone 1min chart (default 20)")
    parser.add_argument("--no-footprint", action="store_true",
                         help="Skip the tick-level volume-by-price footprint (faster; H1+1s+1min only)")
    parser.add_argument("--include-gaps", action="store_true",
                         help="Keep rows where formation/breakout/retest involved a price "
                              "gap (bar never actually traded at the level). Excluded by "
                              "default.")
    parser.add_argument("--strong-only", action="store_true",
                         help="Server-side filter to only 'strong breakout' rows (breakout-bar "
                              "range >= WIDE_BREAKOUT_RATIO_THRESHOLD x trailing-20-bar avg range), "
                              "matching analyze_breakout_exits.py's row selection -- much smaller "
                              "report than the client-side checkbox, and adds a Cluster column/"
                              "row-banding for rows sharing the same retest bar (confluence).")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"Error: {args.data} not found.")
        sys.exit(1)

    render(args.data, args.output, args.title, args.n_ticks, args.tick_size,
           start=args.start, end=args.end, limit=args.limit, order=args.order,
           pad_seconds=args.pad_seconds, one_min_pad_minutes=args.one_min_pad_minutes,
           include_footprint=not args.no_footprint,
           exclude_gaps=not args.include_gaps, strong_only=args.strong_only)
