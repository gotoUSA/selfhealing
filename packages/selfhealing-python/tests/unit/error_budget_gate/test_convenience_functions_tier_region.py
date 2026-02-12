"""
편의 함수 check_automation_allowed() 티어/리전 파라미터 테스트.

check_automation_allowed(tier_id, region) 시그니처 검증.
"""

import pytest
import inspect

from selfhealing.services.error_budget_gate.gate import check_automation_allowed


# =============================================================================
# 계약 검증: 시그니처
# =============================================================================


class TestCheckAutomationAllowedSignatureContract:
    """check_automation_allowed() 시그니처 계약 검증."""

    def test_has_tier_id_parameter(self):
        """tier_id 파라미터 존재."""
        sig = inspect.signature(check_automation_allowed)
        assert "tier_id" in sig.parameters

    def test_tier_id_default_none(self):
        """tier_id 기본값 None."""
        sig = inspect.signature(check_automation_allowed)
        assert sig.parameters["tier_id"].default is None

    def test_has_region_parameter(self):
        """region 파라미터 존재."""
        sig = inspect.signature(check_automation_allowed)
        assert "region" in sig.parameters

    def test_region_default_none(self):
        """region 기본값 None."""
        sig = inspect.signature(check_automation_allowed)
        assert sig.parameters["region"].default is None

    def test_has_force_refresh_parameter(self):
        """기존 force_refresh 파라미터 유지."""
        sig = inspect.signature(check_automation_allowed)
        assert "force_refresh" in sig.parameters
