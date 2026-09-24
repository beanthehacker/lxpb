import pandas as pd

def find_inside_bar(data: pd.DataFrame) -> pd.DataFrame:
  """
  Inside Bar has higher low and lower high than the previous bar.
  """
  # Need at least 2 rows
  if len(data) < 2:
    return pd.DataFrame(columns=data.columns.tolist())
  return data.loc[
    (data["high"] <= data["high"].shift(1)) &
    (data["low"]  >= data["low"].shift(1))
  ]
