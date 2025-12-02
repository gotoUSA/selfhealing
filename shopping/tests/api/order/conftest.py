"""
Order 테스트 전용 Fixture

전역 conftest.py의 fixture는 그대로 사용하고,
order 테스트에만 필요한 특화된 fixture를 정의합니다.

사용 가능한 전역 fixture:
- api_client: DRF APIClient
- user: 기본 사용자 (포인트 5000)
- seller_user: 판매자 사용자
- authenticated_client: 인증된 클라이언트
- category: 카테고리
- product: 기본 상품 (10,000원, 재고 10)
- out_of_stock_product: 품절 상품
- multiple_products: 여러 상품 리스트
- shipping_data: 기본 배송 정보
"""

from decimal import Decimal

from django.urls import reverse

import pytest

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Product
from shopping.models.user import User

# ==========================================
# 테스트용 비밀번호 상수
# ==========================================

TEST_USER_PASSWORD = "testpass123"
TEST_ADMIN_PASSWORD = "admin123"
TEST_SELLER_PASSWORD = "sellerpass123"

# ==========================================
# 1. 포인트 보유 사용자 Fixture
# ==========================================


@pytest.fixture
def user_with_points(db):
    """
    포인트 보유 사용자 (5,000P)

    기본 포인트 사용 테스트에 사용
    """
    return User.objects.create_user(
        username="pointuser",
        email="pointuser@example.com",
        password=TEST_USER_PASSWORD,
        phone_number="010-1234-5678",
        points=5000,
        is_email_verified=True,
    )


@pytest.fixture
def user_with_high_points(db):
    """
    많은 포인트 보유 사용자 (50,000P)

    전액 포인트 결제 테스트용
    """
    return User.objects.create_user(
        username="richuser",
        email="richuser@example.com",
        password=TEST_USER_PASSWORD,
        phone_number="010-9999-8888",
        points=50000,
        is_email_verified=True,
    )


@pytest.fixture
def user_no_points(db):
    """
    포인트 없는 사용자 (0P)

    포인트 부족 테스트용
    """
    return User.objects.create_user(
        username="nopoint",
        email="nopoint@example.com",
        password=TEST_USER_PASSWORD,
        phone_number="010-0000-0000",
        points=0,
        is_email_verified=True,
    )


@pytest.fixture
def unverified_user(db):
    """
    이메일 미인증 사용자

    주문 권한 테스트용
    """
    return User.objects.create_user(
        username="unverified",
        email="unverified@example.com",
        password=TEST_USER_PASSWORD,
        phone_number="010-5555-5555",
        is_email_verified=False,
    )


@pytest.fixture
def admin_user(db):
    """
    관리자 사용자

    모든 주문 조회/관리 권한 테스트용
    """
    return User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password=TEST_ADMIN_PASSWORD,
        is_staff=True,
        is_superuser=True,
        is_email_verified=True,
    )


@pytest.fixture
def other_user(db):
    """
    다른 일반 사용자

    권한 분리 테스트용 (본인 주문만 조회 가능 검증)
    """
    return User.objects.create_user(
        username="otheruser",
        email="other@example.com",
        password=TEST_USER_PASSWORD,
        phone_number="010-8888-8888",
        is_email_verified=True,
    )


# ==========================================
# 2. 배송지 정보 Fixture
# ==========================================


@pytest.fixture
def remote_shipping_data():
    """
    도서산간 배송지 정보 (제주)

    추가 배송비 테스트용
    """
    return {
        "shipping_name": "김제주",
        "shipping_phone": "010-6300-0000",
        "shipping_postal_code": "63000",  # 제주 우편번호
        "shipping_address": "제주특별자치도 제주시 연동",
        "shipping_address_detail": "301호",
        "order_memo": "제주 배송 테스트",
    }


@pytest.fixture
def invalid_shipping_data():
    """
    잘못된 배송지 정보

    유효성 검증 테스트용
    """
    return {
        "shipping_name": "",  # 빈 이름
        "shipping_phone": "invalid-phone",  # 잘못된 전화번호
        "shipping_postal_code": "",
        "shipping_address": "",
        "shipping_address_detail": "",
    }


@pytest.fixture
def postal_codes():
    """
    우편번호 모음

    다양한 지역의 우편번호 테스트용
    """
    return {
        "standard": "12345",
        "jeju": "63100",
        "ulleung": "59000",
        "other_remote": "52000",
    }


# ==========================================
# 3. 재고 관련 Fixture
# ==========================================


@pytest.fixture
def low_stock_product(db, category, seller_user):
    """
    재고 1개 상품

    재고 경계값 테스트용
    """
    return Product.objects.create(
        name="재고1개 상품",
        slug="low-stock-product",
        category=category,
        seller=seller_user,
        price=Decimal("10000"),
        stock=1,
        sku="LOW-STOCK-001",
        is_active=True,
    )


@pytest.fixture
def inactive_product(db, category, seller_user):
    """
    비활성화된 상품

    판매 중단 상품 테스트용
    """
    return Product.objects.create(
        name="판매중단 상품",
        slug="inactive-product",
        category=category,
        seller=seller_user,
        price=Decimal("15000"),
        stock=10,
        sku="INACTIVE-001",
        is_active=False,
    )


# ==========================================
# 4. 주문 관련 Fixture
# ==========================================


@pytest.fixture
def order_factory(db):
    """
    Order 생성 팩토리

    유연한 주문 생성을 위한 팩토리 함수

    사용 예시:
        order = order_factory(user)  # 기본값
        order = order_factory(user, status="paid")  # 상태 지정
        order = order_factory(user, total_amount=Decimal("50000"))  # 금액 지정
    """

    def _create_order(user, status="pending", **kwargs):
        defaults = {
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구",
            "shipping_address_detail": "101호",
            "total_amount": Decimal("10000"),
            "final_amount": Decimal("10000"),
            "shipping_fee": Decimal("3000"),
        }
        defaults.update(kwargs)
        return Order.objects.create(user=user, status=status, **defaults)

    return _create_order


@pytest.fixture
def bulk_order_factory(db, order_factory):
    """
    여러 주문 일괄 생성 팩토리

    사용 예시:
        orders = bulk_order_factory(user, 5)  # 5개 주문 생성
        orders = bulk_order_factory(user, 3, status="paid")  # 3개 paid 주문
    """

    def _create_orders(user, count, **defaults):
        return [order_factory(user, **defaults) for _ in range(count)]

    return _create_orders


# ==========================================
# 5. 주문 상태 상수 Fixture
# ==========================================


@pytest.fixture
def order_statuses():
    """
    주문 상태 리스트와 display 매핑

    주문 상태 관련 테스트에서 반복 사용되는 상수들
    """
    return {
        "all": ["pending", "paid", "preparing", "shipped", "delivered", "canceled", "refunded"],
        "cancelable": ["pending", "paid"],
        "non_cancelable": ["preparing", "shipped", "delivered", "canceled", "refunded"],
        "display": {
            "pending": "결제대기",
            "paid": "결제완료",
            "preparing": "배송준비중",
            "shipped": "배송중",
            "delivered": "배송완료",
            "canceled": "주문취소",
            "refunded": "환불완료",
        },
    }


# ==========================================
# 6. 인증 헬퍼 Fixture
# ==========================================


@pytest.fixture
def login_helper(api_client):
    """
    JWT 로그인 헬퍼

    사용자 로그인 후 토큰을 반환하고 클라이언트에 인증 설정

    사용 예시:
        client, token = login_helper(user)
        client, token = login_helper(admin_user)
    """

    def _login(user):
        response = api_client.post(
            reverse("auth-login"), {"username": user.username, "password": TEST_USER_PASSWORD}, format="json"
        )
        if response.status_code == 200:
            token = response.json()["token"]["access"]
            api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            return api_client, token
        # admin의 경우 비밀번호가 다를 수 있음
        response = api_client.post(
            reverse("auth-login"), {"username": user.username, "password": TEST_ADMIN_PASSWORD}, format="json"
        )
        token = response.json()["token"]["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return api_client, token

    return _login


@pytest.fixture
def authenticate_as(api_client):
    """
    특정 사용자로 인증

    AccessToken을 직접 생성하여 인증 (로그인 없이)

    사용 예시:
        client = authenticate_as(user)
        client = authenticate_as(user_with_high_points)
    """

    def _auth(user):
        from rest_framework_simplejwt.tokens import AccessToken

        token = AccessToken.for_user(user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return api_client

    return _auth


# ==========================================
# 7. Mock 데이터 Fixture
# ==========================================


@pytest.fixture
def mock_payment_success():
    """
    결제 성공 Mock 응답

    Toss Payment API 성공 응답 시뮬레이션

    사용 예시:
        response = mock_payment_success(order.final_amount)
    """

    def _create_response(amount):
        return {
            "status": "DONE",
            "approvedAt": "2025-01-15T10:30:00+09:00",
            "totalAmount": int(amount),
        }

    return _create_response


@pytest.fixture
def mock_payment_cancel():
    """
    결제 취소 Mock 응답

    Toss Payment API 취소 응답 시뮬레이션
    """
    return {
        "status": "CANCELED",
        "canceledAt": "2025-01-15T11:00:00+09:00",
    }


# ==========================================
# 8. 동시성/비동기 테스트 설정 Fixture
# ==========================================


@pytest.fixture
def async_test_config():
    """
    비동기/동시성 테스트 설정값

    모든 동시성 테스트에서 일관된 설정을 사용하기 위한 fixture

    Returns:
        dict: {
            'max_wait_seconds': 비동기 작업 최대 대기 시간 (초),
            'polling_interval': 상태 폴링 간격 (초)
        }

    사용 예시:
        def test_async_order(async_test_config):
            max_wait = async_test_config['max_wait_seconds']
            interval = async_test_config['polling_interval']
    """
    return {
        "max_wait_seconds": 5,
        "polling_interval": 0.1,
    }
