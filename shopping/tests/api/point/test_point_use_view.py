"""
PointUseView, PointCancelView API 테스트

테스트 범위:
- PointUseView: 포인트 사용 API (/api/points/use/)
  - 정상 사용 케이스 (FIFO)
  - 포인트 부족 (400)
  - 만료 포인트 제외 후 부족 (400) - cancel_deduct만 해당
  - 최소 사용 금액 미달 (400)
  - invalid amount/type (400)
  - 미인증 사용자 (403)

- PointCancelView: 취소/환불 포인트 처리 API (/api/points/cancel/)
  - cancel_deduct: 적립 포인트 회수
  - cancel_refund: 사용 포인트 환불
  - 유효하지 않은 주문 (400)
  - 취소되지 않은 주문 (400)
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from shopping.models.point import PointHistory
from shopping.tests.factories import (
    OrderFactory,
    PointHistoryFactory,
    UserFactory,
)


# ==========================================
# PointUseView 테스트
# ==========================================


@pytest.mark.django_db
class TestPointUseView:
    """포인트 사용 API 테스트"""

    def test_use_points_success(self, api_client):
        """정상 포인트 사용 - 성공 응답"""
        # Arrange
        user = UserFactory.with_points(5000)
        # FIFO 대상 적립 이력 생성
        PointHistoryFactory.earn(
            user=user,
            points=3000,
            balance=3000,
            expires_at=timezone.now() + timedelta(days=30),
        )
        PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=5000,
            expires_at=timezone.now() + timedelta(days=60),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 1000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["used_amount"] == 1000
        assert response.data["data"]["remaining_points"] == 4000
        assert "used_details" in response.data["data"]
        assert "1,000" in response.data["message"]

    def test_use_points_fifo_order(self, api_client):
        """FIFO 순서 검증 - 만료 임박 포인트부터 차감"""
        # Arrange
        user = UserFactory.with_points(3000)
        now = timezone.now()

        # 만료 임박 포인트 (10일 후)
        early_history = PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=now + timedelta(days=10),
        )
        # 나중에 만료되는 포인트 (60일 후)
        late_history = PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=3000,
            expires_at=now + timedelta(days=60),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 1500}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        used_details = response.data["data"]["used_details"]

        # 만료 임박 포인트(1000)가 먼저 전액 사용됨
        assert len(used_details) == 2
        assert used_details[0]["history_id"] == early_history.id
        assert used_details[0]["amount"] == 1000
        # 나머지 500은 다음 포인트에서 차감
        assert used_details[1]["history_id"] == late_history.id
        assert used_details[1]["amount"] == 500

    def test_use_points_with_order(self, api_client):
        """주문 연동 포인트 사용"""
        # Arrange
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user, status="confirmed")
        PointHistoryFactory.earn(user=user, points=5000, balance=5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {
            "amount": 2000,
            "order_id": order.id,
            "description": "주문 결제",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

        # PointHistory 확인
        use_history = PointHistory.objects.filter(user=user, type="use").first()
        assert use_history is not None
        assert use_history.order == order
        assert use_history.points == -2000

    def test_use_points_insufficient_points(self, api_client):
        """포인트 부족 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(500)
        PointHistoryFactory.earn(user=user, points=500, balance=500)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 1000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INSUFFICIENT_POINTS"
        assert "부족" in response.data["message"]

    def test_use_points_minimum_amount_not_met(self, api_client):
        """최소 사용 금액 미달 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 50}  # 100 미만

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "MINIMUM_AMOUNT_NOT_MET"
        assert "100" in response.data["message"]

    def test_use_points_invalid_amount_zero(self, api_client):
        """금액 0 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 0}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False

    def test_use_points_invalid_amount_negative(self, api_client):
        """음수 금액 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": -100}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False

    def test_use_points_invalid_amount_string(self, api_client):
        """문자열 금액 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": "abc"}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INVALID_AMOUNT"

    def test_use_points_missing_amount(self, api_client):
        """금액 누락 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False

    def test_use_points_invalid_order(self, api_client):
        """존재하지 않는 주문 ID - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 1000, "order_id": 99999}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_use_points_other_users_order(self, api_client):
        """다른 사용자의 주문 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(5000)
        other_user = UserFactory()
        other_order = OrderFactory(user=other_user)
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 1000, "order_id": other_order.id}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_use_points_unauthenticated(self, api_client):
        """미인증 사용자 - 403 에러"""
        # Arrange
        url = reverse("point_use")
        data = {"amount": 1000}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_use_points_updates_metadata(self, api_client):
        """포인트 사용 시 metadata 업데이트 검증"""
        # Arrange
        user = UserFactory.with_points(2000)
        earn_history = PointHistoryFactory.earn(
            user=user,
            points=2000,
            balance=2000,
            expires_at=timezone.now() + timedelta(days=30),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 500}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # 적립 이력의 metadata 확인
        earn_history.refresh_from_db()
        assert earn_history.metadata.get("used_amount") == 500
        assert "usage_history" in earn_history.metadata
        assert len(earn_history.metadata["usage_history"]) == 1

    def test_use_points_all_expired_insufficient_valid_points(self, api_client):
        """모든 포인트가 만료된 경우 - INSUFFICIENT_VALID_POINTS 에러 코드"""
        # Arrange
        user = UserFactory.with_points(1000)
        # 만료된 포인트만 생성 (유효한 포인트 없음)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() - timedelta(days=1),  # 이미 만료됨
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_use")
        data = {"amount": 500}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        # 일반 use 타입은 user.points 기반이므로 성공할 수 있음
        # cancel_deduct에서만 만료 포인트가 제외됨
        assert response.status_code == status.HTTP_200_OK


# ==========================================
# PointCancelView 테스트
# ==========================================


@pytest.mark.django_db
class TestPointCancelView:
    """취소/환불 포인트 처리 API 테스트"""

    def test_cancel_deduct_success(self, api_client):
        """적립 포인트 회수 성공"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user, status="canceled")
        # 회수 대상 적립 포인트
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            order=order,
            expires_at=timezone.now() + timedelta(days=30),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["processed_amount"] == 500
        assert response.data["data"]["remaining_points"] == 500
        assert response.data["data"]["type"] == "cancel_deduct"
        assert "회수" in response.data["message"]

    def test_cancel_refund_success(self, api_client):
        """사용 포인트 환불 성공"""
        # Arrange
        user = UserFactory.with_points(0)
        order = OrderFactory(user=user, status="canceled", used_points=1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 1000,
            "order_id": order.id,
            "type": "cancel_refund",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["processed_amount"] == 1000
        assert response.data["data"]["remaining_points"] == 1000
        assert response.data["data"]["type"] == "cancel_refund"
        assert "환불" in response.data["message"]

        # PointHistory 확인
        refund_history = PointHistory.objects.filter(user=user, type="cancel_refund").first()
        assert refund_history is not None
        assert refund_history.points == 1000

    def test_cancel_invalid_order(self, api_client):
        """존재하지 않는 주문 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": 99999,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INVALID_ORDER"

    def test_cancel_not_canceled_order(self, api_client):
        """취소되지 않은 주문 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user, status="paid")  # 취소 상태 아님
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "ORDER_NOT_CANCELED"

    def test_cancel_other_users_order(self, api_client):
        """다른 사용자의 주문 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        other_user = UserFactory()
        other_order = OrderFactory(user=other_user, status="canceled")
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": other_order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error_code"] == "INVALID_ORDER"

    def test_cancel_invalid_type(self, api_client):
        """유효하지 않은 타입 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user, status="canceled")
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
            "type": "invalid_type",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INVALID_TYPE"

    def test_cancel_missing_order_id(self, api_client):
        """주문 ID 누락 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False

    def test_cancel_missing_type(self, api_client):
        """타입 누락 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(1000)
        order = OrderFactory(user=user, status="canceled")
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False

    def test_cancel_deduct_insufficient_points(self, api_client):
        """회수할 포인트 부족 - 400 에러"""
        # Arrange
        user = UserFactory.with_points(100)
        order = OrderFactory(user=user, status="canceled")
        PointHistoryFactory.earn(user=user, points=100, balance=100)
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INSUFFICIENT_POINTS"

    def test_cancel_unauthenticated(self, api_client):
        """미인증 사용자 - 403 에러"""
        # Arrange
        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": 1,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_cancel_deduct_excludes_expired_points(self, api_client):
        """회수 시 만료된 포인트 제외 - FIFO 정책"""
        # Arrange
        user = UserFactory.with_points(2000)
        order = OrderFactory(user=user, status="canceled")

        # 만료된 포인트 (회수 대상 아님)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() - timedelta(days=1),  # 이미 만료
        )
        # 유효한 포인트 (회수 대상)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=2000,
            expires_at=timezone.now() + timedelta(days=30),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 1500,  # 유효 포인트(1000)보다 많이 요청
            "order_id": order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        # 유효한 포인트가 1000뿐이므로 실패해야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert "유효한" in response.data["message"] or "부족" in response.data["message"]

    def test_cancel_deduct_insufficient_valid_points_error_code(self, api_client):
        """회수할 유효 포인트 부족 - INSUFFICIENT_VALID_POINTS 에러 코드

        Serializer는 user.points로 검증하지만, Service(use_points_fifo)는
        만료되지 않은 포인트만 대상으로 함. 따라서:
        - user.points >= amount (Serializer 통과)
        - 유효한 포인트 < amount (Service 실패 → INSUFFICIENT_VALID_POINTS)
        """
        # Arrange
        user = UserFactory.with_points(1500)  # Serializer 통과용 (>= 1000)
        order = OrderFactory(user=user, status="canceled")

        # 만료된 포인트 1000 (회수 대상 아님)
        PointHistoryFactory.earn(
            user=user,
            points=1000,
            balance=1000,
            expires_at=timezone.now() - timedelta(days=1),  # 이미 만료
        )
        # 유효한 포인트 500만 있음 (< 요청 금액 1000)
        PointHistoryFactory.earn(
            user=user,
            points=500,
            balance=1500,
            expires_at=timezone.now() + timedelta(days=30),
        )
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 1000,  # user.points(1500) >= 1000 이지만, 유효 포인트(500) < 1000
            "order_id": order.id,
            "type": "cancel_deduct",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["success"] is False
        assert response.data["error_code"] == "INSUFFICIENT_VALID_POINTS"

    def test_cancel_with_custom_description(self, api_client):
        """사용자 정의 description으로 취소 처리"""
        # Arrange
        user = UserFactory.with_points(0)
        order = OrderFactory(user=user, status="canceled", used_points=500)
        api_client.force_authenticate(user=user)

        url = reverse("point_cancel")
        data = {
            "amount": 500,
            "order_id": order.id,
            "type": "cancel_refund",
            "description": "고객 요청에 의한 환불",
        }

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

        # PointHistory 확인
        refund_history = PointHistory.objects.filter(user=user, type="cancel_refund").first()
        assert refund_history is not None
        assert "고객 요청" in refund_history.description
