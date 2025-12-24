"""
Metric Registration Helpers and Domain Registry.

Provides safe metric registration to avoid duplicate registration errors,
and dynamic domain management for metric labeling.
"""

from __future__ import annotations

import logging
from typing import List, Set

from prometheus_client import Counter, Gauge, Histogram, REGISTRY

logger = logging.getLogger(__name__)


# =============================================================================
# Safe Metric Registration Helpers
# =============================================================================


def get_or_create_counter(name: str, description: str, labels: list[str]) -> Counter:
    """Get existing counter or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Counter(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


def get_or_create_gauge(name: str, description: str, labels: list[str]) -> Gauge:
    """Get existing gauge or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        return Gauge(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


def get_or_create_histogram(
    name: str, description: str, labels: list[str], buckets: tuple = None
) -> Histogram:
    """Get existing histogram or create new one to avoid duplicate registration."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    try:
        if buckets:
            return Histogram(name, description, labels, buckets=buckets)
        return Histogram(name, description, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]


# =============================================================================
# Domain Registry (Dynamic Domain Registration)
# =============================================================================

# Registered domains - populated dynamically by adapters at initialization
_registered_domains: Set[str] = set()

# Default domains (domain-neutral fallbacks)
DEFAULT_DOMAINS: List[str] = [
    "external_service",
    "internal_process",
    "async_task",
    "notification",
    "data_sync",
]


def register_domain(domain: str) -> None:
    """
    Register a domain for metrics collection.

    Call this from adapters to register application-specific domains.
    Example: register_domain("payment"), register_domain("order")
    """
    _registered_domains.add(domain.lower())


def get_registered_domains() -> List[str]:
    """Get all registered domains, including defaults."""
    all_domains = _registered_domains | set(DEFAULT_DOMAINS)
    return sorted(all_domains)


# Legacy compatibility
@property
def DOMAINS() -> List[str]:
    """@deprecated: use get_registered_domains()"""
    return get_registered_domains()


# Pre-register common domains for backward compatibility
for _domain in DEFAULT_DOMAINS:
    register_domain(_domain)
