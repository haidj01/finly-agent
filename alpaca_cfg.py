"""
Alpaca API configuration.
Trading mode priority: /data/trading_mode file → ALPACA_MODE env var → "paper"
The file allows runtime switching without restart; the env var sets the deploy-time default.
"""

import os
from pathlib import Path

_MODE_FILE = Path(os.getenv("DB_PATH", "finly_agent.db")).parent / "trading_mode"


def get_trading_mode() -> str:
    if _MODE_FILE.exists():
        return _MODE_FILE.read_text().strip()
    return os.getenv("ALPACA_MODE", "paper")


def set_trading_mode(mode: str) -> None:
    _MODE_FILE.write_text(mode)


def trading_url() -> str:
    if get_trading_mode() == "live":
        return "https://api.alpaca.markets"
    return "https://paper-api.alpaca.markets"


def alpaca_headers() -> dict:
    if get_trading_mode() == "live":
        key    = os.environ.get("ALPACA_LIVE_KEY", "")
        secret = os.environ.get("ALPACA_LIVE_SECRET", "")
        if not key or not secret:
            raise RuntimeError("ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET not configured — add secrets to AWS Secrets Manager")
    else:
        key    = os.environ["ALPACA_API_KEY"]
        secret = os.environ["ALPACA_API_SECRET"]
    return {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
    }
