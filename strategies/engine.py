"""
Strategy Execution Engine
5분마다 활성화된 전략의 조건을 체크하고 조건 충족 시 주문 실행.
"""

import asyncio
import json
import logging
import os
import httpx
from pathlib import Path
from strategies.store import list_strategies, append_log, toggle_strategy, update_peak_price, update_ma_cross_state
from strategies.rsi import calc_rsi
from strategies.ma import calc_ma
from strategies.bb import calc_bollinger
from market.regime import classify_market_regime
from alpaca_cfg import trading_url, alpaca_headers, get_trading_mode

logger = logging.getLogger(__name__)

DATA = "https://data.alpaca.markets"

ENGINE_CONFIG_PATH    = Path(os.getenv("AGENT_DATA_DIR", "/data")) / "engine_config.json"
DEFAULT_ENGINE_CONFIG = {
    "paper": {"enabled": True},
    "live":  {"enabled": True},
}


def load_engine_config() -> dict:
    if ENGINE_CONFIG_PATH.exists():
        return json.loads(ENGINE_CONFIG_PATH.read_text())
    return {"paper": {"enabled": True}, "live": {"enabled": True}}


def save_engine_config(cfg: dict):
    ENGINE_CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

# A: 시장 국면별 엔진 수준 강제 차단
# 2-tuple (type, side)         — direction 무관 차단
# 3-tuple (type, side, direction) — 해당 direction일 때만 차단
#
# bearish:  추세 추종 매수 차단. 손절/익절/trailing은 포지션 보호 목적이므로 유지.
# volatile: MA크로스는 고변동성 구간에서 휩소 오신호가 많아 양방향 차단.
#           bollinger_band 등 변동성 활용 전략과 손절 계열은 유지.
# trending: 추세 반전 베팅인 상단밴드 매도(above_upper)만 차단.
#           하단밴드 청산(below_lower sell)은 손절 보호 목적이므로 허용.
# ranging:  추세 추종(MA크로스) 차단. trailing_stop은 포지션 보호 목적이므로 유지.
REGIME_HARD_BLOCK: dict[str, set[tuple]] = {
    "bearish":  {("ma_cross", "buy"), ("price_target", "buy"), ("rsi_threshold", "buy")},
    "volatile": {("ma_cross", "buy"), ("ma_cross", "sell")},
    "trending": {("bollinger_band", "sell", "above_upper")},
    "ranging":  {("ma_cross", "buy"), ("ma_cross", "sell")},
}


def _is_hard_blocked(blocked: set, stype: str, side: str, direction: str) -> bool:
    """2-tuple(direction 무관) 또는 3-tuple(특정 direction) 차단 규칙을 모두 처리한다."""
    return (stype, side) in blocked or (stype, side, direction) in blocked


async def run_strategy_engine():
    mode = get_trading_mode()
    engine_cfg = load_engine_config()
    if not engine_cfg[mode]["enabled"]:
        return
    strategies = [s for s in await list_strategies(mode=mode) if s["enabled"]]
    if not strategies:
        return
    print(f"[Strategy Engine] 계정 모드: {mode} | 활성 전략 {len(strategies)}개")

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

        # 현재가 + 바 데이터 동시 조회 (시간차 최소화)
        symbols = list({s["symbol"] for s in strategies})
        _bar_types = {"rsi_threshold", "ma_cross", "bollinger_band"}
        bar_symbols = list({s["symbol"] for s in strategies if s["type"] in _bar_types})

        fetch_tasks: list = [
            client.get(f"{DATA}/v2/stocks/trades/latest",
                       params={"symbols": ",".join(symbols), "feed": "iex"},
                       headers=alpaca_headers()),
        ]
        if bar_symbols:
            fetch_tasks.append(
                client.get(f"{DATA}/v2/stocks/bars",
                           params={"symbols": ",".join(bar_symbols), "timeframe": "1Day",
                                   "limit": 50, "sort": "asc", "feed": "iex"},
                           headers=alpaca_headers())
            )

        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        price_res = fetch_results[0]
        prices: dict[str, float] = {}
        if isinstance(price_res, Exception):
            logger.error("현재가 조회 예외 — 전략 엔진 중단: %s", price_res)
            return
        if price_res.status_code != 200:
            logger.error("현재가 조회 HTTP %s — 전략 엔진 중단: %s",
                         price_res.status_code, price_res.text[:200])
            return
        prices = {sym: t["p"] for sym, t in price_res.json().get("trades", {}).items() if t.get("p")}

        bars_closes: dict[str, list[float]] = {}
        if len(fetch_results) > 1:
            bars_res = fetch_results[1]
            if isinstance(bars_res, Exception):
                logger.warning("바 데이터 조회 예외 — RSI/MA/BB 전략 스킵 예정: %s", bars_res)
            elif bars_res.status_code != 200:
                logger.warning("바 데이터 조회 HTTP %s — RSI/MA/BB 전략 스킵 예정: %s",
                               bars_res.status_code, bars_res.text[:200])
            else:
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
                logger.warning("전략[%s] %s 현재가 없음 — trades API 응답에 심볼 누락", sid, sym)
                await append_log(sid, sym, act["side"], 0,
                                 "현재가 조회 불가", "skipped",
                                 error="trades API 응답에 심볼 누락", account_mode=mode)
                continue

            current_regime = regime_info.get("regime", "")

            # A: 엔진 수준 강제 차단
            blocked = REGIME_HARD_BLOCK.get(current_regime, set())
            if _is_hard_blocked(blocked, stype, act["side"], cond.get("direction", "")):
                await append_log(sid, sym, act["side"], 0,
                                 f"시장 국면 차단 ({regime_info.get('label', current_regime)})",
                                 "skipped", account_mode=mode)
                continue

            # B: 전략별 허용 국면 체크
            allowed_regimes = s.get("allowed_regimes")
            if allowed_regimes and current_regime and current_regime not in allowed_regimes:
                await append_log(sid, sym, act["side"], 0,
                                 f"허용되지 않은 국면 ({regime_info.get('label', current_regime)})",
                                 "skipped", account_mode=mode)
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
                closes = bars_closes.get(sym, [])
                required = period + 1
                if len(closes) < required:
                    await append_log(sid, sym, act["side"], 0,
                                     f"RSI({period}) 계산 불가 — 데이터 {len(closes)}개 (최소 {required}개 필요)",
                                     "skipped", account_mode=mode)
                    continue
                rsi = calc_rsi(closes, period)
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
                    print(
                        f"[Strategy] MA{fast_p}/MA{slow_p} {sym}: "
                        f"fast={ma_fast:.2f} slow={ma_slow:.2f} ({curr_state})"
                    )

                    if prev_state is None:
                        # 최초 실행: 현재 상태 저장만 하고 트리거 안 함
                        await update_ma_cross_state(sid, curr_state)
                        s["ma_cross_state"] = curr_state
                    elif curr_state != prev_state:
                        # 상태 변화 = 크로스 발생
                        cross_event = "golden" if curr_state == "above" else "dead"
                        await update_ma_cross_state(sid, curr_state)
                        s["ma_cross_state"] = curr_state

            # Bollinger Band 계산
            bb = None
            if stype == "bollinger_band":
                period     = cond.get("period", 20)
                multiplier = cond.get("multiplier", 2.0)
                bb = calc_bollinger(bars_closes.get(sym, []), period, multiplier)
                if bb:
                    print(
                        f"[Strategy] BB({period},{multiplier}) {sym}: "
                        f"upper={bb[0]:.2f} mid={bb[1]:.2f} lower={bb[2]:.2f} price={price:.2f}"
                    )

            triggered, reason = _evaluate(
                stype, cond, pos, price, s.get("peak_price"),
                rsi=rsi, cross_event=cross_event, bb=bb,
            )
            if not triggered:
                continue

            # 수량 결정
            if act["qty_type"] == "all":
                if not pos:
                    await append_log(sid, sym, act["side"], 0, reason, "skipped", error="포지션 없음", account_mode=mode)
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
                                 order_id=order_res.json().get("id"), account_mode=mode)
                # trailing_stop: 전량 매도 완료 시에만 비활성화 (부분 매도는 잔여 포지션 계속 추적)
                # 나머지 전략: 조건 달성 = 목적 완료이므로 무조건 비활성화
                should_disable = (
                    stype in ("take_profit", "price_target", "rsi_threshold", "ma_cross", "bollinger_band")
                    or (stype == "trailing_stop" and act["qty_type"] == "all")
                )
                if should_disable:
                    await toggle_strategy(sid)
                # trailing_stop 부분 매도: 고점을 초기화해 현재가 기준으로 재추적 시작
                if stype == "trailing_stop" and act["qty_type"] != "all":
                    await update_peak_price(sid, None)
                    s["peak_price"] = None
                print(f"[Strategy] ✅ {sym} {act['side']} {qty}주 | {reason}")
            else:
                await append_log(sid, sym, act["side"], qty, reason, "failed",
                                 error=order_res.text, account_mode=mode)
                print(f"[Strategy] ❌ {sym} {act['side']} 실패 | {order_res.text}")


def _evaluate(
    stype: str, cond: dict, pos: dict | None, price: float,
    peak_price: float | None = None, rsi: float | None = None,
    cross_event: str | None = None, bb: tuple | None = None,
) -> tuple[bool, str]:
    if stype == "stop_loss":
        if not pos:
            return False, ""
        if pos.get("unrealized_plpc") is None:
            logger.warning("stop_loss: %s 포지션에 unrealized_plpc 필드 없음 — 스킵", pos.get("symbol", "?"))
            return False, ""
        drop_pct  = float(pos["unrealized_plpc"]) * 100
        threshold = cond.get("drop_pct", 5.0)
        if drop_pct <= -threshold:
            return True, f"손실 {abs(drop_pct):.1f}% (임계값 {threshold}%)"

    elif stype == "take_profit":
        if not pos:
            return False, ""
        if pos.get("unrealized_plpc") is None:
            logger.warning("take_profit: %s 포지션에 unrealized_plpc 필드 없음 — 스킵", pos.get("symbol", "?"))
            return False, ""
        gain_pct  = float(pos["unrealized_plpc"]) * 100
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
        if not pos:
            return False, ""
        if peak_price is None or peak_price <= 0:
            logger.warning("trailing_stop: peak_price 무효 (%r) — 고점 확정 대기 중", peak_price)
            return False, ""
        if price <= 0:
            logger.warning("trailing_stop: 현재가 무효 (%r)", price)
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
        upper, _, lower = bb
        direction  = cond.get("direction", "below_lower")
        period     = cond.get("period", 20)
        multiplier = cond.get("multiplier", 2.0)
        if direction == "below_lower" and price <= lower:
            return True, f"현재가 ${price:.2f} ≤ 하단밴드 ${lower:.2f} (BB{period},{multiplier}σ 과매도)"
        if direction == "above_upper" and price >= upper:
            return True, f"현재가 ${price:.2f} ≥ 상단밴드 ${upper:.2f} (BB{period},{multiplier}σ 과매수)"

    return False, ""
