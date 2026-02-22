"""
캐싱 테스트.

Gate 결과 캐싱 및 캐시 무효화 테스트.
"""

from unittest.mock import patch


class TestCaching:
    """캐싱 테스트."""

    def test_result_is_cached(self):
        """결과가 캐싱되는지 확인."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        call_count = 0

        def mock_get_budget(region=None):
            nonlocal call_count
            call_count += 1
            return 75.0

        with patch.object(gate, "_get_error_budget_percent", side_effect=mock_get_budget):
            # First call
            result1 = gate.check(force_refresh=True)
            # Second call (should use cache)
            result2 = gate.check()
            # Third call (should use cache)
            result3 = gate.check()

        # Only called once due to caching
        assert call_count == 1
        assert result1.allowed == result2.allowed == result3.allowed

    def test_force_refresh_bypasses_cache(self):
        """force_refresh가 캐시를 무시하는지 확인."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        call_count = 0

        def mock_get_budget(region=None):
            nonlocal call_count
            call_count += 1
            return 75.0

        with patch.object(gate, "_get_error_budget_percent", side_effect=mock_get_budget):
            # First call
            gate.check(force_refresh=True)
            # Second call with force_refresh
            gate.check(force_refresh=True)

        # Called twice due to force_refresh
        assert call_count == 2

    def test_clear_cache(self):
        """캐시 초기화 테스트."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            cache_ttl_seconds=60,
        )
        gate = ErrorBudgetGate(config=config)

        call_count = 0

        def mock_get_budget(region=None):
            nonlocal call_count
            call_count += 1
            return 75.0

        with patch.object(gate, "_get_error_budget_percent", side_effect=mock_get_budget):
            gate.check(force_refresh=True)
            gate.clear_cache()
            gate.check()

        # Called twice (before and after cache clear)
        assert call_count == 2


class TestConfigUpdate:
    """설정 업데이트 테스트."""

    def test_update_config(self):
        """설정 업데이트 테스트."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)

        # Update config
        with patch.object(gate, "_persist_config"):
            updated = gate.update_config(critical_threshold_percent=15.0)

        assert updated.critical_threshold_percent == 15.0
        assert gate.get_config().critical_threshold_percent == 15.0

    def test_update_invalidates_cache(self):
        """설정 업데이트 시 캐시 무효화."""
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )

        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=10.0,
        )
        gate = ErrorBudgetGate(config=config)

        # Populate cache
        with patch.object(gate, "_get_error_budget_percent", return_value=75.0):
            gate.check(force_refresh=True)

        # Update config
        with patch.object(gate, "_persist_config"):
            gate.update_config(critical_threshold_percent=15.0)

        # Cache should be invalidated (clear() empties the dict)
        assert gate._cache == {}
