"""
Build a JUMP-FREE, back-adjusted continuous ES H1 series for 2026 only, for
LXPB strategy backtesting (as opposed to data/build_es_h1_continuous.py,
which is the UNADJUSTED splice used by the label-review tool itself for
absolute S/R-level detection -- these are two different, deliberate
choices for two different consumers).

Reverse-engineered TradingView ES1! roll rule (fully confirmed, exact)
=======================================================================
1. ROLL TIMING: TradingView switches its ES1! continuous contract to the
   next front-month contract at the exact START of the Globex trading
   session that is 3 BUSINESS DAYS before the expiring contract's own
   3rd-Friday expiration -- i.e. 17:00 CT on the previous calendar day
   (the regular Sun-Fri Globex session open/reopen-after-maintenance
   time). This was found by diffing the unadjusted scid splice (each
   contract + its TV_GROUND_TRUTH_OFFSETS constant, below) directly
   against every bar of the real TradingView export around both 2026
   rolls: the residual is EXACTLY 0.00 on the outgoing contract's side
   right up to that instant, and EXACTLY 0.00 on the incoming contract's
   side from that instant onward -- for every single bar, not just on
   average. Both rolls (H26->M26 on 2026-03-16 22:00 UTC, M26->U26 on
   2026-06-15 22:00 UTC) landed on the identical rule (expiry - 3
   business days, 17:00 CT session open), so this is a general,
   reproducible timing rule, not a per-roll coincidence.
2. OFFSET: TradingView applies the REAL market spread between the two
   contracts at that roll moment, cumulatively (each older segment
   carries the sum of every later roll's spread; the front segment
   carries zero) -- exactly the standard back-adjustment method. This
   was confirmed independently: diffing our unadjusted scid splice
   directly against the TradingView export (ground truth, not
   estimated) gives an exact constant offset per segment: H26 segment
   = +114.50, M26 segment = +68.00, U26 (current/front) = 0.00 (modal
   value across ~1400 bars/segment, i.e. this IS what TradingView used).

Precision limit of independently re-deriving the OFFSET from raw ticks
========================================================================
The roll TIMING rule above is exact and reproducible. The roll OFFSET
is harder to re-derive to sub-tick precision from .scid ticks alone: a
real inter-contract calendar spread is itself a traded, fluctuating
market price, not a flat constant, so which single tick/window
TradingView's backend snapshots at the roll instant (likely an
official settlement-adjacent price) cannot be pinned exactly from a
retail tick feed -- the best scid-only estimate found in this repo's
testing was ~0.0-0.25pt off for the M26->U26 roll but ~4-5pt off for
H26->M26 (the local F.US.EPH26.scid file's retained history ends only
hours after its own roll point, so there's little quiet post-roll data
to anchor a stable estimate).

Practical choice made here: since the EXACT offsets TradingView used for
2026 are already known precisely (see above, confirmed via direct diff
against the TradingView export), this script applies those exact
constants -- giving effectively EXACT match to TradingView for 2026 Open/Close (High/Low differ by up to ~1-1.25pt on
~35% of bars from ordinary cross-vendor tick noise, per user: OK to
ignore). The general "N business days before expiry, real spread at that
moment" rule is still the correct/necessary logic for the ROLL DATES
(so segments splice with zero artificial jumps) and should be reused for
future quarters once new .scid files land; for those, re-run
`offset_at_roll()` (kept below for that purpose) and, where possible,
cross-check its output against a fresh TradingView export the way this
file's constants were derived, since raw .scid ticks alone are only
accurate to a few points, not the full sub-tick fidelity TradingView
achieves via official settlement prices.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"D:\acheron\AcheronUtils")
from scidReader import get_scid_df  # noqa: E402

SCID_DIR = Path(r"D:\SC\Data")
OUT_PATH = Path(__file__).parent / "es-h1-2026-backadjusted.csv"
RANGE_START = pd.Timestamp("2026-01-01", tz="UTC")

# (symbol, expiry month/year) in chronological order -- only contracts with
# local .scid coverage; extend this list as new quarterly files land.
CONTRACTS = [("EPH26", 2026, 3), ("EPM26", 2026, 6), ("EPU26", 2026, 9)]

# Ground-truth cumulative back-adjustment offsets TradingView actually used for its
# ES1! 2026 history -- measured directly (not estimated) by diffing this repo's
# unadjusted scid splice against ../data/es-h1-2015-14aug2026.csv and taking the
# per-segment MODE of the difference (constant to the tick across ~1400 bars/segment,
# confirming these ARE the exact values TradingView applied). Used here in place of
# offset_at_roll()'s raw-tick estimate because the latter is only accurate to a few
# points (see module docstring) -- these constants give an exact match instead.
TV_GROUND_TRUTH_OFFSETS = {"EPH26": 114.50, "EPM26": 68.00, "EPU26": 0.00}


def third_friday(year: int, month: int) -> pd.Timestamp:
    d = pd.Timestamp(year=year, month=month, day=1)
    first_friday = d + pd.Timedelta(days=(4 - d.dayofweek) % 7)
    return first_friday + pd.Timedelta(weeks=2)


def roll_switch_utc(year: int, month: int) -> pd.Timestamp:
    """The exact UTC instant TradingView switches its ES1! continuous contract to this
    contract as the new front month, confirmed empirically (see module docstring): the
    START of the Globex trading session that is 3 BUSINESS DAYS before the 3rd-Friday
    expiry -- i.e. 17:00 CT on the previous calendar day. Diffing our unadjusted splice
    (+ the TV_GROUND_TRUTH_OFFSETS) against the real TradingView export shows the
    residual is EXACTLY 0.00 on either side of this instant, down to the bar."""
    trading_day = third_friday(year, month) - pd.tseries.offsets.BDay(3)
    session_open = (trading_day - pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=17)
    return session_open.tz_localize("America/Chicago").tz_convert("UTC")


def load_h1(symbol: str) -> pd.DataFrame:
    df = get_scid_df(str(SCID_DIR / f"F.US.{symbol}.scid"))[["High", "Low", "Close"]]
    h1 = df.resample("1h").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
    h1["Open"] = df["Close"].resample("1h").first()
    return h1.tz_convert("UTC")


def offset_at_roll(prev_h1: pd.DataFrame, next_h1: pd.DataFrame, switch_utc: pd.Timestamp) -> float:
    """Real spread (next.close - prev.close), taken as the MEDIAN over a window just before
    the switch instant (the last few quiet overnight hours of the outgoing contract's own
    trading day). This is the best scid-only estimate found (see module docstring for its
    accuracy limits per roll) -- kept here as the general/reusable method for FUTURE
    quarters that don't yet have a TradingView export to ground-truth against; for 2026
    itself this script uses TV_GROUND_TRUTH_OFFSETS (measured directly) instead, since
    that is exact."""
    window_start = switch_utc - pd.Timedelta(hours=6)
    p = prev_h1[(prev_h1.index >= window_start) & (prev_h1.index < switch_utc)]["Close"]
    n = next_h1[(next_h1.index >= window_start) & (next_h1.index < switch_utc)]["Close"]
    common = p.index.intersection(n.index)
    spread = (n.loc[common] - p.loc[common])
    return float(spread.median())


def main():
    print("Loading local .scid contracts...")
    frames = {sym: load_h1(sym) for sym, _, _ in CONTRACTS}
    for sym, f in frames.items():
        print(f"  {sym}: {f.index.min()} -> {f.index.max()} ({len(f)} H1 bars)")

    # own_roll[i] = the exact UTC instant CONTRACTS[i] rolls OUT and CONTRACTS[i+1]
    # takes over as TradingView's front-month/pricing source (3 business days before
    # expiry, at the start of that Globex trading session -- see roll_switch_utc()).
    own_roll = [roll_switch_utc(y, m) for _, y, m in CONTRACTS]
    print("Reverse-engineered roll-switch instants (expiry 3rd-Fri - 3 business days, session open):")
    for (sym, y, m), r in zip(CONTRACTS, own_roll):
        print(f"  -> {sym} rolls out at {r} (own expiry month {y}-{m:02d})")

    n = len(CONTRACTS)
    # spread_at_roll[j] = real (next.close - cur.close) just before CONTRACTS[j]'s own
    # roll-out instant, i.e. the jump that a naive unadjusted splice would show there.
    # Printed only as a diagnostic cross-check against TV_GROUND_TRUTH_OFFSETS below --
    # the scid-only estimate is noisier (see module docstring) so it is NOT what's applied.
    spread_at_roll = {}
    for j in range(n - 1):
        cur_sym, next_sym = CONTRACTS[j][0], CONTRACTS[j + 1][0]
        spread_at_roll[j] = offset_at_roll(frames[cur_sym], frames[next_sym], own_roll[j])
        implied_tv_spread = TV_GROUND_TRUTH_OFFSETS[cur_sym] - TV_GROUND_TRUTH_OFFSETS[next_sym]
        print(f"  roll spread at {own_roll[j]} ({cur_sym}->{next_sym}): "
              f"scid-estimate={spread_at_roll[j]:+.2f}  "
              f"TV-ground-truth={implied_tv_spread:+.2f}  "
              f"diff={spread_at_roll[j] - implied_tv_spread:+.2f}pt")

    # cumulative back-adjustment offset per contract: the exact, TradingView-confirmed
    # constants (see TV_GROUND_TRUTH_OFFSETS above), NOT the noisier scid-only estimate.
    offsets = {sym: TV_GROUND_TRUTH_OFFSETS[sym] for sym, _, _ in CONTRACTS}

    pieces = []
    for i, (sym, _, _) in enumerate(CONTRACTS):
        start = own_roll[i - 1] if i > 0 else None
        end = own_roll[i] if i < n - 1 else None
        f = frames[sym].copy()
        idx = f.index
        mask = pd.Series(True, index=idx)
        if start is not None:
            mask &= idx >= start
        if end is not None:
            mask &= idx < end
        seg = f.loc[mask].copy()
        seg[["Open", "High", "Low", "Close"]] += offsets[sym]
        seg["contract"] = sym
        seg["offset_applied"] = offsets[sym]
        pieces.append(seg)

    combined = pd.concat(pieces).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined[combined.index >= RANGE_START]

    combined["time"] = (combined.index.view("int64") // 10 ** 9).astype("int64")
    out = combined[["time", "Open", "High", "Low", "Close", "contract", "offset_applied"]].rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
    )
    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {len(out)} back-adjusted, jump-free H1 bars -> {OUT_PATH}")
    print(f"Range: {combined.index.min()} -> {combined.index.max()}")


if __name__ == "__main__":
    main()
