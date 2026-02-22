"""
Redis Key Priority Eviction 단위 테스트.

11.4 E.1 구현 검증:
- RedisKeyPriority enum
- RedisKeyPriorityEviction 키 우선순위 판단
- 보호 키 확인
- TTL 설정

selfhealing 패키지만 import하는 순수 단위 테스트입니다.

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.1
"""

from unittest.mock import MagicMock

import pytest

from selfhealing.services.coordination.redis_key_guard import (
    KeyPatternConfig,
    RedisKeyPriority,
    RedisKeyPriorityEviction,
    RedisMemoryInfo,
    get_redis_key_guard,
)


class TestRedisKeyPriority:
    """RedisKeyPriority enum 테스트."""

    def test_priority_ordering(self):
        """우선순위 순서 확인 (낮은 숫자 = 높은 우선순위)."""
        assert RedisKeyPriority.P0_GOVERNANCE < RedisKeyPriority.P1_RECOVERY
        assert RedisKeyPriority.P1_RECOVERY < RedisKeyPriority.P2_BUDGET
        assert RedisKeyPriority.P2_BUDGET < RedisKeyPriority.P3_CACHE
        assert RedisKeyPriority.P3_CACHE < RedisKeyPriority.P4_AUDIT

    def test_priority_values(self):
        """우선순위 값 확인."""
        assert RedisKeyPriority.P0_GOVERNANCE == 0
        assert RedisKeyPriority.P1_RECOVERY == 1
        assert RedisKeyPriority.P2_BUDGET == 2
        assert RedisKeyPriority.P3_CACHE == 3
        assert RedisKeyPriority.P4_AUDIT == 4


class TestRedisMemoryInfo:
    """RedisMemoryInfo 모델 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        info = RedisMemoryInfo()

        assert info.used_memory == 0
        assert info.max_memory == 0
        assert info.used_percent == 0.0
        assert info.eviction_policy == "noeviction"

    def test_is_warning(self):
        """경고 수준 확인."""
        info = RedisMemoryInfo(used_percent=75.0)
        assert not info.is_warning(threshold=80.0)

        info = RedisMemoryInfo(used_percent=85.0)
        assert info.is_warning(threshold=80.0)

    def test_is_critical(self):
        """위험 수준 확인."""
        info = RedisMemoryInfo(used_percent=88.0)
        assert not info.is_critical(threshold=90.0)

        info = RedisMemoryInfo(used_percent=95.0)
        assert info.is_critical(threshold=90.0)


class TestKeyPatternConfig:
    """KeyPatternConfig 모델 테스트."""

    def test_create_config(self):
        """설정 생성."""
        config = KeyPatternConfig(
            pattern="selfhealing:*:emergency:*",
            priority=RedisKeyPriority.P0_GOVERNANCE,
            default_ttl_seconds=None,
            description="Emergency 상태 데이터",
        )

        assert config.pattern == "selfhealing:*:emergency:*"
        assert config.priority == RedisKeyPriority.P0_GOVERNANCE
        assert config.default_ttl_seconds is None


class TestRedisKeyPriorityEviction:
    """RedisKeyPriorityEviction 테스트."""

    @pytest.fixture
    def eviction(self):
        """테스트용 RedisKeyPriorityEviction 인스턴스."""
        return RedisKeyPriorityEviction()

    # =========================================================================
    # get_key_priority 테스트
    # =========================================================================

    def test_get_priority_governance_keys(self, eviction):
        """거버넌스 키 우선순위 (P0)."""
        keys = [
            "selfhealing:global:emergency:level",
            "selfhealing:payment:emergency:state",
            "selfhealing:global:governance:mode",
        ]

        for key in keys:
            priority = eviction.get_key_priority(key)
            assert priority == RedisKeyPriority.P0_GOVERNANCE, f"Expected P0 for {key}"

    def test_get_priority_recovery_keys(self, eviction):
        """복구 키 우선순위 (P1)."""
        keys = [
            "selfhealing:global:recovery:session:abc123",
            "selfhealing:payment:recovery:lock:xyz",
        ]

        for key in keys:
            priority = eviction.get_key_priority(key)
            assert priority == RedisKeyPriority.P1_RECOVERY, f"Expected P1 for {key}"

    def test_get_priority_budget_keys(self, eviction):
        """버짓 키 우선순위 (P2)."""
        keys = [
            "selfhealing:global:budget:consumed",
            "selfhealing:payment:budget:multiplier",
        ]

        for key in keys:
            priority = eviction.get_key_priority(key)
            assert priority == RedisKeyPriority.P2_BUDGET, f"Expected P2 for {key}"

    def test_get_priority_cache_keys(self, eviction):
        """캐시 키 우선순위 (P3)."""
        keys = [
            "cache:user:profile:123",
            "metrics:realtime:error_rate",
        ]

        for key in keys:
            priority = eviction.get_key_priority(key)
            assert priority == RedisKeyPriority.P3_CACHE, f"Expected P3 for {key}"

    def test_get_priority_audit_keys(self, eviction):
        """감사 키 우선순위 (P4)."""
        keys = [
            "audit:event:12345",
            "some:unknown:key",
        ]

        for key in keys:
            priority = eviction.get_key_priority(key)
            assert priority == RedisKeyPriority.P4_AUDIT, f"Expected P4 for {key}"

    # =========================================================================
    # should_protect_key 테스트
    # =========================================================================

    def test_should_protect_governance_keys(self, eviction):
        """거버넌스 키는 보호되어야 함."""
        assert eviction.should_protect_key("selfhealing:global:emergency:level") is True
        assert eviction.should_protect_key("selfhealing:global:governance:mode") is True

    def test_should_protect_recovery_keys(self, eviction):
        """복구 키는 보호되어야 함."""
        assert eviction.should_protect_key("selfhealing:global:recovery:session:abc") is True
        assert eviction.should_protect_key("selfhealing:global:recovery:lock:xyz") is True

    def test_should_protect_budget_keys(self, eviction):
        """버짓 키는 보호되어야 함."""
        assert eviction.should_protect_key("selfhealing:global:budget:consumed") is True

    def test_should_not_protect_cache_keys(self, eviction):
        """캐시 키는 보호되지 않아야 함."""
        assert eviction.should_protect_key("cache:user:profile") is False
        assert eviction.should_protect_key("metrics:realtime:data") is False

    def test_should_not_protect_audit_keys(self, eviction):
        """감사 키는 보호되지 않아야 함."""
        assert eviction.should_protect_key("audit:event:12345") is False

    # =========================================================================
    # get_key_ttl 테스트
    # =========================================================================

    def test_get_ttl_protected_keys_no_ttl(self, eviction):
        """보호 키는 TTL이 없어야 함."""
        assert eviction.get_key_ttl("selfhealing:global:emergency:level") is None
        assert eviction.get_key_ttl("selfhealing:global:recovery:session:abc") is None

    def test_get_ttl_cache_keys(self, eviction):
        """캐시 키는 TTL이 있어야 함."""
        ttl = eviction.get_key_ttl("cache:user:profile")
        assert ttl is not None
        assert ttl > 0

    def test_get_ttl_audit_keys(self, eviction):
        """감사 키는 7일 TTL."""
        ttl = eviction.get_key_ttl("audit:event:12345")
        assert ttl is not None
        assert ttl == 604800  # 7일

    # =========================================================================
    # get_recommended_redis_config 테스트
    # =========================================================================

    def test_get_recommended_redis_config(self, eviction):
        """권장 Redis 설정."""
        config = eviction.get_recommended_redis_config()

        assert "maxmemory-policy" in config
        assert config["maxmemory-policy"] == "volatile-lru"
        assert "maxmemory-samples" in config

    # =========================================================================
    # check_memory_status 테스트 (Mock)
    # =========================================================================

    def test_check_memory_status_normal(self, eviction):
        """정상 메모리 상태."""
        mock_client = MagicMock()
        mock_client.info.side_effect = [
            {
                "used_memory": 500 * 1024 * 1024,  # 500MB
                "maxmemory": 2 * 1024 * 1024 * 1024,  # 2GB
                "maxmemory_policy": "volatile-lru",
            },
            {"db0": {"keys": 1000, "expires": 800}},
        ]

        status = eviction.check_memory_status(mock_client)

        assert status["status"] == "normal"
        assert status["used_percent"] < 80

    def test_check_memory_status_warning(self, eviction):
        """경고 메모리 상태."""
        mock_client = MagicMock()
        mock_client.info.side_effect = [
            {
                "used_memory": 1700 * 1024 * 1024,  # 1.7GB
                "maxmemory": 2 * 1024 * 1024 * 1024,  # 2GB
                "maxmemory_policy": "volatile-lru",
            },
            {"db0": {"keys": 1000, "expires": 500}},
        ]

        status = eviction.check_memory_status(mock_client)

        assert status["status"] == "warning"
        assert 80 <= status["used_percent"] < 90

    def test_check_memory_status_critical(self, eviction):
        """위험 메모리 상태."""
        mock_client = MagicMock()
        mock_client.info.side_effect = [
            {
                "used_memory": 1900 * 1024 * 1024,  # 1.9GB
                "maxmemory": 2 * 1024 * 1024 * 1024,  # 2GB
                "maxmemory_policy": "volatile-lru",
            },
            {"db0": {"keys": 1000, "expires": 200}},
        ]

        status = eviction.check_memory_status(mock_client)

        assert status["status"] == "critical"
        assert status["used_percent"] >= 90


class TestSingletonAccess:
    """싱글톤 접근 테스트."""

    def test_get_redis_key_guard_returns_same_instance(self):
        """싱글톤이 동일 인스턴스를 반환하는지 확인."""
        guard1 = get_redis_key_guard()
        guard2 = get_redis_key_guard()

        assert guard1 is guard2
