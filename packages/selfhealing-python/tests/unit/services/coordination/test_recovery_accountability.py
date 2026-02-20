"""
Tests for RecoveryAccountabilityConfig.

72번 문서 §5.4.1에 정의된 복구 책임 추적 설정 테스트.

테스트 대상:
- RecoveryAccountabilityConfig 필드
- can_acknowledge 메서드
- to_dict / from_dict 변환
"""

from __future__ import annotations

import pytest

from selfhealing.services.coordination.models import RecoveryAccountabilityConfig


class TestRecoveryAccountabilityConfigDefaults:
    """기본값 테스트."""
    
    def test_requires_manual_acknowledgement_default_false(self):
        """requires_manual_acknowledgement 기본값은 False."""
        config = RecoveryAccountabilityConfig()
        assert config.requires_manual_acknowledgement is False
    
    def test_ready_to_restore_timeout_default_24h(self):
        """READY_TO_RESTORE 타임아웃 기본값은 24시간."""
        config = RecoveryAccountabilityConfig()
        assert config.ready_to_restore_timeout_hours == 24
    
    def test_default_acknowledgement_roles(self):
        """기본 승인 가능 역할."""
        config = RecoveryAccountabilityConfig()
        assert "admin" in config.acknowledgement_required_roles
        assert "sre_lead" in config.acknowledgement_required_roles
    
    def test_auto_restore_after_hours_default_8h(self):
        """자동 복구 대기 시간 기본값은 8시간."""
        config = RecoveryAccountabilityConfig()
        assert config.auto_restore_after_hours == 8.0
    
    def test_default_escalation_channels(self):
        """기본 에스컬레이션 채널."""
        config = RecoveryAccountabilityConfig()
        assert "slack" in config.escalation_channels
        assert "pagerduty" in config.escalation_channels


class TestRecoveryAccountabilityConfigCanAcknowledge:
    """can_acknowledge 메서드 테스트."""
    
    def test_admin_can_acknowledge(self):
        """admin은 승인 가능."""
        config = RecoveryAccountabilityConfig()
        assert config.can_acknowledge("admin") is True
    
    def test_sre_lead_can_acknowledge(self):
        """sre_lead는 승인 가능."""
        config = RecoveryAccountabilityConfig()
        assert config.can_acknowledge("sre_lead") is True
    
    def test_operator_cannot_acknowledge(self):
        """operator는 승인 불가."""
        config = RecoveryAccountabilityConfig()
        assert config.can_acknowledge("operator") is False
    
    def test_custom_roles(self):
        """커스텀 역할 설정."""
        config = RecoveryAccountabilityConfig(
            acknowledgement_required_roles=["cto", "vp_engineering"]
        )
        assert config.can_acknowledge("cto") is True
        assert config.can_acknowledge("admin") is False


class TestRecoveryAccountabilityConfigCustomValues:
    """커스텀 값 설정 테스트."""
    
    def test_enable_manual_acknowledgement(self):
        """수동 승인 활성화."""
        config = RecoveryAccountabilityConfig(
            requires_manual_acknowledgement=True
        )
        assert config.requires_manual_acknowledgement is True
    
    def test_custom_timeout(self):
        """커스텀 타임아웃 설정."""
        config = RecoveryAccountabilityConfig(
            ready_to_restore_timeout_hours=48
        )
        assert config.ready_to_restore_timeout_hours == 48
    
    def test_custom_auto_restore_hours(self):
        """커스텀 자동 복구 대기 시간."""
        config = RecoveryAccountabilityConfig(
            auto_restore_after_hours=4.0
        )
        assert config.auto_restore_after_hours == 4.0
    
    def test_custom_escalation_channels(self):
        """커스텀 에스컬레이션 채널."""
        config = RecoveryAccountabilityConfig(
            escalation_channels=["email", "sms"]
        )
        assert "email" in config.escalation_channels
        assert "sms" in config.escalation_channels
        assert "slack" not in config.escalation_channels


class TestRecoveryAccountabilityConfigSerialization:
    """직렬화/역직렬화 테스트."""
    
    def test_to_dict(self):
        """딕셔너리로 변환."""
        config = RecoveryAccountabilityConfig(
            requires_manual_acknowledgement=True,
            ready_to_restore_timeout_hours=12,
            acknowledgement_required_roles=["admin"],
            auto_restore_after_hours=6.0,
            escalation_channels=["slack"],
        )
        
        data = config.to_dict()
        
        assert data["requires_manual_acknowledgement"] is True
        assert data["ready_to_restore_timeout_hours"] == 12
        assert data["acknowledgement_required_roles"] == ["admin"]
        assert data["auto_restore_after_hours"] == 6.0
        assert data["escalation_channels"] == ["slack"]
    
    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "requires_manual_acknowledgement": True,
            "ready_to_restore_timeout_hours": 12,
            "acknowledgement_required_roles": ["admin"],
            "auto_restore_after_hours": 6.0,
            "escalation_channels": ["slack"],
        }
        
        config = RecoveryAccountabilityConfig.from_dict(data)
        
        assert config.requires_manual_acknowledgement is True
        assert config.ready_to_restore_timeout_hours == 12
        assert config.acknowledgement_required_roles == ["admin"]
        assert config.auto_restore_after_hours == 6.0
        assert config.escalation_channels == ["slack"]
    
    def test_roundtrip(self):
        """직렬화 후 역직렬화하면 동일한 값."""
        original = RecoveryAccountabilityConfig(
            requires_manual_acknowledgement=True,
            ready_to_restore_timeout_hours=36,
        )
        
        data = original.to_dict()
        restored = RecoveryAccountabilityConfig.from_dict(data)
        
        assert restored.requires_manual_acknowledgement == original.requires_manual_acknowledgement
        assert restored.ready_to_restore_timeout_hours == original.ready_to_restore_timeout_hours
    
    def test_from_dict_ignores_unknown_fields(self):
        """알 수 없는 필드는 무시."""
        data = {
            "requires_manual_acknowledgement": True,
            "unknown_field": "ignored",
        }
        
        config = RecoveryAccountabilityConfig.from_dict(data)
        assert config.requires_manual_acknowledgement is True
        assert not hasattr(config, "unknown_field")
