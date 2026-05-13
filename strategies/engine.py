"""
Strategy Execution Engine
5분마다 활성화된 전략의 조건을 체크하고 조건 충족 시 주문 실행.
"""

import httpx
from strategies.store import list_strategies, append_log, toggle_strategy, update_peak_price, update_ma_cross_state
from strategies.rsi import calc_rsi
from strategies.ma import calc_ma
from strategies.bb import calc_bollinger
from market.regime import classify_market_regime
from alpaca_cfg import trading_url, alpaca_headers

DATA = "https://data.alpaca.markets"


async def run_strategy_engine():
    strategies = [s for s in await list_strategies() if s["enabled"]]
    if not strategies:
        return

    async with httpx.AsyncClient(timeout=30) as client:
        # 장 운영 여부
        clock = await client.get(f"{trading_url()}/v2/clock", headers=alpaca_headers())
        if clock.status_code != 200 or not clock.json().get("is_open"):
            return

        # 시장 국면 분류 → 포지션 사이징 계수
        regime_info = await classify_market_regime(client)
        size_factor = regime_info.get("size_factor", 1.0)
        print(f"[Strategy Engine] 시장 국면: {regime_info.get('label', '?')} (size_factor={size_factor})")

        # 포지션 맵
        pos_res = await client.get(f"{trading_url()}/v2/positions", headers=alpaca_headers())
        positions = {p["symbol"]: p for p in (pos_res.json() if pos_res.status_code == 200 else [])}

        # 현재가 일괄 조회
        symbols = list({s["symbol"] for s in strategies})
        price_res = await client.get(
            f"{DATA}/v2/stocks/trades/latest",
            params={"symbols": ",".join(symbols), "feed": "iex"},
            headers=alpaca_headers(),
        )
        prices = {}
        if price_res.status_code == 200:
            prices = {sym: t["p"] for sym, t in price_res.json().get("trades", {}).items() if t.get("p")}

        # Fetch daily bars for indicator strategies (RSI + MA Cross)
        # 50 bars: sufficient for RSI(14) Wilder smoothing and MA(20)
        bar_symbols = list({s["symbol"] for s in strategies if s["type"] in ("rsi_threshold", "ma_cross", "bollinger_band")})
        bars_closes: dict[str, list[float]] = {}
        if bar_symbols:
            bars_res = await client.get(
                f"{DATA}/v2/stocks/bars",
                params={"symbols": ",".join(bar_symbols), "timeframe": "1Day", "limit": 50, "sort": "asc"},
                headers=alpaca_headers(),
            )
            if bars_res.status_code == 200:
                for sym, bars in bars_res.json().get("bars", {}).items():
                    bars_closes[sym] = [b["c"] for b in bars]

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

            # Trailing Stop: 고점 갱신
            if stype == "trailing_stop":
                peak = s.get("peak_price")
                if peak is None or price > peak:
                    await update_peak_price(sid, price)
                    s["peak_price"] = price

            # RSI 계산
            rsi = None
            if stype == "rsi_threshold":
                period = cond.get("period", 14)
                rsi = calc_rsi(bars_closes.get(sym, []), period)
                if rsi is not None:
                    print(f"[Strategy] RSI({period}) {sym}: {rsi:.2f}")

            # MA Cross: 이전 상태 대비 변화 감지
            cross_event = None  # "golden" | "dead" | None
            if stype == "ma_cross":
                closes = bars_closes.get(sym, [])
                fast_p = cond.get("fast", 5)
                slow_p = cond.get("slow", 20)
                ma_fast = calc_ma(closes, fast_p)
                ma_slow = calc_ma(closes, slow_p)

                if ma_fast is not None and ma_slow is not None:
                    curr_state = "above" if ma_fast > ma_slow else "below"
                    prev_state = s.get("ma_cross_state")
                    print(f"[Strategy] MA{fast_p}/MA{slow_p} {sym}: fast={ma_fast:.2f} slow={ma_slow:.2f} ({curr_state})")

                    if prev_state is None:
                        # 최초 실행: 현재 상태 저장만 하고 트리거 안 함
                        await update_ma_cross_state(sid, curr_state)
                    elif curr_state != prev_state:
                        # 상태 변화 = 크로스 발생
                        cross_event = "golden" if curr_state == "above" else "dead"
                        await update_ma_cross_state(sid, curr_state)

            # Bollinger Band 계산
            bb = None
            if stype == "bollinger_band":
                period     = cond.get("period", 20)
                multiplier = cond.get("multiplier", 2.0)
                bb = calc_bollinger(bars_closes.get(sym, []), period, multiplier)
                if bb:
                    print(f"[Strategy] BB({period},{multiplier}) {sym}: upper={bb[0]:.2f} mid={bb[1]:.2f} lower={bb[2]:.2f} price={price:.2f}")

            triggered, reason = _evaluate(stype, cond, pos, price, s.get("peak_price"), rsi=rsi, cross_event=cross_event, bb=bb)
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
                if act["side"] == "buy":
                    qty = max(1, int(qty * size_factor))

            # 주문 실행
            order_res = await client.post(
                f"{trading_url()}/v2/orders",
                headers=alpaca_headers(),
                json={"symbol": sym, "qty": qty, "side": act["side"],
                      "type": "market", "time_in_force": "day"},
            )

            if order_res.status_code == 200:
                await append_log(sid, sym, act["side"], qty, reason, "executed",
                                 order_id=order_res.json().get("id"))
                # 1회 실행 후 비활성화
                if stype in ("take_profit", "price_target", "trailing_stop", "rsi_threshold", "ma_cross", "bollinger_band"):
                    await toggle_strategy(sid)
                print(f"[Strategy] ✅ {sym} {act['side']} {qty}주 | {reason}")
            else:
                await append_log(sid, sym, act["side"], qty, reason, "failed",
                                 error=order_res.text)
                print(f"[Strategy] ❌ {sym} {act['side']} 실패 | {order_res.text}")


def _evaluate(stype: str, cond: dict, pos: dict | None, price: float, peak_price: float | None = None, rsi: float | None = None, cross_event: str | None = None, bb: tuple | None = None) -> tuple[bool, str]:
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

    elif stype == "trailing_stop":
        if not pos or not peak_price:
            return False, ""
        trail_pct = cond.get("trail_pct", 7.0)
        drop_pct  = (peak_price - price) / peak_price * 100
        if drop_pct >= trail_pct:
            return True, f"고점 ${peak_price:.2f} 대비 -{drop_pct:.1f}% 하락 (Trailing {trail_pct}%)"

    elif stype == "rsi_threshold":
        if rsi is None:
            return False, ""
        threshold = cond.get("threshold", 30)
        direction = cond.get("direction", "below")
        if direction == "below" and rsi <= threshold:
            return True, f"RSI {rsi:.1f} ≤ {threshold} (과매도 신호)"
        if direction == "above" and rsi >= threshold:
            return True, f"RSI {rsi:.1f} ≥ {threshold} (과매수 신호)"

    elif stype == "ma_cross":
        if cross_event is None:
            return False, ""
        direction = cond.get("direction", "golden")
        if cross_event == direction:
            fast_p = cond.get("fast", 5)
            slow_p = cond.get("slow", 20)
            label  = "골든크로스" if cross_event == "golden" else "데드크로스"
            return True, f"MA{fast_p}/MA{slow_p} {label} 발생"

    elif stype == "bollinger_band":
        if bb is None:
            return False, ""
        upper, middle, lower = bb
        direction  = cond.get("direction", "below_lower")
        period     = cond.get("period", 20)
        multiplier = cond.get("multiplier", 2.0)
        if direction == "below_lower" and price <= lower:
            return True, f"현재가 ${price:.2f} ≤ 하단밴드 ${lower:.2f} (BB{period},{multiplier}σ 과매도)"
        if direction == "above_upper" and price >= upper:
            return True, f"현재가 ${price:.2f} ≥ 상단밴드 ${upper:.2f} (BB{period},{multiplier}σ 과매수)"

    return False, ""
