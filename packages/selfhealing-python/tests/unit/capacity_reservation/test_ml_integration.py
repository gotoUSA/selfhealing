"""
ML Integration & PoolWatchdog shrink_guard Unit Tests.

Test Categories:
    A. Behavior — PoolWatchdog shrink_guard: 억제/허용/reason 전파
    B. Behavior — SpikeClassifier context: 예정 이벤트 기간 HEALTHY_SURGE 분류
    C. Contract — EventType: SCHEDULED_EVENT_STARTED/ENDED 존재 확인
"""

from unittest.mock import MagicMock

from selfhealing.core.pool_monitor import PoolHealthStatus
from selfhealing.core.pool_watchdog import (
    PoolRecoveryAction,
    PoolWatchdog,
)
from selfhealing.services.event_bus.bus import EventType
from selfhealing.services.predictive_forecaster.proactive_action import (
    SpikeClassifier,
    SpikeType,
)

# =============================================================================
# A. Behavior — PoolWatchdog shrink_guard
# =============================================================================


class TestPoolWatchdogShrinkGuardBehavior:
    """shrink_guard Callback Guard 동작 검증."""

    def _make_watchdog(self, shrink_guard=None, expanded_by=5):
        """PoolWatchdog를 테스트용으로 생성."""
        monitor = MagicMock()
        monitor.check_health.return_value = (
            PoolHealthStatus.HEALTHY,
            MagicMock(usage_percent=30.0, max_connections=20),
        )
        handler = MagicMock()
        handler.shrink_pool.return_value = True

        watchdog = PoolWatchdog(
            monitor=monitor,
            recovery_handler=handler,
            auto_expand=True,
            shrink_guard=shrink_guard,
        )
        watchdog._expanded_by = expanded_by
        return watchdog, handler

    def test_shrink_guard_suppresses_shrink_with_reason(self):
        """guard가 reason 반환 시 shrink 미수행 + message에 reason 포함."""
        guard = lambda: "ScheduledEvent"
        watchdog, handler = self._make_watchdog(shrink_guard=guard)

        result = watchdog.check_and_recover()

        handler.shrink_pool.assert_not_called()
        assert result.action == PoolRecoveryAction.NONE
        assert "ScheduledEvent" in result.message
        assert result.success is True

    def test_shrink_guard_none_allows_normal_shrink(self):
        """guard가 None 반환 시 기존 shrink 로직 정상 동작."""
        guard = lambda: None
        watchdog, handler = self._make_watchdog(shrink_guard=guard)

        result = watchdog.check_and_recover()

        handler.shrink_pool.assert_called_once()

    def test_no_shrink_guard_allows_normal_shrink(self):
        """shrink_guard가 설정되지 않으면 기존 shrink 로직 정상 동작."""
        watchdog, handler = self._make_watchdog(shrink_guard=None)

        result = watchdog.check_and_recover()

        handler.shrink_pool.assert_called_once()

    def test_shrink_guard_emergency_mode_reason(self):
        """guard가 EmergencyMode reason 반환 시 message에 포함."""
        guard = lambda: "EmergencyMode"
        watchdog, _ = self._make_watchdog(shrink_guard=guard)

        result = watchdog.check_and_recover()

        assert "EmergencyMode" in result.message

    def test_shrink_guard_not_called_when_no_expansion(self):
        """expanded_by == 0이면 guard가 호출되지 않음 (shrink 시도 자체 안 함)."""
        call_count = 0

        def counting_guard():
            nonlocal call_count
            call_count += 1
            return "ShouldNotBeHere"

        watchdog, _ = self._make_watchdog(shrink_guard=counting_guard, expanded_by=0)
        result = watchdog.check_and_recover()
        assert call_count == 0


# =============================================================================
# B. Behavior — SpikeClassifier context
# =============================================================================


class TestSpikeClassifierScheduledEventContextBehavior:
    """예정 이벤트 context가 분류 결과에 미치는 영향 검증."""

    def _make_classifier(self):
        return SpikeClassifier(
            error_rate_threshold=0.05,
            acceleration_threshold=100.0,
            sensitivity_multiplier=1.0,
        )

    def _make_stable_histories(self):
        """에러율 안정, RPS 급등 히스토리."""
        rps = [100.0, 110.0, 130.0, 160.0, 200.0, 250.0, 320.0, 400.0, 500.0, 650.0]
        error_rate = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.02]
        latency = [50.0, 52.0, 55.0, 58.0, 60.0, 63.0, 66.0, 70.0, 75.0, 80.0]
        return rps, error_rate, latency

    def test_scheduled_event_with_stable_errors_returns_healthy_surge(self):
        """scheduled_event=True + error 안정 → HEALTHY_SURGE."""
        classifier = self._make_classifier()
        rps, error_rate, latency = self._make_stable_histories()
        context = {"scheduled_event": True}

        result = classifier.classify(rps, error_rate, latency, context=context)
        assert result == SpikeType.HEALTHY_SURGE

    def test_no_context_with_rps_spike_may_not_be_healthy_surge(self):
        """context 없으면 동일 히스토리에서도 HEALTHY_SURGE 보장 불가."""
        classifier = self._make_classifier()
        rps, error_rate, latency = self._make_stable_histories()

        result = classifier.classify(rps, error_rate, latency, context=None)
        # context 없으면 HEALTHY_SURGE 또는 다른 타입일 수 있음
        assert isinstance(result, SpikeType)

    def test_scheduled_event_with_high_errors_returns_anomalous(self):
        """scheduled_event=True이지만 error_rate 급등 → ANOMALOUS_SPIKE."""
        classifier = self._make_classifier()
        rps = [100.0] * 10
        error_rate = [0.01, 0.01, 0.01, 0.01, 0.01, 0.02, 0.04, 0.08, 0.15, 0.25]
        latency = [50.0] * 10
        context = {"scheduled_event": True}

        result = classifier.classify(rps, error_rate, latency, context=context)
        assert result == SpikeType.ANOMALOUS_SPIKE

    def test_classify_features_passes_context_through(self):
        """classify_features()가 context를 classify()에 전달."""
        classifier = self._make_classifier()
        features = {
            "rps_history": ",".join(str(x) for x in [100.0] * 10),
            "error_rate_history": ",".join(str(x) for x in [0.01] * 10),
            "latency_history": ",".join(str(x) for x in [50.0] * 10),
        }
        context = {"scheduled_event": True}

        label, confidence = classifier.classify_features(features, context=context)
        assert label == SpikeType.HEALTHY_SURGE.value


# =============================================================================
# C. Contract — EventType
# =============================================================================


class TestCapacityReservationEventTypeContract:
    """EventType에 Capacity Reservation 이벤트 존재 확인."""

    def test_scheduled_event_started_exists(self):
        """SCHEDULED_EVENT_STARTED 이벤트 타입 존재."""
        assert EventType.SCHEDULED_EVENT_STARTED.value == "scheduled_event_started"

    def test_scheduled_event_ended_exists(self):
        """SCHEDULED_EVENT_ENDED 이벤트 타입 존재."""
        assert EventType.SCHEDULED_EVENT_ENDED.value == "scheduled_event_ended"
