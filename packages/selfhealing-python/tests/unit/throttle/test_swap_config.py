"""
AdaptiveThrottle.swap_config() 단위 테스트.

config 객체의 Atomic Swap이 정상적으로 동작하는지 검증한다.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from selfhealing.services.throttle.adaptive import (
    AdaptiveThrottle,
    reset_adaptive_throttle,
)
from selfhealing.services.throttle.config import ThrottleConfig


@pytest.fixture(autouse=True)
def _reset_throttle():
    """테스트 격리를 위해 글로벌 싱글톤 초기화."""
    reset_adaptive_throttle()
    yield
    reset_adaptive_throttle()


class TestSwapConfig:
    """AdaptiveThrottle.swap_config() 테스트."""

    def test_swap_config_returns_old_config(self):
        """swap_config()는 이전 config 객체를 반환해야 한다."""
        config = ThrottleConfig(sla_warning_ms=200, sla_critical_ms=500)
        throttle = AdaptiveThrottle(config)

        new_config = config.model_copy(update={"sla_warning_ms": 300})
        old_config = throttle.swap_config(new_config)

        assert old_config.sla_warning_ms == config.sla_warning_ms
        assert old_config.sla_critical_ms == config.sla_critical_ms

    def test_swap_config_applies_new_config(self):
        """swap_config() 후 throttle.config가 새 config로 교체되어야 한다."""
        config = ThrottleConfig(sla_warning_ms=200, sla_critical_ms=500)
        throttle = AdaptiveThrottle(config)

        new_sla_warning = 300
        new_sla_critical = 700
        new_config = config.model_copy(update={"sla_warning_ms": new_sla_warning, "sla_critical_ms": new_sla_critical})
        throttle.swap_config(new_config)

        assert throttle.config.sla_warning_ms == new_sla_warning
        assert throttle.config.sla_critical_ms == new_sla_critical

    def test_swap_config_preserves_current_limit(self):
        """swap_config()는 _current_limit 등 파생 상태를 변경하지 않아야 한다."""
        config = ThrottleConfig(initial_limit=100, sla_warning_ms=200)
        throttle = AdaptiveThrottle(config)

        limit_before = throttle._current_limit

        new_config = config.model_copy(update={"sla_warning_ms": 300})
        throttle.swap_config(new_config)

        assert throttle._current_limit == limit_before

    def test_swap_config_original_config_unchanged(self):
        """model_copy()로 생성한 새 config에 의해 원본 config가 변경되지 않아야 한다."""
        config = ThrottleConfig(sla_warning_ms=200, sla_critical_ms=500)
        throttle = AdaptiveThrottle(config)

        original_warning = config.sla_warning_ms
        original_critical = config.sla_critical_ms

        new_config = config.model_copy(update={"sla_warning_ms": 999})
        throttle.swap_config(new_config)

        # 원본 config 불변 검증
        assert config.sla_warning_ms == original_warning
        assert config.sla_critical_ms == original_critical

    def test_swap_config_logs_info(self, caplog):
        """swap_config()는 변경 정보를 로깅해야 한다."""
        config = ThrottleConfig(sla_warning_ms=200, sla_critical_ms=500)
        throttle = AdaptiveThrottle(config)

        new_config = config.model_copy(update={"sla_warning_ms": 250})

        with caplog.at_level("INFO"):
            throttle.swap_config(new_config)

        assert any("Config swapped" in record.message for record in caplog.records)
