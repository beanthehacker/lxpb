"""Live LXPB level book: lxpb.advance_one_bar, one bar at a time.

lxpb keeps its levels in `touch_lv1` while they are broken out and waiting for a retest, which
is exactly the set the playbooks pick from. This wrapper feeds it bars, and records what
happened to every level that left that list: retested at a bar, or silently consumed (touched in
the bar right after the breakout, which can never be a retest).
"""
import collections
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.abspath(os.path.join(_HERE, "..", "..")), os.path.abspath(os.path.join(_HERE, ".."))):
    if p not in sys.path:
        sys.path.insert(0, p)
import lxpb  # noqa: E402

_UTC = "UTC"
_Row = collections.namedtuple("Row", "Index open high low close pp_hammer pp_shooting_star")


def key(lv):
    """Identity of a level: type, price, formation time (ns)."""
    return (lv["type"], round(float(lv["price"]), 4), pd.Timestamp(lv["formation_time"]).value)


class LevelBook:
    def __init__(self, name="levels", verdicts=None):
        """`verdicts` (optional): {bar time ns: (hammer, shooting_star)} precomputed with patterns-pure
        over the same bars, so a replay does not pay pandas for one tiny frame per bar. A bar
        missing from it is judged live, one bar at a time, through lxpb.iter_bars."""
        self.name = name
        self.verdicts = verdicts or {}
        self.state = lxpb.new_state()
        self.fate = {}            # key -> ("retested"|"consumed", bar time ns)
        self.bars = {}            # bar time ns -> (o, h, l, c): formation candles for stops
        self.broke = []           # every level that broke out, oldest first (stops read P1 candles)
        self.broke_by_bar = {}    # breakout bar time ns -> the levels that broke on it (the stop trail reads these)
        self._seen = 0            # state["retests"] rows already looked at
        self.n_bars = 0

    def on_bar(self, t_ns, o, h, l, c):
        """Advance by one CLOSED bar. Returns [(event, level)] with event 'retest' / 'consumed'."""
        ts = pd.Timestamp(t_ns, unit="ns", tz=_UTC)
        before = {key(lv): lv for lv in self.state["touch_lv1"]}
        v = self.verdicts.get(t_ns)
        if v is not None:
            lxpb.advance_one_bar(self.state, _Row(ts, o, h, l, c, v[0], v[1]))
        else:
            frame = pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]},
                                 index=pd.DatetimeIndex([ts]))
            for bar in lxpb.iter_bars(self.state, frame):
                lxpb.advance_one_bar(self.state, bar)
        self.n_bars += 1
        self.bars[t_ns] = (o, h, l, c)
        events = []
        rows = self.state["retests"]
        retested = set()
        for row in rows[self._seen:]:
            k = key(row)
            retested.add(k)
            self.fate[k] = ("retested", t_ns)
            events.append(("retest", row))
        self._seen = len(rows)
        for lv in self.state["touch_lv1"]:
            if key(lv) not in before:
                rec = {"type": lv["type"], "price": lv["price"], "formation_time": lv["formation_time"],
                       "breakout_time": lv["breakout_time"], "breakout_high": lv["breakout_high"],
                       "breakout_low": lv["breakout_low"], "is_spike": bool(lv.get("is_spike")),
                       "is_swing": bool(lv.get("is_swing"))}
                self.broke.append(rec)
                self.broke_by_bar.setdefault(pd.Timestamp(lv["breakout_time"]).value, []).append(rec)
        alive = {key(lv) for lv in self.state["touch_lv1"]}
        for k, lv in before.items():
            if k not in alive and k not in retested:
                self.fate[k] = ("consumed", t_ns)
                events.append(("consumed", lv))
        return events

    def live(self, level_type=None):
        """Levels broken out and still waiting for a retest, as of the last closed bar."""
        lv = self.state["touch_lv1"]
        return [x for x in lv if level_type is None or x["type"] == level_type]

    def forming_bar_count(self):
        """lxpb's bar counter for the bar now forming (closed bars + 1)."""
        return self.state["bar_count"] + 1

    def can_retest(self, lv):
        """A touch on the forming bar is a retest only if more than one bar has passed since
        the breakout bar; a touch before that silently consumes the level."""
        return self.forming_bar_count() - lv["breakout_bar_count"] > lxpb.MIN_BARS_BEFORE_RETEST

    def pending_break(self, level_type, h, l, c):
        """Levels of `level_type` that have formed but not broken out which a bar with this high/low/close
        WOULD break out (lxpb phase 2), as lxpb's own levels with their P0 kind worked out as of now.
        Used to read a bar still forming "as if it closed here"."""
        out = []
        for lv in self.state["touch_lv0"]:
            if lv["type"] != level_type:
                continue
            p = lv["price"]
            if level_type == "LHPB":
                if h < p:
                    continue
                broke = l > p or c > p
                if not broke and h == p:
                    continue
            else:
                if l > p:
                    continue
                broke = h < p or c < p
                if not broke and l == p:
                    continue
            if broke:
                out.append({**lv, "p0_kind": lxpb.p0_kind(lv["is_spike"], lv["is_swing"],
                                                           lxpb._efficiency_ratio(lv.get("er_closes", ())))})
        return out
