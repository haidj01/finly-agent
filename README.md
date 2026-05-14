# finly-agent

자율 매매 에이전트 서비스. Alpaca Trading API와 Claude AI를 연동하여 포트폴리오 분석, 손실 감시, 규칙 기반 자동 매매를 수행합니다. Paper / Live 계좌를 독립적으로 관리합니다.

## 아키텍처

```
finly-agent/
├── main.py                  # FastAPI 앱 + APScheduler 스케줄러
├── db.py                    # SQLite 초기화 (strategies, logs, reports)
├── alpaca_cfg.py            # Alpaca API 설정 (paper/live 모드 전환)
├── agents/
│   ├── portfolio.py         # 포트폴리오 분석 에이전트 (Claude AI)
│   └── watchdog.py          # 손실 임계값 감시 에이전트
├── market/
│   └── regime.py            # 시장 국면 분류 (SPY 기준)
├── strategies/
│   ├── engine.py            # 전략 실행 엔진 (현재 계좌 모드 전략만 실행)
│   ├── store.py             # 전략 CRUD (SQLite)
│   ├── types.py             # Pydantic 모델
│   ├── rsi.py               # Wilder's RSI 계산
│   ├── ma.py                # 단순이동평균(SMA) 계산
│   └── bb.py                # 볼린저밴드 계산
├── api/
│   ├── agent.py             # /api/agent 라우터 (리포트, 워치독, 거래 이력)
│   ├── strategy.py          # /api/strategy 라우터 (전략 관리)
│   └── market.py            # /market 라우터 (시장 국면, 거래 모드)
├── terraform/               # AWS 인프라 (Secrets Manager 등)
├── Dockerfile
└── requirements.txt
```

## 프로세스 흐름

```
[서버 시작]
     │
     ├── init_db()  SQLite 테이블 생성 / 마이그레이션
     └── APScheduler 구동
           │
           ├── 매일 08:00 ET ─── classify_market_regime()
           │                       SPY 50일 바 조회 → MA5/MA20/RSI14/BB 계산
           │                       → bearish(0.25) / volatile(0.50) / trending(1.00) / ranging(0.75)
           │
           ├── 매일 08:30 ET ─── run_portfolio_analysis()
           │                       Alpaca 계좌+포지션 조회
           │                       → Claude AI (web_search) 분석 → portfolio_reports 저장
           │
           ├── 5분 간격 ────────── run_watchdog()
           │                       enabled=false 면 즉시 종료
           │                       장 오픈 확인 → 각 포지션 손실률 체크
           │                       손실 ≥ drop_pct → 시장가 매도 → strategy_logs 저장
           │
           └── 5분 간격 ────────── run_strategy_engine()
                                   현재 trading mode 확인 (paper | live)
                                   해당 모드의 enabled 전략만 조회
                                   장 오픈 확인 → 시장 국면(size_factor) 조회
                                   현재가 + 포지션 + 인디케이터(RSI/MA/BB) 계산
                                   → 조건 평가 → 주문 실행 → 1회성 전략 비활성화
```

## 주요 기능

### 1. 계좌 모드 관리 (Paper / Live)

Paper와 Live 계좌는 독립적으로 운영됩니다.

- **거래 모드 결정 우선순위**: `/data/trading_mode` 파일 → `ALPACA_MODE` 환경 변수 → `paper` 기본값
- **API로 런타임 전환**: 재시작 없이 `PUT /market/trading-mode`로 즉시 전환
- **전략 분리**: 각 전략은 생성 시 `account_mode`가 지정되며, 엔진은 현재 모드의 전략만 실행

```
paper 모드   →  paper-api.alpaca.markets  +  ALPACA_API_KEY / ALPACA_API_SECRET
live  모드   →  api.alpaca.markets         +  ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET
```

### 2. 시장 국면 분류 (Market Regime)

- **실행 주기**: 매일 08:00 ET (장 시작 전), 전략 엔진 실행 시마다 호출
- SPY(S&P500 ETF)의 50일 일별 바를 기준으로 4가지 국면 분류
- 전략 엔진의 매수 수량에 `size_factor`를 곱해 포지션 사이즈 자동 조정

| 국면 | 분류 조건 | size_factor |
|------|----------|-------------|
| **하락장** (bearish) | MA5 < MA20 + RSI < 45 + 가격 < MA20 | 0.25 |
| **변동성장** (volatile) | 볼린저밴드 폭 > 8% | 0.50 |
| **추세장** (trending) | MA5 > MA20 + RSI > 55 + 가격 > MA20 | 1.00 |
| **횡보장** (ranging) | 위 조건 해당 없음 (기본값) | 0.75 |

### 3. Portfolio Analysis Agent

- **실행 주기**: 매일 08:30 ET
- Alpaca API로 보유 포지션 및 계좌 현황 병렬 조회
- Claude AI (`claude-sonnet-4-20250514` + `web_search` 도구)로 한국어 포트폴리오 리포트 생성
  - 포트폴리오 요약 / 종목별 분석(최신 뉴스 반영) / 오늘의 액션 아이템 3개
- 결과를 SQLite `portfolio_reports` 테이블에 영속화

### 4. Watchdog Agent

- **실행 주기**: 5분 간격
- 장 운영 중에만 동작 (Alpaca clock API 확인)
- 각 포지션의 손실률이 설정 임계값을 초과하면 자동 시장가 매도
- 설정: `watchdog_config.json` (기본값: `enabled=false`, `drop_pct=5.0`, `max_sell_qty=10`)
- 실행 이력은 `strategy_logs` 테이블에 `strategy_id="watchdog"`으로 저장

### 5. Strategy Engine

- **실행 주기**: 5분 간격
- **현재 trading mode의 전략만 실행** — paper 모드이면 paper 전략만, live 모드이면 live 전략만 평가
- 활성화된 전략을 순회하며 조건 충족 시 시장가 주문 실행
- 매수 수량 = `qty × size_factor` (시장 국면에 따라 자동 조정)
- 1회성 전략(`take_profit`, `price_target`, `trailing_stop`, `rsi_threshold`, `ma_cross`, `bollinger_band`)은 체결 후 자동 비활성화

**지원 전략 타입 7가지:**

| 타입 | 조건 파라미터 | 설명 | 반복 여부 |
|------|-------------|------|---------|
| `stop_loss` | `drop_pct` | 손실률 초과 시 매도 | 반복 감시 |
| `take_profit` | `gain_pct` | 수익률 달성 시 매도 | 1회 실행 |
| `price_target` | `target_price`, `direction` | 목표가 도달 시 매수/매도 | 1회 실행 |
| `trailing_stop` | `trail_pct` | 고점 대비 N% 하락 시 매도 (`peak_price` DB 갱신) | 1회 실행 |
| `rsi_threshold` | `period`, `threshold`, `direction` | RSI 과매도/과매수 조건 | 1회 실행 |
| `ma_cross` | `fast`, `slow`, `direction` | 골든크로스/데드크로스 발생 시 (`ma_cross_state` DB 추적) | 1회 실행 |
| `bollinger_band` | `period`, `multiplier`, `direction` | 볼린저밴드 상단/하단 이탈 시 | 1회 실행 |

## 환경 변수

| 변수 | 설명 |
|------|------|
| `ALPACA_API_KEY` | Alpaca Paper Trading API 키 |
| `ALPACA_API_SECRET` | Alpaca Paper Trading API 시크릿 |
| `ALPACA_LIVE_KEY` | Alpaca Live Trading API 키 (live 모드 시 필수) |
| `ALPACA_LIVE_SECRET` | Alpaca Live Trading API 시크릿 (live 모드 시 필수) |
| `ALPACA_MODE` | 거래 모드 기본값 (`paper` 또는 `live`, 기본값: `paper`) |
| `CLAUDE_API_KEY` | Anthropic Claude API 키 |
| `ALLOWED_ORIGIN` | CORS 허용 출처 (기본값: `http://localhost:8000`) |
| `DB_PATH` | SQLite DB 파일 경로 (기본값: `finly_agent.db`) |

`.env` 파일 또는 환경 변수로 설정합니다.

### 거래 모드 전환 (재시작 없이)

`trading_mode` 파일이 `DB_PATH`와 같은 디렉터리에 있으면 환경 변수보다 우선 적용됩니다.

```bash
# API로 전환 (권장)
curl -X PUT http://localhost:8001/market/trading-mode \
     -H "Content-Type: application/json" \
     -d '{"mode": "live"}'

# 파일로 직접 전환
echo "live" > /data/trading_mode
```

## 로컬 실행

```bash
# 의존성 설치
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env   # 값 입력 후 저장

# 서버 시작 (포트 8001)
uvicorn main:app --reload --port 8001
```

서버 시작 시 SQLite DB (`finly_agent.db`)가 자동 생성되고 스케줄러가 구동됩니다.

## Docker 실행

```bash
docker build -t finly-agent .
docker run -p 8001:8001 \
  -e ALPACA_API_KEY=... \
  -e ALPACA_API_SECRET=... \
  -e ALPACA_LIVE_KEY=... \
  -e ALPACA_LIVE_SECRET=... \
  -e CLAUDE_API_KEY=... \
  finly-agent
```

## API 엔드포인트

### Health

```
GET /health     # 서버 상태 및 스케줄된 잡 목록
GET /version    # 현재 버전
```

### Market

```
GET /market/regime                          # 현재 시장 국면 조회 (SPY 기준)
GET /market/trading-mode                    # 현재 거래 모드 조회 (paper | live)
PUT /market/trading-mode  {"mode": "live"}  # 거래 모드 변경 (재시작 불필요)
```

### Portfolio

```
GET  /api/agent/report           # 최신 리포트 조회 (없으면 즉시 생성)
GET  /api/agent/reports?limit=10 # 리포트 히스토리 (id, generated_at만 반환)
POST /api/agent/report/generate  # 리포트 즉시 생성
```

### Watchdog

```
GET  /api/agent/watchdog/status  # 설정 + 최근 실행 로그
POST /api/agent/watchdog/config  # 설정 업데이트
POST /api/agent/watchdog/run     # 수동 실행
```

**설정 예시:**
```json
{
  "enabled": true,
  "drop_pct": 5.0,
  "max_sell_qty": 10
}
```

### Trade History

```
GET /api/agent/trade-history
    ?limit=50        # 페이지 크기 (기본값 50)
    &offset=0        # 페이지 오프셋
    &status=executed # 필터: executed | failed | skipped
    &symbol=AAPL     # 필터: 종목 심볼
```

응답에 `strategy_name`, `strategy_type` 필드가 포함되며 워치독 실행 이력도 함께 조회됩니다.

### Strategy

```
GET    /api/strategy                    # 전략 목록 (전체)
GET    /api/strategy?mode=paper         # paper 전략만 조회
GET    /api/strategy?mode=live          # live 전략만 조회
POST   /api/strategy                    # 전략 등록
GET    /api/strategy/{sid}              # 전략 상세 + 실행 로그 (최근 50건)
PATCH  /api/strategy/{sid}/toggle       # 활성화/비활성화 토글
DELETE /api/strategy/{sid}              # 전략 삭제
POST   /api/strategy/run                # 전략 엔진 수동 실행
```

**`account_mode` 필드:**
- `"paper"` — paper 계좌 전략 (기본값: 전략 생성 시점의 현재 거래 모드)
- `"live"` — live 계좌 전략
- 미지정 시 현재 거래 모드로 자동 설정

---

**전략 등록 예시 (stop_loss — paper):**
```json
{
  "name": "AAPL 손절",
  "symbol": "AAPL",
  "type": "stop_loss",
  "condition": { "drop_pct": 5.0 },
  "action": { "side": "sell", "qty_type": "all" },
  "enabled": true,
  "account_mode": "paper"
}
```

**전략 등록 예시 (price_target — live):**
```json
{
  "name": "TSLA 목표가 매도",
  "symbol": "TSLA",
  "type": "price_target",
  "condition": { "target_price": 300.0, "direction": "above" },
  "action": { "side": "sell", "qty": 5, "qty_type": "shares" },
  "enabled": true,
  "account_mode": "live"
}
```

**전략 등록 예시 (trailing_stop):**
```json
{
  "name": "NVDA 추적 손절",
  "symbol": "NVDA",
  "type": "trailing_stop",
  "condition": { "trail_pct": 7.0 },
  "action": { "side": "sell", "qty_type": "all" },
  "enabled": true
}
```

**전략 등록 예시 (rsi_threshold):**
```json
{
  "name": "AAPL RSI 과매도 매수",
  "symbol": "AAPL",
  "type": "rsi_threshold",
  "condition": { "period": 14, "threshold": 30, "direction": "below" },
  "action": { "side": "buy", "qty": 5, "qty_type": "shares" },
  "enabled": true
}
```

**전략 등록 예시 (ma_cross):**
```json
{
  "name": "SPY 골든크로스 매수",
  "symbol": "SPY",
  "type": "ma_cross",
  "condition": { "fast": 5, "slow": 20, "direction": "golden" },
  "action": { "side": "buy", "qty": 10, "qty_type": "shares" },
  "enabled": true
}
```

**전략 등록 예시 (bollinger_band):**
```json
{
  "name": "QQQ 볼린저 하단 매수",
  "symbol": "QQQ",
  "type": "bollinger_band",
  "condition": { "period": 20, "multiplier": 2.0, "direction": "below_lower" },
  "action": { "side": "buy", "qty": 3, "qty_type": "shares" },
  "enabled": true
}
```

## 데이터베이스

SQLite (`finly_agent.db`) 파일에 세 개의 테이블이 생성됩니다.

| 테이블 | 설명 |
|--------|------|
| `strategies` | 등록된 매매 전략 (`account_mode`, `peak_price`, `ma_cross_state` 컬럼 포함) |
| `strategy_logs` | 전략 및 워치독 실행 이력 (`status`: executed / failed / skipped) |
| `portfolio_reports` | Claude AI 포트폴리오 분석 리포트 |

주요 컬럼:

| 컬럼 | 테이블 | 설명 |
|------|--------|------|
| `account_mode` | `strategies` | `paper` 또는 `live` — 엔진 실행 시 현재 모드와 일치하는 전략만 평가 |
| `peak_price` | `strategies` | `trailing_stop` 전략이 고점을 추적하기 위해 5분마다 갱신 |
| `ma_cross_state` | `strategies` | `ma_cross` 전략이 이전 MA 상태(`above`/`below`)를 저장해 크로스 이벤트 감지 |
| `strategy_id` | `strategy_logs` | 워치독 실행 이력은 `"watchdog"` 고정값으로 저장 |

기존 DB는 서버 시작 시 마이그레이션이 자동 적용됩니다 (`account_mode` 컬럼이 없으면 `DEFAULT 'paper'`로 추가).

## AWS 배포

CD 파이프라인은 GitHub Actions OIDC로 AWS에 인증하고, ECR에 이미지를 push한 뒤 EC2로 SSH 배포합니다.

### 배포 흐름

```
main 브랜치 push
  │
  ├── AWS 인증 (OIDC — iam role: finly-github-actions)
  ├── ECR 로그인 → Docker 빌드 & push
  └── EC2 SSH 접속 → docker pull & restart
```

### Secrets Manager

Live 키는 AWS Secrets Manager에서 관리하며 배포 시 컨테이너에 주입됩니다.

| Secret | 내용 |
|--------|------|
| `finly/alpaca-live` | `ALPACA_LIVE_KEY`, `ALPACA_LIVE_SECRET` |
| `finly/claude` | `CLAUDE_API_KEY` |

### 필요한 GitHub Secrets

| Secret | 설명 |
|--------|------|
| `EC2_SSH_KEY` | EC2 인스턴스 접속용 PEM 키 |
| `EC2_HOST` | EC2 퍼블릭 IP 또는 도메인 |
| `ECR_REGISTRY` | ECR 레지스트리 URI |

## 기술 스택

- **Runtime**: Python 3.11
- **Web Framework**: FastAPI 0.115
- **Scheduler**: APScheduler 3.10
- **Database**: SQLite (aiosqlite)
- **HTTP Client**: httpx (비동기)
- **AI**: Anthropic Claude API (claude-sonnet-4-20250514 + web_search)
- **Trading**: Alpaca Paper / Live Trading API
- **Deploy**: Docker + AWS ECR + EC2 (GitHub Actions OIDC)
