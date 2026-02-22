"""
교환/환불 관련 비즈니스 로직
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from shopping.models.return_request import Return
from .point_service import PointService

if TYPE_CHECKING:
    from shopping.models.order import Order
    from shopping.models.user import User

logger = logging.getLogger(__name__)


class ReturnValidationError(Exception):
    """반품 검증 관련 예외"""

    pass


class ReturnService:
    """교환/환불 관련 서비스 클래스"""

    @staticmethod
    def validate_order_for_return(order: Order, user: User) -> None:
        """
        반품 신청 가능 여부 검증 (비즈니스 규칙)

        검증 항목:
        1. 주문 상태가 'delivered'인지 확인
        2. 배송 완료 후 7일 이내인지 확인
        3. 이미 처리 중인 교환/환불이 있는지 확인

        Args:
            order: 검증할 주문 객체
            user: 요청한 사용자

        Raises:
            ReturnValidationError: 검증 실패 시
        """
        from shopping.models.return_request import Return

        # 1. 주문 상태 확인
        if order.status != "delivered":
            raise ReturnValidationError("배송 완료된 주문만 신청 가능합니다.")

        # 2. 배송 완료 후 7일 이내 확인
        days_passed = (timezone.now() - order.created_at).days
        if days_passed > 7:
            raise ReturnValidationError("배송 완료 후 7일이 지나 신청할 수 없습니다.")

        # 3. 이미 신청한 교환/환불이 있는지 확인
        existing_returns = Return.objects.filter(
            order=order, status__in=["requested", "approved", "shipping", "received"]
        ).exists()

        if existing_returns:
            raise ReturnValidationError("이미 처리 중인 교환/환불이 있습니다.")

    @staticmethod
    def validate_return_items(order: Order, return_items_data: list[dict]) -> list:
        """
        반품 상품 항목 검증

        검증 항목:
        1. 반품 상품이 1개 이상인지 확인
        2. 각 OrderItem이 해당 주문에 속하는지 확인
        3. 반품 수량이 주문 수량을 초과하지 않는지 확인

        Args:
            order: 원본 주문 객체
            return_items_data: 반품 항목 데이터 리스트
                [{'order_item_id': int, 'quantity': int}, ...]

        Returns:
            list: 검증된 OrderItem 객체 리스트

        Raises:
            ReturnValidationError: 검증 실패 시
        """
        from shopping.models.order import OrderItem

        if not return_items_data:
            raise ReturnValidationError("반품할 상품을 선택해주세요.")

        validated_items = []

        for item_data in return_items_data:
            order_item_id = item_data.get("order_item_id")
            quantity = item_data.get("quantity")

            # OrderItem 존재 여부 및 해당 주문 소속 확인
            try:
                order_item = OrderItem.objects.get(id=order_item_id, order=order)
            except OrderItem.DoesNotExist:
                raise ReturnValidationError(f"주문 상품(ID: {order_item_id})을 찾을 수 없습니다.")

            # 수량 검증
            if quantity > order_item.quantity:
                raise ReturnValidationError(
                    f"{order_item.product_name}: 반품 수량({quantity})이 "
                    f"주문 수량({order_item.quantity})을 초과할 수 없습니다."
                )

            validated_items.append(order_item)

        return validated_items

    @staticmethod
    def generate_return_number() -> str:
        """
        교환/환불 번호 자동 생성 (동시성 안전)
        형식: RET + YYYYMMDD + 일련번호(3자리)
        예: RET20250115001

        Note:
            - 이 메서드는 create_return()의 @transaction.atomic 내에서 호출됨
            - select_for_update()로 동시성 제어하여 중복 번호 생성 방지

        Returns:
            str: 생성된 교환/환불 번호
        """
        from shopping.models.return_request import Return

        today = timezone.now().strftime("%Y%m%d")
        prefix = f"RET{today}"

        # 동시성 제어: 오늘 날짜 Return 중 마지막 레코드에 락 획득
        last_return = (
            Return.objects.filter(return_number__startswith=prefix)
            .select_for_update()
            .order_by("-return_number")
            .values_list("return_number", flat=True)
            .first()
        )

        if last_return:
            last_number = int(last_return[-3:])
            new_number = last_number + 1
        else:
            new_number = 1

        return f"{prefix}{new_number:03d}"

    @staticmethod
    def calculate_refund_amount(return_items: list) -> Decimal:
        """
        환불 금액 계산

        Args:
            return_items: ReturnItem 리스트

        Returns:
            Decimal: 총 환불 금액
        """
        total = Decimal("0")
        for item in return_items:
            total += item.product_price * item.quantity
        return total

    @staticmethod
    @transaction.atomic
    def create_return(
        order: Order, user: User, type: str, reason: str, reason_detail: str, return_items_data: list[dict], **kwargs
    ) -> Return:
        """
        교환/환불 신청 생성

        Args:
            order: 원본 주문
            user: 신청자
            type: 타입 (refund/exchange)
            reason: 사유
            reason_detail: 상세 사유
            return_items_data: 반품 항목 리스트
                [
                    {
                        'order_item': OrderItem,
                        'quantity': int,
                        'product_name': str (optional),
                        'product_price': Decimal (optional)
                    }
                ]
            **kwargs: 추가 필드 (refund_account_bank, exchange_product 등)

        Returns:
            Return: 생성된 교환/환불 신청
        """
        from shopping.models.return_request import Return, ReturnItem

        # 1. return_number 생성
        return_number = ReturnService.generate_return_number()

        # 2. 교환 비즈니스 로직 검증 (낙관적 방식)
        # Note: 신청 시점에는 재고 확인만 수행, 실제 차감은 complete_exchange()에서 수행
        #       이는 업계 표준 방식으로, 승인 전 취소/반품 미도착 등의 상황을 고려한 설계
        if type == "exchange":
            exchange_product = kwargs.get("exchange_product")

            # [검증 1] 여러 상품 교환 시 exchange_product 필수
            if len(return_items_data) > 1 and not exchange_product:
                raise ValueError("여러 상품 교환 시 교환받을 상품을 선택해주세요. " "또는 환불 후 재구매를 권장합니다.")

            if exchange_product:
                # 명시적으로 다른 상품 지정한 경우 재고 확인 (힌트용, 최종 확인은 complete_exchange)
                if exchange_product.stock < 1:
                    raise ValueError("교환 상품의 재고가 부족합니다.")
            else:
                # exchange_product가 None인 경우: 동일 상품 교환
                if return_items_data:
                    first_order_item = return_items_data[0]["order_item"]

                    # [검증 2] 삭제된 상품 교환 불가
                    if not first_order_item.product:
                        raise ValueError("해당 상품이 삭제되어 동일 상품 교환이 불가합니다. " "환불을 신청해주세요.")

                    # [검증 3] 동일 상품 재고 확인 (힌트용, 최종 확인은 complete_exchange)
                    if first_order_item.product.stock < 1:
                        raise ValueError(
                            f"동일 상품({first_order_item.product.name})의 재고가 부족합니다. "
                            "다른 상품으로 교환하거나 환불을 신청해주세요."
                        )

                    # 동일 상품으로 자동 설정
                    kwargs["exchange_product"] = first_order_item.product
                    logger.info(
                        f"동일 상품 교환: exchange_product 자동 설정 "
                        f"(product_id={first_order_item.product.id}, name={first_order_item.product.name})"
                    )

        # 3. Return 객체 생성 (refund_amount는 나중에 계산)
        return_request = Return.objects.create(
            order=order,
            user=user,
            return_number=return_number,
            type=type,
            reason=reason,
            reason_detail=reason_detail,
            refund_amount=Decimal("0"),  # 임시값
            **kwargs,
        )

        # 4. ReturnItem 생성
        return_items = []
        for item_data in return_items_data:
            order_item = item_data["order_item"]
            quantity = item_data["quantity"]
            product_name = item_data.get("product_name", order_item.product_name)
            product_price = item_data.get("product_price", order_item.price)

            return_item = ReturnItem.objects.create(
                return_request=return_request,
                order_item=order_item,
                quantity=quantity,
                product_name=product_name,
                product_price=product_price,
            )
            return_items.append(return_item)

        # 4. refund_amount 계산 및 업데이트 (환불인 경우에만)
        if type == "refund":
            refund_amount = ReturnService.calculate_refund_amount(return_items)
            return_request.refund_amount = refund_amount
            return_request.save(update_fields=["refund_amount"])

        logger.info(
            f"교환/환불 신청 생성: return_number={return_number}, " f"order_id={order.id}, user_id={user.id}, type={type}"
        )

        return return_request

    @staticmethod
    @transaction.atomic
    def approve_return(return_obj: Return, admin_user: User | None = None, admin_memo: str = "") -> Return:
        """
        교환/환불 승인 처리 (판매자)

        Args:
            return_obj: 승인할 Return 객체
            admin_user: 승인한 관리자 (향후 이력 관리용)
            admin_memo: 관리자 메모

        Returns:
            Return: 승인된 Return 객체

        Raises:
            ValueError: 승인 불가능한 상태인 경우
        """
        # 동시성 제어: Return 객체에 락 획득
        return_obj = Return.objects.select_for_update().get(pk=return_obj.pk)

        if return_obj.status != "requested":
            raise ValueError("신청 상태에서만 승인할 수 있습니다.")

        return_obj.status = "approved"
        return_obj.approved_at = timezone.now()

        if admin_memo:
            return_obj.admin_memo = admin_memo

        return_obj.save()

        # 알림 발송
        from shopping.models import Notification

        Notification.objects.create(
            user=return_obj.user,
            notification_type="return",
            title=f"{return_obj.get_type_display()} 승인",
            message=f"{return_obj.return_number} 신청이 승인되었습니다. 반품 상품을 발송해주세요.",
            link=f"/returns/{return_obj.id}",
            metadata={"return_id": return_obj.id, "return_number": return_obj.return_number},
        )

        logger.info(
            f"교환/환불 승인: return_id={return_obj.id}, "
            f"return_number={return_obj.return_number}, admin_user_id={admin_user.id if admin_user else None}"
        )

        return return_obj

    @staticmethod
    @transaction.atomic
    def reject_return(return_obj: Return, reason: str) -> Return:
        """
        교환/환불 거부 처리 (판매자)

        Args:
            return_obj: 거부할 Return 객체
            reason: 거부 사유

        Returns:
            Return: 거부된 Return 객체

        Raises:
            ValueError: 거부 불가능한 상태인 경우
        """
        # 동시성 제어: Return 객체에 락 획득
        return_obj = Return.objects.select_for_update().get(pk=return_obj.pk)

        if return_obj.status != "requested":
            raise ValueError("신청 상태에서만 거부할 수 있습니다.")

        return_obj.status = "rejected"
        return_obj.rejected_reason = reason
        return_obj.save()

        # 알림 발송
        from shopping.models import Notification

        Notification.objects.create(
            user=return_obj.user,
            notification_type="return",
            title=f"{return_obj.get_type_display()} 거부",
            message=f"{return_obj.return_number} 신청이 거부되었습니다. 사유: {reason}",
            link=f"/returns/{return_obj.id}",
            metadata={"return_id": return_obj.id, "return_number": return_obj.return_number},
        )

        logger.info(
            f"교환/환불 거부: return_id={return_obj.id}, " f"return_number={return_obj.return_number}, reason={reason}"
        )

        return return_obj

    @staticmethod
    @transaction.atomic
    def confirm_receive_return(return_obj: Return) -> Return:
        """
        반품 도착 확인 (판매자)

        Args:
            return_obj: 수령 확인할 Return 객체

        Returns:
            Return: 수령 확인된 Return 객체

        Raises:
            ValueError: 수령 확인 불가능한 상태인 경우
        """
        # 동시성 제어: Return 객체에 락 획득
        return_obj = Return.objects.select_for_update().get(pk=return_obj.pk)

        if return_obj.status != "shipping":
            raise ValueError("배송 중 상태에서만 수령 확인할 수 있습니다.")

        return_obj.status = "received"
        return_obj.save()

        # 알림 발송
        from shopping.models import Notification

        Notification.objects.create(
            user=return_obj.user,
            notification_type="return",
            title="반품 도착 확인",
            message=f"{return_obj.return_number} 반품 상품이 도착했습니다. 곧 처리될 예정입니다.",
            link=f"/returns/{return_obj.id}",
            metadata={"return_id": return_obj.id, "return_number": return_obj.return_number},
        )

        logger.info(f"반품 도착 확인: return_id={return_obj.id}, " f"return_number={return_obj.return_number}")

        return return_obj

    @staticmethod
    @transaction.atomic
    def complete_refund(return_obj: Return) -> Return:
        """
        환불 완료 처리

        실제 환불 처리:
        1. 토스페이먼츠 API 호출하여 환불
        2. 재고 복구
        3. 포인트 처리 (향후 구현)
        4. 상태 변경

        Args:
            return_obj: 환불 처리할 Return 객체

        Returns:
            Return: 환불 완료된 Return 객체

        Raises:
            ValueError: 환불 처리 불가능한 상태인 경우
        """
        # 동시성 제어: Return 객체에 락 획득
        return_obj = Return.objects.select_for_update().get(pk=return_obj.pk)

        if return_obj.type != "refund":
            raise ValueError("환불 타입에서만 사용 가능합니다.")

        if return_obj.status != "received":
            raise ValueError("반품 도착 상태에서만 환불 처리할 수 있습니다.")

        # 성능 최적화: N+1 쿼리 방지
        return_items = return_obj.return_items.select_related("order_item__product").all()

        # 1. 토스페이먼츠 환불 처리
        actual_refund_amount = return_obj.refund_amount - return_obj.return_shipping_fee

        if hasattr(return_obj.order, "payment") and return_obj.order.payment:
            from shopping.utils.toss_payment import TossPaymentClient

            toss_client = TossPaymentClient()

            if actual_refund_amount > 0:
                refund_account = None
                if return_obj.refund_account_number:
                    # 복호화된 계좌번호 사용
                    decrypted_account = return_obj.get_decrypted_account_number()
                    refund_account = {
                        "bank": return_obj.refund_account_bank,
                        "accountNumber": decrypted_account,
                        "holderName": return_obj.refund_account_holder,
                    }

                toss_client.cancel_payment(
                    payment_key=return_obj.order.payment.payment_key,
                    cancel_reason=f"{return_obj.get_reason_display()} - {return_obj.reason_detail}",
                    cancel_amount=int(actual_refund_amount),
                    refund_account=refund_account,
                )

        # 2. 재고 복구 (동시성 제어를 위해 락 획득)
        from shopping.models import Product

        for return_item in return_items:
            if return_item.order_item.product:
                # select_for_update로 동시성 제어 (Race Condition 방지)
                product = Product.objects.select_for_update().get(pk=return_item.order_item.product.pk)
                product.stock += return_item.quantity
                product.save(update_fields=["stock"])

        # 3. 포인트 처리
        order = return_obj.order
        user = return_obj.user
        points_refunded = 0
        points_deducted = 0

        # 3-1. 사용한 포인트 환불
        if order.used_points > 0:
            points_refunded = order.used_points
            logger.info(f"포인트 환불 시작: user_id={user.id}, order_id={order.id}, " f"points={points_refunded}")

            PointService.add_points(
                user=user,
                amount=points_refunded,
                type="cancel_refund",
                order=order,
                description=f"환불 #{return_obj.return_number} - 사용 포인트 환불",
                metadata={
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "return_id": return_obj.id,
                    "return_number": return_obj.return_number,
                },
            )

            logger.info(f"포인트 환불 완료: user_id={user.id}, points={points_refunded}")

        # 3-2. 적립된 포인트 회수
        points_deducted = 0
        if order.earned_points > 0:
            # 중복 처리 방지: 이미 해당 주문에 대한 cancel_deduct가 처리되었는지 확인
            # select_for_update로 동시성 제어 (Race Condition 방지)
            from shopping.models.point import PointHistory

            existing_cancel_deduct = (
                PointHistory.objects.select_for_update()
                .filter(
                    user=user,
                    type="cancel_deduct",
                    order=order,
                )
                .exists()
            )

            if existing_cancel_deduct:
                logger.info(f"이미 적립 포인트 회수 완료됨: user_id={user.id}, order_id={order.id}")
            else:
                user.refresh_from_db()
                point_service = PointService()

                # 실제 회수 가능한 포인트 확인 (원장 기준)
                # 주의: 이 값은 예비 검증용이며, 실제 차감은 use_points_fifo에서 락과 함께 수행
                usable_points = point_service.get_usable_points(user)

                if usable_points < order.earned_points:
                    logger.warning(
                        f"유효한 포인트 부족으로 환불 처리 불가: user_id={user.id}, "
                        f"required={order.earned_points}, usable={usable_points}, cached={user.points}"
                    )
                    raise ValueError(
                        f"유효한 포인트가 부족합니다. " f"(필요: {order.earned_points}P, 사용 가능: {usable_points}P)"
                    )

                points_deducted = order.earned_points
                logger.info(f"적립 포인트 차감 시작: user_id={user.id}, order_id={order.id}, " f"points={points_deducted}")

                result = point_service.use_points_fifo(
                    user=user,
                    amount=points_deducted,
                    type="cancel_deduct",
                    order=order,
                    description=f"환불 #{return_obj.return_number} - 적립 포인트 회수",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                        "return_id": return_obj.id,
                        "return_number": return_obj.return_number,
                    },
                )

                if not result["success"]:
                    raise ValueError(f"포인트 회수 실패: {result['message']}")

                logger.info(f"적립 포인트 차감 완료: user_id={user.id}, points={points_deducted}")

        # 4. 상태 변경
        return_obj.status = "completed"
        return_obj.completed_at = timezone.now()
        return_obj.save()

        # 5. 주문 상태 변경
        return_obj.order.status = "refunded"
        return_obj.order.save(update_fields=["status"])

        # 알림 발송
        from shopping.models import Notification

        Notification.objects.create(
            user=return_obj.user,
            notification_type="return",
            title="환불 완료",
            message=f"{return_obj.return_number} 환불이 완료되었습니다. 환불 금액: {actual_refund_amount:,}원",
            link=f"/returns/{return_obj.id}",
            metadata={
                "return_id": return_obj.id,
                "return_number": return_obj.return_number,
                "refund_amount": str(actual_refund_amount),
            },
        )

        logger.info(
            f"환불 완료: return_id={return_obj.id}, "
            f"return_number={return_obj.return_number}, refund_amount={actual_refund_amount}"
        )

        return return_obj

    @staticmethod
    @transaction.atomic
    def complete_exchange(return_obj: Return, exchange_tracking_number: str, exchange_shipping_company: str) -> Return:
        """
        교환 완료 처리

        교환 상품 발송 후 호출:
        1. 동시성 제어를 위한 락 획득
        2. 교환 상품 재고 확인 및 차감
        3. 반품 상품 재고 증가
        4. 상태 변경
        5. 교환 상품 송장번호 저장

        Args:
            return_obj: 교환 처리할 Return 객체
            exchange_tracking_number: 교환 상품 송장번호
            exchange_shipping_company: 교환 상품 택배사

        Returns:
            Return: 교환 완료된 Return 객체

        Raises:
            ValueError: 교환 처리 불가능한 상태인 경우
        """
        from shopping.models import Product

        # 동시성 제어: Return 객체에 락 획득
        return_obj = Return.objects.select_for_update().get(pk=return_obj.pk)

        if return_obj.type != "exchange":
            raise ValueError("교환 타입에서만 사용 가능합니다.")

        if return_obj.status != "received":
            raise ValueError("반품 도착 상태에서만 교환 처리할 수 있습니다.")

        # 1. 교환 상품 재고 확인 및 차감 (락 획득)
        if return_obj.exchange_product:
            # select_for_update로 동시성 제어 (Race Condition 방지)
            exchange_product = Product.objects.select_for_update().get(pk=return_obj.exchange_product.pk)

            # 재고 부족 시 에러 (완료 시점에 최종 확인)
            if exchange_product.stock < 1:
                raise ValueError(
                    f"교환 상품({exchange_product.name})의 재고가 부족합니다. " "환불로 전환하거나 재입고를 기다려주세요."
                )

            # 교환 상품 재고 감소
            exchange_product.stock -= 1
            exchange_product.save(update_fields=["stock"])

        # 2. 반품 상품 재고 증가 (성능 최적화: N+1 쿼리 방지)
        return_items = return_obj.return_items.select_related("order_item__product").all()

        for return_item in return_items:
            if return_item.order_item.product:
                # 반품 상품도 락 획득하여 재고 증가
                product = Product.objects.select_for_update().get(pk=return_item.order_item.product.pk)
                product.stock += return_item.quantity
                product.save(update_fields=["stock"])

        # 3. 교환 상품 송장번호 저장
        return_obj.exchange_tracking_number = exchange_tracking_number
        return_obj.exchange_shipping_company = exchange_shipping_company

        # 4. 상태 변경
        return_obj.status = "completed"
        return_obj.completed_at = timezone.now()
        return_obj.save()

        # 5. 알림 발송
        from shopping.models import Notification

        Notification.objects.create(
            user=return_obj.user,
            notification_type="return",
            title="교환 완료",
            message=f"{return_obj.return_number} 교환 상품이 발송되었습니다. 송장번호: {exchange_tracking_number}",
            link=f"/returns/{return_obj.id}",
            metadata={
                "return_id": return_obj.id,
                "return_number": return_obj.return_number,
                "tracking_number": exchange_tracking_number,
            },
        )

        logger.info(
            f"교환 완료: return_id={return_obj.id}, "
            f"return_number={return_obj.return_number}, tracking_number={exchange_tracking_number}"
        )

        return return_obj
