"""
Integration 테스트 전용 Fixture

통합 테스트 및 동시성 테스트에서 사용하는 fixture를 정의합니다.

사용 가능한 전역 fixture (tests/conftest.py):
- api_client: DRF APIClient
- user: 기본 사용자 (이메일 인증 완료, 포인트 5000)
- user_factory: 사용자 생성 팩토리
- authenticated_client: 인증된 클라이언트
- product: 기본 상품 (10,000원, 재고 10)
- category: 기본 카테고리
- order: confirmed 상태 기본 주문
- payment: ready 상태 결제
"""

from decimal import Decimal
from datetime import timedelta

import pytest
from django.db.models import F
from django.utils import timezone

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.point import PointHistory
from shopping.models.product import Product
from shopping.models.user import User


# ==========================================
# 1. 배송 정보 Fixture
# ==========================================


@pytest.fixture
def default_shipping_info():
    """
    기본 배송 정보 딕셔너리

    모든 테스트에서 공통으로 사용하는 배송 정보
    - Order 생성 시 **kwargs로 전달 가능
    """
    return {
        "shipping_name": "홍길동",
        "shipping_phone": "010-1234-5678",
        "shipping_postal_code": "12345",
        "shipping_address": "서울시 강남구 테스트로 123",
        "shipping_address_detail": "101동 202호",
    }


@pytest.fixture
def remote_shipping_data():
    """
    도서산간 배송지 정보 (제주)

    추가 배송비 테스트용
    """
    return {
        "shipping_name": "김제주",
        "shipping_phone": "010-6300-0000",
        "shipping_postal_code": "63000",
        "shipping_address": "제주특별자치도 제주시 연동",
        "shipping_address_detail": "301호",
        "order_memo": "제주 배송 테스트",
    }


# ==========================================
# 2. 주문 생성 헬퍼 Fixture
# ==========================================


@pytest.fixture
def create_order(db, default_shipping_info):
    """
    Order 생성 헬퍼 함수 (매개변수화)

    복잡한 Order 생성 로직을 간소화
    - 단일/다중 상품 지원
    - 배송 정보 자동 적용
    - OrderItem 자동 생성

    Usage:
        # 단일 상품 주문
        order = create_order(user=user, product=product)

        # 포인트 사용 주문
        order = create_order(user=user, product=product, used_points=2000)

        # 다중 상품 주문
        order = create_order(user=user, products=[p1, p2], quantities=[2, 3])

        # 커스텀 배송 정보
        order = create_order(user=user, product=product, shipping_name="박영희")
    """
    created_orders = []

    def _create_order(
        user,
        product=None,
        products=None,
        quantities=None,
        status="confirmed",
        used_points=0,
        earned_points=0,
        payment_method=None,
        order_number=None,
        **kwargs,
    ):
        # total_amount 계산
        if products:
            quantities = quantities or [1] * len(products)
            total_amount = sum(p.price * q for p, q in zip(products, quantities))
        elif product:
            quantity = kwargs.pop("quantity", 1)
            total_amount = product.price * quantity
            quantities = [quantity]
        else:
            total_amount = kwargs.get("total_amount", Decimal("10000"))

        final_amount = total_amount - used_points

        # 배송 정보 병합
        shipping_data = default_shipping_info.copy()
        shipping_data.update(kwargs)

        # payment_method 기본값 설정 (NOT NULL 제약 대응)
        if payment_method is None:
            if status in ["paid", "shipped", "delivered"]:
                payment_method = "card"
            else:
                payment_method = ""

        # Order 생성
        order_data = {
            "user": user,
            "status": status,
            "total_amount": total_amount,
            "used_points": used_points,
            "earned_points": earned_points,
            "final_amount": final_amount,
            "order_number": order_number,
            "payment_method": payment_method,
        }

        order_data.update(shipping_data)
        order = Order.objects.create(**order_data)

        # OrderItem 생성
        if products:
            for p, qty in zip(products, quantities):
                OrderItem.objects.create(
                    order=order,
                    product=p,
                    product_name=p.name,
                    quantity=qty,
                    price=p.price,
                )
        elif product:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                quantity=quantities[0],
                price=product.price,
            )

        created_orders.append(order)
        return order

    yield _create_order

    # Cleanup
    for order in reversed(created_orders):
        try:
            if Order.objects.filter(pk=order.pk).exists():
                order.delete()
        except Exception:
            pass


# ==========================================
# 3. Toss 응답 빌더 Fixture
# ==========================================


@pytest.fixture
def toss_response_builder():
    """
    Toss API 응답 빌더

    커스터마이징 가능한 Toss 결제 승인 응답 생성

    Usage:
        response = toss_response_builder()
        response = toss_response_builder(amount=50000)
        response = toss_response_builder(method="가상계좌")
    """
    import uuid

    def _build(
        status="DONE",
        payment_key=None,
        order_id="ORDER_001",
        amount=10000,
        method="카드",
        card_issuer_code="41",
        approved_at="2025-01-15T10:00:00+09:00",
        **kwargs,
    ):
        if payment_key is None:
            payment_key = f"test_key_{uuid.uuid4().hex[:16]}"

        base_response = {
            "status": status,
            "paymentKey": payment_key,
            "orderId": order_id,
            "totalAmount": amount,
            "method": method,
            "approvedAt": approved_at,
        }

        if method == "카드":
            base_response["card"] = {
                "issuerCode": card_issuer_code,
                "number": "1234****",
                "installmentPlanMonths": 0,
                "isInterestFree": False,
            }

        base_response.update(kwargs)
        return base_response

    return _build


@pytest.fixture
def toss_cancel_response_builder():
    """
    Toss 취소 응답 빌더

    Usage:
        response = toss_cancel_response_builder()
        response = toss_cancel_response_builder(canceled_amount=5000)
    """
    import uuid

    def _build(
        payment_key=None,
        order_id="ORDER_001",
        canceled_amount=None,
        cancel_reason="고객 변심",
        canceled_at="2025-01-15T11:00:00+09:00",
        **kwargs,
    ):
        if payment_key is None:
            payment_key = f"test_key_{uuid.uuid4().hex[:16]}"

        response = {
            "status": "CANCELED",
            "paymentKey": payment_key,
            "orderId": order_id,
            "cancelReason": cancel_reason,
            "canceledAt": canceled_at,
        }

        if canceled_amount is not None:
            response["canceledAmount"] = canceled_amount

        response.update(kwargs)
        return response

    return _build


# ==========================================
# 4. 결제 요청 빌더 Fixture
# ==========================================


@pytest.fixture
def build_confirm_request():
    """
    결제 승인 요청 데이터 빌더

    Usage:
        request_data = build_confirm_request(payment_obj, payment_key)
        request_data = build_confirm_request(payment_obj)  # 자동 키 생성
    """

    def _build(payment_obj, payment_key=None):
        if payment_key is None:
            payment_key = f"test_key_{payment_obj.id}"

        return {
            "order_id": payment_obj.order.id,
            "payment_key": payment_key,
            "amount": int(payment_obj.amount),
        }

    return _build


@pytest.fixture
def build_payment_key():
    """
    테스트용 고유 payment_key 생성 헬퍼

    Usage:
        payment_key = build_payment_key(payment_obj)
    """

    def _build(payment_obj):
        return f"test_key_{payment_obj.id}"

    return _build


# ==========================================
# 5. 재고/판매량 조작 Fixture
# ==========================================


@pytest.fixture
def adjust_stock(db):
    """
    재고/판매량 조작 헬퍼

    F() 표현식을 사용하여 race condition 방지

    Usage:
        adjust_stock(product, stock_delta=-2, sold_delta=2)
        adjust_stock(product, stock_delta=1, sold_delta=-1)
    """

    def _adjust(product, stock_delta=0, sold_delta=0):
        updates = {}
        if stock_delta != 0:
            updates["stock"] = F("stock") + stock_delta
        if sold_delta != 0:
            updates["sold_count"] = F("sold_count") + sold_delta

        if updates:
            Product.objects.filter(pk=product.pk).update(**updates)
            product.refresh_from_db()

        return product

    return _adjust


@pytest.fixture
def sku_generator():
    """
    고유 SKU 생성기

    Usage:
        sku1 = sku_generator()  # TEST-000001
        sku2 = sku_generator("PROD")  # PROD-000002
    """
    counter = {"value": 0}

    def _generate(prefix="TEST"):
        counter["value"] += 1
        return f"{prefix}-{counter['value']:06d}"

    return _generate


# ==========================================
# 6. 사용자 관련 Fixture (동시성 테스트용)
# ==========================================


@pytest.fixture
def user_with_high_points(db):
    """
    많은 포인트 보유 사용자 (50,000P)

    전액 포인트 결제 테스트용
    """
    return User.objects.create_user(
        username="richuser_integration",
        email="richuser_integration@example.com",
        password="testpass123",
        phone_number="010-9999-8888",
        points=50000,
        is_email_verified=True,
    )


@pytest.fixture
def other_user(db):
    """
    다른 사용자

    권한 테스트용
    """
    return User.objects.create_user(
        username="otheruser_integration",
        email="other_integration@example.com",
        password="testpass123",
        phone_number="010-9999-9999",
        is_email_verified=True,
    )


# ==========================================
# 7. 결제 완료 주문/Payment Fixture
# ==========================================


@pytest.fixture
def paid_payment(db, paid_order):
    """
    결제 완료된 Payment (취소 테스트용)

    Dependencies:
        - paid_order: tests/conftest.py의 전역 fixture 사용

    Returns:
        - Payment: done 상태
        - Order: paid 상태
        - earned_points 설정 (1% 적립)
        - PointHistory 생성
    """
    # earned_points 계산 및 설정
    earned_points = int(paid_order.total_amount * Decimal("0.01"))
    paid_order.earned_points = earned_points
    paid_order.save()

    # User에게 포인트 실제 적립
    user = paid_order.user
    user.points += earned_points
    user.save()

    # PointHistory 생성
    PointHistory.objects.create(
        user=user,
        points=earned_points,
        balance=user.points,
        type="earn",
        order=paid_order,
        description=f"주문 #{paid_order.order_number} 구매 적립",
        expires_at=timezone.now() + timedelta(days=365),
        metadata={
            "order_id": paid_order.id,
            "order_number": paid_order.order_number,
            "payment_amount": str(paid_order.total_amount),
            "earn_rate": "1%",
        },
    )

    # Payment 생성
    payment = Payment.objects.create(
        order=paid_order,
        amount=paid_order.total_amount,
        status="done",
        toss_order_id=str(paid_order.id),
        payment_key="test_payment_key_paid",
        method="카드",
        approved_at=timezone.now(),
        card_company="신한카드",
        card_number="1234****",
    )

    return payment


# ==========================================
# 8. 인증 헬퍼 Fixture
# ==========================================


@pytest.fixture
def authenticate_as(api_client):
    """
    특정 사용자로 인증

    Usage:
        client = authenticate_as(user)
    """
    from rest_framework_simplejwt.tokens import AccessToken

    def _auth(user):
        token = AccessToken.for_user(user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return api_client

    return _auth
