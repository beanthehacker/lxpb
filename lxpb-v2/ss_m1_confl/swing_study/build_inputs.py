"""(Paths: written for the session scratchpad; SRC = the y2026_fade_tr.html the report wrote, one folder up.)
Inputs for the swing-level study: the year report's 127 rows (parsed from its HTML) plus the M1 bars and the
M1 level ledger the report used (same window, same builder). Writes rows.json, m1.parquet, ledger.parquet."""
import html, json, os, re, sys
import pandas as pd

REPO = r"E:\lxpb-v2\nervous-clarke-0cf3c2\lxpb-v2"
sys.path.insert(0, REPO)
os.chdir(REPO)
OUT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(OUT, "..", "y2026_fade_tr.html")

s = open(SRC, encoding="utf-8").read()
rows = []
for r in re.findall(r'<tr class="lvl-row.*?</tr>', s, re.S):
    a = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', r.split(">", 1)[0]))
    typ, px, d, tm, _ = a["key"].split("_", 4)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
    txt = [re.sub(r"<[^>]+>", " ", c) for c in cells]
    rows.append({
        "idx": int(a["idx"]), "type": typ, "level": float(px), "touch_pt": f"{d} {tm}",
        "tags": a["dyn-tags"].split(), "spike": a["lvl-spike"] == "1", "swing": a["lvl-swing"] == "1",
        "wick": float(a["lvl-wick"]) if a["lvl-wick"] else None,
        "modes": json.loads(html.unescape(a["modes"])), "merged": txt[4].split(), "fill": float(txt[6].split()[0]),
        "stop": float(txt[8].split()[0]),
    })
json.dump(rows, open(os.path.join(OUT, "rows.json"), "w"), indent=1)
print(len(rows), "rows;", sum(r["swing"] for r in rows), "swing")

import render_labels_report as R
import lxpb_levels_cache as LC
lo = pd.Timestamp("2025-12-23 23:00", tz="UTC")
hi = pd.Timestamp("2026-10-26 22:00", tz="UTC")
m1 = R._tick_bars_spanning(lo, hi, "1min")
m1.to_parquet(os.path.join(OUT, "m1.parquet"))
print(len(m1), "M1 bars")
led = LC.m1_levels(m1, verbose=False, plain_p0=LC.PLAIN_P0_TRACKED)
led.to_parquet(os.path.join(OUT, "ledger.parquet"))
print(len(led), "ledger rows", list(led.columns))
