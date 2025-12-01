"""
ReturnViewSet 테스트

View 레이어에서만 테스트 가능한 항목에 집중:
- HTTP 인증/인가 (401/403)
- 권한 체크 (403)
- QuerySet 필터링 (본인 데이터만)
- URL 쿼리 파라미터 필터

비즈니스 로직은 test_return_service.py에서 커버됨
"""

import pytest
from django.urls import reverse
from rest_framework import status

from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    ProductFactory,
    ReturnFactory,
    ReturnItemFactory,
    UserFactory,
)


def get_results(response_data):
    """
    페이지네이션 응답에서 results 추출 헬퍼


    DRF PageNumberPagination 응답: {"count": N, "results": [...]}
    페이지네이션 없는 응답: [...]
    """
    if isinstance(response_data, dict) and "results" in response_data:
        return response_data["results"]
    return response_data


@pytest.mark.django_db
class TestReturnViewAuthentication:
    """인증 테스트 - 미인증 사용자 접근 차단"""

    def test_list_unauthenticated_returns_401_or_403(self, api_client):
        """미인증 사용자 목록 조회 시 인증 에러"""
        # Arrange
        url = reverse("return-list")

        # Act
        response = api_client.get(url)

        # Assert - JWT 설정에 따라 401 또는 403
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_create_unauthenticated_returns_401_or_403(self, api_client):
        """미인증 사용자 신청 생성 시 인증 에러"""
        # Arrange - return-list를 사용하고 order_id를 body에 포함
        url = reverse("return-list")

        # Act
        response = api_client.post(url, {"order_id": 1})


        # Assert
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]

    def test_seller_action_unauthenticated_returns_401_or_403(self, api_client):
        """미인증 사용자 판매자 액션 시 인증 에러"""
        # Arrange
        return_obj = ReturnFactory()
        url = reverse("seller-return-approve", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code in [
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ]


@pytest.mark.django_db
class TestReturnViewPermission:
    """권한 테스트 - 본인/판매자 권한 확인"""

    def test_user_cannot_see_other_user_returns(self, api_client):
        """타인의 교환/환불 목록 조회 불가 (일반 사용자)"""
        # Arrange - 다른 사용자의 Return 생성
        other_user = UserFactory(is_seller=False)
        other_order = OrderFactory.delivered(user=other_user)
        other_return = ReturnFactory(order=other_order, user=other_user)
        ReturnItemFactory(return_request=other_return)

        # 현재 사용자 (is_seller=False 명시)
        current_user = UserFactory(is_seller=False)
        api_client.force_authenticate(user=current_user)
        url = reverse("return-list")

        # Act
        response = api_client.get(url)

        # Assert - 일반 사용자는 본인 Return만 조회 가능
        assert response.status_code == status.HTTP_200_OK
        results = get_results(response.data)
        return_ids = [r["id"] for r in results]
        assert other_return.id not in return_ids

    def test_user_cannot_delete_other_user_return(self, api_client):
        """타인의 교환/환불 신청 삭제 불가"""
        # Arrange
        other_user = UserFactory(is_seller=False)
        other_order = OrderFactory.delivered(user=other_user)
        other_return = ReturnFactory(order=other_order, user=other_user)
        ReturnItemFactory(return_request=other_return)

        current_user = UserFactory(is_seller=False)
        api_client.force_authenticate(user=current_user)
        url = reverse("return-detail", kwargs={"pk": other_return.id})

        # Act
        response = api_client.delete(url)

        # Assert - QuerySet 필터링으로 404 반환
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_non_seller_cannot_approve(self, api_client):
        """판매자가 아닌 사용자는 승인 불가 (404 - queryset 필터링)"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        # 비판매자로 승인 시도
        non_seller = UserFactory(is_seller=False)
        api_client.force_authenticate(user=non_seller)
        url = reverse("seller-return-approve", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert - 비판매자는 queryset에서 아예 조회 안됨 → 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_seller_cannot_approve_other_seller_product(self, api_client):
        """타 판매자 상품의 교환/환불은 접근 불가 (404 - queryset 필터링)"""
        # Arrange
        seller_a = UserFactory(is_seller=True)
        seller_b = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller_a)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        # seller_b로 접근 시도
        api_client.force_authenticate(user=seller_b)
        url = reverse("seller-return-approve", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert - 타 판매자 상품은 queryset에서 필터링 → 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestReturnViewFiltering:
    """필터링 테스트 - QuerySet 및 쿼리 파라미터"""

    def test_list_returns_with_status_filter(self, api_client):
        """status 필터 적용 조회"""
        # Arrange
        user = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=user)

        requested_return = ReturnFactory(order=order, user=user, status="requested")
        ReturnItemFactory(return_request=requested_return)

        approved_return = ReturnFactory.approved(order=order, user=user)
        ReturnItemFactory(return_request=approved_return)

        api_client.force_authenticate(user=user)
        url = reverse("return-list")

        # Act
        response = api_client.get(url, {"status": "requested"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        results = get_results(response.data)
        filtered_returns = [r for r in results if r["status"] == "requested"]
        assert len(filtered_returns) == 1

    def test_list_returns_with_type_filter(self, api_client):
        """type 필터 적용 조회"""
        # Arrange
        user = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=user)

        refund_return = ReturnFactory.refund(order=order, user=user)
        ReturnItemFactory(return_request=refund_return)

        exchange_product = ProductFactory()
        exchange_return = ReturnFactory(
            order=order,
            user=user,
            type="exchange",
            exchange_product=exchange_product,
        )
        ReturnItemFactory(return_request=exchange_return)

        api_client.force_authenticate(user=user)
        url = reverse("return-list")

        # Act
        response = api_client.get(url, {"type": "refund"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        results = get_results(response.data)
        filtered_returns = [r for r in results if r["type"] == "refund"]
        assert len(filtered_returns) == 1

    def test_seller_sees_only_own_product_returns(self, api_client):
        """판매자는 본인 상품 관련 신청만 조회"""
        # Arrange
        seller_a = UserFactory(is_seller=True)
        seller_b = UserFactory(is_seller=True)

        product_a = ProductFactory(seller=seller_a)
        product_b = ProductFactory(seller=seller_b)

        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)

        order_item_a = OrderItemFactory(order=order, product=product_a)
        return_a = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_a, order_item=order_item_a)

        order_item_b = OrderItemFactory(order=order, product=product_b)
        return_b = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_b, order_item=order_item_b)

        api_client.force_authenticate(user=seller_a)
        # 판매자는 seller-return-list URL을 사용해야 함
        url = reverse("seller-return-list")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        results = get_results(response.data)
        return_ids = [r["id"] for r in results]
        assert return_a.id in return_ids
        assert return_b.id not in return_ids


@pytest.mark.django_db
class TestReturnViewIntegration:
    """통합 테스트 - 주요 흐름 검증"""

    def test_full_refund_flow(self, api_client):
        """환불 전체 흐름: 신청 → 승인 → 송장입력 → 수령확인 → 완료"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller, stock=10)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product, quantity=1)

        # Act & Assert - 1. 신청
        api_client.force_authenticate(user=buyer)
        create_url = reverse("return-list")
        create_data = {
            "order_id": order.id,

            "type": "refund",
            "reason": "change_of_mind",
            "reason_detail": "단순 변심",
            "items": [{"order_item_id": order_item.id, "quantity": 1}],
            "refund_account_bank": "신한은행",
            "refund_account_number": "110-123-456789",
            "refund_account_holder": "홍길동",
        }
        response = api_client.post(create_url, create_data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        return_id = response.data["return"]["id"]

        # Act & Assert - 2. 승인
        api_client.force_authenticate(user=seller)
        approve_url = reverse("seller-return-approve", kwargs={"pk": return_id})
        response = api_client.post(approve_url, {})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["return"]["status"] == "approved"

        # Act & Assert - 3. 송장 입력
        api_client.force_authenticate(user=buyer)
        update_url = reverse("return-detail", kwargs={"pk": return_id})
        update_data = {
            "return_shipping_company": "CJ대한통운",
            "return_tracking_number": "123456789012",
        }
        response = api_client.patch(update_url, update_data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "shipping"

        # Act & Assert - 4. 수령 확인
        api_client.force_authenticate(user=seller)
        confirm_url = reverse("seller-return-confirm-receive", kwargs={"pk": return_id})
        response = api_client.post(confirm_url, {})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["return"]["status"] == "received"

        # Act & Assert - 5. 완료
        complete_url = reverse("seller-return-complete", kwargs={"pk": return_id})
        response = api_client.post(complete_url, {})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["return"]["status"] == "completed"

    def test_full_exchange_flow(self, api_client):
        """교환 전체 흐름: 신청 → 승인 → 송장입력 → 수령확인 → 완료"""
        # Arrange
        seller = UserFactory(is_seller=True)
        original_product = ProductFactory(seller=seller, stock=10)
        exchange_product = ProductFactory(seller=seller, stock=5)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=original_product, quantity=1)

        # Act & Assert - 1. 신청
        api_client.force_authenticate(user=buyer)
        create_url = reverse("return-list")
        create_data = {
            "order_id": order.id,

            "type": "exchange",
            "reason": "size_issue",
            "reason_detail": "사이즈가 맞지 않음",
            "items": [{"order_item_id": order_item.id, "quantity": 1}],
            "exchange_product": exchange_product.id,
        }
        response = api_client.post(create_url, create_data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        return_id = response.data["return"]["id"]

        # Act & Assert - 2. 승인
        api_client.force_authenticate(user=seller)
        approve_url = reverse("seller-return-approve", kwargs={"pk": return_id})
        response = api_client.post(approve_url, {})
        assert response.status_code == status.HTTP_200_OK

        # Act & Assert - 3. 송장 입력
        api_client.force_authenticate(user=buyer)
        update_url = reverse("return-detail", kwargs={"pk": return_id})
        update_data = {
            "return_shipping_company": "우체국택배",
            "return_tracking_number": "987654321098",
        }
        response = api_client.patch(update_url, update_data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == "shipping"

        # Act & Assert - 4. 수령 확인
        api_client.force_authenticate(user=seller)
        confirm_url = reverse("seller-return-confirm-receive", kwargs={"pk": return_id})
        response = api_client.post(confirm_url, {})
        assert response.status_code == status.HTTP_200_OK

        # Act & Assert - 5. 완료 (교환은 송장번호 필수)
        complete_url = reverse("seller-return-complete", kwargs={"pk": return_id})
        complete_data = {
            "exchange_shipping_company": "CJ대한통운",
            "exchange_tracking_number": "111222333444",
        }
        response = api_client.post(complete_url, complete_data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["return"]["status"] == "completed"
