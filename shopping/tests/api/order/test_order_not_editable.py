"""
주문은 API로 고칠 수 없다 — 상태·금액은 생성·취소·결제 흐름으로만 바뀐다

2026-09-25 실측(수정 전): 고객이 자기 주문에 PATCH /api/orders/{id}/ {"final_amount": "0"} → 200,
이어서 POST /api/payments/points-only/ → 200 "포인트 전액 결제가 완료되었습니다" — 30,000원 주문이
쓴 포인트 0·결제 0원으로 paid 가 됐다. 같은 PATCH 로 status·total_amount·earned_points 도 바뀌었다.
주문 API가 ModelViewSet 이었고, 스키마 설명은 "관리자만"이었지만 권한 클래스는 주문 주인도 통과시켰다.
"""

from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from shopping.models.order import Order
from shopping.models.payment import Payment
from shopping.models.user import User

WATCHED = ("status", "total_amount", "final_amount", "used_points", "earned_points")
TAMPER = {
    "status": "paid",
    "final_amount": "0",
    "total_amount": "1",
    "earned_points": 99999,
}


def _snapshot(order: Order) -> dict:
    return Order.objects.filter(pk=order.pk).values(*WATCHED).get()


@pytest.mark.django_db
class TestOrderHasNoWriteApi:
    """주문 수정·삭제 API는 없다 (405)"""

    @pytest.mark.parametrize("method", ["patch", "put", "delete"])
    def test_owner_cannot_change_or_delete_the_order(self, authenticated_client, order, method):
        before = _snapshot(order)

        if method == "delete":
            response = authenticated_client.delete(f"/api/orders/{order.id}/")
        else:
            response = getattr(authenticated_client, method)(f"/api/orders/{order.id}/", TAMPER, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert _snapshot(order) == before

    def test_staff_cannot_patch_through_the_api_either(self, order):
        """주문을 손으로 고치는 건 Django 관리자 화면에서 — API에는 수정 경로가 없다"""
        staff = User.objects.create_user(
            username="order_staff",
            email="order_staff@test.com",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=staff)
        before = _snapshot(order)

        response = client.patch(f"/api/orders/{order.id}/", TAMPER, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert _snapshot(order) == before


@pytest.mark.django_db
class TestPointsOnlyPaymentNeedsCoveringPoints:
    """포인트 전액 결제는 final_amount 한 칸이 아니라 쓴 포인트가 상품·배송비를 덮는지로 판단한다"""

    def test_zero_final_amount_without_points_is_refused(self, authenticated_client, order):
        # 어떤 경로로든 final_amount 만 0 이 된 주문 (쓴 포인트 0)
        Order.objects.filter(pk=order.pk).update(final_amount=Decimal("0"))

        response = authenticated_client.post("/api/payments/points-only/", {"order_id": order.id}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        order.refresh_from_db()
        assert order.status == "confirmed"
        assert not Payment.objects.filter(order=order, status="done").exists()
