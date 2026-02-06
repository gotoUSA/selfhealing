"""
BudgetExhaustedFlagManager 단위 테스트.

테스트 대상:
1. set_exhausted() - 예산 소진 상태 설정
2. is_exhausted() - 예산 소진 상태 조회 (로컬 캐시 → Redis → False)
3. clear() - 특정 SLO 플래그 삭제
4. get_status() - 전체 상태 조회
5. 싱글톤 패턴 (get_budget_exhausted_flag_manager, reset_budget_exhausted_flag_manager)
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.error_budget_gate.redis_flag import (
    BudgetExhaustedFlagManager,
    BUDGET_EXHAUSTED_FLAG_KEY,
    BUDGET_EXHAUSTED_BY_SLO_KEY,
    BUDGET_FLAG_TTL_SECONDS,
    get_budget_exhausted_flag_manager,
    reset_budget_exhausted_flag_manager,
)


class TestBudgetExhaustedFlagManagerConstants:
    """Redis 키 상수 테스트."""

    def test_flag_key_pattern(self):
        """플래그 키 패턴 검증."""
        assert "selfhealing" in BUDGET_EXHAUSTED_FLAG_KEY
        assert "error_budget" in BUDGET_EXHAUSTED_FLAG_KEY

    def test_slo_key_pattern(self):
        """SLO별 키 패턴에 {slo_name} 플레이스홀더 포함."""
        assert "{slo_name}" in BUDGET_EXHAUSTED_BY_SLO_KEY

    def test_ttl_positive(self):
        """TTL이 양수."""
        assert BUDGET_FLAG_TTL_SECONDS > 0


class TestBudgetExhaustedFlagManagerLocalCache:
    """로컬 캐시 동작 테스트 (Redis 없이)."""

    def setup_method(self):
        # Redis 없이 로컬 캐시만 테스트
        self.manager = BudgetExhaustedFlagManager(redis_client=None)

    def test_set_and_get_exhausted(self):
        """로컬 캐시로 set_exhausted/is_exhausted 동작."""
        self.manager.set_exhausted("availability", True)

        assert self.manager.is_exhausted("availability") is True

    def test_set_false_exhausted(self):
        """exhausted=False 설정 시 is_exhausted도 False."""
        self.manager.set_exhausted("availability", True)
        self.manager.set_exhausted("availability", False)

        assert self.manager.is_exhausted("availability") is False

    def test_multiple_slo_names(self):
        """여러 SLO 이름 독립 관리."""
        self.manager.set_exhausted("availability", True)
        self.manager.set_exhausted("latency", False)

        assert self.manager.is_exhausted("availability") is True
        assert self.manager.is_exhausted("latency") is False

    def test_unknown_slo_returns_false(self):
        """조회 적 없는 SLO는 False 반환 (Fail-Open)."""
        assert self.manager.is_exhausted("unknown_slo") is False

    def test_clear_removes_slo(self):
        """clear()로 특정 SLO 플래그 삭제."""
        self.manager.set_exhausted("availability", True)
        self.manager.clear("availability")

        assert self.manager.is_exhausted("availability") is False

    def test_clear_nonexistent_slo_does_not_error(self):
        """존재하지 않는 SLO clear() 시 에러 없음."""
        # 에러 없이 실행되어야 함
        self.manager.clear("nonexistent")


class TestBudgetExhaustedFlagManagerRedis:
    """Redis 연동 테스트 (Mock)."""

    def setup_method(self):
        self.mock_redis = MagicMock()
        self.manager = BudgetExhaustedFlagManager(redis_client=self.mock_redis)

    def test_set_exhausted_writes_to_redis(self):
        """set_exhausted=True 시 Redis setex 호출."""
        self.manager.set_exhausted("availability", True)

        expected_key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        self.mock_redis.setex.assert_called_once_with(expected_key, BUDGET_FLAG_TTL_SECONDS, "1")

    def test_set_not_exhausted_deletes_from_redis(self):
        """set_exhausted=False 시 Redis delete 호출."""
        self.manager.set_exhausted("availability", False)

        expected_key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        self.mock_redis.delete.assert_called_once_with(expected_key)

    def test_is_exhausted_reads_from_redis_on_cache_miss(self):
        """로컬 캐시 미스 시 Redis에서 조회."""
        self.mock_redis.get.return_value = b"1"

        # 캐시 만료를 시뮬레이션하기 위해 새 manager 생성
        manager = BudgetExhaustedFlagManager(redis_client=self.mock_redis)

        result = manager.is_exhausted("availability")

        expected_key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        self.mock_redis.get.assert_called_with(expected_key)
        assert result is True

    def test_is_exhausted_returns_false_when_redis_returns_none(self):
        """Redis가 None 반환 시 False."""
        self.mock_redis.get.return_value = None

        manager = BudgetExhaustedFlagManager(redis_client=self.mock_redis)
        result = manager.is_exhausted("availability")

        assert result is False

    def test_is_exhausted_uses_local_cache_first(self):
        """로컬 캐시가 유효하면 Redis 조회 않음."""
        self.manager.set_exhausted("availability", True)

        # Redis get은 호출되지 않아야 함
        self.mock_redis.get.reset_mock()
        result = self.manager.is_exhausted("availability")

        assert result is True
        self.mock_redis.get.assert_not_called()

    def test_clear_deletes_from_redis(self):
        """clear() 시 Redis delete 호출."""
        self.manager.clear("availability")

        expected_key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name="availability")
        self.mock_redis.delete.assert_called_once_with(expected_key)


class TestBudgetExhaustedFlagManagerFailOpen:
    """Fail-Open 동작 테스트."""

    def test_redis_write_failure_does_not_raise(self):
        """Redis 쓰기 실패 시 예외 않고 로컬만 업데이트."""
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = Exception("Redis unavailable")

        manager = BudgetExhaustedFlagManager(redis_client=mock_redis)

        # 예외 없이 실행
        manager.set_exhausted("availability", True)

        # 로컬 캐시는 업데이트됨
        assert manager.is_exhausted("availability") is True

    def test_redis_read_failure_returns_false(self):
        """Redis 읽기 실패 시 False 반환 (Fail-Open)."""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis unavailable")

        manager = BudgetExhaustedFlagManager(redis_client=mock_redis)

        # 로컬 캐시 없는 상태에서 조회
        result = manager.is_exhausted("new_slo")

        assert result is False


class TestBudgetExhaustedFlagManagerGetStatus:
    """get_status() 메서드 테스트."""

    def test_get_status_returns_dict(self):
        """get_status() 반환 타입 및 키 검증."""
        manager = BudgetExhaustedFlagManager(redis_client=None)
        manager.set_exhausted("availability", True)
        manager.set_exhausted("latency", False)

        status = manager.get_status()

        assert isinstance(status, dict)
        assert "local_cache_size" in status
        assert "cached_slos" in status
        assert "availability" in status["cached_slos"]
        assert "latency" in status["cached_slos"]

    def test_get_status_reflects_cache_size(self):
        """get_status()가 로컬 캐시 크기 반영."""
        manager = BudgetExhaustedFlagManager(redis_client=None)
        manager.set_exhausted("availability", True)
        manager.set_exhausted("latency", False)

        status = manager.get_status()

        assert status["local_cache_size"] == 2
        assert len(status["cached_slos"]) == 2


class TestBudgetExhaustedFlagManagerSingleton:
    """싱글톤 패턴 테스트."""

    def teardown_method(self):
        reset_budget_exhausted_flag_manager()

    def test_get_returns_same_instance(self):
        """get_budget_exhausted_flag_manager()는 같은 인스턴스 반환."""
        manager1 = get_budget_exhausted_flag_manager()
        manager2 = get_budget_exhausted_flag_manager()

        assert manager1 is manager2

    def test_reset_creates_new_instance(self):
        """reset 후 새 인스턴스 생성."""
        manager1 = get_budget_exhausted_flag_manager()
        reset_budget_exhausted_flag_manager()
        manager2 = get_budget_exhausted_flag_manager()

        assert manager1 is not manager2

    def test_reset_clears_state(self):
        """reset 후 상태 초기화."""
        manager1 = get_budget_exhausted_flag_manager()
        manager1.set_exhausted("availability", True)

        reset_budget_exhausted_flag_manager()

        manager2 = get_budget_exhausted_flag_manager()
        assert manager2.is_exhausted("availability") is False
