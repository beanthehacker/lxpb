"""
m5_structure.py
================
Derived M5 price structure that is NOT an LXPB level: consolidation
(congestion) AREAS and confirmed swing pivots, both read straight off the
one continuous M5 series (`lxpb_levels_cache.m5_bars_continuous` -- see the
"continuous contracts only" convention in CLAUDE.md; nothing here ever
resamples .scid into bars).

Two consumers, both in the M5-native strategy report:

  * CONSOLIDATION AREAS -> the target rule (`consolidation_target`). A
    congestion area is where resting supply/demand actually sits; price
    returning to one is where an open trade should be paid. Only areas
    built INSIDE the trade's own level lifetime count -- wholly after that
    level's P1 (breakout) and wholly before its P2 (retest) -- i.e. the
    congestion the market laid down while the level was waiting to be
    retested. If that area still holds an UNTESTED opposite-type M5 P0 -- a
    level inside its own price band that has never been retested -- that
    P0's own price is the target; otherwise the area's NEAR EDGE is (its
    low for a long, its high for a short -- the first price of the zone the
    trade reaches).

  * SWING PIVOTS -> the "swerved" entry rule. A confirmed swing low sitting
    right on a planned LONG entry (or a swing high on a planned SHORT entry)
    is an obvious, widely-visible price; the strategy moves its resting
    order past it rather than queue behind everyone else. See
    `swings_near` and the report's own `_swerve_entry`.

An area is a maximal run of consecutive M5 bars that is all three of:

  1. at least `MIN_BARS` bars long (default 12 = one hour),
  2. contained in a price band no taller than `MAX_HEIGHT_PTS` (default
     5.0pt) -- the run's own highest high minus its own lowest low, and
  3. CHOPPY rather than trending over that band: Kaufman's efficiency ratio
     of the run's own closes (lxpb.py's own `_efficiency_ratio`, the same
     measure lxpb.py's candidate gate uses) below `MAX_ER`
     (lxpb.ER_CONSOLIDATION_MAX = 0.5).

Rule 3 is what stops a slow, straight drift across the whole band -- which
satisfies rules 1 and 2 on its own -- from being called a consolidation.

The defaults matter, and were chosen by measurement rather than taste. A
looser pair (6 bars / 6.0pt) calls 44% of all M5 bars a consolidation:
there is then almost always an "area" one or two points away from any
entry, and the target rule degenerates into a one-point scalp (median
target 1.25pt, median R 0.33 over Jul-Aug 2026). An hour inside five points
yields 1,290 areas over the whole series, with a median target of 6.75pt
and a median R of 1.72 on the same trades -- i.e. it picks out the
congestion a trader would actually point at.

Runs never span a DATA GAP longer than `MAX_BAR_GAP` (1h): the continuous
series has real holes (weekends, holidays, and any range the TradingView
exports simply don't cover -- `R._report_series_gaps` prints them), and
walking across one as though the two sides were adjacent bars would invent
a "consolidation" out of two unrelated sessions.

Areas are non-overlapping and greedy-maximal in time: each is extended as
far as rule 2 allows before the next one is looked for.
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import lxpb as L                      # noqa: E402
import lxpb_levels_cache as LC        # noqa: E402

MIN_BARS_DEFAULT = 12                 # one hour of M5 bars
MAX_HEIGHT_PTS_DEFAULT = 5.0          # tallest band a congestion area may span
MAX_ER_DEFAULT = L.ER_CONSOLIDATION_MAX   # 0.5 -- lxpb.py's own "consolidating enough"
MAX_BAR_GAP = pd.Timedelta(hours=1)   # a longer hole ends the run (see module docstring)

SWING_K_DEFAULT = 2                   # bars required on EACH side of a pivot

_AREA_CACHE = {}
_PIVOT_CACHE = {}


# --------------------------------------------------------------------------
# Consolidation areas
# --------------------------------------------------------------------------

def consolidation_areas(bars=None, min_bars=MIN_BARS_DEFAULT,
                        max_height=MAX_HEIGHT_PTS_DEFAULT, max_er=MAX_ER_DEFAULT):
    """Every consolidation area in the continuous M5 series, as a DataFrame
    of [start_time, end_time, high, low, n_bars, er] sorted by start_time
    (see the module docstring for the definition). `end_time` labels the
    area's LAST bar's own start, like every other bar timestamp in this
    repo.

    Memoised per (min_bars, max_height, max_er) for the default series --
    one ~150k-bar walk per process, reused by every trade.
    """
    if bars is None:
        key = (float(min_bars), float(max_height), float(max_er))
        if key in _AREA_CACHE:
            return _AREA_CACHE[key]
        bars = LC.m5_bars_continuous()
        out = _scan_areas(bars, min_bars, max_height, max_er)
        _AREA_CACHE[key] = out
        return out
    return _scan_areas(bars, min_bars, max_height, max_er)


def _scan_areas(bars, min_bars, max_height, max_er):
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["start_time", "end_time", "high", "low", "n_bars", "er"])
    if min_bars < 2:
        raise ValueError("A consolidation needs at least 2 bars")
    if not np.isfinite(max_height) or max_height <= 0:
        raise ValueError("Consolidation height must be finite and positive")
    times = _naive_utc_values(bars.index)
    highs = bars["high"].to_numpy(float)
    lows = bars["low"].to_numpy(float)
    closes = bars["close"].to_numpy(float)
    # A gap longer than MAX_BAR_GAP between bar i and bar i+1 forbids any run
    # from spanning the two (see the module docstring).
    breaks = np.diff(times) > MAX_BAR_GAP.to_timedelta64()
    n = len(bars)

    rows = []
    i = 0
    while i < n:
        hi, lo = highs[i], lows[i]
        j = i
        while j + 1 < n and not breaks[j]:
            nhi, nlo = max(hi, highs[j + 1]), min(lo, lows[j + 1])
            if nhi - nlo > max_height:
                break
            j += 1
            hi, lo = nhi, nlo
        n_bars = j - i + 1
        er = L._efficiency_ratio(list(closes[i:j + 1])) if n_bars >= 2 else None
        if n_bars >= min_bars and er is not None and er < max_er:
            rows.append((bars.index[i], bars.index[j], hi, lo, n_bars, er))
            i = j + 1
        else:
            i += 1
    return pd.DataFrame(rows, columns=["start_time", "end_time", "high", "low", "n_bars", "er"])


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


def consolidation_target(areas, live_opposite, price, is_long, cutoff,
                         min_pts, max_pts, after=None):
    """(target_price, info) for one trade, or (None, None) if no previous
    consolidation area qualifies.

    `areas` -- consolidation_areas() output. `live_opposite` -- the
    opposite-type M5 ledger rows that were BROKEN OUT and still ALIVE
    (i.e. never retested: "untested still") as of `cutoff`, which the
    caller supplies (render_ss_confl_finetune_report._live_m5_before_entry
    is exactly that query). `price` is the trade's own fill, `cutoff` the
    entry_cutoff above.

    An area "has" such a P0 when the P0's own PRICE falls inside the area's
    band. Not when it merely formed during the area's own bars: what makes
    the zone worth exiting into is untested structure sitting AT those
    prices, whenever it was laid down. (Measured on Jul-Aug 2026, the
    price-containment reading finds a P0 for 29 of 69 trades where the
    formed-during reading finds 6.)

    Eligible areas lie ENTIRELY INSIDE the trade's own (`after`, `cutoff`)
    window -- first bar strictly after `after`, last bar completed strictly
    before `cutoff`. The caller passes this level's own P1 (breakout) as
    `after` and its P2 (retest) as `cutoff`, so the only congestion that
    can be a target is congestion the market built while THIS level was
    waiting to be retested: the area is where price went after the breakout
    and stalled, and the trade is a return to it. Structure older than P1
    belongs to a move this level had no part in, and an area whose last bar
    is still forming at P2 is not a place price is returning TO. `after` is
    optional only so a caller with no level of its own can omit the lower
    bound; the report always supplies it.

    Eligible areas also sit in the trade's favourable direction, with their
    NEAR EDGE (low for a long, high for a short) `min_pts`..`max_pts` away
    from the fill.

    Preference order, nearest area first:
      1. an area still holding an untested opposite-type P0 (formed inside
         that area's own span) at a qualifying distance -> that P0's own
         price, info['src'] == 'consol_p0';
      2. otherwise the NEAREST eligible area's own near edge,
         info['src'] == 'consol_edge'.

    info also carries the chosen area (start/end/high/low) and, for
    'consol_p0', the ledger row that supplied the price.
    """
    if areas is None or areas.empty:
        return None, None
    cutoff = _as_utc(cutoff)
    elig = areas[areas["end_time"] < cutoff]
    if after is not None:
        elig = elig[elig["start_time"] > _as_utc(after)]
    if elig.empty:
        return None, None
    edge = elig["low"] if is_long else elig["high"]
    dist = (edge - price) if is_long else (price - edge)
    elig = elig.assign(edge=edge, dist=dist)
    elig = elig[(elig["dist"] >= min_pts) & (elig["dist"] <= max_pts)]
    if elig.empty:
        return None, None
    elig = elig.sort_values("dist")

    opp = live_opposite
    if opp is not None and not opp.empty:
        opp_price = opp["price"].astype(float)
        opp_dist = (opp_price - price) if is_long else (price - opp_price)
        for _, area in elig.iterrows():
            inside = ((opp_price >= area["low"]) & (opp_price <= area["high"]) &
                      (opp_dist >= min_pts) & (opp_dist <= max_pts))
            if not inside.any():
                continue
            sub = opp[inside]
            best = sub.loc[opp_dist[inside].idxmin()]
            return float(best["price"]), {
                "src": "consol_p0", "area": _area_info(area),
                "level": best.to_dict(),
            }

    area = elig.iloc[0]
    return float(area["edge"]), {"src": "consol_edge", "area": _area_info(area), "level": None}


def _area_info(area):
    return {"start_time": pd.Timestamp(area["start_time"]),
            "end_time": pd.Timestamp(area["end_time"]),
            "high": float(area["high"]), "low": float(area["low"]),
            "n_bars": int(area["n_bars"]), "er": float(area["er"])}


# --------------------------------------------------------------------------
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


if __name__ == "__main__":
    bars = LC.m5_bars_continuous()
    areas = consolidation_areas(bars)
    print(f"{len(bars)} M5 bars -> {len(areas)} consolidation areas "
          f"(min {MIN_BARS_DEFAULT} bars, max {MAX_HEIGHT_PTS_DEFAULT:g}pt, "
          f"ER < {MAX_ER_DEFAULT:g})")
    if not areas.empty:
        span = (areas["n_bars"].sum() / len(bars)) * 100
        print(f"  covering {span:.1f}% of all bars; median height "
              f"{(areas['high'] - areas['low']).median():.2f}pt, median "
              f"{areas['n_bars'].median():.0f} bars")
        print(areas.tail(5).to_string(index=False))
    hi_t, hi_p, lo_t, lo_p = swing_pivots(bars)
    print(f"{len(hi_t)} swing highs / {len(lo_t)} swing lows (k={SWING_K_DEFAULT})")
