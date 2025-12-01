"""결제 목록 조회 테스트"""

from decimal import Decimal

from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    CompletedPaymentFactory,
    ProductFactory,
)


@pytest.mark.django_db
class TestPaymentListNormalCase:
    """정상 케이스"""

    def test_get_payment_list_success(
        self,
        authenticated_client,
        user,
        product,
        category,
    ) -> None:
        """정상 결제 목록 조회"""
        # Arrange - 결제 3건 생성
        for i in range(3):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert - 응답 상태
        assert response.status_code == status.HTTP_200_OK

        # Assert - 데이터 개수
        data = response.json()
        assert data["count"] == 3
        assert len(data["results"]) == 3

    def test_response_data_structure(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """응답 데이터 구조 검증"""
        # Arrange
        order = OrderFactory(
            user=user,
            total_amount=product.price,
            order_number="20250115000001",
        )
        OrderItemFactory(
            order=order,
            product=product,
        )
        CompletedPaymentFactory(
            order=order,
            payment_key="test_key",
        )

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()

        # Assert - 최상위 필드
        assert "count" in data
        assert "page" in data
        assert "page_size" in data
        assert "results" in data

        # Assert - 타입 검증
        assert isinstance(data["count"], int)
        assert isinstance(data["page"], int)
        assert isinstance(data["page_size"], int)
        assert isinstance(data["results"], list)

        # Assert - 결제 객체 필드
        payment_data = data["results"][0]
        required_fields = [
            "id",
            "order",
            "order_number",
            "amount",
            "status",
            "status_display",
            "created_at",
        ]
        for field in required_fields:
            assert field in payment_data, f"필수 필드 누락: {field}"

    def test_payment_list_ordered_by_latest(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """최신순 정렬 확인"""
        # Arrange - 시간 순서대로 3건 생성
        payments_created = []
        for i in range(3):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            payment = PaymentFactory(order=order)
            payments_created.append(payment)

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        results = data["results"]

        # Assert - 최신순 정렬 (역순)
        assert results[0]["id"] == payments_created[2].id
        assert results[1]["id"] == payments_created[1].id
        assert results[2]["id"] == payments_created[0].id

    def test_pagination_basic(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """기본 페이지네이션 (1페이지)"""
        # Arrange - 15건 생성
        for i in range(15):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act
        response = authenticated_client.get("/api/payments/?page=1&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 15
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert len(data["results"]) == 10

    def test_pagination_second_page(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """2페이지 조회"""
        # Arrange - 15건 생성
        for i in range(15):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act
        response = authenticated_client.get("/api/payments/?page=2&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 15
        assert data["page"] == 2
        assert data["page_size"] == 10
        assert len(data["results"]) == 5

    def test_filter_by_status_done(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """상태 필터링 - done"""
        # Arrange - 다양한 상태의 결제 생성
        statuses = ["ready", "done", "done", "canceled", "aborted"]
        for i, payment_status in enumerate(statuses):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(
                order=order,
                status=payment_status,
            )

        # Act
        response = authenticated_client.get("/api/payments/?status=done")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 2

        # Assert - 모두 done 상태
        for result in data["results"]:
            assert result["status"] == "done"

    def test_filter_by_status_ready(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """상태 필터링 - ready"""
        # Arrange
        statuses = ["ready", "ready", "ready", "done", "canceled"]
        for i, payment_status in enumerate(statuses):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(
                order=order,
                status=payment_status,
            )

        # Act
        response = authenticated_client.get("/api/payments/?status=ready")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 3
        assert len(data["results"]) == 3

        for result in data["results"]:
            assert result["status"] == "ready"

    def test_filter_by_status_canceled(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """상태 필터링 - canceled"""
        # Arrange
        statuses = ["done", "canceled", "ready"]
        for i, payment_status in enumerate(statuses):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(
                order=order,
                status=payment_status,
            )

        # Act
        response = authenticated_client.get("/api/payments/?status=canceled")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "canceled"

    def test_filter_by_status_aborted(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """상태 필터링 - aborted (결제 실패)"""
        # Arrange
        statuses = ["ready", "aborted", "aborted", "done"]
        for i, payment_status in enumerate(statuses):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(
                order=order,
                status=payment_status,
            )

        # Act
        response = authenticated_client.get("/api/payments/?status=aborted")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 2

        for result in data["results"]:
            assert result["status"] == "aborted"

    def test_only_user_payments_visible(
        self,
        authenticated_client,
        user,
        other_user,
        product,
    ) -> None:
        """내 결제만 조회 (다른 사용자 결제 안보임)"""
        # Arrange - 내 결제 2건
        for i in range(2):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Arrange - 다른 사용자 결제 3건
        for i in range(3):
            order = OrderFactory(
                user=other_user,
                order_number=f"2025011510{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert - 내 결제 2건만 조회
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 2
        assert len(data["results"]) == 2

    def test_default_pagination_params(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """기본값 (page=1, page_size=10)"""
        # Arrange - 5건 생성
        for i in range(5):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - 파라미터 없이 호출
        response = authenticated_client.get("/api/payments/")

        # Assert - 기본값 적용 확인
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert data["count"] == 5
        assert len(data["results"]) == 5


@pytest.mark.django_db
class TestPaymentListBoundary:
    """경계값 테스트"""

    def test_empty_payment_list(
        self,
        authenticated_client,
    ) -> None:
        """빈 목록"""
        # Arrange - 결제 없음

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 0
        assert len(data["results"]) == 0
        assert data["page"] == 1
        assert data["page_size"] == 10

    def test_large_dataset_pagination(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """대량 데이터 조회 (200건) - 성능 및 페이지네이션 검증"""
        # Arrange - 200건 생성
        for i in range(200):
            order = OrderFactory(
                user=user,
                order_number=f"202501{i:06d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - 중간 페이지 조회
        response = authenticated_client.get("/api/payments/?page=10&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 200
        assert data["page"] == 10
        assert data["page_size"] == 10
        assert len(data["results"]) == 10

        # Act - 마지막 페이지 조회
        response_last = authenticated_client.get("/api/payments/?page=20&page_size=10")

        # Assert
        assert response_last.status_code == status.HTTP_200_OK
        data_last = response_last.json()
        assert data_last["count"] == 200
        assert len(data_last["results"]) == 10

    def test_last_page(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """마지막 페이지 (일부만 채워진 페이지)"""
        # Arrange - 25건 생성
        for i in range(25):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - 3페이지 (5건만 있음)
        response = authenticated_client.get("/api/payments/?page=3&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 25
        assert data["page"] == 3
        assert len(data["results"]) == 5

    def test_page_exceeds_total(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """페이지 번호가 전체 페이지 초과 (빈 결과)"""
        # Arrange - 5건만 생성
        for i in range(5):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - 10페이지 요청 (실제로는 1페이지만 있음)
        response = authenticated_client.get("/api/payments/?page=10&page_size=10")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 5
        assert data["page"] == 10
        assert len(data["results"]) == 0

    def test_custom_page_size(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """커스텀 page_size (20, 50)"""
        # Arrange - 30건 생성
        for i in range(30):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - page_size=20
        response = authenticated_client.get("/api/payments/?page=1&page_size=20")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["page_size"] == 20
        assert len(data["results"]) == 20

        # Act - page_size=50
        response2 = authenticated_client.get("/api/payments/?page=1&page_size=50")

        # Assert
        assert response2.status_code == status.HTTP_200_OK
        data2 = response2.json()
        assert data2["page_size"] == 50
        assert len(data2["results"]) == 30

    def test_max_page_size(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """매우 큰 page_size 제한 (1000) - DoS 방지"""
        # Arrange - 10건만 생성
        for i in range(10):
            order = OrderFactory(
                user=user,
                order_number=f"2025011500{i:03d}",
            )
            OrderItemFactory(
                order=order,
                product=product,
            )
            PaymentFactory(order=order)

        # Act - page_size=1000 요청
        response = authenticated_client.get("/api/payments/?page=1&page_size=1000")

        # Assert - 에러 반환
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page_size는 최대 100까지 가능합니다" in str(response.json())

    def test_single_payment(
        self,
        authenticated_client,
        user,
        product,
    ) -> None:
        """결제 1건만 있을 때"""
        # Arrange - 1건만 생성
        order = OrderFactory(
            user=user,
            order_number="20250115000001",
        )
        OrderItemFactory(
            order=order,
            product=product,
        )
        PaymentFactory(order=order)

        # Act
        response = authenticated_client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert data["count"] == 1
        assert len(data["results"]) == 1


@pytest.mark.django_db
class TestPaymentListException:
    """예외 케이스"""

    def test_unauthenticated_user(
        self,
        api_client,
    ) -> None:
        """인증되지 않은 사용자"""
        # Arrange - 인증 없음

        # Act
        response = api_client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_invalid_page_number_zero(
        self,
        authenticated_client,
    ) -> None:
        """페이지 0"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?page=0")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page는 1 이상이어야 합니다" in str(response.json())

    def test_invalid_page_number_negative(
        self,
        authenticated_client,
    ) -> None:
        """음수 페이지"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?page=-1")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page는 1 이상이어야 합니다" in str(response.json())

    def test_invalid_page_number_string(
        self,
        authenticated_client,
    ) -> None:
        """문자열 페이지"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?page=abc")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page와 page_size는 정수여야 합니다" in str(response.json())

    def test_invalid_page_size_zero(
        self,
        authenticated_client,
    ) -> None:
        """page_size 0"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?page_size=0")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page_size는 1 이상이어야 합니다" in str(response.json())

    def test_invalid_page_size_negative(
        self,
        authenticated_client,
    ) -> None:
        """음수 page_size"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?page_size=-10")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "page_size는 1 이상이어야 합니다" in str(response.json())

    def test_invalid_status_filter(
        self,
        authenticated_client,
    ) -> None:
        """잘못된 status 값 (존재하지 않는 상태)"""
        # Arrange

        # Act
        response = authenticated_client.get("/api/payments/?status=invalid_status")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "유효하지 않은 상태값입니다" in str(response.json())
        assert "ready" in str(response.json())
        assert "done" in str(response.json())
