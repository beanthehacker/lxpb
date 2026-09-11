"""
liquidity.py
=============
A tick-measured LIQUIDITY GATE: don't send an order into a market whose
book has just evaporated. Planned macro releases (FOMC, NFP, CPI, PPI) and
unscheduled headline shocks both do the same thing to ES -- top of book
empties, the quoted spread widens past one tick, and a resting order that
"fills" there is really just getting run over -- so this gate is MEASURED
off the tape rather than keyed to an economic calendar. That way it covers
the unscheduled events (a war headline, a surprise announcement) exactly
the same way it covers the scheduled ones, and it needs no calendar data
this repo does not have.

What is actually measurable
---------------------------
Sierra's `.scid` records are TRADES, not book snapshots -- there is no
depth of market on disk, so "how many contracts are resting at the level"
cannot be read directly. What every record DOES carry is the prevailing
quote at that trade: `Low` is the best bid and `High` the best ask (see
render_stop_target_report._market_fill's own docstring for the verification
of that on 2026 data). The share of records whose quoted spread is wider
than ONE tick is therefore a direct, per-trade read on how thin the top of
book is -- and it separates news windows from normal trade by two orders of
magnitude. Measured over Jul-Aug 2026 ES, 5-minute windows:

    normal RTH                                    0.001 - 0.02
    quiet overnight                               0.02
    08:30 ET jobless-claims-grade release         0.16
    FOMC statement (14:00 ET)                     0.21
    NFP / CPI release (08:30 ET)                  0.45
    Globex/ETH reopen (18:00 ET)                  0.43 - 0.76

`WIDE_SPREAD_SHARE_MAX` defaults to 0.20, which trips on 1.2% of all
5-minute windows over those two months. That level is deliberately low
enough to catch the FOMC statement (whose 5-minute window reads 0.21-0.33
for the first six minutes and falls back under 0.06 by +12m) while still
sitting an order of magnitude above normal trade. It does also catch the
second-tier 08:30 ET releases at ~0.16; raise it to 0.25 to keep only the
big ones.

(The reopen and the 16:00 ET settlement print show up here too, and are
genuinely thin -- but both are already handled by their own rules in the
strategy report: the `globex_eth_open` dynamic filter and the end-of-day
flat rule in trade_management.py.)

Using it
--------
`gate(ts)` answers "was liquidity too thin to trade at this instant", from
the `WINDOW_MINUTES` of tape ENDING at ts -- so a trade whose fill lands
minutes after a release is still blocked, and one an hour later is not:
trading re-enables by itself as soon as the spread comes back in. Its
second return value is the full metric dict, which the report puts in the
row's own tooltip so a blocked trade can be reviewed rather than just
disappearing.
"""
import numpy as np
import pandas as pd

import render_labels_report as R  # noqa: E402

TICK_SIZE = R.TICK_SIZE_DEFAULT

WINDOW_MINUTES_DEFAULT = 5.0
# Fewer single-trade records than this in the window and the measurement
# itself is not trustworthy -- see gate()'s "unknown, not blocked" rule.
MIN_RECORDS_DEFAULT = 20
WIDE_SPREAD_SHARE_MAX_DEFAULT = 0.20


def _as_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def spread_metrics(ts, window_minutes=WINDOW_MINUTES_DEFAULT):
    """Liquidity metrics from the real tape over [ts - window, ts), or None
    if that window holds no usable records at all.

    Only SINGLE-TRADE records (`Trades` == 1) contribute to the spread
    measures: on an aggregated record High/Low are a bar's own range, not a
    quote, so counting one as a "wide spread" would be meaningless. Volume
    and record counts still come from every record in the window.

    Returns: n_records, n_quotes (the single-trade subset), volume,
    trades_per_min, median_spread_ticks, wide_spread_share (share of quotes
    wider than one tick), max_spread_ticks.
    """
    ts = _as_utc(ts)
    if not np.isfinite(window_minutes) or window_minutes <= 0:
        raise ValueError("Liquidity window must be finite and positive")
    lo = ts - pd.Timedelta(minutes=window_minutes)
    ticks = R._ticks_for_window(lo, ts)
    if ticks is None or ticks.empty:
        return None
    quotes = ticks[(ticks["Trades"] == 1) & (ticks["High"] >= ticks["Low"])]
    spread_ticks = ((quotes["High"] - quotes["Low"]) / TICK_SIZE).round()
    return {
        "n_records": int(len(ticks)),
        "n_quotes": int(len(quotes)),
        "volume": float(ticks["Volume"].sum()),
        "trades_per_min": float(len(ticks) / window_minutes),
        "median_spread_ticks": (float(spread_ticks.median()) if len(spread_ticks) else None),
        "max_spread_ticks": (float(spread_ticks.max()) if len(spread_ticks) else None),
        "wide_spread_share": (float((spread_ticks > 1).mean()) if len(spread_ticks) else None),
        "window_minutes": float(window_minutes),
    }


def gate(ts, window_minutes=WINDOW_MINUTES_DEFAULT,
         wide_spread_share_max=WIDE_SPREAD_SHARE_MAX_DEFAULT,
         min_records=MIN_RECORDS_DEFAULT):
    """(blocked, metrics) for one instant.

    blocked is True when the window's `wide_spread_share` is at or above
    `wide_spread_share_max` -- the book at the top has thinned out far past
    anything normal trade produces (see the module docstring's measured
    table).

    A window with fewer than `min_records` usable quotes is UNKNOWN, not
    blocked: a thin tape is the normal state of a quiet overnight hour, and
    blocking every trade whose measurement is merely uninformative would
    silently delete a whole session from the strategy. metrics carries
    `sparse: True` in that case so the caller can still show it.
    """
    metrics = spread_metrics(ts, window_minutes)
    if metrics is None:
        return False, None
    share = metrics["wide_spread_share"]
    if share is None or metrics["n_quotes"] < min_records:
        metrics["sparse"] = True
        return False, metrics
    metrics["sparse"] = False
    return bool(share >= wide_spread_share_max), metrics


def describe(metrics):
    """One-line human summary of a metrics dict, for a row tooltip."""
    if not metrics:
        return "no tape in the liquidity window"
    share = metrics.get("wide_spread_share")
    share_str = "n/a" if share is None else f"{share * 100:.1f}%"
    med = metrics.get("median_spread_ticks")
    med_str = "n/a" if med is None else f"{med:g}"
    return (f"{metrics['window_minutes']:g}min before entry: "
            f"{share_str} of quotes wider than 1 tick, median spread {med_str} tick(s), "
            f"{metrics['trades_per_min']:.0f} records/min, "
            f"{metrics['volume']:.0f} contracts"
            + (" [sparse tape -- not measurable]" if metrics.get("sparse") else ""))
