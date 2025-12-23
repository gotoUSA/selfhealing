# Self-Healing 메트릭 수집 전략

## 구현 상태

| 기능 | 상태 | 위치 |
|------|------|------|
| DriftThresholdConfig 모델 | ✅ 구현됨 | `selfhealing/models/drift_config.py` |
| MetricReconciler 동적 설정 로드 | ✅ 구현됨 | `selfhealing/metrics/reconciler.py` |
| **Drift 임계값 설정 API** | ✅ 구현됨 | `selfhealing/api/django/views/drift_threshold.py` |
| GET /config/drift-thresholds/ | ✅ 구현됨 | 현재 임계값 조회 |
| PUT /config/drift-thresholds/ | ✅ 구현됨 | 임계값 수정 |
| POST /config/drift-thresholds/reset/ | ✅ 구현됨 | 기본값으로 리셋 |

---

## 개요

Self-Healing 시스템의 메트릭 수집은 **도메인 중립성**과 **성능**을 모두 만족해야 합니다. 이 문서는 사용자 시스템의 DB에 직접 의존하지 않으면서도 정확한 메트릭을 수집하는 전략을 정의합니다.

### 설계 원칙

1. **Zero DB Dependency** - 사용자 DB 스키마에 직접 의존하지 않음
2. **Plug & Play** - Redis 없이도 동작, 인프라 의존성 최소화
3. **Eventual Consistency** - 메트릭은 관측용, 100% 정확도보다 가용성 우선
4. **Audit Trail** - 주요 동기화 시점은 추적 가능해야 함
5. **Distributed-Ready** - 대규모 분산 환경(K8s 등)에서도 안전하게 동작
6. **Timezone-Aware** - 전 세계 어디서든 시간 무결성 보장

---

## 메트릭 수집 방식 비교

| 방식 | 설명 | 장점 | 단점 |
|------|------|------|------|
| **Pull (DB Polling)** | 주기적으로 DB 쿼리 | 구현이 가장 쉬움 | 사용자 DB 부하 및 결합도 높음 |
| **Push (Event-based)** | 이벤트 발생 시 즉시 업데이트 | DB 부하 제로, 실시간성 | 시스템 장애 시 데이터 불일치 가능성 |
| **Hybrid (권장)** | Push 위주 + Lazy Sync | 정합성과 성능 모두 확보 | 구현 복잡도 약간 상승 |
| **Sidecar/Exporter** | 외부 도구(Prometheus 등) 활용 | 전문성 및 안정성 높음 | 인프라 의존성 발생 |

### 권장 방식: Pragmatic Hybrid

```
┌─────────────────────────────────────────────────────────────┐
│                    메트릭 수집 전략                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │   Counter   │     │    Gauge    │     │  Histogram  │   │
│  │  (누적 값)   │     │  (현재 값)   │     │   (분포)    │   │
│  └──────┬──────┘     └──────┬──────┘     └──────┬──────┘   │
│         │                   │                   │          │
│         ▼                   ▼                   ▼          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │ Push Only   │     │   Hybrid    │     │ Push Only   │   │
│  │ (정확함)    │     │ Push+Lazy   │     │ (정확함)    │   │
│  │             │     │   Sync      │     │             │   │
│  └─────────────┘     └─────────────┘     └─────────────┘   │
│                            │                               │
│                            ▼                               │
│                   ┌─────────────────┐                      │
│                   │  Lazy Sync 시점  │                      │
│                   │ • 서버 재시작    │                      │
│                   │ • 수동 트리거    │                      │
│                   │ • 일 1회 (옵션)  │                      │
│                   └─────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 메트릭 타입별 전략

### 전략 매트릭스

| 메트릭 타입 | 수집 방식 | 정확도 | 설명 |
|------------|----------|--------|------|
| **Counter** | Push Only | 100% | 누적값, 증가만 하므로 Drift 없음 |
| **Histogram** | Push Only | 100% | 관측 시점에 기록, Drift 없음 |
| **Gauge** | Hybrid | ~99%* | Eventual Consistency, 재시작 시 동기화 |

> *Gauge 오차 허용 근거: 메트릭은 "운영 관측용"이며, 정확한 값이 필요한 경우 원본 DB 조회 API 제공

### Counter - Push Only

Counter는 단조 증가만 하므로 이벤트 시점에 `.inc()`만 호출하면 정확합니다.

```python
# 예: DLQ 항목 생성 시
dlq_items_total.labels(domain="payment", failure_type="PG_TIMEOUT").inc()

# 예: 재시도 완료 시
retry_outcomes_total.labels(domain="payment", outcome="success").inc()
```

**Drift 발생 불가**: 값이 감소하지 않으므로 동기화 필요 없음

### Histogram - Push Only

Histogram은 관측 시점에 버킷에 기록되므로 Push만으로 정확합니다.

```python
# 예: 복구 시간 기록
recovery_time_seconds.labels(
    domain="payment",
    resolution_type="auto_replay"
).observe(duration_seconds)
```

**Drift 발생 불가**: 과거 관측값은 변경되지 않음

### Gauge - Hybrid (Push + Lazy Sync)

Gauge는 현재 상태를 나타내므로 프로세스 재시작 시 값이 손실될 수 있습니다.

```python
# Push: 이벤트 시점에 증감
dlq_pending_count.labels(domain="payment").inc()   # 생성 시
dlq_pending_count.labels(domain="payment").dec()   # 해결 시

# Lazy Sync: 재시작 시 DB와 동기화
def sync_gauges():
    for domain in DOMAINS:
        actual = adapter.get_dlq_pending_count(domain)
        dlq_pending_count.labels(domain=domain).set(actual)
```

### 음수 방지를 위한 SafeGauge 래퍼

서버 재시작 후 `dec()` 호출 시 Gauge 값이 음수가 되는 문제를 방지하기 위해 **SafeGauge** 래퍼를 사용합니다.

> 상세 문서: [08_OBSERVABILITY.md#safegauge-래퍼-패턴](08_OBSERVABILITY.md#safegauge-래퍼-패턴)

```python
from shopping.metrics.safe_gauge import SafeGauge

# SafeGauge로 래핑
safe_pending_gauge = SafeGauge(dlq_pending_gauge, "dlq_pending")

# inc/dec 시 자동으로 0 미만 방지
safe_pending_gauge.labels(domain="payment").inc()  # 1
safe_pending_gauge.labels(domain="payment").dec()  # 0
safe_pending_gauge.labels(domain="payment").dec()  # 0 유지 (음수 방지)

# 재시작 후 동기화
safe_pending_gauge.labels(domain="payment").sync_from_source(actual_db_count)
```

**동작 원리:**
1. **Shadow Counter**: 각 레이블 조합별로 현재 값 추적
2. **음수 차단**: `dec()` 호출 시 shadow 값이 0이면 실제 dec 호출 생략
3. **동기화**: `sync_from_source(value)` 또는 Reconciler를 통해 DB와 동기화

---

## 메트릭 소스 어댑터

### 어댑터 패턴 (Adapter Pattern)

Self-Healing 시스템은 사용자 DB를 직접 조회하지 않고, **추상화된 인터페이스**를 통해 데이터를 요청합니다.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Self-Healing   │────▶│  MetricSource   │────▶│   User's DB     │
│    System       │     │    Adapter      │     │   (or Cache)    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │
        │  "DLQ 몇 개?"          │  SELECT COUNT(*)...
        │◀──────────────────────│  (구현은 사용자 책임)
        │       42              │
```

### 인터페이스 정의

```python
# selfhealing/adapters/metrics/base.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class MetricSourceAdapter(Protocol):
    """
    메트릭 소스 어댑터 인터페이스.

    사용자는 이 인터페이스를 구현하여 자신의 데이터 소스에서
    메트릭 값을 제공합니다. DB, 캐시, 외부 API 등 어떤 소스든 가능합니다.
    """

    def get_dlq_pending_count(self, domain: str) -> int:
        """도메인별 대기 중인 DLQ 항목 수 반환"""
        ...

    def get_dlq_count_by_status(self, status: str) -> int:
        """상태별 DLQ 항목 수 반환"""
        ...

    def get_circuit_breaker_state(self, service: str) -> str:
        """서비스의 Circuit Breaker 상태 반환 (closed/open/half_open)"""
        ...

    def get_retry_success_rate(self, domain: str) -> float:
        """도메인별 재시도 성공률 반환 (0.0 ~ 100.0)"""
        ...
```

### Django ORM 구현 예시

```python
# selfhealing/adapters/metrics/django_adapter.py

from django.db.models import Count
from selfhealing.adapters.metrics.base import MetricSourceAdapter


class DjangoMetricSourceAdapter:
    """Django ORM 기반 메트릭 소스 어댑터"""

    def __init__(self, dlq_model, circuit_breaker_model):
        self.dlq_model = dlq_model
        self.cb_model = circuit_breaker_model

    def get_dlq_pending_count(self, domain: str) -> int:
        return self.dlq_model.objects.filter(
            domain=domain,
            status="pending"
        ).count()

    def get_dlq_count_by_status(self, status: str) -> int:
        return self.dlq_model.objects.filter(status=status).count()

    def get_circuit_breaker_state(self, service: str) -> str:
        try:
            cb = self.cb_model.objects.get(service_name=service)
            return cb.state
        except self.cb_model.DoesNotExist:
            return "closed"  # 기본값

    def get_retry_success_rate(self, domain: str) -> float:
        from django.db.models import Avg
        # 최근 1시간 기준 성공률 계산
        result = self.dlq_model.objects.filter(
            domain=domain,
            resolved_at__isnull=False
        ).aggregate(
            success_rate=Avg('is_success') * 100
        )
        return result['success_rate'] or 0.0
```

### 캐시 기반 구현 예시 (Redis 옵션)

```python
# selfhealing/adapters/metrics/redis_adapter.py

import redis
from selfhealing.adapters.metrics.base import MetricSourceAdapter


class RedisMetricSourceAdapter:
    """
    Redis 기반 메트릭 소스 어댑터.

    Write-Through 패턴으로 비즈니스 로직에서 DB 저장 시
    Redis에도 동시에 기록하는 경우 사용.
    """

    def __init__(self, redis_client: redis.Redis, prefix: str = "sh:metrics:"):
        self.redis = redis_client
        self.prefix = prefix

    def get_dlq_pending_count(self, domain: str) -> int:
        key = f"{self.prefix}dlq:pending:{domain}"
        value = self.redis.get(key)
        return int(value) if value else 0

    def get_circuit_breaker_state(self, service: str) -> str:
        key = f"{self.prefix}cb:state:{service}"
        return self.redis.get(key) or "closed"

    # Write-Through 헬퍼 메서드
    def increment_dlq_pending(self, domain: str) -> int:
        """DLQ 생성 시 호출"""
        key = f"{self.prefix}dlq:pending:{domain}"
        return self.redis.incr(key)

    def decrement_dlq_pending(self, domain: str) -> int:
        """DLQ 해결 시 호출"""
        key = f"{self.prefix}dlq:pending:{domain}"
        return self.redis.decr(key)
```

---

## 이벤트 기반 메트릭 업데이트

### DLQ 메트릭 이벤트 핸들러

```python
# selfhealing/metrics/event_handlers.py

from selfhealing.metrics.prometheus import (
    dlq_items_total,
    dlq_pending_count,
    dlq_created_total,
)


class DLQMetricEventHandler:
    """
    DLQ 이벤트 발생 시 메트릭을 업데이트하는 핸들러.

    이 핸들러는 DB 쿼리 없이 인메모리 카운터만 조작합니다.
    비즈니스 로직에서 DLQ 상태 변경 시 호출해야 합니다.
    """

    @staticmethod
    def on_item_created(domain: str, failure_type: str) -> None:
        """
        DLQ 항목 생성 시 호출.

        Args:
            domain: 도메인 이름 (payment, point, inventory 등)
            failure_type: 실패 유형 (PG_TIMEOUT, INSUFFICIENT_STOCK 등)
        """
        # Counter: 누적 카운트 증가
        dlq_items_total.labels(domain=domain, failure_type=failure_type).inc()
        dlq_created_total.labels(domain=domain).inc()

        # Gauge: 현재 대기 수 증가
        dlq_pending_count.labels(domain=domain).inc()

    @staticmethod
    def on_item_resolved(domain: str, resolution_type: str) -> None:
        """
        DLQ 항목 해결 시 호출.

        Args:
            domain: 도메인 이름
            resolution_type: 해결 유형 (auto_replay, manual, expired 등)
        """
        # Gauge: 현재 대기 수 감소
        dlq_pending_count.labels(domain=domain).dec()

    @staticmethod
    def on_item_failed(domain: str) -> None:
        """
        DLQ 재시도 실패 시 호출 (대기 수는 유지).
        """
        # 실패는 별도 메트릭으로 추적
        pass
```

### 비즈니스 로직 통합 예시

```python
# 사용자의 비즈니스 로직 (예: payment/services.py)

from selfhealing.metrics.event_handlers import DLQMetricEventHandler


class PaymentService:
    def __init__(self, dlq_repository, metric_handler=None):
        self.dlq_repo = dlq_repository
        self.metrics = metric_handler or DLQMetricEventHandler()

    def handle_payment_failure(self, order_id: str, error: Exception):
        # 1. DB에 DLQ 항목 저장
        dlq_item = self.dlq_repo.create(
            domain="payment",
            failure_type=self._classify_error(error),
            payload={"order_id": order_id},
        )

        # 2. 메트릭 업데이트 (DB 쿼리 없음)
        self.metrics.on_item_created(
            domain="payment",
            failure_type=dlq_item.failure_type,
        )

        return dlq_item

    def resolve_dlq_item(self, dlq_item):
        # 1. DB 상태 업데이트
        dlq_item.status = "resolved"
        dlq_item.resolved_at = timezone.now()
        self.dlq_repo.save(dlq_item)

        # 2. 메트릭 업데이트 (DB 쿼리 없음)
        self.metrics.on_item_resolved(
            domain=dlq_item.domain,
            resolution_type="auto_replay",
        )
```

### 데코레이터 패턴

```python
# selfhealing/metrics/decorators.py

from functools import wraps
from selfhealing.metrics.event_handlers import DLQMetricEventHandler


def track_dlq_creation(domain: str):
    """
    DLQ 생성 함수에 메트릭 추적을 추가하는 데코레이터.

    Usage:
        @track_dlq_creation(domain="payment")
        def create_payment_dlq(failure_type: str, payload: dict):
            return DLQItem.objects.create(...)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, failure_type: str, **kwargs):
            result = func(*args, failure_type=failure_type, **kwargs)
            DLQMetricEventHandler.on_item_created(domain, failure_type)
            return result
        return wrapper
    return decorator
```

---

## Lazy Sync (지연 동기화)

### 동기화 시점

Gauge 메트릭은 다음 시점에 DB와 동기화합니다:

| 시점 | 트리거 | 설명 |
|------|--------|------|
| **서버 시작** | 자동 (Jitter 적용) | 프로세스 재시작 시 인메모리 값 복원 |
| **수동 트리거** | API 호출 | 운영자가 명시적으로 동기화 요청 |
| **일일 배치** | 선택적 | 장기 Drift 보정 (권장: 비활성화) |

### Thundering Herd 방지 (Jitter)

분산 환경(K8s 등)에서 다수의 인스턴스가 동시에 시작될 때, 모든 노드가 일제히 DB에 쿼리하면 **Thundering Herd** 문제가 발생합니다.

```
┌─────────────────────────────────────────────────────────────┐
│              Thundering Herd 문제                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  배포 시점 (t=0)                                            │
│      │                                                      │
│      ▼                                                      │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐              │
│  │Pod 1 │ │Pod 2 │ │Pod 3 │ │ ...  │ │Pod N │              │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘              │
│     │        │        │        │        │                   │
│     └────────┴────────┴────────┴────────┘                   │
│                       │                                     │
│                       ▼                                     │
│              ┌─────────────────┐                           │
│              │    Database     │  ← 동시 N개 쿼리 = 마비   │
│              └─────────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

**해결책: Jitter (무작위 지연)**

```python
# selfhealing/metrics/jitter.py

import random
import asyncio
from functools import wraps


def with_jitter(max_delay_seconds: float = 60.0):
    """
    동기화 함수에 무작위 지연을 추가하는 데코레이터.

    분산 환경에서 동시 시작되는 인스턴스들의 DB 쿼리를
    시간적으로 분산시켜 Thundering Herd를 방지합니다.

    Args:
        max_delay_seconds: 최대 지연 시간 (초). 기본 60초.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            jitter = random.uniform(0, max_delay_seconds)
            time.sleep(jitter)
            return func(*args, **kwargs)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            jitter = random.uniform(0, max_delay_seconds)
            await asyncio.sleep(jitter)
            return await func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return wrapper
    return decorator
```

**적용 예시:**

```python
# selfhealing/metrics/reconciler.py

from selfhealing.metrics.jitter import with_jitter


class MetricReconciler:
    @with_jitter(max_delay_seconds=60.0)
    def sync_all_gauges_with_jitter(self) -> dict:
        """
        Jitter가 적용된 Gauge 동기화.

        서버 시작 시 이 메서드를 사용하여
        분산 환경에서 DB 부하를 분산시킵니다.
        """
        return self.sync_all_gauges()
```

**서버 시작 시 적용:**

```python
# Django: selfhealing/apps.py

class SelfHealingConfig(AppConfig):
    name = 'selfhealing'

    def ready(self):
        import threading
        from selfhealing.metrics.reconciler import MetricReconciler

        def delayed_sync():
            try:
                reconciler = MetricReconciler(get_adapter())
                # Jitter 적용: 0~60초 사이 무작위 지연 후 동기화
                reconciler.sync_all_gauges_with_jitter()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Metric sync on startup failed (non-critical): {e}"
                )

        # 비동기로 실행하여 서버 시작을 블로킹하지 않음
        thread = threading.Thread(target=delayed_sync, daemon=True)
        thread.start()
```

**Jitter 설정:**

```python
# selfhealing/config.py

class MetricCollectionSettings(BaseSettings):
    # ... 기존 설정 ...

    # Jitter 설정
    jitter_enabled: bool = True           # Jitter 활성화
    jitter_max_delay_seconds: float = 60.0  # 최대 지연 시간

    class Config:
        env_prefix = "SELFHEALING_METRICS_"
```

| 환경 | 권장 Jitter | 이유 |
|------|-------------|------|
| 단일 서버 | 0초 (비활성화) | 분산 필요 없음 |
| K8s 10 Pods | 30초 | 적당한 분산 |
| K8s 100+ Pods | 60초 | 충분한 분산 필요 |

### Reconciler 구현

```python
# selfhealing/metrics/reconciler.py

import logging
from datetime import datetime, timezone
from typing import Optional

from selfhealing.adapters.metrics.base import MetricSourceAdapter
from selfhealing.core.domains import DOMAINS
from selfhealing.metrics.prometheus import (
    dlq_pending_count,
    dlq_items_by_status,
    circuit_breaker_state,
    retry_success_rate,
)

logger = logging.getLogger(__name__)


class MetricReconciler:
    """
    메트릭과 실제 데이터 소스 간의 정합성을 맞추는 Reconciler.

    Gauge 타입 메트릭만 동기화합니다.
    Counter와 Histogram은 Push 시점에 정확하므로 동기화 불필요.

    Note:
        모든 시간 처리는 timezone-aware datetime을 사용합니다.
        datetime.utcnow()는 Python 3.12에서 deprecated되었으므로
        datetime.now(timezone.utc)를 사용합니다.
    """

    def __init__(self, adapter: MetricSourceAdapter):
        self.adapter = adapter
        self._last_sync: Optional[datetime] = None

    def sync_all_gauges(self) -> dict:
        """
        모든 Gauge 메트릭을 데이터 소스와 동기화.

        Returns:
            동기화된 메트릭 값 딕셔너리
        """
        result = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "dlq_pending": {},
            "circuit_breaker_states": {},
            "retry_success_rates": {},
        }

        # DLQ 대기 수 동기화
        for domain in DOMAINS:
            try:
                actual = self.adapter.get_dlq_pending_count(domain)
                dlq_pending_count.labels(domain=domain).set(actual)
                result["dlq_pending"][domain] = actual
            except Exception as e:
                logger.warning(f"Failed to sync DLQ pending for {domain}: {e}")

        # Circuit Breaker 상태 동기화
        for service in self._get_services():
            try:
                state = self.adapter.get_circuit_breaker_state(service)
                state_value = {"closed": 0, "open": 1, "half_open": 2}.get(state, 0)
                circuit_breaker_state.labels(service=service).set(state_value)
                result["circuit_breaker_states"][service] = state
            except Exception as e:
                logger.warning(f"Failed to sync CB state for {service}: {e}")

        # 재시도 성공률 동기화
        for domain in DOMAINS:
            try:
                rate = self.adapter.get_retry_success_rate(domain)
                retry_success_rate.labels(domain=domain).set(rate)
                result["retry_success_rates"][domain] = rate
            except Exception as e:
                logger.warning(f"Failed to sync retry rate for {domain}: {e}")

        self._last_sync = datetime.now(timezone.utc)
        logger.info(f"Metrics reconciled: {result}")

        return result

    def sync_domain_gauges(self, domain: str) -> dict:
        """특정 도메인의 Gauge만 동기화"""
        actual = self.adapter.get_dlq_pending_count(domain)
        dlq_pending_count.labels(domain=domain).set(actual)

        rate = self.adapter.get_retry_success_rate(domain)
        retry_success_rate.labels(domain=domain).set(rate)

        return {"domain": domain, "dlq_pending": actual, "retry_rate": rate}

    def _get_services(self) -> list[str]:
        """Circuit Breaker가 적용된 서비스 목록"""
        # 설정에서 로드하거나 어댑터에서 조회
        return ["toss_payment", "external_api", "notification"]

    @property
    def last_sync_time(self) -> Optional[datetime]:
        return self._last_sync

    def _capture_current_gauges(self) -> dict:
        """현재 Gauge 값 캡처 (Drift 계산용)"""
        # Prometheus 라이브러리에서 현재 값 읽기
        # 구현은 사용하는 라이브러리에 따라 다름
        return {"dlq_pending": {}, "circuit_breaker_states": {}}
```

### 서버 시작 시 자동 동기화

```python
# Django: selfhealing/apps.py

from django.apps import AppConfig


class SelfHealingConfig(AppConfig):
    name = 'selfhealing'

    def ready(self):
        # 서버 시작 시 메트릭 동기화
        from selfhealing.metrics.reconciler import MetricReconciler
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter

        try:
            adapter = DjangoMetricSourceAdapter(
                dlq_model=self._get_dlq_model(),
                circuit_breaker_model=self._get_cb_model(),
            )
            reconciler = MetricReconciler(adapter)
            reconciler.sync_all_gauges()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                f"Metric sync on startup failed (non-critical): {e}"
            )
```

```python
# FastAPI: main.py

from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 메트릭 동기화
    from selfhealing.metrics.reconciler import MetricReconciler
    reconciler = MetricReconciler(get_adapter())
    reconciler.sync_all_gauges()

    yield

    # Shutdown: 정리 작업

app = FastAPI(lifespan=lifespan)
```

### 수동 동기화 API

```python
# selfhealing/api/views/metrics.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser

from selfhealing.metrics.reconciler import MetricReconciler


class MetricSyncView(APIView):
    """
    메트릭 수동 동기화 API.

    POST /api/self-healing/metrics/sync/
    """
    permission_classes = [IsAdminUser]

    def post(self, request):
        reconciler = MetricReconciler(get_adapter())
        result = reconciler.sync_all_gauges()

        # Audit 로깅 (Gauge Sync 시점만 기록)
        from selfhealing.services.audit import AuditService
        AuditService.log_action(
            action="metric_sync",
            actor=request.user.username,
            details=result,
        )

        return Response({
            "status": "synced",
            "result": result,
        })
```

---

## Audit 연동

### 기록 대상

모든 메트릭 변경을 기록하면 로그 폭발이 발생합니다. **Gauge Sync 시점만** 기록합니다.

| 이벤트 | Audit 기록 | 이유 |
|--------|-----------|------|
| Counter 증가 | ❌ | 빈번, 원본 이벤트에서 이미 추적 |
| Histogram 관측 | ❌ | 빈번, 원본 이벤트에서 이미 추적 |
| **Gauge Sync** | ✅ | 수치 보정 시점, 역추적 필요 |
| **수동 Sync 요청** | ✅ | 운영자 행위, 감사 필요 |

### Audit 로그 형식

```json
{
  "timestamp": "2024-01-15T10:30:00.123456Z",
  "action": "metric_sync",
  "actor": "system",  // 또는 운영자 ID
  "trigger": "startup",  // startup, manual, scheduled
  "before": {
    "dlq_pending": {"payment": 0, "point": 0}
  },
  "after": {
    "dlq_pending": {"payment": 42, "point": 7}
  },
  "drift_detected": true,
  "drift_details": {
    "payment": {"expected": 0, "actual": 42, "diff": 42}
  }
}
```

### Drift 감지 및 알림

Drift가 임계값을 초과하면 단순 로그가 아닌 **인시던트 알림**을 발생시킵니다.

```python
# selfhealing/metrics/reconciler.py (확장)

from datetime import datetime, timezone
from selfhealing.services.incident import IncidentService  # 또는 SecurityViolationService


class DriftSeverity:
    """Drift 심각도 레벨"""
    NORMAL = "normal"      # < 5%: 정상 범위
    WARNING = "warning"    # 5~20%: 경고, 로그만 기록
    CRITICAL = "critical"  # 20~50%: 심각, 알림 발송
    INCIDENT = "incident"  # > 50%: 인시던트, 이벤트 유실 의심


class MetricReconciler:
    def __init__(
        self,
        adapter: MetricSourceAdapter,
        incident_service: IncidentService = None,
    ):
        self.adapter = adapter
        self.incident_service = incident_service
        self._last_sync: datetime | None = None

    def sync_with_drift_detection(self) -> dict:
        """동기화 시 Drift를 감지하고 심각도에 따라 대응"""
        before = self._capture_current_gauges()
        result = self.sync_all_gauges()
        after = self._capture_current_gauges()

        drift = self._calculate_drift(before, after)
        severity = self._classify_drift_severity(drift)

        # 심각도별 대응
        if severity == DriftSeverity.INCIDENT:
            self._raise_incident(drift)
        elif severity == DriftSeverity.CRITICAL:
            self._send_alert(drift)
        elif severity == DriftSeverity.WARNING:
            logger.warning(f"Metric drift detected: {drift}")

        return {
            **result,
            "drift": drift,
            "severity": severity,
        }

    def _classify_drift_severity(self, drift: dict) -> str:
        """Drift 비율에 따른 심각도 분류"""
        max_drift = drift.get("max_drift_percent", 0)

        if max_drift > 50:
            return DriftSeverity.INCIDENT
        elif max_drift > 20:
            return DriftSeverity.CRITICAL
        elif max_drift > 5:
            return DriftSeverity.WARNING
        return DriftSeverity.NORMAL

    def _raise_incident(self, drift: dict) -> None:
        """
        50% 이상 Drift: 인시던트 발생.

        이 수준의 Drift는 단순한 오차가 아니라
        '이벤트 유실'을 의미합니다.
        """
        logger.critical(f"METRIC INTEGRITY INCIDENT: {drift}")

        if self.incident_service:
            self.incident_service.create_incident(
                title="메트릭 신뢰도 붕괴 감지",
                severity="critical",
                category="metric_drift",
                details={
                    "drift": drift,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "possible_cause": "이벤트 핸들러 미호출 또는 시스템 장애",
                    "recommended_action": [
                        "이벤트 핸들러 호출 여부 확인",
                        "최근 배포 변경사항 검토",
                        "로그에서 누락된 이벤트 추적",
                    ],
                },
            )

    def _send_alert(self, drift: dict) -> None:
        """20~50% Drift: 알림 발송"""
        logger.error(f"Critical metric drift: {drift}")

        # Prometheus Alertmanager 또는 자체 알림 시스템 연동
        # 예: Slack, PagerDuty, 이메일 등

    def _calculate_drift(self, before: dict, after: dict) -> dict:
        """Drift 계산"""
        drift_details = {}
        max_drift_percent = 0.0

        for key in after.get("dlq_pending", {}):
            before_val = before.get("dlq_pending", {}).get(key, 0)
            after_val = after.get("dlq_pending", {}).get(key, 0)

            if before_val == 0 and after_val == 0:
                drift_percent = 0.0
            elif before_val == 0:
                drift_percent = 100.0  # 0에서 값이 생긴 경우
            else:
                drift_percent = abs(after_val - before_val) / before_val * 100

            drift_details[key] = {
                "before": before_val,
                "after": after_val,
                "diff": after_val - before_val,
                "drift_percent": round(drift_percent, 2),
            }
            max_drift_percent = max(max_drift_percent, drift_percent)

        return {
            "details": drift_details,
            "max_drift_percent": round(max_drift_percent, 2),
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }
```

**Drift 심각도 임계값:**

| 심각도 | Drift 비율 | 의미 | 대응 |
|--------|-----------|------|------|
| `normal` | < 5% | 정상 오차 범위 | 로그만 기록 |
| `warning` | 5~20% | 경고 | 로그 + 모니터링 |
| `critical` | 20~50% | 심각 | 알림 발송 |
| `incident` | > 50% | 이벤트 유실 | 인시던트 생성 |

> **Note**: 위 임계값은 기본값이며, API를 통해 동적으로 조정할 수 있습니다.

---

## Drift 임계값 동적 설정 API

Drift 임계값은 운영 환경에 따라 조정이 필요할 수 있습니다. API를 통해 실시간으로 변경할 수 있습니다.

### 설정 모델

```python
# selfhealing/models/drift_config.py

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass
class DriftThresholdConfig:
    """
    Drift 임계값 설정.

    운영자가 동적으로 조정할 수 있으며,
    변경 시 Audit 로그가 기록됩니다.
    """

    # 임계값 (0.0 ~ 1.0)
    warning_threshold: float = 0.05    # 5%
    critical_threshold: float = 0.20   # 20%
    incident_threshold: float = 0.50   # 50%

    # 알림 설정
    alert_enabled: bool = True
    incident_auto_create: bool = True

    # 메타데이터
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None

    def __post_init__(self):
        self._validate()

    def _validate(self):
        """임계값 유효성 검사"""
        if not (0 < self.warning_threshold < self.critical_threshold < self.incident_threshold <= 1.0):
            raise ValueError(
                "Thresholds must be: 0 < warning < critical < incident <= 1.0"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DriftThresholdConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

### API 엔드포인트

```python
# selfhealing/api/views/drift_config.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from rest_framework import status

from selfhealing.models.drift_config import DriftThresholdConfig
from selfhealing.core.state_backend import get_state_backend
from selfhealing.services.audit import AuditService
from selfhealing.context.actor_context import ActorContext


class DriftThresholdConfigView(APIView):
    """
    Drift 임계값 설정 API.

    GET  /api/self-healing/config/drift-thresholds/
    PUT  /api/self-healing/config/drift-thresholds/
    POST /api/self-healing/config/drift-thresholds/reset/
    """

    permission_classes = [IsAdminUser]
    STORAGE_KEY = "drift_threshold_config"

    def get(self, request):
        """현재 Drift 임계값 설정 조회"""
        config = self._get_config()
        return Response({
            "config": config.to_dict(),
            "thresholds_percent": {
                "warning": f"{config.warning_threshold * 100:.1f}%",
                "critical": f"{config.critical_threshold * 100:.1f}%",
                "incident": f"{config.incident_threshold * 100:.1f}%",
            },
        })

    def put(self, request):
        """Drift 임계값 설정 업데이트"""
        from datetime import datetime, timezone

        current = self._get_config()
        actor = ActorContext.get_current()

        try:
            # 새 설정 생성
            new_config = DriftThresholdConfig(
                warning_threshold=request.data.get(
                    "warning_threshold",
                    current.warning_threshold
                ),
                critical_threshold=request.data.get(
                    "critical_threshold",
                    current.critical_threshold
                ),
                incident_threshold=request.data.get(
                    "incident_threshold",
                    current.incident_threshold
                ),
                alert_enabled=request.data.get(
                    "alert_enabled",
                    current.alert_enabled
                ),
                incident_auto_create=request.data.get(
                    "incident_auto_create",
                    current.incident_auto_create
                ),
                updated_at=datetime.now(timezone.utc).isoformat(),
                updated_by=actor.actor_id,
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 저장
        backend = get_state_backend()
        backend.save(self.STORAGE_KEY, new_config.to_dict())

        # Audit 기록
        AuditService.log_action(
            action="drift_threshold_updated",
            actor=actor.actor_id,
            details={
                "before": current.to_dict(),
                "after": new_config.to_dict(),
            },
        )

        return Response({
            "status": "updated",
            "config": new_config.to_dict(),
        })

    def _get_config(self) -> DriftThresholdConfig:
        """저장된 설정 로드 또는 기본값 반환"""
        backend = get_state_backend()
        data = backend.load(self.STORAGE_KEY)

        if data:
            return DriftThresholdConfig.from_dict(data)
        return DriftThresholdConfig()  # 기본값


class DriftThresholdResetView(APIView):
    """Drift 임계값 기본값으로 리셋"""

    permission_classes = [IsAdminUser]

    def post(self, request):
        from datetime import datetime, timezone

        actor = ActorContext.get_current()
        backend = get_state_backend()

        # 현재 설정 조회 (Audit용)
        current_data = backend.load(DriftThresholdConfigView.STORAGE_KEY)

        # 기본값으로 리셋
        default = DriftThresholdConfig(
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=actor.actor_id,
        )
        backend.save(DriftThresholdConfigView.STORAGE_KEY, default.to_dict())

        # Audit 기록
        AuditService.log_action(
            action="drift_threshold_reset",
            actor=actor.actor_id,
            details={
                "before": current_data,
                "after": default.to_dict(),
            },
        )

        return Response({
            "status": "reset",
            "config": default.to_dict(),
        })
```

### URL 설정

```python
# selfhealing/api/urls.py

from django.urls import path
from selfhealing.api.views.drift_config import (
    DriftThresholdConfigView,
    DriftThresholdResetView,
)

urlpatterns = [
    # ... 기존 URL ...

    # Drift 임계값 설정
    path(
        "config/drift-thresholds/",
        DriftThresholdConfigView.as_view(),
        name="drift-threshold-config",
    ),
    path(
        "config/drift-thresholds/reset/",
        DriftThresholdResetView.as_view(),
        name="drift-threshold-reset",
    ),
]
```

### Reconciler에서 동적 설정 사용

```python
# selfhealing/metrics/reconciler.py (업데이트)

from selfhealing.models.drift_config import DriftThresholdConfig
from selfhealing.core.state_backend import get_state_backend


class MetricReconciler:
    def __init__(
        self,
        adapter: MetricSourceAdapter,
        incident_service: IncidentService = None,
    ):
        self.adapter = adapter
        self.incident_service = incident_service
        self._last_sync: datetime | None = None

    def _get_drift_config(self) -> DriftThresholdConfig:
        """동적으로 저장된 Drift 설정 로드"""
        backend = get_state_backend()
        data = backend.load("drift_threshold_config")

        if data:
            return DriftThresholdConfig.from_dict(data)
        return DriftThresholdConfig()  # 기본값

    def _classify_drift_severity(self, drift: dict) -> str:
        """동적 설정 기반 Drift 심각도 분류"""
        config = self._get_drift_config()
        max_drift = drift.get("max_drift_percent", 0) / 100  # % to decimal

        if max_drift > config.incident_threshold:
            return DriftSeverity.INCIDENT
        elif max_drift > config.critical_threshold:
            return DriftSeverity.CRITICAL
        elif max_drift > config.warning_threshold:
            return DriftSeverity.WARNING
        return DriftSeverity.NORMAL

    def _should_create_incident(self) -> bool:
        """인시던트 자동 생성 여부"""
        config = self._get_drift_config()
        return config.incident_auto_create

    def _should_send_alert(self) -> bool:
        """알림 발송 여부"""
        config = self._get_drift_config()
        return config.alert_enabled
```

### API 사용 예시

**현재 설정 조회:**

```bash
curl -X GET /api/self-healing/config/drift-thresholds/ \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "config": {
    "warning_threshold": 0.05,
    "critical_threshold": 0.20,
    "incident_threshold": 0.50,
    "alert_enabled": true,
    "incident_auto_create": true,
    "updated_at": "2024-01-15T10:30:00+00:00",
    "updated_by": "admin"
  },
  "thresholds_percent": {
    "warning": "5.0%",
    "critical": "20.0%",
    "incident": "50.0%"
  }
}
```

**임계값 업데이트:**

```bash
curl -X PUT /api/self-healing/config/drift-thresholds/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "warning_threshold": 0.10,
    "critical_threshold": 0.30,
    "incident_threshold": 0.60
  }'
```

**기본값으로 리셋:**

```bash
curl -X POST /api/self-healing/config/drift-thresholds/reset/ \
  -H "Authorization: Bearer $TOKEN"
```

**인시던트 자동 생성 비활성화:**

```bash
curl -X PUT /api/self-healing/config/drift-thresholds/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"incident_auto_create": false}'
```

### 환경 설정 오버라이드

API 설정보다 환경 변수가 우선하도록 할 수도 있습니다:

```python
# selfhealing/config.py

class MetricCollectionSettings(BaseSettings):
    # 환경 변수 기반 오버라이드 (API 설정보다 우선)
    drift_warning_threshold_override: float | None = None
    drift_critical_threshold_override: float | None = None
    drift_incident_threshold_override: float | None = None

    class Config:
        env_prefix = "SELFHEALING_METRICS_"
```

```python
# Reconciler에서 우선순위 적용
def _get_effective_threshold(self, config: DriftThresholdConfig) -> DriftThresholdConfig:
    """환경 변수 오버라이드 적용"""
    settings = MetricCollectionSettings()

    return DriftThresholdConfig(
        warning_threshold=settings.drift_warning_threshold_override or config.warning_threshold,
        critical_threshold=settings.drift_critical_threshold_override or config.critical_threshold,
        incident_threshold=settings.drift_incident_threshold_override or config.incident_threshold,
        alert_enabled=config.alert_enabled,
        incident_auto_create=config.incident_auto_create,
    )
```

**인시던트 연동 설정:**

```python
# selfhealing/config.py

class MetricCollectionSettings(BaseSettings):
    # ... 기존 설정 ...

    # Drift 감지
    drift_detection_enabled: bool = True
    drift_warning_threshold: float = 0.05   # 5%
    drift_critical_threshold: float = 0.20  # 20%
    drift_incident_threshold: float = 0.50  # 50%
    drift_incident_enabled: bool = True     # 인시던트 자동 생성
```

---

## 메트릭 신뢰도 레벨

### 문서화된 정확도

사용자에게 메트릭의 신뢰도 수준을 명확히 전달합니다.

```python
# selfhealing/metrics/reliability.py

from enum import Enum


class MetricReliability(Enum):
    """메트릭 신뢰도 레벨"""

    EXACT = "exact"           # 100% 정확, 원본과 동일
    EVENTUAL = "eventual"     # ~99%, 재시작 시 동기화
    APPROXIMATE = "approx"    # ~95%, 샘플링 또는 추정


METRIC_RELIABILITY_MAP = {
    # Counter: 누적값, 증가만 하므로 100% 정확
    "dlq_items_total": MetricReliability.EXACT,
    "retry_outcomes_total": MetricReliability.EXACT,
    "sla_breach_total": MetricReliability.EXACT,

    # Histogram: 관측 시점 기록, 100% 정확
    "recovery_time_seconds": MetricReliability.EXACT,
    "retry_attempts_total": MetricReliability.EXACT,

    # Gauge: 상태값, 재시작 시 동기화
    "dlq_pending_count": MetricReliability.EVENTUAL,
    "circuit_breaker_state": MetricReliability.EVENTUAL,
    "retry_success_rate": MetricReliability.EVENTUAL,
}
```

### 정확한 값이 필요한 경우

```python
# selfhealing/api/views/accurate_stats.py

class AccurateDLQCountView(APIView):
    """
    정확한 DLQ 수 조회 API (DB 직접 쿼리).

    GET /api/self-healing/dlq/accurate-count/

    메트릭의 Gauge 값이 아닌 실제 DB 값을 반환합니다.
    빈번한 호출은 DB 부하를 유발할 수 있습니다.
    """

    def get(self, request):
        adapter = get_adapter()
        result = {}

        for domain in DOMAINS:
            result[domain] = adapter.get_dlq_pending_count(domain)

        return Response({
            "source": "database",
            "reliability": "exact",
            "data": result,
            "note": "For real-time monitoring, use /metrics endpoint (eventual consistency)"
        })
```

---

## 시간 무결성 (Timezone-Awareness)

### 표준 시간 처리 원칙

모든 시간 처리는 **timezone-aware datetime**을 사용합니다.

```python
# ❌ 잘못된 예 (Python 3.12에서 deprecated)
from datetime import datetime
now = datetime.utcnow()  # naive datetime, timezone 정보 없음

# ✅ 올바른 예
from datetime import datetime, timezone
now = datetime.now(timezone.utc)  # aware datetime, UTC 명시
```

### 왜 중요한가?

| 문제 상황 | `utcnow()` 사용 시 | `now(timezone.utc)` 사용 시 |
|----------|-------------------|---------------------------|
| 다른 timezone과 비교 | TypeError 발생 가능 | 안전하게 비교 가능 |
| DB 저장 후 로드 | timezone 정보 손실 | timezone 정보 유지 |
| 로그 분석 | 어느 시간대인지 불명확 | UTC 명시, 추적 용이 |
| 글로벌 배포 | 서버 위치에 따라 다른 결과 | 전 세계 동일한 결과 |

### 코드 표준

```python
# selfhealing/utils/time.py

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """현재 UTC 시간 반환 (timezone-aware)"""
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime) -> datetime:
    """naive datetime을 UTC로 변환"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_iso_string(dt: Optional[datetime]) -> Optional[str]:
    """datetime을 ISO 8601 문자열로 변환"""
    if dt is None:
        return None
    return ensure_aware(dt).isoformat()
```

### Audit 로그 시간 형식

```json
{
  "timestamp": "2024-01-15T10:30:00.123456+00:00",
  "synced_at": "2024-01-15T10:30:00.123456+00:00",
  "calculated_at": "2024-01-15T10:30:00.123456+00:00"
}
```

> **Note**: ISO 8601 형식에 `+00:00` 또는 `Z` suffix가 포함되어
> UTC임을 명시합니다.

---

## 설정

### 환경 설정

```python
# selfhealing/config.py

from pydantic import BaseSettings


class MetricCollectionSettings(BaseSettings):
    """메트릭 수집 설정"""

    # 동기화 설정
    sync_on_startup: bool = True          # 서버 시작 시 동기화
    scheduled_sync_enabled: bool = False  # 주기적 동기화 (권장: 비활성화)
    scheduled_sync_interval: int = 86400  # 주기 (초), 기본 24시간

    # Jitter 설정 (Thundering Herd 방지)
    jitter_enabled: bool = True           # Jitter 활성화
    jitter_max_delay_seconds: float = 60.0  # 최대 지연 시간 (초)

    # 어댑터 설정
    adapter_type: str = "django"          # django, redis, custom
    redis_prefix: str = "sh:metrics:"     # Redis 어댑터용 키 프리픽스

    # Drift 감지 (거버넌스 레벨)
    drift_detection_enabled: bool = True
    drift_warning_threshold: float = 0.05   # 5% - 경고
    drift_critical_threshold: float = 0.20  # 20% - 심각, 알림 발송
    drift_incident_threshold: float = 0.50  # 50% - 인시던트, 이벤트 유실
    drift_incident_enabled: bool = True     # 인시던트 자동 생성
    drift_alert_enabled: bool = True        # 알림 발송 활성화

    class Config:
        env_prefix = "SELFHEALING_METRICS_"
```

### 어댑터 팩토리

```python
# selfhealing/adapters/metrics/factory.py

from selfhealing.config import MetricCollectionSettings
from selfhealing.adapters.metrics.base import MetricSourceAdapter


def get_metric_adapter(settings: MetricCollectionSettings = None) -> MetricSourceAdapter:
    """설정에 따라 적절한 어댑터 반환"""
    settings = settings or MetricCollectionSettings()

    if settings.adapter_type == "django":
        from selfhealing.adapters.metrics.django_adapter import DjangoMetricSourceAdapter
        return DjangoMetricSourceAdapter(
            dlq_model=get_dlq_model(),
            circuit_breaker_model=get_cb_model(),
        )

    elif settings.adapter_type == "redis":
        from selfhealing.adapters.metrics.redis_adapter import RedisMetricSourceAdapter
        import redis
        return RedisMetricSourceAdapter(
            redis_client=redis.from_url(settings.redis_url),
            prefix=settings.redis_prefix,
        )

    else:
        raise ValueError(f"Unknown adapter type: {settings.adapter_type}")
```

---

## 운영 권장사항

### 1. 기본 권장 설정

```bash
# .env
SELFHEALING_METRICS_SYNC_ON_STARTUP=true
SELFHEALING_METRICS_SCHEDULED_SYNC_ENABLED=false
SELFHEALING_METRICS_ADAPTER_TYPE=django
SELFHEALING_METRICS_DRIFT_DETECTION_ENABLED=true
SELFHEALING_METRICS_DRIFT_INCIDENT_ENABLED=true
```

### 2. 분산 환경 (K8s) 설정

```bash
# .env (K8s 환경)
SELFHEALING_METRICS_JITTER_ENABLED=true
SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS=60.0  # Pod 수에 따라 조정
```

### 3. Redis 사용 시 (선택적)

Redis를 사용하면 Write-Through 패턴으로 더 정확한 Gauge 값을 유지할 수 있습니다.

```bash
# Redis 활성화 시
SELFHEALING_METRICS_ADAPTER_TYPE=redis
SELFHEALING_METRICS_REDIS_URL=redis://localhost:6379/1
```

### 4. 모니터링 체크리스트

| 항목 | 확인 방법 | 기대값 |
|------|----------|--------|
| 메트릭 엔드포인트 | `curl /metrics` | 200 OK |
| Gauge 동기화 상태 | 로그에서 "Metrics reconciled" 확인 | 서버 시작 시 1회 |
| Jitter 적용 | 로그 시간 확인 | 인스턴스별 다른 시간 |
| Drift 경고 | 로그에서 "Metric drift detected" 검색 | 없음 (정상) |
| Drift 인시던트 | 로그에서 "METRIC INTEGRITY INCIDENT" 검색 | 없음 (정상) |

### 5. 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| Gauge 값이 0으로 초기화됨 | 프로세스 재시작 | `sync_on_startup=true` 확인 |
| Gauge 값이 실제와 다름 | Drift 발생 | 수동 `/metrics/sync/` 호출 |
| 메트릭 수집 지연 | DB 쿼리 병목 | Redis 어댑터로 전환 고려 |
| DB 과부하 (K8s 배포 시) | Thundering Herd | Jitter 활성화 및 값 증가 |
| 50%+ Drift 발생 | 이벤트 핸들러 미호출 | 비즈니스 로직에서 핸들러 호출 확인 |
| Timezone 관련 오류 | naive datetime 사용 | `datetime.now(timezone.utc)` 사용 |

---

## 아키텍처 결정 기록 (ADR)

### ADR-001: Hybrid 메트릭 수집 방식 채택

**상태**: 채택됨

**컨텍스트**:
- Pull 방식은 DB 부하 및 결합도 문제
- Push 방식은 장애 시 데이터 불일치 가능성

**결정**: Push 위주 + Lazy Sync 하이브리드 방식 채택

**결과**:
- Counter/Histogram: Push Only (100% 정확)
- Gauge: Push + 재시작 시 동기화 (~99% 정확)

---

### ADR-002: Jitter를 통한 Thundering Herd 방지

**상태**: 채택됨

**컨텍스트**:
- K8s 환경에서 다수 Pod 동시 시작 시 DB 마비 위험

**결정**: 서버 시작 시 0~60초 무작위 지연 적용

**결과**:
- DB 부하 분산
- 대규모 분산 환경 지원 증명

---

### ADR-003: Drift 심각도 레벨 거버넌스

**상태**: 채택됨

**컨텍스트**:
- 50% 이상 Drift는 단순 오차가 아닌 이벤트 유실 신호

**결정**: Drift 임계값별 차등 대응 (로그 → 알림 → 인시던트)

**결과**:
- 5%: 로그만
- 20%: 알림 발송
- 50%: 인시던트 자동 생성

---

### ADR-004: Timezone-Aware Datetime 강제

**상태**: 채택됨

**컨텍스트**:
- `datetime.utcnow()` Python 3.12 deprecated
- 글로벌 배포 시 시간 정합성 문제

**결정**: 모든 시간 처리에 `datetime.now(timezone.utc)` 사용

**결과**:
- 전 세계 동일한 시간 표현
- 타임존 간 안전한 비교

---

## 관련 문서

- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 정의 및 Prometheus 설정
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드
- [07_CONTROL_API.md](07_CONTROL_API.md) - API 보안 및 Audit
