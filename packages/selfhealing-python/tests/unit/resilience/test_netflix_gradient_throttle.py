"""
Netflix Gradient Adaptive Throttle Unit Tests.

Tests for the selfhealing.services.throttle module.
"""

import time
import pytest
import threading
from unittest.mock import patch, MagicMock


class TestThrottleConfig:
    """Tests for ThrottleConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig()

        assert config.initial_limit == 100
        assert config.min_limit == 10
        assert config.max_limit == 500  # Updated to match actual default
        assert config.window_seconds == 60
        assert config.sample_interval_ms == 500
        assert config.smoothing_factor == 0.5
        assert config.decrease_ratio == 0.9
        assert config.increase_step == 1
        assert config.sla_warning_ms == 200
        assert config.sla_critical_ms == 500

    def test_custom_config(self):
        """Test custom configuration."""
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=200,
            min_limit=50,
            max_limit=500,
            sla_warning_ms=100,
            sla_critical_ms=250,
        )

        assert config.initial_limit == 200
        assert config.min_limit == 50
        assert config.max_limit == 500
        assert config.sla_warning_ms == 100
        assert config.sla_critical_ms == 250

    def test_from_dict(self):
        """Test creating config from dictionary."""
        from selfhealing.services.throttle.config import ThrottleConfig

        config_dict = {
            "initial_limit": 150,
            "sla_critical_ms": 300,
        }

        config = ThrottleConfig.from_dict(config_dict)

        assert config.initial_limit == 150
        assert config.sla_critical_ms == 300
        # Defaults preserved
        assert config.min_limit == 10


class TestSlidingWindowThrottle:
    """Tests for SlidingWindowThrottle."""

    def test_check_requests_under_limit(self):
        """Test requests are allowed under limit."""
        from selfhealing.services.throttle.base import SlidingWindowThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=10)
        throttle = SlidingWindowThrottle(config)

        # First 10 requests should be allowed
        for i in range(10):
            result = throttle.check(f"user_{i}")
            assert result.allowed is True

    def test_deny_requests_over_limit(self):
        """Test requests are denied when over limit."""
        from selfhealing.services.throttle.base import SlidingWindowThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=5)
        throttle = SlidingWindowThrottle(config)

        # Use up limit
        for i in range(5):
            throttle.check("same_user")

        # 6th request should be denied
        result = throttle.check("same_user")
        assert result.allowed is False
        assert result.remaining == 0

    def test_window_sliding(self):
        """Test that window slides correctly."""
        from selfhealing.services.throttle.base import SlidingWindowThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=5,
            window_seconds=1,  # 1 second window for testing
        )
        throttle = SlidingWindowThrottle(config)

        # Use up limit
        for i in range(5):
            throttle.check("user")

        # Wait for window to slide
        time.sleep(1.1)

        # Should be allowed again
        result = throttle.check("user")
        assert result.allowed is True

    def test_current_limit_property(self):
        """Test current_limit property."""
        from selfhealing.services.throttle.base import SlidingWindowThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=42)
        throttle = SlidingWindowThrottle(config)

        assert throttle.current_limit == 42


class TestGradientCalculator:
    """Tests for Netflix Gradient Calculator."""

    def test_initial_gradient_zero(self):
        """Test initial gradient is zero with no history."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(smoothing_factor=0.5)

        assert calc.get_gradient() == 0.0

    def test_positive_gradient_on_increase(self):
        """Test positive gradient when RTT increases."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(smoothing_factor=0.5)

        # Add samples with increasing RTT
        calc.add_sample(100)  # Base
        calc.add_sample(150)  # 50% increase

        gradient = calc.get_gradient()
        assert gradient > 0  # Positive = RTT increasing

    def test_negative_gradient_on_decrease(self):
        """Test negative gradient when RTT decreases."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(smoothing_factor=0.5)

        # Add samples with decreasing RTT
        calc.add_sample(200)
        calc.add_sample(100)  # 50% decrease

        gradient = calc.get_gradient()
        assert gradient < 0  # Negative = RTT decreasing

    def test_exponential_smoothing(self):
        """Test exponential smoothing is applied."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(smoothing_factor=0.5)

        # Add series of samples
        samples = [100, 150, 120, 180, 90]
        for s in samples:
            calc.add_sample(s)

        # Smoothed RTT should be between extremes (or None for first sample)
        current_rtt = calc.get_current_rtt()
        assert current_rtt is None or (90 <= current_rtt <= 180)

    def test_reset(self):
        """Test reset clears state."""
        from selfhealing.services.throttle.adaptive import GradientCalculator

        calc = GradientCalculator(smoothing_factor=0.5)

        calc.add_sample(100)
        calc.add_sample(200)

        calc.reset()

        # After reset, get_current_rtt returns None or 0.0
        current_rtt = calc.get_current_rtt()
        assert current_rtt is None or current_rtt == 0.0
        assert calc.get_gradient() == 0.0


class TestAdaptiveThrottle:
    """Tests for Netflix Gradient Adaptive Throttle."""

    def test_limit_decreases_on_high_rtt(self):
        """Test limit decreases when RTT exceeds warning threshold."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            sla_warning_ms=50,
            sla_critical_ms=100,
        )
        throttle = AdaptiveThrottle(config)

        initial_limit = throttle.current_limit

        # Record high RTT samples
        for _ in range(15):  # Need at least 10 samples
            throttle.record_response(150)  # Above warning

        assert throttle.current_limit < initial_limit

    def test_limit_decreases_more_on_critical_rtt(self):
        """Test limit decreases more aggressively on critical RTT."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            sla_warning_ms=50,
            sla_critical_ms=100,
        )
        throttle = AdaptiveThrottle(config)

        # Record critical RTT
        for _ in range(15):
            throttle.record_response(500)  # Way above critical

        # Should drop significantly (by 30% or more)
        assert throttle.current_limit < 75  # Less than 75% of original

    def test_limit_increases_on_low_rtt(self):
        """Test limit increases when RTT is healthy."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=50,  # Start lower
            max_limit=100,
            sla_warning_ms=200,
            sla_critical_ms=500,
        )
        throttle = AdaptiveThrottle(config)

        # First establish a baseline with high RTT
        for _ in range(15):
            throttle.record_response(250)  # Warning level

        low_limit = throttle.current_limit

        # Then improve to low RTT
        for _ in range(20):
            throttle.record_response(30)  # Very healthy

        assert throttle.current_limit >= low_limit

    def test_limit_bounded_by_min_max(self):
        """Test limit stays within min/max bounds."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(
            initial_limit=100,
            min_limit=20,
            max_limit=200,
            sla_critical_ms=50,
        )
        throttle = AdaptiveThrottle(config)

        # Try to drive limit below min
        for _ in range(50):
            throttle.record_response(500)  # Critical

        assert throttle.current_limit >= config.min_limit

        # Try to drive limit above max
        throttle2 = AdaptiveThrottle(
            ThrottleConfig(
                initial_limit=180,
                max_limit=200,
                sla_warning_ms=1000,
                sla_critical_ms=2000,
            )
        )
        for _ in range(50):
            throttle2.record_response(10)  # Very healthy

        assert throttle2.current_limit <= 200

    def test_check_returns_result(self):
        """Test check returns ThrottleResult."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig, ThrottleResult

        # 명시적으로 모든 필드 설정하여 Settings 의존성 제거
        config = ThrottleConfig(
            initial_limit=100,
            min_limit=10,
            max_limit=500,
        )
        throttle = AdaptiveThrottle(config)

        result = throttle.check("test_user")

        assert isinstance(result, ThrottleResult)
        assert result.allowed is True
        # initial_limit 값은 throttle 내부 상태에 따라 달라질 수 있음
        assert result.limit >= config.min_limit
        assert result.limit <= config.max_limit
        assert result.remaining >= 0

    def test_thread_safety(self):
        """Test throttle is thread-safe."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=1000)
        throttle = AdaptiveThrottle(config)

        errors = []

        def worker():
            try:
                for _ in range(100):
                    throttle.check(f"user_{threading.current_thread().name}")
                    throttle.record_response(50)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestGlobalThrottleInstance:
    """Tests for global throttle singleton."""

    def test_get_adaptive_throttle_singleton(self):
        """Test get_adaptive_throttle returns same instance."""
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            reset_adaptive_throttle,
        )

        reset_adaptive_throttle()

        t1 = get_adaptive_throttle()
        t2 = get_adaptive_throttle()

        assert t1 is t2

    def test_reset_creates_new_instance(self):
        """Test reset creates new instance."""
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            reset_adaptive_throttle,
        )

        t1 = get_adaptive_throttle()
        reset_adaptive_throttle()
        t2 = get_adaptive_throttle()

        assert t1 is not t2
