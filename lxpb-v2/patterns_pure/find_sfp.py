import numpy as np
import pandas as pd

# Bars required on EACH side of a fractal swing pivot. A pivot is only
# CONFIRMED this many bars after its own, so it cannot be used before then.
SFP_SWING_K = 2


def find_sfp(data: pd.DataFrame, k: int = SFP_SWING_K) -> tuple[pd.DataFrame, pd.DataFrame]:
  """
  The SFP (Swing Failure Pattern) is a candle that SWEEPS a swing pivot and
  CLOSES back on the near side of it — the break of that swing failed.

    - SFP at a low (bullish): the candle's low goes strictly BELOW the swing
      low and its close comes back strictly ABOVE it.
    - SFP at a high (bearish): the candle's high goes strictly ABOVE the swing
      high and its close comes back strictly BELOW it.

  The swing it is measured against is the most recent one that is both
  CONFIRMED and still UNTESTED:

    - CONFIRMED — a swing high at bar j is a high strictly greater than all
      `k` highs on each side of it (the plain fractal pivot, mirrored for a
      low), so it is only known `k` bars later. It must have confirmed before
      the SFP candle's own bar, since a pivot whose right side is still
      forming was not visible to anyone yet.
    - UNTESTED — no bar between the swing and the SFP candle has reached it
      (high >= the swing high, or low <= the swing low). A swing price already
      traded through once is spent: coming back to it is ordinary two-way
      trade, not a failed break, and the sweep that first reached it was the
      only failure on offer. This also means one swing yields at most ONE SFP
      — the sweeping candle itself tests it, so the next candle poking the
      same price is not a second SFP.

  Because an untested swing is by definition the most extreme price since its
  own bar, the search is self-limiting: the reference is always the nearest
  swing still standing overhead (or underfoot), however far back that is.

  `data` must be one contiguous bar series. Returns (sfp_low, sfp_high) — two
  DataFrames sliced from `data`, each with `swing_time` (the swept pivot's own
  bar) and `swing_price` (the price it swept) alongside.
  """
  empty = data.iloc[0:0].assign(swing_time=pd.Series(dtype=data.index.dtype),
                                swing_price=pd.Series(dtype=float))
  if len(data) < 2 * k + 2 or k < 1:
    return empty, empty
  highs  = data["high"].to_numpy(float)
  lows   = data["low"].to_numpy(float)
  closes = data["close"].to_numpy(float)
  return (_sfp_side(data, lows, closes, _pivots(lows, k, True), k, True),
          _sfp_side(data, highs, closes, _pivots(highs, k, False), k, False))


def _pivots(extreme: np.ndarray, k: int, is_low: bool) -> np.ndarray:
  """Positions of the plain k-bar fractal pivots in `extreme`: a value
  strictly beyond all `k` values on each side of it."""
  n = len(extreme)
  hit = np.zeros(n, dtype=bool)
  core = slice(k, n - k)
  hit[core] = True
  for d in range(1, k + 1):
    left, right = extreme[k - d:n - k - d], extreme[k + d:n - k + d]
    if is_low:
      hit[core] &= (extreme[core] < left) & (extreme[core] < right)
    else:
      hit[core] &= (extreme[core] > left) & (extreme[core] > right)
  return np.flatnonzero(hit)


def _sfp_side(data: pd.DataFrame, extreme: np.ndarray, closes: np.ndarray,
              pivot_pos: np.ndarray, k: int, is_low: bool) -> pd.DataFrame:
  """One side's SFPs, in a single left-to-right pass.

  `live` holds the confirmed pivots nothing has reached yet, most recent
  last. Untested pivots get steadily more extreme going back (a nearer one
  that was less extreme would itself have been reached by the further one's
  own bar), so the most recent is the least extreme of them and sits on top:
  a bar that reaches past the top may reach past several, which is exactly
  what popping from the right while the test holds does."""
  reached = (lambda bar, pivot: bar <= pivot) if is_low else (lambda bar, pivot: bar >= pivot)
  swept   = (lambda bar, pivot: bar < pivot) if is_low else (lambda bar, pivot: bar > pivot)
  back    = (lambda c, pivot: c > pivot) if is_low else (lambda c, pivot: c < pivot)
  confirms_at = {int(p) + k: int(p) for p in pivot_pos}
  live, rows, swing_pos = [], [], []
  for i in range(len(extreme)):
    # Asked BEFORE this bar tests anything: the candle's own sweep is the
    # event, not a test that would disqualify its own reference.
    if live and swept(extreme[i], extreme[live[-1]]) and back(closes[i], extreme[live[-1]]):
      rows.append(i)
      swing_pos.append(live[-1])
    while live and reached(extreme[i], extreme[live[-1]]):
      live.pop()
    j = confirms_at.get(i)
    if j is not None:
      live.append(j)
  out = data.iloc[rows]
  return out.assign(swing_time=data.index[swing_pos],
                    swing_price=extreme[swing_pos])
