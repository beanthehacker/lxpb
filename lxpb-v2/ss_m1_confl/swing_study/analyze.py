import os
import numpy as np
import pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(D, "features.csv"))
sw = df[df.swing].copy()
FEATS = ["left_bars", "right_bars", "depth_left_atr", "depth_right_atr", "depth_left_pts", "depth_right_pts", "er",
         "frac_same_1pt", "frac_same_2pt", "frac_opp_2pt", "pre_tests", "p0_range_atr", "wick", "p1_range_atr", "p1_body",
         "p1_close_beyond", "form_to_break_min", "break_to_touch_min", "ext_after_p1_atr", "return_min", "return_speed",
         "atr", "hour_pt", "cluster", "stop_pts", "range_edge_dist", "oz_dist"]


def stats(x):
    x = x.dropna()
    return f"n={len(x):2d} win {np.mean(x > 0):4.0%} avgR {x.mean():+.2f} totR {x.sum():+5.1f}" if len(x) else "n= 0"


print(f"swing trades: {len(sw)} | opposite-zz: {stats(sw.r_oz)} | range-edge: {stats(sw.r_re)}")
print(f"non-swing:    {len(df) - len(sw)} | opposite-zz: {stats(df[~df.swing].r_oz)} | range-edge: {stats(df[~df.swing].r_re)}\n")
res = []
for f in FEATS:
    v = sw[f]
    med = v.median()
    lo, hi = sw[v <= med], sw[v > med]
    if len(hi) < 5:                                      # ties at the median: split at the next value instead
        lo, hi = sw[v < med], sw[v >= med]
    rho_oz = sw[[f, "r_oz"]].dropna().corr("spearman").iloc[0, 1]
    rho_re = sw[[f, "r_re"]].dropna().corr("spearman").iloc[0, 1]
    win_m = sw.loc[sw.r_re > 0, f].median(); los_m = sw.loc[sw.r_re <= 0, f].median()
    res.append((f, med, win_m, los_m, rho_re, rho_oz, stats(lo.r_re), stats(hi.r_re), stats(lo.r_oz), stats(hi.r_oz)))
    print(f"{f:20s} median {med:7.2f} | winners {win_m:7.2f} losers {los_m:7.2f} (range-edge) | rho re {rho_re:+.2f} oz {rho_oz:+.2f}")
    print(f"   <= median  RE {stats(lo.r_re)}   OZ {stats(lo.r_oz)}")
    print(f"   >  median  RE {stats(hi.r_re)}   OZ {stats(hi.r_oz)}")
print()
for f in ("type", "rth", "spike"):
    for k, g in sw.groupby(f):
        print(f"{f}={k}: RE {stats(g.r_re)}  OZ {stats(g.r_oz)}")
