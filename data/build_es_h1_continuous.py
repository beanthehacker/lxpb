"""
RETIRED 2026-09-10 -- DO NOT RUN, AND DO NOT USE ITS OUTPUT.

This script builds `es-h1-continuous-backadjusted.csv` by taking a frozen
TradingView export as a historical base and *extending* it with resampled
front-month `.scid` bars. That joins two vendors' feeds at an arbitrary date,
which is exactly what the "TradingView continuous series only" convention in
`lxpb-v2/CLAUDE.md` now forbids: H1 and M5 bars come from TradingView's own
continuous exports and nothing else, and where those exports stop, the series
stops.

Nothing reads its output any more. Use `es_h1_display.load()` at the repo
root, or `render_labels_report._display_h1()` inside lxpb-v2. The file and
this script are kept only so the older reports in git history remain
reproducible. Everything below describes the retired design.

Build the SINGLE canonical, back-adjusted, jump-free continuous ES H1
dataset for the whole monorepo (lxpb.py, lxpb-es-vol/*, label-review) --
replacing the various stale/duplicate H1 files previously used
independently by different tools.

Why a single canonical file
============================
This repo previously had several different ES H1 CSVs in active use:
  - data/es-h1-4apr2021-11apr2025.csv (lxpb-es-vol, lxpb.py's example) --
    a frozen TradingView back-adjusted export, anchored ~Apr 2025, and
    STALE (no 2025-2026 data at all).
  - data/es-h1-2015-14aug2026.csv -- a newer, longer frozen TradingView
    back-adjusted export, anchored ~Aug 14 2026, previously used only by
    label-review.
  - label-review/data/es-h1-continuous.csv -- a locally-built UNADJUSTED
    (real absolute price, jump-at-roll) splice, built for label-review's
    S/R-level detection use case specifically.
All of these are superseded by this single file, used consistently
everywhere back-adjusted continuous data is needed.

Method
======
TradingView's own back-adjustment for history already baked into
data/es-h1-2015-14aug2026.csv is correct and internally self-consistent
(every historical roll in that one frozen file was adjusted relative to
the SAME anchor date) -- the original "data is broken" bug was about
RE-EXPORTING the chart later and swapping files (which recalculates
*all* history relative to a new anchor and silently changes old absolute
price levels), not about this file's own internal consistency. So there
is no need to rebuild 2015-2026 back-adjustment from scratch: this
script simply takes that frozen file AS-IS for all history through its
own last bar, then EXTENDS it with fresh, real (unadjusted) front-month
`.scid` data for whatever period comes after -- which is valid with zero
extra back-adjustment math because the front/current contract always
carries a +0 offset in a back-adjusted series by definition.

Reverse-engineered TradingView ES1! roll rule (for future extension)
======================================================================
Confirmed empirically (bar-for-bar, 0.00 residual) against both 2026
rolls by diffing real overlapping .scid contract data against a
TradingView export:
  1. TIMING: TradingView switches ES1! to the next front-month contract
     at the exact start of the Globex session 3 BUSINESS DAYS before the
     expiring contract's 3rd-Friday expiration -- i.e. 17:00 CT on the
     previous calendar day (the regular session open/reopen-after-
     maintenance time). This rule matched exactly (not approximately) on
     both the 2026 H26->M26 and M26->U26 rolls, so it is general/
     reproducible, not a one-off coincidence.
  2. OFFSET: the back-adjustment applied to older segments is the real
     market calendar-spread between the two contracts at that roll
     instant, applied cumulatively. This is harder to re-derive to
     sub-tick precision from .scid ticks alone (a real calendar spread
     fluctuates by a few points even right at the roll, so which single
     tick TradingView's backend snapshots -- likely an official
     settlement-adjacent price -- can't be pinned exactly from a retail
     tick feed). Best practice: cross-check any newly-computed offset
     against a fresh TradingView export the way the current
     FRONT_CONTRACTS offsets were derived, rather than trusting a raw
     scid-only estimate.

Going forward (once the current front contract itself rolls, e.g.
U26->Z26 in Sep 2026), re-run this script after dropping the new
quarter's `.scid` file into `D:\\SC\\Data\\` and adding it to
FRONT_CONTRACTS below with its cumulative offset (0 while it's still the
live front month; a real spread value once it in turn rolls further,
computed per the rule above).
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"D:\acheron\AcheronUtils")
from scidReader import get_scid_df  # noqa: E402

HERE = Path(__file__).parent
BASE_TV_EXPORT = HERE / "es-h1-2015-14aug2026.csv"
OUT_PATH = HERE / "es-h1-continuous-backadjusted.csv"
SCID_DIR = Path(r"D:\SC\Data")

# Current front-month contract(s) used to extend past BASE_TV_EXPORT's own
# last bar. Each carries offset 0.0 as long as it is the CURRENT front
# contract (no back-adjustment needed for the un-rolled, live segment).
# Add the next quarter's symbol here once U26 itself rolls (see docstring).
FRONT_CONTRACTS = [("EPU26", 0.0)]


def load_h1(symbol: str) -> pd.DataFrame:
    df = get_scid_df(str(SCID_DIR / f"F.US.{symbol}.scid"))[["High", "Low", "Close"]]
    h1 = df.resample("1h").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
    h1["Open"] = df["Close"].resample("1h").first()
    return h1.tz_convert("UTC")


def main():
    base = pd.read_csv(BASE_TV_EXPORT)
    base["time"] = pd.to_datetime(base["time"], unit="s", utc=True)
    base = base.set_index("time").sort_index()
    last_base_time = base.index.max()
    print(f"Base (frozen TV back-adjusted export): {base.index.min()} -> {last_base_time} "
          f"({len(base)} bars)")

    pieces = [base[["open", "high", "low", "close"]]]
    for symbol, offset in FRONT_CONTRACTS:
        f = load_h1(symbol)
        f = f[f.index > last_base_time].copy()
        f[["Open", "High", "Low", "Close"]] += offset
        f = f.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"})
        print(f"Extension from {symbol} (offset {offset:+.2f}): {f.index.min()} -> "
              f"{f.index.max()} ({len(f)} bars)")
        pieces.append(f[["open", "high", "low", "close"]])

    combined = pd.concat(pieces).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.index.name = "time"

    out = combined.reset_index()
    out["time"] = (out["time"].astype("int64") // 10 ** 9).astype("int64")
    out = out[["time", "open", "high", "low", "close"]]
    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {len(out)} back-adjusted, jump-free H1 bars -> {OUT_PATH}")
    print(f"Range: {combined.index.min()} -> {combined.index.max()}")


if __name__ == "__main__":
    main()
