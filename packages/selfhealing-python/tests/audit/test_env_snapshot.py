"""
Tests for Environment Variable Snapshot Audit.

Tests:
- collect_env_snapshot(): 환경변수 수집
- log_env_snapshot_to_audit(): Audit 로깅
- Sensitive value masking
- Hash generation for change detection

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from __future__ import annotations

import hashlib
import os
from unittest import mock

import pytest


class TestCollectEnvSnapshot:
    """Tests for collect_env_snapshot function."""

    def test_collect_tracked_prefixes(self):
        """Tracked prefix가 있는 환경변수만 수집한다."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot, TRACKED_PREFIXES

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_DLQ_ENABLED": "true",
                "CIRCUIT_BREAKER_THRESHOLD": "5",
                "DLQ_MAX_RETRIES": "3",
                "SLA_TIMEOUT_MS": "5000",
                "CHAOS_ENABLED": "false",
                "UNRELATED_VAR": "should_be_ignored",
                "DATABASE_URL": "should_also_be_ignored",
            },
            clear=True,
        ):
            snapshot = collect_env_snapshot()

            assert snapshot["count"] == 5
            assert "SELFHEALING_DLQ_ENABLED" in snapshot["variables"]
            assert "CIRCUIT_BREAKER_THRESHOLD" in snapshot["variables"]
            assert "DLQ_MAX_RETRIES" in snapshot["variables"]
            assert "SLA_TIMEOUT_MS" in snapshot["variables"]
            assert "CHAOS_ENABLED" in snapshot["variables"]
            assert "UNRELATED_VAR" not in snapshot["variables"]
            assert "DATABASE_URL" not in snapshot["variables"]

    def test_sensitive_values_are_masked(self):
        """민감 키워드가 포함된 변수는 마스킹된다."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_SECRET_KEY": "super-secret-value",
                "SELFHEALING_API_KEY": "api-key-12345",
                "SELFHEALING_PASSWORD": "my-password",
                "SELFHEALING_TOKEN": "bearer-token",
                "SELFHEALING_PRIVATE_KEY": "private-key-data",
                "SELFHEALING_CREDENTIAL": "user:pass",
                "SELFHEALING_DLQ_ENABLED": "true",  # non-sensitive
            },
            clear=True,
        ):
            snapshot = collect_env_snapshot()

            # 민감 변수는 마스킹되어야 함
            assert snapshot["variables"]["SELFHEALING_SECRET_KEY"] == "***MASKED***"
            assert snapshot["variables"]["SELFHEALING_API_KEY"] == "***MASKED***"
            assert snapshot["variables"]["SELFHEALING_PASSWORD"] == "***MASKED***"
            assert snapshot["variables"]["SELFHEALING_TOKEN"] == "***MASKED***"
            assert snapshot["variables"]["SELFHEALING_PRIVATE_KEY"] == "***MASKED***"
            assert snapshot["variables"]["SELFHEALING_CREDENTIAL"] == "***MASKED***"

            # 비민감 변수는 원본 값 유지
            assert snapshot["variables"]["SELFHEALING_DLQ_ENABLED"] == "true"

    def test_hash_generation(self):
        """Hash가 올바르게 생성된다."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_A": "value_a",
                "SELFHEALING_B": "value_b",
            },
            clear=True,
        ):
            snapshot = collect_env_snapshot()

            assert snapshot["hash"].startswith("sha256:")
            assert len(snapshot["hash"]) == 23  # "sha256:" + 16 chars

    def test_hash_changes_with_values(self):
        """값이 변경되면 Hash도 변경된다."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot

        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_TEST": "value1"},
            clear=True,
        ):
            snapshot1 = collect_env_snapshot()

        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_TEST": "value2"},
            clear=True,
        ):
            snapshot2 = collect_env_snapshot()

        assert snapshot1["hash"] != snapshot2["hash"]

    def test_hash_same_for_same_values(self):
        """같은 값이면 Hash도 동일하다."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot

        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_TEST": "same_value"},
            clear=True,
        ):
            snapshot1 = collect_env_snapshot()
            snapshot2 = collect_env_snapshot()

        assert snapshot1["hash"] == snapshot2["hash"]

    def test_empty_env(self):
        """Tracked 환경변수가 없으면 빈 결과 반환."""
        from selfhealing.audit.env_snapshot import collect_env_snapshot

        with mock.patch.dict(
            os.environ,
            {"UNRELATED_VAR": "value"},
            clear=True,
        ):
            snapshot = collect_env_snapshot()

            assert snapshot["count"] == 0
            assert snapshot["variables"] == {}
            assert snapshot["hash"].startswith("sha256:")


class TestLogEnvSnapshotToAudit:
    """Tests for log_env_snapshot_to_audit function."""

    def test_logs_to_audit_service(self):
        """환경변수 스냅샷이 Audit 서비스에 기록된다."""
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_TEST": "value",
                "CIRCUIT_BREAKER_THRESHOLD": "5",
            },
            clear=True,
        ):
            with mock.patch(
                "selfhealing.audit.log_config_change"
            ) as mock_log:
                mock_log.return_value = True

                result = log_env_snapshot_to_audit()

                assert result is True
                mock_log.assert_called_once()

                # 호출 인자 검증
                call_kwargs = mock_log.call_args.kwargs
                assert call_kwargs["config_type"] == "environment_variables"
                assert call_kwargs["config_key"] == "startup_snapshot"
                assert call_kwargs["old_value"] is None
                assert call_kwargs["user"] == "system_startup"
                assert "variable_count" in call_kwargs["metadata"]
                assert "hash" in call_kwargs["metadata"]

    def test_skips_when_no_tracked_vars(self):
        """Tracked 환경변수가 없으면 로깅하지 않는다."""
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit

        with mock.patch.dict(
            os.environ,
            {"UNRELATED_VAR": "value"},
            clear=True,
        ):
            with mock.patch(
                "selfhealing.audit.log_config_change"
            ) as mock_log:
                result = log_env_snapshot_to_audit()

                assert result is True
                mock_log.assert_not_called()

    def test_handles_import_error_gracefully(self):
        """Audit 모듈 import 실패 시 False 반환."""
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit

        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_TEST": "value"},
            clear=True,
        ):
            with mock.patch(
                "selfhealing.audit.log_config_change",
                side_effect=ImportError("Module not found"),
            ):
                result = log_env_snapshot_to_audit()

                # Import error시 False 반환 (시스템은 계속 작동)
                assert result is False

    def test_handles_exception_gracefully(self):
        """예외 발생 시에도 시스템은 계속 동작."""
        from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit

        with mock.patch.dict(
            os.environ,
            {"SELFHEALING_TEST": "value"},
            clear=True,
        ):
            with mock.patch(
                "selfhealing.audit.log_config_change",
                side_effect=Exception("Unexpected error"),
            ):
                result = log_env_snapshot_to_audit()

                assert result is False


class TestGetEnvSnapshotSummary:
    """Tests for get_env_snapshot_summary function."""

    def test_returns_summary(self):
        """요약 정보를 올바르게 반환한다."""
        from selfhealing.audit.env_snapshot import get_env_snapshot_summary, TRACKED_PREFIXES

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_A": "1",
                "SELFHEALING_B": "2",
                "DLQ_ENABLED": "true",
            },
            clear=True,
        ):
            summary = get_env_snapshot_summary()

            assert summary["count"] == 3
            assert summary["hash"].startswith("sha256:")
            assert summary["tracked_prefixes"] == TRACKED_PREFIXES


@pytest.mark.django_db
class TestPostMigrateSignalIntegration:
    """Integration tests for post_migrate signal handler."""

    def test_create_selfhealing_groups_logs_env_snapshot(self):
        """post_migrate 시그널에서 환경변수 스냅샷이 기록된다."""
        from selfhealing.adapters.django.apps import create_selfhealing_groups

        with mock.patch(
            "selfhealing.audit.env_snapshot.log_env_snapshot_to_audit"
        ) as mock_log:
            mock_log.return_value = True

            # 시그널 핸들러 직접 호출
            create_selfhealing_groups(sender=mock.Mock())

            # 환경변수 스냅샷 로깅이 호출되었는지 확인
            mock_log.assert_called_once()

    def test_create_selfhealing_groups_continues_on_env_snapshot_failure(self):
        """env_snapshot 실패해도 그룹 생성은 성공."""
        from selfhealing.adapters.django.apps import create_selfhealing_groups

        with mock.patch(
            "selfhealing.audit.env_snapshot.log_env_snapshot_to_audit",
            side_effect=Exception("Snapshot failed"),
        ):
            # 예외가 발생해도 전파되지 않아야 함
            try:
                create_selfhealing_groups(sender=mock.Mock())
            except Exception:
                pytest.fail("Should not raise exception when env_snapshot fails")
