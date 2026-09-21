import pandas as pd

from find_swings import find_swings

# How long a drive has to reclaim the previous drive's extreme on a close. A
# couple of closes beyond it are fine -- what disqualifies a drive is price
# STAYING beyond it, so the reclaim just has to come quickly. Stretch this and
# the pattern stops meaning rejection and starts catching ordinary pullbacks
# after an impulse: on 2026 H1, 6 bars finds 3x the patterns 2 bars does.
RECOVER_BARS = 2


def find_three_drive_failure(data: pd.DataFrame,
                             atr_mult: float = 0.75,
                             atr_period: int = 21,
                             recover_bars: int = RECOVER_BARS,
                             max_bars: int | None = None) -> pd.DataFrame:
  """
  Three-Drive Failure (3DF) -- three attempts at a new extreme, each one bought
  (or sold) straight back, which leaves a bias in the OPPOSITE direction of the
  drives.

  3DF-bullish is three drives DOWN to a low:

    - three CONSECUTIVE swing lows (find_swings, ATR ZigZag, so the recovery
      between them is a real bounce of at least atr_mult * ATR, not noise),
    - each drive's low strictly below the one before it -- price genuinely made
      a new low each time, AND
    - drive 2 and drive 3 each RECLAIM the previous drive's low: some bar within
      `recover_bars` of the drive closes back above it. The drive candle itself
      counts, so an immediate rejection reclaims at once, but a couple of closes
      below the old low followed by a sharp move back up qualifies just as well.

  3DF-bearish is the exact mirror: three consecutive higher swing highs, each of
  drives 2 and 3 closing back below the previous drive's high within
  `recover_bars`.

  The drive candles themselves are NOT shape-tested -- a drive is judged by what
  price does with the new extreme, not by how the one candle that made it looks.

  The pattern is COMPLETE at drive 3's reclaim bar, which is the bar it is
  indexed at (`confirm_time`); that is at or after `drive3_time`. Note the
  ZigZag only confirms drive 3 as a pivot once price has bounced atr_mult * ATR
  off it, which can be later still -- a caller walking trailing windows sees
  each pattern on the first window that reveals it, which is the honest
  point-in-time moment.

  `max_bars` optionally caps how many bars drive 1 and drive 3 may be apart.

  Returns one row per pattern: the confirm bar's OHLC plus

    pattern         "3DF-bullish" / "3DF-bearish"
    drive1_time     drive2_time   drive3_time     the three drive candles
    drive1_price    drive2_price  drive3_price    their lows (bullish) / highs (bearish)
    bounce1_time    bounce2_time                  the swing pivot between the drives
    bounce1_price   bounce2_price                 its high (bullish) / low (bearish)
    recover2_time   recover3_time                 where drives 2 and 3 reclaimed
    confirm_time                                  == recover3_time, the index
  """
  cols = ["pattern",
          "drive1_time", "drive2_time", "drive3_time",
          "drive1_price", "drive2_price", "drive3_price",
          "bounce1_time", "bounce2_time", "bounce1_price", "bounce2_price",
          "recover2_time", "recover3_time", "confirm_time"]
  if data.empty:
    return pd.DataFrame(columns=data.columns.tolist() + cols)

  swing_highs, swing_lows = find_swings(data, atr_mult=atr_mult, atr_period=atr_period)
  hits = []
  hits += _scan(data, swing_lows, swing_highs, recover_bars, max_bars, bullish=True)
  hits += _scan(data, swing_highs, swing_lows, recover_bars, max_bars, bullish=False)
  if not hits:
    return pd.DataFrame(columns=data.columns.tolist() + cols)

  found = pd.DataFrame(hits).sort_values("confirm_time")
  rows = data.loc[found["confirm_time"]]
  return rows.assign(**{c: found[c].to_numpy() for c in cols})


def _scan(data, drives, bounces, recover_bars, max_bars, bullish):
  """Walk consecutive drive-side pivots, keeping the triples that fail."""
  extreme = "low" if bullish else "high"
  pos = {t: i for i, t in enumerate(data.index)}
  out = []
  for i in range(len(drives) - 2):
    d1, d2, d3 = drives.index[i], drives.index[i + 1], drives.index[i + 2]
    if max_bars is not None and pos[d3] - pos[d1] > max_bars:
      continue
    p1, p2, p3 = (drives[extreme].iat[i + k] for k in range(3))
    # Each drive pushes the extreme further than the one before it.
    if bullish and not (p2 < p1 and p3 < p2):
      continue
    if not bullish and not (p2 > p1 and p3 > p2):
      continue
    # Drives 2 and 3 each have to take the previous drive's extreme back.
    r2 = _reclaim(data, pos[d2], p1, recover_bars, bullish)
    r3 = _reclaim(data, pos[d3], p2, recover_bars, bullish)
    if r2 is None or r3 is None:
      continue
    # The swing pivot between each pair of drives is the bounce.
    b1 = bounces.loc[(bounces.index > d1) & (bounces.index < d2)]
    b2 = bounces.loc[(bounces.index > d2) & (bounces.index < d3)]
    if len(b1) != 1 or len(b2) != 1:
      continue
    other = "high" if bullish else "low"
    out.append({"pattern": "3DF-bullish" if bullish else "3DF-bearish",
                "drive1_time": d1, "drive2_time": d2, "drive3_time": d3,
                "drive1_price": p1, "drive2_price": p2, "drive3_price": p3,
                "bounce1_time": b1.index[0], "bounce2_time": b2.index[0],
                "bounce1_price": b1[other].iat[0], "bounce2_price": b2[other].iat[0],
                "recover2_time": r2, "recover3_time": r3, "confirm_time": r3})
  return out


def _reclaim(data, drive_pos, prior_extreme, recover_bars, bullish):
  """First bar from the drive onward, within `recover_bars`, that closes back
  past `prior_extreme`. None if price never takes it back in time."""
  window = data["close"].iloc[drive_pos:drive_pos + recover_bars + 1]
  back = window[window > prior_extreme] if bullish else window[window < prior_extreme]
  return back.index[0] if len(back) else None
