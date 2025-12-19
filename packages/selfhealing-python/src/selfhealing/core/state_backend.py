"""
State Backend Interface and Implementations.

Provides pluggable state persistence for the self-healing system.
Supports both single-server (file) and multi-server (Redis) deployments.

Configuration:
    # Django settings.py
    SELFHEALING_STATE_BACKEND = "file"  # or "redis"
    SELFHEALING_STATE_DIR = "/var/lib/selfhealing/"  # for file backend
    SELFHEALING_REDIS_URL = "redis://localhost:6379/0"  # for redis backend

    # Or environment variables
    SELFHEALING_STATE_BACKEND=redis
    SELFHEALING_REDIS_URL=redis://localhost:6379/0
"""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, TypeVar, Generic

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StateBackend(ABC, Generic[T]):
    """
    Abstract base class for state persistence backends.
    
    Implementations must be thread-safe.
    """
    
    @abstractmethod
    def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """Get state by key."""
        pass
    
    @abstractmethod
    def set(self, key: str, value: T, ttl_seconds: Optional[int] = None) -> None:
        """Set state by key with optional TTL."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete state by key. Returns True if existed."""
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if key exists."""
        pass
    
    @abstractmethod
    def get_all(self, pattern: str = "*") -> Dict[str, T]:
        """Get all states matching pattern."""
        pass


class FileStateBackend(StateBackend[Dict[str, Any]]):
    """
    File-based state backend for single-server deployments.
    
    Features:
    - JSON file storage
    - Atomic writes (temp file + rename)
    - Thread-safe
    - Survives process restarts
    
    Limitations:
    - Not shared across servers
    - No TTL support (ignored)
    
    Usage:
        backend = FileStateBackend("/var/lib/selfhealing/state")
        backend.set("system_control", {"enabled": True})
        state = backend.get("system_control")
    """
    
    def __init__(self, directory: str | Path):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        logger.info(f"[StateBackend] File backend initialized: {self._directory}")
    
    def _get_file_path(self, key: str) -> Path:
        # Sanitize key for filename
        safe_key = key.replace("/", "_").replace(":", "_")
        return self._directory / f"{safe_key}.json"
    
    def get(self, key: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        file_path = self._get_file_path(key)
        with self._lock:
            try:
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                logger.warning(f"[StateBackend] Error reading {key}: {e}")
        return default
    
    def set(self, key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        file_path = self._get_file_path(key)
        with self._lock:
            try:
                # Atomic write
                temp_file = file_path.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(value, f, indent=2, default=str)
                temp_file.replace(file_path)
            except Exception as e:
                logger.error(f"[StateBackend] Error writing {key}: {e}")
                raise
    
    def delete(self, key: str) -> bool:
        file_path = self._get_file_path(key)
        with self._lock:
            if file_path.exists():
                file_path.unlink()
                return True
        return False
    
    def exists(self, key: str) -> bool:
        return self._get_file_path(key).exists()
    
    def get_all(self, pattern: str = "*") -> Dict[str, Dict[str, Any]]:
        result = {}
        with self._lock:
            for file_path in self._directory.glob("*.json"):
                key = file_path.stem
                if pattern == "*" or pattern.replace("*", "") in key:
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            result[key] = json.load(f)
                    except Exception as e:
                        logger.warning(f"[StateBackend] Error reading {key}: {e}")
        return result


class RedisStateBackend(StateBackend[Dict[str, Any]]):
    """
    Redis-based state backend for multi-server deployments.
    
    Features:
    - Shared state across all servers
    - TTL support
    - Atomic operations
    - High availability (with Redis Sentinel/Cluster)
    
    Requirements:
    - redis package: pip install redis
    
    Usage:
        backend = RedisStateBackend("redis://localhost:6379/0")
        backend.set("system_control", {"enabled": True}, ttl_seconds=3600)
        state = backend.get("system_control")
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "selfhealing:state:",
    ):
        self._key_prefix = key_prefix
        self._redis_url = redis_url
        self._client = None
        self._lock = threading.Lock()
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        try:
            import redis
            self._client = redis.from_url(self._redis_url, decode_responses=True)
            # Test connection
            self._client.ping()
            logger.info(f"[StateBackend] Redis backend connected: {self._redis_url}")
        except ImportError:
            logger.error("[StateBackend] redis package not installed. Run: pip install redis")
            raise
        except Exception as e:
            logger.error(f"[StateBackend] Redis connection failed: {e}")
            raise
    
    def _make_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"
    
    def get(self, key: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        try:
            data = self._client.get(self._make_key(key))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"[StateBackend] Redis get error for {key}: {e}")
        return default
    
    def set(self, key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        try:
            data = json.dumps(value, default=str)
            if ttl_seconds:
                self._client.setex(self._make_key(key), ttl_seconds, data)
            else:
                self._client.set(self._make_key(key), data)
        except Exception as e:
            logger.error(f"[StateBackend] Redis set error for {key}: {e}")
            raise
    
    def delete(self, key: str) -> bool:
        try:
            return self._client.delete(self._make_key(key)) > 0
        except Exception as e:
            logger.error(f"[StateBackend] Redis delete error for {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        try:
            return self._client.exists(self._make_key(key)) > 0
        except Exception as e:
            logger.warning(f"[StateBackend] Redis exists error for {key}: {e}")
            return False
    
    def get_all(self, pattern: str = "*") -> Dict[str, Dict[str, Any]]:
        result = {}
        try:
            full_pattern = self._make_key(pattern)
            for key in self._client.scan_iter(match=full_pattern):
                short_key = key.replace(self._key_prefix, "")
                data = self._client.get(key)
                if data:
                    result[short_key] = json.loads(data)
        except Exception as e:
            logger.error(f"[StateBackend] Redis scan error: {e}")
        return result


class MemoryStateBackend(StateBackend[Dict[str, Any]]):
    """
    In-memory state backend for testing.
    
    WARNING: State is lost on process restart.
    Use only for testing.
    """
    
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        logger.info("[StateBackend] Memory backend initialized (testing only)")
    
    def get(self, key: str, default: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(key, default)
    
    def set(self, key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        with self._lock:
            self._store[key] = value
    
    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False
    
    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._store
    
    def get_all(self, pattern: str = "*") -> Dict[str, Dict[str, Any]]:
        with self._lock:
            if pattern == "*":
                return dict(self._store)
            return {k: v for k, v in self._store.items() if pattern.replace("*", "") in k}


# =============================================================================
# Backend Factory
# =============================================================================

_backend_instance: Optional[StateBackend] = None
_backend_lock = threading.Lock()


def get_state_backend() -> StateBackend:
    """
    Get the configured state backend (singleton).
    
    Configuration priority:
    1. Django settings.SELFHEALING_STATE_BACKEND
    2. Environment variable SELFHEALING_STATE_BACKEND
    3. Default: "file"
    
    Backend types:
    - "file": FileStateBackend (single server)
    - "redis": RedisStateBackend (multi server)
    - "memory": MemoryStateBackend (testing only)
    """
    global _backend_instance
    
    if _backend_instance is not None:
        return _backend_instance
    
    with _backend_lock:
        if _backend_instance is not None:
            return _backend_instance
        
        # Determine backend type
        backend_type = _get_config("SELFHEALING_STATE_BACKEND", "file").lower()
        
        if backend_type == "redis":
            redis_url = _get_config("SELFHEALING_REDIS_URL", "redis://localhost:6379/0")
            _backend_instance = RedisStateBackend(redis_url=redis_url)
        elif backend_type == "memory":
            _backend_instance = MemoryStateBackend()
        else:  # default: file
            state_dir = _get_config("SELFHEALING_STATE_DIR", "logs/selfhealing_state")
            _backend_instance = FileStateBackend(directory=state_dir)
        
        return _backend_instance


def reset_state_backend() -> None:
    """Reset the backend instance. For testing only."""
    global _backend_instance
    with _backend_lock:
        _backend_instance = None


def _get_config(key: str, default: str) -> str:
    """Get configuration from Django settings or environment."""
    # Try Django settings first
    try:
        from django.conf import settings
        value = getattr(settings, key, None)
        if value is not None:
            return str(value)
    except Exception:
        pass
    
    # Fall back to environment variable
    return os.environ.get(key, default)
