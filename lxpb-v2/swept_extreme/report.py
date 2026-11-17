"""SWEPT EXTREME report: one page per entry technique of scan.py (T1 / T2a / T2b / T3), each listing EVERY cross bar as an
opportunity, with the cross bar trades report's charts (cross_bar/render_cross_bar_trades.py: ES and NQ side by side, timeframe
switch, forward replay, position tool, drawings, volume profile; opens on M5 at the end of the trade). The pages link to each
other through a tab strip and share one data folder (<output stem>_data, the cross bar trades report's per-row files, each row's
window running at least to the end of its trades).

A row with no trade says why; "target already reached" = the cross bar's extreme traded again before the entry (no entry).
The technique's marks are drawn on the ES charts as the replay time passes them.

    python swept_extreme/report.py --all --vp --output <dir>/swept_extreme_2026.html      # writes ..._t1.html, _t2a, _t2b, _t3
    python swept_extreme/report.py --at "2026-03-16 12:00,2026-07-01 17:00" --output <file>.html
"""
import os, sys, json, argparse, copy
import pandas as pd
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import scan as S
CT, CB, PT = S.CT, S.CB, S.PT
GREEN, RED, BLUE, ORANGE, CYAN, PURPLE, AMBER, GREY = "#4ade80", "#f87171", "#60a5fa", "#fb923c", "#22d3ee", "#c084fc", "#fbbf24", "#9ca3af"

TECH = {
    "T1": ("1. Reclaim thrust, market", "Reclaim thrust: market at the M5 close",
           "After the cross bar has closed, wait for the first M5 thrust candle (range at least 1.5 x the M5 ATR(14), body at least "
           "half its range) that opens on the far side of the original order block's edge and closes back through it: above the "
           "order block's LOW for a long, below its HIGH for a short. Enter at market on that close (the first second after it). "
           "Stop: one tick beyond the thrust candle. Target: the cross bar's extreme."),
    "T2a": ("2a. Pullback into the gap", "Pullback into the gap the thrust leaves",
            "The same thrust candle as in 1, but no market order. When the candle after the thrust has closed and a gap is left "
            "between the candle before the thrust and the candle after it (a fair value gap), a limit rests at the near edge of "
            "that gap. Stop: one tick beyond the thrust candle. Target: the cross bar's extreme."),
    "T2b": ("2b. Pullback to the LXPB level", "Pullback to the LXPB level the thrust broke out of",
            "The same thrust candle as in 1. When the thrust is also the P1 (breakout candle) of an M5 LXPB level (LHPB for a long, "
            "LLPB for a short), a limit rests at that level: the entry is the level's first retest. Stop: one tick beyond the thrust "
            "candle. Target: the cross bar's extreme."),
    "T3": ("3. Stop-entry over the swing", "Stop-entry over the M5 swing made at the order block or a gap",
           "On the leg away from the extreme, price bounces back into the original order block, or into a gap that leg left, and "
           "prints its first M5 swing there (a 2-bar fractal: a lower high for a long, a higher low for a short). Once the swing is "
           "confirmed, a stop-entry rests one tick beyond it. Stop: one tick beyond the latest confirmed M5 swing on the other side "
           "before the trigger (an assumption; no stop was given). Target: the cross bar's extreme."),
}
STATUS = (("target", "Target", GREEN), ("stop", "Stopped", RED), ("open", "Open", BLUE),
          ("reached", "No entry: target already reached", AMBER), ("none", "No entry: other", GREY))


def _u(ts):
    return pd.Timestamp(int(ts), unit="s", tz="UTC")


def status_of(rec):
    if rec["res"] != "none": return rec["res"]
    return "reached" if rec["reached"] or "target reached first" in rec["why"] else "none"


def marks(base, rec, tech):
    """The technique's drawing for one row: marks and rays in time and price (the page shows each once the replay passes it)."""
    long = not base["bull"]
    ext, edge = ("high", "low") if long else ("low", "high")
    above, below = ("aboveBar", "belowBar") if long else ("belowBar", "aboveBar")
    X = base["X"]
    traded = rec["res"] != "none"
    t_stop = rec.get("t_fill") or rec.get("t_dead") or int(base["dead"])            # where the setup's own lines end
    t_end = rec["t_exit"] if rec["res"] in ("target", "stop") else (rec.get("t_dead") or (int(base["t_gone"]) if base["t_gone"] else None)) if not traded else None
    mk = lambda t, pos, color, text, shape="circle", size=1: {"t": int(t), "pos": pos, "color": color, "shape": shape, "text": text, "size": size}
    ray = lambda t0, t1, px, color, title, label, style=0, width=1, tag=True: {
        "t0": int(t0), "t1": None if t1 is None else int(t1), "px": float(px), "color": color, "title": title, "label": label,
        "style": style, "width": width, "tag": tag}
    M = [mk(base["t_sweep"], above, AMBER, f"extreme {X:.2f}")]
    R = [ray(base["t_sweep"], t_end, X, GREEN, "target", f"TARGET: cross bar {ext} {X:.2f}", style=2, width=2)]
    N = [{"color": GREEN, "text": f"{'LONG' if long else 'SHORT'} idea, target = cross bar {ext} {X:.2f}"}]
    if "ob_lo" in base:
        M.append(mk(base["ob_t0"], below, ORANGE, "OB"))
        R += [ray(base["ob_t0"], t_stop, base["ob_lo"], ORANGE, f"OB {edge}", f"order block {edge} {base['ob_lo']:.2f}: the edge the thrust must close through"),
              ray(base["ob_t0"], t_stop, base["ob_hi"], ORANGE, "", f"order block far edge {base['ob_hi']:.2f}", style=1, tag=False)]
        N.append({"color": ORANGE, "text": f"order block {min(base['ob_lo'], base['ob_hi']):.2f} - {max(base['ob_lo'], base['ob_hi']):.2f}"})
    if "t_thrust" in rec:
        M.append(mk(rec["t_thrust"], below, CYAN, "thrust"))
        N.append({"color": CYAN, "text": f"thrust candle {CB._pt(_u(rec['t_thrust']))} closes through the OB {edge}"})
    if "fvg_hi" in rec:
        R += [ray(rec["t_fvg"], t_stop, rec["fvg_hi"], CYAN, "gap edge", f"gap (FVG) near edge {rec['fvg_hi']:.2f}: the limit"),
              ray(rec["t_fvg"], t_stop, rec["fvg_lo"], CYAN, "", f"gap (FVG) far edge {rec['fvg_lo']:.2f}", style=1, tag=False)]
        N.append({"color": CYAN, "text": f"gap left by the thrust {min(rec['fvg_lo'], rec['fvg_hi']):.2f} - {max(rec['fvg_lo'], rec['fvg_hi']):.2f}, limit at {rec['fvg_hi']:.2f}"})
    if "lvl" in rec:
        name = "LHPB" if long else "LLPB"
        M.append(mk(rec["t_p0"], above, PURPLE, "P0"))
        R.append(ray(rec["t_p0"], t_stop, rec["lvl"], PURPLE, "LXPB", f"M5 {name} {rec['lvl']:.2f} ({rec['p0_kind']}): P1 = the thrust candle, limit at its first retest"))
        N.append({"color": PURPLE, "text": f"M5 {name} {rec['lvl']:.2f}: P1 is the thrust, limit at its first retest"})
    if "sh" in rec:
        M.append(mk(rec["t_sh"], above, CYAN, f"swing {ext} {rec['sh']:.2f}"))
        R.append(ray(rec["t_sh"], t_stop, rec["sh"], CYAN, "stop-entry", f"M5 swing {ext} {rec['sh']:.2f} made at the {rec['zone']}: stop-entry one tick beyond"))
        if rec["zone"] == "gap":
            R += [ray(rec["t_zone"], rec["t_sh"], rec["z_lo"], PURPLE, "gap", f"gap of the leg away from the extreme, near edge {rec['z_lo']:.2f}"),
                  ray(rec["t_zone"], rec["t_sh"], rec["z_hi"], PURPLE, "", f"gap far edge {rec['z_hi']:.2f}", style=1, tag=False)]
        N.append({"color": CYAN, "text": f"M5 swing {ext} {rec['sh']:.2f} at the {rec['zone']} ({min(rec['z_lo'], rec['z_hi']):.2f} - {max(rec['z_lo'], rec['z_hi']):.2f}): stop-entry one tick beyond"})
    if "sl" in rec:
        M.append(mk(rec["t_sl"], below, RED, f"swing {edge} (stop)"))
    if traded:
        side = "long" if long else "short"
        M.append(mk(rec["t_fill"], below, BLUE, f"{side} {rec['entry']:.2f}", "arrowUp" if long else "arrowDown", 2))
        t1 = rec["t_exit"] if rec["res"] != "open" else None
        R += [ray(rec["t_fill"], t1, rec["entry"], BLUE, "entry", f"entry {rec['entry']:.2f}, {CB._pt(_u(rec['t_fill']))}"),
              ray(rec["t_fill"], t1, rec["stop"], RED, "stop", f"stop {rec['stop']:.2f} (risk {rec['risk']:.2f} pt)")]
        N.append({"color": BLUE, "text": f"{side} {rec['entry']:.2f} at {CB._pt(_u(rec['t_fill']))}, stop {rec['stop']:.2f}, risk {rec['risk']:.2f} pt, target {rec['rr']:.2f}R away"})
        if rec["res"] != "open":
            won = rec["res"] == "target"
            M.append(mk(rec["t_exit"], above if won else below, GREEN if won else RED, f"target {rec['R']:+.2f}R" if won else f"stop {rec['R']:+.2f}R", "square"))
            N.append({"color": GREEN if won else RED, "text": f"{'target' if won else 'stop'} at {CB._pt(_u(rec['t_exit']))}: {rec['R']:+.2f}R"})
        else:
            N.append({"color": BLUE, "text": "still open at the end of the data"})
    else:
        gone = rec.get("t_dead") or base["t_gone"]
        if status_of(rec) == "reached" and gone:
            M.append(mk(gone, above, AMBER, "target reached: no entry", "square"))
        N.append({"color": AMBER if status_of(rec) == "reached" else GREY, "text": "NO ENTRY: " + rec["why"]})
    return {"marks": M, "rays": R, "notes": N}


def open_time(base, rec):
    """Where the row's replay opens: half an hour past the end of the trade (or of the opportunity)."""
    t = rec["t_exit"] if rec["res"] in ("target", "stop") else rec.get("t_dead") or base["t_gone"] or base["dead"]
    return (int(t) // 300 + 7) * 300


def until_min(res):
    """Each row's replay window must reach the end of every technique's trade (+ 2 h)."""
    out = {}
    for t, r in res.items():
        ends = [open_time(r["base"], r[k]) for k in S.TECHS] + [r[k]["t_exit"] for k in S.TECHS if r[k]["res"] == "open"]
        out[t] = _u(max(ends) + 7200).tz_convert(PT)
    return out


# The technique's marks on the ES charts, shown once the replay time has passed them; a pane is rebuilt when that set changes.
EXTRA_JS = r"""
function _stratStage(i, T) {
  const s = CHARTS[i].strat;
  let n = 0;
  s.marks.forEach(m => { if (m.t <= T) n++; });
  s.rays.forEach(r => { if (r.t0 <= T) n++; if (r.t1 != null && r.t1 <= T) n++; });
  return n;
}
const _paneCd0 = _paneCd;
_paneCd = function (i, pane) {
  const cd = _paneCd0(i, pane), s = CHARTS[i].strat;
  if (_isN(pane) || !s) return cd;
  const T = _Tof(i, pane), tf = _rp(i).tf[pane], times = cd.candles.map(c => c.time), last = times[times.length - 1];
  const snap = (t) => times[Math.max(0, _lb(times, _bucket(t, tf) + 1) - 1)];
  s.marks.forEach(m => {
    if (m.t <= T && m.t >= times[0]) cd.markers.push({ time: snap(m.t), position: m.pos, color: m.color, shape: m.shape, text: m.text, size: m.size });
  });
  cd.markers.sort((a, b) => a.time - b.time);
  s.rays.forEach(r => {
    if (r.t0 > T) return;
    const a = snap(Math.max(r.t0, times[0])), b = r.t1 == null || r.t1 > T ? last : snap(Math.max(r.t1, times[0]));
    cd.rays.push({ color: r.color, lineWidth: r.width, lineStyle: r.style, title: r.title, priceLabel: r.tag, label: r.label,
                   points: a < b ? [{ time: a, value: r.px }, { time: b, value: r.px }] : [{ time: b, value: r.px }] });
  });
  cd.notes = (cd.notes || []).concat(s.notes);
  cd.stage = _stratStage(i, T);
  return cd;
};
const _apply0 = _apply;
_apply = function (i) {
  const st = _rp(i);
  Object.keys(st.panes).forEach(pane => {
    const p = st.panes[pane];
    if (_isN(pane) || p.cd.stage == null || p.cd.stage === _stratStage(i, _Tof(i, pane))) return;
    const vr = p.chart.timeScale().getVisibleLogicalRange(), n0 = (p.cd.live || p.cd.candles).length;
    _renderTf(i, pane);
    const q = st.panes[pane], d = q.cd.candles.length - n0;
    if (vr) q.chart.timeScale().setVisibleLogicalRange(vr.to >= n0 - 1.5 ? { from: vr.from + d, to: vr.to + d } : vr);
  });
  _apply0(i);
};
// status filter (kept apart from the review filters)
function _statusFilter() {
  const on = {};
  document.querySelectorAll('.f-status').forEach(cb => { on[cb.value] = cb.checked; });
  let n = 0;
  document.querySelectorAll('tr.lvl-row').forEach(tr => {
    const hide = !on[tr.dataset.status];
    tr.classList.toggle('st-hide', hide);
    const cr = document.getElementById('chart-row-' + tr.dataset.idx);
    if (cr) cr.classList.toggle('st-hide', hide);
    if (!hide) n++;
  });
  const e = document.getElementById('st-shown');
  if (e) e.textContent = n;
}
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.f-status').forEach(cb => cb.addEventListener('change', _statusFilter));
});
"""

PAGE_CSS = """<style>
.tabs { display: flex; flex-wrap: wrap; gap: 4px; margin: 10px 0 14px; border-bottom: 1px solid var(--border); }
.tabs a { padding: 7px 14px; border: 1px solid var(--border); border-bottom: none; border-radius: 6px 6px 0 0; color: var(--text);
  text-decoration: none; background: var(--surface); font-size: 14px; }
.tabs a.on { background: #2962ff; color: #fff; font-weight: bold; }
tr.st-hide { display: none !important; }
/* the manual trade columns and boxes of the cross bar trades page stay in the page (its code fills them) but are not shown */
.man-th, td.man-entry, td.man-plan, td.man-exit, td.man-r, td.man-usd, .man-box { display: none; }
td.st-cell { white-space: nowrap; }
</style>"""

HEAD_HTML = (
    "<th class=\"left\">#</th><th class=\"left\">Cross bar (H1 start)</th><th class=\"left\">Cross bar</th><th class=\"left\">Trade</th>"
    "<th title=\"The cross bar's extreme: its high for a long, its low for a short\">Target</th>"
    "<th class=\"left\">Result</th><th class=\"left\" title=\"The candle or swing the entry comes from\">Signal</th>"
    "<th class=\"left\">Entry</th><th>Stop</th><th title=\"Entry to target over entry to stop\">Target away</th><th class=\"left\">Exit</th><th>R</th>"
    "<th class=\"left man-th\"></th><th class=\"man-th\"></th><th class=\"left man-th\"></th><th class=\"man-th\"></th><th class=\"man-th\"></th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">▶</th>")
N_COLS = 21


def _row_html(i, row, base, rec):
    long = not row.bullish
    st = status_of(rec)
    tag = lambda s: f'<br><span class="src-tag">{s}</span>'
    if st == "target": result = f'<strong style="color:{GREEN}">target {rec["R"]:+.2f}R</strong>'
    elif st == "stop": result = f'<strong style="color:{RED}">stopped {rec["R"]:+.2f}R</strong>'
    elif st == "open": result = f'<em style="color:{BLUE}">open {rec["R"]:+.2f}R</em>'
    else: result = f'<span style="color:{AMBER if st == "reached" else GREY}">no entry</span>' + tag(rec["why"])
    if "t_thrust" in rec: signal = "thrust candle" + tag(CB._pt(_u(rec["t_thrust"])))
    elif "sh" in rec: signal = f"swing {rec['sh']:.2f} at the {rec['zone']}" + tag(CB._pt(_u(rec["t_sh"])))
    else: signal = '<span class="src-tag">-</span>'
    if rec["res"] != "none":
        entry = f"{rec['entry']:.2f}" + tag(_u(rec["t_fill"]).tz_convert(PT).strftime("%a %m-%d %H:%M:%S PT"))
        stop = f"{rec['stop']:.2f}" + tag(f"{rec['risk']:.2f} pt")
        away = f"{rec['rr']:.2f}R"
        ex = (f"{rec['exit']:.2f}" + tag(_u(rec["t_exit"]).tz_convert(PT).strftime("%a %m-%d %H:%M:%S PT"))) if rec["res"] != "open" else '<span class="src-tag">open</span>'
        r = f'<strong style="color:{GREEN if rec["R"] > 0 else RED}">{rec["R"]:+.2f}</strong>'
    else:
        entry = stop = away = ex = r = ""
    return f"""
<tr class="lvl-row is-valid {'type-lhpb' if long else 'type-llpb'}" data-idx="{i}" data-key="{CT._row_key(row)}" data-status="{st}" onclick="toggleChart({i})" style="cursor:pointer">
  <td class="left">{i + 1}</td>
  <td class="left">{CB._pt(row.Index)}</td>
  <td class="left">{'bullish' if row.bullish else 'bearish'}{tag('swept ' + f'{row.swing_price:.2f}')}</td>
  <td class="left" style="color:{CB.BULL if long else CB.BEAR}">{'Long' if long else 'Short'}</td>
  <td>{base['X']:.2f}</td>
  <td class="left st-cell">{result}</td><td class="left">{signal}</td>
  <td class="left">{entry}</td><td>{stop}</td><td>{away}</td><td class="left">{ex}</td><td>{r}</td>
  <td class="left man-entry"></td><td class="man-plan"></td><td class="left man-exit"></td><td class="man-r"></td><td class="man-usd"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">▶</button></td>
</tr>""" + CT._row_html(i, row).split("</tr>", 1)[1].replace(f'colspan="{CT.N_COLS}"', f'colspan="{N_COLS}"')


def page(tech, rows, res, charts, stem, data_rel, label, vp):
    tab, name, rule = TECH[tech]
    cs, trs, count = [], [], {k: 0 for k, _, _ in STATUS}
    for i, row in enumerate(rows.itertuples()):
        base, rec = res[row.Index]["base"], res[row.Index][tech]
        c = copy.copy(charts[i])
        c["strat"], c["openT"] = marks(base, rec, tech), open_time(base, rec)
        cs.append(c)
        trs.append(_row_html(i, row, base, rec))
        count[status_of(rec)] += 1
    done = [res[t][tech] for t in rows.index if res[t][tech]["res"] in ("target", "stop")]
    tot = sum(x["R"] for x in done)
    title = f"Swept extreme: {name}" + (f" ({label})" if label else "")
    key = "lxpb_swept_extreme_v1_" + tech.lower() + "_" + "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-")
    js = CT.page_js(cs, key, data_rel, vp, EXTRA_JS)
    for old, new in (("const _DEF_TF = { a: 'H1', b: 'M5', n: 'H1' };", "const _DEF_TF = { a: 'M5', b: 'H1', n: 'M5' };"),
                     ("_ls('cursor2_' + _FILE + '_' + _key(i), cd.start)", "_ls('cursor2_' + _FILE + '_' + _key(i), cd.openT || cd.start)")):
        assert js.count(old) == 1, "cross bar trades page JS changed: " + old
        js = js.replace(old, new)
    tabs = "".join(f'<a href="{os.path.basename(stem)}_{k.lower()}.html"{" class=on" if k == tech else ""}>{TECH[k][0]}</a>' for k in TECH)
    chips = "".join(f'<label class="chip"><input type="checkbox" class="f-status" value="{k}" checked> <span style="color:{col}">{nm}</span> ({count[k]})</label>'
                    for k, nm, col in STATUS)
    box = lambda v, t: f'<div class="box"><strong>{v}</strong>{t}</div>'
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{CB.M.CSS}
{CB.NOTES_CSS}
{CT.MANUAL_CSS}
{PAGE_CSS}
</head><body>
<h1>{title}</h1>
<div class="tabs">{tabs}</div>
<p class="lead">Every ES H1 cross bar is one opportunity for a trade AGAINST it, back to the extreme it made while sweeping: after a
BEARISH cross bar (swept a swing high) the trade is LONG and targets the cross bar's high; after a BULLISH one (swept a swing low)
it is SHORT and targets the cross bar's low. Nothing is looked for before the cross bar has closed. Once the target has traded
again there is NO ENTRY ("target already reached"), and a signal is looked for at most 5 days.</p>
<p class="lead"><b>{name}.</b> {rule} <b>Original order block</b>: on M5, the last candle against the leg away from the extreme (an
up candle at a swept high, a down candle at a swept low), or a run of them, with a body of at least 4 ticks, ending at or before
the candle that made the extreme.</p>
<p class="lead">Fills and exits are read from the 1-second bars: the first second reaching the stop or the target after the fill,
the stop when both fall in one second. No time-of-day rule, no costs. Click a row: ES and NQ side by side on M5, opened half an
hour past the end of the trade (or of the opportunity); the timeframe buttons, forward replay (&#9198; goes back to the cross
bar's hour), 2nd chart, position tool, drawings and volume profile work as in the cross bar trades report. The technique's marks
appear as the replay time passes them. Times are PT.</p>
<div class="summary">
  {box(len(rows), "opportunities")}{box(len(done), "trades closed")}{box(count["target"], "reached target")}{box(count["stop"], "stopped")}
  {box(f"{round(100 * count['target'] / len(done))}%" if done else "-", "win rate")}{box(f"{tot:+.1f}R", "total")}{box(f"{tot / len(done):+.2f}R" if done else "-", "average")}
  {box(count["reached"], "no entry: target already reached")}{box(count["none"], "no entry: other")}{box(count["open"], "open")}
  <div class="box man-box"><strong id="man-n">0</strong></div><div class="box man-box"><strong id="man-open">0</strong></div>
  <div class="box man-box"><strong id="man-wait">0</strong></div><div class="box man-box"><strong id="man-win">-</strong></div>
  <div class="box man-box"><strong id="man-avg">-</strong></div><div class="box man-box"><strong id="man-total">-</strong></div>
  <div class="box man-box"><strong id="man-usd">-</strong></div>
  <div class="box"><strong id="sum-shown">{len(rows)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>{CB.TOOLBAR_HTML}
</div>
<div class="filter-panel"><div class="filter-row"><span class="filter-label">Result</span>{chips}</div></div>
{CB.FILTERS_HTML}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{HEAD_HTML}</tr></thead>
<tbody>{"".join(trs)}
</tbody></table></div>
{js}
</body></html>
"""
    out = f"{stem}_{tech.lower()}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved {out}: {len(rows)} rows, {len(done)} trades, {tot:+.1f}R, " + ", ".join(f"{k} {v}" for k, v in count.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--at", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--output", required=True, help="<stem>.html: the pages are <stem>_t1.html ... and the data <stem>_data/")
    ap.add_argument("--vp", action="store_true", help="volume profiles")
    ap.add_argument("--pages-only", action="store_true", help="rewrite the pages, keep the data folder as it is")
    a = ap.parse_args()
    if not (a.all or a.at):
        ap.error("pass --all or --at")
    rows, h1 = S.load_rows(a.since)
    if a.at:
        rows = rows.loc[rows.index.isin([pd.Timestamp(x.strip(), tz=PT) for x in a.at.split(",")])]
    res = S.scan(rows, h1)
    print(S.tally(res).to_string(index=False))
    stem = os.path.splitext(a.output)[0]
    data_dir = stem + "_data"
    if a.pages_only:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:           # the chart list only (its data files are thrown away)
            charts = CT.prepare(rows, h1, tmp, False, until_min(res))
    else:
        charts = CT.prepare(rows, h1, data_dir, a.vp, until_min(res))
    for tech in TECH:
        page(tech, rows, res, charts, stem, os.path.basename(data_dir), a.label or ("sample" if a.at else f"since {a.since}"), a.vp)


if __name__ == "__main__":
    main()
