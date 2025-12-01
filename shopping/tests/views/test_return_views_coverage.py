"""
ReturnViewSet 커버리지 보완 테스트

커버되지 않은 라인:
- 53: retrieve serializer 선택
- 59: reject serializer 선택
- 64: complete serializer 선택
- 118: destroy 권한 확인 (본인 확인)
- 125: destroy 상태 확인 (requested만 취소 가능)
- 161-171: reject 액션 권한 체크
- 185: confirm_receive 액션 권한 체크
- 204-215: complete 액션 권한 체크
- 232: refund 완료 메시지
- 273-274: complete 예외 처리
"""

from unittest.mock import patch

from django.urls import reverse

import pytest
from rest_framework import status

from shopping.tests.factories import (
    OrderFactory,
    OrderItemFactory,
    ProductFactory,
    ReturnFactory,
    ReturnItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestReturnRetrieveAction:
    """교환/환불 상세 조회 테스트 - Line 53 커버"""

    def test_retrieve_returns_detail_with_correct_serializer(self, api_client):
        """상세 조회 시 ReturnDetailSerializer 사용"""
        # Arrange
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        return_obj = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj)

        api_client.force_authenticate(user=buyer)
        url = reverse("return-detail", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "return_items" in response.data
        assert "can_cancel" in response.data
        assert "order_info" in response.data


@pytest.mark.django_db
class TestReturnRejectAction:
    """교환/환불 거부 테스트 - Lines 59, 161-171 커버"""

    def test_reject_by_non_seller_returns_404(self, api_client):
        """비판매자가 거부 시도 시 404 (queryset 필터링)"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        non_seller = UserFactory(is_seller=False)
        api_client.force_authenticate(user=non_seller)
        url = reverse("seller-return-reject", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {"rejected_reason": "테스트 거부"})

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_reject_other_seller_product_returns_403(self, api_client):
        """타 판매자 상품 거부 시도 시 403"""
        # Arrange
        seller_a = UserFactory(is_seller=True)
        seller_b = UserFactory(is_seller=True)
        product_a = ProductFactory(seller=seller_a)
        product_b = ProductFactory(seller=seller_b)

        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)

        # 두 판매자 상품이 모두 포함된 Return
        order_item_a = OrderItemFactory(order=order, product=product_a)
        order_item_b = OrderItemFactory(order=order, product=product_b)
        return_obj = ReturnFactory(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item_a)
        ReturnItemFactory(return_request=return_obj, order_item=order_item_b)

        # seller_a가 접근 (seller_b 상품도 포함되어 있어 권한 없음)
        api_client.force_authenticate(user=seller_a)
        url = reverse("seller-return-reject", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {"rejected_reason": "테스트 거부"})

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "본인 상품" in response.data.get("message", "")

    def test_reject_success_returns_200(self, api_client):
        """정상 거부 처리 성공"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer, status="requested")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        api_client.force_authenticate(user=seller)
        url = reverse("seller-return-reject", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {"rejected_reason": "상품 하자 아님"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["return"]["status"] == "rejected"


@pytest.mark.django_db
class TestReturnConfirmReceiveAction:
    """반품 도착 확인 테스트 - Line 185 커버"""

    def test_confirm_receive_by_non_seller_returns_404(self, api_client):
        """비판매자가 수령확인 시도 시 404"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory.shipping(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        non_seller = UserFactory(is_seller=False)
        api_client.force_authenticate(user=non_seller)
        url = reverse("seller-return-confirm-receive", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_confirm_receive_other_seller_returns_403(self, api_client):
        """타 판매자 상품 수령확인 시 403"""
        # Arrange
        seller_a = UserFactory(is_seller=True)
        seller_b = UserFactory(is_seller=True)
        product_a = ProductFactory(seller=seller_a)
        product_b = ProductFactory(seller=seller_b)

        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item_a = OrderItemFactory(order=order, product=product_a)
        order_item_b = OrderItemFactory(order=order, product=product_b)
        return_obj = ReturnFactory.shipping(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj, order_item=order_item_a)
        ReturnItemFactory(return_request=return_obj, order_item=order_item_b)

        api_client.force_authenticate(user=seller_a)
        url = reverse("seller-return-confirm-receive", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestReturnCompleteAction:
    """교환/환불 완료 처리 테스트 - Lines 64, 204-215, 232, 273-274 커버"""

    def test_complete_by_non_seller_returns_404(self, api_client):
        """비판매자가 완료처리 시도 시 404"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory.received(order=order, user=buyer, type="refund")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        non_seller = UserFactory(is_seller=False)
        api_client.force_authenticate(user=non_seller)
        url = reverse("seller-return-complete", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_complete_other_seller_returns_403(self, api_client):
        """타 판매자 상품 완료처리 시 403"""
        # Arrange
        seller_a = UserFactory(is_seller=True)
        seller_b = UserFactory(is_seller=True)
        product_a = ProductFactory(seller=seller_a)
        product_b = ProductFactory(seller=seller_b)

        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item_a = OrderItemFactory(order=order, product=product_a)
        order_item_b = OrderItemFactory(order=order, product=product_b)
        return_obj = ReturnFactory.received(order=order, user=buyer, type="refund")
        ReturnItemFactory(return_request=return_obj, order_item=order_item_a)
        ReturnItemFactory(return_request=return_obj, order_item=order_item_b)

        api_client.force_authenticate(user=seller_a)
        url = reverse("seller-return-complete", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_complete_refund_success_returns_200(self, api_client):
        """환불 완료 처리 성공 - '환불이 완료되었습니다' 메시지"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory.received(order=order, user=buyer, type="refund")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        api_client.force_authenticate(user=seller)
        url = reverse("seller-return-complete", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "환불" in response.data.get("message", "")
        assert response.data["return"]["status"] == "completed"

    def test_complete_exchange_success_returns_200(self, api_client):
        """교환 완료 처리 성공 - '교환 상품이 발송되었습니다' 메시지"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        exchange_product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory.received(
            order=order,
            user=buyer,
            type="exchange",
            exchange_product=exchange_product,
        )
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        api_client.force_authenticate(user=seller)
        url = reverse("seller-return-complete", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(
            url,
            {
                "exchange_shipping_company": "CJ대한통운",
                "exchange_tracking_number": "123456789012",
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "교환" in response.data.get("message", "")

    def test_complete_with_service_exception_returns_400(self, api_client):
        """완료 처리 중 예외 발생 시 400"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory.received(order=order, user=buyer, type="refund")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        api_client.force_authenticate(user=seller)
        url = reverse("seller-return-complete", kwargs={"pk": return_obj.id})

        # Act
        with patch(
            "shopping.services.return_service.ReturnService.complete_refund",
            side_effect=Exception("환불 처리 중 오류"),
        ):
            response = api_client.post(url, {})

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "오류" in response.data.get("message", "")


@pytest.mark.django_db
class TestReturnDestroyValidation:
    """교환/환불 삭제 유효성 검사 테스트 - Lines 118, 125 커버"""

    def test_destroy_by_seller_on_buyer_return_returns_404(self, api_client):
        """판매자가 구매자의 Return 삭제 시도 시 404 (queryset 필터링)"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer, status="requested")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        # 판매자로 인증 - 고객용 ViewSet은 user=user로 필터링하므로
        # 판매자는 자신이 신청하지 않은 Return을 조회할 수 없음
        api_client.force_authenticate(user=seller)
        url = reverse("return-detail", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.delete(url)

        # Assert - queryset에서 조회되지 않으므로 404 반환
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_destroy_approved_status_returns_400(self, api_client):
        """approved 상태의 Return 삭제 시도 시 400"""
        # Arrange
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        return_obj = ReturnFactory.approved(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj)

        api_client.force_authenticate(user=buyer)
        url = reverse("return-detail", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "신청 상태" in response.data.get("message", "")

    def test_destroy_shipping_status_returns_400(self, api_client):
        """shipping 상태의 Return 삭제 시도 시 400"""
        # Arrange
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        return_obj = ReturnFactory.shipping(order=order, user=buyer)
        ReturnItemFactory(return_request=return_obj)

        api_client.force_authenticate(user=buyer)
        url = reverse("return-detail", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_destroy_requested_status_success(self, api_client):
        """requested 상태의 Return 삭제 성공"""
        # Arrange
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        return_obj = ReturnFactory(order=order, user=buyer, status="requested")
        ReturnItemFactory(return_request=return_obj)

        api_client.force_authenticate(user=buyer)
        url = reverse("return-detail", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.delete(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "취소" in response.data.get("message", "")


@pytest.mark.django_db
class TestReturnSellerPermissionCheck:
    """판매자 권한 확인 헬퍼 테스트 - _check_seller_permission 커버"""

    def test_non_seller_user_returns_permission_denied(self, api_client):
        """비판매자가 판매자 액션 시도 시 404 (queryset 필터링)"""
        # Arrange
        seller = UserFactory(is_seller=True)
        product = ProductFactory(seller=seller)
        buyer = UserFactory(is_seller=False)
        order = OrderFactory.delivered(user=buyer)
        order_item = OrderItemFactory(order=order, product=product)
        return_obj = ReturnFactory(order=order, user=buyer, status="requested")
        ReturnItemFactory(return_request=return_obj, order_item=order_item)

        # buyer가 자신이 신청한 Return에 approve 시도
        # SellerReturnViewSet의 get_queryset에서 비판매자는 빈 queryset 반환 → 404
        api_client.force_authenticate(user=buyer)
        url = reverse("seller-return-approve", kwargs={"pk": return_obj.id})

        # Act
        response = api_client.post(url, {})

        # Assert - queryset에서 조회되지 않으므로 404 반환
        assert response.status_code == status.HTTP_404_NOT_FOUND
