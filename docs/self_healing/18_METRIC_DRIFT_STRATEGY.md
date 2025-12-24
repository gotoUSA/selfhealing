# Self-Healing 메트릭 Drift 전략 - 비침습적 설계

> **철학**: Self-Healing 엔진은 비즈니스 DB를 "침투"하지 않는다.  
> 오직 **수동적 관찰**과 **명시적 요청**만 허용한다.

---

## 1. 문제 정의

### 1.1 철학적 충돌

기존 `DriftDetector` 설계에서 발견된 문제:

```python
# ❌ 문제: 주기적 DB 폴링 = 침투
class DriftDetector:
    def detect_dlq_drift(self, domain: str):
        actual = float(self.adapter.get_dlq_pending_count(domain))  # DB 조회!
```

| 원칙 | 위반 여부 | 설명 |
|------|----------|------|
| ADR-001 "DB 직접 의존 금지" | ❌ 위반 | 정상 운영 중 DB 폴링 |
| 비침습성 원칙 | ❌ 위반 | 능동적 감시 = 타 시스템 간섭 |
| Push 위주 설계 | ❌ 위반 | Pull 패턴 사용 |

### 1.2 해결 목표

```
┌─────────────────────────────────────────────────────────────┐
│                    비침습적 Drift 관리                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ❌ 제거                    ✅ 허용                        │
│   ─────────────              ─────────────                  │
│   • 주기적 DB 폴링           • 서버 시작 시 1회 초기화       │
│   • 자동 Drift 감지          • 운영자 수동 트리거            │
│   • 백그라운드 PeriodicTask  • Push 이벤트 기반 업데이트     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 구현 로드맵

### 2.1 Phase 개요

| Phase | 항목 | 핵심 포인트 | 철학적 의의 | 우선순위 |
|-------|------|------------|------------|----------|
| **Phase 1** | Poll 제거 + Manual API | PeriodicTask 삭제, 보고 전용 API | 비침습성 확보 | 🔴 긴급 |
| **Phase 2** | Startup Hydration | 서버 기동 시 1회 DB 조회 | 안전한 초기화 | 🟠 높음 |
| **Phase 3** | Push-Only 강화 | 이벤트 훅에서 Gauge 업데이트 | 실시간성 확보 | 🟡 중간 |
| **Phase 4** | Redis Air-Gap (선택) | 공유 캐시로 DB 격리 | 완전 분리 | 🟢 향후 |

---

## 3. Phase 1: Poll 제거 + Manual API

### 3.1 목표

- 주기적 DB 폴링 완전 제거
- 수동 동기화 API 제공 (운영자 명시적 요청만 허용)
- Drift "감지"와 "보정" 분리

### 3.2 제거 대상

```python
# selfhealing/metrics/drift_detector.py

# ❌ 제거: 자동 Drift 감지 스케줄
# @periodic_task(run_every=timedelta(minutes=5))
# def auto_detect_drift():
#     detector.detect_all_drifts()
```

### 3.3 Manual Sync API 설계

```
POST /api/self-healing/metrics/sync/
├── 권한: IsAdminUser
├── 기능: 현재 DB 상태로 Gauge 동기화
├── 응답: 동기화 결과 + Drift 리포트
└── Audit: 누가 언제 트리거했는지 기록

GET /api/self-healing/metrics/drift-report/
├── 권한: IsAdminUser
├── 기능: 현재 Drift 상태 조회 (읽기 전용)
├── 특징: DB 조회 수행 (수동 요청이므로 허용)
└── 응답: 인메모리 vs DB 비교 결과
```

### 3.4 API 상세 명세

#### POST /api/self-healing/metrics/sync/

**Request:**
```json
{
  "domains": ["payment", "point"],  // optional, 미지정 시 전체
  "dry_run": false,                 // true면 리포트만, 실제 동기화 안 함
  "reason": "Monthly maintenance"   // Audit용 (optional)
}
```

**Response:**
```json
{
  "status": "completed",
  "synced_at": "2024-01-20T10:30:00Z",
  "actor": "admin",
  "results": {
    "payment": {
      "dlq_pending": {"before": 0, "after": 5, "drift": 5},
      "circuit_breaker_state": {"before": "unknown", "after": "closed"}
    },
    "point": {
      "dlq_pending": {"before": 2, "after": 2, "drift": 0}
    }
  },
  "summary": {
    "total_drifts_detected": 2,
    "total_drifts_corrected": 2
  }
}
```

#### GET /api/self-healing/metrics/drift-report/

**Response:**
```json
{
  "generated_at": "2024-01-20T10:30:00Z",
  "metrics": {
    "dlq_pending_count": {
      "payment": {"in_memory": 0, "actual": 5, "drift": 5, "is_critical": false},
      "point": {"in_memory": 2, "actual": 2, "drift": 0, "is_critical": false}
    },
    "circuit_breaker_state": {
      "toss_payment": {"in_memory": "closed", "actual": "closed", "drift": 0}
    }
  },
  "overall_health": "warning",
  "recommendation": "Consider running POST /api/self-healing/metrics/sync/"
}
```

### 3.5 Audit 로깅

```python
AuditService.log_action(
    action="manual_metric_sync",
    actor=request.user.username,
    details={
        "domains": domains,
        "dry_run": dry_run,
        "reason": reason,
        "drifts_corrected": result["summary"]["total_drifts_corrected"],
    },
    category="metric_reconciliation",
)
```

### 3.6 체크리스트

- [ ] `DriftDetector`에서 PeriodicTask 제거
- [x] `MetricSyncView` 생성 (POST /api/self-healing/metrics/sync/)
- [x] `DriftReportView` 생성 (GET /api/self-healing/metrics/drift-report/)
- [x] URL 등록 (`urls.py`)
- [x] Serializer 생성 (`MetricSyncSerializer`, `DriftReportSerializer`)
- [x] Audit 로깅 연동
- [x] 단위 테스트 작성
- [ ] 문서 업데이트 (15_METRIC_COLLECTION_ADVANCED.md)

### 3.7 구현 파일

Phase 1 구현은 다음 파일들에서 확인할 수 있습니다:

| 파일 | 설명 |
|------|------|
| `selfhealing/api/django/views/metric_sync.py` | View 및 Service 구현 |
| `selfhealing/api/django/serializers/metric_sync.py` | 요청/응답 Serializer |
| `selfhealing/api/django/urls.py` | URL 라우팅 |
| `tests/api/test_metric_sync_api.py` | 단위 테스트 |

---

## 4. Phase 2: Startup Hydration

### 4.1 목표

- 서버 시작 시 **1회만** DB 조회하여 Gauge 초기화
- Jitter 적용으로 Thundering Herd 방지
- 초기화 실패해도 서버 기동은 계속 (Graceful Degradation)

### 4.2 시점 선택: `ready()` vs `post_migrate`

| 방식 | 장점 | 단점 | 권장 |
|------|------|------|------|
| `AppConfig.ready()` | 항상 실행됨 | DB 연결 전일 수 있음 | ✅ 조건부 사용 |
| `post_migrate` | DB 준비 보장 | 마이그레이션 없으면 실행 안 됨 | ❌ 불안정 |
| Management Command | 명시적 제어 | 수동 실행 필요 | 백업용 |

### 4.3 권장 구현: `ready()` + 안전장치

```python
# selfhealing/apps.py

import logging
import random
import threading
from django.apps import AppConfig
from django.db import connection

logger = logging.getLogger(__name__)


class SelfhealingConfig(AppConfig):
    name = "selfhealing"
    _hydration_done = False
    _hydration_lock = threading.Lock()

    def ready(self):
        # Django 완전 초기화 후 실행
        if not self._should_hydrate():
            return

        # Jitter 적용 후 비동기 초기화
        from django.conf import settings
        jitter_max = getattr(settings, "SELFHEALING_SYNC_JITTER_MAX", 60)
        jitter = random.uniform(0, jitter_max)

        # 백그라운드에서 초기화 (서버 시작 블로킹 방지)
        threading.Timer(jitter, self._hydrate_gauges).start()
        logger.info(f"Gauge hydration scheduled in {jitter:.1f}s")

    def _should_hydrate(self) -> bool:
        """중복 실행 방지 + 설정 체크"""
        from django.conf import settings

        if not getattr(settings, "SELFHEALING_SYNC_ON_STARTUP", True):
            return False

        with self._hydration_lock:
            if self._hydration_done:
                return False
            self._hydration_done = True
            return True

    def _hydrate_gauges(self):
        """Gauge 초기화 (1회)"""
        try:
            # DB 연결 확인
            connection.ensure_connection()

            from selfhealing.metrics.reconciler import MetricReconciler
            from selfhealing.adapters.metrics.factory import get_adapter

            reconciler = MetricReconciler(get_adapter())
            result = reconciler.sync_all_gauges(actor="startup_hydration")

            logger.info(f"Gauge hydration completed: {result}")

        except Exception as e:
            # 실패해도 서버 기동은 계속
            logger.warning(f"Gauge hydration failed (non-fatal): {e}")
```

### 4.4 Jitter 설정

```python
# settings.py

SELFHEALING_SYNC_ON_STARTUP = True      # 시작 시 동기화 여부
SELFHEALING_SYNC_JITTER_MAX = 60        # 최대 지연 (초)
```

### 4.5 체크리스트

- [x] `SelfhealingConfig.ready()` 구현
- [x] 중복 실행 방지 로직 (`_hydration_done` 플래그)
- [x] Jitter 적용 (`threading.Timer`)
- [x] DB 연결 확인 (`connection.ensure_connection()`)
- [x] Graceful Degradation (예외 시 경고 로그만)
- [x] 설정 변수 추가 (`SELFHEALING_SYNC_ON_STARTUP`, `SELFHEALING_SYNC_JITTER_MAX`)
- [x] 단위 테스트 작성
- [ ] 문서 업데이트

### 4.6 구현 파일

Phase 2 구현은 다음 파일들에서 확인할 수 있습니다:

| 파일 | 설명 |
|------|------|
| `selfhealing/adapters/django/apps.py` | SelfHealingConfig.ready() + 비동기 Hydration |
| `tests/unit/test_startup_hydration.py` | 단위 테스트 (14개) |

---

## 5. Phase 3: Push-Only 이벤트 강화

### 5.1 목표

- 모든 Gauge 업데이트는 **이벤트 발생 시점**에 수행
- DB 폴링 완전 제거
- SafeGauge 래퍼로 음수 방지

### 5.2 이벤트 훅 위치

```
┌─────────────────────────────────────────────────────────────┐
│                    Push 이벤트 발생 지점                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  DLQ 적재 시                                                │
│  ───────────                                                │
│  DLQService.enqueue() → dlq_pending_count.inc(domain)       │
│                                                             │
│  DLQ 처리 완료 시                                           │
│  ──────────────                                             │
│  ReplayService.process() → dlq_pending_count.dec(domain)    │
│                                                             │
│  Circuit Breaker 상태 변경 시                               │
│  ─────────────────────────                                  │
│  CircuitBreaker.trip() → circuit_breaker_state.set("open")  │
│  CircuitBreaker.reset() → circuit_breaker_state.set("closed")│
│                                                             │
│  Retry 결과 시                                              │
│  ───────────                                                │
│  RetryHandler.on_success() → retry_success_total.inc()      │
│  RetryHandler.on_failure() → retry_failure_total.inc()      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 SafeGauge 적용

```python
# selfhealing/metrics/safe_gauge.py

from prometheus_client import Gauge


class SafeGauge:
    """
    음수 방지 + 동기화 헬퍼가 포함된 Gauge 래퍼.
    
    서버 재시작 후 Gauge가 0으로 초기화된 상태에서
    dec()를 호출해도 음수가 되지 않습니다.
    """

    def __init__(self, name: str, description: str, labelnames: list[str]):
        self._gauge = Gauge(name, description, labelnames)

    def inc(self, *label_values, amount: float = 1) -> None:
        """값 증가"""
        self._gauge.labels(*label_values).inc(amount)

    def dec(self, *label_values, amount: float = 1) -> None:
        """값 감소 (음수 방지)"""
        gauge = self._gauge.labels(*label_values)
        current = gauge._value.get()
        if current >= amount:
            gauge.dec(amount)
        else:
            # 음수 방지: 0으로 설정
            gauge.set(0)

    def set(self, *label_values, value: float) -> None:
        """값 직접 설정"""
        self._gauge.labels(*label_values).set(value)

    def get(self, *label_values) -> float:
        """현재 값 조회"""
        return self._gauge.labels(*label_values)._value.get()
```

### 5.4 이벤트 훅 구현 예시

```python
# selfhealing/services/dlq_service.py

from selfhealing.metrics.prometheus import dlq_pending_count  # SafeGauge 인스턴스


class DLQService:
    def enqueue(self, domain: str, payload: dict) -> str:
        """DLQ에 항목 추가"""
        # 비즈니스 로직...
        entry_id = self._store_to_dlq(domain, payload)

        # ✅ Push 이벤트: Gauge 증가
        dlq_pending_count.inc(domain)

        return entry_id

    def mark_processed(self, domain: str, entry_id: str) -> None:
        """DLQ 항목 처리 완료"""
        # 비즈니스 로직...
        self._update_status(entry_id, "processed")

        # ✅ Push 이벤트: Gauge 감소 (SafeGauge가 음수 방지)
        dlq_pending_count.dec(domain)
```

### 5.5 체크리스트

- [x] `SafeGauge` 클래스 구현 (`selfhealing/metrics/safe_gauge.py`)
- [x] 기존 Gauge를 SafeGauge로 교체 (event_handlers에서 사용)
- [x] DLQService에 Push 훅 추가 (store_failure/resolve_entry)
- [x] CircuitBreaker에 Push 훅 추가 (force_open/force_close/reset/record_failure/record_success)
- [x] RetryHandler에 Push 훅 추가 (이미 event_handlers에 구현됨)
- [x] 단위 테스트 작성 (`tests/self_healing/unit/test_push_event_integration.py`)
- [x] 통합 테스트: 이벤트 발생 → Gauge 변경 확인

### 5.6 구현 파일

Phase 3 구현은 다음 파일들에서 확인할 수 있습니다:

| 파일 | 설명 |
|------|------|
| `selfhealing/metrics/safe_gauge.py` | SafeGauge 클래스 (음수 방지 래퍼) |
| `selfhealing/metrics/event_handlers.py` | DLQ/CB/Replay 이벤트 핸들러 |
| `selfhealing/services/dlq_service.py` | DLQService Push 훅 통합 |
| `selfhealing/services/circuit_breaker/manual_control.py` | CB 수동 제어 Push 훅 |
| `selfhealing/services/circuit_breaker/service.py` | CB 자동 상태 변경 Push 훅 |
| `tests/self_healing/unit/test_push_event_integration.py` | Push 이벤트 테스트 |

---

## 6. Phase 4: Redis Air-Gap (어댑터 패턴)

### 6.1 목표

- 비즈니스 DB와 Self-Healing 엔진 사이에 **캐시 레이어** 도입
- 엔진은 오직 Redis만 조회 (DB 직접 접근 완전 차단)
- 비즈니스 레이어가 Redis에 요약 상태 기록
- **어댑터 패턴으로 구현하여 필요시에만 활성화**

### 6.2 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    Redis Air-Gap 아키텍처                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐                                          │
│  │ Business DB  │  ← Self-Healing 엔진 접근 금지           │
│  └──────────────┘                                          │
│         │                                                   │
│         │ (비즈니스 레이어가 요약 기록)                      │
│         ▼                                                   │
│  ┌──────────────┐                                          │
│  │    Redis     │  ← 요약 상태 저장소 (Air-Gap)             │
│  │  (L2 Cache)  │     • sh:airgap:dlq:payment:pending=5    │
│  └──────────────┘     • sh:airgap:cb:toss:state=closed     │
│         │                                                   │
│         │ (Self-Healing 엔진 읽기 전용)                     │
│         ▼                                                   │
│  ┌──────────────┐                                          │
│  │ Self-Healing │                                          │
│  │    Engine    │                                          │
│  └──────────────┘                                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.3 어댑터 계층 구조

```
┌─────────────────────────────────────────────────────────────┐
│                  Air-Gap Adapter 계층 구조                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  AirGapStorageAdapter (Protocol)                            │
│  ├── NullAirGapAdapter      # 기본: 비활성화 (Passthrough)   │
│  ├── RedisAirGapAdapter     # Redis 기반 Air-Gap            │
│  └── InMemoryAirGapAdapter  # 테스트/개발용 (향후)          │
│                                                             │
│  Factory: get_airgap_adapter()                              │
│  ├── SELFHEALING_AIRGAP_ENABLED=false → NullAirGapAdapter   │
│  └── SELFHEALING_AIRGAP_ENABLED=true  → RedisAirGapAdapter  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.4 인터페이스 정의

```python
# selfhealing/adapters/airgap/base.py

@runtime_checkable
class AirGapStorageAdapter(Protocol):
    """Air-Gap 저장소 어댑터 인터페이스."""
    
    def write_summary(self, key: str, value: Any, ttl: int = None) -> bool:
        """요약 상태 기록 (비즈니스 레이어에서 호출)"""
        ...
    
    def read_summary(self, key: str) -> Any:
        """요약 상태 조회 (Self-Healing 엔진에서 호출)"""
        ...
    
    def delete_summary(self, key: str) -> bool:
        """요약 상태 삭제"""
        ...
    
    def increment(self, key: str, amount: int = 1) -> int:
        """카운터 증가 (atomic)"""
        ...
    
    def decrement(self, key: str, amount: int = 1) -> int:
        """카운터 감소 (음수 방지, atomic)"""
        ...
    
    def is_enabled(self) -> bool:
        """Air-Gap 활성화 여부"""
        ...
```

### 6.5 NullAirGapAdapter (기본값 = 비활성화)

```python
# selfhealing/adapters/airgap/null_adapter.py

class NullAirGapAdapter(BaseAirGapAdapter):
    """비활성화 시 사용 - 기존 동작 그대로 유지"""
    
    def write_summary(self, key: str, value: Any, ttl: int = None) -> bool:
        return True  # 아무것도 안 함, 성공으로 간주
    
    def read_summary(self, key: str) -> Any:
        return None  # 항상 None → 기존 로직 사용해야 함
    
    def is_enabled(self) -> bool:
        return False
```

### 6.6 RedisAirGapAdapter

```python
# selfhealing/adapters/airgap/redis_adapter.py

class RedisAirGapAdapter(BaseAirGapAdapter):
    """Redis 기반 Air-Gap 저장소"""
    
    def __init__(self, redis_client, prefix="sh:airgap:", default_ttl=3600):
        self.redis = redis_client
        self.prefix = prefix
        self.default_ttl = default_ttl
    
    def write_summary(self, key: str, value: Any, ttl: int = None) -> bool:
        redis_key = f"{self.prefix}{key}"
        self.redis.setex(redis_key, ttl or self.default_ttl, serialize(value))
        return True
    
    def read_summary(self, key: str) -> Any:
        redis_key = f"{self.prefix}{key}"
        return deserialize(self.redis.get(redis_key))
    
    def decrement(self, key: str, amount: int = 1) -> int:
        # Lua 스크립트로 원자적 음수 방지
        lua_script = """
        local current = redis.call('GET', KEYS[1])
        if current == false then return 0 end
        local new_value = math.max(0, tonumber(current) - tonumber(ARGV[1]))
        redis.call('SET', KEYS[1], new_value)
        return new_value
        """
        return self.redis.eval(lua_script, 1, key, amount)
    
    def is_enabled(self) -> bool:
        return self.redis.ping()
```

### 6.7 사용 예시

```python
from selfhealing.adapters.airgap import get_airgap_adapter
from selfhealing.adapters.airgap.base import AirGapKeys

# 어댑터 가져오기 (설정에 따라 Null 또는 Redis)
adapter = get_airgap_adapter()

# 비즈니스 레이어: DLQ 적재 시
if adapter.is_enabled():
    adapter.increment(AirGapKeys.dlq_pending("payment"))

# Self-Healing 엔진: 메트릭 조회 시
if adapter.is_enabled():
    count = adapter.read_summary(AirGapKeys.dlq_pending("payment"))
else:
    # 기존 로직 (예: DB 직접 조회 또는 Push 기반 Gauge)
    count = dlq_pending_gauge.get("payment")
```

### 6.8 설정

```python
# settings.py 또는 환경 변수

# Air-Gap 활성화 여부 (기본: false = 비활성화)
SELFHEALING_AIRGAP_ENABLED = False

# Redis 연결 URL (활성화 시 필요)
SELFHEALING_AIRGAP_REDIS_URL = "redis://localhost:6379/1"

# 키 접두사
SELFHEALING_AIRGAP_PREFIX = "sh:airgap:"

# 기본 TTL (초)
SELFHEALING_AIRGAP_TTL = 3600
```

### 6.9 체크리스트

- [x] Phase 1~3 완료
- [x] `AirGapStorageAdapter` Protocol 정의 (`base.py`)
- [x] `NullAirGapAdapter` 구현 (`null_adapter.py`)
- [x] `RedisAirGapAdapter` 구현 (`redis_adapter.py`)
- [x] Factory 구현 (`factory.py`)
- [x] `AirGapKeys` 헬퍼 클래스 구현
- [x] 단위 테스트 작성
- [ ] 비즈니스 레이어 연동 (선택적)
- [ ] 통합 테스트 작성 (선택적)

### 6.10 구현 파일

Phase 4 구현은 다음 파일들에서 확인할 수 있습니다:

| 파일 | 설명 |
|------|------|
| `selfhealing/adapters/airgap/__init__.py` | 모듈 진입점 |
| `selfhealing/adapters/airgap/base.py` | Protocol 및 Base 클래스, AirGapKeys |
| `selfhealing/adapters/airgap/null_adapter.py` | NullAirGapAdapter (비활성화용) |
| `selfhealing/adapters/airgap/redis_adapter.py` | RedisAirGapAdapter (Redis 기반) |
| `selfhealing/adapters/airgap/factory.py` | Factory 함수 |
| `tests/self_healing/unit/test_airgap_adapter.py` | 단위 테스트 |

### 6.11 장점

| 장점 | 설명 |
|------|------|
| **기존 패턴과 일관성** | `MetricSourceAdapter` 등 기존 어댑터 체계와 동일 |
| **플러그 & 플레이** | 설정 하나로 활성화/비활성화 (`SELFHEALING_AIRGAP_ENABLED`) |
| **Graceful Fallback** | Redis 장애 시 `NullAirGapAdapter`로 자동 전환 |
| **테스트 용이** | Mock 어댑터로 쉽게 테스트 가능 |
| **확장성** | 나중에 Redis 대신 Memcached, DynamoDB 등으로 교체 가능 |

---

## 7. 철학적 정리

### 7.1 허용되는 DB 접근

| 시점 | 접근 유형 | 허용 여부 | 이유 |
|------|----------|----------|------|
| 서버 시작 시 | 1회 Hydration | ✅ 허용 | 초기 상태 확보 필수 |
| 운영자 수동 요청 | Manual Sync | ✅ 허용 | 명시적 의도 |
| 이벤트 발생 시 | Push 훅 | ✅ 허용 | 비즈니스 로직 내부 |
| 주기적 폴링 | PeriodicTask | ❌ 금지 | 침투적 감시 |
| 자동 Drift 감지 | Background Job | ❌ 금지 | 능동적 간섭 |

### 7.2 ADR 업데이트

기존 ADR-001을 보완하여 명확히 합니다:

> **ADR-001 (수정): Push 위주 + 비침습적 설계**
>
> **맥락**: Self-Healing 시스템은 비즈니스 DB에 의존하면 안 됨.
>
> **결정**:
> - Counter/Histogram: Push Only
> - Gauge: Push + **Lazy Sync (시작 시 1회) + Manual Sync (수동 요청)**
> - **주기적 DB 폴링 금지**
>
> **결과**:
> - 정상 운영 중 DB 부하 제로
> - 시작 시점과 수동 요청 시에만 최소한의 조회

---

## 8. 테스트 전략

### 8.1 Phase 1 테스트

```python
class TestMetricSyncAPI:
    def test_sync_requires_admin(self): ...
    def test_sync_all_domains(self): ...
    def test_sync_specific_domains(self): ...
    def test_sync_dry_run(self): ...
    def test_sync_audit_logging(self): ...

class TestDriftReportAPI:
    def test_report_returns_current_drift(self): ...
    def test_report_does_not_modify_gauges(self): ...
```

### 8.2 Phase 2 테스트

```python
class TestStartupHydration:
    def test_hydration_runs_once_on_startup(self): ...
    def test_hydration_applies_jitter(self): ...
    def test_hydration_failure_does_not_block_startup(self): ...
    def test_hydration_skipped_when_disabled(self): ...
```

### 8.3 Phase 3 테스트

```python
class TestSafeGauge:
    def test_dec_does_not_go_negative(self): ...
    def test_dec_from_zero_stays_zero(self): ...
    def test_inc_and_dec_balance(self): ...

class TestPushEvents:
    def test_dlq_enqueue_increments_gauge(self): ...
    def test_dlq_process_decrements_gauge(self): ...
    def test_circuit_breaker_trip_updates_gauge(self): ...
```

### 8.4 Phase 4 테스트

```python
class TestAirGapAdapter:
    def test_null_adapter_is_passthrough(self): ...
    def test_redis_adapter_read_write(self): ...
    def test_factory_returns_correct_adapter(self): ...
    def test_fallback_to_null_on_redis_failure(self): ...
```

### 8.5 Phase 5 테스트

```python
class TestSyncInfo:
    def test_initial_state_is_unknown(self): ...
    def test_mark_synced_updates_status(self): ...
    def test_staleness_detection(self): ...
    def test_recovery_progress(self): ...

class TestMetricSnapshotStorage:
    def test_atomic_write(self): ...
    def test_save_and_load_value(self): ...
    def test_age_tracking(self): ...
    def test_max_age_enforcement(self): ...

class TestMetricReliabilityManager:
    def test_initial_state_is_strict(self): ...
    def test_sync_success_improves_reliability(self): ...
    def test_gradual_stabilization(self): ...
    def test_force_strict_mode(self): ...
```

---

## 9. Phase 5: 메트릭 시스템 장애 대비 ("최후의 비상망")

### 9.1 목표

- 메트릭 시스템 장애 시에도 Self-Healing이 안전하게 동작
- 데이터 신뢰도 투명하게 표시
- 점진적 복구로 급격한 상태 변화 방지

### 9.2 3계층 비상망

```
┌─────────────────────────────────────────────────────────────┐
│                    메트릭 신뢰성 계층                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Layer 1] 신뢰도 플래그 (Sync Status)                      │
│  ─────────────────────────────────                          │
│  • SafeGaugeChild.is_synced → 데이터 신선도                 │
│  • staleness_threshold → 자동 stale 감지 (기본 5분)         │
│  • Prometheus 라벨 is_synced="true/false"                   │
│                                                             │
│  [Layer 2] L1 로컬 스냅샷 (Last Known Good)                 │
│  ─────────────────────────────────                          │
│  • Write-to-Temp-and-Rename 원자적 쓰기                     │
│  • 스냅샷 나이(age) 추적                                    │
│  • max_age 초과 시 기본값으로 fallback                      │
│                                                             │
│  [Layer 3] Conservative Fallback (Strict Mode)              │
│  ─────────────────────────────────                          │
│  • 데이터 불확실 시 보수적 설정 적용                        │
│  • 점진적 안정화 기간 (Stabilization Period)                │
│  • STRICT → CAUTIOUS → NORMAL 단계적 복구                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 9.3 신뢰도 플래그 (Layer 1)

SafeGaugeChild에 sync status 추가:

```python
class SafeGaugeChild:
    def __init__(self, ...):
        self._sync_info = SyncInfo(
            staleness_threshold=300.0,  # 5분
            stabilization_duration=60.0,  # 안정화 1분
        )
    
    @property
    def is_synced(self) -> bool:
        """데이터 신뢰 가능 여부."""
        self._sync_info.check_staleness()
        return self._sync_info.is_synced
    
    def inc(self, amount=1):
        ...
        self._sync_info.mark_synced("push")
```

### 9.4 L1 스냅샷 (Layer 2)

```python
from selfhealing.metrics.snapshot_storage import MetricSnapshotStorage

storage = MetricSnapshotStorage("/var/lib/selfhealing/metrics")

# 저장 (Write-to-Temp-and-Rename)
storage.save_value("dlq_pending", "payment", 5, immediate=True)

# 로드 (나이 체크 포함)
value = storage.load_value("dlq_pending", "payment", max_age=3600)
```

### 9.5 Conservative Fallback (Layer 3)

```python
from selfhealing.metrics.reliability_manager import MetricReliabilityManager

manager = MetricReliabilityManager()

# 동기화 성공 보고
manager.report_sync_success("payment", "push", value=5)

# 상태 확인
state = manager.get_reliability_state("payment")
if state.operating_mode == OperatingMode.STRICT:
    # 보수적 설정 적용
    apply_conservative_limits()
elif state.is_recovering:
    # 점진적 완화
    progress = state.stabilization_progress  # 0.0 ~ 1.0
    apply_gradual_limits(progress)
```

### 9.6 운영 모드 전환

```
┌─────────────┐     동기화 성공     ┌─────────────┐
│   STRICT    │ ─────────────────> │  CAUTIOUS   │
│ (보수적)    │                    │ (안정화 중) │
└─────────────┘                    └─────────────┘
      ▲                                  │
      │ 데이터 stale/실패                │ 안정화 완료
      │                                  │ (consecutive syncs)
      │                                  ▼
      │                            ┌─────────────┐
      └─────────────────────────── │   NORMAL    │
                                   │ (정상 운영) │
                                   └─────────────┘
```

### 9.7 설정

```python
# settings.py

# 신뢰도 임계값
SELFHEALING_STALENESS_THRESHOLD = 300  # 5분
SELFHEALING_STABILIZATION_DURATION = 60  # 1분
SELFHEALING_CONSECUTIVE_SYNCS_FOR_NORMAL = 3

# 스냅샷 저장소
SELFHEALING_SNAPSHOT_DIR = "/var/lib/selfhealing/metrics"
SELFHEALING_SNAPSHOT_MAX_AGE = 3600  # 1시간
```

### 9.8 체크리스트

- [x] `SyncInfo` 클래스 구현 (신뢰도 상태 추적)
- [x] `SafeGaugeChild`에 sync status 통합
- [x] `MetricSnapshotStorage` 구현 (원자적 쓰기)
- [x] `MetricReliabilityManager` 구현 (모드 관리)
- [x] 점진적 안정화 로직 구현
- [x] 단위 테스트 작성
- [ ] Prometheus 메트릭 export 연동 (선택)
- [ ] Grafana 대시보드 경고 표시 (선택)

### 9.9 구현 파일

Phase 5 구현은 다음 파일들에서 확인할 수 있습니다:

| 파일 | 설명 |
|------|------|
| `selfhealing/metrics/safe_gauge.py` | SyncInfo, SafeGaugeChild 신뢰도 확장 |
| `selfhealing/metrics/snapshot_storage.py` | L1 스냅샷 저장소 |
| `selfhealing/metrics/reliability_manager.py` | 신뢰도 관리자 + 모드 전환 |
| `tests/self_healing/unit/test_metric_reliability.py` | 단위 테스트 |

---

## 10. Fallback 계층 종합

```
┌─────────────────────────────────────────────────────────────┐
│                    메트릭 데이터 Fallback 계층               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1순위: 실시간 Push 이벤트 (Phase 3)                        │
│      │   └─ is_synced=true, reliability=HIGH               │
│      ▼                                                      │
│  2순위: DB 조회 (Manual Sync, Phase 1)                      │
│      │   └─ is_synced=true, reliability=HIGH               │
│      ▼                                                      │
│  3순위: Redis Air-Gap (Phase 4)                             │
│      │   └─ is_synced=true, reliability=MEDIUM             │
│      ▼                                                      │
│  4순위: L1 로컬 스냅샷 (Phase 5)                            │
│      │   └─ is_synced=false, reliability=LOW               │
│      ▼                                                      │
│  5순위: Safe Defaults (Stage 16)                            │
│          └─ is_synced=false, reliability=UNKNOWN            │
│          └─ operating_mode=STRICT                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 11. 관련 문서

- [14_METRIC_COLLECTION_CORE.md](14_METRIC_COLLECTION_CORE.md) - 수집 전략 기본
- [15_METRIC_COLLECTION_ADVANCED.md](15_METRIC_COLLECTION_ADVANCED.md) - Drift 감지 상세 (업데이트 예정)
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 정의
- [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 설정 관리

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0 | 2024-12-24 | 초안 작성 - 비침습적 Drift 전략 |
| 2.0 | 2024-12-24 | Phase 4: Air-Gap 어댑터 패턴 구현 |
| 3.0 | 2024-12-24 | Phase 5: 메트릭 신뢰도 시스템 (비상망) 구현 |
