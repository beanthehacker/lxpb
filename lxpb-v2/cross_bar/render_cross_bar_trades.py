"""
render_cross_bar_trades.py
==========================
CROSS BAR TRADES: a forward-replay page of the cross bars (see render_cross_bar_report.py for the cross bar itself), in the M1
fade report's page layout with Reviewed / Valid / Notes, for MANUAL trades (FORWARD REPLAY below). Rows oldest first.

The automated trade (user, 2026-10-29; candidates / pick_trade / manage / race) is no longer shown on the page (user, 2026-11-03:
no automated entries / exits, no "stop or 1R first" column, no M5 trade / M2-LXPB panes). It is kept here for
level_race_trail.py and the numbers in CLAUDE.md:
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

FORWARD REPLAY (user, 2026-11-03): one chart per row (and an optional second one) whose timeframe switches between D1 / H4 /
H1 / M30 / M15 / M5 / M2 / M1 (TradingView's D1 and H4 for ES: the session 15:00 PT to 14:00 PT, H4 from 15:00 PT); it opens on
H1. It starts INSIDE the cross bar's hour, at the close of the first M2 candle that closes back above the swept swing low (below
the swept high for a bearish bar) after price traded below it (above it), the reclaim (replay_start): the cross bar is still
forming then and may not end up confirmed. It steps forward one candle of its timeframe at a time, its candles as they stood at
the replay time (the last one still forming), built in the page from the row's H1 bars before the cross bar's hour (history of
H1 and up) and M1 bars (history of M30 and below, and everything from the cross bar's hour on). The replay window runs at least MIN_FUTURE_M1 M1 bars past the close, on until every P0 price of
the cross bar (its M5 and M2 entry levels not touched before the close) has been revisited, at most MAX_FUTURE_M1 bars, and on
until the farthest swing price (the swept swing and the older swings) is traded again, at most MAX_SWING_M1 bars (replay_window). The long / short position tool (public/js/position-tool.js) keeps ONE manual trade per row, in time and price, so
it stays put through a timeframe switch. It is judged on the 1 s price path: entered when price trades at its entry price again
after the candle it was drawn on (or from the replay time when that candle is still forming), then the first second reaching its
stop or target ends it (a second reaching both = the stop; no end-of-day flat). The H1 / M1 bars and the path of each row are in
its own file next to the page (<page name>_data/<row key>.js), loaded when the row opens.

NQ CHART (user, 2026-11-10): an NQ chart beside the ES one (side by side; one column on a narrow screen), same timeframes and candle building, from
E:\data_source (nq_data.load_nq: NQ continuous, back-adjusted exactly as TradingView's NQ1!; a row whose stretch has no NQ 1-minute
bars there shows a note instead). Each row's
data file carries NQ H1 / M1 bars over the same stretches as ES. "NQ sync" (on by default, kept in the browser): on = NQ replays
at the ES time and stepping either chart moves both, and the ES chart (not the 2nd chart) and NQ share their timeframe: picking
H1 / M5 ... on either switches both (user, 2026-11-11); off = NQ keeps its own time and timeframe (stepped from its own chart, or
the replay bar / arrow keys after clicking it) and ES stays put; back on = NQ jumps to the ES time and timeframe. NQ shows only where the ES cross bar is (its
swing prices are ES prices); the position tool stays on the ES charts. NQ has drawings of its own (user, 2026-11-11: kept apart
from the ES ones, NQ prices) and its own volume profile from the NQ ticks (vap.py sym="NQ"), with the VP's point sizes (last-visit
band, smoothing, confluence) times NQ / ES at the cross bar's hour, its volume in 1-point rows (a quarter of the data).
LAST VISIT -> NOW (user, 2026-11-11): when the last visit was the previous trading day, the profile starts at the earlier of the
visit before it and 5 trading days before today.
FREE MOVEMENT (user, 2026-11-11): every chart pans freely in both directions with the mouse. The first press on a chart takes
its price axis off auto-fit (the range on screen is kept), as dragging both axes used to; double-click the price axis to auto-fit
again. A finger keeps the touch rules (a vertical drag scrolls the page, the axis strip stretches).
CROSSHAIR SYNC (user, 2026-11-11): hovering the ES chart puts NQ's crosshair on the candle holding the same time, at the same
height, and the other way round (none past the other chart's last candle). The 2nd ES chart is not linked.

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
import vap                                        # noqa: E402  (volume at price from the .scid ticks, for the volume profiles)
from nq_data import load_nq                       # noqa: E402  (E:\data_source: NQ continuous, for the NQ chart)

PT = CB.PT
TICK = 0.25
FIVE = pd.Timedelta(minutes=5)
MIN_WICK_RATIO = 0.20                 # trade_management.MIN_WICK_RATIO
EOD_FLAT = pd.Timedelta(hours=12, minutes=45)
EOD_LEAD = pd.Timedelta(minutes=1)
REOPEN = pd.Timedelta(hours=15)
MIN_FUTURE_M1 = 1380                  # replay window: at least this many M1 bars (one trading day, 23 h) past the cross bar ...
MAX_FUTURE_M1 = 5 * 1380              # ... at most five trading days for the P0 prices
MAX_SWING_M1 = 20 * 1380              # ... and on until the farthest swept / older swing price is traded again, at most 20 (user 2026-11-11)
H1_HISTORY = pd.Timedelta(days=150)   # H1 bars before the close in a row's data file (D1 / H4 / H1 history: ~100 D1 candles)
M1_HISTORY = 6 * 1380                 # M1 bars before the close (M30 and below: ~270 M30 candles; user 2026-11-09: was 3 days)
VISIT_BAND = 50.0                     # ... both reach back at least to the last visit of the day's open +- this many points
S1_HISTORY = 100                      # the 1 s path starts this many M5 candles before the cross bar's hour
REVISIT_PAD = 60                      # ... and this many M1 bars past the last P0 price's revisit
VP_JS = os.path.join(CB._REPO_ROOT, "public", "js", "volume-profile.js")     # volume profiles (sample, --vp)
VP_COARSE = 1800                      # volume at price per 30 minutes before the M1 history, per minute from there on
VP_DAYS = 35                          # ... reaching back at least this many days (weekly levels, 20-day composite)
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


def m1_pack(bars):
    """Bars for the page's replay (M1 or H1), compact: flat [minute step, open, high, low, close], prices in ticks from a base."""
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
.pos-btn.tf-btn { padding: 0 4px; }
.pane-b.hidden, .pane-n.hidden { display: none; }
/* ES and NQ side by side (one column on a narrow screen, or when NQ is hidden) */
.es-nq-row { display: grid; grid-template-columns: 1fr; gap: 8px; }
.es-nq-row.two { grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); }
.es-nq-row > .chart-cell { min-width: 0; }
/* the plot takes whatever height the title bar (one or two lines of buttons) and the notes strip leave, so the time axis
   is never cut off */
.chart-stack .chart-cell { display: flex; flex-direction: column; }
.chart-stack .chart-cell > .chart-ph { flex: 1 1 0; min-height: 0; height: auto !important; }
td.man-entry, td.man-exit { white-space: nowrap; }
</style>"""

MANUAL_JS = r"""
// FORWARD REPLAY. Each row has one chart (and an optional second one) whose timeframe switches between D1 / H4 / H1 / M30 /
// M15 / M5 / M2 / M1 (opens on H1). It starts inside the cross bar's hour, at the close of the first M2 candle back past the
// swept swing (cd.start; the cross bar may not end up confirmed), and steps forward one candle of its own timeframe at a time;
// whatever the timeframe, the candles are the ones that stood at the replay time T (the last one still forming), built in the
// page from the row's H1 bars before the cross bar's hour (history of the H1 / H4 / D1 views) and M1 bars (everything from the
// cross bar's hour on, and the history of M30 and below). D1 and H4 candles are TradingView's for ES: a D1 candle is the session from 15:00 PT to 14:00 PT the next day, H4
// candles start at 15:00 PT (15, 19, 23, 03, 07, 11). The row's one manual trade (the position tool's box, kept in time and price,
// so it stays put through a timeframe switch) is judged on the row's 1 s price path up to T: entered when price trades at its
// entry price from the box's `from` on, then the first second reaching the stop or the target ends it (a second reaching both =
// the stop).
const _DATA_DIR = '__DATA_DIR__';
const _TFS = ['D1', 'H4', 'H1', 'M30', 'M15', 'M5', 'M2', 'M1'];
const _LEN = { D1: 86400, H4: 14400, H1: 3600, M30: 1800, M15: 900, M5: 300, M2: 120, M1: 60 };
const _DEF_TF = { a: 'H1', b: 'M5', n: 'H1' };
let _SYNC = true;                                           // the NQ chart replays with ES (localStorage, below)
const _NEAR = 400;                                          // candles in view when a chart opens or switches timeframe
const _BLANK = 0.2;                                         // ... and the share of the chart left blank right of the last one
const _RP = {};
const _FILE = location.pathname.split('/').pop();
const _key = (i) => ((document.querySelector('tr.lvl-row[data-idx="' + i + '"]') || {}).dataset || {}).key;
const _ls = (k, d) => { try { const v = localStorage.getItem('postool_v1_' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
const _lsSet = (k, v) => { try { localStorage.setItem('postool_v1_' + k, JSON.stringify(v)); } catch (e) { /* private window */ } };
function _rp(i) {
  if (!_RP[i]) {
    const cd = CHARTS[i];
    const clampT = (t) => Math.max(cd.start, Math.min(cd.end, t));
    _RP[i] = { start: cd.start, end: cd.end, T: clampT(_ls('cursor2_' + _FILE + '_' + _key(i), cd.start)),
               data: null, panes: {}, hist: {}, res: null, play: null,
               tf: { a: _DEF_TF.a, b: _DEF_TF.b, n: _DEF_TF.n } };   // every row opens on H1 (2nd chart M5, NQ H1), whatever was picked before
    _RP[i].Tn = _SYNC ? _RP[i].T : clampT(_ls('cursorN_' + _FILE + '_' + _key(i), _RP[i].T));   // the NQ chart's own time (sync off)
  }
  return _RP[i];
}
_SYNC = _ls('nq_sync_' + _FILE, true);
const _lb = (a, t) => { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < t) lo = m + 1; else hi = m; } return lo; };

// ---- PT clock: D1 / H4 candles are anchored at 15:00 PT
const _offFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', hourCycle: 'h23', year: 'numeric',
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const _offCache = {};
function _ptOff(t) {                                        // PT minus UTC in seconds (-8 h or -7 h); DST changes on a closed Sunday
  const d = Math.floor(t / 86400);
  if (d in _offCache) return _offCache[d];
  const p = {};
  _offFmt.formatToParts(new Date(t * 1000)).forEach(x => { p[x.type] = x.value; });
  return (_offCache[d] = Date.UTC(+p.year, p.month - 1, +p.day, +p.hour, +p.minute, +p.second) / 1000 - t);
}
function _bucket(t, tf) {                                   // start of the tf candle holding time t
  const len = _LEN[tf];
  if (len <= 3600) return Math.floor(t / len) * len;
  const off = _ptOff(t), loc = t + off, a = 15 * 3600;
  return Math.floor((loc - a) / len) * len + a - off;
}

// ---- the row's data file: H1 bars (before the cross bar's hour), M1 bars and the 1 s path (seconds whose range is not inside the one
// kept before it, that minute)
function _bars(m) {
  const out = [];
  let t = m.t0;
  for (let k = 0; k < m.r.length; k += 5) {
    t += m.r[k] * 60;
    out.push({ time: t, open: (m.b + m.r[k + 1]) * 0.25, high: (m.b + m.r[k + 2]) * 0.25, low: (m.b + m.r[k + 3]) * 0.25,
               close: (m.b + m.r[k + 4]) * 0.25 });
  }
  return out;
}
function _decode(d) {
  const M = _bars(d.m1), H = _bars(d.h1);
  const s = d.s1, n = s.r.length / 3, T = new Float64Array(n), L = new Float64Array(n), Hi = new Float64Array(n);
  let ts = s.t0, lo = s.b;
  for (let k = 0; k < n; k++) { ts += s.r[3 * k]; lo += s.r[3 * k + 1]; T[k] = ts; L[k] = lo * 0.25; Hi[k] = (lo + s.r[3 * k + 2]) * 0.25; }
  const q = d.nq && d.nq.m1 ? { m1: _bars(d.nq.m1), h1: d.nq.h1 ? _bars(d.nq.h1) : [], vp: d.nq.vp } : null;
  if (q) { q.m1t = q.m1.map(c => c.time); q.h1t = q.h1.map(c => c.time); }
  return { m1: M, m1t: M.map(c => c.time), h1: H, h1t: H.map(c => c.time), s1: { n, t: T, lo: L, hi: Hi }, vp: d.vp, nq: q };
}
// the NQ chart (pane 'n') reads the row's NQ bars and, with sync off, its own replay time
const _isN = (pane) => pane === 'n';
const _src = (i, pane) => (_isN(pane) ? _rp(i).data.nq : _rp(i).data);
const _Tof = (i, pane) => (_isN(pane) && !_SYNC ? _rp(i).Tn : _rp(i).T);
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

// ---- candles of one timeframe at T
function _agg(src, srcT, a, b, tf, out, own) {              // bars of src with a <= time < b into tf candles appended to out
  let k = a == null ? 0 : _lb(srcT, a);
  for (; k < src.length && (b == null || src[k].time < b); k++) {
    const c = src[k], bt = _bucket(c.time, tf);
    let last = out[out.length - 1];
    if (last && last.time === bt) {
      if (own.from > out.length - 1) { last = out[out.length - 1] = Object.assign({}, last); own.from = out.length - 1; }
      last.high = Math.max(last.high, c.high); last.low = Math.min(last.low, c.low); last.close = c.close;
    } else out.push({ time: bt, open: c.open, high: c.high, low: c.low, close: c.close });
  }
}
function _hist(i, tf, pane) {                               // the tf candles before the cross bar's hour (H1 bars for H1 and up, else M1)
  const st = _rp(i), h0 = CHARTS[i].crossT, k = (_isN(pane) ? 'n' : '') + tf;
  if (!st.hist[k]) {
    const out = [], D = _src(i, pane);
    if (_LEN[tf] >= 3600 && D.h1.length) _agg(D.h1, D.h1t, null, h0, tf, out, { from: 0 });
    else _agg(D.m1, D.m1t, null, h0, tf, out, { from: 0 });   // (NQ with no H1 history: its M1 bars)
    st.hist[k] = out;
  }
  return st.hist[k];
}
function _barsAt(i, tf, pane) {                             // pane 'n' = NQ; anything else (or none) = ES
  const D = _src(i, pane), h = _hist(i, tf, pane), out = h.slice();
  _agg(D.m1, D.m1t, CHARTS[i].crossT, _Tof(i, pane), tf, out, { from: h.length });   // a D1 / H4 candle open at the hour goes on
  return out;
}

// ---- one chart: candles + the cross bar's marks for its timeframe
function _paneCd(i, pane) {
  const st = _rp(i), m = CHARTS[i], tf = st.tf[pane], bars = _barsAt(i, tf, pane), len = _LEN[tf];
  const times = bars.map(c => c.time);
  const snap = (t) => times[Math.max(0, _lb(times, _bucket(t, tf) + 1) - 1)];
  const up = m.bull, side = up ? 'belowBar' : 'aboveBar';
  if (_isN(pane)) {                                         // NQ: only where the ES cross bar is (its prices are ES prices)
    const cdN = { title: 'NQ·' + tf, tfName: tf, barSec: len, candles: bars, priceLines: [], rays: [], notes: [], precision: 2, dateOnly: false,
                  markers: len >= 3600 ? [{ time: snap(m.crossT), position: side, color: m.color, shape: up ? 'arrowUp' : 'arrowDown', text: 'ES cross bar', size: 2 }] : [],
                  vlines: len < 3600 ? [{ time: snap(m.crossT), color: m.color, label: 'ES H1 cross bar ' + m.crossLabel }] : [] };
    cdN.fit = (chart) => { const n = (cdN.live || cdN.candles).length; chart.timeScale().setVisibleLogicalRange({ from: n - _NEAR, to: n + _NEAR * _BLANK / (1 - _BLANK) }); };
    return cdN;
  }
  const markers = [];
  if (len >= 3600) markers.push({ time: snap(m.crossT), position: side, color: m.color, shape: up ? 'arrowUp' : 'arrowDown', text: 'cross bar', size: 2 });
  else markers.push({ time: snap(m.sweepT), position: side, color: m.color, shape: 'circle', text: 'swept' });   // first candle past the swing
  if (m.swingT >= times[0]) markers.push({ time: snap(m.swingT), position: side, color: m.swingColor, shape: 'circle', text: 'swing ' + (up ? 'low' : 'high') });
  m.older.forEach(o => { if (o[0] >= times[0]) markers.push({ time: snap(o[0]), position: side, color: m.swingColor, shape: 'circle', text: 'older' }); });
  markers.sort((a, b) => a.time - b.time);
  const lastBar = times[times.length - 1];
  const ray = (t, px, style, title, label) => {
    const a = snap(Math.max(t, times[0]));
    return { color: m.swingColor, lineWidth: 1, lineStyle: style, title, priceLabel: true, label,
             points: a < lastBar ? [{ time: a, value: px }, { time: lastBar, value: px }] : [{ time: lastBar, value: px }] };
  };
  const rays = [ray(m.swingT, m.swingPx, 2, 'swept level', m.sweptLabel)]
    .concat(m.older.map(o => ray(o[0], o[1], 3, 'older swing', o[3])));
  const vlines = len < 3600 ? [{ time: snap(m.crossT), color: m.color, label: 'H1 cross bar ' + m.crossLabel }] : [];
  const cd = { title: tf, tfName: tf, barSec: len, candles: bars, markers, priceLines: [], rays, vlines, notes: m.notes,
               precision: 2, dateOnly: false };
  cd.fit = (chart) => {
    const n = (cd.live || cd.candles).length;
    chart.timeScale().setVisibleLogicalRange({ from: n - _NEAR, to: n + _NEAR * _BLANK / (1 - _BLANK) });
  };
  return cd;
}
function _renderTf(i, pane) {
  const st = _rp(i), old = st.panes[pane];
  let el = document.getElementById('c-' + pane + '-' + i);
  if (!el || !st.data) return;
  if (_isN(pane) && !st.data.nq) {
    const t = document.getElementById('t-n-' + i);
    if (t) t.textContent = 'NQ  |  no NQ 1-minute bars for this row in E:\\data_source';
    el.style.height = '0px';
    return;
  }
  if (old) {                                                // a fresh plot element: the old chart's listeners go with it
    try { old.chart.remove(); } catch (e) { /* already gone */ }
    const fresh = el.cloneNode(false);
    el.replaceWith(fresh);
    el = fresh;
    const nd = el.previousElementSibling;
    if (nd && nd.classList.contains('pane-notes')) nd.remove();
  }
  _renderPane(el.id, 't-' + pane + '-' + i, _paneCd(i, pane));
}
function _apply(i) {
  const st = _rp(i);
  Object.keys(st.panes).forEach(pane => {
    const p = st.panes[pane], bars = _barsAt(i, st.tf[pane], pane);
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
function _setT(i, T, pane) {                                // pane 'n' with sync off moves the NQ chart only; anything else moves ES (and NQ when synced)
  const st = _rp(i);
  T = Math.max(st.start, Math.min(st.end, T));
  if (_isN(pane) && !_SYNC) { st.Tn = T; _lsSet('cursorN_' + _FILE + '_' + _key(i), T); }
  else {
    st.T = T;
    _lsSet('cursor2_' + _FILE + '_' + _key(i), T);
    if (_SYNC) st.Tn = T;
  }
  _apply(i);
}
function _stepTf(i, tf, dir, pane) {
  const st = _rp(i);
  if (!st.data) return;
  const D = _src(i, pane) || st.data, T0 = _Tof(i, pane);
  let T = dir > 0 ? _bucket(T0, tf) + _LEN[tf] : _bucket(T0 - 1, tf);
  if (dir > 0) {                                  // no bars in that step (market closed): on to the end of the candle the next bar is in
    const nx = D.m1[_lb(D.m1t, T0)];
    if (nx && nx.time >= T) T = _bucket(nx.time, tf) + _LEN[tf];
  }
  _setT(i, T, pane);
}
function _setTf(i, pane, tf) {                             // with NQ sync on, the ES chart and NQ share their timeframe (the 2nd chart keeps its own)
  const st = _rp(i);
  if (st.tf[pane] === tf) return;
  st.tf[pane] = tf;
  _renderTf(i, pane);
  const mate = !_SYNC ? null : pane === 'a' ? 'n' : pane === 'n' ? 'a' : null;
  if (mate && st.tf[mate] !== tf) { st.tf[mate] = tf; if (st.panes[mate]) _renderTf(i, mate); }
  _syncBar(i);
}
function _showB(i, on) {
  _lsSet('show_b_' + _FILE, on);
  const w = document.getElementById('pb-' + i);
  if (w) w.classList.toggle('hidden', !on);
  if (on && !_rp(i).panes.b) _renderTf(i, 'b');
  _syncBar(i);
}
function _showN(i, on) {
  _lsSet('show_n_' + _FILE, on);
  const w = document.getElementById('pn-' + i);
  if (w) w.classList.toggle('hidden', !on);
  const row = document.getElementById('enr-' + i);
  const st = _rp(i);
  if (row) row.classList.toggle('two', on && !!(st.data && st.data.nq));   // side by side; ES full width when NQ is hidden or has no data
  if (on && !_rp(i).panes.n) _renderTf(i, 'n');
  _syncBar(i);
}
function _setSync(on) {                                     // every row: sync on brings NQ to the ES time
  _SYNC = on;
  _lsSet('nq_sync_' + _FILE, on);
  Object.keys(_RP).forEach(k => {
    const st = _RP[k];
    const n = on && st.tf.n !== st.tf.a;
    if (on) { st.Tn = st.T; st.tf.n = st.tf.a; }          // ... and takes the ES chart's timeframe
    if (st.data) { if (n && st.panes.n) _renderTf(+k, 'n'); _apply(+k); } else _syncBar(+k);
  });
}
function _openRow(i) {
  _syncBar(i);
  _load(i, () => {
    _renderTf(i, 'a');
    if (_ls('show_b_' + _FILE, false)) _showB(i, true);
    if (_ls('show_n_' + _FILE, true)) _showN(i, true);
    _apply(i);
  });
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
  if (st.data) _lsSet('state_' + _FILE + '_' + _key(i), st.res ? { T: st.T, res: st.res } : null);
}
const _cached = (i) => { const st = _rp(i); return st.data ? (st.res ? { T: st.T, res: st.res } : null) : _ls('state_' + _FILE + '_' + _key(i), null); };

// ---- table cells, stats, replay bar
const _f2 = (v) => v.toFixed(2);
const _ptTime = (t, sec) => new Date(t * 1000).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', weekday: 'short',
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: sec ? '2-digit' : undefined, hour12: false }) + ' PT';
const _riskUsd = () => PosTool.settings.account * PosTool.settings.riskPct / 100;
const _sgn = (v, d) => (v >= 0 ? '+' : '') + v.toFixed(d);
const _usd = (v) => _sgn(Math.round(v), 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
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
                                                           : 'drawn on ' + b.pane) + '</span>';
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
    cell('man-usd').innerHTML = '<em style="color:' + rc + '">' + _usd(usd) + '</em>';
    return;
  }
  cell('man-exit').innerHTML = _f2(r.exitPx) + '<br><span class="src-tag">' + _ptTime(r.exitT, true) + ' &middot; ' + r.hit + '</span>';
  cell('man-r').innerHTML = '<strong style="color:' + rc + '">' + _sgn(r.r, 2) + '</strong>';
  cell('man-usd').innerHTML = '<span style="color:' + rc + '">' + _usd(usd) + '</span>';
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
  set('man-usd', n ? _usd(tot * _riskUsd()) : '-');
}
let _xhBusy = false;                                        // a crosshair being copied to the other chart (no echo back)
let _activePane = null;                                     // { i, pane }: the chart the arrow / number keys and the bar's arrows act on
function _paneOf(i) { return _activePane && _activePane.i === i ? _activePane.pane : 'a'; }
function _syncBar(i, msg) {
  const bar = document.querySelector('.replay-bar[data-idx="' + i + '"]');
  if (!bar) return;
  const st = _rp(i), r = st.res, tf = st.tf[_paneOf(i)];
  const wn = document.getElementById('pn-' + i), nOn = !!(wn && !wn.classList.contains('hidden') && st.data && st.data.nq);
  bar.querySelector('.rp-time').textContent = msg || (!st.data ? 'loading the replay data ...'
    : !_SYNC && nOn ? 'ES at ' + _ptTime(st.T) + ' · NQ at ' + _ptTime(st.Tn) : 'replay at ' + _ptTime(st.T));
  bar.querySelector('.rp-end').textContent = 'runs to ' + _ptTime(st.end) + ' (' + CHARTS[i].endWhy + ')';
  bar.querySelector('.rp-trade').textContent = !PosTool.getBox(i) ? 'draw a Long / Short on the chart'
    : !r ? '' : r.state === 'waiting' ? 'manual trade: waiting for the entry'
    : r.state === 'open' ? 'manual trade: open since ' + _ptTime(r.fillT, true) + ', ' + _sgn(r.r, 2) + 'R'
    : 'manual trade: ' + r.hit + ' ' + _ptTime(r.exitT, true) + ', ' + _sgn(r.r, 2) + 'R';
  bar.querySelector('[data-a="back"]').textContent = '◀ ' + tf;
  bar.querySelector('[data-a="fwd"]').textContent = tf + ' ▶';
  const pb = bar.querySelector('[data-a="play"]');
  pb.textContent = st.play ? '❚❚ Pause' : '▶ Play'; pb.classList.toggle('on', !!st.play);
  const w = document.getElementById('pb-' + i), on = w && !w.classList.contains('hidden');
  bar.querySelector('[data-a="second"]').textContent = on ? '− 2nd chart' : '+ 2nd chart';
  bar.querySelector('[data-a="nq"]').textContent = wn && !wn.classList.contains('hidden') ? '− NQ chart' : '+ NQ chart';
  const sb = bar.querySelector('[data-a="sync"]');
  sb.textContent = _SYNC ? 'NQ sync on' : 'NQ sync off'; sb.classList.toggle('on', _SYNC);
  Object.keys(st.panes).forEach(p => document.querySelectorAll('#t-' + p + '-' + i + ' .tf-btn')
    .forEach(b => b.classList.toggle('pos-on', b.dataset.tf === st.tf[p])));
}
function _togglePlay(i) {
  const st = _rp(i);
  if (st.play) { clearInterval(st.play); st.play = null; _syncBar(i); return; }
  const bar = document.querySelector('.replay-bar[data-idx="' + i + '"]');
  const tf = bar.querySelector('[data-a="tf"]').value, ms = +bar.querySelector('[data-a="speed"]').value, pane = _paneOf(i);
  st.play = setInterval(() => {
    const was = st.res ? st.res.state : null;
    _stepTf(i, tf, 1, pane);                      // the chart last clicked (NQ alone when it is NQ and sync is off)
    // pause at the end, and when the manual trade fills or ends
    if (_Tof(i, pane) >= st.end || (st.res && st.res.state !== was && was != null)) { clearInterval(st.play); st.play = null; _syncBar(i); }
  }, ms);
  _syncBar(i);
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.replay-bar button');
  if (!btn) return;
  e.stopPropagation();
  const i = +btn.closest('.replay-bar').dataset.idx, a = btn.dataset.a, st = _rp(i), pane = _paneOf(i), tf = st.tf[pane];
  if (a === 'fwd') _stepTf(i, tf, 1, pane);
  else if (a === 'back') _stepTf(i, tf, -1, pane);
  else if (a === 'start') _setT(i, st.start, pane);
  else if (a === 'end') _setT(i, st.end, pane);
  else if (a === 'play') _togglePlay(i);
  else if (a === 'second') { const w = document.getElementById('pb-' + i); _showB(i, w.classList.contains('hidden')); }
  else if (a === 'nq') { const w = document.getElementById('pn-' + i); _showN(i, w.classList.contains('hidden')); }
  else if (a === 'sync') _setSync(!_SYNC);
});
// arrow keys step the chart last clicked by its own candle; 1-8 switch its timeframe (D1 ... M1)
document.addEventListener('keydown', (e) => {
  if (!_activePane || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName || '') || e.ctrlKey || e.metaKey || e.altKey) return;
  const { i, pane } = _activePane, st = _rp(i);
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { _stepTf(i, st.tf[pane], e.key === 'ArrowRight' ? 1 : -1, pane); e.preventDefault(); }
  else if (/^[1-8]$/.test(e.key)) { _setTf(i, pane, _TFS[+e.key - 1]); e.preventDefault(); }
});

const Replay = {
  attach(chart, series, el, titleEl, cd) {
    const m = /^c-(a|b|n)-(\d+)$/.exec(el.id || '');
    if (!m) return;
    const pane = m[1], i = +m[2], st = _rp(i);
    st.panes[pane] = { chart, series, cd };
    if (cd.tfName === 'D1') {                       // a D1 candle starts 15:00 PT the day before: label it by its trading day
      const f = (t) => timeFmtD.format(new Date((t + 9 * 3600) * 1000));
      chart.applyOptions({ timeScale: { tickMarkFormatter: f }, localization: { timeFormatter: f } });
    }
    el.addEventListener('pointerdown', (e) => {
      _activePane = { i, pane }; _syncBar(i);
      // free movement (user, 2026-11-11): the first mouse press takes the price axis off auto-fit, keeping the range on screen,
      // so a drag moves the chart up / down as well as sideways (what dragging both axes used to unlock). Double-click the
      // price axis to auto-fit again. Not for a finger: there a vertical drag scrolls the page and the axis strip stretches.
      if (e.pointerType !== 'touch') chart.priceScale('right').applyOptions({ autoScale: false });
    }, true);
    // crosshair sync (user, 2026-11-11): hovering the ES chart puts NQ's crosshair on the candle holding the same time, at the
    // same height, and the other way round (NQ's own timeframe and replay time when sync is off; past the other's last candle = none)
    chart.subscribeCrosshairMove((p) => {
      const mate = pane === 'a' ? 'n' : pane === 'n' ? 'a' : null, M = mate && st.panes[mate];
      if (_xhBusy || !M) return;
      _xhBusy = true;
      try {
        const c = M.cd.live || M.cd.candles, t = p.time;
        let lo = 0, hi = c.length;                  // the last candle starting at or before t
        while (lo < hi) { const k = (lo + hi) >> 1; if (c[k].time <= t) lo = k + 1; else hi = k; }
        const bar = c[lo - 1], px = p.point ? M.series.coordinateToPrice(p.point.y) : null;
        const oh = document.querySelector('#t-' + mate + '-' + i + ' .ct-ohlc');   // its title shows that candle's OHLC
        if (t == null || !bar || px == null || t >= bar.time + M.cd.barSec) { M.chart.clearCrosshairPosition(); if (oh) oh.textContent = ''; }
        else {
          M.chart.setCrosshairPosition(px, bar.time, M.series);
          if (oh) oh.textContent = 'O ' + bar.open.toFixed(2) + '  H ' + bar.high.toFixed(2) + '  L ' + bar.low.toFixed(2) + '  C ' + bar.close.toFixed(2);
        }
      } catch (e) { /* that chart is being rebuilt */ }
      _xhBusy = false;
    });
    let bar = titleEl.querySelector('.pos-bar');
    if (!bar) {                                     // a pane without the position tool: the same button cell, by the full-screen button
      const f0 = titleEl.querySelector('.fs-btn');
      bar = document.createElement('span'); bar.className = 'pos-bar';
      titleEl.insertBefore(bar, f0);
      if (f0) bar.appendChild(f0);
    }
    const fs = bar.querySelector('.fs-btn');
    const mk = (txt, title, fn, cls) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'pos-btn' + (cls ? ' ' + cls : ''); b.textContent = txt; b.title = title;
      b.addEventListener('click', (e) => { e.stopPropagation(); _activePane = { i, pane }; fn(); });
      b.addEventListener('dblclick', (e) => e.stopPropagation());
      bar.insertBefore(b, fs || null);
      return b;
    };
    _TFS.forEach((tf, k) => { mk(tf, 'Show ' + tf + ' candles (key ' + (k + 1) + ')', () => _setTf(i, pane, tf), 'tf-btn').dataset.tf = tf; });
    mk('◀', 'Replay back one candle of this chart (left arrow)', () => _stepTf(i, st.tf[pane], -1, pane));
    mk('▶', 'Replay forward one candle of this chart (right arrow)', () => _stepTf(i, st.tf[pane], 1, pane));
    _syncBar(i);
  },
};

PosTool.config.shared = ['a', 'b'];
PosTool.config.drawOnly = ['n'];                            // NQ: lines / rays / rectangles of its own (user, 2026-11-11), no position box
PosTool.config.group = { n: 'nq' };
PosTool.config.cursor = (i) => _rp(i).T;
PosTool.config.result = (i) => _rp(i).res;
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


def trading_day(t):
    """The start of the trading day holding t: 15:00 PT the day before (the session's reopen)."""
    d0 = t.normalize() + REOPEN
    return d0 if t >= d0 else d0 - pd.Timedelta(days=1)


def last_visit(h1, t):
    """How far back a row's history must reach (user, 2026-11-09): the start of the trading day in which price last traded within
    VISIT_BAND points of the open of the cross bar's trading day, before it left that band. From the day's start, step back
    through the H1 bars that traded inside the band (price is still in the area), then through the ones that did not (away),
    to the last one inside it again (the previous visit). None when price never left the band or never traded there before
    (the data's start): the default history then."""
    d0 = trading_day(t)
    k0 = int(h1.index.searchsorted(d0))
    p = float(h1["open"].iloc[k0])
    inb = ((h1["low"].to_numpy()[:k0] <= p + VISIT_BAND) & (h1["high"].to_numpy()[:k0] >= p - VISIT_BAND))[::-1]
    away = np.flatnonzero(~inb)
    if not len(away):
        return None
    back = np.flatnonzero(inb[away[0]:])
    return trading_day(h1.index[k0 - 1 - away[0] - back[0]]) if len(back) else None


def replay_start(m2, row):
    """The replay's start: the close of the first M2 candle of the cross bar's hour that closes back above the swept swing low
    (bullish; below the swept high, bearish) at or after the first M2 candle that traded below it (above it). It always exists:
    the cross bar itself trades through the swing and closes back."""
    t, px = row.Index, row.swing_price
    inside = m2.loc[t: t + pd.Timedelta(minutes=58)]
    gone = (inside["low"] < px) if row.bullish else (inside["high"] > px)
    back = (inside["close"] > px) if row.bullish else (inside["close"] < px)
    k0 = int(np.argmax(gone.to_numpy()))
    assert gone.iloc[k0], f"{t}: no M2 candle of the cross bar's hour went past the swing"
    hit = np.flatnonzero(back.to_numpy()[k0:])
    assert len(hit), f"{t}: no M2 candle closed back past the swing"
    return inside.index[k0], inside.index[k0 + hit[0]] + pd.Timedelta(minutes=2)


def replay_window(m1, closed, levels, swing_px=None, bull=True):
    """(last M1 bar of the replay, why): at least MIN_FUTURE_M1 bars past the cross bar's close, on until every level in
    `levels` not already touched before the close has been revisited (+ REVISIT_PAD bars), at most MAX_FUTURE_M1 bars; and
    (user 2026-11-11) on until the farthest of the swing prices `swing_px` (the swept swing and the older swings: the lowest
    for a bullish bar, the highest for a bearish one) is traded again after the close (+ REVISIT_PAD), at most MAX_SWING_M1."""
    k_p0, why_p0 = _p0_window(m1, closed, levels)
    if not swing_px:
        return m1.index[k_p0], why_p0
    c0 = int(m1.index.searchsorted(closed))
    far = min(swing_px) if bull else max(swing_px)
    after = m1.iloc[c0: c0 + MAX_SWING_M1]
    hit = np.flatnonzero((after["low"] <= far).to_numpy() if bull else (after["high"] >= far).to_numpy())
    if len(hit):
        k_sw = c0 + int(hit[0]) + REVISIT_PAD
        why_sw = f"the farthest swing price {far:.2f} traded again {CB._pt(after.index[hit[0]])}"
    else:
        k_sw = c0 + MAX_SWING_M1 - 1
        why_sw = f"20 trading days: the farthest swing price {far:.2f} is not traded again by then"
    if k_sw <= k_p0:
        return m1.index[k_p0], why_p0
    k = min(k_sw, len(m1) - 1)
    return m1.index[k], why_sw + ("; cut at the data's end" if k == len(m1) - 1 else "")


def _p0_window(m1, closed, levels):
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
    return k, why


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
    "<th class=\"left man-th\" title=\"Your manual trade, drawn with Long / Short on the H1, M5 or M2 pane (one per row): side, entry price, and its fill once the replay reaches it\">Manual entry</th>"
    "<th class=\"man-th\" title=\"Stop / target and their reward to risk\">Manual stop / target</th>"
    "<th class=\"left man-th\" title=\"As of the row's replay time: the first second reaching the stop or the target after the fill, or still open (last price)\">Manual exit</th>"
    "<th class=\"man-th\">Manual R</th><th class=\"man-th\" title=\"R x the risk $ of the position tool's settings (gear)\">Manual P&amp;L $</th>"
    "<th>Reviewed</th><th>Valid</th><th class=\"left\">Notes</th><th class=\"expand-th\">\u25b6</th>")
N_COLS = 14


def _row_key(row):
    return f"{row.Index.tz_convert('UTC').strftime('%Y-%m-%dT%H')}_{'bull' if row.bullish else 'bear'}"


def _row_html(i, row):
    cls = "type-lhpb" if row.bullish else "type-llpb"
    key = _row_key(row)
    return f"""
<tr class="lvl-row is-valid {cls}" data-idx="{i}" data-key="{key}" onclick="toggleChart({i})" style="cursor:pointer">
  <td class="left">{i + 1}</td>
  <td class="left">{CB._pt(row.Index)}</td>
  <td class="left" style="color:{CB.BULL if row.bullish else CB.BEAR}">{'Long' if row.bullish else 'Short'}</td>
  <td class="left">{CB._confirmation(row)}</td>
  <td>{row.swing_price:.2f}</td>
  <td class="left man-entry"></td><td class="man-plan"></td><td class="left man-exit"></td><td class="man-r"></td><td class="man-usd"></td>
  <td onclick="event.stopPropagation();"><input type="checkbox" class="reviewed-cb"></td>
  <td class="valid-cell" onclick="event.stopPropagation();"><input type="checkbox" class="valid-cb" checked></td>
  <td class="left" onclick="event.stopPropagation();"><textarea class="trade-note" placeholder="notes..."></textarea></td>
  <td class="expand-cell"><button class="expand-btn" data-idx="{i}" onclick="event.stopPropagation();toggleChart({i})">\u25b6</button></td>
</tr>
<tr class="chart-row hidden" data-idx="{i}" id="chart-row-{i}">
  <td colspan="{N_COLS}"><div class="chart-stack">
    <div class="replay-bar" data-idx="{i}">
      <button type="button" data-a="start" title="Back to the start: the first M2 candle that closed back past the swept swing">\u23ee</button>
      <button type="button" data-a="back" title="Back one candle of the chart last clicked (left arrow)"></button>
      <button type="button" data-a="fwd" title="Forward one candle of the chart last clicked (right arrow)"></button>
      <button type="button" data-a="play" title="Step forward on its own; pauses when your trade fills or ends"></button>
      <select data-a="tf" title="Play steps">{"".join(f'<option value="{t}"{" selected" if t == "M5" else ""}>{t}</option>' for t in ("M1", "M2", "M5", "M15", "M30", "H1", "H4"))}</select>
      <select data-a="speed" title="Play speed"><option value="1000">1 / s</option><option value="400" selected>2.5 / s</option><option value="150">7 / s</option></select>
      <button type="button" data-a="end" title="Jump to the end of the replay window">\u23ed</button>
      <button type="button" data-a="second" title="A second chart under the first, with its own timeframe"></button>
      <button type="button" data-a="nq" title="The NQ chart beside the ES chart"></button>
      <button type="button" data-a="sync" title="On: the NQ chart replays with ES (stepping either one moves both). Off: it keeps its own replay time, so NQ can be stepped on its own"></button>
      <span class="rp-time"></span><span class="rp-trade"></span><span class="rp-end src-tag"></span>
    </div>
    <div class="es-nq-row" id="enr-{i}">
      <div class="chart-cell chart-pane"><div class="chart-title" id="t-a-{i}"></div><div class="chart-ph" id="c-a-{i}"></div></div>
      <div class="chart-cell chart-pane pane-n hidden" id="pn-{i}"><div class="chart-title" id="t-n-{i}"></div><div class="chart-ph" id="c-n-{i}"></div></div>
    </div>
    <div class="chart-cell chart-pane pane-b hidden" id="pb-{i}"><div class="chart-title" id="t-b-{i}"></div><div class="chart-ph" id="c-b-{i}"></div></div>
  </div></td>
</tr>"""


def render(rows, h1, output, label, vp=False):
    data_dir = os.path.splitext(output)[0] + "_data"
    os.makedirs(data_dir, exist_ok=True)
    visits = {t: last_visit(h1, t) for t in rows.index}
    lo = rows.index.min() - pd.Timedelta(days=30)
    m5 = load_es("5min", start=lo, tz=PT)
    led = CB.ledger(m5)
    m2 = load_es("2min", start=lo, tz=PT)
    led2 = CB.ledger(m2)
    m1 = load_es("1m", start=min([lo] + [v for v in visits.values() if v is not None]), tz=PT)[["open", "high", "low", "close"]]
    h1b = h1[["open", "high", "low", "close"]]
    nq_m1 = load_nq("1m", start=m1.index[0], tz=PT)[["open", "high", "low", "close"]]
    nq_h1 = load_nq("1h", tz=PT)[["open", "high", "low", "close"]]
    charts, trs = [], []
    for i, row in enumerate(rows.itertuples()):
        older = CB.older_swings(h1, h1.index.get_loc(row.Index), row.swing_time, row.swing_price, row.bullish)
        t, bull = row.Index, row.bullish
        closed = t + pd.Timedelta(hours=1)
        until, end_why = replay_window(m1, closed, pd.concat([candidates(led, row), candidates(led2, row)]),
                                       [float(row.swing_price)] + [op for _, op, _ in older], bool(row.bullish))
        gone_t, start_t = replay_start(m2, row)
        side = "low" if bull else "high"
        chart = {"crossT": int(t.timestamp()), "close": int(closed.timestamp()), "start": int(start_t.timestamp()),
                 "bull": bool(bull),
                 "color": CB.BULL if bull else CB.BEAR, "swingColor": CB.SWING_COLOR, "crossLabel": CB._pt(t, False),
                 "swingT": int(row.swing_time.timestamp()), "swingPx": float(row.swing_price),
                 "sweptLabel": f"swept swing {side} {row.swing_price:.2f} (swing bar {CB._pt(row.swing_time)})",
                 "sweepT": int(gone_t.timestamp()),
                 "older": [[int(ot.timestamp()), float(op), bool(hit),
                            f"older swing {side} {op:.2f} (bar {CB._pt(ot)})" + (", cross bar went past it" if hit else "")]
                           for ot, op, hit in older],
                 "notes": [{"color": CB.SWING_COLOR, "text": f"swept swing {side} {row.swing_price:.2f}, bar {CB._pt(row.swing_time)}"}]
                          + [{"color": CB.SWING_COLOR, "text": f"older swing {side} {op:.2f}, bar {CB._pt(ot)}"
                              + (" (cross bar went past it)" if hit else "")} for ot, op, hit in older]}
        # the replay's data file: H1 bars before the cross bar's hour (from H1_HISTORY before the close), M1 bars from M1_HISTORY
        # before the close (the hour and 6 trading days of history before it), both from the last visit of the area when that
        # is further back (last_visit), and the 1 s path from S1_HISTORY M5
        # candles before the cross bar's hour (a box drawn further back starts its watch there), both to the end of the window
        c1 = int(m1.index.searchsorted(closed))
        visit = visits[t]
        m1_from = min(c1 - M1_HISTORY, int(m1.index.searchsorted(visit))) if visit is not None else c1 - M1_HISTORY
        h1_from = min(closed - H1_HISTORY, visit) if visit is not None else closed - H1_HISTORY
        p5 = int(m5.index.searchsorted(t))
        start = m5.index[max(0, p5 - S1_HISTORY)]
        stop = until + pd.Timedelta(minutes=1)
        key = _row_key(row)
        payload = {"h1": m1_pack(h1b[(h1b.index >= h1_from) & (h1b.index < t)]),
                   "m1": m1_pack(m1.iloc[max(0, m1_from): int(m1.index.searchsorted(until, side="right"))]),
                   "s1": s1_pack(s1_between(start, stop))}
        # NQ (the NQ chart): the same stretches of H1 and M1 bars, where the NQ ticks reach
        a1 = m1.index[max(0, m1_from)]
        nqm = nq_m1[(nq_m1.index >= a1) & (nq_m1.index <= until)]
        nqh = nq_h1[(nq_h1.index >= h1_from) & (nq_h1.index < t)]
        payload["nq"] = ({"h1": m1_pack(nqh) if len(nqh) else None, "m1": m1_pack(nqm)}
                         if len(nqm) and nqm.index[0] <= t else None)
        if vp:
            fine = trading_day(m1.index[max(0, c1 - M1_HISTORY)])
            coarse = trading_day(min([trading_day(t) - pd.Timedelta(days=VP_DAYS)] + ([visit] if visit is not None else []))
                                 + pd.Timedelta(hours=2))
            daily = trading_day(h1_from + pd.Timedelta(hours=2))   # one bucket per trading day over the rest of the H1 history
            def vp_of(sym, k=1):                   # k ticks per price row
                pk = lambda v, dur: vap.pack(vap.to_rows(v, k) if k > 1 else v, dur, vap.TICK * k)
                return {"d": (pk(vap.trading_days(daily, coarse, sym), 86400) if daily < coarse
                              else pk(vap.buckets(coarse, coarse, sym=sym), 86400)),
                        "c": pk(vap.buckets(coarse, fine, VP_COARSE, sym), VP_COARSE),
                        "f": pk(vap.buckets(fine, stop, 60, sym), 60)}
            payload["vp"] = vp_of("ES")
            if payload["nq"] is not None:          # the NQ chart's own profile, from the NQ ticks over the same stretches,
                payload["nq"]["vp"] = vp_of("NQ", 4)   # in 1-point rows (user, 2026-11-11: a quarter of the data of tick rows)
        with open(os.path.join(data_dir, key + ".js"), "w", encoding="utf-8") as f:
            f.write("__replayData(" + json.dumps(key) + "," + json.dumps(payload, separators=(",", ":")) + ");\n")
        chart["end"], chart["endWhy"] = int(stop.timestamp()), end_why
        charts.append(chart)
        trs.append(_row_html(i, row))
        print(f"{CB._pt(row.Index)} {'long ' if row.bullish else 'short'} replay {CB._pt(start_t)} to {CB._pt(until)}: {end_why}; "
              f"history from {CB._pt(m1.index[max(0, m1_from)])}" + (" (last visit)" if visit is not None and m1_from < c1 - M1_HISTORY else "")
              + ("; no NQ" if payload["nq"] is None else f"; NQ from {CB._pt(nqm.index[0])}" if nqm.index[0] > a1 + pd.Timedelta(days=3) else ""))
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
                     + "\n  if (window.Replay) Replay.attach(chart, series, el, titleEl, cd);"
                     + ("\n  if (window.VolProfile) VolProfile.attach(chart, series, el, titleEl, cd);" if vp else ""))
    old_stack = ("_renderChartPane(i, 'main', cd.main);\n  _renderChartPane(i, 'ctx', cd.ctx);\n"
                 "  _renderChartPane(i, 'l5', cd.l5);\n  _renderChartPane(i, 'l2', cd.l2);")
    assert js.count(old_stack) == 1, "cross bar report JS changed: re-check _renderStack"
    js = js.replace(old_stack, "_openRow(i);")
    fit = "_fitBars(chart, cd.candles.length);"
    assert js.count(fit) == 2, "M1 report chart JS changed: re-check the candle fit"
    js = js.replace(fit, "(cd.fit ? cd.fit(chart) : _fitBars(chart, cd.candles.length));")   # a replay chart: the last candles
    js = js + "<script>\n" + MANUAL_JS.replace("__DATA_DIR__", data_rel) + "\nwindow.Replay = Replay;\n</script>\n"
    if vp:
        with open(VP_JS, encoding="utf-8") as f:
            js += "<script>\n" + f.read() + "\n</script>\n"
    js = js.replace("l5: 'M5-LXPB'", "a: 'chart', b: '2nd chart', n: 'NQ', l5: 'M5-LXPB'")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{CB.M.CSS}
{CB.NOTES_CSS}
{MANUAL_CSS}
</head><body>
<h1>{title}</h1>
<p class="lead">ES H1 cross bars (an H1 bar that sweeps a swing and closes back, and is also a hammer / shooting star or an
engulfing bar), oldest first, for MANUAL trades: long after a bullish bar, short after a bearish one, as you see fit. Click a row:
its replay starts INSIDE the cross bar's hour, at the close of the first M2 candle that closed back above the swept swing low
(below the swept high, bearish) after price traded below (above) it, so the cross bar is still forming and may not end up confirmed
(amber = the swept swing). Times are PT.</p>
<p class="lead">FORWARD REPLAY. The chart opens on H1; switch it with D1 / H4 / H1 / M30 / M15 / M5 / M2 / M1 in its title bar (or
keys 1-8; D1 and H4 as TradingView draws ES: a D1 candle is the session from 15:00 PT to 14:00 PT, H4 candles start at 15:00 PT).
"+ 2nd chart" adds a second one under it with its own timeframe. Step forward one candle of a chart's timeframe with the &#9664;
&#9654; buttons on it, the replay bar (the chart last clicked), the arrow keys, or Play (steps of the chosen timeframe; pauses when
your trade fills or ends). Whatever the timeframe, the candles are the ones that stood at the replay time, the last one still
forming. The window runs at least one trading day past the cross bar's close and on until every P0 price of the cross bar (its M5 and M2
entry levels) has been revisited (at most five trading days), and on until the farthest swing price (the swept swing and the
older swings: the lowest for a long, the highest for a short) is traded again, at most 20 trading days. Draw your trade with Long / Short (one per row, kept in time and price,
so it stays put through a timeframe switch; a new one replaces the old). Its entry is a PRICE: entered when price trades there again
after the candle you drew it on (from the replay time when that candle is still forming). Judged on the 1 s price path: the first
second reaching the stop or the target after the fill ends it (a second reaching both counts as the stop; no end-of-day flat). The
Manual columns show it as of the row's replay time (open = last price), and the manual boxes above count the trades closed so far.
Manual R = (exit - fill) / (entry - stop), P&amp;L $ = R x the risk $ in the tool's settings (gear). Boxes, replay times
and settings are kept in this browser.</p>
<p class="lead">NQ CHART. Beside the ES chart ("&minus; NQ chart" hides it and ES takes the full width), with its own timeframe buttons, built the same way from the
NQ continuous series in E:\data_source (back-adjusted exactly as TradingView's NQ1!). "NQ sync on": NQ replays with ES, stepping either chart moves both, and picking a timeframe on the ES chart or NQ switches both
(the 2nd ES chart keeps its own). "NQ sync off": NQ keeps its own replay time and timeframe, so
stepping the NQ chart (its arrows, or the replay bar and arrow keys after clicking it) moves NQ only and ES stays put; turning sync
back on brings NQ to the ES time and timeframe. The position tool is on the ES charts only; NQ has its own drawings (lines, rays,
rectangles, kept apart from the ES ones) and its own volume profile from the NQ trades, with the same settings and its point sizes
(the &plusmn;50 pt last-visit band, smoothing, confluence) scaled by NQ / ES price, in 1-point rows. The last visit &rarr; now
profile: when the last visit was the previous trading day, it reaches back to the earlier of the visit before that and 5 trading
days before today (its label says "last 2 visits" or "last 5 days").</p>
<p class="lead">MOVING A CHART. Drag a chart to move it any way, up and down as well as sideways (the price axis stops auto-fitting
from the first press); double-click the price axis to fit the candles again. The crosshair is shared between the ES chart and NQ:
hovering one shows the same time on the other.</p>
<div class="summary">
  <div class="box"><strong>{len(rows)}</strong>cross bars</div>
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
    print(f"saved {output}: {len(rows)} cross bars")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--at", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--output", required=True)
    ap.add_argument("--vp", action="store_true", help="volume profiles (sample)")
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
    rows = rows.sort_index()                      # oldest first
    render(rows, h1, a.output, a.label or "sample", vp=a.vp)


if __name__ == "__main__":
    main()
