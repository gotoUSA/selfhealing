"""
Graceful Degradation 테스트 공통 fixtures 및 Mock 클래스.

분리된 테스트 파일들이 사용하는 공통 설정 및 mock 객체.

Refactored to use Factory Pattern (Phase 3):
- MockRedisClient → factories.MockRedisClient
- MockPipeline → factories.MockPipeline
- MockDistributedLock → factories.MockDistributedLock
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Factory Pattern imports - 중복된 Mock 클래스 대신 통합 Factory 사용
from tests.factories import MockRedisClient

# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    return MockRedisClient()


@pytest.fixture
def failing_redis():
    """Create failing Redis client."""
    return MockRedisClient(should_fail=True)


@pytest.fixture
def temp_dir():
    """Create temporary directory for WAL files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_entry():
    """Create sample log entry."""
    return {
        "event": "test_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"key": "value"},
    }
