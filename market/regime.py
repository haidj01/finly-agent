"""
Market Regime Classification
SPY를 시장 프록시로 사용하여 현재 시장 국면을 분류.

기존 AND 조건 방식 대신 신호별 점수를 계산한 뒤 앙상블로 국면을 결정한다.
각 신호(MA 추세, RSI 모멘텀, BB 변동성)는 [-100, +100] 또는 [0, 100] 점수로
정규화되며, 신호 간 일치도로 신뢰도(confidence)를 산출한다.
신뢰도가 낮으면 불확실 국면(ranging)으로 보수 처리한다.
"""

import logging
import time
import httpx
from datetime import datetime, timezone, timedelta

from strategies.rsi import calc_rsi
from strategies.ma import calc_ma
from strategies.bb import calc_bollinger
from strategies.adx import calc_adx
from strategies.macd import calc_macd
from alpaca_cfg import alpaca_headers

logger = logging.getLogger(__name__)

DATA = "https://data.alpaca.markets"

# ---------------------------------------------------------------------------
# 캐시 + 서킷 브레이커
# ---------------------------------------------------------------------------

_CACHE_TTL = 300  # 5분 — 전략 엔진 주기와 일치


class _CircuitBreaker:
    """Alpaca API 연속 실패 시 일시 차단. CLOSED → OPEN → HALF_OPEN → CLOSED."""

    FAILURE_THRESHOLD = 3    # 연속 실패 횟수 임계값
    RECOVERY_TIMEOUT  = 300  # OPEN 유지 시간(초), 이후 HALF_OPEN 전환

    def __init__(self) -> None:
        self._failures  = 0
        self._state     = "closed"
        self._opened_at = 0.0

    @property
    def state(self) -> str:
        if self._state == "open" and time.monotonic() - self._opened_at >= self.RECOVERY_TIMEOUT:
            self._state = "half_open"
        return self._state

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        if self._state != "closed":
            logger.info("Circuit breaker CLOSED (recovered)")
        self._failures = 0
        self._state    = "closed"

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self.FAILURE_THRESHOLD and self._state == "closed":
            self._state     = "open"
            self._opened_at = time.monotonic()
            logger.warning("Circuit breaker OPEN (consecutive failures=%d)", self._failures)
        elif self._state == "half_open":
            self._state     = "open"
            self._opened_at = time.monotonic()
            logger.warning("Circuit breaker OPEN again (half-open probe failed)")


_cache: dict         = {}   # {"result": dict, "expires_at": float}
_cb   : _CircuitBreaker = _CircuitBreaker()

REGIME_LABELS = {
    "bearish":  "하락장",
    "volatile": "변동성장",
    "trending": "추세장",
    "ranging":  "횡보장",
}

# Position sizing multipliers per regime
REGIME_SIZE = {
    "bearish":  0.25,
    "volatile": 0.50,
    "trending": 1.00,
    "ranging":  0.75,
}

# 앙상블 분류 임계값
_TREND_STRONG   = 40.0   # |trend_score| > 40 → bearish / trending 인정
_VOL_HIGH       = 75.0   # volatility_score > 75 → volatile 우선 (≈ BB폭 8%)
# 신뢰도 판정: 강한 신호 기준 (충돌 심각도 계산에 사용)
_CONF_HIGH_MIN  = 60.0   # 이 이상 강도의 신호는 "강한 신호"로 분류


async def classify_market_regime(client: httpx.AsyncClient | None = None) -> dict:
    """
    현재 시장 국면 분류.
    client를 전달하면 기존 연결 재사용, None이면 새 클라이언트 생성.
    결과는 5분간 캐시됨. Alpaca API 연속 실패 시 서킷 브레이커가 작동하여 캐시 또는 기본값을 반환.
    """
    # 1. 서킷 브레이커: OPEN이면 캐시 또는 기본값 즉시 반환
    if not _cb.allow():
        cached = _cache.get("result")
        if cached:
            logger.debug("Circuit breaker OPEN — returning stale cache (regime=%s)", cached.get("regime"))
            return cached
        return _default("서킷 브레이커 작동 중")

    # 2. 캐시 유효 여부 확인
    now = time.monotonic()
    if _cache.get("result") and now < _cache.get("expires_at", 0.0):
        return _cache["result"]

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)

    try:
        start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
        bars_res = await client.get(
            f"{DATA}/v2/stocks/bars",
            params={"symbols": "SPY", "timeframe": "1Day", "limit": 250, "sort": "asc", "start": start},
            headers=alpaca_headers(),
        )
        if bars_res.status_code != 200:
            logger.warning("Alpaca bars API error: %s %s", bars_res.status_code, bars_res.text)
            _cb.failure()
            return _cache.get("result") or _default("API 오류")

        bars = bars_res.json().get("bars", {}).get("SPY", [])
        if len(bars) < 30:
            # 데이터 부족은 일시적 상태(장 전/후)이므로 서킷 브레이커에는 포함하지 않음
            logger.warning("Alpaca bars insufficient data: got %d bars (need 30)", len(bars))
            return _cache.get("result") or _default("데이터 부족")

        for i, b in enumerate(bars):
            for field in ("c", "h", "l"):
                if field not in b:
                    logger.warning("Bar[%d] missing field '%s'", i, field)
                    return _cache.get("result") or _default("바 데이터 필드 누락")
                try:
                    val = float(b[field])
                except (ValueError, TypeError):
                    logger.warning("Bar[%d] field '%s' not numeric: %r", i, field, b[field])
                    return _cache.get("result") or _default("바 데이터 타입 오류")
                if val <= 0:
                    logger.warning("Bar[%d] field '%s' non-positive: %f", i, field, val)
                    return _cache.get("result") or _default("바 데이터 값 오류")
            if not (float(b["l"]) <= float(b["c"]) <= float(b["h"])):
                logger.warning("Bar[%d] OHLC logic error: H=%.4f C=%.4f L=%.4f", i, b["h"], b["c"], b["l"])
                return _cache.get("result") or _default("바 데이터 논리 오류")

        closes   = [b["c"] for b in bars]
        highs    = [b["h"] for b in bars]
        lows     = [b["l"] for b in bars]
        latest   = closes[-1]
        ma5      = calc_ma(closes, 5)
        ma20     = calc_ma(closes, 20)
        rsi      = calc_rsi(closes, 14)
        bb       = calc_bollinger(closes, 20, 2.0)
        bb_width = round((bb[0] - bb[2]) / bb[1] * 100, 1) if bb else 0.0
        adx_res  = calc_adx(highs, lows, closes, 14)
        adx_val, plus_di, minus_di = adx_res if adx_res else (None, None, None)
        macd_res = calc_macd(closes, 12, 26, 9)
        macd_line, macd_signal, macd_hist = macd_res if macd_res else (None, None, None)

        scores     = _calc_scores(latest, ma5, ma20, rsi, bb_width, adx_val, plus_di, minus_di, macd_hist)
        confidence = _calc_confidence(scores)
        regime     = _classify(scores, confidence)

        details = {
            "symbol":        "SPY",
            "price":         round(latest, 2),
            "ma5":           round(ma5, 2)          if ma5        else None,
            "ma20":          round(ma20, 2)          if ma20       else None,
            "rsi14":         round(rsi, 1)           if rsi        else None,
            "bb_upper":      round(bb[0], 2)         if bb         else None,
            "bb_middle":     round(bb[1], 2)         if bb         else None,
            "bb_lower":      round(bb[2], 2)         if bb         else None,
            "bb_width_pct":  bb_width,
            "adx14":         round(adx_val, 1)       if adx_val    is not None else None,
            "plus_di":       round(plus_di, 1)       if plus_di    is not None else None,
            "minus_di":      round(minus_di, 1)      if minus_di   is not None else None,
            "macd_line":     round(macd_line, 3)     if macd_line  is not None else None,
            "macd_signal":   round(macd_signal, 3)   if macd_signal is not None else None,
            "macd_hist":     round(macd_hist, 3)     if macd_hist  is not None else None,
            "confidence":       _confidence_label(confidence),
            "confidence_score": round(confidence, 3),
            "signal_scores": {
                "ma_trend":   round(scores["ma"], 1),
                "momentum":   round(scores["rsi"], 1),
                "volatility": round(scores["vol"], 1),
                "adx":        round(scores["adx"], 1),
                "macd":       round(scores["macd"], 1),
            },
            "signals":       _signals(latest, ma5, ma20, rsi, bb_width, adx_val, plus_di, minus_di, macd_hist),
        }

        result = {
            "regime":      regime,
            "label":       REGIME_LABELS[regime],
            "size_factor": REGIME_SIZE[regime],
            "details":     details,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        }

        # 3. 성공 시 캐시 갱신 + 서킷 브레이커 리셋
        _cb.success()
        _cache["result"]     = result
        _cache["expires_at"] = time.monotonic() + _CACHE_TTL

        return result

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("classify_market_regime unexpected error: %s", e)
        _cb.failure()
        return _cache.get("result") or _default("내부 오류")
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# 신호 점수 계산
# ---------------------------------------------------------------------------

def _calc_scores(
    price: float, ma5, ma20, rsi, bb_width: float,
    adx=None, plus_di=None, minus_di=None, macd_hist=None,
) -> dict[str, float]:
    """각 신호를 정규화된 점수로 변환한다."""
    return {
        "ma":   _score_ma(ma5, ma20),
        "rsi":  _score_rsi(rsi),
        "vol":  _score_vol(bb_width),
        "adx":  _score_adx(adx, plus_di, minus_di),
        "macd": _score_macd(macd_hist, price),
    }


def _score_ma(ma5, ma20) -> float:
    """
    MA5/MA20 이격도 기반 추세 강도.  [-100, +100]
    양수 = 상승 추세, 음수 = 하락 추세.
    diff 0.5% → ±10, diff 5% → ±100 (선형, clamped).
    """
    if not ma5 or not ma20:
        return 0.0
    diff_pct = (ma5 - ma20) / ma20 * 100  # 이격률 (%)
    return max(-100.0, min(100.0, diff_pct * 20.0))


def _score_rsi(rsi) -> float:
    """
    RSI 모멘텀 강도.  [-100, +100]
    RSI 50 = 0, RSI 70 = +100, RSI 30 = -100 (선형 스케일).
    """
    if rsi is None:
        return 0.0
    if rsi >= 70:
        return 100.0
    if rsi <= 30:
        return -100.0
    return (rsi - 50.0) / 20.0 * 100.0


def _score_vol(bb_width: float) -> float:
    """
    볼린저 밴드 폭 기반 변동성 강도.  [0, +100]
    폭 2% = 0, 폭 10% = 100 (선형, clamped).
    """
    return max(0.0, min(100.0, (bb_width - 2.0) / 8.0 * 100.0))


def _score_adx(adx, plus_di, minus_di) -> float:
    """
    ADX 추세 강도 × 방향.  [-100, +100]
    ADX 15 → 0, ADX 40 → 100 (선형, clamped).  방향: +DI > -DI → 양수.
    """
    if adx is None or plus_di is None or minus_di is None:
        return 0.0
    direction = 1.0 if plus_di > minus_di else -1.0
    strength  = max(0.0, min(100.0, (adx - 15.0) / 25.0 * 100.0))
    return direction * strength


def _score_macd(macd_hist, price: float) -> float:
    """
    MACD 히스토그램 기반 모멘텀 강도.  [-100, +100]
    히스토그램을 현재가 대비 비율로 정규화: ±0.3% of price → ±100.
    """
    if macd_hist is None or price == 0:
        return 0.0
    ratio_pct = macd_hist / price * 100
    return max(-100.0, min(100.0, ratio_pct / 0.3 * 100.0))


# ---------------------------------------------------------------------------
# 신뢰도 산출
# ---------------------------------------------------------------------------

def _calc_confidence(scores: dict[str, float]) -> float:
    """
    4개 방향성 신호(MA/RSI/ADX/MACD)의 가중 합의도와 충돌 심각도로
    신뢰도 점수(0.0–1.0)를 반환한다.
    - agreement_ratio : 가중 합의도 (0.5 = 완전 충돌, 1.0 = 완전 합의)
    - conflict_penalty: 양방향 강한 신호 충돌 심각도 (충돌 쌍당 -0.15)
    """
    directional = [scores["ma"], scores["rsi"], scores["adx"], scores["macd"]]

    pos_w   = sum(max(s, 0.0) for s in directional)
    neg_w   = sum(max(-s, 0.0) for s in directional)
    total_w = pos_w + neg_w
    agreement_ratio = max(pos_w, neg_w) / total_w if total_w else 0.5

    strong_pos = sum(1 for s in directional if s >=  _CONF_HIGH_MIN)
    strong_neg = sum(1 for s in directional if s <= -_CONF_HIGH_MIN)
    conflict_penalty = min(strong_pos, strong_neg) * 0.15

    return max(0.0, min(1.0, agreement_ratio - conflict_penalty))


def _confidence_label(score: float) -> str:
    """신뢰도 점수를 표시용 레이블로 변환한다."""
    if score >= 0.75:
        return "high"
    if score <= 0.45:
        return "low"
    return "medium"


# ---------------------------------------------------------------------------
# 앙상블 분류
# ---------------------------------------------------------------------------

def _classify(scores: dict[str, float], confidence: float) -> str:
    """
    점수 기반 앙상블 국면 결정.

    변동성이 높더라도 추세 신호가 강한 하락세면 bearish로 분류한다.
    나머지 추세/하락 판단은 신호 일치도(confidence)가 낮으면 보수적으로 ranging 처리.
    """
    vol_high = scores["vol"] > _VOL_HIGH

    # MA 25% + RSI 20% + ADX 35% + MACD 20% 가중합 → 추세 강도
    # ADX 가중치가 가장 높은 이유: 추세 강도·방향을 직접 측정하는 유일한 지표
    trend_score = (scores["ma"]   * 0.25 + scores["rsi"]  * 0.20
                 + scores["adx"]  * 0.35 + scores["macd"] * 0.20)

    if vol_high:
        # 고변동성 구간이라도 추세 신호가 강한 하락세면 bearish 우선
        if trend_score < -_TREND_STRONG:
            return "bearish"
        return "volatile"

    if confidence <= 0.45:
        return "ranging"

    if trend_score < -_TREND_STRONG:
        return "bearish"
    if trend_score > _TREND_STRONG:
        return "trending"
    return "ranging"


# ---------------------------------------------------------------------------
# 기존 호환 헬퍼
# ---------------------------------------------------------------------------

def _signals(
    price: float, ma5, ma20, rsi, bb_width: float,
    adx=None, plus_di=None, minus_di=None, macd_hist=None,
) -> dict:
    rsi_val = rsi or 50.0
    return {
        "ma_cross":      ("golden" if ma5 and ma20 and ma5 > ma20
                          else "dead"    if ma5 and ma20 and ma5 < ma20
                          else "neutral"),
        "rsi_zone":      ("overbought" if rsi_val > 70
                          else "bullish"   if rsi_val > 55
                          else "oversold"  if rsi_val < 30
                          else "bearish"   if rsi_val < 45
                          else "neutral"),
        "price_vs_ma20": "above" if ma20 and price > ma20 else "below",
        "volatility":    "high"  if bb_width > 8.0 else "normal",
        "adx_strength":  ("strong" if adx and adx > 25 else "weak") if adx is not None else "unknown",
        "di_direction":  ("bullish" if plus_di and minus_di and plus_di > minus_di
                          else "bearish") if plus_di is not None else "unknown",
        "macd_momentum": ("bullish" if macd_hist and macd_hist > 0
                          else "bearish") if macd_hist is not None else "unknown",
    }


def _default(reason: str) -> dict:
    return {
        "regime":      "ranging",
        "label":       REGIME_LABELS["ranging"],
        "size_factor": REGIME_SIZE["ranging"],
        "details":     {"error": reason},
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }
