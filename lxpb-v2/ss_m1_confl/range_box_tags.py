"""Range-box tags for the ss_m1_confl / ss_m2_confl reports.

Boxes come from patterns-pure's `find_range_box_zz`, run on REAL M5 bars with
the real 1-minute bars as its lower timeframe (whatever bar size the report
itself trades on). Two trade tags are read off them:

  breakout-from-range  PLAYBOOK 1, fade the breakout: the trade fades a fresh,
                       confirmed strong breakout that has not been retested yet
                       (the run-in leg into the level is the leg that broke the
                       box): an LLPB (short) above a breakout-up episode, or an
                       LHPB (long) below a breakout-below one.
  retest-into-range    PLAYBOOK 2, go with the breakout on the retest: an LHPB
                       (long) after a breakout up, an LLPB (short) after a
                       breakout below, with the level inside the box or within
                       GO_WITH_EDGE_TOL beyond the breakout-side edge, while that
                       breakout is still waiting for its retest.

Every strong breakout is an EPISODE of its box and belongs to ONE edge (upper for
an up breakout, lower for a below one), so a box that broke both ways has both
edges, each with its own two playbooks. A strong breakout is never invalidated: a
finished edge (retested, or no level was waiting) is only marked done in the
episode, whether or not trades were taken, and the other edge can still open.
Each (box, edge, playbook) is a seat that takes one trade; see "One trade per
seat" below.

"Waiting for retest" is the detector's own state: a bar closed strong beyond an
edge and no LXPB level of the box on the real M5 ledger has completed its retest
since. Once retested, or for weak / failed / no-level / ranging boxes, no tag.

Point in time: the detector is re-run for every trade as of the retest touch.
The M5 bar holding the touch is not waited for -- it is built from the ticks up
to the touch and treated as if it closed right there, so the question asked is
"if M5 closed here, has that range already broken out strongly?". Nothing after
the touch is read; the ledger is cut back to what was known then too.

A box counts as a range only if it has at least `MIN_EDGE_TOUCHES` pivots at
each edge -- the detector's own rule for a broken box. It applies that rule
only once a box has ended; here it is applied to every box, otherwise every pair
of consecutive pivots would be a "range". Merged boxes are skipped:
the outer box they merged into carries the same area.
"""
import numpy as np
import pandas as pd

from find_range import _atr_series, _spacing_minutes
from find_range_box_zz import (ATR_MULT, MIN_EDGE_TOUCHES, _intrabar_order, _zigzag_pivots,
                               find_range_box_zz)

M5_MIN = 5
LOOKBACK_DAYS = 5   # M5 history fed to the detector before each touch
TAG_FROM_RANGE = "breakout-from-range"
TAG_INTO_RANGE = "retest-into-range"
GO_WITH_EDGE_TOL = 2.0   # pts: a go-with level may sit this far OUTSIDE the breakout-side edge
TAG_SLOT_LOST = "range-slot-lost"
SLOT_FROM = "from"
SLOT_INTO = "into"


def _partial_bar(bars, start):
    """One OHLC row labelled `start`, folded from `bars`."""
    return pd.DataFrame({"open": [bars["open"].iloc[0]], "high": [bars["high"].max()],
                         "low": [bars["low"].min()], "close": [bars["close"].iloc[-1]]},
                        index=pd.DatetimeIndex([start]))


STATE_UP = "breakout-up-waiting-for-retest"
STATE_DOWN = "breakout-below-waiting-for-retest"
WAITING_STATES = (STATE_UP, STATE_DOWN)


def _levels_as_of(levels, touch, lo):
    """The M5 LXPB ledger as the market knew it at `touch`: only levels already
    broken, and any level that was consumed / retested AFTER the touch put back
    to "awaiting retest" (its death and retest erased)."""
    lv = levels[(levels["breakout_time"] <= touch)
                & (levels["death_time"].isna() | (levels["death_time"] > lo))
                & levels["type"].isin(["LHPB", "LLPB"])].copy()
    later = lv["death_time"] > touch
    lv.loc[later, "fate"] = "open_awaiting_retest"
    lv.loc[later, ["death_time", "retest_time"]] = pd.NaT
    return lv


def boxes_as_of(m5, m1, touch_time, tick_bars, levels=None, lookback_days=LOOKBACK_DAYS):
    """Range boxes known at `touch_time`: one row per box, status "open" (still
    live at the touch) or "broken" (already left by price), with start_time,
    break_time (NaT while open), low, high, breakout grade and edge counts.

    `m5` / `m1` are UTC-indexed OHLC frames. The M5 bar holding the touch is
    NOT waited for: it is built from the 1-minute bars and the ticks up to and
    including the touch, and treated as if it closed right there -- so a box
    that this half-formed bar already breaks out of counts as broken. Nothing
    after the touch is read. `tick_bars(lo, hi, freq)` builds bars from ticks
    over [lo, hi) (the minute holding the touch is built that way too).
    `levels` is the real M5 LXPB ledger; the box state (live / not) needs it
    for broken boxes, and it is cut back to what was known at the touch."""
    touch = pd.Timestamp(touch_time)
    touch = touch.tz_localize("UTC") if touch.tzinfo is None else touch.tz_convert("UTC")
    m5_start = touch.floor(f"{M5_MIN}min")
    m1_start = touch.floor("1min")
    lo = m5_start - pd.Timedelta(days=lookback_days)
    low_tf = m1[(m1.index >= lo) & (m1.index < m1_start)]
    cur = tick_bars(m1_start, touch + pd.Timedelta(microseconds=1), "1min")
    if not cur.empty:
        low_tf = pd.concat([low_tf, cur])
    data = m5[(m5.index >= lo) & (m5.index < m5_start)]
    forming = low_tf[low_tf.index >= m5_start]
    if not forming.empty:
        data = pd.concat([data, _partial_bar(forming, m5_start)])
    if len(data) < 30 or low_tf.empty:
        return pd.DataFrame()
    lv = _levels_as_of(levels, touch, lo) if levels is not None else None
    found = find_range_box_zz(data, lower_data=low_tf, levels=lv)
    if found.empty:
        return found
    ok = (found["status"].isin(["open", "broken"])
          & (found["n_top_edge"] >= MIN_EDGE_TOUCHES)
          & (found["n_bottom_edge"] >= MIN_EDGE_TOUCHES))
    found = found[ok].reset_index(drop=True)
    found["pivots"] = box_pivots(found, data, low_tf)
    return expand_episodes(found)


ATR_PERIOD = 21   # find_range_box_zz's default; the pivots below must be the detector's own


def box_pivots(found, data, low_tf):
    """The zigzag crests and troughs behind each box, for the charts: one list per row of
    `found`, each pivot {"time", "kind" ("crest"/"trough"), "price", "brk"} oldest first. They are
    the detector's own pivots (the same zigzag, ATR and intrabar order, re-run with the
    detector's defaults), the ones from the box's first pivot to its last inside pivot, plus
    the pivot that broke it (brk=True) when it has one. Display only."""
    H, L = data["high"].to_numpy(float), data["low"].to_numpy(float)
    order = _intrabar_order(data.index, _spacing_minutes(data.index), low_tf)
    piv = _zigzag_pivots(H, L, _atr_series(data, ATR_PERIOD), ATR_MULT, ATR_PERIOD, order)
    piv = [(data.index[i], kind, float(px)) for kind, i, px, _ in piv]
    out = []
    for _, b in found.iterrows():
        bp = b["break_pivot_time"]
        rows = []
        for t, kind, px in piv:
            brk = pd.notna(bp) and t == bp
            if b["start_time"] <= t <= b["last_time"] or brk:
                rows.append({"time": t, "kind": kind, "price": px, "brk": bool(brk)})
        out.append(rows)
    return out


def expand_episodes(found):
    """One row per strong-breakout EPISODE of each box (find_range_box_zz's
    `episodes`): a box that broke both ways gives an up row and a below row, each
    with its own state, so each edge carries its own two playbooks. The row's
    `state` is the episode's (waiting-for-retest = the edge is still active;
    retested / no-level = the edge is done, whether or not trades were taken),
    `edge` is "upper" / "lower", `edge_done` / `edge_done_reason` say whether
    it is done, and the breakout is always "strong". A strong breakout is never
    invalidated: a finished edge is just marked done. `break_time` is the box's
    own zigzag break time for the edge that broke it and the episode's strong
    breakout bar for the other edge. `strong_time` is always the episode's strong
    breakout bar (the zigzag break can come earlier: the bar that first closed
    outside the box is not always the one that graded strong)."""
    rows = []
    for _, b in found.iterrows():
        for e in b["episodes"] or []:
            r = b.copy()
            own = b["break_dir"] == ("up" if e["direction"] == "up" else "down")
            r["state"] = e["state"]
            r["breakout"] = "strong"
            r["edge"] = e["edge"]
            r["edge_done"] = bool(e["done"])
            r["edge_done_reason"] = e["done_reason"]
            r["break_time"] = b["break_time"] if own and pd.notna(b["break_time"]) else e["breakout_time"]
            r["strong_time"] = e["breakout_time"]   # the bar that graded strong: what a chart marks as "broke"
            rows.append(r)
    return pd.DataFrame(rows).reset_index(drop=True) if rows else found.iloc[0:0]


def _contains(box, price):
    return box["low"] <= price <= box["high"]


def _describe(box):
    return {"start": box["start_time"], "low": float(box["low"]), "high": float(box["high"]),
            "status": box["status"], "state": box["state"], "breakout": box["breakout"],
            "break_dir": box["break_dir"], "break_time": box["break_time"],
            "strong_time": box.get("strong_time", box["break_time"]),
            "edge": box["edge"], "edge_done": bool(box["edge_done"]),
            "pivots": box.get("pivots") or []}


def _broke_in_run_in(box, run_in_start, touch):
    """The run-in into the level is what broke the box: the breakout is still
    forming on the touch's own M5 bar (no break bar yet), or its bar (labelled
    by its start) ends after the run-in leg began. A breakout before the leg
    began is a stale one."""
    bt = box["break_time"]
    if pd.isna(bt):
        return True
    end = bt + pd.Timedelta(minutes=M5_MIN)
    return run_in_start is not None and end > pd.Timestamp(run_in_start)


def _near_breakout_edge(box, price, up):
    """The level sits inside the box, or no more than GO_WITH_EDGE_TOL beyond the
    edge the breakout went through (top edge for an up breakout, bottom edge for
    a below one): where price comes back to when it retests the box."""
    if _contains(box, price):
        return True
    return (box["high"] < price <= box["high"] + GO_WITH_EDGE_TOL) if up \
        else (box["low"] - GO_WITH_EDGE_TOL <= price < box["low"])


def classify(boxes, level_type, level_price, run_in_start=None, touch=None):
    """(from_range, into_range): lists of the episode rows (expand_episodes)
    behind each tag. Each strong breakout is one edge; its two playbooks:

    from_range (fade) -- the trade fades a confirmed strong breakout whose range
        has not been retested yet: an LLPB (short) above the top edge of a
        breakout-up-waiting-for-retest episode, or an LHPB (long) below the
        bottom edge of a breakout-below-waiting-for-retest one -- and the
        breakout is the run-in's own (see _broke_in_run_in: inside the leg that
        began at `run_in_start`, or still forming).
    into_range (go-with) -- the trade goes WITH the breakout when price retests
        the box: an LHPB (long) after a breakout UP, an LLPB (short) after a
        breakout BELOW, with the level inside the box or within GO_WITH_EDGE_TOL
        outside its breakout-side edge. The opposite level type there is neither
        playbook."""
    from_range, into_range = [], []
    if boxes is None or boxes.empty:
        return from_range, into_range
    price = float(level_price)
    for _, b in boxes.iterrows():
        state = b["state"]
        if state not in WAITING_STATES:
            continue
        up = state == STATE_UP
        if level_type == ("LHPB" if up else "LLPB") and _near_breakout_edge(b, price, up):
            into_range.append(_describe(b))
        elif not _broke_in_run_in(b, run_in_start, touch):
            continue
        elif level_type == "LLPB" and state == STATE_UP and b["high"] < price:
            from_range.append(_describe(b))
        elif level_type == "LHPB" and state == STATE_DOWN and b["low"] > price:
            from_range.append(_describe(b))
    return from_range, into_range


# ---------------------------------------------------------------------------
# One trade per seat
#
# Every box has one fade seat (breakout-from-range) and one go-with seat
# (retest-into-range); up and down breakouts are separate boxes, hence separate
# seats. A trade takes the seat of EVERY box it qualifies for (an LLPB above two
# overlapping breakout-up boxes fades both), and nothing for the boxes it does
# not qualify for (an LLPB above box A but inside box B leaves B's seat alone:
# B's own breakout gets its own fade later). Seats are handed out in rank
# order; a trade that finds every box it qualifies for already taken loses its
# range tag. No grouping is precomputed: a "group" is just the boxes one trade
# takes together.
# ---------------------------------------------------------------------------

def box_key(box):
    """Identity of a box for seat purposes: start time, both edges and the
    breakout direction. Edges are frozen at the strong breakout, so a box seen
    by an earlier trade and by a later one is the same key. The END time is
    deliberately not part of it: an earlier trade sees the box still forming
    with no end yet, and including it would make one box look like two. The
    direction comes from the waiting state, because the detector's own
    break_dir is empty while the breakout is still forming."""
    direction = "up" if box["state"] == STATE_UP else "down"
    return (pd.Timestamp(box["start"]), round(float(box["low"]), 4),
            round(float(box["high"]), 4), direction)


def slot_rank(entry_is_spike, touch_time):
    """Sort key for contenders on one seat (lowest wins): a trade whose entry
    level is itself a spike (hammer for an LHPB / shooting star for an LLPB,
    the detector pairing; patterns-pure) beats one that is not, otherwise the
    earliest touch wins. A placeholder ranking: the spike preference is NOT
    causal, since a live algo has already entered an earlier non-spike trade
    before a later spike one appears. Replace it with the real candidate
    ranking."""
    return (0 if entry_is_spike else 1, pd.Timestamp(touch_time))


def assign_slots(claims):
    """Hand out the seats, one slot (SLOT_FROM / SLOT_INTO) at a time, best rank first.

    `claims` is a list of dicts with `id`, `slot`, `keys` (box_key of every box
    the trade qualifies for in that slot), `spike` (bool) and `touch` (UTC
    Timestamp), one per FILLED trade per slot it qualifies for. Unfilled
    candidates must not be passed in: they never use a seat. A trade takes every
    one of its boxes whose seat is still free. Returns {(slot, id): {"won":
    [keys it took], "lost": {key: id of the trade that already held it}}}; a
    trade with an empty "won" lost the slot."""
    out = {}
    for slot in {c["slot"] for c in claims}:
        taken = {}
        for c in sorted((c for c in claims if c["slot"] == slot),
                        key=lambda c: slot_rank(c["spike"], c["touch"])):
            won = [k for k in c["keys"] if k not in taken]
            lost = {k: taken[k] for k in c["keys"] if k in taken}
            for k in won:
                taken[k] = c["id"]
            out[(slot, c["id"])] = {"won": won, "lost": lost}
    return out
