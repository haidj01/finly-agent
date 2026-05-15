"""
Average Directional Index (ADX) calculation.
Returns (adx, plus_di, minus_di) or None if insufficient data.
Uses Wilder's smoothing (RMA): seed = sum of first N values,
then smooth[i] = smooth[i-1] - smooth[i-1]/period + current.
Requires at least 2*period + 1 bars.
"""


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """ATR/+DM/-DM용 Wilder 평활화: seed=합계, 이후 raw 값을 더함.
    DI 계산 시 분자·분모 모두 같은 스케일이므로 비율이 정확히 산출됨."""
    if len(values) < period:
        return []
    seed = sum(values[:period])
    result = [seed]
    for v in values[period:]:
        result.append(result[-1] - result[-1] / period + v)
    return result


def _wilder_ema(values: list[float], period: int) -> list[float]:
    """DX→ADX용 Wilder EMA: seed=평균, multiplier=1/period.
    DX는 이미 0-100 퍼센트 값이므로 평균 기반 평활화가 필요함."""
    if len(values) < period:
        return []
    k = 1.0 / period
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(result[-1] * (1.0 - k) + v * k)
    return result


def calc_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float, float, float] | None:
    """Return (adx, plus_di, minus_di) or None."""
    n = len(closes)
    if n < period * 2 + 1 or len(highs) != n or len(lows) != n:
        return None

    tr_list, pdm_list, mdm_list = [], [], []
    for i in range(1, n):
        h,  lo, pc = highs[i], lows[i], closes[i - 1]
        ph, pl     = highs[i - 1], lows[i - 1]

        tr  = max(h - lo, abs(h - pc), abs(lo - pc))
        up  = h - ph
        dn  = pl - lo
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0

        tr_list.append(tr)
        pdm_list.append(pdm)
        mdm_list.append(mdm)

    atr  = _wilder_smooth(tr_list,  period)
    apdm = _wilder_smooth(pdm_list, period)
    amdm = _wilder_smooth(mdm_list, period)
    if not atr:
        return None

    pdi_list, mdi_list = [], []
    for atr_v, apdm_v, amdm_v in zip(atr, apdm, amdm):
        if atr_v == 0:
            pdi_list.append(0.0)
            mdi_list.append(0.0)
        else:
            pdi_list.append(100.0 * apdm_v / atr_v)
            mdi_list.append(100.0 * amdm_v / atr_v)

    dx_list = []
    for pdi, mdi in zip(pdi_list, mdi_list):
        denom = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)

    adx_list = _wilder_ema(dx_list, period)
    if not adx_list:
        return None

    return adx_list[-1], pdi_list[-1], mdi_list[-1]
