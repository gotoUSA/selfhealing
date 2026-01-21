# 77. Recovery Coordinator (복구 조율자)

> **Version**: 1.0.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 복구 프로세스는 **수동 + 무순서**입니다:

```
현재 복구 흐름:
├── 운영자가 NORMAL 모드 수동 전환
├── Canary 수동 재시작
├── Budget multiplier 수동 리셋
└── 각 시스템 독립적으로 복구

문제점:
❌ 복구 순서 보장 없음
❌ 안정화 검증 없이 복구
❌ 재장애 발생 시 다시 수동 대응
❌ 복구 이력 추적 어려움
```

### 1.2 해결책 (TO-BE)

**RecoveryCoordinator** 도입으로 **역순 + 검증 기반** 복구:

```
새로운 복구 흐름 (Phase 4):
├── [Step 1] Budget Multiplier 리셋 (5.0x → 1.0x)
├── [Step 2] Health Check 대기 (5분간 안정화 확인)
├── [Step 3] Canary Resume (일시정지된 롤아웃 재개)
├── [Step 4] Governance NORMAL (자동화 재활성화)
└── [Audit] 복구 CascadeEvent 기록

특징:
✅ 역순 복구 (장애 연쇄의 역순)
✅ 단계별 Health Check
✅ 재장애 시 자동 중단
✅ 감사 추적 가능
```

---

## 2. 아키텍처

### 2.1 복구 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Recovery Coordinator Flow                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  장애 발생 시 (Forward Cascade):                                     │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐            │
│  │ LEVEL_3 │──▶│ STRICT  │──▶│ CANARY  │──▶│ BUDGET  │            │
│  │ Detected│   │ Mode    │   │ Rollback│   │ 5.0x    │            │
│  └─────────┘   └─────────┘   └─────────┘   └─────────┘            │
│       ①            ②            ③            ④                    │
│                                                                      │
│  ═══════════════════════════════════════════════════════════════   │
│                                                                      │
│  복구 시 (Reverse Recovery):                                        │
│  ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐            │
│  │ BUDGET  │──▶│ HEALTH  │──▶│ CANARY  │──▶│ NORMAL  │            │
│  │ 1.0x    │   │ CHECK   │   │ Resume  │   │ Mode    │            │
│  └─────────┘   └─────────┘   └─────────┘   └─────────┘            │
│       ④            ⬤            ③            ②                    │
│       │            │            │            │                      │
│       │            │            │            │                      │
│       ▼            ▼            ▼            ▼                      │
│  [즉시 실행]   [5분 대기]    [60초 후]    [5분 후]                  │
│                검증 실패 시                                         │
│                복구 중단                                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 상태 머신

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Recovery State Machine                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│                    ┌────────────────┐                               │
│                    │   EMERGENCY    │                               │
│                    │   (LEVEL_3)    │                               │
│                    └───────┬────────┘                               │
│                            │                                         │
│                   안정화 조건 충족                                    │
│                   (error_rate < 10%                                  │
│                    for 10 minutes)                                   │
│                            │                                         │
│                            ▼                                         │
│                    ┌────────────────┐                               │
│                    │   RECOVERING   │                               │
│                    │   (Step 1-4)   │◀──────────────┐               │
│                    └───────┬────────┘               │               │
│                            │                        │               │
│              ┌─────────────┼─────────────┐          │               │
│              │             │             │          │               │
│              ▼             ▼             ▼          │               │
│        ┌──────────┐  ┌──────────┐  ┌──────────┐    │               │
│        │ SUCCESS  │  │ FAILED   │  │ ABORTED  │    │               │
│        │          │  │          │  │          │    │               │
│        │ NORMAL   │  │ Retry or │  │ Manual   │    │               │
│        │ restored │  │ Alert    │──┘ Override │    │               │
│        └──────────┘  └──────────┘  └──────────┘    │               │
│                            │                        │               │
│                            │    재장애 발생 시       │               │
│                            └────────────────────────┘               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 RecoveryState 모델

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_state.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RecoveryStatus(str, Enum):
    """복구 상태."""
    
    NOT_STARTED = "not_started"
    """복구 시작 전."""
    
    IN_PROGRESS = "in_progress"
    """복구 진행 중."""
    
    HEALTH_CHECK = "health_check"
    """안정화 검증 중."""
    
    COMPLETED = "completed"
    """복구 완료."""
    
    FAILED = "failed"
    """복구 실패."""
    
    ABORTED = "aborted"
    """복구 중단 (재장애 또는 수동)."""


class RecoveryStepType(str, Enum):
    """복구 단계 유형."""
    
    BUDGET_RESET = "budget_reset"
    """Budget Multiplier 리셋."""
    
    HEALTH_CHECK = "health_check"
    """안정화 검증."""
    
    CANARY_RESUME = "canary_resume"
    """Canary 롤아웃 재개."""
    
    GOVERNANCE_NORMAL = "governance_normal"
    """Governance NORMAL 모드 전환."""


@dataclass
class RecoveryStep:
    """복구 단계."""
    
    step_type: RecoveryStepType
    """단계 유형."""
    
    order: int
    """실행 순서."""
    
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    """단계 상태."""
    
    wait_after_seconds: int = 0
    """완료 후 대기 시간 (초)."""
    
    params: Dict[str, Any] = field(default_factory=dict)
    """추가 파라미터."""
    
    started_at: Optional[str] = None
    """시작 시각."""
    
    completed_at: Optional[str] = None
    """완료 시각."""
    
    error_message: Optional[str] = None
    """실패 시 에러 메시지."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "step_type": self.step_type.value,
            "order": self.order,
            "status": self.status.value,
            "wait_after_seconds": self.wait_after_seconds,
            "params": self.params,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
        }


@dataclass
class RecoverySession:
    """복구 세션."""
    
    id: str
    """세션 ID."""
    
    namespace: str
    """네임스페이스."""
    
    trigger_level: str
    """트리거된 Emergency 레벨 (복구 대상)."""
    
    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    """전체 복구 상태."""
    
    steps: List[RecoveryStep] = field(default_factory=list)
    """복구 단계 목록."""
    
    current_step_index: int = 0
    """현재 진행 중인 단계 인덱스."""
    
    started_at: Optional[str] = None
    """복구 시작 시각."""
    
    completed_at: Optional[str] = None
    """복구 완료 시각."""
    
    initiated_by: str = "system"
    """복구 시작 주체."""
    
    abort_reason: Optional[str] = None
    """중단 사유."""
    
    cascade_event_id: Optional[str] = None
    """연결된 Cascade Event ID."""
    
    def get_current_step(self) -> Optional[RecoveryStep]:
        """현재 진행 중인 단계 반환."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None
    
    def is_complete(self) -> bool:
        """모든 단계 완료 여부."""
        return all(
            s.status == RecoveryStatus.COMPLETED
            for s in self.steps
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "id": self.id,
            "namespace": self.namespace,
            "trigger_level": self.trigger_level,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "abort_reason": self.abort_reason,
            "cascade_event_id": self.cascade_event_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoverySession":
        """딕셔너리에서 생성."""
        steps = [
            RecoveryStep(
                step_type=RecoveryStepType(s["step_type"]),
                order=s["order"],
                status=RecoveryStatus(s["status"]),
                wait_after_seconds=s.get("wait_after_seconds", 0),
                params=s.get("params", {}),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
                error_message=s.get("error_message"),
            )
            for s in data.get("steps", [])
        ]
        
        return cls(
            id=data["id"],
            namespace=data["namespace"],
            trigger_level=data["trigger_level"],
            status=RecoveryStatus(data["status"]),
            steps=steps,
            current_step_index=data.get("current_step_index", 0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            initiated_by=data.get("initiated_by", "system"),
            abort_reason=data.get("abort_reason"),
            cascade_event_id=data.get("cascade_event_id"),
        )
```

### 3.2 RecoveryCoordinator

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_coordinator.py

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .recovery_state import (
    RecoverySession,
    RecoveryStatus,
    RecoveryStep,
    RecoveryStepType,
)

logger = logging.getLogger(__name__)


class RecoveryCoordinator:
    """
    복구 조율자.
    
    Emergency 상황에서 정상 상태로 복구할 때
    역순으로 안전하게 시스템을 정상화합니다.
    
    복구 순서 (LEVEL_3 기준):
    1. Budget Multiplier 리셋 (5.0x → 1.0x)
    2. Health Check (5분간 안정화 확인)
    3. Canary Resume (일시정지된 롤아웃 재개)
    4. Governance NORMAL (자동화 재활성화)
    
    Reference:
    - docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
    """
    
    # Redis 키 패턴
    SESSION_KEY = "selfhealing:{namespace}:recovery:session:{session_id}"
    ACTIVE_SESSION_KEY = "selfhealing:{namespace}:recovery:active"
    
    # 기본 복구 정책 (72번 문서 Section 7.1 기반)
    DEFAULT_RECOVERY_STEPS = {
        "LEVEL_3": [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=0,
                params={
                    "duration_minutes": 5,
                    "success_threshold": 0.95,
                    "error_rate_threshold": 0.1,
                },
            ),
            RecoveryStep(
                step_type=RecoveryStepType.CANARY_RESUME,
                order=3,
                wait_after_seconds=60,
                params={"resume_paused_only": True},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.GOVERNANCE_NORMAL,
                order=4,
                wait_after_seconds=300,  # 5분 안정화 후
                params={"reason": "[AUTO-RECOVERY] Stability confirmed"},
            ),
        ],
        "LEVEL_2": [
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=1,
                wait_after_seconds=0,
                params={"target_multiplier": 1.0},
            ),
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=2,
                wait_after_seconds=0,
                params={
                    "duration_minutes": 3,
                    "success_threshold": 0.95,
                    "error_rate_threshold": 0.15,
                },
            ),
            RecoveryStep(
                step_type=RecoveryStepType.CANARY_RESUME,
                order=3,
                wait_after_seconds=30,
                params={"resume_paused_only": True},
            ),
        ],
    }
    
    def __init__(self):
        self._lock = threading.RLock()
        self._step_handlers: Dict[RecoveryStepType, Callable] = {}
        self._register_default_handlers()
    
    def _get_backend(self):
        """State backend 획득."""
        from selfhealing.core.state_backend import get_state_backend
        return get_state_backend()
    
    def _register_default_handlers(self):
        """기본 단계 핸들러 등록."""
        self._step_handlers = {
            RecoveryStepType.BUDGET_RESET: self._handle_budget_reset,
            RecoveryStepType.HEALTH_CHECK: self._handle_health_check,
            RecoveryStepType.CANARY_RESUME: self._handle_canary_resume,
            RecoveryStepType.GOVERNANCE_NORMAL: self._handle_governance_normal,
        }
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    def start_recovery(
        self,
        namespace: str,
        trigger_level: str,
        initiated_by: str = "system",
    ) -> RecoverySession:
        """
        복구 시작.
        
        Args:
            namespace: 네임스페이스
            trigger_level: 복구 대상 Emergency 레벨
            initiated_by: 복구 시작 주체
        
        Returns:
            생성된 RecoverySession
        
        Raises:
            ValueError: 이미 진행 중인 복구가 있는 경우
        """
        with self._lock:
            # 1. 진행 중인 복구 확인
            active = self.get_active_session(namespace)
            if active and active.status == RecoveryStatus.IN_PROGRESS:
                raise ValueError(
                    f"Recovery already in progress: {active.id}"
                )
            
            # 2. 복구 단계 조회
            steps = self._get_recovery_steps(trigger_level)
            if not steps:
                raise ValueError(
                    f"No recovery steps defined for {trigger_level}"
                )
            
            # 3. 세션 생성
            session_id = f"recovery-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            
            session = RecoverySession(
                id=session_id,
                namespace=namespace,
                trigger_level=trigger_level,
                status=RecoveryStatus.IN_PROGRESS,
                steps=steps,
                current_step_index=0,
                started_at=now,
                initiated_by=initiated_by,
            )
            
            # 4. 저장
            self._save_session(session)
            self._set_active_session(namespace, session_id)
            
            logger.info(
                f"[Recovery] Started: id={session_id}, "
                f"namespace={namespace}, level={trigger_level}, "
                f"steps={len(steps)}"
            )
            
            return session
    
    def execute_next_step(
        self,
        namespace: str,
    ) -> Optional[RecoveryStep]:
        """
        다음 복구 단계 실행.
        
        Args:
            namespace: 네임스페이스
        
        Returns:
            실행된 RecoveryStep 또는 None (완료/실패 시)
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None
            
            if session.status != RecoveryStatus.IN_PROGRESS:
                return None
            
            step = session.get_current_step()
            if not step:
                # 모든 단계 완료
                self._complete_session(session)
                return None
            
            # 단계 실행
            now = datetime.now(timezone.utc).isoformat()
            step.started_at = now
            step.status = RecoveryStatus.IN_PROGRESS
            
            try:
                handler = self._step_handlers.get(step.step_type)
                if not handler:
                    raise ValueError(
                        f"No handler for step type: {step.step_type}"
                    )
                
                # 핸들러 실행
                result = handler(session, step)
                
                if result.get("success"):
                    step.status = RecoveryStatus.COMPLETED
                    step.completed_at = datetime.now(timezone.utc).isoformat()
                    session.current_step_index += 1
                    
                    logger.info(
                        f"[Recovery] Step completed: {step.step_type.value}, "
                        f"session={session.id}"
                    )
                else:
                    step.status = RecoveryStatus.FAILED
                    step.error_message = result.get("error", "Unknown error")
                    self._fail_session(session, step.error_message)
                    
                    logger.error(
                        f"[Recovery] Step failed: {step.step_type.value}, "
                        f"session={session.id}, error={step.error_message}"
                    )
            
            except Exception as e:
                step.status = RecoveryStatus.FAILED
                step.error_message = str(e)
                self._fail_session(session, str(e))
                
                logger.exception(
                    f"[Recovery] Step exception: {step.step_type.value}, "
                    f"session={session.id}"
                )
            
            self._save_session(session)
            return step
    
    def abort_recovery(
        self,
        namespace: str,
        reason: str,
    ) -> Optional[RecoverySession]:
        """
        복구 중단.
        
        재장애 발생 시 또는 수동으로 복구를 중단합니다.
        
        Args:
            namespace: 네임스페이스
            reason: 중단 사유
        
        Returns:
            중단된 RecoverySession 또는 None
        """
        with self._lock:
            session = self.get_active_session(namespace)
            if not session:
                return None
            
            if session.status not in (
                RecoveryStatus.IN_PROGRESS,
                RecoveryStatus.HEALTH_CHECK,
            ):
                return None
            
            session.status = RecoveryStatus.ABORTED
            session.abort_reason = reason
            session.completed_at = datetime.now(timezone.utc).isoformat()
            
            self._save_session(session)
            self._clear_active_session(namespace)
            
            logger.warning(
                f"[Recovery] Aborted: id={session.id}, reason={reason}"
            )
            
            return session
    
    def get_active_session(
        self,
        namespace: str,
    ) -> Optional[RecoverySession]:
        """
        활성 복구 세션 조회.
        
        Args:
            namespace: 네임스페이스
        
        Returns:
            활성 RecoverySession 또는 None
        """
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        session_id = backend.get(key)
        
        if not session_id:
            return None
        
        return self.get_session(namespace, session_id)
    
    def get_session(
        self,
        namespace: str,
        session_id: str,
    ) -> Optional[RecoverySession]:
        """
        복구 세션 조회.
        
        Args:
            namespace: 네임스페이스
            session_id: 세션 ID
        
        Returns:
            RecoverySession 또는 None
        """
        backend = self._get_backend()
        key = self.SESSION_KEY.format(
            namespace=namespace,
            session_id=session_id,
        )
        data = backend.get(key)
        
        if data:
            return RecoverySession.from_dict(data)
        return None
    
    def check_recovery_trigger(
        self,
        namespace: str,
    ) -> Dict[str, Any]:
        """
        복구 트리거 조건 확인.
        
        LEVEL_3에서 복구 가능 여부를 확인합니다.
        조건: error_rate < 10% for 10 minutes
        
        Args:
            namespace: 네임스페이스
        
        Returns:
            복구 가능 여부 및 상세 정보
        """
        # 현재 Emergency 레벨 확인
        from selfhealing.services.graceful_degradation import (
            get_graceful_degradation_manager,
        )
        
        manager = get_graceful_degradation_manager()
        current_level = manager.get_current_level()
        
        if current_level.name == "NORMAL":
            return {
                "can_recover": False,
                "reason": "Already in NORMAL state",
                "current_level": "NORMAL",
            }
        
        # 안정화 조건 확인 (10분간 error_rate < 10%)
        stability_check = self._check_stability(
            namespace=namespace,
            duration_minutes=10,
            error_rate_threshold=0.1,
        )
        
        return {
            "can_recover": stability_check["stable"],
            "current_level": current_level.name,
            "stability_check": stability_check,
        }
    
    # =========================================================================
    # Step Handlers
    # =========================================================================
    
    def _handle_budget_reset(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> Dict[str, Any]:
        """Budget Multiplier 리셋."""
        from selfhealing.services.budget.crisis_multiplier import (
            get_crisis_multiplier_provider,
        )
        
        target = step.params.get("target_multiplier", 1.0)
        
        try:
            provider = get_crisis_multiplier_provider()
            provider.reset_multiplier(session.namespace)
            
            return {"success": True, "multiplier": target}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _handle_health_check(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> Dict[str, Any]:
        """안정화 검증."""
        duration_minutes = step.params.get("duration_minutes", 5)
        success_threshold = step.params.get("success_threshold", 0.95)
        error_rate_threshold = step.params.get("error_rate_threshold", 0.1)
        
        # Health Check 상태로 전환
        session.status = RecoveryStatus.HEALTH_CHECK
        
        stability = self._check_stability(
            namespace=session.namespace,
            duration_minutes=duration_minutes,
            error_rate_threshold=error_rate_threshold,
        )
        
        if stability["stable"]:
            return {"success": True, "stability": stability}
        else:
            return {
                "success": False,
                "error": f"Stability check failed: {stability['reason']}",
                "stability": stability,
            }
    
    def _handle_canary_resume(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> Dict[str, Any]:
        """Canary 롤아웃 재개."""
        from selfhealing.services.canary import get_canary_service
        
        resume_paused_only = step.params.get("resume_paused_only", True)
        
        try:
            service = get_canary_service()
            
            if resume_paused_only:
                resumed = service.resume_paused_rollouts(session.namespace)
            else:
                resumed = service.resume_all_rollouts(session.namespace)
            
            return {"success": True, "resumed_count": len(resumed)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _handle_governance_normal(
        self,
        session: RecoverySession,
        step: RecoveryStep,
    ) -> Dict[str, Any]:
        """Governance NORMAL 모드 전환."""
        from selfhealing.governance import get_emergency_mode_tracker
        
        reason = step.params.get(
            "reason",
            "[AUTO-RECOVERY] Stability confirmed",
        )
        
        try:
            tracker = get_emergency_mode_tracker()
            tracker.deactivate(
                namespace=session.namespace,
                reason=reason,
            )
            
            return {"success": True, "mode": "NORMAL"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _get_recovery_steps(
        self,
        trigger_level: str,
    ) -> List[RecoveryStep]:
        """복구 단계 목록 조회."""
        steps = self.DEFAULT_RECOVERY_STEPS.get(trigger_level, [])
        # 깊은 복사하여 반환
        return [
            RecoveryStep(
                step_type=s.step_type,
                order=s.order,
                wait_after_seconds=s.wait_after_seconds,
                params=dict(s.params),
            )
            for s in steps
        ]
    
    def _check_stability(
        self,
        namespace: str,
        duration_minutes: int,
        error_rate_threshold: float,
    ) -> Dict[str, Any]:
        """안정화 조건 확인."""
        # TODO: 실제 메트릭 조회 구현
        # 현재는 placeholder
        from selfhealing.core.metrics import get_metrics_collector
        
        try:
            collector = get_metrics_collector()
            metrics = collector.get_error_rate(
                namespace=namespace,
                duration_minutes=duration_minutes,
            )
            
            current_error_rate = metrics.get("error_rate", 0.0)
            stable = current_error_rate < error_rate_threshold
            
            return {
                "stable": stable,
                "error_rate": current_error_rate,
                "threshold": error_rate_threshold,
                "duration_minutes": duration_minutes,
                "reason": None if stable else f"Error rate {current_error_rate:.2%} >= {error_rate_threshold:.2%}",
            }
        except Exception as e:
            return {
                "stable": False,
                "reason": f"Metrics unavailable: {str(e)}",
            }
    
    def _save_session(self, session: RecoverySession) -> None:
        """세션 저장."""
        backend = self._get_backend()
        key = self.SESSION_KEY.format(
            namespace=session.namespace,
            session_id=session.id,
        )
        backend.set(key, session.to_dict())
    
    def _set_active_session(
        self,
        namespace: str,
        session_id: str,
    ) -> None:
        """활성 세션 설정."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.set(key, session_id)
    
    def _clear_active_session(self, namespace: str) -> None:
        """활성 세션 클리어."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.delete(key)
    
    def _complete_session(self, session: RecoverySession) -> None:
        """세션 완료 처리."""
        session.status = RecoveryStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc).isoformat()
        
        self._save_session(session)
        self._clear_active_session(session.namespace)
        
        logger.info(
            f"[Recovery] Completed: id={session.id}, "
            f"namespace={session.namespace}"
        )
    
    def _fail_session(
        self,
        session: RecoverySession,
        error: str,
    ) -> None:
        """세션 실패 처리."""
        session.status = RecoveryStatus.FAILED
        session.abort_reason = error
        session.completed_at = datetime.now(timezone.utc).isoformat()
        
        self._save_session(session)
        self._clear_active_session(session.namespace)
        
        logger.error(
            f"[Recovery] Failed: id={session.id}, error={error}"
        )


# =============================================================================
# Singleton
# =============================================================================

_recovery_coordinator: Optional[RecoveryCoordinator] = None


def get_recovery_coordinator() -> RecoveryCoordinator:
    """RecoveryCoordinator 싱글톤 반환."""
    global _recovery_coordinator
    if _recovery_coordinator is None:
        _recovery_coordinator = RecoveryCoordinator()
    return _recovery_coordinator
```

---

## 4. Celery Task 통합

### 4.1 복구 태스크

```python
# packages/selfhealing-python/src/selfhealing/tasks/recovery_tasks.py

from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def check_recovery_trigger_task(self, namespace: str):
    """
    복구 트리거 조건 확인 태스크.
    
    주기적으로 실행되어 복구 가능 여부를 확인하고
    조건 충족 시 자동으로 복구를 시작합니다.
    """
    from selfhealing.services.coordination.recovery_coordinator import (
        get_recovery_coordinator,
    )
    
    coordinator = get_recovery_coordinator()
    
    # 이미 복구 진행 중인지 확인
    active = coordinator.get_active_session(namespace)
    if active:
        logger.debug(
            f"[RecoveryTask] Recovery already active: {active.id}"
        )
        return
    
    # 복구 트리거 조건 확인
    trigger_check = coordinator.check_recovery_trigger(namespace)
    
    if trigger_check["can_recover"]:
        logger.info(
            f"[RecoveryTask] Recovery trigger conditions met for {namespace}"
        )
        
        # 복구 시작
        session = coordinator.start_recovery(
            namespace=namespace,
            trigger_level=trigger_check["current_level"],
            initiated_by="auto_recovery_task",
        )
        
        # 첫 번째 단계 실행
        execute_recovery_step_task.delay(namespace, session.id)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def execute_recovery_step_task(
    self,
    namespace: str,
    session_id: str,
):
    """
    복구 단계 실행 태스크.
    
    각 단계를 순차적으로 실행하고 완료되면 다음 단계를 스케줄링합니다.
    """
    from selfhealing.services.coordination.recovery_coordinator import (
        get_recovery_coordinator,
    )
    
    coordinator = get_recovery_coordinator()
    
    session = coordinator.get_session(namespace, session_id)
    if not session:
        logger.warning(
            f"[RecoveryTask] Session not found: {session_id}"
        )
        return
    
    if session.status not in ("in_progress", "health_check"):
        logger.info(
            f"[RecoveryTask] Session not active: {session.status}"
        )
        return
    
    # 다음 단계 실행
    step = coordinator.execute_next_step(namespace)
    
    if step:
        if step.status == "completed":
            # 대기 시간 후 다음 단계 스케줄링
            wait_seconds = step.wait_after_seconds
            
            if wait_seconds > 0:
                execute_recovery_step_task.apply_async(
                    args=[namespace, session_id],
                    countdown=wait_seconds,
                )
            else:
                execute_recovery_step_task.delay(namespace, session_id)
        
        elif step.status == "failed":
            logger.error(
                f"[RecoveryTask] Step failed, recovery stopped: "
                f"{step.step_type.value}"
            )
```

### 4.2 Beat Schedule 추가

```python
# beat_schedule.py에 추가

RECOVERY_CHECK_SCHEDULE = {
    "check-recovery-trigger-global": {
        "task": "selfhealing.tasks.recovery_tasks.check_recovery_trigger_task",
        "schedule": crontab(minute="*/5"),  # 5분마다
        "args": ["global"],
    },
    "check-recovery-trigger-seoul": {
        "task": "selfhealing.tasks.recovery_tasks.check_recovery_trigger_task",
        "schedule": crontab(minute="*/5"),
        "args": ["seoul"],
    },
}
```

---

## 5. API 엔드포인트

### 5.1 Recovery API

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/recovery.py

from rest_framework.views import APIView
from rest_framework.request import Request
from rest_framework.response import Response

from selfhealing.services.coordination.recovery_coordinator import (
    get_recovery_coordinator,
)


class RecoveryStatusView(APIView):
    """복구 상태 조회 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """현재 복구 상태 조회."""
        namespace = request.query_params.get("namespace", "global")
        
        coordinator = get_recovery_coordinator()
        
        # 활성 세션 조회
        active = coordinator.get_active_session(namespace)
        
        # 복구 트리거 조건 확인
        trigger_check = coordinator.check_recovery_trigger(namespace)
        
        return Response({
            "active_session": active.to_dict() if active else None,
            "recovery_possible": trigger_check["can_recover"],
            "current_level": trigger_check.get("current_level"),
            "stability_check": trigger_check.get("stability_check"),
        })


class RecoveryStartView(APIView):
    """복구 시작 API."""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """수동 복구 시작."""
        namespace = request.data.get("namespace", "global")
        trigger_level = request.data.get("trigger_level")
        
        if not trigger_level:
            return Response(
                {"error": "trigger_level is required"},
                status=400,
            )
        
        coordinator = get_recovery_coordinator()
        
        try:
            session = coordinator.start_recovery(
                namespace=namespace,
                trigger_level=trigger_level,
                initiated_by=request.user.username,
            )
            
            return Response({
                "session": session.to_dict(),
                "message": "Recovery started",
            })
        
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=400,
            )


class RecoveryAbortView(APIView):
    """복구 중단 API."""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """복구 중단."""
        namespace = request.data.get("namespace", "global")
        reason = request.data.get("reason", "Manual abort")
        
        coordinator = get_recovery_coordinator()
        
        session = coordinator.abort_recovery(
            namespace=namespace,
            reason=f"{reason} (by {request.user.username})",
        )
        
        if not session:
            return Response(
                {"error": "No active recovery to abort"},
                status=404,
            )
        
        return Response({
            "session": session.to_dict(),
            "message": "Recovery aborted",
        })
```

---

## 6. 테스트

### 6.1 단위 테스트

```python
class TestRecoveryCoordinator:
    """RecoveryCoordinator 단위 테스트."""
    
    def test_start_recovery(self):
        """복구 시작."""
        coordinator = RecoveryCoordinator()
        
        session = coordinator.start_recovery(
            namespace="test",
            trigger_level="LEVEL_3",
            initiated_by="test_user",
        )
        
        assert session.id.startswith("recovery-")
        assert session.status == RecoveryStatus.IN_PROGRESS
        assert len(session.steps) == 4  # LEVEL_3 기준
        assert session.steps[0].step_type == RecoveryStepType.BUDGET_RESET
    
    def test_recovery_step_order(self):
        """복구 단계 순서."""
        coordinator = RecoveryCoordinator()
        
        session = coordinator.start_recovery(
            namespace="test",
            trigger_level="LEVEL_3",
        )
        
        # 순서 확인
        step_types = [s.step_type for s in session.steps]
        assert step_types == [
            RecoveryStepType.BUDGET_RESET,
            RecoveryStepType.HEALTH_CHECK,
            RecoveryStepType.CANARY_RESUME,
            RecoveryStepType.GOVERNANCE_NORMAL,
        ]
    
    def test_abort_recovery(self):
        """복구 중단."""
        coordinator = RecoveryCoordinator()
        
        session = coordinator.start_recovery(
            namespace="test",
            trigger_level="LEVEL_3",
        )
        
        aborted = coordinator.abort_recovery(
            namespace="test",
            reason="Test abort",
        )
        
        assert aborted.status == RecoveryStatus.ABORTED
        assert aborted.abort_reason == "Test abort"
    
    def test_no_duplicate_recovery(self):
        """중복 복구 방지."""
        coordinator = RecoveryCoordinator()
        
        coordinator.start_recovery(
            namespace="test",
            trigger_level="LEVEL_3",
        )
        
        with pytest.raises(ValueError, match="already in progress"):
            coordinator.start_recovery(
                namespace="test",
                trigger_level="LEVEL_3",
            )
```

---

## 7. 모니터링

### 7.1 메트릭

```python
RECOVERY_SESSIONS_TOTAL = Counter(
    "selfhealing_recovery_sessions_total",
    "Total recovery sessions",
    ["namespace", "trigger_level", "status"],
)

RECOVERY_STEPS_TOTAL = Counter(
    "selfhealing_recovery_steps_total",
    "Total recovery steps executed",
    ["step_type", "status", "namespace"],
)

RECOVERY_DURATION = Histogram(
    "selfhealing_recovery_duration_seconds",
    "Recovery session duration",
    ["namespace", "trigger_level"],
)
```

### 7.2 알림 템플릿

```
🔄 Recovery Session Started

Session ID: recovery-abc123
Namespace: seoul
Trigger Level: LEVEL_3
Initiated By: auto_recovery_task
Time: 2026-01-21T15:30:00Z

Recovery Steps:
1. ⏳ BUDGET_RESET - Reset multiplier to 1.0x
2. ⬜ HEALTH_CHECK - 5 min stability check
3. ⬜ CANARY_RESUME - Resume paused rollouts
4. ⬜ GOVERNANCE_NORMAL - Restore normal mode

Monitor: https://dashboard/recovery/recovery-abc123
```

```
✅ Recovery Session Completed

Session ID: recovery-abc123
Namespace: seoul
Duration: 12 minutes 34 seconds

Completed Steps:
1. ✅ BUDGET_RESET - Multiplier reset to 1.0x
2. ✅ HEALTH_CHECK - Stability confirmed (error_rate: 2.3%)
3. ✅ CANARY_RESUME - 2 rollouts resumed
4. ✅ GOVERNANCE_NORMAL - Normal mode restored

System is now fully recovered.
```

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
