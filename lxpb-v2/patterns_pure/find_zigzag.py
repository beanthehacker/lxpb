from bisect import bisect_left
from typing import NamedTuple

import numpy as np
import pandas as pd

from find_range import _atr_series, _spacing_minutes

ATR_MULT = 0.75    # a leg is confirmed when price reverses this x ATR(atr_period) off its extreme
ATR_PERIOD = 21    # project-wide ATR period
MIN_BARS = 1       # the confirming bar is at least this many bars after the extreme's own bar, unless the
                   # lower-timeframe order proves the reversal came after the extreme inside that very bar


class Pivot(NamedTuple):
  """A confirmed pivot. `i` is the bar of the extreme itself, `j` the bar that
  confirmed it (j > i). `kind` is "crest" (a high) or "trough" (a low)."""
  kind: str
  i: int
  price: float
  j: int


class _Leg:
  """One leg being sought, in signed prices so a crest and a trough run the same
  code: for a crest `ext` is the running high and `adv` the lowest low since it;
  for a trough both are negated (`ext` = -running low, `adv` = -highest high)."""
  __slots__ = ("up", "ext", "ext_i", "adv", "adv_i")

  def __init__(self, up, ext, ext_i):
    self.up, self.ext, self.ext_i = up, ext, ext_i
    self.adv = self.adv_i = None

  def observe(self, j, xv, av, ext_first, known, thr, min_bars):
    """Apply bar j's two events (extension, adverse), in the order they happened.
    `known` is True when that order was read from lower-timeframe bars (not assumed):
    only then can the extreme's own bar hold evidence of its reversal.
    True when this bar confirms the leg."""
    for ev in (0, 1) if ext_first else (1, 0):
      if ev == 0:
        if xv > self.ext:
          self.ext, self.ext_i, self.adv, self.adv_i = xv, j, None, None
      elif j > self.ext_i or known:     # j == ext_i: this bar made the extreme, then (known order) fell
        if self.adv is None or av < self.adv:
          self.adv, self.adv_i = av, j
        if self.ext - self.adv >= thr and (j - self.ext_i >= min_bars or j == self.ext_i):
          return True
    return False


class ZigZag:
  """The streaming zigzag: feed bars one at a time with `step`. `find_zigzag`
  and `zigzag_pivots` are this same machine run over a whole series, so a live
  feed and a batch read can never disagree.

  `step(high, low, atr, order=0, t=None)` takes the bar's ATR(atr_period) as of
  that bar (its own bar included) and returns the pivots this bar confirms (at
  most one) as `Pivot`s. The first call is bar 0. `t` is the bar's time, any
  comparable (ns int, Timestamp); give it on every call or on none, and
  `most_recent` works off it. `pivots` is every confirmed pivot so far."""

  def __init__(self, atr_mult=ATR_MULT, atr_period=ATR_PERIOD, min_bars=MIN_BARS):
    if not (np.isfinite(atr_mult) and atr_mult > 0):
      raise ValueError("atr_mult must be finite and positive")
    if atr_period < 1:
      raise ValueError("atr_period must be at least 1")
    if min_bars < 1:
      raise ValueError("min_bars must be at least 1")
    self.atr_mult, self.atr_period, self.min_bars = atr_mult, atr_period, min_bars
    self.H, self.L, self.T, self.O = [], [], [], []
    self.pivots = []
    self._legs = []                 # [crest leg, trough leg] until the first pivot, then the one sought
    self._kinds, self._formed, self._confirmed, self._prices = [], [], [], []

  def step(self, high, low, atr, order=0, t=None):
    j = len(self.H)
    self.H.append(high)
    self.L.append(low)
    self.T.append(t)
    self.O.append(order)
    if j == 0:
      self._legs = [_Leg(True, high, 0), _Leg(False, -low, 0)]
      return []
    thr = self.atr_mult * atr if j >= self.atr_period else np.inf
    if thr != thr:
      thr = np.inf
    for leg in self._legs:
      up = leg.up
      xv, av = (high, low) if up else (-low, -high)
      ext_first = order == 0 or (order > 0) == up
      if leg.observe(j, xv, av, ext_first, order != 0, thr, self.min_bars):
        return [self._confirm(leg, j)]
    return []

  def _confirm(self, leg, j):
    piv = Pivot("crest" if leg.up else "trough", leg.ext_i, leg.ext if leg.up else -leg.ext, j)
    # The next leg starts at the most adverse bar of this one; every later bar up
    # to and including j is already evidence of its reversal. So is that bar's OWN
    # far side, when the lower-timeframe order proves it made its extreme first and
    # moved away afterwards (the same rule as for any extreme's own bar): without it,
    # a bar that confirmed the previous pivot and then fell (rose) would lose that
    # move, and the leg's real extreme would be missed.
    nxt = _Leg(not leg.up, -leg.adv, leg.adv_i)
    a, o = leg.adv_i, self.O[leg.adv_i]
    if o != 0 and (o > 0) == nxt.up:
      nxt.adv, nxt.adv_i = (self.L[a] if nxt.up else -self.H[a]), a
    for k in range(leg.adv_i + 1, j + 1):
      av = self.L[k] if nxt.up else -self.H[k]
      if nxt.adv is None or av < nxt.adv:
        nxt.adv, nxt.adv_i = av, k
    self._legs = [nxt]
    self.pivots.append(piv)
    self._kinds.append(piv.kind)
    self._formed.append(self.T[piv.i])
    self._confirmed.append(self.T[j])
    self._prices.append(piv.price)
    return piv

  def most_recent(self, kind, cutoff, after=None):
    """(time, price) of the newest `kind` pivot formed AND confirmed strictly
    before `cutoff` (and formed strictly after `after`); (None, None) if none."""
    return _newest(self._kinds, self._formed, self._confirmed, self._prices, kind, cutoff, after)


def zigzag_pivots(high, low, atr, order=None, atr_mult=ATR_MULT, atr_period=ATR_PERIOD,
                  min_bars=MIN_BARS):
  """`ZigZag` run over arrays: the pivots as a list of `Pivot`, oldest first.
  `atr[j]` is the ATR as of bar j; `order[j]` is the intrabar order (see
  `intrabar_order`), None / 0 where unknown."""
  zz = ZigZag(atr_mult, atr_period, min_bars)
  out = []
  for j in range(len(high)):
    out += zz.step(float(high[j]), float(low[j]), float(atr[j]),
                   0 if order is None else int(order[j]))
  return out


def find_zigzag(data: pd.DataFrame,
                atr_mult: float = ATR_MULT,
                atr_period: int = ATR_PERIOD,
                min_bars: int = MIN_BARS,
                lower_data: pd.DataFrame = None) -> pd.DataFrame:
  """
  The ONE zigzag of this repo: every crest (swing high) and trough (swing low) of
  `data`, an OHLC frame at the timeframe to analyse (no resampling here).

  A crest is confirmed when, on a bar at least `min_bars` bars after the extreme
  itself (or on the extreme's own bar when `lower_data` shows the fall came after
  the high), the lowest low since the running high is `atr_mult` x ATR(atr_period)
  below it; a trough is the mirror image. Pivots alternate. No fixed point
  thresholds: the size of a leg follows the volatility of the series it is run
  on.

    - ATR is read as of the confirming bar (that bar included), so every pivot is
      point-in-time. No pivot can be confirmed before the ATR has `atr_period` bars.
    - Reversal is CUMULATIVE: the deepest adverse price since the extreme counts,
      not only the confirming bar's. A new extreme (a higher high while seeking a
      crest) restarts the count, so a dip taken straight back never makes a pivot.
    - `min_bars` (default 1): the confirming bar is at least that many bars after
      the extreme's bar. The extreme's own bar counts as evidence of the reversal
      ONLY when `lower_data` shows that bar made its extreme first and fell (rose)
      afterwards; with the order unknown (no `lower_data`, no lower bars, both
      extremes in one lower bar) or the other way round it never counts, so a bar
      cannot confirm its own extreme on an assumed order. When it does count, the
      bar can confirm its own extreme (the bar is then `confirm_bar` == `bar`).
      min_bars >= 1.
    - The next leg starts at the most adverse bar of the one just confirmed (the
      lowest low after a crest), and every bar after it up to the confirming bar
      already counts as evidence for it. That most adverse bar's OWN far side counts
      too when `lower_data` proves the order inside it (extreme first, then the move
      away; 2026-10-16: a bar that confirmed the previous pivot and then fell used to
      lose that fall, so the leg's real extreme was missed, e.g. M5 Oct 16 2026 05:50 PT).
      A crest and the trough right after it can therefore share one bar.
      At most one pivot is confirmed per bar.
    - The last, still-extending leg is not a pivot.

  Order inside a bar. A bar's high and low are two events and which came first
  matters (a bar that makes a new high and also falls far enough can either
  confirm the old crest, low first, or just move it, high first). With
  `lower_data` it is read from the lower-timeframe bars inside the bar: the first
  lower bar to reach the bar's high vs the first to reach its low. When that
  cannot be told (no `lower_data`, no lower bars in the bar, or both extremes in
  one lower bar) the extreme in the current direction of travel is assumed first.

  Returns one row per pivot, oldest first, as a RangeIndex frame:

    time          the bar of the extreme itself
    price         the extreme (high for a crest, low for a trough)
    kind          "crest" / "trough"
    confirm_time  the bar that confirmed it, the first moment it is knowable. A
                  caller asking "as of T" must require confirm_time < T too
                  (`most_recent_pivot` does)
    bar           position of `time` in `data`
    confirm_bar   position of `confirm_time` in `data`

  Use `ZigZag` for a live, bar-by-bar feed and `most_recent_pivot` for the
  point-in-time lookup. Never write another zigzag.
  """
  cols = ["time", "price", "kind", "confirm_time", "bar", "confirm_bar"]
  if len(data) <= atr_period + 1:
    return pd.DataFrame(columns=cols)
  order = None
  if lower_data is not None:
    tf_min, lsp = _spacing_minutes(data.index), _spacing_minutes(lower_data.index)
    if tf_min is None or lsp is None or not lsp < tf_min - 1e-9:
      raise ValueError(f"lower_data ({lsp} min bars) must be finer than data ({tf_min} min bars)")
    order = intrabar_order(data.index, tf_min, lower_data)
  piv = zigzag_pivots(data["high"].to_numpy(float), data["low"].to_numpy(float),
                      _atr_series(data, atr_period), order, atr_mult, atr_period, min_bars)
  idx = data.index
  return pd.DataFrame({
    "time": idx[[p.i for p in piv]],
    "price": [p.price for p in piv],
    "kind": [p.kind for p in piv],
    "confirm_time": idx[[p.j for p in piv]],
    "bar": [p.i for p in piv],
    "confirm_bar": [p.j for p in piv],
  }, columns=cols)


def most_recent_pivot(pivots: pd.DataFrame, kind: str, cutoff, after=None):
  """(time, price) of the newest `kind` ("crest" / "trough") pivot of a
  `find_zigzag` frame that was both FORMED and CONFIRMED strictly before `cutoff`
  and, if `after` is given, formed strictly after it; (None, None) if none.

  Requiring confirm_time < cutoff as well as time < cutoff is what keeps this
  causal: a still-unconfirmed candidate is one the market has not yet proven was
  left behind, most obviously when the very move into `cutoff` would confirm it."""
  if pivots is None or pivots.empty:
    return None, None
  formed = _ns(pivots["time"])
  got = _newest(pivots["kind"].tolist(), formed, _ns(pivots["confirm_time"]),
                pivots["price"].tolist(), kind, _one_ns(cutoff),
                None if after is None else _one_ns(after))
  return (None, None) if got[0] is None else (pd.Timestamp(got[0], tz="UTC"), got[1])


def intrabar_order(idx, tf_min, lower):
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


def _newest(kinds, formed, confirmed, prices, kind, cutoff, after):
  """The last pivot of `kind` with formed < cutoff and confirmed < cutoff (and
  formed > after). Both time lists only ever rise, so the eligible pivots are a
  prefix and the answer is found walking back from its end."""
  k = min(bisect_left(formed, cutoff), bisect_left(confirmed, cutoff))
  while k > 0:
    k -= 1
    if after is not None and formed[k] <= after:
      break
    if kinds[k] == kind:
      return formed[k], prices[k]
  return None, None


def _ns(series):
  idx = pd.DatetimeIndex(series)
  idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
  return idx.as_unit("ns").asi8


def _one_ns(ts):
  ts = pd.Timestamp(ts)
  ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
  return ts.as_unit("ns").value
