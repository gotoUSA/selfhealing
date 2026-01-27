"""
X-Test Regional 시나리오 단위 테스트.

Global vs Regional 상태 우선순위 검증 및 다중 리전 격리 시나리오를 테스트합니다.

테스트 케이스:
- test_regional_override_conflict_scenario_execution: 8단계 전체 시나리오 실행
- test_regional_strict_takes_priority_over_global_normal: Regional STRICT 우선
- test_global_strict_overrides_regional: Global STRICT 오버라이드
- test_admin_override_wins_over_global_strict: Admin Override 승리
- test_state_restoration_returns_all_to_normal: 상태 원복 확인
- test_multi_region_isolation_test_scenario_execution: 5단계 격리 시나리오 실행
- test_only_target_region_is_isolated: 타겟 리전만 격리
- test_scenario_registry_contains_regional_scenarios: 레지스트리 등록 확인
"""

from unittest.mock import MagicMock, patch

import pytest

# Django 설정 구성 (테스트용)
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        REST_FRAMEWORK={},
        SECRET_KEY="test-secret-key",
    )
    django.setup()


class TestRegionalOverrideConflictScenario:
    """Regional Override Conflict 시나리오 테스트."""

    @pytest.fixture
    def scenario(self):
        """RegionalOverrideConflictScenario 인스턴스 생성."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            RegionalOverrideConflictScenario,
        )

        return RegionalOverrideConflictScenario(
            service_name="test-service",
            config={"target_region": "seoul"},
        )

    def test_scenario_name_is_correct(self, scenario):
        """시나리오 이름이 올바른지 확인."""
        assert scenario.scenario_name == "regional_override_conflict"

    def test_regional_override_conflict_scenario_execution(self, scenario):
        """8단계 전체 시나리오가 성공적으로 실행되는지 확인."""
        result = scenario.run()

        assert result is not None
        assert result.scenario == "regional_override_conflict"
        assert result.service_name == "test-service"
        assert result.status.value == "completed"
        assert len(result.steps) == 8

        # 각 단계 성공 확인
        for step in result.steps:
            assert step.success is True, f"Step {step.step} failed: {step.error}"

    def test_initial_state_is_normal(self, scenario):
        """Step 1: 초기 상태가 NORMAL인지 확인."""
        result = scenario.run()

        step1 = result.steps[0]
        assert step1.action == "check_initial_state"
        assert "NORMAL" in step1.actual
        assert step1.success is True

    def test_regional_strict_setting(self, scenario):
        """Step 2: Regional STRICT 설정 확인."""
        result = scenario.run()

        step2 = result.steps[1]
        assert step2.action == "set_regional_strict"
        assert "STRICT" in step2.actual
        assert step2.success is True

    def test_regional_strict_takes_priority_over_global_normal(self, scenario):
        """Step 3: Global NORMAL일 때 Regional STRICT가 우선하는지 확인."""
        result = scenario.run()

        step3 = result.steps[2]
        assert step3.action == "get_effective_state_regional_priority"
        assert "STRICT" in step3.actual
        assert "regional" in step3.actual
        assert step3.success is True

    def test_global_strict_setting(self, scenario):
        """Step 4: Global STRICT 설정 확인."""
        result = scenario.run()

        step4 = result.steps[3]
        assert step4.action == "set_global_strict"
        assert "STRICT" in step4.actual
        assert step4.success is True

    def test_global_strict_overrides_regional(self, scenario):
        """Step 5: Global STRICT가 Regional을 오버라이드하는지 확인."""
        result = scenario.run()

        step5 = result.steps[4]
        assert step5.action == "get_effective_state_global_override"
        assert "STRICT" in step5.actual
        assert "global" in step5.actual
        assert step5.success is True

    def test_admin_override_setting(self, scenario):
        """Step 6: Admin Override 설정 확인."""
        result = scenario.run()

        step6 = result.steps[5]
        assert step6.action == "set_admin_override"
        assert "ADMIN_OVERRIDE" in step6.actual
        assert step6.success is True

    def test_admin_override_wins_over_global_strict(self, scenario):
        """Step 7: Admin Override가 Global STRICT를 이기는지 확인."""
        result = scenario.run()

        step7 = result.steps[6]
        assert step7.action == "get_effective_state_admin_wins"
        assert "NORMAL" in step7.actual
        assert "regional" in step7.actual
        assert step7.success is True

    def test_state_restoration_returns_all_to_normal(self, scenario):
        """Step 8: 상태 원복 후 모두 NORMAL인지 확인."""
        result = scenario.run()

        step8 = result.steps[7]
        assert step8.action == "restore_all_states"
        assert "NORMAL" in step8.actual
        assert step8.success is True

    def test_state_transitions_recorded(self, scenario):
        """상태 전환 이력이 기록되는지 확인."""
        result = scenario.run()

        assert result.config is not None
        assert "state_transitions" in result.config
        transitions = result.config["state_transitions"]
        assert len(transitions) >= 4  # init, set_regional, set_global, admin_override, restore_all

    def test_timeline_has_all_events(self, scenario):
        """타임라인에 모든 이벤트가 기록되는지 확인."""
        result = scenario.run()

        assert len(result.timeline) == 8
        for i, event in enumerate(result.timeline):
            assert event.step == i + 1


class TestMultiRegionIsolationTestScenario:
    """Multi-Region Isolation Test 시나리오 테스트."""

    @pytest.fixture
    def scenario(self):
        """MultiRegionIsolationTestScenario 인스턴스 생성."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            MultiRegionIsolationTestScenario,
        )

        return MultiRegionIsolationTestScenario(
            service_name="test-service",
            config={"target_region": "seoul", "other_region": "tokyo"},
        )

    def test_scenario_name_is_correct(self, scenario):
        """시나리오 이름이 올바른지 확인."""
        assert scenario.scenario_name == "multi_region_isolation_test"

    def test_multi_region_isolation_test_scenario_execution(self, scenario):
        """5단계 전체 시나리오가 성공적으로 실행되는지 확인."""
        result = scenario.run()

        assert result is not None
        assert result.scenario == "multi_region_isolation_test"
        assert result.service_name == "test-service"
        assert result.status.value == "completed"
        assert len(result.steps) == 5

        # 각 단계 성공 확인
        for step in result.steps:
            assert step.success is True, f"Step {step.step} failed: {step.error}"

    def test_current_region_check(self, scenario):
        """Step 1: 현재 리전 확인."""
        result = scenario.run()

        step1 = result.steps[0]
        assert step1.action == "check_current_region"
        assert "seoul" in step1.actual
        assert step1.success is True

    def test_target_region_isolation(self, scenario):
        """Step 2: 타겟 리전 격리 설정 확인."""
        result = scenario.run()

        step2 = result.steps[1]
        assert step2.action == "set_region_strict"
        assert "seoul" in step2.actual
        assert "STRICT" in step2.actual
        assert step2.success is True

    def test_other_region_remains_normal(self, scenario):
        """Step 3: 다른 리전이 NORMAL 상태인지 확인."""
        result = scenario.run()

        step3 = result.steps[2]
        assert step3.action == "check_other_region_normal"
        assert "tokyo" in step3.actual
        assert "NORMAL" in step3.actual
        assert step3.success is True

    def test_only_target_region_is_isolated(self, scenario):
        """Step 4: 타겟 리전만 격리되었는지 확인."""
        result = scenario.run()

        step4 = result.steps[3]
        assert step4.action == "verify_isolation_state"
        assert "seoul" in step4.actual
        assert "seoul_isolated: True" in step4.actual
        assert "tokyo_isolated: False" in step4.actual
        assert step4.success is True

    def test_region_restore(self, scenario):
        """Step 5: 격리 해제 확인."""
        result = scenario.run()

        step5 = result.steps[4]
        assert step5.action == "restore_region"
        assert "NORMAL" in step5.actual
        assert step5.success is True


class TestScenarioRegistry:
    """시나리오 레지스트리 테스트."""

    def test_scenario_registry_contains_regional_scenarios(self):
        """레지스트리에 새 리전 시나리오가 등록되어 있는지 확인."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            SCENARIO_REGISTRY,
            list_available_scenarios,
        )

        assert "regional_override_conflict" in SCENARIO_REGISTRY
        assert "multi_region_isolation_test" in SCENARIO_REGISTRY

        available = list_available_scenarios()
        assert "regional_override_conflict" in available
        assert "multi_region_isolation_test" in available

    def test_get_scenario_class_returns_correct_class(self):
        """get_scenario_class가 올바른 클래스를 반환하는지 확인."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            get_scenario_class,
            RegionalOverrideConflictScenario,
            MultiRegionIsolationTestScenario,
        )

        assert get_scenario_class("regional_override_conflict") == RegionalOverrideConflictScenario
        assert get_scenario_class("multi_region_isolation_test") == MultiRegionIsolationTestScenario

    def test_regional_scenarios_extend_integration_scenario(self):
        """새 시나리오들이 IntegrationScenario를 상속하는지 확인."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            IntegrationScenario,
            RegionalOverrideConflictScenario,
            MultiRegionIsolationTestScenario,
        )

        assert issubclass(RegionalOverrideConflictScenario, IntegrationScenario)
        assert issubclass(MultiRegionIsolationTestScenario, IntegrationScenario)


class TestStateTransitionMatrix:
    """상태 전환 매트릭스 테스트 (문서 6.1 검증)."""

    def test_global_normal_regional_normal_returns_normal(self):
        """Global NORMAL + Regional NORMAL → NORMAL."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            RegionalOverrideConflictScenario,
        )

        scenario = RegionalOverrideConflictScenario(
            service_name="test-service",
            config={"target_region": "seoul"},
        )
        result = scenario.run()

        # Step 1에서 초기 상태 확인
        step1 = result.steps[0]
        assert "NORMAL" in step1.actual

    def test_global_normal_regional_strict_returns_strict(self):
        """Global NORMAL + Regional STRICT → STRICT."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            RegionalOverrideConflictScenario,
        )

        scenario = RegionalOverrideConflictScenario(
            service_name="test-service",
            config={"target_region": "seoul"},
        )
        result = scenario.run()

        # Step 3에서 Regional STRICT 상태 확인
        step3 = result.steps[2]
        assert "STRICT" in step3.actual
        assert "regional" in step3.actual

    def test_global_strict_regional_normal_returns_strict(self):
        """Global STRICT + Regional NORMAL → STRICT (Global 오버라이드)."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            RegionalOverrideConflictScenario,
        )

        scenario = RegionalOverrideConflictScenario(
            service_name="test-service",
            config={"target_region": "seoul"},
        )
        result = scenario.run()

        # Step 5에서 Global STRICT 오버라이드 확인
        step5 = result.steps[4]
        assert "STRICT" in step5.actual
        assert "global" in step5.actual

    def test_admin_override_returns_regional_state(self):
        """Admin Override 시 Regional 상태 반환."""
        from selfhealing.api.django.views.xtest.integration_scenarios import (
            RegionalOverrideConflictScenario,
        )

        scenario = RegionalOverrideConflictScenario(
            service_name="test-service",
            config={"target_region": "seoul"},
        )
        result = scenario.run()

        # Step 7에서 Admin Override로 NORMAL 확인
        step7 = result.steps[6]
        assert "NORMAL" in step7.actual
        assert "regional" in step7.actual
