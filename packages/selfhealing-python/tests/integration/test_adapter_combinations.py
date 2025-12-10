"""
Integration tests for adapter combinations.

Tests how different adapters work together in realistic scenarios.
"""

import pytest
import time
import threading
from decimal import Decimal
from datetime import timedelta
from typing import List, Dict, Any

from selfhealing.factory import ProviderRegistry
from selfhealing.interfaces.payment_provider import PaymentConfirmResult
from selfhealing.interfaces.task_queue import TaskStatus
from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter
from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter
from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter


class TestPaymentWithCacheIntegration:
    """Tests for payment operations with cache integration."""

    @pytest.fixture
    def payment(self):
        """Create mock payment adapter."""
        return MockPaymentAdapter()

    @pytest.fixture
    def cache(self):
        """Create in-memory cache adapter."""
        return InMemoryCacheAdapter(key_prefix="test:")

    def test_idempotent_payment_with_cache(self, payment: MockPaymentAdapter, cache: InMemoryCacheAdapter):
        """Test idempotent payment using cache for deduplication."""
        payment.set_confirm_response(success=True)

        def confirm_with_idempotency(payment_key: str, order_id: str, amount: Decimal, idem_key: str):
            # Check if already processed
            cache_key = f"payment:idem:{idem_key}"
            cached_result = cache.get(cache_key)
            if cached_result:
                return cached_result

            # Process payment
            result = payment.confirm_payment(
                payment_key=payment_key,
                order_id=order_id,
                amount=amount,
                idempotency_key=idem_key,
            )

            # Cache result if successful
            if result.success:
                cache.set(cache_key, {"success": True, "tx_id": result.transaction_id}, ttl=timedelta(hours=1))

            return result

        # First call - processes payment
        result1 = confirm_with_idempotency("pk_1", "order_1", Decimal("10000"), "idem_123")
        assert result1.success is True
        assert payment.confirm_call_count == 1

        # Second call with same idempotency key - returns cached
        result2 = confirm_with_idempotency("pk_1", "order_1", Decimal("10000"), "idem_123")
        assert result2["success"] is True
        assert payment.confirm_call_count == 1  # No new call

    def test_payment_rate_limiting_with_cache(self, payment: MockPaymentAdapter, cache: InMemoryCacheAdapter):
        """Test payment rate limiting using cache counter."""
        payment.set_confirm_response(success=True)

        def check_rate_limit(user_id: str, limit: int = 5, window: int = 60) -> bool:
            key = f"rate_limit:payment:{user_id}"
            current = cache.incr(key)
            if current == 1:
                cache.expire(key, timedelta(seconds=window))
            return current <= limit

        user_id = "user_123"

        # Make calls within limit
        for i in range(5):
            assert check_rate_limit(user_id) is True

        # Next call exceeds limit
        assert check_rate_limit(user_id) is False

    def test_payment_lock_prevents_double_processing(self, payment: MockPaymentAdapter, cache: InMemoryCacheAdapter):
        """Test distributed lock prevents double payment processing."""
        payment.set_confirm_response(success=True)
        results = []
        errors = []

        def process_payment(order_id: str):
            lock = cache.get_lock(f"payment:lock:{order_id}", timeout=timedelta(seconds=5))
            if lock.acquire(blocking=True, timeout=2.0):
                try:
                    # Simulate processing time
                    time.sleep(0.1)
                    result = payment.confirm_payment(
                        payment_key=f"pk_{order_id}",
                        order_id=order_id,
                        amount=Decimal("10000"),
                    )
                    results.append(result)
                finally:
                    lock.release()
            else:
                errors.append(f"Could not acquire lock for {order_id}")

        # Simulate concurrent payment attempts for same order
        threads = [threading.Thread(target=process_payment, args=("order_123",)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one payment should succeed due to locking
        assert payment.confirm_call_count == 3  # All acquired lock sequentially


class TestPaymentWithQueueIntegration:
    """Tests for payment operations with task queue integration."""

    @pytest.fixture
    def payment(self):
        """Create mock payment adapter."""
        return MockPaymentAdapter()

    @pytest.fixture
    def queue(self):
        """Create sync task adapter."""
        return SyncTaskAdapter()

    def test_async_payment_confirmation(self, payment: MockPaymentAdapter, queue: SyncTaskAdapter):
        """Test async payment confirmation via task queue."""
        payment.set_confirm_response(success=True)
        processed_payments: List[str] = []

        @queue.task(name="confirm_payment_task")
        def confirm_payment_task(payment_key: str, order_id: str, amount: str):
            result = payment.confirm_payment(
                payment_key=payment_key,
                order_id=order_id,
                amount=Decimal(amount),
            )
            if result.success:
                processed_payments.append(order_id)
            return {"success": result.success, "order_id": order_id}

        # Enqueue payment task
        task_id = queue.enqueue(
            "confirm_payment_task",
            args=("pk_test", "order_123", "50000"),
        )

        # In sync mode, task executes immediately
        result = queue.get_result(task_id)
        assert result.status == TaskStatus.SUCCESS
        assert "order_123" in processed_payments

    def test_payment_retry_via_queue(self, payment: MockPaymentAdapter, queue: SyncTaskAdapter):
        """Test payment retry mechanism via task queue."""
        attempt_count = 0
        success_on_attempt = 3

        @queue.task(name="retry_payment_task", max_retries=5)
        def retry_payment_task(payment_key: str, order_id: str, amount: str):
            nonlocal attempt_count
            attempt_count += 1

            if attempt_count < success_on_attempt:
                payment.set_confirm_response(
                    success=False,
                    error_code="TEMPORARY_ERROR",
                    error_message="일시적 오류",
                )
            else:
                payment.set_confirm_response(success=True)

            result = payment.confirm_payment(
                payment_key=payment_key,
                order_id=order_id,
                amount=Decimal(amount),
            )

            if not result.success:
                raise RuntimeError(f"Payment failed: {result.error_message}")

            return {"success": True, "attempt": attempt_count}

        # First attempt fails
        task_id = queue.enqueue("retry_payment_task", args=("pk_test", "order_123", "10000"))
        result = queue.get_result(task_id)
        assert result.status == TaskStatus.FAILURE

        # Retry until success
        while attempt_count < success_on_attempt:
            task_id = queue.enqueue("retry_payment_task", args=("pk_test", "order_123", "10000"))
            result = queue.get_result(task_id)

        assert result.status == TaskStatus.SUCCESS
        assert result.result["attempt"] == success_on_attempt

    def test_batch_payment_processing(self, payment: MockPaymentAdapter, queue: SyncTaskAdapter):
        """Test batch payment processing via task queue."""
        payment.set_confirm_response(success=True)
        processed: List[str] = []

        @queue.task(name="batch_payment_task")
        def batch_payment_task(payments: List[Dict[str, Any]]):
            results = []
            for p in payments:
                result = payment.confirm_payment(
                    payment_key=p["payment_key"],
                    order_id=p["order_id"],
                    amount=Decimal(p["amount"]),
                )
                results.append({"order_id": p["order_id"], "success": result.success})
                if result.success:
                    processed.append(p["order_id"])
            return results

        batch = [
            {"payment_key": "pk_1", "order_id": "order_1", "amount": "10000"},
            {"payment_key": "pk_2", "order_id": "order_2", "amount": "20000"},
            {"payment_key": "pk_3", "order_id": "order_3", "amount": "30000"},
        ]

        task_id = queue.enqueue("batch_payment_task", args=(batch,))
        result = queue.get_result(task_id)

        assert result.status == TaskStatus.SUCCESS
        assert len(processed) == 3
        assert payment.confirm_call_count == 3


class TestCacheWithQueueIntegration:
    """Tests for cache and task queue integration."""

    @pytest.fixture
    def cache(self):
        """Create in-memory cache adapter."""
        return InMemoryCacheAdapter(key_prefix="test:")

    @pytest.fixture
    def queue(self):
        """Create sync task adapter."""
        return SyncTaskAdapter()

    def test_task_result_caching(self, cache: InMemoryCacheAdapter, queue: SyncTaskAdapter):
        """Test caching task results."""
        computation_count = 0

        @queue.task(name="expensive_computation")
        def expensive_computation(input_data: str):
            nonlocal computation_count
            computation_count += 1
            # Simulate expensive operation
            return {"result": f"processed_{input_data}", "count": computation_count}

        def cached_computation(input_data: str):
            cache_key = f"computation:{input_data}"
            cached = cache.get(cache_key)
            if cached:
                return cached

            task_id = queue.enqueue("expensive_computation", args=(input_data,))
            result = queue.get_result(task_id)

            if result.status == TaskStatus.SUCCESS:
                cache.set(cache_key, result.result, ttl=timedelta(minutes=5))

            return result.result

        # First call - computes
        result1 = cached_computation("data_1")
        assert result1["result"] == "processed_data_1"
        assert computation_count == 1

        # Second call - returns cached
        result2 = cached_computation("data_1")
        assert result2["result"] == "processed_data_1"
        assert computation_count == 1  # No new computation

        # Different input - computes again
        result3 = cached_computation("data_2")
        assert result3["result"] == "processed_data_2"
        assert computation_count == 2

    def test_distributed_task_coordination(self, cache: InMemoryCacheAdapter, queue: SyncTaskAdapter):
        """Test task coordination using cache."""
        task_states: Dict[str, str] = {}

        @queue.task(name="coordinated_task")
        def coordinated_task(task_name: str, dependency: str = None):
            # Check dependency completion
            if dependency:
                dep_status = cache.get(f"task_status:{dependency}")
                if dep_status != "completed":
                    raise RuntimeError(f"Dependency {dependency} not completed")

            # Execute task
            task_states[task_name] = "running"
            time.sleep(0.01)  # Simulate work
            task_states[task_name] = "completed"

            # Mark as completed in cache
            cache.set(f"task_status:{task_name}", "completed", ttl=timedelta(minutes=5))
            return {"task": task_name, "status": "completed"}

        # Task A has no dependencies
        task_a_id = queue.enqueue("coordinated_task", args=("task_a",))
        result_a = queue.get_result(task_a_id)
        assert result_a.status == TaskStatus.SUCCESS

        # Task B depends on A
        task_b_id = queue.enqueue("coordinated_task", args=("task_b",), kwargs={"dependency": "task_a"})
        result_b = queue.get_result(task_b_id)
        assert result_b.status == TaskStatus.SUCCESS

    def test_task_deduplication_with_cache(self, cache: InMemoryCacheAdapter, queue: SyncTaskAdapter):
        """Test task deduplication using cache."""
        execution_count = 0

        @queue.task(name="unique_task")
        def unique_task(task_key: str):
            nonlocal execution_count

            # Check if already processing/processed
            lock_key = f"task_lock:{task_key}"
            with cache.get_lock(lock_key):
                status = cache.get(f"task_status:{task_key}")
                if status in ["processing", "completed"]:
                    return {"status": status, "deduplicated": True}

                cache.set(f"task_status:{task_key}", "processing", ttl=timedelta(minutes=5))

                try:
                    execution_count += 1
                    time.sleep(0.01)  # Simulate work
                    cache.set(f"task_status:{task_key}", "completed", ttl=timedelta(minutes=5))
                    return {"status": "completed", "execution": execution_count}
                except Exception:
                    cache.set(f"task_status:{task_key}", "failed", ttl=timedelta(minutes=5))
                    raise

        # Submit same task multiple times
        queue.enqueue("unique_task", args=("task_123",))
        queue.enqueue("unique_task", args=("task_123",))
        queue.enqueue("unique_task", args=("task_123",))

        # Only first execution should run (in sync mode, they run sequentially)
        assert execution_count == 1


class TestFullIntegrationScenario:
    """Full integration scenario tests."""

    @pytest.fixture
    def payment(self):
        """Create mock payment adapter."""
        return MockPaymentAdapter()

    @pytest.fixture
    def cache(self):
        """Create in-memory cache adapter."""
        return InMemoryCacheAdapter(key_prefix="integration:")

    @pytest.fixture
    def queue(self):
        """Create sync task adapter."""
        return SyncTaskAdapter()

    def test_complete_payment_processing_pipeline(
        self,
        payment: MockPaymentAdapter,
        cache: InMemoryCacheAdapter,
        queue: SyncTaskAdapter,
    ):
        """Test complete payment processing pipeline with all adapters."""
        payment.set_confirm_response(success=True)
        order_statuses: Dict[str, str] = {}

        @queue.task(name="process_order_payment")
        def process_order_payment(order_id: str, payment_key: str, amount: str):
            # Check rate limit
            rate_key = f"rate_limit:{order_id[:8]}"
            if cache.incr(rate_key) > 100:
                raise RuntimeError("Rate limit exceeded")
            cache.expire(rate_key, timedelta(minutes=1))

            # Acquire lock for order
            lock = cache.get_lock(f"order_lock:{order_id}")
            if not lock.acquire(blocking=True, timeout=5.0):
                raise RuntimeError("Could not acquire order lock")

            try:
                # Check if already processed
                if cache.get(f"order_processed:{order_id}"):
                    return {"status": "already_processed", "order_id": order_id}

                # Process payment
                result = payment.confirm_payment(
                    payment_key=payment_key,
                    order_id=order_id,
                    amount=Decimal(amount),
                )

                if result.success:
                    cache.set(f"order_processed:{order_id}", True, ttl=timedelta(hours=24))
                    order_statuses[order_id] = "completed"
                    return {
                        "status": "success",
                        "order_id": order_id,
                        "transaction_id": result.transaction_id,
                    }
                else:
                    order_statuses[order_id] = "failed"
                    return {
                        "status": "failed",
                        "order_id": order_id,
                        "error": result.error_message,
                    }
            finally:
                lock.release()

        # Process multiple orders
        orders = [
            ("order_001", "pk_001", "10000"),
            ("order_002", "pk_002", "20000"),
            ("order_003", "pk_003", "30000"),
        ]

        results = []
        for order_id, pk, amount in orders:
            task_id = queue.enqueue("process_order_payment", args=(order_id, pk, amount))
            results.append(queue.get_result(task_id))

        # Verify all orders processed successfully
        assert all(r.status == TaskStatus.SUCCESS for r in results)
        assert all(order_statuses.get(o[0]) == "completed" for o in orders)
        assert payment.confirm_call_count == 3

        # Try to reprocess - should be deduplicated
        task_id = queue.enqueue("process_order_payment", args=("order_001", "pk_001", "10000"))
        result = queue.get_result(task_id)
        assert result.result["status"] == "already_processed"
        assert payment.confirm_call_count == 3  # No new payment call

    def test_circuit_breaker_pattern_with_adapters(
        self,
        payment: MockPaymentAdapter,
        cache: InMemoryCacheAdapter,
        queue: SyncTaskAdapter,
    ):
        """Test circuit breaker pattern using adapters."""
        failure_threshold = 3
        recovery_timeout = 1  # seconds

        def get_circuit_state(service: str) -> str:
            return cache.get(f"circuit:{service}:state") or "closed"

        def record_failure(service: str):
            failures = cache.incr(f"circuit:{service}:failures")
            cache.expire(f"circuit:{service}:failures", timedelta(minutes=1))

            if failures >= failure_threshold:
                cache.set(f"circuit:{service}:state", "open", ttl=timedelta(seconds=recovery_timeout))

        def record_success(service: str):
            cache.set(f"circuit:{service}:failures", 0)
            if get_circuit_state(service) == "half-open":
                cache.set(f"circuit:{service}:state", "closed")

        @queue.task(name="protected_payment")
        def protected_payment(payment_key: str, order_id: str, amount: str):
            service = "payment"
            state = get_circuit_state(service)

            if state == "open":
                raise RuntimeError("Circuit breaker is open")

            result = payment.confirm_payment(
                payment_key=payment_key,
                order_id=order_id,
                amount=Decimal(amount),
            )

            if result.success:
                record_success(service)
                return {"status": "success"}
            else:
                record_failure(service)
                raise RuntimeError(f"Payment failed: {result.error_message}")

        # Configure failures
        payment.set_confirm_response(success=False, error_code="ERROR", error_message="Service unavailable")

        # Trigger failures to open circuit
        for i in range(failure_threshold):
            task_id = queue.enqueue("protected_payment", args=(f"pk_{i}", f"order_{i}", "1000"))
            result = queue.get_result(task_id)
            assert result.status == TaskStatus.FAILURE

        # Circuit should be open now
        assert get_circuit_state("payment") == "open"

        # Next request should fail immediately (circuit open)
        task_id = queue.enqueue("protected_payment", args=("pk_x", "order_x", "1000"))
        result = queue.get_result(task_id)
        assert "Circuit breaker is open" in result.error

        # Wait for recovery timeout
        time.sleep(recovery_timeout + 0.1)

        # Circuit should be closed (TTL expired)
        assert get_circuit_state("payment") != "open"

        # Configure success
        payment.set_confirm_response(success=True)

        # Next request should succeed
        task_id = queue.enqueue("protected_payment", args=("pk_success", "order_success", "1000"))
        result = queue.get_result(task_id)
        assert result.status == TaskStatus.SUCCESS
