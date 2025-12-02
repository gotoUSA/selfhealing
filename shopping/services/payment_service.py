"""결제 서비스 레이어"""

import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest

from ..models.cart import Cart
from ..models.order import Order
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..utils.toss_payment import TossPaymentClient, TossPaymentError
from .point_service import PointService

logger = logging.getLogger(__name__)


class PaymentCancelError(Exception):
    """결제 취소 관련 에러"""

    pass


class PaymentConfirmError(Exception):
    """결제 승인 관련 에러"""

    pass


# Redis TTL 상수 (Toss 실시간 결제 환경에 최적화)
IDEMPOTENCY_KEY_TTL = 60  # 60초 - 사용자 중복 요청 방어


class PaymentService:
    """결제 관련 비즈니스 로직을 처리하는 서비스"""

    @staticmethod
    def _get_idempotency_cache_key(key: str) -> str:
        """멱등성 키의 Redis 캐시 키 생성"""
        return f"payment:idempotency:{key}"

    @staticmethod
    def _check_idempotency_key(key: str) -> Payment | None:
        """
        Redis에서 멱등성 키 확인 (TTL 60초)

        Returns:
            기존 Payment 객체 또는 None
        """
        if not key:
            return None

        cache_key = PaymentService._get_idempotency_cache_key(key)
        payment_id = cache.get(cache_key)

        if payment_id:
            try:
                return Payment.objects.get(pk=payment_id)
            except Payment.DoesNotExist:
                # Redis에는 있지만 DB에 없는 경우 (드문 케이스)
                cache.delete(cache_key)

        return None

    @staticmethod
    def _set_idempotency_key(key: str, payment_id: int) -> None:
        """Redis에 멱등성 키 저장 (TTL 60초)"""
        if key:
            cache_key = PaymentService._get_idempotency_cache_key(key)
            cache.set(cache_key, payment_id, timeout=IDEMPOTENCY_KEY_TTL)

    @staticmethod
    @transaction.atomic
    def create_payment(order: Order, payment_method: str = "card", idempotency_key: str | None = None) -> Payment:
        """
        결제 정보 생성 (동시성 제어 포함)

        Args:
            order: 주문
            payment_method: 결제 수단
            idempotency_key: 멱등성 키 (클라이언트가 생성, 중복 결제 방지)

        Returns:
            Payment: 생성된 결제 정보

        Note:
            멱등성 키는 Redis TTL 60초로 관리됩니다.
            60초 내 동일한 키로 요청 시 기존 결제를 반환합니다.
        """
        logger.info(
            f"결제 정보 생성 시작: order_id={order.id}, order_number={order.order_number}, "
            f"payment_method={payment_method}, amount={order.final_amount}, "
            f"idempotency_key={idempotency_key}"
        )

        # 멱등성 키 확인 (Redis TTL 60초)
        existing_payment = PaymentService._check_idempotency_key(idempotency_key)
        if existing_payment:
            logger.info(f"기존 결제 반환 (멱등성): idempotency_key={idempotency_key}, " f"payment_id={existing_payment.id}")
            return existing_payment

        # 동시성 제어: Order를 락으로 보호
        Order.objects.select_for_update().get(pk=order.pk)

        # 기존 Payment가 있으면 삭제 (재시도의 경우)
        existing_count = Payment.objects.filter(order=order).count()
        if existing_count > 0:
            logger.warning(f"기존 결제 정보 삭제: order_id={order.id}, count={existing_count}")
            Payment.objects.filter(order=order).delete()

        # 새 Payment 생성
        payment = Payment.objects.create(
            order=order,
            toss_order_id=str(order.id),
            amount=order.final_amount,
            method=payment_method,
            status="ready",
            idempotency_key=idempotency_key,
        )

        # Redis에 멱등성 키 저장 (TTL 60초)
        PaymentService._set_idempotency_key(idempotency_key, payment.id)

        # 로그 기록
        PaymentLog.objects.create(
            payment=payment,
            log_type="request",
            message="결제 요청 생성",
            data={
                "order_id": order.id,
                "total_amount": str(order.total_amount),
                "used_points": order.used_points,
                "amount": str(order.final_amount),
                "payment_method": payment_method,
            },
        )

        logger.info(f"결제 정보 생성 완료: payment_id={payment.id}, order_id={order.id}, " f"amount={payment.amount}")

        return payment

    @staticmethod
    @transaction.atomic
    def confirm_payment_sync(payment: Payment, payment_key: str, order_id: int, amount: int, user) -> dict[str, Any]:
        """
        결제 승인 처리 (동기 버전 - 롤백용)

        Args:
            payment: 결제 객체
            payment_key: 토스페이먼츠 결제 키
            order_id: 주문 번호
            amount: 결제 금액
            user: 요청한 사용자

        Returns:
            결제 승인 결과

        Raises:
            PaymentConfirmError: 결제 승인 실패
            TossPaymentError: 토스페이먼츠 API 에러
        """
        # 동시성 제어: 결제 객체를 락으로 보호하고 최신 상태 확인
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        # 이미 처리된 결제인지 확인
        if payment.is_paid:
            raise PaymentConfirmError("이미 완료된 결제입니다.")

        # 유효하지 않은 상태 확인
        if payment.status in ["expired", "canceled", "aborted"]:
            raise PaymentConfirmError(f"유효하지 않은 결제 상태입니다: {payment.get_status_display()}")

        order = payment.order

        logger.info(
            f"결제 승인 시작: payment_id={payment.id}, order_id={order.id}, "
            f"order_number={order_id}, amount={amount}, user_id={user.id}"
        )

        # 토스페이먼츠 API 클라이언트
        toss_client = TossPaymentClient()

        # 1. 토스페이먼츠에 결제 승인 요청
        logger.info(f"토스페이먼츠 결제 승인 요청: order_id={order_id}, amount={amount}")
        payment_data = toss_client.confirm_payment(
            payment_key=payment_key,
            order_id=str(order_id),  # Toss API는 문자열 orderId를 받음
            amount=amount,
        )
        logger.info(f"토스페이먼츠 결제 승인 성공: payment_id={payment.id}, order_id={order_id}")

        # 2. Payment 정보 업데이트
        payment.mark_as_paid(payment_data)
        logger.info(f"결제 정보 업데이트 완료: payment_id={payment.id}, status={payment.status}")

        # 3. 재고 차감 및 판매량 증가 (Product 락으로 동시성 제어)
        logger.info(f"재고 차감 및 판매량 증가 시작: order_id={order.id}")
        for order_item in order.order_items.all():
            if order_item.product:
                # Product를 락으로 보호
                product = Product.objects.select_for_update().get(pk=order_item.product.pk)
                # 재고 차감 및 sold_count 증가 (F 객체로 안전하게)
                Product.objects.filter(pk=product.pk).update(
                    stock=F("stock") - order_item.quantity,
                    sold_count=F("sold_count") + order_item.quantity,
                )
                logger.info(
                    f"재고 차감 및 판매량 증가: product_id={product.pk}, product_name={product.name}, "
                    f"quantity={order_item.quantity}"
                )

        # 4. 주문 상태 변경
        order.status = "paid"
        order.payment_method = payment.method
        order.save(update_fields=["status", "payment_method", "updated_at"])
        logger.info(f"주문 상태 변경: order_id={order.id}, status=paid")

        # 5. 장바구니 비활성화
        Cart.objects.filter(user=user, is_active=True).update(is_active=False)
        logger.info(f"장바구니 비활성화 완료: user_id={user.id}")

        # 6. 포인트 적립 (순수 상품 금액 기준, 배송비 제외)
        points_to_add = 0
        # 포인트로만 결제한 경우는 적립하지 않음
        if order.final_amount > 0:
            # 등급별 적립률 적용
            earn_rate = user.get_earn_rate()  # 1, 2, 3, 5 (%)
            # total_amount는 이미 순수 상품 금액 (배송비 미포함)
            product_amount = order.total_amount
            points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))

            if points_to_add > 0:
                logger.info(
                    f"포인트 적립 시작: user_id={user.id}, order_id={order.id}, "
                    f"points={points_to_add}, earn_rate={earn_rate}%"
                )

                # 포인트 적립 (PointService 사용)
                PointService.add_points(
                    user=user,
                    amount=points_to_add,
                    type="earn",
                    order=order,
                    description=f"주문 #{order.order_number} 구매 적립",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                        "payment_amount": str(order.final_amount),
                        "product_amount": str(product_amount),
                        "shipping_fee": str(order.get_total_shipping_fee()),
                        "earn_rate": f"{earn_rate}%",
                        "membership_level": user.membership_level,
                    },
                )

                # 주문에 적립 포인트 기록
                order.earned_points = points_to_add
                order.save(update_fields=["earned_points"])

                # 포인트 적립 로그
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="approve",
                    message=f"포인트 {points_to_add}점 적립",
                    data={"points": points_to_add},
                )

                logger.info(f"포인트 적립 완료: user_id={user.id}, order_id={order.id}, points={points_to_add}")
        else:
            # 포인트 전액 결제 로그
            if order.used_points > 0:
                logger.info(f"포인트 전액 결제: user_id={user.id}, order_id={order.id}, " f"used_points={order.used_points}")
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="approve",
                    message=f"포인트 {order.used_points}점으로 전액 결제",
                    data={"used_points": order.used_points},
                )

        # 7. 결제 승인 로그
        PaymentLog.objects.create(
            payment=payment,
            log_type="approve",
            message="결제 승인 완료",
            data=payment_data,
        )

        logger.info(
            f"결제 승인 완료: payment_id={payment.id}, order_id={order.id}, " f"amount={amount}, points_earned={points_to_add}"
        )

        return {
            "payment": payment,
            "points_earned": points_to_add,
            "receipt_url": payment.receipt_url,
        }

    @staticmethod
    def confirm_payment_async(payment: Payment, payment_key: str, order_id: int, amount: int, user) -> dict[str, Any]:
        """
        결제 승인 처리 (비동기 버전)

        1. Toss API 호출 태스크 실행
        2. 즉시 응답 반환 (processing 상태)
        3. 백그라운드에서 결제 최종 처리

        Args:
            payment: 결제 객체
            payment_key: 토스페이먼츠 결제 키
            order_id: 주문 ID
            amount: 결제 금액
            user: 요청한 사용자

        Returns:
            {'status': 'processing', 'payment_id': ..., 'task_id': ...}

        Raises:
            PaymentConfirmError: 결제 승인 실패
        """
        from celery import chain

        from ..tasks.payment_tasks import call_toss_confirm_api, finalize_payment_confirm

        logger.info(f"비동기 결제 승인 시작: payment_id={payment.id}")

        # 1. Payment 상태 확인 및 변경 (동시성 제어)
        with transaction.atomic():
            # DB lock으로 동시 요청 직렬화
            payment = Payment.objects.select_for_update().get(pk=payment.pk)

            # 이미 완료된 결제 확인
            if payment.is_paid:
                raise PaymentConfirmError("이미 완료된 결제입니다.")

            # 이미 처리 중인 결제 확인 (중복 요청 차단)
            if payment.status == "in_progress":
                raise PaymentConfirmError("이미 처리 중인 결제입니다.")

            # 유효하지 않은 상태 확인
            if payment.status in ["expired", "canceled", "aborted"]:
                raise PaymentConfirmError(f"유효하지 않은 결제 상태입니다: {payment.get_status_display()}")

            # 처리 중 상태로 변경 (이 시점부터 다른 요청은 차단됨)
            payment.status = "in_progress"
            payment.save(update_fields=["status"])

        # 2. Celery Chain: Toss API 호출 → 최종 처리
        # 테스트 환경(EAGER=True)에서는 chain이 .get()을 호출하여 에러 발생
        # 따라서 TESTING 모드에서는 직접 순차 호출
        from django.conf import settings

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # Eager 모드: 직접 함수 호출 (동기 실행)
            toss_result = call_toss_confirm_api(payment_key, order_id, amount)
            final_result = finalize_payment_confirm(toss_result, payment.id, user.id)

            # 응답 형식 통일을 위한 더미 AsyncResult
            result = type("DummyResult", (), {"id": "sync-execution"})()
        else:
            # 프로덕션 환경: chain 사용
            task_chain = chain(
                call_toss_confirm_api.s(payment_key, order_id, amount), finalize_payment_confirm.s(payment.id, user.id)
            )
            result = task_chain.apply_async()

        logger.info(f"결제 승인 태스크 실행: payment_id={payment.id}, task_id={result.id}")

        # 3. 즉시 응답 (사용자는 결과를 WebSocket/Polling으로 확인)
        return {
            "status": "processing",
            "payment_id": payment.id,
            "task_id": result.id,
            "message": "결제 처리 중입니다. 잠시만 기다려주세요.",
        }

    @staticmethod
    @transaction.atomic
    def cancel_payment(payment_id: int, user, cancel_reason: str) -> dict[str, Any]:
        """
        결제 취소 처리

        동시성 제어를 위해 락을 먼저 획득한 후 모든 검증을 수행합니다.

        Args:
            payment_id: 취소할 결제 ID
            user: 요청한 사용자
            cancel_reason: 취소 사유

        Returns:
            취소된 결제 정보

        Raises:
            Payment.DoesNotExist: 결제 정보를 찾을 수 없음
            PaymentCancelError: 취소 불가능한 상태
        """
        logger.info(f"결제 취소 시작: payment_id={payment_id}, user_id={user.id}, " f"cancel_reason={cancel_reason}")

        # 1. 동시성 제어: Payment를 락으로 보호하며 조회
        try:
            payment = Payment.objects.select_for_update().get(id=payment_id, order__user=user)
        except Payment.DoesNotExist:
            logger.error(f"결제 정보를 찾을 수 없음: payment_id={payment_id}, user_id={user.id}")
            raise PaymentCancelError("결제 정보를 찾을 수 없습니다.")

        # 2. 중복 취소 방지: 이미 취소된 결제인지 확인
        if payment.is_canceled:
            logger.warning(f"이미 취소된 결제 취소 시도: payment_id={payment_id}, user_id={user.id}")
            raise PaymentCancelError("이미 취소된 결제입니다.")

        # 3. 취소 가능한 상태인지 확인
        if payment.status != "done":
            logger.warning(f"취소 불가능한 결제 상태: payment_id={payment_id}, status={payment.status}")
            raise PaymentCancelError(f"취소할 수 없는 결제 상태입니다: {payment.get_status_display()}")

        # 4. Order를 락으로 보호
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        logger.info(f"결제 취소 검증 완료: payment_id={payment_id}, order_id={order.id}")

        # 5. 토스페이먼츠 API 클라이언트
        toss_client = TossPaymentClient()

        # 포인트 변수 초기화
        points_refunded = 0
        points_deducted = 0

        try:
            # 6. 토스페이먼츠에 취소 요청
            logger.info(f"토스페이먼츠 결제 취소 요청: payment_id={payment_id}, order_id={order.id}")
            cancel_data = toss_client.cancel_payment(payment_key=payment.payment_key, cancel_reason=cancel_reason)
            logger.info(f"토스페이먼츠 결제 취소 성공: payment_id={payment_id}")

            # 7. Payment 정보 업데이트
            payment.mark_as_canceled(cancel_data)
            logger.info(f"결제 정보 업데이트 완료: payment_id={payment_id}, status={payment.status}")

            # 8. 재고 복구 (Product 락으로 동시성 제어)
            logger.info(f"재고 복구 시작: order_id={order.id}")
            for order_item in order.order_items.all():
                if order_item.product:  # 상품이 삭제되지 않았다면
                    # Product를 락으로 보호
                    product = Product.objects.select_for_update().get(pk=order_item.product.pk)
                    # sold_count가 음수가 되지 않도록 Greatest 사용
                    Product.objects.filter(pk=product.pk).update(
                        stock=F("stock") + order_item.quantity,
                        sold_count=Greatest(F("sold_count") - order_item.quantity, 0),
                    )
                    logger.info(
                        f"재고 및 판매량 복구: product_id={product.pk}, product_name={product.name}, "
                        f"quantity={order_item.quantity}"
                    )

            # 9. 주문 상태 변경
            order.status = "canceled"
            order.save(update_fields=["status", "updated_at"])
            logger.info(f"주문 상태 변경: order_id={order.id}, status=canceled")

            # 10. 포인트 처리
            # 10-1. 사용한 포인트 환불
            if order.used_points > 0:
                points_refunded = order.used_points
                logger.info(f"포인트 환불 시작: user_id={user.id}, order_id={order.id}, " f"points={points_refunded}")

                # 포인트 환불 (PointService 사용)
                PointService.add_points(
                    user=user,
                    amount=points_refunded,
                    type="cancel_refund",
                    order=order,
                    description=f"주문 #{order.order_number} 취소로 인한 포인트 환불",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                        "cancel_reason": cancel_reason,
                    },
                )

                # 환불 로그
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="cancel",
                    message=f"사용 포인트 {order.used_points}점 환불",
                    data={"points": order.used_points},
                )

                logger.info(f"포인트 환불 완료: user_id={user.id}, points={points_refunded}")

            # 10-2. 적립된 포인트 차감
            if order.earned_points > 0:
                # 포인트 부족 사전 체크
                user.refresh_from_db()
                if user.points < order.earned_points:
                    logger.warning(
                        f"포인트 부족으로 결제 취소 불가: user_id={user.id}, "
                        f"required={order.earned_points}, available={user.points}"
                    )
                    raise PaymentCancelError(
                        f"포인트가 부족하여 결제를 취소할 수 없습니다. "
                        f"(필요: {order.earned_points}P, 보유: {user.points}P)"
                    )

                points_deducted = order.earned_points
                logger.info(f"적립 포인트 차감 시작: user_id={user.id}, order_id={order.id}, " f"points={points_deducted}")

                # 포인트 차감 (FIFO 방식)
                point_service = PointService()
                result = point_service.use_points_fifo(
                    user=user,
                    amount=points_deducted,
                    type="cancel_deduct",
                    order=order,
                    description=f"주문 #{order.order_number} 취소로 인한 적립 포인트 차감",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                        "cancel_reason": cancel_reason,
                    },
                )

                if not result["success"]:
                    raise ValueError(f"포인트 차감 실패: {result['message']}")

                # 차감 로그
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="cancel",
                    message=f"적립 포인트 {order.earned_points}점 차감",
                    data={"points": -order.earned_points},
                )

                logger.info(f"적립 포인트 차감 완료: user_id={user.id}, points={points_deducted}")

            # 취소 성공 로그
            PaymentLog.objects.create(
                payment=payment,
                log_type="cancel",
                message="결제가 성공적으로 취소되었습니다.",
                data={
                    "cancel_reason": cancel_reason,
                    "canceled_amount": str(payment.canceled_amount),
                    "points_refunded": points_refunded,
                    "points_deducted": points_deducted,
                },
            )

            logger.info(
                f"결제 취소 완료: payment_id={payment_id}, order_id={order.id}, "
                f"canceled_amount={payment.canceled_amount}, "
                f"points_refunded={points_refunded}, points_deducted={points_deducted}"
            )

            # payment 저장 (mark_as_canceled에서 save 호출)
            # 응답 데이터 반환
            return {
                "payment_id": payment.id,
                "status": payment.status,
                "canceled_amount": payment.canceled_amount,
                "cancel_reason": payment.cancel_reason,
                "canceled_at": payment.canceled_at,
                "points_refunded": points_refunded,
                "points_deducted": points_deducted,
            }

        except TossPaymentError as e:
            # 토스 API 에러 (트랜잭션이 깨지기 때문에 로그는 나중에)
            error_message = f"결제 취소 실패: {e.message}"
            error_data = {"error_code": e.code, "error_message": e.message}

            logger.error(
                f"토스페이먼츠 결제 취소 실패: payment_id={payment_id}, " f"error_code={e.code}, error_message={e.message}"
            )

            # 트랜잭션 밖에서 로그 기록
            try:
                from django.db import transaction

                with transaction.atomic():
                    PaymentLog.objects.create(
                        payment=payment,
                        log_type="error",
                        message=error_message,
                        data=error_data,
                    )
            except Exception:
                pass  # 로그 실패는 무시

            raise PaymentCancelError(error_message)

        except Exception as e:
            # 기타 에러 (트랜잭션이 깨지기 때문에 로그는 나중에)
            error_message = f"결제 취소 중 오류 발생: {str(e)}"
            error_data = {"error": str(e)}

            logger.error(f"결제 취소 중 예상치 못한 오류: payment_id={payment_id}, error={str(e)}")

            # 트랜잭션 밖에서 로그 기록
            try:
                from django.db import transaction

                with transaction.atomic():
                    PaymentLog.objects.create(
                        payment=payment,
                        log_type="error",
                        message=error_message,
                        data=error_data,
                    )
            except Exception:
                pass  # 로그 실패는 무시

            raise
