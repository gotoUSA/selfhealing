"""
Chaos Experiment E2E Lifecycle Tests

Tests for full scenario integration:
- Complete chaos experiment flow (4 tests)

Test Coverage:
- Experiment lifecycle (create → analyze → execute → notify → cleanup)
- Multi-component integration
- Error handling and recovery

Total: 4 tests
"""

import time
from unittest.mock import MagicMock, patch

# =============================================================================
# E2E Integration Tests (4 tests)
# =============================================================================


class TestChaosE2EIntegration:
    """End-to-End integration tests for Chaos system - 4 tests."""

    def test_complete_experiment_lifecycle(self):
        """Test complete experiment lifecycle from creation to cleanup."""
        from selfhealing.services.chaos.blast_radius_analyzer import (
            get_blast_radius_analyzer,
        )
        from selfhealing.services.chaos.impact_predictor import get_impact_predictor
        from selfhealing.services.chaos.synthetic_load import (
            GeneratorState,
            LoadConfig,
            LoadPattern,
            SyntheticLoadGenerator,
        )
        from selfhealing.services.chaos.traffic_shaper import (
            ShapingConfig,
            ShapingMode,
            TrafficShaper,
        )

        experiment_id = "e2e-test-001"
        target_service = "payment-api"

        # Phase 1: Pre-flight analysis (Dry Run)
        predictor = get_impact_predictor()
        analyzer = get_blast_radius_analyzer()

        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service=target_service,
            config={"latency_ms": 200},
        )
        assert outcome is not None

        blast_radius = analyzer.analyze(
            target_service=target_service,
            experiment_type="latency_injection",
        )
        assert blast_radius.target_service == target_service

        # Phase 2: Setup traffic shaping
        shaper = TrafficShaper(experiment_id=experiment_id)
        shaper.configure(
            ShapingConfig(
                mode=ShapingMode.RATE_LIMIT,
                target_rps=20.0,
            )
        )

        # Phase 3: Generate synthetic traffic
        load_gen = SyntheticLoadGenerator(
            experiment_id=experiment_id,
            target_service=target_service,
        )

        requests_processed = []

        def handler(req):
            if shaper.should_allow():
                requests_processed.append(req)
                return True
            return False

        load_gen.start(
            LoadConfig(
                target_rps=30.0,
                duration_seconds=0.3,
                pattern=LoadPattern.CONSTANT,
            ),
            request_handler=handler,
        )

        time.sleep(0.5)
        stats = load_gen.stop()

        # Phase 4: Verify and cleanup
        assert load_gen.state == GeneratorState.STOPPED
        assert stats.total_requests > 0
        shaper.reset()

    def test_experiment_with_notification_flow(self):
        """Test experiment triggers notifications at lifecycle events."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert

        experiment_id = "e2e-test-002"
        target_service = "order-api"

        with patch("selfhealing.services.unified_notification.get_unified_notification_manager") as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            # Lifecycle: started → stopped
            result_started = send_chaos_experiment_alert(
                experiment_id=experiment_id,
                experiment_type="latency_injection",
                target_service=target_service,
                event_type="started",
                details={"duration_seconds": 60},
            )

            result_stopped = send_chaos_experiment_alert(
                experiment_id=experiment_id,
                experiment_type="latency_injection",
                target_service=target_service,
                event_type="stopped",
                details={"result": "success", "requests_processed": 1000},
            )

            assert result_started is True
            assert result_stopped is True

            # Both notifications sent
            assert mock_instance.notify.call_count == 2

            # Verify correct event types
            calls = mock_instance.notify.call_args_list
            assert "started" in str(calls[0])
            assert "stopped" in str(calls[1])

    def test_experiment_error_handling_and_recovery(self):
        """Test experiment error handling and cleanup on failure."""
        from selfhealing.services.chaos.notification import send_chaos_experiment_alert
        from selfhealing.services.chaos.synthetic_load import (
            GeneratorState,
            LoadConfig,
            SyntheticLoadGenerator,
            cleanup_generator,
        )

        experiment_id = "e2e-test-003"
        target_service = "error-prone-service"

        # Setup with error-prone handler
        load_gen = SyntheticLoadGenerator(
            experiment_id=experiment_id,
            target_service=target_service,
        )

        error_count = [0]

        def error_handler(req):
            error_count[0] += 1
            if error_count[0] >= 2:
                return False  # Simulate failures
            return True

        load_gen.start(
            LoadConfig(
                target_rps=30.0,
                duration_seconds=0.5,
            ),
            request_handler=error_handler,
        )

        time.sleep(0.7)
        stats = load_gen.stop()

        # Verify failure tracking
        assert stats.failed_requests > 0

        # Send failure notification
        with patch("selfhealing.services.unified_notification.get_unified_notification_manager") as mock_manager:
            mock_instance = MagicMock()
            mock_manager.return_value = mock_instance

            send_chaos_experiment_alert(
                experiment_id=experiment_id,
                experiment_type="latency_injection",
                target_service=target_service,
                event_type="failed",
                details={
                    "error": "High failure rate",
                    "failed_requests": stats.failed_requests,
                },
            )

            mock_instance.notify.assert_called_once()

        # Cleanup
        cleanup_generator(experiment_id, target_service)
        assert load_gen.state == GeneratorState.STOPPED

    def test_full_phase_integration(self):
        """Test integration across all phases (Phase 0-3 + Phase 4)."""
        from selfhealing.services.chaos.actionable_alert_urls import (
            ChaosActionableAlertUrlBuilder,
        )
        from selfhealing.services.chaos.blast_radius_analyzer import BlastRadiusAnalyzer
        from selfhealing.services.chaos.impact_predictor import ImpactPredictor
        from selfhealing.services.chaos.synthetic_load import (
            SYNTHETIC_HEADER,
            SYNTHETIC_VALUE,
            SyntheticTrafficGenerator,
        )
        from selfhealing.services.chaos.traffic_shaper import (
            ShapingConfig,
            ShapingMode,
            TrafficShaper,
        )
        from selfhealing.services.unified_notification import NotificationCategory

        experiment_id = "e2e-full-test"
        target_service = "payment-api"

        # Phase 0: Verify CHAOS category exists
        assert hasattr(NotificationCategory, "CHAOS")
        assert NotificationCategory.CHAOS.value == "chaos"

        # Phase 1: Actionable URLs
        url_builder = ChaosActionableAlertUrlBuilder()
        urls = url_builder.build_experiment_alert_urls(
            experiment_id=experiment_id,
            target_service=target_service,
        )
        assert urls.admin_stop_url is not None
        assert "action=stop" in urls.admin_stop_url

        # Phase 2: Dry Run
        predictor = ImpactPredictor()
        analyzer = BlastRadiusAnalyzer()

        outcome = predictor.predict_outcome(
            experiment_type="latency_injection",
            target_service=target_service,
        )
        assert outcome.predicted_cb_state is not None

        blast = analyzer.analyze(
            target_service=target_service,
            experiment_type="latency_injection",
        )
        assert blast.target_service == target_service

        # Phase 3: Synthetic Traffic
        traffic_gen = SyntheticTrafficGenerator(experiment_id=experiment_id)
        request = traffic_gen.create_synthetic_request(target_service=target_service)

        assert request.synthetic_headers[SYNTHETIC_HEADER] == SYNTHETIC_VALUE
        assert SyntheticTrafficGenerator.is_synthetic_request(request.synthetic_headers)

        # Traffic Shaper
        shaper = TrafficShaper(experiment_id=experiment_id)
        shaper.configure(ShapingConfig(mode=ShapingMode.RATE_LIMIT, target_rps=100))
        assert shaper.should_allow() is True

        # Phase 4: All integration verified
        stats = shaper.get_stats()
        assert stats.total_shaped > 0
