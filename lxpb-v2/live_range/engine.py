"""Live range-box playbook engine: ticks in, orders and trades out.

State it keeps (nothing is recomputed from history):
  * M1 / M5 bars built from the tick stream,
  * the M5 LXPB level book (what a box's breakout waits on) and the box ledger (BoxBook),
  * the trading LXPB level book (bar_minutes bars) that supplies the prices to trade,
  * the zigzag (find_zigzag) and swing pivots on the trading bars (targets, run-in, swerve).

On every tick: fills and exits are checked at once; once a second the forming M5 bar is read
as if it closed (provisional breakouts); on every closed bar the books advance. After any change
the working orders are recomputed from the current state and diffed against the ones already
resting: new ones armed, stale ones pulled, each with a reason in the event log.

M1 FADE REPORT: THE FADE PLAYBOOK ONLY (user, 2026-10-16). This engine decides the trades of the M1 fade playbook
(`ss_m1_confl`, `sep_month_fade`). The go-with playbook below is NOT part of that playbook or its report: its trades
(`playbooks` = go_with in trades.csv, `serves` ending in 'go_with') are not fade trades, must not be counted in fade
results or totals, and must be dropped from any fade comparison. Known side effect: the engine still takes them, and
a go-with position occupies the single `max_open_positions` slot, so it can keep a fade order from being placed.

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
from find_range_box_zz import find_range_box_zz
from find_zigzag import ATR_MULT, ATR_PERIOD, MIN_BARS, ZigZag

from .levels import LevelBook, key as level_key
from .series import Series

PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")
S_NS = 10**9
H1_NS = 3600 * S_NS


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
    zz_atr_mult: float = ATR_MULT      # find_zigzag defaults: the repo's only zigzag
    zz_min_bars: int = MIN_BARS
    tick: float = 0.25
    min_stop: float = 3.0
    stop_radius: float = 10.0
    wider_stop_lookback_h: float = 24.0
    min_target: float = 0.25
    max_target: float = 50.0
    # Fade target (user, 2026-10-17): "range_edge" = the edge of the range the trade fades (a long on an LHPB below the
    # range takes the range LOW, a short on an LLPB above it the range HIGH; the merged range's H1 edges when merged, the
    # nearest edge past the fill when several boxes are served); "zigzag" = the most recent zigzag pivot since P1 (the
    # reports' swing-extreme); "opposite_zz" = the farthest standing opposite-type P0 formed since the leg into the retest
    # began (the reports' opposite-zz). See `_target`. Go-with trades keep the zigzag target.
    target_mode: str = "range_edge"
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
    # M1/M2 report ledger filters and gates (render_m1_confl_report.py)
    bo_range_atr: float = 0.0        # OFF. The reports' --bo-range-atr 1.5: a level's own P1 run must have range >= this x ATR(bo_atr_period)
    bo_body: float = 0.5             # ... and body >= this share of the run's range
    bo_atr_period: int = 30
    bo_atr_window: int = 150
    globex_block: bool = True        # 15:00-15:05 PT reopen: the report tags it and excludes it by default
    globex_open: tuple = (15 * 3600, 15 * 3600 + 5 * 60)
    tag_min_r: float = 1.0           # planned R below this is tagged r_below_min (the report measures, not skips)
    min_r: float = 0.0               # > 0 turns that tag into a gate
    box_lookback_days: float = 0.0   # 0 = a box never expires. The report feeds its detector 5 days of M5 history (range_box_tags.LOOKBACK_DAYS); set 5.0 to copy that
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
    # Production gets data once per SECOND (user, 2026-10-16): the algo decides on a clock of decision_s seconds from the
    # snapshot of the previous second (last price and quotes before the boundary). 0 = decide on every tape record
    # (the old behaviour, only for comparison). Fills of resting orders and stop / target hits stay on the real tick:
    # the exchange does that, not the algo.
    decision_s: float = 1.0
    # Range merge (user, 2026-10-16). Two M5 range boxes whose price ranges overlap by at least this share of the SMALLER box
    # and that both sit inside one known, still unbroken H1 range box (patterns-pure find_range_box_zz on the closed H1
    # bars, M5 as lower data) are ONE range and share their seats: one trade per merged range, direction and playbook.
    # Judged point in time when both boxes have a breakout waiting; links never come apart. 0 = off.
    box_merge_overlap: float = 0.5
    box_merge_tol: float = 2.0       # points an M5 box edge may poke out of the H1 box and still count as inside it
    h1_window: int = 600             # trailing H1 bars the H1 range detector reads
    # FADE 2 is OFF (user, 2026-10-16): a box's second-side breakout (the other edge had already broken out strongly before
    # it) is not traded; only fade 1 (the box's first strong breakout) is. True brings fade 2 back.
    fade2: bool = False


def _pt_sod(t_ns):
    ts = pd.Timestamp(t_ns, unit="ns", tz="UTC").tz_convert(PT)
    return ts.hour * 3600 + ts.minute * 60 + ts.second, ts


class Plan(tuple):
    """(stop, target, stop source) of a planned trade; `meta` carries what the report shows about where they came from."""
    meta = None


class GroupSeats(dict):
    """The engine's seats, except that a seat taken by one box of a merged range is taken for every box of it."""

    def __init__(self, eng):
        super().__init__()
        self.eng = eng

    def __contains__(self, key):
        b, d, pb = key
        root = self.eng.find(b)
        return any(self.eng.find(k0) == root and k1 == d and k2 == pb for (k0, k1, k2) in self.keys())


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
        self.tb = Series(ATR_PERIOD)         # closed trading bars
        # books
        self.box = BoxBook()
        self.lv5 = LevelBook("m5", verdicts5)
        self.lv = LevelBook("trade", verdicts_tb, min_plain_wick=self.p.min_plain_wick)
        self.zz = ZigZag(self.p.zz_atr_mult, ATR_PERIOD, self.p.zz_min_bars)
        self.swing_hi, self.swing_lo = [], []    # confirmed fractal swings (time_ns, price)
        self.dead = set()                    # trade levels touched on the tick before they could be retested
        # orders / trades
        self.orders = {}
        self.seats = GroupSeats(self) if self.p.box_merge_overlap > 0 else {}   # (box_id, direction, playbook) -> trade id
        self.uf, self.merge_links = {}, []   # merged ranges: box id -> parent; (t, box, box, overlap, H1 box) per link
        self.members = {}                    # merged range root -> ids of its M5 boxes
        self._merge_no, self._link_seen = {}, (None, None)
        self._h1_rows, self._m5_rows, self._h1f, self._h1_open = [], [], None, []   # closed H1 bars, M5 bars, forming H1, open H1 boxes
        self.trades = []
        self.open = []
        self._oid = 0
        self._blocked = set()
        self.entries = []                    # one entry per live level (no cluster anchor), by price
        self._ent_prices = []
        self._eps_cache = None
        self._entry_by_key = {}              # level key -> its entry record
        self._bo_cache = {}                  # (breakout ns, is_long) -> strong-breakout verdict of the level's P1
        self._pending = 0                    # working orders with a re-quote not yet seen by the next record
        self._rr_close = None                # M5 close awaiting the RR-floor check on the next tick
        self.shadows = []                    # what-if brackets of the filled trades (every target rule, managed or not)
        self._rr_shadow = None               # trading-bar close awaiting the shadows' RR-floor check
        self._live_keys = set()
        self._bo_sec, self._bo_val = -1, None
        self._last_sec = -1
        self._prov_keys = frozenset()
        self.last_t = 0
        self.last_px = None
        self.last_bid = self.last_ask = None
        self._dec_ns = int(self.p.decision_s * S_NS)
        self._dec_slot = None
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
        fills when price reaches it.

        The algo's own decisions (arm / pull / re-quote orders, RR-floor exit) run on the `decision_s` clock: the
        first record of a new second first lets the algo decide on the snapshot of what came before it (`_decide`),
        then the record is applied. Bars, fills of resting orders and stop / target hits are applied per record."""
        if self._dec_ns:
            slot = t // self._dec_ns
            if slot != self._dec_slot:
                if self._dec_slot is not None and self.last_px is not None:
                    self._decide(slot * self._dec_ns, self.last_px, self.last_bid, self.last_ask)
                self._dec_slot = slot
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
        self.last_t, self.last_px, self.last_bid, self.last_ask = t, px, bid, ask
        if self.open:
            for tr in self.open:        # excursions of the open trades, for the report (nothing decides on them)
                d = (px - tr["price"]) if tr["side"] == "buy" else (tr["price"] - px)
                if d > tr["mfe"]:
                    tr["mfe"] = d
                elif -d > tr["mae"]:
                    tr["mae"] = -d
                if tr["mfe"] - d > tr["dd"]:
                    tr["dd"] = tr["mfe"] - d
        if self.open and (px <= self._lo_t or px >= self._hi_t or t >= self._next_exit):
            self._check_exits(t, px)
        if self.shadows:
            self._shadow_tick(t, px)
        if not self._dec_ns and self._rr_close is not None:
            self._rr_floor(t, px, bid, ask)
        if not self._dec_ns and self._rr_shadow is not None:
            self._shadow_rr(t, px, bid, ask)
        if self._pending or (px >= self._sell_min or px <= self._buy_max if bid is None
                             else (ask >= self._sell_min or bid <= self._buy_max)):
            self._check_orders(t, px, bid, ask, side)
        if not self._dec_ns:
            self._orders_tick(t, px)

    def _decide(self, t, px, bid, ask):
        """The algo's decisions from one snapshot (time `t`, last price and quotes `px` / `bid` / `ask`): the RR-floor
        exit, the peg re-quotes of orders that were touched without filling, then pull / place orders."""
        if self._rr_close is not None:
            self._rr_floor(t, px, bid, ask)
        if self._rr_shadow is not None:
            self._shadow_rr(t, px, bid, ask)
        for o in list(self.orders.values()):
            if o.pop("requote_due", False) and o["id"] in self.orders and (
                    bid is None or (bid <= o["price"] <= ask)) and o["price"] != o["worst"]:
                self._requote(o, t, px)
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
        self.zz.step(g[2], g[3], self.tb.atr[-1], 0, tt)
        self._swings()
        for kind, lv in self.lv.on_bar(tt, g[1], g[2], g[3], g[4]):
            self.dead.discard(level_key(lv))
            self.log("level_" + kind, t, level=level_key(lv)[:2], price=lv["price"], type=lv["type"])
        if self.open and self.p.manage:
            self._manage_tb(tt, g[4])
        if self.shadows:
            self._shadow_manage(tt, g[4])
        self._rebuild_entries(t)

    def _close_m5(self, h, t):
        tt = h[0] * M5_NS
        self._h1_on_m5(h)
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

    # -- the reports' strong-breakout gate on a level's P1 (render_m1_confl_report._scan_strong_breakout) ----------
    def _gate_ok(self, lv):
        """True if the level's breakout (P1) is a strong one: the maximal run of consecutive trading bars that close
        in the breakout direction and contain the P1 bar, merged into one candle, has a range of at least
        bo_range_atr x ATR(bo_atr_period) (measured on the bo_atr_window bars before the run, so never informed by the
        run) and a body of at least bo_body of that range. A level that fails ends at its breakout: it is not an entry
        (it stays a stop candidate while untouched)."""
        p = self.p
        if p.bo_range_atr <= 0:
            return True
        k = (pd.Timestamp(lv["breakout_time"]).value, lv["type"] == "LHPB")
        v = self._bo_cache.get(k)
        if v is None:
            v, final = self._scan_bo(k[0], k[1])
            if final:
                self._bo_cache[k] = v
        return v

    def _scan_bo(self, t0, up):
        s, p = self.tb, self.p
        n = len(s.t)
        pos = bisect.bisect_left(s.t, t0)
        if pos >= n or s.t[pos] != t0:
            return False, True
        o, c = s.o, s.c

        def match(i):
            return (c[i] > o[i]) if up else (c[i] < o[i])

        if not match(pos):
            return False, True
        lo = pos
        while lo > 0 and match(lo - 1):
            lo -= 1
        if lo == 0:
            return False, True
        hi = pos
        while hi < n - 1 and match(hi + 1):
            hi += 1
        final = hi < n - 1                      # a run still open may yet grow
        rng = max(s.h[lo:hi + 1]) - min(s.l[lo:hi + 1])
        body = abs(c[hi] - o[lo])
        a0 = max(0, lo - p.bo_atr_window)
        if lo - a0 < 2 or rng <= 0:
            return False, final
        alpha, atr = 1.0 / p.bo_atr_period, None        # findATR: Wilder RMA of the true range, first bar dropped
        for i in range(a0 + 1, lo):
            pc = c[i - 1]
            tr = max(s.h[i] - s.l[i], abs(s.h[i] - pc), abs(s.l[i] - pc))
            atr = tr if atr is None else atr + alpha * (tr - atr)
        return (rng >= p.bo_range_atr * atr and body >= p.bo_body * rng), final

    def _clusters(self, typ):
        levels = sorted((lv for lv in self.lv.live(typ)
                         if level_key(lv) not in self.dead and self.lv.can_retest(lv) and self._gate_ok(lv)),
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
        """[(episode, playbook)] the retested level's own price qualifies for (the reports classify with the price of the
        cluster's earliest-retesting member, not the refined entry), seats still free."""
        res = []
        for e in eps:
            up = e["direction"] == "up"
            box = e["box"]
            lo, hi = self.range_edges(box)[:2]      # the merged range's H1 box edges, else the M5 box's own
            if typ == ("LHPB" if up else "LLPB") and self._near_edge({"low": lo, "high": hi}, price, up, self.p.go_with_edge_tol):
                pb = "go_with"
            elif typ == ("LLPB" if up else "LHPB") and ((price > hi) if up else (price < lo)):
                pb = "fade"
                if not self.p.fade2 and self._is_fade2(e):
                    continue                # fade 2 is off: only the box's first strong breakout is faded
            else:
                continue
            if (box["id"], e["direction"], pb) not in self.seats:
                res.append((e, pb))
        return res

    @staticmethod
    def _fade_break_ns(e):
        """The time the report compares with the run-in leg (range_box_tags._broke_in_run_in): for the edge the
        box's own zigzag break went through, that break candle (find_range_box_zz `break_time`); otherwise the
        episode's strong-breakout bar."""
        box = e["box"]
        if box.get("break_t") is not None and (box.get("break_dir") == "up") == (e["direction"] == "up"):
            return box["break_t"]
        return e["breakout_time"]

    def _swings_near(self, price, is_low, lo_ns):
        arr = self.swing_lo if is_low else self.swing_hi
        return [s for s in arr if s[0] >= lo_ns and abs(s[1] - price) <= self.p.swerve_tol]

    def _swerve(self, typ, planned, lo_ns):
        """The level to rest on instead of `planned` when a swing sits on it: the nearest live level
        beyond it (up for a short, down for a long) within the swerve limit that has no swing of its own;
        None when there is none (the order is blocked)."""
        is_long = typ == "LHPB"
        cand = [x for x in self.lv.live(typ) if level_key(x) not in self.dead and self.lv.can_retest(x)
                and self._gate_ok(x)
                and ((x["price"] < planned) if is_long else (x["price"] > planned))
                and abs(x["price"] - planned) <= self.p.swerve_max_move]
        cand.sort(key=lambda x: abs(x["price"] - planned))
        return next((x for x in cand if not self._swings_near(x["price"], is_long, lo_ns)), None)

    # ------------------------------------------------------------------ entries and the per-tick order book
    def _rebuild_entries(self, t):
        """The entries an order could rest on: ONE PER LIVE LEVEL (there is no cluster anchor). Every level is judged on its
        own in `_evaluate` (is its own price outside the range edge, is its own run-in strong, ...). A level with a
        confirmed swing sitting on it is not an entry. The cluster (same-type live levels chained within cluster_pts) only
        answers one question afterwards: is there a BETTER entry price among the level's own cluster members (`better`:
        the more extreme ones, most extreme first)? Rebuilt whenever the levels or swings change (closed bars, a level
        spent on a tick), not per tick."""
        ents = []
        self._live_keys = {level_key(x) for x in self.lv.state["touch_lv1"]}
        now_lo = t - int(self.p.swerve_lookback_h * 3600 * S_NS)
        for typ in ("LLPB", "LHPB"):
            is_long = typ == "LHPB"
            for cl in self._clusters(typ):
                ext = sorted(cl, key=lambda x: (x["price"] if is_long else -x["price"], pd.Timestamp(x["formation_time"]).value))
                ck = level_key(ext[0])
                for lv in cl:
                    lo_ns = max(pd.Timestamp(lv["breakout_time"]).value, now_lo)
                    if self.p.swerve and self._swings_near(lv["price"], is_long, lo_ns):
                        continue                # a confirmed swing sits on this level: not an entry
                    better = [m for m in ext if (m["price"] < lv["price"] if is_long else m["price"] > lv["price"])]
                    ents.append({"key": level_key(lv), "type": typ, "side": "buy" if is_long else "sell",
                                 "price": lv["price"], "level_price": lv["price"], "level": lv,
                                 "note": None, "lo_ns": lo_ns, "cluster": len(cl), "cluster_key": ck,
                                 "members": list(cl), "swerve": None, "better": better})
        ents.sort(key=lambda e: e["price"])
        self.entries = ents
        self._entry_by_key = {e["key"]: e for e in ents}
        self._ent_prices = [e["price"] for e in ents]

    def _box_seen(self, e, t=None):
        """A box older than box_lookback_days is not seen: the report feeds its detector 5 days of M5 history."""
        if self.p.box_lookback_days <= 0:
            return True
        t = self.last_t if t is None else t
        return e["box"]["start_t"] >= t - int(self.p.box_lookback_days * 86400 * S_NS)

    def _committed_episodes(self):
        if self._eps_cache is None:
            self._eps_cache = [e for e in self.box.active_episodes(include_forming=False)
                               if not e["done"] and self._box_seen(e)]
        return self._eps_cache

    def _is_fade2(self, e):
        """The box's OTHER edge had already broken out strongly before this episode's breakout (the report's fade 2)."""
        r = self.box.readers.get(e["box_id"])
        return r is not None and any(x["direction"] != e["direction"] and x["breakout_time"] < e["breakout_time"]
                                     for x in r.episodes)

    # -- H1 range boxes and the range merge (point in time) ------------------------------------------------------
    def seed_h1(self, h1, m5):
        """History from before the stream starts: closed H1 bars and M5 bars (UTC-indexed OHLC frames)."""
        self._h1_rows = [(ts.value, o, h, l, c) for ts, o, h, l, c in zip(h1.index, h1.open, h1.high, h1.low, h1.close)]
        self._m5_rows = [(ts.value, o, h, l, c) for ts, o, h, l, c in zip(m5.index, m5.open, m5.high, m5.low, m5.close)]
        self._h1_refresh()

    def _h1_on_m5(self, h):
        """An M5 bar closed: build the H1 bar it belongs to; when an H1 bar is complete, re-read the H1 range boxes."""
        if self.p.box_merge_overlap <= 0:
            return
        tt = h[0] * M5_NS
        slot, f, done = tt // H1_NS, self._h1f, False
        if f is not None and f[0] != slot:
            self._h1_rows.append((f[0] * H1_NS, f[1], f[2], f[3], f[4]))
            self._h1f = f = None
            done = True
        self._m5_rows.append((tt, h[1], h[2], h[3], h[4]))
        if f is None:
            self._h1f = [slot, h[1], h[2], h[3], h[4]]
        else:
            f[2], f[3], f[4] = max(f[2], h[2]), min(f[3], h[3]), h[4]
        if (tt + M5_NS) % H1_NS == 0:
            f = self._h1f
            self._h1_rows.append((f[0] * H1_NS, f[1], f[2], f[3], f[4]))
            self._h1f, done = None, True
        if done:
            self._h1_refresh()

    def _h1_refresh(self):
        """The still-open H1 range boxes as of the last CLOSED H1 bar: [(low, high, start ns)]."""
        if self.p.box_merge_overlap <= 0 or len(self._h1_rows) < 30:
            self._h1_open = []
            return
        rows = self._h1_rows[-self.p.h1_window:]
        self._h1_rows = list(rows)
        t0 = rows[0][0]
        m5r = [r for r in self._m5_rows if r[0] >= t0]
        self._m5_rows = m5r
        cols = ["open", "high", "low", "close"]
        h1 = pd.DataFrame([r[1:] for r in rows], columns=cols, index=pd.to_datetime([r[0] for r in rows], unit="ns", utc=True))
        m5 = pd.DataFrame([r[1:] for r in m5r], columns=cols, index=pd.to_datetime([r[0] for r in m5r], unit="ns", utc=True))
        b = find_range_box_zz(h1, lower_data=m5)
        op = b[b["status"] == "open"]
        self._h1_open = [(float(lo), float(hi), pd.Timestamp(st).value)
                         for lo, hi, st in zip(op["low"], op["high"], op["start_time"])]

    def _h1_holds(self, a, b):
        """(low, high, start ns) of the tightest open H1 range box that holds all the given M5 boxes (edges within
        box_merge_tol), or None."""
        tol, best = self.p.box_merge_tol, None
        boxes = a if isinstance(a, (list, tuple)) else (a, b)
        for lo, hi, st in self._h1_open:
            if all(lo - tol <= x["low"] and x["high"] <= hi + tol for x in boxes):
                if best is None or hi - lo < best[1] - best[0]:
                    best = (lo, hi, st)
        return best

    def range_edges(self, box):
        """(low, high, start ns or None, member ids) of the range a box belongs to. The M5 boxes merged into one range
        are ONE range and its edges are the H1 range box's (the tightest known open one that holds every member); a box
        on its own, or whose members no single open H1 box holds, keeps its own M5 edges (user, 2026-10-16: only one
        range box is considered, whatever the timeframe)."""
        own = (float(box["low"]), float(box["high"]), None, (box["id"],))
        if self.p.box_merge_overlap <= 0:
            return own
        ids = self.members.get(self.find(box["id"]))
        if not ids or len(ids) < 2:
            return own
        mem = [self.box.boxes[i] for i in sorted(ids)]
        hb = self._h1_holds(mem, None)
        if hb is None:
            return own
        return (hb[0], hb[1], hb[2], tuple(sorted(ids)))

    def find(self, b):
        while self.uf.get(b, b) != b:
            self.uf[b] = self.uf.get(self.uf[b], self.uf[b])
            b = self.uf[b]
        return b

    def _link(self, eps, t):
        """Join the M5 boxes of `eps` (breakouts waiting right now) that overlap by >= box_merge_overlap and sit inside one
        known open H1 range box. Only boxes with a breakout waiting are looked at, so nothing needs the other box to have
        broken out in advance."""
        sec = t // S_NS
        ids = tuple(sorted({e["box_id"] for e in eps}))
        if (sec, ids) == self._link_seen:
            return
        self._link_seen = (sec, ids)
        boxes = {e["box_id"]: e["box"] for e in eps}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if self.find(a) == self.find(b) or self._merge_no.get((a, b)) == sec:
                    continue
                A, B = boxes[a], boxes[b]
                frac = (min(A["high"], B["high"]) - max(A["low"], B["low"])) / max(1e-9, min(A["high"] - A["low"], B["high"] - B["low"]))
                hb = self._h1_holds(A, B) if frac >= self.p.box_merge_overlap else None
                if hb is None:
                    self._merge_no[(a, b)] = sec
                    continue
                ra, rb = self.find(a), self.find(b)
                if ra != rb:
                    root, gone = min(ra, rb), max(ra, rb)
                    self.uf[gone] = root
                    self.members[root] = self.members.get(root, {root}) | self.members.pop(gone, {gone}) | {a, b}
                else:
                    self.members.setdefault(ra, {ra}).update((a, b))
                self.merge_links.append((t, a, b, round(frac, 2), f"{hb[0]:.2f}-{hb[1]:.2f}"))
                self.log("range_merge", t, a=a, b=b, overlap=round(frac, 2), h1=f"{hb[0]:.2f}-{hb[1]:.2f}")

    def _canon(self, serves):
        if self.p.box_merge_overlap <= 0:
            return set(serves)
        return {(self.find(b), d, pb) for b, d, pb in serves}

    def _episodes_if_touched(self, price):
        eps = self._episodes_raw(price)
        if self.p.box_merge_overlap > 0 and eps and self._h1_open:
            self._link([e for e in eps if self.p.fade2 or not self._is_fade2(e)], self.last_t)
        return eps

    def _episodes_raw(self, price):
        """The breakouts waiting for their retest if price were on `price` right now: the committed ones plus
        the forming M5 bar read as if it closed there (a breakout that would grade strong at that price)."""
        eps = self._committed_episodes()
        m5f, m1f = self.m5f, self.m1f
        if m5f is None or not self.box.m5.t:
            return eps
        if eps:
            gone = self.box.retested_by(max(m5f[2], price), min(m5f[3], price), self.lv5)
            if gone:
                eps = [e for e in eps if (e["box_id"], e["direction"]) not in gone]
        have = {(e["box_id"], e["direction"]) for e in eps}
        hyp = self.box.forming(m5f[0] * M5_NS, m5f[1], max(m5f[2], price), min(m5f[3], price), price, self.lv5,
                               m1_partial=(m1f[0] * M1_NS, m1f[1], max(m1f[2], price), min(m1f[3], price), price),
                               store=False)
        extra = [e for e in hyp if (e["box_id"], e["direction"]) not in have and self.box.counts(e)
                 and self._box_seen(e)]
        return eps + extra if extra else eps

    def _evaluate(self, e, price, t):
        """Would an order on entry/order `e` pass every gate if price were on `price` (its level, or a chased
        price) right now? A dict (the boxes and playbooks it serves, the planned stop and target) or the reason
        it would not. The level is judged on its OWN: its own price must be outside the range edge, its run-in is
        measured from its own P1 to its price (not the chased price), and the stop and target are planned from the
        price that fills."""
        typ = e["type"]
        why = self._blackout(t)
        if why:
            return why
        eps = self._episodes_if_touched(price)
        serves, reason = [], "no_episode"
        lvl = e["level"]
        q = self._qualify(typ, lvl["price"], eps)
        if q:
            run = self._run_in(typ, lvl, e["level_price"], t)
            if not self._strong_run_in(typ, run, e["level_price"]):
                reason = "weak_run_in"
            else:
                for ep, pb in q:
                    if pb == "fade" and (run is None or self._fade_break_ns(ep) + M5_NS <= run[0]):
                        reason = "breakout_not_run_in" if reason == "no_episode" else reason
                        continue                # the breakout is not the run-in's own
                    if all((ep["box_id"], ep["direction"], pb) != (x["box_id"], x["direction"], p2) for x, p2 in serves):
                        serves.append((ep, pb))
        if not serves:
            return reason
        plan = self._plan({"level": e["level"], "type": typ,
                           "boxes": {(ep["box_id"], ep["direction"]): ep for ep, pb in serves if pb == "fade"}}, price, t)
        if isinstance(plan, str):
            return plan
        if self.p.min_r > 0 and abs(plan[1] - price) < self.p.min_r * abs(plan[0] - price):
            return "r_below_min"
        return {"serves": [(ep["box_id"], ep["direction"], pb) for ep, pb in serves],
                "boxes": {(ep["box_id"], ep["direction"]): ep for ep, _ in serves},
                "provisional": any(ep.get("provisional") for ep, _ in serves), "plan": plan}

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
            res = self._evaluate(o, o["price"], t)
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
        decided = []
        for e in self.entries[lo:hi]:
            if e["key"] in have or e["key"] in self.dead:
                continue
            if self._level_touched(e["side"], e["price"], px):
                continue                    # the tape was through it already: touched, not approached
            res = self._evaluate(e, e["price"], t)
            if not isinstance(res, str):
                decided.append((e, res))
        # Each level that qualifies on its own is then checked against its own cluster for a better entry price: a more
        # extreme member that ALSO qualifies as a fade on its own (it may not violate the outside-the-range rule). The
        # order rests at that better price and only once price is within arm_pts of IT.
        cands, seen = [], set()
        for e, res in decided:
            best = (e, res)
            for m in e["better"]:
                em = self._entry_by_key.get(level_key(m))
                if em is None or em["key"] in self.dead or em["key"] in have or self._level_touched(em["side"], em["price"], px):
                    continue
                rm = self._evaluate(em, em["price"], t)
                if not isinstance(rm, str):
                    best = (em, rm)
                    break
            be, br = best
            if be["key"] in seen or abs(be["price"] - px) > p.arm_pts:
                continue
            seen.add(be["key"])
            if be is not e:
                be = {**be, "decided": e["level"], "note": f"better price in cluster {e['price']:.2f}->{be['price']:.2f}"}
            cands.append((be, br))
        if not cands:
            return
        cands.sort(key=lambda c: self._seat_rank(c[0], px))
        for e, res in cands:
            if len(self.orders) >= p.max_working_orders:
                break
            if p.seat_rank == "spike_first" and self._outranked(e, res, cands):
                continue                    # a spike entry in view serves the same seat: that one gets it
            if self._seat_has_order(res):
                continue                    # one order per seat: an order already resting there stands
            self._oid += 1
            o = {**e, "decided": e.get("decided", e["level"]), "id": self._oid, "armed_t": t, "serves": res["serves"], "boxes": res["boxes"],
                 "provisional": res["provisional"], "plan": res["plan"], "level_price": e["price"],
                 "touched": False, "pending": False,
                 "worst": e["price"] + (p.peg_cap if e["side"] == "buy" else -p.peg_cap)}
            self.orders[o["id"]] = o
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
        """Of the entries that could be placed right now, a plain one is held back while a spike one serves one of
        the same seats: the contenders for a seat are ranked once, before any order exists, and only the best is placed."""
        if self._spike(e):
            return False
        mine = self._canon(res["serves"])
        return any(d is not e and self._spike(d) and mine & self._canon(r["serves"]) for d, r in cands)

    def _seat_has_order(self, res):
        """An order that is already resting for one of these seats stands: it is never pulled for a contender that
        shows up later, and no second order is put on the same seat."""
        mine = self._canon(res["serves"])
        return any(mine & self._canon(o["serves"]) for o in self.orders.values())

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
                if self._dec_ns:
                    o["requote_due"] = True         # the algo chases at its next snapshot (_decide)
                else:
                    self._requote(o, t, px)

    def _requote(self, o, t, px):
        """Chase: move a touched-but-unfilled order one step closer to market. The new price is judged like
        a new order (gates, stop, target); if it would fail, the order is pulled."""
        buy = o["side"] == "buy"
        step = self.p.peg_step
        new = min(o["price"] + step, o["worst"]) if buy else max(o["price"] - step, o["worst"])
        res = self._evaluate(o, new, t)
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
        pt, pp = self.zz.most_recent("crest" if is_long else "trough", t0, after=p1)
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
        if self.p.globex_block and self.p.globex_open[0] <= sod < self.p.globex_open[1]:
            return "globex_open"
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
            fade = {(b, d) for b, d, pb in o["serves"] if pb == "fade"}
            re = self._plan({**o, "boxes": {k: v for k, v in o["boxes"].items() if k in fade}}, fill_px, t)
            plan = plan if isinstance(re, str) else re
        stop, target, src = plan
        o["plan"] = plan
        self._fill(o, t, fill_px, serves, stop, target, src)
        self._rebuild_entries(t)

    def _plan(self, o, fill, t):
        lv, typ = o["level"], o["type"]
        is_long = typ == "LHPB"
        tick = self.p.tick
        stop, src, stop_level = None, None, None
        if lv.get("is_spike"):
            bar = self.lv.bars.get(pd.Timestamp(lv["formation_time"]).value)
            if bar is None:
                return "no_stop"
            ext = bar[2] if is_long else bar[1]
            stop = ext - tick if is_long else ext + tick
            if (stop >= fill) if is_long else (stop <= fill):
                return "no_stop"
            src = "p0_spike"
            stop_level = lv
        else:
            pool = [x for x in self.lv.live_for_stops(typ) if abs(x["price"] - fill) <= self.p.stop_radius]
            extreme = "breakout_low" if is_long else "breakout_high"
            cands = [(x[extreme] + (-tick if is_long else tick)) for x in pool]
            cands = [s for s in cands if ((s < fill) if is_long else (s > fill))]
            if not cands:
                return "no_stop"
            stop = min(cands) if is_long else max(cands)
            src = "thrust"
            stop_level = next((x for x in pool if x[extreme] + (-tick if is_long else tick) == stop), None)
        if abs(stop - fill) < self.p.min_stop:
            wide = self._wider_stop(typ, fill, t)
            if wide is not None:
                stop, src = wide, "wide_p1"
        tg = self._target(o, fill, t)
        if isinstance(tg, str):
            return tg
        pp, meta = tg
        plan = Plan((stop, pp, src))
        plan.meta = {"stop_level": stop_level, **meta}
        return plan

    def _target(self, o, fill, t, mode=None):
        """(target price, meta) for a fill at `fill`, or "no_target"; every rule 0.25-50 pt from the fill on the favourable
        side (min_target / max_target). `mode` (default Params.target_mode), the old M1/M2 reports' three target rules:

        - "range_edge": the faded range's edge, the range LOW for a long (LHPB below it), the range HIGH for a short (LLPB
          above it); the merged range's H1 edges; with several ranges the nearest edge inside the band (old report
          `_range_edge_target`). Needs fade seats in `o["boxes"]`; without any (go-with) the zigzag rule is used.
        - "zigzag" (report "swing-extreme"): the most recent confirmed zigzag trough (long) / crest (short) since the
          level's P1, at its own price; only that one pivot is tried (old `_swing_extreme_target`).
        - "opposite_zz" (report "opposite-zz"): the most recent confirmed zigzag crest (long) / trough (short) since P1,
          where the leg into the retest began; then, of the opposite-type trading-bar P0s still standing (live, or an
          untracked plain-P0 untouched since its breakout: `live_for_stops`) formed at or after that pivot, the FARTHEST
          one inside the band (highest for a long, lowest for a short; ties: earliest formed) (old
          `_opposite_m5_zz_target`)."""
        lv, is_long = o["level"], o["type"] == "LHPB"
        mode = mode or self.p.target_mode
        lo_b, hi_b = self.p.min_target, self.p.max_target
        p1 = pd.Timestamp(lv["breakout_time"]).value
        cutoff = self._tb_floor(t)
        edges = self._fade_edges(o.get("boxes")) if mode == "range_edge" else []
        if edges:
            cand = []
            for lo, hi, st, n in edges:
                px = lo if is_long else hi
                dist = (px - fill) if is_long else (fill - px)
                if lo_b <= dist <= hi_b:
                    cand.append((dist, px, lo, hi, st))
            if not cand:
                return "no_target"
            _, pp, blo, bhi, pt = min(cand, key=lambda c: c[0])
            return pp, {"target_src": "range_edge", "edge": "low" if is_long else "high", "pivot_time": pt,
                        "pivot_price": pp, "box_low": blo, "box_high": bhi, "n_boxes": len(edges)}
        if mode == "opposite_zz":
            pt, pv = self.zz.most_recent("crest" if is_long else "trough", cutoff, after=p1)
            if pt is None:
                return "no_target"
            opp = "LLPB" if is_long else "LHPB"
            cand = []
            for x in self.lv.live_for_stops(opp):
                ft = pd.Timestamp(x["formation_time"]).value
                if ft < pt:
                    continue
                dist = (x["price"] - fill) if is_long else (fill - x["price"])
                if lo_b <= dist <= hi_b:
                    cand.append((-dist, ft, x))
            if not cand:
                return "no_target"
            _, _, best = min(cand, key=lambda c: (c[0], c[1]))
            return float(best["price"]), {"target_src": "opposite_zz", "pivot_time": pt, "pivot_price": pv,
                                          "level": dict(best)}
        # zigzag (swing extreme)
        pt, pp = self.zz.most_recent("trough" if is_long else "crest", cutoff, after=p1)
        if pt is None:
            return "no_target"
        dist = (pp - fill) if is_long else (fill - pp)
        if not (lo_b <= dist <= hi_b):
            return "no_target"
        return pp, {"target_src": "zigzag", "pivot_time": pt, "pivot_price": pp, "pivot_kind": "trough" if is_long else "crest"}

    def _fade_edges(self, boxes):
        """[(low, high, start ns, box id)] of the ranges a plan fades: the merged range's H1 edges, else the M5 box's own
        (`range_edges`); its start is the H1 box's, else the M5 box's. `boxes` = {(box id, direction): episode} of the
        order's fade seats only (go-with seats are not passed)."""
        out = []
        for ep in (boxes or {}).values():
            lo, hi, h1_start, _ = self.range_edges(ep["box"])
            out.append((lo, hi, h1_start if h1_start is not None else ep["box"]["start_t"], ep["box_id"]))
        return out

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

    def _box_snapshot(self, bid, direction, ep):
        """The range box behind one seat, as it stands now, for the report: edges, times, the zigzag pivots that built it."""
        b, m5 = ep["box"], self.box.m5
        r = self.box.readers.get(ep["box_id"])
        prior = [x for x in (r.episodes if r else []) if x["direction"] != ep["direction"] and x["breakout_time"] < ep["breakout_time"]]
        piv = []
        for kind, i, price, j in self.box.pivots:
            if b["start_i"] <= i <= b["last_i"] or i == b.get("break_i"):
                piv.append({"kind": kind, "time": m5.t[i], "price": float(price), "brk": i == b.get("break_i")})
        lo, hi, h1_start, members = self.range_edges(b)
        return {"id": bid, "direction": direction, "low": lo, "high": hi,
                "m5_low": float(b["low"]), "m5_high": float(b["high"]), "h1_start_t": h1_start,
                "merged_ids": list(members), "h1": h1_start is not None,
                "start_t": b["start_t"], "break_t": b.get("break_t"), "breakout_time": ep.get("breakout_time"),
                "status": b["status"], "provisional": bool(ep.get("provisional")), "fade2": bool(prior),
                "prior_time": prior[0]["breakout_time"] if prior else None,
                "prior_dir": ("up" if prior[0]["direction"] == "up" else "down") if prior else None,
                "pivots": piv, "n": b["n"], "seed_high": float(b["seed_high"]), "seed_low": float(b["seed_low"])}

    def _fill(self, o, t, price, serves, stop, target, src):
        sod, ts = _pt_sod(t)
        day = ts.normalize() if sod < self.p.reopen else (ts + pd.Timedelta(days=1)).normalize()
        eod = (day + pd.Timedelta(seconds=self.p.eod_flat)).tz_convert("UTC").value
        tr = {"id": len(self.trades), "order": o["id"], "t": t, "side": o["side"], "type": o["type"],
              "price": price, "stop": stop, "target": target, "stop_src": src, "serves": serves,
              "eod": eod, "trails": 0, "level_price": o["level_price"], "level_spike": bool(o["level"].get("is_spike")), "p0_kind": o["level"].get("p0_kind"),
              "boxes": [(b, d) for b, d, _ in serves], "exit": None,
              # what the M1 fade report shows of the trade (nothing here decides anything)
              "armed_t": o.get("armed_t"), "level": o["level"], "decided": o.get("decided", o["level"]), "cluster_n": o["cluster"],
              "members": o.get("members") or [o["level"]], "swerve": o.get("swerve"),
              "mfe": 0.0, "mae": 0.0, "dd": 0.0, "trail_log": [],
              "box_info": [self._box_snapshot(b, d, o["boxes"].get((b, d))) for b, d, _ in serves
                           if o["boxes"].get((b, d)) is not None]}
        meta = getattr(o.get("plan"), "meta", None)
        tr["plan_meta"] = meta
        stop_pts, tgt_pts = abs(stop - price), abs(target - price)
        tr["stop_pts"], tr["target_pts"], tr["r_planned"] = stop_pts, tgt_pts, tgt_pts / stop_pts
        # the report's row tags that describe the trade (informational there; the ones it excludes by default are gates here)
        tags = []
        if o["level"].get("p0_kind") == "plain-P0":
            tags.append("plain_p0")
        if o["cluster"] == 1:
            tags.append("no_confl")
        if o.get("note"):
            tags.append("better_entry")
        if tr["r_planned"] < self.p.tag_min_r:
            tags.append("r_below_min")
        if not self.p.globex_block and self.p.globex_open[0] <= sod < self.p.globex_open[1]:
            tags.append("globex_eth_open")
        tr["tags"] = tags
        tr["target_rule"] = self.p.target_mode
        tr["views"] = self._views(o, t, price, stop, target, meta, eod, serves)
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

    # ------------------------------------------------------------------ what-if brackets (for the report only)
    TARGET_RULES = ("range_edge", "zigzag", "opposite_zz")

    def _views(self, o, t, price, stop, target, meta, eod, serves):
        """The trade's what-if brackets, for the report's target-rule and management toggles: for every target rule, the
        same fill and stop with that rule's target, followed tick by tick plain (stop / target / end-of-day flat) and
        managed (plus the stop trail and the RR floor, as a real position). They never place, pull or fill an order, take
        a seat or a slot: nothing the engine decides reads them. {(rule, managed): view}; a rule with no target at this
        fill is left out."""
        fade = {(b, d) for b, d, pb in serves if pb == "fade"}
        of = {**o, "boxes": {k: v for k, v in (o.get("boxes") or {}).items() if k in fade}}
        out = {}
        for rule in self.TARGET_RULES:
            if rule == self.p.target_mode:
                tg = (target, meta or {})
            else:
                tg = self._target(of, price, t, rule)
                if isinstance(tg, str):
                    continue
            for managed in (False, True):
                v = {"rule": rule, "managed": managed, "side": o["side"], "type": o["type"], "price": price,
                     "stop": stop, "target": tg[0], "meta": tg[1], "stop_pts": abs(stop - price), "eod": eod,
                     "trails": 0, "trail_log": [], "exit": None, "mfe": 0.0, "mae": 0.0, "dd": 0.0}
                out[(rule, managed)] = v
                self.shadows.append(v)
        return out

    def _shadow_tick(self, t, px):
        for v in list(self.shadows):
            long = v["side"] == "buy"
            d = (px - v["price"]) if long else (v["price"] - px)
            if d > v["mfe"]:
                v["mfe"] = d
            elif -d > v["mae"]:
                v["mae"] = -d
            if v["mfe"] - d > v["dd"]:
                v["dd"] = v["mfe"] - d
            if (px <= v["stop"]) if long else (px >= v["stop"]):
                self._shadow_exit(v, t, v["stop"], "stop")
            elif (px >= v["target"]) if long else (px <= v["target"]):
                self._shadow_exit(v, t, v["target"], "target")
            elif t >= v["eod"]:
                self._shadow_exit(v, t, px, "eod_flat")

    def _shadow_exit(self, v, t, price, why):
        v["exit"] = {"t": t, "price": price, "why": why}
        v["pnl"] = (price - v["price"]) if v["side"] == "buy" else (v["price"] - price)
        v["r"] = v["pnl"] / v["stop_pts"]
        self.shadows.remove(v)

    def _shadow_manage(self, tt, close):
        """`_manage_tb` for the managed what-if brackets."""
        p = self.p
        grp_all = self.lv.broke_by_bar.get(tt, ())
        ids = set()
        for v in self.shadows:
            if not v["managed"]:
                continue
            ids.add(id(v))
            long = v["side"] == "buy"
            grp = [x for x in grp_all if x["type"] == v["type"]]
            if not grp or len(grp) < self._trail_need(grp, v["type"]):
                continue
            new = grp[0]["breakout_low"] - p.tick if long else grp[0]["breakout_high"] + p.tick
            if (new - v["stop"]) * (1 if long else -1) > 0:
                v["trail_log"].append((tt + self.tb_ns, new))
                v["stop"] = new
                v["trails"] += 1
        self._rr_shadow = (close, ids) if ids else None

    def _shadow_rr(self, t, px, bid, ask):
        close, ids = self._rr_shadow
        self._rr_shadow = None
        for v in list(self.shadows):
            if id(v) not in ids:
                continue
            sgn = 1 if v["side"] == "buy" else -1
            reward, risk = sgn * (v["target"] - close), sgn * (close - v["stop"])
            if risk > 0 and reward / risk <= self.p.rr_floor:
                quote = (bid if sgn > 0 else ask)
                self._shadow_exit(v, t, px if quote is None else quote, "rr_floor")

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
                tr["trail_log"].append((tt + self.tb_ns, new))     # (the thrust candle's close, new stop)
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
        if why == "eod_flat":
            tr["tags"].append("eod_flat")
        pnl = (price - tr["price"]) if tr["side"] == "buy" else (tr["price"] - price)
        tr["pnl"], tr["r"] = pnl, pnl / tr["stop_pts"]
        self.open.remove(tr)
        self.log("trade_exit", t, trade=tr["id"], why=why, price=price, pnl=pnl, r=round(tr["r"], 2))
        self._retrig()
