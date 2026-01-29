"""
RuntimeConfigManager Throttle Config Unit Tests.

get_throttle_config, update_throttle_config 메서드 테스트.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestRuntimeConfigManagerThrottleConfig:
    """RuntimeConfigManager Throttle 설정 테스트."""

    def test_get_throttle_config_returns_dict(self):
        """get_throttle_config()가 dict 반환하는지 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        # Mock StateBackend to avoid Redis dependency
        with patch.object(RuntimeConfigManager, "_get_config") as mock_get:
            mock_get.return_value = {
                "sla_warning_ms": 200,
                "sla_critical_ms": 500,
                "initial_limit": 100,
            }

            manager = RuntimeConfigManager()
            config = manager.get_throttle_config()

            assert isinstance(config, dict)
            mock_get.assert_called_once_with("throttle")

    def test_update_throttle_config_sla_thresholds(self):
        """SLA 임계값 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {
                "sla_warning_ms": 300,
                "sla_critical_ms": 600,
            }

            manager = RuntimeConfigManager()
            result = manager.update_throttle_config(
                sla_warning_ms=300,
                sla_critical_ms=600,
            )

            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[0][0] == "throttle"
            assert call_args[1]["sla_warning_ms"] == 300
            assert call_args[1]["sla_critical_ms"] == 600

    def test_update_throttle_config_limit_settings(self):
        """limit 설정 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {
                "initial_limit": 200,
                "min_limit": 20,
                "max_limit": 1000,
            }

            manager = RuntimeConfigManager()
            result = manager.update_throttle_config(
                initial_limit=200,
                min_limit=20,
                max_limit=1000,
            )

            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[1]["initial_limit"] == 200
            assert call_args[1]["min_limit"] == 20
            assert call_args[1]["max_limit"] == 1000

    def test_update_throttle_config_gradient_settings(self):
        """Gradient 계산 설정 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {}

            manager = RuntimeConfigManager()
            manager.update_throttle_config(
                smoothing_factor=0.7,
                sample_interval_ms=1000,
                decrease_ratio=0.85,
                increase_step=2,
            )

            call_args = mock_update.call_args
            assert call_args[1]["smoothing_factor"] == 0.7
            assert call_args[1]["sample_interval_ms"] == 1000
            assert call_args[1]["decrease_ratio"] == 0.85
            assert call_args[1]["increase_step"] == 2

    def test_update_throttle_config_emergency_multipliers(self):
        """Emergency 레벨별 배율 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {}

            manager = RuntimeConfigManager()
            manager.update_throttle_config(
                emergency_level_0_multiplier=1.0,
                emergency_level_1_multiplier=0.7,
                emergency_level_2_multiplier=0.4,
                emergency_level_3_multiplier=0.0,
            )

            call_args = mock_update.call_args
            assert call_args[1]["emergency_level_0_multiplier"] == 1.0
            assert call_args[1]["emergency_level_1_multiplier"] == 0.7
            assert call_args[1]["emergency_level_2_multiplier"] == 0.4
            assert call_args[1]["emergency_level_3_multiplier"] == 0.0

    def test_update_throttle_config_cb_integration(self):
        """CB 연동 설정 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {}

            manager = RuntimeConfigManager()
            manager.update_throttle_config(
                cb_open_limit_percent=0.1,
                cb_half_open_limit_percent=0.6,
            )

            call_args = mock_update.call_args
            assert call_args[1]["cb_open_limit_percent"] == 0.1
            assert call_args[1]["cb_half_open_limit_percent"] == 0.6

    def test_update_throttle_config_recovery_dampening(self):
        """Recovery Dampening 설정 업데이트 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {}

            manager = RuntimeConfigManager()
            manager.update_throttle_config(
                recovery_dampening_enabled=False,
                recovery_step_interval_seconds=60.0,
            )

            call_args = mock_update.call_args
            assert call_args[1]["recovery_dampening_enabled"] is False
            assert call_args[1]["recovery_step_interval_seconds"] == 60.0

    def test_update_throttle_config_none_values_ignored(self):
        """None 값은 업데이트에서 제외되는지 테스트."""
        from selfhealing.services.runtime_config import (
            RuntimeConfigManager,
            reset_runtime_config_manager,
        )

        reset_runtime_config_manager()

        with patch.object(RuntimeConfigManager, "_update_config") as mock_update:
            mock_update.return_value = {}

            manager = RuntimeConfigManager()
            manager.update_throttle_config(
                sla_warning_ms=300,
                sla_critical_ms=None,  # None은 제외됨
                initial_limit=None,  # None은 제외됨
            )

            call_args = mock_update.call_args
            assert "sla_warning_ms" in call_args[1]
            assert "sla_critical_ms" not in call_args[1]
            assert "initial_limit" not in call_args[1]


class TestThrottleConfigStorageKey:
    """Throttle config storage key 테스트."""

    def test_throttle_storage_key_exists(self):
        """STORAGE_KEYS에 throttle 키가 있는지 테스트."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "throttle" in STORAGE_KEYS
        assert STORAGE_KEYS["throttle"] == "runtime_config:throttle"

    def test_throttle_config_class_exists(self):
        """CONFIG_CLASSES에 throttle 설정 클래스가 있는지 테스트."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES

        assert "throttle" in CONFIG_CLASSES
        # ThrottleSettings 클래스가 매핑됨
        assert CONFIG_CLASSES["throttle"] is not None
