"""
Kubernetes Deployment Adapter.

Kubernetes API를 통해 Deployment 이력을 수집하는 어댑터입니다.

필요 RBAC 권한:
- deployments: get, list
- replicasets: get, list
- events: get, list
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from .base import (
    DeploymentConfigChange,
    DeploymentEvent,
    DeploymentSource,
    DeploymentType,
)

logger = structlog.get_logger()


class KubernetesDeploymentAdapter:
    """
    Kubernetes API 연동 배포 어댑터.

    Kubernetes의 apps/v1 Deployment API를 사용하여
    배포 이력 및 롤백 정보를 수집합니다.

    필수 권한 (RBAC):
        - deployments: get, list
        - replicasets: get, list
        - events: get, list (선택)

    설정:
        DEPLOYMENT_ADAPTER=kubernetes

    Example:
        >>> adapter = KubernetesDeploymentAdapter()
        >>> if adapter.is_available():
        ...     deployments = adapter.get_deployments_in_range(
        ...         service_name="payment-service",
        ...         start_time=datetime(2025, 1, 1, 10, 0),
        ...         end_time=datetime(2025, 1, 1, 12, 0)
        ...     )
    """

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        in_cluster: bool = True,
        timeout: int = 10,
    ):
        """
        Kubernetes 어댑터 초기화.

        Args:
            kubeconfig_path: kubeconfig 파일 경로 (None이면 기본 경로)
            in_cluster: 클러스터 내부에서 실행 중인지 여부
            timeout: API 요청 타임아웃 (초)
        """
        self._kubeconfig_path = kubeconfig_path
        self._in_cluster = in_cluster
        self._timeout = timeout
        self._client: Any = None
        self._apps_v1: Any = None
        self._core_v1: Any = None
        self._is_available = False

        self._initialize_client()

    def _initialize_client(self) -> None:
        """Kubernetes 클라이언트를 초기화합니다."""
        try:
            from kubernetes import client, config

            if self._in_cluster:
                try:
                    config.load_incluster_config()
                    logger.info("kubernetes_adapter.loaded_cluster_config")
                except config.ConfigException:
                    # In-cluster 실패 시 kubeconfig 시도
                    config.load_kube_config(config_file=self._kubeconfig_path)
                    logger.info("kubernetes_adapter.loaded_kubeconfig")
            else:
                config.load_kube_config(config_file=self._kubeconfig_path)
                logger.info("kubernetes_adapter.loaded_kubeconfig")

            self._apps_v1 = client.AppsV1Api()
            self._core_v1 = client.CoreV1Api()
            self._is_available = True

        except ImportError:
            logger.warning("kubernetes_adapter.kubernetes_package_installed_install")
            self._is_available = False
        except Exception as e:
            logger.warning(
                "kubernetes_adapter.failed_initialize",
                error=e,
            )
            self._is_available = False

    def get_deployments_in_range(
        self,
        service_name: str,
        start_time: datetime,
        end_time: datetime,
        namespace: str = "default",
    ) -> list[DeploymentEvent]:
        """
        지정된 시간 범위 내의 배포 이력을 조회합니다.

        ReplicaSet 이력을 통해 배포 버전을 추적합니다.

        Args:
            service_name: 서비스 이름 (Deployment 이름)
            start_time: 조회 시작 시각
            end_time: 조회 종료 시각
            namespace: 네임스페이스

        Returns:
            배포 이벤트 목록 (시간순 정렬)
        """
        if not self._is_available or not self._apps_v1:
            logger.warning("kubernetes_adapter.client_available")
            return []

        try:
            # ReplicaSet 목록 조회 (Deployment의 배포 이력)
            label_selector = f"app={service_name}"
            replicasets = self._apps_v1.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=label_selector,
                _request_timeout=self._timeout,
            )

            deployments: list[DeploymentEvent] = []
            previous_version: str | None = None

            # 생성 시간순 정렬
            sorted_rs = sorted(
                replicasets.items,
                key=lambda rs: rs.metadata.creation_timestamp,
            )

            for rs in sorted_rs:
                created_at = rs.metadata.creation_timestamp
                if not created_at:
                    continue

                # 시간 범위 필터
                if not (start_time <= created_at <= end_time):
                    # 이전 버전 추적을 위해 저장
                    if created_at < start_time:
                        previous_version = self._extract_version(rs)
                    continue

                version_to = self._extract_version(rs)
                version_from = previous_version or "unknown"

                # 롤백 여부 확인
                is_rollback = self._check_if_rollback(rs)

                deployment = DeploymentEvent(
                    deployment_id=rs.metadata.uid or f"rs-{rs.metadata.name}",
                    service_name=service_name,
                    version_from=version_from,
                    version_to=version_to,
                    deployed_at=created_at.isoformat(),
                    deployed_by=self._extract_deployer(rs),
                    deployment_type=self._extract_deployment_type(rs),
                    source=DeploymentSource.KUBERNETES,
                    namespace=namespace,
                    is_rollback=is_rollback,
                    metadata={
                        "replicaset_name": rs.metadata.name,
                        "revision": (
                            rs.metadata.annotations.get("deployment.kubernetes.io/revision", "unknown")
                            if rs.metadata.annotations
                            else "unknown"
                        ),
                    },
                )

                deployments.append(deployment)
                previous_version = version_to

            logger.debug(
                "kubernetes_adapter.found_deployments",
                deployments_count=len(deployments),
                service_name=service_name,
                namespace=namespace,
            )
            return deployments

        except Exception as e:
            logger.warning(
                "kubernetes_adapter.failed_get_deployments",
                error=e,
            )
            return []

    def get_deployment_by_version(
        self,
        service_name: str,
        version: str,
        namespace: str = "default",
    ) -> DeploymentEvent | None:
        """
        특정 버전의 배포 상세 정보를 조회합니다.

        Args:
            service_name: 서비스 이름
            version: 배포 버전
            namespace: 네임스페이스

        Returns:
            배포 이벤트 또는 None
        """
        if not self._is_available or not self._apps_v1:
            return None

        try:
            label_selector = f"app={service_name},version={version}"
            replicasets = self._apps_v1.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=label_selector,
                _request_timeout=self._timeout,
            )

            if replicasets.items:
                rs = replicasets.items[0]
                return DeploymentEvent(
                    deployment_id=rs.metadata.uid or f"rs-{rs.metadata.name}",
                    service_name=service_name,
                    version_from="unknown",
                    version_to=version,
                    deployed_at=rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else "",
                    deployed_by=self._extract_deployer(rs),
                    deployment_type=self._extract_deployment_type(rs),
                    source=DeploymentSource.KUBERNETES,
                    namespace=namespace,
                )

            return None

        except Exception as e:
            logger.warning(
                "kubernetes_adapter.failed_get_deployment_version",
                error=e,
            )
            return None

    def get_current_version(
        self,
        service_name: str,
        namespace: str = "default",
    ) -> str | None:
        """
        서비스의 현재 배포 버전을 조회합니다.

        Args:
            service_name: 서비스 이름 (Deployment 이름)
            namespace: 네임스페이스

        Returns:
            현재 버전 문자열 또는 None
        """
        if not self._is_available or not self._apps_v1:
            return None

        try:
            deployment = self._apps_v1.read_namespaced_deployment(
                name=service_name,
                namespace=namespace,
                _request_timeout=self._timeout,
            )

            # version 레이블에서 추출
            if deployment.spec.template.metadata.labels:
                version = deployment.spec.template.metadata.labels.get("version")
                if version:
                    return version

            # image 태그에서 추출
            containers = deployment.spec.template.spec.containers
            if containers:
                image = containers[0].image
                if ":" in image:
                    return image.split(":")[-1]

            return None

        except Exception as e:
            logger.warning(
                "kubernetes_adapter.failed_get_current_version",
                error=e,
            )
            return None

    def get_rollback_history(
        self,
        service_name: str,
        namespace: str = "default",
        limit: int = 10,
    ) -> list[DeploymentEvent]:
        """
        롤백 이력을 조회합니다.

        Args:
            service_name: 서비스 이름
            namespace: 네임스페이스
            limit: 최대 조회 개수

        Returns:
            롤백 이벤트 목록 (최신순 정렬)
        """
        if not self._is_available or not self._apps_v1:
            return []

        try:
            # 전체 배포 이력에서 롤백만 필터
            now = datetime.now(timezone.utc)
            # 최근 7일 이력 조회
            start = now.replace(hour=0, minute=0, second=0) - __import__("datetime").timedelta(days=7)

            all_deployments = self.get_deployments_in_range(
                service_name=service_name,
                start_time=start,
                end_time=now,
                namespace=namespace,
            )

            rollbacks = [d for d in all_deployments if d.is_rollback]

            # 최신순 정렬
            rollbacks.sort(key=lambda d: d.deployed_at, reverse=True)

            return rollbacks[:limit]

        except Exception as e:
            logger.warning(
                "kubernetes_adapter.failed_get_rollback_history",
                error=e,
            )
            return []

    def get_config_changes_in_range(
        self,
        service_name: str,
        start_time: datetime,
        end_time: datetime,
        namespace: str = "default",
    ) -> list[DeploymentConfigChange]:
        """
        지정된 시간 범위 내의 설정 변경 이력을 조회합니다.

        ConfigMap 변경 이벤트를 통해 수집합니다.

        Args:
            service_name: 서비스 이름
            start_time: 조회 시작 시각
            end_time: 조회 종료 시각
            namespace: 네임스페이스

        Returns:
            설정 변경 이벤트 목록 (시간순 정렬)

        Note:
            Kubernetes는 ConfigMap 변경 이력을 자체적으로 보관하지 않습니다.
            RuntimeConfig의 ConfigHistory 서비스를 통해 수집하는 것을 권장합니다.
        """
        # Kubernetes는 ConfigMap 변경 이력을 보관하지 않음
        # RuntimeConfig의 ConfigHistory를 통해 수집
        logger.debug("kubernetes_adapter.config_changes_collected_runtimeconfig")
        return []

    def is_available(self) -> bool:
        """
        어댑터가 사용 가능한지 확인합니다.

        Returns:
            사용 가능 여부
        """
        return self._is_available

    # =========================================================================
    # 헬퍼 메서드
    # =========================================================================

    def _extract_version(self, replicaset: Any) -> str:
        """ReplicaSet에서 버전 정보를 추출합니다."""
        # 1. labels에서 version 추출
        if replicaset.metadata.labels:
            version = replicaset.metadata.labels.get("version")
            if version:
                return version

        # 2. annotations에서 revision 추출
        if replicaset.metadata.annotations:
            revision = replicaset.metadata.annotations.get("deployment.kubernetes.io/revision")
            if revision:
                return f"rev-{revision}"

        # 3. Pod template의 container image 태그 추출
        if replicaset.spec.template.spec.containers:
            image = replicaset.spec.template.spec.containers[0].image
            if ":" in image:
                return image.split(":")[-1]

        return "unknown"

    def _extract_deployer(self, replicaset: Any) -> str:
        """배포자 정보를 추출합니다."""
        if replicaset.metadata.annotations:
            # kubectl에 의한 배포
            last_applied = replicaset.metadata.annotations.get("kubectl.kubernetes.io/last-applied-configuration")
            if last_applied:
                return "kubectl"

            # ArgoCD에 의한 배포
            if replicaset.metadata.annotations.get("argocd.argoproj.io/sync-wave"):
                return "argocd"

        return "system"

    def _extract_deployment_type(self, replicaset: Any) -> DeploymentType:
        """배포 유형을 추출합니다."""
        if replicaset.metadata.annotations:
            # Canary 배포 확인
            if replicaset.metadata.annotations.get("canary"):
                return DeploymentType.CANARY

        # 기본값: Rolling
        return DeploymentType.ROLLING

    def _check_if_rollback(self, replicaset: Any) -> bool:
        """롤백 배포인지 확인합니다."""
        if replicaset.metadata.annotations:
            # rollback annotation 확인
            if replicaset.metadata.annotations.get("deployment.kubernetes.io/rollback-to"):
                return True

            # revision 비교로 롤백 판단 (이전 revision으로 돌아간 경우)
            # 단순화를 위해 annotation만 확인

        return False
