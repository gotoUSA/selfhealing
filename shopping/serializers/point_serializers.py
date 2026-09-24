from __future__ import annotations

from typing import Any

from rest_framework import serializers

from ..models.point import PointHistory
from ..models.user import User


class PointHistorySerializer(serializers.ModelSerializer):
    """포인트 이력 조회용 시리얼라이저"""

    type_display = serializers.CharField(source="get_type_display", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True, allow_null=True)

    class Meta:
        model = PointHistory
        fields = [
            "id",
            "points",
            "balance",
            "type",
            "type_display",
            "order",
            "order_number",
            "description",
            "expires_at",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["balance", "created_at"]

    def validate_points(self, value: int) -> int:
        """포인트 변동량 검증"""
        if value == 0:
            raise serializers.ValidationError("포인트 변동량은 0이 될 수 없습니다.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """포인트 이력 데이터 검증 - Model의 clean() 로직 반영"""
        points = attrs.get("points")
        point_type = attrs.get("type")
        balance = attrs.get("balance")

        # type별 points 부호 검증
        positive_types = {"earn", "cancel_refund", "admin_add", "event"}
        negative_types = {"use", "cancel_deduct", "admin_deduct", "expire"}

        if point_type in positive_types and points is not None and points <= 0:
            type_display = dict(PointHistory.TYPE_CHOICES).get(point_type, point_type)
            raise serializers.ValidationError({"points": f"{type_display}는 양수 포인트여야 합니다."})

        if point_type in negative_types and points is not None and points >= 0:
            type_display = dict(PointHistory.TYPE_CHOICES).get(point_type, point_type)
            raise serializers.ValidationError({"points": f"{type_display}는 음수 포인트여야 합니다."})

        # 잔액 검증 (balance가 제공된 경우)
        if balance is not None and balance < 0:
            raise serializers.ValidationError({"balance": "잔액은 음수가 될 수 없습니다."})

        return attrs


class UserPointSerializer(serializers.ModelSerializer):
    """사용자 포인트 정보 조회용 시리얼라이저"""

    total_earned = serializers.SerializerMethodField()
    total_used = serializers.SerializerMethodField()
    expiring_soon = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "points",
            "total_earned",
            "total_used",
            "expiring_soon",
        ]
        read_only_fields = ["points"]

    def get_total_earned(self, obj: User) -> int:
        """총 적립 포인트 - DB aggregate 사용"""
        from ..models.point import PointHistory

        return PointHistory.objects.get_total_earned(obj)

    def get_total_used(self, obj: User) -> int:
        """총 사용 포인트 - DB aggregate 사용"""
        from ..models.point import PointHistory

        return PointHistory.objects.get_total_used(obj)

    def get_expiring_soon(self, obj: User) -> int:
        """30일 내 만료 예정 포인트 - DB aggregate 사용"""
        from ..models.point import PointHistory

        return PointHistory.objects.get_expiring_soon(obj, days=30)


class PointCheckSerializer(serializers.Serializer):
    """포인트 사용 가능 여부 확인 시리얼라이저"""

    order_amount = serializers.DecimalField(max_digits=10, decimal_places=0, help_text="주문 금액")
    use_points = serializers.IntegerField(min_value=0, help_text="사용하려는 포인트")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """포인트 사용 가능 여부 검증"""
        user = self.context["request"].user
        order_amount = attrs["order_amount"]
        use_points = attrs["use_points"]

        result = {
            "available_points": user.points,
            "can_use": False,
            "max_usable": 0,
            "message": "",
        }

        # 최대 사용 가능 포인트 (주문 금액의 100%)
        max_usable = min(user.points, int(order_amount))
        result["max_usable"] = max_usable

        if use_points == 0:
            result["can_use"] = True
            result["message"] = "포인트를 사용하지 않습니다."
        elif use_points < 100:
            result["message"] = "최소 100포인트 이상 사용 가능합니다."
        elif use_points > user.points:
            result["message"] = f"보유 포인트가 부족합니다. (보유: {user.points}P)"
        elif use_points > order_amount:
            result["message"] = "주문 금액을 초과하여 사용할 수 없습니다."
        else:
            result["can_use"] = True
            result["message"] = f"{use_points}포인트 사용 가능합니다."

        attrs["result"] = result
        return attrs
