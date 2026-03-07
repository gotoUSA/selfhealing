"""
ProviderRegistry cache metrics wrapping tests (311 — Phase 4a).

Verifies that _wrap_cache_with_metrics correctly wraps cache adapters
and prevents double-wrapping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from selfhealing.adapters.cache.metrics_decorator import MetricsAwareCacheAdapter
from selfhealing.factory import ProviderRegistry
from selfhealing.interfaces.cache_provider import CacheProviderInterface


class TestWrapCacheWithMetricsBehavior:
    """ProviderRegistry._wrap_cache_with_metrics behavior."""

    def test_wraps_plain_adapter_with_metrics(self):
        """Plain CacheProviderInterface is wrapped with MetricsAwareCacheAdapter."""
        plain = MagicMock(spec=CacheProviderInterface)
        result = ProviderRegistry._wrap_cache_with_metrics(plain)
        assert isinstance(result, MetricsAwareCacheAdapter)

    def test_does_not_double_wrap(self):
        """Already-wrapped adapter is returned as-is."""
        plain = MagicMock(spec=CacheProviderInterface)
        wrapped = MetricsAwareCacheAdapter(plain)
        result = ProviderRegistry._wrap_cache_with_metrics(wrapped)
        assert result is wrapped

    def test_wrapped_adapter_delegates_to_original(self):
        """Wrapped adapter's delegate is the original adapter."""
        plain = MagicMock(spec=CacheProviderInterface)
        result = ProviderRegistry._wrap_cache_with_metrics(plain)
        assert result._delegate is plain
