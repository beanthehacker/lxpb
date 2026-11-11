"""Every H1 bar that trades past a confirmed, untested swing (find_sfp's reference), whether or not it closes back: the
population the cross bars are picked from once the hour has closed."""
import sys
import numpy as np, pandas as pd
import ict
from ict import CB, load_es, PT, K

def all_sweeps(h1):
    out = []
    for is_low in (True, False):
        ext = h1["low" if is_low else "high"].to_numpy(float)
        conf = {int(p) + K: int(p) for p in CB._pivots(ext, K, is_low)}
        live = []
        for i in range(len(ext)):
            if live and ((ext[i] < ext[live[-1]]) if is_low else (ext[i] > ext[live[-1]])):
                out.append((h1.index[i], ext[live[-1]], is_low))
            while live and ((ext[i] <= ext[live[-1]]) if is_low else (ext[i] >= ext[live[-1]])): live.pop()
            if i in conf: live.append(conf[i])
    return pd.DataFrame(out, columns=["ts", "swing_price", "bullish"]).set_index("ts").sort_index()

if __name__ == "__main__":
    since, out, until = sys.argv[1], sys.argv[2], sys.argv[3]
    h1f = load_es("1h", tz=PT); h1 = h1f[["open", "high", "low", "close"]]
    rows = all_sweeps(h1)
    cb = CB.collect(h1f)
    rows["cross"] = [(t, b) in set(zip(cb.index, cb.bullish)) for t, b in zip(rows.index, rows.bullish)]
    rows = rows.loc[(rows.index >= pd.Timestamp(since, tz=PT)) & (rows.index < pd.Timestamp(until, tz=PT))]
    print(len(rows), "sweeps,", int(rows.cross.sum()), "cross bars")
    m1 = load_es("1m", start=rows.index[0] - pd.Timedelta(days=6), tz=PT)[["open", "high", "low", "close"]]
    allr = []
    for n, row in enumerate(rows.itertuples()):
        r = ict.run_row(row, m1, h1)
        for x in r: x["cross"] = row.cross
        allr += [x for x in r if x["mode"] == "live"]
        if n % 100 == 0: print(n, row.Index, len(allr), flush=True)
    pd.DataFrame(allr).to_parquet(out)
