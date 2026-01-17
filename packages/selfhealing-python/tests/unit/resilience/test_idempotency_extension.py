"""
IdempotencyDomain 및 IdempotencyKey 확장 테스트

순위 4, 5, 5.3 구현 테스트:
- IdempotencyDomain 신규 추가 (CHAOS_EXPERIMENT, CONFIG_CHANGE, L2_SYNC, WAL_RECOVERY, AUTO_ADJUSTMENT)
- IdempotencyKey 팩토리 메서드 (for_chaos_experiment, for_config_change, for_l2_sync, for_wal_recovery, for_auto_adjustment)
- AntiFlappingWindow (메모리 기반, Redis 분산 캐싱)
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.idempotency_service import (
    AntiFlappingWindow,
    IdempotencyDomain,
    IdempotencyKey,
    get_anti_flapping_window,
)


# =============================================================================
# IdempotencyDomain Tests (순위 4)
# =============================================================================


class TestIdempotencyDomainExtension:
    """IdempotencyDomain 확장 테스트."""

    def test_new_domains_exist(self):
        """
        Purpose:
            신규 IdempotencyDomain이 정의되어 있는지 확인.
        """
        new_domains = [
            "CHAOS_EXPERIMENT",
            "CONFIG_CHANGE",
            "L2_SYNC",
            "WAL_RECOVERY",
            "AUTO_ADJUSTMENT",
        ]

        for domain_name in new_domains:
            assert hasattr(IdempotencyDomain, domain_name), f"Missing: {domain_name}"

    def test_existing_domains_still_exist(self):
        """
        Purpose:
            기존 IdempotencyDomain이 유지되는지 확인 (하위 호환성).
        """
        existing_domains = [
            "EXTERNAL_SERVICE",
            "INTERNAL_PROCESS",
            "ASYNC_TASK",
            "EVENT",
            "CUSTOM",
        ]

        for domain_name in existing_domains:
            assert hasattr(IdempotencyDomain, domain_name), f"Missing existing domain: {domain_name}"

    def test_domain_values_are_strings(self):
        """
        Purpose:
            모든 도메인 값이 snake_case 문자열인지 확인.
        """
        for domain in IdempotencyDomain:
            assert isinstance(domain.value, str)
            assert domain.value == domain.value.lower()


# =============================================================================
# IdempotencyKey Factory Tests (순위 5)
# =============================================================================


class TestIdempotencyKeyFactories:
    """IdempotencyKey 팩토리 메서드 테스트."""

    def test_for_chaos_experiment(self):
        """
        Purpose:
            for_chaos_experiment 팩토리가 올바르게 동작하는지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="sched-123",
            experiment_type="latency_injection",
            target_service="payment",
        )

        assert key.domain == IdempotencyDomain.CHAOS_EXPERIMENT
        assert "sched-123" in key.key
        assert "latency_injection" in key.key
        assert "payment" in key.key
        assert key.components["schedule_id"] == "sched-123"
        assert key.components["experiment_type"] == "latency_injection"
        assert key.components["target_service"] == "payment"

    def test_for_config_change_basic(self):
        """
        Purpose:
            for_config_change 기본 동작 확인.
        """
        key = IdempotencyKey.for_config_change(
            config_key="max_retries",
            new_value_hash="abc123",
            changed_by="admin",
        )

        assert key.domain == IdempotencyDomain.CONFIG_CHANGE
        assert "max_retries" in key.key
        assert "abc123" in key.key
        assert key.components["config_key"] == "max_retries"
        assert key.components["changed_by"] == "admin"

    def test_for_config_change_with_request_id(self):
        """
        Purpose:
            request_id가 제공되면 요청 단위 멱등성 키가 생성되는지 확인.
        """
        key = IdempotencyKey.for_config_change(
            config_key="timeout",
            new_value_hash="xyz789",
            changed_by="system",
            request_id="req-456",
        )

        # request_id 기반 키
        assert "req-456" in key.key
        # new_value_hash는 키에 포함되지 않음 (request_id가 우선)
        assert key.components["request_id"] == "req-456"

    def test_for_config_change_with_window_id(self):
        """
        Purpose:
            window_id가 제공되면 슬라이딩 윈도우 기반 키가 생성되는지 확인.
        """
        key = IdempotencyKey.for_config_change(
            config_key="threshold",
            new_value_hash="hash123",
            changed_by="auto_tuner",
            window_id="w001",
        )

        assert "w001" in key.key
        assert "hash123" in key.key
        assert key.components["window_id"] == "w001"

    def test_for_l2_sync(self):
        """
        Purpose:
            for_l2_sync 팩토리가 올바르게 동작하는지 확인.
        """
        key = IdempotencyKey.for_l2_sync(
            service_name="payment",
            record_id="rec-456",
            intended_state="open",
        )

        assert key.domain == IdempotencyDomain.L2_SYNC
        assert "payment" in key.key
        assert "rec-456" in key.key
        assert key.components["service_name"] == "payment"
        assert key.components["record_id"] == "rec-456"
        assert key.components["intended_state"] == "open"

    def test_for_wal_recovery(self):
        """
        Purpose:
            for_wal_recovery 팩토리가 올바르게 동작하는지 확인.
        """
        key = IdempotencyKey.for_wal_recovery(
            wal_entry_id="wal-789",
            operation="replay",
        )

        assert key.domain == IdempotencyDomain.WAL_RECOVERY
        assert "wal-789" in key.key
        assert "replay" in key.key
        assert key.components["wal_entry_id"] == "wal-789"
        assert key.components["operation"] == "replay"

    def test_for_auto_adjustment(self):
        """
        Purpose:
            for_auto_adjustment 팩토리가 올바르게 동작하는지 확인.
        """
        key = IdempotencyKey.for_auto_adjustment(
            module="circuit_breaker",
            parameter="failure_threshold",
            target_value="0.5",
        )

        assert key.domain == IdempotencyDomain.AUTO_ADJUSTMENT
        assert "circuit_breaker" in key.key
        assert "failure_threshold" in key.key
        assert "0.5" in key.key
        assert key.components["module"] == "circuit_breaker"
        assert key.components["parameter"] == "failure_threshold"
        assert key.components["target_value"] == "0.5"

    def test_cache_key_format(self):
        """
        Purpose:
            cache_key가 올바른 형식으로 생성되는지 확인.
        """
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="test",
            experiment_type="test_type",
            target_service="svc",
        )

        assert key.cache_key.startswith("idempotency:")
        assert "chaos_experiment" in key.cache_key


# =============================================================================
# AntiFlappingWindow Tests (순위 5, 5.3)
# =============================================================================


class TestAntiFlappingWindowMemory:
    """AntiFlappingWindow 메모리 모드 테스트."""

    @pytest.fixture
    def window(self):
        """메모리 기반 AntiFlappingWindow 생성."""
        return AntiFlappingWindow(
            window_seconds=60,
            similarity_threshold=0.01,
            max_similar_changes=3,
            use_redis=False,  # 메모리 모드
        )

    def test_no_flapping_with_few_changes(self, window):
        """
        Purpose:
            max_similar_changes 미만의 변경은 플래핑이 아닌지 확인.
        """
        key = "test:param"

        # 2번 변경 (임계값 3 미만)
        is_flapping1, _ = window.check_and_record(key, 1.0)
        is_flapping2, _ = window.check_and_record(key, 1.0)

        assert is_flapping1 is False
        assert is_flapping2 is False

    def test_flapping_detected_with_similar_values(self, window):
        """
        Purpose:
            유사한 값이 max_similar_changes 이상 반복되면 플래핑 감지.
            
        Note:
            max_similar_changes=3일 때, 이미 윈도우에 3개가 있으면 4번째 호출 시 감지됨.
        """
        key = "test:threshold"

        # 동일 값으로 4번 변경 (3개가 이미 윈도우에 있고, 4번째에서 감지)
        window.check_and_record(key, 0.5)
        window.check_and_record(key, 0.5)
        window.check_and_record(key, 0.5)
        is_flapping, reason = window.check_and_record(key, 0.5)

        assert is_flapping is True
        assert "Flapping detected" in reason
        assert "3" in reason

    def test_similar_values_within_threshold(self, window):
        """
        Purpose:
            1% 이내의 유사한 값이 동일하게 취급되는지 확인.
        """
        key = "test:similarity"

        # 1% 이내로 유사한 값들 (4번 호출 - 4번째에서 감지)
        window.check_and_record(key, 100.0)
        window.check_and_record(key, 100.5)  # 0.5% 차이
        window.check_and_record(key, 100.8)  # 0.8% 차이
        is_flapping, _ = window.check_and_record(key, 100.3)  # 0.3% 차이

        assert is_flapping is True

    def test_different_values_not_flapping(self, window):
        """
        Purpose:
            1% 초과로 다른 값은 플래핑으로 간주되지 않는지 확인.
        """
        key = "test:different"

        # 1% 초과로 다른 값들
        window.check_and_record(key, 100.0)
        window.check_and_record(key, 105.0)  # 5% 차이
        is_flapping, _ = window.check_and_record(key, 110.0)  # 10% 차이

        assert is_flapping is False

    def test_sliding_window_expiry(self, window):
        """
        Purpose:
            윈도우 시간이 지나면 이전 기록이 만료되는지 확인.
        """
        # 짧은 윈도우로 설정
        short_window = AntiFlappingWindow(
            window_seconds=1,  # 1초
            similarity_threshold=0.01,
            max_similar_changes=3,
            use_redis=False,
        )

        key = "test:expiry"

        # 2번 기록
        short_window.check_and_record(key, 1.0)
        short_window.check_and_record(key, 1.0)

        # 윈도우 만료 대기
        time.sleep(1.1)

        # 이전 기록은 만료, 다시 시작
        is_flapping, _ = short_window.check_and_record(key, 1.0)
        assert is_flapping is False

    def test_clear_window(self, window):
        """
        Purpose:
            clear_window가 윈도우를 비우는지 확인.
        """
        key = "test:clear"

        # 기록 추가
        window.check_and_record(key, 1.0)
        window.check_and_record(key, 1.0)

        # 클리어
        result = window.clear_window(key)
        assert result is True

        # 클리어 후 새로 시작
        is_flapping, _ = window.check_and_record(key, 1.0)
        assert is_flapping is False

    def test_zero_value_handling(self, window):
        """
        Purpose:
            0 값 처리가 올바른지 확인.
        """
        key = "test:zero"

        # 0 값 반복 (4번 호출 - 4번째에서 감지)
        window.check_and_record(key, 0.0)
        window.check_and_record(key, 0.0)
        window.check_and_record(key, 0.0)
        is_flapping, _ = window.check_and_record(key, 0.0)

        assert is_flapping is True

    def test_different_keys_isolated(self, window):
        """
        Purpose:
            다른 키는 독립적으로 추적되는지 확인.
        """
        # 키 A에 4번 기록 (4번째에서 감지)
        window.check_and_record("key_a", 1.0)
        window.check_and_record("key_a", 1.0)
        window.check_and_record("key_a", 1.0)
        is_flapping_a, _ = window.check_and_record("key_a", 1.0)

        # 키 B에 1번만 기록
        is_flapping_b, _ = window.check_and_record("key_b", 1.0)

        assert is_flapping_a is True
        assert is_flapping_b is False


class TestAntiFlappingWindowRedis:
    """AntiFlappingWindow Redis 모드 테스트."""

    @pytest.fixture
    def mock_redis_client(self):
        """Mock Redis 클라이언트."""
        return MagicMock()

    @pytest.fixture
    def window_with_redis(self, mock_redis_client):
        """Redis 모드 AntiFlappingWindow (mocked)."""
        window = AntiFlappingWindow(
            window_seconds=60,
            similarity_threshold=0.01,
            max_similar_changes=3,
            use_redis=True,
        )
        window._redis_client = mock_redis_client
        return window

    def test_redis_zset_operations(self, window_with_redis, mock_redis_client):
        """
        Purpose:
            Redis ZSET 기반 연산이 호출되는지 확인.
        """
        # Pipeline mock 설정
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [
            None,  # zremrangebyscore 결과
            [],  # zrangebyscore 결과 (빈 윈도우)
        ]
        mock_redis_client.pipeline.return_value = mock_pipe

        # 테스트 실행
        is_flapping, _ = window_with_redis.check_and_record("test:key", 1.0)

        # ZSET 연산 검증
        mock_pipe.zremrangebyscore.assert_called_once()
        mock_pipe.zrangebyscore.assert_called_once()
        mock_redis_client.zadd.assert_called_once()
        mock_redis_client.expire.assert_called_once()

        assert is_flapping is False

    def test_redis_flapping_detection(self, window_with_redis, mock_redis_client):
        """
        Purpose:
            Redis 모드에서 플래핑이 감지되는지 확인.
        """
        now = time.time()
        # Pipeline mock - 이미 3개의 유사한 값이 있음
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [
            None,
            [
                (f"{now-10}:1.0", now - 10),
                (f"{now-5}:1.005", now - 5),  # 0.5% 차이
                (f"{now-2}:0.995", now - 2),  # 0.5% 차이
            ],
        ]
        mock_redis_client.pipeline.return_value = mock_pipe

        is_flapping, reason = window_with_redis.check_and_record("test:key", 1.0)

        assert is_flapping is True
        assert "Flapping detected" in reason

    def test_redis_fallback_on_error(self, window_with_redis, mock_redis_client):
        """
        Purpose:
            Redis 오류 시 메모리 모드로 폴백되는지 확인.
        """
        # Redis 오류 발생
        mock_redis_client.pipeline.side_effect = Exception("Redis connection error")

        # 메모리 폴백이 동작해야 함
        is_flapping, _ = window_with_redis.check_and_record("test:key", 1.0)

        # 폴백으로 동작하므로 False (첫 번째 기록)
        assert is_flapping is False


class TestGetAntiFlappingWindow:
    """get_anti_flapping_window 싱글톤 테스트."""

    def test_returns_singleton(self):
        """
        Purpose:
            get_anti_flapping_window가 싱글톤을 반환하는지 확인.
        """
        window1 = get_anti_flapping_window()
        window2 = get_anti_flapping_window()

        assert window1 is window2
        assert isinstance(window1, AntiFlappingWindow)
