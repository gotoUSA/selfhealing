from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

# 암호화 유틸리티 임포트
from shopping.utils.encryption import (
    encrypt_account_number,
    decrypt_account_number,
    mask_account_number,
    is_encrypted,
)

from .order import Order, OrderItem
from .product import Product

if TYPE_CHECKING:
    from shopping.models.user import User


class Return(models.Model):
    """
    교환/환불 신청 모델

    하나의 모델로 교환과 환불을 모두 처리합니다.
    프로세스: 신청 → 승인 → 반품 배송 → 수령 확인 → 완료
    """

    # 교환/환불 타입
    TYPE_CHOICES = [
        ("refund", "환불"),
        ("exchange", "교환"),
    ]

    # 처리 상태
    STATUS_CHOICES = [
        ("requested", "신청"),  # 고객이 신청함
        ("approved", "승인"),  # 판매자가 승인함
        ("rejected", "거부"),  # 판매자가 거부함
        ("shipping", "반품배송중"),  # 고객이 반품 발송함
        ("received", "반품도착"),  # 판매자가 반품 받음
        ("completed", "완료"),  # 환불/교환 완료
    ]

    # 신청 사유
    REASON_CHOICES = [
        ("change_of_mind", "단순변심"),
        ("defective", "상품불량"),
        ("wrong_product", "오배송"),
        ("description_mismatch", "상세페이지와 다름"),
        ("size_issue", "사이즈 문제"),
        ("other", "기타"),
    ]

    # 기본 정보
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="returns",
        verbose_name="원본 주문",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # Changed from CASCADE: 거래 기록 보존 의무(전자상거래법)
        related_name="returns",
        verbose_name="신청자",
        help_text="사용자 탈퇴 시에도 교환/환불 기록 유지 (법적 보존 의무)",
    )

    return_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="교환/환불 번호",
        help_text="자동 생성됨 (예: RET20250115001)",
    )

    # 타입 및 상태
    type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        verbose_name="타입",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="requested",
        verbose_name="처리 상태",
    )

    # 사유
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
        verbose_name="사유",
    )

    reason_detail = models.TextField(
        verbose_name="상세 사유",
        help_text="교환/환불 사유를 구체적으로 작성해주세요",
    )

    # 반품 배송 정보
    return_shipping_company = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="반품 택배사",
        help_text="예: CJ대한통운, 우체국택배",
    )

    return_tracking_number = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="반품 송장번호",
    )

    return_shipping_fee = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="반품 배송비",
        help_text="고객 부담인 경우 금액 입력",
    )

    # 환불 정보 (type='refund'일 때만 사용)
    refund_amount = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="환불 금액",
    )

    refund_method = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="환불 방법",
        help_text="원결제수단 또는 계좌환불",
    )

    refund_account_bank = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="환불 계좌 은행",
    )

    # 암호화하여 저장 (Fernet 대칭 암호화)
    # 저장 시 자동 암호화, 조회 시 get_decrypted_account_number() 사용
    refund_account_number = models.TextField(
        blank=True,
        verbose_name="환불 계좌번호 (암호화)",
        help_text="암호화되어 저장됩니다. 복호화는 get_decrypted_account_number() 사용",
    )

    refund_account_holder = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="예금주",
    )

    # 교환 정보 (type='exchange'일 때만 사용)
    exchange_product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exchange_returns",
        verbose_name="교환 상품",
        help_text="교환받을 상품 (같은 상품일 수도 있음)",
    )

    exchange_shipping_company = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="교환 상품 택배사",
    )

    exchange_tracking_number = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="교환 상품 송장번호",
    )

    # 처리 정보
    admin_memo = models.TextField(
        blank=True,
        verbose_name="관리자 메모",
        help_text="⚠️ 내부용 메모 - 고객에게 노출되어선 안 됨 (Serializer/View에서 관리자만 조회 가능하도록 설정 필요)",
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="승인 시각",
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="완료 시각",
    )

    rejected_reason = models.TextField(
        blank=True,
        verbose_name="거부 사유",
    )

    # 타임스탬프
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="신청일",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="수정일",
    )

    class Meta:
        db_table = "shopping_returns"
        verbose_name = "교환/환불"
        verbose_name_plural = "교환/환불 목록"
        ordering = ["-created_at"]
        indexes = [
            # return_number는 unique=True로 자동 인덱스 생성되므로 제외
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["order"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} - {self.return_number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        저장 시 자동 처리:
        1. 계좌번호 암호화 (평문인 경우에만)
        2. 교환/환불 생성은 서비스 레이어에서 담당 (ReturnService.create_return())
        """
        # 계좌번호 암호화 (평문인 경우에만)
        if self.refund_account_number and not is_encrypted(self.refund_account_number):
            try:
                self.refund_account_number = encrypt_account_number(self.refund_account_number)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"계좌번호 암호화 실패: {e}")
                # 암호화 실패 시에도 저장은 진행 (운영 중단 방지)
                # 단, 경고 로그 남김

        super().save(*args, **kwargs)

    def generate_return_number(self) -> str:
        """
        교환/환불 번호 자동 생성.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.generate_return_number` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "Return.generate_return_number() is deprecated. "
            "Use ReturnService.generate_return_number() instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        return ReturnService.generate_return_number()

    def calculate_refund_amount(self) -> Decimal:
        """
        환불 금액 계산.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.calculate_refund_amount` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "Return.calculate_refund_amount() is deprecated. "
            "Use ReturnService.calculate_refund_amount() instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        return ReturnService.calculate_refund_amount(self.return_items.all())

    def get_decrypted_account_number(self) -> str:
        """
        암호화된 계좌번호를 복호화하여 반환

        환불 처리 시 토스페이먼츠 API에 전달할 때 사용

        Returns:
            str: 복호화된 계좌번호 (예: "110-123-456789")

        Raises:
            ValueError: 복호화 실패 시
        """
        if not self.refund_account_number:
            return ""

        # 이미 평문인 경우 (마이그레이션 전 데이터)
        if not is_encrypted(self.refund_account_number):
            return self.refund_account_number

        try:
            return decrypt_account_number(self.refund_account_number)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"계좌번호 복호화 실패 (Return ID: {self.id}): {e}")
            raise ValueError("계좌번호 복호화에 실패했습니다.")

    def get_masked_account_number(self) -> str:
        """
        계좌번호를 마스킹 처리하여 반환

        API 응답에 포함할 때 사용 (보안)

        Returns:
            str: 마스킹된 계좌번호 (예: "***-***-6789")
        """
        if not self.refund_account_number:
            return ""

        try:
            # 복호화 후 마스킹
            decrypted = self.get_decrypted_account_number()
            return mask_account_number(decrypted)
        except Exception:
            # 복호화 실패 시 안전한 마스킹 (형식 무관)
            return "************"

    @classmethod
    def can_request_for_order(cls, order: Order) -> tuple[bool, str]:
        """
        교환/환불 신청 가능 여부 확인 (클래스 메서드)

        신청 전에 호출하여 검증할 수 있도록 클래스 메서드로 구현

        조건:
        1. 주문 상태가 'delivered' (배송 완료)
        2. 배송 완료 후 설정된 기간 이내 (기본 7일)
        3. 이미 교환/환불 신청한 주문이 아니어야 함

        Args:
            order: 확인할 주문 객체

        Returns:
            (가능 여부, 메시지)
        """
        # 주문 상태 확인
        if order.status != "delivered":
            return False, "배송 완료된 주문만 신청 가능합니다."

        # 배송 완료 후 지정된 기간 이내 확인
        # (실제로는 Order 모델에 delivered_at 필드가 있어야 함)
        # 여기서는 간단히 created_at 기준으로 체크
        deadline_days = getattr(settings, 'RETURN_REQUEST_DEADLINE_DAYS', 7)
        days_passed = (timezone.now() - order.created_at).days
        if days_passed > deadline_days:
            return False, f"배송 완료 후 {deadline_days}일이 지나 신청할 수 없습니다."

        # 이미 신청한 교환/환불이 있는지 확인
        existing_returns = cls.objects.filter(
            order=order, status__in=["requested", "approved", "shipping", "received"]
        ).exists()

        if existing_returns:
            return False, "이미 처리 중인 교환/환불이 있습니다."

        return True, "신청 가능"

    def approve(self, admin_user: User | None = None) -> None:
        """
        판매자 승인 처리.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.approve_return` instead.
            Will be removed in version 3.0.0.

        Args:
            admin_user: 승인한 관리자 (향후 이력 관리용)
        """
        import warnings
        warnings.warn(
            "Return.approve() is deprecated. "
            "Use ReturnService.approve_return(return_obj, admin_user) instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        ReturnService.approve_return(self, admin_user=admin_user)

    def reject(self, reason: str) -> None:
        """
        판매자 거부 처리.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.reject_return` instead.
            Will be removed in version 3.0.0.

        Args:
            reason: 거부 사유
        """
        import warnings
        warnings.warn(
            "Return.reject() is deprecated. "
            "Use ReturnService.reject_return(return_obj, reason) instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        ReturnService.reject_return(self, reason=reason)

    def confirm_receive(self) -> None:
        """
        반품 도착 확인 (판매자).

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.confirm_receive_return` instead.
            Will be removed in version 3.0.0.

        반품 상품을 수령했음을 확인
        """
        import warnings
        warnings.warn(
            "Return.confirm_receive() is deprecated. "
            "Use ReturnService.confirm_receive_return(return_obj) instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        ReturnService.confirm_receive_return(self)

    def complete_refund(self) -> None:
        """
        환불 완료 처리.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.complete_refund` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "Return.complete_refund() is deprecated. "
            "Use ReturnService.complete_refund(return_obj) instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        ReturnService.complete_refund(self)

    def complete_exchange(self) -> None:
        """
        교환 완료 처리.

        .. deprecated:: 2.0.0
            Use :meth:`ReturnService.complete_exchange` instead.
            Will be removed in version 3.0.0.
        """
        import warnings
        warnings.warn(
            "Return.complete_exchange() is deprecated. "
            "Use ReturnService.complete_exchange(return_obj, ...) instead. "
            "This method will be removed in v3.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from shopping.services.return_service import ReturnService
        # 교환 송장번호는 이미 저장되어 있어야 함
        if not self.exchange_tracking_number or not self.exchange_shipping_company:
            raise ValueError("교환 상품 송장번호가 필요합니다.")
        ReturnService.complete_exchange(
            self,
            exchange_tracking_number=self.exchange_tracking_number,
            exchange_shipping_company=self.exchange_shipping_company
        )


class ReturnItem(models.Model):
    """
    교환/환불하는 상품 항목

    하나의 Return에 여러 상품이 포함될 수 있음 (부분 환불/교환)
    """

    return_request = models.ForeignKey(
        Return,
        on_delete=models.CASCADE,
        related_name="return_items",
        verbose_name="교환/환불 신청",
    )

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="return_items",
        verbose_name="주문 상품",
    )

    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name="반품 수량",
    )

    # 상품 정보 스냅샷 (주문 당시 정보 유지)
    product_name = models.CharField(
        max_length=255,
        verbose_name="상품명",
    )

    product_price = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="단가",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="등록일",
    )

    class Meta:
        db_table = "shopping_return_items"
        verbose_name = "반품 상품"
        verbose_name_plural = "반품 상품 목록"
        # 같은 주문 상품을 중복으로 반품할 수 없음
        unique_together = [["return_request", "order_item"]]

    def __str__(self) -> str:
        return f"{self.product_name} x {self.quantity}개"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        저장 시 자동 처리:
        1. product_name이 없으면 order_item에서 가져오기
        2. product_price가 없으면 order_item에서 가져오기
        """
        if not self.product_name:
            self.product_name = self.order_item.product_name

        if not self.product_price:
            self.product_price = self.order_item.price

        super().save(*args, **kwargs)

    def get_subtotal(self) -> Decimal:
        """해당 상품의 반품 금액 계산"""
        return self.product_price * self.quantity

    def clean(self) -> None:
        """
        데이터 유효성 검증

        반품 수량이 주문 수량을 초과할 수 없음
        """
        from django.core.exceptions import ValidationError

        if self.quantity > self.order_item.quantity:
            raise ValidationError(f"반품 수량({self.quantity})이 주문 수량({self.order_item.quantity})을 초과할 수 없습니다.")
