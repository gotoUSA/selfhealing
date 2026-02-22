# 268. CircuitBreaker Metadata 확장 — Cell 컨텍스트 직접 주입

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Related**: [264_CELL_HEALTH.md](264_CELL_HEALTH.md) (R4 Phase 2)

---

## 0. 요약

`CircuitBreakerStateData`에 `metadata: dict[str, Any]` 필드를 추가하여 CB의 범용 확장 포인트를 확보한다. Cell 격리의 핵심 메커니즘은 **Composite Key** (`service_name::cell_id`) 네임스페이스화로 물리적 CB 인스턴스를 분리하고, metadata는 `region_id`, `deployment_version`, `last_trip_reason` 등 부가 컨텍스트 역할로 한정한다.

### 0.1 왜 필요한가?

264 v2.0.0의 Phase 1(CB 상태 변경 콜백 기반)에는 두 가지 구조적 한계가 있다:

1. **수동 제어 미감지**: `ManualControlMixin.force_open()` / `force_close()` (L69–L267, `manual_control.py`)는 `_invoke_state_change_callbacks()`를 호출하지 않으므로, 콜백 기반으로는 수동 CB 제어를 감지할 수 없다.

2. **TTL 레이스 컨디션**: `CellRegistry.assigned_services`는 TTL(5분) 기반으로 자동 만료된다 (`_evict_expired_services()`, L247–L286, `registry.py`). Heartbeat(30초) 주기와 TTL(300초) 사이의 윈도우에서 서비스가 evict되면, CB는 OPEN인데 Cell 매핑이 사라져 `cb_open_ratio`가 0.0으로 떨어진다.

### 0.2 v1.0.0 → v2.0.0 변경 이유

v1.0.0에서는 `metadata["cell_id"]`를 태깅하여 CB-Cell 매핑을 해결하려 했으나, 아키텍처 리뷰에서 4가지 구조적 결함이 식별되었다:

1. **핑퐁 오버라이트**: 단일 CB에 여러 Cell이 `metadata["cell_id"]`를 경합 → Composite Key로 해결 (§2.4)
2. **미들웨어 Hot Path 병목**: 매 요청마다 Redis I/O → Composite Key 도입으로 자연 소멸 + L1 캐시 전략 (§2.5)
3. **Read-Modify-Write 레이스**: ABC 기본 구현의 Lost Update → 각 Repository에 원자적 핀포인트 업데이트 강제 (§2.6)
4. **토폴로지 변경 시 스텔스 상태**: 고아 CB → Ring Resize Reconciliation Job (§2.7)

---

## 1. 설계 근거 — 기존 코드 분석

### 1.1 현재 `CircuitBreakerStateData` — metadata 필드 부재

**파일**: `packages/selfhealing-python/src/selfhealing/interfaces/repositories.py` (L176–L229)

```python
@dataclass
class CircuitBreakerStateData:
    """
    Data transfer object for CircuitBreakerState model.

    Represents the current state of a circuit breaker for a service.
    """

    # Identity
    service_name: str
    id: int | None = None

    # State
    state: str = CircuitBreakerStateEnum.CLOSED.value
    failure_count: int = 0
    success_count: int = 0

    # Timing
    last_failure_at: datetime | None = None
    opened_at: datetime | None = None

    # Manual Control
    manually_controlled: bool = False
    controlled_by_id: int | None = None
    control_reason: str = ""
    manual_override_expires_at: datetime | None = None

    # Half-Open Tracking
    half_open_request_count: int = 0

    # Lifecycle
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

`metadata` 필드가 **존재하지 않는다**.

### 1.2 프로젝트 내 선례: `metadata: dict` 표준 패턴

**`FailedOperationData`** — 동일 파일 (L103–L173):

```python
@dataclass
class FailedOperationData:
    # ...
    # Forensic Context
    request_data: dict[str, Any] = field(default_factory=dict)
    response_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)     # ← 표준 패턴
    # ...
```

프로젝트 전반에서 **20개 이상의 모델**이 동일 패턴을 사용:

| 모델 | 위치 |
|------|------|
| `FailedOperationData` | `interfaces/repositories.py` L154 |
| `RecoverySessionArchive` | `coordination/recovery_session_archive.py` |
| `RegionalRecoveryPolicy` | `coordination/regional_recovery_policy.py` |
| `PendingRecoveryApproval` | `coordination/pending_recovery_approval.py` |
| `RecoveryAuditEntry` | `coordination/recovery_audit.py` |
| `RollbackModel` | `rollback/models.py` |
| `SagaStepModel` | `saga/models.py` |
| `NotificationRecord` | `unified_notification/models.py` |

표준 선언: **`metadata: dict[str, Any] = field(default_factory=dict)`** — 기본값이 빈 dict이므로 기존 코드에 영향 없음.

### 1.3 `CircuitBreakerService.get_all_states()` — 직렬화 누락

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py` (L436–L460)

```python
def get_all_states(self) -> list[dict[str, Any]]:
    states = self.repository.get_all_states()
    return [
        {
            "service_name": s.service_name,
            "state": s.state,
            "failure_count": s.failure_count,
            "success_count": s.success_count,
            "last_failure_at": s.last_failure_at,
            "opened_at": s.opened_at,
            "manually_controlled": s.manually_controlled,
            "controlled_by_id": s.controlled_by_id,
            "control_reason": s.control_reason,
        }
        for s in states
    ]
```

`metadata` 필드가 직렬화에 포함되지 않는다.

### 1.4 `_invoke_state_change_callbacks()` — 동기 콜백 메커니즘

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py` (L150–L172)

```python
def _invoke_state_change_callbacks(
    self,
    service_name: str,
    old_state: str,
    new_state: str,
) -> None:
    callbacks = self._state_change_callbacks.get(new_state, [])
    for callback in callbacks:
        try:
            callback(service_name, old_state, new_state)
        except Exception as e:
            logger.error(f"[CircuitBreaker] Sync callback failed for '{new_state}': {e}")
```

콜백 시그니처: `(service_name, old_state, new_state)` — metadata를 전달하지 않는다.

### 1.5 `ManualControlMixin.force_open()` — 콜백 미호출 확인

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py` (L69–L189)

```python
success, previous_state, new_state = self.repository.atomic_force_open(
    service_name=service_name,
    reason=reason,
    controlled_by_id=controlled_by_id,
    ttl_minutes=self.config.manual_override_ttl_minutes,
)

if success:
    # Audit 기록만 수행
    try:
        from selfhealing.services.audit_helpers import log_cb_state_change_audit
        log_cb_state_change_audit(...)
    except Exception as e:
        logger.debug(f"[CircuitBreaker] Audit log failed: {e}")
    # 메트릭 이벤트만 기록
    try:
        from selfhealing.metrics.event_handlers import CircuitBreakerEventHandler
        CircuitBreakerEventHandler.on_state_changed(...)
    except ImportError:
        pass
    # ❌ _invoke_state_change_callbacks() 호출 없음
```

`force_close()` (L193–L267)도 동일하게 `_invoke_state_change_callbacks()`를 호출하지 않는다.

이것이 264 Phase 1의 콜백 방식으로 **수동 제어 경로를 감지할 수 없는** 근본 이유이다.

### 1.6 Repository 제공 방식 — `ProviderRegistry`

**파일**: `packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py` (L174–L180)

```python
@property
def repository(self) -> CircuitBreakerStateRepository:
    """Get the repository using ProviderRegistry (Redis by default)."""
    if self._repository is None:
        from selfhealing.factory import ProviderRegistry
        self._repository = ProviderRegistry.get_circuit_breaker_repo()
    return self._repository
```

`CircuitBreakerStateRepository` ABC의 구현체는 `ProviderRegistry` 팩토리를 통해 제공되며, **InMemory**, **Redis**, **Django ORM** 세 가지 어댑터가 존재한다.

---

## 2. 변경 사양

### 2.1 `CircuitBreakerStateData`에 metadata 필드 추가

**파일**: `interfaces/repositories.py`

```python
@dataclass
class CircuitBreakerStateData:
    # ... 기존 필드 ...

    # Half-Open Tracking
    half_open_request_count: int = 0

    # Extensible Metadata (268 추가)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Lifecycle
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

**하위 호환성**: `default_factory=dict`이므로 기존에 metadata를 사용하지 않는 모든 코드가 **그대로 동작**한다. `FailedOperationData`와 동일한 검증된 패턴이다.

### 2.2 `CircuitBreakerService.get_all_states()` 직렬화 확장

**파일**: `services/circuit_breaker/service.py`

```python
def get_all_states(self) -> list[dict[str, Any]]:
    states = self.repository.get_all_states()
    return [
        {
            "service_name": s.service_name,
            "state": s.state,
            "failure_count": s.failure_count,
            "success_count": s.success_count,
            "last_failure_at": s.last_failure_at,
            "opened_at": s.opened_at,
            "manually_controlled": s.manually_controlled,
            "controlled_by_id": s.controlled_by_id,
            "control_reason": s.control_reason,
            "metadata": s.metadata,                          # ← 추가
        }
        for s in states
    ]
```

### 2.3 Repository 구현체별 변경

#### 2.3.1 InMemory Repository

`CircuitBreakerStateData` dataclass의 `default_factory=dict`에 의해 **변경 불필요**. dataclass 필드가 자동 반영된다.

#### 2.3.2 Redis Repository

Redis Hash에 `metadata` 키를 JSON 직렬화하여 저장한다:

```python
# 저장 시
import json
if state_data.metadata:
    redis.hset(key, "metadata", json.dumps(state_data.metadata))

# 조회 시
raw_metadata = redis.hget(key, "metadata")
metadata = json.loads(raw_metadata) if raw_metadata else {}
```

기존 키에 `metadata` 필드가 없으면 빈 dict로 처리 — **하위 호환**.

`_to_data()` (L422–L450, `adapters/redis/circuit_breaker.py`)에 `metadata` 역직렬화 추가:

```python
def _to_data(self, service_name: str, data: dict[str, Any]) -> CircuitBreakerStateData:
    # ... 기존 필드 ...
    import json
    raw_metadata = data.get("metadata", "{}")
    try:
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    return CircuitBreakerStateData(
        # ... 기존 인자 ...
        metadata=metadata if isinstance(metadata, dict) else {},
    )
```

#### 2.3.3 Django ORM Repository

Django 모델에 `JSONField` 추가:

```python
# shopping/models.py 또는 해당 모델 파일
metadata = models.JSONField(
    default=dict,
    blank=True,
    help_text="Extensible metadata (e.g., cell_id, region_id)",
)
```

DB 마이그레이션 필요:

```bash
python manage.py makemigrations
python manage.py migrate
```

`default=dict`이므로 기존 레코드는 빈 dict(`{}`)로 자동 채워진다 — **non-destructive 마이그레이션**.

### 2.4 Cell 격리 — Composite Key 네임스페이스화

#### 2.4.1 문제: 핑퐁 오버라이트 (v1.0.0 결함)

v1.0.0에서는 단일 CB(`service_name=payment_api`)에 `metadata["cell_id"]`를 태깅하여
Cell-CB 매핑을 해결하려 했다. 그러나 여러 Cell이 동시에 동일 서비스의 CB에 접근하면,
`metadata["cell_id"]`를 서로 덮어쓰는 **핑퐁 오버라이트**가 발생한다.

**코드 근거 — CB 식별자는 `service_name` 단일 문자열**:

| 어댑터 | 키 구조 | 근거 |
|--------|---------|------|
| Redis | `cb:{service_name}` (Hash) | `_make_key()` L86–L88, `adapters/redis/circuit_breaker.py` |
| InMemory | `self._storage[service_name]` (dict) | L72–L83, `adapters/memory/circuit_breaker.py` |

`service_name` 하나에 CB 인스턴스 하나이므로, cell-3과 cell-4가 동시에 접근하면
metadata가 무한 경합된다.

#### 2.4.2 해결: Composite Key

CB의 `service_name`을 `{base_name}::{cell_id}` 형태로 네임스페이스화하여
Cell별로 **물리적으로 분리된 CB 인스턴스**를 생성한다.

```python
# services/cell_topology/cb_namespace.py

COMPOSITE_KEY_SEPARATOR = "::"

def make_cell_scoped_cb_name(service_name: str, cell_id: str) -> str:
    """
    Cell-scoped CB Composite Key 생성.

    Args:
        service_name: 기본 서비스 이름 (예: "payment_api")
        cell_id: Cell 식별자 (예: "cell-3")

    Returns:
        Composite Key (예: "payment_api::cell-3")
    """
    return f"{service_name}{COMPOSITE_KEY_SEPARATOR}{cell_id}"


def parse_composite_cb_name(composite_name: str) -> tuple[str, str]:
    """
    Composite Key에서 (service_name, cell_id) 분리.

    레거시 호환: 구분자가 없으면 cell_id="" 반환.

    Args:
        composite_name: CB 식별자

    Returns:
        (base_service_name, cell_id)
    """
    if COMPOSITE_KEY_SEPARATOR in composite_name:
        parts = composite_name.split(COMPOSITE_KEY_SEPARATOR, 1)
        return parts[0], parts[1]
    return composite_name, ""  # 레거시 단일 키 호환
```

**하위 호환성**: 기존 CB는 `service_name=payment_api`로 유지된다.
`parse_composite_cb_name()`이 구분자 없는 키를 `("payment_api", "")`로 처리하므로
레거시 코드에 영향 없음.

**선택 근거**: `service_name`이 순수 문자열이므로 (`CircuitBreakerStateData.service_name: str`,
`interfaces/repositories.py` L184), Redis 키(`"cb:payment_api::cell-3"`)와
InMemory dict 키(`self._storage["payment_api::cell-3"]`) 모두 **어댑터 변경 없이**
자연스럽게 분리된다.

#### 2.4.3 Composite Key 호출 시점

`CellTagger.resolve_cell_id_from_request()` (L107–L159, `tagger.py`)가 순수 인메모리
해시 계산만 수행하므로, 미들웨어에서 Composite Key를 생성하는 비용은
**문자열 연결 1회** (`f"{service_name}::{cell_id}"`)뿐이다.

```python
# CellTaggingMiddleware 내부 (개념적 호출 흐름)
cell_id = tagger.resolve_cell_id_from_request(request)  # 인메모리 Hash Ring 조회
cb_name = make_cell_scoped_cb_name("target_service", cell_id)  # 문자열 연결
result = cb_service.should_allow(cb_name)  # CB 조회
```

#### 2.4.4 Metric Label 분리 (필수 동반 작업)

Composite Key 도입 시, 메트릭 발행 계층에서 식별자를 분리하지 않으면
Grafana에서 서비스 글로벌 집계가 불가능해진다.

**현재 메트릭 정의** (`services/metrics/definitions.py` L98–L109):

```python
circuit_breaker_state = get_or_create_gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["service"],  # ← Label이 service 하나뿐
)
```

**변경**: `cell_id` Label 추가:

```python
circuit_breaker_state = get_or_create_gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["service", "cell_id"],  # ← cell_id 분리
)

circuit_breaker_transitions = get_or_create_counter(
    "circuit_breaker_transitions_total",
    "Total circuit breaker state transitions",
    ["service", "cell_id", "from_state", "to_state", "is_synthetic"],
)
```

**`CircuitBreakerEventHandler` 파싱 로직** (`metrics/event_handlers.py` L309–L358):

```python
class CircuitBreakerEventHandler:
    @staticmethod
    def on_state_changed(service: str, from_state: str, to_state: str) -> None:
        metrics = _get_metrics()
        if metrics is None:
            return

        # Composite Key 분리 — 메트릭 발행 경계에서만 수행 (SRP)
        from selfhealing.services.cell_topology.cb_namespace import (
            parse_composite_cb_name,
        )
        base_service, cell_id = parse_composite_cb_name(service)

        try:
            state_value = CircuitBreakerEventHandler.STATE_VALUES.get(to_state, 0)
            if hasattr(metrics, "circuit_breaker_state"):
                metrics.circuit_breaker_state.labels(
                    service=base_service, cell_id=cell_id,
                ).set(state_value)

            if hasattr(metrics, "circuit_breaker_transitions"):
                metrics.circuit_breaker_transitions.labels(
                    service=base_service, cell_id=cell_id,
                    from_state=from_state, to_state=to_state,
                ).inc()
        except Exception as e:
            logger.warning(f"[EventHandler] Failed to record CB state change: {e}")
```

**PromQL 활용**:
- Cell별: `circuit_breaker_state{service="payment_api", cell_id="cell-3"}`
- 글로벌: `sum by (service)(circuit_breaker_state{service="payment_api"})`

### 2.5 CB 인스턴스 생성 전략 — Lazy Init + L1 캐시

#### 2.5.1 문제: 미들웨어 Hot Path 성능 병목 (v1.0.0 결함)

v1.0.0에서는 `CellTaggingMiddleware`가 매 요청마다 `_inject_cell_metadata()`를 호출하여
Redis I/O(GET + 조건부 SET)를 수행하도록 설계했다.

**코드 근거 — 현재 미들웨어는 Zero Redis I/O**:

`CellTagger.resolve_cell_id_from_request()` (L107–L159, `tagger.py`)는
`CellRegistry._hash()` (SHA-256) + binary search만 수행한다.
이미 프로젝트에서는 `assign_service()` (L224–L244, `registry.py`)를
"매 요청마다 호출하지 않고 30초 Heartbeat 스레드에서 호출"하는 패턴이 확립되어 있다.

#### 2.5.2 해결: Composite Key로 자연 소멸 + Lazy Init

Composite Key 도입으로 `metadata` 주입 자체가 불필요해지므로,
미들웨어의 Redis I/O는 **Zero**로 유지된다.

CB 인스턴스 생성은 **Lazy Init** 전략을 적용한다:

```
요청 흐름:
1. 미들웨어: cell_id = resolve(request)          ← 인메모리 (Zero I/O)
2. 미들웨어: cb_name = f"{svc}::{cell_id}"       ← 문자열 연결
3. CB Service: should_allow(cb_name)              ← get_or_create() → 첫 호출 시에만 Redis 생성
```

**코드 근거 — `get_or_create()`는 이미 Lazy**:

`RedisCircuitBreakerStateRepository.get_or_create()` (L113–L143, `adapters/redis/circuit_breaker.py`):
```python
def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
    existing = self.get_state(service_name)  # HGETALL
    if existing:
        return existing
    # 없을 때만 생성
    default_data = {"state": "closed", ...}
    self._backend.hset(self._make_key(service_name), default_data)
    return self._to_data(service_name, default_data)
```

Cell×Service 조합이 늘어나도, 트래픽이 없는 조합은 CB가 생성되지 않는다.

#### 2.5.3 should_allow() L1 캐시 전략

`should_allow()` (L217–L300, `service.py`)는 매 요청마다 `get_or_create_state()`를 호출하여
Redis `HGETALL`을 유발한다. Composite Key로 Cell×Service 조합이 증가하면
Redis ops도 비례 증가한다.

**L1 캐시 패턴**: 프로세스 로컬 캐시(TTL 1~5초)를 두어 Redis RTT를 감쇠한다.
이는 `CellRegistry`의 L1/L2 티어링 (`_load_all_states_from_redis()`, L359–L413, `registry.py`)과
동일한 검증된 패턴이다.

```python
# should_allow()의 L1 캐시 적용 (개념적)
_cb_state_cache: dict[str, tuple[str, float]] = {}  # {cb_name: (state, cached_at)}
_CB_CACHE_TTL = 2.0  # 초

def should_allow(self, service_name: str) -> bool:
    if not self.is_enabled:
        return True

    # L1 캐시 히트
    cached = _cb_state_cache.get(service_name)
    if cached and (time.monotonic() - cached[1]) < _CB_CACHE_TTL:
        return cached[0] != CircuitState.OPEN

    # L2 (Redis/Memory) 조회
    state = self.get_or_create_state(service_name)
    _cb_state_cache[service_name] = (state.state, time.monotonic())
    # ... 기존 로직 ...
```

**트레이드오프**: 최대 2초간 stale 상태를 읽을 수 있다.
CB OPEN→CLOSED 전환이 2초 지연되는 것은 운영상 수용 가능하다.
CB CLOSED→OPEN 전환은 `record_failure()` 내부에서 즉시 반영되므로 지연 없다.

### 2.6 Repository `update_metadata()` — 원자적 핀포인트 업데이트

#### 2.6.1 문제: Read-Modify-Write 레이스 (v1.0.0 결함)

v1.0.0의 ABC 기본 구현:

```python
def update_metadata(self, service_name, metadata):
    state = self.get_by_service_name(service_name)  # ① READ
    state.metadata = metadata                        # ② MODIFY
    return self.update_state(service_name, state.state)  # ③ WRITE
```

①과 ③ 사이에 다른 워커가 `record_failure()`를 호출하면 **Lost Update** 발생.
또한 `update_state()`의 시그니처에 `metadata` 파라미터가 없으므로
(`update_state(service_name, state, failure_count=None, ...)`, L144, `adapters/redis/circuit_breaker.py`),
metadata가 **실제로 Redis에 기록되지도 않는다**.

#### 2.6.2 해결: 각 Repository에 원자적 업데이트 강제

**`CircuitBreakerStateRepository` ABC** (`interfaces/repositories.py`):

```python
class CircuitBreakerStateRepository(ABC):
    # ... 기존 메서드 ...

    def update_metadata(
        self,
        service_name: str,
        metadata: dict[str, Any],
    ) -> bool:
        """
        CB 메타데이터 필드만 원자적으로 업데이트 (상태 변경 없음).

        ⚠️ 기본 구현은 안전하지 않음 (Read-Modify-Write 레이스 컨디션).
        각 구현체에서 반드시 오버라이드하여 원자적 핀포인트 업데이트를 수행해야 한다:
        - Redis: HSET 단일 필드 (다른 Hash 필드에 영향 없음)
        - InMemory: RLock 내에서 metadata 필드만 교체
        - Django ORM: update(metadata=...) 쿼리

        Args:
            service_name: 서비스 이름
            metadata: 업데이트할 메타데이터

        Returns:
            성공 여부
        """
        state = self.get_by_service_name(service_name)
        if state is None:
            return False
        state.metadata = metadata
        return self.update_state(service_name, state.state)
```

**Redis 구현체** (`adapters/redis/circuit_breaker.py`):

```python
def update_metadata(self, service_name: str, metadata: dict[str, Any]) -> bool:
    """metadata 필드만 HSET — 다른 Hash 필드(state, failure_count 등) 무영향."""
    import json
    return self._backend.hset(
        self._make_key(service_name),
        {"metadata": json.dumps(metadata)},
    )
```

Redis `HSET`은 지정 필드만 업데이트하므로 `failure_count`, `state` 등에 영향 없음 —
**원자적 핀포인트 업데이트**. `_backend.hset()` (L450–L466, `adapters/resilient/backend.py`)은
내부에서 `self._redis._redis.hset(full_key, mapping=str_mapping)`으로
Redis 단일 커맨드를 실행한다.

**InMemory 구현체** (`adapters/memory/circuit_breaker.py`):

```python
def update_metadata(self, service_name: str, metadata: dict[str, Any]) -> bool:
    """RLock 내에서 metadata 필드만 교체. 다른 필드 무영향."""
    with self._lock:
        state = self._storage.get(service_name)
        if state is None:
            return False
        state.metadata = metadata
        return True
```

#### 2.6.3 향후 CAS 확장 — Lua 스크립트는 지금 구현하지 않는 이유

향후 조건부 업데이트(예: `state==OPEN일 때만 metadata 갱신`)가 필요해지면
Redis Lua 스크립트 기반 Compare-and-Swap이 필요할 수 있다.

**지금 구현하지 않는 코드 기반 이유 3가지**:

**이유 1 — `ResilientStorageBackend`에 `eval()` 메서드가 존재하지 않는다**

`ResilientStorageBackend` (L1–L889, `adapters/resilient/backend.py`)의 전체 공개 메서드:

| 메서드 | 용도 |
|--------|------|
| `get` / `set` / `delete` | Key-Value |
| `hget` / `hset` / `hgetall` / `hdel` | Hash |
| `lpush` / `lrange` / `ltrim` | List |
| `zadd` / `zrange` / `zrem` / `zcard` | Sorted Set |
| `incr` | Counter |

`eval()`, `execute_command()`, `register_script()` — **모두 부재**.
Lua 스크립트를 실행하려면 Backend에 새 메서드를 추가해야 하며,
이는 268의 범위(CB metadata 확장)를 크게 초과한다.

**이유 2 — Degraded Mode 폴백 전략이 없다**

`ResilientStorageBackend`의 모든 메서드는 `Redis 실행 → 실패 시 Memory + WAL 폴백` 패턴을 따른다.
WAL은 `_replay_wal_entry()` (L280–L299)에서 `operation` 필드로 디스패치되며,
현재 지원하는 operation은:

```python
if operation == "set":     ...
elif operation == "hset":  ...
elif operation == "delete": ...
elif operation == "hdel":  ...
else:
    logger.warning(f"Unknown WAL operation: {operation}")
```

Lua 스크립트의 WAL 표현과 재생 전략이 정의되지 않았다.
Lua 스크립트는 서버 사이드에서 원자적으로 실행되므로,
Memory+WAL 모드에서 동일한 원자성을 보장하려면 **Python 레벨 CAS 로직**을
별도 구현해야 한다 — 상당한 설계·검증 비용이 발생한다.

**이유 3 — 현재 유일한 사용처(`update_metadata`)에 CAS가 불필요하다**

`update_metadata()`는 `metadata` 단일 Hash 필드에 대한 `HSET`이며,
이미 Redis 단일 커맨드로 원자적이다. `state`와 `metadata`를 **동시에 조건부 변경**하는
요구사항은 현재 존재하지 않는다.

**향후 확장 접근법**: CAS가 필요해지면 `ResilientStorageBackend`에 `execute_script()` 메서드를 추가하고,
Degraded Mode에서는 `threading.RLock` + Python CAS로 폴백하는 구조를 설계한다.
이는 별도 설계 문서로 분리한다 (§5 참조).

### 2.7 Ring Resize Reconciliation — 고아 CB 정리

#### 2.7.1 문제: 토폴로지 변경 시 스텔스 상태 (v1.0.0 결함)

`CellRegistry.add_cells()` (L467–L499, `registry.py`)로 Hash Ring이 리빌딩되면
`get_cell_for_key("target_service")`의 결과가 변한다.
그러나 **기존 CB의 Composite Key는 갱신되지 않는다**.

```
Ring resize 전: CB key = "target_service::cell-3"
Ring resize 후: get_cell_for_key("target_service") = "cell-9"
→ "target_service::cell-3" CB가 Redis에 고아로 남음
→ cell-3의 건강도 집계에 이 CB가 계속 잡힘 → 스텔스 오염
```

#### 2.7.2 해결: Reconciliation Job

**핵심 원칙**: Reconciliation은 **"상태 마이그레이션"이 아니라 "고아 정리 + 신규 생성"**이다.
고아 CB의 장애 상태(OPEN)를 새 Cell로 복사하면, 물리적으로 다른 격벽에
**포이즌 필(Poison Pill)**이 전염된다.

선행 패턴: `CellRegistry._load_all_states_from_redis()` (L359–L413, `registry.py`)의
Anti-entropy Reconciliation 패턴을 재사용한다.

```python
def reconcile_cb_cell_mapping(self) -> dict[str, Any]:
    """
    Ring Resize 후 CB-Cell 매핑 정합성 보정.

    1. 모든 CB를 순회하여 Composite Key에서 cell_id 추출
    2. 현재 Hash Ring 기준으로 올바른 cell_id 비교
    3. 불일치 시: 고아 CB 아카이브 + 삭제 (상태 전이 없음)
    4. 신규 Cell의 CB는 get_or_create()에 의해 Lazy 생성

    Returns:
        {"archived": [...], "errors": [...]}
    """
    from selfhealing.services.cell_topology import get_cell_registry
    from selfhealing.services.cell_topology.cb_namespace import parse_composite_cb_name

    registry = get_cell_registry()
    result = {"archived": [], "errors": []}

    try:
        all_states = self.repository.get_all_states()

        for state in all_states:
            base_name, old_cell_id = parse_composite_cb_name(state.service_name)
            if not old_cell_id:
                continue  # 레거시 단일 키 — 건너뜀

            # 현재 Hash Ring 기준 올바른 Cell
            current_cell_id = registry.get_cell_for_key(base_name)

            if old_cell_id != current_cell_id:
                # 고아 CB — 아카이브 후 삭제 (상태 복사 절대 금지)
                try:
                    self._archive_orphan_cb(state)
                    self.repository.delete_state(state.service_name)
                    result["archived"].append(state.service_name)
                except Exception as e:
                    result["errors"].append(
                        {"service_name": state.service_name, "error": str(e)}
                    )

    except Exception as e:
        logger.error(f"[CB Reconciliation] Failed: {e}")
        result["errors"].append({"error": str(e)})

    return result


def _archive_orphan_cb(self, state: CircuitBreakerStateData) -> None:
    """
    고아 CB를 히스토리에 기록 후 삭제 준비.

    패턴: _record_history() (L385–L399, adapters/redis/circuit_breaker.py)
    """
    self.repository._record_history(
        state.service_name,
        state.state,
        now(),
        note=f"ring_resize_eviction|old_state={state.state}",
    )
```

**고아 CB는 상태 전이 없이 아카이빙 + 삭제**한다:
- OPEN 상태의 고아 CB → 아카이브 후 삭제. 신규 Cell은 Lazy Init으로 CLOSED 생성.
- CLOSED 상태의 고아 CB → 삭제. 신규 Cell은 필요 시 CLOSED로 자동 생성.
- **어떤 경우에도 장애 상태가 새 Cell로 전파되지 않는다** (Poison Pill 방지).

#### 2.7.3 트리거 방식

| 트리거 | 방식 | 비고 |
|--------|------|------|
| `CellRegistry.add_cells()` / `remove_cells()` | EventBus emit → Reconciliation Job 트리거 | 즉시 |
| LeaderScheduler 주기적 | 10~30초 Anti-entropy | 이벤트 누락 보정 |
| Admin API | `POST /api/selfhealing/reconcile-cb-topology/` | 긴급 수동 조치 |

---

## 3. 264 CellHealthAggregator Phase 2 전환

Phase 2에서 264의 `_get_cb_open_ratio()`를 콜백 방식에서 Composite Key 기반 조회로 교체한다:

```python
def _get_cb_open_ratio(self, cell_id: str) -> float:
    """
    Composite Key 기반 Cell CB OPEN 비율 조회 (Phase 2).

    CB의 service_name에서 cell_id를 직접 파싱하므로:
    - assigned_services TTL에 의존하지 않음 (레이스 컨디션 해소)
    - 수동 제어(force_open/force_close)도 감지 가능
    - metadata 경합 없음 (각 Cell이 물리적으로 분리된 CB 보유)

    268_CB_METADATA.md §2.4 참조.
    """
    try:
        from selfhealing.services.circuit_breaker import (
            get_circuit_breaker_service,
        )
        from selfhealing.services.cell_topology.cb_namespace import (
            parse_composite_cb_name,
        )

        cb_service = get_circuit_breaker_service()
        all_states = cb_service.get_all_states()

        open_count = 0
        total = 0
        for state in all_states:
            _, state_cell_id = parse_composite_cb_name(
                state.get("service_name", "")
            )
            if state_cell_id == cell_id:
                total += 1
                if state.get("state") == "open":
                    open_count += 1

        return (open_count / total) if total > 0 else 0.0
    except Exception:
        return 0.0
```

**외부 API 변경 없음**: `compute_health()`, `get_snapshot()`, `aggregate_all()` 등 264의 공개 인터페이스는 그대로 유지된다. 내부 구현만 교체된다.

---

## 4. Phase 1 → Phase 2 전환 비교

| 기준 | Phase 1 (콜백 기반) | Phase 2 (Composite Key 기반) |
|------|---------------------|------------------------------|
| **매핑 방식** | CB 자동 전환 콜백 + `get_cell_for_key()` resolve | `service_name::cell_id` Composite Key 파싱 |
| **수동 제어 감지** | ❌ `force_open`/`force_close` 콜백 미호출 | ✅ Composite Key에 cell_id 영구 기록 |
| **TTL 레이스 컨디션** | ⚠️ `assigned_services` evict 시 resolve 실패 가능 | ✅ CB 자체에 기록, TTL 무관 |
| **핑퐁 오버라이트** | ⚠️ metadata 단일 값 경합 | ✅ Cell별 물리적 CB 분리 |
| **Hot Path I/O** | ⚠️ 미들웨어 Redis I/O 필요 (metadata 주입) | ✅ Zero Redis I/O (문자열 연결만) |
| **Cell 재할당 시** | ⚠️ resolve 결과 불일치 가능 | ✅ Reconciliation Job (§2.7) 보정 |
| **복잡도** | O(1) dict increment (콜백) | O(N) `get_all_states()` 순회 |
| **구현 범위** | 264 내부 완결 | CB DTO + Repository + Service + Metric 변경 |
| **다른 모듈 수혜** | 264 전용 | ✅ 포렌식, 감사, 대시보드 등 전체 |

---

## 5. 확장 가능성 — metadata 활용 사례

`metadata: dict[str, Any]`는 **Cell 격리와 무관한 부가 컨텍스트**로 활용한다.
Cell 격리는 Composite Key (§2.4)가 담당하므로 metadata에 `cell_id`를 저장할 필요가 없다.

| 키 | 용도 | 활용 모듈 |
|----|------|----------|
| `region_id` | 리전별 CB 추적 | `coordination/regional_recovery_policy.py` |
| `tenant_id` | 멀티테넌트 CB 추적 | 향후 테넌트 격벽 |
| `deployment_version` | 배포 버전별 CB 추적 | 카나리 배포 분석 |
| `last_trip_reason` | 마지막 트립 사유 | 포렌식, 포스트모템 |
| `reconciled_at` | 마지막 Reconciliation 시각 | §2.7 Reconciliation Job |

### 5.1 향후 확장 — Lua CAS Extension Point

현재 `ResilientStorageBackend`에 `eval()` 메서드가 없고, Degraded Mode WAL에
Lua 스크립트 재생 전략이 정의되지 않았다 (§2.6.3).

향후 `state`와 `metadata`를 동시에 조건부 변경하는 요구사항이 생기면:
1. `ResilientStorageBackend`에 `execute_script(script, keys, args)` 메서드 추가
2. Degraded Mode 폴백: `threading.RLock` + Python CAS 로직
3. WAL에 `"operation": "lua_script"` 엔트리 타입 추가 + 재생 로직

이는 `ResilientStorageBackend` 아키텍처 변경이므로 별도 설계 문서로 분리한다.

---

## 6. 변경 범위

| 파일 | 변경 | 유형 | Breaking Change |
|------|------|------|----------------|
| `interfaces/repositories.py` | `CircuitBreakerStateData`에 `metadata` 필드 추가 | 수정 | ❌ `default_factory=dict` |
| `interfaces/repositories.py` | `CircuitBreakerStateRepository`에 `update_metadata()` 추가 | 수정 | ❌ 구체 메서드 (기본 구현) |
| `services/circuit_breaker/service.py` | `get_all_states()` 직렬화에 `metadata` 추가 | 수정 | ❌ 새 키 추가 |
| `adapters/redis/circuit_breaker.py` | `update_metadata()` 오버라이드 (HSET 핀포인트) + `_to_data()` metadata 역직렬화 | 수정 | ❌ |
| `adapters/memory/circuit_breaker.py` | `update_metadata()` 오버라이드 (RLock 핀포인트) | 수정 | ❌ |
| `services/cell_topology/cb_namespace.py` | `make_cell_scoped_cb_name()`, `parse_composite_cb_name()` 신규 | 신규 | ❌ |
| `services/metrics/definitions.py` | `circuit_breaker_state` Label에 `cell_id` 추가 | 수정 | ⚠️ Grafana 대시보드 쿼리 수정 필요 |
| `metrics/event_handlers.py` | `on_state_changed()` Composite Key 파싱 | 수정 | ❌ 내부 구현만 |
| `services/cell_topology/health.py` | `_get_cb_open_ratio()` Phase 2 전환 | 수정 | ❌ 내부 구현만 |
| `services/circuit_breaker/service.py` | `reconcile_cb_cell_mapping()` 신규 | 수정 | ❌ 신규 메서드 |

**기존 모든 코드 하위 호환** — `metadata` 필드는 `default_factory=dict`, Composite Key는 `parse_composite_cb_name()`이 구분자 없는 레거시 키를 `("name", "")`로 처리.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (Cell 토폴로지 설정) |
| `264_CELL_HEALTH.md` | R4 Phase 1→Phase 2 전환 대상 |
| `263_CELL_TAGGER.md` | Composite Key 생성 시 cell_id 제공 |
| `coordination/scheduler.py` | `LeaderScheduler` (264 실행 방식, Reconciliation 주기 실행) |
| `interfaces/repositories.py` | `CircuitBreakerStateData`, `CircuitBreakerStateRepository` 수정 대상 |
| `services/circuit_breaker/service.py` | `get_all_states()` 직렬화 수정, `_state_change_callbacks` 참조 |
| `services/circuit_breaker/manual_control.py` | `force_open`/`force_close` 콜백 미호출 근거 |
| `adapters/resilient/backend.py` | Lua CAS 미구현 근거 (§2.6.3) |
| `services/metrics/definitions.py` | Metric Label 분리 (§2.4.4) |
