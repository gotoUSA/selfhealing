# 303. Circuit Mesh Service — 오케스트레이터 + 통합 + 테스트 전략

> **Status**: Design
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/circuit_mesh/service.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/circuit_mesh/models.py` — 신규
> - `packages/selfhealing-python/tests/unit/services/circuit_mesh/` — 신규
> **References**:
> - [302_CIRCUIT_BREAKER_MESH_COORDINATOR.md](302_CIRCUIT_BREAKER_MESH_COORDINATOR.md) — MeshCoordinator 핵심 로직
> - `services/circuit_breaker/service.py` — CircuitBreakerService
> - `services/circuit_breaker/blast_radius_integration.py` — BlastRadiusIntegration
> - `services/event_bus/bus/__init__.py` — SelfHealingEventBus
> - `services/coordination/coordinator.py` — EmergencyCoordinator (싱글톤 패턴 참조)

---

## 1. 목적

302에서 설계한 MeshCoordinator의 핵심 알고리즘을
운영 환경에서 사용할 수 있는 **완전한 서비스**로 조립한다.

CircuitMeshService는:
- MeshCoordinator를 생성하고 EventBus에 핸들러를 등록한다
- 싱글톤 패턴으로 앱 전역에서 접근 가능하다
- 관리 API(상태 조회, 수동 오버라이드 해제)를 제공한다
- 멀티리전 환경에서 리전별 독립 메쉬로 동작한다

---

## 2. 기존 코드 근거

### 2.1 싱글톤 패턴 — BlastRadiusService 참조

`services/blast_radius/service.py:37-46`:

```python
class BlastRadiusService:
    _instance: BlastRadiusService | None = None
    _lock = Lock()

    def __new__(cls) -> BlastRadiusService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
```

**활용**: CircuitMeshService도 동일한 싱글톤 패턴을 사용한다.

### 2.2 EmergencyCoordinator — 조율 서비스 패턴 참조

`services/coordination/coordinator.py:47-60`:

```python
class DryRunAuditLogger:
    def log_would_execute(
        self,
        action: CoordinationAction,
        namespace: str,
        context: dict[str, Any],
    ) -> str:
```

**활용**: CircuitMeshService도 dry-run 모드를 지원하여,
오버라이드 적용 전 "어떤 조정이 발생할지" 미리 확인할 수 있다.

### 2.3 MultiRegion Router — 리전별 독립 운영

`multiregion/router.py` ServiceLocalityRouter:

```python
# cb:payment_kakao.* → ap-northeast-2
# cb:payment_stripe.* → us-east-1
```

**활용**: 각 리전의 MeshCoordinator는 해당 리전의 CB 이벤트만 처리한다.
크로스 리전 CB 이벤트는 `ReplicationFilter`를 통해 수신하되,
오버라이드 적용은 로컬 리전 CB에만 한다.

---

## 3. 파일 구조

```
packages/selfhealing-python/src/selfhealing/services/circuit_mesh/
    __init__.py             # get_circuit_mesh_service() 팩토리
    models.py               # DownstreamHealthSignal, ThresholdOverride, MeshStateSnapshot
    mesh_coordinator.py     # 핵심 조율 로직 (302 설계)
    service.py              # CircuitMeshService 오케스트레이터
```

---

## 4. CircuitMeshService 설계

### 4.1 서비스 클래스

```python
class CircuitMeshService:
    """
    Adaptive Circuit Breaker Mesh 오케스트레이터.

    MeshCoordinator를 생성하고 EventBus에 연결하며,
    운영 API를 제공한다.
    """

    _instance: CircuitMeshService | None = None
    _lock = Lock()

    def __new__(cls) -> CircuitMeshService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._settings = get_circuit_mesh_settings()
        self._coordinator: MeshCoordinator | None = None
        self._active = False
        self._initialized = True

    def start(self) -> None:
        """
        메쉬 서비스 시작.

        1. BlastRadiusIntegration에서 dependency graph 참조
        2. MeshCoordinator 생성
        3. EventBus에 핸들러 등록
        """
        if not self._settings.enabled:
            logger.info("circuit_mesh_service.disabled")
            return

        if self._active:
            logger.debug("circuit_mesh_service.already_active")
            return

        # 1. 의존성 수집
        from selfhealing.services.circuit_breaker.blast_radius_integration import (
            BlastRadiusIntegration,
        )
        from selfhealing.services.circuit_breaker.service import (
            CircuitBreakerService,
        )

        br_integration = BlastRadiusIntegration()
        cb_service = CircuitBreakerService()

        # 2. MeshCoordinator 생성
        self._coordinator = MeshCoordinator(
            dependency_graph=br_integration._dependency_graph,
            cb_service=cb_service,
            settings=self._settings,
        )

        # 3. EventBus 핸들러 등록
        register_mesh_handlers(self._coordinator)

        self._active = True
        logger.info("circuit_mesh_service.started")

    def stop(self) -> None:
        """메쉬 서비스 중지. 모든 오버라이드 해제."""
        if not self._active:
            return

        if self._coordinator:
            self._coordinator.release_all_overrides()

        self._active = False
        logger.info("circuit_mesh_service.stopped")
```

### 4.2 운영 API

```python
    # === 조회 API ===

    def get_mesh_state(self) -> MeshStateSnapshot:
        """현재 메쉬 상태 스냅샷 반환."""
        if not self._coordinator:
            return MeshStateSnapshot.empty()
        return self._coordinator.get_snapshot()

    def get_active_overrides(self) -> list[ThresholdOverride]:
        """현재 활성 오버라이드 목록."""
        if not self._coordinator:
            return []
        return list(self._coordinator._overrides.values())

    def get_recovery_order(self) -> list[str]:
        """현재 순차 복구 순서."""
        if not self._coordinator:
            return []
        return self._coordinator.get_recovery_order()

    # === 수동 제어 API ===

    def force_release_override(
        self, service_name: str, reason: str = ""
    ) -> bool:
        """
        특정 서비스의 오버라이드 수동 해제.

        운영자가 MeshCoordinator의 자동 조정을 무시하고
        원래 config로 즉시 복귀시킬 때 사용.
        """
        if not self._coordinator:
            return False
        return self._coordinator.force_release(service_name, reason)

    def force_release_all(self, reason: str = "") -> int:
        """모든 오버라이드 일괄 해제. 반환값: 해제된 오버라이드 수."""
        if not self._coordinator:
            return 0
        return self._coordinator.release_all_overrides(reason)

    # === Dry-Run API ===

    def simulate_downstream_failure(
        self, service_name: str
    ) -> list[ThresholdOverride]:
        """
        특정 서비스가 OPEN되었을 때 어떤 오버라이드가 발생할지 시뮬레이션.

        실제 오버라이드를 적용하지 않고 결과만 반환한다.
        운영 대시보드에서 "what-if" 분석에 사용.
        """
        if not self._coordinator:
            return []
        return self._coordinator.simulate_downstream_open(service_name)
```

### 4.3 팩토리 함수

```python
# circuit_mesh/__init__.py

_service: CircuitMeshService | None = None

def get_circuit_mesh_service() -> CircuitMeshService:
    """CircuitMeshService 싱글톤 반환."""
    global _service
    if _service is None:
        _service = CircuitMeshService()
    return _service
```

---

## 5. 모델 정의

```python
# circuit_mesh/models.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from selfhealing.services.circuit_breaker.config import CircuitState


@dataclass
class DownstreamHealthSignal:
    """하류 서비스의 CB 상태 시그널."""

    service_name: str
    state: CircuitState
    changed_at: datetime
    affected_upstream: list[str] = field(default_factory=list)


@dataclass
class ThresholdOverride:
    """상류 CB에 적용할 동적 임계치 오버라이드."""

    service_name: str
    original_failure_threshold: int
    adjusted_failure_threshold: int
    original_recovery_timeout: int
    adjusted_recovery_timeout: int
    reason: str
    expires_at: datetime
    created_at: datetime = field(default_factory=lambda: datetime.now())


@dataclass
class MeshStateSnapshot:
    """메쉬 전체 상태 스냅샷."""

    timestamp: datetime
    cb_states: dict[str, str] = field(default_factory=dict)
    active_overrides: list[ThresholdOverride] = field(default_factory=list)
    downstream_signals: list[DownstreamHealthSignal] = field(default_factory=list)
    recovery_queue: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> MeshStateSnapshot:
        """빈 스냅샷 생성."""
        return cls(timestamp=datetime.now())
```

---

## 6. 기존 모듈 변경사항

302에서 설계한 확장점의 구체적 변경 범위:

### 6.1 CircuitBreakerService (service.py)

| 변경 | 내용 | 영향도 |
|------|------|--------|
| `_threshold_overrides` dict 추가 | `__init__`에 빈 dict 초기화 | 없음 |
| `apply_threshold_override()` 추가 | 신규 메서드 | 없음 |
| `remove_threshold_override()` 추가 | 신규 메서드 | 없음 |
| `get_effective_config()` 추가 | 신규 메서드 | 없음 |
| `should_allow()`에서 config 참조 변경 | `self.config` → `self.get_effective_config(svc)` | 낮음 |
| `record_failure()`에서 config 참조 변경 | `self.config` → `self.get_effective_config(svc)` | 낮음 |

**Breaking change 없음**: 기존 `self.config` 속성은 유지되며,
오버라이드가 없는 서비스에서는 기존과 동일하게 동작한다.

### 6.2 BlastRadiusIntegration (blast_radius_integration.py)

| 변경 | 내용 | 영향도 |
|------|------|--------|
| `get_dependents()` 추가 | DependencyGraph에 역방향 조회 | 없음 |
| `topological_sort_subset()` 추가 | 부분 위상정렬 | 없음 |

**Breaking change 없음**: 신규 메서드 추가만.

### 6.3 Settings (settings/circuit_mesh.py)

신규 파일. `SELFHEALING_CIRCUIT_MESH_` 환경변수 prefix.
기존 settings 모듈과 동일한 패턴.

---

## 7. 멀티리전 동작

```
┌─────────────────────┐     ┌─────────────────────┐
│  Region: ap-ne-2    │     │  Region: us-east-1   │
│                     │     │                      │
│  MeshCoordinator    │     │  MeshCoordinator     │
│  ├─ CB: order_svc   │     │  ├─ CB: stripe_api   │
│  ├─ CB: payment_kr  │     │  ├─ CB: payment_us   │
│  └─ CB: points_svc  │     │  └─ CB: shipping_us  │
│                     │     │                      │
│  로컬 EventBus 구독  │     │  로컬 EventBus 구독   │
│  로컬 오버라이드 적용 │     │  로컬 오버라이드 적용  │
└─────────┬───────────┘     └──────────┬───────────┘
          │                            │
          └────── ReplicationFilter ───┘
                  cb:* 상태 복제 (기존)
```

**원칙**:
- 각 리전의 MeshCoordinator는 **로컬 EventBus 이벤트만** 처리
- 크로스 리전 CB 상태는 `ReplicationFilter`를 통해 수신하지만, **로컬 dependency graph에 등록된 서비스만** 오버라이드 대상
- `ServiceLocalityRouter.should_write_locally()`를 체크하여 로컬 리전 소유 CB만 조정

---

## 8. 테스트 전략

### 8.1 단위 테스트 위치

```
packages/selfhealing-python/tests/unit/services/circuit_mesh/
    __init__.py
    test_mesh_coordinator.py      # MeshCoordinator 핵심 로직
    test_service.py               # CircuitMeshService 오케스트레이터
    test_models.py                # 모델 직렬화/유효성
    test_threshold_override.py    # 오버라이드 적용/해제/TTL만료
    test_recovery_order.py        # 순차 복구 위상정렬
```

### 8.2 핵심 테스트 시나리오

#### TC-1: 하류 OPEN → 상류 임계치 상향

```python
def test_downstream_open_raises_upstream_threshold():
    """
    C가 OPEN되면 B의 failure_threshold가 multiplier만큼 상향된다.

    Given: A→B→C 의존 체인, threshold_multiplier=2.0
    When: C가 CIRCUIT_BREAKER_OPENED 이벤트 발행
    Then: B의 effective failure_threshold = 10 (원래 5 * 2.0)
    And: A의 failure_threshold는 변경 없음 (B는 아직 OPEN 아님)
    """
```

#### TC-2: 하류 복구 → 상류 오버라이드 해제

```python
def test_downstream_closed_releases_upstream_override():
    """
    C가 CLOSED되면 B의 오버라이드가 해제된다.

    Given: TC-1 상태 (B에 오버라이드 활성)
    When: C가 CIRCUIT_BREAKER_CLOSED 이벤트 발행
    Then: B의 effective failure_threshold = 5 (원래 값 복귀)
    And: active_overrides는 빈 리스트
    """
```

#### TC-3: 연쇄 오픈 방지 검증

```python
def test_cascade_open_prevented():
    """
    MeshCoordinator가 활성이면 연쇄 OPEN이 방지된다.

    Given: A→B→C, C가 OPEN, B의 threshold가 10으로 상향됨
    When: B에 failure 5회 기록 (원래 threshold에서는 OPEN)
    Then: B는 CLOSED 유지 (adjusted threshold 10 미도달)
    """
```

#### TC-4: 순차 복구 순서

```python
def test_recovery_order_leaves_first():
    """
    복구 순서가 하류 우선(리프 먼저)이다.

    Given: A→B→C 의존 체인, 모두 recovery_queue에 있음
    When: get_recovery_order() 호출
    Then: [C, B, A] 순서 반환
    """
```

#### TC-5: 오버라이드 TTL 만료

```python
def test_override_expires_after_ttl():
    """
    TTL 만료 후 오버라이드가 자동 정리된다.

    Given: B에 오버라이드 적용, override_ttl_seconds=600
    When: 601초 경과 후 get_effective_config(B) 호출
    Then: 원래 config 반환 (오버라이드 만료)
    """
```

#### TC-6: max_concurrent_overrides 안전장치

```python
def test_max_concurrent_overrides_limit():
    """
    동시 오버라이드 수가 max_concurrent_overrides를 초과하면
    신규 오버라이드가 거부된다.

    Given: max_concurrent_overrides=3, 이미 3개 활성
    When: 4번째 오버라이드 시도
    Then: 오버라이드 적용되지 않고 경고 로그 출력
    """
```

#### TC-7: Dry-run 시뮬레이션

```python
def test_simulate_downstream_failure():
    """
    simulate_downstream_failure()가 실제 오버라이드 없이 결과를 반환한다.

    Given: A→B→C 의존 체인
    When: simulate_downstream_failure("C") 호출
    Then: B에 대한 ThresholdOverride 반환
    And: 실제 active_overrides는 변경 없음
    """
```

#### TC-8: 멀티리전 격리

```python
def test_multiregion_isolation():
    """
    다른 리전의 CB 이벤트는 오버라이드를 트리거하지 않는다.

    Given: ap-ne-2 리전의 MeshCoordinator, B→C 의존성 등록
    When: us-east-1 리전의 "stripe_api" OPENED 이벤트 수신
    Then: 로컬 의존성 그래프에 없으므로 오버라이드 없음
    """
```

### 8.3 테스트 패턴

기존 프로젝트의 단위 테스트 패턴을 따른다:

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from selfhealing.services.circuit_mesh.mesh_coordinator import MeshCoordinator
from selfhealing.services.circuit_mesh.models import (
    ThresholdOverride,
    MeshStateSnapshot,
)


@pytest.fixture
def mock_dependency_graph():
    """테스트용 의존성 그래프."""
    graph = MagicMock()
    graph.get_dependents.return_value = ["service_b"]
    graph.topological_sort_subset.return_value = ["service_c", "service_b", "service_a"]
    return graph


@pytest.fixture
def mock_cb_service():
    """테스트용 CircuitBreakerService."""
    service = MagicMock()
    config = MagicMock()
    config.failure_threshold = 5
    config.recovery_timeout = 60
    service.get_config.return_value = config
    return service


@pytest.fixture
def coordinator(mock_dependency_graph, mock_cb_service):
    """테스트용 MeshCoordinator."""
    settings = MagicMock()
    settings.threshold_multiplier = 2.0
    settings.recovery_timeout_multiplier = 3.0
    settings.override_ttl_seconds = 600
    settings.max_concurrent_overrides = 20
    return MeshCoordinator(
        dependency_graph=mock_dependency_graph,
        cb_service=mock_cb_service,
        settings=settings,
    )
```

---

## 9. 통합 체크리스트

구현 완료 후 확인 사항:

- [ ] `circuit_mesh/__init__.py` — `get_circuit_mesh_service()` 팩토리 동작
- [ ] `mesh_coordinator.py` — 3개 이벤트 핸들러 (OPENED/HALF_OPENED/CLOSED) 동작
- [ ] `service.py` — 싱글톤, start/stop 라이프사이클
- [ ] `models.py` — 모든 dataclass 직렬화 가능
- [ ] `settings/circuit_mesh.py` — `SELFHEALING_CIRCUIT_MESH_` 환경변수 파싱
- [ ] `CircuitBreakerService` — `get_effective_config()` 오버라이드 적용
- [ ] `BlastRadiusIntegration` — `get_dependents()`, `topological_sort_subset()` 추가
- [ ] EventBus 핸들러 등록 — `register_mesh_handlers()` 호출
- [ ] Audit 로그 — `mesh_threshold_override`, `mesh_override_released` 기록
- [ ] Prometheus 메트릭 — 6개 메트릭 등록
- [ ] 단위 테스트 8개 시나리오 통과
- [ ] `enabled=False` 기본값 → 기존 동작에 영향 없음 확인

---

## 10. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 오버라이드 무한 누적 | 메모리 증가 | `max_concurrent_overrides` + TTL 자동 만료 |
| 복구 진동(oscillation) | 불안정 | 순차 복구 + 하류 CLOSED 확인 후에만 상류 해제 |
| dependency graph 미등록 서비스 | 오버라이드 누락 | `get_dependents()` 빈 리스트 반환 → 안전 |
| 멀티리전 이벤트 혼선 | 잘못된 오버라이드 | 로컬 dependency graph 범위만 처리 |
| 성능 영향 | EventBus 지연 | 핸들러 내부는 O(V) — 의존 서비스 수 비례, 통상 < 10 |

---

*문서 버전: 1.0*
*작성일: 2026-03-06*
*최종 업데이트: 2026-03-06*
