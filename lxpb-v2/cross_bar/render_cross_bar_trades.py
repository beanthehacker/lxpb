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

FORWARD REPLAY (user, 2026-11-03): panes 1-3 (H1, M5, M2) start at the cross bar's close and step forward together, one candle of
any of them at a time; every pane shows its candles as they stood at the replay time (the last one still forming), built in the
page from the row's M1 bars. The replay window runs at least MIN_FUTURE_M1 M1 bars past the close, on until every P0 price of the
cross bar (its M5 and M2 entry levels not touched before the close) has been revisited, at most MAX_FUTURE_M1 bars
(replay_window). The long / short position tool (public/js/position-tool.js) keeps ONE manual trade per row, drawn on all three
panes (and on the automated trade's panes 4-5, hidden until asked for). It is judged on the 1 s price path: entered when price
trades at its entry price again after the candle it was drawn on (or from the replay time when that candle is still forming), then
the first second reaching its stop or target ends it (a second reaching both = the stop; no end-of-day flat). The M1 bars and the
path of each row are in its own file next to the page (<page name>_data/<row key>.js), loaded when the row opens.

Usage:
    python cross_bar/render_cross_bar_trades.py --last 5 --output <file>.html
    python cross_bar/render_cross_bar_trades.py --at "2026-10-06 02:00,2026-09-23 19:00" --output <file>.html
"""
import os
import sys
import argparse
import json
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
AFTER_EXIT = 30                       # M5 candles shown after the exit (at least)
MIN_FUTURE_M1 = 1380                  # replay window: at least this many M1 bars (one trading day, 23 h) past the cross bar ...
MAX_FUTURE_M1 = 5 * 1380              # ... at most five trading days
REVISIT_PAD = 60                      # ... and this many M1 bars past the last P0 price's revisit
POS_TOOL_JS = os.path.join(CB._REPO_ROOT, "public", "js", "position-tool.js")   # TradingView-style long / short position tool


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
def trade_pane(m5, row, cands, lv, ft, fp, res, spent, races, until):
    t, bull = row.Index, row.bullish
    h1_end = t + pd.Timedelta(hours=1)
    pos = lambda x: int(m5.index.searchsorted(x, side="right")) - 1
    e, x, u = pos(ft), pos(res["exit_t"]), pos(until)
    spans = [(int(m5.index.searchsorted(t)), pos(h1_end - FIVE), 30), (e, e, 20), (x, x, AFTER_EXIT)]
    spans += [(pos(tr), pos(tr), 6) for tr, _, _ in res["trails"]]
    spans.append((int(m5.index.searchsorted(t)), u, 0))          # every candle from the cross bar to the horizon
    n = min(len(m5), max(x + AFTER_EXIT, u) + 1)
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


def blind_pane(bars, minutes, label, row, older):
    """The M5 context pane's view on other bars (the M2 one): NEAR candles before the cross bar's hour, ends with its last candle."""
    t, bull = row.Index, row.bullish
    dt = pd.Timedelta(minutes=minutes)
    h1_end = t + pd.Timedelta(hours=1)
    color = CB.BULL if bull else CB.BEAR
    p0 = int(bars.index.searchsorted(t))
    p1 = int(bars.index.searchsorted(h1_end)) - 1
    segs = [bars.iloc[lo: hi + 1] for lo, hi in CB._plan([(p0, p1, CB.NEAR)], p1 + 1)]
    inside = bars.loc[t: h1_end - dt]
    sweep_t = inside["low"].idxmin() if bull else inside["high"].idxmax()
    sweep_px = inside.at[sweep_t, "low" if bull else "high"]
    marks = [{"time": int(sweep_t.timestamp()), "position": "belowBar" if bull else "aboveBar", "color": color,
              "shape": "circle", "text": f"sweep {'low' if bull else 'high'} {sweep_px:.2f}"}]
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {CB._pt(t, False)}"},
          {"time": int((h1_end - dt).timestamp()), "color": color, "label": "bar end"}]
    return CB._pane(segs, t, row.swing_time, row.swing_price, bull, label, extra_markers=marks, vlines=vl,
                    cross_marker=False, older=older)


def future_pane(bars, minutes, label, row, until):
    """A plain pane for a row with no trade / no level: NEAR candles before the cross bar's hour, then every candle to `until`."""
    t, bull = row.Index, row.bullish
    dt = pd.Timedelta(minutes=minutes)
    h1_end = t + pd.Timedelta(hours=1)
    color = CB.BULL if bull else CB.BEAR
    p0 = int(bars.index.searchsorted(t))
    u = int(bars.index.searchsorted(until, side="right")) - 1
    segs = [bars.iloc[lo: hi + 1] for lo, hi in CB._plan([(p0, u, 0), (p0, p0, CB.NEAR)], u + 1)]
    vl = [{"time": int(t.timestamp()), "color": color, "label": f"H1 cross bar {CB._pt(t, False)}"},
          {"time": int((h1_end - dt).timestamp()), "color": color, "label": "bar end"}]
    return CB._pane(segs, t, row.swing_time, row.swing_price, bull, label, vlines=vl, cross_marker=False)


def m1_pack(bars):
    """M1 bars for the page's replay, compact: flat [minute step, open, high, low, close], prices in ticks from a base."""
    tk = np.rint(bars[["open", "high", "low", "close"]].to_numpy(float) / TICK).astype(np.int64)
    base = int(tk[0, 0])
    sec = bars.index.asi8 // 1_000_000_000
    steps = np.diff(sec, prepend=sec[0]) // 60
    rows = np.column_stack([steps, tk - base]).ravel().tolist()
    return {"t0": int(sec[0]), "b": base, "r": rows}


# ---------------------------------------------------------------- forward replay and the manual trade
MANUAL_CSS = """<style>
.replay-bar { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 6px 10px; position: sticky; top: 0;
  z-index: 6; background: #0b0f17; border: 1px solid #1f2937; font: 12px Courier New, monospace; color: #9ca3af; }
.replay-bar button, .replay-bar select { background: #1b2330; color: #e5e7eb; border: 1px solid #334155; border-radius: 4px;
  padding: 2px 8px; font: 12px/1.6 Courier New, monospace; cursor: pointer; }
.replay-bar button:hover { background: #273449; }
.replay-bar button.on { background: #2962ff; color: #fff; }
.replay-bar .rp-time { color: #e5e7eb; font-weight: bold; }
.reveal-bar { display: flex; gap: 14px; align-items: center; padding: 8px 10px; font: 12px Courier New, monospace; color: #9ca3af; }
.reveal-btn { background: #374151; color: #fff; border: 0; border-radius: 4px; padding: 5px 14px; cursor: pointer;
  font: bold 13px Courier New, monospace; }
.future-wrap.hidden { display: none; }
td.man-entry, td.man-exit { white-space: nowrap; }
</style>"""

MANUAL_JS = r"""
// FORWARD REPLAY. The H1, M5 and M2 panes start at the cross bar's close and step forward together: a step of one pane's candle
// moves the replay time T to that candle's end, and every pane shows its candles as they stood at T (the last one still forming),
// built from the row's M1 bars (loaded from its data file when the row opens). The row's one manual trade (the position tool's
// box, shared by the three panes) is judged on the row's 1 s price path up to T: entered when price trades at its entry price
// from the box's `from` on, then the first second reaching the stop or the target ends it (a second reaching both = the stop).
const _DATA_DIR = '__DATA_DIR__';
const _BLIND = { main: 3600, ctx: 300, m2: 120 };          // pane -> candle length (s)
const _TFN = { main: 'H1', ctx: 'M5', m2: 'M2' };
const _RP = {};
const _FILE = location.pathname.split('/').pop();
const _key = (i) => ((document.querySelector('tr.lvl-row[data-idx="' + i + '"]') || {}).dataset || {}).key;
const _ls = (k, d) => { try { const v = localStorage.getItem('postool_v1_' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
const _lsSet = (k, v) => { try { localStorage.setItem('postool_v1_' + k, JSON.stringify(v)); } catch (e) { /* private window */ } };
function _rp(i) {
  if (!_RP[i]) {
    const cd = CHARTS[i];
    _RP[i] = { close: cd.close, end: cd.end, T: Math.max(cd.close, Math.min(cd.end, _ls('cursor_' + _FILE + '_' + _key(i), cd.close))),
               data: null, panes: {}, res: null, fin: null, play: null };
  }
  return _RP[i];
}
const _lb = (a, t) => { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < t) lo = m + 1; else hi = m; } return lo; };

// ---- the row's data file: M1 bars and the 1 s path (seconds whose range is not inside the one kept before it, that minute)
function _decode(d) {
  const m = d.m1, M = [];
  let t = m.t0;
  for (let k = 0; k < m.r.length; k += 5) {
    t += m.r[k] * 60;
    M.push({ time: t, open: (m.b + m.r[k + 1]) * 0.25, high: (m.b + m.r[k + 2]) * 0.25, low: (m.b + m.r[k + 3]) * 0.25,
             close: (m.b + m.r[k + 4]) * 0.25 });
  }
  const s = d.s1, n = s.r.length / 3, T = new Float64Array(n), L = new Float64Array(n), H = new Float64Array(n);
  let ts = s.t0, lo = s.b;
  for (let k = 0; k < n; k++) { ts += s.r[3 * k]; lo += s.r[3 * k + 1]; T[k] = ts; L[k] = lo * 0.25; H[k] = (lo + s.r[3 * k + 2]) * 0.25; }
  return { m1: M, m1t: M.map(c => c.time), s1: { n, t: T, lo: L, hi: H } };
}
window.__replayData = (key, d) => {
  document.querySelectorAll('tr.lvl-row').forEach(tr => {
    if (tr.dataset.key !== key) return;
    const st = _rp(+tr.dataset.idx);
    st.data = _decode(d);
    (st.waiting || []).splice(0).forEach(f => f());
  });
};
function _load(i, cb) {
  const st = _rp(i);
  if (st.data) { cb(); return; }
  (st.waiting = st.waiting || []).push(cb);
  if (st.loading) return;
  st.loading = true;
  const sc = document.createElement('script');
  sc.src = _DATA_DIR + '/' + _key(i) + '.js';
  sc.onerror = () => { st.loading = false; _syncBar(i, 'replay data not found (' + sc.src + ')'); };
  document.head.appendChild(sc);
}

// ---- candles at T
function _barsAt(i, pane) {
  const st = _rp(i), p = st.panes[pane], b = _BLIND[pane], out = p.hist.slice();
  if (!st.data) return out;
  const M = st.data.m1;
  for (let k = _lb(st.data.m1t, st.close); k < M.length && M[k].time < st.T; k++) {
    const c = M[k], bt = Math.floor(c.time / b) * b, last = out[out.length - 1];
    if (last.time === bt && bt >= st.close) {
      last.high = Math.max(last.high, c.high); last.low = Math.min(last.low, c.low); last.close = c.close;
    } else out.push({ time: bt, open: c.open, high: c.high, low: c.low, close: c.close });
  }
  return out;
}
function _apply(i) {
  const st = _rp(i);
  Object.keys(st.panes).forEach(pane => {
    const p = st.panes[pane], bars = _barsAt(i, pane);
    const ts = p.chart.timeScale(), vr = ts.getVisibleLogicalRange(), n0 = (p.cd.live || p.cd.candles).length;
    p.cd.live = bars;
    p.series.setData(bars);
    if (vr) {
      const d = bars.length - n0;
      ts.setVisibleLogicalRange(vr.to >= n0 - 1.5 ? { from: vr.from + d, to: vr.to + d } : vr);   // follow the right edge
    }
  });
  _evalRow(i);
  PosTool.refresh(i);
  _syncBar(i);
  _fillRow(i);
  _manualStats();
}
function _setT(i, T) {
  const st = _rp(i);
  st.T = Math.max(st.close, Math.min(st.end, T));
  _lsSet('cursor_' + _FILE + '_' + _key(i), st.T);
  _apply(i);
}
function _step(i, pane, dir) {
  const st = _rp(i), b = _BLIND[pane];
  if (!st.data) return;
  let T = dir > 0 ? (Math.floor(st.T / b) + 1) * b : (Math.ceil(st.T / b) - 1) * b;
  if (dir > 0) {                                  // no bars in that step (market closed): on to the candle the next bar is in
    const k = _lb(st.data.m1t, st.T), nx = st.data.m1[k];
    if (nx && nx.time >= T) T = (Math.floor(nx.time / b) + 1) * b;
  }
  _setT(i, T);
}

// ---- the manual trade on the 1 s path, up to `upto`
function _lastPx(D, upto) {
  const k = _lb(D.m1t, upto - 59) - 1;                     // the last M1 bar that has closed by upto
  return k >= 0 ? { t: D.m1[k].time, px: D.m1[k].close } : null;
}
function _eval(p, D, upto) {
  const S = D.s1, s = p.side === 'long' ? 1 : -1, risk = Math.abs(p.entry - p.sl);
  let f = -1, fp = null;
  for (let k = _lb(S.t, p.from); k < S.n && S.t[k] < upto; k++) {
    const lo = S.lo[k], hi = S.hi[k];
    if (lo <= p.entry && hi >= p.entry) { f = k; fp = p.entry; break; }
    if (k > 0 && S.hi[k - 1] < p.entry && lo > p.entry) { f = k; fp = lo; break; }      // jumped over it: its first price
    if (k > 0 && S.lo[k - 1] > p.entry && hi < p.entry) { f = k; fp = hi; break; }
  }
  if (f < 0) return { state: 'waiting' };
  const out = (state, j, px, hit) => ({ state, fillT: S.t[f], fillPx: fp, exitT: j == null ? null : S.t[j], exitPx: px, hit,
                                        r: risk > 0 ? s * (px - fp) / risk : 0 });
  for (let j = f; j < S.n && S.t[j] < upto; j++) {
    const lo = S.lo[j], hi = S.hi[j], ref = j === f;
    if (s > 0 ? lo <= p.sl : hi >= p.sl)
      return out('closed', j, ref ? (s > 0 ? Math.min(p.sl, fp) : Math.max(p.sl, fp)) : (s > 0 ? Math.min(p.sl, hi) : Math.max(p.sl, lo)), 'stop');
    if (s > 0 ? hi >= p.tp : lo <= p.tp)
      return out('closed', j, ref ? (s > 0 ? Math.max(p.tp, fp) : Math.min(p.tp, fp)) : (s > 0 ? Math.max(p.tp, lo) : Math.min(p.tp, hi)), 'target');
  }
  const last = _lastPx(D, upto);
  const o = out('open', null, last && last.t >= S.t[f] - 60 ? last.px : fp, null);
  o.lastT = last ? Math.max(last.t, S.t[f]) : S.t[f]; o.lastPx = o.exitPx;
  return o;
}
function _evalRow(i) {
  const st = _rp(i), box = PosTool.getBox(i);
  st.res = box && st.data ? _eval(box, st.data, st.T) : null;
  st.fin = box && st.data ? _eval(box, st.data, st.end) : null;
  if (st.data) _lsSet('state_' + _FILE + '_' + _key(i), st.res ? { T: st.T, res: st.res } : null);
}
const _cached = (i) => { const st = _rp(i); return st.data ? (st.res ? { T: st.T, res: st.res } : null) : _ls('state_' + _FILE + '_' + _key(i), null); };

// ---- table cells, stats, replay bar
const _f2 = (v) => v.toFixed(2);
const _ptTime = (t, sec) => new Date(t * 1000).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', weekday: 'short',
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: sec ? '2-digit' : undefined, hour12: false }) + ' PT';
const _riskUsd = () => PosTool.settings.account * PosTool.settings.riskPct / 100;
const _sgn = (v, d) => (v >= 0 ? '+' : '') + v.toFixed(d);
function _fillRow(i) {
  const tr = document.querySelector('tr.lvl-row[data-idx="' + i + '"]');
  if (!tr) return;
  const cell = (c) => tr.querySelector('td.' + c);
  const b = PosTool.getBox(i), c = _cached(i), r = c && c.res;
  if (!b) {
    cell('man-entry').innerHTML = '<span class="src-tag">-</span>';
    ['man-plan', 'man-exit', 'man-r', 'man-usd'].forEach(k => { cell(k).innerHTML = ''; });
    return;
  }
  const risk = Math.abs(b.entry - b.sl), rr = risk > 0 ? Math.abs(b.tp - b.entry) / risk : 0;
  const col = b.side === 'long' ? '#4ade80' : '#f87171';
  cell('man-entry').innerHTML = '<span style="color:' + col + '">' + b.side + '</span> ' + _f2(b.entry)
    + '<br><span class="src-tag">' + (r && r.fillT != null ? 'fill ' + _f2(r.fillPx) + ' ' + _ptTime(r.fillT, true)
                                                           : 'drawn on ' + (_TFN[b.pane] || b.pane)) + '</span>';
  cell('man-plan').innerHTML = _f2(b.sl) + ' / ' + _f2(b.tp) + '<br><span class="src-tag">' + rr.toFixed(2) + ' : 1</span>';
  const asOf = c ? '<br><span class="src-tag">replay at ' + _ptTime(c.T) + '</span>' : '';
  if (!r || r.state === 'waiting') {
    cell('man-exit').innerHTML = '<span class="src-tag">' + (r ? 'waiting for the entry' : 'open the row to replay') + '</span>' + asOf;
    cell('man-r').innerHTML = ''; cell('man-usd').innerHTML = '';
    return;
  }
  const rc = r.r > 0 ? '#4ade80' : r.r < 0 ? '#f87171' : '#e5e7eb', usd = r.r * _riskUsd();
  if (r.state === 'open') {
    cell('man-exit').innerHTML = '<span class="src-tag">open, last ' + _f2(r.exitPx) + '</span>' + asOf;
    cell('man-r').innerHTML = '<em style="color:' + rc + '">' + _sgn(r.r, 2) + '</em><br><span class="src-tag">open</span>';
    cell('man-usd').innerHTML = '<em style="color:' + rc + '">' + _sgn(Math.round(usd), 0) + '</em>';
    return;
  }
  cell('man-exit').innerHTML = _f2(r.exitPx) + '<br><span class="src-tag">' + _ptTime(r.exitT, true) + ' &middot; ' + r.hit + '</span>';
  cell('man-r').innerHTML = '<strong style="color:' + rc + '">' + _sgn(r.r, 2) + '</strong>';
  cell('man-usd').innerHTML = '<span style="color:' + rc + '">' + _sgn(Math.round(usd), 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '</span>';
}
function _manualStats() {
  let n = 0, open = 0, wait = 0, wins = 0, tot = 0;
  document.querySelectorAll('tr.lvl-row').forEach(tr => {
    const i = +tr.dataset.idx;
    if (!PosTool.getBox(i)) return;
    const c = _cached(i), r = c && c.res;
    if (!r || r.state === 'waiting') { wait++; return; }
    if (r.state === 'open') { open++; return; }
    n++; tot += r.r; if (r.r > 0) wins++;
  });
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('man-n', n); set('man-open', open); set('man-wait', wait);
  set('man-win', n ? Math.round(100 * wins / n) + '%' : '-');
  set('man-avg', n ? _sgn(tot / n, 2) : '-');
  set('man-total', n ? _sgn(tot, 2) : '-');
  set('man-usd', n ? _sgn(Math.round(tot * _riskUsd()), 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '-');
}
function _syncBar(i, msg) {
  const bar = document.querySelector('.replay-bar[data-idx="' + i + '"]');
  if (!bar) return;
  const st = _rp(i), r = st.res;
  bar.querySelector('.rp-time').textContent = msg || (st.data ? 'replay at ' + _ptTime(st.T) : 'loading the replay data ...');
  bar.querySelector('.rp-end').textContent = 'runs to ' + _ptTime(st.end) + ' (' + CHARTS[i].endWhy + ')';
  bar.querySelector('.rp-trade').textContent = !PosTool.getBox(i) ? 'draw a Long / Short on any of the three panes'
    : !r ? '' : r.state === 'waiting' ? 'manual trade: waiting for the entry'
    : r.state === 'open' ? 'manual trade: open since ' + _ptTime(r.fillT, true) + ', ' + _sgn(r.r, 2) + 'R'
    : 'manual trade: ' + r.hit + ' ' + _ptTime(r.exitT, true) + ', ' + _sgn(r.r, 2) + 'R';
  const pb = bar.querySelector('[data-a="play"]');
  pb.textContent = st.play ? '❚❚ Pause' : '▶ Play'; pb.classList.toggle('on', !!st.play);
}
function _togglePlay(i) {
  const st = _rp(i);
  if (st.play) { clearInterval(st.play); st.play = null; _syncBar(i); return; }
  const bar = document.querySelector('.replay-bar[data-idx="' + i + '"]');
  const pane = bar.querySelector('[data-a="tf"]').value, ms = +bar.querySelector('[data-a="speed"]').value;
  st.play = setInterval(() => {
    const was = st.res ? st.res.state : null;
    _step(i, pane, 1);
    // pause at the end, and when the manual trade fills or ends
    if (st.T >= st.end || (st.res && st.res.state !== was && was != null)) { clearInterval(st.play); st.play = null; _syncBar(i); }
  }, ms);
  _syncBar(i);
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.replay-bar button');
  if (!btn) return;
  e.stopPropagation();
  const i = +btn.closest('.replay-bar').dataset.idx, a = btn.dataset.a, st = _rp(i);
  if (a === 'fwd') _step(i, btn.dataset.tf, 1);
  else if (a === 'back') _step(i, btn.dataset.tf, -1);
  else if (a === 'start') _setT(i, st.close);
  else if (a === 'end') _setT(i, st.end);
  else if (a === 'play') _togglePlay(i);
});
// arrow keys step the pane last clicked (its own candle length)
let _activePane = null;
document.addEventListener('keydown', (e) => {
  if (!_activePane || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName || '')) return;
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { _step(_activePane.i, _activePane.pane, e.key === 'ArrowRight' ? 1 : -1); e.preventDefault(); }
});

const Replay = {
  attach(chart, series, el, titleEl, cd) {
    const m = /^c-(\w+)-(\d+)$/.exec(el.id || '');
    if (!m || !(m[1] in _BLIND)) return;
    const pane = m[1], i = +m[2], st = _rp(i);
    st.panes[pane] = { chart, series, cd, hist: cd.candles };
    el.addEventListener('pointerdown', () => { _activePane = { i, pane }; }, true);
    const bar = titleEl.querySelector('.pos-bar') || titleEl;
    const fs = bar.querySelector('.fs-btn');
    [['◀', -1], ['▶', 1]].forEach(([txt, dir]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pos-btn'; b.textContent = txt;
      b.title = (dir > 0 ? 'Replay forward' : 'Replay back') + ' one ' + _TFN[pane] + ' candle (all three panes follow; arrow keys too)';
      b.addEventListener('click', (e) => { e.stopPropagation(); _step(i, pane, dir); });
      bar.insertBefore(b, fs || null);
    });
    _syncBar(i);
    _load(i, () => _apply(i));
  },
};

// ---- the automated trade's panes (M5 trade, M2-LXPB), hidden until asked for
const _autoDone = {};
function toggleAuto(i) {
  const wrap = document.getElementById('future-' + i), btn = document.querySelector('.reveal-btn[data-idx="' + i + '"]');
  const show = wrap.classList.contains('hidden');
  wrap.classList.toggle('hidden', !show);
  btn.textContent = show ? 'Hide the automated trade' : 'Show the automated trade (M5 trade, M2-LXPB: the whole replay window) ▼';
  if (show && !_autoDone[i]) { _autoDone[i] = true; _renderChartPane(i, 'l5', CHARTS[i].l5); _renderChartPane(i, 'l2', CHARTS[i].l2); }
  _load(i, () => { _evalRow(i); PosTool.refresh(i); });
}

PosTool.config.shared = Object.keys(_BLIND);
PosTool.config.mirror = ['l5', 'l2'];
PosTool.config.cursor = (i) => _rp(i).T;
PosTool.config.result = (i, pane) => (pane in _BLIND ? _rp(i).res : _rp(i).fin);
PosTool.config.onChange = (i) => {
  if (i == null) document.querySelectorAll('tr.lvl-row').forEach(tr => _fillRow(+tr.dataset.idx));
  else { _evalRow(i); PosTool.refresh(i); _fillRow(i); _syncBar(i); }
  _manualStats();
};
window.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('tr.lvl-row').forEach(tr => _fillRow(+tr.dataset.idx));
  _manualStats();
});
"""


def replay_window(m1, closed, levels):
    """(last M1 bar of the replay, why): at least MIN_FUTURE_M1 bars past the cross bar's close, on until every level in
    `levels` not already touched before the close has been revisited (+ REVISIT_PAD bars), at most MAX_FUTURE_M1 bars."""
    c0 = int(m1.index.searchsorted(closed))
    lo, hi = c0 + MIN_FUTURE_M1 - 1, c0 + MAX_FUTURE_M1 - 1
    live = levels[~(levels["retest_time"] < closed)]
    if live["retest_time"].isna().any():
        k, why = hi, "5 trading days: a P0 price of this cross bar is not revisited by then"
    elif len(live):
        k = int(m1.index.searchsorted(live["retest_time"].max())) + REVISIT_PAD
        why = f"every P0 price of this cross bar revisited ({len(live)} levels)"
        if k > hi:
            k, why = hi, "5 trading days: a P0 price of this cross bar is not revisited by then"
        elif k < lo:
            k, why = lo, f"1 trading day; every P0 price of this cross bar revisited sooner ({len(live)} levels)"
    else:
        k, why = lo, "1 trading day (no P0 price of this cross bar left to revisit)"
    k = min(k, len(m1) - 1)
    if k == len(m1) - 1:
        why += "; cut at the data's end"
    return m1.index[k], why


def s1_pack(s):
    """The 1 s price path for the page's trade walk: every second whose range is not inside the range of the second kept just
    before it within the same minute (a dropped second only repeats prices that second traded earlier, so the first touch of
    any price is the same; each minute starts afresh, so a watch starting on a minute loses nothing). Flat [dt s, low step,
    range] in ticks."""
    lo = np.rint(s["low"].to_numpy(float) / TICK).astype(np.int64)
    hi = np.rint(s["high"].to_numpy(float) / TICK).astype(np.int64)
    sec = s.index.asi8 // 1_000_000_000
    keep, kl, kh, km = [], 0, 0, -1
    for j in range(len(sec)):
        m = sec[j] // 60
        if m == km and lo[j] >= kl and hi[j] <= kh:
            continue
        keep.append(j)
        kl, kh, km = lo[j], hi[j], m
    keep = np.array(keep, dtype=np.int64)
    t, l, h = sec[keep], lo[keep], hi[keep]
    rows = np.column_stack([np.diff(t, prepend=t[0]), np.diff(l, prepend=l[0]), h - l])
    return {"t0": int(t[0]), "b": int(l[0]), "r": rows.ravel().tolist()}


# ---------------------------------------------------------------- page
HEAD_HTML = (
    "<th class=\"left\">#</th><th class=\"left\">Cross bar (H1 start)</th><th class=\"left\">Side</th>"
    "<th class=\"left\">Confirmed by</th><th>Swept</th>"
    "<th class=\"left\" title=\"M5 LXPB level traded: nearest to the swept price, or an earlier one price came back to first\">Entry level</th>"
    "<th class=\"left\">Fill</th><th title=\"1 tick beyond the cross bar; risk in points\">Stop / risk</th>"
    "<th title=\"Thrust-P1 trail steps fired\">Trails</th><th class=\"left\">Exit</th><th>R</th>"
    "<th title=\"Best open profit before the exit, in R\">Best R</th>"
    "<th class=\"left\" title=\"Every entry level on its own (no seat): entered at its first touch after the cross bar closed, same stop, which came first: the stop or +1R. No trail, no end-of-day flat. Hover a level for the numbers\">Levels: stop or 1R first</th>"
    "<th class=\"left man-th\" title=\"Your manual trade, drawn with Long / Short on the H1, M5 or M2 pane (one per row): side, entry price, and its fill once the replay reaches it\">Manual entry</th>"
    "<th class=\"man-th\" title=\"Stop / target and their reward to risk\">Manual stop / target</th>"
    "<th class=\"left man-th\" title=\"As of the row's replay time: the first second reaching the stop or the target after the fill, or still open (last price)\">Manual exit</th>"
    "<th class=\"man-th\">Manual R</th><th class=\"man-th\" title=\"R x the risk $ of the position tool's settings (gear)\">Manual P&amp;L $</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 22


def _row_key(row):
    return f"{row.Index.tz_convert('UTC').strftime('%Y-%m-%dT%H')}_{'bull' if row.bullish else 'bear'}"


def _row_html(i, row, tr, cells_extra, races):
    cls = "type-lhpb" if row.bullish else "type-llpb"
    key = _row_key(row)
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
  <td class="left man-entry"></td><td class="man-plan"></td><td class="left man-exit"></td><td class="man-r"></td><td class="man-usd"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="replay-bar" data-idx="{i}">
      <button type="button" data-a="start" title="Back to the cross bar's close">\u23ee</button>
      <button type="button" data-a="back" data-tf="m2" title="Back one M2 candle">\u25c0 M2</button>
      <button type="button" data-a="fwd" data-tf="m2" title="Forward one M2 candle">M2 \u25b6</button>
      <button type="button" data-a="fwd" data-tf="ctx" title="Forward one M5 candle">M5 \u25b6</button>
      <button type="button" data-a="fwd" data-tf="main" title="Forward one H1 candle">H1 \u25b6</button>
      <button type="button" data-a="play" title="Step forward on its own; pauses when your trade fills or ends"></button>
      <select data-a="tf" title="Play steps"><option value="m2">M2</option><option value="ctx" selected>M5</option><option value="main">H1</option></select>
      <select data-a="speed" title="Play speed"><option value="1000">1 / s</option><option value="400" selected>2.5 / s</option><option value="150">7 / s</option></select>
      <button type="button" data-a="end" title="Jump to the end of the replay window">\u23ed</button>
      <span class="rp-time"></span><span class="rp-trade"></span><span class="rp-end src-tag"></span>
    </div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-main-{i}"></div><div class="chart-ph" id="c-main-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-ctx-{i}"></div><div class="chart-ph" id="c-ctx-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-m2-{i}"></div><div class="chart-ph" id="c-m2-{i}"></div></div>
    <div class="reveal-bar"><button type="button" class="reveal-btn" data-idx="{i}" onclick="event.stopPropagation();toggleAuto({i})">Show the automated trade (M5 trade, M2-LXPB: the whole replay window) \u25bc</button></div>
    <div class="future-wrap hidden" id="future-{i}">
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l5-{i}"></div><div class="chart-ph" id="c-l5-{i}"></div></div>
    <div class="chart-cell chart-pane"><div class="chart-title" id="t-l2-{i}"></div><div class="chart-ph" id="c-l2-{i}"></div></div>
    </div>
  </div></td>
</tr>"""


def render(rows, h1, output, label):
    data_dir = os.path.splitext(output)[0] + "_data"
    os.makedirs(data_dir, exist_ok=True)
    lo = rows.index.min() - pd.Timedelta(days=30)
    m5 = load_es("5min", start=lo, tz=PT)
    led = CB.ledger(m5)
    m2 = load_es("2min", start=lo, tz=PT)
    led2 = CB.ledger(m2)
    m1 = load_es("1m", start=lo, tz=PT)[["open", "high", "low", "close"]]
    charts, trs, rs, all_races = [], [], [], []
    for i, row in enumerate(rows.itertuples()):
        older = CB.older_swings(h1, h1.index.get_loc(row.Index), row.swing_time, row.swing_price, row.bullish)
        chart = CB.build_example(h1, m5, row, older)
        chart["m2"] = blind_pane(m2, 2, "M2", row, older)
        closed = row.Index + pd.Timedelta(hours=1)
        cands = candidates(led, row)
        until, end_why = replay_window(m1, closed, pd.concat([cands, candidates(led2, row)]))
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
                ex = int(m5.index.searchsorted(res["exit_t"], side="right")) - 1
                auto_end = m5.index[min(len(m5) - 1, ex + AFTER_EXIT)] + FIVE - pd.Timedelta(minutes=1)
                if auto_end > until:
                    until, end_why = auto_end, end_why + "; 30 M5 candles past the automated trade's exit"
                near = cands.iloc[0]
                which = ("nearest" if (near["price"] == lv["price"] and near["formation_time"] == lv["formation_time"])
                         else f"earlier (nearest {float(near['price']):.2f})")
                tr = (lv, ft, fp, res, which)
                rs.append(res["r"])
                chart["l5"] = trade_pane(m5, row, cands, lv, ft, fp, res, spent, races, until)
                print(f"{CB._pt(row.Index)} {'long ' if row.bullish else 'short'} {which:28s} fill {fp:.2f} stop {res['stop0']:.2f} "
                      f"trails {len(res['trails'])} exit {res['exit_px']:.2f} {res['why']}: {res['r']:+.2f}R")
        if tr is None:
            chart["l5"] = future_pane(m5, 5, "M5 (no trade)", row, until)
            print(f"{CB._pt(row.Index)} no trade: {why}")
        lv2 = CB.pick_level(led2, row.Index, row.swing_price, row.bullish)
        chart["l2"] = (future_pane(m2, 2, "M2 (no M2 level)", row, until) if lv2 is None else
                       CB.level_pane(m2, 2, "M2", lv2, row, CB.BULL if row.bullish else CB.BEAR, led2, until=until))
        # the replay's data file: M1 bars and the 1 s path from the M5 pane's first candle (the earliest a box on M5 / M2 can
        # start its watch; one on an older H1 candle starts there too) to the end of the replay window
        start = pd.Timestamp(chart["ctx"]["candles"][0]["time"], unit="s", tz="UTC").tz_convert(PT)
        stop = until + pd.Timedelta(minutes=1)
        key = _row_key(row)
        with open(os.path.join(data_dir, key + ".js"), "w", encoding="utf-8") as f:
            f.write("__replayData(" + json.dumps(key) + "," + json.dumps(
                {"m1": m1_pack(m1.loc[start: until]), "s1": s1_pack(s1_between(start, stop))}, separators=(",", ":")) + ");\n")
        chart["close"], chart["end"], chart["endWhy"] = int(closed.timestamp()), int(stop.timestamp()), end_why
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
    hook = "_touchPriceAxis(chart, el, series, rayInfo.map(r => r.series), queueThin);"
    data_rel = os.path.basename(data_dir)
    with open(POS_TOOL_JS, encoding="utf-8") as f:
        pos_tool = f.read()
    js = CB._js(charts, storage_key)
    assert js.count(hook) == 1, "M1 report chart JS changed: re-check the position tool hook"
    js = "<script>\n" + pos_tool + "\n</script>\n" + js.replace(
        hook, hook + "\n  if (window.PosTool) PosTool.attach(chart, series, el, titleEl, cd);"
                     + "\n  if (window.Replay) Replay.attach(chart, series, el, titleEl, cd);")
    old_stack = "_renderChartPane(i, 'l5', cd.l5);\n  _renderChartPane(i, 'l2', cd.l2);"
    assert js.count(old_stack) == 1, "cross bar report JS changed: re-check _renderStack"
    js = js.replace(old_stack, "_renderChartPane(i, 'm2', cd.m2);")
    js = js + "<script>\n" + MANUAL_JS.replace("__DATA_DIR__", data_rel) + "\nwindow.Replay = Replay;\n</script>\n"
    js = js.replace("l5: 'M5-LXPB'", "m2: 'M2', l5: 'M5 trade'").replace(
        "(no LXPB level of this direction beyond the swept price with its P0 formed inside the cross bar hour)",
        "(no trade / no level of this direction beyond the swept price with its P0 inside the cross bar hour)")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{CB.M.CSS}
{CB.NOTES_CSS}
{MANUAL_CSS}
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
<p class="lead">FORWARD REPLAY. The H1, M5 and M2 panes start at the cross bar's close. Step forward with the replay bar (M2 / M5 / H1
candle, Play, back, start, end), the &#9664; &#9654; buttons on each pane or the arrow keys (the pane last clicked): all three move to
the same time, and the other panes show their last candle still forming (one M5 step = 2.5 M2 candles and a twelfth of an H1
candle). The window runs at least one trading day past the cross bar and on until every P0 price of the cross bar (its M5 and M2
entry levels) has been revisited, at most five trading days. Draw your trade with Long / Short on any of the three (one per row,
shown on all of them; a new one replaces the old). Its entry is a PRICE: entered when price trades there again after the candle
you drew it on (from the replay time when that candle is still forming). Judged on the 1 s price path: the first second reaching
the stop or the target after the fill ends it (a second reaching both counts as the stop; no end-of-day flat). The Manual columns
show it as of the row's replay time (open = last price), and the manual boxes above count the trades closed so far. Manual R =
(exit - fill) / (entry - stop), P&amp;L $ = R x the risk $ in the tool's settings (gear). The automated trade's M5 and M2 panes stay
hidden under the charts until you ask for them. Boxes, replay times and settings are kept in this browser.</p>
<div class="summary">
  <div class="box"><strong>{len(rows)}</strong>cross bars</div>
  <div class="box"><strong>{n}</strong>trades</div>
  <div class="box"><strong>{(wins / n if n else 0):.0%}</strong>win rate</div>
  <div class="box"><strong>{(sum(rs) / n if n else 0):+.2f}</strong>avg R</div>
  <div class="box"><strong>{sum(rs):+.2f}</strong>total R</div>
  <div class="box"><strong>{one_r}/{len(decided)}</strong>levels 1R first ({pct(one_r, len(decided))})</div>
  <div class="box"><strong>{near_1r}/{len(near)}</strong>nearest levels 1R first</div>
  <div class="box"><strong>{earl_1r}/{len(earl)}</strong>earlier levels 1R first</div>
  <div class="box man-box"><strong id="man-n">0</strong>manual trades closed</div>
  <div class="box man-box"><strong id="man-open">0</strong>manual open</div>
  <div class="box man-box"><strong id="man-wait">0</strong>manual waiting for entry</div>
  <div class="box man-box"><strong id="man-win">-</strong>manual win rate</div>
  <div class="box man-box"><strong id="man-avg">-</strong>manual avg R</div>
  <div class="box man-box"><strong id="man-total">-</strong>manual total R</div>
  <div class="box man-box"><strong id="man-usd">-</strong>manual P&amp;L $</div>
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
