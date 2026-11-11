"""algo.py with the user's OB rules of 2026-11-10 (second pass):
a) an OB is one opposite-colour candle whose body is at least MIN_BODY ticks, or else a run of consecutive opposite-colour
   candles whose combined body (first open -> last close) is;
b) the stop-entry only counts while a displacement is IN PROGRESS: at the second price crosses the OB's far edge, the forming
   M2 (or M1) candle's low so far is above the high of the candle two before it (an FVG is being left behind).
Armed from the first M5 close back; a new extreme inside the 4-candle window only moves the stop. Long logic; shorts mirrored."""
import pickle
import numpy as np, pandas as pd
import algo
from algo import (CB, PT, TICK, K, Path, s1_arrays, secs, pool_above, sbars, fmt, COL, WINDOW, VICINITY_ATR, pullback, load)

MIN_BODY = 4          # ticks
SCAN_CAP = 86400      # the stop-entry is looked for at most this long after the first close back


def find_ob(tb, oo, hh, ll, cc, tf, upto, first_q):
    """The latest OB among the candles closed by `upto`, not older than candle first_q."""
    q = int(np.searchsorted(tb, upto - tf * 60, side="right")) - 1
    while q >= first_q:
        if cc[q] < oo[q]:
            a = q
            while True:
                if oo[a] - cc[q] >= MIN_BODY * TICK - 1e-9:
                    hi = float(hh[a:q + 1].max())
                    return dict(tf=tf, q0=a, q1=q, t=int(tb[a]), t1=int(tb[q] + tf * 60), lo=float(ll[a:q + 1].min()), hi=hi, lvl=hi + TICK,
                                n=q - a + 1, body=int(round((oo[a] - cc[q]) / TICK)))
                if a - 1 >= first_q and cc[a - 1] < oo[a - 1]: a -= 1
                else: break
            q = a - 1
        else:
            q -= 1
    return None


def run(row, m1all, h1):
    t, bull = row.Index, bool(row.bullish)
    sgn = 1 if bull else -1
    lo_w, hi_w = ("low", "high") if bull else ("high", "low")
    swing = sgn * row.swing_price
    tu = int(t.timestamp())
    P = Path(*s1_arrays(t - pd.Timedelta(hours=2), t + pd.Timedelta(days=5)), sgn)
    m1 = m1all.loc[t - pd.Timedelta(days=3): t + pd.Timedelta(days=2)]
    B5, bt, o, h, l, c = sbars(m1, 5, sgn)
    B2, bt2, o2, h2, l2, c2 = sbars(m1, 2, sgn)
    B1, bt1, o1, h1m, l1, c1 = sbars(m1, 1, sgn)
    hh = h1.loc[t - pd.Timedelta(days=150): t + pd.Timedelta(days=5)]
    h1hi = (hh["high"] if bull else -hh["low"]).to_numpy(float)
    h1lo = (hh["low"] if bull else -hh["high"]).to_numpy(float)
    h1st = secs(hh.index); h1end = h1st + 3600
    hb = h1.loc[:t].iloc[-16:-1]; pc = hb["close"].shift()
    atr = float(pd.concat([hb["high"] - hb["low"], (hb["high"] - pc).abs(), (hb["low"] - pc).abs()], axis=1).max(axis=1).iloc[1:].mean())
    V = VICINITY_ATR * atr
    out = dict(row=t, bull=bull, cat=None, log=[], draws=[], trade=None, trades=[], cursor=tu + 4 * 3600)
    log = lambda u, s_: out["log"].append((int(u), s_))
    px = lambda p: f"{sgn * p:.2f}"

    def rect(id, t0, t1, p0, p1, col, label, alpha=0.15, la="tl"):
        out["draws"].append(dict(id="ict_" + id, type="rect", t0=int(t0), p0=float(sgn * p0), t1=int(t1), p1=float(sgn * p1), color=col, fill=col,
                                 alpha=alpha, width=1, dash="solid", label=label, la=la))

    def ray(id, t0, p, col, label, dash="solid"):
        out["draws"].append(dict(id="ict_" + id, type="ray", t=int(t0), p=float(sgn * p), color=col, width=1, dash=dash, label=label))

    def stacked(T, E):
        n = int(np.searchsorted(h1end, T, side="right"))
        lo_ = h1lo[:n]; piv = CB._pivots(lo_, K, True)
        mn_after = np.r_[np.minimum.accumulate(lo_[::-1])[::-1][1:], np.inf]
        return [(int(h1st[p]), float(lo_[p])) for p in piv if lo_[p] < mn_after[p] and E - V <= lo_[p] < E]

    def skip(T, E, st):
        out["cat"] = "skip: stacked H1 liquidity beyond the sweep"
        for q, (ts_, p_) in enumerate(st):
            ray(f"st{q}", ts_, p_, COL["pool"], f"untested H1 swing {lo_w} {px(p_)}: liquidity still resting", "dashed")
        ray("sweep", T - 300, E, COL["sweep"], f"sweep extreme {px(E)}")
        log(T, f"untested H1 swing {lo_w}{'s' if len(st) > 1 else ''} within {V:.2f} pt (1 x H1 ATR) beyond the extreme {px(E)}: "
               + ", ".join(px(p_) for _, p_ in st) + ": liquidity is stacked there, NO TRADE")
        out["cursor"] = int(T) + 2 * 3600
        return out

    k0, k1 = int(np.searchsorted(bt, tu)), int(np.searchsorted(bt, tu + 3600))
    s = k0 + int(np.flatnonzero(l[k0:k1] < swing)[0])
    i_s = P.at(bt[s])
    log(bt[s], f"M5 candle {fmt(bt[s])} trades past the H1 swing {lo_w} {px(swing)}: the sweep starts")
    js = [j for j in range(s, min(s + WINDOW, len(c) - 1) + 1) if c[j] > swing]
    if not js:
        out["cat"] = "no setup: no M5 close back within 4 candles"
        log(bt[s + WINDOW] + 300, f"no M5 candle from {fmt(bt[s])} to {fmt(bt[s + WINDOW])} closes back {'above' if bull else 'below'} {px(swing)}: no setup")
        return out
    j = js[0]; Tc = int(bt[j] + 300); i0 = P.at(Tc)
    E0 = float(l[s:j + 1].min())
    log(Tc, f"M5 candle {fmt(bt[j])} closes back at {px(c[j])} ({j - s} candle{'s' if j - s != 1 else ''} after the sweeping one); sweep extreme {px(E0)}: armed")
    st = stacked(Tc, E0)
    if st: return skip(Tc, E0, st)
    log(Tc, f"no other untested H1 swing {lo_w} within {V:.2f} pt (1 x H1 ATR) beyond {px(E0)}")
    rect("cb", bt[j], bt[j] + 300, l[j], h[j], COL["entry"], "close back", 0.0)
    T1 = pool_above(h1hi, h1end, Tc, float(P.h[i_s:i0].max()))
    # the sweep may still extend inside the window; after it a new extreme with no position = void
    iw = max(P.at(bt[s] + (WINDOW + 1) * 300), i0)
    Ew = float(P.l[i_s:iw].min())
    void = P.le(iw, Ew - TICK)
    i_t1 = P.ge(i0, T1) if T1 is not None else None
    first5, first2 = max(s - 1, 0), int(np.searchsorted(bt2, bt[max(s - 1, 0)]))
    seen = {}

    def scan(i_from):
        """First second price crosses an OB's far edge while a displacement is in progress: (index, ob, 'M2' / 'M1')."""
        end = min(x for x in (void, i_t1, P.at(Tc + SCAN_CAP), len(P.ts) - 1) if x is not None)
        k = int(np.searchsorted(bt2, P.ts[i_from], side="right")) - 1
        while k < len(bt2):
            a, b = max(P.at(bt2[k]), i_from), P.at(bt2[k] + 120)
            if a > end: return None
            b = min(b, end + 1)
            if b > a:
                obs = [x for x in (find_ob(bt, o, h, l, c, 5, bt2[k], first5), find_ob(bt2, o2, h2, l2, c2, 2, bt2[k], first2)) if x]
                prev = np.r_[P.c[a - 1], P.c[a:b - 1]]
                best = None
                for ob in obs:
                    key = (ob["tf"], ob["t"], ob["t1"])
                    if key not in seen:
                        seen[key] = ob
                        log(bt2[k], f"M{ob['tf']} OB: " + (f"candle {fmt(ob['t'])}" if ob["n"] == 1 else f"{ob['n']} candles {fmt(ob['t'])} - {fmt(ob['t1'] - ob['tf'] * 60)}")
                            + f", body {ob['body']} ticks, {px(ob['lo'])} - {px(ob['hi'])}; stop-entry {px(ob['lvl'])}")
                    for i in a + np.flatnonzero((P.h[a:b] >= ob["lvl"] - 1e-9) & (prev < ob["lvl"] - 1e-9)):
                        i = int(i); why = None
                        if k >= 2 and P.l[P.at(bt2[k]):i + 1].min() > h2[k - 2] + 1e-9:
                            why = f"M2: the forming {fmt(bt2[k])} candle's low {px(P.l[P.at(bt2[k]):i + 1].min())} is beyond the {fmt(bt2[k - 2])} candle's {hi_w} {px(h2[k - 2])}"
                        else:
                            km = int(np.searchsorted(bt1, P.ts[i], side="right")) - 1
                            if km >= 2 and P.l[P.at(bt1[km]):i + 1].min() > h1m[km - 2] + 1e-9:
                                why = f"M1: the forming {fmt(bt1[km])} candle's low {px(P.l[P.at(bt1[km]):i + 1].min())} is beyond the {fmt(bt1[km - 2])} candle's {hi_w} {px(h1m[km - 2])}"
                        if why is None:
                            if (key, "miss") not in seen:
                                seen[(key, "miss")] = 1
                                log(P.ts[i], f"price crosses the M{ob['tf']} OB's edge {px(ob['lvl'])} at {fmt(P.ts[i], True)} with no gap being left on M2 or M1: no displacement, no entry")
                            continue
                        if best is None or i < best[0]: best = (i, ob, why)
                        break
                if best: return best
            k += 1
        return None

    def trade(i_f, entry, E, kind):
        tgt = pool_above(h1hi, h1end, P.ts[i_f], max(float(P.h[i_s:i_f + 1].max()), entry))
        if tgt is None: return None
        R, res, ix, ex = P.sim(i_f, entry, E - TICK, tgt)
        return dict(kind=kind, t_fill=int(P.ts[i_f]), entry=sgn * entry, stop=sgn * (E - TICK), target=sgn * tgt, risk=entry - E + TICK,
                    rr=(tgt - entry) / (entry - E + TICK), R=R, out=res, t_exit=int(P.ts[ix]), exit=sgn * ex, ix=ix)

    def show(tr, label):
        k_ = len(out["trades"])
        rect(f"entry{k_}", tr["t_fill"], tr["t_exit"], sgn * tr["entry"], sgn * tr["entry"], COL["entry"], f"{label} {tr['entry']:.2f} ({tr['R']:+.2f} R)", 0.0, "l")
        out["draws"] = [d for d in out["draws"] if d["id"] != "ict_target"]
        ray("target", tr["t_fill"], sgn * tr["target"], COL["pool"], f"target: H1 swing {hi_w} {tr['target']:.2f}", "dashed")
        log(tr["t_exit"], f"exit: {tr['out']} at {tr['exit']:.2f}, {fmt(tr['t_exit'], True)}: {tr['R']:+.2f} R (planned {tr['rr']:.1f} R)")
        out["trades"].append(tr); out["trade"] = tr
        out["cursor"] = max(tr["t_exit"] + 1800, Tc + 2 * 3600)

    # the pullback entry, from the sweep's final extreme
    ieb = s + int(np.argmin(l[s:min(s + WINDOW, len(l) - 1) + 1])) if Ew < E0 else s + int(np.argmin(l[s:j + 1]))
    i_e = i_s + int(np.argmin(P.l[i_s:iw])) if iw > i_s else i_s
    Tp = max(Tc, int(P.ts[i_e]) + 1)
    pb = pullback(P, sgn, m1, bt, h, l, c, s, ieb, Ew, Tp, P.at(Tp), T1, void, h1hi, h1st, h1end, px, lo_w, hi_w)
    i_from = i0
    while True:
        se = scan(i_from)
        use_pb = pb["f"] is not None and pb["f"] >= i_from and (se is None or pb["f"] < se[0])
        if se is None and not use_pb:
            break
        if use_pb:
            f = pb["f"]; E = Ew
            tr = trade(f, pb["price"], E, "pullback: " + pb["why"])
            if tr is None: out["cat"] = "no trade: no H1 swing left as a target"; break
            for u_, s_ in pb["log"]: log(u_, s_)
            for d_ in pb["draws"]: out["draws"].append(d_)
            log(P.ts[f], f"pullback: limit at {px(pb['price'])} fills {fmt(P.ts[f], True)} ({pb['why']}); stop {tr['stop']:.2f}, target = nearest untested H1 swing {hi_w} {tr['target']:.2f}")
            out["cat"] = "pullback entry: " + pb["kind"]; show(tr, "pullback entry")
            pb = dict(f=None, log=[], draws=[], none="")
        else:
            f, ob, why = se
            E = float(P.l[i_s:f + 1].min())
            st = stacked(P.ts[f], E)
            if st: return skip(int(P.ts[f]), E, st)
            entry = max(ob["lvl"], float(P.o[f]))
            tr = trade(f, entry, E, f"stop-entry over the M{ob['tf']} OB")
            if tr is None: out["cat"] = "no trade: no H1 swing left as a target"; break
            rect(f"ob{len(out['trades'])}", ob["t"], P.ts[f] + 600, ob["lo"], ob["hi"], COL["ob"], f"M{ob['tf']} OB", 0.15)
            log(P.ts[f], f"price crosses {px(ob['lvl'])} at {fmt(P.ts[f], True)} while displacing ({why}): stop-entry fills at {px(entry)}; "
                         f"stop {tr['stop']:.2f} (a tick beyond the extreme {px(E)}), target = nearest untested H1 swing {hi_w} {tr['target']:.2f}")
            out["cat"] = f"stop-entry over the M{ob['tf']} OB while price displaces"; show(tr, "stop-entry")
        m = int(np.searchsorted(bt, tr["t_exit"], side="right")) - 1
        if tr["out"] != "stop" or m > s + WINDOW: break
        log(tr["t_exit"], "stopped inside the 4-candle window: the sweep goes on, still armed")
        i_from = tr["ix"] + 1
    Efin = Ew if not out["trades"] else sgn * out["trades"][-1]["stop"] + TICK
    ray("sweep", P.ts[i_e], Efin, COL["sweep"], f"sweep extreme {px(Efin)} (stop a tick beyond)")
    if not out["trades"] and out["cat"] is None:
        for u_, s_ in pb["log"]: log(u_, s_)
        for q, ob in enumerate(seen[k_] for k_ in seen if len(k_) == 3):
            rect(f"obx{q}", ob["t"], ob["t1"] + 3600, ob["lo"], ob["hi"], COL["ob"], f"M{ob['tf']} OB", 0.12, "tl" if ob["tf"] == 5 else "tr")
        if void is not None and (i_t1 is None or void < i_t1):
            out["cat"] = "no entry: new extreme after the window (setup void)"
            log(P.ts[void], f"price trades beyond the sweep extreme {px(Ew)} at {fmt(P.ts[void], True)}, after the 4-candle window, with no entry: setup void")
            out["cursor"] = int(P.ts[void]) + 3600
        elif i_t1 is not None:
            out["cat"] = "no entry: first H1 swing reached, no displacement entry and no qualifying pullback"
            log(P.ts[i_t1], f"price reaches the first H1 swing {hi_w} {px(T1)} at {fmt(P.ts[i_t1], True)} with no entry")
            out["cursor"] = int(P.ts[i_t1]) + 1800
        else:
            out["cat"] = "no entry: nothing triggered"
    out["log"].sort(key=lambda z: z[0])
    return out


if __name__ == "__main__":
    rows, m1, h1 = load()
    res = []
    for n, row in enumerate(rows.itertuples()):
        r = run(row, m1, h1); r["n"] = n + 1
        res.append(r)
        print(n + 1, f"{row.Index:%m-%d %H:%M}", "bull" if row.bullish else "bear", "|", r["cat"], "|",
              "; ".join(f"{tr['kind'].split(':')[0]} {tr['out']} {tr['R']:+.2f}R rr {tr['rr']:.1f}" for tr in r["trades"]), flush=True)
    for r in res:
        for tr in r["trades"]: tr.pop("ix", None)
    pickle.dump(res, open("algo.pkl", "wb"))
    df = pd.DataFrame([dict(cat=tr["kind"].split(":")[0], R=tr["R"], win=tr["out"] == "target") for r in res for tr in r["trades"]]
                      + [dict(cat=r["cat"].split(":")[0], R=np.nan, win=np.nan) for r in res if not r["trades"]])
    print(df.groupby("cat").agg(n=("R", "size"), trades=("R", "count"), win=("win", "mean"), avgR=("R", "mean"), totR=("R", "sum")).round(2).to_string())
