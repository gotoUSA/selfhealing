"""
Mock Redis Client for Testing.

두 개의 conftest.py에서 중복 정의되었던 MockRedisClient를 통합합니다.
- tests/unit/audit/hash_chain_core/conftest.py (90줄)
- tests/unit/audit/graceful_degradation/conftest.py (90줄)

이 통합된 구현은 양쪽 기능을 모두 지원합니다.
"""

import fnmatch
import threading
from typing import Any, Dict, List, Optional


class MockPipeline:
    """
    Mock Redis Pipeline.
    
    Redis 파이프라인을 모방하여 여러 명령을 배치로 실행합니다.
    context manager 프로토콜을 지원합니다.
    """
    
    def __init__(self, redis: "MockRedisClient"):
        self._redis = redis
        self._commands: List[tuple] = []
    
    def get(self, key: str) -> "MockPipeline":
        """GET 명령 추가."""
        self._commands.append(("get", key))
        return self
    
    def set(self, key: str, value: Any, ex: int = None, nx: bool = False) -> "MockPipeline":
        """SET 명령 추가."""
        self._commands.append(("set", key, value, ex, nx))
        return self
    
    def delete(self, *keys: str) -> "MockPipeline":
        """DELETE 명령 추가."""
        decoded_keys = []
        for k in keys:
            if isinstance(k, bytes):
                decoded_keys.append(k.decode("utf-8"))
            else:
                decoded_keys.append(k)
        self._commands.append(("delete", tuple(decoded_keys)))
        return self
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs) -> "MockPipeline":
        """HSET 명령 추가."""
        self._commands.append(("hset", key, mapping or kwargs))
        return self
    
    def hget(self, key: str, field: str) -> "MockPipeline":
        """HGET 명령 추가."""
        self._commands.append(("hget", key, field))
        return self
    
    def incr(self, key: str) -> "MockPipeline":
        """INCR 명령 추가."""
        self._commands.append(("incr", key))
        return self
    
    def expire(self, key: str, seconds: int) -> "MockPipeline":
        """EXPIRE 명령 추가."""
        self._commands.append(("expire", key, seconds))
        return self
    
    def execute(self) -> List[Any]:
        """배치된 명령들을 실행하고 결과 반환."""
        results = []
        for cmd in self._commands:
            try:
                if cmd[0] == "get":
                    results.append(self._redis.get(cmd[1]))
                elif cmd[0] == "set":
                    key, value = cmd[1], cmd[2]
                    self._redis.set(key, value)
                    results.append(True)
                elif cmd[0] == "delete":
                    count = 0
                    for k in cmd[1]:
                        count += self._redis.delete(k)
                    results.append(count)
                elif cmd[0] == "hset":
                    self._redis.hset(cmd[1], cmd[2])
                    results.append(len(cmd[2]) if cmd[2] else 0)
                elif cmd[0] == "hget":
                    results.append(self._redis.hget(cmd[1], cmd[2]))
                elif cmd[0] == "incr":
                    results.append(self._redis.incr(cmd[1]))
                elif cmd[0] == "expire":
                    results.append(self._redis.expire(cmd[1], cmd[2]))
                else:
                    results.append(None)
            except Exception as e:
                results.append(e)
        self._commands = []
        return results
    
    def __enter__(self) -> "MockPipeline":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class MockDistributedLock:
    """
    Mock Distributed Lock.
    
    분산 락을 모방합니다. graceful_degradation 테스트에서 사용.
    """
    
    def __init__(self, *args, **kwargs):
        self._acquired = False
    
    def acquire(self, blocking: bool = True) -> bool:
        """락 획득."""
        self._acquired = True
        return True
    
    def release(self) -> None:
        """락 해제."""
        self._acquired = False
    
    def __enter__(self) -> "MockDistributedLock":
        self.acquire()
        return self
    
    def __exit__(self, *args) -> None:
        self.release()


class MockRedisClient:
    """
    테스트용 통합 Mock Redis 클라이언트.
    
    hash_chain_core와 graceful_degradation의 MockRedisClient를 통합합니다.
    
    Features:
        - 기본 GET/SET/DELETE 지원
        - Hash 명령 (HGET, HSET, HGETALL)
        - List 명령 (LPUSH, LTRIM)
        - INCR/DECR
        - Pipeline 지원
        - 실패 모드 (should_fail)
        - Thread-safe 연산
    
    Usage:
        # 정상 모드
        redis = MockRedisClient()
        redis.set("key", "value")
        assert redis.get("key") == b"value"
        
        # 실패 모드
        failing_redis = MockRedisClient(should_fail=True)
        # 모든 연산이 ConnectionError 발생
    """
    
    def __init__(self, should_fail: bool = False):
        """
        Mock Redis 클라이언트 초기화.
        
        Args:
            should_fail: True면 모든 연산에서 ConnectionError 발생
        """
        self._data: Dict[str, Any] = {}
        self._hashes: Dict[str, Dict[str, Any]] = {}
        self._lists: Dict[str, List[str]] = {}
        self._should_fail = should_fail
        self._lock = threading.Lock()
    
    def set_should_fail(self, should_fail: bool) -> None:
        """실패 모드 설정."""
        self._should_fail = should_fail
    
    def _check_failure(self) -> None:
        """실패 모드 확인 및 예외 발생."""
        if self._should_fail:
            raise ConnectionError("Redis connection failed")
    
    # =========================================================================
    # String 명령
    # =========================================================================
    
    def get(self, key: str) -> Optional[bytes]:
        """GET 명령."""
        self._check_failure()
        value = self._data.get(key)
        if value is not None:
            return str(value).encode() if not isinstance(value, bytes) else value
        return None
    
    def set(
        self,
        key: str,
        value: Any,
        nx: bool = False,
        ex: int = None,
        px: int = None,
    ) -> bool:
        """
        SET 명령.
        
        Args:
            key: 키
            value: 값
            nx: True면 키가 없을 때만 설정
            ex: 만료 시간 (초)
            px: 만료 시간 (밀리초)
        """
        self._check_failure()
        with self._lock:
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True
    
    def delete(self, *keys: str) -> int:
        """DELETE 명령."""
        self._check_failure()
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
            if key in self._hashes:
                del self._hashes[key]
                count += 1
            if key in self._lists:
                del self._lists[key]
                count += 1
        return count
    
    def keys(self, pattern: str) -> List[bytes]:
        """KEYS 명령 (패턴 매칭)."""
        self._check_failure()
        matching = [
            k.encode() if isinstance(k, str) else k
            for k in self._data.keys()
            if fnmatch.fnmatch(k, pattern)
        ]
        return matching
    
    def incr(self, key: str) -> int:
        """INCR 명령."""
        self._check_failure()
        with self._lock:
            current = int(self._data.get(key, 0))
            new_value = current + 1
            self._data[key] = new_value
            return new_value
    
    def decr(self, key: str) -> int:
        """DECR 명령."""
        self._check_failure()
        with self._lock:
            current = int(self._data.get(key, 0))
            new_value = current - 1
            self._data[key] = new_value
            return new_value
    
    def expire(self, key: str, seconds: int) -> int:
        """EXPIRE 명령 (Mock: 실제 만료 없음)."""
        self._check_failure()
        return 1 if key in self._data or key in self._hashes else 0
    
    def exists(self, *keys: str) -> int:
        """EXISTS 명령."""
        self._check_failure()
        count = 0
        for key in keys:
            if key in self._data or key in self._hashes or key in self._lists:
                count += 1
        return count
    
    # =========================================================================
    # Hash 명령
    # =========================================================================
    
    def hget(self, key: str, field: str) -> Optional[bytes]:
        """HGET 명령."""
        self._check_failure()
        hash_data = self._hashes.get(key, {})
        value = hash_data.get(field)
        if value is not None:
            return str(value).encode() if not isinstance(value, bytes) else value
        return None
    
    def hset(self, key: str, mapping: Dict[str, Any] = None, **kwargs) -> int:
        """HSET 명령."""
        self._check_failure()
        if mapping is None:
            mapping = kwargs
        with self._lock:
            if key not in self._hashes:
                self._hashes[key] = {}
            self._hashes[key].update({str(k): str(v) for k, v in mapping.items()})
            return len(mapping)
    
    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        """HGETALL 명령."""
        self._check_failure()
        hash_data = self._hashes.get(key, {})
        return {
            k.encode() if isinstance(k, str) else k: 
            str(v).encode() if not isinstance(v, bytes) else v
            for k, v in hash_data.items()
        }
    
    def hdel(self, key: str, *fields: str) -> int:
        """HDEL 명령."""
        self._check_failure()
        if key not in self._hashes:
            return 0
        count = 0
        for field in fields:
            if field in self._hashes[key]:
                del self._hashes[key][field]
                count += 1
        return count
    
    # =========================================================================
    # List 명령
    # =========================================================================
    
    def lpush(self, key: str, *values: str) -> int:
        """LPUSH 명령."""
        self._check_failure()
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].insert(0, v)
        return len(self._lists[key])
    
    def rpush(self, key: str, *values: str) -> int:
        """RPUSH 명령."""
        self._check_failure()
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].append(v)
        return len(self._lists[key])
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        """LTRIM 명령."""
        self._check_failure()
        if key in self._lists:
            self._lists[key] = self._lists[key][start:end + 1]
        return True
    
    def lrange(self, key: str, start: int, end: int) -> List[bytes]:
        """LRANGE 명령."""
        self._check_failure()
        if key not in self._lists:
            return []
        if end == -1:
            end = len(self._lists[key])
        else:
            end = end + 1
        return [v.encode() if isinstance(v, str) else v for v in self._lists[key][start:end]]
    
    def llen(self, key: str) -> int:
        """LLEN 명령."""
        self._check_failure()
        return len(self._lists.get(key, []))
    
    # =========================================================================
    # Pipeline
    # =========================================================================
    
    def pipeline(self, transaction: bool = True) -> MockPipeline:
        """Pipeline 생성."""
        return MockPipeline(self)
    
    # =========================================================================
    # Connection
    # =========================================================================
    
    def ping(self) -> bool:
        """PING 명령."""
        self._check_failure()
        return True
    
    def flushdb(self) -> bool:
        """FLUSHDB 명령."""
        self._check_failure()
        self._data.clear()
        self._hashes.clear()
        self._lists.clear()
        return True
    
    def close(self) -> None:
        """연결 종료 (Mock: 아무것도 안함)."""
        pass
    
    # =========================================================================
    # Pub/Sub (Mock)
    # =========================================================================
    
    def publish(self, channel: str, message: str) -> int:
        """PUBLISH 명령 (Mock: 수신자 0)."""
        self._check_failure()
        return 0
    
    # =========================================================================
    # Utility
    # =========================================================================
    
    def clear(self) -> None:
        """모든 데이터 초기화."""
        self._data.clear()
        self._hashes.clear()
        self._lists.clear()
