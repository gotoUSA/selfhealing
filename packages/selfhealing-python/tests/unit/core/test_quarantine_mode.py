"""
Quarantine Mode 테스트.

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import os
import pytest
from unittest.mock import patch


class TestQuarantineMode:
    """Quarantine Mode 기능 테스트."""
    
    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def teardown_method(self):
        """각 테스트 후에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def test_is_quarantine_mode_initially_false(self):
        """초기 Quarantine Mode는 False."""
        from selfhealing.core.cluster_identity import (
            is_quarantine_mode,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        assert is_quarantine_mode() is False
    
    def test_set_quarantine_mode_manually(self):
        """수동으로 Quarantine Mode 설정."""
        from selfhealing.core.cluster_identity import (
            is_quarantine_mode,
            set_quarantine_mode,
        )
        
        set_quarantine_mode(True)
        assert is_quarantine_mode() is True
        
        set_quarantine_mode(False)
        assert is_quarantine_mode() is False
    
    def test_quarantine_mode_enabled_on_invalid_cluster_id(self):
        """잘못된 cluster_id일 때 Quarantine Mode 활성화."""
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            is_quarantine_mode,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        
        # 기본값 'default'는 유효하지 않음
        with patch.dict(os.environ, {
            "SELFHEALING_CLUSTER_ID": "default",
            "SELFHEALING_FAIL_FAST": "false",
        }, clear=False):
            identity = get_cluster_identity(skip_validation=False)
            # default는 유효하지 않으므로 Quarantine Mode 활성화
            assert identity.cluster_id == "default"
            assert is_quarantine_mode() is True
    
    def test_quarantine_mode_disabled_on_valid_cluster_id(self):
        """유효한 cluster_id일 때 Quarantine Mode 비활성화."""
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            is_quarantine_mode,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        
        with patch.dict(os.environ, {
            "SELFHEALING_CLUSTER_ID": "seoul-prod-01",
            "SELFHEALING_REGION": "seoul",
            "SELFHEALING_FAIL_FAST": "false",
        }, clear=False):
            identity = get_cluster_identity(skip_validation=False)
            assert identity.cluster_id == "seoul-prod-01"
            assert is_quarantine_mode() is False
    
    def test_reset_clears_quarantine_mode(self):
        """리셋하면 Quarantine Mode도 초기화."""
        from selfhealing.core.cluster_identity import (
            is_quarantine_mode,
            set_quarantine_mode,
            reset_cluster_identity,
        )
        
        set_quarantine_mode(True)
        assert is_quarantine_mode() is True
        
        reset_cluster_identity()
        assert is_quarantine_mode() is False


class TestPropagatorQuarantineMode:
    """GlobalConfigPropagator의 Quarantine Mode 동작 테스트."""
    
    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def teardown_method(self):
        """각 테스트 후에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def test_propagate_blocked_in_quarantine_mode(self):
        """Quarantine Mode에서는 전파가 차단됨."""
        from selfhealing.core.cluster_identity import set_quarantine_mode
        from selfhealing.services.config.propagator import (
            GlobalConfigPropagator,
            GlobalConfigChange,
            ConfigScope,
            PropagationTier,
        )
        
        set_quarantine_mode(True)
        
        propagator = GlobalConfigPropagator(redis_client=None)
        
        change = GlobalConfigChange(
            config_type="test",
            config_key="key1",
            new_value="value1",
            previous_value=None,
            scope=ConfigScope.GLOBAL,
            tier=PropagationTier.TIER_1_IMMEDIATE,
            source_cluster="test-cluster",
        )
        
        result = propagator.propagate(change)
        assert result is False  # Quarantine Mode에서는 전파 실패
