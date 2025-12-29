"""
Auto Tuning Models - 자율 조정 모델

조정 이력, 세션 관리를 위한 데이터 모델
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum


class TuningState(str, Enum):
    """튜닝 세션 상태"""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class AdjustmentRecord:
    """조정 기록"""
    record_id: str
    parameter: str
    old_value: float
    new_value: float
    reason: str
    confidence: float
    triggered_by: str  # system, manual, emergency
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    success: bool = True
    rollback_performed: bool = False
    rollback_timestamp: Optional[datetime] = None
    metrics_snapshot: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "record_id": self.record_id,
            "parameter": self.parameter,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
            "confidence": self.confidence,
            "triggered_by": self.triggered_by,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "rollback_performed": self.rollback_performed,
            "rollback_timestamp": (
                self.rollback_timestamp.isoformat() 
                if self.rollback_timestamp else None
            ),
            "metrics_snapshot": self.metrics_snapshot,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AdjustmentRecord":
        """딕셔너리에서 생성"""
        return cls(
            record_id=data["record_id"],
            parameter=data["parameter"],
            old_value=data["old_value"],
            new_value=data["new_value"],
            reason=data["reason"],
            confidence=data.get("confidence", 0.0),
            triggered_by=data.get("triggered_by", "system"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            success=data.get("success", True),
            rollback_performed=data.get("rollback_performed", False),
            rollback_timestamp=(
                datetime.fromisoformat(data["rollback_timestamp"])
                if data.get("rollback_timestamp") else None
            ),
            metrics_snapshot=data.get("metrics_snapshot", {}),
        )


@dataclass
class TuningSession:
    """튜닝 세션 - 관련 조정들의 그룹"""
    session_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    state: TuningState = TuningState.ACTIVE
    adjustments: List[str] = field(default_factory=list)  # record_id 목록
    total_adjustments: int = 0
    successful_adjustments: int = 0
    rolled_back_adjustments: int = 0
    notes: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "state": self.state.value,
            "adjustments": self.adjustments,
            "total_adjustments": self.total_adjustments,
            "successful_adjustments": self.successful_adjustments,
            "rolled_back_adjustments": self.rolled_back_adjustments,
            "notes": self.notes,
        }


__all__ = [
    "AdjustmentRecord",
    "TuningSession",
    "TuningState",
]
