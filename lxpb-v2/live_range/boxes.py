"""Incremental range-box ledger: patterns_pure.find_range_box_zz, one bar at a time.

The batch detector re-reads days of history for every question. This keeps the same state
machine alive instead: patterns_pure.find_zigzag.ZigZag on M5 bars (order inside a bar from the M1 bars), the box
state machine fed one pivot at a time, and one Reader per box that walks the box bar by bar
and opens an EPISODE when a bar closes strongly beyond an edge (strong = graded on the M1 bars).
Nothing here looks ahead.

Feeding:   on_m1_close(...)  every closed M1 bar;  on_m5_close(...)  every closed M5 bar,
           after the M5 LevelBook has advanced for that bar.
Forming:   forming(...) reads the M5 bar still being built "as if it closed right here", on a
           copy of each reader, and returns provisional episodes (nothing is committed).
"""
import bisect
import math

from find_range_box_zz import extends_box
from find_zigzag import ATR_MULT, ATR_PERIOD, MIN_BARS, ZigZag

from .series import Series
from .levels import key as level_key

WIDEN_PCT = 20.0
MERGE_TOL = 2.0
MIN_EDGE_TOUCHES = 2
EDGE_TOL = 2.0
STRONG_RANGE_ATR = 1.5
STRONG_BODY = 0.5
M5_NS = 5 * 60 * 10**9
M1_NS = 60 * 10**9


class Grader:
    """Strong / weak grading of a break on the M1 bars inside one M5 bar
    (patterns_pure.find_range._BreakoutGrader)."""

    def __init__(self, m1, span_ns, range_atr=STRONG_RANGE_ATR, body=STRONG_BODY):
        self.m1, self.span, self.range_atr, self.body = m1, span_ns, range_atr, body

    def grade(self, t0, up, box_hi, box_lo):
        m1 = self.m1
        O, H, L, C, atr = m1.o, m1.h, m1.l, m1.c, m1.atr
        a, b = m1.left(t0), m1.left(t0 + self.span)
        first = (math.nan, math.nan)
        for m in range(a, b):
            same_m = (C[m] > O[m]) if up else (C[m] < O[m])
            if not (same_m and ((C[m] > box_hi) if up else (C[m] < box_lo))):
                continue
            run = m
            while run - 1 >= 1 and ((C[run - 1] > O[run - 1]) if up else (C[run - 1] < O[run - 1])):
                run -= 1
            for s in range(run, m + 1):
                if not (s == m or box_lo <= O[s] <= box_hi):
                    continue
                span = max(H[s:m + 1]) - min(L[s:m + 1])
                if span <= 0 or not atr[s - 1] > 0:
                    continue
                ra, bd = span / atr[s - 1], abs(C[m] - O[s]) / span
                if math.isnan(first[0]):
                    first = (ra, bd)
                if ra >= self.range_atr and bd >= self.body:
                    return "strong", ra, bd
        return "weak", first[0], first[1]


PLAIN = "plain-P0"


def _label(up, kind):
    return f"breakout-{'up' if up else 'below'}-{kind}"


class Reader:
    """The bar-by-bar State read of one box (find_range_box_zz._read_states), as an object.

    step() is called once per closed M5 bar after the box's confirm bar. It returns
    'done' once nothing more can happen to this box. Episodes (one per edge, strong
    breakouts only) live in self.episodes. The only departure from the batch read is where
    the retest comes from: the batch knows the future retest time of the box's waiting M5
    levels from the finished ledger; here the reader watches those levels and notices when one is
    retested (or when all of them are gone without a retest: the edge is then done, "no-level")."""

    __slots__ = ("box", "hi", "lo", "lim_up", "lim_dn", "w", "out", "state", "failed_t",
                 "post", "used", "episodes", "finished", "widen_pct")

    def __init__(self, box, widen_pct):
        sh, sl = box["seed_high"], box["seed_low"]
        self.box = box
        self.widen_pct = widen_pct
        self.lim_up = sh + widen_pct / 100 * (sh - sl)
        self.lim_dn = sl - widen_pct / 100 * (sh - sl)
        self.hi, self.lo = sh, sl
        self.w = 0
        self.out = None
        self.state = "ranging"
        self.failed_t = None
        self.post = False
        self.used = set()
        self.episodes = []
        self.finished = False

    def clone(self):
        r = Reader.__new__(Reader)
        for s in Reader.__slots__:
            setattr(r, s, getattr(self, s))
        r.used = set(self.used)
        r.episodes = [dict(e) for e in self.episodes]
        r.out = None if self.out is None else dict(self.out)
        if r.out is not None and r.out.get("ep") is not None:
            k = next(i for i, e in enumerate(self.episodes) if e is self.out["ep"])
            r.out["ep"] = r.episodes[k]
            r.out["watch"] = list(self.out["watch"])
        return r

    # -- waiting levels of the M5 ledger ---------------------------------------------------
    def _watch(self, levels5, up, spec=None):
        """Keys of the M5 levels a breakout of this edge waits on: LHPB (up) / LLPB (below) inside the box,
        already broken out and untouched. Plain-P0s do not count (the reports' range-box ledger leaves them
        out). `spec` = (high, low, close) of a bar still forming: levels that bar would break out count too."""
        want = "LHPB" if up else "LLPB"
        lv = [x for x in levels5.live(want) if x["p0_kind"] != PLAIN]
        if spec is not None:
            lv += [x for x in levels5.pending_break(want, *spec) if x["p0_kind"] != PLAIN]
        return [level_key(x) for x in lv if self.lo <= x["price"] <= self.hi]

    def _retest_time(self, levels5, keys):
        times = [levels5.fate[k][1] for k in keys if k in levels5.fate and levels5.fate[k][0] == "retested"]
        return min(times) if times else None

    def _alive(self, levels5, keys):
        return [k for k in keys if k not in levels5.fate]

    def step(self, m, t, O, H, L, C, grader, levels5, speculative=False):
        """One M5 bar (index m, start time t, OHLC). Returns 'done' / None."""
        box = self.box
        stop = box["end_j"]
        if self.out is None and stop is not None and m > stop and not self.post:
            self.finished = True
            return "done"
        wid = box["widen"]
        while self.w < len(wid) and wid[self.w][0] < m:
            _, side, price = wid[self.w]
            if side == "high":
                self.hi = price
            else:
                self.lo = price
            self.w += 1
        inside = self.lo <= C <= self.hi
        if self.out is not None:
            up = self.out["up"]
            if self.out["strong"]:       # confirmed: never invalidated, only a retest ends the wait
                ep = self.out["ep"]
                watch = self.out["watch"]
                rt = self._retest_time(levels5, watch)
                if rt is not None and rt <= t:
                    ep.update(state=_label(up, "retested"), retest_time=rt, done=True,
                              done_reason="retested", done_time=t)
                    self.out, self.post = None, True
                elif watch and not self._alive(levels5, watch) and not speculative:
                    ep.update(state=_label(up, "no-level"), done=True, done_reason="no-level", done_time=t)
                    self.out, self.post = None, True
                return None
            if (C < self.lo) if up else (C > self.hi):      # weak one ran through to the far edge
                self.out = None
            else:
                ext = self.out["ext"]
                self.out["ext"] = max(ext, H) if up else min(ext, L)
                if inside and (H > self.lim_up if up else L < self.lim_dn):
                    self.state, self.failed_t = ("breakout-above-failed" if up else "breakout-below-failed"), t
                    self.out, self.post = None, True
                    return None
                if inside and (self.out["ext"] <= self.lim_up if up else self.out["ext"] >= self.lim_dn):
                    if up:
                        self.hi = max(self.hi, self.out["ext"])
                    else:
                        self.lo = min(self.lo, self.out["ext"])
                    self.out, self.state = None, "ranging"
                    return None
                if (C > self.hi if up else C < self.lo) and grader.grade(t, up, self.hi, self.lo)[0] == "strong":
                    self.out["strong"] = True
                    self._confirm(up, t, levels5, (H, L, C) if speculative else None)
                return None
        if not self.post:
            if H > self.lim_up and inside:
                self.state, self.failed_t, self.post = "breakout-above-failed", t, True
                return None
            if L < self.lim_dn and inside:
                self.state, self.failed_t, self.post = "breakout-below-failed", t, True
                return None
        up = C > self.hi
        if (up or C < self.lo):
            if self.post and up in self.used:
                return None                  # that edge already had its episode
            strong = grader.grade(t, up, self.hi, self.lo)[0] == "strong"
            if self.post and not strong:
                return None                  # after the first outcome only strong breakouts count
            self.out = dict(up=up, ext=H if up else L, strong=strong, ep=None, watch=[])
            if strong:
                self._confirm(up, t, levels5, (H, L, C) if speculative else None)
            else:
                self.state = "weak-breakout"
        return None

    def _confirm(self, up, t, levels5, spec=None):
        """A strong breakout closes on the bar at `t`: open its episode with the box's waiting
        M5 levels; with none, the edge is done on the spot."""
        watch = self._watch(levels5, up, spec)
        self.used.add(up)
        ep = {"box_id": self.box["id"], "direction": "up" if up else "below", "edge": "upper" if up else "lower",
              "breakout_time": t, "state": _label(up, "waiting-for-retest" if watch else "no-level"),
              "retest_time": None, "done": False, "done_reason": None, "done_time": None,
              "hi": self.hi, "lo": self.lo, "provisional": False}
        self.episodes.append(ep)
        self.out["ep"], self.out["watch"] = ep, watch
        if not self.post:
            self.state = ep["state"]
        if not watch:
            ep.update(done=True, done_reason="no-level", done_time=t)
            self.out, self.post = None, True

    def active_episodes(self):
        return [e for e in self.episodes if not e["done"]]


class BoxBook:
    def __init__(self, atr_mult=ATR_MULT, atr_period=ATR_PERIOD, widen_pct=WIDEN_PCT,
                 merge_tol=MERGE_TOL, min_edge_touches=MIN_EDGE_TOUCHES, edge_tol=EDGE_TOL,
                 min_bars=MIN_BARS):
        self.widen_pct, self.merge_tol = widen_pct, merge_tol
        self.min_edge_touches, self.edge_tol = min_edge_touches, edge_tol
        self.atr_period = atr_period
        self.m5, self.m1 = Series(atr_period), Series(atr_period)
        self.grader = Grader(self.m1, M5_NS)
        self.zz = ZigZag(atr_mult, atr_period, min_bars)
        self.pivots = []
        self.boxes = []
        self.live = []            # open boxes, earliest-started first
        self.readers = {}         # box id -> Reader (not merged, not finished)
        self.events = []          # (kind, payload) since the last drain()
        self._prov = []           # provisional episodes of the forming bar
        self._trig_hi = math.inf  # smallest reader hi / largest reader lo: cheap forming-bar trigger
        self._trig_lo = -math.inf

    # -- feeding ------------------------------------------------------------------------------
    def on_m1_close(self, t, o, h, l, c):
        self.m1.append(t, o, h, l, c)

    def _order(self, t5):
        """+1 if the bar's high came before its low on the M1 bars inside it, -1 if the low came
        first, 0 if it cannot be told (find_zigzag.intrabar_order)."""
        m1 = self.m1
        a, b = m1.left(t5), m1.left(t5 + M5_NS)
        if b <= a:
            return 0
        H, L = m1.h[a:b], m1.l[a:b]
        mh, ml = max(H), min(L)
        fh = a + H.index(mh)
        fl = a + L.index(ml)
        return (fl > fh) - (fl < fh)

    def on_m5_close(self, t, o, h, l, c, levels5):
        """A closed M5 bar. `levels5` is the M5 LevelBook, ALREADY advanced for this bar."""
        self._prov = []
        m5 = self.m5
        m5.append(t, o, h, l, c)
        j = len(m5) - 1
        if j == 0:
            self.zz.step(h, l, m5.atr[0])
            return
        for piv in self.zz.step(h, l, m5.atr[j], self._order(t)):
            self._on_pivot(piv)
        for bid in list(self.readers):
            r = self.readers[bid]
            b = r.box
            if b["status"] == "merged":
                self._drop(bid)
                continue
            if j <= b["confirm_j"]:
                continue
            was = len(r.episodes)
            res = r.step(j, t, o, h, l, c, self.grader, levels5)
            for e in r.episodes[was:]:
                self.events.append(("episode_open", e))
            for e in r.episodes:
                if e["done"] and not e.get("_reported"):
                    e["_reported"] = True
                    self.events.append(("episode_done", e))
            if res == "done" or (r.finished):
                self._drop(bid)
        self._refresh_trigger()

    def _drop(self, bid):
        self.readers.pop(bid, None)

    # -- the box state machine (find_range_box_zz._boxes_from_pivots), one pivot --------------
    def _twin(self, hi, lo, others):
        for o in others:
            if abs(hi - o["high"]) <= self.merge_tol and abs(lo - o["low"]) <= self.merge_tol:
                return o
        return None

    def _break_bar(self, b, j):
        """Time (ns) of the box's break candle, as find_range_box_zz reports `break_time`: the first M5 bar after the
        box's last inside pivot, up to the bar that confirms the breaking pivot (`j`), that OPENS or CLOSES beyond the
        broken edge; the breaking pivot's own bar when only wicks got out."""
        m5, up = self.m5, b["break_dir"] == "up"
        k = b["break_i"]
        for m in range(b["last_i"] + 1, j + 1):
            if (m5.o[m] > b["high"] or m5.c[m] > b["high"]) if up else (m5.o[m] < b["low"] or m5.c[m] < b["low"]):
                k = m
                break
        return m5.t[k]

    def _end(self, b, status, j):
        b["status"], b["end_j"] = status, j
        self.events.append(("box_" + status, b))

    def _on_pivot(self, piv):
        kind, i, price, j = piv
        q = len(self.pivots)
        self.pivots.append(piv)
        wp = self.widen_pct / 100
        for pos, b in enumerate(list(self.live)):
            if kind == "crest":
                over, beyond = price - b["seed_high"], price > b["high"]
            else:
                over, beyond = b["seed_low"] - price, price < b["low"]
            m5 = self.m5
            if beyond and over > wp * (b["seed_high"] - b["seed_low"]) and not extends_box(
                    kind, m5.o[i], m5.h[i], m5.l[i], m5.c[i], b["high"], b["low"], b["seed_high"], b["seed_low"],
                    price, self.widen_pct):
                hi, lo = max(b["high"], price), min(b["low"], price)
                outer = self._twin(hi, lo, [o for o in self.live[:pos] if o["status"] == "open"])
                if outer is not None:
                    self._end(b, "merged", j)
                    b["merged_into"] = outer["id"]
                else:
                    self._end(b, "broken", j)
                    b.update(break_dir="up" if kind == "crest" else "down", break_i=i, break_price=price)
                    b["break_t"] = self._break_bar(b, j)
                continue
            if beyond:
                if kind == "crest":
                    b["high"] = price
                else:
                    b["low"] = price
                b["widen"].append((j, "high" if kind == "crest" else "low", price))
            b["n"] += 1
            b["crests" if kind == "crest" else "troughs"].append(price)
            b["last_i"] = i
            outer = self._twin(b["high"], b["low"], [o for o in self.live[:pos] if o["status"] == "open"])
            if outer is not None:
                self._end(b, "merged", j)
                b["merged_into"] = outer["id"]
        self.live = [b for b in self.live if b["status"] == "open"]
        if q > 0:
            k0, i0, p0, _ = self.pivots[q - 1]
            if kind == "trough":
                chi, tlo = p0, price
            else:
                chi, tlo = price, p0
            b = dict(id=len(self.boxes), start_i=i0, start_kind=k0, confirm_j=j, last_i=i,
                     high=chi, low=tlo, seed_high=chi, seed_low=tlo, n=2, crests=[chi], troughs=[tlo],
                     widen=[], status="open", end_j=None, merged_into=None,
                     break_dir=None, break_i=None, break_price=None, break_t=None,
                     start_t=self.m5.t[i0], confirm_t=self.m5.t[j])
            self.boxes.append(b)
            outer = self._twin(chi, tlo, self.live)
            if outer is not None:
                self._end(b, "merged", j)
                b["merged_into"] = outer["id"]
            else:
                self.live.append(b)
                self.readers[b["id"]] = Reader(b, self.widen_pct)
                self.events.append(("box_open", b))

    # -- queries ---------------------------------------------------------------------------------
    def touches(self, b):
        """(crests within edge_tol of the top edge, troughs within edge_tol of the bottom edge)."""
        return (sum(abs(p - b["high"]) <= self.edge_tol for p in b["crests"]),
                sum(abs(p - b["low"]) <= self.edge_tol for p in b["troughs"]))

    def is_range(self, b):
        """A box counts as a range once it has MIN_EDGE_TOUCHES pivots at each edge."""
        if b["status"] not in ("open", "broken"):
            return False
        t, u = self.touches(b)
        return t >= self.min_edge_touches and u >= self.min_edge_touches

    def active_episodes(self, include_forming=True):
        """Episodes still waiting for their retest, on boxes that count as ranges, as dicts that
        carry the box: {'box': b, **episode}. Provisional ones come from the forming bar."""
        out = []
        for r in self.readers.values():
            if r.box["status"] == "merged" or not self.is_range(r.box):
                continue
            for e in r.active_episodes():
                out.append({**e, "box": r.box})
        if include_forming:
            for e in self._prov:
                if self.is_range(e["box"]):
                    out.append(e)
        return out

    def retested_by(self, h, l, levels5):
        """The episodes still waiting that a bar with this high/low would end by retesting one of the box's waiting M5
        levels - what the reports see when the M5 bar holding the touch is "closed right there"."""
        out, live = set(), {}
        for r in self.readers.values():
            o = r.out
            if o is None or not o.get("strong") or o.get("ep") is None or o["ep"]["done"]:
                continue
            up = o["up"]
            want = "LHPB" if up else "LLPB"
            if want not in live:
                live[want] = {level_key(x): x for x in levels5.live(want)}
            for k in o["watch"]:
                lv = live[want].get(k)
                if lv is None or not levels5.can_retest(lv):
                    continue
                if (l <= lv["price"]) if up else (h >= lv["price"]):
                    out.add((o["ep"]["box_id"], o["ep"]["direction"]))
                    break
        return out

    def drain(self):
        ev, self.events = self.events, []
        return ev

    # -- the forming M5 bar ----------------------------------------------------------------------
    def _refresh_trigger(self):
        """Cheap exit for forming(): the lowest top edge among readers that could still open an up episode, the
        highest bottom edge among those that could open a below one (a reader with a weak breakout out can
        do either)."""
        his, los = [], []
        for r in self.readers.values():
            if r.finished:
                continue
            if r.out is not None or True not in r.used:
                his.append(r.hi)
            if r.out is not None or False not in r.used:
                los.append(r.lo)
        self._trig_hi = min(his) if his else math.inf
        self._trig_lo = max(los) if los else -math.inf

    def forming(self, t5, o, h, l, c, levels5, m1_partial=None, store=True):
        """Read the M5 bar now forming as if it closed at `c`. Returns the provisional
        episodes (also kept for active_episodes). `m1_partial` is the M1 bar still forming.
        Cheap exit unless the price is beyond some box edge."""
        if not (c > self._trig_hi or c < self._trig_lo) or not len(self.m5):
            if store and self._prov:
                self._prov = []
            return self._prov if store else []
        pushed = False
        if m1_partial is not None:
            self.m1.append(*m1_partial)
            pushed = True
        j = len(self.m5)
        prov = []
        try:
            for r in self.readers.values():
                b = r.box
                if r.finished or b["status"] == "merged" or j <= b["confirm_j"]:
                    continue
                if r.out is None and not ((c > r.hi and True not in r.used) or (c < r.lo and False not in r.used)):
                    continue
                if r.out is not None and not (c > r.hi or c < r.lo):
                    continue
                c2 = r.clone()
                was = len(c2.episodes)
                c2.step(j, t5, o, h, l, c, self.grader, levels5, speculative=True)
                for e in c2.episodes[was:]:
                    if not e["done"]:
                        prov.append({**e, "box": b, "provisional": True})
        finally:
            if pushed:
                self.m1.pop()
        if store:
            self._prov = prov
        return prov
