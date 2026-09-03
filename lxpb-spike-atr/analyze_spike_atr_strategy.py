"""
"spike-only, beyond the ATR boundary" LXPB strategy.
=====================================================

Lives in its own folder (`D:\\lxpb\\lxpb-spike-atr`) but is a thin strategy
layer over `..\\lxpb-v2`: level detection (`lxpb.py`), the gap/spike row
filters (`render_labels_report.py`) and the tick-accurate exit resolver
(`analyze_breakout_exits_1min.py` + `render_stop_target_report.py`) are all
imported from there rather than reimplemented, so results stay
apples-to-apples with `lxpb-v2/exit_analysis_report.html`.

A fully structural, zero-grid-search variant of the LXPB retest trade --
every leg is derived from the level itself or from the daily ATR, nothing
is swept:

  entry   the LXPB level's own retest price (`entry_price`), i.e. lxpb.py
          Phase 2. LHPB = LONG (broken-out swing high retested as support),
          LLPB = SHORT.
  filter  the FORMATION bar must be a **spike**, judged by patterns-pure
          (`D:\\daily-analysis\\patterns-pure`): `find_shooting_star` for
          LHPB (rejection of higher prices -> long upper wick),
          `find_hammer` for LLPB. This is the same LHPB->shooting-star /
          LLPB->hammer mapping `render_labels_report.is_spike_pp` and
          `analyze_retest_cluster_selection.py` use, NOT lxpb.py's own
          `is_spike` field, which maps the two the other way round (see
          patterns_pure/README.md -> "Spike-side mapping").
  stop    lxpb.py's own `stop_loss`: beyond the BREAKOUT candle --
          `breakout_low` for LHPB (below it), `breakout_high` for LLPB
          (above it).
  target  lxpb.py's own `fta` (First Trouble Area): min(low) of the bars
          strictly between the breakout and retest bars for LHPB,
          max(high) for LLPB. See D:\\daily-analysis\\lxpb-h1-apr2026\\README.md
          for the canonical definition.
  filter  the trade is only taken when price is **beyond the moving daily
          ATR boundary** -- either already at entry, or by the time it
          would reach the stop (see `atr_boundary_frame` below).

ATR boundary
------------
D1 candles are built from the H1 series by `helpers.calculateD1W1`
(D:\\daily-analysis), so the trading-day boundary is the symbol's real
session close (17:00 ET for ES/NQ via `helpers.market_sessions`), never a
naive UTC midnight. ATR is patterns-pure `findATR` (Wilder RMA, **period
21**, project convention) over the D1 candles that had already CLOSED
before the trade's own trading day.

The boundary itself is *moving* -- it is re-derived at the point in time of
the trade from the trading day's range **so far**:

    lower_atr_boundary = (day high so far) - ATR
    upper_atr_boundary = (day low  so far) + ATR

i.e. how far price could still travel if the day only just completed one
full average daily range.

The level is traded only if **price is beyond that boundary, or would be
beyond it once it reached the stop price**:

    LHPB (long)   entry  < lower_atr_boundary   OR   stop  < lower_atr_boundary
    LLPB (short)  entry  > upper_atr_boundary   OR   stop  > upper_atr_boundary

Either way the market would have to exceed a full ATR of daily range,
measured from the extreme it has already printed, before the trade could
be stopped out. For a normally-oriented bracket the stop always sits
further from the boundary's anchor than the entry does, so the stop leg
subsumes the entry leg and the OR reduces to the stop test; the entry leg
is kept explicit because it is the rule as stated, and because it is the
one that still holds if a degenerate row ever puts the stop on the same
side of the entry as the target (`select_trades` rejects those separately).

Note the boundary does not drift while the trade is open in the direction
that matters: reaching a long's stop takes price DOWN, which cannot raise
the day high the lower boundary is anchored to (and vice versa for a
short), so testing the stop against the entry-time boundary is exact, not
an approximation.

"Day so far" is strictly point-in-time (no look-ahead): the running
high/low of every H1 bar of that trading day that had already CLOSED
before the retest bar, extended with the retest bar's own OPEN and with
the entry price itself (price demonstrably traded from the open to the
level in order to fill the entry). The retest bar's own high/low are NOT
used -- most of that bar's range happens after the fill. (Verified on the
bundled Jul-Aug 2026 sample: using the full retest bar instead changes no
row's verdict, so the strict version costs nothing.)

Exit resolution reuses the tick-accurate machinery shared with
exit_analysis_report.html / render_stop_target_report.py -- entry anchored
to the real 1s aggressor-side fill instant inside the retest hour, exits
pinned to the exact second via real 1s .scid ticks, with
`_pin_exact_exit(not_before=touch_time)` so a same-minute exit can never be
credited before the entry actually happened.

Usage:
    python analyze_spike_atr_strategy.py
    python analyze_spike_atr_strategy.py --start 2026-07-01 --end 2026-08-31
    python analyze_spike_atr_strategy.py --atr-mult 0.75      # relax the boundary
    python analyze_spike_atr_strategy.py --atr-d1-window 21   # ATR over last 21 D1 bars only
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
# D:\daily-analysis\patterns-pure on sys.path as a side effect of import.
import render_labels_report as R  # noqa: E402
import analyze_breakout_exits as A  # noqa: E402
import analyze_breakout_exits_1min as M  # noqa: E402
import render_stop_target_report as S  # noqa: E402
import lxpb as L  # noqa: E402

from find_ATR import findATR  # noqa: E402  (patterns-pure, via R's sys.path)

# `helpers` is a package (relative imports), so its PARENT goes on the path.
# Appended, never inserted: D:\daily-analysis also contains an `lxpb`
# package that would otherwise shadow D:\lxpb\lxpb.py.
_DAILY_ANALYSIS = r"D:\daily-analysis"
if _DAILY_ANALYSIS not in sys.path:
    sys.path.append(_DAILY_ANALYSIS)
from helpers.calculateD1W1 import calculateD1W1  # noqa: E402
from helpers.market_sessions import trading_day_labels  # noqa: E402

# Same H1 series analyze_breakout_exits.py / render_stop_target_report.py /
# exit_analysis_report.html use, so this strategy's rows are directly
# comparable with those reports (it starts 2025-07-23, ~230 trading days of
# D1 history before the Jul-2026 window -- far more than ATR(21) needs).
DEFAULT_DATA = A.DATA_PATH
DEFAULT_SYMBOL = "ES1!"
DEFAULT_START = "2026-07-01"
DEFAULT_END = "2026-08-31"

ATR_PERIOD = 21          # project-wide convention -- see patterns-pure/find_ATR.py
ATR_MULT_DEFAULT = 1.0   # boundary = day extreme -/+ ATR_MULT * ATR
ATR_D1_WINDOW_DEFAULT = 0  # 0 = full history (converged Wilder RMA, = ta.atr(21))
MIN_D1_BARS = ATR_PERIOD + 4  # need a warmed-up RMA before a day is tradeable
TICK_SIZE = 0.25

# Its own cache file, NOT analyze_breakout_exits_1min's: that cache is keyed
# by trade INDEX, which only means anything within one fixed trade list --
# see build_or_load_1min_series's `cache_path` docstring.
CACHE_1MIN_PATH = os.path.join(_HERE, "data", "1min_spike_atr_window.csv")


# ---------------------------------------------------------------------------
# Level selection
# ---------------------------------------------------------------------------

def load_retests(data_path=DEFAULT_DATA, symbol=DEFAULT_SYMBOL,
                 start=DEFAULT_START, end=DEFAULT_END, include_gaps=False):
    """Completed LXPB retests whose RETEST fell in [start, end], with the
    same gap-row exclusion the labeling/exit reports use (a level whose
    formation, breakout or retest only happened via a price gap never
    really traded there -- see render_labels_report._is_gap_lxpb).

    Returns (h1_df, retests_df, all_broken, n_gap). `all_broken` is the
    confluence universe render_labels_report.compute_hints needs -- EVERY
    level that ever broke out across the whole series (completed retests +
    still-open touch_lv1), not just the ones inside the date window, so the
    confluence hint counts real neighbouring levels rather than only the
    handful this strategy happens to select. compute_hints still ignores
    any level that broke out after the row's own retest, so this stays
    look-ahead free."""
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


def add_spike_flag(h1_df, retests_df):
    """`is_spike_pp` (patterns-pure shooting-star/hammer) per row -- see
    module docstring for why lxpb.py's own `is_spike` field is not used."""
    pos_by_ts = {ts: i for i, ts in enumerate(h1_df.index)}
    flags = [R.is_spike_pp(h1_df, pos_by_ts, row["type"], row["formation_time"])
             for _, row in retests_df.iterrows()]
    return retests_df.assign(spike=flags)


# ---------------------------------------------------------------------------
# Moving daily-ATR boundary
# ---------------------------------------------------------------------------

def atr_boundary_frame(h1_df, symbol=DEFAULT_SYMBOL, atr_d1_window=ATR_D1_WINDOW_DEFAULT):
    """Pre-compute everything the per-row boundary needs, once for the whole
    H1 series.

    Returns (day_label, prior_high, prior_low, atr_by_day):
      day_label   Series[H1 timestamp -> D1 trading-day label] from
                  helpers.market_sessions.trading_day_labels (session close
                  aware, DST aware), the same labeling calculateD1W1 uses.
      prior_high  Series[H1 timestamp -> running high of that trading day
      prior_low   across every bar of it that CLOSED STRICTLY BEFORE this
                  one] -- NaN on a session's first bar. Point-in-time by
                  construction: a bar never sees itself or any later bar.
      atr_by_day  {day label -> ATR(21) over the D1 candles that closed
                  before that day}, None where history is too short.
    """
    d1_df, _w1 = calculateD1W1(h1_df, symbol)
    labels = pd.Series(trading_day_labels(h1_df.index, symbol), index=h1_df.index)

    running_high = h1_df["high"].groupby(labels).cummax()
    running_low = h1_df["low"].groupby(labels).cummin()
    prior_high = running_high.groupby(labels).shift(1)
    prior_low = running_low.groupby(labels).shift(1)

    atr_by_day = {}
    for day in labels.unique():
        closed = d1_df.loc[d1_df.index < day]
        if len(closed) < MIN_D1_BARS:
            atr_by_day[day] = None
            continue
        if atr_d1_window:
            closed = closed.tail(atr_d1_window)
        atr_by_day[day] = float(findATR(closed, period=ATR_PERIOD))

    return labels, prior_high, prior_low, atr_by_day


def add_atr_boundary(h1_df, retests_df, symbol=DEFAULT_SYMBOL,
                     atr_mult=ATR_MULT_DEFAULT, atr_d1_window=ATR_D1_WINDOW_DEFAULT):
    """Attach the point-in-time ATR-boundary columns + the `beyond_atr`
    verdict to every retest row.

    The verdict is the OR the strategy is defined by: trade the level only
    if price is ALREADY beyond the boundary at entry, or WOULD be beyond it
    once it reached the stop. `beyond_atr_leg` records which leg carried it
    ("entry", "stop" or "" when neither did)."""
    labels, prior_high, prior_low, atr_by_day = atr_boundary_frame(
        h1_df, symbol, atr_d1_window)

    cols = {k: [] for k in ("atr", "day_high_sofar", "day_low_sofar",
                            "atr_lower", "atr_upper", "atr_boundary",
                            "stop_atr_margin", "entry_atr_margin",
                            "beyond_atr", "beyond_atr_leg")}
    for _, row in retests_df.iterrows():
        ts = row["retest_time"]
        atr = atr_by_day.get(labels.loc[ts])
        if atr is None:
            for k in cols:
                cols[k].append("" if k == "beyond_atr_leg"
                               else (False if k == "beyond_atr" else None))
            continue

        # Everything already known at the instant the entry filled: the
        # session's closed bars, the retest bar's open, and the level.
        known = [row["retest_open"], row["entry_price"]]
        ph, pl = prior_high.loc[ts], prior_low.loc[ts]
        day_high = float(np.nanmax(known + ([ph] if ph == ph else [])))
        day_low = float(np.nanmin(known + ([pl] if pl == pl else [])))

        band = atr * atr_mult
        lower, upper = day_high - band, day_low + band
        is_long = row["type"] == "LHPB"
        boundary = lower if is_long else upper
        stop = float(row["stop_loss"])
        entry = float(row["entry_price"])
        # Positive margin = beyond the boundary, i.e. on the far side of a
        # full ATR of daily range from the extreme already printed.
        stop_margin = (lower - stop) if is_long else (stop - upper)
        entry_margin = (lower - entry) if is_long else (entry - upper)

        cols["atr"].append(atr)
        cols["day_high_sofar"].append(day_high)
        cols["day_low_sofar"].append(day_low)
        cols["atr_lower"].append(lower)
        cols["atr_upper"].append(upper)
        cols["atr_boundary"].append(boundary)
        cols["stop_atr_margin"].append(stop_margin)
        cols["entry_atr_margin"].append(entry_margin)
        cols["beyond_atr"].append(bool(entry_margin > 0 or stop_margin > 0))
        cols["beyond_atr_leg"].append(
            "entry" if entry_margin > 0 else ("stop" if stop_margin > 0 else ""))

    return retests_df.assign(**cols)


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def select_trades(rows_df, qualifying_only=True):
    """Strategy row filter: spike formation, a usable FTA target and a
    usable structural stop, and (unless disabled) price beyond the moving
    ATR boundary at the entry or by the time it reaches the stop.
    Returns (selected_df, funnel_counts)."""
    funnel = {"rows": len(rows_df)}
    df = rows_df[rows_df["spike"]]
    funnel["spike"] = len(df)
    df = df[df["fta"].notna() & df["stop_loss"].notna()]
    funnel["has_fta_and_stop"] = len(df)
    sizes_ok = ((df["fta"] - df["entry_price"]).abs() >= TICK_SIZE) & \
               ((df["entry_price"] - df["stop_loss"]).abs() >= TICK_SIZE)
    df = df[sizes_ok]
    funnel["nonzero_bracket"] = len(df)
    # Orientation guard: the stop has to be on the OPPOSITE side of the
    # entry from the target. A gap can leave the whole breakout bar on the
    # far side of the level, which puts `stop_loss` on the target side --
    # not a tradeable bracket, and it would also invert the ATR test's
    # entry-vs-stop ordering.
    is_long = df["type"] == "LHPB"
    oriented = ((is_long & (df["stop_loss"] < df["entry_price"]) & (df["fta"] > df["entry_price"])) |
                (~is_long & (df["stop_loss"] > df["entry_price"]) & (df["fta"] < df["entry_price"])))
    funnel["inverted_bracket_dropped"] = int((~oriented).sum())
    df = df[oriented]
    funnel["oriented_bracket"] = len(df)
    funnel["beyond_atr"] = int(df["beyond_atr"].sum())
    if qualifying_only:
        df = df[df["beyond_atr"]]
    return df.reset_index(drop=True), funnel


def build_trades(rows_df):
    """Trade dicts in the shape analyze_breakout_exits_1min /
    render_stop_target_report expect, with the structural bracket carried
    per trade in `stop_dist` / `target_dist` (S.resolve_trades reads those
    when its `stop`/`target` args are left None)."""
    trades = []
    for _, row in rows_df.iterrows():
        entry = float(row["entry_price"])
        trades.append({
            "type": row["type"],
            "is_long": row["type"] == "LHPB",
            "entry": entry,
            "retest_time": row["retest_time"],
            "stop_dist": abs(entry - float(row["stop_loss"])),
            "target_dist": abs(float(row["fta"]) - entry),
        })
    return trades


def resolve(trades, cache_path=CACHE_1MIN_PATH):
    """1-minute walk-forward + 1s exit pinning for the structural bracket."""
    series_by_idx = M.build_or_load_1min_series(trades, cache_path=cache_path)
    return series_by_idx, S.resolve_trades(trades, series_by_idx)


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
        "avg_stop_pts": float(np.mean([t["stop_dist"] for t in trades])) if trades else float("nan"),
        "avg_target_pts": float(np.mean([t["target_dist"] for t in trades])) if trades else float("nan"),
    }


def load_strategy_rows(data_path=DEFAULT_DATA, symbol=DEFAULT_SYMBOL,
                       start=DEFAULT_START, end=DEFAULT_END,
                       atr_mult=ATR_MULT_DEFAULT, atr_d1_window=ATR_D1_WINDOW_DEFAULT,
                       qualifying_only=True, include_gaps=False):
    """Whole selection pipeline in one call (used by
    render_spike_atr_report.py). Returns (h1_df, rows_df, all_broken, funnel)."""
    h1_df, retests_df, all_broken, n_gap = load_retests(data_path, symbol, start, end, include_gaps)
    retests_df = add_spike_flag(h1_df, retests_df)
    retests_df = add_atr_boundary(h1_df, retests_df, symbol, atr_mult, atr_d1_window)
    rows_df, funnel = select_trades(retests_df, qualifying_only)
    funnel["gap_rows_dropped"] = n_gap
    return h1_df, rows_df, all_broken, funnel


def _add_cli_args(parser):
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--start", default=DEFAULT_START, help="earliest RETEST date (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="latest RETEST date (YYYY-MM-DD, inclusive)")
    parser.add_argument("--atr-mult", type=float, default=ATR_MULT_DEFAULT,
                        help="boundary = day extreme -/+ this many ATRs (1.0 = a full ATR)")
    parser.add_argument("--atr-d1-window", type=int, default=ATR_D1_WINDOW_DEFAULT,
                        help="0 = ATR(21) over all closed D1 bars (converged Wilder RMA, "
                             "= TradingView ta.atr(21)); N = only the last N D1 bars "
                             "(matches D:\\daily-analysis mean-reversion-playbook's tail(21))")
    parser.add_argument("--include-gaps", action="store_true",
                        help="keep levels whose formation/breakout/retest only happened via a price gap")
    return parser


if __name__ == "__main__":
    parser = _add_cli_args(argparse.ArgumentParser(
        description="Spike-only LXPB retests taken only when price is beyond the moving daily "
                    "ATR boundary at the entry, or would be by the time it reached the stop"))
    args = parser.parse_args()
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    h1_df, retests_df, _all_broken, n_gap = load_retests(args.data, args.symbol, args.start,
                                                         args.end, args.include_gaps)
    retests_df = add_spike_flag(h1_df, retests_df)
    retests_df = add_atr_boundary(h1_df, retests_df, args.symbol,
                                  args.atr_mult, args.atr_d1_window)
    rows_df, funnel = select_trades(retests_df, qualifying_only=True)
    funnel["gap_rows_dropped"] = n_gap

    print(f"Data: {args.data}  ({len(h1_df)} H1 bars)")
    print(f"Retest window: {args.start} .. {args.end}   ATR mult: {args.atr_mult}   "
          f"ATR D1 window: {args.atr_d1_window or 'full history'}")
    print("\n=== Selection funnel ===")
    print(f"  completed retests in window ...... {funnel['rows']} "
          f"(+{funnel['gap_rows_dropped']} gap rows dropped repo-wide)")
    print(f"  spike formation (patterns-pure) .. {funnel['spike']}")
    print(f"  has FTA target + structural stop . {funnel['has_fta_and_stop']}")
    print(f"  bracket >= 1 tick on both legs ... {funnel['nonzero_bracket']}")
    print(f"  stop/target correctly oriented ... {funnel['oriented_bracket']} "
          f"({funnel['inverted_bracket_dropped']} inverted dropped)")
    print(f"  beyond the ATR boundary .......... {funnel['beyond_atr']} "
          f"(at entry, or by the time price reaches the stop)")

    show = ["type", "retest_time", "entry_price", "stop_loss", "fta", "atr",
            "day_high_sofar", "day_low_sofar", "atr_boundary",
            "entry_atr_margin", "stop_atr_margin", "beyond_atr", "beyond_atr_leg"]
    spikes = retests_df[retests_df["spike"]].sort_values("stop_atr_margin", ascending=False)
    print(f"\n=== All {len(spikes)} spike retests, by how far they clear the boundary ===")
    print("(margins: positive = beyond the boundary; the level trades if EITHER is positive)")
    print(spikes[show].round(2).to_string(index=False))

    if rows_df.empty:
        print("\nNo qualifying trades -- nothing to resolve.")
        sys.exit(0)

    trades = build_trades(rows_df)
    print(f"\nResolving {len(trades)} qualifying trades at 1-minute + 1s-tick resolution ...")
    _series, resolved_list = resolve(trades)
    stats = summarize(trades, resolved_list)
    print("\n=== Qualifying trades (beyond the ATR boundary at entry or at the stop) ===")
    print(pd.Series(stats).round(3).to_string())

    detail = pd.DataFrame([{
        "type": t["type"],
        "entry_time": R._to_pt_str(r["touch_time"]) if r.get("touch_time") is not None else "-",
        "entry": t["entry"], "stop_pts": round(t["stop_dist"], 2),
        "target_pts": round(t["target_dist"], 2),
        "outcome": r["outcome"], "R": round(r["r"], 3) if r["r"] is not None else None,
        "exit_time": R._to_pt_str(r["exit_time"]) if r["exit_time"] is not None else "-",
    } for t, r in zip(trades, resolved_list)])
    print("\n" + detail.to_string(index=False))
