"""
"hammer/shooting-star formation, strong breakout" LXPB strategy.
=================================================================

Lives in its own folder (`D:\\lxpb\\lxpb-spike`) but is a thin strategy layer
over `..\\lxpb-v2`: level detection (`lxpb.py`), the gap-row filter and
tick-accurate exit resolver (`analyze_breakout_exits_1min.py` /
`render_stop_target_report.py`) are all imported from there rather than
reimplemented, so results stay apples-to-apples with `lxpb-v2`'s own
reports and with the sibling `lxpb-spike-atr` strategy.

Rules
-----
  level    A completed LXPB retest (`lxpb.detect_lxpb_h1` on H1). LHPB =
           broke up, retested from above -> LONG. LLPB = broke down,
           retested from below -> SHORT.
  filter 1 Formation-candle pattern (NOTE the polarity -- see below):
             LHPB is valid only if its FORMATION bar is a HAMMER.
             LLPB is valid only if its FORMATION bar is a SHOOTING STAR.
           All other formations are invalid and dropped. Both patterns are
           judged by the canonical `D:\\daily-analysis\\patterns-pure`
           implementations (`find_hammer.find_hammer`,
           `find_shooting_star.find_shooting_star`) applied to the
           formation bar -- NOT lxpb.py's own simplified inline
           is_hammer/is_shootingstar, and NOT
           render_labels_report.is_spike_pp's mapping either (that helper
           uses the OPPOSITE polarity: LHPB->shooting-star,
           LLPB->hammer -- see its own docstring / patterns_pure/README.md
           "Spike-side mapping"). This strategy's polarity is a deliberate,
           explicit instruction and matches lxpb.py's own native `is_spike`
           mapping, just computed from the more rigorous patterns-pure
           logic instead of lxpb.py's inline version.
  filter 2 Breakout strength: the ORIGINAL breakout bar (the one that broke
           the level out) must be a "strong" impulse, using the same
           `breakout_range_ratio` definition as
           `../retest-vol-scalp/lxpb_fade_report.html` ("Breakout strength"
           section) and `../lxpb-v2`'s own WIDE_BREAKOUT_RATIO_THRESHOLD:
           breakout bar (high-low) range, divided by the trailing 20-bar
           average range (shift(1), no look-ahead), must be
           >= WIDE_BREAKOUT_RATIO_THRESHOLD (2.0 = at least double the
           recent normal range).
  entry    The LXPB level's own retest price (`entry_price`), lxpb.py
           Phase 2.
  stop     Fixed 3 points from entry.
  target   Fixed 9 points from entry (3R).

Exit resolution reuses the tick-accurate machinery shared with
exit_analysis_report.html / render_stop_target_report.py -- entry anchored
to the real 1s aggressor-side fill instant inside the retest hour, exits
pinned to the exact second via real 1s .scid ticks, with
`_pin_exact_exit(not_before=touch_time)` so a same-minute exit can never be
credited before the entry actually happened (see ../AGENTS.md history of
that bug).

Usage:
    python analyze_hammer_star_strategy.py
    python analyze_hammer_star_strategy.py --start 2026-07-01 --end 2026-08-31
    python analyze_hammer_star_strategy.py --stop 3 --target 9
"""
import os
import sys
import argparse

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
# This strategy lives in its own folder but reuses lxpb-v2's detection /
# tick-resolution machinery wholesale, so lxpb-v2 goes on the path too.
_LXPB_V2 = os.path.abspath(os.path.join(_HERE, os.pardir, "lxpb-v2"))
for _p in (_LXPB_V2, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# render_labels_report puts D:\lxpb (for lxpb.py), D:\lxpb\data and
# D:\daily-analysis\patterns-pure on sys.path as a side effect of import,
# and already imports patterns-pure's find_hammer/find_shooting_star as
# R._pp_find_hammer / R._pp_find_shooting_star -- reused verbatim below
# rather than re-imported, to avoid a second, differently-ordered sys.path
# insert for the same D:\daily-analysis\patterns-pure directory.
import render_labels_report as R  # noqa: E402
import analyze_breakout_exits as A  # noqa: E402
import analyze_breakout_exits_1min as M  # noqa: E402
import render_stop_target_report as S  # noqa: E402
import lxpb as L  # noqa: E402

DEFAULT_DATA = A.DATA_PATH   # ../lxpb-v2/data/24aug-CME_MINI_ES1!, 60.csv -- same series
DEFAULT_SYMBOL = "ES1!"      # as lxpb-v2's own exit reports, for apples-to-apples results
DEFAULT_START = "2026-07-01"
DEFAULT_END = "2026-08-31"

STOP_DEFAULT = 3.0
TARGET_DEFAULT = 9.0

# Its own cache file, NOT analyze_breakout_exits_1min's default: that cache
# is keyed by trade INDEX, which is only unique within one fixed trade
# list -- see build_or_load_1min_series's `cache_path` docstring.
CACHE_1MIN_PATH = os.path.join(_HERE, "data", "1min_hammer_star_window.csv")


# ---------------------------------------------------------------------------
# Level selection
# ---------------------------------------------------------------------------

def load_retests(data_path=DEFAULT_DATA, symbol=DEFAULT_SYMBOL,
                  start=DEFAULT_START, end=DEFAULT_END, include_gaps=False):
    """Completed LXPB retests whose RETEST fell in [start, end], with the
    same gap-row exclusion the labeling/exit reports use.

    Returns (h1_df, retests_df, all_broken, n_gap)."""
    h1_df = L.load_ohlc_data(data_path)
    h1_df.attrs["symbol"] = symbol
    _touch_lv0, touch_lv1_df, retests_df = L.detect_lxpb_h1(h1_df)
    all_broken = R.build_all_broken_out(retests_df, touch_lv1_df)

    n_gap = 0
    if not include_gaps:
        retests_df, n_gap = R.filter_gap_rows(h1_df, retests_df)

    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    retests_df = retests_df[(retests_df["retest_time"] >= lo) &
                             (retests_df["retest_time"] <= hi)]
    retests_df = retests_df.sort_values("retest_time").reset_index(drop=True)
    return h1_df, retests_df, all_broken, n_gap


def is_valid_formation(h1_df, pos_by_ts, level_type, formation_time):
    """Formation-candle filter for THIS strategy (see module docstring for
    the polarity rationale): LHPB valid <=> formation bar is a HAMMER;
    LLPB valid <=> formation bar is a SHOOTING STAR. Both judged by
    patterns-pure on a 2-row slice ending at the formation bar (mirrors
    render_labels_report.is_spike_pp's own slicing convention, so a valid
    previous bar is always available for the shift(1)-based confirmation
    check inside find_hammer/find_shooting_star)."""
    fi = pos_by_ts[formation_time]
    slice_2 = h1_df.iloc[max(0, fi - 1):fi + 1]
    if level_type == "LHPB":
        matched = R._pp_find_hammer(slice_2, atr=0.0)
    else:
        matched = R._pp_find_shooting_star(slice_2, atr=0.0)
    return formation_time in matched.index


def add_pattern_flag(h1_df, retests_df):
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    flags = [is_valid_formation(h1_df, pos_by_ts, row["type"], row["formation_time"])
              for _, row in retests_df.iterrows()]
    return retests_df.assign(valid_formation=flags)


def add_breakout_strength(h1_df, retests_df):
    """breakout_range_ratio per row -- breakout bar's high-low range divided
    by its own trailing-20-bar average range (shift(1), no look-ahead).
    Same definition as ../retest-vol-scalp/lxpb_fade_report.html's
    "Breakout strength" section and ../lxpb-v2's own
    WIDE_BREAKOUT_RATIO_THRESHOLD ("strong breakout") convention."""
    ratios = R.compute_range_ratio_col(h1_df, retests_df)
    strong = [r is not None and r >= R.WIDE_BREAKOUT_RATIO_THRESHOLD for r in ratios]
    return retests_df.assign(range_ratio=ratios, strong_breakout=strong)


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def select_trades(rows_df):
    """Strategy row filter: valid hammer/shooting-star formation AND a
    strong original breakout. Returns (selected_df, funnel_counts)."""
    funnel = {"rows": len(rows_df)}
    df = rows_df[rows_df["valid_formation"]]
    funnel["valid_formation"] = len(df)
    df = df[df["strong_breakout"]]
    funnel["strong_breakout"] = len(df)
    return df.reset_index(drop=True), funnel


def build_trades(rows_df, stop=STOP_DEFAULT, target=TARGET_DEFAULT):
    """Trade dicts in the shape analyze_breakout_exits_1min /
    render_stop_target_report expect."""
    trades = []
    for _, row in rows_df.iterrows():
        trades.append({
            "type": row["type"],
            "is_long": row["type"] == "LHPB",
            "entry": float(row["entry_price"]),
            "retest_time": row["retest_time"],
            "stop_dist": float(stop),
            "target_dist": float(target),
        })
    return trades


def resolve(trades, stop=STOP_DEFAULT, target=TARGET_DEFAULT, cache_path=CACHE_1MIN_PATH):
    """1-minute walk-forward + 1s exit pinning for the fixed stop/target bracket."""
    series_by_idx = M.build_or_load_1min_series(trades, cache_path=cache_path)
    return series_by_idx, S.resolve_trades(trades, series_by_idx, stop=stop, target=target)


def summarize(trades, resolved_list):
    r_values = [r["r"] for r in resolved_list if r["r"] is not None]
    wins = sum(1 for r in resolved_list if r["outcome"] == "target")
    losses = sum(1 for r in resolved_list if r["outcome"] == "stop")
    return {
        "n": len(trades),
        "wins": wins,
        "losses": losses,
        "no_hit": sum(1 for r in resolved_list if r["outcome"] == "no_hit"),
        "no_data": sum(1 for r in resolved_list if r["outcome"] == "no_data"),
        "win_rate": (wins / len(r_values)) if r_values else float("nan"),
        "avg_R": float(np.mean(r_values)) if r_values else float("nan"),
        "total_R": float(np.sum(r_values)) if r_values else float("nan"),
    }


def load_strategy_rows(data_path=DEFAULT_DATA, symbol=DEFAULT_SYMBOL,
                        start=DEFAULT_START, end=DEFAULT_END,
                        include_gaps=False):
    """Whole selection pipeline in one call (used by
    render_hammer_star_report.py). Returns (h1_df, rows_df, all_broken, funnel)."""
    h1_df, retests_df, all_broken, n_gap = load_retests(data_path, symbol, start, end, include_gaps)
    retests_df = add_pattern_flag(h1_df, retests_df)
    retests_df = add_breakout_strength(h1_df, retests_df)
    rows_df, funnel = select_trades(retests_df)
    funnel["gap_rows_dropped"] = n_gap
    return h1_df, rows_df, all_broken, funnel


def _add_cli_args(parser):
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--start", default=DEFAULT_START, help="earliest RETEST date (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="latest RETEST date (YYYY-MM-DD, inclusive)")
    parser.add_argument("--stop", type=float, default=STOP_DEFAULT, help="stop distance in points")
    parser.add_argument("--target", type=float, default=TARGET_DEFAULT, help="target distance in points")
    parser.add_argument("--include-gaps", action="store_true",
                         help="keep levels whose formation/breakout/retest only happened via a price gap")
    return parser


if __name__ == "__main__":
    parser = _add_cli_args(argparse.ArgumentParser(
        description="Hammer(LHPB)/Shooting-star(LLPB) formation + strong-breakout LXPB retests, "
                    "fixed stop/target bracket"))
    args = parser.parse_args()
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    h1_df, retests_df, _all_broken, n_gap = load_retests(args.data, args.symbol, args.start, args.end,
                                                          args.include_gaps)
    retests_df = add_pattern_flag(h1_df, retests_df)
    retests_df = add_breakout_strength(h1_df, retests_df)
    rows_df, funnel = select_trades(retests_df)
    funnel["gap_rows_dropped"] = n_gap

    print(f"Data: {args.data}  ({len(h1_df)} H1 bars)")
    print(f"Retest window: {args.start} .. {args.end}   stop={args.stop}pt  target={args.target}pt")
    print("\n=== Selection funnel ===")
    print(f"  completed retests in window ........ {funnel['rows']} "
          f"(+{funnel['gap_rows_dropped']} gap rows dropped repo-wide)")
    print(f"  valid formation (hammer/star) ....... {funnel['valid_formation']}")
    print(f"  strong breakout (>= {R.WIDE_BREAKOUT_RATIO_THRESHOLD}x 20-bar avg range) . {funnel['strong_breakout']}")

    if rows_df.empty:
        print("\nNo qualifying trades -- nothing to resolve.")
        sys.exit(0)

    trades = build_trades(rows_df, args.stop, args.target)
    print(f"\nResolving {len(trades)} qualifying trades at 1-minute + 1s-tick resolution ...")
    _series, resolved_list = resolve(trades, args.stop, args.target)
    stats = summarize(trades, resolved_list)
    print("\n=== Qualifying trades ===")
    print(pd.Series(stats).round(3).to_string())

    detail = pd.DataFrame([{
        "type": t["type"],
        "entry_time": R._to_pt_str(r["touch_time"]) if r.get("touch_time") is not None else "-",
        "entry": t["entry"], "stop_pts": t["stop_dist"], "target_pts": t["target_dist"],
        "outcome": r["outcome"], "R": round(r["r"], 3) if r["r"] is not None else None,
        "exit_time": R._to_pt_str(r["exit_time"]) if r["exit_time"] is not None else "-",
    } for t, r in zip(trades, resolved_list)])
    print("\n" + detail.to_string(index=False))
