import numpy as np
import pandas as pd

from find_hammer import find_hammer
from find_shooting_star import find_shooting_star

# The only (range, body) combinations that qualify, and nothing between or
# beyond them: (minimum range as a multiple of the spike candle's range,
# minimum body as a share of the thrust candle's own range).
SPIKE_THRUST_TIERS = (
  (0.75, 0.80),
  (1.00, 0.60),
  (1.20, 0.50),
  (1.50, 0.40),
)


def find_spike_thrust(data: pd.DataFrame) -> pd.DataFrame:
  """
  The Spike-Thrust candle is the candle IMMEDIATELY after a spike (a Hammer or a
  Shooting Star, exactly as find_hammer / find_shooting_star judge them) that
  carries the spike through:

    - it closes in the spike's direction: up (close > open) after a Hammer,
      down (close < open) after a Shooting Star, AND
    - it meets at least one SPIKE_THRUST_TIERS row -- range vs the spike's range
      AND body vs its own range:

        range >= 0.75x spike  and  body >= 80%
        range >= 1.00x spike  and  body >= 60%
        range >= 1.20x spike  and  body >= 50%
        range >= 1.50x spike  and  body >= 40%

      A candle between two rows needs the body of the row below it (1.4x needs
      50%); nothing under 0.75x or under a 40% body ever qualifies.

  `data` must be one contiguous bar series: "immediately after" is the next row,
  and the spike itself needs the row before it (find_hammer's previous-bar close).

  Returns the Spike-Thrust rows plus `spike_time` (the spike candle's index) and
  `spike_pattern` ("hammer" / "shooting_star").
  """
  # If data is empty, return an empty dataframe
  if data.empty:
    return pd.DataFrame(columns=data.columns.tolist() + ["spike_time", "spike_pattern"])
  # Variables
  total_length = (data["high"] - data["low"]).to_numpy()
  body_size    = (data["close"] - data["open"]).abs().to_numpy()
  spike_length = np.concatenate([[np.nan], total_length[:-1]])
  is_hammer    = data.index.isin(find_hammer(data, atr=0.0).index)
  is_star      = data.index.isin(find_shooting_star(data, atr=0.0).index)
  after_hammer = np.concatenate([[False], is_hammer[:-1]])
  after_star   = np.concatenate([[False], is_star[:-1]])
  closes_up    = (data["close"] > data["open"]).to_numpy()
  closes_down  = (data["close"] < data["open"]).to_numpy()
  strong_enough = np.zeros(len(data), dtype=bool)
  for range_mult, body_share in SPIKE_THRUST_TIERS:
    strong_enough |= (total_length >= spike_length * range_mult) & (body_size >= total_length * body_share)
  # Find all Spike-Thrusts
  up   = after_hammer & strong_enough & closes_up
  down = after_star & strong_enough & closes_down
  hits = np.flatnonzero(up | down)
  rows = data.iloc[hits]
  return rows.assign(spike_time=data.index[hits - 1],
                     spike_pattern=np.where(up[hits], "hammer", "shooting_star"))
