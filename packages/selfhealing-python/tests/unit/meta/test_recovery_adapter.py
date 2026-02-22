"""
RecoveryInfrastructureAdapter 테스트.

환경별 복구 어댑터 테스트.
"""

from unittest import mock

from selfhealing.meta.recovery_adapter import (
    DockerComposeRecoveryAdapter,
    KubernetesRecoveryAdapter,
    NoOpRecoveryAdapter,
    RecoveryAction,
    RecoveryResult,
    get_recovery_adapter,
)


class TestRecoveryAction:
    """RecoveryAction 열거형 테스트."""

    def test_values(self):
        """값 확인."""
        assert RecoveryAction.RESTART_WORKER.value == "restart_worker"
        assert RecoveryAction.SCALE_DEPLOYMENT.value == "scale_deployment"
        assert RecoveryAction.DELETE_POD.value == "delete_pod"
        assert RecoveryAction.RESET_CONNECTION.value == "reset_connection"


class TestRecoveryResult:
    """RecoveryResult 데이터클래스 테스트."""

    def test_creation(self):
        """생성 테스트."""
        from datetime import datetime, timezone

        result = RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target="celery-worker",
            message="Restarted successfully",
            timestamp=datetime.now(timezone.utc),
        )

        assert result.action == RecoveryAction.RESTART_WORKER
        assert result.success is True
        assert result.target == "celery-worker"

    def test_with_details(self):
        """상세 정보 포함 테스트."""
        from datetime import datetime, timezone

        result = RecoveryResult(
            action=RecoveryAction.SCALE_DEPLOYMENT,
            success=True,
            target="api",
            message="Scaled",
            timestamp=datetime.now(timezone.utc),
            details={"replicas": 3},
        )

        assert result.details["replicas"] == 3


class TestNoOpRecoveryAdapter:
    """NoOpRecoveryAdapter 테스트."""

    def test_is_available(self):
        """사용 가능 여부 테스트."""
        adapter = NoOpRecoveryAdapter()
        assert adapter.is_available() is True

    def test_restart_worker(self):
        """restart_worker 테스트."""
        adapter = NoOpRecoveryAdapter()
        result = adapter.restart_worker("test-worker")

        assert isinstance(result, RecoveryResult)
        assert result.success is True
        assert result.action == RecoveryAction.RESTART_WORKER
        assert "No-op" in result.message

    def test_scale_deployment(self):
        """scale_deployment 테스트."""
        adapter = NoOpRecoveryAdapter()
        result = adapter.scale_deployment("test-deployment", 3)

        assert isinstance(result, RecoveryResult)
        assert result.success is True
        assert result.action == RecoveryAction.SCALE_DEPLOYMENT

    def test_delete_pod(self):
        """delete_pod 테스트."""
        adapter = NoOpRecoveryAdapter()
        result = adapter.delete_pod("test-pod", "default")

        assert isinstance(result, RecoveryResult)
        assert result.success is True
        assert result.action == RecoveryAction.DELETE_POD


class TestDockerComposeRecoveryAdapter:
    """DockerComposeRecoveryAdapter 테스트."""

    def test_is_available(self):
        """사용 가능 여부 테스트."""
        adapter = DockerComposeRecoveryAdapter()
        # docker-compose 또는 docker 명령어 유무에 따라 결과 다름
        assert isinstance(adapter.is_available(), bool)

    def test_restart_worker_success(self):
        """restart_worker 성공 테스트 (Mock)."""
        adapter = DockerComposeRecoveryAdapter()

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stdout="Container restarted",
                stderr="",
            )

            result = adapter.restart_worker("celery-worker")

            assert result.success is True
            assert result.action == RecoveryAction.RESTART_WORKER

    def test_restart_worker_failure(self):
        """restart_worker 실패 테스트 (Mock)."""
        adapter = DockerComposeRecoveryAdapter()

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: No such service",
            )

            result = adapter.restart_worker("unknown-service")

            assert result.success is False

    def test_restart_worker_timeout(self):
        """restart_worker 타임아웃 테스트 (Mock)."""
        adapter = DockerComposeRecoveryAdapter()

        with mock.patch("subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=60)

            result = adapter.restart_worker("celery-worker")

            assert result.success is False
            assert "timed out" in result.message.lower()

    def test_scale_deployment_success(self):
        """scale_deployment 성공 테스트 (Mock)."""
        adapter = DockerComposeRecoveryAdapter()

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stdout="Scaled",
                stderr="",
            )

            result = adapter.scale_deployment("worker", 3)

            assert result.success is True
            assert result.action == RecoveryAction.SCALE_DEPLOYMENT

    def test_delete_pod_redirects_to_restart(self):
        """delete_pod가 restart로 리다이렉트 테스트."""
        adapter = DockerComposeRecoveryAdapter()

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(
                returncode=0,
                stdout="Restarted",
                stderr="",
            )

            result = adapter.delete_pod("container-name", "")

            # Docker Compose에서는 restart로 대체
            assert result.action == RecoveryAction.RESTART_WORKER


class TestKubernetesRecoveryAdapter:
    """KubernetesRecoveryAdapter 테스트."""

    def test_initialization(self):
        """초기화 테스트."""
        adapter = KubernetesRecoveryAdapter(namespace="test-namespace")

        # kubernetes 패키지 없으면 False
        assert isinstance(adapter.is_available(), bool)

    def test_restart_worker_not_available(self):
        """K8s 미사용 시 restart_worker 테스트."""
        adapter = KubernetesRecoveryAdapter()

        if not adapter.is_available():
            result = adapter.restart_worker("test-worker")
            assert result.success is False
            assert "not available" in result.message

    def test_scale_deployment_not_available(self):
        """K8s 미사용 시 scale_deployment 테스트."""
        adapter = KubernetesRecoveryAdapter()

        if not adapter.is_available():
            result = adapter.scale_deployment("test-deployment", 3)
            assert result.success is False

    def test_delete_pod_not_available(self):
        """K8s 미사용 시 delete_pod 테스트."""
        adapter = KubernetesRecoveryAdapter()

        if not adapter.is_available():
            result = adapter.delete_pod("test-pod", "default")
            assert result.success is False

    def test_restart_worker_with_mock_client(self):
        """Mock K8s 클라이언트로 restart_worker 테스트."""
        adapter = KubernetesRecoveryAdapter()
        adapter._is_available = True
        adapter._apps_v1 = mock.MagicMock()
        # _detect_resource_kind() 캐시 프라이밍 — kubernetes 패키지 미설치 환경 대응
        adapter._resource_kind_cache["selfhealing/celery-worker"] = "Deployment"

        result = adapter.restart_worker("celery-worker")

        assert result.action == RecoveryAction.RESTART_WORKER
        adapter._apps_v1.patch_namespaced_deployment.assert_called_once()

    def test_scale_deployment_with_mock_client(self):
        """Mock K8s 클라이언트로 scale_deployment 테스트."""
        adapter = KubernetesRecoveryAdapter()
        adapter._is_available = True
        adapter._apps_v1 = mock.MagicMock()

        result = adapter.scale_deployment("api", 5)

        assert result.action == RecoveryAction.SCALE_DEPLOYMENT
        adapter._apps_v1.patch_namespaced_deployment_scale.assert_called_once()

    def test_delete_pod_with_mock_client(self):
        """Mock K8s 클라이언트로 delete_pod 테스트."""
        adapter = KubernetesRecoveryAdapter()
        adapter._is_available = True
        adapter._core_v1 = mock.MagicMock()

        result = adapter.delete_pod("redis-abc123", "selfhealing")

        assert result.action == RecoveryAction.DELETE_POD
        adapter._core_v1.delete_namespaced_pod.assert_called_once()


class TestRecoveryAdapterFactory:
    """복구 어댑터 팩토리 테스트."""

    def test_get_adapter_noop(self):
        """NOOP 어댑터 환경변수 테스트."""
        import os

        with mock.patch.dict(os.environ, {"SELFHEALING_RECOVERY_ADAPTER": "noop"}):
            adapter = get_recovery_adapter()
            assert isinstance(adapter, NoOpRecoveryAdapter)

    def test_get_adapter_docker(self):
        """Docker 어댑터 환경변수 테스트."""
        import os

        with mock.patch.dict(os.environ, {"SELFHEALING_RECOVERY_ADAPTER": "docker"}):
            adapter = get_recovery_adapter()
            assert isinstance(adapter, DockerComposeRecoveryAdapter)

    def test_get_adapter_kubernetes_fallback(self):
        """Kubernetes 폴백 테스트."""
        import os

        with mock.patch.dict(os.environ, {"SELFHEALING_RECOVERY_ADAPTER": "kubernetes"}):
            adapter = get_recovery_adapter()
            # K8s 사용 불가 시 Docker 또는 NoOp로 폴백
            assert isinstance(
                adapter,
                (KubernetesRecoveryAdapter, DockerComposeRecoveryAdapter, NoOpRecoveryAdapter),
            )

    def test_get_adapter_default(self):
        """기본 어댑터 테스트."""
        adapter = get_recovery_adapter()
        assert adapter is not None
