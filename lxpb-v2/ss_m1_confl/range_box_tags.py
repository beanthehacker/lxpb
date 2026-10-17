"""Tag names of the M1 fade report's range-box trades.

The detection that used to live here (re-running find_range_box_zz as of each trade's touch and tagging the trade
afterwards, then handing out one seat per box) was DELETED on 2026-10-16: the live engine (live_range/) decides the M1 fade
trades, range first, and `ss_m1_confl/engine_rows.py` hands the report its boxes. Only the names the page uses remain.

FADE 1 / FADE 2. A breakout-from-range trade fades the strong breakout of one edge of its box. It is fade1 when that is the
box's first strong breakout and fade2 when the other edge had already broken out strongly before it (price left one side,
reversed, and broke out of the second). A trade on several boxes is fade1 when all of them are first breakouts, fade2 when all
are second-side breakouts, and fade-mixed when it is both. The engine trades fade 1 only unless `Params.fade2` / `--fade2`.
"""
TAG_FROM_RANGE = "breakout-from-range"
TAG_FADE1 = "fade1"             # the fade of a box's FIRST strong breakout
TAG_FADE2 = "fade2"             # the fade of the box's second-side breakout (the other edge broke out first)
TAG_FADE_MIXED = "fade-mixed"   # a trade on several boxes, some first-breakout and some second-side
SLOT_FROM = "from"
