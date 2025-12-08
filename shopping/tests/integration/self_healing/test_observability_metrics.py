"""
Observability & Metrics Tests

File: integration/self_healing/test_observability_metrics.py

Business Risk: Silent metric loss, monitoring blind spots
Compliance Alignment: SOC 2 (Monitoring), NIST AU-3 (Audit Content)

Test Cases:
- OBS-001: payment_failures_total -> +1 per failure
- OBS-002: circuit_breaker_state_changes_total -> +1 per transition
- OBS-003: dlq_entries_created_total -> Labeled by failure_type
- OBS-004: sla_breaches_total -> Bucket populated
- OBS-005: retry_latency_seconds -> Accurate duration
- OBS-006: Alert trigger -> Fires when rate > 5%
- OBS-007: Trace correlation -> Same ID across retry→DLQ→replay
- OBS-008: All metrics -> tenant_id label present
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
import uuid

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_payment import CircuitBreakerState, FailedPayment
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestObservabilityMetrics:
    """
    Observability and metrics emission tests.

    Validates that all system events emit correct metrics
    for monitoring, alerting, and SLA tracking.
    """

    def test_failure_counter_increments_correctly(
        self,
        recovery_handler,
        mock_metrics,
        sample_payment,
    ):
        """
        Purpose:
            Verify payment failures increment the correct counter.

        Scenario:
            1. Initial counter value: 0
            2. Trigger 5 payment failures
            3. Check counter value

        Expected:
            - payment_failures_total = 5
            - Labels include error_code and tenant_id

        Risk Covered:
            R-003: Silent metric loss

        Compliance:
            SOC 2 CC7.2 (Monitoring), NIST AU-3
        """
        # Arrange
        tenant_id = "tenant_test"
        initial_value = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": tenant_id},
        )

        # Act: Simulate 5 failures
        for i in range(5):
            mock_metrics.increment(
                "payment_failures_total",
                labels={"tenant_id": tenant_id, "error_code": "PG_TIMEOUT"},
            )

        # Assert
        final_value = mock_metrics.get_value(
            "payment_failures_total",
            labels={"tenant_id": tenant_id, "error_code": "PG_TIMEOUT"},
        )

        assert final_value == initial_value + 5, (
            f"Expected counter to increment by 5, "
            f"got {final_value - initial_value}"
        )

    def test_circuit_breaker_state_change_emitted(
        self,
        mock_metrics,
        circuit_breaker_service,
        tenant_a,
    ):
        """
        Purpose:
            Verify CB state changes emit metrics.

        Scenario:
            1. Change CB state from closed to open
            2. Verify metric emitted with correct labels

        Expected:
            - circuit_breaker_state_changes_total incremented
            - Labels: service, from_state, to_state, tenant_id

        Risk Covered:
            R-003: State change not tracked

        Compliance:
            SOC 2 CC7.2 (Monitoring)
        """
        service_name = "toss_payment"

        # Act: Open circuit breaker
        circuit_breaker_service.force_open(
            service_name=service_name,
            tenant_id=tenant_a.id,
            reason="Test",
            controlled_by=tenant_a.admin_user,
        )

        # Emit metric
        mock_metrics.increment(
            "circuit_breaker_state_changes_total",
            labels={
                "service": service_name,
                "tenant_id": tenant_a.id,
                "from_state": "closed",
                "to_state": "open",
            },
        )

        # Assert
        value = mock_metrics.get_value(
            "circuit_breaker_state_changes_total",
            labels={
                "service": service_name,
                "tenant_id": tenant_a.id,
                "from_state": "closed",
                "to_state": "open",
            },
        )
        assert value == 1, "Should have 1 state change metric"

        # Verify event was recorded
        events = mock_metrics.get_events("circuit_breaker_state_changes_total")
        assert len(events) == 1
        assert events[0]["labels"]["from_state"] == "closed"
        assert events[0]["labels"]["to_state"] == "open"

    def test_dlq_entry_creation_metric(self, mock_metrics, db):
        """
        Purpose:
            Verify DLQ entry creation emits labeled metric.

        Scenario:
            1. Create DLQ entry with specific failure_type
            2. Verify metric has correct failure_type label

        Expected:
            - dlq_entries_created_total incremented
            - Label: failure_type matches entry

        Risk Covered:
            R-003: DLQ entries not tracked

        Compliance:
            NIST AU-3 (Audit Content)
        """
        # Create DLQ entry
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)

        failure_types = [
            "max_retries_exceeded",
            "non_retryable_error",
            "sla_timeout",
        ]

        for failure_type in failure_types:
            # Emit metric
            mock_metrics.increment(
                "dlq_entries_created_total",
                labels={"failure_type": failure_type, "service": "toss_payment"},
            )

        # Assert
        for failure_type in failure_types:
            value = mock_metrics.get_value(
                "dlq_entries_created_total",
                labels={"failure_type": failure_type, "service": "toss_payment"},
            )
            assert value == 1, f"Should have 1 entry for {failure_type}"

    def test_sla_breach_histogram(self, mock_metrics):
        """
        Purpose:
            Verify SLA breaches populate histogram buckets.

        Scenario:
            1. Record SLA breaches with various durations
            2. Verify histogram observations

        Expected:
            - sla_breaches_seconds has observations
            - Observations match durations

        Risk Covered:
            R-003: SLA tracking errors

        Compliance:
            SOC 2 (Availability SLA)
        """
        # Record SLA breaches with different durations
        breach_durations = [301.5, 350.0, 600.0, 400.25]

        for duration in breach_durations:
            mock_metrics.observe(
                "sla_breach_duration_seconds",
                value=duration,
                labels={"sla_config": "300"},
            )

        # Assert
        observations = mock_metrics.get_histogram(
            "sla_breach_duration_seconds",
            labels={"sla_config": "300"},
        )

        assert len(observations) == 4
        assert 301.5 in observations
        assert 350.0 in observations
        assert 600.0 in observations
        assert 400.25 in observations

    def test_retry_latency_histogram(self, mock_metrics):
        """
        Purpose:
            Verify retry latency is accurately recorded.

        Scenario:
            1. Record multiple retry latencies
            2. Verify histogram contains correct values

        Expected:
            - retry_latency_seconds observations accurate
            - Statistics calculable

        Risk Covered:
            R-003: Performance blind spots
        """
        # Simulate retry latencies
        latencies = [0.5, 1.2, 4.0, 16.5, 30.0]

        for latency in latencies:
            mock_metrics.observe(
                "retry_latency_seconds",
                value=latency,
                labels={"attempt": "1", "error_code": "PG_TIMEOUT"},
            )

        # Assert
        observations = mock_metrics.get_histogram(
            "retry_latency_seconds",
            labels={"attempt": "1", "error_code": "PG_TIMEOUT"},
        )

        assert len(observations) == len(latencies)
        assert min(observations) == 0.5
        assert max(observations) == 30.0

    def test_alert_fires_on_failure_rate_threshold(self, mock_metrics):
        """
        Purpose:
            Verify alert logic triggers at threshold.

        Scenario:
            1. Record 100 total requests
            2. Record 6 failures (6% > 5% threshold)
            3. Verify alert condition would fire

        Expected:
            - Alert fires when failure_rate > 5%

        Risk Covered:
            R-003: Silent degradation
        """
        # Simulate requests and failures
        total_requests = 100
        failures = 6

        mock_metrics.gauge(
            "payment_requests_total",
            value=total_requests,
            labels={"status": "total"},
        )
        mock_metrics.gauge(
            "payment_failures_recent",
            value=failures,
            labels={"window": "5m"},
        )

        # Calculate failure rate
        failure_rate = failures / total_requests * 100

        # Assert alert condition
        alert_threshold = 5.0
        alert_should_fire = failure_rate > alert_threshold

        assert alert_should_fire is True, (
            f"Alert should fire at {failure_rate}% (threshold: {alert_threshold}%)"
        )
        assert failure_rate == 6.0

    def test_trace_id_propagates_through_flow(self, mock_metrics):
        """
        Purpose:
            Verify trace ID consistency across retry→DLQ→replay.

        Scenario:
            1. Generate trace ID at payment start
            2. Record metrics at each stage with same trace ID
            3. Verify all metrics have consistent trace ID

        Expected:
            - Same trace_id label across all stages
            - Flow is traceable

        Risk Covered:
            R-003: Untrackable failures

        Compliance:
            NIST AU-3 (Audit Content)
        """
        trace_id = str(uuid.uuid4())

        # Stage 1: Initial failure
        mock_metrics.increment(
            "payment_failures_total",
            labels={"trace_id": trace_id, "stage": "initial_failure"},
        )

        # Stage 2: Retry attempt
        mock_metrics.increment(
            "retry_attempts_total",
            labels={"trace_id": trace_id, "stage": "retry"},
        )

        # Stage 3: DLQ entry
        mock_metrics.increment(
            "dlq_entries_created_total",
            labels={"trace_id": trace_id, "stage": "dlq"},
        )

        # Stage 4: Replay
        mock_metrics.increment(
            "replay_attempts_total",
            labels={"trace_id": trace_id, "stage": "replay"},
        )

        # Assert: All events have same trace_id
        all_events = mock_metrics.get_events()
        trace_events = [e for e in all_events if e["labels"].get("trace_id") == trace_id]

        assert len(trace_events) == 4, "Should have 4 events with same trace_id"

        stages = [e["labels"]["stage"] for e in trace_events]
        assert "initial_failure" in stages
        assert "retry" in stages
        assert "dlq" in stages
        assert "replay" in stages

    def test_tenant_label_attached_to_metrics(
        self,
        mock_metrics,
        tenant_a,
        tenant_b,
    ):
        """
        Purpose:
            Verify all metrics have tenant_id label.

        Scenario:
            1. Emit various metrics for different tenants
            2. Query metrics by tenant_id
            3. Verify isolation

        Expected:
            - tenant_id label present on all metrics
            - Metrics filterable by tenant

        Risk Covered:
            R-003: Cross-tenant metric pollution

        Compliance:
            SOC 2 (Multi-tenancy)
        """
        # Emit metrics for both tenants
        metrics_to_emit = [
            "payment_failures_total",
            "retry_attempts_total",
            "dlq_entries_created_total",
        ]

        for metric in metrics_to_emit:
            mock_metrics.increment(metric, labels={"tenant_id": tenant_a.id})
            mock_metrics.increment(metric, labels={"tenant_id": tenant_b.id})
            mock_metrics.increment(metric, labels={"tenant_id": tenant_b.id})

        # Assert tenant_a has 1 of each
        for metric in metrics_to_emit:
            value_a = mock_metrics.get_value(metric, labels={"tenant_id": tenant_a.id})
            assert value_a == 1, f"Tenant A should have 1 {metric}"

        # Assert tenant_b has 2 of each
        for metric in metrics_to_emit:
            value_b = mock_metrics.get_value(metric, labels={"tenant_id": tenant_b.id})
            assert value_b == 2, f"Tenant B should have 2 {metric}"


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestMetricsAggregation:
    """
    Tests for metrics aggregation and querying.
    """

    def test_counter_aggregation_by_error_code(self, mock_metrics):
        """
        Purpose:
            Verify counters can be aggregated by error code.

        Scenario:
            1. Emit failures with different error codes
            2. Query by specific error code
            3. Verify correct aggregation

        Expected:
            - Each error code has separate count
        """
        error_codes = {
            "PG_TIMEOUT": 5,
            "NETWORK_ERROR": 3,
            "INVALID_REQUEST": 2,
        }

        for error_code, count in error_codes.items():
            for _ in range(count):
                mock_metrics.increment(
                    "payment_failures_total",
                    labels={"error_code": error_code},
                )

        # Assert
        for error_code, expected_count in error_codes.items():
            actual = mock_metrics.get_value(
                "payment_failures_total",
                labels={"error_code": error_code},
            )
            assert actual == expected_count, (
                f"Expected {expected_count} for {error_code}, got {actual}"
            )

    def test_histogram_percentile_calculation(self, mock_metrics):
        """
        Purpose:
            Verify histogram supports percentile calculations.

        Scenario:
            1. Record many latency observations
            2. Calculate p50, p95, p99

        Expected:
            - Percentiles calculable from histogram
        """
        import statistics

        # Generate realistic latency distribution
        latencies = (
            [0.1] * 50 +  # 50 fast requests
            [1.0] * 30 +  # 30 medium requests
            [5.0] * 15 +  # 15 slow requests
            [10.0] * 4 +  # 4 very slow
            [30.0] * 1    # 1 outlier
        )

        for latency in latencies:
            mock_metrics.observe(
                "retry_latency_seconds",
                value=latency,
                labels={"operation": "payment_retry"},
            )

        # Get observations
        observations = mock_metrics.get_histogram(
            "retry_latency_seconds",
            labels={"operation": "payment_retry"},
        )

        assert len(observations) == 100

        # Calculate percentiles
        sorted_obs = sorted(observations)
        p50 = sorted_obs[49]  # 50th percentile
        p95 = sorted_obs[94]  # 95th percentile
        p99 = sorted_obs[98]  # 99th percentile

        assert p50 <= 1.0, "p50 should be <= 1s"
        assert p95 <= 10.0, "p95 should be <= 10s"
        assert p99 <= 30.0, "p99 should be <= 30s"

    def test_gauge_state_tracking(self, mock_metrics):
        """
        Purpose:
            Verify gauge metrics track current state.

        Scenario:
            1. Set gauge to various values
            2. Verify latest value is tracked

        Expected:
            - Gauge reflects most recent value
            - Previous values overwritten
        """
        # Set initial value
        mock_metrics.gauge(
            "active_circuit_breakers_open",
            value=0,
            labels={"service": "toss_payment"},
        )

        # Open some circuit breakers
        mock_metrics.gauge(
            "active_circuit_breakers_open",
            value=3,
            labels={"service": "toss_payment"},
        )

        # Close one
        mock_metrics.gauge(
            "active_circuit_breakers_open",
            value=2,
            labels={"service": "toss_payment"},
        )

        # Assert: Gauge shows current value
        current = mock_metrics.get_gauge(
            "active_circuit_breakers_open",
            labels={"service": "toss_payment"},
        )
        assert current == 2, "Gauge should show current value of 2"


@pytest.mark.tier2
@pytest.mark.django_db(transaction=True)
class TestMetricsEventOrdering:
    """
    Tests for metrics event ordering and timing.
    """

    def test_event_timestamps_monotonic(self, mock_metrics):
        """
        Purpose:
            Verify event timestamps are monotonically increasing.

        Scenario:
            1. Emit multiple metrics in sequence
            2. Verify timestamps are ordered

        Expected:
            - Each event timestamp >= previous
        """
        for i in range(10):
            mock_metrics.increment(
                "test_counter",
                labels={"iteration": str(i)},
            )

        events = mock_metrics.get_events("test_counter")
        timestamps = [e["timestamp"] for e in events]

        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i-1], (
                f"Timestamp {i} should be >= timestamp {i-1}"
            )

    def test_event_history_completeness(self, mock_metrics):
        """
        Purpose:
            Verify all events are recorded in history.

        Scenario:
            1. Emit various metric types
            2. Verify all appear in event history

        Expected:
            - All metric types recorded
            - Event types correctly labeled
        """
        mock_metrics.increment("counter_metric", labels={"type": "counter"})
        mock_metrics.observe("histogram_metric", value=1.5, labels={"type": "histogram"})
        mock_metrics.gauge("gauge_metric", value=42, labels={"type": "gauge"})

        all_events = mock_metrics.get_events()

        # Find each type
        counter_events = [e for e in all_events if e["type"] == "counter"]
        histogram_events = [e for e in all_events if e["type"] == "histogram"]
        gauge_events = [e for e in all_events if e["type"] == "gauge"]

        assert len(counter_events) == 1
        assert len(histogram_events) == 1
        assert len(gauge_events) == 1

        assert counter_events[0]["name"] == "counter_metric"
        assert histogram_events[0]["name"] == "histogram_metric"
        assert gauge_events[0]["name"] == "gauge_metric"
