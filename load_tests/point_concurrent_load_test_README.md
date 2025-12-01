# Point Concurrent Load Test

포인트 시스템 대규모 동시성 부하 테스트

## 개요

- **목적**: 포인트 시스템 동시성 및 성능 검증
- **테스트 시나리오**:
  - 포인트 조회/사용/이력 확인
  - 1000명 이상 동시 사용자
- **실행**: `locust -f shopping/tests/performance/point_concurrent_load_test.py --users 1000 --spawn-rate 50`

## 실행 방법

### 1. 테스트 사용자 준비
```bash
# Django shell에서 실행
python manage.py shell

from shopping.tests.factories import UserFactory

# 1000명의 테스트 사용자 생성 (각 100,000P)
for i in range(1, 1001):
    UserFactory(
        username=f"loadtest_user_{i}",
        email=f"loadtest_{i}@test.com",
        phone_number=f"010-{1000+i:04d}-{i:04d}",
        points=100_000,
        is_email_verified=True
    )
```

### 2. Locust 실행

#### 웹 UI 모드 (추천)
```bash
locust -f shopping/tests/performance/point_concurrent_load_test.py
# 브라우저에서 http://localhost:8089 접속
```

#### Headless 모드
```bash
# 1000명 동시 사용자, 초당 50명씩 증가, 30초 실행
locust -f shopping/tests/performance/point_concurrent_load_test.py \
  --users 1000 \
  --spawn-rate 50 \
  --run-time 30s \
  --headless \
  --html report.html
```

#### 특정 사용자 클래스 실행
```bash
# 일반 부하 (혼합 시나리오)
locust -f locustfiles/point_concurrent_load_test.py PointConcurrentUser

# 고부하 (포인트 사용만)
locust -f locustfiles/point_concurrent_load_test.py PointHighLoadUser
```

## 성능 메트릭

모니터링할 주요 지표:
- **RPS (Requests Per Second)**: 초당 요청 처리량
- **Response Time**: 응답 시간 (평균, 중앙값, 95/99 백분위)
- **Failure Rate**: 실패율
- **DB Connection Pool**: 커넥션 풀 사용률

## 주의사항

1. **프로덕션 환경 금지**: 개발/스테이징 환경에서만 실행
2. **DB 백업**: 테스트 전 데이터베이스 백업 권장
3. **리소스 확인**: 충분한 CPU/메모리/DB 커넥션 확보
4. **테스트 후 정리**: 테스트 데이터 정리

## 예상 결과

### 정상 동작 기준
- **RPS**: 100 이상
- **평균 응답 시간**: 500ms 이하
- **실패율**: 1% 이하
- **DB 커넥션**: 최대 사용량 100개 이하

### 이상 징후
- 응답 시간 급증 (>2초)
- 실패율 증가 (>5%)
- DB 커넥션 풀 고갈
- 메모리 누수

## 역할 분리

| 도구 | 목적 | 사용자 수 | 실행 빈도 |
|------|------|-----------|-----------|
| pytest | 로직 정확성 검증 | 5-20명 | 매 커밋 (CI/CD) |
| Locust | 성능/부하 검증 | 1000명+ | 릴리스 전 |
