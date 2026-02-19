"""
Recovery State Models.

복구 프로세스의 상태 및 세션 관리를 위한 데이터 모델.

주요 모델:
- RecoveryStepType: 복구 단계 유형 (BUDGET_RESET, HEALTH_CHECK 등)
- RecoveryStep: 개별 복구 단계
- RecoverySession: 복구 세션 (여러 단계로 구성)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecoveryStepType(str, Enum):
    """
    복구 단계 유형.

    Emergency 상황에서 정상으로 복구할 때 실행되는 단계 유형.
    역순 복구 원칙에 따라 정의됨.
    """

    BUDGET_RESET = "budget_reset"
    """
    Budget Multiplier 리셋.

    Crisis Multiplier를 기본값(1.0)으로 초기화.
    복구 시 가장 먼저 실행되어야 함.
    """

    HEALTH_CHECK = "health_check"
    """
    안정화 검증.

    지정된 시간 동안 에러율이 임계값 이하인지 확인.
    다음 단계 진행 전 시스템 안정성 보장.
    """

    CANARY_RESUME = "canary_resume"
    """
    Canary 롤아웃 재개.

    Emergency로 인해 일시 중지된 Canary 롤아웃 재개.
    """

    GOVERNANCE_NORMAL = "governance_normal"
    """
    Governance NORMAL 모드 전환.

    STRICT 모드에서 NORMAL 모드로 전환하여 자동화 재활성화.
    복구의 마지막 단계.
    """


# enums.py의 RecoveryStatus, CompensationStatus를 재사용
from .enums import CompensationStatus, RecoveryStatus


@dataclass
class RecoveryStep:
    """
    복구 단계.

    개별 복구 작업을 나타내며, 상태 추적 및 파라미터 관리.

    Attributes:
        step_type: 단계 유형 (RecoveryStepType)
        order: 실행 순서 (1부터 시작)
        status: 현재 상태 (RecoveryStatus)
        wait_after_seconds: 완료 후 대기 시간
        params: 단계별 추가 파라미터
        started_at: 시작 시각 (ISO 8601)
        completed_at: 완료 시각 (ISO 8601)
        error_message: 실패 시 에러 메시지
    """

    step_type: RecoveryStepType
    """단계 유형."""

    order: int
    """실행 순서."""

    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    """단계 상태."""

    wait_after_seconds: int = 0
    """완료 후 대기 시간 (초). 다음 단계 실행 전 안정화 대기."""

    params: dict[str, Any] = field(default_factory=dict)
    """
    추가 파라미터.

    예시:
    - BUDGET_RESET: {"target_multiplier": 1.0}
    - HEALTH_CHECK: {"duration_minutes": 5, "error_rate_threshold": 0.1}
    - CANARY_RESUME: {"resume_paused_only": True}
    - GOVERNANCE_NORMAL: {"reason": "[AUTO-RECOVERY] Stability confirmed"}
    """

    started_at: str | None = None
    """시작 시각 (ISO 8601 문자열)."""

    completed_at: str | None = None
    """완료 시각 (ISO 8601 문자열)."""

    error_message: str | None = None
    """실패 시 에러 메시지."""

    result_data: dict[str, Any] = field(default_factory=dict)
    """
    Forward 핸들러 실행 결과 데이터.

    핸들러가 반환하는 dict를 저장하여 compensate 시 참조 가능하게 함.
    Saga 패턴의 기본 원칙: "무엇을 했는지 알아야 되돌릴 수 있다."

    예시:
    - BUDGET_RESET: {"success": True, "multiplier": 1.0}
    - CANARY_RESUME: {"success": True, "resumed_count": 3, "staggered": True}

    네이밍 근거:
    - 248번 Saga Core Models의 StepResult.data 필드와 의미적 통일.
    - execution_context는 248번 SagaContext(Step 간 공유 데이터)와 혼동 우려.
    """

    compensation_status: CompensationStatus = CompensationStatus.NOT_REQUIRED
    """
    보상 상태.

    서버 재시작 시 어디까지 보상했는지 추적.
    _attempt_compensation 루프에서 보상 성공 시 즉시 COMPENSATED로 업데이트 + Redis 저장.

    네이밍 근거:
    - CompensationStatus Enum으로 별도 정의 (RecoveryStatus와 분리).
    - 248번 SagaStepStatus의 COMPENSATED/COMPENSATE_FAILED와 값 통일.
    """

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "step_type": self.step_type.value,
            "order": self.order,
            "status": self.status.value,
            "wait_after_seconds": self.wait_after_seconds,
            "params": self.params,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "result_data": self.result_data,
            "compensation_status": self.compensation_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryStep:
        """딕셔너리에서 생성."""
        return cls(
            step_type=RecoveryStepType(data["step_type"]),
            order=data["order"],
            status=RecoveryStatus(data.get("status", "not_started")),
            wait_after_seconds=data.get("wait_after_seconds", 0),
            params=data.get("params", {}),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message"),
            result_data=data.get("result_data", {}),
            compensation_status=CompensationStatus(data.get("compensation_status", "not_required")),
        )


@dataclass
class CompensationResult:
    """
    보상 실행 결과.

    _attempt_compensation()의 반환값으로, 보상 성공/실패/건너뜀 Step 목록을 구조화.
    Phase 3 DLQ 연동 시 failed_steps를 DLQ로 전송.
    """

    compensated_steps: list[RecoveryStep] = field(default_factory=list)
    """보상 성공한 Step 목록."""

    failed_steps: list[tuple[RecoveryStep, str]] = field(default_factory=list)
    """보상 실패한 Step 목록. (step, error_message) 튜플."""

    skipped_steps: list[RecoveryStep] = field(default_factory=list)
    """compensate 핸들러 미등록으로 건너뛴 Step 목록."""

    @property
    def all_compensated(self) -> bool:
        """모든 보상 대상이 성공했는지 여부."""
        return len(self.failed_steps) == 0


@dataclass
class RecoverySession:
    """
    복구 세션.

    Emergency 상황에서 정상으로 복구하는 전체 프로세스를 나타냄.
    여러 RecoveryStep으로 구성되며, 순차적으로 실행됨.

    Attributes:
        id: 고유 세션 ID (예: "recovery-abc123def456")
        namespace: 네임스페이스 (예: "global", "seoul")
        trigger_level: 복구 대상 Emergency 레벨 (예: "LEVEL_3", "LEVEL_2")
        status: 전체 복구 상태
        steps: 복구 단계 목록
        current_step_index: 현재 진행 중인 단계 인덱스
        started_at: 복구 시작 시각
        completed_at: 복구 완료 시각
        initiated_by: 복구 시작 주체 ("system" 또는 사용자 ID)
        abort_reason: 중단 사유 (ABORTED/FAILED 시)
        cascade_event_id: 연결된 Cascade Event ID (감사 추적용)
    """

    id: str
    """고유 세션 ID."""

    namespace: str
    """네임스페이스."""

    trigger_level: str
    """복구 대상 Emergency 레벨."""

    status: RecoveryStatus = RecoveryStatus.NOT_STARTED
    """전체 복구 상태."""

    steps: list[RecoveryStep] = field(default_factory=list)
    """복구 단계 목록."""

    current_step_index: int = 0
    """현재 진행 중인 단계 인덱스 (0부터 시작)."""

    started_at: str | None = None
    """복구 시작 시각 (ISO 8601)."""

    completed_at: str | None = None
    """복구 완료 시각 (ISO 8601)."""

    initiated_by: str = "system"
    """복구 시작 주체."""

    abort_reason: str | None = None
    """중단 사유."""

    cascade_event_id: str | None = None
    """연결된 Cascade Event ID."""

    metadata: dict[str, Any] | None = None
    """
    추가 메타데이터.

    Phase 3.7: requires_approval, approved_by, approved_at 등 저장.
    """

    def get_current_step(self) -> RecoveryStep | None:
        """
        현재 진행 중인 단계 반환.

        Returns:
            현재 RecoveryStep 또는 None (모든 단계 완료 시)
        """
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def is_complete(self) -> bool:
        """
        모든 단계 완료 여부 확인.

        Returns:
            True if 모든 단계가 COMPLETED 상태
        """
        return all(step.status == RecoveryStatus.COMPLETED for step in self.steps)

    def get_progress(self) -> dict[str, Any]:
        """
        진행 상황 요약.

        Returns:
            진행률 및 단계별 상태 정보
        """
        completed = sum(1 for s in self.steps if s.status == RecoveryStatus.COMPLETED)
        total = len(self.steps)

        return {
            "completed_steps": completed,
            "total_steps": total,
            "progress_percent": (completed / total * 100) if total > 0 else 0,
            "current_step": self.current_step_index,
            "current_step_type": (
                self.steps[self.current_step_index].step_type.value if self.current_step_index < total else None
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "id": self.id,
            "namespace": self.namespace,
            "trigger_level": self.trigger_level,
            "status": self.status.value,
            "steps": [step.to_dict() for step in self.steps],
            "current_step_index": self.current_step_index,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "abort_reason": self.abort_reason,
            "cascade_event_id": self.cascade_event_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoverySession:
        """딕셔너리에서 생성."""
        steps = [RecoveryStep.from_dict(step_data) for step_data in data.get("steps", [])]

        return cls(
            id=data["id"],
            namespace=data["namespace"],
            trigger_level=data["trigger_level"],
            status=RecoveryStatus(data.get("status", "not_started")),
            steps=steps,
            current_step_index=data.get("current_step_index", 0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            initiated_by=data.get("initiated_by", "system"),
            abort_reason=data.get("abort_reason"),
            cascade_event_id=data.get("cascade_event_id"),
            metadata=data.get("metadata"),
        )
