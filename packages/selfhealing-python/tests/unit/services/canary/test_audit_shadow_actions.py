"""
Tests for new CANARY_ACTIONS audit entries added in commit 300.

Target: services/canary/audit.py
"""

from selfhealing.services.canary.audit import CANARY_ACTIONS


class TestCanaryAuditShadowActionsContract:
    """CANARY_ACTIONS에 Shadow 관련 액션이 포함되어 있는지 계약 검증."""

    def test_shadow_evaluation_bypass_in_actions(self):
        """'shadow_evaluation_bypass' 액션이 CANARY_ACTIONS에 존재한다."""
        assert "shadow_evaluation_bypass" in CANARY_ACTIONS

    def test_shadow_evaluation_low_confidence_in_actions(self):
        """'shadow_evaluation_low_confidence' 액션이 CANARY_ACTIONS에 존재한다."""
        assert "shadow_evaluation_low_confidence" in CANARY_ACTIONS

    def test_total_action_count_after_shadow_additions(self):
        """Shadow 액션 추가 후 총 액션 수: 14개."""
        assert len(CANARY_ACTIONS) == 14
