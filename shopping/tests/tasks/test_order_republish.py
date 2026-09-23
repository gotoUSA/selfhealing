"""
발행이 끊긴 주문 테스트

비동기 경로(create_order_hybrid)는 Order 를 커밋한 뒤 process_order_heavy_tasks 를 발행한다.
커밋 뒤 발행이 실패하거나 워커가 메시지를 잃으면 주문이 pending·OrderItem 0개로 남고,
expire_unpaid_orders 는 이 주문을 건너뛴다 — republish_stalled_orders 가 다시 발행하거나 닫는다.
"""

from datetime import timedelta

from django.utils import timezone

import pytest

from shopping.constants import ORDER_STALLED_FAILURE_REASON
from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Product
from shopping.tasks.order_tasks import process_order_heavy_tasks, republish_stalled_orders


def _age_order(order, minutes: int) -> None:
    """created_at 은 auto_now_add 라 생성 후 직접 과거로 돌린다"""
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))


def _ordered_cart(user, product, quantity: int = 2) -> Cart:
    """주문에 쓰인 장바구니 (create_order_hybrid 가 비활성화한 상태, 상품은 그대로)"""
    cart = Cart.objects.create(user=user, is_active=False)
    CartItem.objects.create(cart=cart, product=product, quantity=quantity)
    return cart


def _stalled_order(order_factory, user, product, quantity: int = 2, **kwargs) -> Order:
    """워커가 한 번도 처리하지 못한 주문: pending + OrderItem 0개 + 주문 장바구니에 상품이 남아 있음"""
    cart = _ordered_cart(user, product, quantity)
    return order_factory(
        status="pending",
        cart=cart,
        total_amount=product.price * quantity,
        final_amount=product.price * quantity,
        **kwargs,
    )


@pytest.mark.django_db(transaction=True)
class TestFailedOrderRestoresCart:
    """주문 처리가 실패하면 장바구니 상품이 사용자에게 돌아온다"""

    def test_items_move_into_the_newer_active_cart(self, user, product, product_factory, order_factory):
        """처리가 늦어지는 사이 사용자가 새 장바구니를 만들었으면(사용자당 활성 장바구니 1개),
        주문 장바구니를 다시 켜는 대신 상품을 새 장바구니로 옮긴다"""
        product.stock = 1
        product.save()
        other = product_factory(name="다른 상품", sku="TEST-002", stock=10)
        ordered_cart = _ordered_cart(user, product, quantity=2)
        new_cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=new_cart, product=other, quantity=1)
        order = order_factory(status="pending", cart=ordered_cart)

        result = process_order_heavy_tasks(order_id=order.id, cart_id=ordered_cart.id)

        assert result["status"] == "failed"
        order.refresh_from_db()
        assert order.status == "failed"
        assert set(new_cart.items.values_list("product_id", "quantity")) == {(other.id, 1), (product.id, 2)}
        ordered_cart.refresh_from_db()
        assert ordered_cart.is_active is False

    def test_product_already_in_the_newer_cart_keeps_the_newer_quantity(self, user, product, order_factory):
        """새 장바구니에 같은 상품이 있으면 사용자가 나중에 고른 수량을 그대로 둔다"""
        product.stock = 1
        product.save()
        ordered_cart = _ordered_cart(user, product, quantity=2)
        new_cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=new_cart, product=product, quantity=1)
        order = order_factory(status="pending", cart=ordered_cart)

        process_order_heavy_tasks(order_id=order.id, cart_id=ordered_cart.id)

        assert list(new_cart.items.values_list("product_id", "quantity")) == [(product.id, 1)]
        assert Cart.objects.filter(user=user, is_active=True).count() == 1

    def test_ordered_cart_is_reactivated_when_there_is_no_newer_cart(self, user, product, order_factory):
        """새 장바구니가 없으면 주문 장바구니를 그대로 다시 켠다"""
        product.stock = 1
        product.save()
        ordered_cart = _ordered_cart(user, product, quantity=2)
        order = order_factory(status="pending", cart=ordered_cart)

        process_order_heavy_tasks(order_id=order.id, cart_id=ordered_cart.id)

        ordered_cart.refresh_from_db()
        assert ordered_cart.is_active is True
        assert ordered_cart.items.count() == 1


class TestOrderTaskDelivery:
    """처리 중 워커 프로세스가 죽어도 메시지가 사라지지 않는다"""

    def test_order_task_acknowledges_after_running(self):
        """acks_late: 처리를 끝낸 뒤 ack — 처리 중 죽으면(task_reject_on_worker_lost) 브로커가 다시 배달한다.
        태스크는 주문 행 락 + pending 재검사로 시작하므로 두 번 배달돼도 한 번만 처리된다"""
        assert process_order_heavy_tasks.acks_late is True


@pytest.mark.django_db(transaction=True)
class TestRepublishStalledOrders:
    """발행이 끊긴 주문은 다시 발행되고, 결제 만료 시간까지 처리되지 못하면 실패로 닫힌다"""

    def test_stalled_order_is_republished_and_processed(self, user, product, order_factory):
        """기준 시간이 지난 pending·항목 0개 주문 → 다시 발행 → 워커가 재고를 차감하고 확정한다"""
        initial_stock = product.stock
        order = _stalled_order(order_factory, user, product, quantity=2)
        _age_order(order, minutes=6)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["republished"] == 1
        assert result["errors"] == []
        order.refresh_from_db()
        assert order.status == "confirmed"
        assert order.order_items.count() == 1
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

    def test_republish_uses_the_points_recorded_on_the_order(self, user, product, order_factory):
        """재발행은 주문에 기록된 사용 포인트로 처리한다"""
        user.points = 5000
        user.save(update_fields=["points"])
        order = _stalled_order(order_factory, user, product, quantity=2, used_points=1000)
        _age_order(order, minutes=6)

        republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        user.refresh_from_db()
        assert user.points == 4000

    def test_recent_order_is_left_alone(self, user, product, order_factory):
        """기준 시간이 안 지난 주문은 원래 메시지가 아직 처리 중일 수 있어 건드리지 않는다"""
        order = _stalled_order(order_factory, user, product)
        _age_order(order, minutes=4)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["candidates"] == 0
        order.refresh_from_db()
        assert order.status == "pending"

    def test_processed_order_is_not_a_candidate(self, user, product, order_factory):
        """OrderItem 이 있는 주문(워커가 처리함)은 대상이 아니다"""
        order = _stalled_order(order_factory, user, product)
        OrderItem.objects.create(order=order, product=product, product_name=product.name, quantity=2, price=product.price)
        _age_order(order, minutes=6)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["candidates"] == 0

    def test_order_past_the_payment_window_is_closed_and_cart_restored(self, user, product, order_factory):
        """결제 만료 시간이 지나도 처리되지 못한 주문 → failed + 실패 사유 + 장바구니 복구.
        재고·포인트는 차감된 적이 없으니 그대로다"""
        user.points = 5000
        user.save(update_fields=["points"])
        initial_stock = product.stock
        order = _stalled_order(order_factory, user, product, used_points=1000)
        _age_order(order, minutes=31)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["abandoned"] == 1
        assert result["republished"] == 0
        order.refresh_from_db()
        assert order.status == "failed"
        assert order.failure_reason == ORDER_STALLED_FAILURE_REASON
        assert order.cart.is_active is True
        assert order.cart.items.count() == 1
        product.refresh_from_db()
        assert product.stock == initial_stock
        user.refresh_from_db()
        assert user.points == 5000

    def test_close_rechecks_under_lock(self, user, product, order_factory, mocker):
        """후보 조회와 잠금 사이에 워커가 처리를 끝낸 주문은 닫지 않는다"""
        from shopping.tasks import order_tasks

        order = _stalled_order(order_factory, user, product)
        _age_order(order, minutes=31)
        original = order_tasks._abandon_stalled_order

        def worker_finishes_first(order_id):
            process_order_heavy_tasks(order_id=order_id, cart_id=order.cart_id)
            return original(order_id)

        mocker.patch.object(order_tasks, "_abandon_stalled_order", side_effect=worker_finishes_first)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["abandoned"] == 0
        assert result["skipped"] == 1
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_uses_settings_by_default(self, settings, user, product, order_factory):
        """인자를 안 주면 settings.ORDER_REPUBLISH_AFTER_MINUTES / ORDER_PAYMENT_TIMEOUT_MINUTES 를 쓴다"""
        settings.ORDER_REPUBLISH_AFTER_MINUTES = 2
        settings.ORDER_PAYMENT_TIMEOUT_MINUTES = 30
        order = _stalled_order(order_factory, user, product)
        _age_order(order, minutes=3)

        result = republish_stalled_orders()

        assert result["republished"] == 1
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_one_failure_does_not_block_the_rest(self, user, product, product_factory, order_factory, mocker):
        """한 주문의 발행이 예외를 내도 나머지는 발행된다"""
        other = product_factory(name="다른 상품", sku="TEST-002", stock=10)
        failing = _stalled_order(order_factory, user, product)
        healthy = _stalled_order(order_factory, user, other)
        for order in (failing, healthy):
            _age_order(order, minutes=6)
        initial_stock = product.stock
        original_delay = process_order_heavy_tasks.delay

        def delay_or_blow_up(order_id, **kwargs):
            if order_id == failing.id:
                raise ConnectionError("broker down")
            return original_delay(order_id=order_id, **kwargs)

        mocker.patch.object(process_order_heavy_tasks, "delay", side_effect=delay_or_blow_up)

        result = republish_stalled_orders(stall_minutes=5, give_up_minutes=30)

        assert result["republished"] == 1
        assert len(result["errors"]) == 1
        assert f"order_id={failing.id}" in result["errors"][0]
        failing.refresh_from_db()
        healthy.refresh_from_db()
        assert failing.status == "pending"
        assert healthy.status == "confirmed"
        assert Product.objects.get(pk=product.pk).stock == initial_stock


class TestRepublishStalledOrdersSchedule:
    """Beat 스케줄에 등록되어 있어야 실제로 돈다"""

    def test_beat_schedule_registers_the_task(self):
        from myproject.celery import app

        entry = app.conf.beat_schedule["republish-stalled-orders"]

        assert entry["task"] == "shopping.tasks.order_tasks.republish_stalled_orders"
        assert entry["options"]["queue"] == "order_processing"
