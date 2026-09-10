r"""
LXPB level ledger + on-disk cache (H1 and M5)
=============================================

CONVENTION -- continuous contracts only (see CLAUDE.md): every level ledger
built here comes from ONE continuous, back-adjusted series spanning every
contract rollover, sourced from TradingView's own continuous exports and
nothing else. Both h1_levels() (one run over R._display_h1()) and
m5_levels() (one run over m5_bars_continuous()) follow this, and neither
takes a contract or segment argument -- there is one ledger per timeframe,
not one per contract.

Raw .scid data is never resampled into H1 or M5 bars here, not even to fill
a hole an export doesn't cover: where the exports stop, the ledger stops.
.scid remains the source for second- and tick-level work (fills, exits,
footprints, the 1s/1min panes), where prices are mapped ONTO this scale via
R._offset_for_ts and never the other way round.

A continuous series with a large price jump at a rollover boundary means the
back-adjustment/splice is wrong (R._assert_no_roll_gaps checks for exactly
this) -- fix the splice, never fall back to per-contract data.

Every consumer in this repo that needs LXPB levels currently re-runs
`lxpb.detect_lxpb_h1` from scratch, and that function only ever reports the
state machine's THREE END-OF-DATA BUCKETS:

    touch_lv0  formed, still unbroken   at the last bar it was handed
    touch_lv1  broken, awaiting retest  at the last bar it was handed
    retests    completed retests        (i.e. levels that are now DEAD)

Two things make that a poor basis for anything except "give me the trade
population":

1. **It hides most levels.** The machine registers two levels per bar (the
   bar's high as an LHPB, its low as an LLPB) and then *silently* drops any
   that (a) get traded into but not closed through (phase 2), or (b) get
   touched/gapped past before MIN_HOURS_BEFORE_RETEST has elapsed (phase 3).
   Those levels appear in none of the three buckets. Over 2024-10..2026-09 H1
   that is 17,676 of 22,518 levels -- 78% of them.

2. **The buckets are a snapshot, not a history.** They describe the last bar
   only, so answering "which levels existed at time T?" means re-running the
   whole machine over bars[:T] -- which is what `render_stop_target_report`
   had to do per trade, and getting it wrong (running it over a window that
   extended past the trade) is what caused dead levels to be drawn on the M5
   pane as though they were live.

This module records the FULL LIFECYCLE of every level exactly once and caches
it, so both problems go away:

    formation_time -> breakout_time -> retest_time
                   \-> death_time + fate

A level is live over the half-open interval ``[formation_time, death_time)``,
so "which levels were live at T" becomes a vectorised interval filter
(`levels_live_as_of`) instead of an O(bars) replay. The ledger is a superset
of the three buckets, so anything the old API could answer, this can too.

`fate` is one of:

    retested             reached a valid retest; death_time == retest_time
    consumed_early       touched/gapped past before MIN_HOURS_BEFORE_RETEST
    discarded_no_close   bar traded into the level but did not close through
    gated_dropped        closed through, but failed lxpb.py's candidate gate
                         (see advance_one_bar's own docstring for the full
                         rule set: is_spike, or is_swing AND consolidating)
                         -- not a real pre-breakout extreme, so never
                         tracked as a P0
    open_unbroken        still in touch_lv0 at the end of the data
    open_awaiting_retest still in touch_lv1 at the end of the data

How the lifecycle is captured
-----------------------------
By OBSERVING `lxpb.advance_one_bar` from the outside, never by
re-implementing it. `lxpb.py` is a synced copy of a canonical file kept in
D:\\daily-analysis, so forking its rules here would silently drift from the
source of truth (and `lxpb_fade_research_step1_levels.py` already shows how
easy that is to do by accident). Instead this module diffs the state's two
live lists after each bar.

That is exact, not heuristic, because of two invariants of `advance_one_bar`:

  * survivors are carried over by reference (`keep.append(lv)`), so object
    identity distinguishes "same level" from "new level"; and
  * new entries are only ever appended at the END of each list (phase 2
    appends to touch_lv1 after phase 3 has rebuilt it; phase 1 appends to
    touch_lv0 after phase 2 has rebuilt it).

So a two-pointer walk over (previous list, current list) by identity yields
exactly the levels that left each list on this bar, and each departure is
classified by where it went (see `_LedgerObserver.observe`).

Both lists stay small -- H1 peaks at |lv0|=247, |lv1|=152 -- so the walk costs
about as much as the state machine itself.

Keeping the cache current
-------------------------
The source data changes in three distinct ways, and a full rebuild is the right
answer to only one of them:

1. **Rollover / re-anchor.** A contract rollover (or any TradingView re-export)
   re-anchors back-adjusted history: every price moves by a constant, but not a
   single tick of market structure changes. The state machine is *shift
   invariant* -- every test in it is a comparison between prices or a ratio of
   price differences, with no absolute thresholds -- so the whole lifecycle
   (formation, breakout, retest, death times, swing/spike flags, fates) is
   provably identical and only the 12 price columns move. This is handled by
   re-anchoring the cached ledger in place (~10 ms) instead of rebuilding.

   The ledger is therefore stored in the **scale it was built in**
   (`state_anchor` in the meta), and `h1_levels()` / `m5_levels()` shift it to
   whatever scale the current bars are on before returning. Callers always get
   prices matching today's charts.

2. **New bars appended.** Levels that were still open at the end of the old data
   can later break, retest or die, and new levels form. Rows that already
   reached a terminal fate can never change again, so the ledger is extended
   rather than rebuilt: the machine's state is persisted next to the ledger and
   *resumed* on the new bars. Only the previously-open rows are recomputed.

3. **History changed.** A revised or backfilled bar anywhere in the existing
   range invalidates everything downstream of it, so this does force a rebuild.

`_reconcile` picks between these by comparing the incoming bars against the meta:
an exact content match is a hit; a match after removing a constant shift is a
re-anchor; a matching *prefix* is an extend; anything else is a rebuild. A change
to `ALGO_VERSION` or to `lxpb.py`'s rules fingerprint forces a rebuild outright.

Usage
-----
    import lxpb_levels_cache as LC

    h1 = LC.h1_levels()                     # whole merged H1 series
    m5 = LC.m5_levels()                     # whole merged M5 series (every contract)

    live = LC.levels_live_as_of(m5, ts, level_type="LLPB",
                                near_price=7568.0, near_pts=20.0)

CLI
---
    python lxpb_levels_cache.py --build-h1
    python lxpb_levels_cache.py --build-m5
    python lxpb_levels_cache.py --stats
"""

import os
import sys
import json
import time
import pickle
import hashlib
import argparse

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_labels_report as R  # noqa: E402

L = R.L

# Bump when the ledger's SCHEMA or the way it is derived changes, so every
# cached file rebuilds. (Changes to lxpb.py's own rules are picked up via
# RULES_FINGERPRINT below instead.)
ALGO_VERSION = 3

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "levels_cache")

FATE_RETESTED = "retested"
FATE_CONSUMED_EARLY = "consumed_early"
FATE_DISCARDED_NO_CLOSE = "discarded_no_close"
FATE_GATED_DROPPED = "gated_dropped"
FATE_OPEN_UNBROKEN = "open_unbroken"
FATE_OPEN_AWAITING_RETEST = "open_awaiting_retest"

# Levels that are still alive at the end of the data have no death_time.
_OPEN_FATES = (FATE_OPEN_UNBROKEN, FATE_OPEN_AWAITING_RETEST)

COLUMNS = [
    "timeframe", "contract", "type", "price",
    "formation_time", "is_spike", "is_swing",
    "breakout_time", "breakout_open", "breakout_high", "breakout_low", "breakout_close",
    "er_score",
    "retest_time", "retest_open", "retest_high", "retest_low", "retest_close",
    "entry_price", "fta", "stop_loss",
    "death_time", "fate",
]

_TIME_COLS = ("formation_time", "breakout_time", "retest_time", "death_time")

# Every column that carries a PRICE, and therefore moves by the back-adjustment
# delta when history is re-anchored. Verified empirically: shifting all input
# OHLC by a constant leaves every other column byte-identical and moves exactly
# these by the same constant.
PRICE_COLS = (
    "price", "breakout_open", "breakout_high", "breakout_low", "breakout_close",
    "retest_open", "retest_high", "retest_low", "retest_close",
    "entry_price", "fta", "stop_loss",
)


def _rules_fingerprint():
    """Hash of lxpb.py's source + MIN_HOURS_BEFORE_RETEST.

    lxpb.py is a synced copy of a file maintained elsewhere, so it can change
    under us. Keying on its bytes means a re-sync that alters the detection
    rules invalidates every cached ledger instead of silently serving levels
    built by the old rules."""
    src = open(L.__file__, "rb").read()
    h = hashlib.sha1(src).hexdigest()[:12]
    return f"{h}-mh{L.MIN_HOURS_BEFORE_RETEST}"


def _bars_fingerprint(bars):
    """Content hash of an OHLC frame: every timestamp and every OHLC value.

    This deliberately hashes the WHOLE frame rather than sampling head/tail +
    row count. Sampling is tempting because it is O(1), but it cannot see a
    mid-series revision -- a corrected .scid tick or a backfilled gap that
    leaves the bar count and both endpoints untouched -- and would then serve
    a ledger built from superseded prices with no indication anything was
    wrong. Hashing everything costs ~2 ms for the H1 series and ~20 ms for a
    contract's M5 bars, against a 7-45 s rebuild, so the O(n) scan is free in
    practice and removes the failure mode entirely."""
    n = len(bars)
    if n == 0:
        return "empty"
    h = hashlib.sha1(bars.index.values.tobytes())
    h.update(bars[["open", "high", "low", "close"]].to_numpy("float64").tobytes())
    return f"{n}-{h.hexdigest()[:16]}"


def _anchor(bars):
    """The price the shape fingerprint is measured against."""
    return float(bars["close"].iloc[0])


def _shape_fingerprint(bars):
    """Shift-INVARIANT hash: the same bars re-anchored give the same value.

    Back-adjustment only ever adds a constant to a whole series, so hashing
    prices relative to the first close identifies market structure independently
    of which contract is currently front. That is what lets a rollover be
    recognised as 'same data, new scale' and repaired by shifting the cached
    ledger, instead of being mistaken for new data and rebuilt."""
    n = len(bars)
    if n == 0:
        return "empty"
    rel = bars[["open", "high", "low", "close"]].to_numpy("float64") - _anchor(bars)
    # Round before hashing: re-anchoring is float arithmetic, so the same bar
    # can differ in the last ulp depending on the offset applied. ES ticks are
    # 0.25, so 4dp is far finer than the data and immune to that noise.
    h = hashlib.sha1(bars.index.values.tobytes())
    h.update(np.round(rel, 4).tobytes())
    return f"{n}-{h.hexdigest()[:16]}"


def _reanchor(df, delta):
    """Move a ledger onto a price scale `delta` points away. See PRICE_COLS."""
    if not delta:
        return df
    out = df.copy()
    for c in PRICE_COLS:
        out[c] = out[c] + delta
    return out


# --------------------------------------------------------------------------
# Lifecycle capture
# --------------------------------------------------------------------------

def _departed(prev, cur):
    """Objects present in list `prev` but no longer in list `cur`, by identity.

    Relies on advance_one_bar's two invariants (survivors keep identity and
    keep relative order; new entries are appended at the end), which makes a
    single two-pointer walk exact and O(len(prev))."""
    out = []
    i = j = 0
    n_prev, n_cur = len(prev), len(cur)
    while i < n_prev:
        if j < n_cur and prev[i] is cur[j]:
            i += 1
            j += 1
        else:
            out.append(prev[i])
            i += 1
    return out


class _LedgerObserver:
    """Watches a live lxpb state and records every level's full lifecycle."""

    def __init__(self):
        self.rows = {}          # (type, formation_time) -> dict
        self._prev_lv0 = []
        self._prev_lv1 = []
        self._n_retests = 0

    @staticmethod
    def _key(lv):
        return (lv["type"], lv["formation_time"])

    def _base_row(self, lv):
        return {
            "type": lv["type"],
            "price": float(lv["price"]),
            "formation_time": lv["formation_time"],
            "is_spike": bool(lv["is_spike"]),
            # is_swing is finalized on the bar AFTER formation; a level can
            # never die before then, so by the time we read it here it is
            # always a real bool rather than the pending None.
            "is_swing": bool(lv["is_swing"]) if lv["is_swing"] is not None else False,
            "breakout_time": pd.NaT, "breakout_open": np.nan, "breakout_high": np.nan,
            "breakout_low": np.nan, "breakout_close": np.nan, "er_score": np.nan,
            "retest_time": pd.NaT, "retest_open": np.nan, "retest_high": np.nan,
            "retest_low": np.nan, "retest_close": np.nan,
            "entry_price": np.nan, "fta": np.nan, "stop_loss": np.nan,
            "death_time": pd.NaT, "fate": None,
        }

    def _row_for(self, lv):
        k = self._key(lv)
        row = self.rows.get(k)
        if row is None:
            row = self.rows[k] = self._base_row(lv)
        return row

    @staticmethod
    def _apply_breakout(row, lv):
        row["breakout_time"] = lv["breakout_time"]
        row["breakout_open"] = lv["breakout_open"]
        row["breakout_high"] = lv["breakout_high"]
        row["breakout_low"] = lv["breakout_low"]
        row["breakout_close"] = lv["breakout_close"]
        # The candidate gate's efficiency-ratio score at the moment this
        # level broke out (see lxpb.advance_one_bar's own docstring),
        # computed regardless of which rule actually passed it (is_spike
        # included) -- useful for reviewing/retuning ER_CONSOLIDATION_MAX
        # later even on levels a different rule let through. NaN if
        # lxpb.py had too few closes to score it at all (.get is
        # defensive; every fresh build always sets the key).
        er = lv.get("er_score")
        row["er_score"] = er if er is not None else np.nan

    def apply_finalized_swing(self, finalized_swing):
        """Patch the real is_swing value into an already-emitted row.

        `_row_for`/`_base_row` snapshot a level's fields the moment it is
        FIRST observed -- for a brand-new level that's its own formation
        bar, when is_swing is still the pending `None` (coerced to
        False there). `lxpb.advance_one_bar` finalizes is_swing one bar
        later and hands back exactly the levels it just finalized
        (`finalized_swing`) so this is the only place the real value
        ever gets written into the row that's already sitting in
        self.rows."""
        for lv in finalized_swing:
            row = self.rows.get(self._key(lv))
            if row is not None:
                row["is_swing"] = bool(lv["is_swing"])

    def observe(self, state, bar_time, gated_dropped=()):
        """Record everything that happened to levels on the bar just processed."""
        lv0, lv1, retests = state["touch_lv0"], state["touch_lv1"], state["retests"]
        gated_by_key = {self._key(lv): lv for lv in gated_dropped}

        # --- completed retests (phase 3): the level's terminal state ---
        retested_keys = set()
        for r in retests[self._n_retests:]:
            k = (r["type"], r["formation_time"])
            retested_keys.add(k)
            row = self.rows.get(k)
            if row is None:
                # Only possible if a level formed, broke and retested without
                # ever being observed in a list snapshot -- can't happen given
                # phase ordering, but stay total rather than KeyError.
                row = self.rows[k] = self._base_row(r)
            self._apply_breakout(row, r)
            for f in ("retest_time", "retest_open", "retest_high", "retest_low",
                      "retest_close", "entry_price", "fta", "stop_loss"):
                row[f] = r[f]
            row["death_time"] = r["retest_time"]
            row["fate"] = FATE_RETESTED
        self._n_retests = len(retests)

        # --- levels that left touch_lv1: retested above, else consumed early ---
        for lv in _departed(self._prev_lv1, lv1):
            k = self._key(lv)
            if k in retested_keys:
                continue
            row = self._row_for(lv)
            self._apply_breakout(row, lv)
            row["death_time"] = bar_time
            row["fate"] = FATE_CONSUMED_EARLY

        # --- levels that left touch_lv0: broke out, else discarded ---
        # A level that broke out on this bar is re-added to touch_lv1 as a NEW
        # dict, so identity is lost across that hop -- match on the key, and
        # only against entries stamped with THIS bar's breakout_time.
        broke_now = {}
        for lv in lv1:
            if lv["breakout_time"] == bar_time:
                broke_now[self._key(lv)] = lv
        for lv in _departed(self._prev_lv0, lv0):
            k = self._key(lv)
            row = self._row_for(lv)
            hit = broke_now.get(k)
            gated = gated_by_key.get(k)
            if hit is not None:
                self._apply_breakout(row, hit)
            elif gated is not None:
                self._apply_breakout(row, gated)
                row["death_time"] = bar_time
                row["fate"] = FATE_GATED_DROPPED
            else:
                row["death_time"] = bar_time
                row["fate"] = FATE_DISCARDED_NO_CLOSE

        # --- newly registered levels (phase 1) ---
        for lv in lv0:
            if lv["formation_time"] == bar_time:
                self._row_for(lv)

        self._prev_lv0 = list(lv0)
        self._prev_lv1 = list(lv1)

    def finish(self, state):
        """Stamp the levels that were still alive when the data ran out."""
        for lv in state["touch_lv0"]:
            row = self._row_for(lv)
            row["is_swing"] = bool(lv["is_swing"]) if lv["is_swing"] is not None else False
            row["fate"] = FATE_OPEN_UNBROKEN
        for lv in state["touch_lv1"]:
            row = self._row_for(lv)
            self._apply_breakout(row, lv)
            row["fate"] = FATE_OPEN_AWAITING_RETEST
        return self.rows


def build_ledger(bars, timeframe, contract="", resume=None):
    """Run the canonical state machine over `bars` and return the full ledger.

    `bars` must be the same shape lxpb expects: a DatetimeIndex with
    open/high/low/close columns, in whatever price scale you want the levels
    expressed in (this module always hands it display/back-adjusted prices).

    Pass `resume` -- a blob from a previous call's `resume_blob()` -- to CONTINUE
    a ledger over newly appended bars instead of replaying from bar zero. The
    caller is responsible for having verified that `bars` are exactly the bars
    that follow the ones the blob was built from, and on the same price scale.
    Returns `(df, blob)`; `df` covers only the levels this call knows about, so
    a resumed call returns the previously-open levels plus any new ones, and the
    caller concatenates it with the already-terminal rows it kept."""
    if resume is None:
        state, obs = L.new_state(), _LedgerObserver()
    else:
        state, obs = resume

    for bar in bars.itertuples(index=True):
        finalized_swing, gated_dropped = L.advance_one_bar(state, bar)
        obs.apply_finalized_swing(finalized_swing)
        obs.observe(state, bar.Index, gated_dropped)

    # Snapshot BEFORE finish(), which stamps terminal fates onto the rows of
    # levels that are merely still open -- those must stay open in the blob so a
    # later resume can carry them forward.
    blob = _resume_blob(state, obs)

    rows = obs.finish(state)

    df = pd.DataFrame(list(rows.values()))
    if df.empty:
        df = pd.DataFrame(columns=[c for c in COLUMNS if c not in ("timeframe", "contract")])
    df.insert(0, "contract", contract)
    df.insert(0, "timeframe", timeframe)
    for c in _TIME_COLS:
        df[c] = pd.to_datetime(df[c], utc=True)
    df = df.sort_values(["formation_time", "type"]).reset_index(drop=True)
    return df[COLUMNS], blob


def _resume_blob(state, obs):
    """Everything needed to continue this ledger on later bars.

    Pickled as ONE object graph on purpose: the observer tracks levels by object
    identity, and its `_prev_lv0` / `_prev_lv1` hold the very same dicts as
    `state["touch_lv0"]` / `["touch_lv1"]`. Pickling them together preserves
    that sharing; pickling them separately would silently break the identity
    walk and make every level look like it departed on the first resumed bar.

    Two things are dropped to keep the blob small and the resume honest:
    `state["retests"]` (append-only in lxpb, and already in the ledger) and every
    observer row that has reached a terminal fate (it can never change again, and
    lives in the parquet)."""
    live_keys = {obs._key(lv) for lv in state["touch_lv0"]}
    live_keys |= {obs._key(lv) for lv in state["touch_lv1"]}

    slim = _LedgerObserver()
    slim.rows = {k: v for k, v in obs.rows.items() if k in live_keys}
    slim._prev_lv0 = obs._prev_lv0
    slim._prev_lv1 = obs._prev_lv1
    slim._n_retests = 0

    slim_state = dict(state)
    slim_state["retests"] = []
    return pickle.dumps((slim_state, slim), protocol=pickle.HIGHEST_PROTOCOL)


# --------------------------------------------------------------------------
# M5 bars -- the one continuous TradingView series
# --------------------------------------------------------------------------

_M5_BARS_CONTINUOUS = None


def m5_bars_continuous():
    """The M5 equivalent of R._display_h1(): one continuous, back-adjusted
    series spanning every rollover, straight from TradingView's own ES1! M5
    exports (R._display_m5()) and nothing else.

    There is deliberately no .scid fallback. Resampling a contract's raw
    ticks into M5 bars and shifting them by a measured per-segment constant
    looks equivalent, but it is a different vendor's feed joined to
    TradingView's at an arbitrary date, and that constant is one average for
    a whole segment, so it carries a few points of error near a roll. Where
    the exports stop, the series stops -- see the "continuous contracts only"
    convention in CLAUDE.md. R._display_m5 reports any hole it does contain,
    and validates both the splice at every roll and the scale against the H1
    series, before any of this is handed to the state machine.

    Cached per process, though R._display_m5 is itself cached: this exists as
    a named entry point so callers say which series they mean rather than
    reaching into a display helper."""
    global _M5_BARS_CONTINUOUS
    if _M5_BARS_CONTINUOUS is None:
        _M5_BARS_CONTINUOUS = R._display_m5()
    return _M5_BARS_CONTINUOUS


# --------------------------------------------------------------------------
# On-disk cache
# --------------------------------------------------------------------------

def _cache_paths(name):
    """Stable paths -- one live file per ledger, overwritten in place.

    Deliberately NOT content-addressed. Keying the filename on the data would
    mean every refresh wrote a new file and orphaned the old one, and -- far
    worse -- the reconcile step could never FIND the previous ledger to extend
    or re-anchor it, so every update would degrade into a full rebuild. The
    fingerprints live in the sidecar meta instead."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    base = os.path.join(CACHE_DIR, name)
    return base + ".parquet", base + ".json", base + ".state.pkl"


def _load_cached(name):
    """Return `(df, meta, blob)` for the cached ledger, or `(None, None, None)`."""
    pq, js, pk = _cache_paths(name)
    if not (os.path.exists(pq) and os.path.exists(js)):
        return None, None, None
    try:
        meta = json.load(open(js, encoding="utf-8"))
        df = pd.read_parquet(pq)
    except (ValueError, OSError):
        return None, None, None
    for c in _TIME_COLS:
        df[c] = pd.to_datetime(df[c], utc=True)
    blob = None
    if os.path.exists(pk):
        try:
            with open(pk, "rb") as f:
                blob = f.read()
        except OSError:
            blob = None
    return df, meta, blob


def _save_cached(name, df, meta, blob):
    pq, js, pk = _cache_paths(name)
    df.to_parquet(pq, index=False)
    with open(js, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)
    if blob is None:
        if os.path.exists(pk):
            os.remove(pk)
    else:
        with open(pk, "wb") as f:
            f.write(blob)
    return pq


def _reconcile(name, bars, kind, contract, rebuild, verbose):
    """Bring the cached ledger up to date with `bars` by the cheapest route.

    Returns `(df_in_state_scale, meta, blob)`. The ledger is kept in the scale
    the machine ran in; `state_anchor` records which that is, and the public
    loaders shift it to the current scale on the way out. Keeping the stored
    ledger scale-stable is what makes a rollover a metadata event rather than a
    recompute, and it means the persisted machine state never has to be
    rewritten (its prices stay valid in its own scale forever)."""
    rules = _rules_fingerprint()
    n = len(bars)
    now_fp, now_shape, now_anchor = (_bars_fingerprint(bars), _shape_fingerprint(bars),
                                     _anchor(bars))

    cached, meta, blob = (None, None, None) if rebuild else _load_cached(name)
    how, resume, work = "rebuild", None, bars

    if cached is not None and meta is not None \
            and meta.get("algo_version") == ALGO_VERSION and meta.get("rules") == rules:
        n_old = int(meta.get("n_bars", 0))
        if n_old <= n:
            prefix = bars if n_old == n else bars.iloc[:n_old]
            state_anchor = float(meta.get("state_anchor", 0.0))
            if _bars_fingerprint(prefix) == meta.get("bars_fingerprint") \
                    or _shape_fingerprint(prefix) == meta.get("shape_fingerprint"):
                if n_old == n:
                    resolved = "hit" if now_fp == meta.get("bars_fingerprint") \
                        else "reanchor"
                    return cached, dict(meta, anchor=now_anchor, resolved=resolved), blob
                if blob is not None:
                    how = "extend"
                    resume = pickle.loads(blob)
                    # New bars arrive on today's scale; the resumed machine
                    # thinks in its own. Shifting the few new bars is exact and
                    # keeps every price already inside the state untouched --
                    # which is what makes rollover-plus-append safe to combine.
                    to_state = state_anchor - now_anchor
                    work = bars.iloc[n_old:]
                    if to_state:
                        work = work.assign(**{c: work[c] + to_state
                                              for c in ("open", "high", "low", "close")})

    t0 = time.time()
    if how == "extend":
        keep = cached[~cached["fate"].isin(_OPEN_FATES)]
        fresh, blob = build_ledger(work, kind, contract, resume=resume)
        df = pd.concat([keep, fresh], ignore_index=True)
        df = df.sort_values(["formation_time", "type"]).reset_index(drop=True)[COLUMNS]
        state_anchor = float(meta.get("state_anchor", 0.0))
    else:
        df, blob = build_ledger(bars, kind, contract)
        state_anchor = now_anchor

    meta = {"kind": kind.lower(), "contract": contract, "algo_version": ALGO_VERSION,
            "rules": rules, "bars_fingerprint": now_fp, "shape_fingerprint": now_shape,
            "anchor": now_anchor, "state_anchor": state_anchor, "n_bars": int(n),
            "first_bar": str(bars.index[0]), "last_bar": str(bars.index[-1]),
            "n_levels": int(len(df)), "built_how": how, "resolved": how,
            "built_seconds": round(time.time() - t0, 2),
            "built_at": pd.Timestamp.utcnow().isoformat()}
    _save_cached(name, df, meta, blob)
    if verbose:
        what = "extended" if how == "extend" else "built"
        extra = f" (+{len(work):,} new bars)" if how == "extend" else ""
        print(f"  [levels] {what} {kind} ledger{' ' + contract if contract else ''}: "
              f"{len(df):,} levels from {n:,} bars{extra} in "
              f"{meta['built_seconds']}s", flush=True)
    return df, meta, blob


def _to_current_scale(df, meta, bars, verbose, label):
    """Shift a stored (state-scale) ledger onto the scale `bars` are on."""
    delta = _anchor(bars) - float(meta.get("state_anchor", 0.0))
    if abs(delta) < 1e-9:
        return df
    if verbose:
        print(f"  [levels] re-anchored {label} ledger by {delta:+.2f} pts "
              f"(rollover / re-export; structure unchanged)", flush=True)
    return _reanchor(df, delta)


def h1_levels(bars=None, rebuild=False, verbose=True):
    """Full H1 level ledger for the merged display H1 series.

    The bars come from R._display_h1() -- the single source of truth for the
    TradingView H1 exports -- so this ledger is on exactly the same price
    scale as the H1 charts and as `row["price"]` in the trade reports. This
    is the model every timeframe's level detection should follow: ONE
    continuous, back-adjusted series (validated gap-free at every roll by
    R._assert_no_roll_gaps), ONE state-machine run over the whole thing --
    see the "continuous contracts only" convention in CLAUDE.md."""
    if bars is None:
        bars = R._display_h1()
    df, meta, _ = _reconcile("h1_levels", bars, "H1", "", rebuild, verbose)
    return _to_current_scale(df, meta, bars, verbose, "H1")


def m5_levels(rebuild=False, verbose=True):
    """Full M5 level ledger over the continuous, back-adjusted, gap-free
    series spanning every contract (m5_bars_continuous) -- one
    state-machine run over the whole thing, same model as h1_levels. A
    level formed on one contract CAN be retested by a later contract's
    bars, exactly like a real trader watching one continuous chart would
    expect (see the "continuous contracts only" convention in CLAUDE.md;
    this replaced a per-contract-segment version that could not do that --
    [[feedback-continuous-contracts-only]] in project memory).

    Survives a rollover without recomputing: the stored ledger is shifted onto
    the current back-adjustment scale rather than rebuilt, because that shift is
    provably all that changes (see the module docstring)."""
    bars = m5_bars_continuous()
    if bars is None or bars.empty:
        return None
    df, meta, _ = _reconcile("m5_levels_continuous", bars, "M5", "", rebuild, verbose)
    return _to_current_scale(df, meta, bars, verbose, "M5")


def m5_levels_for_ts(ts, **kw):
    """Convenience/backward-compatible name: the one continuous M5 ledger.
    `ts` is accepted but unused -- kept so existing call sites that ask
    "the M5 ledger relevant to this timestamp" don't need to change; there
    is now only ever one M5 ledger, not one per contract."""
    return m5_levels(**kw)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------

def levels_live_as_of(ledger, as_of, level_type=None, near_price=None,
                      near_pts=None, formed_by=None):
    """Levels that were alive at `as_of`, with the `stage` they were in then.

    "Alive at T" is the half-open interval [formation_time, death_time): a
    level exists from the bar that registered it until the bar that consumed
    it, and a level consumed exactly at T is NOT alive at T. `stage` is
    "broken" once breakout_time <= T, else "formed" -- i.e. what the old
    touch_lv1 / touch_lv0 buckets would have said had the machine been
    stopped at T, which is what makes this a drop-in replacement for
    re-running it.

    Optional filters:
      level_type   "LHPB" / "LLPB"
      formed_by    keep only levels with formation_time <= this
      near_price + near_pts   keep only levels within +/- near_pts, and add a
                              `dist` column sorted ascending
    """
    as_of = pd.Timestamp(as_of, tz="UTC") if pd.Timestamp(as_of).tzinfo is None \
        else pd.Timestamp(as_of)
    d = ledger
    if level_type is not None:
        d = d[d["type"] == level_type]
    alive = (d["formation_time"] <= as_of) & (d["death_time"].isna() | (d["death_time"] > as_of))
    d = d[alive]
    if formed_by is not None:
        formed_by = pd.Timestamp(formed_by)
        if formed_by.tzinfo is None:
            formed_by = formed_by.tz_localize("UTC")
        d = d[d["formation_time"] <= formed_by]
    d = d.copy()
    d["stage"] = np.where(d["breakout_time"].notna() & (d["breakout_time"] <= as_of),
                          "broken", "formed")
    if near_price is not None and near_pts is not None:
        d["dist"] = (d["price"] - float(near_price)).abs()
        d = d[d["dist"] <= float(near_pts)].sort_values("dist")
    return d.reset_index(drop=True)


def retests(ledger):
    """The completed-retest rows -- the equivalent of detect_lxpb_h1()[2]."""
    return ledger[ledger["fate"] == FATE_RETESTED].reset_index(drop=True)


def _as_utc(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def find_confluent_levels(ledger, level_type, price, formation_time, cutoff_time,
                          n_points, min_formation_time=None):
    """"Confluence" strategy query: other LXPB levels within +/- n_points of
    `price` that could support pairing with the subject level (type
    `level_type`, formed at `formation_time`).

    A candidate qualifies if it was:
      - formed strictly before `cutoff_time` (the subject trade's own retest
        -- the decision point; nothing after it may be used), AND
      - formed at or after `min_formation_time` when given -- bounds how far
        back "confluence" may reach; omit (default) for no lower bound. The
        reports (render_stop_target_report.py /
        render_ss_confl_finetune_report.py) both omit it -- an earlier
        CONFLUENCE_LOOKBACK_BARS=100-bar bound there wrongly excluded
        genuinely live, still-standing structure formed further back (see
        render_stop_target_report.py's CONFLUENCE_N_POINTS comment). Kept
        as an optional param for exploration scripts (e.g.
        analyze_confluent_levels.py's own --lookback-bars) that want one, AND
      - given a CONFIRMED breakout at or before `cutoff_time`: breakout_time
        not null and <= cutoff_time.

    That excludes exactly the two "no real breakout" fates:
      - discarded_no_close: price wicked through but the bar's CLOSE didn't
        confirm -- a failed breakout (for an LLPB: wick below, close above;
        mirrored close-below-a-high for LHPB).
      - open_unbroken: never even broken out.
    The remaining fates (retested / consumed_early / open_awaiting_retest)
    all have a real, confirmed breakout_time, i.e. "a successful break did
    happen" -- whether or not the level was ever itself later retested is
    irrelevant to whether it counts as confluence.

    Excludes the subject level's own ledger row (matched by
    type+price+formation_time). Sorted most-recently-formed first, so
    `.iloc[0]` (if any rows exist) is the "recent nearby structure" a
    confluence strategy would pair the subject level with.
    """
    cutoff_time = _as_utc(cutoff_time)
    formation_time = _as_utc(formation_time)
    zone_lo, zone_hi = price - n_points, price + n_points

    keep = ((ledger["price"] >= zone_lo) & (ledger["price"] <= zone_hi) &
            (ledger["formation_time"] < cutoff_time) &
            ledger["breakout_time"].notna() &
            (ledger["breakout_time"] <= cutoff_time))
    if min_formation_time is not None:
        keep &= ledger["formation_time"] >= _as_utc(min_formation_time)
    cand = ledger[keep].copy()
    is_self = ((cand["type"] == level_type) &
               (cand["price"].round(2) == round(price, 2)) &
               (cand["formation_time"] == formation_time))
    cand = cand[~is_self]
    cand["dist"] = (cand["price"] - price).abs()
    return cand.sort_values("formation_time", ascending=False).reset_index(drop=True)


def same_side_live_confluence(confluent, level_type, breakout_time):
    """Narrower reading of the same confluence strategy: restrict `confluent`
    (see find_confluent_levels) to levels on the SAME side as the subject
    trade -- SAME type, not "same-or-opposite" (an LHPB retest for a long
    only counts nearby LHPB structure, an LLPB retest for a short only counts
    nearby LLPB structure) -- that had NOT yet been retested as of the
    subject's own P1 (breakout) bar.

    A same-type candidate qualifies here if death_time is null (still
    open_awaiting_retest, i.e. never retested at all) or its death_time is
    AFTER `breakout_time` -- i.e. it was still live, competing/reinforcing
    structure at the moment the subject level's own breakout confirmed,
    rather than already-resolved history by then. Note this is a stricter
    "as of P1" liveness check than find_confluent_levels' own filtering
    (which only cares that the candidate's breakout was confirmed by the
    subject's retest/P2, regardless of whether it later died before P1).
    """
    breakout_time = _as_utc(breakout_time)
    same_type = confluent[confluent["type"] == level_type]
    live = same_type[same_type["death_time"].isna() |
                     (same_type["death_time"] > breakout_time)]
    return live.reset_index(drop=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_stats(df, label):
    if df is None or df.empty:
        print(f"{label}: (empty)")
        return
    print(f"{label}: {len(df):,} levels  "
          f"{df['formation_time'].min()} -> {df['formation_time'].max()}")
    counts = df["fate"].value_counts()
    for fate in (FATE_RETESTED, FATE_CONSUMED_EARLY, FATE_DISCARDED_NO_CLOSE,
                 FATE_GATED_DROPPED, FATE_OPEN_UNBROKEN, FATE_OPEN_AWAITING_RETEST):
        print(f"    {fate:<22} {int(counts.get(fate, 0)):>8,}")
    print(f"    {'broke out at some point':<22} {int(df['breakout_time'].notna().sum()):>8,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-h1", action="store_true")
    ap.add_argument("--build-m5", action="store_true",
                    help="one continuous M5 ledger spanning every contract -- "
                         "see the 'continuous contracts only' convention in CLAUDE.md")
    ap.add_argument("--rebuild", action="store_true", help="ignore any cached copy")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if not (args.build_h1 or args.build_m5 or args.stats):
        ap.error("nothing to do -- pass --build-h1 and/or --build-m5")

    if args.build_h1 or args.stats:
        df = h1_levels(rebuild=args.rebuild)
        _print_stats(df, "H1")

    if args.build_m5 or args.stats:
        df = m5_levels(rebuild=args.rebuild)
        _print_stats(df, "M5 (continuous)")


if __name__ == "__main__":
    main()
