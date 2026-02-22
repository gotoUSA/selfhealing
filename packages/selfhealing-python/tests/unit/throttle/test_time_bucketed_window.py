"""
Time-Bucketed RTT Window Unit Tests.

초당 버킷 기반 RTT 윈도우의 기능 테스트.
"""

from unittest.mock import patch


class TestTimeBucketedRTTWindow:
    """TimeBucketedRTTWindow 클래스 테스트."""

    def test_window_initialization(self):
        """윈도우 초기화 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        assert window.window_seconds == 10
        stats = window.get_stats()
        assert stats.total_samples == 0
        assert stats.avg_rtt_ms is None
        assert stats.bucket_count == 0

    def test_add_single_sample(self):
        """단일 샘플 추가 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)
        window.add_sample(100.0)

        stats = window.get_stats()
        assert stats.total_samples == 1
        assert stats.avg_rtt_ms == 100.0
        assert stats.min_rtt_ms == 100.0
        assert stats.max_rtt_ms == 100.0
        assert stats.bucket_count == 1

    def test_add_multiple_samples_same_second(self):
        """동일 초에 여러 샘플 추가 시 집계 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        # 동일 초에 여러 샘플 추가
        window.add_sample(100.0)
        window.add_sample(200.0)
        window.add_sample(300.0)

        stats = window.get_stats()
        assert stats.total_samples == 3
        assert stats.avg_rtt_ms == 200.0  # (100+200+300)/3
        assert stats.min_rtt_ms == 100.0
        assert stats.max_rtt_ms == 300.0
        assert stats.bucket_count == 1  # 모두 같은 버킷

    def test_samples_across_different_seconds(self):
        """다른 초에 샘플 추가 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        # 시간 모킹으로 다른 초에 샘플 추가
        base_time = 1000.0

        with patch("time.time", return_value=base_time):
            window.add_sample(100.0)

        with patch("time.time", return_value=base_time + 1):
            window.add_sample(200.0)

        with patch("time.time", return_value=base_time + 2):
            stats = window.get_stats()
            assert stats.total_samples == 2
            assert stats.avg_rtt_ms == 150.0  # (100+200)/2
            assert stats.bucket_count == 2

    def test_old_buckets_expire(self):
        """오래된 버킷 만료 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=5)

        base_time = 1000.0

        # 첫 번째 샘플
        with patch("time.time", return_value=base_time):
            window.add_sample(100.0)

        # 5초 후: 첫 버킷이 만료됨
        with patch("time.time", return_value=base_time + 5):
            window.add_sample(200.0)
            stats = window.get_stats()
            # 첫 번째 버킷(100.0)은 만료됨
            assert stats.total_samples == 1
            assert stats.avg_rtt_ms == 200.0
            assert stats.bucket_count == 1

    def test_bucket_reuse_after_full_rotation(self):
        """버킷 순환 재사용 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=3)

        base_time = 1000.0

        # 0초, 1초, 2초에 샘플 추가
        for i in range(3):
            with patch("time.time", return_value=base_time + i):
                window.add_sample(100.0 * (i + 1))

        # 3초 후: 0번 버킷이 재사용됨
        with patch("time.time", return_value=base_time + 3):
            window.add_sample(400.0)
            stats = window.get_stats()
            # 1초(200), 2초(300), 3초(400) 만 유효
            assert stats.total_samples == 3
            assert stats.avg_rtt_ms == 300.0  # (200+300+400)/3

    def test_negative_rtt_ignored(self):
        """음수 RTT 무시 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        window.add_sample(-100.0)  # 무시됨
        window.add_sample(100.0)

        stats = window.get_stats()
        assert stats.total_samples == 1
        assert stats.avg_rtt_ms == 100.0

    def test_memory_usage_calculation(self):
        """메모리 사용량 계산 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)
        memory = window.memory_usage_bytes()

        # 10개 버킷 × (4+4+4+4+8) = 240 바이트
        # sum(float 4) + count(int 4) + min(float 4) + max(float 4) + ts(int64 8)
        assert memory == 10 * (4 + 4 + 4 + 4 + 8)

    def test_get_bucket_data(self):
        """개별 버킷 데이터 조회 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)
        base_time = 1000.0

        with patch("time.time", return_value=base_time):
            window.add_sample(150.0)
            bucket_idx = int(base_time) % 10

            data = window.get_bucket_data(bucket_idx)
            assert data is not None
            assert data.sum_rtt == 150.0
            assert data.count == 1
            assert data.min_rtt == 150.0
            assert data.max_rtt == 150.0

    def test_get_all_bucket_data(self):
        """모든 버킷 데이터 조회 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)
        base_time = 1000.0

        # 3개 버킷에 데이터 추가
        for i in range(3):
            with patch("time.time", return_value=base_time + i):
                window.add_sample(100.0 + i * 50)

        with patch("time.time", return_value=base_time + 2):
            all_data = window.get_all_bucket_data()
            assert len(all_data) == 3
            assert all_data[0]["avg_rtt_ms"] == 100.0
            assert all_data[1]["avg_rtt_ms"] == 150.0
            assert all_data[2]["avg_rtt_ms"] == 200.0

    def test_reset(self):
        """윈도우 리셋 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        window.add_sample(100.0)
        window.add_sample(200.0)

        stats_before = window.get_stats()
        assert stats_before.total_samples == 2

        window.reset()

        stats_after = window.get_stats()
        assert stats_after.total_samples == 0
        assert stats_after.avg_rtt_ms is None

    def test_cache_invalidation_on_add_sample(self):
        """샘플 추가 시 캐시 무효화 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedRTTWindow,
        )

        window = TimeBucketedRTTWindow(window_seconds=10)

        window.add_sample(100.0)
        stats1 = window.get_stats()
        assert stats1.avg_rtt_ms == 100.0

        # 캐시된 상태에서 새 샘플 추가
        window.add_sample(200.0)
        stats2 = window.get_stats()
        assert stats2.avg_rtt_ms == 150.0  # 캐시 무효화되어 새 값


class TestTimeBucketedGradientCalculator:
    """TimeBucketedGradientCalculator 클래스 테스트."""

    def test_gradient_calculator_initialization(self):
        """Gradient 계산기 초기화 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10)

        assert calc.window.window_seconds == 10
        assert calc.get_gradient() == 0.0
        assert calc.get_current_rtt() is None

    def test_gradient_with_insufficient_samples(self):
        """샘플 부족 시 gradient 0 반환 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10, min_samples_for_gradient=5)

        calc.add_sample(100.0)
        calc.add_sample(200.0)

        # 샘플이 5개 미만이므로 gradient=0
        assert calc.get_gradient() == 0.0

    def test_gradient_increasing_rtt(self):
        """RTT 증가 시 양수 gradient 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10, min_samples_for_gradient=2)

        # RTT 증가 패턴
        calc.add_sample(100.0)
        calc.add_sample(150.0)
        calc.add_sample(200.0)

        gradient = calc.get_gradient()
        assert gradient > 0  # RTT 증가 = 양수 gradient

    def test_gradient_decreasing_rtt(self):
        """RTT 감소 시 음수 gradient 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10, min_samples_for_gradient=2)

        # RTT 감소 패턴
        calc.add_sample(200.0)
        calc.add_sample(150.0)
        calc.add_sample(100.0)

        gradient = calc.get_gradient()
        assert gradient < 0  # RTT 감소 = 음수 gradient

    def test_get_stats(self):
        """통계 조회 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10)

        calc.add_sample(100.0)
        calc.add_sample(200.0)
        calc.add_sample(300.0)

        stats = calc.get_stats()

        assert stats["sample_count"] == 3
        assert stats["avg_rtt_ms"] == 200.0
        assert stats["min_rtt_ms"] == 100.0
        assert stats["max_rtt_ms"] == 300.0
        assert stats["window_seconds"] == 10
        assert "memory_bytes" in stats
        assert "gradient" in stats
        assert "smoothed_rtt_ms" in stats

    def test_reset(self):
        """Gradient 계산기 리셋 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10)

        calc.add_sample(100.0)
        calc.add_sample(200.0)

        assert calc.get_current_rtt() is not None

        calc.reset()

        assert calc.get_current_rtt() is None
        assert calc.get_gradient() == 0.0
        assert calc.get_stats()["sample_count"] == 0

    def test_ema_compatibility(self):
        """기존 EMA 방식 호환성 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import (
            TimeBucketedGradientCalculator,
        )

        calc = TimeBucketedGradientCalculator(window_seconds=10)

        # EMA 방식으로 smoothed_rtt 업데이트 확인
        calc.add_sample(100.0)
        assert calc._smoothed_rtt == 100.0

        calc.add_sample(200.0)
        # EMA: 0.5 * 200 + 0.5 * 100 = 150
        assert calc._smoothed_rtt == 150.0


class TestRTTWindowStats:
    """RTTWindowStats 데이터 클래스 테스트."""

    def test_stats_dataclass(self):
        """RTTWindowStats 필드 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import RTTWindowStats

        stats = RTTWindowStats(
            avg_rtt_ms=150.0,
            min_rtt_ms=100.0,
            max_rtt_ms=200.0,
            total_samples=10,
            bucket_count=3,
            window_seconds=10,
            oldest_bucket_age_seconds=5.0,
        )

        assert stats.avg_rtt_ms == 150.0
        assert stats.min_rtt_ms == 100.0
        assert stats.max_rtt_ms == 200.0
        assert stats.total_samples == 10
        assert stats.bucket_count == 3
        assert stats.window_seconds == 10
        assert stats.oldest_bucket_age_seconds == 5.0


class TestBucketData:
    """BucketData NamedTuple 테스트."""

    def test_bucket_data_namedtuple(self):
        """BucketData 필드 테스트."""
        from selfhealing.services.throttle.time_bucketed_window import BucketData

        data = BucketData(
            sum_rtt=300.0,
            count=3,
            min_rtt=50.0,
            max_rtt=150.0,
        )

        assert data.sum_rtt == 300.0
        assert data.count == 3
        assert data.min_rtt == 50.0
        assert data.max_rtt == 150.0

        # 평균 계산
        assert data.sum_rtt / data.count == 100.0
