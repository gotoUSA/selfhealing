# 209. Governance Checks ↔ AdaptiveThrottle 연동 계획

> **상태**: ✅ 구현 완료 (1-3단계)
> **목적**: AdaptiveThrottle에 Governance 3단계 안전 체크(Kill Switch → Emergency → Error Budget)를 공식 통합한다.
> **근거 문서**: [207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md](207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md)

---

## 1. 현재 코드 구조

### 1-1. Governance Checks 측

**파일**: `services/governance/checks.py` (863줄)

```python
# checks.py L424-536
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    ...
) -> GovernanceCheckResult:
    """
    체크 순서:
    0. Break Glass (활성화 시 모든 체크 우회)
    1. Kill Switch
    2. Emergency Level
    3. Error Budget
    """
```

**제공 패턴 3가지**:

| 패턴 | 코드 위치 | 사용처 |
|------|-----------|--------|
| `check_all_governance()` 직접 호출 | checks.py L424 | `AutoTuningService._check_governance_before_adjustment()` (service.py L149) |
| `@require_governance` 데코레이터 | checks.py L700 | 사용처 없음 (정의만 존재) |
| `GovernanceCheckMixin` 상속 | checks.py L738 | `ChaosExecutionService`, `ConfigApplyService` (execution_services.py L142, L455) |

**TTL 캐시**: `TTLCache(default_ttl=30.0)` (checks.py L260)
- `is_system_enabled()`: `_governance_cache.get("system_enabled")` → 30초 캐시
- `is_emergency_blocking()`: `_governance_cache.get(f"emergency_blocking_{min_level}")` → 30초 캐시
- `invalidate_governance_cache()`: EventBus에서 즉시 무효화 가능

**Break Glass**: checks.py L466

```python
if settings.break_glass_enabled:
    logger.warning(f"[GovernanceChecks] BREAK GLASS ACTIVE - bypassing all checks")
    return GovernanceCheckResult.allowed_result()
```

### 1-2. AdaptiveThrottle 측 — 현재 안전 체크

```python
# adaptive.py L2176
def _sync_governance_state(self) -> bool:
    """Governance 통합 상태 동기화 (Check on Use, 30초 TTL)."""
    # TTL 확인
    if now - self._last_emergency_check_time < self._emergency_cache_ttl_seconds:
        return False

    # Kill Switch 상태 동기화 (is_system_enabled() Drift 교정)
    self._sync_kill_switch_state()
    # Break Glass 상태 동기화 (Settings 기반)
    self._sync_break_glass_state()
    # Emergency Level 동기화 (get_emergency_manager())
    manager = get_emergency_manager()
    current_level = manager.get_current_level().value
    if current_level != self._emergency_level:
        self.adjust_for_emergency(current_level)

# adaptive.py L2264 — 하위호환 래퍼
def check_and_sync_emergency_state(self) -> bool:
    """하위호환 래퍼: _sync_governance_state()로 위임."""
    return self._sync_governance_state()
```

**체크하는 것** (1-3단계 구현 완료):
- ✅ Emergency Level (Check-on-Use, 30초 TTL, `get_emergency_manager()`)
- ✅ Error Budget (EventBus `ERROR_BUDGET_*` 구독, L735)
- ✅ Kill Switch (`_sync_kill_switch_state()` → `is_system_enabled()` Drift 교정)
- ✅ Break Glass (`_sync_break_glass_state()` → `get_governance_settings()`)
- ✅ `check_all_governance()` (`GovernanceCheckMixin.is_automation_allowed()` → `_maybe_adjust_limit()` Safety Net)

### 1-3. AutoTuningService — 참조 패턴

```python
# auto_tuning/service.py L149-165
def _check_governance_before_adjustment(
    self, module: str, adjustment_type: str = "automatic",
) -> GovernanceCheckResult:
    return check_all_governance(
        check_kill_switch=True,
        check_emergency=True,
        emergency_min_level=2,
        check_error_budget=True,
        operation_name=f"auto_tuning:{module}:{adjustment_type}",
        service_name="auto_tuning",
        domain=module,
        audit_on_block=True,
    )
```

- `AutoTuningService.start()`: 서비스 시작 전 governance 체크 (service.py L173)
- 모든 조정 전 `_check_governance_before_adjustment()` 호출

---

## 2. 문제점 (코드 근거)

### 2-1. Kill Switch 미반영

```python
# 현재 AdaptiveThrottle은 Kill Switch를 체크하지 않음
# checks.py L356에 is_system_enabled() 함수 존재하나 AdaptiveThrottle에서 미사용

# Kill Switch 활성화 → SystemControlManager.is_enabled() == False
# → AutoTuningService: 차단됨 ✅
# → AdaptiveThrottle: 계속 동작 ⚠️
```

- Kill Switch 활성화 시 AutoTuningService는 차단되나 AdaptiveThrottle은 계속 요청을 처리
- `adaptive.py` 전체에서 `is_system_enabled` / `system_control` / `kill_switch` 검색 결과: `activate_full_stop()` (L1860)에서 `KILL_SWITCH_ACTIVATED` 이벤트를 **발행**하지만, 외부에서 오는 Kill Switch를 **구독하지 않음**

### 2-2. Break Glass 미지원

```python
# checks.py L466-479
if settings.break_glass_enabled:
    # 모든 체크 우회 → 장애 시 긴급 투과
    return GovernanceCheckResult.allowed_result()
```

- 장애 상황에서 Break Glass 활성화 시:
  - AutoTuningService: 통과 ✅
  - Governance 데코레이터 사용 서비스: 통과 ✅
  - AdaptiveThrottle: Full Stop 상태면 **모든 요청 차단 유지** ⚠️
  - 운영자가 Throttle만 별도로 해제해야 함

### 2-3. 안전 체크 일관성 ~~불일치~~ (1-3단계 완료 후 해소)

| 체크 항목 | AutoTuningService | AdaptiveThrottle |
|-----------|-------------------|------------------|
| Kill Switch | ✅ `check_all_governance()` | ✅ EventBus 즉시 반영 + `_sync_kill_switch_state()` Drift 교정 |
| Emergency Level | ✅ `check_all_governance()` | ✅ `_sync_governance_state()` → `get_emergency_manager()` |
| Error Budget | ✅ `check_all_governance()` | ✅ EventBus 구독 + `is_automation_allowed()` Safety Net |
| Break Glass | ✅ `check_all_governance()` | ✅ `_sync_break_glass_state()` → Full Stop 해제 |
| Audit on Block | ✅ `audit_on_block=True` | ✅ `is_automation_allowed()` + `_record_audit_safe()` |

---

## 3. 연동 설계

### 3-1. 방안 A — GovernanceCheckMixin 상속 (추천)

```python
# 제안: AdaptiveThrottle 클래스 수정

class AdaptiveThrottle(GovernanceCheckMixin, ThrottleDLQReplayMixin, SlidingWindowThrottle):
    """..."""

    # Mixin 설정 (checks.py L749-750)
    _governance_service_name = "adaptive_throttle"
    _governance_domain = "throttle"
```

**기존 Mixin 사용처 (코드 근거)**:
- `ChaosExecutionService(GovernanceCheckMixin)` — execution_services.py L142
- `ConfigApplyService(GovernanceCheckMixin)` — execution_services.py L455

**활용 메서드**:
- `self.check_governance()` → `check_all_governance()` 위임 (checks.py L790)
- `self.is_automation_allowed()` → 빠른 bool 체크 (checks.py L758)
- `self.require_automation_allowed()` → 차단 시 결과 반환, 허용 시 None (checks.py L811)

### 3-2. limit 조정 시 Governance 체크

```python
# 제안: _maybe_adjust_limit() 수정 (adaptive.py L1173)

def _maybe_adjust_limit(self, rtt_ms: float) -> None:
    # LEVEL_3 Freeze 체크 (기존)
    if self._gradient_frozen:
        return

    # Governance 체크 (신규) — limit 조정 전
    if not self._is_governance_allowed_cached():
        return

    # 기존 gradient 기반 조정 로직...
```

**캐시 전략**: Governance TTLCache가 이미 30초 캐시 → `_is_governance_allowed_cached()` 내부에서 `self.is_automation_allowed()` 호출 시 실제 체크는 30초마다 1회.

### 3-3. Break Glass → Full Stop 해제 연동

```python
# 제안: check() 메서드에 Break Glass 체크 추가

def check(self, key: str, tier_id: str = "standard", ...) -> ThrottleResult:
    # Break Glass 활성 시 Full Stop 무시
    if self._is_break_glass_active():
        if self._full_stop_active:
            logger.warning("[AdaptiveThrottle] Break Glass: overriding Full Stop")
            self.deactivate_full_stop()

    # 기존 로직...
```

**근거**: Break Glass의 목적은 "장애 시 모든 안전장치를 우회" (checks.py L466-479). Full Stop은 3중 조건 충족 시 모든 요청 차단 (adaptive.py L1856) → Break Glass가 이를 해제할 수 있어야 함.

### 3-4. Kill Switch 구독

```python
# 제안: __init__에 Kill Switch 이벤트 구독 추가

def _subscribe_kill_switch_events(self) -> None:
    """Kill Switch 이벤트 구독 (Fail-Open)."""
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus
        bus = get_event_bus()
        bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self._handle_kill_switch_activated)
        bus.subscribe(EventType.KILL_SWITCH_DEACTIVATED, self._handle_kill_switch_deactivated)
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available for kill switch")

def _handle_kill_switch_activated(self, event) -> None:
    """Kill Switch 활성화 → Gradient Freeze + limit 유지."""
    self._gradient_frozen = True
    self._kill_switch_active = True
    logger.warning("[AdaptiveThrottle] Kill Switch activated: gradient frozen")

def _handle_kill_switch_deactivated(self, event) -> None:
    """Kill Switch 비활성화 → Gradient 재개."""
    self._kill_switch_active = False
    if self._emergency_level < 3:
        self._gradient_frozen = False
    self.start_recovery_dampening()
    logger.info("[AdaptiveThrottle] Kill Switch deactivated: recovery started")
```

**패턴 근거**: 기존 `_subscribe_rate_limit_events()` (adaptive.py L603), `_subscribe_error_budget_events()` (adaptive.py L735)와 동일한 Fail-Open 구독 패턴.

---

## 4. 기존 Emergency 자체 sync와의 관계

### 4-1. 정리 완료

기존 `check_and_sync_emergency_state()` (adaptive.py L2264)는 이제 `_sync_governance_state()`의 하위호환 래퍼:

```python
# adaptive.py L2264
def check_and_sync_emergency_state(self) -> bool:
    """하위호환 래퍼: _sync_governance_state()로 위임."""
    return self._sync_governance_state()
```

`_sync_governance_state()` (adaptive.py L2176)는 `get_emergency_manager()`를 사용:

```python
# adaptive.py L2207
from selfhealing.services.emergency_mode import get_emergency_manager
manager = get_emergency_manager()
current_level = manager.get_current_level().value
```

`sync_emergency_state_on_init()` (adaptive.py L2153)도 동일하게 `get_emergency_manager()` 사용:

```python
# adaptive.py L2158
from selfhealing.services.emergency_mode import get_emergency_manager
manager = get_emergency_manager()
```

→ `GracefulDegradationManager` 직접 import 완전히 제거됨.

### 4-2. 마이그레이션 완료 (3단계 모두 완료)

1. **1단계** ✅: `GovernanceCheckMixin` 추가 + 기존 자체 sync 유지 (병렬 동작)
2. **2단계** ✅: `_sync_governance_state()`에서 Kill Switch/Break Glass 상태도 함께 동기화. `_sync_kill_switch_state()` → `is_system_enabled()` Drift 교정, `_sync_break_glass_state()` → Settings 기반 동기화.
3. **3단계** ✅: `check_and_sync_emergency_state()` → `_sync_governance_state()` 리네임 (이전 메서드는 하위호환 래퍼로 유지). `GracefulDegradationManager` 직접 참조 제거, `get_emergency_manager()` 공개 API로 교체. Emergency + Kill Switch + Break Glass 동기화가 통합 메서드에 일원화.

---

## 5. 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| `services/throttle/adaptive.py` | `GovernanceCheckMixin` 상속 추가, Kill Switch 구독, Break Glass 체크 |
| `services/governance/checks.py` | 변경 없음 (기존 Mixin/함수 재사용) |
| `services/event_bus.py` | Kill Switch 이벤트 타입 확인 (이미 `KILL_SWITCH_ACTIVATED` 존재, L1860 참조) |

---

## 6. 검증 항목

- [x] Kill Switch 활성화 시 AdaptiveThrottle gradient가 freeze 되는지
- [x] Break Glass 활성화 시 Full Stop이 해제되는지
- [x] `check_all_governance()` TTL 캐시(30초)로 check() 성능 영향 최소인지
- [x] Governance 차단 시 Audit Log에 `operation_name="adaptive_throttle:limit_adjustment"` 기록되는지
- [x] Governance 패키지 Import 실패 시 Fail-Open으로 기존 동작 유지
- [x] AutoTuningService와 동일한 governance 체크 수준(Kill Switch + Emergency + Error Budget) 달성

---

## 7. 설계 리뷰 반영 — Control Plane / Data Plane 분리

> **원칙**: 매 요청 처리 경로(Data Plane)에서는 로컬 플래그만 읽고, 상태 결정 로직(Control Plane)에서만 Governance 호출·EventBus 처리를 수행한다.

### 7-1. 분리 근거 (기존 코드 패턴)

현재 `check()` (adaptive.py L1467) Data Plane은 이미 로컬 플래그만 참조하는 패턴을 사용:

| Data Plane 로컬 플래그 | Control Plane 설정 주체 | 코드 위치 |
|----------------------|----------------------|-----------|
| `self._error_budget_limit_reduction_active` | `_handle_error_budget_critical()` | adaptive.py L921 |
| `self._error_budget_multiplier` | `_handle_error_budget_critical()` | adaptive.py L944 |
| `self._429_reduction_active` | `_handle_rate_limit_429()` | adaptive.py L653 |
| `self._shedding_affected_services` | `_handle_shedding_changed()` | adaptive.py L797 |
| `self._full_stop_active` | `activate_full_stop()` | adaptive.py L1960 |

`_maybe_adjust_limit()` (adaptive.py L1246) Control Plane도 동일하게 로컬 플래그로 조기 반환:

```python
# adaptive.py L1255
if self._gradient_frozen:
    logger.debug("[AdaptiveThrottle] Gradient frozen (LEVEL_3), skipping limit adjustment")
    return
```

### 7-2. Governance 연동 시 추가되는 로컬 플래그

```python
# __init__에 추가 (기존 _full_stop_active, _429_reduction_active 등과 동일 패턴)

# =====================================================================
# Governance 연동 상태
# =====================================================================
self._kill_switch_active: bool = False   # Kill Switch → Gradient Freeze
self._break_glass_active: bool = False   # Break Glass → Full Stop 해제
```

**네이밍 검증**:
- `_kill_switch_active`: 기존 `_full_stop_active`, `_429_reduction_active`와 동일한 `_*_active` bool 패턴 → **기존 시스템에 미존재, 충돌 없음**
- `_break_glass_active`: 동일 패턴 → **기존 시스템에 미존재, 충돌 없음**

### 7-3. Data Plane (`check()`) — 로컬 플래그만 참조

```python
# check() 수정안 (adaptive.py L1467)

def check(self, key: str, tier_id: str = "standard", ...) -> ThrottleResult:
    # ① Break Glass: Full Stop 해제 (로컬 플래그만 읽음)
    if self._break_glass_active and self._full_stop_active:
        logger.warning("[AdaptiveThrottle] Break Glass: overriding Full Stop")
        self.deactivate_full_stop()

    # ② 기존 Check on Use: Emergency 상태 동기화 (TTL 30초)
    self.check_and_sync_emergency_state()

    # ③ 기존 로직 그대로 (Recovery Dampening, tier 분기 등)
    ...
```

**설계 결정**: `check()`에서 `check_all_governance()`를 **직접 호출하지 않음**.

이유:
1. Kill Switch는 "Self-healing 자동화 중단"이지 "트래픽 차단"이 아님 (`system_control.py L233`: `"This immediately stops all self-healing operations."`)
2. Emergency 동기화는 기존 `check_and_sync_emergency_state()` (adaptive.py L2067)가 이미 수행
3. Error Budget 반응은 기존 EventBus 구독 `_handle_error_budget_critical()` (adaptive.py L921)이 수행
4. Break Glass는 Settings 기반(환경변수)이므로 Control Plane에서 플래그를 동기화하고, Data Plane에서는 로컬 플래그 참조

### 7-4. Control Plane (`_maybe_adjust_limit()`) — Governance 정밀 체크

```python
# _maybe_adjust_limit() 수정안 (adaptive.py L1246)

def _maybe_adjust_limit(self, rtt_ms: float) -> None:
    now = time.time()

    with self._adjustment_lock:
        # ① 로컬 플래그 조기 반환 (EventBus에 의해 즉시 설정됨)
        if self._gradient_frozen:
            logger.debug(
                "[AdaptiveThrottle] Gradient frozen (LEVEL_3/KillSwitch), "
                "skipping limit adjustment but RTT data collected"
            )
            return

        # ② Safety Net: Governance 정합성 확인 (30초 TTL 캐시)
        #    EventBus 이벤트 유실 대비 — Drift 교정 역할
        if not self.is_automation_allowed(
            operation_name="adaptive_throttle:limit_adjustment"
        ):
            self._gradient_frozen = True  # 로컬 플래그 동기화
            logger.warning(
                "[AdaptiveThrottle] Governance blocked limit adjustment, "
                "gradient frozen"
            )
            return

        # ③ Break Glass 상태 동기화 (Settings 기반, TTL과 무관)
        self._sync_break_glass_state()

        # ④ 기존 gradient 기반 조정 로직...
        interval_seconds = self.config.sample_interval_ms / 1000.0
        if now - self._last_adjustment_time < interval_seconds:
            return
        ...
```

**기존 `_is_governance_allowed_cached()` 네이밍 폐기 사유**:

`GovernanceCheckMixin`이 이미 `is_automation_allowed()` (checks.py L758)를 제공하며, 내부에서 `check_all_governance()` → `_governance_cache` (전역 TTL 30초)를 사용. 별도 래퍼는 이중 추상화이므로 Mixin 메서드를 직접 호출.

```python
# checks.py L758 — Mixin이 이미 제공하는 메서드
def is_automation_allowed(self, ...) -> bool:
    """자동화가 허용되는지 빠르게 체크."""
    result = self.check_governance(...)
    return result.allowed
```

### 7-5. EventBus 구독 역할 정의

| 역할 | 메커니즘 | 반응 속도 | 실패 시 |
|------|---------|----------|---------|
| **Primary** (즉시 반영) | EventBus 핸들러 → 로컬 플래그 설정 | 즉시 (ms) | Fail-Open: 플래그 미변경 |
| **Safety Net** (Drift 교정) | `is_automation_allowed()` in `_maybe_adjust_limit()` | 30초 TTL | Fail-Open: True 반환 |
| **Legacy** (기존 호환) | `check_and_sync_emergency_state()` in `check()` | 30초 TTL | Fail-Open: 기존 동작 유지 |

---

## 8. 설계 리뷰 반영 — Kill Switch 체크 위치 확정

### 8-1. 원칙: Kill Switch ≠ 트래픽 차단

Kill Switch는 **Self-healing 자동화만 중단**하는 것이지, 요청 트래픽을 차단하는 것이 아님.

```python
# system_control.py L233
def disable(self, actor: str = "system", reason: str = "") -> SystemState:
    """
    Disable self-healing system (Kill Switch).
    This immediately stops all self-healing operations.
    """
```

```python
# checks.py L214 — blocked_by_kill_switch() 메시지
block_message="Kill Switch is active: self-healing system is disabled"
```

### 8-2. check()에서 Kill Switch 미검사

`check()` (adaptive.py L1467)은 트래픽 허용/거부를 결정하는 Data Plane이므로 Kill Switch를 검사하지 않음.

- `check()` → `super().check(key)` → `SlidingWindowThrottle.check()` (base.py L190): sliding window 카운트 기반 판단
- Kill Switch 활성화 시에도 기존 `_current_limit` 값으로 트래픽 계속 처리

### 8-3. _maybe_adjust_limit()에서 Kill Switch 검사

`_maybe_adjust_limit()` (adaptive.py L1246)은 limit 자동 조정(Self-healing operation)이므로 Kill Switch 검사 대상.

**검사 순서**:
1. 로컬 플래그 `self._gradient_frozen` (EventBus에 의해 즉시 설정) → 조기 반환
2. `self.is_automation_allowed()` (Mixin Safety Net, 30초 캐시) → 로컬 플래그 동기화 후 반환

이유: EventBus 핸들러 `_handle_kill_switch_activated()` (§3-4)이 `self._gradient_frozen = True`를 즉시 설정하므로, 대부분의 경우 ①에서 걸림. ②는 EventBus 이벤트 유실 시 30초 내 교정.

### 8-4. 기존 LEVEL_3 Freeze와의 관계

```python
# 현재 _gradient_frozen을 설정하는 코드:
# 1) adjust_for_emergency(level=3) → self._gradient_frozen = True (adaptive.py L1675)
# 2) _handle_kill_switch_activated() → self._gradient_frozen = True (신규 §3-4)

# 해제 조건이 다름:
# 1) adjust_for_emergency(level < 3) → self._gradient_frozen = False (adaptive.py L1721)
# 2) _handle_kill_switch_deactivated():
#    → if self._emergency_level < 3: self._gradient_frozen = False
#    → LEVEL_3면 frozen 유지 (LEVEL_3 자체가 freeze 요구)
```

**공유 플래그 충돌 방지**: `_handle_kill_switch_deactivated()`에서 `self._emergency_level < 3` 가드를 두어and, Kill Switch 해제 시 LEVEL_3 Freeze를 실수로 해제하지 않음.

---

## 9. 설계 리뷰 반영 — Break Glass 연동 구체화

### 9-1. Break Glass = "거버넌스 체크 우회"이지 "Throttle OFF"가 아님

```python
# governance.py L173-175
break_glass_enabled: bool = Field(
    default=False,
    description="긴급 상황 시 모든 거버넌스 체크 우회 (PIR 필수)",
)
```

```python
# checks.py L458-470
# 0. Break Glass 체크 (최상단)
if settings.break_glass_enabled:
    return GovernanceCheckResult.allowed_result()  # "자동화 허용" 반환
```

**AdaptiveThrottle에서의 의미**: Break Glass가 해제하는 것은 **Governance가 발동한 Full Stop** (3중 조건: LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED)이지, Rate Limit 로직 자체가 아님.

### 9-2. `check_break_glass` 파라미터 부재 문제

`check_all_governance()` 시그니처 (checks.py L419-427):

```python
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    service_name: str | None = None,
    domain: str | None = None,
    audit_on_block: bool = True,
) -> GovernanceCheckResult:
```

→ `check_break_glass` 파라미터가 **존재하지 않음**. Break Glass는 항상 최상단에서 무조건 확인됨 (checks.py L458).

따라서 `check()` Data Plane에서 Break Glass를 확인하려면 `check_all_governance()`를 호출하는 대신, **Settings를 직접 읽어 로컬 플래그에 동기화**해야 함.

### 9-3. Break Glass 상태 동기화 메서드

```python
# 제안: Control Plane에서 Break Glass 상태를 로컬 플래그로 동기화

def _sync_break_glass_state(self) -> None:
    """Break Glass 상태를 로컬 플래그로 동기화 (Fail-Open)."""
    try:
        from selfhealing.settings.governance import get_governance_settings
        self._break_glass_active = get_governance_settings().break_glass_enabled
    except ImportError:
        logger.debug("[AdaptiveThrottle] Governance settings not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Break glass sync failed: {e}")
```

**호출 위치**: `_maybe_adjust_limit()` 내부 (§7-4 ③) — limit 조정 시마다 Break Glass 상태 갱신.

**Break Glass는 EventBus 이벤트가 아닌 Settings(환경변수) 기반** (governance.py L173):
- `SELFHEALING_GOVERNANCE_BREAK_GLASS_ENABLED=true` 환경변수로 제어
- EventBus 구독이 불가능 → Control Plane에서 주기적 polling 필요
- `_maybe_adjust_limit()`이 RTT 기록마다 호출되므로 실질적으로 `sample_interval_ms` (기본 500ms) 주기로 동기화

### 9-4. check() 내 Break Glass → Full Stop 해제 로직

```python
# check() 도입부

def check(self, key: str, tier_id: str = "standard", ...) -> ThrottleResult:
    # ① Break Glass 활성 + Full Stop → Full Stop 해제 후 표준 로직
    if self._break_glass_active and self._full_stop_active:
        logger.warning("[AdaptiveThrottle] Break Glass: overriding Full Stop")
        self.deactivate_full_stop()
        # deactivate_full_stop() → self._full_stop_active = False
        #                        → self.start_recovery_dampening()
        #                        → 80% → 90% → 100% 점진적 복구 시작

    # ② 기존 Check on Use 패턴
    self.check_and_sync_emergency_state()

    # ③ Recovery Dampening
    if self._recovery_dampening_active:
        self.advance_recovery_dampening()

    # ④ 기존 Rate Limit 로직 (super().check() → SlidingWindowThrottle)
    ...
    # → 무조건 Allowed 반환 금지, 표준 sliding window 판단 수행
```

**`deactivate_full_stop()` 동작** (adaptive.py L2017):
```python
def deactivate_full_stop(self) -> None:
    self._full_stop_active = False
    self.start_recovery_dampening()  # 80% → 90% → 100%
```

→ Break Glass가 Full Stop을 해제하면, Recovery Dampening으로 점진적 limit 복구 후 `super().check(key)` → `SlidingWindowThrottle.check()` (base.py L190)의 표준 sliding window 판단이 실행됨. **무조건 Allowed 반환이 아님**.

---

## 10. 설계 리뷰 반영 — EventBus 즉시 반영 + 캐시 구조

### 10-1. Kill Switch EventBus 구독 (Primary)

```python
# __init__에서 구독 등록 (기존 구독과 동일 위치)

# EventBus 구독 등록
self._subscribe_rate_limit_events()      # 기존
self._subscribe_error_budget_events()    # 기존
self._subscribe_load_shedding_events()   # 기존
self._subscribe_kill_switch_events()     # 신규

def _subscribe_kill_switch_events(self) -> None:
    """Kill Switch 이벤트 구독 (Fail-Open)."""
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus
        bus = get_event_bus()
        bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self._handle_kill_switch_activated)
        bus.subscribe(EventType.KILL_SWITCH_DEACTIVATED, self._handle_kill_switch_deactivated)
        logger.info("[AdaptiveThrottle] Subscribed to kill switch events")
    except ImportError:
        logger.debug("[AdaptiveThrottle] EventBus not available for kill switch")
    except Exception as e:
        logger.warning(f"[AdaptiveThrottle] Failed to subscribe to kill switch events: {e}")
```

**패턴 근거**: `_subscribe_rate_limit_events()` (adaptive.py L616), `_subscribe_error_budget_events()` (adaptive.py L825), `_subscribe_load_shedding_events()` (adaptive.py L771)와 동일한 try/except ImportError Fail-Open 패턴.

**EventType 확인**: `KILL_SWITCH_ACTIVATED`, `KILL_SWITCH_DEACTIVATED`는 이미 정의됨 (event_bus/bus.py L80-81):
```python
KILL_SWITCH_ACTIVATED = "kill_switch_activated"
KILL_SWITCH_DEACTIVATED = "kill_switch_deactivated"
```

### 10-2. Kill Switch 핸들러 (즉시 로컬 반영)

```python
def _handle_kill_switch_activated(self, event) -> None:
    """Kill Switch 활성화 → Gradient Freeze + limit 유지 (즉시)."""
    self._kill_switch_active = True
    self._gradient_frozen = True
    logger.warning("[AdaptiveThrottle] Kill Switch activated: gradient frozen, limit preserved")

    # 감사 로깅
    _record_audit_safe(
        action="throttle_kill_switch_activated",
        old_limit=self._current_limit,
        new_limit=self._current_limit,  # limit 변경 없음
        trigger_source="kill_switch",
    )

def _handle_kill_switch_deactivated(self, event) -> None:
    """Kill Switch 비활성화 → 조건부 Gradient 재개."""
    self._kill_switch_active = False

    # LEVEL_3 Emergency가 활성화되어 있으면 frozen 유지
    # 근거: adjust_for_emergency(level=3) → self._gradient_frozen = True (adaptive.py L1675)
    if self._emergency_level < 3:
        self._gradient_frozen = False

    self.start_recovery_dampening()
    logger.info("[AdaptiveThrottle] Kill Switch deactivated: recovery started")

    # 감사 로깅
    _record_audit_safe(
        action="throttle_kill_switch_deactivated",
        old_limit=self._current_limit,
        new_limit=self._current_limit,
        trigger_source="kill_switch",
    )
```

**`_gradient_frozen` 해제 가드 설명**:

`_gradient_frozen`은 2가지 원인으로 `True`가 될 수 있음:
1. LEVEL_3 Emergency → `adjust_for_emergency(level=3)` (adaptive.py L1675)
2. Kill Switch → `_handle_kill_switch_activated()` (신규)

Kill Switch 해제 시 LEVEL_3가 여전히 활성이면 frozen을 유지해야 함 → `self._emergency_level < 3` 가드 필수.

### 10-3. Governance 캐시는 모듈-레벨 싱글톤 (인스턴스 독립)

```python
# checks.py L311 — 모듈 로드 시 1회 생성
_governance_cache = _create_governance_cache()
```

- `_governance_cache`는 **Python 모듈 레벨 전역 변수**
- `TTLCache._cache: dict[str, tuple[Any, float]]` = 순수 Python dict (checks.py L266)
- AdaptiveThrottle이 여러 인스턴스로 생성되어도 (`get_adaptive_throttle()` 싱글톤 + 테스트 직접 생성) 모든 인스턴스가 **동일한 `_governance_cache`를 공유**
- 따라서 인스턴스별 캐시 분리나 중앙화 설계 **불필요**

### 10-4. `reset_state()` 메서드 확장

기존 `reset_state()` (adaptive.py L1830)에 Governance 플래그 초기화 추가:

```python
def reset_state(self) -> None:
    """...기존 초기화 코드..."""
    # 기존 Emergency 상태 초기화
    self._emergency_mode_active = False
    self._emergency_level = 0
    self._gradient_frozen = False
    self._full_stop_active = False
    ...

    # Governance 연동 상태 초기화 (신규)
    self._kill_switch_active = False
    self._break_glass_active = False
```

---

## 11. 설계 리뷰 반영 — 기존 Emergency sync와의 중복 제거

### 11-1. check()에서의 통합 (3단계 완료)

`check()` (adaptive.py L1598):
```python
# Governance 통합 상태 동기화 (Emergency + Kill Switch + Break Glass, 30초 TTL)
self._sync_governance_state()
```

`_sync_governance_state()` (adaptive.py L2176)는 세 가지 동기화를 통합:
1. `_sync_kill_switch_state()` → `is_system_enabled()` Drift 교정
2. `_sync_break_glass_state()` → Settings 기반 Break Glass 동기화
3. `get_emergency_manager()` → Emergency Level Drift 교정

```python
# adaptive.py L2207
from selfhealing.services.emergency_mode import get_emergency_manager
manager = get_emergency_manager()
current_level = manager.get_current_level().value
```

→ `GracefulDegradationManager` 직접 참조 완전 제거됨. 모든 Emergency 접근은 `get_emergency_manager()` 공개 API 사용.

### 11-2. check()에서 check_all_governance(check_emergency=True)를 추가 호출하지 않는 이유

1. `_sync_governance_state()`가 이미 Emergency + Kill Switch + Break Glass 동기화를 수행 → 중복 호출 불필요
2. `check()`는 Data Plane → Governance 함수를 직접 호출하면 Control/Data Plane 분리 원칙 위반
3. Emergency 결과는 이미 로컬 플래그 (`self._emergency_level`, `self._emergency_mode_active`, `self._full_stop_active`)에 반영되어 `check()` 내부 분기에서 사용됨

### 11-3. 마이그레이션 완료 결과

§4-2의 3단계 마이그레이션 **모두 완료**:

1. **1단계** ✅: `GovernanceCheckMixin` 상속 추가 + `_subscribe_kill_switch_events()` + Break Glass 플래그. 기존 `check_and_sync_emergency_state()` 유지.
2. **2단계** ✅: `_sync_governance_state()`에서 Kill Switch (`_sync_kill_switch_state()` → `is_system_enabled()`) + Break Glass (`_sync_break_glass_state()` → `get_governance_settings()`) + Emergency (`get_emergency_manager()`)를 통합 동기화. 동일 30초 TTL.
3. **3단계** ✅: `check_and_sync_emergency_state()` → 하위호환 래퍼 (1줄, `_sync_governance_state()` 위임). `GracefulDegradationManager` 직접 참조 제거. `check()` 호출 사이트도 `_sync_governance_state()` 직접 호출로 변경. Emergency 전용 sync 코드 일원화 완료.

---

## 12. 설계 리뷰 반영 — Fail-Open 정책 확인

### 12-1. 코드에 이미 확립된 Fail-Open 정책

Governance 체크 함수 3개 모두 `except` 블록에서 명시적 Fail-Open:

```python
# is_system_enabled() — checks.py L353
except Exception as e:
    # Fail-open: 시스템 상태 확인 실패 시 활성화 가정
    return True

# is_emergency_blocking() — checks.py L400
except Exception as e:
    # Fail-open: 비상 모드 확인 실패 시 허용
    return False, "UNKNOWN"

# is_error_budget_blocking() — checks.py L422
except Exception as e:
    # Fail-open: 에러 예산 확인 실패 시 허용
    return False, 100.0, 0.0
```

### 12-2. AdaptiveThrottle EventBus 구독 Fail-Open 패턴

기존 4개 구독 모두 동일한 Fail-Open 패턴:

```python
# _subscribe_rate_limit_events() — adaptive.py L616
except ImportError:
    logger.debug("[AdaptiveThrottle] EventBus not available for subscription")

# _subscribe_error_budget_events() — adaptive.py L825
except ImportError:
    logger.debug("[AdaptiveThrottle] EventBus not available for error budget subscription")

# _subscribe_load_shedding_events() — adaptive.py L771
except ImportError:
    logger.debug("[AdaptiveThrottle] EventBus not available for load shedding subscription")
```

→ 신규 `_subscribe_kill_switch_events()`도 동일 패턴 적용 (§10-1).

### 12-3. Fail-Open 시 동작 정리

| 장애 상황 | Fail-Open 결과 | AdaptiveThrottle 동작 |
|----------|---------------|---------------------|
| Governance Import 실패 | `is_automation_allowed()` → True | limit 조정 계속 (기존 동작) |
| EventBus Import 실패 | Kill Switch 구독 안 됨 | Kill Switch 미반영, 기존 동작 유지 |
| `SystemControlManager` 예외 | `is_system_enabled()` → True | Kill Switch 미감지, 기존 동작 유지 |
| `GracefulDegradationManager` 예외 | `is_emergency_blocking()` → False | Emergency 미감지, 기존 동작 유지 |
| Settings 로드 실패 | `_sync_break_glass_state()` → 플래그 미변경 | Break Glass 미감지, 기존 동작 유지 |

**정책**: 모든 장애 상황에서 AdaptiveThrottle은 **기존 limit으로 트래픽 처리를 계속**한다. Governance 연동 실패가 서비스 가용성에 영향을 주지 않음.

---

## 13. 최종 영향 범위 (1-3단계 구현 완료)

| 파일 | 변경 내용 |
|------|-----------|
| `services/throttle/adaptive.py` | `GovernanceCheckMixin` 상속 추가, `_kill_switch_active`/`_break_glass_active` 플래그 추가, `_subscribe_kill_switch_events()` 추가, `_sync_break_glass_state()` 추가, `_maybe_adjust_limit()` Governance Safety Net 추가, `check()` Break Glass 체크 추가, `reset_state()` 확장, `_sync_governance_state()` 통합 동기화 메서드 (Kill Switch + Break Glass + Emergency), `_sync_kill_switch_state()` 추가 (`is_system_enabled()` Drift 교정), `check_and_sync_emergency_state()` → 하위호환 래퍼, `GracefulDegradationManager` 직접 import 완전 제거, `get_emergency_manager()` 공개 API로 교체 |
| `services/governance/checks.py` | **변경 없음** (기존 Mixin/함수/캐시 재사용) |
| `services/event_bus/bus.py` | **변경 없음** (`KILL_SWITCH_ACTIVATED`, `KILL_SWITCH_DEACTIVATED` 이미 존재, L80-81) |
| `settings/governance.py` | **변경 없음** (`break_glass_enabled` 이미 존재, L173) |

---

## 14. 최종 검증 항목 (1-3단계 모두 구현 완료)

### 14-1. Kill Switch 연동

- [x] EventBus `KILL_SWITCH_ACTIVATED` 수신 시 `self._kill_switch_active = True`, `self._gradient_frozen = True` 즉시 설정
- [x] EventBus `KILL_SWITCH_DEACTIVATED` 수신 시 `self._kill_switch_active = False`, LEVEL_3 아닐 때만 `self._gradient_frozen = False`
- [x] Kill Switch 활성화 중 `_maybe_adjust_limit()` 진입 시 `self._gradient_frozen` 조기 반환
- [x] Kill Switch 활성화 중 `check()` → 기존 limit으로 트래픽 계속 처리 (차단 아님)
- [x] `is_automation_allowed()` Safety Net: EventBus 유실 시 30초 내 Governance 캐시로 Kill Switch 감지

### 14-2. Break Glass 연동

- [x] `_sync_break_glass_state()` → `get_governance_settings().break_glass_enabled` 읽어 `self._break_glass_active` 갱신
- [x] `check()` 진입 시 `self._break_glass_active and self._full_stop_active` → `deactivate_full_stop()` 호출
- [x] `deactivate_full_stop()` 후 Recovery Dampening (80%→90%→100%) 정상 시작
- [x] Break Glass 비활성화 후 Full Stop 3중 조건 재충족 시 Full Stop 재활성화

### 14-3. 성능 (Control/Data Plane 분리)

- [x] `check()` Hot Path에서 Governance 함수 직접 호출 **없음** (로컬 플래그만)
- [x] `_maybe_adjust_limit()`에서 `is_automation_allowed()` 호출 시 `_governance_cache` TTL 30초 → 실제 체크는 30초마다 1회
- [x] `_governance_cache`는 순수 Python dict (checks.py L266): `self._cache: dict[str, tuple[Any, float]]`

### 14-4. Fail-Open

- [x] Governance Import 실패 시 Fail-Open (`is_automation_allowed()` → True)
- [x] EventBus Import 실패 시 Kill Switch 구독 스킵 (Fail-Open)
- [x] Settings Import 실패 시 Break Glass 플래그 미변경 (Fail-Open)

### 14-5. 일관성

- [x] AutoTuningService와 동일한 governance 체크 수준 달성 (Kill Switch + Emergency + Error Budget + Break Glass)
- [x] 기존 `check_and_sync_emergency_state()` → 하위호환 래퍼 (1줄, `_sync_governance_state()` 위임)
- [x] `_sync_governance_state()`에서 Kill Switch + Break Glass + Emergency 통합 동기화 (30초 TTL)
- [x] `GracefulDegradationManager` 직접 import 완전 제거, `get_emergency_manager()` 공개 API 사용
- [x] `_sync_kill_switch_state()`: `is_system_enabled()` → Kill Switch 활성화/비활성화 Drift 교정
- [x] Kill Switch 비활성화 Drift 교정 시 LEVEL_3 가드 유지 (`self._emergency_level < 3`)
- [x] Kill Switch 비활성화 Drift 교정 시 Recovery Dampening 시작
- [x] `_kill_switch_active` / `_break_glass_active` 네이밍이 기존 `_full_stop_active` / `_429_reduction_active` 패턴과 일치
- [x] Governance 차단 시 Audit Log에 `operation_name="adaptive_throttle:limit_adjustment"` 기록

### 14-6. 테스트 (2-3단계)

- [x] 통합 동기화 19개 테스트 통과 (`test_adaptive_throttle_governance_sync.py`)
  - `_sync_governance_state()` → `_sync_kill_switch_state()` + `_sync_break_glass_state()` + Emergency 동기화 호출 확인
  - Kill Switch Drift 교정 (활성화/비활성화) 8개 테스트
  - 하위호환 래퍼 `check_and_sync_emergency_state()` → `_sync_governance_state()` 위임 확인
  - `sync_emergency_state_on_init()` → `get_emergency_manager()` 사용 확인
  - `check()` → `_sync_governance_state()` 호출 확인
  - `GracefulDegradationManager` 직접 import 미존재 소스코드 검증
- [x] 기존 상태 동기화 16개 테스트 통과 (`test_throttle_state_sync.py`, mock 패치 업데이트)
- [x] 기존 선제적 보호 8개 테스트 통과 (`test_throttle_preemptive_protection.py`, mock 패치 업데이트)
- [x] Governance 통합 47개 테스트 통과 (`test_adaptive_throttle_governance_integration.py`, TTL 가드 추가)
- [x] **전체 90개 테스트 통과 (6.39s)**
