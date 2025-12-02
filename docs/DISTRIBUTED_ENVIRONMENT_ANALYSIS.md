# 🐳 분산 환경 분석 및 개선 가이드

> **작성일**: 2025년 12월 2일
> **목적**: 현재 Docker Compose 환경에서 동시성 제어의 적절성 분석 및 개선 방안

---

## 📊 1. 현재 인프라 구성 분석

### Docker Compose 서비스 현황

```
┌─────────────────────────────────────────────────────────────────┐
│                         Docker Network                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────┐     ┌─────────────────────────────────────┐       │
│  │  Nginx  │────▶│  Gunicorn (web)                     │       │
│  │  :80    │     │  4 workers × 4 threads = 16 동시처리 │       │
│  └─────────┘     └─────────────────────────────────────┘       │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    PostgreSQL (db)                       │   │
│  │            select_for_update() → 행 레벨 락              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                      Redis                               │   │
│  │   • Celery Broker (작업 큐)                              │   │
│  │   • Cache Backend (django-redis)  ← 현재 캐시용만 사용    │   │
│  │   • 🔴 분산락 미사용                                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Celery Worker (8 concurrency)               │   │
│  │   • payment_critical (결제)                              │   │
│  │   • order_processing (주문)                              │   │
│  │   • points, notifications, etc.                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 현재 설정 요약

| 서비스 | 설정 | 동시 처리 능력 |
|--------|------|---------------|
| **Nginx** | keepalive 16 | 리버스 프록시 |
| **Gunicorn** | 4 workers × 4 threads | 최대 16개 동시 요청 |
| **Celery** | 8 concurrency | 8개 작업 동시 처리 |
| **PostgreSQL** | CONN_MAX_AGE=120 | 연결 풀링 |
| **Redis** | 단일 인스턴스 | Celery 브로커 + 캐시 |

---

## ✅ 2. 현재 동시성 제어가 적절한 이유

### 2-1. 단일 PostgreSQL = 단일 진실의 원천 (Single Source of Truth)

```python
# payment_service.py - 현재 구현
payment = Payment.objects.select_for_update().get(pk=payment.pk)
if payment.is_paid:
    raise PaymentConfirmError("이미 완료된 결제입니다.")
```

**왜 충분한가?**

```
┌─────────────────────────────────────────────────────────────────┐
│                      Gunicorn Workers                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Worker 1 ──┐                                                   │
│  Worker 2 ──┼──▶ PostgreSQL ──▶ select_for_update() 락         │
│  Worker 3 ──┤         │                                         │
│  Worker 4 ──┘         ▼                                         │
│                  행 레벨 락으로 직렬화                            │
│                  (다른 요청은 대기)                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

- 모든 Gunicorn 워커가 **같은 PostgreSQL 인스턴스**에 연결
- `select_for_update()`가 **DB 레벨**에서 락을 걸음
- Redis 분산락 없이도 **완벽한 동시성 제어** 가능

### 2-2. Redis가 이미 존재 → 분산락 추가 용이

```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine
```

```python
# production.py
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL"),
    }
}
```

**현재 Redis 용도:**
1. ✅ Celery 브로커 (작업 큐)
2. ✅ Django 캐시 (category_tree 등)
3. ❌ 분산락 (미사용 - **필요 없음**)

---

## 🔍 3. 언제 Redis 분산락이 필요한가?

### 필요한 경우 ❌ (현재 해당 안 됨)

| 시나리오 | 현재 상황 | 분산락 필요? |
|----------|----------|-------------|
| **다중 DB 인스턴스** | PostgreSQL 1개 | ❌ 불필요 |
| **다중 서버 (물리적)** | Docker 단일 호스트 | ❌ 불필요 |
| **DB 없는 리소스 락** | 모두 DB에 저장 | ❌ 불필요 |
| **외부 API Rate Limiting** | 현재 미구현 | 🟡 선택적 |

### 필요한 경우 ✅ (향후 확장 시)

```
향후 확장 시나리오:

┌─────────────────┐     ┌─────────────────┐
│   서버 A        │     │   서버 B        │
│   (AWS EC2)     │     │   (AWS EC2)     │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
    ┌─────────────────────────────────┐
    │       PostgreSQL (RDS)          │
    │   select_for_update() 여전히 유효 │
    └─────────────────────────────────┘

→ 이 경우에도 PostgreSQL 락으로 충분!
→ Redis 분산락은 "DB를 거치지 않는 락"이 필요할 때만
```

---

## 📋 4. 현재 구현의 강점과 약점

### ✅ 강점 (이미 잘 되어 있는 부분)

#### 4-1. 결제 동시성 제어 - 완벽

```python
# payment_service.py
@transaction.atomic
def confirm_payment_sync(payment, payment_key, order_id, amount, user):
    # 1. DB 락 획득
    payment = Payment.objects.select_for_update().get(pk=payment.pk)

    # 2. 상태 체크 (중복 방지)
    if payment.is_paid:
        raise PaymentConfirmError("이미 완료된 결제입니다.")

    # 3. 토스 API 호출 → 결제 완료
```

**분석**: PostgreSQL `select_for_update()`가 모든 Gunicorn 워커 간 동기화 보장 ✅

#### 4-2. 웹훅 중복 처리 방지 - 완벽

```python
# toss_webhook_view.py
@transaction.atomic
def handle_payment_done(event_data):
    # DB 락으로 동시 웹훅 직렬화
    payment = Payment.objects.select_for_update().get(toss_order_id=order_id)

    if payment.is_paid:
        logger.info(f"Payment already processed: {order_id}")
        return  # 중복 무시
```

**분석**: 같은 결제에 웹훅 2번 도착해도 1번만 처리 ✅

#### 4-3. 재고 관리 - 완벽

```python
# toss_webhook_view.py
Product.objects.filter(
    pk=order_item.product.pk,
    stock__gte=order_item.quantity,  # 조건부 업데이트
).update(
    stock=F("stock") - order_item.quantity,
)
```

**분석**: `F()` 객체로 원자적 연산, 조건부 업데이트로 음수 방지 ✅

#### 4-4. Celery 작업 분리 - 우수

```python
# celery.py
task_queues = {
    "payment_critical": {...},   # 결제 (최우선)
    "order_processing": {...},   # 주문
    "points": {...},             # 포인트
    "notifications": {...},      # 알림
}
```

**분석**: 결제 작업이 다른 작업에 밀리지 않음 ✅

---

### ⚠️ 약점 (개선 가능한 부분)

#### 4-5. Idempotency Key TTL 없음

```python
# 현재 구현
idempotency_key = models.CharField(max_length=64, unique=True, null=True)
# → DB에 영구 저장 (삭제 안 됨)
```

**문제**: 오래된 키가 계속 쌓임

**개선안** (Redis 활용):

```python
# Redis로 24시간 TTL 적용
from django.core.cache import cache

def check_idempotency_key(key: str, payment_id: int) -> tuple[bool, int | None]:
    """
    Returns: (이미존재여부, 기존payment_id)
    """
    cache_key = f"idempotency:{key}"
    existing = cache.get(cache_key)

    if existing:
        return True, existing

    # 24시간 TTL로 저장
    cache.set(cache_key, payment_id, timeout=86400)
    return False, None
```

#### 4-6. 웹훅 이벤트 ID 미저장

```python
# 현재: Payment.is_paid로 중복 체크
if payment.is_paid:
    return

# 개선안: 이벤트 ID 별도 저장
class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True, db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)
```

**장점**:
- 더 명확한 중복 방지 (토스가 제공하는 eventId 활용)
- 로깅/디버깅 용이

---

## 🛠️ 5. 현재 환경에서 즉시 적용 가능한 개선

### 5-1. Redis 기반 Idempotency Key (TTL 24시간)

**수정 파일**: `shopping/services/payment_service.py`

```python
from django.core.cache import cache

class PaymentService:
    IDEMPOTENCY_TTL = 86400  # 24시간

    @staticmethod
    def _check_idempotency_key(key: str) -> Payment | None:
        """Redis에서 멱등성 키 확인 (TTL 적용)"""
        if not key:
            return None

        cache_key = f"payment:idempotency:{key}"
        payment_id = cache.get(cache_key)

        if payment_id:
            try:
                return Payment.objects.get(pk=payment_id)
            except Payment.DoesNotExist:
                cache.delete(cache_key)  # 정리

        return None

    @staticmethod
    def _set_idempotency_key(key: str, payment_id: int) -> None:
        """Redis에 멱등성 키 저장"""
        if key:
            cache_key = f"payment:idempotency:{key}"
            cache.set(cache_key, payment_id, timeout=PaymentService.IDEMPOTENCY_TTL)

    @staticmethod
    @transaction.atomic
    def create_payment(order, payment_method="card", idempotency_key=None):
        # Redis 먼저 체크 (빠름)
        existing = PaymentService._check_idempotency_key(idempotency_key)
        if existing:
            return existing

        # DB에도 저장 (영구 기록)
        if idempotency_key:
            existing_db = Payment.objects.filter(idempotency_key=idempotency_key).first()
            if existing_db:
                return existing_db

        # ... 새 Payment 생성 ...

        # Redis에 저장 (TTL 24시간)
        PaymentService._set_idempotency_key(idempotency_key, payment.id)

        return payment
```

### 5-2. 웹훅 이벤트 ID 저장 (선택적)

**새 파일**: `shopping/models/webhook_event.py`

```python
from django.db import models

class WebhookEvent(models.Model):
    """
    웹훅 이벤트 중복 처리 방지용 모델
    토스페이먼츠 등 외부 서비스의 웹훅 이벤트 ID 저장
    """
    event_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="외부 서비스에서 제공하는 이벤트 고유 ID"
    )
    event_type = models.CharField(max_length=50)
    source = models.CharField(max_length=50, default="toss")
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "shopping_webhook_event"
        indexes = [
            models.Index(fields=["-processed_at"]),
        ]

    @classmethod
    def is_processed(cls, event_id: str) -> bool:
        """이미 처리된 이벤트인지 확인"""
        return cls.objects.filter(event_id=event_id).exists()

    @classmethod
    def mark_processed(cls, event_id: str, event_type: str, source: str = "toss"):
        """이벤트 처리 완료 기록"""
        cls.objects.get_or_create(
            event_id=event_id,
            defaults={"event_type": event_type, "source": source}
        )
```

---

## 📈 6. 확장 시나리오별 대응 전략

### 시나리오 A: Gunicorn 워커 증가 (16 → 32)

```yaml
# docker-compose.yml
web:
  command: gunicorn ... --workers 8 --threads 4  # 32 동시 처리
```

**필요한 조치**: 없음 ✅
- PostgreSQL 락이 여전히 모든 워커 간 동기화 보장

### 시나리오 B: 웹 컨테이너 수평 확장 (web 2개)

```yaml
# docker-compose.yml
web:
  deploy:
    replicas: 2
```

**필요한 조치**: 없음 ✅
- 모든 컨테이너가 같은 PostgreSQL에 연결
- `select_for_update()` 여전히 유효

### 시나리오 C: 물리적 다중 서버 (AWS ECS/EKS)

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Server 1   │  │   Server 2   │  │   Server 3   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └────────────────┬┴─────────────────┘
                        ▼
              ┌─────────────────┐
              │  PostgreSQL RDS │
              └─────────────────┘
```

**필요한 조치**: 여전히 없음 ✅
- AWS RDS PostgreSQL도 `select_for_update()` 지원
- 네트워크 레이턴시만 약간 증가

### 시나리오 D: Redis 분산락이 정말 필요한 경우

```python
# 예: 외부 API Rate Limiting (DB에 저장할 필요 없는 락)
from redis.lock import Lock
from django_redis import get_redis_connection

def call_external_api_with_rate_limit(api_name: str):
    redis = get_redis_connection("default")
    lock = Lock(redis, f"api_rate:{api_name}", timeout=1)

    if lock.acquire(blocking=False):
        try:
            # API 호출
            pass
        finally:
            lock.release()
    else:
        raise RateLimitError("API 호출 제한 중")
```

---

## 🎯 7. 결론 및 권장사항

### 현재 상태 평가

| 영역 | 현재 구현 | 충분 여부 | 개선 필요? |
|------|----------|----------|-----------|
| **결제 동시성** | PostgreSQL 락 | ✅ 충분 | ❌ 불필요 |
| **웹훅 중복 방지** | is_paid 체크 | ✅ 충분 | 🟡 선택적 |
| **재고 관리** | F() + 조건부 | ✅ 충분 | ❌ 불필요 |
| **멱등성 키** | DB 저장 | 🟡 가능 | 🟡 TTL 추가 권장 |
| **분산락** | 미사용 | ✅ 불필요 | ❌ 현재 불필요 |

### 핵심 메시지

> **현재 Docker Compose 환경에서 PostgreSQL 락만으로 충분합니다.**
>
> Redis 분산락은 "여러 PostgreSQL 인스턴스" 또는 "DB에 저장하지 않는 리소스 락"이 필요할 때만 의미가 있습니다.
>
> 현재 아키텍처는:
> - ✅ 단일 PostgreSQL → 모든 동시성 제어 가능
> - ✅ Redis 존재 → 필요 시 분산락 추가 용이
> - ✅ Celery 큐 분리 → 결제 작업 우선 처리

### 즉시 적용 권장 사항

1. **선택적**: Idempotency Key에 Redis TTL 적용 (24시간)
2. **선택적**: WebhookEvent 모델 추가 (이벤트 ID 기록)
3. **불필요**: Redis 분산락 (현재 아키텍처에서 필요 없음)

### 향후 확장 시 참고

- **웹 컨테이너 수평 확장**: 추가 조치 불필요
- **다중 물리 서버 배포**: 추가 조치 불필요 (PostgreSQL 락 유효)
- **다중 PostgreSQL 인스턴스**: 이때 Redis 분산락 검토

---

## 📚 참고 자료

- [PostgreSQL SELECT FOR UPDATE](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [Django select_for_update()](https://docs.djangoproject.com/en/4.2/ref/models/querysets/#select-for-update)
- [Redis Distributed Locks (Redlock)](https://redis.io/docs/manual/patterns/distributed-locks/)
- [django-redis Documentation](https://github.com/jazzband/django-redis)

---

## 📝 변경 이력

| 날짜 | 내용 | 작성자 |
|-----|------|--------|
| 2025-12-02 | 최초 작성 - 분산 환경 분석 | AI Assistant |
