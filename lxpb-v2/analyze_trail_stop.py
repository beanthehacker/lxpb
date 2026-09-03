"""Would a trailing stop have helped?

Replays every trade in an already-rendered stop/target report against real 1s
ticks, replacing the fixed stop with a trail sitting N points behind the
running extreme (never worse than the original fixed stop), and reports total R
per trail distance. The `trail=100` control row is a no-trail baseline: it must
reproduce the report's own target count exactly, which is what validates the
replay.

Trades are read back out of the rendered HTML rather than re-resolved, so this
costs ~2 min instead of ~20. The HTML prints times truncated to the second, so
both ends of the window are re-pinned to the tick that actually straddles the
fill price within its displayed second; each trade's own MAE/MFE is then
recomputed as a check that the window and price scale are right (492/502 exact
on stop3.5/target15 -- the rest are 0.25pt edge-minute fallbacks inside
_compute_excursion).

The give-back metric itself now lives in render_stop_target_report as
_compute_giveback and is a per-trade column ("Max DD"). That column uses a
high/low watermark so it lines up with the MAE/MFE columns, which makes it
~1 tick wider than what you could actually liquidate at; this script marks
both the watermark and the current price on the SAME side, because a trail
has to fill on one side. Expect its numbers to run ~0.25 under the column.

Usage:  python analyze_trail_stop.py [report.html] [stop] [target]
"""
import re
import sys

import numpy as np
import pandas as pd

import render_labels_report as R
import render_stop_target_report as S

REPORT = "stop3.5_target15_trades_report_2026_full_year.html"
STOP = 3.5
TARGET = 15.0
MIN_PULLBACK = S.MIN_GIVEBACK_PTS
if len(sys.argv) > 1:
    REPORT = sys.argv[1]
if len(sys.argv) > 3:
    STOP, TARGET = float(sys.argv[2]), float(sys.argv[3])


def pt_to_utc(s):
    """tz-AWARE UTC: _ticks_for_window compares against tz-aware segment
    bounds, so handing it naive timestamps raises."""
    return (pd.Timestamp(s.replace(" PT", ""))
            .tz_localize("America/Los_Angeles", ambiguous=True, nonexistent="shift_forward")
            .tz_convert("UTC"))


def load_rows():
    h = open(REPORT, encoding="utf-8").read()
    # NB: the excursion-percentile table now also has a <thead>, and it comes
    # first -- pick the one that is actually the trades table.
    head = next(x for x in re.findall(r"<thead>(.*?)</thead>", h, re.S) if "Outcome" in x)
    ths = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]
    ix = {n: ths.index(n) for n in
          ("#", "Type", "Entry (touch) time", "Entry", "Outcome", "R",
           "Exit time", "Exit px", "MAE (win)", "MFE (loss)")}
    out = []
    for row in re.findall(r'<tr class="lvl-row.*?</tr>', h, re.S):
        c = [re.sub(r"<[^>]+>", "", x).strip()
             for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if c[ix["Exit time"]] in ("-", ""):
            continue
        out.append({
            "i": int(c[ix["#"]]),
            "type": c[ix["Type"]],
            "is_long": c[ix["Type"]] == "LHPB",
            "entry_pt": c[ix["Entry (touch) time"]],
            "entry": float(re.sub(r"[^0-9.]", "", c[ix["Entry"]])),
            "outcome": c[ix["Outcome"]],
            "r": float(c[ix["R"]]),
            "exit_pt": c[ix["Exit time"]],
            "exit_px": None if c[ix["Exit px"]] in ("-", "") else float(c[ix["Exit px"]]),
            "mae": None if c[ix["MAE (win)"]] == "-" else float(c[ix["MAE (win)"]]),
            "mfe": None if c[ix["MFE (loss)"]] == "-" else float(c[ix["MFE (loss)"]]),
        })
    return out


def analyse(t, want_path=False):
    t0, t1 = pt_to_utc(t["entry_pt"]), pt_to_utc(t["exit_pt"])
    offset, _sym = R._offset_for_ts(t0)
    raw_entry = t["entry"] - offset
    ticks = R._ticks_for_window(t0, t1 + pd.Timedelta(seconds=2))
    if ticks is None or ticks.empty:
        return None
    # The HTML prints times truncated to the second, so [t0, t1] can start and
    # end up to 1s early. Pin both ends to the tick that actually straddles
    # the fill price within its displayed second.
    ticks = ticks.loc[(ticks.index >= t0) & (ticks.index < t1 + pd.Timedelta(seconds=1))]
    if ticks.empty:
        return None
    hi_a, lo_a = ticks["High"].to_numpy(float), ticks["Low"].to_numpy(float)
    idx_a = ticks.index

    in_sec0 = idx_a < t0 + pd.Timedelta(seconds=1)
    hit = np.flatnonzero(in_sec0 & (lo_a <= raw_entry + 1e-9) & (hi_a >= raw_entry - 1e-9))
    lo_i = int(hit[0]) if hit.size else 0

    hi_i = len(idx_a) - 1
    if t.get("exit_px") is not None:
        raw_x = t["exit_px"] - offset
        hit = np.flatnonzero((idx_a >= t1) & (lo_a <= raw_x + 1e-9) & (hi_a >= raw_x - 1e-9))
        if hit.size:
            hi_i = int(hit[0])
    ticks = ticks.iloc[lo_i:hi_i + 1]
    if ticks.empty:
        return None

    ask = ticks["High"].to_numpy(float)
    bid = ticks["Low"].to_numpy(float)

    # Exit side of an OPEN position: a long is closed by selling into the
    # bid, a short by lifting the ask -- the same sides _market_fill uses.
    # Watermark runs from the first tick (peak->trough give-back), NOT seeded
    # at entry: we are asking how far a trail can sit behind the extreme.
    mark = bid if t["is_long"] else ask
    # Favourable side -- the one a resting target limit is measured against,
    # matching _compute_excursion (long -> ask/High, short -> bid/Low).
    fav = ask if t["is_long"] else bid
    if t["is_long"]:
        peak = np.maximum.accumulate(mark)
        dd = peak - mark
    else:
        peak = np.minimum.accumulate(mark)
        dd = mark - peak
    k = int(np.argmax(dd))

    # Reproduce the report's own two columns as a check on window + scale.
    hi, lo = max(ask.max(), raw_entry), min(bid.min(), raw_entry)
    if t["is_long"]:
        mfe_chk, mae_chk = hi - raw_entry, raw_entry - lo
    else:
        mfe_chk, mae_chk = raw_entry - lo, hi - raw_entry

    res = {"max_dd": float(dd[k]), "n_ticks": len(ticks),
           "dd_time": ticks.index[k], "mfe_chk": float(mfe_chk),
           "mae_chk": float(mae_chk), "offset": offset,
           "_arr": (mark, peak, dd, fav, raw_entry, ticks.index)}
    if want_path:
        res["path"] = (ticks.index, mark + offset, peak + offset, dd)
    return res


def trail_sim(t, a, trail, stop=STOP, target=TARGET):
    """Replay the trade with the fixed stop REPLACED by a trail `trail` pts
    behind the running extreme, once that trail is better than the fixed stop.
    Returns realised R. Pullbacks < MIN_PULLBACK are ignored as noise."""
    mark, peak, dd, fav, raw_entry, idx = a["_arr"]
    sgn = 1.0 if t["is_long"] else -1.0
    # effective stop level: never worse than the original fixed stop
    trail_lvl = peak - sgn * trail
    fixed_lvl = raw_entry - sgn * stop
    lvl = np.maximum(trail_lvl, fixed_lvl) if t["is_long"] else np.minimum(trail_lvl, fixed_lvl)
    hit = np.flatnonzero((mark <= lvl) if t["is_long"] else (mark >= lvl))
    # a give-back below the noise floor cannot trigger a trail exit
    hit = hit[dd[hit] >= MIN_PULLBACK - 1e-9]
    tgt_lvl = raw_entry + sgn * target
    tgt = np.flatnonzero((fav >= tgt_lvl) if t["is_long"] else (fav <= tgt_lvl))
    j_t = int(tgt[0]) if tgt.size else None
    j_s = int(hit[0]) if hit.size else None
    if j_s is not None and (j_t is None or j_s <= j_t):
        return sgn * (mark[j_s] - raw_entry) / stop, "trail", idx[j_s]
    if j_t is not None:
        return target / stop, "target", idx[j_t]
    return sgn * (mark[-1] - raw_entry) / stop, "open", idx[-1]


if __name__ == "__main__":
    rows = load_rows()
    good, bad = [], []
    for t in rows:
        a = analyse(t)
        if a is None:
            bad.append(t); continue
        t["a"] = a
        ref = t["mfe"] if t["outcome"] == "LOSS" else t["mae"]
        got = a["mfe_chk"] if t["outcome"] == "LOSS" else a["mae_chk"]
        t["max_dd"] = a["max_dd"]
        (good if (ref is not None and abs(ref - got) <= 0.011) else bad).append(t)
    print(f"{len(rows)} resolved trades -> {len(good)} reproduce the report's "
          f"MAE/MFE exactly, {len(bad)} unmatched\n")
    for b in bad[:6]:
        r = b["mfe"] if b["outcome"] == "LOSS" else b["mae"]
        g = b.get("a", {}).get("mfe_chk" if b["outcome"] == "LOSS" else "mae_chk")
        print(f"   #{b['i']} {b['outcome']} report={r} recomputed="
              + (f"{g:.2f}" if g is not None else "no ticks"))

    dd = np.array([t["max_dd"] for t in good])
    print(f"\nmax peak->trough give-back, points ({len(dd)} trades)")
    print("   " + "  ".join(f"p{q}={np.percentile(dd,q):.2f}" for q in (25,50,75,90,95,99)))
    print(f"   mean {dd.mean():.2f}   max {dd.max():.2f}")
    for lab in ("WIN", "LOSS"):
        s = np.array([t["max_dd"] for t in good if t["outcome"] == lab])
        print(f"   {lab:5} n={s.size:3}  median={np.median(s):5.2f}  mean={s.mean():5.2f}  max={s.max():5.2f}")

    base = sum(t["r"] for t in good)
    print(f"\ntrail sweep (fixed stop {STOP} / target {TARGET}, pullbacks >= {MIN_PULLBACK} pt)")
    print(f"   baseline (no trail): total {base:+7.1f} R over {len(good)} trades, "
          f"avg {base/len(good):+.3f} R")
    print(f"\n   {'trail':>6}{'total R':>10}{'avg R':>8}{'targets':>9}{'trailed':>9}{'win%':>7}{'avg win':>9}{'avg loss':>9}")
    for tr in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 100.0):
        rs, how = [], []
        for t in good:
            r, w, _ = trail_sim(t, t["a"], tr)
            rs.append(r); how.append(w)
        rs = np.array(rs)
        wins = rs[rs > 0]; loss = rs[rs <= 0]
        print(f"   {tr:6.1f}{rs.sum():10.1f}{rs.mean():8.3f}"
              f"{how.count('target'):9}{how.count('trail'):9}"
              f"{len(wins)/len(rs)*100:6.1f}%{(wins.mean() if len(wins) else 0):9.2f}"
              f"{(loss.mean() if len(loss) else 0):9.2f}")
