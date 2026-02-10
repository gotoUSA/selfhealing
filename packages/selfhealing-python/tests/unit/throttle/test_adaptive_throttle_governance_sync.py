"""
AdaptiveThrottle Governance 통합 상태 동기화 테스트.

테스트 대상:
1. _sync_governance_state() — Emergency + Kill Switch + Break Glass 통합 동기화
2. _sync_kill_switch_state() — Kill Switch Drift 교정 (is_system_enabled 기반)
3. check_and_sync_emergency_state() — _sync_governance_state() 하위호환 래퍼
4. sync_emergency_state_on_init() — get_emergency_manager() 경유 초기화
5. check()에서 _sync_governance_state() 호출 확인
"""

import time

import pytest
from unittest.mock import MagicMock, patch


class MockEmergencyLevel:
    """테스트용 EmergencyLevel Mock."""

    def __init__(self, value: int):
        self.value = value
        self.name = f"LEVEL_{value}" if value > 0 else "NORMAL"


class TestSyncGovernanceStateCallsAllSyncs:
    """_sync_governance_state()가 Kill Switch + Break Glass + Emergency를 모두 동기화하는지 확인."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_calls_sync_kill_switch_state(self, mock_get_manager):
        """_sync_governance_state()가 _sync_kill_switch_state()를 호출."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = self._create_throttle()
        throttle._last_emergency_check_time = 0  # TTL 만료

        with patch.object(throttle, "_sync_kill_switch_state") as mock_ks:
            throttle._sync_governance_state()
            mock_ks.assert_called_once()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_calls_sync_break_glass_state(self, mock_get_manager):
        """_sync_governance_state()가 _sync_break_glass_state()를 호출."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = self._create_throttle()
        throttle._last_emergency_check_time = 0

        with patch.object(throttle, "_sync_break_glass_state") as mock_bg:
            throttle._sync_governance_state()
            mock_bg.assert_called_once()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_syncs_emergency_level_via_get_emergency_manager(self, mock_get_manager):
        """Emergency Level 동기화가 get_emergency_manager()를 사용."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = self._create_throttle()
        throttle._last_emergency_check_time = 0

        throttle._sync_governance_state()

        mock_get_manager.assert_called_once()
        mock_manager.get_current_level.assert_called_once()

    def test_ttl_skips_all_syncs(self):
        """TTL 미만료 시 모든 동기화 스킵."""
        throttle = self._create_throttle()
        throttle._last_emergency_check_time = time.time()  # 방금 체크

        with patch.object(throttle, "_sync_kill_switch_state") as mock_ks:
            with patch.object(throttle, "_sync_break_glass_state") as mock_bg:
                result = throttle._sync_governance_state()

                mock_ks.assert_not_called()
                mock_bg.assert_not_called()
                assert result is False


class TestSyncKillSwitchState:
    """_sync_kill_switch_state() Kill Switch Drift 교정 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=False)
    def test_detects_kill_switch_activation_drift(self, mock_is_enabled):
        """시스템 비활성(Kill Switch ON) + 로컬 미반영 → Drift 교정: gradient freeze."""
        throttle = self._create_throttle()
        assert throttle._kill_switch_active is False
        assert throttle._gradient_frozen is False

        throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is True
        assert throttle._gradient_frozen is True

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=True)
    def test_detects_kill_switch_deactivation_drift(self, mock_is_enabled):
        """시스템 활성(Kill Switch OFF) + 로컬 활성 → Drift 교정: gradient unfreeze."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True
        throttle._emergency_level = 0

        throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is False
        assert throttle._gradient_frozen is False

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=True)
    def test_keeps_frozen_when_level3_on_deactivation(self, mock_is_enabled):
        """Kill Switch 비활성화 Drift 교정 시 LEVEL_3이면 frozen 유지."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True
        throttle._emergency_level = 3

        throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is False
        assert throttle._gradient_frozen is True  # LEVEL_3가 frozen 유지

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=True)
    def test_starts_recovery_dampening_on_deactivation_drift(self, mock_is_enabled):
        """Kill Switch 비활성화 Drift 교정 시 Recovery Dampening 시작."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True

        with patch.object(throttle, "start_recovery_dampening") as mock_recovery:
            throttle._sync_kill_switch_state()
            mock_recovery.assert_called_once()

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=False)
    def test_no_op_when_already_synced_active(self, mock_is_enabled):
        """이미 Kill Switch 활성 상태로 동기화된 경우 no-op."""
        throttle = self._create_throttle()
        throttle._kill_switch_active = True
        throttle._gradient_frozen = True

        throttle._sync_kill_switch_state()

        # 이미 동기화됨 — 상태 변경 없음
        assert throttle._kill_switch_active is True
        assert throttle._gradient_frozen is True

    @patch("selfhealing.services.governance.checks.is_system_enabled", return_value=True)
    def test_no_op_when_already_synced_inactive(self, mock_is_enabled):
        """이미 Kill Switch 비활성 상태로 동기화된 경우 no-op."""
        throttle = self._create_throttle()
        assert throttle._kill_switch_active is False

        throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is False

    def test_fail_open_on_import_error(self):
        """Governance Import 실패 시 Fail-Open (상태 미변경)."""
        throttle = self._create_throttle()

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            side_effect=ImportError("No governance"),
        ):
            throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is False

    def test_fail_open_on_exception(self):
        """Governance 체크 예외 시 Fail-Open (상태 미변경)."""
        throttle = self._create_throttle()

        with patch(
            "selfhealing.services.governance.checks.is_system_enabled",
            side_effect=RuntimeError("DB error"),
        ):
            throttle._sync_kill_switch_state()

        assert throttle._kill_switch_active is False


class TestBackwardCompatWrapper:
    """check_and_sync_emergency_state() 하위호환 래퍼 테스트."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_delegates_to_sync_governance_state(self):
        """check_and_sync_emergency_state()가 _sync_governance_state()로 위임."""
        throttle = self._create_throttle()

        with patch.object(throttle, "_sync_governance_state", return_value=True) as mock_sync:
            result = throttle.check_and_sync_emergency_state()

            mock_sync.assert_called_once()
            assert result is True

    def test_returns_false_from_delegate(self):
        """래퍼가 delegate 반환값 False를 전달."""
        throttle = self._create_throttle()

        with patch.object(throttle, "_sync_governance_state", return_value=False) as mock_sync:
            result = throttle.check_and_sync_emergency_state()

            assert result is False


class TestSyncEmergencyStateOnInitUsesGetManager:
    """sync_emergency_state_on_init()이 get_emergency_manager()를 사용하는지 확인."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_calls_get_emergency_manager(self, mock_get_manager):
        """sync_emergency_state_on_init()이 get_emergency_manager()를 호출."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(0)
        mock_get_manager.return_value = mock_manager

        throttle = self._create_throttle()
        throttle.sync_emergency_state_on_init()

        mock_get_manager.assert_called_once()

    @patch("selfhealing.services.emergency_mode.get_emergency_manager")
    def test_adjusts_for_active_emergency(self, mock_get_manager):
        """활성 Emergency Level 감지 시 adjust_for_emergency 호출."""
        mock_manager = MagicMock()
        mock_manager.get_current_level.return_value = MockEmergencyLevel(2)
        mock_get_manager.return_value = mock_manager

        throttle = self._create_throttle()
        throttle.sync_emergency_state_on_init()

        assert throttle.get_emergency_level() == 2
        assert throttle.is_emergency_active() is True


class TestCheckCallsSyncGovernanceState:
    """check()에서 _sync_governance_state()가 호출되는지 확인."""

    def setup_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def teardown_method(self):
        from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

        reset_adaptive_throttle()

    def _create_throttle(self):
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        return AdaptiveThrottle(ThrottleConfig(initial_limit=100))

    def test_check_calls_sync_governance_state(self):
        """check() 호출 시 _sync_governance_state()가 호출됨."""
        throttle = self._create_throttle()

        with patch.object(throttle, "_sync_governance_state") as mock_sync:
            throttle.check("test_key")
            mock_sync.assert_called_once()

    def test_check_does_not_call_governance_functions_directly(self):
        """check()는 check_all_governance()를 직접 호출하지 않음 (Data Plane 원칙)."""
        throttle = self._create_throttle()

        with patch.object(throttle, "_sync_governance_state"):
            with patch("selfhealing.services.governance.checks.check_all_governance") as mock_gov:
                throttle.check("test_key")
                mock_gov.assert_not_called()


class TestNoGracefulDegradationManagerDirectImport:
    """GracefulDegradationManager 직접 import가 제거됐는지 확인."""

    def test_no_direct_gdm_import_in_sync_methods(self):
        """sync 메서드들이 GracefulDegradationManager를 직접 import하지 않음."""
        import inspect
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle

        # _sync_governance_state 소스에서 직접 import 확인
        sync_source = inspect.getsource(AdaptiveThrottle._sync_governance_state)
        assert "GracefulDegradationManager" not in sync_source
        assert "get_emergency_manager" in sync_source

        # sync_emergency_state_on_init 소스에서 직접 import 확인
        init_sync_source = inspect.getsource(AdaptiveThrottle.sync_emergency_state_on_init)
        assert "GracefulDegradationManager" not in init_sync_source
        assert "get_emergency_manager" in init_sync_source
