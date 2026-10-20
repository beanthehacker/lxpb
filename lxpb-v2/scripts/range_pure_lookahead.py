"""No-look-ahead check for patterns_pure/find_trading_range.

Replays the bars cut off at many points and checks that each cut run reports
exactly what the full run had reported by then: the same events (time, kind,
box, edges at that moment) and, for every confirmed box, the same start,
confirm time and confirm edges.

    python range_pure_lookahead.py <bars.csv> [n_cuts] [--lower <m1.csv>]

With --lower, breakouts are graded strong / weak on those bars, and each cut
run gets only the lower bars up to the end of its last bar.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "patterns_pure"))
from find_trading_range import find_trading_range  # noqa: E402

FIXED = ["id", "start_time", "confirm_time", "confirm_high", "confirm_low"]


def load(path):
    b = pd.read_csv(path, index_col=0, parse_dates=True)
    b.index = pd.DatetimeIndex(b.index).tz_convert("America/Los_Angeles")
    return b


args = sys.argv[1:]
lower = load(args[args.index("--lower") + 1]) if "--lower" in args else None
args = [a for i, a in enumerate(args) if a != "--lower" and (i == 0 or args[i - 1] != "--lower")]
bars = load(args[0])
step = bars.index.to_series().diff().median()
if lower is not None:
    lower = lower[lower.index < bars.index[-1] + step]
n_cuts = int(args[1]) if len(args) > 1 else 40
full = find_trading_range(bars, return_all=True, lower_data=lower)
bad = 0
for k in np.linspace(50, len(bars) - 1, n_cuts).astype(int):
    low_k = None if lower is None else lower[lower.index < bars.index[k - 1] + step]
    cut = find_trading_range(bars.iloc[:k], return_all=True, lower_data=low_k)
    ev_full = full["events"][full["events"].time <= bars.index[k - 1]].reset_index(drop=True)
    ok_ev = cut["events"].to_dict("records") == ev_full.to_dict("records")
    ok_rg = (cut["ranges"][FIXED].to_dict("records")
             == full["ranges"][FIXED].iloc[:len(cut["ranges"])].to_dict("records"))
    if not (ok_ev and ok_rg):
        bad += 1
        print(f"MISMATCH at {bars.index[k - 1]}: events {ok_ev}, ranges {ok_rg}")
print(f"{n_cuts} cuts, {len(full['events'])} events, {len(full['ranges'])} ranges: "
      f"{'no look-ahead' if bad == 0 else f'{bad} cuts differ'}")
