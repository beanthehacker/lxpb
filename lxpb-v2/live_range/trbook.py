"""Range-box ledger from patterns_pure.find_trading_range, behind BoxBook's interface (user, 2026-10-19).

Only WHERE the boxes come from changes. BoxBook makes its boxes from find_zigzag pivots
(find_range_box_zz's state machine); this book makes them from find_trading_range's
TradingRangeTracker, fed the same closed M5 bars and grading breakouts with BoxBook's own M1
Grader. Everything downstream is BoxBook's, unchanged: one Reader per box opens an EPISODE on
a strong breakout and follows it to its retest, and active_episodes / forming / retested_by /
the engine's seats and both playbooks read those Readers exactly as they do for zigzag boxes.

The tracker's events map onto boxes like this:

  confirmed  a new box. Its seed edges are the tracker's edges at confirmation, its Reader reads
             from the next bar. Chart pivots are the touches that confirmed it (top = crest,
             bottom = trough).
  widened    the tracker's live box moved an edge: appended to box["widen"], which the Reader
             applies from the next bar (as a zigzag pivot widening does).
  broken     status "broken", end_j = the bar that broke it; break_i / break_t = the first close
             out. As for zigzag boxes, the Reader keeps going while a breakout waits for its retest.
  merged     RENEWAL: a new box B folded into the earlier box A (the tracker's merge: within
             merge_gap_bars, overlapping, joined box still short enough). A is a NEW GENERATION:
             a new box id (so new seats, user 2026-10-19) with a fresh Reader on the merged edges,
             reading from this bar on. The old generation keeps its Reader until its own
             episodes are done. Generations share `root` (the first generation's id); the engine
             never links two generations of one range in its H1 range merge.

Every box counts as a range (the tracker only hands over confirmed ones); BoxBook's
6-candle length rule still applies to each episode.
"""
from find_trading_range import TradingRangeTracker, _touches

from .boxes import BoxBook, Reader


class TradingRangeBook(BoxBook):
    def __init__(self, **tracker_params):
        super().__init__()
        self.tr = TradingRangeTracker(grader=lambda t, up, hi, lo: self.grader.grade(t, up, hi, lo)[0],
                                      **tracker_params)
        self.cur = {}        # tracker box id -> its current generation (a box dict here)

    # -- feeding ------------------------------------------------------------------------------
    def on_m5_close(self, t, o, h, l, c, levels5):
        """A closed M5 bar. `levels5` is the M5 LevelBook, ALREADY advanced for this bar."""
        self._prov = []
        self.m5.append(t, o, h, l, c)
        j = len(self.m5) - 1
        events = self.tr.update(t, o, h, l, c)
        self._track_live(j)
        for ev in events:
            tb = next(b for b in self.tr.ranges if b["id"] == ev["range_id"])
            if ev["event"] == "confirmed":
                self.cur[tb["id"]] = self._open(tb, j, root=None, gen=1)
            elif ev["event"] == "merged":
                old = self.cur[tb["id"]]
                new = self._open(tb, j, root=old["root"], gen=old["gen"] + 1)
                old["renewed_into"] = new["id"]
                self.cur[tb["id"]] = new
            elif ev["event"] == "broken":
                self._broken(self.cur[tb["id"]], tb, j)
        for bid in list(self.readers):
            r = self.readers[bid]
            b = r.box
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
            if res == "done" or r.finished:
                self._drop(bid)
        self._refresh_trigger()

    def _track_live(self, j):
        """The tracker's live box widened on this bar: record it as the box's widening."""
        tb = self.tr.active
        b = self.cur.get(tb["id"]) if tb is not None else None
        if b is None or b["status"] != "open" or b["confirm_j"] == j:
            return
        if tb["high"] != b["high"]:
            b["high"] = tb["high"]
            b["widen"].append((j, "high", tb["high"]))
        if tb["low"] != b["low"]:
            b["low"] = tb["low"]
            b["widen"].append((j, "low", tb["low"]))
        b["last_i"] = tb["end"]

    def _open(self, tb, j, root, gen):
        m5, s = self.m5, tb["start"]
        hi, lo = tb["high"], tb["low"]
        p = self.tr.p
        _, _, marks = _touches(self.tr.H[s:j + 1], self.tr.L[s:j + 1], hi, lo, p["touch_frac"],
                               p["max_half_touches"], where=True, running=p["running_touches"])
        crests, troughs = [], []
        for i, edge, _half in marks:
            if edge == "top":
                self.pivots.append(("crest", s + i, m5.h[s + i], j))
                crests.append(m5.h[s + i])
            else:
                self.pivots.append(("trough", s + i, m5.l[s + i], j))
                troughs.append(m5.l[s + i])
        b = dict(id=len(self.boxes), start_i=s, start_kind=None, confirm_j=j, last_i=j,
                 high=hi, low=lo, seed_high=hi, seed_low=lo, n=len(marks), crests=crests,
                 troughs=troughs, widen=[], status="open", end_j=None, merged_into=None,
                 break_dir=None, break_i=None, break_price=None, break_t=None,
                 start_t=m5.t[s], confirm_t=m5.t[j], gen=gen, renewed_into=None,
                 detector="trading_range", tracker_id=tb["id"])
        b["root"] = b["id"] if root is None else root
        self.boxes.append(b)
        self.readers[b["id"]] = Reader(b, self.widen_pct)
        self.events.append(("box_open", b))
        return b

    def _broken(self, b, tb, j):
        k = tb["break_bar"]
        b.update(status="broken", end_j=j, break_dir=tb["break_dir"], break_i=k,
                 break_price=self.m5.c[k], break_t=self.m5.t[k], last_i=k - 1,
                 break_grade=tb.get("break_grade"))
        self.pivots.append(("crest" if tb["break_dir"] == "up" else "trough", k,
                            self.m5.h[k] if tb["break_dir"] == "up" else self.m5.l[k], j))
        self.events.append(("box_broken", b))

    # -- queries ---------------------------------------------------------------------------------
    def is_range(self, b):
        """Every box here is a confirmed range (the tracker hands over nothing else)."""
        return b["status"] in ("open", "broken")
