# 192. Retry Handler - AdaptiveThrottle Backoff 조정 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**:
> - `selfhealing/services/retry_handler.py` (Line 164-654)
> - `selfhealing/services/backoff_calculator.py` (Line 1-192)
> - `selfhealing/core/backoff.py` (Line 1-403)
> - `selfhealing/services/throttle/adaptive.py`
> **우선순위**: 🟡 P2

---

## 1. 개요

본 문서는 `RetryHandler`의 Backoff 전략을 `AdaptiveThrottle` 상태와 연동하는 구현을 정의합니다.

### 1.1 연동 목적

| 목적 | 설명 |
|------|------|
| Thundering Herd 방지 | Throttle limit 감소 시 재시도 간격 증가 |
| Self-DDoS 방지 강화 | 429 + Throttle 이중 Backoff 적용 |
| 적응적 재시도 | 시스템 부하에 따른 동적 Backoff 조정 |

### 1.2 현재 RetryHandler 구현

**코드 위치**: [retry_handler.py](../../packages/selfhealing-python/src/selfhealing/services/retry_handler.py#L164-L240)

```python
class RetryHandler:
    """
    Handles retry logic with exponential backoff.

    Now includes Rate Limit Awareness to prevent Self-DDoS:
    - Detects 429 responses
    - Coordinates cooldown across all workers
    - Uses distributed storage (Redis/DB)
    """

    def __init__(
        self,
        config: RetryConfig | None = None,
        domain: str = "default",
        rate_limit_coordinator: RateLimitCoordinator | None = None,
    ):
        self.config = config or RetryConfig.from_settings(domain)
        self.backoff = BackoffCalculator(
            BackoffConfig(
                base=self.config.backoff_base,
                max_delay=self.config.backoff_max,
                jitter_percent=self.config.jitter_percent,
            )
        )
        # Rate limit coordinator for Self-DDoS prevention
        self._rate_limit_coordinator = rate_limit_coordinator
```

### 1.3 현재 Backoff 계산

**코드 위치**: [backoff_calculator.py](../../packages/selfhealing-python/src/selfhealing/services/backoff_calculator.py#L64-L111)

```python
class BackoffCalculator:
    def calculate(self, attempt: int, with_jitter: bool = True) -> int:
        """
        Calculate backoff delay for a given attempt.

        Formula: delay = min(base^attempt, max_delay) * (1 ± jitter)

        Example (base=4, max=180, jitter=25%):
        - Attempt 1: 4s (±1s) → 3-5s
        - Attempt 2: 16s (±4s) → 12-20s
        - Attempt 3: 64s (±16s) → 48-80s
        - Attempt 4+: 180s (capped)
        """
        if attempt < 1:
            return self.config.min_delay

        delay = self.config.base ** attempt
        delay = min(delay, self.config.max_delay)

        if with_jitter and self.config.jitter_percent > 0:
            jitter_factor = self.config.jitter_percent / 100.0
            jitter = delay * jitter_factor * (random.random() * 2 - 1)
            delay = int(delay + jitter)

        return max(self.config.min_delay, delay)
```

### 1.4 문제점

| 문제 | 설명 |
|------|------|
| 정적 Backoff | 시스템 상태와 무관한 고정된 지수적 증가 |
| Throttle 미인식 | Throttle limit 감소 시에도 동일한 재시도 간격 |
| Emergency 미인식 | Emergency 모드에서도 공격적인 재시도 |
| 단절된 조정 | 429 Rate Limit과 Throttle 간 조정 분리 |

---

## 2. 연동 아키텍처

### 2.1 시퀀스 다이어그램

```
┌─────────────┐     ┌──────────────┐     ┌───────────────────┐
│ RetryHandler │    │BackoffCalculator│  │  AdaptiveThrottle  │
└──────┬──────┘     └───────┬──────┘     └─────────┬─────────┘
       │                     │                      │
       │ ① get_next_delay    │                      │
       │────────────────────►│                      │
       │                     │ ② get_throttle_state │
       │                     │─────────────────────►│
       │                     │                      │
       │                     │ ③ ThrottleState      │
       │                     │◄─────────────────────│
       │                     │                      │
       │                     │ ④ apply_multiplier   │
       │                     │─────────┐            │
       │                     │◄────────┘            │
       │                     │                      │
       │ ⑤ adjusted_delay   │                      │
       │◄────────────────────│                      │
```

### 2.2 Backoff 배율 매핑

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Throttle 상태별 Backoff 배율                          │
├─────────────────────────┬──────────────────┬────────────────────────────┤
│      Throttle 상태       │  Backoff 배율    │          설명              │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  RTT 정상, limit 정상    │      ×1.0        │  기본 Backoff 유지         │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  SLA Warning 발생       │      ×1.5        │  재시도 간격 50% 증가      │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  SLA Critical 발생      │      ×2.0        │  재시도 간격 2배 증가      │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  Emergency LEVEL_1~2    │      ×2.5        │  재시도 간격 2.5배 증가    │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  Emergency LEVEL_3      │      ×4.0        │  재시도 간격 4배 증가      │
├─────────────────────────┼──────────────────┼────────────────────────────┤
│  Full Stop 활성화       │      ∞ (차단)    │  재시도 즉시 중단, DLQ 이동│
└─────────────────────────┴──────────────────┴────────────────────────────┘
```

---

## 3. 구현 명세

### 3.1 ThrottleAwareBackoffCalculator 클래스

**추가 위치**: [backoff_calculator.py](../../packages/selfhealing-python/src/selfhealing/services/backoff_calculator.py)

```python
@dataclass
class ThrottleState:
    """AdaptiveThrottle 현재 상태 스냅샷."""

    current_limit: int
    initial_limit: int
    emergency_level: int = 0
    full_stop_active: bool = False
    sla_warning_active: bool = False
    sla_critical_active: bool = False
    recovery_dampening_active: bool = False
    error_budget_reduction_active: bool = False


class ThrottleAwareBackoffCalculator(BackoffCalculator):
    """
    AdaptiveThrottle 상태를 인식하는 Backoff 계산기.

    시스템 부하 상태에 따라 동적으로 재시도 간격을 조정합니다.

    Usage:
        calculator = ThrottleAwareBackoffCalculator()
        delay = calculator.calculate_with_throttle_context(attempt=2)
    """

    # 상태별 Backoff 배율
    BACKOFF_MULTIPLIERS = {
        "normal": 1.0,
        "sla_warning": 1.5,
        "sla_critical": 2.0,
        "emergency_1_2": 2.5,
        "emergency_3": 4.0,
        "error_budget_critical": 3.0,
    }

    def __init__(
        self,
        config: BackoffConfig | None = None,
        throttle_getter: Callable[[], Any] | None = None,
    ):
        """
        초기화.

        Args:
            config: Backoff 설정
            throttle_getter: AdaptiveThrottle 인스턴스 getter (DI용)
        """
        super().__init__(config)
        self._throttle_getter = throttle_getter

    def _get_throttle(self):
        """AdaptiveThrottle 인스턴스 획득 (Fail-Open)."""
        if self._throttle_getter:
            return self._throttle_getter()

        try:
            from selfhealing.services.throttle.adaptive import get_adaptive_throttle
            return get_adaptive_throttle()
        except ImportError:
            return None
        except Exception:
            return None

    def _get_throttle_state(self) -> ThrottleState | None:
        """현재 Throttle 상태 스냅샷 획득."""
        throttle = self._get_throttle()
        if throttle is None:
            return None

        try:
            stats = throttle.get_stats()
            adaptive_stats = stats.get("adaptive", {})
            emergency_stats = stats.get("emergency", {})

            return ThrottleState(
                current_limit=stats.get("current_limit", 100),
                initial_limit=throttle.config.initial_limit,
                emergency_level=emergency_stats.get("level", 0),
                full_stop_active=emergency_stats.get("full_stop_active", False),
                sla_warning_active=adaptive_stats.get("sla_warnings", 0) > 0,
                sla_critical_active=adaptive_stats.get("sla_criticals", 0) > 0,
                recovery_dampening_active=stats.get("recovery", {}).get("dampening_active", False),
                error_budget_reduction_active=getattr(
                    throttle, "_error_budget_limit_reduction_active", False
                ),
            )
        except Exception:
            return None

    def _calculate_multiplier(self, state: ThrottleState) -> float:
        """상태 기반 Backoff 배율 계산."""
        # Full Stop: 최대 배율 (재시도 차단에 가까움)
        if state.full_stop_active:
            return float("inf")  # 무한대 → execute()에서 즉시 DLQ 이동

        # Emergency LEVEL_3
        if state.emergency_level >= 3:
            return self.BACKOFF_MULTIPLIERS["emergency_3"]

        # Emergency LEVEL_1~2
        if state.emergency_level > 0:
            return self.BACKOFF_MULTIPLIERS["emergency_1_2"]

        # Error Budget Critical
        if state.error_budget_reduction_active:
            return self.BACKOFF_MULTIPLIERS["error_budget_critical"]

        # SLA Critical
        if state.sla_critical_active:
            return self.BACKOFF_MULTIPLIERS["sla_critical"]

        # SLA Warning
        if state.sla_warning_active:
            return self.BACKOFF_MULTIPLIERS["sla_warning"]

        # 정상 상태
        return self.BACKOFF_MULTIPLIERS["normal"]

    def calculate_with_throttle_context(
        self,
        attempt: int,
        with_jitter: bool = True,
    ) -> tuple[int, float, str]:
        """
        Throttle 상태를 고려한 Backoff 계산.

        Args:
            attempt: 재시도 횟수
            with_jitter: Jitter 적용 여부

        Returns:
            (adjusted_delay, multiplier, reason) 튜플
        """
        base_delay = self.calculate(attempt, with_jitter)

        state = self._get_throttle_state()
        if state is None:
            return base_delay, 1.0, "throttle_unavailable"

        multiplier = self._calculate_multiplier(state)

        # Full Stop 시 무한대 → 특수 처리
        if multiplier == float("inf"):
            return -1, float("inf"), "full_stop_active"

        adjusted_delay = int(base_delay * multiplier)

        # max_delay cap 적용
        adjusted_delay = min(adjusted_delay, self.config.max_delay * 2)  # 2배까지 허용

        # Reason 결정
        reason = "normal"
        if state.full_stop_active:
            reason = "full_stop"
        elif state.emergency_level >= 3:
            reason = "emergency_level_3"
        elif state.emergency_level > 0:
            reason = f"emergency_level_{state.emergency_level}"
        elif state.error_budget_reduction_active:
            reason = "error_budget_critical"
        elif state.sla_critical_active:
            reason = "sla_critical"
        elif state.sla_warning_active:
            reason = "sla_warning"

        return adjusted_delay, multiplier, reason
```

### 3.2 RetryHandler 수정

**수정 위치**: [retry_handler.py](../../packages/selfhealing-python/src/selfhealing/services/retry_handler.py)

```python
class RetryHandler:

    def __init__(
        self,
        config: RetryConfig | None = None,
        domain: str = "default",
        rate_limit_coordinator: RateLimitCoordinator | None = None,
        throttle_aware: bool = True,  # 신규 파라미터
    ):
        self.config = config or RetryConfig.from_settings(domain)

        # Throttle-aware Backoff 사용 여부
        self._throttle_aware = throttle_aware

        if throttle_aware:
            from .backoff_calculator import ThrottleAwareBackoffCalculator
            self.backoff = ThrottleAwareBackoffCalculator(
                BackoffConfig(
                    base=self.config.backoff_base,
                    max_delay=self.config.backoff_max,
                    jitter_percent=self.config.jitter_percent,
                )
            )
        else:
            self.backoff = BackoffCalculator(
                BackoffConfig(
                    base=self.config.backoff_base,
                    max_delay=self.config.backoff_max,
                    jitter_percent=self.config.jitter_percent,
                )
            )

    def get_next_delay(self, attempt: int) -> int:
        """
        Get the delay before the next retry attempt.

        Throttle-aware 모드 시 시스템 상태 반영.

        Args:
            attempt: Current attempt number

        Returns:
            Delay in seconds
        """
        if self._throttle_aware and hasattr(self.backoff, "calculate_with_throttle_context"):
            delay, multiplier, reason = self.backoff.calculate_with_throttle_context(attempt)

            # Full Stop 시 즉시 DLQ 이동 신호
            if delay < 0:
                logger.warning(
                    f"[RetryHandler] Full Stop active, skipping retry for attempt {attempt}"
                )
                return -1  # 특수값: execute()에서 즉시 DLQ 이동

            if multiplier > 1.0:
                logger.info(
                    f"[RetryHandler] Backoff adjusted: {self.backoff.calculate(attempt)}s → "
                    f"{delay}s (×{multiplier:.1f}, reason={reason})"
                )

            return delay

        return self.backoff.calculate(attempt)

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RetryResult:
        """
        Execute a function with retry logic.

        Throttle-aware Backoff 적용:
        - Full Stop 시 재시도 없이 즉시 DLQ 이동
        - Emergency 시 Backoff 배율 적용
        """
        # ... 기존 Kill Switch / ErrorBudgetGate 체크 ...

        attempt = 0
        last_error: Exception | None = None
        retry_history: list[dict[str, Any]] = []

        while attempt < self.config.max_attempts:
            attempt += 1

            # Self-DDoS prevention: Wait if rate limited
            self._wait_for_rate_limit()

            try:
                result = func(*args, **kwargs)
                # ... 성공 처리 ...

            except Exception as e:
                last_error = e
                # ... 에러 기록 ...

                if self.should_retry(e, attempt):
                    delay = self.get_next_delay(attempt)

                    # Full Stop 신호 처리
                    if delay < 0:
                        logger.warning(
                            f"[RetryHandler] Full Stop triggered, moving to DLQ immediately"
                        )
                        break  # while 루프 탈출 → DLQ 이동

                    # Audit 기록: Throttle-aware backoff 적용
                    self._log_retry_audit(
                        attempt=attempt,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e)[:500],
                        wait_time=delay,
                        context={
                            **(context or {}),
                            "throttle_aware_backoff": self._throttle_aware,
                        },
                    )

                    continue
                else:
                    break

        # ... 기존 DLQ 이동 로직 ...
```

### 3.3 RetryConfig 확장

```python
@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base: int = 4
    backoff_max: int = 180
    jitter_percent: int = 25
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (Exception,)
    )
    non_retryable_exceptions: tuple[type[Exception], ...] = field(default_factory=tuple)
    enable_dlq: bool = True
    domain: str = "default"

    # Rate limit awareness
    rate_limit_aware: bool = True
    rate_limit_key: str | None = None

    # Throttle awareness (신규)
    throttle_aware: bool = True
    throttle_backoff_multiplier_cap: float = 4.0  # 최대 4배까지
```

---

## 4. EventBus 연동 (선택적)

### 4.1 Throttle 이벤트 구독

```python
class RetryHandler:

    def __init__(self, ...):
        # ... 기존 코드 ...

        # Throttle 이벤트 캐싱 (매번 조회 대신 EventBus 푸시 수신)
        self._cached_throttle_multiplier: float = 1.0
        self._subscribe_throttle_events()

    def _subscribe_throttle_events(self) -> None:
        """Throttle 상태 변경 이벤트 구독 (선택적 최적화)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.THROTTLE_LIMIT_CHANGED, self._on_throttle_changed)
            bus.subscribe(EventType.THROTTLE_SLA_WARNING, self._on_sla_warning)
            bus.subscribe(EventType.THROTTLE_SLA_CRITICAL, self._on_sla_critical)
        except Exception:
            pass  # Fail-Open

    def _on_throttle_changed(self, event) -> None:
        """Throttle limit 변경 시 캐시 업데이트."""
        data = event.data if hasattr(event, "data") else event
        reason = data.get("reason", "")

        if "emergency" in reason:
            self._cached_throttle_multiplier = 2.5
        elif "sla_critical" in reason:
            self._cached_throttle_multiplier = 2.0
        elif "sla_warning" in reason:
            self._cached_throttle_multiplier = 1.5
        else:
            self._cached_throttle_multiplier = 1.0
```

---

## 5. 테스트 케이스

### 5.1 단위 테스트

```python
class TestThrottleAwareBackoffCalculator:
    """Throttle-aware Backoff 테스트."""

    def test_normal_state_no_multiplier(self):
        """정상 상태 시 배율 1.0."""
        mock_throttle = Mock()
        mock_throttle.get_stats.return_value = {
            "current_limit": 100,
            "adaptive": {"sla_warnings": 0, "sla_criticals": 0},
            "emergency": {"level": 0, "full_stop_active": False},
        }
        mock_throttle.config.initial_limit = 100

        calculator = ThrottleAwareBackoffCalculator(
            throttle_getter=lambda: mock_throttle
        )

        delay, multiplier, reason = calculator.calculate_with_throttle_context(1)

        assert multiplier == 1.0
        assert reason == "normal"
        assert delay == 4  # base^1

    def test_emergency_level_3_quadruples_delay(self):
        """Emergency LEVEL_3 시 4배 증가."""
        mock_throttle = Mock()
        mock_throttle.get_stats.return_value = {
            "current_limit": 10,
            "adaptive": {"sla_warnings": 5, "sla_criticals": 3},
            "emergency": {"level": 3, "full_stop_active": False},
        }
        mock_throttle.config.initial_limit = 100

        calculator = ThrottleAwareBackoffCalculator(
            throttle_getter=lambda: mock_throttle
        )

        delay, multiplier, reason = calculator.calculate_with_throttle_context(1)

        assert multiplier == 4.0
        assert reason == "emergency_level_3"
        assert delay == 16  # 4 * 4

    def test_full_stop_returns_negative_delay(self):
        """Full Stop 시 -1 반환 (즉시 중단 신호)."""
        mock_throttle = Mock()
        mock_throttle.get_stats.return_value = {
            "current_limit": 0,
            "adaptive": {},
            "emergency": {"level": 3, "full_stop_active": True},
        }
        mock_throttle.config.initial_limit = 100

        calculator = ThrottleAwareBackoffCalculator(
            throttle_getter=lambda: mock_throttle
        )

        delay, multiplier, reason = calculator.calculate_with_throttle_context(1)

        assert delay == -1
        assert multiplier == float("inf")
        assert reason == "full_stop_active"


class TestRetryHandlerThrottleIntegration:
    """RetryHandler Throttle 연동 테스트."""

    def test_full_stop_skips_to_dlq(self):
        """Full Stop 시 재시도 없이 즉시 DLQ 이동."""
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig

        config = RetryConfig(max_attempts=5, throttle_aware=True)
        handler = RetryHandler(config=config)

        # Full Stop 상태 시뮬레이션
        with patch.object(handler.backoff, "calculate_with_throttle_context") as mock:
            mock.return_value = (-1, float("inf"), "full_stop_active")

            def always_fail():
                raise Exception("Test error")

            result = handler.execute(always_fail)

            assert result.success is False
            assert result.attempt == 1  # 첫 시도에서 즉시 DLQ
            assert result.action == RetryAction.DLQ
```

---

## 6. 메트릭 정의

### 6.1 Prometheus 메트릭

```python
# 추가 메트릭 정의 필요
retry_backoff_multiplier = Histogram(
    "selfhealing_retry_backoff_multiplier",
    "Backoff multiplier applied due to throttle state",
    ["domain", "reason"],
    buckets=[1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
)

retry_throttle_full_stop_skips_total = Counter(
    "selfhealing_retry_throttle_full_stop_skips_total",
    "Total retries skipped due to throttle full stop",
    ["domain"],
)
```

---

## 7. 구현 체크리스트

- [ ] `ThrottleState` dataclass 정의
- [ ] `ThrottleAwareBackoffCalculator` 클래스 구현
- [ ] `BACKOFF_MULTIPLIERS` 상수 정의
- [ ] `calculate_with_throttle_context()` 메서드 구현
- [ ] `RetryHandler.__init__` 수정 (`throttle_aware` 파라미터)
- [ ] `RetryHandler.get_next_delay()` 수정
- [ ] `RetryHandler.execute()` Full Stop 처리 추가
- [ ] `RetryConfig` 확장 (`throttle_aware`, `throttle_backoff_multiplier_cap`)
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] Prometheus 메트릭 추가

---

## 8. 참고 문서

- [191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md](191_ERROR_BUDGET_GATE_THROTTLE_INTEGRATION.md) - Error Budget 연동
- [187_RATE_LIMIT_COORDINATOR_INTEGRATION.md](187_RATE_LIMIT_COORDINATOR_INTEGRATION.md) - Rate Limit 연동
- [154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md](154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md) - Emergency 연동
