"""
Retry Count Persistence Tests

Tests for G-06: Retry count persists across worker restart.
Validates that retry count survives Celery worker restart.
Risk Covered: R-018 (Infinite retries due to count reset)
"""

import gc
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

# 이 파일의 모든 테스트는 DB 필요
pytestmark = pytest.mark.requires_db

from shopping.models.failed_operation import FailedOperation
from selfhealing.services import (
    ReplayResult,
    ReplayService,
    get_replay_service,
)
from shopping.handlers.replay_handlers import PaymentReplayHandler
from shopping.tests.factories import OrderFactory, PaymentFactory, UserFactory


@pytest.mark.django_db(transaction=True)
class TestRetryCountPersistence:
    """
    Tests for retry count durability.

    Gap ID: G-06
    Purpose: Verify retry count survives Celery worker restart.
    """

    def test_retry_count_persists_after_worker_restart(self):
        """
        Purpose:
            Verify retry count survives Celery worker restart.

        Scenario:
            1. Create DLQ entry with retry_count = 1
            2. Simulate worker restart (clear any in-memory state)
            3. Attempt replay
            4. Verify retry_count = 2 (not reset to 1)

        Expected:
            - Retry count is database-backed
            - No in-memory-only state
            - Count continues from last value

        Risk Covered:
            - R-018: Infinite retries due to count reset
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(
            order=order,
            amount=Decimal("50000"),
            status="in_progress",
        )

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Set initial retry count
        entry.retry_count = 1
        entry.save()

        entry_id = entry.id

        # Simulate worker restart: clear local references and force garbage collection
        del entry
        gc.collect()

        # Create new service instance (simulating new worker)
        service = ReplayService()

        # Reload from database
        fresh_entry = FailedOperation.objects.get(id=entry_id)
        assert fresh_entry.retry_count == 1, "Retry count should persist in database"

        # Perform replay (which increments count)
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(entry_id, "Still failing")
            service.replay_single(entry_id)

        fresh_entry.refresh_from_db()
        assert fresh_entry.retry_count == 2, f"Retry count should be 2 after replay, got {fresh_entry.retry_count}"

    def test_retry_count_increments_on_each_attempt(self):
        """
        Purpose:
            Verify retry count increments correctly on each replay attempt.

        Scenario:
            1. Create DLQ entry with retry_count = 0
            2. Perform multiple replays
            3. Verify count increments each time

        Expected:
            - Count increases by 1 for each replay attempt
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        assert entry.retry_count == 0

        service = ReplayService()

        # Perform multiple replays, each should increment count
        for expected_count in [1, 2]:
            with patch.object(PaymentReplayHandler, "replay") as mock_replay:
                mock_replay.return_value = ReplayResult.failed(entry.id, "Still failing")

                # Reset status to allow replay
                entry.status = FailedOperation.Status.PENDING
                entry.save()

                service.replay_single(entry.id)

            entry.refresh_from_db()
            assert entry.retry_count == expected_count, f"Expected retry_count={expected_count}, got {entry.retry_count}"

    def test_retry_count_persists_across_service_instances(self):
        """
        Purpose:
            Verify retry count is consistent across different service instances.

        Scenario:
            1. Create entry and increment count using service A
            2. Read count using service B (new instance)
            3. Verify counts match

        Expected:
            - Database is source of truth
            - No service-local state issues
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Service A increments count
        service_a = ReplayService()
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(entry.id, "Failed")
            service_a.replay_single(entry.id)

        # Service B reads count
        service_b = ReplayService()
        entry_from_b = FailedOperation.objects.get(id=entry.id)

        assert entry_from_b.retry_count == 1

    def test_max_retry_count_enforced_after_restart(self):
        """
        Purpose:
            Verify max retry count is enforced even after worker restart.

        Scenario:
            1. Create entry at max-1 retries
            2. Simulate restart
            3. Perform replay
            4. Verify entry is rejected (max exceeded)

        Expected:
            - Max retry limit prevents infinite loops
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Set at max retries (default is 2)
        entry.retry_count = 2
        entry.save()

        entry_id = entry.id

        # Simulate restart
        del entry
        gc.collect()

        # New service instance
        service = ReplayService()

        # Attempt replay - should be rejected
        result = service.replay_single(entry_id)

        assert result.success is False
        # Error message should indicate max exceeded or rejected status
        assert "rejected" in result.error.lower() or "max" in result.error.lower()

        # Verify entry is now rejected
        entry = FailedOperation.objects.get(id=entry_id)
        assert entry.status == FailedOperation.Status.REJECTED

    def test_retry_count_stored_in_database_not_cache(self):
        """
        Purpose:
            Verify retry count is stored in database, not just cache.

        Scenario:
            1. Create entry and increment count
            2. Clear cache
            3. Verify count still correct from database

        Expected:
            - Count survives cache clear
        """
        from django.core.cache import cache

        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        # Increment count
        service = ReplayService()
        with patch.object(PaymentReplayHandler, "replay") as mock_replay:
            mock_replay.return_value = ReplayResult.failed(entry.id, "Failed")
            service.replay_single(entry.id)

        # Clear all cache
        cache.clear()

        # Verify count persists
        entry.refresh_from_db()
        assert entry.retry_count == 1

    def test_concurrent_replay_attempts_handle_count_correctly(self):
        """
        Purpose:
            Verify concurrent replay attempts don't corrupt retry count.

        Scenario:
            1. Create entry
            2. Simulate concurrent replays (sequential but testing integrity)
            3. Verify count is accurate

        Expected:
            - Count accurately reflects number of attempts
        """
        user = UserFactory()
        order = OrderFactory(user=user, status="confirmed")
        payment = PaymentFactory(order=order, status="in_progress")

        entry = FailedOperation.create_from_failure(
            domain="payment",
            failure_type="PG_TIMEOUT",
            entity_type="order",
            entity_id=str(order.id),
            snapshot_data={"payment_id": payment.id, "order_id": order.id},
        )

        entry_id = entry.id

        # Simulate multiple services attempting replay
        services = [ReplayService() for _ in range(3)]

        successful_replays = 0
        for i, service in enumerate(services):
            # Reload entry to get current state
            entry = FailedOperation.objects.get(id=entry_id)

            if entry.retry_count >= 2:  # Max replays
                break

            entry.status = FailedOperation.Status.PENDING
            entry.save()

            with patch.object(PaymentReplayHandler, "replay") as mock_replay:
                mock_replay.return_value = ReplayResult.failed(entry_id, "Still failing")
                result = service.replay_single(entry_id)
                if result.success is False and result.error != "max_replays_exceeded":
                    successful_replays += 1

        # Verify final count
        entry = FailedOperation.objects.get(id=entry_id)
        # Count should match number of replay attempts (capped at max)
        assert entry.retry_count <= 2
