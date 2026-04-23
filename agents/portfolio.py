"""
Portfolio Analysis Agent
매일 08:30 ET에 포지션을 조회하고 Claude로 분석 후 DB에 저장.
"""

import os
import json
import httpx
import asyncio
from datetime import datetime, timezone

import aiosqlite
from db import DB_PATH

PAPER          = "https://paper-api.alpaca.markets"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"


def _alpaca_headers():
    return {
        "APCA-API-KEY-ID":     os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
    }


def _claude_headers():
    return {
        "Content-Type":      "application/json",
        "x-api-key":         os.environ["CLAUDE_API_KEY"],
        "anthropic-version": "2023-06-01",
    }


async def run_portfolio_analysis():
    print(f"[Portfolio] 분석 시작 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    async with httpx.AsyncClient(timeout=90) as client:
        acc_res, pos_res = await asyncio.gather(
            client.get(f"{PAPER}/v2/account",  headers=_alpaca_headers()),
            client.get(f"{PAPER}/v2/positions", headers=_alpaca_headers()),
        )

        account   = acc_res.json() if acc_res.status_code == 200 else {}
        positions = pos_res.json() if pos_res.status_code == 200 else []

        if not positions:
            content = "현재 보유 포지션이 없습니다."
        else:
            pos_lines = "\n".join(
                f"- {p['symbol']}: {p['qty']}주 | 평균단가 ${float(p['avg_entry_price']):.2f} | "
                f"현재가 ${float(p['current_price']):.2f} | 손익 {float(p['unrealized_plpc'])*100:.2f}%"
                for p in positions
            )
            prompt = f"""다음 포트폴리오를 분석해서 한국어로 리포트를 작성해줘.

## 계좌 현황
- 총 자산: ${float(account.get('portfolio_value', 0)):.2f}
- 매수 가능: ${float(account.get('buying_power', 0)):.2f}

## 보유 종목
{pos_lines}

## 리포트 형식
### 1. 포트폴리오 요약
### 2. 종목별 분석 (최신 뉴스 + 추천 액션)
### 3. 오늘의 액션 아이템 (최대 3개)

웹 검색으로 최신 시황을 반영해서 분석해줘."""

            res = await client.post(
                CLAUDE_API_URL,
                headers=_claude_headers(),
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 2048,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            content = next(
                (b["text"] for b in res.json().get("content", []) if b["type"] == "text"),
                "분석 실패"
            ) if res.status_code == 200 else f"Claude 오류: {res.text}"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO portfolio_reports (generated_at, content, positions, account) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), content,
             json.dumps(positions), json.dumps(account)),
        )
        await db.commit()

    print("[Portfolio] 리포트 저장 완료")
