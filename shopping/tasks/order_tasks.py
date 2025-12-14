"""주문 관련 Celery 태스크"""

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
from django.db.models import F

logger = get_task_logger(__name__)


@shared_task(
    name="shopping.tasks.order_tasks.process_order_heavy_tasks",
    queue="order_processing",
    max_retries=3,
    default_retry_delay=10,
)
def process_order_heavy_tasks(
    order_id: int,
    cart_id: int,
    use_points: int = 0
) -> dict:
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
            for cart_item in cart.items.all():
                product = Product.objects.select_for_update().get(pk=cart_item.product.pk)

                # 재고 부족 체크
                if product.stock < cart_item.quantity:
                    logger.error(
                        f"재고 부족: product_id={product.pk}, "
                        f"requested={cart_item.quantity}, available={product.stock}"
                    )

                    # 주문 실패 처리
                    order.status = "failed"
                    order.save(update_fields=["status", "updated_at"])

                    return {
                        "status": "failed",
                        "reason": "insufficient_stock",
                        "product": product.name,
                        "order_id": order_id,
                    }

                # 재고 차감
                Product.objects.filter(pk=product.pk).update(
                    stock=F("stock") - cart_item.quantity
                )

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
                    }
                )

                if not result["success"]:
                    logger.error(f"포인트 사용 실패: order_id={order_id}, reason={result['message']}")

                    # 주문 실패 처리 (재고는 이미 차감됨 → 복구 필요)
                    for item in order.order_items.all():
                        Product.objects.filter(pk=item.product.pk).update(
                            stock=F("stock") + item.quantity
                        )

                    order.status = "failed"
                    order.save(update_fields=["status", "updated_at"])

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
