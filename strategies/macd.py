"""
MACD (Moving Average Convergence Divergence) calculation.
Returns (macd_line, signal_line, histogram) or None if insufficient data.
Standard EMA: multiplier = 2 / (period + 1), seeded with SMA of first N values.
Requires at least slow + signal bars.
"""


def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1.0 - k))
    return result


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float] | None:
    """Return (macd_line, signal_line, histogram) or None."""
    if len(closes) < slow + signal:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None

    # ema_fast is longer by (slow - fast) elements; align from the end
    trim      = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[trim:], ema_slow)]

    if len(macd_line) < signal:
        return None

    sig_line = _ema(macd_line, signal)
    if not sig_line:
        return None

    trim2     = len(macd_line) - len(sig_line)
    histogram = [m - s for m, s in zip(macd_line[trim2:], sig_line)]

    return macd_line[-1], sig_line[-1], histogram[-1]
