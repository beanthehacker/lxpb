import numpy as np
import pandas as pd

from find_range import (STRONG_BODY, STRONG_RANGE_ATR, _atr_series, _BreakoutGrader,
                        _spacing_minutes)

ATR_MULT = 0.75     # zigzag turns when price reverses this x ATR(atr_period)
WIDEN_PCT = 20.0    # an edge may pass its starting edge by this % of the starting length
MERGE_TOL = 2.0     # two boxes whose BOTH edges are this close (points) are one range
MIN_EDGE_TOUCHES = 2  # a broken box is kept only with this many pivots at EACH edge
EDGE_TOL = 2.0        # a pivot is "at the edge" when within this many points of it


def find_range_box_zz(data: pd.DataFrame,
                      atr_mult: float = ATR_MULT,
                      atr_period: int = 21,
                      widen_pct: float = WIDEN_PCT,
                      merge_tol: float = MERGE_TOL,
                      lower_data: pd.DataFrame = None,
                      strong_range_atr: float = STRONG_RANGE_ATR,
                      strong_body: float = STRONG_BODY,
                      min_edge_touches: int = MIN_EDGE_TOUCHES,
                      edge_tol: float = EDGE_TOL) -> pd.DataFrame:
  """
  Range box built from ZigZag pivots (crests and troughs) instead of candles.

  `data` is OHLC bars at the timeframe to analyse (no resampling here).

  ZigZag. A crest is confirmed when price falls `atr_mult` x ATR(atr_period)
  off the running high, a trough symmetrically; pivots alternate. ATR is read
  as of the confirming bar, so every pivot is point-in-time. The last
  unconfirmed leg is not a pivot.

  Order inside a bar. A bar's high and low are applied one after the other, and
  which came first matters (a wide bar can end one leg and start the next).
  With `lower_data` it is read from the lower-timeframe bars inside the bar:
  the first lower bar to reach the bar's high vs the first to reach its low.
  When that cannot be told (no `lower_data`, no lower bars in the bar, or both
  extremes in one lower bar) the extreme in the current direction of travel is
  assumed first, as `find_swings` does. So `lower_data` can change which pivots
  exist, not only the grades.

  Every pair of consecutive pivots is a candidate: a crest and the trough
  IMMEDIATELY after it, or a trough and the crest immediately after it (so each
  pivot opens a box and closes one). The box is [trough, crest] and its height
  is the STARTING range length L. It is known from `confirm_time`, the bar that
  confirms the second pivot of the pair. Each later pivot is
  read against the box:

    - inside the box: the box holds.
    - a crest above the box that is no more than `widen_pct` (20) % of L over
      the STARTING crest, or a trough below it by no more than that: the box
      is WIDENED and the new edge recorded. (Per side, against the starting
      edge, so a box can end up at most 1.4 x L tall.)
    - past that limit: a) if some other live box that started earlier
      (an outer box) has BOTH edges within `merge_tol` (2) points of this
      box's edges once the pivot is included, this box is MERGED into it
      (status "merged", `merged_into`); b) otherwise it is BROKEN: the box
      ends (status "broken"), `break_dir` / `break_pivot_time` /
      `break_price` give the pivot.

  Candidates are tracked at the same time, so boxes may overlap in time and
  price and sit inside one another. They are NOT merged just for overlapping:
  only when both edges come within `merge_tol` of each other (which includes a
  100% overlap), whenever either box starts or widens. That is why an inner
  box that widens can be merged into its outer box later. The later-started
  box is the one that merges; the earlier one is left exactly as it was. A
  candidate that is already within `merge_tol` of a live box when it starts is
  returned at once as "merged".

  Breakout grade. A broken box is frozen: its edges never change again. With
  `lower_data` (OHLC on a finer timeframe than `data`) the break is graded
  exactly as `find_range` does. The break candle is the first `data` bar after
  the box's last inside pivot, up to the bar that confirms the breaking pivot,
  that OPENS or CLOSES beyond the box edge (`break_time`); if only wicks got
  out, it is the breaking pivot's own bar. Inside that candle's span, "strong"
  = some run of same-direction lower candles (starting from one that opens
  inside the box, or the closing candle itself) ends with a close beyond the
  edge and, as one candle, has a range >= `strong_range_atr` (1.5) x the lower
  timeframe's ATR(atr_period) as of the close before the run and a body >=
  `strong_body` (0.5) of that range; otherwise "weak". Without `lower_data`,
  breakouts are not graded (`breakout` is None).

  Edge filter. Once a box is frozen (broken), it is kept only if at least
  `min_edge_touches` (2) of its inside crests sit within `edge_tol` (2) points
  of its final top edge AND at least that many of its inside troughs within
  `edge_tol` of its final bottom edge. The starting crest and trough count, as
  does a pivot that widened an edge (distance 0); the breaking pivot does not.
  So the default needs one more touch on each side beyond the seed. Boxes that
  fail are dropped from the result (their `range_id` is not reused, so ids can
  have gaps and a `merged_into` can name a dropped box). Open and merged boxes
  are not frozen and are never filtered. `n_top_edge` / `n_bottom_edge` give the
  counts for every row; `min_edge_touches=0` keeps everything.

  Point in time: a pivot is known only at its confirming bar, so `end_time` is
  that bar, later than the pivot's own `break_pivot_time`; price may well have
  left the box before it. Every candidate is returned (broken, merged, open)
  so a caller can ask which boxes were live at a moment without looking ahead.
  `high` / `low` are the box as it stood when it ended (or at the last bar).

  Returns one row per candidate, indexed by `confirm_time` and sorted by it:
  the confirm bar's OHLC plus

    range_id       0, 1, 2, ... in the order the candidates started
    status         "open" (live at the last bar) / "broken" / "merged"
    start_time     the bar of the box's first pivot
    start_kind     "crest" / "trough": what that first pivot is
    trough_time    the starting trough's bar
    confirm_time   the bar that confirms the second starting pivot
    last_time      the bar of the last pivot that sat inside the box
    end_time       confirming bar of the pivot that ended it (NaT while open)
    high, low      box; height = high - low
    seed_high, seed_low, seed_height   the starting box (L)
    n_pivots       pivots inside the box, the two starting ones included
    n_top_edge, n_bottom_edge   crests within edge_tol of the top edge / troughs
                   within edge_tol of the bottom edge (starting pivots included)
    merged_into    range_id of the box it merged into (merged only)
    break_dir      "up" / "down" (broken only)
    break_pivot_time, break_price   the pivot that broke it (broken only)
    break_time     the break candle that was graded (broken only)
    breakout       "strong" / "weak" (broken only; None if not graded)
    breakout_range_atr, breakout_body
                   range (x lower-TF ATR) and body share of the stretch that
                   was graded: the qualifying one if strong, else the first
                   candidate's (NaN if the lower bars held none)
  """
  cols = ["range_id", "status", "start_time", "start_kind", "trough_time", "confirm_time",
          "last_time", "end_time", "high", "low", "height", "seed_high",
          "seed_low", "seed_height", "n_pivots", "n_top_edge", "n_bottom_edge",
          "merged_into", "break_dir",
          "break_pivot_time", "break_price", "break_time", "breakout",
          "breakout_range_atr", "breakout_body"]
  grader = None
  if lower_data is not None:
    tf_min, lsp = _spacing_minutes(data.index), _spacing_minutes(lower_data.index)
    if tf_min is None or lsp is None or not lsp < tf_min - 1e-9:
      raise ValueError(f"lower_data ({lsp} min bars) must be finer than data ({tf_min} min bars)")
    grader = _BreakoutGrader(lower_data, tf_min, atr_period, strong_range_atr, strong_body)
  n = len(data)
  if n <= atr_period + 1:
    return pd.DataFrame(columns=data.columns.tolist() + cols)

  H, L = data["high"].to_numpy(float), data["low"].to_numpy(float)
  order = _intrabar_order(data.index, tf_min, lower_data) if grader is not None else None
  pivots = _zigzag_pivots(H, L, _atr_series(data, atr_period), atr_mult, atr_period, order)
  boxes = _boxes_from_pivots(pivots, widen_pct, merge_tol)

  idx = data.index
  O, C = data["open"].to_numpy(float), data["close"].to_numpy(float)
  rows = []
  for b in boxes:
    ended = b["end_j"] is not None
    broken = b["status"] == "broken"
    n_top = sum(abs(p - b["high"]) <= edge_tol for p in b["crests"])
    n_bot = sum(abs(p - b["low"]) <= edge_tol for p in b["troughs"])
    if broken and min(n_top, n_bot) < min_edge_touches:
      continue
    bo, k = (None, np.nan, np.nan), None
    if broken:
      up = b["break_dir"] == "up"
      k = b["break_i"]
      for m in range(b["last_i"] + 1, b["end_j"] + 1):
        if (O[m] > b["high"] or C[m] > b["high"]) if up else (O[m] < b["low"] or C[m] < b["low"]):
          k = m
          break
      if grader is not None:
        bo = grader.grade(idx[k], up, b["high"], b["low"])
    rows.append({
      "range_id": b["id"],
      "status": b["status"],
      "start_time": idx[b["start_i"]],
      "start_kind": b["start_kind"],
      "trough_time": idx[b["trough_i"]],
      "confirm_time": idx[b["confirm_j"]],
      "last_time": idx[b["last_i"]],
      "end_time": idx[b["end_j"]] if ended else pd.NaT,
      "high": b["high"], "low": b["low"], "height": b["high"] - b["low"],
      "seed_high": b["seed_high"], "seed_low": b["seed_low"],
      "seed_height": b["seed_high"] - b["seed_low"],
      "n_pivots": b["n"],
      "n_top_edge": n_top, "n_bottom_edge": n_bot,
      "merged_into": b["merged_into"] if b["status"] == "merged" else np.nan,
      "break_dir": b["break_dir"] if broken else None,
      "break_pivot_time": idx[b["break_i"]] if broken else pd.NaT,
      "break_price": b["break_price"] if broken else np.nan,
      "break_time": idx[k] if broken else pd.NaT,
      "breakout": bo[0], "breakout_range_atr": bo[1], "breakout_body": bo[2],
    })
  if not rows:
    return pd.DataFrame(columns=data.columns.tolist() + cols)
  found = pd.DataFrame(rows)
  out = data.loc[found["confirm_time"]]
  return out.assign(**{c: found[c].to_numpy() for c in cols})


def _zigzag_pivots(H, L, atr, atr_mult, atr_period, order=None):
  """ZigZag pivots as (kind, bar, price, confirming bar), oldest first, kind
  "crest" / "trough". Same state machine as find_swings, but the reversal
  threshold is the ATR as of the bar doing the confirming (nothing is read from
  later bars) and no pivot can be confirmed before ATR has `atr_period` bars.

  A bar's high and low are applied as two events. `order[j]` is +1 when bar j's
  high came before its low, -1 when the low came first, 0 / None when unknown;
  unknown means the extreme in the current direction of travel comes first
  (the find_swings assumption)."""
  pivots = []
  direction = 0
  run_hi, run_hi_i = H[0], 0
  run_lo, run_lo_i = L[0], 0
  for j in range(1, len(H)):
    h, l = H[j], L[j]
    thr = atr_mult * atr[j] if j >= atr_period else np.inf
    if direction == 0:
      if h > run_hi: run_hi, run_hi_i = h, j
      if l < run_lo: run_lo, run_lo_i = l, j
      if run_hi - l >= thr and run_hi_i < j:
        pivots.append(("crest", run_hi_i, run_hi, j))
        direction = -1
        run_lo, run_lo_i = l, j
      elif h - run_lo >= thr and run_lo_i < j:
        pivots.append(("trough", run_lo_i, run_lo, j))
        direction = 1
        run_hi, run_hi_i = h, j
      continue
    o = order[j] if order is not None else 0
    high_first = o > 0 or (o == 0 and direction == 1)
    for ev in ("h", "l") if high_first else ("l", "h"):
      if ev == "h":
        if direction == 1:
          if h > run_hi: run_hi, run_hi_i = h, j
        elif h - run_lo >= thr:
          pivots.append(("trough", run_lo_i, run_lo, j))
          direction = 1
          run_hi, run_hi_i = h, j
      else:
        if direction == -1:
          if l < run_lo: run_lo, run_lo_i = l, j
        elif run_hi - l >= thr:
          pivots.append(("crest", run_hi_i, run_hi, j))
          direction = -1
          run_lo, run_lo_i = l, j
  return pivots


def _intrabar_order(idx, tf_min, lower):
  """+1 where a bar's high came before its low in the lower-timeframe bars
  inside it, -1 where the low came first, 0 where it cannot be told (no lower
  bars, or high and low in the same lower bar)."""
  n = len(idx)
  order = np.zeros(n, dtype=np.int8)
  lidx = lower.index
  j = np.searchsorted(idx.to_numpy(), lidx.to_numpy(), side="right") - 1
  ok = (j >= 0) & (lidx.to_numpy() < idx.to_numpy()[np.clip(j, 0, n - 1)] + pd.Timedelta(minutes=tf_min))
  df = pd.DataFrame({"j": j[ok], "h": lower["high"].to_numpy(float)[ok],
                     "l": lower["low"].to_numpy(float)[ok], "p": np.arange(len(lower))[ok]})
  if df.empty:
    return order
  g = df.groupby("j")
  first_hi = df[df["h"] == g["h"].transform("max")].groupby("j")["p"].min()
  first_lo = df[df["l"] == g["l"].transform("min")].groupby("j")["p"].min()
  both = first_hi.index.intersection(first_lo.index)
  order[both.to_numpy()] = np.sign(first_lo[both].to_numpy() - first_hi[both].to_numpy())
  return order


def _boxes_from_pivots(pivots, widen_pct, merge_tol):
  """The box state machine on a (kind, bar, price, confirming bar) pivot list."""
  boxes, live = [], []          # live: earliest-started first

  def twin(hi, lo, others):
    """First box in `others` with both edges within merge_tol of (hi, lo)."""
    for o in others:
      if abs(hi - o["high"]) <= merge_tol and abs(lo - o["low"]) <= merge_tol:
        return o
    return None

  def end(b, status, j):
    b["status"], b["end_j"] = status, j

  for q, (kind, i, price, j) in enumerate(pivots):
    # Existing boxes read this pivot, earliest-started first, so an outer box
    # has already taken it (widened, merged away or broken) when an inner box
    # tests itself against it.
    for pos, b in enumerate(list(live)):
      if kind == "crest":
        over = price - b["seed_high"]
        beyond = price > b["high"]
      else:
        over = b["seed_low"] - price
        beyond = price < b["low"]
      if beyond and over > widen_pct / 100 * (b["seed_high"] - b["seed_low"]):
        hi = max(b["high"], price)
        lo = min(b["low"], price)
        outer = twin(hi, lo, [o for o in live[:pos] if o["status"] == "open"])
        if outer is not None:
          end(b, "merged", j)
          b["merged_into"] = outer["id"]
        else:
          end(b, "broken", j)
          b.update(break_dir="up" if kind == "crest" else "down",
                   break_i=i, break_price=price)
        continue
      if beyond:
        if kind == "crest":
          b["high"] = price
        else:
          b["low"] = price
      b["n"] += 1
      b["crests" if kind == "crest" else "troughs"].append(price)
      b["last_i"] = i
      outer = twin(b["high"], b["low"], [o for o in live[:pos] if o["status"] == "open"])
      if outer is not None:
        end(b, "merged", j)
        b["merged_into"] = outer["id"]
    live = [b for b in live if b["status"] == "open"]

    if q > 0:
      k0, i0, p0, _ = pivots[q - 1]
      if kind == "trough":
        ci, chi, ti, tlo = i0, p0, i, price
      else:
        ci, chi, ti, tlo = i, price, i0, p0
      b = dict(id=len(boxes), start_i=i0, start_kind=k0, crest_i=ci, trough_i=ti,
               confirm_j=j, last_i=i,
               high=chi, low=tlo, seed_high=chi, seed_low=tlo, n=2,
               crests=[chi], troughs=[tlo],
               status="open", end_j=None, merged_into=None,
               break_dir=None, break_i=None, break_price=None)
      boxes.append(b)
      outer = twin(chi, tlo, live)
      if outer is not None:
        end(b, "merged", j)
        b["merged_into"] = outer["id"]
      else:
        live.append(b)
  return boxes
