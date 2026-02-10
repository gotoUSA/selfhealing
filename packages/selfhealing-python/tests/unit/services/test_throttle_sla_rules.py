"""
THROTTLE_SLA_RULES 단위 테스트.

DecisionEngine에 주입되는 SLA 자동 조정 규칙의 조건/조정 로직을 검증한다.
"""

from __future__ import annotations

import pytest

from selfhealing.core.decision_engine import AdjustmentPriority, AdjustmentRule
from selfhealing.services.auto_tuning.throttle_sla_rules import THROTTLE_SLA_RULES


class TestThrottleSlaRulesDefinition:
    """규칙 정의 구조 검증."""

    def test_rules_count(self):
        """THROTTLE_SLA_RULES에 3개의 규칙이 있어야 한다."""
        assert len(THROTTLE_SLA_RULES) == 3

    def test_all_rules_are_adjustment_rule(self):
        """모든 규칙이 AdjustmentRule 타입이어야 한다."""
        for rule in THROTTLE_SLA_RULES:
            assert isinstance(rule, AdjustmentRule)

    def test_rule_parameters(self):
        """규칙의 파라미터가 SLA 관련이어야 한다."""
        params = {rule.parameter for rule in THROTTLE_SLA_RULES}
        assert params == {"throttle_sla_warning_ms", "throttle_sla_critical_ms"}

    def test_all_rules_use_p99_latency_metric(self):
        """모든 규칙이 p99_latency_ms 메트릭을 사용해야 한다."""
        for rule in THROTTLE_SLA_RULES:
            assert rule.metric == "p99_latency_ms"

    def test_sla_warning_rules_priority_low(self):
        """SLA Warning 규칙은 LOW 우선순위여야 한다."""
        warning_rules = [r for r in THROTTLE_SLA_RULES if r.parameter == "throttle_sla_warning_ms"]
        for rule in warning_rules:
            assert rule.priority == AdjustmentPriority.LOW

    def test_sla_critical_rule_priority_medium(self):
        """SLA Critical 규칙은 MEDIUM 우선순위여야 한다."""
        critical_rules = [r for r in THROTTLE_SLA_RULES if r.parameter == "throttle_sla_critical_ms"]
        for rule in critical_rules:
            assert rule.priority == AdjustmentPriority.MEDIUM


class TestSlaWarningUpRule:
    """SLA Warning 상향 규칙 (P99 > 90% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Warning 상향 규칙."""
        return THROTTLE_SLA_RULES[0]

    def test_condition_true_when_p99_above_90_percent(self, rule):
        """P99가 현재 값의 90%~100% 사이이면 조건 충족."""
        # current=200, metric=185(92.5%)
        assert rule.condition(200, 185) is True

    def test_condition_false_when_p99_below_90_percent(self, rule):
        """P99가 현재 값의 90% 미만이면 조건 미충족."""
        # current=200, metric=170(85%)
        assert rule.condition(200, 170) is False

    def test_condition_false_when_p99_above_current(self, rule):
        """P99가 현재 값 이상이면 조건 미충족 (이미 SLA 위반)."""
        assert rule.condition(200, 210) is False

    def test_adjustment_increases_by_15_percent(self, rule):
        """조정값은 현재 값의 115%여야 한다."""
        result = rule.adjustment(200, 185)
        assert result == min(200 * 1.15, 2000)

    def test_adjustment_capped_at_2000(self, rule):
        """조정값은 2000ms를 초과하지 않아야 한다."""
        result = rule.adjustment(1800, 1650)
        assert result == 2000


class TestSlaWarningDownRule:
    """SLA Warning 하향 규칙 (P99 < 50% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Warning 하향 규칙."""
        return THROTTLE_SLA_RULES[1]

    def test_condition_true_when_p99_below_50_percent(self, rule):
        """P99가 현재 값의 50% 미만이고 current > 100이면 조건 충족."""
        # current=200, metric=80(40%)
        assert rule.condition(200, 80) is True

    def test_condition_false_when_p99_above_50_percent(self, rule):
        """P99가 현재 값의 50% 이상이면 조건 미충족."""
        # current=200, metric=120(60%)
        assert rule.condition(200, 120) is False

    def test_condition_false_when_current_low(self, rule):
        """current가 100 이하이면 조건 미충족 (추가 하향 불가)."""
        assert rule.condition(100, 30) is False

    def test_adjustment_decreases_by_15_percent(self, rule):
        """조정값은 현재 값의 85%여야 한다."""
        result = rule.adjustment(200, 80)
        assert result == max(200 * 0.85, 50)

    def test_adjustment_floored_at_50(self, rule):
        """조정값은 50ms 미만으로 내려가지 않아야 한다."""
        result = rule.adjustment(55, 20)
        assert result == 50


class TestSlaCriticalUpRule:
    """SLA Critical 상향 규칙 (P99 > 85% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Critical 상향 규칙."""
        return THROTTLE_SLA_RULES[2]

    def test_condition_true_when_p99_above_85_percent(self, rule):
        """P99가 현재 값의 85%~100% 사이이면 조건 충족."""
        # current=500, metric=440(88%)
        assert rule.condition(500, 440) is True

    def test_condition_false_when_p99_below_85_percent(self, rule):
        """P99가 현재 값의 85% 미만이면 조건 미충족."""
        # current=500, metric=400(80%)
        assert rule.condition(500, 400) is False

    def test_condition_false_when_p99_above_current(self, rule):
        """P99가 현재 값 이상이면 조건 미충족."""
        assert rule.condition(500, 510) is False

    def test_adjustment_increases_by_15_percent(self, rule):
        """조정값은 현재 값의 115%여야 한다."""
        result = rule.adjustment(500, 440)
        assert result == min(500 * 1.15, 5000)

    def test_adjustment_capped_at_5000(self, rule):
        """조정값은 5000ms를 초과하지 않아야 한다."""
        result = rule.adjustment(4500, 4000)
        assert result == 5000
