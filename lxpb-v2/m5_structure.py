"""
m5_structure.py
================
Derived M5 price structure that is NOT an LXPB level: confirmed swing pivots
and zigzag pivots, both read straight off the one continuous M5
series (`lxpb_levels_cache.m5_bars_continuous` -- see the "continuous
contracts only" convention in CLAUDE.md; nothing here ever resamples .scid
into bars).

Two consumers, both in the M5-native strategy report:

  * ZIGZAG PIVOTS -> the two target rules (`zigzag_pivots`,
    `most_recent_pivot`): the swing-extreme target and the opposite-M5
    zigzag-anchor target. The zigzag itself is patterns-pure's `find_zigzag`
    and nothing else (CLAUDE.md, "Zigzag: patterns-pure only"); this module
    only feeds it the continuous series and memoises the answer.

  * SWING PIVOTS -> the "swerved" entry rule. A confirmed swing low sitting
    right on a planned LONG entry (or a swing high on a planned SHORT entry)
    is an obvious, widely-visible price; the strategy moves its resting
    order past it rather than queue behind everyone else. See
    `swings_near` and the report's own `_swerve_entry`.
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PATTERNS_PURE = os.path.join(_HERE, "patterns_pure")
if _PATTERNS_PURE not in sys.path:
    sys.path.insert(0, _PATTERNS_PURE)

import lxpb_levels_cache as LC        # noqa: E402
from find_zigzag import ATR_MULT, ATR_PERIOD, MIN_BARS, find_zigzag, most_recent_pivot  # noqa: E402,F401

SWING_K_DEFAULT = 2                   # bars required on EACH side of a pivot

_PIVOT_CACHE = {}
_ZIGZAG_CACHE = {}


def _naive_utc_values(index):
    """A tz-aware DatetimeIndex as a plain datetime64[ns] array in UTC.
    `.to_numpy()` on a tz-aware index yields an OBJECT array of Timestamps,
    which neither `np.diff` nor a datetime64 comparison can use."""
    return index.tz_convert("UTC").tz_localize(None).to_numpy()


def _as_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def entry_cutoff(touch_time, bar_width=pd.Timedelta(minutes=5)):
    """The instant this strategy is allowed to see structure up to: the last
    nanosecond before the entry's OWN M5 (or `bar_width`) candle opened. Same
    convention (and same reason) as
    render_ss_confl_finetune_report._live_m5_before_entry -- ledger/bar
    timestamps label bar STARTS, so querying at the tick itself would expose
    the rest of an unfinished candle. `bar_width` defaults to the real M5
    series' own 5 minutes; a caller running this against a different-
    timeframe bars/ledger (e.g. an M1 one) passes its own width."""
    return _as_utc(touch_time).floor(bar_width) - pd.Timedelta(nanoseconds=1)


# Swing pivots (the "swerved" entry rule)
# --------------------------------------------------------------------------

def swing_pivots(bars=None, k=SWING_K_DEFAULT):
    """(swing_high_times, swing_high_prices, swing_low_times,
    swing_low_prices) over the continuous M5 series.

    A swing low at bar i is a low STRICTLY below all `k` lows on each side
    of it (and the mirror for a swing high) -- the plain, widely-used
    fractal pivot, deliberately not a repo-specific variant, since the
    whole point of the swerve rule is that this is a price other traders
    can see too. A pivot is only CONFIRMED `k` bars later, which is what
    `swings_near` enforces before letting one move an entry.

    Memoised per k for the default series."""
    if bars is None:
        if k in _PIVOT_CACHE:
            return _PIVOT_CACHE[k]
        bars = LC.m5_bars_continuous()
        out = _scan_pivots(bars, k)
        _PIVOT_CACHE[k] = out
        return out
    return _scan_pivots(bars, k)


def _scan_pivots(bars, k):
    empty = (np.empty(0, dtype="datetime64[ns]"), np.empty(0),
             np.empty(0, dtype="datetime64[ns]"), np.empty(0))
    if bars is None or bars.empty or k < 1:
        return empty
    highs = bars["high"].to_numpy(float)
    lows = bars["low"].to_numpy(float)
    n = len(highs)
    if n < 2 * k + 1:
        return empty
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    core = slice(k, n - k)
    is_high[core] = True
    is_low[core] = True
    for d in range(1, k + 1):
        is_high[core] &= highs[core] > highs[k - d:n - k - d]
        is_high[core] &= highs[core] > highs[k + d:n - k + d]
        is_low[core] &= lows[core] < lows[k - d:n - k - d]
        is_low[core] &= lows[core] < lows[k + d:n - k + d]
    times = _naive_utc_values(bars.index)
    return (times[is_high], highs[is_high], times[is_low], lows[is_low])


def swings_near(price, tol, lo_ts, hi_ts, is_low, k=SWING_K_DEFAULT, bars=None,
                bar_width=pd.Timedelta(minutes=5)):
    """Every confirmed swing pivot within +/-`tol` points of `price` whose
    own bar falls in [lo_ts, hi_ts), as a list of (time, pivot_price).

    `is_low` picks lows (the side that matters for a LONG entry) or highs
    (for a SHORT). CONFIRMED means the pivot's own `k` right-hand bars had
    all closed before `hi_ts` -- a pivot whose right side is still forming
    was not visible to anyone at `hi_ts` and must not move an entry.
    `bar_width` defaults to the real M5 series' own 5 minutes; pass the
    bars' own width when `bars` is a different timeframe."""
    hi_times, hi_prices, lo_times, lo_prices = swing_pivots(bars, k)
    times, prices = (lo_times, lo_prices) if is_low else (hi_times, hi_prices)
    if len(times) == 0:
        return []
    lo_ts, hi_ts = _as_utc(lo_ts), _as_utc(hi_ts)
    # A pivot at time t is only confirmed once its k-th right-hand bar has
    # closed, i.e. at t + (k + 1) * bar_width.
    confirm_by = hi_ts - bar_width * (k + 1)
    idx = np.flatnonzero((times >= lo_ts.to_datetime64()) &
                         (times <= confirm_by.to_datetime64()) &
                         (np.abs(prices - float(price)) <= float(tol)))
    return [(pd.Timestamp(times[i]).tz_localize("UTC"), float(prices[i])) for i in idx]


# --------------------------------------------------------------------------
# Zigzag legs (new "opposite M5 target": most recent leg's crest/trough)
# --------------------------------------------------------------------------

def zigzag_pivots(bars=None, atr_mult=ATR_MULT, atr_period=ATR_PERIOD, min_bars=MIN_BARS):
    """`find_zigzag(bars, ...)`, the repo's only zigzag; `bars` defaults to the
    continuous M5 series, memoised per parameter set. Columns time, price, kind
    ("crest" / "trough"), confirm_time, bar, confirm_bar. Look pivots up
    point-in-time with `most_recent_pivot` (re-exported from find_zigzag)."""
    if bars is not None:
        return find_zigzag(bars, atr_mult, atr_period, min_bars)
    key = (float(atr_mult), int(atr_period), int(min_bars))
    if key not in _ZIGZAG_CACHE:
        _ZIGZAG_CACHE[key] = find_zigzag(LC.m5_bars_continuous(), atr_mult, atr_period, min_bars)
    return _ZIGZAG_CACHE[key]


if __name__ == "__main__":
    bars = LC.m5_bars_continuous()
    hi_t, hi_p, lo_t, lo_p = swing_pivots(bars)
    print(f"{len(hi_t)} swing highs / {len(lo_t)} swing lows (k={SWING_K_DEFAULT})")
    zz = zigzag_pivots(bars)
    n_hi = int((zz["kind"] == "crest").sum())
    n_lo = int((zz["kind"] == "trough").sum())
    print(f"{len(zz)} confirmed zigzag pivots ({n_hi} crests / {n_lo} troughs, "
          f"atr_mult={ATR_MULT:g}, atr_period={ATR_PERIOD}, min_bars={MIN_BARS})")
    if not zz.empty:
        print(zz.tail(5).to_string(index=False))
