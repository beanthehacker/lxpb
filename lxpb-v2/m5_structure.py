"""
m5_structure.py
================
Derived M5 price structure that is NOT an LXPB level: confirmed swing pivots
and threshold zigzag pivots, both read straight off the one continuous M5
series (`lxpb_levels_cache.m5_bars_continuous` -- see the "continuous
contracts only" convention in CLAUDE.md; nothing here ever resamples .scid
into bars).

Two consumers, both in the M5-native strategy report:

  * ZIGZAG PIVOTS -> the two target rules (`zigzag_pivots`,
    `most_recent_pivot`): the swing-extreme target and the opposite-M5
    zigzag-anchor target.

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

import lxpb_levels_cache as LC        # noqa: E402

SWING_K_DEFAULT = 2                   # bars required on EACH side of a pivot

ZIGZAG_THRESHOLD_DEFAULT = 3.0        # pt reversal that confirms a new zigzag leg
ZIGZAG_MIN_BARS_DEFAULT = 3           # bars required after the extreme before it can confirm
# Second, FAST confirmation criterion: a bigger reversal is allowed to confirm
# sooner. A pivot confirms on EITHER (threshold, min_bars) OR
# (fast threshold, fast min_bars).
ZIGZAG_FAST_THRESHOLD_DEFAULT = 5.0
ZIGZAG_FAST_MIN_BARS_DEFAULT = 2

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


def entry_cutoff(touch_time):
    """The instant this strategy is allowed to see structure up to: the last
    nanosecond before the entry's OWN M5 candle opened. Same convention (and
    same reason) as render_ss_confl_finetune_report._live_m5_before_entry --
    ledger/bar timestamps label bar STARTS, so querying at the tick itself
    would expose the rest of an unfinished candle."""
    return _as_utc(touch_time).floor("5min") - pd.Timedelta(nanoseconds=1)


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


def swings_near(price, tol, lo_ts, hi_ts, is_low, k=SWING_K_DEFAULT, bars=None):
    """Every confirmed swing pivot within +/-`tol` points of `price` whose
    own bar falls in [lo_ts, hi_ts), as a list of (time, pivot_price).

    `is_low` picks lows (the side that matters for a LONG entry) or highs
    (for a SHORT). CONFIRMED means the pivot's own `k` right-hand bars had
    all closed before `hi_ts` -- a pivot whose right side is still forming
    was not visible to anyone at `hi_ts` and must not move an entry."""
    hi_times, hi_prices, lo_times, lo_prices = swing_pivots(bars, k)
    times, prices = (lo_times, lo_prices) if is_low else (hi_times, hi_prices)
    if len(times) == 0:
        return []
    lo_ts, hi_ts = _as_utc(lo_ts), _as_utc(hi_ts)
    # A pivot at time t is only confirmed once its k-th right-hand bar has
    # closed, i.e. at t + (k + 1) * 5min.
    confirm_by = hi_ts - pd.Timedelta(minutes=5 * (k + 1))
    idx = np.flatnonzero((times >= lo_ts.to_datetime64()) &
                         (times <= confirm_by.to_datetime64()) &
                         (np.abs(prices - float(price)) <= float(tol)))
    return [(pd.Timestamp(times[i]).tz_localize("UTC"), float(prices[i])) for i in idx]


# --------------------------------------------------------------------------
# Zigzag legs (new "opposite M5 target": most recent leg's crest/trough)
# --------------------------------------------------------------------------

def zigzag_pivots(bars=None, threshold_pts=ZIGZAG_THRESHOLD_DEFAULT,
                  min_bars=ZIGZAG_MIN_BARS_DEFAULT,
                  fast_threshold_pts=ZIGZAG_FAST_THRESHOLD_DEFAULT,
                  fast_min_bars=ZIGZAG_FAST_MIN_BARS_DEFAULT):
    """Every CONFIRMED zigzag pivot over the continuous M5 series, as a
    DataFrame of [time, price, kind, confirmed_time] sorted by time.
    `kind` is 'high' (a crest) or 'low' (a trough).

    Classic threshold zigzag on bar highs/lows, not closes: walk forward
    extending the running extreme in the current direction (a crest's price
    only ever rises while one is being sought, a trough's only ever falls),
    and confirm it -- flip direction, anchor the new running extreme at the
    bar that did it -- the first bar, at least `min_bars` after the extreme's
    own bar, where the OPPOSITE side has reversed by at least `threshold_pts`
    from that running extreme (the reversal is tracked cumulatively since
    the extreme, so a bar that dips deep enough and then bounces still
    counts once `min_bars` catches up -- the threshold does not have to be
    re-met on the confirming bar itself). This is deliberately not
    `swing_pivots`' k-bar fractal test: a fractal only asks whether a bar
    stands alone among its `k` neighbours, so two bars a small, noisy chop
    apart can both register as separate confirmed pivots even though price
    never actually left the first one's neighbourhood. A zigzag leg is
    real only once price has demonstrably moved on for real, not just
    ticked past the threshold on a single bar -- which is also what
    resolves the "lower second crest" case on its own: if the chop between
    two highs never reverses `threshold_pts` off the first one (or does so
    for less than `min_bars`), the first one is still the running extreme
    and the second, lower high is simply never confirmed as its own leg.

    `confirmed_time` is the bar that completed the reversal -- the instant
    this pivot first became knowable, not the pivot's own bar. A caller
    asking "as of cutoff" must require confirmed_time < cutoff too, or it is
    using a pivot nothing yet proved was real (see `most_recent_pivot`).
    The trailing, still-extending candidate at the end of the series is
    never confirmed and so never appears here.

    TWO confirmation criteria, either sufficing: (a) a reversal of at least
    `threshold_pts` with at least `min_bars` bars since the extreme, OR (b) a
    reversal of at least `fast_threshold_pts` with at least `fast_min_bars`
    bars since the extreme (default 5pt / 2 bars). (b) lets a sharp swing
    confirm before a small undercut of the extreme can restart the count.
    Pass fast_threshold_pts=None to use (a) alone.

    Memoised per parameter set for the default series."""
    if bars is None:
        key = (float(threshold_pts), int(min_bars),
               None if fast_threshold_pts is None else float(fast_threshold_pts),
               None if fast_min_bars is None else int(fast_min_bars))
        if key in _ZIGZAG_CACHE:
            return _ZIGZAG_CACHE[key]
        bars = LC.m5_bars_continuous()
        out = _scan_zigzag(bars, threshold_pts, min_bars, fast_threshold_pts, fast_min_bars)
        _ZIGZAG_CACHE[key] = out
        return out
    return _scan_zigzag(bars, threshold_pts, min_bars, fast_threshold_pts, fast_min_bars)


def _scan_zigzag(bars, threshold_pts, min_bars, fast_threshold_pts=None, fast_min_bars=None):
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["time", "price", "kind", "confirmed_time"])
    if not np.isfinite(threshold_pts) or threshold_pts <= 0:
        raise ValueError("Zigzag threshold must be finite and positive")
    if min_bars < 1:
        raise ValueError("Zigzag min_bars must be at least 1")
    highs = bars["high"].to_numpy(float)
    lows = bars["low"].to_numpy(float)
    n = len(bars)
    use_fast = fast_threshold_pts is not None and fast_min_bars is not None
    if use_fast and (not np.isfinite(fast_threshold_pts) or fast_threshold_pts <= 0
                     or fast_min_bars < 1):
        raise ValueError("Fast zigzag threshold/min_bars must be positive")

    def confirms(reversal, bars_since):
        if reversal >= threshold_pts and bars_since >= min_bars:
            return True
        return use_fast and reversal >= fast_threshold_pts and bars_since >= fast_min_bars

    rows = []
    looking_for = "high"
    extreme_price, extreme_idx = highs[0], 0
    # Lowest low (seeking a high) / highest high (seeking a low) over the bars
    # AFTER the extreme's own bar. The extreme bar's own opposite side is
    # excluded: its high/low order inside the bar is unknown, so it is not
    # evidence of a reversal (standard zigzag behaviour).
    worst_since = np.inf
    for i in range(1, n):
        if looking_for == "high":
            if highs[i] > extreme_price:
                extreme_price, extreme_idx, worst_since = highs[i], i, np.inf
            else:
                worst_since = min(worst_since, lows[i])
                if confirms(extreme_price - worst_since, i - extreme_idx):
                    rows.append((bars.index[extreme_idx], extreme_price, "high", bars.index[i]))
                    looking_for = "low"
                    extreme_price, extreme_idx, worst_since = lows[i], i, -np.inf
        else:
            if lows[i] < extreme_price:
                extreme_price, extreme_idx, worst_since = lows[i], i, -np.inf
            else:
                worst_since = max(worst_since, highs[i])
                if confirms(worst_since - extreme_price, i - extreme_idx):
                    rows.append((bars.index[extreme_idx], extreme_price, "low", bars.index[i]))
                    looking_for = "high"
                    extreme_price, extreme_idx, worst_since = highs[i], i, np.inf
    return pd.DataFrame(rows, columns=["time", "price", "kind", "confirmed_time"])


def most_recent_pivot(pivots, kind, cutoff, after=None):
    """(time, price) of the most recent `kind` ('high' for a crest, 'low'
    for a trough) zigzag pivot that was both FORMED and CONFIRMED strictly
    before `cutoff`, and (if `after` given) formed strictly after `after` --
    only a
    leg the market built while this level was waiting to be retested
    qualifies. (None, None) if none qualifies.

    Requiring confirmed_time < cutoff too (not just the pivot's own bar) is
    what keeps this causal: a still-unconfirmed candidate crest is one the
    market has not yet proven was actually left behind, most obviously when
    the very drop into the retest is what would have confirmed it."""
    if pivots is None or pivots.empty:
        return None, None
    cutoff = _as_utc(cutoff)
    elig = pivots[(pivots["kind"] == kind) & (pivots["time"] < cutoff) &
                  (pivots["confirmed_time"] < cutoff)]
    if after is not None:
        elig = elig[elig["time"] > _as_utc(after)]
    if elig.empty:
        return None, None
    row = elig.loc[elig["time"].idxmax()]
    return pd.Timestamp(row["time"]), float(row["price"])


if __name__ == "__main__":
    bars = LC.m5_bars_continuous()
    hi_t, hi_p, lo_t, lo_p = swing_pivots(bars)
    print(f"{len(hi_t)} swing highs / {len(lo_t)} swing lows (k={SWING_K_DEFAULT})")
    zz = zigzag_pivots(bars)
    n_hi = int((zz["kind"] == "high").sum())
    n_lo = int((zz["kind"] == "low").sum())
    print(f"{len(zz)} confirmed zigzag pivots ({n_hi} crests / {n_lo} troughs, "
          f"threshold={ZIGZAG_THRESHOLD_DEFAULT:g}pt, min_bars={ZIGZAG_MIN_BARS_DEFAULT})")
    if not zz.empty:
        print(zz.tail(5).to_string(index=False))
