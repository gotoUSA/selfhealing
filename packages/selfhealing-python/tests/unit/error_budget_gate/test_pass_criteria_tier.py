"""
PassCriteria 티어별 기본값 및 apply_tier_floor() 테스트.

PassCriteria.for_tier() 팩토리 메서드와
apply_tier_floor() 하한 강제 동작 검증.
"""

import pytest

from selfhealing.services.canary.models import PassCriteria, apply_tier_floor


# =============================================================================
# 계약 검증: PassCriteria.for_tier() 기본값
# =============================================================================


class TestPassCriteriaForTierContract:
    """PassCriteria.for_tier() 티어별 기본값 계약 검증."""

    def test_critical_tier_drain_rate_max(self):
        """critical 티어 error_budget_drain_rate_max=0.8."""
        criteria = PassCriteria.for_tier("critical")
        assert criteria.error_budget_drain_rate_max == 0.8

    def test_critical_tier_remaining_min(self):
        """critical 티어 error_budget_remaining_min=0.15."""
        criteria = PassCriteria.for_tier("critical")
        assert criteria.error_budget_remaining_min == 0.15

    def test_critical_tier_error_rate_max(self):
        """critical 티어 error_rate_absolute_max=0.03."""
        criteria = PassCriteria.for_tier("critical")
        assert criteria.error_rate_absolute_max == 0.03

    def test_standard_tier_drain_rate_max(self):
        """standard 티어 error_budget_drain_rate_max=1.2."""
        criteria = PassCriteria.for_tier("standard")
        assert criteria.error_budget_drain_rate_max == 1.2

    def test_standard_tier_remaining_min(self):
        """standard 티어 error_budget_remaining_min=0.10."""
        criteria = PassCriteria.for_tier("standard")
        assert criteria.error_budget_remaining_min == 0.10

    def test_standard_tier_error_rate_max(self):
        """standard 티어 error_rate_absolute_max=0.05."""
        criteria = PassCriteria.for_tier("standard")
        assert criteria.error_rate_absolute_max == 0.05

    def test_non_essential_tier_drain_rate_max(self):
        """non_essential 티어 error_budget_drain_rate_max=2.0."""
        criteria = PassCriteria.for_tier("non_essential")
        assert criteria.error_budget_drain_rate_max == 2.0

    def test_non_essential_tier_remaining_min(self):
        """non_essential 티어 error_budget_remaining_min=0.05."""
        criteria = PassCriteria.for_tier("non_essential")
        assert criteria.error_budget_remaining_min == 0.05

    def test_non_essential_tier_error_rate_max(self):
        """non_essential 티어 error_rate_absolute_max=0.10."""
        criteria = PassCriteria.for_tier("non_essential")
        assert criteria.error_rate_absolute_max == 0.10


class TestPassCriteriaForTierUnknownContract:
    """PassCriteria.for_tier() 미정의 티어 동작 계약 검증."""

    def test_unknown_tier_returns_default_criteria(self):
        """미정의 티어는 기본 PassCriteria 반환 (오버라이드 없음)."""
        criteria = PassCriteria.for_tier("unknown_tier")
        default = PassCriteria()
        assert criteria.error_budget_drain_rate_max == default.error_budget_drain_rate_max
        assert criteria.error_budget_remaining_min == default.error_budget_remaining_min
        assert criteria.error_rate_absolute_max == default.error_rate_absolute_max


# =============================================================================
# 동작 검증: apply_tier_floor()
# =============================================================================


class TestApplyTierFloorBehavior:
    """apply_tier_floor() 하한 강제 동작 검증."""

    def test_user_looser_than_tier_gets_tightened(self):
        """사용자 기준이 티어보다 느슨하면 티어 하한으로 강제."""
        user = PassCriteria(
            error_rate_absolute_max=0.10,  # 사용자: 느슨한 10%
            error_budget_drain_rate_max=3.0,  # 사용자: 느슨한 3.0
            error_budget_remaining_min=0.02,  # 사용자: 느슨한 2%
        )
        result = apply_tier_floor(user, "critical")
        tier = PassCriteria.for_tier("critical")

        # max 필드: min(user, tier) → 더 작은 값(티어)이 적용
        assert result.error_rate_absolute_max == tier.error_rate_absolute_max
        assert result.error_budget_drain_rate_max == tier.error_budget_drain_rate_max
        # min 필드: max(user, tier) → 더 큰 값(티어)이 적용
        assert result.error_budget_remaining_min == tier.error_budget_remaining_min

    def test_user_stricter_than_tier_preserved(self):
        """사용자 기준이 티어보다 엄격하면 사용자 값 유지."""
        user = PassCriteria(
            error_rate_absolute_max=0.01,  # 사용자: 아주 엄격한 1%
            error_budget_drain_rate_max=0.5,  # 사용자: 아주 엄격
            error_budget_remaining_min=0.30,  # 사용자: 아주 엄격한 30%
        )
        result = apply_tier_floor(user, "critical")

        assert result.error_rate_absolute_max == user.error_rate_absolute_max
        assert result.error_budget_drain_rate_max == user.error_budget_drain_rate_max
        assert result.error_budget_remaining_min == user.error_budget_remaining_min

    def test_latency_fields_always_preserved(self):
        """레이턴시 필드는 티어와 무관하게 사용자 값 유지."""
        user = PassCriteria(
            latency_p95_delta_ms=100.0,
            latency_p99_delta_pct=0.5,
        )
        result = apply_tier_floor(user, "critical")

        assert result.latency_p95_delta_ms == user.latency_p95_delta_ms
        assert result.latency_p99_delta_pct == user.latency_p99_delta_pct

    def test_evaluation_window_preserved(self):
        """평가 윈도우는 사용자 값 유지."""
        user = PassCriteria(evaluation_window_seconds=600)
        result = apply_tier_floor(user, "critical")

        assert result.evaluation_window_seconds == user.evaluation_window_seconds

    def test_min_requests_required_uses_stricter(self):
        """min_requests_required는 더 큰 값(더 엄격) 적용."""
        user = PassCriteria(min_requests_required=50)
        tier = PassCriteria.for_tier("critical")
        result = apply_tier_floor(user, "critical")

        assert result.min_requests_required == max(user.min_requests_required, tier.min_requests_required)

    def test_returns_new_instance(self):
        """원본 PassCriteria를 변경하지 않고 새 인스턴스 반환."""
        user = PassCriteria(error_rate_absolute_max=0.10)
        result = apply_tier_floor(user, "critical")

        assert result is not user
        assert user.error_rate_absolute_max == 0.10  # 원본 유지
