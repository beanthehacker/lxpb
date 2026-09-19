"""
compute.py -- the upcoming-M5-levels computation, on the repo's own rules.

WHAT "UPCOMING" MEANS. The ss_m5_confl2 report trades M5 LXPB *retests*: a
level that has been formed (P0) and broken out (P1) and is then touched again
(P2). This shows the levels that are between P1 and P2 right now -- open,
broken out, not yet retested -- prepared exactly as the report would if each
were retested on the next bar:

  * Selection / clustering / fine-tuned entry / swerve / stop are the report's
    OWN functions (render_m5_confl2_report), called unchanged. The only thing
    supplied here is the missing P2: every awaiting level gets a stand-in
    `retest_time` of the next M5 bar, so "as of the retest" reads "as of now".
    Nothing in the rule set is restated in this file.
  * No fill scan, no target, no tick data: the retest is in the future.

`compute_state(ledger, m5, h1)` is pure: bars and ledger in, the JSON-able page
state out. Fetching, storage and the level cache live in refresh.py.
Import bootstrap.setup() before this module.
"""
import argparse

import numpy as np
import pandas as pd

import render_labels_report as R
import lxpb_levels_cache as LC
import render_ss_confl_finetune_report as SF
import render_m5_confl2_report as RM
import render_stop_target_report as SR
import charts as CH

MAX_RANGE_PTS = 200.0        # widest N the page offers; rows are built out to here
RANGE_MARGIN_PTS = 40.0      # extra reach so clusters at the edge are not truncated
M5 = pd.Timedelta(minutes=5)


def _epoch(ts):
    return int(pd.Timestamp(ts).timestamp())


def _price(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 2)


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
    """One cluster -> the result dict (report-style names: own_price, alt_price,
    alt_source, stop_price, ...) or None when it has no valid stop -- which the
    report treats as no trade."""
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
    ratio = RM._m5_p1_range_ratio_by_window(row_d["breakout_time"])[RM.M5_RANGE_RATIO_WINDOW_DEFAULT - 1]
    members = sorted({round(float(c["row"]["price"]), 2) for c in cluster},
                     reverse=(level_type == "LLPB"))
    tags = []
    if swerve is not None:
        tags.append("swerved" if swerve["moved"] else "swerve_blocked")
    return {
        "level_type": level_type, "is_long": is_long, "last_price": last_price,
        "own_price": float(row_d["price"]), "own_formed": pd.Timestamp(row_d["formation_time"]),
        "own_p1": pd.Timestamp(row_d["breakout_time"]),
        "alt_price": entry, "alt_source": conf["alt_source"],
        "alt_formation_time": pd.Timestamp(conf["alt_formation_time"]),
        "group_n": int(conf["group_n"]), "cluster_size": len(cluster), "members": members,
        "qualified": bool(anchor["qualified"]),
        "stop_price": float(stop_price), "stop_source": stop_source,
        "stop_from_ts": stop_from, "stop_title": _stop_title(is_long, stop_source, stop_row),
        "p1_ratio": ratio, "swerve": swerve, "tags": tags,
    }


def _stop_title(is_long, stop_source, stop_level):
    """The report's own Stop-cell tooltip wording."""
    if stop_source == "m5_p0_spike":
        extreme = "low" if is_long else "high"
        return (f"Entry level's OWN P0 was a spike candle "
                f"({'hammer' if is_long else 'shooting star'}): {stop_level['type']} "
                f"{stop_level['price']:.2f}, P0 {R._to_pt_str(stop_level['formation_time'])}, "
                f"P0 {extreme} {stop_level['p0_' + extreme]:.2f}; one tick "
                f"{'below' if is_long else 'above'} THAT candle (not the P1 thrust candle)")
    extreme = "breakout_low" if is_long else "breakout_high"
    return (f"Live M5 {stop_level['type']} {stop_level['price']:.2f}, "
            f"P0 {R._to_pt_str(stop_level['formation_time'])}; "
            f"P1 {R._to_pt_str(stop_level['breakout_time'])}, "
            f"{extreme} {stop_level[extreme]:.2f}; one tick {'below' if is_long else 'above'}")


def _row_payload(res, m5, h1, ledger):
    """Everything the page needs for one row: cells, tooltips, both chart specs."""
    last = res["last_price"]
    entry, own = res["alt_price"], res["own_price"]
    nearest = min(res["members"], key=lambda p: abs(p - last))
    sw = res["swerve"]
    swings = ""
    if sw:
        swings = ", ".join(f"{px:.2f} @ {R._to_pt_str(t)}" for t, px in sw["swings"][:3])
    return {
        "type": res["level_type"], "own": _price(own), "entry": _price(entry),
        "stop": _price(res["stop_price"]), "risk": _price(abs(entry - res["stop_price"])),
        "away_level": _price(nearest - last), "away_entry": _price(entry - last),
        "members": res["members"], "cluster_size": res["cluster_size"], "pool": res["group_n"],
        "qualified": res["qualified"], "entry_source": res["alt_source"],
        "improved": abs(entry - own) > 1e-9,
        "stop_source": res["stop_source"], "stop_title": res["stop_title"],
        "p0": R._to_pt_str(res["own_formed"]), "p1": R._to_pt_str(res["own_p1"]),
        "p1_ratio": _price(res["p1_ratio"]),
        "tags": res["tags"],
        "swerve": None if not sw else {"planned": _price(sw["planned_price"]),
                                       "price": _price(sw["price"]), "swings": swings},
        "m5": CH.m5_spec(res, m5, ledger, last),
        "h1": CH.h1_spec(res, h1, last),
    }




def compute_state(ledger, m5, h1):
    """The page's state for the series as it stands: every awaiting level within
    MAX_RANGE_PTS of the latest completed close, prepared as the report would."""
    next_bar = m5.index[-1] + M5
    # The latest ES CLOSE: the last completed M5 bar's close. Every distance on
    # the page is measured from this.
    last_price = float(m5["close"].iloc[-1])
    cands = _awaiting_candidates(ledger, last_price, next_bar)
    swerve_args = _swerve_args()
    results, no_stop = [], 0
    for cluster in _clusters(cands):
        res = _process(cluster, ledger, next_bar, last_price, swerve_args)
        if res is None:
            no_stop += 1
        else:
            results.append(res)

    def reach(r):   # the row's nearest LXPB level to the latest close
        return min(abs(p - last_price) for p in r["members"])

    results = sorted((r for r in results if reach(r) <= MAX_RANGE_PTS), key=reach)
    rows = []
    for i, r in enumerate(results):
        p = _row_payload(r, m5, h1, ledger)
        p["id"] = i
        rows.append(p)
    return {
        "last_price": _price(last_price), "last_bar": _epoch(m5.index[-1]),
        "last_bar_pt": R._to_pt_str(m5.index[-1]), "next_bar": _epoch(next_bar),
        "max_range": MAX_RANGE_PTS, "n_awaiting": len(cands), "n_no_stop": no_stop,
        "confl_radius": RM.M5_CONFLUENCE_N_POINTS_DEFAULT,
        "swerve_tol": RM.SWERVE_TOL_PTS_DEFAULT, "swerve_max_move": RM.SWERVE_MAX_MOVE_PTS_DEFAULT,
        "stop_radius": RM.DYNAMIC_STOP_RADIUS_PTS, "near_pts": SR.M5_NEAR_PTS,
        "rows": rows,
    }
