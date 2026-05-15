"""
Strategy Recommender Agent
현재 시장 국면 + 포지션을 Claude에 전달하여 맞춤 전략을 추천.
"""

import asyncio
import json
import logging
import os
import re
import httpx

from market.regime import classify_market_regime
from alpaca_cfg import trading_url, alpaca_headers

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
if not CLAUDE_MODEL.startswith("claude-"):
    raise ValueError(f"CLAUDE_MODEL 환경변수 값이 유효하지 않음: {CLAUDE_MODEL!r}")

_MAX_RETRIES = 2          # 최대 재시도 횟수 (첫 시도 포함 총 3회)
_RETRY_BASE  = 1.0        # exponential backoff 기본 대기(초)

# 국면별 fallback 전략 (Claude API 실패 시 사용)
_FALLBACK: dict[str, list[dict]] = {
    "bearish": [
        {
            "type": "stop_loss", "symbol": "SPY", "name": "하락장 손절",
            "condition": {"drop_pct": 3.0},
            "action": {"side": "sell", "qty_type": "all"},
            "reason": "하락 압력 강함 — 적극적 손절로 추가 손실 방어",
            "allowed_regimes": ["bearish"],
        },
    ],
    "volatile": [
        {
            "type": "bollinger_band", "symbol": "SPY", "name": "변동성장 하단 매수",
            "condition": {"period": 20, "multiplier": 2.0, "direction": "below_lower"},
            "action": {"side": "buy", "qty_type": "notional", "qty": 500},
            "reason": "고변동성 구간에서 하단밴드 터치는 단기 반등 기회",
            "allowed_regimes": ["volatile"],
        },
        {
            "type": "stop_loss", "symbol": "SPY", "name": "변동성장 손절",
            "condition": {"drop_pct": 5.0},
            "action": {"side": "sell", "qty_type": "all"},
            "reason": "변동성 급등 시 손절 강화",
            "allowed_regimes": ["volatile"],
        },
    ],
    "trending": [
        {
            "type": "trailing_stop", "symbol": "SPY", "name": "추세 트레일링 손절",
            "condition": {"trail_pct": 5.0},
            "action": {"side": "sell", "qty_type": "all"},
            "reason": "상승 추세 유지 중 급락 시 익익 보호",
            "allowed_regimes": ["trending"],
        },
    ],
    "ranging": [
        {
            "type": "rsi_threshold", "symbol": "SPY", "name": "횡보장 RSI 과매도 매수",
            "condition": {"period": 14, "threshold": 30, "direction": "below"},
            "action": {"side": "buy", "qty_type": "notional", "qty": 500},
            "reason": "횡보 구간에서 RSI 30 이하는 단기 반등 기회",
            "allowed_regimes": ["ranging"],
        },
        {
            "type": "stop_loss", "symbol": "SPY", "name": "횡보장 손절",
            "condition": {"drop_pct": 5.0},
            "action": {"side": "sell", "qty_type": "all"},
            "reason": "횡보 국면 이탈 시 하락 방어",
            "allowed_regimes": ["ranging"],
        },
    ],
}


def _claude_headers() -> dict:
    return {
        "Content-Type":      "application/json",
        "x-api-key":         os.environ["CLAUDE_API_KEY"],
        "anthropic-version": "2023-06-01",
    }


def _sanitize_symbol(symbol: str) -> str:
    """공백·제어문자 기준으로 첫 토큰만 추출한 뒤 알파벳·숫자만 남겨 prompt injection을 차단한다."""
    first = re.split(r"\s", symbol.strip())[0]
    return re.sub(r"[^A-Z0-9]", "", first.upper())[:10]


def _escape_prompt_field(text: str, max_len: int = 20) -> str:
    """API 응답에서 가져온 문자열을 프롬프트에 삽입하기 전에 정제한다.

    제어 문자를 제거하고 마크다운 메타문자를 이스케이프하여
    프롬프트 구조가 깨지는 것을 방지한다.
    """
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    for ch in ("#", "[", "]", "`", "*"):
        text = text.replace(ch, f"\\{ch}")
    return text[:max_len]


_VALID_TYPES    = frozenset({"stop_loss", "take_profit", "price_target", "trailing_stop",
                              "rsi_threshold", "ma_cross", "bollinger_band"})
_VALID_SIDES    = frozenset({"buy", "sell"})
_VALID_QTY_TYPES = frozenset({"shares", "notional", "all"})
_REQUIRED_FIELDS = ("type", "symbol", "name", "condition", "action", "reason")


def _extract_json(text: str) -> str:
    """응답에서 JSON 배열 문자열을 추출한다.

    우선순위:
    1. ```json … ``` 또는 ``` … ``` 코드블록 (위치 무관)
    2. 첫 '[' 의 매칭 ']' 까지 — 괄호 깊이를 추적하여 rfind 오추출 방지
    3. 원본 텍스트 그대로
    """
    code_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()

    start = text.find("[")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return text


def _validate_recommendations(items: list) -> list:
    """Claude 응답 항목을 스키마 검증하여 유효한 것만 반환한다."""
    valid = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning("추천[%d] dict가 아님: %s", i, type(item))
            continue

        missing = [f for f in _REQUIRED_FIELDS if f not in item]
        if missing:
            logger.warning("추천[%d] 필수 필드 누락: %s", i, missing)
            continue

        if item["type"] not in _VALID_TYPES:
            logger.warning("추천[%d] 유효하지 않은 type: %r", i, item["type"])
            continue

        sym = re.sub(r"[^A-Z0-9]", "", str(item["symbol"]).upper())
        if len(sym) < 1 or len(sym) > 10:
            logger.warning("추천[%d] 유효하지 않은 symbol: %r", i, item["symbol"])
            continue
        item["symbol"] = sym

        action = item.get("action")
        if not isinstance(action, dict):
            logger.warning("추천[%d] action이 dict가 아님", i)
            continue
        if action.get("side") not in _VALID_SIDES:
            logger.warning("추천[%d] 유효하지 않은 side: %r", i, action.get("side"))
            continue
        if action.get("qty_type") not in _VALID_QTY_TYPES:
            logger.warning("추천[%d] 유효하지 않은 qty_type: %r", i, action.get("qty_type"))
            continue
        if action.get("qty_type") == "shares":
            try:
                qty = int(action["qty"]) if action.get("qty") is not None else 1
                if qty < 1 or qty > 1000:
                    logger.warning("추천[%d] qty 범위 초과: %d", i, qty)
                    continue
                action["qty"] = qty
            except (ValueError, TypeError):
                logger.warning("추천[%d] qty가 숫자가 아님: %r", i, action.get("qty"))
                continue
        elif action.get("qty_type") == "notional":
            try:
                notional = float(action["qty"])
                if notional < 10.0 or notional > 5000.0:
                    logger.warning("추천[%d] notional 범위 초과: %.2f", i, notional)
                    continue
                action["qty"] = round(notional, 2)
            except (ValueError, TypeError):
                logger.warning("추천[%d] notional이 숫자가 아님: %r", i, action.get("qty"))
                continue

        if len(str(item.get("reason", ""))) > 300:
            item["reason"] = str(item["reason"])[:300]

        valid.append(item)

    return valid


async def generate_recommendations(symbol: str | None = None) -> dict:
    if symbol:
        symbol = _sanitize_symbol(symbol)

    async with httpx.AsyncClient(timeout=30) as client:
        regime_info = await classify_market_regime(client)
        pos_res     = await client.get(f"{trading_url()}/v2/positions", headers=alpaca_headers())

    positions    = pos_res.json() if pos_res.status_code == 200 else []
    regime       = regime_info.get("regime", "ranging")
    regime_label = regime_info.get("label", "횡보장")
    details      = regime_info.get("details", {})
    signals      = details.get("signals", {})

    prompt = _build_prompt(regime, regime_label, details, signals, positions, symbol)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                res = await client.post(
                    CLAUDE_API_URL,
                    headers=_claude_headers(),
                    json={
                        "model":      CLAUDE_MODEL,
                        "max_tokens": 1500,
                        "messages":   [{"role": "user", "content": prompt}],
                    },
                )

            if res.status_code != 200:
                try:
                    err_type = res.json().get("error", {}).get("type", "")
                except Exception:  # pylint: disable=broad-exception-caught
                    err_type = ""
                if err_type == "model_not_found":
                    logger.error(
                        "Claude 모델을 찾을 수 없음: %r — CLAUDE_MODEL 환경변수 확인 필요", CLAUDE_MODEL
                    )
                else:
                    logger.warning(
                        "Claude API HTTP %s (attempt %d/%d): %s",
                        res.status_code, attempt + 1, _MAX_RETRIES + 1, res.text[:200]
                    )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                    continue
                return _fallback(regime_info, "Claude API 오류", positions)

            raw  = res.json()["content"][0]["text"].strip()
            text = _extract_json(raw)

            try:
                recommendations = json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning("JSON 파싱 실패 (attempt %d/%d): %s | raw=%s",
                               attempt + 1, _MAX_RETRIES + 1, exc, raw[:300])
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                    continue
                return _fallback(regime_info, "JSON 파싱 실패", positions)

            if not isinstance(recommendations, list):
                logger.warning("Claude 응답이 list가 아님 (attempt %d): %s", attempt + 1, type(recommendations))
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                    continue
                return _fallback(regime_info, "응답 형식 오류", positions)

            validated = _validate_recommendations(recommendations)
            if not validated:
                logger.warning("유효한 추천 없음 (attempt %d/%d): 원본 %d개",
                               attempt + 1, _MAX_RETRIES + 1, len(recommendations))
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                    continue
                return _fallback(regime_info, "유효한 추천 없음", positions)

            return {
                "regime":          regime,
                "regime_label":    regime_label,
                "size_factor":     regime_info.get("size_factor", 1.0),
                "details":         details,
                "recommendations": validated,
            }

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("Claude API 네트워크 오류 (attempt %d/%d): %s",
                           attempt + 1, _MAX_RETRIES + 1, exc)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                continue
            return _fallback(regime_info, f"네트워크 오류: {exc}", positions)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Claude API 예상치 못한 오류 (attempt %d/%d)", attempt + 1, _MAX_RETRIES + 1)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BASE * (2 ** attempt))
                continue
            return _fallback(regime_info, f"내부 오류: {exc}", positions)

    return _fallback(regime_info, "재시도 초과", positions)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _build_prompt(regime: str, regime_label: str, details: dict, signals: dict,
                  positions: list, symbol: str | None) -> str:
    pos_lines = "\n".join(
        f"- {_escape_prompt_field(p['symbol'])}: {float(p['qty']):.0f}주 | 평균단가 ${float(p['avg_entry_price']):.2f} | "
        f"현재가 ${float(p['current_price']):.2f} | 손익 {float(p['unrealized_plpc'])*100:.2f}%"
        for p in positions[:20]
    ) if positions else "없음"

    symbol_ctx = ""
    if symbol:
        # symbol은 _sanitize_symbol()을 통과했으므로 안전하지만,
        # 포지션 맵 조회 시 원본 API 심볼과 비교가 필요하므로 그대로 사용
        pos_map = {p["symbol"]: p for p in positions}
        if symbol in pos_map:
            p = pos_map[symbol]
            symbol_ctx = (
                f"\n## 대상 종목\n"
                f"SYMBOL={symbol} | 보유={float(p['qty']):.0f}주 | "
                f"평균단가=${float(p['avg_entry_price']):.2f} | "
                f"현재가=${float(p['current_price']):.2f} | "
                f"손익={float(p['unrealized_plpc'])*100:.2f}%"
            )
        else:
            symbol_ctx = f"\n## 대상 종목\nSYMBOL={symbol} (미보유)"

    symbol_note = (
        f"대상 종목 {symbol}에 맞는 구체적인 파라미터를 사용하세요."
        if symbol else
        "포지션 중 종목을 활용하거나, 포지션이 없으면 SPY를 예시 종목으로 쓰세요."
    )

    return f"""자동매매 시스템을 위한 전략을 추천해주세요.

## 현재 시장 국면: {regime_label} ({regime})
- SPY: ${details.get('price', '-')} | MA5={details.get('ma5', '-')} | MA20={details.get('ma20', '-')}
- RSI(14): {details.get('rsi14', '-')} ({signals.get('rsi_zone', '-')})
- BB폭: {details.get('bb_width_pct', '-')}% ({signals.get('volatility', '-')})
- MA 크로스: {signals.get('ma_cross', '-')}
- ADX(14): {details.get('adx14', '-')} ({signals.get('adx_strength', '-')}) | +DI={details.get('plus_di', '-')} / -DI={details.get('minus_di', '-')} ({signals.get('di_direction', '-')})
- MACD(12,26,9): 히스토그램={details.get('macd_hist', '-')} ({signals.get('macd_momentum', '-')})

## 현재 보유 포지션
{pos_lines}{symbol_ctx}

## 요청
이 시장 국면에서 효과적인 자동매매 전략 3가지를 추천해주세요.
{symbol_note}

**JSON 배열만 응답. 마크다운이나 설명 텍스트 없이 순수 JSON만.**

[
  {{
    "type": "stop_loss|take_profit|price_target|trailing_stop|rsi_threshold|ma_cross|bollinger_band",
    "symbol": "티커",
    "name": "전략명 (간결하게)",
    "condition": {{}},
    "action": {{"side": "buy|sell", "qty_type": "notional|shares|all", "qty": null}},
    "reason": "추천 이유 (1-2문장, 국면과의 연관성 포함)",
    "allowed_regimes": ["{regime}"]
  }}
]

qty_type 규칙: 매수(buy)는 notional 우선($10-$5000). 전량 청산은 all(qty 불필요). 주식 수 지정이 꼭 필요한 경우만 shares(1-1000).

condition 형식 (타입별):
- stop_loss: {{"drop_pct": 5.0}}
- take_profit: {{"gain_pct": 10.0}}
- trailing_stop: {{"trail_pct": 7.0}}
- price_target: {{"target_price": 150.0, "direction": "above|below"}}
- rsi_threshold: {{"period": 14, "threshold": 30, "direction": "below|above"}}
- ma_cross: {{"fast": 5, "slow": 20, "direction": "golden|dead"}}
- bollinger_band: {{"period": 20, "multiplier": 2.0, "direction": "below_lower|above_upper"}}"""


def _adapt_fallback(templates: list[dict], positions: list) -> list[dict]:
    """
    Fallback 템플릿의 SPY 고정을 실제 보유 포지션 기반으로 교체한다.
    - sell 전략: 보유 포지션 종목별 생성 (시장가치 기준 상위 5개)
    - buy  전략: 포지션 무관, SPY 유지 (범용 ETF 기준값)
    """
    if not positions:
        return [dict(t) for t in templates]

    def _market_value(p: dict) -> float:
        try:
            return float(p.get("qty", 0)) * float(p.get("current_price", 0))
        except (ValueError, TypeError):
            return 0.0

    held_syms = [
        p["symbol"] for p in sorted(positions, key=_market_value, reverse=True)
        if p.get("symbol")
    ][:5]

    result = []
    for tmpl in templates:
        if tmpl.get("action", {}).get("side") == "sell" and held_syms:
            for sym in held_syms:
                result.append({**tmpl, "symbol": sym, "name": f"{tmpl['name']} ({sym})"})
        else:
            result.append(dict(tmpl))
    return result


def _fallback(regime_info: dict, reason: str, positions: list | None = None) -> dict:
    regime = regime_info.get("regime", "ranging")
    logger.warning("Fallback 추천 사용: %s (국면=%s)", reason, regime)
    templates = _FALLBACK.get(regime, [])
    return {
        "regime":          regime,
        "regime_label":    regime_info.get("label", "횡보장"),
        "size_factor":     regime_info.get("size_factor", 1.0),
        "details":         regime_info.get("details", {}),
        "recommendations": _adapt_fallback(templates, positions or []),
        "fallback_reason": reason,
    }
