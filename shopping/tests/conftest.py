import glob
import os
import sys

# ==========================================
# Windows 인코딩 문제 해결 (psycopg2 + cp949 충돌)
# 반드시 다른 import보다 먼저 실행되어야 함
# ==========================================
if sys.platform == "win32":
    # PostgreSQL 클라이언트 인코딩을 UTF-8로 강제 설정
    os.environ.setdefault("PGCLIENTENCODING", "UTF8")
    # Python 표준 출력 인코딩 설정
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


# ==========================================
# 커버리지 데이터 자동 정리 (병렬 테스트 안정성)
# ==========================================
def pytest_configure(config):
    """테스트 세션 시작 전 이전 커버리지 데이터 정리."""
    # xdist worker가 아닌 master에서만 실행
    if not hasattr(config, "workerinput"):
        # 프로젝트 루트에서 .coverage 파일들 삭제
        root_dir = config.rootdir
        coverage_files = glob.glob(str(root_dir / ".coverage*"))
        for f in coverage_files:
            try:
                os.remove(f)
            except OSError:
                pass

from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Category, Product
from shopping.models.user import User
from shopping.services.order_service import OrderService
from shopping.services.shipping_service import ShippingService

# ==========================================
# 0. 테스트 상수 (Business Policy Constants)
# ==========================================

# 비즈니스 정책 상수 (서비스에서 import)
FREE_SHIPPING_THRESHOLD = ShippingService.FREE_SHIPPING_THRESHOLD
DEFAULT_SHIPPING_FEE = ShippingService.DEFAULT_SHIPPING_FEE
REMOTE_AREA_FEE = ShippingService.REMOTE_AREA_FEE
MIN_POINTS = OrderService.MIN_POINTS

# Fixture 기본값 상수
DEFAULT_USER_POINTS = 5000
DEFAULT_PRODUCT_PRICE = Decimal("10000")
DEFAULT_PRODUCT_STOCK = 10

# ==========================================
# 1. 전역 설정 (Session Scope)
# ==========================================


@pytest.fixture(scope="session", autouse=True)
def setup_celery_for_tests():
    """
    테스트 환경에서 Celery 동기 실행 설정

    Session scope: 전체 테스트 세션에서 한 번만 실행
    autouse: 자동으로 모든 테스트에 적용
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(scope="session", autouse=True)
def setup_throttle_for_tests():
    """
    테스트 환경에서 throttle rates를 매우 높게 설정

    동시성 테스트에서 rate limiting에 걸리지 않도록 설정
    Session scope: 전체 테스트 세션에서 한 번만 실행
    autouse: 자동으로 모든 테스트에 적용
    """
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        "login": "10000/min",
        "register": "10000/hour",
        "token_refresh": "10000/min",
        "password_reset": "10000/hour",
        "email_verification": "10000/min",
        "email_verification_resend": "10000/hour",
        "payment_request": "10000/min",
        "payment_confirm": "10000/min",
        "payment_cancel": "10000/min",
        "order_create": "10000/min",
        "order_cancel": "10000/min",
        "anon_global": "100000/hour",
        "user_global": "100000/hour",
        "webhook": "10000/min",
    }


@pytest.fixture(scope="session", autouse=True)
def setup_logging_for_tests():
    """
    테스트 환경에서 로그 propagation 활성화

    caplog가 로그를 캡처할 수 있도록 propagate=True로 설정
    Session scope: 전체 테스트 세션에서 한 번만 실행
    autouse: 자동으로 모든 테스트에 적용
    """
    import logging

    # shopping 앱의 주요 로거들 propagate 설정
    for logger_name in [
        "shopping.services",
        "shopping.services.order_service",
        "shopping.services.payment_service",
        "shopping.webhooks",
    ]:
        logger = logging.getLogger(logger_name)
        logger.propagate = True


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """
    데이터베이스 초기 설정

    PostgreSQL 환경에서 테스트 DB 설정 최적화
    Session scope로 DB 연결 비용 최소화
    """
    with django_db_blocker.unblock():
        # 필요한 경우 초기 데이터 설정
        pass


# ==========================================
# 2. API 클라이언트 Fixture
# ==========================================


@pytest.fixture
def api_client():
    """
    DRF APIClient 인스턴스

    REST API 테스트용 클라이언트
    Function scope: 매 테스트마다 새로운 클라이언트 생성
    """
    return APIClient()


# ==========================================
# 3. 사용자(User) Fixture
# ==========================================


@pytest.fixture
def user(db):
    """
    기본 일반 사용자 (이메일 인증 완료)

    - username: testuser
    - 포인트: DEFAULT_USER_POINTS (5000)
    - 이메일 인증: 완료
    """
    # 기존 user가 있다면 삭제 (중복 방지)
    User.objects.filter(username="testuser").delete()

    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
        phone_number="010-1234-5678",
        points=DEFAULT_USER_POINTS,
        is_email_verified=True,
    )


@pytest.fixture
def seller_user(db):
    """
    판매자 사용자

    상품 등록/관리 권한을 가진 사용자
    """
    return User.objects.create_user(
        username="seller",
        email="seller@example.com",
        password="sellerpass123",
        phone_number="010-9999-8888",
        is_email_verified=True,
        is_seller=True,  # 판매자 권한 부여
    )


@pytest.fixture
def unverified_user(db):
    """
    이메일 미인증 사용자

    결제 등 일부 기능 사용 제한
    """
    return User.objects.create_user(
        username="unverified",
        email="unverified@example.com",
        password="testpass123",
        is_email_verified=False,
    )


@pytest.fixture
def inactive_user(db):
    """
    비활성화된 사용자 (is_active=False)

    관리자가 계정을 정지시킨 경우를 시뮬레이션
    로그인 시도 시 "비활성화된 계정입니다." 에러 발생

    사용 예시:
        def test_login_inactive(api_client, inactive_user):
            response = api_client.post(login_url, {
                "username": "inactive_user",
                "password": "testpass123"
            })
            assert response.status_code == 400
    """
    return User.objects.create_user(
        username="inactive_user",
        email="inactive@example.com",
        password="testpass123",
        phone_number="010-5555-5555",
        is_active=False,  # 계정 비활성화
        is_email_verified=True,
    )


@pytest.fixture
def withdrawn_user(db):
    """
    탈퇴한 사용자 (is_withdrawn=True)

    회원 탈퇴 처리가 완료된 사용자
    로그인 시도 시 "탈퇴한 회원입니다." 에러 발생

    주의: 탈퇴 시 is_active도 False로 변경됨

    사용 예시:
        def test_login_withdrawn(api_client, withdrawn_user):
            response = api_client.post(login_url, {
                "username": "withdrawn_user",
                "password": "testpass123"
            })
            assert response.status_code == 400
    """
    user = User.objects.create_user(
        username="withdrawn_user",
        email="withdrawn@example.com",
        password="testpass123",
        phone_number="010-6666-6666",
        is_email_verified=True,
    )
    # 탈퇴 처리
    user.is_withdrawn = True
    user.withdrawn_at = timezone.now()
    user.is_active = False  # 탈퇴 시 계정도 비활성화
    user.save()
    return user


@pytest.fixture
def user_factory(db):
    """
    User Factory - 유연한 사용자 생성

    사용 예시:
        user1 = user_factory()  # 기본값
        user2 = user_factory(username="custom", points=10000)  # 커스텀
        user3 = user_factory(is_email_verified=False)  # 미인증
    """
    import uuid

    def _create_user(**kwargs):
        unique_id = uuid.uuid4().hex[:8]
        defaults = {
            "username": f"testuser_{unique_id}",
            "email": f"test_{unique_id}@example.com",
            "password": "testpass123",
            "phone_number": f"010-{unique_id[:4]}-{unique_id[4:8]}",
            "points": DEFAULT_USER_POINTS,
            "is_email_verified": True,
        }
        # kwargs로 받은 값으로 defaults 덮어쓰기
        defaults.update(kwargs)

        # username이 중복될 수 있으므로 카운터 추가
        username = defaults.pop("username")
        counter = 1
        original_username = username
        while User.objects.filter(username=username).exists():
            username = f"{original_username}{counter}"
            counter += 1

        # email이 중복될 수 있으므로 카운터 추가
        email = defaults.pop("email")
        counter = 1
        original_email = email
        while User.objects.filter(email=email).exists():
            email_parts = original_email.split("@")
            email = f"{email_parts[0]}{counter}@{email_parts[1]}"
            counter += 1

        return User.objects.create_user(username=username, email=email, **defaults)

    return _create_user


# ==========================================
# 4. 인증 관련 Fixture
# ==========================================


@pytest.fixture
def authenticated_client(api_client, user):
    """
    인증된 APIClient (가장 많이 사용)

    JWT 토큰이 설정된 클라이언트
    대부분의 API 테스트에서 사용
    """
    response = api_client.post(
        reverse("auth-login"),
        {"username": "testuser", "password": "testpass123"},
    )
    # 새 API 응답 구조: {"token": {"access": "..."}}
    token = response.json()["token"]["access"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def seller_authenticated_client(api_client, seller_user):
    """
    판매자 인증 클라이언트

    상품 등록/관리 API 테스트용
    """
    response = api_client.post(
        reverse("auth-login"),
        {"username": "seller", "password": "sellerpass123"},
    )
    # 새 API 응답 구조: {"token": {"access": "..."}}
    token = response.json()["token"]["access"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def get_tokens(api_client, user):
    """
    JWT 토큰 발급 헬퍼

    access, refresh 토큰을 dict로 반환

    Note:
        새 API에서는 refresh token이 HTTP Only Cookie로 전달되므로,
        테스트에서는 Cookie에서 가져옵니다.
        Cookie가 없는 경우 로그인 API에서 정상적으로 발급되지 않은 것입니다.
    """
    response = api_client.post(
        reverse("auth-login"),
        {"username": "testuser", "password": "testpass123"},
    )

    # Access token은 response body에서 가져옴
    access_token = response.json()["token"]["access"]

    # Refresh token은 Cookie에서 가져옴
    refresh_cookie = response.cookies.get("refresh_token")
    if refresh_cookie:
        refresh_token = refresh_cookie.value
    else:
        # Cookie가 없으면 에러 - 로그인 API가 Cookie를 설정하지 않음
        raise ValueError("로그인 응답에 refresh_token Cookie가 없습니다. auth_views.py를 확인하세요.")

    return {"access": access_token, "refresh": refresh_token}


# ==========================================
# 5. 카테고리/상품 Fixture
# ==========================================


@pytest.fixture
def category(db):
    """
    기본 카테고리

    대부분의 상품 테스트에서 사용
    """
    # 기존 category가 있다면 삭제 (중복 방지)
    Category.objects.filter(slug="test-category").delete()

    return Category.objects.create(
        name="테스트 카테고리",
        slug="test-category",
    )


@pytest.fixture
def product(db, category, seller_user):
    """
    기본 테스트 상품 (재고 있음)

    - 가격: DEFAULT_PRODUCT_PRICE (10,000원)
    - 재고: DEFAULT_PRODUCT_STOCK (10개)
    - 판매중
    """
    return Product.objects.create(
        name="테스트 상품",
        slug="test-product",
        category=category,
        seller=seller_user,
        price=DEFAULT_PRODUCT_PRICE,
        stock=DEFAULT_PRODUCT_STOCK,
        sku="TEST-001",
        description="테스트 상품 설명",
        is_active=True,
    )


@pytest.fixture
def out_of_stock_product(db, category, seller_user):
    """
    품절 상품

    재고 부족 시나리오 테스트용
    """
    return Product.objects.create(
        name="품절 상품",
        slug="out-of-stock",
        category=category,
        seller=seller_user,
        price=Decimal("5000"),
        stock=0,  # 품절
        sku="TEST-003",
        is_active=True,
    )


@pytest.fixture
def inactive_product(db, category, seller_user):
    """
    판매 중단 상품

    비활성 상품 테스트용
    """
    return Product.objects.create(
        name="판매중단 상품",
        slug="inactive-product",
        category=category,
        seller=seller_user,
        price=Decimal("15000"),
        stock=10,
        sku="TEST-004",
        is_active=False,  # 판매 중단
    )


@pytest.fixture
def product_factory(db, category, seller_user):
    """
    Product Factory - 유연한 상품 생성

    사용 예시:
        product1 = product_factory()  # 기본값
        product2 = product_factory(price=Decimal("20000"), stock=5)
        product3 = product_factory(name="커스텀 상품", sku="CUSTOM-001")
    """

    def _create_product(**kwargs):
        defaults = {
            "name": "테스트 상품",
            "slug": "test-product",
            "category": category,
            "seller": seller_user,
            "price": DEFAULT_PRODUCT_PRICE,
            "stock": DEFAULT_PRODUCT_STOCK,
            "sku": "TEST-001",
            "description": "테스트 상품 설명",
            "is_active": True,
        }
        defaults.update(kwargs)

        # slug 중복 방지
        slug = defaults.pop("slug")
        counter = 1
        original_slug = slug
        while Product.objects.filter(slug=slug).exists():
            slug = f"{original_slug}-{counter}"
            counter += 1

        return Product.objects.create(slug=slug, **defaults)

    return _create_product


@pytest.fixture
def multiple_products(db, category, seller_user):
    """
    여러 상품 (리스트 테스트용)

    3개의 상품 리스트 반환
    가격: 10,000 / 20,000 / 30,000
    """
    return [
        Product.objects.create(
            name=f"상품 {i}",
            slug=f"product-{i}",
            category=category,
            seller=seller_user,
            price=Decimal(str(10000 * i)),
            stock=10,
            sku=f"MULTI-{i:03d}",
            is_active=True,
        )
        for i in range(1, 4)
    ]


# ==========================================
# 6. 장바구니/주문 Fixture
# ==========================================


@pytest.fixture
def cart(db, user):
    """
    빈 장바구니

    활성 상태의 빈 장바구니
    """
    return Cart.objects.create(user=user, is_active=True)


@pytest.fixture
def cart_with_items(db, user, product):
    """
    상품이 담긴 장바구니

    기본 상품 1개가 담긴 장바구니
    """
    cart = Cart.objects.create(user=user, is_active=True)
    CartItem.objects.create(cart=cart, product=product, quantity=1)
    return cart


@pytest.fixture
def shipping_data():
    """
    기본 배송 정보 (dict)

    주문 생성 시 사용하는 배송 정보
    """
    return {
        "shipping_name": "홍길동",
        "shipping_phone": "010-9999-8888",
        "shipping_postal_code": "12345",
        "shipping_address": "서울시 강남구 테스트로 123",
        "shipping_address_detail": "101동 202호",
        "order_memo": "부재시 경비실에 맡겨주세요",
    }


@pytest.fixture
def invalid_shipping_field_factory():
    """
    배송지 필드별 검증 테스트용 데이터 팩토리

    특정 필드만 빈 값으로 설정하여 유효성 검증 테스트에 사용

    사용 예시:
        invalid_data = invalid_shipping_field_factory("shipping_name")
        # {"shipping_name": "", "shipping_phone": "010-1234-5678", ...}
    """

    def _create_invalid_data(empty_field: str):
        base_data = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101호",
        }
        # 특정 필드만 빈 값으로 설정
        base_data[empty_field] = ""
        return base_data

    return _create_invalid_data


@pytest.fixture
def order_factory(db, user):
    """
    Order Factory - 유연한 주문 생성

    사용 예시:
        order1 = order_factory()  # 기본값 (pending 상태)
        order2 = order_factory(status="confirmed", total_amount=20000)
        order3 = order_factory(user=other_user, used_points=1000)
    """

    def _create_order(**kwargs):
        defaults = {
            "user": user,
            "status": "pending",
            "total_amount": Decimal("10000"),
            "final_amount": Decimal("10000"),
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101호",
        }
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    return _create_order


# ==========================================
# 8. 기타 유틸리티 Fixture
# ==========================================


@pytest.fixture
def freeze_time(mocker):
    """
    시간 고정 유틸리티

    시간 의존적인 테스트에 사용

    사용 예시:
        def test_with_time(freeze_time):
            frozen_time = datetime(2025, 1, 15, 10, 0, 0)
            freeze_time(frozen_time)
            # 테스트 코드
    """

    def _freeze(dt):
        return mocker.patch("django.utils.timezone.now", return_value=dt)

    return _freeze


# ==========================================
# 9. 주문/결제 Fixture (Order/Payment)
# ==========================================


@pytest.fixture
def order(db, user, product):
    """
    기본 주문 (confirmed 상태 - 결제 테스트용)

    - 상태: confirmed (주문 확정 - 결제 가능 상태)
    - 상품 1개 포함
    - 재고 차감 완료 (비동기 처리 완료 상태 시뮬레이션)
    """
    from django.db.models import F

    from shopping.models.order import Order, OrderItem

    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        final_amount=product.price,  # 포인트 적립을 위해 final_amount 설정
        shipping_name="홍길동",
        shipping_phone="010-9999-8888",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250122000001",  # 고유 주문번호
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    # 비동기 처리 완료 시뮬레이션: confirmed 상태는 재고가 이미 차감된 상태
    from shopping.models.product import Product

    Product.objects.filter(pk=product.pk).update(stock=F("stock") - 1)
    product.refresh_from_db()

    return order


@pytest.fixture
def paid_order(db, user, product):
    """
    결제 완료된 주문

    - 상태: paid (결제 완료)
    - 상품 1개 포함
    - 결제 흐름 시뮬레이션 (재고 차감 + sold_count 증가)
    """
    from django.db.models import F

    from shopping.models.order import Order, OrderItem

    order = Order.objects.create(
        user=user,
        status="paid",
        total_amount=product.price,
        final_amount=product.price,  # 포인트 적립을 위해 final_amount 설정
        payment_method="card",
        shipping_name="홍길동",
        shipping_phone="010-9999-8888",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250122000002",  # 고유 주문번호
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    # 결제 흐름 시뮬레이션
    # 1. 주문 생성 시: 재고 차감
    # 2. 결제 완료 시: sold_count 증가
    from shopping.models.product import Product

    Product.objects.filter(pk=product.pk).update(
        stock=F("stock") - 1,
        sold_count=F("sold_count") + 1,
    )

    # product 객체 갱신
    product.refresh_from_db()

    return order


@pytest.fixture
def order_with_multiple_items(db, user, multiple_products):
    """
    여러 상품이 포함된 주문 (주문명 테스트용)

    - multiple_products fixture의 상품 3개 사용
    - 총 금액: 60,000원 (10,000 + 20,000 + 30,000)
    - pending 상태 (비동기 처리 진행 중)
    """
    total = sum(p.price for p in multiple_products)
    order = Order.objects.create(
        user=user,
        status="pending",
        total_amount=total,
        final_amount=total,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250115000001",
    )

    for product in multiple_products:
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

    return order


@pytest.fixture
def pending_order(db, user, product):
    """
    pending 상태 주문 (취소 및 재고 테스트용)

    - 상태: pending (결제대기)
    - 상품 1개 포함 (수량: 1)
    - 재고 차감된 상태로 시뮬레이션
    """
    from django.db.models import F

    order = Order.objects.create(
        user=user,
        status="pending",
        total_amount=product.price,
        final_amount=product.price,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구 테스트로 123",
        shipping_address_detail="101동 202호",
        order_number="20250122000003",  # 고유 주문번호
    )

    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )

    # 재고 차감 시뮬레이션 (주문 생성 시 차감된 상태)
    Product.objects.filter(pk=product.pk).update(stock=F("stock") - 1)
    product.refresh_from_db()

    return order


@pytest.fixture
def payment(db, order):
    """
    결제 준비 상태 Payment (fail 테스트용)

    - 상태: ready (결제 준비)
    - Order: confirmed 상태
    """
    from shopping.models.payment import Payment

    return Payment.objects.create(
        order=order,
        amount=order.total_amount,
        status="ready",
        toss_order_id=str(order.id),
    )


@pytest.fixture
def canceled_payment(db, order):
    """
    취소된 결제

    - 상태: canceled (취소됨)
    """
    from django.utils import timezone

    from shopping.models.payment import Payment

    return Payment.objects.create(
        order=order,
        amount=order.total_amount,
        status="canceled",
        toss_order_id=str(order.id),
        canceled_at=timezone.now(),
    )


# ==========================================
# 10. 테스트 헬퍼 Fixture
# ==========================================


@pytest.fixture
def add_to_cart_helper(db):
    """
    장바구니에 상품 추가 헬퍼 함수

    사용 예시:
        add_to_cart_helper(user, product, quantity=2)
    """

    def _add_to_cart(user, product, quantity=1):
        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
        if not created:
            cart_item.quantity += quantity
            cart_item.save()
        else:
            cart_item.quantity = quantity
            cart_item.save()
        return cart, cart_item

    return _add_to_cart
