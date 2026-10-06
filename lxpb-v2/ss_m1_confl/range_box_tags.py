"""Range-box tags for the ss_m1_confl / ss_m2_confl reports.

Boxes come from patterns-pure's `find_range_box_zz`, run on REAL M5 bars with
the real 1-minute bars as its lower timeframe (whatever bar size the report
itself trades on). Two trade tags are read off them:

  breakout-from-range  the trade fades a fresh, confirmed strong breakout that
                       has not been retested yet (the run-in leg into the level
                       is the leg that broke the box): an LLPB (short) with a range BELOW the
                       level in state breakout-up-waiting-for-retest, or an LHPB
                       (long) with a range ABOVE it in state
                       breakout-below-waiting-for-retest.
  retest-into-range    the retested level's price lies inside a range whose
                       strong breakout (either way) is still waiting for its
                       retest.

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

from find_range_box_zz import MIN_EDGE_TOUCHES, find_range_box_zz

M5_MIN = 5
LOOKBACK_DAYS = 5   # M5 history fed to the detector before each touch
TAG_FROM_RANGE = "breakout-from-range"
TAG_INTO_RANGE = "retest-into-range"


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
    return found


def _contains(box, price):
    return box["low"] <= price <= box["high"]


def _describe(box):
    return {"start": box["start_time"], "low": float(box["low"]), "high": float(box["high"]),
            "status": box["status"], "state": box["state"], "breakout": box["breakout"],
            "break_dir": box["break_dir"], "break_time": box["break_time"]}


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


def classify(boxes, level_type, level_price, run_in_start=None, touch=None):
    """(from_range, into_range): lists of the boxes behind each tag.

    from_range -- the trade fades a confirmed strong breakout whose range has
        not been retested yet: an LLPB (short) with a box BELOW the level's
        price in state breakout-up-waiting-for-retest, or an LHPB (long) with
        a box ABOVE it in state breakout-below-waiting-for-retest -- and the
        box's breakout is the run-in's own (see _broke_in_run_in: inside the
        leg that began at `run_in_start`, or still forming). A breakout
        before the leg began does not count.
    into_range -- the level's price lies inside a box whose strong breakout is
        still waiting for its retest (either direction): the level retests
        into that range."""
    from_range, into_range = [], []
    if boxes is None or boxes.empty:
        return from_range, into_range
    price = float(level_price)
    for _, b in boxes.iterrows():
        state = b["state"]
        if state not in WAITING_STATES:
            continue
        if _contains(b, price):
            into_range.append(_describe(b))
        elif not _broke_in_run_in(b, run_in_start, touch):
            continue
        elif level_type == "LLPB" and state == STATE_UP and b["high"] < price:
            from_range.append(_describe(b))
        elif level_type == "LHPB" and state == STATE_DOWN and b["low"] > price:
            from_range.append(_describe(b))
    return from_range, into_range
