"""
Unit tests for forensic context module.
"""

import pytest
from datetime import datetime, timezone


class TestRetryAttempt:
    """Tests for RetryAttempt dataclass."""

    def test_create_attempt(self):
        from selfhealing.core.forensic import RetryAttempt

        attempt = RetryAttempt(
            attempt=1,
            error_code="TIMEOUT",
            error_message="Connection timed out",
            attempted_at="2024-12-10T10:00:00",
            backoff_seconds=4,
        )

        assert attempt.attempt == 1
        assert attempt.error_code == "TIMEOUT"
        assert attempt.backoff_seconds == 4

    def test_to_dict(self):
        from selfhealing.core.forensic import RetryAttempt

        attempt = RetryAttempt(
            attempt=2,
            error_code="500",
            error_message="Server error",
            attempted_at="2024-12-10T10:00:00",
            backoff_seconds=16,
        )

        d = attempt.to_dict()

        assert d["attempt"] == 2
        assert d["error_code"] == "500"
        assert d["backoff_seconds"] == 16


class TestStateSnapshot:
    """Tests for StateSnapshot dataclass."""

    def test_create_snapshot(self):
        from selfhealing.core.forensic import StateSnapshot

        # Domain-neutral: use states dict and get_state() method
        snapshot = StateSnapshot()
        snapshot.set_state("order_status", "pending")
        snapshot.set_state("payment_status", "completed")
        snapshot.set_state("user_points", 1000)

        assert snapshot.get_state("order_status") == "pending"
        assert snapshot.get_state("user_points") == 1000

    def test_to_dict(self):
        from selfhealing.core.forensic import StateSnapshot

        snapshot = StateSnapshot()
        snapshot.set_state("order_status", "shipped")
        snapshot.set_state("payment_status", "refunded")

        d = snapshot.to_dict()

        assert d["order_status"] == "shipped"
        assert d["payment_status"] == "refunded"

    def test_with_extra(self):
        from selfhealing.core.forensic import StateSnapshot

        snapshot = StateSnapshot(
            state_data={"entity_status": "pending"},
            extra={"custom_field": "value"},
        )

        d = snapshot.to_dict()
        assert d["custom_field"] == "value"

    def test_from_dict(self):
        from selfhealing.core.forensic import StateSnapshot

        data = {
            "order_status": "completed",
            "payment_status": "paid",
            "user_points": 500,
            "custom": "extra_value",
        }

        snapshot = StateSnapshot.from_dict(data)

        assert snapshot.get_state("order_status") == "completed"
        assert snapshot.get_state("custom") == "extra_value"


class TestForensicContext:
    """Tests for ForensicContext dataclass."""

    def test_create_empty_context(self):
        from selfhealing.core.forensic import ForensicContext

        ctx = ForensicContext()

        assert ctx.request_timestamp == ""
        assert ctx.latency_ms == 0
        assert len(ctx.retry_history) == 0

    def test_add_retry_attempt(self):
        from selfhealing.core.forensic import ForensicContext

        ctx = ForensicContext()
        ctx.add_retry_attempt(
            attempt=1,
            error_code="ERR001",
            error_message="Test error",
            backoff_seconds=5,
        )

        assert len(ctx.retry_history) == 1
        assert ctx.retry_history[0].attempt == 1
        assert ctx.retry_history[0].error_code == "ERR001"

    def test_capture_state_before(self):
        from selfhealing.core.forensic import ForensicContext

        ctx = ForensicContext()
        ctx.capture_state_before(
            order_status="pending",
            payment_status="waiting",
        )

        assert ctx.state_before is not None
        assert ctx.state_before.get_state("order_status") == "pending"

    def test_to_metadata(self):
        from selfhealing.core.forensic import ForensicContext

        ctx = ForensicContext(
            request_timestamp="2024-12-10T10:00:00",
            client_ip="192.168.1.1",
            task_id="abc123",
        )
        ctx.add_retry_attempt(1, "ERR", "error", 4)

        metadata = ctx.to_metadata()

        assert metadata["request_timestamp"] == "2024-12-10T10:00:00"
        assert metadata["client_ip"] == "192.168.1.1"
        assert metadata["task_id"] == "abc123"
        assert len(metadata["retry_history"]) == 1

    def test_from_metadata(self):
        from selfhealing.core.forensic import ForensicContext

        metadata = {
            "request_timestamp": "2024-12-10T10:00:00",
            "response_timestamp": "2024-12-10T10:00:01",
            "latency_ms": 1000,
            "client_ip": "10.0.0.1",
            "task_id": "task-123",
            "retry_history": [
                {
                    "attempt": 1,
                    "error_code": "500",
                    "error_message": "Server error",
                    "attempted_at": "2024-12-10T10:00:00",
                    "backoff_seconds": 4,
                }
            ],
            "state_before": {
                "order_status": "pending",
            },
        }

        ctx = ForensicContext.from_metadata(metadata)

        assert ctx.latency_ms == 1000
        assert ctx.client_ip == "10.0.0.1"
        assert len(ctx.retry_history) == 1
        assert ctx.state_before.get_state("order_status") == "pending"


class TestForensicContextBuilder:
    """Tests for ForensicContextBuilder."""

    def test_builder_basic(self):
        from selfhealing.core.forensic import ForensicContextBuilder

        ctx = (
            ForensicContextBuilder()
            .start_timing()
            .with_client_info(client_ip="10.0.0.1", user_agent="TestAgent")
            .with_task(task_id="task-1", task_name="my_task")
            .build()
        )

        assert ctx.client_ip == "10.0.0.1"
        assert ctx.user_agent == "TestAgent"
        assert ctx.task_id == "task-1"
        assert ctx.request_timestamp != ""  # Should be set

    def test_builder_with_state(self):
        from selfhealing.core.forensic import ForensicContextBuilder

        ctx = (
            ForensicContextBuilder()
            .with_state_before(order_status="pending")
            .with_state_after(order_status="completed")
            .build()
        )

        assert ctx.state_before.get_state("order_status") == "pending"
        assert ctx.state_after.get_state("order_status") == "completed"

    def test_builder_with_external_response(self):
        from selfhealing.core.forensic import ForensicContextBuilder

        ctx = (
            ForensicContextBuilder()
            .with_external_response(
                request_id="ext-123",
                response_code=200,
                response_body='{"status": "ok"}',
            )
            .build()
        )

        assert ctx.external_request_id == "ext-123"
        assert ctx.external_response_code == 200

    def test_builder_with_extra(self):
        from selfhealing.core.forensic import ForensicContextBuilder

        ctx = ForensicContextBuilder().with_extra(custom_field="value", another="data").build()

        assert ctx.extra["custom_field"] == "value"
        assert ctx.extra["another"] == "data"

    def test_builder_add_retry(self):
        from selfhealing.core.forensic import ForensicContextBuilder

        ctx = (
            ForensicContextBuilder()
            .add_retry_attempt(1, "TIMEOUT", "Connection timeout", 4)
            .add_retry_attempt(2, "TIMEOUT", "Connection timeout", 16)
            .build()
        )

        assert len(ctx.retry_history) == 2
        assert ctx.retry_history[0].attempt == 1
        assert ctx.retry_history[1].attempt == 2


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_capture_forensic_context(self):
        from selfhealing.core.forensic import capture_forensic_context

        ctx = capture_forensic_context(
            client_ip="192.168.1.1",
            task_id="task-abc",
            state_before={"entity_status": "pending"},
        )

        assert ctx.client_ip == "192.168.1.1"
        assert ctx.task_id == "task-abc"
        assert ctx.state_before.state_data.get("entity_status") == "pending"

    def test_create_snapshot_data(self):
        from selfhealing.core.forensic import create_snapshot_data

        # Domain-neutral snapshot creation
        snapshot = create_snapshot_data(
            entity_type="order",
            entity_id="123",
            status="pending",
            total_amount="10000",
            user_id=789,
            user_email="test@example.com",
        )

        assert snapshot["entity_type"] == "order"
        assert snapshot["entity_id"] == "123"
        assert snapshot["status"] == "pending"
        assert snapshot["user_id"] == 789

    def test_create_snapshot_data_partial(self):
        from selfhealing.core.forensic import create_snapshot_data

        # Partial snapshot
        snapshot = create_snapshot_data(
            entity_type="shipment",
            entity_id="100",
            status="shipped",
        )

        assert snapshot["entity_type"] == "shipment"
        assert snapshot["entity_id"] == "100"
        assert "user_id" not in snapshot

    def test_set_time_provider(self):
        from selfhealing.core.forensic import (
            set_time_provider,
            get_current_time_iso,
            ForensicContextBuilder,
        )

        # Set custom time provider
        set_time_provider(lambda: "2024-01-01T00:00:00")

        ctx = ForensicContextBuilder().start_timing().build()

        assert ctx.request_timestamp == "2024-01-01T00:00:00"

        # Reset to default
        set_time_provider(lambda: datetime.now(timezone.utc).isoformat())
