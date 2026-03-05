"""
Tests for GovernanceCheckResult.blocked_by_shadow_evaluation() and BlockReason.SHADOW_EVALUATION_FAILED.

Target: services/governance/checks.py (commit 300)
"""

from selfhealing.services.governance.checks import BlockReason, GovernanceCheckResult


class TestBlockReasonShadowEvaluationContract:
    """BlockReason.SHADOW_EVALUATION_FAILED 계약 검증."""

    def test_shadow_evaluation_failed_value(self):
        """SHADOW_EVALUATION_FAILED의 값은 'shadow_evaluation_failed'이다."""
        assert BlockReason.SHADOW_EVALUATION_FAILED.value == "shadow_evaluation_failed"

    def test_shadow_evaluation_failed_is_str_enum(self):
        """BlockReason은 str Enum이므로 문자열 비교가 가능하다."""
        assert BlockReason.SHADOW_EVALUATION_FAILED == "shadow_evaluation_failed"


class TestBlockedByShadowEvaluationBehavior:
    """GovernanceCheckResult.blocked_by_shadow_evaluation() 동작 검증."""

    def test_result_is_not_allowed(self):
        """blocked_by_shadow_evaluation 결과는 allowed=False이다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-123",
            summary="Drift detected",
            confidence_score=0.85,
        )
        assert result.allowed is False

    def test_result_block_reason_is_shadow_evaluation_failed(self):
        """block_reason이 SHADOW_EVALUATION_FAILED이다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-123",
            summary="Drift detected",
            confidence_score=0.85,
        )
        assert result.block_reason == BlockReason.SHADOW_EVALUATION_FAILED

    def test_block_message_contains_evaluation_id(self):
        """block_message에 evaluation_id가 포함된다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-abc",
            summary="test",
            confidence_score=0.5,
        )
        assert "eval-abc" in result.block_message

    def test_block_message_contains_confidence_score(self):
        """block_message에 confidence_score가 포함된다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-123",
            summary="test",
            confidence_score=0.85,
        )
        assert "0.85" in result.block_message

    def test_block_message_contains_summary(self):
        """block_message에 summary가 포함된다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-123",
            summary="Drift detected in circuit_breaker",
            confidence_score=0.5,
        )
        assert "Drift detected in circuit_breaker" in result.block_message

    def test_block_message_format_matches_contract(self):
        """block_message 형식이 계약과 일치한다."""
        result = GovernanceCheckResult.blocked_by_shadow_evaluation(
            evaluation_id="eval-001",
            summary="Major drift",
            confidence_score=0.42,
        )
        expected = (
            "Shadow evaluation failed (id=eval-001, confidence=0.42): Major drift"
        )
        assert result.block_message == expected
