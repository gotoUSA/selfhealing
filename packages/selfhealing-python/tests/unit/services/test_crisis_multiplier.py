"""
Crisis Multiplier Unit Tests.

CrisisMultiplierConfig와 CrisisMultiplierProvider의 단위 테스트.

Test Coverage:
- CrisisMultiplierConfig: 가중치 설정, 직렬화/역직렬화
- CrisisMultiplierProvider: 가중치 조회, 캐시, 무효화

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
"""

import time
from unittest.mock import MagicMock

from selfhealing.services.coordination.enums import EmergencyScope
from selfhealing.services.coordination.models import ScopedEmergencyState
from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.error_budget.multiplier import (
    DEFAULT_MAX_MULTIPLIER,
    CrisisMultiplierConfig,
    CrisisMultiplierProvider,
    configure_crisis_multiplier_provider,
    get_crisis_multiplier_provider,
    reset_crisis_multiplier_provider,
)

# =============================================================================
# Test Fixtures
# =============================================================================


def create_mock_state(
    level: EmergencyLevel = EmergencyLevel.NORMAL,
    namespace: str = "test",
) -> ScopedEmergencyState:
    """테스트용 ScopedEmergencyState 생성."""
    return ScopedEmergencyState(
        namespace=namespace,
        emergency_level=level,
        governance_mode="STRICT" if level != EmergencyLevel.NORMAL else "NORMAL",
        scope=EmergencyScope.REGIONAL,
    )


def create_mock_tracker(level: EmergencyLevel = EmergencyLevel.NORMAL):
    """테스트용 Mock Tracker 생성."""
    mock_tracker = MagicMock()
    mock_tracker.get_effective_state.return_value = create_mock_state(level=level)
    return mock_tracker


# =============================================================================
# CrisisMultiplierConfig Tests
# =============================================================================


class TestCrisisMultiplierConfig:
    """CrisisMultiplierConfig 단위 테스트."""

    def test_default_config_has_expected_multipliers(self):
        """기본 설정이 예상 가중치를 가지는지 확인."""
        config = CrisisMultiplierConfig()

        assert config.get_multiplier(EmergencyLevel.NORMAL) == 1.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_1) == 1.5
        assert config.get_multiplier(EmergencyLevel.LEVEL_2) == 3.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 5.0

    def test_default_config_is_enabled(self):
        """기본 설정은 활성화 상태."""
        config = CrisisMultiplierConfig()

        assert config.enabled is True

    def test_default_max_multiplier(self):
        """기본 최대 가중치 확인."""
        config = CrisisMultiplierConfig()

        assert config.max_multiplier == DEFAULT_MAX_MULTIPLIER

    def test_disabled_config_returns_1x(self):
        """비활성화 시 항상 1.0 반환."""
        config = CrisisMultiplierConfig(enabled=False)

        assert config.get_multiplier(EmergencyLevel.NORMAL) == 1.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_1) == 1.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_2) == 1.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 1.0

    def test_custom_multipliers(self):
        """커스텀 가중치 설정."""
        config = CrisisMultiplierConfig(
            multipliers={
                EmergencyLevel.NORMAL: 1.0,
                EmergencyLevel.LEVEL_1: 2.0,
                EmergencyLevel.LEVEL_2: 4.0,
                EmergencyLevel.LEVEL_3: 8.0,
            }
        )

        assert config.get_multiplier(EmergencyLevel.LEVEL_1) == 2.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_2) == 4.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 8.0

    def test_max_multiplier_cap(self):
        """max_multiplier가 가중치를 제한."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 100.0},
            max_multiplier=10.0,
        )

        # 100.0 설정했지만 10.0으로 제한됨
        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 10.0

    def test_unknown_level_returns_1x(self):
        """없는 레벨은 1.0 반환."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 5.0}  # 다른 레벨 없음
        )

        # 설정되지 않은 레벨은 1.0 반환
        assert config.get_multiplier(EmergencyLevel.NORMAL) == 1.0

    def test_from_dict_valid_data(self):
        """딕셔너리에서 정상 생성."""
        data = {
            "multipliers": {
                "NORMAL": 1.0,
                "LEVEL_1": 2.0,
                "LEVEL_2": 4.0,
                "LEVEL_3": 8.0,
            },
            "enabled": True,
            "max_multiplier": 15.0,
        }

        config = CrisisMultiplierConfig.from_dict(data)

        assert config.get_multiplier(EmergencyLevel.LEVEL_1) == 2.0
        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 8.0
        assert config.enabled is True
        assert config.max_multiplier == 15.0

    def test_from_dict_invalid_level_ignored(self):
        """유효하지 않은 레벨 이름은 무시."""
        data = {
            "multipliers": {
                "NORMAL": 1.0,
                "INVALID_LEVEL": 99.0,  # 무시됨
                "LEVEL_3": 5.0,
            },
        }

        config = CrisisMultiplierConfig.from_dict(data)

        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 5.0
        # INVALID_LEVEL은 무시되었으므로 기본값

    def test_from_dict_empty_uses_defaults(self):
        """빈 딕셔너리는 기본값 사용."""
        config = CrisisMultiplierConfig.from_dict({})

        assert config.get_multiplier(EmergencyLevel.LEVEL_3) == 5.0
        assert config.enabled is True

    def test_to_dict_roundtrip(self):
        """to_dict -> from_dict 왕복 테스트."""
        original = CrisisMultiplierConfig(
            multipliers={
                EmergencyLevel.NORMAL: 1.0,
                EmergencyLevel.LEVEL_3: 7.5,
            },
            enabled=True,  # enabled=True로 설정해야 가중치가 적용됨
            max_multiplier=20.0,
        )

        data = original.to_dict()
        restored = CrisisMultiplierConfig.from_dict(data)

        assert restored.enabled == original.enabled
        assert restored.max_multiplier == original.max_multiplier
        assert restored.get_multiplier(EmergencyLevel.LEVEL_3) == 7.5


# =============================================================================
# CrisisMultiplierProvider Tests
# =============================================================================


class TestCrisisMultiplierProvider:
    """CrisisMultiplierProvider 단위 테스트."""

    def test_normal_level_returns_1x(self):
        """NORMAL 레벨에서 1.0x 반환."""
        provider = CrisisMultiplierProvider()
        mock_tracker = create_mock_tracker(EmergencyLevel.NORMAL)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        assert multiplier == 1.0

    def test_level_1_returns_1_5x(self):
        """LEVEL_1에서 1.5x 반환."""
        provider = CrisisMultiplierProvider()
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_1)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        assert multiplier == 1.5

    def test_level_2_returns_3x(self):
        """LEVEL_2에서 3.0x 반환."""
        provider = CrisisMultiplierProvider()
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_2)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        assert multiplier == 3.0

    def test_level_3_returns_5x(self):
        """LEVEL_3에서 5.0x 반환."""
        provider = CrisisMultiplierProvider()
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        assert multiplier == 5.0

    def test_custom_config_applied(self):
        """커스텀 설정이 적용되는지 확인."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 10.0},
        )
        provider = CrisisMultiplierProvider(config=config)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        assert multiplier == 10.0

    def test_max_multiplier_cap_in_provider(self):
        """Provider에서도 max_multiplier 제한 적용."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 100.0},
            max_multiplier=10.0,
        )
        provider = CrisisMultiplierProvider(config=config)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        # 100.0 설정했지만 10.0으로 제한
        assert multiplier == 10.0

    def test_cache_returns_cached_value(self):
        """캐시 내에서 같은 값 반환 (tracker 재호출 없음)."""
        provider = CrisisMultiplierProvider(cache_ttl=30.0)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 첫 번째 호출
        multiplier1 = provider.get_current_multiplier()

        # tracker 레벨 변경 (캐시 안에서는 반영 안됨)
        mock_tracker.get_effective_state.return_value = create_mock_state(
            level=EmergencyLevel.NORMAL
        )

        # 두 번째 호출 (캐시에서)
        multiplier2 = provider.get_current_multiplier()

        assert multiplier1 == 5.0
        assert multiplier2 == 5.0  # 여전히 캐시된 값
        # get_effective_state는 1번만 호출됨
        assert mock_tracker.get_effective_state.call_count == 1

    def test_bypass_cache_forces_fresh_query(self):
        """bypass_cache=True면 캐시 무시."""
        provider = CrisisMultiplierProvider(cache_ttl=30.0)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 첫 번째 호출 (캐시 저장)
        multiplier1 = provider.get_current_multiplier()

        # tracker 레벨 변경
        mock_tracker.get_effective_state.return_value = create_mock_state(
            level=EmergencyLevel.LEVEL_1
        )

        # bypass_cache=True로 호출
        multiplier2 = provider.get_current_multiplier(bypass_cache=True)

        assert multiplier1 == 5.0
        assert multiplier2 == 1.5  # 새 값 조회

    def test_invalidate_cache_clears_cache(self):
        """invalidate_cache()가 캐시를 비움."""
        provider = CrisisMultiplierProvider(cache_ttl=30.0)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 첫 번째 호출 (캐시 저장)
        multiplier1 = provider.get_current_multiplier()

        # 캐시 무효화
        provider.invalidate_cache()

        # tracker 레벨 변경
        mock_tracker.get_effective_state.return_value = create_mock_state(
            level=EmergencyLevel.LEVEL_1
        )

        # 다시 호출 (새로 조회됨)
        multiplier2 = provider.get_current_multiplier()

        assert multiplier1 == 5.0
        assert multiplier2 == 1.5  # 무효화 후 새 값
        assert mock_tracker.get_effective_state.call_count == 2

    def test_cache_expires_after_ttl(self):
        """TTL 경과 후 캐시 만료."""
        provider = CrisisMultiplierProvider(cache_ttl=0.1)  # 0.1초 TTL
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 첫 번째 호출
        multiplier1 = provider.get_current_multiplier()

        # tracker 레벨 변경
        mock_tracker.get_effective_state.return_value = create_mock_state(
            level=EmergencyLevel.LEVEL_2
        )

        # TTL 경과 대기
        time.sleep(0.15)

        # 두 번째 호출 (TTL 만료로 새로 조회)
        multiplier2 = provider.get_current_multiplier()

        assert multiplier1 == 5.0
        assert multiplier2 == 3.0  # 새 레벨 반영

    def test_namespace_changes_trigger_fresh_query(self):
        """namespace가 바뀌면 캐시 미사용."""
        provider = CrisisMultiplierProvider(cache_ttl=30.0)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 첫 번째 호출 (namespace="seoul")
        provider.get_current_multiplier(namespace="seoul")

        # 다른 namespace로 호출
        mock_tracker.get_effective_state.return_value = create_mock_state(
            level=EmergencyLevel.LEVEL_1,
            namespace="tokyo",
        )
        multiplier2 = provider.get_current_multiplier(namespace="tokyo")

        # 두 번 호출됨 (namespace가 다르므로)
        assert mock_tracker.get_effective_state.call_count == 2
        assert multiplier2 == 1.5

    def test_set_multiplier_override(self):
        """set_multiplier_override로 런타임 가중치 변경."""
        provider = CrisisMultiplierProvider()
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # 기본값 확인
        assert provider.get_current_multiplier() == 5.0

        # 오버라이드 적용
        provider.set_multiplier_override(EmergencyLevel.LEVEL_3, 8.0)

        # 캐시가 무효화되어 새 값 반영
        assert provider.get_current_multiplier() == 8.0

    def test_set_multiplier_override_respects_max(self):
        """set_multiplier_override도 max_multiplier 제한 적용."""
        config = CrisisMultiplierConfig(max_multiplier=10.0)
        provider = CrisisMultiplierProvider(config=config)
        mock_tracker = create_mock_tracker(EmergencyLevel.LEVEL_3)
        provider._emergency_tracker = mock_tracker

        # max를 초과하는 값 설정
        provider.set_multiplier_override(EmergencyLevel.LEVEL_3, 100.0)

        # 10.0으로 제한됨
        assert provider.get_current_multiplier() == 10.0

    def test_get_all_multipliers(self):
        """모든 레벨의 가중치 반환."""
        provider = CrisisMultiplierProvider()

        all_multipliers = provider.get_all_multipliers()

        assert all_multipliers["NORMAL"] == 1.0
        assert all_multipliers["LEVEL_1"] == 1.5
        assert all_multipliers["LEVEL_2"] == 3.0
        assert all_multipliers["LEVEL_3"] == 5.0

    def test_get_config(self):
        """현재 설정 반환."""
        config = CrisisMultiplierConfig(max_multiplier=20.0)
        provider = CrisisMultiplierProvider(config=config)

        returned_config = provider.get_config()

        assert returned_config.max_multiplier == 20.0

    def test_tracker_error_returns_normal_multiplier(self):
        """Tracker 오류 시 NORMAL 가중치(1.0) 반환."""
        provider = CrisisMultiplierProvider()
        mock_tracker = MagicMock()
        mock_tracker.get_effective_state.side_effect = Exception("Connection error")
        provider._emergency_tracker = mock_tracker

        multiplier = provider.get_current_multiplier()

        # 오류 시 안전하게 1.0 반환
        assert multiplier == 1.0


# =============================================================================
# Singleton Factory Tests
# =============================================================================


class TestCrisisMultiplierSingleton:
    """싱글톤 팩토리 테스트."""

    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_crisis_multiplier_provider()

    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_crisis_multiplier_provider()

    def test_get_returns_singleton(self):
        """get_crisis_multiplier_provider는 같은 인스턴스 반환."""
        provider1 = get_crisis_multiplier_provider()
        provider2 = get_crisis_multiplier_provider()

        assert provider1 is provider2

    def test_configure_creates_new_instance(self):
        """configure_crisis_multiplier_provider는 새 인스턴스 생성."""
        provider1 = get_crisis_multiplier_provider()

        config = CrisisMultiplierConfig(max_multiplier=20.0)
        provider2 = configure_crisis_multiplier_provider(config=config)

        assert provider2 is not provider1
        assert provider2.config.max_multiplier == 20.0

    def test_reset_clears_singleton(self):
        """reset_crisis_multiplier_provider는 싱글톤 초기화."""
        provider1 = get_crisis_multiplier_provider()

        reset_crisis_multiplier_provider()

        provider2 = get_crisis_multiplier_provider()

        assert provider2 is not provider1
