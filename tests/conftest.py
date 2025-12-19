"""
Global pytest configuration and fixtures.

Auto-skip logic for infrastructure-dependent tests.
"""
import pytest


def pytest_configure(config):
    """pytest 시작 시 DB 연결 상태 확인 및 환경 설정."""
    # 마커가 이미 pyproject.toml에 정의되어 있으므로 여기서는 skip


def pytest_collection_modifyitems(config, items):
    """
    테스트 수집 후 requires_db, requires_redis 마커가 있는 테스트 자동 skip.
    
    환경변수로 인프라가 available하다고 표시하지 않으면 skip.
    """
    import os
    
    db_available = os.environ.get("TEST_DB_AVAILABLE", "").lower() == "true"
    redis_available = os.environ.get("TEST_REDIS_AVAILABLE", "").lower() == "true"
    
    skip_db = pytest.mark.skip(reason="Database not available (set TEST_DB_AVAILABLE=true)")
    skip_redis = pytest.mark.skip(reason="Redis not available (set TEST_REDIS_AVAILABLE=true)")
    
    for item in items:
        if not db_available and "requires_db" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_db)
        if not redis_available and "requires_redis" in [m.name for m in item.iter_markers()]:
            item.add_marker(skip_redis)
