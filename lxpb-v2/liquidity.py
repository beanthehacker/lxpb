"""
liquidity.py
=============
A tick-measured NEWS-SPIKE gate: don't send an order into a market that has
just been hit by a headline or a release. Planned macro releases (FOMC, NFP,
CPI, PPI) and unscheduled headline shocks do the same thing to ES -- one or
two seconds in which price travels several points and the top of the book
empties (the quoted spread opens past one tick) -- so this gate is MEASURED
off the tape rather than keyed to an economic calendar. That covers the
unscheduled events exactly as it covers the scheduled ones, and needs no
calendar data this repo does not have.

(It replaces the earlier "share of quotes wider than one tick over the last
five minutes" gate, 2026-10-15. That measure is built for sustained events
such as FOMC; a news burst lasts one to five seconds, so averaged over five
minutes it read 2-3% on September 28 2026's two stop-outs, against a 20%
trip level, and the tag never fired.)

What is actually measurable
---------------------------
Sierra's `.scid` records are TRADES, not book snapshots -- there is no depth
of market on disk, so "how many contracts are resting at the level" cannot be
read directly. What every single-trade record DOES carry is the prevailing
quote at that trade: `Low` is the best bid and `High` the best ask (see
render_stop_target_report._market_fill's own docstring for the verification
of that on 2026 data). So thin liquidity is read through two proxies, both
taken one SECOND at a time:

    range   the high-low of the trade prices inside the second;
    spread  the widest quote (ask - bid, in ticks) seen on a single-trade
            record inside the second.

A second is a SHOCK second when its range is at least `range_pts` (3.0 pt)
OR its widest quote is at least `spread_ticks` (2 ticks; one tick is the
normal ES spread). A trade is tagged NEWS SPIKE when any shock second falls
in the `lookback_s` (120 s) before its touch, the touch's own second
included up to the touch instant.

Window convention (the same in the batch gate and in the streaming tracker
the live engine uses, so the two agree): the tape read is
[floor(touch to the second) - lookback_s, touch), seconds are whole clock
seconds, and the touch's own second counts with only the ticks before the
touch -- nothing after the touch is ever read.

Measured on Sep 2026 ES (see the session of 2026-10-15): 12 of the 35
range-fade trades had a shock second in that window at 3 pt / 4 ticks and
went 3 wins, 9 losses (-5.1 R) against -0.15 R for the other 23; 563 of
1,058,919 active seconds were shock seconds at that setting. Spread 2 ticks
trips much more often than 4 -- run it and look at the tag's hover text.

Using it
--------
`gate(ts)` answers "was there a news-type shock just before this instant";
its second return value is the full metric dict (count of shock seconds,
the worst ones with their time, range, spread and volume), which the report
puts in the row's own tooltip via `describe` so a tagged trade can be
reviewed. A window with no usable tape is UNKNOWN, not flagged.

`ShockTape` is the same definition for a streaming consumer
(live_range/engine.py): feed it every tape record, ask `flagged(t)` before
the next one.
"""
import collections

import numpy as np
import pandas as pd

TICK_SIZE = 0.25

LOOKBACK_S_DEFAULT = 120
SHOCK_RANGE_PTS_DEFAULT = 3.0
SHOCK_SPREAD_TICKS_DEFAULT = 2

_S_NS = 10 ** 9
_MAX_LISTED = 3          # shock seconds named in a tooltip, most recent first


def _as_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _check_params(lookback_s, range_pts, spread_ticks):
    if not np.isfinite(lookback_s) or lookback_s <= 0:
        raise ValueError("News-spike lookback must be finite and positive")
    if not np.isfinite(range_pts) or range_pts <= 0:
        raise ValueError("News-spike range threshold must be finite and positive")
    if not np.isfinite(spread_ticks) or spread_ticks <= 0:
        raise ValueError("News-spike spread threshold must be finite and positive")


def shock_metrics(ts, lookback_s=LOOKBACK_S_DEFAULT, range_pts=SHOCK_RANGE_PTS_DEFAULT,
                  spread_ticks=SHOCK_SPREAD_TICKS_DEFAULT):
    """News-spike metrics from the real tape over [floor(ts, 1s) - lookback_s, ts),
    or None if that window holds no usable records at all.

    Returns: n_records, n_seconds (seconds that traded), n_shock, and `shocks`:
    up to the three most recent shock seconds, each {time (UTC Timestamp),
    offset_s (seconds before ts), range_pts, spread_ticks, volume}; plus the
    thresholds used.

    Spread is read from SINGLE-TRADE records only (`Trades` == 1): on an
    aggregated record High/Low are a bar's own range, not a quote.
    """
    import render_labels_report as R  # deferred: pulls in the tick store
    _check_params(lookback_s, range_pts, spread_ticks)
    ts = _as_utc(ts)
    lo = ts.floor("1s") - pd.Timedelta(seconds=lookback_s)
    ticks = R._ticks_for_window(lo, ts)
    if ticks is None or ticks.empty:
        return None
    ticks = ticks[(ticks["Volume"] > 0) & (ticks["Close"] > 0)]
    if ticks.empty:
        return None
    sec = ticks.index.floor("1s")
    quote = (ticks["Trades"] == 1) & (ticks["Low"] > 0) & (ticks["High"] >= ticks["Low"])
    spr = ((ticks["High"] - ticks["Low"]) / TICK_SIZE).round().where(quote, 0.0)
    per_sec = pd.DataFrame({
        "hi": ticks["Close"].groupby(sec).max(),
        "lo": ticks["Close"].groupby(sec).min(),
        "spread_ticks": spr.groupby(sec).max(),
        "volume": ticks["Volume"].groupby(sec).sum(),
    })
    per_sec["range_pts"] = per_sec["hi"] - per_sec["lo"]
    shock = per_sec[(per_sec["range_pts"] >= range_pts) | (per_sec["spread_ticks"] >= spread_ticks)]
    listed = []
    for t, row in shock.iloc[::-1].head(_MAX_LISTED).iterrows():
        listed.append({
            "time": t.tz_convert("UTC"),
            "offset_s": float((ts - t.tz_convert("UTC")).total_seconds()),
            "range_pts": float(row["range_pts"]),
            "spread_ticks": float(row["spread_ticks"]),
            "volume": float(row["volume"]),
        })
    return {
        "n_records": int(len(ticks)),
        "n_seconds": int(len(per_sec)),
        "n_shock": int(len(shock)),
        "shocks": listed,
        "lookback_s": float(lookback_s),
        "range_pts_min": float(range_pts),
        "spread_ticks_min": float(spread_ticks),
    }


def gate(ts, lookback_s=LOOKBACK_S_DEFAULT, range_pts=SHOCK_RANGE_PTS_DEFAULT,
         spread_ticks=SHOCK_SPREAD_TICKS_DEFAULT):
    """(flagged, metrics) for one instant: flagged is True when at least one
    shock second sits in the window (see shock_metrics). A window with no
    tape at all is (False, None) -- unknown, not flagged."""
    metrics = shock_metrics(ts, lookback_s, range_pts, spread_ticks)
    if metrics is None:
        return False, None
    return metrics["n_shock"] > 0, metrics


def describe(metrics):
    """One-line human summary of a metrics dict, for a row tooltip."""
    if not metrics:
        return "no tape in the news-spike window"
    tz = "America/Los_Angeles"
    parts = []
    for s in metrics["shocks"]:
        parts.append(f"{s['time'].tz_convert(tz):%H:%M:%S} PT: {s['range_pts']:g} pt range, "
                     f"{s['spread_ticks']:g}-tick spread, {s['volume']:.0f} contracts, "
                     f"{s['offset_s']:.0f}s before entry")
    head = (f"{metrics['n_shock']} shock second(s) in the {metrics['lookback_s']:g}s before entry "
            f"(shock = 1s range >= {metrics['range_pts_min']:g} pt or quote spread >= "
            f"{metrics['spread_ticks_min']:g} ticks)")
    return head + (": " + "; ".join(parts) if parts else "")


class ShockTape:
    """The same news-spike definition for a streaming consumer.

    `on_record(t_ns, px, spread_ticks)` for every tape record in time order
    (`spread_ticks` is None for a record that is not a single-trade quote);
    `flagged(t_ns)` asks "is there a shock second in
    [floor(t, 1s) - lookback_s, t)" from the records fed so far, so the caller
    asks BEFORE feeding the record at t, as the batch gate reads ticks < ts.
    Memory is the shock seconds inside the lookback, nothing else.
    """

    def __init__(self, lookback_s=LOOKBACK_S_DEFAULT, range_pts=SHOCK_RANGE_PTS_DEFAULT,
                 spread_ticks=SHOCK_SPREAD_TICKS_DEFAULT):
        _check_params(lookback_s, range_pts, spread_ticks)
        self.lookback_s = int(lookback_s)
        self.range_pts = float(range_pts)
        self.spread_ticks = float(spread_ticks)
        self._done = collections.deque()    # clock-second index of every finished shock second
        self._sec = None                    # the second now forming
        self._lo = self._hi = 0.0
        self._spr = 0.0

    def _is_shock(self):
        return (self._hi - self._lo) >= self.range_pts or self._spr >= self.spread_ticks

    def on_record(self, t_ns, px, spread_ticks=None):
        sec = t_ns // _S_NS
        if sec != self._sec:
            if self._sec is not None and self._is_shock():
                self._done.append(self._sec)
            self._sec = sec
            self._lo = self._hi = px
            self._spr = 0.0
        else:
            if px > self._hi:
                self._hi = px
            elif px < self._lo:
                self._lo = px
        if spread_ticks is not None and spread_ticks > self._spr:
            self._spr = spread_ticks

    def flagged(self, t_ns):
        lo = t_ns // _S_NS - self.lookback_s
        dq = self._done
        while dq and dq[0] < lo:
            dq.popleft()
        if dq:
            return True
        return self._sec is not None and self._sec >= lo and self._is_shock()
