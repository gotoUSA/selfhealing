# 포인트 시스템 부하 테스트 가이드

## 🚨 중요: Celery Worker 필수!

**주문이 비동기로 처리되므로 Celery worker가 실행 중이어야 재고가 차감됩니다.**

## 사전 준비

### 1. Redis 실행
```bash
# Windows/Mac: Redis 설치 후
redis-server

# Docker 사용 시
docker run -d -p 6379:6379 redis
```

### 2. Celery Worker 실행 (필수!)
```bash
# Windows
celery -A myproject worker --loglevel=info --pool=solo

# Linux/Mac
celery -A myproject worker --loglevel=info
```

**⚠️ Worker가 실행되지 않으면:**
- 주문은 생성되지만 (HTTP 202 Accepted)
- 실제 처리(재고 차감, 포인트 차감)가 안 됩니다!

### 3. Django 개발 서버 실행
```bash
python manage.py runserver
```

### 4. 테스트 데이터 생성
```bash
# 자동 생성 스크립트 (권장)
scripts/prepare_load_test.bat  # Windows
scripts/prepare_load_test.sh   # Linux/Mac

# 또는 수동 생성
python manage.py create_test_data --preset full
python manage.py create_load_test_users --count 1000 --points 50000
```

## 부하 테스트 실행

### 기본 실행
```bash
locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000
```

웹 브라우저에서 http://localhost:8089 접속

### CLI 모드 (Headless)
```bash
# 100명 동시 사용자, 10명/초 증가, 60초 실행 (한 줄 명령어 - 권장)
PYTHONUTF8=1 locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=60s --headless
```

> ⚠️ **Windows 주의**: 멀티라인 명령어(`\` 사용)를 복사-붙여넣기하면 공백이 누락될 수 있습니다.

### 고부하 테스트
```bash
# 1000명 동시 사용자 (한 줄 명령어)
PYTHONUTF8=1 locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000 --users=1000 --spawn-rate=50 --run-time=120s --headless
```

## 테스트 시나리오

### PointConcurrentUser (일반 시나리오)
- 포인트 조회 (50%)
- 포인트 이력 조회 (30%)
- 만료 예정 포인트 조회 (20%)
- 포인트 사용 주문 (10%)

### PointHighLoadUser (고부하 시나리오)
- 포인트 사용 주문만 집중 테스트

## 모니터링

### Celery Worker 로그 확인
```bash
# Worker 콘솔에서 실시간 확인
[2025-11-27 18:00:00,123: INFO/MainProcess] Task order_processing.process_order[...] received
[2025-11-27 18:00:00,456: INFO/ForkPoolWorker] Task succeeded: order_id=123
```

### 재고 확인
```bash
# Django shell에서
python manage.py shell

>>> from shopping.models import Product
>>> Product.objects.filter(stock_quantity__gt=0).count()
```

### 주문 처리 상태 확인
```bash
>>> from shopping.models import Order
>>> Order.objects.filter(status='pending').count()  # 처리 대기 중
>>> Order.objects.filter(status='paid').count()     # 처리 완료
```

## 문제 해결

### "장바구니가 비어있습니다" 에러
**원인**: 동시성 환경에서 정상적인 실패
**해결**: 무시 (Locust 통계에 반영됨)

### 재고가 차감되지 않음
**원인**: Celery worker 미실행
**해결**: `celery -A myproject worker --loglevel=info --pool=solo` 실행

### Redis 연결 오류
```
ConnectionError: Error connecting to Redis
```
**해결**: Redis 서버 실행 확인 (`redis-cli ping` → PONG)

### 포인트가 차감되지 않음
**원인**: 주문이 비동기 처리 중
**확인**: Celery worker 로그에서 `Task succeeded` 확인

## 성능 지표

### 정상 범위 (참고)
- **응답 시간**:
  - 조회: < 100ms
  - 주문 생성: < 500ms (비동기이므로 빠름)
- **성공률**: > 95%
- **처리량**: > 100 RPS (서버 사양에 따라 다름)

### 실패 원인별 분포
- 장바구니 비어있음: 정상 (동시성)
- 재고 부족: 정상 (테스트 진행 중)
- 포인트 부족: 정상 (테스트 설정)
- 500 에러: 비정상 → 서버 로그 확인 필요

## 참고 사항

1. **비동기 처리**: 주문은 HTTP 202로 즉시 응답하고, 백그라운드에서 Celery가 처리
2. **재고 확인 시점**: Celery task 실행 시점에 재고 차감
3. **동시성 제어**: DB 레벨 `select_for_update()`로 처리
4. **테스트 격리**: 각 사용자는 독립적인 장바구니와 포인트 보유
