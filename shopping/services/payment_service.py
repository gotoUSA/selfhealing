"""결제 서비스 레이어"""

import logging
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from ..constants import TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT
from ..models.cart import Cart
from ..models.order import Order
from ..models.payment import Payment, PaymentLog
from ..models.product import Product
from ..utils.toss_payment import TossPaymentClient, TossPaymentError
from .point_service import PointService

# Chaos injection imports
from ..chaos.decorators import (
    inject_payment_confirm_delay,
    inject_partial_failure_after_pg,
    inject_confirm_race_delay,
    inject_cancel_race_delay,
    PartialFailureError,
    # Phase 2 injections
    inject_phase2_orphan_pg,
    inject_phase2_race_delay,
    Phase2OrphanPGError,
)

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
            toss_order_id=Payment.toss_order_id_for(order),
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
        # [CHAOS] Race condition amplification - delay before lock acquisition
        inject_confirm_race_delay(payment_id=payment.pk)

        # 동시성 제어: 결제 객체를 락으로 보호하고 최신 상태 확인
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        # 이미 처리된 결제인지 확인
        if payment.is_paid:
            raise PaymentConfirmError("이미 완료된 결제입니다.")

        # 가상계좌 발급 뒤 재승인 요청 차단 — 입금 웹훅이 마감한다
        if payment.is_waiting_for_deposit:
            raise PaymentConfirmError("가상계좌 입금 대기 중인 결제입니다.")

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
            order_id=payment.toss_order_id,  # 결제창에 넘긴 orderId 와 같아야 한다 (주문번호)
            amount=amount,
        )
        logger.info(f"토스페이먼츠 결제 승인 성공: payment_id={payment.id}, order_id={order_id}")

        # [CHAOS] Partial failure after PG success - simulates internal failure
        try:
            inject_partial_failure_after_pg(payment_id=payment.id, pg_response=payment_data)
        except PartialFailureError as e:
            logger.error(f"[CHAOS] Partial failure injected after PG success: payment_id={payment.id}")
            # PG succeeded but internal processing failed - this triggers rollback/DLQ
            raise PaymentConfirmError(str(e))

        # [CHAOS PHASE 2] BP-21: Orphaned PG Transaction
        # PG succeeded but internal DB commit will fail - creates PG/DB inconsistency
        try:
            inject_phase2_orphan_pg(payment_id=payment.id, pg_response=payment_data)
        except Phase2OrphanPGError as e:
            logger.error(f"[CHAOS BP-21] Orphan PG triggered: payment_id={payment.id}, pg_status={payment_data.get('status')}")
            # Self-healing should detect this via DLQ and trigger reconciliation
            # The PG has charged the customer, but our DB won't record it
            raise PaymentConfirmError(str(e))

        # [CHAOS PHASE 2] BP-27: Race window amplification
        inject_phase2_race_delay(payment_id=payment.id, operation="confirm")

        # [CHAOS] Payment confirm delay - expands race window before DB commit
        inject_payment_confirm_delay(payment_id=payment.id, order_id=order.id)

        # 가상계좌: 승인 응답은 "발급"이지 "입금"이 아니다 — 입금 전엔 결제 완료 처리하지 않는다
        if payment_data.get("status") == TOSS_PAYMENT_STATUS_WAITING_FOR_DEPOSIT:
            PaymentService.record_virtual_account_issued(payment, payment_data)
            return {
                "payment": payment,
                "points_earned": 0,
                "receipt_url": "",
                "waiting_for_deposit": True,
            }

        # 2. Payment 정보 업데이트
        payment.mark_as_paid(payment_data)
        logger.info(f"결제 정보 업데이트 완료: payment_id={payment.id}, status={payment.status}")

        # 3. 판매량 증가 (재고 차감은 주문 생성 시 이미 처리됨)
        logger.info(f"판매량 증가 시작: order_id={order.id}")
        for order_item in order.order_items.all():
            if order_item.product:
                # Product를 락으로 보호
                product = Product.objects.select_for_update().get(pk=order_item.product.pk)
                # sold_count만 증가 (재고는 주문 생성 시 이미 차감됨)
                Product.objects.filter(pk=product.pk).update(
                    sold_count=F("sold_count") + order_item.quantity,
                )
                logger.info(
                    f"판매량 증가: product_id={product.pk}, product_name={product.name}, " f"quantity={order_item.quantity}"
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
            # 주문 시점에 스냅샷된 적립률 사용 (등급 변경 시에도 일관성 보장)
            earn_rate = order.earn_rate_at_order  # 스냅샷 사용
            # total_amount는 이미 순수 상품 금액 (배송비 미포함)
            product_amount = order.total_amount
            points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))

            if points_to_add > 0:
                logger.info(
                    f"포인트 적립 시작: user_id={user.id}, order_id={order.id}, "
                    f"points={points_to_add}, earn_rate={earn_rate}%, "
                    f"membership_at_order={order.membership_at_order}"
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
                        "membership_at_order": order.membership_at_order,  # 주문 시점 등급
                        "current_membership": user.membership_level,  # 현재 등급
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
    def record_virtual_account_issued(payment: Payment, payment_data: dict[str, Any]) -> None:
        """
        가상계좌 발급 기록 (승인 응답 status=WAITING_FOR_DEPOSIT)

        입금 전이므로 판매량·주문 상태·장바구니·포인트는 건드리지 않는다.
        주문은 confirmed 로 남아 입금 웹훅(DEPOSIT_CALLBACK / PAYMENT_STATUS_CHANGED)을 기다리고,
        기한 만료(EXPIRED)는 웹훅이 롤백을 트리거한다. 미결제 주문 만료 배치는 이 상태를 건너뛴다.

        Args:
            payment: 결제 객체 (호출자가 락을 잡고 있어야 한다)
            payment_data: 토스 승인 응답 (Payment 객체, virtualAccount · secret 포함)
        """
        payment.mark_as_waiting_for_deposit(payment_data)

        PaymentLog.objects.create(
            payment=payment,
            log_type="approve",
            message="가상계좌 발급 완료, 입금 대기",
            data=payment_data,
        )

        logger.info(
            f"가상계좌 발급: payment_id={payment.id}, order_id={payment.order_id}, "
            f"bank={payment.virtual_account_bank_code}, due={payment.virtual_account_due_date}"
        )

    @staticmethod
    @transaction.atomic
    def cancel_virtual_account_before_deposit(order_id: int, user) -> None:
        """
        입금 전 가상계좌 결제의 주문 취소 — 토스에 계좌를 먼저 닫고 주문을 취소한다

        토스 결제 취소 API 를 입금 전(WAITING_FOR_DEPOSIT) 결제에 부르면 그 계좌로 더 이상 입금할 수 없다.
        돈이 들어오지 않았으므로 환불 계좌도 필요 없다. 주문만 취소하고 계좌를 열어 두면 뒤늦은 입금이
        취소된 주문에 들어와 입금 웹훅이 주문을 결제 완료로 되살렸다.

        순서: 결제·주문 락 → 검증 → 토스 취소(계좌 닫기) → 결제 canceled → OrderService.cancel_order
        (재고·쓴 포인트 복구). 토스 취소 뒤 DB 가 실패해도 계좌는 닫혔으므로 입금은 들어올 수 없고,
        토스가 보내는 CANCELED 웹훅이 주문을 취소한다.

        Raises:
            PaymentCancelError: 입금 대기 상태가 아니거나(그 사이 입금됨 등) 토스가 취소를 거절함
        """
        from .order_service import OrderService

        payment = Payment.objects.select_for_update().filter(order_id=order_id, order__user=user).first()
        if payment is None or payment.status != "waiting_for_deposit":
            raise PaymentCancelError("결제 상태가 바뀌었습니다. 주문 상태를 확인한 뒤 다시 시도해주세요.")

        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status not in ["pending", "confirmed"]:
            raise PaymentCancelError("취소할 수 없는 주문입니다.")

        try:
            cancel_data = TossPaymentClient().cancel_payment(
                payment_key=payment.payment_key, cancel_reason="고객 주문 취소 (입금 전 가상계좌 반납)"
            )
        except TossPaymentError as e:
            # 방금 입금이 들어왔으면 토스가 입금 전 취소를 거절한다 — 입금 웹훅이 결제를 마감한다
            logger.warning(f"가상계좌 반납 실패: payment_id={payment.id}, code={e.code}, message={e.message}")
            raise PaymentCancelError(f"가상계좌를 닫지 못했습니다: {e.message}") from e

        payment.mark_as_canceled(cancel_data)
        PaymentLog.objects.create(
            payment=payment,
            log_type="cancel",
            message="입금 전 가상계좌 반납 — 주문 취소",
            data={"order_id": order_id},
        )
        logger.info(f"입금 전 가상계좌 반납: payment_id={payment.id}, order_id={order_id}")

        OrderService.cancel_order(order)

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

            # 가상계좌 발급 뒤 재승인 요청 차단 — 입금 웹훅이 마감한다
            if payment.is_waiting_for_deposit:
                raise PaymentConfirmError("가상계좌 입금 대기 중인 결제입니다.")

            # 유효하지 않은 상태 확인
            if payment.status in ["expired", "canceled", "aborted"]:
                raise PaymentConfirmError(f"유효하지 않은 결제 상태입니다: {payment.get_status_display()}")

            # 처리 중 상태로 변경 (이 시점부터 다른 요청은 차단됨)
            # payment_key 도 함께 저장한다 — 승인 체인이 끝나지 못하고 멈추면 reconcile_stalled_payments 가
            # 이 키로 토스에 현재 상태를 묻는다. updated_at 은 멈춘 시간을 재는 기준이다.
            payment.status = "in_progress"
            payment.payment_key = payment_key
            try:
                with transaction.atomic():
                    payment.save(update_fields=["status", "payment_key", "updated_at"])
            except IntegrityError:
                # 다른 결제가 이미 쓰는 paymentKey — 정상 결제창 흐름에서는 생길 수 없다
                raise PaymentConfirmError("유효하지 않은 결제 키입니다.")

        # 2. Celery Chain: Toss API 호출 → 최종 처리
        # 테스트 환경(EAGER=True)에서는 chain이 .get()을 호출하여 에러 발생
        # 따라서 TESTING 모드에서는 직접 순차 호출
        from django.conf import settings

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            # Eager 모드: 직접 함수 호출 (동기 실행)
            toss_result = call_toss_confirm_api(payment_key, order_id, amount, toss_order_id=payment.toss_order_id)
            final_result = finalize_payment_confirm(toss_result, payment.id, user.id)

            # 응답 형식 통일을 위한 더미 AsyncResult
            result = type("DummyResult", (), {"id": "sync-execution"})()
        else:
            # 프로덕션 환경: chain 사용
            task_chain = chain(
                call_toss_confirm_api.s(payment_key, order_id, amount, toss_order_id=payment.toss_order_id),
                finalize_payment_confirm.s(payment.id, user.id),
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

        # [CHAOS] Cancel race window - delay to amplify race conditions with confirm
        inject_cancel_race_delay(payment_id=payment_id)

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

        # 배송이 시작된 주문의 돈은 반품(ReturnService.complete_refund)으로만 돌려준다 — 상품은 고객에게 있다.
        # canceled 는 환불 없이 주문만 취소된 예전 데이터(아래에서 돈만 돌려준다)
        if order.status not in ("paid", "canceled"):
            raise PaymentCancelError(
                f"결제를 취소할 수 없는 주문 상태입니다({order.get_status_display()}). "
                f"배송이 시작된 주문은 반품으로 신청해주세요."
            )

        # 5. 되돌릴 수 없는 토스 환불 전에 검증을 끝낸다 — 환불 뒤에 거절하면 트랜잭션만 롤백되고 돈은 나간다
        #    - 주문이 이미 취소됐으면(환불 없이 주문만 취소된 예전 데이터·결제 마감 경합) 재고·포인트는 이미
        #      돌아갔다 → 돈만 돌려준다
        #    - 적립 포인트를 회수할 수 없으면(이미 써 버림) 취소를 거절한다. 회수는 FIFO 로 적립 건에서 빼므로
        #      잔액이 아니라 회수 가능한 적립 건으로 본다. 사용자 행을 잠가 회수 전까지 포인트 사용을 막는다
        order_already_canceled = order.status == "canceled"
        if not order_already_canceled and order.earned_points > 0:
            locked_user = type(user).objects.select_for_update().get(pk=user.pk)
            # 잔액 조건(사용 포인트 환불 뒤 잔액)과 FIFO 조건(회수 가능한 적립 건) 둘 다 — 회수 단계가 보는 두 가지
            reclaimable = min(
                locked_user.points + order.used_points,
                PointService().get_usable_points(locked_user, for_cancel=True),
            )
            if reclaimable < order.earned_points:
                logger.warning(
                    f"적립 포인트 회수 불가로 결제 취소 거절: payment_id={payment_id}, "
                    f"required={order.earned_points}, reclaimable={reclaimable}"
                )
                raise PaymentCancelError(
                    f"포인트가 부족하여 결제를 취소할 수 없습니다. 적립 포인트를 이미 사용했습니다. "
                    f"(회수 필요: {order.earned_points}P, 회수 가능: {reclaimable}P)"
                )
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

            if order_already_canceled:
                # 재고·포인트는 주문 취소 때 이미 돌아갔다 — 다시 하면 두 번 복구된다
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="cancel",
                    message="이미 취소된 주문의 결제 환불 (재고·포인트는 주문 취소 때 복구됨)",
                    data={"cancel_reason": cancel_reason, "canceled_amount": str(payment.canceled_amount)},
                )
                logger.info(f"취소된 주문의 결제 환불 완료: payment_id={payment_id}, order_id={order.id}")
                return {
                    "payment_id": payment.id,
                    "status": payment.status,
                    "canceled_amount": payment.canceled_amount,
                    "cancel_reason": payment.cancel_reason,
                    "canceled_at": payment.canceled_at,
                    "points_refunded": 0,
                    "points_deducted": 0,
                }

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

                # 포인트 환불 — 쓴 적립 건과 만료일까지 되돌린다
                PointService().refund_used_points(
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

    @staticmethod
    @transaction.atomic
    def complete_points_only_payment(order: Order, user) -> dict[str, Any]:
        """
        포인트 전액 결제 처리

        final_amount가 0원인 경우 Toss API 호출 없이 바로 결제 완료 처리합니다.

        Args:
            order: 주문 객체
            user: 요청한 사용자

        Returns:
            결제 완료 정보

        Raises:
            PaymentConfirmError: 결제 처리 실패
        """
        logger.info(
            f"포인트 전액 결제 시작: order_id={order.id}, order_number={order.order_number}, "
            f"used_points={order.used_points}, user_id={user.id}"
        )

        # 1. 주문 상태 확인
        if order.status != "confirmed":
            raise PaymentConfirmError(f"주문 처리가 완료되지 않았습니다. (현재 상태: {order.get_status_display()})")

        # 2. 포인트 전액 결제인지 확인
        if order.final_amount != 0:
            raise PaymentConfirmError(f"포인트 전액 결제가 아닙니다. 결제 금액: {order.final_amount}원")

        # 3. 동시성 제어: Order를 락으로 보호
        order = Order.objects.select_for_update().get(pk=order.pk)

        # 4. Payment 생성 또는 조회
        payment, created = Payment.objects.get_or_create(
            order=order,
            defaults={
                "toss_order_id": f"POINTS-{order.id}",
                "amount": 0,
                "method": "points",
                "status": "ready",
            },
        )

        if payment.is_paid:
            raise PaymentConfirmError("이미 완료된 결제입니다.")

        # 5. Payment 상태 업데이트 (포인트 전액 결제)
        payment.status = "done"
        payment.method = "points"
        payment.save(update_fields=["status", "method", "updated_at"])

        # 6. 판매량 증가
        logger.info(f"판매량 증가 시작: order_id={order.id}")
        for order_item in order.order_items.all():
            if order_item.product:
                product = Product.objects.select_for_update().get(pk=order_item.product.pk)
                Product.objects.filter(pk=product.pk).update(
                    sold_count=F("sold_count") + order_item.quantity,
                )
                logger.info(
                    f"판매량 증가: product_id={product.pk}, product_name={product.name}, " f"quantity={order_item.quantity}"
                )

        # 7. 주문 상태 변경
        order.status = "paid"
        order.payment_method = "points"
        order.save(update_fields=["status", "payment_method", "updated_at"])
        logger.info(f"주문 상태 변경: order_id={order.id}, status=paid")

        # 8. 장바구니 비활성화
        Cart.objects.filter(user=user, is_active=True).update(is_active=False)
        logger.info(f"장바구니 비활성화 완료: user_id={user.id}")

        # 9. 포인트 전액 결제 로그
        PaymentLog.objects.create(
            payment=payment,
            log_type="approve",
            message=f"포인트 {order.used_points}점으로 전액 결제 완료",
            data={
                "used_points": order.used_points,
                "order_id": order.id,
                "order_number": order.order_number,
            },
        )

        logger.info(
            f"포인트 전액 결제 완료: payment_id={payment.id}, order_id={order.id}, " f"used_points={order.used_points}"
        )

        return {
            "payment": payment,
            "order": order,
            "points_used": order.used_points,
            "message": "포인트 전액 결제가 완료되었습니다.",
        }

    @staticmethod
    def verify_order_stock(order: Order) -> dict[str, Any]:
        """
        주문의 재고 사전 검증

        결제 요청 전에 주문 상품들의 재고를 확인합니다.

        Args:
            order: 주문 객체

        Returns:
            dict: {
                'is_valid': bool,
                'issues': list[dict],
                'message': str
            }
        """
        issues = []

        for order_item in order.order_items.select_related("product"):
            product = order_item.product
            if not product:
                continue

            # 상품 활성화 상태 확인
            if not product.is_active:
                issues.append(
                    {
                        "order_item_id": order_item.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "issue_type": "inactive",
                        "message": f"'{product.name}' 상품이 판매 중단되었습니다.",
                        "requested": order_item.quantity,
                        "available": 0,
                    }
                )
            # 재고 확인 (주문 생성 시 이미 차감되었으므로 현재 재고가 음수가 아닌지 확인)
            elif product.stock < 0:
                issues.append(
                    {
                        "order_item_id": order_item.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "issue_type": "oversold",
                        "message": f"'{product.name}' 상품의 재고가 부족합니다.",
                        "requested": order_item.quantity,
                        "available": max(0, product.stock + order_item.quantity),
                    }
                )

        if issues:
            logger.warning(f"주문 재고 검증 실패: order_id={order.id}, issues={len(issues)}")
            return {
                "is_valid": False,
                "issues": issues,
                "message": "일부 상품의 재고에 문제가 있습니다.",
            }

        return {
            "is_valid": True,
            "issues": [],
            "message": "모든 상품의 재고가 확인되었습니다.",
        }
