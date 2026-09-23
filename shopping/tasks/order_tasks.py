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
    ORDER_STALLED_FAILURE_REASON,
)

logger = get_task_logger(__name__)


@shared_task(
    name="shopping.tasks.order_tasks.process_order_heavy_tasks",
    queue="order_processing",
    max_retries=3,
    default_retry_delay=10,
    # 처리를 끝낸 뒤 ack — 처리 중 워커 프로세스가 죽으면(task_reject_on_worker_lost) 메시지가 큐로 돌아간다.
    # 아래 주문 행 락 + pending 재검사가 있어 두 번 배달돼도 한 번만 처리된다.
    acks_late=True,
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

                    # 앞 반복에서 이미 차감한 재고 복구
                    # (이 분기는 예외 없이 return 하므로 트랜잭션이 커밋된다 — 되돌리지 않으면 부분 차감이 남는다)
                    for item in order.order_items.all():
                        Product.objects.filter(pk=item.product.pk).update(stock=F("stock") + item.quantity)

                    # 주문 실패 처리
                    order.status = "failed"
                    order.failure_reason = (
                        f"재고 부족: {product.name} " f"(요청: {cart_item.quantity}개, 재고: {product.stock}개)"
                    )
                    order.save(update_fields=["status", "failure_reason", "updated_at"])

                    _restore_cart(cart_id)

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

                    _restore_cart(cart_id)

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


def _restore_cart(cart_id: int) -> None:
    """
    주문이 실패했을 때 장바구니 상품을 사용자에게 돌려준다 (호출자의 트랜잭션 안에서)

    처리가 늦어지는 사이 사용자가 새 장바구니를 만들었을 수 있다. 사용자당 활성 장바구니는 하나라
    (unique_active_cart_per_user) 주문 장바구니를 다시 켜면 제약 위반으로 실패 처리 전체가 롤백되고
    주문이 pending 에 남는다 — 그 경우 상품을 새 장바구니로 옮긴다. 새 장바구니에 이미 있는 상품은
    사용자가 나중에 고른 수량을 그대로 둔다.
    """
    from ..models.cart import Cart

    cart = Cart.objects.select_for_update().get(pk=cart_id)
    active_cart = None
    if cart.user_id is not None:
        active_cart = Cart.objects.select_for_update().filter(user_id=cart.user_id, is_active=True).exclude(pk=cart_id).first()

    if active_cart is None:
        Cart.objects.filter(pk=cart_id).update(is_active=True)
        logger.info(f"장바구니 복구: cart_id={cart_id}")
        return

    already_in_active = active_cart.items.values_list("product_id", flat=True)
    moved = cart.items.exclude(product_id__in=list(already_in_active)).update(cart=active_cart)
    logger.info(f"장바구니 복구: cart_id={cart_id} → active_cart_id={active_cart.pk}, moved_items={moved}")


def _abandon_stalled_order(order_id: int) -> bool:
    """
    결제 만료 시간까지 처리되지 못한 주문을 실패로 닫고 장바구니를 돌려준다 (독립 트랜잭션)

    pending·OrderItem 0개 주문은 재고·포인트가 아직 차감되지 않았으므로 되돌릴 것은 장바구니뿐이다.

    Returns:
        True 이면 닫음, False 이면 잠근 뒤 재검증 결과 건너뜀 (그 사이 워커가 처리함)
    """
    from ..models.order import Order

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != "pending" or order.order_items.exists():
            logger.info(f"처리 지연 주문 닫기 건너뜀: order_id={order_id}, status={order.status}")
            return False

        order.status = "failed"
        order.failure_reason = ORDER_STALLED_FAILURE_REASON
        order.save(update_fields=["status", "failure_reason", "updated_at"])

        if order.cart_id is not None:
            _restore_cart(order.cart_id)

    logger.warning(f"처리 지연 주문 실패 처리: order_id={order_id}, order_number={order.order_number}")
    return True


@shared_task(
    bind=True,
    name="shopping.tasks.order_tasks.republish_stalled_orders",
    queue="order_processing",
    max_retries=0,
)
def republish_stalled_orders(self, stall_minutes: int | None = None, give_up_minutes: int | None = None) -> dict:
    """
    발행이 끊긴 주문(pending·OrderItem 0개)을 다시 발행한다

    create_order_hybrid 는 Order 를 커밋한 뒤 process_order_heavy_tasks 를 발행한다. 커밋 뒤 발행이 실패하거나
    (브로커 장애), 워커가 메시지를 잃거나, 재시도를 다 쓰고 끝나면 주문이 pending·OrderItem 0개로 남는다.
    expire_unpaid_orders 는 이 주문을 건너뛰므로(돌려놓을 재고가 없음) 여기서 처리한다.

    - stall_minutes 가 지난 주문 → 다시 발행. 원래 메시지가 늦게 도착해도 태스크가 주문 행 락 + pending
      재검사로 시작하므로 한 번만 처리된다.
    - give_up_minutes 가 지나도 남은 주문 → 실패로 닫고 장바구니를 돌려준다 (_abandon_stalled_order).
      같은 원인으로 계속 실패하는 주문을 영원히 다시 발행하지 않게 하는 상한이다.

    Args:
        stall_minutes: 재발행 기준(분). None 이면 settings.ORDER_REPUBLISH_AFTER_MINUTES
        give_up_minutes: 포기 기준(분). None 이면 settings.ORDER_PAYMENT_TIMEOUT_MINUTES

    Returns:
        처리 결과 (candidates / republished / abandoned / skipped / errors)
    """
    from ..models.order import Order, OrderItem

    if stall_minutes is None:
        stall_minutes = settings.ORDER_REPUBLISH_AFTER_MINUTES
    if give_up_minutes is None:
        give_up_minutes = settings.ORDER_PAYMENT_TIMEOUT_MINUTES

    now = timezone.now()
    give_up_cutoff = now - timedelta(minutes=give_up_minutes)

    candidates = list(
        Order.objects.filter(status="pending", created_at__lt=now - timedelta(minutes=stall_minutes))
        .filter(~Exists(OrderItem.objects.filter(order_id=OuterRef("pk"))))
        .order_by("id")
        .values_list("id", "cart_id", "used_points", "created_at")
    )

    republished_count = 0
    abandoned_count = 0
    skipped_count = 0
    errors: list[str] = []

    for order_id, cart_id, used_points, created_at in candidates:
        try:
            if created_at < give_up_cutoff:
                if _abandon_stalled_order(order_id):
                    abandoned_count += 1
                else:
                    skipped_count += 1
            elif cart_id is None:
                # 장바구니 기록이 없는 주문은 다시 발행할 수 없다 — 포기 기준이 지나면 위에서 닫힌다
                skipped_count += 1
            else:
                process_order_heavy_tasks.delay(order_id=order_id, cart_id=cart_id, use_points=used_points)
                republished_count += 1
                logger.warning(f"처리 지연 주문 재발행: order_id={order_id}, cart_id={cart_id}")
        except Exception as e:
            error_msg = f"order_id={order_id}: {e}"
            errors.append(error_msg)
            logger.error(f"처리 지연 주문 재발행 실패: {error_msg}")

    if candidates:
        logger.warning(
            f"처리 지연 주문 검사 완료: candidates={len(candidates)}, republished={republished_count}, "
            f"abandoned={abandoned_count}, skipped={skipped_count}, errors={len(errors)}"
        )

    return {
        "status": "completed",
        "candidates": len(candidates),
        "republished": republished_count,
        "abandoned": abandoned_count,
        "skipped": skipped_count,
        "errors": errors,
    }


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
