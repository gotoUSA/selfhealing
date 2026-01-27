"""
Unit tests for EmergencyCoordinator.

Tests:
- Basic coordination flow
- AntiFlappingGuard integration
- Dry-Run mode
- TTL enforcement
- Namespace isolation
"""

import pytest
from datetime import datetime, timezone, timedelta

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.coordination.enums import ActionType, EmergencyScope
from selfhealing.services.coordination.models import (
    CoordinationAction,
    OverrideTTLConfig,
)
from selfhealing.services.coordination.anti_flapping import AntiFlappingGuard
from selfhealing.services.coordination.coordinator import (
    EmergencyCoordinator,
    DryRunAuditLogger,
)


class TestDryRunAuditLogger:
    """DryRunAuditLogger 테스트."""
    
    def test_log_would_execute_returns_event_id(self):
        """이벤트 ID 반환."""
        logger = DryRunAuditLogger()
        action = CoordinationAction(type=ActionType.GOVERNANCE_STRICT)
        
        event_id = logger.log_would_execute(
            action=action,
            namespace="seoul",
            context={"trigger": "test"},
        )
        
        assert event_id.startswith("dry-run-")
        assert len(event_id) > 10


class TestEmergencyCoordinatorBasic:
    """EmergencyCoordinator 기본 기능 테스트."""
    
    def test_create_coordinator(self):
        """Coordinator 생성."""
        coordinator = EmergencyCoordinator()
        
        assert coordinator is not None
        assert coordinator._dry_run_mode is False
    
    def test_get_state_returns_default(self):
        """네임스페이스 상태 조회 (기본값)."""
        coordinator = EmergencyCoordinator()
        
        state = coordinator.get_state("seoul")
        
        assert state.namespace == "seoul"
        assert state.emergency_level == EmergencyLevel.NORMAL
        assert state.governance_mode == "NORMAL"
    
    def test_get_state_same_namespace_returns_same(self):
        """동일 네임스페이스는 동일 상태 반환."""
        coordinator = EmergencyCoordinator()
        
        state1 = coordinator.get_state("tokyo")
        state2 = coordinator.get_state("tokyo")
        
        assert state1 is state2
    
    def test_get_state_different_namespace_isolated(self):
        """다른 네임스페이스는 독립적 상태."""
        coordinator = EmergencyCoordinator()
        
        state_seoul = coordinator.get_state("seoul")
        state_tokyo = coordinator.get_state("tokyo")
        
        assert state_seoul is not state_tokyo
        assert state_seoul.namespace == "seoul"
        assert state_tokyo.namespace == "tokyo"


class TestEmergencyCoordinatorLevelChange:
    """on_emergency_level_changed 테스트."""
    
    def test_level_change_without_actions(self):
        """액션 없는 레벨 변경."""
        coordinator = EmergencyCoordinator()
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
        )
        
        assert result.success is True
        assert result.cascade_event_id.startswith("cascade-")
        assert len(result.executed_actions) == 0
    
    def test_level_change_updates_state(self):
        """레벨 변경 시 상태 업데이트."""
        coordinator = EmergencyCoordinator()
        
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
        )
        
        state = coordinator.get_state("seoul")
        assert state.emergency_level == EmergencyLevel.LEVEL_3
    
    def test_level_change_with_actions(self):
        """액션 포함 레벨 변경."""
        coordinator = EmergencyCoordinator()
        
        actions = [
            CoordinationAction(
                type=ActionType.GOVERNANCE_STRICT,
                immediate=True,
            ),
        ]
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
        )
        
        assert result.success is True
        assert len(result.executed_actions) == 1
        assert result.executed_actions[0].action_type == ActionType.GOVERNANCE_STRICT
    
    def test_governance_strict_updates_mode(self):
        """GOVERNANCE_STRICT 액션이 모드 업데이트."""
        coordinator = EmergencyCoordinator()
        
        actions = [
            CoordinationAction(
                type=ActionType.GOVERNANCE_STRICT,
                immediate=True,
            ),
        ]
        
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
        )
        
        state = coordinator.get_state("seoul")
        assert state.governance_mode == "STRICT"


class TestEmergencyCoordinatorAntiFlapping:
    """AntiFlappingGuard 연동 테스트."""
    
    def test_cooldown_blocks_rapid_changes(self):
        """쿨다운 중 빠른 변경 차단."""
        guard = AntiFlappingGuard(level_cooldown_seconds=300)
        coordinator = EmergencyCoordinator(anti_flapping_guard=guard)
        
        # 첫 번째 변경
        result1 = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-1",
        )
        assert result1.success is True
        
        # 즉시 두 번째 변경 시도 (쿨다운 차단)
        result2 = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_1,
            new_level=EmergencyLevel.LEVEL_2,
            namespace="seoul",
            trigger_event_id="trigger-2",
        )
        assert result2.success is False
    
    def test_force_bypasses_flapping_guard(self):
        """force=True로 플래핑 가드 우회."""
        guard = AntiFlappingGuard(level_cooldown_seconds=300)
        coordinator = EmergencyCoordinator(anti_flapping_guard=guard)
        
        # 첫 번째 변경
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_1,
            namespace="seoul",
            trigger_event_id="trigger-1",
        )
        
        # force=True로 즉시 두 번째 변경
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.LEVEL_1,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-2",
            force=True,
        )
        
        assert result.success is True
    
    def test_get_anti_flapping_status(self):
        """플래핑 가드 상태 조회."""
        coordinator = EmergencyCoordinator()
        
        status = coordinator.get_anti_flapping_status()
        
        assert "level_cooldown_seconds" in status
        assert "is_locked_out" in status


class TestEmergencyCoordinatorDryRun:
    """Dry-Run 모드 테스트."""
    
    def test_global_dry_run_mode(self):
        """전역 Dry-Run 모드."""
        coordinator = EmergencyCoordinator(dry_run_mode=True)
        
        assert coordinator.is_dry_run_mode() is True
    
    def test_set_dry_run_mode(self):
        """Dry-Run 모드 설정."""
        coordinator = EmergencyCoordinator()
        
        coordinator.set_dry_run_mode(True)
        
        assert coordinator.is_dry_run_mode() is True
    
    def test_dry_run_action_not_executed(self):
        """Dry-Run 액션은 실행되지 않음."""
        coordinator = EmergencyCoordinator(dry_run_mode=True)
        
        actions = [
            CoordinationAction(
                type=ActionType.GOVERNANCE_STRICT,
                immediate=True,
            ),
        ]
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
            force=True,  # 플래핑 가드 우회
        )
        
        assert result.success is True
        assert result.executed_actions[0].was_dry_run is True
    
    def test_per_action_dry_run(self):
        """개별 액션 Dry-Run 모드."""
        coordinator = EmergencyCoordinator()  # 전역 dry-run은 False
        
        actions = [
            CoordinationAction(
                type=ActionType.CANARY_ROLLBACK,
                is_dry_run=True,  # 이 액션만 dry-run
            ),
        ]
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
            force=True,
        )
        
        assert result.executed_actions[0].was_dry_run is True


class TestEmergencyCoordinatorTTLEnforcement:
    """TTL 강제 테스트."""
    
    def test_ttl_enforcement_on_governance_action(self):
        """Governance 액션에 TTL 강제."""
        ttl_config = OverrideTTLConfig(
            default_override_ttl_minutes=120,
            max_override_ttl_minutes=480,
        )
        coordinator = EmergencyCoordinator(ttl_config=ttl_config)
        
        actions = [
            CoordinationAction(
                type=ActionType.GOVERNANCE_STRICT,
                ttl_minutes=None,  # 미지정
            ),
        ]
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
            force=True,
        )
        
        # 기본 TTL 적용됨
        assert result.executed_actions[0].details.get("effective_ttl_minutes") == 120
    
    def test_ttl_capped_at_max(self):
        """TTL 최대값 제한."""
        ttl_config = OverrideTTLConfig(max_override_ttl_minutes=480)
        coordinator = EmergencyCoordinator(ttl_config=ttl_config)
        
        actions = [
            CoordinationAction(
                type=ActionType.GOVERNANCE_STRICT,
                ttl_minutes=1000,  # 최대값 초과
            ),
        ]
        
        result = coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-123",
            actions=actions,
            force=True,
        )
        
        # 최대값으로 제한됨
        assert result.executed_actions[0].details.get("effective_ttl_minutes") == 480


class TestEmergencyCoordinatorNamespaceIsolation:
    """네임스페이스 격리 테스트."""
    
    def test_seoul_level3_does_not_affect_tokyo(self):
        """서울 LEVEL_3가 도쿄에 영향 없음."""
        coordinator = EmergencyCoordinator()
        
        # 서울에 LEVEL_3
        coordinator.on_emergency_level_changed(
            old_level=EmergencyLevel.NORMAL,
            new_level=EmergencyLevel.LEVEL_3,
            namespace="seoul",
            trigger_event_id="trigger-1",
            actions=[
                CoordinationAction(type=ActionType.GOVERNANCE_STRICT),
            ],
            force=True,
        )
        
        # 도쿄는 NORMAL 유지
        tokyo_state = coordinator.get_state("tokyo")
        assert tokyo_state.emergency_level == EmergencyLevel.NORMAL
        assert tokyo_state.governance_mode == "NORMAL"
        
        # 서울은 LEVEL_3, STRICT
        seoul_state = coordinator.get_state("seoul")
        assert seoul_state.emergency_level == EmergencyLevel.LEVEL_3
        assert seoul_state.governance_mode == "STRICT"
