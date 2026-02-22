"""
AdaptiveThrottle.swap_config() 단위 테스트.

config 객체의 Atomic Swap이 정상적으로 동작하는지 검증한다.
"""

from __future__ import annotations

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


@pytest.fixture
def default_config():
    """기본 ThrottleConfig 인스턴스 (기본값 참조용)."""
    return ThrottleConfig()


class TestSwapConfig:
    """AdaptiveThrottle.swap_config() 테스트."""

    def test_swap_config_returns_old_config(self, default_config):
        """swap_config()는 이전 config 객체를 반환해야 한다."""
        throttle = AdaptiveThrottle(default_config)

        new_warning = default_config.sla_warning_ms + 100
        new_config = default_config.model_copy(update={"sla_warning_ms": new_warning})
        old_config = throttle.swap_config(new_config)

        # 반환된 old_config는 교체 전 값과 동일
        assert old_config.sla_warning_ms == default_config.sla_warning_ms
        assert old_config.sla_critical_ms == default_config.sla_critical_ms

    def test_swap_config_applies_new_config(self, default_config):
        """swap_config() 후 throttle.config가 새 config로 교체되어야 한다."""
        throttle = AdaptiveThrottle(default_config)

        new_sla_warning = default_config.sla_warning_ms + 100
        new_sla_critical = default_config.sla_critical_ms + 200
        new_config = default_config.model_copy(
            update={"sla_warning_ms": new_sla_warning, "sla_critical_ms": new_sla_critical},
        )
        throttle.swap_config(new_config)

        assert throttle.config.sla_warning_ms == new_sla_warning
        assert throttle.config.sla_critical_ms == new_sla_critical

    def test_swap_config_preserves_current_limit(self, default_config):
        """swap_config()는 _current_limit 등 파생 상태를 변경하지 않아야 한다."""
        throttle = AdaptiveThrottle(default_config)
        limit_before = throttle._current_limit

        new_warning = default_config.sla_warning_ms + 100
        new_config = default_config.model_copy(update={"sla_warning_ms": new_warning})
        throttle.swap_config(new_config)

        assert throttle._current_limit == limit_before

    def test_swap_config_original_config_unchanged(self, default_config):
        """model_copy()로 생성한 새 config에 의해 원본 config가 변경되지 않아야 한다."""
        throttle = AdaptiveThrottle(default_config)

        original_warning = default_config.sla_warning_ms
        original_critical = default_config.sla_critical_ms

        new_config = default_config.model_copy(update={"sla_warning_ms": 999})
        throttle.swap_config(new_config)

        # 원본 config 불변 검증 (Pydantic frozen model 보장)
        assert default_config.sla_warning_ms == original_warning
        assert default_config.sla_critical_ms == original_critical

    def test_swap_config_logs_info(self, default_config, caplog):
        """swap_config()는 변경 정보를 로깅해야 한다."""
        throttle = AdaptiveThrottle(default_config)

        new_warning = default_config.sla_warning_ms + 50
        new_config = default_config.model_copy(update={"sla_warning_ms": new_warning})

        with caplog.at_level("INFO"):
            throttle.swap_config(new_config)

        assert any("config_swapped" in record.message for record in caplog.records)
