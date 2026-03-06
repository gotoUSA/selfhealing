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
    """Audit Circuit Breaker 공통 상태 머신"""

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
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState: ...

    def can_execute(self) -> bool:
        # 공통: CLOSED→True, OPEN→timeout 체크, HALF_OPEN→True
        ...

    def record_success(self) -> None:
        # 공통: HALF_OPEN→threshold 도달 시 _on_close() 호출
        ...

    def record_failure(self) -> None:
        # 공통: threshold 도달 시 _on_open() 호출
        ...

    def _transition_to(self, new_state: CircuitState) -> None: ...
    def force_open(self) -> None: ...
    def reset(self) -> None: ...
    def get_stats(self) -> dict: ...

    # 서브클래스 훅
    @abstractmethod
    def _get_elapsed_seconds(self) -> float:
        """시간 추적 방식 (datetime vs monotonic)"""

    def _on_open(self) -> None:
        """OPEN 전환 시 콜백 (서브클래스 오버라이드)"""

    def _on_close(self) -> None:
        """CLOSED 전환 시 콜백 (서브클래스 오버라이드)"""

    def _can_attempt_half_open(self) -> bool:
        """HALF_OPEN에서 요청 허용 여부 (서브클래스 오버라이드)"""
        return True  # 기본: 무제한
```

### Phase 2: 기존 CB를 Base에서 상속

```python
# resilience/circuit_breaker.py
class CircuitBreaker(CircuitBreakerBase):
    """외부 감사 백엔드용 CB"""
    def _get_elapsed_seconds(self) -> float:
        return (datetime.now(UTC) - self._last_failure_time).total_seconds()

# graceful_degradation/circuit_breaker.py
class HashChainCircuitBreaker(CircuitBreakerBase):
    """Redis 해시체인용 CB"""
    def __init__(self, config, degradation_manager=None):
        super().__init__(config.failure_threshold, ...)
        self._degradation_manager = degradation_manager
        self._half_open_requests = config.half_open_requests

    def _get_elapsed_seconds(self) -> float:
        return time.monotonic() - self._last_failure_time

    def _on_open(self) -> None:
        if self._degradation_manager:
            self._degradation_manager.on_redis_failure(...)

    def _on_close(self) -> None:
        if self._degradation_manager:
            self._degradation_manager.on_redis_recovery()

    def _can_attempt_half_open(self) -> bool:
        return self._half_open_attempts < self._half_open_requests
```

---

## 3. 리팩터링 계획 — Degradation Manager

### Phase 1: 상태 브로드캐스트 인터페이스

3개의 독립 Degradation Manager 간 상태 동기화를 위한 경량 인터페이스 추가:

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
    """Degradation 상태 변경 브로드캐스터"""
    _observers: list[DegradationObserver] = []

    @classmethod
    def register(cls, observer: DegradationObserver) -> None: ...

    @classmethod
    def notify(cls, source, is_degraded, level, reason) -> None: ...
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

---

## 5. 검증 기준

### Circuit Breaker
- [ ] `CircuitBreakerBase` 추출 및 단위 테스트
- [ ] `CircuitBreaker`가 Base에서 상속, 기존 테스트 통과
- [ ] `HashChainCircuitBreaker`가 Base에서 상속, 기존 테스트 통과
- [ ] 중복 코드 ~200줄 제거 확인

### Degradation Manager
- [ ] `DegradationBroadcaster` 구현 및 단위 테스트
- [ ] `DegradedModeManager`가 상태 변경 시 브로드캐스트
- [ ] `HashChainDegradationManager`가 상태 변경 시 브로드캐스트
- [ ] `DegradationStatus.get_unified_status()` 통합 조회 동작 확인
- [ ] API 엔드포인트 (`audit/api.py`)에서 통합 상태 노출

---

## 6. 절감 효과

| 항목 | Before | After |
|------|--------|-------|
| CB 상태 머신 구현 | 2곳 (각 ~150줄) | 1곳 (`CircuitBreakerBase`) + 2 서브클래스 (~50줄씩) |
| Degradation 상태 추적 | 3곳 독립 | 3곳 + 브로드캐스터 (상호 통신) |
| 상태 불일치 리스크 | 높음 | 해소 (통합 상태 조회) |
| **CB 코드 절감** | | **~200줄** |
| **Degradation 안전성** | | **상태 불일치 버그 방지** |
