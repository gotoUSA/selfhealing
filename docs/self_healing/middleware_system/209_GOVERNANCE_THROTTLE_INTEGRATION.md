# 209. Governance Checks ↔ AdaptiveThrottle 연동 계획

> **상태**: ✅ 연동 추천
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
# adaptive.py L1877-1936
def check_and_sync_emergency_state(self) -> bool:
    """Check on Use 패턴: TTL 만료 시 Emergency 상태 재확인."""
    # TTL 확인
    if now - self._last_emergency_check_time < self._emergency_cache_ttl_seconds:
        return False

    manager = GracefulDegradationManager()
    current_level = manager.get_current_level().value

    if current_level != self._emergency_level:
        self.adjust_for_emergency(current_level)
```

**체크하는 것**:
- ✅ Emergency Level (Check-on-Use, 30초 TTL)
- ✅ Error Budget (EventBus `ERROR_BUDGET_*` 구독, L735)
- ❌ Kill Switch — **미체크**
- ❌ Break Glass — **미지원**
- ❌ `check_all_governance()` — **미호출**

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

### 2-3. 안전 체크 일관성 불일치

| 체크 항목 | AutoTuningService | AdaptiveThrottle |
|-----------|-------------------|------------------|
| Kill Switch | ✅ `check_all_governance()` | ❌ 없음 |
| Emergency Level | ✅ `check_all_governance()` | △ 자체 sync (`check_and_sync_emergency_state()`) |
| Error Budget | ✅ `check_all_governance()` | △ EventBus 구독 (별도 로직) |
| Break Glass | ✅ `check_all_governance()` | ❌ 없음 |
| Audit on Block | ✅ `audit_on_block=True` | △ 자체 감사 로깅 (`_record_audit_safe()`) |

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

### 4-1. 정리 대상

현재 `check_and_sync_emergency_state()` (adaptive.py L1877)는 `GracefulDegradationManager`를 직접 참조:

```python
# adaptive.py L1898
from selfhealing.services.emergency_mode.manager import GracefulDegradationManager
manager = GracefulDegradationManager()
current_level = manager.get_current_level().value
```

Governance 연동 후에는 `is_emergency_blocking()` (checks.py L370)이 동일 체크를 수행:

```python
# checks.py L394
from selfhealing.services.emergency_mode import get_emergency_manager
manager = get_emergency_manager()
level = manager.get_current_level()
```

### 4-2. 마이그레이션 방안

1. **1단계**: `GovernanceCheckMixin` 추가 + 기존 자체 sync 유지 (병렬 동작)
2. **2단계**: 기존 `check_and_sync_emergency_state()` 내부에서 `self.check_governance()` 호출로 대체
3. **3단계**: Emergency 전용 sync 코드 제거, Governance 통합 체크에 일원화

---

## 5. 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| `services/throttle/adaptive.py` | `GovernanceCheckMixin` 상속 추가, Kill Switch 구독, Break Glass 체크 |
| `services/governance/checks.py` | 변경 없음 (기존 Mixin/함수 재사용) |
| `services/event_bus.py` | Kill Switch 이벤트 타입 확인 (이미 `KILL_SWITCH_ACTIVATED` 존재, L1860 참조) |

---

## 6. 검증 항목

- [ ] Kill Switch 활성화 시 AdaptiveThrottle gradient가 freeze 되는지
- [ ] Break Glass 활성화 시 Full Stop이 해제되는지
- [ ] `check_all_governance()` TTL 캐시(30초)로 check() 성능 영향 최소인지
- [ ] Governance 차단 시 Audit Log에 `operation_name="adaptive_throttle:limit_adjustment"` 기록되는지
- [ ] Governance 패키지 Import 실패 시 Fail-Open으로 기존 동작 유지
- [ ] AutoTuningService와 동일한 governance 체크 수준(Kill Switch + Emergency + Error Budget) 달성
