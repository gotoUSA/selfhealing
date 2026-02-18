"""
단위 테스트 — GradientCalculator 모듈 추출 및 싱글톤 레지스트리.

테스트 항목:
- gradient.py에서 직접 import 가능
- adaptive.py 하위 호환 import 유지
- 싱글톤 레지스트리 동일 인스턴스 반환
- Tier별 인스턴스 분리
- reset으로 전체 초기화
"""

from __future__ import annotations

import pytest

from selfhealing.services.throttle.gradient import (
    GradientCalculator,
    RTTSample,
    get_gradient_calculator,
    reset_gradient_calculators,
)


@pytest.fixture(autouse=True)
def _reset_calculators():
    """각 테스트 전후로 싱글톤 레지스트리를 초기화."""
    reset_gradient_calculators()
    yield
    reset_gradient_calculators()


class TestGradientCalculatorExtractionBehavior:
    """GradientCalculator 모듈 추출 동작 검증."""

    def test_import_from_gradient_module(self):
        """gradient.py에서 GradientCalculator를 import할 수 있다."""
        calc = GradientCalculator(smoothing_factor=0.5)
        assert calc is not None
        assert calc.smoothing_factor == 0.5

    def test_import_rtt_sample_from_gradient_module(self):
        """gradient.py에서 RTTSample을 import할 수 있다."""
        sample = RTTSample(timestamp=1.0, rtt_ms=100.0)
        assert sample.timestamp == 1.0
        assert sample.rtt_ms == 100.0

    def test_import_from_adaptive_backward_compatible(self):
        """adaptive.py에서도 여전히 GradientCalculator를 import할 수 있다 (하위 호환)."""
        from selfhealing.services.throttle.adaptive import (
            GradientCalculator as AdaptiveGradientCalculator,
        )
        from selfhealing.services.throttle.adaptive import (
            RTTSample as AdaptiveRTTSample,
        )

        assert AdaptiveGradientCalculator is GradientCalculator
        assert AdaptiveRTTSample is RTTSample

    def test_gradient_calculator_add_sample_and_snapshot(self):
        """GradientCalculator가 추출 후에도 정상 동작한다."""
        calc = GradientCalculator(smoothing_factor=0.5)

        calc.add_sample(100.0)
        calc.add_sample(120.0)

        rtt, gradient = calc.get_snapshot()

        assert rtt is not None
        assert rtt > 0
        assert isinstance(gradient, float)


class TestGradientCalculatorRegistryBehavior:
    """GradientCalculator 싱글톤 레지스트리 동작 검증."""

    def test_singleton_same_name_returns_same_instance(self):
        """같은 이름으로 호출하면 동일한 인스턴스를 반환한다."""
        calc1 = get_gradient_calculator("test_service")
        calc2 = get_gradient_calculator("test_service")

        assert calc1 is calc2

    def test_singleton_different_name_returns_different_instance(self):
        """다른 이름으로 호출하면 다른 인스턴스를 반환한다."""
        calc_critical = get_gradient_calculator("admission_control:critical")
        calc_standard = get_gradient_calculator("admission_control:standard")

        assert calc_critical is not calc_standard

    def test_singleton_by_tier_three_instances(self):
        """Tier별(critical, standard, non_essential) 인스턴스가 분리된다."""
        tiers = ["critical", "standard", "non_essential"]
        calculators = {t: get_gradient_calculator(f"admission_control:{t}") for t in tiers}

        # 모두 다른 인스턴스
        instances = list(calculators.values())
        assert len(set(id(c) for c in instances)) == 3

    def test_reset_clears_all_calculators(self):
        """reset_gradient_calculators()가 모든 인스턴스를 제거한다."""
        calc_before = get_gradient_calculator("test_reset")
        calc_before.add_sample(100.0)

        reset_gradient_calculators()

        calc_after = get_gradient_calculator("test_reset")
        assert calc_after is not calc_before
        # 새 인스턴스에는 데이터가 없다
        rtt = calc_after.get_current_rtt()
        assert rtt is None

    def test_default_name_calculator(self):
        """이름을 지정하지 않으면 'default' 이름의 인스턴스를 반환한다."""
        calc1 = get_gradient_calculator()
        calc2 = get_gradient_calculator("default")

        assert calc1 is calc2

    def test_tier_data_isolation(self):
        """Tier별 Calculator에 추가한 RTT 데이터가 다른 Tier에 영향을 주지 않는다."""
        calc_critical = get_gradient_calculator("admission_control:critical")
        calc_non_essential = get_gradient_calculator("admission_control:non_essential")

        calc_critical.add_sample(50.0)

        assert calc_critical.get_current_rtt() is not None
        assert calc_non_essential.get_current_rtt() is None
