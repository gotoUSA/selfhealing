"""
Payment provider adapters for the self-healing system.

This package contains implementations of PaymentProviderInterface.
"""

from selfhealing.adapters.payments.mock_adapter import MockPaymentAdapter

__all__ = [
    "MockPaymentAdapter",
]

# Conditionally import adapters based on available dependencies
try:
    from selfhealing.adapters.payments.stripe_adapter import StripePaymentAdapter

    __all__.append("StripePaymentAdapter")
except ImportError:
    pass
