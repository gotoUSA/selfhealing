"""
GateAlertManager 테스트.

알림 쿨다운 및 관리 기능 테스트.
"""



class TestGateAlertManager:
    """GateAlertManager 테스트."""

    def test_alert_manager_cooldown(self):
        """알림 쿨다운 동작."""
        from selfhealing.services.error_budget_gate import GateAlertManager

        manager = GateAlertManager(cooldown_seconds=300)

        # 첫 번째 알림 - 성공
        result1 = manager._can_send_alert("test_alert")
        assert result1 is True

        manager._record_alert_sent("test_alert")

        # 두 번째 알림 - 쿨다운 중
        result2 = manager._can_send_alert("test_alert")
        assert result2 is False

    def test_alert_manager_different_types(self):
        """다른 알림 타입은 별도 쿨다운."""
        from selfhealing.services.error_budget_gate import GateAlertManager

        manager = GateAlertManager(cooldown_seconds=300)

        manager._record_alert_sent("type_a")

        # type_b는 별도
        result = manager._can_send_alert("type_b")
        assert result is True

    def test_alert_manager_reset(self):
        """알림 쿨다운 리셋."""
        from selfhealing.services.error_budget_gate import GateAlertManager

        manager = GateAlertManager(cooldown_seconds=300)

        manager._record_alert_sent("test_alert")
        assert manager._can_send_alert("test_alert") is False

        manager.reset()

        assert manager._can_send_alert("test_alert") is True

    def test_alert_manager_status(self):
        """Alert Manager 상태 조회."""
        from selfhealing.services.error_budget_gate import GateAlertManager

        manager = GateAlertManager(cooldown_seconds=600)

        status = manager.get_status()

        assert status["cooldown_seconds"] == 600
        assert "last_alerts" in status
