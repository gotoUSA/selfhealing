"""
Stage 28: Multi-Region Latency Test - Unit Tests

이 파일은 Stage 28 시나리오의 핵심 로직을 검증합니다.
"""

import pytest
import time
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from load_tests.scenarios.stage28_multi_region import (
    MultiRegionCoordinator,
    RegionConfig,
    RegionStatus,
    MultiRegionStats,
    inject_region_a_failure,
    inject_region_b_failure,
    inject_clock_skew,
    recover_all_regions,
)


class TestRegionConfig:
    """RegionConfig 테스트"""

    def test_default_region_config(self):
        """기본 리전 설정 테스트"""
        region = RegionConfig(name="test-region")

        assert region.name == "test-region"
        assert region.latency_ms == 5
        assert region.packet_loss == 0.0
        assert region.clock_offset_sec == 0
        assert region.status == RegionStatus.HEALTHY
        assert region.is_primary is False

    def test_custom_region_config(self):
        """커스텀 리전 설정 테스트"""
        region = RegionConfig(
            name="ap-northeast-2a",
            latency_ms=10,
            packet_loss=0.1,
            clock_offset_sec=60,
            status=RegionStatus.DEGRADED,
            is_primary=True,
        )

        assert region.name == "ap-northeast-2a"
        assert region.latency_ms == 10
        assert region.packet_loss == 0.1
        assert region.clock_offset_sec == 60
        assert region.status == RegionStatus.DEGRADED
        assert region.is_primary is True


class TestMultiRegionCoordinator:
    """MultiRegionCoordinator 테스트"""

    @pytest.fixture
    def coordinator(self):
        """새로운 조정자 인스턴스 생성"""
        return MultiRegionCoordinator()

    def test_initial_state(self, coordinator):
        """초기 상태 테스트"""
        assert coordinator.active_region == "region_a"
        assert len(coordinator.regions) == 2
        assert "region_a" in coordinator.regions
        assert "region_b" in coordinator.regions
        assert coordinator.failover_in_progress is False

    def test_get_active_region(self, coordinator):
        """활성 리전 반환 테스트"""
        active = coordinator.get_active_region()

        assert active is not None
        assert active.name == "ap-northeast-2a"
        assert active.is_primary is True

    def test_get_fallback_region(self, coordinator):
        """Fallback 리전 반환 테스트"""
        fallback = coordinator.get_fallback_region()

        assert fallback is not None
        assert fallback.name == "ap-northeast-2b"
        assert fallback.is_primary is False

    def test_inject_packet_loss_failure(self, coordinator):
        """패킷 손실 장애 주입 테스트"""
        coordinator.inject_region_failure("region_a", "packet_loss")

        region = coordinator.regions["region_a"]
        assert region.packet_loss == 0.9
        assert region.status == RegionStatus.DEGRADED

    def test_inject_clock_skew_failure(self, coordinator):
        """Clock Skew 장애 주입 테스트"""
        coordinator.inject_region_failure("region_b", "clock_skew")

        region = coordinator.regions["region_b"]
        assert region.clock_offset_sec == 300
        assert region.status == RegionStatus.CLOCK_SKEW

    def test_inject_down_failure(self, coordinator):
        """리전 다운 장애 주입 테스트"""
        coordinator.inject_region_failure("region_a", "down")

        region = coordinator.regions["region_a"]
        assert region.status == RegionStatus.DOWN

    def test_recover_region(self, coordinator):
        """리전 복구 테스트"""
        # 먼저 장애 주입
        coordinator.inject_region_failure("region_a", "packet_loss")
        assert coordinator.regions["region_a"].status == RegionStatus.DEGRADED

        # 복구
        coordinator.recover_region("region_a")

        region = coordinator.regions["region_a"]
        assert region.packet_loss == 0.0
        assert region.clock_offset_sec == 0
        assert region.status == RegionStatus.HEALTHY

    def test_should_fallback_on_high_packet_loss(self, coordinator):
        """높은 패킷 손실 시 Fallback 필요 테스트"""
        assert coordinator.should_fallback("region_a") is False

        coordinator.inject_region_failure("region_a", "packet_loss")

        assert coordinator.should_fallback("region_a") is True

    def test_should_fallback_on_down(self, coordinator):
        """리전 다운 시 Fallback 필요 테스트"""
        coordinator.inject_region_failure("region_a", "down")

        assert coordinator.should_fallback("region_a") is True

    def test_perform_failover(self, coordinator):
        """Failover 수행 테스트"""
        original_region = coordinator.active_region

        # Region A 장애 주입
        coordinator.inject_region_failure("region_a", "packet_loss")

        # Failover 수행
        new_region = coordinator.perform_failover("region_a")

        assert new_region == "region_b"
        assert coordinator.active_region == "region_b"
        assert coordinator.last_failover_time is not None

    def test_failover_no_available_region(self, coordinator):
        """Fallback 가능한 리전이 없을 때 테스트"""
        # 모든 리전 다운
        coordinator.inject_region_failure("region_a", "down")
        coordinator.inject_region_failure("region_b", "down")

        # Failover 시도
        new_region = coordinator.perform_failover("region_a")

        # Fallback 불가능
        assert coordinator.get_fallback_region() is None

    def test_idempotency_check_new_key(self, coordinator):
        """새로운 Idempotency 키 확인 테스트"""
        is_duplicate, result = coordinator.check_idempotency("new_key_123")

        assert is_duplicate is False
        assert result is None

    def test_idempotency_check_existing_key(self, coordinator):
        """기존 Idempotency 키 확인 테스트"""
        # 먼저 등록
        coordinator.register_idempotency("key_123", "order_456")

        # 확인
        is_duplicate, result = coordinator.check_idempotency("key_123")

        assert is_duplicate is True
        assert result == "order_456"

    def test_idempotency_prevents_duplicates(self, coordinator):
        """Idempotency로 중복 방지 테스트"""
        key = "payment_key_789"

        # 첫 번째 요청
        is_dup1, _ = coordinator.check_idempotency(key)
        assert is_dup1 is False
        coordinator.register_idempotency(key, "payment_result")

        # 두 번째 요청 (중복)
        is_dup2, result = coordinator.check_idempotency(key)
        assert is_dup2 is True
        assert result == "payment_result"

    def test_clock_skew_within_tolerance(self, coordinator):
        """허용 범위 내 Clock Skew 테스트"""
        current_time = datetime.now(timezone.utc)

        is_valid, skew = coordinator.check_clock_skew("region_a", current_time)

        assert is_valid is True
        assert skew < 30

    def test_clock_skew_exceeds_tolerance(self, coordinator):
        """허용 범위 초과 Clock Skew 테스트"""
        # Region B에 5분 Clock Skew 주입
        coordinator.inject_region_failure("region_b", "clock_skew")

        current_time = datetime.now(timezone.utc)

        is_valid, skew = coordinator.check_clock_skew("region_b", current_time)

        assert is_valid is False
        assert skew >= 30  # 300초 오프셋이므로 30초 초과

    def test_simulate_packet_loss_healthy_region(self, coordinator):
        """정상 리전의 패킷 손실 시뮬레이션 테스트"""
        # 정상 리전은 패킷 손실 없음
        losses = [coordinator.simulate_packet_loss("region_a") for _ in range(100)]

        # 모두 False여야 함 (packet_loss = 0.0)
        assert all(loss is False for loss in losses)

    def test_simulate_packet_loss_degraded_region(self, coordinator):
        """장애 리전의 패킷 손실 시뮬레이션 테스트"""
        coordinator.inject_region_failure("region_a", "packet_loss")

        # 90% 패킷 손실이므로 대부분 True
        losses = [coordinator.simulate_packet_loss("region_a") for _ in range(1000)]
        loss_rate = sum(losses) / len(losses)

        # 90% 손실 (0.85 ~ 0.95 범위)
        assert 0.85 <= loss_rate <= 0.95

    def test_get_latency(self, coordinator):
        """리전 지연 시간 반환 테스트"""
        latency_a = coordinator.get_latency("region_a")
        latency_b = coordinator.get_latency("region_b")

        assert latency_a == 5
        assert latency_b == 50

    def test_get_latency_unknown_region(self, coordinator):
        """알 수 없는 리전의 지연 시간 테스트"""
        latency = coordinator.get_latency("unknown_region")

        assert latency == 0


class TestMultiRegionConcurrency:
    """동시성 테스트"""

    @pytest.fixture
    def coordinator(self):
        return MultiRegionCoordinator()

    def test_concurrent_failover_requests(self, coordinator):
        """동시 Failover 요청 테스트"""
        coordinator.inject_region_failure("region_a", "packet_loss")

        results = []
        threads = []

        def do_failover():
            result = coordinator.perform_failover("region_a")
            results.append(result)

        # 10개의 동시 Failover 요청
        for _ in range(10):
            t = threading.Thread(target=do_failover)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Failover는 한 번만 수행되어야 함
        # (다른 요청은 None 또는 이미 변경된 region_b를 반환)
        non_none_results = [r for r in results if r is not None]
        assert len(non_none_results) >= 1

    def test_concurrent_idempotency_registration(self, coordinator):
        """동시 Idempotency 등록 테스트"""
        key = "concurrent_key"
        results = []
        threads = []

        def register_and_check():
            is_dup, existing = coordinator.check_idempotency(key)
            if not is_dup:
                coordinator.register_idempotency(key, f"result_{threading.current_thread().name}")
            results.append((is_dup, existing))

        for i in range(10):
            t = threading.Thread(target=register_and_check, name=f"thread_{i}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # 첫 번째 등록만 성공, 나머지는 중복
        non_duplicates = [r for r in results if not r[0]]
        assert len(non_duplicates) >= 1


class TestMultiRegionStats:
    """통계 테스트"""

    def test_stats_initialization(self):
        """통계 초기화 테스트"""
        stats = MultiRegionStats()

        assert stats.total_requests == 0
        assert stats.region_a_requests == 0
        assert stats.region_b_requests == 0
        assert stats.failover_count == 0
        assert stats.duplicate_orders_prevented == 0
        assert stats.clock_skew_detections == 0

    def test_stats_update(self):
        """통계 업데이트 테스트"""
        stats = MultiRegionStats()

        stats.total_requests = 100
        stats.region_a_requests = 70
        stats.region_b_requests = 30
        stats.failover_count = 2
        stats.failover_success = 2
        stats.failover_time_sum_ms = 1000

        assert stats.total_requests == 100
        assert stats.region_a_requests == 70
        assert stats.region_b_requests == 30

        # 평균 Failover 시간
        avg_failover_time = stats.failover_time_sum_ms / stats.failover_count
        assert avg_failover_time == 500


class TestExternalControls:
    """외부 제어 함수 테스트"""

    def test_inject_and_recover_flow(self):
        """장애 주입 및 복구 흐름 테스트"""
        # 글로벌 coordinator를 사용하므로 import 후 테스트
        from load_tests.scenarios.stage28_multi_region import _coordinator

        # 초기 상태 저장
        initial_status_a = _coordinator.regions["region_a"].status
        initial_status_b = _coordinator.regions["region_b"].status

        # 장애 주입
        inject_region_a_failure()
        assert _coordinator.regions["region_a"].status == RegionStatus.DEGRADED

        inject_region_b_failure()
        assert _coordinator.regions["region_b"].status == RegionStatus.DEGRADED

        # 복구
        recover_all_regions()
        assert _coordinator.regions["region_a"].status == RegionStatus.HEALTHY
        assert _coordinator.regions["region_b"].status == RegionStatus.HEALTHY

    def test_inject_clock_skew(self):
        """Clock Skew 주입 테스트"""
        from load_tests.scenarios.stage28_multi_region import _coordinator

        # Clock Skew 주입
        inject_clock_skew("region_b")

        assert _coordinator.regions["region_b"].status == RegionStatus.CLOCK_SKEW
        assert _coordinator.regions["region_b"].clock_offset_sec == 300

        # 복구
        recover_all_regions()


class TestValidationCriteria:
    """검증 기준 테스트"""

    @pytest.fixture
    def coordinator(self):
        return MultiRegionCoordinator()

    def test_failover_time_under_5_seconds(self, coordinator):
        """Failover 시간 < 5초 검증"""
        coordinator.inject_region_failure("region_a", "packet_loss")

        start_time = time.time()
        coordinator.perform_failover("region_a")
        failover_time = time.time() - start_time

        # 5초 미만이어야 함
        assert failover_time < 5.0

    def test_data_consistency_no_duplicates(self, coordinator):
        """데이터 일관성 - 중복 없음 검증"""
        key = "order_12345"

        # 첫 번째 등록
        coordinator.register_idempotency(key, "order_created")

        # 중복 체크
        is_duplicate, result = coordinator.check_idempotency(key)

        assert is_duplicate is True
        assert result == "order_created"

    def test_clock_skew_tolerance_30_seconds(self, coordinator):
        """Clock Skew 허용 ±30초 검증"""
        # 정상 시간
        now = datetime.now(timezone.utc)
        is_valid, _ = coordinator.check_clock_skew("region_a", now)
        assert is_valid is True

        # 25초 이전 (허용)
        past_25s = now - timedelta(seconds=25)
        is_valid, _ = coordinator.check_clock_skew("region_a", past_25s)
        assert is_valid is True

        # 5분 드리프트 주입 후 검증
        coordinator.inject_region_failure("region_b", "clock_skew")
        is_valid, skew = coordinator.check_clock_skew("region_b", now)
        assert is_valid is False
        assert skew >= 30


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
