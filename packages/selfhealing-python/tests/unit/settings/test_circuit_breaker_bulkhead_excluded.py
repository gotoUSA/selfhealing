"""
CircuitBreakerSettings excluded_exceptions 테스트.

BulkheadFullError가 기본 excluded_exceptions에 포함되는지 검증합니다.
"""

from __future__ import annotations

import pytest

from selfhealing.settings.circuit_breaker import (
    CircuitBreakerSettings,
    get_circuit_breaker_settings,
    reset_circuit_breaker_settings,
)


@pytest.fixture(autouse=True)
def reset_settings():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_circuit_breaker_settings()
    yield
    reset_circuit_breaker_settings()


class TestCircuitBreakerExcludedExceptions:
    """excluded_exceptions 설정 테스트."""

    def test_bulkhead_error_in_default_excluded(self):
        """BulkheadFullError가 기본 excluded_exceptions에 포함."""
        settings = CircuitBreakerSettings()

        assert len(settings.excluded_exceptions) > 0
        assert "selfhealing.resilience.bulkhead.exceptions.BulkheadFullError" in settings.excluded_exceptions

    def test_get_circuit_breaker_settings_has_bulkhead_excluded(self):
        """싱글톤에서도 BulkheadFullError 포함 확인."""
        settings = get_circuit_breaker_settings()

        assert "selfhealing.resilience.bulkhead.exceptions.BulkheadFullError" in settings.excluded_exceptions

    def test_excluded_exceptions_is_list(self):
        """excluded_exceptions이 리스트 타입."""
        settings = CircuitBreakerSettings()

        assert isinstance(settings.excluded_exceptions, list)
