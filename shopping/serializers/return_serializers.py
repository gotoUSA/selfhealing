from __future__ import annotations

from typing import Any

from django.db import transaction

from rest_framework import serializers

from shopping.models import Order, OrderItem, Return, ReturnItem
from shopping.services.return_service import ReturnService, ReturnValidationError


class ReturnItemSerializer(serializers.ModelSerializer):
    """반품 상품 항목 Serializer"""

    order_item_id = serializers.IntegerField(write_only=True)
    subtotal = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ReturnItem
        fields = [
            "id",
            "order_item_id",
            "quantity",
            "product_name",
            "product_price",
            "subtotal",
        ]
        read_only_fields = ["id", "product_name", "product_price", "subtotal"]

    def get_subtotal(self, obj: ReturnItem) -> str:
        """반품 금액 계산"""
        return obj.get_subtotal()


class ReturnCreateSerializer(serializers.ModelSerializer):
    """교환/환불 신청 Serializer"""

    items = ReturnItemSerializer(many=True, write_only=True, source="return_items")
    order_id = serializers.IntegerField(write_only=True, required=False, help_text="주문 ID")

    class Meta:
        model = Return
        fields = [
            "order_id",  # body에서 order_id를 받을 수 있도록 추가
            "type",
            "reason",
            "reason_detail",
            "items",
            # 환불 정보 (type='refund'일 때만)
            "refund_account_bank",
            "refund_account_number",
            "refund_account_holder",
            # 교환 정보 (type='exchange'일 때만)
            "exchange_product",
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """데이터 유효성 검증"""
        request = self.context.get("request")
        # context에서 order_id를 먼저 확인하고, 없으면 body에서 확인
        order_id = self.context.get("order_id") or attrs.get("order_id")

        if not order_id:
            raise serializers.ValidationError("주문 ID가 필요합니다.")

        # 주문 존재 여부 확인
        try:
            order = Order.objects.get(id=order_id, user=request.user)
        except Order.DoesNotExist:
            raise serializers.ValidationError("주문을 찾을 수 없습니다.")

        # 비즈니스 규칙 검증 (Service Layer 위임)
        try:
            ReturnService.validate_order_for_return(order, request.user)
        except ReturnValidationError as e:
            raise serializers.ValidationError(str(e))

        # 환불인 경우 계좌 정보 필수 (데이터 형식 검증)
        if attrs["type"] == "refund":
            if not attrs.get("refund_account_bank") or not attrs.get("refund_account_number"):
                raise serializers.ValidationError("환불 계좌 정보를 입력해주세요.")

        # 반품 상품 검증 (Service Layer 위임)
        return_items = attrs.get("return_items", [])
        try:
            ReturnService.validate_return_items(order, return_items)
        except ReturnValidationError as e:
            raise serializers.ValidationError(str(e))

        attrs["order"] = order
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Return:
        """교환/환불 신청 생성"""
        from shopping.services import ReturnService

        request = self.context.get("request")
        return_items_data = validated_data.pop("return_items", [])
        order = validated_data.pop("order")

        # ReturnItem 데이터 준비
        return_items_list = []
        for item_data in return_items_data:
            order_item_id = item_data.pop("order_item_id")
            order_item = OrderItem.objects.get(id=order_item_id)

            return_items_list.append(
                {
                    "order_item": order_item,
                    "quantity": item_data["quantity"],
                    "product_name": order_item.product_name,
                    "product_price": order_item.price,
                }
            )

        # ReturnService를 통해 생성
        with transaction.atomic():
            try:
                return_obj = ReturnService.create_return(
                    order=order,
                    user=request.user,
                    type=validated_data["type"],
                    reason=validated_data["reason"],
                    reason_detail=validated_data["reason_detail"],
                    return_items_data=return_items_list,
                    refund_account_bank=validated_data.get("refund_account_bank", ""),
                    refund_account_number=validated_data.get("refund_account_number", ""),
                    refund_account_holder=validated_data.get("refund_account_holder", ""),
                    exchange_product=validated_data.get("exchange_product"),
                )
            except ValueError as e:
                # Service 레이어의 비즈니스 로직 에러를 ValidationError로 변환
                raise serializers.ValidationError(str(e))

            # 판매자에게 알림 발송
            from shopping.models import Notification

            # 반품하는 상품들의 판매자 찾기
            sellers = set()
            for return_item in return_obj.return_items.all():
                if return_item.order_item.product and return_item.order_item.product.seller:
                    sellers.add(return_item.order_item.product.seller)

            # 각 판매자에게 알림
            for seller in sellers:
                Notification.objects.create(
                    user=seller,  # 판매자에게 알림
                    notification_type="return",
                    title=f"새로운 {return_obj.get_type_display()} 신청",
                    message=f"{return_obj.return_number} - {return_obj.get_reason_display()}",
                    link=f"/returns/{return_obj.id}",
                    metadata={"return_id": return_obj.id, "return_number": return_obj.return_number},
                )

        return return_obj


class ReturnListSerializer(serializers.ModelSerializer):
    """교환/환불 목록 Serializer"""

    type_display = serializers.CharField(source="get_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    item_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Return
        fields = [
            "id",
            "return_number",
            "type",
            "type_display",
            "status",
            "status_display",
            "reason",
            "reason_display",
            "order_number",
            "refund_amount",
            "item_count",
            "created_at",
        ]

    def get_item_count(self, obj: Return) -> int:
        """
        반품 상품 개수

        성능 최적화: prefetch_related된 데이터 사용
        count()는 DB 쿼리를 유발할 수 있으므로 len() 사용
        """
        return len(obj.return_items.all())


class ReturnDetailSerializer(serializers.ModelSerializer):
    """교환/환불 상세 Serializer"""

    type_display = serializers.CharField(source="get_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)

    order_info = serializers.SerializerMethodField(read_only=True)
    return_items = ReturnItemSerializer(many=True, read_only=True)
    exchange_product_info = serializers.SerializerMethodField(read_only=True)
    refund_account_number = serializers.SerializerMethodField(read_only=True)

    # 액션 가능 여부
    can_cancel = serializers.SerializerMethodField(read_only=True)
    can_update_tracking = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Return
        fields = [
            "id",
            "return_number",
            "type",
            "type_display",
            "status",
            "status_display",
            "reason",
            "reason_display",
            "reason_detail",
            # 주문 정보
            "order_info",
            "return_items",
            # 반품 배송 정보
            "return_shipping_company",
            "return_tracking_number",
            "return_shipping_fee",
            # 환불 정보
            "refund_amount",
            "refund_method",
            "refund_account_bank",
            "refund_account_number",
            "refund_account_holder",
            # 교환 정보
            "exchange_product_info",
            "exchange_shipping_company",
            "exchange_tracking_number",
            # 처리 정보
            "rejected_reason",
            "approved_at",
            "completed_at",
            # 타임스탬프
            "created_at",
            "updated_at",
            # 액션 가능 여부
            "can_cancel",
            "can_update_tracking",
        ]

    def get_order_info(self, obj: Return) -> dict[str, Any]:
        """주문 정보"""
        return {
            "id": obj.order.id,
            "order_number": obj.order.order_number,
            "total_amount": obj.order.total_amount,
            "created_at": obj.order.created_at,
        }

    def get_refund_account_number(self, obj: Return) -> str:
        """
        환불 계좌번호 (마스킹 처리)

        보안을 위해 마스킹된 계좌번호를 반환합니다.
        예: "***-***-6789"
        """
        return obj.get_masked_account_number()

    def get_exchange_product_info(self, obj: Return) -> dict[str, Any] | None:
        """교환 상품 정보"""
        if obj.type != "exchange" or not obj.exchange_product:
            return None

        product = obj.exchange_product
        return {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "stock": product.stock,
        }

    def get_can_cancel(self, obj: Return) -> bool:
        """신청 취소 가능 여부"""
        return obj.status == "requested"

    def get_can_update_tracking(self, obj: Return) -> bool:
        """송장번호 입력 가능 여부"""
        return obj.status == "approved"


class ReturnUpdateSerializer(serializers.ModelSerializer):
    """송장번호 업데이트 Serializer (고객)"""

    status = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Return
        fields = [
            "return_shipping_company",
            "return_tracking_number",
            "status",
            "status_display",
        ]
        read_only_fields = ["status", "status_display"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """송장번호 입력 가능 상태인지 확인"""
        if self.instance.status != "approved":
            raise serializers.ValidationError("승인된 신청만 송장번호를 입력할 수 있습니다.")

        if not attrs.get("return_shipping_company") or not attrs.get("return_tracking_number"):
            raise serializers.ValidationError("택배사와 송장번호를 모두 입력해주세요.")

        return attrs

    def update(self, instance: Return, validated_data: dict[str, Any]) -> Return:
        """송장번호 업데이트 및 상태 변경"""
        instance.return_shipping_company = validated_data["return_shipping_company"]
        instance.return_tracking_number = validated_data["return_tracking_number"]
        instance.status = "shipping"  # 상태를 '반품배송중'으로 변경
        instance.save()

        # 판매자에게 알림
        from shopping.models import Notification

        # 성능 최적화: select_related로 N+1 쿼리 방지
        sellers = set()
        for return_item in instance.return_items.select_related("order_item__product__seller").all():
            if return_item.order_item.product and return_item.order_item.product.seller:
                sellers.add(return_item.order_item.product.seller)

        # 각 판매자에게 알림
        for seller in sellers:
            Notification.objects.create(
                user=seller,  # 판매자에게 알림
                notification_type="return",
                title="반품 상품 발송",
                message=f"{instance.return_number} - 고객이 반품 상품을 발송했습니다. 송장번호: {instance.return_tracking_number}",
                link=f"/returns/{instance.id}",
                metadata={
                    "return_id": instance.id,
                    "return_number": instance.return_number,
                    "tracking_number": instance.return_tracking_number,
                },
            )
        return instance


class ReturnApproveSerializer(serializers.Serializer):
    """승인 Serializer (판매자)"""

    admin_memo = serializers.CharField(required=False, allow_blank=True, help_text="관리자 메모")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """승인 가능 상태인지 확인"""
        return_obj = self.context.get("return_obj")

        if return_obj.status != "requested":
            raise serializers.ValidationError("신청 상태에서만 승인할 수 있습니다.")

        return attrs

    def save(self) -> Return:
        """승인 처리"""
        from shopping.services.return_service import ReturnService

        return_obj = self.context.get("return_obj")
        admin_memo = self.validated_data.get("admin_memo", "")

        return ReturnService.approve_return(return_obj, admin_user=None, admin_memo=admin_memo)  # 향후 request.user 전달 가능


class ReturnRejectSerializer(serializers.Serializer):
    """거부 Serializer (판매자)"""

    rejected_reason = serializers.CharField(required=True, help_text="거부 사유")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """거부 가능 상태인지 확인"""
        return_obj = self.context.get("return_obj")

        if return_obj.status != "requested":
            raise serializers.ValidationError("신청 상태에서만 거부할 수 있습니다.")

        return attrs

    def save(self) -> Return:
        """거부 처리"""
        from shopping.services.return_service import ReturnService

        return_obj = self.context.get("return_obj")
        rejected_reason = self.validated_data["rejected_reason"]

        return ReturnService.reject_return(return_obj, reason=rejected_reason)


class ReturnConfirmReceiveSerializer(serializers.Serializer):
    """반품 도착 확인 Serializer (판매자)"""

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """수령 확인 가능 상태인지 확인"""
        return_obj = self.context.get("return_obj")

        if return_obj.status != "shipping":
            raise serializers.ValidationError("배송 중 상태에서만 수령 확인할 수 있습니다.")

        return attrs

    def save(self) -> Return:
        """수령 확인 처리"""
        from shopping.services.return_service import ReturnService

        return_obj = self.context.get("return_obj")
        return ReturnService.confirm_receive_return(return_obj)


class ReturnCompleteSerializer(serializers.Serializer):
    """완료 처리 Serializer (판매자)"""

    # 교환인 경우 교환 상품 송장번호 필수
    exchange_tracking_number = serializers.CharField(
        required=False, allow_blank=True, help_text="교환 상품 송장번호 (교환인 경우 필수)"
    )
    exchange_shipping_company = serializers.CharField(
        required=False, allow_blank=True, help_text="교환 상품 택배사 (교환인 경우 필수)"
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """완료 처리 가능 상태인지 확인"""
        return_obj = self.context.get("return_obj")

        if return_obj.status != "received":
            raise serializers.ValidationError("반품 도착 상태에서만 완료 처리할 수 있습니다.")

        # 교환인 경우 송장번호 필수
        if return_obj.type == "exchange":
            if not attrs.get("exchange_tracking_number") or not attrs.get("exchange_shipping_company"):
                raise serializers.ValidationError("교환 상품의 택배사와 송장번호를 입력해주세요.")

        return attrs

    def save(self) -> Return:
        """완료 처리 (환불 또는 교환)"""
        from shopping.services.return_service import ReturnService

        return_obj = self.context.get("return_obj")

        if return_obj.type == "refund":
            # 환불 처리
            return ReturnService.complete_refund(return_obj)
        else:
            # 교환 처리
            exchange_tracking_number = self.validated_data["exchange_tracking_number"]
            exchange_shipping_company = self.validated_data["exchange_shipping_company"]

            return ReturnService.complete_exchange(
                return_obj,
                exchange_tracking_number=exchange_tracking_number,
                exchange_shipping_company=exchange_shipping_company,
            )
