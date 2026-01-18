"""
Graceful Degradation 테스트 공통 fixtures 및 Mock 클래스.

분리된 테스트 파일들이 사용하는 공통 설정 및 mock 객체.
"""

import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# =============================================================================
# Mock Redis Client
# =============================================================================

class MockRedisClient:
    """Mock Redis client for testing."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
        self._incr_counter = 0
    
    def set_should_fail(self, should_fail: bool) -> None:
        """Set failure mode."""
        self._should_fail = should_fail
    
    def get(self, key: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        value = self._data.get(key)
        return str(value).encode() if value is not None else None
    
    def set(self, key: str, value: Any, nx: bool = False, ex: int = None) -> bool:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True
    
    def delete(self, *keys: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
            if key in self._hashes:
                del self._hashes[key]
                count += 1
        return count
    
    def incr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            val = int(self._data.get(key, 0)) + 1
            self._data[key] = val
            return val
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            self._hashes[key] = {}
        if mapping:
            self._hashes[key].update(mapping)
        if kwargs:
            self._hashes[key].update(kwargs)
        return 1
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            return None
        value = self._hashes[key].get(field)
        if value is None:
            return None
        return str(value).encode() if not isinstance(value, bytes) else value
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            return {}
        return {
            k.encode() if isinstance(k, str) else k: 
            str(v).encode() if not isinstance(v, bytes) else v
            for k, v in self._hashes[key].items()
        }
    
    def expire(self, key: str, seconds: int) -> bool:
        return True
    
    def lpush(self, key: str, *values: str) -> int:
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].insert(0, v)
        return len(self._lists[key])
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self._lists:
            self._lists[key] = self._lists[key][start:end + 1]
        return True
    
    def pipeline(self, transaction: bool = True) -> "MockPipeline":
        return MockPipeline(self)
    
    def ping(self) -> bool:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        return True


class MockPipeline:
    """Mock Redis pipeline."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands: List[tuple] = []
    
    def set(self, key: str, value: Any) -> "MockPipeline":
        self._commands.append(("set", key, value))
        return self
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> "MockPipeline":
        self._commands.append(("hset", key, mapping or kwargs))
        return self
    
    def execute(self) -> List[Any]:
        results = []
        for cmd in self._commands:
            if cmd[0] == "set":
                self._redis.set(cmd[1], cmd[2])
                results.append(True)
            elif cmd[0] == "hset":
                self._redis.hset(cmd[1], cmd[2])
                results.append(1)
        return results
    
    def __enter__(self) -> "MockPipeline":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class MockDistributedLock:
    """Mock distributed lock."""
    
    def __init__(self, *args, **kwargs):
        self._acquired = False
    
    def acquire(self, blocking: bool = True) -> bool:
        self._acquired = True
        return True
    
    def release(self) -> None:
        self._acquired = False
    
    def __enter__(self) -> "MockDistributedLock":
        self.acquire()
        return self
    
    def __exit__(self, *args) -> None:
        self.release()


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
