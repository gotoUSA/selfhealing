"""
Hybrid Rate Limiting for Self-Healing Control API.

Defense-in-Depth Strategy:
- L2 (Primary): Redis-based sliding window rate limit (configurable, default 100 req/min)
- L1 (Fallback): Local memory rate limit when Redis fails (configurable, default 10 req/min)

Features:
- Redis health checking with mini circuit breaker
- Jitter-based gradual recovery to prevent thundering herd
- Shadow audit logging for forensic analysis
- Prometheus metrics for observability
- Runtime-configurable via API (RateLimitConfig)
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from django.http import JsonResponse

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Constants (Fallback Defaults)
# =============================================================================

# Normal mode settings (Redis available) - fallback if RuntimeConfig unavailable
DEFAULT_RATE_LIMIT = 100  # requests per minute
DEFAULT_WINDOW_SECONDS = 60

# Emergency mode settings (Redis unavailable)
# 10x stricter because local memory is not distributed
EMERGENCY_RATE_LIMIT = 10  # requests per minute per pod
EMERGENCY_WINDOW_SECONDS = 60

# Control API path prefix
CONTROL_API_PATH_PREFIX = "/api/self-healing/"

# Fallback log path
FALLBACK_LOG_PATH = Path("logs/rate_limit_fallback.jsonl")


# =============================================================================
# Runtime Config Reader (API Control)
# =============================================================================


def get_rate_limit_config() -> dict:
    """
    Get rate limit configuration from RuntimeConfigManager.
    
    Returns:
        dict with keys:
        - control_api_rate_limit: int (requests/minute for normal mode)
        - control_api_window_seconds: int
        - emergency_rate_limit: int (requests/minute for emergency mode)
        - emergency_window_seconds: int
    """
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        
        manager = get_runtime_config_manager()
        config = manager.get_rate_limit_config()
        
        return {
            "control_api_rate_limit": config.get("control_api_rate_limit", DEFAULT_RATE_LIMIT),
            "control_api_window_seconds": config.get("control_api_window_seconds", DEFAULT_WINDOW_SECONDS),
            "emergency_rate_limit": config.get("emergency_rate_limit", EMERGENCY_RATE_LIMIT),
            "emergency_window_seconds": config.get("emergency_window_seconds", EMERGENCY_WINDOW_SECONDS),
        }
    except Exception as e:
        # Fallback to constants if RuntimeConfig fails
        logger.warning(f"[RateLimit] Failed to get RuntimeConfig, using defaults: {e}")
        return {
            "control_api_rate_limit": DEFAULT_RATE_LIMIT,
            "control_api_window_seconds": DEFAULT_WINDOW_SECONDS,
            "emergency_rate_limit": EMERGENCY_RATE_LIMIT,
            "emergency_window_seconds": EMERGENCY_WINDOW_SECONDS,
        }


# =============================================================================
# Prometheus Metrics (Lazy Import)
# =============================================================================


def _get_metrics():
    """Get or create Prometheus metrics (lazy import to avoid circular deps)."""
    try:
        from prometheus_client import Counter, Gauge, REGISTRY
        
        # Check if already registered
        if "selfhealing_rate_limit_exceeded_total" in REGISTRY._names_to_collectors:
            exceeded_total = REGISTRY._names_to_collectors["selfhealing_rate_limit_exceeded_total"]
            degraded_mode = REGISTRY._names_to_collectors["selfhealing_rate_limit_degraded_mode"]
            failover_total = REGISTRY._names_to_collectors["selfhealing_rate_limit_failover_total"]
        else:
            exceeded_total = Counter(
                "selfhealing_rate_limit_exceeded_total",
                "Rate limit exceeded count",
                ["mode"],  # normal, emergency
            )
            degraded_mode = Gauge(
                "selfhealing_rate_limit_degraded_mode",
                "Rate limit operating in degraded mode (1=yes, 0=no)",
            )
            failover_total = Counter(
                "selfhealing_rate_limit_failover_total",
                "Number of times rate limit failed over to local memory",
            )
        
        return exceeded_total, degraded_mode, failover_total
    except ImportError:
        return None, None, None


# =============================================================================
# L1 Local Memory Rate Limiter (Emergency Fallback)
# =============================================================================


class LocalMemoryRateLimiter:
    """
    L1 로컬 메모리 기반 레이트 리미터.
    
    Redis 장애 시 활성화되는 비상 레이트 리미터.
    분산 환경에서 동기화되지 않으므로 10배 엄격한 제한 적용.
    
    Features:
    - Thread-safe with lock
    - Sliding window algorithm
    - Automatic cleanup of expired entries
    """
    
    def __init__(
        self,
        max_requests: int = EMERGENCY_RATE_LIMIT,
        window_seconds: int = EMERGENCY_WINDOW_SECONDS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # Cleanup every 60 seconds
    
    def is_allowed(self, key: str) -> Tuple[bool, int]:
        """
        Check if request is allowed.
        
        Args:
            key: Rate limit key (IP + user combination)
            
        Returns:
            Tuple of (is_allowed, remaining_requests)
        """
        now = time.time()
        window_start = now - self.window_seconds
        
        with self._lock:
            # Periodic cleanup
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_expired()
                self._last_cleanup = now
            
            # Keep only requests within window
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts > window_start
            ]
            
            current_count = len(self._requests[key])
            
            if current_count >= self.max_requests:
                return (False, 0)
            
            # Record this request
            self._requests[key].append(now)
            remaining = self.max_requests - current_count - 1
            
            return (True, remaining)
    
    def _cleanup_expired(self):
        """Remove expired entries to prevent memory leak."""
        now = time.time()
        window_start = now - self.window_seconds
        
        expired_keys = []
        for key, timestamps in self._requests.items():
            self._requests[key] = [
                ts for ts in timestamps
                if ts > window_start
            ]
            if not self._requests[key]:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self._requests[key]
    
    def reset(self):
        """Reset all rate limit state (for testing)."""
        with self._lock:
            self._requests.clear()


# =============================================================================
# Redis Health State
# =============================================================================


class RedisHealthState(Enum):
    """Redis health states."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"


# =============================================================================
# Redis Health Checker with Mini Circuit Breaker
# =============================================================================


class RedisHealthChecker:
    """
    Redis 헬스 체커 + Mini Circuit Breaker.
    
    Features:
    - Periodic ping to check Redis status
    - Circuit breaker to stop connection attempts after failures
    - Jitter-based recovery to prevent thundering herd
    """
    
    PING_INTERVAL = 5  # Check every 5 seconds
    FAILURE_THRESHOLD = 3  # 3 consecutive failures = UNHEALTHY
    RECOVERY_JITTER_MAX = 10  # Max 10 seconds random delay on recovery
    
    def __init__(self):
        self._state = RedisHealthState.HEALTHY
        self._consecutive_failures = 0
        self._last_check_time = 0.0
        self._recovery_time: Optional[float] = None
        self._redis_client = None
        self._lock = threading.Lock()
    
    @property
    def is_healthy(self) -> bool:
        """Check if Redis is available."""
        return self._state == RedisHealthState.HEALTHY
    
    @property
    def is_degraded(self) -> bool:
        """Check if operating in degraded mode."""
        return self._state != RedisHealthState.HEALTHY
    
    @property
    def state(self) -> RedisHealthState:
        """Get current state."""
        return self._state
    
    def check_health(self) -> bool:
        """
        Perform Redis health check.
        
        Returns:
            True if Redis is healthy
        """
        now = time.time()
        
        # Rate limit health checks
        if now - self._last_check_time < self.PING_INTERVAL:
            return self.is_healthy
        
        with self._lock:
            self._last_check_time = now
            
            try:
                if self._redis_client is None:
                    self._redis_client = self._get_redis_client()
                
                if self._redis_client is None:
                    # Redis not configured
                    return self._handle_no_redis()
                
                # PING test
                self._redis_client.ping()
                
                # Handle recovery states
                if self._state == RedisHealthState.UNHEALTHY:
                    self._initiate_recovery()
                elif self._state == RedisHealthState.RECOVERING:
                    self._complete_recovery_if_ready()
                else:
                    self._consecutive_failures = 0
                
                return self.is_healthy
                
            except Exception as e:
                self._handle_failure(e)
                return False
    
    def _handle_no_redis(self) -> bool:
        """Handle case where Redis is not configured."""
        if self._state != RedisHealthState.UNHEALTHY:
            logger.info("[RedisHealth] Redis not configured - using local fallback")
            self._state = RedisHealthState.UNHEALTHY
            self._record_degraded_mode(True)
        return False
    
    def _handle_failure(self, error: Exception):
        """Handle Redis connection failure."""
        self._consecutive_failures += 1
        
        if self._consecutive_failures >= self.FAILURE_THRESHOLD:
            if self._state != RedisHealthState.UNHEALTHY:
                self._state = RedisHealthState.UNHEALTHY
                logger.critical(
                    f"[RedisHealth] UNHEALTHY: {self._consecutive_failures} "
                    f"consecutive failures. Error: {error}"
                )
                self._record_degraded_mode(True)
    
    def _initiate_recovery(self):
        """Start recovery with jitter to prevent thundering herd."""
        jitter = random.uniform(1, self.RECOVERY_JITTER_MAX)
        self._recovery_time = time.time() + jitter
        self._state = RedisHealthState.RECOVERING
        
        logger.info(
            f"[RedisHealth] Recovery initiated with {jitter:.1f}s jitter"
        )
    
    def _complete_recovery_if_ready(self):
        """Complete recovery after jitter delay."""
        if self._recovery_time and time.time() >= self._recovery_time:
            self._state = RedisHealthState.HEALTHY
            self._consecutive_failures = 0
            self._recovery_time = None
            
            logger.info("[RedisHealth] RECOVERED - resuming normal operation")
            self._record_degraded_mode(False)
    
    def _get_redis_client(self):
        """Get Redis client from Django cache."""
        try:
            from django.core.cache import caches
            
            # caches is a CacheHandler - use [] indexing, not .get()
            cache = caches["default"]
            if cache is None:
                return None
            
            # Try to get the underlying client
            if hasattr(cache, "client"):
                client = cache.client
                if hasattr(client, "get_client"):
                    return client.get_client()
            
            # For django-redis
            if hasattr(cache, "_cache"):
                return cache._cache.get_client()
            
            return None
        except Exception as e:
            logger.debug(f"[RedisHealth] Could not get Redis client: {e}")
            return None
    
    def _record_degraded_mode(self, is_degraded: bool):
        """Update Prometheus metrics."""
        try:
            _, degraded_mode, failover_total = _get_metrics()
            if degraded_mode:
                degraded_mode.set(1 if is_degraded else 0)
            if is_degraded and failover_total:
                failover_total.inc()
        except Exception:
            pass
    
    def reset(self):
        """Reset health checker state (for testing)."""
        with self._lock:
            self._state = RedisHealthState.HEALTHY
            self._consecutive_failures = 0
            self._last_check_time = 0
            self._recovery_time = None


# =============================================================================
# Singleton Instances
# =============================================================================

_health_checker: Optional[RedisHealthChecker] = None
_local_limiter: Optional[LocalMemoryRateLimiter] = None


def get_redis_health_checker() -> RedisHealthChecker:
    """Get or create Redis health checker singleton."""
    global _health_checker
    if _health_checker is None:
        _health_checker = RedisHealthChecker()
    return _health_checker


def get_local_limiter() -> LocalMemoryRateLimiter:
    """Get or create local memory limiter singleton."""
    global _local_limiter
    if _local_limiter is None:
        _local_limiter = LocalMemoryRateLimiter()
    return _local_limiter


# =============================================================================
# Hybrid Rate Limit Middleware
# =============================================================================


class HybridRateLimitMiddleware:
    """
    지능형 하이브리드 레이트 리밋 미들웨어.
    
    Defense-in-Depth Strategy:
    - L2 (Redis) 정상 → Redis 기반 Rate Limit (100 req/min)
    - L2 (Redis) 장애 → L1 (로컬 메모리) 비상 Rate Limit (10 req/min)
    
    Features:
    - Automatic failover to local memory on Redis failure
    - Shadow audit logging for forensic analysis
    - Prometheus metrics for observability
    - Jitter-based recovery to prevent thundering herd
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = self._get_redis_client()
        self.local_limiter = get_local_limiter()
        self.health_checker = get_redis_health_checker()
    
    def _get_redis_client(self):
        """Get Redis client from Django cache."""
        try:
            from django.core.cache import caches
            
            cache = caches.get("default")
            if cache is None:
                return None
            
            if hasattr(cache, "client"):
                client = cache.client
                if hasattr(client, "get_client"):
                    return client.get_client()
            
            if hasattr(cache, "_cache"):
                return cache._cache.get_client()
            
            return None
        except Exception:
            return None
    
    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Only apply to Control API
        if not request.path.startswith(CONTROL_API_PATH_PREFIX):
            return self.get_response(request)
        
        # Hook Registry bypass check (Domain-Free, Audit-Logged)
        # All bypass logic is now in selfhealing.resilience.bypass_hooks
        bypass_result = self._check_bypass_registry(request)
        if bypass_result.bypassed:
            response = self.get_response(request)
            response["X-RateLimit-Mode"] = "bypass"
            response["X-RateLimit-Remaining"] = "unlimited"
            response["X-RateLimit-Bypass-Reason"] = bypass_result.hook_name
            return response
        
        # Get runtime config (API Control)
        config = get_rate_limit_config()
        rate_limit = config["control_api_rate_limit"]
        window_seconds = config["control_api_window_seconds"]
        emergency_limit = config["emergency_rate_limit"]
        emergency_window = config["emergency_window_seconds"]
        
        # Health check
        redis_healthy = self.health_checker.check_health()
        
        if redis_healthy:
            # L2 (Redis) Rate Limit
            is_allowed, remaining, reset_time = self._check_redis_limit(
                request, rate_limit, window_seconds
            )
            mode = "normal"
        else:
            # L1 (Local Memory) Emergency Rate Limit
            is_allowed, remaining = self._check_local_limit(
                request, emergency_limit, emergency_window
            )
            reset_time = int(time.time()) + emergency_window
            mode = "emergency"
            
            # Shadow Audit for forensic analysis
            self._log_emergency_bypass(request, is_allowed, emergency_limit)
        
        if not is_allowed:
            # Record exceeded metric
            self._record_exceeded(mode)
            return self._rate_limit_response(remaining, reset_time, mode)
        
        response = self.get_response(request)
        
        # Add rate limit headers
        response["X-RateLimit-Remaining"] = str(remaining)
        response["X-RateLimit-Reset"] = str(reset_time)
        response["X-RateLimit-Mode"] = mode
        response["X-RateLimit-Limit"] = str(rate_limit if mode == "normal" else emergency_limit)
        
        return response
    
    def _check_bypass_registry(self, request: HttpRequest) -> "BypassResult":
        """
        Check if request should bypass rate limiting via Hook Registry.
        
        This method delegates all bypass logic to the BypassRegistry system,
        which is configured in selfhealing.resilience.bypass_hooks.
        
        All bypass decisions are:
        - Priority-ordered (higher priority hooks run first)
        - Audit-logged (for compliance)
        - Environment-conditional (blocked in production)
        
        Returns:
            BypassResult with bypass decision and audit information
        """
        try:
            from selfhealing.core.hooks import BypassRegistry, BypassResult
            return BypassRegistry.should_bypass(request)
        except ImportError:
            # Fallback if hooks module not available
            from dataclasses import dataclass
            
            @dataclass
            class FallbackResult:
                bypassed: bool = False
                reason: str = "Hook registry not available"
                hook_name: str = ""
                priority: int = 0
            
            return FallbackResult()
    
    # =========================================================================
    # DEPRECATED: _should_bypass_for_xtest
    # =========================================================================
    # This method is kept for backward compatibility but should not be used.
    # All bypass logic has been moved to selfhealing.resilience.bypass_hooks
    # via the BypassRegistry pattern.
    #
    # Migration: Use _check_bypass_registry() instead.
    # Removal target: v7.0.0
    # =========================================================================
    
    def _should_bypass_for_xtest(self, request: HttpRequest) -> bool:
        """
        DEPRECATED: Use _check_bypass_registry() instead.
        
        This method is kept for backward compatibility.
        All bypass logic has been moved to BypassRegistry.
        """
        import warnings
        warnings.warn(
            "_should_bypass_for_xtest is deprecated, use _check_bypass_registry instead",
            DeprecationWarning,
            stacklevel=2
        )
        result = self._check_bypass_registry(request)
        return result.bypassed
    
    def _get_client_key(self, request: HttpRequest) -> str:
        """Generate rate limit key (IP + User)."""
        ip = self._get_client_ip(request)
        user_id = getattr(request.user, "id", "anonymous") if hasattr(request, "user") else "anonymous"
        return f"ratelimit:control_api:{ip}:{user_id}"
    
    def _get_client_ip(self, request: HttpRequest) -> str:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")
    
    def _check_redis_limit(
        self,
        request: HttpRequest,
        rate_limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int, int]:
        """
        Check rate limit using Redis.
        
        Args:
            request: HTTP request
            rate_limit: Max requests per window
            window_seconds: Window size in seconds
        
        Returns:
            Tuple of (is_allowed, remaining, reset_timestamp)
        """
        # Redis unavailable - fail-open but this shouldn't happen
        # because we check health first
        if not self.redis_client:
            logger.warning("[RateLimit] Redis unavailable in check_redis_limit")
            return (True, rate_limit, 0)
        
        try:
            key = self._get_client_key(request)
            now = int(time.time())
            window_start = now - window_seconds
            
            pipe = self.redis_client.pipeline()
            
            # Sliding window: add current time, remove old entries
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, window_seconds + 10)
            
            results = pipe.execute()
            current_count = results[2]
            
            remaining = max(0, rate_limit - current_count)
            reset_time = now + window_seconds
            
            if current_count > rate_limit:
                logger.warning(
                    f"[RateLimit] Exceeded: key={key}, count={current_count}, limit={rate_limit}"
                )
                return (False, 0, reset_time)
            
            return (True, remaining, reset_time)
            
        except Exception as e:
            # On Redis error, fall back to local limiter
            logger.error(f"[RateLimit] Redis error - falling back to local: {e}")
            config = get_rate_limit_config()
            is_allowed, remaining = self._check_local_limit(
                request,
                config["emergency_rate_limit"],
                config["emergency_window_seconds"],
            )
            reset_time = int(time.time()) + config["emergency_window_seconds"]
            
            # Log the fallback
            self._log_emergency_bypass(
                request, is_allowed, config["emergency_rate_limit"], reason=str(e)
            )
            
            return (is_allowed, remaining, reset_time)
    
    def _check_local_limit(
        self,
        request: HttpRequest,
        max_requests: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ) -> Tuple[bool, int]:
        """Check rate limit using local memory."""
        key = self._get_client_key(request)
        
        # Update local limiter settings if different
        if max_requests is not None:
            self.local_limiter.max_requests = max_requests
        if window_seconds is not None:
            self.local_limiter.window_seconds = window_seconds
        
        return self.local_limiter.is_allowed(key)
    
    def _log_emergency_bypass(
        self,
        request: HttpRequest,
        is_allowed: bool,
        emergency_limit: int = EMERGENCY_RATE_LIMIT,
        reason: str = "Redis failure",
    ):
        """
        Log emergency mode access for forensic analysis.
        
        Writes to both:
        1. AuditService (if available)
        2. Local fallback file (always)
        """
        # Local fallback file (always works)
        try:
            FALLBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "rate_limit_emergency",
                "mode": "REDIS_FAILURE_BYPASS",
                "allowed": is_allowed,
                "path": request.path,
                "method": request.method,
                "client_ip": self._get_client_ip(request),
                "emergency_limit": emergency_limit,
                "reason": reason,
            }
            
            with open(FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                
        except Exception as e:
            logger.error(f"[RateLimit] Failed to write fallback log: {e}")
        
        # AuditService (best-effort)
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="rate_limit_emergency",
                changes={
                    "mode": "REDIS_FAILURE_BYPASS",
                    "allowed": is_allowed,
                    "path": request.path,
                    "client_ip": self._get_client_ip(request),
                    "emergency_limit": emergency_limit,
                },
                changed_by="system",
                reason=f"Rate limit operating in emergency mode: {reason}",
                metadata={
                    "severity": "critical",
                    "tag": "REDIS_FAILURE_BYPASS",
                },
            )
        except Exception as e:
            logger.debug(f"[RateLimit] Shadow audit failed (non-critical): {e}")
    
    def _record_exceeded(self, mode: str):
        """Record rate limit exceeded metric."""
        try:
            exceeded_total, _, _ = _get_metrics()
            if exceeded_total:
                exceeded_total.labels(mode=mode).inc()
        except Exception:
            pass
    
    def _rate_limit_response(
        self,
        remaining: int,
        reset_time: int,
        mode: str,
    ) -> JsonResponse:
        """Generate 429 Too Many Requests response."""
        retry_after = max(1, reset_time - int(time.time()))
        
        message = "Too many requests to Control API"
        if mode == "emergency":
            message += " (Emergency mode: stricter limits applied)"
        
        return JsonResponse(
            {
                "error": "rate_limit_exceeded",
                "message": message,
                "mode": mode,
                "retry_after": retry_after,
            },
            status=429,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_time),
                "X-RateLimit-Mode": mode,
                "Retry-After": str(retry_after),
            },
        )


# =============================================================================
# Utility Functions for Testing
# =============================================================================


def reset_rate_limit_state():
    """Reset all rate limit state (for testing)."""
    global _health_checker, _local_limiter
    
    if _health_checker:
        _health_checker.reset()
    if _local_limiter:
        _local_limiter.reset()


def get_current_state() -> dict:
    """Get current rate limit state (for debugging/monitoring)."""
    health_checker = get_redis_health_checker()
    local_limiter = get_local_limiter()
    
    return {
        "redis_state": health_checker.state.value,
        "redis_healthy": health_checker.is_healthy,
        "redis_degraded": health_checker.is_degraded,
        "local_limiter_keys": len(local_limiter._requests),
    }
