# Locust 부하 테스트 실행 계획서

## 📋 목표

### 성능 지표 목표
- **동시 사용자**: 1000명 이상 처리
- **평균 응답 시간**: < 500ms
- **P95 응답 시간**: < 1000ms
- **P99 응답 시간**: < 2000ms
- **실패율**: < 5%
- **TPS (Transactions Per Second)**: 최소 100

### 테스트 목적
1. **병목 지점 파악**: 어느 API가 느린지, 어디서 에러가 나는지
2. **확장성 검증**: 사용자가 늘어날 때 시스템이 버티는지
3. **안정성 확인**: 장시간 부하에서도 정상 작동하는지

---

## 🛠️ 사전 준비

### 1단계: 테스트 환경 설정

#### 로컬 환경 (개발/디버깅용)
```bash
# Django 서버 실행
python manage.py runserver

# 별도 터미널에서 Celery 워커 실행 (비동기 작업용)
celery -A myproject worker -l info

# Redis 실행 확인
redis-cli ping  # PONG 응답 확인
```

#### Production-like 환경 (권장)
- Docker Compose로 격리된 환경 구성
- Gunicorn + Nginx 조합
- PostgreSQL (실제 DB)
- Redis (캐시/Celery)

### 2단계: 테스트 데이터 생성

```bash
# 1000명 사용자 + 100개 상품 생성
python shopping/tests/performance/setup_test_data.py
```

**확인 사항**:
- ✅ 사용자 1000명 생성됨 (`load_test_user_0` ~ `load_test_user_999`)
- ✅ 상품 100개 생성됨 (재고 충분)
- ✅ 모든 사용자 비밀번호: `testpass123`
- ✅ 이메일 인증 완료됨

### 3단계: API 엔드포인트 확인

실제 존재하는 API만 테스트하도록 확인:
```bash
# Django URL 확인
python manage.py show_urls | grep api
```

현재 `locustfile.py`에서 사용 중인 엔드포인트:
- `/api/auth/login/` ✅
- `/api/products/` ✅
- `/api/products/{id}/` ✅
- `/api/cart/items/` ❓ (405 에러 발생 중 → 확인 필요)
- `/api/orders/` ✅
- `/api/payments/confirm/` ✅

> **중요**: `POST /api/cart/items/`가 405 에러가 나고 있음. 테스트 전 확인 필요!

---

## 🚀 테스트 시나리오 실행 계획

### Phase 1: 워밍업 (Smoke Test)

**목적**: 시스템이 최소 부하에서 정상 작동하는지 확인

```bash
locust -f shopping/tests/performance/locustfile.py \
    --host=http://localhost:8000 \
    --users 10 \
    --spawn-rate 2 \
    --run-time 2m \
    --headless
```

**기대 결과**:
- 모든 요청 성공 (실패율 0%)
- 평균 응답 시간 < 200ms

**실패 시**: API 엔드포인트 수정 후 재실행

---

### Phase 2: 점진적 부하 증가 (Stress Test)

**목적**: 시스템의 한계점 찾기

#### 웹 UI 모드 (수동 제어)
```bash
locust -f shopping/tests/performance/locustfile.py \
    --host=http://localhost:8000
```

- 브라우저: http://localhost:8089 접속
- 사용자 수를 **10 → 50 → 100 → 500 → 1000**으로 점진적 증가
- 각 단계마다 **5분간 실행**, 결과 관찰

#### 자동 실행 모드 (CLI)
```bash
# 100명 동시 사용자, 5분 실행
locust -f shopping/tests/performance/locustfile.py \
    --host=http://localhost:8000 \
    --users 100 \
    --spawn-rate 10 \
    --run-time 5m \
    --headless \
    --html report_100users.html
```

**관찰 포인트**:
- 몇 명부터 응답 시간이 급증하는가?
- 몇 명부터 에러가 발생하는가?
- CPU/메모리 사용률 모니터링

---

### Phase 3: 결제 집중 테스트

**목적**: 가장 중요한 결제 API의 동시성 처리 능력 검증

```bash
locust -f shopping/tests/performance/scenarios/payment.py \
    --host=http://localhost:8000 \
    --users 500 \
    --spawn-rate 50 \
    --run-time 3m \
    --headless \
    --html report_payment.html
```

**주의 사항**:
- 결제는 실제 Toss API 호출 안 하도록 Mock 설정 필요
- 재고 부족 시나리오도 테스트
- 중복 결제 방지 로직 확인

---

### Phase 4: 장시간 안정성 테스트 (Soak Test)

**목적**: 메모리 누수, DB 커넥션 풀 고갈 등 장시간 운영 시 문제 확인

```bash
locust -f shopping/tests/performance/locustfile.py \
    --host=http://localhost:8000 \
    --users 300 \
    --spawn-rate 30 \
    --run-time 30m \
    --headless \
    --html report_soak_30min.html
```

**모니터링 항목**:
- DB 커넥션 수 (`SELECT count(*) FROM pg_stat_activity;`)
- Redis 메모리 사용량 (`redis-cli info memory`)
- Celery 큐 적체 여부
- 시간이 지날수록 응답 시간이 증가하는가?

---

## 📊 결과 분석

### 1. Locust 리포트 확인

HTML 리포트 (`report_*.html`)에서 확인:

#### Response Time 분석
- **Median (P50)**: 중간값
- **Average**: 평균 (outlier에 민감)
- **95th percentile (P95)**: 상위 5% 제외한 최악의 경우
- **99th percentile (P99)**: 상위 1% 제외한 최악의 경우

> **목표**: P95 < 1초, P99 < 2초

#### Failure Rate 분석
- **0-1%**: 정상 (네트워크 오류 허용 범위)
- **5%**: 경고 (병목 시작)
- **10% 이상**: 심각 (즉시 수정 필요)

#### RPS (Requests Per Second)
- 초당 처리 가능한 요청 수
- 시스템 최대 처리량 지표

### 2. 서버 모니터링

#### Django 로그 확인
```bash
tail -f logs/django.log | grep -E "(ERROR|WARNING)"
```

#### 느린 쿼리 찾기
```sql
-- PostgreSQL slow query log
SELECT query, calls, total_time, mean_time
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;
```

#### Celery 큐 상태
```bash
celery -A myproject inspect active
celery -A myproject inspect stats
```

---

## 🔧 병목 지점 해결 방법

### 응답 시간 느림

#### DB 쿼리 최적화
- **N+1 문제**: `select_related()`, `prefetch_related()` 사용
- **인덱스 추가**: 자주 조회되는 컬럼에 INDEX
- **불필요한 JOIN 제거**: 필요한 필드만 가져오기

```python
# 나쁜 예
products = Product.objects.all()  # N+1 발생
for p in products:
    print(p.category.name)  # 매번 쿼리

# 좋은 예
products = Product.objects.select_related('category').all()
```

#### 캐시 도입
```python
from django.core.cache import cache

# 상품 목록 캐싱 (5분)
products = cache.get('products_list')
if not products:
    products = Product.objects.filter(is_active=True)
    cache.set('products_list', products, 300)
```

#### 비동기 처리 확대
- 무거운 작업(이메일, 알림)은 Celery로 처리
- 결제 승인 후 포인트 적립은 비동기로

### DB 커넥션 풀 고갈

#### PgBouncer 도입
```yaml
# docker-compose.yml
pgbouncer:
  image: pgbouncer/pgbouncer
  environment:
    DATABASES_HOST: db
    DATABASES_PORT: 5432
    DATABASES_USER: postgres
    DATABASES_PASSWORD: password
    DATABASES_DBNAME: shopping
    PGBOUNCER_POOL_MODE: transaction
    PGBOUNCER_MAX_CLIENT_CONN: 1000
    PGBOUNCER_DEFAULT_POOL_SIZE: 25
```

#### Django CONN_MAX_AGE 설정
```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'CONN_MAX_AGE': 600,  # 커넥션 재사용 (10분)
    }
}
```

### Celery Task 적체

```bash
# Worker 수 증가
celery -A myproject worker -l info --concurrency=10

# 우선순위 큐 분리
celery -A myproject worker -Q high_priority,default -l info
```

### 메모리 부족

- Gunicorn worker 수 조정
- Redis maxmemory 설정
- 불필요한 로깅 제거

---

## 📅 실행 순서 (권장)

### 1주차: 기본 테스트
- [ ] Phase 1: Smoke Test (10명, 2분)
- [ ] API 엔드포인트 수정 (405, 404 에러 해결)
- [ ] Phase 2: 100명 부하 테스트 (5분)

### 2주차: 최적화
- [ ] 느린 쿼리 개선
- [ ] 캐시 도입
- [ ] Phase 2 재실행: 500명 부하 테스트

### 3주차: 대규모 테스트
- [ ] Phase 2: 1000명 부하 테스트
- [ ] Phase 3: 결제 집중 테스트
- [ ] Phase 4: 30분 장시간 테스트

### 4주차: Production 검증
- [ ] Staging 환경에서 실제 부하 테스트
- [ ] 모니터링 알림 설정
- [ ] 최종 리포트 작성

---

## 🎯 Quick Start

바로 시작하려면:

```bash
# 1. 테스트 데이터 생성
python shopping/tests/performance/setup_test_data.py

# 2. Django 서버 실행
python manage.py runserver

# 3. Locust 웹 UI 실행
locust -f shopping/tests/performance/locustfile.py --host=http://localhost:8000

# 4. 브라우저에서 http://localhost:8089 접속
# 5. Users: 10, Spawn rate: 2 입력하고 Start
# 6. 결과 관찰 후 점진적으로 사용자 수 증가
```

---

## 🚨 주의 사항

1. **Production 직접 테스트 금지**: 실제 서비스 중인 서버에 부하 테스트하면 안 됨
2. **외부 API Mock 필수**: Toss 결제 API 등은 Mock 처리
3. **테스트 데이터 분리**: `load_test_` prefix로 구분, 테스트 후 삭제
4. **DB 백업**: 부하 테스트 전 반드시 백업
5. **Rate Limiting 해제**: 테스트 중에는 throttle 설정 임시 해제

---

## 📚 참고 자료

- [Locust 공식 문서](https://docs.locust.io/)
- [Django Performance Tips](https://docs.djangoproject.com/en/5.0/topics/performance/)
- [PostgreSQL Performance Tuning](https://wiki.postgresql.org/wiki/Performance_Optimization)
