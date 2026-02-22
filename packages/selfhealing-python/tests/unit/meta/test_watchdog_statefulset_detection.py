"""
KubernetesRecoveryAdapter — StatefulSet 동적 감지 단위 테스트.

_detect_resource_kind() 캐싱 동작과
restart_worker() Deployment/StatefulSet 분기 검증.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

# kubernetes 패키지가 없는 환경을 위한 Mock 모듈 설정
_mock_k8s_exceptions = types.ModuleType("kubernetes.client.exceptions")


class _MockApiException(Exception):
    """kubernetes.client.exceptions.ApiException Mock."""

    def __init__(self, status: int = 0, reason: str = ""):
        self.status = status
        self.reason = reason
        super().__init__(f"({status}) Reason: {reason}")


_mock_k8s_exceptions.ApiException = _MockApiException

# kubernetes 모듈 트리 Mock 등록 (이미 있으면 스킵)
if "kubernetes" not in sys.modules:
    sys.modules["kubernetes"] = types.ModuleType("kubernetes")
if "kubernetes.client" not in sys.modules:
    sys.modules["kubernetes.client"] = types.ModuleType("kubernetes.client")
if "kubernetes.client.exceptions" not in sys.modules:
    sys.modules["kubernetes.client.exceptions"] = _mock_k8s_exceptions
else:
    # 이미 로드된 경우 ApiException만 주입
    if not hasattr(sys.modules["kubernetes.client.exceptions"], "ApiException"):
        sys.modules["kubernetes.client.exceptions"].ApiException = _MockApiException

from selfhealing.meta.recovery_adapter import (
    KubernetesRecoveryAdapter,
    RecoveryAction,
)


@pytest.fixture
def k8s_adapter():
    """Mock K8s 클라이언트가 주입된 KubernetesRecoveryAdapter."""
    adapter = KubernetesRecoveryAdapter()
    adapter._is_available = True
    adapter._apps_v1 = MagicMock()
    adapter._core_v1 = MagicMock()
    adapter._namespace = "selfhealing"
    adapter._resource_kind_cache = {}
    return adapter


# =============================================================================
# _detect_resource_kind() 테스트
# =============================================================================


class TestDetectResourceKindBehavior:
    """_detect_resource_kind() 동작 검증."""

    def test_deployment_detected(self, k8s_adapter):
        """Deployment가 존재하면 'Deployment'를 반환한다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.return_value = MagicMock()

        result = k8s_adapter._detect_resource_kind("redis")

        assert result == "Deployment"
        k8s_adapter._apps_v1.read_namespaced_deployment.assert_called_once_with("redis", "selfhealing")

    def test_statefulset_fallback_on_deployment_404(self, k8s_adapter):
        """Deployment 404 시 StatefulSet을 탐색하여 반환한다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.side_effect = _MockApiException(status=404)
        k8s_adapter._apps_v1.read_namespaced_stateful_set.return_value = MagicMock()

        result = k8s_adapter._detect_resource_kind("redis")

        assert result == "StatefulSet"

    def test_result_cached_after_first_call(self, k8s_adapter):
        """2회 호출 시 API는 1회만 호출된다 (캐싱)."""
        k8s_adapter._apps_v1.read_namespaced_deployment.return_value = MagicMock()

        k8s_adapter._detect_resource_kind("redis")
        k8s_adapter._detect_resource_kind("redis")

        assert k8s_adapter._apps_v1.read_namespaced_deployment.call_count == 1

    def test_different_names_cached_separately(self, k8s_adapter):
        """다른 이름은 각각 별도로 캐싱된다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.return_value = MagicMock()

        k8s_adapter._detect_resource_kind("redis")
        k8s_adapter._detect_resource_kind("celery-worker")

        assert k8s_adapter._apps_v1.read_namespaced_deployment.call_count == 2

    def test_not_found_raises_exception(self, k8s_adapter):
        """Deployment/StatefulSet 모두 404 시 예외를 발생시킨다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.side_effect = _MockApiException(status=404)
        k8s_adapter._apps_v1.read_namespaced_stateful_set.side_effect = _MockApiException(status=404)

        with pytest.raises(Exception, match="not found"):
            k8s_adapter._detect_resource_kind("nonexistent")

    def test_non_404_api_error_propagated(self, k8s_adapter):
        """404 이외의 API 에러는 그대로 전파된다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.side_effect = _MockApiException(status=403)

        with pytest.raises(_MockApiException):
            k8s_adapter._detect_resource_kind("redis")


# =============================================================================
# restart_worker() Deployment/StatefulSet 분기 테스트
# =============================================================================


class TestRestartWorkerResourceKindBehavior:
    """restart_worker() Deployment/StatefulSet 분기 동작 검증."""

    def test_deployment_restart_uses_patch_deployment(self, k8s_adapter):
        """Deployment 대상 restart가 patch_namespaced_deployment를 호출한다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.return_value = MagicMock()

        result = k8s_adapter.restart_worker("celery-worker")

        assert result.success is True
        assert result.action == RecoveryAction.RESTART_WORKER
        k8s_adapter._apps_v1.patch_namespaced_deployment.assert_called_once()
        k8s_adapter._apps_v1.patch_namespaced_stateful_set.assert_not_called()
        assert "Deployment" in result.message

    def test_statefulset_restart_uses_patch_stateful_set(self, k8s_adapter):
        """StatefulSet 대상 restart가 patch_namespaced_stateful_set을 호출한다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.side_effect = _MockApiException(status=404)
        k8s_adapter._apps_v1.read_namespaced_stateful_set.return_value = MagicMock()

        result = k8s_adapter.restart_worker("redis")

        assert result.success is True
        k8s_adapter._apps_v1.patch_namespaced_stateful_set.assert_called_once()
        k8s_adapter._apps_v1.patch_namespaced_deployment.assert_not_called()
        assert "StatefulSet" in result.message

    def test_restart_worker_not_available_returns_failure(self, k8s_adapter):
        """K8s 클라이언트 미사용 시 실패 결과를 반환한다."""
        k8s_adapter._is_available = False

        result = k8s_adapter.restart_worker("redis")

        assert result.success is False
        assert "not available" in result.message

    def test_restart_worker_validates_service_name(self, k8s_adapter):
        """유효하지 않은 서비스 이름은 실패를 반환한다."""
        result = k8s_adapter.restart_worker("../../etc/passwd")

        assert result.success is False

    def test_restart_worker_workload_not_found(self, k8s_adapter):
        """Deployment/StatefulSet 모두 미발견 시 실패를 반환한다."""
        k8s_adapter._apps_v1.read_namespaced_deployment.side_effect = _MockApiException(status=404)
        k8s_adapter._apps_v1.read_namespaced_stateful_set.side_effect = _MockApiException(status=404)

        result = k8s_adapter.restart_worker("nonexistent")

        assert result.success is False
        assert "not found" in result.message


# =============================================================================
# MetaWatchdogSettings 신규 필드 계약 테스트
# =============================================================================


class TestMetaWatchdogSettingsNewFieldsContract:
    """MetaWatchdogSettings 신규 필드 기본값 계약 검증."""

    def test_recovery_cooldown_seconds_default(self):
        """recovery_cooldown_seconds 기본값은 300.0이다."""
        from selfhealing.settings.meta_watchdog import MetaWatchdogSettings

        settings = MetaWatchdogSettings()
        assert settings.recovery_cooldown_seconds == 300.0

    def test_redis_workload_name_default(self):
        """redis_workload_name 기본값은 'redis'이다."""
        from selfhealing.settings.meta_watchdog import MetaWatchdogSettings

        settings = MetaWatchdogSettings()
        assert settings.redis_workload_name == "redis"

    def test_dlq_worker_workload_name_default(self):
        """dlq_worker_workload_name 기본값은 'celery-dlq-worker'이다."""
        from selfhealing.settings.meta_watchdog import MetaWatchdogSettings

        settings = MetaWatchdogSettings()
        assert settings.dlq_worker_workload_name == "celery-dlq-worker"
