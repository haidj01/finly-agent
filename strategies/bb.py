"""
Bollinger Band calculation.
Returns (upper, middle, lower) or None if insufficient data.
"""

import math


def calc_bollinger(closes: list[float], period: int = 20, multiplier: float = 2.0) -> tuple[float, float, float] | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    std = math.sqrt(sum((x - middle) ** 2 for x in window) / period)
    band = multiplier * std
    return middle + band, middle, middle - band
