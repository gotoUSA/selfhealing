"""
AdaptiveThrottle.get_config_snapshot() 메서드 테스트.

테스트 대상:
- 설정 스냅샷 반환 구조
- 스냅샷 필드 존재 여부
- 현재 상태 반영 확인
"""



class TestGetConfigSnapshotReturnsDict:
    """get_config_snapshot() 반환 타입 테스트."""

    def test_returns_dict(self):
        """딕셔너리 반환 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert isinstance(snapshot, dict)


class TestGetConfigSnapshotFields:
    """get_config_snapshot() 필드 존재 여부 테스트."""

    def test_contains_current_limit(self):
        """current_limit 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "current_limit" in snapshot

    def test_contains_initial_limit(self):
        """initial_limit 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "initial_limit" in snapshot

    def test_contains_emergency_mode_active(self):
        """emergency_mode_active 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "emergency_mode_active" in snapshot

    def test_contains_full_stop_active(self):
        """full_stop_active 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "full_stop_active" in snapshot

    def test_contains_recovery_dampening_active(self):
        """recovery_dampening_active 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "recovery_dampening_active" in snapshot

    def test_contains_smoothed_rtt_ms(self):
        """smoothed_rtt_ms 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "smoothed_rtt_ms" in snapshot

    def test_contains_current_gradient(self):
        """current_gradient 필드 존재 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert "current_gradient" in snapshot


class TestGetConfigSnapshotReflectsState:
    """get_config_snapshot() 상태 반영 테스트."""

    def test_reflects_initial_limit_from_config(self):
        """config의 initial_limit 반영 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        expected_initial_limit = 200
        config = ThrottleConfig(initial_limit=expected_initial_limit)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert snapshot["initial_limit"] == expected_initial_limit

    def test_reflects_full_stop_state(self):
        """Full Stop 상태 반영 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # Full Stop 비활성 상태
        snapshot_before = throttle.get_config_snapshot()
        assert snapshot_before["full_stop_active"] is False

        # Full Stop 활성화
        throttle.activate_full_stop(reason="test")

        snapshot_after = throttle.get_config_snapshot()
        assert snapshot_after["full_stop_active"] is True

    def test_reflects_sla_warning_ms(self):
        """sla_warning_ms 반영 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        expected_sla = 150
        config = ThrottleConfig(initial_limit=100, sla_warning_ms=expected_sla)
        throttle = AdaptiveThrottle(config)

        snapshot = throttle.get_config_snapshot()

        assert snapshot["sla_warning_ms"] == expected_sla

    def test_reflects_current_limit_changes(self):
        """current_limit 변경 반영 확인."""
        from selfhealing.services.throttle.adaptive import AdaptiveThrottle
        from selfhealing.services.throttle.config import ThrottleConfig

        config = ThrottleConfig(initial_limit=100)
        throttle = AdaptiveThrottle(config)

        # limit 변경
        new_limit = 50
        throttle.current_limit = new_limit

        snapshot = throttle.get_config_snapshot()

        assert snapshot["current_limit"] == new_limit
