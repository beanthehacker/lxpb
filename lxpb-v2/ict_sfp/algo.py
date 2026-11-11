"""M5 sweep of an H1 swing -> stop-entry over the OB while price displaces, else a pullback entry (user's spec, 2026-11-10).
Written for a LONG (sweep of an H1 swing low); shorts are mirrored by negating prices. Scratch study."""
import sys
import numpy as np, pandas as pd
import ict
from ict import CB, load_es, PT, TICK, K, Path, s1_arrays, tf_bars, secs, pool_above, first

WINDOW = 4            # the close back must come within this many M5 candles after the sweeping candle
VICINITY_ATR = 1.0    # stacked liquidity: another untested H1 swing within this many H1 ATR(14) beyond the sweep extreme
OTE_LO, OTE_HI = 0.62, 0.79
FVG_TFS = (5, 15, 60)
LVL_TFS = (5, 2)
COL = dict(ob="#ff9800", fvg="#2962ff", ote="#ff5cb8", pool="#ffffff", sweep="#f23645", entry="#00e676", lvl="#1b8a3c")


def fmt(u, sec=False):
    return pd.Timestamp(int(u), unit="s", tz="UTC").tz_convert(PT).strftime("%H:%M:%S" if sec else "%H:%M")


def sbars(m1, tf, sgn):
    B = tf_bars(m1, tf)
    o, c = sgn * B["open"].to_numpy(float), sgn * B["close"].to_numpy(float)
    h, l = (B["high"].to_numpy(float), B["low"].to_numpy(float)) if sgn > 0 else (-B["low"].to_numpy(float), -B["high"].to_numpy(float))
    return B, secs(B.index), o, h, l, c


def run(row, m1all, h1):
    t, bull = row.Index, bool(row.bullish)
    sgn = 1 if bull else -1
    lo_w, hi_w = ("low", "high") if bull else ("high", "low")          # words for the page
    swing = sgn * row.swing_price
    tu = int(t.timestamp())
    P = Path(*s1_arrays(t - pd.Timedelta(hours=2), t + pd.Timedelta(days=5)), sgn)
    m1 = m1all.loc[t - pd.Timedelta(days=3): t + pd.Timedelta(days=2)]
    B5, bt, o, h, l, c = sbars(m1, 5, sgn)
    B2, bt2, o2, h2, l2, c2 = sbars(m1, 2, sgn)
    hh = h1.loc[t - pd.Timedelta(days=150): t + pd.Timedelta(days=5)]
    h1hi = (hh["high"] if bull else -hh["low"]).to_numpy(float)
    h1lo = (hh["low"] if bull else -hh["high"]).to_numpy(float)
    h1st = secs(hh.index); h1end = h1st + 3600
    hb = h1.loc[:t].iloc[-16:-1]; pc = hb["close"].shift()
    atr = float(pd.concat([hb["high"] - hb["low"], (hb["high"] - pc).abs(), (hb["low"] - pc).abs()], axis=1).max(axis=1).iloc[1:].mean())
    V = VICINITY_ATR * atr
    out = dict(row=t, bull=bull, cat=None, log=[], draws=[], trade=None, trades=[], alt=None, cursor=tu + 4 * 3600)
    kept = []
    log = lambda u, s: out["log"].append((int(u), s))
    px = lambda p: f"{sgn * p:.2f}"

    def rect(id, t0, t1, p0, p1, col, label, alpha=0.15, la="tl"):
        out["draws"].append(dict(id="ict_" + id, type="rect", t0=int(t0), p0=float(sgn * p0), t1=int(t1), p1=float(sgn * p1), color=col, fill=col,
                                 alpha=alpha, width=1, dash="solid", label=label, la=la))

    def ray(id, t0, p, col, label, dash="solid"):
        out["draws"].append(dict(id="ict_" + id, type="ray", t=int(t0), p=float(sgn * p), color=col, width=1, dash=dash, label=label))

    k0, k1 = int(np.searchsorted(bt, tu)), int(np.searchsorted(bt, tu + 3600))
    gone = np.flatnonzero(l[k0:k1] < swing)
    assert len(gone)
    s = k0 + int(gone[0])
    log(bt[s], f"M5 candle {fmt(bt[s])} trades past the H1 swing {lo_w} {px(swing)}: the sweep starts")
    start_j, n_conf = s, 0
    while True:
        js = [j for j in range(start_j, min(s + WINDOW, len(c) - 1) + 1) if c[j] > swing]
        if not js:
            out["cat"] = "no setup: no M5 close back within 4 candles"
            log(bt[min(s + WINDOW, len(c) - 1)] + 300, f"no M5 candle from {fmt(bt[start_j])} to {fmt(bt[s + WINDOW])} closes back {'above' if bull else 'below'} {px(swing)}: no setup")
            return out
        j = js[0]; n_conf += 1
        ie = s + int(np.argmin(l[s:j + 1])); E = float(l[ie]); Tc = int(bt[j] + 300)
        i0 = P.at(Tc)
        log(Tc, f"M5 candle {fmt(bt[j])} closes back at {px(c[j])} ({j - s} candle{'s' if j - s != 1 else ''} after the sweeping one); sweep extreme {px(E)}")
        # ---- stacked liquidity beyond the extreme
        n = int(np.searchsorted(h1end, Tc, side="right"))
        lo_ = h1lo[:n]; piv = CB._pivots(lo_, K, True)
        mn_after = np.r_[np.minimum.accumulate(lo_[::-1])[::-1][1:], np.inf]
        stacked = [(int(h1st[p]), float(lo_[p])) for p in piv if lo_[p] < mn_after[p] and E - V <= lo_[p] < E]
        if stacked:
            out["cat"] = "skip: stacked H1 liquidity beyond the sweep"
            for q, (ts_, p_) in enumerate(stacked):
                ray(f"st{q}", ts_, p_, COL["pool"], f"untested H1 swing {lo_w} {px(p_)}: liquidity still resting", "dashed")
            ray("sweep", bt[ie], E, COL["sweep"], f"sweep extreme {px(E)}")
            log(Tc, f"untested H1 swing {lo_w}{'s' if len(stacked) > 1 else ''} within {V:.2f} pt (1 x H1 ATR) beyond the extreme: "
                    + ", ".join(px(p_) for _, p_ in stacked) + ": liquidity is stacked there, NO TRADE")
            out["cursor"] = Tc + 2 * 3600
            return out
        log(Tc, f"no other untested H1 swing {lo_w} within {V:.2f} pt (1 x H1 ATR) beyond {px(E)}: look for the entry")
        T1 = pool_above(h1hi, h1end, Tc, float(P.h[P.at(bt[s]):i0].max()))
        # ---- order blocks: the latest opposite-colour candle closed by now, not yet traded through
        obs = []
        for tf, tb, oo, hh_, ll_, cc in ((5, bt, o, h, l, c), (2, bt2, o2, h2, l2, c2)):
            last = int(np.searchsorted(tb, Tc - tf * 60, side="right")) - 1
            cand = [q for q in range(max(last - 30, 0), last + 1) if cc[q] < oo[q]]
            if not cand: continue
            q = cand[-1]; lvl = float(hh_[q] + TICK); a_ = P.at(tb[q] + tf * 60)
            through = i0 > a_ and P.h[a_:i0].max() >= lvl - 1e-9
            obs.append(dict(tf=tf, t=int(tb[q]), lo=float(ll_[q]), hi=float(hh_[q]), lvl=lvl, ok=not through))
            log(Tc, f"M{tf} OB = candle {fmt(tb[q])} ({px(ll_[q])} - {px(hh_[q])}); stop-entry {px(lvl)}: "
                    + ("order placed" if not through else "price has already traded through it, no stop-entry"))
        dead = P.le(i0, E - TICK)
        live = [x for x in obs if x["ok"]]
        fills = [(P.ge(i0, x["lvl"]), x) for x in live]
        fills = [(f, x) for f, x in fills if f is not None]
        f, ob = min(fills, key=lambda z: (z[0], z[1]["lvl"])) if fills else (None, None)
        # ---- the pullback entry (always worked out: the real entry when no stop-entry was possible, else shown as the alternative)
        pb = pullback(P, sgn, m1, bt, h, l, c, s, ie, E, Tc, i0, T1, dead, h1hi, h1st, h1end, px, lo_w, hi_w)
        first_dead = dead is not None and (not live or f is None or dead <= f) and (live or pb["f"] is None or dead <= pb["f"])
        if first_dead:
            m = int(np.searchsorted(bt, P.ts[dead], side="right")) - 1
            if m <= s + WINDOW:
                log(P.ts[dead], f"price makes a new extreme ({fmt(P.ts[dead], True)}) before any entry: the sweep goes on, wait for the next close back")
                start_j = max(m, j + 1) if m <= j else m
                out["draws"] = list(kept)
                continue
            out["cat"] = "no entry: new extreme before any entry (setup void)"
            log(P.ts[dead], f"price trades beyond the sweep extreme {px(E)} at {fmt(P.ts[dead], True)}, after the 4-candle window, before any entry: setup void")
            ray("sweep", bt[ie], E, COL["sweep"], f"sweep extreme {px(E)}")
            for x in obs: rect(f"ob{x['tf']}", x["t"], Tc + 3600, x["lo"], x["hi"], COL["ob"], f"M{x['tf']} OB", 0.15, "tl" if x["tf"] == 5 else "tr")
            out["cursor"] = int(P.ts[dead]) + 3600
            return out
        ray("sweep", bt[ie], E, COL["sweep"], f"sweep extreme {px(E)} (stop a tick beyond)")
        rect("cb", bt[j], bt[j] + 300, l[j], h[j], COL["entry"], "close back", 0.0)
        for x in obs:
            rect(f"ob{x['tf']}", x["t"], Tc + 2 * 3600, x["lo"], x["hi"], COL["ob"], f"M{x['tf']} OB" + ("" if x["ok"] else " (already traded through)"), 0.15,
                 "tl" if x["tf"] == 5 else "tr")
        if not live:
            for d_ in pb["draws"]: out["draws"].append(d_)

        def trade(i_f, entry, kind):
            tgt = pool_above(h1hi, h1end, P.ts[i_f], max(float(P.h[P.at(bt[s]):i_f + 1].max()), entry))
            if tgt is None: return None
            R, res, ix, ex = P.sim(i_f, entry, E - TICK, tgt)
            return dict(kind=kind, t_fill=int(P.ts[i_f]), entry=sgn * entry, stop=sgn * (E - TICK), target=sgn * tgt, risk=entry - E + TICK,
                        rr=(tgt - entry) / (entry - E + TICK), R=R, out=res, t_exit=int(P.ts[ix]), exit=sgn * ex)

        def show(tr, label):
            k_ = len(out["trades"])
            rect(f"entry{k_}", tr["t_fill"], tr["t_exit"], sgn * tr["entry"], sgn * tr["entry"], COL["entry"], f"{label} {tr['entry']:.2f} ({tr['R']:+.2f} R)", 0.0, "l")
            kept.append(out["draws"][-1])
            ray("target", tr["t_fill"], sgn * tr["target"], COL["pool"], f"target: H1 swing {hi_w} {tr['target']:.2f}", "dashed")
            out["trades"].append(tr)
            log(tr["t_exit"], f"exit: {tr['out']} at {tr['exit']:.2f}, {fmt(tr['t_exit'], True)}: {tr['R']:+.2f} R (planned {tr['rr']:.1f} R)")
            out["cursor"] = max(tr["t_exit"] + 1800, Tc + 2 * 3600)

        def again(tr):
            nonlocal start_j
            m = int(np.searchsorted(bt, tr["t_exit"], side="right")) - 1
            if tr["out"] != "stop" or m > s + WINDOW: return False
            log(tr["t_exit"], "stopped inside the 4-candle window: the sweep goes on, wait for the next close back")
            start_j = m; out["draws"] = list(kept)
            return True

        if live:
            if f is None:
                out["cat"] = "stop-entry placed, never triggered"; return out
            entry = max(ob["lvl"], float(P.o[f]))
            tr = trade(f, entry, f"stop-entry over the M{ob['tf']} OB")
            if tr is None:
                out["cat"] = "no trade: no H1 swing left as a target"; return out
            out["cat"] = f"stop-entry over the M{ob['tf']} OB as price displaces"
            log(P.ts[f], f"stop-entry over the M{ob['tf']} OB fills at {px(entry)}, {fmt(P.ts[f], True)}; stop {tr['stop']:.2f}, target = nearest untested H1 swing {hi_w} {tr['target']:.2f}")
            out["trade"] = tr; show(tr, "stop-entry")
            if again(tr): continue
            if pb["f"] is not None:
                out["alt"] = f"had the stop-entry been missed: pullback entry at {px(pb['price'])} ({pb['why']}), {fmt(P.ts[pb['f']], True)}"
                log(P.ts[pb["f"]], "(for review only) " + out["alt"])
            return out
        log(Tc, "no stop-entry possible: price has already displaced through every OB. Wait for a pullback to an M5 / M2 "
                + ("LHPB" if bull else "LLPB") + " level that is in the OTE zone or inside an old FVG of the left leg")
        for u_, s_ in pb["log"]: log(u_, s_)
        if pb["f"] is None:
            out["cat"] = "no entry: price displaced, " + pb["none"]
            out["cursor"] = Tc + 3 * 3600
            return out
        tr = trade(pb["f"], pb["price"], "pullback: " + pb["why"])
        if tr is None:
            out["cat"] = "no trade: no H1 swing left as a target"; return out
        out["cat"] = "pullback entry: " + pb["kind"]
        log(P.ts[pb["f"]], f"limit at {px(pb['price'])} fills {fmt(P.ts[pb['f']], True)} ({pb['why']}); stop {tr['stop']:.2f}, target = nearest untested H1 swing {hi_w} {tr['target']:.2f}")
        out["trade"] = tr; show(tr, "pullback entry")
        if again(tr): continue
        return out


def pullback(P, sgn, m1, bt, h, l, c, s, ie, E, Tc, i0, T1, dead, h1hi, h1st, h1end, px, lo_w, hi_w):
    """First touch, from the close back on, of an M5 / M2 LXPB level of the trade's side that the move away from the sweep broke out
    of, when that level is in the OTE zone of (sweep extreme -> the run's extreme so far) or inside an FVG of the left leg."""
    r = dict(f=None, log=[], draws=[], none="no qualifying pullback level touched")
    typ = "LHPB" if sgn > 0 else "LLPB"
    # the left leg: from the extreme M5 price since the last confirmed H1 swing on the other side, to the sweep extreme
    n = int(np.searchsorted(h1end, Tc, side="right"))
    piv = CB._pivots(h1hi[:n], K, False)
    a0 = int(np.searchsorted(bt, h1st[piv[-1]])) if len(piv) else max(ie - 288, 0)
    a0 = min(a0, ie)
    L0 = a0 + int(np.argmax(h[a0:ie + 1]))
    fvgs = []
    real = pd.DataFrame({"open": sgn * np.r_[0.0], "x": 0})        # placeholder to keep names local
    B = pd.DataFrame({"h": h[L0:ie + 1], "l": l[L0:ie + 1]}, index=pd.to_datetime(bt[L0:ie + 1], unit="s", utc=True))
    for tf in FVG_TFS:
        g = B if tf == 5 else B.resample(f"{tf}min").agg({"h": "max", "l": "min"}).dropna()
        gt = secs(g.index); gh, gl = g["h"].to_numpy(), g["l"].to_numpy()
        for q in range(2, len(g)):
            if gt[q] + tf * 60 > Tc: break
            if gh[q] < gl[q - 2] - 1e-9:                      # a gap left on the way to the sweep (signed: a down leg)
                fvgs.append(dict(tf=tf, t=int(gt[q - 2]), lo=float(gh[q]), hi=float(gl[q - 2])))
    i_t1 = P.ge(i0, T1) if T1 is not None else None
    end = min(x for x in (i_t1, dead, len(P.ts) - 1) if x is not None)
    iE = P.at(bt[ie])
    cands = []
    for tf in LVL_TFS:
        bars = m1 if tf == 1 else tf_bars(m1, tf)
        led = CB.ledger(bars)
        led = led[(led["type"] == typ) & led["breakout_time"].notna()]
        for lv in led.itertuples():
            price = sgn * float(lv.price); bo = int(lv.breakout_time.timestamp())
            if price <= E or bo < bt[s]: continue
            a_ = P.at(bo + tf * 60)
            tch = P.le(a_, price)
            if tch is None or P.ts[tch] < Tc or tch > end: continue
            H = float(P.h[iE:tch + 1].max())
            in_ote = H - OTE_HI * (H - E) - 1e-9 <= price <= H - OTE_LO * (H - E) + 1e-9
            inside = [g for g in fvgs if g["lo"] - 1e-9 <= price <= g["hi"] + 1e-9]
            cands.append(dict(f=tch, price=price, tf=tf, H=H, ote=in_ote, fvg=inside, p0=int(lv.formation_time.timestamp()), bo=bo))
    cands.sort(key=lambda z: (z["f"], -z["price"]))
    for z in cands:
        nm = f"M{z['tf']} {typ} {px(z['price'])}"
        if not (z["ote"] or z["fvg"]):
            r["log"].append((int(P.ts[z["f"]]), f"{nm} touched {ict_fmt(P.ts[z['f']])}: not in the OTE zone, not in an old FVG: passed over"))
            continue
        why = []
        if z["fvg"]:
            g = z["fvg"][0]
            why.append(f"inside the left leg's M{g['tf']} FVG {min(sgn * g['lo'], sgn * g['hi']):.2f} - {max(sgn * g['lo'], sgn * g['hi']):.2f}")
        if z["ote"]: why.append(f"in the OTE zone of {px(E)} -> {px(z['H'])}")
        r.update(f=z["f"], price=z["price"], why=f"{nm}, " + " and ".join(why),
                 kind=("old FVG + LXPB level" if z["fvg"] else "LXPB level in OTE") + (" (also in OTE)" if z["fvg"] and z["ote"] else ""))
        D = lambda **k: r["draws"].append(k)
        tend = int(P.ts[z["f"]]) + 1800
        for q, g in enumerate(z["fvg"][:1]):
            D(id=f"ict_fvg{q}", type="rect", t0=g["t"], p0=sgn * g["lo"], t1=tend, p1=sgn * g["hi"], color=COL["fvg"], fill=COL["fvg"], alpha=0.18,
              width=1, dash="solid", label=f"left leg M{g['tf']} FVG", la="tl")
        H = z["H"]
        D(id="ict_ote", type="rect", t0=Tc, p0=sgn * (H - OTE_LO * (H - E)), t1=tend, p1=sgn * (H - OTE_HI * (H - E)), color=COL["ote"], fill=COL["ote"],
          alpha=0.10, width=1, dash="solid", label=f"OTE 62-79% of {px(E)} -> {px(H)}", la="br")
        D(id="ict_lvl", type="rect", t0=z["p0"], p0=sgn * z["price"], t1=tend, p1=sgn * z["price"], color=COL["lvl"], fill=COL["lvl"], alpha=0.0,
          width=2, dash="solid", label=nm, la="l")
        break
    if r["f"] is None:
        if i_t1 is not None and end == i_t1:
            r["none"] = "first H1 swing reached with no qualifying pullback"
            r["log"].append((int(P.ts[i_t1]), f"price reaches the first H1 swing {hi_w} {px(T1)} at {ict_fmt(P.ts[i_t1])} without touching a qualifying level: no entry"))
        elif dead is not None and end == dead:
            r["none"] = "then a new extreme, no qualifying pullback before it"
            r["log"].append((int(P.ts[dead]), f"price trades beyond the sweep extreme at {ict_fmt(P.ts[dead])} without touching a qualifying level: no entry"))
    return r


def ict_fmt(u):
    return fmt(u, True)


def load(since="2026-01-01"):
    h1f = load_es("1h", tz=PT); h1 = h1f[["open", "high", "low", "close"]]
    rows = CB.collect(h1f)
    rows = rows.loc[rows.index >= pd.Timestamp(since, tz=PT)].sort_index()
    m1 = load_es("1m", start=rows.index[0] - pd.Timedelta(days=6), tz=PT)[["open", "high", "low", "close"]]
    return rows, m1, h1


if __name__ == "__main__":
    import pickle
    rows, m1, h1 = load()
    res = []
    for n, row in enumerate(rows.itertuples()):
        r = run(row, m1, h1); r["n"] = n + 1
        res.append(r)
        print(n + 1, f"{row.Index:%m-%d %H:%M}", "bull" if row.bullish else "bear", "|", r["cat"], "|",
              "; ".join(f"{tr['out']} {tr['R']:+.2f}R rr {tr['rr']:.1f}" for tr in r["trades"]), flush=True)
    pickle.dump(res, open("algo.pkl", "wb"))
    df = pd.DataFrame([dict(cat=tr["kind"].split(":")[0], R=tr["R"], win=tr["out"] == "target") for r in res for tr in r["trades"]]
                      + [dict(cat=r["cat"].split(":")[0], R=np.nan, win=np.nan) for r in res if not r["trades"]])
    print(df.groupby("cat").agg(n=("R", "size"), trades=("R", "count"), win=("win", "mean"), avgR=("R", "mean"), totR=("R", "sum")).round(2).to_string())
