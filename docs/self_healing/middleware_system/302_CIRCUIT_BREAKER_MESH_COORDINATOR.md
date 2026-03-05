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
> - `adapters/memory/layered_repository/` — Two-Tier Cache 패턴 참조
> - `adapters/cache/redis_adapter.py` — Redis 분산 락 참조

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
    MeshCoordinator: C OPEN 감지 → B의 _downstream_open_set에 C 추가
    B: should_allow() 호출 시 하류 pre-check → C가 OPEN임을 감지 → 즉시 Fallback (B는 OPEN 안 됨)
    A: B의 latency 증가 없음 → A도 정상 유지
    복구: C Half-Open 성공 → C CLOSED → B의 _downstream_open_set에서 C 제거 → B Fallback 해제
          → B의 recovery_timeout을 fast-recovery 값(5초)으로 단축 → B 빠른 HALF_OPEN 전이
```

**설계 원칙**:
- 기존 EventBus/BlastRadius/LoadShedding 인프라를 **소비**만 한다 (중복 구현 금지)
- MeshCoordinator는 조율 계층이지, CB 상태 자체를 변경하지 않는다
- 상류 CB의 `failure_threshold`, `recovery_timeout`을 동적 조정하는 것이 핵심
- **분산 환경(멀티 인스턴스)을 기본 가정**으로 설계한다
- Hot Path(`should_allow()`)에서 외부 I/O를 절대 발생시키지 않는다

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

### 2.2 CircuitBreakerService — 동기 콜백 시스템

`services/circuit_breaker/service.py:95-182`:

```python
def register_state_change_callback(
    state: str, callback: Callable[[str, str, str], None]
) -> None:
    # state: "open", "closed", or "half_open"
    # callback(service_name, old_state, new_state) -> None

def _invoke_state_change_callbacks(
    service_name: str, old_state: str, new_state: str
) -> None:
    # 동기 실행 — EventBus emit보다 먼저 호출됨
```

**활용**: MeshCoordinator의 프리엠티브 Fallback은 이 동기 콜백 시스템을 활용한다.
콜백은 EventBus 발행 전에 **동일 프로세스 내에서 즉시** 실행되므로,
단일 인스턴스 내에서는 레이스 컨디션이 발생하지 않는다.

### 2.3 EventBus — CB 상태 이벤트 (이미 전파됨)

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

**분산 전파 메커니즘** (`services/event_bus/redis_bus.py:288-339`):
1. 로컬 버스에 publish (동기, 항상 성공)
2. Redis Pub/Sub로 타 인스턴스에 전파
3. Redis 실패 시 Kafka fallback (critical 이벤트)
4. 양쪽 실패 시 WAL 기록 (안전망)

이 기존 전파 체인을 통해 `_downstream_open_set`과 오버라이드 상태가
멀티 인스턴스 간에 자동으로 동기화된다.

### 2.4 BlastRadiusIntegration — 의존성 그래프 (이미 존재)

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

**기존 메서드 현황**:
- `get_dependents()` (`blast_radius_integration.py:162`): 직접 상류만 반환 (1-depth)
- `get_cascading_affected()` (`blast_radius_integration.py:176`): 재귀적 하류 BFS + visited 추적

### 2.5 CircuitBreakerConfig — 정적 임계치

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

### 2.6 LoadSheddingManager — 백프레셔 (이미 존재)

`services/event_bus/bus/_throttle_handlers.py:16-58`:

```python
def _on_emergency_level_changed_throttle(event: SelfHealingEvent) -> None:
    throttle = get_adaptive_throttle()
    throttle.adjust_for_emergency(level)
```

**활용**: MeshCoordinator는 하류 장애 감지 시 LoadShedding을 직접 호출하지 않는다.
대신 기존 EventBus→Throttle 핸들러 체인을 통해 자동으로 처리된다.
MeshCoordinator는 **CB 임계치 조정**에만 집중한다.

### 2.7 기존 Two-Tier Cache 패턴 (분산 저장소 참조)

`adapters/memory/layered_repository/`:

프로젝트에는 이미 L1(인메모리) + L2(Redis/DB)의 Two-Tier Cache 패턴이 확립되어 있다.

- **L1**: `InMemoryCircuitBreakerStateRepository` — 즉시 응답 (0.01ms)
- **L2**: Redis/Django DB — Bulkhead로 보호된 비동기 동기화
- **Drift 감지**: `PrecomputedCache.check_l1_l2_drift()` — L1↔L2 불일치 탐지

MeshCoordinator의 오버라이드 저장소(`MeshOverrideStore`)도 이 패턴을 따른다.

---

## 3. 설계

### 3.1 MeshCoordinator 핵심 책임

| 책임 | 입력 | 출력 | 기존 인프라 |
|------|------|------|-------------|
| 하류 장애 전파 | `CB_OPENED` 이벤트 | 상류 CB 임계치 오버라이드 | EventBus 구독 |
| 프리엠티브 Fallback | 하류 OPEN 감지 | 상류 `should_allow()` pre-check 차단 | `_downstream_open_set` (로컬 인메모리) |
| 순차 복구 조율 | `CB_HALF_OPENED`/`CB_CLOSED` 이벤트 | 상류 오버라이드 해제 + fast-recovery | EventBus 구독 |
| 메쉬 상태 뷰 | dependency graph + CB states | `MeshStateSnapshot` | BlastRadiusIntegration |
| TTL Heartbeat | 주기적 체크 (Celery beat) | 오버라이드 자동 갱신/만료 | Celery beat scheduler |
| 감쇠 전파 | dependency graph depth | depth별 차등 오버라이드 | `get_dependents_recursive()` |

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
    renewal_count: int = 0             # 현재까지 자동 갱신된 횟수

@dataclass
class MeshStateSnapshot:
    """메쉬 전체 상태 스냅샷."""
    timestamp: datetime
    cb_states: dict[str, CircuitState]          # service → state
    active_overrides: list[ThresholdOverride]    # 현재 활성 오버라이드
    downstream_signals: list[DownstreamHealthSignal]
    recovery_queue: list[str]                   # 순차 복구 대기열 (하류 우선)
```

### 3.3 프리엠티브 Fallback 메커니즘

MeshCoordinator는 **능동적 보호**(프리엠티브 Fallback)와 **수동적 보호**(임계치 조정)를
2단계로 제공한다.

**능동적 보호**: `_downstream_open_set`을 통해 상류가 하류 호출 전에 즉시 Fallback.
**수동적 보호**: 임계치 상향으로 상류 CB가 OPEN되는 것을 지연.

```python
class MeshCoordinator:
    """CB 메쉬 조율자 — 하류 상태 기반 상류 임계치 동적 조정."""

    def __init__(
        self,
        dependency_graph: DependencyGraph,
        cb_service: CircuitBreakerService,
        override_store: MeshOverrideStore,
        settings: CircuitMeshSettings,
    ):
        self._graph = dependency_graph
        self._cb = cb_service
        self._store = override_store
        self._settings = settings
        self._recovery_queue: list[str] = []

        # 프리엠티브 Fallback을 위한 로컬 인메모리 Set.
        # Hot Path(should_allow())에서 O(1) 조회, 외부 I/O 없음.
        # EventBus(Redis Pub/Sub)를 통해 멀티 인스턴스 간 비동기 동기화.
        self._downstream_open_set: set[str] = set()
```

**`should_allow()` pre-check hook 설계**:

`CircuitBreakerService.should_allow()`에 하류 상태 pre-check를 삽입한다.
기존 동기 콜백 시스템(`register_state_change_callback`)의 패턴을 따르되,
**호출 전(pre-call)** 체크로 동작한다:

```python
# services/circuit_breaker/service.py 에 추가할 pre-check hook

def register_downstream_checker(
    self,
    checker: Callable[[str], bool],
) -> None:
    """
    should_allow() 호출 시 하류 상태를 체크하는 함수 등록.

    checker(service_name) → True면 하류 정상, False면 하류 장애 → 즉시 Fallback.
    이 함수는 반드시 로컬 인메모리 조회만 수행해야 한다 (외부 I/O 금지).
    """
    self._downstream_checkers.append(checker)

# should_allow() 내부에 삽입할 로직:
def should_allow(self, service_name: str) -> bool:
    # ... 기존 CB 상태 체크 전에 하류 pre-check 수행
    for checker in self._downstream_checkers:
        if not checker(service_name):
            # 하류 장애 감지 → 즉시 False 반환 (CB 상태 변경 없음)
            logger.info(
                "circuit_breaker.downstream_preemptive_fallback",
                service=service_name,
            )
            return False
    # ... 이후 기존 CB 상태 체크 로직 진행
```

MeshCoordinator가 등록하는 checker:

```python
def _check_downstream_health(self, service_name: str) -> bool:
    """
    service_name이 의존하는 하류 중 OPEN인 것이 있으면 False.
    O(1) set lookup만 수행, 외부 I/O 없음.
    """
    dependencies = self._graph.get_dependencies(service_name)
    return not any(dep in self._downstream_open_set for dep in dependencies)
```

**`_downstream_open_set` 동기화 전략**:

```
단일 인스턴스: CB 동기 콜백 → _downstream_open_set.add/discard → 즉시 반영
멀티 인스턴스: EventBus(Redis Pub/Sub) → 리스너 스레드 → _downstream_open_set 갱신
                                         (비동기, eventual consistency)
```

### 3.4 하류 장애 전파 알고리즘 (감쇠 전파)

```python
    def on_downstream_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB OPEN 이벤트 핸들러.

        1. _downstream_open_set에 추가 (프리엠티브 Fallback 활성화)
        2. dependency graph에서 감쇠 전파로 상류 목록 조회
        3. depth별 차등 배율로 상류 CB 임계치 오버라이드 적용
        4. 순차 복구 대기열에 하류 서비스 추가
        """
        downstream = event.data["service_name"]

        # 프리엠티브 Fallback 즉시 활성화
        self._downstream_open_set.add(downstream)

        # 감쇠 전파: depth에 따라 배율 감소
        affected = self._graph.get_dependents_recursive(
            downstream,
            max_depth=self._settings.propagation_max_depth,
        )

        for upstream, depth in affected:
            # depth별 감쇠 적용: depth 1 = 100%, depth 2 = 50%, ...
            damping = self._settings.propagation_damping_factor ** (depth - 1)
            effective_threshold_multiplier = 1.0 + (
                (self._settings.threshold_multiplier - 1.0) * damping
            )
            effective_recovery_multiplier = 1.0 + (
                (self._settings.recovery_timeout_multiplier - 1.0) * damping
            )

            current_config = self._cb.get_config(upstream)
            override = ThresholdOverride(
                service_name=upstream,
                original_failure_threshold=current_config.failure_threshold,
                adjusted_failure_threshold=int(
                    current_config.failure_threshold
                    * effective_threshold_multiplier
                ),
                original_recovery_timeout=current_config.recovery_timeout,
                adjusted_recovery_timeout=int(
                    current_config.recovery_timeout
                    * effective_recovery_multiplier
                ),
                reason=f"downstream:{downstream} OPEN (depth={depth})",
                expires_at=now() + timedelta(
                    seconds=self._settings.override_ttl_seconds
                ),
                renewal_count=0,
            )
            self._store.set(upstream, override)
            self._cb.apply_threshold_override(upstream, override)

            logger.info(
                "mesh_coordinator.upstream_threshold_adjusted",
                upstream=upstream,
                downstream=downstream,
                depth=depth,
                damping=damping,
                adjusted_failure_threshold=override.adjusted_failure_threshold,
                adjusted_recovery_timeout=override.adjusted_recovery_timeout,
            )

        # 복구 대기열에 추가 (하류 우선)
        if downstream not in self._recovery_queue:
            self._recovery_queue.append(downstream)
```

### 3.5 순차 복구 조율 알고리즘 (Fast-Recovery 포함)

```python
    def on_downstream_half_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB HALF_OPEN 이벤트 핸들러.

        하류가 HALF_OPEN 성공(CLOSED 전이) 시에만 상류 오버라이드 해제.
        HALF_OPEN 단계에서는 오버라이드와 _downstream_open_set 모두 유지.
        """
        service = event.data["service_name"]
        logger.info(
            "mesh_coordinator.downstream_half_opened",
            service=service,
            action="maintain_upstream_overrides",
        )

    def on_downstream_closed(self, event: SelfHealingEvent) -> None:
        """
        하류 CB CLOSED 이벤트 핸들러.

        하류 복구 완료 시:
        1. _downstream_open_set에서 제거 (프리엠티브 Fallback 해제)
        2. 상류 오버라이드를 fast-recovery 값으로 교체 (빠른 HALF_OPEN 전이 유도)
        3. 복구 대기열에서 제거

        CLOSED 이벤트는 하류가 success_threshold(기본 2회) 연속 성공 후 발행되므로,
        이 시점에서 하류는 최소한의 안정성이 검증된 상태이다.
        """
        downstream = event.data["service_name"]

        # 프리엠티브 Fallback 해제
        self._downstream_open_set.discard(downstream)

        # 상류에 fast-recovery 적용
        affected = self._graph.get_dependents_recursive(
            downstream,
            max_depth=self._settings.propagation_max_depth,
        )

        for upstream, depth in affected:
            existing = self._store.get(upstream)
            if existing is None:
                continue

            # 기존 오버라이드를 제거하는 대신, recovery_timeout을 짧은 값으로 교체하여
            # 상류가 빠르게 HALF_OPEN 테스트를 시작하도록 유도한다.
            fast_recovery_override = ThresholdOverride(
                service_name=upstream,
                original_failure_threshold=existing.original_failure_threshold,
                adjusted_failure_threshold=existing.original_failure_threshold,
                original_recovery_timeout=existing.original_recovery_timeout,
                adjusted_recovery_timeout=self._settings.fast_recovery_timeout_seconds,
                reason=f"downstream:{downstream} RECOVERED → fast-recovery",
                expires_at=now() + timedelta(
                    seconds=self._settings.fast_recovery_timeout_seconds + 10
                ),
                renewal_count=0,
            )
            self._store.set(upstream, fast_recovery_override)
            self._cb.apply_threshold_override(upstream, fast_recovery_override)

            logger.info(
                "mesh_coordinator.fast_recovery_applied",
                upstream=upstream,
                downstream=downstream,
                fast_recovery_timeout=self._settings.fast_recovery_timeout_seconds,
            )

        # 복구 대기열에서 제거
        if downstream in self._recovery_queue:
            self._recovery_queue.remove(downstream)

    def on_fast_recovery_completed(self, event: SelfHealingEvent) -> None:
        """
        상류 CB가 fast-recovery 후 CLOSED 전이 시 최종 오버라이드 해제.

        fast-recovery override의 짧은 TTL(15초)이 만료되면
        get_effective_config()에서 자동 정리되므로, 이 핸들러는
        즉시 정리를 위한 보조 역할이다.
        """
        service = event.data["service_name"]
        existing = self._store.get(service)
        if existing and "fast-recovery" in existing.reason:
            self._store.remove(service)
            self._cb.remove_threshold_override(service)

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

### 3.6 TTL Heartbeat 갱신 메커니즘

오버라이드 TTL(`override_ttl_seconds`, 기본 10분)이 만료되었는데
하류 서비스가 여전히 OPEN이면, 상류가 원래 임계치로 복귀하여
연쇄 장애가 재발한다. 이를 방지하기 위해 주기적 heartbeat 갱신을 수행한다.

**스케줄러**: Celery beat task로 `snapshot_interval_seconds`(기본 60초) 주기로 실행.
기존 `check_recovery_transitions()` (`service.py:900`)이 Celery beat로 구동되는
동일한 패턴을 따른다.

```python
    def check_override_renewals(self) -> None:
        """
        주기적 TTL 갱신 체크.
        Celery beat task로 snapshot_interval_seconds 주기에 실행.
        """
        all_overrides = self._store.get_all()

        for service_name, override in list(all_overrides.items()):
            remaining = (override.expires_at - now()).total_seconds()
            if remaining > self._settings.renewal_check_threshold_seconds:
                continue

            # 하류가 여전히 OPEN인지 확인
            downstream = self._extract_downstream_from_reason(override.reason)
            if downstream is None:
                continue

            downstream_state = self._cb.get_state(downstream)

            if downstream_state == CircuitState.OPEN:
                if override.renewal_count < self._settings.max_renewals:
                    # TTL 갱신
                    override.expires_at = now() + timedelta(
                        seconds=self._settings.override_ttl_seconds
                    )
                    override.renewal_count += 1
                    self._store.set(service_name, override)
                    self._cb.apply_threshold_override(service_name, override)

                    logger.info(
                        "mesh_coordinator.override_renewed",
                        service=service_name,
                        downstream=downstream,
                        renewal_count=override.renewal_count,
                        max_renewals=self._settings.max_renewals,
                    )
                else:
                    # 최대 갱신 횟수 초과 → EmergencyCoordinator로 에스컬레이션
                    logger.warning(
                        "mesh_coordinator.max_renewals_exceeded",
                        service=service_name,
                        downstream=downstream,
                        renewal_count=override.renewal_count,
                        action="escalate_to_emergency_coordinator",
                    )
                    # 만료 허용 + 운영자 개입 필요 신호
            else:
                # 하류가 이미 복구됨 → 오버라이드 해제
                self._store.remove(service_name)
                self._cb.remove_threshold_override(service_name)

    @staticmethod
    def _extract_downstream_from_reason(reason: str) -> str | None:
        """reason 문자열에서 하류 서비스명 추출."""
        # "downstream:payment_api OPEN" → "payment_api"
        # "downstream:payment_api RECOVERED → fast-recovery" → "payment_api"
        if not reason.startswith("downstream:"):
            return None
        return reason.split(":")[1].split()[0]
```

### 3.7 EventBus 등록

```python
def register_mesh_handlers(coordinator: MeshCoordinator) -> None:
    """MeshCoordinator의 이벤트 핸들러를 EventBus에 등록."""
    from selfhealing.services.event_bus import EventType, get_event_bus

    bus = get_event_bus()
    bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, coordinator.on_downstream_opened)
    bus.subscribe(EventType.CIRCUIT_BREAKER_HALF_OPENED, coordinator.on_downstream_half_opened)
    bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, coordinator.on_downstream_closed)
```

### 3.8 CircuitBreakerService 확장점

MeshCoordinator가 CB config를 동적으로 조정하기 위해,
CircuitBreakerService에 **최소한의 확장**이 필요하다:

```python
# services/circuit_breaker/service.py 에 추가할 메서드

def register_downstream_checker(
    self,
    checker: Callable[[str], bool],
) -> None:
    """
    should_allow() pre-check hook 등록.
    checker(service_name) → False면 프리엠티브 Fallback.
    checker는 반드시 로컬 인메모리 조회만 수행해야 한다 (외부 I/O 금지).
    """
    self._downstream_checkers.append(checker)

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

    L1 로컬 캐시에서 조회하므로 외부 I/O 없음.
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

## 4. 오버라이드 상태 저장소 (Two-Tier Cache)

분산 환경을 기본 가정으로 하여, **L1 인메모리 + L2 Redis** Two-Tier 저장소를 사용한다.
기존 `LayeredCircuitBreakerStateRepository` (`adapters/memory/layered_repository/`)의 패턴을 따른다.

### 4.1 MeshOverrideStore Protocol

```python
class MeshOverrideStore(Protocol):
    """오버라이드 상태 저장소 인터페이스."""
    def get(self, service_name: str) -> ThresholdOverride | None: ...
    def set(self, service_name: str, override: ThresholdOverride) -> None: ...
    def remove(self, service_name: str) -> None: ...
    def get_all(self) -> dict[str, ThresholdOverride]: ...
```

### 4.2 TwoTierMeshOverrideStore 구현

```python
class TwoTierMeshOverrideStore:
    """
    L1 로컬 dict + L2 Redis Hash 기반 Two-Tier 오버라이드 저장소.

    읽기 경로 (Hot Path):
        get()/get_all() → L1 dict 조회 → O(1), 외부 I/O 없음

    쓰기 경로:
        set()/remove() → L1 dict 즉시 갱신 → L2 Redis 비동기 쓰기
        → EventBus로 무효화 이벤트 발행 → 타 인스턴스 L1 갱신

    동기화:
        - EventBus(Redis Pub/Sub) 구독으로 타 인스턴스의 변경 수신
        - snapshot_interval_seconds 주기로 L1↔L2 drift 검증

    참조 구현: adapters/memory/layered_repository/repository_operations.py
    """

    REDIS_KEY = "selfhealing:mesh:overrides"

    def __init__(
        self,
        cache: CacheProvider,
        event_bus: EventBus,
    ):
        self._l1: dict[str, ThresholdOverride] = {}   # 로컬 인메모리
        self._cache = cache                             # L2 Redis
        self._bus = event_bus

    def get(self, service_name: str) -> ThresholdOverride | None:
        """L1에서 즉시 반환. 외부 I/O 없음."""
        return self._l1.get(service_name)

    def set(self, service_name: str, override: ThresholdOverride) -> None:
        """L1 즉시 갱신 + L2 비동기 쓰기 + 무효화 이벤트 발행."""
        self._l1[service_name] = override
        self._sync_to_l2(service_name, override)
        self._publish_invalidation(service_name, "set")

    def remove(self, service_name: str) -> None:
        """L1 즉시 제거 + L2 비동기 삭제 + 무효화 이벤트 발행."""
        self._l1.pop(service_name, None)
        self._remove_from_l2(service_name)
        self._publish_invalidation(service_name, "remove")

    def get_all(self) -> dict[str, ThresholdOverride]:
        """L1 전체 반환. 외부 I/O 없음."""
        return dict(self._l1)

    def on_invalidation_event(self, event: SelfHealingEvent) -> None:
        """타 인스턴스의 변경 수신 → L2에서 최신 값을 가져와 L1 갱신."""
        service_name = event.data["service_name"]
        action = event.data["action"]
        if action == "remove":
            self._l1.pop(service_name, None)
        else:
            # L2에서 최신 값 fetch → L1 갱신
            override = self._fetch_from_l2(service_name)
            if override:
                self._l1[service_name] = override

    def check_drift(self) -> dict:
        """L1↔L2 drift 감지. snapshot_interval_seconds 주기로 호출."""
        # PrecomputedCache.check_l1_l2_drift() 패턴 참조
        ...
```

### 4.3 ProviderRegistry 등록

```python
# factory.py 에 추가
ProviderRegistry.register_mesh_override_store("two_tier", TwoTierMeshOverrideStore)
ProviderRegistry.register_mesh_override_store("memory", InMemoryMeshOverrideStore)  # 테스트용
```

---

## 5. Settings

```python
# settings/circuit_mesh.py

from pydantic_settings import BaseSettings

class CircuitMeshSettings(BaseSettings):
    """Adaptive Circuit Breaker Mesh 설정."""

    enabled: bool = False

    # --- 임계치 조정 ---

    # 하류 OPEN 시 상류 failure_threshold 배율 (2.0 = 임계치 2배 상향)
    threshold_multiplier: float = 2.0

    # 하류 OPEN 시 상류 recovery_timeout 배율 (3.0 = 타임아웃 3배 연장)
    recovery_timeout_multiplier: float = 3.0

    # 오버라이드 자동 만료 (seconds) — safety net
    override_ttl_seconds: int = 600  # 10분

    # --- 감쇠 전파 ---

    # 전파 최대 깊이 (1 = 직접 부모만, 2 = 조부모까지)
    propagation_max_depth: int = 1

    # depth마다 배율 감쇠 계수 (0.5 = depth 2에서 배율 50% 적용)
    propagation_damping_factor: float = 0.5

    # --- 순차 복구 ---

    # 순차 복구 활성화 여부
    coordinated_recovery_enabled: bool = True

    # 순차 복구 시 단계 간 대기 시간 (seconds)
    recovery_step_delay_seconds: int = 30

    # 하류 복구 시 상류 fast-recovery timeout (seconds)
    fast_recovery_timeout_seconds: int = 5

    # --- TTL Heartbeat ---

    # 최대 자동 갱신 횟수 (초과 시 EmergencyCoordinator 에스컬레이션)
    max_renewals: int = 3

    # TTL 만료 N초 전부터 갱신 체크
    renewal_check_threshold_seconds: int = 60

    # --- 운영 ---

    # 메쉬 상태 스냅샷 + 갱신 체크 주기 (seconds, Celery beat)
    snapshot_interval_seconds: int = 60

    # 최대 동시 오버라이드 수 (안전장치)
    max_concurrent_overrides: int = 20

    class Config:
        env_prefix = "SELFHEALING_CIRCUIT_MESH_"
```

---

## 6. DependencyGraph 확장

BlastRadiusIntegration의 기존 `_dependency_graph`에 아래 메서드를 추가한다.

기존 `get_dependents()` (`blast_radius_integration.py:162`)는 직접 부모만 반환한다.
감쇠 전파를 위해 재귀적 상류 탐색 + 순환 참조 방어가 필요하다.

```python
# blast_radius_integration.py 의 ServiceDependencyGraph에 추가

def get_dependents_recursive(
    self,
    service_id: str,
    max_depth: int = 1,
    _visited: set[str] | None = None,
    _current_depth: int = 0,
) -> list[tuple[str, int]]:
    """
    감쇠 전파를 위한 재귀적 상류 서비스 탐색.

    Returns: [(service_name, depth)] — depth는 원본 서비스로부터의 거리.

    순환 참조 방어: visited set으로 이미 방문한 노드는 재탐색하지 않는다.
    기존 get_cascading_affected()의 BFS + visited 패턴을 따른다.

    순환 참조가 감지되면 메트릭(selfhealing_mesh_circular_dependency_detected_total)을
    기록하여 운영 팀이 의존성 그래프 건강 상태를 모니터링할 수 있도록 한다.
    """
    if _visited is None:
        _visited = set()
    if _current_depth >= max_depth or service_id in _visited:
        return []

    _visited.add(service_id)
    results = []

    for dependent in self.get_dependents(service_id):
        if dependent in _visited:
            # 순환 참조 감지 → 메트릭 기록, 탐색 중단
            logger.warning(
                "dependency_graph.circular_dependency_detected",
                service=service_id,
                dependent=dependent,
            )
            continue
        results.append((dependent, _current_depth + 1))
        results.extend(
            self.get_dependents_recursive(
                dependent, max_depth, _visited, _current_depth + 1
            )
        )

    return results

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

## 7. 감사(Audit) 연동

MeshCoordinator의 모든 조율 행위는 감사 로그에 기록한다:

| 이벤트 | action | 기록 내용 |
|--------|--------|-----------|
| 상류 임계치 조정 | `mesh_threshold_override` | upstream, downstream, depth, original/adjusted 값 |
| 오버라이드 해제 | `mesh_override_released` | upstream, downstream, duration |
| 오버라이드 TTL 만료 | `mesh_override_expired` | upstream, ttl_seconds |
| 오버라이드 TTL 갱신 | `mesh_override_renewed` | upstream, downstream, renewal_count |
| 에스컬레이션 | `mesh_escalation` | upstream, downstream, max_renewals_exceeded |
| Fast-recovery 적용 | `mesh_fast_recovery_applied` | upstream, downstream, timeout_seconds |
| 순차 복구 시작 | `mesh_recovery_started` | recovery_order, trigger |
| 순차 복구 완료 | `mesh_recovery_completed` | duration, services_recovered |
| 순환 참조 감지 | `mesh_circular_dependency` | service, dependent |

기존 `log_cb_state_change_audit()` 패턴을 따르되, `source="mesh_coordinator"`로 구분한다.

---

## 8. Prometheus 메트릭

```python
# 신규 메트릭 (기존 selfhealing_* 네임스페이스 준수)

selfhealing_mesh_overrides_active                     # Gauge: 현재 활성 오버라이드 수
selfhealing_mesh_override_applied_total               # Counter: 오버라이드 적용 횟수
selfhealing_mesh_override_released_total              # Counter: 오버라이드 해제 횟수
selfhealing_mesh_override_expired_total               # Counter: TTL 만료 횟수
selfhealing_mesh_override_renewed_total               # Counter: TTL 갱신 횟수
selfhealing_mesh_recovery_duration_seconds            # Histogram: 순차 복구 소요 시간
selfhealing_mesh_cascade_prevented_total              # Counter: 연쇄 OPEN 방지 횟수
selfhealing_mesh_preemptive_fallback_total            # Counter: 프리엠티브 Fallback 발동 횟수
selfhealing_mesh_fast_recovery_total                  # Counter: fast-recovery 적용 횟수
selfhealing_mesh_escalation_total                     # Counter: EmergencyCoordinator 에스컬레이션 횟수
selfhealing_mesh_circular_dependency_detected_total   # Counter: 순환 참조 감지 횟수
selfhealing_mesh_override_store_drift_total           # Counter: L1↔L2 drift 감지 횟수
```

---

## 9. 아키텍처 다이어그램

```
┌──────────────────────────────────────────────────────────────────────┐
│                         EventBus (기존)                               │
│  CIRCUIT_BREAKER 채널: OPENED / HALF_OPENED / CLOSED                 │
│  MESH_OVERRIDE 채널: L1 캐시 무효화 이벤트 (신규)                      │
└────────────┬───────────────────┬──────────────────┬─────────────────┘
             │ subscribe         │ subscribe         │ subscribe
             ▼                   ▼                   ▼
┌──────────────────────────┐  ┌───────────────┐  ┌─────────────────────┐
│   MeshCoordinator        │  │ _throttle_    │  │ TwoTierMeshOverride │
│   (신규 302)             │  │ handlers(기존)│  │ Store               │
│                          │  │ → LoadShedding│  │ .on_invalidation()  │
│  ┌────────────────────┐  │  └───────────────┘  │ → L1 dict 갱신      │
│  │ _downstream_open_  │  │                     └─────────┬───────────┘
│  │ set (로컬 인메모리) │  │                               │
│  │ O(1) lookup        │  │                     ┌─────────▼───────────┐
│  └────────┬───────────┘  │                     │ L1: dict (로컬)     │
│           │               │                     │ L2: Redis Hash      │
│  ┌────────▼───────────┐  │                     │ (Source of Truth)   │
│  │ 감쇠 전파 엔진      │  │                     └─────────────────────┘
│  │ (depth × damping)  │  │
│  └────────┬───────────┘  │    ┌──────────────────────────────────────┐
│           │               │    │ CircuitBreakerService                │
│  ┌────────▼───────────┐  │    │ .register_downstream_checker()       │
│  │ ThresholdOverride   │──┼──▶│ .apply_threshold_override()          │
│  │ 계산 + 적용         │  │    │ .get_effective_config() ← L1 조회    │
│  └────────┬───────────┘  │    │ .should_allow() ← pre-check hook     │
│           │               │    └──────────────────────────────────────┘
│  ┌────────▼───────────┐  │
│  │ Recovery Queue      │  │    ┌──────────────────────────────────────┐
│  │ (위상정렬 기반)     │  │◀───│ BlastRadiusIntegration               │
│  │ + Fast-Recovery     │  │    │ ._dependency_graph (읽기)            │
│  └────────┬───────────┘  │    │ .get_dependents_recursive()          │
│           │               │    └──────────────────────────────────────┘
│  ┌────────▼───────────┐  │
│  │ TTL Heartbeat       │  │    ┌──────────────────────────────────────┐
│  │ (Celery beat task)  │──┼──▶│ EmergencyCoordinator                 │
│  │ max_renewals 초과   │  │    │ (에스컬레이션)                        │
│  └────────────────────┘  │    └──────────────────────────────────────┘
└──────────────────────────┘
```

---

## 10. 분산 환경 레이스 컨디션 분석

### 10.1 단일 인스턴스 (레이스 컨디션 없음)

CB 상태 변경 시 실행 순서:
1. Repository 상태 변경 (atomic)
2. **동기 콜백** 즉시 실행 (`_invoke_state_change_callbacks`, `service.py:156`)
3. EventBus emit (로컬 버스도 동기 실행)

MeshCoordinator의 핸들러는 2~3단계에서 **동일 스레드 내에서 즉시** 실행되므로,
C가 OPEN → B의 `_downstream_open_set` 갱신이 원자적으로 완료된다.

### 10.2 멀티 인스턴스 (Eventual Consistency)

인스턴스 1에서 C OPEN → Redis Pub/Sub → 인스턴스 2의 B 갱신까지
네트워크 딜레이(수 ms~수십 ms)가 존재한다.

이 구간에서 인스턴스 2의 B가 C로 요청을 보내 실패할 수 있지만:
- **수동적 보호**: 임계치 상향으로 B가 OPEN되는 것을 지연
- B가 이 구간에서 자체적으로 OPEN되더라도, recovery_timeout이 연장되어 있으므로
  C 복구 전까지 OPEN 상태를 안정적으로 유지
- C 복구 후 fast-recovery로 B가 빠르게 정상화

**상태 강제 변경은 수행하지 않는다.** CB가 OPEN된 것은 정당한 이유가 있으며,
강제 CLOSED/HALF_OPEN 전이는 이미 실패 중인 경로에 트래픽을 재투입하는 위험이 있다.

---

## 11. 구현 우선순위

| 순서 | 항목 | 예상 변경 |
|------|------|-----------|
| Q1 | DependencyGraph에 `get_dependents_recursive()`, `topological_sort_subset()` 추가 | blast_radius_integration.py 수정 |
| Q2 | `MeshOverrideStore` Protocol + `TwoTierMeshOverrideStore` 구현 | circuit_mesh/store.py 신규 |
| Q3 | CircuitBreakerService에 threshold override + downstream pre-check hook 추가 | service.py 수정 |
| Q4 | MeshCoordinator 구현 (프리엠티브 Fallback + 감쇠 전파 + Fast-Recovery + TTL Heartbeat) | circuit_mesh/mesh_coordinator.py 신규 |
| Q5 | Settings + 감사 로그 + 메트릭 연동 | settings/, audit, metrics 수정 |
| Q6 | ProviderRegistry 등록 + EventBus 핸들러 + Celery beat task 등록 | factory.py, beat schedule 수정 |
| Q7 | 통합 테스트 | → 303 문서 범위 |

---

*문서 버전: 2.0*
*작성일: 2026-03-06*
*최종 업데이트: 2026-03-06*

### 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 1.0 | 2026-03-06 | 초안 작성 |
| 2.0 | 2026-03-06 | 설계 리뷰 반영: 프리엠티브 Fallback 메커니즘 구체화 (`_downstream_open_set` + `should_allow()` pre-check hook), 감쇠 전파(Damped Propagation) 도입 (`propagation_max_depth` + `damping_factor`), 순환 참조 방어(visited tracking + 메트릭), Two-Tier Cache 저장소(`MeshOverrideStore` + `TwoTierMeshOverrideStore`), Fast-Recovery(`on_downstream_closed`에서 recovery_timeout 즉시 단축), TTL Heartbeat 갱신(`max_renewals` + EmergencyCoordinator 에스컬레이션, Celery beat 스케줄러 명시), 분산 환경 기본 가정, 레이스 컨디션 분석 추가 |
