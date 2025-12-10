"""
Unit tests for ProviderRegistry and Factory.

Tests the centralized registry and factory pattern implementation.
"""

import pytest
from typing import Optional
from decimal import Decimal
from datetime import timedelta

from selfhealing.factory import ProviderRegistry
from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)
from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)
from selfhealing.interfaces.task_queue import (
    TaskQueueInterface,
    TaskStatus,
    TaskResult,
)


class DummyPaymentProvider(PaymentProviderInterface):
    """Dummy payment provider for testing registration."""

    @property
    def provider_name(self) -> str:
        return "dummy"

    def confirm_payment(
        self,
        payment_key: str,
        order_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> PaymentConfirmResult:
        return PaymentConfirmResult(success=True, payment_key=payment_key)

    def cancel_payment(
        self,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[Decimal] = None,
        idempotency_key: Optional[str] = None,
    ) -> PaymentCancelResult:
        return PaymentCancelResult(success=True)

    def verify_webhook(
        self,
        payload: bytes,
        signature: str,
        timestamp: Optional[str] = None,
    ) -> WebhookVerifyResult:
        return WebhookVerifyResult(valid=True)

    def get_payment_status(self, payment_key: str) -> PaymentStatusResult:
        return PaymentStatusResult(success=True, status="DONE")

    def health_check(self) -> bool:
        return True


class TestProviderRegistry:
    """Tests for ProviderRegistry class."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry state before each test."""
        # Store original state
        original_instances = ProviderRegistry._instances.copy()
        original_defaults = {
            "payment": ProviderRegistry._default_payment,
            "cache": ProviderRegistry._default_cache,
            "queue": ProviderRegistry._default_queue,
        }

        yield

        # Restore original state
        ProviderRegistry._instances = original_instances
        ProviderRegistry._default_payment = original_defaults["payment"]
        ProviderRegistry._default_cache = original_defaults["cache"]
        ProviderRegistry._default_queue = original_defaults["queue"]

    # =========================================================================
    # Registration Tests
    # =========================================================================

    def test_register_payment_provider(self):
        """Test registering a payment provider."""
        ProviderRegistry.register_payment("dummy", DummyPaymentProvider)
        assert "dummy" in ProviderRegistry._payment_providers

    def test_register_cache_provider(self):
        """Test registering a cache provider."""
        from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

        ProviderRegistry.register_cache("test_memory", InMemoryCacheAdapter)
        assert "test_memory" in ProviderRegistry._cache_providers

    def test_register_task_queue(self):
        """Test registering a task queue."""
        from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

        ProviderRegistry.register_queue("test_sync", SyncTaskAdapter)
        assert "test_sync" in ProviderRegistry._task_queues

    # =========================================================================
    # Provider Getter Tests
    # =========================================================================

    def test_get_payment_default(self):
        """Test getting default payment provider."""
        payment = ProviderRegistry.get_payment()
        assert isinstance(payment, PaymentProviderInterface)

    def test_get_payment_by_name(self):
        """Test getting payment provider by name."""
        ProviderRegistry.register_payment("dummy", DummyPaymentProvider)
        payment = ProviderRegistry.get_payment("dummy")
        assert payment.provider_name == "dummy"

    def test_get_payment_unknown_raises(self):
        """Test getting unknown payment provider raises ValueError."""
        with pytest.raises(ValueError, match="Unknown payment provider"):
            ProviderRegistry.get_payment("nonexistent")

    def test_get_cache_default(self):
        """Test getting default cache provider."""
        cache = ProviderRegistry.get_cache()
        assert isinstance(cache, CacheProviderInterface)

    def test_get_cache_by_name(self):
        """Test getting cache provider by name."""
        cache = ProviderRegistry.get_cache("memory")
        assert cache.provider_name == "memory"

    def test_get_cache_unknown_raises(self):
        """Test getting unknown cache provider raises ValueError."""
        with pytest.raises(ValueError, match="Unknown cache provider"):
            ProviderRegistry.get_cache("nonexistent")

    def test_get_queue_default(self):
        """Test getting default task queue."""
        queue = ProviderRegistry.get_queue()
        assert isinstance(queue, TaskQueueInterface)

    def test_get_queue_by_name(self):
        """Test getting task queue by name."""
        queue = ProviderRegistry.get_queue("sync")
        assert queue.provider_name == "sync"

    def test_get_queue_unknown_raises(self):
        """Test getting unknown task queue raises ValueError."""
        with pytest.raises(ValueError, match="Unknown task queue"):
            ProviderRegistry.get_queue("nonexistent")

    # =========================================================================
    # Singleton Tests
    # =========================================================================

    def test_singleton_returns_same_instance(self):
        """Test singleton mode returns same instance."""
        cache1 = ProviderRegistry.get_cache("memory", singleton=True)
        cache2 = ProviderRegistry.get_cache("memory", singleton=True)
        assert cache1 is cache2

    def test_non_singleton_returns_different_instances(self):
        """Test non-singleton mode returns different instances."""
        cache1 = ProviderRegistry.get_cache("memory", singleton=False)
        cache2 = ProviderRegistry.get_cache("memory", singleton=False)
        assert cache1 is not cache2

    def test_clear_instances_resets_singletons(self):
        """Test clear_instances resets singleton cache."""
        cache1 = ProviderRegistry.get_cache("memory", singleton=True)
        ProviderRegistry.clear_instances()
        cache2 = ProviderRegistry.get_cache("memory", singleton=True)
        assert cache1 is not cache2

    # =========================================================================
    # Default Setting Tests
    # =========================================================================

    def test_set_defaults(self):
        """Test setting default providers."""
        ProviderRegistry.register_payment("dummy", DummyPaymentProvider)
        ProviderRegistry.set_defaults(
            payment="dummy",
            cache="memory",
            queue="sync",
        )

        assert ProviderRegistry._default_payment == "dummy"
        assert ProviderRegistry._default_cache == "memory"
        assert ProviderRegistry._default_queue == "sync"

    def test_set_defaults_partial(self):
        """Test setting only some defaults."""
        original_cache = ProviderRegistry._default_cache
        ProviderRegistry.set_defaults(payment="mock")
        assert ProviderRegistry._default_payment == "mock"
        assert ProviderRegistry._default_cache == original_cache  # Unchanged

    # =========================================================================
    # Provider List Tests
    # =========================================================================

    def test_list_providers(self):
        """Test listing all registered providers."""
        providers = ProviderRegistry.list_providers()

        assert "payment" in providers
        assert "cache" in providers
        assert "queue" in providers

        # Default providers should be registered
        assert "mock" in providers["payment"]
        assert "memory" in providers["cache"]
        assert "sync" in providers["queue"]

    # =========================================================================
    # Health Check Tests
    # =========================================================================

    def test_health_check_all_healthy(self):
        """Test aggregate health check when all providers are healthy."""
        health = ProviderRegistry.health_check_all()

        assert "cache" in health
        assert "queue" in health
        assert health["cache"] is True
        assert health["queue"] is True

    def test_health_check_with_payment(self):
        """Test health check includes payment when available."""
        ProviderRegistry.register_payment("dummy", DummyPaymentProvider)
        ProviderRegistry.set_defaults(payment="dummy")
        ProviderRegistry.clear_instances()

        health = ProviderRegistry.health_check_all()
        assert "payment" in health
        assert health["payment"] is True


class TestProviderRegistryIntegration:
    """Integration tests for ProviderRegistry with actual adapters."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry before each test."""
        ProviderRegistry.clear_instances()
        yield
        ProviderRegistry.clear_instances()

    def test_cache_operations_through_registry(self):
        """Test cache operations using registry-provided adapter."""
        cache = ProviderRegistry.get_cache("memory")

        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"

        cache.delete("test_key")
        assert cache.get("test_key") is None

    def test_cache_with_ttl_through_registry(self):
        """Test cache TTL through registry."""
        cache = ProviderRegistry.get_cache("memory")

        cache.set("ttl_key", "value", ttl=timedelta(seconds=10))
        remaining = cache.ttl("ttl_key")
        assert remaining is not None
        assert remaining > 0

    def test_queue_task_execution_through_registry(self):
        """Test task execution using registry-provided queue."""
        queue = ProviderRegistry.get_queue("sync")
        results = []

        @queue.task(name="registry_test_task")
        def test_task(value):
            results.append(value)
            return value

        test_task.delay("from_registry")
        assert "from_registry" in results

    def test_payment_operations_through_registry(self):
        """Test payment operations using registry-provided adapter."""
        payment = ProviderRegistry.get_payment("mock")
        payment.set_confirm_response(success=True)

        result = payment.confirm_payment(
            payment_key="pk_test",
            order_id="order_123",
            amount=Decimal("10000"),
        )
        assert result.success is True

    def test_distributed_lock_through_registry(self):
        """Test distributed locking through registry-provided cache."""
        cache = ProviderRegistry.get_cache("memory")

        with cache.get_lock("test_lock") as lock:
            assert lock.locked() is True

    def test_provider_switching(self):
        """Test switching between providers at runtime."""
        # Start with memory cache
        cache1 = ProviderRegistry.get_cache("memory", singleton=False)
        cache1.set("key", "value1")

        # Create another memory instance (different from singleton)
        cache2 = ProviderRegistry.get_cache("memory", singleton=False)
        # Different instance, so key won't exist
        assert cache2.get("key") is None

    def test_registry_thread_safety(self):
        """Test registry is thread-safe for reads."""
        import threading

        results = []
        errors = []

        def get_provider(provider_type: str):
            try:
                if provider_type == "cache":
                    provider = ProviderRegistry.get_cache()
                elif provider_type == "queue":
                    provider = ProviderRegistry.get_queue()
                else:
                    provider = ProviderRegistry.get_payment()
                results.append(provider is not None)
            except Exception as e:
                errors.append(str(e))

        threads = []
        for _ in range(10):
            for ptype in ["cache", "queue"]:
                t = threading.Thread(target=get_provider, args=(ptype,))
                threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(results)
