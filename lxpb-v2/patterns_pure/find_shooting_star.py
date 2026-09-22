import pandas as pd

# A shooting star must close at or below the previous bar's high, give or take this
# share of the star's OWN range (so a close a hair over the previous high still counts).
PREV_BAR_BUFFER = 0.05

def find_shooting_star(data: pd.DataFrame, atr: float) -> pd.DataFrame:
  """
  The Shooting Star candlestick is a bearish reversal pattern with a small body and long upper shadow.
  It closes at or below the previous bar's high plus PREV_BAR_BUFFER x its own range.
  """
  # If data is empty, return an empty dataframe
  if data.empty:
    return pd.DataFrame(columns=data.columns.tolist())
  # Variables
  total_length = data["high"] - data["low"]
  body_size    = (data["close"] - data["open"]).abs()
  upper_wick   = data["high"] - data[["open","close"]].max(axis=1)
  lower_wick   = data[["open","close"]].min(axis=1) - data["low"]
  # Find all Shooting Stars
  rows = data[
    (body_size <= total_length * 0.35) &
    (upper_wick > total_length * 0.5) &
    (lower_wick <= total_length * 0.25) &
    (data["close"] <= data["high"].shift(1) + total_length * PREV_BAR_BUFFER)
  ]
  # Untested Logic
  rows = rows.assign(untested=False)
  # Current Value of the Symbol
  current_value = data["close"].iat[-1]
  # Iterate over all rows within 3 x ATR range
  for row in rows.loc[rows["low"].between(current_value - atr * 3, current_value + atr * 3)].itertuples():
    intersects = data[(data.index > row.Index) & (data["low"] <= row.low) & (row.low <= data["high"])]
    if len(intersects) == 0 or (len(intersects) == 1 and intersects["open"].iat[0] > row.low > intersects["close"].iat[0]):
      rows.at[row.Index, "untested"] = True
  return rows
