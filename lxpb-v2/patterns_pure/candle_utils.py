"""Per-bar candlestick structural helpers.

All functions accept a pandas Series (e.g. from DataFrame.iloc[i]) with
open, high, low, close keys.
"""
from __future__ import annotations

import pandas as pd


def _wick_components(row: pd.Series) -> tuple[float, float, float, float]:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    bar_range  = h - l
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return bar_range, upper_wick, lower_wick


def has_large_upper_wick(row: pd.Series, threshold: float = 0.40) -> bool:
    """True if the upper wick is at least `threshold` fraction of the bar range."""
    bar_range, upper_wick, _ = _wick_components(row)
    return bar_range > 0 and upper_wick > bar_range * threshold


def has_large_lower_wick(row: pd.Series, threshold: float = 0.40) -> bool:
    """True if the lower wick is at least `threshold` fraction of the bar range."""
    bar_range, _, lower_wick = _wick_components(row)
    return bar_range > 0 and lower_wick > bar_range * threshold
