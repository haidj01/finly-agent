# finly-agent

자율 매매 에이전트 서비스. Alpaca Paper Trading API와 Claude AI를 연동하여 포트폴리오 분석, 손실 감시, 규칙 기반 자동 매매를 수행합니다.

## 아키텍처

```
finly-agent/
├── main.py                  # FastAPI 앱 + APScheduler 스케줄러
├── db.py                    # SQLite 초기화 (strategies, logs, reports)
├── agents/
│   ├── portfolio.py         # 포트폴리오 분석 에이전트 (Claude AI)
│   └── watchdog.py          # 손실 임계값 감시 에이전트
├── strategies/
│   ├── engine.py            # 전략 실행 엔진
│   ├── store.py             # 전략 CRUD (SQLite)
│   └── types.py             # Pydantic 모델
├── api/
│   ├── agent.py             # /api/agent 라우터 (리포트, 워치독)
│   └── strategy.py          # /api/strategy 라우터 (전략 관리)
├── terraform/               # AWS ECS Fargate 배포
├── Dockerfile
└── requirements.txt
```

## 주요 기능

### 1. Portfolio Analysis Agent
- **실행 주기**: 매일 08:30 ET
- Alpaca API로 보유 포지션 및 계좌 현황 조회
- Claude AI (claude-sonnet-4-20250514 + web_search 도구)로 한국어 포트폴리오 리포트 생성
- 리포트를 SQLite `portfolio_reports` 테이블에 영속화

### 2. Watchdog Agent
- **실행 주기**: 5분 간격
- 장 운영 중에만 동작 (Alpaca clock API 확인)
- 각 포지션의 손실률이 설정 임계값을 초과하면 자동 시장가 매도
- 설정: `watchdog_config.json` (기본값: `enabled=false`, `drop_pct=5.0`, `max_sell_qty=10`)

### 3. Strategy Engine
- **실행 주기**: 5분 간격
- 활성화된 전략을 순회하며 조건 충족 시 주문 실행
- 지원 전략 타입:

| 타입 | 조건 파라미터 | 설명 |
|------|-------------|------|
| `stop_loss` | `drop_pct` | 손실률 초과 시 매도 |
| `take_profit` | `gain_pct` | 수익률 달성 시 매도 (1회 실행 후 비활성화) |
| `price_target` | `target_price`, `direction` | 목표가 도달 시 매수/매도 (1회 실행 후 비활성화) |

## 환경 변수

| 변수 | 설명 |
|------|------|
| `ALPACA_API_KEY` | Alpaca Paper Trading API 키 |
| `ALPACA_API_SECRET` | Alpaca Paper Trading API 시크릿 |
| `CLAUDE_API_KEY` | Anthropic Claude API 키 |
| `ALLOWED_ORIGIN` | CORS 허용 출처 (기본값: `http://localhost:8000`) |

`.env` 파일 또는 환경 변수로 설정합니다.

## 로컬 실행

```bash
# 의존성 설치
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
  -e CLAUDE_API_KEY=... \
  finly-agent
```

## API 엔드포인트

### Health
```
GET /health
```
서버 상태 및 스케줄된 잡 목록 반환.

### Portfolio
```
GET  /api/agent/report           # 최신 리포트 조회 (없으면 즉시 생성)
GET  /api/agent/reports?limit=10 # 리포트 히스토리 (id, generated_at만 반환 — content 제외)
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

### Strategy
```
GET    /api/strategy             # 전략 목록
POST   /api/strategy             # 전략 등록
GET    /api/strategy/{sid}       # 전략 상세 + 실행 로그 (최근 50건)
PATCH  /api/strategy/{sid}/toggle # 활성화/비활성화 토글
DELETE /api/strategy/{sid}       # 전략 삭제
POST   /api/strategy/run         # 전략 엔진 수동 실행
```

**전략 등록 예시 (stop_loss):**
```json
{
  "name": "AAPL 손절",
  "symbol": "AAPL",
  "type": "stop_loss",
  "condition": { "drop_pct": 5.0 },
  "action": { "side": "sell", "qty_type": "all" },
  "enabled": true
}
```

**전략 등록 예시 (price_target):**
```json
{
  "name": "TSLA 목표가 매도",
  "symbol": "TSLA",
  "type": "price_target",
  "condition": { "target_price": 300.0, "direction": "above" },
  "action": { "side": "sell", "qty": 5, "qty_type": "shares" },
  "enabled": true
}
```

## 데이터베이스

SQLite (`finly_agent.db`) 파일에 세 개의 테이블이 생성됩니다.

| 테이블 | 설명 |
|--------|------|
| `strategies` | 등록된 매매 전략 |
| `strategy_logs` | 전략 및 워치독 실행 이력 |
| `portfolio_reports` | Claude AI 포트폴리오 분석 리포트 |

## AWS 배포 (Terraform + ECS Fargate)

`terraform/` 디렉터리에 ECS Fargate 배포 구성이 포함되어 있습니다.

### 사전 요구사항
- S3 버킷 `finly-terraform-state` (Terraform 상태 저장용)
- DynamoDB 테이블 `finly-terraform-locks` (상태 잠금용)

### 배포 순서

```bash
# 1. ECR 이미지 빌드 & 푸시
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com
docker build -t finly-agent .
docker tag finly-agent:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/finly-agent:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/finly-agent:latest

# 2. Terraform 적용
cd terraform
terraform init
terraform apply
```

### 인프라 구성

- **ECR**: 컨테이너 이미지 저장소 (최근 10개 이미지 유지)
- **ECS Fargate**: 256 CPU / 512 MB 메모리, 단일 태스크
- **Secrets Manager**: `CLAUDE_API_KEY`, `ALPACA_API_KEY`, `ALPACA_API_SECRET` 관리
- **CloudWatch Logs**: `/ecs/finly-agent` (14일 보관)
- **Security Group**: 포트 8001 인바운드 허용

## 기술 스택

- **Runtime**: Python 3.11
- **Web Framework**: FastAPI 0.115
- **Scheduler**: APScheduler 3.10
- **Database**: SQLite (aiosqlite)
- **HTTP Client**: httpx (비동기)
- **AI**: Anthropic Claude API (claude-sonnet-4-20250514 + web_search)
- **Trading**: Alpaca Paper Trading API
- **Deploy**: Docker + AWS ECS Fargate + Terraform
