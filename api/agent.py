import json
import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import DB_PATH
from agents.portfolio import run_portfolio_analysis
from agents.watchdog import load_config, save_config, run_watchdog

router = APIRouter(prefix="/api/agent")


# ── Portfolio ─────────────────────────────────────────────────

@router.get("/report")
async def get_latest_report():
    """가장 최근 포트폴리오 리포트 조회. 없으면 즉시 생성."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM portfolio_reports ORDER BY generated_at DESC LIMIT 1"
        )
        row = await cur.fetchone()

    if not row:
        await run_portfolio_analysis()
        return await get_latest_report()

    return {
        **dict(row),
        "positions": json.loads(row["positions"]),
        "account":   json.loads(row["account"]),
    }


@router.get("/reports")
async def get_report_history(limit: int = 10):
    """리포트 히스토리 (최근 N개, content 제외)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, generated_at FROM portfolio_reports ORDER BY generated_at DESC LIMIT ?",
            (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


@router.post("/report/generate")
async def generate_report():
    """리포트 즉시 생성."""
    await run_portfolio_analysis()
    return await get_latest_report()


# ── Watchdog ──────────────────────────────────────────────────

class WatchdogConfig(BaseModel):
    enabled:      bool  = True
    drop_pct:     float = 5.0
    max_sell_qty: int   = 10


@router.get("/watchdog/status")
async def get_watchdog_status():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM strategy_logs WHERE strategy_id='watchdog' ORDER BY time DESC LIMIT 20"
        )
        logs = [dict(r) for r in await cur.fetchall()]
    return {"config": load_config(), "recent_logs": logs}


@router.post("/watchdog/config")
async def update_watchdog(req: WatchdogConfig):
    if not (0 < req.drop_pct <= 50):
        raise HTTPException(400, "drop_pct는 0~50 사이여야 합니다.")
    if not (1 <= req.max_sell_qty <= 1000):
        raise HTTPException(400, "max_sell_qty는 1~1000 사이여야 합니다.")
    save_config(req.model_dump())
    return {"message": "워치독 설정 업데이트", "config": req.model_dump()}


@router.post("/watchdog/run")
async def trigger_watchdog():
    await run_watchdog()
    return {"message": "워치독 실행 완료"}
