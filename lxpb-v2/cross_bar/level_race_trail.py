"""Each M5 entry level on its own (no seat), as in the report's "stop or 1R first" race, but with a fixed 1R target AND the
thrust-P1 stop trail (no RR floor, no end-of-day flat). Same fill, same initial stop. Prints the results for the levels the
plain race decided, and how many cross bars (groups of levels) have at least one winner.

Usage: python cross_bar/level_race_trail.py <out.csv>"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import render_cross_bar_trades as T

PT = T.PT
h1 = T.load_es("1h", tz=PT)
rows = T.CB.collect(h1)
rows = rows.loc[rows.index >= pd.Timestamp("2026-01-01", tz=PT)]
m5 = T.load_es("5min", start=rows.index.min() - pd.Timedelta(days=30), tz=PT)
led = T.CB.ledger(m5)


def managed(lv, row, rc):
    bull = row.bullish
    sign = 1.0 if bull else -1.0
    ft, fp, risk, stop0, tgt = rc["ft"], rc["fp"], rc["risk"], rc["stop"], rc["tgt"]
    if risk <= 0:
        return dict(res="stop", r=-1.0, trails=0)
    evs = T.trail_events(led, m5, "LHPB" if bull else "LLPB", ft, ft + pd.Timedelta(days=60))
    sched = [(ft, stop0)]
    for trig, new, _ in evs:
        if sign * (new - sched[-1][1]) > 0:
            sched.append((trig, new))
    trig_ns = np.array([t.value for t, _ in sched])
    stops = np.array([s for _, s in sched])
    a = ft
    while True:
        b = a + pd.Timedelta(days=3)
        s = T.s1_between(a, b)
        if s.empty:
            return dict(res="open", r=None, trails=len(sched) - 1)
        lo, hi, op = s["low"].to_numpy(float), s["high"].to_numpy(float), s["open"].to_numpy(float)
        cur = stops[np.searchsorted(trig_ns, s.index.asi8, side="right") - 1]
        st = np.flatnonzero(lo <= cur if bull else hi >= cur)
        tg = np.flatnonzero(hi >= tgt if bull else lo <= tgt)
        i_st = st[0] if len(st) else None
        i_tg = tg[0] if len(tg) else None
        if i_st is not None and (i_tg is None or i_st <= i_tg):
            k = int(i_st)
            px = cur[k] if (s.index[k] == ft) else (min(cur[k], op[k]) if bull else max(cur[k], op[k]))
            n_tr = int(np.searchsorted(trig_ns, s.index[k].value, side="right") - 1)
            return dict(res="trailed stop" if n_tr else "stop", r=sign * (px - fp) / risk, trails=n_tr)
        if i_tg is not None:
            k = int(i_tg)
            return dict(res="1R target", r=1.0, trails=int(np.searchsorted(trig_ns, s.index[k].value, side="right") - 1))
        a = b


out = []
for row in rows.itertuples():
    cands = T.candidates(led, row)
    for j, (_, lv) in enumerate(cands.iterrows()):
        rc = T.race(lv, row)
        if rc["res"] not in ("1R first", "stop first"):
            continue
        m = managed(lv, row, rc)
        out.append(dict(t=row.Index, side="long" if row.bullish else "short", nearest=j == 0, plain=rc["res"],
                        plain_r=1.0 if rc["res"] == "1R first" else -1.0, **m))
df = pd.DataFrame(out)
df.to_csv(sys.argv[1] if len(sys.argv) > 1 else "level_race_trail.csv", index=False)


def line(name, d):
    d = d[d["r"].notna()]
    print(f"{name:22s} n={len(d):3d}  win={(d.r > 0).mean():.0%}  avg={d.r.mean():+.2f}  total={d.r.sum():+.1f}R   "
          f"(plain 1R race: win={(d.plain_r > 0).mean():.0%} total={d.plain_r.sum():+.1f}R)")


line("all", df)
line("nearest", df[df.nearest])
line("earlier", df[~df.nearest])
line("long", df[df.side == "long"])
line("short", df[df.side == "short"])
print(df["res"].value_counts().to_string())
print(pd.crosstab(df["plain"], df["res"]).to_string())
tr = df[df.res == "trailed stop"]
print(f"trailed stops: {len(tr)}, R: >0 {int((tr.r > 0).sum())}, ==0 {int((tr.r == 0).sum())}, <0 {int((tr.r < 0).sum())}, avg {tr.r.mean():+.2f}")


for col, name in (("plain_r", "plain stop-or-1R race"), ("r", "1R target + trail")):
    g = df.groupby("t")[col].apply(lambda x: (x > 0).any())
    print(f"{name}: cross bars with decided levels {len(g)}, >= 1 winner {int(g.sum())}, all losers {int((~g).sum())}")
