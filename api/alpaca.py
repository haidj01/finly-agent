"""
Mode-aware Alpaca account/trading routes.
All endpoints use alpaca_cfg to select paper vs live URL and credentials.
"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from alpaca_cfg import trading_url, alpaca_headers

router = APIRouter(prefix="/api/alpaca")


@router.get("/account")
async def get_account():
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(f"{trading_url()}/v2/account", headers=alpaca_headers())
    if res.status_code != 200:
        raise HTTPException(res.status_code, res.text)
    return res.json()


@router.get("/positions")
async def get_positions():
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(f"{trading_url()}/v2/positions", headers=alpaca_headers())
    if res.status_code != 200:
        raise HTTPException(res.status_code, res.text)
    return res.json()


@router.get("/orders")
async def get_orders(status: str = "all", limit: int = 20):
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            f"{trading_url()}/v2/orders",
            params={"status": status, "limit": limit, "direction": "desc"},
            headers=alpaca_headers(),
        )
    if res.status_code != 200:
        raise HTTPException(res.status_code, res.text)
    return res.json()


class OrderRequest(BaseModel):
    symbol: str
    qty: int
    side: str


@router.post("/orders")
async def place_order(req: OrderRequest):
    if req.qty < 1 or req.qty > 10000:
        raise HTTPException(400, "수량은 1~10,000주 사이여야 합니다.")
    if req.side not in ("buy", "sell"):
        raise HTTPException(400, "side는 buy 또는 sell이어야 합니다.")

    body = {
        "symbol": req.symbol,
        "qty": req.qty,
        "side": req.side,
        "type": "market",
        "time_in_force": "day",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(f"{trading_url()}/v2/orders", headers=alpaca_headers(), json=body)
    data = res.json()
    if res.status_code not in (200, 201):
        raise HTTPException(res.status_code, data.get("message", "주문 실패"))
    return data
