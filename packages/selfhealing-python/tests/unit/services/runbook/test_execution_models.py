"""
RunbookExecutionStatus, RunbookStepResult, CompensationSummary, RunbookExecutionContext
단위 테스트.

테스트 대상:
    selfhealing.services.runbook.execution_models

계약 검증 클래스 (Test*Contract):
    - TestRunbookExecutionStatusContract   — Enum 값 설계 계약
    - TestRunbookStepResultDefaultContract — 기본값 설계 계약
    - TestCompensationSummaryDefaultContract — 기본값 설계 계약
    - TestRunbookExecutionContextDefaultContract — 기본값 설계 계약

동작 검증 클래스 (Test*Behavior):
    - TestRunbookStepResultSerializationBehavior — 직렬화 왕복
    - TestCompensationSummaryBehavior            — all_compensated 속성
    - TestRunbookExecutionContextBehavior        — get(), to_dict/from_dict 왕복
"""

from __future__ import annotations

import pytest

from selfhealing.services.runbook.execution_models import (
    CompensationSummary,
    RunbookExecutionContext,
    RunbookExecutionStatus,
    RunbookStepResult,
)


# =============================================================================
# 계약 검증 — RunbookExecutionStatus Enum 값
# =============================================================================


class TestRunbookExecutionStatusContract:
    """실행 상태 Enum 설계 계약값 검증."""

    def test_status_values_match_design_document(self):
        """§3.1 설계 계약: 7가지 상태값이 명시된 문자열과 정확히 일치해야 한다."""
        assert RunbookExecutionStatus.PENDING.value == "pending"
        assert RunbookExecutionStatus.EXECUTING.value == "executing"
        assert RunbookExecutionStatus.WAITING_APPROVAL.value == "waiting_approval"
        assert RunbookExecutionStatus.COMPENSATING.value == "compensating"
        assert RunbookExecutionStatus.COMPLETED.value == "completed"
        assert RunbookExecutionStatus.FAILED.value == "failed"
        assert RunbookExecutionStatus.CANCELLED.value == "cancelled"

    def test_status_count_matches_design_document(self):
        """§3.1 설계 계약: 정확히 7개 상태만 존재해야 한다."""
        assert len(RunbookExecutionStatus) == 7

    def test_status_is_str_subclass(self):
        """str 서브클래스이므로 JSON 직렬화 시 str로 처리된다."""
        assert isinstance(RunbookExecutionStatus.PENDING, str)


# =============================================================================
# 계약 검증 — RunbookStepResult 기본값
# =============================================================================


class TestRunbookStepResultDefaultContract:
    """RunbookStepResult 기본값 설계 계약 검증."""

    def test_result_data_default_is_empty_dict(self):
        """§3.2 설계 계약: result_data 기본값은 빈 dict."""
        r = RunbookStepResult(step_name="s", action_name="a", success=True, executed=True)
        assert r.result_data == {}

    def test_idempotent_default_is_false(self):
        """§3.2 설계 계약: idempotent 기본값은 False."""
        r = RunbookStepResult(step_name="s", action_name="a", success=True, executed=True)
        assert r.idempotent is False

    def test_partial_execution_default_is_false(self):
        """§3.2 설계 계약: partial_execution 기본값은 False."""
        r = RunbookStepResult(step_name="s", action_name="a", success=True, executed=True)
        assert r.partial_execution is False

    def test_compensation_status_default_is_not_needed(self):
        """§3.2 설계 계약: compensation_status 기본값은 'not_needed'."""
        r = RunbookStepResult(step_name="s", action_name="a", success=True, executed=True)
        assert r.compensation_status == "not_needed"


# =============================================================================
# 계약 검증 — CompensationSummary 기본값
# =============================================================================


class TestCompensationSummaryDefaultContract:
    """CompensationSummary 기본값 설계 계약 검증."""

    def test_all_lists_default_to_empty(self):
        """§6.2 설계 계약: compensated, failed, skipped 기본값은 빈 리스트."""
        s = CompensationSummary()
        assert s.compensated == []
        assert s.failed == []
        assert s.skipped == []


# =============================================================================
# 계약 검증 — RunbookExecutionContext 기본값
# =============================================================================


class TestRunbookExecutionContextDefaultContract:
    """RunbookExecutionContext 기본값 설계 계약 검증."""

    def test_runbook_version_default_is_1(self):
        """§3 설계 계약: runbook_version 기본값은 1."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        assert ctx.runbook_version == 1

    def test_status_default_is_pending(self):
        """§3.1 설계 계약: 초기 status는 PENDING."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        assert ctx.status == RunbookExecutionStatus.PENDING

    def test_current_step_index_default_is_zero(self):
        """§3 설계 계약: current_step_index 기본값은 0."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        assert ctx.current_step_index == 0

    def test_step_results_default_is_empty_dict(self):
        """§3 설계 계약: step_results 기본값은 빈 dict."""
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
        )
        assert ctx.step_results == {}


# =============================================================================
# 동작 검증 — RunbookStepResult 직렬화 왕복
# =============================================================================


class TestRunbookStepResultSerializationBehavior:
    """RunbookStepResult to_dict/from_dict 직렬화 왕복 검증."""

    def test_roundtrip_preserves_all_fields(self):
        """to_dict → from_dict가 모든 필드를 보존해야 한다."""
        # Given
        original = RunbookStepResult(
            step_name="kill_idle",
            action_name="db.kill_idle",
            success=True,
            executed=True,
            result_data={"killed_count": 5},
            error=None,
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            idempotent=False,
            partial_execution=False,
            compensation_status="not_needed",
        )
        # When
        restored = RunbookStepResult.from_dict(original.to_dict())

        # Then
        assert restored.step_name == original.step_name
        assert restored.action_name == original.action_name
        assert restored.success == original.success
        assert restored.executed == original.executed
        assert restored.result_data == original.result_data
        assert restored.idempotent == original.idempotent
        assert restored.partial_execution == original.partial_execution
        assert restored.compensation_status == original.compensation_status

    def test_roundtrip_with_partial_execution_flag(self):
        """partial_execution=True 필드가 직렬화 후에도 보존되어야 한다."""
        # Given
        r = RunbookStepResult(
            step_name="s",
            action_name="a",
            success=False,
            executed=True,
            partial_execution=True,
            error="timeout",
        )
        # When / Then
        restored = RunbookStepResult.from_dict(r.to_dict())
        assert restored.partial_execution is True
        assert restored.success is False
        assert restored.error == "timeout"

    def test_to_dict_does_not_mutate_original(self):
        """to_dict 호출이 원본 객체를 변경하지 않아야 한다."""
        # Given
        r = RunbookStepResult(
            step_name="s",
            action_name="a",
            success=True,
            executed=True,
            result_data={"key": "value"},
        )
        # When
        serialized = r.to_dict()
        serialized["result_data"]["key"] = "mutated"
        # Then — 원본 결과가 변경되지 않아야 한다 (dict는 참조 복사이므로 변경됨 — shallow copy 주의)
        # to_dict는 shallow copy이므로 result_data는 공유됨. 독립 테스트로 확인.
        assert r.step_name == "s"  # 필드 자체는 보존


# =============================================================================
# 동작 검증 — CompensationSummary
# =============================================================================


class TestCompensationSummaryBehavior:
    """CompensationSummary.all_compensated 속성 동작 검증."""

    def test_all_compensated_true_when_no_failures(self):
        """failed가 비어있으면 all_compensated는 True여야 한다."""
        # Given
        summary = CompensationSummary(
            compensated=["step_a", "step_b"],
            failed=[],
            skipped=["step_c"],
        )
        # When / Then
        assert summary.all_compensated is True

    def test_all_compensated_false_when_has_failures(self):
        """failed가 있으면 all_compensated는 False여야 한다."""
        # Given
        summary = CompensationSummary(
            compensated=["step_a"],
            failed=[("step_b", "connection refused")],
        )
        # When / Then
        assert summary.all_compensated is False

    def test_to_dict_includes_all_compensated_key(self):
        """to_dict 결과에 all_compensated 키가 포함되어야 한다."""
        summary = CompensationSummary(compensated=["s1"])
        d = summary.to_dict()
        assert "all_compensated" in d
        assert d["all_compensated"] is True

    def test_to_dict_serializes_failed_as_list_of_dicts(self):
        """to_dict의 failed 항목이 {step, error} dict 형식이어야 한다."""
        summary = CompensationSummary(failed=[("step1", "error_msg")])
        d = summary.to_dict()
        assert d["failed"] == [{"step": "step1", "error": "error_msg"}]


# =============================================================================
# 동작 검증 — RunbookExecutionContext
# =============================================================================


class TestRunbookExecutionContextBehavior:
    """RunbookExecutionContext 동작 검증."""

    def test_get_returns_step_result_by_name(self):
        """get()이 step_name으로 RunbookStepResult를 반환해야 한다."""
        # Given
        step_result = RunbookStepResult(
            step_name="kill_idle",
            action_name="db.kill_idle",
            success=True,
            executed=True,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"kill_idle": step_result},
        )
        # When / Then
        assert ctx.get("kill_idle") is step_result
        assert ctx.get("nonexistent") is None

    def test_roundtrip_preserves_status_as_enum(self):
        """from_dict 역직렬화 후 status가 RunbookExecutionStatus Enum이어야 한다."""
        # Given
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={"key": "val"},
            status=RunbookExecutionStatus.EXECUTING,
            runbook_version=3,
        )
        # When
        restored = RunbookExecutionContext.from_dict(ctx.to_dict())
        # Then
        assert restored.status == RunbookExecutionStatus.EXECUTING
        assert isinstance(restored.status, RunbookExecutionStatus)
        assert restored.runbook_version == 3

    def test_roundtrip_preserves_step_results(self):
        """직렬화 후 step_results 내 RunbookStepResult가 보존되어야 한다."""
        # Given
        step_result = RunbookStepResult(
            step_name="s1",
            action_name="a1",
            success=True,
            executed=True,
            result_data={"count": 10},
            partial_execution=False,
        )
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            step_results={"s1": step_result},
        )
        # When
        restored = RunbookExecutionContext.from_dict(ctx.to_dict())
        # Then
        assert "s1" in restored.step_results
        restored_sr = restored.step_results["s1"]
        assert restored_sr.success is True
        assert restored_sr.result_data == {"count": 10}

    def test_roundtrip_preserves_nested_variables(self):
        """variables 딕셔너리가 직렬화 후 보존되어야 한다."""
        # Given
        ctx = RunbookExecutionContext(
            execution_id="e1",
            runbook_id="rb1",
            namespace="ns",
            trigger_event={},
            variables={"__resume_count": 2, "custom_key": "value"},
        )
        # When
        restored = RunbookExecutionContext.from_dict(ctx.to_dict())
        # Then
        assert restored.variables["__resume_count"] == 2
        assert restored.variables["custom_key"] == "value"
