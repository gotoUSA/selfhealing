# 307. Audit Circuit Breaker + Degradation Manager 통합 — 상태 머신 중복 제거

> **Status**: Refactor
> **Severity**: P2 (MEDIUM)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/audit/resilience/circuit_breaker.py` — CircuitBreaker, CircuitBreakerRegistry
> - `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/circuit_breaker.py` — HashChainCircuitBreaker
> - `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/enums.py` — CircuitState, HashChainCircuitBreakerConfig
> - `packages/selfhealing-python/src/selfhealing/audit/resilience/degraded_mode.py` — DegradedModeManager
> - `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/degradation_manager.py` — HashChainDegradationManager
> - `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/manager.py` — HashChainGracefulDegradationManager
> **References**:
> - `services/circuit_breaker/service.py` — CircuitBreakerService (정규 서비스, 통합 대상 아님)

---

## 1. 현황 및 문제

### 1.1 Circuit Breaker — 82.5% 코드 중복

Audit 모듈 내에 동일한 CLOSED→OPEN→HALF_OPEN 상태 머신이 2곳에서 독립 구현되어 있다.

| 측면 | `CircuitBreaker` (resilience) | `HashChainCircuitBreaker` (graceful_deg) |
|------|------------------------------|----------------------------------------|
| 용도 | 외부 감사 백엔드 보호 | Redis 해시체인 보호 |
| 파일 | `resilience/circuit_breaker.py` | `graceful_degradation/circuit_breaker.py` |
| 시간 추적 | `datetime.now(UTC)` | `time.monotonic()` |
| Half-Open 제한 | 무제한 | `half_open_requests=3` |
| 콜백 | 없음 | `on_redis_failure()`/`on_redis_recovery()` |
| 기본 failure_threshold | 3 | 5 |
| 상태 열거형 | `CircuitState` (import) | `CircuitState` (동일, enums.py에서 공유) |

### 1.2 메서드 단위 중복

| 메서드 | Resilience CB | GracefulDeg CB | 중복률 |
|--------|---------------|----------------|--------|
| `__init__()` | :71-86 | :55-85 | 95% |
| `state` property | :88-93 | :87-92 | 100% |
| `can_execute()` | :95-111 | :94-115 | 85% |
| `record_success()` | :113-124 | :117-127 | 90% |
| `record_failure()` | :126-138 | :129-144 | 80% |
| timeout 체크 | `_check_timeout()` :140 | `_maybe_transition_to_half_open()` :146 | 75% |
| `force_open()` | :176-183 | :190-193 | 95% |
| `get_stats()` | :185-203 | :200-218 | 90% |
| **전체 중복률** | | | **82.5%** |

### 1.3 Degradation Manager — 3중 독립 추적

| 구현체 | 파일 | 상태 모델 | 탐지 방식 | 상호 통신 |
|--------|------|----------|----------|----------|
| `DegradedModeManager` | `resilience/degraded_mode.py` :22 | Binary (bool) | CB Registry 폴링 | 없음 |
| `HashChainDegradationManager` | `graceful_degradation/degradation_manager.py` :28 | 4단계 (NORMAL→DEGRADED→EMERGENCY→READONLY) | 명시적 failure count | 없음 |
| `HashChainGracefulDegradationManager` | `graceful_degradation/manager.py` :30 | 래퍼 (위 2번 위임) | CB + degradation 통합 | 부분적 |

**핵심 문제**: Redis는 EMERGENCY인데 외부 백엔드는 NORMAL인 불일치 상태 발생 가능. 세 시스템이 독립적으로 상태를 추적하며 상호 통신하지 않음.

---

## 2. 리팩터링 계획 — Circuit Breaker

### Phase 1: CircuitBreakerBase 추출

`audit/resilience/circuit_breaker.py`에 base class 추가:

```python
class CircuitBreakerBase(ABC):
    """Audit Circuit Breaker 공통 상태 머신.

    설계 원칙:
    - 상태 전이 로직(_*_impl)과 동시성 제어(lock)를 분리하여
      향후 async 서브클래스 확장 시 로직 재구현 없이 락만 교체 가능
    - 타임아웃 계산은 time.monotonic() 통일 (NTP/윤초 면역)
    - datetime은 순수 관측/로깅/stats용으로만 사용
    """

    def __init__(
        self,
        failure_threshold: int,
        success_threshold: int,
        timeout_seconds: float,
    ):
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        # DR-1: Monotonic time — duration 계산용 (NTP/윤초 면역)
        self._last_failure_mono: float = 0.0
        # 관측/로깅용 절대 시각 (stats API, 로그 출력에만 사용)
        self._last_failure_time: datetime | None = None

    @property
    def state(self) -> CircuitState: ...

    # --- 상태 전이 로직 (락 없는 순수 로직) ---

    def _can_execute_impl(self) -> bool:
        # 공통: CLOSED→True, OPEN→timeout 체크, HALF_OPEN→_can_attempt_half_open()
        ...

    def _record_success_impl(self) -> None:
        # 공통: HALF_OPEN→threshold 도달 시 _on_close() 호출
        ...

    def _record_failure_impl(self) -> None:
        # 공통: threshold 도달 시 _on_open() 호출
        ...

    # --- 서브클래스 필수 구현: 동시성 제어 래핑 ---

    @abstractmethod
    def can_execute(self) -> bool:
        """서브클래스가 적절한 락으로 _can_execute_impl()을 감싸서 호출"""
        ...

    @abstractmethod
    def record_success(self) -> None:
        """서브클래스가 적절한 락으로 _record_success_impl()을 감싸서 호출"""
        ...

    @abstractmethod
    def record_failure(self) -> None:
        """서브클래스가 적절한 락으로 _record_failure_impl()을 감싸서 호출"""
        ...

    # --- 공통 내부 메서드 ---

    def _get_elapsed_seconds(self) -> float:
        """DR-1: 구체 메서드 — time.monotonic() 통일, 추상 아님"""
        return time.monotonic() - self._last_failure_mono

    def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        # DR-6: 상태 전환 시 Observability 훅 호출
        self._on_state_changed(old_state, new_state)

    def force_open(self) -> None: ...
    def reset(self) -> None: ...
    def get_stats(self) -> dict: ...

    # --- 서브클래스 훅 (선택적 오버라이드) ---

    def _on_open(self) -> None:
        """OPEN 전환 시 콜백 (서브클래스 오버라이드)"""

    def _on_close(self) -> None:
        """CLOSED 전환 시 콜백 (서브클래스 오버라이드)"""

    def _can_attempt_half_open(self) -> bool:
        """HALF_OPEN에서 요청 허용 여부 (서브클래스 오버라이드)"""
        return True  # 기본: 무제한

    def _on_state_changed(self, old: CircuitState, new: CircuitState) -> None:
        """DR-6: 상태 전환 시 메트릭 업데이트 훅 (서브클래스 오버라이드).
        기본 구현은 no-op. 서브클래스에서 AuditMetrics 연동."""
```

### Phase 2: 기존 CB를 Base에서 상속

```python
# resilience/circuit_breaker.py
class CircuitBreaker(CircuitBreakerBase):
    """외부 감사 백엔드용 CB (Sync)"""
    def __init__(self, config: AuditCircuitBreakerConfig):
        super().__init__(config.failure_threshold, ...)
        self._lock = threading.RLock()  # DR-5: Sync 전용 락

    # DR-5: 동시성 제어 — threading.RLock으로 래핑
    def can_execute(self) -> bool:
        with self._lock:
            return self._can_execute_impl()

    def record_success(self) -> None:
        with self._lock:
            self._record_success_impl()

    def record_failure(self) -> None:
        with self._lock:
            self._record_failure_impl()

    # DR-6: Observability 훅 — AuditMetrics 연동
    def _on_state_changed(self, old: CircuitState, new: CircuitState) -> None:
        AuditMetrics.record_cb_transition(self._name, old, new)
        AuditMetrics.set_cb_state(self._name, new)

# graceful_degradation/circuit_breaker.py
class HashChainCircuitBreaker(CircuitBreakerBase):
    """Redis 해시체인용 CB (Sync)"""
    def __init__(self, config, degradation_manager=None):
        super().__init__(config.failure_threshold, ...)
        self._lock = threading.RLock()  # DR-5: Sync 전용 락
        self._degradation_manager = degradation_manager
        self._half_open_requests = config.half_open_requests

    # DR-5: 동시성 제어 — threading.RLock으로 래핑
    def can_execute(self) -> bool:
        with self._lock:
            return self._can_execute_impl()

    def record_success(self) -> None:
        with self._lock:
            self._record_success_impl()

    def record_failure(self) -> None:
        with self._lock:
            self._record_failure_impl()

    def _on_open(self) -> None:
        if self._degradation_manager:
            self._degradation_manager.on_redis_failure(...)

    def _on_close(self) -> None:
        if self._degradation_manager:
            self._degradation_manager.on_redis_recovery()

    def _can_attempt_half_open(self) -> bool:
        return self._half_open_attempts < self._half_open_requests

    # DR-6: Observability 훅
    def _on_state_changed(self, old: CircuitState, new: CircuitState) -> None:
        AuditMetrics.record_cb_transition("redis_hashchain", old, new)
        AuditMetrics.set_cb_state("redis_hashchain", new)
```

---

## 3. 리팩터링 계획 — Degradation Manager

### Phase 1: 상태 브로드캐스트 인터페이스

3개의 독립 Degradation Manager 간 상태 동기화를 위한 경량 인터페이스 추가.

> **DR-3 (브로드캐스트 목적)**: 1차 범위에서 브로드캐스트의 목적은 **상태 통지 + 통합 조회(Read-only)**에 한정한다.
> 연쇄적 상태 변경(Cascading Action)은 포함하지 않는다. 향후 필요 시 Policy Engine을
> Observer로 추가 등록하는 것만으로 확장 가능하므로, 현재 설계의 확장성은 확보되어 있다.

```python
# audit/resilience/degradation_protocol.py
class DegradationObserver(Protocol):
    def on_degradation_changed(
        self,
        source: str,          # "redis_hashchain" | "external_backends"
        is_degraded: bool,
        level: str | None,    # "DEGRADED" | "EMERGENCY" | "READONLY" | None
        reason: str,
    ) -> None: ...

class DegradationBroadcaster:
    """Degradation 상태 변경 브로드캐스터.

    DR-2 (예외 격리): notify() 루프 내부에서 각 Observer의 예외를
    try-except로 격리하여, 한 Observer의 실패가 호출자의 실행 흐름을
    중단시키지 않도록 한다 (Fail-safe Observer 패턴).
    기존 HashChainDegradationManager.set_level()의 콜백 예외 격리 패턴과 동일.
    """
    _observers: list[DegradationObserver] = []

    @classmethod
    def register(cls, observer: DegradationObserver) -> None: ...

    @classmethod
    def notify(cls, source, is_degraded, level, reason) -> None:
        for observer in cls._observers:
            try:
                observer.on_degradation_changed(source, is_degraded, level, reason)
            except Exception:
                logger.warning(
                    "degradation_observer_notify_failed",
                    observer=type(observer).__name__,
                    source=source,
                    exc_info=True,
                )
```

### Phase 2: 기존 Manager에 브로드캐스터 연결

```
DegradedModeManager.enter_degraded_mode():
  + DegradationBroadcaster.notify("external_backends", True, None, reason)

DegradedModeManager.exit_degraded_mode():
  + DegradationBroadcaster.notify("external_backends", False, None, "recovered")

HashChainDegradationManager.set_level():
  + DegradationBroadcaster.notify("redis_hashchain", level != NORMAL, level, reason)
```

### Phase 3: 통합 상태 조회

```python
# audit/resilience/degradation_protocol.py
class DegradationStatus:
    @classmethod
    def get_unified_status(cls) -> dict:
        """모든 Degradation Manager의 통합 상태"""
        return {
            "external_backends": DegradedModeManager.get_instance().get_status(),
            "redis_hashchain": HashChainDegradationManager.get_instance().get_status(),
            "overall_degraded": any_degraded,
            "worst_level": worst_level,
        }
```

---

## 4. 변경하지 않는 것

- **`CircuitBreakerService` (services/circuit_breaker/)**: 60+ 서비스가 의존하는 정규 서비스 CB. audit 내부 CB와 아키텍처적으로 분리가 올바름 (Repository 패턴 + EventBus 연동 등 audit CB에 불필요한 기능).
- **DegradedModeManager와 HashChainDegradationManager의 분리**: 도메인이 다름 (외부 백엔드 vs Redis). 통합이 아닌 **상호 통신**으로 해결.
- **HashChainGracefulDegradationManager 유지 (DR-4)**: 이 클래스는 상태 래퍼가 아닌 **Facade/Orchestrator**로서, 컴포넌트 초기화 순서 조율, CB 상태에 따른 폴백 체인 실행, 시작 시 WAL 복구 등 비즈니스 오케스트레이션을 담당한다. 브로드캐스터는 "상태 통신"을, 이 클래스는 "비즈니스 오케스트레이션"을 해결하므로 책임이 다르다. 삭제하지 않는다.

---

## 5. 검증 기준

### Circuit Breaker
- [ ] `CircuitBreakerBase` 추출 및 단위 테스트
- [ ] `CircuitBreaker`가 Base에서 상속, 기존 테스트 통과
- [ ] `HashChainCircuitBreaker`가 Base에서 상속, 기존 테스트 통과
- [ ] 중복 코드 ~200줄 제거 확인
- [ ] DR-1: `_get_elapsed_seconds()`가 `time.monotonic()` 기반 구체 메서드로 구현됨
- [ ] DR-1: `_last_failure_mono` (monotonic) + `_last_failure_time` (UTC) 이중 기록 동작
- [ ] DR-5: `_*_impl()` 메서드가 락 없는 순수 로직으로 분리됨
- [ ] DR-5: 서브클래스에서 `threading.RLock`으로 `_impl` 메서드를 래핑
- [ ] DR-6: `_on_state_changed()` 훅이 `_transition_to()` 내부에서 호출됨
- [ ] DR-6: 각 서브클래스에서 `AuditMetrics` 연동 구현

### Degradation Manager
- [ ] `DegradationBroadcaster` 구현 및 단위 테스트
- [ ] `DegradedModeManager`가 상태 변경 시 브로드캐스트
- [ ] `HashChainDegradationManager`가 상태 변경 시 브로드캐스트
- [ ] `DegradationStatus.get_unified_status()` 통합 조회 동작 확인
- [ ] API 엔드포인트 (`audit/api.py`)에서 통합 상태 노출
- [ ] DR-2: `DegradationBroadcaster.notify()` 내부 Observer 예외 격리 (`try-except` + `exc_info=True`)
- [ ] DR-3: 브로드캐스트 수신자가 연쇄 상태 변경을 일으키지 않음 (Read-only 통지 확인)
- [ ] DR-4: `HashChainGracefulDegradationManager`가 Facade로서 유지되고 삭제되지 않음

---

## 6. 절감 효과

| 항목 | Before | After |
|------|--------|-------|
| CB 상태 머신 구현 | 2곳 (각 ~150줄) | 1곳 (`CircuitBreakerBase`) + 2 서브클래스 (~50줄씩) |
| Degradation 상태 추적 | 3곳 독립 | 3곳 + 브로드캐스터 (상호 통신) |
| 상태 불일치 리스크 | 높음 | 해소 (통합 상태 조회) |
| **CB 코드 절감** | | **~200줄** |
| **Degradation 안전성** | | **상태 불일치 버그 방지** |

---

## 7. 설계 결정 기록 (Design Records)

리팩터링 설계 리뷰에서 확정된 6가지 결정 사항.

### DR-1: 시간 측정 방식 통일 — `time.monotonic()`

| 항목 | 결정 |
|------|------|
| **문제** | 기존 두 CB가 `datetime.now(UTC)`와 `time.monotonic()`을 혼용. `datetime`은 NTP 동기화·윤초 시 시간 역전으로 CB 오작동(Flapping) 위험 |
| **결정** | `CircuitBreakerBase._get_elapsed_seconds()`를 **구체 메서드**로 구현하여 `time.monotonic()` 통일. 추상 메서드로 두지 않음 |
| **구현** | `_last_failure_mono: float` (monotonic, duration 계산용) + `_last_failure_time: datetime \| None` (UTC, 로깅/stats용) 이중 기록 |
| **근거** | 두 CB 모두 "마지막 실패 이후 경과 시간"이라는 상대적 duration만 필요. 크로스 프로세스 비교 불필요. Netflix Hystrix, Resilience4j 모두 monotonic 기반 |

### DR-2: 브로드캐스터 예외 격리 — Fail-safe Observer

| 항목 | 결정 |
|------|------|
| **문제** | `DegradationBroadcaster.notify()`에서 한 Observer의 예외가 발신자(Degradation Manager)의 메인 흐름을 중단시킬 위험 |
| **결정** | `notify()` 루프 내부에 `try-except` 블록을 두어 각 Observer의 예외를 격리하고 `exc_info=True`로 트레이스를 로깅 |
| **근거** | 기존 `HashChainDegradationManager.set_level()`이 동일한 콜백 예외 격리 패턴을 사용 중. 프로젝트 내 검증된 패턴과의 일관성 확보 |

### DR-3: 브로드캐스트 목적 — 단순 통지 + 통합 조회

| 항목 | 결정 |
|------|------|
| **문제** | 브로드캐스트 수신자가 연쇄적 상태 변경(Cascading Action)을 일으켜야 하는지 불명확 |
| **결정** | 1차 범위에서는 **상태 통지 + 통합 조회(Read-only)**에 한정. 연쇄 Degradation은 포함하지 않음 |
| **확장성** | Observer 패턴이므로, 향후 Policy Engine을 Observer로 추가 등록하는 것만으로 Cascading Action 확장 가능 |
| **근거** | 현재 코드에서 `DegradedModeManager`와 `HashChainDegradationManager` 간 연쇄 작용 없음. 연쇄 정책은 별도 설계 문서 범위 |

### DR-4: HashChainGracefulDegradationManager 유지

| 항목 | 결정 |
|------|------|
| **문제** | 브로드캐스터와 `get_unified_status()` 도입 후 래퍼 클래스의 존재 의의 |
| **결정** | **삭제하지 않고 유지**. 이 클래스는 상태 래퍼가 아닌 Facade/Orchestrator |
| **역할** | (1) 5개 컴포넌트의 초기화 순서 조율 (2) CB 상태에 따른 폴백 체인 실행 (3) 시작 시 WAL 복구 + 미조정 항목 체크 |
| **근거** | 브로드캐스터는 "상태 통신", 이 클래스는 "비즈니스 오케스트레이션" — 책임이 다름 |

### DR-5: 동시성 모델 — Sync-first, Async-opt-in

| 항목 | 결정 |
|------|------|
| **문제** | `threading.RLock`만으로 충분한지, asyncio 환경 지원이 필요한지 |
| **결정** | `CircuitBreakerBase`에서 **상태 전이 로직(`_*_impl`)과 동시성 제어(`lock`)를 분리**. 307 범위에서는 Sync(`threading.RLock`) 서브클래스만 구현 |
| **설계** | `_can_execute_impl()`, `_record_success_impl()`, `_record_failure_impl()` — 락 없는 순수 로직 메서드를 Base에 정의. 서브클래스가 `can_execute()`, `record_success()`, `record_failure()`에서 적절한 락으로 감싸서 호출 |
| **향후 확장** | Async 서브클래스(`AsyncCircuitBreaker`)는 `asyncio.Lock`으로 동일한 `_impl` 메서드를 감싸면 되므로 로직 재구현 불필요 (별도 문서로 분리) |
| **근거** | 프로젝트 내 Hedging(`AsyncHedgingExecutor`), Bulkhead(`AsyncSemaphoreBulkhead`)가 이미 Dual-Mode 패턴 채택. 인터페이스 레이어에 `AsyncResiliencePolicy` Protocol 존재. 업계 표준(Resilience4j, Polly, tenacity, httpx)도 동일 전략 |
| **타깃 사용자** | 유니콘/스케일업 + 중견 엔터프라이즈 (Django/FastAPI 기반). Sync 60-70% + Async(FastAPI/ASGI) 30-40% 혼재 환경 |

### DR-6: Observability 훅 — `_on_state_changed()`

| 항목 | 결정 |
|------|------|
| **문제** | CB 상태 전환 시 Prometheus 메트릭 발행이 누락되어 있음 (`DegradedModeManager`만 `AuditMetrics` 연동 중) |
| **결정** | `CircuitBreakerBase._transition_to()` 내부에서 `_on_state_changed(old, new)` 훅을 호출. 기본 구현은 no-op, 서브클래스에서 `AuditMetrics` 연동 |
| **범위 제한** | `DegradationBroadcaster.notify()` 내부에는 메트릭을 넣지 않음 — 브로드캐스터는 전파만 담당(SRP). 메트릭은 각 Manager가 자체 발행 |
| **근거** | `DegradedModeManager`가 이미 `AuditMetrics.set_degraded_mode(bool)` 호출하는 패턴 사용 중. CB도 동일 패턴을 따라 일관성 확보 |
