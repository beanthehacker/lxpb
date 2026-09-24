import pandas as pd

def find_engulfing_bearish(data: pd.DataFrame) -> pd.DataFrame:
  """
  A bearish engulfing bar has a higher "high" and lower "close" than previous bar's "high" and "low", respectively.
  Valid only if latest price is below the bearish engulfing's low price.
  """
  # Need at least 2 rows
  if len(data) < 2:
    return pd.DataFrame(columns=data.columns.tolist())
  
  return data.loc[
    (data["high"]  >= data["high"].shift(1)) &
    (data["close"] <= data["low"].shift(1))
  ]
