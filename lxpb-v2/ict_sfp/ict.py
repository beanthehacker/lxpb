"""ICT structures on the cross bar rows + strategy tests (scratch study, 2026-11-10). All logic is written for a LONG
(bullish cross bar); bearish rows are mirrored by negating prices."""
import os, sys
import numpy as np, pandas as pd
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "cross_bar"))
import render_cross_bar_report as CB
from es_data import load_es
PT, TICK, K = CB.PT, 0.25, CB.SWING_K
DISP_ATR, DISP_BODY = 1.5, 0.5        # displacement candle: range >= 1.5 x ATR(14), body >= half its range, leaves an FVG
HORIZON = pd.Timedelta(days=5)
NEAR_ATR = 0.25                       # "near" the pool: within 0.25 x H1 ATR(14)
OTE = (0.62, 0.705, 0.79)
LATEST = 3600                         # the displacement / candidate order block must start within an hour of the cross bar's close


def secs(idx):
    return idx.as_unit("s").asi8


_S1 = {}
def s1_arrays(a, b):
    utc = lambda x: x.tz_convert("UTC").tz_localize(None)
    out = []
    for m in pd.period_range(utc(a).to_period("M"), utc(b).to_period("M"), freq="M"):
        if m not in _S1:
            if len(_S1) > 2: _S1.pop(next(iter(_S1)))
            try:
                d = load_es("1s", start=m.start_time.tz_localize("UTC"), end=(m + 1).start_time.tz_localize("UTC"))
                _S1[m] = (secs(d.index), d["open"].to_numpy(float), d["high"].to_numpy(float), d["low"].to_numpy(float), d["close"].to_numpy(float))
            except ValueError:
                _S1[m] = tuple(np.array([], dtype=t) for t in (np.int64, float, float, float, float))
        out.append(_S1[m])
    ts = np.concatenate([x[0] for x in out])
    i0, i1 = np.searchsorted(ts, [a.timestamp(), b.timestamp()])
    return tuple(np.concatenate([x[j] for x in out])[i0:i1] for j in range(5))


def first(mask):
    k = int(np.argmax(mask)) if len(mask) else 0
    return k if len(mask) and mask[k] else None


class Path:
    """1 s path of a row in signed space."""
    def __init__(self, ts, o, h, l, c, sgn):
        self.ts = ts
        self.o, self.c = sgn * o, sgn * c
        self.h, self.l = (h, l) if sgn > 0 else (-l, -h)
    def at(self, t):                                   # first second at or after t (unix s)
        return int(np.searchsorted(self.ts, t))
    def ge(self, i0, level, i1=None):
        k = first(self.h[i0:i1] >= level - 1e-9); return None if k is None else i0 + k
    def le(self, i0, level, i1=None):
        k = first(self.l[i0:i1] <= level + 1e-9); return None if k is None else i0 + k
    def sim(self, i_f, entry, stop, target, d=1):
        """(R, outcome, exit index, exit price). Stop first when both in one second; a second opening through the stop fills at its open."""
        risk = abs(entry - stop)
        if d > 0: s, g = self.le(i_f, stop), self.ge(i_f + 1, target)
        else:     s, g = self.ge(i_f, stop), self.le(i_f + 1, target)
        if s is None and g is None:
            return d * (self.c[-1] - entry) / risk, "open", len(self.c) - 1, self.c[-1]
        if g is None or (s is not None and s <= g):
            px = stop if s == i_f else (min(stop, self.o[s]) if d > 0 else max(stop, self.o[s]))
            return d * (px - entry) / risk, "stop", s, px
        return d * (target - entry) / risk, "target", g, target


def tf_bars(m1, minutes):
    if minutes == 1: return m1
    return m1.resample(f"{minutes}min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def pool_above(h1s_hi, h1_end, T, ref):
    """Nearest unswept H1 swing high (K-bar fractal, confirmed before T) above ref. h1s_hi: signed highs of H1 bars, h1_end: their close times (unix s)."""
    n = int(np.searchsorted(h1_end, T, side="right"))           # bars closed by T
    hi = h1s_hi[:n]
    piv = CB._pivots(hi, K, False)
    if not len(piv): return None
    after = np.maximum.accumulate(hi[::-1])[::-1]               # max from i on
    nxt = np.r_[after[1:], -np.inf]
    ok = piv[(hi[piv] > nxt[piv]) & (hi[piv] > ref + 1e-9)]
    return float(hi[ok].min()) if len(ok) else None


def setups(o, h, l, c, atr, ks, kmax):
    """Every displacement FVG with the displacement candle at or after bar ks (first bar past the swept swing). Signed space."""
    out = []
    for i in range(max(ks + 1, 2), len(o)):
        a, b = i - 2, i - 1
        if b > kmax: break
        if b < ks or not (h[a] < l[i] - 1e-9 and c[b] > o[b]): continue
        rng = h[b] - l[b]
        if not (np.isfinite(atr[b - 1]) and rng >= DISP_ATR * atr[b - 1] and (c[b] - o[b]) >= DISP_BODY * rng): continue
        e_bar = ks + int(np.argmin(l[ks:i + 1])); E = l[e_bar]
        j = b
        while j - 1 >= 0 and c[j - 1] > o[j - 1]: j -= 1          # the run of up-close candles ending in the displacement candle
        ob = j - 1 if j >= 1 else None                            # order block: the last down-close candle before that run
        if ob is None: continue
        # market structure shift: the displacement leg closes above the last confirmed 2-bar fractal high before the sweep low
        ph = CB._pivots(h[:e_bar + 1], 2, False)
        mss = bool(len(ph) and max(c[b], c[i]) > h[ph[-1]])
        out.append(dict(a=a, b=b, c=i, E=E, e_bar=e_bar, ob=ob, ob_lo=l[ob], ob_hi=h[ob], fvg_lo=h[a], fvg_hi=l[i],
                        leg_hi=float(h[e_bar:i + 1].max()), b_lo=l[b], b_hi=h[b], mss=mss))
    return out


def run_row(row, m1all, h1, tfs=(5, 2, 1), marks=None):
    t, bull = row.Index, bool(row.bullish)
    sgn = 1 if bull else -1
    swing = sgn * row.swing_price
    tu, close_u = t.timestamp(), t.timestamp() + 3600
    P = Path(*s1_arrays(t, t + HORIZON), sgn)
    if len(P.ts) < 3600: return []
    hh = h1.loc[t - pd.Timedelta(days=150): t + HORIZON]
    h1hi = (hh["high"] if bull else -hh["low"]).to_numpy(float)
    h1end = secs(hh.index) + 3600
    hb = h1.loc[:t].iloc[-16:-1]; pc = hb["close"].shift()
    atr_h1 = float(pd.concat([hb["high"] - hb["low"], (hb["high"] - pc).abs(), (hb["low"] - pc).abs()], axis=1).max(axis=1).iloc[1:].mean())
    near = NEAR_ATR * atr_h1
    m1 = m1all.loc[t - pd.Timedelta(days=4): t + HORIZON]
    res = []

    def rec(strat, tf, mode, i_f, entry, stop, target, d=1, **kw):
        if entry is None or abs(entry - stop) < TICK - 1e-9: return
        risk = abs(entry - stop)
        R, out, ix, px = P.sim(i_f, entry, stop, target, d)
        r1 = P.sim(i_f, entry, stop, entry + d * risk, d)[0]; r2 = P.sim(i_f, entry, stop, entry + d * 2 * risk, d)[0]
        res.append(dict(row=t, bull=bull, strat=strat, tf=tf, mode=mode, side="long" if d * sgn > 0 else "short",
                        t_fill=int(P.ts[i_f]), entry=sgn * entry, stop=sgn * stop, target=sgn * target, risk=risk,
                        rr=abs(target - entry) / risk, R=R, out=out, t_exit=int(P.ts[ix]), exit=sgn * px, R1=r1, R2=r2, **kw))

    # reference: market at the H1 close, stop a tick beyond the cross bar, target the pool
    ic = P.at(close_u)
    if 0 < ic < len(P.ts):
        pl = pool_above(h1hi, h1end, close_u, float(P.h[:ic].max()))
        if pl is not None: rec("REF market at H1 close", 0, "closed", ic, P.o[ic], float(P.l[:ic].min()) - TICK, pl)
    for tf in tfs:
        B = tf_bars(m1, tf)
        bt = secs(B.index)
        o, c = sgn * B["open"].to_numpy(float), sgn * B["close"].to_numpy(float)
        h, l = (B["high"].to_numpy(float), B["low"].to_numpy(float)) if bull else (-B["low"].to_numpy(float), -B["high"].to_numpy(float))
        pcl = np.r_[np.nan, c[:-1]]
        tr = np.fmax(h - l, np.fmax(np.abs(h - pcl), np.abs(l - pcl)))
        atr = pd.Series(tr).rolling(14).mean().to_numpy()
        k0 = int(np.searchsorted(bt, tu))
        k1 = int(np.searchsorted(bt, close_u))
        gone = np.flatnonzero(l[k0:k1] < swing)
        if not len(gone): continue
        ks = k0 + int(gone[0]); S = tf * 60
        kmax = int(np.searchsorted(bt, close_u + LATEST)) - 1      # last candle that may be the displacement / the order block
        ss = setups(o, h, l, c, atr, ks, kmax)
        cand = [j for j in range(ks, kmax + 1) if c[j] < o[j]]
        for mode in ("live", "closed"):
            # ---------- A: stop-entry over the candidate order block (latest down-close candle since the sweep began)
            for n, j in enumerate(cand):
                act = bt[j] + S; end = bt[cand[n + 1]] + S if n + 1 < len(cand) else P.ts[-1] + 1
                if mode == "closed":
                    if end <= close_u: continue
                    if act < close_u:
                        if P.ge(P.at(act), h[j] + TICK, P.at(close_u)) is not None: continue     # already triggered before the close
                        act = close_u
                i0, i1 = P.at(act), P.at(end)
                if i0 >= len(P.ts): break
                f = P.ge(i0, h[j] + TICK, i1)
                if f is None: continue
                entry = max(h[j] + TICK, P.o[f]); stop = l[j] - TICK
                pl = pool_above(h1hi, h1end, P.ts[f], max(float(P.h[:f + 1].max()), entry))
                if pl is not None:
                    jf = int(np.searchsorted(bt, P.ts[f], side="right")) - 1
                    disp = any(s["b"] in (jf, jf + 1) for s in ss)
                    rec("A stop-entry over OB", tf, mode, f, entry, stop, pl, disp=disp, ob_t=int(bt[j]))
                break
            # ---------- the displacement setup
            st = None
            for s in ss:
                Tc = bt[s["c"]] + S
                if mode == "live" or Tc >= close_u: st = s; break
                i_a, i_b = P.at(Tc), P.at(close_u)
                if i_b > i_a and P.l[i_a:i_b].min() > s["E"] - 1e-9: st = s; break      # still intact at the close
            if st is None: continue
            Tc = bt[st["c"]] + S
            T0 = Tc if mode == "live" else max(Tc, close_u)
            i0 = P.at(T0)
            if i0 >= len(P.ts) - 1 or i0 == 0: continue
            ref = max(float(P.h[:i0].max()), st["leg_hi"])
            pl = pool_above(h1hi, h1end, T0, ref)
            if marks is not None and tf == marks["tf"] and mode == "live":
                marks["out"].append(dict(row=t, sgn=sgn, S=S, t_a=int(bt[st["a"]]), t_b=int(bt[st["b"]]), t_c=int(bt[st["c"]]), t_ob=int(bt[st["ob"]]),
                                         t_e=int(bt[st["e_bar"]]), pool=None if pl is None else sgn * pl,
                                         **{k: sgn * st[k] for k in ("E", "ob_lo", "ob_hi", "fvg_lo", "fvg_hi", "leg_hi", "b_lo", "b_hi")}, mss=st["mss"]))
            if pl is None: continue
            E = st["E"]; i_pool = P.ge(i0, pl); i_dead = P.le(i0, E - TICK)
            lim_end = min(x for x in (i_pool, i_dead, len(P.ts) - 1) if x is not None)
            kw = dict(mss=st["mss"])

            def limit(name, level, stop):
                ia = P.at(Tc)
                if i0 > ia and P.l[ia:i0].min() <= level + 1e-9: return          # already traded there before the order
                f = P.le(i0, level, lim_end + 1)
                if f is not None and (i_pool is None or f < i_pool): rec(name, tf, mode, f, level, stop, pl, **kw)
            limit("C1 limit FVG edge", st["fvg_hi"], E - TICK)
            limit("C2 limit FVG mid", (st["fvg_hi"] + st["fvg_lo"]) / 2, E - TICK)
            limit("C3 limit OB top, stop under OB", st["ob_hi"], st["ob_lo"] - TICK)
            limit("C4 limit OB top, stop under sweep", st["ob_hi"], E - TICK)
            # OTE: a limit trailing under the leg high at a retracement of sweep low -> leg high
            if lim_end > i0:
                run = np.maximum.accumulate(np.r_[ref, P.h[i0:lim_end]])        # leg high known before each second
                for nm, fr in (("C5 limit OTE 62%", OTE[0]), ("C6 limit OTE 70.5%", OTE[1])):
                    lv = np.floor((run - fr * (run - E)) / TICK) * TICK
                    k = first(P.l[i0:i0 + len(lv)] <= lv + 1e-9)
                    if k is not None and (i_pool is None or i0 + k < i_pool) and lv[k] > E:
                        rec(nm, tf, mode, i0 + k, float(lv[k]), E - TICK, pl, ote_hi=sgn * float(run[k]), **kw)
            # ---------- B: price gets to / near the pool first
            i_near = P.ge(i0, pl - near)
            if i_near is None or (i_dead is not None and i_dead < i_near): continue
            q = int(np.searchsorted(bt, P.ts[i_near], side="right")) - 1
            # B1: failure candle at the pool (down-close, closes back under the pool) within 6 candles: short to the displacement area
            for r in range(q, min(q + 6, len(o))):
                if c[r] < pl and c[r] < o[r]:
                    f = P.at(bt[r] + S)
                    if f < len(P.ts):
                        top = float(h[q:r + 1].max()); entry = P.o[f]
                        if entry > st["fvg_hi"] + TICK and top + TICK > entry:
                            rec("B1 short pool failure -> FVG", tf, mode, f, entry, top + TICK, st["fvg_hi"], d=-1, **kw)
                    break
            # B2 / B3: back into the displacement area after the pool: long again, target the high the run made
            for name, level, stop in (("B2 long FVG edge after pool -> run high", st["fvg_hi"], E - TICK),
                                      ("B3 long OB top after pool -> run high", st["ob_hi"], st["ob_lo"] - TICK)):
                f = P.le(i_near, level)
                if f is None or (i_dead is not None and i_dead < f): continue
                top = float(P.h[i0:f + 1].max())
                if top > level + TICK: rec(name, tf, mode, f, level, stop, top, **kw)
    return res


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
    out = sys.argv[2] if len(sys.argv) > 2 else "trades.parquet"
    h1f = load_es("1h", tz=PT)
    h1 = h1f[["open", "high", "low", "close"]]
    rows = CB.collect(h1f)
    rows = rows.loc[rows.index >= pd.Timestamp(since, tz=PT)].sort_index()
    if len(sys.argv) > 3: rows = rows.loc[rows.index < pd.Timestamp(sys.argv[3], tz=PT)]
    m1 = load_es("1m", start=rows.index[0] - pd.Timedelta(days=6), tz=PT)[["open", "high", "low", "close"]]
    allr, marks = [], dict(tf=5, out=[])
    for n, row in enumerate(rows.itertuples()):
        allr += run_row(row, m1, h1, marks=marks)
        if n % 20 == 0: print(n, row.Index, len(allr), flush=True)
    pd.DataFrame(allr).to_parquet(out); pd.DataFrame(marks["out"]).to_parquet(out.replace(".parquet", "_marks.parquet"))
    print("rows", len(rows), "trades", len(allr))


if __name__ == "__main__":
    main()
