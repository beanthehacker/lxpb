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

TARGET_RULE = "swing-extreme"      # the engine's only target rule: the last confirmed zigzag swing since the level's P1


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
    ex = tr["exit"]
    meta = tr.get("plan_meta") or {}
    stop_pts, tgt_pts = tr["stop_pts"], tr["target_pts"]
    r_planned = tr["r_planned"]
    r = tr.get("r") if ex else None
    fav, adv, dd = tr["mfe"], tr["mae"], tr["dd"]
    resolved = {"outcome": _outcome(tr), "exit_time": _ts(ex["t"]) if ex else None,
                "exit_price": float(ex["price"]) if ex else None, "r": r,
                "touch_time": _ts(tr["t"]), "favorable_pts": fav, "adverse_pts": adv, "giveback_pts": dd,
                "entry_gapped": False}
    trail_events = [(_ts(t), float(px)) for t, px in tr["trail_log"]]
    rr_floor = bool(ex and ex["why"] == "rr_floor")
    mgmt = {"r": r, "outcome": resolved["outcome"], "exit_time": resolved["exit_time"],
            "pnl_pts": (r * stop_pts) if r is not None else None, "fired": bool(trail_events or rr_floor),
            "trail_events": trail_events, "rr_floor_fired": rr_floor}
    pv_t = meta.get("pivot_time")
    target_info = {"src": "swing_extreme", "pivot_kind": meta.get("pivot_kind", "trough" if is_long else "crest"),
                   "pivot_price": float(meta.get("pivot_price", tr["target"])),
                   "pivot_time": _ts(pv_t) if pv_t is not None else _ts(tr["t"])}
    mode_tags = []
    if r_planned < min_r:
        mode_tags.append("r_below_min")
    if ex and ex["why"] == "eod_flat":
        mode_tags.append("eod_flat")
    mode = {"target_price": float(tr["target"]), "target_pts": tgt_pts, "target_info": target_info,
            "r_multiple": r_planned, "resolved": resolved, "mgmt": mgmt, "dyn_tags": mode_tags}
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
        "stop_price": initial_stop, "stop_pts": stop_pts, "stop_source": tr["stop_src"],
        "stop_m5_level": led.dict(stop_level) if stop_level is not None else None,
        "modes": {TARGET_RULE: mode}, "active_mode": TARGET_RULE,
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
