"""
Build a single, verifiable, non-back-adjusted continuous ES H1 OHLC series
for label-review ONLY (does not touch ../../data/es-h1-*.csv used by other
tools in this monorepo).

Why: ../../data/es-h1-2015-14aug2026.csv is a TradingView "ES1!" *back-adjusted*
continuous contract. Back-adjustment recalculates ALL historical bars every
time the series is re-exported, anchored to whatever the front contract is
"today" -- so old absolute price levels are not real traded prices and drift
by hundreds of points between vintages (verified: avg +280pt, up to +412pt,
comparing the bundled file against an older vintage exported ~16 months
earlier for identical timestamps). That makes any absolute-price-level logic
(S/R zones, LXPB levels) non-reproducible and historically inaccurate.

This script instead SPLICES REAL, UNADJUSTED per-contract prices:
  1. Recent window: tick data from local Sierra Chart .scid files
     (D:\\SC\\Data\\F.US.EP{H26,M26,U26}.scid), read via
     D:\\acheron\\AcheronUtils\\scidReader.py. Verified byte-exact against
     the TradingView export for the current front contract's own window.
  2. Older window (back to 2024-08-19, as far back as free hourly data is
     available with no back-adjustment): Yahoo Finance's public, unauthenticated
     chart API for ES=F (continuous front-month, never back-adjusted).
     Verified against known real historical prints (spot-checked separately).

Contracts are joined using CME's standard quarterly roll calendar (switch to
the next contract ~8 calendar days before the expiring contract's own
3rd-Friday expiry), restricting each contract's ticks to its real front-month
window -- earlier local ticks for a not-yet-front contract are excluded so
stale deferred-contract pricing can't leak into the splice. Every output row
is tagged with its `source`
and (when known) `contract` so provenance stays auditable, and re-running
this script should reproduce identical historical bars every time (unlike
the TradingView back-adjusted export).

Known limitation: real, freely-available, unadjusted HOURLY data only goes
back to 2024-08-19 (Yahoo's intraday history cap). Older bars are not
included here -- do not blend them with the old back-adjusted file. Use
../../data/es-d1-9sep1997-11apr2025.csv (verify separately) for long-range
daily context if needed; extending real unadjusted H1 further back would
require a paid tick-data vendor.
"""
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\acheron\AcheronUtils")
from scidReader import get_scid_df  # noqa: E402

SCID_DIR = Path(r"D:\SC\Data")
CONTRACTS = ["EPH26", "EPM26", "EPU26"]  # local .scid files available, in chrono order
OUT_PATH = Path(__file__).parent / "es-h1-continuous.csv"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/ES=F?range=2y&interval=1h"


def load_scid_h1(symbol: str) -> pd.DataFrame:
    """Load one contract's ticks and resample to H1 OHLC + daily-volume-taggable index."""
    df = get_scid_df(str(SCID_DIR / f"F.US.{symbol}.scid"))
    df = df[["High", "Low", "Close", "Volume"]].copy()
    # scidReader's per-tick "Open" field is not a real per-tick open (Sierra Chart tick
    # .scid records leave it at 0.0 / a sentinel) -- the true H1 open is the first trade's
    # Close price seen in that hour bucket, so derive Open from Close, not the raw field.
    h1 = df.resample("1h").agg(
        {"High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["High", "Low", "Close"])
    h1["Open"] = df["Close"].resample("1h").first()
    h1["contract"] = symbol
    return h1


def third_friday(year: int, month: int) -> pd.Timestamp:
    d = pd.Timestamp(year=year, month=month, day=1)
    first_friday = d + pd.Timedelta(days=(4 - d.dayofweek) % 7)
    return first_friday + pd.Timedelta(weeks=2)


# CME quarterly ES roll convention: front month switches ~8 calendar days before the
# EXPIRING contract's own 3rd-Friday expiry (standard front-month roll rule). Using this
# fixed calendar window -- not raw tick volume -- matters here: EPH26 has ticks going back
# to 2025-07-10, long before it was actually the front month (~2025-12-11); a naive
# "most ticks/volume wins" splice let that stale deferred-contract pricing leak in and
# produced a false ~110pt jump at the 2025-07-10 boundary. Restricting each contract to its
# real front-month window avoids that.
FRONT_MONTH_WINDOWS = {
    "EPH26": (third_friday(2025, 12) - pd.Timedelta(days=8), third_friday(2026, 3) - pd.Timedelta(days=8)),
    "EPM26": (third_friday(2026, 3) - pd.Timedelta(days=8), third_friday(2026, 6) - pd.Timedelta(days=8)),
    "EPU26": (third_friday(2026, 6) - pd.Timedelta(days=8), third_friday(2026, 9) - pd.Timedelta(days=8)),
}


def calendar_roll_splice(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Keep only each contract's real front-month window (see FRONT_MONTH_WINDOWS), then
    stack them in chronological order -- this is the standard, reproducible way to build an
    unadjusted continuous "current contract" series."""
    pieces = []
    for sym, f in frames.items():
        start, end = FRONT_MONTH_WINDOWS[sym]
        start = start.tz_localize("UTC")
        end = end.tz_localize("UTC")
        idx = f.index.tz_convert("UTC")
        window = f.loc[(idx >= start) & (idx < end)]
        print(f"  {sym} front-month window kept: {start.date()} -> {end.date()} "
              f"({len(window)} H1 bars, out of {len(f)} total ticks-derived bars)")
        pieces.append(window)
    out = pd.concat(pieces).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def fetch_yahoo_h1() -> pd.DataFrame:
    req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req))["chart"]["result"][0]
    ts = data["timestamp"]
    q = data["indicators"]["quote"][0]
    df = pd.DataFrame(
        {"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]},
        index=pd.to_datetime(ts, unit="s", utc=True),
    )
    df.index.name = "time"
    df = df.dropna(how="all")
    df["contract"] = "ES=F (yahoo continuous)"
    return df


def main():
    print("Loading local .scid contracts (this reads multi-GB tick files, be patient)...")
    frames = {sym: load_scid_h1(sym) for sym in CONTRACTS}
    for sym, f in frames.items():
        print(f"  {sym}: {f.index.min()} -> {f.index.max()} ({len(f)} H1 bars)")

    scid_h1 = calendar_roll_splice(frames)
    scid_h1.index = scid_h1.index.tz_convert("UTC")
    scid_h1["source"] = "scid"

    print("Fetching Yahoo Finance ES=F continuous H1 (free, unauthenticated, unadjusted)...")
    yahoo_h1 = fetch_yahoo_h1()
    yahoo_h1["source"] = "yahoo"

    # Prefer scid (tick-exact) wherever both exist; use yahoo only to extend further back.
    cutover = scid_h1.index.min()
    combined = pd.concat([yahoo_h1[yahoo_h1.index < cutover], scid_h1]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]

    combined["time"] = (combined.index.view("int64") // 10 ** 9).astype("int64")
    out = combined[["time", "Open", "High", "Low", "Close", "contract", "source"]].rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
    )
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(out)} H1 bars -> {OUT_PATH}")
    print(f"Range: {combined.index.min()} -> {combined.index.max()}")


if __name__ == "__main__":
    main()
