"""
Strategy Recommender Agent
현재 시장 국면 + 포지션을 Claude에 전달하여 맞춤 전략을 추천.
"""

import json
import os
import httpx

from market.regime import classify_market_regime
from alpaca_cfg import trading_url, alpaca_headers

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"


def _claude_headers():
    return {
        "Content-Type":      "application/json",
        "x-api-key":         os.environ["CLAUDE_API_KEY"],
        "anthropic-version": "2023-06-01",
    }


async def generate_recommendations(symbol: str | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        regime_info = await classify_market_regime(client)
        pos_res = await client.get(f"{trading_url()}/v2/positions", headers=alpaca_headers())
    positions = pos_res.json() if pos_res.status_code == 200 else []

    regime       = regime_info.get("regime", "ranging")
    regime_label = regime_info.get("label", "횡보장")
    details      = regime_info.get("details", {})
    signals      = details.get("signals", {})

    pos_lines = "\n".join(
        f"- {p['symbol']}: {float(p['qty']):.0f}주 | 평균단가 ${float(p['avg_entry_price']):.2f} | "
        f"현재가 ${float(p['current_price']):.2f} | 손익 {float(p['unrealized_plpc'])*100:.2f}%"
        for p in positions
    ) if positions else "없음"

    # 종목이 지정된 경우 포지션에서 현재 보유 여부 확인
    symbol_ctx = ""
    if symbol:
        pos_map = {p["symbol"]: p for p in positions}
        if symbol in pos_map:
            p = pos_map[symbol]
            symbol_ctx = (
                f"\n## 대상 종목\n{symbol} — 현재 보유 중: {float(p['qty']):.0f}주 | "
                f"평균단가 ${float(p['avg_entry_price']):.2f} | "
                f"현재가 ${float(p['current_price']):.2f} | "
                f"손익 {float(p['unrealized_plpc'])*100:.2f}%"
            )
        else:
            symbol_ctx = f"\n## 대상 종목\n{symbol} (미보유)"

    prompt = f"""자동매매 시스템을 위한 전략을 추천해주세요.

## 현재 시장 국면: {regime_label} ({regime})
- SPY: ${details.get('price', '-')} | MA5={details.get('ma5', '-')} | MA20={details.get('ma20', '-')}
- RSI(14): {details.get('rsi14', '-')} ({signals.get('rsi_zone', '-')})
- BB폭: {details.get('bb_width_pct', '-')}% ({signals.get('volatility', '-')})
- MA 크로스: {signals.get('ma_cross', '-')}

## 현재 보유 포지션
{pos_lines}{symbol_ctx}

## 요청
이 시장 국면에서 효과적인 자동매매 전략 3가지를 추천해주세요.
{"대상 종목 " + symbol + "에 맞는 구체적인 파라미터를 사용하세요." if symbol else "포지션 중 종목을 활용하거나, 포지션이 없으면 SPY를 예시 종목으로 쓰세요."}

**JSON 배열만 응답. 마크다운이나 설명 텍스트 없이 순수 JSON만.**

[
  {{
    "type": "stop_loss|take_profit|price_target|trailing_stop|rsi_threshold|ma_cross|bollinger_band",
    "symbol": "티커",
    "name": "전략명 (간결하게)",
    "condition": {{}},
    "action": {{"side": "buy|sell", "qty_type": "shares|all", "qty": null}},
    "reason": "추천 이유 (1-2문장, 국면과의 연관성 포함)",
    "allowed_regimes": ["{regime}"]
  }}
]

condition 형식 (타입별):
- stop_loss: {{"drop_pct": 5.0}}
- take_profit: {{"gain_pct": 10.0}}
- trailing_stop: {{"trail_pct": 7.0}}
- price_target: {{"target_price": 150.0, "direction": "above|below"}}
- rsi_threshold: {{"period": 14, "threshold": 30, "direction": "below|above"}}
- ma_cross: {{"fast": 5, "slow": 20, "direction": "golden|dead"}}
- bollinger_band: {{"period": 20, "multiplier": 2.0, "direction": "below_lower|above_upper"}}"""

    async with httpx.AsyncClient(timeout=60) as client:
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
        raise RuntimeError(f"Claude API 오류: {res.status_code}")

    text = res.json()["content"][0]["text"].strip()
    # Strip optional markdown code fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]

    recommendations = json.loads(text.strip())

    return {
        "regime":        regime,
        "regime_label":  regime_label,
        "size_factor":   regime_info.get("size_factor", 1.0),
        "details":       details,
        "recommendations": recommendations,
    }
