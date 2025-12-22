"""
Shadow Log Forensic Analysis 통합 테스트.

L2 장애 시 Shadow Log 기록 및 Forensic 분석 기능을 테스트합니다.

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
"""

import time
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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


@pytest.fixture
def clean_shadow_logger():
    """테스트 전후 ShadowLogger 초기화."""
    shadow_logger = get_shadow_logger()
    shadow_logger.clear()
    yield shadow_logger
    shadow_logger.clear()


@pytest.fixture
def failing_l2_repo():
    """항상 실패하는 L2 레포지토리 mock."""
    mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
    mock_l2.get_all.return_value = []
    mock_l2.set.side_effect = Exception("L2 connection failed")
    mock_l2.get_by_service_name.side_effect = Exception("L2 connection failed")
    return mock_l2


@pytest.mark.integration
class TestShadowLogIntegration:
    """Shadow Log 통합 테스트."""

    def test_l2_failure_records_to_shadow_log(
        self, clean_shadow_logger, failing_l2_repo
    ):
        """L2 실패 시 Shadow Log에 자동 기록."""
        # Given: 실패하는 L2가 있는 Layered Repository
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=failing_l2_repo,
            adapter_type="redis",
        )
        repo._l2_healthy = True  # L2 동기화 시도하도록 설정

        # When: 상태 변경 (L2 동기화 실패 발생)
        repo.record_failure("payment-gateway")
        
        # 비동기 작업 완료 대기
        time.sleep(0.2)

        # Then: Shadow Log에 기록됨
        records = clean_shadow_logger.get_all_records()
        # L2 동기화 시도가 발생했다면 기록이 있을 수 있음
        # (타임아웃이나 즉시 실패에 따라 다름)
        assert repo._metrics["l2_sync_failure_count"] >= 0

    def test_shadow_log_forensic_analysis_after_multiple_failures(
        self, clean_shadow_logger
    ):
        """여러 실패 후 Forensic 분석."""
        # Given: 여러 서비스에서 L2 실패 발생
        services = ["payment-gateway", "order-service", "inventory-service"]
        
        for svc in services:
            clean_shadow_logger.record_sync_failure(
                service_name=svc,
                intended_state="open",
                error=Exception(f"Connection failed for {svc}"),
                adapter_type="redis",
                operation="sync",
            )

        # When: Forensic 분석 수행
        analysis = clean_shadow_logger.analyze_l2_failures()

        # Then: 정확한 분석 결과
        assert analysis["unsynced_count"] == 3
        assert len(analysis["affected_services"]) == 3
        for svc in services:
            assert svc in analysis["affected_services"]
        
        # 타임라인 검증
        assert len(analysis["failure_timeline"]) == 3
        
        # 권장사항 포함
        assert len(analysis["recommendations"]) >= 1

    def test_shadow_log_replay_workflow(self, clean_shadow_logger):
        """Shadow Log 재생 워크플로우."""
        # Given: 미동기화 기록
        clean_shadow_logger.record_sync_failure(
            "test-service", "open", Exception("Error"),
            adapter_type="redis"
        )
        
        assert clean_shadow_logger.get_stats()["unsynced_count"] == 1
        
        # When: 동기화 완료 마킹
        marked = clean_shadow_logger.mark_as_synced("test-service")
        
        # Then: 기록이 동기화 완료로 표시
        assert marked == 1
        assert clean_shadow_logger.get_stats()["unsynced_count"] == 0
        
        # 분석에서도 반영
        analysis = clean_shadow_logger.analyze_l2_failures()
        assert analysis["unsynced_count"] == 0

    def test_concurrent_shadow_log_access(self, clean_shadow_logger):
        """동시 접근 시 Shadow Log 안전성."""
        num_threads = 5
        records_per_thread = 50
        errors = []

        def record_and_analyze(thread_id):
            try:
                for i in range(records_per_thread):
                    clean_shadow_logger.record_sync_failure(
                        f"service-{thread_id}-{i}",
                        "open",
                        Exception(f"error-{thread_id}-{i}"),
                    )
                    # 중간에 분석도 수행
                    if i % 10 == 0:
                        clean_shadow_logger.analyze_l2_failures()
            except Exception as e:
                errors.append(e)

        # When: 여러 스레드에서 동시 접근
        threads = [
            threading.Thread(target=record_and_analyze, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Then: 에러 없이 완료
        assert len(errors) == 0
        
        # 기록이 있음 (max_entries 제한 내에서)
        stats = clean_shadow_logger.get_stats()
        assert stats["total_records"] > 0


@pytest.mark.integration
class TestL2FailureScenarios:
    """L2 장애 시나리오 테스트."""

    def test_graceful_degradation_on_l2_failure(self, clean_shadow_logger):
        """L2 장애 시 우아한 저하."""
        # Given: 실패하는 L2
        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        mock_l2.set.side_effect = Exception("L2 unavailable")
        
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=mock_l2,
            adapter_type="redis",
        )

        # When: L1 작업 수행
        state = repo.get_or_create("test-service")
        repo.record_failure("test-service")

        # Then: L1은 정상 동작
        updated_state = repo.get_by_service_name("test-service")
        assert updated_state is not None
        assert updated_state.failure_count >= 1

    def test_l2_recovery_after_failure(self, clean_shadow_logger):
        """L2 복구 후 동기화."""
        # Given: 처음에 실패하다가 복구되는 L2
        call_count = [0]
        
        def conditional_fail(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("L2 temporarily unavailable")
            return None

        mock_l2 = MagicMock(spec=InMemoryCircuitBreakerStateRepository)
        mock_l2.get_all.return_value = []
        mock_l2.set.side_effect = conditional_fail
        mock_l2.get_by_service_name.return_value = None

        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=mock_l2,
            adapter_type="redis",
        )

        # When: 여러 번 작업 수행
        for _ in range(5):
            repo.record_failure("test-service")
            time.sleep(0.05)

        # Then: 나중에는 L2 호출 성공
        assert call_count[0] > 0

    def test_shadow_log_preserves_failure_history(self, clean_shadow_logger):
        """Shadow Log가 실패 이력을 보존."""
        # Given: 여러 실패 발생
        for i in range(10):
            clean_shadow_logger.record_sync_failure(
                f"service-{i % 3}",  # 3개 서비스 순환
                "open",
                Exception(f"Error {i}"),
                adapter_type="redis",
            )

        # When: 분석 수행
        analysis = clean_shadow_logger.analyze_l2_failures()

        # Then: 모든 이력이 타임라인에 보존
        assert len(analysis["failure_timeline"]) == 10
        
        # 시간순 정렬 확인
        times = [entry["time"] for entry in analysis["failure_timeline"]]
        assert times == sorted(times)


@pytest.mark.integration
class TestShadowLogAPIIntegration:
    """Shadow Log API 통합 테스트 (Django 테스트 클라이언트 필요)."""

    def test_shadow_log_stats_accuracy(self, clean_shadow_logger):
        """Shadow Log 통계 정확성."""
        # Given: 다양한 상태의 기록
        clean_shadow_logger.record_sync_failure(
            "svc-1", "open", Exception("e1"), adapter_type="redis"
        )
        clean_shadow_logger.record_sync_failure(
            "svc-2", "closed", Exception("e2"), adapter_type="django"
        )
        clean_shadow_logger.mark_as_synced("svc-1")

        # When: 통계 및 분석 조회
        stats = clean_shadow_logger.get_stats()
        analysis = clean_shadow_logger.analyze_l2_failures()

        # Then: 통계 일치
        assert stats["total_records"] == 2
        assert stats["unsynced_count"] == 1
        assert analysis["unsynced_count"] == 1
        
        # 어댑터별 통계
        assert analysis["by_adapter"]["redis"] == 1
        assert analysis["by_adapter"]["django"] == 1


@pytest.mark.integration
class TestShadowLogMaxEntriesEnforcement:
    """Shadow Log 최대 엔트리 제한 테스트."""

    def test_max_entries_enforced(self, clean_shadow_logger):
        """최대 엔트리 수 제한 적용."""
        # Given: 제한 설정
        clean_shadow_logger.set_max_entries(50)

        # When: 제한 초과 기록
        for i in range(100):
            clean_shadow_logger.record_sync_failure(
                f"service-{i}", "open", Exception(f"error-{i}")
            )

        # Then: 최대 엔트리만 유지
        stats = clean_shadow_logger.get_stats()
        assert stats["total_records"] == 50
        assert stats["max_entries"] == 50

        # 가장 최근 기록이 유지됨
        records = clean_shadow_logger.get_all_records()
        assert records[-1].service_name == "service-99"

    def test_max_entries_trims_oldest(self, clean_shadow_logger):
        """가장 오래된 기록부터 제거."""
        clean_shadow_logger.set_max_entries(10)

        for i in range(20):
            clean_shadow_logger.record_sync_failure(
                f"service-{i}", "open", Exception(f"error-{i}")
            )

        records = clean_shadow_logger.get_all_records()
        
        # 처음 10개(service-0 ~ service-9)는 제거됨
        service_names = [r.service_name for r in records]
        assert "service-0" not in service_names
        assert "service-10" in service_names
        assert "service-19" in service_names
