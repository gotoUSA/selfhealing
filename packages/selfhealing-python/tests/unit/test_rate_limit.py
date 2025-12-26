"""
Tests for Hybrid Rate Limit Middleware.

Tests the Defense-in-Depth rate limiting strategy:
- L2 (Redis): Normal rate limiting (100 req/min)
- L1 (Local Memory): Emergency fallback (10 req/min)
- Redis Health Checker with mini circuit breaker
- Shadow audit logging
- Prometheus metrics

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md (Section 3)
"""

import json
import time
import threading
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest


# =============================================================================
# LocalMemoryRateLimiter Tests
# =============================================================================


class TestLocalMemoryRateLimiter:
    """Tests for L1 Local Memory Rate Limiter."""

    def test_allows_requests_under_limit(self):
        """Should allow requests under the limit."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=10, window_seconds=60)
        
        for i in range(10):
            is_allowed, remaining = limiter.is_allowed("test_key")
            assert is_allowed is True
            assert remaining == 10 - i - 1

    def test_blocks_requests_over_limit(self):
        """Should block requests over the limit."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=5, window_seconds=60)
        
        # Use up the limit
        for _ in range(5):
            limiter.is_allowed("test_key")
        
        # Next request should be blocked
        is_allowed, remaining = limiter.is_allowed("test_key")
        assert is_allowed is False
        assert remaining == 0

    def test_different_keys_have_separate_limits(self):
        """Different keys should have separate rate limits."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=3, window_seconds=60)
        
        # Use up key1's limit
        for _ in range(3):
            limiter.is_allowed("key1")
        
        # key1 should be blocked
        is_allowed, _ = limiter.is_allowed("key1")
        assert is_allowed is False
        
        # key2 should still be allowed
        is_allowed, remaining = limiter.is_allowed("key2")
        assert is_allowed is True
        assert remaining == 2

    def test_thread_safety(self):
        """Should be thread-safe under concurrent access."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=100, window_seconds=60)
        results = []
        
        def make_request():
            is_allowed, _ = limiter.is_allowed("concurrent_key")
            results.append(is_allowed)
        
        threads = [threading.Thread(target=make_request) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All 50 requests should be allowed (under 100 limit)
        assert all(results)
        assert len(results) == 50

    def test_reset_clears_all_state(self):
        """Reset should clear all rate limit state."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=3, window_seconds=60)
        
        # Use up the limit
        for _ in range(3):
            limiter.is_allowed("test_key")
        
        # Should be blocked
        is_allowed, _ = limiter.is_allowed("test_key")
        assert is_allowed is False
        
        # Reset
        limiter.reset()
        
        # Should be allowed again
        is_allowed, _ = limiter.is_allowed("test_key")
        assert is_allowed is True

    def test_sliding_window_expires_old_requests(self):
        """Old requests should expire from the sliding window."""
        from selfhealing.api.django.rate_limit import LocalMemoryRateLimiter
        
        limiter = LocalMemoryRateLimiter(max_requests=2, window_seconds=1)
        
        # Use up the limit
        limiter.is_allowed("test_key")
        limiter.is_allowed("test_key")
        
        # Should be blocked
        is_allowed, _ = limiter.is_allowed("test_key")
        assert is_allowed is False
        
        # Wait for window to expire
        time.sleep(1.1)
        
        # Should be allowed again
        is_allowed, _ = limiter.is_allowed("test_key")
        assert is_allowed is True


# =============================================================================
# RedisHealthChecker Tests
# =============================================================================


class TestRedisHealthChecker:
    """Tests for Redis Health Checker with mini circuit breaker."""

    def test_initial_state_is_healthy(self):
        """Initial state should be healthy."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        assert checker.state == RedisHealthState.HEALTHY
        assert checker.is_healthy is True
        assert checker.is_degraded is False

    def test_transitions_to_unhealthy_after_failures(self):
        """Should transition to UNHEALTHY after consecutive failures."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        checker._last_check_time = 0  # Force check
        
        # Simulate failures
        for i in range(3):
            checker._handle_failure(Exception(f"Redis error {i}"))
        
        assert checker.state == RedisHealthState.UNHEALTHY
        assert checker.is_healthy is False
        assert checker.is_degraded is True

    def test_stays_healthy_with_intermittent_failures(self):
        """Should stay healthy with fewer than threshold failures."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        
        # Only 2 failures (threshold is 3)
        checker._handle_failure(Exception("Error 1"))
        checker._handle_failure(Exception("Error 2"))
        
        assert checker.state == RedisHealthState.HEALTHY
        assert checker._consecutive_failures == 2

    def test_reset_restores_healthy_state(self):
        """Reset should restore healthy state."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        
        # Force unhealthy
        for _ in range(3):
            checker._handle_failure(Exception("Error"))
        
        assert checker.state == RedisHealthState.UNHEALTHY
        
        # Reset
        checker.reset()
        
        assert checker.state == RedisHealthState.HEALTHY
        assert checker._consecutive_failures == 0

    def test_recovery_applies_jitter(self):
        """Recovery should apply jitter delay."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        
        # Force unhealthy
        for _ in range(3):
            checker._handle_failure(Exception("Error"))
        
        # Initiate recovery
        checker._initiate_recovery()
        
        assert checker.state == RedisHealthState.RECOVERING
        assert checker._recovery_time is not None
        assert checker._recovery_time > time.time()  # Future time

    def test_recovery_completes_after_jitter(self):
        """Recovery should complete after jitter delay."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker, RedisHealthState
        
        checker = RedisHealthChecker()
        checker._state = RedisHealthState.RECOVERING
        checker._recovery_time = time.time() - 1  # Past time
        
        with patch.object(checker, '_record_degraded_mode'):
            checker._complete_recovery_if_ready()
        
        assert checker.state == RedisHealthState.HEALTHY
        assert checker._consecutive_failures == 0
        assert checker._recovery_time is None


# =============================================================================
# HybridRateLimitMiddleware Tests
# =============================================================================


class TestHybridRateLimitMiddleware:
    """Tests for Hybrid Rate Limit Middleware."""

    def _create_mock_request(self, path="/api/self-healing/test/", method="GET"):
        """Create a mock request."""
        request = Mock()
        request.path = path
        request.method = method
        request.user = Mock()
        request.user.id = 1
        request.META = {
            "REMOTE_ADDR": "127.0.0.1",
        }
        return request

    def test_skips_non_control_api_paths(self):
        """Should skip paths not under /api/self-healing/."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        mock_response = Mock()
        get_response = Mock(return_value=mock_response)
        
        middleware = HybridRateLimitMiddleware(get_response)
        request = self._create_mock_request(path="/api/products/")
        
        response = middleware(request)
        
        assert response == mock_response
        get_response.assert_called_once_with(request)

    def test_applies_to_control_api_paths(self):
        """Should apply rate limiting to /api/self-healing/ paths."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        mock_response = Mock()
        mock_response.__setitem__ = Mock()
        get_response = Mock(return_value=mock_response)
        
        middleware = HybridRateLimitMiddleware(get_response)
        middleware.health_checker = Mock()
        middleware.health_checker.check_health.return_value = False  # Redis unavailable
        middleware.local_limiter = Mock()
        middleware.local_limiter.is_allowed.return_value = (True, 9)
        
        request = self._create_mock_request()
        
        with patch.object(middleware, '_log_emergency_bypass'):
            response = middleware(request)
        
        # Should have rate limit headers
        mock_response.__setitem__.assert_any_call("X-RateLimit-Remaining", "9")
        mock_response.__setitem__.assert_any_call("X-RateLimit-Mode", "emergency")

    def test_uses_redis_when_healthy(self):
        """Should use Redis rate limiting when healthy."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        mock_response = Mock()
        mock_response.__setitem__ = Mock()
        get_response = Mock(return_value=mock_response)
        
        middleware = HybridRateLimitMiddleware(get_response)
        middleware.health_checker = Mock()
        middleware.health_checker.check_health.return_value = True  # Redis healthy
        middleware.redis_client = Mock()
        
        # Mock Redis pipeline
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [True, 0, 5, True]  # 5 requests in window
        middleware.redis_client.pipeline.return_value = mock_pipe
        
        request = self._create_mock_request()
        response = middleware(request)
        
        mock_response.__setitem__.assert_any_call("X-RateLimit-Mode", "normal")

    def test_falls_back_to_local_when_redis_unhealthy(self):
        """Should fall back to local limiter when Redis is unhealthy."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        mock_response = Mock()
        mock_response.__setitem__ = Mock()
        get_response = Mock(return_value=mock_response)
        
        middleware = HybridRateLimitMiddleware(get_response)
        middleware.health_checker = Mock()
        middleware.health_checker.check_health.return_value = False
        middleware.local_limiter = Mock()
        middleware.local_limiter.is_allowed.return_value = (True, 5)
        
        request = self._create_mock_request()
        
        with patch.object(middleware, '_log_emergency_bypass'):
            response = middleware(request)
        
        middleware.local_limiter.is_allowed.assert_called_once()
        mock_response.__setitem__.assert_any_call("X-RateLimit-Mode", "emergency")

    def test_returns_429_when_limit_exceeded(self):
        """Should return 429 when rate limit is exceeded."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        get_response = Mock()
        
        middleware = HybridRateLimitMiddleware(get_response)
        middleware.health_checker = Mock()
        middleware.health_checker.check_health.return_value = False
        middleware.local_limiter = Mock()
        middleware.local_limiter.is_allowed.return_value = (False, 0)  # Blocked
        
        request = self._create_mock_request()
        
        with patch.object(middleware, '_log_emergency_bypass'):
            with patch.object(middleware, '_record_exceeded'):
                response = middleware(request)
        
        assert response.status_code == 429
        response_data = json.loads(response.content)
        assert response_data["error"] == "rate_limit_exceeded"

    def test_client_key_includes_ip_and_user(self):
        """Client key should include both IP and user ID."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        middleware = HybridRateLimitMiddleware(Mock())
        
        request = self._create_mock_request()
        request.user.id = 123
        request.META["REMOTE_ADDR"] = "192.168.1.100"
        
        key = middleware._get_client_key(request)
        
        assert "192.168.1.100" in key
        assert "123" in key

    def test_extracts_ip_from_x_forwarded_for(self):
        """Should extract IP from X-Forwarded-For header."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware
        
        middleware = HybridRateLimitMiddleware(Mock())
        
        request = Mock()
        request.META = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.50, 70.41.3.18",
            "REMOTE_ADDR": "127.0.0.1",
        }
        
        ip = middleware._get_client_ip(request)
        
        assert ip == "203.0.113.50"


# =============================================================================
# Shadow Audit Tests
# =============================================================================


class TestShadowAudit:
    """Tests for Shadow Audit logging in emergency mode."""

    def test_logs_to_fallback_file(self, tmp_path):
        """Should log to fallback file in emergency mode."""
        from selfhealing.api.django.rate_limit import HybridRateLimitMiddleware, FALLBACK_LOG_PATH
        
        # Use tmp_path for test
        with patch('selfhealing.api.django.rate_limit.FALLBACK_LOG_PATH', tmp_path / "rate_limit_fallback.jsonl"):
            mock_response = Mock()
            mock_response.__setitem__ = Mock()
            get_response = Mock(return_value=mock_response)
            
            middleware = HybridRateLimitMiddleware(get_response)
            middleware.health_checker = Mock()
            middleware.health_checker.check_health.return_value = False
            middleware.local_limiter = Mock()
            middleware.local_limiter.is_allowed.return_value = (True, 5)
            
            request = Mock()
            request.path = "/api/self-healing/test/"
            request.method = "POST"
            request.user = Mock()
            request.user.id = 1
            request.META = {"REMOTE_ADDR": "10.0.0.1"}
            
            with patch('selfhealing.audit.log_config_change'):
                middleware(request)
            
            # Check fallback file was written
            fallback_path = tmp_path / "rate_limit_fallback.jsonl"
            assert fallback_path.exists()
            
            content = fallback_path.read_text()
            entry = json.loads(content.strip())
            assert entry["event"] == "rate_limit_emergency"
            assert entry["mode"] == "REDIS_FAILURE_BYPASS"


# =============================================================================
# Integration Tests
# =============================================================================


class TestRateLimitIntegration:
    """Integration tests for the full rate limit flow."""

    def test_full_emergency_flow(self):
        """Test complete flow from Redis failure to emergency mode."""
        from selfhealing.api.django.rate_limit import (
            HybridRateLimitMiddleware,
            RedisHealthChecker,
            LocalMemoryRateLimiter,
            reset_rate_limit_state,
        )
        
        # Reset state
        reset_rate_limit_state()
        
        mock_response = Mock()
        mock_response.__setitem__ = Mock()
        get_response = Mock(return_value=mock_response)
        
        middleware = HybridRateLimitMiddleware(get_response)
        
        # Simulate Redis failure
        middleware.health_checker._redis_client = Mock()
        middleware.health_checker._redis_client.ping.side_effect = Exception("Connection refused")
        
        # Force health check
        middleware.health_checker._last_check_time = 0
        
        request = Mock()
        request.path = "/api/self-healing/test/"
        request.method = "GET"
        request.user = Mock()
        request.user.id = 1
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        
        # Make multiple requests to trigger UNHEALTHY state
        for _ in range(5):
            middleware.health_checker._last_check_time = 0  # Force recheck
            with patch.object(middleware, '_log_emergency_bypass'):
                middleware(request)
        
        # Should be in emergency mode
        assert middleware.health_checker.is_degraded is True

    def test_get_current_state(self):
        """Test state inspection utility."""
        from selfhealing.api.django.rate_limit import get_current_state, reset_rate_limit_state
        
        reset_rate_limit_state()
        
        state = get_current_state()
        
        assert "redis_state" in state
        assert "redis_healthy" in state
        assert "redis_degraded" in state
        assert "local_limiter_keys" in state


# =============================================================================
# Defense-in-Depth Strategy Tests
# =============================================================================


class TestDefenseInDepth:
    """Tests verifying the Defense-in-Depth strategy."""

    def test_l1_is_stricter_than_l2(self):
        """L1 (emergency) should be 10x stricter than L2 (normal)."""
        from selfhealing.api.django.rate_limit import (
            DEFAULT_RATE_LIMIT,
            EMERGENCY_RATE_LIMIT,
        )
        
        assert EMERGENCY_RATE_LIMIT == DEFAULT_RATE_LIMIT / 10

    def test_multiple_fallback_layers(self):
        """Should have multiple fallback layers."""
        from selfhealing.api.django.rate_limit import (
            HybridRateLimitMiddleware,
            LocalMemoryRateLimiter,
            RedisHealthChecker,
        )
        
        # Verify components exist
        middleware = HybridRateLimitMiddleware(Mock())
        
        # L2: Redis rate limiting
        assert hasattr(middleware, 'redis_client')
        
        # L1: Local memory fallback
        assert hasattr(middleware, 'local_limiter')
        assert isinstance(middleware.local_limiter, LocalMemoryRateLimiter)
        
        # Health checker
        assert hasattr(middleware, 'health_checker')
        assert isinstance(middleware.health_checker, RedisHealthChecker)

    def test_jitter_prevents_thundering_herd(self):
        """Recovery jitter should be randomized to prevent thundering herd."""
        from selfhealing.api.django.rate_limit import RedisHealthChecker
        
        checker1 = RedisHealthChecker()
        checker2 = RedisHealthChecker()
        
        # Both initiate recovery
        checker1._initiate_recovery()
        checker2._initiate_recovery()
        
        # Recovery times should be different (randomized jitter)
        assert checker1._recovery_time != checker2._recovery_time
