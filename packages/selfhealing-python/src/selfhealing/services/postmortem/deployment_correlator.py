"""
Deployment Correlator.

인시던트 발생 시점 전후의 배포 이력을 수집하여
Postmortem에 포함시켜 장애 원인 분석 정확도를 높입니다.

주요 기능:
- correlate_incident: 인시던트에 배포 정보 연결
- get_deployments_for_postmortem: Postmortem용 배포 데이터 수집
- analyze_correlation: 배포-인시던트 상관관계 분석

상관관계 유형:
- deployment_triggered: 배포 후 30분 내 인시던트 (높음)
- config_changed: 설정 변경 후 10분 내 (높음)
- possible_correlation: 배포 후 1시간 내 (중간)
- unlikely: 배포 없음 또는 2시간 이상 (낮음)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class CorrelationType(str, Enum):
    """배포-인시던트 상관관계 유형."""

    DEPLOYMENT_TRIGGERED = "deployment_triggered"
    """배포 후 30분 내 인시던트 발생 - 높은 상관관계."""

    CONFIG_CHANGED = "config_changed"
    """설정 변경 후 10분 내 인시던트 발생 - 높은 상관관계."""

    POSSIBLE_CORRELATION = "possible_correlation"
    """배포 후 1시간 내 인시던트 발생 - 중간 상관관계."""

    UNLIKELY = "unlikely"
    """배포 없음 또는 2시간 이상 경과 - 낮은 상관관계."""


@dataclass
class DeploymentCorrelationResult:
    """
    배포-인시던트 상관관계 분석 결과.

    Attributes:
        deployments: 관련 배포 목록
        config_changes: 관련 설정 변경 목록
        correlation_score: 상관관계 점수 (0-1)
        correlation_type: 상관관계 유형
        analysis_summary: 분석 요약
    """

    deployments: list[dict[str, Any]] = field(default_factory=list)
    """관련 배포 목록."""

    config_changes: list[dict[str, Any]] = field(default_factory=list)
    """관련 설정 변경 목록."""

    correlation_score: float = 0.0
    """상관관계 점수 (0.0 ~ 1.0)."""

    correlation_type: CorrelationType = CorrelationType.UNLIKELY
    """상관관계 유형."""

    analysis_summary: str = ""
    """분석 요약 메시지."""

    closest_deployment_minutes: float | None = None
    """가장 가까운 배포까지의 시간 (분)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "deployments": self.deployments,
            "config_changes": self.config_changes,
            "correlation_score": self.correlation_score,
            "correlation_type": self.correlation_type.value,
            "analysis_summary": self.analysis_summary,
            "closest_deployment_minutes": self.closest_deployment_minutes,
        }


class DeploymentCorrelator:
    """
    배포 연관성 분석기.

    인시던트 발생 시점 전후의 배포 이력을 수집하고
    상관관계를 분석하여 Postmortem에 컨텍스트를 제공합니다.

    설정:
        deployment_correlator_enabled: 기능 활성화 (기본: True)
        deployment_adapter: 어댑터 선택 (mock/kubernetes)
        deployment_pre_window_minutes: 사전 조회 범위 (기본: 60분)
        deployment_post_window_minutes: 사후 조회 범위 (기본: 30분)

    Example:
        >>> correlator = DeploymentCorrelator()
        >>> result = correlator.correlate_incident(
        ...     incident_time=datetime.now(),
        ...     service_name="payment-service",
        ...     namespace="production"
        ... )
        >>> print(result.correlation_type)
    """

    # 상관관계 판정 기준 (분)
    HIGH_CORRELATION_THRESHOLD_MINUTES = 30
    """배포 후 30분 내 인시던트: 높은 상관관계."""

    CONFIG_CHANGE_THRESHOLD_MINUTES = 10
    """설정 변경 후 10분 내 인시던트: 높은 상관관계."""

    MEDIUM_CORRELATION_THRESHOLD_MINUTES = 60
    """배포 후 1시간 내 인시던트: 중간 상관관계."""

    def __init__(self, adapter=None):
        """
        DeploymentCorrelator 초기화.

        Args:
            adapter: 배포 어댑터 인스턴스 (None이면 설정에 따라 자동 생성)
        """
        self._adapter = adapter
        self._settings = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """어댑터와 설정을 지연 초기화합니다."""
        if self._initialized:
            return

        self._settings = self._get_settings()

        if self._adapter is None:
            self._adapter = self._create_adapter()

        self._initialized = True

    def _get_settings(self) -> Any:
        """Settings 객체를 가져옵니다."""
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            return get_postmortem_settings()
        except ImportError:
            return None

    def _create_adapter(self):
        """설정에 따라 어댑터를 생성합니다."""
        adapter_type = "mock"
        if self._settings and hasattr(self._settings, "deployment_adapter"):
            adapter_type = self._settings.deployment_adapter

        if adapter_type == "kubernetes":
            try:
                from selfhealing.adapters.deployment import (
                    KubernetesDeploymentAdapter,
                )

                adapter = KubernetesDeploymentAdapter()
                if adapter.is_available():
                    logger.info("deployment_correlator.using_kubernetes_adapter")
                    return adapter
                else:
                    logger.warning("deployment_correlator.kubernetes_available_falling_back")
            except Exception as e:
                logger.warning(
                    "deployment_correlator.kubernetes_adapter_failed_falling",
                    error=e,
                )

        # Mock 어댑터로 폴백
        from selfhealing.adapters.deployment import MockDeploymentAdapter

        logger.info("deployment_correlator.using_mock_adapter")
        return MockDeploymentAdapter()

    def is_enabled(self) -> bool:
        """기능이 활성화되어 있는지 확인합니다."""
        self._ensure_initialized()

        if self._settings and hasattr(self._settings, "deployment_correlator_enabled"):
            return self._settings.deployment_correlator_enabled
        return True  # 기본값: 활성화

    def correlate_incident(
        self,
        incident_time: datetime,
        service_name: str,
        namespace: str = "default",
    ) -> DeploymentCorrelationResult:
        """
        인시던트에 배포 정보를 연결하고 상관관계를 분석합니다.

        Args:
            incident_time: 인시던트 발생 시각
            service_name: 영향받은 서비스
            namespace: 네임스페이스

        Returns:
            DeploymentCorrelationResult: 상관관계 분석 결과
        """
        if not self.is_enabled():
            logger.debug("deployment_correlator.feature_disabled")
            return DeploymentCorrelationResult(analysis_summary="Deployment correlation feature disabled")

        self._ensure_initialized()

        # 시간 윈도우 설정
        pre_window = self._get_pre_window_minutes()
        post_window = self._get_post_window_minutes()

        start_time = incident_time - timedelta(minutes=pre_window)
        end_time = incident_time + timedelta(minutes=post_window)

        # 배포 이력 조회
        deployments = self._get_deployments_safe(service_name, start_time, end_time, namespace)

        # 설정 변경 이력 조회
        config_changes = self._get_config_changes_safe(service_name, start_time, end_time, namespace)

        # 상관관계 분석
        result = self._analyze_correlation(
            incident_time=incident_time,
            deployments=deployments,
            config_changes=config_changes,
        )

        logger.info(
            "deployment_correlator.analyzed_deployments_config_changes",
            service_name=service_name,
            count=len(deployments),
            count_2=len(config_changes),
            correlation_type=result.correlation_type.value,
        )

        return result

    def get_deployments_for_postmortem(
        self,
        incident_time: datetime,
        service_name: str,
        namespace: str = "default",
    ) -> dict[str, Any]:
        """
        Postmortem용 배포 컨텍스트를 수집합니다.

        Args:
            incident_time: 인시던트 발생 시각
            service_name: 영향받은 서비스
            namespace: 네임스페이스

        Returns:
            deployment_context 딕셔너리
        """
        if not self.is_enabled():
            return {"status": "disabled"}

        try:
            result = self.correlate_incident(
                incident_time=incident_time,
                service_name=service_name,
                namespace=namespace,
            )

            return {
                "status": "collected",
                "recent_deployments": result.deployments,
                "config_changes": result.config_changes,
                "deployment_correlation": result.correlation_type.value,
                "correlation_score": result.correlation_score,
                "analysis_summary": result.analysis_summary,
            }

        except Exception as e:
            logger.warning(
                "deployment_correlator.failed_collect_deployment_context",
                error=e,
            )
            return {
                "status": "error",
                "error": str(e),
            }

    def get_deployment_timeline_events(
        self,
        incident_time: datetime,
        service_name: str,
        namespace: str = "default",
    ) -> list[dict[str, Any]]:
        """
        타임라인에 삽입할 배포/설정 변경 이벤트를 생성합니다.

        Args:
            incident_time: 인시던트 발생 시각
            service_name: 영향받은 서비스
            namespace: 네임스페이스

        Returns:
            타임라인 이벤트 목록
        """
        if not self.is_enabled():
            return []

        self._ensure_initialized()

        pre_window = self._get_pre_window_minutes()
        post_window = self._get_post_window_minutes()

        start_time = incident_time - timedelta(minutes=pre_window)
        end_time = incident_time + timedelta(minutes=post_window)

        events = []

        # 배포 이벤트 변환
        deployments = self._get_deployments_safe(service_name, start_time, end_time, namespace)
        for deploy in deployments:
            events.append(deploy.to_timeline_event())

        # 설정 변경 이벤트 변환
        config_changes = self._get_config_changes_safe(service_name, start_time, end_time, namespace)
        for change in config_changes:
            events.append(change.to_timeline_event())

        # 시간순 정렬
        events.sort(key=lambda e: e.get("timestamp", ""))

        return events

    # =========================================================================
    # 내부 메서드
    # =========================================================================

    def _get_pre_window_minutes(self) -> int:
        """사전 조회 윈도우 (분)를 반환합니다."""
        if self._settings and hasattr(self._settings, "deployment_pre_window_minutes"):
            return self._settings.deployment_pre_window_minutes
        return 60  # 기본값

    def _get_post_window_minutes(self) -> int:
        """사후 조회 윈도우 (분)를 반환합니다."""
        if self._settings and hasattr(self._settings, "deployment_post_window_minutes"):
            return self._settings.deployment_post_window_minutes
        return 30  # 기본값

    def _get_deployments_safe(
        self,
        service_name: str,
        start_time: datetime,
        end_time: datetime,
        namespace: str,
    ) -> list:
        """안전하게 배포 이력을 조회합니다."""
        try:
            if self._adapter and hasattr(self._adapter, "get_deployments_in_range"):
                return self._adapter.get_deployments_in_range(
                    service_name=service_name,
                    start_time=start_time,
                    end_time=end_time,
                    namespace=namespace,
                )
        except Exception as e:
            logger.warning(
                "deployment_correlator.failed_get_deployments",
                error=e,
            )
        return []

    def _get_config_changes_safe(
        self,
        service_name: str,
        start_time: datetime,
        end_time: datetime,
        namespace: str,
    ) -> list:
        """안전하게 설정 변경 이력을 조회합니다."""
        try:
            if self._adapter and hasattr(self._adapter, "get_config_changes_in_range"):
                return self._adapter.get_config_changes_in_range(
                    service_name=service_name,
                    start_time=start_time,
                    end_time=end_time,
                    namespace=namespace,
                )
        except Exception as e:
            logger.warning(
                "deployment_correlator.failed_get_config_changes",
                error=e,
            )
        return []

    def _find_closest_deployment(
        self,
        incident_time: datetime,
        deployments: list,
    ) -> tuple[float | None, Any]:
        """가장 가까운 배포 시간 계산."""
        closest_minutes: float | None = None
        closest_deploy = None

        for deploy in deployments:
            try:
                deploy_time = datetime.fromisoformat(deploy.deployed_at.replace("Z", "+00:00"))
                minutes_diff = abs((incident_time - deploy_time).total_seconds() / 60)
                if closest_minutes is None or minutes_diff < closest_minutes:
                    closest_minutes = minutes_diff
                    closest_deploy = deploy
            except (ValueError, TypeError):
                continue

        return closest_minutes, closest_deploy

    def _find_closest_config_change(
        self,
        incident_time: datetime,
        config_changes: list,
    ) -> tuple[float | None, Any]:
        """가장 가까운 설정 변경 시간 계산."""
        closest_config_minutes: float | None = None
        closest_config = None

        for change in config_changes:
            try:
                change_time = datetime.fromisoformat(change.changed_at.replace("Z", "+00:00"))
                minutes_diff = abs((incident_time - change_time).total_seconds() / 60)
                if closest_config_minutes is None or minutes_diff < closest_config_minutes:
                    closest_config_minutes = minutes_diff
                    closest_config = change
            except (ValueError, TypeError):
                continue

        return closest_config_minutes, closest_config

    def _determine_correlation(
        self,
        result: DeploymentCorrelationResult,
        closest_minutes: float | None,
        closest_deploy: Any,
        closest_config_minutes: float | None,
        closest_config: Any,
    ) -> None:
        """상관관계 판정."""
        if closest_config_minutes is not None and closest_config_minutes <= self.CONFIG_CHANGE_THRESHOLD_MINUTES:
            result.correlation_type = CorrelationType.CONFIG_CHANGED
            result.correlation_score = 0.9
            result.analysis_summary = (
                f"Config change '{closest_config.config_key}' occurred "
                f"{closest_config_minutes:.1f} minutes before incident"
            )
        elif closest_minutes is not None and closest_minutes <= self.HIGH_CORRELATION_THRESHOLD_MINUTES:
            result.correlation_type = CorrelationType.DEPLOYMENT_TRIGGERED
            result.correlation_score = 0.85
            version_info = f"{closest_deploy.version_from} → {closest_deploy.version_to}" if closest_deploy else "unknown"
            result.analysis_summary = f"Deployment ({version_info}) occurred {closest_minutes:.1f} minutes before incident"
        elif closest_minutes is not None and closest_minutes <= self.MEDIUM_CORRELATION_THRESHOLD_MINUTES:
            result.correlation_type = CorrelationType.POSSIBLE_CORRELATION
            result.correlation_score = 0.5
            result.analysis_summary = (
                f"Deployment occurred {closest_minutes:.1f} minutes before incident - possible correlation"
            )
        else:
            result.correlation_type = CorrelationType.UNLIKELY
            result.correlation_score = 0.1
            result.analysis_summary = "Deployments found but not closely correlated with incident time"

    def _analyze_correlation(
        self,
        incident_time: datetime,
        deployments: list,
        config_changes: list,
    ) -> DeploymentCorrelationResult:
        """배포-인시던트 상관관계를 분석합니다."""
        deployment_dicts = [d.to_dict() for d in deployments]
        config_change_dicts = [c.to_dict() for c in config_changes]

        result = DeploymentCorrelationResult(
            deployments=deployment_dicts,
            config_changes=config_change_dicts,
        )

        # 배포 없으면 낮은 상관관계
        if not deployments and not config_changes:
            result.correlation_type = CorrelationType.UNLIKELY
            result.correlation_score = 0.0
            result.analysis_summary = "No deployments or config changes found near incident time"
            return result

        # 가장 가까운 배포/설정 변경 시간 계산
        closest_minutes, closest_deploy = self._find_closest_deployment(incident_time, deployments)
        closest_config_minutes, closest_config = self._find_closest_config_change(incident_time, config_changes)

        result.closest_deployment_minutes = closest_minutes

        # 상관관계 판정
        self._determine_correlation(result, closest_minutes, closest_deploy, closest_config_minutes, closest_config)

        return result


# =============================================================================
# 싱글톤 관리
# =============================================================================

_deployment_correlator: DeploymentCorrelator | None = None


def get_deployment_correlator() -> DeploymentCorrelator:
    """캐시된 DeploymentCorrelator 인스턴스를 반환합니다."""
    global _deployment_correlator
    if _deployment_correlator is None:
        _deployment_correlator = DeploymentCorrelator()
    return _deployment_correlator


def reset_deployment_correlator() -> None:
    """캐시된 인스턴스를 리셋합니다 (테스트용)."""
    global _deployment_correlator
    _deployment_correlator = None
