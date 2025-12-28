#!/usr/bin/env python
"""
Quick test for V3 modules (Throttle + Corruption Shield)
Run without Django/Database dependencies.
"""

import sys
import os

# Add package to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'packages', 'selfhealing-python', 'src'))

# Disable Django
os.environ['DJANGO_SETTINGS_MODULE'] = ''


def test_throttle_config():
    """Test ThrottleConfig defaults."""
    from selfhealing.services.throttle.config import ThrottleConfig
    
    config = ThrottleConfig()
    assert config.initial_limit == 100
    assert config.max_limit == 500
    assert config.sla_warning_ms == 200
    assert config.sla_critical_ms == 500
    print("✓ ThrottleConfig passed")


def test_sliding_window():
    """Test SlidingWindowThrottle."""
    from selfhealing.services.throttle.base import SlidingWindowThrottle
    from selfhealing.services.throttle.config import ThrottleConfig
    
    config = ThrottleConfig(initial_limit=5)
    throttle = SlidingWindowThrottle(config)
    
    # First 5 should pass
    for i in range(5):
        result = throttle.check("user")
        assert result.allowed, f"Request {i+1} should be allowed"
    
    # 6th should be denied
    result = throttle.check("user")
    assert not result.allowed, "6th request should be denied"
    assert result.remaining == 0
    
    print("✓ SlidingWindowThrottle passed")


def test_adaptive_throttle():
    """Test AdaptiveThrottle Netflix Gradient."""
    from selfhealing.services.throttle.adaptive import AdaptiveThrottle
    from selfhealing.services.throttle.config import ThrottleConfig
    
    config = ThrottleConfig(
        initial_limit=100,
        min_limit=10,
        sla_warning_ms=100,
        sla_critical_ms=200,
    )
    throttle = AdaptiveThrottle(config)
    
    initial_limit = throttle.current_limit
    
    # Record high RTT - should decrease limit
    for _ in range(20):
        throttle.record_response(500)  # Critical RTT
    
    assert throttle.current_limit < initial_limit, \
        f"Limit should decrease: {throttle.current_limit} < {initial_limit}"
    
    print(f"✓ AdaptiveThrottle passed (limit: {initial_limit} → {throttle.current_limit})")


def test_corruption_shield_valid():
    """Test valid data passes."""
    from selfhealing.services.corruption_shield import CorruptionShield
    
    shield = CorruptionShield()
    
    data = {
        "amount": 50000,
        "order_id": "order_12345",
        "status": "DONE",
    }
    
    result = shield.validate(data)
    assert result.is_valid, f"Valid data should pass: {result.violations}"
    assert not result.blocked
    
    print("✓ CorruptionShield valid data passed")


def test_corruption_shield_sql_injection():
    """Test SQL injection is blocked."""
    from selfhealing.services.corruption_shield import CorruptionShield
    
    shield = CorruptionShield()
    
    data = {
        "amount": 50000,
        "order_id": "order_123; DROP TABLE users; --",
    }
    
    result = shield.validate(data)
    assert not result.is_valid, "SQL injection should fail validation"
    assert result.blocked, "SQL injection should be blocked"
    
    violations = [v.code for v in result.violations]
    assert "injection_attempt" in violations, f"Expected injection_attempt in {violations}"
    
    print("✓ CorruptionShield SQL injection blocked")


def test_corruption_shield_negative_amount():
    """Test negative amount is blocked."""
    from selfhealing.services.corruption_shield import CorruptionShield
    
    shield = CorruptionShield()
    
    data = {
        "amount": -10000,
        "order_id": "order_12345",
    }
    
    result = shield.validate(data)
    assert not result.is_valid, "Negative amount should fail"
    assert result.blocked, "Negative amount should be blocked"
    
    violations = [v.code for v in result.violations]
    assert "negative_amount" in violations, f"Expected negative_amount in {violations}"
    
    print("✓ CorruptionShield negative amount blocked")


def test_corruption_shield_xss():
    """Test XSS attack is blocked."""
    from selfhealing.services.corruption_shield import CorruptionShield
    
    shield = CorruptionShield()
    
    data = {
        "amount": 50000,
        "order_id": "<script>alert(1)</script>",
    }
    
    result = shield.validate(data)
    assert not result.is_valid, "XSS should fail validation"
    
    print("✓ CorruptionShield XSS blocked")


def test_corruption_shield_amount_mismatch():
    """Test amount mismatch is detected."""
    from selfhealing.services.corruption_shield import CorruptionShield
    
    shield = CorruptionShield()
    
    data = {
        "amount": 50000,
        "order_id": "order_12345",
    }
    context = {"expected_amount": 100000}
    
    result = shield.validate(data, context)
    assert not result.is_valid, "Amount mismatch should fail"
    
    violations = [v.code for v in result.violations]
    assert "amount_mismatch" in violations, f"Expected amount_mismatch in {violations}"
    
    print("✓ CorruptionShield amount mismatch detected")


if __name__ == "__main__":
    print("=" * 60)
    print("V3 Module Tests (Throttle + Corruption Shield)")
    print("=" * 60)
    print()
    
    tests = [
        test_throttle_config,
        test_sliding_window,
        test_adaptive_throttle,
        test_corruption_shield_valid,
        test_corruption_shield_sql_injection,
        test_corruption_shield_negative_amount,
        test_corruption_shield_xss,
        test_corruption_shield_amount_mismatch,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    if failed > 0:
        sys.exit(1)
    else:
        print("✅ All V3 module tests passed!")
        sys.exit(0)
