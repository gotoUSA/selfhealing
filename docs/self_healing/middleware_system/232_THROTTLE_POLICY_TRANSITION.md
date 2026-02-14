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
| 5 | `RATE_LIMIT_429` 이벤트 → limit 감소 | adaptive.py L634, L651 `_handle_rate_limit_429()` | EventBus 구독 | `ThrottleLimitAdjuster` — 독립 수명주기 컴포넌트 |
| 6 | `ERROR_BUDGET_WARNING/CRITICAL` → limit 감소 | adaptive.py L910, L970 `_handle_error_budget_warning()` | EventBus 구독 | `ThrottleLimitAdjuster` — 독립 수명주기 컴포넌트 |
| 7 | `LOAD_SHEDDING_LEVEL_CHANGED` → limit 조정 | adaptive.py L846, L862 `_handle_shedding_changed()` | EventBus 구독 | `ThrottleLimitAdjuster` — 독립 수명주기 컴포넌트 |
| 8 | `KILL_SWITCH_ACTIVATED/DEACTIVATED` → Gradient Freeze | adaptive.py L782, L793 `_handle_kill_switch_activated()` | EventBus 구독 | `ThrottleLimitAdjuster` — 독립 수명주기 컴포넌트 |
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
            new_limit = self._current_limit + self._config.increase_step

            # Recovery Dampening Cap: Dampening 활성 시 상한 제한
            # 근거: recovery_dampening.py L130 — phase별 target_limit * ratio로 Cap 설정
            #       adaptive.py L2361 — advance_recovery_dampening()이 limit 직접 설정
            #       Gradient가 독립적으로 limit을 올리면 Dampening 의미 소실
            if self._dampening_manager and self._dampening_manager.is_recovery_active(
                self._config.service_name,
            ):
                dampened_limit = self._dampening_manager.get_current_dampened_limit(
                    self._config.service_name,
                )
                if dampened_limit is not None:
                    new_limit = min(new_limit, dampened_limit)

            self._current_limit = min(new_limit, self._config.max_limit)

### 3.1.1 비동기 지원 제외

`AsyncThrottlePolicy`는 현재 구현하지 않는다.

**근거**:

| 항목 | 현재 상태 | 코드 위치 |
|------|-----------|----------|
| `AdaptiveThrottle` | `async def` 0건 — 순수 동기 | adaptive.py 전체 |
| `SlidingWindowThrottle` | 인메모리 `dict`/`list` 기반, I/O 0건 | base.py L170-L222 |
| 225 문서 결론 | "7개 전환 대상 중 async를 지원하는 것은 Bulkhead 하나" | 225 문서 §2.3 L151 |

`SlidingWindowThrottle`은 `defaultdict(list)` + `threading.Lock()`으로만 동작한다 (base.py L185-L187).
Redis 기반 분산 Throttle을 `check()` 경로에 도입할 경우에 한해 `AsyncThrottlePolicy`를 신설한다.

`redis_lua.py`의 `RedisThrottleLimitManager`는 **분산 limit 동기화** 전용이며,
`check()` 자체의 rate limit 판정 경로가 아니므로 async 전환 대상이 아니다.

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

#### ThrottleLimitAdjuster (EventBus 5건 통합 — 독립 수명주기 컴포넌트)

`PolicyHook` 인터페이스(225 문서 §2.5)는 요청 생명주기(`on_execute`, `on_success`, `on_failure`) 메서드만 정의한다.
EventBus 구독은 시스템 **전역 이벤트**(429, ErrorBudget, LoadShedding, KillSwitch)에 반응하여
ThrottlePolicy의 limit을 **직접 변경**하는 것이므로 요청 생명주기와 무관하다.

따라서 `PolicyHook`을 구현하지 않고, 230번 문서의 `HedgingConfigUpdateHook` (hedging.py L798-L844) 패턴을 따라
`register()` + `start()` 수명주기를 가진 **독립 컴포넌트**로 정의한다.

**네이밍 근거**:
- `ThrottleEventBusHook` ❌ — `PolicyHook`을 구현하지 않으므로 "Hook" 접미사 부적합
- `ThrottleConfigurationObserver` ❌ — 429, LoadShedding은 "Configuration" 변경이 아닌 시스템 상태 변경
- `ThrottleLimitAdjuster` ✅ — 핵심 책임이 limit 조정이며, 코드베이스에 0건 충돌 없음
- 기존 `Observer` 패턴: `AuditEventObserver` (audit_integration.py L463) — 순수 관찰. 본 컴포넌트는 limit을 **변경**하므로 Observer보다 Adjuster가 적확

```python
class ThrottleLimitAdjuster:
    """
    AdaptiveThrottle에서 분리된 EventBus → Limit 조정 컴포넌트.

    기존 코드 위치:
    - _subscribe_rate_limit_events()     adaptive.py L627
    - _subscribe_error_budget_events()   adaptive.py L910
    - _subscribe_load_shedding_events()  adaptive.py L846
    - _subscribe_kill_switch_events()    adaptive.py L782

    PolicyHook이 아닌 독립 수명주기 컴포넌트.
    HedgingConfigUpdateHook (hedging.py L798) 패턴과 동일 구조.

    ThrottlePolicy.current_limit에 대한 외부 조정을 수행.
    """

    def __init__(self) -> None:
        self._policies: list[ThrottlePolicy] = []

    def register(self, policy: ThrottlePolicy) -> None:
        """limit 조정 대상 ThrottlePolicy 등록."""
        self._policies.append(policy)

    def start(self) -> None:
        """
        EventBus 구독 시작.

        EventBus가 없는 환경에서는 아무 동작도 하지 않는다 (Fail-Open).
        HedgingConfigUpdateHook.start() (hedging.py L828)와 동일 패턴.
        """
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.RATE_LIMIT_429, self._handle_rate_limit_429)
            bus.subscribe(EventType.RATE_LIMIT_COOLDOWN_END, self._handle_cooldown_end)
            bus.subscribe(EventType.ERROR_BUDGET_WARNING, self._handle_error_budget)
            bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, self._handle_error_budget)
            bus.subscribe(EventType.ERROR_BUDGET_RECOVERED, self._handle_error_budget_recovered)
            bus.subscribe(EventType.LOAD_SHEDDING_LEVEL_CHANGED, self._handle_shedding_changed)
            bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self._handle_kill_switch)
            bus.subscribe(EventType.KILL_SWITCH_DEACTIVATED, self._handle_kill_switch)
        except ImportError:
            pass  # Fail-Open
        except Exception:
            pass  # Fail-Open

    def _handle_rate_limit_429(self, event) -> None:
        """429 이벤트 → 등록된 모든 Policy의 limit 감소."""
        for policy in self._policies:
            try:
                policy.reduce_limit_for_429(event)
            except Exception:
                pass  # Fail-Open

    # ... 기타 핸들러도 동일 Fail-Open 패턴
```

**Composer 연동**: `add_hook()` 대신 Facade 또는 팩토리 함수에서 직접 생성/시작한다:

```python
adjuster = ThrottleLimitAdjuster()
adjuster.register(throttle_policy)
adjuster.start()  # EventBus 구독 시작
```

#### LoadShedding 로직 분리 — Limit 조정 vs 요청 거부

현재 AdaptiveThrottle에는 두 가지 LoadShedding 로직이 섞여 있다:

| 로직 | 현재 위치 | 역할 | 전환 후 |
|------|-----------|------|--------|
| **Limit 조정** | adaptive.py L862 `_handle_shedding_changed()` | `_shedding_suggested_limit` 변경 | `ThrottleLimitAdjuster` (위 컴포넌트) |
| **우선순위 거부** | traffic_gate.py L161 `should_allow(priority=)` | 특정 priority 이하 요청 거부 | `LoadSheddingGuard` (별도 Guard) |

**ThrottlePolicy는 요청의 priority 정보를 전혀 알 필요가 없다.**
우선순위 기반 거부는 `LoadSheddingGuard`가 `PolicyContext.extra["priority"]`에서 읽어 판단한다:

```python
class LoadSheddingGuard:
    """
    우선순위 기반 LoadShedding Guard.

    기존 코드:
    - traffic_gate.py L143-L158 _check_load_shedding(priority)
    - CascadeLoadShedding.should_accept(priority=priority)

    ThrottlePolicy 외부에서 PolicyComposer.add_guard()로 등록.
    ThrottlePolicy는 priority를 알지 못한다.
    """

    @property
    def name(self) -> str:
        return "load_shedding"

    def __init__(self, load_shedding: Any | None = None):
        """
        Args:
            load_shedding: CascadeLoadShedding 인스턴스.
                           None이면 lazy import로 획득 (Fail-Open).
        """
        self._load_shedding = load_shedding

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        """
        우선순위 기반 LoadShedding 체크.

        context.extra["priority"]에서 요청 우선순위를 읽는다.
        context=None이면 전역 체크 (priority 무관, 통과 허용).
        """
        if self._load_shedding is None:
            return GuardResult(allowed=True)

        priority = 0
        if context and context.extra:
            priority = context.extra.get("priority", 0)

        try:
            result = self._load_shedding.should_accept(priority=priority)
            if isinstance(result, dict) and not result.get("accepted", True):
                return GuardResult(
                    allowed=False,
                    reason=f"load_shedding_rejected:priority={priority}",
                )
        except Exception:
            pass  # Fail-Open

        return GuardResult(allowed=True)
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

#### RateController → BackpressureGuard 전환

TrafficGate의 3번째 구성요소인 `RateController` (scaling/rate_controller.py)는 **ThrottlePolicy에 흡수되지 않는다.**

두 컴포넌트는 알고리즘과 목적이 완전히 다르다:

| | SlidingWindowThrottle (ThrottlePolicy 내부 엔진) | RateController |
|---|---|---|
| **알고리즘** | Sliding Window — 윈도우 내 요청 수 카운팅 (base.py L190-L222) | Token Bucket + AIMD 패턴 (rate_controller.py L53-L111, L250-L275) |
| **Rate 조절** | 외부에서 `current_limit` setter로 변경 | 큐 크기 기반 자동 조절 (Multiplicative Decrease / Additive Increase) |
| **목적** | 외부 API 호출 제한 (서비스 보호) | 내부 큐 과부하 방지 (backpressure) |
| **입력** | 요청 key (`"user_123"`) | 큐 크기 (`queue_size_provider: Callable[[], int]`) |
| **전략** | 단순 거부 | `REJECT` / `THROTTLE`(대기) / `DROP_OLDEST` / `QUEUE` (rate_controller.py L210-L240) |

RateController는 별도의 **BackpressureGuard**로 변환하여 PolicyComposer에 등록한다:

```python
class BackpressureGuard:
    """
    RateController 기반 Backpressure Guard.

    기존 코드:
    - traffic_gate.py L205 RateController.should_process()
    - rate_controller.py L196-L240 Token Bucket + 전략 분기

    ThrottlePolicy와 독립. 큐 기반 backpressure를 담당.
    """

    @property
    def name(self) -> str:
        return "backpressure"

    def __init__(self, rate_controller: RateController | None = None):
        self._controller = rate_controller

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        if self._controller is None:
            return GuardResult(allowed=True)

        if not self._controller.should_process():
            level = self._controller.get_state().level
            return GuardResult(
                allowed=False,
                reason=f"backpressure:level={level.value}",
            )
        return GuardResult(allowed=True)
```

**전환 후 TrafficGate 완전 대체:**

```python
policy = compose(
    bulkhead("database", max_concurrent=10),     # 228 BulkheadPolicy — 0단계
    throttle("payment_api", initial_limit=100),  # 232 ThrottlePolicy — rate limit
)
policy.add_guard(LoadSheddingGuard())              # 1단계: 우선순위 필터링
policy.add_guard(BackpressureGuard(rate_controller)) # 2단계: 큐 기반 backpressure
```

**네이밍 근거**: `BackpressureGuard` — 코드베이스에 0건 충돌 없음. `RateController`의 핵심 책임이 Backpressure이므로 적합.

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

    def check(self, context: PolicyContext | None = None) -> GuardResult:
        is_level_3 = self._get_emergency_level() >= 3
        db_cb_open = self._get_cb_state("database") == "open"
        budget_exhausted = self._get_budget_remaining() <= 0

        if is_level_3 and db_cb_open and budget_exhausted:
            return GuardResult(allowed=False, reason="full_stop:LEVEL_3+DB_CB_OPEN+BUDGET_EXHAUSTED")
        return GuardResult(allowed=True)
```

### 5.3 팩토리 함수 — `create_default_full_stop_guard()`

소비자가 매번 `FullStopGuard(lambda: ..., lambda: ..., lambda: ...)`를 작성하는 것은 번거롭다.
기존 코드가 adaptive.py L2001-L2057에서 lazy import + Fail-Open 패턴으로 동일 문제를 해결하고 있으므로,
이 패턴을 팩토리 함수로 추출한다.

**선례**: `_create_default_probes()` (177 문서 §3), `_create_default_service()` (227 문서 §6) — 동일 lazy import 팩토리 패턴.

```python
def create_default_full_stop_guard() -> FullStopGuard:
    """
    기본 FullStopGuard 생성.

    내부에서 CircuitBreakerService, ErrorBudgetService, EmergencyMode를
    lazy import하여 Guard를 조립한다.
    각 provider가 import 실패 시 Fail-Open (Guard 미작동 = 통과 허용).

    기존 코드 대응:
    - _check_db_circuit_breaker_open()  adaptive.py L2001-L2028
    - _check_error_budget_exhausted()   adaptive.py L2035-L2057
    - EmergencyMode.get_current_level() adaptive.py L2202
    """

    def _get_emergency_level() -> int:
        try:
            from selfhealing.core.emergency_mode import EmergencyMode

            return EmergencyMode.get_current_level()
        except ImportError:
            return 0  # Fail-Open: Emergency 모듈 없으면 NORMAL
        except Exception:
            return 0

    def _get_cb_state(service: str) -> str:
        try:
            from selfhealing.services.circuit_breaker_service import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()
            # 핵심 DB 서비스 목록 (adaptive.py L2017과 동일)
            db_services = ["database", "db", "postgres", "mysql", "redis", "mongodb"]
            for db_name in db_services:
                try:
                    state = cb_service.get_state(db_name)
                    if state == "open":
                        return "open"
                except Exception:
                    pass
            return "closed"
        except ImportError:
            return "closed"  # Fail-Open
        except Exception:
            return "closed"

    def _get_budget_remaining() -> float:
        try:
            from selfhealing.services.error_budget_service import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            return service.get_budget_status().budget_remaining_percent
        except ImportError:
            return 100.0  # Fail-Open: 예산 모듈 없으면 충분
        except Exception:
            return 100.0

    return FullStopGuard(
        emergency_provider=_get_emergency_level,
        cb_state_provider=_get_cb_state,
        budget_provider=_get_budget_remaining,
    )
```

**소비자 사용:**

```python
from selfhealing.resilience.policies.guards.full_stop import create_default_full_stop_guard

policy.add_guard(create_default_full_stop_guard())  # 람다 없이 한 줄
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

ThrottlePolicy에서는 `on_limit_change` 콜백을 통해 연결만 하면 된다. Recovery Dampening의 트리거(Emergency 복구, CB CLOSE 등)는 `ThrottleLimitAdjuster`에서 담당한다.

### 6.3 Dampening과 Gradient의 상호작용 — Limit Cap 규칙

Recovery Dampening은 단순 Observer가 아니라 **limit을 직접 변경하는 Controller**이다:

- `start_recovery()` (recovery_dampening.py L100-L150): 1단계 limit을 직접 계산하여 반환
- `_on_phase_transition()` (recovery_dampening.py L170-L210): 타이머로 자동 단계 전이 후 `on_limit_change` 콜백으로 limit 직접 설정

**문제**: Dampening이 활성 상태(80%→90%→100% 점진 복구)일 때, Gradient의 `_maybe_adjust_limit()`이 독립적으로 limit을 올릴 수 있다. 현재 코드에서 이를 막는 메커니즘은 `_gradient_frozen` 플래그뿐이며 (adaptive.py L1340-L1343), Dampening 상태와 직접 연동되지 않는다.

**해결 규칙**: ThrottlePolicy의 `_maybe_adjust_limit()` 내부에서 limit **상향** 시 Dampening Cap을 적용한다:

```
Dampening 활성 시:
  Gradient가 계산한 new_limit = min(gradient_limit, dampening_cap)
  dampening_cap = RecoveryDampeningManager의 현재 단계별 target_limit

Dampening 비활성 시:
  Gradient가 계산한 new_limit = min(gradient_limit, config.max_limit)  # 기존 동작
```

**limit 감소는 억제하지 않는다**: RTT가 급등하면 Dampening 중이라도 limit을 낮춰야 서비스를 보호할 수 있다. Cap은 **상향**에만 적용된다.

이 로직은 §3.1의 `_maybe_adjust_limit()` 코드에 반영되어 있다 (`gradient < -0.05` 분기 내 Dampening Cap 체크).

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
    limit_adjuster = ThrottleLimitAdjuster()
    limit_adjuster.register(policy)
    limit_adjuster.start()  # EventBus 구독 시작
    dlq_sink = ThrottleDLQSink()

    return AdaptiveThrottleFacade(
        policy=policy,
        guards=[governance_guard],
        limit_adjuster=limit_adjuster,
        sinks=[dlq_sink],
    )
```

### 7.3 AdaptiveThrottleFacade API — `check()` + `record_response()` 이중 API 유지

기존 `AdaptiveThrottle`은 `check()` + `record_response()` 2단계 API를 제공한다:

| 메서드 | 역할 | 소비자 호출처 |
|--------|------|---------------|
| `check(key)` | 허용/거부 판단 (실행 전) | `__init__.py` L22, throttle_adapter.py L139 |
| `record_response(rtt_ms)` | RTT 기록 → Gradient 기반 limit 동적 조정 | `__init__.py` L24, throttle_adapter.py L139, throttle_simulation.py L320, load_tests controller L486 |

**`record_response()`를 Facade에서 생략하면 최소 4곳의 소비자가 깨진다.**
`ThrottlePolicy.execute()`는 RTT를 내부 측정하지만, 레거시 `check()` 경로에서는 함수 실행이 Policy 외부에서 수행되므로 별도 RTT 피드백이 필요하다.

```python
class AdaptiveThrottleFacade:
    """
    레거시 check()/record_response() API를 유지하는 과도기 Facade.

    내부적으로 ThrottlePolicy에 위임한다.
    기존 __init__.py 공식 Usage와 동일한 2단계 패턴을 보장한다:
        result = throttle.check("user_123")
        throttle.record_response(response_time_ms=45.2)
    """

    def __init__(
        self,
        policy: ThrottlePolicy,
        guards: list | None = None,
        limit_adjuster: ThrottleLimitAdjuster | None = None,
        sinks: list | None = None,
    ):
        self._policy = policy
        self._guards = guards or []
        self._limit_adjuster = limit_adjuster
        self._sinks = sinks or []

    def check(
        self,
        key: str,
        tier_id: str = "standard",
        context: dict | None = None,
        store_rejection: bool = True,
    ) -> ThrottleResult:
        """
        기존 AdaptiveThrottle.check() API와 동일한 시그니처.

        Guards → SlidingWindowThrottle.check() → DLQ Sink 순서로 실행.
        """
        # Guard 체크
        for guard in self._guards:
            result = guard.check()
            if not result.allowed:
                return ThrottleResult(
                    allowed=False, current_count=0, limit=0,
                    remaining=0, reset_at=0, reason=result.reason,
                )

        # 순수 rate limit 체크
        throttle_result = self._policy._engine.check(key)

        # 거부 시 DLQ Sink 처리
        if not throttle_result.allowed and store_rejection and context:
            for sink in self._sinks:
                try:
                    sink.handle_rejection(context, throttle_result.reason or "rate_limit_exceeded")
                except Exception:
                    pass  # Fail-Open

        return throttle_result

    def record_response(self, rtt_ms: float) -> None:
        """
        RTT 기록 + Gradient 기반 limit 동적 조정.

        기존 AdaptiveThrottle.record_response() (adaptive.py L1257-L1340)과
        동일한 동작을 ThrottlePolicy에 위임한다.
        """
        self._policy._gradient.add_sample(rtt_ms)
        self._policy._maybe_adjust_limit(rtt_ms)
```

## 8. 기존 코드와의 대응 맵

| 기존 (adaptive.py) | 전환 후 | 책임 |
|-------------------|---------|------|
| `SlidingWindowThrottle.check()` | `ThrottlePolicy._engine.check()` | 순수 rate limit |
| `GradientCalculator + _maybe_adjust_limit()` | `ThrottlePolicy._maybe_adjust_limit()` | 순수 Gradient 기반 limit 조정 |
| `GovernanceCheckMixin` (상속) | `ThrottleGovernanceGuard` (Guard) | Kill Switch/Emergency/ErrorBudget/BreakGlass |
| `_subscribe_rate_limit_events()` | `ThrottleLimitAdjuster` (독립 컴포넌트) | 429 이벤트 → limit 감소 |
| `_subscribe_error_budget_events()` | `ThrottleLimitAdjuster` (독립 컴포넌트) | Error Budget 이벤트 → limit 감소 |
| `_subscribe_load_shedding_events()` | `ThrottleLimitAdjuster` (독립 컴포넌트) | Load Shedding → limit 조정 |
| `_subscribe_kill_switch_events()` | `ThrottleLimitAdjuster` (독립 컴포넌트) | Kill Switch → Gradient Freeze |
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
Phase 3: EventBus 연동 + Hook 분리
    - ThrottleLimitAdjuster (4개 EventBus 구독 통합 — 독립 수명주기 컴포넌트)
    - ThrottleMetricsHook (25+ Prometheus 메트릭)
    - ThrottleAuditHook (감사 로깅)
    ↓
Phase 4: Sink 분리
    - ThrottleDLQSink (DLQ 저장 + Recovery Replay)
    ↓
Phase 5: 하위 호환 퍼사드
    - AdaptiveThrottleFacade: 기존 check() + record_response() 이중 API 유지
    - get_adaptive_throttle() 점진 전환
    ↓
Phase 6: TrafficGate 대체
    - TrafficGate 소비자를 compose(bulkhead(), throttle()) + LoadSheddingGuard + BackpressureGuard로 마이그레이션
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

## 11. 구현 결과 — 설계 모순 해결 및 파일 구조

### 11.1 설계 모순 해결 (3건)

#### 모순 1: ThrottleDLQSink — FailureSink vs handle_rejection()

| 항목 | 설계 문서 | 실제 코드 |
|------|-----------|----------|
| §3.2 텍스트 | "FailureSink 인터페이스를 구현" | `handle_rejection(context, reason)` |
| FailureSink Protocol | `handle_failure(error, context, policy_result) → str` | 파이프라인 종단 실행 실패 전용 |
| §7.3 Facade | N/A | `sink.handle_rejection(context, reason)` |

**해결**: FailureSink를 구현하지 않고 독립 인터페이스로 구현.

**근거**: FailureSink.handle_failure()는 "함수 실행 실패 후" 최종 처리 목적이다.
Throttle 거부는 "함수 실행 이전의 정책 결정"이므로 실행 실패가 아니다.
기존 AdaptiveThrottle._auto_store_rejection_to_dlq(context, reason)도
FailureSink가 아닌 거부 전용 저장이었다.
동일한 개념적 분리를 유지하여 handle_rejection(context, reason)을 제공한다.

#### 모순 2: get_current_dampened_limit() 미존재 메서드

| 항목 | 설계 문서 §3.1 | 실제 RecoveryDampeningManager |
|------|----------------|-------------------------------|
| 호출 | `self._dampening_manager.get_current_dampened_limit(service_name)` | 해당 메서드 없음 |
| 가용 API | N/A | `get_current_multiplier() → float`, `get_recovery_state() → dict` |

**해결**: 기존 공개 API 2개를 조합하여 dampened_limit 계산.

```python
# ThrottlePolicy._apply_dampening_cap() 내부
recovery_state = self._dampening_manager.get_recovery_state(service_name)
target_limit = recovery_state["target_limit"]
multiplier = self._dampening_manager.get_current_multiplier(service_name)
dampened_limit = int(target_limit * multiplier)
```

**근거**: RecoveryDampeningManager는 외부 의존성 0건의 독립 모듈이며
이미 안정화된 상태이므로 새 메서드를 추가하지 않고
기존 공개 API만 사용하여 안정성을 유지한다.

#### 모순 3: execute() 시그니처 — context 파라미터 누락

| 항목 | 설계 문서 §3.1 | ResiliencePolicy Protocol |
|------|----------------|--------------------------|
| 시그니처 | `execute(func, *args, **kwargs)` | `execute(func, *args, context=None, **kwargs)` |
| throttle key | `kwargs.pop("_throttle_key", "default")` | N/A |

**해결**: Protocol에 맞게 `context: PolicyContext | None = None` 추가.
throttle key는 `context.extra["throttle_key"]`에서 읽도록 변경.

**근거**: 모든 기존 Policy(RetryPolicy, CircuitBreakerPolicy, BulkheadPolicy,
FallbackPolicy, HedgingPolicy)가 context 파라미터를 포함한다.
PolicyComposer가 context를 체인 전체에 전파하므로 누락 시
Guard/Hook/Sink에 context가 전달되지 않는다.
throttle key를 kwargs.pop()으로 꺼내면 다운스트림 함수에
전달될 kwargs가 오염되므로 context.extra 경유가 적합하다.

### 11.2 구현 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── services/throttle/
│   ├── policy.py              # ThrottlePolicy — 순수 rate limit Policy (신규)
│   ├── limit_adjuster.py      # ThrottleLimitAdjuster — EventBus limit 조정 (신규)
│   ├── dlq_sink.py            # ThrottleDLQSink — 거부 요청 DLQ 저장 (신규)
│   ├── facade.py              # AdaptiveThrottleFacade — 레거시 API 호환 (신규)
│   └── __init__.py            # re-export 추가
├── resilience/policies/
│   ├── guards/
│   │   ├── governance.py      # ThrottleGovernanceGuard (신규)
│   │   ├── full_stop.py       # FullStopGuard + create_default_full_stop_guard (신규)
│   │   ├── load_shedding.py   # LoadSheddingGuard (신규)
│   │   ├── backpressure.py    # BackpressureGuard (신규)
│   │   └── __init__.py        # re-export 추가
│   └── __init__.py            # ThrottlePolicy + Guards re-export 추가
```

### 11.3 통합 테스트 판단

**결론: 별도 통합 테스트 불필요.**

| 근거 | 상세 |
|------|------|
| 기존 Policy 선례 | RetryPolicy, BulkheadPolicy, FallbackPolicy, HedgingPolicy 중 전용 통합 테스트 0건 |
| 기존 Throttle 커버리지 | `test_throttle_eventbus_integration.py` 등 7건이 하위 인프라 연동 검증 |
| 신규 코드 특성 | 기존 SlidingWindowThrottle/GradientCalculator 래핑 + lazy import Fail-Open 패턴 |
