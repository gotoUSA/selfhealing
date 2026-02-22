"""
API 미노출 설정 추가 테스트.

ForensicConfigSerializer 확장 필드 및 LoggingConfigSerializer 테스트.
"""

import os
import django

# Configure Django settings before importing DRF
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import MagicMock
from rest_framework.test import APIRequestFactory
from rest_framework import status

# Import views
from selfhealing.api.django.views.config import (
    ForensicConfigView,
    LoggingConfigView,
)

# Import serializers
from selfhealing.api.django.serializers.config import (
    ForensicConfigSerializer,
    LoggingConfigSerializer,
)


@pytest.fixture
def factory():
    """Create API request factory."""
    return APIRequestFactory()


@pytest.fixture
def admin_user():
    """Create mock admin user with selfhealing_admin role."""
    user = MagicMock()
    user.is_staff = True
    user.is_authenticated = True
    user.username = "admin"
    user.groups.filter.return_value.exists.return_value = True
    return user


@pytest.fixture
def viewer_user():
    """Create mock viewer user."""
    user = MagicMock()
    user.is_staff = False
    user.is_authenticated = True
    user.username = "viewer"
    user.groups.filter.return_value.exists.return_value = True
    return user


# =============================================================================
# ForensicConfigSerializer Tests (확장된 필드)
# =============================================================================


class TestForensicConfigSerializer:
    """ForensicConfigSerializer 테스트 - 확장 필드 포함."""

    def test_valid_basic_fields(self):
        """기본 필드 검증 테스트."""
        data = {
            "error_message_max_length": 500,
            "response_body_max_length": 10000,
            "user_agent_max_length": 200,
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["error_message_max_length"] == 500

    def test_valid_extended_fields(self):
        """확장 필드 검증 테스트."""
        data = {
            "max_stack_frames": 100,
            "max_context_size_bytes": 131072,  # 128KB
            "include_local_variables": False,
            "sanitize_sensitive_data": True,
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["max_stack_frames"] == 100
        assert serializer.validated_data["max_context_size_bytes"] == 131072

    def test_max_stack_frames_min_value(self):
        """max_stack_frames 최소값 검증 테스트."""
        data = {"max_stack_frames": 5}  # Min is 10
        serializer = ForensicConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "max_stack_frames" in serializer.errors

    def test_max_stack_frames_max_value(self):
        """max_stack_frames 최대값 검증 테스트."""
        data = {"max_stack_frames": 300}  # Max is 200
        serializer = ForensicConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "max_stack_frames" in serializer.errors

    def test_max_context_size_bytes_min_value(self):
        """max_context_size_bytes 최소값 검증 테스트."""
        data = {"max_context_size_bytes": 512}  # Min is 1024
        serializer = ForensicConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "max_context_size_bytes" in serializer.errors

    def test_max_context_size_bytes_max_value(self):
        """max_context_size_bytes 최대값 검증 테스트 (1MB 상한)."""
        data = {"max_context_size_bytes": 2097152}  # Max is 1048576 (1MB)
        serializer = ForensicConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "max_context_size_bytes" in serializer.errors

    def test_sensitive_key_patterns_list(self):
        """sensitive_key_patterns 리스트 필드 검증 테스트."""
        data = {
            "sensitive_key_patterns": ["password", "secret", "api_key", "token"],
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["sensitive_key_patterns"]) == 4

    def test_boolean_fields(self):
        """Boolean 필드 검증 테스트."""
        data = {
            "include_local_variables": True,
            "sanitize_sensitive_data": False,
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["include_local_variables"] is True
        assert serializer.validated_data["sanitize_sensitive_data"] is False

    def test_apply_strategy_mixin(self):
        """ApplyStrategyMixin 필드 검증 테스트."""
        data = {
            "max_stack_frames": 50,
            "apply_strategy": "delayed",
            "delay_seconds": 30,
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["apply_strategy"] == "delayed"
        assert serializer.validated_data["delay_seconds"] == 30


# =============================================================================
# LoggingConfigSerializer Tests (신규)
# =============================================================================


class TestLoggingConfigSerializer:
    """LoggingConfigSerializer 테스트."""

    def test_valid_log_levels(self):
        """유효한 로그 레벨 검증 테스트."""
        data = {
            "dlq_log_level": "DEBUG",
            "circuit_breaker_log_level": "WARNING",
            "replay_log_level": "ERROR",
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["dlq_log_level"] == "DEBUG"
        assert serializer.validated_data["circuit_breaker_log_level"] == "WARNING"

    def test_invalid_log_level(self):
        """잘못된 로그 레벨 검증 테스트."""
        data = {"dlq_log_level": "TRACE"}  # Invalid level
        serializer = LoggingConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "dlq_log_level" in serializer.errors

    def test_all_component_log_levels(self):
        """모든 컴포넌트 로그 레벨 검증 테스트."""
        data = {
            "dlq_log_level": "INFO",
            "circuit_breaker_log_level": "INFO",
            "replay_log_level": "INFO",
            "sla_log_level": "INFO",
            "forensic_log_level": "DEBUG",
            "emergency_log_level": "WARNING",
            "chaos_log_level": "INFO",
            "l2_storage_log_level": "INFO",
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data) == 8

    def test_log_format_settings(self):
        """로그 포맷 설정 검증 테스트."""
        data = {
            "include_timestamps": True,
            "include_request_id": True,
            "include_user_info": False,
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["include_timestamps"] is True
        assert serializer.validated_data["include_user_info"] is False

    def test_log_output_settings(self):
        """로그 출력 설정 검증 테스트."""
        data = {
            "console_output_enabled": True,
            "file_output_enabled": True,
            "structured_json": True,
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["console_output_enabled"] is True
        assert serializer.validated_data["structured_json"] is True

    def test_partial_update(self):
        """부분 업데이트 검증 테스트."""
        data = {"dlq_log_level": "WARNING"}
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        # Only the provided field should be in validated_data
        assert "dlq_log_level" in serializer.validated_data

    def test_empty_data_valid(self):
        """빈 데이터도 유효함 검증 테스트 (모든 필드 optional)."""
        serializer = LoggingConfigSerializer(data={})
        assert serializer.is_valid(), serializer.errors

    def test_apply_strategy_immediate(self):
        """Apply Strategy immediate 검증 테스트."""
        data = {
            "dlq_log_level": "DEBUG",
            "apply_strategy": "immediate",
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["apply_strategy"] == "immediate"

    def test_apply_strategy_graceful(self):
        """Apply Strategy graceful 검증 테스트."""
        data = {
            "circuit_breaker_log_level": "WARNING",
            "apply_strategy": "graceful",
            "grace_timeout_seconds": 30,
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["apply_strategy"] == "graceful"
        assert serializer.validated_data["grace_timeout_seconds"] == 30


# =============================================================================
# ForensicConfigView Tests
# =============================================================================


class TestForensicConfigView:
    """ForensicConfigView API 테스트."""

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user, viewer_user):
        """Setup test fixtures."""
        self.factory = factory
        self.admin_user = admin_user
        self.viewer_user = viewer_user

    def test_get_forensic_config(self):
        """GET /api/self-healing/config/forensic/ 테스트."""
        request = self.factory.get("/api/self-healing/config/forensic/")
        request.user = self.admin_user

        view = ForensicConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "config" in response.data

    def test_put_forensic_config_extended_fields(self):
        """PUT /api/self-healing/config/forensic/ - 확장 필드 업데이트 테스트."""
        data = {
            "max_stack_frames": 75,
            "max_context_size_bytes": 65536,
            "sanitize_sensitive_data": True,
        }
        request = self.factory.put(
            "/api/self-healing/config/forensic/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = ForensicConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "applied"

    def test_put_forensic_config_invalid(self):
        """PUT /api/self-healing/config/forensic/ - 잘못된 데이터 테스트."""
        data = {"max_stack_frames": 500}  # Exceeds max of 200
        request = self.factory.put(
            "/api/self-healing/config/forensic/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = ForensicConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# LoggingConfigView Tests
# =============================================================================


class TestLoggingConfigView:
    """LoggingConfigView API 테스트."""

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user, viewer_user):
        """Setup test fixtures."""
        self.factory = factory
        self.admin_user = admin_user
        self.viewer_user = viewer_user

    def test_get_logging_config(self):
        """GET /api/self-healing/config/logging/ 테스트."""
        request = self.factory.get("/api/self-healing/config/logging/")
        request.user = self.admin_user

        view = LoggingConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "success"
        assert "config" in response.data

    def test_put_logging_config_single_level(self):
        """PUT /api/self-healing/config/logging/ - 단일 레벨 업데이트 테스트."""
        data = {"dlq_log_level": "DEBUG"}
        request = self.factory.put(
            "/api/self-healing/config/logging/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = LoggingConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "applied"

    def test_put_logging_config_multiple_levels(self):
        """PUT /api/self-healing/config/logging/ - 다중 레벨 업데이트 테스트."""
        data = {
            "dlq_log_level": "WARNING",
            "circuit_breaker_log_level": "ERROR",
            "chaos_log_level": "DEBUG",
        }
        request = self.factory.put(
            "/api/self-healing/config/logging/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = LoggingConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "applied"

    def test_put_logging_config_invalid_level(self):
        """PUT /api/self-healing/config/logging/ - 잘못된 레벨 테스트."""
        data = {"dlq_log_level": "VERBOSE"}  # Invalid level
        request = self.factory.put(
            "/api/self-healing/config/logging/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = LoggingConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_logging_config_format_settings(self):
        """PUT /api/self-healing/config/logging/ - 포맷 설정 업데이트 테스트."""
        data = {
            "structured_json": True,
            "include_timestamps": True,
            "include_request_id": True,
        }
        request = self.factory.put(
            "/api/self-healing/config/logging/",
            data=data,
            format="json",
        )
        request.user = self.admin_user

        view = LoggingConfigView.as_view()
        response = view(request)

        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# Integration Tests
# =============================================================================


class TestConfigIntegration:
    """설정 통합 테스트."""

    @pytest.fixture(autouse=True)
    def setup(self, factory, admin_user):
        """Setup test fixtures."""
        self.factory = factory
        self.admin_user = admin_user

    def test_forensic_and_logging_config_together(self):
        """Forensic + Logging 설정 함께 조회 테스트."""
        # Forensic 조회
        forensic_request = self.factory.get("/api/self-healing/config/forensic/")
        forensic_request.user = self.admin_user
        forensic_response = ForensicConfigView.as_view()(forensic_request)
        assert forensic_response.status_code == status.HTTP_200_OK

        # Logging 조회
        logging_request = self.factory.get("/api/self-healing/config/logging/")
        logging_request.user = self.admin_user
        logging_response = LoggingConfigView.as_view()(logging_request)
        assert logging_response.status_code == status.HTTP_200_OK

    def test_serializer_get_config_changes(self):
        """Serializer get_config_changes 메서드 테스트."""
        data = {
            "dlq_log_level": "DEBUG",
            "apply_strategy": "immediate",
        }
        serializer = LoggingConfigSerializer(data=data)
        assert serializer.is_valid()

        changes = serializer.get_config_changes()
        assert "dlq_log_level" in changes
        assert "apply_strategy" not in changes  # Excluded

    def test_serializer_get_apply_options(self):
        """Serializer get_apply_options 메서드 테스트."""
        data = {
            "max_stack_frames": 100,
            "apply_strategy": "delayed",
            "delay_seconds": 60,
        }
        serializer = ForensicConfigSerializer(data=data)
        assert serializer.is_valid()

        options = serializer.get_apply_options()
        assert options["strategy"] == "delayed"
        assert options["delay_seconds"] == 60
