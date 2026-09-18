"""
build.py -- one refresh of the upcoming-M5-levels dashboard.

Fetches the live tail from TradingView (tv_feed.py), joins it to the repo's
TradingView exports as one more vintage, runs the M5 LXPB state machine over
the whole continuous series, and writes `data/state.json` for the page.

WHAT "UPCOMING" MEANS. The ss_m5_confl2 report trades M5 LXPB *retests*: a
level that has been formed (P0) and broken out (P1) and is then touched again
(P2). This dashboard shows the levels that are between P1 and P2 right now --
open, broken out, not yet retested -- and prepares each one exactly as the
report would if it were retested on the next bar:

  * Selection / clustering / fine-tuned entry / swerve / stop are the
    report's OWN functions (render_m5_confl2_report), called unchanged. The
    only thing supplied here is the missing P2: every awaiting level gets a
    stand-in `retest_time` of the next M5 bar, so "as of the retest" reads
    "as of now". Nothing in the rule set is restated in this file.
  * No fill scan, no target, no tick data: the retest is in the future.
    Tick-side filters (liquidity, volume spike, EOD) are not applicable.

The whole thing runs in a fresh process every refresh (server.py spawns it),
so none of the modules' per-process caches can go stale between hours.

Bars: the TradingView-only convention (lxpb-v2/CLAUDE.md) holds. The live
tail is one more TradingView export -- a same-vintage M5 tail joins the newest
vintage at offset 0; after a roll, `_merge_vintages` re-anchors the older
history by an offset measured on the overlap, exactly as for any export.
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V2 = os.path.dirname(_HERE)
DATA_DIR = os.path.join(_HERE, "data")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
sys.path.insert(0, _V2)
sys.path.insert(0, os.path.join(_V2, "ss_m5_confl2"))
sys.path.insert(0, _HERE)

import tv_feed as TV                       # noqa: E402
import render_labels_report as R           # noqa: E402
import lxpb_levels_cache as LC             # noqa: E402
import render_ss_confl_finetune_report as SF   # noqa: E402
import render_m5_confl2_report as RM       # noqa: E402

MAX_RANGE_PTS = 200.0        # widest N the page offers; rows are built out to here
RANGE_MARGIN_PTS = 40.0      # extra reach so clusters at the edge are not truncated
M5_BARS_SHIPPED = 4000       # ~14 days of extended-hours M5 for the row charts
H1_BARS_SHIPPED = 1000
LIVE_M5 = os.path.join(DATA_DIR, "live_M5.csv")
LIVE_H1 = os.path.join(DATA_DIR, "live_H1.csv")
M5 = pd.Timedelta(minutes=5)


def _log(msg):
    print(f"[build {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _epoch(ts):
    return int(pd.Timestamp(ts).timestamp())


def _price(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 2)


# --------------------------------------------------------------------------
# Data: exports + live tail, on the repo's own loaders
# --------------------------------------------------------------------------

def _fetch_live():
    """Fetch M5 and H1, write them as export-format CSVs, and return
    (latest_price, latest_price_time) read off the still-forming M5 bar."""
    m5 = TV.fetch_bars("5")
    h1 = TV.fetch_bars("60")
    last_price, last_time = float(m5["close"].iloc[-1]), m5.index[-1]
    now = pd.Timestamp.now(tz="UTC")
    TV.write_export(TV.completed(m5, 5, now), LIVE_M5)
    TV.write_export(TV.completed(h1, 60, now), LIVE_H1)
    _log(f"fetched M5 {len(m5)} bars to {m5.index[-1]}, H1 {len(h1)} bars to {h1.index[-1]}")
    return last_price, last_time


def _wire_series():
    """Point the repo's loaders at exports + live tail, and the level cache at
    this dashboard's own folder (never the shared one)."""
    base_h1, base_m5 = list(R.DISPLAY_H1_PATHS), [list(v) for v in R.DISPLAY_M5_PATHS]
    R.DISPLAY_M5_PATHS = base_m5 + [[LIVE_M5]]
    R.DISPLAY_H1_PATHS = base_h1 + [LIVE_H1]
    R._DISPLAY_H1_CACHE = R._DISPLAY_M5_CACHE = None
    try:
        R._display_h1()
    except RuntimeError as e:
        if "vintage" not in str(e):
            raise
        # A roll has re-anchored TradingView's history since the H1 export was
        # made: the live H1 (5000 bars, ~8 months) is the only current-vintage
        # H1, and is what the M5 scale check needs anyway. See CLAUDE.md,
        # "Rollover checklist", step 1.
        _log("H1 export is an older vintage than the live tail -- using live H1 alone")
        R.DISPLAY_H1_PATHS = [LIVE_H1]
        R._DISPLAY_H1_CACHE = None
    LC.CACHE_DIR = os.path.join(DATA_DIR, "levels_cache")


# --------------------------------------------------------------------------
# Upcoming levels
# --------------------------------------------------------------------------

def _swerve_args():
    return argparse.Namespace(
        swerve=True, swerve_tol_pts=RM.SWERVE_TOL_PTS_DEFAULT,
        swerve_lookback_hours=RM.SWERVE_LOOKBACK_HOURS_DEFAULT,
        swerve_max_move_pts=RM.SWERVE_MAX_MOVE_PTS_DEFAULT,
        swerve_swing_k=RM.SWERVE_SWING_K_DEFAULT)


def _awaiting_candidates(ledger, last_price, next_bar):
    """Every open, broken-out, not-yet-retested M5 level within reach of the
    latest price, as report-style candidates: the ledger row with the
    stand-in retest instant, its same-side confluence pool as of now, and the
    report's own >= ss-confl-min verdict (`qualified`)."""
    aw = ledger[(ledger["fate"] == LC.FATE_OPEN_AWAITING_RETEST) &
                ((ledger["price"] - last_price).abs() <= MAX_RANGE_PTS + RANGE_MARGIN_PTS)]
    cands = []
    for _, r in aw.iterrows():
        row = r.copy()
        row["retest_time"] = next_bar
        same = SF._same_side_confluence(ledger, row, RM.M5_CONFLUENCE_N_POINTS_DEFAULT)
        cands.append({"row": row, "same_side_m5": same, "m5_ledger": ledger,
                      "seg_idx": None, "qualified": len(same) >= RM.SS_CONFL_MIN_DEFAULT})
    cands.sort(key=lambda c: pd.Timestamp(c["row"]["formation_time"]))
    for i, c in enumerate(cands):
        c["i"] = i
    return cands


def _clusters(cands):
    """The report's own union-find over the qualified candidates; a level that
    does not qualify is its own singleton (shown only when the page's
    confluence filter is switched off)."""
    qualified = [c for c in cands if c["qualified"]]
    out = RM.cluster_candidates(qualified) if qualified else []
    out += [[c] for c in cands if not c["qualified"]]
    return out


def _process(cluster, ledger, next_bar, last_price, swerve_args):
    anchor = SF.cluster_anchor(cluster)
    row_d = anchor["row"]
    level_type = row_d["type"]
    is_long = level_type == "LHPB"

    conf = RM.cluster_confluence(cluster)
    swerve = RM._swerve_entry(ledger, level_type, is_long, conf, row_d, swerve_args,
                              hi_ts=next_bar)
    entry = float(conf["alt_price"])
    entry_level = RM._entry_level_row(cluster, conf)
    stop_price, stop_row, stop_source = RM._dynamic_stop_m5(
        level_type, entry, is_long, entry_level, ledger, next_bar)
    if stop_price is None:
        return None

    stop_from = (pd.Timestamp(stop_row["formation_time"]) if stop_source == "m5_p0_spike"
                 else pd.Timestamp(stop_row["breakout_time"]))
    breakout = pd.Timestamp(entry_level["breakout_time"]) if entry_level is not None else None
    ratio = RM._m5_p1_range_ratio_by_window(row_d["breakout_time"])[RM.M5_RANGE_RATIO_WINDOW_DEFAULT - 1]
    members = sorted({round(float(c["row"]["price"]), 2) for c in cluster},
                     reverse=(level_type == "LLPB"))
    tags = []
    if swerve is not None:
        tags.append("swerved" if swerve["moved"] else "swerve_blocked")
    return {
        "type": level_type,
        "entry": _price(entry), "stop": _price(stop_price),
        "risk": _price(abs(entry - stop_price)),
        "dist": _price(entry - last_price),
        "members": members, "cluster_size": len(cluster), "pool": int(conf["group_n"]),
        "qualified": bool(anchor["qualified"]),
        "entry_source": conf["alt_source"],
        "entry_formed": _epoch(conf["alt_formation_time"]),
        "entry_p1": _epoch(breakout) if breakout is not None else None,
        "entry_spike": bool(entry_level is not None and entry_level.get("is_spike")),
        "stop_source": stop_source, "stop_from": _epoch(stop_from),
        "p1_ratio": _price(ratio),
        "planned_entry": _price(swerve["planned_price"]) if swerve is not None else None,
        "tags": tags,
    }


def build():
    t0 = time.time()
    os.makedirs(DATA_DIR, exist_ok=True)
    last_price, last_time = _fetch_live()
    _wire_series()

    ledger = LC.m5_levels(verbose=True)
    m5 = LC.m5_bars_continuous()
    h1 = R._display_h1()
    next_bar = m5.index[-1] + M5
    _log(f"M5 series {m5.index[0]} -> {m5.index[-1]} ({len(m5):,} bars); "
         f"latest price {last_price} @ {last_time}")

    cands = _awaiting_candidates(ledger, last_price, next_bar)
    _log(f"{len(cands)} awaiting levels within {MAX_RANGE_PTS + RANGE_MARGIN_PTS:.0f}pt, "
         f"{sum(c['qualified'] for c in cands)} with same-side confluence")
    swerve_args = _swerve_args()
    rows, no_stop = [], 0
    for cluster in _clusters(cands):
        res = _process(cluster, ledger, next_bar, last_price, swerve_args)
        if res is None:
            no_stop += 1
        else:
            rows.append(res)
    rows = [r for r in rows if abs(r["dist"]) <= MAX_RANGE_PTS]
    rows.sort(key=lambda r: abs(r["dist"]))
    for i, r in enumerate(rows):
        r["id"] = i

    def pack(bars, n):
        b = bars.iloc[-n:]
        return {"t": [_epoch(t) for t in b.index],
                "o": [_price(x) for x in b["open"]], "h": [_price(x) for x in b["high"]],
                "l": [_price(x) for x in b["low"]], "c": [_price(x) for x in b["close"]]}

    state = {
        "built_at": _epoch(pd.Timestamp.now(tz="UTC")),
        "last_price": _price(last_price), "last_price_time": _epoch(last_time),
        "last_m5_bar": _epoch(m5.index[-1]), "next_bar": _epoch(next_bar),
        "max_range": MAX_RANGE_PTS,
        "n_awaiting": len(cands), "n_no_stop": no_stop,
        "rows": rows, "m5": pack(m5, M5_BARS_SHIPPED), "h1": pack(h1, H1_BARS_SHIPPED),
    }
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    os.replace(tmp, STATE_PATH)
    _log(f"wrote {len(rows)} rows ({no_stop} clusters dropped: no valid stop) "
         f"in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    build()
