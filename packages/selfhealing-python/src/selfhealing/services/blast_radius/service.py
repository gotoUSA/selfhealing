"""
Blast Radius DNA Service - 장애 영향 범위 관리 서비스

Audit Integration (85_AUDIT_INTEGRATION_OVERVIEW.md Phase 1):
- 정책 설정: log_blast_radius_audit (action="set_policy")
- 의존성 추가: log_blast_radius_audit (action="add_dependency")
- 서비스 격리: log_blast_radius_audit (action="isolate_service")
- 격리 해제: log_blast_radius_audit (action="release_isolation")
"""

from __future__ import annotations

import uuid
from threading import Lock

import structlog

from selfhealing.services.audit import log_blast_radius_audit

from .models import (
    BlastRadiusLevel,
    BlastRadiusPolicy,
    ImpactAssessment,
    ServiceDependencyEdge,
)

logger = structlog.get_logger()


class BlastRadiusService:
    """
    Blast Radius DNA 서비스

    장애 영향 범위를 분석하고 관리합니다.
    """

    _instance: BlastRadiusService | None = None
    _lock = Lock()

    def __new__(cls) -> BlastRadiusService:
        """싱글톤 패턴"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._policies: dict[str, BlastRadiusPolicy] = {}
        self._dependencies: list[ServiceDependencyEdge] = []
        self._assessments: list[ImpactAssessment] = []
        self._isolated_services: set[str] = set()
        self._enabled = True
        self._initialized = True

        logger.info("blast_radius_service.initialized")

    def set_policy(
        self,
        stage_name: str,
        level: BlastRadiusLevel = BlastRadiusLevel.MINIMAL,
        affected_services: list[str] | None = None,
        max_affected_percentage: float = 10.0,
        auto_isolate: bool = True,
    ) -> BlastRadiusPolicy:
        """
        영향 범위 정책 설정

        Args:
            stage_name: Stage 이름
            level: 영향 범위 수준
            affected_services: 영향받는 서비스 목록
            max_affected_percentage: 최대 영향 비율
            auto_isolate: 자동 격리 여부

        Returns:
            BlastRadiusPolicy: 설정된 정책
        """
        policy_id = str(uuid.uuid4())[:8]
        policy = BlastRadiusPolicy(
            policy_id=policy_id,
            stage_name=stage_name,
            level=level,
            affected_services=affected_services or [],
            max_affected_percentage=max_affected_percentage,
            auto_isolate=auto_isolate,
        )
        self._policies[stage_name] = policy
        logger.info(
            "blast_radius_policy_set",
            stage_name=stage_name,
            blast_radius_level=level.value,
        )

        # === Audit 기록: 정책 설정 (85_AUDIT_INTEGRATION Phase 1) ===
        log_blast_radius_audit(
            experiment_id=policy_id,
            blast_radius=level.value,
            target_service=stage_name,
            action="set_policy",
            allowed=True,
            target_domain=stage_name,
            traffic_percent=max_affected_percentage,
            reason=f"Policy configured with auto_isolate={auto_isolate}",
        )

        return policy

    def get_policy(self, stage_name: str) -> BlastRadiusPolicy | None:
        """정책 조회"""
        return self._policies.get(stage_name)

    def add_dependency(
        self,
        source_service: str,
        target_service: str,
        dependency_type: str = "sync",
        criticality: str = "medium",
    ) -> ServiceDependencyEdge:
        """
        서비스 의존성 추가

        Args:
            source_service: 원본 서비스
            target_service: 대상 서비스
            dependency_type: 의존성 유형 (sync, async, weak)
            criticality: 중요도 (low, medium, high, critical)

        Returns:
            ServiceDependencyEdge: 생성된 의존성
        """
        dependency = ServiceDependencyEdge(
            source_service=source_service,
            target_service=target_service,
            dependency_type=dependency_type,
            criticality=criticality,
        )
        self._dependencies.append(dependency)

        # === Audit 기록: 의존성 추가 (85_AUDIT_INTEGRATION Phase 1) ===
        log_blast_radius_audit(
            experiment_id=f"dep-{source_service}-{target_service}",
            blast_radius=criticality,
            target_service=target_service,
            action="add_dependency",
            allowed=True,
            target_domain=source_service,
            reason=f"Dependency added: {source_service} -> {target_service} ({dependency_type})",
        )

        return dependency

    def get_dependencies(self, service: str) -> dict[str, list[ServiceDependencyEdge]]:
        """
        서비스 의존성 조회

        Args:
            service: 서비스 이름

        Returns:
            Dict: upstream과 downstream 의존성
        """
        upstream = [d for d in self._dependencies if d.target_service == service]
        downstream = [d for d in self._dependencies if d.source_service == service]

        return {
            "upstream": upstream,
            "downstream": downstream,
        }

    def assess_impact(
        self,
        stage_name: str,
        trigger_event: str,
        failing_services: list[str],
        total_users: int = 1000,
    ) -> ImpactAssessment:
        """
        영향 평가 수행

        Args:
            stage_name: Stage 이름
            trigger_event: 트리거 이벤트
            failing_services: 장애 발생 서비스 목록
            total_users: 전체 사용자 수 (추정용)

        Returns:
            ImpactAssessment: 영향 평가 결과
        """
        assessment_id = str(uuid.uuid4())[:8]

        # 연쇄 영향 분석
        all_affected, dependencies_analyzed = self._analyze_cascading_impact(failing_services)

        # 영향 수준 결정
        level = self._determine_blast_radius_level(len(all_affected))

        # 영향 비율 계산
        affected_percentage = self._calculate_affected_percentage(len(all_affected))

        # 연쇄 리스크 체크
        cascading_risk = self._check_cascading_risk(all_affected)

        # 추천사항 생성
        recommendations = self._generate_recommendations(level, cascading_risk, all_affected)

        assessment = self._create_assessment(
            assessment_id=assessment_id,
            stage_name=stage_name,
            trigger_event=trigger_event,
            level=level,
            all_affected=all_affected,
            total_users=total_users,
            affected_percentage=affected_percentage,
            dependencies_analyzed=dependencies_analyzed,
            cascading_risk=cascading_risk,
            recommendations=recommendations,
        )

        self._assessments.append(assessment)
        logger.info(
            "impact_assessed_services",
            assessment_id=assessment_id,
            blast_radius_level=level.value,
            all_affected_count=len(all_affected),
        )

        # 자동 격리 체크
        self._check_and_auto_isolate(stage_name, level, failing_services)

        return assessment

    def _analyze_cascading_impact(self, failing_services: list[str]) -> tuple[set[str], int]:
        """
        연쇄 영향 분석

        Args:
            failing_services: 장애 발생 서비스 목록

        Returns:
            tuple: (영향받는 서비스 집합, 분석된 의존성 수)
        """
        all_affected = set(failing_services)
        to_check = list(failing_services)
        dependencies_analyzed = 0

        while to_check:
            service = to_check.pop(0)
            deps = self.get_dependencies(service)
            dependencies_analyzed += len(deps["upstream"]) + len(deps["downstream"])

            for dep in deps["upstream"]:
                if dep.source_service not in all_affected:
                    all_affected.add(dep.source_service)
                    if dep.dependency_type == "sync" and dep.criticality in [
                        "high",
                        "critical",
                    ]:
                        to_check.append(dep.source_service)

        return all_affected, dependencies_analyzed

    def _determine_blast_radius_level(self, affected_count: int) -> BlastRadiusLevel:
        """
        영향 수준 결정

        Args:
            affected_count: 영향받는 서비스 수

        Returns:
            BlastRadiusLevel: 영향 수준
        """
        if affected_count <= 1:
            return BlastRadiusLevel.MINIMAL
        elif affected_count <= 3:
            return BlastRadiusLevel.CONTAINED
        elif affected_count <= 5:
            return BlastRadiusLevel.MODERATE
        elif affected_count <= 10:
            return BlastRadiusLevel.EXTENSIVE
        return BlastRadiusLevel.CRITICAL

    def _calculate_affected_percentage(self, affected_count: int) -> float:
        """
        영향 비율 계산

        Args:
            affected_count: 영향받는 서비스 수

        Returns:
            float: 영향 비율
        """
        total_services = max(len(self._get_all_services()), 1)
        return (affected_count / total_services) * 100

    def _check_cascading_risk(self, all_affected: set[str]) -> bool:
        """
        연쇄 리스크 체크

        Args:
            all_affected: 영향받는 서비스 집합

        Returns:
            bool: 연쇄 리스크 여부
        """
        return any(
            d.dependency_type == "sync" and d.criticality == "critical"
            for d in self._dependencies
            if d.source_service in all_affected or d.target_service in all_affected
        )

    def _create_assessment(
        self,
        assessment_id: str,
        stage_name: str,
        trigger_event: str,
        level: BlastRadiusLevel,
        all_affected: set[str],
        total_users: int,
        affected_percentage: float,
        dependencies_analyzed: int,
        cascading_risk: bool,
        recommendations: list[str],
    ) -> ImpactAssessment:
        """ImpactAssessment 객체 생성"""
        return ImpactAssessment(
            assessment_id=assessment_id,
            stage_name=stage_name,
            trigger_event=trigger_event,
            level=level,
            affected_services=list(all_affected),
            affected_users_estimate=int(total_users * affected_percentage / 100),
            affected_percentage=affected_percentage,
            dependencies_analyzed=dependencies_analyzed,
            cascading_risk=cascading_risk,
            recommendations=recommendations,
        )

    def _check_and_auto_isolate(
        self,
        stage_name: str,
        level: BlastRadiusLevel,
        failing_services: list[str],
    ) -> None:
        """자동 격리 조건 체크 및 실행"""
        policy = self._policies.get(stage_name)
        if policy and policy.auto_isolate and level.value in ["extensive", "critical"]:
            self._auto_isolate(failing_services)

    def _get_all_services(self) -> set[str]:
        """모든 서비스 목록"""
        services = set()
        for dep in self._dependencies:
            services.add(dep.source_service)
            services.add(dep.target_service)
        for policy in self._policies.values():
            services.update(policy.affected_services)
        return services

    def _generate_recommendations(
        self,
        level: BlastRadiusLevel,
        cascading_risk: bool,
        affected_services: set[str],
    ) -> list[str]:
        """추천사항 생성"""
        recommendations = []

        if level in [BlastRadiusLevel.EXTENSIVE, BlastRadiusLevel.CRITICAL]:
            recommendations.append("즉시 장애 대응 팀에 에스컬레이션 필요")
            recommendations.append("영향받는 서비스에 대한 긴급 롤백 고려")

        if cascading_risk:
            recommendations.append("연쇄 장애 위험: 비동기 통신으로 전환 권장")
            recommendations.append("Circuit Breaker 설정 강화 필요")

        if len(affected_services) > 3:
            recommendations.append("영향 범위 격리를 위한 서비스 분리 검토")

        if not recommendations:
            recommendations.append("현재 영향 범위 내 제어 가능")

        return recommendations

    def _auto_isolate(self, services: list[str]) -> None:
        """자동 격리 실행"""
        for service in services:
            if service not in self._isolated_services:
                self._isolated_services.add(service)
                logger.warning(
                    "service_auto_isolated",
                    target_service=service,
                )

                # === Audit 기록: 자동 격리 (85_AUDIT_INTEGRATION Phase 1) ===
                log_blast_radius_audit(
                    experiment_id=f"auto-isolate-{service}",
                    blast_radius="auto",
                    target_service=service,
                    action="auto_isolate",
                    allowed=True,
                    reason="Auto-isolation triggered by impact assessment",
                )

    def isolate_service(self, service: str) -> bool:
        """수동 격리"""
        if service not in self._isolated_services:
            self._isolated_services.add(service)
            logger.info(
                "service_isolated",
                target_service=service,
            )

            # === Audit 기록: 수동 격리 (85_AUDIT_INTEGRATION Phase 1) ===
            log_blast_radius_audit(
                experiment_id=f"manual-isolate-{service}",
                blast_radius="manual",
                target_service=service,
                action="isolate_service",
                allowed=True,
                reason="Manual service isolation",
            )

            return True
        return False

    def release_isolation(self, service: str) -> bool:
        """격리 해제"""
        if service in self._isolated_services:
            self._isolated_services.discard(service)
            logger.info(
                "service_isolation_released",
                target_service=service,
            )

            # === Audit 기록: 격리 해제 (85_AUDIT_INTEGRATION Phase 1) ===
            log_blast_radius_audit(
                experiment_id=f"release-{service}",
                blast_radius="released",
                target_service=service,
                action="release_isolation",
                allowed=True,
                reason="Service isolation released",
            )

            return True
        return False

    def is_isolated(self, service: str) -> bool:
        """격리 상태 확인"""
        return service in self._isolated_services

    def get_isolated_services(self) -> list[str]:
        """격리된 서비스 목록"""
        return list(self._isolated_services)

    def get_assessments(
        self,
        stage_name: str | None = None,
        min_level: BlastRadiusLevel | None = None,
        limit: int = 100,
    ) -> list[ImpactAssessment]:
        """
        영향 평가 이력 조회

        Args:
            stage_name: Stage 이름 필터
            min_level: 최소 영향 수준
            limit: 최대 개수

        Returns:
            List[ImpactAssessment]: 평가 목록
        """
        assessments = self._assessments[-limit:]

        if stage_name:
            assessments = [a for a in assessments if a.stage_name == stage_name]

        if min_level:
            level_order = [l.value for l in BlastRadiusLevel]
            min_index = level_order.index(min_level.value)
            assessments = [a for a in assessments if level_order.index(a.level.value) >= min_index]

        return assessments

    def build_dependency_graph(self) -> dict:
        """
        의존성 그래프 생성

        Returns:
            Dict: 그래프 데이터
        """
        nodes = set()
        edges = []

        for dep in self._dependencies:
            nodes.add(dep.source_service)
            nodes.add(dep.target_service)
            edges.append(
                {
                    "source": dep.source_service,
                    "target": dep.target_service,
                    "type": dep.dependency_type,
                    "criticality": dep.criticality,
                }
            )

        return {
            "nodes": list(nodes),
            "edges": edges,
            "isolated": list(self._isolated_services),
        }

    def enable(self) -> None:
        """서비스 활성화"""
        self._enabled = True

    def disable(self) -> None:
        """서비스 비활성화"""
        self._enabled = False

    def clear(self) -> None:
        """모든 데이터 초기화 (테스트용)"""
        self._policies.clear()
        self._dependencies.clear()
        self._assessments.clear()
        self._isolated_services.clear()
