"""
Payment provider adapters for the self-healing system.

This package contains implementations of PaymentProviderInterface.
"""

from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter

__all__ = [
    "MockPaymentAdapter",
]
