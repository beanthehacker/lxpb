#"Last High Pre Breakout" (LHPB) and "Last Low Pre Breakout" (LLPB) trading strategy.
#"First Trouble Area" is FTA
import pandas as pd
from datetime import datetime, time, timedelta
import multiprocessing as mp
from itertools import product
import os
import sys

def is_shootingstar(row):
    # Extract OHLC values from the row
    o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
    
    # Calculate component parts of the candlestick
    body_size = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_length = h - l
    
    # Skip if bar is too small
    if total_length == 0:
        return False
        
    # Apply the shooting star criteria
    body_to_wick_ratio = 0.3  # This can be adjusted as needed
    small_body = body_size <= (total_length * body_to_wick_ratio)
    long_upper_wick = upper_wick > (total_length * 0.5)
    small_lower_wick = lower_wick < (total_length * 0.2)
    
    # Return true if all conditions are met
    return small_body and long_upper_wick and small_lower_wick

def is_hammer(row):
    # Extract OHLC values from the row
    o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
    
    # Calculate component parts of the candlestick
    body_size = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    total_length = h - l
    
    # Skip if bar is too small
    if total_length == 0:
        return False
        
    # Apply the hammer criteria
    body_to_wick_ratio = 0.3  # This can be adjusted as needed
    small_body = body_size <= (total_length * body_to_wick_ratio)
    long_lower_wick = lower_wick > (total_length * 0.5)
    small_upper_wick = upper_wick < (total_length * 0.2)
    
    # Return true if all conditions are met
    return small_body and long_lower_wick and small_upper_wick

def is_swing_high(df, index, lookback=1):
    """
    Check if the current bar forms a swing high.
    A swing high occurs when the current bar's high is higher than the 
    previous bar's high and the next bar's high.
    """
    if index < lookback or index >= len(df) - lookback:
        return False
    
    current_high = df.iloc[index]['High']
    
    # Check if current high is higher than previous and next bar's high
    prev_high = df.iloc[index-lookback]['High']
    next_high = df.iloc[index+lookback]['High']
    
    return current_high >= prev_high and current_high >= next_high

def is_swing_low(df, index, lookback=1):
    """
    Check if the current bar forms a swing low.
    A swing low occurs when the current bar's low is lower than the 
    previous bar's low and the next bar's low.
    """
    if index < lookback or index >= len(df) - lookback:
        return False
    
    current_low = df.iloc[index]['Low']
    
    # Check if current low is lower than previous and next bar's low
    prev_low = df.iloc[index-lookback]['Low']
    next_low = df.iloc[index+lookback]['Low']
    
    return current_low <= prev_low and current_low <= next_low

def find_d1_levels_simple(df_d1, days_before_trade=2):
    """
    Find D1 levels using only the 1-touch criteria without requiring swing/spike patterns.
    - Level is identified by price and formation time
    - Level becomes one-touch (valid) when it has a breakout
    - Level is removed after second touch 
    - If second touch is a valid retest and waiting period satisfied, take trade
    """
    results = []  # Format: [{price, formation_time, breakout_time, type}, ...]
    zero_touch_levels = []  # Format: [(price, formation_time, is_high), ...]
    one_touch_levels = []  # Format: [(price, formation_time, breakout_time, is_high), ...]
    
    for i in range(len(df_d1)):
        row = df_d1.iloc[i]
        current_time = df_d1.index[i]
        
        # Process existing zero-touch levels
        remaining_zero_touch = []
        for level in zero_touch_levels:
            price, formation_time, is_high = level
                
            if row['Low'] <= price <= row['High']:
                # Check breakout conditions based on level type
                is_breakout = False
                if is_high:  # LHPB
                    is_breakout = row['Open'] < price and row['Close'] > price
                else:  # LLPB
                    is_breakout = row['Open'] > price and row['Close'] < price
                
                if is_breakout:
                    # Convert to 1-touch level with both formation and breakout time
                    one_touch_levels.append((price, formation_time, current_time, is_high))
            else:
                remaining_zero_touch.append(level)
        
        zero_touch_levels = remaining_zero_touch
        
        # Process existing one-touch levels
        remaining_one_touch = []
        for level in one_touch_levels:
            price, formation_time, breakout_time, is_high = level
                
            # Check if current bar overlaps with the level (second touch)
            if row['Low'] <= price <= row['High']:
                # Check if enough time has passed since breakout
                days_since_breakout = (current_time - breakout_time).days
                
                # Check if this is a valid retest based on direction
                is_valid_retest = False
                if is_high:  # LHPB - must retest from above
                    is_valid_retest = row['Open'] > price and row['Close'] <= price
                else:  # LLPB - must retest from below
                    is_valid_retest = row['Open'] < price and row['Close'] >= price
                
                # If valid retest and enough time has passed, add to results
                if is_valid_retest and days_since_breakout >= days_before_trade:
                    results.append({
                        'price': price,
                        'formation_time': formation_time,
                        'breakout_time': breakout_time,
                        'retest_time': current_time,
                        'type': 'LHPB' if is_high else 'LLPB'
                    })
                
                # Do not keep the level after second touch (whether valid retest or not)
            else:
                # Keep the level if not touched
                remaining_one_touch.append(level)
        
        one_touch_levels = remaining_one_touch
        
        # Add new zero-touch levels from current bar
        zero_touch_levels.append((row['High'], current_time, True))  # High level (LHPB)
        zero_touch_levels.append((row['Low'], current_time, False))  # Low level (LLPB)
    
    return results

def find_h1_levels_with_classification(df_h1, hours_before_trade=4):
    """
    Find H1 levels and classify them as spike, swing, or basic.
    - Level is identified by price and formation time
    - Level becomes one-touch (valid) when it has a breakout
    - Level is removed after second touch 
    - If second touch is a valid retest and waiting period satisfied, take trade
    """
    results = []  # Format: [{price, formation_time, breakout_time, type, is_spike, is_swing}, ...]
    zero_touch_levels = []  # Format: [(price, formation_time, is_high), ...]
    one_touch_levels = []  # Format: [(price, formation_time, breakout_time, is_high, is_spike, is_swing), ...]
    
    for i in range(len(df_h1)):
        row = df_h1.iloc[i]
        current_time = df_h1.index[i]
        
        # Process existing zero-touch levels
        remaining_zero_touch = []
        for level in zero_touch_levels:
            price, formation_time, is_high = level
            formation_idx = df_h1.index.get_loc(formation_time)
            
            if row['Low'] <= price <= row['High']:
                # Check breakout conditions based on level type
                is_breakout = False
                if is_high:  # LHPB
                    is_breakout = row['Open'] < price and row['Close'] > price
                else:  # LLPB
                    is_breakout = row['Open'] > price and row['Close'] < price
                
                if is_breakout:
                    # Classify the level
                    is_spike = (is_shootingstar(df_h1.iloc[formation_idx]) if not is_high 
                              else is_hammer(df_h1.iloc[formation_idx]))
                    is_swing = (is_swing_low(df_h1, formation_idx) if not is_high
                              else is_swing_high(df_h1, formation_idx))
                    
                    # Convert to 1-touch level with both formation and breakout time
                    one_touch_levels.append((price, formation_time, current_time, is_high, is_spike, is_swing))
            else:
                remaining_zero_touch.append(level)
        
        zero_touch_levels = remaining_zero_touch
        
        # Process existing one-touch levels
        remaining_one_touch = []
        for level in one_touch_levels:
            price, formation_time, breakout_time, is_high, is_spike, is_swing = level
                
            # Check if current bar overlaps with the level (second touch)
            if row['Low'] <= price <= row['High']:
                # Check if enough time has passed since breakout
                hours_since_breakout = (current_time - breakout_time).total_seconds() / 3600
                
                # Check if this is a valid retest based on direction
                is_valid_retest = False
                if is_high:  # LHPB - must retest from above
                    is_valid_retest = row['Open'] > price and row['Close'] <= price
                else:  # LLPB - must retest from below
                    is_valid_retest = row['Open'] < price and row['Close'] >= price
                
                # If valid retest and enough time has passed, add to results
                if is_valid_retest and hours_since_breakout >= hours_before_trade:
                    results.append({
                        'price': price,
                        'formation_time': formation_time,
                        'breakout_time': breakout_time,
                        'retest_time': current_time,
                        'type': 'LHPB' if is_high else 'LLPB',
                        'is_spike': is_spike,
                        'is_swing': is_swing
                    })
                
                # Do not keep the level after second touch (whether valid retest or not)
            else:
                # Keep the level if not touched
                remaining_one_touch.append(level)
        
        one_touch_levels = remaining_one_touch
        
        # Add new zero-touch levels from current bar
        zero_touch_levels.append((row['High'], current_time, True))  # High level (LHPB)
        zero_touch_levels.append((row['Low'], current_time, False))  # Low level (LLPB)
    
    return results

def match_d1_h1_levels(d1_level, h1_levels, max_distance=10):
    """
    Find the best matching H1 level for a D1 level based on the specified criteria.
    """
    matching_levels = []
    
    # Filter H1 levels within distance
    for h1_level in h1_levels:
        distance = abs(d1_level['price'] - h1_level['price'])
        if distance <= max_distance:
            h1_level_copy = h1_level.copy()
            h1_level_copy['distance_to_d1'] = distance
            matching_levels.append(h1_level_copy)
    
    if not matching_levels:
        return None
        
    # First priority: Spike levels
    spike_levels = [level for level in matching_levels if level['is_spike']]
    if spike_levels:
        return min(spike_levels, key=lambda x: x['distance_to_d1'])
    
    # Second priority: Swing levels
    swing_levels = [level for level in matching_levels if level['is_swing']]
    if swing_levels:
        return min(swing_levels, key=lambda x: x['distance_to_d1'])
    
    # Last priority: Closest level
    return min(matching_levels, key=lambda x: x['distance_to_d1'])

def create_d1_h1_relationships(d1_levels, h1_levels, max_distance=10):
    """
    Create relationships between D1 and H1 levels.
    Returns:
    - d1_to_h1: Dict mapping D1 level index to list of H1 level indices
    - h1_to_d1: Dict mapping H1 level index to list of D1 level indices
    """
    d1_to_h1 = {}  # D1 level idx -> list of H1 level indices
    h1_to_d1 = {}  # H1 level idx -> list of D1 level indices
    
    # Initialize empty lists for each level
    for d1_idx in range(len(d1_levels)):
        d1_to_h1[d1_idx] = []
    for h1_idx in range(len(h1_levels)):
        h1_to_d1[h1_idx] = []
    
    # Create relationships
    for d1_idx, d1_level in enumerate(d1_levels):
        d1_price = d1_level['price']
        
        for h1_idx, h1_level in enumerate(h1_levels):
            h1_price = h1_level['price']
            
            # Check if within distance
            if abs(d1_price - h1_price) <= max_distance:
                d1_to_h1[d1_idx].append(h1_idx)
                h1_to_d1[h1_idx].append(d1_idx)
    
    return d1_to_h1, h1_to_d1

def find_best_h1_level(h1_levels, available_h1_indices):
    """
    Find the best H1 level from the available indices based on priority:
    1. Spike levels (closest if multiple)
    2. Swing levels (closest if multiple)
    3. Basic levels (closest)
    """
    if not available_h1_indices:
        return None
        
    # Filter to only available levels
    candidate_levels = [(idx, h1_levels[idx]) for idx in available_h1_indices]
    
    # First priority: Spike levels
    spike_levels = [(idx, level) for idx, level in candidate_levels if level['is_spike']]
    if spike_levels:
        return min(spike_levels, key=lambda x: abs(x[1]['price']))[0]
    
    # Second priority: Swing levels
    swing_levels = [(idx, level) for idx, level in candidate_levels if level['is_swing']]
    if swing_levels:
        return min(swing_levels, key=lambda x: abs(x[1]['price']))[0]
    
    # Last priority: Closest level
    return min(candidate_levels, key=lambda x: abs(x[1]['price']))[0]

def simulate_trades_revised(df_h1, df_m5, d1_levels, h1_levels, max_distance=10, fixed_stop_distance=5, fixed_target_distance=10, contract_multiplier=1):
    """
    Simulate trades based on D1 and H1 level confluence, maintaining relationships between levels.
    Each D1 level is only used once for a trade.
    """
    trades = []
    
    # Create D1-H1 relationships
    d1_to_h1, h1_to_d1 = create_d1_h1_relationships(d1_levels, h1_levels, max_distance)
    
    # Track used D1 levels
    used_d1_indices = set()
    
    # Track available H1 levels (all initially available)
    available_h1_indices = set(range(len(h1_levels)))
    
    # Sort D1 levels by formation time to process them in chronological order
    sorted_d1_indices = sorted(range(len(d1_levels)), 
                              key=lambda i: d1_levels[i]['formation_time'])
    
    for d1_idx in sorted_d1_indices:
        if d1_idx in used_d1_indices:
            continue
            
        # Get available H1 levels for this D1 level
        h1_candidates = set(d1_to_h1[d1_idx]) & available_h1_indices
        
        # Find best available H1 level
        best_h1_idx = find_best_h1_level(h1_levels, h1_candidates)
        
        if best_h1_idx is not None:
            best_h1_level = h1_levels[best_h1_idx]
            
            # Mark D1 level as used
            used_d1_indices.add(d1_idx)
            
            # Remove H1 levels that are ONLY associated with this D1 level
            for h1_idx in d1_to_h1[d1_idx]:
                if set(h1_to_d1[h1_idx]) <= {d1_idx}:  # If all associated D1 levels are used
                    available_h1_indices.discard(h1_idx)
            
            # Set up trade parameters
            entry_price = best_h1_level['price']
            is_long = best_h1_level['type'] == 'LHPB'
            
            if is_long:
                stop_price = entry_price - fixed_stop_distance
                target_price = entry_price + fixed_target_distance
            else:
                stop_price = entry_price + fixed_stop_distance
                target_price = entry_price - fixed_target_distance
            
            # Create trade with additional information
            trade = {
                'D1_Level': d1_levels[d1_idx]['price'],
                'H1_Level': best_h1_level['price'],
                'Distance_to_D1': abs(d1_levels[d1_idx]['price'] - best_h1_level['price']),
                'H1_Is_Spike': best_h1_level['is_spike'],
                'H1_Is_Swing': best_h1_level['is_swing'],
                'Entry_Type': ('Spike' if best_h1_level['is_spike'] else 
                             'Swing' if best_h1_level['is_swing'] else 'Basic'),
                'Type': best_h1_level['type'],
                'Direction': 'LONG' if is_long else 'SHORT',
                'Entry_Time': None,
                'Exit_Time': None,
                'Entry_Price': entry_price,
                'Exit_Price': None,
                'Stop_Price': stop_price,
                'Target_Price': target_price,
                'PnL': None,
                'Outcome': None,
                'MAE': 0,
                'MFE': 0,
                'Max_Drawdown': 0,
                'Formation_Time': best_h1_level['formation_time'],
                'Breakout_Time': best_h1_level['breakout_time'],
                'Associated_D1_Levels': len(h1_to_d1[best_h1_idx])  # Number of D1 levels this H1 level was associated with
            }
            
            # Simulate trade execution
            in_trade = False
            df_m5_after_formation = df_m5[df_m5.index >= best_h1_level['formation_time']]
            
            for idx, bar in df_m5_after_formation.iterrows():
                if not in_trade:
                    if bar['Low'] <= entry_price <= bar['High']:
                        trade['Entry_Time'] = idx
                        in_trade = True
                elif in_trade:
                    # Calculate metrics with proper limits
                    if is_long:
                        # MAE limited to risk (stop distance)
                        current_adverse = min(entry_price - bar['Low'], fixed_stop_distance)
                        # MFE limited to reward (target distance)
                        current_favorable = min(bar['High'] - entry_price, fixed_target_distance)
                    else:
                        # MAE limited to risk (stop distance)
                        current_adverse = min(bar['High'] - entry_price, fixed_stop_distance)
                        # MFE limited to reward (target distance)
                        current_favorable = min(entry_price - bar['Low'], fixed_target_distance)
                    
                    # Update MAE and MFE
                    trade['MAE'] = max(trade['MAE'], current_adverse)
                    trade['MFE'] = max(trade['MFE'], current_favorable)
                    
                    # Max drawdown cannot exceed sum of MFE + MAE
                    current_drawdown = min(current_adverse + current_favorable, trade['MFE'] + trade['MAE'])
                    trade['Max_Drawdown'] = max(trade['Max_Drawdown'], current_drawdown)
                    
                    # Check exit conditions
                    if is_long:
                        if bar['Low'] <= stop_price:
                            trade.update({
                                'Exit_Time': idx,
                                'Exit_Price': stop_price,
                                'Outcome': 'STOP',
                                'PnL': (stop_price - entry_price) * contract_multiplier
                            })
                            break
                        elif bar['High'] >= target_price:
                            trade.update({
                                'Exit_Time': idx,
                                'Exit_Price': target_price,
                                'Outcome': 'TARGET',
                                'PnL': (target_price - entry_price) * contract_multiplier
                            })
                            break
                    else:
                        if bar['High'] >= stop_price:
                            trade.update({
                                'Exit_Time': idx,
                                'Exit_Price': stop_price,
                                'Outcome': 'STOP',
                                'PnL': (entry_price - stop_price) * contract_multiplier
                            })
                            break
                        elif bar['Low'] <= target_price:
                            trade.update({
                                'Exit_Time': idx,
                                'Exit_Price': target_price,
                                'Outcome': 'TARGET',
                                'PnL': (entry_price - target_price) * contract_multiplier
                            })
                            break
            
            # Handle open trades at end of data
            if trade['Exit_Time'] is None and trade['Entry_Time'] is not None:
                last_bar = df_m5_after_formation.iloc[-1]
                
                # Calculate final metrics for open trades with proper limits
                if is_long:
                    final_adverse = min(entry_price - last_bar['Low'], fixed_stop_distance)
                    final_favorable = min(last_bar['High'] - entry_price, fixed_target_distance)
                else:
                    final_adverse = min(last_bar['High'] - entry_price, fixed_stop_distance)
                    final_favorable = min(entry_price - last_bar['Low'], fixed_target_distance)
                
                trade['MAE'] = max(trade['MAE'], final_adverse)
                trade['MFE'] = max(trade['MFE'], final_favorable)
                trade['Max_Drawdown'] = max(trade['Max_Drawdown'], min(final_adverse + final_favorable, trade['MFE'] + trade['MAE']))
                
                trade.update({
                    'Exit_Time': df_m5_after_formation.index[-1],
                    'Exit_Price': last_bar['Close'],
                    'Outcome': 'OPEN',
                    'PnL': ((last_bar['Close'] - entry_price) if is_long else (entry_price - last_bar['Close'])) * contract_multiplier
                })
            
            if trade['Entry_Time'] is not None:  # Only add completed trades
                trades.append(trade)
    
    return pd.DataFrame(trades)

def load_and_process_csv(csv_path, resample=None):
    # Load CSV file WITH headers since the first row is header
    df = pd.read_csv(csv_path)
    
    # Convert epoch timestamp to datetime
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('datetime', inplace=True)
    
    # Remove time column as we're using datetime as index
    df.drop('time', axis=1, inplace=True)
    
    # Rename columns to uppercase to match the rest of the code
    df.rename(columns={
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close'
    }, inplace=True)
    
    return df

def ensure_price_continuity(df):
    # Create a copy to avoid modifying the original dataframe
    df_corrected = df.copy()
    
    # For each bar after the first one, ensure open equals previous close
    for i in range(1, len(df_corrected)):
        prev_close = df_corrected.iloc[i-1]['Close']
        current_open = df_corrected.iloc[i]['Open']
        current_high = df_corrected.iloc[i]['High']
        current_low = df_corrected.iloc[i]['Low']
        
        # Adjust the open to match previous close
        df_corrected.iloc[i, df_corrected.columns.get_loc('Open')] = prev_close
        
        # If new open is higher than current high (gap up scenario)
        if prev_close > current_high:
            df_corrected.iloc[i, df_corrected.columns.get_loc('High')] = prev_close
            
        # If new open is lower than current low (gap down scenario)
        if prev_close < current_low:
            df_corrected.iloc[i, df_corrected.columns.get_loc('Low')] = prev_close
    
    return df_corrected

def load_m5_data(data_folder='data'):
    """
    Load the combined 5-minute data file or individual files if combined file doesn't exist.
    """
    import glob
    import os
    
    # First try to load the combined file if it exists
    combined_file = os.path.join(data_folder, 'es-m5-combined.csv')
    if os.path.exists(combined_file):
        print(f"Loading combined 5-minute data file: {combined_file}")
        df = load_and_process_csv(combined_file)
        print(f"Loaded {len(df)} rows from combined 5-minute data file")
        return df
    
    # If combined file doesn't exist, load individual files
    print("Combined 5-minute data file not found. Loading individual files...")
    
    # Find all m5 data files with the new naming convention
    m5_files = glob.glob(os.path.join(data_folder, 'es-m5*.csv'))
    if not m5_files:
        raise FileNotFoundError(f"No 5-minute data files found in {data_folder} directory")
    
    print(f"Found {len(m5_files)} 5-minute data files")
    
    # Load and combine all files
    dfs = []
    for file in m5_files:
        print(f"Loading {os.path.basename(file)}...")
        df = load_and_process_csv(file)
        dfs.append(df)
    
    # Concatenate all dataframes
    combined_df = pd.concat(dfs)
    
    # Remove duplicates
    original_length = len(combined_df)
    combined_df = combined_df.loc[~combined_df.index.duplicated(keep='first')]
    deduped_length = len(combined_df)
    
    print(f"Removed {original_length - deduped_length} duplicate rows")
    print(f"Final 5-minute dataset has {deduped_length} rows")
    
    # Sort by datetime index
    combined_df.sort_index(inplace=True)
    
    # Apply price continuity
    combined_df = ensure_price_continuity(combined_df)
    
    return combined_df

def load_d1_data(data_folder='data'):
    """
    Load the daily data file.
    """
    # Find the D1 data file with the new naming convention
    d1_file = os.path.join(data_folder, 'es-d1-2021-till-7thapr2025.csv')
    if not os.path.exists(d1_file):
        raise FileNotFoundError(f"Daily data file not found: {d1_file}")
    
    print(f"Loading daily data file: {d1_file}")
    df = load_and_process_csv(d1_file)
    print(f"Loaded {len(df)} rows from daily data file")
    
    # Apply price continuity
    df = ensure_price_continuity(df)
    
    return df

def find_nearest_d1_level(h1_level_price, d1_levels, max_distance=5):
    """
    Find the nearest D1 level within max_distance points of the H1 level.
    Returns (level_price, distance) if found, else (None, None)
    """
    if not d1_levels:
        return None, None
        
    nearest_level = None
    min_distance = float('inf')
    
    for d1_level in d1_levels:
        distance = abs(d1_level[0] - h1_level_price)
        if distance <= max_distance and distance < min_distance:
            nearest_level = d1_level
            min_distance = distance
    
    return (nearest_level[0], min_distance) if nearest_level else (None, None)

def find_d1_levels(df_d1, days_before_trade=2, tick_size=0.25, log_file=None):
    """
    Find D1 levels using only the 1-touch criteria without requiring swing/spike patterns.
    This is a simplified version that treats any price level that gets retested as a potential level.
    """
    results = []
    
    # Initialize containers for LHPB and LLPB levels
    zero_touch_lhpb = []  # Format: [(price, index), ...]
    one_touch_lhpb = []   # Format: [(price, formation_idx, breakout_idx), ...]
    
    zero_touch_llpb = []  # Format: [(price, index), ...]
    one_touch_llpb = []   # Format: [(price, formation_idx, breakout_idx), ...]
    
    # Debug periods
    breakout_start = pd.Timestamp('2023-12-18')
    breakout_end = pd.Timestamp('2024-01-20')
    retest_start = pd.Timestamp('2025-04-01')
    retest_end = pd.Timestamp('2025-04-30')
    
    for i in range(len(df_d1)):
        row = df_d1.iloc[i]
        current_time = df_d1.index[i]
        
        # Debug logging for the specific periods
        is_breakout_period = breakout_start <= current_time <= breakout_end
        is_retest_period = retest_start <= current_time <= retest_end
        is_debug_period = is_breakout_period or is_retest_period
        
        if is_debug_period and log_file:
            period_type = "BREAKOUT" if is_breakout_period else "RETEST"
            log_file.write(f"\n=== Processing {period_type} Bar {current_time} ===\n")
            log_file.write(f"OHLC: O={row['Open']:.2f}, H={row['High']:.2f}, L={row['Low']:.2f}, C={row['Close']:.2f}\n")
            log_file.write(f"Active Zero-Touch LHPB Levels: {len(zero_touch_lhpb)}\n")
            if len(zero_touch_lhpb) > 0:
                log_file.write("Zero-Touch Levels:\n")
                for level_price, formation_idx in zero_touch_lhpb:
                    log_file.write(f"  Price: {level_price:.2f}, Formation: {df_d1.index[formation_idx]}\n")
            log_file.write(f"Active One-Touch LHPB Levels: {len(one_touch_lhpb)}\n")
            if len(one_touch_lhpb) > 0:
                log_file.write("One-Touch Levels:\n")
                for level_price, formation_idx, breakout_idx in one_touch_lhpb:
                    log_file.write(f"  Price: {level_price:.2f}, Formation: {df_d1.index[formation_idx]}, Breakout: {df_d1.index[breakout_idx]}\n")
        
        # A) Process 1-touch LHPB levels
        remaining_one_touch_lhpb = []
        for level in one_touch_lhpb:
            level_price, formation_idx, breakout_idx = level
                
            # Check if current bar overlaps with the level
            if row['Low'] <= level_price <= row['High']:
                if is_debug_period and log_file:
                    log_file.write(f"LHPB level {level_price:.2f} overlapped by current bar\n")
                    log_file.write(f"Formation time: {df_d1.index[formation_idx]}\n")
                    log_file.write(f"Breakout time: {df_d1.index[breakout_idx]}\n")
                
                # Check if enough time has passed since breakout (not formation)
                breakout_time = df_d1.index[breakout_idx]
                
                # Ensure both times are timezone-aware or naive in the same way
                if breakout_time.tzinfo != current_time.tzinfo:
                    if breakout_time.tzinfo is None:
                        breakout_time = breakout_time.replace(tzinfo=current_time.tzinfo)
                    elif current_time.tzinfo is None:
                        current_time = current_time.replace(tzinfo=breakout_time.tzinfo)
                
                days_diff = (current_time - breakout_time).days
                
                if is_debug_period and log_file:
                    log_file.write(f"Days since breakout: {days_diff}\n")
                
                if days_diff >= days_before_trade:
                    if is_debug_period and log_file:
                        log_file.write(f"LHPB level {level_price:.2f} ready for trade\n")
                    
                    # Take a trade with current timestamp
                    # Log the level's OHLC, breakout bar's OHLC, and retest bar's OHLC
                    if log_file:
                        formation_bar = df_d1.iloc[formation_idx]
                        breakout_bar = df_d1.iloc[breakout_idx]
                        retest_bar = row
                        log_file.write(f"========== D1 LHPB Level Found ==========\n")
                        log_file.write(f"Formation Time: {df_d1.index[formation_idx]}\n")
                        log_file.write(f"Formation Bar OHLC: O={formation_bar['Open']:.2f}, H={formation_bar['High']:.2f}, L={formation_bar['Low']:.2f}, C={formation_bar['Close']:.2f}\n")
                        log_file.write(f"LxPB Price: {level_price:.2f}\n")
                        log_file.write(f"Breakout Time: {breakout_time}\n")
                        log_file.write(f"Breakout Bar OHLC: O={breakout_bar['Open']:.2f}, H={breakout_bar['High']:.2f}, L={breakout_bar['Low']:.2f}, C={breakout_bar['Close']:.2f}\n")
                        log_file.write(f"Retest Time: {current_time}\n")
                        log_file.write(f"Retest Bar OHLC: O={retest_bar['Open']:.2f}, H={retest_bar['High']:.2f}, L={retest_bar['Low']:.2f}, C={retest_bar['Close']:.2f}\n")
                        log_file.write(f"Days since breakout: {days_diff:.2f}\n\n")
                    
                    results.append({
                        'Type': 'LHPB',
                        'LxPB_Time': df_d1.index[formation_idx],
                        'LxPB_Price': level_price,
                        'Breakout_Time': df_d1.index[breakout_idx],
                        'Retest_Time': current_time
                    })
                else:
                    if is_debug_period and log_file:
                        log_file.write(f"LHPB level {level_price:.2f} retested too early\n")
                    # If retested before minimum time, keep it in the list
                    remaining_one_touch_lhpb.append(level)
            else:
                # Keep the level if not overlapped
                remaining_one_touch_lhpb.append(level)
        
        # Update 1-touch LHPB list
        one_touch_lhpb = remaining_one_touch_lhpb
        
        # B) Process 0-touch LHPB levels
        remaining_zero_touch_lhpb = []
        for level in zero_touch_lhpb:
            level_price, formation_idx = level
                
            # Check if current bar overlaps with the level
            if row['Low'] <= level_price <= row['High']:
                if is_debug_period and log_file:
                    log_file.write(f"Zero-touch LHPB level {level_price:.2f} overlapped by current bar\n")
                    log_file.write(f"Formation time: {df_d1.index[formation_idx]}\n")
                    log_file.write(f"Current bar OHLC: O={row['Open']:.2f}, H={row['High']:.2f}, L={row['Low']:.2f}, C={row['Close']:.2f}\n")
                
                # Move to 1-touch only if:
                # 1. Current bar opens below the level
                # 2. Current bar closes above the level
                if row['Open'] < level_price and row['Close'] > level_price:
                    if is_debug_period and log_file:
                        log_file.write(f"Converting zero-touch LHPB level {level_price:.2f} to one-touch\n")
                    # Move to 1-touch with current index as breakout index
                    one_touch_lhpb.append((level_price, formation_idx, i))
                else:
                    if is_debug_period and log_file:
                        log_file.write(f"Zero-touch LHPB level {level_price:.2f} not converted - no proper breakout\n")
                        if row['Open'] >= level_price:
                            log_file.write("  Reason: Bar opened above or at level\n")
                        if row['Close'] <= level_price:
                            log_file.write("  Reason: Bar closed below or at level\n")
                # Remove from 0-touch either way
            else:
                # Keep the level if not overlapped
                remaining_zero_touch_lhpb.append(level)
        
        # Update 0-touch LHPB list
        zero_touch_lhpb = remaining_zero_touch_lhpb
        
        # C) Add current bar to 0-touch LxPB - for D1, we add every high/low as potential level
        if is_debug_period and log_file:
            log_file.write(f"Adding new zero-touch LHPB level at {row['High']:.2f}\n")
        zero_touch_lhpb.append((row['High'], i))
        if is_debug_period and log_file:
            log_file.write(f"Adding new zero-touch LLPB level at {row['Low']:.2f}\n")
        zero_touch_llpb.append((row['Low'], i))
    
    # Return both the results dataframe and the remaining untested levels
    return pd.DataFrame(results), one_touch_lhpb, one_touch_llpb

def analyze_and_simulate_simplified(
    csv_file_path,
    hours_before_trade=4,
    days_before_trade=2,
    max_distance=10,
    fixed_stop_distance=5,
    fixed_target_distance=10,
    tick_size=0.25
):
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f'LxPB_SpikeSwing_{current_time}'
    os.makedirs(output_dir, exist_ok=True)
    
    # Load the H1 data from CSV file
    df_h1 = load_and_process_csv(csv_file_path)
    
    # Load D1 data
    try:
        df_d1 = load_d1_data()
    except Exception as e:
        print(f"Error loading daily data: {str(e)}")
        return None, None
    
    # Load 5-minute data
    try:
        df_m5 = load_m5_data()
    except Exception as e:
        print(f"Error loading 5-minute data: {str(e)}")
        return None, None
    
    # Apply price continuity correction to hourly data
    df_h1 = ensure_price_continuity(df_h1)
    
    # First find D1 levels
    d1_levels = find_d1_levels_simple(df_d1, days_before_trade=days_before_trade)
    
    # Then find H1 levels with classification
    h1_levels = find_h1_levels_with_classification(df_h1, hours_before_trade=hours_before_trade)
    
    # Save D1 and H1 levels to CSV
    d1_df = pd.DataFrame(d1_levels)
    h1_df = pd.DataFrame(h1_levels)
    
    d1_df.to_csv(os.path.join(output_dir, f'D1_Levels_{current_time}.csv'), index=False)
    h1_df.to_csv(os.path.join(output_dir, f'H1_Levels_{current_time}.csv'), index=False)
    
    # Simulate trades
    trades_df = simulate_trades_revised(
        df_h1=df_h1,
        df_m5=df_m5,
        d1_levels=d1_levels,
        h1_levels=h1_levels,
        max_distance=max_distance,
        fixed_stop_distance=fixed_stop_distance,
        fixed_target_distance=fixed_target_distance,
        contract_multiplier=1
    )

    if len(trades_df) > 0:
        # Ensure all metrics are properly formatted in the CSV
        for col in ['Entry_Time', 'Exit_Time', 'Formation_Time', 'Breakout_Time']:
            if col in trades_df.columns:
                trades_df[col] = trades_df[col].astype(str)
        
        # Round numeric columns to 2 decimal places
        numeric_cols = ['Entry_Price', 'Exit_Price', 'PnL', 'Stop_Price', 'Target_Price', 
                       'D1_Level', 'H1_Level', 'Distance_to_D1', 'MAE', 'MFE', 'Max_Drawdown']
        for col in numeric_cols:
            if col in trades_df.columns:
                trades_df[col] = trades_df[col].round(2)
        
        filename = f'LxPB_trades_Revised_{current_time}.csv'
        trades_df.to_csv(os.path.join(output_dir, filename), index=False)
        
        # Create summary statistics
        summary = {
            'total_trades': len(trades_df),
            'win_rate': (trades_df['Outcome'] == 'TARGET').mean() * 100,
            'avg_win': trades_df[trades_df['Outcome'] == 'TARGET']['PnL'].mean(),
            'avg_loss': trades_df[trades_df['Outcome'] == 'STOP']['PnL'].mean(),
            'total_pnl': trades_df['PnL'].sum(),
            'avg_distance_to_d1': trades_df['Distance_to_D1'].mean(),
            'spike_trades': len(trades_df[trades_df['Entry_Type'] == 'Spike']),
            'swing_trades': len(trades_df[trades_df['Entry_Type'] == 'Swing']),
            'basic_trades': len(trades_df[trades_df['Entry_Type'] == 'Basic']),
            'spike_win_rate': (trades_df[trades_df['Entry_Type'] == 'Spike']['Outcome'] == 'TARGET').mean() * 100 if len(trades_df[trades_df['Entry_Type'] == 'Spike']) > 0 else 0,
            'swing_win_rate': (trades_df[trades_df['Entry_Type'] == 'Swing']['Outcome'] == 'TARGET').mean() * 100 if len(trades_df[trades_df['Entry_Type'] == 'Swing']) > 0 else 0,
            'basic_win_rate': (trades_df[trades_df['Entry_Type'] == 'Basic']['Outcome'] == 'TARGET').mean() * 100 if len(trades_df[trades_df['Entry_Type'] == 'Basic']) > 0 else 0,
            'avg_mae': trades_df['MAE'].mean(),
            'avg_mfe': trades_df['MFE'].mean(),
            'avg_max_drawdown': trades_df['Max_Drawdown'].mean()
        }
        
        return output_dir, summary
    
    return output_dir, None

if __name__ == "__main__":
    # Run the full analysis instead of just the debug function
    # Create data directory if it doesn't exist
    os.makedirs('data', exist_ok=True)
    
    # Check if required data files exist
    required_files = [
        'data/es-h1-8apr2021-7apr2025.csv',
        'data/es-d1-2021-till-7thapr2025.csv',
        'data/es-m5-combined.csv'
    ]
    
    missing_files = [f for f in required_files if not os.path.exists(f)]
    if missing_files:
        print("Error: The following required data files are missing:")
        for f in missing_files:
            print(f"  - {f}")
        print("\nPlease ensure all required data files are present in the 'data' directory.")
        sys.exit(1)
    
    # Run the full analysis
    print("Running full LxPB analysis...")
    output_dir, summary = analyze_and_simulate_simplified(
        csv_file_path='data/es-h1-8apr2021-7apr2025.csv',
        hours_before_trade=4,
        days_before_trade=2,
        max_distance=10,
        fixed_stop_distance=5,
        fixed_target_distance=10
    )
    
    if summary:
        print("\nAnalysis completed successfully!")
        print(f"Results saved to: {output_dir}")
        print("\nSummary:")
        print(f"Total trades: {summary['total_trades']}")
        print(f"Win rate: {summary['win_rate']:.2f}%")
        print(f"Total PnL: {summary['total_pnl']:.2f}")
    else:
        print("\nAnalysis completed but no trades were found.")