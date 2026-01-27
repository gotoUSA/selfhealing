"""
Rollback DNA Models - 롤백 관련 데이터 모델
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RollbackStrategy(Enum):
    """롤백 전략"""

    AUTOMATIC = "automatic"  # 자동 롤백
    MANUAL = "manual"  # 수동 승인 필요
    CANARY = "canary"  # 카나리 방식 (점진적)
    BLUE_GREEN = "blue_green"  # 블루-그린 전환
    INSTANT = "instant"  # 즉시 롤백


class RollbackState(Enum):
    """롤백 상태"""

    PENDING = "pending"  # 대기 중
    IN_PROGRESS = "in_progress"  # 진행 중
    COMPLETED = "completed"  # 완료
    FAILED = "failed"  # 실패
    CANCELLED = "cancelled"  # 취소됨


@dataclass
class RollbackPolicy:
    """롤백 정책"""

    policy_id: str
    stage_name: str
    strategy: RollbackStrategy = RollbackStrategy.AUTOMATIC
    timeout_seconds: int = 120
    max_retries: int = 3
    require_approval: bool = False
    notify_on_rollback: bool = True
    preserve_data: bool = True
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "stage_name": self.stage_name,
            "strategy": self.strategy.value,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "require_approval": self.require_approval,
            "notify_on_rollback": self.notify_on_rollback,
            "preserve_data": self.preserve_data,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class RollbackRequest:
    """롤백 요청"""

    request_id: str
    stage_name: str
    reason: str
    triggered_by: str = "system"  # system, user, policy
    source_version: str = ""
    target_version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    requested_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "stage_name": self.stage_name,
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "metadata": self.metadata,
            "requested_at": self.requested_at.isoformat(),
        }


@dataclass
class RollbackResult:
    """롤백 결과"""

    request_id: str
    state: RollbackState
    message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    affected_components: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.state == RollbackState.COMPLETED

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "state": self.state.value,
            "is_success": self.is_success,
            "message": self.message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "duration_seconds": self.duration_seconds,
            "affected_components": self.affected_components,
            "errors": self.errors,
        }
