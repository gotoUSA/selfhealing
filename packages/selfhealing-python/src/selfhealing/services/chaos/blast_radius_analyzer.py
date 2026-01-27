"""
Blast Radius Analyzer.

실험의 영향 범위를 분석하고 시각화합니다.
BlastRadiusManager와 ImpactPredictor를 통합하여
종합적인 영향도 분석을 제공합니다.

Related Modules:
- BlastRadiusManager: services/chaos/blast_radius.py
- ImpactPredictor: services/chaos/impact_predictor.py

Design Principle:
- 의존성 그래프 탐색을 통한 영향 범위 분석
- 폭발 반경 레벨 계산
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class BlastRadiusLevel(str, Enum):
    """폭발 반경 레벨."""

    MINIMAL = "minimal"
    """단일 인스턴스/팟. 가장 낮은 위험."""

    CONTAINED = "contained"
    """단일 서비스. 격리된 영향."""

    MODERATE = "moderate"
    """2-3개 서비스 영향. 주의 필요."""

    EXTENSIVE = "extensive"
    """4개 이상 서비스 영향. 승인 필요."""

    CRITICAL = "critical"
    """핵심 서비스 포함. 상위 승인 필요."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class DependencyNode:
    """의존성 그래프 노드."""

    service_name: str
    depth: int = 0  # 타겟 서비스로부터의 거리
    is_critical: bool = False  # 핵심 서비스 여부
    dependency_type: str = "downstream"  # "upstream" | "downstream"
    impact_score: float = 0.0  # 0.0 ~ 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "service_name": self.service_name,
            "depth": self.depth,
            "is_critical": self.is_critical,
            "dependency_type": self.dependency_type,
            "impact_score": self.impact_score,
        }


@dataclass
class BlastRadiusAnalysisResult:
    """폭발 반경 분석 결과."""

    # 분석 대상
    target_service: str
    experiment_type: str

    # 폭발 반경 레벨
    level: BlastRadiusLevel = BlastRadiusLevel.CONTAINED

    # 영향받는 서비스
    affected_services: list[DependencyNode] = field(default_factory=list)
    total_affected_count: int = 0

    # 핵심 서비스 포함 여부
    includes_critical_services: bool = False
    critical_services: list[str] = field(default_factory=list)

    # 위험 점수 (0.0 ~ 1.0)
    risk_score: float = 0.0

    # 권장 사항
    recommendations: list[str] = field(default_factory=list)

    # 승인 요구사항
    requires_approval: bool = False
    approval_level: str = ""  # "team_lead" | "sre_manager" | "cto"

    # 실험 가능 여부
    experiment_allowed: bool = True
    blocking_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "target_service": self.target_service,
            "experiment_type": self.experiment_type,
            "level": self.level.value,
            "affected_services": [s.to_dict() for s in self.affected_services],
            "total_affected_count": self.total_affected_count,
            "includes_critical_services": self.includes_critical_services,
            "critical_services": self.critical_services,
            "risk_score": self.risk_score,
            "recommendations": self.recommendations,
            "requires_approval": self.requires_approval,
            "approval_level": self.approval_level,
            "experiment_allowed": self.experiment_allowed,
            "blocking_reasons": self.blocking_reasons,
        }


# =============================================================================
# Blast Radius Analyzer
# =============================================================================


class BlastRadiusAnalyzer:
    """
    폭발 반경 분석기.

    의존성 그래프를 탐색하여 Chaos 실험의
    잠재적 영향 범위를 분석합니다.

    Usage:
        analyzer = BlastRadiusAnalyzer()
        result = analyzer.analyze(
            target_service="payment-api",
            experiment_type="latency_injection",
        )

        if result.requires_approval:
            print(f"승인 필요: {result.approval_level}")
    """

    # 핵심 서비스 목록 (설정으로 분리 권장)
    CRITICAL_SERVICES = {
        "payment-api",
        "auth-service",
        "order-service",
        "user-service",
    }

    # 서비스 의존성 맵 (실제로는 서비스 메시/레지스트리에서 조회)
    # upstream: 이 서비스에 의존하는 서비스들
    # downstream: 이 서비스가 의존하는 서비스들
    SERVICE_DEPENDENCIES = {
        "payment-api": {
            "upstream": ["order-service", "checkout-service"],
            "downstream": ["bank-gateway", "fraud-detection"],
        },
        "order-service": {
            "upstream": ["api-gateway", "mobile-app"],
            "downstream": ["payment-api", "inventory-service", "notification-service"],
        },
        "user-service": {
            "upstream": ["api-gateway", "mobile-app", "web-app"],
            "downstream": ["auth-service", "profile-db"],
        },
        "inventory-service": {
            "upstream": ["order-service"],
            "downstream": ["warehouse-api", "supplier-gateway"],
        },
        "notification-service": {
            "upstream": ["order-service", "payment-api", "user-service"],
            "downstream": ["email-provider", "sms-gateway", "push-service"],
        },
    }

    def __init__(
        self,
        max_depth: int = 3,
        critical_services: set[str] | None = None,
    ):
        """
        Args:
            max_depth: 의존성 탐색 최대 깊이
            critical_services: 핵심 서비스 목록 (None이면 기본값 사용)
        """
        self._max_depth = max_depth
        self._critical_services = critical_services or self.CRITICAL_SERVICES

    def analyze(
        self,
        target_service: str,
        experiment_type: str,
        blast_radius_level: str | None = None,
    ) -> BlastRadiusAnalysisResult:
        """
        폭발 반경 분석 수행.

        Args:
            target_service: 대상 서비스
            experiment_type: 실험 유형
            blast_radius_level: 요청된 폭발 반경 레벨 (optional)

        Returns:
            BlastRadiusAnalysisResult: 분석 결과
        """
        logger.info(
            f"[BlastRadiusAnalyzer] Analyzing blast radius for "
            f"{experiment_type} on {target_service}"
        )

        # 1. 영향받는 서비스 탐색
        affected_services = self._traverse_dependencies(target_service)

        # 2. 핵심 서비스 포함 여부 확인
        critical_services = self._find_critical_services(affected_services)
        includes_critical = len(critical_services) > 0

        # 3. 폭발 반경 레벨 계산
        level = self._calculate_blast_radius_level(
            affected_count=len(affected_services),
            includes_critical=includes_critical,
        )

        # 4. 위험 점수 계산
        risk_score = self._calculate_risk_score(
            affected_services=affected_services,
            includes_critical=includes_critical,
            experiment_type=experiment_type,
        )

        # 5. 승인 요구사항 결정
        requires_approval, approval_level = self._determine_approval_requirement(
            level=level,
            includes_critical=includes_critical,
            risk_score=risk_score,
        )

        # 6. 권장 사항 생성
        recommendations = self._generate_recommendations(
            level=level,
            affected_services=affected_services,
            includes_critical=includes_critical,
            experiment_type=experiment_type,
        )

        # 7. 실험 가능 여부 결정
        allowed, blocking_reasons = self._check_experiment_allowed(
            target_service=target_service,
            experiment_type=experiment_type,
            level=level,
            includes_critical=includes_critical,
        )

        result = BlastRadiusAnalysisResult(
            target_service=target_service,
            experiment_type=experiment_type,
            level=level,
            affected_services=affected_services,
            total_affected_count=len(affected_services),
            includes_critical_services=includes_critical,
            critical_services=critical_services,
            risk_score=risk_score,
            recommendations=recommendations,
            requires_approval=requires_approval,
            approval_level=approval_level,
            experiment_allowed=allowed,
            blocking_reasons=blocking_reasons,
        )

        logger.info(
            f"[BlastRadiusAnalyzer] Analysis complete: level={level.value}, "
            f"affected={len(affected_services)}, risk={risk_score:.2f}"
        )

        return result

    def analyze_affected_services(
        self,
        target_service: str,
    ) -> list[DependencyNode]:
        """
        영향받는 서비스 목록 조회.

        Args:
            target_service: 대상 서비스

        Returns:
            List[DependencyNode]: 영향받는 서비스 노드 목록
        """
        return self._traverse_dependencies(target_service)

    def _traverse_dependencies(
        self,
        target_service: str,
        current_depth: int = 0,
        visited: set[str] | None = None,
    ) -> list[DependencyNode]:
        """의존성 그래프 탐색 (BFS)."""
        if visited is None:
            visited = set()

        if target_service in visited or current_depth > self._max_depth:
            return []

        visited.add(target_service)
        nodes = []

        deps = self.SERVICE_DEPENDENCIES.get(target_service, {})

        # Upstream 서비스 (이 서비스에 의존하는 서비스들 - 영향 전파)
        for upstream in deps.get("upstream", []):
            if upstream not in visited:
                impact_score = 1.0 / (current_depth + 1)  # 거리에 따라 감쇠
                node = DependencyNode(
                    service_name=upstream,
                    depth=current_depth + 1,
                    is_critical=upstream in self._critical_services,
                    dependency_type="upstream",
                    impact_score=impact_score,
                )
                nodes.append(node)

                # 재귀 탐색
                child_nodes = self._traverse_dependencies(
                    target_service=upstream,
                    current_depth=current_depth + 1,
                    visited=visited,
                )
                nodes.extend(child_nodes)

        return nodes

    def _find_critical_services(
        self,
        affected_services: list[DependencyNode],
    ) -> list[str]:
        """영향받는 서비스 중 핵심 서비스 필터링."""
        return [node.service_name for node in affected_services if node.is_critical]

    def _calculate_blast_radius_level(
        self,
        affected_count: int,
        includes_critical: bool,
    ) -> BlastRadiusLevel:
        """폭발 반경 레벨 계산."""
        if includes_critical:
            return BlastRadiusLevel.CRITICAL

        if affected_count == 0:
            return BlastRadiusLevel.MINIMAL
        elif affected_count <= 2:
            return BlastRadiusLevel.CONTAINED
        elif affected_count <= 4:
            return BlastRadiusLevel.MODERATE
        else:
            return BlastRadiusLevel.EXTENSIVE

    def _calculate_risk_score(
        self,
        affected_services: list[DependencyNode],
        includes_critical: bool,
        experiment_type: str,
    ) -> float:
        """위험 점수 계산 (0.0 ~ 1.0)."""
        base_score = 0.1

        # 영향받는 서비스 수에 따른 가산
        affected_score = min(0.3, len(affected_services) * 0.05)

        # 핵심 서비스 포함 시 가산
        critical_score = 0.3 if includes_critical else 0.0

        # 실험 유형에 따른 가산
        type_scores = {
            "failure_injection": 0.2,
            "resource_exhaustion": 0.25,
            "latency_injection": 0.1,
            "network_partition": 0.3,
        }
        type_score = type_scores.get(experiment_type, 0.15)

        # 영향 점수 합계
        impact_sum = sum(node.impact_score for node in affected_services)
        impact_score = min(0.2, impact_sum * 0.05)

        total = base_score + affected_score + critical_score + type_score + impact_score
        return min(1.0, total)

    def _determine_approval_requirement(
        self,
        level: BlastRadiusLevel,
        includes_critical: bool,
        risk_score: float,
    ) -> tuple[bool, str]:
        """승인 요구사항 결정."""
        if level == BlastRadiusLevel.CRITICAL or includes_critical:
            return True, "cto"

        if level == BlastRadiusLevel.EXTENSIVE or risk_score > 0.7:
            return True, "sre_manager"

        if level == BlastRadiusLevel.MODERATE or risk_score > 0.5:
            return True, "team_lead"

        return False, ""

    def _generate_recommendations(
        self,
        level: BlastRadiusLevel,
        affected_services: list[DependencyNode],
        includes_critical: bool,
        experiment_type: str,
    ) -> list[str]:
        """권장 사항 생성."""
        recommendations = []

        if includes_critical:
            recommendations.append(
                "⚠️ 핵심 서비스가 영향 범위에 포함되어 있습니다. "
                "업무 시간 외 실험을 권장합니다."
            )

        if level in (BlastRadiusLevel.EXTENSIVE, BlastRadiusLevel.CRITICAL):
            recommendations.append(
                "영향 범위가 넓습니다. INSTANCE 레벨에서 시작하여 "
                "점진적으로 확대하는 것을 권장합니다."
            )

        if len(affected_services) > 3:
            affected_names = [s.service_name for s in affected_services[:3]]
            recommendations.append(
                f"영향받는 서비스: {', '.join(affected_names)} 외 "
                f"{len(affected_services) - 3}개. 모니터링 강화를 권장합니다."
            )

        if experiment_type == "failure_injection":
            recommendations.append(
                "failure_injection 실험은 단계적으로 failure_rate를 증가시키세요. "
                "시작: 10%, 최대: 50%"
            )

        if not recommendations:
            recommendations.append("영향 범위가 제한적입니다. 실험 진행이 안전합니다.")

        return recommendations

    def _check_experiment_allowed(
        self,
        target_service: str,
        experiment_type: str,
        level: BlastRadiusLevel,
        includes_critical: bool,
    ) -> tuple[bool, list[str]]:
        """실험 가능 여부 확인."""
        blocking_reasons = []

        # BlastRadiusManager와 연동하여 확인
        try:
            from selfhealing.services.chaos.blast_radius import (
                BlastRadius,
                get_blast_radius_manager,
            )

            manager = get_blast_radius_manager()

            # 레벨에 따른 blast_radius 매핑
            br_map = {
                BlastRadiusLevel.MINIMAL: BlastRadius.INSTANCE,
                BlastRadiusLevel.CONTAINED: BlastRadius.INSTANCE,
                BlastRadiusLevel.MODERATE: BlastRadius.SERVICE,
                BlastRadiusLevel.EXTENSIVE: BlastRadius.SERVICE,
                BlastRadiusLevel.CRITICAL: BlastRadius.REGION,
            }
            blast_radius = br_map.get(level, BlastRadius.SERVICE)

            result = manager.check(
                blast_radius=blast_radius,
                target_service=target_service,
            )

            if not result.allowed:
                blocking_reasons.extend(result.violations)

        except Exception as e:
            logger.warning(f"[BlastRadiusAnalyzer] Manager check failed: {e}")

        return len(blocking_reasons) == 0, blocking_reasons


# =============================================================================
# Singleton
# =============================================================================

_instance: BlastRadiusAnalyzer | None = None


def get_blast_radius_analyzer() -> BlastRadiusAnalyzer:
    """싱글톤 인스턴스 반환."""
    global _instance
    if _instance is None:
        _instance = BlastRadiusAnalyzer()
    return _instance
