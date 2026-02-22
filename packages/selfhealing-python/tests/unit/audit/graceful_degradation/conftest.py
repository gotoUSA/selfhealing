"""
Graceful Degradation 테스트 공통 fixtures 및 Mock 클래스.

분리된 테스트 파일들이 사용하는 공통 설정 및 mock 객체.

Refactored to use Factory Pattern (Phase 3):
- MockRedisClient → factories.MockRedisClient
- MockPipeline → factories.MockPipeline
- MockDistributedLock → factories.MockDistributedLock
"""

import gc
import shutil
import tempfile
import time
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
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    # Close disk buffer before temp dir is removed (Windows file locking)
    try:
        from selfhealing.audit.persistence.disk_buffer import reset_disk_buffer

        reset_disk_buffer()
    except Exception:
        pass
    # Force GC to close file handles held by fallback chain objects (Windows)
    gc.collect()
    # Retry cleanup to handle lingering file locks on Windows
    for attempt in range(3):
        try:
            shutil.rmtree(tmpdir, ignore_errors=False)
            break
        except PermissionError:
            gc.collect()
            time.sleep(0.1 * (attempt + 1))
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_entry():
    """Create sample log entry."""
    return {
        "event": "test_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"key": "value"},
    }
