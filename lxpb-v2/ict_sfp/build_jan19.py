"""Jan 19 2026 06:00 PT only: M1 detail of the sweep entry, every mark a line with its label stacked on the right."""
import json, re, html
import pandas as pd

SRC, OUT = "jan19_src.html", "ict_jan19.html"
src = open(SRC, encoding="utf-8").read()
key = re.findall(r'data-key="([^"]+)"', src)[0]
U = lambda s: int(pd.Timestamp("2026-01-19 " + s, tz="America/Los_Angeles").timestamp())
C = dict(ob="#ff9800", fvg="#4f8cff", disp="#ff5cb8", pool="#ffffff", stop="#f23645", entry="#00e676", swing="#9aa0ad", m2="#c9a227")
ENTRY, STOP, TGT, ALT = 7072.50, 7067.75, 7090.25, 7070.25
R = (TGT - ENTRY) / (ENTRY - STOP); RA = (TGT - ALT) / (ALT - STOP)


def ray(id, t, p, col, label, dash="solid", width=1):
    return dict(id="ict_" + id, type="ray", t=U(t), p=p, color=col, width=width, dash=dash, label=label)


draws = [
    ray("target", "04:00", TGT, C["pool"], f"TARGET {TGT:.2f} = H1 swing high of 04:00 (hit 08:11:12)", "dashed"),
    ray("m5ob", "06:30", 7077.75, C["m2"], "M5 OB high 7077.75 (leg 06:30-06:44, last candle 40% body)", "dotted"),
    ray("obh", "06:41", 7072.50, C["ob"], "M1 OB high 7072.50 (leg 06:41-06:45; the 06:45 candle alone is 50% body)"),
    ray("entry", "06:49:25", ENTRY, C["entry"], f"ENTRY {ENTRY:.2f} at 06:49:25, buy stop over the M2 OB; target 7090.25 ({R:+.2f} R)", "solid", 2),
    ray("m2ob", "06:42", 7072.25, C["m2"], "M2 OB high 7072.25 (leg 06:42-06:45, last candle 38% body)", "dotted"),
    ray("disp", "06:47", 7071.25, C["disp"], "M1 DISPLACEMENT starts: candle 06:47, close 7071.25 on its high"),
    ray("swing", "01:00", 7070.75, C["swing"], "swept H1 swing low 7070.75 (01:00)", "dashed"),
    ray("fvgt", "06:46", 7070.50, C["fvg"], "M1 FVG top 7070.50 = low of 06:48"),
    ray("fvgb", "06:46", 7069.75, C["fvg"], "M1 FVG bottom 7069.75 = high of 06:46"),
    ray("obl", "06:41", 7068.00, C["ob"], "M1 / M2 OB low 7068.00 = sweep extreme (06:45:50)"),
    ray("stop", "06:45", STOP, C["stop"], f"STOP {STOP:.2f}, a tick under the extreme"),
]
log = [
    ("06:35:00", "M5 candle 06:35 trades under the H1 swing low 7070.75 (the 01:00 bar): the sweep starts"),
    ("06:40:00", "that M5 candle closes back above it at 7071.00: armed. No other untested H1 swing low within 1 x H1 ATR below"),
    ("06:45:50", "the sweep extends to 7068.00 (still inside the 4 M5 candles): this is the extreme; stop 7067.75"),
    ("06:46:00", "OB RULE: the last down-close candle is the OB only if its body is at least 70% of its range; otherwise the OB is the whole leg of lower (or equal) highs that ends with it"),
    ("06:46:00", "M1: the 06:45 candle has a 4-tick body in an 8-tick range (50%): not enough. M1 OB = the leg 06:41-06:45, 7068.00 - 7072.50; buy stop 7072.75"),
    ("06:46:00", "M2: the 06:44 candle is 38% body. M2 OB = the leg 06:42-06:45, 7068.00 - 7072.25; buy stop 7072.50"),
    ("06:46:00", "M5: the 06:40 candle is 40% body. M5 OB = the leg 06:30-06:44, 7069.00 - 7077.75; buy stop 7078.00"),
    ("06:47:00", "M1 DISPLACEMENT: the 06:47 candle closes at 7071.25, on its high; 06:48 pulls back only to 7070.50, above the 06:46 high 7069.75"),
    ("06:49:00", "06:48 closes: M1 FVG 7069.75 - 7070.50 is on the chart (06:46 high to 06:48 low)"),
    ("06:49:25", f"price crosses 7072.50, a tick over the M2 OB, with the displacement and its FVG in place: ENTRY {ENTRY:.2f}, stop {STOP:.2f} (risk {ENTRY - STOP:.2f} pt). The same second trades 7072.75, the M1 OB buy stop"),
    ("06:49:25", f"TARGET = first local H1 swing high above = the 04:00 bar, {TGT:.2f} ({TGT - ENTRY:.2f} pt, planned {R:.2f} R)"),
    ("08:11:12", f"target {TGT:.2f} hit: {R:+.2f} R. Lowest price after the entry: 7072.00, two ticks of heat"),
]

m = re.search(r"^const CHARTS = (\[.*\]);$", src, re.M)
charts = json.loads(m.group(1))
charts[0]["notes"] = ([{"color": "#00e676", "text": "ALGO: OB = 70% body candle or else the whole leg; M1 displacement with FVG, entry over the M2 OB, target the 04:00 H1 swing high"}]
                      + [{"color": "#d1d4dc", "text": f"{t} PT  {s}"} for t, s in log])
src = src[:m.start(1)] + json.dumps(charts) + src[m.end(1):]


def rep(a, b):
    global src
    assert src.count(a) == 1, (src.count(a), a)
    src = src.replace(a, b)


seed = {key: dict(draws=draws, cursor=U("06:54"))}
seed_js = ("<script>(function () { const F = location.pathname.split('/').pop(), S = " + json.dumps(seed) + ";\n"
           "Object.keys(S).forEach(k => { try { const dk = 'postool_v1_draw_' + F + '_' + k, ck = 'postool_v1_cursor2_' + F + '_' + k, sk = 'ict_j19_4_' + F + '_' + k;\n"
           "  const old = JSON.parse(localStorage.getItem(dk) || '[]').filter(d => !String(d.id).startsWith('ict_'));\n"
           "  if (!localStorage.getItem(sk)) { localStorage.setItem(dk, JSON.stringify(old.concat(S[k].draws))); localStorage.setItem(ck, JSON.stringify(S[k].cursor));\n"
           "    localStorage.setItem(sk, '1'); } } catch (e) {} }); })();</script>\n")
rep("      getDraws(this.row).forEach(d => {\n        const g = this.dGeom(d);", "      const LBL = [];\n      getDraws(this.row).forEach(d => {\n        const g = this.dGeom(d);")
rep("        ctx.setLineDash([]);\n        if (this.edit && this.focus(d))",
    "        ctx.setLineDash([]);\n        if (d.label && d.type !== 'rect') LBL.push({ y: g.y, text: d.label, color: d.color });\n        if (this.edit && this.focus(d))")
rep("        });\n      });\n    }\n\n    draw(ctx) {", """        });
      });
      if (LBL.length) {                                  // labels on the right, pushed apart so none overlaps; a leader joins each to its line
        const H = 16, hC = ctx.canvas.clientHeight || 600;
        LBL.sort((a, b) => a.y - b.y);
        LBL.forEach(o => { o.ly = o.y; });
        for (let n = 0; n < 60; n++) {
          let moved = false;
          for (let i = 1; i < LBL.length; i++) {
            const gap = LBL[i].ly - LBL[i - 1].ly;
            if (gap < H - 0.01) { const sft = (H - gap) / 2; LBL[i - 1].ly -= sft; LBL[i].ly += sft; moved = true; }
          }
          if (!moved) break;
        }
        ctx.font = "12px Courier New, monospace"; ctx.textBaseline = "middle"; ctx.textAlign = 'right';
        LBL.forEach(o => {
          if (o.y < -20 || o.y > hC + 20) return;
          const w = ctx.measureText(o.text).width, xr = W - 4, xl = xr - w - 4;
          ctx.fillStyle = 'rgba(19, 23, 34, 0.88)'; ctx.fillRect(xl, o.ly - H / 2, w + 8, H);
          ctx.strokeStyle = o.color; ctx.lineWidth = 1; ctx.setLineDash([]);
          ctx.beginPath(); ctx.moveTo(xl - 22, Math.round(o.y) + 0.5); ctx.lineTo(xl - 6, o.ly); ctx.lineTo(xl, o.ly); ctx.stroke();
          ctx.fillStyle = o.color; ctx.fillText(o.text, xr, o.ly);
        });
      }
    }

    draw(ctx) {""")
rep("const _DEF_TF = { a: 'H1', b: 'M5' };", "const _DEF_TF = { a: 'M1', b: 'M5' };")
rep("const _NEAR = 400;", "const _NEAR = 24;")
steps = "".join(f"<li><span class='src-tag'>{t} PT</span> {html.escape(s)}</li>" for t, s in log)
block = f"""
<style>ol.ict {{ margin:0 0 12px 8px; font-size:0.86em; color:var(--text-dim); }} ol.ict li {{ margin:2px 0; }}</style>
<p class="lead"><b>Mon 2026-01-19 06:00 PT, long: the sweep of the H1 swing low 7070.75, on M1 (temp file).</b> Every mark is a line that starts at its candle,
with its label on the right. The chart opens on M1 stopped at 06:54 PT; step the replay forward to see the target hit at 08:11 PT.</p>
<ol class="ict">{steps}</ol>
<p class="lead"><b>Mitigation:</b> with the OB now the whole leg, price is inside the OB from the low until the entry, so there is no separate return into it to mark. After the entry the lowest price is 7072.00, back inside the top of the M1 and M2 OB by two ticks.</p>
"""
h = re.search(r"<h1>.*?</h1>", src).group(0)
rep(h, "<h1>Jan 19 sweep: M1 OB, displacement, FVG, entry and target (temp)</h1>" + seed_js + block)
open(OUT, "w", encoding="utf-8").write(src)
print(OUT, len(src), key)
