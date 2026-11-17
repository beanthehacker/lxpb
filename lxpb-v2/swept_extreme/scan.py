"""Swept-extreme study (user, 2026-11-12): trades AGAINST an H1 cross bar, back to the extreme it made while sweeping.
A bearish cross bar (swept a swing high) -> LONG trades targeting the cross bar's high; a bullish one -> SHORT trades
targeting its low. All logic is written for the LONG case; bullish rows are mirrored by negating prices.

Four entries, each looked for after the cross bar has closed and only while its extreme (the target) has NOT traded again
(the target already reached = no entry), at most MAX_DAYS after the close:
  T1   reclaim thrust: the first M5 thrust candle whose body crosses the low of the original order block; market at its
       close (the open of the first second after it), stop a tick under the thrust candle.
  T2a  that thrust, then a limit at the top of the gap (FVG) it leaves; stop a tick under the thrust candle.
  T2b  that thrust, then a limit at the LXPB level it broke out of (its P1 is the thrust candle); same stop.
  T3   stop-entry a tick over an M5 swing high made at the order block or at a gap of the down leg; stop a tick under the
       latest confirmed M5 swing low before the trigger.
Fills and exits are read from the 1 s bars: first second reaching the stop or the target after the fill, the stop when
both are in one second, a second opening through the stop fills at its open. No time-of-day rule, no costs.

Definitions reused: order block = ict_sfp/algo2.find_ob (opposite-colour candle or run with a body of 4 ticks);
thrust = ict_sfp/ict.py's displacement candle (range >= 1.5 x ATR(14), body >= half its range); gap = 3-candle FVG;
swing = 2-bar fractal (find_sfp's K); LXPB levels = lxpb.py's ledger (cross_bar report's `ledger`).

`scan(rows, h1)` -> {cross bar time: {"base": {...}, "T1": rec, "T2a": rec, "T2b": rec, "T3": rec}}; a rec has `res`
("target" / "stop" / "open" / "none") and, for "none", `why`. Prices in a rec are real prices.

    python swept_extreme/scan.py            # the 2026 tally
"""
import os, sys
import numpy as np, pandas as pd
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "cross_bar"))
import render_cross_bar_trades as CT
CB = CT.CB
from es_data import load_es

PT, TICK, K = CB.PT, 0.25, CB.SWING_K
MIN_BODY = 4                 # ticks: order block body (ict_sfp/algo2.py)
OB_LOOKBACK = 12             # M5 candles before the sweep candle the order block may start in
DISP_ATR, DISP_BODY = 1.5, 0.5   # thrust candle (ict_sfp/ict.py displacement)
NEAR = 2 * TICK              # a swing high this close to a zone counts as made at it
MAX_DAYS = 5                 # a signal is looked for this long after the cross bar's close
RUN_DAYS = 10                # ... and a trade is followed this long after it
TECHS = ("T1", "T2a", "T2b", "T3")


def secs(idx):
    return idx.as_unit("s").asi8


def signed(B, sgn):
    o, c = sgn * B["open"].to_numpy(float), sgn * B["close"].to_numpy(float)
    h, l = (B["high"].to_numpy(float), B["low"].to_numpy(float)) if sgn > 0 else (-B["low"].to_numpy(float), -B["high"].to_numpy(float))
    return secs(B.index), o, h, l, c


def find_ob(o, h, l, c, e, first_q):
    """Long case: the latest up-close candle (or run of them) with a body of MIN_BODY ticks, ending at or before candle e."""
    q = e
    while q >= first_q:
        if c[q] > o[q]:
            a = q
            while True:
                if c[q] - o[a] >= MIN_BODY * TICK - 1e-9:
                    return dict(a=a, b=q, lo=float(l[a:q + 1].min()), hi=float(h[a:q + 1].max()))
                if a - 1 >= first_q and c[a - 1] > o[a - 1]: a -= 1
                else: break
            q = a - 1
        else:
            q -= 1
    return None


def _first(mask, i0):
    k = int(np.argmax(mask)) if len(mask) else 0
    return i0 + k if len(mask) and mask[k] else None


class Path:
    """The row's 1 s bars from the cross bar's close on, in signed space."""
    def __init__(self, s, sgn):
        self.t, self.o, self.h, self.l, self.c = signed(s, sgn)
    def at(self, T):
        return int(np.searchsorted(self.t, T))
    def ge(self, i0, level, i1=None):
        return _first(self.h[i0:i1] >= level - 1e-9, i0)
    def le(self, i0, level, i1=None):
        return _first(self.l[i0:i1] <= level + 1e-9, i0)
    def sim(self, f, entry, stop, target):
        s, g = self.le(f, stop), self.ge(f + 1, target)
        if s is None and g is None:
            return "open", len(self.t) - 1, float(self.c[-1])
        if g is None or (s is not None and s <= g):
            return "stop", s, stop if s == f else min(stop, float(self.o[s]))
        return "target", g, target


def _when(u):
    return CB._pt(pd.Timestamp(int(u), unit="s", tz="UTC"))


def run_row(row, m5all, led, h1):
    t, bull = row.Index, bool(row.bullish)
    sgn = -1 if bull else 1                      # the trade is LONG after a bearish bar
    close_t = t + pd.Timedelta(hours=1)
    cu = close_t.timestamp()
    B = m5all.loc[t - pd.Timedelta(days=2): close_t + pd.Timedelta(days=MAX_DAYS + 1)]
    bt, o, h, l, c = signed(B, sgn)
    k0, k1 = int(np.searchsorted(bt, t.timestamp())), int(np.searchsorted(bt, cu))
    e = k0 + int(np.argmax(h[k0:k1])); X = float(h[e])          # sweep candle, the cross bar's extreme = the target
    older = CB.older_swings(h1, h1.index.get_loc(t), row.swing_time, row.swing_price, bull)
    P = Path(CT.s1_between(close_t, close_t + pd.Timedelta(days=RUN_DAYS)), sgn)
    g0 = P.ge(0, X)                                             # the extreme trades again: nothing is entered from here on
    cap = cu + MAX_DAYS * 86400
    t_gone = float(P.t[g0]) if g0 is not None and P.t[g0] <= cap else None
    dead = t_gone if t_gone is not None else min(cap, float(P.t[-1]))    # signals must be complete by here
    over = (f"target already reached {_when(t_gone)}, before any entry" if t_gone is not None
            else f"no entry within {MAX_DAYS} days of the cross bar's close")
    base = dict(row=t, bull=bull, sgn=sgn, X=sgn * X, t_sweep=int(bt[e]), swing_time=row.swing_time, swing_price=float(row.swing_price),
                beyond=[float(p) for _, p, hit in older if not hit and sgn * p > X], t_gone=t_gone, dead=dead,
                reached=t_gone is not None)
    out = {"base": base}
    none = lambda why, **kw: dict(res="none", why=why, reached=why.startswith("target already reached"), **kw)
    ob = find_ob(o, h, l, c, e, max(e - OB_LOOKBACK, 0))
    if ob is None:
        for k in TECHS: out[k] = none("no order block at the sweep")
        return out
    base.update(ob_t0=int(bt[ob["a"]]), ob_t1=int(bt[ob["b"]]), ob_lo=sgn * ob["lo"], ob_hi=sgn * ob["hi"])
    pcl = np.r_[np.nan, c[:-1]]
    atr = pd.Series(np.fmax(h - l, np.fmax(np.abs(h - pcl), np.abs(l - pcl)))).rolling(14).mean().to_numpy()

    def trade(f, entry, stop, **kw):
        if entry >= X - TICK + 1e-9: return none("entry at the target: no room", **kw)
        if entry - stop < TICK - 1e-9: return none("entry at the stop", **kw)
        res, ix, px = P.sim(f, entry, stop, X)
        risk = entry - stop
        return dict(res=res, t_fill=int(P.t[f]), entry=sgn * entry, stop=sgn * stop, risk=risk, rr=(X - entry) / risk,
                    R=(px - entry) / risk, t_exit=int(P.t[ix]), exit=sgn * px, **kw)

    # ---------- T1 / T2: the first reclaim thrust
    j = next((j for j in range(max(k1, 15), len(bt) - 1)
              if bt[j] + 300 <= dead and c[j] > o[j] and np.isfinite(atr[j - 1]) and h[j] - l[j] >= DISP_ATR * atr[j - 1]
              and c[j] - o[j] >= DISP_BODY * (h[j] - l[j]) and o[j] <= ob["lo"] + 1e-9 and c[j] > ob["lo"]), None)
    if j is None:
        for k in ("T1", "T2a", "T2b"): out[k] = none(over.replace("entry", "thrust candle"))
    else:
        Tc = bt[j] + 300; stop = l[j] - TICK
        thr = dict(t_thrust=int(bt[j]), thr_lo=sgn * l[j], thr_hi=sgn * h[j])
        f = P.at(Tc)
        out["T1"] = trade(f, float(P.o[f]), stop, **thr) if f < len(P.t) else none("data ends at the thrust", **thr)

        def limit(level, T0, **kw):
            i0 = P.at(T0)
            f, g = P.le(i0, level), P.ge(i0, X)
            if f is None or (g is not None and g < f):
                return none(f"limit not filled: target reached first, {_when(P.t[g])}" if g is not None else "limit not filled by the data's end",
                            **({"t_dead": int(P.t[g])} if g is not None else {}), **thr, **kw)
            return trade(f, min(level, float(P.o[f])), stop, **thr, **kw)
        if h[j - 1] < l[j + 1] - 1e-9:                                       # the gap the thrust leaves
            out["T2a"] = limit(l[j + 1], bt[j + 1] + 300, fvg_lo=sgn * h[j - 1], fvg_hi=sgn * l[j + 1], t_fvg=int(bt[j - 1]))
        else:
            out["T2a"] = none("the thrust leaves no gap", **thr)
        lv = led[(led["type"] == ("LHPB" if sgn > 0 else "LLPB")) & (led["breakout_time"] == B.index[j])]
        lv = lv.assign(p=sgn * lv["price"]).sort_values("p")
        lv = lv[lv["p"] > l[j]]
        if len(lv):                                                          # levels the thrust broke out of: nearest to price
            lv = lv.iloc[-1]
            out["T2b"] = limit(float(lv["p"]), Tc, lvl=float(lv["price"]), t_p0=int(lv["formation_time"].timestamp()), p0_kind=lv["p0_kind"])
        else:
            out["T2b"] = none("the thrust is not the P1 of an LXPB level", **thr)

    # ---------- T3: stop-entry over an M5 swing high made at the order block or at a gap of the down leg
    out["T3"] = none(over.replace("entry", "swing at the order block or a gap"))
    piv_hi = [int(p) for p in CB._pivots(h, K, False) if p > e and bt[p + K] + 300 <= dead]
    piv_lo = [int(p) for p in CB._pivots(l, K, True) if p > e]
    for p in piv_hi:
        zones = [("order block", ob["lo"], ob["hi"], int(bt[ob["a"]]))]
        for i in range(max(e, ob["b"]) + 1, p - 1):                          # down-leg gaps closed before the swing bar
            if l[i - 2] > h[i] + 1e-9 and c[i - 1] < o[i - 1] and i - 2 >= ob["a"]:
                zones.append(("gap", float(h[i]), float(l[i - 2]), int(bt[i - 1])))
        zs = [z for z in zones if z[1] - NEAR <= h[p] <= z[2] + NEAR and h[p] < X - 4 * TICK]
        if not zs or h[e + 1:p].max(initial=-np.inf) >= h[p]: continue        # the first lower high into the zone
        z = zs[-1]
        kw = dict(t_sh=int(bt[p]), sh=sgn * h[p], zone=z[0], z_lo=sgn * z[1], z_hi=sgn * z[2], t_zone=z[3])
        T0 = max(bt[p + K] + 300, cu); trig = h[p] + TICK
        i0 = P.at(T0)
        if h[p + 1:p + K + 1].max() >= trig or (P.at(bt[p + K] + 300) < i0 and P.h[P.at(bt[p + K] + 300):i0].max() >= trig):
            continue                                                         # taken out before the order could rest
        f = P.ge(i0, trig)
        if f is None or P.t[f] >= dead:
            out["T3"] = none(over.replace("before any entry", "before the stop-entry triggered").replace("no entry", "stop-entry not triggered"), **kw); break
        lows = [q for q in piv_lo if q > p and bt[q + K] + 300 <= P.t[f]]
        if not lows:
            out["T3"] = none("triggered before any M5 swing low was confirmed: no stop", **kw); break
        q = lows[-1]
        i_q = P.at(bt[q + K] + 300)
        if l[q + 1:q + K + 1].min() < l[q] - 1e-9 or (i_q < f and P.l[i_q:f].min() < l[q] - 1e-9):
            out["T3"] = none("the last M5 swing low was broken before the trigger: no stop", **kw); break
        out["T3"] = trade(f, max(trig, float(P.o[f])), l[q] - TICK, t_sl=int(bt[q]), sl=sgn * l[q], **kw)
        break
    return out


def load_rows(since="2026-01-01"):
    h1 = load_es("1h", tz=PT)
    rows = CB.collect(h1)
    return rows.loc[rows.index >= pd.Timestamp(since, tz=PT)].sort_index(), h1


def scan(rows, h1):
    m5 = load_es("5min", start=rows.index[0] - pd.Timedelta(days=30), tz=PT)[["open", "high", "low", "close"]]
    led = CB.ledger(m5)
    return {row.Index: run_row(row, m5, led, h1) for row in rows.itertuples()}


def tally(res):
    out = []
    for k in TECHS:
        r = [v[k] for v in res.values()]
        tr = [x for x in r if x["res"] in ("target", "stop")]
        out.append(dict(tech=k, rows=len(r), trades=len(tr), target=sum(x["res"] == "target" for x in tr),
                        open=sum(x["res"] == "open" for x in r), reached_first=sum(x["res"] == "none" and x["reached"] for x in r),
                        R=round(sum(x["R"] for x in tr), 1), median_rr=round(float(np.median([x["rr"] for x in tr])), 2) if tr else None))
    return pd.DataFrame(out)


if __name__ == "__main__":
    rows, h1 = load_rows()
    pd.set_option("display.width", 200)
    res = scan(rows, h1)
    print(tally(res).to_string(index=False))
    import collections, re
    for k in TECHS:
        print(k, dict(collections.Counter(re.sub(r" (Mon|Tue|Wed|Thu|Fri|Sat|Sun) .* PT", "", v[k].get("why", v[k]["res"])) for v in res.values())))
