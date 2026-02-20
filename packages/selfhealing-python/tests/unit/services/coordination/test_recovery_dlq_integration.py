"""
Unit tests for Recovery Session 실패 시 DLQ 자동 연동.

테스트 대상:
- _fail_session()에서 DLQ 저장 호출 (1건 Aggregation)
- Fail-Open: DLQ 저장 실패해도 세션 FAILED 상태 정상 처리
- PII 마스킹된 스냅샷 저장
- status 기반 Failed Step 탐색
- 보상 실패 Aggregation (N건 → DLQ 1건)
- compensation_summary 요약 문자열
- recommended_action 동적 분기
- next_action_hint에 result_data 키 포함
- 호출 순서: logger.error → Lock 해제 → DLQ 저장
- entity_id == session.id
- 마스킹이 원본 객체에 영향 없음
"""

import pytest
from unittest.mock import MagicMock, patch, call

from selfhealing.core.state_backend import MemoryStateBackend
from selfhealing.services.coordination.enums import (
    CompensationStatus,
    RecoveryStatus,
)
from selfhealing.services.coordination.recovery_state import (
    CompensationResult,
    RecoverySession,
    RecoveryStep,
    RecoveryStepType,
)
from selfhealing.services.coordination.distributed_recovery_lock import (
    InMemoryRecoveryLock,
)
from selfhealing.services.coordination.recovery_coordinator import (
    RecoveryCoordinator,
)


# =========================================================================
# Helper: _fail_session()을 직접 호출하기 위한 세션 + 코디네이터 생성
# =========================================================================


def _make_coordinator(*, step_handler=None, compensate_handler=None):
    """테스트용 코디네이터 생성."""
    backend = MemoryStateBackend()
    lock = InMemoryRecoveryLock()
    coord = RecoveryCoordinator(
        backend=backend,
        recovery_lock=lock,
        use_idempotent_handlers=False,
        use_regional_policy=False,
    )
    if step_handler:
        for step_type, handler in step_handler.items():
            compensate = (compensate_handler or {}).get(step_type)
            coord.register_step_handler(step_type, handler, compensate=compensate)
    # compensate_handler에만 있고 step_handler에는 없는 경우 (이론상 없지만 안전)
    if compensate_handler:
        for step_type, handler in compensate_handler.items():
            if step_type not in (step_handler or {}):
                coord._compensate_handlers[step_type] = handler
    return coord


def _make_failed_session(coordinator, *, namespace="global", trigger_level="LEVEL_3"):
    """복구 시작 후 첫 번째 Step 실패시킨 세션 반환."""
    session = coordinator.start_recovery(
        namespace=namespace,
        trigger_level=trigger_level,
    )
    coordinator.execute_next_step(namespace)
    return coordinator.get_active_session(namespace)


# =========================================================================
# Behavior Tests — _fail_session() DLQ 자동 저장 동작 검증
# =========================================================================


class TestFailSessionDlqStoreBehavior:
    """_fail_session() DLQ 자동 저장 동작 검증."""

    def test_fail_session_stores_to_dlq(self):
        """_fail_session() 호출 시 DLQ에 엔트리 1건 생성되어야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "step failure",
                },
            },
        )

        with patch(
            "selfhealing.services.coordination.recovery_coordinator.RecoveryCoordinator._store_failure_to_dlq"
        ) as mock_store:
            _make_failed_session(coord)

            assert mock_store.call_count == 1
            args = mock_store.call_args
            session_arg = args[0][0]
            error_arg = args[0][1]
            comp_result_arg = args[0][2]

            assert session_arg.status == RecoveryStatus.FAILED
            assert error_arg == "step failure"
            assert isinstance(comp_result_arg, CompensationResult)

    def test_fail_session_dlq_fail_open(self):
        """DLQ 저장 실패해도 세션 FAILED 상태가 정상 처리되어야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "step failure",
                },
            },
        )

        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            side_effect=RuntimeError("DLQ unavailable"),
        ):
            session = _make_failed_session(coord)

        assert session.status == RecoveryStatus.FAILED
        assert session.completed_at is not None

    def test_fail_session_dlq_disabled(self):
        """DLQ import 실패 시 예외 없이 진행되어야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "step failure",
                },
            },
        )

        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            side_effect=ImportError("No module named dlq"),
        ):
            session = _make_failed_session(coord)

        assert session.status == RecoveryStatus.FAILED

    def test_dlq_entry_entity_id_is_session_id(self):
        """entity_id가 세션 ID와 동일해야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "test error",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            session = _make_failed_session(coord)

        mock_dlq.assert_called_once()
        call_kwargs = mock_dlq.call_args[1]
        assert call_kwargs["entity_id"] == session.id
        assert call_kwargs["domain"] == "selfhealing"
        assert call_kwargs["failure_type"] == "RECOVERY_SESSION_FAILED"
        assert call_kwargs["entity_type"] == "recovery_session"


class TestDlqEntrySnapshotBehavior:
    """DLQ 엔트리 스냅샷 동작 검증."""

    def test_dlq_entry_contains_masked_snapshot(self):
        """snapshot_data에 PII 마스킹된 세션 상태가 포함되어야 한다."""

        def failing_handler(session, step):
            step.params["api_key"] = "secret-key-123"
            return {"success": False, "error": "test error"}

        coord = _make_coordinator(
            step_handler={RecoveryStepType.BUDGET_RESET: failing_handler},
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            _make_failed_session(coord)

        call_kwargs = mock_dlq.call_args[1]
        snapshot = call_kwargs["snapshot_data"]["session"]

        # 세션 기본 정보가 포함되어야 함
        assert "id" in snapshot
        assert "namespace" in snapshot
        assert "status" in snapshot

        # PII 마스킹 확인: api_key가 마스킹됨
        step_data = snapshot["steps"][0]
        assert step_data["params"]["api_key"] == "***REDACTED***"

    def test_mask_sensitive_fields_no_side_effect(self):
        """마스킹이 원본 세션 객체에 영향을 주지 않아야 한다."""

        def handler_with_sensitive(session, step):
            step.params["token"] = "bearer-xyz"
            return {"success": False, "error": "test error"}

        coord = _make_coordinator(
            step_handler={RecoveryStepType.BUDGET_RESET: handler_with_sensitive},
        )

        with patch("selfhealing.services.dlq.store_to_dlq"):
            session = _make_failed_session(coord)

        # 원본 세션의 params는 마스킹되지 않아야 함
        original_step = session.steps[0]
        assert original_step.params["token"] == "bearer-xyz"


class TestDlqEntryFailedStepBehavior:
    """DLQ 엔트리 Failed Step 탐색 동작 검증."""

    def test_dlq_entry_contains_failed_step_by_status(self):
        """metadata.failed_step이 status == FAILED 기반 탐색 결과여야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "budget reset failed",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            _make_failed_session(coord)

        call_kwargs = mock_dlq.call_args[1]
        metadata = call_kwargs["metadata"]
        failed_step = metadata["failed_step"]

        assert failed_step is not None
        assert failed_step["step_type"] == RecoveryStepType.BUDGET_RESET.value
        assert failed_step["error_message"] == "budget reset failed"
        assert "order" in failed_step
        assert "started_at" in failed_step
        assert "params" in failed_step


class TestDlqCompensationAggregationBehavior:
    """보상 실패 Aggregation 동작 검증."""

    def _make_multi_step_failure_coordinator(
        self,
        *,
        num_success_steps=2,
        compensate_errors=None,
    ):
        """여러 Step 성공 후 실패, 보상도 실패하는 코디네이터 생성."""
        call_count = {"n": 0}

        def step_handler(session, step):
            call_count["n"] += 1
            if call_count["n"] <= num_success_steps:
                return {"success": True, "multiplier": 1.0}
            return {"success": False, "error": "stability check failed"}

        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: step_handler,
                RecoveryStepType.HEALTH_CHECK: step_handler,
                RecoveryStepType.CANARY_RESUME: step_handler,
                RecoveryStepType.GOVERNANCE_NORMAL: step_handler,
            },
        )

        if compensate_errors:
            for step_type, err_msg in compensate_errors.items():
                coord._compensate_handlers[step_type] = lambda s, st, err=err_msg: {"success": False, "error": err}

        return coord

    def test_dlq_entry_aggregates_compensation_failures(self):
        """보상 실패 N건 → DLQ 1건, metadata.compensation_failures에 N항목이어야 한다."""
        coord = self._make_multi_step_failure_coordinator(
            num_success_steps=2,
            compensate_errors={
                RecoveryStepType.BUDGET_RESET: "Provider unavailable",
                RecoveryStepType.HEALTH_CHECK: "Connection timeout",
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            session = coord.start_recovery(namespace="global", trigger_level="LEVEL_3")
            while coord.execute_next_step("global") is not None:
                pass

        # DLQ는 정확히 1번만 호출
        mock_dlq.assert_called_once()
        call_kwargs = mock_dlq.call_args[1]
        metadata = call_kwargs["metadata"]

        comp_failures = metadata["compensation_failures"]
        assert len(comp_failures) == 2

        step_types = {f["step_type"] for f in comp_failures}
        assert RecoveryStepType.BUDGET_RESET.value in step_types
        assert RecoveryStepType.HEALTH_CHECK.value in step_types

        # 각 항목에 필수 키가 있어야 함
        for failure in comp_failures:
            assert "compensation_error" in failure
            assert "forward_result" in failure
            assert "order" in failure

    def test_dlq_entry_compensation_summary(self):
        """metadata.compensation_summary에 요약 문자열이 포함되어야 한다."""
        coord = self._make_multi_step_failure_coordinator(
            num_success_steps=1,
            compensate_errors={
                RecoveryStepType.BUDGET_RESET: "Provider timeout",
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            coord.start_recovery(namespace="global", trigger_level="LEVEL_3")
            while coord.execute_next_step("global") is not None:
                pass

        call_kwargs = mock_dlq.call_args[1]
        metadata = call_kwargs["metadata"]

        summary = metadata["compensation_summary"]
        assert RecoveryStepType.BUDGET_RESET.value in summary
        assert "Provider timeout" in summary


class TestDlqRecommendedActionBehavior:
    """recommended_action 동적 분기 동작 검증."""

    def test_recommended_action_manual_review_without_compensation_failure(self):
        """보상 실패 없으면 recommended_action == 'manual_review'이어야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "test error",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            _make_failed_session(coord)

        call_kwargs = mock_dlq.call_args[1]
        assert call_kwargs["recommended_action"] == "manual_review"

    def test_recommended_action_manual_consistency_check_with_compensation_failure(
        self,
    ):
        """보상 실패 있으면 recommended_action == 'manual_consistency_check'이어야 한다."""
        call_count = {"n": 0}

        def step_handler(session, step):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return {"success": True, "multiplier": 1.0}
            return {"success": False, "error": "check failed"}

        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: step_handler,
                RecoveryStepType.HEALTH_CHECK: step_handler,
                RecoveryStepType.CANARY_RESUME: step_handler,
                RecoveryStepType.GOVERNANCE_NORMAL: step_handler,
            },
            compensate_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "compensation failed",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            coord.start_recovery(namespace="global", trigger_level="LEVEL_3")
            while coord.execute_next_step("global") is not None:
                pass

        call_kwargs = mock_dlq.call_args[1]
        assert call_kwargs["recommended_action"] == "manual_consistency_check"


class TestDlqNextActionHintBehavior:
    """next_action_hint 동작 검증."""

    def test_next_action_hint_includes_result_data_keys(self):
        """보상 실패 시 next_action_hint에 result_data 키가 포함되어야 한다."""
        call_count = {"n": 0}

        def step_handler(session, step):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return {"success": True, "multiplier": 1.0, "previous_value": 5.0}
            return {"success": False, "error": "check failed"}

        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: step_handler,
                RecoveryStepType.HEALTH_CHECK: step_handler,
                RecoveryStepType.CANARY_RESUME: step_handler,
                RecoveryStepType.GOVERNANCE_NORMAL: step_handler,
            },
            compensate_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "comp error",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            coord.start_recovery(namespace="global", trigger_level="LEVEL_3")
            while coord.execute_next_step("global") is not None:
                pass

        call_kwargs = mock_dlq.call_args[1]
        hint = call_kwargs["next_action_hint"]

        assert "Automatic compensation failed for:" in hint
        assert "budget_reset" in hint
        assert "affected:" in hint
        assert "Verify affected state manually before resume_recovery()." in hint

    def test_next_action_hint_without_compensation_failure(self):
        """보상 실패 없으면 next_action_hint에 step 이름과 안내가 포함되어야 한다."""
        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "step error",
                },
            },
        )

        with patch("selfhealing.services.dlq.store_to_dlq") as mock_dlq:
            _make_failed_session(coord)

        call_kwargs = mock_dlq.call_args[1]
        hint = call_kwargs["next_action_hint"]

        assert "failed at step" in hint
        assert "budget_reset" in hint
        assert "resume_recovery()" in hint


class TestDlqCallOrderBehavior:
    """DLQ 저장과 Lock 해제 호출 순서 동작 검증."""

    def test_dlq_stored_after_lock_release(self):
        """DLQ 저장이 Lock 해제 후 수행되어야 한다."""
        call_order = []

        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "test error",
                },
            },
        )

        original_release = coord._recovery_lock.release

        def tracking_release(*args, **kwargs):
            call_order.append("lock_release")
            return original_release(*args, **kwargs)

        coord._recovery_lock.release = tracking_release

        with patch(
            "selfhealing.services.dlq.store_to_dlq",
            side_effect=lambda **kwargs: call_order.append("dlq_store"),
        ):
            _make_failed_session(coord)

        assert "lock_release" in call_order
        assert "dlq_store" in call_order
        assert call_order.index("lock_release") < call_order.index("dlq_store")

    def test_logger_error_before_lock_release(self):
        """logger.error()가 Lock 해제 전에 호출되어야 한다."""
        call_order = []

        coord = _make_coordinator(
            step_handler={
                RecoveryStepType.BUDGET_RESET: lambda s, st: {
                    "success": False,
                    "error": "test error",
                },
            },
        )

        original_release = coord._recovery_lock.release

        def tracking_release(*args, **kwargs):
            call_order.append("lock_release")
            return original_release(*args, **kwargs)

        coord._recovery_lock.release = tracking_release

        with patch("selfhealing.services.coordination.recovery_coordinator._session_persistence.logger") as mock_logger:

            def tracking_error(*args, **kwargs):
                if args and "[Recovery] Failed:" in str(args[0]):
                    call_order.append("logger_error")

            mock_logger.error = MagicMock(side_effect=tracking_error)
            # logger.warning, logger.info 등은 기본 MagicMock으로 동작
            mock_logger.warning = MagicMock()
            mock_logger.info = MagicMock()
            mock_logger.debug = MagicMock()
            mock_logger.exception = MagicMock()

            with patch("selfhealing.services.dlq.store_to_dlq"):
                _make_failed_session(coord)

        assert "logger_error" in call_order
        assert "lock_release" in call_order
        assert call_order.index("logger_error") < call_order.index("lock_release")
