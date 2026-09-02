"""
Re-resolve the stop/target grid search at 1-minute resolution (escalating to
real 1s ticks only for the rare minute where BOTH stop and target fall inside
that single minute's High/Low range -- i.e. only when 1min itself can't tell
which was touched first), instead of the original H1-bar walk in
analyze_breakout_exits.py, which had a look-ahead bias: an H1 bar is wide
enough that a tight stop AND a wide target often land in the SAME bar, and
the sim just assumed "target wins" on that tie -- inflating win rate/avg_R
for tight stops (see session discussion; e.g. stop=2/target=12 had 38 of 54
"wins" be same-H1-bar ties).

IMPORTANT (fixed after a second, more subtle look-ahead bug found in a later
session -- see build_or_load_1min_series's docstring): the 1-minute series
is anchored to each trade's real tick-accurate TOUCH instant within the
retest H1 bar (found via render_labels_report._find_touch_time on raw
ticks), not to the retest bar's own start-of-hour timestamp. Anchoring to
the bar start let price action from BEFORE the level was actually retested
(i.e. before the trade could have been entered) count towards a stop/target
hit -- e.g. one trade's target was "hit" at the very first minute of the
retest hour, 18 minutes before price ever touched back down to the entry
level. Exit timing is additionally pinned to the exact second (not just the
resolving minute) by escalating to real 1s ticks for the resolving minute
(see _pin_exact_exit), for BOTH ties and ordinary single-sided hits.

Real ticks come from the same local Sierra Chart .scid files (D:\\SC\\Data)
and roll-timing logic (build_es_h1_2026_backadjusted.CONTRACTS) already used
by render_labels_report.py's 1s/1min/footprint charts -- there is no
prebuilt 1-minute or 1-second CSV under D:\\lxpb\\data (only 5-min/H1/D1
files exist there); this script builds 1-minute bars itself from those real
ticks for exactly the 72 strong-breakout trades' forward windows (all within
2026, so within CONTRACTS' verified real-tick coverage), and CACHES the
built 1-minute OHLC to data\\1min_strong_breakout_window_v2.csv inside
lxpb-v2 so reruns don't need to re-touch D:\\SC\\Data (the "_v2" cache is a
new file, not a resumption of the old pre-fix "1min_strong_breakout_window.csv"
cache, which is anchored wrong and must not be reused). 1s ticks for
ambiguous minutes / precise exit pinning are fetched on demand (not bulk-
persisted -- full 1s for the whole window across all trades would be tens of
millions of rows).
"""
import os
import time
import numpy as np
import pandas as pd

import analyze_breakout_exits as A
import render_labels_report as R

_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_1MIN_PATH = os.path.join(_HERE, "data", "1min_strong_breakout_window_v2.csv")

STOPS = [2, 3, 4, 6, 8, 10, 12, 16, 20]
TARGETS = [2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30]


def _find_trade_touch_time(t):
    """Tick-accurate instant (not just the retest H1 bar's own hour-start
    timestamp) within the retest hour that the level was actually FILLED --
    see module docstring. A LONG entry is a resting BUY order sitting at the
    level: it can only be filled by a trade on the BID side (BidVolume > 0,
    i.e. a seller hitting that bid), never by an ASK-side print (a buyer
    lifting the offer at/through the level does not execute against a
    resting buy). Symmetrically, a SHORT entry is a resting SELL, filled
    only by an ASK-side print (AskVolume > 0). Using the naive "any side"
    touch (R._find_touch_time's default behaviour) can be several seconds
    too early -- e.g. one trade's naive touch was an ask-side (buyer-paid)
    print dipping through the level, ~5s before the first real bid-side
    (seller-hit) print that would have actually filled the resting long.
    Returns hour_start itself (a safe/conservative fallback) if no tick
    coverage exists for that hour, or if no correct-side fill is found
    anywhere in the retest hour (falls back to the naive any-side touch)."""
    hour_start = pd.Timestamp(t["retest_time"], tz="UTC")
    hour_end = hour_start + pd.Timedelta(hours=1)
    hour_ticks = R._ticks_for_window(hour_start, hour_end)
    if hour_ticks is None or hour_ticks.empty:
        return hour_start
    offset, _sym = R._offset_for_ts(hour_start)
    raw_entry = t["entry"] - offset
    is_long = t["is_long"]
    level_type = t["type"]
    win = hour_ticks.loc[(hour_ticks.index >= hour_start) & (hour_ticks.index < hour_end)]
    for ts, r in win.iterrows():
        right_side = (r.BidVolume > 0) if is_long else (r.AskVolume > 0)
        if not right_side:
            continue
        touched = r.Low <= raw_entry <= r.High
        gap_over = (r.High < raw_entry) if level_type == "LHPB" else (r.Low > raw_entry)
        if touched or gap_over:
            return ts
    # No correct-side fill anywhere in the hour (rare) -- fall back to the
    # naive any-side touch rather than defaulting all the way to hour_start.
    return R._find_touch_time(hour_ticks, hour_start, hour_end, raw_entry, level_type)


def build_or_load_1min_series(trades, horizon_bars=A.HORIZON_BARS, cache_path=None):
    """One combined 1-minute OHLC series (raw/uncontinuous terms) per
    trade's own forward window, cached to CACHE_1MIN_PATH keyed by trade
    INDEX + retest_time (see key format below) so repeat runs don't refetch
    .scid data. Anchored to each trade's tick-accurate touch_time (see
    _find_trade_touch_time), NOT the retest H1 bar's own start-of-hour
    timestamp -- see module docstring for why that distinction matters.
    touch_time is always (re)computed fresh (a cheap single-hour tick
    fetch) even on a 1-min cache hit, and exposed via each returned
    DataFrame's `.attrs["touch_time"]` for callers that need the exact
    entry instant.

    `cache_path` MUST be overridden by any caller whose `trades` list is
    not the canonical "strong breakout" sample: the cache key is
    `{trade_index}_{retest_time}`, which is only unique WITHIN one fixed
    trade list. A different selection (different filter, different
    ordering) reuses the same integer indices for different trades, so
    sharing CACHE_1MIN_PATH across selections would both read another
    selection's bars for a same-index/same-hour trade and permanently
    pollute that file for the original caller."""
    CACHE_1MIN_PATH = cache_path or globals()["CACHE_1MIN_PATH"]
    if os.path.exists(CACHE_1MIN_PATH):
        # trade_key is a pure-digit string (e.g. "0042_20260817080000") --
        # must be forced to str, else pandas infers int64 on read and every
        # `key in by_trade` lookup below silently misses, causing a full
        # re-fetch + duplicate-append of the whole cache on every run.
        cached = pd.read_csv(CACHE_1MIN_PATH, parse_dates=["time"], dtype={"trade_key": str})
        cached["time"] = pd.to_datetime(cached["time"], utc=True)
        by_trade = {k: g.set_index("time")[["open", "high", "low", "close"]]
                    for k, g in cached.groupby("trade_key")}
    else:
        by_trade = {}

    all_rows = []
    result = {}
    for i, t in enumerate(trades):
        # MUST include the trade's own index, not just retest_time: two
        # different trades (e.g. a long and a short level, or two distinct
        # price levels) can retest within the SAME H1 bar, so retest_time
        # alone is not a unique key -- using it alone previously caused
        # groupby("trade_key") on reload to silently MERGE multiple
        # unrelated trades' 1-minute bars into one combined (wrong, longer)
        # series, corrupting their stop/target resolution on every run that
        # read the cache back from disk (as opposed to a from-scratch build,
        # which never hits this path since result[i] is set directly from
        # the freshly-fetched bars before any cache read/write round-trip).
        key = f"{i:04d}_" + pd.Timestamp(t["retest_time"]).strftime("%Y%m%d%H%M%S")
        touch_time = _find_trade_touch_time(t)
        if key in by_trade:
            bars = by_trade[key]
            bars.attrs["touch_time"] = touch_time
            result[i] = bars
            continue
        hi_needed = pd.Timestamp(t["retest_time"], tz="UTC") + pd.Timedelta(hours=horizon_bars)
        ticks = R._ticks_for_window(touch_time, hi_needed)
        if ticks is None or ticks.empty:
            result[i] = None
            continue
        bars = ticks.resample("1min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
        bars["Close"] = bars["Close"].ffill()
        bars["Open"] = bars["Open"].fillna(bars["Close"])
        bars["High"] = bars["High"].fillna(bars["Close"])
        bars["Low"] = bars["Low"].fillna(bars["Close"])
        bars = bars.dropna(subset=["Close"])
        bars.columns = ["open", "high", "low", "close"]
        bars.index.name = "time"
        bars.attrs["touch_time"] = touch_time
        result[i] = bars
        rows = bars.reset_index()
        rows.insert(0, "trade_key", key)
        all_rows.append(rows)

    if all_rows:
        combined_new = pd.concat(all_rows, ignore_index=True)
        if os.path.exists(CACHE_1MIN_PATH):
            combined_new.to_csv(CACHE_1MIN_PATH, mode="a", header=False, index=False)
        else:
            combined_new.to_csv(CACHE_1MIN_PATH, index=False)
        print(f"Cached {len(combined_new)} new 1min rows -> {CACHE_1MIN_PATH}")
    return result


_1S_AMBIGUOUS_CACHE = {}


def _pin_exact_exit(sym, minute_start, offset, entry_price_adj, stop, target, is_long,
                     not_before=None):
    """Fetch real 1s ticks for exactly this one (already-resolved, at
    1-minute resolution) minute and walk them in order to pin the EXACT
    second (and price) the stop or target was actually crossed -- used for
    both ambiguous ties (1-min bar's own H/L range covers both levels) and
    ordinary single-sided hits, so every reported exit_time in this module
    is accurate to the second, not just to the minute.

    STOP fills are checked on ANY side (a stop-loss is a stop/market order
    once triggered -- it doesn't wait for a specific resting-order
    counterparty). TARGET fills, however, are a resting LIMIT order and can
    only be filled by the opposite-side aggressor actually trading into it:
    a LONG's target is a resting SELL, filled only by an ASK-side print
    (a buyer lifting the offer); a SHORT's target is a resting BUY, filled
    only by a BID-side print (a seller hitting the bid) -- same fill-realism
    principle as _find_trade_touch_time's entry-side check.

    `not_before` (typically the trade's tick-accurate touch_time) MUST be
    passed whenever the resolving minute can be the same clock-minute as
    entry (i.e. always -- the caller doesn't know in advance): this
    function fetches the FULL clock minute (e.g. 07:42:00-07:42:59), but
    when the trade wasn't actually filled until partway through that same
    minute (e.g. touch at 07:42:13), ticks before the touch (07:42:00-12)
    are PRE-ENTRY and must not be allowed to resolve the trade -- otherwise
    a stop/target level that was merely brushed before the position even
    existed gets credited as the exit (found via a real trade: touch at
    07:42:13, naive scan hit target at 07:42:00, i.e. 13s before entry).
    Ticks before `not_before` are dropped before scanning; harmless when
    minute_start is already later than not_before (normal, non-first-bar
    case), since none of that minute's ticks are before it anyway.

    Returns (outcome, exit_time, exit_price_adj) or (None, None, None) if no
    tick coverage exists for that minute, or no qualifying fill is found in
    it (caller falls back to minute-level)."""
    cache_key = (sym, minute_start)
    if cache_key not in _1S_AMBIGUOUS_CACHE:
        ticks = R._ticks_for_window(minute_start, minute_start + pd.Timedelta(minutes=1))
        _1S_AMBIGUOUS_CACHE[cache_key] = None if ticks is None or ticks.empty else ticks
    ticks = _1S_AMBIGUOUS_CACHE[cache_key]
    if ticks is None:
        return None, None, None
    if not_before is not None:
        ticks = ticks.loc[ticks.index >= not_before]
        if ticks.empty:
            return None, None, None
    raw_entry = entry_price_adj - offset
    stop_price = raw_entry - stop if is_long else raw_entry + stop
    target_price = raw_entry + target if is_long else raw_entry - target
    for t, r in ticks.iterrows():
        lo, hi = float(r["Low"]), float(r["High"])
        if is_long:
            if lo <= stop_price:
                return "stop", t, entry_price_adj - stop
            if hi >= target_price and r.AskVolume > 0:
                return "target", t, entry_price_adj + target
        else:
            if hi >= stop_price:
                return "stop", t, entry_price_adj + stop
            if lo <= target_price and r.BidVolume > 0:
                return "target", t, entry_price_adj - target
    return None, None, None


def _resolve_ambiguous_minute(sym, minute_start, offset, entry_price_adj, stop, target, is_long):
    """Fetch real 1s ticks for exactly this one minute and walk them in
    order to find whether stop or target was touched first (only called
    for minutes where the 1-min bar's own H/L range covers BOTH levels).

    NOTE: no longer used by stop_target_grid_1min in this module (which now
    uses _pin_exact_exit for every resolving minute, tie or not, with its
    aggressor-side fill-realism check for targets -- see that function's
    docstring); kept here only because analyze_fta_target.py still calls it
    directly and has not been updated with the same fill-realism check."""
    cache_key = (sym, minute_start)
    if cache_key not in _1S_AMBIGUOUS_CACHE:
        ticks = R._ticks_for_window(minute_start, minute_start + pd.Timedelta(minutes=1))
        _1S_AMBIGUOUS_CACHE[cache_key] = None if ticks is None or ticks.empty else ticks
    ticks = _1S_AMBIGUOUS_CACHE[cache_key]
    if ticks is None:
        return None  # can't resolve -- caller should fall back to the 1min tie-break
    raw_entry = entry_price_adj - offset
    stop_price = raw_entry - stop if is_long else raw_entry + stop
    target_price = raw_entry + target if is_long else raw_entry - target
    for _, r in ticks.iterrows():
        lo, hi = float(r["Low"]), float(r["High"])
        if is_long:
            if lo <= stop_price:
                return "stop"
            if hi >= target_price:
                return "target"
        else:
            if hi >= stop_price:
                return "stop"
            if lo <= target_price:
                return "target"
    return None


def stop_target_grid_1min(trades, series_by_idx, stops, targets):
    """Escalates the actual resolving minute (whichever of stop/target comes
    first, tie or not) to real 1s ticks via _pin_exact_exit -- same
    fill-realism logic as render_stop_target_report.py's resolve_trades:
    TARGET is a resting LIMIT order and only counts with a qualifying
    opposite-side print (stop fills on any side, per user's explicit
    "yes_target_only" choice). If a candidate minute's naive OHLC touch
    turns out to have no qualifying fill (only the "wrong" side traded at
    the target price that minute), keeps scanning forward to the next
    candidate minute instead of crediting a fill that couldn't really have
    happened -- so this no longer needs (or uses) the old
    _resolve_ambiguous_minute tie-break, which lacked that check."""
    rows = []
    n_escalations_total = 0
    n_forced_none_total = 0
    for stop in stops:
        for target in targets:
            r_list = []
            wins = losses = none_hit = escalations = forced_none = 0
            for i, t in enumerate(trades):
                bars = series_by_idx.get(i)
                entry_adj = t["entry"]
                is_long = t["is_long"]
                if bars is None or bars.empty:
                    continue
                touch_time = bars.attrs.get("touch_time")
                offset, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
                raw_entry = entry_adj - offset
                highs, lows = bars["high"].to_numpy(float), bars["low"].to_numpy(float)
                if is_long:
                    stop_price, target_price = raw_entry - stop, raw_entry + target
                    stop_hit = lows <= stop_price
                    target_hit = highs >= target_price
                else:
                    stop_price, target_price = raw_entry + stop, raw_entry - target
                    stop_hit = highs >= stop_price
                    target_hit = lows <= target_price
                s_idx = np.flatnonzero(stop_hit)
                tg_idx = np.flatnonzero(target_hit)

                n_bars_series = len(bars)
                idx = 0
                outcome = None
                had_forced_advance = False
                while True:
                    s_rem = s_idx[s_idx >= idx]
                    tg_rem = tg_idx[tg_idx >= idx]
                    hs = s_rem[0] if s_rem.size else None
                    ht = tg_rem[0] if tg_rem.size else None
                    if hs is None and ht is None:
                        break
                    resolving_idx = min(x for x in (hs, ht) if x is not None)
                    minute_start = bars.index[resolving_idx]
                    o, _et, _ep = _pin_exact_exit(sym, minute_start, offset, entry_adj,
                                                   stop, target, is_long, not_before=touch_time)
                    escalations += 1
                    if o is not None:
                        outcome = o
                        break
                    had_forced_advance = True
                    idx = resolving_idx + 1
                    if idx >= n_bars_series:
                        break

                if outcome is None:
                    none_hit += 1
                    if had_forced_advance:
                        forced_none += 1
                    last = bars["close"].iloc[-1] + offset
                    pnl = (last - entry_adj) if is_long else (entry_adj - last)
                    r_list.append(pnl / stop)
                    continue

                if outcome == "target":
                    wins += 1
                    r_list.append(target / stop)
                else:
                    losses += 1
                    r_list.append(-1.0)
            r_arr = np.array(r_list)
            n = len(r_arr)
            n_escalations_total += escalations
            n_forced_none_total += forced_none
            rows.append({
                "stop": stop, "target": target, "rr": round(target / stop, 2), "n": n,
                "win_rate": wins / n if n else np.nan,
                "avg_R": r_arr.mean() if n else np.nan,
                "total_R": r_arr.sum() if n else np.nan,
                "wins": wins, "losses": losses, "no_hit": none_hit,
                "escalations_1s": escalations, "forced_no_hit": forced_none,
            })
    print(f"Total 1s escalations across whole grid: {n_escalations_total}; "
          f"forced-to-no_hit due to no qualifying fill: {n_forced_none_total}")
    return pd.DataFrame(rows)


def _grid_worker_chunk(args):
    """Top-level (picklable) helper for stop_target_grid_1min_parallel --
    runs the ordinary single-process stop_target_grid_1min on just one
    worker's slice of stop values."""
    trades, series_by_idx, stops_chunk, targets = args
    return stop_target_grid_1min(trades, series_by_idx, stops_chunk, targets)


def stop_target_grid_1min_parallel(trades, series_by_idx, stops, targets, n_workers=4):
    """Same result as stop_target_grid_1min, but splits the (independent)
    stop values across up to n_workers separate OS processes to speed up
    the 1s-tick-escalation work stop_target_grid_1min now does for every
    resolving minute (not just ties -- see that function's docstring),
    which is CPU/IO heavy across the full 9x11 stop/target grid. Each
    worker process builds its own in-memory .scid tick cache (render_
    labels_report._CONTRACT_CACHE) independently -- there is no cross-
    process cache sharing, so this trades some duplicated .scid loading
    for parallel wall-clock speedup. Caller's module MUST be guarded by
    `if __name__ == "__main__":` on Windows (spawn-based multiprocessing
    re-imports the launching script in each worker)."""
    import concurrent.futures as cf
    n_workers = max(1, min(n_workers, len(stops)))
    chunks = [c for c in (stops[i::n_workers] for i in range(n_workers)) if c]
    with cf.ProcessPoolExecutor(max_workers=len(chunks)) as ex:
        results = list(ex.map(_grid_worker_chunk,
                               [(trades, series_by_idx, c, targets) for c in chunks]))
    return pd.concat(results, ignore_index=True)


def chunk_indices_by_contract(trades, n_chunks):
    """Split trade positions into `n_chunks` CONTRACT-PURE, contiguous-in-time
    groups.

    Every trade's tick data comes from whichever ES contract was front-month
    at its retest, and render_labels_report._load_contract holds each one
    (~1.9-2.8GB of .scid) resident for the life of the process. Grouping by
    contract first therefore caps a worker's tick memory at essentially one
    contract, instead of the ~7GB it takes to touch all three of 2026's
    contracts -- which is what an ordinary "split the list into N even
    slices" does to most workers, and what makes naive parallelism over a
    full calendar year exhaust RAM. Chunks are then allocated across the
    contracts proportionally to their trade counts so workers still finish
    at roughly the same time."""
    by_contract = {}
    for i, t in enumerate(trades):
        _off, sym = R._offset_for_ts(pd.Timestamp(t["retest_time"], tz="UTC"))
        by_contract.setdefault(sym, []).append(i)
    groups = [by_contract[k] for k in sorted(by_contract, key=lambda s: by_contract[s][0])]
    n_chunks = max(1, min(n_chunks, len(trades)))
    if len(groups) >= n_chunks:
        return groups

    # Hand the spare workers to the contracts with the most trades
    # (largest-remainder), so no single chunk dominates the wall time.
    splits = [1] * len(groups)
    for _ in range(n_chunks - len(groups)):
        loads = [len(g) / splits[k] for k, g in enumerate(groups)]
        splits[loads.index(max(loads))] += 1
    chunks = []
    for g, k in zip(groups, splits):
        step = len(g) / k
        for c in range(k):
            piece = g[int(round(c * step)):int(round((c + 1) * step))]
            if piece:
                chunks.append(piece)
    return chunks


def _series_worker_chunk(spec):
    """Child-process entry point for build_1min_series_parallel."""
    import pickle
    with open(spec["in_path"], "rb") as f:
        sub_trades = pickle.load(f)
    series = build_or_load_1min_series(sub_trades, cache_path=spec["cache_path"])
    with open(spec["out_path"], "wb") as f:
        pickle.dump(series, f, protocol=pickle.HIGHEST_PROTOCOL)


def build_1min_series_parallel(trades, chunks, cache_paths, work_dir, n_workers=None):
    """build_or_load_1min_series over CONTRACT-PURE chunks of `trades`, in
    parallel, returning the same {global_index: bars} mapping the serial
    call produces.

    `cache_paths[k]` MUST be unique per chunk AND per chunk membership:
    build_or_load_1min_series keys its CSV cache by each trade's position
    within the list it was handed, so two chunks (each re-indexed from 0)
    sharing one file would read each other's bars back and corrupt it --
    exactly what that function's docstring warns about."""
    import pickle
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    os.makedirs(work_dir, exist_ok=True)
    n_workers = n_workers or len(chunks)

    specs = []
    for k, idxs in enumerate(chunks):
        in_path = os.path.join(work_dir, f"series_in_{k:02d}.pkl")
        with open(in_path, "wb") as f:
            pickle.dump([trades[i] for i in idxs], f, protocol=pickle.HIGHEST_PROTOCOL)
        specs.append({"in_path": in_path, "cache_path": cache_paths[k],
                      "out_path": os.path.join(work_dir, f"series_out_{k:02d}.pkl"),
                      "indices": idxs, "k": k})

    result, running, pending, failed = {}, [], list(specs), []
    while pending or running:
        while pending and len(running) < n_workers:
            spec = pending.pop(0)
            p = ctx.Process(target=_series_worker_chunk, args=(spec,), daemon=False)
            p.start()
            print(f"[1min] started chunk{spec['k']:02d} pid={p.pid} "
                  f"({len(spec['indices'])} trades)", flush=True)
            running.append((p, spec))
        time.sleep(2.0)
        for p, spec in list(running):
            if p.is_alive():
                continue
            running.remove((p, spec))
            if p.exitcode != 0 or not os.path.exists(spec["out_path"]):
                failed.append((spec["k"], p.exitcode))
                continue
            with open(spec["out_path"], "rb") as f:
                local = pickle.load(f)
            for j, gi in enumerate(spec["indices"]):
                result[gi] = local.get(j)
            os.remove(spec["out_path"])
            os.remove(spec["in_path"])
            print(f"[1min] finished chunk{spec['k']:02d} "
                  f"({len(result)}/{len(trades)} series)", flush=True)
    if failed:
        raise RuntimeError(f"1min series chunk(s) failed: {failed}")
    return result


def _trade_grid_worker(spec):
    """Child-process entry point for stop_target_grid_1min_trade_parallel:
    runs the ordinary grid over ONE contract-pure slice of trades."""
    import pickle
    with open(spec["in_path"], "rb") as f:
        sub_trades, sub_series = pickle.load(f)
    df = stop_target_grid_1min(sub_trades, sub_series, spec["stops"], spec["targets"])
    with open(spec["out_path"], "wb") as f:
        pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)


def combine_grid_partials(parts):
    """Recombine per-trade-slice grids into the single grid the serial
    stop_target_grid_1min would have produced.

    Valid because every column is either a plain count/sum over independent
    trades (n, wins, losses, no_hit, escalations_1s, forced_no_hit,
    total_R) or a ratio derived from those (win_rate = wins/n, avg_R =
    total_R/n) -- so the ratios are recomputed from the summed parts rather
    than averaged, which would be wrong for unequal slice sizes."""
    all_rows = pd.concat(parts, ignore_index=True)
    sums = ["n", "wins", "losses", "no_hit", "total_R", "escalations_1s", "forced_no_hit"]
    agg = all_rows.groupby(["stop", "target"], as_index=False)[sums].sum()
    agg["rr"] = (agg["target"] / agg["stop"]).round(2)
    agg["win_rate"] = np.where(agg["n"] > 0, agg["wins"] / agg["n"], np.nan)
    agg["avg_R"] = np.where(agg["n"] > 0, agg["total_R"] / agg["n"], np.nan)
    return agg[["stop", "target", "rr", "n", "win_rate", "avg_R", "total_R",
                "wins", "losses", "no_hit", "escalations_1s", "forced_no_hit"]]


def stop_target_grid_1min_trade_parallel(trades, series_by_idx, stops, targets,
                                          chunks, work_dir, n_workers=None):
    """Same result as stop_target_grid_1min, but splits the TRADES (not the
    stop values, as stop_target_grid_1min_parallel does) across processes.

    Splitting by stop value gives every worker the whole trade list, so over
    a multi-contract span each worker ends up loading EVERY contract's .scid
    (~7GB for a full 2026 year, times n_workers). Splitting by contract-pure
    trade chunks instead keeps each worker to ~1 contract, which is what
    makes a full-year grid fit in memory at all. Results are recombined by
    combine_grid_partials."""
    import pickle
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    os.makedirs(work_dir, exist_ok=True)
    n_workers = n_workers or len(chunks)

    specs = []
    for k, idxs in enumerate(chunks):
        sub_trades = [trades[i] for i in idxs]
        sub_series = {j: series_by_idx.get(gi) for j, gi in enumerate(idxs)}
        in_path = os.path.join(work_dir, f"grid_in_{k:02d}.pkl")
        with open(in_path, "wb") as f:
            pickle.dump((sub_trades, sub_series), f, protocol=pickle.HIGHEST_PROTOCOL)
        specs.append({"in_path": in_path, "stops": stops, "targets": targets,
                      "out_path": os.path.join(work_dir, f"grid_out_{k:02d}.pkl"),
                      "k": k, "n": len(idxs)})

    parts, running, pending, failed = [], [], list(specs), []
    while pending or running:
        while pending and len(running) < n_workers:
            spec = pending.pop(0)
            p = ctx.Process(target=_trade_grid_worker, args=(spec,), daemon=False)
            p.start()
            print(f"[grid] started chunk{spec['k']:02d} pid={p.pid} "
                  f"({spec['n']} trades)", flush=True)
            running.append((p, spec))
        time.sleep(2.0)
        for p, spec in list(running):
            if p.is_alive():
                continue
            running.remove((p, spec))
            if p.exitcode != 0 or not os.path.exists(spec["out_path"]):
                failed.append((spec["k"], p.exitcode))
                continue
            with open(spec["out_path"], "rb") as f:
                parts.append(pickle.load(f))
            os.remove(spec["out_path"])
            os.remove(spec["in_path"])
            print(f"[grid] finished chunk{spec['k']:02d} "
                  f"({len(parts)}/{len(specs)} slices)", flush=True)
    if failed:
        raise RuntimeError(f"grid chunk(s) failed: {failed}")
    return combine_grid_partials(parts)


if __name__ == "__main__":
    h1_df, pos_by_ts, retests_df = A.load_strong_breakout_rows()
    strong = retests_df[retests_df["range_ratio"].notna() &
                         (retests_df["range_ratio"] >= A.R.WIDE_BREAKOUT_RATIO_THRESHOLD)]
    trades = A.simulate(h1_df, pos_by_ts, strong)
    print(f"Strong breakout trades: {len(trades)}")

    series_by_idx = build_or_load_1min_series(trades)
    missing_idx = [i for i, v in series_by_idx.items() if v is None]
    n_missing = len(missing_idx)
    print(f"1min series built/loaded for {len(series_by_idx) - n_missing}/{len(trades)} trades "
          f"({n_missing} missing tick coverage)")
    if missing_idx:
        print("Missing-coverage retest times (no real ticks found for that window):")
        for i in missing_idx:
            print(f"  idx={i} retest_time={trades[i]['retest_time']}")

    grid_1min = stop_target_grid_1min(trades, series_by_idx, STOPS, TARGETS)

    print("\n=== 1-MIN-RESOLVED stop/target grid (top by total_R) ===")
    print(grid_1min.sort_values("total_R", ascending=False).head(15).to_string(index=False))

    # Apples-to-apples: H1-bar-walk grid restricted to ONLY the trades that
    # also have real 1-min tick coverage (same sample as grid_1min), so the
    # comparison isolates the resolution-method effect from any sample-size
    # difference caused by tick-data gaps.
    covered_idx = [i for i in range(len(trades)) if series_by_idx.get(i) is not None]
    covered_trades = [trades[i] for i in covered_idx]
    print(f"\n=== Apples-to-apples comparison (same {len(covered_trades)}-trade subset both ways) ===")
    h1_grid_subset = A.stop_target_grid(covered_trades, STOPS, TARGETS)
    merged = h1_grid_subset.merge(grid_1min, on=["stop", "target"], suffixes=("_h1", "_1min"))
    merged["avg_R_delta"] = merged["avg_R_1min"] - merged["avg_R_h1"]
    print(merged[["stop", "target", "n_h1", "win_rate_h1", "avg_R_h1", "total_R_h1",
                  "win_rate_1min", "avg_R_1min", "total_R_1min", "avg_R_delta",
                  "ambiguous_minutes", "unresolved_ties"]]
          .sort_values("avg_R_delta").head(20).to_string(index=False))
