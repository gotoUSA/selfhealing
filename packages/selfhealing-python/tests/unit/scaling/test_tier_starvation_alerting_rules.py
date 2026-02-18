"""
TierStarvation 알림 규칙 단위 테스트.

테스트 항목:
- 계약: TierStarvationNonEssential 규칙 존재
- 계약: PromQL 표현식에 non_essential tier 필터 포함
- 계약: 최소 볼륨 조건 > 10 포함
- 계약: 거부율 임계치 > 0.99 포함
- 계약: for 기간 10m
- 계약: severity warning
- 계약: team ops
- 계약: YAML 파일에 TierStarvation 알람 존재
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from selfhealing.services.metrics.alerting_rules import ALERTING_RULES


class TestTierStarvationAlertContract:
    """TierStarvationNonEssential 알림 규칙 계약 검증."""

    def test_rule_exists(self):
        """TierStarvationNonEssential 규칙이 ALERTING_RULES에 정의되어 있다."""
        assert "TierStarvationNonEssential" in ALERTING_RULES

    def test_expr_contains_non_essential_tier_filter(self):
        """PromQL 표현식에 tier='non_essential' 필터가 포함된다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert 'tier="non_essential"' in rule["expr"]

    def test_expr_contains_dropped_metric(self):
        """PromQL 표현식에 dropped_total 메트릭이 포함된다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert "selfhealing_rate_controller_dropped_total" in rule["expr"]

    def test_expr_contains_processed_metric(self):
        """PromQL 표현식에 processed_total 메트릭이 포함된다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert "selfhealing_rate_controller_processed_total" in rule["expr"]

    def test_expr_contains_rejection_ratio_threshold(self):
        """PromQL 표현식에 거부율 임계치 0.99가 포함된다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert "> 0.99" in rule["expr"]

    def test_expr_contains_minimum_volume_threshold(self):
        """PromQL 표현식에 최소 볼륨 조건 > 10이 포함된다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert "> 10" in rule["expr"]

    def test_for_duration_10m(self):
        """감지 지속 기간이 10분이다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert rule["for"] == "10m"

    def test_severity_warning(self):
        """severity가 warning이다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert rule["severity"] == "warning"

    def test_team_ops(self):
        """담당 팀이 ops이다."""
        rule = ALERTING_RULES["TierStarvationNonEssential"]
        assert rule["team"] == "ops"


class TestTierStarvationYamlAlertContract:
    """throttle_alerts.yml의 TierStarvation 알람 계약 검증."""

    @pytest.fixture(scope="class")
    def yaml_rules(self):
        """throttle_alerts.yml 파일의 alert rule 목록."""
        yaml_path = pathlib.Path(__file__).resolve().parents[5] / "docker" / "prometheus" / "rules" / "throttle_alerts.yml"
        if not yaml_path.exists():
            pytest.skip(f"throttle_alerts.yml not found at {yaml_path}")

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        rules = []
        for group in data.get("groups", []):
            rules.extend(group.get("rules", []))
        return rules

    def _find_alert(self, rules: list, alert_name: str) -> dict | None:
        """이름으로 alert rule 검색."""
        for rule in rules:
            if rule.get("alert") == alert_name:
                return rule
        return None

    def test_tier_starvation_alert_exists_in_yaml(self, yaml_rules):
        """TierStarvation alert가 throttle_alerts.yml에 정의되어 있다."""
        alert = self._find_alert(yaml_rules, "TierStarvation")
        assert alert is not None, "TierStarvation alert not found in YAML"

    def test_tier_starvation_yaml_for_10m(self, yaml_rules):
        """YAML TierStarvation alert의 for 기간이 10m이다."""
        alert = self._find_alert(yaml_rules, "TierStarvation")
        assert alert["for"] == "10m"

    def test_tier_starvation_yaml_severity_warning(self, yaml_rules):
        """YAML TierStarvation alert의 severity가 warning이다."""
        alert = self._find_alert(yaml_rules, "TierStarvation")
        assert alert["labels"]["severity"] == "warning"

    def test_tier_starvation_yaml_expr_contains_non_essential(self, yaml_rules):
        """YAML TierStarvation alert의 expr에 non_essential 필터가 포함된다."""
        alert = self._find_alert(yaml_rules, "TierStarvation")
        assert 'tier="non_essential"' in alert["expr"]

    def test_tier_starvation_yaml_expr_minimum_volume(self, yaml_rules):
        """YAML TierStarvation alert의 expr에 최소 볼륨 조건이 포함된다."""
        alert = self._find_alert(yaml_rules, "TierStarvation")
        assert "> 10" in alert["expr"]
