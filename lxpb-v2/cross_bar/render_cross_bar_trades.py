"""
render_cross_bar_trades.py
==========================
CROSS BAR TRADES: one trade per cross bar (see render_cross_bar_report.py for the cross bar itself), in the M1 fade report's
page layout with Reviewed / Valid / Notes.

The trade (user, 2026-10-29):
- ENTRY LEVELS: the M5 LXPB levels of the bar's direction (LHPB for a bullish bar = long, LLPB for a bearish bar = short) whose P0
  candle starts inside the cross bar's H1 hour and whose price lies beyond the swept price. The one nearest the swept price is
  the planned entry; an EARLIER one (further from the swept price, so price reaches it first on the way back) takes the trade
  instead when price comes back to it first.
- ONE SEAT per cross bar: the first of those levels price comes back to after the cross bar has CLOSED is the trade; every
  other level of the set is then spent, however price treats it later. A level price already came back to before the cross
  bar closed is spent too.
- FILL: a limit order at the level, filled in the first second (1 s ES bars) whose range reaches it; a second that opens
  beyond the level fills at its open (gap). No entry from 12:45 PT to the 15:00 PT reopen (that level is spent).
- STOP: one tick beyond the cross bar (below its low for a long, above its high for a short). Risk = fill to stop.
- NO TARGET. Management = the M1 fade report's managed bracket without its RR floor (the RR floor needs a target):
  thrust-P1 stop trail on the M5 ledger (trade_management.thrust_trail_events: an M5 candle that closes past enough still-live
  same-type P0s moves the stop to one tick beyond that candle; 1 P0 if any is a swing or spike P0, 2 if all have a 20%
  rejection wick, else 3; the stop only moves towards profit) and end-of-day flat (out at market at 12:44 PT).
- R = (exit - fill) / risk, signed. Stops and the flat are filled from the 1 s bars (a second that opens through the stop fills
  at its open).

Data: E:\\data_source (es_data.load_es): H1 for the cross bars, M5 rolled up from 1-minute bars for the ledger, 1 s bars for fill
and exits. Times are PT.

Usage:
    python cross_bar/render_cross_bar_trades.py --last 5 --output <file>.html
    python cross_bar/render_cross_bar_trades.py --at "2026-10-06 02:00,2026-09-23 19:00" --output <file>.html
"""
import os
import sys
import argparse
import pandas as pd
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import render_cross_bar_report as CB              # noqa: E402  (sets up the import paths)
from es_data import load_es                       # noqa: E402

PT = CB.PT
TICK = 0.25
FIVE = pd.Timedelta(minutes=5)
MIN_WICK_RATIO = 0.20                 # trade_management.MIN_WICK_RATIO
EOD_FLAT = pd.Timedelta(hours=12, minutes=45)
EOD_LEAD = pd.Timedelta(minutes=1)
REOPEN = pd.Timedelta(hours=15)
STOP_COLOR = "#f87171"
ENTRY_COLOR = "#60a5fa"
AFTER_EXIT = 30                       # M5 candles shown after the exit


# ---------------------------------------------------------------- 1 s bars (one month file read once)
_S1 = {}


def s1_between(a, b):
    """1 s bars with a <= time < b (PT index)."""
    utc = lambda x: x.tz_convert("UTC").tz_localize(None)
    months = pd.period_range(utc(a).to_period("M"), utc(b).to_period("M"), freq="M")
    parts = []
    for m in months:
        if m not in _S1:
            s = m.start_time.tz_localize("UTC")
            try:
                _S1[m] = load_es("1s", start=s, end=(m + 1).start_time.tz_localize("UTC"), tz=PT)[["open", "high", "low", "close"]]
            except ValueError:            # a month past the data's end: no files
                _S1[m] = pd.DataFrame(columns=["open", "high", "low", "close"], index=pd.DatetimeIndex([], tz=PT), dtype=float)
        parts.append(_S1[m])
    df = pd.concat(parts)
    return df[(df.index >= a) & (df.index < b)]


def _tod(ts):
    return ts - ts.normalize()


def entry_blocked(ts):
    return EOD_FLAT <= _tod(ts) < REOPEN


def eod_signal(ts):
    """The first 12:44 PT strictly after `ts`."""
    for d in (0, 1):
        c = (ts.normalize() + pd.Timedelta(days=d)).tz_localize(None) + EOD_FLAT - EOD_LEAD
        c = c.tz_localize(PT)
        if c > ts:
            return c
    raise AssertionError


# ---------------------------------------------------------------- the trade
def candidates(led, row):
    t, bull = row.Index, row.bullish
    c = led[(led["type"] == ("LHPB" if bull else "LLPB"))
            & (led["formation_time"] >= t) & (led["formation_time"] < t + pd.Timedelta(hours=1))
            & ((led["price"] > row.swing_price) if bull else (led["price"] < row.swing_price))]
    c = c.assign(dist=(c["price"] - row.swing_price).abs()).sort_values(["dist", "formation_time"])
    return c.reset_index(drop=True)      # row 0 = nearest to the swept price = the planned entry


def _fill(lv, bull):
    """(fill time, fill price) of a limit order at the level in its retest M5 bar, from the 1 s bars."""
    px = float(lv["price"])
    s = s1_between(lv["retest_time"], lv["retest_time"] + FIVE)
    hit = s[s["low"] <= px] if bull else s[s["high"] >= px]
    if hit.empty:                         # 1 s and M5 disagree by a tick at most: take the bar's first second
        hit = s
    ft = hit.index[0]
    o = float(hit["open"].iloc[0])
    return ft, (min(px, o) if bull else max(px, o))


def pick_trade(cands, row):
    """(entry level row, fill time, fill price, list of (level, why spent)) or (None, None, None, spent)."""
    closed = row.Index + pd.Timedelta(hours=1)
    spent, live = [], []
    for _, lv in cands.iterrows():
        if pd.isna(lv["retest_time"]):
            spent.append((lv, "not come back to yet (data end)"))
        elif lv["retest_time"] < closed:
            spent.append((lv, f"came back to before the cross bar closed ({CB._pt(lv['retest_time'], False)})"))
        else:
            ft, fp = _fill(lv, row.bullish)
            live.append((ft, -float(lv["price"]) if row.bullish else float(lv["price"]), lv, fp))
    live.sort(key=lambda x: (x[0], x[1]))   # first touched; same second: the one price reaches first on the way back
    for i, (ft, _, lv, fp) in enumerate(live):
        if entry_blocked(ft):
            spent.append((lv, f"touched {CB._pt(ft, False)}, inside the 12:45-15:00 PT no-entry window"))
            continue
        rest = [(x[2], f"seat already taken (touched {CB._pt(x[0], False)})") for x in live[i + 1:]]
        return lv, ft, fp, spent + rest
    return None, None, None, spent


def trail_events(led, m5, typ, start, end):
    """trade_management.thrust_trail_events on this ledger: [(trigger time = breakout candle close, new stop)]."""
    sub = led[led["type"] == typ]
    sub = sub.assign(trigger=sub["breakout_time"] + FIVE)
    sub = sub[(sub["trigger"] > start) & (sub["trigger"] <= end)]
    out = []
    for bt, g in sub.groupby("breakout_time"):
        if bool(g["is_swing"].fillna(False).any() or g["is_spike"].fillna(False).any()):
            need = 1
        else:
            ratios = []
            for ft in g["formation_time"]:
                if ft not in m5.index:
                    ratios.append(None)
                    continue
                b = m5.loc[ft]
                rng = b.high - b.low
                wick = (b.high - max(b.open, b.close)) if typ == "LHPB" else (min(b.open, b.close) - b.low)
                ratios.append(wick / rng if rng > 0 else None)
            need = 2 if ratios and all(r is not None and r >= MIN_WICK_RATIO for r in ratios) else 3
        if len(g) < need:
            continue
        r0 = g.iloc[0]
        stop = float(r0["breakout_low"]) - TICK if typ == "LHPB" else float(r0["breakout_high"]) + TICK
        out.append((bt + FIVE, stop, len(g)))
    return sorted(out)


def manage(led, m5, row, ft, fp):
    """Walk the 1 s bars from the fill: trailed stop, EOD flat. Returns a dict."""
    bull = row.bullish
    sign = 1.0 if bull else -1.0
    stop = float(row.low) - TICK if bull else float(row.high) + TICK
    risk = sign * (fp - stop)
    eod = eod_signal(ft)
    s = s1_between(ft, eod + pd.Timedelta(seconds=1))
    lo, hi, op = s["low"].to_numpy(float), s["high"].to_numpy(float), s["open"].to_numpy(float)
    idx = s.index
    evs = trail_events(led, m5, "LHPB" if bull else "LLPB", ft, eod)
    steps = [(ft, stop)]
    fired = []
    bounds = [0]
    stops = [stop]
    for trig, new, n in evs:
        if sign * (new - stops[-1]) > 0:
            k = int(idx.searchsorted(trig))
            bounds.append(k)
            stops.append(new)
            fired.append((trig, new, n))
    bounds.append(len(s))
    mfe = 0.0
    for j, st in enumerate(stops):
        a, b = bounds[j], bounds[j + 1]
        if a >= b:
            continue
        seg = lo[a:b] <= st if bull else hi[a:b] >= st
        hit = np.flatnonzero(seg)
        if len(hit):
            k = a + int(hit[0])
            ext = hi[:k + 1].max() if bull else lo[:k + 1].min()
            mfe = sign * (ext - fp) / risk
            px = st if k == 0 else (min(st, op[k]) if bull else max(st, op[k]))
            why = "stop" if j == 0 else f"trailed stop #{j}"
            return dict(exit_t=idx[k], exit_px=px, why=why, r=sign * (px - fp) / risk, risk=risk, stop0=stop,
                        trails=fired[:j], mfe=mfe, final_stop=st)
    if len(s) and idx[-1] >= eod:
        k = int(idx.searchsorted(eod))
        ext = hi[:k + 1].max() if bull else lo[:k + 1].min()
        return dict(exit_t=idx[k], exit_px=op[k], why="EOD flat 12:44 PT", r=sign * (op[k] - fp) / risk, risk=risk,
                    stop0=stop, trails=fired, mfe=sign * (ext - fp) / risk, final_stop=stops[-1])
    ext = hi.max() if bull else lo.min()
    last = float(s["close"].iloc[-1]) if len(s) else fp
    return dict(exit_t=idx[-1] if len(s) else ft, exit_px=last, why="open at data end (marked at last price)",
                r=sign * (last - fp) / risk, risk=risk, stop0=stop, trails=fired, mfe=sign * (ext - fp) / risk,
                final_stop=stops[-1])


# ---------------------------------------------------------------- 1R race per level
def race(lv, row):
    """Each entry level on its own (no seat): entered at its first touch after the cross bar closed, stop 1 tick beyond
    the cross bar, which comes first: the stop or +1R (entry + risk). No trail, no end-of-day flat; a second reaching both
    counts as the stop. Returns a dict: res = '1R first' / 'stop first' / 'not reached' / 'spent' / 'open at data end'."""
    bull = row.bullish
    sign = 1.0 if bull else -1.0
    stop = float(row.low) - TICK if bull else float(row.high) + TICK
    closed = row.Index + pd.Timedelta(hours=1)
    if pd.isna(lv["retest_time"]):
        return dict(res="not reached", lv=lv)
    if lv["retest_time"] < closed:
        return dict(res="spent", lv=lv, t=lv["retest_time"])
    ft, fp = _fill(lv, bull)
    risk = sign * (fp - stop)
    if risk <= 0:
        return dict(res="stop first", lv=lv, ft=ft, fp=fp, risk=risk, t=ft, stop=stop, tgt=fp)
    tgt = fp + sign * risk
    a = ft
    while True:
        b = a + pd.Timedelta(days=3)
        s = s1_between(a, b)
        if s.empty:
            return dict(res="open at data end", lv=lv, ft=ft, fp=fp, risk=risk, stop=stop, tgt=tgt)
        lo, hi = s["low"].to_numpy(float), s["high"].to_numpy(float)
        st = np.flatnonzero(lo <= stop if bull else hi >= stop)
        tg = np.flatnonzero(hi >= tgt if bull else lo <= tgt)
        i_st = st[0] if len(st) else None
        i_tg = tg[0] if len(tg) else None
        if i_st is not None and (i_tg is None or i_st <= i_tg):
            return dict(res="stop first", lv=lv, ft=ft, fp=fp, risk=risk, t=s.index[i_st], stop=stop, tgt=tgt)
        if i_tg is not None:
            return dict(res="1R first", lv=lv, ft=ft, fp=fp, risk=risk, t=s.index[i_tg], stop=stop, tgt=tgt)
        a = b


def _race_line(rc, nearest):
    lv = rc["lv"]
    head = f"{float(lv['price']):.2f}{' (nearest)' if nearest else ''}"
    if rc["res"] in ("1R first", "stop first"):
        return head, rc["res"], (f"fill {rc['fp']:.2f} {CB._pt(rc['ft'], False)}, risk {rc['risk']:.2f}, "
                                 f"1R {rc['tgt']:.2f}, {rc['res']} {CB._pt(rc['t'])}")
    if rc["res"] == "spent":
        return head, "spent", f"touched {CB._pt(rc['t'], False)}, before the cross bar closed"
    if rc["res"] == "open at data end":
        return head, rc["res"], f"fill {rc['fp']:.2f} {CB._pt(rc['ft'], False)}, neither stop nor 1R {rc['tgt']:.2f} by data end"
    return head, rc["res"], "price never came back to it (data end)"


RACE_COLOR = {"1R first": "#4ade80", "stop first": "#f87171"}


def race_cell(races):
    if not races:
        return '<span class="src-tag">no levels</span>'
    out = []
    for j, rc in enumerate(races):
        head, res, detail = _race_line(rc, j == 0)
        col = RACE_COLOR.get(res, "#9ca3af")
        out.append(f'<span title="{detail}">{head}: <span style="color:{col}">{res}</span></span>')
    return "<br>".join(out)


# ---------------------------------------------------------------- the trade pane
def trade_pane(m5, row, cands, lv, ft, fp, res, spent, races):
    t, bull = row.Index, row.bullish
    h1_end = t + pd.Timedelta(hours=1)
    pos = lambda x: int(m5.index.searchsorted(x, side="right")) - 1
    e, x = pos(ft), pos(res["exit_t"])
    spans = [(int(m5.index.searchsorted(t)), pos(h1_end - FIVE), 30), (e, e, 20), (x, x, AFTER_EXIT)]
    spans += [(pos(tr), pos(tr), 6) for tr, _, _ in res["trails"]]
    n = min(len(m5), x + AFTER_EXIT + 1)
    segs = [m5.iloc[lo: hi + 1] for lo, hi in CB._plan(spans, n, CB.LEVEL_GAP)]
    bars = pd.concat(segs)
    times = [int(i.timestamp()) for i in bars.index]
    snap = lambda ts: CB._snap(times, ts)
    first, last = times[0], times[-1]
    color = CB.BULL if bull else CB.BEAR
    px = float(lv["price"])
    marks = [{"time": snap(ft), "position": "belowBar" if bull else "aboveBar", "color": ENTRY_COLOR,
              "shape": "arrowUp" if bull else "arrowDown", "text": f"entry {fp:.2f}", "size": 2},
             {"time": snap(res["exit_t"]), "position": "aboveBar" if bull else "belowBar", "color": "#e5e7eb",
              "shape": "circle", "text": f"exit {res['r']:+.2f}R"}]
    for i, (tr, st, nn) in enumerate(res["trails"], 1):
        marks.append({"time": snap(tr - FIVE), "position": "belowBar" if bull else "aboveBar", "color": STOP_COLOR,
                      "shape": "square", "text": f"trail {i}"})
    if any(sg.index[0] <= lv["formation_time"] <= sg.index[-1] for sg in segs):
        marks.append({"time": snap(lv["formation_time"]), "position": "aboveBar" if bull else "belowBar",
                      "color": CB.LXPB_COLOR, "shape": "circle", "text": "P0"})
    # stop: the initial stop and each trailed stop, as one stepped line from the fill to the exit
    pts, cur = [], [(ft, res["stop0"])] + [(tr, st) for tr, st, _ in res["trails"]]
    for i, (a, st) in enumerate(cur):
        # a trail is live from its trigger candle on: the old stop holds through the candle before it
        b = cur[i + 1][0] - pd.Timedelta(seconds=1) if i + 1 < len(cur) else res["exit_t"]
        ta, tb = snap(a), snap(b)
        if pts and pts[-1]["time"] >= ta:
            ta = pts[-1]["time"]
            pts[-1] = {"time": ta, "value": st}
        else:
            pts.append({"time": ta, "value": st})
        if tb > ta:
            pts.append({"time": tb, "value": st})
    dedup = []
    for p in pts:
        if dedup and dedup[-1]["time"] == p["time"]:
            dedup[-1] = p
        else:
            dedup.append(p)
    rays = [{"color": CB.LXPB_COLOR, "lineWidth": 2, "lineStyle": 0, "title": "entry level", "priceLabel": True,
             "label": f"entry level M5 {lv['type']} {px:.2f} ({lv['p0_kind']})",
             "points": [{"time": snap(max(lv["formation_time"], bars.index[0])), "value": px}, {"time": snap(ft), "value": px}]},
            {"color": STOP_COLOR, "lineWidth": 1, "lineStyle": 0, "title": "stop", "priceLabel": True,
             "label": "stop (1 tick beyond the cross bar, then trailed)", "points": dedup}]
    bg = []
    for _, o in cands.iterrows():
        if o["formation_time"] == lv["formation_time"] and o["price"] == lv["price"]:
            continue
        end = o["retest_time"] if pd.notna(o["retest_time"]) else bars.index[-1]
        a, b = snap(max(o["formation_time"], bars.index[0])), snap(min(end, bars.index[-1]))
        if b > a:
            bg.append({"color": CB.LXPB_COLOR + "99", "lineWidth": 1, "lineStyle": 1,
                       "points": [{"time": a, "value": float(o["price"])}, {"time": b, "value": float(o["price"])}]})
    nearest = cands.iloc[0]
    which = ("the nearest to the swept price" if (nearest["price"] == px and nearest["formation_time"] == lv["formation_time"])
             else f"an EARLIER level (nearest was {float(nearest['price']):.2f}): price came back to this one first")
    notes = [{"color": CB.LXPB_COLOR, "text": f"entry level M5 {lv['type']} {px:.2f} ({lv['p0_kind']}), P0 {CB._pt(lv['formation_time'], False)}, {which}"},
             {"color": ENTRY_COLOR, "text": f"fill {fp:.2f} at {CB._pt(ft)}"},
             {"color": STOP_COLOR, "text": f"stop {res['stop0']:.2f} = 1 tick beyond the cross bar, risk {res['risk']:.2f} pt"}]
    for i, (tr, st, nn) in enumerate(res["trails"], 1):
        notes.append({"color": STOP_COLOR, "text": f"trail {i}: M5 candle closing {CB._pt(tr, False)} broke {nn} P0(s), stop -> {st:.2f}"})
    notes.append({"color": "#e5e7eb", "text": f"exit {res['exit_px']:.2f} at {CB._pt(res['exit_t'])} ({res['why']}), "
                                               f"{res['r']:+.2f}R, best {res['mfe']:+.2f}R"})
    if bg:
        notes.append({"color": "#8b8b8b", "text": f"dotted: the {len(bg)} other entry levels of this cross bar (spent, one seat)"})
    for j, rc in enumerate(races):
        head, r, detail = _race_line(rc, j == 0)
        notes.append({"color": RACE_COLOR.get(r, "#9ca3af"), "text": f"level {head} on its own: {detail}"})
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {CB._pt(t, False)}"},
          {"time": int((h1_end - FIVE).timestamp()), "color": color, "label": "bar end"}]
    pane = CB._pane(segs, t, row.swing_time, row.swing_price, bull, "M5 trade", extra_markers=marks, vlines=vl,
                    cross_marker=False, extra_rays=rays, notes=notes)
    pane["bgRays"] = bg
    return pane


# ---------------------------------------------------------------- page
HEAD_HTML = (
    "<th class=\"left\">#</th><th class=\"left\">Cross bar (H1 start)</th><th class=\"left\">Side</th>"
    "<th class=\"left\">Confirmed by</th><th>Swept</th>"
    "<th class=\"left\" title=\"M5 LXPB level traded: nearest to the swept price, or an earlier one price came back to first\">Entry level</th>"
    "<th class=\"left\">Fill</th><th title=\"1 tick beyond the cross bar; risk in points\">Stop / risk</th>"
    "<th title=\"Thrust-P1 trail steps fired\">Trails</th><th class=\"left\">Exit</th><th>R</th>"
    "<th title=\"Best open profit before the exit, in R\">Best R</th>"
    "<th class=\"left\" title=\"Every entry level on its own (no seat): entered at its first touch after the cross bar closed, same stop, which came first: the stop or +1R. No trail, no end-of-day flat. Hover a level for the numbers\">Levels: stop or 1R first</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 17


def _row_html(i, row, tr, cells_extra, races):
    cls = "type-lhpb" if row.bullish else "type-llpb"
    key = f"{row.Index.tz_convert('UTC').strftime('%Y-%m-%dT%H')}_{'bull' if row.bullish else 'bear'}"
    if tr is None:
        trade_tds = f'<td class="left" colspan="7"><span class="src-tag">NO TRADE: {cells_extra}</span></td>'
    else:
        lv, ft, fp, res, which = tr
        rc = "#4ade80" if res["r"] > 0 else "#f87171"
        trade_tds = f"""
  <td class="left">{float(lv['price']):.2f} {lv['type']}<span class="src-tag"> {lv['p0_kind']}</span><br><span class="src-tag">{which}</span></td>
  <td class="left">{fp:.2f}<br><span class="src-tag">{CB._pt(ft)}</span></td>
  <td>{res['stop0']:.2f}<br><span class="src-tag">{res['risk']:.2f} pt</span></td>
  <td>{len(res['trails'])}</td>
  <td class="left">{res['exit_px']:.2f}<br><span class="src-tag">{CB._pt(res['exit_t'])} &middot; {res['why']}</span></td>
  <td style="color:{rc}"><strong>{res['r']:+.2f}</strong></td>
  <td>{res['mfe']:+.2f}</td>"""
    return f"""
<tr class="lvl-row is-valid {cls}" data-idx="{i}" data-key="{key}" onclick="toggleChart({i})" style="cursor:pointer">
  <td class="left">{i + 1}</td>
  <td class="left">{CB._pt(row.Index)}</td>
  <td class="left" style="color:{CB.BULL if row.bullish else CB.BEAR}">{'Long' if row.bullish else 'Short'}</td>
  <td class="left">{CB._confirmation(row)}</td>
  <td>{row.swing_price:.2f}</td>{trade_tds}
  <td class="left" style="white-space:nowrap">{race_cell(races)}</td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-main-{i}"></div><div class="chart-ph" id="c-main-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-ctx-{i}"></div><div class="chart-ph" id="c-ctx-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l5-{i}"></div><div class="chart-ph" id="c-l5-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l2-{i}"></div><div class="chart-ph" id="c-l2-{i}"></div></div>
  </div></td>
</tr>"""


def render(rows, h1, output, label):
    lo = rows.index.min() - pd.Timedelta(days=30)
    m5 = load_es("5min", start=lo, tz=PT)
    led = CB.ledger(m5)
    m2 = load_es("2min", start=lo, tz=PT)
    led2 = CB.ledger(m2)
    charts, trs, rs, all_races = [], [], [], []
    for i, row in enumerate(rows.itertuples()):
        older = CB.older_swings(h1, h1.index.get_loc(row.Index), row.swing_time, row.swing_price, row.bullish)
        chart = CB.build_example(h1, m5, row, older)
        cands = candidates(led, row)
        races = [race(lv, row) for _, lv in cands.iterrows()]
        all_races.extend((j == 0, rc) for j, rc in enumerate(races))
        tr, why = None, ""
        if cands.empty:
            why = "no M5 LXPB level of this direction beyond the swept price with its P0 inside the cross bar hour"
        else:
            lv, ft, fp, spent = pick_trade(cands, row)
            if lv is None:
                why = "; ".join(f"{float(s['price']):.2f}: {w}" for s, w in spent)
            else:
                res = manage(led, m5, row, ft, fp)
                near = cands.iloc[0]
                which = ("nearest" if (near["price"] == lv["price"] and near["formation_time"] == lv["formation_time"])
                         else f"earlier (nearest {float(near['price']):.2f})")
                tr = (lv, ft, fp, res, which)
                rs.append(res["r"])
                chart["l5"] = trade_pane(m5, row, cands, lv, ft, fp, res, spent, races)
                print(f"{CB._pt(row.Index)} {'long ' if row.bullish else 'short'} {which:28s} fill {fp:.2f} stop {res['stop0']:.2f} "
                      f"trails {len(res['trails'])} exit {res['exit_px']:.2f} {res['why']}: {res['r']:+.2f}R")
        if tr is None:
            chart["l5"] = None
            print(f"{CB._pt(row.Index)} no trade: {why}")
        lv2 = CB.pick_level(led2, row.Index, row.swing_price, row.bullish)
        chart["l2"] = None if lv2 is None else CB.level_pane(m2, 2, "M2", lv2, row, CB.BULL if row.bullish else CB.BEAR, led2)
        charts.append(chart)
        trs.append(_row_html(i, row, tr, why, races))
    n, wins = len(rs), sum(r > 0 for r in rs)
    decided = [(nr, rc["res"]) for nr, rc in all_races if rc["res"] in ("1R first", "stop first")]
    one_r = sum(r == "1R first" for _, r in decided)
    near = [r for nr, r in decided if nr]
    near_1r = sum(r == "1R first" for r in near)
    earl = [r for nr, r in decided if not nr]
    earl_1r = sum(r == "1R first" for r in earl)
    pct = lambda a, b: f"{a / b:.0%}" if b else "-"
    print(f"levels: {len(all_races)}, decided {len(decided)}, 1R first {one_r} ({pct(one_r, len(decided))}); "
          f"nearest {near_1r}/{len(near)}, earlier {earl_1r}/{len(earl)}")
    title = "ES H1 cross bar trades" + (f" ({label})" if label else "")
    storage_key = "lxpb_cross_bar_trades_v1_" + "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-")
    js = CB._js(charts, storage_key).replace("l5: 'M5-LXPB'", "l5: 'M5 trade'").replace(
        "(no LXPB level of this direction beyond the swept price with its P0 formed inside the cross bar hour)",
        "(no trade / no level of this direction beyond the swept price with its P0 inside the cross bar hour)")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{CB.M.CSS}
{CB.NOTES_CSS}
</head><body>
<h1>{title}</h1>
<p class="lead">One trade per H1 cross bar. ENTRY: a limit order on the M5 LXPB levels of the bar's direction (LHPB = long after a
bullish bar, LLPB = short after a bearish one) whose P0 formed inside the cross bar's hour, beyond the swept price. The planned
entry is the one nearest the swept price; when price comes back to an EARLIER one first (further from the swept price), that one
takes the trade. ONE SEAT: after that fill no other level of the set trades; a level already touched before the cross bar closed is
spent. FILL: the first 1 s bar reaching the level (at its open if it gapped past). STOP: 1 tick beyond the cross bar. NO TARGET:
managed like the M1 fade report's managed bracket without its RR floor (which needs a target): the thrust-P1 trail on the M5 ledger
and the 12:44 PT end-of-day flat. No entry 12:45-15:00 PT. R = (exit - fill) / risk. Click a row: the H1 cross bar, the M5 view of its hour,
then the M5 trade pane (blue = entry, red = stop as it trails, dotted = the other entry levels, 30 candles past the
exit) and the cross bar report's M2-LXPB pane (nearest M2 level, for reference: trades are on M5 levels only). Times are PT.</p>
<p class="lead">LEVELS: STOP OR 1R FIRST. Every entry level of a cross bar is also judged ON ITS OWN, ignoring the seat:
entered at its first touch after the cross bar closed (same fill rule), the same stop, and which comes first, the stop or +1R
(entry + risk). No trail, no end-of-day flat (the race can run over days); a second reaching both counts as the stop. "spent" =
touched before the cross bar closed, "not reached" = price has not come back to it. The boxes count the decided levels only.</p>
<div class="summary">
  <div class="box"><strong>{len(rows)}</strong>cross bars</div>
  <div class="box"><strong>{n}</strong>trades</div>
  <div class="box"><strong>{(wins / n if n else 0):.0%}</strong>win rate</div>
  <div class="box"><strong>{(sum(rs) / n if n else 0):+.2f}</strong>avg R</div>
  <div class="box"><strong>{sum(rs):+.2f}</strong>total R</div>
  <div class="box"><strong>{one_r}/{len(decided)}</strong>levels 1R first ({pct(one_r, len(decided))})</div>
  <div class="box"><strong>{near_1r}/{len(near)}</strong>nearest levels 1R first</div>
  <div class="box"><strong>{earl_1r}/{len(earl)}</strong>earlier levels 1R first</div>
  <div class="box"><strong id="sum-shown">{len(rows)}</strong>shown</div>
  <div class="box"><strong id="sum-reviewed">0</strong>reviewed</div>
  <div class="box"><strong id="sum-valid">0</strong>valid</div>{CB.TOOLBAR_HTML}
</div>
{CB.FILTERS_HTML}
<div class="table-wrap"><table id="lvl-table">
<thead><tr>{HEAD_HTML}</tr></thead>
<tbody>{"".join(trs)}
</tbody></table></div>
{js}
</body></html>
"""
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"saved {output}: {len(rows)} cross bars, {n} trades, {wins} wins, {sum(rs):+.2f}R")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--at", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    if not (a.all or a.last or a.at):
        ap.error("pass --all, --last N or --at")
    h1 = load_es("1h", tz=PT)
    rows = CB.collect(h1)
    rows = rows.loc[rows.index >= pd.Timestamp(a.since, tz=PT)]
    if a.at:
        want = [pd.Timestamp(x.strip(), tz=PT) for x in a.at.split(",")]
        rows = rows.loc[rows.index.isin(want)]
    elif a.last:
        rows = pd.concat([g.tail(a.last) for _, g in rows.groupby("bullish")]).sort_index()
    rows = rows.sort_index(ascending=False)
    render(rows, h1, a.output, a.label or "sample")


if __name__ == "__main__":
    main()
