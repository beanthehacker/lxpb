"""Run an lxpb-v2 report with NO TradingView exports: the H1/M5 series are built
from the .scid ticks of the front-month contracts, at the offsets already
measured in data/scid_offsets.json.

For machines that have the Sierra Chart .scid files but not the TradingView
exports (e.g. a cloud sandbox). The normal data convention (CLAUDE.md:
"TradingView exports confirm, Sierra Chart ticks extend") is NOT followed, so:

  * H1/M5 level history starts at the first listed contract's front-month start
    (EPU26: 2026-06-14) -- older levels do not exist here, and the ledgers are
    rebuilt from scratch on that shorter series.
  * The offsets are re-used from data/scid_offsets.json, not re-measured: nothing
    here can confirm them against an export.
  * The level ledgers are cached in a scratch dir, never in data/levels_cache/.
  * Nothing is edited in the repo; the report script runs unmodified.

usage (from anywhere):
    python scid_only/run_scid_only.py ss_m1_confl/render_m1_confl_report.py \\
        --since-day 2026-09-28 --bar-minutes 2 --range-box-only --output out.html

The first argument is a script path relative to lxpb-v2/; the rest are passed
through. Always run it SERIALLY (no --workers > 1): worker children are spawned
fresh and would not inherit these patches (and would write the repo's caches).

Environment (all optional):
    LXPB_SCID_DIR       folder with F.US.<SYM>.scid          (default /home/user/scid)
    LXPB_SCID_READER    folder holding scidReader.py         (default /home/user/acheron/AcheronUtils)
    LXPB_SCID_CACHE     scratch ledger cache dir             (default <tmp>/lxpb_scid_only_cache)
    LXPB_SCID_CONTRACTS comma list, oldest first, whose offsets are in
                        scid_offsets.json                    (default EPU26,EPZ26)
"""
import json
import os
import runpy
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # lxpb-v2/
SCID_DIR = os.environ.get("LXPB_SCID_DIR", "/home/user/scid")
READER_DIR = os.environ.get("LXPB_SCID_READER", "/home/user/acheron/AcheronUtils")
CACHE = os.environ.get("LXPB_SCID_CACHE", os.path.join(tempfile.gettempdir(), "lxpb_scid_only_cache"))
CONTRACTS = os.environ.get("LXPB_SCID_CONTRACTS", "EPU26,EPZ26").split(",")

if len(sys.argv) < 2:
    sys.exit(__doc__)
for i, a in enumerate(sys.argv):
    if a == "--workers" and i + 1 < len(sys.argv) and int(sys.argv[i + 1]) > 1:
        sys.exit("run_scid_only.py: --workers > 1 is not supported (spawned children skip the patches)")
    if a.startswith("--workers=") and int(a.split("=", 1)[1]) > 1:
        sys.exit("run_scid_only.py: --workers > 1 is not supported (spawned children skip the patches)")

sys.path[:0] = [READER_DIR, REPO, os.path.join(REPO, "ss_m1_confl"), os.path.join(REPO, "ss_m5_confl2")]
os.chdir(REPO)

import pandas as pd  # noqa: E402
import render_labels_report as R  # noqa: E402
import lxpb_levels_cache as LC  # noqa: E402

R.SCID_DIR = SCID_DIR
LC.CACHE_DIR = CACHE                      # never touch the committed ledgers
os.makedirs(CACHE, exist_ok=True)

stored = json.load(open(os.path.join(REPO, "data", "scid_offsets.json")))["offsets"]
missing = [c for c in CONTRACTS if c not in stored]
if missing:
    sys.exit(f"run_scid_only.py: no stored offset for {missing} in data/scid_offsets.json")
for sym in CONTRACTS:
    R._SCID_OFFSETS[sym] = stored[sym]["offset"]

_first = [s for s, _, _ in R.CONTRACTS].index(CONTRACTS[0])
_lo = R._front_month_start(_first)
_series = {}


def _build(freq):
    if freq not in _series:
        last_tick = R._scid_bounds(CONTRACTS[-1])[1]
        hi = pd.Timestamp(last_tick).floor(freq)       # the bar still forming is left out
        bars = R._tick_bars_spanning(_lo, hi, freq)
        print(f"[scid-only] {freq}: {len(bars):,} bars {bars.index[0]} -> {bars.index[-1]}", flush=True)
        _series[freq] = bars
    return _series[freq]


R._display_h1 = R._tv_h1 = lambda: _build("1h")
R._display_m5 = R._tv_m5 = lambda: _build("5min")

script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
runpy.run_path(os.path.join(REPO, script), run_name="__main__")
