"""
주문 처리 태스크 테스트

Phase 2: Task 2-1에서 구현한 process_order_heavy_tasks 테스트
"""

import pytest

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order
from shopping.tasks.order_tasks import process_order_heavy_tasks


@pytest.mark.django_db(transaction=True)
class TestOrderTasksHappyPath:
    """주문 태스크 정상 케이스"""

    def test_process_order_heavy_tasks_success(
        self, user, product, category, seller_user
    ):
        """무거운 작업 처리가 성공적으로 완료됨

        - 재고 차감
        - OrderItem 생성
        - 장바구니 비우기
        - Order 상태를 confirmed로 변경
        """
        # Arrange: 장바구니와 주문 생성
        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            final_amount=product.price * 2,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act: 태스크 실행
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 결과 검증
        assert result["status"] == "success"
        assert result["order_id"] == order.id

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "confirmed"

        # OrderItem 생성 확인
        assert order.order_items.count() == 1
        order_item = order.order_items.first()
        assert order_item.product == product
        assert order_item.quantity == 2

        # 재고 차감 확인
        product.refresh_from_db()
        assert product.stock == initial_stock - 2

        # 장바구니 비우기 확인
        cart.refresh_from_db()
        assert cart.items.count() == 0

    def test_process_order_heavy_tasks_with_points(
        self, user, product
    ):
        """포인트 사용이 포함된 주문 처리가 성공함"""
        # Arrange
        user.points = 5000
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price,
            used_points=1000,
            final_amount=product.price - 1000,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=1000
        )

        # Assert
        assert result["status"] == "success"

        order.refresh_from_db()
        assert order.status == "confirmed"

        # 포인트 차감 확인
        user.refresh_from_db()
        assert user.points == 4000


@pytest.mark.django_db(transaction=True)
class TestOrderTasksBoundary:
    """주문 태스크 경계 케이스"""

    def test_already_processed_order_ignored(self, user, product):
        """이미 처리된 주문은 무시됨 (멱등성)"""
        # Arrange: 이미 confirmed 상태인 주문
        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="confirmed",  # 이미 처리됨
            total_amount=product.price,
            final_amount=product.price,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 이미 처리됨 응답
        assert result["status"] == "already_processed"
        assert result["order_id"] == order.id

        # 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_multiple_products_in_cart(
        self, user, product_factory
    ):
        """여러 상품이 담긴 장바구니 처리 성공"""
        # Arrange: 3개 상품
        products = [
            product_factory(name=f"상품{i}", sku=f"SKU-{i}", stock=10)
            for i in range(1, 4)
        ]

        cart = Cart.objects.create(user=user, is_active=True)
        for p in products:
            CartItem.objects.create(cart=cart, product=p, quantity=2)

        total = sum(p.price * 2 for p in products)
        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=total,
            final_amount=total,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert
        assert result["status"] == "success"
        assert order.order_items.count() == 3

        # 모든 상품의 재고 차감 확인
        for p in products:
            p.refresh_from_db()
            assert p.stock == 8  # 10 - 2


@pytest.mark.django_db(transaction=True)
class TestOrderTasksException:
    """주문 태스크 예외 케이스"""

    def test_insufficient_stock_fails_order(
        self, user, product
    ):
        """재고 부족 시 주문 실패 처리"""
        # Arrange: 재고가 1개인데 2개 주문
        product.stock = 1
        product.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            final_amount=product.price * 2,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=0
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_stock"
        assert product.name in result["product"]

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "재고 부족" in order.failure_reason

        # 재고는 변경되지 않음
        product.refresh_from_db()
        assert product.stock == 1

    def test_point_deduction_failure_rollback_stock(
        self, user, product
    ):
        """포인트 차감 실패 시 재고 롤백"""
        # Arrange: 포인트 부족
        user.points = 500
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=2)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price * 2,
            used_points=1000,  # 보유량보다 많음
            final_amount=product.price * 2 - 1000,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=1000
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # Order 상태 확인
        order.refresh_from_db()
        assert order.status == "failed"
        assert "포인트 사용 실패" in order.failure_reason

        # 재고가 롤백됨 (차감됐다가 다시 복구됨)
        product.refresh_from_db()
        assert product.stock == initial_stock

        # 포인트는 차감되지 않음
        user.refresh_from_db()
        assert user.points == 500

    def test_point_deduction_with_minimum_amount(
        self, user, product
    ):
        """최소 포인트 사용 금액(100) 미만은 실패"""
        # Arrange
        user.points = 50
        user.save()

        cart = Cart.objects.create(user=user, is_active=True)
        CartItem.objects.create(cart=cart, product=product, quantity=1)

        order = Order.objects.create(
            user=user,
            status="pending",
            total_amount=product.price,
            used_points=50,  # 최소 금액 미만
            final_amount=product.price - 50,
            shipping_name="홍길동",
            shipping_phone="010-1234-5678",
            shipping_postal_code="12345",
            shipping_address="서울시 강남구",
            shipping_address_detail="101호",
        )

        initial_stock = product.stock

        # Act
        result = process_order_heavy_tasks(
            order_id=order.id,
            cart_id=cart.id,
            use_points=50
        )

        # Assert: 실패 응답
        assert result["status"] == "failed"
        assert result["reason"] == "point_deduction_failed"

        # 재고가 롤백됨
        product.refresh_from_db()
        assert product.stock == initial_stock
# ==========================================
# expire_unpaid_orders — 미결제 주문 만료 (재고 반환)
# ==========================================

from datetime import timedelta

from django.utils import timezone
from shopping.constants import ORDER_EXPIRED_FAILURE_REASON
from shopping.models.order import OrderItem
from shopping.models.payment import Payment, PaymentLog
from shopping.models.product import Product
from shopping.tasks.order_tasks import expire_unpaid_orders


def _age_order(order, minutes: int) -> None:
    """created_at 은 auto_now_add 라 생성 후 직접 과거로 돌린다"""
    Order.objects.filter(pk=order.pk).update(
        created_at=timezone.now() - timedelta(minutes=minutes)
    )


def _order_holding_stock(order_factory, product, quantity: int = 2, **kwargs):
    """재고를 점유한 주문 (주문 생성 경로가 끝난 상태를 흉내: OrderItem 있음 + 재고 차감됨)"""
    order = order_factory(**kwargs)
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=quantity,
        price=product.price,
    )
    Product.objects.filter(pk=product.pk).update(stock=product.stock - quantity)
    return order


@pytest.mark.django_db(transaction=True)
class TestExpireUnpaidOrders:
    """결제 시간이 지난 미결제 주문은 취소되고 재고가 돌아온다"""

    def test_stale_pending_order_is_canceled_and_stock_restored(
        self, user, product, order_factory
    ):
        """시간이 지난 pending 주문 → canceled + 재고 복구 + 실패 사유 기록"""
        initial_stock = product.stock
        order = _order_holding_stock(
            order_factory, product, quantity=2, status="pending"
        )
        _age_order(order, minutes=31)

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["expired"] == 1
        assert result["errors"] == []
        order.refresh_from_db()
        assert order.status == "canceled"
        assert order.failure_reason == ORDER_EXPIRED_FAILURE_REASON
        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_stale_confirmed_order_refunds_used_points(
        self, user, product, order_factory
    ):
        """사용한 포인트가 있는 주문은 취소 시 포인트도 돌아온다 (cancel_order 경로 재사용)"""
        user.points = 5000
        user.save(update_fields=["points"])
        order = _order_holding_stock(
            order_factory,
            product,
            status="confirmed",
            used_points=1000,
            final_amount=product.price * 2 - 1000,
        )
        _age_order(order, minutes=31)

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["expired"] == 1
        user.refresh_from_db()
        assert user.points == 6000

    def test_recent_order_is_left_alone(self, product, order_factory):
        """시간이 안 지난 주문은 그대로"""
        order = _order_holding_stock(order_factory, product, status="pending")
        _age_order(order, minutes=29)
        stock_after_order = Product.objects.get(pk=product.pk).stock

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["candidates"] == 0
        order.refresh_from_db()
        assert order.status == "pending"
        assert Product.objects.get(pk=product.pk).stock == stock_after_order

    def test_uses_settings_timeout_by_default(self, settings, product, order_factory):
        """timeout_minutes 를 안 주면 settings.ORDER_PAYMENT_TIMEOUT_MINUTES 를 쓴다"""
        settings.ORDER_PAYMENT_TIMEOUT_MINUTES = 10
        order = _order_holding_stock(order_factory, product, status="pending")
        _age_order(order, minutes=11)

        result = expire_unpaid_orders()

        assert result["expired"] == 1
        order.refresh_from_db()
        assert order.status == "canceled"

    @pytest.mark.parametrize(
        "payment_status", ["in_progress", "waiting_for_deposit", "done"]
    )
    def test_order_with_live_payment_is_skipped(
        self, product, order_factory, payment_status
    ):
        """승인 진행 중·입금 대기·승인 완료 결제가 붙은 주문은 시간이 지나도 취소하지 않는다"""
        order = _order_holding_stock(order_factory, product, status="confirmed")
        payment = Payment.objects.create(
            order=order,
            toss_order_id=str(order.id),
            amount=order.final_amount,
            status=payment_status,
        )
        _age_order(order, minutes=31)
        stock_after_order = Product.objects.get(pk=product.pk).stock

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["expired"] == 0
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == "confirmed"
        assert payment.status == payment_status
        assert Product.objects.get(pk=product.pk).stock == stock_after_order

    @pytest.mark.parametrize("payment_status", ["ready", "aborted"])
    def test_dead_payment_is_fenced_to_expired(
        self, product, order_factory, payment_status
    ):
        """결제창을 열었다 떠났거나(ready) 승인이 실패한(aborted) 결제는 expired 로 바뀌어
        이후 승인 요청이 PaymentService 에서 거부된다"""
        order = _order_holding_stock(order_factory, product, status="confirmed")
        payment = Payment.objects.create(
            order=order,
            toss_order_id=str(order.id),
            amount=order.final_amount,
            status=payment_status,
        )
        _age_order(order, minutes=31)

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["expired"] == 1
        order.refresh_from_db()
        payment.refresh_from_db()
        assert order.status == "canceled"
        assert payment.status == "expired"
        log = PaymentLog.objects.get(payment=payment, log_type="cancel")
        assert log.data["previous_status"] == payment_status

    def test_pending_order_without_items_is_not_a_candidate(
        self, user, product, order_factory
    ):
        """비동기 경로에서 아직 재고를 깎지 않은 pending 주문(OrderItem 없음)은 대상이 아니다
        — 돌려놓을 재고가 없고, 취소하면 아직 차감되지 않은 포인트를 환불해 버린다"""
        user.points = 5000
        user.save(update_fields=["points"])
        order = order_factory(status="pending", used_points=1000)
        _age_order(order, minutes=31)

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["candidates"] == 0
        order.refresh_from_db()
        assert order.status == "pending"
        user.refresh_from_db()
        assert user.points == 5000

    def test_status_is_rechecked_under_lock(self, product, order_factory, mocker):
        """후보 조회와 잠금 사이에 결제가 끝난 주문은 잠근 뒤 재검증에서 걸러진다"""
        from shopping.tasks import order_tasks

        order = _order_holding_stock(order_factory, product, status="confirmed")
        _age_order(order, minutes=31)

        original = order_tasks._expire_order

        def pay_before_lock(order_id):
            # 후보로 뽑힌 뒤, 잠그기 직전에 결제가 완료된 상황
            Order.objects.filter(pk=order_id).update(
                status="paid", payment_method="card"
            )
            return original(order_id)

        mocker.patch.object(order_tasks, "_expire_order", side_effect=pay_before_lock)
        stock_after_order = Product.objects.get(pk=product.pk).stock

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["candidates"] == 1
        assert result["skipped"] == 1
        assert result["expired"] == 0
        order.refresh_from_db()
        assert order.status == "paid"
        assert Product.objects.get(pk=product.pk).stock == stock_after_order

    def test_one_failure_does_not_block_the_rest(
        self, product, product_factory, order_factory, mocker
    ):
        """한 주문의 취소가 예외를 내도 나머지는 처리되고, 실패한 주문은 그대로 남는다"""
        from shopping.services.order_service import OrderService

        other_product = product_factory(name="다른 상품", sku="TEST-002", stock=10)
        failing = _order_holding_stock(order_factory, product, status="pending")
        healthy = _order_holding_stock(order_factory, other_product, status="pending")
        for order in (failing, healthy):
            _age_order(order, minutes=31)
        stock_of_failing = Product.objects.get(pk=product.pk).stock
        initial_stock_of_healthy = other_product.stock

        original_cancel = OrderService.cancel_order

        def cancel_or_blow_up(order):
            if order.pk == failing.pk:
                raise RuntimeError("boom")
            return original_cancel(order)

        mocker.patch.object(OrderService, "cancel_order", side_effect=cancel_or_blow_up)

        result = expire_unpaid_orders(timeout_minutes=30)

        assert result["candidates"] == 2
        assert result["expired"] == 1
        assert len(result["errors"]) == 1
        assert f"order_id={failing.id}" in result["errors"][0]

        failing.refresh_from_db()
        healthy.refresh_from_db()
        assert failing.status == "pending"
        assert Product.objects.get(pk=product.pk).stock == stock_of_failing
        assert healthy.status == "canceled"
        assert (
            Product.objects.get(pk=other_product.pk).stock == initial_stock_of_healthy
        )


class TestExpireUnpaidOrdersSchedule:
    """Beat 스케줄에 등록되어 있어야 실제로 돈다"""

    def test_beat_schedule_registers_the_task(self):
        from myproject.celery import app

        entry = app.conf.beat_schedule["expire-unpaid-orders"]

        assert entry["task"] == "shopping.tasks.order_tasks.expire_unpaid_orders"
        assert entry["options"]["queue"] == "order_processing"
