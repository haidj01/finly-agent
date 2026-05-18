"""
Strategy CRUD — PostgreSQL 기반 (asyncpg)
"""

import json
import uuid
import asyncpg
from datetime import datetime, timezone
from db import get_pool


def _parse_strategy_row(r) -> dict:
    d = dict(r)
    d["condition"] = json.loads(d["condition"])
    d["action"]    = json.loads(d["action"])
    d["enabled"]   = bool(d["enabled"])
    raw = d.get("allowed_regimes")
    d["allowed_regimes"] = json.loads(raw) if raw else None
    return d


async def list_strategies(mode: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if mode:
            rows = await conn.fetch(
                "SELECT * FROM strategies WHERE account_mode=$1 AND deleted_at IS NULL ORDER BY created_at DESC", mode
            )
        else:
            rows = await conn.fetch("SELECT * FROM strategies WHERE deleted_at IS NULL ORDER BY created_at DESC")
        return [_parse_strategy_row(r) for r in rows]


async def get_strategy(sid: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM strategies WHERE id=$1 AND deleted_at IS NULL", sid)
        if not row:
            return None
        s = _parse_strategy_row(row)
        logs = await conn.fetch(
            "SELECT * FROM strategy_logs WHERE strategy_id=$1 ORDER BY time DESC LIMIT 50", sid
        )
        s["logs"] = [dict(r) for r in logs]
        return s


async def create_strategy(req, account_mode: str) -> dict:
    sid = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    allowed_regimes_json = json.dumps(sorted(req.allowed_regimes)) if req.allowed_regimes else None
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO strategies
                   (id, name, symbol, type, condition, action, enabled, created_at, account_mode, allowed_regimes)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                sid, req.name, req.symbol.upper(), req.type,
                json.dumps(req.condition), json.dumps(req.action.model_dump()),
                int(req.enabled), now, account_mode, allowed_regimes_json,
            )
        except asyncpg.UniqueViolationError as exc:
            regimes = ", ".join(sorted(req.allowed_regimes)) if req.allowed_regimes else "전체"
            raise ValueError(
                f"이미 동일한 전략이 존재합니다: {account_mode} / {req.symbol.upper()} / {req.type} / {regimes}"
            ) from exc
    return await get_strategy(sid)


async def toggle_strategy(sid: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT enabled, type FROM strategies WHERE id=$1 AND deleted_at IS NULL", sid)
        if not row:
            return None
        new_val = 0 if row["enabled"] else 1
        await conn.execute("UPDATE strategies SET enabled=$1 WHERE id=$2", new_val, sid)
        # trailing_stop 재활성화 시 이전 고점 초기화 — 새 포지션 진입 시 현재가부터 추적 시작
        if new_val == 1 and row["type"] == "trailing_stop":
            await conn.execute("UPDATE strategies SET peak_price=NULL WHERE id=$1", sid)
    return await get_strategy(sid)


async def update_peak_price(sid: str, price: float | None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE strategies SET peak_price=$1 WHERE id=$2", price, sid)


async def update_ma_cross_state(sid: str, state: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE strategies SET ma_cross_state=$1 WHERE id=$2", state, sid)


async def delete_strategy(sid: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE strategies SET deleted_at=$1 WHERE id=$2 AND deleted_at IS NULL", now, sid
        )
        return int(result.split()[-1]) > 0


async def append_log(sid: str, symbol: str, side: str, qty: int,
                     reason: str, status: str, order_id=None, error=None, account_mode: str = "paper"):
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO strategy_logs
               (strategy_id, time, symbol, side, qty, reason, status, order_id, error, account_mode)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            sid, datetime.now(timezone.utc).isoformat(),
            symbol, side, qty, reason, status, order_id, error, account_mode,
        )
