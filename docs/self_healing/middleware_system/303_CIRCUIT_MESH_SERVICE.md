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
- TwoTierMeshOverrideStore(L1 dict + L2 Redis)를 사용하여 분산 환경에서 오버라이드를 관리한다
- 싱글톤 패턴으로 앱 전역에서 접근 가능하다
- 관리 API(상태 조회, 수동 오버라이드 해제)를 제공한다
- 멀티리전 환경에서 리전별 독립 메쉬로 동작한다
- 시작 시 L2(Redis)에서 글로벌 상태를 Hydration하여 기존 오버라이드를 복원한다

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

### 2.4 TwoTierMeshOverrideStore — 302 합의

`services/circuit_mesh/store.py`:

302에서 합의한 TwoTierMeshOverrideStore(L1 dict + L2 Redis per-key)를
전면 사용한다. 인메모리 dict 직접 참조(`self._coordinator._overrides.values()`)는
사용하지 않으며, 반드시 Store API를 통해 접근한다.

### 2.5 DistributedRecoveryLock — Cold Path 보호

`services/coordination/distributed_recovery_lock.py`:

Redis SET NX PX 기반 분산 락. Celery beat의 주기적 작업(TTL Renewal, Escalation)에서
단일 리더만 글로벌 상태를 갱신하도록 보호한다.

---

## 3. 파일 구조

```
packages/selfhealing-python/src/selfhealing/services/circuit_mesh/
    __init__.py             # get_circuit_mesh_service() 팩토리
    models.py               # DownstreamHealthSignal, ThresholdOverride, MeshStateSnapshot
    mesh_coordinator.py     # 핵심 조율 로직 (302 설계)
    store.py                # TwoTierMeshOverrideStore (L1 dict + L2 Redis)
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

        이벤트 유실 방지를 위해 다음 순서를 엄격히 준수한다:
        1. 의존성 수집 및 MeshCoordinator 생성
        2. EventBus 핸들러 등록 (구독 시작, 단 Hydration 완료 전 이벤트는 큐잉)
        3. L2(Redis) → L1 Hydration (기존 글로벌 오버라이드 복원)
        4. 정상 이벤트 처리 시작
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

        # 2. MeshCoordinator 생성 (TwoTierMeshOverrideStore 포함)
        override_store = TwoTierMeshOverrideStore(
            redis_client=get_redis_client(),
            key_prefix="selfhealing:mesh:override",
        )
        self._coordinator = MeshCoordinator(
            dependency_graph=br_integration._dependency_graph,
            cb_service=cb_service,
            override_store=override_store,
            settings=self._settings,
        )

        # 3. EventBus 핸들러 등록 (Hydration 전에 구독하여 이벤트 유실 방지)
        #    MeshCoordinator 내부에서 _hydrating 플래그로 Hydration 완료 전
        #    수신된 이벤트를 큐잉한다.
        self._coordinator.set_hydrating(True)
        register_mesh_handlers(self._coordinator)

        # 4. L2 → L1 Hydration (기존 글로벌 오버라이드 복원)
        self._coordinator.hydrate_from_store()

        # 5. 정상 처리 시작 (큐잉된 이벤트 flush)
        self._coordinator.set_hydrating(False)

        self._active = True
        logger.info("circuit_mesh_service.started")

    def stop(self) -> None:
        """
        메쉬 서비스 중지.

        분산 환경의 글로벌 정합성을 보장하기 위해:
        - EventBus 구독만 해제한다
        - L1(로컬 메모리) 캐시만 정리한다
        - L2(Redis)의 글로벌 오버라이드는 건드리지 않는다
          → TTL에 의한 자연 만료 또는 다른 인스턴스의 정상 해제에 위임

        K8s Rolling Update 시 파드가 종료되더라도 다른 파드의
        보호 상태가 유지된다.

        주의: 이 메서드가 SIGTERM 시그널 핸들러에 반드시
        연결되어야 한다. Django의 경우 AppConfig.ready()에서
        signal.signal(SIGTERM, handler)를 등록하거나,
        ShutdownCoordinator의 DRAINING 페이즈에 훅을 건다.
        """
        if not self._active:
            return

        # EventBus 구독 해제
        if self._coordinator:
            unregister_mesh_handlers(self._coordinator)
            self._coordinator.override_store.clear_l1()

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
        """현재 활성 오버라이드 목록. TwoTierStore API를 통해 조회."""
        if not self._coordinator:
            return []
        return self._coordinator.override_store.get_all()

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
        """
        모든 오버라이드 일괄 해제 (L1 + L2 모두).
        운영자가 명시적으로 호출하는 수동 API.
        반환값: 해제된 오버라이드 수.
        """
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
    renewal_count: int = 0


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

## 6. Settings 정의

```python
# settings/circuit_mesh.py

class CircuitMeshSettings(SelfHealingBaseSettings):
    """SELFHEALING_CIRCUIT_MESH_ 환경변수 prefix."""

    # === 기본 설정 ===
    enabled: bool = False
    threshold_multiplier: float = 2.0
    recovery_timeout_multiplier: float = 3.0
    override_ttl_seconds: int = 600
    max_concurrent_overrides: int = 20

    # === Damped Propagation ===
    propagation_max_depth: int = 1
    propagation_damping_factor: float = 0.5

    # === Fast-Recovery ===
    fast_recovery_timeout_seconds: int = 5

    # === TTL Heartbeat ===
    max_renewals: int = 3

    # === 세부 기능별 Feature Flag ===
    # 스테이징 환경에서 하나씩 켜가며 격리 테스트 가능
    enable_damped_propagation: bool = True
    enable_preemptive_fallback: bool = True
    enable_fast_recovery: bool = True
    enable_ttl_heartbeat: bool = True

    # === 크로스 리전 의존성 ===
    # 로컬 그래프에 존재하는 타 리전 서비스 목록 (명시적 Opt-in)
    # 예: ["auth_global", "payment_stripe"]
    cross_region_dependencies: list[str] = []

    class Config:
        env_prefix = "SELFHEALING_CIRCUIT_MESH_"
```

---

## 7. 기존 모듈 변경사항

302에서 설계한 확장점의 구체적 변경 범위:

### 7.1 CircuitBreakerService (service.py)

| 변경 | 내용 | 영향도 |
|------|------|--------|
| `_threshold_overrides` dict 추가 | `__init__`에 빈 dict 초기화 | 없음 |
| `_downstream_checkers` list 추가 | Preemptive Fallback용 pre-check 함수 목록 | 없음 |
| `register_downstream_checker()` 추가 | 신규 메서드 | 없음 |
| `apply_threshold_override()` 추가 | 신규 메서드 | 없음 |
| `remove_threshold_override()` 추가 | 신규 메서드 | 없음 |
| `get_effective_config()` 추가 | 신규 메서드 | 없음 |
| `should_allow()`에 downstream checker 호출 추가 | pre-check hook | 낮음 |
| `should_allow()`에서 config 참조 변경 | `self.config` → `self.get_effective_config(svc)` | 낮음 |
| `record_failure()`에서 config 참조 변경 | `self.config` → `self.get_effective_config(svc)` | 낮음 |

**Breaking change 없음**: 기존 `self.config` 속성은 유지되며,
오버라이드가 없는 서비스에서는 기존과 동일하게 동작한다.

### 7.2 BlastRadiusIntegration (blast_radius_integration.py)

| 변경 | 내용 | 영향도 |
|------|------|--------|
| `get_dependents()` 추가 | DependencyGraph에 역방향 조회 | 없음 |
| `topological_sort_subset()` 추가 | 부분 위상정렬 | 없음 |

**Breaking change 없음**: 신규 메서드 추가만.

### 7.3 Settings (settings/circuit_mesh.py)

신규 파일. `SELFHEALING_CIRCUIT_MESH_` 환경변수 prefix.
기존 settings 모듈과 동일한 패턴.
세부 기능별 Feature Flag 포함 (6항 참조).

---

## 8. 분산 환경 동작 원칙

### 8.1 멱등성 및 분산 락 — 하이브리드 전략

```
┌──────────────────────────────────────────────────┐
│ Hot Path (이벤트 핸들러)                            │
│ → 락 없이 멱등 덮어쓰기 (성능 최우선)                │
│ → L1 즉시 갱신 + L2 async sync                     │
│ → 동일 이벤트를 여러 인스턴스가 처리해도 결과 동일    │
├──────────────────────────────────────────────────┤
│ Cold Path (Celery beat 주기 작업)                   │
│ → DistributedRecoveryLock 활용                      │
│ → check_override_renewals()는 리더만 실행            │
│ → EmergencyCoordinator 에스컬레이션도 리더만          │
│ → 락 TTL > 함수 최대 실행 시간 (Brain Split 방지)   │
└──────────────────────────────────────────────────┘
```

**Hot Path 멱등성 근거**:

| 연산 | 동시 실행 결과 | 멱등? |
|------|---------------|------|
| Override 적용 (OPEN 이벤트) | 동일 ThresholdOverride 계산 → 같은 값 덮어쓰기 | Yes |
| Override 해제 (CLOSED 이벤트) | 동일 키 삭제 | Yes |
| `_downstream_open_set` 갱신 | Set add/remove | Yes |

**Cold Path 분산 락 적용**:

```python
# Celery beat task에서:
lock = DistributedRecoveryLock(redis_client)
if lock.acquire("mesh_renewal", session_id, blocking=False):
    try:
        coordinator.check_override_renewals()
    finally:
        lock.release("mesh_renewal", session_id)
```

| 연산 | 동시 실행 위험 | 락 필요? |
|------|--------------|---------|
| TTL Renewal (`renewal_count++`) | 중복 증가 | Yes |
| EmergencyCoordinator 에스컬레이션 | 중복 알림 | Yes |

### 8.2 Graceful Shutdown 원칙

- `stop()` 호출 시 **EventBus 구독 해제 + L1 정리**만 수행
- L2(Redis)의 글로벌 오버라이드는 건드리지 않음
- `force_release_all()`은 운영자 전용 수동 API로만 제공
- SIGTERM 핸들러 연결 필수:
  - Django: `AppConfig.ready()`에서 `signal.signal(SIGTERM, handler)` 등록
  - 또는 `ShutdownCoordinator`의 DRAINING 페이즈에 훅 연결
  - SIGKILL 전에 로컬 정리가 완료되도록 `terminationGracePeriodSeconds` 충분히 설정

---

## 9. 멀티리전 동작

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

### 9.1 크로스 리전 의존성 예외 처리

타 리전 서비스(예: `us-east-1`의 `auth_global`)에 대한 강한 의존성이
로컬 그래프에 존재하는 경우, `cross_region_dependencies` 설정으로
명시적 Opt-in한다.

```python
# MeshCoordinator.on_downstream_opened() 내부
def _is_processable(self, service_name: str) -> bool:
    """이벤트 처리 대상 판별."""
    # 로컬 dependency graph에 등록된 서비스
    if self._dependency_graph.has_node(service_name):
        return True
    # 명시적으로 선언된 크로스 리전 의존성
    if service_name in self._settings.cross_region_dependencies:
        return True
    return False
```

**핵심 원칙**:
1. **이벤트 수신**: 타 리전 OPEN 이벤트는 `ReplicationFilter`를 통해 수신 가능
2. **Override 적용 대상**: 항상 **로컬 소유 상류 서비스**에만 적용 (`should_write_locally()` 준수)
3. **선언 없는 타 리전 서비스**: 기존대로 무시 (암묵적 허용 금지)

---

## 10. 테스트 전략

### 10.1 단위 테스트 위치

```
packages/selfhealing-python/tests/unit/services/circuit_mesh/
    __init__.py
    test_mesh_coordinator.py      # MeshCoordinator 핵심 로직
    test_service.py               # CircuitMeshService 오케스트레이터
    test_models.py                # 모델 직렬화/유효성
    test_threshold_override.py    # 오버라이드 적용/해제/TTL만료
    test_recovery_order.py        # 순차 복구 위상정렬
    test_store.py                 # TwoTierMeshOverrideStore L1/L2 동기화
```

### 10.2 핵심 테스트 시나리오

#### TC-1: 하류 OPEN → Damped Propagation으로 상류 임계치 상향

```python
def test_downstream_open_raises_upstream_threshold_with_damping():
    """
    C가 OPEN되면 Damped Propagation에 의해 B와 A 모두 임계치가 상향된다.
    B는 depth=1로 풀 배율, A는 depth=2로 감쇠 배율.

    Given: A→B→C 의존 체인, threshold_multiplier=2.0, damping_factor=0.5
    When: C가 CIRCUIT_BREAKER_OPENED 이벤트 발행
    Then: B의 effective failure_threshold = 10 (원래 5 * 2.0, depth=1, damping=0.5^0=1.0)
    And: A의 effective failure_threshold = 8 (원래 5 * 1.5, depth=2, damping=0.5^1=0.5)
         → effective_multiplier = 1.0 + (2.0 - 1.0) * 0.5 = 1.5
    And: TwoTierStore의 L1과 L2에 모두 override가 저장됨
    """
```

#### TC-2: 하류 복구 → 상류 오버라이드 해제 + Fast-Recovery

```python
def test_downstream_closed_releases_override_and_applies_fast_recovery():
    """
    C가 CLOSED되면 B의 오버라이드가 해제되고 Fast-Recovery 오버라이드가 적용된다.

    Given: TC-1 상태 (B에 오버라이드 활성)
    When: C가 CIRCUIT_BREAKER_CLOSED 이벤트 발행
    Then: B의 기존 threshold 오버라이드가 해제됨
    And: B에 fast_recovery_timeout_seconds=5 적용 (빠른 재검증)
    And: Fast-Recovery 완료 후 B의 effective config = 원래 값 복귀
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
    Then: B와 A에 대한 ThresholdOverride 반환 (Damped Propagation 반영)
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

#### TC-9: Preemptive Fallback

```python
def test_preemptive_fallback_rejects_when_downstream_open():
    """
    하류 서비스가 OPEN이면 should_allow()에서 선제적으로 거부한다.

    Given: B→C 의존성, C가 OPEN 상태
    When: B에 대해 should_allow() 호출
    Then: downstream_checker가 False 반환 → should_allow() = False
    And: B의 CB 상태는 변경 없음 (CLOSED 유지)
    And: mesh_preemptive_fallback_total 메트릭 증가
    """
```

#### TC-10: TTL Heartbeat Renewal 및 Escalation

```python
def test_ttl_renewal_extends_override():
    """
    하류가 여전히 OPEN이면 TTL 갱신으로 오버라이드를 연장한다.

    Given: B에 오버라이드 활성, C가 여전히 OPEN, max_renewals=3
    When: check_override_renewals() 호출
    Then: B의 오버라이드 TTL 갱신, renewal_count = 1
    """


def test_ttl_escalation_after_max_renewals():
    """
    max_renewals 초과 시 EmergencyCoordinator로 에스컬레이션한다.

    Given: B에 오버라이드 활성, renewal_count = 3, max_renewals=3
    When: check_override_renewals() 호출
    Then: EmergencyCoordinator.escalate() 호출
    And: mesh_escalation_total 메트릭 증가
    """
```

#### TC-11: State Hydration at Startup

```python
def test_hydration_restores_overrides_from_l2():
    """
    새 파드 시작 시 L2(Redis)에서 기존 오버라이드를 복원한다.

    Given: L2(Redis)에 B에 대한 오버라이드가 존재
    When: CircuitMeshService.start() 호출 (Hydration 수행)
    Then: L1에 B의 오버라이드가 복원됨
    And: _downstream_open_set에 해당 downstream 서비스가 추가됨
    And: get_effective_config(B)가 오버라이드된 값 반환
    """
```

#### TC-12: Graceful Shutdown — 글로벌 상태 보존

```python
def test_stop_preserves_global_overrides():
    """
    stop() 호출 시 L2(Redis)의 글로벌 오버라이드가 보존된다.

    Given: B에 오버라이드 활성 (L1 + L2)
    When: CircuitMeshService.stop() 호출
    Then: EventBus 구독 해제됨
    And: L1 캐시 비워짐
    And: L2(Redis)의 오버라이드는 그대로 존재
    """
```

#### TC-13: Feature Flag 격리

```python
def test_damped_propagation_disabled_falls_back_to_direct_only():
    """
    enable_damped_propagation=False이면 직접 부모만 오버라이드된다.

    Given: A→B→C, enable_damped_propagation=False
    When: C가 OPEN
    Then: B에만 오버라이드 적용 (depth=1, 풀 배율)
    And: A는 변경 없음 (전파 없음)
    """


def test_preemptive_fallback_disabled():
    """
    enable_preemptive_fallback=False이면 downstream checker가 등록되지 않는다.

    Given: enable_preemptive_fallback=False
    When: MeshCoordinator.initialize() 호출
    Then: cb_service.register_downstream_checker() 호출되지 않음
    """
```

#### TC-14: 크로스 리전 의존성

```python
def test_cross_region_dependency_override_applied():
    """
    cross_region_dependencies에 선언된 타 리전 서비스의 OPEN 이벤트로
    로컬 상류 서비스에 오버라이드가 적용된다.

    Given: ap-ne-2의 order_svc → auth_global (us-east-1)
           cross_region_dependencies=["auth_global"]
    When: auth_global OPENED 이벤트 수신 (ReplicationFilter 경유)
    Then: order_svc에 오버라이드 적용됨
    And: 오버라이드 대상은 로컬 소유(should_write_locally) 서비스만
    """


def test_undeclared_cross_region_dependency_ignored():
    """
    cross_region_dependencies에 없는 타 리전 서비스의 OPEN 이벤트는 무시된다.

    Given: cross_region_dependencies=[]
    When: 타 리전의 "unknown_service" OPENED 이벤트 수신
    Then: 오버라이드 없음
    """
```

### 10.3 테스트 패턴

기존 프로젝트의 단위 테스트 패턴을 따른다:

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from selfhealing.services.circuit_mesh.mesh_coordinator import MeshCoordinator
from selfhealing.services.circuit_mesh.store import TwoTierMeshOverrideStore
from selfhealing.services.circuit_mesh.models import (
    ThresholdOverride,
    MeshStateSnapshot,
)


@pytest.fixture
def mock_dependency_graph():
    """테스트용 의존성 그래프."""
    graph = MagicMock()
    graph.get_dependents.return_value = [
        ("service_b", 1),
        ("service_a", 2),
    ]
    graph.has_node.return_value = True
    graph.topological_sort_subset.return_value = [
        "service_c", "service_b", "service_a",
    ]
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
def mock_override_store():
    """테스트용 TwoTierMeshOverrideStore."""
    store = MagicMock(spec=TwoTierMeshOverrideStore)
    store.get_all.return_value = []
    store.get.return_value = None
    return store


@pytest.fixture
def coordinator(mock_dependency_graph, mock_cb_service, mock_override_store):
    """테스트용 MeshCoordinator."""
    settings = MagicMock()
    settings.threshold_multiplier = 2.0
    settings.recovery_timeout_multiplier = 3.0
    settings.override_ttl_seconds = 600
    settings.max_concurrent_overrides = 20
    settings.propagation_max_depth = 2
    settings.propagation_damping_factor = 0.5
    settings.fast_recovery_timeout_seconds = 5
    settings.max_renewals = 3
    settings.enable_damped_propagation = True
    settings.enable_preemptive_fallback = True
    settings.enable_fast_recovery = True
    settings.enable_ttl_heartbeat = True
    settings.cross_region_dependencies = []
    return MeshCoordinator(
        dependency_graph=mock_dependency_graph,
        cb_service=mock_cb_service,
        override_store=mock_override_store,
        settings=settings,
    )
```

---

## 11. 통합 체크리스트

구현 완료 후 확인 사항:

- [ ] `circuit_mesh/__init__.py` — `get_circuit_mesh_service()` 팩토리 동작
- [ ] `mesh_coordinator.py` — 3개 이벤트 핸들러 (OPENED/HALF_OPENED/CLOSED) 동작
- [ ] `store.py` — TwoTierMeshOverrideStore L1/L2 동기화
- [ ] `service.py` — 싱글톤, start/stop 라이프사이클
- [ ] `service.py` — start() Hydration (L2 → L1 복원) 동작
- [ ] `service.py` — stop() EventBus 해제만, L2 보존 확인
- [ ] `service.py` — SIGTERM 핸들러 연결 확인
- [ ] `models.py` — 모든 dataclass 직렬화 가능, `renewal_count` 필드 포함
- [ ] `settings/circuit_mesh.py` — Feature Flag 4개 포함, `cross_region_dependencies` 포함
- [ ] `CircuitBreakerService` — `get_effective_config()` 오버라이드 적용
- [ ] `CircuitBreakerService` — `register_downstream_checker()` Preemptive Fallback 동작
- [ ] `BlastRadiusIntegration` — `get_dependents()`, `topological_sort_subset()` 추가
- [ ] EventBus 핸들러 등록 — `register_mesh_handlers()` 호출
- [ ] EventBus 핸들러 해제 — `unregister_mesh_handlers()` 호출
- [ ] Celery beat — `check_override_renewals()` DistributedRecoveryLock 적용
- [ ] Audit 로그 — `mesh_threshold_override`, `mesh_override_released` 기록
- [ ] Prometheus 메트릭 — 10개 메트릭 등록 (기존 6개 + preemptive_fallback, escalation, drift, recovery_duration)
- [ ] 단위 테스트 14개 시나리오 통과
- [ ] `enabled=False` 기본값 → 기존 동작에 영향 없음 확인
- [ ] Feature Flag 개별 비활성화 시 해당 기능만 비활성 확인

---

## 12. 리스크 및 완화

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 오버라이드 무한 누적 | 메모리 증가 | `max_concurrent_overrides` + TTL 자동 만료 |
| 복구 진동(oscillation) | 불안정 | 순차 복구 + Fast-Recovery + 하류 CLOSED 확인 후 상류 해제 |
| dependency graph 미등록 서비스 | 오버라이드 누락 | `get_dependents()` 빈 리스트 반환 → 안전 |
| 멀티리전 이벤트 혼선 | 잘못된 오버라이드 | 로컬 dependency graph + `cross_region_dependencies` 명시 선언 |
| 성능 영향 | EventBus 지연 | 핸들러 내부는 O(V) — 의존 서비스 수 비례, 통상 < 10 |
| L1↔L2 drift | 일시적 불일치 | `check_drift()` 주기 감지 + `mesh_override_store_drift_total` 메트릭 |
| Rolling Update 시 보호 상실 | 연쇄 OPEN | stop()에서 L2 미삭제 + Hydration으로 신규 파드 즉시 복원 |
| TTL Renewal 중복 실행 | renewal_count 과다 증가 | DistributedRecoveryLock으로 리더만 실행 |
| Hydration 중 이벤트 유실 | 상태 누락 | 구독 먼저 → Hydration → flush 순서 보장 |
| Feature Flag 조합 폭발 | 테스트 복잡도 | 각 Flag 독립 테스트 + 기본값은 모두 True |

---

*문서 버전: 2.0*
*작성일: 2026-03-06*
*최종 업데이트: 2026-03-06*
*변경 이력: v1.0 → v2.0: 302 합의안 전면 반영 (TwoTierStore, Damped Propagation, Preemptive Fallback, Fast-Recovery, TTL Heartbeat), State Hydration, Graceful Shutdown 분산 정합성, 멱등성/분산 락 하이브리드 전략, 크로스 리전 명시적 Opt-in, Feature Flag, TC 8개→14개 확장*
