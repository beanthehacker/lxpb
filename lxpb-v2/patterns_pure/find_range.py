import numpy as np
import pandas as pd

from find_ATR import findATR

# Defaults, tuned on 2026 ES H1 (a range the user marked by hand, Sun 2026-01-04
# 17:00-21:00 PT, breaking up at 22:00, must come out exactly). Everything is
# measured in bars and in ATR multiples, so the same defaults apply to any
# timeframe; only MERGE_TOL is in price units.
MIN_BARS = 4        # candles that must fit the starting box
SEED_ATR_MULT = 0.7  # starting box height <= this x ATR
MAX_ATR_MULT = 0.8   # a range with no outer range may widen up to this x ATR
MERGE_TOL = 1.0      # inner box height within this of its outer box's -> same range


def find_range(data: pd.DataFrame,
               min_bars: int = MIN_BARS,
               seed_atr_mult: float = SEED_ATR_MULT,
               max_atr_mult: float = MAX_ATR_MULT,
               merge_tol: float = MERGE_TOL,
               atr_period: int = 21) -> pd.DataFrame:
  """
  Tight range (consolidation) -- a run of consecutive candles held inside a
  box that is small next to the market's normal candle size, ended by a
  candle that closes (or opens, on a gap) outside it. Timeframe-agnostic: pass
  M5, H1 or D1 bars and the ranges come out on that timeframe.

  Every candidate is tracked at the same time, so ranges can sit inside one
  another:

    1. START. Any `min_bars` consecutive candles whose combined high-low box
       is no taller than `seed_atr_mult` x ATR start a candidate. ATR is
       findATR(atr_period) as of the candle BEFORE the first one, so the
       range's own small candles never shrink the yardstick. A candidate that
       starts while others are live sits inside the innermost of them -- its
       OUTER range.
    2. GROW. A candle that closes inside the box joins it. A wick outside
       widens the box: up to the outer range's edges if it has one, otherwise
       up to `max_atr_mult` x ATR. A wick past that limit that closes back
       inside is a FAILED BREAKOUT: this candidate is cancelled, while wider
       ranges around it carry on (widening on the same wick if they can).
    3. MERGE. An inner box whose height comes within `merge_tol` of its outer
       box's height is the same range: it is folded into the outer one
       (status "merged"). A new start already within `merge_tol` of the range
       around it is never opened.
    4. BREAK. The first candle that OPENS outside the box (a gap) or CLOSES
       outside it ends the range: status "broken". An inner range can break
       while its outer range holds.

  The bars are read as one continuous series: a weekend, holiday or session
  gap is just the next candle, and a gap open outside the box is a break.
  Candles may belong to several ranges; a new range may start on another
  range's break candle.

  Point in time: a range is known from `confirm_time` (the last of its
  starting candles) and ends at `end_time`. Every candidate is returned,
  including cancelled and merged ones, so a caller can ask which ranges were
  live at some moment without looking ahead. `high` / `low` are the FINAL
  box; for the box exactly as it stood at a given moment, run the detector on
  the bars up to that moment. ATR needs history: candles before
  `atr_period` + 1 bars into `data` never start a range.

  Returns one row per candidate, indexed by `confirm_time` and sorted by it
  (oldest first): the confirm candle's OHLC plus

    range_id       0, 1, 2, ... in the order the candidates started
    status         "broken" / "open" (still live at the last bar) /
                   "cancelled" (failed breakout) / "merged" (into its outer)
    start_time     first candle of the range
    confirm_time   last starting candle: the range exists from here on
    last_time      last candle inside the box
    end_time       the break / cancel / merge candle (NaT while open)
    high, low      final box
    height         high - low
    seed_high, seed_low   the starting box
    atr            the ATR the range was measured against
    outer_id       range_id of the outer range at the end (NaN if none)
    outer_held     broken ranges with an outer range: True if the outer range
                   was still live after the break candle
    break_dir      "up" / "down" (broken only)
    break_how      "close" / "gap" (broken only)
    break_price    the break candle's close, or its open on a gap
  """
  cols = ["range_id", "status", "start_time", "confirm_time", "last_time", "end_time",
          "high", "low", "height", "seed_high", "seed_low", "atr", "outer_id",
          "outer_held", "break_dir", "break_how", "break_price"]
  n = len(data)
  if n <= atr_period + min_bars:
    return _empty(data, cols)

  atr = _atr_series(data, atr_period)
  O, H, L, C = (data[c].to_numpy(float) for c in ("open", "high", "low", "close"))
  idx = data.index
  cands, live = [], []           # live: outer -> inner (start order)

  def alive_outer(r):
    p = r["outer"]
    while p is not None and cands[p]["end"] is not None:
      p = cands[p]["outer"]
    return p

  for j in range(n):
    # Break / widen / cancel: every live range reads this candle against its
    # own box and its outer range's box as they stood BEFORE this candle.
    pre = {r["id"]: (r["high"], r["low"]) for r in live}
    for r in live:
      hi, lo = pre[r["id"]]
      if O[j] > hi or O[j] < lo:
        r.update(end="broken", how="gap", end_k=j)
      elif C[j] > hi or C[j] < lo:
        r.update(end="broken", how="close", end_k=j)
      elif H[j] > hi or L[j] < lo:
        p = r["outer"]
        if p is not None and p in pre:
          past = H[j] > pre[p][0] or L[j] < pre[p][1]
        else:
          past = max(hi, H[j]) - min(lo, L[j]) > max_atr_mult * r["atr"]
        if past:
          r.update(end="cancelled", end_k=j)
        else:
          r["high"], r["low"] = max(hi, H[j]), min(lo, L[j])
    live = [r for r in live if r["end"] is None]
    # Merge an inner box that has grown into (nearly) its outer box.
    for r in live:
      r["outer"] = alive_outer(r)
      p = r["outer"]
      if p is not None and (cands[p]["high"] - cands[p]["low"]) - (r["high"] - r["low"]) <= merge_tol:
        r.update(end="merged", end_k=j)
    live = [r for r in live if r["end"] is None]
    for r in live:
      r["outer"] = alive_outer(r)
    # A new candidate whose starting candles end on this one.
    s = j - min_bars + 1
    if s < atr_period + 1:
      continue
    hi, lo = H[s:j + 1].max(), L[s:j + 1].min()
    if hi - lo > seed_atr_mult * atr[s - 1]:
      continue
    p = live[-1]["id"] if live else None
    if p is not None and (cands[p]["high"] - cands[p]["low"]) - (hi - lo) <= merge_tol:
      continue
    r = dict(id=len(cands), s=s, k=j, high=hi, low=lo, seed_high=hi, seed_low=lo,
             atr=atr[s - 1], outer=p, end=None, how=None, end_k=None)
    cands.append(r)
    live.append(r)

  if not cands:
    return _empty(data, cols)
  rows = []
  for r in cands:
    k = r["end_k"]
    broken = r["end"] == "broken"
    px = None
    if broken:
      px = O[k] if r["how"] == "gap" else C[k]
    held = None
    if broken and r["outer"] is not None:
      q = cands[r["outer"]]
      held = q["end_k"] is None or q["end_k"] > k
    rows.append({
      "range_id": r["id"],
      "status": r["end"] or "open",
      "start_time": idx[r["s"]],
      "confirm_time": idx[r["k"]],
      "last_time": idx[(k if k is not None else n) - 1],
      "end_time": idx[k] if k is not None else pd.NaT,
      "high": r["high"], "low": r["low"], "height": r["high"] - r["low"],
      "seed_high": r["seed_high"], "seed_low": r["seed_low"], "atr": r["atr"],
      "outer_id": r["outer"] if r["outer"] is not None else np.nan,
      "outer_held": held,
      "break_dir": ("up" if px > r["high"] else "down") if broken else None,
      "break_how": r["how"] if broken else None,
      "break_price": px,
    })
  found = pd.DataFrame(rows)
  out = data.loc[found["confirm_time"]]
  return out.assign(**{c: found[c].to_numpy() for c in cols})


def _atr_series(data, period):
  """findATR(data.iloc[:k + 1], period) for every k in one pass. findATR is the
  sole ATR formula: this is its own smoothing run once over the whole frame
  (Wilder's RMA is causal, so each value only sees bars up to k), and it is
  checked against findATR itself before use."""
  tr = pd.concat([
    (data["high"] - data["low"]),
    (data["high"] - data["close"].shift(1)).abs(),
    (data["low"] - data["close"].shift(1)).abs(),
  ], axis=1).max(axis=1).iloc[1:]
  s = tr.ewm(alpha=1 / period, adjust=False).mean().reindex(data.index).to_numpy(float)
  for k in (len(data) // 2, len(data) - 1):
    ref = findATR(data.iloc[:k + 1], period)
    assert abs(s[k] - ref) <= 1e-9 * max(1.0, abs(ref)), (k, s[k], ref)
  return s


def _empty(data, cols):
  return pd.DataFrame(columns=data.columns.tolist() + cols)
