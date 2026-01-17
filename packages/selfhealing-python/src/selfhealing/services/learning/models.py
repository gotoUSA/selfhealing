"""
Self-Learning DNA Models - 학습 관련 데이터 모델
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


class PatternType(Enum):
    """패턴 유형"""
    FAILURE = "failure"           # 장애 패턴
    RECOVERY = "recovery"         # 복구 패턴
    PERFORMANCE = "performance"   # 성능 패턴
    ANOMALY = "anomaly"           # 이상 패턴
    OPTIMIZATION = "optimization" # 최적화 패턴


class BlacklistReason(str, Enum):
    """
    블랙리스트 등록 사유.
    
    Escape Strategy - LearningService 블랙리스트 관리.
    루프/플래핑 탐지 시 해당 파라미터 조합을 블랙리스트에 등록합니다.
    """
    RECOVERY_LOOP = "recovery_loop"
    """복구/조정 무한 루프 감지."""
    
    CONFLICTING_ADJUSTMENT = "conflicting_adjustment"
    """상충하는 자율 조정 감지 (예: A→B→A 반복)."""
    
    FLAPPING = "flapping"
    """파라미터 플래핑 감지 (미세 조정 반복)."""
    
    MANUAL_BLOCK = "manual_block"
    """관리자에 의한 수동 차단."""


class SuggestionPriority(Enum):
    """제안 우선순위"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class LearningPattern:
    """학습된 패턴"""
    pattern_id: str
    pattern_type: PatternType
    name: str
    description: str
    confidence: float  # 0.0 ~ 1.0
    occurrence_count: int = 1
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    features: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type.value,
            "name": self.name,
            "description": self.description,
            "confidence": self.confidence,
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "features": self.features,
            "metadata": self.metadata,
        }


@dataclass
class LearningSession:
    """학습 세션"""
    session_id: str
    stage_name: str
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    patterns_learned: int = 0
    suggestions_generated: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    status: str = "running"  # running, completed, failed
    
    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "stage_name": self.stage_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "patterns_learned": self.patterns_learned,
            "suggestions_generated": self.suggestions_generated,
            "metrics": self.metrics,
            "status": self.status,
        }


@dataclass
class Suggestion:
    """최적화 제안"""
    suggestion_id: str
    title: str
    description: str
    priority: SuggestionPriority
    stage_name: str
    pattern_id: Optional[str] = None
    confidence: float = 0.8
    expected_improvement: float = 0.0  # 예상 개선율 (%)
    action: str = ""  # 실행할 액션
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    applied: bool = False
    applied_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            "suggestion_id": self.suggestion_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.value,
            "stage_name": self.stage_name,
            "pattern_id": self.pattern_id,
            "confidence": self.confidence,
            "expected_improvement": self.expected_improvement,
            "action": self.action,
            "parameters": self.parameters,
            "created_at": self.created_at.isoformat(),
            "applied": self.applied,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }


@dataclass
class PerformanceMetric:
    """성능 메트릭"""
    metric_name: str
    value: float
    unit: str = ""
    stage_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "metric_name": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "stage_name": self.stage_name,
            "timestamp": self.timestamp.isoformat(),
            "tags": self.tags,
        }


@dataclass
class BlacklistedParameter:
    """
    블랙리스트된 파라미터.
    
    순위 5.5: Escape Strategy - 위험한 파라미터 조합 학습
    Reference: 28_IMPROVEMENT_PART3_ENUM_EXTENSION.md §8.3.4
    """
    module: str
    """모듈 이름 (예: circuit_breaker, retry)."""
    
    parameter: str
    """파라미터 이름 (예: threshold, max_attempts)."""
    
    blocked_values: set[str]
    """차단된 값들."""
    
    reason: BlacklistReason
    """등록 사유."""
    
    registered_at: datetime
    """등록 시간."""
    
    registered_by: str = "system"
    """등록 주체 ('system' 또는 사용자 ID)."""
    
    incident_id: Optional[int] = None
    """관련 인시던트 ID."""
    
    expires_at: Optional[datetime] = None
    """만료 시간 (None = 영구)."""
    
    def to_dict(self) -> Dict:
        """직렬화 가능한 딕셔너리로 변환."""
        return {
            "module": self.module,
            "parameter": self.parameter,
            "blocked_values": list(self.blocked_values),
            "reason": self.reason.value,
            "registered_at": self.registered_at.isoformat(),
            "registered_by": self.registered_by,
            "incident_id": self.incident_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "BlacklistedParameter":
        """딕셔너리에서 복원."""
        return cls(
            module=data["module"],
            parameter=data["parameter"],
            blocked_values=set(data["blocked_values"]),
            reason=BlacklistReason(data["reason"]),
            registered_at=datetime.fromisoformat(data["registered_at"]),
            registered_by=data.get("registered_by", "system"),
            incident_id=data.get("incident_id"),
            expires_at=(
                datetime.fromisoformat(data["expires_at"])
                if data.get("expires_at")
                else None
            ),
        )
