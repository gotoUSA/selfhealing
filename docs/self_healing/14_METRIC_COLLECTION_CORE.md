# Self-Healing 메트릭 수집 전략 - Core

> **문서 분할 안내**: 메트릭 수집 문서가 크기 때문에 2개로 분할되었습니다.
> - [14_METRIC_COLLECTION_CORE.md](14_METRIC_COLLECTION_CORE.md) - 수집 전략 및 구현 (현재 문서)
> - [15_METRIC_COLLECTION_ADVANCED.md](15_METRIC_COLLECTION_ADVANCED.md) - Audit, Drift 감지, 설정

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

**Jitter 설정:**

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

## 관련 문서

- [15_METRIC_COLLECTION_ADVANCED.md](15_METRIC_COLLECTION_ADVANCED.md) - Audit, Drift 감지, 설정
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 정의 및 Prometheus 설정
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드
- [07_CONTROL_API.md](07_CONTROL_API.md) - API 보안 및 Audit
