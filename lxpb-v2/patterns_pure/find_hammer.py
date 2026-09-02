import pandas as pd

def find_hammer(data: pd.DataFrame, atr: float) -> pd.DataFrame:
  """
  The Hammer candlestick is a bullish reversal pattern with a small body and long lower shadow.
  """
  # If data is empty, return an empty dataframe
  if data.empty:
    return pd.DataFrame(columns=data.columns.tolist())
  # Variables
  total_length = data["high"] - data["low"]
  body_size    = (data["close"] - data["open"]).abs()
  upper_wick   = data["high"] - data[["open","close"]].max(axis=1)
  lower_wick   = data[["open","close"]].min(axis=1) - data["low"]
  # Find all Hammers
  rows = data[
    (body_size <= total_length * 0.35) &
    (upper_wick <= total_length * 0.25) &
    (lower_wick > total_length * 0.5) &
    (data["close"] >= data["low"].shift(1))
  ]
  # Untested Logic
  rows = rows.assign(untested = False)
  # Current Value of the Symbol
  current_value = data["close"].iat[-1]
  # Iterate over all rows within 3 x ATR range
  for row in rows.loc[rows["high"].between(current_value - atr * 3, current_value + atr * 3)].itertuples(index=True):
    intersects = data[(data.index > row.Index) & (data["low"] <= row.high) & (row.high <= data["high"])]
    if len(intersects) == 0 or (len(intersects) == 1 and intersects["open"].iat[0] < row.high < intersects["close"].iat[0]):
      rows.at[row.Index, "untested"] = True
  return rows
