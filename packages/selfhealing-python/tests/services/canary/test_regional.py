"""
Phase 2: RegionalInterlockBehavior, RegionalInterlockPolicy 단위 테스트.

멀티리전 인터락 정책 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest

from selfhealing.services.canary.regional import (
    RegionalInterlockBehavior,
    RegionalInterlockPolicy,
)


# =============================================================================
# Test: RegionalInterlockBehavior
# =============================================================================


class TestRegionalInterlockBehavior:
    """RegionalInterlockBehavior enum 테스트."""

    def test_behavior_values(self):
        """행동 값이 올바르게 정의되어 있는지 확인."""
        assert RegionalInterlockBehavior.PAUSE_ALL.value == "pause_all"
        assert RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY.value == "rollback_affected_only"
        assert RegionalInterlockBehavior.HYBRID.value == "hybrid"
        assert RegionalInterlockBehavior.CONTINUE_HEALTHY.value == "continue_healthy"

    def test_behavior_is_string_enum(self):
        """RegionalInterlockBehavior가 문자열 비교 가능한지 확인."""
        assert RegionalInterlockBehavior.PAUSE_ALL == "pause_all"
        assert RegionalInterlockBehavior.HYBRID == "hybrid"


# =============================================================================
# Test: RegionalInterlockPolicy
# =============================================================================


class TestRegionalInterlockPolicy:
    """RegionalInterlockPolicy 테스트."""

    def test_default_policy_values(self):
        """기본 정책 값 확인."""
        policy = RegionalInterlockPolicy()
        
        assert policy.default_behavior == RegionalInterlockBehavior.PAUSE_ALL
        assert policy.allow_isolated_rollback is False
        assert policy.require_manual_resume_after_regional_rollback is True
        assert policy.max_affected_regions_for_isolated_rollback == 1

    def test_all_regions_affected_returns_pause_all(self):
        """모든 리전이 영향받으면 PAUSE_ALL 반환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.CONTINUE_HEALTHY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul", "tokyo"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_non_isolated_deployment_ignores_rollback_affected_only(self):
        """격리 배포가 아니면 ROLLBACK_AFFECTED_ONLY를 PAUSE_ALL로 변환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=False,  # 격리 배포 아님
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_too_many_affected_regions_returns_pause_all(self):
        """영향 리전이 max_affected_regions보다 많으면 PAUSE_ALL."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
            max_affected_regions_for_isolated_rollback=1,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul", "tokyo"],  # 2개 영향
            total_regions=["seoul", "tokyo", "oregon"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_isolated_rollback_disabled_returns_pause_all(self):
        """격리 롤백 비활성화 시 ROLLBACK_AFFECTED_ONLY를 PAUSE_ALL로 변환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=False,  # 격리 롤백 비활성화
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_hybrid_allowed_for_non_isolated(self):
        """격리 배포 아니어도 HYBRID는 허용."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.HYBRID,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=False,
        )
        
        assert behavior == RegionalInterlockBehavior.HYBRID

    def test_isolated_rollback_enabled_with_valid_conditions(self):
        """모든 조건 충족 시 설정된 행동 반환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
            max_affected_regions_for_isolated_rollback=2,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo", "oregon"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY
