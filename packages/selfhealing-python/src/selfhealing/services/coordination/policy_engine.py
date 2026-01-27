"""
Coordination Policy Engine.

정책 기반으로 Emergency 레벨 변경 시 어떤 액션을 실행할지 결정합니다.

Code reference:
    governance.py#L307-375 (정책 기반 동작 패턴)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from selfhealing.services.emergency_mode.enums import EmergencyLevel

from .enums import ActionType, EmergencyScope
from .models import CoordinationAction

logger = logging.getLogger(__name__)


@dataclass
class CoordinationPolicy:
    """
    연계 정책 정의.

    Emergency 레벨 변경 시 실행할 액션 세트를 정의합니다.

    Usage:
        policy = CoordinationPolicy(
            trigger_level=EmergencyLevel.LEVEL_3,
            scope=EmergencyScope.REGIONAL,
            actions=[
                CoordinationAction(type=ActionType.GOVERNANCE_STRICT, immediate=True),
                CoordinationAction(type=ActionType.CANARY_ROLLBACK, immediate=True),
            ],
            priority=100,
        )
    """

    trigger_level: EmergencyLevel
    """트리거되는 Emergency 레벨."""

    scope: EmergencyScope = EmergencyScope.REGIONAL
    """적용 범위 (REGIONAL or GLOBAL)."""

    actions: list[CoordinationAction] = field(default_factory=list)
    """실행할 액션 목록."""

    priority: int = 0
    """우선순위 (높을수록 먼저 실행)."""

    name: str = ""
    """정책 이름 (식별용)."""

    description: str = ""
    """정책 설명."""

    enabled: bool = True
    """활성화 여부."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "trigger_level": self.trigger_level.name,  # .name for string representation
            "scope": self.scope.value,
            "actions": [a.to_dict() for a in self.actions],
            "priority": self.priority,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinationPolicy:
        """딕셔너리에서 생성."""
        data = dict(data)

        if "trigger_level" in data:
            level = data["trigger_level"]
            if isinstance(level, str):
                # 문자열 이름으로부터 EmergencyLevel 얻기
                data["trigger_level"] = EmergencyLevel[level]
            elif isinstance(level, int):
                data["trigger_level"] = EmergencyLevel(level)
        if "scope" in data:
            data["scope"] = EmergencyScope(data["scope"])
        if "actions" in data:
            data["actions"] = [CoordinationAction.from_dict(a) for a in data["actions"]]

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CoordinationPolicyEngine:
    """
    연계 정책 엔진.

    설정 가능한 정책 기반으로 Emergency 상황에서
    어떤 연계 액션을 실행할지 결정합니다.

    Code reference:
        governance.py#L307 (정책 조회 및 적용 패턴)

    Usage:
        engine = CoordinationPolicyEngine()
        actions = engine.get_actions_for_level_change(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
        )
    """

    def __init__(
        self,
        policies: list[CoordinationPolicy] | None = None,
        use_defaults: bool = True,
    ):
        """
        Args:
            policies: 사용자 정의 정책 목록
            use_defaults: 기본 정책 사용 여부
        """
        self._policies: list[CoordinationPolicy] = []

        if use_defaults:
            self._policies.extend(self._get_default_policies())

        if policies:
            self._policies.extend(policies)

        # 우선순위 내림차순 정렬
        self._policies.sort(key=lambda p: p.priority, reverse=True)

    def _get_default_policies(self) -> list[CoordinationPolicy]:
        """
        기본 연계 정책 반환.

        Returns:
            기본 정책 목록
        """
        return [
            # LEVEL_2: Canary 일시 중지 (30초 유예)
            CoordinationPolicy(
                name="level_2_canary_pause",
                description="LEVEL_2 발생 시 진행 중인 Canary 롤아웃 일시 중지",
                trigger_level=EmergencyLevel.LEVEL_2,
                scope=EmergencyScope.REGIONAL,
                actions=[
                    CoordinationAction(
                        type=ActionType.CANARY_PAUSE,
                        delay_seconds=30,  # 30초 유예
                    ),
                    CoordinationAction(
                        type=ActionType.BUDGET_MULTIPLIER,
                        params={"multiplier": 3.0},
                    ),
                ],
                priority=10,
            ),
            # LEVEL_3: 전체 연계 대응
            CoordinationPolicy(
                name="level_3_full_response",
                description="LEVEL_3 발생 시 전체 연계 대응 (STRICT, Rollback, Multiplier)",
                trigger_level=EmergencyLevel.LEVEL_3,
                scope=EmergencyScope.REGIONAL,
                actions=[
                    CoordinationAction(
                        type=ActionType.GOVERNANCE_STRICT,
                        immediate=True,
                        params={"reason_prefix": "[AUTO-CASCADE]"},
                    ),
                    CoordinationAction(
                        type=ActionType.CANARY_ROLLBACK,
                        immediate=True,
                    ),
                    CoordinationAction(
                        type=ActionType.BUDGET_MULTIPLIER,
                        params={"multiplier": 5.0},
                    ),
                    CoordinationAction(
                        type=ActionType.NOTIFICATION,
                        params={
                            "channels": ["slack", "pagerduty"],
                            "severity": "critical",
                        },
                    ),
                ],
                priority=100,
            ),
        ]

    def get_actions_for_level_change(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        scope_filter: EmergencyScope | None = None,
    ) -> list[CoordinationAction]:
        """
        레벨 변경에 대해 실행할 액션 목록 조회.

        Args:
            old_level: 이전 레벨
            new_level: 새 레벨
            namespace: 대상 네임스페이스
            scope_filter: 범위 필터 (없으면 모든 범위)

        Returns:
            실행할 액션 목록 (우선순위순)
        """
        # 레벨이 상승한 경우만 액션 실행 (하락은 Recovery Coordinator 담당)
        if new_level.value <= old_level.value:
            logger.debug(
                f"[PolicyEngine] No actions: level not increasing "
                f"({old_level.name} -> {new_level.name})"
            )
            return []

        matching_actions: list[CoordinationAction] = []

        for policy in self._policies:
            # 비활성화 정책 스킵
            if not policy.enabled:
                continue

            # 트리거 레벨 확인
            if policy.trigger_level != new_level:
                continue

            # 범위 필터 확인
            if scope_filter and policy.scope != scope_filter:
                continue

            logger.info(
                f"[PolicyEngine] Policy matched: {policy.name} "
                f"for {new_level.name} on {namespace}"
            )

            matching_actions.extend(policy.actions)

        return matching_actions

    def get_policy_by_name(self, name: str) -> CoordinationPolicy | None:
        """이름으로 정책 조회."""
        for policy in self._policies:
            if policy.name == name:
                return policy
        return None

    def add_policy(self, policy: CoordinationPolicy) -> None:
        """
        정책 추가.

        우선순위 순서를 유지하며 정책을 추가합니다.
        """
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.priority, reverse=True)
        logger.info(f"[PolicyEngine] Policy added: {policy.name}")

    def remove_policy(self, name: str) -> bool:
        """
        정책 제거.

        Args:
            name: 제거할 정책 이름

        Returns:
            제거 성공 여부
        """
        original_count = len(self._policies)
        self._policies = [p for p in self._policies if p.name != name]

        if len(self._policies) < original_count:
            logger.info(f"[PolicyEngine] Policy removed: {name}")
            return True
        return False

    def enable_policy(self, name: str) -> bool:
        """정책 활성화."""
        policy = self.get_policy_by_name(name)
        if policy:
            policy.enabled = True
            logger.info(f"[PolicyEngine] Policy enabled: {name}")
            return True
        return False

    def disable_policy(self, name: str) -> bool:
        """정책 비활성화."""
        policy = self.get_policy_by_name(name)
        if policy:
            policy.enabled = False
            logger.info(f"[PolicyEngine] Policy disabled: {name}")
            return True
        return False

    def list_policies(self) -> list[dict[str, Any]]:
        """모든 정책 목록 조회."""
        return [p.to_dict() for p in self._policies]

    def get_enabled_policies(self) -> list[CoordinationPolicy]:
        """활성화된 정책 목록 조회."""
        return [p for p in self._policies if p.enabled]
