import numpy as np
import pandas as pd

from find_ATR import findATR

# Defaults. Everything is measured in bars and in ATR multiples; only MERGE_TOL
# is in price units (points).
MIN_BARS = 2        # candles that must fit the starting box
MERGE_TOL = 2.0     # inner box height within this of its outer box's -> same range
STRONG_RANGE_ATR = 1.5  # strong breakout: lower-TF candle(s) range >= this x lower-TF ATR
STRONG_BODY = 0.5       # ... with a body >= this share of that range

TIMEFRAME_MINUTES = {"M1": 1, "M2": 2, "M3": 3, "M5": 5, "M10": 10, "M15": 15, "M30": 30,
                     "H1": 60, "H2": 120, "H4": 240, "D1": 1440}

# (k, kPrime) per timeframe: k = starting box height limit, kPrime = widening
# limit for a range with no outer range, both x ATR; kPrime > k. All equal for
# now, to be fine-tuned per timeframe (first tuned on 2026 ES H1).
TIMEFRAME_ATR_MULTS = {tf: (0.7, 0.8) for tf in TIMEFRAME_MINUTES}

# TEMPORARY start rule (`seed_by_overlap=True`): consecutive candles start a
# range when each adjacent pair overlaps by at least this % of the pair's
# combined high-low box, instead of the k x ATR height cap. Per timeframe, to
# be fine-tuned later. `seed_by_overlap=False` restores the ATR rule.
TIMEFRAME_SEED_OVERLAP = {tf: 75.0 for tf in TIMEFRAME_MINUTES}

# TEMPORARY widening cap (`widen_by_pct=True`): a range with no outer range may
# widen to at most this % taller than its STARTING box, instead of kPrime x
# ATR. Per timeframe, to be fine-tuned later. `widen_by_pct=False` restores the
# ATR cap.
TIMEFRAME_MAX_WIDEN_PCT = {tf: 20.0 for tf in TIMEFRAME_MINUTES}


def find_range(data: pd.DataFrame,
               timeframe: str = "H1",
               lower_data: pd.DataFrame = None,
               lower_timeframe: str = "M5",
               min_bars: int = MIN_BARS,
               seed_atr_mult: float = None,
               max_atr_mult: float = None,
               merge_tol: float = MERGE_TOL,
               atr_period: int = 21,
               seed_by_overlap: bool = True,
               seed_overlap_pct: float = None,
               widen_by_pct: bool = True,
               max_widen_pct: float = None,
               strong_range_atr: float = STRONG_RANGE_ATR,
               strong_body: float = STRONG_BODY) -> pd.DataFrame:
  """
  Tight range (consolidation) -- a run of consecutive candles held inside a
  box that is small next to the market's normal candle size, ended by a
  candle that closes (or opens, on a gap) outside it.

  `timeframe` ("M1" ... "H4") is the timeframe the ranges are found on. `data`
  is OHLC bars at that timeframe, or at a finer one, in which case it is
  resampled up first (bars labelled by their start; `data` coarser than
  `timeframe` is an error; "D1" ranges need D1 bars, a calendar-day resample
  would not match the futures session). The timeframe also picks the tuned
  (k, kPrime) pair below (`TIMEFRAME_ATR_MULTS`); passing `seed_atr_mult` /
  `max_atr_mult` overrides it.

  Every candidate is tracked at the same time, so ranges can sit inside one
  another:

    1. START. TEMPORARILY (`seed_by_overlap`, default on): any `min_bars` (2)
       consecutive candles where each adjacent pair overlaps by at least
       `seed_overlap_pct` (75) % of the pair's combined high-low box start a
       candidate, whatever the box's height. With `seed_by_overlap=False` the
       original rule applies: the candles' combined box is no taller than k x
       ATR (`seed_atr_mult`, 0.7). ATR is findATR(atr_period) as of the candle
       BEFORE the first one, so the range's own small candles never shrink the
       yardstick (the widening cap below still uses it). A candidate that
       starts while others are live sits inside the innermost of them -- its
       OUTER range.
    2. GROW. A candle that closes inside the box joins it. A wick outside
       widens the box: up to the outer range's edges if it has one, otherwise
       up to `max_widen_pct` (20) % taller than its starting box
       (`widen_by_pct`, TEMPORARY; with `widen_by_pct=False`, up to kPrime x
       ATR, `max_atr_mult`, 0.8, kPrime > k). A wick past that
       limit that closes back inside is a FAILED BREAKOUT: this candidate is
       cancelled, while wider ranges around it carry on (widening on the same
       wick if they can).
    3. MERGE. An inner box whose height comes within `merge_tol` (2 points) of
       its outer box's height is the same range: it is folded into the outer
       one (status "merged"). A new start already within `merge_tol` of the
       range around it is never opened.
    4. BREAK. The first candle that OPENS outside the box (a gap) or CLOSES
       outside it ends the range: status "broken". An inner range can break
       while its outer range holds. The break is then graded on the lower
       timeframe (`lower_timeframe`, default "M5"): "strong" if, inside the
       break candle's span, some run of same-direction lower candles (starting
       from one that opens inside the box, or the closing candle itself) ends
       with a close beyond the box edge and, as one candle, has a range >=
       `strong_range_atr` x the lower timeframe's ATR(atr_period) as of the
       close before the run and a body >= `strong_body` of that range;
       otherwise "weak".

  The lower bars are `lower_data` (OHLC at `lower_timeframe`) or, when `data`
  was resampled from bars of exactly that timeframe, those bars. With neither,
  breakouts are not graded (`breakout` is None).

  The bars are read as one continuous series: a weekend, holiday or session
  gap is just the next candle, and a gap open outside the box is a break.
  Candles may belong to several ranges; a new range may start on another
  range's break candle.

  Point in time: a range is known from `confirm_time` (the last of its
  starting candles) and ends at `end_time`, its break grade from the close of
  its break candle. Every candidate is returned, including cancelled and merged
  ones, so a caller can ask which ranges were live at some moment without
  looking ahead. `high` / `low` are the FINAL box; for the box exactly as it
  stood at a given moment, run the detector on the bars up to that moment. ATR
  needs history: candles before `atr_period` + 1 bars into `data` never start a
  range.

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
    breakout       "strong" / "weak" (broken only; None if not graded)
    breakout_range_atr, breakout_body
                   range (x lower-TF ATR) and body share of the stretch that
                   was graded: the qualifying one if strong, else the first
                   candidate's (NaN if the lower bars held none)
  """
  tf_min = _tf_minutes(timeframe)
  if seed_atr_mult is None or max_atr_mult is None:
    k_def, kp_def = TIMEFRAME_ATR_MULTS[timeframe.upper()]
    seed_atr_mult = k_def if seed_atr_mult is None else seed_atr_mult
    max_atr_mult = kp_def if max_atr_mult is None else max_atr_mult
  if seed_overlap_pct is None:
    seed_overlap_pct = TIMEFRAME_SEED_OVERLAP[timeframe.upper()]
  if max_widen_pct is None:
    max_widen_pct = TIMEFRAME_MAX_WIDEN_PCT[timeframe.upper()]
  if not max_atr_mult > seed_atr_mult:
    raise ValueError(f"kPrime ({max_atr_mult}) must exceed k ({seed_atr_mult})")

  cols = ["range_id", "status", "start_time", "confirm_time", "last_time", "end_time",
          "high", "low", "height", "seed_high", "seed_low", "atr", "outer_id",
          "outer_held", "break_dir", "break_how", "break_price",
          "breakout", "breakout_range_atr", "breakout_body"]

  sp = _spacing_minutes(data.index)
  base = data
  if sp is not None and sp > tf_min + 1e-9:
    raise ValueError(f"data is {sp:g}-minute bars, coarser than timeframe {timeframe}")
  if sp is not None and sp < tf_min - 1e-9:
    if tf_min >= 1440:
      raise ValueError("D1 ranges need D1 bars; a calendar-day resample would not match the session")
    data = _resample(data, tf_min)
    if lower_data is None and lower_timeframe is not None \
        and abs(sp - _tf_minutes(lower_timeframe)) < 1e-9:
      lower_data = base
  lower = None
  if lower_data is not None:
    lt_min = _tf_minutes(lower_timeframe)
    if lt_min >= tf_min:
      raise ValueError(f"lower_timeframe {lower_timeframe} must be finer than {timeframe}")
    lsp = _spacing_minutes(lower_data.index)
    if lsp is not None and lsp > lt_min + 1e-9:
      raise ValueError(f"lower_data is {lsp:g}-minute bars, coarser than {lower_timeframe}")
    lower = lower_data

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
          cap = ((r["seed_high"] - r["seed_low"]) * (1 + max_widen_pct / 100) if widen_by_pct
                 else max_atr_mult * r["atr"])
          past = max(hi, H[j]) - min(lo, L[j]) > cap
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
    if seed_by_overlap:
      if not np.all(_overlap(H[s:j], L[s:j], H[s + 1:j + 1], L[s + 1:j + 1]) >= seed_overlap_pct / 100):
        continue
    elif hi - lo > seed_atr_mult * atr[s - 1]:
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

  grader = _BreakoutGrader(lower, tf_min, atr_period, strong_range_atr, strong_body) \
      if lower is not None else None
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
    bo = (None, np.nan, np.nan)
    if broken and grader is not None:
      bo = grader.grade(idx[k], px > r["high"], r["high"], r["low"])
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
      "breakout": bo[0], "breakout_range_atr": bo[1], "breakout_body": bo[2],
    })
  found = pd.DataFrame(rows)
  out = data.loc[found["confirm_time"]]
  return out.assign(**{c: found[c].to_numpy() for c in cols})


class _BreakoutGrader:
  """Strong / weak grading of a range break on the lower timeframe."""

  def __init__(self, lower, tf_min, atr_period, range_atr, body):
    self.idx = lower.index
    self.O, self.H, self.L, self.C = (lower[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    self.atr = _atr_series(lower, atr_period)
    self.span = pd.Timedelta(minutes=tf_min)
    self.range_atr, self.body = range_atr, body

  def grade(self, t0, up, box_hi, box_lo):
    """(grade, range_atr, body) for a break whose higher-TF candle opens at t0:
    the first lower candle inside that candle closing beyond the edge, as the
    end of a same-direction run, that qualifies as strong; else weak."""
    O, H, L, C, atr = self.O, self.H, self.L, self.C, self.atr
    a, b = self.idx.searchsorted(t0, "left"), self.idx.searchsorted(t0 + self.span, "left")
    same = (C > O) if up else (C < O)
    first = (np.nan, np.nan)
    for m in range(a, b):
      if not (same[m] and ((C[m] > box_hi) if up else (C[m] < box_lo))):
        continue
      run = m
      while run - 1 >= 1 and same[run - 1]:
        run -= 1
      for s in range(run, m + 1):
        if not (s == m or box_lo <= O[s] <= box_hi):
          continue
        span = H[s:m + 1].max() - L[s:m + 1].min()
        if span <= 0 or not atr[s - 1] > 0:
          continue
        ra, bd = span / atr[s - 1], abs(C[m] - O[s]) / span
        if np.isnan(first[0]):
          first = (ra, bd)
        if ra >= self.range_atr and bd >= self.body:
          return "strong", ra, bd
    return "weak", first[0], first[1]


def _overlap(h1, l1, h2, l2):
  """Share of the pair's combined high-low box that both candles cover."""
  union = np.maximum(h1, h2) - np.minimum(l1, l2)
  inter = np.minimum(h1, h2) - np.maximum(l1, l2)
  return np.where(union > 0, np.clip(inter, 0, None) / np.where(union > 0, union, 1), 1.0)


def _tf_minutes(name):
  try:
    return TIMEFRAME_MINUTES[name.upper()]
  except (KeyError, AttributeError):
    raise ValueError(f"unknown timeframe {name!r}; use one of {', '.join(TIMEFRAME_MINUTES)}") from None


def _spacing_minutes(index):
  """Typical bar spacing in minutes (the median gap), None if too short to tell."""
  if len(index) < 3:
    return None
  return float(index.to_series().diff().median() / pd.Timedelta(minutes=1))


def _resample(data, minutes):
  agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
  if "volume" in data.columns:
    agg["volume"] = "sum"
  out = data.resample(f"{minutes}min", label="left", closed="left").agg(agg)
  return out.dropna(subset=["open"])


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
