"""
Payment 테스트 전용 Fixture

전역 conftest.py의 fixture는 그대로 사용하고,
payment 테스트에만 필요한 특화된 fixture를 정의합니다.

사용 가능한 전역 fixture:
- api_client: DRF APIClient
- user: 기본 사용자 (이메일 인증 완료, 포인트 5000)
- unverified_user: 이메일 미인증 사용자
- authenticated_client: 인증된 클라이언트
- product: 기본 상품 (10,000원, 재고 10)
- multiple_products: 여러 상품 리스트
- order: pending 상태 기본 주문
- paid_order: 결제 완료된 주문
- payment: pending 상태 결제
"""

from decimal import Decimal

import pytest
from django.db.models import F

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
from shopping.models.product import Product


# ==========================================
# 1. 주문 관련 Fixture
# ==========================================


@pytest.fixture
def order_with_multiple_items(db, user, category):
    """
    여러 상품이 포함된 주문 (주문명 테스트용)

    - 상품 3개 포함
    - 총 금액: 60,000원
    - pending 상태
    """
    # 여러 상품 생성 (고유한 SKU 할당)
    products = [
        Product.objects.create(
            name=f"테스트 상품 {i+1}",
            category=category,
            price=Decimal("10000") * (i + 1),
            stock=10,
            sku=f"TEST-MULTI-{i+1:03d}",  # 고유한 SKU 추가
            is_active=True,
        )
        for i in range(3)
    ]

    total = sum(p.price for p in products)
    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=total,
        final_amount=total,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000001",
    )

    for product in products:
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

    return order


@pytest.fixture
def order_with_points(db, user, product):
    """
    포인트 사용 주문

    - total_amount: 10,000원
    - used_points: 2,000P
    - final_amount: 8,000원
    """
    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        used_points=2000,
        final_amount=product.price - Decimal("2000"),
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000002",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    return order


@pytest.fixture
def order_with_long_product_name(db, user, category):
    """
    긴 상품명을 가진 주문

    - 상품명 100자 이상
    - pending 상태
    """
    long_name = "아주 긴 상품명 테스트 " * 10  # 100자 이상

    product = Product.objects.create(
        name=long_name,
        category=category,
        price=Decimal("10000"),
        stock=10,
        sku="TEST-LONG-NAME-001",  # 고유한 SKU 추가
        is_active=True,
    )

    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000003",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    return order


@pytest.fixture
def paid_order_with_payment(db, user, product):
    """
    이미 결제 완료된 주문 + Payment

    - 주문: paid 상태
    - Payment: done 상태, is_paid=True
    """
    from django.utils import timezone

    order = Order.objects.create(
        user=user,
        status="paid",
        total_amount=product.price,
        final_amount=product.price,
        payment_method="card",
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000004",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    payment = Payment.objects.create(
        order=order,
        amount=order.total_amount,
        status="done",
        toss_order_id=order.id,
        payment_key="test_payment_key_already_paid",
        method="카드",
        approved_at=timezone.now(),
    )

    return order


@pytest.fixture
def paid_payment(db, paid_order):
    """
    결제 완료된 Payment (취소 테스트용)

    - Payment: done 상태, is_paid=True
    - Order: paid 상태 (재고 차감, sold_count 증가 완료)
    - earned_points 설정 (1% 적립)
    - User에게 포인트 실제 적립
    - PointHistory 생성 (FIFO 차감을 위해 필수)
    """
    from django.utils import timezone
    from datetime import timedelta
    from shopping.models.point import PointHistory

    # earned_points 계산 및 설정 (1% 적립)
    earned_points = int(paid_order.total_amount * Decimal("0.01"))
    paid_order.earned_points = earned_points
    paid_order.save()

    # User에게 포인트 실제 적립 (취소 시 차감 가능하도록)
    user = paid_order.user
    user.points += earned_points
    user.save()

    # PointHistory 생성 (FIFO 차감을 위해 필수)
    # 실제 결제 승인 flow에서 생성되는 것과 동일한 형태로 생성
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


@pytest.fixture
def canceled_order(db, user, product):
    """
    취소된 주문

    - 주문: canceled 상태
    - pending이 아니므로 결제 불가
    """
    order = Order.objects.create(
        user=user,
        status="canceled",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000005",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    return order


@pytest.fixture
def order_with_existing_payment(db, user, product):
    """
    기존 Payment가 있는 주문 (재시도 테스트용)

    - 주문: pending
    - 기존 Payment: ready 상태 (미완료)
    """
    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000006",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    # 기존 Payment (미완료)
    old_payment = Payment.objects.create(
        order=order,
        amount=order.total_amount,
        status="ready",
        toss_order_id=order.id,
    )

    return order


# ==========================================
# 2. 다른 사용자 Fixture
# ==========================================


@pytest.fixture
def other_user(db):
    """
    다른 사용자

    권한 테스트용 - 다른 사용자의 주문 접근 차단 확인
    """
    from shopping.models.user import User

    return User.objects.create_user(
        username="otheruser",
        email="other@example.com",
        password="testpass123",
        phone_number="010-9999-9999",
        is_email_verified=True,
    )


@pytest.fixture
def other_user_order(db, other_user, product):
    """
    다른 사용자의 주문

    - 주문: pending
    - 소유자: other_user
    """
    order = Order.objects.create(
        user=other_user,
        status="confirmed",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="김철수",
        shipping_phone="010-8888-8888",
        shipping_postal_code="54321",
        shipping_address="부산시 해운대구 테스트로 456",
        shipping_address_detail="202동 303호",
        order_number="20250115000007",
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    return order


# ==========================================
# 3. 토스페이먼츠 전용 Fixture
# ==========================================


@pytest.fixture
def toss_client():
    """
    토스페이먼츠 클라이언트 인스턴스

    - TossPaymentClient() 생성
    - settings 값 자동 로드
    """
    from shopping.utils.toss_payment import TossPaymentClient

    return TossPaymentClient()


@pytest.fixture
def toss_success_response():
    """
    토스 API 성공 응답 템플릿

    - status: DONE
    - method: 카드
    - approvedAt 포함
    """
    return {
        "paymentKey": "test_payment_key_123",
        "orderId": "ORDER_20250115_001",
        "status": "DONE",
        "totalAmount": 13000,
        "method": "카드",
        "approvedAt": "2025-01-15T10:00:00+09:00",
        "card": {
            "company": "신한카드",
            "number": "1234****",
            "installmentPlanMonths": 0,
            "isInterestFree": False,
        },
    }


@pytest.fixture
def toss_error_response():
    """
    토스 API 에러 응답 생성 함수

    Usage:
        error = toss_error_response("INVALID_CARD", "카드 정보가 잘못되었습니다")
    """

    def _make_error(code: str, message: str):
        return {"code": code, "message": message}

    return _make_error


@pytest.fixture
def toss_cancel_response():
    """
    토스 취소 성공 응답 템플릿

    - status: CANCELED
    - canceledAmount, cancelReason 포함
    """
    return {
        "paymentKey": "test_payment_key_123",
        "orderId": "ORDER_20250115_001",
        "status": "CANCELED",
        "canceledAmount": 13000,
        "cancelReason": "고객 변심",
        "canceledAt": "2025-01-15T11:00:00+09:00",
    }


@pytest.fixture
def toss_webhook_data():
    """
    토스 웹훅 요청 데이터 템플릿

    - eventType: PAYMENT.DONE
    - 서명 검증용 기본 데이터
    """
    return {
        "eventType": "PAYMENT.DONE",
        "data": {
            "paymentKey": "test_key_123",
            "orderId": "ORDER_001",
            "status": "DONE",
            "totalAmount": 10000,
        },
    }


@pytest.fixture
def mock_requests_response():
    """
    requests 라이브러리 Mock 헬퍼

    Usage:
        mock = mock_requests_response(200, {"status": "DONE"})
        mocker.patch("requests.post", return_value=mock)
    """
    from unittest.mock import Mock

    def _create_mock(status_code: int, json_data: dict):
        mock = Mock()
        mock.status_code = status_code
        mock.json.return_value = json_data
        return mock

    return _create_mock


# ==========================================
# 4. 배송 정보 Fixture
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
def alternative_shipping_info():
    """
    대안 배송 정보 (other_user용)

    다른 사용자의 주문 테스트 시 사용
    """
    return {
        "shipping_name": "김철수",
        "shipping_phone": "010-8888-8888",
        "shipping_postal_code": "54321",
        "shipping_address": "부산시 해운대구 테스트로 456",
        "shipping_address_detail": "202동 303호",
    }


# ==========================================
# 5. 동적 생성 헬퍼 Fixture
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
                payment_method = ""  # pending, canceled 등의 경우 빈 문자열

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

    # Cleanup: 생성된 주문들 삭제 (역순으로)
    # ProtectedError 방지를 위해 try-except 사용
    for order in reversed(created_orders):
        try:
            if Order.objects.filter(pk=order.pk).exists():
                order.delete()
        except Exception:
            # pytest-django가 트랜잭션을 롤백하므로 cleanup 실패는 무시
            pass


@pytest.fixture
def adjust_stock(db):
    """
    재고/판매량 조작 헬퍼

    F() 표현식을 사용하여 race condition 방지
    테스트에서 재고 차감/복구 시뮬레이션에 사용

    Usage:
        # 재고 차감, 판매량 증가
        adjust_stock(product, stock_delta=-2, sold_delta=2)

        # 재고 복구, 판매량 감소
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

    테스트 간 충돌 방지를 위한 고유 SKU 생성
    카운터 기반으로 순차적 증가

    Usage:
        sku1 = sku_generator()  # TEST-000001
        sku2 = sku_generator("PROD")  # PROD-000002
    """
    counter = {"value": 0}

    def _generate(prefix="TEST"):
        counter["value"] += 1
        return f"{prefix}-{counter['value']:06d}"

    return _generate


@pytest.fixture
def toss_response_builder():
    """
    Toss API 응답 빌더

    커스터마이징 가능한 Toss 결제 승인 응답 생성
    - 기본값 제공 (payment_key는 UUID로 자동 생성)
    - 부분 오버라이드 가능
    - 카드 정보 자동 생성

    Usage:
        # 기본 응답 (고유 payment_key 자동 생성)
        response = toss_response_builder()

        # 금액 커스터마이징
        response = toss_response_builder(amount=50000)

        # 결제 수단 변경
        response = toss_response_builder(method="가상계좌")

        # 전체 커스터마이징
        response = toss_response_builder(
            status="DONE",
            payment_key="custom_key",
            amount=30000,
            card_company="국민카드"
        )
    """
    import uuid

    def _build(
        status="DONE",
        payment_key=None,
        order_id="ORDER_001",
        amount=10000,
        method="카드",
        card_company="신한카드",
        approved_at="2025-01-15T10:00:00+09:00",
        **kwargs,
    ):
        # payment_key가 지정되지 않으면 고유한 UUID 생성
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

        # 카드 결제인 경우 카드 정보 추가
        if method == "카드":
            base_response["card"] = {
                "company": card_company,
                "number": "1234****",
                "installmentPlanMonths": 0,
                "isInterestFree": False,
            }

        # 추가 필드 병합
        base_response.update(kwargs)
        return base_response

    return _build


@pytest.fixture
def toss_cancel_response_builder():
    """
    Toss 취소 응답 빌더

    커스터마이징 가능한 Toss 결제 취소 응답 생성

    Usage:
        # 전체 취소 (고유 payment_key 자동 생성)
        response = toss_cancel_response_builder()

        # 부분 취소
        response = toss_cancel_response_builder(canceled_amount=5000)

        # 커스터마이징
        response = toss_cancel_response_builder(
            payment_key="custom_key",
            cancel_reason="상품 품절"
        )
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
        # payment_key가 지정되지 않으면 고유한 UUID 생성
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


@pytest.fixture
def build_payment_key():
    """
    테스트용 고유 payment_key 생성 헬퍼

    동시성 테스트에서 각 payment마다 고유한 키 생성

    Usage:
        payment_key = build_payment_key(payment_obj)
    """

    def _build(payment_obj):
        return f"test_key_{payment_obj.id}"

    return _build


@pytest.fixture
def build_confirm_request():
    """
    결제 승인 요청 데이터 빌더

    API 스펙에 맞는 request_data 구조 생성

    Usage:
        # payment_obj와 payment_key 사용
        request_data = build_confirm_request(payment_obj, payment_key)

        # payment_obj만 사용 (자동 키 생성)
        request_data = build_confirm_request(payment_obj)
    """

    def _build(payment_obj, payment_key=None):
        if payment_key is None:
            payment_key = f"test_key_{payment_obj.id}"

        return {
            "order_id": payment_obj.order.id,  # order.id로 변경 (정수)
            "payment_key": payment_key,
            "amount": int(payment_obj.amount),
        }

    return _build
