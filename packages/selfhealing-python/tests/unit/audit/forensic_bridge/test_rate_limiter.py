"""
Forensic Rate Limiter 테스트.

테스트 대상:
- TestForensicRateLimiter: Rate Limiter 기능
"""

import time
import pytest


class TestForensicRateLimiter:
    """Forensic Rate Limiter 테스트."""
    
    def test_exception_rate_limit(self):
        """분당 10건 초과 시 드롭."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(exception_limit=3, window_seconds=60.0)
        
        # 첫 3건은 허용
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is True
        
        # 4번째부터 거부
        assert limiter.try_acquire_exception() is False
        assert limiter.try_acquire_exception() is False
        
        stats = limiter.get_stats()
        assert stats["exception_requests_in_window"] == 3
        assert stats["exceptions_dropped"] == 2
    
    def test_snapshot_rate_limit(self):
        """분당 1건 초과 시 드롭."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(snapshot_limit=1, window_seconds=60.0)
        
        assert limiter.try_acquire_snapshot() is True
        assert limiter.try_acquire_snapshot() is False
        assert limiter.try_acquire_snapshot() is False
        
        stats = limiter.get_stats()
        assert stats["snapshot_requests_in_window"] == 1
        assert stats["snapshots_dropped"] == 2
    
    def test_anomaly_rate_limit(self):
        """이상 탐지 Rate Limit."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(anomaly_limit=2, window_seconds=60.0)
        
        assert limiter.try_acquire_anomaly() is True
        assert limiter.try_acquire_anomaly() is True
        assert limiter.try_acquire_anomaly() is False
        
        stats = limiter.get_stats()
        assert stats["anomaly_requests_in_window"] == 2
        assert stats["anomalies_dropped"] == 1
    
    def test_window_expiry(self):
        """윈도우 경과 후 토큰 재충전."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        # 0.1초 윈도우로 빠른 테스트
        limiter = ForensicRateLimiter(exception_limit=1, window_seconds=0.1)
        
        assert limiter.try_acquire_exception() is True
        assert limiter.try_acquire_exception() is False
        
        # 윈도우 경과 대기
        time.sleep(0.15)
        
        # 다시 허용
        assert limiter.try_acquire_exception() is True
    
    def test_reset(self):
        """Rate limiter 리셋."""
        from selfhealing.services.forensic_audit_bridge import ForensicRateLimiter
        
        limiter = ForensicRateLimiter(exception_limit=1)
        
        limiter.try_acquire_exception()
        limiter.try_acquire_exception()  # dropped
        
        stats_before = limiter.get_stats()
        assert stats_before["exceptions_dropped"] == 1
        
        limiter.reset()
        
        stats_after = limiter.get_stats()
        assert stats_after["exceptions_dropped"] == 0
        assert stats_after["exception_requests_in_window"] == 0
    
    def test_bridge_uses_rate_limiter(self):
        """ForensicAuditBridge가 Rate Limiter 사용."""
        from selfhealing.services.forensic_audit_bridge import (
            ForensicAuditBridge,
            ForensicRateLimiter,
        )
        
        limiter = ForensicRateLimiter(exception_limit=2)
        bridge = ForensicAuditBridge(rate_limiter=limiter)
        
        # 첫 2건은 성공
        result1 = bridge.on_exception_captured(
            ValueError("test"), "stack", {"key": "value"}
        )
        result2 = bridge.on_exception_captured(
            ValueError("test2"), "stack2", {"key2": "value2"}
        )
        
        # 3번째는 Rate Limited
        result3 = bridge.on_exception_captured(
            ValueError("test3"), "stack3", {"key3": "value3"}
        )
        
        assert result1 is True
        assert result2 is True
        assert result3 is False
    
    def test_bridge_rate_limiter_stats(self):
        """Bridge에서 Rate Limiter 통계 조회."""
        from selfhealing.services.forensic_audit_bridge import (
            ForensicAuditBridge,
            ForensicRateLimiter,
        )
        
        limiter = ForensicRateLimiter()
        bridge = ForensicAuditBridge(rate_limiter=limiter)
        
        stats = bridge.get_rate_limiter_stats()
        
        assert "exception_limit" in stats
        assert "snapshot_limit" in stats
        assert "anomaly_limit" in stats
