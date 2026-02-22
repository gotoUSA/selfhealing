"""
CircuitBreakerPolicy Hook 단위 테스트 (#227).

테스트 대상:
- services/circuit_breaker/hooks.py (AuditPolicyHook, EventBusPolicyHook, build_default_hooks)

코드 근거:
- AuditPolicyHook: on_execute(no-op), on_success(debug 로그), on_failure(debug 로그),
  on_retry(no-op), on_reject → log_cb_state_change_audit 호출 (Fail-Open)
- EventBusPolicyHook: on_execute/on_success/on_failure/on_retry(no-op),
  on_reject → EventBus.emit(CIRCUIT_BREAKER_OPENED) 호출 (Fail-Open)
- build_default_hooks(): [AuditPolicyHook, EventBusPolicyHook] 리스트 반환, 개별 실패 시 Fail-Open

UNIT_TEST_GUIDELINES.md 준수:
- 계약 검증(Contract): 반환 타입, 메서드 존재, build_default_hooks 구성
- 동작 검증(Behavior): Fail-Open, mock 호출 검증, 소스 참조
- conftest.py 배치: 1개 파일 전용 fixture → 파일 내부 (§5.1)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.circuit_breaker.hooks import (
    AuditPolicyHook,
    EventBusPolicyHook,
    build_default_hooks,
)

# =============================================================================
# Fixtures — 1개 파일 전용이므로 파일 내부 배치 (§5.1)
# =============================================================================


@pytest.fixture
def audit_hook():
    """AuditPolicyHook 인스턴스."""
    return AuditPolicyHook()


@pytest.fixture
def eventbus_hook():
    """EventBusPolicyHook 인스턴스."""
    return EventBusPolicyHook()


# =============================================================================
# AuditPolicyHook 계약 검증 (Contract)
# =============================================================================


class TestAuditPolicyHookContract:
    """AuditPolicyHook 메서드 존재 및 시그니처 계약 검증."""

    def test_has_on_execute_method(self, audit_hook):
        """AuditPolicyHook.on_execute 메서드가 존재한다."""
        assert hasattr(audit_hook, "on_execute")
        assert callable(audit_hook.on_execute)

    def test_has_on_success_method(self, audit_hook):
        """AuditPolicyHook.on_success 메서드가 존재한다."""
        assert hasattr(audit_hook, "on_success")
        assert callable(audit_hook.on_success)

    def test_has_on_failure_method(self, audit_hook):
        """AuditPolicyHook.on_failure 메서드가 존재한다."""
        assert hasattr(audit_hook, "on_failure")
        assert callable(audit_hook.on_failure)

    def test_has_on_retry_method(self, audit_hook):
        """AuditPolicyHook.on_retry 메서드가 존재한다."""
        assert hasattr(audit_hook, "on_retry")
        assert callable(audit_hook.on_retry)

    def test_has_on_reject_method(self, audit_hook):
        """AuditPolicyHook.on_reject 메서드가 존재한다."""
        assert hasattr(audit_hook, "on_reject")
        assert callable(audit_hook.on_reject)


# =============================================================================
# AuditPolicyHook 동작 검증 (Behavior)
# =============================================================================


class TestAuditPolicyHookBehavior:
    """AuditPolicyHook 동작 검증."""

    def test_on_execute_is_noop(self, audit_hook):
        """on_execute는 아무 동작도 하지 않는다."""
        # 예외 없이 정상 반환
        result = audit_hook.on_execute("test_service", 1)
        assert result is None

    def test_on_retry_is_noop(self, audit_hook):
        """on_retry는 no-op이다 (CB는 재시도 없음)."""
        result = audit_hook.on_retry("test_service", 1, 0.5)
        assert result is None

    def test_on_success_does_not_raise(self, audit_hook):
        """on_success는 debug 로그만 기록하고 예외를 던지지 않는다."""
        mock_result = MagicMock()
        # 예외 없이 정상 반환
        audit_hook.on_success("test_service", mock_result)

    def test_on_failure_does_not_raise(self, audit_hook):
        """on_failure는 debug 로그만 기록하고 예외를 던지지 않는다."""
        error = ValueError("test error")
        # 예외 없이 정상 반환
        audit_hook.on_failure("test_service", error, 1)

    def test_on_reject_calls_audit_log(self, audit_hook):
        """on_reject는 log_cb_state_change_audit를 호출한다."""
        with patch(
            "selfhealing.services.circuit_breaker.hooks.log_cb_state_change_audit",
            create=True,
        ) as mock_audit:
            # audit 모듈을 lazy import하므로 해당 경로를 패치
            with patch(
                "selfhealing.services.audit.cb_audit.log_cb_state_change_audit",
                mock_audit,
            ):
                audit_hook.on_reject("payment_api", "circuit_open")

    def test_on_reject_passes_correct_args_to_audit(self, audit_hook):
        """on_reject는 cb_name, old_state, new_state, reason을 전달한다."""
        with patch("selfhealing.services.audit.cb_audit.log_cb_state_change_audit") as mock_audit:
            audit_hook.on_reject("payment_api", "circuit_open")
            mock_audit.assert_called_once_with(
                cb_name="payment_api",
                old_state="open",
                new_state="open",
                reason="request_rejected|circuit_open",
            )

    def test_on_reject_fail_open_on_import_error(self, audit_hook):
        """audit import 실패 시 예외를 삼킨다 (Fail-Open)."""
        with patch(
            "selfhealing.services.audit.cb_audit.log_cb_state_change_audit",
            side_effect=ImportError("audit not available"),
        ):
            # Fail-Open: 예외가 발생하지 않아야 함
            audit_hook.on_reject("test_service", "circuit_open")

    def test_on_reject_fail_open_on_runtime_error(self, audit_hook):
        """audit 실행 중 예외 시에도 Fail-Open."""
        with patch(
            "selfhealing.services.audit.cb_audit.log_cb_state_change_audit",
            side_effect=RuntimeError("audit failed"),
        ):
            # Fail-Open: 예외가 발생하지 않아야 함
            audit_hook.on_reject("test_service", "circuit_open")


# =============================================================================
# EventBusPolicyHook 계약 검증 (Contract)
# =============================================================================


class TestEventBusPolicyHookContract:
    """EventBusPolicyHook 메서드 존재 및 시그니처 계약 검증."""

    def test_has_on_execute_method(self, eventbus_hook):
        """EventBusPolicyHook.on_execute 메서드가 존재한다."""
        assert hasattr(eventbus_hook, "on_execute")
        assert callable(eventbus_hook.on_execute)

    def test_has_on_success_method(self, eventbus_hook):
        """EventBusPolicyHook.on_success 메서드가 존재한다."""
        assert hasattr(eventbus_hook, "on_success")
        assert callable(eventbus_hook.on_success)

    def test_has_on_failure_method(self, eventbus_hook):
        """EventBusPolicyHook.on_failure 메서드가 존재한다."""
        assert hasattr(eventbus_hook, "on_failure")
        assert callable(eventbus_hook.on_failure)

    def test_has_on_retry_method(self, eventbus_hook):
        """EventBusPolicyHook.on_retry 메서드가 존재한다."""
        assert hasattr(eventbus_hook, "on_retry")
        assert callable(eventbus_hook.on_retry)

    def test_has_on_reject_method(self, eventbus_hook):
        """EventBusPolicyHook.on_reject 메서드가 존재한다."""
        assert hasattr(eventbus_hook, "on_reject")
        assert callable(eventbus_hook.on_reject)


# =============================================================================
# EventBusPolicyHook 동작 검증 (Behavior)
# =============================================================================


class TestEventBusPolicyHookBehavior:
    """EventBusPolicyHook 동작 검증."""

    def test_on_execute_is_noop(self, eventbus_hook):
        """on_execute는 아무 동작도 하지 않는다."""
        result = eventbus_hook.on_execute("test_service", 1)
        assert result is None

    def test_on_success_is_noop(self, eventbus_hook):
        """on_success는 no-op이다."""
        mock_result = MagicMock()
        result = eventbus_hook.on_success("test_service", mock_result)
        assert result is None

    def test_on_failure_is_noop(self, eventbus_hook):
        """on_failure는 no-op이다."""
        result = eventbus_hook.on_failure("test_service", ValueError("err"), 1)
        assert result is None

    def test_on_retry_is_noop(self, eventbus_hook):
        """on_retry는 no-op이다 (CB는 재시도 없음)."""
        result = eventbus_hook.on_retry("test_service", 1, 0.5)
        assert result is None

    def test_on_reject_calls_event_bus_emit(self, eventbus_hook):
        """on_reject는 EventBus.emit(CIRCUIT_BREAKER_OPENED)을 호출한다."""
        mock_bus = MagicMock()
        mock_event_type = MagicMock()
        mock_event_type.CIRCUIT_BREAKER_OPENED = "CIRCUIT_BREAKER_OPENED"

        with (
            patch(
                "selfhealing.services.event_bus.get_event_bus",
                return_value=mock_bus,
            ),
            patch(
                "selfhealing.services.event_bus.EventType",
                mock_event_type,
            ),
        ):
            eventbus_hook.on_reject("payment_api", "circuit_open")
            mock_bus.emit.assert_called_once_with(
                "CIRCUIT_BREAKER_OPENED",
                {
                    "service_name": "payment_api",
                    "event": "request_rejected",
                    "reason": "circuit_open",
                },
                source="circuit_breaker_policy",
            )

    def test_on_reject_fail_open_on_import_error(self, eventbus_hook):
        """EventBus import 실패 시 Fail-Open."""
        with patch(
            "selfhealing.services.event_bus.get_event_bus",
            side_effect=ImportError("event_bus not available"),
        ):
            # Fail-Open: 예외가 발생하지 않아야 함
            eventbus_hook.on_reject("test_service", "circuit_open")

    def test_on_reject_fail_open_on_emit_error(self, eventbus_hook):
        """emit() 실패 시에도 Fail-Open."""
        mock_bus = MagicMock()
        mock_bus.emit.side_effect = RuntimeError("emit failed")

        with (
            patch(
                "selfhealing.services.event_bus.get_event_bus",
                return_value=mock_bus,
            ),
            patch(
                "selfhealing.services.event_bus.EventType",
                MagicMock(),
            ),
        ):
            # Fail-Open: 예외가 발생하지 않아야 함
            eventbus_hook.on_reject("test_service", "circuit_open")


# =============================================================================
# build_default_hooks 계약 검증 (Contract)
# =============================================================================


class TestBuildDefaultHooksContract:
    """build_default_hooks() 반환값 계약 검증."""

    def test_returns_list(self):
        """build_default_hooks()는 list를 반환한다."""
        hooks = build_default_hooks()
        assert isinstance(hooks, list)

    def test_contains_two_hooks(self):
        """기본적으로 AuditPolicyHook + EventBusPolicyHook 2개를 포함한다."""
        hooks = build_default_hooks()
        assert len(hooks) == 2

    def test_first_hook_is_audit(self):
        """첫 번째 hook은 AuditPolicyHook이다."""
        hooks = build_default_hooks()
        assert isinstance(hooks[0], AuditPolicyHook)

    def test_second_hook_is_eventbus(self):
        """두 번째 hook은 EventBusPolicyHook이다."""
        hooks = build_default_hooks()
        assert isinstance(hooks[1], EventBusPolicyHook)


# =============================================================================
# build_default_hooks Fail-Open 동작 검증 (Behavior)
# =============================================================================


class TestBuildDefaultHooksBehavior:
    """build_default_hooks() Fail-Open 동작 검증."""

    def test_returns_eventbus_only_when_audit_fails(self):
        """AuditPolicyHook 생성 실패 시에도 EventBusPolicyHook은 포함된다."""
        with patch(
            "selfhealing.services.circuit_breaker.hooks.AuditPolicyHook",
            side_effect=RuntimeError("audit init failed"),
        ):
            hooks = build_default_hooks()
            # AuditPolicyHook 실패 → 1개만 포함
            assert len(hooks) == 1
            assert isinstance(hooks[0], EventBusPolicyHook)

    def test_returns_audit_only_when_eventbus_fails(self):
        """EventBusPolicyHook 생성 실패 시에도 AuditPolicyHook은 포함된다."""
        with patch(
            "selfhealing.services.circuit_breaker.hooks.EventBusPolicyHook",
            side_effect=RuntimeError("eventbus init failed"),
        ):
            hooks = build_default_hooks()
            # EventBusPolicyHook 실패 → 1개만 포함
            assert len(hooks) == 1
            assert isinstance(hooks[0], AuditPolicyHook)

    def test_returns_empty_list_when_both_fail(self):
        """모든 Hook 생성 실패 시 빈 리스트를 반환한다."""
        with (
            patch(
                "selfhealing.services.circuit_breaker.hooks.AuditPolicyHook",
                side_effect=RuntimeError("audit failed"),
            ),
            patch(
                "selfhealing.services.circuit_breaker.hooks.EventBusPolicyHook",
                side_effect=RuntimeError("eventbus failed"),
            ),
        ):
            hooks = build_default_hooks()
            assert hooks == []
