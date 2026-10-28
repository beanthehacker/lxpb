"""findings.html: the swing study's tables, computed from features.csv (so the page and the numbers agree)."""
import os
import numpy as np
import pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(D, "features.csv"))
df["first_half"] = pd.to_datetime(df.touch_pt) < "2026-06-01"
sw = df[df.swing]


def cells(x):
    x = x.dropna()
    if not len(x):
        return "<td>0</td><td></td><td></td>"
    return f"<td>{len(x)}</td><td>{np.mean(x > 0):.0%}</td><td>{x.sum():+.1f}</td>"


def table(title, groups):
    h = (f"<table><tr><th>{title}</th><th>trades</th><th>win</th><th>total R</th>"
         "<th>trades</th><th>win</th><th>total R</th><th>Jan-May total R</th><th>Jun-Oct total R</th></tr>"
         "<tr><th></th><th colspan=3>range-edge target</th><th colspan=3>opposite-zz target</th><th colspan=2>opposite-zz</th></tr>")
    for name, m in groups:
        g = sw[m]
        h += (f"<tr><td>{name}</td>{cells(g.r_re)}{cells(g.r_oz)}"
              f"<td>{g[g.first_half].r_oz.sum():+.1f}</td><td>{g[~g.first_half].r_oz.sum():+.1f}</td></tr>")
    return h + "</table>"


ns = df[~df.swing]
html = f"""<div class="findings">
<b>Swing-P0 entries: what wins and what loses</b> ({len(sw)} of the year report's 127 fade trades, Jan 2 - Oct 26 2026).
Swing entries beat the rest by a wide margin: opposite-zz {np.mean(sw.r_oz.dropna() > 0):.0%} win, {sw.r_oz.sum():+.1f}R
(non-swing {np.mean(ns.r_oz.dropna() > 0):.0%}, {ns.r_oz.sum():+.1f}R); range edge {np.mean(sw.r_re > 0):.0%},
{sw.r_re.sum():+.1f}R (non-swing {np.mean(ns.r_re > 0):.0%}, {ns.r_re.sum():+.1f}R).
{table("Strongest splits", [
    ("all swing entries", sw.swing),
    ("few same-side fractals near the level (&le;6 within 2 pt, 24 h)", sw.frac_same_2pt <= 6),
    ("many fractals (&gt;6)", sw.frac_same_2pt > 6),
    ("small P0 candle (&le;1.1 ATR)", sw.p0_range_atr <= 1.1),
    ("big P0 candle (&gt;1.1 ATR)", sw.p0_range_atr > 1.1),
    ("shallow pullback before P1 (&le;1.45 ATR)", sw.depth_right_atr <= 1.45),
    ("deep pullback (&gt;1.45 ATR)", sw.depth_right_atr > 1.45),
    ("few fractals AND small P0", (sw.frac_same_2pt <= 6) & (sw.p0_range_atr <= 1.1)),
    ("neither", (sw.frac_same_2pt > 6) & (sw.p0_range_atr > 1.1)),
    ("touch after 05:30 PT", sw.hour_pt > 5.5),
    ("touch before 05:30 PT", sw.hour_pt <= 5.5),
])}
<p>What did NOT matter: how many bars the level stayed the extreme on either side (median 2 and 2, winners = losers), the
swing's depth before the P0, the efficiency ratio at breakout, the P0's rejection wick, how fast the breakout came.
<b>Caveat:</b> 43 trades and about 25 features were tried, so some of these splits are luck. Every edge above comes from
Jan-May; in Jun-Oct (17 trades) both sides of every split did about equally well. Treat them as hypotheses to test on
2025 or on later 2026 data, not as filters to trade.</p>
<p>The <b>Swing</b> column shows each trade's fractals (Fr), P0 candle (P0) and pullback (PB); green = the side that won
in Jan-May. Hover it for every feature. The <b>Swing features</b> filters below recompute the stats live.</p>
</div>
"""
open(os.path.join(D, "findings.html"), "w", encoding="utf-8").write(html)
print(html)
