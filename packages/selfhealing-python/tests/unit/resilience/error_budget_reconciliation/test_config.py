"""
ReconciliationConfig 테스트.
"""

import pytest


class TestReconciliationConfig:
    """ReconciliationConfig 테스트."""

    def test_default_config(self):
        """기본 설정 테스트."""
        from selfhealing.services.error_budget.reconciliation import (
            ReconciliationConfig,
            ApplyMode,
        )
        
        config = ReconciliationConfig()
        
        assert config.enabled is True
        assert config.auto_calculate is True
        assert config.auto_apply is False  # 자동 적용 비활성화
        assert config.apply_mode == ApplyMode.CAPPED
        assert config.max_adjustment_percent_per_cycle == 10.0

    def test_to_dict(self):
        """to_dict 변환 테스트."""
        from selfhealing.services.error_budget.reconciliation import ReconciliationConfig
        
        config = ReconciliationConfig(
            auto_apply=True,
            max_adjustment_percent_per_cycle=5.0,
        )
        
        result = config.to_dict()
        
        assert result["auto_apply"] is True
        assert result["max_adjustment_percent_per_cycle"] == 5.0

    def test_from_dict(self):
        """from_dict 복원 테스트."""
        from selfhealing.services.error_budget.reconciliation import (
            ReconciliationConfig,
            ApplyMode,
        )
        
        data = {
            "enabled": True,
            "auto_calculate": False,
            "apply_mode": "immediate",
            "max_adjustment_percent_per_cycle": 15.0,
        }
        
        config = ReconciliationConfig.from_dict(data)
        
        assert config.auto_calculate is False
        assert config.apply_mode == ApplyMode.IMMEDIATE
        assert config.max_adjustment_percent_per_cycle == 15.0
