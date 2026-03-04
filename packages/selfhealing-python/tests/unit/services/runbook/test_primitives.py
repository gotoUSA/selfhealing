"""
Builtin ActionPrimitive 단위 테스트 — Pydantic 스키마 + 핸들러 + 등록 로직.

테스트 대상:
    selfhealing.services.runbook.primitives

계약 검증 클래스 (Test*Contract):
    - TestConfigSetParamsContract
    - TestAssertMetricParamsContract
    - TestNotifySendParamsContract
    - TestRecoveryStartParamsContract
    - TestEmergencyActivateParamsContract
    - TestWaitStabilizeParamsContract
    - TestBuiltinPrimitivesMapContract
    - TestOperatorMapContract

동작 검증 클래스 (Test*Behavior):
    - TestConfigSetParamsBehavior
    - TestAssertMetricParamsBehavior
    - TestNotifySendParamsBehavior
    - TestEmergencyActivateParamsBoundaryBehavior
    - TestWaitStabilizeParamsBoundaryBehavior
    - TestHandleConfigSetBehavior
    - TestHandleAssertMetricBehavior
    - TestHandleNotifySendBehavior
    - TestHandleRecoveryStartBehavior
    - TestHandleEmergencyActivateBehavior
    - TestHandleEmergencyDeactivateBehavior
    - TestHandleWaitStabilizeBehavior
    - TestQueryMetricBehavior
    - TestRegisterBuiltinPrimitivesBehavior
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from selfhealing.services.runbook.primitives import (
    _OPERATOR_MAP,
    BUILTIN_PRIMITIVES,
    AssertMetricParams,
    ConfigSetParams,
    EmergencyActivateParams,
    NotifySendParams,
    RecoveryStartParams,
    WaitStabilizeParams,
    _handle_assert_metric,
    _handle_config_set,
    _handle_emergency_activate,
    _handle_emergency_deactivate,
    _handle_notify_send,
    _handle_recovery_start,
    _handle_wait_stabilize,
    _query_metric,
    register_builtin_primitives,
)
from selfhealing.services.runbook.runbook_registry import RunbookStepContext

# =============================================================================
# 테스트 헬퍼
# =============================================================================


def _make_ctx(
    params: dict | None = None,
    runbook_id: str = "rb_test",
    step_name: str = "step_1",
    initiated_by: str = "tester",
) -> RunbookStepContext:
    """최소 구성의 RunbookStepContext 생성."""
    return RunbookStepContext(
        runbook_id=runbook_id,
        step_name=step_name,
        params=params or {},
        prev_results={},
        execution_id="exec_001",
        initiated_by=initiated_by,
    )


# =============================================================================
# 계약 검증 — ConfigSetParams
# =============================================================================


class TestConfigSetParamsContract:
    """ConfigSetParams 설계 계약값 검증."""

    def test_value_field_default_is_none(self):
        """value 필드 기본값은 None."""
        p = ConfigSetParams(key="k", value_delta=1)
        assert p.value is None

    def test_value_delta_field_default_is_none(self):
        """value_delta 필드 기본값은 None."""
        p = ConfigSetParams(key="k", value="v")
        assert p.value_delta is None


# =============================================================================
# 계약 검증 — AssertMetricParams
# =============================================================================


class TestAssertMetricParamsContract:
    """AssertMetricParams 설계 계약값 검증."""

    def test_operator_literal_values(self):
        """operator 필드는 gt/lt/eq/gte/lte 중 하나만 허용."""
        for op in ("gt", "lt", "eq", "gte", "lte"):
            p = AssertMetricParams(metric_name="m", operator=op, threshold=1.0)
            assert p.operator == op


# =============================================================================
# 계약 검증 — NotifySendParams
# =============================================================================


class TestNotifySendParamsContract:
    """NotifySendParams 설계 계약값 검증."""

    def test_message_default_is_empty_string(self):
        """message 기본값은 빈 문자열."""
        p = NotifySendParams(title="t")
        assert p.message == ""

    def test_priority_default_is_medium(self):
        """priority 기본값은 'medium'."""
        p = NotifySendParams(title="t")
        assert p.priority == "medium"

    def test_priority_literal_values(self):
        """priority 필드는 low/medium/high/critical 중 하나만 허용."""
        for prio in ("low", "medium", "high", "critical"):
            p = NotifySendParams(title="t", priority=prio)
            assert p.priority == prio


# =============================================================================
# 계약 검증 — RecoveryStartParams
# =============================================================================


class TestRecoveryStartParamsContract:
    """RecoveryStartParams 설계 계약값 검증."""

    def test_namespace_default_is_global(self):
        """namespace 기본값은 'global'."""
        p = RecoveryStartParams(trigger_level="LEVEL_3")
        assert p.namespace == "global"


# =============================================================================
# 계약 검증 — EmergencyActivateParams
# =============================================================================


class TestEmergencyActivateParamsContract:
    """EmergencyActivateParams 설계 계약값 검증."""

    def test_level_field_constraint_ge_1_le_5(self):
        """level 필드는 ge=1, le=5 제약을 갖는다."""
        field_info = EmergencyActivateParams.model_fields["level"]
        metadata = field_info.metadata
        ge_val = None
        le_val = None
        for m in metadata:
            if hasattr(m, "ge"):
                ge_val = m.ge
            if hasattr(m, "le"):
                le_val = m.le
        assert ge_val == 1
        assert le_val == 5


# =============================================================================
# 계약 검증 — WaitStabilizeParams
# =============================================================================


class TestWaitStabilizeParamsContract:
    """WaitStabilizeParams 설계 계약값 검증."""

    def test_seconds_field_constraint_ge_1_le_3600(self):
        """seconds 필드는 ge=1, le=3600 제약을 갖는다."""
        field_info = WaitStabilizeParams.model_fields["seconds"]
        metadata = field_info.metadata
        ge_val = None
        le_val = None
        for m in metadata:
            if hasattr(m, "ge"):
                ge_val = m.ge
            if hasattr(m, "le"):
                le_val = m.le
        assert ge_val == 1
        assert le_val == 3600

    def test_assert_metric_default_is_none(self):
        """assert_metric 기본값은 None."""
        p = WaitStabilizeParams(seconds=10)
        assert p.assert_metric is None

    def test_threshold_default_is_none(self):
        """threshold 기본값은 None."""
        p = WaitStabilizeParams(seconds=10)
        assert p.threshold is None


# =============================================================================
# 계약 검증 — BUILTIN_PRIMITIVES 매핑
# =============================================================================


class TestBuiltinPrimitivesMapContract:
    """BUILTIN_PRIMITIVES 매핑 테이블 계약 검증."""

    def test_builtin_count_is_seven(self):
        """빌트인 프리미티브는 7개."""
        assert len(BUILTIN_PRIMITIVES) == 7

    def test_all_expected_action_names_registered(self):
        """7개 action name이 모두 등록되어 있다."""
        expected = {
            "config.set",
            "assert.metric",
            "notify.send",
            "recovery.start",
            "emergency.activate",
            "emergency.deactivate",
            "wait.stabilize",
        }
        assert set(BUILTIN_PRIMITIVES.keys()) == expected

    def test_config_set_maps_to_correct_handler_and_schema(self):
        """config.set은 _handle_config_set + ConfigSetParams."""
        handler, schema = BUILTIN_PRIMITIVES["config.set"]
        assert handler is _handle_config_set
        assert schema is ConfigSetParams

    def test_assert_metric_maps_to_correct_handler_and_schema(self):
        """assert.metric은 _handle_assert_metric + AssertMetricParams."""
        handler, schema = BUILTIN_PRIMITIVES["assert.metric"]
        assert handler is _handle_assert_metric
        assert schema is AssertMetricParams

    def test_notify_send_maps_to_correct_handler_and_schema(self):
        """notify.send는 _handle_notify_send + NotifySendParams."""
        handler, schema = BUILTIN_PRIMITIVES["notify.send"]
        assert handler is _handle_notify_send
        assert schema is NotifySendParams

    def test_recovery_start_maps_to_correct_handler_and_schema(self):
        """recovery.start는 _handle_recovery_start + RecoveryStartParams."""
        handler, schema = BUILTIN_PRIMITIVES["recovery.start"]
        assert handler is _handle_recovery_start
        assert schema is RecoveryStartParams

    def test_emergency_activate_maps_to_correct_handler_and_schema(self):
        """emergency.activate는 _handle_emergency_activate + EmergencyActivateParams."""
        handler, schema = BUILTIN_PRIMITIVES["emergency.activate"]
        assert handler is _handle_emergency_activate
        assert schema is EmergencyActivateParams

    def test_emergency_deactivate_maps_to_handler_with_no_schema(self):
        """emergency.deactivate는 _handle_emergency_deactivate + None (스키마 없음)."""
        handler, schema = BUILTIN_PRIMITIVES["emergency.deactivate"]
        assert handler is _handle_emergency_deactivate
        assert schema is None

    def test_wait_stabilize_maps_to_correct_handler_and_schema(self):
        """wait.stabilize는 _handle_wait_stabilize + WaitStabilizeParams."""
        handler, schema = BUILTIN_PRIMITIVES["wait.stabilize"]
        assert handler is _handle_wait_stabilize
        assert schema is WaitStabilizeParams


# =============================================================================
# 계약 검증 — _OPERATOR_MAP
# =============================================================================


class TestOperatorMapContract:
    """_OPERATOR_MAP 매핑 계약 검증."""

    def test_operator_map_keys(self):
        """_OPERATOR_MAP은 gt/lt/eq/gte/lte 5개 키를 갖는다."""
        assert set(_OPERATOR_MAP.keys()) == {"gt", "lt", "eq", "gte", "lte"}

    def test_gt_operator_evaluates_correctly(self):
        """gt 연산자는 a > b."""
        assert _OPERATOR_MAP["gt"](10, 5) is True
        assert _OPERATOR_MAP["gt"](5, 10) is False

    def test_lt_operator_evaluates_correctly(self):
        """lt 연산자는 a < b."""
        assert _OPERATOR_MAP["lt"](5, 10) is True
        assert _OPERATOR_MAP["lt"](10, 5) is False

    def test_eq_operator_evaluates_correctly(self):
        """eq 연산자는 a == b."""
        assert _OPERATOR_MAP["eq"](5, 5) is True
        assert _OPERATOR_MAP["eq"](5, 6) is False

    def test_gte_operator_evaluates_correctly(self):
        """gte 연산자는 a >= b."""
        assert _OPERATOR_MAP["gte"](5, 5) is True
        assert _OPERATOR_MAP["gte"](6, 5) is True
        assert _OPERATOR_MAP["gte"](4, 5) is False

    def test_lte_operator_evaluates_correctly(self):
        """lte 연산자는 a <= b."""
        assert _OPERATOR_MAP["lte"](5, 5) is True
        assert _OPERATOR_MAP["lte"](4, 5) is True
        assert _OPERATOR_MAP["lte"](6, 5) is False


# =============================================================================
# 동작 검증 — ConfigSetParams
# =============================================================================


class TestConfigSetParamsBehavior:
    """ConfigSetParams 동작 검증."""

    def test_value_and_delta_both_none_raises_validation_error(self):
        """value와 value_delta 모두 None이면 ValidationError."""
        with pytest.raises(ValidationError, match="value 또는 value_delta"):
            ConfigSetParams(key="k")

    def test_value_only_creates_valid_params(self):
        """value만 제공하면 유효한 파라미터 생성."""
        p = ConfigSetParams(key="k", value="v")
        assert p.key == "k"
        assert p.value == "v"

    def test_value_delta_only_creates_valid_params(self):
        """value_delta만 제공하면 유효한 파라미터 생성."""
        p = ConfigSetParams(key="k", value_delta=10)
        assert p.value_delta == 10

    def test_both_value_and_delta_provided_is_valid(self):
        """value와 value_delta 모두 제공해도 유효."""
        p = ConfigSetParams(key="k", value="v", value_delta=5)
        assert p.value == "v"
        assert p.value_delta == 5


# =============================================================================
# 동작 검증 — AssertMetricParams
# =============================================================================


class TestAssertMetricParamsBehavior:
    """AssertMetricParams 동작 검증."""

    def test_invalid_operator_raises_validation_error(self):
        """허용되지 않는 operator는 ValidationError."""
        with pytest.raises(ValidationError):
            AssertMetricParams(metric_name="m", operator="ne", threshold=1.0)

    def test_missing_metric_name_raises_validation_error(self):
        """metric_name 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            AssertMetricParams(operator="gt", threshold=1.0)


# =============================================================================
# 동작 검증 — NotifySendParams
# =============================================================================


class TestNotifySendParamsBehavior:
    """NotifySendParams 동작 검증."""

    def test_invalid_priority_raises_validation_error(self):
        """허용되지 않는 priority는 ValidationError."""
        with pytest.raises(ValidationError):
            NotifySendParams(title="t", priority="urgent")

    def test_missing_title_raises_validation_error(self):
        """title 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            NotifySendParams()


# =============================================================================
# 동작 검증 — EmergencyActivateParams 경계값
# =============================================================================


class TestEmergencyActivateParamsBoundaryBehavior:
    """EmergencyActivateParams 경계값 동작 검증."""

    def test_level_below_minimum_raises_validation_error(self):
        """level=0 (경계 직전)은 ValidationError."""
        with pytest.raises(ValidationError):
            EmergencyActivateParams(level=0, reason="test")

    def test_level_at_minimum_is_valid(self):
        """level=1 (최소 경계)은 유효."""
        p = EmergencyActivateParams(level=1, reason="test")
        assert p.level == 1

    def test_level_at_maximum_is_valid(self):
        """level=5 (최대 경계)은 유효."""
        p = EmergencyActivateParams(level=5, reason="test")
        assert p.level == 5

    def test_level_above_maximum_raises_validation_error(self):
        """level=6 (경계 직후)은 ValidationError."""
        with pytest.raises(ValidationError):
            EmergencyActivateParams(level=6, reason="test")


# =============================================================================
# 동작 검증 — WaitStabilizeParams 경계값
# =============================================================================


class TestWaitStabilizeParamsBoundaryBehavior:
    """WaitStabilizeParams 경계값 동작 검증."""

    def test_seconds_below_minimum_raises_validation_error(self):
        """seconds=0 (경계 직전)은 ValidationError."""
        with pytest.raises(ValidationError):
            WaitStabilizeParams(seconds=0)

    def test_seconds_at_minimum_is_valid(self):
        """seconds=1 (최소 경계)은 유효."""
        p = WaitStabilizeParams(seconds=1)
        assert p.seconds == 1

    def test_seconds_at_maximum_is_valid(self):
        """seconds=3600 (최대 경계)은 유효."""
        p = WaitStabilizeParams(seconds=3600)
        assert p.seconds == 3600

    def test_seconds_above_maximum_raises_validation_error(self):
        """seconds=3601 (경계 직후)은 ValidationError."""
        with pytest.raises(ValidationError):
            WaitStabilizeParams(seconds=3601)


# =============================================================================
# 동작 검증 — _handle_config_set
# =============================================================================


class TestHandleConfigSetBehavior:
    """_handle_config_set 핸들러 동작 검증."""

    @patch(
        "selfhealing.services.runtime_config.get_runtime_config_manager",
    )
    def test_config_set_with_value_calls_update_config(self, mock_get_manager):
        """value 제공 시 _update_config를 호출한다."""
        mock_manager = MagicMock()
        mock_manager._update_config.return_value = {"updated": True}
        mock_get_manager.return_value = mock_manager

        ctx = _make_ctx(params={"key": "circuit_breaker.threshold", "value": 10})
        result = _handle_config_set(ctx)

        assert result.success is True
        assert result.data["config_type"] == "circuit_breaker"
        mock_manager._update_config.assert_called_once_with(
            "circuit_breaker",
            changed_by="tester",
            reason="runbook:rb_test",
            threshold=10,
        )

    @patch(
        "selfhealing.services.runtime_config.get_runtime_config_manager",
    )
    def test_config_set_with_value_delta_reads_current_and_adds(self, mock_get_manager):
        """value_delta 제공 시 현재값에 delta를 더하여 업데이트."""
        mock_manager = MagicMock()
        mock_manager._get_config.return_value = {"threshold": 5}
        mock_manager._update_config.return_value = {"updated": True}
        mock_get_manager.return_value = mock_manager

        ctx = _make_ctx(params={"key": "circuit_breaker.threshold", "value_delta": 3})
        result = _handle_config_set(ctx)

        assert result.success is True
        mock_manager._update_config.assert_called_once_with(
            "circuit_breaker",
            changed_by="tester",
            reason="runbook:rb_test",
            threshold=8,
        )

    @patch(
        "selfhealing.services.runtime_config.get_runtime_config_manager",
    )
    def test_config_set_key_without_dot_uses_general_config_type(
        self, mock_get_manager
    ):
        """key에 dot이 없으면 config_type='general'."""
        mock_manager = MagicMock()
        mock_manager._update_config.return_value = {"updated": True}
        mock_get_manager.return_value = mock_manager

        ctx = _make_ctx(params={"key": "threshold", "value": 42})
        result = _handle_config_set(ctx)

        assert result.success is True
        assert result.data["config_type"] == "general"

    def test_config_set_invalid_params_returns_failed(self):
        """파라미터 검증 실패 시 StepResult.failed 반환."""
        ctx = _make_ctx(params={"key": "k"})
        result = _handle_config_set(ctx)

        assert result.success is False
        assert result.error_code == "CONFIG_SET_ERROR"
        assert result.retryable is True

    @patch(
        "selfhealing.services.runtime_config.get_runtime_config_manager",
        side_effect=RuntimeError("connection lost"),
    )
    def test_config_set_exception_returns_failed_retryable(self, mock_get_manager):
        """내부 예외 발생 시 retryable=True인 실패 반환."""
        ctx = _make_ctx(params={"key": "cb.threshold", "value": 1})
        result = _handle_config_set(ctx)

        assert result.success is False
        assert result.error_code == "CONFIG_SET_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_assert_metric
# =============================================================================


class TestHandleAssertMetricBehavior:
    """_handle_assert_metric 핸들러 동작 검증."""

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    def test_assert_metric_passes_when_condition_met(self, mock_query):
        """메트릭이 조건을 만족하면 success=True."""
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={"metric_name": "cpu_usage", "operator": "gt", "threshold": 50.0}
        )
        result = _handle_assert_metric(ctx)

        assert result.success is True
        assert result.data["passed"] is True
        assert result.data["value"] == 95.0

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    def test_assert_metric_fails_when_condition_not_met(self, mock_query):
        """메트릭이 조건을 만족하지 않으면 ASSERT_METRIC_FAILED."""
        mock_query.return_value = 30.0

        ctx = _make_ctx(
            params={"metric_name": "cpu_usage", "operator": "gt", "threshold": 50.0}
        )
        result = _handle_assert_metric(ctx)

        assert result.success is False
        assert result.error_code == "ASSERT_METRIC_FAILED"

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    def test_assert_metric_not_found_returns_retryable_failed(self, mock_query):
        """메트릭 조회 결과가 None이면 METRIC_NOT_FOUND + retryable."""
        mock_query.return_value = None

        ctx = _make_ctx(
            params={"metric_name": "unknown", "operator": "gt", "threshold": 1.0}
        )
        result = _handle_assert_metric(ctx)

        assert result.success is False
        assert result.error_code == "METRIC_NOT_FOUND"
        assert result.retryable is True

    def test_assert_metric_invalid_params_returns_error(self):
        """잘못된 파라미터는 ASSERT_METRIC_ERROR."""
        ctx = _make_ctx(params={"metric_name": "m"})
        result = _handle_assert_metric(ctx)

        assert result.success is False
        assert result.error_code == "ASSERT_METRIC_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_notify_send
# =============================================================================


class TestHandleNotifySendBehavior:
    """_handle_notify_send 핸들러 동작 검증."""

    @patch(
        "selfhealing.services.unified_notification.service.UnifiedNotificationManager",
    )
    @patch(
        "selfhealing.services.unified_notification.models.NotificationPayload",
    )
    @patch(
        "selfhealing.services.unified_notification.models.NotificationPriority",
    )
    @patch(
        "selfhealing.services.unified_notification.models.NotificationCategory",
    )
    def test_notify_send_success_calls_manager(
        self, mock_category, mock_priority, mock_payload_cls, mock_manager_cls
    ):
        """알림 발송 성공 시 manager.notify()를 호출한다."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.suppressed = False
        mock_manager_cls.return_value.notify.return_value = mock_result

        mock_priority.LOW = "low"
        mock_priority.MEDIUM = "medium"
        mock_priority.HIGH = "high"
        mock_priority.CRITICAL = "critical"
        mock_category.OPERATIONS = "operations"

        ctx = _make_ctx(params={"title": "Test Alert", "message": "Something happened"})
        result = _handle_notify_send(ctx)

        assert result.success is True
        assert result.data["notified"] is True
        mock_manager_cls.return_value.notify.assert_called_once()

    def test_notify_send_invalid_params_returns_error(self):
        """파라미터 누락 시 NOTIFY_SEND_ERROR."""
        ctx = _make_ctx(params={})
        result = _handle_notify_send(ctx)

        assert result.success is False
        assert result.error_code == "NOTIFY_SEND_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_recovery_start
# =============================================================================


class TestHandleRecoveryStartBehavior:
    """_handle_recovery_start 핸들러 동작 검증."""

    @patch(
        "selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator",
    )
    def test_recovery_start_success_returns_session_id(self, mock_coordinator_cls):
        """복구 시작 성공 시 session_id를 반환한다."""
        mock_session = MagicMock()
        mock_session.session_id = "session_123"
        mock_coordinator_cls.return_value.start_recovery.return_value = mock_session

        ctx = _make_ctx(params={"namespace": "db", "trigger_level": "LEVEL_3"})
        result = _handle_recovery_start(ctx)

        assert result.success is True
        assert result.data["session_id"] == "session_123"
        assert result.data["namespace"] == "db"

    @patch(
        "selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator",
    )
    def test_recovery_start_uses_default_namespace(self, mock_coordinator_cls):
        """namespace 미지정 시 'global' 기본값 사용."""
        mock_session = MagicMock()
        mock_session.session_id = "s1"
        mock_coordinator_cls.return_value.start_recovery.return_value = mock_session

        ctx = _make_ctx(params={"trigger_level": "LEVEL_1"})
        result = _handle_recovery_start(ctx)

        assert result.success is True
        mock_coordinator_cls.return_value.start_recovery.assert_called_once_with(
            namespace="global",
            trigger_level="LEVEL_1",
            initiated_by="tester",
        )

    @patch(
        "selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator",
    )
    def test_recovery_start_value_error_returns_rejected(self, mock_coordinator_cls):
        """ValueError 발생 시 RECOVERY_START_REJECTED (not retryable)."""
        mock_coordinator_cls.return_value.start_recovery.side_effect = ValueError(
            "already in recovery"
        )

        ctx = _make_ctx(params={"trigger_level": "LEVEL_3"})
        result = _handle_recovery_start(ctx)

        assert result.success is False
        assert result.error_code == "RECOVERY_START_REJECTED"
        assert result.retryable is False

    @patch(
        "selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator",
    )
    def test_recovery_start_generic_exception_returns_retryable(
        self, mock_coordinator_cls
    ):
        """일반 예외 발생 시 retryable=True."""
        mock_coordinator_cls.return_value.start_recovery.side_effect = RuntimeError(
            "connection error"
        )

        ctx = _make_ctx(params={"trigger_level": "LEVEL_3"})
        result = _handle_recovery_start(ctx)

        assert result.success is False
        assert result.error_code == "RECOVERY_START_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_emergency_activate
# =============================================================================


class TestHandleEmergencyActivateBehavior:
    """_handle_emergency_activate 핸들러 동작 검증."""

    @patch(
        "selfhealing.services.emergency_mode.manager.GracefulDegradationManager",
    )
    @patch("selfhealing.services.emergency_mode.enums.EmergencyLevel")
    def test_emergency_activate_level_1_succeeds(
        self, mock_level_enum, mock_manager_cls
    ):
        """level=1 활성화 성공."""
        mock_level_enum.LEVEL_1 = "LEVEL_1"
        mock_level_enum.LEVEL_2 = "LEVEL_2"
        mock_level_enum.LEVEL_3 = "LEVEL_3"

        mock_state = MagicMock()
        mock_state.level.value = 1
        mock_manager_cls.return_value.activate_auto.return_value = mock_state

        ctx = _make_ctx(params={"level": 1, "reason": "high load"})
        result = _handle_emergency_activate(ctx)

        assert result.success is True
        assert result.data["activated_level"] == 1

    @patch(
        "selfhealing.services.emergency_mode.manager.GracefulDegradationManager",
    )
    @patch("selfhealing.services.emergency_mode.enums.EmergencyLevel")
    def test_emergency_activate_unsupported_level_returns_invalid(
        self, mock_level_enum, mock_manager_cls
    ):
        """지원하지 않는 레벨(4, 5)은 INVALID_EMERGENCY_LEVEL."""
        mock_level_enum.LEVEL_1 = "LEVEL_1"
        mock_level_enum.LEVEL_2 = "LEVEL_2"
        mock_level_enum.LEVEL_3 = "LEVEL_3"

        ctx = _make_ctx(params={"level": 4, "reason": "test"})
        result = _handle_emergency_activate(ctx)

        assert result.success is False
        assert result.error_code == "INVALID_EMERGENCY_LEVEL"

    def test_emergency_activate_invalid_params_returns_error(self):
        """파라미터 검증 실패 시 EMERGENCY_ACTIVATE_ERROR."""
        ctx = _make_ctx(params={"level": 0, "reason": "test"})
        result = _handle_emergency_activate(ctx)

        assert result.success is False
        assert result.error_code == "EMERGENCY_ACTIVATE_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_emergency_deactivate
# =============================================================================


class TestHandleEmergencyDeactivateBehavior:
    """_handle_emergency_deactivate 핸들러 동작 검증."""

    @patch(
        "selfhealing.services.emergency_mode.manager.GracefulDegradationManager",
    )
    def test_emergency_deactivate_success(self, mock_manager_cls):
        """비상 모드 해제 성공 시 deactivated=True 반환."""
        mock_state = MagicMock()
        mock_state.level.value = 0
        mock_manager_cls.return_value.deactivate.return_value = mock_state

        ctx = _make_ctx(params={})
        result = _handle_emergency_deactivate(ctx)

        assert result.success is True
        assert result.data["deactivated"] is True
        mock_manager_cls.return_value.deactivate.assert_called_once_with(
            deactivated_by="tester",
            reason="runbook:rb_test",
        )

    @patch(
        "selfhealing.services.emergency_mode.manager.GracefulDegradationManager",
    )
    def test_emergency_deactivate_exception_returns_retryable(self, mock_manager_cls):
        """예외 발생 시 retryable=True인 실패 반환."""
        mock_manager_cls.return_value.deactivate.side_effect = RuntimeError("fail")

        ctx = _make_ctx(params={})
        result = _handle_emergency_deactivate(ctx)

        assert result.success is False
        assert result.error_code == "EMERGENCY_DEACTIVATE_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _handle_wait_stabilize
# =============================================================================


class TestHandleWaitStabilizeBehavior:
    """_handle_wait_stabilize 핸들러 동작 검증."""

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_wait_stabilize_basic_waits_and_succeeds(self, mock_sleep, mock_query):
        """기본 대기 후 성공 반환."""
        ctx = _make_ctx(params={"seconds": 10})
        result = _handle_wait_stabilize(ctx)

        assert result.success is True
        assert result.data["waited_seconds"] == 10
        mock_sleep.assert_called_once_with(10)

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_wait_stabilize_with_metric_check_passes(self, mock_sleep, mock_query):
        """대기 후 메트릭 검증 통과 시 성공."""
        mock_query.return_value = 95.0

        ctx = _make_ctx(
            params={
                "seconds": 5,
                "assert_metric": "health_score",
                "threshold": 80.0,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is True
        mock_query.assert_called_once_with("health_score")

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_wait_stabilize_metric_below_threshold_fails(self, mock_sleep, mock_query):
        """대기 후 메트릭이 threshold 미만이면 WAIT_STABILIZE_FAILED."""
        mock_query.return_value = 50.0

        ctx = _make_ctx(
            params={
                "seconds": 5,
                "assert_metric": "health_score",
                "threshold": 80.0,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_STABILIZE_FAILED"

    @patch("selfhealing.services.runbook.primitives._query_metric", autospec=True)
    @patch("time.sleep")
    def test_wait_stabilize_metric_not_found_returns_retryable(
        self, mock_sleep, mock_query
    ):
        """대기 후 메트릭 미발견 시 WAIT_METRIC_NOT_FOUND + retryable."""
        mock_query.return_value = None

        ctx = _make_ctx(
            params={
                "seconds": 5,
                "assert_metric": "unknown",
                "threshold": 80.0,
            }
        )
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_METRIC_NOT_FOUND"
        assert result.retryable is True

    def test_wait_stabilize_invalid_params_returns_error(self):
        """seconds 범위 밖이면 WAIT_STABILIZE_ERROR."""
        ctx = _make_ctx(params={"seconds": 0})
        result = _handle_wait_stabilize(ctx)

        assert result.success is False
        assert result.error_code == "WAIT_STABILIZE_ERROR"
        assert result.retryable is True


# =============================================================================
# 동작 검증 — _query_metric
# =============================================================================


class TestQueryMetricBehavior:
    """_query_metric 헬퍼 동작 검증."""

    @patch("selfhealing.factory.ProviderRegistry")
    def test_query_metric_uses_provider_registry_first(self, mock_registry_cls):
        """ProviderRegistry의 runbook_metrics_provider를 우선 사용."""
        mock_provider = MagicMock()
        mock_provider.query.return_value = 42.0
        mock_registry_cls.get.return_value = mock_provider

        result = _query_metric("test_metric")

        assert result == 42.0
        mock_registry_cls.get.assert_called_once_with("runbook_metrics_provider")

    @patch("selfhealing.factory.ProviderRegistry")
    def test_query_metric_provider_returns_none_falls_through(self, mock_registry_cls):
        """ProviderRegistry가 None을 반환하면 폴백 경로로 진행."""
        mock_registry_cls.get.return_value = None

        # get_metric_value도 존재하지 않으므로 최종적으로 None 반환
        result = _query_metric("test_metric")

        assert result is None

    @patch("selfhealing.factory.ProviderRegistry")
    def test_query_metric_returns_none_when_all_fail(self, mock_registry_cls):
        """모든 소스 실패 시 None 반환."""
        mock_registry_cls.get.side_effect = Exception("fail")

        result = _query_metric("nonexistent")

        assert result is None


# =============================================================================
# 동작 검증 — register_builtin_primitives
# =============================================================================


class TestRegisterBuiltinPrimitivesBehavior:
    """register_builtin_primitives 동작 검증."""

    def test_register_with_none_registry_does_not_raise(self):
        """registry=None이면 예외 없이 조기 반환."""
        register_builtin_primitives(None)

    def test_register_calls_registry_for_all_builtins(self):
        """모든 빌트인 프리미티브가 registry.register()로 등록된다."""
        mock_registry = MagicMock()
        register_builtin_primitives(mock_registry)

        assert mock_registry.register.call_count == len(BUILTIN_PRIMITIVES)

        registered_names = {
            call.args[0] for call in mock_registry.register.call_args_list
        }
        assert registered_names == set(BUILTIN_PRIMITIVES.keys())

    def test_register_passes_correct_handler_and_schema(self):
        """각 빌트인의 handler와 schema가 정확히 전달된다."""
        mock_registry = MagicMock()
        register_builtin_primitives(mock_registry)

        for call in mock_registry.register.call_args_list:
            action_name, handler, schema = call.args
            expected_handler, expected_schema = BUILTIN_PRIMITIVES[action_name]
            assert handler is expected_handler
            assert schema is expected_schema

    def test_register_is_idempotent(self):
        """동일 registry에 2회 호출해도 정상 동작 (멱등성)."""
        mock_registry = MagicMock()
        register_builtin_primitives(mock_registry)
        register_builtin_primitives(mock_registry)

        assert mock_registry.register.call_count == len(BUILTIN_PRIMITIVES) * 2
