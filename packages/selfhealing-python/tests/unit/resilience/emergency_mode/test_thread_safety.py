"""
Emergency Mode Thread Safety Tests.

Tests for thread-safe operations.
"""

import pytest
import threading


class TestThreadSafety:
    """스레드 안전성 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """각 테스트 전에 매니저 초기화."""
        from selfhealing.services.emergency_mode import (
            GracefulDegradationManager,
            get_emergency_manager,
        )
        
        GracefulDegradationManager._instance = None
        manager = get_emergency_manager()
        manager.reset()
        yield
        manager.reset()
    
    def test_concurrent_activation(self):
        """동시 활성화가 안전하게 처리되어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        manager = get_emergency_manager()
        errors = []
        
        def activate(level, name):
            try:
                manager.activate_manual(
                    level=level,
                    reason=f"Test {name}",
                    activated_by=name,
                )
            except Exception as e:
                errors.append(e)
        
        threads = [
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_1, "thread1")),
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_2, "thread2")),
            threading.Thread(target=activate, args=(EmergencyLevel.LEVEL_3, "thread3")),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # 에러가 없어야 함
        assert len(errors) == 0
        
        # 최종 상태가 유효해야 함
        state = manager.get_state()
        assert state.level in EmergencyLevel
        assert state.is_active is True
    
    def test_concurrent_reads(self):
        """동시 읽기가 안전하게 처리되어야 함."""
        from selfhealing.services.emergency_mode import EmergencyLevel, get_emergency_manager
        
        manager = get_emergency_manager()
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test",
            activated_by="test",
        )
        
        results = []
        errors = []
        
        def read_state():
            try:
                for _ in range(100):
                    state = manager.get_state()
                    results.append(state.level)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=read_state) for _ in range(5)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        assert all(level == EmergencyLevel.LEVEL_2 for level in results)
