"""
PoolCircuitBreaker v6.2.0/v6.2.1 테스트 - 캐시 기반 Non-Blocking Pool 상태 조회

v6.2.0에서 블로킹 이슈를 해결한 내용:
1. 매 요청마다 check_pool_status() 직접 호출 → 캐시 기반 조회
2. 백그라운드 스레드에서 주기적으로 Pool 상태 갱신 (기본 100ms)
3. should_allow_request()는 캐시된 값만 읽음 (Non-Blocking)

v6.2.1 추가 개선:
1. TTL 범위 검증 (50ms ~ 1000ms)
2. Stale 데이터 단계별 처리 (경고 → 안전 폴백)
3. Audit 연동 (캐시 기반 결정임을 명시)
4. 백그라운드 스레드 자동 재시작

테스트 실행:
    pytest tests/unit/selfhealing/test_pool_circuit_breaker_v620.py -v
"""

import time
import os
import threading
from unittest.mock import patch, MagicMock
import pytest


class TestPoolCircuitBreakerCaching:
    """캐시 기반 Pool 상태 조회 테스트"""

    @pytest.fixture
    def fresh_circuit_breaker(self):
        """각 테스트마다 새로운 인스턴스 생성"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        # 싱글톤 리셋
        PoolCircuitBreaker._instance = None

        cb = PoolCircuitBreaker()
        yield cb

        # 정리: 백그라운드 스레드 중지
        cb._stop_background_refresh()
        PoolCircuitBreaker._instance = None

    def test_cache_initialization(self, fresh_circuit_breaker):
        """캐시 초기화 확인"""
        cb = fresh_circuit_breaker

        # 캐시 관련 속성 존재 확인
        assert hasattr(cb, "_cached_pool_status")
        assert hasattr(cb, "_cache_interval_ms")
        assert hasattr(cb, "_cache_lock")
        assert hasattr(cb, "_background_thread")

        # 기본값 확인
        assert cb._cache_interval_ms == 100  # 기본 100ms

    def test_get_cached_pool_status_is_nonblocking(self, fresh_circuit_breaker):
        """get_cached_pool_status()가 빠른지 확인 (Non-Blocking)"""
        cb = fresh_circuit_breaker

        # 1000회 호출 시간 측정
        start = time.perf_counter()
        for _ in range(1000):
            cb.get_cached_pool_status()
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 1000회에 20ms 이내여야 함 (평균 0.02ms 이하) - 환경 변동 고려
        assert elapsed_ms < 20, f"get_cached_pool_status() too slow: {elapsed_ms}ms for 1000 calls"

    def test_background_thread_starts(self, fresh_circuit_breaker):
        """백그라운드 갱신 스레드가 시작되는지 확인"""
        cb = fresh_circuit_breaker

        # 스레드 존재 확인
        assert cb._background_thread is not None
        assert cb._background_thread.is_alive()
        assert cb._background_thread.name == "PoolCB-Refresh"
        assert cb._background_thread.daemon is True

    # test_background_thread_stops_on_shutdown 삭제됨 - 부하테스트(Locust)에서 검증

    def test_cache_stats_tracking(self, fresh_circuit_breaker):
        """캐시 통계 추적 확인"""
        cb = fresh_circuit_breaker

        initial_hits = cb._stats.get("cache_hits", 0)

        # 10회 캐시 조회
        for _ in range(10):
            cb.get_cached_pool_status()

        # cache_hits 증가 확인
        assert cb._stats["cache_hits"] >= initial_hits + 10

    def test_should_allow_request_uses_cache(self, fresh_circuit_breaker):
        """should_allow_request()가 캐시를 사용하는지 확인"""
        cb = fresh_circuit_breaker

        # 캐시를 정상 상태로 설정
        with cb._cache_lock:
            cb._cached_pool_status = {
                "available": True,
                "is_exhausted": False,
                "is_near_exhaustion": False,
                "checkedout": 1,
                "total_capacity": 5,
                "usage_percent": 20,
                "_cache_time": time.time(),
            }

        # _fetch_pool_status_internal이 호출되지 않아야 함
        with patch.object(cb, "_fetch_pool_status_internal") as mock_fetch:
            allow, reason = cb.should_allow_request()

            # 허용되어야 하고, 실제 fetch는 호출 안됨
            assert allow is True
            assert reason is None
            mock_fetch.assert_not_called()

    # test_circuit_opens_on_cached_exhaustion 삭제됨 - 부하테스트(Locust)에서 검증


class TestPoolCircuitBreakerMiddlewareIntegration:
    """PoolCircuitBreakerMiddleware 통합 테스트"""

    @pytest.fixture
    def mock_request(self):
        """Django Request Mock"""
        request = MagicMock()
        request.path = "/api/products/"
        request.method = "GET"
        return request

    @pytest.fixture
    def mock_response(self):
        """Django Response Mock"""
        response = MagicMock()
        response.status_code = 200
        return response

    def test_middleware_initialization(self):
        """미들웨어 초기화 테스트"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreakerMiddleware

        def get_response(request):
            return MagicMock(status_code=200)

        middleware = PoolCircuitBreakerMiddleware(get_response)

        # 초기화 확인
        assert middleware._request_count == 0
        assert middleware._log_interval == 100  # v6.2.0: 100으로 변경됨

    def test_middleware_excludes_health_paths(self, mock_request, mock_response):
        """health 경로가 제외되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import (
            PoolCircuitBreakerMiddleware,
            pool_circuit_breaker,
        )

        def get_response(request):
            return mock_response

        middleware = PoolCircuitBreakerMiddleware(get_response)

        # Circuit을 OPEN으로 강제 설정
        pool_circuit_breaker._state = pool_circuit_breaker.OPEN

        try:
            # health 경로는 통과해야 함
            mock_request.path = "/health/"
            response = middleware(mock_request)
            assert response.status_code == 200

            # Circuit Breaker 경로도 통과
            mock_request.path = "/api/self-healing/circuit-breaker/"
            response = middleware(mock_request)
            assert response.status_code == 200
        finally:
            # 정리
            pool_circuit_breaker.reset()

    def test_middleware_rejects_when_circuit_open(self, mock_request):
        """Circuit OPEN 시 요청 거부 확인"""
        from selfhealing.api.django.pool_circuit_breaker import (
            PoolCircuitBreakerMiddleware,
            pool_circuit_breaker,
        )

        call_count = 0

        def get_response(request):
            nonlocal call_count
            call_count += 1
            return MagicMock(status_code=200)

        middleware = PoolCircuitBreakerMiddleware(get_response)

        # Circuit을 OPEN으로 설정
        pool_circuit_breaker._state = pool_circuit_breaker.OPEN
        pool_circuit_breaker._open_time = time.time()  # 방금 열림

        try:
            response = middleware(mock_request)

            # 503 반환되어야 함
            assert response.status_code == 503
            # 실제 handler는 호출되지 않음
            assert call_count == 0
        finally:
            pool_circuit_breaker.reset()


class TestPoolCircuitBreakerCacheRefresh:
    """백그라운드 캐시 갱신 테스트"""

    @pytest.fixture
    def fast_refresh_circuit_breaker(self):
        """빠른 갱신 주기의 CB (테스트용)"""
        import os
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        # 싱글톤 리셋
        PoolCircuitBreaker._instance = None

        # 환경변수로 빠른 갱신 설정
        with patch.dict(os.environ, {"POOL_CB_CACHE_INTERVAL_MS": "50"}):
            cb = PoolCircuitBreaker()

        yield cb

        cb._stop_background_refresh()
        PoolCircuitBreaker._instance = None

    # test_cache_refreshes_periodically, test_stale_cache_warning 삭제됨 - 부하테스트(Locust)에서 검증


class TestPoolCircuitBreakerReset:
    """Circuit Breaker Reset 테스트"""

    def test_reset_clears_cache_stats(self):
        """reset()이 캐시 통계도 초기화하는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker as cb

        # 통계 누적
        for _ in range(10):
            cb.get_cached_pool_status()

        assert cb._stats["cache_hits"] > 0

        # 리셋
        cb.reset()

        # 캐시 통계도 리셋되었는지 확인
        assert cb._stats["cache_hits"] == 0
        assert cb._stats["cache_refreshes"] == 0
        assert cb.state == cb.CLOSED


class TestPoolCircuitBreakerStats:
    """통계 API 테스트"""

    def test_get_stats_includes_cache_info(self):
        """get_stats()에 캐시 정보가 포함되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker as cb

        stats = cb.get_stats()

        # v6.2.0: 캐시 관련 정보 포함
        assert "cache_interval_ms" in stats
        assert "cache_hits" in stats["stats"]
        assert "cache_refreshes" in stats["stats"]

    def test_get_stats_includes_stale_stats(self):
        """v6.2.1: get_stats()에 stale 관련 통계가 포함되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker as cb

        stats = cb.get_stats()

        # v6.2.1: stale 관련 통계 포함
        assert "stale_cache_fallbacks" in stats["stats"]
        assert "stale_cache_warnings" in stats["stats"]
        assert "background_thread_restarts" in stats["stats"]


class TestPoolCircuitBreakerV621Improvements:
    """v6.2.1 개선사항 테스트"""

    @pytest.fixture
    def fresh_circuit_breaker_v621(self):
        """v6.2.1 테스트용 CB 인스턴스"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        # 싱글톤 리셋
        PoolCircuitBreaker._instance = None

        cb = PoolCircuitBreaker()
        yield cb

        cb._stop_background_refresh()
        PoolCircuitBreaker._instance = None

    def test_ttl_range_validation_min(self, fresh_circuit_breaker_v621):
        """TTL이 최소값(50ms) 이상으로 제한되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        PoolCircuitBreaker._instance = None

        with patch.dict(os.environ, {"POOL_CB_CACHE_INTERVAL_MS": "10"}):  # 너무 작은 값
            cb = PoolCircuitBreaker()
            try:
                # 50ms로 클램프되어야 함
                assert cb._cache_interval_ms == 50
            finally:
                cb._stop_background_refresh()
                PoolCircuitBreaker._instance = None

    def test_ttl_range_validation_max(self):
        """TTL이 최대값(1000ms) 이하로 제한되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        PoolCircuitBreaker._instance = None

        with patch.dict(os.environ, {"POOL_CB_CACHE_INTERVAL_MS": "5000"}):  # 너무 큰 값
            cb = PoolCircuitBreaker()
            try:
                # 1000ms로 클램프되어야 함
                assert cb._cache_interval_ms == 1000
            finally:
                cb._stop_background_refresh()
                PoolCircuitBreaker._instance = None

    def test_stale_fallback_returns_safe_status(self, fresh_circuit_breaker_v621):
        """Critical stale 시 안전한 폴백 상태를 반환하는지 확인"""
        cb = fresh_circuit_breaker_v621

        # 캐시를 매우 오래된 것으로 설정 (10초 전, critical_stale_ms 기본값 5000ms 초과)
        with cb._cache_lock:
            cb._cached_pool_status = {
                "available": True,
                "is_exhausted": True,  # 원래는 고갈 상태
                "is_near_exhaustion": True,
                "_cache_time": time.time() - 10,  # 10초 전 (5초 임계값 초과)
            }

        # 폴백 상태 조회
        status = cb.get_cached_pool_status()

        # 안전 폴백: exhausted=False로 변경되어야 함 (요청 허용)
        assert status.get("is_exhausted") is False
        assert status.get("_stale_fallback") is True
        assert status.get("_is_stale") is True

    def test_stale_fallback_increments_counter(self, fresh_circuit_breaker_v621):
        """Critical stale 시 stale_cache_fallbacks 카운터가 증가하는지 확인"""
        cb = fresh_circuit_breaker_v621

        initial_fallbacks = cb._stats.get("stale_cache_fallbacks", 0)

        # 캐시를 매우 오래된 것으로 설정
        with cb._cache_lock:
            cb._cached_pool_status["_cache_time"] = time.time() - 10

        cb.get_cached_pool_status()

        assert cb._stats["stale_cache_fallbacks"] > initial_fallbacks

    # test_background_thread_auto_restart 삭제됨 - 부하테스트(Locust)에서 검증

    def test_stale_threshold_multiplier_setting(self):
        """stale_threshold_multiplier 환경변수가 적용되는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreaker

        PoolCircuitBreaker._instance = None

        with patch.dict(os.environ, {"POOL_CB_STALE_MULTIPLIER": "20"}):
            cb = PoolCircuitBreaker()
            try:
                assert cb._stale_threshold_multiplier == 20
            finally:
                cb._stop_background_refresh()
                PoolCircuitBreaker._instance = None


class TestPoolCircuitBreakerAuditIntegration:
    """Audit 연동 테스트"""

    def test_middleware_has_audit_method(self):
        """미들웨어에 _record_rejection_audit 메서드가 있는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreakerMiddleware

        def get_response(request):
            return MagicMock(status_code=200)

        middleware = PoolCircuitBreakerMiddleware(get_response)

        assert hasattr(middleware, "_record_rejection_audit")
        assert callable(middleware._record_rejection_audit)

    def test_middleware_checks_audit_availability(self):
        """미들웨어가 Audit 가용성을 체크하는지 확인"""
        from selfhealing.api.django.pool_circuit_breaker import PoolCircuitBreakerMiddleware

        def get_response(request):
            return MagicMock(status_code=200)

        middleware = PoolCircuitBreakerMiddleware(get_response)

        assert hasattr(middleware, "_audit_enabled")
        assert isinstance(middleware._audit_enabled, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
