# Locust Order Concurrency Testing

## 📋 개요

500-1000명의 동시 주문 생성을 테스트하기 위한 Locust 시나리오입니다.

pytest의 DB 커넥션 풀 한계를 회피하고, 실제 HTTP 요청으로 프로덕션 환경과 유사한 부하 테스트를 수행합니다.

---

## 🚀 실행 방법

### 1. 기본 실행 (Web UI)

```bash
# 웹 UI로 실행
locust -f shopping/tests/performance/scenarios/order.py \
    --host=http://localhost:8000

# 브라우저에서 http://localhost:8089 접속
# - Number of users: 500 or 1000
# - Spawn rate: 50~100
# - Host: http://localhost:8000
```

### 2. Headless 모드 (자동 실행)

#### 500명 동시 주문 테스트

```bash
locust -f shopping/tests/performance/scenarios/order.py \
    --host=http://localhost:8000 \
    --users 500 \
    --spawn-rate 50 \
    --run-time 5m \
    --headless
```

**예상 결과**:
- 총 주문 시도: ~500건
- 성공률: 95%+ (재고 충분 시)
- 실행 시간: 5분
- 평균 응답 시간: \<500ms

#### 1000명 동시 주문 테스트

```bash
locust -f shopping/tests/performance/scenarios/order.py \
    --host=http://localhost:8000 \
    --users 1000 \
    --spawn-rate 100 \
    --run-time 10m \
    --headless
```

**예상 결과**:
- 총 주문 시도: ~1000건
- 성공률: 90%+ (재고 및 시스템 리소스 충분 시)
- 실행 시간: 10분
- 평균 응답 시간: \<1s

### 3. 점진적 부하 증가 (LoadTestShape)

`order.py` 파일에서 `OrderLoadShape` 클래스 주석을 해제하면:

```python
# order.py 하단 주석 해제
class OrderLoadShape(LoadTestShape):
    ...
```

실행:
```bash
locust -f shopping/tests/performance/scenarios/order.py \
    --host=http://localhost:8000 \
    --headless
```

**부하 패턴**:
- 1분: 100명
- 3분: 300명
- 5분: 500명
- 7분: 700명
- 10분: 1000명 (피크)

---

## 📊 테스트 시나리오

### OrderConcurrencyUser

**플로우**:
1. 로그인
2. 장바구니에 1-2개 상품 추가
3. 주문 생성
4. 완료 (결제는 skip)

**특징**:
- 실제 사용자 행동 시뮬레이션
- 각 사용자마다 고유한 ID
- 재고 있는 상품만 선택
- 통계 자동 수집

---

## 📈 통계 확인

### 실행 중

Locust Web UI (http://localhost:8089)에서:
- Requests/s (RPS)
- Response times (min/median/max)
- Failure rate
- Current users

### 실행 후

터미널에 자동 출력:
```
📊 Order Concurrency Test Results
============================================================
총 주문 시도:     1000
성공한 주문:      950
실패한 주문:      50
장바구니 실패:    10
성공률:           95.00%
============================================================
```

---

## 🔧 사전 준비

### 1. 테스트 데이터 생성 ⚠️ 필수

```bash
python shopping/tests/performance/setup_test_data.py
```

**생성 내용**:
- 사용자: `load_test_user_0` ~ `load_test_user_999` (1,000명)
- 상품: 100개 (각 재고 100,000개)

**실행 시간**: 약 30초 소요

> [!IMPORTANT]
> 이 스크립트를 실행하지 않으면 로그인 400 에러가 대량 발생합니다!

### 2. 서버 실행

```bash
# Django 개발 서버
python manage.py runserver

# 또는 Gunicorn (프로덕션 환경)
gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

### 3. Celery Worker 실행

```bash
# 비동기 주문 처리용
celery -A myproject worker -l info
```

---

## ⚠️ 주의사항

### DB 커넥션 풀 설정

`settings.py`에서 커넥션 풀 증가:

```python
DATABASES = {
    'default': {
        ...
        'CONN_MAX_AGE': 600,
        'OPTIONS': {
            'connect_timeout': 10,
        }
    }
}
```

PostgreSQL `postgresql.conf`:
```
max_connections = 200
```

### 재고 관리

1000명 주문 시 **최소 2000개 이상** 재고 필요:
```python
# Admin에서 상품 재고 업데이트
product.stock = 5000
product.save()
```

---

## 🆚 pytest vs Locust

| 항목 | pytest (50-100명) | Locust (500-1000명) |
|------|------------------|---------------------|
| **목적** | 로직 검증 | 부하 테스트 |
| **방식** | Threading | HTTP 요청 |
| **DB 커넥션** | 스레드당 1개 (한계) | 서버 pooling |
| **실행 시간** | 1-3분 | 5-10분 |
| **CI/CD** | ✅ 적합 | ❌ 무거움 |
| **실제 부하** | ❌ 제한적 | ✅ 프로덕션 유사 |

**결론**: 두 가지 모두 필요하며, 역할이 다릅니다.

---

## 📝 커밋 메시지

```
test: add Locust order concurrency scenario for 500-1000 users

- Add scenarios/order.py for large-scale order testing
- Include statistics tracking and logging
- Support headless mode and LoadTestShape
- Avoid pytest DB connection pool limitations
```
