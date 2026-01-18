"""
Emergency Mode 테스트 공통 설정.

이 패키지의 모든 테스트에서 사용하는 fixtures.
"""

import pytest


@pytest.fixture
def reset_manager():
    """각 테스트 전에 매니저 초기화."""
    # Lazy import to avoid Prometheus registry collision
    from selfhealing.services.emergency_mode import (
        GracefulDegradationManager,
        get_emergency_manager,
    )
    
    # 싱글톤 인스턴스 초기화
    GracefulDegradationManager._instance = None
    manager = get_emergency_manager()
    manager.reset()
    yield manager
    manager.reset()
