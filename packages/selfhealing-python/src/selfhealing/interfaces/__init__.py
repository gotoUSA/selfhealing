"""
Interface definitions for the self-healing system.

This module contains abstract base classes that define contracts
for repository implementations (Repository Pattern) and external
service integrations (Adapter Pattern).
"""

from selfhealing.interfaces.repositories import (
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)
from selfhealing.interfaces.cache_provider import (
    CacheProviderInterface,
    DistributedLock,
)
from selfhealing.interfaces.task_queue import (
    TaskQueueInterface,
    TaskStatus,
    TaskResult,
    TaskOptions,
)
from selfhealing.interfaces.payment_provider import (
    PaymentProviderInterface,
    PaymentConfirmResult,
    PaymentCancelResult,
    WebhookVerifyResult,
    PaymentStatusResult,
)

__all__ = [
    # Repository interfaces
    "FailedOperationRepository",
    "CircuitBreakerStateRepository",
    "SecurityIncidentRepository",
    # Cache provider interface
    "CacheProviderInterface",
    "DistributedLock",
    # Task queue interface
    "TaskQueueInterface",
    "TaskStatus",
    "TaskResult",
    "TaskOptions",
    # Payment provider interface
    "PaymentProviderInterface",
    "PaymentConfirmResult",
    "PaymentCancelResult",
    "WebhookVerifyResult",
    "PaymentStatusResult",
]
