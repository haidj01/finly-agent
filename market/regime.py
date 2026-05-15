"""
Market Regime Classification
SPY를 시장 프록시로 사용하여 현재 시장 국면을 분류.
우선순위: 하락장 > 변동성장 > 추세장 > 횡보장
"""

import logging
import httpx
from datetime import datetime, timezone, timedelta

from strategies.rsi import calc_rsi
from strategies.ma import calc_ma
from strategies.bb import calc_bollinger
from alpaca_cfg import alpaca_headers

logger = logging.getLogger(__name__)

DATA = "https://data.alpaca.markets"

REGIME_LABELS = {
    "bearish":  "하락장",
    "volatile": "변동성장",
    "trending": "추세장",
    "ranging":  "횡보장",
}

# Position sizing multipliers per regime (P5에서 사용)
REGIME_SIZE = {
    "bearish":  0.25,   # 극도로 보수적
    "volatile": 0.50,   # 반감
    "trending": 1.00,   # 풀 사이즈
    "ranging":  0.75,   # 소폭 축소
}

_BEARISH_RSI       = 45.0   # RSI < 45 → 하락 압력
_TRENDING_RSI      = 55.0   # RSI > 55 → 상승 모멘텀
_VOLATILE_BB_WIDTH = 8.0    # BB width % > 8% → 고변동성


async def classify_market_regime(client: httpx.AsyncClient | None = None) -> dict:
    """
    현재 시장 국면 분류.
    client를 전달하면 기존 연결 재사용, None이면 새 클라이언트 생성.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30)

    try:
        start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        bars_res = await client.get(
            f"{DATA}/v2/stocks/bars",
            params={"symbols": "SPY", "timeframe": "1Day", "limit": 50, "sort": "asc", "start": start},
            headers=alpaca_headers(),
        )
        if bars_res.status_code != 200:
            logger.warning("Alpaca bars API error: %s %s", bars_res.status_code, bars_res.text)
            return _default("API 오류")

        bars = bars_res.json().get("bars", {}).get("SPY", [])
        if len(bars) < 20:
            logger.warning("Alpaca bars insufficient data: got %d bars (need 20)", len(bars))
            return _default("데이터 부족")

        closes  = [b["c"] for b in bars]
        latest  = closes[-1]
        ma5     = calc_ma(closes, 5)
        ma20    = calc_ma(closes, 20)
        rsi     = calc_rsi(closes, 14)
        bb      = calc_bollinger(closes, 20, 2.0)
        bb_width = round((bb[0] - bb[2]) / bb[1] * 100, 1) if bb else 0.0

        regime  = _classify(latest, ma5, ma20, rsi, bb_width)
        details = {
            "symbol":        "SPY",
            "price":         round(latest, 2),
            "ma5":           round(ma5, 2)  if ma5  else None,
            "ma20":          round(ma20, 2) if ma20 else None,
            "rsi14":         round(rsi, 1)  if rsi  else None,
            "bb_upper":      round(bb[0], 2) if bb else None,
            "bb_middle":     round(bb[1], 2) if bb else None,
            "bb_lower":      round(bb[2], 2) if bb else None,
            "bb_width_pct":  bb_width,
            "signals":       _signals(latest, ma5, ma20, rsi, bb_width),
        }

        return {
            "regime":       regime,
            "label":        REGIME_LABELS[regime],
            "size_factor":  REGIME_SIZE[regime],
            "details":      details,
            "updated_at":   datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.exception("classify_market_regime unexpected error: %s", e)
        return _default("내부 오류")
    finally:
        if own_client:
            await client.aclose()


def _classify(price: float, ma5, ma20, rsi, bb_width: float) -> str:
    rsi_val = rsi or 50.0

    # 1. 하락장: 데드크로스 + 과매도 압력 + MA20 하회
    if ma5 and ma20 and ma5 < ma20 and rsi_val < _BEARISH_RSI and price < ma20:
        return "bearish"

    # 2. 변동성장: 밴드 폭 확대 (공포/불확실성)
    if bb_width > _VOLATILE_BB_WIDTH:
        return "volatile"

    # 3. 추세장: 골든크로스 + 상승 모멘텀 + MA20 상회
    if ma5 and ma20 and ma5 > ma20 and rsi_val > _TRENDING_RSI and price > ma20:
        return "trending"

    # 4. 횡보장 (기본)
    return "ranging"


def _signals(price: float, ma5, ma20, rsi, bb_width: float) -> dict:
    rsi_val = rsi or 50.0
    return {
        "ma_cross":      ("golden" if ma5 and ma20 and ma5 > ma20
                          else "dead" if ma5 and ma20 and ma5 < ma20
                          else "neutral"),
        "rsi_zone":      ("overbought" if rsi_val > 70
                          else "bullish"   if rsi_val > 55
                          else "bearish"   if rsi_val < 45
                          else "oversold"  if rsi_val < 30
                          else "neutral"),
        "price_vs_ma20": "above" if ma20 and price > ma20 else "below",
        "volatility":    "high" if bb_width > _VOLATILE_BB_WIDTH else "normal",
    }


def _default(reason: str) -> dict:
    return {
        "regime":      "ranging",
        "label":       REGIME_LABELS["ranging"],
        "size_factor": REGIME_SIZE["ranging"],
        "details":     {"error": reason},
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }
