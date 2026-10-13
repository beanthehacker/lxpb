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

The playbooks (one trade per box seat):
  fade      the breakout is confirmed and waiting for its retest; rest an order on a live LXPB
            level BEYOND the breakout edge (LLPB above an up breakout, LHPB below a down one).
  go-with   rest an order on a live LXPB level INSIDE the box or within GO_WITH_EDGE_TOL beyond
            the breakout-side edge (LHPB after an up breakout, LLPB after a below one).
Same-type live levels within CLUSTER_PTS of each other are one order at the extreme price; a swing
on that price moves it (swerve).

Orders: placed only near price, only as margin allows, and only while every gate would pass at the
order price (strong run-in, strong breakout, a plannable stop and target, no blackout); pulled the
moment one would fail. Seat ranking: for the working-order slots a spike entry outranks a plain one
that serves one of the same seats, then the nearer level wins (Params.seat_rank). Fills follow the
report's pegged model: only a trade of the right aggressor at or through the price fills; a wrong-side
touch re-quotes the order one tick closer to market (peg_step, up to peg_cap), re-judged by the gates.
Management (trade_management.py): a trading bar that closes past enough live P0s of the trade's type
trails the stop to one tick beyond that bar; a bar close with remaining reward / remaining risk at or
below rr_floor exits at market.
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
    # A plain-P0 (neither swing nor spike) is tradable only with this much rejection wick on its formation
    # candle (the M1/M2 reports' --plain-p0-min-wick-pts); weaker ones end at their breakout.
    min_plain_wick: float = 1.0
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
    # Orders are placed the way an account with limited margin can: only when price is within arm_pts of the
    # level, pulled when it drifts beyond cancel_pts, never more than max_working_orders at once and none
    # while max_open_positions are open. Every gate is judged BEFORE the touch, as if price were already on
    # the order: it is only placed while it would pass, and pulled the moment it would not.
    arm_pts: float = 4.0
    cancel_pts: float = 6.0
    max_working_orders: int = 2
    max_open_positions: int = 1
    # Pegged (chasing) entry, the report's fill model: an order that the tape touches on the wrong side
    # (price at the order but nobody traded through it) is re-quoted peg_step closer to market, up to
    # peg_cap in all; a re-quote that lands on the opposite quote fills there. Every re-quote is judged
    # by the same gates as a new order (the order is a new price) and pulled if it would fail.
    pegged: bool = True
    peg_step: float = 0.25
    peg_cap: float = 1.0
    # Seat ranking: "spike_first" (a spike-P0 entry outranks a plain one for the same seat, as in the
    # report; then the nearer level) or "nearest" (first come, nearest to price).
    seat_rank: str = "spike_first"
    # Trade management (trade_management.py): rule 1 trails the stop when a trading bar closes past
    # enough live P0s of the trade's type; rule 2 exits at market on a trading-bar close when the
    # remaining reward / remaining risk is at or below rr_floor.
    manage: bool = True
    rr_floor: float = 0.2
    trail_min_wick: float = 0.20


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
        self.lv = LevelBook("trade", verdicts_tb, min_plain_wick=self.p.min_plain_wick)
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
        self.entries = []                    # cluster-extreme levels an order could rest on, by price
        self._ent_prices = []
        self._eps_cache = None
        self._pending = 0                    # working orders with a re-quote not yet seen by the next record
        self._rr_close = None                # M5 close awaiting the RR-floor check on the next tick
        self._live_keys = set()
        self._bo_sec, self._bo_val = -1, None
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
    def on_tick(self, t, px, bid=None, ask=None, side=0):
        """One tape record: trade price `px`, the quotes `bid` / `ask` around it and its aggressor `side`
        (+1 a buyer lifted the offer, -1 a seller hit the bid, 0 unknown). Without quotes an order simply
        fills when price reaches it."""
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
        if self._rr_close is not None:
            self._rr_floor(t, px, bid, ask)
        if self._pending or (px >= self._sell_min or px <= self._buy_max if bid is None
                             else (ask >= self._sell_min or bid <= self._buy_max)):
            self._check_orders(t, px, bid, ask, side)
        self._orders_tick(t, px)

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
        if self.open and self.p.manage:
            self._manage_tb(tt, g[4])
        self._rebuild_entries(t)

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
        self._eps_cache = None

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

    def _swerve(self, typ, planned, lo_ns):
        """The level to rest on instead of `planned` when a swing sits on it: the nearest live level
        beyond it (up for a short, down for a long) within the swerve limit that has no swing of its own;
        None when there is none (the order is blocked)."""
        is_long = typ == "LHPB"
        cand = [x for x in self.lv.live(typ) if level_key(x) not in self.dead and self.lv.can_retest(x)
                and ((x["price"] < planned) if is_long else (x["price"] > planned))
                and abs(x["price"] - planned) <= self.p.swerve_max_move]
        cand.sort(key=lambda x: abs(x["price"] - planned))
        return next((x for x in cand if not self._swings_near(x["price"], is_long, lo_ns)), None)

    # ------------------------------------------------------------------ entries and the per-tick order book
    def _rebuild_entries(self, t):
        """The prices an order could rest on, from the live levels: one per cluster (the extreme price), moved
        past a swing when one sits on it. Rebuilt whenever the levels or swings change (closed bars, a level
        spent on a tick), not per tick."""
        ents = []
        self._live_keys = {level_key(x) for x in self.lv.state["touch_lv1"]}
        now_lo = t - int(self.p.swerve_lookback_h * 3600 * S_NS)
        for typ in ("LLPB", "LHPB"):
            is_long = typ == "LHPB"
            for cl in self._clusters(typ):
                entry = min(cl, key=lambda x: (x["price"] if is_long else -x["price"],
                                               pd.Timestamp(x["formation_time"]).value))
                planned = entry["price"]
                note = None
                lo_ns = max(min(pd.Timestamp(x["breakout_time"]).value for x in cl), now_lo)
                if self.p.swerve and self._swings_near(planned, is_long, lo_ns):
                    moved = self._swerve(typ, planned, lo_ns)
                    if moved is None:
                        continue
                    entry, note = moved, f"swerved {planned:.2f}->{moved['price']:.2f}"
                ents.append({"key": level_key(entry), "type": typ, "side": "buy" if is_long else "sell",
                             "price": entry["price"], "level": entry, "note": note, "lo_ns": lo_ns})
        ents.sort(key=lambda e: e["price"])
        self.entries = ents
        self._ent_prices = [e["price"] for e in ents]

    def _committed_episodes(self):
        if self._eps_cache is None:
            self._eps_cache = [e for e in self.box.active_episodes(include_forming=False) if not e["done"]]
        return self._eps_cache

    def _episodes_if_touched(self, price):
        """The breakouts waiting for their retest if price were on `price` right now: the committed ones plus
        the forming M5 bar read as if it closed there (a breakout that would grade strong at that price)."""
        eps = self._committed_episodes()
        m5f, m1f = self.m5f, self.m1f
        if m5f is None or not self.box.m5.t:
            return eps
        have = {(e["box_id"], e["direction"]) for e in eps}
        hyp = self.box.forming(m5f[0] * M5_NS, m5f[1], max(m5f[2], price), min(m5f[3], price), price, self.lv5,
                               m1_partial=(m1f[0] * M1_NS, m1f[1], max(m1f[2], price), min(m1f[3], price), price),
                               store=False)
        extra = [e for e in hyp if (e["box_id"], e["direction"]) not in have and self.box.is_range(e["box"])]
        return eps + extra if extra else eps

    def _evaluate(self, level, typ, price, t):
        """Would an order resting on `level` pass every gate if price touched it now? A dict (the boxes and
        playbooks it serves, the planned stop and target) or the reason it would not."""
        why = self._blackout(t)
        if why:
            return why
        eps = self._episodes_if_touched(price)
        q = self._qualify(typ, price, eps)
        if not q:
            return "no_episode"
        run = self._run_in(typ, level, price, t)
        if not self._strong_run_in(typ, run, price):
            return "weak_run_in"
        serves = []
        for e, pb in q:
            if pb == "fade" and (run is None or e["breakout_time"] + M5_NS <= run[0]):
                continue                    # the breakout is not the run-in's own
            serves.append((e, pb))
        if not serves:
            return "breakout_not_run_in"
        plan = self._plan({"level": level, "type": typ}, price, t)
        if isinstance(plan, str):
            return plan
        return {"serves": [(e["box_id"], e["direction"], pb) for e, pb in serves],
                "boxes": {(e["box_id"], e["direction"]): e for e, _ in serves},
                "provisional": any(e.get("provisional") for e, _ in serves), "plan": plan}

    def _orders_tick(self, t, px):
        """Keep the working orders right for this tick: pull the ones that would no longer pass, place the ones that
        now would, only near price and only as margin allows."""
        if t < self.arm_from or not (self.orders or self.entries):
            return
        p = self.p
        for o in list(self.orders.values()):
            lv = o["level"]
            if (o["key"] in self.dead and not o["touched"]) or o["key"] not in self._live_keys:
                self._cancel(o, t, "level_gone")
                continue
            if abs(px - o["price"]) > p.cancel_pts:
                self._cancel(o, t, "too_far")
                continue
            if p.swerve and not o["touched"] and self._swings_near(o["level_price"], o["type"] == "LHPB", o["lo_ns"]):
                self._cancel(o, t, "swing_on_level")
                self._rebuild_entries(t)
                continue
            res = self._evaluate(lv, o["type"], o["price"], t)
            if isinstance(res, str):
                self._cancel(o, t, res)
                continue
            o["serves"], o["boxes"], o["provisional"], o["plan"] = (
                res["serves"], res["boxes"], res["provisional"], res["plan"])
        if len(self.open) >= p.max_open_positions or len(self.orders) >= p.max_working_orders or not self.entries:
            return
        lo = bisect.bisect_left(self._ent_prices, px - p.arm_pts)
        hi = bisect.bisect_right(self._ent_prices, px + p.arm_pts)
        if lo >= hi:
            return
        have = {o["key"] for o in self.orders.values()}
        cands = []
        for e in self.entries[lo:hi]:
            if e["key"] in have or e["key"] in self.dead:
                continue
            if self._level_touched(e["side"], e["price"], px):
                continue                    # the tape was through it already: touched, not approached
            res = self._evaluate(e["level"], e["type"], e["price"], t)
            if not isinstance(res, str):
                cands.append((e, res))
        if not cands:
            return
        cands.sort(key=lambda c: self._seat_rank(c[0], px))
        for e, res in cands:
            if len(self.orders) >= p.max_working_orders:
                break
            if p.seat_rank == "spike_first" and self._outranked(e, res, cands):
                continue                    # a spike entry in view serves the same seat: that one gets it
            self._oid += 1
            o = {**e, "id": self._oid, "armed_t": t, "serves": res["serves"], "boxes": res["boxes"],
                 "provisional": res["provisional"], "plan": res["plan"], "level_price": e["price"],
                 "touched": False, "pending": False,
                 "worst": e["price"] + (p.peg_cap if e["side"] == "buy" else -p.peg_cap)}
            self.orders[o["id"]] = o
            if p.seat_rank == "spike_first" and self._spike(o):
                mine = set(o["serves"])
                for w in list(self.orders.values()):
                    if w is not o and not self._spike(w) and mine & set(w["serves"]):
                        self._cancel(w, t, "superseded_by_spike")
            self.log("order_armed", t, id=o["id"], type=o["type"], side=o["side"], price=o["price"],
                     serves=o["serves"], note=o["note"], provisional=o["provisional"], px=px,
                     stop=o["plan"][0], target=o["plan"][1],
                     boxes=[{"id": b, "dir": d, "low": x["box"]["low"], "high": x["box"]["high"]}
                            for (b, d), x in o["boxes"].items()])
            self._limits()

    def _level_touched(self, side, price, px):
        """The trading bar now forming has already traded through the level (or price is on it): the level
        is spent for this bar, so an order there would only chase a retest that has happened."""
        f = self.tbf
        return (f[2] >= price or px >= price) if side == "sell" else (f[3] <= price or px <= price)

    @staticmethod
    def _spike(e):
        return bool(e["level"].get("is_spike"))

    def _seat_rank(self, e, px):
        """Sort key for entries competing for working-order slots (lowest wins)."""
        if self.p.seat_rank == "spike_first":
            return (0 if self._spike(e) else 1, abs(e["price"] - px), e["price"])
        return (abs(e["price"] - px), e["price"])

    def _outranked(self, e, res, cands):
        """A plain entry is held back while a spike entry (placeable now, or already resting) serves one
        of the same seats: of the contenders for a seat the spike one is the one to rest an order on."""
        if self._spike(e):
            return False
        mine = set(res["serves"])
        for d, r in cands:
            if d is not e and self._spike(d) and mine & set(r["serves"]):
                return True
        for o in self.orders.values():
            if self._spike(o) and mine & set(o["serves"]):
                return True
        return False

    def _cancel(self, o, t, why):
        if o.get("pending"):
            self._pending -= 1
        self.orders.pop(o["id"], None)
        self.log("order_cancelled", t, id=o["id"], price=o["price"], type=o["type"], why=why)
        self._limits()

    def _limits(self):
        sells = [o["price"] for o in self.orders.values() if o["side"] == "sell"]
        buys = [o["price"] for o in self.orders.values() if o["side"] == "buy"]
        self._sell_min = min(sells) if sells else math.inf
        self._buy_max = max(buys) if buys else -math.inf

    # ------------------------------------------------------------------ touch: gates, plan, fill
    def _check_orders(self, t, px, bid=None, ask=None, side=0):
        """Match the tape record against the working orders (the report's fill model, find_alt_fill):
        an order fills when a trade of the right aggressor (a seller for a buy, a buyer for a sell) prints
        at or through its price; a record that merely touches the price from the wrong side re-quotes a
        pegged order one step closer to market, and the re-quote fills at once if it now meets the
        opposite quote. Without quotes (no bid/ask) price reaching the order is a fill."""
        for o in list(self.orders.values()):
            if o["id"] not in self.orders:
                continue
            buy = o["side"] == "buy"
            cur = o["price"]
            if not o["touched"] and ((px <= o["level_price"]) if buy else (px >= o["level_price"])):
                o["touched"] = True
                self.dead.add(o["key"])             # the level is traded: no order may be put on it again
            if bid is None:
                if (px <= cur) if buy else (px >= cur):
                    self._touch(o, t, cur)
                continue
            if o["pending"]:
                o["pending"] = False
                self._pending -= 1
                quote = ask if buy else bid
                if (cur >= quote) if buy else (cur <= quote):
                    self._touch(o, t, quote)
                    continue
            right = (side < 0) if buy else (side > 0)
            if right and ((px <= cur) if buy else (px >= cur)):
                self._touch(o, t, cur)
            elif self.p.pegged and not right and bid <= cur <= ask and cur != o["worst"]:
                self._requote(o, t, px)

    def _requote(self, o, t, px):
        """Chase: move a touched-but-unfilled order one step closer to market. The new price is judged like
        a new order (gates, stop, target); if it would fail, the order is pulled."""
        buy = o["side"] == "buy"
        step = self.p.peg_step
        new = min(o["price"] + step, o["worst"]) if buy else max(o["price"] - step, o["worst"])
        res = self._evaluate(o["level"], o["type"], new, t)
        if isinstance(res, str):
            self._cancel(o, t, "peg:" + res)
            return
        old = o["price"]
        o["price"], o["pending"] = new, True
        self._pending += 1
        o["serves"], o["boxes"], o["provisional"], o["plan"] = res["serves"], res["boxes"], res["provisional"], res["plan"]
        self._limits()
        self.log("order_requoted", t, id=o["id"], type=o["type"], frm=old, to=new, level=o["level_price"],
                 stop=o["plan"][0], target=o["plan"][1])

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
        sec = t // S_NS
        if sec == self._bo_sec:
            return self._bo_val
        self._bo_sec, self._bo_val = sec, self._blackout_now(t)
        return self._bo_val

    def _blackout_now(self, t):
        sod, ts = _pt_sod(t)
        a, b = self.p.news_window
        if a <= sod < b:
            return "news_pull"
        if self.p.no_entry_from <= sod < self.p.reopen:
            return "eod_entry_blocked"
        return None

    def _touch(self, o, t, fill_px):
        """The order filled at `fill_px`. It was kept only because it passed every gate on the previous tick,
        so it fills on the plan made then (re-planned when the fill landed away from the order price, e.g.
        a re-quote that met the quote); the level is spent (retested) either way."""
        k = level_key(o["level"])
        if not self.lv.can_retest(o["level"]):
            self.dead.add(k)
            self._cancel(o, t, "level_touched_before_it_could_be_retested")
            self._rebuild_entries(t)
            return
        self.dead.add(k)
        serves = [s for s in o["serves"] if s not in self.seats]
        if not serves:
            self._cancel(o, t, "no_seat_left")
            self._rebuild_entries(t)
            return
        plan = o["plan"]
        if fill_px != o["price"]:
            re = self._plan(o, fill_px, t)
            plan = plan if isinstance(re, str) else re
        stop, target, src = plan
        self._fill(o, t, fill_px, serves, stop, target, src)
        self._rebuild_entries(t)

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
            pool = [x for x in self.lv.live_for_stops(typ) if abs(x["price"] - fill) <= self.p.stop_radius]
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
              "eod": eod, "trails": 0, "level_price": o["level_price"], "level_spike": bool(o["level"].get("is_spike")), "p0_kind": o["level"].get("p0_kind"),
              "boxes": [(b, d) for b, d, _ in serves], "exit": None}
        stop_pts, tgt_pts = abs(stop - price), abs(target - price)
        tr["stop_pts"], tr["target_pts"], tr["r_planned"] = stop_pts, tgt_pts, tgt_pts / stop_pts
        self.trades.append(tr)
        self.open.append(tr)
        for s in serves:
            self.seats[s] = tr["id"]
        if o.get("pending"):
            self._pending -= 1
        self.orders.pop(o["id"], None)
        self.log("fill", t, trade=tr["id"], order=o["id"], side=o["side"], price=price, stop=stop, target=target,
                 stop_src=src, r=round(tr["r_planned"], 2), serves=serves, level_type=o["type"],
                 level=o["level_price"], chased=round(abs(price - o["level_price"]), 2))
        self._limits()
        self._retrig()
        if len(self.open) >= self.p.max_open_positions:
            for other in list(self.orders.values()):
                self._cancel(other, t, "margin: position open")

    def _manage_tb(self, tt, close):
        """A trading bar closed with trades open: rule 1 (stop trail) now, rule 2 (RR floor) on the next tick.
        Both read the trading-bar ledger (the reports run trade_management on the M1/M2 ledger and bars
        they were built on, one bar at a time)."""
        p = self.p
        grp_all = self.lv.broke_by_bar.get(tt, ())
        for tr in self.open:
            long = tr["side"] == "buy"
            grp = [x for x in grp_all if x["type"] == tr["type"]]
            if not grp or len(grp) < self._trail_need(grp, tr["type"]):
                continue
            new = grp[0]["breakout_low"] - p.tick if long else grp[0]["breakout_high"] + p.tick
            if (new - tr["stop"]) * (1 if long else -1) > 0:
                self.log("stop_trailed", self.last_t, trade=tr["id"], frm=tr["stop"], to=new, group=len(grp))
                tr["stop"] = new
                tr["trails"] += 1
        self._retrig()
        self._rr_close = (close, {tr["id"] for tr in self.open})

    def _trail_need(self, grp, typ):
        """How many P0s must share the breakout bar: 1 when any is a swing or a spike, 2 when every wick is at
        least trail_min_wick of its own candle, else 3 (trade_management._group_threshold)."""
        if any(x["is_swing"] or x["is_spike"] for x in grp):
            return 1
        for x in grp:
            bar = self.lv.bars.get(pd.Timestamp(x["formation_time"]).value)
            if bar is None or bar[1] <= bar[2]:
                return 3
            o, h, l, c = bar
            wick = (h - max(o, c)) if typ == "LHPB" else (min(o, c) - l)
            if wick / (h - l) < self.p.trail_min_wick:
                return 3
        return 2

    def _rr_floor(self, t, px, bid, ask):
        close, ids = self._rr_close
        self._rr_close = None
        for tr in list(self.open):
            if tr["id"] not in ids:
                continue
            sgn = 1 if tr["side"] == "buy" else -1
            reward, risk = sgn * (tr["target"] - close), sgn * (close - tr["stop"])
            if risk > 0 and reward / risk <= self.p.rr_floor:
                quote = (bid if sgn > 0 else ask)
                self._exit(tr, t, px if quote is None else quote, "rr_floor")

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
