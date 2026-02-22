"""
CoordinationPolicyEngine 단위 테스트.

정책 기반 액션 결정 로직을 검증합니다.
"""


from selfhealing.services.coordination.enums import ActionType, EmergencyScope
from selfhealing.services.coordination.models import CoordinationAction
from selfhealing.services.coordination.policy_engine import (
    CoordinationPolicy,
    CoordinationPolicyEngine,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel


class TestCoordinationPolicy:
    """CoordinationPolicy 모델 테스트."""

    def test_create_policy_with_defaults(self):
        """기본값으로 정책 생성."""
        policy = CoordinationPolicy(
            trigger_level=EmergencyLevel.LEVEL_3,
        )

        assert policy.trigger_level == EmergencyLevel.LEVEL_3
        assert policy.scope == EmergencyScope.REGIONAL
        assert policy.actions == []
        assert policy.priority == 0
        assert policy.enabled is True

    def test_create_policy_with_actions(self):
        """액션 포함 정책 생성."""
        actions = [
            CoordinationAction(type=ActionType.GOVERNANCE_STRICT, immediate=True),
            CoordinationAction(type=ActionType.CANARY_ROLLBACK, delay_seconds=30),
        ]

        policy = CoordinationPolicy(
            name="test_policy",
            trigger_level=EmergencyLevel.LEVEL_3,
            scope=EmergencyScope.GLOBAL,
            actions=actions,
            priority=100,
        )

        assert policy.name == "test_policy"
        assert len(policy.actions) == 2
        assert policy.priority == 100
        assert policy.scope == EmergencyScope.GLOBAL

    def test_policy_to_dict(self):
        """딕셔너리 변환 테스트."""
        policy = CoordinationPolicy(
            name="test",
            trigger_level=EmergencyLevel.LEVEL_2,
            actions=[
                CoordinationAction(type=ActionType.CANARY_PAUSE),
            ],
        )

        data = policy.to_dict()

        assert data["name"] == "test"
        assert data["trigger_level"] == "LEVEL_2"
        assert data["scope"] == "regional"
        assert len(data["actions"]) == 1

    def test_policy_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        data = {
            "name": "from_dict_policy",
            "trigger_level": "LEVEL_3",
            "scope": "global",
            "actions": [
                {"type": "governance_strict", "immediate": True},
            ],
            "priority": 50,
        }

        policy = CoordinationPolicy.from_dict(data)

        assert policy.name == "from_dict_policy"
        assert policy.trigger_level == EmergencyLevel.LEVEL_3
        assert policy.scope == EmergencyScope.GLOBAL
        assert len(policy.actions) == 1
        assert policy.actions[0].type == ActionType.GOVERNANCE_STRICT


class TestCoordinationPolicyEngine:
    """CoordinationPolicyEngine 테스트."""

    def test_engine_with_default_policies(self):
        """기본 정책으로 엔진 생성."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        policies = engine.list_policies()
        assert len(policies) >= 2  # LEVEL_2, LEVEL_3 기본 정책

    def test_engine_without_defaults(self):
        """기본 정책 없이 엔진 생성."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        policies = engine.list_policies()
        assert len(policies) == 0

    def test_get_actions_for_level_3_escalation(self):
        """LEVEL_3로 상승 시 액션 조회."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
        )

        assert len(actions) > 0
        action_types = [a.type for a in actions]
        assert ActionType.GOVERNANCE_STRICT in action_types
        assert ActionType.CANARY_ROLLBACK in action_types

    def test_get_actions_for_level_2(self):
        """LEVEL_2로 상승 시 액션 조회."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_2,
            namespace="tokyo",
        )

        assert len(actions) > 0
        action_types = [a.type for a in actions]
        assert ActionType.CANARY_PAUSE in action_types

    def test_no_actions_on_level_decrease(self):
        """레벨 하락 시 액션 없음 (Recovery Coordinator 담당)."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.LEVEL_3,
            new_level=EmergencyLevel.NORMAL,
            namespace="seoul",
        )

        assert len(actions) == 0

    def test_no_actions_on_same_level(self):
        """동일 레벨 시 액션 없음."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.LEVEL_2,
            new_level=EmergencyLevel.LEVEL_2,
            namespace="seoul",
        )

        assert len(actions) == 0

    def test_add_custom_policy(self):
        """사용자 정의 정책 추가."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        custom_policy = CoordinationPolicy(
            name="custom_level_1",
            trigger_level=EmergencyLevel.LEVEL_1,
            actions=[
                CoordinationAction(type=ActionType.NOTIFICATION),
            ],
        )

        engine.add_policy(custom_policy)

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="oregon",
        )

        assert len(actions) == 1
        assert actions[0].type == ActionType.NOTIFICATION

    def test_remove_policy(self):
        """정책 제거."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        engine.add_policy(CoordinationPolicy(
            name="to_remove",
            trigger_level=EmergencyLevel.LEVEL_1,
        ))

        assert len(engine.list_policies()) == 1

        result = engine.remove_policy("to_remove")

        assert result is True
        assert len(engine.list_policies()) == 0

    def test_enable_disable_policy(self):
        """정책 활성화/비활성화."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        engine.add_policy(CoordinationPolicy(
            name="toggle_me",
            trigger_level=EmergencyLevel.LEVEL_1,
            actions=[CoordinationAction(type=ActionType.NOTIFICATION)],
        ))

        # 비활성화
        engine.disable_policy("toggle_me")

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="test",
        )
        assert len(actions) == 0  # 비활성화된 정책 무시

        # 재활성화
        engine.enable_policy("toggle_me")

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="test",
        )
        assert len(actions) == 1

    def test_policy_priority_order(self):
        """정책 우선순위 순서 확인."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        engine.add_policy(CoordinationPolicy(
            name="low_priority",
            trigger_level=EmergencyLevel.LEVEL_3,
            priority=10,
            actions=[CoordinationAction(type=ActionType.NOTIFICATION)],
        ))

        engine.add_policy(CoordinationPolicy(
            name="high_priority",
            trigger_level=EmergencyLevel.LEVEL_3,
            priority=100,
            actions=[CoordinationAction(type=ActionType.GOVERNANCE_STRICT)],
        ))

        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="test",
        )

        # 높은 우선순위 정책의 액션이 먼저
        assert actions[0].type == ActionType.GOVERNANCE_STRICT
        assert actions[1].type == ActionType.NOTIFICATION

    def test_scope_filter(self):
        """범위 필터 테스트."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        engine.add_policy(CoordinationPolicy(
            name="regional_only",
            trigger_level=EmergencyLevel.LEVEL_3,
            scope=EmergencyScope.REGIONAL,
            actions=[CoordinationAction(type=ActionType.CANARY_PAUSE)],
        ))

        engine.add_policy(CoordinationPolicy(
            name="global_only",
            trigger_level=EmergencyLevel.LEVEL_3,
            scope=EmergencyScope.GLOBAL,
            actions=[CoordinationAction(type=ActionType.GOVERNANCE_STRICT)],
        ))

        # REGIONAL 필터
        regional_actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="test",
            scope_filter=EmergencyScope.REGIONAL,
        )
        assert len(regional_actions) == 1
        assert regional_actions[0].type == ActionType.CANARY_PAUSE

        # GLOBAL 필터
        global_actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="test",
            scope_filter=EmergencyScope.GLOBAL,
        )
        assert len(global_actions) == 1
        assert global_actions[0].type == ActionType.GOVERNANCE_STRICT

    def test_get_policy_by_name(self):
        """이름으로 정책 조회."""
        engine = CoordinationPolicyEngine(use_defaults=True)

        policy = engine.get_policy_by_name("level_3_full_response")

        assert policy is not None
        assert policy.trigger_level == EmergencyLevel.LEVEL_3

    def test_get_enabled_policies(self):
        """활성화된 정책만 조회."""
        engine = CoordinationPolicyEngine(use_defaults=False)

        engine.add_policy(CoordinationPolicy(
            name="enabled",
            trigger_level=EmergencyLevel.LEVEL_1,
            enabled=True,
        ))
        engine.add_policy(CoordinationPolicy(
            name="disabled",
            trigger_level=EmergencyLevel.LEVEL_2,
            enabled=False,
        ))

        enabled = engine.get_enabled_policies()

        assert len(enabled) == 1
        assert enabled[0].name == "enabled"
