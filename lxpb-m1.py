# Imports
import os
import pandas as pd

# From Imports
from itertools import zip_longest

# Configuration
N = 1


# -------------------------------------------------------------------- #
# Helper Functions
# -------------------------------------------------------------------- #

def load_ohlc_data(csv_path: str) -> pd.DataFrame:
  """
  Load and process a CSV file containing OHLC data.
  Return the OHLC data indexed by 'time' column as pd.Timestamp.
  """
  # Read the CSV
  ohlc = pd.read_csv(csv_path, usecols = [ "time", "open", "high", "low", "close" ])
  # Convert epoch timestamp into pd.Timestamp
  ohlc["time"] = pd.to_datetime(ohlc["time"], unit="s")
  # Set 'time' as index and reorder for safety
  return ohlc.set_index("time").sort_index()


# -------------------------------------------------------------------- #
# Analysis Functions
# -------------------------------------------------------------------- #

def lxpb_analysis(ohlc_h1: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
  """
  Perform a LxPB (LHPC & LLPB) analysis on OHLC H1 data.
  """
  # Storage
  touch_lv0 = []
  touch_lv1 = []
  retests   = []
  # For Each bar in H1 OHLC data
  for bar in ohlc_h1.itertuples():
    # 1. Check for retests of existing one-touch levels
    touch_lv_rem = []
    for lv in touch_lv1:
      # If bar overlaps with any Highs or Lows that come before it
      if bar.low <= lv["price"] <= bar.high:
        # If it is a valid retest
        if (bar.Index - lv["breakout_time"]) >= pd.Timedelta(hours=4):
          retests.append({ **lv, "retest_time": bar.Index })
      else:
        touch_lv_rem.append(lv)
    touch_lv1 = touch_lv_rem
    # 2. Check for breakouts of existing zero-touch levels
    touch_lv_rem = []
    for lv in touch_lv0:
      # If bar overlaps with any Highs or Lows that come before it
      if bar.low <= lv["price"] <= bar.high:
        # If it is a valid breakout
        if ((lv["type"] == "LHPB" and bar.open < lv["price"] < bar.close) or
            (lv["type"] == "LLPB" and bar.open > lv["price"] > bar.close)):
          touch_lv1.append({ **lv, "breakout_time": bar.Index })
      else:
        touch_lv_rem.append(lv)
    touch_lv0 = touch_lv_rem
    # 3. Add new zero-touch levels
    touch_lv0.append({ "type": "LHPB", "price": bar.high, "formation_time": bar.Index })
    touch_lv0.append({ "type": "LLPB", "price": bar.low , "formation_time": bar.Index })
  return pd.DataFrame(retests), pd.DataFrame(touch_lv1)


def find_m1_retest(lxpb: pd.DataFrame, ohlc_m1: pd.DataFrame, ohlc_h1: pd.DataFrame) -> pd.DataFrame:
  """
  Find the exact M1 bar where the retest occurred.
  Returns the M1 time and OHLC data for the retest bar.
  """
  # Storage
  retests = []
  # For each retested level
  for row in lxpb.itertuples(index=False):
    # For each bar in the related M1 timeframe
    for bar in ohlc_m1.loc[row.retest_time:row.retest_time + pd.Timedelta(minutes=59)].itertuples():
      if bar.low <= row.price <= bar.high:
        # FTA is lowest swing between breakout and retest for LHPB, highest swing high for LLPB.
        # Stop Loss is low of breakout for LHPB, high for LLPB.
        if row.type == "LHPB":
          fta       = ohlc_h1.loc[(ohlc_h1.index > row.breakout_time) & (ohlc_h1.index < row.retest_time), "low"].min()
          stop_loss = ohlc_h1.loc[row.breakout_time, "low"]
        if row.type == "LLPB":
          fta       = ohlc_h1.loc[(ohlc_h1.index > row.breakout_time) & (ohlc_h1.index < row.retest_time), "high"].max()
          stop_loss = ohlc_h1.loc[row.breakout_time, "high"]
        retests.append({
          **row._asdict(),
          "m1_retest_time": bar.Index,
          "m1_open"       : bar.open,
          "m1_high"       : bar.high,
          "m1_low"        : bar.low,
          "m1_close"      : bar.close,
          "fta"           : fta,
          "stop_loss"     : stop_loss
        })
        break
  return pd.DataFrame(retests)


def join_retest_data(es_data: pd.DataFrame, nq_data: pd.DataFrame, rty_data: pd.DataFrame) -> tuple[pd.DataFrame, list]:
  """
  Join ES, NQ, and RTY retest data based on matching m1_retest_time within a time window.
  """
  # Storage
  joined_retests = []
  es_unmatched   = []
  # For each ES retest
  for es_idx, es_row in es_data.iterrows():
    # Find NQ and RTY retests between ES retest time +- N minutes (5 by default)
    nq_window  = nq_data[ ( nq_data["type"] == es_row["type"]) &  nq_data["m1_retest_time"].between(es_row.m1_retest_time - pd.Timedelta(minutes=N), es_row.m1_retest_time + pd.Timedelta(minutes=N))]
    rty_window = rty_data[(rty_data["type"] == es_row["type"]) & rty_data["m1_retest_time"].between(es_row.m1_retest_time - pd.Timedelta(minutes=N), es_row.m1_retest_time + pd.Timedelta(minutes=N))]
    # All 3 must test a LHPB or LLPB level, if one is missing, mark and skip
    if nq_window.empty or rty_window.empty:
      es_unmatched.append(es_idx)
      continue
    # Iterate over the longer of NQ or RTY windows, fill the other with Nones
    for (_, nq_row), (_, rty_row) in zip_longest(nq_window.iterrows(), rty_window.iterrows(), fillvalue=(None,None)):
      joined_retest = { **{ f'es_{k}': v for k,v in es_row.items() } }
      if nq_row is not None:
        joined_retest.update({ **{ f'nq_{k}': v for k,v in nq_row.items() } })
      if rty_row is not None:
        joined_retest.update({ **{ f'rty_{k}': v for k,v in rty_row.items() } })
      joined_retests.append(joined_retest)
  return pd.DataFrame(joined_retests), es_unmatched


def simulate_trades(data: pd.DataFrame, m1: dict) -> pd.DataFrame:
  # Storage
  results = []
  # Find each group with unique "es_m1_retest_time"
  for timestamp, window in data.groupby("es_m1_retest_time"):
    latest = window[["es_m1_retest_time","nq_m1_retest_time","rty_m1_retest_time"]].max(axis=1).max()
    # Find the Symbol & Row to be simulated
    max_R = 0
    row_R = None
    pre_R = ""
    # For each symbol
    for symbol in m1.keys():
      # For each row
      for _,row in window.iterrows():
        # Calculate R Values
        reward = row[f'{symbol}_fta'] - m1[symbol].loc[latest, "close"]
        risk   = m1[symbol].loc[latest, "close"] - row[f'{symbol}_stop_loss']
        # Convert to scalar values if they are Series
        if isinstance(reward, pd.Series):
            reward = reward.iat[0] if len(reward) > 0 else 0
        if isinstance(risk, pd.Series):
            risk = risk.iat[0] if len(risk) > 0 else 0
        cur_R = (reward / risk) if risk > 0 else 0
        if cur_R > max_R:
          max_R = cur_R
          row_R = row
          pre_R = symbol
    # R should be equal to higher than 0.9
    if max_R < 0.9:
      continue
    # Initialize Trade Info
    trade_info = {
      "symbol":       pre_R,
      "type":         row_R[f'{pre_R}_type'],
      "entry_time":   latest,
      "entry_price":  m1[pre_R].loc[latest, "close"],
      "target_price": row_R[f'{pre_R}_fta'],
      "stop_price":   row_R[f'{pre_R}_stop_loss'],
      "exit_time":    None,
      "exit_price":   None,
      "pnl":          None,
      "mae":          None,
      "mfe":          None,
      "outcome":      None,
    }
    # Simulation
    for bar in m1[pre_R][m1[pre_R].index > latest].itertuples(index=True):
      # LHPB
      if trade_info["type"] == "LHPB":
        # Check for Target Price
        if bar.high >= trade_info["target_price"]:
          trade_info["exit_time"]  = bar.Index
          trade_info["exit_price"] = trade_info["target_price"]
          trade_info["outcome"]    = "WIN"
          break
        # Check for Stop Loss
        if bar.low  <= trade_info["stop_price"]:
          trade_info["exit_time"]  = bar.Index
          trade_info["exit_price"] = trade_info["stop_price"]
          trade_info["outcome"]    = "LOSS"
          break
      # LLPB
      if trade_info["type"] == "LLPB":
        # Check for Stop Loss
        if bar.high >= trade_info["stop_price"]:
          trade_info["exit_time"]  = bar.Index
          trade_info["exit_price"] = trade_info["stop_price"]
          trade_info["outcome"]    = "LOSS"
          break
        # Check for Target Price
        if bar.low  <= trade_info["target_price"]:
          trade_info["exit_time"]  = bar.Index
          trade_info["exit_price"] = trade_info["target_price"]
          trade_info["outcome"]    = "WIN"
          break
    # If trade was still open at the end of data
    if trade_info["outcome"] is None:
      trade_info["exit_time"]  = m1[pre_R].index.max()
      trade_info["exit_price"] = m1[pre_R].loc[trade_info["exit_time"], "close"]
      trade_info["outcome"]    = "OPEN"
    # PnL
    trade_info["pnl"] = trade_info["exit_price"] - trade_info["entry_price"]
    # Related M1 timeframe
    m1_timeframe = m1[pre_R].loc[trade_info["entry_time"]:trade_info["exit_time"]]
    # MAE / MFE
    if trade_info["type"] == "LHPB":
      trade_info["mfe"] = m1_timeframe["high"].max() - trade_info["entry_price"]
      trade_info["mae"] = trade_info["entry_price"] - m1_timeframe["low"].min()
    if trade_info["type"] == "LLPB":
      trade_info["mfe"] = trade_info["entry_price"] - m1_timeframe["low"].min()
      trade_info["mae"] = m1_timeframe["high"].max() -trade_info["entry_price"]
      # Fix PnL
      trade_info["pnl"] *= -1
    # Add the current trade into the results
    results.append(trade_info)
  return pd.DataFrame(results).drop_duplicates(subset=["symbol", "entry_time", "target_price", "stop_price"])


# -------------------------------------------------------------------- #
# Main Function
# -------------------------------------------------------------------- #

if __name__ == "__main__":

  # Output Directory
  # output_dir = f'LxPB_{pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")}' # TODO
  output_dir = "test"
  data_dir = "dataTest"
  # Create the Output Directory if it doesn't exist
  os.makedirs(output_dir, exist_ok=True)

  # Load H1 OHLC Data
  es_h1  = load_ohlc_data(os.path.join(data_dir, "es-h1.csv"))
  nq_h1  = load_ohlc_data(os.path.join(data_dir, "nq-h1.csv"))
  rty_h1 = load_ohlc_data(os.path.join(data_dir, "rty-h1.csv"))

  # Load M1 OHLC Data
  es_m1  = load_ohlc_data(os.path.join(data_dir, "es-m1.csv"))
  nq_m1  = load_ohlc_data(os.path.join(data_dir, "nq-m1.csv"))
  rty_m1 = load_ohlc_data(os.path.join(data_dir, "rty-m1.csv"))

  # LxPB Analysis
  es_lxpb , es_lxpb_naked  = lxpb_analysis(es_h1)
  nq_lxpb , nq_lxpb_naked  = lxpb_analysis(nq_h1)
  rty_lxpb, rty_lxpb_naked = lxpb_analysis(rty_h1)

  # Output LxPB Data
  es_lxpb.to_csv( os.path.join(output_dir, "ES_LxPB.csv") , index=False, float_format="%.2f")
  nq_lxpb.to_csv( os.path.join(output_dir, "NQ_LxPB.csv") , index=False, float_format="%.2f")
  rty_lxpb.to_csv(os.path.join(output_dir, "RTY_LxPB.csv"), index=False, float_format="%.2f")

  # Output LxPB Data
  es_lxpb_naked.to_csv( os.path.join(output_dir, "ES_LxPB_Naked.csv") , index=False, float_format="%.2f")
  nq_lxpb_naked.to_csv( os.path.join(output_dir, "NQ_LxPB_Naked.csv") , index=False, float_format="%.2f")
  rty_lxpb_naked.to_csv(os.path.join(output_dir, "RTY_LxPB_Naked.csv"), index=False, float_format="%.2f")

  # Retest in M1 timeframe
  es_retest  = find_m1_retest(es_lxpb , es_m1 , es_h1)
  nq_retest  = find_m1_retest(nq_lxpb , nq_m1 , nq_h1)
  rty_retest = find_m1_retest(rty_lxpb, rty_m1, rty_h1)

  # Output Retest Data
  es_retest.to_csv( os.path.join(output_dir, "ES_M1_Retest.csv") , index=False, float_format="%.2f")
  nq_retest.to_csv( os.path.join(output_dir, "NQ_M1_Retest.csv") , index=False, float_format="%.2f")
  rty_retest.to_csv(os.path.join(output_dir, "RTY_M1_Retest.csv"), index=False, float_format="%.2f")

  # Join Retest Data and Output
  joined_retest, es_unmatched = join_retest_data(es_retest, nq_retest, rty_retest)
  joined_retest.to_csv(os.path.join(output_dir, "Joined_M1_Retests.csv"), index=False, float_format="%.2f")

  # Print Statistics
  print("\n\n# Retest Statistics")
  print("-"*40)
  print(f'Total ES  retests       : {len(es_retest)}')
  print(f'TotaL NQ  retests       : {len(nq_retest)}')
  print(f'TotaL RTY retests       : {len(rty_retest)}')
  print(f'Joined ES-NQ-RTY retests: {len(joined_retest)}')
  print(f'Unmatched ES retests    : {len(es_unmatched)}')
  print(f'Match rate:             : {(1 - len(es_unmatched) / len(es_retest)) * 100:.2f}%')

  # Simulate Trades & Output
  trades = simulate_trades(joined_retest, { "es": es_m1, "nq": nq_m1, "rty": rty_m1 })
  trades.to_csv(os.path.join(output_dir, "Simulated_Trades.csv"), index=False, float_format="%.2f")

  # Append Statistics to the output file
  print("\n\n# Statistics")
  # Print a divider line of 40 dashes to separate sections in the output
  print("-"*40)

  print(f'Win       : {(len(trades[trades["outcome"] == "WIN"])  / len(trades)) * 100:6.2f}%')
  print(f'Loss      : {(len(trades[trades["outcome"] == "LOSS"]) / len(trades)) * 100:6.2f}%')
  print(f'Total PnL : {(trades["pnl"] / (trades["entry_price"] - trades["stop_price"]).abs()).sum():.2f}')
  print("\n")

# ------------------------------------------------------------------- #
