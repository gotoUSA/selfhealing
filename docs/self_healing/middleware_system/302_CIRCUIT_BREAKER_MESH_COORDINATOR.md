# 302. Circuit Breaker Mesh Coordinator — 하류 CB 상태 기반 상류 임계치 동적 조정

> **Status**: Design
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/circuit_mesh/mesh_coordinator.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/circuit_mesh/__init__.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/settings/circuit_mesh.py` — 신규
> **References**:
> - [303_CIRCUIT_MESH_SERVICE.md](303_CIRCUIT_MESH_SERVICE.md) — CircuitMeshService 오케스트레이터
> - `services/circuit_breaker/service.py` — CircuitBreakerService
> - `services/circuit_breaker/blast_radius_integration.py` — BlastRadiusIntegration
> - `services/event_bus/redis_bus.py` — RedisEventBus (CIRCUIT_BREAKER 채널)
> - `services/coordination/coordinator.py` — EmergencyCoordinator

---

## 1. 목적

현재 CircuitBreakerService는 **개별 서비스 단위**로 독립 동작한다.
서비스 A→B→C 호출 체인에서 C가 장애로 CB OPEN되면:

```
현행 문제:
    C: OPEN (장애)
    B: C 호출 시도 → 실패 누적 → B도 OPEN (연쇄)
    A: B 호출 시도 → 실패 누적 → A도 OPEN (연쇄)
    → 3개 서비스 모두 OPEN, 복구 시 각자 독립적 Half-Open → 불안정
```

MeshCoordinator는 **하류 CB 상태 변경 이벤트를 구독**하여,
상류 CB의 임계치를 동적으로 조정하고 복구 순서를 조율한다.

```
목표 플로우:
    C: OPEN (장애)
    MeshCoordinator: C OPEN 감지 → B에 "하류 장애" 시그널
    B: C 호출 전 즉시 Fallback (B는 OPEN 안 됨)
    A: B의 latency 증가 없음 → A도 정상 유지
    복구: C Half-Open 성공 → B Fallback 해제 → A 영향 없음
```

**설계 원칙**:
- 기존 EventBus/BlastRadius/LoadShedding 인프라를 **소비**만 한다 (중복 구현 금지)
- MeshCoordinator는 조율 계층이지, CB 상태 자체를 변경하지 않는다
- 상류 CB의 `failure_threshold`, `recovery_timeout`을 동적 조정하는 것이 핵심

---

## 2. 기존 코드 근거

### 2.1 CircuitBreakerService — 독립적 상태 전이

`services/circuit_breaker/service.py:244-304`:

```python
if state.state == CircuitState.OPEN:
    if state.opened_at:
        elapsed = (now() - state.opened_at).total_seconds()
        if elapsed >= self.config.recovery_timeout:
            # Transition to half-open via repository
            self.repository.update_state(
                service_name=service_name,
                state=CircuitState.HALF_OPEN,
                success_count=0,
            )
```

**문제점**: 각 CB가 `recovery_timeout` 후 독립적으로 HALF_OPEN 전이한다.
하류 C가 아직 OPEN인데 상류 B가 먼저 HALF_OPEN되면,
B의 테스트 요청이 C로 가서 실패 → B가 다시 OPEN → 복구 진동(oscillation).

**해결**: MeshCoordinator가 dependency graph를 참조하여,
하류가 CLOSED/HALF_OPEN 성공 전까지 상류의 `recovery_timeout`을 연장한다.

### 2.2 EventBus — CB 상태 이벤트 (이미 전파됨)

`services/event_bus/redis_bus.py:52-59`:

```python
class EventChannel(str, Enum):
    CIRCUIT_BREAKER = "circuit_breaker"
```

`services/circuit_breaker/service.py:784-793`:

```python
bus.emit(
    EventType.CIRCUIT_BREAKER_OPENED,
    {
        "service_name": service_name,
        "burn_rate_multiplier": multiplier,
        "timestamp": now().isoformat(),
    },
    source="circuit_breaker_service",
)
```

**활용**: MeshCoordinator는 `CIRCUIT_BREAKER_OPENED`, `CIRCUIT_BREAKER_CLOSED`,
`CIRCUIT_BREAKER_HALF_OPENED` 이벤트를 구독하여 메쉬 상태를 유지한다.
추가 이벤트 타입 불필요 — 기존 인프라 그대로 사용.

### 2.3 BlastRadiusIntegration — 의존성 그래프 (이미 존재)

`services/circuit_breaker/blast_radius_integration.py:323-349`:

```python
def register_dependency(
    self,
    service_id: str,
    depends_on: list[str] | None = None,
    criticality: str = "medium",
) -> None:
    self._dependency_graph.register_service(
        service_id=service_id,
        depends_on=depends_on,
        criticality=criticality,
    )
```

`services/circuit_breaker/blast_radius_integration.py:392-397`:

```python
# 1. 연쇄적으로 영향받는 서비스 수집
affected_services = set()
for service in all_failing:
    affected = self._dependency_graph.get_cascading_affected(service)
    affected_services.update(affected)
```

**활용**: MeshCoordinator는 `BlastRadiusIntegration._dependency_graph`를 읽기 전용으로 참조하여
상류/하류 관계를 파악한다. 별도 의존성 그래프를 구축하지 않는다.

### 2.4 CircuitBreakerConfig — 정적 임계치

`services/circuit_breaker/config.py:27-66`:

```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: int = 60
    success_threshold: int = 2
    minimum_calls: int = 10
    failure_rate_threshold: float = 50.0
```

**문제점**: 모든 설정이 정적이다. 하류 장애 시 상류의 `failure_threshold`를 올리거나
`recovery_timeout`을 연장하는 동적 조정 메커니즘이 없다.

**해결**: MeshCoordinator가 서비스별 **오버라이드 맵**을 관리하고,
CircuitBreakerService가 config 조회 시 오버라이드를 우선 적용하도록 확장점을 제공한다.

### 2.5 LoadSheddingManager — 백프레셔 (이미 존재)

`services/event_bus/bus/_throttle_handlers.py:16-58`:

```python
def _on_emergency_level_changed_throttle(event: SelfHealingEvent) -> None:
    throttle = get_adaptive_throttle()
    throttle.adjust_for_emergency(level)
```

**활용**: MeshCoordinator는 하류 장애 감지 시 LoadShedding을 직접 호출하지 않는다.
대신 기존 EventBus→Throttle 핸들러 체인을 통해 자동으로 처리된다.
MeshCoordinator는 **CB 임계치 조정**에만 집중한다.

---

## 3. 설계

### 3.1 MeshCoordinator 핵심 책임

| 책임 | 입력 | 출력 | 기존 인프라 |
|------|------|------|-------------|
| 하류 장애 전파 | `CB_OPENED` 이벤트 | 상류 CB 임계치 오버라이드 | EventBus 구독 |
| 순차 복구 조율 | `CB_HALF_OPENED` 이벤트 | 상류 recovery_timeout 연장/해제 | EventBus 구독 |
| 메쉬 상태 뷰 | dependency graph + CB states | `MeshStateSnapshot` | BlastRadiusIntegration |
| 프리엠티브 Fallback | 하류 OPEN 감지 | 상류에 fallback 권고 | CB sync callbacks |

### 3.2 메쉬 상태 모델

```python
@dataclass
class DownstreamHealthSignal:
    """하류 서비스의 CB 상태 시그널."""
    service_name: str
    state: CircuitState                # OPEN / HALF_OPEN / CLOSED
    changed_at: datetime
    affected_upstream: list[str]       # 이 하류에 의존하는 상류 서비스 목록

@dataclass
class ThresholdOverride:
    """상류 CB에 적용할 동적 임계치 오버라이드."""
    service_name: str
    original_failure_threshold: int
    adjusted_failure_threshold: int    # 하류 장애 시 상향 (더 관대하게)
    original_recovery_timeout: int
    adjusted_recovery_timeout: int     # 하류 OPEN 시 연장
    reason: str                        # "downstream:payment_api OPEN"
    expires_at: datetime               # TTL 기반 자동 만료

@dataclass
class MeshStateSnapshot:
    """메쉬 전체 상태 스냅샷."""
    timestamp: datetime
    cb_states: dict[str, CircuitState]          # service → state
    active_overrides: list[ThresholdOverride]    # 현재 활성 오버라이드
    downstream_signals: list[DownstreamHealthSignal]
    recovery_queue: list[str]                   # 순차 복구 대기열 (하류 우선)
```

### 3.3 하류 장애 전파 알고리즘

```python
class MeshCoordinator:
    """CB 메쉬 조율자 — 하류 상태 기반 상류 임계치 동적 조정."""

    def __init__(
        self,
        dependency_graph: DependencyGraph,
        cb_service: CircuitBreakerService,
        settings: CircuitMeshSettings,
    ):
        self._graph = dependency_graph
        self._cb = cb_service
        self._settings = settings
        self._overrides: dict[str, ThresholdOverride] = {}
        self._recovery_queue: list[str] = []

    def on_downstream_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB OPEN 이벤트 핸들러.

        1. dependency graph에서 이 서비스에 의존하는 상류 목록 조회
        2. 각 상류 CB의 failure_threshold를 상향 조정
        3. 각 상류 CB의 recovery_timeout을 연장
        4. 순차 복구 대기열에 하류 서비스 추가
        """
        downstream = event.data["service_name"]
        upstream_services = self._graph.get_dependents(downstream)

        for upstream in upstream_services:
            current_config = self._cb.get_config(upstream)
            override = ThresholdOverride(
                service_name=upstream,
                original_failure_threshold=current_config.failure_threshold,
                adjusted_failure_threshold=int(
                    current_config.failure_threshold
                    * self._settings.threshold_multiplier
                ),
                original_recovery_timeout=current_config.recovery_timeout,
                adjusted_recovery_timeout=int(
                    current_config.recovery_timeout
                    * self._settings.recovery_timeout_multiplier
                ),
                reason=f"downstream:{downstream} OPEN",
                expires_at=now() + timedelta(
                    seconds=self._settings.override_ttl_seconds
                ),
            )
            self._overrides[upstream] = override
            self._cb.apply_threshold_override(upstream, override)

            logger.info(
                "mesh_coordinator.upstream_threshold_adjusted",
                upstream=upstream,
                downstream=downstream,
                adjusted_failure_threshold=override.adjusted_failure_threshold,
                adjusted_recovery_timeout=override.adjusted_recovery_timeout,
            )

        # 복구 대기열에 추가 (하류 우선)
        if downstream not in self._recovery_queue:
            self._recovery_queue.append(downstream)
```

### 3.4 순차 복구 조율 알고리즘

```python
    def on_downstream_half_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB HALF_OPEN 이벤트 핸들러.

        하류가 HALF_OPEN 성공(CLOSED 전이) 시에만 상류 오버라이드 해제.
        """
        service = event.data["service_name"]
        # HALF_OPEN 시에는 아직 오버라이드 유지
        logger.info(
            "mesh_coordinator.downstream_half_opened",
            service=service,
            action="maintain_upstream_overrides",
        )

    def on_downstream_closed(self, event: SelfHealingEvent) -> None:
        """
        하류 CB CLOSED 이벤트 핸들러.

        하류 복구 완료 → 상류 오버라이드 해제 + 복구 대기열에서 제거.
        """
        downstream = event.data["service_name"]
        upstream_services = self._graph.get_dependents(downstream)

        for upstream in upstream_services:
            if upstream in self._overrides:
                override = self._overrides.pop(upstream)
                self._cb.remove_threshold_override(upstream)

                logger.info(
                    "mesh_coordinator.upstream_override_released",
                    upstream=upstream,
                    downstream=downstream,
                    restored_failure_threshold=override.original_failure_threshold,
                )

        # 복구 대기열에서 제거
        if downstream in self._recovery_queue:
            self._recovery_queue.remove(downstream)

    def get_recovery_order(self) -> list[str]:
        """
        순차 복구 순서 반환.

        dependency graph의 위상 정렬(topological sort) 기반:
        하류(리프) → 상류(루트) 순서로 Half-Open 시도.
        """
        if not self._recovery_queue:
            return []

        return self._graph.topological_sort_subset(
            self._recovery_queue,
            direction="leaves_first",
        )
```

### 3.5 EventBus 등록

```python
def register_mesh_handlers(coordinator: MeshCoordinator) -> None:
    """MeshCoordinator의 이벤트 핸들러를 EventBus에 등록."""
    from selfhealing.services.event_bus import EventType, get_event_bus

    bus = get_event_bus()
    bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, coordinator.on_downstream_opened)
    bus.subscribe(EventType.CIRCUIT_BREAKER_HALF_OPENED, coordinator.on_downstream_half_opened)
    bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, coordinator.on_downstream_closed)
```

### 3.6 CircuitBreakerService 확장점

MeshCoordinator가 CB config를 동적으로 조정하기 위해,
CircuitBreakerService에 **최소한의 확장**이 필요하다:

```python
# services/circuit_breaker/service.py 에 추가할 메서드 (2개)

def apply_threshold_override(
    self, service_name: str, override: ThresholdOverride
) -> None:
    """
    메쉬 코디네이터가 설정한 임계치 오버라이드 적용.

    오버라이드가 활성인 동안 해당 서비스의 failure_threshold와
    recovery_timeout은 오버라이드 값을 사용한다.
    """
    self._threshold_overrides[service_name] = override

def remove_threshold_override(self, service_name: str) -> None:
    """임계치 오버라이드 해제, 원래 config로 복귀."""
    self._threshold_overrides.pop(service_name, None)

def get_effective_config(self, service_name: str) -> CircuitBreakerConfig:
    """
    오버라이드 적용된 실효 config 반환.

    오버라이드가 없으면 기본 config, 있으면 해당 필드만 교체.
    """
    if service_name not in self._threshold_overrides:
        return self.config

    override = self._threshold_overrides[service_name]
    if now() > override.expires_at:
        # TTL 만료 → 자동 정리
        self._threshold_overrides.pop(service_name)
        return self.config

    return CircuitBreakerConfig(
        **{
            **vars(self.config),
            "failure_threshold": override.adjusted_failure_threshold,
            "recovery_timeout": override.adjusted_recovery_timeout,
        }
    )
```

`should_allow()`, `record_failure()`에서 `self.config` 대신 `self.get_effective_config(service_name)`을 사용하도록 변경한다.

---

## 4. Settings

```python
# settings/circuit_mesh.py

from pydantic_settings import BaseSettings

class CircuitMeshSettings(BaseSettings):
    """Adaptive Circuit Breaker Mesh 설정."""

    enabled: bool = False

    # 하류 OPEN 시 상류 failure_threshold 배율 (2.0 = 임계치 2배 상향)
    threshold_multiplier: float = 2.0

    # 하류 OPEN 시 상류 recovery_timeout 배율 (3.0 = 타임아웃 3배 연장)
    recovery_timeout_multiplier: float = 3.0

    # 오버라이드 자동 만료 (seconds) — safety net
    override_ttl_seconds: int = 600  # 10분

    # 순차 복구 활성화 여부
    coordinated_recovery_enabled: bool = True

    # 순차 복구 시 단계 간 대기 시간 (seconds)
    recovery_step_delay_seconds: int = 30

    # 메쉬 상태 스냅샷 주기 (seconds)
    snapshot_interval_seconds: int = 60

    # 최대 동시 오버라이드 수 (안전장치)
    max_concurrent_overrides: int = 20

    class Config:
        env_prefix = "SELFHEALING_CIRCUIT_MESH_"
```

---

## 5. DependencyGraph 확장

BlastRadiusIntegration의 기존 `_dependency_graph`에 아래 2개 메서드를 추가한다:

```python
# blast_radius_integration.py 의 DependencyGraph에 추가

def get_dependents(self, service_id: str) -> list[str]:
    """
    이 서비스에 의존하는 상류 서비스 목록 반환 (역방향 조회).

    기존 get_cascading_affected()는 하류 방향 BFS이므로,
    역방향(상류) 조회 메서드가 필요하다.
    """
    dependents = []
    for sid, dep in self._dependencies.items():
        if service_id in (dep.depends_on or []):
            dependents.append(sid)
    return dependents

def topological_sort_subset(
    self, services: list[str], direction: str = "leaves_first"
) -> list[str]:
    """
    주어진 서비스 부분집합에 대해 위상 정렬 수행.

    direction="leaves_first": 하류(의존 없는 리프) → 상류 순서 (복구용)
    direction="roots_first": 상류(루트) → 하류 순서
    """
    # Kahn's algorithm on subgraph
    ...
```

---

## 6. 감사(Audit) 연동

MeshCoordinator의 모든 조율 행위는 감사 로그에 기록한다:

| 이벤트 | action | 기록 내용 |
|--------|--------|-----------|
| 상류 임계치 조정 | `mesh_threshold_override` | upstream, downstream, original/adjusted 값 |
| 오버라이드 해제 | `mesh_override_released` | upstream, downstream, duration |
| 오버라이드 TTL 만료 | `mesh_override_expired` | upstream, ttl_seconds |
| 순차 복구 시작 | `mesh_recovery_started` | recovery_order, trigger |
| 순차 복구 완료 | `mesh_recovery_completed` | duration, services_recovered |

기존 `log_cb_state_change_audit()` 패턴을 따르되, `source="mesh_coordinator"`로 구분한다.

---

## 7. Prometheus 메트릭

```python
# 신규 메트릭 (기존 selfhealing_* 네임스페이스 준수)

selfhealing_mesh_overrides_active          # Gauge: 현재 활성 오버라이드 수
selfhealing_mesh_override_applied_total    # Counter: 오버라이드 적용 횟수
selfhealing_mesh_override_released_total   # Counter: 오버라이드 해제 횟수
selfhealing_mesh_override_expired_total    # Counter: TTL 만료 횟수
selfhealing_mesh_recovery_duration_seconds # Histogram: 순차 복구 소요 시간
selfhealing_mesh_cascade_prevented_total   # Counter: 연쇄 OPEN 방지 횟수
```

---

## 8. 아키텍처 다이어그램

```
┌──────────────────────────────────────────────────────────┐
│                     EventBus (기존)                       │
│  CIRCUIT_BREAKER 채널: OPENED / HALF_OPENED / CLOSED     │
└────────────┬─────────────────────────────┬───────────────┘
             │ subscribe                   │ subscribe
             ▼                             ▼
┌────────────────────────┐    ┌──────────────────────────┐
│   MeshCoordinator      │    │  _throttle_handlers (기존)│
│   (신규 302)           │    │  → LoadShedding 자동     │
│                        │    └──────────────────────────┘
│  ┌──────────────────┐  │
│  │ DownstreamHealth  │  │
│  │ Signal 관리       │  │
│  └────────┬─────────┘  │
│           │             │
│  ┌────────▼─────────┐  │
│  │ ThresholdOverride │  │    ┌──────────────────────────┐
│  │ 계산 + 적용       │──┼──▶│ CircuitBreakerService    │
│  └────────┬─────────┘  │    │ .apply_threshold_override│
│           │             │    │ .get_effective_config()  │
│  ┌────────▼─────────┐  │    └──────────────────────────┘
│  │ Recovery Queue    │  │
│  │ (위상정렬 기반)   │  │    ┌──────────────────────────┐
│  └──────────────────┘  │◀───│ BlastRadiusIntegration   │
│                        │    │ ._dependency_graph (읽기) │
└────────────────────────┘    └──────────────────────────┘
```

---

## 9. 구현 우선순위

| 순서 | 항목 | 예상 변경 |
|------|------|-----------|
| Q1 | DependencyGraph에 `get_dependents()`, `topological_sort_subset()` 추가 | blast_radius_integration.py 수정 |
| Q2 | CircuitBreakerService에 threshold override 메커니즘 추가 | service.py 수정 |
| Q3 | MeshCoordinator 구현 (하류 전파 + 순차 복구) | circuit_mesh/mesh_coordinator.py 신규 |
| Q4 | Settings + 감사 로그 + 메트릭 연동 | settings/, audit, metrics 수정 |
| Q5 | EventBus 핸들러 등록 + 통합 테스트 | → 303 문서 범위 |

---

*문서 버전: 1.0*
*작성일: 2026-03-06*
*최종 업데이트: 2026-03-06*
