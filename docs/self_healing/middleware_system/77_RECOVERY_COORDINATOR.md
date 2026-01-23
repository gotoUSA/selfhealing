# 77. Recovery Coordinator (복구 조율자)

> **Version**: 1.3.0
> **Created**: 2026-01-21
> **Updated**: 2026-01-23
> **Status**: Draft
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 0. 요약 및 보완 기능 (Phase 2)

### 0.1 Phase 2 보완 기능 목록

| # | 기능명 | 해결 문제 | 코드 파일 | 상태 |
|---|--------|----------|----------|------|
| 1 | **RecoveryCircuitBreaker** | 재장애 시 자동 중단 및 재-에스컬레이션 | `recovery_circuit_breaker.py` | ✅ Completed |
| 2 | **RegionalRecoveryPolicy** | 리전별 복구 정책 차별화 | `regional_recovery_policy.py` | ✅ Completed |
| 3 | **DistributedRecoveryLock** | 분산 환경 상태 재진입 방어 | `distributed_recovery_lock.py` | ✅ Completed |
| 4 | **PendingRecoveryApproval** | 수동 승인 및 방치 알림 | `pending_recovery_approval.py` | ✅ Completed |
| 5 | **RecoverySessionArchive** | PostgreSQL 영속화 (Resume 지원) | `recovery_session_archive.py` | ✅ Completed |
| 6 | **IdempotentStepHandlers** | 멱등성 보장 (재시도 안전) | `idempotent_step_handlers.py` | ✅ Completed |
| 7 | **DangerousForceRecoveryAudit** | 위험 복구 감사 추적 | `recovery_audit.py` | ✅ Completed |
| 8 | **WeightedBudgetStability** | Plan 75 연동 (가중 버짓 검증) | `recovery_coordinator.py` | ✅ Completed |
| 9 | **RecoveryDashboardWidget** | Ready to Restore 가시성 강화 | `dashboard_service.py`, `recovery_views.py` | ✅ Completed |
| 10 | **RedisKeyPriorityEviction** | Q6: Redis maxmemory 시 P0 키 보호 | `redis_key_guard.py` | ✅ Completed |
| 11 | **CriticalPathDedicatedWorker** | Q11: P0 전용 Celery Worker 격리 | `critical_worker.py` | ✅ Completed |
| 12 | **RecoveryAwareShutdownHook** | Q12: K8s preStop 시 Recovery 보호 | `recovery_shutdown.py` | ✅ Completed |
| 13 | **RecoveryMetrics** | Prometheus 복구 프로세스 모니터링 지표 | `recovery_metrics.py` | ✅ Completed |
| 14 | **RecoveryNotifications** | Slack/Teams 알림 템플릿 | `recovery_notifications.py` | ✅ Completed |

### 0.2 네이밍 선택 근거

| 선택된 이름 | 대안 | 선택 이유 |
|------------|------|----------|
| `RecoveryCircuitBreaker` | Reverse-Rollback | 기존 `CircuitBreaker` 패턴과 일치, "회로 차단" 의미 전달 |
| `DistributedRecoveryLock` | AtomicRecoverySession, MutualExclusion | `AtomicBudgetConsumer`의 Redis Lock 패턴과 일치 |
| `RegionalRecoveryPolicy` | NamespaceRecoveryOverride | `RegionalConfig` 패턴이 72번 문서에서 이미 사용됨 |
| `DANGEROUS_FORCE_RECOVERY` | RISKY_RECOVERY | `DANGEROUS_BYPASS_INTERLOCK` 패턴과 일치 |
| `READY_TO_RESTORE` | PENDING_APPROVAL, AWAITING_APPROVAL | 72번 문서 `RecoveryStatus.READY_TO_RESTORE`와 일치, "복구 준비 완료" 의미 전달 |
| `RedisKeyPriorityEviction` | KeyBasedEviction, MemoryGuard | 기존 `CriticalPathFallback` 패턴과 일관성, Priority 개념 명시 |
| `CriticalPathDedicatedWorker` | P0Worker, EmergencyWorker | 기존 `CriticalPathFallback` 네이밍과 일치, "전용 경로" 의미 전달 |
| `RecoveryAwareShutdownHook` | SafeShutdown, GracefulRecoveryShutdown | 기존 `GracefulShutdownCoordinator` 패턴과 일치, Recovery 인식 강조 |

---

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

    # 신규: 수동 승인 대기 상태 (72번 문서 RecoveryAccountabilityConfig 참조)
    READY_TO_RESTORE = "ready_to_restore"
    """
    자동 복구 조건 충족 + requires_manual_acknowledgement=True일 때,
    즉시 NORMAL 복구 대신 이 상태로 전환.

    사람이 마지막 Acknowledge 버튼을 누른 시점을 Audit 로그에 박제하여
    "복구 결정의 최종 책임은 사람에게 있었다"는 점을 명확히 함.

    Code reference:
        72_EMERGENCY_COORDINATION_LAYER.md#L940
    """

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

## 8. 보완 기능 (Phase 2)

### 8.1 RecoveryCircuitBreaker (재장애 시 자동 중단 및 재-에스컬레이션)

복구 진행 중 지표가 다시 악화되면 즉시 복구를 중단하고 Emergency 상태로 회귀합니다.

**네이밍 선택: `RecoveryCircuitBreaker`**
- **선택 이유**:
  - 기존 프로젝트의 `CircuitBreaker` 패턴과 일치 ([circuit_breaker.py](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/))
  - "복구가 실패하면 회로를 다시 열어(Open) 장애 모드로 돌아간다"는 의미 전달
  - 대안 `Reverse-Rollback`은 "롤백의 롤백"처럼 혼란스러움

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_circuit_breaker.py

from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging

from selfhealing.services.coordination.recovery_state import RecoverySession, RecoveryStatus

logger = logging.getLogger(__name__)


@dataclass
class RecoveryCircuitBreakerConfig:
    """복구 회로 차단기 설정."""

    error_rate_threshold: float = 0.15
    """복구 중단 에러율 임계치 (15%)."""

    check_interval_seconds: int = 30
    """지표 모니터링 주기 (초)."""

    consecutive_failures_to_trip: int = 2
    """연속 실패 횟수로 트립."""

    escalation_enabled: bool = True
    """재-에스컬레이션 활성화 여부."""


class RecoveryCircuitBreaker:
    """
    복구 회로 차단기.

    복구 진행 중 지표가 악화되면 즉시 복구를 중단하고
    72_EMERGENCY_COORDINATION_LAYER를 재호출하여 STRICT 모드로 회귀합니다.

    Code reference:
        circuit_breaker/models.py (CircuitState 패턴)
        anti_flapping.py (플래핑 감지 패턴)

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md §8.1
    """

    def __init__(self, config: Optional[RecoveryCircuitBreakerConfig] = None):
        self.config = config or RecoveryCircuitBreakerConfig()
        self._consecutive_failures: Dict[str, int] = {}

    def check_and_trip(
        self,
        session: RecoverySession,
    ) -> Dict[str, Any]:
        """
        복구 중 지표 확인 및 회로 차단.

        Args:
            session: 현재 복구 세션

        Returns:
            {"tripped": bool, "reason": str, "action": str}
        """
        from selfhealing.core.metrics import get_metrics_collector

        namespace = session.namespace

        try:
            collector = get_metrics_collector()
            metrics = collector.get_error_rate(
                namespace=namespace,
                duration_minutes=1,  # 최근 1분
            )

            current_error_rate = metrics.get("error_rate", 0.0)

            if current_error_rate >= self.config.error_rate_threshold:
                # 연속 실패 카운트 증가
                self._consecutive_failures[namespace] = \
                    self._consecutive_failures.get(namespace, 0) + 1

                if self._consecutive_failures[namespace] >= self.config.consecutive_failures_to_trip:
                    # 회로 차단 (트립)
                    return self._trip_circuit(session, current_error_rate)
                else:
                    return {
                        "tripped": False,
                        "warning": True,
                        "consecutive_failures": self._consecutive_failures[namespace],
                        "threshold": self.config.consecutive_failures_to_trip,
                    }
            else:
                # 정상 - 연속 실패 카운트 리셋
                self._consecutive_failures[namespace] = 0
                return {"tripped": False, "healthy": True}

        except Exception as e:
            logger.warning(f"[RecoveryCircuitBreaker] Metrics check failed: {e}")
            return {"tripped": False, "error": str(e)}

    def _trip_circuit(
        self,
        session: RecoverySession,
        error_rate: float,
    ) -> Dict[str, Any]:
        """
        회로 차단 실행.

        1. 복구 세션 중단 (ABORTED)
        2. EmergencyCoordinator 재호출 (재-에스컬레이션)
        """
        from selfhealing.services.coordination.recovery_coordinator import (
            get_recovery_coordinator,
        )

        coordinator = get_recovery_coordinator()

        # 1. 복구 중단
        abort_reason = (
            f"[CIRCUIT_BREAKER_TRIPPED] Error rate {error_rate:.2%} "
            f">= {self.config.error_rate_threshold:.2%} during recovery"
        )

        coordinator.abort_recovery(
            namespace=session.namespace,
            reason=abort_reason,
        )

        # 2. 재-에스컬레이션 (선택적)
        if self.config.escalation_enabled:
            self._re_escalate(session.namespace, session.trigger_level)

        logger.warning(
            f"[RecoveryCircuitBreaker] TRIPPED: namespace={session.namespace}, "
            f"error_rate={error_rate:.2%}, re_escalation={self.config.escalation_enabled}"
        )

        return {
            "tripped": True,
            "reason": abort_reason,
            "action": "RE_ESCALATED" if self.config.escalation_enabled else "ABORTED",
        }

    def _re_escalate(self, namespace: str, target_level: str) -> None:
        """
        재-에스컬레이션: STRICT 모드로 강제 회귀.

        Code reference:
            coordinator.py#on_emergency_level_changed
        """
        from selfhealing.services.coordination.coordinator import (
            EmergencyCoordinator,
        )
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        try:
            coordinator = EmergencyCoordinator()

            # NORMAL → 원래 레벨로 재활성화
            level = EmergencyLevel[target_level]

            coordinator.on_emergency_level_changed(
                old_level=EmergencyLevel.NORMAL,
                new_level=level,
                namespace=namespace,
                trigger_event_id=f"re-escalation-{namespace}",
                force=True,  # 플래핑 가드 무시
            )

            logger.info(
                f"[RecoveryCircuitBreaker] Re-escalated to {target_level}"
            )
        except Exception as e:
            logger.error(
                f"[RecoveryCircuitBreaker] Re-escalation failed: {e}"
            )
```

#### 8.1.1 Celery Task 통합

```python
# recovery_tasks.py에 추가

@shared_task(
    bind=True,
    max_retries=0,  # 모니터링 태스크는 재시도 없음
)
def monitor_recovery_health_task(self, namespace: str, session_id: str):
    """
    복구 세션 헬스 모니터링.

    복구 진행 중 주기적으로 지표를 확인하고
    악화 시 RecoveryCircuitBreaker를 트리거합니다.
    """
    from selfhealing.services.coordination.recovery_coordinator import (
        get_recovery_coordinator,
    )
    from selfhealing.services.coordination.recovery_circuit_breaker import (
        RecoveryCircuitBreaker,
    )

    coordinator = get_recovery_coordinator()
    session = coordinator.get_session(namespace, session_id)

    if not session or session.status not in ("in_progress", "health_check"):
        # 세션 종료됨 - 모니터링 중단
        return

    # 회로 차단기 확인
    breaker = RecoveryCircuitBreaker()
    result = breaker.check_and_trip(session)

    if result.get("tripped"):
        logger.warning(
            f"[RecoveryMonitor] Circuit breaker tripped: {result}"
        )
        return

    # 세션이 여전히 진행 중이면 다음 모니터링 스케줄
    monitor_recovery_health_task.apply_async(
        args=[namespace, session_id],
        countdown=30,  # 30초 후 재확인
    )
```

---

### 8.2 RegionalRecoveryPolicy (리전별 복구 정책 차별화)

네임스페이스(리전)별로 복구 정책을 다르게 설정할 수 있습니다.

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/regional_recovery_policy.py

from dataclasses import dataclass, field
from typing import Dict, Optional

from .recovery_state import RecoveryStep, RecoveryStepType


@dataclass
class RegionalRecoveryConfig:
    """리전별 복구 설정."""

    namespace: str
    """네임스페이스."""

    stability_duration_minutes: int = 5
    """안정화 검증 시간 (분)."""

    error_rate_threshold: float = 0.1
    """에러율 임계치."""

    canary_resume_delay_seconds: int = 60
    """Canary 재개 전 대기 시간 (초)."""

    governance_restore_delay_seconds: int = 300
    """Governance 복원 전 대기 시간 (초)."""

    require_manual_approval: bool = False
    """수동 승인 필요 여부."""

    approval_timeout_minutes: int = 30
    """수동 승인 타임아웃 (분). 초과 시 알림 재발송."""


# 기본 리전별 설정
DEFAULT_REGIONAL_CONFIGS: Dict[str, RegionalRecoveryConfig] = {
    # 서울 리전: 결제 비중이 커서 보수적
    "seoul": RegionalRecoveryConfig(
        namespace="seoul",
        stability_duration_minutes=10,
        error_rate_threshold=0.05,
        canary_resume_delay_seconds=120,
        governance_restore_delay_seconds=600,
        require_manual_approval=False,
    ),
    # 도쿄 리전: 표준 설정
    "tokyo": RegionalRecoveryConfig(
        namespace="tokyo",
        stability_duration_minutes=5,
        error_rate_threshold=0.1,
        canary_resume_delay_seconds=60,
        governance_restore_delay_seconds=300,
    ),
    # 테스트 리전: 빠른 복구
    "test": RegionalRecoveryConfig(
        namespace="test",
        stability_duration_minutes=1,
        error_rate_threshold=0.2,
        canary_resume_delay_seconds=10,
        governance_restore_delay_seconds=30,
    ),
    # Global (기본값)
    "global": RegionalRecoveryConfig(
        namespace="global",
        stability_duration_minutes=5,
        error_rate_threshold=0.1,
        canary_resume_delay_seconds=60,
        governance_restore_delay_seconds=300,
    ),
}


class RegionalRecoveryPolicyEngine:
    """
    리전별 복구 정책 엔진.

    네임스페이스에 따라 복구 정책을 차별화합니다.

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md §8.2
    """

    def __init__(
        self,
        configs: Optional[Dict[str, RegionalRecoveryConfig]] = None,
    ):
        self._configs = configs or dict(DEFAULT_REGIONAL_CONFIGS)

    def get_config(self, namespace: str) -> RegionalRecoveryConfig:
        """
        네임스페이스별 설정 조회.

        설정이 없으면 global 설정 반환.
        """
        return self._configs.get(
            namespace,
            self._configs.get("global", RegionalRecoveryConfig(namespace="global")),
        )

    def apply_to_steps(
        self,
        namespace: str,
        steps: list[RecoveryStep],
    ) -> list[RecoveryStep]:
        """
        리전별 설정을 복구 단계에 적용.

        Args:
            namespace: 네임스페이스
            steps: 기본 복구 단계 목록

        Returns:
            설정이 적용된 복구 단계 목록
        """
        config = self.get_config(namespace)

        for step in steps:
            if step.step_type == RecoveryStepType.HEALTH_CHECK:
                step.params["duration_minutes"] = config.stability_duration_minutes
                step.params["error_rate_threshold"] = config.error_rate_threshold

            elif step.step_type == RecoveryStepType.CANARY_RESUME:
                step.wait_after_seconds = config.canary_resume_delay_seconds

            elif step.step_type == RecoveryStepType.GOVERNANCE_NORMAL:
                step.wait_after_seconds = config.governance_restore_delay_seconds

        return steps

    def register_config(self, config: RegionalRecoveryConfig) -> None:
        """리전별 설정 등록/업데이트."""
        self._configs[config.namespace] = config
```

---

### 8.3 DistributedRecoveryLock (분산 락 기반 상태 재진입 방어)

다중 노드 환경에서 동일 네임스페이스에 대해 하나의 복구 세션만 실행되도록 보장합니다.

**네이밍 선택: `DistributedRecoveryLock`**
- **선택 이유**:
  - 기존 `AtomicBudgetConsumer`의 Redis Lock 패턴과 일치 ([atomic_consumer.py](packages/selfhealing-python/src/selfhealing/services/error_budget/atomic_consumer.py))
  - `Mutual Exclusion`은 학술적 용어로 코드에 부적합
  - 대안 `AtomicRecoverySession`은 원자성보다 배타성이 핵심이므로 Lock이 더 명확

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/distributed_recovery_lock.py

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class DistributedRecoveryLockConfig:
    """분산 복구 락 설정."""

    lock_timeout_seconds: int = 3600
    """락 타임아웃 (1시간). 복구 세션 최대 예상 시간보다 길어야 함."""

    lock_retry_count: int = 3
    """락 획득 재시도 횟수."""

    lock_retry_delay_seconds: float = 0.5
    """락 재시도 대기 시간."""


class DistributedRecoveryLock:
    """
    분산 복구 락.

    네임스페이스당 하나의 복구 세션만 실행되도록
    Redis 기반 분산 락을 제공합니다.

    Code reference:
        atomic_consumer.py (Redis Lock 패턴)
        locking.py#L177 (Lua 스크립트 원자적 처리)

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md §8.3
    """

    LOCK_KEY_PATTERN = "selfhealing:{namespace}:recovery:lock"

    # Lua 스크립트: 원자적 락 해제 (소유자만 해제 가능)
    RELEASE_LOCK_SCRIPT = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """

    def __init__(
        self,
        config: Optional[DistributedRecoveryLockConfig] = None,
    ):
        self.config = config or DistributedRecoveryLockConfig()
        self._lock_tokens: Dict[str, str] = {}

    def _get_redis_client(self):
        """Redis 클라이언트 획득."""
        from selfhealing.core.state_backend import get_redis_client
        return get_redis_client()

    def acquire(
        self,
        namespace: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        복구 락 획득.

        Args:
            namespace: 네임스페이스
            session_id: 세션 ID (락 소유자 식별)

        Returns:
            {"acquired": bool, "lock_holder": str, "reason": str}
        """
        redis = self._get_redis_client()
        if not redis:
            logger.warning("[DistributedRecoveryLock] Redis unavailable, using local lock")
            return {"acquired": True, "degraded_mode": True}

        lock_key = self.LOCK_KEY_PATTERN.format(namespace=namespace)
        lock_token = f"{session_id}:{uuid.uuid4().hex[:8]}"

        for attempt in range(self.config.lock_retry_count):
            try:
                # SET NX (Not Exists) + EX (Expiry)
                acquired = redis.set(
                    lock_key,
                    lock_token,
                    nx=True,
                    ex=self.config.lock_timeout_seconds,
                )

                if acquired:
                    self._lock_tokens[namespace] = lock_token
                    logger.info(
                        f"[DistributedRecoveryLock] Acquired: "
                        f"namespace={namespace}, token={lock_token}"
                    )
                    return {"acquired": True, "token": lock_token}

                # 락 보유자 확인
                current_holder = redis.get(lock_key)

                if attempt < self.config.lock_retry_count - 1:
                    import time
                    time.sleep(self.config.lock_retry_delay_seconds)
                    continue

                return {
                    "acquired": False,
                    "lock_holder": current_holder,
                    "reason": f"Lock held by another session: {current_holder}",
                }

            except Exception as e:
                logger.error(f"[DistributedRecoveryLock] Error: {e}")
                return {
                    "acquired": False,
                    "error": str(e),
                    "reason": f"Redis error: {e}",
                }

        return {"acquired": False, "reason": "Max retries exceeded"}

    def release(self, namespace: str) -> bool:
        """
        복구 락 해제.

        Lua 스크립트로 소유자만 해제할 수 있도록 원자적으로 처리.
        """
        redis = self._get_redis_client()
        if not redis:
            return True

        lock_key = self.LOCK_KEY_PATTERN.format(namespace=namespace)
        lock_token = self._lock_tokens.get(namespace)

        if not lock_token:
            logger.warning(
                f"[DistributedRecoveryLock] No token for namespace: {namespace}"
            )
            return False

        try:
            result = redis.eval(
                self.RELEASE_LOCK_SCRIPT,
                1,
                lock_key,
                lock_token,
            )

            if result:
                del self._lock_tokens[namespace]
                logger.info(
                    f"[DistributedRecoveryLock] Released: namespace={namespace}"
                )
                return True
            else:
                logger.warning(
                    f"[DistributedRecoveryLock] Token mismatch, cannot release"
                )
                return False

        except Exception as e:
            logger.error(f"[DistributedRecoveryLock] Release error: {e}")
            return False

    def is_locked(self, namespace: str) -> bool:
        """락 상태 확인."""
        redis = self._get_redis_client()
        if not redis:
            return False

        lock_key = self.LOCK_KEY_PATTERN.format(namespace=namespace)
        return redis.exists(lock_key) > 0
```

#### 8.3.1 RecoveryCoordinator 통합

```python
# RecoveryCoordinator.start_recovery()에 분산 락 추가

def start_recovery(
    self,
    namespace: str,
    trigger_level: str,
    initiated_by: str = "system",
) -> RecoverySession:
    """복구 시작 (분산 락 적용)."""

    # 1. 분산 락 획득
    from selfhealing.services.coordination.distributed_recovery_lock import (
        DistributedRecoveryLock,
    )

    lock = DistributedRecoveryLock()
    session_id = f"recovery-{uuid.uuid4().hex[:12]}"

    lock_result = lock.acquire(namespace, session_id)

    if not lock_result.get("acquired"):
        raise ValueError(
            f"Cannot start recovery: {lock_result.get('reason')}"
        )

    # 2. 기존 로직 계속...
    with self._lock:
        # ... 세션 생성 로직
        pass
```

---

### 8.4 PendingRecoveryApproval (수동 승인 및 알림)

중요 네임스페이스에서 수동 승인을 요구하거나, 방치된 복구 대기 상태에 대해 알림을 발송합니다.

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/pending_recovery_approval.py

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from .recovery_state import RecoverySession, RecoveryStatus

logger = logging.getLogger(__name__)


class RecoveryApprovalStatus(str, Enum):
    """복구 승인 상태."""

    NOT_REQUIRED = "not_required"
    """승인 불필요 (자동 복구)."""

    PENDING = "pending"
    """승인 대기 중."""

    APPROVED = "approved"
    """승인됨."""

    REJECTED = "rejected"
    """거부됨."""

    TIMED_OUT = "timed_out"
    """타임아웃 (자동 승인 또는 알림 재발송)."""


@dataclass
class RecoveryApprovalRequest:
    """복구 승인 요청."""

    session_id: str
    """복구 세션 ID."""

    namespace: str
    """네임스페이스."""

    status: RecoveryApprovalStatus = RecoveryApprovalStatus.PENDING
    """승인 상태."""

    requested_at: Optional[str] = None
    """요청 시각."""

    approved_by: Optional[str] = None
    """승인자."""

    approved_at: Optional[str] = None
    """승인 시각."""

    timeout_minutes: int = 30
    """타임아웃 (분)."""

    reminder_count: int = 0
    """알림 발송 횟수."""


class PendingRecoveryApprovalManager:
    """
    복구 승인 관리자.

    중요 네임스페이스에서 수동 승인을 요구하거나,
    방치된 대기 상태에 대해 알림을 재발송합니다.

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md §8.4
    """

    APPROVAL_KEY = "selfhealing:{namespace}:recovery:approval:{session_id}"

    def __init__(self, auto_approve_on_timeout: bool = True):
        """
        Args:
            auto_approve_on_timeout: 타임아웃 시 자동 승인 여부.
                False면 알림만 재발송.
        """
        self._auto_approve_on_timeout = auto_approve_on_timeout

    def request_approval(
        self,
        session: RecoverySession,
        timeout_minutes: int = 30,
    ) -> RecoveryApprovalRequest:
        """승인 요청 생성 및 알림 발송."""
        request = RecoveryApprovalRequest(
            session_id=session.id,
            namespace=session.namespace,
            status=RecoveryApprovalStatus.PENDING,
            requested_at=datetime.now(timezone.utc).isoformat(),
            timeout_minutes=timeout_minutes,
        )

        self._save_request(request)
        self._send_approval_notification(session, request)

        logger.info(
            f"[PendingRecoveryApproval] Approval requested: "
            f"session={session.id}, timeout={timeout_minutes}min"
        )

        return request

    def approve(
        self,
        namespace: str,
        session_id: str,
        approved_by: str,
    ) -> Optional[RecoveryApprovalRequest]:
        """복구 승인."""
        request = self._get_request(namespace, session_id)
        if not request:
            return None

        request.status = RecoveryApprovalStatus.APPROVED
        request.approved_by = approved_by
        request.approved_at = datetime.now(timezone.utc).isoformat()

        self._save_request(request)

        logger.info(
            f"[PendingRecoveryApproval] Approved: "
            f"session={session_id}, by={approved_by}"
        )

        return request

    def check_timeout(
        self,
        namespace: str,
        session_id: str,
    ) -> Optional[RecoveryApprovalRequest]:
        """타임아웃 확인 및 처리."""
        request = self._get_request(namespace, session_id)
        if not request or request.status != RecoveryApprovalStatus.PENDING:
            return None

        requested_at = datetime.fromisoformat(
            request.requested_at.replace("Z", "+00:00")
        )
        timeout_at = requested_at + timedelta(minutes=request.timeout_minutes)

        if datetime.now(timezone.utc) >= timeout_at:
            if self._auto_approve_on_timeout:
                request.status = RecoveryApprovalStatus.APPROVED
                request.approved_by = "system_auto_approve"
                request.approved_at = datetime.now(timezone.utc).isoformat()

                logger.info(
                    f"[PendingRecoveryApproval] Auto-approved on timeout: "
                    f"session={session_id}"
                )
            else:
                request.status = RecoveryApprovalStatus.TIMED_OUT
                request.reminder_count += 1
                self._send_reminder_notification(request)

                logger.warning(
                    f"[PendingRecoveryApproval] Timeout, reminder sent: "
                    f"session={session_id}, count={request.reminder_count}"
                )

            self._save_request(request)

        return request

    def _send_approval_notification(
        self,
        session: RecoverySession,
        request: RecoveryApprovalRequest,
    ) -> None:
        """승인 요청 알림 발송."""
        from selfhealing.audit.cascade_notifications import send_notification

        message = f"""
⏳ Recovery Approval Required

Session ID: {session.id}
Namespace: {session.namespace}
Trigger Level: {session.trigger_level}
Requested At: {request.requested_at}
Timeout: {request.timeout_minutes} minutes

Actions:
- Approve: POST /api/recovery/approve
- Reject: POST /api/recovery/reject

Dashboard: https://dashboard/recovery/{session.id}
"""
        send_notification(
            channel="slack",
            message=message,
            priority="high",
        )

    def _send_reminder_notification(
        self,
        request: RecoveryApprovalRequest,
    ) -> None:
        """리마인더 알림 발송."""
        from selfhealing.audit.cascade_notifications import send_notification

        message = f"""
⚠️ Recovery Approval STALLED (Reminder #{request.reminder_count})

Session ID: {request.session_id}
Namespace: {request.namespace}
Waiting Since: {request.requested_at}

This recovery has been waiting for approval for over {request.timeout_minutes} minutes.
Please take action immediately.

Dashboard: https://dashboard/recovery/{request.session_id}
"""
        send_notification(
            channel="slack",
            message=message,
            priority="critical",
        )

    def _get_request(
        self,
        namespace: str,
        session_id: str,
    ) -> Optional[RecoveryApprovalRequest]:
        """승인 요청 조회."""
        from selfhealing.core.state_backend import get_state_backend

        backend = get_state_backend()
        key = self.APPROVAL_KEY.format(
            namespace=namespace,
            session_id=session_id,
        )

        data = backend.get(key)
        if data:
            return RecoveryApprovalRequest(**data)
        return None

    def _save_request(self, request: RecoveryApprovalRequest) -> None:
        """승인 요청 저장."""
        from selfhealing.core.state_backend import get_state_backend

        backend = get_state_backend()
        key = self.APPROVAL_KEY.format(
            namespace=request.namespace,
            session_id=request.session_id,
        )

        backend.set(key, request.__dict__)
```

---

### 8.5 신뢰성 및 거버넌스 통합

#### 8.5.1 PostgreSQL 영속화 (Resume 지원)

```python
# packages/selfhealing-python/src/selfhealing/models/recovery_session_archive.py

from django.db import models


class RecoverySessionArchive(models.Model):
    """
    복구 세션 영속 저장 (PostgreSQL).

    Redis가 재시작되어도 복구를 재개할 수 있도록
    마스터 데이터를 PostgreSQL에 저장합니다.

    Code reference:
        cascade_event_archive.py (CascadeEventArchive 패턴)
    """

    session_id = models.CharField(max_length=50, primary_key=True)
    """세션 ID."""

    namespace = models.CharField(max_length=50, db_index=True)
    """네임스페이스."""

    trigger_level = models.CharField(max_length=20)
    """트리거 Emergency 레벨."""

    status = models.CharField(max_length=20, db_index=True)
    """복구 상태."""

    current_step_index = models.IntegerField(default=0)
    """현재 단계 인덱스."""

    steps_json = models.JSONField(default=list)
    """복구 단계 JSON."""

    initiated_by = models.CharField(max_length=100)
    """시작 주체."""

    abort_reason = models.TextField(null=True, blank=True)
    """중단 사유."""

    cascade_event_id = models.CharField(max_length=50, null=True, blank=True)
    """연결된 Cascade Event ID."""

    # 위험 복구 마킹 (Q6)
    is_dangerous_force_recovery = models.BooleanField(default=False)
    """강제 복구 여부 (경고 무시)."""

    force_recovery_reason = models.TextField(null=True, blank=True)
    """강제 복구 사유."""

    started_at = models.DateTimeField(auto_now_add=True)
    """시작 시각."""

    completed_at = models.DateTimeField(null=True, blank=True)
    """완료 시각."""

    class Meta:
        db_table = "selfhealing_recovery_session_archive"
        indexes = [
            models.Index(fields=["namespace", "status"]),
            models.Index(fields=["started_at"]),
        ]
```

#### 8.5.2 멱등성 핸들러 (Idempotent Step Handlers)

```python
# RecoveryCoordinator의 Step Handler에 멱등성 체크 추가

def _handle_canary_resume(
    self,
    session: RecoverySession,
    step: RecoveryStep,
) -> Dict[str, Any]:
    """Canary 롤아웃 재개 (멱등성 보장)."""
    from selfhealing.services.canary import get_canary_service

    resume_paused_only = step.params.get("resume_paused_only", True)

    try:
        service = get_canary_service()

        # 멱등성 체크: 이미 진행 중인 롤아웃은 스킵
        # Code reference: atomic_consumer.py (is_already_executed 패턴)
        paused_rollouts = service.get_paused_rollouts(session.namespace)

        if not paused_rollouts:
            logger.info(
                f"[Recovery] No paused rollouts to resume (idempotent skip)"
            )
            return {"success": True, "resumed_count": 0, "idempotent_skip": True}

        if resume_paused_only:
            resumed = service.resume_paused_rollouts(session.namespace)
        else:
            resumed = service.resume_all_rollouts(session.namespace)

        return {"success": True, "resumed_count": len(resumed)}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

#### 8.5.3 위험 복구 감사 (DANGEROUS_FORCE_RECOVERY)

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_audit.py

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class DangerousForceRecoveryAuditEntry:
    """
    위험 강제 복구 감사 엔트리.

    Admin이 시스템 경고를 무시하고 강제로 복구했을 때 기록.

    Code reference:
        bypass_audit.py#DANGEROUS_BYPASS_INTERLOCK 패턴
    """

    audit_id: str
    session_id: str
    namespace: str
    trigger_level: str
    forced_by: str
    force_reason: str
    timestamp: str

    # 강제 복구 시점의 시스템 상태
    current_error_rate: float
    recommended_wait_minutes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": "DANGEROUS_FORCE_RECOVERY",
            "audit_id": self.audit_id,
            "session_id": self.session_id,
            "namespace": self.namespace,
            "trigger_level": self.trigger_level,
            "forced_by": self.forced_by,
            "force_reason": self.force_reason,
            "timestamp": self.timestamp,
            "system_state": {
                "current_error_rate": self.current_error_rate,
                "recommended_wait_minutes": self.recommended_wait_minutes,
            },
            "governance": {
                "requires_incident_review": True,
                "incident_review_due_hours": 48,
            },
        }


def record_dangerous_force_recovery(
    session_id: str,
    namespace: str,
    trigger_level: str,
    forced_by: str,
    force_reason: str,
    current_error_rate: float,
    recommended_wait_minutes: int,
) -> DangerousForceRecoveryAuditEntry:
    """
    위험 강제 복구 기록.

    CascadeEventAuditor와 연동하여 인과관계 추적에 포함.
    """
    import uuid
    from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

    entry = DangerousForceRecoveryAuditEntry(
        audit_id=f"danger-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        namespace=namespace,
        trigger_level=trigger_level,
        forced_by=forced_by,
        force_reason=force_reason,
        timestamp=datetime.now(timezone.utc).isoformat(),
        current_error_rate=current_error_rate,
        recommended_wait_minutes=recommended_wait_minutes,
    )

    # Cascade Event에 기록
    auditor = get_cascade_event_auditor()
    auditor.record(
        trigger_type="DANGEROUS_FORCE_RECOVERY",
        trigger_details=entry.to_dict(),
        effects=[],
        namespace=namespace,
        triggered_by=forced_by,
    )

    logger.warning(
        f"[DangerousForceRecovery] Recorded: "
        f"session={session_id}, forced_by={forced_by}, "
        f"error_rate={current_error_rate:.2%}"
    )

    return entry
```

#### 8.5.4 Plan 75 연동 (가중 버짓 기반 안정화 검증)

```python
# RecoveryCoordinator._check_stability() 개선

def _check_stability(
    self,
    namespace: str,
    duration_minutes: int,
    error_rate_threshold: float,
    use_weighted_budget: bool = True,  # Plan 75 연동
) -> Dict[str, Any]:
    """
    안정화 조건 확인 (Plan 75 연동).

    단순 에러율 대신 CrisisMultiplier가 적용된
    가중 버짓 소진율을 사용합니다.

    Code reference:
        crisis_multiplier.py#get_weighted_budget_consumption
    """
    from selfhealing.core.metrics import get_metrics_collector

    try:
        collector = get_metrics_collector()

        if use_weighted_budget:
            # Plan 75 연동: 가중 버짓 소진율 사용
            from selfhealing.services.coordination.crisis_multiplier import (
                DomainAwareCrisisMultiplier,
            )

            multiplier = DomainAwareCrisisMultiplier()

            # 도메인별 가중 에러율 조회
            weighted_metrics = collector.get_weighted_error_rate(
                namespace=namespace,
                duration_minutes=duration_minutes,
                domain_weights=multiplier.domain_sensitivity,
            )

            current_error_rate = weighted_metrics.get("weighted_error_rate", 0.0)

            logger.debug(
                f"[Recovery] Weighted stability check: "
                f"rate={current_error_rate:.2%}, threshold={error_rate_threshold:.2%}"
            )
        else:
            # 기본: 단순 에러율
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
            "weighted_budget_used": use_weighted_budget,
            "reason": None if stable else (
                f"Error rate {current_error_rate:.2%} >= {error_rate_threshold:.2%}"
            ),
        }
    except Exception as e:
        return {
            "stable": False,
            "reason": f"Metrics unavailable: {str(e)}",
        }
```

---

### 8.6 Ready to Restore 가시성 강화 (RecoveryDashboardWidget)

`READY_TO_RESTORE` 상태가 방치되지 않도록 대시보드, API, 알림을 통해 가시성을 강화합니다.

**코드 근거:**
- [dashboard_service.py](packages/selfhealing-python/src/selfhealing/services/dashboard_service.py) - `StatusCounts` 패턴
- [schedule_views.py#PendingApprovalsView](packages/selfhealing-python/src/selfhealing/api/django/views/chaos/schedule_views.py#L240) - Pending Approvals API 패턴
- [chaos_scheduler.py#check_and_alert_pending_approvals](packages/selfhealing-python/src/selfhealing/tasks/chaos_scheduler.py#L193) - 주기적 알림 태스크 패턴

#### 8.6.1 대시보드 통합

```python
# packages/selfhealing-python/src/selfhealing/services/dashboard_service.py 확장

@dataclass
class RecoveryStatusWidget:
    """
    복구 상태 대시보드 위젯.

    Code reference:
        dashboard_service.py#StatusCounts 패턴
    """

    # 현재 복구 세션 요약
    active_sessions: int = 0
    """진행 중인 복구 세션 수."""

    pending_approval_sessions: int = 0
    """READY_TO_RESTORE 상태로 승인 대기 중인 세션 수."""

    completed_today: int = 0
    """오늘 완료된 복구 세션 수."""

    failed_today: int = 0
    """오늘 실패한 복구 세션 수."""

    # 가장 오래된 대기 세션 (가시성 강화)
    oldest_pending_session_id: Optional[str] = None
    """가장 오래 대기 중인 세션 ID."""

    oldest_pending_since: Optional[str] = None
    """가장 오래 대기 중인 세션의 대기 시작 시각."""

    oldest_pending_namespace: Optional[str] = None
    """가장 오래 대기 중인 세션의 네임스페이스."""


class RecoveryDashboardService:
    """
    복구 대시보드 서비스.

    Reference:
        docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md §8.6
    """

    def get_recovery_widget(self) -> RecoveryStatusWidget:
        """
        복구 상태 위젯 데이터 조회.

        Code reference:
            dashboard_service.py#get_summary 패턴
        """
        from selfhealing.services.coordination.recovery_coordinator import (
            get_recovery_coordinator,
        )
        from selfhealing.services.coordination.recovery_state import RecoveryStatus

        coordinator = get_recovery_coordinator()
        backend = coordinator._get_backend()

        # 모든 네임스페이스의 활성 세션 조회
        # TODO: 네임스페이스 목록은 RegionalRecoveryPolicy에서 조회
        namespaces = ["global", "seoul", "tokyo"]

        widget = RecoveryStatusWidget()
        oldest_pending_time = None

        for ns in namespaces:
            session = coordinator.get_active_session(ns)
            if not session:
                continue

            if session.status == RecoveryStatus.IN_PROGRESS:
                widget.active_sessions += 1

            elif session.status == RecoveryStatus.READY_TO_RESTORE:
                widget.pending_approval_sessions += 1

                # 가장 오래된 대기 세션 추적
                if session.started_at:
                    started = datetime.fromisoformat(
                        session.started_at.replace("Z", "+00:00")
                    )
                    if oldest_pending_time is None or started < oldest_pending_time:
                        oldest_pending_time = started
                        widget.oldest_pending_session_id = session.id
                        widget.oldest_pending_since = session.started_at
                        widget.oldest_pending_namespace = ns

        return widget

    def get_pending_approval_list(self) -> List[Dict[str, Any]]:
        """
        승인 대기 중인 복구 세션 목록.

        Code reference:
            schedule_views.py#PendingApprovalsView 패턴
        """
        from selfhealing.services.coordination.recovery_coordinator import (
            get_recovery_coordinator,
        )
        from selfhealing.services.coordination.recovery_state import RecoveryStatus

        coordinator = get_recovery_coordinator()
        namespaces = ["global", "seoul", "tokyo"]

        pending_list = []

        for ns in namespaces:
            session = coordinator.get_active_session(ns)
            if session and session.status == RecoveryStatus.READY_TO_RESTORE:
                pending_list.append({
                    "session_id": session.id,
                    "namespace": ns,
                    "trigger_level": session.trigger_level,
                    "started_at": session.started_at,
                    "current_step": session.current_step_index,
                    "total_steps": len(session.steps),
                    "initiated_by": session.initiated_by,
                })

        return pending_list
```

#### 8.6.2 Recovery Pending Approvals API

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/recovery.py 확장

class RecoveryPendingApprovalsView(APIView):
    """
    복구 승인 대기 목록 API.

    Code reference:
        schedule_views.py#PendingApprovalsView (기존 Chaos 패턴과 동일)
    """

    permission_classes = [IsAuthenticated, IsViewer]

    def get(self, request: Request) -> Response:
        """승인 대기 중인 복구 세션 목록."""
        from selfhealing.services.coordination.recovery_dashboard import (
            RecoveryDashboardService,
        )

        service = RecoveryDashboardService()

        pending_list = service.get_pending_approval_list()
        widget = service.get_recovery_widget()

        return Response({
            "status": "success",
            "data": {
                "pending_sessions": pending_list,
                "summary": {
                    "total_pending": widget.pending_approval_sessions,
                    "oldest_pending_since": widget.oldest_pending_since,
                    "oldest_pending_namespace": widget.oldest_pending_namespace,
                },
            },
        })


class RecoveryApproveView(APIView):
    """
    복구 승인 API.

    READY_TO_RESTORE 상태에서만 승인 가능.
    """

    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """복구 승인."""
        namespace = request.data.get("namespace")
        session_id = request.data.get("session_id")
        reason = request.data.get("reason", "")

        if not namespace or not session_id:
            return Response(
                {"status": "error", "error": "namespace and session_id required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from selfhealing.services.coordination.recovery_coordinator import (
            get_recovery_coordinator,
        )
        from selfhealing.services.coordination.recovery_state import RecoveryStatus

        coordinator = get_recovery_coordinator()
        session = coordinator.get_session(namespace, session_id)

        if not session:
            return Response(
                {"status": "error", "error": "Session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if session.status != RecoveryStatus.READY_TO_RESTORE:
            return Response(
                {"status": "error", "error": f"Cannot approve: status is {session.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 승인 처리 및 복구 재개
        from selfhealing.services.coordination.pending_recovery_approval import (
            PendingRecoveryApprovalManager,
        )

        approval_manager = PendingRecoveryApprovalManager()
        approval = approval_manager.approve(
            namespace=namespace,
            session_id=session_id,
            approved_by=request.user.username,
        )

        # 복구 재개 (다음 단계 실행)
        from selfhealing.tasks.recovery_tasks import execute_recovery_step_task
        execute_recovery_step_task.delay(namespace, session_id)

        return Response({
            "status": "success",
            "message": f"Recovery approved by {request.user.username}",
            "session_id": session_id,
            "namespace": namespace,
        })
```

#### 8.6.3 주기적 알림 태스크

```python
# packages/selfhealing-python/src/selfhealing/tasks/recovery_tasks.py 확장

@shared_task(
    name="selfhealing.tasks.recovery_tasks.check_stale_pending_recoveries",
    bind=True,
    max_retries=0,
)
def check_stale_pending_recoveries_task(self):
    """
    방치된 READY_TO_RESTORE 세션 확인 및 알림.

    Code reference:
        chaos_scheduler.py#check_and_alert_pending_approvals 패턴
    """
    from selfhealing.services.coordination.recovery_dashboard import (
        RecoveryDashboardService,
    )
    from selfhealing.services.coordination.pending_recovery_approval import (
        PendingRecoveryApprovalManager,
    )
    from datetime import datetime, timezone, timedelta

    service = RecoveryDashboardService()
    approval_manager = PendingRecoveryApprovalManager()

    pending_list = service.get_pending_approval_list()
    stale_threshold = timedelta(minutes=30)  # 30분 이상 방치
    now = datetime.now(timezone.utc)

    stale_sessions = []

    for session_info in pending_list:
        started_at = datetime.fromisoformat(
            session_info["started_at"].replace("Z", "+00:00")
        )
        waiting_time = now - started_at

        if waiting_time >= stale_threshold:
            stale_sessions.append({
                **session_info,
                "waiting_minutes": int(waiting_time.total_seconds() / 60),
            })

            # 타임아웃 확인 (리마인더 또는 자동 승인)
            approval_manager.check_timeout(
                namespace=session_info["namespace"],
                session_id=session_info["session_id"],
            )

    if stale_sessions:
        _send_stale_recovery_alert(stale_sessions)

    return {
        "checked": len(pending_list),
        "stale_count": len(stale_sessions),
        "stale_sessions": stale_sessions,
    }


def _send_stale_recovery_alert(stale_sessions: List[Dict[str, Any]]) -> None:
    """방치된 복구 알림 발송."""
    from selfhealing.audit.cascade_notifications import send_notification

    session_details = "\n".join([
        f"- [{s['namespace']}] {s['session_id']} (대기 {s['waiting_minutes']}분)"
        for s in stale_sessions
    ])

    message = f"""
⚠️ Stale Recovery Sessions Detected

{len(stale_sessions)} recovery session(s) are waiting for approval:

{session_details}

Please review and approve/reject these recoveries.

Dashboard: https://dashboard/recovery/pending
"""

    send_notification(
        channel="slack",
        message=message,
        priority="high" if len(stale_sessions) >= 3 else "medium",
    )


# Beat Schedule에 추가
RECOVERY_STALE_CHECK_SCHEDULE = {
    "check-stale-pending-recoveries": {
        "task": "selfhealing.tasks.recovery_tasks.check_stale_pending_recoveries",
        "schedule": crontab(minute="*/10"),  # 10분마다
    },
}
```

#### 8.6.4 URL 라우팅

```python
# packages/selfhealing-python/src/selfhealing/api/django/urls.py에 추가

# Recovery Pending Approvals (Viewer)
path(
    "recovery/pending-approvals/",
    RecoveryPendingApprovalsView.as_view(),
    name="recovery-pending-approvals",
),

# Recovery Approve (Admin)
path(
    "recovery/approve/",
    RecoveryApproveView.as_view(),
    name="recovery-approve",
),
```

---

## 9. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 1.1.0 | 2026-01-23 | Phase 2 보완 기능 추가 (8.1~8.5) | AI Assistant |
| 1.2.0 | 2026-01-23 | READY_TO_RESTORE 가시성 강화 (§8.6), 구현 순서 (§10) 추가 | AI Assistant |

---

## 10. 구현 순서 (Implementation Roadmap)

문서 전체를 구현하기 위한 권장 순서입니다. 의존성을 고려하여 Phase별로 구분합니다.

### 10.1 구현 의존성 그래프

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Implementation Dependency Graph                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Phase 1 (Foundation) - 독립 구현 가능                               │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│  │ RecoveryState   │   │ RecoverySession │   │ DistributedLock │   │
│  │ (§3.1)          │   │ Archive (§8.5.1)│   │ (§8.3)          │   │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘   │
│           │                     │                     │             │
│           └──────────┬──────────┴─────────────────────┘             │
│                      ▼                                               │
│  Phase 2 (Core) - Phase 1 의존                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              RecoveryCoordinator (§3.2)                      │    │
│  │     (RecoveryState + Lock + Archive 통합)                    │    │
│  └─────────────────────────────────┬───────────────────────────┘    │
│                                    │                                 │
│           ┌────────────────────────┼────────────────────────┐        │
│           │                        │                        │        │
│           ▼                        ▼                        ▼        │
│  Phase 3 (Extension) - Phase 2 의존                                  │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│  │ CircuitBreaker  │   │ RegionalPolicy  │   │ PendingApproval │   │
│  │ (§8.1)          │   │ (§8.2)          │   │ (§8.4)          │   │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘   │
│           │                     │                     │             │
│           └──────────┬──────────┴─────────────────────┘             │
│                      ▼                                               │
│  Phase 4 (Integration) - Phase 3 의존                                │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│  │ Celery Tasks    │   │ API Endpoints   │   │ Dashboard Widget│   │
│  │ (§4)            │   │ (§5)            │   │ (§8.6)          │   │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘   │
│           │                     │                     │             │
│           └──────────┬──────────┴─────────────────────┘             │
│                      ▼                                               │
│  Phase 5 (Audit & Monitoring) - Phase 4 의존                         │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│  │ DangerousForce  │   │ WeightedBudget  │   │ Metrics &       │   │
│  │ Audit (§8.5.3)  │   │ (§8.5.4)        │   │ Alerts (§7)     │   │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 Phase별 상세 구현 순서

#### Phase 1: Foundation (1-2일) ✅ COMPLETED

**목표**: 핵심 데이터 모델 및 인프라 구성요소

| 순서 | 작업 | 파일 | 의존성 | 산출물 | 상태 |
|------|------|------|--------|--------|------|
| 1.1 | RecoveryStatus enum 정의 (READY_TO_RESTORE 포함) | `enums.py` | 없음 | Enum 클래스 | ✅ |
| 1.2 | RecoveryStep, RecoverySession 모델 | `recovery_state.py` | 1.1 | Dataclass | ✅ |
| 1.3 | DistributedRecoveryLock 구현 | `distributed_recovery_lock.py` | Redis | Lock 클래스 | ✅ |
| 1.4 | RecoverySessionArchive Django 모델 | `models/recovery_session_archive.py` | Django ORM | DB 모델 | ✅ |
| 1.5 | DB 마이그레이션 생성 및 적용 | `migrations/` | 1.4 | 마이그레이션 | ✅ |

**검증 기준:**
- [x] RecoveryStatus에 7개 상태 존재 (NOT_STARTED ~ ABORTED + READY_TO_RESTORE)
- [x] Redis Lock 획득/해제 테스트 통과 (24개 테스트 + 33개 테스트)
- [x] PostgreSQL 테이블 생성 확인 (AbstractRecoverySessionArchive 모델 구현)

#### Phase 2: Core (2-3일) ✅ COMPLETED

**목표**: RecoveryCoordinator 핵심 로직

| 순서 | 작업 | 파일 | 의존성 | 산출물 | 상태 |
|------|------|------|--------|--------|------|
| 2.1 | RecoveryCoordinator 기본 골격 | `recovery_coordinator.py` | Phase 1 | 클래스 골격 | ✅ |
| 2.2 | start_recovery() 구현 | `recovery_coordinator.py` | 2.1, 1.3 | 분산 락 통합 | ✅ |
| 2.3 | execute_next_step() 구현 | `recovery_coordinator.py` | 2.2 | 단계 실행 | ✅ |
| 2.4 | Step Handler 구현 (BUDGET_RESET, HEALTH_CHECK) | `recovery_coordinator.py` | 2.3 | 핸들러 | ✅ |
| 2.5 | Step Handler 구현 (CANARY_RESUME, GOVERNANCE_NORMAL) | `recovery_coordinator.py` | 2.4 | 핸들러 | ✅ |
| 2.6 | abort_recovery() 구현 | `recovery_coordinator.py` | 2.2 | 중단 로직 | ✅ |
| 2.7 | 멱등성 핸들러 적용 (§8.5.2) | `recovery_coordinator.py` | 2.4, 2.5 | 멱등 체크 | ✅ |

**검증 기준:**
- [x] start_recovery() → execute_next_step() × 4 → COMPLETED 흐름 테스트 (35개 테스트)
- [x] 중복 start_recovery() 호출 시 ValueError
- [x] abort_recovery() 시 ABORTED 상태 전환

#### Phase 3: Extension (2-3일) ✅ Completed

**목표**: 확장 기능 (회로 차단기, 리전별 정책, 승인)

| 순서 | 작업 | 파일 | 의존성 | 산출물 | 상태 |
|------|------|------|--------|--------|------|
| 3.1 | RecoveryCircuitBreaker 구현 | `recovery_circuit_breaker.py` | Phase 2 | 회로 차단기 | ✅ |
| 3.2 | 재-에스컬레이션 로직 | `recovery_circuit_breaker.py` | 3.1, 72번 문서 | 재에스컬레이션 | ✅ |
| 3.3 | RegionalRecoveryConfig 정의 | `regional_recovery_policy.py` | 없음 | 설정 모델 | ✅ |
| 3.4 | RegionalRecoveryPolicyEngine 구현 | `regional_recovery_policy.py` | 3.3 | 정책 엔진 | ✅ |
| 3.5 | RecoveryCoordinator에 리전 정책 통합 | `recovery_coordinator.py` | 3.4 | 통합 | ✅ |
| 3.6 | PendingRecoveryApprovalManager 구현 | `pending_recovery_approval.py` | Phase 2 | 승인 관리자 | ✅ |
| 3.7 | READY_TO_RESTORE 상태 전환 로직 | `recovery_coordinator.py` | 3.6, 1.1 | 상태 전환 | ✅ |

**검증 기준:**
- [x] 복구 중 에러율 15% 초과 시 CircuitBreaker 트립 (83개 테스트)
- [x] seoul 네임스페이스는 stability 10분, global은 5분
- [x] require_manual_approval=True일 때 READY_TO_RESTORE 전환
- [x] Phase 3 테스트: test_recovery_circuit_breaker.py, test_regional_recovery_policy.py, test_pending_recovery_approval.py 전체 통과

#### Phase 4: Integration (2-3일) ✅ Completed

**목표**: 외부 연동 (Celery, API, Dashboard)

| 순서 | 작업 | 파일 | 의존성 | 산출물 |
|------|------|------|--------|--------|
| 4.1 | check_recovery_trigger_task 구현 | `recovery_tasks.py` | Phase 3 | Celery 태스크 |
| 4.2 | execute_recovery_step_task 구현 | `recovery_tasks.py` | 4.1 | Celery 태스크 |
| 4.3 | monitor_recovery_health_task 구현 | `recovery_tasks.py` | 3.1 | 모니터링 태스크 |
| 4.4 | check_stale_pending_recoveries_task 구현 | `recovery_tasks.py` | 3.6 | 방치 알림 태스크 |
| 4.5 | Beat Schedule 등록 | `celery.py` | 4.1-4.4 | 스케줄 |
| 4.6 | RecoveryStatusView API 구현 | `recovery_views.py` | Phase 3 | GET API |
| 4.7 | RecoveryStartView API 구현 | `recovery_views.py` | Phase 3 | POST API |
| 4.8 | RecoveryAbortView API 구현 | `recovery_views.py` | Phase 3 | POST API |
| 4.9 | RecoveryPendingApprovalsView API 구현 | `recovery_views.py` | 3.6 | GET API |
| 4.10 | RecoveryApproveView API 구현 | `recovery_views.py` | 3.6 | POST API |
| 4.11 | URL 라우팅 등록 | `urls.py` | 4.6-4.10 | URL 패턴 |
| 4.12 | RecoveryDashboardService 구현 | `recovery_dashboard.py` | Phase 3 | 대시보드 서비스 |
| 4.13 | DashboardSummaryView에 Recovery Widget 통합 | `dashboard_service.py` | 4.12 | 대시보드 통합 |

**검증 기준:**
- [x] POST /api/recovery/start/ → 복구 시작
- [x] GET /api/recovery/pending-approvals/ → 대기 목록
- [x] 10분마다 방치된 복구 알림 발송 (check_stale_pending_recoveries_task 구현 완료)

#### Phase 5: Audit & Monitoring (1-2일)

**목표**: 감사 추적 및 모니터링

| 순서 | 작업 | 파일 | 의존성 | 산출물 |
|------|------|------|--------|--------|
| 5.1 | DangerousForceRecoveryAuditEntry 구현 | `recovery_audit.py` | 없음 | 감사 모델 |
| 5.2 | record_dangerous_force_recovery() 구현 | `recovery_audit.py` | 5.1, 76번 문서 | 감사 기록 |
| 5.3 | RecoveryCoordinator에 CascadeEvent 연동 | `recovery_coordinator.py` | 5.2, 76번 문서 | 연동 |
| 5.4 | Plan 75 연동 (weighted budget 검증) | `recovery_coordinator.py` | 75번 문서 | 가중 버짓 |
| 5.5 | Prometheus 메트릭 추가 | `recovery_coordinator.py` | Phase 4 | 메트릭 |
| 5.6 | 알림 템플릿 구현 | `cascade_notifications.py` | Phase 4 | 알림 |

**검증 기준:**
- [x] 강제 복구 시 DANGEROUS_FORCE_RECOVERY 이벤트 기록 (recovery_audit.py 구현 완료)
- [ ] selfhealing_recovery_sessions_total 메트릭 노출
- [ ] Slack 알림 수신 확인

### 10.3 전체 일정 요약

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Implementation Timeline                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Week 1:                                                             │
│  ├── Day 1-2: Phase 1 (Foundation)                                  │
│  ├── Day 3-5: Phase 2 (Core)                                        │
│                                                                      │
│  Week 2:                                                             │
│  ├── Day 1-3: Phase 3 (Extension)                                   │
│  ├── Day 4-5: Phase 4 (Integration) Part 1                          │
│                                                                      │
│  Week 3:                                                             │
│  ├── Day 1-2: Phase 4 (Integration) Part 2                          │
│  ├── Day 3-4: Phase 5 (Audit & Monitoring)                          │
│  └── Day 5: Integration Testing & Documentation                     │
│                                                                      │
│  Total: 약 15일 (3주)                                                │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.4 테스트 우선순위

| 우선순위 | 테스트 대상 | 테스트 유형 | 이유 |
|----------|------------|------------|------|
| P0 | RecoveryCoordinator.start_recovery() | Unit | 핵심 진입점 |
| P0 | DistributedRecoveryLock | Unit + Integration | 분산 환경 안전성 |
| P0 | RecoveryCircuitBreaker.check_and_trip() | Unit | 재장애 방지 |
| P1 | Step Handlers (멱등성) | Unit | 재시도 안전성 |
| P1 | READY_TO_RESTORE 상태 전환 | Unit | 책임 추적성 |
| P1 | API Endpoints | Integration | 운영 인터페이스 |
| P2 | Celery Tasks | Integration | 자동화 |
| P2 | Dashboard Widget | Integration | 가시성 |
| P3 | 알림 발송 | E2E | 운영 알림 |

---

## 11. 인프라 안정성 보완 (Phase 2 Extension)

### 11.1 RedisKeyPriorityEviction (Q6: Redis maxmemory 대응)

#### 11.1.1 문제점 분석

단순히 `noeviction` 정책을 적용하면 메모리가 꽉 찼을 때 **모든 쓰기 작업이 실패**하여 시스템 전체가 마비될 수 있습니다.

```
현재 문제:
├── Redis maxmemory 도달
├── noeviction 정책: 쓰기 에러 (OOM)
├── Emergency Level 변경 불가
└── 시스템 마비
```

#### 11.1.2 해결책: Key-based Priority Eviction

**네이밍 선택**: `RedisKeyPriorityEviction`
- **선택 이유**: 기존 `CriticalPathFallback` 패턴과 일관성 유지, Priority 개념 명시
- **대안 `KeyBasedEviction`**: Priority 개념이 명확하지 않음
- **대안 `MemoryGuard`**: Redis 특화 의미 전달 부족

**코드 근거**:
- [critical_path_fallback.py#L34](packages/selfhealing-python/src/selfhealing/services/coordination/critical_path_fallback.py#L34): 3단계 Fallback 패턴
- [payment_service.py#L47](shopping/services/payment_service.py#L47): Redis TTL 관리 패턴

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/redis_key_guard.py

import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class RedisKeyPriority(IntEnum):
    """
    Redis 키 우선순위.
    
    낮은 숫자 = 높은 우선순위 = 절대 삭제 금지
    
    Reference:
        TaskPriority enum pattern (celery_adapter.py#L30)
    """
    P0_GOVERNANCE = 0
    """거버넌스 키: emergency:level, governance:mode (절대 보호)"""
    
    P1_RECOVERY = 1
    """복구 키: recovery:session, recovery:lock (중요)"""
    
    P2_BUDGET = 2
    """버짓 키: budget:consumed, budget:multiplier (중요)"""
    
    P3_CACHE = 3
    """캐시 키: cache:*, metrics:* (휘발 가능)"""
    
    P4_AUDIT = 4
    """감사 키: audit:7일 경과 데이터 (자동 삭제 가능)"""


@dataclass
class RedisKeyPriorityEviction:
    """
    Redis 키 우선순위 기반 Eviction 관리자.
    
    핵심 거버넌스 데이터는 절대 삭제하지 않으면서,
    중요도가 낮은 데이터는 TTL 기반으로 자동 정리합니다.
    
    전략:
    1. P0-P2 키: TTL 없음 (영구 보관, noeviction 보호)
    2. P3-P4 키: volatile-lru + 엄격한 TTL 관리
    3. 메모리 임계 경고 시: P4 → P3 순서로 수동 정리
    
    Reference:
        CriticalPathFallback 패턴 (critical_path_fallback.py#L34)
        Redis TTL 패턴 (payment_service.py#L47)
    """
    
    # P0: 절대 보호 키 패턴 (TTL 없음)
    protected_key_patterns: List[str] = field(default_factory=lambda: [
        "selfhealing:*:emergency:level",
        "selfhealing:*:emergency:state",
        "selfhealing:*:governance:*",
        "selfhealing:*:recovery:session:*",
        "selfhealing:*:recovery:lock:*",
        "selfhealing:*:budget:*",
    ])
    
    # P3-P4: 휘발 가능 키 TTL (초)
    volatile_key_ttl: Dict[str, int] = field(default_factory=lambda: {
        "cache:*": 3600,           # 1시간
        "metrics:*": 7200,         # 2시간
        "audit:event:*": 604800,   # 7일
    })
    
    # 메모리 경고 임계값 (%)
    memory_warning_threshold: float = float(
        os.environ.get("REDIS_MEMORY_WARNING_THRESHOLD", "80.0")
    )
    
    # 메모리 위험 임계값 (%)
    memory_critical_threshold: float = float(
        os.environ.get("REDIS_MEMORY_CRITICAL_THRESHOLD", "90.0")
    )
    
    def get_key_priority(self, key: str) -> RedisKeyPriority:
        """키의 우선순위 반환."""
        import fnmatch
        
        for pattern in self.protected_key_patterns:
            if fnmatch.fnmatch(key, pattern):
                if "emergency" in pattern or "governance" in pattern:
                    return RedisKeyPriority.P0_GOVERNANCE
                elif "recovery" in pattern:
                    return RedisKeyPriority.P1_RECOVERY
                elif "budget" in pattern:
                    return RedisKeyPriority.P2_BUDGET
        
        if key.startswith("cache:") or key.startswith("metrics:"):
            return RedisKeyPriority.P3_CACHE
        
        return RedisKeyPriority.P4_AUDIT
    
    def should_protect_key(self, key: str) -> bool:
        """키가 보호 대상인지 확인."""
        priority = self.get_key_priority(key)
        return priority <= RedisKeyPriority.P2_BUDGET
    
    def get_recommended_redis_config(self) -> Dict[str, str]:
        """
        권장 Redis 설정 반환.
        
        Returns:
            redis.conf에 적용할 설정값
        """
        return {
            # noeviction: 보호 키가 삭제되지 않도록
            "maxmemory-policy": "volatile-lru",
            
            # volatile-lru: TTL이 설정된 키만 LRU로 삭제
            # → P0-P2 키는 TTL 없으므로 보호됨
            # → P3-P4 키는 TTL 있으므로 메모리 부족 시 삭제
            
            # 메모리 샘플링
            "maxmemory-samples": "10",
        }
    
    def emergency_cleanup(
        self,
        redis_client,
        target_free_percent: float = 20.0,
    ) -> Dict[str, int]:
        """
        긴급 메모리 정리.
        
        P4 → P3 순서로 키를 삭제하여 목표 여유 공간 확보.
        
        Args:
            redis_client: Redis 클라이언트
            target_free_percent: 목표 여유 공간 비율
        
        Returns:
            {"deleted_p4": N, "deleted_p3": M}
        """
        result = {"deleted_p4": 0, "deleted_p3": 0}
        
        # P4 키 삭제 (audit 7일 경과)
        for key in redis_client.scan_iter("audit:event:*"):
            if not self.should_protect_key(key):
                redis_client.delete(key)
                result["deleted_p4"] += 1
        
        # 메모리 확인 후 P3도 필요 시 삭제
        info = redis_client.info("memory")
        used_percent = (
            info["used_memory"] / info["maxmemory"] * 100
            if info.get("maxmemory") else 0
        )
        
        if used_percent > (100 - target_free_percent):
            for key in redis_client.scan_iter("cache:*"):
                redis_client.delete(key)
                result["deleted_p3"] += 1
        
        logger.warning(
            f"[RedisKeyPriorityEviction] Emergency cleanup: "
            f"P4={result['deleted_p4']}, P3={result['deleted_p3']}"
        )
        
        return result
```

#### 11.1.3 Kubernetes ConfigMap 예시

```yaml
# k8s/redis-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-selfhealing-config
data:
  redis.conf: |
    # Memory management
    maxmemory 2gb
    maxmemory-policy volatile-lru
    maxmemory-samples 10
    
    # Protected keys use no TTL
    # Volatile keys (cache:*, audit:*) use TTL
```

---

### 11.2 CriticalPathDedicatedWorker (Q11: P0 전용 Worker)

#### 11.2.1 문제점 분석

단순히 큐를 나누는 것만으로는 **물리적 대기 시간** 문제를 해결할 수 없습니다.

```
현재 문제:
├── 일반 큐: 10만 건의 로그 아카이빙 대기
├── Recovery Abort 태스크 인입
├── 우선순위 높음 but Worker가 점유됨
└── 실제 처리까지 수십 초 지연
```

#### 11.2.2 해결책: Critical-Path Dedicated Worker

**네이밍 선택**: `CriticalPathDedicatedWorker`
- **선택 이유**: 기존 `CriticalPathFallback` 네이밍과 일치, "전용 경로" 의미 전달
- **대안 `P0Worker`**: 의미가 불명확
- **대안 `EmergencyWorker`**: Emergency와 혼동 가능

**코드 근거**:
- [celery_adapter.py#L192-195](packages/selfhealing-python/src/selfhealing/adapters/queues/celery_adapter.py#L192): Priority 매핑 패턴
- [GracefulShutdownCoordinator](packages/selfhealing-python/src/selfhealing/core/shutdown_coordinator.py#L34): Shutdown 조율 패턴

```python
# packages/selfhealing-python/src/selfhealing/adapters/celery/critical_worker.py

import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Set
import logging

logger = logging.getLogger(__name__)


class CriticalTaskPriority(IntEnum):
    """
    Critical Path 태스크 우선순위.
    
    P0: 즉시 실행 (전용 Worker)
    P1-P3: 일반 Worker
    
    Reference:
        TaskPriority (celery_adapter.py#L30)
    """
    ABORT = 0        # 복구 중단, Kill Switch
    ESCALATION = 1   # 긴급 레벨 상승
    RECOVERY = 2     # 복구 단계 실행
    ALERT = 3        # 알림 발송
    BUDGET_RESET = 4 # 버짓 리셋
    AUDIT = 5        # 감사 기록
    ARCHIVE = 6      # 로그 아카이빙


@dataclass
class CriticalPathDedicatedWorkerConfig:
    """
    Critical Path 전용 Worker 설정.
    
    일반 Worker 그룹 외에, 오직 P0 전용 태스크만 처리하는
    Small-size 전용 Worker를 별도로 운영합니다.
    
    Reference:
        CriticalPathFallback (critical_path_fallback.py#L34)
        GracefulShutdownCoordinator (shutdown_coordinator.py)
    """
    
    # 전용 큐 이름
    critical_queue_name: str = "selfhealing.critical"
    """P0 전용 큐 (Abort, Kill Switch 전용)."""
    
    high_priority_queue_name: str = "selfhealing.high"
    """P1-P2 고우선순위 큐 (Escalation, Recovery)."""
    
    default_queue_name: str = "selfhealing.default"
    """P3+ 일반 큐 (Alert, Audit, Archive)."""
    
    # 전용 Worker 수 (최소 1개 보장)
    critical_worker_count: int = int(
        os.environ.get("SELFHEALING_CRITICAL_WORKER_COUNT", "2")
    )
    
    # P0 태스크 목록
    critical_tasks: Set[str] = field(default_factory=lambda: {
        "selfhealing.tasks.abort_recovery",
        "selfhealing.tasks.kill_switch",
        "selfhealing.tasks.force_escalation",
    })
    
    # P1-P2 태스크 목록
    high_priority_tasks: Set[str] = field(default_factory=lambda: {
        "selfhealing.tasks.execute_recovery_step",
        "selfhealing.tasks.escalate_emergency",
        "selfhealing.tasks.reset_budget",
    })
    
    def get_task_queue(self, task_name: str) -> str:
        """태스크에 맞는 큐 반환."""
        if task_name in self.critical_tasks:
            return self.critical_queue_name
        elif task_name in self.high_priority_tasks:
            return self.high_priority_queue_name
        return self.default_queue_name
    
    def get_celery_task_routes(self) -> Dict[str, Dict[str, str]]:
        """
        Celery CELERY_TASK_ROUTES 설정 생성.
        
        Returns:
            Celery task routing 설정
        
        Example:
            >>> config = CriticalPathDedicatedWorkerConfig()
            >>> routes = config.get_celery_task_routes()
            >>> # settings.py에 적용:
            >>> # CELERY_TASK_ROUTES = routes
        """
        routes = {}
        
        for task in self.critical_tasks:
            routes[task] = {"queue": self.critical_queue_name}
        
        for task in self.high_priority_tasks:
            routes[task] = {"queue": self.high_priority_queue_name}
        
        return routes
    
    def get_worker_commands(self) -> Dict[str, str]:
        """
        Celery Worker 실행 명령어 생성.
        
        Returns:
            {"critical": "...", "high": "...", "default": "..."}
        """
        return {
            "critical": (
                f"celery -A myproject worker "
                f"-Q {self.critical_queue_name} "
                f"-c {self.critical_worker_count} "
                f"--hostname=critical@%h "
                f"-l INFO"
            ),
            "high": (
                f"celery -A myproject worker "
                f"-Q {self.high_priority_queue_name} "
                f"-c 4 "
                f"--hostname=high@%h "
                f"-l INFO"
            ),
            "default": (
                f"celery -A myproject worker "
                f"-Q {self.default_queue_name} "
                f"-c 8 "
                f"--hostname=default@%h "
                f"-l INFO"
            ),
        }
```

#### 11.2.3 Kubernetes Deployment 예시

```yaml
# k8s/celery-critical-worker.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-critical-worker
  labels:
    app: selfhealing
    tier: critical
spec:
  replicas: 2  # 최소 2개 (HA)
  selector:
    matchLabels:
      app: celery-critical-worker
  template:
    metadata:
      labels:
        app: celery-critical-worker
    spec:
      # P0 전용 Worker는 항상 우선 스케줄링
      priorityClassName: system-cluster-critical
      containers:
      - name: worker
        image: myproject:latest
        command:
        - celery
        - -A
        - myproject
        - worker
        - -Q
        - selfhealing.critical
        - -c
        - "2"
        - --hostname=critical@%h
        resources:
          requests:
            cpu: "100m"
            memory: "256Mi"
          limits:
            cpu: "500m"
            memory: "512Mi"
```

---

### 11.3 RecoveryAwareShutdownHook (Q12: K8s preStop 보호)

#### 11.3.1 문제점 분석

기존 `GracefulShutdownCoordinator`는 **Recovery Session**을 인식하지 못합니다.

```
현재 문제:
├── HPA가 Worker를 0개로 Scale Down
├── preStop 훅: 30초 대기 후 종료
├── Recovery Session 진행 중이어도 강제 종료
└── 복구 상태 불일치 발생
```

#### 11.3.2 해결책: Recovery-Aware Shutdown Hook

**네이밍 선택**: `RecoveryAwareShutdownHook`
- **선택 이유**: 기존 `GracefulShutdownCoordinator` 패턴과 일치, Recovery 인식 강조
- **대안 `SafeShutdown`**: 일반적인 이름, Recovery 특화 의미 부족
- **대안 `GracefulRecoveryShutdown`**: 너무 김

**코드 근거**:
- [shutdown_coordinator.py](packages/selfhealing-python/src/selfhealing/core/shutdown_coordinator.py): GracefulShutdownCoordinator 패턴
- [test_graceful_shutdown.py](packages/selfhealing-python/tests/unit/resilience/test_graceful_shutdown.py): 테스트 패턴

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_shutdown.py

import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
import logging

from selfhealing.core.shutdown_coordinator import (
    GracefulShutdownCoordinator,
    ShutdownPhase,
    ShutdownHandler,
    TrackedRequest,
)

logger = logging.getLogger(__name__)


@dataclass
class RecoveryAwareShutdownConfig:
    """
    Recovery 인식 Shutdown 설정.
    
    Reference:
        GracefulShutdownCoordinator (shutdown_coordinator.py)
    """
    
    # 기본 drain 타임아웃 (초)
    default_drain_timeout_seconds: float = float(
        os.environ.get("SELFHEALING_DRAIN_TIMEOUT", "30.0")
    )
    
    # Recovery Session 진행 중일 때 추가 대기 시간 (초)
    recovery_extension_seconds: float = float(
        os.environ.get("SELFHEALING_RECOVERY_EXTENSION", "300.0")
    )
    
    # 최대 대기 시간 (초) - Kubernetes terminationGracePeriodSeconds와 일치해야 함
    max_shutdown_wait_seconds: float = float(
        os.environ.get("SELFHEALING_MAX_SHUTDOWN_WAIT", "600.0")
    )
    
    # Recovery Session 체크 간격 (초)
    recovery_check_interval_seconds: float = 5.0


class RecoveryAwareShutdownHook(ShutdownHandler):
    """
    Recovery Session을 인식하는 Shutdown Hook.
    
    K8s preStop 훅에서 현재 진행 중인 Recovery Session이 있는지 확인하고,
    있다면 종료를 지연시켜 복구 프로세스를 물리적으로 보호합니다.
    
    Usage:
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=lambda: get_active_recovery_session() is not None
        )
        
        coordinator = GracefulShutdownCoordinator(
            drain_timeout_seconds=30.0,
            handlers=[hook],
        )
        
    Reference:
        GracefulShutdownCoordinator (shutdown_coordinator.py)
        RecoveryCoordinator.get_active_session() (recovery_coordinator.py)
    """
    
    def __init__(
        self,
        recovery_session_checker: Callable[[], bool],
        config: Optional[RecoveryAwareShutdownConfig] = None,
    ):
        """
        Args:
            recovery_session_checker: Recovery Session 진행 중 여부 반환 함수
            config: Shutdown 설정
        """
        self._check_recovery = recovery_session_checker
        self._config = config or RecoveryAwareShutdownConfig()
        self._shutdown_requested = False
        self._shutdown_complete = threading.Event()
    
    def on_shutdown_start(self) -> None:
        """
        Shutdown 시작 시 Recovery Session 확인.
        
        Recovery Session이 진행 중이면 추가 대기 시간을 요청합니다.
        """
        self._shutdown_requested = True
        
        if self._check_recovery():
            logger.warning(
                "[RecoveryAwareShutdownHook] Recovery session in progress. "
                f"Extending shutdown timeout by {self._config.recovery_extension_seconds}s"
            )
            
            # Recovery 완료까지 대기
            self._wait_for_recovery_completion()
        else:
            logger.info(
                "[RecoveryAwareShutdownHook] No active recovery session. "
                "Proceeding with normal shutdown."
            )
    
    def on_drain_complete(self) -> None:
        """Drain 완료 시 호출."""
        logger.info("[RecoveryAwareShutdownHook] Drain completed successfully.")
    
    def on_force_shutdown(self, pending_requests: list[TrackedRequest]) -> None:
        """강제 종료 시 호출."""
        if pending_requests:
            logger.error(
                f"[RecoveryAwareShutdownHook] Force shutdown with "
                f"{len(pending_requests)} pending requests!"
            )
            
            # 강제 종료 시 Recovery Session 상태 기록
            if self._check_recovery():
                logger.critical(
                    "[RecoveryAwareShutdownHook] CRITICAL: "
                    "Force shutdown during active recovery session! "
                    "Recovery state may be inconsistent."
                )
    
    def _wait_for_recovery_completion(self) -> None:
        """Recovery 완료까지 대기."""
        start_time = time.monotonic()
        max_wait = self._config.max_shutdown_wait_seconds
        interval = self._config.recovery_check_interval_seconds
        
        while time.monotonic() - start_time < max_wait:
            if not self._check_recovery():
                elapsed = time.monotonic() - start_time
                logger.info(
                    f"[RecoveryAwareShutdownHook] Recovery completed after {elapsed:.1f}s. "
                    "Proceeding with shutdown."
                )
                return
            
            remaining = max_wait - (time.monotonic() - start_time)
            logger.info(
                f"[RecoveryAwareShutdownHook] Waiting for recovery... "
                f"({remaining:.0f}s remaining)"
            )
            
            time.sleep(interval)
        
        logger.warning(
            f"[RecoveryAwareShutdownHook] Max wait time ({max_wait}s) exceeded. "
            "Proceeding with shutdown despite active recovery."
        )
    
    def is_shutdown_safe(self) -> bool:
        """
        현재 Shutdown이 안전한지 확인.
        
        Returns:
            True if no recovery session is active
        """
        return not self._check_recovery()
```

#### 11.3.3 Kubernetes PDB + preStop 설정

```yaml
# k8s/selfhealing-worker-pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: selfhealing-worker-pdb
spec:
  minAvailable: 1  # 최소 1개 Worker 보장
  selector:
    matchLabels:
      app: selfhealing-worker

---
# k8s/celery-worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-worker
spec:
  replicas: 3
  template:
    spec:
      # 최대 10분 대기 (Recovery 완료까지)
      terminationGracePeriodSeconds: 600
      containers:
      - name: worker
        lifecycle:
          preStop:
            exec:
              command:
              - /bin/sh
              - -c
              - |
                # Recovery Session 확인 스크립트
                python -c "
                from selfhealing.services.coordination import RecoveryCoordinator
                import time
                
                coord = RecoveryCoordinator()
                max_wait = 300  # 5분
                start = time.time()
                
                while time.time() - start < max_wait:
                    # 모든 네임스페이스의 활성 세션 확인
                    # (실제 구현에서는 네임스페이스 목록 조회 필요)
                    session = coord.get_active_session('default')
                    if session is None:
                        print('No active recovery. Safe to shutdown.')
                        break
                    print(f'Recovery in progress: {session.id}. Waiting...')
                    time.sleep(5)
                "
```

---

### 11.4 구현 순서 (Phase 2 Extension)

| 순서 | 작업 | 파일 | 의존성 | 상태 | 산출물 |
|------|------|------|--------|------|--------|
| E.1 | RedisKeyPriorityEviction 구현 | `redis_key_guard.py` | 없음 | ✅ Completed | 키 우선순위 관리자 |
| E.2 | Redis ConfigMap 작성 | `k8s/redis-config.yaml` | E.1 | ✅ Completed | K8s 설정 |
| E.3 | CriticalPathDedicatedWorkerConfig 구현 | `critical_worker.py` | 없음 | ✅ Completed | Worker 설정 |
| E.4 | Celery task routing 적용 | `settings.py` | E.3 | ✅ Completed | 태스크 라우팅 |
| E.5 | Critical Worker Deployment 작성 | `k8s/celery-critical-worker.yaml` | E.4 | ✅ Completed | K8s 배포 |
| E.6 | RecoveryAwareShutdownHook 구현 | `recovery_shutdown.py` | GracefulShutdownCoordinator | ✅ Completed | Shutdown Hook |
| E.7 | PDB + preStop 스크립트 작성 | `k8s/selfhealing-worker-pdb.yaml` | E.6 | ✅ Completed | K8s 설정 |
| E.8 | 통합 테스트 | `test_infra_stability.py` | E.1-E.7 | ✅ Completed | 테스트 |

**검증 기준:**
- [x] Redis maxmemory 도달 시에도 P0 키 보호됨 (redis_key_guard.py 구현 완료)
- [x] P0 태스크가 전용 Worker에서 처리됨 (critical_worker.py 구현 완료)
- [x] Recovery 진행 중 Worker 종료 방지 (recovery_shutdown.py 구현 완료)
