"""
Simple Moving Average calculation.
Returns None if insufficient data.
"""


def calc_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period
