"""
Blast Radius DNA Models - 장애 영향 범위 관련 데이터 모델
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class BlastRadiusLevel(Enum):
    """영향 범위 수준"""

    ISOLATED = "isolated"  # 완전 격리 (단일 컴포넌트)
    LIMITED = "limited"  # 제한적 (같은 서비스 내)
    MODERATE = "moderate"  # 중간 (관련 서비스들)
    EXTENSIVE = "extensive"  # 광범위 (여러 도메인)
    CRITICAL = "critical"  # 치명적 (전체 시스템)


@dataclass
class ServiceDependency:
    """서비스 의존성"""

    source_service: str
    target_service: str
    dependency_type: str = "sync"  # sync, async, weak
    criticality: str = "medium"  # low, medium, high, critical
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_service": self.source_service,
            "target_service": self.target_service,
            "dependency_type": self.dependency_type,
            "criticality": self.criticality,
            "metadata": self.metadata,
        }


@dataclass
class BlastRadiusPolicy:
    """영향 범위 정책"""

    policy_id: str
    stage_name: str
    level: BlastRadiusLevel = BlastRadiusLevel.ISOLATED
    affected_services: list[str] = field(default_factory=list)
    max_affected_percentage: float = 10.0  # 최대 영향 비율 (%)
    auto_isolate: bool = True
    isolation_timeout_seconds: int = 300
    notify_threshold: BlastRadiusLevel = BlastRadiusLevel.MODERATE
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "stage_name": self.stage_name,
            "level": self.level.value,
            "affected_services": self.affected_services,
            "max_affected_percentage": self.max_affected_percentage,
            "auto_isolate": self.auto_isolate,
            "isolation_timeout_seconds": self.isolation_timeout_seconds,
            "notify_threshold": self.notify_threshold.value,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ImpactAssessment:
    """영향 평가 결과"""

    assessment_id: str
    stage_name: str
    trigger_event: str
    level: BlastRadiusLevel
    affected_services: list[str]
    affected_users_estimate: int = 0
    affected_percentage: float = 0.0
    dependencies_analyzed: int = 0
    cascading_risk: bool = False
    recommendations: list[str] = field(default_factory=list)
    assessed_at: datetime = field(default_factory=datetime.now)

    @property
    def is_critical(self) -> bool:
        return self.level in [BlastRadiusLevel.EXTENSIVE, BlastRadiusLevel.CRITICAL]

    def to_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id,
            "stage_name": self.stage_name,
            "trigger_event": self.trigger_event,
            "level": self.level.value,
            "affected_services": self.affected_services,
            "affected_users_estimate": self.affected_users_estimate,
            "affected_percentage": self.affected_percentage,
            "dependencies_analyzed": self.dependencies_analyzed,
            "cascading_risk": self.cascading_risk,
            "is_critical": self.is_critical,
            "recommendations": self.recommendations,
            "assessed_at": self.assessed_at.isoformat(),
        }
