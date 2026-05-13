from fastapi import APIRouter
from market.regime import classify_market_regime

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/regime")
async def get_market_regime():
    return await classify_market_regime()
