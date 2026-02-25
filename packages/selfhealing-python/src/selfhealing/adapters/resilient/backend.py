"""
Resilient Storage Backend Implementation.

Redis-First + Graceful Degradation + WAL architecture
for zero data loss guarantees.

Key Principles:
- WAL-First: In degraded mode, WAL is written BEFORE memory (server crash safe)
- Zero Data Loss: WAL enables recovery after server restart
- Redis-Only: Normal mode uses Redis exclusively for simplicity
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()

# Windows 한국어 로케일에서 OS 에러 메시지가 cp949로 나와 로그가 깨지는 문제 방지.
# errno 기반 영어 메시지로 통일.
_ERRNO_MESSAGES: dict[int, str] = {
    10061: "Connection refused",  # WSAECONNREFUSED
    10060: "Connection timed out",  # WSAETIMEDOUT
    10065: "No route to host",  # WSAEHOSTUNREACH
    111: "Connection refused",  # ECONNREFUSED (Linux)
    110: "Connection timed out",  # ETIMEDOUT (Linux)
}


def _safe_error_message(e: Exception) -> str:
    """Return an ASCII-safe error description (avoids cp949 garbled text on Windows)."""
    if isinstance(e, OSError) and e.errno in _ERRNO_MESSAGES:
        return f"{type(e).__name__}: {_ERRNO_MESSAGES[e.errno]} (errno={e.errno})"
    try:
        msg = str(e)
        msg.encode("ascii")
        return msg
    except (UnicodeEncodeError, UnicodeDecodeError):
        return f"{type(e).__name__}(errno={getattr(e, 'errno', 'N/A')})"


class ResilientStorageMode(str, Enum):
    """Storage operation mode."""

    REDIS = "redis"  # Normal mode - Redis only
    DEGRADED = "degraded"  # Fallback mode - Memory + WAL
    RECOVERING = "recovering"  # Transitioning back to Redis


@dataclass
class ResilientStorageConfig:
    """Resilient Storage configuration."""

    redis_url: str = "redis://localhost:6379/0"
    wal_dir: str = "/var/log/selfhealing/wal"
    health_check_interval: float = 5.0
    recovery_jitter_max: float = 5.0
    key_prefix: str = "selfhealing:"

    # Allow memory-only mode (for testing/development)
    allow_memory_only: bool = False

    # Dynamic prefix 지원: TestModeContext 활성화 시 xtest: 프리픽스 자동 적용
    use_dynamic_prefix: bool = True


class ResilientStorageBackend:
    """
    Redis-First + Graceful Degradation + WAL.

    Zero data loss storage backend that:
    - Uses Redis in normal mode
    - Falls back to Memory + WAL on Redis failure
    - Recovers from WAL on server restart
    - Syncs WAL to Redis on recovery

    Core Invariant:
        WAL-First Write Protocol in degraded mode:
        1. WAL.write() + fsync() - disk persisted first
        2. Memory[key] = value  - then memory

        This ensures server crash at any point is recoverable.
    """

    def __init__(self, config: ResilientStorageConfig | None = None):
        """
        Initialize Resilient Storage Backend.

        Args:
            config: Storage configuration. Uses defaults if not provided.
        """
        self.config = config or ResilientStorageConfig()
        self._mode = ResilientStorageMode.REDIS
        self._lock = threading.RLock()

        # Redis client
        self._redis: Any | None = None
        self._redis_initialized = False

        # WAL for disk-based recovery queue
        self._wal: Any | None = None
        self._wal_initialized = False

        # Memory fallback
        self._memory: dict[str, Any] = {}

        # Shadow Logger for forensic logging
        self._shadow: Any | None = None

        # Health checker
        self._health_checker: Any | None = None

        # Last processed WAL sequence
        self._last_processed_wal_seq: int = 0

        # Initialize components
        self._init_components()

        # Recover from WAL on startup
        self._recover_from_wal_on_startup()

    def _init_components(self) -> None:
        """Initialize all components (Redis, WAL, ShadowLogger)."""
        self._init_redis()
        self._init_wal()
        self._init_shadow_logger()
        self._init_health_checker()

    def _get_full_key(self, key: str) -> str:
        """
        동적 프리픽스를 적용한 전체 키 반환.

        TestModeContext가 활성화된 경우 xtest: 프리픽스가 추가되어
        운영 데이터와 테스트 데이터가 분리됩니다.

        Args:
            key: 원본 키

        Returns:
            프리픽스가 적용된 전체 키
            - 운영 모드: "selfhealing:dlq:pending"
            - 합성 모드: "xtest:selfhealing:dlq:pending"
        """
        if self.config.use_dynamic_prefix:
            from selfhealing.settings.namespace import get_effective_key_prefix

            prefix = get_effective_key_prefix()
            return f"{prefix}{key}"

        return f"{self.config.key_prefix}{key}"

    def _init_redis(self) -> None:
        """Initialize Redis connection (failure is acceptable)."""
        try:
            from selfhealing.adapters.cache import RedisCacheAdapter

            self._redis = RedisCacheAdapter(
                url=self.config.redis_url,
                key_prefix="",  # We handle prefix ourselves
            )

            # Test connection
            self._redis._redis.ping()
            self._redis_initialized = True
            logger.info("resilient_storage.redis_connected")

        except Exception as e:
            err_msg = _safe_error_message(e)
            logger.warning(
                "resilient_storage.redis_init_failed",
                error=err_msg,
            )
            self._mode = ResilientStorageMode.DEGRADED

            if self._shadow:
                self._shadow.record_sync_failure(
                    service_name="redis_init",
                    intended_state="connected",
                    error=e,
                    adapter_type="redis",
                )

            if not self.config.allow_memory_only:
                logger.critical(
                    "resilient_storage.degraded_mode_entered",
                    reason="redis_unavailable",
                    fallback="memory_wal",
                )

    def _init_wal(self) -> None:
        """Initialize Write-Ahead Log."""
        try:
            from selfhealing.audit.wal import WALConfig, WriteAheadLog

            # Ensure WAL directory exists
            os.makedirs(self.config.wal_dir, exist_ok=True)

            wal_config = WALConfig(
                wal_dir=self.config.wal_dir,
                sync_on_write=True,  # fsync guarantee - server crash safe
                file_prefix="resilient_storage",
            )

            self._wal = WriteAheadLog(config=wal_config)
            self._wal_initialized = True
            logger.debug("resilient_storage.wal_initialized")

        except Exception as e:
            logger.exception(
                "resilient_storage.wal_init_failed",
                _safe_error_message=_safe_error_message(e),
            )
            # WAL failure is serious but we continue with memory-only
            self._wal_initialized = False

    def _init_shadow_logger(self) -> None:
        """Initialize Shadow Logger for forensic logging."""
        try:
            from selfhealing.adapters.memory.shadow_logger import get_shadow_logger

            self._shadow = get_shadow_logger()
        except Exception:
            pass  # Shadow logger is optional

    def _init_health_checker(self) -> None:
        """Initialize Redis Health Checker."""
        try:
            from selfhealing.api.django.rate_limit import get_redis_health_checker

            self._health_checker = get_redis_health_checker()
        except Exception:
            pass  # Health checker is optional

    def _recover_from_wal_on_startup(self) -> None:
        """
        Recover unprocessed WAL entries on server startup.

        This is the key to zero data loss:
        - Server crashed in degraded mode? WAL has all changes.
        - WAL entries are replayed to Redis on startup.
        """
        if not self._wal_initialized or not self._wal:
            return

        try:
            stats = self._wal.get_stats()
            if stats.total_entries == 0:
                return  # Nothing to recover

            logger.info(
                "resilient_storage.found_wal_entries_check",
                stats=stats.last_sequence,
            )

            # If Redis is unavailable, defer recovery
            if self._mode == ResilientStorageMode.DEGRADED:
                logger.warning("resilient_storage.redis_unavailable_wal_recovery")
                return

            # Replay WAL entries to Redis
            entries = self._wal.recover_unprocessed(last_processed_seq=self._last_processed_wal_seq)

            recovered_count = 0
            for entry in entries:
                try:
                    self._replay_wal_entry(entry)
                    self._last_processed_wal_seq = entry.sequence
                    recovered_count += 1
                except Exception as e:
                    logger.exception(
                        "resilient_storage.wal_replay_failed_seq",
                        wal_sequence=entry.sequence,
                        _safe_error_message=_safe_error_message(e),
                    )
                    # Continue with next entry

            if recovered_count > 0:
                logger.info(
                    "resilient_storage.recovered_entries_wal",
                    recovered_count=recovered_count,
                )

                # Cleanup processed WAL entries
                self._wal.cleanup_processed(self._last_processed_wal_seq)

        except Exception as e:
            logger.exception(
                "resilient_storage.wal_recovery_failed",
                _safe_error_message=_safe_error_message(e),
            )
            # Recovery failure doesn't prevent server from starting

    def _replay_wal_entry(self, entry: Any) -> None:
        """Replay a single WAL entry to Redis."""
        if not self._redis or not self._redis_initialized:
            return

        operation = entry.data.get("operation")
        key = entry.data.get("key")
        value = entry.data.get("value")

        if operation == "set":
            self._redis._redis.set(key, self._redis._serialize(value))
        elif operation == "hset":
            # Ensure all values are strings for Redis hash
            mapping = {str(k): str(v) for k, v in value.items()}
            self._redis._redis.hset(key, mapping=mapping)
        elif operation == "delete":
            self._redis._redis.delete(key)
        elif operation == "hdel":
            field = entry.data.get("field")
            if field:
                self._redis._redis.hdel(key, field)
        else:
            logger.warning(
                "resilient_storage.unknown_wal_operation",
                operation=operation,
            )

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def mode(self) -> ResilientStorageMode:
        """Get current storage mode."""
        return self._mode

    @property
    def is_degraded(self) -> bool:
        """Check if operating in degraded mode."""
        return self._mode != ResilientStorageMode.REDIS

    @property
    def is_redis_available(self) -> bool:
        """Check if Redis is available."""
        return self._redis_initialized and self._mode == ResilientStorageMode.REDIS

    # =========================================================================
    # Core Operations
    # =========================================================================

    def get(self, key: str) -> Any | None:
        """
        Get value by key.

        Args:
            key: Key without prefix (prefix is auto-added)

        Returns:
            Value if exists, None otherwise
        """
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                return self._redis.get(full_key)
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key)
        else:
            return self._memory.get(key)

    def set(self, key: str, value: Any) -> bool:
        """
        Set value.

        In degraded mode, uses WAL-First protocol:
        1. Write to WAL (disk) with fsync
        2. Write to Memory

        Args:
            key: Key without prefix
            value: Value to store

        Returns:
            True on success
        """
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                self._redis.set(full_key, value)
                return True
            except Exception as e:
                self._switch_to_degraded()
                return self._set_degraded(key, full_key, value, e)
        else:
            return self._set_degraded(key, full_key, value, None)

    def _set_degraded(self, key: str, full_key: str, value: Any, error: Exception | None) -> bool:
        """
        Set in degraded mode using WAL-First protocol.

        CRITICAL: WAL must be written BEFORE memory!
        This ensures server crash at any point is recoverable.
        """
        # 1. WAL first (disk with fsync)
        if self._wal and self._wal_initialized:
            self._wal.write(
                {
                    "operation": "set",
                    "key": full_key,
                    "value": value,
                    "timestamp": time.time(),
                }
            )

        # 2. Memory second
        self._memory[key] = value

        # 3. Forensic logging (optional)
        if error and self._shadow:
            self._shadow.record_sync_failure(
                service_name=key,
                intended_state=str(value),
                error=error,
                adapter_type="redis",
            )

        return True

    def delete(self, key: str) -> bool:
        """Delete value by key."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                self._redis.delete(full_key)
                return True
            except Exception:
                self._switch_to_degraded()

        # Degraded mode: WAL first, memory second
        if self._wal and self._wal_initialized:
            self._wal.write(
                {
                    "operation": "delete",
                    "key": full_key,
                    "timestamp": time.time(),
                }
            )

        self._memory.pop(key, None)
        return True

    # =========================================================================
    # Hash Operations (for CB, DLQ)
    # =========================================================================

    def hget(self, key: str, field: str) -> Any | None:
        """Get hash field value."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                result = self._redis._redis.hget(full_key, field)
                if result is None:
                    return None
                if isinstance(result, bytes):
                    return result.decode("utf-8")
                return result
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key, {}).get(field)
        else:
            return self._memory.get(key, {}).get(field)

    def hset(self, key: str, mapping: dict[str, Any]) -> bool:
        """Set hash fields."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                # Convert all values to strings for Redis
                str_mapping = {str(k): str(v) for k, v in mapping.items()}
                self._redis._redis.hset(full_key, mapping=str_mapping)
                return True
            except Exception as e:
                self._switch_to_degraded()
                return self._hset_degraded(key, full_key, mapping, e)
        else:
            return self._hset_degraded(key, full_key, mapping, None)

    def _hset_degraded(
        self,
        key: str,
        full_key: str,
        mapping: dict[str, Any],
        error: Exception | None,
    ) -> bool:
        """Hash set in degraded mode using WAL-First protocol."""
        # 1. WAL first
        if self._wal and self._wal_initialized:
            self._wal.write(
                {
                    "operation": "hset",
                    "key": full_key,
                    "value": mapping,
                    "timestamp": time.time(),
                }
            )

        # 2. Memory second
        if key not in self._memory:
            self._memory[key] = {}
        self._memory[key].update(mapping)

        return True

    def hgetall(self, key: str) -> dict[str, Any]:
        """Get all hash fields."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                result = self._redis._redis.hgetall(full_key)
                if not result:
                    return {}
                # Decode bytes
                return {
                    (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in result.items()
                }
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key, {})
        else:
            return self._memory.get(key, {})

    def hdel(self, key: str, field: str) -> bool:
        """Delete hash field."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                self._redis._redis.hdel(full_key, field)
                return True
            except Exception:
                self._switch_to_degraded()

        # Degraded mode
        if self._wal and self._wal_initialized:
            self._wal.write(
                {
                    "operation": "hdel",
                    "key": full_key,
                    "field": field,
                    "timestamp": time.time(),
                }
            )

        if key in self._memory and isinstance(self._memory[key], dict):
            self._memory[key].pop(field, None)

        return True

    # =========================================================================
    # List Operations (for history)
    # =========================================================================

    def lpush(self, key: str, *values: Any) -> int:
        """Push values to list head."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                import json

                serialized = [json.dumps(v) for v in values]
                return self._redis._redis.lpush(full_key, *serialized)
            except Exception:
                self._switch_to_degraded()

        # Degraded mode
        if key not in self._memory:
            self._memory[key] = []
        for v in reversed(values):
            self._memory[key].insert(0, v)
        return len(self._memory[key])

    def lrange(self, key: str, start: int, end: int) -> list[Any]:
        """Get list range."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                import json

                result = self._redis._redis.lrange(full_key, start, end)
                return [json.loads(v.decode() if isinstance(v, bytes) else v) for v in result]
            except Exception:
                self._switch_to_degraded()
                return self._memory.get(key, [])[start : end + 1 if end >= 0 else None]
        else:
            return self._memory.get(key, [])[start : end + 1 if end >= 0 else None]

    def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim list to specified range."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                self._redis._redis.ltrim(full_key, start, end)
                return True
            except Exception:
                self._switch_to_degraded()

        # Degraded mode
        if key in self._memory:
            self._memory[key] = self._memory[key][start : end + 1 if end >= 0 else None]
        return True

    # =========================================================================
    # Sorted Set Operations (for DLQ pending queue)
    # =========================================================================

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """Add members to sorted set with scores."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                return self._redis._redis.zadd(full_key, mapping)
            except Exception:
                self._switch_to_degraded()

        # Degraded mode (simple list simulation)
        if key not in self._memory:
            self._memory[key] = []
        for member, score in mapping.items():
            self._memory[key].append({"member": member, "score": score})
        self._memory[key].sort(key=lambda x: x["score"])
        return len(mapping)

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        """Get sorted set range by index."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                result = self._redis._redis.zrange(full_key, start, end)
                return [v.decode() if isinstance(v, bytes) else v for v in result]
            except Exception:
                self._switch_to_degraded()

        # Degraded mode
        items = self._memory.get(key, [])
        end_idx = end + 1 if end >= 0 else None
        return [item["member"] for item in items[start:end_idx]]

    def zrem(self, key: str, *members: str) -> int:
        """Remove members from sorted set."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                return self._redis._redis.zrem(full_key, *members)
            except Exception:
                self._switch_to_degraded()

        # Degraded mode
        if key in self._memory:
            before = len(self._memory[key])
            self._memory[key] = [item for item in self._memory[key] if item["member"] not in members]
            return before - len(self._memory[key])
        return 0

    def zcard(self, key: str) -> int:
        """Get sorted set cardinality."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                return self._redis._redis.zcard(full_key)
            except Exception:
                self._switch_to_degraded()

        return len(self._memory.get(key, []))

    # =========================================================================
    # Atomic Operations
    # =========================================================================

    def incr(self, key: str) -> int:
        """Atomically increment counter."""
        full_key = self._get_full_key(key)

        if self._mode == ResilientStorageMode.REDIS and self._redis:
            try:
                return self._redis._redis.incr(full_key)
            except Exception:
                self._switch_to_degraded()

        # Degraded mode (not truly atomic but best effort)
        with self._lock:
            current = self._memory.get(key, 0)
            new_value = int(current) + 1
            self._memory[key] = new_value
            return new_value

    # =========================================================================
    # Mode Management
    # =========================================================================

    def _switch_to_degraded(self) -> None:
        """Switch to degraded mode on Redis failure."""
        with self._lock:
            if self._mode != ResilientStorageMode.DEGRADED:
                self._mode = ResilientStorageMode.DEGRADED
                logger.critical("resilient_storage.switched_degraded_mode_using")

    def check_and_recover(self) -> bool:
        """
        Check Redis health and recover if available.

        Called periodically to attempt recovery from degraded mode.

        Returns:
            True if recovered to Redis mode
        """
        if self._mode != ResilientStorageMode.DEGRADED:
            return False

        # Check health
        if self._health_checker:
            if not self._health_checker.check_health():
                return False
        else:
            # Manual ping check
            try:
                if self._redis:
                    self._redis._redis.ping()
                else:
                    return False
            except Exception:
                return False

        # Apply jitter to prevent thundering herd
        jitter = random.uniform(0, self.config.recovery_jitter_max)
        time.sleep(jitter)

        return self._do_recovery()

    def _do_recovery(self) -> bool:
        """
        Perform recovery from degraded mode to Redis mode.

        Steps:
        1. Set mode to RECOVERING
        2. Replay WAL entries to Redis
        3. Use DriftReconciler for conflict resolution
        4. Sync memory to Redis
        5. Clear memory and WAL
        6. Set mode to REDIS
        """
        try:
            with self._lock:
                self._mode = ResilientStorageMode.RECOVERING

            # 1. Replay WAL to Redis
            if self._wal and self._wal_initialized:
                entries = self._wal.recover_unprocessed(last_processed_seq=self._last_processed_wal_seq)

                for entry in entries:
                    try:
                        self._replay_wal_entry(entry)
                        self._last_processed_wal_seq = entry.sequence
                    except Exception as e:
                        logger.exception(
                            "resilient_storage.wal_replay_error",
                            _safe_error_message=_safe_error_message(e),
                        )

            # 2. Sync remaining memory to Redis (with conflict resolution)
            self._sync_memory_to_redis()

            # 3. Cleanup
            self._memory.clear()
            if self._wal:
                self._wal.cleanup_processed(self._last_processed_wal_seq)

            with self._lock:
                self._mode = ResilientStorageMode.REDIS

            logger.info("resilient_storage.redis_mode_recovered")
            return True

        except Exception as e:
            logger.exception(
                "watchdog.recovery_failed",
                _safe_error_message=_safe_error_message(e),
            )
            with self._lock:
                self._mode = ResilientStorageMode.DEGRADED
            return False

    def _sync_memory_to_redis(self) -> None:
        """Sync in-memory data to Redis with drift reconciliation."""
        if not self._redis or not self._redis_initialized:
            return

        try:
            from selfhealing.adapters.memory.drift_reconciliation import (
                get_drift_reconciler,
            )

            reconciler = get_drift_reconciler()
        except Exception:
            reconciler = None

        for key, value in self._memory.items():
            full_key = self._get_full_key(key)

            try:
                if isinstance(value, dict):
                    # Check for drift if reconciler available
                    if reconciler and key.startswith("cb:"):
                        # Circuit breaker state - use reconciliation
                        redis_data = self._redis._redis.hgetall(full_key)
                        if redis_data:
                            redis_state = redis_data.get(b"state", b"closed").decode()
                            memory_state = value.get("state", "closed")

                            # Most Restrictive Wins
                            winner_state, _ = reconciler.reconcile(
                                service_name=key,
                                l1_state=memory_state,
                                l2_state=redis_state,
                            )
                            value["state"] = winner_state

                    str_mapping = {str(k): str(v) for k, v in value.items()}
                    self._redis._redis.hset(full_key, mapping=str_mapping)
                else:
                    self._redis.set(full_key, value)

            except Exception as e:
                logger.exception(
                    "resilient_storage.sync_error_key",
                    redis_key=key,
                    _safe_error_message=_safe_error_message(e),
                )

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def flush_wal(self) -> None:
        """Flush WAL buffer to disk."""
        if self._wal and self._wal_initialized:
            self._wal.flush()

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        stats = {
            "mode": self._mode.value,
            "redis_available": self._redis_initialized,
            "wal_initialized": self._wal_initialized,
            "memory_keys": len(self._memory),
        }

        if self._wal:
            try:
                wal_stats = self._wal.get_stats()
                stats["wal_entries"] = wal_stats.total_entries
                stats["wal_last_sequence"] = wal_stats.last_sequence
            except Exception:
                pass

        return stats

    def close(self) -> None:
        """Close storage backend and release resources."""
        if self._wal:
            self._wal.close()


# Singleton instance
_storage_backend: ResilientStorageBackend | None = None
_storage_lock = threading.Lock()


def get_storage_backend(
    config: ResilientStorageConfig | None = None,
) -> ResilientStorageBackend:
    """
    Get singleton storage backend instance.

    Args:
        config: Configuration (only used on first call)

    Returns:
        ResilientStorageBackend singleton instance
    """
    global _storage_backend

    if _storage_backend is None:
        with _storage_lock:
            if _storage_backend is None:
                _storage_backend = ResilientStorageBackend(config)

    return _storage_backend


def reset_storage_backend() -> None:
    """Reset singleton for testing."""
    global _storage_backend

    with _storage_lock:
        if _storage_backend:
            _storage_backend.close()
        _storage_backend = None


# 하위 호환 alias (deprecated)
StorageMode = ResilientStorageMode
