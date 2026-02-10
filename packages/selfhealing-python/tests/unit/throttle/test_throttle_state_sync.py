"""
AdaptiveThrottle 상태 동기화 테스트.

테스트 대상:
1. sync_emergency_state_on_init() - 초기화 시 Emergency 상태 동기화
2. _sync_governance_state() - Governance 통합 상태 동기화 (Emergency + Kill Switch + Break Glass)
3. Drift 감지 및 자동 동기화
4. TTL 캐싱 동작
"""

import pytest
import time
from unittest.mock import MagicMock, patch

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    get_adaptive_throttle,
    reset_adaptive_throttle,
)


class MockEmergencyLevel:
    """테스트용 EmergencyLevel Mock."""

    def __init__(self, value: int):
        self.value = value
        self.name = f"LEVEL_{value}" if value > 0 else "NORMAL"


class TestSyncEmergencyStateOnInit:
    """sync_emergency_state_on_init() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_syncs_active_emergency_state(self, mock_get_manager):
        """활성화된 Emergency 상태 동기화."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(2)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.sync_emergency_state_on_init()

        assert throttle.is_emergency_active() is True
        assert throttle.get_emergency_level() == 2
        assert throttle.current_limit == 50  # 100 × 0.5

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_does_not_change_state_when_normal(self, mock_get_manager):
        """NORMAL 상태에서는 변경 없음."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle.current_limit = 100

        throttle.sync_emergency_state_on_init()

        assert throttle.is_emergency_active() is False
        assert throttle.current_limit == 100

    def test_handles_import_error_gracefully(self):
        """EmergencyMode Import 실패 시 graceful 처리."""
        throttle = get_adaptive_throttle()
        initial_limit = throttle.current_limit  # 실제 초기값 저장

        # 실제로 메서드가 예외를 처리하는지 확인
        throttle.sync_emergency_state_on_init()

        # 기존 상태 유지 (예외 발생해도 변경 없음)
        assert throttle.current_limit == initial_limit

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_updates_last_check_time(self, mock_get_manager):
        """동기화 후 last_check_time 업데이트."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        initial_time = throttle._last_emergency_check_time

        throttle.sync_emergency_state_on_init()

        assert throttle._last_emergency_check_time > initial_time


class TestSyncGovernanceState:
    """_sync_governance_state() 메서드 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_skips_check_within_ttl(self):
        """TTL 내에서는 재확인 스킵."""
        throttle = get_adaptive_throttle()
        throttle._last_emergency_check_time = time.time()  # 방금 체크함

        result = throttle.check_and_sync_emergency_state()

        assert result is False  # 동기화 발생 안함

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_checks_after_ttl_expires(self, mock_get_manager):
        """TTL 만료 후 상태 재확인."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 1
        throttle._last_emergency_check_time = time.time() - 2  # TTL 만료

        throttle._sync_governance_state()

        mock_manager.get_current_level.assert_called_once()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_detects_and_syncs_drift(self, mock_get_manager):
        """Drift 감지 시 자동 동기화."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(2)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0  # 즉시 만료
        throttle._emergency_level = 1  # 캐시된 레벨 (drift 발생)
        throttle._emergency_mode_active = True
        throttle._base_limit_before_emergency = 100
        throttle.current_limit = 80

        result = throttle._sync_governance_state()

        assert result is True  # Drift 감지됨
        assert throttle.get_emergency_level() == 2
        # adjust_for_emergency(2) 호출됨

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_no_drift_when_levels_match(self, mock_get_manager):
        """레벨 일치 시 Drift 없음."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(1)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0
        throttle._emergency_level = 1  # 캐시된 레벨과 동일

        result = throttle._sync_governance_state()

        assert result is False  # Drift 없음

    def test_handles_manager_exception(self):
        """Manager 예외 시 graceful 처리."""
        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0
        throttle.current_limit = 100

        with patch(
            "selfhealing.services.emergency_mode.get_emergency_manager",
            side_effect=Exception("Manager error"),
        ):
            result = throttle._sync_governance_state()

        assert result is False
        assert throttle.current_limit == 100  # 상태 유지


class TestCheckOnUsePatternIntegration:
    """check() 메서드에서 Governance 통합 동기화 호출 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch.object(AdaptiveThrottle, "_sync_governance_state")
    def test_check_calls_sync_on_each_request(self, mock_sync):
        """check() 호출 시 상태 동기화 호출."""
        throttle = get_adaptive_throttle()

        throttle.check("test_key")

        mock_sync.assert_called_once()

    @patch.object(AdaptiveThrottle, "_sync_governance_state")
    @patch.object(AdaptiveThrottle, "advance_recovery_dampening")
    def test_check_advances_recovery_dampening(self, mock_advance, mock_sync):
        """check() 호출 시 Recovery Dampening 진행 확인."""
        mock_sync.return_value = False

        throttle = get_adaptive_throttle()
        throttle._recovery_dampening_active = True

        throttle.check("test_key")

        mock_advance.assert_called_once()


class TestTtlCachingBehavior:
    """TTL 캐싱 동작 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    def test_default_ttl_is_30_seconds(self):
        """기본 TTL은 30초."""
        throttle = get_adaptive_throttle()

        assert throttle._emergency_cache_ttl_seconds == 30

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_respects_ttl_between_checks(self, mock_get_manager):
        """TTL 동안 재확인 스킵."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 30

        # 첫 번째 체크 (TTL 만료 상태)
        throttle._last_emergency_check_time = 0
        throttle._sync_governance_state()

        # 두 번째 체크 (TTL 내)
        throttle._sync_governance_state()

        # Manager는 한 번만 호출됨
        assert mock_manager.get_current_level.call_count == 1


class TestDriftDetectionWithFullStop:
    """Full Stop 조건과 Drift 감지 연동 테스트."""

    def setup_method(self):
        reset_adaptive_throttle()

    def teardown_method(self):
        reset_adaptive_throttle()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    @patch.object(AdaptiveThrottle, "check_full_stop_conditions")
    def test_rechecks_full_stop_at_level_3(self, mock_full_stop, mock_get_manager):
        """LEVEL_3에서 Full Stop 조건 재확인."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(3)
        mock_get_manager.return_value = mock_manager

        mock_full_stop.return_value = (True, "LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED")

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0
        throttle._emergency_level = 3  # 이미 LEVEL_3
        throttle._base_limit_before_emergency = 100

        throttle._sync_governance_state()

        mock_full_stop.assert_called_once()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    @patch.object(AdaptiveThrottle, "check_full_stop_conditions")
    @patch.object(AdaptiveThrottle, "activate_full_stop")
    def test_activates_full_stop_when_conditions_become_true(self, mock_activate, mock_full_stop, mock_get_manager):
        """조건 충족 시 Full Stop 활성화."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(3)
        mock_get_manager.return_value = mock_manager

        mock_full_stop.return_value = (True, "test_reason")

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0
        throttle._emergency_level = 3
        throttle._full_stop_active = False

        throttle._sync_governance_state()

        mock_activate.assert_called_once_with("test_reason")

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    @patch.object(AdaptiveThrottle, "check_full_stop_conditions")
    @patch.object(AdaptiveThrottle, "deactivate_full_stop")
    def test_deactivates_full_stop_when_conditions_become_false(self, mock_deactivate, mock_full_stop, mock_get_manager):
        """조건 해제 시 Full Stop 비활성화."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(3)
        mock_get_manager.return_value = mock_manager

        mock_full_stop.return_value = (False, "NORMAL")

        throttle = get_adaptive_throttle()
        throttle._emergency_cache_ttl_seconds = 0
        throttle._emergency_level = 3
        throttle._full_stop_active = True

        throttle._sync_governance_state()

        mock_deactivate.assert_called_once()
