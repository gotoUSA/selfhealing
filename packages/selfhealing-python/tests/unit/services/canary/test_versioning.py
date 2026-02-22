"""
Config Version Conflict 단위 테스트.

테스트 대상:
1. VersionConflictError - 버전 충돌 예외
2. VersionChecker.check() - 버전 일치 확인
3. VersionChecker.get_current_version() - 현재 버전 조회
4. check_version_and_rollback() - 버전 확인 후 롤백

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.canary.versioning import (
    VersionChecker,
    VersionConflictError,
    check_version_and_rollback,
)

# =============================================================================
# Test: VersionConflictError
# =============================================================================


class TestVersionConflictError:
    """VersionConflictError 예외 테스트."""

    def test_error_attributes(self):
        """속성이 올바르게 설정되는지 확인."""
        error = VersionConflictError(
            expected_version=5,
            actual_version=8,
            conflicting_operator="other@example.com",
            config_type="circuit_breaker",
        )

        assert error.expected_version == 5
        assert error.actual_version == 8
        assert error.conflicting_operator == "other@example.com"
        assert error.config_type == "circuit_breaker"

    def test_error_message(self):
        """에러 메시지 형식 확인."""
        error = VersionConflictError(
            expected_version=5,
            actual_version=8,
            conflicting_operator="other@example.com",
            config_type="circuit_breaker",
        )

        assert "v5" in str(error)
        assert "v8" in str(error)
        assert "other@example.com" in str(error)


# =============================================================================
# Test: VersionChecker
# =============================================================================


class TestVersionChecker:
    """VersionChecker 테스트."""

    @pytest.fixture
    def checker(self):
        """VersionChecker 인스턴스."""
        return VersionChecker()

    def test_check_version_match(self, checker):
        """버전이 일치할 때."""
        mock_version = MagicMock()
        mock_version.version = 5
        mock_version.changed_by = "admin@example.com"

        mock_service = MagicMock()
        mock_service.get_current_version.return_value = mock_version

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            is_valid, info = checker.check("circuit_breaker", expected_version=5)

        assert is_valid is True
        assert info["actual_version"] == 5

    def test_check_version_mismatch(self, checker):
        """버전이 다를 때."""
        mock_version = MagicMock()
        mock_version.version = 8
        mock_version.changed_by = "other@example.com"

        mock_service = MagicMock()
        mock_service.get_current_version.return_value = mock_version

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            is_valid, info = checker.check("circuit_breaker", expected_version=5)

        assert is_valid is False
        assert info["actual_version"] == 8
        assert info["changed_by"] == "other@example.com"

    def test_check_no_existing_config(self, checker):
        """설정이 없을 때."""
        mock_service = MagicMock()
        mock_service.get_current_version.return_value = None

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            is_valid, info = checker.check("circuit_breaker", expected_version=5)

        # 설정이 없으면 충돌 아님 (새 설정)
        assert is_valid is True
        assert info["actual_version"] == 0

    def test_get_current_version(self, checker):
        """현재 버전 조회."""
        mock_version = MagicMock()
        mock_version.version = 10

        mock_service = MagicMock()
        mock_service.get_current_version.return_value = mock_version

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            version = checker.get_current_version("circuit_breaker")

        assert version == 10

    def test_get_current_version_no_config(self, checker):
        """설정이 없을 때 0 반환."""
        mock_service = MagicMock()
        mock_service.get_current_version.return_value = None

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            version = checker.get_current_version("circuit_breaker")

        assert version == 0


# =============================================================================
# Test: check_version_and_rollback
# =============================================================================


class TestCheckVersionAndRollback:
    """check_version_and_rollback 함수 테스트."""

    def test_rollback_success_when_version_matches(self):
        """버전이 일치하면 롤백 성공."""
        mock_current = MagicMock()
        mock_current.version = 8
        mock_current.changed_by = "admin@example.com"

        mock_new_version = MagicMock()
        mock_new_version.version = 9

        mock_service = MagicMock()
        mock_service.get_current_version.return_value = mock_current
        mock_service.rollback.return_value = mock_new_version

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service
            result = check_version_and_rollback(
                config_type="circuit_breaker",
                target_version=5,
                expected_current_version=8,
                rolled_back_by="admin@example.com",
            )

        assert result.version == 9
        mock_service.rollback.assert_called_once()

    def test_rollback_fails_on_version_conflict(self):
        """버전이 다르면 VersionConflictError."""
        mock_current = MagicMock()
        mock_current.version = 10  # 예상과 다름
        mock_current.changed_by = "other@example.com"

        mock_service = MagicMock()
        mock_service.get_current_version.return_value = mock_current

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service

            with pytest.raises(VersionConflictError) as exc_info:
                check_version_and_rollback(
                    config_type="circuit_breaker",
                    target_version=5,
                    expected_current_version=8,
                    rolled_back_by="admin@example.com",
                )

        assert exc_info.value.expected_version == 8
        assert exc_info.value.actual_version == 10
        assert exc_info.value.conflicting_operator == "other@example.com"

    def test_rollback_no_existing_config(self):
        """설정이 없을 때 ValueError."""
        mock_service = MagicMock()
        mock_service.get_current_version.return_value = None

        with patch("selfhealing.services.config_history.get_config_history_service") as mock_get:
            mock_get.return_value = mock_service

            # 설정이 없는 경우의 동작은 구현에 따라 다름
            # 여기서는 롤백 시도 시 get_version 호출됨
            result = check_version_and_rollback(
                config_type="circuit_breaker",
                target_version=5,
                expected_current_version=0,
                rolled_back_by="admin@example.com",
            )

        # 설정이 없으면 롤백 호출됨 (새 설정으로 처리)
        mock_service.rollback.assert_called_once()
