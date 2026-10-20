"""Regression cases for find_trading_range (user, 2026-10-19).

RUN THIS AFTER ANY CHANGE to the range-box logic: find_trading_range.py,
find_zigzag.py (its pivots anchor the edges), find_ATR.py, find_range.py
(_BreakoutGrader, the strong-breakout grade), or live_range/trbook.py (the
engine adapter replays the same tracker). A failing case means the change
broke a range the user signed off; do not edit the expected values to make
it pass unless the user agrees to the new box.

    python patterns_pure/tests/test_find_trading_range_cases.py

The bars are frozen in fixtures/ (H1 from E:/daily-analysis/data, which match
the user's TradingView exports), so the cases never move with the data. The
files keep the source's UTC stamps; the test reads them in PT and every time
below is PT (user rule, 2026-10-20: times are PT, never UTC).

1. USDJPY H1, Apr 2026 (exact). One range: 158.958 (trough Apr 27 21:00) -
   159.847 (crest Apr 23 10:00), from the crest at Apr 21 10:00, confirmed
   Apr 22 00:00, last bar inside Apr 29 03:00, then a failed breakout up on the
   Apr 29 04:00 bar (its wick could not be taken in; it closed back inside).
   User OK 2026-10-20 after the bar-close freeze rule (was 159.093 from the
   Apr 27 02:00 trough, last bar inside 04:00, broken up 05:00 / 06:00). Fed
   bar by bar only up to Apr 27 19:00, the tracker's live range is the box as
   it stood then: 159.093 - 159.847.
2. AUDUSD H1, Jan 2026 (exact, user 2026-10-20): the range Jan 13 08:00 -
   Jan 19 06:00 (last bar inside), 0.66671 (trough Jan 14 19:00) - 0.67108,
   confirmed Jan 14 06:00, broken up Jan 19 07:00. (Was: ONE range somewhere
   in Jan 8 - 19.)
3. Both fixtures: no look-ahead (runs cut at many bars report exactly what the
   full run had reported by then).
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from find_trading_range import TradingRangeTracker, find_trading_range  # noqa: E402

USDJPY = os.path.join(HERE, "fixtures", "USDJPY_H1_2026-03_2026-05.csv")
AUDUSD = os.path.join(HERE, "fixtures", "AUDUSD_H1_2025-11_2026-02.csv")


PT = "America/Los_Angeles"


def load(path):
    b = pd.read_csv(path, index_col=0, parse_dates=True)
    b.index = b.index.tz_localize("UTC").tz_convert(PT)
    return b


def ts(s):
    return pd.Timestamp(s, tz=PT)


class USDJPYApr2026(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = load(USDJPY)
        cls.ranges = find_trading_range(cls.bars)

    def test_the_apr_21_29_range(self):
        r = self.ranges[(self.ranges.end_time >= ts("2026-04-22")) & (self.ranges.start_time < ts("2026-04-29"))]
        self.assertEqual(len(r), 1, f"expected one range over Apr 22-28, got\n{r}")
        r = r.iloc[0]
        self.assertEqual(r.start_time, ts("2026-04-21 10:00"))
        self.assertEqual(r.confirm_time, ts("2026-04-22 00:00"))
        self.assertEqual(r.end_time, ts("2026-04-29 03:00"))
        self.assertAlmostEqual(r.low, 158.958, places=6)
        self.assertAlmostEqual(r.high, 159.847, places=6)
        self.assertEqual(r.pivot_low_time, ts("2026-04-27 21:00"))
        self.assertEqual(r.pivot_high_time, ts("2026-04-23 10:00"))
        self.assertEqual(r.status, "broken")
        self.assertEqual(r.break_dir, "up")
        self.assertEqual(r.break_type, "failed")
        self.assertEqual(r.break_time, ts("2026-04-29 04:00"))
        self.assertEqual(r.break_confirm_time, ts("2026-04-29 04:00"))

    def test_live_as_of_apr_27_1900(self):
        tr = TradingRangeTracker()
        b = self.bars[self.bars.index + pd.Timedelta(hours=1) <= ts("2026-04-27 19:00")]
        for t, o, h, l, c in zip(b.index, b.open, b.high, b.low, b.close):
            tr.update(t, o, h, l, c)
        a = tr.active
        self.assertIsNotNone(a, "no live range as of Apr 27 19:00 PT")
        self.assertEqual(tr.T[a["start"]], ts("2026-04-21 10:00"))
        self.assertEqual(tr.T[a["confirm"]], ts("2026-04-22 00:00"))
        self.assertAlmostEqual(a["low"], 159.093, places=6)
        self.assertAlmostEqual(a["high"], 159.847, places=6)


class AUDUSDJan2026(unittest.TestCase):
    def test_the_jan_13_19_range(self):
        r = find_trading_range(load(AUDUSD))
        r = r[(r.start_time >= ts("2026-01-13")) & (r.start_time < ts("2026-01-19"))]
        self.assertEqual(len(r), 1, "expected one range from Jan 13, got\n"
                         + r[["start_time", "confirm_time", "end_time", "low", "high"]].to_string())
        r = r.iloc[0]
        self.assertEqual(r.start_time, ts("2026-01-13 08:00"))
        self.assertEqual(r.confirm_time, ts("2026-01-14 06:00"))
        self.assertEqual(r.end_time, ts("2026-01-19 06:00"))
        self.assertAlmostEqual(r.low, 0.66671, places=6)
        self.assertAlmostEqual(r.high, 0.67108, places=6)
        self.assertEqual(r.pivot_low_time, ts("2026-01-14 19:00"))
        self.assertEqual(r.status, "broken")
        self.assertEqual(r.break_dir, "up")
        self.assertEqual(r.break_type, "breakout")
        self.assertEqual(r.break_time, ts("2026-01-19 07:00"))


FIXED = ["id", "start_time", "confirm_time", "confirm_high", "confirm_low"]


class NoLookAhead(unittest.TestCase):
    def check(self, path, n_cuts=25):
        bars = load(path)
        full = find_trading_range(bars, return_all=True)
        for k in np.linspace(50, len(bars) - 1, n_cuts).astype(int):
            cut = find_trading_range(bars.iloc[:k], return_all=True)
            ev = full["events"][full["events"].time <= bars.index[k - 1]].reset_index(drop=True)
            self.assertEqual(cut["events"].to_dict("records"), ev.to_dict("records"),
                             f"events differ when cut at {bars.index[k - 1]}")
            self.assertEqual(cut["ranges"][FIXED].to_dict("records"),
                             full["ranges"][FIXED].iloc[:len(cut["ranges"])].to_dict("records"),
                             f"ranges differ when cut at {bars.index[k - 1]}")

    def test_usdjpy(self):
        self.check(USDJPY)

    def test_audusd(self):
        self.check(AUDUSD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
