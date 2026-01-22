"""
Phase 3: StateRefresherConfig, EmergencyStateRefresher 단위 테스트.

비상 상태 갱신기 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest
from unittest.mock import MagicMock

from selfhealing.services.canary.state_refresher import (
    StateRefresherConfig,
    EmergencyStateRefresher,
)
from selfhealing.services.canary.interlock import (
    CanarySafetyInterlock,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_tracker():
    """Mock NamespacedEmergencyTracker."""
    tracker = MagicMock()
    mock_state = MagicMock()
    mock_state.emergency_level = EmergencyLevel.NORMAL
    mock_state.namespace = "test-namespace"
    tracker.get_effective_state.return_value = mock_state
    return tracker


def create_mock_state(level: EmergencyLevel, namespace: str = "test-namespace"):
    """Mock ScopedEmergencyState 생성 헬퍼."""
    mock_state = MagicMock()
    mock_state.emergency_level = level
    mock_state.namespace = namespace
    return mock_state


# =============================================================================
# Test: StateRefresherConfig
# =============================================================================


class TestStateRefresherConfig:
    """StateRefresherConfig 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        config = StateRefresherConfig()
        
        assert config.refresh_interval_seconds == 30
        assert config.jitter_max_seconds == 5
        assert config.enabled is True
        assert config.on_refresh_failure_action == "log_and_continue"
        assert config.max_consecutive_failures == 3

    def test_custom_values(self):
        """커스텀 값 설정."""
        config = StateRefresherConfig(
            refresh_interval_seconds=60,
            jitter_max_seconds=10,
            enabled=False,
            on_refresh_failure_action="fail_closed",
            max_consecutive_failures=5,
        )
        
        assert config.refresh_interval_seconds == 60
        assert config.jitter_max_seconds == 10
        assert config.enabled is False
        assert config.on_refresh_failure_action == "fail_closed"
        assert config.max_consecutive_failures == 5


# =============================================================================
# Test: EmergencyStateRefresher
# =============================================================================


class TestEmergencyStateRefresher:
    """EmergencyStateRefresher 테스트."""

    def test_init_with_defaults(self):
        """기본값으로 초기화."""
        refresher = EmergencyStateRefresher()
        
        assert refresher.is_running is False
        assert refresher.last_known_levels == {}

    def test_init_with_config(self):
        """설정과 함께 초기화."""
        config = StateRefresherConfig(refresh_interval_seconds=60)
        refresher = EmergencyStateRefresher(config=config)
        
        assert refresher._config.refresh_interval_seconds == 60

    def test_start_when_disabled(self):
        """비활성화 상태에서 start() 호출 시 False 반환."""
        config = StateRefresherConfig(enabled=False)
        refresher = EmergencyStateRefresher(config=config)
        
        result = refresher.start()
        
        assert result is False
        assert refresher.is_running is False

    def test_start_and_stop(self):
        """start()와 stop() 동작."""
        config = StateRefresherConfig(
            enabled=True,
            refresh_interval_seconds=100,  # 길게 설정
        )
        refresher = EmergencyStateRefresher(config=config)
        
        # 시작
        result = refresher.start()
        assert result is True
        assert refresher.is_running is True
        
        # 중복 시작 시도
        result2 = refresher.start()
        assert result2 is False
        
        # 중지
        refresher.stop()
        assert refresher.is_running is False

    def test_force_refresh_without_interlock(self):
        """safety_interlock 없이 force_refresh 호출."""
        refresher = EmergencyStateRefresher()
        
        result = refresher.force_refresh()
        
        assert result is None

    def test_force_refresh_with_mock_interlock(self, mock_tracker):
        """Mock interlock으로 force_refresh 테스트."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL, namespace="test"
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        refresher = EmergencyStateRefresher(safety_interlock=interlock)
        
        # 초기 상태 설정
        refresher._last_known_level["test"] = 0
        
        result = refresher.force_refresh()
        
        # 레벨 변경 없으므로 None
        assert result is None
        assert refresher.last_known_levels["test"] == 0

    def test_detects_level_change(self, mock_tracker):
        """레벨 변경 감지."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_2, namespace="test"
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        mock_service = MagicMock()
        mock_service.get_active_rollouts.return_value = []
        
        refresher = EmergencyStateRefresher(
            safety_interlock=interlock,
            canary_service=mock_service,
        )
        
        # 이전 상태: NORMAL (0)
        refresher._last_known_level["test"] = 0
        
        result = refresher.force_refresh()
        
        # 레벨 상승 감지됨
        assert result is not None
        assert refresher.last_known_levels["test"] == 2

    def test_pause_all_active_rollouts(self):
        """Fail-Closed 시 모든 롤아웃 일시 중지."""
        mock_service = MagicMock()
        mock_rollout1 = MagicMock(id="rollout-1")
        mock_rollout2 = MagicMock(id="rollout-2")
        mock_service.get_active_rollouts.return_value = [mock_rollout1, mock_rollout2]
        mock_service.pause.return_value = True
        
        refresher = EmergencyStateRefresher(canary_service=mock_service)
        
        count = refresher._pause_all_active_rollouts(reason="Test reason")
        
        assert count == 2
        assert mock_service.pause.call_count == 2
