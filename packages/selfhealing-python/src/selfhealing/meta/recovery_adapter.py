"""
Recovery Infrastructure Adapter.

환경별(Kubernetes/Docker/Local) 복구 전략 추상화.
Pod 재시작, Deployment 스케일링 등 인프라 레벨 복구 수행.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RecoveryAction(str, Enum):
    """복구 액션 유형."""

    RESTART_WORKER = "restart_worker"
    """워커 재시작."""

    SCALE_DEPLOYMENT = "scale_deployment"
    """Deployment 스케일 조정."""

    DELETE_POD = "delete_pod"
    """Pod 강제 삭제."""

    RESET_CONNECTION = "reset_connection"
    """연결 리셋."""


@dataclass
class RecoveryResult:
    """복구 결과."""

    action: RecoveryAction
    """수행한 액션."""

    success: bool
    """성공 여부."""

    target: str
    """대상 (Pod 이름, Deployment 이름 등)."""

    message: str
    """결과 메시지."""

    timestamp: datetime
    """수행 시각."""

    details: dict[str, Any] | None = None
    """추가 상세 정보."""


class RecoveryInfrastructureAdapter(ABC):
    """
    복구 인프라 어댑터 인터페이스.

    환경에 따라 다른 복구 전략을 사용합니다:
    - Kubernetes: Pod 삭제, Deployment scale
    - Docker Compose: Container restart
    - Local: Process signal

    입력 검증 정책:
    - 서비스 이름 화이트리스트 검증 (OS 명령어 인젝션 방지)
    - replicas 상한 제한 (리소스 고갈 DoS 방지)
    """

    # 보안: 서비스 이름에 허용되는 문자 (영문, 숫자, 하이픈, 밑줄, 점)
    _SAFE_NAME_PATTERN: re.Pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-.]{0,127}$")
    # 보안: 최대 허용 replica 수
    _MAX_REPLICAS: int = 50

    def _validate_service_name(self, name: str) -> None:
        """
        서비스 이름 검증 — OS 명령어 인젝션 방지.

        허용 규칙:
        - 영문, 숫자, 하이픈, 밑줄, 점만 허용
        - 최대 128자
        - 영문/숫자로 시작해야 함

        Raises:
            ValueError: 유효하지 않은 서비스 이름
        """
        if not name or not self._SAFE_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid service name: {name!r}. "
                "Must start with alphanumeric, contain only [a-zA-Z0-9_\\-.], "
                "and be 1-128 characters long."
            )

    def _validate_replicas(self, replicas: int) -> None:
        """
        replica 수 범위 검증 — 리소스 고갈 DoS 방지.

        허용 범위: 0 <= replicas <= _MAX_REPLICAS (기본 50)

        Raises:
            ValueError: 범위 벗어난 replicas
        """
        if not isinstance(replicas, int) or replicas < 0 or replicas > self._MAX_REPLICAS:
            raise ValueError(f"Invalid replicas count: {replicas}. " f"Must be integer between 0 and {self._MAX_REPLICAS}.")

    @abstractmethod
    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """
        워커 재시작.

        Args:
            worker_name: 워커 이름

        Returns:
            RecoveryResult
        """
        pass

    @abstractmethod
    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """
        Deployment 스케일 조정.

        Args:
            name: Deployment 이름
            replicas: 목표 replica 수

        Returns:
            RecoveryResult
        """
        pass

    @abstractmethod
    def delete_pod(self, pod_name: str, namespace: str) -> RecoveryResult:
        """
        Pod 강제 삭제.

        Args:
            pod_name: Pod 이름
            namespace: 네임스페이스

        Returns:
            RecoveryResult
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        어댑터 사용 가능 여부.

        Returns:
            사용 가능 여부
        """
        pass


class KubernetesRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    Kubernetes 복구 어댑터.

    K8s API를 통해 Pod/Deployment 복구를 수행합니다.
    RBAC 권한 필요: selfhealing-watchdog ServiceAccount
    """

    def __init__(self, namespace: str = "selfhealing"):
        """
        초기화.

        Args:
            namespace: 기본 네임스페이스
        """
        self._namespace = namespace
        self._apps_v1: Any = None
        self._core_v1: Any = None
        self._is_available = False
        self._resource_kind_cache: dict[str, str] = {}
        self._initialize_client()

    def _initialize_client(self) -> None:
        """K8s 클라이언트 초기화."""
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
                logger.info("[KubernetesRecoveryAdapter] Loaded in-cluster config")
            except config.ConfigException:
                config.load_kube_config()
                logger.info("[KubernetesRecoveryAdapter] Loaded kubeconfig")

            self._apps_v1 = client.AppsV1Api()
            self._core_v1 = client.CoreV1Api()
            self._is_available = True
        except ImportError:
            logger.warning("[KubernetesRecoveryAdapter] kubernetes package not installed")
        except Exception as e:
            logger.warning(f"[KubernetesRecoveryAdapter] Init failed: {e}")

    def is_available(self) -> bool:
        return self._is_available

    def _detect_resource_kind(self, name: str) -> str:
        """
        워크로드 리소스 Kind를 감지하고 캐싱.

        탐색 체인: Deployment → StatefulSet
        최초 1회 API 호출 후 캐싱하여 이후 호출에서는 API 미호출.

        Args:
            name: 워크로드 이름

        Returns:
            "Deployment" 또는 "StatefulSet"

        Raises:
            Exception: 두 Kind 모두에서 리소스를 찾지 못한 경우
        """
        cache_key = f"{self._namespace}/{name}"
        if cache_key in self._resource_kind_cache:
            return self._resource_kind_cache[cache_key]

        from kubernetes.client.exceptions import ApiException

        # Deployment 탐색
        try:
            self._apps_v1.read_namespaced_deployment(name, self._namespace)
            self._resource_kind_cache[cache_key] = "Deployment"
            logger.debug(f"[KubernetesRecoveryAdapter] Detected {name} as Deployment")
            return "Deployment"
        except ApiException as e:
            if e.status != 404:
                raise

        # StatefulSet 탐색
        try:
            self._apps_v1.read_namespaced_stateful_set(name, self._namespace)
            self._resource_kind_cache[cache_key] = "StatefulSet"
            logger.debug(f"[KubernetesRecoveryAdapter] Detected {name} as StatefulSet")
            return "StatefulSet"
        except ApiException as e:
            if e.status != 404:
                raise

        raise Exception(f"Workload '{name}' not found as Deployment or StatefulSet " f"in namespace '{self._namespace}'")

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """
        워크로드 Pod 재시작 (Rolling restart via annotation).

        Deployment/StatefulSet을 자동 감지하여 annotation 패치로 rolling restart를 트리거합니다.

        Args:
            worker_name: 워크로드(Deployment/StatefulSet) 이름

        Returns:
            RecoveryResult
        """
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            self._validate_service_name(worker_name)

            kind = self._detect_resource_kind(worker_name)

            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {"selfhealing.watchdog/restartedAt": datetime.now(timezone.utc).isoformat()}
                        }
                    }
                }
            }

            if kind == "Deployment":
                self._apps_v1.patch_namespaced_deployment(
                    name=worker_name,
                    namespace=self._namespace,
                    body=patch,
                )
            else:  # StatefulSet
                self._apps_v1.patch_namespaced_stateful_set(
                    name=worker_name,
                    namespace=self._namespace,
                    body=patch,
                )

            logger.info(f"[KubernetesRecoveryAdapter] Triggered restart: " f"{worker_name} ({kind})")
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=True,
                target=worker_name,
                message=f"Rolling restart triggered ({kind})",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"[KubernetesRecoveryAdapter] Restart failed: {e}")
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """
        Deployment replicas 조정.

        Args:
            name: Deployment 이름
            replicas: 목표 replica 수

        Returns:
            RecoveryResult
        """
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            # replicas 범위 검증 (DoS 방지: 0 ~ MAX_REPLICAS)
            self._validate_replicas(replicas)

            self._apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=self._namespace,
                body={"spec": {"replicas": replicas}},
            )
            logger.info(f"[KubernetesRecoveryAdapter] Scaled {name} to {replicas} replicas")
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=True,
                target=name,
                message=f"Scaled to {replicas} replicas",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"[KubernetesRecoveryAdapter] Scale failed: {e}")
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def delete_pod(self, pod_name: str, namespace: str | None = None) -> RecoveryResult:
        """
        Pod 강제 삭제 (ReplicaSet이 재생성).

        Args:
            pod_name: Pod 이름
            namespace: 네임스페이스 (None이면 기본값)

        Returns:
            RecoveryResult
        """
        ns = namespace or self._namespace
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=False,
                target=pod_name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            self._core_v1.delete_namespaced_pod(name=pod_name, namespace=ns)
            logger.info(f"[KubernetesRecoveryAdapter] Deleted pod: {pod_name}")
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=True,
                target=pod_name,
                message="Pod deleted, will be recreated by ReplicaSet",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"[KubernetesRecoveryAdapter] Delete pod failed: {e}")
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=False,
                target=pod_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )


class DockerComposeRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    Docker Compose 복구 어댑터.

    로컬 개발 환경용. docker-compose 명령을 사용합니다.
    서비스 이름 입력 검증 및 replicas 상한 제한은 부모 클래스에서 상속합니다.
    """

    def is_available(self) -> bool:
        """docker-compose 또는 docker compose 사용 가능 여부."""
        return shutil.which("docker-compose") is not None or shutil.which("docker") is not None

    def _get_compose_command(self) -> list[str]:
        """docker-compose 또는 docker compose 명령 반환."""
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        return ["docker", "compose"]

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """
        Docker container 재시작.

        Args:
            worker_name: 서비스 이름

        Returns:
            RecoveryResult
        """
        try:
            self._validate_service_name(worker_name)
            cmd = self._get_compose_command() + ["restart", worker_name]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            success = result.returncode == 0
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=success,
                target=worker_name,
                message=result.stdout if success else result.stderr,
                timestamp=datetime.now(timezone.utc),
            )
        except subprocess.TimeoutExpired:
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message="Command timed out",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """
        Docker Compose scale.

        Args:
            name: 서비스 이름
            replicas: 목표 replica 수

        Returns:
            RecoveryResult
        """
        try:
            self._validate_service_name(name)
            self._validate_replicas(replicas)
            cmd = self._get_compose_command() + [
                "up",
                "-d",
                "--scale",
                f"{name}={replicas}",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            success = result.returncode == 0
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=success,
                target=name,
                message=f"Scaled to {replicas}" if success else result.stderr,
                timestamp=datetime.now(timezone.utc),
            )
        except subprocess.TimeoutExpired:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message="Command timed out",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def delete_pod(self, pod_name: str, namespace: str = "") -> RecoveryResult:
        """
        Docker container 삭제 (restart로 대체).

        Args:
            pod_name: 컨테이너 이름
            namespace: (무시됨)

        Returns:
            RecoveryResult
        """
        # Docker에서는 restart로 대체
        return self.restart_worker(pod_name)


class NoOpRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    No-Op 복구 어댑터.

    테스트/드라이런 환경용. 실제 복구를 수행하지 않습니다.
    """

    def is_available(self) -> bool:
        return True

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would restart: {worker_name}")
        return RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target=worker_name,
            message="No-op (dry run)",
            timestamp=datetime.now(timezone.utc),
        )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would scale {name} to {replicas}")
        return RecoveryResult(
            action=RecoveryAction.SCALE_DEPLOYMENT,
            success=True,
            target=name,
            message=f"No-op (would scale to {replicas})",
            timestamp=datetime.now(timezone.utc),
        )

    def delete_pod(self, pod_name: str, namespace: str = "") -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would delete pod: {pod_name}")
        return RecoveryResult(
            action=RecoveryAction.DELETE_POD,
            success=True,
            target=pod_name,
            message="No-op (dry run)",
            timestamp=datetime.now(timezone.utc),
        )


def get_recovery_adapter() -> RecoveryInfrastructureAdapter:
    """
    환경에 맞는 복구 어댑터 반환.

    환경변수 SELFHEALING_RECOVERY_ADAPTER로 선택:
    - kubernetes (기본, K8s 환경)
    - docker (Docker Compose 환경)
    - noop (테스트/드라이런)

    Returns:
        RecoveryInfrastructureAdapter
    """
    adapter_type = os.environ.get("SELFHEALING_RECOVERY_ADAPTER", "kubernetes").lower()

    if adapter_type == "noop":
        return NoOpRecoveryAdapter()
    elif adapter_type == "docker":
        return DockerComposeRecoveryAdapter()
    else:
        adapter = KubernetesRecoveryAdapter()
        if adapter.is_available():
            return adapter

        # K8s 불가 시 Docker로 폴백
        docker_adapter = DockerComposeRecoveryAdapter()
        if docker_adapter.is_available():
            logger.info("[RecoveryAdapter] K8s unavailable, falling back to Docker")
            return docker_adapter

        # 최종 폴백: NoOp
        logger.warning("[RecoveryAdapter] No adapter available, using NoOp")
        return NoOpRecoveryAdapter()
