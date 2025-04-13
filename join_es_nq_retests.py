import pandas as pd
from datetime import datetime, timedelta
import os

def load_retest_data(file_path):
    """Load the retest data from CSV with proper datetime conversion"""
    df = pd.read_csv(file_path, sep=None, engine='python')
    
    # Convert time columns to datetime
    for col in ['formation_time', 'breakout_time', 'retest_time', 'm5_retest_time']:
        df[col] = pd.to_datetime(df[col])
    
    return df

def is_within_time_window(time1, time2, minutes=5):
    """Check if two timestamps are within a specified time window of each other"""
    delta = abs((time1 - time2).total_seconds()) / 60
    return delta <= minutes

def join_retest_data(es_data, nq_data, window_minutes=5):
    """Join ES and NQ retest data based on matching m5_retest_time within a time window"""
    joined_results = []
    unmatched_es = []
    
    # Track ES rows that have matches
    matched_es_indices = set()
    
    # For each ES retest, find matching NQ retests
    for es_idx, es_row in es_data.iterrows():
        es_retest_time = es_row['m5_retest_time']
        found_match = False
        
        # Look for matching NQ retests
        for nq_idx, nq_row in nq_data.iterrows():
            nq_retest_time = nq_row['m5_retest_time']
            
            # Check if times are within the specified window
            if is_within_time_window(es_retest_time, nq_retest_time, window_minutes):
                # Create a joined record
                joined_record = {
                    # ES data (prefixed with es_)
                    'es_price': es_row['price'],
                    'es_formation_time': es_row['formation_time'],
                    'es_breakout_time': es_row['breakout_time'],
                    'es_retest_time': es_row['retest_time'],
                    'es_type': es_row['type'],
                    'es_status': es_row['status'],
                    'es_m5_retest_time': es_row['m5_retest_time'],
                    'es_m5_open': es_row['m5_open'],
                    'es_m5_high': es_row['m5_high'],
                    'es_m5_low': es_row['m5_low'],
                    'es_m5_close': es_row['m5_close'],
                    
                    # NQ data (prefixed with nq_)
                    'nq_price': nq_row['price'],
                    'nq_formation_time': nq_row['formation_time'],
                    'nq_breakout_time': nq_row['breakout_time'],
                    'nq_retest_time': nq_row['retest_time'],
                    'nq_type': nq_row['type'],
                    'nq_status': nq_row['status'],
                    'nq_m5_retest_time': nq_row['m5_retest_time'],
                    'nq_m5_open': nq_row['m5_open'],
                    'nq_m5_high': nq_row['m5_high'],
                    'nq_m5_low': nq_row['m5_low'],
                    'nq_m5_close': nq_row['m5_close'],
                    
                    # Time difference in minutes
                    'time_diff_minutes': abs((es_retest_time - nq_retest_time).total_seconds() / 60)
                }
                
                joined_results.append(joined_record)
                found_match = True
                matched_es_indices.add(es_idx)
        
        # If no match found, add to unmatched list
        if not found_match:
            unmatched_es.append(es_row)
    
    return joined_results, unmatched_es

def main():
    # Define file paths
    timepath = "20250412_081429"
    input_folder = f"LxPB_{timepath}"
    es_file = f"{input_folder}/ES_LxPB_M5_Retests.csv"
    nq_file = f"{input_folder}/NQ_LxPB_M5_Retests.csv"
    output_file = f"{input_folder}/Joined_ES_NQ_M5_Retests.csv"
    
    # Load the data
    es_data = load_retest_data(es_file)
    nq_data = load_retest_data(nq_file)
    
    # Join the data
    joined_results, unmatched_es = join_retest_data(es_data, nq_data)
    
    # Create and save the joined DataFrame
    if joined_results:
        joined_df = pd.DataFrame(joined_results)
        joined_df.to_csv(output_file, index=False)
        print(f"Joined data saved to {output_file}")
    else:
        print("No matching retests found")
    
    # Calculate statistics
    es_count = len(es_data)
    nq_count = len(nq_data)
    joined_count = len(joined_results)
    unmatched_es_count = len(unmatched_es)
    
    # Append statistics to the output file
    with open(output_file, 'a') as f:
        f.write("\n\n# Statistics\n")
        f.write(f"Total ES retests: {es_count}\n")
        f.write(f"Total NQ retests: {nq_count}\n")
        f.write(f"Joined ES-NQ retests: {joined_count}\n")
        f.write(f"ES retests without NQ match: {unmatched_es_count}\n")
        f.write(f"Match rate: {joined_count / es_count * 100:.2f}%\n")
    
    print(f"\nStatistics:")
    print(f"Total ES retests: {es_count}")
    print(f"Total NQ retests: {nq_count}")
    print(f"Joined ES-NQ retests: {joined_count}")
    print(f"ES retests without NQ match: {unmatched_es_count}")
    print(f"Match rate: {joined_count / es_count * 100:.2f}%")

if __name__ == "__main__":
    main() 