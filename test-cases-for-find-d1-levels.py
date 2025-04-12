import pandas as pd
from datetime import datetime

def create_test_data():
    """
    Create test data with various scenarios for testing find_d1_levels function
    """
    # Test case 1: Basic zero-touch to one-touch to retest scenario
    test_case_1 = [
        # Bar 0: Creates initial zero-touch levels
        {'time': 1703718000, 'open': 5000.0, 'high': 5100.0, 'low': 4900.0, 'close': 5050.0},
        
        # Bar 1: Breakout of Bar 0's high (LHPB) - should become one-touch
        {'time': 1703804400, 'open': 5050.0, 'high': 5150.0, 'low': 5080.0, 'close': 5120.0},
        
        # Bar 2: Retest of the one-touch LHPB level
        {'time': 1703890800, 'open': 5120.0, 'high': 5130.0, 'low': 5080.0, 'close': 5110.0}
    ]
    
    # Test case 2: Focus on LLPB (Last Low Pre Breakout)
    test_case_2 = [
        # Bar 0: Creates initial zero-touch levels
        {'time': 1704063600, 'open': 5200.0, 'high': 5250.0, 'low': 5150.0, 'close': 5200.0},
        
        # Bar 1: Breakout of Bar 0's low (LLPB) - should become one-touch
        {'time': 1704150000, 'open': 5180.0, 'high': 5190.0, 'low': 5120.0, 'close': 5130.0},
        
        # Bar 2: Retest of the one-touch LLPB level
        {'time': 1704236400, 'open': 5140.0, 'high': 5160.0, 'low': 5130.0, 'close': 5155.0}
    ]
    
    # Test case 3: Multiple levels and overlapping scenarios
    test_case_3 = [
        # Bar 0: Initial levels
        {'time': 1704409200, 'open': 5300.0, 'high': 5350.0, 'low': 5250.0, 'close': 5320.0},
        
        # Bar 1: Breakout of high from Bar 0
        {'time': 1704495600, 'open': 5330.0, 'high': 5400.0, 'low': 5310.0, 'close': 5380.0},
        
        # Bar 2: Creates new levels without touching previous ones
        {'time': 1704582000, 'open': 5400.0, 'high': 5450.0, 'low': 5380.0, 'close': 5430.0},
        
        # Bar 3: Breakout of low from Bar 0 (LLPB)
        {'time': 1704668400, 'open': 5280.0, 'high': 5290.0, 'low': 5200.0, 'close': 5220.0},
        
        # Bar 4: Retests both previous breakout levels
        {'time': 1704754800, 'open': 5300.0, 'high': 5360.0, 'low': 5240.0, 'close': 5330.0}
    ]
    
    # Test case 4: Edge cases - exact touches and near misses
    test_case_4 = [
        # Bar 0: Initial levels
        {'time': 1704927600, 'open': 5500.0, 'high': 5550.0, 'low': 5450.0, 'close': 5520.0},
        
        # Bar 1: Open exactly at high from Bar 0, close above (should count as breakout)
        {'time': 1705014000, 'open': 5550.0, 'high': 5580.0, 'low': 5540.0, 'close': 5570.0},
        
        # Bar 2: Low exactly at 5550 (should retest the previous breakout)
        {'time': 1705100400, 'open': 5570.0, 'high': 5590.0, 'low': 5550.0, 'close': 5580.0},
        
        # Bar 3: Almost touches 5450 but not quite (should NOT trigger breakout)
        {'time': 1705186800, 'open': 5480.0, 'high': 5490.0, 'low': 5451.0, 'close': 5470.0}
    ]
    
    # Test case 5: Based on the example in your question
    test_case_5 = [
        # Bar 0: Creates zero-touch levels
        {'time': 1703718000, 'open': 5147.5, 'high': 5154.5, 'low': 5141.0, 'close': 5146.5},
        
        # Bar 1: Breakout of high from Bar 0 (LHPB)
        {'time': 1705618800, 'open': 5124.25, 'high': 5187.25, 'low': 5121.5, 'close': 5182.75},
        
        # Bar 2: Retest LHPB and breakout of low from Bar 0 (LLPB)
        {'time': 1743717600, 'open': 5423.0, 'high': 5435.0, 'low': 5074.0, 'close': 5096.75}
    ]
    
    test_cases = {
        "basic_scenario": test_case_1,
        "llpb_focus": test_case_2,
        "multiple_levels": test_case_3,
        "edge_cases": test_case_4,
        "example_scenario": test_case_5
    }
    
    return test_cases

def prepare_dataframe(test_data):
    """Convert test data to DataFrame and format it properly"""
    df = pd.DataFrame(test_data)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('datetime', inplace=True)
    
    # Rename columns to match the function requirements
    df.rename(columns={
        'open': 'Open',
        'high': 'High', 
        'low': 'Low',
        'close': 'Close'
    }, inplace=True)
    
    return df

def run_test_cases(function_to_test):
    """Run test cases and analyze results"""
    test_cases = create_test_data()
    results = {}
    
    for name, test_data in test_cases.items():
        print(f"\n=== Testing {name} ===")
        df = prepare_dataframe(test_data)
        print("Input data:")
        print(df[['Open', 'High', 'Low', 'Close']])
        
        result_df = function_to_test(df)
        results[name] = result_df
        
        print("\nResult:")
        if len(result_df) == 0:
            print("No levels detected")
        else:
            print(result_df)
            
        # Analyze results
        print("\nAnalysis:")
        if name == "basic_scenario":
            if len(result_df) >= 2:
                print("✓ Created zero-touch levels")
                print("✓ Detected LHPB breakout")
                if 'retested' in result_df['status'].values:
                    print("✓ Detected retest")
                else:
                    print("✗ Failed to detect retest")
            else:
                print("✗ Test case failed - insufficient levels detected")
        
        elif name == "llpb_focus":
            if len(result_df) >= 1 and 'LLPB' in result_df['type'].values:
                print("✓ Detected LLPB breakout")
                if 'retested' in result_df['status'].values:
                    print("✓ Detected LLPB retest")
                else:
                    print("✗ Failed to detect LLPB retest")
            else:
                print("✗ Test case failed - LLPB handling issue")
        
        elif name == "multiple_levels":
            lhpb_count = len(result_df[result_df['type'] == 'LHPB'])
            llpb_count = len(result_df[result_df['type'] == 'LLPB'])
            retested_count = len(result_df[result_df['status'] == 'retested'])
            
            print(f"LHPB levels: {lhpb_count}")
            print(f"LLPB levels: {llpb_count}")
            print(f"Retested levels: {retested_count}")
            
            if lhpb_count >= 1 and llpb_count >= 1 and retested_count >= 1:
                print("✓ Successfully handled multiple levels")
            else:
                print("✗ Issues with handling multiple levels")
        
        elif name == "edge_cases":
            # Check if the exact touch at 5550 was properly handled
            lhpb_5550 = result_df[(result_df['price'] == 5550.0) & (result_df['type'] == 'LHPB')]
            if not lhpb_5550.empty and 'retested' in lhpb_5550['status'].values:
                print("✓ Correctly handled exact level touch")
            else:
                print("✗ Issue with exact level touch")
                
            # Check that near miss didn't trigger
            llpb_5450 = result_df[(result_df['price'] == 5450.0) & (result_df['type'] == 'LLPB')]
            if llpb_5450.empty or all(llpb_5450['status'] != 'one_touch'):
                print("✓ Correctly avoided near-miss breakout")
            else:
                print("✗ Incorrectly triggered near-miss breakout")
        
        elif name == "example_scenario":
            # Check if we get the expected behavior from the example
            lhpb_5154_5 = result_df[(result_df['price'] == 5154.5) & (result_df['type'] == 'LHPB')]
            llpb_5141 = result_df[(result_df['price'] == 5141.0) & (result_df['type'] == 'LLPB')]
            
            if not lhpb_5154_5.empty and 'retested' in lhpb_5154_5['status'].values:
                print("✓ Bar 0 high became LHPB and was retested")
            else:
                print("✗ Issue with Bar 0 high LHPB detection or retest")
                
            if not llpb_5141.empty and 'one_touch' in llpb_5141['status'].values:
                print("✓ Bar 0 low became LLPB")
            else:
                print("✗ Issue with Bar 0 low LLPB detection")
    
    return results

if __name__ == "__main__":
    from d1 import find_d1_levels
    results = run_test_cases(find_d1_levels)
