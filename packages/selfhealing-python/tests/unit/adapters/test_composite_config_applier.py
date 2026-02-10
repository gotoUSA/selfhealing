"""
CompositeConfigApplier 단위 테스트.

여러 ConfigApplier를 조합하여 올바른 라우팅이 수행되는지 검증한다.
"""

from __future__ import annotations

import pytest

from selfhealing.adapters.config_applier.composite import CompositeConfigApplier


class StubConfigApplier:
    """테스트용 ConfigApplier 스텁."""

    def __init__(self, handled_params: set[str] | None = None):
        self._handled_params = handled_params or set()
        self._values: dict[str, float] = {}

    def get_current(self, parameter: str) -> float:
        if parameter not in self._handled_params:
            raise ValueError(f"Not handled: {parameter}")
        return self._values.get(parameter, 0.0)

    def apply(self, parameter: str, value: float) -> bool:
        if parameter not in self._handled_params:
            return False
        self._values[parameter] = value
        return True

    def rollback(self, parameter: str, value: float) -> bool:
        if parameter not in self._handled_params:
            return False
        self._values[parameter] = value
        return True


class FallbackConfigApplier:
    """모든 파라미터를 수용하는 Fallback 스텁."""

    def __init__(self):
        self._values: dict[str, float] = {}

    def get_current(self, parameter: str) -> float:
        return self._values.get(parameter, 0.0)

    def apply(self, parameter: str, value: float) -> bool:
        self._values[parameter] = value
        return True

    def rollback(self, parameter: str, value: float) -> bool:
        self._values[parameter] = value
        return True


@pytest.fixture
def throttle_applier():
    """SLA 파라미터만 처리하는 스텁."""
    return StubConfigApplier({"throttle_sla_warning_ms", "throttle_sla_critical_ms"})


@pytest.fixture
def fallback_applier():
    """모든 파라미터를 처리하는 Fallback."""
    return FallbackConfigApplier()


@pytest.fixture
def composite(throttle_applier, fallback_applier):
    """Composite = ThrottleStub + Fallback."""
    return CompositeConfigApplier([throttle_applier, fallback_applier])


class TestCompositeConfigApplierInit:
    """생성자 테스트."""

    def test_empty_appliers_raises(self):
        """빈 appliers 리스트는 ValueError를 발생시켜야 한다."""
        with pytest.raises(ValueError, match="requires at least one applier"):
            CompositeConfigApplier([])


class TestCompositeConfigApplierApply:
    """apply() 라우팅 테스트."""

    def test_apply_routes_to_first_handler(self, composite, throttle_applier):
        """SLA 파라미터는 첫 번째 applier(throttle)가 처리해야 한다."""
        result = composite.apply("throttle_sla_warning_ms", 250.0)

        assert result is True
        assert throttle_applier._values["throttle_sla_warning_ms"] == 250.0

    def test_apply_falls_through_to_fallback(self, composite, throttle_applier, fallback_applier):
        """비-SLA 파라미터는 첫 번째 applier를 건너뛰고 fallback이 처리해야 한다."""
        result = composite.apply("circuit_breaker_threshold", 5.0)

        assert result is True
        assert "circuit_breaker_threshold" not in throttle_applier._values
        assert fallback_applier._values["circuit_breaker_threshold"] == 5.0

    def test_apply_returns_false_when_no_handler(self):
        """모든 applier가 거부하면 False를 반환해야 한다."""
        strict_applier = StubConfigApplier({"only_this"})
        composite = CompositeConfigApplier([strict_applier])

        result = composite.apply("unknown_param", 100.0)
        assert result is False


class TestCompositeConfigApplierGetCurrent:
    """get_current() 라우팅 테스트."""

    def test_get_current_from_first_handler(self, composite, throttle_applier):
        """SLA 파라미터는 첫 번째 applier에서 값을 조회해야 한다."""
        throttle_applier._values["throttle_sla_warning_ms"] = 200.0
        assert composite.get_current("throttle_sla_warning_ms") == 200.0

    def test_get_current_falls_through_to_fallback(self, composite, fallback_applier):
        """비-SLA 파라미터는 fallback에서 조회해야 한다."""
        fallback_applier._values["timeout_ms"] = 3000.0
        assert composite.get_current("timeout_ms") == 3000.0

    def test_get_current_raises_when_no_handler(self):
        """모든 applier가 실패하면 ValueError를 발생시켜야 한다."""
        strict_applier = StubConfigApplier({"only_this"})
        another = StubConfigApplier({"only_that"})
        composite = CompositeConfigApplier([strict_applier, another])

        with pytest.raises(ValueError, match="No applier can handle"):
            composite.get_current("unknown_param")


class TestCompositeConfigApplierRollback:
    """rollback() 라우팅 테스트."""

    def test_rollback_routes_to_first_handler(self, composite, throttle_applier):
        """SLA 파라미터 롤백은 첫 번째 applier가 처리해야 한다."""
        result = composite.rollback("throttle_sla_critical_ms", 500.0)

        assert result is True
        assert throttle_applier._values["throttle_sla_critical_ms"] == 500.0

    def test_rollback_falls_through_to_fallback(self, composite, fallback_applier):
        """비-SLA 파라미터 롤백은 fallback이 처리해야 한다."""
        result = composite.rollback("retry_count", 3.0)

        assert result is True
        assert fallback_applier._values["retry_count"] == 3.0

    def test_rollback_returns_false_when_no_handler(self):
        """모든 applier가 거부하면 False를 반환해야 한다."""
        strict_applier = StubConfigApplier({"only_this"})
        composite = CompositeConfigApplier([strict_applier])

        result = composite.rollback("unknown_param", 0.0)
        assert result is False


class TestCompositeConfigApplierRoutingFlow:
    """문서의 라우팅 흐름 시나리오 검증."""

    def test_throttle_sla_routed_to_throttle_applier(self, composite, throttle_applier, fallback_applier):
        """throttle_sla_warning_ms → ThrottleApplier 처리, Fallback 도달 안 함."""
        composite.apply("throttle_sla_warning_ms", 250.0)

        assert "throttle_sla_warning_ms" in throttle_applier._values
        assert "throttle_sla_warning_ms" not in fallback_applier._values

    def test_circuit_breaker_routed_to_fallback(self, composite, throttle_applier, fallback_applier):
        """circuit_breaker_threshold → ThrottleApplier skip → Fallback 처리."""
        composite.apply("circuit_breaker_threshold", 5.0)

        assert "circuit_breaker_threshold" not in throttle_applier._values
        assert "circuit_breaker_threshold" in fallback_applier._values
