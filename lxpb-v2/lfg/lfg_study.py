"""
lfg_study.py -- LFG (liquidity flush-n-grab) study page
=======================================================

WIP, PARKED 2026-09-24 -- see README.md in this folder for the status,
the 2026 snapshot and the open questions before building on this.

A mean-reversion trade off an H1 tight range: price flushes out of the range
on M5 and runs into the nearest untested M5 P0 just outside it, and the
retest of that P0 is traded back towards the range.

Rules (user-specified 2026-09-24):

  1. RANGE. Every patterns-pure find_range candidate on the H1 series (inner
     and outer ranges alike, whatever their final status), tradable from the
     close of its confirm candle until the H1 candle that ends it (break,
     cancel or merge) closes. A break by a gap OPEN ends it at that candle's
     open instead. The box edges are the box as it stood at the last H1 close
     -- find_range run on the bars up to that close, never its final box.
  2. FLUSH. An M5 candle trades past a box edge (a wick is enough).
  3. LEVEL. At the flush, the NEAREST M5 P0 beyond that edge that is still
     awaiting its retest, any kind (plain / swing / spike): an LHPB below the
     box low (long), an LLPB above the box high (short). The M5 ledger is read
     with plain-P0s TRACKED, since plain-P0s are entries here.
  4. VOID. If price comes back to the box edge (as it stood at the flush)
     before touching that level, the bounce has already happened: no trade,
     and that side of the range is finished for LFG.
  5. ENTRY. Limit at the level, filled on the first touch (at the bar's open
     if it opened through the level).
  6. STOP. ss_m5_confl2's own stop rule, called, not restated
     (render_m5_confl2_report._dynamic_stop_m5 over the plain-P0-UNTRACKED M5
     ledger, as that report does): one tick beyond a spike-P0 entry level's
     own P0 candle, else one tick beyond the most protective P1 thrust-candle
     extreme among live same-side levels within 10pt of the fill, widened to
     an older P1 if under 3pt.
  7. TARGET (placeholder, to be refined). The most recently formed
     opposite-type M5 P0 still awaiting its retest beyond the fill, as of the
     CLOSE of the entry candle -- so the target is only working from the next
     candle on.
  8. One trade per side per range; a void also finishes that side.

Resolved on M5 bars: a bar touching both stop and target counts as a loss
(flagged). A flush bar that both returns to the edge and touches the level
cannot be ordered on M5 and is flagged ambiguous (no trade, side finished).

Usage:  python lfg/lfg_study.py [--start 2026-01-01] [--out PATH]
"""
import os
import sys
import json
import argparse

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "ss_m5_confl2"))
import render_labels_report as R                 # noqa: E402  (puts patterns_pure on sys.path)
import render_stop_target_report as SR           # noqa: E402
import lxpb_levels_cache as LC                   # noqa: E402
import render_m5_confl2_report as C2             # noqa: E402
from find_range import find_range                # noqa: E402

PT = "America/Los_Angeles"
H1 = pd.Timedelta(hours=1)
M5 = pd.Timedelta(minutes=5)
# find_range is re-run on a trailing window for each point-in-time box.
# Its ATR is a causal Wilder smoothing, so after this many bars the window's
# start no longer moves it; check_boxes() verifies the result anyway.
BOX_WINDOW = 1500
CTX_BEFORE = pd.Timedelta(hours=3)
CTX_AFTER = pd.Timedelta(hours=2)


def pt(ts, fmt="%Y-%m-%d %H:%M"):
    return pd.Timestamp(ts).tz_convert(PT).strftime(fmt)


# --------------------------------------------------------------------------
# Ranges and their point-in-time boxes
# --------------------------------------------------------------------------

def ranges_with_boxes(h, start):
    """Every find_range candidate confirmed on/after `start`, each with the
    box it had going into every H1 candle it is tradable in:
    r["boxes"][j] = (high, low) as of the close of H1 candle j - 1."""
    fr = find_range(h)
    pos = {t: k for k, t in enumerate(h.index)}
    out = []
    for x in fr.itertuples(index=False):
        if x.confirm_time < start:
            continue
        c = pos[x.confirm_time]
        e = pos[x.end_time] if pd.notna(x.end_time) else len(h)
        last = e if (e < len(h) and x.break_how != "gap") else e - 1
        out.append(dict(id=int(x.range_id), start=x.start_time, confirm=x.confirm_time,
                        end=x.end_time, status=x.status, break_dir=x.break_dir,
                        break_how=x.break_how, hi=float(x.high), lo=float(x.low),
                        atr=float(x.atr), outer=(int(x.outer_id) if pd.notna(x.outer_id) else None),
                        c=c, e=e, js=list(range(c + 1, min(last, len(h) - 1) + 1)), boxes={}))
    need = sorted({j - 1 for r in out for j in r["js"]})
    by_start = {r["start"]: r for r in out}
    for k in need:
        w = find_range(h.iloc[max(0, k + 1 - BOX_WINDOW):k + 1])
        w = w[w["status"] == "open"]
        for x in w.itertuples(index=False):
            r = by_start.get(x.start_time)
            if r is not None and k + 1 in r["js"]:
                r["boxes"][k + 1] = (float(x.high), float(x.low))
    for r in out:
        missing = [j for j in r["js"] if j not in r["boxes"]]
        if missing:
            raise RuntimeError(f"range {r['id']}: no point-in-time box going into H1 "
                               f"{[pt(h.index[j]) for j in missing]}")
    check_boxes(h, out)
    return out


def check_boxes(h, ranges):
    """The box going into a broken/cancelled range's end candle must be its
    final box (neither a break nor a cancel widens it) -- a direct check that
    the trailing window reproduced the full-history run."""
    for r in ranges:
        if r["status"] in ("broken", "cancelled") and r["e"] in r["boxes"]:
            if r["boxes"][r["e"]] != (r["hi"], r["lo"]):
                raise RuntimeError(f"range {r['id']}: windowed box {r['boxes'][r['e']]} "
                                   f"!= final box {(r['hi'], r['lo'])}")


# --------------------------------------------------------------------------
# The LFG walk
# --------------------------------------------------------------------------

class Levels:
    """Tracked-plain-P0 M5 ledger, split by type, for point-in-time queries."""

    def __init__(self, ledger):
        d = ledger[ledger["breakout_time"].notna()]
        self.by_type = {t: d[d["type"] == t] for t in ("LHPB", "LLPB")}

    def awaiting(self, level_type, before):
        """P0s of `level_type` awaiting retest at the open of the bar at
        `before`: broken on a completed bar, not yet touched."""
        d = self.by_type[level_type]
        ok = (d["breakout_time"] < before) & (d["death_time"].isna() | (d["death_time"] >= before))
        return d[ok]

    def awaiting_after_close(self, level_type, bar_time):
        """P0s awaiting retest once the bar at `bar_time` has CLOSED."""
        d = self.by_type[level_type]
        ok = (d["breakout_time"] <= bar_time) & (d["death_time"].isna() | (d["death_time"] > bar_time))
        return d[ok]


def walk_side(r, side, h, m5, levels):
    """One side of one range: returns an event dict, or None if price never
    flushed out of that side while the range was tradable."""
    is_long = side == "down"
    for j in r["js"]:
        hi, lo = r["boxes"][j]
        edge = lo if is_long else hi
        t0 = h.index[j]
        bars = m5[(m5.index >= t0) & (m5.index < t0 + H1)]
        for t, b in bars.iterrows():
            crossed = b.low < edge if is_long else b.high > edge
            if not crossed:
                continue
            return flush_from(r, side, edge, (hi, lo), t, h, m5, levels)
    return None


def flush_from(r, side, edge, box, t_flush, h, m5, levels):
    is_long = side == "down"
    lt = "LHPB" if is_long else "LLPB"
    ev = dict(range=r, side=side, edge=edge, box=box, flush_time=t_flush, level=None,
              kind=None, fill_time=None, fill=None, void_time=None, tags=[])
    cand = levels.awaiting(lt, t_flush)
    cand = cand[cand["price"] < edge] if is_long else cand[cand["price"] > edge]
    if cand.empty:
        ev["kind"] = "no level"
        return ev
    near = cand["price"].max() if is_long else cand["price"].min()
    row = cand[cand["price"] == near].sort_values("formation_time").iloc[-1]
    ev["level"] = row
    lvl = float(row["price"])
    end_t = h.index[r["js"][-1]] + H1   # range stops being tradable here
    after = m5[(m5.index >= t_flush) & (m5.index < end_t)]
    for t, b in after.iterrows():
        touched = b.low <= lvl if is_long else b.high >= lvl
        if t == t_flush:
            back = (b.close >= edge) if is_long else (b.close <= edge)
            if touched:
                return fill(ev, t, b, lvl, is_long)
            if back:
                ev.update(kind="void", void_time=t)
                return ev
            continue
        back = (b.high >= edge) if is_long else (b.low <= edge)
        if touched and back:
            ev.update(kind="ambiguous", void_time=t)
            return ev
        if back:
            ev.update(kind="void", void_time=t)
            return ev
        if touched:
            return fill(ev, t, b, lvl, is_long)
    ev["kind"] = "range ended"
    return ev


def fill(ev, t, b, lvl, is_long):
    gapped = (b.open < lvl) if is_long else (b.open > lvl)
    ev.update(kind="trade", fill_time=t, fill=float(b.open) if gapped else lvl)
    if gapped:
        ev["tags"].append("opened through level")
    return ev


def manage(ev, m5, levels, ledger_u):
    """Stop, target and outcome for a filled event."""
    is_long = ev["side"] == "down"
    lt, opp = ("LHPB", "LLPB") if is_long else ("LLPB", "LHPB")
    t, px = ev["fill_time"], ev["fill"]
    stop, stop_row, stop_src = C2._dynamic_stop_m5(lt, px, is_long, ev["level"].to_dict(),
                                                   ledger_u, t)
    ev.update(stop=stop, stop_row=stop_row, stop_src=stop_src, target=None, target_row=None,
              outcome=None, exit_time=None, r=None, rr=None)
    if stop is None:
        ev["kind"] = "no stop"
        return ev
    tg = levels.awaiting_after_close(opp, t)
    tg = tg[tg["price"] > px] if is_long else tg[tg["price"] < px]
    if tg.empty:
        ev["kind"] = "no target"
        return ev
    trow = tg.sort_values(["formation_time", "breakout_time"]).iloc[-1]
    target = float(trow["price"])
    risk = abs(px - stop)
    ev.update(target=target, target_row=trow, rr=abs(target - px) / risk)
    for bt, b in m5[m5.index >= t].iterrows():
        hit_stop = b.low <= stop if is_long else b.high >= stop
        hit_tgt = bt > t and (b.high >= target if is_long else b.low <= target)
        if hit_stop:
            ev.update(outcome="loss", exit_time=bt, r=-1.0)
            if hit_tgt:
                ev["tags"].append("stop+target same bar")
            return ev
        if hit_tgt:
            ev.update(outcome="win", exit_time=bt, r=ev["rr"])
            return ev
    ev["outcome"] = "open"
    return ev


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

def chart(ev, m5):
    r = ev["range"]
    t_a = r["start"] - CTX_BEFORE
    t_z = (ev.get("exit_time") or ev.get("void_time") or ev.get("fill_time")
           or ev["flush_time"]) + CTX_AFTER
    w = m5[(m5.index >= t_a) & (m5.index <= t_z)]
    ts = lambda x: int(pd.Timestamp(x).timestamp())
    candles = [dict(time=ts(t), open=float(b.open), high=float(b.high), low=float(b.low),
                    close=float(b.close)) for t, b in w.iterrows()]
    hi, lo = ev["box"]
    snap = lambda x: w.index[w.index <= x][-1]   # a bar that exists on the chart
    box_end = snap(r["end"] + H1 - M5) if pd.notna(r["end"]) else w.index[-1]
    boxes = [dict(t0=ts(w.index[w.index >= r["start"]][0]), t1=ts(box_end), hi=hi, lo=lo)]
    in_w = lambda a, b: w.index[(w.index >= a) & (w.index <= b)]

    def ray(price, a, b, color, label, style=0, width=1):
        pts = [dict(time=ts(t), value=float(price)) for t in in_w(a, b)]
        return dict(points=pts, color=color, lineWidth=width, lineStyle=style, title="",
                    label=label) if pts else None

    rays = []
    lv = ev["level"]
    end_ray = ev.get("exit_time") or ev.get("void_time") or ev.get("fill_time") or w.index[-1]
    if lv is not None:
        rays.append(ray(lv["price"], lv["formation_time"], end_ray, "#60a5fa",
                        f"entry level {lv['type']} {lv['price']:.2f} ({lv['p0_kind']}), "
                        f"formed {pt(lv['formation_time'])} PT, P0 {pt(lv['breakout_time'])} PT", 0, 2))
    if ev.get("stop") is not None:
        rays.append(ray(ev["stop"], ev["fill_time"], end_ray, "#f87171",
                        f"stop {ev['stop']:.2f} ({ev['stop_src']})", 2))
    if ev.get("target") is not None:
        tr = ev["target_row"]
        rays.append(ray(ev["target"], tr["formation_time"], end_ray, "#4ade80",
                        f"target {tr['type']} {tr['price']:.2f} ({tr['p0_kind']}), "
                        f"formed {pt(tr['formation_time'])} PT", 2))
    rays = [x for x in rays if x]
    long_ = ev["side"] == "down"
    mk = [dict(time=ts(ev["flush_time"]), position="belowBar" if long_ else "aboveBar",
               color="#fbbf24", shape="arrowUp" if long_ else "arrowDown", text="flush")]
    if ev.get("fill_time") is not None:
        mk.append(dict(time=ts(ev["fill_time"]), position="belowBar" if long_ else "aboveBar",
                       color="#60a5fa", shape="arrowUp" if long_ else "arrowDown",
                       text=f"{'long' if long_ else 'short'} {ev['fill']:.2f}"))
    if ev.get("void_time") is not None:
        mk.append(dict(time=ts(ev["void_time"]), position="aboveBar" if long_ else "belowBar",
                       color="#f87171", shape="circle", text=ev["kind"]))
    if ev.get("exit_time") is not None:
        win = ev["outcome"] == "win"
        mk.append(dict(time=ts(ev["exit_time"]), position="aboveBar" if long_ == win else "belowBar",
                       color="#4ade80" if win else "#f87171", shape="circle",
                       text=f"{ev['outcome']} {ev['r']:+.2f}R"))
    mk.sort(key=lambda x: x["time"])
    title = (f"M5  |  H1 range {pt(r['start'], '%a %d %b %H:%M')}-{pt(r['confirm'], '%H:%M')} PT "
             f"box {lo:.2f}-{hi:.2f} as of the flush")
    return {"h1": dict(title=title, candles=candles, rays=rays, markers=mk, boxes=boxes,
                       precision=2)}


KIND_CLASS = {"void": "is-void", "ambiguous": "is-void", "trade": "is-trade"}


def row_html(i, ev):
    r = ev["range"]
    long_ = ev["side"] == "down"
    lv = ev["level"]
    kind = ev["kind"]
    if kind == "trade":
        o = ev["outcome"] or ev.get("kind")
        rtxt = "" if ev.get("r") is None else f" {ev['r']:+.2f}R"
        res = (f'<span class="{"good" if o == "win" else "bad" if o == "loss" else ""}">'
               f'{o}{rtxt}</span>')
    elif kind == "void":
        res = f'<span class="void-tag">VOID</span> back at edge {pt(ev["void_time"], "%H:%M")}'
    elif kind == "ambiguous":
        res = f'<span class="void-tag">AMBIGUOUS</span> edge + level same bar {pt(ev["void_time"], "%H:%M")}'
    else:
        res = f'<span class="muted">{kind}</span>'
    tags = "".join(f'<span class="src-tag">{t}</span>' for t in ev["tags"])
    dist = abs(lv["price"] - ev["edge"]) if lv is not None else None
    cells = [
        str(i + 1),
        f'{pt(r["start"], "%Y-%m-%d")}<br><span class="time-part">{pt(r["start"], "%a %H:%M")}-{pt(r["confirm"], "%H:%M")} PT</span>',
        f'{r["status"]}{"" if r["outer"] is None else "<br><span class=muted>inner</span>"}',
        f'{ev["box"][1]:.2f}<br>{ev["box"][0]:.2f}',
        f'<span class="{"good" if long_ else "bad"}">{"&darr; low &rarr; long" if long_ else "&uarr; high &rarr; short"}</span>',
        f'{pt(ev["flush_time"], "%a %H:%M")}',
        "-" if lv is None else f'{lv["type"]} {lv["price"]:.2f}<br><span class="muted">{lv["p0_kind"]}, {dist:.2f}pt out</span>',
        "-" if lv is None else f'{pt(lv["formation_time"], "%a %H:%M")}<br><span class="muted">P0 {pt(lv["breakout_time"], "%a %H:%M")}</span>',
        "-" if ev["fill_time"] is None else f'{ev["fill"]:.2f}<br><span class="muted">{pt(ev["fill_time"], "%a %H:%M")}</span>',
        "-" if ev.get("stop") is None else f'{ev["stop"]:.2f}<br><span class="muted">{ev["stop_src"]}</span>',
        "-" if ev.get("target") is None else f'{ev["target"]:.2f}<br><span class="muted">{ev["target_row"]["p0_kind"]} {pt(ev["target_row"]["formation_time"], "%H:%M")}</span>',
        "-" if ev.get("rr") is None else f'{ev["rr"]:.2f}',
        res + tags,
        "-" if ev.get("exit_time") is None else pt(ev["exit_time"], "%a %H:%M"),
    ]
    tds = "".join(f'<td class="left">{c}</td>' if k in (1, 6, 12) else f"<td>{c}</td>"
                  for k, c in enumerate(cells))
    n = len(cells) + 1
    return f"""<tr class="lvl-row {KIND_CLASS.get(kind, 'is-other')}" data-idx="{i}" onclick="toggleChart({i})">{tds}
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">&#9654;</button></td></tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}"><td colspan="{n}"><div class="chart-stack">
  <div class="chart-row-2col chart-row-solo"><div class="chart-cell chart-h1 m5-pane"><div class="chart-title" id="th1-{i}"></div><div class="chart-ph" id="ch1-{i}"></div></div></div>
</div></td></tr>"""


def page(events, charts, start, h, m5):
    trades = [e for e in events if e["kind"] == "trade"]
    done = [e for e in trades if e["outcome"] in ("win", "loss")]
    wins = sum(e["outcome"] == "win" for e in done)
    tot_r = sum(e["r"] for e in done)
    kinds = pd.Series([e["kind"] for e in events]).value_counts()
    boxes = [(len(events), "flushes (range sides)"), (len(trades), "filled trades"),
             (f"{wins}/{len(done)}", "wins / resolved"),
             (f"{tot_r:+.2f}R", "total R"),
             (f"{tot_r / len(done):+.3f}R" if done else "-", "avg R / trade"),
             (f"{np.median([e['rr'] for e in trades if e.get('rr')]):.2f}" if trades else "-",
              "median planned R:R")]
    boxes += [(v, k) for k, v in kinds.items() if k != "trade"]
    summary = "\n".join(f'  <div class="box"><strong>{v}</strong>{k}</div>' for v, k in boxes)
    heads = ["#", "H1 range", "Range status", "Box low / high (at flush)", "Side", "Flush",
             "Entry level", "Level formed", "Fill", "Stop", "Target", "Planned R:R", "Result",
             "Exit", ""]
    head = "".join(f"<th>{x}</th>" for x in heads)
    js = SR.JS.split('<script src="/js/row-store.js"></script>')[0]
    rows = "".join(row_html(i, e) for i, e in enumerate(events))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LFG setup study</title>
{SR.CSS}
<style>
.chart-row-2col.chart-row-solo {{ grid-template-columns: 1fr; }}
#lvl-table thead th {{ white-space:normal; max-width:90px; }}
p.note {{ max-width:1150px; }}
.range-box {{ position:absolute; pointer-events:none; z-index:3; box-sizing:border-box;
             border:1px solid #fbbf24; background:rgba(251,191,36,0.07); display:none; }}
.muted {{ color:#8b949e; }}
tr.lvl-row.is-void td {{ background:rgba(248,113,113,0.10); }}
tr.lvl-row.is-other td {{ opacity:0.6; }}
.void-tag {{ background:#7f1d1d; color:#fecaca; border-radius:3px; padding:1px 5px; font-weight:600; }}
.filters {{ margin:8px 0 12px; font-size:0.85em; }}
.filters label {{ margin-right:14px; cursor:pointer; }}
</style></head><body>
<h1>LFG &mdash; liquidity flush-n-grab (from {pt(start, '%d %b %Y')})</h1>
<p class="note"><b>Setup.</b> An H1 tight range (patterns-pure find_range, any range, inner or outer)
is tradable from the close of its confirming candle until the H1 candle that ends it closes. The box is
the one standing at the last H1 close. When an M5 candle trades past a box edge (a wick is enough), the
<b>entry level</b> is the nearest M5 P0 beyond that edge still waiting for its retest &mdash; any kind,
plain / swing / spike: an LHPB below the low (buy it), an LLPB above the high (sell it). Filled on the
first touch. <b>Void</b> (red rows): price came back to the box edge before touching the level &mdash;
the bounce already happened, no trade, and that side of the range is finished. <b>Ambiguous</b>: one M5
bar both returned to the edge and touched the level, so the order can't be told; treated like a void.
<b>Stop</b>: ss_m5_confl2's rule (one tick beyond a spike-P0 level's own candle, else one tick beyond the
most protective P1 thrust candle among same-side levels within 10pt of the fill, widened to an older P1 if
under 3pt). <b>Target (placeholder)</b>: the most recently formed opposite-type M5 P0 still awaiting its
retest beyond the fill, as of the entry candle's close (working from the next candle). One trade per side
per range. Outcomes on M5 bars; a bar hitting stop and target counts as a loss. Times PT. M5 data ends
{pt(m5.index[-1])} PT. Click a row for the chart (yellow box = range at the flush, blue = entry level,
red dashed = stop, green dashed = target).</p>
<div class="summary">
{summary}
</div>
<div class="filters">Show:
  <label><input type="checkbox" class="kf" value="is-trade" checked> trades</label>
  <label><input type="checkbox" class="kf" value="is-void" checked> void / ambiguous</label>
  <label><input type="checkbox" class="kf" value="is-other" checked> no level / no stop / no target / range ended</label>
</div>
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{head}</tr></thead>
<tbody>
{rows}
</tbody></table></div>
{js.replace("__CHARTS_JSON__", json.dumps(charts))}
<script>
const _lwcCreate = LightweightCharts.createChart;
function _renderH1(i, cd) {{
  let chart = null, series = null;
  LightweightCharts.createChart = function (el, o) {{
    chart = _lwcCreate(el, o);
    const add = chart.addCandlestickSeries.bind(chart);
    chart.addCandlestickSeries = function (so) {{ series = add(so); return series; }};
    return chart;
  }};
  try {{ _renderPane('ch1-' + i, 'th1-' + i, cd, {{ fontSize: 13 }}); }}
  finally {{ LightweightCharts.createChart = _lwcCreate; }}
  if (!chart || !series || !cd.boxes) return;
  const el = document.getElementById('ch1-' + i);
  const divs = cd.boxes.map(() => {{
    const d = document.createElement('div'); d.className = 'range-box'; el.appendChild(d); return d;
  }});
  function place() {{
    const ts = chart.timeScale(), half = (ts.options().barSpacing || 6) / 2;
    cd.boxes.forEach((b, k) => {{
      const x0 = ts.timeToCoordinate(b.t0), x1 = ts.timeToCoordinate(b.t1);
      const y0 = series.priceToCoordinate(b.hi), y1 = series.priceToCoordinate(b.lo);
      const d = divs[k];
      if ([x0, x1, y0, y1].some(v => v == null)) {{ d.style.display = 'none'; return; }}
      const right = el.clientWidth - chart.priceScale('right').width();
      const l = Math.max(0, x0 - half), r = Math.min(right, x1 + half);
      if (r <= l) {{ d.style.display = 'none'; return; }}
      Object.assign(d.style, {{ display: 'block', left: l + 'px', width: (r - l) + 'px',
                               top: y0 + 'px', height: Math.max(1, y1 - y0) + 'px' }});
    }});
    requestAnimationFrame(place);
  }}
  requestAnimationFrame(place);
}}
document.querySelectorAll('.kf').forEach(cb => cb.addEventListener('change', () => {{
  const on = new Set([...document.querySelectorAll('.kf:checked')].map(x => x.value));
  document.querySelectorAll('tr.lvl-row').forEach(tr => {{
    const cls = ['is-trade', 'is-void', 'is-other'].find(c => tr.classList.contains(c));
    const show = on.has(cls);
    tr.classList.toggle('hidden', !show);
    const cr = document.getElementById('chart-row-' + tr.dataset.idx);
    if (!show && cr) cr.classList.add('hidden');
  }});
}}));
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01", help="first range confirm date (PT)")
    ap.add_argument("--out", required=True, help="HTML output path")
    ap.add_argument("--max-ranges", type=int, default=None, help="smoke test: first N ranges only")
    a = ap.parse_args()
    start = pd.Timestamp(a.start, tz=PT).tz_convert("UTC")
    h = R._display_h1()
    m5 = LC.m5_bars_continuous()
    ranges = ranges_with_boxes(h, start)
    if a.max_ranges:
        ranges = ranges[:a.max_ranges]
    levels = Levels(LC.m5_levels(plain_p0=LC.PLAIN_P0_TRACKED, verbose=False))
    ledger_u = LC.m5_levels(plain_p0=LC.PLAIN_P0_UNTRACKED, verbose=False)
    events = []
    for r in ranges:
        for side in ("down", "up"):
            ev = walk_side(r, side, h, m5, levels)
            if ev is None:
                continue
            if ev["kind"] == "trade":
                manage(ev, m5, levels, ledger_u)
            events.append(ev)
    events.sort(key=lambda e: e["flush_time"])
    charts = [chart(e, m5) for e in events]
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page(events, charts, start, h, m5))
    kinds = pd.Series([e["kind"] for e in events]).value_counts().to_dict()
    print(f"{len(ranges)} ranges, {len(events)} flushes: {kinds} -> {a.out}")


if __name__ == "__main__":
    main()
