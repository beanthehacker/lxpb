import pandas as pd

'''
Real-World Example

Available Swing Highs:
- 4235.00 @ 2025-01-10
- 4232.50 @ 2025-01-12
- 4230.75 @ 2025-01-14
- 4220.00 @ 2025-01-08
- 4215.50 @ 2025-01-06

Boxed_range = 5 points:

Algorithm Execution:
Iteration 1:
Highest point: 4235.00
Range: [4230.00, 4235.00]
Points in range: 4235.00, 4232.50, 4230.75 (3 points)
✅ Creates LP High at 4235.00 with constituents [4235.00, 4232.50, 4230.75]
Removes: 4235.00, 4232.50, 4230.75
Iteration 2:
Remaining points: 4220.00, 4215.50
Highest point: 4220.00
Range: [4215.00, 4220.00]
Points in range: 4220.00, 4215.50 (2 points)
✅ Creates LP High at 4220.00 with constituents [4220.00, 4215.50]
Result: Two LP Highs identified at 4235.00 and 4220.00
'''

def find_LP(data: pd.DataFrame, boxed_range: float) -> pd.DataFrame:
  """
  Liquidity Pool: cluster of >=2 ZigZag swing highs (or lows) within
  `boxed_range` of one another, where the cluster zone hasn't been swept
  by a later bar.

  Algorithm:
    1. Candidate pool: swing highs `> latest_high - boxed_range` (or swing
       lows `< latest_low + boxed_range`). The boxed_range tolerance
       matches the cluster tolerance, so a swing barely below the latest
       depth (within the cluster band) is still eligible.
    2. Greedy cluster: pick the most extreme remaining swing, gather all
       swings within `boxed_range` of it, require >=2 members.
    3. Subsequent-sweep invalidation: after the last cluster member, no
       later bar may have wicked beyond the cluster zone
       (`cluster_extreme + boxed_range` for highs, `- boxed_range` for
       lows). A wick that stays inside the zone counts as a re-test of the
       same level, not a sweep.

  No intermediary-sweep check is needed: ZigZag swing alternation
  structurally guarantees the level wasn't broken between cluster members
  (a break would have produced a new swing extreme outside the cluster).
  This is the key difference from find_eqh_eql, which uses raw bar
  highs/lows and therefore does need the explicit intermediary check.
  """
  lp = pd.DataFrame(columns=data.columns.tolist() + ["isHigh", "constituent_prices", "constituent_timestamps"]).astype({ "isHigh": "bool" })
  latest_high = data.at[data.index.max(), "high"]
  latest_low  = data.at[data.index.max(), "low"]
  from find_swings import find_swings
  sh, sl = find_swings(data)
  swing_highs = sh.loc[sh["high"] > latest_high - boxed_range]
  swing_lows  = sl.loc[sl["low"]  < latest_low  + boxed_range]

  # ── LP HIGH ──────────────────────────────────────────────────────────
  while not swing_highs.empty:
    swing_extreme_idx = swing_highs["high"].idxmax()
    swing_range = swing_highs.loc[swing_highs["high"] >= (swing_highs.at[swing_extreme_idx, "high"] - boxed_range)]
    if len(swing_range) >= 2:
      cluster_extreme = swing_highs.at[swing_extreme_idx, "high"]
      zone_top        = cluster_extreme + boxed_range
      subsequent      = data.loc[data.index > swing_range.index.max()]
      if subsequent.empty or not (subsequent["high"] > zone_top).any():
        lp_bar = swing_highs.loc[swing_extreme_idx]
        lp_bar["isHigh"] = True
        lp_bar["constituent_prices"] = swing_range["high"].tolist()
        lp_bar["constituent_timestamps"] = swing_range.index.tolist()
        lp.loc[swing_extreme_idx] = lp_bar
    swing_highs = swing_highs.loc[swing_highs.index > swing_range.index.max()]

  # ── LP LOW ───────────────────────────────────────────────────────────
  while not swing_lows.empty:
    swing_extreme_idx = swing_lows["low"].idxmin()
    swing_range = swing_lows.loc[swing_lows["low"] <= (swing_lows.at[swing_extreme_idx, "low"] + boxed_range)]
    if len(swing_range) >= 2:
      cluster_extreme = swing_lows.at[swing_extreme_idx, "low"]
      zone_bottom     = cluster_extreme - boxed_range
      subsequent      = data.loc[data.index > swing_range.index.max()]
      if subsequent.empty or not (subsequent["low"] < zone_bottom).any():
        lp_bar = swing_lows.loc[swing_extreme_idx]
        lp_bar["isHigh"] = False
        lp_bar["constituent_prices"] = swing_range["low"].tolist()
        lp_bar["constituent_timestamps"] = swing_range.index.tolist()
        lp.loc[swing_extreme_idx] = lp_bar
    swing_lows = swing_lows.loc[swing_lows.index > swing_range.index.max()]

  return lp.sort_index(ascending=False)
