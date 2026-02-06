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

- [x] `__init__`에 Error Budget 상태 변수 추가
- [x] `_subscribe_error_budget_events()` 메서드 구현
- [x] `_handle_error_budget_warning()` 구현
- [x] `_handle_error_budget_critical()` 구현
- [x] `_handle_error_budget_recovered()` 구현
- [x] `check()` 메서드에 non_essential 거부 로직 추가
- [x] `conservative_limit` 프로퍼티 확장
- [x] Prometheus 메트릭 추가
- [x] 단위 테스트 작성
- [ ] 통합 테스트 작성

---

## 9. 리뷰 기반 추가 구현 명세

본 섹션은 코드 리뷰를 통해 식별된 개선 항목의 상세 구현 명세입니다.

### 9.1 SLO 기반 서비스 매핑 및 예산 샤딩 (#2, #13 통합)

#### 9.1.1 문제점

현재 Error Budget 이벤트에는 `service_id`나 `scope` 필드가 없어, 단일 예산이 모든 Throttle 인스턴스에 영향을 줍니다. 이로 인해 특정 API의 에러로 무관한 서비스까지 제한되는 '인접 간섭'이 발생합니다.

#### 9.1.2 설계 결정

**선택**: `slo_name` 계층화 방식 채택

**이유** (코드 근거):
- [calculator.py#L57](../../packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py#L57)에서 이미 `slo_name` 파라미터 지원
- [models.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget/models.py)의 `ErrorBudgetStatus`에 `slo_name` 필드 존재
- 새로운 필드 추가 대신 기존 필드 확장으로 하위 호환성 유지

```python
# calculator.py 기존 시그니처
def calculate_budget_status(
    self,
    slo_name: str = "availability",  # 이미 존재
    window_start: datetime | None = None,
    ...
)
```

#### 9.1.3 SLO Name 계층화 규칙

| 패턴 | 설명 | 예시 |
|------|------|------|
| `{slo}` | 전체 시스템 | `availability` |
| `{slo}:{domain}` | 도메인별 | `availability:payment` |
| `{slo}:{domain}:{api_group}` | API 그룹별 | `availability:payment:checkout` |

#### 9.1.4 이벤트 페이로드 확장

**수정 위치**: [gate.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py) `_emit_error_budget_critical_event`

```python
def _emit_error_budget_critical_event(
    self,
    budget_percent: float,
    slo_name: str = "availability",  # 추가
) -> None:
    bus.emit(
        event_type=EventType.ERROR_BUDGET_CRITICAL,
        data={
            "budget_percent": budget_percent,
            "threshold": self._config.critical_threshold_percent,
            "status": "critical",
            "slo_name": slo_name,  # 추가: 영향 범위 식별
        },
        source="error_budget_gate",
        priority=EventPriority.CRITICAL,
    )
```

#### 9.1.5 Throttle 측 SLO 필터링

**수정 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    def __init__(self, config: ThrottleConfig | None = None):
        # ...
        # SLO 매핑 설정 (기본: 전역 예산 반응)
        self._target_slo_patterns: list[str] = ["availability"]  # 구독 대상 SLO

    def set_target_slo_patterns(self, patterns: list[str]) -> None:
        """
        이 Throttle이 반응할 SLO 패턴 설정.

        Args:
            patterns: SLO name 패턴 리스트 (prefix 매칭)
                      예: ["availability:payment"] → payment 도메인만 반응
        """
        self._target_slo_patterns = patterns
        logger.info(f"[AdaptiveThrottle] Target SLO patterns: {patterns}")

    def _should_react_to_slo(self, slo_name: str) -> bool:
        """이벤트의 SLO가 이 Throttle의 반응 대상인지 확인."""
        if not self._target_slo_patterns:
            return True  # 패턴 미설정 시 모든 이벤트 반응

        return any(
            slo_name.startswith(pattern) or pattern == "*"
            for pattern in self._target_slo_patterns
        )

    def _handle_error_budget_warning(self, event) -> None:
        """Warning 이벤트 처리 (SLO 필터링 포함)."""
        event_data = event.data if hasattr(event, "data") else event
        slo_name = event_data.get("slo_name", "availability")

        # SLO 필터링
        if not self._should_react_to_slo(slo_name):
            logger.debug(
                f"[AdaptiveThrottle] Ignoring warning for SLO '{slo_name}' "
                f"(not in target patterns: {self._target_slo_patterns})"
            )
            return

        # ... 기존 처리 로직 ...
```

---

### 9.2 Min-Winner 정책 통합 (#3)

#### 9.2.1 문제점

현재 `conservative_limit`은 RTT와 429만 포함하고 Error Budget 배율이 빠져 있습니다.

#### 9.2.2 코드 근거

**현재 구현**: [adaptive.py#L694-L703](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L694-L703)

```python
@property
def conservative_limit(self) -> int:
    """Min-Winner 정책: RTT와 429 중 낮은 값."""
    if not self._conservative_enabled:
        return self._current_limit
    return min(self._rtt_suggested_limit, self._429_suggested_limit)
```

#### 9.2.3 확장 구현

```python
@property
def conservative_limit(self) -> int:
    """
    Min-Winner 정책 적용한 보수적 limit.

    RTT 기반 limit, 429 기반 limit, Error Budget 기반 limit 중
    가장 낮은 값 반환.

    코드 근거: 기존 Min-Winner 패턴 확장
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

### 9.3 임계치 히스테리시스 (#4)

#### 9.3.1 문제점

예산이 10.1%와 9.9% 사이를 오갈 때 알림과 제한이 반복되는 '플래핑(Flapping)' 현상 발생.

#### 9.3.2 설계 결정

**선택**: `threshold_hysteresis_buffer_percent` 필드 추가

**이유** (코드 근거):
- [anti_flapping.py#L96-L104](../../packages/selfhealing-python/src/selfhealing/settings/anti_flapping.py#L96-L104)에서 이미 유사한 `recovery_hysteresis_factor` 패턴이 사용됨
- 기존 Anti-Flapping 설계와 일관성 유지

```python
# anti_flapping.py 기존 패턴
recovery_hysteresis_factor: float = Field(
    default=1.15,
    description="복구 윈도우 히스테리시스 팩터. 1.15 = 15% 더 긴 시간 필요"
)
```

#### 9.3.3 설정 확장

**수정 위치**: [config.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/config.py)

```python
@dataclass
class ErrorBudgetGateConfig:
    enabled: bool = True
    critical_threshold_percent: float = 10.0
    warning_threshold_percent: float = 20.0
    fail_open: bool = True
    cache_ttl_seconds: int = 30

    # 히스테리시스 설정 (플래핑 방지)
    threshold_hysteresis_buffer_percent: float = 2.0  # 추가
    """
    임계치 복구 시 적용되는 버퍼.

    예: critical=10%, buffer=2%
    - 진입: budget < 10% → CRITICAL
    - 복구: budget > 12% → WARNING으로 복귀

    코드 근거: anti_flapping.py의 recovery_hysteresis_factor 패턴
    """
```

#### 9.3.4 Gate 판정 로직 수정

```python
def _evaluate(self, budget_percent: float) -> GateCheckResult:
    """에러 예산 기반 판정 (히스테리시스 적용)."""

    # 히스테리시스 적용된 복구 임계치
    critical_recovery = (
        self._config.critical_threshold_percent +
        self._config.threshold_hysteresis_buffer_percent
    )
    warning_recovery = (
        self._config.warning_threshold_percent +
        self._config.threshold_hysteresis_buffer_percent
    )

    # 현재 상태에 따른 임계치 선택
    if self._current_status == GateStatus.BLOCKED:
        # BLOCKED 상태에서는 복구 임계치(12%) 적용
        if budget_percent >= critical_recovery:
            # WARNING으로 복귀
            return self._build_warning_result(budget_percent)
        else:
            # BLOCKED 유지
            return self._build_blocked_result(budget_percent)

    elif self._current_status == GateStatus.WARNING:
        # WARNING 상태에서는 복구 임계치(22%) 적용
        if budget_percent >= warning_recovery:
            # OPEN으로 복귀
            return self._build_open_result(budget_percent)
        elif budget_percent < self._config.critical_threshold_percent:
            # CRITICAL로 진입
            return self._build_blocked_result(budget_percent)
        else:
            # WARNING 유지
            return self._build_warning_result(budget_percent)

    else:
        # OPEN 상태: 기존 임계치 적용
        if budget_percent < self._config.critical_threshold_percent:
            return self._build_blocked_result(budget_percent)
        elif budget_percent < self._config.warning_threshold_percent:
            return self._build_warning_result(budget_percent)
        else:
            return self._build_open_result(budget_percent)
```

#### 9.3.5 히스테리시스 상태 다이어그램

```
                    budget >= 22%
        ┌──────────────────────────────────┐
        │                                  │
        ▼           budget < 20%          │
    ┌───────┐  ──────────────────►  ┌─────┴───┐
    │ OPEN  │                       │ WARNING │
    └───────┘  ◄──────────────────  └────┬────┘
                   budget >= 22%          │
                                          │ budget < 10%
                                          ▼
                   budget >= 12%    ┌──────────┐
                 ◄─────────────────│ BLOCKED  │
                                    └──────────┘

    * 진입 임계치: 10%, 20%
    * 복구 임계치: 12%, 22% (buffer=2%)
```

---

### 9.4 BUDGET_EXHAUSTED Redis 글로벌 플래그 (#5)

#### 9.4.1 문제점

현재 예산 소진 상태는 실시간 조회로 정확하지만, 고부하 환경에서 ErrorBudgetService 부하 우려.

#### 9.4.2 설계 결정

**선택**: Redis 글로벌 플래그 추가

**이유** (코드 근거):
- [event_bus_redis.py#L51-L58](../../packages/selfhealing-python/src/selfhealing/services/event_bus_redis.py#L51-L58)에서 이미 채널별 Redis 키 패턴 사용

```python
# event_bus_redis.py 기존 패턴
SELFHEALING_EVENT_CHANNELS: dict[str, str] = {
    EventChannel.GLOBAL.value: "selfhealing:global:events",
    # ...
}
```

#### 9.4.3 Redis 키 설계

```python
# 추가 위치: services/error_budget_gate/redis_flag.py

BUDGET_EXHAUSTED_FLAG_KEY = "selfhealing:error_budget:exhausted"
BUDGET_EXHAUSTED_BY_SLO_KEY = "selfhealing:error_budget:exhausted:{slo_name}"
BUDGET_STATUS_KEY = "selfhealing:error_budget:status:{slo_name}"

# TTL: 캐시보다 약간 길게 (stale 방지)
BUDGET_FLAG_TTL_SECONDS = 60
```

#### 9.4.4 플래그 관리 서비스

```python
class BudgetExhaustedFlagManager:
    """
    Redis 기반 예산 소진 상태 글로벌 플래그 관리.

    코드 근거: event_bus_redis.py의 Redis 키 패턴
    """

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._local_cache: dict[str, bool] = {}
        self._local_cache_time: dict[str, float] = {}
        self._local_ttl_seconds = 5  # 로컬 캐시 TTL

    def set_exhausted(self, slo_name: str, exhausted: bool) -> None:
        """예산 소진 상태 설정 (Redis + 로컬 캐시)."""
        key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name=slo_name)

        if self._redis:
            try:
                if exhausted:
                    self._redis.setex(key, BUDGET_FLAG_TTL_SECONDS, "1")
                else:
                    self._redis.delete(key)
            except Exception as e:
                logger.warning(f"[BudgetFlag] Redis write failed: {e}")

        # 로컬 캐시 업데이트
        self._local_cache[slo_name] = exhausted
        self._local_cache_time[slo_name] = time.time()

    def is_exhausted(self, slo_name: str) -> bool:
        """예산 소진 상태 조회 (로컬 캐시 → Redis → False)."""
        now = time.time()

        # 로컬 캐시 확인
        if slo_name in self._local_cache:
            if now - self._local_cache_time.get(slo_name, 0) < self._local_ttl_seconds:
                return self._local_cache[slo_name]

        # Redis 조회
        if self._redis:
            try:
                key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name=slo_name)
                result = self._redis.get(key) == "1"
                self._local_cache[slo_name] = result
                self._local_cache_time[slo_name] = now
                return result
            except Exception as e:
                logger.warning(f"[BudgetFlag] Redis read failed: {e}")

        return False  # Fail-Open
```

---

### 9.5 관리자 바이패스 (BypassRegistry 확장) (#8)

#### 9.5.1 문제점

예산 소진 시에도 비즈니스 긴급도에 따라 일시적으로 제한을 푸는 기능 부재.

#### 9.5.2 설계 결정

**선택**: 기존 `BypassRegistry` 확장 (새 컴포넌트 추가 X)

**이유** (코드 근거):
- [hooks.py#L79-L260](../../packages/selfhealing-python/src/selfhealing/core/hooks.py#L79-L260)에 Enterprise-grade Bypass 인프라 이미 존재
- 감사 로깅, 우선순위 정렬, Thread-safe 기능 재사용

```python
# hooks.py 기존 인프라
class BypassRegistry:
    """
    Enterprise-grade Hook Registry for bypass decisions.
    All bypass decisions are:
    1. Priority-ordered
    2. Audit-logged
    3. Thread-safe
    """
```

#### 9.5.3 Error Budget Bypass Hook 등록

**추가 위치**: [hooks.py](../../packages/selfhealing-python/src/selfhealing/core/hooks.py)

```python
# =============================================================================
# Error Budget Bypass Hooks
# =============================================================================

def _error_budget_admin_bypass(request: HttpRequest) -> bool:
    """
    관리자 Error Budget 바이패스.

    X-ErrorBudget-Bypass: admin-override 헤더로 예산 제한 우회.
    감사 로그에 기록됨.
    """
    bypass_header = request.headers.get("X-ErrorBudget-Bypass", "")
    return bypass_header == "admin-override"


def _error_budget_critical_path_bypass(request: HttpRequest) -> bool:
    """
    중요 경로 자동 바이패스.

    결제, 인증 등 치명적 경로는 예산 제한에서 자동 제외.

    코드 근거: defaults.py의 STATIC_CRITICAL_PATHS
    """
    from selfhealing.api.django.tiering.defaults import STATIC_CRITICAL_PREFIXES

    path = getattr(request, "path", "")
    return path.startswith(STATIC_CRITICAL_PREFIXES)


def register_error_budget_bypass_hooks() -> None:
    """Error Budget 관련 바이패스 훅 등록."""
    BypassRegistry.register(
        _error_budget_admin_bypass,
        priority=900,  # 높은 우선순위
        name="error_budget_admin",
        description="Admin override for error budget restrictions",
    )

    BypassRegistry.register(
        _error_budget_critical_path_bypass,
        priority=950,  # 최고 우선순위
        name="error_budget_critical_path",
        description="Auto-bypass for critical paths (payment, auth)",
    )
```

#### 9.5.4 Gate 통합

```python
# gate.py 수정
def check(self, force_refresh: bool = False, request: HttpRequest | None = None) -> GateCheckResult:
    """자동화 허용 여부 체크 (Bypass 지원)."""

    # Bypass 체크 (request가 있는 경우)
    if request:
        from selfhealing.core.hooks import BypassRegistry

        bypass_result = BypassRegistry.should_bypass(request)
        if bypass_result.bypassed:
            logger.warning(
                f"[ErrorBudgetGate] Bypassed by hook '{bypass_result.hook_name}': "
                f"{bypass_result.reason}"
            )
            return GateCheckResult(
                allowed=True,
                status=GateStatus.OPEN,
                reason=f"Bypassed: {bypass_result.reason}",
                recommendation="Admin bypass active - audit logged",
            )

    # ... 기존 체크 로직 ...
```

---

### 9.6 Recovery Dampening 연결 (#9)

#### 9.6.1 문제점

현재 `_on_error_budget_recovered_throttle()`은 즉시 100% 복구하여 '요요 현상' 발생 가능.

#### 9.6.2 설계 결정

**선택**: 기존 Recovery Dampening 인프라 연결

**이유** (코드 근거):
- [adaptive.py#L1560-L1695](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py#L1560-L1695)에 완성된 Recovery Dampening 존재
- 80% → 90% → 100% 점진적 복구 지원

```python
# adaptive.py 기존 인프라
RECOVERY_DAMPENING_MULTIPLIERS: tuple[float, ...] = (0.8, 0.9, 1.0)

def start_recovery_dampening(self) -> None:
    """Recovery Dampening 시작: 80%부터 점진적으로 복구."""
```

#### 9.6.3 이벤트 핸들러 수정

**수정 위치**: [event_bus.py#L1590-L1610](../../packages/selfhealing-python/src/selfhealing/services/event_bus.py#L1590-L1610)

```python
def _on_error_budget_recovered_throttle(event: SelfHealingEvent) -> None:
    """
    Error Budget 회복 시 limit 제한 해제.

    변경: 즉시 100% 복구 → Recovery Dampening으로 점진적 복구
    코드 근거: adaptive.py의 start_recovery_dampening()
    """
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()

        # Error Budget 감소 상태가 아니면 무시
        if not throttle._error_budget_limit_reduction_active:
            return

        previous_limit = throttle.current_limit

        # Recovery Dampening 시작 (80% → 90% → 100%)
        throttle._error_budget_limit_reduction_active = False
        throttle._error_budget_multiplier = 1.0
        throttle._base_limit_before_emergency = throttle._limit_before_error_budget_reduction
        throttle.start_recovery_dampening()

        logger.info(
            f"[Throttle] Error budget recovered, "
            f"starting recovery dampening (80% → 90% → 100%), "
            f"previous_limit={previous_limit}"
        )
    except ImportError:
        logger.debug("[EventHandler] Throttle module not available")
    except Exception as e:
        logger.warning(f"[EventHandler] Failed to start recovery dampening: {e}")
```

---

### 9.7 복구 Jitter (#10)

#### 9.7.1 문제점

일간/주간 예산 리셋 시 모든 Pod가 동시에 RECOVERED 이벤트를 수신하면 Thundering Herd 발생.

#### 9.7.2 설계 결정

**선택**: `recovery_jitter_max_seconds` 설정 추가

**이유** (코드 근거):
- [192 문서](192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md)에서 Jitter 패턴이 사용됨

```python
# 192 문서의 Jitter 패턴
if with_jitter and self.config.jitter_percent > 0:
    jitter_factor = self.config.jitter_percent / 100.0
    jitter = delay * jitter_factor * (random.random() * 2 - 1)
```

#### 9.7.3 설정 추가

```python
@dataclass
class ThrottleRecoveryConfig:
    """Recovery 관련 설정."""

    # Recovery Dampening
    dampening_interval_seconds: float = 30.0
    dampening_multipliers: tuple[float, ...] = (0.8, 0.9, 1.0)

    # Jitter (Thundering Herd 방지)
    recovery_jitter_max_seconds: int = 10
    """
    복구 시작 전 무작위 지연 최대값.
    0-10초 범위에서 랜덤 지연 후 복구 시작.

    코드 근거: 192 문서의 jitter_percent 패턴
    """
```

#### 9.7.4 Jitter 적용 구현

```python
def start_recovery_dampening(self, apply_jitter: bool = True) -> None:
    """
    Recovery Dampening 시작: 80%부터 점진적으로 복구.

    Args:
        apply_jitter: Thundering Herd 방지용 랜덤 지연 적용 여부
    """
    import random

    # Jitter 적용 (Pod간 복구 시점 분산)
    if apply_jitter:
        jitter_max = getattr(self.config, 'recovery_jitter_max_seconds', 10)
        jitter_seconds = random.uniform(0, jitter_max)

        logger.info(
            f"[AdaptiveThrottle] Recovery jitter applied: "
            f"waiting {jitter_seconds:.2f}s before dampening start"
        )

        # 비동기 지연 후 실제 복구 시작
        self._schedule_dampening_start(jitter_seconds)
        return

    # ... 기존 복구 로직 ...

def _schedule_dampening_start(self, delay_seconds: float) -> None:
    """지연 후 Dampening 시작 스케줄링."""
    import threading

    def delayed_start():
        time.sleep(delay_seconds)
        self._do_start_recovery_dampening()

    thread = threading.Thread(target=delayed_start, daemon=True)
    thread.start()
```

---

### 9.8 감사 ID 연동 (#11)

#### 9.8.1 문제점

Error Budget 위반으로 인한 Limit 조정 시 `correlation_id`에 위반 ID가 포함되지 않아 추적 어려움.

#### 9.8.2 설계 결정

**선택**: 기존 `correlation_id` 필드 활용

**이유** (코드 근거):
- [audit.py#L333-L345](../../packages/selfhealing-python/src/selfhealing/services/throttle/audit.py#L333-L345)에 이미 `correlation_id` 파라미터 존재

```python
# audit.py 기존 시그니처
def _build_audit_data(
    ...
    correlation_id: str | None = None,
    ...
):
    audit_data = {
        "correlation_id": correlation_id or event_id,
    }
```

#### 9.8.3 위반 ID 생성 및 전달

```python
def _handle_error_budget_critical(self, event) -> None:
    """Error Budget Critical 이벤트 처리."""
    event_data = event.data if hasattr(event, "data") else event
    budget_percent = event_data.get("budget_percent", 100.0)
    slo_name = event_data.get("slo_name", "availability")

    # 위반 ID 생성 (추적용)
    import uuid
    violation_id = f"eb-violation-{slo_name}-{uuid.uuid4().hex[:8]}"

    # ... 기존 처리 로직 ...

    # 감사 로깅 (violation_id를 correlation_id로 사용)
    _record_audit_safe(
        action="throttle_error_budget_critical",
        old_limit=previous_limit,
        new_limit=new_limit,
        error_budget_percent=budget_percent,
        multiplier=self._error_budget_multiplier,
        correlation_id=violation_id,  # 위반 ID 연결
        extra_data={
            "slo_name": slo_name,
            "violation_id": violation_id,
        },
    )
```

#### 9.8.4 위반 ID 조회 API

```python
# 감사 로그 조회 시 violation_id로 필터링 가능
# GET /api/self-healing/audit/?correlation_id=eb-violation-availability-a1b2c3d4
```

---

### 9.9 예측 기반 보호 (BudgetDepletionForecaster) (#14)

#### 9.9.1 문제점

현재는 예산 소진 후에만 반응하여 사전 예방 불가.

#### 9.9.2 설계 결정

**선택**: `BudgetDepletionForecaster` 클래스 추가

**이유** (코드 근거):
- [calculator.py#L166-L201](../../packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py#L166-L201)에 `burn_rate_1h`, `burn_rate_6h` 계산 로직 존재

```python
# calculator.py 기존 burn rate 계산
burn_rate_1h = self._calculate_burn_rate(slo=slo, window_hours=1, ...)
burn_rate_6h = self._calculate_burn_rate(slo=slo, window_hours=6, ...)
```

#### 9.9.3 Forecaster 구현

**추가 위치**: [services/error_budget/forecaster.py](../../packages/selfhealing-python/src/selfhealing/services/error_budget/forecaster.py)

```python
"""
Budget Depletion Forecaster

Burn Rate 기반 예산 소진 예측.
코드 근거: calculator.py의 burn_rate_1h, burn_rate_6h
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from selfhealing.services.error_budget.models import ErrorBudgetStatus

logger = logging.getLogger(__name__)


@dataclass
class DepletionForecast:
    """예산 소진 예측 결과."""

    estimated_depletion_hours: float | None
    """예상 소진까지 시간 (None = 정상 범위)"""

    burn_rate_1h: float
    """1시간 Burn Rate"""

    burn_rate_6h: float
    """6시간 Burn Rate"""

    is_accelerating: bool
    """Burn Rate 가속 여부 (1h > 6h)"""

    risk_level: str
    """위험 수준: low, medium, high, critical"""

    recommended_action: str
    """권장 조치"""


class BudgetDepletionForecaster:
    """
    예산 소진 예측기.

    Burn Rate를 기반으로 예산 소진 시점을 예측하고
    사전 경고를 제공합니다.
    """

    # 위험 수준 임계치 (예상 소진 시간 기준)
    RISK_THRESHOLDS = {
        "critical": 1.0,   # 1시간 이내 소진 예상
        "high": 6.0,       # 6시간 이내 소진 예상
        "medium": 24.0,    # 24시간 이내 소진 예상
    }

    def forecast(self, status: ErrorBudgetStatus) -> DepletionForecast:
        """
        예산 소진 예측.

        Args:
            status: 현재 ErrorBudgetStatus

        Returns:
            DepletionForecast: 예측 결과
        """
        burn_rate_1h = status.burn_rate_1h
        burn_rate_6h = status.burn_rate_6h
        remaining_percent = status.budget_remaining_percent

        # Burn Rate 가속 여부
        is_accelerating = burn_rate_1h > burn_rate_6h * 1.2  # 20% 이상 가속

        # 소진 시간 예측 (1시간 Burn Rate 기준)
        if burn_rate_1h <= 1.0:
            # 정상 범위 (SLO 내 소진)
            estimated_hours = None
            risk_level = "low"
            action = "정상 운영 유지"
        else:
            # 비정상 소진 속도
            # 1시간당 소진율 = (burn_rate - 1.0) * slo_error_budget
            # 예: burn_rate=14.4, slo_error_budget=0.1% → 1시간당 1.34% 소진
            hourly_depletion = (burn_rate_1h - 1.0) * 0.1  # 99.9% SLO 가정

            if hourly_depletion > 0:
                estimated_hours = remaining_percent / hourly_depletion
            else:
                estimated_hours = None

            # 위험 수준 판단
            if estimated_hours is None:
                risk_level = "low"
                action = "정상 운영 유지"
            elif estimated_hours < self.RISK_THRESHOLDS["critical"]:
                risk_level = "critical"
                action = "즉시 Throttle 강화 및 on-call 알림"
            elif estimated_hours < self.RISK_THRESHOLDS["high"]:
                risk_level = "high"
                action = "Throttle WARNING 레벨로 사전 조정 권장"
            elif estimated_hours < self.RISK_THRESHOLDS["medium"]:
                risk_level = "medium"
                action = "모니터링 강화 및 원인 분석 시작"
            else:
                risk_level = "low"
                action = "정상 운영 유지"

        return DepletionForecast(
            estimated_depletion_hours=estimated_hours,
            burn_rate_1h=burn_rate_1h,
            burn_rate_6h=burn_rate_6h,
            is_accelerating=is_accelerating,
            risk_level=risk_level,
            recommended_action=action,
        )

    def should_preemptive_throttle(self, status: ErrorBudgetStatus) -> bool:
        """
        선제적 Throttle 조정이 필요한지 판단.

        Returns:
            True if 1시간 이내 소진 예상 (CRITICAL) 또는
                 6시간 이내 소진 예상 + Burn Rate 가속 중
        """
        forecast = self.forecast(status)

        if forecast.risk_level == "critical":
            return True

        if forecast.risk_level == "high" and forecast.is_accelerating:
            return True

        return False
```

#### 9.9.4 Throttle 통합

```python
# adaptive.py에 예측 기반 조정 추가
def check_and_sync_emergency_state(self) -> bool:
    """Check on Use 패턴 (예측 기반 보호 포함)."""
    # ... 기존 로직 ...

    # 예측 기반 선제적 보호
    self._check_preemptive_protection()

    return False

def _check_preemptive_protection(self) -> None:
    """Burn Rate 기반 선제적 보호 체크."""
    try:
        from selfhealing.services.error_budget_service import get_error_budget_service
        from selfhealing.services.error_budget.forecaster import BudgetDepletionForecaster

        service = get_error_budget_service()
        status = service.get_budget_status()

        forecaster = BudgetDepletionForecaster()

        if forecaster.should_preemptive_throttle(status):
            forecast = forecaster.forecast(status)

            logger.warning(
                f"[AdaptiveThrottle] Preemptive throttle triggered: "
                f"risk_level={forecast.risk_level}, "
                f"estimated_depletion={forecast.estimated_depletion_hours:.1f}h, "
                f"burn_rate_1h={forecast.burn_rate_1h:.2f}"
            )

            # WARNING 레벨로 선제 조정 (아직 WARNING 이벤트를 받지 않았더라도)
            if not self._error_budget_limit_reduction_active:
                self._apply_preemptive_reduction(forecast)

    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Preemptive check failed: {e}")

def _apply_preemptive_reduction(self, forecast: DepletionForecast) -> None:
    """선제적 limit 감소 적용."""
    self._limit_before_error_budget_reduction = self._current_limit
    self._error_budget_limit_reduction_active = True

    # 위험 수준에 따른 배율 결정
    if forecast.risk_level == "critical":
        self._error_budget_multiplier = 0.5  # 50%
    else:
        self._error_budget_multiplier = 0.8  # 80%

    previous_limit = self._current_limit
    new_limit = max(
        int(self._limit_before_error_budget_reduction * self._error_budget_multiplier),
        self.config.min_limit,
    )

    self.current_limit = new_limit

    _record_audit_safe(
        action="throttle_preemptive_reduction",
        old_limit=previous_limit,
        new_limit=new_limit,
        extra_data={
            "risk_level": forecast.risk_level,
            "estimated_depletion_hours": forecast.estimated_depletion_hours,
            "burn_rate_1h": forecast.burn_rate_1h,
        },
    )
```

---

## 10. 확장 구현 체크리스트

### 10.1 Priority 1 (필수)

- [x] `_handle_error_budget_warning()` SLO 필터링 추가 (#2, #13)
- [x] `conservative_limit` Error Budget 배율 통합 (#3)
- [x] `_on_error_budget_recovered_throttle()` Recovery Dampening 연결 (#9)

### 10.2 Priority 2 (권장)

- [x] `ErrorBudgetGateConfig.threshold_hysteresis_buffer_percent` 추가 (#4)
- [x] Gate `_evaluate()` 히스테리시스 로직 구현 (#4)
- [x] `BudgetExhaustedFlagManager` Redis 플래그 구현 (#5)
- [x] `register_error_budget_bypass_hooks()` 바이패스 훅 등록 (#8)
- [x] 감사 로그 `violation_id` 연결 (#11)

### 10.3 Priority 3 (선택)

- [x] `recovery_jitter_max_seconds` 설정 및 Jitter 적용 (#10)
- [x] `BudgetDepletionForecaster` 예측기 구현 (#14)
- [x] `_check_preemptive_protection()` 선제적 보호 통합 (#14)

---

## 11. 참고 문서

- [190_AUDIT_SYSTEM_THROTTLE_INTEGRATION.md](190_AUDIT_SYSTEM_THROTTLE_INTEGRATION.md) - 감사 로깅 연동
- [189_PROMETHEUS_METRICS_THROTTLE_INTEGRATION.md](189_PROMETHEUS_METRICS_THROTTLE_INTEGRATION.md) - 메트릭 연동
- [154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md](154_ADAPTIVE_THROTTLE_EMERGENCY_MODE_INTEGRATION.md) - Emergency 연동
- [172_CANARY_ERROR_BUDGET_GATE.md](172_CANARY_ERROR_BUDGET_GATE.md) - ErrorBudgetGate 설계
- [192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md](192_RETRY_HANDLER_BACKOFF_THROTTLE_INTEGRATION.md) - Jitter 패턴 참조
- [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md) - Anti-Flapping 히스테리시스 참조
