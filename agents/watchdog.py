"""
Watchdog Agent
5분마다 전체 포지션을 순회하여 설정된 손실 임계값 초과 시 자동 매도.
설정은 watchdog_config.json 파일로 영속화.
"""

import json
import os
import httpx
from datetime import datetime, timezone
from pathlib import Path

from db import get_pool
from alpaca_cfg import trading_url, alpaca_headers, get_trading_mode

CONFIG_PATH    = Path(os.getenv("AGENT_DATA_DIR", "/data")) / "watchdog_config.json"
DEFAULT_MODE_CONFIG = {"enabled": False, "drop_pct": 5.0, "max_sell_qty": 10}
DEFAULT_CONFIG = {
    "paper": DEFAULT_MODE_CONFIG.copy(),
    "live":  DEFAULT_MODE_CONFIG.copy(),
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
        # 구버전 flat 포맷 → per-mode 포맷 마이그레이션
        if "paper" not in cfg and "live" not in cfg:
            mode_cfg = {
                "enabled":      cfg.get("enabled", False),
                "drop_pct":     cfg.get("drop_pct", 5.0),
                "max_sell_qty": cfg.get("max_sell_qty", 10),
            }
            cfg = {"paper": mode_cfg.copy(), "live": mode_cfg.copy()}
            save_config(cfg)
        return cfg
    return {"paper": DEFAULT_MODE_CONFIG.copy(), "live": DEFAULT_MODE_CONFIG.copy()}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


async def run_watchdog():
    cfg = load_config()
    mode = get_trading_mode()
    if not cfg[mode]["enabled"]:
        return

    async with httpx.AsyncClient(timeout=30) as client:
        clock = await client.get(f"{trading_url()}/v2/clock", headers=alpaca_headers())
        if clock.status_code != 200 or not clock.json().get("is_open"):
            return

        pos_res = await client.get(f"{trading_url()}/v2/positions", headers=alpaca_headers())
        if pos_res.status_code != 200:
            return

        pool = get_pool()
        mode_cfg = cfg[mode]
        for pos in pos_res.json():
            sym      = pos["symbol"]
            drop_pct = float(pos.get("unrealized_plpc", 0)) * 100
            threshold = mode_cfg["drop_pct"]

            if drop_pct > -threshold:
                continue

            qty = min(int(float(pos["qty"])), mode_cfg["max_sell_qty"])
            reason = f"손실 {abs(drop_pct):.1f}% (임계값 {threshold}%)"

            order_res = await client.post(
                f"{trading_url()}/v2/orders",
                headers=alpaca_headers(),
                json={"symbol": sym, "qty": qty, "side": "sell",
                      "type": "market", "time_in_force": "day"},
            )

            status   = "executed" if order_res.status_code == 200 else "failed"
            order_id = order_res.json().get("id") if status == "executed" else None
            error    = order_res.text if status == "failed" else None

            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO strategy_logs
                       (strategy_id, time, symbol, side, qty, reason, status, order_id, error, account_mode)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    "watchdog", datetime.now(timezone.utc).isoformat(),
                    sym, "sell", qty, reason, status, order_id, error, get_trading_mode(),
                )

            label = "✅" if status == "executed" else "❌"
            print(f"[Watchdog] {label} {sym} 매도 {qty}주 | {reason}")
