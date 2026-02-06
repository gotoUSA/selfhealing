# 191. Error Budget Gate - AdaptiveThrottle 에러 예산 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**:
> - `selfhealing/services/throttle/adaptive.py` (Line 1367-1387)
> - `selfhealing/services/error_budget_gate/gate.py` (Line 1-824)
> - `selfhealing/services/error_budget/service.py` (Line 1-270)
> **우선순위**: 🟡 P2

---

## 1. 개요

본 문서는 `AdaptiveThrottle`과 `ErrorBudgetGate` 간의 연동 구현을 정의합니다.

### 1.1 연동 목적

| 목적 | 설명 |
|------|------|
| 에러 예산 기반 Limit 조정 | Error Budget 소진 시 Throttle limit 자동 감소 |
| Dual Gate 방어 | ErrorBudgetGate + Throttle 이중 보호 |
| Full Stop 3중 조건 참여 | LEVEL_3 + DB_CB_OPEN + **BUDGET_EXHAUSTED** |

### 1.2 현재 구현 상태

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L1367-L1387)

```python
def _check_error_budget_exhausted(self) -> bool:
    """
    Error Budget 소진 상태 확인.

    Returns:
        True if error budget is exhausted (0% or less)
    """
    try:
        from selfhealing.services.error_budget_service import (
            get_error_budget_service,
        )

        service = get_error_budget_service()
        status = service.get_budget_status()

        is_exhausted = status.budget_remaining_percent <= 0
        if is_exhausted:
            logger.debug(f"[AdaptiveThrottle] Budget exhausted: "
                        f"{status.budget_remaining_percent:.1f}%")
        return is_exhausted
    except ImportError:
        logger.debug("[AdaptiveThrottle] ErrorBudgetService not available")
        return False
    except Exception as e:
        logger.warning(f"[AdaptiveThrottle] Failed to check error budget: {e}")
        return False
```

### 1.3 문제점

| 문제 | 설명 |
|------|------|
| 단일 임계치 | `0%` 이하만 체크 (Full Stop 조건으로만 사용) |
| 점진적 대응 부재 | Warning/Critical 단계별 Limit 조정 없음 |
| 단방향 연동 | Throttle → ErrorBudget 조회만 (EventBus 미활용) |
| Rate Limit 미반영 | ErrorBudgetGate의 Rate Limit 정책 미적용 |

---

## 2. ErrorBudgetGate 현재 인터페이스

### 2.1 GateCheckResult 구조

**코드 위치**: [config.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/config.py#L98-L179)

```python
class GateStatus(str, Enum):
    OPEN = "open"           # 자동화 허용 (정상)
    WARNING = "warning"     # 자동화 허용 (경고)
    BLOCKED = "blocked"     # 자동화 차단
    DISABLED = "disabled"   # 게이트 비활성화
    FAIL_OPEN = "fail_open" # Fail-Open 모드
    FAIL_OPEN_RATE_LIMITED = "fail_open_rate_limited"


@dataclass
class GateCheckResult:
    allowed: bool
    status: GateStatus
    error_budget_percent: float | None = None
    threshold_percent: float | None = None
    reason: str = ""
    recommendation: str = ""
    fail_open_triggered: bool = False
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: datetime | None = None
```

### 2.2 ErrorBudgetGateConfig 기본값

**코드 위치**: [config.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/config.py#L18-L52)

```python
@dataclass
class ErrorBudgetGateConfig:
    enabled: bool = True
    critical_threshold_percent: float = 10.0   # 이 미만: 차단
    warning_threshold_percent: float = 20.0    # 이 미만: 경고
    fail_open: bool = True
    cache_ttl_seconds: int = 30
```

### 2.3 EventBus 이벤트

**코드 위치**: [gate.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py#L512-L570)

```python
def _emit_error_budget_critical_event(self, budget_percent: float) -> None:
    bus.emit(
        event_type=EventType.ERROR_BUDGET_CRITICAL,
        data={
            "budget_percent": budget_percent,
            "threshold": self._config.critical_threshold_percent,
            "status": "critical",
        },
        source="error_budget_gate",
        priority=EventPriority.CRITICAL,
    )

def _emit_error_budget_warning_event(self, budget_percent: float) -> None:
    bus.emit(
        event_type=EventType.ERROR_BUDGET_WARNING,
        data={...},
        source="error_budget_gate",
        priority=EventPriority.HIGH,
    )
```

---

## 3. 연동 아키텍처

### 3.1 시퀀스 다이어그램

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│AdaptiveThrottle│  │ErrorBudgetGate│   │ErrorBudgetService│  │   EventBus    │
└──────┬──────┘     └───────┬──────┘     └────────┬────────┘     └───────┬──────┘
       │                     │                     │                      │
       │ ① subscribe         │                     │                      │
       │─────────────────────┼────────────────────►│                      │
       │                     │                     │                      │
       │                     │ ② check()          │                      │
       │                     │────────────────────►│                      │
       │                     │                     │                      │
       │                     │ ③ budget_percent   │                      │
       │                     │◄────────────────────│                      │
       │                     │                     │                      │
       │                     │ ④ emit WARNING/CRITICAL                   │
       │                     │────────────────────────────────────────────►│
       │                     │                     │                      │
       │ ⑤ receive event    │                     │                      │
       │◄─────────────────────────────────────────────────────────────────│
       │                     │                     │                      │
       │ ⑥ adjust_limit()   │                     │                      │
       │──────────┐         │                     │                      │
       │          │         │                     │                      │
       │◄─────────┘         │                     │                      │
```

### 3.2 연동 지점

| 연동 지점 | 현재 상태 | 목표 상태 |
|-----------|----------|----------|
| Full Stop 조건 | `_check_error_budget_exhausted()` | 유지 |
| EventBus 구독 | 미구현 | `ERROR_BUDGET_WARNING`, `ERROR_BUDGET_CRITICAL` 구독 |
| 점진적 Limit 조정 | 미구현 | Budget 잔여량에 따른 단계별 감소 |
| Gate Check 통합 | 미구현 | `check()` 시 Gate 결과 반영 |

---

## 4. 구현 명세

### 4.1 ErrorBudget EventBus 구독 추가

**수정 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) `__init__`

```python
class AdaptiveThrottle(SlidingWindowThrottle):

    # ... 기존 코드 ...

    # =========================================================================
    # Error Budget 연동 상태
    # =========================================================================
    self._error_budget_limit_reduction_active: bool = False
    self._error_budget_multiplier: float = 1.0
    self._limit_before_error_budget_reduction: int = self.config.initial_limit

    # EventBus 구독 등록 (기존)
    self._subscribe_rate_limit_events()
    # Error Budget 이벤트 구독 추가
    self._subscribe_error_budget_events()
```

### 4.2 Error Budget 이벤트 구독 메서드

**추가 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
def _subscribe_error_budget_events(self) -> None:
    """Error Budget 이벤트 구독 등록 (Fail-Open)."""
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus

        bus = get_event_bus()

        # ERROR_BUDGET_WARNING 구독
        bus.subscribe(EventType.ERROR_BUDGET_WARNING, self._handle_error_budget_warning)

        # ERROR_BUDGET_CRITICAL 구독
        bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, self._handle_error_budget_critical)

        logger.info("[AdaptiveThrottle] Subscribed to error budget events")
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available for error budget subscription")
    except Exception as e:
        logger.warning(f"[AdaptiveThrottle] Failed to subscribe to error budget events: {e}")


def _handle_error_budget_warning(self, event) -> None:
    """
    Error Budget Warning 이벤트 처리.

    전략:
    - Warning 단계 (10-20%): Limit 20% 감소
    - Recovery Dampening 미적용 (경고 수준이므로 빠른 복구 허용)
    """
    event_data = event.data if hasattr(event, "data") else event

    budget_percent = event_data.get("budget_percent", 100.0)

    # Warning 상태 진입 시 limit 저장
    if not self._error_budget_limit_reduction_active:
        self._limit_before_error_budget_reduction = self._current_limit

    self._error_budget_limit_reduction_active = True
    self._error_budget_multiplier = 0.8  # 20% 감소

    previous_limit = self._current_limit
    new_limit = max(
        int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
        self.config.min_limit,
    )

    self.current_limit = new_limit

    logger.warning(
        f"[AdaptiveThrottle] Error budget WARNING: {budget_percent:.1f}%, "
        f"limit reduced: {previous_limit} → {new_limit} (×0.8)"
    )

    # 메트릭 기록
    _record_throttle_metrics(
        service=self._service_name,
        limit=new_limit,
        limit_change_direction="down",
        limit_change_trigger="error_budget_warning",
    )

    # 감사 로깅
    _record_audit_safe(
        action="throttle_error_budget_warning",
        old_limit=previous_limit,
        new_limit=new_limit,
        error_budget_percent=budget_percent,
        multiplier=self._error_budget_multiplier,
    )


def _handle_error_budget_critical(self, event) -> None:
    """
    Error Budget Critical 이벤트 처리.

    전략:
    - Critical 단계 (<10%): Limit 50% 감소
    - New Request 차단 고려 (min_limit에 가까워짐)
    """
    event_data = event.data if hasattr(event, "data") else event

    budget_percent = event_data.get("budget_percent", 100.0)

    # Critical 상태 진입 시 limit 저장 (Warning이 선행하지 않은 경우)
    if not self._error_budget_limit_reduction_active:
        self._limit_before_error_budget_reduction = self._current_limit

    self._error_budget_limit_reduction_active = True
    self._error_budget_multiplier = 0.5  # 50% 감소

    previous_limit = self._current_limit
    new_limit = max(
        int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
        self.config.min_limit,
    )

    self.current_limit = new_limit

    logger.error(
        f"[AdaptiveThrottle] Error budget CRITICAL: {budget_percent:.1f}%, "
        f"limit reduced: {previous_limit} → {new_limit} (×0.5)"
    )

    # 메트릭 기록
    _record_throttle_metrics(
        service=self._service_name,
        limit=new_limit,
        limit_change_direction="down",
        limit_change_trigger="error_budget_critical",
    )

    # EventBus 발행
    _emit_throttle_event(
        "THROTTLE_SLA_WARNING",
        {
            "trigger": "error_budget_critical",
            "budget_percent": budget_percent,
            "current_limit": new_limit,
            "previous_limit": previous_limit,
        },
        priority_name="CRITICAL",
    )

    # 감사 로깅
    _record_audit_safe(
        action="throttle_error_budget_critical",
        old_limit=previous_limit,
        new_limit=new_limit,
        error_budget_percent=budget_percent,
        multiplier=self._error_budget_multiplier,
    )
```

### 4.3 Error Budget 복구 처리

```python
def _subscribe_error_budget_events(self) -> None:
    # ... 기존 코드 ...

    # ERROR_BUDGET_RECOVERED 구독 (회복 시)
    bus.subscribe(EventType.ERROR_BUDGET_RECOVERED, self._handle_error_budget_recovered)


def _handle_error_budget_recovered(self, event) -> None:
    """
    Error Budget 회복 이벤트 처리.

    전략:
    - Recovery Dampening으로 점진적 복구 (80% → 90% → 100%)
    - Thundering Herd 방지
    """
    if not self._error_budget_limit_reduction_active:
        return

    event_data = event.data if hasattr(event, "data") else event
    budget_percent = event_data.get("budget_percent", 100.0)

    logger.info(
        f"[AdaptiveThrottle] Error budget recovered: {budget_percent:.1f}%, "
        f"starting recovery dampening"
    )

    # Recovery Dampening 시작
    self._error_budget_limit_reduction_active = False
    self._error_budget_multiplier = 1.0
    self._base_limit_before_emergency = self._limit_before_error_budget_reduction
    self.start_recovery_dampening()
```

### 4.4 check() 메서드 통합

**수정 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L1000-L1047)

```python
def check(self, key: str, tier_id: str = "standard") -> ThrottleResult:
    """
    Check if request is allowed with adaptive info and priority protection.

    Error Budget Gate 통합:
    - ERROR_BUDGET_CRITICAL 상태 시 non_essential 티어 추가 거부
    """
    # Check on Use 패턴: TTL 만료 시 Emergency 상태 동기화
    self.check_and_sync_emergency_state()

    # Error Budget Gate 체크 (Critical 상태 시 추가 제한)
    if self._error_budget_limit_reduction_active:
        if tier_id == "non_essential" and self._error_budget_multiplier <= 0.5:
            # Critical 상태에서 non_essential 요청 거부
            return ThrottleResult(
                allowed=False,
                current_count=0,
                limit=self._current_limit,
                remaining=0,
                reset_at=0,
                reason="error_budget_critical_non_essential_blocked",
            )

    # ... 기존 check 로직 ...
```

---

## 5. 연동 흐름도

### 5.1 Error Budget 단계별 Throttle 대응

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     Error Budget 잔여량별 대응 전략                       │
├─────────────────┬──────────────────┬──────────────────────────────────────┤
│  Budget 잔여량   │     Gate 상태     │         Throttle 대응               │
├─────────────────┼──────────────────┼──────────────────────────────────────┤
│    > 20%        │      OPEN        │  정상 운영 (limit 변동 없음)          │
├─────────────────┼──────────────────┼──────────────────────────────────────┤
│   10% ~ 20%     │     WARNING      │  Limit ×0.8 (20% 감소)               │
├─────────────────┼──────────────────┼──────────────────────────────────────┤
│    < 10%        │     CRITICAL     │  Limit ×0.5 (50% 감소)               │
│                 │                  │  + non_essential 티어 거부           │
├─────────────────┼──────────────────┼──────────────────────────────────────┤
│    ≤ 0%         │     BLOCKED      │  Full Stop 3중 조건 참여              │
│                 │                  │  (LEVEL_3 + DB_CB + BUDGET 시 limit=0)│
└─────────────────┴──────────────────┴──────────────────────────────────────┘
```

### 5.2 Min-Winner 정책 통합

```python
@property
def conservative_limit(self) -> int:
    """
    Min-Winner 정책 적용한 보수적 limit.

    RTT 기반 limit, 429 기반 limit, Error Budget 기반 limit 중
    가장 낮은 값 반환.
    """
    if not self._conservative_enabled:
        return self._current_limit

    # Error Budget 기반 limit 계산
    error_budget_limit = self.config.max_limit
    if self._error_budget_limit_reduction_active:
        error_budget_limit = int(
            self._limit_before_error_budget_reduction * self._error_budget_multiplier
        )

    return min(
        self._rtt_suggested_limit,
        self._429_suggested_limit,
        error_budget_limit,
    )
```

---

## 6. 테스트 케이스

### 6.1 단위 테스트

```python
class TestAdaptiveThrottleErrorBudgetIntegration:
    """Error Budget Gate 연동 테스트."""

    def test_warning_event_reduces_limit_by_20_percent(self):
        """WARNING 이벤트 시 limit 20% 감소."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=1000, min_limit=10)
        throttle = AdaptiveThrottle(config)

        # Warning 이벤트 시뮬레이션
        warning_event = MockEvent(data={"budget_percent": 15.0})
        throttle._handle_error_budget_warning(warning_event)

        assert throttle._current_limit == 800  # 1000 × 0.8
        assert throttle._error_budget_limit_reduction_active is True

    def test_critical_event_reduces_limit_by_50_percent(self):
        """CRITICAL 이벤트 시 limit 50% 감소."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10)
        throttle = AdaptiveThrottle(config)

        critical_event = MockEvent(data={"budget_percent": 5.0})
        throttle._handle_error_budget_critical(critical_event)

        assert throttle._current_limit == 500  # 1000 × 0.5
        assert throttle._error_budget_multiplier == 0.5

    def test_critical_blocks_non_essential_tier(self):
        """CRITICAL 상태에서 non_essential 티어 거부."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10)
        throttle = AdaptiveThrottle(config)

        # Critical 상태 설정
        throttle._error_budget_limit_reduction_active = True
        throttle._error_budget_multiplier = 0.5

        result = throttle.check("test_key", tier_id="non_essential")

        assert result.allowed is False
        assert "error_budget_critical" in result.reason

    def test_recovery_triggers_dampening(self):
        """복구 이벤트 시 Recovery Dampening 시작."""
        config = ThrottleConfig(initial_limit=1000, min_limit=10)
        throttle = AdaptiveThrottle(config)

        # Critical 상태 진입
        throttle._handle_error_budget_critical(MockEvent(data={"budget_percent": 5.0}))

        # 복구 이벤트
        throttle._handle_error_budget_recovered(MockEvent(data={"budget_percent": 25.0}))

        assert throttle._error_budget_limit_reduction_active is False
        assert throttle._recovery_dampening_active is True
```

### 6.2 통합 테스트

```python
class TestErrorBudgetGateThrottleIntegration:
    """실제 EventBus 연동 테스트."""

    def test_eventbus_warning_propagates_to_throttle(self):
        """EventBus WARNING 이벤트가 Throttle에 전달되는지 확인."""
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle, reset_adaptive_throttle
        from selfhealing.services.event_bus import get_event_bus, EventType

        reset_adaptive_throttle()
        throttle = get_adaptive_throttle()
        initial_limit = throttle._current_limit

        bus = get_event_bus()
        bus.emit(
            event_type=EventType.ERROR_BUDGET_WARNING,
            data={"budget_percent": 15.0, "threshold": 20.0},
            source="test",
        )

        # 이벤트 처리 대기
        time.sleep(0.1)

        assert throttle._current_limit < initial_limit
        assert throttle._error_budget_limit_reduction_active is True
```

---

## 7. 메트릭 정의

### 7.1 Prometheus 메트릭

```python
# 추가 메트릭 정의 필요 (definitions.py)
throttle_error_budget_adjustments_total = Counter(
    "selfhealing_throttle_error_budget_adjustments_total",
    "Total error budget-triggered throttle adjustments",
    ["service", "budget_status"],  # warning, critical, recovered
)

throttle_error_budget_multiplier = Gauge(
    "selfhealing_throttle_error_budget_multiplier",
    "Current error budget multiplier applied to limit",
    ["service"],
)
```

### 7.2 감사 이벤트

| 이벤트 | 트리거 | 기록 내용 |
|--------|--------|----------|
| `throttle_error_budget_warning` | WARNING 이벤트 수신 | old_limit, new_limit, budget_percent, multiplier |
| `throttle_error_budget_critical` | CRITICAL 이벤트 수신 | old_limit, new_limit, budget_percent, multiplier |
| `throttle_error_budget_recovered` | RECOVERED 이벤트 수신 | budget_percent, recovery_mode |

---

## 8. 구현 체크리스트

- [ ] `__init__`에 Error Budget 상태 변수 추가
- [ ] `_subscribe_error_budget_events()` 메서드 구현
- [ ] `_handle_error_budget_warning()` 구현
- [ ] `_handle_error_budget_critical()` 구현
- [ ] `_handle_error_budget_recovered()` 구현
- [ ] `check()` 메서드에 non_essential 거부 로직 추가
- [ ] `conservative_limit` 프로퍼티 확장
- [ ] Prometheus 메트릭 추가
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성

---

## 9. 참고 문서

- [190_AUDIT_SYSTEM_THROTTLE_INTEGRATION.md](190_AUDIT_SYSTEM_THROTTLE_INTEGRATION.md) - 감사 로깅 연동
- [189_PROMETHEUS_METRICS_THROTTLE_INTEGRATION.md](189_PROMETHEUS_METRICS_THROTTLE_INTEGRATION.md) - 메트릭 연동
- [154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md](154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md) - Emergency 연동
- [172_CANARY_ERROR_BUDGET_GATE.md](172_CANARY_ERROR_BUDGET_GATE.md) - ErrorBudgetGate 설계
