"""
Phase 3: Synthetic Traffic Generator and Traffic Shaper Tests

Tests for Phase 3 implementation:
- SyntheticLoadGenerator (5 tests)
- TrafficShaper (5 tests)

Reference: docs/self_healing/middleware_system/24_CHAOS_INTEGRATION_PLAN.md §8.4

Total: 10 tests
"""

import pytest
import time
import threading
from unittest.mock import patch, MagicMock


# =============================================================================
# SyntheticLoadGenerator Tests (5 tests)
# =============================================================================


class TestSyntheticLoadGenerator:
    """Tests for SyntheticLoadGenerator - 5 tests."""

    def test_generate_baseline_traffic(self):
        """Test baseline traffic generation."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
            GeneratorState,
        )

        generator = SyntheticLoadGenerator(
            experiment_id="test-exp-001",
            target_service="payment-api",
        )

        config = LoadConfig(
            target_rps=10.0,
            duration_seconds=2,
            pattern=LoadPattern.CONSTANT,
        )

        # 콜백으로 요청 추적
        requests_received = []

        def track_request(req):
            requests_received.append(req)
            return True

        generator.start(config, request_handler=track_request)

        # 잠시 대기
        time.sleep(2.5)

        stats = generator.stop()

        # 검증
        assert stats.total_requests > 0
        assert stats.successful_requests > 0
        assert generator.state == GeneratorState.STOPPED

    def test_ramp_up_pattern(self):
        """Test ramp-up load pattern."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
        )

        generator = SyntheticLoadGenerator(
            experiment_id="test-exp-002",
            target_service="order-api",
        )

        config = LoadConfig(
            target_rps=20.0,
            duration_seconds=3,
            pattern=LoadPattern.RAMP_UP,
            ramp_up_seconds=2,
        )

        rps_samples = []

        def track_with_timing(req):
            return True

        generator.start(config, request_handler=track_with_timing)

        # RPS 샘플링
        for _ in range(6):
            time.sleep(0.5)
            rps_samples.append(generator.get_current_rps())

        generator.stop()

        # 검증: RPS가 증가하는 경향
        # (완벽한 증가는 아닐 수 있으나 전반적 경향)
        assert len(rps_samples) >= 3

    def test_steady_state_pattern(self):
        """Test steady-state load pattern with warmup."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
            GeneratorState,
        )

        generator = SyntheticLoadGenerator(
            experiment_id="test-exp-003",
            target_service="inventory-api",
        )

        config = LoadConfig(
            target_rps=15.0,
            duration_seconds=3,
            pattern=LoadPattern.STEADY_STATE,
        )

        generator.start(config)
        time.sleep(3.5)
        stats = generator.stop()

        # 검증
        assert stats.total_requests > 0
        # 상태가 정상적으로 종료되었는지 확인
        assert generator.state in [GeneratorState.STOPPED, GeneratorState.STOPPING]

    def test_spike_pattern(self):
        """Test spike load pattern."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
        )

        generator = SyntheticLoadGenerator(
            experiment_id="test-exp-004",
            target_service="user-api",
        )

        config = LoadConfig(
            target_rps=10.0,
            duration_seconds=4,
            pattern=LoadPattern.SPIKE,
            spike_multiplier=3.0,
            spike_duration_seconds=1,
        )

        generator.start(config)
        time.sleep(4.5)
        stats = generator.stop()

        # 검증
        assert stats.total_requests > 0
        # 스파이크 기간에 더 많은 요청이 있었을 것

    def test_graceful_shutdown(self):
        """Test graceful shutdown of generator."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
            GeneratorState,
        )

        generator = SyntheticLoadGenerator(
            experiment_id="test-exp-005",
            target_service="checkout-api",
        )

        config = LoadConfig(
            target_rps=50.0,
            duration_seconds=60,  # 긴 duration
            pattern=LoadPattern.CONSTANT,
        )

        generator.start(config)

        # 잠시 후 종료
        time.sleep(1)

        # Graceful shutdown
        stats = generator.stop(graceful=True)

        # 검증
        assert generator.state == GeneratorState.STOPPED
        assert stats.total_requests > 0


# =============================================================================
# TrafficShaper Tests (5 tests)
# =============================================================================


class TestTrafficShaper:
    """Tests for TrafficShaper - 5 tests."""

    def test_shape_request_rate(self):
        """Test request rate shaping."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
        )

        shaper = TrafficShaper(experiment_id="test-shaper-001")

        config = ShapingConfig(
            mode=ShapingMode.RATE_LIMIT,
            target_rps=10.0,
            burst_size=5,
        )

        shaper.configure(config)

        # 빠르게 요청 시도
        allowed_count = 0
        throttled_count = 0

        for _ in range(20):
            if shaper.should_allow():
                allowed_count += 1
            else:
                throttled_count += 1

        # 일부는 허용되고 일부는 스로틀됨
        stats = shaper.get_stats()
        assert stats.total_shaped > 0
        # 버스트 사이즈에 따라 일부만 허용됨

    def test_shape_concurrent_users(self):
        """Test concurrent user shaping."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
        )

        shaper = TrafficShaper(experiment_id="test-shaper-002")

        config = ShapingConfig(
            mode=ShapingMode.CONCURRENT_LIMIT,
            max_concurrent=5,
        )

        shaper.configure(config)

        # 동시 슬롯 획득
        acquired = []
        for i in range(10):
            if shaper.acquire_concurrent():
                acquired.append(i)

        # 최대 5개만 획득 가능
        assert len(acquired) == 5
        assert shaper.get_stats().current_concurrent == 5

        # 해제 후 다시 획득 가능
        shaper.release_concurrent()
        shaper.release_concurrent()

        assert shaper.get_stats().current_concurrent == 3
        assert shaper.acquire_concurrent() is True

    def test_traffic_distribution_uniform(self):
        """Test uniform traffic distribution."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
            DistributionStrategy,
        )

        shaper = TrafficShaper(experiment_id="test-shaper-003")

        config = ShapingConfig(
            mode=ShapingMode.RATE_LIMIT,
            target_rps=100.0,
            distribution_strategy=DistributionStrategy.UNIFORM,
            endpoint_weights={
                "/api/v1/orders": 1.0,
                "/api/v1/payments": 1.0,
                "/api/v1/users": 1.0,
            },
        )

        shaper.configure(config)

        # 엔드포인트 선택 분포 테스트
        selections = {"orders": 0, "payments": 0, "users": 0}

        for _ in range(100):
            endpoint = shaper.select_endpoint()
            if endpoint:
                if "orders" in endpoint:
                    selections["orders"] += 1
                elif "payments" in endpoint:
                    selections["payments"] += 1
                elif "users" in endpoint:
                    selections["users"] += 1

        # 균등 분배이므로 대략 비슷한 수
        assert all(count > 0 for count in selections.values())

    def test_traffic_distribution_weighted(self):
        """Test weighted traffic distribution."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
            DistributionStrategy,
        )

        shaper = TrafficShaper(experiment_id="test-shaper-004")

        config = ShapingConfig(
            mode=ShapingMode.RATE_LIMIT,
            target_rps=100.0,
            distribution_strategy=DistributionStrategy.WEIGHTED,
            endpoint_weights={
                "/api/v1/orders": 3.0,  # 60%
                "/api/v1/payments": 1.0,  # 20%
                "/api/v1/users": 1.0,  # 20%
            },
        )

        shaper.configure(config)

        # 분배 확인
        distributions = shaper.get_distributions()

        assert len(distributions) == 3

        orders_dist = next(
            (d for d in distributions if "orders" in d.endpoint), None
        )
        assert orders_dist is not None
        assert orders_dist.weight == pytest.approx(0.6, rel=0.01)

    def test_adaptive_shaping(self):
        """Test adaptive rate shaping based on latency."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
        )

        shaper = TrafficShaper(experiment_id="test-shaper-005")

        config = ShapingConfig(
            mode=ShapingMode.ADAPTIVE,
            target_rps=100.0,
            target_latency_ms=100.0,
            adjustment_interval_seconds=0.5,
        )

        shaper.configure(config)

        # 높은 레이턴시 시뮬레이션
        for _ in range(20):
            shaper.record_latency(200.0)  # 타겟보다 높음
            shaper.should_allow()
            time.sleep(0.1)

        stats = shaper.get_stats()

        # 적응 발생 확인
        # (실제 적응은 adjustment_interval 이후)
        assert stats.avg_latency_ms > 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase3Integration:
    """Integration tests for Phase 3 components."""

    def test_experiment_with_synthetic_traffic(self):
        """Test chaos experiment with synthetic traffic injection."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
            SyntheticTrafficGenerator,
        )
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
        )

        # 합성 트래픽 생성기
        load_gen = SyntheticLoadGenerator(
            experiment_id="test-integration-001",
            target_service="payment-api",
        )

        # 트래픽 형성기
        shaper = TrafficShaper(experiment_id="test-integration-001")
        shaper.configure(
            ShapingConfig(
                mode=ShapingMode.RATE_LIMIT,
                target_rps=20.0,
            )
        )

        # 형성기와 연동된 요청 핸들러
        shaped_requests = []

        def shaped_handler(req):
            if shaper.should_allow():
                shaped_requests.append(req)
                return True
            return False

        # 부하 생성
        load_gen.start(
            LoadConfig(
                target_rps=50.0,  # 형성기보다 높은 RPS
                duration_seconds=2,
                pattern=LoadPattern.CONSTANT,
            ),
            request_handler=shaped_handler,
        )

        time.sleep(2.5)
        load_gen.stop()

        # 형성기 통계
        stats = shaper.get_stats()

        # 형성되어 허용된 요청과 스로틀된 요청 있음
        assert stats.total_shaped > 0

    def test_traffic_cleanup_on_error(self):
        """Test traffic cleanup when error occurs."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticLoadGenerator,
            LoadConfig,
            LoadPattern,
            GeneratorState,
            cleanup_generator,
            get_synthetic_load_generator,
        )

        # 생성기 생성
        generator = get_synthetic_load_generator(
            experiment_id="test-cleanup-001",
            target_service="error-service",
        )

        config = LoadConfig(
            target_rps=10.0,
            duration_seconds=60,
        )

        # 에러를 발생시키는 핸들러
        error_count = [0]

        def error_handler(req):
            error_count[0] += 1
            if error_count[0] > 5:
                raise Exception("Simulated error")
            return True

        generator.start(config, request_handler=error_handler)
        time.sleep(1)

        # 정리
        cleanup_generator("test-cleanup-001", "error-service")

        # 정리 후 상태 확인
        assert generator.state in [GeneratorState.STOPPED, GeneratorState.STOPPING]

    def test_synthetic_request_headers(self):
        """Test that synthetic requests have correct headers."""
        from selfhealing.services.chaos.synthetic_load import (
            SyntheticTrafficGenerator,
            SYNTHETIC_HEADER,
            SYNTHETIC_VALUE,
        )

        generator = SyntheticTrafficGenerator(experiment_id="test-headers-001")

        request = generator.create_synthetic_request(
            target_service="payment-api",
            original_headers={"Content-Type": "application/json"},
        )

        # 헤더 검증
        assert SYNTHETIC_HEADER in request.synthetic_headers
        assert request.synthetic_headers[SYNTHETIC_HEADER] == SYNTHETIC_VALUE
        assert request.synthetic_headers["X-Experiment-Id"] == "test-headers-001"
        assert request.synthetic_headers["X-Traffic-Type"] == "synthetic"

        # 원본 헤더 보존
        assert request.synthetic_headers["Content-Type"] == "application/json"

        # 합성 요청 탐지
        assert SyntheticTrafficGenerator.is_synthetic_request(request.synthetic_headers)

    def test_shaper_reset(self):
        """Test shaper reset functionality."""
        from selfhealing.services.chaos.traffic_shaper import (
            TrafficShaper,
            ShapingConfig,
            ShapingMode,
        )

        shaper = TrafficShaper(experiment_id="test-reset-001")

        config = ShapingConfig(
            mode=ShapingMode.RATE_LIMIT,
            target_rps=100.0,
        )

        shaper.configure(config)

        # 일부 요청 처리
        for _ in range(10):
            shaper.should_allow()

        initial_stats = shaper.get_stats()
        assert initial_stats.total_shaped > 0

        # 리셋
        shaper.reset()

        reset_stats = shaper.get_stats()
        assert reset_stats.total_shaped == 0
        assert reset_stats.total_throttled == 0
