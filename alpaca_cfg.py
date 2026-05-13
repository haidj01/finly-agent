"""
Alpaca API configuration.
ALPACA_MODE=paper  → paper trading (기본값)
ALPACA_MODE=live   → live trading
"""

import os


def trading_url() -> str:
    if os.getenv("ALPACA_MODE", "paper") == "live":
        return "https://api.alpaca.markets"
    return "https://paper-api.alpaca.markets"


def alpaca_headers() -> dict:
    if os.getenv("ALPACA_MODE", "paper") == "live":
        key    = os.environ["ALPACA_LIVE_KEY"]
        secret = os.environ["ALPACA_LIVE_SECRET"]
    else:
        key    = os.environ["ALPACA_API_KEY"]
        secret = os.environ["ALPACA_API_SECRET"]
    return {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
    }
