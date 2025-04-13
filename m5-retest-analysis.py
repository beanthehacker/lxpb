import pandas as pd
from datetime import datetime, timedelta

def load_lxpb_data(file_path):
    """Load LxPB data with proper datetime conversion"""
    df = pd.read_csv(file_path)
    # Convert time columns to datetime
    for col in ['formation_time', 'breakout_time', 'retest_time']:
        df[col] = pd.to_datetime(df[col])
    return df

def load_m5_data(file_path):
    """Load M5 data with proper datetime conversion"""
    df = pd.read_csv(file_path)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('datetime', inplace=True)
    return df

def find_m5_retest(row, m5_data):
    """
    Find the exact M5 bar where the retest occurred.
    Returns the M5 datetime and OHLC data for the retest bar.
    """
    # Get the hour of the retest
    retest_hour = row['retest_time']
    
    # Look at M5 bars within that hour and the next hour to be safe
    start_time = retest_hour - timedelta(minutes=5)
    end_time = retest_hour + timedelta(minutes=65)
    
    # Filter M5 data to this time window
    m5_window = m5_data[start_time:end_time]

    # Find the first M5 bar that touches the level
    level_price = row['price']
    for idx, m5_bar in m5_window.iterrows():
        if m5_bar['low'] <= level_price <= m5_bar['high']:
            return {
                'm5_retest_time': idx,
                'm5_open': m5_bar['open'],
                'm5_high': m5_bar['high'],
                'm5_low': m5_bar['low'],
                'm5_close': m5_bar['close']
            }
    return None

def process_instrument(lxpb_file, m5_file, output_file):
    """Process a single instrument's data"""
    # Load data
    lxpb_data = load_lxpb_data(lxpb_file)
    m5_data = load_m5_data(m5_file)
    
    # Filter for retested levels only
    retested_levels = lxpb_data[lxpb_data['status'] == 'retested'].copy()
    
    # Store results
    m5_results = []
    
    # Process each retested level
    for _, row in retested_levels.iterrows():
        m5_retest = find_m5_retest(row, m5_data)
        if m5_retest:
            result = row.to_dict()
            result.update(m5_retest)
            m5_results.append(result)
    
    # Create and save results DataFrame
    if m5_results:
        results_df = pd.DataFrame(m5_results)
        results_df.to_csv(output_file, index=False)
        print(f"Saved detailed retest analysis to {output_file}")
    else:
        print(f"No retests found for analysis in {lxpb_file}")

def main():
    # Define file paths
    lxpb_folder = f"LxPB_20250412_174521"
    es_lxpb = f"{lxpb_folder}/ES_{lxpb_folder}.csv"
    nq_lxpb = f"{lxpb_folder}/NQ_{lxpb_folder}.csv"
    
    es_m5 = "data/es-m5-m25-till11apr2025.csv"
    nq_m5 = "data/nq-m5-m25-till11apr2025.csv"
    
    # Process ES
    process_instrument(
        es_lxpb,
        es_m5,
        f"{lxpb_folder}/ES_LxPB_M5_Retests.csv"
    )
    
    # Process NQ
    process_instrument(
        nq_lxpb,
        nq_m5,
        f"{lxpb_folder}/NQ_LxPB_M5_Retests.csv"
    )

if __name__ == "__main__":
    main()