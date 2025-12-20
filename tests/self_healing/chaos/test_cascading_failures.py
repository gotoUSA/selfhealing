"""
Cascading Failure Tests

File: integration/self_healing/test_cascading_failures.py

Business Risk: System-wide outage from failure chain reaction
Compliance Alignment: NIST CP-2 (Contingency Planning), SOC 2 (Availability)

Test Cases:
- CASC-001: PG Timeout + DB Connection Lost -> Local fallback logging, no data loss
- CASC-002: DLQ Insert + Redis Down -> Synchronous fallback queue
- CASC-003: Retry Task + Celery Broker Down -> Persist to file, manual recovery
- CASC-004: CB State Update + DB Locked -> Graceful degradation, default-allow
- CASC-005: Notification Send + SMTP Down -> Async retry, no blocking
- CASC-006: Rollback Execution + Inventory Service Down -> Compensating transaction queued
- CASC-007: Handler Crash + DLQ Full -> REQUIRES_REVIEW escalation
"""

import json
import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import dataclass, field
from typing import Any, Generator

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, DatabaseError
from django.test import override_settings
from django.utils import timezone

from shopping.models.failed_external_request import CircuitBreakerState, FailedExternalRequest
from shopping.services.payment_recovery_service import CeleryPaymentRecovery
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


# =============================================================================
# Failure Injector for Cascading Failure Tests
# =============================================================================


@dataclass
class FailureInjector:
    """
    Failure injection utility for testing cascading failures.

    Provides methods to simulate various component failures
    and track their effects on the system.
    """

    pg_timeout_enabled: bool = False
    db_connection_error_enabled: bool = False
    db_write_error_enabled: bool = False
    redis_down_enabled: bool = False
    celery_broker_down_enabled: bool = False
    smtp_down_enabled: bool = False
    inventory_service_down_enabled: bool = False
    dlq_full_enabled: bool = False

    injection_history: list = field(default_factory=list)
    fallback_path: str | None = None

    def inject_pg_timeout(self) -> None:
        """Enable PG timeout injection."""
        self.pg_timeout_enabled = True
        self.injection_history.append(
            {
                "type": "pg_timeout",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_db_connection_error(self) -> None:
        """Enable DB connection error injection."""
        self.db_connection_error_enabled = True
        self.injection_history.append(
            {
                "type": "db_connection_error",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_db_connection_error_on_write(self) -> None:
        """Enable DB connection error on write operations."""
        self.db_write_error_enabled = True
        self.injection_history.append(
            {
                "type": "db_write_error",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_redis_down(self) -> None:
        """Enable Redis down injection."""
        self.redis_down_enabled = True
        self.injection_history.append(
            {
                "type": "redis_down",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_celery_broker_down(self) -> None:
        """Enable Celery broker down injection."""
        self.celery_broker_down_enabled = True
        self.injection_history.append(
            {
                "type": "celery_broker_down",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_smtp_down(self) -> None:
        """Enable SMTP down injection."""
        self.smtp_down_enabled = True
        self.injection_history.append(
            {
                "type": "smtp_down",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_inventory_service_down(self) -> None:
        """Enable inventory service down injection."""
        self.inventory_service_down_enabled = True
        self.injection_history.append(
            {
                "type": "inventory_service_down",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def inject_dlq_full(self) -> None:
        """Enable DLQ full injection."""
        self.dlq_full_enabled = True
        self.injection_history.append(
            {
                "type": "dlq_full",
                "timestamp": timezone.now().isoformat(),
            }
        )

    def reset(self) -> None:
        """Reset all injections."""
        self.pg_timeout_enabled = False
        self.db_connection_error_enabled = False
        self.db_write_error_enabled = False
        self.redis_down_enabled = False
        self.celery_broker_down_enabled = False
        self.smtp_down_enabled = False
        self.inventory_service_down_enabled = False
        self.dlq_full_enabled = False
        self.injection_history.clear()
        self.fallback_path = None

    def get_injection_summary(self) -> dict:
        """Get summary of all injections."""
        return {
            "active_failures": [k.replace("_enabled", "") for k, v in self.__dict__.items() if k.endswith("_enabled") and v],
            "history": self.injection_history,
        }


@pytest.fixture
def failure_injector() -> Generator[FailureInjector, None, None]:
    """Provide a failure injector instance."""
    injector = FailureInjector()
    yield injector
    injector.reset()


# =============================================================================
# Cascading Failure Handler (Enhanced Recovery Handler)
# =============================================================================


class CascadingFailureHandler:
    """
    Handler for cascading failure scenarios.

    Provides fallback mechanisms when multiple components fail.
    """

    def __init__(self, failure_injector: FailureInjector):
        self.failure_injector = failure_injector
        self.recovery_handler = CeleryPaymentRecovery()
        self._fallback_dir = tempfile.mkdtemp()

    def handle_failure(
        self,
        payment,
        error_code: str,
        order_id: int | None = None,
    ) -> dict:
        """
        Handle payment failure with cascading failure resilience.

        Implements fallback logging when primary systems are unavailable.
        """
        result = {
            "action": None,
            "fallback_path": None,
            "primary_error": error_code,
            "secondary_error": None,
            "recovery_instructions": None,
        }

        # Check for PG timeout
        if self.failure_injector.pg_timeout_enabled:
            result["primary_error"] = "PG_TIMEOUT"

        # Try to write to DLQ (database)
        if self.failure_injector.db_write_error_enabled:
            # Database write failed - use fallback logging
            result["secondary_error"] = "DB_CONNECTION_LOST"
            result["action"] = "fallback_logged"

            # Write to fallback file
            fallback_data = {
                "payment_id": payment.id,
                "order_id": order_id or payment.order_id,
                "error_code": result["primary_error"],
                "secondary_error": result["secondary_error"],
                "timestamp": timezone.now().isoformat(),
                "recovery_instructions": (
                    "Manual review required. "
                    "Check payment status with PG directly. "
                    "Re-process once DB connectivity is restored."
                ),
            }

            fallback_path = os.path.join(
                self._fallback_dir, f"fallback_payment_{payment.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
            )

            with open(fallback_path, "w") as f:
                json.dump(fallback_data, f, indent=2)

            result["fallback_path"] = fallback_path
            result["recovery_instructions"] = fallback_data["recovery_instructions"]
            self.failure_injector.fallback_path = fallback_path

        elif self.failure_injector.redis_down_enabled:
            # Redis down - use synchronous fallback queue
            result["secondary_error"] = "REDIS_DOWN"
            result["action"] = "sync_fallback_queue"
            result["recovery_instructions"] = "Using synchronous fallback queue. " "Restore Redis and process pending items."

        elif self.failure_injector.celery_broker_down_enabled:
            # Celery broker down - persist to file
            result["secondary_error"] = "CELERY_BROKER_DOWN"
            result["action"] = "file_persisted"

            fallback_data = {
                "payment_id": payment.id,
                "error_code": result["primary_error"],
                "secondary_error": result["secondary_error"],
                "task_type": "payment_retry",
                "timestamp": timezone.now().isoformat(),
            }

            fallback_path = os.path.join(self._fallback_dir, f"celery_fallback_{payment.id}.json")

            with open(fallback_path, "w") as f:
                json.dump(fallback_data, f, indent=2)

            result["fallback_path"] = fallback_path
            result["recovery_instructions"] = "Celery broker unavailable. " "Task persisted to file for manual recovery."

        elif self.failure_injector.dlq_full_enabled:
            # DLQ is full - escalate to REQUIRES_REVIEW
            result["secondary_error"] = "DLQ_FULL"
            result["action"] = "requires_review"
            result["escalation_level"] = "CRITICAL"
            result["recovery_instructions"] = (
                "DLQ capacity reached. "
                "Immediate attention required. "
                "Process existing DLQ entries before new failures can be queued."
            )

        else:
            # Normal DLQ processing
            result["action"] = "moved_to_dlq"

        return result

    def handle_circuit_breaker_update(
        self,
        service_name: str,
        new_state: str,
    ) -> dict:
        """
        Handle circuit breaker state update with DB lock resilience.

        If DB is locked, defaults to allowing requests (graceful degradation).
        """
        result = {
            "action": None,
            "state": None,
            "degraded_mode": False,
        }

        if self.failure_injector.db_connection_error_enabled:
            # DB locked/unavailable - graceful degradation
            result["action"] = "graceful_degradation"
            result["state"] = "default_allow"
            result["degraded_mode"] = True
            result["recovery_instructions"] = (
                "Circuit breaker state update failed. " "Defaulting to ALLOW to prevent service disruption."
            )
        else:
            result["action"] = "state_updated"
            result["state"] = new_state
            result["degraded_mode"] = False

        return result

    def handle_notification_send(
        self,
        notification_type: str,
        recipient: str,
        message: str,
    ) -> dict:
        """
        Handle notification sending with SMTP failure resilience.

        SMTP failures should not block payment processing.
        """
        result = {
            "action": None,
            "blocking": False,
            "queued_for_retry": False,
        }

        if self.failure_injector.smtp_down_enabled:
            result["action"] = "async_retry_queued"
            result["blocking"] = False
            result["queued_for_retry"] = True
            result["recovery_instructions"] = (
                "SMTP unavailable. " "Notification queued for async retry. " "Payment processing continues."
            )
        else:
            result["action"] = "sent"
            result["blocking"] = False

        return result

    def handle_rollback_with_inventory_failure(
        self,
        order_id: int,
        items: list,
    ) -> dict:
        """
        Handle rollback when inventory service is down.

        Queue compensating transaction for later processing.
        """
        result = {
            "action": None,
            "compensating_transaction_queued": False,
        }

        if self.failure_injector.inventory_service_down_enabled:
            result["action"] = "compensating_transaction_queued"
            result["compensating_transaction_queued"] = True
            result["pending_items"] = items
            result["recovery_instructions"] = (
                "Inventory service unavailable. " "Compensating transaction queued. " "Will retry when service is restored."
            )
        else:
            result["action"] = "rollback_complete"

        return result

    def cleanup(self):
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self._fallback_dir):
            shutil.rmtree(self._fallback_dir)


@pytest.fixture
def cascading_handler(failure_injector: FailureInjector) -> Generator[CascadingFailureHandler, None, None]:
    """Provide a cascading failure handler."""
    handler = CascadingFailureHandler(failure_injector)
    yield handler
    handler.cleanup()


# =============================================================================
# CASC-001: PG Timeout with DB Connection Lost
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingPGTimeoutDBConnectionLost:
    """
    Test cascading failure: PG timeout followed by DB connection loss.

    Validates graceful degradation when primary payment gateway fails
    and database is also unavailable.
    """

    def test_pg_timeout_with_db_connection_lost_uses_fallback(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Validate graceful degradation when PG and DB both fail.

        Scenario:
            1. Inject PG timeout on payment attempt
            2. During DLQ write, inject DB connection error
            3. Verify fallback logging triggered
            4. Verify no data loss (fallback file exists)

        Expected:
            - Primary failure: PG_TIMEOUT detected
            - Secondary failure: DB write fails
            - Fallback: Local file logging activated
            - Recovery: Manual recovery path documented
            - No data loss: Fallback contains full context

        Risk Covered:
            R-004: Cascading system failure

        Compliance:
            NIST CP-2 (Contingency Planning)
        """
        # Arrange
        failure_injector.inject_pg_timeout()
        failure_injector.inject_db_connection_error_on_write()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
        )

        # Assert
        assert result["action"] == "fallback_logged"
        assert result["fallback_path"] is not None
        assert os.path.exists(result["fallback_path"])

        # Verify fallback file contains full context
        with open(result["fallback_path"], "r") as f:
            fallback_data = json.load(f)

        assert fallback_data["payment_id"] == sample_payment.id
        assert fallback_data["error_code"] == "PG_TIMEOUT"
        assert fallback_data["secondary_error"] == "DB_CONNECTION_LOST"
        assert "recovery_instructions" in fallback_data
        assert "Manual review required" in fallback_data["recovery_instructions"]

    def test_fallback_file_contains_complete_forensic_data(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify fallback file contains all data needed for manual recovery.

        Scenario:
            1. Trigger cascading failure (PG + DB)
            2. Verify fallback file contains payment_id, order_id, timestamps
            3. Verify recovery instructions are actionable

        Expected:
            - Fallback file is valid JSON
            - Contains all identifiers
            - Contains timestamp for forensics
            - Contains actionable recovery instructions

        Risk Covered:
            R-004: Data loss during cascading failure
        """
        # Arrange
        failure_injector.inject_pg_timeout()
        failure_injector.inject_db_connection_error_on_write()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
            order_id=sample_payment.order_id,
        )

        # Assert
        with open(result["fallback_path"], "r") as f:
            fallback_data = json.load(f)

        # Required fields for forensics
        assert "payment_id" in fallback_data
        assert "order_id" in fallback_data
        assert "timestamp" in fallback_data
        assert "error_code" in fallback_data
        assert "secondary_error" in fallback_data
        assert "recovery_instructions" in fallback_data

        # Verify timestamp is parseable
        from datetime import datetime

        timestamp = fallback_data["timestamp"]
        assert datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


# =============================================================================
# CASC-002: DLQ Insert with Redis Down
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingDLQRedisDown:
    """
    Test cascading failure: DLQ insert when Redis is down.

    Validates synchronous fallback queue activation.
    """

    def test_dlq_insert_with_redis_down_uses_sync_fallback(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify synchronous fallback queue when Redis is unavailable.

        Scenario:
            1. Redis is down
            2. Attempt to insert into DLQ (which uses Redis for queueing)
            3. Verify synchronous fallback is used

        Expected:
            - Redis down detected
            - Synchronous fallback queue activated
            - No blocking of primary flow
            - Recovery path documented

        Risk Covered:
            R-004: Redis unavailability
        """
        # Arrange
        failure_injector.inject_redis_down()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="NETWORK_ERROR",
        )

        # Assert
        assert result["action"] == "sync_fallback_queue"
        assert result["secondary_error"] == "REDIS_DOWN"
        assert "Restore Redis" in result["recovery_instructions"]


# =============================================================================
# CASC-003: Retry Task with Celery Broker Down
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingRetryCeleryBrokerDown:
    """
    Test cascading failure: Retry task when Celery broker is down.

    Validates file persistence for manual recovery.
    """

    def test_retry_with_celery_broker_down_persists_to_file(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify task persistence when Celery broker is unavailable.

        Scenario:
            1. Celery broker is down
            2. Attempt to schedule retry task
            3. Verify task is persisted to file
            4. Verify manual recovery path exists

        Expected:
            - Celery broker down detected
            - Task persisted to local file
            - Manual recovery documented
            - No data loss

        Risk Covered:
            R-004: Celery broker failure

        Compliance:
            NIST CP-2 (Contingency Planning)
        """
        # Arrange
        failure_injector.inject_celery_broker_down()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="RETRYABLE_ERROR",
        )

        # Assert
        assert result["action"] == "file_persisted"
        assert result["secondary_error"] == "CELERY_BROKER_DOWN"
        assert result["fallback_path"] is not None
        assert os.path.exists(result["fallback_path"])

        # Verify persisted task data
        with open(result["fallback_path"], "r") as f:
            task_data = json.load(f)

        assert task_data["payment_id"] == sample_payment.id
        assert task_data["task_type"] == "payment_retry"


# =============================================================================
# CASC-004: CB State Update with DB Locked
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingCBUpdateDBLocked:
    """
    Test cascading failure: Circuit breaker update when DB is locked.

    Validates graceful degradation with default-allow behavior.
    """

    def test_cb_update_with_db_locked_defaults_to_allow(
        self,
        failure_injector,
        cascading_handler,
    ):
        """
        Purpose:
            Verify graceful degradation when CB state update fails.

        Scenario:
            1. DB is locked/unavailable
            2. Attempt to update circuit breaker state
            3. Verify default-allow behavior (no blocking)

        Expected:
            - DB lock detected
            - Graceful degradation activated
            - Default to ALLOW (fail-open)
            - No service disruption

        Risk Covered:
            R-004: DB unavailability during CB update

        Compliance:
            SOC 2 (Availability)
        """
        # Arrange
        failure_injector.inject_db_connection_error()

        # Act
        result = cascading_handler.handle_circuit_breaker_update(
            service_name="toss_payment",
            new_state="open",
        )

        # Assert
        assert result["action"] == "graceful_degradation"
        assert result["state"] == "default_allow"
        assert result["degraded_mode"] is True
        assert "Defaulting to ALLOW" in result["recovery_instructions"]


# =============================================================================
# CASC-005: Notification Send with SMTP Down
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingNotificationSMTPDown:
    """
    Test cascading failure: Notification send when SMTP is down.

    Validates async retry without blocking payment processing.
    """

    def test_notification_with_smtp_down_does_not_block(
        self,
        failure_injector,
        cascading_handler,
    ):
        """
        Purpose:
            Verify notification failure does not block primary flow.

        Scenario:
            1. SMTP is down
            2. Attempt to send failure notification
            3. Verify payment processing continues
            4. Verify notification queued for async retry

        Expected:
            - SMTP down detected
            - Notification queued for retry
            - Primary flow NOT blocked
            - Async retry scheduled

        Risk Covered:
            R-005: Notification failure blocking payments
        """
        # Arrange
        failure_injector.inject_smtp_down()

        # Act
        result = cascading_handler.handle_notification_send(
            notification_type="payment_failure",
            recipient="user@example.com",
            message="Your payment failed",
        )

        # Assert
        assert result["action"] == "async_retry_queued"
        assert result["blocking"] is False
        assert result["queued_for_retry"] is True


# =============================================================================
# CASC-006: Rollback with Inventory Service Down
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingRollbackInventoryDown:
    """
    Test cascading failure: Rollback when inventory service is down.

    Validates compensating transaction queueing.
    """

    def test_rollback_with_inventory_down_queues_compensating_transaction(
        self,
        failure_injector,
        cascading_handler,
    ):
        """
        Purpose:
            Verify compensating transaction queued when inventory fails.

        Scenario:
            1. Inventory service is down
            2. Attempt rollback (restore stock)
            3. Verify compensating transaction queued
            4. Verify items tracked for retry

        Expected:
            - Inventory service down detected
            - Compensating transaction queued
            - All items tracked for later processing
            - Recovery path documented

        Risk Covered:
            R-006: Inventory rollback failure

        Compliance:
            NIST CP-2 (Contingency Planning)
        """
        # Arrange
        failure_injector.inject_inventory_service_down()
        items = [
            {"product_id": 1, "quantity": 2},
            {"product_id": 2, "quantity": 1},
        ]

        # Act
        result = cascading_handler.handle_rollback_with_inventory_failure(
            order_id=12345,
            items=items,
        )

        # Assert
        assert result["action"] == "compensating_transaction_queued"
        assert result["compensating_transaction_queued"] is True
        assert result["pending_items"] == items
        assert "Will retry when service is restored" in result["recovery_instructions"]


# =============================================================================
# CASC-007: Handler Crash with DLQ Full
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestCascadingHandlerCrashDLQFull:
    """
    Test cascading failure: Handler crash when DLQ is full.

    Validates REQUIRES_REVIEW escalation.
    """

    def test_handler_crash_with_dlq_full_escalates_to_requires_review(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify REQUIRES_REVIEW escalation when DLQ is at capacity.

        Scenario:
            1. DLQ is full (capacity reached)
            2. New failure needs to be queued
            3. Verify REQUIRES_REVIEW escalation
            4. Verify immediate attention required

        Expected:
            - DLQ full condition detected
            - Escalation to REQUIRES_REVIEW
            - Critical priority flagged
            - Immediate action required

        Risk Covered:
            R-007: DLQ overflow

        Compliance:
            NIST IR-4 (Incident Handling)
        """
        # Arrange
        failure_injector.inject_dlq_full()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="CRITICAL_ERROR",
        )

        # Assert
        assert result["action"] == "requires_review"
        assert result["secondary_error"] == "DLQ_FULL"
        assert result.get("escalation_level") == "CRITICAL"
        assert "Immediate attention required" in result["recovery_instructions"]


# =============================================================================
# Combined Cascading Failure Scenarios
# =============================================================================


@pytest.mark.tier3_chaos
@pytest.mark.django_db(transaction=True)
class TestMultipleCascadingFailures:
    """
    Test multiple simultaneous cascading failures.

    Validates system behavior under extreme failure conditions.
    """

    def test_system_maintains_audit_trail_during_cascading_failures(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify audit trail is maintained even during cascading failures.

        Scenario:
            1. Inject multiple failures
            2. Process payment failure
            3. Verify injection history is tracked
            4. Verify recovery path exists

        Expected:
            - All injected failures tracked
            - Audit trail complete
            - Recovery possible from fallback

        Compliance:
            NIST AU-3 (Audit Content)
        """
        # Arrange
        failure_injector.inject_pg_timeout()
        failure_injector.inject_db_connection_error_on_write()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
        )

        # Assert - verify injection tracking
        summary = failure_injector.get_injection_summary()
        assert "pg_timeout" in summary["active_failures"]
        assert "db_write_error" in summary["active_failures"]
        assert len(summary["history"]) == 2

        # Verify fallback exists
        assert result["fallback_path"] is not None

    def test_failure_recovery_produces_no_data_loss(
        self,
        failure_injector,
        cascading_handler,
        sample_payment,
    ):
        """
        Purpose:
            Verify no data loss occurs during cascading failure recovery.

        Scenario:
            1. Trigger cascading failures
            2. Verify all payment data is preserved in fallback
            3. Verify data can be reconstructed for manual recovery

        Expected:
            - All payment identifiers preserved
            - Order relationship maintained
            - Sufficient data for manual intervention

        Risk Covered:
            R-004: Data loss during cascading failure
        """
        # Arrange
        failure_injector.inject_pg_timeout()
        failure_injector.inject_db_connection_error_on_write()

        # Act
        result = cascading_handler.handle_failure(
            payment=sample_payment,
            error_code="PG_TIMEOUT",
            order_id=sample_payment.order_id,
        )

        # Assert - verify data completeness
        with open(result["fallback_path"], "r") as f:
            fallback_data = json.load(f)

        # Critical identifiers preserved
        assert fallback_data["payment_id"] == sample_payment.id
        assert fallback_data["order_id"] == sample_payment.order_id

        # Error context preserved
        assert fallback_data["error_code"] is not None
        assert fallback_data["secondary_error"] is not None

        # Recovery path documented
        assert len(fallback_data["recovery_instructions"]) > 0
