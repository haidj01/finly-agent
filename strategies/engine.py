"""
Strategy Execution Engine
5분마다 활성화된 전략의 조건을 체크하고 조건 충족 시 주문 실행.
"""

import os
import httpx
from strategies.store import list_strategies, append_log, toggle_strategy

PAPER = "https://paper-api.alpaca.markets"
DATA  = "https://data.alpaca.markets"


def _headers():
    return {
        "APCA-API-KEY-ID":     os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
    }


async def run_strategy_engine():
    strategies = [s for s in await list_strategies() if s["enabled"]]
    if not strategies:
        return

    async with httpx.AsyncClient(timeout=30) as client:
        # 장 운영 여부
        clock = await client.get(f"{PAPER}/v2/clock", headers=_headers())
        if clock.status_code != 200 or not clock.json().get("is_open"):
            return

        # 포지션 맵
        pos_res = await client.get(f"{PAPER}/v2/positions", headers=_headers())
        positions = {p["symbol"]: p for p in (pos_res.json() if pos_res.status_code == 200 else [])}

        # 현재가 일괄 조회
        symbols = list({s["symbol"] for s in strategies})
        price_res = await client.get(
            f"{DATA}/v2/stocks/trades/latest",
            params={"symbols": ",".join(symbols), "feed": "iex"},
            headers=_headers(),
        )
        prices = {}
        if price_res.status_code == 200:
            prices = {sym: t["p"] for sym, t in price_res.json().get("trades", {}).items() if t.get("p")}

        for s in strategies:
            sid   = s["id"]
            sym   = s["symbol"]
            stype = s["type"]
            cond  = s["condition"]
            act   = s["action"]
            pos   = positions.get(sym)
            price = prices.get(sym)

            if not price:
                continue

            triggered, reason = _evaluate(stype, cond, pos, price)
            if not triggered:
                continue

            # 수량 결정
            if act["qty_type"] == "all":
                if not pos:
                    await append_log(sid, sym, act["side"], 0, reason, "skipped", error="포지션 없음")
                    continue
                qty = int(float(pos["qty"]))
            else:
                qty = act.get("qty") or 1

            # 주문 실행
            order_res = await client.post(
                f"{PAPER}/v2/orders",
                headers=_headers(),
                json={"symbol": sym, "qty": qty, "side": act["side"],
                      "type": "market", "time_in_force": "day"},
            )

            if order_res.status_code == 200:
                await append_log(sid, sym, act["side"], qty, reason, "executed",
                                 order_id=order_res.json().get("id"))
                # take_profit / price_target은 1회 실행 후 비활성화
                if stype in ("take_profit", "price_target"):
                    await toggle_strategy(sid)
                print(f"[Strategy] ✅ {sym} {act['side']} {qty}주 | {reason}")
            else:
                await append_log(sid, sym, act["side"], qty, reason, "failed",
                                 error=order_res.text)
                print(f"[Strategy] ❌ {sym} {act['side']} 실패 | {order_res.text}")


def _evaluate(stype: str, cond: dict, pos: dict | None, price: float) -> tuple[bool, str]:
    if stype == "stop_loss":
        if not pos:
            return False, ""
        drop_pct  = float(pos.get("unrealized_plpc", 0)) * 100
        threshold = cond.get("drop_pct", 5.0)
        if drop_pct <= -threshold:
            return True, f"손실 {abs(drop_pct):.1f}% (임계값 {threshold}%)"

    elif stype == "take_profit":
        if not pos:
            return False, ""
        gain_pct  = float(pos.get("unrealized_plpc", 0)) * 100
        threshold = cond.get("gain_pct", 10.0)
        if gain_pct >= threshold:
            return True, f"수익 {gain_pct:.1f}% (목표 {threshold}%)"

    elif stype == "price_target":
        target    = cond.get("target_price", 0)
        direction = cond.get("direction", "above")
        if direction == "above" and price >= target:
            return True, f"현재가 ${price:.2f} ≥ 목표가 ${target:.2f}"
        if direction == "below" and price <= target:
            return True, f"현재가 ${price:.2f} ≤ 목표가 ${target:.2f}"

    return False, ""
