import pandas as pd

from find_swings import is_local_swing_high, is_local_swing_low


def find_eqh_eql(data: pd.DataFrame, boxed_range: float) -> pd.DataFrame:
  """
  Equal Highs / Equal Lows.

  Cluster of >=2 nearby highs (or lows) within `boxed_range` of one another,
  anchored around the latest bar's high (or low). Sibling of find_LP, but uses
  raw bar highs/lows instead of ZigZag swings as the candidate pool — which
  is why EQH/EQL needs the explicit intermediary-sweep check below (ZigZag
  swings provide that guarantee structurally; raw bar highs/lows do not).

  Algorithm (mirrors find_LP):
    1. Candidates: bars within `boxed_range` of the latest depth — i.e.
       `high >= latest_high - boxed_range` (or `low <= latest_low + boxed_range`).
       The boxed_range tolerance matches the cluster tolerance — without it,
       a bar whose high is 1 tick below the latest high gets filtered out
       even though it would be inside the cluster.
    2. Greedy cluster: pick the most extreme remaining candidate, gather all
       candidates within `boxed_range` of it, require >=2 members.
    3. Local-swing anchor requirement: at least one cluster member must be a
       genuine local pivot — `is_local_swing_high` for EQH, `is_local_swing_low`
       for EQL (both from find_swings.py; strict 3-bar fractal, no ATR
       threshold). Without this, a run of raw bars on the shoulder of a single
       directional move — e.g. three strictly descending lows within
       `boxed_range` of one another — would cluster as "equal lows" even
       though price never actually bounced off any of them.
    4. Intermediary-sweep invalidation (between cluster members): no bar
       between the earliest and latest member may have wicked beyond the
       cluster zone (`extreme_val + boxed_range` for highs, `- boxed_range`
       for lows). Without this, two raw-bar highs separated by a deep
       breakout-and-return get clustered even though the level was broken
       in the middle.
    5. Subsequent-sweep invalidation: no later bar (after the last cluster
       member) may have wicked beyond the cluster zone either. Same rule,
       just the tail end.
    6. Emit one row per surviving cluster, then continue with candidates
       past the last cluster member.

  Returns one row per cluster (sorted newest-first):
    - isHigh                  bool
    - constituent_prices      list of the highs/lows in the cluster
    - constituent_timestamps  list of the bar timestamps in the cluster

  Rows accumulate in a plain list (not `result.loc[extreme_idx] = bar`
  insertion) and are assembled into a DataFrame at the end. An EQH cluster
  and an EQL cluster can legitimately share the same anchor timestamp
  (extreme_idx) — inserting both via `.loc[label] = ...` would silently
  overwrite one with the other since they'd collide on the same index
  label. Accumulating first avoids that data loss.
  """
  empty = pd.DataFrame(
    columns=data.columns.tolist() + ["isHigh", "constituent_prices", "constituent_timestamps"]
  ).astype({"isHigh": "bool"})

  if data.empty:
    return empty

  latest_high = data.at[data.index.max(), "high"]
  latest_low  = data.at[data.index.max(), "low"]

  records: list[pd.Series] = []

  # ── EQH ─────────────────────────────────────────────────────────────────
  candidates = data.loc[data["high"] >= latest_high - boxed_range].copy()
  while not candidates.empty:
    extreme_idx = candidates["high"].idxmax()
    extreme_val = candidates.at[extreme_idx, "high"]
    in_range    = candidates.loc[candidates["high"] >= extreme_val - boxed_range]
    if len(in_range) >= 2:
      zone_top      = extreme_val + boxed_range
      cluster_start = in_range.index.min()
      cluster_end   = in_range.index.max()

      # Intermediary check: between earliest and latest cluster member, no
      # bar may have wicked above the zone. Cluster members themselves can't
      # break above zone_top by construction (their highs <= extreme_val).
      intermediary = data.loc[(data.index > cluster_start) & (data.index < cluster_end)]
      intermediary_break = (not intermediary.empty) and (intermediary["high"] > zone_top).any()

      # Subsequent check: after last cluster member, no bar may have wicked
      # above the zone either.
      subsequent = data.loc[data.index > cluster_end]
      subsequent_break = (not subsequent.empty) and (subsequent["high"] > zone_top).any()

      # Local-swing anchor: at least one member must be a genuine 3-bar
      # pivot high, not just a raw bar caught by the boxed_range tolerance.
      has_swing_anchor = any(
        is_local_swing_high(data, data.index.get_loc(ts)) for ts in in_range.index
      )

      if not intermediary_break and not subsequent_break and has_swing_anchor:
        bar = candidates.loc[extreme_idx].copy()
        bar["isHigh"] = True
        bar["constituent_prices"]     = in_range["high"].tolist()
        bar["constituent_timestamps"] = in_range.index.tolist()
        records.append(bar)
    candidates = candidates.loc[candidates.index > in_range.index.max()]

  # ── EQL ─────────────────────────────────────────────────────────────────
  candidates = data.loc[data["low"] <= latest_low + boxed_range].copy()
  while not candidates.empty:
    extreme_idx = candidates["low"].idxmin()
    extreme_val = candidates.at[extreme_idx, "low"]
    in_range    = candidates.loc[candidates["low"] <= extreme_val + boxed_range]
    if len(in_range) >= 2:
      zone_bottom   = extreme_val - boxed_range
      cluster_start = in_range.index.min()
      cluster_end   = in_range.index.max()

      intermediary = data.loc[(data.index > cluster_start) & (data.index < cluster_end)]
      intermediary_break = (not intermediary.empty) and (intermediary["low"] < zone_bottom).any()

      subsequent = data.loc[data.index > cluster_end]
      subsequent_break = (not subsequent.empty) and (subsequent["low"] < zone_bottom).any()

      # Local-swing anchor: at least one member must be a genuine 3-bar
      # pivot low, not just a raw bar caught by the boxed_range tolerance.
      has_swing_anchor = any(
        is_local_swing_low(data, data.index.get_loc(ts)) for ts in in_range.index
      )

      if not intermediary_break and not subsequent_break and has_swing_anchor:
        bar = candidates.loc[extreme_idx].copy()
        bar["isHigh"] = False
        bar["constituent_prices"]     = in_range["low"].tolist()
        bar["constituent_timestamps"] = in_range.index.tolist()
        records.append(bar)
    candidates = candidates.loc[candidates.index > in_range.index.max()]

  if not records:
    return empty

  result = pd.DataFrame(records).astype({"isHigh": "bool"})
  return result.sort_index(ascending=False, kind="stable")
