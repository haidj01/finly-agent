import os
import pathlib
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db import init_db
from agents.portfolio import run_portfolio_analysis
from agents.watchdog import run_watchdog
from strategies.engine import run_strategy_engine
from market.regime import classify_market_regime
from api.agent import router as agent_router
from api.alpaca import router as alpaca_router
from api.strategy import router as strategy_router
from api.market import router as market_router

app = FastAPI(title="Finly Agent", description="자율 매매 에이전트 서비스")

# 토큰 검증 없이 허용할 경로 (헬스체크·버전은 외부 모니터링도 사용)
_TOKEN_EXEMPT = frozenset({"/health", "/version"})


@app.middleware("http")
async def verify_internal_token(request: Request, call_next):
    """백엔드→에이전트 내부 API 토큰 검증.
    FINLY_INTERNAL_TOKEN 환경변수가 설정된 경우에만 강제한다.
    """
    if request.url.path not in _TOKEN_EXEMPT:
        expected = os.getenv("FINLY_INTERNAL_TOKEN")
        if expected:
            token = request.headers.get("X-Internal-Token")
            if not token or token != expected:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGIN", "http://localhost:8000")],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    allow_headers=["Content-Type"],
)

app.include_router(agent_router)
app.include_router(alpaca_router)
app.include_router(strategy_router)
app.include_router(market_router)

scheduler = AsyncIOScheduler(timezone="America/New_York")


@app.on_event("startup")
async def startup():
    await init_db()

    # 시장 국면 분류: 매일 08:00 ET (장 시작 전 워밍업)
    scheduler.add_job(
        classify_market_regime,
        CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        id="market_regime",
    )
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
    print("  · 시장 국면 분류: 매일 08:00 ET")
    print("  · 포트폴리오 분석: 매일 08:30 ET")
    print("  · 워치독 / 전략엔진: 5분 간격")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


@app.get("/health")
def health():
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)} for j in scheduler.get_jobs()]
    return {"status": "ok", "scheduled_jobs": jobs}


@app.get("/version")
def version():
    v = (pathlib.Path(__file__).parent / "version.txt").read_text().strip()
    return {"service": "finly-agent", "version": v}
