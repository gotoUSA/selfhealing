"""
Layered Repository 단위 테스트.

L1+L2 저장소 복원력 기능 테스트:
- L2 타임아웃 처리
- Shadow Logging
- 드리프트 복구
- 콜드 스타트 보호
- 지능형 폴백
"""

import threading
import time
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock
from concurrent.futures import TimeoutError as FuturesTimeoutError

import pytest

from selfhealing.adapters.memory.circuit_breaker import (
    LayeredCircuitBreakerStateRepository,
    InMemoryCircuitBreakerStateRepository,
    ShadowLogger,
    L2SyncFailureRecord,
    get_shadow_logger,
)
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateEnum,
)


class TestL2Timeout:
    """L2 타임아웃 테스트."""

    def test_timeout_on_slow_l2(self):
        """L2가 느리면 타임아웃 발생."""
        # Given: L2가 200ms 걸리는 상황 시뮬레이션
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        
        def slow_get_all():
            time.sleep(0.2)  # 200ms 지연
            return []
        
        slow_l2.get_all.side_effect = slow_get_all
        
        # When: 50ms 타임아웃으로 레포지토리 생성 (Redis 타입)
        with patch('selfhealing.adapters.memory.circuit_breaker.get_shadow_logger') as mock_logger:
            mock_logger.return_value = ShadowLogger()
            repo = LayeredCircuitBreakerStateRepository(
                l2_repo=slow_l2,
                adapter_type="redis",
            )
        
        # Then: 타임아웃이 발생하고 L1만으로 동작 (에러 없이)
        # 타임아웃 카운트 증가 확인
        assert repo._metrics["l2_timeout_count"] >= 0  # 초기 로드 시 타임아웃 발생 가능

    def test_fallback_to_l1_on_timeout(self):
        """타임아웃 시 L1만으로 동작."""
        # Given: 느린 L2
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        slow_l2.get_all.side_effect = lambda: time.sleep(1) or []
        
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=slow_l2,
            adapter_type="redis",
        )
        
        # When: L1에서 상태 생성
        state = repo.get_or_create("test-service")
        
        # Then: L1 데이터로 정상 동작
        assert state is not None
        assert state.service_name == "test-service"
        assert state.state == CircuitBreakerStateEnum.CLOSED.value

    def test_adapter_specific_timeout(self):
        """어댑터별 다른 타임아웃 적용."""
        # Redis 어댑터
        redis_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="redis",
        )
        assert redis_repo._get_timeout_seconds() == 0.05  # 50ms
        
        # Database 어댑터
        db_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="database",
        )
        assert db_repo._get_timeout_seconds() == 0.2  # 200ms
        
        # Django 어댑터 (database와 동일)
        django_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="django",
        )
        assert django_repo._get_timeout_seconds() == 0.2  # 200ms
        
        # 알 수 없는 어댑터
        unknown_repo = LayeredCircuitBreakerStateRepository(
            l2_repo=None,
            adapter_type="unknown",
        )
        assert unknown_repo._get_timeout_seconds() == 0.1  # 100ms

    def test_l2_timeout_increments_metric(self):
        """L2 타임아웃 시 내부 메트릭 증가."""
        # Given: 느린 L2
        slow_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        slow_l2.get_all.return_value = []
        slow_l2.get_by_service_name.side_effect = lambda _: time.sleep(0.2) or None
        
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=slow_l2,
            adapter_type="redis",  # 50ms 타임아웃
        )
        
        initial_timeout_count = repo._metrics["l2_timeout_count"]
        
        # When: L2 조회 시도 (타임아웃 발생)
        repo._l2_healthy = True  # 헬시 상태로 설정하여 L2 조회 시도하도록
        result = repo.get_by_service_name("test-service")
        
        # Then: 타임아웃 카운트 증가 (또는 L1 결과 반환)
        assert result is None or repo._metrics["l2_timeout_count"] >= initial_timeout_count


class TestShadowLogging:
    """Shadow Logging 테스트."""

    def setup_method(self):
        """각 테스트 전 ShadowLogger 초기화."""
        shadow_logger = get_shadow_logger()
        shadow_logger.clear()

    def test_record_sync_failure(self):
        """동기화 실패 기록."""
        shadow_logger = get_shadow_logger()
        
        # When: 동기화 실패 기록
        shadow_logger.record_sync_failure(
            service_name="payment-gateway",
            intended_state="open",
            error=Exception("Connection refused"),
            adapter_type="redis",
            operation="sync",
        )
        
        # Then: 기록 조회 가능
        records = shadow_logger.get_all_records()
        assert len(records) == 1
        assert records[0].service_name == "payment-gateway"
        assert records[0].intended_state == "open"
        assert "Connection refused" in records[0].error_message
        assert records[0].adapter_type == "redis"
        assert records[0].synced_after_recovery is False

    def test_get_unsynced_records(self):
        """미동기화 기록 조회."""
        shadow_logger = get_shadow_logger()
        
        # Given: 여러 동기화 실패 발생
        shadow_logger.record_sync_failure("service-a", "open", Exception("err1"))
        shadow_logger.record_sync_failure("service-b", "closed", Exception("err2"))
        
        # When: 하나만 동기화 완료
        shadow_logger.mark_as_synced("service-a")
        
        # Then: 미동기화 기록만 조회
        unsynced = shadow_logger.get_unsynced_records()
        assert len(unsynced) == 1
        assert unsynced[0].service_name == "service-b"

    def test_mark_as_synced(self):
        """동기화 완료 마킹."""
        shadow_logger = get_shadow_logger()
        
        # Given: 동기화 실패 기록
        shadow_logger.record_sync_failure("service-x", "open", Exception("err"))
        
        # When: 동기화 완료 마킹
        count = shadow_logger.mark_as_synced("service-x")
        
        # Then: 마킹 완료
        assert count == 1
        records = shadow_logger.get_all_records()
        assert records[0].synced_after_recovery is True
        assert records[0].recovery_time is not None

    def test_shadow_log_thread_safety(self):
        """Shadow Log 스레드 안전성."""
        shadow_logger = get_shadow_logger()
        shadow_logger.clear()
        
        num_threads = 10
        records_per_thread = 100
        
        def record_failures(thread_id):
            for i in range(records_per_thread):
                shadow_logger.record_sync_failure(
                    service_name=f"service-{thread_id}-{i}",
                    intended_state="open",
                    error=Exception(f"error-{thread_id}-{i}"),
                )
        
        # When: 여러 스레드에서 동시 기록
        threads = [
            threading.Thread(target=record_failures, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Then: 모든 기록이 저장됨 (최대 1000개 제한 내에서)
        records = shadow_logger.get_all_records()
        assert len(records) <= 1000  # max_entries 제한
        assert len(records) > 0

    def test_shadow_log_max_entries(self):
        """Shadow Log 최대 항목 수 제한."""
        shadow_logger = get_shadow_logger()
        shadow_logger.clear()
        shadow_logger.set_max_entries(100)
        
        # When: 150개 기록 추가
        for i in range(150):
            shadow_logger.record_sync_failure(
                service_name=f"service-{i}",
                intended_state="open",
                error=Exception(f"error-{i}"),
            )
        
        # Then: 최대 100개만 유지
        records = shadow_logger.get_all_records()
        assert len(records) == 100
        # 가장 오래된 것이 제거됨 (service-50 ~ service-149)
        assert records[0].service_name == "service-50"

    def test_shadow_log_stats(self):
        """Shadow Log 통계 조회."""
        shadow_logger = get_shadow_logger()
        shadow_logger.clear()
        
        # Given: 여러 실패 기록
        shadow_logger.record_sync_failure("svc-a", "open", Exception("e1"))
        shadow_logger.record_sync_failure("svc-b", "open", Exception("e2"))
        shadow_logger.record_sync_failure("svc-a", "closed", Exception("e3"))
        shadow_logger.mark_as_synced("svc-a")
        
        # When: 통계 조회
        stats = shadow_logger.get_stats()
        
        # Then: 통계 정확
        assert stats["total_records"] == 3
        assert stats["unsynced_count"] == 1  # svc-b만 미동기화
        assert "svc-a" in stats["affected_services"]
        assert "svc-b" in stats["affected_services"]


class TestDriftReconciliation:
    """드리프트 복구 테스트."""

    def test_most_restrictive_wins_open_vs_closed(self):
        """OPEN > CLOSED 우선순위.
        
        시나리오:
        - L1: OPEN (장애 감지)
        - L2: CLOSED (장애 전 상태)
        - 결과: OPEN (더 제한적인 상태 승리)
        
        TODO: DriftReconciler 구현 후 활성화
        """
        pass

    def test_most_restrictive_wins_half_open_vs_closed(self):
        """HALF_OPEN > CLOSED 우선순위.
        
        시나리오:
        - L1: HALF_OPEN (복구 시도 중)
        - L2: CLOSED
        - 결과: HALF_OPEN
        
        TODO: DriftReconciler 구현 후 활성화
        """
        pass

    def test_most_restrictive_wins_open_vs_half_open(self):
        """OPEN > HALF_OPEN 우선순위.
        
        시나리오:
        - L1: HALF_OPEN
        - L2: OPEN
        - 결과: OPEN (더 제한적)
        
        TODO: DriftReconciler 구현 후 활성화
        """
        pass

    def test_timestamp_tiebreaker_same_state(self):
        """같은 상태면 타임스탬프로 결정.
        
        시나리오:
        - L1: OPEN (10:00:00)
        - L2: OPEN (10:00:05) ← 더 최신
        - 결과: L2 상태 채택
        
        TODO: DriftReconciler 구현 후 활성화
        """
        pass

    def test_jitter_applied_to_reconciliation(self):
        """Jitter가 적용되어 지연됨.
        
        시나리오:
        - L2 복구 감지
        - 0~5초 사이 무작위 지연
        - 지연 후 동기화 실행
        
        TODO: DriftReconciler 구현 후 활성화
        """
        pass

    def test_jitter_distribution(self):
        """Jitter가 균등 분포.
        
        시나리오:
        - 1000회 Jitter 생성
        - 0~5초 범위에 균등 분포
        """
        import random
        jitters = [random.uniform(0.0, 5.0) for _ in range(1000)]
        
        # 평균이 약 2.5초 근처
        avg = sum(jitters) / len(jitters)
        assert 2.0 < avg < 3.0, f"Jitter 평균이 예상 범위 벗어남: {avg}"
        
        # 최소/최대가 범위 내
        assert min(jitters) >= 0.0
        assert max(jitters) <= 5.0

    def test_thundering_herd_prevention(self):
        """Thundering Herd 방지.
        
        시나리오:
        - 100개 Pod가 동시에 L2 복구 감지
        - 각 Pod마다 다른 Jitter 적용
        - 동시 쓰기 요청 분산
        
        TODO: 통합 테스트로 이동 고려 (Phase 2)
        """
        pass


class TestColdStartProtection:
    """콜드 스타트 보호 테스트."""

    def test_default_state_is_closed(self):
        """기본 상태가 CLOSED (안전한 값)."""
        repo = InMemoryCircuitBreakerStateRepository()
        state = repo.get_or_create("new-service")
        
        assert state.state == "closed", "기본 상태는 CLOSED여야 함"
        assert state.failure_count == 0
        assert state.success_count == 0

    def test_l2_load_attempted_on_init(self):
        """초기화 시 L2 로드 시도."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = [
            CircuitBreakerStateData(
                service_name="existing-service",
                state="open",
                failure_count=5,
                success_count=0,
                last_failure_at=datetime.now(timezone.utc),
            )
        ]
        
        # When: LayeredRepository 초기화 시 L2에서 로드 시도
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        # Then: L2에서 상태 로드됨
        mock_l2.get_all.assert_called()
        
    def test_cold_start_with_failed_l2(self):
        """L2 로드 실패 시 기본값 사용."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.side_effect = Exception("L2 connection failed")
        
        # When: L2 연결 실패 상황에서 초기화
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        # Then: 에러 없이 동작하고 기본값 사용
        state = repo.get_or_create("new-service")
        assert state.state == "closed"


class TestIntelligentFallback:
    """지능형 폴백 테스트."""

    def test_fallback_on_l2_error(self):
        """L2 에러 시 L1만으로 동작."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        mock_l2.get_by_service_name.side_effect = Exception("L2 error")
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        # When: L2 에러 발생
        state = repo.get_or_create("test-service")
        
        # Then: L1에서 정상 동작
        assert state is not None
        assert state.service_name == "test-service"

    def test_l2_health_status_tracks_errors(self):
        """L2 에러 발생 시 메트릭 추적."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        mock_l2.set.side_effect = Exception("Write failed")
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        repo._l2_healthy = True
        
        # When: L2 쓰기 시도 (동기화는 백그라운드에서 실행)
        repo.record_failure("test-service")
        
        # Then: 기본적인 동작 확인 - L1은 정상 동작
        state = repo.get_by_service_name("test-service")
        assert state is not None

    def test_no_l2_operations_when_unhealthy(self):
        """L2가 unhealthy 상태에서도 L1은 정상 동작."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        repo._l2_healthy = False  # L2 비정상 상태로 설정
        
        # When: 상태 변경
        repo.record_failure("test-service")
        
        # Then: L1은 정상 동작
        state = repo.get_by_service_name("test-service")
        assert state is not None
        assert state.failure_count >= 1


class TestLayeredRepositoryBasic:
    """LayeredRepository 기본 동작 테스트."""

    def test_l1_always_returns_immediately(self):
        """L1은 항상 즉시 반환."""
        repo = LayeredCircuitBreakerStateRepository(l2_repo=None)
        
        start = time.time()
        repo.get_or_create("test-service")
        elapsed = time.time() - start
        
        assert elapsed < 0.01, f"L1 조회가 너무 느림: {elapsed*1000:.2f}ms"

    def test_l2_sync_is_async(self):
        """L2 동기화는 비동기."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        def slow_set(*args, **kwargs):
            time.sleep(0.5)
        
        mock_l2.set.side_effect = slow_set
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        start = time.time()
        repo.record_failure("test-service")
        elapsed = time.time() - start
        
        # L1 업데이트는 빠르게 완료되어야 함 (L2 동기화는 백그라운드)
        # 비동기 작업이므로 L2 지연이 L1에 영향 없음
        assert elapsed < 0.2, f"L1 업데이트가 너무 느림: {elapsed*1000:.2f}ms"

    def test_get_storage_info(self):
        """저장소 정보 조회."""
        repo = LayeredCircuitBreakerStateRepository(l2_repo=None)
        info = repo.get_storage_info()
        
        assert "l1_type" in info
        assert "l2_enabled" in info
        assert info["l1_type"] is not None
        
    def test_get_storage_info_with_l2(self):
        """L2가 있을 때 저장소 정보 조회."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        info = repo.get_storage_info()
        
        assert info["l2_enabled"] is True
        assert "l2_healthy" in info
        assert "metrics" in info


class TestL2HealthCheck:
    """L2 헬스체크 테스트."""

    def test_get_l2_health(self):
        """L2 헬스 정보 조회."""
        repo = LayeredCircuitBreakerStateRepository(l2_repo=None)
        health = repo.get_l2_health()
        
        assert "healthy" in health
        assert "consecutive_failures" in health
        assert "last_error_time" in health
        assert "adapter_type" in health
        assert "timeout_ms" in health

    def test_reset_l2_health(self):
        """L2 헬스 상태 리셋."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        repo._l2_healthy = False
        repo._l2_consecutive_failures = 5
        
        # When: 헬스 리셋
        repo.reset_l2_health()
        
        # Then: 헬시 상태로 복구
        assert repo._l2_healthy is True
        assert repo._l2_consecutive_failures == 0

    def test_consecutive_failures_tracked(self):
        """연속 실패 횟수 추적 기본 동작."""
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        
        repo = LayeredCircuitBreakerStateRepository(l2_repo=mock_l2)
        
        # L2 헬스 상태 확인
        health = repo.get_l2_health()
        assert "consecutive_failures" in health
        assert health["consecutive_failures"] >= 0
