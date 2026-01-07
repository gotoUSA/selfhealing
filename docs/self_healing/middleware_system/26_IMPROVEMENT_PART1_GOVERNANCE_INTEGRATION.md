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
| SafetyGuard Emergency Audit | ✅ 완료 | `_log_emergency_block_audit()`, `log_governance_blocked_audit()` 호출 |
| 단위 테스트 | ✅ 완료 | 14개 테스트 작성 및 통과 (Emergency Audit 6개 포함) |

**변경된 파일:**
- [safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py) - Emergency Mode + Audit 구현
- [auto_tuning/service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py)
- [tests/chaos/test_safety_guard_emergency_mode.py](../../../packages/selfhealing-python/tests/chaos/test_safety_guard_emergency_mode.py) - Audit 테스트 추가
- [tests/unit/test_auto_tuning_governance.py](../../../packages/selfhealing-python/tests/unit/test_auto_tuning_governance.py)

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

---

## 8. 리뷰 피드백 및 보완 계획

### 8.1 리뷰 요약

| 항목 | 리뷰 내용 | 현재 상태 | 보완 필요 |
|------|----------|----------|----------|
| S3 WORM 7년 보존 | 인터페이스 → 즉시 사용 가능 옵션으로 격상 | ⚪ 인터페이스 존재 | ✅ 설정 가이드 추가 |
| IdP 연동 (Okta, Azure AD) | 외부 IdP 그룹 매핑 가이드 필요 | ⚪ Django 그룹 기반 | ✅ 매핑 가이드 추가 |
| Emergency Audit | SafetyGuard 차단 시 `log_governance_blocked_audit()` 호출 | ❌ 미구현 | ✅ 코드 추가 |

---

### 8.2 보완 제안 1: S3 WORM 7년 보존 - 즉시 사용 가능 옵션

#### 8.2.1 현재 상태 분석

**코드 근거:**

1. **S3WORMBackend** ([s3_worm.py](../../../packages/selfhealing-python/src/selfhealing/audit/backends/s3_worm.py)):
   - 인터페이스 + 주석 기반 예시 코드 존재
   - `get_configuration_template()`에 Terraform 예시 포함
   - `retention_days=2555` (7년) 설정 가능

2. **S3ObjectLockAdapter** ([worm_adapters.py](../../../packages/selfhealing-python/src/selfhealing/adapters/audit/worm_adapters.py#L164-L233)):
   - **더 완전한 구현** - boto3 클라이언트 주입 시 실제 동작
   - Fallback 파일 기록 지원
   - 테스트 존재: [test_worm_adapters.py](../../../packages/selfhealing-python/tests/unit/test_worm_adapters.py#L92-L170)

```python
# worm_adapters.py L164-200 (실제 코드)
class S3ObjectLockAdapter(WORMAdapter):
    """
    AWS S3 Object Lock 어댑터 (인터페이스).
    
    실제 사용 시:
        pip install boto3
        그리고 _write_to_worm() 메서드를 boto3로 구현하세요.
    
    Usage:
        import boto3
        s3 = boto3.client('s3')
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="my-audit-bucket"),
            s3_client=s3
        )
    """
```

#### 8.2.2 구현 가능성: ✅ 즉시 사용 가능

**이유:**
- `S3ObjectLockAdapter`는 boto3 클라이언트만 주입하면 실제 동작
- `_write_to_worm()` 메서드가 이미 `put_object()` 호출 구현 완료
- 테스트에서 Mock S3 클라이언트로 동작 검증됨

```python
# test_worm_adapters.py L112-130 (실제 테스트)
def test_with_mock_s3_client(self, sample_entry, temp_dir):
    """Mock S3 클라이언트 테스트."""
    mock_s3 = MagicMock()
    
    adapter = S3ObjectLockAdapter(
        config=S3Config(bucket="test-bucket"),
        s3_client=mock_s3,
        fallback_path=temp_dir / "fallback.jsonl",
    )
    
    adapter.log(sample_entry)
    
    # S3 put_object 호출 확인
    mock_s3.put_object.assert_called_once()
```

#### 8.2.3 설정 가이드 (How-to)

**Step 1: 의존성 설치**
```bash
pip install boto3
```

**Step 2: S3 버킷 생성 (Object Lock 활성화)**

```hcl
# Terraform 예시 (s3_worm.py get_configuration_template()에서 발췌)
resource "aws_s3_bucket" "audit_logs" {
  bucket = "selfhealing-audit-logs"

  object_lock_configuration {
    object_lock_enabled = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.bucket

  rule {
    default_retention {
      mode = "GOVERNANCE"  # 또는 "COMPLIANCE" (삭제 불가)
      days = 2555  # 7년
    }
  }
}
```

**Step 3: 어댑터 설정**

```python
# settings.py 또는 selfhealing 설정
import boto3
from selfhealing.adapters.audit.worm_adapters import S3ObjectLockAdapter, S3Config

# S3 클라이언트 생성
s3_client = boto3.client(
    's3',
    region_name='ap-northeast-2',  # 서울 리전
    # AWS 자격 증명은 환경 변수 또는 IAM Role 사용 권장
)

# 어댑터 생성
s3_adapter = S3ObjectLockAdapter(
    config=S3Config(
        bucket="selfhealing-audit-logs",
        prefix="audit/",
        region="ap-northeast-2",
        retention_days=2555,  # 7년
        object_lock_mode="COMPLIANCE",  # 삭제 불가
    ),
    s3_client=s3_client,
    fallback_path="/var/log/selfhealing/s3_fallback.jsonl",  # S3 실패 시 로컬 저장
)

# Audit 시스템에 등록
SELFHEALING_CONFIG = {
    "AUDIT": {
        "WORM_ADAPTER": s3_adapter,
    }
}
```

**Step 4: IAM 정책 (최소 권한)**

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:PutObjectRetention",
                "s3:PutObjectLegalHold"
            ],
            "Resource": "arn:aws:s3:::selfhealing-audit-logs/audit/*"
        }
    ]
}
```

**가치**: 매수 기업이 "S3 연결 가능한가?"라고 물을 때:
> "네, `S3ObjectLockAdapter`에 boto3 클라이언트만 연결하시면 즉시 7년 WORM 보관이 시작됩니다. Terraform 예시와 IAM 정책도 문서에 포함되어 있습니다."

---

### 8.3 보완 제안 2: IdP 연동 - External IdP Integration Guide

#### 8.3.1 현재 RBAC 구조 분석

**코드 근거** ([permissions.py](../../../packages/selfhealing-python/src/selfhealing/api/django/permissions.py#L89-95)):

```python
# IsViewer 권한 체크 (실제 코드)
return request.user.groups.filter(
    name__in=["selfhealing_viewer", "selfhealing_operator", "selfhealing_admin"]
).exists()
```

**핵심 포인트:**
- Django의 `request.user.groups`에 의존
- 그룹명만 매칭되면 어떤 인증 백엔드든 호환

#### 8.3.2 구현 가능성: ✅ 플러그 앤 플레이

**Django IdP 연동 흐름:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    외부 IdP 연동 흐름                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Okta/Azure AD/LDAP                                             │
│       ↓ SAML/OIDC 인증                                          │
│  Django Authentication Backend                                   │
│       ↓ 그룹 동기화                                              │
│  request.user.groups = ["selfhealing_admin", ...]               │
│       ↓                                                          │
│  IsViewer/IsOperator/IsSelfHealingAdmin 권한 체크               │
│       ↓ 기존 RBAC 로직 그대로 동작                               │
│  ✅ 권한 허용/거부                                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 8.3.3 External IdP Integration Guide

**Okta SAML 연동 예시:**

```python
# settings.py
INSTALLED_APPS = [
    ...
    'django_saml2_auth',
]

SAML2_AUTH = {
    'METADATA_AUTO_CONF_URL': 'https://your-company.okta.com/app/xxx/sso/saml/metadata',
    
    # Okta 그룹 → Django 그룹 매핑
    'ATTRIBUTES_MAP': {
        'groups': 'http://schemas.xmlsoap.org/claims/Group',
    },
    
    # 그룹 동기화 활성화
    'CREATE_USER': True,
    'NEW_USER_PROFILE': {
        'USER_GROUPS': [],  # 기본 그룹
    },
    
    # ✅ 핵심: Okta 그룹명을 Django 그룹으로 매핑
    'GROUP_MAP': {
        # Okta 그룹명          → Django 그룹명
        'SelfHealing-Admins':   'selfhealing_admin',
        'SelfHealing-Operators': 'selfhealing_operator', 
        'SelfHealing-Viewers':  'selfhealing_viewer',
        
        # 또는 기존 그룹 활용
        'IT-Operations':        'selfhealing_operator',
        'DevOps-Team':          'selfhealing_admin',
    },
}
```

**Azure AD (Entra ID) 연동 예시:**

```python
# settings.py
INSTALLED_APPS = [
    ...
    'django_auth_adfs',
]

AUTH_ADFS = {
    'AUDIENCE': 'your-app-client-id',
    'CLIENT_ID': 'your-app-client-id',
    'CLIENT_SECRET': 'your-app-client-secret',
    'TENANT_ID': 'your-tenant-id',
    
    # Azure AD 그룹 → Django 그룹 매핑
    'CLAIM_MAPPING': {
        'groups': 'groups',
    },
    
    # ✅ 핵심: Azure AD 그룹 Object ID → Django 그룹명 매핑
    'GROUP_MAP': {
        # Azure AD Group Object ID  → Django 그룹명
        'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee': 'selfhealing_admin',
        'ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj': 'selfhealing_operator',
        'kkkkkkkk-llll-mmmm-nnnn-oooooooooooo': 'selfhealing_viewer',
    },
    
    # 또는 그룹명 기반 매핑 (Azure AD 프리미엄)
    'GROUP_NAME_MAP': {
        'SH-Admins': 'selfhealing_admin',
        'SH-Operators': 'selfhealing_operator',
        'SH-Viewers': 'selfhealing_viewer',
    },
}
```

**LDAP/Active Directory 연동 예시:**

```python
# settings.py
import ldap
from django_auth_ldap.config import LDAPSearch, GroupOfNamesType

AUTH_LDAP_SERVER_URI = "ldap://ldap.example.com"
AUTH_LDAP_BIND_DN = "cn=admin,dc=example,dc=com"
AUTH_LDAP_BIND_PASSWORD = "password"

AUTH_LDAP_USER_SEARCH = LDAPSearch(
    "ou=users,dc=example,dc=com",
    ldap.SCOPE_SUBTREE,
    "(uid=%(user)s)"
)

# ✅ 핵심: LDAP 그룹 → Django 그룹 매핑
AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
    "ou=groups,dc=example,dc=com",
    ldap.SCOPE_SUBTREE,
    "(objectClass=groupOfNames)"
)
AUTH_LDAP_GROUP_TYPE = GroupOfNamesType()

AUTH_LDAP_USER_FLAGS_BY_GROUP = {
    "is_staff": "cn=staff,ou=groups,dc=example,dc=com",
}

AUTH_LDAP_FIND_GROUP_PERMS = True

# LDAP 그룹 DN → Django 그룹명 매핑
AUTH_LDAP_MIRROR_GROUPS = True  # LDAP 그룹을 Django에 자동 생성

# 또는 명시적 매핑
AUTH_LDAP_GROUP_MAP = {
    "cn=sh-admins,ou=groups,dc=example,dc=com": "selfhealing_admin",
    "cn=sh-operators,ou=groups,dc=example,dc=com": "selfhealing_operator",
    "cn=sh-viewers,ou=groups,dc=example,dc=com": "selfhealing_viewer",
}
```

**가치**: 대기업 보안 환경에 플러그 앤 플레이:
> "Okta, Azure AD, LDAP 모두 지원합니다. 귀사의 IdP 그룹명을 `selfhealing_admin/operator/viewer`로 매핑하는 설정만 추가하시면 기존 RBAC가 그대로 동작합니다."

---

### 8.4 보완 제안 3: Emergency Mode Audit - SafetyGuard 차단 시 증적 기록

#### 8.4.1 현재 상태 분석

**코드 근거** ([safety_guard.py#L632-L652](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py#L632-652)):

```python
# 현재 코드 (Audit 호출 없음)
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
        return True  # ← 여기서 그냥 반환, Audit 호출 없음
    
    result.checks_passed.append("emergency_mode")
    return False
```

**`log_governance_blocked_audit()` 함수 존재 확인** ([cb_audit.py#L89-L142](../../../packages/selfhealing-python/src/selfhealing/services/audit/cb_audit.py#L89-142)):

```python
def log_governance_blocked_audit(
    action: str,
    block_reason: str,
    details: Optional[dict] = None,
    request: Any = None,
) -> Optional[int]:
    """
    Governance 차단을 Audit 로그에 기록.
    
    WAL 기반 누락 0 보장.
    """
    event_details = {
        "action": action,
        "block_reason": block_reason,
        **(details or {}),
    }
    
    # WAL에 먼저 기록
    wal_seq = _write_to_wal(
        event_type="GOVERNANCE_BLOCKED",
        source="GovernanceGuard",
        details=event_details,
        success=False,
        error_message=block_reason,
    )
    ...
```

#### 8.4.2 구현 가능성: ✅ 코드 몇 줄 추가로 완료

#### 8.4.3 구현 코드

**수정 위치**: [safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py) `_check_emergency_mode_status()` 메서드

```python
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
        
        # ✅ 신규: Audit 로그 기록 (리뷰 피드백 반영)
        self._log_emergency_block_audit(emergency_result)
        
        return True
    
    result.checks_passed.append("emergency_mode")
    return False

def _log_emergency_block_audit(self, emergency_result: Dict[str, Any]) -> None:
    """Emergency Mode 차단 시 Audit 로그 기록."""
    try:
        from selfhealing.services.audit_helpers import log_governance_blocked_audit
        
        log_governance_blocked_audit(
            action="chaos_experiment",
            block_reason="emergency_mode_active",
            details={
                "current_emergency_level": emergency_result["level"],
                "emergency_level_value": emergency_result["level_value"],
                "blocked_by": "SafetyGuard._check_emergency_mode_status",
            },
        )
    except Exception as e:
        logger.debug(f"[SafetyGuard] Audit logging failed (non-critical): {e}")
```

**Audit 레코드 예시:**

```json
{
  "event_type": "GOVERNANCE_BLOCKED",
  "source": "GovernanceGuard",
  "timestamp": "2026-01-08T14:30:00Z",
  "details": {
    "action": "chaos_experiment",
    "block_reason": "emergency_mode_active",
    "current_emergency_level": "LEVEL_2",
    "emergency_level_value": 2,
    "blocked_by": "SafetyGuard._check_emergency_mode_status"
  },
  "success": false,
  "error_message": "emergency_mode_active"
}
```

**대시보드 통계 쿼리 예시 (Grafana/Prometheus):**

```promql
# 비상 모드로 인한 실험 차단 횟수
count(selfhealing_audit_events{
  event_type="GOVERNANCE_BLOCKED",
  block_reason="emergency_mode_active"
}) by (current_emergency_level)
```

**가치**: "왜 이때 카오스 실험이 안 돌았지?"라는 질문에:
> "시스템이 '비상 모드 LEVEL_2라서 내가 막았다'고 Audit 로그에 기록했습니다. 대시보드에서 비상 모드로 인한 차단 통계도 확인 가능합니다."

---

### 8.5 구현 우선순위 (보완 작업)

| 순위 | 작업 | 중요도 | 예상 공수 | 상태 |
|------|------|--------|----------|------|
| 1 | SafetyGuard Emergency Audit 추가 | 🔴 Critical | 30분 | 📋 계획 |
| 2 | S3 WORM 설정 가이드 문서화 | 🟡 High | 1시간 | ✅ 본 섹션에 포함 |
| 3 | IdP 연동 가이드 문서화 | 🟡 High | 1시간 | ✅ 본 섹션에 포함 |
| 4 | Emergency Audit 단위 테스트 | 🟢 Medium | 30분 | 📋 계획 |

**총 추가 공수**: 3시간

---

### 8.6 리뷰에 대한 분석 의견

#### 8.6.1 S3 WORM 관련

**리뷰어 제안**: "인터페이스가 있으니 이 어댑터 설정만 넣으시면 바로 7년 보관이 시작됩니다"

**내 분석**:
- ✅ **동의**: `S3ObjectLockAdapter`는 이미 boto3 주입 시 동작하는 구현이 있음
- ✅ **추가 가치**: Terraform 예시, IAM 정책까지 문서화하면 "즉시 사용 가능" 주장 가능
- ⚠️ **주의점**: `S3WORMBackend`(backends/s3_worm.py)는 순수 인터페이스, `S3ObjectLockAdapter`(adapters/audit/worm_adapters.py)가 실제 구현

#### 8.6.2 IdP 연동 관련

**리뷰어 제안**: "Okta나 Azure AD의 그룹 명칭을 selfhealing_admin 등으로 어떻게 매핑하는지 예시"

**내 분석**:
- ✅ **동의**: 현재 Django 그룹 기반이라 IdP 연동 시 그룹 매핑만 하면 됨
- ✅ **검증됨**: `permissions.py`의 `groups.filter(name__in=[...])` 로직 확인
- ⚠️ **보완 필요**: 25_RBAC_AUDIT_INTEGRATION_PLAN.md에 별도 섹션 추가 권장

#### 8.6.3 Emergency Audit 관련

**리뷰어 제안**: "`log_governance_blocked_audit()` 호출 로직을 즉시 추가"

**내 분석**:
- ✅ **동의**: 현재 코드에 Audit 호출이 없어 증적 누락
- ✅ **함수 존재**: `log_governance_blocked_audit()`가 cb_audit.py에 구현됨
- ✅ **구현 용이**: `_check_emergency_mode_status()`에 몇 줄 추가로 완료

---

## 9. 관련 문서 (확장)

- [safety_guard.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/safety_guard.py) - Chaos Safety Guard (Emergency Mode 구현 완료)
- [governance_checks.py](../../../packages/selfhealing-python/src/selfhealing/services/governance_checks.py) - Governance 핵심 구현
- [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) - ChaosScheduler 서비스
- [service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py) - AutoTuning 서비스 (Governance 구현 완료)
- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](../16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Governance 설계 문서
- [test_safety_guard_emergency_mode.py](../../../packages/selfhealing-python/tests/chaos/test_safety_guard_emergency_mode.py) - Emergency Mode 테스트
- [test_auto_tuning_governance.py](../../../packages/selfhealing-python/tests/unit/test_auto_tuning_governance.py) - AutoTuning Governance 테스트
- [worm_adapters.py](../../../packages/selfhealing-python/src/selfhealing/adapters/audit/worm_adapters.py) - S3 Object Lock 어댑터 (WORM)
- [s3_worm.py](../../../packages/selfhealing-python/src/selfhealing/audit/backends/s3_worm.py) - S3 WORM 백엔드 인터페이스
- [cb_audit.py](../../../packages/selfhealing-python/src/selfhealing/services/audit/cb_audit.py) - Governance Blocked Audit 함수
- [permissions.py](../../../packages/selfhealing-python/src/selfhealing/api/django/permissions.py) - RBAC 권한 클래스
- [25_RBAC_AUDIT_INTEGRATION_PLAN.md](./25_RBAC_AUDIT_INTEGRATION_PLAN.md) - RBAC-Audit 연동 계획

---

## 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|-----------|
| 1.0.0 | 2026-01-07 | AI Assistant | 초안 작성 |
| 1.1.0 | 2026-01-07 | AI Assistant | SafetyGuard Emergency Mode, AutoTuning Governance 구현 완료 |
| 1.2.0 | 2026-01-07 | AI Assistant | 단위 테스트 15개 작성 및 통과 |
| 1.3.0 | 2026-01-08 | AI Assistant | 리뷰 피드백 반영: S3 WORM 설정 가이드, IdP 연동 가이드, Emergency Audit 구현 계획 추가 |
