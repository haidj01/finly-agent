import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db import init_db
from agents.portfolio import run_portfolio_analysis
from agents.watchdog import run_watchdog
from strategies.engine import run_strategy_engine
from api.agent import router as agent_router
from api.strategy import router as strategy_router

app = FastAPI(title="Finly Agent", description="자율 매매 에이전트 서비스")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)

app.include_router(agent_router)
app.include_router(strategy_router)

scheduler = AsyncIOScheduler(timezone="America/New_York")


@app.on_event("startup")
async def startup():
    await init_db()

    # 포트폴리오 분석: 매일 08:30 ET
    scheduler.add_job(
        run_portfolio_analysis,
        CronTrigger(hour=8, minute=30, timezone="America/New_York"),
        id="portfolio_analysis",
    )
    # 워치독 + 전략 엔진: 5분 간격
    scheduler.add_job(run_watchdog,        IntervalTrigger(minutes=5), id="watchdog")
    scheduler.add_job(run_strategy_engine, IntervalTrigger(minutes=5), id="strategy_engine")

    scheduler.start()
    print("[finly-agent] 시작됨 — http://localhost:8001")
    print("  · 포트폴리오 분석: 매일 08:30 ET")
    print("  · 워치독 / 전략엔진: 5분 간격")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


@app.get("/health")
def health():
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()]
    return {"status": "ok", "scheduled_jobs": jobs}
