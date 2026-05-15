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
    if req.drop_pct <= 0 or req.drop_pct > 50:
        raise HTTPException(400, "drop_pct는 0~50 사이여야 합니다.")
    if req.max_sell_qty < 1 or req.max_sell_qty > 1000:
        raise HTTPException(400, "max_sell_qty는 1~1000 사이여야 합니다.")
    save_config(req.model_dump())
    return {"message": "워치독 설정 업데이트", "config": req.model_dump()}


@router.post("/watchdog/run")
async def trigger_watchdog():
    await run_watchdog()
    return {"message": "워치독 실행 완료"}


# ── Strategy Recommendations ──────────────────────────────────

@router.get("/regime-recommendations")
async def get_regime_recommendations(symbol: str = ""):
    from agents.recommender import generate_recommendations  # pylint: disable=import-outside-toplevel
    try:
        return await generate_recommendations(symbol.upper() if symbol else None)
    except Exception as e:  # pylint: disable=broad-exception-caught
        raise HTTPException(500, str(e)) from e


# ── Trade History ──────────────────────────────────────────────

@router.get("/trade-history")
async def get_trade_history(
    limit: int = 50, offset: int = 0, status: str = "",
    symbol: str = "", mode: str = "", source: str = "",
):
    conditions = []
    params: list = []

    if status:
        conditions.append("sl.status = ?")
        params.append(status)
    if symbol:
        conditions.append("sl.symbol = ?")
        params.append(symbol.upper())
    if mode:
        conditions.append("sl.account_mode = ?")
        params.append(mode)
    if source == "watchdog":
        conditions.append("sl.strategy_id = 'watchdog'")
    elif source == "strategy":
        conditions.append("sl.strategy_id != 'watchdog'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"""
        SELECT
            sl.id, sl.strategy_id, sl.time, sl.symbol, sl.side, sl.qty,
            sl.reason, sl.status, sl.order_id, sl.error,
            COALESCE(s.name, CASE WHEN sl.strategy_id = 'watchdog' THEN '워치독' ELSE sl.strategy_id END) AS strategy_name,
            COALESCE(s.type, CASE WHEN sl.strategy_id = 'watchdog' THEN 'watchdog' ELSE '' END) AS strategy_type
        FROM strategy_logs sl
        LEFT JOIN strategies s ON sl.strategy_id = s.id
        {where}
        ORDER BY sl.time DESC
        LIMIT ? OFFSET ?
    """
    params += [limit, offset]

    count_query = f"SELECT COUNT(*) FROM strategy_logs sl {where}"
    count_params = params[:-2]

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        rows = [dict(r) for r in await cur.fetchall()]
        cur2 = await db.execute(count_query, count_params)
        total = (await cur2.fetchone())[0]

    return {"total": total, "items": rows}
