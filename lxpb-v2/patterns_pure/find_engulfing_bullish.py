import pandas as pd

def find_engulfing_bullish(data: pd.DataFrame) -> pd.DataFrame:
  """
  A bullish engulfing bar has a lower "low" and higher "close" than previous bar's "low" and "high", respectively.
  Valid only if latest price is above the bullish engulfing's high price.
  """
  # Need at least 2 rows
  if len(data) < 2:
    return pd.DataFrame(columns=data.columns.tolist())
  
  return data.loc[
    (data["low"]   < data["low"].shift(1)) &
    (data["close"] > data["high"].shift(1))
  ]
