from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from strategies.types import CreateStrategyRequest, AccountMode
from strategies.store import (
    list_strategies, get_strategy, create_strategy,
    toggle_strategy, delete_strategy
)
from strategies.engine import run_strategy_engine
from alpaca_cfg import get_trading_mode

router = APIRouter(prefix="/api/strategy")


@router.get("")
async def api_list(mode: Optional[AccountMode] = Query(None, description="paper | live (미지정 시 전체)")):
    return await list_strategies(mode=mode)


@router.post("")
async def api_create(req: CreateStrategyRequest):
    if req.action.qty_type == "shares" and not req.action.qty:
        raise HTTPException(400, "qty_type=shares 일 때 qty 필수")
    if req.action.side == "buy" and req.action.qty_type == "all":
        raise HTTPException(400, "buy 전략에 qty_type=all은 사용 불가")
    account_mode = req.account_mode or get_trading_mode()
    try:
        strategy = await create_strategy(req, account_mode=account_mode)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"message": "전략 등록 완료", "strategy": strategy}


@router.get("/{sid}")
async def api_get(sid: str):
    s = await get_strategy(sid)
    if not s:
        raise HTTPException(404, "전략을 찾을 수 없습니다.")
    return s


@router.patch("/{sid}/toggle")
async def api_toggle(sid: str):
    s = await toggle_strategy(sid)
    if not s:
        raise HTTPException(404, "전략을 찾을 수 없습니다.")
    state = "활성화" if s["enabled"] else "비활성화"
    return {"message": f"전략 {state}", "enabled": s["enabled"]}


@router.delete("/{sid}")
async def api_delete(sid: str):
    if not await delete_strategy(sid):
        raise HTTPException(404, "전략을 찾을 수 없습니다.")
    return {"message": "전략 삭제 완료"}


@router.post("/run")
async def api_run():
    await run_strategy_engine()
    return {"message": "전략 엔진 실행 완료"}
