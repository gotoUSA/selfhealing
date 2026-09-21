"""주문 관련 Celery 태스크"""

from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from ..constants import (
    ORDER_EXPIRABLE_STATUSES,
    ORDER_EXPIRED_FAILURE_REASON,
    ORDER_EXPIRY_PROTECTED_PAYMENT_STATUSES,
)

logger = get_task_logger(__name__)


@shared_task(
    name="shopping.tasks.order_tasks.process_order_heavy_tasks",
    queue="order_processing",
    max_retries=3,
    default_retry_delay=10,
)
def process_order_heavy_tasks(order_id: int, cart_id: int, use_points: int = 0) -> dict:
    """
    주문 생성 후 무거운 작업 처리
    - 재고 차감
    - 포인트 사용
    - 장바구니 비우기

    Args:
        order_id: Order ID
        cart_id: Cart ID
        use_points: 사용할 포인트

    Returns:
        처리 결과
    """
    from ..models.cart import Cart
    from ..models.order import Order, OrderItem
    from ..models.product import Product
    from ..services.point_service import PointService

    logger.info(f"주문 무거운 작업 시작: order_id={order_id}")

    try:
        with transaction.atomic():
            # 1. Order 조회 및 락
            order = Order.objects.select_for_update().get(pk=order_id)

            # 이미 처리된 주문인지 확인 (멱등성)
            if order.status != "pending":
                logger.warning(f"이미 처리된 주문: order_id={order_id}, status={order.status}")
                return {"status": "already_processed", "order_id": order_id}

            # 2. Cart 조회 및 락
            cart = Cart.objects.select_for_update().get(pk=cart_id)

            # 3. 재고 차감 및 OrderItem 생성
            # ✅ Deadlock 방지: Product ID 순서대로 정렬하여 락 획득 순서를 일관되게 유지
            cart_items = cart.items.select_related("product").order_by("product_id").all()

            for cart_item in cart_items:
                product = Product.objects.select_for_update().get(pk=cart_item.product.pk)

                # 재고 부족 체크
                if product.stock < cart_item.quantity:
                    logger.error(
                        f"재고 부족: product_id={product.pk}, " f"requested={cart_item.quantity}, available={product.stock}"
                    )

                    # 주문 실패 처리
                    order.status = "failed"
                    order.failure_reason = (
                        f"재고 부족: {product.name} " f"(요청: {cart_item.quantity}개, 재고: {product.stock}개)"
                    )
                    order.save(update_fields=["status", "failure_reason", "updated_at"])

                    # 장바구니 복구
                    Cart.objects.filter(pk=cart_id).update(is_active=True)
                    logger.info(f"장바구니 복구: cart_id={cart_id}")

                    return {
                        "status": "failed",
                        "reason": "insufficient_stock",
                        "product": product.name,
                        "order_id": order_id,
                    }

                # 재고 차감
                Product.objects.filter(pk=product.pk).update(stock=F("stock") - cart_item.quantity)

                logger.info(f"재고 차감: product_id={product.pk}, quantity={cart_item.quantity}")

                # OrderItem 생성
                OrderItem.objects.create(
                    order=order,
                    product=cart_item.product,
                    product_name=cart_item.product.name,
                    quantity=cart_item.quantity,
                    price=cart_item.product.price,
                )

            # 4. 포인트 사용 (선택적)
            if use_points > 0:
                point_service = PointService()
                result = point_service.use_points_fifo(
                    user=order.user,
                    amount=use_points,
                    type="use",
                    order=order,
                    description=f"주문 #{order.order_number} 결제시 사용",
                    metadata={
                        "order_id": order.id,
                        "order_number": order.order_number,
                    },
                )

                if not result["success"]:
                    logger.error(f"포인트 사용 실패: order_id={order_id}, reason={result['message']}")

                    # 주문 실패 처리 (재고는 이미 차감됨 → 복구 필요)
                    for item in order.order_items.all():
                        Product.objects.filter(pk=item.product.pk).update(stock=F("stock") + item.quantity)

                    order.status = "failed"
                    order.failure_reason = f"포인트 사용 실패: {result['message']}"
                    order.save(update_fields=["status", "failure_reason", "updated_at"])

                    # 장바구니 복구
                    Cart.objects.filter(pk=cart_id).update(is_active=True)
                    logger.info(f"장바구니 복구: cart_id={cart_id}")

                    return {
                        "status": "failed",
                        "reason": "point_deduction_failed",
                        "message": result["message"],
                        "order_id": order_id,
                    }

            # 5. 주문 확정
            order.status = "confirmed"
            order.save(update_fields=["status", "updated_at"])

            # 6. 장바구니 비우기
            cart.items.all().delete()

            logger.info(f"주문 무거운 작업 완료: order_id={order_id}")

            return {
                "status": "success",
                "order_id": order_id,
                "order_number": order.order_number,
            }

    except Exception as e:
        logger.error(f"주문 처리 실패: order_id={order_id}, error={str(e)}")

        # 재시도
        raise process_order_heavy_tasks.retry(exc=e)


class _OrderNotExpirable(Exception):
    """만료 트랜잭션 안에서 재검증에 실패했을 때 — 트랜잭션을 되돌리고 그 주문만 건너뛴다"""


def _expire_order(order_id: int) -> bool:
    """
    주문 하나를 만료 처리한다 (독립 트랜잭션)

    Returns:
        True 이면 취소됨, False 이면 재검증 결과 건너뜀
    """
    from ..models.order import Order
    from ..models.payment import Payment, PaymentLog
    from ..services.order_service import OrderService

    try:
        with transaction.atomic():
            # 1. 주문 잠금 후 상태 재검증 (후보 조회와 여기 사이에 결제가 끝났을 수 있음)
            order = Order.objects.select_for_update().get(pk=order_id)
            if order.status not in ORDER_EXPIRABLE_STATUSES:
                raise _OrderNotExpirable(f"status={order.status}")

            # 2. 결제 울타리: 살아 있는 결제(승인 진행 중·입금 대기·승인 완료)가 붙어 있으면 건드리지 않고,
            #    아니면 결제를 expired 로 바꿔 이후의 승인 요청이 PaymentService 에서 거부되게 한다.
            #    조건부 UPDATE 라 승인 요청이 먼저 in_progress 로 바꿨으면 0행이 갱신되고 아래 검사에 걸린다.
            payments = Payment.objects.filter(order_id=order_id)
            previous_statuses = dict(payments.values_list("id", "status"))
            payments.exclude(status__in=ORDER_EXPIRY_PROTECTED_PAYMENT_STATUSES).update(status="expired")
            if payments.filter(status__in=ORDER_EXPIRY_PROTECTED_PAYMENT_STATUSES).exists():
                raise _OrderNotExpirable("payment is live")

            # 3. 취소 (재고·판매량·포인트 복구는 cancel_order 에 있음)
            OrderService.cancel_order(order)
            Order.objects.filter(pk=order_id).update(failure_reason=ORDER_EXPIRED_FAILURE_REASON)

            for payment in payments:
                PaymentLog.objects.create(
                    payment=payment,
                    log_type="cancel",
                    message=f"주문 만료로 결제 종료: {ORDER_EXPIRED_FAILURE_REASON}",
                    data={"order_id": order_id, "previous_status": previous_statuses.get(payment.id)},
                )

    except _OrderNotExpirable as e:
        logger.info(f"미결제 주문 만료 건너뜀: order_id={order_id}, reason={e}")
        return False

    logger.info(f"미결제 주문 만료 처리: order_id={order_id}, order_number={order.order_number}")
    return True


@shared_task(
    bind=True,
    name="shopping.tasks.order_tasks.expire_unpaid_orders",
    queue="order_processing",
    max_retries=0,
)
def expire_unpaid_orders(self, timeout_minutes: int | None = None) -> dict:
    """
    결제 시간이 지난 미결제 주문을 취소하고 재고를 돌려놓는다

    주문 생성 시 재고를 깎아 두는데(동기 경로 `_create_order_items_and_decrease_stock`,
    비동기 경로 `process_order_heavy_tasks`), 결제하지 않고 떠난 주문은 그 재고를 계속 점유한다.
    Beat 가 주기적으로 이 태스크를 돌려 `ORDER_PAYMENT_TIMEOUT_MINUTES` 가 지난 pending/confirmed
    주문을 `OrderService.cancel_order` 로 취소한다.

    건너뛰는 주문:
    - 결제가 in_progress / waiting_for_deposit / done 인 주문 (승인이 진행 중이거나 곧 마감됨)
    - OrderItem 이 없는 pending 주문 (비동기 경로에서 아직 재고를 깎지 않음 — 돌려놓을 재고가 없고,
      cancel_order 가 아직 차감되지 않은 포인트를 환불해 버림)

    주문마다 독립 트랜잭션·독립 try/except 로 처리해 하나의 실패가 나머지를 막지 않는다.

    Args:
        timeout_minutes: 만료 기준(분). None 이면 settings.ORDER_PAYMENT_TIMEOUT_MINUTES

    Returns:
        처리 결과 (candidates / expired / skipped / errors)
    """
    from ..models.order import Order, OrderItem

    if timeout_minutes is None:
        timeout_minutes = settings.ORDER_PAYMENT_TIMEOUT_MINUTES

    cutoff = timezone.now() - timedelta(minutes=timeout_minutes)
    logger.info(f"미결제 주문 만료 검사 시작: timeout={timeout_minutes}분, cutoff={cutoff.isoformat()}")

    candidate_ids = list(
        Order.objects.filter(status__in=ORDER_EXPIRABLE_STATUSES, created_at__lt=cutoff)
        .filter(Exists(OrderItem.objects.filter(order_id=OuterRef("pk"))))
        .exclude(payment__status__in=ORDER_EXPIRY_PROTECTED_PAYMENT_STATUSES)
        .order_by("id")
        .values_list("id", flat=True)
    )

    expired_count = 0
    skipped_count = 0
    errors: list[str] = []

    try:
        for order_id in candidate_ids:
            try:
                if _expire_order(order_id):
                    expired_count += 1
                else:
                    skipped_count += 1
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:
                error_msg = f"order_id={order_id}: {e}"
                errors.append(error_msg)
                logger.error(f"미결제 주문 만료 실패: {error_msg}")
    except SoftTimeLimitExceeded:
        # 남은 후보는 다음 실행에서 다시 잡힌다
        logger.warning(
            f"미결제 주문 만료 시간 제한 도달: processed={expired_count + skipped_count + len(errors)}/{len(candidate_ids)}"
        )

    if expired_count or errors:
        logger.warning(
            f"미결제 주문 만료 완료: candidates={len(candidate_ids)}, expired={expired_count}, "
            f"skipped={skipped_count}, errors={len(errors)}"
        )
    else:
        logger.info(f"미결제 주문 없음: candidates={len(candidate_ids)}, skipped={skipped_count}")

    return {
        "status": "completed",
        "candidates": len(candidate_ids),
        "expired": expired_count,
        "skipped": skipped_count,
        "errors": errors,
    }
