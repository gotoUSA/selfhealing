"""
GradientCalculator.get_snapshot() 단위 테스트.
"""



class TestGradientCalculatorSnapshot:
    """get_snapshot() 메서드 테스트."""

    def test_get_snapshot_returns_tuple(self):
        """get_snapshot()이 (rtt, gradient) 튜플 반환하는지 확인."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(sample_window_seconds=60.0)

        # 샘플 데이터 추가
        calc.add_sample(0.05)
        calc.add_sample(0.06)
        calc.add_sample(0.055)

        snapshot = calc.get_snapshot()

        assert isinstance(snapshot, tuple)
        assert len(snapshot) == 2

    def test_get_snapshot_values(self):
        """get_snapshot() 값이 유효한지 확인."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(
            sample_window_seconds=60.0,
            smoothing_factor=0.5,
        )

        # 샘플 데이터 추가
        for rtt in [0.05, 0.06, 0.055, 0.045, 0.05]:
            calc.add_sample(rtt)

        smoothed_rtt, gradient = calc.get_snapshot()

        # smoothed_rtt는 양수여야 함
        assert smoothed_rtt > 0
        # gradient는 유한한 값
        assert isinstance(gradient, float)

    def test_get_snapshot_initial_state(self):
        """초기 상태에서 get_snapshot() 호출 시 기본값 반환."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(sample_window_seconds=60.0)

        smoothed_rtt, gradient = calc.get_snapshot()

        # 초기 상태에서는 None 또는 float
        assert smoothed_rtt is None or isinstance(smoothed_rtt, float)
        assert isinstance(gradient, float)

    def test_get_snapshot_after_many_samples(self):
        """많은 샘플 후 get_snapshot()이 안정적인지 확인."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(
            sample_window_seconds=60.0,
            smoothing_factor=0.2,
        )

        # 100개 샘플 추가 (점진적 증가 후 안정화)
        for i in range(50):
            rtt = 0.05 + (i * 0.001)  # 50ms에서 100ms까지 증가
            calc.add_sample(rtt)

        for i in range(50):
            calc.add_sample(0.08)  # 80ms에서 안정화

        smoothed_rtt, gradient = calc.get_snapshot()

        # smoothed_rtt는 80ms 근처
        assert 0.05 < smoothed_rtt < 0.15, f"Expected rtt ~0.08, got {smoothed_rtt}"
        # gradient는 유한한 값 (-1 ~ 1 범위 내)
        assert -1.0 < gradient < 1.0, f"Expected small gradient, got {gradient}"


class TestGradientCalculatorRecordSample:
    """record_sample() 메서드 테스트."""

    def test_record_sample_updates_state(self):
        """record_sample()이 내부 상태를 업데이트하는지 확인."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(sample_window_seconds=60.0)

        initial_rtt, initial_gradient = calc.get_snapshot()

        calc.add_sample(0.1)

        updated_rtt, updated_gradient = calc.get_snapshot()

        # 샘플 추가 후 상태 변화
        assert updated_rtt != initial_rtt or updated_gradient != initial_gradient

    def test_record_sample_with_zero_rtt(self):
        """0 RTT 기록 시 안전하게 처리하는지 확인."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(sample_window_seconds=60.0)

        # 0 RTT 기록 (예외 없이 처리되어야 함)
        calc.add_sample(0.0)

        smoothed_rtt, gradient = calc.get_snapshot()
        assert isinstance(smoothed_rtt, float)
        assert isinstance(gradient, float)
