"""Read-only look at what the live engine had in front of it at each fill (changes nothing).

  python scid_only/run_scid_only.py box_merge_study/fill_candidates.py --day 2026-10-16

For every trade it prints the LXPB levels (candidates) of the entry's cluster, the anchor, the swerve, other live
same-type levels within 10 pt, and each range box the trade serves with its edges.
M1 FADE PLAYBOOK ONLY: go-with trades are printed but marked, they are not part of it.
Uses the engine's defaults (1 s decisions, H1-backed range merge on, fade 2 off).
"""
import argparse

import pandas as pd

import render_labels_report as R
from live_range import replay as RP
from live_range.engine import Engine, Params
from live_range.levels import key as level_key

PT = "America/Los_Angeles"


def pt(ns):
    return pd.Timestamp(ns, unit="ns", tz="UTC").tz_convert(PT).strftime("%m-%d %H:%M")


def lv_txt(eng, lv):
    fate = eng.lv.fate.get(level_key(lv))
    kind = lv.get("p0_kind") or "?"
    spike = " spike" if lv.get("is_spike") else ""
    return (f"{float(lv['price']):.2f} {lv['type']} {kind}{spike}, formed {pt(pd.Timestamp(lv['formation_time']).value)}, "
            f"broke {pt(pd.Timestamp(lv['breakout_time']).value)}"
            + (f", {fate[0]} {pt(fate[1])}" if fate else ", waiting"))


class Spy(Engine):
    def _rebuild_entries(self, t):
        super()._rebuild_entries(t)
        snap = {typ: [list(cl) for cl in self._clusters(typ)] for typ in ("LLPB", "LHPB")}
        live = {typ: list(self.lv.live(typ)) for typ in ("LLPB", "LHPB")}
        self.snaps.append((t, snap, live))

    def _fill(self, o, t, price, serves, stop, target, src):
        typ = o["type"]
        armed = o.get("armed_t", t)
        snap = next((x for x in reversed(self.snaps) if x[0] <= armed), self.snaps[-1])
        ck = o.get("cluster_key")
        chain = next((cl for cl in snap[1][typ] if any(level_key(x) == ck for x in cl)), [])
        ids = {level_key(x) for x in chain}
        others = [lv for lv in snap[2][typ] if abs(float(lv["price"]) - o["level_price"]) <= 10 and level_key(lv) not in ids]
        info = {"t": t, "serves": list(serves), "entry": o["level_price"], "note": o.get("note"), "anchor": o["anchor"],
                "cluster": chain, "others": others, "armed": armed, "boxes": []}
        for (b, d, pb) in serves:
            ep = o["boxes"].get((b, d))
            if ep is not None:
                bx = ep["box"]
                info["boxes"].append((b, d, pb, bx["low"], bx["high"], bx["start_t"], bx.get("break_t"), bx["n"],
                                      ep.get("breakout_time"), bool(ep.get("provisional"))))
        self.spy.append(info)
        super()._fill(o, t, price, serves, stop, target, src)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-16")
    ap.add_argument("--warmup-days", type=float, default=5.0)
    a = ap.parse_args()
    day = pd.Timestamp(a.day)
    lo = (day - pd.Timedelta(days=1) + pd.Timedelta(hours=15)).tz_localize(PT).tz_convert("UTC")
    hi = min(pd.Timestamp(R._scid_bounds("EPZ26")[1]).floor("1min"), lo + pd.Timedelta(hours=21))
    eng, start = RP.build_engine(lo, hi, a.warmup_days, Params(bar_minutes=1), engine_cls=Spy)
    eng.spy, eng.snaps = [], []
    RP.feed(eng, start, hi)
    for tr, info in zip(eng.trades, eng.spy):
        fade = all(pb == "fade" for _, _, pb in info["serves"])
        print(f"\n=== trade {tr['id']} {pt(tr['t'])} {tr['type']} {tr['side']} fill {tr['price']:.2f} stop {tr['stop']:.2f} "
              f"target {tr['target']:.2f}" + ("" if fade else "   [GO-WITH, not part of the fade playbook]"))
        print(f"  entry level {info['entry']:.2f}" + (f"  ({info['note']})" if info["note"] else "")
              + f"; anchor {lv_txt(eng, info['anchor'])}")
        print(f"  order placed {pt(info['armed'])}. LXPB cluster it came from: {len(info['cluster'])} levels "
              f"(chained within 2 pt; the entry is the extreme, unless swerved):")
        for lv in sorted(info["cluster"], key=lambda x: float(x["price"])):
            print("     ", lv_txt(eng, lv))
        if info["others"]:
            print("  other waiting same-type levels within 10 pt of the entry at that time:")
            for lv in sorted(info["others"], key=lambda x: float(x["price"])):
                print("     ", lv_txt(eng, lv))
        print("  range boxes served (edges as they stand at the fill):")
        for (b, d, pb, low, high, st, brk, n, bo, prov) in info["boxes"]:
            broken = "bottom" if d == "below" else "top"
            print(f"      box {b} [{pb}] broke {d}: low {low:.2f}  high {high:.2f}  (broken edge = {broken}, "
                  f"{low if d == 'below' else high:.2f}); started {pt(st)}, breakout bar {pt(bo) if bo else '?'}"
                  f"{', provisional at the fill' if prov else ''}")


if __name__ == "__main__":
    main()
