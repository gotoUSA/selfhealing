"""
페이지네이션 경계값 테스트

테스트 범위:
- page_size = 데이터 개수
- 마지막 페이지가 1개
- 빈 결과 페이지
- 극단적인 값
"""

import pytest
from django.urls import reverse
from rest_framework import status

from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    PointHistoryFactory,
    ProductFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestPaginationBoundary:
    """페이지네이션 경계값 테스트"""

    def test_page_size_equals_total_count(self, api_client):
        """page_size가 전체 데이터 개수와 같을 때"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(10):
            order = OrderFactory(user=user, order_number=f"2025010100{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 10
        assert len(data["results"]) == 10
        assert data["page"] == 1

    def test_last_page_single_item(self, api_client):
        """마지막 페이지에 1개만 있을 때"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(11):
            order = OrderFactory(user=user, order_number=f"2025010200{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=2&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 11
        assert len(data["results"]) == 1
        assert data["page"] == 2

    def test_page_size_larger_than_data(self, api_client):
        """page_size가 전체 데이터보다 클 때"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(3):
            order = OrderFactory(user=user, order_number=f"2025010300{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=50")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 3
        assert len(data["results"]) == 3
        assert data["page_size"] == 50

    def test_empty_page_beyond_data(self, api_client):
        """데이터 범위를 벗어난 페이지 요청"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(5):
            order = OrderFactory(user=user, order_number=f"2025010400{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=10&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 5
        assert len(data["results"]) == 0

    def test_page_1_always_valid(self, api_client):
        """page=1은 데이터 없어도 유효"""
        # Arrange
        user = UserFactory()
        api_client.force_authenticate(user=user)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 0
        assert len(data["results"]) == 0
        assert data["page"] == 1


@pytest.mark.django_db
class TestPaginationExtremeValues:
    """페이지네이션 극단값 테스트"""

    def test_page_size_1(self, api_client):
        """page_size=1 (최소값)"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(3):
            order = OrderFactory(user=user, order_number=f"2025010500{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=1")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 3
        assert len(data["results"]) == 1
        assert data["page_size"] == 1

    def test_page_size_100(self, api_client):
        """page_size=100 (최대값)"""
        # Arrange
        user = UserFactory()
        product = ProductFactory()
        api_client.force_authenticate(user=user)

        for i in range(5):
            order = OrderFactory(user=user, order_number=f"2025010600{i:02d}")
            OrderItemFactory(order=order, product=product)
            PaymentFactory(order=order)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=100")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["page_size"] == 100
        assert len(data["results"]) == 5

    def test_page_size_101_rejected(self, api_client):
        """page_size=101 (최대값 초과) 거부"""
        # Arrange
        user = UserFactory()
        api_client.force_authenticate(user=user)

        # Act
        response = api_client.get("/api/payments/?page=1&page_size=101")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page_size는 최대 100까지" in str(response.json())

    def test_very_large_page_number(self, api_client):
        """매우 큰 페이지 번호"""
        # Arrange
        user = UserFactory()
        api_client.force_authenticate(user=user)

        # Act
        response = api_client.get("/api/payments/?page=99999&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["results"]) == 0


@pytest.mark.django_db
class TestPointHistoryPagination:
    """포인트 이력 페이지네이션 테스트"""

    def test_point_history_pagination(self, api_client):
        """포인트 이력 페이지네이션"""
        # Arrange
        user = UserFactory.with_points(10000)
        api_client.force_authenticate(user=user)

        for i in range(15):
            PointHistoryFactory.earn(user=user, points=100, balance=(i + 1) * 100)

        # Act
        response = api_client.get(reverse("point_history") + "?page=1&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 15
        assert len(data["results"]) == 10

    def test_point_history_second_page(self, api_client):
        """포인트 이력 2페이지"""
        # Arrange
        user = UserFactory.with_points(10000)
        api_client.force_authenticate(user=user)

        for i in range(15):
            PointHistoryFactory.earn(user=user, points=100, balance=(i + 1) * 100)

        # Act
        response = api_client.get(reverse("point_history") + "?page=2&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["count"] == 15
        assert len(data["results"]) == 5
        assert data["page"] == 2
