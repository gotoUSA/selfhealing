# 268. CircuitBreaker Metadata 확장 — Cell 컨텍스트 직접 주입

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Related**: [264_CELL_HEALTH.md](264_CELL_HEALTH.md) (R4 Phase 2)

---

## 0. 요약

`CircuitBreakerStateData`에 `metadata: dict[str, Any]` 필드를 추가하여, 서킷 브레이커(CB)가 생성·갱신될 때 `cell_id`를 직접 주입(Injection)한다. 이를 통해 264 `CellHealthAggregator`가 CB 상태를 Cell에 매핑할 때 `assigned_services` TTL에 의존하지 않고, CB 자체에 기록된 `cell_id`를 직접 조회한다.

### 0.1 왜 필요한가?

264 v2.0.0의 Phase 1(CB 상태 변경 콜백 기반)에는 두 가지 구조적 한계가 있다:

1. **수동 제어 미감지**: `ManualControlMixin.force_open()` / `force_close()` (L69–L267, `manual_control.py`)는 `_invoke_state_change_callbacks()`를 호출하지 않으므로, 콜백 기반으로는 수동 CB 제어를 감지할 수 없다.

2. **TTL 레이스 컨디션**: `CellRegistry.assigned_services`는 TTL(5분) 기반으로 자동 만료된다 (`_evict_expired_services()`, L247–L286, `registry.py`). Heartbeat(30초) 주기와 TTL(300초) 사이의 윈도우에서 서비스가 evict되면, CB는 OPEN인데 Cell 매핑이 사라져 `cb_open_ratio`가 0.0으로 떨어진다.

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

### 2.4 metadata 주입 시점 — CellTagger 미들웨어

`CellTaggingMiddleware` (263)가 요청 처리 시 CB `get_or_create()`를 호출하는 경로에서 `metadata["cell_id"]`를 주입한다:

```python
# CellTaggingMiddleware 또는 CB 훅에서
def _inject_cell_metadata(self, service_name: str, cell_id: str) -> None:
    """CB 상태에 cell_id 메타데이터 주입."""
    try:
        cb_service = get_circuit_breaker_service()
        state = cb_service.repository.get_by_service_name(service_name)
        if state and state.metadata.get("cell_id") != cell_id:
            state.metadata["cell_id"] = cell_id
            # metadata만 업데이트 (상태 변경 없음)
            cb_service.repository.update_metadata(
                service_name, state.metadata
            )
    except Exception:
        pass  # 비필수 — 실패해도 CB 동작에 영향 없음
```

### 2.5 `CircuitBreakerStateRepository` ABC 확장

**파일**: `interfaces/repositories.py`

```python
class CircuitBreakerStateRepository(ABC):
    # ... 기존 메서드 ...

    def update_metadata(
        self,
        service_name: str,
        metadata: dict[str, Any],
    ) -> bool:
        """
        CB 메타데이터만 업데이트 (상태 변경 없음).

        기본 구현: get → update 패턴.
        구현체에서 원자적 업데이트로 오버라이드 가능.

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

**비파괴적 확장**: `update_metadata()`는 구체 메서드(기본 구현 포함)로 추가하므로 기존 구현체가 깨지지 않는다. `@abstractmethod`가 아니므로 기존 Repository 구현체는 변경 없이 기본 구현을 상속받는다.

---

## 3. 264 CellHealthAggregator Phase 2 전환

Phase 2에서 264의 `_get_cb_open_ratio()`를 콜백 방식에서 metadata 조회 방식으로 교체한다:

```python
def _get_cb_open_ratio(self, cell_id: str) -> float:
    """
    CB metadata 기반 Cell CB OPEN 비율 조회 (Phase 2).

    CircuitBreakerStateData.metadata["cell_id"]를 직접 조회하므로:
    - assigned_services TTL에 의존하지 않음 (레이스 컨디션 해소)
    - 수동 제어(force_open/force_close)도 감지 가능

    268_CB_METADATA.md 참조.
    """
    try:
        from selfhealing.services.circuit_breaker import (
            get_circuit_breaker_service,
        )

        cb_service = get_circuit_breaker_service()
        all_states = cb_service.get_all_states()

        open_count = 0
        total = 0
        for state in all_states:
            if state.get("metadata", {}).get("cell_id") == cell_id:
                total += 1
                if state.get("state") == "OPEN":
                    open_count += 1

        return (open_count / total) if total > 0 else 0.0
    except Exception:
        return 0.0
```

**외부 API 변경 없음**: `compute_health()`, `get_snapshot()`, `aggregate_all()` 등 264의 공개 인터페이스는 그대로 유지된다. 내부 구현만 교체된다.

---

## 4. Phase 1 → Phase 2 전환 비교

| 기준 | Phase 1 (콜백 기반) | Phase 2 (metadata 기반) |
|------|---------------------|------------------------|
| **매핑 방식** | CB 자동 전환 콜백 + `get_cell_for_key()` resolve | `metadata["cell_id"]` 직접 조회 |
| **수동 제어 감지** | ❌ `force_open`/`force_close` 콜백 미호출 | ✅ metadata에 cell_id 영구 기록 |
| **TTL 레이스 컨디션** | ⚠️ `assigned_services` evict 시 resolve 실패 가능 | ✅ CB 자체에 기록, TTL 무관 |
| **Cell 재할당 시** | ⚠️ resolve 결과 불일치 가능 | ⚠️ heartbeat로 metadata 갱신 필요 |
| **복잡도** | O(1) dict increment (콜백) | O(N) `get_all_states()` 순회 |
| **구현 범위** | 264 내부 완결 | CB DTO + Repository + Service 변경 |
| **다른 모듈 수혜** | 264 전용 | ✅ 포렌식, 감사, 대시보드 등 전체 |

---

## 5. 확장 가능성 — metadata 활용 사례

`metadata: dict[str, Any]`는 `cell_id` 외에도 범용 확장 포인트로 활용 가능하다:

| 키 | 용도 | 활용 모듈 |
|----|------|----------|
| `cell_id` | Cell 토폴로지 매핑 | 264 CellHealthAggregator |
| `region_id` | 리전별 CB 격리 | `coordination/regional_recovery_policy.py` |
| `tenant_id` | 멀티테넌트 CB 격리 | 향후 테넌트 격벽 |
| `deployment_version` | 배포 버전별 CB 추적 | 카나리 배포 분석 |
| `last_trip_reason` | 마지막 트립 사유 | 포렌식, 포스트모템 |

---

## 6. 변경 범위

| 파일 | 변경 | 유형 | Breaking Change |
|------|------|------|----------------|
| `interfaces/repositories.py` | `CircuitBreakerStateData`에 `metadata` 필드 추가 | 수정 | ❌ `default_factory=dict` |
| `interfaces/repositories.py` | `CircuitBreakerStateRepository`에 `update_metadata()` 추가 | 수정 | ❌ 구체 메서드 (기본 구현) |
| `services/circuit_breaker/service.py` | `get_all_states()` 직렬화에 `metadata` 추가 | 수정 | ❌ 새 키 추가 |
| Redis Repository 구현체 | `metadata` JSON 직렬화/역직렬화 | 수정 | ❌ 기본값 처리 |
| Django ORM Repository 구현체 | `JSONField` 추가 + DB 마이그레이션 | 수정 | ❌ `default=dict` |
| `services/cell_topology/health.py` | `_get_cb_open_ratio()` Phase 2 전환 | 수정 | ❌ 내부 구현만 |

**기존 모든 코드 하위 호환** — `metadata` 필드는 기본값이 빈 dict이므로, metadata를 사용하지 않는 코드는 어떤 변경도 필요 없다.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (Cell 토폴로지 설정) |
| `264_CELL_HEALTH.md` | R4 Phase 1→Phase 2 전환 대상 |
| `263_CELL_TAGGER.md` | metadata 주입 시점 제공 |
| `coordination/scheduler.py` | `LeaderScheduler` (264 실행 방식) |
| `interfaces/repositories.py` | `CircuitBreakerStateData`, `CircuitBreakerStateRepository` 수정 대상 |
| `services/circuit_breaker/service.py` | `get_all_states()` 직렬화 수정, `_state_change_callbacks` 참조 |
| `services/circuit_breaker/manual_control.py` | `force_open`/`force_close` 콜백 미호출 근거 |
