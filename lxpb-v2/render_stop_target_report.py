"""
Per-trade H1 chart report for a single stop/target combination, styled like
lxpb_labels_report.html (same dark theme, expandable-row table, H1
candlestick chart with lightweight-charts) but showing trade OUTCOME
instead of hand-labeling checkboxes.

Uses the exact same 99-trade "strong breakout" sample and tick-accurate
exit logic as analyze_breakout_exits_1min.py / exit_analysis_report.html:
entries are anchored to the real 1s-tick touch instant within the retest H1
bar (not the bar's own start-of-hour timestamp -- an earlier version had a
look-ahead bug where price action before the level was ever actually
retested could get counted as a stop/target hit), and every exit is pinned
to the exact second (and exact crossing price) by escalating to real 1s
ticks for the resolving minute -- so a trade's outcome/exit time here always
matches its row in the "1-MINUTE-RESOLVED" grid in exit_analysis_report.html
for the same stop/target, down to the second.

Each row's H1 chart spans the same formation/breakout/retest context
windows as render_labels_report.py's build_row_chart, extended forward to
also include the exit bar, with:
  - gold line  = the LXPB level itself (entry price)
  - green line = target price
  - red line   = stop price
  - P0/P1/P2 markers (formation/breakout/retest, i.e. entry) exactly as in
    the labels report, plus an EXIT marker (green up-arrow = target hit /
    win, red down-arrow = stop hit / loss) at the resolved exit bar.

`--candle-exit` adds a third way out on top of that bracket: after the
retest, the FIRST 1-minute candle that closes below the previous candle's
LOW *and* below the entry level closes a long at market (mirrored for a
short: closes above the previous candle's HIGH and above the entry level).
Stop and target still win whenever they are actually hit earlier in the
tick stream, since they resolve inside a minute and this rule can only fire
at a minute's close. See resolve_trades / _candle_exit_signals.

`--candle-exit-skip-entry` narrows that rule so the entry minute's own
candle cannot fire it -- the earliest signal is the first full candle after
entry. Worth having as a switch because the entry candle is the very candle
that pushed into the level, so the mirrored condition is disproportionately
already true on it (44% of all rule exits over full-year 2026).

Usage:
    python render_stop_target_report.py --stop 2 --target 8
    python render_stop_target_report.py --stop 2 --target 8 --output my_report.html
    python render_stop_target_report.py --stop 10 --target 10 --candle-exit --full-year
    python render_stop_target_report.py --stop 10 --target 10 --candle-exit \
        --candle-exit-skip-entry --full-year
"""
import os
import sys
import json
import time
import pickle
import hashlib
import argparse
import multiprocessing as mp
import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_labels_report as R  # noqa: E402
import analyze_breakout_exits as A  # noqa: E402
import analyze_breakout_exits_1min as M  # noqa: E402
import lxpb_levels_cache as LC  # noqa: E402

DEFAULT_STOP = 2.0
DEFAULT_TARGET = 8.0

BARS_BEFORE = 8
CONTEXT_BARS_AFTER_FORMATION = 3
CONTEXT_BARS_BEFORE_BREAKOUT = 3
CONTEXT_BARS_AFTER_BREAKOUT = 6
CONTEXT_BARS_BEFORE_RETEST = 6
BARS_AFTER_MIN = 8       # same floor as render_labels_report's BARS_AFTER
BARS_AFTER_EXIT = 3      # extra bars of context shown past the exit bar
MAX_MERGE_GAP = 15

EXIT_WIN_COLOR = "#4ade80"
EXIT_LOSS_COLOR = "#f87171"
EXIT_CANDLE_COLOR = "#fbbf24"

# "Confluence" strategy (see lxpb_levels_cache.find_confluent_levels): other
# LXPB levels within +/- CONFLUENCE_N_POINTS of the trade's own level, formed
# before it, with a confirmed (non-failed) breakout of their own, and formed
# no further back than CONFLUENCE_LOOKBACK_BARS H1 bars before the trade's
# own P1 (breakout) bar -- an unbounded lookback let a handful of very old
# levels (formed months earlier) count as "confluence" alongside genuinely
# recent structure. CONFLUENCE_COLOR is a blue distinct from both P2_COLOR
# (light purple circle marker) and M5_COLOR (the M5 pane's own rays) so the
# c1/c2/... markers/rays are never confused with either on the H1 chart.
CONFLUENCE_N_POINTS = 2.5
CONFLUENCE_LOOKBACK_BARS = 100
CONFLUENCE_COLOR = "#3b82f6"

# How far past a candle-rule signal to look for the market order's fill.
# The first window is the minute right after the signal (which is where the
# fill essentially always lands); the rest only matter across a session
# break or a weekend, when the order simply rests until the tape reopens.
MARKET_FILL_SEARCH_MINUTES = (1, 15, 240, 4320)


def _fmt_pts(v):
    """A point distance for display and for identifiers (filenames,
    localStorage keys). Whole values stay whole ("10"), fractional ones keep
    their decimals ("3.5").

    The old "{:.0f}" rendered BOTH 3.5 and 4.5 as "4" -- two different
    brackets would have collided onto one filename and, worse, onto one
    localStorage key, so the second report would silently inherit and
    overwrite the first's saved review notes."""
    v = float(v)
    return f"{v:.0f}" if v.is_integer() else f"{v:g}"


EXCURSION_PCTILES = (5, 10, 25, 50, 75, 90, 95, 99)

# Kept apart from CSS so a report rendered before this table existed can be
# retro-fitted with exactly the rules a fresh render would have emitted.
EXCURSION_CSS = """.pctile-wrap { margin:14px 0 18px; }
.pctile-wrap h2 { font-size:1.05em; margin:0 0 6px; color:var(--text); }
.pctile-tbl { border-collapse:collapse; font-size:0.92em; }
.pctile-tbl th, .pctile-tbl td { border:1px solid var(--border); padding:3px 9px;
                                 text-align:right; white-space:nowrap; }
.pctile-tbl th { background:var(--surface2); }
.pctile-tbl td.left, .pctile-tbl th.left { text-align:left; }
.pctile-tbl tr.pctile-r td { color:#94a3b8; border-top:none; font-style:italic; }
.pctile-note { color:#94a3b8; font-style:italic; margin-left:8px; font-size:0.9em; }
.pctile-cap { max-width:1100px; color:#94a3b8; font-size:0.88em; line-height:1.5;
              margin:8px 0 0; }"""


def excursion_percentile_html(groups, stop):
    """Percentile tables for the MAE/MFE excursion columns.

    `groups` is an ordered list of (label, note, values_in_points). Kept as a
    plain function of already-extracted numbers -- rather than reading
    `resolved_list` directly -- so the exact same markup can be produced from
    a report that has already been rendered, without re-resolving 500 trades
    off the tick data just to add a summary table.

    Every population is also shown in R (points / stop), since that is the
    unit the rest of the report reasons in."""
    if not any(vals for _, _, vals in groups):
        return ""
    head = "".join(f"<th>p{q}</th>" for q in EXCURSION_PCTILES)
    body = []
    for label, note, vals in groups:
        if not vals:
            continue
        a = np.asarray(vals, dtype=float)
        pcts = [float(np.percentile(a, q)) for q in EXCURSION_PCTILES]
        body.append(
            f'<tr><td class="left">{label}<span class="pctile-note">{note}</span></td>'
            f'<td>{a.size}</td>'
            + "".join(f"<td>{v:.2f}</td>" for v in pcts)
            + f'<td>{a.mean():.2f}</td><td>{a.max():.2f}</td></tr>'
            + f'<tr class="pctile-r"><td class="left">&nbsp;&nbsp;same, in R</td><td></td>'
            + "".join(f"<td>{v / stop:.2f}</td>" for v in pcts)
            + f'<td>{a.mean() / stop:.2f}</td><td>{a.max() / stop:.2f}</td></tr>')
    return f"""
<div class="pctile-wrap">
<h2>Excursion percentiles</h2>
<table class="pctile-tbl">
<thead><tr><th class="left">Population</th><th>n</th>{head}<th>mean</th><th>max</th></tr></thead>
<tbody>{"".join(body)}</tbody>
</table>
<p class="pctile-cap">Read a percentile as a share of <em>that population</em>, not as a win rate.
p75 = 5.75pt means 75% of losing trades ran LESS than 5.75pt in the position's favour before
stopping out, so only the top 25% of them would have been rescued by a 5.75pt target &mdash; the
win rate at that target is those 25% of losers plus every trade that already won, not 75%.
The excursion is also a quote touch, not a fill: it is the extreme of the raw tick High/Low
(= ask/bid, see _compute_excursion), whereas a target is a resting limit order that needs an
opposite-side print, so a target placed at a given MFE level converts slightly fewer trades than
the percentile implies. Both effects are why these numbers sit just above the corresponding row
of exit_analysis_report's stop/target grid rather than reproducing it exactly.</p>
</div>
"""


def _full_minute_ohlc(sym, minute_start):
    """Real, FULL clock-minute OHLC for one minute, built from 1s ticks with
    the same aggregation M.build_or_load_1min_series uses, so it is directly
    comparable to (and interchangeable with) that series' own bars.

    Needed because the candle rule has to see the candles a trader would see
    on a 1-minute chart, and the trade-anchored series starts at the trade's
    tick-accurate touch_time: its FIRST bar is a PARTIAL minute whose low/high
    only cover the part of the minute after entry. That truncation would
    change the rule's answer (the rule tests the previous candle's low/high),
    so the entry minute -- and the minute before it, which the entry candle is
    compared against -- are rebuilt here from the whole clock minute.

    Reuses M._1S_AMBIGUOUS_CACHE (keyed by (sym, minute_start)) so this shares
    tick fetches with _pin_exact_exit / _compute_excursion. Returns None when
    the minute has no tick coverage at all."""
    cache_key = (sym, minute_start)
    if cache_key not in M._1S_AMBIGUOUS_CACHE:
        t = R._ticks_for_window(minute_start, minute_start + pd.Timedelta(minutes=1))
        M._1S_AMBIGUOUS_CACHE[cache_key] = None if t is None or t.empty else t
    ticks = M._1S_AMBIGUOUS_CACHE[cache_key]
    if ticks is None:
        return None
    return {"high": float(ticks["High"].max()), "low": float(ticks["Low"].min()),
            "close": float(ticks["Close"].iloc[-1])}


def _candle_exit_signals(bars, sym, raw_entry, is_long, skip_entry_bar=False):
    """Indices into `bars` of every 1-minute candle whose CLOSE fires the
    candle exit rule: for a LONG, a candle that closes below the previous
    candle's LOW *and* below the entry level; for a SHORT, one that closes
    above the previous candle's HIGH *and* above the entry level.

    Both conditions are on the candle's own close, so the signal is only
    known at the END of that minute -- which is what makes stop/target take
    precedence within the same minute (they resolve on ticks inside it).

    `bars` index 0 is the trade's entry minute, and by default it IS
    eligible: it closes after the retest, so a trader watching a 1-minute
    chart would act on it. Its comparison candle is therefore the clock
    minute BEFORE entry, and index 1's is the FULL entry minute rather than
    bars[0]'s post-entry fragment -- both fetched via _full_minute_ohlc.
    Only these first two references need that treatment; every later bar's
    predecessor is already a whole clock minute. If either minute has no
    tick coverage its bar is simply made unsignallable (+/-inf reference)
    rather than silently compared against a truncated candle.

    `skip_entry_bar` drops index 0 from the result, i.e. only candles that
    OPEN after the entry minute can fire. That matters because the entry
    candle is the very candle that pushed into the level, so the mirrored
    condition is disproportionately already true on it (44% of all rule
    exits over full-year 2026 fired on bar 0). Index 1 is unaffected: its
    reference is still the full entry minute, which is exactly right."""
    closes = bars["close"].to_numpy(float)
    n = len(closes)
    if n == 0:
        return np.empty(0, dtype=int)
    # prev_ref[k] = candle k-1's low (long) / high (short)
    prev_ref = np.full(n, -np.inf if is_long else np.inf)
    if n > 2:
        edge = bars["low"].to_numpy(float) if is_long else bars["high"].to_numpy(float)
        prev_ref[2:] = edge[1:-1]
    first_minute = bars.index[0]
    for k, minute in ((0, first_minute - pd.Timedelta(minutes=1)), (1, first_minute)):
        if k >= n:
            break
        ohlc = _full_minute_ohlc(sym, minute)
        if ohlc is not None:
            prev_ref[k] = ohlc["low"] if is_long else ohlc["high"]
    if is_long:
        sig = (closes < prev_ref) & (closes < raw_entry)
    else:
        sig = (closes > prev_ref) & (closes > raw_entry)
    if skip_entry_bar:
        sig[0] = False
    return np.flatnonzero(sig)


def _market_fill(sym, signal_ts, is_long):
    """Fill for the MARKET order the candle rule sends at `signal_ts` (the
    signalling candle's close instant).

    Sierra's raw .scid tick records carry the live quote alongside the trade:
    `Low` is the best BID and `High` the best ASK at that record. (Verified on
    2026 EPU26 data: High-Low is exactly one tick on 99.3% of records, an
    ask-side print's Close equals High 97.6% of the time and a bid-side
    print's Close equals Low 96.5%.) A market SELL -- a long's exit -- is
    therefore filled at the first record's `Low`, and a market BUY -- a
    short's exit -- at its `High`: literally "the best bid/ask at that point",
    paying the spread, rather than booking the candle's close price.

    Returns (exit_time, raw_price), or (None, None) if the tape has no record
    at all within ~3 days (only reachable at the very end of the data).
    Prices are in raw/uncontinuous per-contract terms, like `bars`."""
    for span in MARKET_FILL_SEARCH_MINUTES:
        ticks = R._ticks_for_window(signal_ts, signal_ts + pd.Timedelta(minutes=span))
        if ticks is not None and not ticks.empty:
            r = ticks.iloc[0]
            return ticks.index[0], float(r["Low"] if is_long else r["High"])
    return None, None


def resolve_trades(trades, series_by_idx, stop=None, target=None, candle_exit=False,
                   candle_exit_skip_entry=False):
    """Per-trade version of analyze_breakout_exits_1min.stop_target_grid_1min,
    with the same tick-accurate anchoring fix (see that module's docstring):
    series_by_idx's bars are already anchored to each trade's real touch_time
    (not the retest H1 bar's own start-of-hour timestamp, AND -- since
    M.build_or_load_1min_series/M._find_trade_touch_time -- the touch_time
    itself only counts a fill-eligible aggressor-side print: a LONG entry
    is a resting BUY, filled only by a bid-side/seller-initiated print).

    The resolving minute (whichever minute first satisfies either the stop
    or target condition, tie or not) is escalated to real 1s ticks via
    M._pin_exact_exit so every returned exit_time is accurate to the exact
    second (and exact crossing price), not just to the minute. TARGET is
    also a resting LIMIT order, so M._pin_exact_exit only counts a
    qualifying opposite-side print there too (STOP, a stop/market order
    once triggered, fills on any side). If a candidate minute's naive
    OHLC touch turns out not to contain any qualifying fill (e.g. only the
    "wrong" side traded at the target price that minute), this keeps
    scanning forward to the next candidate minute rather than crediting a
    fill that couldn't really have happened. Returns full per-trade detail
    (outcome, R, exit time/price, touch_time) instead of only aggregate
    grid stats.

    `stop` / `target` are point distances from entry. Pass scalars for a
    fixed bracket (the grid-search use case); leave either as None to take
    that leg per-trade from `t["stop_dist"]` / `t["target_dist"]` -- what a
    structural, level-derived bracket needs (e.g.
    analyze_spike_atr_strategy.py: stop = beyond the breakout candle,
    target = the level's own FTA), where every trade has its own size.

    `candle_exit` adds the candle rule (see _candle_exit_signals) as a third
    exit: whichever of stop / target / candle-rule comes FIRST in real time
    ends the trade. Because the rule can only be known at a candle's close,
    a stop or target actually filled anywhere inside that same minute wins;
    the rule only takes that minute if the bracket's own escalation found no
    qualifying fill there. Outcome "candle" carries a real, variable R (the
    market fill vs entry, over the stop distance) instead of the bracket's
    fixed +target/stop or -1.

    `candle_exit_skip_entry` makes the rule ignore the entry minute itself,
    so the earliest it can fire is the first full candle after entry."""
    out = []
    for i, t in enumerate(trades):
        stop_pts = float(t["stop_dist"]) if stop is None else float(stop)
        target_pts = float(t["target_dist"]) if target is None else float(target)
        bars = series_by_idx.get(i)
        entry_adj = t["entry"]
        is_long = t["is_long"]
        if bars is None or bars.empty:
            out.append({"outcome": "no_data", "r": None, "exit_time": None, "touch_time": None})
            continue
        touch_time = bars.attrs.get("touch_time")
        offset, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
        raw_entry = entry_adj - offset
        highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
        if is_long:
            stop_price, target_price = raw_entry - stop_pts, raw_entry + target_pts
            stop_hit = lows <= stop_price
            target_hit = highs >= target_price
        else:
            stop_price, target_price = raw_entry + stop_pts, raw_entry - target_pts
            stop_hit = highs >= stop_price
            target_hit = lows <= target_price
        s_idx = np.flatnonzero(stop_hit)
        tg_idx = np.flatnonzero(target_hit)
        sig_idx = (_candle_exit_signals(bars, sym, raw_entry, is_long,
                                        skip_entry_bar=candle_exit_skip_entry)
                   if candle_exit else np.empty(0, dtype=int))

        # TARGET is a resting LIMIT order (opposite-side fill required --
        # see M._pin_exact_exit's docstring), so a minute whose naive
        # OHLC range merely brushes the target price may not actually
        # contain a qualifying fill (e.g. only the "wrong"-side aggressor
        # traded there). STOP is a stop/market order once triggered and
        # fills on any side, so it never needs this re-scan. Keep advancing
        # to the next candidate minute (whichever of stop/target comes
        # first from there) until M._pin_exact_exit actually finds a
        # qualifying fill, or we run out of bars (-> no_hit).
        #
        # `b_idx` and `sig_cur` are SEPARATE cursors, both monotonically
        # increasing (so this always terminates). They have to be separate
        # for the one case where a bracket candidate and a candle signal
        # land on the SAME minute: the bracket is tried first (its ticks are
        # inside the minute, the signal is at its close), and if that
        # escalation finds no qualifying fill, only the bracket cursor moves
        # past the minute -- the signal at that same minute is still the
        # earliest remaining exit and fires on the next pass.
        n_bars_series = len(bars)
        b_idx = sig_cur = 0
        outcome = exact_time = exact_price = None
        while b_idx < n_bars_series or sig_cur < n_bars_series:
            s_rem = s_idx[s_idx >= b_idx]
            tg_rem = tg_idx[tg_idx >= b_idx]
            hs = s_rem[0] if s_rem.size else None
            ht = tg_rem[0] if tg_rem.size else None
            sig_rem = sig_idx[sig_idx >= sig_cur]
            sg = sig_rem[0] if sig_rem.size else None
            cand = min((x for x in (hs, ht) if x is not None), default=None)
            if cand is None and sg is None:
                break
            if cand is not None and (sg is None or cand <= sg):
                minute_start = bars.index[cand]
                o, et, ep = M._pin_exact_exit(
                    sym, minute_start, offset, entry_adj, stop_pts, target_pts, is_long,
                    not_before=touch_time)
                if o is not None:
                    outcome, exact_time, exact_price = o, et, ep
                    break
                b_idx = cand + 1
                continue
            # Candle rule: the order is sent the instant that candle closes,
            # i.e. at the start of the next minute, and takes the prevailing
            # bid/ask (see _market_fill).
            fill_ts, fill_raw = _market_fill(
                sym, bars.index[sg] + pd.Timedelta(minutes=1), is_long)
            if fill_ts is None:
                sig_cur = sg + 1
                continue
            outcome, exact_time, exact_price = "candle", fill_ts, fill_raw + offset
            break

        if outcome is None:
            last_time = bars.index[-1]
            out.append({
                "outcome": "no_hit", "r": None, "exit_time": last_time,
                "exit_price": entry_adj, "touch_time": touch_time,
            })
            continue

        if outcome == "candle":
            # Real, variable R: what the market fill actually gave up (or
            # kept) against entry, expressed in stop units. Deliberately NOT
            # clamped to >= -1: the fill is a market order, and so is the
            # stop once triggered, so if the tape jumped past the stop in
            # the instant between the candle's close and the fill, both
            # orders would have suffered the same slippage.
            gain = (exact_price - entry_adj) if is_long else (entry_adj - exact_price)
            r = gain / stop_pts
        else:
            r = target_pts / stop_pts if outcome == "target" else -1.0
        favorable_pts, adverse_pts, entry_traded = _compute_excursion(
            bars, touch_time, exact_time, raw_entry, is_long, sym, offset)
        giveback_pts = _compute_giveback(touch_time, exact_time, is_long)
        out.append({"outcome": outcome, "r": r, "exit_time": exact_time,
                    "exit_price": exact_price, "touch_time": touch_time,
                    "favorable_pts": favorable_pts, "adverse_pts": adverse_pts,
                    "giveback_pts": giveback_pts,
                    "entry_gapped": entry_traded is False})
    return out


# A give-back smaller than this is spread/queue noise, not a real pullback,
# so it is reported as 0.00 rather than a spurious one-tick wiggle.
MIN_GIVEBACK_PTS = 0.75


def _compute_giveback(touch_time, exit_time, is_long):
    """Max points handed back from the best price the OPEN position reached,
    over [touch_time, exit_time], on real 1s ticks.

    Unlike MAE/MFE this is PATH-DEPENDENT -- it needs to know the peak came
    before the trough -- so 1-minute OHLC cannot be used for the middle of
    the trade the way _compute_excursion does; the whole window is scanned
    tick by tick.

    High/low watermark, on the same two sides the report's own MFE/MAE use
    (a long's favourable side is the ask/High, its adverse side the
    bid/Low): a long's watermark is the running max of High and it is
    marked down to Low, mirrored for a short. That keeps this column
    directly comparable to the MAE/MFE columns, at the cost of including
    the bid/ask spread -- it runs ~1 tick wider than the give-back you
    could actually have liquidated at, which is the number
    analyze_trail_stop.py uses, since a trail has to fill on one side.
    MIN_GIVEBACK_PTS then doubles as a floor that suppresses the pure-spread
    wiggle a flat market would otherwise report.

    Returned in points, scale-free (a difference of two raw prices, so the
    back-adjustment offset cancels). None when no ticks cover the window."""
    ticks = R._ticks_for_window(touch_time, exit_time + pd.Timedelta(seconds=1))
    if ticks is None or ticks.empty:
        return None
    ticks = ticks.loc[(ticks.index >= touch_time) & (ticks.index <= exit_time)]
    if ticks.empty:
        return None
    hi = ticks["High"].to_numpy(float)
    lo = ticks["Low"].to_numpy(float)
    if is_long:
        dd = np.maximum.accumulate(hi) - lo
    else:
        dd = hi - np.minimum.accumulate(lo)
    best = float(dd.max())
    return best if best >= MIN_GIVEBACK_PTS - 1e-9 else 0.0


def _compute_excursion(bars, touch_time, exit_time, raw_entry, is_long, sym, offset):
    """Max favorable / max adverse move (in points, raw/uncontinuous
    scale) between touch_time and exit_time inclusive, at real-1s-tick
    precision throughout -- not just 1-minute OHLC precision.

    A trade starts and ends mid-minute, so the minute containing
    `touch_time` and the minute containing `exit_time` are each only
    PARTLY inside the trade. Both are rescanned at 1s resolution and
    clipped to [touch_time, exit_time]; only the whole minutes strictly
    between them are read from `bars`, whose high/low columns are
    themselves a resample of the same real 1s ticks (see
    build_or_load_1min_series) and are therefore already exact.

    Clipping matters in both directions. Past `exit_time` it is the
    look-ahead bug class fixed by `_pin_exact_exit`'s `not_before` (see
    that function's docstring): an excursion that only happened after the
    position was already closed must not be credited to this trade.
    Before `touch_time` it is the mirror image: the entry minute's bar
    opens before the level was even touched, so its high/low can contain
    a move the trade never actually sat through.

    Do NOT go back to selecting whole bars by `touch_time <= bar_start <=
    exit_time`. That silently dropped the entry minute (its start is
    always < touch_time), losing the excursion between touch_time and the
    next minute boundary -- typically the most volatile part of the
    trade. Worse, a trade that opened and closed inside ONE minute
    selected no bars at all and reported no excursion ("-"), which is
    exactly what a fast stop-out looks like.

    Reuses analyze_breakout_exits_1min._1S_AMBIGUOUS_CACHE (module-level,
    keyed by (sym, minute_start)) so this doesn't refetch ticks
    resolve_trades's own _pin_exact_exit call already fetched for that
    same resolving minute."""
    first_minute = touch_time.floor("min")
    last_minute = exit_time.floor("min")

    mid = bars.loc[(bars.index > first_minute) & (bars.index < last_minute)]
    hi = float(mid["high"].max()) if not mid.empty else -np.inf
    lo = float(mid["low"].min()) if not mid.empty else np.inf

    # sorted({...}): the two edge minutes collapse to one when the trade
    # opens and closes inside a single minute.
    for minute in sorted({first_minute, last_minute}):
        cache_key = (sym, minute)
        if cache_key not in M._1S_AMBIGUOUS_CACHE:
            t = R._ticks_for_window(minute, minute + pd.Timedelta(minutes=1))
            M._1S_AMBIGUOUS_CACHE[cache_key] = None if t is None or t.empty else t
        ticks = M._1S_AMBIGUOUS_CACHE[cache_key]
        seg = None
        if ticks is not None:
            seg = ticks.loc[(ticks.index >= touch_time) & (ticks.index <= exit_time)]
        if seg is not None and not seg.empty:
            hi = max(hi, float(seg["High"].max()))
            lo = min(lo, float(seg["Low"].min()))
        elif minute in bars.index:
            # No usable ticks for this edge minute -- fall back to its own
            # 1-minute bar. Overstates the excursion slightly (the bar spans
            # the whole minute, including the part outside the trade), but
            # that beats dropping the minute entirely.
            row = bars.loc[minute]
            hi = max(hi, float(row["high"]))
            lo = min(lo, float(row["low"]))

    if np.isneginf(hi) or np.isinf(lo):
        return None, None, None

    # The position sits AT raw_entry the instant it opens, so raw_entry is
    # part of the price path by construction and both excursions are >= 0.
    # Seeding the range with it matters when the modeled fill was never
    # actually available: entry is the level's own price with no slippage
    # modeled, so a tick that gaps clean through the level (and sometimes
    # the stop as well, resolving the trade on the very tick that touched)
    # leaves a scanned window that never reaches raw_entry. Without this,
    # such a trade reports a NEGATIVE "max favorable excursion", which is a
    # statement about fill quality, not about how far the trade ran. For
    # every normally-filled trade raw_entry already lies inside [lo, hi],
    # so this is a no-op -- which is exactly what makes the pre-seed test
    # below a reliable "was this fill ever actually available?" flag.
    entry_traded = bool(lo <= raw_entry <= hi)
    hi, lo = max(hi, raw_entry), min(lo, raw_entry)

    if is_long:
        favorable, adverse = hi - raw_entry, raw_entry - lo
    else:
        favorable, adverse = raw_entry - lo, hi - raw_entry
    return float(favorable), float(adverse), entry_traded


def build_trade_chart(h1_df, pos_by_ts, row, trade, resolved, stop, target,
                      confluent=None):
    level_type = row["type"]
    price = float(row["price"])
    is_long = level_type == "LHPB"
    form_pos = pos_by_ts[row["formation_time"]]
    breakout_pos = pos_by_ts[row["breakout_time"]]
    retest_pos = pos_by_ts[row["retest_time"]]
    n_bars = len(h1_df)

    exit_time = resolved["exit_time"]
    if exit_time is not None:
        # h1_df's index is tz-naive (epoch-seconds -> naive UTC, see
        # lxpb.load_ohlc_data), but exit_time comes from the 1-min tick
        # series (tz-aware UTC, see render_labels_report._ticks_for_window)
        # -- strip the tz label (same instant, just re-represented) before
        # comparing against h1_df's naive index.
        exit_time_naive = exit_time.tz_localize(None) if exit_time.tzinfo is not None else exit_time
        exit_pos = min(int(h1_df.index.searchsorted(exit_time_naive, side="right")) - 1, n_bars - 1)
        exit_pos = max(exit_pos, retest_pos)
    else:
        exit_pos = retest_pos

    retest_after = max(BARS_AFTER_MIN, (exit_pos - retest_pos) + BARS_AFTER_EXIT)
    segments = [
        (form_pos - BARS_BEFORE, form_pos + CONTEXT_BARS_AFTER_FORMATION),
        (breakout_pos - CONTEXT_BARS_BEFORE_BREAKOUT, breakout_pos + CONTEXT_BARS_AFTER_BREAKOUT),
        (retest_pos - CONTEXT_BARS_BEFORE_RETEST, retest_pos + retest_after),
    ]
    merged = R._merge_segments(segments, n_bars, MAX_MERGE_GAP)

    parts, skip_markers = [], []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": R._to_epoch_utc(h1_df.index[s]),
                "position": "inBar", "color": "#9ca3af", "shape": "square",
                "text": f"[{gap} bars skipped]",
            })
        parts.append(h1_df.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)

    candles = [{
        "time": R._to_epoch_utc(t),
        "open": float(r.open), "high": float(r.high),
        "low": float(r.low), "close": float(r.close),
    } for t, r in window.iterrows()]

    # Confluence strategy (see lxpb_levels_cache.find_confluent_levels): drawn
    # exactly like the M5 pane's own LXPB rays (build_m5_chart above) rather
    # than as always-visible price lines -- a flat ray from the level's own
    # formation to its death (or the right edge of view, when it's still
    # open/unretested), clipped to bars that survived this window's
    # compression. Rays carry no drawn label (c1..cN would overlap into
    # unreadable text) -- the details show in the shared hover tooltip
    # (_renderPane already wires this up generically for any pane's
    # cd.rays). Solid = still open/unretested (live going forward), dashed =
    # already retested/consumed (resolved history). Each level ALSO gets an
    # on-timeline "c1"/"c2"/... marker when its formation bar happens to
    # fall inside this window.
    wt = window.index
    confluence_markers, confluence_rays = [], []
    n_confluent = 0 if confluent is None else len(confluent)
    if confluent is not None and not confluent.empty:
        for c_idx, lvl in enumerate(confluent.itertuples(index=False), start=1):
            label = f"c{c_idx}"
            lvl_form = lvl.formation_time
            lvl_form_naive = lvl_form.tz_localize(None) if lvl_form.tzinfo is not None else lvl_form
            if lvl_form_naive in wt:
                confluence_markers.append({
                    "time": R._to_epoch_utc(lvl_form_naive),
                    "position": "aboveBar" if lvl.type == "LHPB" else "belowBar",
                    "color": CONFLUENCE_COLOR, "shape": "circle", "text": label,
                })
            still_open = pd.isna(lvl.death_time)
            if still_open:
                end_naive = wt[-1]
            else:
                end_naive = (lvl.death_time.tz_localize(None)
                            if lvl.death_time.tzinfo is not None else lvl.death_time)
            mask = (wt >= lvl_form_naive) & (wt <= end_naive)
            pts = [{"time": R._to_epoch_utc(t), "value": float(lvl.price)} for t in wt[mask]]
            if not pts:
                continue
            confluence_rays.append({
                "points": pts, "color": CONFLUENCE_COLOR, "lineWidth": 1,
                "lineStyle": 0 if still_open else 2,
                "label": (f"{label} {lvl.type} {lvl.price:.2f}  &middot;  formed "
                          f"{R._to_pt_str(lvl.formation_time)}  &middot;  {lvl.fate}"
                          f"  &middot;  {lvl.dist:.2f}pt from entry"),
            })

    outcome = resolved["outcome"]
    win = outcome == "target"
    exit_marker_time = h1_df.index[exit_pos]
    if outcome == "target":
        exit_text = f"WIN +{target/stop:.2f}R"
        exit_color, exit_shape = EXIT_WIN_COLOR, ("arrowUp" if is_long else "arrowDown")
        exit_pos_label = "aboveBar" if is_long else "belowBar"
    elif outcome == "stop":
        exit_text = "LOSS -1.00R"
        exit_color, exit_shape = EXIT_LOSS_COLOR, ("arrowDown" if is_long else "arrowUp")
        exit_pos_label = "belowBar" if is_long else "aboveBar"
    elif outcome == "candle":
        r_val = resolved.get("r")
        exit_text = f"CANDLE {r_val:+.2f}R" if r_val is not None else "CANDLE"
        exit_color, exit_shape = EXIT_CANDLE_COLOR, ("arrowDown" if is_long else "arrowUp")
        exit_pos_label = "belowBar" if is_long else "aboveBar"
    else:
        exit_text = "NO-HIT" if outcome == "no_hit" else "NO DATA"
        exit_color, exit_shape = "#9ca3af", "circle"
        exit_pos_label = "inBar"
    markers = [
        {"time": R._to_epoch_utc(row["formation_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P0_COLOR, "shape": "circle", "text": "P0"},
        {"time": R._to_epoch_utc(row["breakout_time"]),
         "position": "belowBar" if is_long else "aboveBar",
         "color": R.P1_COLOR_UP if is_long else R.P1_COLOR_DOWN,
         "shape": "arrowUp" if is_long else "arrowDown", "text": "P1"},
        {"time": R._to_epoch_utc(row["retest_time"]),
         "position": "aboveBar" if is_long else "belowBar",
         "color": R.P2_COLOR, "shape": "circle", "text": "P2"},
        {"time": R._to_epoch_utc(exit_marker_time),
         "position": exit_pos_label, "color": exit_color, "shape": exit_shape, "text": exit_text},
    ] + skip_markers + confluence_markers
    markers.sort(key=lambda m: m["time"])

    target_price = price + target if is_long else price - target
    stop_price = price - stop if is_long else price + stop
    price_lines = [
        {"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
         "title": f"{level_type} {price:.2f} (entry)"},
        {"price": target_price, "color": EXIT_WIN_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"target {target_price:.2f} (+{_fmt_pts(target)}pt)"},
        {"price": stop_price, "color": EXIT_LOSS_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"stop {stop_price:.2f} (-{_fmt_pts(stop)}pt)"},
    ]

    title = (f"{level_type} {price:.2f}  |  formed {R._to_pt_str(row['formation_time'])}  "
             f"broke {R._to_pt_str(row['breakout_time'])}  retest {R._to_pt_str(row['retest_time'])}  "
             f"exit {R._to_pt_str(exit_marker_time)}")
    if n_confluent:
        title += f"  |  {n_confluent} confluent level(s)"
        if len(confluence_rays) != n_confluent:
            title += f" ({len(confluence_rays)} shown)"
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"

    return {"title": title, "candles": candles, "markers": markers,
            "priceLines": price_lines, "rays": confluence_rays, "precision": 2}


M5_LOOKBACK_DAYS = 3         # minimum M5 history shown before the retest
M5_BREAKOUT_WARMUP_DAYS = 1  # extra history before the H1 breakout bar, so a level that
                             # formed on that bar still has context to its left
M5_NEAR_PTS = 20.0           # "nearby" band around the H1 retest level
M5_BARS_BEFORE_RETEST = 60
M5_BARS_AFTER_EXIT = 12
M5_CTX_BEFORE_FORMATION = 4  # context bars kept around an M5 level's formation bar, so a
M5_CTX_AFTER_FORMATION = 4   # ray's START is visible even when it formed long before entry
M5_CTX_AROUND_RAY_END = 2    # ditto for the ray's END (the level's own M5 retest bar)
M5_MAX_MERGE_GAP = 12        # gaps <= this many M5 bars are kept rather than compressed
M5_COLOR = "#38bdf8"         # qualifying M5 level

# Whole-contract M5 bars and the M5 level ledger both live in lxpb_levels_cache
# so that every consumer (this report, backtests, ad-hoc analysis) shares one
# implementation and one on-disk cache. See that module's docstring.
_m5_bars = LC.m5_bars_for_contract



def build_m5_chart(row, resolved, stop, target):
    """5-minute companion pane for build_trade_chart's H1 chart.

    Runs the SAME LXPB state machine (lxpb.detect_lxpb_h1 is timeframe
    agnostic -- it just walks whatever bars it is handed) over real 1s ticks
    resampled to 5 minutes, so the M5 chart shows the M5 timeframe's own
    LXPB levels alongside the H1 level being traded. Both the bars and the
    levels come from lxpb_levels_cache, which persists the whole lifecycle of
    every level so this pane never has to replay the machine per trade.

    A qualifying M5 level must be all four of:
      * the SAME type as the H1 level (an H1 LLPB retest only cares about M5
        LLPB levels -- an opposite-type level is not confluence),
      * formed during the H1 breakout bar or earlier, so it was already on
        the chart when the H1 setup triggered rather than being discovered
        afterwards,
      * STILL LIVE at entry -- a level that had already been retested (or
        silently consumed) before this trade's entry is no longer an M5 level
        at all and is never drawn, and
      * within M5_NEAR_PTS of the H1 retest level.
    If nothing qualifies, no M5 levels are drawn at all.

    Each qualifying level is drawn as a horizontal RAY starting at its
    formation bar and running to the right edge (it is by construction still
    unretested at entry), rather than a full-width price line, so its origin
    is visible. A level can form long before the H1 setup triggers, so
    the window is assembled from merged context segments (the same treatment
    build_trade_chart gives the H1 pane): bars between a ray's start and the
    entry region are compressed out with a "[N bars skipped]" marker instead
    of squeezing the whole span into the pane. Rays carry no drawn label --
    their price/type/formation time show in a hover tooltip.

    The H1 level being retested is drawn in the background here (same gold
    line as the H1 pane) and the only structural marker is P2 -- the M5
    candle on which price actually touched that H1 level.

    Ticks are raw per-contract prices; the offset _m5_bars applies puts them
    in the same back-adjusted/continuous scale as row["price"] and the H1
    pane, so the two charts never mix raw and adjusted numbers for the same
    instant. Bars come from _m5_bars' cached whole-contract series, so the
    state machine can be walked from the H1 breakout bar forward no matter how
    long ago that was -- the window is bounded only by the contract segment."""
    retest_time = pd.Timestamp(row["retest_time"], tz="UTC")
    breakout_time = pd.Timestamp(row["breakout_time"], tz="UTC")
    exit_time = resolved.get("exit_time")
    touch_time = resolved.get("touch_time")
    hi = (exit_time if exit_time is not None else retest_time) + pd.Timedelta(hours=2)
    # Reach back far enough that the M5 machine can actually see the H1
    # breakout bar -- levels are only eligible if they formed by then, so a
    # window starting after it would report "none qualify" as an artefact.
    # There is deliberately NO fixed span cap here: an H1 level can be
    # retested long after it broke (median 0.75 days, but 15% of 2026 trades
    # exceed a week and the longest gap is 235 days), and the old 8-day cap
    # silently truncated the window to start AFTER the breakout bar, which
    # made the formation filter unsatisfiable and reported "no live M5 level"
    # for ~15% of trades no matter what the data said. The only clamp is the
    # contract segment below, which is a real data boundary rather than an
    # arbitrary cost cap -- affordable because the bars come from a cached
    # whole-contract M5 series (_m5_bars) instead of a per-trade tick load.
    lo = min(retest_time - pd.Timedelta(days=M5_LOOKBACK_DAYS),
             breakout_time - pd.Timedelta(days=M5_BREAKOUT_WARMUP_DAYS))
    # Clamp to the retest's OWN contract segment. Raw .scid prices differ by a
    # constant per contract, so a window straddling a roll would splice two
    # price scales together and a single `offset` could not correct both.
    seg_idx = R._contract_index_for(retest_time)
    seg_start, seg_end = R._segment_for(seg_idx)
    if seg_start is not None:
        lo = max(lo, seg_start)
    if seg_end is not None:
        hi = min(hi, seg_end)
    if lo >= hi:
        return None
    all_bars = _m5_bars(seg_idx)
    if all_bars is None or all_bars.empty:
        return None
    # Only chart a window that actually contains the trade. Near the end of
    # the tick data the series can cover an earlier span only, in which case
    # searchsorted would clamp the "retest" to the last bar and the pane would
    # show an unrelated window with a bogus entry marker.
    if all_bars.index[0] > retest_time or all_bars.index[-1] < retest_time:
        return None
    a = int(all_bars.index.searchsorted(lo, side="left"))
    b = int(all_bars.index.searchsorted(hi, side="left"))
    bars = all_bars.iloc[a:b]
    if bars.empty:
        return None
    # True when the H1 breakout happened in an EARLIER contract, so no bar in
    # this segment can satisfy the formation filter -- reported honestly in
    # the title rather than as a bare "no level qualified".
    breakout_out_of_reach = bars.index[0] > breakout_time + pd.Timedelta(hours=1)

    level_type = row["type"]
    price = float(row["price"])
    is_long = level_type == "LHPB"
    target_price = price + target if is_long else price - target
    stop_price = price - stop if is_long else price + stop

    # M5's own LXPB levels, taken from the cached level ledger.
    #
    # The ledger records every level's full lifecycle (formation -> breakout ->
    # retest/death), so "which M5 levels were live at entry" is an interval
    # lookup rather than a replay of the state machine. That matters for
    # correctness as much as speed: detect_lxpb_h1 only reports its buckets as
    # of the LAST bar it is handed, so running it over a window that extends
    # past the trade -- as this function used to -- reports levels with
    # hindsight, and in particular surfaces levels from the `retests` bucket
    # that were already consumed hours before the H1 setup triggered.
    #
    # `as_of` is the bar BEFORE the entry bar, so a level whose own M5 retest
    # coincides with the H1 retest still counts as live (that confluence is the
    # point) and no post-entry price action can retroactively kill a level.
    # `formed_by` enforces "formed during the H1 breakout bar or earlier" --
    # H1 bars are hourly, so that bar covers [breakout_time, +1h).
    as_of = pd.Timestamp(touch_time) if touch_time is not None else retest_time
    entry_bar_pos = int(bars.index.searchsorted(as_of, side="right")) - 1
    form_cutoff = breakout_time + pd.Timedelta(hours=1)
    near_levels = []
    if entry_bar_pos > 0:
        ledger = LC.m5_levels(seg_idx)
        if ledger is not None and not ledger.empty:
            live = LC.levels_live_as_of(
                ledger, bars.index[entry_bar_pos - 1], level_type=level_type,
                near_price=price, near_pts=M5_NEAR_PTS, formed_by=form_cutoff)
            seen = set()
            for _, lv in live.iterrows():
                # levels_live_as_of returns nearest-first, so on a price tie the
                # survivor is the closest one to the H1 level.
                key = round(float(lv["price"]), 2)
                if key in seen:
                    continue
                seen.add(key)
                near_levels.append({"price": float(lv["price"]), "type": lv["type"],
                                    "stage": lv["stage"], "end_time": None,
                                    "formation_time": pd.Timestamp(lv["formation_time"]),
                                    "dist": float(lv["dist"])})

    idx = bars.index
    n_bars = len(idx)
    retest_pos = max(0, int(idx.searchsorted(retest_time, side="right")) - 1)
    if exit_time is not None:
        exit_pos = min(int(idx.searchsorted(exit_time, side="right")) - 1, n_bars - 1)
        exit_pos = max(exit_pos, retest_pos)
    else:
        exit_pos = retest_pos

    def _pos(ts):
        return min(max(int(idx.searchsorted(pd.Timestamp(ts), side="right")) - 1, 0), n_bars - 1)

    # The entry region is always shown; each ray additionally contributes a
    # small segment at its start (and at its end, when it was retested) so a
    # level that formed hours earlier still shows both endpoints. Everything
    # between is compressed out by _merge_segments.
    segments = [(retest_pos - M5_BARS_BEFORE_RETEST, exit_pos + M5_BARS_AFTER_EXIT)]
    for lv in near_levels:
        fpos = _pos(lv["formation_time"])
        lv["form_pos"] = fpos
        segments.append((fpos - M5_CTX_BEFORE_FORMATION, fpos + M5_CTX_AFTER_FORMATION))
        if lv["end_time"] is not None:
            epos = _pos(lv["end_time"])
            lv["end_pos"] = epos
            segments.append((epos - M5_CTX_AROUND_RAY_END, epos + M5_CTX_AROUND_RAY_END))
    merged = R._merge_segments(segments, n_bars, M5_MAX_MERGE_GAP)

    parts, skip_markers = [], []
    for s, e, gap in merged:
        if gap:
            skip_markers.append({
                "time": R._to_epoch_utc(idx[s]),
                "position": "inBar", "color": "#9ca3af", "shape": "square",
                "text": f"[{gap} bars skipped]",
            })
        parts.append(bars.iloc[s:e + 1])
    window = pd.concat(parts) if len(parts) > 1 else parts[0]
    total_skipped = sum(g for _, _, g in merged)
    if window.empty:
        return None

    candles = [{"time": R._to_epoch_utc(t), "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close)} for t, r in window.iterrows()]

    entry_marker_time = touch_time if touch_time is not None else retest_time
    entry_bar = idx[max(0, int(idx.searchsorted(entry_marker_time, side="right")) - 1)]
    markers = [{"time": R._to_epoch_utc(entry_bar),
                "position": "aboveBar" if is_long else "belowBar",
                "color": R.P2_COLOR, "shape": "circle", "text": "P2"}]
    outcome = resolved["outcome"]
    if outcome == "target":
        markers.append({"time": R._to_epoch_utc(idx[exit_pos]),
                        "position": "aboveBar" if is_long else "belowBar",
                        "color": EXIT_WIN_COLOR,
                        "shape": "arrowUp" if is_long else "arrowDown",
                        "text": f"WIN +{target/stop:.2f}R"})
    elif outcome == "stop":
        markers.append({"time": R._to_epoch_utc(idx[exit_pos]),
                        "position": "belowBar" if is_long else "aboveBar",
                        "color": EXIT_LOSS_COLOR,
                        "shape": "arrowDown" if is_long else "arrowUp",
                        "text": "LOSS -1.00R"})
    elif outcome == "candle":
        r_val = resolved.get("r")
        markers.append({"time": R._to_epoch_utc(idx[exit_pos]),
                        "position": "belowBar" if is_long else "aboveBar",
                        "color": EXIT_CANDLE_COLOR,
                        "shape": "arrowDown" if is_long else "arrowUp",
                        "text": f"CANDLE {r_val:+.2f}R" if r_val is not None else "CANDLE"})
    markers.extend(skip_markers)
    markers.sort(key=lambda m: m["time"])

    # One ray per qualifying level: a flat line at the level price, plotted
    # only on window bars between formation and the level's own retest (or
    # the right edge when it was never retested). Points land exclusively on
    # bars that survived compression, so a ray follows the compressed axis.
    wt = window.index
    rays = []
    for lv in near_levels:
        end_t = lv["end_time"] if lv["end_time"] is not None else wt[-1]
        mask = (wt >= lv["formation_time"]) & (wt <= end_t)
        pts = [{"time": R._to_epoch_utc(t), "value": lv["price"]} for t in wt[mask]]
        if not pts:
            continue
        rays.append({
            "points": pts, "color": M5_COLOR, "lineWidth": 1,
            "lineStyle": 0 if lv["stage"] == "broken" else 2,
            "label": (f"M5 {lv['type']} {lv['price']:.2f}  &middot;  formed "
                      f"{R._to_pt_str(lv['formation_time'])}  &middot;  {lv['stage']}"
                      f"  &middot;  {lv['dist']:.2f}pt from H1 level"),
        })

    price_lines = [
        {"price": price, "color": R.LEVEL_COLOR, "lineWidth": 2, "lineStyle": 0,
         "title": f"H1 {level_type} {price:.2f} (entry)"},
        {"price": target_price, "color": EXIT_WIN_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"target {target_price:.2f} (+{_fmt_pts(target)}pt)"},
        {"price": stop_price, "color": EXIT_LOSS_COLOR, "lineWidth": 1, "lineStyle": 2,
         "title": f"stop {stop_price:.2f} (-{_fmt_pts(stop)}pt)"},
    ]

    if near_levels:
        lvl_txt = (f"{len(rays)} live M5 {level_type} ray(s) within "
                   f"{M5_NEAR_PTS:.0f}pt formed by H1 breakout "
                   f"(solid = broken, dashed = unbroken; hover for details)")
    elif breakout_out_of_reach:
        lvl_txt = (f"H1 breakout bar predates this contract's tick data -- "
                   f"cannot tell which M5 {level_type} levels existed by then")
    else:
        lvl_txt = (f"no live M5 {level_type} level within {M5_NEAR_PTS:.0f}pt "
                   f"formed by H1 breakout")
    title = (f"M5  |  {lvl_txt}  |  {R._to_pt_str(window.index[0])} \u2192 "
             f"{R._to_pt_str(window.index[-1])}")
    if total_skipped:
        title += f"  [{total_skipped} bars compressed out of view]"
    return {"title": title, "candles": candles, "markers": markers,
            "priceLines": price_lines, "rays": rays, "precision": 2}


CSS = R.CSS + """
<style>
td.good { color:#4ade80; }
td.bad { color:#f87171; }
td.candle { color:#fbbf24; }
.gap-flag { color:#fbbf24; margin-left:4px; cursor:help; }
.table-wrap { max-height:none; }
.expand-th { width:28px; }
tr.lvl-row.is-replayed td.replayed-cell { color:#7bb4f5; font-weight:600; }
tr.lvl-row.is-valid td.valid-cell { color:#4ade80; font-weight:600; }
textarea.trade-note { width:160px; height:34px; resize:vertical; background:var(--surface2);
                       color:var(--text); border:1px solid var(--border); border-radius:4px;
                       font-size:0.9em; padding:3px 5px; }
""" + EXCURSION_CSS + """
/* Half-width H1/M5 panes: keep the hover OHLC readout pinned right and
   fully visible, letting the descriptive part ellipsis instead. */
.chart-title.chart-title-split { display:flex; align-items:baseline; gap:10px; }
.chart-title-split .ct-base { flex:1 1 auto; min-width:0; overflow:hidden;
                              text-overflow:ellipsis; white-space:nowrap; }
.chart-title-split .ct-ohlc { flex:0 0 auto; white-space:nowrap; color:#e5e7eb; }
/* Hover tooltip for the M5 level rays. Positioned inside the chart body (not
   the title bar) so it can wrap onto several lines and list several
   overlapping rays without ever being clipped or ellipsised. */
.chart-ph { position:relative; }
.pane-tip { position:absolute; display:none; z-index:5; pointer-events:none;
            background:rgba(10,14,20,0.94); border:1px solid #38bdf8; border-radius:4px;
            color:#e5e7eb; font-family:'Courier New', monospace; font-size:11px;
            line-height:1.45; padding:5px 8px; max-width:none; white-space:nowrap;
            box-shadow:0 2px 10px rgba(0,0,0,0.6); }
</style>
"""

JS = """
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const CHARTS = __CHARTS_JSON__;
const rendered = {};
const FIXED_BAR_SPACING = 6;
const RAY_HOVER_PTS = 1.5;   // vertical tolerance (points) for hovering a level ray
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
function _centerLogicalRange(chart, el, nBars) {
  if (!nBars) return;
  const barsVisible = Math.max(1, el.clientWidth / FIXED_BAR_SPACING);
  const halfPad = Math.max(0, (barsVisible - nBars) / 2);
  chart.timeScale().setVisibleLogicalRange({ from: -halfPad, to: (nBars - 1) + halfPad });
}
function _renderH1(i, cd) {
  _renderPane('ch1-' + i, 'th1-' + i, cd);
}
function _renderM5(i, cd) {
  _renderPane('cm5-' + i, 'tm5-' + i, cd);
}
function _renderPane(elId, titleId, cd) {
  const el = document.getElementById(elId);
  const titleEl = document.getElementById(titleId);
  if (!el || !titleEl) return;
  const baseTitle = cd.title;
  // Split the legend: the descriptive part truncates, the OHLC readout is
  // pinned right and never clipped. These panes are half-width now, so a
  // single nowrap+ellipsis line would cut the OHLC off on hover.
  titleEl.classList.add('chart-title-split');
  titleEl.textContent = '';
  const baseEl = document.createElement('span');
  baseEl.className = 'ct-base';
  baseEl.textContent = baseTitle;
  baseEl.title = baseTitle;
  const ohlcEl = document.createElement('span');
  ohlcEl.className = 'ct-ohlc';
  titleEl.appendChild(baseEl);
  titleEl.appendChild(ohlcEl);
  const chart = LightweightCharts.createChart(el, Object.assign(_baseOpts(timeFmtH1), {
    autoSize: false, width: el.clientWidth || 800, height: el.clientHeight || 320,
  }));
  const series = _addCandles(chart, cd.precision);
  series.setData(cd.candles);
  if (cd.markers && cd.markers.length) series.setMarkers(cd.markers);
  (cd.priceLines || []).forEach(pl => series.createPriceLine(pl));
  // Level rays: flat line segments spanning formation -> retest. They carry
  // no drawn label (a chart with several of them turns into unreadable
  // overlapping text) -- the details go in the hover tooltip below.
  const rayInfo = [];
  (cd.rays || []).forEach(r => {
    const rs = chart.addLineSeries({
      color: r.color, lineWidth: r.lineWidth || 1,
      lineStyle: (r.lineStyle == null ? 0 : r.lineStyle),
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, pointMarkersVisible: false,
    });
    rs.setData(r.points);
    rayInfo.push({ series: rs, label: r.label });
  });
  let tip = null;
  if (rayInfo.length) {
    tip = document.createElement('div');
    tip.className = 'pane-tip';
    el.appendChild(tip);
  }
  const prec = cd.precision || 2;
  chart.subscribeCrosshairMove((param) => {
    const d = param.seriesData && param.seriesData.get(series);
    if (d && d.open != null) {
      ohlcEl.textContent = 'O ' + d.open.toFixed(prec)
        + '  H ' + d.high.toFixed(prec) + '  L ' + d.low.toFixed(prec)
        + '  C ' + d.close.toFixed(prec);
    } else { ohlcEl.textContent = ''; }
    if (!tip) return;
    // Only the ray(s) actually under the cursor -- a ray has a point on every
    // bar it spans, so "has a value at this time" means the cursor is inside
    // its formation..retest span; the price test picks the one being pointed at.
    if (!param.point || !param.time) { tip.style.display = 'none'; return; }
    const cursorPrice = series.coordinateToPrice(param.point.y);
    const hits = [];
    rayInfo.forEach(ri => {
      const v = param.seriesData.get(ri.series);
      if (v && v.value != null && cursorPrice != null
          && Math.abs(v.value - cursorPrice) <= RAY_HOVER_PTS) hits.push(ri.label);
    });
    if (!hits.length) { tip.style.display = 'none'; return; }
    tip.innerHTML = hits.map(h => '<div>' + h + '</div>').join('');
    tip.style.display = 'block';
    let x = param.point.x + 14, y = param.point.y + 14;
    if (x + tip.offsetWidth > el.clientWidth) x = param.point.x - tip.offsetWidth - 14;
    if (y + tip.offsetHeight > el.clientHeight) y = param.point.y - tip.offsetHeight - 14;
    tip.style.left = Math.max(0, x) + 'px';
    tip.style.top = Math.max(0, y) + 'px';
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
  if (cd.m5) { _renderM5(i, cd.m5); }
  else {
    const t = document.getElementById('tm5-' + i);
    if (t) t.textContent = 'M5  |  (no tick data covering this trade)';
  }
  if (cd.trio) _renderTrio(i, cd.trio);
  if (cd.oneMin) _renderOneMin(i, cd.oneMin);
}
function toggleChart(i) {
  const row = document.getElementById('chart-row-' + i);
  const btn = document.querySelector('.expand-btn[data-idx="' + i + '"]');
  if (!row) return;
  const opening = row.classList.contains('hidden');
  row.classList.toggle('hidden', !opening);
  if (btn) { btn.classList.toggle('open', opening); btn.textContent = opening ? '\\u25bc' : '\\u25b6'; }
  if (opening && !rendered[i]) { _renderStack(i); rendered[i] = true; }
}
</script>
<script>
// Reviewed / Replayed / Notes tracking -- persisted to this browser's
// localStorage under a key unique to THIS report (stop/target combo), with
// each row further keyed by its own trade identity (data-key), so notes
// don't collide across different stop/target reports.
const REVIEW_STORAGE_KEY = '__STORAGE_KEY__';

function loadReviewStore() {
  try { return JSON.parse(localStorage.getItem(REVIEW_STORAGE_KEY) || '{}'); }
  catch (e) { return {}; }
}
function saveReviewStore(store) { localStorage.setItem(REVIEW_STORAGE_KEY, JSON.stringify(store)); }

function reviewRowState(tr) {
  return {
    reviewed: tr.querySelector('.reviewed-cb').checked,
    valid: tr.querySelector('.valid-cb').checked,
    replayed: tr.querySelector('.replayed-cb').checked,
    notes: tr.querySelector('.trade-note').value,
  };
}
function applyReviewRowState(tr, state) {
  if (!state) state = {};
  tr.querySelector('.reviewed-cb').checked = !!state.reviewed;
  tr.querySelector('.valid-cb').checked = !!state.valid;
  tr.querySelector('.replayed-cb').checked = !!state.replayed;
  tr.querySelector('.trade-note').value = state.notes || '';
  tr.classList.toggle('is-reviewed', !!state.reviewed);
  tr.classList.toggle('is-valid', !!state.valid);
  tr.classList.toggle('is-replayed', !!state.replayed);
}
function persistReviewRow(tr) {
  const store = loadReviewStore();
  store[tr.dataset.key] = reviewRowState(tr);
  saveReviewStore(store);
  tr.classList.toggle('is-reviewed', !!store[tr.dataset.key].reviewed);
  tr.classList.toggle('is-valid', !!store[tr.dataset.key].valid);
  tr.classList.toggle('is-replayed', !!store[tr.dataset.key].replayed);
  updateReviewSummary();
  applyReviewFilters();
}
function updateReviewSummary() {
  const reviewed = document.querySelectorAll('.lvl-row.is-reviewed').length;
  const valid = document.querySelectorAll('.lvl-row.is-valid').length;
  const replayed = document.querySelectorAll('.lvl-row.is-replayed').length;
  const elR = document.getElementById('sum-reviewed'); if (elR) elR.textContent = reviewed;
  const elV = document.getElementById('sum-valid'); if (elV) elV.textContent = valid;
  const elP = document.getElementById('sum-replayed'); if (elP) elP.textContent = replayed;
}
function initReview() {
  const store = loadReviewStore();
  document.querySelectorAll('.lvl-row').forEach(tr => {
    applyReviewRowState(tr, store[tr.dataset.key]);
    tr.querySelectorAll('.reviewed-cb, .valid-cb, .replayed-cb, .trade-note').forEach(el => {
      el.addEventListener('change', () => persistReviewRow(tr));
    });
    tr.querySelector('.trade-note').addEventListener('input', () => persistReviewRow(tr));
  });
  updateReviewSummary();
}
function csvEscapeReview(v) {
  v = (v === null || v === undefined) ? '' : String(v);
  return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}
function exportReviewCsv() {
  const store = loadReviewStore();
  const header = ['key', 'reviewed', 'valid', 'replayed', 'notes'];
  const lines = [header.join(',')];
  Object.keys(store).forEach(key => {
    const st = store[key] || {};
    lines.push([key, st.reviewed ? 1 : 0, st.valid ? 1 : 0, st.replayed ? 1 : 0, st.notes || '']
      .map(csvEscapeReview).join(','));
  });
  const blob = new Blob([lines.join('\\n')], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = REVIEW_STORAGE_KEY + '.csv';
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
function parseReviewCsvLine(line) {
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
function importReviewCsv(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const lines = reader.result.split(/\\r?\\n/).filter(l => l.length);
    const header = parseReviewCsvLine(lines[0]);
    const store = loadReviewStore();
    for (let i = 1; i < lines.length; i++) {
      const cols = parseReviewCsvLine(lines[i]);
      const rec = {};
      header.forEach((h, j) => rec[h] = cols[j]);
      if (!rec.key) continue;
      store[rec.key] = { reviewed: rec.reviewed === '1', valid: rec.valid === '1',
                         replayed: rec.replayed === '1', notes: rec.notes || '' };
    }
    saveReviewStore(store);
    document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, store[tr.dataset.key]));
    updateReviewSummary();
    applyReviewFilters();
    evt.target.value = '';
    alert('Imported review notes from ' + file.name);
  };
  reader.readAsText(file);
}
function clearAllReview() {
  localStorage.removeItem(REVIEW_STORAGE_KEY);
  document.querySelectorAll('.lvl-row').forEach(tr => applyReviewRowState(tr, {}));
  updateReviewSummary();
  applyReviewFilters();
}
function applyReviewFilters() {
  const statusOn = Array.from(document.querySelectorAll('.f-review-status:checked')).map(c => c.value);
  const validOn = Array.from(document.querySelectorAll('.f-review-valid:checked')).map(c => c.value);
  const replayOn = Array.from(document.querySelectorAll('.f-review-replay:checked')).map(c => c.value);
  const notesOn = Array.from(document.querySelectorAll('.f-review-notes:checked')).map(c => c.value);
  let shown = 0;
  document.querySelectorAll('.lvl-row').forEach(function(tr) {
    const isReviewed = tr.classList.contains('is-reviewed');
    const isValid = tr.classList.contains('is-valid');
    const isReplayed = tr.classList.contains('is-replayed');
    const hasNotes = tr.querySelector('.trade-note').value.trim().length > 0;
    const statusOk = statusOn.includes(isReviewed ? 'reviewed' : 'unreviewed');
    const validOk = validOn.includes(isValid ? 'valid' : 'not_valid');
    const replayOk = replayOn.includes(isReplayed ? 'replayed' : 'not_replayed');
    const notesOk = notesOn.includes(hasNotes ? 'has_notes' : 'no_notes');
    const show = statusOk && validOk && replayOk && notesOk;
    tr.classList.toggle('hidden', !show);
    if (show) shown++;
    if (!show) {
      const cr = document.getElementById('chart-row-' + tr.dataset.idx);
      if (cr) cr.classList.add('hidden');
    }
  });
  const elS = document.getElementById('sum-shown'); if (elS) elS.textContent = shown;
}
document.querySelectorAll('.f-review-status, .f-review-valid, .f-review-replay, .f-review-notes')
  .forEach(cb => cb.addEventListener('change', applyReviewFilters));
initReview();
applyReviewFilters();
</script>
"""


def _relabel_fta_as_target(chart_dict):
    """build_1s_trio_chart's price-line title for the target level always
    reads "fta {price}" (lxpb.py's own field name for that level). Since
    this report repurposes that field to hold OUR stop/target combo's
    target price (see row_for_trio override above), relabel it to "target"
    here so its on-chart legend matches the entry/stop lines' plain
    wording instead of leaking the original field's internal name."""
    for pl in chart_dict.get("priceLines", []):
        if pl.get("title", "").startswith("fta "):
            pl["title"] = "target " + pl["title"][len("fta "):]


def _select_rows(start=None, end=None, limit=A._DEFAULT, merged=False):
    """Row selection + forward-window simulation shared by the parent
    process and every parallel chunk worker. Deterministic: given the same
    arguments it always yields the same `trades` ordering, which is what
    makes a worker's local chunk indices line up with the parent's global
    ones."""
    h1_df = A.load_merged_h1() if merged else None
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows(
        start=start, end=end, limit=limit, h1_df=h1_df)
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    trades = A.simulate(h1_df, pos_by_ts, strong)
    strong = strong.reset_index(drop=True)
    assert len(strong) == len(trades), (
        f"strong ({len(strong)}) / trades ({len(trades)}) count mismatch -- "
        "simulate() must have dropped a row (near end of data); positional "
        "alignment with `strong` below would be wrong.")
    return h1_df, pos_by_ts, strong, trades


def _build_records(h1_df, pos_by_ts, strong, trades, indices, stop, target,
                   cache_path, label="", candle_exit=False,
                   candle_exit_skip_entry=False):
    """All the .scid-backed heavy lifting for the trades at `indices`
    (positions into the global `trades` list): 1-minute series, stop/target
    resolution, and the H1 / 1s-trio / 1min / footprint charts.

    Returns {global_index: record}. Split out of render() so a chunk of
    indices can run in its own process -- that is the unit of parallelism,
    and it is deliberately contract-pure (see _chunk_indices) so each worker
    only ever memory-maps one ~2-3GB .scid contract.

    `cache_path` MUST be unique per chunk: build_or_load_1min_series keys
    its cache by the trade's position within the list it was handed, so two
    chunks (each re-indexed from 0) would otherwise read each other's bars
    out of a shared file and permanently corrupt it."""
    sub_trades = [trades[i] for i in indices]
    series_by_idx = M.build_or_load_1min_series(sub_trades, cache_path=cache_path)
    resolved_list = resolve_trades(sub_trades, series_by_idx, stop, target,
                                   candle_exit=candle_exit,
                                   candle_exit_skip_entry=candle_exit_skip_entry)

    # Loaded once per chunk (not per row) -- see lxpb_levels_cache.h1_levels,
    # it's the whole merged H1 ledger and is cheap once cached on disk.
    ledger = LC.h1_levels(verbose=False)

    out = {}
    n = len(indices)
    for j, i in enumerate(indices):
        trade, resolved = sub_trades[j], resolved_list[j]
        row_d = strong.iloc[i]
        # Bound how far back "confluence" may reach: only H1 levels formed
        # within CONFLUENCE_LOOKBACK_BARS bars before THIS trade's own P1
        # (breakout) bar. pos_by_ts/h1_df are the same naive-UTC-indexed
        # H1 series build_trade_chart's own P0/P1/P2 markers use.
        breakout_pos = pos_by_ts[row_d["breakout_time"]]
        lookback_pos = max(0, breakout_pos - CONFLUENCE_LOOKBACK_BARS)
        min_formation_time = h1_df.index[lookback_pos]
        confluent = LC.find_confluent_levels(
            ledger, row_d["type"], float(row_d["price"]), row_d["formation_time"],
            row_d["retest_time"], CONFLUENCE_N_POINTS,
            min_formation_time=min_formation_time)
        # Narrower same-side reading (see same_side_live_confluence): same
        # type as this trade, still un-retested as of this trade's own P1.
        same_side = LC.same_side_live_confluence(confluent, row_d["type"], row_d["breakout_time"])
        chart = build_trade_chart(h1_df, pos_by_ts, row_d, trade, resolved, stop, target,
                                  confluent=confluent)

        is_long = row_d["type"] == "LHPB"
        # Real-tick 1s/1min/footprint charts, same real-tick machinery as
        # lxpb_labels_report.html. build_1s_trio_chart reads "fta"/"stop_loss"
        # as its target/stop price lines -- override those two fields (in
        # entry_price's own adjusted scale, not the level's "price") with
        # THIS combo's stop/target so the extra charts show the same
        # stop/target bracket as the H1 chart, not lxpb.py's original
        # fta/stop_loss.
        entry_price_adj = float(row_d["entry_price"])
        row_for_trio = row_d.copy()
        row_for_trio["fta"] = entry_price_adj + target if is_long else entry_price_adj - target
        row_for_trio["stop_loss"] = entry_price_adj - stop if is_long else entry_price_adj + stop
        trio_chart = R.build_1s_trio_chart(row_for_trio, R.PAD_SECONDS_DEFAULT,
                                            R.ONE_MIN_PAD_MINUTES_DEFAULT, True,
                                            touch_time_override=resolved.get("touch_time"))
        if trio_chart is not None:
            _relabel_fta_as_target(trio_chart["trio"])
            _relabel_fta_as_target(trio_chart["oneMin"])
            chart_stack = {"h1": chart, "trio": trio_chart["trio"], "oneMin": trio_chart["oneMin"]}
            fp = {"narrow": trio_chart.get("footprintNarrowHtml"),
                  "wide": trio_chart.get("footprintWideHtml")}
        else:
            chart_stack = {"h1": chart, "trio": None, "oneMin": None}
            fp = {"narrow": "<p class='note'>(no tick data in this window)</p>",
                  "wide": "<p class='note'>(no tick data in this window)</p>"}
        chart_stack["m5"] = build_m5_chart(row_d, resolved, stop, target)
        out[i] = {"chart_stack": chart_stack, "fp": fp, "resolved": resolved,
                  "confluence_count": len(confluent),
                  "same_side_confluence_count": len(same_side)}
        if (j + 1) % 10 == 0 or (j + 1) == n:
            print(f"  {label}built charts for {j + 1}/{n} rows", flush=True)
    return out


def _chunk_indices(trades, n_chunks):
    """Contract-pure chunking -- see M.chunk_indices_by_contract, which owns
    the implementation (shared with the exit-analysis report's grid)."""
    return M.chunk_indices_by_contract(trades, n_chunks)


def _run_chunk_subprocess(spec):
    """Child-process entry point: rebuild the identical selection, do this
    chunk's heavy work, pickle the records out."""
    h1_df, pos_by_ts, strong, trades = _select_rows(
        spec["start"], spec["end"], spec["limit"], spec["merged"])
    recs = _build_records(h1_df, pos_by_ts, strong, trades, spec["indices"],
                          spec["stop"], spec["target"], spec["cache_path"],
                          label=spec["label"], candle_exit=spec["candle_exit"],
                          candle_exit_skip_entry=spec["candle_exit_skip_entry"])
    with open(spec["out_path"], "wb") as f:
        pickle.dump(recs, f, protocol=pickle.HIGHEST_PROTOCOL)


def _build_records_parallel(specs, workers):
    """Run chunk specs as concurrent child processes and merge their records.

    Uses real processes (not threads) because the work is CPU/IO bound
    inside pandas and, more importantly, because each child must be able to
    drop its multi-GB contract cache by exiting."""
    ctx = mp.get_context("spawn")
    running, pending, records = [], list(specs), {}
    failed = []
    while pending or running:
        while pending and len(running) < workers:
            spec = pending.pop(0)
            p = ctx.Process(target=_run_chunk_subprocess, args=(spec,), daemon=False)
            p.start()
            print(f"[parallel] started {spec['label'].strip()} pid={p.pid} "
                  f"({len(spec['indices'])} trades)", flush=True)
            running.append((p, spec))
        time.sleep(2.0)
        for p, spec in list(running):
            if p.is_alive():
                continue
            running.remove((p, spec))
            if p.exitcode != 0 or not os.path.exists(spec["out_path"]):
                failed.append((spec["label"].strip(), p.exitcode))
                continue
            with open(spec["out_path"], "rb") as f:
                records.update(pickle.load(f))
            os.remove(spec["out_path"])
            print(f"[parallel] finished {spec['label'].strip()} "
                  f"({len(records)} records so far)", flush=True)
    if failed:
        raise RuntimeError(f"parallel chunk(s) failed: {failed}")
    return records


def render(stop, target, output_path, start=None, end=None, limit=A._DEFAULT,
           merged=False, workers=1, storage_key=None, title_suffix=None,
           candle_exit=False, candle_exit_skip_entry=False):
    # Resolve the "not passed" sentinel HERE, before it can reach a worker.
    # A._DEFAULT is a bare object(), so its identity is what marks the
    # default -- and identity does not survive pickling: a child process
    # unpickles a DIFFERENT object, `limit is A._DEFAULT` is False there, and
    # load_strong_breakout_rows calls .head() on the sentinel itself. That
    # made every --workers>1 run crash unless the caller happened to pass a
    # real limit (which --full-year does, as limit=None).
    limit = A.DEFAULT_LIMIT if limit is A._DEFAULT else limit
    h1_df, pos_by_ts, strong, trades = _select_rows(start, end, limit, merged)
    print(f"Selected {len(trades)} strong-breakout trades "
          f"({strong['retest_time'].min()} -> {strong['retest_time'].max()})", flush=True)

    # Chunk cache files are keyed to the SELECTION (span + limit + merge) AND
    # to a digest of the exact global trade indices the chunk covers, not just
    # its ordinal: build_or_load_1min_series' cache key is the trade's
    # position within the list it was handed, so re-running with a different
    # --workers (which moves the chunk boundaries) would otherwise map
    # different trades onto the same "chunk 0" keys in the same file. That is
    # precisely the silent cross-selection corruption its docstring warns
    # about, so the digest makes any change of membership a different file.
    sel_tag = f"{start or A.DEFAULT_START}_{end or A.DEFAULT_END}_{'m' if merged else 's'}"
    sel_tag = sel_tag.replace("-", "").replace(":", "").replace(" ", "")
    cache_dir = os.path.join(_HERE, "data", "1min_chunks")
    os.makedirs(cache_dir, exist_ok=True)

    def _chunk_cache(idxs):
        digest = hashlib.sha1(",".join(map(str, idxs)).encode()).hexdigest()[:10]
        return os.path.join(cache_dir, f"1min_{sel_tag}_{digest}.csv")

    if workers > 1 and len(trades) > 1:
        chunks = _chunk_indices(trades, workers)
        specs = []
        for c, idxs in enumerate(chunks):
            specs.append({
                "indices": idxs, "start": start, "end": end, "limit": limit,
                "merged": merged, "stop": stop, "target": target,
                "cache_path": _chunk_cache(idxs),
                "out_path": os.path.join(cache_dir, f"recs_{sel_tag}_c{c:02d}.pkl"),
                "label": f"chunk{c:02d} ", "candle_exit": candle_exit,
                "candle_exit_skip_entry": candle_exit_skip_entry,
            })
        print(f"[parallel] {len(specs)} contract-pure chunks, {workers} concurrent: "
              + ", ".join(f"c{c:02d}={len(s['indices'])}" for c, s in enumerate(specs)), flush=True)
        records = _build_records_parallel(specs, workers)
    else:
        all_idx = list(range(len(trades)))
        records = _build_records(h1_df, pos_by_ts, strong, trades, all_idx, stop, target,
                                 _chunk_cache(all_idx), candle_exit=candle_exit,
                                 candle_exit_skip_entry=candle_exit_skip_entry)

    missing = [i for i in range(len(trades)) if i not in records]
    if missing:
        raise RuntimeError(f"{len(missing)} trades missing from chunk results: {missing[:10]}")
    resolved_list = [records[i]["resolved"] for i in range(len(trades))]

    wins = sum(1 for r in resolved_list if r["outcome"] == "target")
    losses = sum(1 for r in resolved_list if r["outcome"] == "stop")
    no_hits = sum(1 for r in resolved_list if r["outcome"] == "no_hit")
    candles = sum(1 for r in resolved_list if r["outcome"] == "candle")
    r_values = [r["r"] for r in resolved_list if r["r"] is not None]
    # "win rate" stays the target-hit rate (its meaning in every other
    # report here). With the candle rule on, most exits are neither target
    # nor stop, so `profit_rate` -- the share of resolved trades that ended
    # up ahead at all -- is the number that actually describes the variant.
    win_rate = wins / len(r_values) if r_values else 0.0
    profit_rate = (sum(1 for v in r_values if v > 0) / len(r_values)) if r_values else 0.0
    avg_r = float(np.mean(r_values)) if r_values else 0.0
    total_r = float(np.sum(r_values)) if r_values else 0.0
    candle_r = [r["r"] for r in resolved_list
                if r["outcome"] == "candle" and r["r"] is not None]
    avg_candle_r = float(np.mean(candle_r)) if candle_r else 0.0
    # a) worst (largest) adverse excursion any WINNING trade went through
    #    before ultimately hitting target; b) largest favorable excursion
    #    any LOSING trade went through before ultimately hitting stop --
    #    see _compute_excursion.
    win_mae_values = [r["adverse_pts"] for r in resolved_list
                       if r["outcome"] == "target" and r.get("adverse_pts") is not None]
    loss_mfe_values = [r["favorable_pts"] for r in resolved_list
                        if r["outcome"] == "stop" and r.get("favorable_pts") is not None]
    max_win_mae = max(win_mae_values) if win_mae_values else 0.0
    max_loss_mfe = max(loss_mfe_values) if loss_mfe_values else 0.0
    # Same two columns as the table below, summarised as a distribution. A
    # candle exit implies neither number, so it gets its own rows.
    candle_mfe_values = [r["favorable_pts"] for r in resolved_list
                         if r["outcome"] == "candle" and r.get("favorable_pts") is not None]
    candle_mae_values = [r["adverse_pts"] for r in resolved_list
                         if r["outcome"] == "candle" and r.get("adverse_pts") is not None]
    pctile_html = excursion_percentile_html([
        ("MFE &mdash; losing trades", "ran this far in favour before hitting stop",
         loss_mfe_values),
        ("MAE &mdash; winning trades", "heat taken before reaching target",
         win_mae_values),
        ("MFE &mdash; candle exits", "ran this far in favour before the candle rule fired",
         candle_mfe_values),
        ("MAE &mdash; candle exits", "heat taken before the candle rule fired",
         candle_mae_values),
        ("Max DD &mdash; all trades", "handed back from the best price the open position reached",
         [r["giveback_pts"] for r in resolved_list if r.get("giveback_pts") is not None]),
        ("Max DD &mdash; winning trades", "handed back before the winner reached target",
         [r["giveback_pts"] for r in resolved_list
          if r["outcome"] == "target" and r.get("giveback_pts") is not None]),
    ], stop)
    gapped_entries = sum(1 for r in resolved_list if r.get("entry_gapped"))

    charts, rows_html = [], []
    for i, resolved in enumerate(resolved_list):
        row_d = strong.iloc[i]
        rec = records[i]
        charts.append(rec["chart_stack"])
        fp = rec["fp"]
        confluence_count = rec.get("confluence_count", 0)
        same_side_confluence_count = rec.get("same_side_confluence_count", 0)

        level_type = row_d["type"]
        price = float(row_d["price"])
        is_long = level_type == "LHPB"
        target_price = price + target if is_long else price - target
        stop_price = price - stop if is_long else price + stop

        outcome = resolved["outcome"]
        r_val = resolved["r"]
        if outcome == "target":
            outcome_cls = "good"
        elif outcome == "stop":
            outcome_cls = "bad"
        elif outcome == "candle":
            # The candle rule's R is variable, so colour by the actual
            # result rather than by the exit reason.
            outcome_cls = "good" if (r_val or 0) > 0 else ("bad" if (r_val or 0) < 0 else "candle")
        else:
            outcome_cls = ""
        outcome_label = {"target": "WIN", "stop": "LOSS", "candle": "CANDLE",
                         "no_hit": "NO-HIT", "no_data": "NO DATA"}[outcome]
        exit_px = resolved.get("exit_price")
        exit_px_str = f"{exit_px:.2f}" if outcome != "no_hit" and exit_px is not None else "-"
        exit_str = R._to_pt_str(resolved["exit_time"]) if resolved["exit_time"] is not None else "-"
        entry_str = R._to_pt_str(resolved["touch_time"]) if resolved.get("touch_time") is not None \
            else R._to_pt_str(row_d["retest_time"])
        type_cls = "type-lhpb" if is_long else "type-llpb"

        # a) for a WINNING trade, how far price moved AGAINST the position
        #    (adverse_pts) before it ultimately hit target; b) for a LOSING
        #    trade, how far price moved IN FAVOR of the position
        #    (favorable_pts) before it ultimately hit stop. Real-1s-tick
        #    precision throughout, bounded to touch_time..exit_time (see
        #    _compute_excursion). "-" for no_hit/no_data rows (no pinned
        #    exit_time to bound the window at).
        #    A CANDLE exit shows BOTH: unlike a bracket exit, neither number
        #    is implied by the outcome (a winner's MFE is the target
        #    distance by definition, a loser's MAE the stop distance), so
        #    both carry real information about how the trade actually went.
        if outcome == "target":
            mae_str = f"{resolved['adverse_pts']:.2f}" if resolved.get("adverse_pts") is not None else "-"
            mfe_str = "-"
        elif outcome == "stop":
            mae_str = "-"
            mfe_str = f"{resolved['favorable_pts']:.2f}" if resolved.get("favorable_pts") is not None else "-"
        elif outcome == "candle":
            mae_str = f"{resolved['adverse_pts']:.2f}" if resolved.get("adverse_pts") is not None else "-"
            mfe_str = f"{resolved['favorable_pts']:.2f}" if resolved.get("favorable_pts") is not None else "-"
        else:
            mae_str = mfe_str = "-"

        # Max points handed back from the best price the OPEN position
        # reached (see _compute_giveback). Always shown -- unlike MAE/MFE
        # neither outcome implies it, and it is the number that says how far
        # behind the extreme a trailing stop would have had to sit.
        gb = resolved.get("giveback_pts")
        gb_str = f"{gb:.2f}" if gb is not None else "-"

        # Entry is the level's own price with no slippage modeled. When a
        # tick gaps clean THROUGH the level, that price never actually
        # traded between touch and exit, so the fill is fictional and the
        # trade's R is not something the strategy could have realised.
        # Flag it rather than dropping it -- the row is still worth seeing.
        gap_flag = ""
        if resolved.get("entry_gapped"):
            gap_flag = ('<span class="gap-flag" title="Entry price never traded between '
                        'touch and exit -- price gapped through the level, so this fill '
                        'was not actually available.">\u26a0</span>')

        fp_narrow_html = fp.get("narrow")
        fp_wide_html = fp.get("wide")
        fp_section = (
            f'<div class="chart-row-2col footprint-outer-row">'
            f'<div class="footprint-pair">'
            f'<div class="chart-cell footprint-cell">{fp_narrow_html}</div>'
            f'<div class="chart-cell footprint-cell">{fp_wide_html}</div>'
            f'</div></div>'
        )

        # Stable per-row key for the Reviewed/Replayed/Notes localStorage
        # store -- identifies this trade (not just its position `i`, which
        # would silently reshuffle saved notes if the underlying row set
        # ever changes) within THIS stop/target report.
        row_key = f"{level_type}_{price:.2f}_{entry_str}".replace(" ", "_")

        rows_html.append(f"""
<tr class="lvl-row {type_cls}" data-idx="{i}" data-key="{row_key}" onclick="toggleChart({i})">
  <td class="left">{i}</td><td class="left type-cell">{level_type}</td>
  <td class="left">{entry_str}</td>
  <td>{price:.2f}{gap_flag}</td><td>{stop_price:.2f}</td><td>{target_price:.2f}</td>
  <td>{confluence_count}</td>
  <td>{same_side_confluence_count}</td>
  <td class="{outcome_cls}">{outcome_label}</td>
  <td class="left">{exit_str}</td><td>{exit_px_str}</td>
  <td class="bad">{mae_str}</td><td class="good">{mfe_str}</td><td>{gb_str}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb"></td>
  <td class="replayed-cell" onclick="event.stopPropagation();"><input type="checkbox" class="replayed-cb"></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}"
      onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="19"><div class="chart-stack">
    <div class="chart-row-2col">
      <div class="chart-cell chart-h1"><div class="chart-title" id="th1-{i}"></div><div class="chart-ph" id="ch1-{i}"></div></div>
      <div class="chart-cell chart-h1"><div class="chart-title" id="tm5-{i}"></div><div class="chart-ph" id="cm5-{i}"></div></div>
    </div>
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
    {fp_section}
  </div></td>
</tr>
""")

    candle_rule_html = ""
    candle_boxes = ""
    if candle_exit:
        side_txt = ("closes BELOW the previous candle's LOW <em>and</em> below the entry level "
                    "(mirrored for a short: closes above the previous candle's HIGH and above "
                    "the entry level)")
        entry_bar_txt = (
            "The entry minute's own candle is <b>NOT</b> eligible here: the rule starts at the "
            "first full candle after entry, whose reference is the FULL entry clock minute. "
            "(The entry candle is the very candle that pushed into the level, so the mirrored "
            "condition is disproportionately already true on it -- it accounted for 44% of all "
            "rule exits when it was allowed to fire.)"
            if candle_exit_skip_entry else
            "The entry minute's own candle is eligible (it does close after the retest), and it "
            "is compared against the FULL clock minute before entry -- both rebuilt from whole "
            "clock minutes rather than from the trade-anchored series, whose first bar is only "
            "the post-entry fragment of its minute.")
        candle_rule_html = f"""
<b>Candle-close exit variant.</b> On top of the {_fmt_pts(stop)}/{_fmt_pts(target)} bracket, the trade is also
closed at market by the first 1-minute candle after the retest that {side_txt}. The signal is only
known at that candle's CLOSE, so a stop or target actually filled anywhere inside the same minute
still wins; the rule takes the minute only when the bracket's own tick escalation found no
qualifying fill there. {entry_bar_txt} The market order goes in at the candle's close instant and
is filled at the prevailing quote on the very next tick record -- the best BID for a long, the best
ASK for a short (Sierra's raw tick records carry the quote: Low = bid, High = ask), so the spread
is paid rather than the candle's close price being booked. These rows show outcome CANDLE with a
real, variable R = (fill - entry) / {_fmt_pts(stop)}pt, coloured by whether that R came out positive.
"""
        candle_boxes = (f'\n  <div class="box"><strong>{candles}</strong>candle exits</div>'
                        f'\n  <div class="box"><strong>{profit_rate*100:.1f}%</strong>profitable</div>'
                        f'\n  <div class="box"><strong>{avg_candle_r:+.2f}</strong>avg R (candle)</div>')

    header = f"""
<h1>LXPB Strong-Breakout Trades &mdash; Stop {_fmt_pts(stop)} / Target {_fmt_pts(target)}{
    ' &mdash; candle-close exit' if candle_exit else ''}{
    ' (from the candle after entry)' if candle_exit and candle_exit_skip_entry else ''}</h1>
<p class="lead">Same {len(trades)} "strong breakout" LXPB retests as exit_analysis_report.html's
1-minute-resolved grid, walked forward with a fixed stop={_fmt_pts(stop)}pt / target={_fmt_pts(target)}pt bracket.
{candle_rule_html}
Entry is anchored to the real 1-second-tick instant the level was actually FILLABLE within the
retest H1 bar: a long entry is a resting buy, so only a bid-side (seller-initiated) print at/through
the level counts (an earlier version used the naive first touch by EITHER side, which could be
several seconds too early); a short entry symmetrically requires an ask-side print. Exits are
pinned to the exact second and price via real 1s ticks for the resolving minute; a target exit is
likewise a resting limit order and only counts an opposite-side print (ask-side for a long's
target, bid-side for a short's), while a stop exit -- a stop/market order once triggered -- fills
on any side. Entry price itself is still the level's own retest price (P2, no slippage modeled).
Click a row to expand its H1 chart: gold line = entry level, green dashed = target, red dashed =
stop; P0/P1/P2 markers mark formation/breakout/retest, and a 4th green/red arrow marks the resolved
exit bar. "MAE (win)" = max points a WINNING trade moved against the position before hitting
target; "MFE (loss)" = max points a LOSING trade moved in the position's favor before hitting
stop -- both computed from real 1s ticks (see _compute_excursion), not just 1-minute bars, and
both measured over the trade's own [touch, exit] span so neither pre-entry nor post-exit movement
is credited. (A CANDLE row shows both, since for it neither number is implied by the outcome.)
"Max DD" = the largest give-back in points from the best price the OPEN position ever reached,
on the same two sides as MAE/MFE above (a long's watermark is the running max of the ask/High,
marked down to the bid/Low; mirrored for a short). Unlike MAE/MFE it is path-dependent -- the
peak must precede the trough -- so it is scanned tick by tick across the whole [touch, exit] span
rather than using 1-minute bars for the middle; give-backs under {MIN_GIVEBACK_PTS}pt are treated as spread
noise and reported as 0.00. It is shown for every outcome and answers "how far did this trade
hand back before it resolved". Note it spans bid to ask, so it runs about one tick wider than the
give-back you could actually have liquidated at. A \u26a0 beside the Entry price marks a GAPPED ENTRY: that price never traded between
touch and exit, so the modeled no-slippage fill was never actually available and the row's R is
not something the strategy could have realised.
Reviewed/Valid/Replayed/Notes persist in this browser's localStorage (keyed to this
stop/target report) and can be exported/imported as CSV (top-right buttons).</p>
<div class="summary">
  <div class="box"><strong>{len(trades)}</strong>trades</div>
  <div class="box"><strong>{wins}</strong>wins</div>
  <div class="box"><strong>{losses}</strong>losses</div>
  <div class="box"><strong>{no_hits}</strong>no-hit</div>
  <div class="box"><strong>{win_rate*100:.1f}%</strong>win rate</div>{candle_boxes}
  <div class="box"><strong>{avg_r:.2f}</strong>avg R</div>
  <div class="box"><strong>{total_r:.1f}</strong>total R</div>
  <div class="box"><strong>{max_win_mae:.2f}</strong>max MAE (win)</div>
  <div class="box"><strong>{max_loss_mfe:.2f}</strong>max MFE (loss)</div>
  <div class="box"><strong>{gapped_entries}</strong>gapped entry</div>
  <div class="box"><strong id="sum-shown">{len(trades)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>
  <div class="box"><strong id="sum-replayed">0</strong>replayed</div>
  <div class="toolbar">
    <button class="btn" onclick="exportReviewCsv()">\u2b07 Export notes CSV</button>
    <label class="btn" for="import-review-file">\u2b06 Import notes CSV</label>
    <input type="file" id="import-review-file" accept=".csv" class="hidden" onchange="importReviewCsv(event)">
    <button class="btn" onclick="if(confirm('Clear ALL saved Reviewed/Valid/Replayed/Notes in this browser for this report?')) clearAllReview();">\U0001f5d1 Clear all</button>
  </div>
</div>
{pctile_html}
"""
    filter_panel = """
<div class="filter-panel">
  <div class="filter-row">
    <span class="filter-label">Status</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="unreviewed" checked> Unreviewed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-status" value="reviewed" checked> Reviewed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Valid</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="not_valid" checked> Not valid</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-valid" value="valid" checked> Valid</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Replayed</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-replay" value="not_replayed" checked> Not replayed</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-replay" value="replayed" checked> Replayed</label>
  </div>
  <div class="filter-row">
    <span class="filter-label">Notes</span>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="no_notes" checked> No notes</label>
    <label class="chip"><input type="checkbox" class="f-cb f-review-notes" value="has_notes" checked> Has notes</label>
  </div>
</div>
"""
    thead = """
<table id="lvl-table">
<thead><tr>
  <th class="left">#</th><th class="left">Type</th><th class="left">Entry (touch) time</th>
  <th>Entry</th><th>Stop</th><th>Target</th><th>Confl.</th>
  <th title="Same-side confluence: other LXPB levels of the SAME type (LHPB for a long, LLPB for a short) in the confluence zone, still un-retested as of this trade's own P1 breakout bar">SS Confl.</th>
  <th>Outcome</th>
  <th class="left">Exit time</th><th>Exit px</th><th>MAE (win)</th><th>MFE (loss)</th><th>Max DD</th>
  <th>Reviewed</th><th>Valid</th><th>Replayed</th>
  <th class="left">Notes</th><th class="expand-th">\u25b6</th>
</tr></thead>
<tbody>
"""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>LXPB Stop {_fmt_pts(stop)} / Target {_fmt_pts(target)}{' candle-exit' if candle_exit else ''}{' (skip entry bar)' if candle_exit and candle_exit_skip_entry else ''} Trades{title_suffix or ''}</title>
{CSS}
</head><body>
{header}
{filter_panel}
<div class="table-wrap">{thead}
{''.join(rows_html)}
</tbody></table></div>
{JS.replace("__CHARTS_JSON__", json.dumps(charts))
   .replace("__STORAGE_KEY__", storage_key
            or f"lxpb_trade_review_v1_stop{_fmt_pts(stop)}_target{_fmt_pts(target)}")}
</body></html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved -> {output_path}  ({len(trades)} trades, {wins}W/{losses}L/"
          f"{candles}C/{no_hits}NH, win rate {win_rate*100:.1f}%, "
          f"profitable {profit_rate*100:.1f}%, avg_R {avg_r:.2f}, total_R {total_r:.1f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-trade H1 chart report for one stop/target combo")
    parser.add_argument("--stop", type=float, default=DEFAULT_STOP)
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--output", default=None)
    parser.add_argument("--start", default=None,
                        help="first retest date (default: this script's original 2026-07-01)")
    parser.add_argument("--end", default=None,
                        help="last retest date (default: this script's original 2026-08-31)")
    parser.add_argument("--limit", default=None,
                        help="max rows, newest-first (int, or 'none' for no cap)")
    parser.add_argument("--merged", action="store_true",
                        help="merge every TradingView H1 export (newest wins) instead of "
                             "using only the single default one -- needed for spans that "
                             "run past the default export's last bar")
    parser.add_argument("--full-year", action="store_true",
                        help="shorthand for --start 2026-01-01 --end 2026-12-31 --limit none --merged")
    parser.add_argument("--workers", type=int, default=1,
                        help="concurrent chunk processes; chunks are contract-pure so each "
                             "holds only ~1 contract (~2-3GB) of .scid ticks in memory")
    parser.add_argument("--storage-key", default=None)
    parser.add_argument("--candle-exit", action="store_true",
                        help="also close at market on the first 1-minute candle after the "
                             "retest that closes below the previous candle's LOW and below "
                             "the entry level (mirrored for shorts) -- see resolve_trades")
    parser.add_argument("--candle-exit-skip-entry", action="store_true",
                        help="with --candle-exit, make the entry minute's own candle "
                             "ineligible so the rule can only fire from the first full "
                             "candle after entry onwards")
    args = parser.parse_args()

    start, end, merged = args.start, args.end, args.merged
    limit = A._DEFAULT
    if args.limit is not None:
        limit = None if str(args.limit).lower() in ("none", "0", "all") else int(args.limit)
    suffix, skey = None, args.storage_key
    # The candle rule is a different STRATEGY on the same bracket, so it gets
    # its own filename and its own localStorage key -- otherwise its rows
    # would inherit (and overwrite) the plain combo's saved review notes.
    ce_tag = "_candleexit" if args.candle_exit else ""
    if args.candle_exit and args.candle_exit_skip_entry:
        ce_tag += "_skipentry"
    if args.full_year:
        start, end, limit, merged = start or "2026-01-01", end or "2026-12-31", None, True
        suffix = " (2026 full year)"
        skey = skey or (f"lxpb_trade_review_v1_stop{_fmt_pts(args.stop)}_"
                        f"target{_fmt_pts(args.target)}{ce_tag}_fy2026")
    else:
        skey = skey or (f"lxpb_trade_review_v1_stop{_fmt_pts(args.stop)}_"
                        f"target{_fmt_pts(args.target)}{ce_tag}")

    default_name = f"stop{_fmt_pts(args.stop)}_target{_fmt_pts(args.target)}{ce_tag}_trades_report.html"
    if args.full_year:
        default_name = (f"stop{_fmt_pts(args.stop)}_target{_fmt_pts(args.target)}{ce_tag}"
                        f"_trades_report_2026_full_year.html")
    out = args.output or os.path.join(_HERE, default_name)
    render(args.stop, args.target, out, start=start, end=end, limit=limit,
           merged=merged, workers=args.workers, storage_key=skey, title_suffix=suffix,
           candle_exit=args.candle_exit,
           candle_exit_skip_entry=args.candle_exit_skip_entry)

