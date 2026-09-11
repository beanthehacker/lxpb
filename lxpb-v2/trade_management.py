"""
trade_management.py
====================
Plug-n-play post-entry trade-management rules, usable on any trigger
timeframe, independent of which strategy generated the trade. A "rule" is
just a function that looks at price/structure after entry and either
tightens the stop or ends the trade early; `resolve_managed_trade` runs the
active rules against the SAME tick-accurate machinery
`render_stop_target_report.resolve_trades` uses (real 1s-tick pinning via
`analyze_breakout_exits_1min._pin_exact_exit` / `_market_fill`), so a
managed trade's outcome is directly comparable to its unmanaged baseline.

Both rules are symmetric across direction (LHPB/long and LLPB/short are
mirror images of each other -- see each rule's own docstring):

  Rule 1 -- thrust-P1 stop trail (`thrust_trail_events`). LONG: when an M5
  breakout candle closes above the high wicks of several still-live P0
  LHPB candles (making that candle their shared P1), the stop trails to
  one tick below that candle's own low. SHORT (mirrored): when a breakout
  candle closes below the low wicks of several still-live P0 LLPB
  candles, the stop trails to one tick above that candle's own high.
  "Several" is a tiered threshold: 1 P0 is enough if any member is a
  swing or a spike candle (lxpb.py's own is_swing/is_spike), 2 if every
  member has a "slight" wick (< 10% of that candle's own range), 3
  otherwise. No requirement on the breakout candle's own body size --
  any candle closing past enough still-live P0s qualifies. The stop only
  ever moves TOWARDS the target, and nothing pins it to the losing side
  of entry: when the breakout candle's own low (LHPB) already sits above
  entry, the trail parks the stop in profit.

  Rule 2 -- RR-floor exit. At every M5 candle close after entry, the
  remaining reward (target - current close) divided by the remaining risk
  (current close - CURRENT stop, i.e. post-trail if rule 1 already moved
  it) is compared to RR_FLOOR_MIN_RR; at or below that floor, the trade
  exits at market.

  Rule 3 -- END-OF-DAY FLAT (`eod_exit_signal` / `entry_blocked`). No
  position is carried past the end of the trading day: an open trade is
  closed at market before `EOD_FLAT_PT` (12:45 Pacific), and no new entry
  is taken from that instant until the Globex/ETH reopen. Unlike rules 1
  and 2 this is NOT part of the optional management toggle -- it is a hard
  constraint on the strategy itself, so `resolve_with_eod` applies it to
  the BASELINE resolution too and both numbers in the report already have
  it. See `eod_exit_signal` for the exact instant and why the order goes
  out a minute early.

Both rules share one sequential walk (`resolve_managed_trade`) rather than
being independently toggleable, since rule 2's own risk measurement depends
on whatever rule 1 has already done to the stop by that point.

Price-scale note: the M5 ledger (`lxpb_levels_cache.m5_levels`) and
`lxpb_levels_cache.m5_bars_continuous` are both in the ADJUSTED/display
scale (same scale as a trade's own `entry`/`fill_price`). `resolve_trades`'
per-trade 1-minute `bars` are RAW (un-continuous-adjusted) scale. This
module follows `render_stop_target_report.resolve_trades`'s own convention:
do all stop/target-vs-bar comparisons as raw prices (`raw_entry -/+
points`), and do the M5-close-price rule-2 check entirely in adjusted scale
(both sides of that comparison -- the M5 bar's own close, and
entry-derived stop/target -- are adjusted, so no offset conversion is
needed there).
"""
import pandas as pd
import numpy as np

import render_labels_report as R                 # noqa: E402
import render_stop_target_report as SR           # noqa: E402
import analyze_breakout_exits_1min as M           # noqa: E402
import lxpb_levels_cache as LC                    # noqa: E402

TICK_SIZE = R.TICK_SIZE_DEFAULT

# Rule 1 -- thrust-P1 stop trail
SLIGHT_WICK_RATIO = 0.10        # a P0's own wick < 10% of its range is "slight"

# Rule 2 -- RR-floor exit
RR_FLOOR_MIN_RR = 0.2

# Rule 3 -- end-of-day flat. Pacific times of day (the whole repo reports in
# PT -- render_labels_report._to_pt_str), converted per timestamp so DST is
# handled by the zone, never by a fixed UTC offset.
EOD_FLAT_PT = pd.Timedelta(hours=12, minutes=45)   # no position may be open at/after this
EOD_EXIT_LEAD = pd.Timedelta(minutes=1)            # so the market order's FILL lands before it
SESSION_REOPEN_PT = pd.Timedelta(hours=15)         # Globex/ETH reopen (18:00 ET); entries resume
_PT = "America/Los_Angeles"


def _pt_tod(ts):
    """Pacific time-of-day of an instant, as a Timedelta since PT midnight."""
    pt = _norm_utc(ts).tz_convert(_PT)
    return pd.Timedelta(hours=pt.hour, minutes=pt.minute, seconds=pt.second,
                        microseconds=pt.microsecond, nanoseconds=pt.nanosecond)


def entry_blocked(ts):
    """True if no NEW entry may be taken at `ts`: the last 15 minutes of the
    day session (from EOD_FLAT_PT) through the Globex/ETH reopen. ES is
    closed for most of that span anyway (13:00-15:00 PT); what this rule
    really forbids is opening a position that could not be closed before
    EOD_FLAT_PT, and the reopen is where a fresh day's trading resumes."""
    return EOD_FLAT_PT <= _pt_tod(ts) < SESSION_REOPEN_PT


def eod_exit_signal(entry_ts):
    """The instant the flattening MARKET order is sent for a trade entered
    at `entry_ts`: the first `EOD_FLAT_PT - EOD_EXIT_LEAD` (12:44 PT) that
    falls strictly after the entry. A trade opened during the evening
    Globex session is therefore flattened before the NEXT day's 12:45 PT,
    not held into it.

    The order goes out one minute early on purpose: the requirement is that
    the position is CLOSED before 12:45 PT, and a market order sent at
    12:45:00 would fill after it."""
    entry_ts = _norm_utc(entry_ts)
    signal_tod = EOD_FLAT_PT - EOD_EXIT_LEAD
    pt_day = entry_ts.tz_convert(_PT).normalize()
    for extra_days in (0, 1):
        # tz_localize on the naive wall-clock, so a DST transition day keeps
        # its real 12:44 PT rather than drifting by an hour.
        candidate = ((pt_day + pd.Timedelta(days=extra_days)).tz_localize(None)
                     + signal_tod).tz_localize(_PT).tz_convert("UTC")
        if candidate > entry_ts:
            return candidate
    raise AssertionError("unreachable: tomorrow's 12:44 PT is always after entry")


def resolve_with_eod(trade, bars):
    """BASELINE (unmanaged) resolution of one trade, under rule 3.

    Runs `SR.resolve_trades`' own tick-accurate machinery over the trade's
    bars TRUNCATED at its end-of-day signal, so any stop or target the trade
    really reached first still resolves exactly as it would have without
    this rule. Only a trade still open at the signal is changed: it is
    flattened at the prevailing bid/ask (`SR._market_fill`, the same fill
    model rule 2 uses), outcome "eod_flat", with a real, variable, signed R.

    Returns the same shape `SR.resolve_trades(...)[0]` does."""
    if bars is None or bars.empty:
        return SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]
    touch_time = bars.attrs.get("touch_time")
    eod_ts = eod_exit_signal(touch_time if touch_time is not None else bars.index[0])
    if bars.index[-1] < eod_ts:
        return SR.resolve_trades([trade], {0: bars}, stop=None, target=None)[0]

    cut = bars[bars.index < eod_ts]
    if cut.empty:
        return {"outcome": "no_data", "r": None, "exit_time": None, "touch_time": touch_time}
    cut.attrs["touch_time"] = touch_time      # attrs do not survive a boolean mask
    resolved = SR.resolve_trades([trade], {0: cut}, stop=None, target=None)[0]
    if resolved.get("outcome") != "no_hit":
        return resolved

    is_long = bool(trade["is_long"])
    sign = 1.0 if is_long else -1.0
    entry_adj = float(trade["entry"])
    stop_pts = float(trade["stop_dist"])
    offset, sym = R._offset_for_ts(pd.Timestamp(trade["retest_time"], tz="UTC"))
    fill_ts, fill_raw = SR._market_fill(sym, eod_ts, is_long)
    if fill_raw is None:
        return resolved                       # no tape left at all -- keep the no_hit
    exit_price = fill_raw + offset
    favorable_pts, adverse_pts, entry_traded = SR._compute_excursion(
        cut, touch_time, fill_ts, entry_adj - offset, is_long, sym, offset)
    return {"outcome": "eod_flat", "r": sign * (exit_price - entry_adj) / stop_pts,
            "exit_time": fill_ts, "exit_price": exit_price, "touch_time": touch_time,
            "favorable_pts": favorable_pts, "adverse_pts": adverse_pts,
            "giveback_pts": SR._compute_giveback(touch_time, fill_ts, is_long),
            "entry_gapped": entry_traded is False}


# --------------------------------------------------------------------------
# Rule 1: thrust-P1 stop trail
# --------------------------------------------------------------------------

def _norm_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts


def _p0_wick_ratio(m5_bars, formation_time, level_type):
    """The P0's own level-side wick (upper wick for LHPB) as a fraction of
    its own candle's total range. None if the formation bar isn't in
    `m5_bars` or has zero range."""
    if m5_bars is None:
        return None
    ts = _norm_utc(formation_time)
    if ts not in m5_bars.index:
        return None
    bar = m5_bars.loc[ts]
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    total = h - l
    if total <= 0:
        return None
    wick = (h - max(o, c)) if level_type == "LHPB" else (min(o, c) - l)
    return wick / total


def _group_threshold(group, m5_bars, level_type):
    """Tiered minimum sibling-P0 count for one P1 group to qualify as a
    thrust trigger: 1 if any member is a local swing or a spike candle, 2
    if EVERY member's own wick is "slight" (< SLIGHT_WICK_RATIO of its own
    range), 3 otherwise (a member whose wick can't be measured -- missing
    bar data -- makes the group ineligible for the 2-tier, same as a
    non-slight wick would)."""
    if bool(group["is_swing"].fillna(False).any() or group["is_spike"].fillna(False).any()):
        return 1
    ratios = [_p0_wick_ratio(m5_bars, ft, level_type) for ft in group["formation_time"]]
    if ratios and all(r is not None and r < SLIGHT_WICK_RATIO for r in ratios):
        return 2
    return 3


def thrust_trail_events(level_type, start_time, end_time, ledger=None, m5_bars=None):
    """Every qualifying thrust-P1 stop-trail event in (start_time, end_time],
    as a chronological list of `(trigger_time, new_stop_price)` --
    `trigger_time` is the breakout bar's own CLOSE (formation_time + one M5
    bar), not its open, since the group isn't confirmed until the bar
    actually closes, and the window is applied to that close, so a breakout
    candle already in progress at `start_time` still counts;
    `new_stop_price` is in adjusted/display scale -- one tick beyond the
    breakout bar's own low (LHPB/long) or high (LLPB/short), same scale as
    the ledger's own `breakout_low`/`breakout_high`.
    No body-size ("thrust") requirement on the breakout candle itself --
    only `_group_threshold`'s own sibling-count/quality tiers gate this;
    any candle that closes past enough still-live P0s qualifies regardless
    of how large its own body is."""
    if level_type not in ("LHPB", "LLPB"):
        raise ValueError(f"unknown level_type {level_type!r}")
    ledger = LC.m5_levels(verbose=False) if ledger is None else ledger
    if ledger is None or ledger.empty:
        return []
    m5_bars = LC.m5_bars_continuous() if m5_bars is None else m5_bars
    start_time, end_time = _norm_utc(start_time), _norm_utc(end_time)

    sub = ledger[ledger["type"] == level_type].copy()
    sub["breakout_time"] = pd.to_datetime(sub["breakout_time"], utc=True)
    # Window on the event's own TRIGGER (the breakout bar's close), not on
    # the bar's open: a breakout candle that was still in progress at entry
    # closes AFTER it, and that close is a perfectly good trail. Filtering
    # on `breakout_time` would silently drop every event in the first M5
    # bar of the trade.
    sub["trigger_time"] = sub["breakout_time"] + pd.Timedelta(minutes=5)
    sub = sub[(sub["trigger_time"] > start_time) & (sub["trigger_time"] <= end_time)]
    if sub.empty:
        return []

    events = []
    for breakout_time, group in sub.groupby("breakout_time"):
        if len(group) < _group_threshold(group, m5_bars, level_type):
            continue
        row0 = group.iloc[0]
        new_stop = (float(row0["breakout_low"]) - TICK_SIZE if level_type == "LHPB"
                   else float(row0["breakout_high"]) + TICK_SIZE)
        trigger_time = breakout_time + pd.Timedelta(minutes=5)
        events.append((trigger_time, new_stop))
    events.sort(key=lambda e: e[0])
    return events


# --------------------------------------------------------------------------
# Combined sequential resolver (rule 1 + rule 2)
# --------------------------------------------------------------------------

def resolve_managed_trade(trade, bars, level_type, ledger=None, m5_bars=None, eod=True):
    """Sequential twin of `SR.resolve_trades`' single-trade path (same
    tick-accurate stop/target pinning) that ALSO runs the active management
    rules above -- symmetric across direction via a single `sign` (+1 long,
    -1 short) that both rules key off. Returns the same shape as one
    `SR.resolve_trades(...)` result dict, plus `trail_events` (fired rule-1
    events) and `rr_floor_fired` (bool).

    Unlike the baseline's fixed `-1.0` for a stop-out, R here is ALWAYS
    variable (`(exit_price - entry) / original_stop_dist`, signed) for
    every non-target outcome: a trailed stop or an RR-floor exit can close
    at breakeven or better, and a fixed -1R would hide that."""
    is_long = bool(trade["is_long"])
    sign = 1.0 if is_long else -1.0
    entry_adj = float(trade["entry"])
    stop_pts0 = float(trade["stop_dist"])
    target_pts = float(trade["target_dist"])
    if bars is None or bars.empty:
        return {"outcome": "no_data", "r": None, "exit_time": None, "touch_time": None,
                "trail_events": [], "rr_floor_fired": False, "eod_flat_fired": False}

    touch_time = bars.attrs.get("touch_time")
    offset, sym = R._offset_for_ts(pd.Timestamp(trade["retest_time"], tz="UTC"))
    raw_entry = entry_adj - offset
    highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
    target_price_raw = raw_entry + sign * target_pts
    target_price_adj = entry_adj + sign * target_pts

    m5_bars = LC.m5_bars_continuous() if m5_bars is None else m5_bars
    trail_events = thrust_trail_events(level_type, touch_time, bars.index[-1],
                                       ledger=ledger, m5_bars=m5_bars)

    eod_ts = (eod_exit_signal(touch_time if touch_time is not None else bars.index[0])
              if eod else None)

    fired_trail = []
    # The live stop is carried as a PRICE in adjusted scale, the same scale
    # the trail events themselves arrive in. Holding it as a signed distance
    # instead makes "tighter" mean "smaller number", which flips sign the
    # moment the stop crosses entry into profit and invites a guard that
    # rejects exactly the trails worth taking.
    current_stop_price_adj = entry_adj - sign * stop_pts0
    ev_cursor = 0
    n_bars = len(bars)
    outcome = exact_time = exact_price = None
    rr_floor_fired = False

    for b_idx in range(n_bars):
        bar_time = bars.index[b_idx]

        # Rule 3 (end-of-day flat) is checked BEFORE this bar's own stop/
        # target: the flattening order goes out at eod_ts exactly, which is
        # a minute boundary, so anything this bar's range would have hit
        # happens strictly after the order was already sent.
        if eod_ts is not None and bar_time >= eod_ts:
            fill_ts, fill_raw = SR._market_fill(sym, eod_ts, is_long)
            if fill_raw is not None:
                outcome, exact_time, exact_price = "eod_flat", fill_ts, fill_raw + offset
                break

        while ev_cursor < len(trail_events) and trail_events[ev_cursor][0] <= bar_time:
            _, new_stop_price_adj = trail_events[ev_cursor]
            # Monotonic: the stop only ever moves TOWARDS the target -- up
            # for a long, down for a short. That is the whole test. Whether
            # the new stop still sits on the losing side of entry is not a
            # separate question: crossing entry into profit is just another
            # step in the same direction, and an event pointing the other
            # way (or landing on the same price) is simply not applied.
            if sign * (new_stop_price_adj - current_stop_price_adj) > 0:
                current_stop_price_adj = new_stop_price_adj
                fired_trail.append(trail_events[ev_cursor])
            ev_cursor += 1

        stop_price_raw = current_stop_price_adj - offset
        if is_long:
            stop_hit, target_hit = lows[b_idx] <= stop_price_raw, highs[b_idx] >= target_price_raw
        else:
            stop_hit, target_hit = highs[b_idx] >= stop_price_raw, lows[b_idx] <= target_price_raw
        if stop_hit or target_hit:
            o, et, ep = M._pin_exact_exit(
                sym, bar_time, offset, entry_adj,
                sign * (entry_adj - current_stop_price_adj), target_pts, is_long,
                not_before=touch_time)
            if o is not None:
                outcome, exact_time, exact_price = o, et, ep
                break
            continue  # no qualifying fill this minute -- keep scanning forward

        # Rule 2: RR floor, evaluated once per M5 candle close (bar_time
        # lands exactly on a 5-minute boundary) -- and only once stop/target
        # have already been ruled out for this same bar.
        if m5_bars is not None and bar_time.minute % 5 == 0 and bar_time > touch_time:
            m5_close_time = bar_time - pd.Timedelta(minutes=5)
            if m5_close_time in m5_bars.index:
                close_adj = float(m5_bars.loc[m5_close_time, "close"])
                remaining_reward = sign * (target_price_adj - close_adj)
                remaining_risk = sign * (close_adj - current_stop_price_adj)
                if remaining_risk > 0 and remaining_reward / remaining_risk <= RR_FLOOR_MIN_RR:
                    fill_ts, fill_raw = SR._market_fill(sym, bar_time, is_long)
                    if fill_raw is not None:
                        outcome, exact_time, exact_price = "rr_floor", fill_ts, fill_raw + offset
                        rr_floor_fired = True
                        break

    if outcome is None:
        last_time = bars.index[-1]
        return {"outcome": "no_hit", "r": None, "exit_time": last_time, "exit_price": entry_adj,
                "touch_time": touch_time, "trail_events": fired_trail, "rr_floor_fired": False,
                "eod_flat_fired": False}

    if outcome == "target":
        r = target_pts / stop_pts0
    else:  # "stop", "rr_floor" or "eod_flat" -- real, variable, signed R
        r = sign * (exact_price - entry_adj) / stop_pts0

    favorable_pts, adverse_pts, entry_traded = SR._compute_excursion(
        bars, touch_time, exact_time, raw_entry, is_long, sym, offset)
    giveback_pts = SR._compute_giveback(touch_time, exact_time, is_long)
    return {"outcome": outcome, "r": r, "exit_time": exact_time, "exit_price": exact_price,
            "touch_time": touch_time, "favorable_pts": favorable_pts, "adverse_pts": adverse_pts,
            "giveback_pts": giveback_pts, "entry_gapped": entry_traded is False,
            "trail_events": fired_trail, "rr_floor_fired": rr_floor_fired,
            "eod_flat_fired": outcome == "eod_flat"}
