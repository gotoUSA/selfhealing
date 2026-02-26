"""
RunbookPlaybackRecorder 및 RecordingSummary 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.recorder
    selfhealing.services.runbook.execution_models.RecordingSummary

계약 검증 클래스 (Test*Contract):
    - TestRecordingSummaryDefaultContract — RecordingSummary 기본값 설계 계약
    - TestRunbookSensitiveKeysContract — RUNBOOK_SENSITIVE_KEYS 확장 키 계약
    - TestPayloadLimitsContract — MAX_ERROR_MESSAGE_LENGTH, TRUNCATION_MARKER 계약
    - TestSlowRecoverySlaRatioContract — SLOW_RECOVERY_SLA_RATIO 계약

동작 검증 클래스 (Test*Behavior):
    - TestRecordingSummarySerializationBehavior — to_dict 직렬화 왕복
    - TestRecordingSummaryAllRecordedBehavior — all_recorded 속성 동작
    - TestRecorderSanitizationBehavior — PII 마스킹 동작
    - TestRecorderCascadeChannelBehavior — Cascade Event 기록 동작
    - TestRecorderLearningChannelBehavior — Learning Service 피드백 동작
    - TestRecorderPostmortemChannelBehavior — Postmortem 인시던트 생성 동작
    - TestRecorderEventBusChannelBehavior — EventBus 이벤트 발행 동작
    - TestRecorderFailOpenBehavior — 채널 독립 Fail-Open 동작
    - TestRecorderDataImmutabilityBehavior — 원본 데이터 불변성 동작
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.runbook.execution_models import (
    CompensationSummary,
    RecordingSummary,
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)
from selfhealing.services.runbook.recorder import (
    MAX_ERROR_MESSAGE_LENGTH,
    RUNBOOK_SENSITIVE_KEYS,
    RunbookPlaybackRecorder,
    SLOW_RECOVERY_SLA_RATIO,
    TRUNCATION_MARKER,
)


# =============================================================================
# Test Fixtures
# =============================================================================


def _make_runbook(
    *,
    runbook_id: str = "test_runbook",
    name: str = "Test Runbook",
    version: int = 1,
    risk_level_value: str = "low",
    global_timeout_seconds: int | None = None,
    steps: list | None = None,
) -> Any:
    """테스트용 Runbook-like 객체 생성.

    실제 Runbook 생성에는 PatternCondition 등 복잡한 의존성이 필요하므로
    필요한 속성만 갖춘 간단한 객체를 사용한다.
    """

    @dataclass
    class _FakeRiskLevel:
        value: str

    @dataclass
    class _FakeStep:
        name: str = "step1"
        action: str = "test_action"

    @dataclass
    class _FakeRunbook:
        id: str = "test_runbook"
        name: str = "Test Runbook"
        description: str = "Test"
        version: int = 1
        risk_level: _FakeRiskLevel = field(default_factory=lambda: _FakeRiskLevel("low"))
        global_timeout_seconds: int | None = None
        steps: list = field(default_factory=lambda: [_FakeStep()])
        created_by: str = "system"
        tags: list = field(default_factory=list)

    actual_steps = steps if steps is not None else [_FakeStep()]
    return _FakeRunbook(
        id=runbook_id,
        name=name,
        version=version,
        risk_level=_FakeRiskLevel(risk_level_value),
        global_timeout_seconds=global_timeout_seconds,
        steps=actual_steps,
    )


def _make_ctx(
    *,
    execution_id: str = "exec-001",
    runbook_id: str = "test_runbook",
    namespace: str = "global",
    status: RunbookExecutionStatus = RunbookExecutionStatus.COMPLETED,
    trigger_event: dict | None = None,
    step_results: dict | None = None,
    started_at: str | None = "2026-02-26T10:00:00",
    completed_at: str | None = "2026-02-26T10:00:05",
    abort_reason: str | None = None,
    runbook_version: int = 1,
) -> RunbookExecutionContext:
    """테스트용 RunbookExecutionContext 생성."""
    return RunbookExecutionContext(
        execution_id=execution_id,
        runbook_id=runbook_id,
        namespace=namespace,
        trigger_event=trigger_event or {},
        runbook_version=runbook_version,
        step_results=step_results or {},
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        abort_reason=abort_reason,
    )


def _make_step_result(
    *,
    step_name: str = "step1",
    action_name: str = "test_action",
    success: bool = True,
    executed: bool = True,
    result_data: dict | None = None,
    error: str | None = None,
    idempotent: bool = False,
    partial_execution: bool = False,
    compensation_status: str = "not_needed",
) -> RunbookStepResult:
    """테스트용 RunbookStepResult 생성."""
    return RunbookStepResult(
        step_name=step_name,
        action_name=action_name,
        success=success,
        executed=executed,
        result_data=result_data or {},
        error=error,
        idempotent=idempotent,
        partial_execution=partial_execution,
        compensation_status=compensation_status,
    )


# =============================================================================
# 계약 검증 — RecordingSummary 기본값
# =============================================================================


class TestRecordingSummaryDefaultContract:
    """RecordingSummary 필드 기본값 설계 계약 검증."""

    def test_cascade_recorded_default_is_false(self):
        """cascade_recorded 기본값: False."""
        summary = RecordingSummary(execution_id="test")
        assert summary.cascade_recorded is False

    def test_pattern_recorded_default_is_false(self):
        """pattern_recorded 기본값: False."""
        summary = RecordingSummary(execution_id="test")
        assert summary.pattern_recorded is False

    def test_postmortem_recorded_default_is_false(self):
        """postmortem_recorded 기본값: False."""
        summary = RecordingSummary(execution_id="test")
        assert summary.postmortem_recorded is False

    def test_event_emitted_default_is_false(self):
        """event_emitted 기본값: False."""
        summary = RecordingSummary(execution_id="test")
        assert summary.event_emitted is False


# =============================================================================
# 계약 검증 — RUNBOOK_SENSITIVE_KEYS 확장 키
# =============================================================================


class TestRunbookSensitiveKeysContract:
    """RUNBOOK_SENSITIVE_KEYS 설계 계약값 검증."""

    def test_contains_standard_masking_keys(self):
        """audit/masking.py 기본 키가 모두 포함되어야 한다."""
        standard_keys = [
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "authorization",
            "auth",
            "credential",
            "private_key",
            "credit_card",
            "ssn",
            "social_security",
        ]
        for key in standard_keys:
            assert key in RUNBOOK_SENSITIVE_KEYS

    def test_contains_infrastructure_keys(self):
        """Runbook 도메인 인프라 접속 문자열 키가 포함되어야 한다."""
        infra_keys = [
            "connection_string",
            "dsn",
            "database_url",
            "db_password",
            "redis_url",
            "broker_url",
            "smtp_password",
        ]
        for key in infra_keys:
            assert key in RUNBOOK_SENSITIVE_KEYS

    def test_sensitive_keys_count(self):
        """확장 키는 총 19개."""
        assert len(RUNBOOK_SENSITIVE_KEYS) == 19


# =============================================================================
# 계약 검증 — 페이로드 크기 제한
# =============================================================================


class TestPayloadLimitsContract:
    """이벤트 페이로드 크기 제한 설계 계약값 검증."""

    def test_max_error_message_length(self):
        """MAX_ERROR_MESSAGE_LENGTH: 200."""
        assert MAX_ERROR_MESSAGE_LENGTH == 200

    def test_truncation_marker_format(self):
        """TRUNCATION_MARKER는 execution_id DB 조회를 안내한다."""
        assert "TRUNCATED" in TRUNCATION_MARKER
        assert "execution_id" in TRUNCATION_MARKER


# =============================================================================
# 계약 검증 — SLA 비율
# =============================================================================


class TestSlowRecoverySlaRatioContract:
    """SLOW_RECOVERY_SLA_RATIO 설계 계약값 검증."""

    def test_sla_ratio_is_eighty_percent(self):
        """SLA 비율 임계값: 0.8 (80%)."""
        assert SLOW_RECOVERY_SLA_RATIO == 0.8


# =============================================================================
# 동작 검증 — RecordingSummary 직렬화
# =============================================================================


class TestRecordingSummarySerializationBehavior:
    """RecordingSummary 직렬화 왕복 검증."""

    def test_to_dict_contains_all_fields(self):
        """to_dict()에는 6개 필드가 모두 포함된다."""
        summary = RecordingSummary(
            execution_id="exec-001",
            cascade_recorded=True,
            pattern_recorded=True,
            postmortem_recorded=False,
            event_emitted=True,
        )
        data = summary.to_dict()

        assert data["execution_id"] == "exec-001"
        assert data["cascade_recorded"] is True
        assert data["pattern_recorded"] is True
        assert data["postmortem_recorded"] is False
        assert data["event_emitted"] is True
        assert data["all_recorded"] is True

    def test_to_dict_keys_match_contract(self):
        """직렬화된 딕셔너리의 키 이름이 계약과 일치한다."""
        summary = RecordingSummary(execution_id="test")
        data = summary.to_dict()
        expected_keys = {
            "execution_id",
            "cascade_recorded",
            "pattern_recorded",
            "postmortem_recorded",
            "event_emitted",
            "all_recorded",
        }
        assert set(data.keys()) == expected_keys


# =============================================================================
# 동작 검증 — RecordingSummary.all_recorded
# =============================================================================


class TestRecordingSummaryAllRecordedBehavior:
    """RecordingSummary.all_recorded 속성 동작 검증."""

    def test_all_recorded_true_when_three_required_channels_succeed(self):
        """cascade + pattern + event_bus 성공 시 all_recorded=True."""
        summary = RecordingSummary(
            execution_id="test",
            cascade_recorded=True,
            pattern_recorded=True,
            event_emitted=True,
        )
        assert summary.all_recorded is True

    def test_all_recorded_true_even_without_postmortem(self):
        """postmortem이 False여도 all_recorded에 영향 없다 (성공 시에는 없음)."""
        summary = RecordingSummary(
            execution_id="test",
            cascade_recorded=True,
            pattern_recorded=True,
            postmortem_recorded=False,
            event_emitted=True,
        )
        assert summary.all_recorded is True

    def test_all_recorded_false_when_cascade_fails(self):
        """cascade 실패 시 all_recorded=False."""
        summary = RecordingSummary(
            execution_id="test",
            cascade_recorded=False,
            pattern_recorded=True,
            event_emitted=True,
        )
        assert summary.all_recorded is False

    def test_all_recorded_false_when_pattern_fails(self):
        """pattern 실패 시 all_recorded=False."""
        summary = RecordingSummary(
            execution_id="test",
            cascade_recorded=True,
            pattern_recorded=False,
            event_emitted=True,
        )
        assert summary.all_recorded is False

    def test_all_recorded_false_when_event_bus_fails(self):
        """event_bus 실패 시 all_recorded=False."""
        summary = RecordingSummary(
            execution_id="test",
            cascade_recorded=True,
            pattern_recorded=True,
            event_emitted=False,
        )
        assert summary.all_recorded is False


# =============================================================================
# 동작 검증 — PII 마스킹
# =============================================================================


class TestRecorderSanitizationBehavior:
    """RunbookPlaybackRecorder PII 마스킹 동작 검증."""

    def test_step_result_data_password_is_redacted(self):
        """result_data의 password 키 값이 마스킹된다."""
        step_results = {
            "step1": _make_step_result(
                result_data={"db_password": "super_secret_123"},
            ),
        }
        sanitized = RunbookPlaybackRecorder._sanitize_step_results(step_results)
        assert sanitized["step1"].result_data["db_password"] == "***REDACTED***"

    def test_step_result_preserves_non_sensitive_data(self):
        """민감하지 않은 데이터는 그대로 보존된다."""
        step_results = {
            "step1": _make_step_result(
                result_data={"status": "ok", "count": 42},
            ),
        }
        sanitized = RunbookPlaybackRecorder._sanitize_step_results(step_results)
        assert sanitized["step1"].result_data["status"] == "ok"
        assert sanitized["step1"].result_data["count"] == 42

    def test_step_result_nested_dict_password_is_redacted(self):
        """중첩 dict 안의 민감 키도 마스킹된다."""
        step_results = {
            "step1": _make_step_result(
                result_data={"config": {"redis_url": "redis://secret@host:6379"}},
            ),
        }
        sanitized = RunbookPlaybackRecorder._sanitize_step_results(step_results)
        assert sanitized["step1"].result_data["config"]["redis_url"] == "***REDACTED***"

    def test_sanitize_preserves_step_metadata(self):
        """마스킹 시 step_name, action_name 등 메타데이터는 보존된다."""
        step_results = {
            "step1": _make_step_result(
                step_name="step1",
                action_name="do_thing",
                success=True,
                executed=True,
                idempotent=True,
            ),
        }
        sanitized = RunbookPlaybackRecorder._sanitize_step_results(step_results)
        sanitized_step = sanitized["step1"]
        assert sanitized_step.step_name == "step1"
        assert sanitized_step.action_name == "do_thing"
        assert sanitized_step.success is True
        assert sanitized_step.executed is True
        assert sanitized_step.idempotent is True

    def test_trigger_event_masking_in_record(self):
        """record() 호출 시 trigger_event의 민감 데이터가 마스킹된다."""
        # Given
        mock_auditor = MagicMock()
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=mock_auditor,
            event_bus=MagicMock(),
        )
        ctx = _make_ctx(
            trigger_event={"api_key": "my-secret-key", "metric": "cpu"},
        )
        runbook = _make_runbook()

        # When
        recorder.record(ctx, runbook)

        # Then — cascade_auditor.record()에 전달된 trigger_details 확인
        call_kwargs = mock_auditor.record.call_args
        trigger_details = call_kwargs.kwargs.get("trigger_details") or call_kwargs[1].get("trigger_details")
        assert trigger_details["trigger_event"]["api_key"] == "***REDACTED***"
        assert trigger_details["trigger_event"]["metric"] == "cpu"


# =============================================================================
# 동작 검증 — Cascade Channel
# =============================================================================


class TestRecorderCascadeChannelBehavior:
    """RunbookPlaybackRecorder Cascade Event 기록 동작 검증."""

    def test_cascade_records_with_correct_trigger_type(self):
        """CascadeEventAuditor.record()가 trigger_type='RUNBOOK_EXECUTION'으로 호출된다."""
        # Given
        mock_auditor = MagicMock()
        recorder = RunbookPlaybackRecorder(cascade_auditor=mock_auditor)
        ctx = _make_ctx(
            step_results={
                "step1": _make_step_result(success=True),
            },
        )
        runbook = _make_runbook()

        # When
        result = recorder._record_cascade_event(
            ctx,
            runbook,
            ctx.step_results,
            ctx.trigger_event,
        )

        # Then
        assert result is True
        mock_auditor.record.assert_called_once()
        call_kwargs = mock_auditor.record.call_args
        assert call_kwargs.kwargs["trigger_type"] == "RUNBOOK_EXECUTION"
        assert call_kwargs.kwargs["triggered_by"] == "system:runbook_executor"
        assert call_kwargs.kwargs["namespace"] == "global"

    def test_cascade_forward_steps_have_step_action_prefix(self):
        """정방향 Step의 action_type은 'RUNBOOK_STEP:' 접두사를 갖는다."""
        # Given
        mock_auditor = MagicMock()
        recorder = RunbookPlaybackRecorder(cascade_auditor=mock_auditor)
        ctx = _make_ctx(
            step_results={
                "step1": _make_step_result(action_name="enable_cb"),
            },
        )
        runbook = _make_runbook()

        # When
        recorder._record_cascade_event(
            ctx,
            runbook,
            ctx.step_results,
            ctx.trigger_event,
        )

        # Then
        effects = mock_auditor.record.call_args.kwargs["effects"]
        assert effects[0]["action_type"] == "RUNBOOK_STEP:enable_cb"

    def test_cascade_compensation_steps_have_compensate_prefix(self):
        """보상 Step의 action_type은 'RUNBOOK_COMPENSATE:' 접두사를 갖는다."""
        # Given
        mock_auditor = MagicMock()
        recorder = RunbookPlaybackRecorder(cascade_auditor=mock_auditor)
        step_results = {
            "step1": _make_step_result(
                action_name="enable_cb",
                success=False,
                compensation_status="compensated",
            ),
        }
        ctx = _make_ctx(step_results=step_results, status=RunbookExecutionStatus.FAILED)
        runbook = _make_runbook()

        # When
        recorder._record_cascade_event(ctx, runbook, step_results, ctx.trigger_event)

        # Then
        effects = mock_auditor.record.call_args.kwargs["effects"]
        compensate_effects = [e for e in effects if "COMPENSATE" in e["action_type"]]
        assert len(compensate_effects) == 1
        assert compensate_effects[0]["action_type"] == "RUNBOOK_COMPENSATE:enable_cb"
        assert compensate_effects[0]["target"] == "compensate:step1"
        assert compensate_effects[0]["success"] is True

    def test_cascade_returns_false_when_auditor_is_none(self):
        """CascadeEventAuditor가 None이면 False를 반환한다."""
        recorder = RunbookPlaybackRecorder(cascade_auditor=None)
        ctx = _make_ctx()
        runbook = _make_runbook()
        result = recorder._record_cascade_event(
            ctx,
            runbook,
            {},
            {},
        )
        assert result is False

    def test_cascade_returns_false_on_exception(self):
        """CascadeEventAuditor.record()가 예외를 던지면 False를 반환한다."""
        # Given
        mock_auditor = MagicMock()
        mock_auditor.record.side_effect = RuntimeError("Redis down")
        recorder = RunbookPlaybackRecorder(cascade_auditor=mock_auditor)
        ctx = _make_ctx()
        runbook = _make_runbook()

        # When
        result = recorder._record_cascade_event(
            ctx,
            runbook,
            {},
            {},
        )

        # Then
        assert result is False

    def test_cascade_compensation_failed_step_has_error_message(self):
        """보상 실패 Step의 error_message에 에러가 포함된다."""
        mock_auditor = MagicMock()
        recorder = RunbookPlaybackRecorder(cascade_auditor=mock_auditor)
        step_results = {
            "step1": _make_step_result(
                action_name="do_thing",
                success=False,
                error="Compensation timed out",
                compensation_status="compensate_failed",
            ),
        }
        ctx = _make_ctx(step_results=step_results, status=RunbookExecutionStatus.FAILED)

        recorder._record_cascade_event(ctx, _make_runbook(), step_results, {})

        effects = mock_auditor.record.call_args.kwargs["effects"]
        compensate_effects = [e for e in effects if "COMPENSATE" in e["action_type"]]
        assert compensate_effects[0]["error_message"] == "Compensation timed out"
        assert compensate_effects[0]["success"] is False


# =============================================================================
# 동작 검증 — Learning Channel
# =============================================================================


class TestRecorderLearningChannelBehavior:
    """RunbookPlaybackRecorder Learning Service 피드백 동작 검증."""

    def test_success_records_recovery_pattern(self):
        """성공한 Runbook은 RECOVERY 패턴으로 학습된다."""
        # Given
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook(runbook_id="cb_recovery", version=2)

        # When
        result = recorder._record_learning_feedback(ctx, runbook)

        # Then
        assert result is True
        call_kwargs = mock_learning.learn_pattern.call_args
        from selfhealing.services.learning.models import PatternType

        assert call_kwargs.kwargs["pattern_type"] == PatternType.RECOVERY
        assert call_kwargs.kwargs["name"] == "runbook:cb_recovery:v2:success"

    def test_failure_records_failure_pattern(self):
        """실패한 Runbook은 FAILURE 패턴으로 학습된다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Step timeout",
            step_results={"step1": _make_step_result(success=False, error="timeout")},
        )
        runbook = _make_runbook(runbook_id="db_recovery", version=1)

        result = recorder._record_learning_feedback(ctx, runbook)

        assert result is True
        call_kwargs = mock_learning.learn_pattern.call_args
        from selfhealing.services.learning.models import PatternType

        assert call_kwargs.kwargs["pattern_type"] == PatternType.FAILURE
        assert call_kwargs.kwargs["name"] == "runbook:db_recovery:v1:failure"

    def test_pattern_name_includes_version(self):
        """패턴 이름에 버전이 포함된다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook(runbook_id="my_rb", version=5)

        recorder._record_learning_feedback(ctx, runbook)

        call_kwargs = mock_learning.learn_pattern.call_args
        assert "v5" in call_kwargs.kwargs["name"]

    def test_metadata_contains_previous_pattern_name_lineage(self):
        """metadata에 이전 버전 패턴 이름(계보)이 포함된다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook(runbook_id="rb1", version=3)

        recorder._record_learning_feedback(ctx, runbook)

        call_kwargs = mock_learning.learn_pattern.call_args
        metadata = call_kwargs.kwargs["metadata"]
        assert metadata["previous_pattern_name"] == "runbook:rb1:v2:success"
        assert metadata["runbook_version"] == 3

    def test_version_one_has_no_previous_pattern(self):
        """v1 런북은 previous_pattern_name이 None이다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook(version=1)

        recorder._record_learning_feedback(ctx, runbook)

        metadata = mock_learning.learn_pattern.call_args.kwargs["metadata"]
        assert metadata["previous_pattern_name"] is None

    def test_success_confidence_is_higher_than_failure(self):
        """성공 패턴 confidence(0.9) > 실패 패턴 confidence(0.85)."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        runbook = _make_runbook()

        # 성공
        ctx_ok = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        recorder._record_learning_feedback(ctx_ok, runbook)
        success_confidence = mock_learning.learn_pattern.call_args.kwargs["confidence"]

        # 실패
        ctx_fail = _make_ctx(status=RunbookExecutionStatus.FAILED)
        recorder._record_learning_feedback(ctx_fail, runbook)
        failure_confidence = mock_learning.learn_pattern.call_args.kwargs["confidence"]

        assert success_confidence > failure_confidence

    def test_slow_recovery_learns_performance_pattern(self):
        """SLA의 80% 초과 시 PERFORMANCE 패턴이 추가 학습된다."""
        # Given
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        # 타임아웃 10s, 실행 시간 9s (90% > 80%)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.COMPLETED,
            started_at="2026-02-26T10:00:00",
            completed_at="2026-02-26T10:00:09",
        )
        runbook = _make_runbook(global_timeout_seconds=10)

        # When
        recorder._record_learning_feedback(ctx, runbook)

        # Then — learn_pattern이 2번 호출됨 (RECOVERY + PERFORMANCE)
        assert mock_learning.learn_pattern.call_count == 2
        second_call = mock_learning.learn_pattern.call_args_list[1]
        from selfhealing.services.learning.models import PatternType

        assert second_call.kwargs["pattern_type"] == PatternType.PERFORMANCE
        assert "slow_recovery" in second_call.kwargs["name"]

    def test_fast_recovery_does_not_learn_performance_pattern(self):
        """SLA의 80% 미만이면 PERFORMANCE 패턴은 학습되지 않는다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        # 타임아웃 10s, 실행 시간 5s (50% < 80%)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.COMPLETED,
            started_at="2026-02-26T10:00:00",
            completed_at="2026-02-26T10:00:05",
        )
        runbook = _make_runbook(global_timeout_seconds=10)

        recorder._record_learning_feedback(ctx, runbook)

        # learn_pattern은 1번만 호출 (RECOVERY만)
        assert mock_learning.learn_pattern.call_count == 1

    def test_no_timeout_skips_performance_check(self):
        """global_timeout_seconds가 None이면 성능 패턴은 학습되지 않는다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook(global_timeout_seconds=None)

        recorder._record_learning_feedback(ctx, runbook)

        assert mock_learning.learn_pattern.call_count == 1

    def test_learning_returns_false_when_service_is_none(self):
        """LearningService가 None이면 False를 반환한다."""
        recorder = RunbookPlaybackRecorder(learning_service=None)
        ctx = _make_ctx()
        runbook = _make_runbook()
        result = recorder._record_learning_feedback(ctx, runbook)
        assert result is False

    def test_learning_returns_false_on_exception(self):
        """LearningService가 예외를 던지면 False를 반환한다."""
        mock_learning = MagicMock()
        mock_learning.learn_pattern.side_effect = RuntimeError("DB error")
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx()
        runbook = _make_runbook()

        result = recorder._record_learning_feedback(ctx, runbook)
        assert result is False

    def test_failure_features_include_failed_steps_and_abort_reason(self):
        """실패 패턴의 features에 실패한 step과 abort_reason이 포함된다."""
        mock_learning = MagicMock()
        recorder = RunbookPlaybackRecorder(learning_service=mock_learning)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Step2 timed out",
            step_results={
                "step1": _make_step_result(step_name="step1", success=True),
                "step2": _make_step_result(step_name="step2", success=False),
            },
        )
        runbook = _make_runbook()

        recorder._record_learning_feedback(ctx, runbook)

        features = mock_learning.learn_pattern.call_args.kwargs["features"]
        assert "step2" in features["failed_steps"]
        assert features["abort_reason"] == "Step2 timed out"


# =============================================================================
# 동작 검증 — Postmortem Channel
# =============================================================================


class TestRecorderPostmortemChannelBehavior:
    """RunbookPlaybackRecorder Postmortem 인시던트 생성 동작 검증."""

    @patch("selfhealing.services.postmortem.store.add_healing_incident")
    def test_postmortem_called_on_failure(self, mock_add_incident):
        """_record_postmortem()이 성공하면 add_healing_incident가 호출된다."""
        recorder = RunbookPlaybackRecorder()
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Step failed",
            step_results={
                "s1": _make_step_result(success=False, error="timeout"),
            },
        )
        runbook = _make_runbook()

        result = recorder._record_postmortem(
            ctx,
            runbook,
            None,
            ctx.step_results,
            ctx.trigger_event,
        )

        assert result is True
        mock_add_incident.assert_called_once()
        incident = mock_add_incident.call_args[0][0]
        assert incident["incident_type"] == "runbook_execution_failed"
        assert incident["resolved"] is False

    @patch("selfhealing.services.postmortem.store.add_healing_incident")
    def test_postmortem_includes_failed_steps(self, mock_add_incident):
        """인시던트에 실패한 step 정보가 포함된다."""
        recorder = RunbookPlaybackRecorder()
        step_results = {
            "step1": _make_step_result(
                step_name="step1",
                action_name="enable_cb",
                success=False,
                error="Connection refused",
            ),
        }
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            step_results=step_results,
        )

        recorder._record_postmortem(
            ctx,
            _make_runbook(),
            None,
            step_results,
            ctx.trigger_event,
        )

        incident = mock_add_incident.call_args[0][0]
        failed_steps = incident["details"]["failed_steps"]
        assert len(failed_steps) == 1
        assert failed_steps[0]["step_name"] == "step1"
        assert failed_steps[0]["error"] == "Connection refused"

    @patch("selfhealing.services.postmortem.store.add_healing_incident")
    def test_postmortem_includes_compensation_info(self, mock_add_incident):
        """보상 결과가 인시던트에 포함된다."""
        recorder = RunbookPlaybackRecorder()
        ctx = _make_ctx(status=RunbookExecutionStatus.FAILED)
        compensation = CompensationSummary(
            compensated=["step1"],
            failed=[("step2", "Compensation error")],
            skipped=["step3"],
        )

        recorder._record_postmortem(
            ctx,
            _make_runbook(),
            compensation,
            {},
            {},
        )

        incident = mock_add_incident.call_args[0][0]
        comp_info = incident["details"]["compensation"]
        assert comp_info["compensated"] == ["step1"]
        assert comp_info["all_compensated"] is False

    @patch("selfhealing.services.postmortem.store.add_healing_incident")
    def test_postmortem_includes_trigger_context_root_cause(self, mock_add_incident):
        """trigger_event의 trigger_context가 인시던트에 포함된다 (Root Cause Link)."""
        recorder = RunbookPlaybackRecorder()
        trigger_event = {
            "trigger_context": {
                "triggered_by_event": "cpu_spike",
                "metric_snapshot": {"cpu_usage": 0.95},
                "match_confidence": 0.92,
                "matched_conditions": ["cpu > 0.9"],
            },
        }
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            trigger_event=trigger_event,
        )

        recorder._record_postmortem(
            ctx,
            _make_runbook(),
            None,
            {},
            trigger_event,
        )

        incident = mock_add_incident.call_args[0][0]
        tc = incident["details"]["trigger_context"]
        assert tc["triggered_by_event"] == "cpu_spike"
        assert tc["match_confidence"] == 0.92

    def test_postmortem_returns_false_on_exception(self):
        """add_healing_incident가 예외를 던지면 False를 반환한다."""
        recorder = RunbookPlaybackRecorder()
        ctx = _make_ctx(status=RunbookExecutionStatus.FAILED)

        with patch(
            "selfhealing.services.postmortem.store.add_healing_incident",
            side_effect=RuntimeError("DB down"),
        ):
            result = recorder._record_postmortem(
                ctx,
                _make_runbook(),
                None,
                {},
                {},
            )
            assert result is False


# =============================================================================
# 동작 검증 — EventBus Channel
# =============================================================================


class TestRecorderEventBusChannelBehavior:
    """RunbookPlaybackRecorder EventBus 이벤트 발행 동작 검증."""

    def test_success_emits_completed_event(self):
        """성공 시 RUNBOOK_EXECUTION_COMPLETED 이벤트를 발행한다."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)

        result = recorder._emit_event(ctx, _make_runbook())

        assert result is True
        from selfhealing.services.event_bus.bus import EventType

        emitted_type = mock_bus.emit.call_args[0][0]
        assert emitted_type == EventType.RUNBOOK_EXECUTION_COMPLETED

    def test_failure_emits_failed_event(self):
        """실패 시 RUNBOOK_EXECUTION_FAILED 이벤트를 발행한다."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Step failed",
            step_results={"s1": _make_step_result(success=False)},
        )

        result = recorder._emit_event(ctx, _make_runbook())

        assert result is True
        from selfhealing.services.event_bus.bus import EventType

        emitted_type = mock_bus.emit.call_args[0][0]
        assert emitted_type == EventType.RUNBOOK_EXECUTION_FAILED

    def test_failed_event_data_includes_abort_reason(self):
        """실패 이벤트 데이터에 abort_reason이 포함된다."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Connection refused",
        )

        recorder._emit_event(ctx, _make_runbook())

        event_data = mock_bus.emit.call_args[0][1]
        assert event_data["abort_reason"] == "Connection refused"

    def test_long_abort_reason_is_truncated(self):
        """MAX_ERROR_MESSAGE_LENGTH를 초과하는 abort_reason은 Truncate된다."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        long_reason = "x" * (MAX_ERROR_MESSAGE_LENGTH + 100)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason=long_reason,
        )

        recorder._emit_event(ctx, _make_runbook())

        event_data = mock_bus.emit.call_args[0][1]
        assert len(event_data["abort_reason"]) <= MAX_ERROR_MESSAGE_LENGTH + len(TRUNCATION_MARKER)
        assert event_data["abort_reason"].endswith(TRUNCATION_MARKER)

    def test_short_abort_reason_is_not_truncated(self):
        """MAX_ERROR_MESSAGE_LENGTH 이내의 abort_reason은 그대로 유지된다."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        reason = "Brief error"
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason=reason,
        )

        recorder._emit_event(ctx, _make_runbook())

        event_data = mock_bus.emit.call_args[0][1]
        assert event_data["abort_reason"] == reason

    def test_failed_event_includes_failed_step_names_only(self):
        """실패 이벤트의 failed_steps에는 step 이름만 포함된다 (에러 상세 없음)."""
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            step_results={
                "step1": _make_step_result(step_name="step1", success=True),
                "step2": _make_step_result(step_name="step2", success=False, error="err"),
            },
        )

        recorder._emit_event(ctx, _make_runbook())

        event_data = mock_bus.emit.call_args[0][1]
        assert event_data["failed_steps"] == ["step2"]

    def test_event_bus_returns_false_when_bus_is_none(self):
        """EventBus가 None이면 False를 반환한다."""
        recorder = RunbookPlaybackRecorder(event_bus=None)
        ctx = _make_ctx()
        result = recorder._emit_event(ctx, _make_runbook())
        assert result is False

    def test_event_bus_returns_false_on_exception(self):
        """EventBus.emit()가 예외를 던지면 False를 반환한다."""
        mock_bus = MagicMock()
        mock_bus.emit.side_effect = RuntimeError("Connection lost")
        recorder = RunbookPlaybackRecorder(event_bus=mock_bus)
        ctx = _make_ctx()

        result = recorder._emit_event(ctx, _make_runbook())
        assert result is False


# =============================================================================
# 동작 검증 — Fail-Open (채널 독립 실패)
# =============================================================================


class TestRecorderFailOpenBehavior:
    """채널 독립 Fail-Open 동작 검증.

    한 채널의 실패가 다른 채널을 차단하지 않는다.
    """

    def test_cascade_failure_does_not_block_other_channels(self):
        """Cascade 실패 시 Learning과 EventBus는 정상 동작한다."""
        # Given
        mock_auditor = MagicMock()
        mock_auditor.record.side_effect = RuntimeError("Cascade down")
        mock_learning = MagicMock()
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=mock_auditor,
            learning_service=mock_learning,
            event_bus=mock_bus,
        )
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)
        runbook = _make_runbook()

        # When
        summary = recorder.record(ctx, runbook)

        # Then
        assert summary.cascade_recorded is False
        assert summary.pattern_recorded is True
        assert summary.event_emitted is True

    def test_learning_failure_does_not_block_other_channels(self):
        """Learning 실패 시 Cascade와 EventBus는 정상 동작한다."""
        mock_auditor = MagicMock()
        mock_learning = MagicMock()
        mock_learning.learn_pattern.side_effect = RuntimeError("Learning down")
        mock_bus = MagicMock()
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=mock_auditor,
            learning_service=mock_learning,
            event_bus=mock_bus,
        )
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)

        summary = recorder.record(ctx, _make_runbook())

        assert summary.cascade_recorded is True
        assert summary.pattern_recorded is False
        assert summary.event_emitted is True

    def test_event_bus_failure_does_not_block_other_channels(self):
        """EventBus 실패 시 Cascade와 Learning은 정상 동작한다."""
        mock_auditor = MagicMock()
        mock_learning = MagicMock()
        mock_bus = MagicMock()
        mock_bus.emit.side_effect = RuntimeError("EventBus down")
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=mock_auditor,
            learning_service=mock_learning,
            event_bus=mock_bus,
        )
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)

        summary = recorder.record(ctx, _make_runbook())

        assert summary.cascade_recorded is True
        assert summary.pattern_recorded is True
        assert summary.event_emitted is False

    def test_postmortem_only_called_on_failure(self):
        """성공 시 postmortem_recorded는 False(기본값)로 유지된다."""
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=MagicMock(),
            learning_service=MagicMock(),
            event_bus=MagicMock(),
        )
        ctx = _make_ctx(status=RunbookExecutionStatus.COMPLETED)

        summary = recorder.record(ctx, _make_runbook())
        assert summary.postmortem_recorded is False


# =============================================================================
# 동작 검증 — 입력 데이터 불변성
# =============================================================================


class TestRecorderDataImmutabilityBehavior:
    """원본 실행 컨텍스트 데이터 불변성 검증."""

    def test_record_does_not_mutate_step_results(self):
        """record() 호출이 원본 step_results를 변경하지 않는다."""
        original_result_data = {"password": "secret123", "status": "ok"}
        original_copy = copy.deepcopy(original_result_data)
        step_results = {
            "step1": _make_step_result(result_data=original_result_data),
        }
        ctx = _make_ctx(step_results=step_results)
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=MagicMock(),
            learning_service=MagicMock(),
            event_bus=MagicMock(),
        )

        recorder.record(ctx, _make_runbook())

        # 원본 데이터가 변경되지 않음
        assert ctx.step_results["step1"].result_data == original_copy

    def test_record_does_not_mutate_trigger_event(self):
        """record() 호출이 원본 trigger_event를 변경하지 않는다."""
        original_trigger = {"api_key": "secret", "metric": "cpu"}
        original_copy = copy.deepcopy(original_trigger)
        ctx = _make_ctx(trigger_event=original_trigger)
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=MagicMock(),
            learning_service=MagicMock(),
            event_bus=MagicMock(),
        )

        recorder.record(ctx, _make_runbook())

        assert ctx.trigger_event == original_copy


# =============================================================================
# 동작 검증 — 전체 record() 통합
# =============================================================================


class TestRecorderIntegrationBehavior:
    """record() 전체 흐름 통합 동작 검증."""

    def test_record_returns_summary_with_execution_id(self):
        """record()가 올바른 execution_id를 가진 RecordingSummary를 반환한다."""
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=MagicMock(),
            learning_service=MagicMock(),
            event_bus=MagicMock(),
        )
        ctx = _make_ctx(execution_id="exec-42")

        summary = recorder.record(ctx, _make_runbook())

        assert summary.execution_id == "exec-42"

    @patch("selfhealing.services.postmortem.store.add_healing_incident")
    def test_failed_runbook_records_all_four_channels(self, mock_add_incident):
        """실패한 Runbook은 4개 채널 모두에 기록된다."""
        recorder = RunbookPlaybackRecorder(
            cascade_auditor=MagicMock(),
            learning_service=MagicMock(),
            event_bus=MagicMock(),
        )
        ctx = _make_ctx(
            status=RunbookExecutionStatus.FAILED,
            abort_reason="Step failed",
            step_results={"s1": _make_step_result(success=False)},
        )

        summary = recorder.record(ctx, _make_runbook())

        assert summary.cascade_recorded is True
        assert summary.pattern_recorded is True
        assert summary.postmortem_recorded is True
        assert summary.event_emitted is True

    def test_all_none_dependencies_returns_all_false(self):
        """모든 의존성이 None이면 모든 채널이 False이다."""
        recorder = RunbookPlaybackRecorder()
        ctx = _make_ctx()

        summary = recorder.record(ctx, _make_runbook())

        assert summary.cascade_recorded is False
        assert summary.pattern_recorded is False
        assert summary.event_emitted is False
        assert summary.all_recorded is False

    def test_calculate_duration_with_valid_timestamps(self):
        """유효한 ISO 8601 타임스탬프로 실행 시간을 올바르게 계산한다."""
        ctx = _make_ctx(
            started_at="2026-02-26T10:00:00",
            completed_at="2026-02-26T10:00:05",
        )
        duration = RunbookPlaybackRecorder._calculate_duration(ctx)
        assert duration == pytest.approx(5.0)

    def test_calculate_duration_with_missing_timestamps_returns_none(self):
        """시작 또는 완료 시각이 없으면 None을 반환한다."""
        ctx = _make_ctx(started_at=None, completed_at=None)
        duration = RunbookPlaybackRecorder._calculate_duration(ctx)
        assert duration is None

    def test_calculate_duration_with_invalid_format_returns_none(self):
        """잘못된 형식의 타임스탬프는 None을 반환한다."""
        ctx = _make_ctx(started_at="not-a-date", completed_at="also-not-a-date")
        duration = RunbookPlaybackRecorder._calculate_duration(ctx)
        assert duration is None
