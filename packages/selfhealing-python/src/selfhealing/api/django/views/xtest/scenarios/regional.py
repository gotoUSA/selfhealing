"""
Regional 관련 통합 테스트 시나리오.

Regional Override Conflict, Multi-Region Isolation Test 시나리오 제공.
144 문서 구현 시나리오 포함.
"""

from typing import Any

from django.utils import timezone

from .base import (
    IntegrationScenario,
    ScenarioResult,
)


class RegionalOverrideConflictScenario(IntegrationScenario):
    """
    Global vs Regional 상태 우선순위 검증 시나리오.

    Global STRICT가 Regional을 오버라이드하고,
    Admin Override 시 Regional이 우선하는지 검증합니다.

    Steps:
    1. 초기 상태 확인 (Global: NORMAL, Regional: NORMAL)
    2. Regional STRICT 설정
    3. get_effective_state() 호출 (STRICT - Regional 우선)
    4. Global STRICT 설정
    5. get_effective_state() 호출 (STRICT - Global 오버라이드)
    6. Regional ADMIN_OVERRIDE 설정
    7. get_effective_state() 호출 (NORMAL - Admin 승리)
    8. 상태 원복 (모든 상태 NORMAL)

    Config options:
    - target_region: str - 타겟 리전 (기본: 현재 리전)
    """

    scenario_name = "regional_override_conflict"
    max_timeout_seconds = 60

    def execute(self) -> ScenarioResult:
        from selfhealing.services.coordination.enums import (
            EmergencyScope,
        )
        from selfhealing.services.coordination.models import ScopedEmergencyState
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        target_region = self.config.get("target_region", "seoul")

        # 상태 추적용 변수
        global_state = EmergencyLevel.NORMAL
        regional_state = EmergencyLevel.NORMAL
        admin_override_active = False
        state_transitions: list[dict[str, Any]] = []

        def record_transition(action: str, previous: str, new: str, region: str):
            """상태 전환 기록."""
            state_transitions.append(
                {
                    "action": action,
                    "previous_state": previous,
                    "new_state": new,
                    "region": region,
                    "timestamp": timezone.now().isoformat(),
                }
            )

        class MockNamespacedEmergencyTracker:
            """네임스페이스별 Emergency 상태를 시뮬레이션하는 Mock 트래커."""

            def __init__(tracker_self):
                tracker_self._global_level = EmergencyLevel.NORMAL
                tracker_self._regional_levels: dict[str, EmergencyLevel] = {}
                tracker_self._admin_overrides: dict[str, bool] = {}

            def set_global_state(tracker_self, level: EmergencyLevel) -> None:
                """Global 상태 설정."""
                tracker_self._global_level = level

            def set_regional_state(
                tracker_self, namespace: str, level: EmergencyLevel
            ) -> None:
                """Regional 상태 설정."""
                tracker_self._regional_levels[namespace] = level

            def set_admin_override(tracker_self, namespace: str, active: bool) -> None:
                """Admin Override 설정."""
                tracker_self._admin_overrides[namespace] = active

            def get_effective_state(
                tracker_self, namespace: str = None
            ) -> ScopedEmergencyState:
                """
                유효 상태 반환 (우선순위 로직 적용).

                우선순위:
                1. Admin Override 활성화 시 → Regional 우선
                2. Global STRICT → 모든 리전 STRICT (오버라이드)
                3. Regional 상태 → 로컬 상태
                """
                ns = namespace or target_region

                # Admin Override 체크
                if tracker_self._admin_overrides.get(ns, False):
                    regional_level = tracker_self._regional_levels.get(
                        ns, EmergencyLevel.NORMAL
                    )
                    governance_mode = (
                        "STRICT"
                        if regional_level
                        in (
                            EmergencyLevel.LEVEL_1,
                            EmergencyLevel.LEVEL_2,
                            EmergencyLevel.LEVEL_3,
                        )
                        else "NORMAL"
                    )
                    return ScopedEmergencyState(
                        namespace=ns,
                        emergency_level=regional_level,
                        governance_mode=governance_mode,
                        scope=EmergencyScope.REGIONAL,
                    )

                # Global STRICT 오버라이드 체크
                global_is_strict = tracker_self._global_level in (
                    EmergencyLevel.LEVEL_1,
                    EmergencyLevel.LEVEL_2,
                    EmergencyLevel.LEVEL_3,
                )

                if global_is_strict:
                    return ScopedEmergencyState(
                        namespace=ns,
                        emergency_level=tracker_self._global_level,
                        governance_mode="STRICT",
                        scope=EmergencyScope.GLOBAL,
                    )

                # Regional 상태 반환
                regional_level = tracker_self._regional_levels.get(
                    ns, EmergencyLevel.NORMAL
                )
                regional_is_strict = regional_level in (
                    EmergencyLevel.LEVEL_1,
                    EmergencyLevel.LEVEL_2,
                    EmergencyLevel.LEVEL_3,
                )
                governance_mode = "STRICT" if regional_is_strict else "NORMAL"

                return ScopedEmergencyState(
                    namespace=ns,
                    emergency_level=regional_level,
                    governance_mode=governance_mode,
                    scope=EmergencyScope.REGIONAL,
                )

        mock_tracker = MockNamespacedEmergencyTracker()

        # =====================================================================
        # Step 1: 초기 상태 확인 (Global: NORMAL, Regional: NORMAL)
        # =====================================================================
        def step1():
            state = mock_tracker.get_effective_state(target_region)
            record_transition("init", "N/A", "NORMAL", target_region)
            return (
                f"Global: NORMAL, Regional: NORMAL, governance: {state.governance_mode}"
            )

        if not self._execute_step(
            1,
            "check_initial_state",
            "emergency_tracker",
            "Global: NORMAL, Regional: NORMAL",
            step1,
        ):
            return self.result

        # =====================================================================
        # Step 2: Regional STRICT 설정
        # =====================================================================
        def step2():
            nonlocal regional_state
            mock_tracker.set_regional_state(target_region, EmergencyLevel.LEVEL_2)
            regional_state = EmergencyLevel.LEVEL_2
            record_transition("set_regional_strict", "NORMAL", "STRICT", target_region)
            return "Regional: STRICT (LEVEL_2)"

        if not self._execute_step(
            2, "set_regional_strict", "emergency_tracker", "Regional: STRICT", step2
        ):
            return self.result

        # =====================================================================
        # Step 3: get_effective_state() 호출 (STRICT - Regional 우선)
        # =====================================================================
        def step3():
            state = mock_tracker.get_effective_state(target_region)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            3,
            "get_effective_state_regional_priority",
            "emergency_tracker",
            "governance: STRICT, scope: regional",
            step3,
        ):
            return self.result

        # =====================================================================
        # Step 4: Global STRICT 설정
        # =====================================================================
        def step4():
            nonlocal global_state
            mock_tracker.set_global_state(EmergencyLevel.LEVEL_3)
            global_state = EmergencyLevel.LEVEL_3
            record_transition("set_global_strict", "NORMAL", "STRICT", "global")
            return "Global: STRICT (LEVEL_3)"

        if not self._execute_step(
            4, "set_global_strict", "emergency_tracker", "Global: STRICT", step4
        ):
            return self.result

        # =====================================================================
        # Step 5: get_effective_state() 호출 (STRICT - Global 오버라이드)
        # =====================================================================
        def step5():
            state = mock_tracker.get_effective_state(target_region)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            5,
            "get_effective_state_global_override",
            "emergency_tracker",
            "governance: STRICT, scope: global",
            step5,
        ):
            return self.result

        # =====================================================================
        # Step 6: Regional ADMIN_OVERRIDE 설정
        # =====================================================================
        def step6():
            nonlocal admin_override_active
            # Regional을 NORMAL로 변경하고 Admin Override 활성화
            mock_tracker.set_regional_state(target_region, EmergencyLevel.NORMAL)
            mock_tracker.set_admin_override(target_region, True)
            admin_override_active = True
            record_transition(
                "set_admin_override", "STRICT", "ADMIN_OVERRIDE", target_region
            )
            return "Regional: ADMIN_OVERRIDE (NORMAL)"

        if not self._execute_step(
            6,
            "set_admin_override",
            "emergency_tracker",
            "Regional: ADMIN_OVERRIDE",
            step6,
        ):
            return self.result

        # =====================================================================
        # Step 7: get_effective_state() 호출 (NORMAL - Admin 승리)
        # =====================================================================
        def step7():
            state = mock_tracker.get_effective_state(target_region)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            7,
            "get_effective_state_admin_wins",
            "emergency_tracker",
            "governance: NORMAL, scope: regional",
            step7,
        ):
            return self.result

        # =====================================================================
        # Step 8: 상태 원복 (모든 상태 NORMAL)
        # =====================================================================
        def step8():
            mock_tracker.set_global_state(EmergencyLevel.NORMAL)
            mock_tracker.set_regional_state(target_region, EmergencyLevel.NORMAL)
            mock_tracker.set_admin_override(target_region, False)
            state = mock_tracker.get_effective_state(target_region)
            record_transition("restore_all", "ADMIN_OVERRIDE", "NORMAL", "all")
            return f"All states restored: governance={state.governance_mode}"

        self._execute_step(
            8,
            "restore_all_states",
            "emergency_tracker",
            "All states restored: governance=NORMAL",
            step8,
        )

        # 결과에 상태 전환 이력 추가
        if self.result and self.result.config is not None:
            self.result.config["state_transitions"] = state_transitions
        elif self.result:
            self.result.config = {"state_transitions": state_transitions}

        return self.result


class MultiRegionIsolationTestScenario(IntegrationScenario):
    """
    다중 리전 격리 검증 시나리오.

    특정 리전만 격리하고 다른 리전은 정상인지 확인합니다.
    RegionalIsolationGate를 사용하여 리전 격리를 시뮬레이션합니다.

    Steps:
    1. 현재 리전 확인
    2. 특정 리전 STRICT 설정 (격리)
    3. 다른 리전 상태 확인 (NORMAL)
    4. RegionalIsolationGate 체크 (격리 리전만 격리됨)
    5. 격리 해제

    Config options:
    - target_region: str - 격리할 리전 (기본: seoul)
    - other_region: str - 비교용 다른 리전 (기본: tokyo)
    """

    scenario_name = "multi_region_isolation_test"
    max_timeout_seconds = 60

    def execute(self) -> ScenarioResult:
        target_region = self.config.get("target_region", "seoul")
        other_region = self.config.get("other_region", "tokyo")

        # Mock RegionalIsolationGate
        class MockRegionalIsolationGate:
            """RegionalIsolationGate Mock."""

            def __init__(gate_self):
                gate_self._isolated_regions: dict[str, str] = {}

            def isolate_region(
                gate_self, region: str, reason: str, duration_seconds: int = 300
            ) -> bool:
                """리전 격리."""
                gate_self._isolated_regions[region] = reason
                return True

            def is_region_isolated(gate_self, region: str):
                """격리 상태 확인."""
                if region in gate_self._isolated_regions:
                    return True, gate_self._isolated_regions[region]
                return False, None

            def restore_region(gate_self, region: str) -> bool:
                """격리 해제."""
                if region in gate_self._isolated_regions:
                    del gate_self._isolated_regions[region]
                    return True
                return False

            def get_isolated_regions(gate_self) -> list[str]:
                """격리된 리전 목록."""
                return list(gate_self._isolated_regions.keys())

        mock_gate = MockRegionalIsolationGate()

        # =====================================================================
        # Step 1: 현재 리전 확인
        # =====================================================================
        def step1():
            return f"region: {target_region}"

        if not self._execute_step(
            1,
            "check_current_region",
            "cluster_identity",
            f"region: {target_region}",
            step1,
        ):
            return self.result

        # =====================================================================
        # Step 2: 특정 리전 STRICT 설정 (격리)
        # =====================================================================
        def step2():
            success = mock_gate.isolate_region(
                target_region, reason="X-Test 시뮬레이션 격리", duration_seconds=300
            )
            return f"{target_region}: STRICT (isolated={success})"

        if not self._execute_step(
            2,
            "set_region_strict",
            "regional_isolation_gate",
            f"{target_region}: STRICT",
            step2,
        ):
            return self.result

        # =====================================================================
        # Step 3: 다른 리전 상태 확인 (NORMAL)
        # =====================================================================
        def step3():
            is_isolated, reason = mock_gate.is_region_isolated(other_region)
            if is_isolated:
                return f"{other_region}: STRICT (unexpected)"
            return f"{other_region}: NORMAL"

        if not self._execute_step(
            3,
            "check_other_region_normal",
            "regional_isolation_gate",
            f"{other_region}: NORMAL",
            step3,
        ):
            return self.result

        # =====================================================================
        # Step 4: RegionalIsolationGate 체크 (격리 리전만 격리됨)
        # =====================================================================
        def step4():
            target_isolated, target_reason = mock_gate.is_region_isolated(target_region)
            other_isolated, _ = mock_gate.is_region_isolated(other_region)

            isolated_list = mock_gate.get_isolated_regions()

            return f"isolated_regions: {isolated_list}, {target_region}_isolated: {target_isolated}, {other_region}_isolated: {other_isolated}"

        expected_step4 = f"isolated_regions: ['{target_region}'], {target_region}_isolated: True, {other_region}_isolated: False"
        if not self._execute_step(
            4,
            "verify_isolation_state",
            "regional_isolation_gate",
            expected_step4,
            step4,
        ):
            return self.result

        # =====================================================================
        # Step 5: 격리 해제
        # =====================================================================
        def step5():
            success = mock_gate.restore_region(target_region)
            is_isolated, _ = mock_gate.is_region_isolated(target_region)
            return (
                f"{target_region}: NORMAL (restored={success}, isolated={is_isolated})"
            )

        self._execute_step(
            5,
            "restore_region",
            "regional_isolation_gate",
            f"{target_region}: NORMAL",
            step5,
        )

        return self.result


__all__ = [
    "RegionalOverrideConflictScenario",
    "MultiRegionIsolationTestScenario",
]
