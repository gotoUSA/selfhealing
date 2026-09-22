from rest_framework import serializers

from ..models.order import Order
from ..models.payment import Payment, PaymentLog


class PaymentSerializer(serializers.ModelSerializer):
    """
    결제 정보 조회용 시리얼라이저
    """

    order_number = serializers.CharField(
        source="order.order_number",
        read_only=True,
    )

    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    used_points = serializers.IntegerField(
        source="order.used_points",
        read_only=True,
    )

    earned_points = serializers.IntegerField(
        source="order.earned_points",
        read_only=True,
    )

    class Meta:
        model = Payment
        fields = [
            "id",
            "order",
            "order_number",
            "payment_key",
            "order_id",
            "amount",
            "used_points",
            "earned_points",
            "method",
            "card_company",
            "card_number",
            "installment_plan_months",
            "status",
            "status_display",
            "approved_at",
            "receipt_url",
            "virtual_account_bank_code",
            "virtual_account_number",
            "virtual_account_due_date",
            "is_canceled",
            "canceled_amount",
            "cancel_reason",
            "canceled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "payment_key",
            "approved_at",
            "receipt_url",
            "virtual_account_bank_code",
            "virtual_account_number",
            "virtual_account_due_date",
            "canceled_at",
        ]


class PaymentRequestSerializer(serializers.Serializer):
    """
    결제 요청용 시리얼라이저
    프론트엔드에서 결제창 열기 전 필요한 정보
    """

    order_id = serializers.IntegerField(help_text="주문 ID")
    payment_method = serializers.CharField(
        max_length=50,
        default="card",
        help_text="결제 수단 (card, bank_transfer 등)",
    )
    idempotency_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="멱등성 키 (클라이언트가 생성, 중복 결제 방지)",
    )

    def validate_order_id(self, value):
        """주문 검증"""
        user = self.context["request"].user

        try:
            # 관리자 권한 체크
            if user.is_staff or user.is_superuser:
                # 관리자는 모든 주문 접근 가능
                order = Order.objects.get(id=value)
            else:
                # 일반 사용자는 본인 주문만 접근 가능
                order = Order.objects.get(id=value, user=user)
        except Order.DoesNotExist:
            raise serializers.ValidationError("주문을 찾을 수 없습니다.")

        # 이미 결제 완료된 주문인지 확인
        if hasattr(order, "payment"):
            existing_payment = order.payment
            if existing_payment.is_paid:
                raise serializers.ValidationError("이미 결제된 주문입니다.")
            # Payment가 존재하지만 미결제 상태면 재사용 가능하도록 저장
            self.existing_payment = existing_payment
        else:
            self.existing_payment = None

        # 주문 상태 확인 (재고 확보 완료된 주문만 결제 가능)
        if order.status != "confirmed":
            raise serializers.ValidationError(
                f"주문 처리가 완료되지 않았습니다. 잠시 후 다시 시도해주세요. (현재 상태: {order.get_status_display()})"
            )

        # 주문 금액이 0원인지 확인 (포인트 전액 결제 허용)
        # final_amount가 0원이어도 허용 (포인트 전액 결제)
        if order.total_amount <= 0:
            raise serializers.ValidationError("결제 금액이 올바르지 않습니다.")

        self.order = order
        return value

    def create(self, validated_data):
        """결제 정보 생성 (PaymentService 사용)"""
        # 이미 미결제 Payment가 있으면 재사용
        if hasattr(self, "existing_payment") and self.existing_payment:
            return self.existing_payment

        from ..services.payment_service import PaymentService

        order = self.order
        payment_method = validated_data.get("payment_method", "card")
        idempotency_key = validated_data.get("idempotency_key")

        # PaymentService를 통해 결제 정보 생성 (멱등성 키 전달)
        payment = PaymentService.create_payment(
            order=order,
            payment_method=payment_method,
            idempotency_key=idempotency_key if idempotency_key else None,
        )

        return payment


class PaymentConfirmSerializer(serializers.Serializer):
    """
    결제 승인용 시리얼라이저
    토스페이먼츠 결제창에서 결제 완료 후 승인 요청
    """

    order_id = serializers.IntegerField(help_text="우리 시스템의 주문 ID")

    payment_key = serializers.CharField(help_text="토스페이먼츠 결제키")

    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=0,
        help_text="결제 금액",
    )

    def validate(self, attrs):
        """데이터 검증"""
        order_id = attrs["order_id"]
        amount = attrs["amount"]

        # Payment 찾기 (사용자 검증 포함)
        try:
            payment = Payment.objects.get(
                order_id=order_id,
                order__user=self.context["request"].user,  # 보안: 본인 주문만 접근 가능
            )
        except Payment.DoesNotExist:
            raise serializers.ValidationError("결제 정보를 찾을 수 없습니다.")

        # 이미 완료된 결제인지 확인
        if payment.is_paid:
            raise serializers.ValidationError("이미 완료된 결제입니다.")

        # 유효하지 않은 상태 확인 (보안: Toss API 호출 전 사전 검증)
        if payment.status in ["expired", "canceled", "aborted"]:
            raise serializers.ValidationError(f"유효하지 않은 결제 상태입니다: {payment.get_status_display()}")

        # 금액 검증 (포인트 차감 후 금액과 비교)
        if payment.amount != amount:
            raise serializers.ValidationError(f"결제 금액이 일치하지 않습니다. " f"(예상: {payment.amount}, 실제: {amount})")

        self.payment = payment
        return attrs


class PaymentCancelSerializer(serializers.Serializer):
    """
    결제 취소용 시리얼라이저

    입력 검증만 수행하며, 비즈니스 로직 검증은 서비스 레이어에서 처리합니다.
    """

    payment_id = serializers.IntegerField(help_text="결제 ID")

    cancel_reason = serializers.CharField(max_length=200, help_text="취소 사유")


class PaymentLogSerializer(serializers.ModelSerializer):
    """
    결제 로그 조회용 시리얼라이저

    보안 주의사항:
    - 'data' 필드는 디버깅 정보와 API 응답을 포함할 수 있습니다.
    - 민감한 정보(카드번호, 개인정보 등)가 포함될 수 있으므로,
      일반 사용자에게 노출할 때는 주의가 필요합니다.
    - 관리자 전용 API에서만 사용하거나, 'data' 필드를 제외한
      별도의 serializer를 사용하는 것을 권장합니다.
    """

    log_type_display = serializers.CharField(source="get_log_type_display", read_only=True)

    class Meta:
        model = PaymentLog
        fields = [
            "id",
            "payment",
            "log_type",
            "log_type_display",
            "message",
            "data",  # 주의: 민감 정보 포함 가능
            "created_at",
        ]


class PaymentWebhookSerializer(serializers.Serializer):
    """
    토스페이먼츠 웹훅 본문 시리얼라이저

    토스 웹훅 본문: {"eventType": "...", "createdAt": "...", "data": {...}}
    - PAYMENT_STATUS_CHANGED: data = Payment 객체 (paymentKey, orderId, status, ...)
    - 그 외 이벤트(DEPOSIT_CALLBACK, CANCEL_STATUS_CHANGED, ...)는 is_supported=False 로 표시만 하고 에러는 내지 않는다
    """

    SUPPORTED_EVENTS = ("PAYMENT_STATUS_CHANGED",)

    eventType = serializers.CharField(help_text="이벤트 타입 (PAYMENT_STATUS_CHANGED 등)")

    createdAt = serializers.CharField(required=False, help_text="이벤트 생성 시각 (ISO 8601)")

    data = serializers.JSONField(help_text="이벤트 데이터 (PAYMENT_STATUS_CHANGED 이면 Payment 객체)")

    is_supported = False

    def validate_eventType(self, value):
        """지원하는 이벤트 타입인지 표시 (미지원은 무시 대상이지 에러가 아니다)"""
        self.is_supported = value in self.SUPPORTED_EVENTS
        return value

    def validate_data(self, value):
        """data 는 객체여야 한다 (Payment 객체)"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("data must be an object.")
        return value


class DepositCallbackSerializer(serializers.Serializer):
    """
    토스페이먼츠 가상계좌 입금 웹훅(DEPOSIT_CALLBACK) 본문 시리얼라이저

    이 이벤트만 본문이 평평하다 — eventType/data 래퍼 없이
    {"createdAt", "secret", "status", "transactionKey", "orderId"}.
    secret 은 승인 응답의 Payment.secret 과 같아야 한다.
    """

    createdAt = serializers.CharField(required=False, help_text="웹훅 생성 시각 (ISO 8601)")

    secret = serializers.CharField(help_text="승인 응답의 secret 과 비교해 진위를 확인하는 값")

    status = serializers.CharField(help_text="결제 상태 (DONE, CANCELED, ...)")

    transactionKey = serializers.CharField(required=False, help_text="상태가 바뀐 가상계좌 거래 키")

    orderId = serializers.CharField(help_text="주문번호")


class PaymentFailSerializer(serializers.Serializer):
    """
    결제 실패 처리용 시리얼라이저
    토스페이먼츠 결제창에서 실패/취소 시 호출
    """

    code = serializers.CharField(
        max_length=100,
        help_text="실패 코드 (USER_CANCEL, TIMEOUT 등)",
    )

    message = serializers.CharField(
        max_length=500,
        help_text="실패 사유",
    )

    order_id = serializers.CharField(
        max_length=100,
        help_text="토스 주문번호 (toss_order_id)",
    )

    def validate_order_id(self, value: str) -> str:
        """주문 및 결제 정보 검증"""
        try:
            # N+1 방지: order와 user를 함께 조회 (view에서 사용)
            payment = Payment.objects.select_related("order__user").get(toss_order_id=value)
        except Payment.DoesNotExist:
            raise serializers.ValidationError("결제 정보를 찾을 수 없습니다.")

        # 이미 완료된 결제는 실패 처리 불가
        if payment.status == "done":
            raise serializers.ValidationError("이미 완료된 결제입니다.")

        # 이미 취소된 결제는 실패 처리 불가
        if payment.status == "canceled":
            raise serializers.ValidationError("이미 취소된 결제입니다.")

        self.payment = payment
        return value
