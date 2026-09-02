import pandas as pd

from find_ATR import findATR


def find_swings(data: pd.DataFrame, atr_mult: float = 0.75, atr_period: int = 21) -> tuple[pd.DataFrame, pd.DataFrame]:
  """
  ZigZag swing-point detection.

  A swing high is confirmed when the running high reverses down by
  `atr_mult * ATR(atr_period)`. Symmetric for swing lows. Pivots
  alternate (high -> low -> high). Only confirmed pivots are emitted —
  the final in-progress leg is dropped so callers see point-in-time
  consistent results.

  Local-extreme filter: a candidate swing must also be at least as
  extreme as its immediate neighbours — swing high requires
  high[j] >= high[j-1] and high[j] >= high[j+1] (and symmetric for
  swing low). The last bar has no right neighbour, so only the left
  neighbour is checked.

  Returns (swing_highs, swing_lows) — two DataFrames sliced from `data`
  at the pivot timestamps.
  """
  empty = data.iloc[0:0]
  if len(data) < atr_period + 2:
    return empty, empty

  threshold = atr_mult * findATR(data, atr_period)
  if threshold <= 0:
    return empty, empty

  high = data["high"].to_numpy()
  low  = data["low"].to_numpy()
  n    = len(data)

  high_idx, low_idx = [], []

  direction  = 0
  run_high   = high[0]; run_high_i = 0
  run_low    = low[0];  run_low_i  = 0

  for i in range(1, n):
    h, l = high[i], low[i]
    if direction == 0:
      if h > run_high: run_high = h; run_high_i = i
      if l < run_low:  run_low  = l; run_low_i  = i
      if run_high - l >= threshold and run_high_i < i:
        high_idx.append(run_high_i)
        direction  = -1
        run_low    = l; run_low_i  = i
      elif h - run_low >= threshold and run_low_i < i:
        low_idx.append(run_low_i)
        direction  = 1
        run_high   = h; run_high_i = i
    elif direction == 1:
      if h > run_high: run_high = h; run_high_i = i
      if run_high - l >= threshold:
        high_idx.append(run_high_i)
        direction  = -1
        run_low    = l; run_low_i  = i
    else:
      if l < run_low: run_low = l; run_low_i = i
      if h - run_low >= threshold:
        low_idx.append(run_low_i)
        direction  = 1
        run_high   = h; run_high_i = i

  def _is_local_high(j):
    if j > 0      and high[j] < high[j - 1]: return False
    if j < n - 1  and high[j] < high[j + 1]: return False
    return True

  def _is_local_low(j):
    if j > 0      and low[j]  > low[j - 1]:  return False
    if j < n - 1  and low[j]  > low[j + 1]:  return False
    return True

  high_idx = [j for j in high_idx if _is_local_high(j)]
  low_idx  = [j for j in low_idx  if _is_local_low(j)]

  return data.iloc[high_idx], data.iloc[low_idx]


def is_local_swing_high(data: pd.DataFrame, iloc: int) -> bool:
  """True if data.iloc[iloc].high is strictly greater than both immediate neighbours."""
  if iloc <= 0 or iloc >= len(data) - 1:
    return False
  h = data["high"]
  return float(h.iloc[iloc]) > float(h.iloc[iloc - 1]) and float(h.iloc[iloc]) > float(h.iloc[iloc + 1])


def is_local_swing_low(data: pd.DataFrame, iloc: int) -> bool:
  """True if data.iloc[iloc].low is strictly less than both immediate neighbours."""
  if iloc <= 0 or iloc >= len(data) - 1:
    return False
  l = data["low"]
  return float(l.iloc[iloc]) < float(l.iloc[iloc - 1]) and float(l.iloc[iloc]) < float(l.iloc[iloc + 1])
