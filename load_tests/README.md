# 🔥 Locust 부하 테스트

Django Shopping Mall의 성능 및 확장성을 검증하기 위한 부하 테스트 스위트입니다.

## ⚡ 빠른 시작 (Docker 권장)

```bash
# 1. Docker 서비스 시작
docker compose up -d

# 2. 테스트 데이터 생성
docker compose exec web python manage.py create_load_test_users
docker compose exec web python manage.py create_test_data

# 3. 부하 테스트 실행 (100명, 5분)
docker compose exec web locust -f load_tests/locustfile.py --host=http://web:8000 --users=100 --spawn-rate=10 --run-time=5m --headless --html=reports/load_test_100.html
```

> 📌 로컬 환경에서 실행하려면 **반드시 가상환경을 활성화**하세요!

---

## 📋 목차

- [테스트 전략](#-테스트-전략)
- [폴더 구조](#-폴더-구조)
- [사전 준비](#-사전-준비)
- [실행 방법](#-실행-방법)
- [시나리오 설명](#-시나리오-설명)
- [성능 목표 (SLA)](#-성능-목표-sla)
- [결과 분석](#-결과-분석)

---

## 🎯 테스트 전략

### pytest vs Locust 역할 구분

| 구분 | pytest 동시성 테스트 | Locust 부하 테스트 |
|------|---------------------|-------------------|
| **목적** | 데이터 정합성, Race Condition 재현 | 성능 지표, 시스템 한계점 확인 |
| **규모** | 10~50명 수준 | 50~500명 이상 |
| **검증 대상** | 트랜잭션, 락, DB 정합성 | P95/P99 레이턴시, 에러율, TPS |
| **실행 환경** | CI/CD 파이프라인 | 별도 부하 테스트 환경 |

### 테스트 실행 순서

```
1. Smoke Test (10명 × 2분)
   → 기본 동작 확인

2. Load Test (100명 × 5분)
   → 목표 성능 달성 확인

3. Stress Test (점진적 100→500→1000명)
   → 한계점 파악

4. Spike Test (10명 → 500명 급증)
   → 급격한 트래픽 대응력

5. Soak Test (300명 × 30분~2시간)
   → 메모리 누수, DB 커넥션 풀 확인
```

---

## 📁 폴더 구조

```
load_tests/
├── README.md                # 이 문서
├── locustfile.py            # 메인 진입점 (통합 시나리오)
├── config.py                # 설정값 (호스트, SLA 등)
│
├── users/                   # 사용자 행동 패턴
│   ├── base.py              # 공통 기능 (로그인, 캐싱)
│   ├── browser.py           # 조회만 하는 사용자 (65%)
│   ├── shopper.py           # 장바구니까지 담는 사용자 (25%)
│   └── buyer.py             # 결제까지 완료하는 사용자 (10%)
│
└── scenarios/               # 특수 시나리오
    ├── payment_stress.py    # 결제 API 집중 테스트
    └── concurrent_order.py  # 재고 경쟁 테스트
```

---

## 🛠️ 사전 준비

### 1. 서버 실행

#### Option A: Docker Compose (권장)

```bash
# 전체 서비스 실행 (DB, Redis, Django, Celery, Nginx)
docker compose up -d

# 로그 확인
docker compose logs -f web

# 서비스 상태 확인
docker compose ps
```

**Docker 환경 구성:**
| 서비스 | 포트 | 설명 |
|--------|------|------|
| nginx | 8000 | 리버스 프록시 (외부 접근점) |
| web | - | Django + Gunicorn (4 workers) |
| db | 5432 | PostgreSQL 15 |
| redis | 6379 | Celery 브로커 + 캐시 |
| celery_worker | - | 백그라운드 작업 처리 |
| celery_beat | - | 주기적 작업 스케줄러 |
| flower | 5555 | Celery 모니터링 UI |

```bash
# 테스트 데이터 생성 (Docker 환경) - 권장
docker compose exec web python manage.py create_load_test_users
docker compose exec web python manage.py create_test_data
```

#### Option B: 로컬 개발 환경

> ⚠️ **필수**: 반드시 가상환경을 활성화한 후 실행하세요!

```bash
# 가상환경 활성화 (Windows)
.\venv\Scripts\activate

# 가상환경 활성화 (macOS/Linux)
source venv/bin/activate

# Redis 실행 확인
redis-cli ping  # PONG 응답 확인

# Django 서버
python manage.py runserver

# Celery 워커 (별도 터미널)
celery -A myproject worker -l info
```

```bash
# 테스트 데이터 생성 (로컬 환경 - 가상환경 활성화 필수)
python manage.py create_load_test_users
python manage.py create_test_data
```

### 2. 테스트 데이터 확인

**확인 사항:**
- ✅ 사용자 1000명 생성됨 (`load_test_user_0` ~ `load_test_user_999`)
- ✅ 모든 사용자 비밀번호: `testpass123`
- ✅ 이메일 인증 완료됨
- ✅ 상품 재고 충분함

```bash
# 데이터 확인 - Docker 환경
docker compose exec web python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); print(f'테스트 유저 수: {User.objects.filter(username__startswith=\"load_test_user_\").count()}')"

# 데이터 확인 - 로컬 환경 (가상환경 활성화 필수)
python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); print(f'테스트 유저 수: {User.objects.filter(username__startswith=\"load_test_user_\").count()}')"
```

### 3. Locust 설치

```bash
# 로컬 환경
pip install locust

# Docker 환경 (web 컨테이너에 이미 설치됨)
docker compose exec web pip install locust
```

---

## 🚀 실행 방법

> ⚠️ **중요**: Docker 환경에서는 `docker compose exec web` 접두사를 붙여야 합니다.
> 로컬 환경에서는 반드시 **가상환경을 활성화**한 후 실행하세요.

### Windows 환경 주의사항

Windows에서 실행 시 인코딩 문제가 발생할 수 있습니다. `PYTHONUTF8=1` 환경변수를 설정하세요:

```bash
# Windows (Git Bash / MINGW64) - 로컬 가상환경
PYTHONUTF8=1 locust -f load_tests/locustfile.py --host=http://localhost:8000

# Windows (PowerShell) - 로컬 가상환경
$env:PYTHONUTF8=1; locust -f load_tests/locustfile.py --host=http://localhost:8000

# Windows (CMD) - 로컬 가상환경
set PYTHONUTF8=1 && locust -f load_tests/locustfile.py --host=http://localhost:8000

# Docker 환경 (인코딩 문제 없음)
docker compose exec web locust -f load_tests/locustfile.py --host=http://localhost:8000
```

### 기본 실행 (웹 UI)

```bash
# 로컬 환경 (가상환경 활성화 필수)
locust -f load_tests/locustfile.py --host=http://localhost:8000

# Docker 환경
docker compose exec web locust -f load_tests/locustfile.py --host=http://web:8000
```

브라우저에서 http://localhost:8089 접속

### CLI 모드 (자동 실행)

```bash
# 로컬 환경 - Load Test: 100명, 5분 (한 줄 명령어 - 권장)
PYTHONUTF8=1 locust -f load_tests/locustfile.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=5m --headless --html=reports/load_test_100.html

# Docker 환경 - Load Test: 100명, 5분
docker compose exec web locust -f load_tests/locustfile.py --host=http://web:8000 --users=100 --spawn-rate=10 --run-time=5m --headless --html=reports/load_test_100.html
```

> ⚠️ **Windows 주의**: 멀티라인 명령어(`\` 사용)를 복사-붙여넣기하면 공백이 누락될 수 있습니다. 한 줄 명령어를 사용하세요.

### 특수 시나리오 실행

```bash
# 결제 스트레스 테스트 - 로컬 환경
PYTHONUTF8=1 locust -f load_tests/scenarios/payment_stress.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --run-time=3m

# 결제 스트레스 테스트 - Docker 환경
docker compose exec web locust -f load_tests/scenarios/payment_stress.py --host=http://web:8000 --users=50 --spawn-rate=10 --run-time=3m

# 재고 경쟁 테스트 - 로컬 환경
PYTHONUTF8=1 locust -f load_tests/scenarios/concurrent_order.py --host=http://localhost:8000 --users=100 --spawn-rate=50 --run-time=2m

# 재고 경쟁 테스트 - Docker 환경
docker compose exec web locust -f load_tests/scenarios/concurrent_order.py --host=http://web:8000 --users=100 --spawn-rate=50 --run-time=2m
```

---

## 📖 시나리오 설명

### 1. 통합 시나리오 (locustfile.py)

실제 사용자 행동 패턴을 시뮬레이션합니다.

| 사용자 타입 | 비율 | 행동 패턴 |
|------------|------|----------|
| **Browser** | 65% | 상품 목록/상세 조회만 |
| **Shopper** | 25% | 장바구니까지 담고 이탈 |
| **Buyer** | 10% | 결제까지 완료 |

> 현실 기준: 100명 방문 → 10명 장바구니 → 2~5명 결제

### 2. 결제 스트레스 테스트 (payment_stress.py)

**목적:** 결제 API 성능 및 안정성 검증

- PG 연동 응답시간
- Idempotency (중복 결제 방지)
- 에러율 및 P99 레이턴시

**측정 지표:**
- `POST /api/payments/confirm/` P95/P99
- 에러율 (목표: < 0.1%)
- 중복 결제 방지 동작 확인

### 3. 재고 경쟁 테스트 (concurrent_order.py)

**목적:** 동시 주문 시 재고 정합성 검증

- 같은 상품에 대한 동시 주문
- Overselling 방지 확인

**주의:**
- pytest 동시성 테스트에서 correctness는 이미 검증됨
- Locust에서는 실제 부하 상황에서의 동작 확인이 목적

---

## 📊 성능 목표 (SLA)

> ⚠️ 아래 수치는 초기 목표치입니다. 1차 측정 후 조정 예정.

| API | P95 | P99 | 에러율 |
|-----|-----|-----|--------|
| 상품 목록 | < 800ms | < 1500ms | < 1% |
| 상품 상세 | < 500ms | < 1000ms | < 1% |
| 장바구니 | < 500ms | < 1000ms | < 1% |
| 주문 생성 | < 1000ms | < 2000ms | < 2% |
| **결제** | < 300ms | < 500ms | < 0.1% |

### 측정 방법

```
1차 측정 결과 예시:
- 상품 목록: P95 ≈ 750ms, P99 ≈ 1300ms
- 결제: P95 ≈ 280ms, P99 ≈ 420ms

→ 목표 SLA (V1):
- 상품 목록: P95 < 800ms, P99 < 1500ms
- 결제: P95 < 300ms, P99 < 500ms
```

---

## 📈 결과 분석

### 1. Locust 리포트 확인

HTML 리포트에서 확인할 항목:

- **Median (P50)**: 중간값
- **95th percentile (P95)**: 상위 5% 제외 최악
- **99th percentile (P99)**: 상위 1% 제외 최악
- **Failures**: 실패 수 및 에러 메시지
- **RPS**: 초당 요청 처리량

### 2. 문제 진단

| 증상 | 가능한 원인 | 해결 방법 |
|------|------------|----------|
| P99 급증 | DB 쿼리 느림 | 인덱스 추가, 쿼리 최적화 |
| 5xx 에러 증가 | 서버 과부하 | Worker 수 조정, 캐시 도입 |
| 에러율 > 5% | 비즈니스 로직 문제 | 로그 확인, 재고 체크 |
| RPS 정체 | 병목 구간 존재 | 프로파일링으로 병목 찾기 |

### 3. 서버 모니터링

#### Docker 환경

```bash
# 전체 서비스 로그
docker compose logs -f

# 특정 서비스 로그
docker compose logs -f web celery_worker

# Flower UI (Celery 모니터링)
# http://localhost:5555 접속

# 컨테이너 리소스 사용량
docker stats

# DB 커넥션 수 (Docker PostgreSQL)
docker compose exec db psql -U shopping_user -d shopping_db -c "SELECT count(*) FROM pg_stat_activity;"

# Redis 메모리 (Docker)
docker compose exec redis redis-cli info memory
```

#### 로컬 환경

```bash
# Django 에러 로그
tail -f logs/django.log | grep -E "(ERROR|WARNING)"

# DB 커넥션 수 (PostgreSQL)
SELECT count(*) FROM pg_stat_activity;

# Redis 메모리
redis-cli info memory

# Celery 큐 상태
celery -A myproject inspect active
```

---

## 🚨 주의 사항

1. **Production 직접 테스트 금지**
   - 실제 서비스 중인 서버에 부하 테스트 X
   - 별도 Staging 환경에서 실행

2. **테스트 데이터 분리**
   - `load_test_` prefix로 테스트 유저 구분
   - 테스트 후 데이터 정리

3. **Rate Limiting 해제**
   - 테스트 중에는 throttle 설정 임시 해제
   - 또는 테스트 IP 화이트리스트 추가

4. **외부 API Mock**
   - Toss 결제 API는 Mock 처리 권장
   - 실제 PG 연동 테스트는 별도 진행

---

## 📚 참고 자료

- [Locust 공식 문서](https://docs.locust.io/)
- [Django Performance Tips](https://docs.djangoproject.com/en/5.0/topics/performance/)
- [PostgreSQL Performance Tuning](https://wiki.postgresql.org/wiki/Performance_Optimization)
