"""
Hash Chain Performance Test Fixtures.

Provides MockRedisClient and MockPipeline for testing.
Uses lazy imports to avoid Prometheus registry conflicts.
"""

import pytest
import threading
from typing import Any, Dict, List, Optional


class MockRedisClient:
    """Mock Redis client for testing."""
    
    def __init__(self, should_fail: bool = False):
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        self._scripts: Dict[str, str] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
        self._script_counter = 0
    
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
    
    def decr(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        with self._lock:
            val = int(self._data.get(key, 0)) - 1
            self._data[key] = val
            return val
    
    def hset(self, key: str, mapping: Dict = None, **kwargs) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        if key not in self._hashes:
            self._hashes[key] = {}
        data = mapping or kwargs
        self._hashes[key].update(data)
        return len(data)
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        return str(value).encode() if value is not None else None
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        hash_data = self._hashes.get(key, {})
        return {
            k.encode() if isinstance(k, str) else k: 
            str(v).encode() if not isinstance(v, bytes) else v
            for k, v in hash_data.items()
        }
    
    def exists(self, key: str) -> int:
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        return 1 if (key in self._data or key in self._hashes) else 0
    
    def expire(self, key: str, seconds: int) -> bool:
        return True  # Simplified for testing
    
    def script_load(self, script: str) -> str:
        """Load script and return SHA."""
        self._script_counter += 1
        sha = f"sha_{self._script_counter}"
        self._scripts[sha] = script
        return sha
    
    def eval(self, script: str, num_keys: int, *args) -> Any:
        """Simple eval implementation for testing."""
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
        
        # Simulate atomic add integrity
        if "INCR" in script and "HGET" in script:
            # Reserve sequence
            seq_key = args[0]
            new_seq = self.incr(seq_key)
            prev_hash = self.hget(args[1], "previous_hash")
            prev_hash = prev_hash.decode() if prev_hash else "GENESIS"
            return [new_seq, prev_hash]
        
        # Simulate commit
        if "EXISTS" in script and "DEL" in script:
            pending_key = args[0]
            if self.exists(pending_key):
                self.delete(pending_key)
                return {"ok": True}
            return {"err": "PENDING_NOT_FOUND"}
        
        return None
    
    def evalsha(self, sha: str, num_keys: int, *args) -> Any:
        """Eval by SHA - delegates to eval."""
        if sha in self._scripts:
            return self.eval(self._scripts[sha], num_keys, *args)
        raise Exception("NOSCRIPT")
    
    def pipeline(self, transaction: bool = True):
        """Return mock pipeline."""
        return MockPipeline(self)


class MockPipeline:
    """Mock Redis pipeline for testing."""
    
    def __init__(self, redis: MockRedisClient):
        self._redis = redis
        self._commands: List[tuple] = []
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    
    def hgetall(self, key: str):
        self._commands.append(("hgetall", key))
        return self
    
    def exists(self, key: str):
        self._commands.append(("exists", key))
        return self
    
    def incr(self, key: str):
        self._commands.append(("incr", key))
        return self
    
    def expire(self, key: str, seconds: int):
        self._commands.append(("expire", key, seconds))
        return self
    
    def get(self, key: str):
        self._commands.append(("get", key))
        return self
    
    def execute(self) -> List[Any]:
        results = []
        for cmd in self._commands:
            if cmd[0] == "hgetall":
                results.append(self._redis.hgetall(cmd[1]))
            elif cmd[0] == "exists":
                results.append(self._redis.exists(cmd[1]))
            elif cmd[0] == "incr":
                results.append(self._redis.incr(cmd[1]))
            elif cmd[0] == "get":
                results.append(self._redis.get(cmd[1]))
            else:
                results.append(None)
        return results


@pytest.fixture
def mock_redis():
    """Provide a MockRedisClient for testing."""
    return MockRedisClient()


@pytest.fixture
def mock_redis_failing():
    """Provide a failing MockRedisClient for testing error paths."""
    return MockRedisClient(should_fail=True)
