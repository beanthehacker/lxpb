#!/usr/bin/env python3
"""
Process 5-minute ES data files, deduplicate them, and store the result in a single CSV file.
This script handles files with the pattern 'es-m5*.csv' in the data directory.
"""

import os
import pandas as pd
import glob
from datetime import datetime

def process_m5_data(data_dir='data', output_file='es-m5-combined.csv'):
    """
    Process all 5-minute data files, deduplicate them, and save to a single CSV file.
    
    Args:
        data_dir (str): Directory containing the data files
        output_file (str): Name of the output file
    
    Returns:
        str: Path to the output file
    """
    # Get the absolute path to the data directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(script_dir, data_dir)
    
    # Find all m5 data files
    m5_files = glob.glob(os.path.join(data_path, 'es-m5*.csv'))
    
    if not m5_files:
        print(f"No 5-minute data files found in {data_path}")
        return None
    
    print(f"Found {len(m5_files)} 5-minute data files")
    
    # Load and combine all files
    dfs = []
    for file in m5_files:
        print(f"Loading {os.path.basename(file)}...")
        try:
            # Read the CSV file
            df = pd.read_csv(file)
            
            # Check if the file has the expected columns
            required_columns = ['time', 'open', 'high', 'low', 'close']
            if not all(col in df.columns for col in required_columns):
                print(f"Warning: {os.path.basename(file)} does not have the expected columns. Skipping.")
                continue
                
            # Add filename as a column for tracking
            df['source_file'] = os.path.basename(file)
            
            dfs.append(df)
        except Exception as e:
            print(f"Error loading {os.path.basename(file)}: {str(e)}")
    
    if not dfs:
        print("No valid data files found.")
        return None
    
    # Concatenate all dataframes
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Remove duplicates based on the 'time' column (epoch timestamp)
    original_length = len(combined_df)
    combined_df = combined_df.drop_duplicates(subset=['time'], keep='first')
    deduped_length = len(combined_df)
    
    print(f"Removed {original_length - deduped_length} duplicate rows")
    print(f"Final 5-minute dataset has {deduped_length} rows")
    
    # Sort by time
    combined_df = combined_df.sort_values('time')
    
    # Save to CSV
    output_path = os.path.join(data_path, output_file)
    combined_df.to_csv(output_path, index=False)
    print(f"Combined data saved to {output_path}")
    
    return output_path

if __name__ == "__main__":
    # Process the data
    output_file = process_m5_data()
    
    if output_file:
        print(f"Successfully processed 5-minute data and saved to {output_file}")
    else:
        print("Failed to process 5-minute data") 