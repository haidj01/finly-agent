"""
Strategy CRUD — SQLite 기반 (재시작 후에도 전략 유지)
"""

import json
import uuid
import aiosqlite
from datetime import datetime, timezone
from db import DB_PATH


def _row_to_dict(row, cursor) -> dict:
    cols = [d[0] for d in cursor.description]
    d = dict(zip(cols, row))
    d["condition"] = json.loads(d["condition"])
    d["action"]    = json.loads(d["action"])
    d["enabled"]   = bool(d["enabled"])
    return d


def _parse_strategy_row(r) -> dict:
    d = {**dict(r),
         "condition": json.loads(r["condition"]),
         "action":    json.loads(r["action"]),
         "enabled":   bool(r["enabled"])}
    raw = d.get("allowed_regimes")
    d["allowed_regimes"] = json.loads(raw) if raw else None
    return d


async def list_strategies(mode: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if mode:
            cur = await db.execute(
                "SELECT * FROM strategies WHERE account_mode=? ORDER BY created_at DESC", (mode,)
            )
        else:
            cur = await db.execute("SELECT * FROM strategies ORDER BY created_at DESC")
        rows = await cur.fetchall()
        return [_parse_strategy_row(r) for r in rows]


async def get_strategy(sid: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM strategies WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row:
            return None
        s = _parse_strategy_row(row)
        log_cur = await db.execute(
            "SELECT * FROM strategy_logs WHERE strategy_id=? ORDER BY time DESC LIMIT 50", (sid,)
        )
        s["logs"] = [dict(r) for r in await log_cur.fetchall()]
        return s


async def create_strategy(req, account_mode: str) -> dict:
    sid = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    allowed_regimes_json = json.dumps(req.allowed_regimes) if req.allowed_regimes else None
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO strategies
                   (id, name, symbol, type, condition, action, enabled, created_at, account_mode, allowed_regimes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (sid, req.name, req.symbol.upper(), req.type,
                 json.dumps(req.condition), json.dumps(req.action.model_dump()),
                 int(req.enabled), now, account_mode, allowed_regimes_json),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ValueError(
                f"이미 동일한 전략이 존재합니다: {account_mode} / {req.symbol.upper()} / {req.type}"
            )
    return await get_strategy(sid)


async def toggle_strategy(sid: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT enabled, type FROM strategies WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row:
            return None
        new_val = 0 if row[0] else 1
        await db.execute("UPDATE strategies SET enabled=? WHERE id=?", (new_val, sid))
        # trailing_stop 재활성화 시 이전 고점 초기화 — 새 포지션 진입 시 현재가부터 추적 시작
        if new_val == 1 and row[1] == "trailing_stop":
            await db.execute("UPDATE strategies SET peak_price=NULL WHERE id=?", (sid,))
        await db.commit()
    return await get_strategy(sid)


async def update_peak_price(sid: str, price: float | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE strategies SET peak_price=? WHERE id=?", (price, sid))
        await db.commit()


async def update_ma_cross_state(sid: str, state: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE strategies SET ma_cross_state=? WHERE id=?", (state, sid))
        await db.commit()


async def delete_strategy(sid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM strategies WHERE id=?", (sid,))
        await db.commit()
        return cur.rowcount > 0


async def append_log(sid: str, symbol: str, side: str, qty: int,
                     reason: str, status: str, order_id=None, error=None, account_mode: str = "paper"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO strategy_logs
               (strategy_id, time, symbol, side, qty, reason, status, order_id, error, account_mode)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid, datetime.now(timezone.utc).isoformat(),
             symbol, side, qty, reason, status, order_id, error, account_mode),
        )
        await db.commit()
