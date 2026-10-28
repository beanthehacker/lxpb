"""Swing-only copy of the y2026_fade_tr page: no regen. Keeps the swing-P0 entry rows (and their charts) exactly as
the engine report rendered them, drops every other row and its chart data, adds a 'Swing' column with the
features from features.py (hover = all of them, data-* attributes for filters), a 'Swing features' filter row
and a findings block at the top."""
import json, os, re, sys
import pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
SRC, OUT = sys.argv[1], sys.argv[2]
s = open(SRC, encoding="utf-8").read()
feat = {int(r["idx"]): r for r in json.load(open(os.path.join(D, "features.json")))}

# ---- rows: keep swing rows + their chart rows -------------------------------------------------------------------
body_i, body_j = s.index("<tbody>") + len("<tbody>"), s.index("</tbody>")
body = s[body_i:body_j]
parts = re.split(r'(?=<tr class="lvl-row)', body)
keep, kept = [parts[0]], []


def cell(f):
    t = (f"Entry level {f['p0_kind']}, P0 {f['p0_pt']} PT. "
         f"Swing: the extreme for {f['left_bars']} bar(s) before the P0 and {f['right_bars']} after it (to P1); "
         f"depth before {f['depth_left_pts']:.2f} pt ({f['depth_left_atr']:.2f} ATR), pullback before P1 "
         f"{f['depth_right_pts']:.2f} pt ({f['depth_right_atr']:.2f} ATR). ER at breakout {f['er']}. "
         f"Same-side M1 fractals (2 bars each side) in the 24 h before the touch: {f['frac_same_1pt']} within 1 pt, "
         f"{f['frac_same_2pt']} within 2 pt; opposite-side within 2 pt: {f['frac_opp_2pt']}. "
         f"M1 bars that traded within 0.5 pt of the price in the 24 h before the P0: {f['pre_tests']}. "
         f"P0 candle {f['p0_range_atr']:.2f} ATR, rejection wick {f['wick']} pt. P1 candle {f['p1_range_atr']:.2f} ATR, body "
         f"{f['p1_body']:.0%}, closed {f['p1_close_beyond']:.2f} pt past. P1 to touch {f['break_to_touch_min']} min; price ran "
         f"{f['ext_after_p1_atr']:.1f} ATR past the level after P1 and took {f['return_min']} min back. M1 ATR(21) {f['atr']:.2f}.")
    good = lambda ok: ' class="sw-good"' if ok else ' class="sw-bad"'
    return (f'<td class="left swing-cell" title="{t}">'
            f'<span{good(f["frac_same_2pt"] <= 6)}>Fr {f["frac_same_2pt"]}</span><br>'
            f'<span{good(f["p0_range_atr"] <= 1.1)}>P0 {f["p0_range_atr"]:.2f}A</span><br>'
            f'<span{good(f["depth_right_atr"] <= 1.45)}>PB {f["depth_right_atr"]:.2f}A</span><br>'
            f'<span class="sw-dim">L{f["left_bars"]}/R{f["right_bars"]} D{f["depth_left_atr"]:.1f}A</span></td>')


for p in parts[1:]:
    if 'data-lvl-swing="1"' not in p.split(">", 1)[0]:
        continue
    idx = int(re.search(r'data-idx="(\d+)"', p).group(1))
    f = feat[idx]
    attrs = (f' data-sw-frac="{f["frac_same_2pt"]}" data-sw-p0="{f["p0_range_atr"]}" data-sw-pb="{f["depth_right_atr"]}"'
             f' data-sw-hour="{f["hour_pt"]:.2f}"')
    p = p.replace(f'onclick="toggleChart({idx})">', f'{attrs}\n    onclick="toggleChart({idx})">', 1)
    tc = re.search(r'  <td class="left type-cell">[^<]*</td>\n', p).group(0)
    p = p.replace(tc, tc + "  " + cell(f) + "\n", 1)
    p = p.replace('colspan="25"', 'colspan="26"')
    keep.append(p)
    kept.append(idx)
s = s[:body_i] + "".join(keep) + s[body_j:]
s = s.replace('<th class="left">Type</th>', '<th class="left">Type</th><th class="left" title="Swing-level features (hover a cell for all of them): Fr = '
              'same-side M1 fractals within 2 pt of the level in the 24 h before the touch; P0 = the P0 candle\'s range in '
              'M1 ATR(21); PB = how far price pulled back from the swing before P1, in ATR; L/R = bars the level stayed the '
              'extreme before / after the P0; D = swing depth before the P0, ATR. Green = the side that won more in Jan-May '
              '(see the findings above), red = the other.">Swing</th>', 1)
# the new column sits after Type (column 2): every column number after it moves up by one
assert s.count("const SORT_COLS = [0, 10, 14, 17, 18, 19];") == 1
s = s.replace("const SORT_COLS = [0, 10, 14, 17, 18, 19];", "const SORT_COLS = [0, 11, 15, 18, 19, 20];", 1)
for n in (20, 19, 18):
    assert s.count(f"#lvl-table thead th:nth-child({n})") == 1
    s = s.replace(f"#lvl-table thead th:nth-child({n})", f"#lvl-table thead th:nth-child({n + 1})", 1)

# ---- charts: keep only the kept trades' data ----------------------------------------------------------------------
m = re.search(r"const CHARTS = (\[.*?\]);\n", s, re.S)
charts = json.loads(m.group(1))
charts = [c if i in kept else None for i, c in enumerate(charts)]
s = s[:m.start(1)] + json.dumps(charts) + s[m.end(1):]

# ---- swing feature filters ----------------------------------------------------------------------------------------
filt = """  <div class="filter-row">
    <span class="filter-label" title="Live filters on the swing features in the Swing column. Each: any, at most, or more than
the value. Stats recompute from the rows left.">Swing features</span>
    <label class="chip">Fractals within 2 pt <select class="f-sw" data-attr="swFrac"><option value="any">any</option><option value="le">&le;</option><option value="gt">&gt;</option></select>
      <input type="number" class="f-sw-val" data-attr="swFrac" value="6" step="1" style="width:3.5em"></label>
    <label class="chip">P0 candle ATR <select class="f-sw" data-attr="swP0"><option value="any">any</option><option value="le">&le;</option><option value="gt">&gt;</option></select>
      <input type="number" class="f-sw-val" data-attr="swP0" value="1.1" step="0.1" style="width:3.5em"></label>
    <label class="chip">Pullback before P1 ATR <select class="f-sw" data-attr="swPb"><option value="any">any</option><option value="le">&le;</option><option value="gt">&gt;</option></select>
      <input type="number" class="f-sw-val" data-attr="swPb" value="1.45" step="0.05" style="width:3.5em"></label>
    <label class="chip">Touch hour PT <select class="f-sw" data-attr="swHour"><option value="any">any</option><option value="le">&le;</option><option value="gt">&gt;</option></select>
      <input type="number" class="f-sw-val" data-attr="swHour" value="5.5" step="0.5" style="width:3.5em"></label>
    <label class="chip">Combine <select id="f-sw-combine"><option value="and">ALL (and)</option><option value="or">ANY (or)</option></select></label>
  </div>
"""
a = '  <div class="filter-row">\n    <span class="filter-label" title="Live filters on the ENTRY level'
assert s.count(a) == 1
s = s.replace(a, filt + a, 1)
js_fn = """function swingOk(tr) {
  const conds = [];
  document.querySelectorAll('.f-sw').forEach(sel => {
    if (sel.value === 'any') return;
    const a = sel.dataset.attr;
    const inp = document.querySelector('.f-sw-val[data-attr="' + a + '"]');
    const lim = parseFloat(inp && inp.value), v = parseFloat(tr.dataset[a]);
    if (isNaN(lim) || isNaN(v)) return;
    conds.push(sel.value === 'le' ? v <= lim : v > lim);
  });
  if (!conds.length) return true;
  const comb = (document.getElementById('f-sw-combine') || {}).value;
  return comb === 'or' ? conds.some(Boolean) : conds.every(Boolean);
}
function recomputeDynStats() {"""
assert s.count("function recomputeDynStats() {") == 1
s = s.replace("function recomputeDynStats() {", js_fn, 1)
assert s.count("!entryLevelOk(tr) ||") == 1
s = s.replace("!entryLevelOk(tr) ||", "!entryLevelOk(tr) || !swingOk(tr) ||", 1)
hook = "document.querySelectorAll('.f-lvl, #f-lvl-combine')"
s = s.replace(hook, "document.querySelectorAll('.f-sw, #f-sw-combine').forEach(el => el.addEventListener('change', recomputeDynStats));\n"
              "document.querySelectorAll('.f-sw-val').forEach(el => el.addEventListener('input', recomputeDynStats));\n" + hook, 1)
s = s.replace("</style>", ".swing-cell{font-size:11px;line-height:1.25;white-space:nowrap}.sw-good{color:#4ade80}.sw-bad{color:#f87171}"
              ".sw-dim{color:#8b93a1}\n.findings{margin:10px 0 16px;padding:10px 14px;border:1px solid #2a2f37;border-radius:8px;"
              "background:#12151a;font-size:13px;line-height:1.45}.findings table{border-collapse:collapse;margin:6px 0}"
              ".findings td,.findings th{padding:2px 10px;border-bottom:1px solid #23262b;text-align:right}"
              ".findings td:first-child,.findings th:first-child{text-align:left}</style>", 1)

# ---- title, summary boxes, findings, stale percentile tab ---------------------------------------------------------
s = s.replace("find_trading_range)</title>", "find_trading_range, SWING-P0 entries only)</title>", 1)
s = s.replace("find_trading_range)</h1>", "find_trading_range, SWING-P0 entries only)</h1>", 1)
s = s.replace("<strong>127</strong>fade trades (engine)", f"<strong>{len(kept)}</strong>swing-P0 fade trades (of 127)", 1)
s = s.replace('<strong id="sum-shown">127</strong>', f'<strong id="sum-shown">{len(kept)}</strong>', 1)
s = re.sub(r"<strong>\d+</strong>/127 entry improved over own level", "", s, count=1)
fnd = open(os.path.join(D, "findings.html"), encoding="utf-8").read()
s = s.replace('<div class="tab-panel" id="tab-trades">', '<div class="tab-panel" id="tab-trades">\n' + fnd, 1)
k = s.index('<div class="tab-panel tab-hidden" id="tab-pctile">')
e = s.index('<script', k)
NOTE = ('<div class="tab-panel tab-hidden" id="tab-pctile">\n<p class="lead">The excursion percentiles were '
        'computed over all 127 trades of the year report and are not recomputed for the swing-only rows; see '
        'y2026_fade_tr.</p>\n</div>\n')
s = s[:k] + NOTE + s[e:]
open(OUT, "w", encoding="utf-8").write(s)
print(len(kept), "swing rows kept ->", OUT)
