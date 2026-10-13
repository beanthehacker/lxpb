"""Live range-box playbook engine: ticks in, orders and trades out.

State it keeps (nothing is recomputed from history):
  * M1 / M5 bars built from the tick stream,
  * the M5 LXPB level book (what a box's breakout waits on) and the box ledger (BoxBook),
  * the trading LXPB level book (bar_minutes bars) that supplies the prices to trade,
  * a structure zigzag and swing pivots on the trading bars (targets, run-in, swerve).

On every tick: fills and exits are checked at once; once a second the forming M5 bar is read
as if it closed (provisional breakouts); on every closed bar the books advance. After any change
the working orders are recomputed from the current state and diffed against the ones already
resting: new ones armed, stale ones pulled, each with a reason in the event log.

The playbooks (one trade per box seat; first fill takes the seat):
  fade      the breakout is confirmed and waiting for its retest; rest an order on a live LXPB
            level BEYOND the breakout edge (LLPB above an up breakout, LHPB below a down one).
  go-with   rest an order on a live LXPB level INSIDE the box or within GO_WITH_EDGE_TOL beyond
            the breakout-side edge (LHPB after an up breakout, LLPB after a below one).
Same-type live levels within CLUSTER_PTS of each other are one order at the extreme price; a swing
on that price moves it (swerve). At the touch the order is pulled, not filled, if the run-in
into it is weak, a fade's breakout is not the run-in's own, a stop or target cannot be planned,
or the time is in a blackout.
"""
import bisect
import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import pandas as pd

from .boxes import BoxBook, M1_NS, M5_NS
from .levels import LevelBook, key as level_key
from .series import Series
from .zigzag import StructZigzag

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
S_NS = 10**9


@dataclass
class Params:
    bar_minutes: int = 1
    cluster_pts: float = 2.0
    go_with_edge_tol: float = 2.0
    swerve: bool = True
    swerve_tol: float = 1.0
    swerve_max_move: float = 10.0
    swerve_lookback_h: float = 24.0
    swing_k: int = 2
    run_in_mult: float = 4.0
    run_in_baseline: int = 30
    zz_threshold: float = 3.0
    zz_min_bars: int = 3
    tick: float = 0.25
    min_stop: float = 3.0
    stop_radius: float = 10.0
    wider_stop_lookback_h: float = 24.0
    min_target: float = 0.25
    max_target: float = 50.0
    news_window: tuple = (5 * 3600 + 29 * 60 + 55, 5 * 3600 + 30 * 60 + 40)   # PT seconds of day
    eod_flat: int = 12 * 3600 + 44 * 60       # PT: flatten at 12:44
    no_entry_from: int = 12 * 3600 + 45 * 60  # PT: no new entry 12:45 .. reopen
    reopen: int = 15 * 3600


def _pt_sod(t_ns):
    ts = pd.Timestamp(t_ns, unit="ns", tz="UTC").tz_convert(PT)
    return ts.hour * 3600 + ts.minute * 60 + ts.second, ts


class Engine:
    def __init__(self, params=None, arm_from_ns=0, on_event=None, verdicts5=None, verdicts_tb=None):
        self.p = params or Params()
        self.arm_from = arm_from_ns          # warm-up: nothing is armed before this time
        self.on_event = on_event
        self.events = []
        # bars
        bm = self.p.bar_minutes
        self.tb_ns = bm * M1_NS
        self.m1f = None                      # [slot, o, h, l, c] forming bars
        self.tbf = None
        self.m5f = None
        self.tb = Series(21)                 # closed trading bars
        # books
        self.box = BoxBook()
        self.lv5 = LevelBook("m5", verdicts5)
        self.lv = LevelBook("trade", verdicts_tb)
        self.zz = StructZigzag(self.p.zz_threshold, self.p.zz_min_bars)
        self.swing_hi, self.swing_lo = [], []    # confirmed fractal swings (time_ns, price)
        self.dead = set()                    # trade levels touched on the tick before they could be retested
        # orders / trades
        self.orders = {}
        self.seats = {}                      # (box_id, direction, playbook) -> trade id
        self.trades = []
        self.open = []
        self._oid = 0
        self._blocked = set()
        self._last_sec = -1
        self._prov_keys = frozenset()
        self.last_t = 0
        self.last_px = None
        self._sell_min = math.inf            # cheapest working sell / dearest working buy: cheap per-tick filter
        self._buy_max = -math.inf
        self._next_exit = math.inf
        self._lo_t, self._hi_t = -math.inf, math.inf

    # ------------------------------------------------------------------ events
    def log(self, kind, t, **kw):
        ev = {"t": t, "kind": kind, **kw}
        if t >= self.arm_from or kind.startswith("trade") or kind.startswith("fill"):
            self.events.append(ev)
            if self.on_event:
                self.on_event(ev)
        return ev

    # ------------------------------------------------------------------ tick entry point
    def on_tick(self, t, px):
        m1s = t // M1_NS
        f = self.m1f
        if f is None:
            self.m1f = [m1s, px, px, px, px]
            self.tbf = [t // self.tb_ns, px, px, px, px]
            self.m5f = [t // M5_NS, px, px, px, px]
        else:
            if m1s != f[0]:
                self._roll(t, m1s, px)
                f = self.m1f
            else:
                if px > f[2]:
                    f[2] = px
                elif px < f[3]:
                    f[3] = px
                f[4] = px
                g, h = self.tbf, self.m5f
                if px > g[2]:
                    g[2] = px
                elif px < g[3]:
                    g[3] = px
                g[4] = px
                if px > h[2]:
                    h[2] = px
                elif px < h[3]:
                    h[3] = px
                h[4] = px
        self.last_t, self.last_px = t, px
        if self.open and (px <= self._lo_t or px >= self._hi_t or t >= self._next_exit):
            self._check_exits(t, px)
        if px >= self._sell_min or px <= self._buy_max:
            self._check_orders(t, px)
        sec = t // S_NS
        if sec != self._last_sec:
            self._last_sec = sec
            self._second(t, px)

    def _roll(self, t, m1s, px):
        """The tick belongs to a new minute: close what is done, open the new forming bars."""
        f, g, h = self.m1f, self.tbf, self.m5f
        tb_slot, m5_slot = t // self.tb_ns, t // M5_NS
        self._close_m1(f)
        self.m1f = [m1s, px, px, px, px]
        if tb_slot != g[0]:
            self._close_tb(g, t)
            self.tbf = g = [tb_slot, px, px, px, px]
        else:
            if px > g[2]:
                g[2] = px
            elif px < g[3]:
                g[3] = px
            g[4] = px
        if m5_slot != h[0]:
            self._close_m5(h, t)
            self.m5f = [m5_slot, px, px, px, px]
        else:
            if px > h[2]:
                h[2] = px
            elif px < h[3]:
                h[3] = px
            h[4] = px

    # ------------------------------------------------------------------ bar closes
    def _close_m1(self, f):
        self.box.on_m1_close(f[0] * M1_NS, f[1], f[2], f[3], f[4])

    def _close_tb(self, g, t):
        tt = g[0] * self.tb_ns
        self.tb.append(tt, g[1], g[2], g[3], g[4])
        self.zz.step(tt, g[2], g[3])
        self._swings()
        for kind, lv in self.lv.on_bar(tt, g[1], g[2], g[3], g[4]):
            self.dead.discard(level_key(lv))
            self.log("level_" + kind, t, level=level_key(lv)[:2], price=lv["price"], type=lv["type"])
        self._refresh(t, "bar")

    def _close_m5(self, h, t):
        tt = h[0] * M5_NS
        self.lv5.on_bar(tt, h[1], h[2], h[3], h[4])
        self.box.on_m5_close(tt, h[1], h[2], h[3], h[4], self.lv5)
        for kind, payload in self.box.drain():
            if kind in ("episode_open", "episode_done"):
                b = payload
                self.log(kind, t, box=b["box_id"], direction=b["direction"], breakout=b["breakout_time"],
                         state=b["state"], reason=b["done_reason"])
            elif kind == "box_broken":
                self.log("box_broken", t, box=payload["id"], dir=payload["break_dir"])
        self._prov_keys = frozenset()
        self._refresh(t, "m5")

    def _swings(self):
        """Confirm the fractal swing k bars back (high/low strictly beyond k bars each side)."""
        k, s = self.p.swing_k, self.tb
        n = len(s)
        if n < 2 * k + 1:
            return
        i = n - 1 - k
        hi, lo = s.h[i], s.l[i]
        if all(hi > s.h[i - d] and hi > s.h[i + d] for d in range(1, k + 1)):
            self.swing_hi.append((s.t[i], hi))
        if all(lo < s.l[i - d] and lo < s.l[i + d] for d in range(1, k + 1)):
            self.swing_lo.append((s.t[i], lo))

    # ------------------------------------------------------------------ once a second
    def _second(self, t, px):
        if not self.box.m5.t:
            return
        m5f, m1f = self.m5f, self.m1f
        prov = self.box.forming(m5f[0] * M5_NS, m5f[1], m5f[2], m5f[3], px, self.lv5,
                                m1_partial=(m1f[0] * M1_NS, m1f[1], m1f[2], m1f[3], m1f[4]))
        keys = frozenset((e["box_id"], e["direction"]) for e in prov)
        if keys != self._prov_keys:
            self._prov_keys = keys
            self._refresh(t, "forming")
        if self.orders and t >= self.arm_from:
            self._expire_orders(t)

    def _expire_orders(self, t):
        return

    # ------------------------------------------------------------------ order planning
    @staticmethod
    def _near_edge(box, price, up, tol):
        if box["low"] <= price <= box["high"]:
            return True
        return (box["high"] < price <= box["high"] + tol) if up else (box["low"] - tol <= price < box["low"])

    def _clusters(self, typ):
        levels = sorted((lv for lv in self.lv.live(typ)
                         if level_key(lv) not in self.dead and self.lv.can_retest(lv)),
                        key=lambda x: x["price"])
        out, cur = [], []
        for lv in levels:
            if cur and lv["price"] - cur[-1]["price"] > self.p.cluster_pts:
                out.append(cur)
                cur = []
            cur.append(lv)
        if cur:
            out.append(cur)
        return out

    def _qualify(self, typ, price, eps):
        """[(episode, playbook)] the level price qualifies for, seats still free."""
        res = []
        for e in eps:
            up = e["direction"] == "up"
            box = e["box"]
            if typ == ("LHPB" if up else "LLPB") and self._near_edge(box, price, up, self.p.go_with_edge_tol):
                pb = "go_with"
            elif typ == ("LLPB" if up else "LHPB") and ((price > box["high"]) if up else (price < box["low"])):
                pb = "fade"
            else:
                continue
            if (box["id"], e["direction"], pb) not in self.seats:
                res.append((e, pb))
        return res

    def _swings_near(self, price, is_low, lo_ns):
        arr = self.swing_lo if is_low else self.swing_hi
        return [s for s in arr if s[0] >= lo_ns and abs(s[1] - price) <= self.p.swerve_tol]

    def _refresh(self, t, why):
        """Recompute the working orders from the current state and diff them against the resting ones.

        Orders are sticky: one stays while its level is live and some episode it serves still
        qualifies (so a swerved order does not flap as the cluster around it changes), and a new one
        is only started for a cluster that holds no resting order's level. Levels that cannot be
        retested yet (the bar after their breakout has not closed) get no order: touching them
        only consumes them."""
        if t < self.arm_from:
            return
        eps = [e for e in self.box.active_episodes() if not e["done"]]
        want = {}
        if eps:
            live_keys = {typ: {level_key(x): x for x in self.lv.live(typ)} for typ in ("LLPB", "LHPB")}
            for o in self.orders.values():
                lv = live_keys[o["type"]].get(o["key"])
                if lv is None or o["key"] in self.dead:
                    continue
                q = self._qualify(o["type"], o["price"], eps)
                if q:
                    want[o["key"]] = dict(o, serves=[(e["box_id"], e["direction"], pb) for e, pb in q],
                                          boxes={(e["box_id"], e["direction"]): e for e, _ in q},
                                          provisional=any(e.get("provisional") for e, _ in q))
            now_lo = t - int(self.p.swerve_lookback_h * 3600 * S_NS)
            for typ in ("LLPB", "LHPB"):
                is_long = typ == "LHPB"
                for cl in self._clusters(typ):
                    if any(level_key(x) in want for x in cl):
                        continue                 # an order already rests in this cluster
                    entry = min(cl, key=lambda x: (x["price"] if is_long else -x["price"],
                                                   pd.Timestamp(x["formation_time"]).value))
                    planned = entry["price"]
                    if not self._qualify(typ, planned, eps):
                        continue
                    note = None
                    if self.p.swerve:
                        lo_ns = max(min(pd.Timestamp(x["breakout_time"]).value for x in cl), now_lo)
                        if self._swings_near(planned, is_long, lo_ns):
                            cand = [x for x in self.lv.live(typ) if level_key(x) not in self.dead
                                    and self.lv.can_retest(x)
                                    and ((x["price"] < planned) if is_long else (x["price"] > planned))
                                    and abs(x["price"] - planned) <= self.p.swerve_max_move]
                            cand.sort(key=lambda x: abs(x["price"] - planned))
                            moved = next((x for x in cand if not self._swings_near(x["price"], is_long, lo_ns)), None)
                            if moved is None:
                                bk = (typ, planned)
                                if bk not in self._blocked:
                                    self._blocked.add(bk)
                                    self.log("order_blocked", t, price=planned, type=typ, why="swerve: nowhere to move")
                                continue
                            entry, note = moved, f"swerved {planned:.2f}->{moved['price']:.2f}"
                    q = self._qualify(typ, entry["price"], eps)
                    if not q:
                        continue
                    k = level_key(entry)
                    want[k] = {"key": k, "type": typ, "side": "buy" if is_long else "sell", "price": entry["price"],
                               "level": entry, "members": [level_key(x) for x in cl], "note": note,
                               "serves": [(e["box_id"], e["direction"], pb) for e, pb in q],
                               "boxes": {(e["box_id"], e["direction"]): e for e, _ in q},
                               "provisional": any(e.get("provisional") for e, _ in q)}
        have = {o["key"]: o for o in self.orders.values()}
        for k, o in list(have.items()):
            if k not in want:
                self._cancel(o, t, self._why_gone(o, eps))
        for k, w in want.items():
            if k in have:
                o = have[k]
                o["serves"], o["boxes"], o["provisional"] = w["serves"], w["boxes"], w["provisional"]
                continue
            self._oid += 1
            w.update(id=self._oid, armed_t=t)
            self.orders[w["id"]] = w
            self.log("order_armed", t, id=w["id"], type=w["type"], side=w["side"], price=w["price"],
                     serves=w["serves"], note=w["note"], provisional=w["provisional"],
                     boxes=[{"id": b, "dir": d, "low": e["box"]["low"], "high": e["box"]["high"]}
                            for (b, d), e in w["boxes"].items()])
        self._limits()

    def _why_gone(self, o, eps):
        live = {(e["box_id"], e["direction"]) for e in eps}
        if not any(b in live for b in o["boxes"]):
            return "episode_ended"
        if level_key(o["level"]) not in {level_key(x) for x in self.lv.live(o["type"])}:
            return "level_gone"
        return "no_longer_qualifies"

    def _cancel(self, o, t, why):
        self.orders.pop(o["id"], None)
        self.log("order_cancelled", t, id=o["id"], price=o["price"], type=o["type"], why=why)
        self._limits()

    def _limits(self):
        sells = [o["price"] for o in self.orders.values() if o["side"] == "sell"]
        buys = [o["price"] for o in self.orders.values() if o["side"] == "buy"]
        self._sell_min = min(sells) if sells else math.inf
        self._buy_max = max(buys) if buys else -math.inf

    # ------------------------------------------------------------------ touch: gates, plan, fill
    def _check_orders(self, t, px):
        for o in list(self.orders.values()):
            if o["id"] not in self.orders:
                continue
            if (px >= o["price"]) if o["side"] == "sell" else (px <= o["price"]):
                self._touch(o, t, px)

    def _tb_floor(self, t):
        return (t // self.tb_ns) * self.tb_ns

    def _run_in(self, level_type, level, level_price, t):
        """(pivot_time, pivot_price) where the leg into the touch starts, or None."""
        is_long = level_type == "LHPB"
        t0 = self._tb_floor(t)
        p1 = pd.Timestamp(level["breakout_time"]).value
        pt, pp = self.zz.most_recent("high" if is_long else "low", t0, after=p1)
        s = self.tb
        if pt is None:
            lo = bisect.bisect_right(s.t, p1)
            hi = bisect.bisect_left(s.t, t0)
            arr = s.h if is_long else s.l
            if hi > lo:
                seg = arr[lo:hi]
                ext = max(seg) if is_long else min(seg)
                idx = max(i for i, v in enumerate(seg) if v == ext)
                return s.t[lo + idx], ext
            at = bisect.bisect_left(s.t, p1)
            if at >= len(s.t) or s.t[at] != p1:
                return None
            return p1, arr[at]
        return pt, pp

    def _strong_run_in(self, level_type, run, level_price):
        if run is None:
            return False
        pt, pp = run
        s = self.tb
        pos = bisect.bisect_left(s.t, pt)
        base = pos - self.p.run_in_baseline
        if base < 0:
            return False
        rng = sorted(h - l for h, l in zip(s.h[base:pos], s.l[base:pos]))
        if len(rng) < self.p.run_in_baseline:
            return False
        med = (rng[len(rng) // 2] + rng[(len(rng) - 1) // 2]) / 2
        if med <= 0:
            return False
        move = (pp - level_price) if level_type == "LHPB" else (level_price - pp)
        return move >= self.p.run_in_mult * med

    def _blackout(self, t):
        sod, ts = _pt_sod(t)
        a, b = self.p.news_window
        if a <= sod < b:
            return "news_pull"
        if self.p.no_entry_from <= sod < self.p.reopen:
            return "eod_entry_blocked"
        return None

    def _touch(self, o, t, px):
        lv = o["level"]
        typ = o["type"]
        is_long = typ == "LHPB"
        k = level_key(lv)
        if not self.lv.can_retest(lv):
            self.dead.add(k)
            self._cancel(o, t, "level_touched_before_it_could_be_retested")
            self._refresh(t, "tick")
            return
        why = self._blackout(t)
        if why:
            self._cancel(o, t, why)
            return
        run = self._run_in(typ, lv, o["price"], t)
        if not self._strong_run_in(typ, run, o["price"]):
            self._cancel(o, t, "weak_run_in")
            return
        serves = []
        for b, d, pb in o["serves"]:
            if (b, d, pb) in self.seats:
                continue
            e = o["boxes"][(b, d)]
            if pb == "fade":
                bt = e.get("breakout_time")
                if run is None or bt + M5_NS <= run[0]:
                    continue                    # the breakout is not the run-in's own
            serves.append((b, d, pb))
        if not serves:
            self._cancel(o, t, "no_seat_left")
            return
        plan = self._plan(o, o["price"], t)
        if isinstance(plan, str):
            self._cancel(o, t, plan)
            return
        stop, target, src = plan
        self._fill(o, t, o["price"], serves, stop, target, src)

    def _plan(self, o, fill, t):
        lv, typ = o["level"], o["type"]
        is_long = typ == "LHPB"
        tick = self.p.tick
        stop, src = None, None
        if lv.get("is_spike"):
            bar = self.lv.bars.get(pd.Timestamp(lv["formation_time"]).value)
            if bar is None:
                return "no_stop"
            ext = bar[2] if is_long else bar[1]
            stop = ext - tick if is_long else ext + tick
            if (stop >= fill) if is_long else (stop <= fill):
                return "no_stop"
            src = "p0_spike"
        else:
            pool = [x for x in self.lv.live(typ) if abs(x["price"] - fill) <= self.p.stop_radius]
            extreme = "breakout_low" if is_long else "breakout_high"
            cands = [(x[extreme] + (-tick if is_long else tick)) for x in pool]
            cands = [s for s in cands if ((s < fill) if is_long else (s > fill))]
            if not cands:
                return "no_stop"
            stop = min(cands) if is_long else max(cands)
            src = "thrust"
        if abs(stop - fill) < self.p.min_stop:
            wide = self._wider_stop(typ, fill, t)
            if wide is not None:
                stop, src = wide, "wide_p1"
        # target: the most recent confirmed zigzag pivot since the entry level's P1
        p1 = pd.Timestamp(lv["breakout_time"]).value
        pt, pp = self.zz.most_recent("low" if is_long else "high", self._tb_floor(t), after=p1)
        if pt is None:
            return "no_target"
        dist = (pp - fill) if is_long else (fill - pp)
        if not (self.p.min_target <= dist <= self.p.max_target):
            return "no_target"
        return stop, pp, src

    def _wider_stop(self, typ, fill, t):
        is_long = typ == "LHPB"
        tick = self.p.tick
        lo = self._tb_floor(t) - int(self.p.wider_stop_lookback_h * 3600 * S_NS)
        cand = [x for x in self.lv.broke
                if x["type"] == typ and lo <= pd.Timestamp(x["breakout_time"]).value < self._tb_floor(t)
                and abs(x["price"] - fill) <= self.p.stop_radius]
        extreme = "breakout_low" if is_long else "breakout_high"
        if is_long:
            cand = [x for x in cand if x[extreme] - tick <= fill - self.p.min_stop]
        else:
            cand = [x for x in cand if x[extreme] + tick >= fill + self.p.min_stop]
        if not cand:
            return None
        newest = max(pd.Timestamp(x["breakout_time"]).value for x in cand)
        group = [x for x in cand if pd.Timestamp(x["breakout_time"]).value == newest]
        best = min(group, key=lambda x: x[extreme]) if is_long else max(group, key=lambda x: x[extreme])
        return best[extreme] + (-tick if is_long else tick)

    # ------------------------------------------------------------------ positions
    def _retrig(self):
        los = [tr["stop"] if tr["side"] == "buy" else tr["target"] for tr in self.open]
        his = [tr["target"] if tr["side"] == "buy" else tr["stop"] for tr in self.open]
        self._lo_t = max(los) if los else -math.inf     # a long's stop / a short's target, below
        self._hi_t = min(his) if his else math.inf
        self._next_exit = min((tr["eod"] for tr in self.open), default=math.inf)

    def _fill(self, o, t, price, serves, stop, target, src):
        sod, ts = _pt_sod(t)
        day = ts.normalize() if sod < self.p.reopen else (ts + pd.Timedelta(days=1)).normalize()
        eod = (day + pd.Timedelta(seconds=self.p.eod_flat)).tz_convert("UTC").value
        tr = {"id": len(self.trades), "order": o["id"], "t": t, "side": o["side"], "type": o["type"],
              "price": price, "stop": stop, "target": target, "stop_src": src, "serves": serves,
              "eod": eod, "level_spike": bool(o["level"].get("is_spike")), "p0_kind": o["level"].get("p0_kind"),
              "boxes": [(b, d) for b, d, _ in serves], "exit": None}
        stop_pts, tgt_pts = abs(stop - price), abs(target - price)
        tr["stop_pts"], tr["target_pts"], tr["r_planned"] = stop_pts, tgt_pts, tgt_pts / stop_pts
        self.trades.append(tr)
        self.open.append(tr)
        for s in serves:
            self.seats[s] = tr["id"]
        self.orders.pop(o["id"], None)
        self.log("fill", t, trade=tr["id"], order=o["id"], side=o["side"], price=price, stop=stop, target=target,
                 stop_src=src, r=round(tr["r_planned"], 2), serves=serves, level_type=o["type"])
        self._limits()
        self._retrig()
        self._refresh(t, "fill")

    def _check_exits(self, t, px):
        for tr in list(self.open):
            long = tr["side"] == "buy"
            hit = None
            if (px <= tr["stop"]) if long else (px >= tr["stop"]):
                hit = ("stop", tr["stop"])
            elif (px >= tr["target"]) if long else (px <= tr["target"]):
                hit = ("target", tr["target"])
            elif t >= tr["eod"]:
                hit = ("eod_flat", px)
            if hit:
                self._exit(tr, t, hit[1], hit[0])

    def _exit(self, tr, t, price, why):
        tr["exit"] = {"t": t, "price": price, "why": why}
        pnl = (price - tr["price"]) if tr["side"] == "buy" else (tr["price"] - price)
        tr["pnl"], tr["r"] = pnl, pnl / tr["stop_pts"]
        self.open.remove(tr)
        self.log("trade_exit", t, trade=tr["id"], why=why, price=price, pnl=pnl, r=round(tr["r"], 2))
        self._retrig()
