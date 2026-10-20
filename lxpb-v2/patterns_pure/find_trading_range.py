"""find_trading_range -- sideways ranges (consolidation boxes), bar by bar.

Written from scratch 2026-10-19; it shares no code or rules with
`find_range.py` / `find_range_box_zz.py`. A range is ANCHORED TO ZIGZAG PIVOTS
(user, 2026-10-19; `edges="pivot"`, the default): its edges are the highest
crest and the lowest trough of patterns-pure's `find_zigzag` (0.75 x ATR(21)
reversal, the repo's only zigzag, run bar by bar inside the tracker and
identical to the batch zigzag), it starts on a pivot, its touches are pivots,
and only a newly confirmed pivot moves an edge. No bar's wick ever sets an
edge. `edges="wick"` (with `max_widen` 0.25) is the earlier definition, kept
for comparison: bar highs / lows for all of the above. Breakouts can be graded
with `find_range`'s strong-breakout grader (`lower_data`).

REGRESSION CASES (user, 2026-10-19): after ANY change here, or to find_zigzag,
find_ATR, find_range (_BreakoutGrader, _atr_series) or live_range/trbook.py, run
`python patterns_pure/tests/test_find_trading_range_cases.py`. It pins the USDJPY
H1 Apr 21-29 2026 range (158.958-159.847, failed breakout Apr 29 04:00 PT) and
the AUDUSD H1 Jan 13 08:00 - Jan 19 06:00 PT 2026 range (0.66671-0.67108), both
exact, on frozen bars, plus no look-ahead. Never change its expected
values to make a change pass without the user's OK.

Point in time. `TradingRangeTracker.update()` takes one closed bar at a time
and decides everything from that bar and the bars before it; nothing it has
reported is ever revised by a later bar except the live box's own end and
edges (which only widen) and a box merging into its predecessor (an event).
`find_trading_range(data)` is the same tracker replayed over a frame, so batch
and live cannot disagree: running it on the first k bars gives exactly the
state the live tracker had after bar k.

A window of consecutive bars i..t QUALIFIES when

1. it has at least `min_bars` bars (and at most `max_bars`),
2. it is short: height (highest crest - lowest trough of the pivots on its
   bars that are confirmed by bar t; wick mode: highest high - lowest low) at most
   `max_height_tr` x the mean true range of its own bars, so one setting
   works overnight and in the cash session,
3. it goes nowhere: the least-squares line through its closes moves at most
   `max_drift` x the height from its first bar to its last,
4. (pivot mode) it starts on a pivot bar, and holds both a crest and a trough.

Each bar, with no confirmed box live:

- CANDIDATE: of the qualifying windows ending at this bar and starting at or
  after the floor (the bar that broke the previous box), the one whose price
  rotated most between its edges (most alternating touches in total, then the
  better-balanced, then the longest). None qualifies -> no candidate. A
  candidate is a range forming, never a range.
- CONFIRMED: the candidate becomes a box once price has rotated between its
  edges: at least `min_total_touches` touches of the two edge zones together
  and at least `min_touches` of each. With the defaults that is 5, e.g.
  top-bottom-top-bottom-top. How a touch counts:
  - pivot mode: a crest in the top edge zone is a top touch, a trough in the
    bottom zone a bottom touch (a crest at / above the midline or a trough at /
    below it is the swing a half touch needs); wick mode: a bar's high / low
    in the zone;
  - each pivot (bar) is judged against the box AS IT STOOD THEN (running edges
    from the window's first bar; edge zones = the outer `touch_frac` of that
    height, midline = its middle), the way it is read live, so a later new
    extreme never demotes an earlier touch (user, 2026-10-19);
  - a bar counts only once that box is at least `running_touches` x the
    window's height (the first bars of any window would otherwise all be
    "edges"); `running_touches` 0 judges every bar against the final edges.
    Pivot mode: the pivots before that wait and are judged against the first
    box that is that tall, so a window's opening swings still count (user,
    2026-10-20; wick mode still skips its early bars);
  - a touch of one edge counts after the other edge was touched (a full
    swing; one edge poked five times in a row is one touch) or, at most
    `max_half_touches` times, after price reached the midline since that
    edge's last counted touch (a half swing back to retest the same edge).
  `confirm_time` is that bar; edges then are `confirm_high` / `confirm_low`.
- MERGE: a newly confirmed box joins the previous box instead when they are
  one range with a fake breakout between them: at most `merge_gap_bars` bars
  apart, their price ranges overlap by at least `merge_overlap` x the smaller
  height, and the joined box -- both boxes plus the bars between them --
  still passes rule 2 over all its bars. The earlier box takes the bars,
  edges and live state; its id stays, `merged` counts the boxes it absorbed.

Each bar, with a confirmed box live, PIVOT MODE, decided on the bar's close
and never revisited (user, 2026-10-20):

- a close outside that grades STRONG on `lower_data` BREAKS the box.
- else a wick past an edge widens the box to it while the box stays within
  `max_widen` (0.5) of its height at confirmation (or last merge) and within
  rule 2. A wick the box can't take in FREEZES the box on that bar: broken that
  way (`break_type` "breakout") if the bar closed outside, a failed breakout
  (`break_type` "failed", `break_dir` the side the wick poked) if it closed
  back inside. Either way the box ends on the bar before. (Was: a close out was
  only an attempt and the box lived on, edges fixed, until a close past
  `max_widen` x its height: AUDUSD H1 box #9 ignored the Jan 12 05:00 PT
  breakout to 0.67179 and lived to Jan 19.) (`break_tr` / `confirm_bars` are
  wick mode only.)

Each bar, with a confirmed box live, WICK MODE:

- a close inside the box keeps it going and widens the edges to the bar's
  high/low, unless the box would then be more than `max_widen` taller than
  at confirmation (or at its last merge) or break rule 2 (then the wick is
  left out);
- a close outside is a breakout attempt. With `lower_data` (user, 2026-10-19:
  the strong-breakout logic of `find_range_box_zz`, through `find_range`'s own
  grader, see `lower_grader`) the box is BROKEN by the first close past an
  edge that grades STRONG on the lower timeframe (`break_grade` "strong"), or
  by a close past the allowable price -- the edge pushed out `max_widen` x
  the box's height, where the box could never reach -- graded or not
  ("weak"). A weak close past the edge but within the allowable price is
  only an attempt: later closes out there can still grade strong. Without
  `lower_data` the old rule applies: one close at least `break_tr` x the
  box's mean true range past the edge, or `confirm_bars` consecutive closes
  past it (`break_grade` None). `break_time` is the first close out,
  `break_confirm_time` the bar that broke it, and the box ends on the bar
  before the first close out. An attempt that closes back inside failed: its
  bars join the box (widening as above).

PIVOT EDGES of a wick-mode box: `pivot_high` / `pivot_low` / `pivot_start_time`
in the output and `pivot_edges(box, upto)` give its highest crest / lowest
trough / first pivot (pivots confirmed by then); in pivot mode they equal the
box's own edges.

Pivot anchoring, USDJPY H1 (user, 2026-10-19): as of 2026-04-27 19:00 PT the
live range is 159.093 (trough Apr 27 02:00 PT) - 159.847 (crest Apr 23 10:00 PT),
from the crest at Apr 21 10:00 PT, confirmed Apr 22 00:00 PT (14:00 PT before early pivots
waited for the box, 2026-10-20); the wick definition
had 159.038-159.847 from Apr 21 07:00 PT, the previous range's breakout bar.
`max_widen` 0.5 is the smallest that takes in both later pivots (0.4 misses the
Apr 27 trough, 0.75 swallows all of April into one box). ES M5 Oct 15-16 in
pivot mode: the overnight box 7792.50-7809.50, 21:30-06:45, and nothing else
in the window; Oct 1-19: 21 ranges; no look-ahead (30 cut points).

Tuned 2026-10-19 on ES (EPZ26 .scid) M5 by the user's reading of the charts:
- Oct 15 07:00 - Oct 16 08:30 PT: the overnight box 20:50-06:30,
  7792.50-7809.50 is a range; the 12:20-15:50 chop and the 17:35-20:40 grind
  are not. The defaults find the overnight box (from 20:55, confirmed 23:05)
  and none of those two; they also find 08:20-10:30 (7763-7782), which
  rotated edge to edge as it formed.
- Oct 19 09:50-11:00 has valid touches (B 10:05, T 10:15, B 10:20, ...):
  the defaults give 09:50-12:10, confirmed 11:00. Judging touches against the
  final edges instead missed them (the 10:45 low moved the bottom zone below
  the 10:05 / 10:35 lows), and `min_bars` 24 started the box at 09:30.
History of the touch rule (same day): 3 + 3 full swings, then 5 in total with
2+ per edge, then up to 2 half swings, then running edges with the 0.7 guard
and `min_bars` 15 (was 24). Guard 0.5 also confirms the 17:40-19:15 grind.
Oct 1 - Oct 19 2026: 33 ranges. No look-ahead:
`../scripts/range_pure_lookahead.py` replays 60-80 cut points and every event
matches the full run.

    find_trading_range(data, **params) -> DataFrame of confirmed ranges:
        id, start_time, confirm_time, end_time, high, low, height, mean_tr,
        height_tr, n_bars, touches_high, touches_low, drift, confirm_high,
        confirm_low, merged, status ("active" / "broken" = ended), break_dir
        ("up" / "down" / None), break_grade, break_type ("breakout" / "failed"
        = a failed breakout froze it / None), break_time, break_confirm_time,
        confirm_start_time (start of the window that confirmed it),
        confirm_touches ([(time, "top"/"bottom", is_half)] that confirmed it),
        members ([{start_time, end_time, high, low}] of a merged range, else []),
        pivot_high, pivot_low, pivot_high_time, pivot_low_time, pivot_start_time
    find_trading_range(data, return_all=True) -> dict(ranges=..., candidates=
        one row per candidate episode, events=every confirm/merge/break)

`data`: DatetimeIndex (or a `time` / `datetime` / `date` column) and
Open/High/Low/Close columns, any case. Gaps (the daily halt, weekends) are
walked across as adjacent bars.
"""
import numpy as np
import pandas as pd

DEFAULTS = dict(
    min_bars=15,
    max_bars=600,
    max_height_tr=6.0,
    max_drift=0.5,
    min_touches=2,
    min_total_touches=5,
    max_half_touches=2,
    running_touches=0.7,
    touch_frac=0.2,
    max_widen=0.5,
    break_tr=1.0,
    confirm_bars=2,
    merge_gap_bars=36,
    merge_overlap=0.5,
    edges="pivot",
)

RANGE_COLUMNS = ["id", "start_time", "confirm_time", "end_time", "high", "low", "height",
                 "mean_tr", "height_tr", "n_bars", "touches_high", "touches_low", "drift",
                 "confirm_high", "confirm_low", "merged", "status", "break_dir", "break_grade",
                 "break_type",
                 "break_time", "break_confirm_time", "confirm_start_time", "confirm_touches",
                 "members", "pivot_high", "pivot_low", "pivot_high_time", "pivot_low_time",
                 "pivot_start_time"]
CANDIDATE_COLUMNS = ["id", "first_seen", "last_seen", "start_time", "high", "low",
                     "touches_high", "touches_low", "outcome", "range_id"]
EVENT_COLUMNS = ["time", "event", "range_id", "high", "low"]


def _ohlc(data):
    df = data.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex):
        for c in ("time", "datetime", "date"):
            if c in df.columns:
                df = df.set_index(pd.DatetimeIndex(df[c]))
                break
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"find_trading_range: missing columns {sorted(missing)}")
    return df


_TOP, _BOT, _BELOW, _ABOVE, _BOTH = 1, 2, 3, 4, 5


def _touches(H, L, hi, lo, frac, max_half=0, where=False, running=0.0):
    """Edge-zone touches (top, bottom) of a box hi/lo; see the module docstring.

    A touch of one edge counts after the other edge was touched (a full swing)
    or, at most `max_half` times, after price reached the box's midline since
    that edge's last counted touch (a half swing back to retest it).
    `where=True` also returns [(bar offset, "top"/"bottom", is_half), ...].
    `running` > 0 judges each bar against the box as it stood then (running
    high / low from the window's first bar), counting touches only once that
    box is at least `running` x the final height; 0 judges every bar against
    the final edges hi / lo."""
    H, L = np.asarray(H), np.asarray(L)
    if running:  # edges, zones and midline as the box stood at each bar
        hi_r, lo_r = np.maximum.accumulate(H), np.minimum.accumulate(L)
        h_r = hi_r - lo_r
        zone, mid = frac * h_r, (hi_r + lo_r) / 2
        ok = h_r >= running * (hi - lo)  # box already tall enough to be touched
        u, d = ok & (H >= hi_r - zone), ok & (L <= lo_r + zone)
        hi, lo = hi_r, lo_r
    else:
        zone, mid = frac * (hi - lo), (hi + lo) / 2
        u, d = H >= hi - zone, L <= lo + zone
    lm, hm = L <= mid, H >= mid
    tok = np.where(u & ~d, _TOP, np.where(d & ~u, _BOT, np.where(
        lm & hm, _BOTH, np.where(lm, _BELOW, np.where(hm, _ABOVE, 0)))))
    at = np.flatnonzero(tok)
    tok = tok[at]
    if len(tok) > 1:  # repeats change nothing, so runs collapse to one token
        keep = np.r_[True, tok[1:] != tok[:-1]]
        tok, at = tok[keep], at[keep]
    return _count(tok, at, max_half, where)


def _pivot_touches(piv, hi, lo, frac, max_half=0, where=False, running=0.0):
    """Touches counted on zigzag pivots instead of bars (edges="pivot"). `piv` is
    [(kind, bar offset, price)] in bar order, `hi` / `lo` the box's pivot edges. A
    crest in the top zone is a top touch, a trough in the bottom zone a bottom
    touch; a crest at or above the midline / a trough at or below it is the swing
    a half touch needs. With `running`, each pivot is judged against the pivot box
    as it stood then, once that box is `running` x the final height; the pivots
    before that WAIT and are judged, in order, against the first box that is
    (user, 2026-10-20: they used to be dropped, so a window's opening swings,
    e.g. AUDUSD H1 crests Jan 12 08:00 / Jan 13 05:00 PT, could never count)."""
    rh, rl = -np.inf, np.inf
    tok, at = [], []
    wait = []   # pivots seen before the running box was tall enough to be touched
    for kind, off, price in piv:
        if kind == "crest":
            rh = max(rh, price)
        else:
            rl = min(rl, price)
        eh, el = (rh, rl) if running else (hi, lo)
        if not np.isfinite(eh - el) or (running and eh - el < running * (hi - lo)):
            wait.append((kind, off, price))
            continue
        zone, mid = frac * (eh - el), (eh + el) / 2
        for k, o, pr in wait + [(kind, off, price)]:
            if k == "crest":
                t = _TOP if pr >= eh - zone else _ABOVE if pr >= mid else 0
            else:
                t = _BOT if pr <= el + zone else _BELOW if pr <= mid else 0
            if t:
                tok.append(t)
                at.append(o)
        wait = []
    return _count(tok, at, max_half, where)


def _count(tok, at, max_half, where):
    """The alternating-touch state machine over edge tokens (see _touches)."""
    th = tl = half = last = 0
    crossed = False
    marks = []
    for t, i in zip(tok, at):
        if t == _TOP:
            if last != 1:
                th, last, crossed = th + 1, 1, False
                marks.append((int(i), "top", False))
            elif crossed and half < max_half:
                th, half, crossed = th + 1, half + 1, False
                marks.append((int(i), "top", True))
        elif t == _BOT:
            if last != -1:
                tl, last, crossed = tl + 1, -1, False
                marks.append((int(i), "bottom", False))
            elif crossed and half < max_half:
                tl, half, crossed = tl + 1, half + 1, False
                marks.append((int(i), "bottom", True))
        elif (last == 1 and t != _ABOVE) or (last == -1 and t != _BELOW):
            crossed = True
    return (th, tl, marks) if where else (th, tl)


def _drift(C, hi, lo):
    if len(C) < 2 or hi <= lo:
        return 0.0
    slope = np.polyfit(np.arange(len(C), dtype=float), C, 1)[0]
    return abs(slope) * (len(C) - 1) / (hi - lo)


class TradingRangeTracker:
    """Bar-by-bar range detector. Feed closed bars to `update()`; read
    `candidate` (forming, or None), `active` (the confirmed box still live, or
    None) and `ranges` (every confirmed box, merged ones folded in)."""

    def __init__(self, grader=None, **params):
        """`grader(bar_time, up, box_high, box_low)` -> "strong" / "weak" grades a
        close beyond an edge (see `lower_grader`); None uses the close-count rule."""
        unknown = set(params) - set(DEFAULTS)
        if unknown:
            raise TypeError(f"TradingRangeTracker: unknown parameters {sorted(unknown)}")
        self.p = {**DEFAULTS, **params}
        self.grader = grader
        self.T, self.H, self.L, self.C, self.TR = [], [], [], [], []
        from find_zigzag import ZigZag
        self.zz = ZigZag()          # patterns-pure's zigzag, defaults: the pivot edges
        self.pivots = []            # find_zigzag Pivot(kind, i, price, j), in confirm order
        self.ATR = []
        if self.p["edges"] not in ("wick", "pivot"):
            raise ValueError(f"edges {self.p['edges']!r}: use 'pivot' or 'wick'")
        self.pivot = self.p["edges"] == "pivot"
        self.CP, self.TP = [], []   # crest / trough price at its own bar once confirmed (nan elsewhere)
        self.ranges = []
        self.active = None
        self.candidate = None
        self.candidates = []
        self.events = []
        self.floor = 0
        self._pend = None  # (direction, first attempt bar) of a live box's breakout attempt

    # ---- per bar ---------------------------------------------------------
    def update(self, time, open_, high, low, close):
        """Add one closed bar; returns the events it caused (may be empty)."""
        pc = self.C[-1] if self.C else close
        self.T.append(time)
        self.H.append(float(high))
        self.L.append(float(low))
        self.C.append(float(close))
        self.TR.append(max(high - low, abs(high - pc), abs(low - pc)))
        j = len(self.C) - 1
        n_ev = len(self.events)
        self._step_zigzag(j)
        if self.active is not None:
            self._step_active(j)
        if self.active is None:
            self._step_search(j)
        return self.events[n_ev:]

    def _step_zigzag(self, j):
        """Feed find_zigzag's ZigZag this bar (its ATR(21) as of this bar: the same
        recursion as find_range._atr_series) and keep the pivots it confirms."""
        tr = self.TR[j]
        atr = np.nan if j == 0 else tr if j == 1 else self.ATR[-1] + (tr - self.ATR[-1]) / self.zz.atr_period
        self.ATR.append(atr)
        self.CP.append(np.nan)
        self.TP.append(np.nan)
        for piv in self.zz.step(self.H[j], self.L[j], atr):
            self.pivots.append(piv)
            (self.CP if piv.kind == "crest" else self.TP)[piv.i] = piv.price
            b = self.active
            if self.pivot and b is not None and piv.i >= b["start"]:
                self._pivot_widen(b, piv, j)

    def _pivot_widen(self, box, piv, j):
        """Pivot mode: a pivot confirmed past an edge of the live box moves that edge
        out to it, while the box stays within `max_widen` of its height at
        confirmation (or last merge) and within rule 2."""
        p = self.p
        nh = max(box["high"], piv.price) if piv.kind == "crest" else box["high"]
        nl = min(box["low"], piv.price) if piv.kind == "trough" else box["low"]
        if (nh - nl <= (1 + p["max_widen"]) * box["base_height"]
                and nh - nl <= p["max_height_tr"] * self._mean_tr(box["start"], j)):
            box["high"], box["low"] = nh, nl

    def _pivots_in(self, s, e, upto):
        """[(kind, bar offset from s, price)] of the pivots on bars s..e confirmed by `upto`."""
        return [(k, i - s, pr) for k, i, pr, jc in self.pivots if s <= i <= e and jc <= upto]

    def _box_touches(self, s, e, hi, lo, upto, where=False):
        p = self.p
        if self.pivot:
            return _pivot_touches(self._pivots_in(s, e, upto), hi, lo, p["touch_frac"],
                                  p["max_half_touches"], where, p["running_touches"])
        return _touches(self.H[s:e + 1], self.L[s:e + 1], hi, lo, p["touch_frac"],
                        p["max_half_touches"], where, p["running_touches"])

    def _gap_ext(self, a, b, hi, lo):
        """Highest / lowest price of bars a..b-1 (pivot mode: of their pivots), else hi / lo."""
        if self.pivot:
            cp, tp = np.asarray(self.CP[a:b]), np.asarray(self.TP[a:b])
            return (float(np.nanmax(cp)) if np.isfinite(cp).any() else hi,
                    float(np.nanmin(tp)) if np.isfinite(tp).any() else lo)
        return max(self.H[a:b], default=hi), min(self.L[a:b], default=lo)

    def pivot_edges(self, box, upto=None):
        """The box's PIVOT EDGES as of bar `upto` (default: the last bar; for a
        broken box, the bar that broke it): the highest zigzag crest and the lowest
        zigzag trough whose own bar is inside the box and that were confirmed by
        then. dict(high, low, high_i, low_i, start_i = the box's first pivot bar),
        or None until the box holds both a crest and a trough."""
        if upto is None:
            upto = box["break_confirm_bar"] if box["break_confirm_bar"] is not None else len(self.C) - 1
        hi = lo = None
        first = None
        for kind, i, price, jc in self.pivots:
            if jc > upto or not box["start"] <= i <= box["end"]:
                continue
            if self.pivot and not box["low"] <= price <= box["high"]:
                continue    # pivot mode: a pivot the box left out (past max_widen) is no edge
            first = i if first is None else min(first, i)
            if kind == "crest" and (hi is None or price > hi[0]):
                hi = (price, i)
            elif kind == "trough" and (lo is None or price < lo[0]):
                lo = (price, i)
        if hi is None or lo is None:
            return None
        return dict(high=hi[0], low=lo[0], high_i=hi[1], low_i=lo[1], start_i=first)


    def _event(self, j, kind, box):
        self.events.append(dict(time=self.T[j], event=kind, range_id=box["id"],
                                high=box["high"], low=box["low"]))

    def _mean_tr(self, i, j):
        return float(np.mean(self.TR[i:j + 1]))

    def _absorb(self, box, i0, i1):
        """Bars i0..i1 join the box, widening it while rule 2 still holds."""
        p = self.p
        cap = (1 + p["max_widen"]) * box["base_height"]
        for i in range(i0, i1 + 1):
            if self.pivot:      # pivot mode: bars never move an edge, only pivots do
                box["end"] = i
                continue
            nh, nl = max(box["high"], self.H[i]), min(box["low"], self.L[i])
            if (nh - nl <= cap
                    and nh - nl <= p["max_height_tr"] * self._mean_tr(box["start"], i)):
                box["high"], box["low"] = nh, nl
            box["end"] = i

    def _step_pivot(self, j):
        """Pivot mode, on this bar's close (user, 2026-10-20): a close out that grades
        strong breaks the box; else a wick past an edge widens the box to it while
        the box stays within `max_widen` of its height at confirmation (or last
        merge) and within rule 2; a wick the box can't take in FREEZES it on this
        bar: broken that way if the bar closed out, a failed breakout if it closed
        back inside. Decided now, never revisited on later bars."""
        p, box = self.p, self.active
        h, l, c = self.H[j], self.L[j], self.C[j]
        d = 1 if c > box["high"] else -1 if c < box["low"] else 0
        if d and self.grader is not None and self.grader(self.T[j], d > 0, box["high"], box["low"]) == "strong":
            self._pend = (d, j)
            return self._break(box, d, j, "strong")
        nh, nl = max(box["high"], h), min(box["low"], l)
        if (nh, nl) == (box["high"], box["low"]) or (
                nh - nl <= (1 + p["max_widen"]) * box["base_height"]
                and nh - nl <= p["max_height_tr"] * self._mean_tr(box["start"], j)):
            box.update(high=nh, low=nl, end=j)
            return
        if d == 0:  # failed breakout: the side the wick reached farther past
            d = 1 if h - box["high"] >= box["low"] - l else -1
            self._pend = (d, j)
            return self._break(box, d, j, None, failed=True)
        self._pend = (d, j)
        return self._break(box, d, j, "weak" if self.grader is not None else None)

    def _step_active(self, j):
        if self.pivot:
            return self._step_pivot(j)
        p, box, c = self.p, self.active, self.C[j]
        d = 1 if c > box["high"] else -1 if c < box["low"] else 0
        if d == 0:
            if self._pend is not None:  # attempt closed back inside: failed
                self._absorb(box, self._pend[1], j)
                self._pend = None
            else:
                self._absorb(box, j, j)
            return
        if self._pend is not None and self._pend[0] != d:  # flipped straight through
            self._absorb(box, self._pend[1], j - 1)
            self._pend = None
        if self._pend is None:
            self._pend = (d, j)
        if self.grader is not None:
            limit = p["max_widen"] * box["base_height"]  # the allowable price, past the edge
            beyond = c - box["high"] if d > 0 else box["low"] - c
            grade = self.grader(self.T[j], d > 0, box["high"], box["low"])
            if grade == "strong":
                return self._break(box, d, j, "strong")
            if beyond > limit:  # closed where the box can no longer reach
                return self._break(box, d, j, "weak")
            return
        beyond = c - box["high"] if d > 0 else box["low"] - c
        mtr = self._mean_tr(box["start"], box["end"])
        if beyond >= p["break_tr"] * mtr or j - self._pend[1] + 1 >= p["confirm_bars"]:
            self._break(box, d, j, None)

    def _break(self, box, d, j, grade, failed=False):
        k = self._pend[1]
        box.update(status="broken", break_dir="up" if d > 0 else "down", break_bar=k,
                   break_confirm_bar=j, break_grade=grade,
                   break_type="failed" if failed else "breakout")
        self._event(j, "broken", box)
        self.floor = k
        self.active = None
        self._pend = None

    def _best_window(self, j):
        """The candidate at bar j: of the qualifying windows ending at j and
        starting at or after the floor, the one whose price rotated most
        (most touches in total, then the better-balanced, then the longest):
        (start, high, low, touches_high, touches_low) or None. Windows with the
        same high and low are compared by their longest member only."""
        p = self.p
        i0 = max(self.floor, j - p["max_bars"] + 1)
        if j - i0 + 1 < p["min_bars"]:
            return None
        H = np.asarray(self.H[i0:j + 1])[::-1]
        L = np.asarray(self.L[i0:j + 1])[::-1]
        C = np.asarray(self.C[i0:j + 1])[::-1] - self.C[j]
        TR = np.asarray(self.TR[i0:j + 1])[::-1]
        m = np.arange(1, len(H) + 1, dtype=float)  # window length, ending at j
        if self.pivot:  # edges = the window's highest crest / lowest trough; it starts on a pivot
            cp = np.asarray(self.CP[i0:j + 1])[::-1]
            tp = np.asarray(self.TP[i0:j + 1])[::-1]
            hi = np.maximum.accumulate(np.where(np.isnan(cp), -np.inf, cp))
            lo = np.minimum.accumulate(np.where(np.isnan(tp), np.inf, tp))
            on_pivot = ~np.isnan(cp) | ~np.isnan(tp)
        else:
            hi = np.maximum.accumulate(H)
            lo = np.minimum.accumulate(L)
        h = hi - lo
        mtr = np.cumsum(TR) / m
        u = m - 1  # bars back from j
        s_c, s_uc = np.cumsum(C), np.cumsum(u * C)
        s_u, s_uu = m * (m - 1) / 2, (m - 1) * m * (2 * m - 1) / 6
        den = m * s_uu - s_u ** 2
        with np.errstate(divide="ignore", invalid="ignore"):
            slope = np.where(den > 0, (m * s_uc - s_u * s_c) / den, 0.0)
            drift = np.where((h > 0) & np.isfinite(h), np.abs(slope) * (m - 1) / h, 0.0)
            ok = (m >= p["min_bars"]) & (h <= p["max_height_tr"] * mtr) & (drift <= p["max_drift"])
        if self.pivot:
            ok &= np.isfinite(h) & (h > 0) & on_pivot
        if not ok.any():
            return None
        block = np.concatenate([[0], np.cumsum((hi[1:] != hi[:-1]) | (lo[1:] != lo[:-1]))])
        okk = np.flatnonzero(ok)
        last = okk[np.r_[block[okk][1:] != block[okk][:-1], True]]  # longest per block
        best = None
        for k in last:
            th, tl = self._box_touches(j - int(k), j, hi[k], lo[k], j)
            key = (th + tl, min(th, tl), k)
            if best is None or key > best[0]:
                best = (key, k, th, tl)
        _, k, th, tl = best
        return j - int(k), float(hi[k]), float(lo[k]), th, tl

    def _step_search(self, j):
        p = self.p
        w = self._best_window(j)
        cand = self.candidate
        if w is None:
            if cand is not None:
                cand["outcome"] = "dropped"
                self.candidate = None
            return
        i, hi, lo, th, tl = w
        if cand is None:
            cand = self.candidate = dict(id=len(self.candidates), first_seen=j,
                                         outcome="live", range_id=None)
            self.candidates.append(cand)
        cand.update(last_seen=j, start=i, high=hi, low=lo, touches_high=th, touches_low=tl)
        if (th < p["min_touches"] or tl < p["min_touches"]
                or th + tl < p["min_total_touches"]):
            return
        self.candidate = None
        box = dict(id=len(self.ranges), start=i, end=j, high=hi, low=lo, confirm=j,
                   base_height=hi - lo, confirm_start=i, members=[],
                   confirm_high=hi, confirm_low=lo, merged=0, status="active",
                   break_dir=None, break_bar=None, break_confirm_bar=None,
                   break_grade=None, break_type=None)
        prev = self.ranges[-1] if self.ranges else None
        if prev is not None and self._joins(prev, box):
            gap_h, gap_l = self._gap_ext(prev["end"] + 1, i, hi, lo)
            nh, nl = max(prev["high"], hi, gap_h), min(prev["low"], lo, gap_l)
            if not prev["members"]:
                prev["members"].append(dict(start=prev["start"], end=prev["end"],
                                            high=prev["high"], low=prev["low"]))
            prev["members"].append(dict(start=i, end=j, high=hi, low=lo))
            prev.update(end=j, high=nh, low=nl, base_height=nh - nl,
                        merged=prev["merged"] + 1,
                        status="active", break_dir=None, break_bar=None,
                        break_confirm_bar=None, break_grade=None, break_type=None)
            cand.update(outcome="merged", range_id=prev["id"])
            self.active = prev
            self._event(j, "merged", prev)
            return
        cand.update(outcome="confirmed", range_id=box["id"])
        self.ranges.append(box)
        self.active = box
        self._event(j, "confirmed", box)

    def _joins(self, a, b):
        p = self.p
        if b["start"] - a["end"] - 1 > p["merge_gap_bars"]:
            return False
        overlap = min(a["high"], b["high"]) - max(a["low"], b["low"])
        smaller = min(a["high"] - a["low"], b["high"] - b["low"])
        if overlap < p["merge_overlap"] * smaller:
            return False
        s, e = a["start"], b["end"]
        gh, gl = self._gap_ext(a["end"] + 1, b["start"], b["high"], b["low"])
        hi, lo = max(a["high"], b["high"], gh), min(a["low"], b["low"], gl)
        return hi - lo <= p["max_height_tr"] * self._mean_tr(s, e)

    # ---- output ----------------------------------------------------------
    def ranges_frame(self):
        rows = []
        for b in self.ranges:
            s, e = b["start"], b["end"]
            upto = b["break_confirm_bar"] if b["break_confirm_bar"] is not None else len(self.C) - 1
            th, tl = self._box_touches(s, e, b["high"], b["low"], upto)
            mtr = self._mean_tr(s, e)
            T = self.T
            pe = self.pivot_edges(b)
            rows.append(dict(
                id=b["id"], start_time=T[s], confirm_time=T[b["confirm"]], end_time=T[e],
                high=b["high"], low=b["low"], height=b["high"] - b["low"], mean_tr=mtr,
                height_tr=(b["high"] - b["low"]) / mtr, n_bars=e - s + 1,
                touches_high=th, touches_low=tl,
                drift=_drift(np.asarray(self.C[s:e + 1]), b["high"], b["low"]),
                confirm_high=b["confirm_high"], confirm_low=b["confirm_low"],
                merged=b["merged"], status=b["status"], break_dir=b["break_dir"],
                break_grade=b["break_grade"], break_type=b["break_type"],
                break_time=T[b["break_bar"]] if b["break_bar"] is not None else None,
                break_confirm_time=(T[b["break_confirm_bar"]]
                                    if b["break_confirm_bar"] is not None else None),
                confirm_start_time=T[b["confirm_start"]],
                confirm_touches=self._confirm_marks(b),
                members=[dict(start_time=T[m["start"]], end_time=T[m["end"]],
                              high=m["high"], low=m["low"]) for m in b["members"]],
                pivot_high=pe["high"] if pe else None, pivot_low=pe["low"] if pe else None,
                pivot_high_time=T[pe["high_i"]] if pe else None,
                pivot_low_time=T[pe["low_i"]] if pe else None,
                pivot_start_time=T[pe["start_i"]] if pe else None,
            ))
        return pd.DataFrame(rows, columns=RANGE_COLUMNS)

    def _confirm_marks(self, b):
        """The touches that confirmed box b: [(time, "top"/"bottom", is_half)]."""
        s, c = b["confirm_start"], b["confirm"]
        _, _, marks = self._box_touches(s, c, b["confirm_high"], b["confirm_low"], c, where=True)
        return [(self.T[s + i], edge, half) for i, edge, half in marks]

    def candidates_frame(self):
        T = self.T
        rows = [dict(id=c["id"], first_seen=T[c["first_seen"]], last_seen=T[c["last_seen"]],
                     start_time=T[c["start"]], high=c["high"], low=c["low"],
                     touches_high=c["touches_high"], touches_low=c["touches_low"],
                     outcome=c["outcome"], range_id=c["range_id"]) for c in self.candidates]
        return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def lower_grader(lower_data, bar_minutes, atr_period=21,
                 strong_range_atr=None, strong_body=None):
    """The strong / weak breakout grade of `find_range` / `find_range_box_zz`
    (`find_range._BreakoutGrader`, borrowed as is, user 2026-10-19), on
    `lower_data` (e.g. M1 OHLC under M5 `data`): a close beyond an edge is
    STRONG when, inside that bar's span, a run of same-direction lower candles
    (from one that opens inside the box, or the closing candle alone) ends with
    a close beyond the edge, spans >= `STRONG_RANGE_ATR` (1.5) x the lower ATR
    as of the close before the run, and has a body >= `STRONG_BODY` (0.5) of
    that span. Reads only lower bars inside the bar being graded."""
    from find_range import STRONG_BODY, STRONG_RANGE_ATR, _BreakoutGrader
    g = _BreakoutGrader(_ohlc(lower_data), bar_minutes, atr_period,
                        STRONG_RANGE_ATR if strong_range_atr is None else strong_range_atr,
                        STRONG_BODY if strong_body is None else strong_body)
    return lambda t, up, hi, lo: g.grade(t, up, hi, lo)[0]


def find_trading_range(data, return_all=False, lower_data=None, **params):
    df = _ohlc(data)
    grader = None
    if lower_data is not None:
        minutes = float(df.index.to_series().diff().median() / pd.Timedelta(minutes=1))
        grader = lower_grader(lower_data, minutes)
    tr = TradingRangeTracker(grader=grader, **params)
    for row in zip(df.index, df["open"].to_numpy(float), df["high"].to_numpy(float),
                   df["low"].to_numpy(float), df["close"].to_numpy(float)):
        tr.update(*row)
    ranges = tr.ranges_frame()
    if not return_all:
        return ranges
    return dict(ranges=ranges, candidates=tr.candidates_frame(),
                events=pd.DataFrame(tr.events, columns=EVENT_COLUMNS))
