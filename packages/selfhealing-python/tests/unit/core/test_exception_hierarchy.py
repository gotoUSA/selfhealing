"""
Exception Hierarchy (312) 단위 테스트.

검증 대상:
- SelfHealingError base 클래스 및 전체 예외 계층
- extra_context() 메서드
- 각 도메인별 예외가 올바른 상속 체인을 가지는지

기법 분류:
- 계약 검증: 예외 계층 구조, extra_context() 반환값
- 동작 검증: catch-all 패턴, 메시지/코드 전달
"""

from __future__ import annotations

import pytest

from selfhealing.core.exceptions import (
    AdapterConnectionError,
    AdapterError,
    AdapterInitializationError,
    AdapterNotFoundError,
    CircuitBreakerError,
    CircuitBreakerTransitionError,
    ConfigurationError,
    DLQEntryNotFoundError,
    DLQError,
    DLQReplayError,
    ResilienceError,
    RetryExhaustedError,
    RunbookError,
    SelfHealingError,
    SettingsValidationError,
)

# =============================================================================
# 계약 검증 — 예외 계층 구조
# =============================================================================


class TestExceptionHierarchyContract:
    """예외 계층 구조가 312 설계 계약대로 구성되어 있는지 검증."""

    def test_selfhealing_error_inherits_from_exception(self):
        """SelfHealingError는 Exception을 상속해야 한다."""
        assert issubclass(SelfHealingError, Exception)

    def test_adapter_error_inherits_from_selfhealing_error(self):
        """AdapterError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(AdapterError, SelfHealingError)

    def test_adapter_not_found_inherits_from_adapter_error(self):
        """AdapterNotFoundError는 AdapterError를 상속해야 한다."""
        assert issubclass(AdapterNotFoundError, AdapterError)

    def test_adapter_initialization_inherits_from_adapter_error(self):
        """AdapterInitializationError는 AdapterError를 상속해야 한다."""
        assert issubclass(AdapterInitializationError, AdapterError)

    def test_adapter_connection_inherits_from_adapter_error(self):
        """AdapterConnectionError는 AdapterError를 상속해야 한다."""
        assert issubclass(AdapterConnectionError, AdapterError)

    def test_circuit_breaker_error_inherits_from_selfhealing_error(self):
        """CircuitBreakerError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(CircuitBreakerError, SelfHealingError)

    def test_circuit_breaker_transition_inherits_from_circuit_breaker_error(self):
        """CircuitBreakerTransitionError는 CircuitBreakerError를 상속해야 한다."""
        assert issubclass(CircuitBreakerTransitionError, CircuitBreakerError)

    def test_dlq_error_inherits_from_selfhealing_error(self):
        """DLQError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(DLQError, SelfHealingError)

    def test_dlq_entry_not_found_inherits_from_dlq_error(self):
        """DLQEntryNotFoundError는 DLQError를 상속해야 한다."""
        assert issubclass(DLQEntryNotFoundError, DLQError)

    def test_dlq_replay_error_inherits_from_dlq_error(self):
        """DLQReplayError는 DLQError를 상속해야 한다."""
        assert issubclass(DLQReplayError, DLQError)

    def test_resilience_error_inherits_from_selfhealing_error(self):
        """ResilienceError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(ResilienceError, SelfHealingError)

    def test_retry_exhausted_inherits_from_resilience_error(self):
        """RetryExhaustedError는 ResilienceError를 상속해야 한다."""
        assert issubclass(RetryExhaustedError, ResilienceError)

    def test_runbook_error_inherits_from_selfhealing_error(self):
        """RunbookError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(RunbookError, SelfHealingError)

    def test_configuration_error_inherits_from_selfhealing_error(self):
        """ConfigurationError는 SelfHealingError를 상속해야 한다."""
        assert issubclass(ConfigurationError, SelfHealingError)

    def test_settings_validation_inherits_from_configuration_error(self):
        """SettingsValidationError는 ConfigurationError를 상속해야 한다."""
        assert issubclass(SettingsValidationError, ConfigurationError)

    def test_adapter_not_found_is_not_value_error(self):
        """AdapterNotFoundError는 ValueError가 아닌 AdapterError 계열이어야 한다."""
        err = AdapterNotFoundError("test")
        assert isinstance(err, AdapterError)
        assert isinstance(err, SelfHealingError)
        assert not isinstance(err, ValueError)

    def test_dlq_entry_not_found_is_not_value_error(self):
        """DLQEntryNotFoundError는 ValueError가 아닌 DLQError 계열이어야 한다."""
        err = DLQEntryNotFoundError("test")
        assert isinstance(err, DLQError)
        assert not isinstance(err, ValueError)


# =============================================================================
# 계약 검증 — extra_context() 메서드
# =============================================================================


class TestExtraContextContract:
    """SelfHealingError.extra_context() 설계 계약 검증."""

    def test_extra_context_with_code_returns_error_code(self):
        """code가 설정된 경우 extra_context()에 error_code 키가 포함되어야 한다."""
        err = SelfHealingError("test", code="E001")
        ctx = err.extra_context()
        assert ctx == {"error_code": "E001"}

    def test_extra_context_without_code_returns_empty_dict(self):
        """code가 빈 문자열이면 extra_context()는 빈 dict를 반환해야 한다."""
        err = SelfHealingError("test")
        assert err.extra_context() == {}

    def test_extra_context_default_code_is_empty_string(self):
        """code 기본값은 빈 문자열이어야 한다."""
        err = SelfHealingError("test")
        assert err.code == ""


# =============================================================================
# 동작 검증 — catch-all 패턴
# =============================================================================


class TestCatchAllPatternBehavior:
    """SelfHealingError로 모든 라이브러리 에러를 포괄할 수 있는지 검증."""

    @pytest.mark.parametrize(
        "error_class",
        [
            AdapterError,
            AdapterNotFoundError,
            AdapterInitializationError,
            AdapterConnectionError,
            CircuitBreakerError,
            CircuitBreakerTransitionError,
            DLQError,
            DLQEntryNotFoundError,
            DLQReplayError,
            ResilienceError,
            RetryExhaustedError,
            RunbookError,
            ConfigurationError,
            SettingsValidationError,
        ],
    )
    def test_selfhealing_error_catches_all_subclasses(self, error_class):
        """SelfHealingError로 모든 서브클래스를 catch할 수 있어야 한다."""
        with pytest.raises(SelfHealingError):
            raise error_class("test error")

    def test_message_preserved_through_hierarchy(self):
        """메시지가 예외 계층을 통해 보존되어야 한다."""
        msg = "adapter xyz not found"
        err = AdapterNotFoundError(msg)
        assert str(err) == msg

    def test_code_preserved_through_hierarchy(self):
        """code 인자가 서브클래스에서도 동작해야 한다."""
        err = DLQError("dlq failed", code="DLQ_001")
        assert err.code == "DLQ_001"
        assert err.extra_context() == {"error_code": "DLQ_001"}


# =============================================================================
# 동작 검증 — 외부 모듈 예외 계층 통합
# =============================================================================


class TestExternalExceptionIntegrationBehavior:
    """외부 모듈(CB, Bulkhead, Hedging)의 예외가 계층에 통합되었는지 검증."""

    def test_circuit_breaker_open_is_selfhealing_error(self):
        """CircuitBreakerOpenError는 SelfHealingError 계열이어야 한다."""
        from selfhealing.services.circuit_breaker.exceptions import (
            CircuitBreakerOpenError,
        )

        err = CircuitBreakerOpenError("payment")
        assert isinstance(err, CircuitBreakerError)
        assert isinstance(err, SelfHealingError)
        assert err.service_name == "payment"

    def test_bulkhead_full_is_resilience_error(self):
        """BulkheadFullError는 ResilienceError 계열이어야 한다."""
        from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

        err = BulkheadFullError("api", max_concurrent=10, active_count=10)
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_bulkhead_timeout_is_resilience_error(self):
        """BulkheadTimeoutError는 ResilienceError 계열이어야 한다."""
        from selfhealing.resilience.bulkhead.exceptions import BulkheadTimeoutError

        err = BulkheadTimeoutError("api", timeout=5.0)
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_hedging_error_is_resilience_error(self):
        """HedgingError는 ResilienceError 계열이어야 한다."""
        from selfhealing.core.hedging.exceptions import HedgingError

        err = HedgingError("test")
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_hedging_timeout_is_resilience_error(self):
        """HedgingTimeoutError는 ResilienceError 계열이어야 한다."""
        from selfhealing.core.hedging.exceptions import HedgingTimeoutError

        err = HedgingTimeoutError(timeout=3.0)
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_hedging_all_failed_is_resilience_error(self):
        """HedgingAllFailedError는 ResilienceError 계열이어야 한다."""
        from selfhealing.core.hedging.exceptions import HedgingAllFailedError

        err = HedgingAllFailedError(candidates_tried=3, errors=["e1", "e2", "e3"])
        assert isinstance(err, ResilienceError)
        assert isinstance(err, SelfHealingError)

    def test_catch_resilience_error_catches_bulkhead_and_hedging(self):
        """ResilienceError로 Bulkhead와 Hedging 예외를 모두 catch할 수 있어야 한다."""
        from selfhealing.core.hedging.exceptions import HedgingTimeoutError
        from selfhealing.resilience.bulkhead.exceptions import BulkheadFullError

        with pytest.raises(ResilienceError):
            raise BulkheadFullError("api", max_concurrent=5, active_count=5)

        with pytest.raises(ResilienceError):
            raise HedgingTimeoutError(timeout=1.0)

    def test_catch_circuit_breaker_error_catches_open_error(self):
        """CircuitBreakerError로 CircuitBreakerOpenError를 catch할 수 있어야 한다."""
        from selfhealing.services.circuit_breaker.exceptions import (
            CircuitBreakerOpenError,
        )

        with pytest.raises(CircuitBreakerError):
            raise CircuitBreakerOpenError("svc")
