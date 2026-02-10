"""
Self-Healing 보안 취약점 회귀 방지 테스트.

핵심 보안 로직의 동작을 검증하며, 이 테스트가 실패하면 보안 패치가 무력화된 것을 의미합니다.

테스트 대상:
1. _is_auth_disabled(): 프로덕션 Fail-Secure 보장
2. DockerComposeRecoveryAdapter: 서비스 이름 검증, replicas 범위 검증
3. KubernetesRecoveryAdapter: replicas 범위 검증
"""

from __future__ import annotations

import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# Django 설정 구성 (테스트용 — REST_FRAMEWORK import 시 필요)
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        REST_FRAMEWORK={},
        SECRET_KEY="test-secret-key",
    )
    django.setup()

from selfhealing.api.django.permissions import _is_auth_disabled
from selfhealing.meta.recovery_adapter import (
    DockerComposeRecoveryAdapter,
    KubernetesRecoveryAdapter,
    RecoveryAction,
)


# =============================================================================
# 취약점 #1: 환경변수 인증 우회 - Fail-Secure 검증
# =============================================================================


class TestAuthBypassFailSecure:
    """_is_auth_disabled() 프로덕션 Fail-Secure 검증.

    프로덕션 환경에서는 DISABLE_SELFHEALING_AUTH=true라도
    인증이 절대 비활성화되지 않아야 합니다.
    """

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_production_env_blocks_auth_disable(self):
        """ENVIRONMENT=production일 때 auth bypass 차단."""
        assert _is_auth_disabled() is False

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "prod",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_prod_env_blocks_auth_disable(self):
        """ENVIRONMENT=prod일 때 auth bypass 차단."""
        assert _is_auth_disabled() is False

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "live",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_live_env_blocks_auth_disable(self):
        """ENVIRONMENT=live일 때 auth bypass 차단."""
        assert _is_auth_disabled() is False

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "development",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "myproject.settings.production",
        },
    )
    def test_django_settings_production_blocks_auth_disable(self):
        """DJANGO_SETTINGS_MODULE에 'prod' 포함 시 auth bypass 차단."""
        assert _is_auth_disabled() is False

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "development",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_development_env_allows_auth_disable(self):
        """개발 환경에서는 auth bypass 허용 (기존 동작 유지)."""
        assert _is_auth_disabled() is True

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "development",
            "DISABLE_SELFHEALING_AUTH": "",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_no_disable_flag_returns_false(self):
        """DISABLE_SELFHEALING_AUTH 미설정 시 False."""
        assert _is_auth_disabled() is False

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "DISABLE_SELFHEALING_AUTH": "true",
            "DJANGO_SETTINGS_MODULE": "",
        },
    )
    def test_production_bypass_attempt_logs_error(self):
        """프로덕션에서 bypass 시도 시 ERROR 로그 기록."""
        with patch("selfhealing.api.django.permissions.logger") as mock_logger:
            _is_auth_disabled()
            mock_logger.error.assert_called_once()
            call_args = str(mock_logger.error.call_args)
            assert "SECURITY" in call_args
            assert "PRODUCTION" in call_args


# =============================================================================
# 취약점 #2: OS 명령어 인젝션 - 입력 검증
# =============================================================================


class TestServiceNameValidation:
    """DockerComposeRecoveryAdapter 서비스 이름 화이트리스트 검증.

    OS 명령어 인젝션 방지를 위한 입력 화이트리스트 검증.
    """

    def setup_method(self):
        self.adapter = DockerComposeRecoveryAdapter()

    def test_valid_service_name_passes(self):
        """유효한 서비스 이름은 에러 없이 통과."""
        # 에러 발생하지 않으면 통과
        self.adapter._validate_service_name("celery-worker")
        self.adapter._validate_service_name("web")
        self.adapter._validate_service_name("api.v2")
        self.adapter._validate_service_name("worker_01")

    def test_injection_semicolon_raises_value_error(self):
        """세미콜론 인젝션 시 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid service name"):
            self.adapter._validate_service_name("web; rm -rf /")

    def test_injection_newline_raises_value_error(self):
        """개행 문자 인젝션 시 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid service name"):
            self.adapter._validate_service_name("web\n--privileged")

    def test_injection_pipe_raises_value_error(self):
        """파이프 인젝션 시 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid service name"):
            self.adapter._validate_service_name("web | cat /etc/passwd")

    def test_empty_name_raises_value_error(self):
        """빈 이름은 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid service name"):
            self.adapter._validate_service_name("")

    def test_too_long_name_raises_value_error(self):
        """128자 초과 서비스 이름은 ValueError 발생."""
        long_name = "a" * 129
        with pytest.raises(ValueError, match="Invalid service name"):
            self.adapter._validate_service_name(long_name)

    def test_max_length_name_passes(self):
        """128자 서비스 이름은 통과."""
        max_name = "a" * 128
        self.adapter._validate_service_name(max_name)  # 에러 없이 통과

    def test_restart_worker_rejects_invalid_name(self):
        """restart_worker()가 유효하지 않은 이름을 거부."""
        result = self.adapter.restart_worker("web; rm -rf /")
        # ValueError가 except Exception에 잡혀 실패 결과 반환
        assert result.success is False
        assert "Invalid service name" in result.message


class TestReplicasValidation:
    """replicas 범위 검증 — 리소스 고갈 DoS 방지.

    과도한 replica 수로 인한 리소스 고갈을 방지하는 상한 검증.
    """

    def setup_method(self):
        self.adapter = DockerComposeRecoveryAdapter()

    def test_valid_replicas_passes(self):
        """유효한 replicas 값은 에러 없이 통과."""
        self.adapter._validate_replicas(0)
        self.adapter._validate_replicas(1)
        self.adapter._validate_replicas(50)

    def test_excessive_replicas_raises_value_error(self):
        """50 초과 replicas는 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid replicas count"):
            self.adapter._validate_replicas(51)

    def test_extreme_replicas_raises_value_error(self):
        """극단적 replicas 값(DoS 시도)은 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid replicas count"):
            self.adapter._validate_replicas(999999)

    def test_negative_replicas_raises_value_error(self):
        """음수 replicas는 ValueError 발생."""
        with pytest.raises(ValueError, match="Invalid replicas count"):
            self.adapter._validate_replicas(-1)

    def test_scale_deployment_rejects_excessive_replicas(self):
        """scale_deployment()가 과도한 replicas를 거부."""
        result = self.adapter.scale_deployment("worker", 1000)
        assert result.success is False
        assert "Invalid replicas count" in result.message


# =============================================================================
# 취약점 #2 확장: KubernetesRecoveryAdapter replicas 검증
# =============================================================================


class TestKubernetesReplicasValidation:
    """KubernetesRecoveryAdapter replicas 범위 검증.

    K8s 어댑터에도 replicas 상한이 적용되어야 합니다.
    """

    def test_k8s_scale_rejects_excessive_replicas(self):
        """K8s 어댑터도 과도한 replicas를 거부."""
        adapter = KubernetesRecoveryAdapter()
        adapter._is_available = True
        adapter._apps_v1 = MagicMock()

        result = adapter.scale_deployment("api", 1000)

        assert result.success is False
        assert "Invalid replicas count" in result.message
        # K8s API 호출이 실행되지 않아야 함
        adapter._apps_v1.patch_namespaced_deployment_scale.assert_not_called()

    def test_k8s_scale_allows_valid_replicas(self):
        """K8s 어댑터에서 유효한 replicas는 통과."""
        adapter = KubernetesRecoveryAdapter()
        adapter._is_available = True
        adapter._apps_v1 = MagicMock()

        result = adapter.scale_deployment("api", 5)

        assert result.success is True
        adapter._apps_v1.patch_namespaced_deployment_scale.assert_called_once()
