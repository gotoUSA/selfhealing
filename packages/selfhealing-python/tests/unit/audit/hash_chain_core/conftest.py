"""
Hash Chain Core 테스트 공통 설정.

이 패키지의 모든 테스트에서 사용하는 Mock 클래스 및 fixtures.
"""

import json
import threading
import tempfile
from typing import Any, Dict, List, Optional

import pytest


# =============================================================================
# Mock Redis Client
# =============================================================================

class MockRedisClient:
    """테스트용 Mock Redis 클라이언트."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        value = self._data.get(key)
        if value is not None:
            return str(value).encode() if not isinstance(value, bytes) else value
        return None
    
    def set(self, key: str, value: Any, nx: bool = False, ex: int = None, px: int = None) -> bool:
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
    
    def keys(self, pattern: str) -> List[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        import fnmatch
        matching = [k.encode() for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]
        return matching
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        if value is not None:
            return str(value).encode()
        return None
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if mapping is None:
            mapping = kwargs
        with self._lock:
            if key not in self._hashes:
                self._hashes[key] = {}
            self._hashes[key].update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        return {k.encode(): v.encode() for k, v in hash_data.items()}
    
    def incr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            current = int(self._data.get(key, 0))
            new_value = current + 1
            self._data[key] = new_value
            return new_value
    
    def expire(self, key: str, seconds: int) -> int:
        return 1 if key in self._data or key in self._hashes else 0
    
    def pipeline(self):
        return MockPipeline(self)


class MockPipeline:
    """Mock Redis Pipeline."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands = []
    
    def get(self, key: str):
        self._commands.append(("get", key))
        return self
    
    def set(self, key: str, value: Any, ex: int = None, nx: bool = False):
        self._commands.append(("set", key, value, ex, nx))
        return self
    
    def delete(self, *keys: str):
        decoded_keys = []
        for k in keys:
            if isinstance(k, bytes):
                decoded_keys.append(k.decode("utf-8"))
            else:
                decoded_keys.append(k)
        self._commands.append(("delete", tuple(decoded_keys)))
        return self
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs):
        self._commands.append(("hset", key, mapping or kwargs))
        return self
    
    def execute(self):
        results = []
        for cmd in self._commands:
            if cmd[0] == "get":
                results.append(self._redis.get(cmd[1]))
            elif cmd[0] == "set":
                key, value = cmd[1], cmd[2]
                self._redis.set(key, value)
                results.append(True)
            elif cmd[0] == "delete":
                for k in cmd[1]:
                    self._redis.delete(k)
                results.append(len(cmd[1]))
            elif cmd[0] == "hset":
                self._redis.hset(cmd[1], cmd[2])
                results.append(len(cmd[2]))
        self._commands = []
        return results


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_redis():
    """테스트용 Mock Redis 클라이언트."""
    return MockRedisClient()


@pytest.fixture
def failing_redis():
    """실패하는 Mock Redis 클라이언트."""
    return MockRedisClient(should_fail=True)


@pytest.fixture
def temp_log_dir(tmp_path):
    """임시 로그 디렉토리."""
    log_dir = tmp_path / "audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir
