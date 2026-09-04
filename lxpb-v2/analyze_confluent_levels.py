"""
analyze_confluent_levels.py
============================
New strategy idea (not yet wired into any report): pair a retest-level trade
with a nearby "confluent" LXPB level.

Definition (per row-26 example of stop2_target8_trades_report.html: LHPB
7523.25, retest 2026-07-22 22:00 UTC / 15:02:13 PT):

  1. Treat the trade's own level (price P) as a zone [P-N, P+N], N points wide.
  2. Look BACKWARD in time from the trade's own retest (the decision point --
     nothing after it may be used) for another LHPB/LLPB level (either type --
     "same-or-opposite") whose price falls inside that zone.
  3. That candidate only counts if it had a CONFIRMED breakout by then: its
     close actually passed through its own price. Concretely, on the ledger's
     `fate` column (see lxpb_levels_cache.py), it must be one of
     {retested, consumed_early, open_awaiting_retest} -- all three have a real
     breakout_time. It must NOT be:
       - discarded_no_close: price wicked through but the bar's CLOSE didn't
         confirm (for an LLPB that's "wick below, close above" -- a failed
         breakdown; mirrored close-below-a-high for LHPB) -- i.e. exactly the
         "failed breakout" the strategy explicitly excludes.
       - open_unbroken: never even broken out.
  4. Among everything that passes, the MOST RECENTLY FORMED one is "the"
     confluent level (the strategy pairs with *recent* nearby structure).
  5. "Confluence" only reaches back CONFLUENCE_LOOKBACK_BARS H1 bars before
     the trade's own P1 (breakout) bar -- levels formed earlier than that
     don't count, however close in price, so a handful of very old levels
     can't masquerade as "recent" structure.

A narrower "same-side" reading (same_side_live_confluence) restricts this to
levels of the SAME type as the subject (an LHPB retest only counts nearby
LHPB structure, an LLPB retest only counts nearby LLPB structure) that were
NOT yet retested as of the subject's own P1 (breakout) bar -- i.e. still live,
reinforcing structure at breakout time rather than already-resolved history.

Worked example: LHPB 7523.25 formed 2026-07-22 12:00 UTC. With the default
N=2.5, the zone is [7520.75, 7525.75]. The most recent qualifying level
inside it is LLPB 7524.50, formed 2026-07-22 10:00 UTC (2026-07-22 03:00 PT)
-- 1.25 pts away, breakout_time 2026-07-22 11:00 UTC, fate "retested" (clean
breakout, later retested/died on its own at 13:00 UTC, well before our
trade's own 22:00 UTC retest). That matches the row-26 confluence by hand.

This script only *identifies* confluence for one row at a time -- it does not
change trade selection/resolution or regenerate any report. The actual
`find_confluent_levels` / `same_side_live_confluence` queries live in
lxpb_levels_cache.py (shared with render_stop_target_report.py, which draws
find_confluent_levels on the H1 chart / Confl. column, and
same_side_live_confluence in the SS Confl. column).

Usage:
    python analyze_confluent_levels.py --row 26 --n 2.5
    python analyze_confluent_levels.py --row 26 --n 1    # (for contrast)
"""
import argparse

import pandas as pd

import render_stop_target_report as SR  # noqa: E402 -- reuses the exact trade
import render_labels_report as R        # noqa: E402 -- selection stop2_target8
import lxpb_levels_cache as LC          # noqa: E402 -- _trades_report.html uses


def _fmt(df):
    show = df[["type", "price", "dist", "formation_time", "breakout_time",
               "retest_time", "death_time", "fate"]].copy()
    for c in ("formation_time", "breakout_time", "retest_time", "death_time"):
        show[c] = show[c].apply(lambda t: R._to_pt_str(t) if pd.notna(t) else "-")
    return show.to_string(index=False)


def main():
    parser = argparse.ArgumentParser(
        description="Identify the confluent LXPB level for one retest-level "
                     "trade (row index into the default stop2_target8 "
                     "selection, i.e. the same trades as "
                     "stop2_target8_trades_report.html).")
    parser.add_argument("--row", type=int, default=26,
                        help="row index (0-based, matches the HTML report's # column)")
    parser.add_argument("--n", type=float, default=SR.CONFLUENCE_N_POINTS,
                        help=f"zone half-width in points (default {SR.CONFLUENCE_N_POINTS})")
    parser.add_argument("--lookback-bars", type=int, default=SR.CONFLUENCE_LOOKBACK_BARS,
                        help="max H1 bars before this trade's own P1 (breakout) bar a "
                             f"candidate may have formed in (default {SR.CONFLUENCE_LOOKBACK_BARS})")
    args = parser.parse_args()

    # Same selection the plain (non-full-year) stop/target reports use --
    # see render_stop_target_report._select_rows / analyze_breakout_exits
    # DEFAULT_START/END/LIMIT.
    h1_df, pos_by_ts, strong, _trades = SR._select_rows()
    if not (0 <= args.row < len(strong)):
        raise SystemExit(f"--row {args.row} out of range (0..{len(strong)-1})")
    subject = strong.iloc[args.row]
    level_type = subject["type"]
    price = float(subject["price"])
    formation_time = subject["formation_time"]
    breakout_time = subject["breakout_time"]      # P1 -- same_side_live_confluence cutoff
    cutoff_time = subject["retest_time"]          # P2 -- the trade's own retest, decision point

    form_pos = pos_by_ts[breakout_time]
    lookback_pos = max(0, form_pos - args.lookback_bars)
    min_formation_time = h1_df.index[lookback_pos]

    print(f"Row {args.row}: {level_type} {price:.2f}  "
          f"formed {R._to_pt_str(LC._as_utc(formation_time))}  "
          f"retest {R._to_pt_str(LC._as_utc(cutoff_time))}")
    print(f"Zone (N={args.n}): [{price - args.n:.2f}, {price + args.n:.2f}]")
    print(f"Lookback bound ({args.lookback_bars} bars before P1): "
          f"formed at/after {R._to_pt_str(min_formation_time)}")

    ledger = LC.h1_levels()
    cand = LC.find_confluent_levels(ledger, level_type, price, formation_time,
                                    cutoff_time, args.n, min_formation_time=min_formation_time)

    if cand.empty:
        print("\nNo confluent level found in this zone before the retest.")
        return

    print(f"\n{len(cand)} qualifying level(s) in zone, most recent first:")
    print(_fmt(cand))
    top = cand.iloc[0]
    print(f"\n=> Confluent level: {top['type']} {top['price']:.2f} "
          f"({top['dist']:.2f} pts away), formed {R._to_pt_str(top['formation_time'])}, "
          f"fate={top['fate']}")

    same_side = LC.same_side_live_confluence(cand, level_type, breakout_time)
    print(f"\nSame-side (type={level_type}) confluence still live as of P1 "
          f"({R._to_pt_str(LC._as_utc(breakout_time))}): {len(same_side)}")
    if not same_side.empty:
        print(_fmt(same_side))


if __name__ == "__main__":
    main()
