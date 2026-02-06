# 192. Retry Handler - AdaptiveThrottle Backoff 조정 연동 구현

> **문서 버전**: 2.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**:
> - `selfhealing/services/retry_handler.py` (Line 164-654)
> - `selfhealing/services/backoff_calculator.py` (Line 1-192)
> - `selfhealing/core/backoff.py` (Line 1-403)
> - `selfhealing/services/throttle/adaptive.py`
> - `selfhealing/services/throttle/registry.py` (v2.0 추가)
> - `selfhealing/services/circuit_breaker/protection.py` (v2.0 추가)
> - `selfhealing/core/fallback_strategy.py` (v2.0 추가)
> **우선순위**: 🟡 P2

---

## v2.0 리뷰 반영 사항

본 문서 v2.0에서는 설계 리뷰를 통해 식별된 12개 항목을 반영합니다.

| # | 리뷰 항목 | 반영 위치 | 핵심 변경 |
|---|----------|----------|----------|
| 1 | 백오프 배율 cap 명시화 | §9.1 | `max_delay * 2` → `SYSTEM_TIMEOUT_SECONDS` 상수 정의 |
| 2 | 지터 범위 확장 | §3.1 | 기존 설계 유지 (배율 × Jitter 자동 확장) |
| 3 | Throttle 상태 캐싱 필수화 | §9.2 | EventBus 구독 기반 `PushBasedThrottleStateCache` 기본 활성화 |
| 4 | 글로벌 Throttle 상태 공유 | §9.3 | Redis 기반 `GlobalThrottleState` 옵션 추가 |
| 5 | 도메인별 Throttle 매핑 | §9.4 | `ThrottleRegistry` 연동으로 서비스별 Throttle 지원 |
| 6 | 429/백오프 max() 방식 | §9.5 | `get_combined_delay()` - 합산 대신 max() 선택 |
| 7 | 에러 예산 위기 시 배율 | §9.6 | `ErrorBudgetGate` WARNING 시에도 3.0x 배율 적용 |
| 8 | DLQ 에스컬레이션 강화 | §9.7 | 페이로드에 `backoff_multiplier`, `throttle_reason` 추가 |
| 9 | 우선순위별 차등 대우 | §9.8 | CRITICAL 티어 FULL_STOP 시 grace retry 1회 허용 |
| 10 | 백오프 확장 메트릭 | §9.9 | `definitions.py`에 4개 메트릭 공식 등록 |
| 11 | 복합 시나리오 테스트 | §9.10 | Hedging + Bulkhead + Backoff 통합 테스트 케이스 |
| 12 | Adaptive Retry Budget | §9.11 | 재시도 비율 10% 제한 + Throttle 연동 동적 삭감 |

> **참고**: 13번 "CB 자동 오픈"은 이미 `protection.py`의 `record_rate_limit_response()`에 구현되어 있음 (§9.12 참조)

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

---

## 9. v2.0 구현 명세

### 9.1 백오프 배율 cap 명시화 (리뷰 #1)

**문제점**: 기존 `max_delay * 2`는 임의적이며, 시스템 타임아웃과 관계가 불명확.

**수정 제안**:

```python
# backoff_calculator.py 상수 추가

# 시스템 전체 타임아웃 (30분) - 이를 초과하면 사용자 체감 불가
SYSTEM_TIMEOUT_SECONDS = 1800

class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def calculate_with_throttle_context(self, attempt: int, with_jitter: bool = True) -> tuple[int, float, str]:
        base_delay = self.calculate(attempt, with_jitter)

        state = self._get_throttle_state()
        if state is None:
            return base_delay, 1.0, "throttle_unavailable"

        multiplier = self._calculate_multiplier(state)

        if multiplier == float("inf"):
            return -1, float("inf"), "full_stop_active"

        adjusted_delay = int(base_delay * multiplier)

        # v2.0: 시스템 타임아웃 기준 cap (임의적 2배 대신)
        adjusted_delay = min(adjusted_delay, SYSTEM_TIMEOUT_SECONDS)

        # 운영자 알림용 로깅 (cap 적용 시)
        if adjusted_delay == SYSTEM_TIMEOUT_SECONDS:
            logger.warning(
                f"[ThrottleAwareBackoff] Delay capped at SYSTEM_TIMEOUT: "
                f"original={base_delay * multiplier}s → {SYSTEM_TIMEOUT_SECONDS}s"
            )

        return adjusted_delay, multiplier, self._determine_reason(state)
```

**네이밍 선택**: `SYSTEM_TIMEOUT_SECONDS`
- **이유**: 프로젝트 내 `*_SECONDS` 접미사 패턴 일관성 (`recovery_timeout`, `fallback_cache_ttl_seconds` 등)

---

### 9.2 Throttle 상태 캐싱 필수화 (리뷰 #3)

**문제점**: 매 재시도마다 `get_stats()` 호출 → Lock 경합 발생

**코드 근거**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L459-L476)
```python
def get_stats(self) -> dict:
    with self._lock:  # ← 매번 Lock 획득
        # ...
```

**수정 제안**: EventBus 푸시 기반 캐싱을 **기본 활성화**

```python
@dataclass
class PushBasedThrottleStateCache:
    """
    EventBus 푸시 기반 Throttle 상태 캐시.

    매번 get_stats() 호출 대신 EventBus 이벤트를 구독하여
    상태 변경 시에만 캐시를 업데이트합니다.

    네이밍 이유: "PushBased" - EventBus가 상태를 푸시하는 구조를 명확히 표현
                "Cache" - 캐싱 목적 명시
    """

    multiplier: float = 1.0
    reason: str = "normal"
    last_updated: float = 0.0
    full_stop_active: bool = False
    emergency_level: int = 0

    # 캐시 유효 시간 (EventBus 이벤트 누락 대비 폴백)
    max_cache_age_seconds: float = 30.0

    def is_stale(self) -> bool:
        """캐시가 오래되었는지 확인 (Fail-safe)."""
        import time
        return (time.time() - self.last_updated) > self.max_cache_age_seconds


class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def __init__(
        self,
        config: BackoffConfig | None = None,
        throttle_getter: Callable[[], Any] | None = None,
        enable_push_cache: bool = True,  # v2.0: 기본 활성화
    ):
        super().__init__(config)
        self._throttle_getter = throttle_getter
        self._enable_push_cache = enable_push_cache

        # v2.0: 푸시 기반 캐시 (기본 활성화)
        self._state_cache = PushBasedThrottleStateCache()
        if enable_push_cache:
            self._subscribe_throttle_events()

    def _subscribe_throttle_events(self) -> None:
        """Throttle 상태 변경 이벤트 구독 (기본 활성화)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.THROTTLE_LIMIT_CHANGED, self._on_throttle_changed)
            bus.subscribe(EventType.THROTTLE_SLA_WARNING, self._on_sla_warning)
            bus.subscribe(EventType.THROTTLE_SLA_CRITICAL, self._on_sla_critical)

            logger.debug("[ThrottleAwareBackoff] EventBus subscription enabled")
        except Exception as e:
            logger.warning(f"[ThrottleAwareBackoff] EventBus subscription failed: {e}")
            self._enable_push_cache = False  # 폴백: 직접 조회 모드

    def _on_throttle_changed(self, event) -> None:
        """Throttle limit 변경 시 캐시 업데이트."""
        import time
        data = event.data if hasattr(event, "data") else event
        reason = data.get("reason", "")

        self._state_cache.last_updated = time.time()
        self._state_cache.full_stop_active = data.get("full_stop", False)

        if self._state_cache.full_stop_active:
            self._state_cache.multiplier = float("inf")
            self._state_cache.reason = "full_stop_active"
        elif "emergency" in reason:
            level = data.get("emergency_level", 1)
            self._state_cache.emergency_level = level
            self._state_cache.multiplier = 4.0 if level >= 3 else 2.5
            self._state_cache.reason = f"emergency_level_{level}"
        elif "sla_critical" in reason:
            self._state_cache.multiplier = 2.0
            self._state_cache.reason = "sla_critical"
        elif "sla_warning" in reason:
            self._state_cache.multiplier = 1.5
            self._state_cache.reason = "sla_warning"
        else:
            self._state_cache.multiplier = 1.0
            self._state_cache.reason = "normal"

    def _get_throttle_state_cached(self) -> tuple[float, str]:
        """캐시된 상태 반환 (stale 시 직접 조회 폴백)."""
        if self._enable_push_cache and not self._state_cache.is_stale():
            return self._state_cache.multiplier, self._state_cache.reason

        # 폴백: 직접 조회
        state = self._get_throttle_state()
        if state is None:
            return 1.0, "throttle_unavailable"

        multiplier = self._calculate_multiplier(state)
        reason = self._determine_reason(state)
        return multiplier, reason
```

**네이밍 선택**: `PushBasedThrottleStateCache`
- **이유**:
  - `PushBased` - EventBus가 상태를 "푸시"하는 구조 명확히 표현 (Pull 기반과 구분)
  - 프로젝트 내 `XxxCache` 패턴 존재 (`fallback_cache_ttl_seconds`)

---

### 9.3 글로벌 Throttle 상태 공유 (리뷰 #4)

**문제점**: `AdaptiveThrottle`은 프로세스 로컬 싱글톤 → Pod 간 상태 불일치

**코드 근거**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L2219-L2235)
```python
_global_adaptive_throttle: AdaptiveThrottle | None = None  # 프로세스 로컬
```

**수정 제안**: Redis 기반 클러스터 전체 상태 공유 옵션

```python
@dataclass
class GlobalThrottleState:
    """
    클러스터 전체 Throttle 상태 (Redis 저장).

    네이밍 이유: "Global" - 프로젝트 내 Global 접두사 사용
               (예: namespace_emergency/tracker.py의 GLOBAL_NAMESPACE)
    """

    cluster_avg_rtt_ms: float = 0.0
    cluster_emergency_level: int = 0
    cluster_sla_warning_count: int = 0
    cluster_sla_critical_count: int = 0
    reporting_pod_count: int = 0
    last_updated: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cluster_avg_rtt_ms": self.cluster_avg_rtt_ms,
            "cluster_emergency_level": self.cluster_emergency_level,
            "cluster_sla_warning_count": self.cluster_sla_warning_count,
            "cluster_sla_critical_count": self.cluster_sla_critical_count,
            "reporting_pod_count": self.reporting_pod_count,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalThrottleState":
        return cls(**data)


class GlobalThrottleStateManager:
    """
    Redis 기반 글로벌 Throttle 상태 관리자.

    외부 API 공통 호출 시 클러스터 전체의 평균 부하를 참조하여
    재시도 강도를 조절합니다.
    """

    REDIS_KEY = "selfhealing:throttle:global_state"
    STATE_TTL_SECONDS = 60

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            try:
                from selfhealing.adapters.cache import get_redis_client
                self._redis = get_redis_client()
            except Exception:
                return None
        return self._redis

    def report_local_state(self, local_state: ThrottleState, pod_id: str) -> None:
        """로컬 상태를 글로벌에 보고."""
        if not self.redis:
            return

        try:
            import json
            import time

            # 개별 Pod 상태 저장
            pod_key = f"{self.REDIS_KEY}:pod:{pod_id}"
            self.redis.setex(
                pod_key,
                self.STATE_TTL_SECONDS,
                json.dumps({
                    "emergency_level": local_state.emergency_level,
                    "sla_warning": local_state.sla_warning_active,
                    "sla_critical": local_state.sla_critical_active,
                    "timestamp": time.time(),
                })
            )
        except Exception as e:
            logger.debug(f"[GlobalThrottleState] Failed to report: {e}")

    def get_global_state(self) -> GlobalThrottleState | None:
        """클러스터 전체 상태 조회."""
        if not self.redis:
            return None

        try:
            import json
            import time

            # 모든 Pod 상태 조회
            pod_keys = self.redis.keys(f"{self.REDIS_KEY}:pod:*")
            if not pod_keys:
                return None

            total_emergency = 0
            warning_count = 0
            critical_count = 0

            for key in pod_keys:
                data = self.redis.get(key)
                if data:
                    pod_state = json.loads(data)
                    total_emergency += pod_state.get("emergency_level", 0)
                    if pod_state.get("sla_warning"):
                        warning_count += 1
                    if pod_state.get("sla_critical"):
                        critical_count += 1

            pod_count = len(pod_keys)
            return GlobalThrottleState(
                cluster_emergency_level=total_emergency // pod_count if pod_count > 0 else 0,
                cluster_sla_warning_count=warning_count,
                cluster_sla_critical_count=critical_count,
                reporting_pod_count=pod_count,
                last_updated=time.time(),
            )
        except Exception as e:
            logger.debug(f"[GlobalThrottleState] Failed to get: {e}")
            return None


class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def __init__(
        self,
        config: BackoffConfig | None = None,
        throttle_getter: Callable[[], Any] | None = None,
        enable_push_cache: bool = True,
        use_global_state: bool = False,  # v2.0: 글로벌 상태 사용 옵션
    ):
        # ... 기존 코드 ...
        self._use_global_state = use_global_state
        self._global_state_manager = GlobalThrottleStateManager() if use_global_state else None

    def _get_effective_multiplier(self) -> tuple[float, str]:
        """로컬 또는 글로벌 상태 기반 배율 계산."""
        if self._use_global_state and self._global_state_manager:
            global_state = self._global_state_manager.get_global_state()
            if global_state:
                return self._calculate_global_multiplier(global_state)

        # 폴백: 로컬 상태
        return self._get_throttle_state_cached()

    def _calculate_global_multiplier(self, state: GlobalThrottleState) -> tuple[float, str]:
        """글로벌 상태 기반 배율 계산."""
        # 클러스터 과반수가 SLA Critical이면 2.0x
        if state.cluster_sla_critical_count > state.reporting_pod_count / 2:
            return 2.0, "cluster_sla_critical"

        # 클러스터 평균 Emergency Level 기반
        if state.cluster_emergency_level >= 3:
            return 4.0, "cluster_emergency_level_3"
        elif state.cluster_emergency_level > 0:
            return 2.5, f"cluster_emergency_level_{state.cluster_emergency_level}"

        return 1.0, "cluster_normal"
```

**네이밍 선택**: `GlobalThrottleState`, `GlobalThrottleStateManager`
- **이유**: 프로젝트 내 `GLOBAL_NAMESPACE` 상수 사용 패턴 존재 (namespace_emergency/tracker.py)

---

### 9.4 도메인별 Throttle 매핑 (리뷰 #5)

**문제점**: 전역 싱글톤 사용 → 서비스 간 간섭

**코드 근거**: `ThrottleRegistry`가 **이미 존재**합니다.

[registry.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/registry.py#L121-L140)
```python
class ThrottleRegistry:
    def get_throttle(self, service_name: str) -> AdaptiveThrottle:
        """서비스별 Throttle 인스턴스 가져오기 (없으면 생성)."""
        with self._throttle_lock:
            if service_name not in self._throttles:
                self._create_throttle(service_name)
            return self._throttles[service_name].throttle
```

**수정 제안**: `ThrottleAwareBackoffCalculator`에서 `ThrottleRegistry` 연동

```python
class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def __init__(
        self,
        config: BackoffConfig | None = None,
        throttle_getter: Callable[[], Any] | None = None,
        enable_push_cache: bool = True,
        use_global_state: bool = False,
        service_name: str = "default",  # v2.0: 서비스별 Throttle 지정
    ):
        super().__init__(config)
        self._throttle_getter = throttle_getter
        self._service_name = service_name
        # ...

    def _get_throttle(self):
        """서비스별 AdaptiveThrottle 인스턴스 획득."""
        if self._throttle_getter:
            return self._throttle_getter()

        # v2.0: 서비스별 Throttle 사용
        if self._service_name != "default":
            try:
                from selfhealing.services.throttle.registry import get_throttle_registry
                return get_throttle_registry().get_throttle(self._service_name)
            except Exception as e:
                logger.debug(f"[ThrottleAwareBackoff] Registry lookup failed: {e}")

        # 폴백: 전역 싱글톤
        try:
            from selfhealing.services.throttle.adaptive import get_adaptive_throttle
            return get_adaptive_throttle()
        except ImportError:
            return None
        except Exception:
            return None


class RetryHandler:
    def __init__(
        self,
        config: RetryConfig | None = None,
        domain: str = "default",
        rate_limit_coordinator: RateLimitCoordinator | None = None,
        throttle_aware: bool = True,
        service_name: str | None = None,  # v2.0: 서비스별 Throttle 지정
    ):
        self.config = config or RetryConfig.from_settings(domain)

        # v2.0: 서비스명 미지정 시 domain 사용
        effective_service_name = service_name or domain

        if throttle_aware:
            from .backoff_calculator import ThrottleAwareBackoffCalculator
            self.backoff = ThrottleAwareBackoffCalculator(
                BackoffConfig(
                    base=self.config.backoff_base,
                    max_delay=self.config.backoff_max,
                    jitter_percent=self.config.jitter_percent,
                ),
                service_name=effective_service_name,
            )
        else:
            # ... 기존 코드 ...
```

**네이밍 선택**: `service_name` 파라미터
- **이유**: `ThrottleRegistry.get_throttle(service_name)` 시그니처와 일치

---

### 9.5 429/백오프 max() 방식 (리뷰 #6)

**문제점**: 순차 합산 시 대기 시간이 비현실적으로 길어짐

**코드 근거**: [retry_handler.py](../../packages/selfhealing-python/src/selfhealing/services/retry_handler.py#L450)
```python
self._wait_for_rate_limit()  # ① 429 쿨다운 대기 (실제 sleep)
# ...
delay = self.get_next_delay(attempt)  # ② Backoff 계산
```

**수정 제안**: 두 지연 시간 중 큰 값 선택

```python
class RetryHandler:
    def get_combined_delay(self, attempt: int) -> int:
        """
        429 쿨다운과 Throttle 백오프 중 긴 값 반환.

        네이밍 이유: "combined" - 두 지연을 통합한다는 의미
                   "delay" - 기존 get_next_delay와 일관성

        v2.0: 합산 대신 max() 선택으로 사용자 체감 대기 시간 개선
        """
        throttle_delay = self.get_next_delay(attempt)

        # Full Stop 신호는 그대로 전달
        if throttle_delay < 0:
            return throttle_delay

        # 429 쿨다운 남은 시간 조회 (대기하지 않고 확인만)
        coordinator = self.rate_limit_coordinator
        if coordinator:
            try:
                state = coordinator._storage.get_state(self._rate_limit_key)
                if state.is_in_cooldown:
                    rate_limit_delay = int(state.remaining_cooldown)

                    # v2.0: max() 선택
                    combined = max(rate_limit_delay, throttle_delay)

                    if combined != throttle_delay:
                        logger.info(
                            f"[RetryHandler] Using 429 cooldown ({rate_limit_delay}s) "
                            f"over throttle backoff ({throttle_delay}s)"
                        )

                    return combined
            except Exception as e:
                logger.debug(f"[RetryHandler] Rate limit state check failed: {e}")

        return throttle_delay

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> RetryResult:
        # ...
        while attempt < self.config.max_attempts:
            attempt += 1

            # v2.0: 통합된 대기 시간 사용 (기존 _wait_for_rate_limit 대체)
            # self._wait_for_rate_limit()  # 제거

            try:
                result = func(*args, **kwargs)
                # ...
            except Exception as e:
                # ...
                if self.should_retry(e, attempt):
                    delay = self.get_combined_delay(attempt)  # v2.0

                    if delay < 0:
                        break  # Full Stop

                    # v2.0: 통합된 지연 시간으로 대기
                    time.sleep(delay)
                    continue
                else:
                    break
```

**네이밍 선택**: `get_combined_delay`
- **이유**:
  - `combined` - 429 쿨다운과 Throttle 백오프를 "통합"한다는 의미
  - `get_*_delay` 패턴 - 기존 `get_next_delay`와 일관성

---

### 9.6 에러 예산 위기 시 배율 적용 (리뷰 #7)

**문제점**: `ErrorBudgetGate`가 차단만 지원, Soft-Landing 전략 부재

**코드 근거**: [retry_handler.py](../../packages/selfhealing-python/src/selfhealing/services/retry_handler.py#L395-L411)
```python
gate_result = self._check_error_budget_gate()
if gate_result is not None and not gate_result.allowed:
    return RetryResult(action=RetryAction.ABORT, ...)  # 완전 차단만
```

**수정 제안**: WARNING 상태에서도 배율 적용 (Soft-Landing)

```python
class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def _calculate_multiplier(self, state: ThrottleState) -> float:
        """상태 기반 Backoff 배율 계산 (v2.0: Error Budget 연동 추가)."""

        # v2.0: 에러 예산 CRITICAL/WARNING 시 Throttle 상태와 무관하게 3.0x
        if self._check_error_budget_critical_or_warning():
            return self.BACKOFF_MULTIPLIERS["error_budget_critical"]  # 3.0

        # Full Stop: 최대 배율
        if state.full_stop_active:
            return float("inf")

        # ... 기존 로직 ...

    def _check_error_budget_critical_or_warning(self) -> bool:
        """
        ErrorBudgetGate CRITICAL 또는 WARNING 상태 확인.

        v2.0: 차단 직전 단계에서도 재시도 빈도를 낮추는 Soft-Landing 전략
        """
        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate
            from selfhealing.services.error_budget_gate.gate import GateStatus

            gate = get_error_budget_gate()
            result = gate.check()

            # WARNING 또는 BLOCKED 상태면 배율 적용
            return result.status in (GateStatus.WARNING, GateStatus.BLOCKED)
        except ImportError:
            return False
        except Exception as e:
            logger.debug(f"[ThrottleAwareBackoff] ErrorBudgetGate check failed: {e}")
            return False
```

---

### 9.7 DLQ 에스컬레이션 페이로드 강화 (리뷰 #8)

**수정 제안**: 재시도 정보를 DLQ 메타데이터에 포함

```python
class RetryHandler:
    def _move_to_dlq(
        self,
        last_error: Exception | None,
        attempt: int,
        context: dict[str, Any] | None,
        retry_history: list[dict[str, Any]],
        backoff_info: dict[str, Any] | None = None,  # v2.0
    ) -> int | None:
        from .dlq_service import store_to_dlq

        try:
            context = context or {}
            error_type = type(last_error).__name__ if last_error else "Unknown"

            # v2.0: Throttle 관련 정보 추가
            metadata = {
                "retry_history": retry_history,
                "max_attempts": self.config.max_attempts,
                "domain": self.config.domain,
                "final_attempt": attempt,
            }

            if backoff_info:
                metadata.update({
                    "final_delay_seconds": backoff_info.get("delay"),
                    "backoff_multiplier": backoff_info.get("multiplier"),
                    "throttle_reason": backoff_info.get("reason"),
                    "throttle_aware_enabled": self._throttle_aware,
                })

            result = store_to_dlq(
                # ... 기존 파라미터 ...
                metadata=metadata,
            )
            # ...

    def execute(self, ...):
        # ...
        # v2.0: 마지막 백오프 정보 저장
        last_backoff_info = None

        while attempt < self.config.max_attempts:
            # ...
            except Exception as e:
                if self.should_retry(e, attempt):
                    delay, multiplier, reason = self.backoff.calculate_with_throttle_context(attempt)

                    # v2.0: DLQ 이동 시 사용할 정보 저장
                    last_backoff_info = {
                        "delay": delay,
                        "multiplier": multiplier,
                        "reason": reason,
                    }
                    # ...

        # DLQ 이동
        if self.config.enable_dlq:
            dlq_id = self._move_to_dlq(
                # ...
                backoff_info=last_backoff_info,  # v2.0
            )
```

---

### 9.8 우선순위별 차등 대우 (리뷰 #9)

**수정 제안**: CRITICAL 티어에 FULL_STOP grace retry 1회 허용

```python
@dataclass
class RetryConfig:
    # ... 기존 필드 ...

    # v2.0: 우선순위 기반 생존 로직
    critical_tier_full_stop_grace_retries: int = 1
    """CRITICAL 티어 요청은 FULL_STOP에서도 추가 재시도 허용 횟수"""

    critical_tier_full_stop_max_delay: int = 720
    """CRITICAL 티어 FULL_STOP 시 최대 대기 시간 (12분)"""


class RetryHandler:
    def get_next_delay(self, attempt: int, is_critical_tier: bool = False) -> int:
        """
        v2.0: 우선순위 기반 생존 로직 추가

        Args:
            attempt: 현재 시도 횟수
            is_critical_tier: CRITICAL 티어 요청 여부

        Returns:
            지연 시간 (초). -1은 즉시 DLQ 이동 신호.
        """
        if self._throttle_aware and hasattr(self.backoff, "calculate_with_throttle_context"):
            delay, multiplier, reason = self.backoff.calculate_with_throttle_context(attempt)

            # Full Stop 처리
            if delay < 0:
                # v2.0: CRITICAL 티어는 grace retry 허용
                if is_critical_tier:
                    grace_attempts = self.config.critical_tier_full_stop_grace_retries
                    grace_attempt_number = attempt - self.config.max_attempts

                    if grace_attempt_number <= grace_attempts:
                        logger.warning(
                            f"[RetryHandler] CRITICAL tier grace retry "
                            f"{grace_attempt_number}/{grace_attempts} during FULL_STOP"
                        )
                        return self.config.critical_tier_full_stop_max_delay

                logger.warning(
                    f"[RetryHandler] Full Stop active, skipping retry for attempt {attempt}"
                )
                return -1

            # ...

        return self.backoff.calculate(attempt)

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: dict[str, Any] | None = None,
        is_critical_tier: bool = False,  # v2.0
        **kwargs: Any,
    ) -> RetryResult:
        # ...

        # v2.0: CRITICAL 티어는 grace retry 포함한 최대 시도 횟수
        effective_max_attempts = self.config.max_attempts
        if is_critical_tier:
            effective_max_attempts += self.config.critical_tier_full_stop_grace_retries

        while attempt < effective_max_attempts:
            # ...
            delay = self.get_next_delay(attempt, is_critical_tier=is_critical_tier)
```

**네이밍 선택**: `critical_tier_full_stop_grace_retries`
- **이유**:
  - `critical_tier` - 프로젝트 내 tiering 개념과 일치 (test_tiering.py)
  - `grace_retries` - "유예" 재시도라는 의미 명확

---

### 9.9 백오프 확장 메트릭 (리뷰 #10)

**수정 제안**: `definitions.py`에 공식 등록

```python
# definitions.py 추가

# =============================================================================
# Retry Backoff Extended Metrics (192번 문서 v2.0)
# =============================================================================

retry_backoff_multiplier = get_or_create_histogram(
    "selfhealing_retry_backoff_multiplier",
    "Backoff multiplier applied due to throttle state",
    ["domain", "reason"],
    buckets=(1.0, 1.5, 2.0, 2.5, 3.0, 4.0),
)

retry_throttle_full_stop_skips_total = get_or_create_counter(
    "selfhealing_retry_throttle_full_stop_skips_total",
    "Total retries skipped due to throttle full stop",
    ["domain"],
)

retry_backoff_original_seconds = get_or_create_histogram(
    "selfhealing_retry_backoff_original_seconds",
    "Original backoff delay before throttle multiplier",
    ["domain"],
    buckets=(1, 4, 16, 64, 180),
)

retry_backoff_adjusted_seconds = get_or_create_histogram(
    "selfhealing_retry_backoff_adjusted_seconds",
    "Adjusted backoff delay after throttle multiplier",
    ["domain"],
    buckets=(1, 4, 16, 64, 180, 360, 720),
)

retry_critical_tier_grace_retries_total = get_or_create_counter(
    "selfhealing_retry_critical_tier_grace_total",
    "Total CRITICAL tier grace retries during FULL_STOP",
    ["domain"],
)
```

**메트릭 기록 코드**:

```python
class ThrottleAwareBackoffCalculator(BackoffCalculator):
    def calculate_with_throttle_context(self, attempt: int, with_jitter: bool = True) -> tuple[int, float, str]:
        base_delay = self.calculate(attempt, with_jitter)

        # ... 기존 로직 ...

        # v2.0: 메트릭 기록
        self._record_backoff_metrics(
            domain=self._service_name,
            original_delay=base_delay,
            adjusted_delay=adjusted_delay,
            multiplier=multiplier,
            reason=reason,
        )

        return adjusted_delay, multiplier, reason

    def _record_backoff_metrics(
        self,
        domain: str,
        original_delay: int,
        adjusted_delay: int,
        multiplier: float,
        reason: str,
    ) -> None:
        """v2.0: Prometheus 메트릭 기록."""
        try:
            from selfhealing.services.metrics.definitions import (
                retry_backoff_multiplier,
                retry_backoff_original_seconds,
                retry_backoff_adjusted_seconds,
                retry_throttle_full_stop_skips_total,
            )

            retry_backoff_multiplier.labels(domain=domain, reason=reason).observe(multiplier)
            retry_backoff_original_seconds.labels(domain=domain).observe(original_delay)
            retry_backoff_adjusted_seconds.labels(domain=domain).observe(adjusted_delay)

            if multiplier == float("inf"):
                retry_throttle_full_stop_skips_total.labels(domain=domain).inc()

        except ImportError:
            pass  # Fail-Open
        except Exception as e:
            logger.debug(f"[ThrottleAwareBackoff] Metrics recording failed: {e}")
```

---

### 9.10 복합 시나리오 테스트 (리뷰 #11)

**수정 제안**: Hedging + Bulkhead + Backoff 통합 테스트

```python
# tests/integration/selfhealing/test_resilience_end_to_end.py

import pytest
import threading
import time
from unittest.mock import Mock, patch


class TestEndToEndResilienceScenario:
    """
    Hedging + Bulkhead + Throttle Backoff 복합 시나리오 테스트.

    검증 목표:
    1. 여러 패턴 중첩 시 Deadlock 없음
    2. 복구 루프 무한 반복 없음
    3. 타임아웃 내 정상 완료
    """

    @pytest.fixture
    def setup_high_load_environment(self):
        """높은 부하 환경 설정."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.resilience.bulkhead.registry import get_bulkhead_registry

        reset_adaptive_throttle()
        throttle = get_adaptive_throttle()

        # Emergency LEVEL_3 설정
        throttle.adjust_for_emergency(level=3)

        # Bulkhead 80% 점유
        registry = get_bulkhead_registry()
        bulkhead = registry.get_or_create("test_service", max_concurrent=10)
        # 8개 슬롯 점유
        for _ in range(8):
            bulkhead.try_acquire()

        yield throttle, bulkhead

        # 정리
        reset_adaptive_throttle()
        bulkhead.reset()

    @pytest.mark.timeout(30)  # 30초 타임아웃으로 Deadlock 감지
    def test_hedging_with_throttle_backoff_under_high_load(self, setup_high_load_environment):
        """높은 부하에서 Hedging + Throttle Backoff 동시 작동."""
        throttle, bulkhead = setup_high_load_environment

        from selfhealing.core.hedging import HedgingStrategy, HedgingConfig
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig

        # Hedging 설정
        hedging_config = HedgingConfig(
            mode="delayed",
            delay=0.5,
            bulkhead_name="test_service",
        )
        strategy = HedgingStrategy(config=hedging_config)

        # RetryHandler 설정 (Throttle-aware)
        retry_config = RetryConfig(max_attempts=3, throttle_aware=True)
        handler = RetryHandler(config=retry_config)

        call_count = 0
        def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Transient error")
            return "success"

        # 실행: Hedging 내에서 RetryHandler 사용
        def primary_with_retry():
            return handler.execute(flaky_operation)

        result = strategy.execute(
            primary_fn=primary_with_retry,
            default_value=None,
        )

        # 검증
        # 1. Deadlock 없이 완료 (pytest.timeout이 보장)
        # 2. Backoff가 4배 증가 (Emergency LEVEL_3)
        assert result.success or result.used_fallback

    @pytest.mark.timeout(60)
    def test_no_deadlock_when_all_patterns_active(self):
        """모든 패턴 활성화 시 Deadlock 없음 검증."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig
        from selfhealing.core.hedging import HedgingExecutor, HedgingConfig, HedgingCandidate

        reset_adaptive_throttle()
        throttle = get_adaptive_throttle()
        cb_service = get_circuit_breaker_service()

        # 모든 패턴 활성화
        throttle.adjust_for_emergency(level=2)
        cb_service.force_open("test_service", reason="Test")

        handler = RetryHandler(
            config=RetryConfig(max_attempts=2, throttle_aware=True),
            domain="test_service",
        )

        results = []
        errors = []

        def worker():
            try:
                def always_fail():
                    raise Exception("Service unavailable")

                result = handler.execute(always_fail)
                results.append(result)
            except Exception as e:
                errors.append(e)

        # 동시 실행
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 검증: 모든 스레드 완료 (Deadlock 없음)
        assert len(results) + len(errors) == 5
        assert all(not t.is_alive() for t in threads)

    @pytest.mark.timeout(120)
    def test_recovery_loop_stability(self):
        """복구 루프가 무한히 반복되지 않는지 검증."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle

        reset_adaptive_throttle()
        throttle = get_adaptive_throttle()

        # FULL_STOP → Recovery → FULL_STOP 사이클 시뮬레이션
        cycle_count = 0
        max_cycles = 3

        for _ in range(10):  # 최대 10회 시도
            # FULL_STOP 활성화
            throttle.adjust_for_emergency(level=3)
            throttle.activate_full_stop("test_cycle")

            # Recovery 시도
            throttle.deactivate_full_stop()
            throttle.adjust_for_emergency(level=0)

            # 다시 FULL_STOP 조건 발생 여부 확인
            is_full_stop, _ = throttle.check_full_stop_conditions()
            if is_full_stop:
                cycle_count += 1

            if cycle_count >= max_cycles:
                break

            time.sleep(0.1)

        # 검증: 3회 이상 사이클 반복되지 않음
        assert cycle_count < max_cycles, f"Recovery loop repeated {cycle_count} times"
```

---

### 9.11 Adaptive Retry Budget (리뷰 #12)

**수정 제안**: 재시도 비율 10% 제한 + Throttle 연동 동적 삭감

```python
@dataclass
class AdaptiveRetryBudget:
    """
    적응형 재시도 예산 관리자.

    전체 요청 대비 재시도 비율을 관리하여
    Self-DDoS를 방지합니다.

    네이밍 이유: "Adaptive" - AdaptiveThrottle과 일관성
               "RetryBudget" - ErrorBudget 개념과 유사
    """

    max_retry_ratio: float = 0.10  # 기본 10%
    current_retry_count: int = 0
    current_total_count: int = 0
    window_seconds: int = 60
    _window_start: float = 0.0

    # Throttle 연동 동적 삭감 비율
    THROTTLE_BUDGET_RATIOS = {
        "normal": 0.10,           # 10%
        "sla_warning": 0.07,      # 7%
        "sla_critical": 0.05,     # 5%
        "emergency_1_2": 0.03,    # 3%
        "emergency_3": 0.01,      # 1%
        "full_stop": 0.0,         # 0% (재시도 금지)
    }

    def should_allow_retry(self) -> bool:
        """재시도 허용 여부."""
        self._maybe_reset_window()

        if self.current_total_count == 0:
            return True

        current_ratio = self.current_retry_count / self.current_total_count
        return current_ratio < self.max_retry_ratio

    def record_request(self, is_retry: bool = False) -> None:
        """요청 기록."""
        self._maybe_reset_window()
        self.current_total_count += 1
        if is_retry:
            self.current_retry_count += 1

    def _maybe_reset_window(self) -> None:
        """윈도우 초과 시 리셋."""
        import time
        now = time.time()
        if now - self._window_start > self.window_seconds:
            self.current_retry_count = 0
            self.current_total_count = 0
            self._window_start = now

    def adjust_budget_for_throttle_state(self, throttle_reason: str) -> None:
        """Throttle 상태에 따라 예산 동적 조정."""
        if throttle_reason in self.THROTTLE_BUDGET_RATIOS:
            self.max_retry_ratio = self.THROTTLE_BUDGET_RATIOS[throttle_reason]
        else:
            # 알 수 없는 상태면 보수적으로 5%
            self.max_retry_ratio = 0.05

    def get_stats(self) -> dict:
        """현재 상태 통계."""
        return {
            "max_retry_ratio": self.max_retry_ratio,
            "current_retry_count": self.current_retry_count,
            "current_total_count": self.current_total_count,
            "current_ratio": (
                self.current_retry_count / self.current_total_count
                if self.current_total_count > 0 else 0.0
            ),
            "budget_remaining": max(
                0,
                int(self.current_total_count * self.max_retry_ratio) - self.current_retry_count
            ),
        }


class RetryHandler:
    def __init__(self, ...):
        # ...

        # v2.0: Adaptive Retry Budget
        self._retry_budget = AdaptiveRetryBudget()

    def execute(self, ...):
        # ...
        while attempt < self.config.max_attempts:
            attempt += 1

            # v2.0: 요청 기록
            self._retry_budget.record_request(is_retry=(attempt > 1))

            # v2.0: 재시도 예산 확인
            if attempt > 1 and not self._retry_budget.should_allow_retry():
                logger.warning(
                    f"[RetryHandler] Retry budget exhausted: "
                    f"{self._retry_budget.get_stats()}"
                )
                break

            try:
                result = func(*args, **kwargs)
                # ...
            except Exception as e:
                if self.should_retry(e, attempt):
                    delay, multiplier, reason = self.backoff.calculate_with_throttle_context(attempt)

                    # v2.0: Throttle 상태에 따라 예산 조정
                    self._retry_budget.adjust_budget_for_throttle_state(reason)

                    # ...
```

**네이밍 선택**: `AdaptiveRetryBudget`
- **이유**:
  - `Adaptive` - `AdaptiveThrottle`과 일관성, 동적 조정 의미
  - `RetryBudget` - `ErrorBudget` 개념과 유사한 "예산" 개념

---

### 9.12 CB 자동 오픈 (리뷰 #13) - 기존 구현 확인

**결론**: **이미 구현되어 있음**. 추가 구현 불필요.

**코드 근거 1**: [protection.py](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/protection.py#L43-L85)

```python
def record_rate_limit_response(self, service_name: str) -> CircuitBreakerResult | None:
    """
    Record a 429 rate limit response and check for cascade.
    If a rate limit cascade is detected, the circuit breaker will
    automatically open to prevent self-DDoS.
    """
    # ...
    if rate_limit_count >= self.config.rate_limit_cascade_threshold:
        logger.warning(
            f"[CircuitBreaker] Rate limit cascade detected for '{service_name}': "
            f"{rate_limit_count} 429s in {self.config.rate_limit_cascade_window_seconds}s"
        )

        # Auto-open circuit breaker
        result = self.force_open(
            service_name=service_name,
            reason=f"Rate limit cascade detected ({rate_limit_count} 429s in "
                   f"{self.config.rate_limit_cascade_window_seconds}s)",
        )
```

**코드 근거 2**: [config.py](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/config.py#L52-L55)

```python
# Fallback strategy when CB is open
# Options: "block" (default), "cache", "dlq", "default_response"
fallback_strategy: str = "block"
```

**코드 근거 3**: [fallback_strategy.py](../../packages/selfhealing-python/src/selfhealing/core/fallback_strategy.py#L25-L32)

```python
class FallbackMode(str, Enum):
    """Fallback behavior modes"""
    FAIL_FAST = "fail_fast"      # 즉시 실패
    USE_CACHE = "use_cache"      # 캐시된 값 사용
    USE_DEFAULT = "use_default"  # 기본값 사용
    DEGRADE_GRACEFULLY = "degrade"  # 기능 축소
    RETRY_ALTERNATIVE = "retry_alt"  # 대체 경로 시도
    HEDGE = "hedge"              # 병렬 헷징으로 인한 대체 응답
```

**Fallback 전략 요약**:

| 유형 | 옵션 | 설명 |
|------|------|------|
| **CB Fallback** (3가지) | `block` | 요청 차단 (기본값) |
| | `cache` | Stale 캐시 데이터 반환 |
| | `dlq` | DLQ에 큐잉 후 나중에 재시도 |
| | `default_response` | 정적 기본 응답 반환 |
| **FallbackMode** (6가지) | `FAIL_FAST` | 즉시 실패 |
| | `USE_CACHE` | 캐시 사용 |
| | `USE_DEFAULT` | 기본값 사용 |
| | `DEGRADE_GRACEFULLY` | 기능 축소 |
| | `RETRY_ALTERNATIVE` | 대체 경로 |
| | `HEDGE` | 헷징 대체 응답 |

**따라서**: 백오프가 특정 임계치 이상 시 CB 자동 오픈 로직은 **추가 불필요**. 기존 Rate Limit Cascade Detection이 동일한 역할을 수행하며, 자동 오픈 후 다양한 Fallback 전략이 적용됩니다.

---

## 10. v2.0 구현 체크리스트 (확장)

### 10.1 기존 체크리스트

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

### 10.2 v2.0 추가 체크리스트

- [ ] `SYSTEM_TIMEOUT_SECONDS` 상수 정의 (§9.1)
- [ ] `PushBasedThrottleStateCache` 클래스 구현 (§9.2)
- [ ] EventBus 구독 기본 활성화 (§9.2)
- [ ] `GlobalThrottleState` dataclass 정의 (§9.3)
- [ ] `GlobalThrottleStateManager` 클래스 구현 (§9.3)
- [ ] `service_name` 파라미터 추가 및 `ThrottleRegistry` 연동 (§9.4)
- [ ] `get_combined_delay()` 메서드 구현 (§9.5)
- [ ] `_check_error_budget_critical_or_warning()` 메서드 구현 (§9.6)
- [ ] DLQ 메타데이터 확장 (`backoff_info`) (§9.7)
- [ ] `critical_tier_full_stop_grace_retries` 설정 추가 (§9.8)
- [ ] `definitions.py`에 4개 메트릭 등록 (§9.9)
- [ ] `AdaptiveRetryBudget` 클래스 구현 (§9.11)
- [ ] End-to-End Resilience 테스트 작성 (§9.10)

---

## 11. 네이밍 컨벤션 요약

| 항목 | 선택된 네이밍 | 선택 이유 |
|------|-------------|----------|
| 시스템 타임아웃 | `SYSTEM_TIMEOUT_SECONDS` | `*_SECONDS` 접미사 패턴 일관성 |
| 푸시 캐시 | `PushBasedThrottleStateCache` | EventBus "푸시" 구조 명확화, `XxxCache` 패턴 |
| 글로벌 상태 | `GlobalThrottleState` | `GLOBAL_NAMESPACE` 상수 패턴 존재 |
| 통합 지연 | `get_combined_delay` | `get_*_delay` 패턴 일관성 |
| 우선순위 재시도 | `critical_tier_full_stop_grace_retries` | Tiering 개념 + grace 의미 명확 |
| 재시도 예산 | `AdaptiveRetryBudget` | `AdaptiveThrottle`, `ErrorBudget` 패턴 일관성 |
