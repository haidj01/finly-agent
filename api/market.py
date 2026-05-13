from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from market.regime import classify_market_regime
from alpaca_cfg import get_trading_mode, set_trading_mode

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/regime")
async def get_market_regime():
    return await classify_market_regime()


class TradingModeRequest(BaseModel):
    mode: str  # "paper" | "live"


@router.get("/trading-mode")
def get_mode():
    return {"mode": get_trading_mode()}


@router.put("/trading-mode")
def update_mode(req: TradingModeRequest):
    if req.mode not in ("paper", "live"):
        raise HTTPException(400, "mode must be 'paper' or 'live'")
    set_trading_mode(req.mode)
    return {"mode": req.mode}
