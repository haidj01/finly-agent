"""
FRED 거시경제 지표 — 24시간 캐시로 시장 국면 분류 보정에 활용.

지표:
  T10Y2Y  - 10Y-2Y 국채 금리 스프레드 (역전 시 경기침체 선행)
  DFF     - Fed Funds Rate (연준 기준금리)
  VIXCLS  - VIX 공포지수 (BB폭보다 직접적인 변동성 측정)

macro_signal:
  "hawkish" — 긴축 환경 (DFF 높음 + T10Y2Y 역전)
               → 기술적 상승 신호 신뢰도 하향 조정
  "dovish"  — 완화 환경 (DFF 낮음 또는 T10Y2Y 가파름)
               → 기술적 하락 신호 신뢰도 하향 조정
  "neutral" — 중간
"""

import logging
import os
import time
from datetime import datetime, timezone
import httpx

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_CACHE_TTL = 86_400  # 24시간 — 지표 변화가 느리므로 충분

_cache: dict = {}  # {"result": dict, "expires_at": float}

# 매크로 신호 판단 임계값
_HAWKISH_RATE_MIN  = 4.0   # DFF >= 4.0% → 고금리 환경
_SPREAD_INVERSION  = 0.0   # T10Y2Y < 0 → 역전 (침체 선행)
_DOVISH_RATE_MAX   = 2.0   # DFF <= 2.0% → 저금리 환경
_SPREAD_STEEP      = 0.5   # T10Y2Y > 0.5% → 정상 스프레드

# trend_score 조정 계수
_HAWKISH_BULL_DAMP = 0.70  # 긴축 시 상승 추세 신호 30% 하향
_DOVISH_BEAR_DAMP  = 0.80  # 완화 시 하락 추세 신호 20% 하향


async def fetch_macro_bias() -> dict:
    """
    FRED 핵심 3개 지표를 조회하고 매크로 편향(macro_signal)을 반환한다.
    24시간 캐시. FRED_API_KEY 환경변수 미설정 시 {"macro_signal": "neutral"} 반환.
    """
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        logger.debug("FRED_API_KEY 미설정 — macro neutral 사용")
        return _neutral("API 키 없음")

    now = time.monotonic()
    if _cache.get("result") and now < _cache.get("expires_at", 0.0):
        return _cache["result"]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            t10y2y = await _fetch_latest(client, "T10Y2Y", api_key)
            dff    = await _fetch_latest(client, "DFF",    api_key)
            vix    = await _fetch_latest(client, "VIXCLS", api_key)

        if t10y2y is None and dff is None:
            logger.warning("FRED 지표 조회 실패 — macro neutral 사용")
            return _cache.get("result") or _neutral("FRED 조회 실패")

        signal = _classify_macro(t10y2y, dff)
        result = {
            "macro_signal":  signal,
            "yield_spread":  t10y2y,
            "fed_rate":      dff,
            "vix":           vix,
            "updated_at":    _now_iso(),
        }

        _cache["result"]     = result
        _cache["expires_at"] = now + _CACHE_TTL

        logger.info(
            "FRED macro: signal=%s T10Y2Y=%.2f DFF=%.2f VIX=%s",
            signal,
            t10y2y if t10y2y is not None else float("nan"),
            dff    if dff    is not None else float("nan"),
            f"{vix:.1f}" if vix is not None else "N/A",
        )
        return result

    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("fetch_macro_bias 예외 발생")
        return _cache.get("result") or _neutral("예외 발생")


def apply_macro_adjustment(trend_score: float, macro_signal: str) -> float:
    """
    regime.py의 trend_score에 매크로 편향을 반영한다.

    hawkish: 상승 신호(trend_score > 0) 신뢰도 30% 하향
    dovish:  하락 신호(trend_score < 0) 신뢰도 20% 하향
    neutral: 변경 없음

    Usage in regime.py _classify():
        from market.macro import apply_macro_adjustment
        trend_score = apply_macro_adjustment(trend_score, macro_bias["macro_signal"])
    """
    if macro_signal == "hawkish" and trend_score > 0:
        return trend_score * _HAWKISH_BULL_DAMP
    if macro_signal == "dovish" and trend_score < 0:
        return trend_score * _DOVISH_BEAR_DAMP
    return trend_score


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _fetch_latest(client: httpx.AsyncClient, series_id: str, api_key: str) -> float | None:
    """FRED 시계열에서 최신 관측값 1개를 조회한다. 실패 시 None 반환."""
    try:
        res = await client.get(
            FRED_BASE,
            params={
                "series_id":  series_id,
                "api_key":    api_key,
                "file_type":  "json",
                "sort_order": "desc",
                "limit":      5,  # 결측값(.) 대비 여유분
            },
        )
        if res.status_code != 200:
            logger.warning("FRED %s HTTP %s", series_id, res.status_code)
            return None

        observations = res.json().get("observations", [])
        for obs in observations:
            val = obs.get("value", ".")
            if val != ".":
                return round(float(val), 4)

        logger.warning("FRED %s: 유효한 관측값 없음", series_id)
        return None

    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("FRED %s 조회 실패", series_id, exc_info=True)
        return None


def _classify_macro(t10y2y: float | None, dff: float | None) -> str:
    """T10Y2Y + DFF 조합으로 매크로 환경을 분류한다."""
    if dff is None and t10y2y is None:
        return "neutral"

    hawkish_score = 0
    dovish_score  = 0

    if dff is not None:
        if dff >= _HAWKISH_RATE_MIN:
            hawkish_score += 1
        elif dff <= _DOVISH_RATE_MAX:
            dovish_score += 1

    if t10y2y is not None:
        if t10y2y < _SPREAD_INVERSION:
            hawkish_score += 1  # 역전 = 침체 선행 = 긴축 환경 강화
        elif t10y2y > _SPREAD_STEEP:
            dovish_score += 1   # 정상 스프레드 = 경기 확장 기대

    if hawkish_score >= 2:
        return "hawkish"
    if dovish_score >= 2:
        return "dovish"
    if hawkish_score > dovish_score:
        return "hawkish"
    if dovish_score > hawkish_score:
        return "dovish"
    return "neutral"


def _neutral(reason: str) -> dict:
    return {
        "macro_signal": "neutral",
        "yield_spread": None,
        "fed_rate":     None,
        "vix":          None,
        "note":         reason,
        "updated_at":   _now_iso(),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
