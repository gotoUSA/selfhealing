"""
CheckOnUseMultiplierProvider Unit Tests.

매 사용 시점에 Emergency Level을 조회하여 MultiplierContext를 반환하는 Provider 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.0
"""

from unittest.mock import MagicMock

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.provider import (
    CheckOnUseMultiplierProvider,
    MultiplierContext,
    get_check_on_use_provider,
    reset_check_on_use_provider,
)

# =============================================================================
# Test Fixtures
# =============================================================================

def create_mock_tracker(level: EmergencyLevel = EmergencyLevel.NORMAL):
    """테스트용 Mock Tracker 생성."""
    mock_tracker = MagicMock()
    mock_state = MagicMock()
    mock_state.emergency_level = level
    mock_tracker.get_effective_state.return_value = mock_state
    return mock_tracker


# =============================================================================
# MultiplierContext Tests
# =============================================================================

class TestMultiplierContext:
    """MultiplierContext 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        ctx = MultiplierContext()

        assert ctx.level == EmergencyLevel.NORMAL
        assert ctx.level_multiplier == 1.0
        assert ctx.domain is None
        assert ctx.domain_multiplier == 1.0
        assert ctx.final_multiplier == 1.0
        assert ctx.emergency_id is None

    def test_to_dict(self):
        """딕셔너리 변환."""
        ctx = MultiplierContext(
            level=EmergencyLevel.LEVEL_3,
            level_multiplier=5.0,
            domain="payment",
            domain_multiplier=10.0,
            final_multiplier=10.0,
            emergency_id="emg_123",
            namespace="seoul",
        )

        data = ctx.to_dict()

        assert data["level"] == "LEVEL_3"
        assert data["level_multiplier"] == 5.0
        assert data["domain"] == "payment"
        assert data["domain_multiplier"] == 10.0
        assert data["final_multiplier"] == 10.0
        assert data["emergency_id"] == "emg_123"
        assert data["namespace"] == "seoul"


# =============================================================================
# CheckOnUseMultiplierProvider Tests
# =============================================================================

class TestCheckOnUseMultiplierProvider:
    """CheckOnUseMultiplierProvider 테스트."""

    def test_returns_multiplier_context(self):
        """MultiplierContext 반환 확인."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier(namespace="test")

        assert isinstance(ctx, MultiplierContext)
        assert ctx.level_multiplier >= 1.0

    def test_normal_level_returns_1x(self):
        """NORMAL 레벨에서 1.0x 반환."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier()

        assert ctx.level == EmergencyLevel.NORMAL
        assert ctx.level_multiplier == 1.0

    def test_level_3_returns_5x(self):
        """LEVEL_3에서 5.0x 반환."""
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier()

        assert ctx.level == EmergencyLevel.LEVEL_3
        assert ctx.level_multiplier == 5.0

    def test_realtime_level_lookup(self):
        """매 호출마다 실시간 조회 확인."""
        mock_tracker = MagicMock()

        # 첫 번째 호출: NORMAL
        mock_state1 = MagicMock()
        mock_state1.emergency_level = EmergencyLevel.NORMAL

        # 두 번째 호출: LEVEL_3
        mock_state2 = MagicMock()
        mock_state2.emergency_level = EmergencyLevel.LEVEL_3

        mock_tracker.get_effective_state.side_effect = [mock_state1, mock_state2]

        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx1 = provider.get_current_multiplier()
        ctx2 = provider.get_current_multiplier()

        assert ctx1.level_multiplier == 1.0
        assert ctx2.level_multiplier == 5.0
        # 두 번 호출됨 (캐시 없음)
        assert mock_tracker.get_effective_state.call_count == 2

    def test_domain_multiplier_applied(self):
        """도메인 가중치 적용."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier(domain="payment")

        assert ctx.domain == "payment"
        assert ctx.domain_multiplier == 10.0  # payment 기본 민감도

    def test_custom_domain_sensitivity(self):
        """커스텀 도메인 민감도 설정."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        custom_sensitivity = {"custom_domain": 7.5}

        provider = CheckOnUseMultiplierProvider(
            emergency_tracker=mock_tracker,
            domain_sensitivity=custom_sensitivity,
        )

        ctx = provider.get_current_multiplier(domain="custom_domain")

        assert ctx.domain_multiplier == 7.5

    def test_unknown_domain_returns_1x(self):
        """알 수 없는 도메인은 1.0x."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier(domain="unknown_domain")

        assert ctx.domain_multiplier == 1.0

    def test_final_multiplier_uses_max(self):
        """final_multiplier는 max 전략 사용 (기본)."""
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_2)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        # LEVEL_2 = 3.0x, payment = 10.0x → max = 10.0x (but capped)
        ctx = provider.get_current_multiplier(domain="payment")

        # Cap 적용 (10.0)
        assert ctx.final_multiplier == 10.0

    def test_max_multiplier_cap_applied(self):
        """max_multiplier Cap 적용."""
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider = CheckOnUseMultiplierProvider(
            emergency_tracker=mock_tracker,
            max_multiplier=5.0,  # 낮은 Cap
        )

        # LEVEL_3 = 5.0x, payment = 10.0x → max = 10.0 → capped to 5.0
        ctx = provider.get_current_multiplier(domain="payment")

        assert ctx.final_multiplier == 5.0

    def test_tracker_failure_returns_normal(self):
        """Tracker 오류 시 NORMAL 반환."""
        mock_tracker = MagicMock()
        mock_tracker.get_effective_state.side_effect = Exception("Connection error")

        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        ctx = provider.get_current_multiplier()

        assert ctx.level == EmergencyLevel.NORMAL
        assert ctx.level_multiplier == 1.0

    def test_set_domain_sensitivity(self):
        """set_domain_sensitivity로 민감도 추가."""
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider = CheckOnUseMultiplierProvider(emergency_tracker=mock_tracker)

        provider.set_domain_sensitivity("new_domain", 8.0)

        ctx = provider.get_current_multiplier(domain="new_domain")

        assert ctx.domain_multiplier == 8.0

    def test_get_all_multipliers(self):
        """모든 레벨의 가중치 반환."""
        provider = CheckOnUseMultiplierProvider()

        all_multipliers = provider.get_all_multipliers()

        assert all_multipliers["NORMAL"] == 1.0
        assert all_multipliers["LEVEL_1"] == 1.5
        assert all_multipliers["LEVEL_2"] == 3.0
        assert all_multipliers["LEVEL_3"] == 5.0


# =============================================================================
# Singleton Tests
# =============================================================================

class TestCheckOnUseSingleton:
    """싱글톤 팩토리 테스트."""

    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_check_on_use_provider()

    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_check_on_use_provider()

    def test_get_returns_singleton(self):
        """get_check_on_use_provider는 같은 인스턴스 반환."""
        provider1 = get_check_on_use_provider()
        provider2 = get_check_on_use_provider()

        assert provider1 is provider2

    def test_reset_clears_singleton(self):
        """reset_check_on_use_provider는 싱글톤 초기화."""
        provider1 = get_check_on_use_provider()

        reset_check_on_use_provider()

        provider2 = get_check_on_use_provider()

        assert provider2 is not provider1
