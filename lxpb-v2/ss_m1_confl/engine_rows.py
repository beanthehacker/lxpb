"""The M1 fade report's rows, from the live engine and nothing else (user, 2026-10-16).

The M1 fade playbook is decided by `live_range` (range first; the LXPB ledger is only a cache lookup of the levels a
box's fade seat can trade). This module turns the engine's FILLED fade trades into the row dicts the report's page,
charts and stats are built from. Nothing here selects, clusters, fills, stops or targets anything: every price and time
is what the engine did. Go-with trades (the engine takes them too) are not part of the fade playbook and are dropped.

The ledger (lxpb_levels_cache.m1_levels, built on the same M1 bars) is looked up only to give the chart code the ledger
rows it draws P0 / P1 / P2 from.
"""
import pandas as pd

import range_box_tags as RB

# engine target rule -> the page's target-rule checkbox (data-mode). Every trade carries each rule's what-if bracket
# (engine `_views`: same fill and stop, that rule's target, plain and managed), so the page swaps them live.
RULE_MODES = {"range_edge": "range-edge", "zigzag": "swing-extreme", "opposite_zz": "opposite-zz"}
DEFAULT_MODE = "range-edge"


def _ts(ns):
    return pd.Timestamp(int(ns), unit="ns", tz="UTC")


def _naive(ns):
    return pd.Timestamp(int(ns), unit="ns")


def lv_key(lv):
    return (lv["type"], round(float(lv["price"]), 4), pd.Timestamp(lv["formation_time"]).value)


class Ledger:
    """Row lookup in the M1 ledger by (type, price, formation time), falling back to the engine's own level dict."""

    def __init__(self, ledger, fate, bars):
        self.df = ledger
        self.fate = fate
        self.bars = bars
        self.miss = []
        form = pd.to_datetime(ledger["formation_time"], utc=True).astype("int64").to_numpy()
        self.pos = {(t, round(float(p), 4), int(f)): i
                    for i, (t, p, f) in enumerate(zip(ledger["type"], ledger["price"], form))}

    def row(self, lv):
        i = self.pos.get(lv_key(lv))
        if i is not None:
            return self.df.iloc[i].copy()
        self.miss.append(lv_key(lv))
        out = {k: v for k, v in lv.items()}
        fate = self.fate.get(lv_key(lv))
        out["retest_time"] = _ts(fate[1]) if fate is not None and fate[0] == "retested" else pd.NaT
        out["entry_price"] = lv["price"]
        return pd.Series(out)

    def dict(self, lv):
        d = self.row(lv).to_dict()
        bar = self.bars.get(pd.Timestamp(lv["formation_time"]).value)      # (open, high, low, close) of the P0 candle
        if bar is not None:
            d["p0_low"], d["p0_high"] = float(bar[2]), float(bar[1])
        return d


def _box_view(b):
    up = b["direction"] == "up"
    st = _ts(b["breakout_time"]) if b.get("breakout_time") is not None else pd.NaT
    bt = _ts(b["break_t"]) if b.get("break_t") is not None else st
    start = _ts(b["h1_start_t"]) if b.get("h1_start_t") is not None else _ts(b["start_t"])
    return {"start": start, "low": b["low"], "high": b["high"], "status": b["status"],
            "h1": bool(b.get("h1")), "m5_low": b.get("m5_low", b["low"]), "m5_high": b.get("m5_high", b["high"]),
            "merged_n": len(b.get("merged_ids") or ()),
            "state": "waiting-for-retest", "breakout": "strong", "break_dir": "up" if up else "down",
            "break_time": bt, "strong_time": st, "edge": "upper" if up else "lower", "edge_done": False,
            "fade_no": 2 if b["fade2"] else 1,
            "prior_time": _ts(b["prior_time"]) if b.get("prior_time") is not None else pd.NaT, "prior_dir": b.get("prior_dir"),
            "pivots": [{"kind": p["kind"], "time": _ts(p["time"]), "brk": p["brk"]} for p in b["pivots"]]}


def _outcome(tr):
    ex = tr["exit"]
    if ex is None:
        return "no_hit"
    if ex["why"] == "target":
        return "target"
    if ex["why"] == "stop":
        return "trail_stop" if tr["trails"] and tr["r"] > 1e-9 else "stop"
    return ex["why"]            # eod_flat, rr_floor


def _resolved(tr, v):
    ex = v["exit"]
    return {"outcome": _outcome(v), "exit_time": _ts(ex["t"]) if ex else None,
            "exit_price": float(ex["price"]) if ex else None, "r": v.get("r") if ex else None,
            "touch_time": _ts(tr["t"]), "favorable_pts": v["mfe"], "adverse_pts": v["mae"], "giveback_pts": v["dd"],
            "entry_gapped": False}


def _mode(tr, base, man, is_long, stop_pts, min_r):
    """One target rule's row data: `base` the plain bracket (stop / target / end-of-day flat), `man` the managed one (stop
    trail + RR floor), both followed by the engine from the same fill and stop."""
    resolved, mres = _resolved(tr, base), _resolved(tr, man)
    trail_events = [(_ts(t), float(px)) for t, px in man["trail_log"]]
    rr_floor = bool(man["exit"] and man["exit"]["why"] == "rr_floor")
    mgmt = {"r": mres["r"], "outcome": mres["outcome"], "exit_time": mres["exit_time"],
            "pnl_pts": (mres["r"] * stop_pts) if mres["r"] is not None else None, "fired": bool(trail_events or rr_floor),
            "trail_events": trail_events, "rr_floor_fired": rr_floor}
    meta = base.get("meta") or base.get("plan_meta") or {}
    pv_t = meta.get("pivot_time")
    target_info = {"src": "swing_extreme", "pivot_kind": meta.get("pivot_kind", "trough" if is_long else "crest"),
                   "pivot_price": float(meta.get("pivot_price", base["target"])),
                   "pivot_time": _ts(pv_t) if pv_t is not None else _ts(tr["t"])}
    if meta.get("target_src") == "range_edge":       # the faded range's edge
        target_info.update(src="range_edge", edge=meta["edge"], box_low=float(meta["box_low"]),
                           box_high=float(meta["box_high"]), boxes=int(meta["n_boxes"]))
    elif meta.get("target_src") == "opposite_zz":    # the farthest standing opposite-type P0 since the leg's start
        lvl = dict(meta["level"])
        lvl["formation_time"] = pd.Timestamp(lvl["formation_time"])
        target_info = {"src": "opposite_zz", "level": lvl, "pivot_time": target_info["pivot_time"],
                       "pivot_price": float(meta["pivot_price"])}
    tgt_pts = abs(base["target"] - tr["price"])
    r_planned = tgt_pts / stop_pts
    tags = []
    if r_planned < min_r:
        tags.append("r_below_min")
    if base["exit"] and base["exit"]["why"] == "eod_flat":
        tags.append("eod_flat")
    return {"target_price": float(base["target"]), "target_pts": tgt_pts, "target_info": target_info,
            "r_multiple": r_planned, "resolved": resolved, "mgmt": mgmt, "dyn_tags": tags}


def _entry_kind(lvl, bars):
    """What the ENTRY level is (the level the order sat on, not the one that qualified it): spike (its P0 kind is spike-P0 or
    spike+swing-P0, the detector pairing of `../lxpb.py`), swing (swing-P0 or spike+swing-P0: a swing AND consolidating at the
    breakout) and its formation candle's rejection wick in points (upper wick for an LHPB, lower for an LLPB, the same reading
    as the engine's plain-P0 wick rule). The page's 'Entry level' filters read these."""
    kind = lvl.get("p0_kind") or ""
    bar = bars.get(pd.Timestamp(lvl["formation_time"]).value)     # (open, high, low, close)
    wick = None
    if bar is not None:
        wick = bar[1] - max(bar[0], bar[3]) if lvl["type"] == "LHPB" else min(bar[0], bar[3]) - bar[2]
    return {"p0_kind": kind, "spike": kind in ("spike-P0", "spike+swing-P0"),
            "swing": kind in ("swing-P0", "spike+swing-P0"), "wick": float(wick) if wick is not None else None}


def _result(tr, led, min_r):
    is_long = tr["side"] == "buy"
    lvl, anc = tr["level"], tr["decided"]       # anc: the level that qualified on its own; lvl: the entry (a better cluster price or the same level)
    anchor_row = led.row(anc)
    own_price = float(anc["price"])
    alt_price = float(tr["level_price"])
    fill = float(tr["price"])
    same = lv_key(lvl) == lv_key(anc)
    sw = tr.get("swerve")
    source = "own" if same else "cluster"
    stop_pts = tr["stop_pts"]
    modes = {}
    for rule, page_mode in RULE_MODES.items():
        base, man = (tr.get("views") or {}).get((rule, False)), (tr.get("views") or {}).get((rule, True))
        if base is None or man is None:
            continue                    # no target under this rule at the fill: the page shows NO TARGET when only it is ticked
        modes[page_mode] = _mode(tr, base, man, is_long, stop_pts, min_r)
    if not modes:                       # an engine without what-if brackets: the trade as the engine managed it
        modes[DEFAULT_MODE] = _mode(tr, tr, tr, is_long, stop_pts, min_r)
    active = RULE_MODES.get(tr.get("target_rule"), DEFAULT_MODE)
    active = active if active in modes else next(iter(modes))
    mode = modes[active]
    tgt_pts, r_planned, target_info = mode["target_pts"], mode["r_multiple"], mode["target_info"]
    resolved, mgmt = mode["resolved"], mode["mgmt"]
    fav, adv, dd = resolved["favorable_pts"], resolved["adverse_pts"], resolved["giveback_pts"]
    meta = tr.get("plan_meta") or {}
    boxes = [_box_view(b) for b in tr["box_info"]]
    nos = {b["fade_no"] for b in boxes}
    fade_no = 2 if nos == {2} else (1 if nos == {1} else 0)
    tags = []
    if anc.get("p0_kind") == "plain-P0":
        tags.append("plain_p0")
    if tr["cluster_n"] == 1:
        tags.append("no_confl")
    if "globex_eth_open" in tr["tags"]:
        tags.append("globex_eth_open")
    tags += [RB.TAG_FROM_RANGE, {1: RB.TAG_FADE1, 2: RB.TAG_FADE2, 0: RB.TAG_FADE_MIXED}[fade_no]]
    members = tr["members"]
    stop_level = meta.get("stop_level")
    initial_stop = fill - stop_pts if is_long else fill + stop_pts
    chase = (fill - alt_price) if is_long else (alt_price - fill)
    res = {
        "i": tr["id"], "row": anchor_row, "level_type": tr["type"], "is_long": is_long, "seg_idx": 0,
        "group_n": tr["cluster_n"], "cluster_size": len(members),
        "cluster_members": sorted({float(m["price"]) for m in members}, reverse=not is_long),
        "cluster_member_formations": sorted(pd.Timestamp(m["formation_time"]) for m in members),
        "own_price": own_price, "alt_price": alt_price, "alt_source": source,
        "alt_formation_time": pd.Timestamp(lvl["formation_time"]), "alt_end_time": pd.NaT,
        "entry_m5_level": None if same else led.dict(lvl),
        "improved": (fill < own_price) if is_long else (fill > own_price),
        "swerve": sw, "dyn_tags": tags, "filled": True,
        "p0_kind": anc.get("p0_kind"), "plain_p0": None,
        "touch_time_alt": _ts(tr["t"]), "fill_price": fill,
        "chased_pts": max(0.0, chase), "price_improvement_pts": max(0.0, -chase),
        "entry_is_spike": bool(lvl.get("is_spike")), "run_in_start": None,
        "entry_kind": _entry_kind(lvl, led.bars),
        "stop_price": initial_stop, "stop_pts": stop_pts, "stop_source": tr["stop_src"],
        "stop_m5_level": led.dict(stop_level) if stop_level is not None else None,
        "modes": modes, "active_mode": active,
        "range_box": {"from": boxes, "into": []}, "fade_no": fade_no,
        "armed_t": _ts(tr["armed_t"]) if tr.get("armed_t") is not None else None,
        "engine_trade": tr["id"],
    }
    res.update({"target_price": mode["target_price"], "target_pts": tgt_pts, "target_info": target_info,
                "r_multiple": r_planned, "resolved": resolved, "mgmt": mgmt, "favorable_pts": fav,
                "adverse_pts": adv, "giveback_pts": dd, "entry_gapped": False})
    return res


def engine_results(eng, ledger, min_r):
    """Row dicts for the engine's FILLED fade trades, in fill order (go-with trades are not part of the fade playbook)."""
    led = Ledger(ledger, eng.lv.fate, eng.lv.bars)
    out = [_result(tr, led, min_r) for tr in eng.trades if all(pb == "fade" for _, _, pb in tr["serves"])]
    if led.miss:
        print(f"[engine rows] {len(led.miss)} level(s) not in the ledger lookup, e.g. {led.miss[:2]}; ledger has "
              f"{len(led.df)} rows, e.g. {list(led.pos)[:2]}", flush=True)
    return out
