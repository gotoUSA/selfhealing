# 232. ThrottlePolicy 전환 설계

## 1. 개요

AdaptiveThrottle은 224 마스터 플랜에서 누락되었으나, **RetryHandler(12건)에 필적하는 13건의 하드코딩 크로스-패턴 의존성**을 가진 전환 대상이다.

`check()` 메서드가 요청 실행 전에 호출되어 허용/거부를 결정하는 전형적인 Resilience Policy 패턴이며, 내부에 Emergency Mode, Circuit Breaker, Error Budget, Kill Switch, Load Shedding, DLQ 등 6개 이상의 외부 시스템이 하드코딩되어 소비자가 조합을 변경할 수 없다.

**핵심 문제**: PolicyComposer에서 `compose(throttle(), circuit_breaker(), retry())`를 구성할 경우, AdaptiveThrottle 내부에서도 CB를 참조하므로 **이중 CB 체크**가 발생한다. ThrottlePolicy로 전환하여 내부 CB 의존성을 분리해야만 이 문제가 해결된다.

## 2. 현재 구현 분석

### 2.1 파일 구조

```
services/throttle/
├── adaptive.py            # 핵심 AdaptiveThrottle — 2,549줄, 하드코딩 13건
├── base.py                # SlidingWindowThrottle (독립, 표준 라이브러리)  — 249줄
├── config.py              # ThrottleConfig, ThrottleResult 정의          — 60줄
├── adaptive_dlq_replay.py # DLQ Replay Mixin — 452줄
├── cb_bridge.py           # Throttle↔CB 데이터 공유 브릿지           — 410줄
├── recovery_dampening.py  # Recovery Dampening 상태 머신              — 334줄
├── registry.py            # 서비스별 Throttle 인스턴스 관리           — 352줄
├── dlq_integration.py     # Throttle DLQ 연계                        — 273줄
├── safe_open_fallback.py  # Redis 장애 시 Safe-Open 전략             — 360줄
├── audit.py               # Throttle 감사 로깅                        — 별도
├── postmortem.py          # Limit 변경 이력 기록                      — 별도
└── __init__.py            # 패키지 퍼사드                            — 134줄
                                                            합계: 약 6,151줄
```

### 2.2 클래스 상속 체인

```python
# adaptive.py L503
class AdaptiveThrottle(GovernanceCheckMixin, ThrottleDLQReplayMixin, SlidingWindowThrottle):
```

| 부모 클래스 | 위치 | 역할 | Policy 전환 시 처리 |
|------------|------|------|-------------------|
| `SlidingWindowThrottle` | `throttle/base.py` | 순수 rate limit 로직 (독립) | ThrottlePolicy 내부 엔진으로 유지 |
| `GovernanceCheckMixin` | `governance/checks.py` | Kill Switch/Emergency/ErrorBudget/BreakGlass 체크 | Guard 인터페이스로 분리 |
| `ThrottleDLQReplayMixin` | `throttle/adaptive_dlq_replay.py` | 거부 시 DLQ 저장 + Recovery Replay | Sink 인터페이스로 분리 |

### 2.3 AdaptiveThrottle.check() 실행 흐름 (고정 순서)

```
adaptive.py L1566: Break Glass + Full Stop → Full Stop 해제 판단
    ↓
adaptive.py L1570: _sync_governance_state()  ← Emergency + Kill Switch + Break Glass, 30초 TTL 캐시
    │  ├ L2202: EmergencyMode.get_current_level()       ← lazy import
    │  ├ L2238: governance.checks.is_system_enabled()   ← lazy import
    │  └ L831:  GovernanceSettings.break_glass_enabled   ← lazy import
    ↓
adaptive.py L1574: advance_recovery_dampening()  ← 내부 상태 머신 (80%→90%→100%)
    ↓
adaptive.py L1577: Error Budget Critical 상태 → non_essential 티어 거부
    │  └ self._error_budget_limit_reduction_active (EventBus 구독으로 설정됨)
    ↓
adaptive.py L1607: 429 감소 상태 → CRITICAL 티어 보호 (pre-429 limit 사용)
    │  └ self._429_reduction_active (EventBus 구독으로 설정됨)
    ↓
adaptive.py L1650: Load Shedding 대상 서비스 → 제한적 limit 적용
    │  └ self._shedding_affected_services (EventBus 구독으로 설정됨)
    ↓
adaptive.py L1661: super().check(key)  ← SlidingWindowThrottle.check() — 순수 rate limit
    ↓
adaptive.py L1688: 거부 시 DLQ 자동 저장 ← ThrottleDLQReplayMixin
```

### 2.4 __init__() EventBus 구독 (4건 — 외부 시스템 이벤트에 반응)

```
adaptive.py L627: _subscribe_rate_limit_events()
    └ L634: EventBus.subscribe(RATE_LIMIT_429, ...)             ← 429 시 limit 감소
    └ L634: EventBus.subscribe(RATE_LIMIT_COOLDOWN_END, ...)    ← Recovery Dampening 시작

adaptive.py L628: _subscribe_error_budget_events()
    └ L910: EventBus.subscribe(ERROR_BUDGET_WARNING, ...)       ← limit 20% 감소
    └ L910: EventBus.subscribe(ERROR_BUDGET_CRITICAL, ...)      ← limit 50% 감소
    └ L910: EventBus.subscribe(ERROR_BUDGET_RECOVERED, ...)     ← Recovery Dampening 시작

adaptive.py L629: _subscribe_load_shedding_events()
    └ L846: EventBus.subscribe(LOAD_SHEDDING_LEVEL_CHANGED, ...) ← Shedding limit 조정

adaptive.py L630: _subscribe_kill_switch_events()
    └ L782: EventBus.subscribe(KILL_SWITCH_ACTIVATED, ...)      ← Gradient Freeze
    └ L782: EventBus.subscribe(KILL_SWITCH_DEACTIVATED, ...)    ← Recovery 시작
```

### 2.5 하드코딩 크로스-패턴 의존성 — 13건 상세

#### Guard로 분리 (4건)

| # | 의존 대상 | 현재 위치 | import 방식 | 분리 후 |
|---|-----------|-----------|-------------|---------|
| 1 | `GovernanceCheckMixin.is_automation_allowed()` (Kill Switch + Emergency + ErrorBudget + BreakGlass 통합 체크) | adaptive.py L38 정적 import, L1345 `_maybe_adjust_limit()` 내 호출 | 클래스 상속 | `GovernanceGuard.check()` |
| 2 | `EmergencyMode.get_current_level()` | adaptive.py L2202 lazy import | `_sync_governance_state()` 내 30초 TTL 체크 | `EmergencyGuard.check()` |
| 3 | `governance.checks.is_system_enabled()` | adaptive.py L2238 lazy import | `_sync_kill_switch_state()` 내 Drift 교정 | `KillSwitchGuard.check()` |
| 4 | `GovernanceSettings.break_glass_enabled` | adaptive.py L831 lazy import | `_sync_break_glass_state()` 내 polling | `BreakGlassGuard.check()` |

#### EventBus 구독으로 상태 변경 (5건) — Hook/Observer로 분리

| # | 의존 대상 | 현재 위치 | 트리거 | 분리 후 |
|---|-----------|-----------|--------|---------|
| 5 | `RATE_LIMIT_429` 이벤트 → limit 감소 | adaptive.py L634, L651 `_handle_rate_limit_429()` | EventBus 구독 | `RateLimitHook` — 외부에서 limit 조정 콜백 주입 |
| 6 | `ERROR_BUDGET_WARNING/CRITICAL` → limit 감소 | adaptive.py L910, L970 `_handle_error_budget_warning()` | EventBus 구독 | `ErrorBudgetHook` — 외부에서 limit 조정 콜백 주입 |
| 7 | `LOAD_SHEDDING_LEVEL_CHANGED` → limit 조정 | adaptive.py L846, L862 `_handle_shedding_changed()` | EventBus 구독 | `LoadSheddingHook` — 외부에서 limit 조정 콜백 주입 |
| 8 | `KILL_SWITCH_ACTIVATED/DEACTIVATED` → Gradient Freeze | adaptive.py L782, L793 `_handle_kill_switch_activated()` | EventBus 구독 | Guard의 실시간 알림 채널 |
| 9 | `CircuitBreakerService.get_state()` (Full Stop 3중 조건) | adaptive.py L2007 lazy import | `_check_db_circuit_breaker_open()` | Policy 외부에서 Full Stop 판단 |

#### Sink로 분리 (1건)

| # | 의존 대상 | 현재 위치 | import 방식 | 분리 후 |
|---|-----------|-----------|-------------|---------|
| 10 | `ThrottleDLQReplayMixin` (DLQ 저장 + Recovery Replay) | adaptive.py L39 정적 import, L1688 `_auto_store_rejection_to_dlq()` | 클래스 상속 | `FailureSink.handle_rejection()` |

#### Hook으로 분리 (2건)

| # | 의존 대상 | 현재 위치 | import 방식 | 분리 후 |
|---|-----------|-----------|-------------|---------|
| 11 | `Prometheus Metrics` (25+ 메트릭) | adaptive.py L133 lazy import | `_record_throttle_metrics()` | `PolicyHook.on_check()` / `.on_reject()` |
| 12 | `Audit` (감사 로깅) | adaptive.py L64 lazy import | `_record_audit_safe()` | `PolicyHook.on_check()` / `.on_reject()` |

#### 기타 (1건)

| # | 의존 대상 | 현재 위치 | import 방식 | 분리 후 |
|---|-----------|-----------|-------------|---------|
| 13 | `ErrorBudgetService + BudgetDepletionForecaster` (선제적 Burn Rate 보호) | adaptive.py L1155-L1156 lazy import | `_check_preemptive_protection()` | Guard 또는 외부 피드백 루프 유지 |

## 3. ThrottlePolicy 인터페이스 설계

### 3.1 순수 ThrottlePolicy — 핵심 rate limit만 담당

```python
from selfhealing.core.types import PolicyResult, ResiliencePolicy

class ThrottlePolicy(ResiliencePolicy[T]):
    """
    순수 Throttle Policy — rate limit 검사만 수행.

    기존 AdaptiveThrottle에서 13건의 외부 의존성을 분리한 결과.
    SlidingWindowThrottle의 순수 rate limit 로직만 래핑한다.

    Guard/Hook/Sink는 PolicyComposer가 외부에서 연결한다.

    참조:
        - base.py: SlidingWindowThrottle (순수 rate limit)
        - config.py: ThrottleConfig, ThrottleResult
        - 225 문서: ResiliencePolicy Protocol
    """

    def __init__(
        self,
        config: ThrottleConfig | None = None,
        gradient_calculator: GradientCalculator | None = None,
    ):
        self._config = config or ThrottleConfig()
        self._engine = SlidingWindowThrottle(config)
        self._gradient = gradient_calculator or GradientCalculator(
            smoothing_factor=self._config.smoothing_factor,
        )
        self._current_limit = self._config.initial_limit

    @property
    def name(self) -> str:
        return "throttle"

    def execute(
        self,
        func: Callable[..., T],
        *args,
        **kwargs,
    ) -> PolicyResult[T]:
        """
        Throttle 검사 후 함수 실행.

        Args:
            func: 실행할 함수
            *args, **kwargs: 함수 인자

        Returns:
            PolicyResult — allowed면 함수 실행, rejected면 거부
        """
        key = kwargs.pop("_throttle_key", "default")

        result = self._engine.check(key)

        if not result.allowed:
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                metadata={
                    "policy": "throttle",
                    "reason": result.reason or "rate_limit_exceeded",
                    "limit": result.limit,
                    "remaining": result.remaining,
                },
            )

        # Rate limit 통과 → 함수 실행
        import time

        start = time.time()
        try:
            value = func(*args, **kwargs)
            elapsed_ms = (time.time() - start) * 1000
            self._gradient.add_sample(elapsed_ms)
            self._maybe_adjust_limit(elapsed_ms)

            return PolicyResult(
                value=value,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["throttle"],
                total_duration_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.time() - start) * 1000
            return PolicyResult(
                outcome=PolicyOutcome.FAILURE,
                error=e,
                executed_policies=["throttle"],
                total_duration_ms=elapsed_ms,
            )

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """
        Gradient 기반 limit 조정 — 순수 RTT/SLA 로직만 남김.

        기존 adaptive.py L1340-L1555의 거버넌스 체크,
        Emergency 동기화, Kill Switch Drift 교정은 모두 제거.
        Gradient 계산 + SLA 임계값 기반 limit 변경만 수행.
        """
        gradient = self._gradient.get_gradient()

        if rtt_ms >= self._config.sla_critical_ms:
            self._current_limit = max(
                int(self._current_limit * 0.7),
                self._config.min_limit,
            )
        elif rtt_ms >= self._config.sla_warning_ms:
            self._current_limit = max(
                int(self._current_limit * self._config.decrease_ratio),
                self._config.min_limit,
            )
        elif gradient > 0.1:
            self._current_limit = max(
                int(self._current_limit * self._config.decrease_ratio),
                self._config.min_limit,
            )
        elif gradient < -0.05:
            self._current_limit = min(
                self._current_limit + self._config.increase_step,
                self._config.max_limit,
            )
```

### 3.2 분리된 Guard/Hook/Sink — 기존 코드에서 추출

#### GovernanceGuard (Guard 4건 통합)

```python
class ThrottleGovernanceGuard:
    """
    AdaptiveThrottle에서 분리된 Governance Guard.

    기존 코드 위치:
    - _sync_governance_state()      adaptive.py L2186
    - _sync_kill_switch_state()     adaptive.py L2228
    - _sync_break_glass_state()     adaptive.py L826
    - is_automation_allowed()       GovernanceCheckMixin 상속

    Guard 인터페이스(225 문서 §2.3)를 구현하여
    PolicyComposer에서 add_guard()로 등록.
    """

    def check(self) -> GuardResult:
        """
        Kill Switch, Emergency Level, Error Budget, Break Glass 순서로 체크.

        Returns:
            GuardResult(allowed=True) 또는 GuardResult(allowed=False, reason=...)
        """
        ...
```

#### ThrottleEventBusHook (EventBus 5건 통합)

```python
class ThrottleEventBusHook:
    """
    AdaptiveThrottle에서 분리된 EventBus 연동 Hook.

    기존 코드 위치:
    - _subscribe_rate_limit_events()     adaptive.py L627
    - _subscribe_error_budget_events()   adaptive.py L910
    - _subscribe_load_shedding_events()  adaptive.py L846
    - _subscribe_kill_switch_events()    adaptive.py L782

    PolicyHook 인터페이스(225 문서 §2.4)를 구현하여
    PolicyComposer에서 add_hook()으로 등록.

    ThrottlePolicy.current_limit에 대한 외부 조정을 수행.
    """

    def __init__(self, throttle_policy: ThrottlePolicy):
        self._policy = throttle_policy
        self._subscribe_all()

    def _subscribe_all(self) -> None:
        """모든 EventBus 구독 등록 (Fail-Open)."""
        ...
```

#### ThrottleDLQSink (Sink 1건)

```python
class ThrottleDLQSink:
    """
    AdaptiveThrottle에서 분리된 DLQ Sink.

    기존 코드 위치:
    - ThrottleDLQReplayMixin      adaptive_dlq_replay.py 전체
    - _auto_store_rejection_to_dlq()  adaptive.py L1695

    FailureSink 인터페이스(225 문서 §2.5)를 구현하여
    PolicyComposer에서 add_sink()으로 등록.
    """

    def handle_rejection(self, context: dict, reason: str) -> None:
        """거부된 요청을 DLQ에 저장."""
        ...
```

## 4. 전환 후 소비자 사용 예시

### 4.1 기존 코드 (현재)

```python
# 소비자는 AdaptiveThrottle 내부의 13개 의존성 조합을 변경할 수 없음
throttle = get_adaptive_throttle()
result = throttle.check("user_123")  # 내부에서 Emergency/CB/ErrorBudget/DLQ 모두 하드코딩
```

### 4.2 Policy Composition 전환 후

```python
from selfhealing.policy import compose, throttle, retry, circuit_breaker, fallback

# 소비자가 필요한 패턴만 선언적으로 조합
policy = compose(
    throttle("payment_api", initial_limit=100, sla_warning_ms=200),
    circuit_breaker("payment_api", failure_threshold=5),
    retry(max_attempts=3, backoff=exponential()),
    fallback(cached_response),
)

# Guard/Hook/Sink는 소비자가 선택적으로 추가
policy.add_guard(ErrorBudgetGuard())      # 필요 시만
policy.add_hook(ThrottleMetricsHook())    # 필요 시만
policy.add_sink(DLQSink())               # 필요 시만

result = policy.execute(call_payment_api, order_id=123)
```

### 4.3 TrafficGate 대체 — PolicyComposer로 자연 흡수

기존 `TrafficGate`는 3개 컴포넌트(Bulkhead, LoadShedding, RateController)를 하드코딩 파이프라인으로 묶은 클래스이다:

```python
# 기존: scaling/traffic_gate.py — 하드코딩 3단계 파이프라인
class TrafficGate:
    def should_allow(self, priority, bulkhead_name=None):
        # 0단계: Bulkhead.try_acquire()     ← lazy import (L113)
        # 1단계: LoadShedding.should_accept() ← 생성자 주입 (L143)
        # 2단계: RateController.should_process() ← 정적 import (L24)
```

PolicyComposer 구현 후, 이 세 컴포넌트가 각기 독립 Policy/Guard로 분리되므로 TrafficGate 클래스 자체는 불필요해진다:

```python
# 전환 후: TrafficGate → compose()로 대체
policy = compose(
    bulkhead("database", max_concurrent=10),           # 228 BulkheadPolicy
    throttle("payment_api", initial_limit=100),        # 232 ThrottlePolicy
)
policy.add_guard(LoadSheddingGuard(priority_threshold=5))  # Guard로 분리
```

**코드 근거**: `traffic_gate.py` L165-L218의 `should_allow()` 메서드는 Bulkhead → LoadShedding → RateController 순서가 고정이며, 소비자가 이 순서를 변경하거나 특정 단계를 생략할 수 없다. PolicyComposer의 `compose()`는 이 제약을 해소한다.

## 5. Full Stop 로직 처리 방안

### 5.1 현재 Full Stop 3중 조건 (adaptive.py L1992-L2050)

```python
def check_full_stop_conditions(self) -> tuple[bool, str]:
    # 조건 1: Emergency LEVEL_3
    is_level_3 = self._emergency_level >= 3

    # 조건 2: DB Circuit Breaker OPEN ← L2007 lazy import CircuitBreakerService
    db_cb_open = self._check_db_circuit_breaker_open()

    # 조건 3: Error Budget 소진 ← L2042 lazy import ErrorBudgetService
    budget_exhausted = self._check_error_budget_exhausted()

    return is_level_3 and db_cb_open and budget_exhausted, reason
```

**문제점**: ThrottlePolicy 내부에서 CircuitBreakerService와 ErrorBudgetService를 직접 참조하는 것은 Policy 간 의존성을 유발한다.

### 5.2 전환 후 처리

Full Stop은 ThrottlePolicy와 분리하여 **전용 Guard**로 구현한다:

```python
class FullStopGuard:
    """
    Full Stop 3중 조건을 단독 Guard로 분리.

    기존 코드:
    - check_full_stop_conditions()       adaptive.py L1992
    - _check_db_circuit_breaker_open()   adaptive.py L2002
    - _check_error_budget_exhausted()    adaptive.py L2035
    - activate_full_stop()               adaptive.py L2064
    """

    def __init__(
        self,
        emergency_provider: Callable[[], int],
        cb_state_provider: Callable[[str], str],
        budget_provider: Callable[[], float],
    ):
        """생성자 주입으로 외부 시스템 의존성을 Protocol로 추상화."""
        self._get_emergency_level = emergency_provider
        self._get_cb_state = cb_state_provider
        self._get_budget_remaining = budget_provider

    def check(self) -> GuardResult:
        is_level_3 = self._get_emergency_level() >= 3
        db_cb_open = self._get_cb_state("database") == "open"
        budget_exhausted = self._get_budget_remaining() <= 0

        if is_level_3 and db_cb_open and budget_exhausted:
            return GuardResult(allowed=False, reason="full_stop:LEVEL_3+DB_CB_OPEN+BUDGET_EXHAUSTED")
        return GuardResult(allowed=True)
```

## 6. Recovery Dampening 처리 방안

### 6.1 현재 상태

Recovery Dampening은 `adaptive.py` 내부의 상태 머신이다 (L2274-L2380):
- 3단계: 80% → 90% → 100%
- `advance_recovery_dampening()`이 `check()` 호출마다 진행 확인

### 6.2 전환 후 처리

Recovery Dampening Manager(`recovery_dampening.py`)는 이미 독립 모듈이며 외부 의존성 0건이다. ThrottlePolicy의 limit 변경 콜백으로 연결한다:

```python
# recovery_dampening.py — 이미 독립적 (외부 의존성 0건)
class RecoveryDampeningManager:
    def __init__(
        self,
        config: RecoveryDampeningConfig | None = None,
        on_limit_change: Callable[[str, int], None] | None = None,  # ← 콜백 기반
    ):
```

ThrottlePolicy에서는 `on_limit_change` 콜백을 통해 연결만 하면 된다. Recovery Dampening의 트리거(Emergency 복구, CB CLOSE 등)는 EventBus Hook에서 담당한다.

## 7. 하위 호환성 보장

### 7.1 기존 API 유지

```python
# 기존 코드: 변경 없이 동작
from selfhealing.services.throttle import get_adaptive_throttle

throttle = get_adaptive_throttle()
result = throttle.check("user_123")  # ← 기존 API 그대로
```

### 7.2 내부 구현 점진 전환

```python
# Phase 1: ThrottlePolicy 래퍼 생성, AdaptiveThrottle은 유지
# Phase 2: AdaptiveThrottle 내부에서 ThrottlePolicy 위임
# Phase 3: get_adaptive_throttle()이 ThrottlePolicy 기반 인스턴스 반환

def get_adaptive_throttle(config=None):
    """기존 API 유지 — 내부적으로 ThrottlePolicy 사용."""
    # Phase 3 구현
    policy = ThrottlePolicy(config)
    governance_guard = ThrottleGovernanceGuard()
    event_hook = ThrottleEventBusHook(policy)
    dlq_sink = ThrottleDLQSink()

    return AdaptiveThrottleFacade(
        policy=policy,
        guards=[governance_guard],
        hooks=[event_hook],
        sinks=[dlq_sink],
    )
```

## 8. 기존 코드와의 대응 맵

| 기존 (adaptive.py) | 전환 후 | 책임 |
|-------------------|---------|------|
| `SlidingWindowThrottle.check()` | `ThrottlePolicy._engine.check()` | 순수 rate limit |
| `GradientCalculator + _maybe_adjust_limit()` | `ThrottlePolicy._maybe_adjust_limit()` | 순수 Gradient 기반 limit 조정 |
| `GovernanceCheckMixin` (상속) | `ThrottleGovernanceGuard` (Guard) | Kill Switch/Emergency/ErrorBudget/BreakGlass |
| `_subscribe_rate_limit_events()` | `ThrottleEventBusHook` (Hook) | 429 이벤트 반응 |
| `_subscribe_error_budget_events()` | `ThrottleEventBusHook` (Hook) | Error Budget 이벤트 반응 |
| `_subscribe_load_shedding_events()` | `ThrottleEventBusHook` (Hook) | Load Shedding 이벤트 반응 |
| `_subscribe_kill_switch_events()` | `ThrottleEventBusHook` (Hook) | Kill Switch 이벤트 반응 |
| `check_full_stop_conditions()` | `FullStopGuard` (Guard) | Full Stop 3중 조건 판단 |
| `ThrottleDLQReplayMixin` (상속) | `ThrottleDLQSink` (Sink) | 거부 요청 DLQ 저장 |
| `_record_throttle_metrics()` | `ThrottleMetricsHook` (Hook) | Prometheus 메트릭 |
| `_record_audit_safe()` | `ThrottleAuditHook` (Hook) | 감사 로깅 |
| `RecoveryDampening` (내부 상태 머신) | `RecoveryDampeningManager` (이미 독립) | limit 점진 복구 — 콜백 연결 |
| `_check_preemptive_protection()` | 외부 피드백 루프 유지 (Decision Engine과 동일 계층) | Burn Rate 기반 선제 보호 |

## 9. 전환 순서

```
Phase 1: ThrottlePolicy 핵심 구현
    - SlidingWindowThrottle + GradientCalculator 래핑
    - execute() 구현 (check → 함수 실행 → RTT 기록)
    - 단위 테스트: 순수 rate limit + Gradient 동작 검증
    ↓
Phase 2: Guard 분리
    - ThrottleGovernanceGuard (GovernanceCheckMixin → Guard 프로토콜)
    - FullStopGuard (CB + ErrorBudget 의존성 → 생성자 주입)
    ↓
Phase 3: Hook 분리
    - ThrottleEventBusHook (4개 EventBus 구독 통합)
    - ThrottleMetricsHook (25+ Prometheus 메트릭)
    - ThrottleAuditHook (감사 로깅)
    ↓
Phase 4: Sink 분리
    - ThrottleDLQSink (DLQ 저장 + Recovery Replay)
    ↓
Phase 5: 하위 호환 퍼사드
    - AdaptiveThrottleFacade: 기존 check() API 유지
    - get_adaptive_throttle() 점진 전환
    ↓
Phase 6: TrafficGate 대체
    - TrafficGate 소비자를 compose(bulkhead(), throttle()) + LoadSheddingGuard로 마이그레이션
    - TrafficGate 클래스 @deprecated 마킹
```

## 10. 참조 문서

| 문서번호 | 제목 | 관계 |
|---------|------|------|
| **224** | Policy Composition 마스터 플랜 | 상위 계획 |
| **225** | Policy 인터페이스 설계 | ResiliencePolicy, Guard, Hook, Sink 인터페이스 정의 |
| **226** | RetryPolicy 전환 | AdaptiveThrottle과 동일 구조의 하드코딩 분리 패턴 참조 |
| **228** | BulkheadPolicy 전환 | TrafficGate 대체 시 BulkheadPolicy와 조합 |
| **231** | PolicyComposer 조합 엔진 | compose() 빌더에 ThrottlePolicy 포함 |
