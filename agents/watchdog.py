"""
Watchdog Agent
5분마다 전체 포지션을 순회하여 설정된 손실 임계값 초과 시 자동 매도.
설정은 watchdog_config.json 파일로 영속화.
"""

import os
import json
import httpx
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from db import DB_PATH

PAPER          = "https://paper-api.alpaca.markets"
CONFIG_PATH    = Path("watchdog_config.json")
DEFAULT_CONFIG = {"enabled": False, "drop_pct": 5.0, "max_sell_qty": 10}


def _headers():
    return {
        "APCA-API-KEY-ID":     os.environ["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
    }


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


async def run_watchdog():
    cfg = load_config()
    if not cfg["enabled"]:
        return

    async with httpx.AsyncClient(timeout=30) as client:
        clock = await client.get(f"{PAPER}/v2/clock", headers=_headers())
        if clock.status_code != 200 or not clock.json().get("is_open"):
            return

        pos_res = await client.get(f"{PAPER}/v2/positions", headers=_headers())
        if pos_res.status_code != 200:
            return

        for pos in pos_res.json():
            sym      = pos["symbol"]
            drop_pct = float(pos.get("unrealized_plpc", 0)) * 100
            threshold = cfg["drop_pct"]

            if drop_pct > -threshold:
                continue

            qty = min(int(float(pos["qty"])), cfg["max_sell_qty"])
            reason = f"손실 {abs(drop_pct):.1f}% (임계값 {threshold}%)"

            order_res = await client.post(
                f"{PAPER}/v2/orders",
                headers=_headers(),
                json={"symbol": sym, "qty": qty, "side": "sell",
                      "type": "market", "time_in_force": "day"},
            )

            status   = "executed" if order_res.status_code == 200 else "failed"
            order_id = order_res.json().get("id") if status == "executed" else None
            error    = order_res.text if status == "failed" else None

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """INSERT INTO strategy_logs
                       (strategy_id, time, symbol, side, qty, reason, status, order_id, error)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("watchdog", datetime.now(timezone.utc).isoformat(),
                     sym, "sell", qty, reason, status, order_id, error),
                )
                await db.commit()

            label = "✅" if status == "executed" else "❌"
            print(f"[Watchdog] {label} {sym} 매도 {qty}주 | {reason}")
