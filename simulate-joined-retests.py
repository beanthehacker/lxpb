import pandas as pd
import numpy as np
from datetime import datetime
import os
import sys

def load_and_process_csv(csv_path):
    """Load and process the CSV file with time column."""
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

def is_swing_low(df, index, lookback=1):
    """
    Check if the current bar forms a swing low.
    A swing low occurs when the current bar's low is lower than the lookback bars' lows.
    """
    if index < lookback or index >= len(df) - lookback:
        return False
    
    current_low = df.iloc[index]['Low']
    
    # Check if current low is lower than previous and next bars' lows
    if current_low <= df.iloc[index-1]['Low'] and current_low <= df.iloc[index+1]['Low']:
        return True
    
    return False

def calculate_fta(df_h1, breakout_time, retest_time, min_wick_size=8):
    """
    Calculate the First Trouble Area (FTA) for a LHPB level
    FTA is the min(lowest swing low, low of a candle with lower wick >= 5 points)
    """
    # Get index of formation time in h1 data
    try:
        breakout_idx = df_h1.index.get_indexer([pd.to_datetime(breakout_time)])[0]
        # Convert retest_time to datetime and get its index
        retest_idx = df_h1.index.get_indexer([pd.to_datetime(retest_time)])[0]
    except:
        # If formation time is not found, return None
        return None
    
    # Look at H1 candles after the formation time
    df_after = df_h1.iloc[breakout_idx+1:retest_idx]
    
    # Find all swing lows
    swing_lows = []
    for i in range(len(df_after)):
        if is_swing_low(df_after, i):
            swing_lows.append(df_after.iloc[i]['Low'])
    
    # Find candles with lower wick >= min_wick_size
    large_wicks = []
    for i in range(len(df_after)):
        bar = df_after.iloc[i]
        lower_wick = min(bar['Open'], bar['Close']) - bar['Low']
        if lower_wick >= min_wick_size:
            large_wicks.append(bar['Low'])
    
    # Find the FTA as min(lowest swing low, lowest large wick)
    if not swing_lows and not large_wicks:
        # If no swing lows or large wicks found, FTA is None
        return None
    elif not swing_lows:
        return min(large_wicks)
    elif not large_wicks:
        return min(swing_lows)
    else:
        return min(min(swing_lows), min(large_wicks))

def simulate_trade(df_m5, entry_price, target_price, stop_price, retest_time):
    """
    Simulate a trade with entry at LHPB level, target at FTA, stop at low of breakout candle
    """
    # Convert retest_time to pandas datetime
    retest_time = pd.to_datetime(retest_time)
    
    # Get data after retest time
    df_after = df_m5[df_m5.index >= retest_time]
    
    if df_after.empty:
        return None
    
    # Initialize trade info
    trade_info = {
        'entry_price': entry_price,
        'target_price': target_price,
        'stop_price': stop_price,
        'entry_time': None,
        'exit_time': None,
        'exit_price': None,
        'pnl': None,
        'outcome': None
    }
    
    # Simulate trade bar by bar
    in_trade = False
    
    for idx, bar in df_after.iterrows():
        # Check for entry - LHPB level must be touched
        if not in_trade and bar['Low'] <= entry_price <= bar['High']:
            trade_info['entry_time'] = idx
            in_trade = True
            continue
            
        # If in trade, check for stop or target
        if in_trade:
            # Check if stop loss hit
            if bar['Low'] <= stop_price:
                trade_info['exit_time'] = idx
                trade_info['exit_price'] = stop_price
                trade_info['pnl'] = stop_price - entry_price
                trade_info['outcome'] = 'LOSS'
                break
                
            # Check if target hit
            if bar['High'] >= target_price:
                trade_info['exit_time'] = idx
                trade_info['exit_price'] = target_price
                trade_info['pnl'] = target_price - entry_price
                trade_info['outcome'] = 'WIN'
                break
    
    # If trade still open at end of data
    if in_trade and trade_info['exit_time'] is None:
        trade_info['exit_time'] = df_after.index[-1]
        trade_info['exit_price'] = df_after.iloc[-1]['Close']
        trade_info['pnl'] = df_after.iloc[-1]['Close'] - entry_price
        trade_info['outcome'] = 'OPEN'
    
    return trade_info

def main():
    # Get current timestamp for output file names
    curr_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Load data
    print("Loading data...")
    es_h1_data = load_and_process_csv('data/es-h1-4apr2021-11apr2025.csv')
    es_m5_data = load_and_process_csv('data/es-m5-m25-till11apr2025.csv')
    joined_data = pd.read_csv('LxPB_20250412_174521/Joined_ES_NQ_M5_Retests.csv')
    
    # joined_data = pd.read_csv('LxPB_20250412_174521/test_joined_retests.csv')
    
    # Create output directory
    output_dir = f'Trades_{curr_time}'
    os.makedirs(output_dir, exist_ok=True)
    
    # Filter for LHPB levels only
    lhpb_data = joined_data[joined_data['es_type'] == 'LHPB']
    
    # Convert price columns to float
    lhpb_data['es_price'] = lhpb_data['es_price'].astype(float)
    
    # Create a unique identifier for each level
    lhpb_data['level_id'] = lhpb_data['es_price'].astype(str) + '_' + lhpb_data['es_formation_time']
    
    # Keep only unique levels (price + formation time)
    unique_levels = lhpb_data.drop_duplicates(subset=['level_id'])
    
    # For each unique level, calculate FTA and simulate trade
    trades = []
    
    print(f"Processing {len(unique_levels)} unique LHPB levels...")

    for _, level in unique_levels.iterrows():
        # Calculate First Trouble Area (FTA)
        fta = calculate_fta(es_h1_data, level['es_breakout_time'], level['es_retest_time'])

        if fta is None:
            # Skip if FTA cannot be calculated
            continue
        
        # Get stop loss price (low of breakout candle)
        breakout_time = pd.to_datetime(level['es_breakout_time'])
        breakout_candle = es_h1_data[es_h1_data.index == breakout_time]
        
        if breakout_candle.empty:
            # Skip if breakout candle not found
            continue
            
        stop_price = breakout_candle.iloc[0]['Low']
        
        # Ensure price is float for comparison
        es_price = float(level['es_price'])
        
        # Skip if FTA is not above entry or stop is not below entry (trade doesn't make sense)
        if fta < es_price or stop_price >= es_price:
            continue
            
        # Calculate R (reward/risk ratio)
        risk = es_price - stop_price
        reward = fta - es_price
        R = reward / risk if risk > 0 else 0
        
        # Skip if R is less than 0.9
        if R < 0.9:
            continue
        
        # Simulate trade
        trade_info = simulate_trade(
            es_m5_data,
            entry_price=es_price,
            target_price=fta,
            stop_price=stop_price,
            retest_time=level['es_retest_time']
        )
        
        if trade_info is not None and trade_info['entry_time'] is not None:
            # Add level info to trade
            trade_info.update({
                'level_price': es_price,
                'formation_time': level['es_formation_time'],
                'breakout_time': level['es_breakout_time'],
                'retest_time': level['es_retest_time'],
                'fta': fta,
                'risk': risk,
                'reward': reward,
                'rr_ratio': reward / risk if risk > 0 else 0,
                'R': R
            })
            
            trades.append(trade_info)
    
    # Convert to DataFrame and calculate metrics
    if trades:
        trades_df = pd.DataFrame(trades)
        
        # Calculate metrics
        total_trades = len(trades_df)
        win_trades = len(trades_df[trades_df['outcome'] == 'WIN'])
        loss_trades = len(trades_df[trades_df['outcome'] == 'LOSS'])
        open_trades = len(trades_df[trades_df['outcome'] == 'OPEN'])
        
        win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0
        loss_rate = (loss_trades / total_trades) * 100 if total_trades > 0 else 0
        
        avg_win = trades_df[trades_df['outcome'] == 'WIN']['pnl'].mean() if win_trades > 0 else 0
        avg_loss = trades_df[trades_df['outcome'] == 'LOSS']['pnl'].mean() if loss_trades > 0 else 0
        
        total_pnl = trades_df['pnl'].sum()
        
        # Save trades to CSV
        output_file = os.path.join(output_dir, f'LHPB_Trades_{curr_time}.csv')
        trades_df.to_csv(output_file, index=False)
        
        # Append metrics to CSV
        with open(output_file, 'a') as f:
            f.write('\n\n')
            f.write('METRICS\n')
            f.write(f'Total Trades,{total_trades}\n')
            f.write(f'Win Trades,{win_trades}\n')
            f.write(f'Loss Trades,{loss_trades}\n')
            f.write(f'Open Trades,{open_trades}\n')
            f.write(f'Win Rate,{win_rate:.2f}%\n')
            f.write(f'Loss Rate,{loss_rate:.2f}%\n')
            f.write(f'Average Win,{avg_win:.2f}\n')
            f.write(f'Average Loss,{avg_loss:.2f}\n')
            f.write(f'Total PnL,{total_pnl:.2f}\n')
            f.write(f'Min R Value,0.9\n')
            
        print(f"Analysis complete. Results saved to {output_file}")
        print(f"Total trades: {total_trades}")
        print(f"Win rate: {win_rate:.2f}%")
        print(f"Loss rate: {loss_rate:.2f}%")
        print(f"Total PnL: {total_pnl:.2f}")
    else:
        print("No valid trades found.")

if __name__ == "__main__":
    main()