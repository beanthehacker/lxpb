import pandas as pd
import numpy as np
from datetime import datetime
import os

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

def lxpb_analysis(df_d1):
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
                is_1t_retest = False
                if is_high:  # LHPB
                    is_breakout = row['Open'] < price and row['Close'] > price
                    # print(f"LHPB Breakout {is_breakout} at {price}, {row['Open']} : {price} :{row['Close']}")
                    is_1t_retest = row['Open'] > price and row['Low'] < price
                else:  # LLPB
                    is_breakout = row['Open'] > price and row['Close'] < price
                    # print(f"LLPB Breakout {is_breakout} at {price}, {row['Open']} : {price} :{row['Close']}")
                    is_1t_retest = row['Open'] < price and row['High'] < price
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
                if is_1t_retest:
                    # print(f"Retest detected at {price, formation_time, current_time}")
                    is_1t_retest = True #do nothing, just for debugging
            else:
                remaining_zero_touch.append(level)
        
        zero_touch_levels = remaining_zero_touch
        
        # 3. Finally add new zero-touch levels
        # print(f"\nC) Adding new zero-touch levels - High: {row['High']}, Low: {row['Low']}")
        zero_touch_levels.append((row['High'], current_time, True))
        zero_touch_levels.append((row['Low'], current_time, False))
        
        # print(f"Current zero-touch levels: {zero_touch_levels}")
        # print(f"Current one-touch levels: {one_touch_levels}")
    
    return pd.DataFrame(results), pd.DataFrame(one_touch_levels)

def main():
    current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f'D1_Levels_{current_time}'
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        df_d1 = load_and_process_csv('data/es-d1-9sep1997-11apr2025.csv')
        # df_h1 = load_and_process_csv('data/es-h1-8apr2021-7apr2025.csv')
        # print("\nInitial Data:")
        # print(df_d1)
        
        levels_d1, lxpb_naked = lxpb_analysis(df_d1)
        
        # print("\nFinal Results:")
        # print(levels_df)
        
        output_file = os.path.join(output_dir, f'D1_Levels_{current_time}.csv')
        levels_d1.to_csv(output_file, index=False)

        output_file_naked_lxpb = os.path.join(output_dir, f'D1_Levels_Naked_{current_time}.csv')
        lxpb_naked.to_csv(output_file_naked_lxpb, index=False)
        # print(f"\nResults saved to: {output_file}")
        
        total_levels = len(levels_d1)
        one_touch = len(levels_d1[levels_d1['status'] == 'one_touch'])
        retested = len(levels_d1[levels_d1['status'] == 'retested'])
        
        # print("\nSummary:")
        # print(f"Total valid levels found: {total_levels}")
        # print(f"One-touch levels: {one_touch}")
        # print(f"Retested levels: {retested}")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        raise e

if __name__ == "__main__":
    main()