"""
PostgreSQL 초기화 및 헬퍼 (asyncpg)
- finly-backend와 동일한 PostgreSQL 인스턴스를 공유합니다.
"""

import os
import asyncpg

_pool: asyncpg.Pool | None = None  # pylint: disable=invalid-name


async def init_db():
    global _pool  # pylint: disable=global-statement
    _pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=2,
        max_size=10,
    )
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                symbol          TEXT NOT NULL,
                type            TEXT NOT NULL,
                condition       TEXT NOT NULL,
                action          TEXT NOT NULL,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL,
                peak_price      DOUBLE PRECISION,
                account_mode    TEXT NOT NULL DEFAULT 'paper',
                ma_cross_state  TEXT,
                allowed_regimes TEXT DEFAULT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_logs (
                id           BIGSERIAL PRIMARY KEY,
                strategy_id  TEXT NOT NULL,
                time         TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                side         TEXT NOT NULL,
                qty          INTEGER,
                reason       TEXT,
                status       TEXT NOT NULL,
                order_id     TEXT,
                error        TEXT,
                account_mode TEXT NOT NULL DEFAULT 'paper'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_reports (
                id           BIGSERIAL PRIMARY KEY,
                generated_at TEXT NOT NULL,
                content      TEXT NOT NULL,
                positions    TEXT NOT NULL,
                account      TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_strategy
            ON strategies(account_mode, symbol, type, COALESCE(allowed_regimes, ''))
        """)


async def close_db():
    global _pool  # pylint: disable=global-statement
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_db() first")
    return _pool
