"""
point_serializers.py 단위 테스트

테스트 범위:
- PointHistorySerializer: validate_points, validate (type별 부호, balance 검증)
- PointUseSerializer: validate_order_id (None 반환)
- PointCheckSerializer: validate (order_amount 초과, 정상 사용 가능)

커버리지 대상 라인: 36-38, 42-62, 159, 275-276, 282
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from shopping.serializers.point_serializers import (
    PointCheckSerializer,
    PointHistorySerializer,
    PointUseSerializer,
)
from shopping.tests.factories import OrderFactory, UserFactory


# ==========================================
# PointHistorySerializer 테스트
# ==========================================


@pytest.mark.django_db
class TestPointHistorySerializer:
    """PointHistorySerializer 검증 테스트"""

    def test_validate_points_zero_raises_error(self):
        """포인트 변동량 0일 때 ValidationError 발생"""
        # Arrange
        serializer = PointHistorySerializer()

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_points(0)

        assert "0이 될 수 없습니다" in str(exc_info.value.detail[0])

    def test_validate_points_positive_passes(self):
        """양수 포인트 검증 통과"""
        # Arrange
        serializer = PointHistorySerializer()

        # Act
        result = serializer.validate_points(100)

        # Assert
        assert result == 100

    def test_validate_points_negative_passes(self):
        """음수 포인트 검증 통과"""
        # Arrange
        serializer = PointHistorySerializer()

        # Act
        result = serializer.validate_points(-100)

        # Assert
        assert result == -100

    def test_validate_earn_type_with_negative_points_raises_error(self):
        """적립(earn) 타입에 음수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn", "points": -100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "양수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_earn_type_with_zero_points_raises_error(self):
        """적립(earn) 타입에 0 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn", "points": 0}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "양수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_use_type_with_positive_points_raises_error(self):
        """사용(use) 타입에 양수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "use", "points": 100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_use_type_with_zero_points_raises_error(self):
        """사용(use) 타입에 0 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "use", "points": 0}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_cancel_refund_type_with_negative_points_raises_error(self):
        """취소환불(cancel_refund) 타입에 음수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "cancel_refund", "points": -100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "양수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_cancel_deduct_type_with_positive_points_raises_error(self):
        """취소차감(cancel_deduct) 타입에 양수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "cancel_deduct", "points": 100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_expire_type_with_positive_points_raises_error(self):
        """만료(expire) 타입에 양수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "expire", "points": 100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_admin_add_type_with_negative_points_raises_error(self):
        """관리자지급(admin_add) 타입에 음수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "admin_add", "points": -100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "양수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_admin_deduct_type_with_positive_points_raises_error(self):
        """관리자차감(admin_deduct) 타입에 양수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "admin_deduct", "points": 100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_event_type_with_negative_points_raises_error(self):
        """이벤트(event) 타입에 음수 포인트 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "event", "points": -100}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "양수 포인트" in str(exc_info.value.detail["points"])

    def test_validate_negative_balance_raises_error(self):
        """음수 잔액 - ValidationError"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn", "points": 100, "balance": -1}

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate(attrs)

        assert "음수가 될 수 없습니다" in str(exc_info.value.detail["balance"])

    def test_validate_valid_earn_type_passes(self):
        """적립(earn) 타입 정상 검증 통과"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn", "points": 100, "balance": 100}

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result == attrs

    def test_validate_valid_use_type_passes(self):
        """사용(use) 타입 정상 검증 통과"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "use", "points": -100, "balance": 0}

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result == attrs

    def test_validate_without_balance_passes(self):
        """balance 없이 검증 통과"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn", "points": 100}

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result == attrs

    def test_validate_without_points_passes(self):
        """points 없이 검증 통과 (None)"""
        # Arrange
        serializer = PointHistorySerializer()
        attrs = {"type": "earn"}

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result == attrs


# ==========================================
# PointUseSerializer 테스트
# ==========================================


@pytest.mark.django_db
class TestPointUseSerializerValidation:
    """PointUseSerializer 검증 테스트"""

    def test_validate_order_id_none_returns_none(self):
        """order_id가 None일 때 None 반환"""
        # Arrange
        user = UserFactory.with_points(5000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointUseSerializer(context={"request": mock_request})

        # Act
        result = serializer.validate_order_id(None)

        # Assert
        assert result is None

    def test_validate_order_id_valid_order_passes(self):
        """유효한 주문 ID 검증 통과"""
        # Arrange
        user = UserFactory.with_points(5000)
        order = OrderFactory(user=user)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointUseSerializer(context={"request": mock_request})

        # Act
        result = serializer.validate_order_id(order.id)

        # Assert
        assert result == order.id

    def test_validate_order_id_nonexistent_raises_error(self):
        """존재하지 않는 주문 ID - ValidationError"""
        # Arrange
        user = UserFactory.with_points(5000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointUseSerializer(context={"request": mock_request})

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_order_id(99999)

        assert "유효하지 않은 주문" in str(exc_info.value.detail[0])

    def test_validate_order_id_other_users_order_raises_error(self):
        """다른 사용자의 주문 ID - ValidationError"""
        # Arrange
        user = UserFactory.with_points(5000)
        other_user = UserFactory()
        other_order = OrderFactory(user=other_user)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointUseSerializer(context={"request": mock_request})

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_order_id(other_order.id)

        assert "유효하지 않은 주문" in str(exc_info.value.detail[0])


# ==========================================
# PointCheckSerializer 테스트
# ==========================================


@pytest.mark.django_db
class TestPointCheckSerializerValidation:
    """PointCheckSerializer 검증 테스트"""

    def test_validate_use_points_exceeds_order_amount(self):
        """사용 포인트가 주문 금액 초과 시 can_use=False"""
        # Arrange
        user = UserFactory.with_points(10000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("5000"),
            "use_points": 7000,  # 주문 금액 5000 초과
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["can_use"] is False
        assert "주문 금액을 초과" in result["result"]["message"]

    def test_validate_use_points_valid_returns_can_use_true(self):
        """정상 포인트 사용 시 can_use=True"""
        # Arrange
        user = UserFactory.with_points(5000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("10000"),
            "use_points": 3000,  # 보유 5000, 주문금액 10000 이내
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["can_use"] is True
        assert "3000포인트 사용 가능" in result["result"]["message"]
        assert result["result"]["available_points"] == 5000
        assert result["result"]["max_usable"] == 5000

    def test_validate_use_points_zero_returns_can_use_true(self):
        """포인트 0 사용 시 can_use=True"""
        # Arrange
        user = UserFactory.with_points(5000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("10000"),
            "use_points": 0,
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["can_use"] is True
        assert "사용하지 않습니다" in result["result"]["message"]

    def test_validate_use_points_below_minimum_returns_can_use_false(self):
        """100 미만 사용 시 can_use=False"""
        # Arrange
        user = UserFactory.with_points(5000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("10000"),
            "use_points": 50,  # 100 미만
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["can_use"] is False
        assert "100포인트 이상" in result["result"]["message"]

    def test_validate_use_points_exceeds_user_points_returns_can_use_false(self):
        """보유 포인트 초과 시 can_use=False"""
        # Arrange
        user = UserFactory.with_points(1000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("10000"),
            "use_points": 5000,  # 보유 1000 초과
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["can_use"] is False
        assert "부족합니다" in result["result"]["message"]

    def test_validate_max_usable_limited_by_user_points(self):
        """max_usable이 보유 포인트로 제한"""
        # Arrange
        user = UserFactory.with_points(3000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("10000"),
            "use_points": 2000,
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["max_usable"] == 3000  # 보유 포인트가 주문금액보다 적음

    def test_validate_max_usable_limited_by_order_amount(self):
        """max_usable이 주문 금액으로 제한"""
        # Arrange
        user = UserFactory.with_points(10000)
        mock_request = MagicMock()
        mock_request.user = user

        serializer = PointCheckSerializer(context={"request": mock_request})
        attrs = {
            "order_amount": Decimal("5000"),
            "use_points": 3000,
        }

        # Act
        result = serializer.validate(attrs)

        # Assert
        assert result["result"]["max_usable"] == 5000  # 주문금액이 보유 포인트보다 적음
