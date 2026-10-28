"""Per-trade features of the entry level, from the M1 bars and ledger the year report used (point in time: nothing
after the touch is read except the outcome). LHPB = the P0 candle's HIGH (a swing high for a swing-P0), LLPB = its LOW."""
import json, os
import numpy as np
import pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(D, "rows.json")))
m1 = pd.read_parquet(os.path.join(D, "m1.parquet"))
led = pd.read_parquet(os.path.join(D, "ledger.parquet"))
for c in ("formation_time", "breakout_time", "retest_time"):
    led[c] = pd.to_datetime(led[c], utc=True)

T = m1.index
H, L, O, C = (m1[c].to_numpy(float) for c in ("high", "low", "open", "close"))
pc = np.r_[C[0], C[:-1]]
TR = np.maximum(H - L, np.maximum(abs(H - pc), abs(L - pc)))
ATR = pd.Series(TR).ewm(alpha=1 / 21, adjust=False).mean().to_numpy()      # Wilder ATR(21), M1
pos = {t: i for i, t in enumerate(T)}
K = 2                                                     # fractal: high/low strictly beyond 2 bars each side
fr_hi = np.zeros(len(H), bool); fr_lo = np.zeros(len(H), bool)
for d in range(1, K + 1):
    pass
hh = np.ones(len(H), bool); ll = np.ones(len(H), bool)
for d in range(1, K + 1):
    hh[K:-K] &= (H[K:-K] > H[K - d:len(H) - K - d]) & (H[K:-K] > H[K + d:len(H) - K + d])
    ll[K:-K] &= (L[K:-K] < L[K - d:len(H) - K - d]) & (L[K:-K] < L[K + d:len(H) - K + d])
hh[:K] = hh[-K:] = ll[:K] = ll[-K:] = False


def pt(ts):
    return ts.tz_convert("America/Los_Angeles")


def r_of(m):
    return None if m is None else float(m["r"])


out = []
for r in rows:
    touch = pd.Timestamp(r["touch_pt"], tz="America/Los_Angeles").tz_convert("UTC")
    tbar = touch.floor("1min")
    cand = led[(led["type"] == r["type"]) & (np.isclose(led["price"], r["level"])) & (led["breakout_time"] < tbar)
               & (led["retest_time"] == tbar)]
    if cand.empty:
        cand = led[(led["type"] == r["type"]) & (np.isclose(led["price"], r["level"])) & (led["breakout_time"] < tbar)]
        cand = cand[cand["formation_time"] == cand["formation_time"].max()]
    want = ({"spike+swing-P0"} if r["spike"] and r["swing"] else {"swing-P0"} if r["swing"] else
            {"spike-P0"} if r["spike"] else {"plain-P0"})
    k = cand[cand["p0_kind"].isin(want)]
    if k.empty:
        k = led[(led["type"] == r["type"]) & (np.isclose(led["price"], r["level"])) & (led["breakout_time"] < tbar)
                & (led["p0_kind"].isin(want)) & ((led["retest_time"].isna()) | (led["retest_time"] >= tbar))]
    if k.empty:
        print("NO MATCH", r["idx"], r["type"], r["level"], r["touch_pt"], want); k = cand
    lv = k.sort_values("formation_time").iloc[-1]
    hi_side = r["type"] == "LHPB"                         # swing high (LHPB) / swing low (LLPB)
    f, b, t = pos[lv["formation_time"]], pos[lv["breakout_time"]], pos[tbar]
    px, atr = r["level"], ATR[f]
    beyond = (lambda i: H[i] >= px) if hi_side else (lambda i: L[i] <= px)
    i = f - 1
    while i >= 0 and not beyond(i) and f - i <= 1440:
        i -= 1
    left_n = f - 1 - i                                    # bars before the P0 the level stayed the extreme
    lseg = slice(i + 1, f)
    depth_left = (((px - L[lseg].min()) if hi_side else (H[lseg].max() - px)) if left_n else 0.0)
    rseg = slice(f + 1, b)
    right_n = b - f - 1
    depth_right = ((px - L[rseg].min()) if hi_side else (H[rseg].max() - px)) if right_n > 0 else 0.0
    # same-kind fractals near the level price in the 24 h before the touch (not the P0 bar itself)
    w0 = pos.get((tbar - pd.Timedelta(hours=24)), None)
    w0 = max(0, t - 1440) if w0 is None else w0
    idx = np.arange(w0, t - K)
    same = hh if hi_side else ll
    opp = ll if hi_side else hh
    sp = H if hi_side else L
    op = L if hi_side else H
    near = lambda arr, pr, tol: int(((arr[idx]) & (abs(pr[idx] - px) <= tol) & (idx != f)).sum())
    # earlier tests of the price before the P0 formed (24 h): bars whose range reached within 0.5 pt of the level
    pre = np.arange(max(0, f - 1440), f)
    pre_tests = int((((H[pre] >= px - 0.5) & (L[pre] <= px + 0.5))).sum())
    # the P1 (breakout) candle and the return leg into the touch
    rng = H[b] - L[b]
    body = abs(C[b] - O[b]) / rng if rng > 0 else 0.0
    ext = (H[b:t + 1].max() - px) if hi_side else (px - L[b:t + 1].min())   # how far price went past the level after P1
    ext_i = b + int(np.argmax(H[b:t + 1]) if hi_side else np.argmin(L[b:t + 1]))
    fc_rng = H[f] - L[f]
    m = r["modes"]
    oz, re_ = m.get("opposite-zz"), m.get("range-edge")
    stop_pts = abs(r["fill"] - r["stop"])
    o = {
        "idx": r["idx"], "type": r["type"], "level": px, "touch_pt": r["touch_pt"], "swing": r["swing"], "spike": r["spike"],
        "p0_kind": lv["p0_kind"], "p0_pt": f"{pt(lv['formation_time']):%Y-%m-%d %H:%M}",
        "left_bars": left_n, "right_bars": right_n,
        "depth_left_pts": round(float(depth_left), 2), "depth_right_pts": round(float(depth_right), 2),
        "depth_left_atr": round(float(depth_left / atr), 2), "depth_right_atr": round(float(depth_right / atr), 2),
        "er": round(float(lv["er_score"]), 2) if pd.notna(lv["er_score"]) else None,
        "frac_same_1pt": near(same, sp, 1.0), "frac_same_2pt": near(same, sp, 2.0), "frac_opp_2pt": near(opp, op, 2.0),
        "pre_tests": pre_tests,
        "p0_range_atr": round(float(fc_rng / atr), 2), "wick": r["wick"],
        "p1_range_atr": round(float(rng / atr), 2), "p1_body": round(float(body), 2),
        "p1_close_beyond": round(float((C[b] - px) if hi_side else (px - C[b])), 2),
        "form_to_break_min": b - f, "break_to_touch_min": int((tbar - lv["breakout_time"]).total_seconds() // 60),
        "ext_after_p1_pts": round(float(ext), 2), "ext_after_p1_atr": round(float(ext / atr), 2),
        "return_min": t - ext_i, "return_speed": round(float(ext / max(1, t - ext_i)), 2),
        "atr": round(float(atr), 2), "hour_pt": pt(touch).hour + pt(touch).minute / 60,
        "rth": 6.5 <= pt(touch).hour + pt(touch).minute / 60 < 13, "cluster": len(r["merged"]),
        "stop_pts": round(stop_pts, 2), "range_edge_dist": float(re_["dist"]) if re_ else None,
        "oz_dist": float(oz["dist"]) if oz else None,
        "r_oz": r_of(oz), "r_re": r_of(re_), "out_oz": oz["outcome"] if oz else "no_target", "out_re": re_["outcome"] if re_ else None,
        "mae": float((oz or re_)["mae"]), "tags": r["tags"],
    }
    out.append(o)
df = pd.DataFrame(out)
df.to_csv(os.path.join(D, "features.csv"), index=False)
df.to_json(os.path.join(D, "features.json"), orient="records", indent=1)
print(df[df.swing][["idx", "type", "level", "p0_kind", "left_bars", "right_bars", "depth_left_atr", "depth_right_atr",
                    "frac_same_2pt", "er", "r_oz", "r_re"]].to_string())
