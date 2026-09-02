import pandas as pd

def findATR(data: pd.DataFrame, period: int = 21) -> float:
  """
  Average True Range — Wilder's RMA of TR (alpha=1/period), matching TradingView's ta.atr().

  Project convention: **ATR(21)** everywhere. Do not pass `period=` unless you
  have a specific reason to deviate (and write that reason down in the calling
  code). See CLAUDE.md → "ATR convention" and patterns-pure/PATTERNS.md.
  """
  if len(data) < 2:
    return 0
  tr = pd.concat([
    (data['high']  - data['low']),
    (data['high'] - data['close'].shift(1)).abs(),
    (data['low']  - data['close'].shift(1)).abs(),
  ], axis=1).max(axis=1).iloc[1:]   # drop first row (NaN from shift)
  return float(tr.ewm(alpha=1/period, adjust=False).mean().iloc[-1])
