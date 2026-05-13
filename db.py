"""
SQLite 초기화 및 헬퍼
- finly-backend의 in-memory dict와 달리 재시작 후에도 전략/로그가 유지됩니다.
"""

import os

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "finly_agent.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                type         TEXT NOT NULL,
                condition    TEXT NOT NULL,   -- JSON
                action       TEXT NOT NULL,   -- JSON
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL,
                peak_price   REAL,
                account_mode TEXT NOT NULL DEFAULT 'paper'
            );

            CREATE TABLE IF NOT EXISTS strategy_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                time        TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                side        TEXT NOT NULL,
                qty         INTEGER,
                reason      TEXT,
                status      TEXT NOT NULL,   -- executed | failed | skipped
                order_id    TEXT,
                error       TEXT,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS portfolio_reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                content      TEXT NOT NULL,
                positions    TEXT NOT NULL,  -- JSON
                account      TEXT NOT NULL   -- JSON
            );
        """)
        await db.commit()
        # 기존 DB 마이그레이션: peak_price 컬럼 추가
        for migration in [
            "ALTER TABLE strategies ADD COLUMN peak_price REAL",
            "ALTER TABLE strategies ADD COLUMN ma_cross_state TEXT",
            "ALTER TABLE strategies ADD COLUMN account_mode TEXT NOT NULL DEFAULT 'paper'",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass  # 이미 존재하면 무시


async def get_db():
    return aiosqlite.connect(DB_PATH)
