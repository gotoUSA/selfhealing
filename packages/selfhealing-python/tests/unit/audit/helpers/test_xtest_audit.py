"""
Tests for xtest_audit module.

X-Test-Mode 작업의 WAL 기반 Audit 로깅 기능을 테스트합니다.
"""

from unittest.mock import patch


class TestLogXtestOperationAudit:
    """log_xtest_operation_audit 함수 테스트."""

    def test_writes_to_wal_with_correct_event_type(self):
        """XTEST_OPERATION 이벤트 타입으로 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=42,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_operation_audit

            result = log_xtest_operation_audit(
                session_id="test-session-001",
                action="inject_dlq",
                component="dlq",
                details={"count": 5, "domain": "payment"},
                result="success",
                user="test_user",
            )

            assert result == 42
            mock_wal.assert_called_once()
            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["event_type"] == "XTEST_OPERATION"
            assert call_kwargs["source"] == "XTest.dlq"
            assert call_kwargs["domain"] == "xtest"
            assert call_kwargs["target_id"] == "test-session-001"
            assert call_kwargs["success"] is True

    def test_includes_session_and_action_in_details(self):
        """details에 session_id, action, component가 포함되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_operation_audit

            log_xtest_operation_audit(
                session_id="sess-123",
                action="force_status",
                component="cb",
                details={"target_service": "payment"},
                result="success",
            )

            call_kwargs = mock_wal.call_args.kwargs
            details = call_kwargs["details"]
            assert details["session_id"] == "sess-123"
            assert details["action"] == "force_status"
            assert details["component"] == "cb"
            assert details["target_service"] == "payment"

    def test_marks_failure_correctly(self):
        """result가 failed/error인 경우 success=False로 기록."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_operation_audit

            log_xtest_operation_audit(
                session_id="sess-456",
                action="inject_dlq",
                component="dlq",
                details={},
                result="failed",
                error_message="Database connection failed",
            )

            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["success"] is False
            assert call_kwargs["error_message"] == "Database connection failed"

    def test_passes_trace_id_when_provided(self):
        """trace_id가 제공되면 WAL에 전달."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_operation_audit

            log_xtest_operation_audit(
                session_id="sess-789",
                action="query",
                component="idempotency",
                details={},
                result="success",
                trace_id="trace-abc-123",
            )

            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["trace_id"] == "trace-abc-123"


class TestLogXtestScenarioAudit:
    """log_xtest_scenario_audit 함수 테스트."""

    def test_writes_scenario_to_wal(self):
        """시나리오 실행 결과가 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=100,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_scenario_audit

            result = log_xtest_scenario_audit(
                scenario_id="scenario-001",
                scenario_name="dlq_injection_and_replay",
                service_name="payment",
                status="completed",
                steps_total=5,
                steps_completed=5,
                errors=[],
                duration_ms=1500.5,
            )

            assert result == 100
            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["event_type"] == "XTEST_SCENARIO"
            assert call_kwargs["source"] == "XTest.Integration"
            assert call_kwargs["success"] is True

    def test_includes_scenario_details(self):
        """시나리오 상세 정보가 details에 포함되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_scenario_audit

            log_xtest_scenario_audit(
                scenario_id="scn-002",
                scenario_name="cb_trip_and_recovery",
                service_name="external_api",
                status="completed",
                steps_total=8,
                steps_completed=8,
                errors=[],
                duration_ms=3200.0,
                session_id="sess-xyz",
                user="admin",
            )

            details = mock_wal.call_args.kwargs["details"]
            assert details["scenario_id"] == "scn-002"
            assert details["scenario_name"] == "cb_trip_and_recovery"
            assert details["service_name"] == "external_api"
            assert details["steps_total"] == 8
            assert details["steps_completed"] == 8
            assert details["duration_ms"] == 3200.0
            assert details["user"] == "admin"

    def test_marks_failed_scenario(self):
        """에러가 있는 시나리오는 success=False로 기록."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_scenario_audit

            log_xtest_scenario_audit(
                scenario_id="scn-003",
                scenario_name="rate_limit_exhaustion",
                service_name="api_gateway",
                status="failed",
                steps_total=6,
                steps_completed=3,
                errors=["Step 4 timeout", "Connection refused"],
                duration_ms=5000.0,
            )

            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["success"] is False
            assert call_kwargs["error_message"] == "Step 4 timeout"

    def test_limits_errors_to_ten(self):
        """errors 목록은 최대 10개로 제한."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_scenario_audit

            many_errors = [f"Error {i}" for i in range(20)]

            log_xtest_scenario_audit(
                scenario_id="scn-004",
                scenario_name="stress_test",
                service_name="load_balancer",
                status="failed",
                steps_total=20,
                steps_completed=10,
                errors=many_errors,
                duration_ms=10000.0,
            )

            details = mock_wal.call_args.kwargs["details"]
            assert len(details["errors"]) == 10


class TestLogXtestSessionAudit:
    """log_xtest_session_start_audit, log_xtest_session_end_audit 함수 테스트."""

    def test_session_start_writes_to_wal(self):
        """세션 시작이 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import (
                log_xtest_session_start_audit,
            )

            result = log_xtest_session_start_audit(
                session_id="session-abc",
                user="tester",
                metadata={"purpose": "regression_test"},
            )

            assert result == 1
            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["event_type"] == "XTEST_SESSION"
            assert call_kwargs["source"] == "XTest.Session"
            details = call_kwargs["details"]
            assert details["event"] == "session_start"
            assert details["session_id"] == "session-abc"
            assert details["metadata"]["purpose"] == "regression_test"

    def test_session_end_writes_to_wal(self):
        """세션 종료가 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=2,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import (
                log_xtest_session_end_audit,
            )

            result = log_xtest_session_end_audit(
                session_id="session-abc",
                operations_count=15,
                scenarios_count=3,
                duration_seconds=120.5,
                user="tester",
                summary={"passed": 3, "failed": 0},
            )

            assert result == 2
            call_kwargs = mock_wal.call_args.kwargs
            details = call_kwargs["details"]
            assert details["event"] == "session_end"
            assert details["operations_count"] == 15
            assert details["scenarios_count"] == 3
            assert details["duration_seconds"] == 120.5


class TestLogXtestInjectionAudit:
    """log_xtest_injection_audit 함수 테스트."""

    def test_writes_injection_to_wal(self):
        """데이터 주입이 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=50,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_injection_audit

            result = log_xtest_injection_audit(
                session_id="sess-inject",
                component="dlq",
                injection_type="create",
                count=5,
                target_ids=["dlq-1", "dlq-2", "dlq-3", "dlq-4", "dlq-5"],
                user="injector",
            )

            assert result == 50
            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["event_type"] == "XTEST_INJECTION"
            assert call_kwargs["source"] == "XTest.dlq"
            details = call_kwargs["details"]
            assert details["injection_type"] == "create"
            assert details["count"] == 5
            assert len(details["target_ids"]) == 5

    def test_limits_target_ids_to_twenty(self):
        """target_ids 목록은 최대 20개로 제한."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=1,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_injection_audit

            many_ids = [f"id-{i}" for i in range(30)]

            log_xtest_injection_audit(
                session_id="sess-many",
                component="idempotency",
                injection_type="override",
                count=30,
                target_ids=many_ids,
            )

            details = mock_wal.call_args.kwargs["details"]
            assert len(details["target_ids"]) == 20


class TestLogXtestCleanupAudit:
    """log_xtest_cleanup_audit 함수 테스트."""

    def test_writes_cleanup_to_wal(self):
        """정리 작업이 WAL에 기록되어야 함."""
        with patch(
            "selfhealing.services.audit.xtest_audit._write_to_wal",
            return_value=60,
        ) as mock_wal:
            from selfhealing.services.audit.xtest_audit import log_xtest_cleanup_audit

            result = log_xtest_cleanup_audit(
                session_id="sess-cleanup",
                component="dlq",
                cleaned_count=10,
                cleaned_ids=["dlq-1", "dlq-2"],
                user="cleaner",
            )

            assert result == 60
            call_kwargs = mock_wal.call_args.kwargs
            assert call_kwargs["event_type"] == "XTEST_CLEANUP"
            assert call_kwargs["source"] == "XTest.dlq"
            details = call_kwargs["details"]
            assert details["action"] == "cleanup"
            assert details["cleaned_count"] == 10


class TestXtestAuditImports:
    """모듈 export 테스트."""

    def test_can_import_from_audit_package(self):
        """selfhealing.services.audit에서 import 가능해야 함."""
        from selfhealing.services.audit import (
            log_xtest_cleanup_audit,
            log_xtest_injection_audit,
            log_xtest_operation_audit,
            log_xtest_scenario_audit,
            log_xtest_session_end_audit,
            log_xtest_session_start_audit,
        )

        assert callable(log_xtest_operation_audit)
        assert callable(log_xtest_scenario_audit)
        assert callable(log_xtest_session_start_audit)
        assert callable(log_xtest_session_end_audit)
        assert callable(log_xtest_injection_audit)
        assert callable(log_xtest_cleanup_audit)

    def test_can_import_from_audit_helpers(self):
        """selfhealing.services.audit에서 import 가능해야 함 (하위 호환성)."""
        from selfhealing.services.audit import (
            log_xtest_cleanup_audit,
            log_xtest_injection_audit,
            log_xtest_operation_audit,
            log_xtest_scenario_audit,
            log_xtest_session_end_audit,
            log_xtest_session_start_audit,
        )

        assert callable(log_xtest_operation_audit)
        assert callable(log_xtest_scenario_audit)
        assert callable(log_xtest_session_start_audit)
        assert callable(log_xtest_session_end_audit)
        assert callable(log_xtest_injection_audit)
        assert callable(log_xtest_cleanup_audit)
