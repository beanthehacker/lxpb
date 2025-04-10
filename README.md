Focusing on how the levels are calculated and maintained across different timeframes.

Overview of the Script
This script implements a trading strategy called "Last High Pre Breakout" (LHPB) and its mirror/opposite, called "Last Low Pre Breakout" (LLPB). It analyzes price data across multiple timeframes (Daily, Hourly, and 5-minute) to identify potential trading opportunities based on price levels that are broken and then retested.

Key Components
1. Data Loading and Processing
The script loads data from CSV files for different timeframes (D1, H1, M5)
It ensures price continuity by adjusting gaps between any two consecutive bars
It processes timestamps and standardizes column names

2. Level Identification
The script identifies potential price levels on D1 and H1 timeframes
It classifies levels as "zero-touch" or "one-touch" based on how many times they've been tested
It tracks when levels are formed, broken, and retested

3. Trade Simulation
The script simulates trades based on the identified levels
It calculates entry, exit, stop loss, and target prices
It tracks trade outcomes and performance metrics (you can ignore metrics).

Level Calculation and Maintenance
Let's focus on how levels are calculated and maintained:
1. D1 Level Identification
The find_d1_levels_simple function identifies D1 levels:

A) D1 Level Maintenance Process:
1. Zero-Touch Levels:
Every bar's high and low are added as potential zero-touch levels
When a bar overlaps with a zero-touch level, it checks for a breakout
A breakout occurs when:
  For LHPB: Bar opens below the level and closes above it
  For LLPB: Bar opens above the level and closes below it
When a breakout occurs, the level is moved to one-touch levels
If no breakout occurs, the level remains in zero-touch
Special case: For LHPB, if a bar opens below or equal to LHPB price and closes back below it then remove this LHPB price from zero-touch level and also don't promote it to one-touch level. Vice-versa for LLPB.

2. One-Touch Levels:
These are levels that have been broken once
When a bar overlaps with a one-touch level, it checks for a valid retest
A valid retest occurs when:
  For LHPB: Bar opens above the level and closes at or below it (retest from above)
  For LLPB: Bar opens below the level and closes at or above it (retest from below)
If a valid retest occurs and enough time has passed since breakout, the level is added to results. "Enough time" is configurable, set to default 4 hours since breakout time
After a second touch (whether valid retest or not), the level is removed
If no second touch occurs, the level remains in one-touch forever until retested

B) H1 Level Identification
The find_h1_levels_with_classification function identifies H1 levels with additional classification

H1 Level Maintenance Process:

1.Zero-Touch Levels:
Similar to D1, every bar's high and low are added as potential zero-touch levels
When a bar overlaps with a zero-touch level, it checks for a breakout
When a breakout occurs, the level is classified as:
  Spike: If it forms a shooting star (for LLPB) or hammer (for LHPB)
  Swing: If it forms a swing high (for LHPB) or swing low (for LLPB)
The level is then moved to one-touch levels with its classification

2. One-Touch Levels:
Similar to D1, these are levels that have been broken once
When a bar overlaps with a one-touch level, it checks for a valid retest
If a valid retest occurs and enough time has passed since breakout, the level is added to results
After a second touch, the level is removed
If no second touch occurs, the level remains in one-touch


C) Level Relationship and Trade Simulation
The script then establishes relationships between D1 and H1 levels and simulates trades.
Level Relationship Process:
The script creates mappings between D1 and H1 levels that are within a specified distance
It maintains two dictionaries:
d1_to_h1: Maps each D1 level to a list of H1 levels that are close to it
h1_to_d1: Maps each H1 level to a list of D1 levels that are close to it
When simulating trades, it prioritizes H1 levels based on:
  Spike levels (highest priority)
  Swing levels (second priority)
  Basic levels (lowest priority)
Each D1 level is only used once for a trade, and H1 levels are removed after they're used


Summary of Level Bookkeeping
a) Level Creation:
Every bar's high and low are added as potential zero-touch levels
These are the initial candidates for trading levels
b) Level Progression:
Zero-touch → One-touch → Results (or removal)
A level starts as zero-touch
When broken, it becomes one-touch
When retested, it either becomes a valid trade or is removed
c) Level Classification:
H1 levels are classified as spike, swing, or basic
This classification affects trade priority
d) Level Relationships:
D1 and H1 levels are related based on proximity
This relationship is used to find confluence for trade entries
e) Level Removal:
Levels are removed after a second touch (whether valid retest or not)
Used levels are removed to prevent reuse

This bookkeeping system ensures that:
Each level is properly tracked through its lifecycle
Levels are only used once for trades
The most significant levels (spike, swing) are prioritized
Levels with confluence between timeframes are identified
The entire process creates a systematic way to identify, track, and utilize price levels for trading decisions, with a focus on finding high-probability setups based on price action and level confluence.