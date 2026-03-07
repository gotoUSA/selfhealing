"""
AuditHook, MetricsHook 및 PolicyHook.on_retry Protocol 단위 테스트.

테스트 대상: services/retry_handler/hooks.py, interfaces/resilience_policy.py
- AuditHook: log_retry_audit 래핑, Fail-Open
- MetricsHook: Prometheus 메트릭 기록, Fail-Open
- PolicyHook.on_retry: Protocol에 on_retry 메서드 존재 여부
"""

from __future__ import annotations

from unittest.mock import patch

from selfhealing.interfaces.resilience_policy import (
    PolicyHook,
    PolicyOutcome,
    PolicyResult,
)
from selfhealing.services.retry_handler.hooks import AuditHook, MetricsHook

# =============================================================================
# AuditHook — 계약 검증
# =============================================================================


class TestAuditHookContract:
    """AuditHook 구조 및 기본값 검증."""

    def test_has_all_hook_methods(self):
        """AuditHook은 on_execute, on_success, on_failure, on_retry, on_reject을 가진다."""
        hook = AuditHook()
        assert hasattr(hook, "on_execute")
        assert hasattr(hook, "on_success")
        assert hasattr(hook, "on_failure")
        assert hasattr(hook, "on_retry")
        assert hasattr(hook, "on_reject")

    def test_default_domain(self):
        """기본 domain은 'default'이다."""
        assert AuditHook()._domain == "default"


# =============================================================================
# AuditHook — 동작 검증
# =============================================================================


class TestAuditHookBehavior:
    """AuditHook 동작 검증. Fail-Open 원칙 포함."""

    def test_on_execute_is_noop(self):
        """on_execute는 아무 동작도 하지 않는다."""
        AuditHook(domain="test").on_execute("retry", 1)

    @patch("selfhealing.services.audit.log_retry_audit")
    def test_on_success_calls_log_retry_audit(self, mock_audit):
        """on_success는 성공 정보로 log_retry_audit를 호출한다."""
        hook = AuditHook(domain="payment")
        result = PolicyResult(
            outcome=PolicyOutcome.SUCCESS,
            total_attempts=2,
            metadata={"max_attempts": 3},
        )
        hook.on_success("retry", result)
        mock_audit.assert_called_once_with(
            domain="payment",
            attempt=2,
            max_attempts=3,
            success=True,
        )

    @patch("selfhealing.services.audit.log_retry_audit")
    def test_on_failure_calls_log_retry_audit(self, mock_audit):
        """on_failure는 에러 정보로 log_retry_audit를 호출한다."""
        hook = AuditHook(domain="payment")
        hook.on_failure("retry", ConnectionError("timeout"), 3)
        mock_audit.assert_called_once_with(
            domain="payment",
            attempt=3,
            max_attempts=3,
            success=False,
            error_type="ConnectionError",
            error_message="timeout",
        )

    @patch("selfhealing.services.audit.log_retry_audit")
    def test_on_retry_calls_log_retry_audit_with_delay(self, mock_audit):
        """on_retry는 delay 정보와 함께 log_retry_audit를 호출한다."""
        hook = AuditHook(domain="payment")
        hook.on_retry("retry", 2, 5.0)
        mock_audit.assert_called_once_with(
            domain="payment",
            attempt=2,
            max_attempts=3,
            success=False,
            wait_time=5.0,
        )

    @patch("selfhealing.services.audit.log_retry_audit")
    def test_on_reject_calls_log_retry_audit_as_policy_rejected(self, mock_audit):
        """on_reject는 PolicyRejected 타입으로 log_retry_audit를 호출한다."""
        hook = AuditHook(domain="payment")
        hook.on_reject("retry", "Kill Switch active")
        mock_audit.assert_called_once_with(
            domain="payment",
            attempt=0,
            max_attempts=0,
            success=False,
            error_type="PolicyRejected",
            error_message="Kill Switch active",
        )

    def test_fail_open_on_success_audit_failure(self):
        """on_success 중 예외가 발생해도 전파되지 않는다."""
        with patch(
            "selfhealing.services.audit.log_retry_audit",
            side_effect=RuntimeError("audit down"),
        ):
            AuditHook(domain="test").on_success("retry", PolicyResult())

    def test_fail_open_on_failure_audit_failure(self):
        """on_failure 중 예외가 발생해도 전파되지 않는다."""
        with patch(
            "selfhealing.services.audit.log_retry_audit",
            side_effect=RuntimeError("audit down"),
        ):
            AuditHook(domain="test").on_failure("retry", Exception("err"), 1)

    def test_fail_open_on_retry_audit_failure(self):
        """on_retry 중 예외가 발생해도 전파되지 않는다."""
        with patch(
            "selfhealing.services.audit.log_retry_audit",
            side_effect=RuntimeError("audit down"),
        ):
            AuditHook(domain="test").on_retry("retry", 1, 2.0)

    def test_fail_open_on_reject_audit_failure(self):
        """on_reject 중 예외가 발생해도 전파되지 않는다."""
        with patch(
            "selfhealing.services.audit.log_retry_audit",
            side_effect=RuntimeError("audit down"),
        ):
            AuditHook(domain="test").on_reject("retry", "reason")


# =============================================================================
# MetricsHook — 계약 검증
# =============================================================================


class TestMetricsHookContract:
    """MetricsHook 구조 및 기본값 검증."""

    def test_has_all_hook_methods(self):
        """MetricsHook은 on_execute, on_success, on_failure, on_retry, on_reject을 가진다."""
        hook = MetricsHook()
        assert hasattr(hook, "on_execute")
        assert hasattr(hook, "on_success")
        assert hasattr(hook, "on_failure")
        assert hasattr(hook, "on_retry")
        assert hasattr(hook, "on_reject")

    def test_default_domain(self):
        """기본 domain은 'default'이다."""
        assert MetricsHook()._domain == "default"


# =============================================================================
# MetricsHook — 동작 검증
# =============================================================================


class TestMetricsHookBehavior:
    """MetricsHook 동작 검증. 모든 메서드가 에러 없이 완료되는지 확인."""

    def test_on_execute_completes_without_error(self):
        MetricsHook(domain="test").on_execute("retry", 1)

    def test_on_success_completes_without_error(self):
        MetricsHook(domain="test").on_success("retry", PolicyResult())

    def test_on_failure_completes_without_error(self):
        MetricsHook(domain="test").on_failure("retry", Exception("err"), 1)

    def test_on_retry_tolerates_import_error(self):
        """metrics definitions import 실패에도 에러 없이 완료된다."""
        MetricsHook(domain="test").on_retry("retry", 1, 2.0)

    def test_on_reject_completes_without_error(self):
        MetricsHook(domain="test").on_reject("retry", "reason")

    def test_custom_domain(self):
        """커스텀 domain이 설정된다."""
        assert MetricsHook(domain="payment")._domain == "payment"


# =============================================================================
# PolicyHook.on_retry — Protocol 계약 검증
# =============================================================================


class TestPolicyHookOnRetryProtocolContract:
    """PolicyHook Protocol에 on_retry 메서드 존재 검증."""

    def test_on_retry_exists_in_protocol(self):
        """PolicyHook Protocol에 on_retry 메서드가 존재한다."""
        assert hasattr(PolicyHook, "on_retry")

    def test_class_with_all_methods_satisfies_protocol(self):
        """5개 메서드를 모두 구현한 클래스가 PolicyHook isinstance 검사를 통과한다."""

        class CompleteHook:
            def on_execute(self, policy_name: str, attempt: int) -> None:
                pass

            def on_success(self, policy_name: str, result: PolicyResult) -> None:
                pass

            def on_failure(self, policy_name: str, error: Exception, attempt: int) -> None:
                pass

            def on_retry(self, policy_name: str, attempt: int, delay: float) -> None:
                pass

            def on_reject(self, policy_name: str, reason: str) -> None:
                pass

        assert isinstance(CompleteHook(), PolicyHook)
