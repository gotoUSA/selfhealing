# Part 1: Governance Integration 개선 구현 가이드

**문서 버전**: 1.0.0  
**작성일**: 2026-01-07  
**근거 코드**: 실제 소스 코드 분석 기반

---

## 1. 현황 분석

### 1.1 현재 Governance 체크 구현 위치

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

### 1.2 Governance 미연결 서비스 확인 결과

#### Gap 1: ChaosSchedulerService

[scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) 분석 결과:

```python
# scheduler.py L56-78
class ChaosSchedulerService:
    """
    Manages scheduled chaos experiments.
    
    Responsibilities:
    1. CRUD for scheduled experiments
    2. Pre-flight safety checks before execution
    3. Blast radius validation
    4. Approval workflow
    5. Kill switch integration  # ⚠️ 문서에는 있으나 실제 구현 없음
    6. Execution tracking and reporting
    """
```

**현재 `execute_now()` 메서드** (L502-550):
```python
def execute_now(self, schedule_id: str, force: bool = False) -> ExecutionResult:
    # 0. Error Budget Gate Check
    if not force:
        blocked = self._check_error_budget_gate(...)
    
    # 1-3. Check pre-execution conditions
    blocked = self._check_pre_execution_conditions(...)
    
    # 4. Safety checks (Pre-flight)
    if not force:
        blocked = self._check_safety_conditions(...)
    
    # 5. Blast radius check
    if not force:
        blocked = self._check_blast_radius_conditions(...)
    
    # ⚠️ check_all_governance() 호출 없음
    # ⚠️ Kill Switch 체크 없음
    # ⚠️ Emergency Mode 체크 없음
```

**grep 검색 결과**: `governance|is_system_enabled|kill_switch` → **No matches found**

#### Gap 2: AutoTuningService

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
- 그러나 `check_all_governance()` 호출 없음
- 조정 실행 전 Kill Switch / Emergency Mode 체크 없음

**grep 검색 결과**: `governance|is_system_enabled` → **No matches found**

### 1.3 Governance 연결이 불필요한 서비스

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

### 2.1 ChaosSchedulerService Governance 통합

#### 목표
Chaos 실험 실행 전 반드시 Governance 체크 수행

#### 수정 위치
[scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py#L502)

#### 구현 코드

```python
# scheduler.py 수정안
# 상단에 import 추가
from selfhealing.services.governance_checks import (
    check_all_governance,
    GovernanceCheckResult,
    BlockReason,
)

class ChaosSchedulerService:
    
    def _check_governance_conditions(
        self, 
        schedule: "ScheduledExperiment", 
        schedule_id: str, 
        experiment_id: str, 
        started_at
    ) -> ExecutionResult | None:
        """
        Governance 조건 체크.
        
        Kill Switch, Emergency Mode, Error Budget 순차 검증.
        Returns ExecutionResult if blocked, None otherwise.
        """
        result = check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,  # LEVEL_2 이상에서 차단
            check_error_budget=True,
            operation_name=f"chaos_experiment:{schedule.experiment_type}",
            service_name=schedule.target_service,
            domain=schedule.target_domain,
            audit_on_block=True,  # 차단 시 자동 Audit 기록
        )
        
        if not result.allowed:
            self._record_audit("experiment_blocked_governance", {
                "schedule_id": schedule_id,
                "experiment_id": experiment_id,
                "block_reason": result.block_reason.value if result.block_reason else "unknown",
                "block_message": result.block_message,
            })
            
            return self._make_skipped_result(
                schedule_id, 
                experiment_id, 
                started_at,
                f"Governance blocked: {result.block_message}",
                status="governance_blocked"
            )
        
        return None  # 허용됨
    
    def execute_now(self, schedule_id: str, force: bool = False) -> ExecutionResult:
        """Execute a scheduled experiment immediately."""
        # ... 기존 코드 ...
        
        try:
            # 0. Error Budget Gate Check (기존)
            if not force:
                blocked = self._check_error_budget_gate(...)
                if blocked:
                    return blocked
            
            # ✅ 신규: Governance 통합 체크
            if not force:
                blocked = self._check_governance_conditions(
                    schedule, schedule_id, experiment_id, started_at
                )
                if blocked:
                    return blocked
            
            # 1-3. Check pre-execution conditions (기존)
            # ...
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

### 2.3 execute_due_schedules Governance 통합

[scheduler.py L620-635](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py#L620) Celery Beat에서 호출되는 메서드:

```python
# scheduler.py 수정안
def execute_due_schedules(self) -> List[ExecutionResult]:
    """
    Execute all schedules that are due.
    Called by Celery Beat task.
    """
    # ✅ 신규: Beat 작업 시작 전 전역 Governance 체크
    gov_result = check_all_governance(
        operation_name="chaos_beat_execution",
        service_name="chaos_scheduler",
        audit_on_block=True,
    )
    
    if not gov_result.allowed:
        logger.warning(
            f"[ChaosScheduler] Beat execution blocked: {gov_result.block_message}"
        )
        return []  # 모든 실험 건너뜀
    
    results = []
    current = now()
    
    # ... 기존 로직 ...
```

---

## 3. 테스트 계획

### 3.1 단위 테스트

```python
# tests/self_healing/unit/test_chaos_governance_integration.py

import pytest
from unittest.mock import patch, MagicMock

class TestChaosSchedulerGovernanceIntegration:
    """ChaosSchedulerService Governance 통합 테스트."""
    
    def test_execute_now_blocked_by_kill_switch(self):
        """Kill Switch 활성화 시 실험 차단."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        schedule = scheduler.create_schedule(
            experiment_type="latency_injection",
            target_service="payment",
        )
        
        with patch(
            "selfhealing.services.governance_checks.is_system_enabled",
            return_value=False
        ):
            result = scheduler.execute_now(schedule.id)
            
            assert result.status == "governance_blocked"
            assert "Kill Switch" in result.skip_reason
    
    def test_execute_now_blocked_by_emergency_mode(self):
        """Emergency Mode LEVEL_2 이상에서 실험 차단."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        
        scheduler = get_chaos_scheduler()
        schedule = scheduler.create_schedule(
            experiment_type="cpu_stress",
            target_service="order",
        )
        
        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager"
        ) as mock_em:
            mock_em.return_value.get_current_level.return_value = EmergencyLevel.LEVEL_3
            
            result = scheduler.execute_now(schedule.id)
            
            assert result.status == "governance_blocked"
            assert "emergency" in result.skip_reason.lower()
    
    def test_execute_now_allowed_when_governance_passes(self):
        """모든 Governance 통과 시 실험 실행."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        schedule = scheduler.create_schedule(
            experiment_type="network_partition",
            target_service="inventory",
        )
        
        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov:
            mock_gov.return_value.allowed = True
            
            result = scheduler.execute_now(schedule.id, force=True)
            
            # force=True이므로 Governance 체크 건너뜀
            assert result.status != "governance_blocked"


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
| `services/chaos/scheduler.py` | 수정 | Governance 통합 체크 추가 |
| `services/auto_tuning/service.py` | 수정 | Governance 통합 체크 추가 |
| `tests/.../test_chaos_governance_integration.py` | 신규 | 통합 테스트 |
| `tests/.../test_auto_tuning_governance.py` | 신규 | 통합 테스트 |

### 4.2 하위 호환성

- **기존 API 유지**: 모든 public 메서드 시그니처 변경 없음
- **force 파라미터**: 기존처럼 `force=True`로 Governance 우회 가능
- **Fail-open 정책**: Governance 서비스 장애 시 기본적으로 허용

### 4.3 위험도

| 항목 | 위험도 | 완화 방안 |
|------|--------|----------|
| 서비스 장애 | 낮음 | Fail-open 정책 유지 |
| 성능 영향 | 최소 | TTL 캐시 (30초) 활용 |
| 호환성 | 없음 | API 변경 없음 |

---

## 5. 구현 우선순위

| 순위 | 작업 | 중요도 | 예상 공수 |
|------|------|--------|----------|
| 1 | ChaosScheduler Governance 통합 | 🔴 Critical | 2시간 |
| 2 | AutoTuningService Governance 통합 | 🟡 High | 2시간 |
| 3 | execute_due_schedules 체크 추가 | 🟡 High | 1시간 |
| 4 | 단위 테스트 작성 | 🟢 Medium | 3시간 |

**총 예상 공수**: 8시간

---

## 6. 관련 문서

- [governance_checks.py](../../../packages/selfhealing-python/src/selfhealing/services/governance_checks.py) - Governance 핵심 구현
- [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) - ChaosScheduler 서비스
- [service.py](../../../packages/selfhealing-python/src/selfhealing/services/auto_tuning/service.py) - AutoTuning 서비스
- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](../16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Governance 설계 문서
