import pandas as pd
import numpy as np
from datetime import datetime
import os

def ensure_price_continuity(df):
    # print("\nPrice Continuity Check:")
    df_corrected = df.copy()
    
    for i in range(1, len(df_corrected)):
        prev_close = df_corrected.iloc[i-1]['Close']
        current_open = df_corrected.iloc[i]['Open']
        current_high = df_corrected.iloc[i]['High']
        current_low = df_corrected.iloc[i]['Low']
        
        # print(f"\nBar {i}:")
        # print(f"Previous Close: {prev_close}")
        # print(f"Before adjustment - Open: {current_open}, High: {current_high}, Low: {current_low}")
        
        df_corrected.iloc[i, df_corrected.columns.get_loc('Open')] = prev_close
        
        if prev_close > current_high:
            df_corrected.iloc[i, df_corrected.columns.get_loc('High')] = prev_close
            # print("Adjusted High due to gap up")
            
        if prev_close < current_low:
            df_corrected.iloc[i, df_corrected.columns.get_loc('Low')] = prev_close
            # print("Adjusted Low due to gap down")
        
        # print(f"After adjustment - Open: {df_corrected.iloc[i]['Open']}, High: {df_corrected.iloc[i]['High']}, Low: {df_corrected.iloc[i]['Low']}")
    
    return df_corrected

def load_and_process_csv(csv_path):
    """Load and process the CSV file containing D1 data."""
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
    
    # Ensure price continuity
    # df = ensure_price_continuity(df)
    
    return df

def find_d1_levels(df_d1):
    # print("\nProcessing D1 Levels:")
    results = []
    zero_touch_levels = []
    one_touch_levels = []
    
    for i in range(len(df_d1)):
        row = df_d1.iloc[i]
        current_time = df_d1.index[i]
        
        # print(f"\nProcessing Bar {i}:")
        # print(f"DateTime: {current_time}")
        # print(f"OHLC: Open={row['Open']}, High={row['High']}, Low={row['Low']}, Close={row['Close']}")
        
        # 1. First check for retests of existing one-touch levels
        # print("\nA) Checking One-Touch Levels for Retests:")
        remaining_one_touch = []
        for level in one_touch_levels:
            price, formation_time, breakout_time, is_high = level
            # print(f"Checking level: Price={price}, Type={'LHPB' if is_high else 'LLPB'}")
            
            if row['Low'] <= price <= row['High']:
                # print(f"Bar touches level at {price}")
                # add more conditions later
                is_valid_retest = (row['High'] >= price and 
                                     row['Low'] <= price)
                
                if is_valid_retest:
                    # print(f"Valid retest detected at {price}")
                    # Find and update the existing level in results
                    for result in results:
                        if (result['price'] == price and 
                            result['formation_time'] == formation_time and 
                            result['status'] == 'one_touch'):
                            result['status'] = 'retested'
                            result['retest_time'] = current_time
                            break
            else:
                remaining_one_touch.append(level)
        
        one_touch_levels = remaining_one_touch
        
        # 2. Then check for breakouts of existing zero-touch levels
        # print("\nB) Checking Zero-Touch Levels for Breakouts:")
        remaining_zero_touch = []
        for level in zero_touch_levels:
            price, formation_time, is_high = level
            # print(f"Checking level: Price={price}, Type={'LHPB' if is_high else 'LLPB'}")
            
            if row['Low'] <= price <= row['High']:
                # print(f"Bar touches level at {price}")
                is_breakout = False
                if is_high:  # LHPB
                    is_breakout = row['Open'] < price and row['Close'] > price
                    # print(f"LHPB Breakout {is_breakout} at {price}, {row['Open']} : {price} :{row['Close']}")
                else:  # LLPB
                    is_breakout = row['Open'] > price and row['Close'] < price
                    # print(f"LLPB Breakout {is_breakout} at {price}, {row['Open']} : {price} :{row['Close']}")
                if is_breakout:
                    one_touch_levels.append((price, formation_time, current_time, is_high))
                    results.append({
                        'price': price,
                        'formation_time': formation_time,
                        'breakout_time': current_time,
                        'retest_time': None,
                        'type': 'LHPB' if is_high else 'LLPB',
                        'status': 'one_touch'
                    })
            else:
                remaining_zero_touch.append(level)
        
        zero_touch_levels = remaining_zero_touch
        
        # 3. Finally add new zero-touch levels
        # print(f"\nC) Adding new zero-touch levels - High: {row['High']}, Low: {row['Low']}")
        zero_touch_levels.append((row['High'], current_time, True))
        zero_touch_levels.append((row['Low'], current_time, False))
        
        # print(f"Current zero-touch levels: {zero_touch_levels}")
        # print(f"Current one-touch levels: {one_touch_levels}")
    
    return pd.DataFrame(results)

def main():
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f'D1_Levels_{current_time}'
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        df_d1 = load_and_process_csv('data/es-d1-9sep1997-11apr2025.csv')  # Use the test file instead
        # print("\nInitial Data:")
        # print(df_d1)
        
        levels_df = find_d1_levels(df_d1)
        
        # print("\nFinal Results:")
        # print(levels_df)
        
        output_file = os.path.join(output_dir, f'D1_Levels_{current_time}.csv')
        levels_df.to_csv(output_file, index=False)
        # print(f"\nResults saved to: {output_file}")
        
        total_levels = len(levels_df)
        one_touch = len(levels_df[levels_df['status'] == 'one_touch'])
        retested = len(levels_df[levels_df['status'] == 'retested'])
        
        # print("\nSummary:")
        # print(f"Total valid levels found: {total_levels}")
        # print(f"One-touch levels: {one_touch}")
        # print(f"Retested levels: {retested}")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e

if __name__ == "__main__":
    main()