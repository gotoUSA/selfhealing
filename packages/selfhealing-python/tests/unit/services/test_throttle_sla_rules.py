"""
THROTTLE_SLA_RULES 단위 테스트.

DecisionEngine에 주입되는 SLA 자동 조정 규칙의 조건/조정 로직을 검증한다.

- 소스 상수 참조: AdjustmentPriority enum 직접 import
- 규칙 필터링: 인덱스 접근 대신 parameter 기반 필터링으로 순서 의존 제거
- 함수 동작 검증: condition/adjustment 입출력 테스트 (guideline 2.1 허용)
"""

from __future__ import annotations

import pytest

from selfhealing.core.decision_engine import AdjustmentPriority, AdjustmentRule
from selfhealing.services.auto_tuning.throttle_sla_rules import THROTTLE_SLA_RULES


# =============================================================================
# 규칙 필터링 헬퍼 (인덱스 접근 대신 parameter 기반 조회)
# =============================================================================


def _find_rules_by_param(parameter: str) -> list[AdjustmentRule]:
    """THROTTLE_SLA_RULES에서 특정 파라미터의 규칙 목록 반환."""
    return [r for r in THROTTLE_SLA_RULES if r.parameter == parameter]


def _find_up_rule(parameter: str) -> AdjustmentRule:
    """특정 파라미터의 상향 규칙 반환 (adjustment가 current보다 큰 규칙)."""
    rules = _find_rules_by_param(parameter)
    # 상향 규칙: adjustment(100, 95) > 100
    up_rules = [r for r in rules if r.adjustment(100, 95) > 100]
    assert len(up_rules) == 1, f"Expected 1 up rule for {parameter}, got {len(up_rules)}"
    return up_rules[0]


def _find_down_rule(parameter: str) -> AdjustmentRule:
    """특정 파라미터의 하향 규칙 반환 (adjustment가 current보다 작은 규칙)."""
    rules = _find_rules_by_param(parameter)
    # 하향 규칙: adjustment(200, 50) < 200
    down_rules = [r for r in rules if r.adjustment(200, 50) < 200]
    assert len(down_rules) == 1, f"Expected 1 down rule for {parameter}, got {len(down_rules)}"
    return down_rules[0]


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
        warning_rules = _find_rules_by_param("throttle_sla_warning_ms")
        for rule in warning_rules:
            assert rule.priority == AdjustmentPriority.LOW

    def test_sla_critical_rule_priority_medium(self):
        """SLA Critical 규칙은 MEDIUM 우선순위여야 한다."""
        critical_rules = _find_rules_by_param("throttle_sla_critical_ms")
        for rule in critical_rules:
            assert rule.priority == AdjustmentPriority.MEDIUM


class TestSlaWarningUpRule:
    """SLA Warning 상향 규칙 (P99 > 90% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Warning 상향 규칙 (parameter 필터링으로 조회)."""
        return _find_up_rule("throttle_sla_warning_ms")

    def test_condition_true_when_p99_above_90_percent(self, rule):
        """P99가 현재 값의 90%~100% 사이이면 조건 충족."""
        # current=200, metric=185(92.5%) → 90%초과 & current미만
        assert rule.condition(200, 185) is True

    def test_condition_false_when_p99_below_90_percent(self, rule):
        """P99가 현재 값의 90% 미만이면 조건 미충족."""
        # current=200, metric=170(85%)
        assert rule.condition(200, 170) is False

    def test_condition_false_when_p99_above_current(self, rule):
        """P99가 현재 값 이상이면 조건 미충족 (이미 SLA 위반)."""
        assert rule.condition(200, 210) is False

    def test_adjustment_increases_value(self, rule):
        """조정값은 현재 값보다 커야 한다 (상향 조정)."""
        result = rule.adjustment(200, 185)
        assert result > 200

    def test_adjustment_capped_at_max(self, rule):
        """조정값은 상한값을 초과하지 않아야 한다."""
        result = rule.adjustment(1800, 1650)
        # 1800 * 1.15 = 2070 → cap 적용
        assert result <= 2000


class TestSlaWarningDownRule:
    """SLA Warning 하향 규칙 (P99 < 50% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Warning 하향 규칙 (parameter 필터링으로 조회)."""
        return _find_down_rule("throttle_sla_warning_ms")

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

    def test_adjustment_decreases_value(self, rule):
        """조정값은 현재 값보다 작아야 한다 (하향 조정)."""
        result = rule.adjustment(200, 80)
        assert result < 200

    def test_adjustment_floored_at_min(self, rule):
        """조정값은 하한값 미만으로 내려가지 않아야 한다."""
        result = rule.adjustment(55, 20)
        # 55 * 0.85 = 46.75 → floor 적용
        assert result >= 50


class TestSlaCriticalUpRule:
    """SLA Critical 상향 규칙 (P99 > 85% of threshold) 테스트."""

    @pytest.fixture
    def rule(self):
        """SLA Critical 상향 규칙 (parameter 필터링으로 조회)."""
        return _find_up_rule("throttle_sla_critical_ms")

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

    def test_adjustment_increases_value(self, rule):
        """조정값은 현재 값보다 커야 한다 (상향 조정)."""
        result = rule.adjustment(500, 440)
        assert result > 500

    def test_adjustment_capped_at_max(self, rule):
        """조정값은 상한값을 초과하지 않아야 한다."""
        result = rule.adjustment(4500, 4000)
        # 4500 * 1.15 = 5175 → cap 적용
        assert result <= 5000
