"""Example page for the M5-sweep algo: the renderer's page for the chosen rows + the algo's marks and step log per row."""
import json, html, pickle, re
import pandas as pd
import algo
from algo import PT, fmt

SRC, OUT = "ex_src.html", "ict_algo.html"
src = open(SRC, encoding="utf-8").read()
keys = re.findall(r'data-key="([^"]+)"', src)
res = {r["row"]: r for r in pickle.load(open("algo.pkl", "rb"))}
allres = list(res.values())
rows = [res[pd.Timestamp(k[:13].replace("T", " ") + ":00", tz="UTC").tz_convert(PT)] for k in keys]

# ---- the step log goes into each row's note strip (above its chart)
m = re.search(r"^const CHARTS = (\[.*\]);$", src, re.M)
charts = json.loads(m.group(1))
for cd, r in zip(charts, rows):
    cd["notes"] = ([{"color": "#00e676", "text": "ALGO: " + r["cat"]}]
                   + [{"color": "#d1d4dc", "text": f"{fmt(u, True)} PT  {s}"} for u, s in r["log"]] + cd.get("notes", []))
src = src[:m.start(1)] + json.dumps(charts) + src[m.end(1):]

seed = {k: dict(draws=r["draws"], cursor=r["cursor"]) for k, r in zip(keys, rows)}
seed_js = ("<script>(function () { const F = location.pathname.split('/').pop(), S = " + json.dumps(seed) + ";\n"
           "Object.keys(S).forEach(k => { try { const dk = 'postool_v1_draw_' + F + '_' + k, ck = 'postool_v1_cursor2_' + F + '_' + k, sk = 'ict_seeded5_' + F + '_' + k;\n"
           "  const old = JSON.parse(localStorage.getItem(dk) || '[]').filter(d => !String(d.id).startsWith('ict_'));\n"
           "  if (!localStorage.getItem(sk)) { localStorage.setItem(dk, JSON.stringify(old.concat(S[k].draws))); localStorage.setItem(ck, JSON.stringify(S[k].cursor));\n"
           "    localStorage.setItem(sk, '1'); } } catch (e) {} }); })();</script>\n")
label_js = """        ctx.setLineDash([]);
        if (d.label) {
          ctx.font = '11px Courier New, monospace'; ctx.fillStyle = d.color; ctx.textBaseline = 'bottom';
          if (d.type === 'rect') {
            const xl = Math.min(g.x0, g.x1), xr = Math.max(g.x0, g.x1), yt = Math.min(g.y0, g.y1), yb = Math.max(g.y0, g.y1), la = d.la || 'tl';
            if (la === 'l') { ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(d.label, xl - 4, yt); }
            else if (la === 'tr') { ctx.textAlign = 'right'; ctx.fillText(d.label, xr - 3, yt - 2); }
            else if (la === 'br') { ctx.textAlign = 'right'; ctx.textBaseline = 'top'; ctx.fillText(d.label, xr - 3, yb + 2); }
            else { ctx.textAlign = 'left'; ctx.fillText(d.label, xl + 3, yt - 2); }
          } else { ctx.textAlign = 'right'; ctx.fillText(d.label, W - 4, g.y - 2); }
        }
"""


def rep(a, b):
    global src
    assert src.count(a) == 1, (src.count(a), a)
    src = src.replace(a, b)


rep("        ctx.setLineDash([]);\n        if (this.edit && this.focus(d))", label_js + "        if (this.edit && this.focus(d))")
rep("const _DEF_TF = { a: 'H1', b: 'M5' };", "const _DEF_TF = { a: 'M5', b: 'M2' };")
rep("const _NEAR = 400;", "const _NEAR = 110;")

# ---- tally over the 115 cross bars of 2026
tally = {}
for r in allres:
    if not r["trades"]:
        tally.setdefault(r["cat"].split(" (")[0], []).append(None)
    for tr in r["trades"]:
        tally.setdefault(tr["kind"].split(":")[0].replace("pullback", "pullback entry"), []).append(tr)
trs = "".join(
    f"<tr><td class='left'>{html.escape(k)}</td><td>{len(v)}</td>" + (
        "<td>-</td><td>-</td><td>-</td>" if v[0] is None else
        f"<td>{100 * sum(t['out'] == 'target' for t in v) / len(v):.0f}%</td><td>{sum(t['R'] for t in v) / len(v):+.2f}</td><td>{sum(t['R'] for t in v):+.1f}</td>") + "</tr>"
    for k, v in sorted(tally.items()))


def example(n, k, r):
    t = r["row"]
    steps = "".join(f"<li><span class='src-tag'>{fmt(u, True)} PT</span> {html.escape(s)}</li>" for u, s in r["log"])
    return (f"<p class='lead'><b>Row {n}: {t:%a %Y-%m-%d %H:%M} PT, {'long (sweep of an H1 swing low)' if r['bull'] else 'short (sweep of an H1 swing high)'}"
            f" &rarr; <span style='color:#00e676'>{html.escape(r['cat'])}</span></b></p><ol class='ict'>{steps}</ol>")


sw = lambda c, t: f"<span style='color:{c}'>&#9632;</span> {t}"
C = algo.COL
block = f"""
<style>
table.ict {{ border-collapse:collapse; margin:6px 0 14px; font-size:0.82em; }}
table.ict th, table.ict td {{ border:1px solid #2a2e39; padding:3px 8px; text-align:right; white-space:nowrap; }}
table.ict th.left, table.ict td.left {{ text-align:left; white-space:normal; }}
ol.ict {{ margin:0 0 12px 8px; font-size:0.84em; color:var(--text-dim); }} ol.ict li {{ margin:1px 0; }}
</style>
<p class="lead"><b>M5 SWEEP ALGO, EXAMPLES FOR REVIEW (temp file).</b> Each row opens on M5 with the algo's marks drawn and its step log above the chart
(and below). The rules as I built them, for a short (longs mirrored):</p>
<ol class="ict">
<li><b>Start:</b> an M5 candle trades above an untested, confirmed H1 swing high. On this page the sweeps are the ones inside the 2026 cross bar hours (the examples come from that file); the algo itself never looks at the H1 bar.</li>
<li><b>Close back:</b> that candle or one of the next 4 closes back below the swing price. None does = no setup. The sweep extreme is the highest price up to that close.</li>
<li><b>Stacked liquidity:</b> at that close, any other untested H1 swing high above the sweep extreme, within 1 x H1 ATR(14) of it = liquidity still resting there = NO TRADE. (Equal highs are swing highs in that band; the ones this sweep already took do not count.)</li>
<li><b>OB:</b> the latest up-close candle, on M1, M2 and M5, kept up to date as candles close. That one candle is the OB only if its body is at least 70% of its range; otherwise the OB is the whole leg of higher (or equal) lows that ends with it.</li>
<li><b>Stop-entry, only while displacing:</b> armed from the first close back. When price crosses a tick below an OB, the entry counts only if a gap is being left at that second: the forming M2 (or M1) candle's high so far is below the low of the candle two before it. A crossing with no gap is ignored and the OB stays live. Stop = a tick above the sweep extreme at that moment (a new extreme inside the 4 candles only moves it). Stopped inside the 4 candles = still armed; a new extreme after them with no position = void.</li>
<li><b>Target:</b> the nearest untested H1 swing low below: an H1 low with two higher lows on each side, where the hour still forming counts as a bar on the right.</li>
<li><b>Pullback (whichever comes first, it or the stop-entry):</b> a limit at an M5 or M2 LLPB level that the move down broke out of and has not come back to, taken at its first touch with a limit, if it is (a) in the OTE zone, 62-79% back from the run's low to the sweep extreme, or (b) inside an old FVG (M5, M15 or H1 candles) of the left leg. The left leg runs from the lowest M5 price since the last confirmed H1 swing low up to the sweep extreme. Same stop and target rule. The first H1 swing low reached before such a touch = no entry.</li>
</ol>
<p class="lead">Marks: {sw(C['sweep'], 'sweep extreme')} &middot; {sw(C['entry'], 'close-back candle, entries (with their R)')} &middot; {sw(C['ob'], 'M5 / M2 / M1 OB')} &middot; {sw(C['fvg'], "left leg's FVG")}
&middot; {sw(C['ote'], 'OTE zone')} &middot; {sw(C['lvl'], 'the LXPB level entered')} &middot; {sw(C['pool'], 'target, or the resting H1 swing that cancels the trade')}. They are ordinary drawings.</p>
<p class="lead"><b>All 115 cross bar hours of 2026, each trade counted</b> (1 s fills, no costs; a second reaching stop and target = stop). These hours were picked because they ENDED as reversals, so the
numbers flatter the algo and say nothing about an edge; the real test is every M5 sweep of an H1 swing, cross bar or not.</p>
<table class="ict"><thead><tr><th class="left">Outcome</th><th>count</th><th>target first</th><th>avg R</th><th>total R</th></tr></thead><tbody>{trs}</tbody></table>
{''.join(example(n + 1, k, r) for n, (k, r) in enumerate(zip(keys, rows)))}
"""
h = re.search(r"<h1>.*?</h1>", src).group(0)
rep(h, "<h1>M5 sweep of an H1 swing: algo examples (temp)</h1>" + seed_js + block)
open(OUT, "w", encoding="utf-8").write(src)
print(OUT, len(src))
for k, r in zip(keys, rows): print(k, r["cat"])
