from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.order import Order, OrderItem



@pytest.mark.django_db
class TestOrderListHappyPath:
    """주문 목록 조회 - 정상 케이스"""

    def test_get_empty_order_list(self, authenticated_client, user):
        """빈 주문 목록 조회"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0
        assert response.data["results"] == []

    def test_get_single_order(self, authenticated_client, user, order):
        """단일 주문 조회"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["id"] == order.id
        assert response.data["results"][0]["status"] == order.status

    def test_get_multiple_orders(self, authenticated_client, user, product, order_factory):
        """여러 주문 조회"""
        # Arrange - 3개의 주문 생성
        orders = []
        for i in range(3):
            order_obj = order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000") * (i + 1),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )
            OrderItem.objects.create(
                order=order_obj,
                product=product,
                product_name=product.name,
                quantity=i + 1,
                price=product.price,
            )
            orders.append(order_obj)

        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 3

    def test_filter_by_status_pending(self, authenticated_client, user, order_factory):
        """pending 상태 주문 필터링"""
        # Arrange - 다양한 상태의 주문 생성
        order_factory(user, status="pending", total_amount=Decimal("10000"))
        order_factory(user, status="paid", total_amount=Decimal("20000"))

        url = reverse("order-list") + "?status=pending"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["status"] == "pending"

    def test_filter_by_status_paid(self, authenticated_client, user, order_factory):
        """paid 상태 주문 필터링"""
        # Arrange
        order_factory(user, status="pending", total_amount=Decimal("10000"))
        order_factory(user, status="paid", total_amount=Decimal("20000"))

        url = reverse("order-list") + "?status=paid"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["status"] == "paid"

    def test_filter_by_status_shipped(self, authenticated_client, user, order_factory):
        """shipped 상태 주문 필터링"""
        # Arrange
        order_factory(user, status="shipped", total_amount=Decimal("30000"))
        order_factory(user, status="delivered", total_amount=Decimal("40000"))

        url = reverse("order-list") + "?status=shipped"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["status"] == "shipped"

    def test_order_by_created_desc(self, authenticated_client, user, order_factory):
        """최신순 정렬 (기본값)"""
        # Arrange - 시간차를 두고 주문 생성
        now = timezone.now()

        order1 = order_factory(
            user,
            status="pending",
            total_amount=Decimal("10000"),
            shipping_name="첫번째",
            shipping_address_detail="101호",
        )
        order2 = order_factory(
            user,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="두번째",
            shipping_address_detail="102호",
        )

        # created_at을 명시적으로 수정 (시간차 보장)
        Order.objects.filter(id=order1.id).update(created_at=now - timedelta(hours=1))
        Order.objects.filter(id=order2.id).update(created_at=now)

        url = reverse("order-list") + "?ordering=-created_at"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert results[0]["id"] == order2.id  # 최신 주문이 먼저
        assert results[1]["id"] == order1.id

    def test_order_by_created_asc(self, authenticated_client, user, order_factory):
        """오래된순 정렬"""
        # Arrange
        now = timezone.now()

        order1 = order_factory(
            user,
            status="pending",
            total_amount=Decimal("10000"),
            shipping_name="첫번째",
            shipping_address_detail="101호",
        )
        order2 = order_factory(
            user,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="두번째",
            shipping_address_detail="102호",
        )

        # created_at을 명시적으로 수정
        Order.objects.filter(id=order1.id).update(created_at=now - timedelta(hours=1))
        Order.objects.filter(id=order2.id).update(created_at=now)

        url = reverse("order-list") + "?ordering=created_at"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert results[0]["id"] == order1.id  # 오래된 주문이 먼저
        assert results[1]["id"] == order2.id

    def test_pagination_first_page(self, authenticated_client, user, order_factory):
        """첫 페이지 조회"""
        # Arrange - 5개 주문 생성
        for i in range(5):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page=1"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 5
        assert len(response.data["results"]) == 5
        assert "next" in response.data
        assert "previous" in response.data

    def test_pagination_with_page_size(self, authenticated_client, user, order_factory):
        """페이지 사이즈 변경"""
        # Arrange - 15개 주문 생성
        for i in range(15):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page_size=5"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 15
        assert len(response.data["results"]) == 5  # page_size만큼만 반환


@pytest.mark.django_db
class TestOrderListBoundary:
    """주문 목록 조회 - 경계값 테스트"""

    def test_pagination_page_size_1(self, authenticated_client, user, order_factory):
        """최소 페이지 사이즈 (1)"""
        # Arrange
        for i in range(3):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page_size=1"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3
        assert len(response.data["results"]) == 1

    def test_pagination_page_size_100(self, authenticated_client, user, order_factory):
        """최대 페이지 사이즈 (100)"""
        # Arrange - 50개 주문 생성
        for i in range(50):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page_size=100"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 50
        assert len(response.data["results"]) == 50

    def test_pagination_last_page(self, authenticated_client, user, order_factory):
        """마지막 페이지 조회"""
        # Arrange - 25개 주문 생성 (page_size=10이면 3페이지)
        for i in range(25):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page=3"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 25
        assert len(response.data["results"]) == 5  # 마지막 페이지는 5개
        assert response.data["next"] is None
        assert response.data["previous"] is not None

    def test_pagination_beyond_last_page(self, authenticated_client, user, order_factory):
        """범위 초과 페이지 조회"""
        # Arrange - 5개 주문만 생성
        for i in range(5):
            order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )

        url = reverse("order-list") + "?page=999"

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_filter_all_status_values(self, authenticated_client, user, order_factory, order_statuses):
        """모든 주문 상태 값 필터링"""
        # Arrange - 모든 상태의 주문 생성
        for status_value in order_statuses["all"]:
            order_factory(user, status=status_value, total_amount=Decimal("10000"))

        # Act & Assert - 각 상태별로 필터링 테스트
        for status_value in order_statuses["all"]:
            url = reverse("order-list") + f"?status={status_value}"
            response = authenticated_client.get(url)

            assert response.status_code == status.HTTP_200_OK
            assert response.data["count"] == 1
            assert response.data["results"][0]["status"] == status_value

    def test_large_dataset(self, authenticated_client, user, product, order_factory):
        """대량 주문 조회 성능 테스트"""
        # Arrange - 100개 주문 생성
        orders = []
        for i in range(100):
            order_obj = order_factory(
                user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_address_detail=f"{i}호",
            )
            OrderItem.objects.create(
                order=order_obj,
                product=product,
                product_name=product.name,
                quantity=1,
                price=product.price,
            )
            orders.append(order_obj)

        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 100
        assert len(response.data["results"]) == 10  # 기본 page_size


@pytest.mark.django_db
class TestOrderListException:
    """주문 목록 조회 - 예외 케이스"""

    def test_get_orders_unauthenticated(self, api_client):
        """인증 없이 주문 목록 조회 시도"""
        # Arrange
        url = reverse("order-list")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_user_sees_only_own_orders(self, user, other_user, order_factory, login_helper):
        """일반 사용자는 본인 주문만 조회"""
        # Arrange - 두 명의 사용자와 각각의 주문 생성
        user1 = user
        user2 = other_user

        # user1의 주문
        order_factory(
            user1,
            status="pending",
            total_amount=Decimal("10000"),
            shipping_name="사용자1",
            shipping_phone="010-1111-1111",
            shipping_address_detail="101호",
        )

        # user2의 주문
        order_factory(
            user2,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="사용자2",
            shipping_phone="010-2222-2222",
            shipping_address_detail="202호",
        )

        # user1으로 로그인
        client, _ = login_helper(user1)
        url = reverse("order-list")

        # Act
        response = client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1  # 본인 주문만
        assert response.data["results"][0]["user_username"] == "testuser"

    def test_admin_sees_all_orders(self, user, admin_user, order_factory, login_helper):
        """관리자는 모든 주문 조회 가능"""
        # Arrange - 관리자와 일반 사용자 생성
        # 일반 사용자 주문
        order_factory(
            user,
            status="pending",
            total_amount=Decimal("10000"),
            shipping_name="일반사용자",
            shipping_phone="010-1111-1111",
            shipping_address_detail="101호",
        )

        # 관리자 주문
        order_factory(
            admin_user,
            status="pending",
            total_amount=Decimal("20000"),
            shipping_name="관리자",
            shipping_phone="010-2222-2222",
            shipping_address_detail="202호",
        )

        # 관리자로 로그인
        client, _ = login_helper(admin_user)
        url = reverse("order-list")

        # Act
        response = client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2  # 모든 주문 조회

    def test_invalid_status_filter(self, authenticated_client, user, order_factory):
        """잘못된 status 값으로 필터링"""
        # Arrange
        order_factory(user, status="pending", total_amount=Decimal("10000"))

        url = reverse("order-list") + "?status=invalid_status"

        # Act
        response = authenticated_client.get(url)

        # Assert - django-filter는 잘못된 choice 값에 대해 400 반환
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_ordering_field(self, authenticated_client, user, order_factory):
        """허용되지 않은 필드로 정렬 시도"""
        # Arrange
        order_factory(user, status="pending", total_amount=Decimal("10000"))

        url = reverse("order-list") + "?ordering=invalid_field"

        # Act
        response = authenticated_client.get(url)

        # Assert - 잘못된 ordering은 무시되고 기본 정렬 적용
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_order_list_no_n_plus_1(self, authenticated_client, user, product, django_assert_num_queries):
        """N+1 쿼리 문제 확인"""
        # Arrange - 10개 주문 생성 (각 주문에 OrderItem 포함)
        for i in range(10):
            order_obj = Order.objects.create(
                user=user,
                status="pending",
                total_amount=Decimal("10000"),
                shipping_name=f"주문자{i}",
                shipping_phone="010-1234-5678",
                shipping_postal_code="12345",
                shipping_address="서울",
                shipping_address_detail=f"{i}호",
            )
            OrderItem.objects.create(
                order=order_obj,
                product=product,
                product_name=product.name,
                quantity=1,
                price=product.price,
            )

        url = reverse("order-list")

        # Act & Assert - 쿼리 수 확인
        # 실제 쿼리: 인증(1) + count(1) + orders with select_related/prefetch_related(1)
        #            + annotate(1) + session(1) = 5
        # 리팩토링 후 select_related("user")로 user 조회 최적화 완료
        with django_assert_num_queries(5):
            response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 10

    def test_list_without_pagination_returns_raw_list(self, authenticated_client, user, order_factory, mocker):
        """페이지네이션 비활성화 시 raw 리스트 반환"""
        # Arrange
        order_factory(
            user, status="pending", total_amount=Decimal("10000"), shipping_name="주문자1", shipping_address_detail="101호"
        )
        order_factory(
            user, status="paid", total_amount=Decimal("20000"), shipping_name="주문자2", shipping_address_detail="102호"
        )

        from shopping.views.order_views import OrderViewSet

        mocker.patch.object(OrderViewSet, "paginate_queryset", return_value=None)

        url = reverse("order-list")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)
        assert len(response.data) == 2
