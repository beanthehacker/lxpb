from typing import Optional

import pandas as pd

from find_LP import find_LP
from find_eqh_eql import find_eqh_eql
from find_inside_bar import find_inside_bar


def _untouched_after(data_after: pd.DataFrame, level: float, side: str) -> bool:
  if data_after.empty:
    return True
  if side == "high":
    return not (data_after["high"] >= level).any()
  return not (data_after["low"] <= level).any()


def find_targets(
  data_d1: pd.DataFrame,
  data_w1: pd.DataFrame,
  direction: str,
  boxed_range: float,
  atr: float,
  as_of: Optional[pd.Timestamp] = None,
  symbol: str = "",
) -> pd.DataFrame:
  """
  Umbrella detector — currently-active price targets at a point in time.

  Sources (long shown; short mirrors to the low side):
    - LPH/LPL   : unswept LP cluster above (long) / below (short) current price
    - EQH/EQL   : equal-highs/equal-lows cluster anchored by the latest bar
    - IDH/IDL   : inside-day high/low in the last 3 D1 candles, untouched since
                  formation; always included even when the latest close sits
                  exactly on that extreme (close == low / close == high)
    - IWH/IWL   : inside-week high/low in the last 3 W1 candles, untouched since formation
    - WO        : current week's open, not revisited after the first day's close
  Targets are then filtered to those within 2*ATR of the latest D1 close.

  Output column `constituents` is a list of (timestamp, price) tuples — every
  member of the cluster for LP/EQ sources, single-element [(ts, price)] for the
  rest.
  """
  assert direction in ("long", "short")

  cols = ["price", "source", "direction", "constituents"]
  empty = pd.DataFrame(columns=cols).astype({"price": "float64"})

  if data_d1.empty or data_w1.empty:
    return empty

  if as_of is None:
    as_of = data_d1.index.max()

  d1 = data_d1.loc[:as_of]
  w1 = data_w1.loc[:as_of]
  if d1.empty or w1.empty:
    return empty

  close = float(d1.iloc[-1]["close"])
  is_long = direction == "long"
  side = "high" if is_long else "low"
  on_side = (lambda p: p > close) if is_long else (lambda p: p < close)
  # Inclusive variant for inside-day targets. When a bar closes exactly on its
  # extreme, IDH == close (long) or IDL == close (short); the strict on_side
  # test would drop it, but an inside-day extreme sitting at the close is still
  # a valid forward target. ID targets are always included regardless of the
  # close touching the low/high.
  on_side_incl = (lambda p: p >= close) if is_long else (lambda p: p <= close)

  rows = []  # (timestamp, price, source, constituents)

  # 1. LP — find_LP already enforces unswept-since-cluster on the sliced frame
  lp_label = "LPH" if is_long else "LPL"
  if boxed_range > 0:
    lp = find_LP(d1, boxed_range)
    if not lp.empty:
      want = lp[lp["isHigh"] == is_long]
      for ts, r in want.iterrows():
        price = float(r["high"] if is_long else r["low"])
        if on_side(price):
          consts = list(zip(r["constituent_timestamps"], r["constituent_prices"]))
          rows.append((ts, price, lp_label, consts))

  # 2. EQH/EQL — cluster of raw bar highs/lows anchored by latest bar
  eq_label = "EQH" if is_long else "EQL"
  if boxed_range > 0:
    eq = find_eqh_eql(d1, boxed_range)
    if not eq.empty:
      want = eq[eq["isHigh"] == is_long]
      for ts, r in want.iterrows():
        price = float(r["high"] if is_long else r["low"])
        if on_side(price):
          consts = list(zip(r["constituent_timestamps"], r["constituent_prices"]))
          rows.append((ts, price, eq_label, consts))

  # 3. Inside Day — IDH (long) / IDL (short) in last 3 D1 candles, untouched since formation
  id_label = "IDH" if is_long else "IDL"
  ib_d = find_inside_bar(d1)
  if not ib_d.empty:
    cutoff = d1.index[-min(3, len(d1))]
    for ts, r in ib_d.loc[ib_d.index >= cutoff].iterrows():
      price = float(r[side])
      if not on_side_incl(price):
        continue
      if _untouched_after(d1.loc[d1.index > ts], price, side):
        rows.append((ts, price, id_label, [(ts, price)]))

  # 4. Inside Week — IWH (long) / IWL (short) in last 3 W1 candles, untouched since formation.
  # Checks both W1 and D1 data: calculateD1W1 drops the partial current-week W1 bar, so
  # D1-level visits within the current week would be invisible to the W1-only check.
  iw_label = "IWH" if is_long else "IWL"
  ib_w = find_inside_bar(w1)
  if not ib_w.empty:
    cutoff = w1.index[-min(3, len(w1))]
    for ts, r in ib_w.loc[ib_w.index >= cutoff].iterrows():
      price = float(r[side])
      if not on_side(price):
        continue
      if (_untouched_after(w1.loc[w1.index > ts], price, side) and
          _untouched_after(d1.loc[d1.index > ts],  price, side)):
        rows.append((ts, price, iw_label, [(ts, price)]))

  # 5. Weekly Open — enabled only for EURUSD; too noisy on futures/crypto.
  # Derive from D1 (not w1.iloc[-1]) because calculateD1W1 drops the incomplete
  # current-week W1 bar on Mondays/early-week runs, leaving w1.iloc[-1] pointing
  # at last week's Monday.
  if symbol.upper() == "EURUSD":
    as_of_date = d1.index[-1].normalize()
    week_start = as_of_date - pd.Timedelta(days=as_of_date.weekday())  # Monday of current week
    current_week_d1 = d1.loc[d1.index >= week_start]
    if not current_week_d1.empty:
      wo_ts = current_week_d1.index[0]
      wo = float(current_week_d1.iloc[0]["open"])
      after_first_day = d1.loc[d1.index > wo_ts]
      if on_side(wo) and _untouched_after(after_first_day, wo, side):
        rows.append((wo_ts, wo, "WO", [(wo_ts, wo)]))

  # ATR distance filter: target must be within 2*ATR of latest close
  if atr > 0:
    rows = [(ts, p, src, cs) for (ts, p, src, cs) in rows if abs(p - close) <= 2.0 * atr]

  if not rows:
    return empty

  out = pd.DataFrame(rows, columns=["__ts", "price", "source", "constituents"])
  out["direction"] = direction
  out = out.set_index("__ts").sort_index(ascending=False)
  out.index.name = None
  return out[cols]
