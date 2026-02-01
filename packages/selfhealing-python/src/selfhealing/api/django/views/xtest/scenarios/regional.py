"""
Regional 관련 통합 테스트 시나리오.

Global vs Regional 상태 우선순위 검증 및 다중 리전 격리 시나리오 제공.

주요 기능:
- RegionalOverrideConflictScenario: Global/Regional 우선순위 및 Admin Override 검증
- MultiRegionIsolationTestScenario: 특정 리전만 격리되는지 검증

실제 AtomicStateQuery와 NamespacedEmergencyTracker를 사용하여
Lua 스크립트 기반 원자적 상태 조회 로직을 검증합니다.
"""

import json
from typing import Any

from django.utils import timezone

from .base import (
    IntegrationScenario,
    ScenarioResult,
)


class MockStateBackend:
    """
    테스트용 Mock StateBackend.

    Redis 없이 메모리에서 상태를 관리합니다.
    AtomicStateQuery와 함께 사용하여 우선순위 로직을 검증할 수 있습니다.
    """

    def __init__(self, redis_client: Any = None):
        """
        Mock StateBackend 초기화.

        Args:
            redis_client: Mock Redis 클라이언트 (상태 공유용)
        """
        self._redis_client = redis_client
        self._storage: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        """키 조회."""
        if self._redis_client:
            data = self._redis_client.get(key)
            if data:
                return json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
            return default
        return self._storage.get(key, default)

    def set(self, key: str, value: Any, ttl_seconds: int = None) -> None:
        """키 저장."""
        if self._redis_client:
            self._redis_client.set(key, json.dumps(value, default=str))
        else:
            self._storage[key] = value

    def delete(self, key: str) -> bool:
        """키 삭제."""
        if self._redis_client:
            return self._redis_client.delete(key) > 0
        if key in self._storage:
            del self._storage[key]
            return True
        return False

    def exists(self, key: str) -> bool:
        """키 존재 확인."""
        if self._redis_client:
            return self._redis_client.get(key) is not None
        return key in self._storage

    def get_all(self, pattern: str = "*") -> dict[str, Any]:
        """패턴 매칭 키 조회."""
        if self._redis_client:
            import fnmatch

            result = {}
            for key in list(self._storage.keys()):
                if fnmatch.fnmatch(key, pattern):
                    result[key] = self._storage[key]
            return result
        return self._storage.copy()


class RegionalOverrideConflictScenario(IntegrationScenario):
    """
    Global vs Regional 상태 우선순위 검증 시나리오.

    NamespacedEmergencyTracker와 AtomicStateQuery를 사용하여
    실제 우선순위 로직을 검증합니다.

    우선순위 규칙:
    1. Admin Override 활성화 시 → Regional 우선
    2. Global STRICT → 모든 리전 STRICT (오버라이드)
    3. Regional 상태 → 로컬 상태

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
    - target_region: str - 타겟 리전 (기본: seoul)
    - redis_client: Redis 클라이언트 (테스트용 주입)
    """

    scenario_name = "regional_override_conflict"
    max_timeout_seconds = 60

    # =========================================================================
    # Step 2: 상태 설정 헬퍼 메서드 (144 문서 2-1, 2-2, 2-3)
    # =========================================================================

    def _set_global_state(
        self,
        tracker: Any,
        level: Any,
        activated_by: str = "xtest-scenario",
    ) -> dict[str, Any]:
        """
        Global Emergency 상태 설정.

        Args:
            tracker: NamespacedEmergencyTracker 인스턴스
            level: EmergencyLevel (NORMAL, LEVEL_1, LEVEL_2, LEVEL_3)
            activated_by: 활성화 주체

        Returns:
            상태 전환 정보 딕셔너리
        """
        from selfhealing.services.coordination.enums import EmergencyScope
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        previous_state = tracker.get_state(namespace="global")
        previous_mode = previous_state.governance_mode if previous_state else "NORMAL"

        if level == EmergencyLevel.NORMAL:
            new_state = tracker.deactivate_emergency(
                deactivated_by=activated_by,
                namespace="global",
                scope=EmergencyScope.GLOBAL,
            )
        else:
            new_state = tracker.activate_emergency(
                level=level,
                activated_by=activated_by,
                reason="X-Test Global STRICT 설정",
                namespace="global",
                scope=EmergencyScope.GLOBAL,
            )

        return {
            "action": "set_global_state",
            "previous_state": previous_mode,
            "new_state": new_state.governance_mode,
            "level": level.name if hasattr(level, "name") else str(level),
            "region": "global",
            "timestamp": timezone.now().isoformat(),
        }

    def _set_regional_state(
        self,
        tracker: Any,
        namespace: str,
        level: Any,
        activated_by: str = "xtest-scenario",
    ) -> dict[str, Any]:
        """
        Regional Emergency 상태 설정.

        Args:
            tracker: NamespacedEmergencyTracker 인스턴스
            namespace: 대상 리전 (예: "seoul", "tokyo")
            level: EmergencyLevel
            activated_by: 활성화 주체

        Returns:
            상태 전환 정보 딕셔너리
        """
        from selfhealing.services.coordination.enums import EmergencyScope
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        previous_state = tracker.get_state(namespace=namespace)
        previous_mode = previous_state.governance_mode if previous_state else "NORMAL"

        if level == EmergencyLevel.NORMAL:
            new_state = tracker.deactivate_emergency(
                deactivated_by=activated_by,
                namespace=namespace,
                scope=EmergencyScope.REGIONAL,
            )
        else:
            new_state = tracker.activate_emergency(
                level=level,
                activated_by=activated_by,
                reason=f"X-Test Regional STRICT 설정: {namespace}",
                namespace=namespace,
                scope=EmergencyScope.REGIONAL,
            )

        return {
            "action": "set_regional_state",
            "previous_state": previous_mode,
            "new_state": new_state.governance_mode,
            "level": level.name if hasattr(level, "name") else str(level),
            "region": namespace,
            "timestamp": timezone.now().isoformat(),
        }

    def _set_admin_override(
        self,
        tracker: Any,
        namespace: str,
        active: bool,
    ) -> dict[str, Any]:
        """
        Admin Override 설정 (precedence 파라미터로 제어).

        Admin Override가 활성화되면 get_effective_state() 호출 시
        precedence="ADMIN_OVERRIDE"를 사용하여 Regional 우선 적용.

        Args:
            tracker: NamespacedEmergencyTracker 인스턴스
            namespace: 대상 리전
            active: Override 활성화 여부

        Returns:
            상태 전환 정보 딕셔너리
        """
        # Admin Override는 호출 시 precedence 파라미터로 제어됨
        # 여기서는 상태 추적용 플래그만 관리
        return {
            "action": "set_admin_override",
            "previous_state": "OFF" if active else "ON",
            "new_state": "ON" if active else "OFF",
            "region": namespace,
            "timestamp": timezone.now().isoformat(),
        }

    # =========================================================================
    # Step 3: get_effective_state() 연동 (144 문서 3-1, 3-2, 3-3)
    # =========================================================================

    def _get_effective_state_with_logging(
        self,
        tracker: Any,
        namespace: str,
        precedence: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """
        get_effective_state() 호출 및 결과 로깅.

        AtomicStateQuery를 통해 원자적으로 상태를 조회합니다.

        Args:
            tracker: NamespacedEmergencyTracker 인스턴스
            namespace: 대상 리전
            precedence: 우선순위 ("AUTO", "ADMIN_OVERRIDE" 등)

        Returns:
            (ScopedEmergencyState, 로그 딕셔너리)
        """
        state = tracker.get_effective_state(
            namespace=namespace,
            precedence=precedence,
        )

        log_entry = {
            "action": "get_effective_state",
            "namespace": namespace,
            "precedence": precedence or "AUTO",
            "result_governance_mode": state.governance_mode,
            "result_scope": state.scope.value if hasattr(state.scope, "value") else str(state.scope),
            "result_level": (
                state.emergency_level.name if hasattr(state.emergency_level, "name") else str(state.emergency_level)
            ),
            "timestamp": timezone.now().isoformat(),
        }

        return state, log_entry

    def execute(self) -> ScenarioResult:
        """8단계 시나리오 실행."""
        from selfhealing.services.coordination.enums import EmergencyScope
        from selfhealing.services.emergency_mode.enums import EmergencyLevel
        from selfhealing.services.namespace_emergency.tracker import (
            NamespacedEmergencyTracker,
        )

        target_region = self.config.get("target_region", "seoul")
        redis_client = self.config.get("redis_client")
        state_transitions: list[dict[str, Any]] = []
        admin_override_active = False

        # Tracker 초기화 (테스트용 Mock 백엔드 주입 가능)
        if redis_client:
            from selfhealing.services.namespace_emergency.atomic_query import (
                AtomicStateQuery,
            )

            backend = MockStateBackend(redis_client=redis_client)
            atomic_query = AtomicStateQuery(redis_client=redis_client)
            tracker = NamespacedEmergencyTracker(
                backend=backend,
                atomic_query=atomic_query,
            )
        else:
            tracker = NamespacedEmergencyTracker()

        # =====================================================================
        # Step 1: 초기 상태 확인 (Global: NORMAL, Regional: NORMAL)
        # =====================================================================
        def step1():
            # 상태 초기화 (이전 테스트 잔재 제거)
            self._set_global_state(tracker, EmergencyLevel.NORMAL)
            self._set_regional_state(tracker, target_region, EmergencyLevel.NORMAL)

            state, log = self._get_effective_state_with_logging(tracker, target_region)
            state_transitions.append(log)

            return f"Global: NORMAL, Regional: NORMAL, governance: {state.governance_mode}"

        if not self._execute_step(
            1,
            "check_initial_state",
            "namespaced_emergency_tracker",
            "Global: NORMAL, Regional: NORMAL",
            step1,
        ):
            return self.result

        # =====================================================================
        # Step 2: Regional STRICT 설정
        # =====================================================================
        def step2():
            transition = self._set_regional_state(tracker, target_region, EmergencyLevel.LEVEL_2)
            state_transitions.append(transition)
            return f"Regional: STRICT (LEVEL_2)"

        if not self._execute_step(
            2,
            "set_regional_strict",
            "namespaced_emergency_tracker",
            "Regional: STRICT",
            step2,
        ):
            return self.result

        # =====================================================================
        # Step 3: get_effective_state() 호출 (STRICT - Regional 우선)
        # =====================================================================
        def step3():
            state, log = self._get_effective_state_with_logging(tracker, target_region)
            state_transitions.append(log)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            3,
            "get_effective_state_regional_priority",
            "atomic_state_query",
            "governance: STRICT, scope: regional",
            step3,
        ):
            return self.result

        # =====================================================================
        # Step 4: Global STRICT 설정
        # =====================================================================
        def step4():
            transition = self._set_global_state(tracker, EmergencyLevel.LEVEL_3)
            state_transitions.append(transition)
            return "Global: STRICT (LEVEL_3)"

        if not self._execute_step(
            4,
            "set_global_strict",
            "namespaced_emergency_tracker",
            "Global: STRICT",
            step4,
        ):
            return self.result

        # =====================================================================
        # Step 5: get_effective_state() 호출 (STRICT - Global 오버라이드)
        # =====================================================================
        def step5():
            state, log = self._get_effective_state_with_logging(tracker, target_region)
            state_transitions.append(log)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            5,
            "get_effective_state_global_override",
            "atomic_state_query",
            "governance: STRICT, scope: global",
            step5,
        ):
            return self.result

        # =====================================================================
        # Step 6: Regional ADMIN_OVERRIDE 설정
        # =====================================================================
        def step6():
            nonlocal admin_override_active
            # Regional을 NORMAL로 변경
            self._set_regional_state(tracker, target_region, EmergencyLevel.NORMAL)
            # Admin Override 플래그 활성화
            transition = self._set_admin_override(tracker, target_region, True)
            state_transitions.append(transition)
            admin_override_active = True
            return "Regional: ADMIN_OVERRIDE (NORMAL)"

        if not self._execute_step(
            6,
            "set_admin_override",
            "namespaced_emergency_tracker",
            "Regional: ADMIN_OVERRIDE",
            step6,
        ):
            return self.result

        # =====================================================================
        # Step 7: get_effective_state() 호출 (NORMAL - Admin 승리)
        # =====================================================================
        def step7():
            # Admin Override 활성화 상태이므로 precedence="ADMIN_OVERRIDE" 사용
            precedence = "ADMIN_OVERRIDE" if admin_override_active else None
            state, log = self._get_effective_state_with_logging(tracker, target_region, precedence=precedence)
            state_transitions.append(log)
            return f"governance: {state.governance_mode}, scope: {state.scope.value}"

        if not self._execute_step(
            7,
            "get_effective_state_admin_wins",
            "atomic_state_query",
            "governance: NORMAL, scope: regional",
            step7,
        ):
            return self.result

        # =====================================================================
        # Step 8: 상태 원복 (모든 상태 NORMAL)
        # =====================================================================
        def step8():
            nonlocal admin_override_active
            # Global 원복
            self._set_global_state(tracker, EmergencyLevel.NORMAL)
            # Regional 원복
            self._set_regional_state(tracker, target_region, EmergencyLevel.NORMAL)
            # Admin Override 해제
            self._set_admin_override(tracker, target_region, False)
            admin_override_active = False

            state, log = self._get_effective_state_with_logging(tracker, target_region)
            state_transitions.append(log)
            return f"All states restored: governance={state.governance_mode}"

        self._execute_step(
            8,
            "restore_all_states",
            "namespaced_emergency_tracker",
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

    NamespacedEmergencyTracker를 사용하여 특정 리전만 격리되고
    다른 리전은 정상인지 확인합니다.

    Steps:
    1. 현재 리전 확인
    2. 특정 리전 STRICT 설정 (격리)
    3. 다른 리전 상태 확인 (NORMAL)
    4. 격리 상태 검증 (타겟만 격리됨)
    5. 격리 해제

    Config options:
    - target_region: str - 격리할 리전 (기본: seoul)
    - other_region: str - 비교용 다른 리전 (기본: tokyo)
    - redis_client: Redis 클라이언트 (테스트용 주입)
    """

    scenario_name = "multi_region_isolation_test"
    max_timeout_seconds = 60

    def _create_tracker(self) -> tuple[Any, Any]:
        """
        NamespacedEmergencyTracker 생성.

        Returns:
            (tracker, atomic_query) 튜플
        """
        from selfhealing.services.namespace_emergency.tracker import (
            NamespacedEmergencyTracker,
        )

        redis_client = self.config.get("redis_client")

        if redis_client:
            from selfhealing.services.namespace_emergency.atomic_query import (
                AtomicStateQuery,
            )

            backend = MockStateBackend(redis_client=redis_client)
            atomic_query = AtomicStateQuery(redis_client=redis_client)
            tracker = NamespacedEmergencyTracker(
                backend=backend,
                atomic_query=atomic_query,
            )
            return tracker, atomic_query
        else:
            tracker = NamespacedEmergencyTracker()
            return tracker, None

    def execute(self) -> ScenarioResult:
        """5단계 시나리오 실행."""
        from selfhealing.services.coordination.enums import EmergencyScope
        from selfhealing.services.emergency_mode.enums import EmergencyLevel

        target_region = self.config.get("target_region", "seoul")
        other_region = self.config.get("other_region", "tokyo")

        tracker, _ = self._create_tracker()

        # =====================================================================
        # Step 1: 현재 리전 확인
        # =====================================================================
        def step1():
            # 초기화: 모든 리전 NORMAL로 설정
            tracker.deactivate_emergency(
                deactivated_by="xtest-init",
                namespace=target_region,
                scope=EmergencyScope.REGIONAL,
            )
            tracker.deactivate_emergency(
                deactivated_by="xtest-init",
                namespace=other_region,
                scope=EmergencyScope.REGIONAL,
            )
            return f"region: {target_region}"

        if not self._execute_step(
            1,
            "check_current_region",
            "namespaced_emergency_tracker",
            f"region: {target_region}",
            step1,
        ):
            return self.result

        # =====================================================================
        # Step 2: 특정 리전 STRICT 설정 (격리)
        # =====================================================================
        def step2():
            state = tracker.activate_emergency(
                level=EmergencyLevel.LEVEL_2,
                activated_by="xtest-scenario",
                reason="X-Test 다중 리전 격리 테스트",
                namespace=target_region,
                scope=EmergencyScope.REGIONAL,
            )
            return f"{target_region}: STRICT (isolated={state.governance_mode == 'STRICT'})"

        if not self._execute_step(
            2,
            "set_region_strict",
            "namespaced_emergency_tracker",
            f"{target_region}: STRICT",
            step2,
        ):
            return self.result

        # =====================================================================
        # Step 3: 다른 리전 상태 확인 (NORMAL)
        # =====================================================================
        def step3():
            state = tracker.get_effective_state(namespace=other_region)
            if state.governance_mode == "STRICT":
                return f"{other_region}: STRICT (unexpected)"
            return f"{other_region}: NORMAL"

        if not self._execute_step(
            3,
            "check_other_region_normal",
            "namespaced_emergency_tracker",
            f"{other_region}: NORMAL",
            step3,
        ):
            return self.result

        # =====================================================================
        # Step 4: 격리 상태 검증 (타겟만 격리됨)
        # =====================================================================
        def step4():
            target_state = tracker.get_effective_state(namespace=target_region)
            other_state = tracker.get_effective_state(namespace=other_region)

            target_isolated = target_state.governance_mode == "STRICT"
            other_isolated = other_state.governance_mode == "STRICT"

            isolated_regions = []
            if target_isolated:
                isolated_regions.append(target_region)
            if other_isolated:
                isolated_regions.append(other_region)

            return f"isolated_regions: {isolated_regions}, {target_region}_isolated: {target_isolated}, {other_region}_isolated: {other_isolated}"

        expected_step4 = (
            f"isolated_regions: ['{target_region}'], {target_region}_isolated: True, {other_region}_isolated: False"
        )
        if not self._execute_step(
            4,
            "verify_isolation_state",
            "namespaced_emergency_tracker",
            expected_step4,
            step4,
        ):
            return self.result

        # =====================================================================
        # Step 5: 격리 해제
        # =====================================================================
        def step5():
            tracker.deactivate_emergency(
                deactivated_by="xtest-scenario",
                namespace=target_region,
                scope=EmergencyScope.REGIONAL,
            )
            state = tracker.get_effective_state(namespace=target_region)
            is_isolated = state.governance_mode == "STRICT"
            return f"{target_region}: NORMAL (restored={not is_isolated}, isolated={is_isolated})"

        self._execute_step(
            5,
            "restore_region",
            "namespaced_emergency_tracker",
            f"{target_region}: NORMAL",
            step5,
        )

        return self.result


__all__ = [
    "RegionalOverrideConflictScenario",
    "MultiRegionIsolationTestScenario",
]
