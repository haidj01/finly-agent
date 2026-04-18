"""
SQLite 초기화 및 헬퍼
- finly-backend의 in-memory dict와 달리 재시작 후에도 전략/로그가 유지됩니다.
"""

import aiosqlite

DB_PATH = "finly_agent.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                type        TEXT NOT NULL,
                condition   TEXT NOT NULL,   -- JSON
                action      TEXT NOT NULL,   -- JSON
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
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


async def get_db():
    return aiosqlite.connect(DB_PATH)
