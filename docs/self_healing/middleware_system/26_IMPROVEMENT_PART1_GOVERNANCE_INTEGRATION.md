# Part 1: Governance Integration 개선 구현 가이드

**문서 버전**: 1.2.0  
**작성일**: 2026-01-07  
**최종 수정**: 2026-01-07 (구현 완료)  
**근거 코드**: 실제 소스 코드 분석 기반  
**구현 상태**: ✅ **구현 완료**

---

## 구현 완료 요약

| 항목 | 상태 | 구현 내용 |
|------|------|----------|
| SafetyGuard Emergency Mode | ✅ 완료 | `_check_emergency_mode()`, `BlockReason.EMERGENCY_MODE_ACTIVE` 추가 |
| AutoTuningService Governance | ✅ 완료 | `_check_governance_before_adjustment()`, `start()` 체크 추가 |
| 단위 테스트 | ✅ 완료 | 15개 테스트 작성 및 통과 |

**변경된 파일:**
- [safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py)
- [auto_tuning/service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py)
- [tests/chaos/test_safety_guard_emergency_mode.py](../../../packages/selfhealing-python/tests/chaos/test_safety_guard_emergency_mode.py) (신규)
- [tests/unit/test_auto_tuning_governance.py](../../../packages/selfhealing-python/tests/unit/test_auto_tuning_governance.py) (신규)

---

## 1. 현황 분석

### 1.1 현재 Governance 체크 구현 위치

#### governance_checks.py - 통합 Governance 체크

[governance_checks.py](../../../packages/selfhealing-python/src/selfhealing/services/governance_checks.py) 파일에서 확인된 핵심 함수들:

```python
# governance_checks.py L401-450
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int = 2,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    service_name: Optional[str] = None,
    domain: Optional[str] = None,
    audit_on_block: bool = True,
) -> GovernanceCheckResult:
    """
    모든 거버넌스 체크를 순차적으로 수행.
    
    체크 순서:
    1. Kill Switch (enabled일 때)
    2. Emergency Level (enabled일 때)
    3. Error Budget (enabled일 때)
    """
```

**제공되는 데코레이터** (L507-580):
```python
@require_system_enabled      # Kill Switch 체크
@require_not_emergency(min_level=2)  # Emergency Mode 체크
@require_error_budget()      # Error Budget 체크
```

### 1.2 ChaosSchedulerService 분석 결과

#### ✅ Kill Switch: SafetyGuard 경유로 **이미 구현됨**

[scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) 코드 추적 결과:

```
ChaosSchedulerService.execute_now()
    └─► _check_safety_conditions()           # scheduler.py L468-485
            └─► SafetyGuard.check()          # safety_guard.py
                    └─► _run_core_checks()   # safety_guard.py L411-423
                            └─► _check_kill_switch_status()
                                    └─► _check_kill_switch()  # L530-539
                                            └─► SystemControlManager.is_selfhealing_enabled()
                                                    ✅ 글로벌 Kill Switch 체크
```

**SafetyGuard._check_kill_switch() 구현** ([safety_guard.py#L530](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py#L530)):

```python
def _check_kill_switch(self) -> bool:
    """Check if kill switch is active."""
    try:
        from selfhealing.services.system_control import get_system_control
        
        control = get_system_control()
        return not control.is_selfhealing_enabled()
    except Exception as e:
        logger.warning(f"[SafetyGuard] Could not check kill switch: {e}")
        return False  # Fail-open
```

#### ❌ Emergency Mode: SafetyGuard에 **구현 없음**

SafetyGuard의 `_run_core_checks()` 분석 ([safety_guard.py#L411-423](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py#L411)):

```python
def _run_core_checks(self, result: SafetyCheckResult, experiment_id: str) -> bool:
    # 1. Check global block
    if self._check_global_block(result):
        return True

    # 2. Check kill switch  ← ✅ 있음
    if self._check_kill_switch_status(result):
        return True

    # 3. Check error budget ← ✅ 있음
    if self._check_error_budget_status(result, experiment_id):
        return True

    # ❌ Emergency Mode 체크 없음
    return False
```

#### SafetyGuard 체크 항목 현황

| 체크 항목 | 구현 여부 | 위치 |
|----------|----------|------|
| Kill Switch | ✅ 있음 | `_check_kill_switch()` |
| Error Budget | ✅ 있음 | `_check_error_budget()` |
| System Health | ✅ 있음 | `_check_system_health()` |
| Active Incidents | ✅ 있음 | `_check_active_incidents()` |
| Deployment Freeze | ✅ 있음 | `_check_deployment_freeze()` |
| Cooldown | ✅ 있음 | `_check_cooldown()` |
| **Emergency Mode** | ❌ **없음** | 추가 필요 |

### 1.3 AutoTuningService 분석 결과

[service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py) 분석 결과:

```python
# service.py L39-60
class AutoTuningService:
    """
    자율 조정 서비스
    
    RuntimeFeedbackLoop + DecisionEngine + SafetyBounds + AutoRollbackGuard를
    통합하여 완전한 자율 조정 기능을 제공합니다.
    """
```

**현재 상태**:
- `trigger_emergency_recovery()` 메서드 존재 (L173)
- SafetyGuard를 사용하지 않음
- `check_all_governance()` 호출 없음
- 조정 실행 전 Kill Switch / Emergency Mode 체크 없음

**grep 검색 결과**: `governance|is_system_enabled|SafetyGuard` → **No matches found**

### 1.4 Governance 연결이 불필요한 서비스

#### CircuitBreakerService - Governance 불필요

[service.py](../../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py) 분석 결과:

```python
# circuit_breaker/service.py - Governance 연결 없음
class CircuitBreakerService(ProtectionMixin, ManualControlMixin):
    """
    Circuit Breaker Service.
    Provides management operations for circuit breaker states.
    Designed for manual (toggle-based) control by operators.
    """
```

**Governance 불필요 이유:**
- **동기적 요청 게이트**: 개별 HTTP 요청 진행 중 동작
- **이미 시작된 요청**: 요청 중간에 Kill Switch로 차단 불가
- **수동 제어 설계**: 운영자가 직접 `force_open()`, `force_close()` 호출
- **상태 조회만 수행**: `should_allow()`는 단순 상태 조회

#### RetryHandler - Governance 불필요

[retry_handler.py](../../../packages/selfhealing-python/src/selfhealing/services/retry_handler.py) 분석 결과:

```python
# retry_handler.py L35-40
def _is_system_enabled() -> bool:
    """Check if self-healing system is enabled (Kill Switch not activated)."""
    try:
        from selfhealing.services.system_control import SystemControlManager
        manager = SystemControlManager()
        return manager.is_enabled()
    except Exception:
        return True  # Fail-open
```

**Governance 불필요 이유:**
- **동기적 재시도**: 개별 요청 내 동기 루프로 재시도
- **이미 Kill Switch 참조**: `_is_system_enabled()` 함수가 존재
- **요청 범위**: Emergency Mode 체크는 요청 단위에서 무의미
- **배치 작업 아님**: Celery Beat 같은 자동화 아님

### 1.4 Governance 연결 기준 정리

| 구분 | Governance 필요 | 예시 |
|------|-----------------|------|
| **배치 자동화** | ✅ 필요 | ReplayService, ChaosScheduler, AutoTuning |
| **스케줄러 실행** | ✅ 필요 | Celery Beat Task, Cron Job |
| **동기적 게이트** | ❌ 불필요 | CircuitBreaker, RetryHandler |
| **수동 API** | ❌ 불필요 | Admin force_open/force_close |

---

## 2. 구현 계획

### 2.1 SafetyGuard에 Emergency Mode 체크 추가

#### 목표
Chaos 실험 실행 전 Emergency Mode (LEVEL_2+) 상태에서 차단

#### 수정 위치
[safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py)

#### BlockReason Enum 확장

```python
# safety_guard.py - BlockReason enum에 추가
class BlockReason(str, Enum):
    # ... 기존 항목 ...
    
    EMERGENCY_MODE_ACTIVE = "emergency_mode_active"
    """Emergency mode is active (LEVEL_2+)."""
```

#### SafetyCheckResult 필드 추가

```python
# safety_guard.py - SafetyCheckResult에 필드 추가
@dataclass
class SafetyCheckResult:
    # ... 기존 필드 ...
    
    # Emergency Mode
    emergency_mode_active: bool = False
    emergency_level: str = "NORMAL"
```

#### Emergency Mode 체크 메서드 구현

```python
# safety_guard.py 수정안
class SafetyGuard:
    
    def _check_emergency_mode(self) -> Dict[str, Any]:
        """Check current emergency mode status."""
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager
            from selfhealing.services.emergency_mode.enums import EmergencyLevel
            
            manager = get_emergency_manager()
            level = manager.get_current_level()
            
            return {
                "active": level.value >= EmergencyLevel.LEVEL_2.value,
                "level": level.name,
                "level_value": level.value,
            }
        except Exception as e:
            logger.warning(f"[SafetyGuard] Could not check emergency mode: {e}")
            # Fail-open: 비상 모드 확인 실패 시 허용
            return {"active": False, "level": "UNKNOWN", "level_value": 0}
    
    def _check_emergency_mode_status(self, result: SafetyCheckResult) -> bool:
        """Check emergency mode status. Returns True if blocked."""
        result.checks_performed.append("emergency_mode")
        emergency_result = self._check_emergency_mode()
        result.emergency_mode_active = emergency_result["active"]
        result.emergency_level = emergency_result["level"]
        
        if emergency_result["active"]:
            result.status = SafetyStatus.BLOCKED.value
            result.allowed = False
            result.block_reason = BlockReason.EMERGENCY_MODE_ACTIVE.value
            result.block_message = (
                f"Emergency mode {emergency_result['level']} is active: "
                f"chaos experiments blocked"
            )
            result.checks_failed.append("emergency_mode")
            return True
        
        result.checks_passed.append("emergency_mode")
        return False
```

#### _run_core_checks() 수정

```python
# safety_guard.py - _run_core_checks() 수정
def _run_core_checks(self, result: SafetyCheckResult, experiment_id: str) -> bool:
    """Run core safety checks (always required). Returns True if blocked."""
    # 1. Check global block
    if self._check_global_block(result):
        return True

    # 2. Check kill switch
    if self._check_kill_switch_status(result):
        return True

    # 3. ✅ 신규: Check emergency mode (LEVEL_2+에서 차단)
    if self._check_emergency_mode_status(result):
        return True

    # 4. Check error budget (CRITICAL)
    if self._check_error_budget_status(result, experiment_id):
        return True

    return False
```

### 2.2 AutoTuningService Governance 통합

#### 목표
자율 조정 실행 전 Governance 체크 수행

#### 수정 위치
[service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py)

#### 구현 코드

```python
# service.py 수정안
# 상단에 import 추가
from selfhealing.services.governance_checks import (
    check_all_governance,
    require_not_emergency,
    GovernanceCheckResult,
)

class AutoTuningService:
    
    def _check_governance_before_adjustment(
        self, 
        module: str,
        adjustment_type: str = "automatic"
    ) -> GovernanceCheckResult:
        """
        조정 전 Governance 체크.
        
        Args:
            module: 조정 대상 모듈 (circuit_breaker, retry 등)
            adjustment_type: 조정 유형 (automatic, manual, rollback)
        
        Returns:
            GovernanceCheckResult
        """
        return check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,  # LEVEL_2 이상에서 차단
            check_error_budget=True,
            operation_name=f"auto_tuning:{module}:{adjustment_type}",
            service_name="auto_tuning",
            domain=module,
            audit_on_block=True,
        )
    
    def start(self) -> bool:
        """서비스 시작"""
        with self._lock:
            # ✅ 신규: 시작 전 Governance 체크
            gov_result = self._check_governance_before_adjustment(
                module="all",
                adjustment_type="service_start"
            )
            if not gov_result.allowed:
                logger.warning(
                    f"[AutoTuningService] Start blocked by governance: "
                    f"{gov_result.block_message}"
                )
                return False
            
            # 기존 로직...
            self.adjustment_recorder.start_session("AutoTuningService started")
            self.feedback_loop.start()
            self.rollback_guard.start()
            
            logger.info("[AutoTuningService] Started")
            return True
    
    # RuntimeFeedbackLoop 콜백에도 Governance 체크 추가
    def _on_adjustment_proposed(
        self, 
        module: str, 
        proposed_value: Any
    ) -> bool:
        """
        RuntimeFeedbackLoop에서 조정을 제안할 때 호출되는 콜백.
        
        Returns:
            True if adjustment allowed, False otherwise
        """
        gov_result = self._check_governance_before_adjustment(
            module=module,
            adjustment_type="automatic"
        )
        
        if not gov_result.allowed:
            self._record_audit_event(
                action="auto_tuning_blocked",
                details={
                    "module": module,
                    "proposed_value": proposed_value,
                    "block_reason": gov_result.block_reason.value 
                        if gov_result.block_reason else "unknown",
                }
            )
            return False
        
        return True
```

### 2.3 SafetyGuard Audit 통합 (선택사항)

Emergency Mode 차단 시 Audit 로그에 기록:

```python
# safety_guard.py - _check_emergency_mode_status() 내부
if emergency_result["active"]:
    # Audit 기록
    try:
        from selfhealing.services.audit_helpers import log_governance_blocked_audit
        
        log_governance_blocked_audit(
            block_reason="emergency_mode",
            operation_name=f"chaos_experiment:{experiment_id}",
            details={
                "emergency_level": emergency_result["level"],
                "experiment_id": experiment_id,
            },
        )
    except Exception as e:
        logger.debug(f"[SafetyGuard] Audit logging failed: {e}")
```

---

## 3. 테스트 계획

### 3.1 단위 테스트

```python
# tests/chaos/test_safety_guard_emergency_mode.py

import pytest
from unittest.mock import patch, MagicMock

class TestSafetyGuardEmergencyMode:
    """SafetyGuard Emergency Mode 체크 테스트."""
    
    def test_blocked_by_emergency_level_2(self):
        """Emergency Mode LEVEL_2에서 실험 차단."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        guard = get_safety_guard()
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager"
        ) as mock_em:
            mock_manager = MagicMock()
            mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_2
            mock_em.return_value = mock_manager
            
            result = guard.check(experiment_id="test-001")
            
            assert not result.allowed
            assert result.block_reason == "emergency_mode_active"
            assert result.emergency_level == "LEVEL_2"
    
    def test_blocked_by_emergency_level_3(self):
        """Emergency Mode LEVEL_3에서 실험 차단."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        guard = get_safety_guard()
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager"
        ) as mock_em:
            mock_manager = MagicMock()
            mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_3
            mock_em.return_value = mock_manager
            
            result = guard.check(experiment_id="test-002")
            
            assert not result.allowed
            assert result.block_reason == "emergency_mode_active"
            assert result.emergency_level == "LEVEL_3"
    
    def test_allowed_on_level_1(self):
        """Emergency Mode LEVEL_1에서는 허용."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        guard = get_safety_guard()
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager"
        ) as mock_em:
            mock_manager = MagicMock()
            mock_manager.get_current_level.return_value = EmergencyLevel.LEVEL_1
            mock_em.return_value = mock_manager
            
            # 다른 체크들도 통과하도록 설정
            with patch.object(guard, '_check_kill_switch', return_value=False):
                with patch.object(guard, '_check_error_budget', return_value={"remaining_percent": 100.0}):
                    result = guard.check(experiment_id="test-003")
            
            # emergency_mode 체크는 통과
            assert "emergency_mode" in result.checks_passed
    
    def test_allowed_on_normal(self):
        """Emergency Mode NORMAL에서는 허용."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        guard = get_safety_guard()
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager"
        ) as mock_em:
            mock_manager = MagicMock()
            mock_manager.get_current_level.return_value = EmergencyLevel.NORMAL
            mock_em.return_value = mock_manager
            
            with patch.object(guard, '_check_kill_switch', return_value=False):
                with patch.object(guard, '_check_error_budget', return_value={"remaining_percent": 100.0}):
                    result = guard.check(experiment_id="test-004")
            
            assert "emergency_mode" in result.checks_passed
    
    def test_fail_open_on_exception(self):
        """Emergency Mode 확인 실패 시 Fail-open."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        guard = get_safety_guard()
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager",
            side_effect=ImportError("Module not found")
        ):
            with patch.object(guard, '_check_kill_switch', return_value=False):
                with patch.object(guard, '_check_error_budget', return_value={"remaining_percent": 100.0}):
                    result = guard.check(experiment_id="test-005")
            
            # Fail-open: 예외 발생 시 허용
            assert "emergency_mode" in result.checks_passed


class TestAutoTuningGovernanceIntegration:
    """AutoTuningService Governance 통합 테스트."""
    
    def test_start_blocked_by_kill_switch(self):
        """Kill Switch 활성화 시 서비스 시작 차단."""
        # 테스트 구현
        pass
    
    def test_adjustment_blocked_during_emergency(self):
        """Emergency Mode 중 조정 차단."""
        # 테스트 구현
        pass
```

---

## 4. 영향 분석

### 4.1 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `services/chaos/safety_guard.py` | 수정 | Emergency Mode 체크 추가 |
| `services/auto_tuning/service.py` | 수정 | Governance 통합 체크 추가 |
| `tests/chaos/test_safety_guard_emergency_mode.py` | 신규 | Emergency Mode 테스트 |
| `tests/.../test_auto_tuning_governance.py` | 신규 | AutoTuning 통합 테스트 |

### 4.2 ChaosSchedulerService - 변경 불필요

**이유**: SafetyGuard 경유로 이미 Kill Switch 체크됨. SafetyGuard에 Emergency Mode만 추가하면 자동 적용.

```
ChaosSchedulerService.execute_now()
    └─► _check_safety_conditions()
            └─► SafetyGuard.check()  ← 여기에 Emergency Mode 추가하면 끝
```

### 4.3 하위 호환성

- **기존 API 유지**: SafetyCheckResult에 필드 추가만 (기존 필드 변경 없음)
- **force 파라미터**: 기존처럼 `force=True`로 Safety 체크 우회 가능
- **Fail-open 정책**: Emergency Mode 확인 실패 시 기본적으로 허용

### 4.4 위험도

| 항목 | 위험도 | 완화 방안 |
|------|--------|----------|
| 서비스 장애 | 낮음 | Fail-open 정책 유지 |
| 성능 영향 | 최소 | Emergency Mode는 싱글톤 캐시 활용 |
| 호환성 | 없음 | 기존 필드 변경 없음 |

---

## 5. 구현 우선순위

| 순위 | 작업 | 중요도 | 예상 공수 |
|------|------|--------|----------|
| 1 | SafetyGuard Emergency Mode 추가 | 🔴 Critical | 1시간 |
| 2 | AutoTuningService Governance 통합 | 🟡 High | 2시간 |
| 3 | SafetyGuard Audit 통합 | 🟢 Medium | 30분 |
| 4 | 단위 테스트 작성 | 🟢 Medium | 2시간 |

**총 예상 공수**: 5.5시간

---

## 6. 현황 요약 (구현 완료 후)

### 6.1 ChaosSchedulerService 체크 항목 현황

| 체크 항목 | 구현 여부 | 위치 |
|----------|----------|------|
| Kill Switch | ✅ 있음 | SafetyGuard._check_kill_switch() |
| Error Budget | ✅ 있음 | SafetyGuard._check_error_budget() + scheduler._check_error_budget_gate() |
| System Health | ✅ 있음 | SafetyGuard._check_system_health() |
| Active Incidents | ✅ 있음 | SafetyGuard._check_active_incidents() |
| Deployment Freeze | ✅ 있음 | SafetyGuard._check_deployment_freeze() |
| Blast Radius | ✅ 있음 | scheduler._check_blast_radius_conditions() |
| Approval Status | ✅ 있음 | scheduler._check_pre_execution_conditions() |
| **Emergency Mode** | ✅ **구현 완료** | SafetyGuard._check_emergency_mode() |

### 6.2 AutoTuningService 체크 항목 현황

| 체크 항목 | 구현 여부 | 비고 |
|----------|----------|------|
| Kill Switch | ✅ 구현 완료 | _check_governance_before_adjustment() |
| Emergency Mode | ✅ 구현 완료 | _check_governance_before_adjustment() |
| Error Budget | ✅ 구현 완료 | _check_governance_before_adjustment() |

---

## 7. 관련 문서

- [safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py) - Chaos Safety Guard (Emergency Mode 구현 완료)
- [governance_checks.py](../../../packages/selfhealing-python/src/selfhealing/services/governance_checks.py) - Governance 핵심 구현
- [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) - ChaosScheduler 서비스
- [service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py) - AutoTuning 서비스 (Governance 구현 완료)
- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](../16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Governance 설계 문서
- [test_safety_guard_emergency_mode.py](../../../packages/selfhealing-python/tests/chaos/test_safety_guard_emergency_mode.py) - Emergency Mode 테스트
- [test_auto_tuning_governance.py](../../../packages/selfhealing-python/tests/unit/test_auto_tuning_governance.py) - AutoTuning Governance 테스트
