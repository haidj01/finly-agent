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


async def list_strategies() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM strategies ORDER BY created_at DESC")
        rows = await cur.fetchall()
        return [
            {**dict(r),
             "condition": json.loads(r["condition"]),
             "action":    json.loads(r["action"]),
             "enabled":   bool(r["enabled"])}
            for r in rows
        ]


async def get_strategy(sid: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM strategies WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row:
            return None
        s = {**dict(row),
             "condition": json.loads(row["condition"]),
             "action":    json.loads(row["action"]),
             "enabled":   bool(row["enabled"])}
        log_cur = await db.execute(
            "SELECT * FROM strategy_logs WHERE strategy_id=? ORDER BY time DESC LIMIT 50", (sid,)
        )
        s["logs"] = [dict(r) for r in await log_cur.fetchall()]
        return s


async def create_strategy(req) -> dict:
    sid = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO strategies (id, name, symbol, type, condition, action, enabled, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sid, req.name, req.symbol.upper(), req.type,
             json.dumps(req.condition), json.dumps(req.action.model_dump()),
             int(req.enabled), now),
        )
        await db.commit()
    return await get_strategy(sid)


async def toggle_strategy(sid: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT enabled FROM strategies WHERE id=?", (sid,))
        row = await cur.fetchone()
        if not row:
            return None
        new_val = 0 if row[0] else 1
        await db.execute("UPDATE strategies SET enabled=? WHERE id=?", (new_val, sid))
        await db.commit()
    return await get_strategy(sid)


async def delete_strategy(sid: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM strategies WHERE id=?", (sid,))
        await db.commit()
        return cur.rowcount > 0


async def append_log(sid: str, symbol: str, side: str, qty: int,
                     reason: str, status: str, order_id=None, error=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO strategy_logs
               (strategy_id, time, symbol, side, qty, reason, status, order_id, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, datetime.now(timezone.utc).isoformat(),
             symbol, side, qty, reason, status, order_id, error),
        )
        await db.commit()
