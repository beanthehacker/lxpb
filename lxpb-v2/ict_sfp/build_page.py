"""The sample page with the ICT structures of each row drawn on it (M5) and the strategy test results on top."""
import json, html
import numpy as np, pandas as pd
import ict, summ
from ict import CB, load_es, PT

NAMES = {"A stop-entry over OB": "A stop-entry at the OB's far edge", "B1 short pool failure -> FVG": "B1 fade the failure at the pool, target the FVG",
         "B2 long FVG edge after pool -> run high": "B2 after the pool: re-enter at the FVG edge, target the run's extreme",
         "B3 long OB top after pool -> run high": "B3 after the pool: re-enter at the OB edge, target the run's extreme",
         "C1 limit FVG edge": "C1 limit at the FVG edge", "C2 limit FVG mid": "C2 limit at the FVG middle",
         "C3 limit OB top, stop under OB": "C3 limit at the OB edge, stop beyond the OB", "C4 limit OB top, stop under sweep": "C4 limit at the OB edge, stop beyond the sweep",
         "C5 limit OTE 62%": "C5 limit at OTE 62%", "C6 limit OTE 70.5%": "C6 limit at OTE 70.5%", "REF market at H1 close": "REF market at the H1 close"}
SRC, OUT = "ict_src.html", "ict_marks.html"
COL = dict(ob="#ff9800", fvg="#2962ff", disp="#00e676", ote="#ff5cb8", pool="#ffffff", sweep="#f23645", entry="#1b8a3c")
src = open(SRC, encoding="utf-8").read()
keys = __import__("re").findall(r'data-key="([^"]+)"', src)

h1f = load_es("1h", tz=PT); h1 = h1f[["open", "high", "low", "close"]]
rows = CB.collect(h1f)
want = [pd.Timestamp(k[:13].replace("T", " ") + ":00", tz="UTC").tz_convert(PT) for k in keys]
rows = rows.loc[rows.index.isin(want)].sort_index()
assert len(rows) == len(keys), (len(rows), keys)
m1 = load_es("1m", start=rows.index[0] - pd.Timedelta(days=6), tz=PT)[["open", "high", "low", "close"]]

fmt = lambda u: pd.Timestamp(u, unit="s", tz="UTC").tz_convert(PT).strftime("%a %m-%d %H:%M:%S")
seed, per_row = {}, []
for key, row in zip(keys, rows.itertuples()):
    marks = dict(tf=5, out=[])
    tr = pd.DataFrame(ict.run_row(row, m1, h1, tfs=(5,), marks=marks))
    ds = []
    rect = lambda id, t0, t1, p0, p1, c, label, alpha=0.18, la='tl': ds.append(dict(la=la, id="ict_" + id, type="rect", t0=int(t0), p0=float(p0), t1=int(t1), p1=float(p1),
                                                                       color=c, fill=c, alpha=alpha, width=1, dash="solid", label=label))
    ray = lambda id, t, p, c, label, dash="solid": ds.append(dict(id="ict_" + id, type="ray", t=int(t), p=float(p), color=c, width=1, dash=dash, label=label))
    note = "no displacement candle (M5) within an hour of the cross bar's close"
    if marks["out"]:
        m = marks["out"][0]; S = m["S"]; end = m["t_c"] + S + 3 * 3600
        ray("sweep", m["t_e"], m["E"], COL["sweep"], f"sweep extreme {m['E']:.2f}")
        rect("ob", m["t_ob"], end, m["ob_lo"], m["ob_hi"], COL["ob"], "OB")
        rect("disp", m["t_b"], m["t_b"] + S, m["b_lo"], m["b_hi"], COL["disp"], "displacement", 0.0)
        rect("fvg", m["t_a"], end, m["fvg_lo"], m["fvg_hi"], COL["fvg"], "FVG", 0.18, "tr")
        c5 = tr[(tr.strat.str.startswith("C5")) & (tr["mode"] == "live")] if len(tr) else tr
        leg = float(c5["ote_hi"].iloc[0]) if len(c5) else m["leg_hi"]
        rect("ote", m["t_c"] + S, end, leg - 0.62 * (leg - m["E"]), leg - 0.79 * (leg - m["E"]), COL["ote"],
             f"OTE 62-79% of {m['E']:.2f} -> {leg:.2f}", 0.12, "br")
        if m["pool"] is not None: ray("pool", m["t_c"] + S, m["pool"], COL["pool"], f"liquidity pool {m['pool']:.2f} (H1 swing)", "dashed")
        note = (f"displacement candle {fmt(m['t_b'])[4:-3]} PT, FVG {min(m['fvg_lo'], m['fvg_hi']):.2f}-{max(m['fvg_lo'], m['fvg_hi']):.2f}, "
                f"OB {min(m['ob_lo'], m['ob_hi']):.2f}-{max(m['ob_lo'], m['ob_hi']):.2f}, structure shift {'yes' if m['mss'] else 'no'}")
        cursor = end
    else:
        cursor = int(row.Index.timestamp()) + 4 * 3600
    a = tr[(tr.strat.str.startswith("A")) & (tr["mode"] == "live")] if len(tr) else tr
    if len(a):
        x = a.iloc[0]
        rect("a", x["ob_t"], x["t_fill"] + 900, x["entry"], x["entry"], COL["entry"], f"A stop-entry {x['entry']:.2f}", 0.0, "l")
    seed[key] = dict(draws=ds, cursor=cursor)
    if len(tr):
        tr = tr.sort_values(["mode", "t_fill"])
        tr["fill"] = tr["t_fill"].map(fmt); tr["exit_t"] = tr["t_exit"].map(fmt)
    per_row.append((row, note, tr))

# ---------------------------------------------------------------- results
t26 = pd.read_parquet("t26.parquet"); tall = pd.concat([pd.read_parquet("t19.parquet"), t26]); s26 = pd.read_parquet("s26.parquet")


def cell(g):
    if not len(g): return "<td>-</td><td>-</td><td>-</td>"
    net = g["R"].to_numpy() - summ.COST / g["risk"].to_numpy()
    lo, hi = summ.ci(net)
    sig = lo > 0 or hi < 0
    col = "#4ade80" if (sig and lo > 0) else "#f87171" if (sig and hi < 0) else "inherit"
    return (f"<td>{len(g)}</td><td>{100 * (g['out'] == 'target').mean():.0f}%</td>"
            f"<td style='color:{col}'>{net.mean():+.2f} <span class='src-tag'>({lo:+.2f} to {hi:+.2f})</span></td>")


def results(tf):
    strats = sorted(t26.strat.unique(), key=lambda s: (s[0] != "R", s))
    out = ["<table class='ict'><thead><tr><th class='left' rowspan='2'>Strategy</th><th colspan='3'>2026, entered inside the hour (hindsight)</th>"
           "<th colspan='3'>2026, orders only after the H1 close</th><th colspan='3'>2019 - 2026, orders only after the H1 close</th></tr>"
           "<tr>" + "<th>trades</th><th>target first</th><th>avg R after costs (95% range)</th>" * 3 + "</tr></thead><tbody>"]
    for s in strats:
        k = 0 if s.startswith("REF") else tf
        f = lambda t, mode: t[(t.strat == s) & (t.tf == k) & (t["mode"] == mode)]
        out.append(f"<tr><td class='left'>{html.escape(NAMES[s])}</td>{cell(f(t26, 'live'))}{cell(f(t26, 'closed'))}{cell(f(tall, 'closed'))}</tr>")
    a = s26[(s26.strat.str.startswith("A")) & (s26.tf == tf)]
    out.append(f"<tr><td class='left'>A on EVERY H1 swing sweep of 2026 ({a.row.nunique()} hours, cross bar or not)</td>{cell(a)}<td colspan='6' class='left'>"
               f"of which the {int(a.cross.sum())} that closed as cross bars: {(a[a.cross].R - summ.COST / a[a.cross].risk).mean():+.2f} R; "
               f"the {int((~a.cross).sum())} that did not: {(a[~a.cross].R - summ.COST / a[~a.cross].risk).mean():+.2f} R</td></tr>")
    return "".join(out) + "</tbody></table>"


def row_table(row, note, tr):
    hd = f"<p class='lead'><b>{row.Index:%a %Y-%m-%d %H:%M} PT, {'bullish' if row.bullish else 'bearish'}</b>: {note}.</p>"
    if not len(tr): return hd
    rs = "".join(f"<tr><td class='left'>{html.escape(NAMES[x.strat])}</td><td class='left'>{'inside the hour' if x.mode == 'live' else 'after the close'}</td><td>{x.side}</td>"
                 f"<td>{x.fill} PT</td><td>{x.entry:.2f}</td><td>{x.stop:.2f}</td><td>{x.target:.2f}</td><td>{x.rr:.1f}</td>"
                 f"<td>{x.out} {x.exit_t} PT</td><td style='color:{'#4ade80' if x.R > 0 else '#f87171'}'>{x.R:+.2f}</td></tr>" for x in tr.itertuples())
    return (hd + "<table class='ict'><thead><tr><th class='left'>Strategy (M5)</th><th class='left'>Orders</th><th>Side</th><th>Fill</th><th>Entry</th><th>Stop</th>"
            "<th>Target</th><th>Planned RR</th><th>Exit</th><th>R</th></tr></thead><tbody>" + rs + "</tbody></table>")


sw = lambda c, t: f"<span style='color:{c}'>&#9632;</span> {t}"
block = f"""
<style>
table.ict {{ border-collapse:collapse; margin:6px 0 14px; font-size:0.82em; }}
table.ict th, table.ict td {{ border:1px solid #2a2e39; padding:3px 8px; text-align:right; white-space:nowrap; }}
table.ict th.left, table.ict td.left {{ text-align:left; white-space:normal; }}
details.ict {{ margin:0 0 12px; }} details.ict summary {{ cursor:pointer; color:var(--text-dim); font-size:0.87em; }}
</style>
<p class="lead"><b>ICT MARKS (temp file).</b> Each row opens on M5, three hours after its displacement, with these drawn (they are
ordinary drawings: move, recolour or delete them): {sw(COL['sweep'], 'sweep extreme (the stop side)')} &middot; {sw(COL['ob'], 'OB: the last opposite-colour candle before the run that holds the displacement candle')}
&middot; {sw(COL['disp'], 'displacement candle: range at least 1.5 x ATR(14), body at least half of it, leaves an FVG')} &middot; {sw(COL['fvg'], 'FVG: the gap between the candles either side of it')}
&middot; {sw(COL['ote'], 'OTE: 62-79% back from the leg high to the sweep extreme')} &middot; {sw(COL['pool'], 'liquidity pool: the nearest untouched H1 swing beyond the leg (same swing rule as the swept one)')}
&middot; {sw(COL['entry'], 'strategy A stop-entry price')}. The first displacement after price went past the swept swing is used, at the latest one hour after the cross bar's close.</p>
<p class="lead"><b>DOES ANY OF IT HAVE AN EDGE? Not on these rows.</b> Every strategy below was run on all 115 cross bars of 2026 and on the
899 since 2019, on M5, M2 and M1, filled and exited on the 1 s price path (stop first when both in one second; 0.5 pt costs per trade; no end-of-day flat; still open after 5 days = marked at the last price).
Orders placed only after the H1 bar has closed (when the cross bar is known) lose or sit at zero: no strategy's average is above zero outside its 95% range over 2019 - 2026.
Entered INSIDE the hour, strategy A looks strong (2026, M5: about +0.8 R a trade), but that is hindsight: these rows were picked because the hour ended as a reversal.
Taken on every hour in which price went past an H1 swing, the same entry loses (last line of the table).
Green / red = the 95% range of the average excludes zero.</p>
<p class="lead">A = buy stop a tick over the latest down-close candle since the sweep began (the candidate OB), stop a tick under it, target the pool.
C1 - C4 = limit at the FVG edge / FVG middle / OB top after the displacement, first touch only, cancelled once the pool is reached; stop a tick beyond the sweep extreme (C3: beyond the OB).
C5 / C6 = limit trailing at 62% / 70.5% back from the leg high. B1 = price gets within a quarter of an H1 ATR of the pool, then a candle closes down and back under it (within 6 candles): short at its close, stop over that high, target the FVG edge.
B2 / B3 = after that visit of the pool, long again at the FVG edge / OB top, target the high the run made. REF = market at the H1 close, stop a tick beyond the cross bar, target the pool. Shorts mirrored.</p>
<p class="lead"><b>M5</b></p>{results(5)}
<details class="ict"><summary>M2 and M1</summary><p class="lead"><b>M2</b></p>{results(2)}<p class="lead"><b>M1</b></p>{results(1)}</details>
<details class="ict" open><summary>The M5 trades of the four rows on this page</summary>{''.join(row_table(*x) for x in per_row)}</details>
"""

seed_js = ("<script>(function () { const F = location.pathname.split('/').pop(), S = " + json.dumps(seed) + ";\n"
           "Object.keys(S).forEach(k => { try { const dk = 'postool_v1_draw_' + F + '_' + k, ck = 'postool_v1_cursor2_' + F + '_' + k;\n"
           "  const old = JSON.parse(localStorage.getItem(dk) || '[]').filter(d => !String(d.id).startsWith('ict_'));\n"
           "  if (!localStorage.getItem('ict_seeded_' + F + '_' + k)) { localStorage.setItem(dk, JSON.stringify(old.concat(S[k].draws))); localStorage.setItem(ck, JSON.stringify(S[k].cursor));\n"
           "    localStorage.setItem('ict_seeded_' + F + '_' + k, '1'); } } catch (e) {} }); })();</script>\n")
label_js = """        ctx.setLineDash([]);
        if (d.label) {
          ctx.font = '11px Courier New, monospace'; ctx.fillStyle = d.color; ctx.textBaseline = 'bottom';
          if (d.type === 'rect') {
            const xl = Math.min(g.x0, g.x1), xr = Math.max(g.x0, g.x1), yt = Math.min(g.y0, g.y1), yb = Math.max(g.y0, g.y1), la = d.la || 'tl';
            if (la === 'l') { ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(d.label, xl - 4, yt); }
            else if (la === 'tr') { ctx.textAlign = 'right'; ctx.fillText(d.label, xr - 3, yt - 2); }
            else if (la === 'br') { ctx.textAlign = 'right'; ctx.textBaseline = 'top'; ctx.fillText(d.label, xr - 3, yb + 2); }
            else { ctx.textAlign = 'left'; ctx.fillText(d.label, xl + 3, yt - 2); }
          }
          else { ctx.textAlign = 'right'; ctx.fillText(d.label, W - 4, g.y - 2); }
        }
"""
anchor = "        ctx.setLineDash([]);\n        if (this.edit && this.focus(d))"
assert src.count(anchor) == 1
src = src.replace(anchor, label_js + "        if (this.edit && this.focus(d))")
a2 = "const _DEF_TF = { a: 'H1', b: 'M5', n: 'H1' };"
assert a2 in src
src = src.replace(a2, "const _DEF_TF = { a: 'M5', b: 'M1', n: 'M5' };")
a3 = "const _NEAR = 400;"
assert a3 in src
src = src.replace(a3, "const _NEAR = 90; ")
h = "<h1>ES H1 cross bar trades (volume profile + NQ sample)</h1>"
assert h in src
src = src.replace(h, "<h1>ES H1 cross bar trades: ICT marks + strategy check (temp)</h1>" + seed_js + block)
open(OUT, "w", encoding="utf-8").write(src)
print(OUT, len(src))
for row, note, tr in per_row:
    print(row.Index, note)
    if len(tr): print(tr[["strat", "mode", "side", "fill", "entry", "stop", "target", "rr", "out", "R"]].round(2).to_string(index=False))
