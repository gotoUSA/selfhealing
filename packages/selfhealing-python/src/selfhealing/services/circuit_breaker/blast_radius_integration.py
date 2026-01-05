"""
Blast Radius Integration for Circuit Breaker

CB가 자동 OPEN되기 전에 연쇄 장애 영향을 분석하여,
CRITICAL 수준이면 OPEN을 보류합니다.

Reference: docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
Section 7 - Blast Radius 연동

통합 플로우:
    record_failure() 호출
            │
            ▼
    ┌───────────────────┐
    │  실패 횟수 증가    │
    └────────┬──────────┘
             │
             ▼
        threshold 초과?
             │
        Yes  │  No
             │   └──▶ 종료
             ▼
    ┌───────────────────┐
    │  Blast Radius     │
    │  assess_impact()  │
    └────────┬──────────┘
             │
             ▼
        level == CRITICAL?
             │
        Yes  │  No
             │   └──▶ OPEN 진행
             ▼
    ┌───────────────────────────────────────┐
    │  OPEN 보류                             │
    │  - 운영팀 알림                         │
    │  - Audit: GOVERNANCE_BLOCKED 기록     │
    │  - 수동 승인 대기                      │
    └───────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
import uuid

logger = logging.getLogger(__name__)


# =============================================================================
# Blast Radius Level
# =============================================================================


class BlastRadiusLevel(Enum):
    """
    Blast Radius 영향 레벨.
    
    MINIMAL: 영향 서비스 0-1개
    MODERATE: 영향 서비스 2-3개
    EXTENSIVE: 영향 서비스 4-5개
    CRITICAL: 영향 서비스 6개 이상 또는 critical 서비스 포함
    """
    MINIMAL = "minimal"       # 영향 범위 최소
    MODERATE = "moderate"     # 중간 영향
    EXTENSIVE = "extensive"   # 광범위한 영향
    CRITICAL = "critical"     # 치명적 영향 (자동 OPEN 차단)


# =============================================================================
# Blast Radius Assessment Result
# =============================================================================


@dataclass
class BlastRadiusAssessment:
    """
    Blast Radius 영향 평가 결과.
    
    Attributes:
        assessment_id: 평가 고유 ID
        level: 영향 레벨
        trigger_service: 트리거 서비스 (CB OPEN 대상)
        affected_services: 영향받는 서비스 목록
        affected_count: 영향받는 서비스 수
        cascading_risk: 연쇄 장애 위험 여부
        critical_services_affected: 영향받는 critical 서비스 목록
        recommendation: 권장 조치
        details: 추가 상세 정보
        timestamp: 평가 시간
    """
    assessment_id: str = field(default_factory=lambda: f"blast-{uuid.uuid4().hex[:8]}")
    level: BlastRadiusLevel = BlastRadiusLevel.MINIMAL
    trigger_service: str = ""
    affected_services: List[str] = field(default_factory=list)
    affected_count: int = 0
    cascading_risk: bool = False
    critical_services_affected: List[str] = field(default_factory=list)
    recommendation: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def should_block_auto_open(self) -> bool:
        """자동 OPEN을 차단해야 하는지 여부."""
        return self.level == BlastRadiusLevel.CRITICAL


# =============================================================================
# Service Dependency Graph
# =============================================================================


@dataclass
class ServiceDependency:
    """
    서비스 의존성 정보.
    
    Attributes:
        service_id: 서비스 ID
        depends_on: 이 서비스가 의존하는 서비스 목록
        dependents: 이 서비스에 의존하는 서비스 목록
        criticality: 서비스 criticality
    """
    service_id: str
    depends_on: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    criticality: str = "medium"


class ServiceDependencyGraph:
    """
    서비스 의존성 그래프 관리.
    
    CB OPEN 시 연쇄 장애 영향을 분석하기 위한 의존성 정보를 관리합니다.
    """
    
    def __init__(self):
        self._dependencies: Dict[str, ServiceDependency] = {}
    
    def register_service(
        self, 
        service_id: str,
        depends_on: Optional[List[str]] = None,
        criticality: str = "medium",
    ) -> None:
        """
        서비스와 의존성 등록.
        
        Args:
            service_id: 서비스 ID
            depends_on: 이 서비스가 의존하는 서비스 목록
            criticality: 서비스 criticality
        """
        depends_on = depends_on or []
        
        # 서비스 등록 또는 업데이트
        if service_id in self._dependencies:
            dep = self._dependencies[service_id]
            dep.depends_on = depends_on
            dep.criticality = criticality
        else:
            self._dependencies[service_id] = ServiceDependency(
                service_id=service_id,
                depends_on=depends_on,
                criticality=criticality,
            )
        
        # 역방향 의존성 업데이트 (dependents)
        for dep_service in depends_on:
            if dep_service not in self._dependencies:
                self._dependencies[dep_service] = ServiceDependency(
                    service_id=dep_service,
                )
            self._dependencies[dep_service].dependents.append(service_id)
    
    def get_dependents(self, service_id: str) -> List[str]:
        """
        서비스에 의존하는 서비스 목록 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            List[str]: 의존하는 서비스 목록
        """
        if service_id not in self._dependencies:
            return []
        return list(set(self._dependencies[service_id].dependents))
    
    def get_cascading_affected(
        self, 
        service_id: str, 
        visited: Optional[set] = None,
    ) -> List[str]:
        """
        연쇄적으로 영향받는 모든 서비스 조회.
        
        Args:
            service_id: 서비스 ID
            visited: 방문한 서비스 (순환 방지)
            
        Returns:
            List[str]: 영향받는 서비스 목록 (재귀적)
        """
        if visited is None:
            visited = set()
        
        if service_id in visited:
            return []
        
        visited.add(service_id)
        affected = []
        
        for dependent in self.get_dependents(service_id):
            if dependent not in visited:
                affected.append(dependent)
                affected.extend(self.get_cascading_affected(dependent, visited))
        
        return list(set(affected))
    
    def get_critical_dependents(self, service_id: str) -> List[str]:
        """
        서비스에 의존하는 critical 서비스 목록 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            List[str]: critical 서비스 목록
        """
        affected = self.get_cascading_affected(service_id)
        return [
            s for s in affected
            if s in self._dependencies and 
               self._dependencies[s].criticality == "critical"
        ]
    
    def clear(self) -> None:
        """모든 의존성 정보 초기화."""
        self._dependencies.clear()


# =============================================================================
# Blast Radius Integration Manager
# =============================================================================


class BlastRadiusIntegration:
    """
    Circuit Breaker와 Blast Radius 연동 관리자.
    
    CB가 자동 OPEN되기 전에 연쇄 장애 영향을 분석하여,
    CRITICAL 수준이면 OPEN을 보류합니다.
    
    Usage:
        integration = BlastRadiusIntegration()
        
        # 의존성 등록
        integration.register_dependency("order-api", depends_on=["payment-api", "inventory-api"])
        integration.register_dependency("cart-api", depends_on=["payment-api"])
        
        # CB OPEN 전 영향 평가
        assessment = integration.assess_impact(
            trigger_service="payment-api",
            trigger_event="CB auto-opening due to timeout errors",
        )
        
        if assessment.should_block_auto_open():
            # OPEN 보류, 운영팀 알림
            send_alert(assessment)
    
    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 7 - Blast Radius 연동
    """
    
    _instance: Optional["BlastRadiusIntegration"] = None
    
    def __new__(cls):
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        
        self._dependency_graph = ServiceDependencyGraph()
        self._service_criticality: Dict[str, str] = {}
        self._config = BlastRadiusConfig()
        self._last_assessment: Optional[BlastRadiusAssessment] = None
        self._initialized = True
        
        logger.debug("[BlastRadiusIntegration] Initialized")
    
    @classmethod
    def reset_instance(cls) -> None:
        """싱글톤 인스턴스 초기화 (테스트용)."""
        cls._instance = None
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    def configure(
        self,
        critical_threshold: int = 6,
        extensive_threshold: int = 4,
        moderate_threshold: int = 2,
        block_on_critical: bool = True,
        alert_on_extensive: bool = True,
    ) -> None:
        """
        Blast Radius 설정.
        
        Args:
            critical_threshold: CRITICAL 레벨 임계값 (영향 서비스 수)
            extensive_threshold: EXTENSIVE 레벨 임계값
            moderate_threshold: MODERATE 레벨 임계값
            block_on_critical: CRITICAL 시 자동 OPEN 차단 여부
            alert_on_extensive: EXTENSIVE 시 알림 발송 여부
        """
        self._config = BlastRadiusConfig(
            critical_threshold=critical_threshold,
            extensive_threshold=extensive_threshold,
            moderate_threshold=moderate_threshold,
            block_on_critical=block_on_critical,
            alert_on_extensive=alert_on_extensive,
        )
        logger.info(
            f"[BlastRadiusIntegration] Configured: critical_threshold={critical_threshold}"
        )
    
    # =========================================================================
    # Dependency Management
    # =========================================================================
    
    def register_dependency(
        self,
        service_id: str,
        depends_on: Optional[List[str]] = None,
        criticality: str = "medium",
    ) -> None:
        """
        서비스 의존성 등록.
        
        Args:
            service_id: 서비스 ID
            depends_on: 이 서비스가 의존하는 서비스 목록
            criticality: 서비스 criticality
        """
        self._dependency_graph.register_service(
            service_id=service_id,
            depends_on=depends_on,
            criticality=criticality,
        )
        self._service_criticality[service_id] = criticality
        
        logger.debug(
            f"[BlastRadiusIntegration] Dependency registered: {service_id} "
            f"(criticality={criticality}, depends_on={depends_on})"
        )
    
    def set_service_criticality(self, service_id: str, criticality: str) -> None:
        """
        서비스 criticality 설정.
        
        Args:
            service_id: 서비스 ID
            criticality: criticality 레벨
        """
        self._service_criticality[service_id] = criticality
        if service_id in self._dependency_graph._dependencies:
            self._dependency_graph._dependencies[service_id].criticality = criticality
    
    def clear_dependencies(self) -> None:
        """모든 의존성 정보 초기화."""
        self._dependency_graph.clear()
        self._service_criticality.clear()
    
    # =========================================================================
    # Impact Assessment
    # =========================================================================
    
    def assess_impact(
        self,
        trigger_service: str,
        trigger_event: str = "",
        failing_services: Optional[List[str]] = None,
    ) -> BlastRadiusAssessment:
        """
        CB OPEN 시 영향 평가.
        
        Args:
            trigger_service: 트리거 서비스 (CB OPEN 대상)
            trigger_event: 트리거 이벤트 설명
            failing_services: 추가로 실패 중인 서비스 목록
            
        Returns:
            BlastRadiusAssessment: 영향 평가 결과
        """
        failing_services = failing_services or []
        all_failing = list(set([trigger_service] + failing_services))
        
        # 1. 연쇄적으로 영향받는 서비스 수집
        affected_services = set()
        for service in all_failing:
            affected = self._dependency_graph.get_cascading_affected(service)
            affected_services.update(affected)
        
        # 트리거 서비스 자체는 제외
        affected_services.discard(trigger_service)
        affected_list = list(affected_services)
        affected_count = len(affected_list)
        
        # 2. Critical 서비스 영향 확인
        critical_affected = [
            s for s in affected_list
            if self._service_criticality.get(s) == "critical"
        ]
        
        # 3. 레벨 결정
        level = self._determine_level(affected_count, critical_affected)
        
        # 4. 연쇄 장애 위험 판단
        cascading_risk = (
            affected_count >= self._config.moderate_threshold or
            len(critical_affected) > 0
        )
        
        # 5. 권장 조치 결정
        recommendation = self._get_recommendation(level, critical_affected)
        
        # 평가 결과 생성
        assessment = BlastRadiusAssessment(
            level=level,
            trigger_service=trigger_service,
            affected_services=affected_list,
            affected_count=affected_count,
            cascading_risk=cascading_risk,
            critical_services_affected=critical_affected,
            recommendation=recommendation,
            details={
                "trigger_event": trigger_event,
                "failing_services": all_failing,
                "config": {
                    "critical_threshold": self._config.critical_threshold,
                    "block_on_critical": self._config.block_on_critical,
                },
            },
        )
        
        self._last_assessment = assessment
        
        logger.info(
            f"[BlastRadiusIntegration] Impact assessed: {trigger_service} | "
            f"level={level.value} | affected={affected_count} | "
            f"critical_affected={len(critical_affected)}"
        )
        
        return assessment
    
    def _determine_level(
        self, 
        affected_count: int, 
        critical_affected: List[str],
    ) -> BlastRadiusLevel:
        """영향 레벨 결정."""
        # Critical 서비스가 영향받으면 무조건 CRITICAL
        if critical_affected:
            return BlastRadiusLevel.CRITICAL
        
        # 영향 서비스 수 기반 결정
        if affected_count >= self._config.critical_threshold:
            return BlastRadiusLevel.CRITICAL
        elif affected_count >= self._config.extensive_threshold:
            return BlastRadiusLevel.EXTENSIVE
        elif affected_count >= self._config.moderate_threshold:
            return BlastRadiusLevel.MODERATE
        else:
            return BlastRadiusLevel.MINIMAL
    
    def _get_recommendation(
        self, 
        level: BlastRadiusLevel, 
        critical_affected: List[str],
    ) -> str:
        """권장 조치 결정."""
        if level == BlastRadiusLevel.CRITICAL:
            if critical_affected:
                return (
                    f"CB OPEN 차단 권장: critical 서비스 영향 ({', '.join(critical_affected)}). "
                    f"수동 승인 필요."
                )
            return "CB OPEN 차단 권장: 영향 범위가 너무 넓음. 수동 승인 필요."
        elif level == BlastRadiusLevel.EXTENSIVE:
            return "CB OPEN 진행 가능, 단 운영팀 경고 알림 필요."
        elif level == BlastRadiusLevel.MODERATE:
            return "CB OPEN 진행 가능, 모니터링 강화 권장."
        else:
            return "CB OPEN 진행 가능, 영향 최소."
    
    # =========================================================================
    # Auto OPEN Decision
    # =========================================================================
    
    def should_auto_open(
        self,
        service_id: str,
        trigger_event: str = "threshold_exceeded",
    ) -> Tuple[bool, Optional[str], Optional[BlastRadiusAssessment]]:
        """
        자동 OPEN 허용 여부 판단.
        
        Args:
            service_id: 서비스 ID
            trigger_event: 트리거 이벤트
            
        Returns:
            tuple[bool, Optional[str], Optional[BlastRadiusAssessment]]:
                (허용 여부, 거부 시 사유, 평가 결과)
        """
        # 1. Blast Radius 영향 평가
        assessment = self.assess_impact(
            trigger_service=service_id,
            trigger_event=f"CB auto-opening: {trigger_event}",
        )
        
        # 2. CRITICAL이고 block_on_critical이면 차단
        if assessment.level == BlastRadiusLevel.CRITICAL and self._config.block_on_critical:
            reason = (
                f"Blast Radius CRITICAL: {assessment.affected_count} services affected. "
                f"Cascading risk: {assessment.cascading_risk}"
            )
            
            # Audit 기록
            self._log_governance_blocked(service_id, assessment)
            
            return False, reason, assessment
        
        # 3. EXTENSIVE면 경고만
        if assessment.level == BlastRadiusLevel.EXTENSIVE and self._config.alert_on_extensive:
            logger.warning(
                f"[BlastRadiusIntegration] CB auto-open proceeding with caution: "
                f"{service_id}, blast radius EXTENSIVE ({assessment.affected_count} services)"
            )
        
        return True, None, assessment
    
    def _log_governance_blocked(
        self, 
        service_id: str, 
        assessment: BlastRadiusAssessment,
    ) -> None:
        """GOVERNANCE_BLOCKED Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_governance_blocked_cb_audit
            
            log_governance_blocked_cb_audit(
                service_id=service_id,
                action="auto_open",
                block_reason="blast_radius_critical",
                blast_radius_level=assessment.level.value.upper(),
                affected_services=assessment.affected_services,
                assessment_id=assessment.assessment_id,
            )
        except ImportError:
            logger.warning(
                f"[BlastRadiusIntegration] GOVERNANCE_BLOCKED | service={service_id} | "
                f"reason=blast_radius_critical | affected={assessment.affected_count}"
            )
        except Exception as e:
            logger.debug(f"[BlastRadiusIntegration] Audit log failed: {e}")
    
    # =========================================================================
    # Status
    # =========================================================================
    
    def get_last_assessment(self) -> Optional[BlastRadiusAssessment]:
        """마지막 평가 결과 조회."""
        return self._last_assessment
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 조회."""
        return {
            "registered_services": len(self._service_criticality),
            "config": {
                "critical_threshold": self._config.critical_threshold,
                "extensive_threshold": self._config.extensive_threshold,
                "moderate_threshold": self._config.moderate_threshold,
                "block_on_critical": self._config.block_on_critical,
                "alert_on_extensive": self._config.alert_on_extensive,
            },
            "last_assessment": (
                {
                    "assessment_id": self._last_assessment.assessment_id,
                    "level": self._last_assessment.level.value,
                    "affected_count": self._last_assessment.affected_count,
                    "timestamp": self._last_assessment.timestamp,
                } if self._last_assessment else None
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# =============================================================================
# Blast Radius Config
# =============================================================================


@dataclass
class BlastRadiusConfig:
    """Blast Radius 설정."""
    critical_threshold: int = 6    # CRITICAL 임계값 (영향 서비스 수)
    extensive_threshold: int = 4   # EXTENSIVE 임계값
    moderate_threshold: int = 2    # MODERATE 임계값
    block_on_critical: bool = True   # CRITICAL 시 자동 OPEN 차단
    alert_on_extensive: bool = True  # EXTENSIVE 시 알림 발송


# =============================================================================
# Module-level Convenience Functions
# =============================================================================


_integration: Optional[BlastRadiusIntegration] = None


def get_blast_radius_integration() -> BlastRadiusIntegration:
    """
    BlastRadiusIntegration 싱글톤 인스턴스 반환.
    
    Returns:
        BlastRadiusIntegration: 싱글톤 인스턴스
    """
    global _integration
    if _integration is None:
        _integration = BlastRadiusIntegration()
    return _integration


def reset_blast_radius_integration() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _integration
    _integration = None
    BlastRadiusIntegration.reset_instance()


def assess_cb_open_impact(
    service_id: str,
    trigger_event: str = "threshold_exceeded",
) -> BlastRadiusAssessment:
    """
    CB OPEN 시 영향 평가.
    
    Args:
        service_id: 서비스 ID
        trigger_event: 트리거 이벤트
        
    Returns:
        BlastRadiusAssessment: 영향 평가 결과
    """
    return get_blast_radius_integration().assess_impact(
        trigger_service=service_id,
        trigger_event=trigger_event,
    )


def should_allow_cb_auto_open(
    service_id: str,
    trigger_event: str = "threshold_exceeded",
) -> Tuple[bool, Optional[str]]:
    """
    CB 자동 OPEN 허용 여부.
    
    Args:
        service_id: 서비스 ID
        trigger_event: 트리거 이벤트
        
    Returns:
        tuple[bool, Optional[str]]: (허용 여부, 거부 시 사유)
    """
    allowed, reason, _ = get_blast_radius_integration().should_auto_open(
        service_id=service_id,
        trigger_event=trigger_event,
    )
    return allowed, reason


def register_service_dependency(
    service_id: str,
    depends_on: Optional[List[str]] = None,
    criticality: str = "medium",
) -> None:
    """
    서비스 의존성 등록.
    
    Args:
        service_id: 서비스 ID
        depends_on: 의존하는 서비스 목록
        criticality: criticality 레벨
    """
    get_blast_radius_integration().register_dependency(
        service_id=service_id,
        depends_on=depends_on,
        criticality=criticality,
    )
