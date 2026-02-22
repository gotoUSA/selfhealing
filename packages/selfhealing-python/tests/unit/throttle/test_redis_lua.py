"""
Redis Lua 스크립트 테스트.

Phase 6: Redis 원자적 업데이트 테스트
- Lua 스크립트 문법 검증
- 원자적 limit 업데이트
- CAS (Compare-And-Swap) 동작
"""

from unittest.mock import MagicMock

from selfhealing.services.throttle.redis_lua import (
    RedisThrottleLimitManager,
    ThrottleLuaScripts,
)


class TestThrottleLuaScripts:
    """Lua 스크립트 정의 테스트."""

    def test_atomic_limit_update_script_exists(self):
        """원자적 limit 업데이트 스크립트 존재 확인."""
        script = ThrottleLuaScripts.LUA_ATOMIC_LIMIT_UPDATE
        assert script is not None
        assert "new_limit" in script
        assert "min_limit" in script
        assert "max_limit" in script
        assert "KEYS[1]" in script

    def test_cas_limit_update_script_exists(self):
        """CAS 업데이트 스크립트 존재 확인."""
        script = ThrottleLuaScripts.LUA_CAS_LIMIT_UPDATE
        assert script is not None
        assert "expected" in script
        assert "MISMATCH" in script

    def test_load_safe_limit_script_exists(self):
        """안전 limit 로드 스크립트 존재 확인."""
        script = ThrottleLuaScripts.LUA_LOAD_SAFE_LIMIT
        assert script is not None
        assert "SAFE" in script
        assert "DEFAULT" in script
        assert "CURRENT" in script

    def test_add_rtt_sample_script_exists(self):
        """RTT 샘플 추가 스크립트 존재 확인."""
        script = ThrottleLuaScripts.LUA_ADD_RTT_SAMPLE
        assert script is not None
        assert "ZADD" in script
        assert "ZREMRANGEBYSCORE" in script


class TestRedisThrottleLimitManager:
    """Redis Throttle Limit 관리자 테스트."""

    def setup_method(self):
        """각 테스트 전 Mock Redis 설정."""
        self.mock_redis = MagicMock()
        self.manager = RedisThrottleLimitManager(
            redis_client=self.mock_redis,
            key_prefix="test:",
        )

    def test_get_limit_key(self):
        """limit 키 생성 확인."""
        key = self.manager._get_limit_key("payment_api")
        assert key == "test:throttle:limit:payment_api"

    def test_get_safe_limit_key(self):
        """안전 limit 키 생성 확인."""
        key = self.manager._get_safe_limit_key("payment_api")
        assert key == "test:throttle:last_safe_limit:payment_api"

    def test_get_rtt_key(self):
        """RTT 키 생성 확인."""
        key = self.manager._get_rtt_key("payment_api")
        assert key == "test:throttle:rtt:payment_api"

    def test_update_limit_atomic(self):
        """원자적 limit 업데이트."""
        # evalsha 결과 모킹
        self.mock_redis.evalsha.return_value = [50, 80]
        self.mock_redis.script_load.return_value = "fake_sha"

        prev, new = self.manager.update_limit_atomic(
            service_name="payment_api",
            new_limit=80,
            min_limit=10,
            max_limit=100,
            save_as_safe=True,
        )

        assert prev == 50
        assert new == 80

    def test_update_limit_cas_success(self):
        """CAS 성공 케이스."""
        self.mock_redis.evalsha.return_value = [1, 80, b"OK"]
        self.mock_redis.script_load.return_value = "fake_sha"

        success, value, msg = self.manager.update_limit_cas(
            service_name="payment_api",
            expected_current=50,
            new_limit=80,
            min_limit=10,
            max_limit=100,
        )

        assert success is True
        assert value == 80
        assert msg == "OK"

    def test_update_limit_cas_mismatch(self):
        """CAS 불일치 케이스."""
        self.mock_redis.evalsha.return_value = [0, 60, b"MISMATCH"]
        self.mock_redis.script_load.return_value = "fake_sha"

        success, value, msg = self.manager.update_limit_cas(
            service_name="payment_api",
            expected_current=50,
            new_limit=80,
            min_limit=10,
            max_limit=100,
        )

        assert success is False
        assert value == 60  # 실제 현재 값
        assert msg == "MISMATCH"

    def test_load_safe_limit_from_current(self):
        """현재 limit에서 로드."""
        self.mock_redis.evalsha.return_value = [100, b"CURRENT"]
        self.mock_redis.script_load.return_value = "fake_sha"

        limit, source = self.manager.load_safe_limit(
            service_name="payment_api",
            default_limit=50,
        )

        assert limit == 100
        assert source == "CURRENT"

    def test_load_safe_limit_from_safe(self):
        """안전 limit에서 로드."""
        self.mock_redis.evalsha.return_value = [80, b"SAFE", "1234567890.123"]
        self.mock_redis.script_load.return_value = "fake_sha"

        limit, source = self.manager.load_safe_limit(
            service_name="payment_api",
            default_limit=50,
        )

        assert limit == 80
        assert source == "SAFE"

    def test_load_safe_limit_default(self):
        """기본값 사용."""
        self.mock_redis.evalsha.return_value = [50, b"DEFAULT"]
        self.mock_redis.script_load.return_value = "fake_sha"

        limit, source = self.manager.load_safe_limit(
            service_name="payment_api",
            default_limit=50,
        )

        assert limit == 50
        assert source == "DEFAULT"

    def test_save_safe_limit(self):
        """안전 limit 저장."""
        self.mock_redis.hset.return_value = 1

        result = self.manager.save_safe_limit("payment_api", 100)

        assert result is True
        self.mock_redis.hset.assert_called_once()
        self.mock_redis.expire.assert_called_once()

    def test_get_current_limit(self):
        """현재 limit 조회."""
        self.mock_redis.get.return_value = b"75"

        limit = self.manager.get_current_limit("payment_api")

        assert limit == 75

    def test_get_current_limit_none(self):
        """limit 없을 때 None 반환."""
        self.mock_redis.get.return_value = None

        limit = self.manager.get_current_limit("payment_api")

        assert limit is None

    def test_add_rtt_sample(self):
        """RTT 샘플 추가."""
        self.mock_redis.evalsha.return_value = 5
        self.mock_redis.script_load.return_value = "fake_sha"

        count = self.manager.add_rtt_sample(
            service_name="payment_api",
            rtt_ms=45.5,
        )

        assert count == 5

    def test_get_rtt_samples(self):
        """RTT 샘플 조회."""
        self.mock_redis.zrangebyscore.return_value = [
            b"45.5:1234567890.123",
            b"50.2:1234567890.456",
        ]

        samples = self.manager.get_rtt_samples("payment_api")

        assert len(samples) == 2
        assert 45.5 in samples
        assert 50.2 in samples

    def test_script_fallback_on_noscript(self):
        """NOSCRIPT 에러 시 eval 폴백."""
        # evalsha 실패, eval 성공 시뮬레이션
        self.mock_redis.evalsha.side_effect = Exception("NOSCRIPT No matching script")
        self.mock_redis.eval.return_value = [50, 80]
        self.mock_redis.script_load.return_value = "fake_sha"

        prev, new = self.manager.update_limit_atomic(
            service_name="payment_api",
            new_limit=80,
            min_limit=10,
            max_limit=100,
        )

        assert prev == 50
        assert new == 80
        self.mock_redis.eval.assert_called_once()
