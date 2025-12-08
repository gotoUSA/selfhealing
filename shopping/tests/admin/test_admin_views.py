"""
Tests for Admin list views and CRUD operations.

Verifies that:
- Admin list views load successfully
- Search functionality works
- Filters work correctly
- Detail views load
"""

import pytest
from django.urls import reverse

from shopping.tests.factories import (
    CategoryFactory,
    OrderFactory,
    PaymentFactory,
    ProductFactory,
    UserFactory,
)


@pytest.fixture
def admin_user(db):
    """Create a superuser for admin access."""
    return UserFactory.admin(username="admin_test_user")


@pytest.fixture
def admin_client(client, admin_user):
    """Client logged in as admin."""
    client.force_login(admin_user)
    return client


@pytest.mark.django_db
class TestUserAdminListView:
    """Tests for UserAdmin list view."""

    def test_user_list_view_loads(self, admin_client):
        """Test that user list view loads successfully."""
        url = reverse("admin:shopping_user_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_user_search(self, admin_client):
        """Test user search functionality."""
        user = UserFactory(username="searchable_user", email="searchable@test.com")
        url = reverse("admin:shopping_user_changelist")
        
        response = admin_client.get(url, {"q": "searchable"})
        assert response.status_code == 200
        assert user.username in str(response.content)


@pytest.mark.django_db
class TestProductAdminListView:
    """Tests for ProductAdmin list view."""

    def test_product_list_view_loads(self, admin_client):
        """Test that product list view loads successfully."""
        url = reverse("admin:shopping_product_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_product_search(self, admin_client):
        """Test product search functionality."""
        category = CategoryFactory()
        product = ProductFactory(name="TestSearchProduct", category=category)
        url = reverse("admin:shopping_product_changelist")
        
        response = admin_client.get(url, {"q": "TestSearchProduct"})
        assert response.status_code == 200
        assert product.name in str(response.content)

    def test_product_detail_view_loads(self, admin_client):
        """Test that product detail view loads successfully."""
        category = CategoryFactory()
        product = ProductFactory(category=category)
        url = reverse("admin:shopping_product_change", args=[product.pk])
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestCategoryAdminListView:
    """Tests for CategoryAdmin list view."""

    def test_category_list_view_loads(self, admin_client):
        """Test that category list view loads successfully."""
        url = reverse("admin:shopping_category_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestOrderAdminListView:
    """Tests for OrderAdmin list view."""

    def test_order_list_view_loads(self, admin_client):
        """Test that order list view loads successfully."""
        url = reverse("admin:shopping_order_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_order_search_by_order_number(self, admin_client):
        """Test order search by order number."""
        user = UserFactory()
        order = OrderFactory(user=user)
        url = reverse("admin:shopping_order_changelist")
        
        response = admin_client.get(url, {"q": order.order_number})
        assert response.status_code == 200


@pytest.mark.django_db
class TestPaymentAdminListView:
    """Tests for PaymentAdmin list view."""

    def test_payment_list_view_loads(self, admin_client):
        """Test that payment list view loads successfully."""
        url = reverse("admin:shopping_payment_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_payment_detail_view_loads(self, admin_client):
        """Test that payment detail view loads successfully."""
        user = UserFactory()
        order = OrderFactory(user=user)
        payment = PaymentFactory(order=order)
        url = reverse("admin:shopping_payment_change", args=[payment.pk])
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestCartAdminListView:
    """Tests for CartAdmin list view."""

    def test_cart_list_view_loads(self, admin_client):
        """Test that cart list view loads successfully."""
        url = reverse("admin:shopping_cart_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestNotificationAdminListView:
    """Tests for NotificationAdmin list view."""

    def test_notification_list_view_loads(self, admin_client):
        """Test that notification list view loads successfully."""
        url = reverse("admin:shopping_notification_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestPointHistoryAdminListView:
    """Tests for PointHistoryAdmin list view."""

    def test_point_history_list_view_loads(self, admin_client):
        """Test that point history list view loads successfully."""
        url = reverse("admin:shopping_pointhistory_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestReturnAdminListView:
    """Tests for ReturnAdmin list view."""

    def test_return_list_view_loads(self, admin_client):
        """Test that return list view loads successfully."""
        url = reverse("admin:shopping_return_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestCircuitBreakerAdminListView:
    """Tests for CircuitBreakerStateAdmin list view."""

    def test_circuit_breaker_list_view_loads(self, admin_client):
        """Test that circuit breaker list view loads successfully."""
        url = reverse("admin:shopping_circuitbreakerstate_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestFailedOperationAdminListView:
    """Tests for FailedOperationAdmin list view."""

    def test_failed_operation_list_view_loads(self, admin_client):
        """Test that failed operation list view loads successfully."""
        url = reverse("admin:shopping_failedoperation_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200
