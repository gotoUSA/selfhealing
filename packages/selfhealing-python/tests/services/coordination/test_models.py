"""
Unit tests for Emergency Coordination Models.

Tests:
- CoordinationAction with TTL enforcement
- ScopedEmergencyState namespace isolation
- OverrideTTLConfig TTL enforcement logic
- ActionResult and CoordinationResult
"""

import pytest
from datetime import datetime, timezone, timedelta

from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.coordination.enums import ActionType, EmergencyScope
from selfhealing.services.coordination.models import (
    CoordinationAction,
    ScopedEmergencyState,
    OverrideTTLConfig,
    ActionResult,
    CoordinationResult,
)


class TestCoordinationAction:
    """CoordinationAction 테스트."""
    
    def test_create_action_with_defaults(self):
        """기본값으로 액션 생성."""
        action = CoordinationAction(type=ActionType.GOVERNANCE_STRICT)
        
        assert action.type == ActionType.GOVERNANCE_STRICT
        assert action.immediate is False
        assert action.delay_seconds == 0
        assert action.params == {}
        assert action.ttl_minutes is None
        assert action.is_dry_run is False
    
    def test_create_action_with_ttl(self):
        """TTL 설정으로 액션 생성."""
        action = CoordinationAction(
            type=ActionType.GOVERNANCE_STRICT,
            ttl_minutes=120,
            immediate=True,
        )
        
        assert action.ttl_minutes == 120
        assert action.immediate is True
    
    def test_create_action_with_params(self):
        """파라미터 설정으로 액션 생성."""
        action = CoordinationAction(
            type=ActionType.BUDGET_MULTIPLIER,
            params={"multiplier": 5.0},
        )
        
        assert action.params["multiplier"] == 5.0
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        action = CoordinationAction(
            type=ActionType.CANARY_PAUSE,
            delay_seconds=30,
            ttl_minutes=60,
        )
        
        result = action.to_dict()
        
        assert result["type"] == "canary_pause"
        assert result["delay_seconds"] == 30
        assert result["ttl_minutes"] == 60
    
    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "type": "governance_strict",
            "immediate": True,
            "ttl_minutes": 120,
        }
        
        action = CoordinationAction.from_dict(data)
        
        assert action.type == ActionType.GOVERNANCE_STRICT
        assert action.immediate is True
        assert action.ttl_minutes == 120
    
    def test_dry_run_mode(self):
        """Dry-Run 모드 설정."""
        action = CoordinationAction(
            type=ActionType.CANARY_ROLLBACK,
            is_dry_run=True,
        )
        
        assert action.is_dry_run is True


class TestScopedEmergencyState:
    """ScopedEmergencyState 테스트."""
    
    def test_create_default_state(self):
        """기본 상태 생성 (NORMAL)."""
        state = ScopedEmergencyState(namespace="seoul")
        
        assert state.namespace == "seoul"
        assert state.emergency_level == EmergencyLevel.NORMAL
        assert state.governance_mode == "NORMAL"
        assert state.scope == EmergencyScope.REGIONAL
        assert state.is_active() is False
    
    def test_is_active_when_level_3(self):
        """LEVEL_3일 때 is_active 확인."""
        state = ScopedEmergencyState(
            namespace="tokyo",
            emergency_level=EmergencyLevel.LEVEL_3,
        )
        
        assert state.is_active() is True
    
    def test_is_expired_false_when_no_expiry(self):
        """만료 시간 미설정 시 is_expired=False."""
        state = ScopedEmergencyState(namespace="oregon")
        
        assert state.is_expired() is False
    
    def test_is_expired_true_when_past(self):
        """만료 시간 경과 시 is_expired=True."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        state = ScopedEmergencyState(
            namespace="oregon",
            expires_at=past,
        )
        
        assert state.is_expired() is True
    
    def test_is_expired_false_when_future(self):
        """만료 시간 미래 시 is_expired=False."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        state = ScopedEmergencyState(
            namespace="oregon",
            expires_at=future,
        )
        
        assert state.is_expired() is False
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        now = datetime.now(timezone.utc)
        state = ScopedEmergencyState(
            namespace="seoul",
            emergency_level=EmergencyLevel.LEVEL_2,
            governance_mode="STRICT",
            activated_at=now,
        )
        
        result = state.to_dict()
        
        assert result["namespace"] == "seoul"
        assert result["emergency_level"] == 2  # EmergencyLevel.LEVEL_2.value
        assert result["governance_mode"] == "STRICT"
        assert result["activated_at"] == now.isoformat()
    
    def test_from_dict(self):
        """딕셔너리에서 생성."""
        now = datetime.now(timezone.utc)
        data = {
            "namespace": "tokyo",
            "emergency_level": 3,
            "governance_mode": "STRICT",
            "scope": "global",
            "activated_at": now.isoformat(),
        }
        
        state = ScopedEmergencyState.from_dict(data)
        
        assert state.namespace == "tokyo"
        assert state.emergency_level == EmergencyLevel.LEVEL_3
        assert state.scope == EmergencyScope.GLOBAL


class TestOverrideTTLConfig:
    """OverrideTTLConfig 테스트."""
    
    def test_default_values(self):
        """기본값 확인."""
        config = OverrideTTLConfig()
        
        assert config.default_override_ttl_minutes == 120
        assert config.max_override_ttl_minutes == 480
        assert config.allow_permanent_override is False
    
    def test_enforce_ttl_uses_default_when_none(self):
        """TTL 미지정 시 기본값 적용."""
        config = OverrideTTLConfig()
        
        result = config.enforce_ttl(None)
        
        assert result == 120
    
    def test_enforce_ttl_uses_default_when_zero(self):
        """TTL=0 시 기본값 적용."""
        config = OverrideTTLConfig()
        
        result = config.enforce_ttl(0)
        
        assert result == 120
    
    def test_enforce_ttl_caps_at_max(self):
        """최대값 초과 시 제한."""
        config = OverrideTTLConfig(max_override_ttl_minutes=480)
        
        result = config.enforce_ttl(1000)
        
        assert result == 480
    
    def test_enforce_ttl_accepts_valid(self):
        """유효한 TTL 그대로 반환."""
        config = OverrideTTLConfig()
        
        result = config.enforce_ttl(240)
        
        assert result == 240
    
    def test_permanent_override_allowed_for_super_admin(self):
        """super_admin은 영구 오버라이드 가능."""
        config = OverrideTTLConfig(allow_permanent_override=True)
        
        result = config.enforce_ttl(None, role="super_admin")
        
        assert result == 0  # 영구 오버라이드
    
    def test_permanent_override_denied_for_regular_admin(self):
        """일반 admin은 영구 오버라이드 불가."""
        config = OverrideTTLConfig(allow_permanent_override=True)
        
        result = config.enforce_ttl(None, role="admin")
        
        assert result == 120  # 기본값 적용


class TestActionResult:
    """ActionResult 테스트."""
    
    def test_create_success_result(self):
        """성공 결과 생성."""
        result = ActionResult(
            success=True,
            action_type=ActionType.GOVERNANCE_STRICT,
            event_id="action-123",
            namespace="seoul",
        )
        
        assert result.success is True
        assert result.error is None
    
    def test_create_failure_result(self):
        """실패 결과 생성."""
        result = ActionResult(
            success=False,
            action_type=ActionType.CANARY_ROLLBACK,
            event_id="action-456",
            namespace="tokyo",
            error="Connection timeout",
        )
        
        assert result.success is False
        assert result.error == "Connection timeout"
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        result = ActionResult(
            success=True,
            action_type=ActionType.BUDGET_MULTIPLIER,
            event_id="action-789",
            namespace="oregon",
            was_dry_run=True,
        )
        
        data = result.to_dict()
        
        assert data["action_type"] == "budget_multiplier"
        assert data["was_dry_run"] is True


class TestCoordinationResult:
    """CoordinationResult 테스트."""
    
    def test_create_result(self):
        """결과 생성."""
        action_result = ActionResult(
            success=True,
            action_type=ActionType.GOVERNANCE_STRICT,
            event_id="action-1",
            namespace="seoul",
        )
        
        result = CoordinationResult(
            success=True,
            cascade_event_id="cascade-123",
            executed_actions=[action_result],
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            namespace="seoul",
        )
        
        assert result.success is True
        assert len(result.executed_actions) == 1
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        result = CoordinationResult(
            success=True,
            cascade_event_id="cascade-456",
            executed_actions=[],
            trigger_type="TEST",
            namespace="test",
        )
        
        data = result.to_dict()
        
        assert data["cascade_event_id"] == "cascade-456"
        assert data["executed_actions"] == []
