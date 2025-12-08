"""
E2E Test Fixtures

Provides fixtures for end-to-end testing of payment flows,
failure recovery, and user journeys.
"""

import pytest
from decimal import Decimal

from shopping.models.user import User
from shopping.tests.factories import (
    OrderFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)


@pytest.fixture
def admin_user(db) -> User:
    """Create an admin user for E2E tests."""
    return UserFactory.admin(username="e2e_test_admin")


@pytest.fixture
def sample_payment(db):
    """Create a sample payment for E2E testing."""
    user = UserFactory.with_points(50000)
    order = OrderFactory(user=user, status="confirmed")
    payment = PaymentFactory(
        order=order,
        status="in_progress",
        amount=Decimal("10000"),
    )
    return payment
