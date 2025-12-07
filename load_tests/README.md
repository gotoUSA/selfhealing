# 🔥 Locust Payment Load & Chaos Test Suite

> Django Shopping Mall의 결제 시스템 성능 및 **데이터 무결성**을 검증하는 부하/카오스 테스트 스위트

## ⚡ Quick Start

```bash
# 1. Docker 서비스 시작
docker compose up -d

# 2. 테스트 데이터 생성
docker compose exec web python manage.py create_load_test_users
docker compose exec web python manage.py create_test_data

# 3. Smoke 테스트 (환경 확인)
docker compose exec web python load_tests/runners/run_stage.py stage0_smoke

# 4. Full 테스트 실행
docker compose exec web python load_tests/runners/run_stage.py --profile full
```

---

## 📍 Test Stages

| Stage | Name | 목적 | Users |
|-------|------|------|-------|
| 0 | Smoke | 환경 정상 확인 | 5 |
| 1 | Happy Load | 정상 성능 측정 | 50~200 |
| 2 | Idempotency | 중복 결제 방지 검증 | 30 |
| 3 | Latency | PG 지연 시뮬레이션 | 50 |
| 4 | Cancel Storm | 결제 직후 취소 폭주 | 50 |
| 5 | Rollback | 재고/포인트 복구 검증 | 30 |
| 6 | Chaos | 랜덤 실패 (3~15%) | 100 |
| 7 | Race | 동시 결제 경쟁 | 50 |
| 8 | Webhook | Webhook 신뢰성 | 30 |
| 9 | Soak | 장시간 안정성 | 100 |

> 📖 상세 설계: [docs/CHAOS_TEST_DESIGN.md](docs/CHAOS_TEST_DESIGN.md)

---

## 🚀 실행 방법

### 단일 Stage 실행

```bash
# Smoke 테스트
python load_tests/runners/run_stage.py stage0_smoke

# Happy Load 테스트
python load_tests/runners/run_stage.py stage1_happy

# Idempotency 테스트
python load_tests/runners/run_stage.py stage2_idempotent
```

### 프로파일 실행

```bash
# Quick (Stage 0~2)
python load_tests/runners/run_stage.py --profile quick

# Full (Stage 0~5)
python load_tests/runners/run_stage.py --profile full

# Chaos (Stage 6~7)
python load_tests/runners/run_stage.py --profile chaos

# All (Stage 0~8)
python load_tests/runners/run_stage.py --profile all
```

### 직접 Locust 실행

```bash
# Windows (Git Bash)
PYTHONUTF8=1 locust -f load_tests/scenarios/stage1_happy_load.py \
  --host=http://localhost:8000 --users=100 --spawn-rate=20 --run-time=3m

# Docker
docker compose exec web locust -f load_tests/scenarios/stage1_happy_load.py \
  --host=http://web:8000 --users=100 --spawn-rate=20 --run-time=3m --headless
```

### Web UI 모드

```bash
locust -f load_tests/locustfile.py --host=http://localhost:8000
# 브라우저에서 http://localhost:8089 접속
```

---

## 📁 디렉토리 구조

```
load_tests/
├── README.md                 # 이 문서
├── config.py                 # Python 설정
├── locustfile.py             # 메인 진입점 (통합)
│
├── docs/
│   └── CHAOS_TEST_DESIGN.md  # 상세 설계 문서
│
├── scenarios/                # Stage별 시나리오
│   ├── stage0_smoke.py
│   ├── stage1_happy_load.py
│   └── ...
│
├── users/                    # 사용자 행동 패턴
│   ├── browser.py            # 조회만 (65%)
│   ├── shopper.py            # 장바구니 (25%)
│   └── buyer.py              # 결제 (10%)
│
├── utils/                    # 공통 헬퍼
│   ├── login_helper.py
│   ├── product_helper.py
│   └── payment_helper.py
│
├── validators/               # 데이터 무결성 검증
│   ├── stock_validator.py
│   └── point_validator.py
│
├── chaos/                    # 카오스 엔지니어링
│   └── fault_injector.py
│
├── runners/                  # 실행 스크립트
│   ├── config.yaml
│   └── run_stage.py
│
└── reports/                  # 결과 리포트
```

---

## 📊 성능 목표 (SLA)

| API | P95 | P99 | Error Rate |
|-----|-----|-----|------------|
| 상품 목록 | < 800ms | < 1500ms | < 1% |
| 상품 상세 | < 500ms | < 1000ms | < 1% |
| 장바구니 | < 500ms | < 1000ms | < 1% |
| 주문 생성 | < 1000ms | < 2000ms | < 2% |
| **결제** | < 300ms | < 500ms | < 0.1% |

---

## 🔧 사전 준비

### 테스트 데이터

```bash
# Docker
docker compose exec web python manage.py create_load_test_users --count=1000
docker compose exec web python manage.py create_test_data

# 로컬 (가상환경 활성화 필수)
python manage.py create_load_test_users --count=1000
python manage.py create_test_data
```

### 데이터 확인

```bash
docker compose exec web python manage.py shell -c \
  "from django.contrib.auth import get_user_model; \
   User = get_user_model(); \
   print(f'테스트 유저: {User.objects.filter(username__startswith=\"load_test_user_\").count()}')"
```

```# window
docker compose exec web python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); print('Test Users:', User.objects.filter(username__startswith=\"load_test_user_\").count())"
```

---

## 📈 결과 분석

### HTML 리포트

```bash
# reports/ 디렉토리에 자동 생성
open load_tests/reports/stage1_happy.html
```

### 성공 기준

| Stage | 성공 조건 |
|-------|----------|
| Stage 0 | 100% 성공 |
| Stage 1 | P99 < SLA, 에러율 < 1% |
| Stage 2 | 중복 결제 0건 |
| Stage 5 | Rollback 100% 성공 |
| Stage 7 | Race 중복 0건 |

### 모니터링

```bash
# 서버 로그
docker compose logs -f web celery_worker

# DB 커넥션
docker compose exec db psql -U shopping_user -d shopping_db \
  -c "SELECT count(*) FROM pg_stat_activity;"

# Redis
docker compose exec redis redis-cli info memory

# Flower (Celery)
open http://localhost:5555
```

---

## ⚠️ 주의사항

1. **Production 테스트 금지** — Staging 환경에서만 실행
2. **Rate Limiting 해제** — 테스트 중 throttle 설정 해제 또는 화이트리스트
3. **PG Mock 사용** — 실제 PG 연동은 별도 진행
4. **테스트 후 정리** — `load_test_` prefix 데이터 정리

---

## 📚 문서

- [상세 설계 문서](docs/CHAOS_TEST_DESIGN.md) — Stage별 구현 요구사항
- [Locust 공식 문서](https://docs.locust.io/)
- [Chaos Engineering 원칙](https://principlesofchaos.org/)
