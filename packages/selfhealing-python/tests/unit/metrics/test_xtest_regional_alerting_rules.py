"""
X-Test Regional Boundary 알림 규칙 단위 테스트.

Section 7.2 구현 검증:
- XTestCrossRegionDeniedRateHigh: cross_region 거부 > 10/min
- XTestCrossRegionDeniedFromSameSource: 동일 소스에서 반복 거부

테스트 케이스:
- test_xtest_cross_region_denied_rate_high_rule_exists: 규칙 존재 확인
- test_xtest_cross_region_denied_rate_high_expr: PromQL 표현식 확인
- test_xtest_cross_region_denied_from_same_source_rule_exists: 규칙 존재 확인
- test_xtest_cross_region_denied_from_same_source_expr: PromQL 표현식 확인
"""

import pytest


class TestXTestRegionalAlertingRules:
    """X-Test Regional 알림 규칙 테스트."""

    def test_xtest_cross_region_denied_rate_high_rule_exists(self):
        """XTestCrossRegionDeniedRateHigh 규칙이 정의됨."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        assert "XTestCrossRegionDeniedRateHigh" in ALERTING_RULES

    def test_xtest_cross_region_denied_rate_high_expr(self):
        """XTestCrossRegionDeniedRateHigh가 올바른 PromQL 표현식을 가짐."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]

        # rate > 10/min 조건 확인
        assert "rate" in rule["expr"]
        assert "selfhealing_xtest_cross_region_denied_total" in rule["expr"]
        assert "> 10" in rule["expr"]

    def test_xtest_cross_region_denied_rate_high_severity(self):
        """XTestCrossRegionDeniedRateHigh가 warning severity."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]

        assert rule["severity"] == "warning"

    def test_xtest_cross_region_denied_rate_high_team(self):
        """XTestCrossRegionDeniedRateHigh가 security 팀 담당."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]

        assert rule["team"] == "security"

    def test_xtest_cross_region_denied_from_same_source_rule_exists(self):
        """XTestCrossRegionDeniedFromSameSource 규칙이 정의됨."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        assert "XTestCrossRegionDeniedFromSameSource" in ALERTING_RULES

    def test_xtest_cross_region_denied_from_same_source_expr(self):
        """XTestCrossRegionDeniedFromSameSource가 올바른 PromQL 표현식을 가짐."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        # sum by + increase 조건 확인
        assert "sum by" in rule["expr"]
        assert "increase" in rule["expr"]
        assert "selfhealing_xtest_cross_region_denied_total" in rule["expr"]
        assert "> 5" in rule["expr"]

    def test_xtest_cross_region_denied_from_same_source_severity(self):
        """XTestCrossRegionDeniedFromSameSource가 warning severity."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        assert rule["severity"] == "warning"

    def test_xtest_cross_region_denied_from_same_source_team(self):
        """XTestCrossRegionDeniedFromSameSource가 security 팀 담당."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        assert rule["team"] == "security"

    def test_both_rules_have_runbook_url(self):
        """두 규칙 모두 runbook URL을 가짐."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rate_rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]
        source_rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        assert "runbook_url" in rate_rule
        assert rate_rule["runbook_url"].startswith("https://")

        assert "runbook_url" in source_rule
        assert source_rule["runbook_url"].startswith("https://")

    def test_both_rules_have_description(self):
        """두 규칙 모두 description을 가짐."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rate_rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]
        source_rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        assert "description" in rate_rule
        assert len(rate_rule["description"]) > 0

        assert "description" in source_rule
        assert len(source_rule["description"]) > 0

    def test_both_rules_have_summary(self):
        """두 규칙 모두 summary를 가짐."""
        from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

        rate_rule = ALERTING_RULES["XTestCrossRegionDeniedRateHigh"]
        source_rule = ALERTING_RULES["XTestCrossRegionDeniedFromSameSource"]

        assert "summary" in rate_rule
        assert len(rate_rule["summary"]) > 0

        assert "summary" in source_rule
        assert len(source_rule["summary"]) > 0
