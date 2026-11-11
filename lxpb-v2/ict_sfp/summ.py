import sys
import numpy as np, pandas as pd
pd.set_option("display.width", 260); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 400)
COST = 0.5      # points per round trip (1 tick each side)
rng = np.random.default_rng(7)


def ci(x):
    if len(x) < 5: return (np.nan, np.nan)
    b = rng.choice(x, (4000, len(x))).mean(axis=1)
    return tuple(np.percentile(b, [2.5, 97.5]))


def table(t, by=("strat", "mode", "tf")):
    rows = []
    for key, g in t.groupby(list(by), sort=True):
        x = g["R"].to_numpy(); net = x - COST / g["risk"].to_numpy()
        lo, hi = ci(net)
        rows.append(dict(zip(by, key), n=len(g), win=round(100 * (g["out"] == "target").mean()), open=int((g["out"] == "open").sum()),
                         risk_pt=round(g["risk"].median(), 2), rr=round(g["rr"].median(), 1), avgR=round(x.mean(), 2), totR=round(x.sum(), 1),
                         netAvg=round(net.mean(), 2), netTot=round(net.sum(), 1), ci_lo=round(lo, 2), ci_hi=round(hi, 2),
                         R1=round((g["R1"] - COST / g["risk"]).mean(), 2), R2=round((g["R2"] - COST / g["risk"]).mean(), 2)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    t = pd.concat([pd.read_parquet(f) for f in sys.argv[1:]])
    print("rows:", t["row"].nunique())
    print(table(t).to_string(index=False))
