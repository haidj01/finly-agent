"""
Reflect and Remember — 장 마감 후 당일 거래를 분석해 교훈을 추출·저장.

매일 16:05 ET cron으로 run_reflection() 호출.
1. DB에서 당일 전략 실행 로그(status=executed) 조회
2. Alpaca에서 당일 체결 주문 조회
3. Claude(Haiku)에 반성 프롬프트 전송 → JSON 교훈 추출
4. strategy_lessons 테이블에 저장
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from alpaca_cfg import trading_url, alpaca_headers
from db import get_pool, save_lesson
from market.regime import classify_market_regime

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
_REFLECT_MODEL = "claude-haiku-4-5-20251001"
_ET            = ZoneInfo("America/New_York")
_MAX_LESSONS   = 3


async def run_reflection() -> None:
    """장 마감 후 당일 거래를 반성하고 교훈을 DB에 저장한다."""
    now_et     = datetime.now(_ET)
    trade_date = now_et.strftime("%Y-%m-%d")

    try:
        logs = await _get_today_logs(trade_date)
        if not logs:
            logger.info("[reflector] %s 실행된 전략 없음 — 반성 스킵", trade_date)
            return

        account = await _get_account()
        orders  = await _get_today_orders(now_et)
        regime  = await _get_regime()
        pnl     = _calc_daily_pnl(account)

        prompt  = _build_reflection_prompt(trade_date, regime, pnl, account, logs, orders)
        lessons = await _call_claude(prompt)

        saved = 0
        for item in lessons[:_MAX_LESSONS]:
            lesson_text   = (item.get("lesson") or "").strip()
            lesson_regime = (item.get("regime") or regime) or None
            if not lesson_text:
                continue
            await save_lesson(
                lesson=lesson_text,
                trade_date=trade_date,
                regime=lesson_regime,
                pnl_usd=pnl,
            )
            saved += 1
            logger.info("[reflector] 교훈 저장: %.80s", lesson_text)

        logger.info("[reflector] %s 반성 완료 — 교훈 %d개 저장", trade_date, saved)

    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("[reflector] run_reflection 예외 발생")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _get_today_logs(trade_date: str) -> list[dict]:
    """당일 실행된(status=executed) 전략 로그를 DB에서 조회한다."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT strategy_id, symbol, side, qty, reason, status, time
               FROM strategy_logs
               WHERE time >= $1 AND status = 'executed'
               ORDER BY time""",
            f"{trade_date}T00:00:00",
        )
    return [dict(r) for r in rows]


async def _get_account() -> dict:
    """Alpaca 계좌 정보를 조회한다."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"{trading_url()}/v2/account", headers=alpaca_headers())
        if res.status_code == 200:
            return res.json()
        logger.warning("[reflector] 계좌 조회 실패: %s", res.status_code)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[reflector] 계좌 조회 예외 발생", exc_info=True)
    return {}


async def _get_today_orders(now_et: datetime) -> list[dict]:
    """당일 장 시작 이후 체결된 주문 목록을 Alpaca에서 조회한다."""
    open_et  = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    after_ts = open_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(
                f"{trading_url()}/v2/orders",
                params={"status": "closed", "after": after_ts, "limit": 50, "direction": "asc"},
                headers=alpaca_headers(),
            )
        if res.status_code == 200:
            return [o for o in res.json() if o.get("filled_qty", "0") != "0"]
        logger.warning("[reflector] 주문 조회 실패: %s", res.status_code)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[reflector] 주문 조회 예외 발생", exc_info=True)
    return []


async def _get_regime() -> str:
    """현재 시장 국면을 반환한다."""
    try:
        regime_info = await classify_market_regime()
        return regime_info.get("regime", "unknown")
    except Exception:  # pylint: disable=broad-exception-caught
        return "unknown"


def _calc_daily_pnl(account: dict) -> float | None:
    """당일 손익 = equity - last_equity."""
    try:
        return round(float(account["equity"]) - float(account["last_equity"]), 2)
    except (KeyError, ValueError, TypeError):
        return None


def _build_reflection_prompt(
    trade_date: str,
    regime: str,
    pnl: float | None,
    account: dict,
    logs: list[dict],
    orders: list[dict],
) -> str:
    pnl_str    = f"${pnl:+.2f}" if pnl is not None else "N/A"
    equity_str = account.get("equity", "N/A")

    log_lines = "\n".join(
        f"- [{r['time'][:16]}] {r['symbol']} {r['side'].upper()} {r['qty']}주"
        f" | 전략={r['strategy_id']} | 이유={r['reason'] or '-'}"
        for r in logs
    ) or "없음"

    order_lines = "\n".join(
        f"- {o['symbol']} {o['side'].upper()} {o['filled_qty']}주"
        f" @ ${float(o['filled_avg_price']):.2f}"
        for o in orders
        if o.get("filled_avg_price")
    ) or "없음"

    return f"""당신은 자동매매 시스템의 트레이딩 코치입니다.
오늘 거래 내역을 분석하고, 미래 전략 개선을 위한 교훈을 한국어로 추출하세요.

## 날짜: {trade_date} | 시장 국면: {regime}
- 당일 손익: {pnl_str} | 총 자산: ${equity_str}

## 전략 엔진 실행 내역
{log_lines}

## Alpaca 체결 주문
{order_lines}

## 요청
오늘 거래를 객관적으로 평가하고 향후 개선을 위한 구체적 교훈 1~3개를 추출하세요.
교훈은 파라미터 조정, 진입/청산 타이밍, 리스크 관리, 국면별 전략 선택에 관한 실행 가능한 내용이어야 합니다.

JSON 배열만 응답:
[{{"lesson": "교훈 내용 (1-2문장)", "regime": "{regime}"}}]"""


async def _call_claude(prompt: str) -> list[dict]:
    """Claude에 반성 프롬프트를 보내고 교훈 JSON 배열을 파싱한다."""
    api_key = os.environ.get("CLAUDE_API_KEY", "")
    if not api_key:
        logger.warning("[reflector] CLAUDE_API_KEY 없음 — 반성 스킵")
        return []

    payload = {
        "model":      _REFLECT_MODEL,
        "max_tokens": 512,
        "messages":   [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(CLAUDE_API_URL, json=payload, headers=headers)

        if res.status_code != 200:
            logger.warning("[reflector] Claude API %s: %.200s", res.status_code, res.text)
            return []

        content = res.json()["content"][0]["text"]
        match   = re.search(r"\[.*?\]", content, re.DOTALL)
        if not match:
            logger.warning("[reflector] Claude 응답에서 JSON 배열 없음: %.200s", content)
            return []
        return json.loads(match.group())

    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("[reflector] Claude 호출 예외 발생")
        return []
