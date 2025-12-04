"""
Schemathesis 테스트 전용 설정 (conftest.py)
==========================================

배경 및 목적
-----------
OpenAPI 스키마 기반 API Contract 테스트를 위한 fixture 및 설정 파일입니다.
test_api_contract.py에서 사용하는 모든 테스트 데이터와 인증 정보를 제공합니다.

구성 요소
--------
1. 엔드포인트 분류
   - EXCLUDED_ENDPOINTS: 테스트에서 완전히 제외 (외부 의존, 부작용 있음)
   - PUBLIC_ENDPOINTS: 인증 불필요 (공개 API)
   - SELLER_ENDPOINTS: 판매자 권한 필요
   - MUTATING_ENDPOINTS: 데이터 변경 발생 (주의 필요)

2. 인증 Fixture
   - auth_token / auth_headers: 일반 사용자 JWT 인증
   - seller_auth_token / seller_auth_headers: 판매자 JWT 인증

3. 테스트 데이터 Fixture
   - schema_test_category: 테스트용 카테고리
   - schema_test_product: 테스트용 상품
   - schema_test_cart: 테스트용 장바구니 (아이템 포함)
   - schema_test_order: 테스트용 주문

4. 헬퍼 함수
   - is_excluded_endpoint(): 제외 대상 확인
   - is_public_endpoint(): 공개 API 확인
   - is_seller_endpoint(): 판매자 전용 확인
   - should_skip_mutation(): 변경 API 스킵 여부

제외 엔드포인트 기준
------------------
1. 외부 서비스 의존: Toss 웹훅, OAuth 콜백
2. 부작용 발생: 이메일 발송, 결제 처리
3. HTML 반환: 테스트 페이지 (JSON이 아님)

의존성
-----
- shopping/tests/conftest.py의 user, seller_user fixture 필요
- seller_user는 is_seller=True 설정 필요 (2024.12 수정됨)
"""

from decimal import Decimal

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.product import Category, Product


# ==========================================
# 제외/포함 엔드포인트 설정
# ==========================================

# 완전히 제외할 엔드포인트 패턴 (테스트하지 않음)
EXCLUDED_ENDPOINTS = [
    # Webhook - 외부 서비스 콜백 (실제 Toss에서만 호출)
    "/api/webhooks/toss/",
    # 이메일 발송 관련 - 실제 이메일 발송되므로 제외
    "/api/auth/password/reset/request/",
    "/api/auth/email/send/",
    "/api/auth/email/resend/",
    # 소셜 로그인 - 외부 OAuth 의존
    "/api/auth/social/google/",
    "/api/auth/social/kakao/",
    "/api/auth/social/naver/",
    "/api/social/callback/",
    # 테스트 페이지 (HTML 반환)
    "/api/payment/test/",
    "/api/social/test/",
]

# 인증 불필요 엔드포인트 패턴 (공개 API)
PUBLIC_ENDPOINTS = [
    "/api/products/",
    "/api/categories/",
    "/api/auth/register/",
    "/api/auth/login/",
    "/api/auth/password/reset/confirm/",
    "/api/auth/email/verify/",
    "/api/schema/",
]

# 판매자 권한 필요 엔드포인트
SELLER_ENDPOINTS = [
    "/api/seller/returns/",
]

# 데이터 생성/수정/삭제가 필요한 엔드포인트 (주의 필요)
MUTATING_ENDPOINTS = [
    "/api/payments/cancel/",  # 결제 취소
    "/api/orders/",  # 주문 생성/취소
    "/api/cart/clear/",  # 장바구니 비우기
    "/api/wishlist/clear/",  # 위시리스트 비우기
    "/api/users/withdraw/",  # 회원 탈퇴
]


# ==========================================
# 인증 관련 Fixture
# ==========================================


@pytest.fixture
def auth_token(user):
    """
    Schemathesis 테스트용 JWT Access Token 생성

    user fixture를 사용하여 유효한 JWT 토큰 발급
    """
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token)


@pytest.fixture
def seller_auth_token(seller_user):
    """
    판매자용 JWT Access Token 생성

    상품 등록/수정 등 판매자 권한 필요 엔드포인트 테스트용
    """
    refresh = RefreshToken.for_user(seller_user)
    return str(refresh.access_token)


@pytest.fixture
def auth_headers(auth_token):
    """
    인증 헤더 딕셔너리 반환

    Django Test Client용: HTTP_AUTHORIZATION 형식
    Schemathesis case.call용: Authorization 형식
    """
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def seller_auth_headers(seller_auth_token):
    """
    판매자 인증 헤더 딕셔너리 반환
    """
    return {"Authorization": f"Bearer {seller_auth_token}"}


# ==========================================
# 스키마 테스트용 데이터 Fixture
# ==========================================


@pytest.fixture
def schema_test_category(db):
    """스키마 테스트용 카테고리"""
    return Category.objects.create(
        name="스키마 테스트 카테고리",
        slug="schema-test-category",
    )


@pytest.fixture
def schema_test_product(db, schema_test_category, seller_user):
    """스키마 테스트용 상품"""
    return Product.objects.create(
        name="스키마 테스트 상품",
        slug="schema-test-product",
        category=schema_test_category,
        seller=seller_user,
        price=Decimal("10000"),
        stock=100,
        sku="SCHEMA-001",
        description="스키마 테스트용 상품입니다",
        is_active=True,
    )


@pytest.fixture
def schema_test_cart(db, user, schema_test_product):
    """스키마 테스트용 장바구니 (상품 포함)"""
    cart = Cart.objects.create(user=user, is_active=True)
    CartItem.objects.create(cart=cart, product=schema_test_product, quantity=1)
    return cart


@pytest.fixture
def schema_test_order(db, user, schema_test_product):
    """스키마 테스트용 주문"""
    order = Order.objects.create(
        user=user,
        status="pending",
        total_amount=schema_test_product.price,
        final_amount=schema_test_product.price,
        shipping_name="테스트",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구",
        shipping_address_detail="101호",
        order_number="SCHEMA20250101000001",
    )
    OrderItem.objects.create(
        order=order,
        product=schema_test_product,
        product_name=schema_test_product.name,
        quantity=1,
        price=schema_test_product.price,
    )
    return order


# ==========================================
# 헬퍼 함수
# ==========================================


def is_excluded_endpoint(path: str) -> bool:
    """엔드포인트가 제외 대상인지 확인"""
    return any(excluded in path for excluded in EXCLUDED_ENDPOINTS)


def is_public_endpoint(path: str) -> bool:
    """엔드포인트가 공개 API인지 확인"""
    return any(public in path for public in PUBLIC_ENDPOINTS)


def is_seller_endpoint(path: str) -> bool:
    """엔드포인트가 판매자 전용인지 확인"""
    return any(seller in path for seller in SELLER_ENDPOINTS)


def should_skip_mutation(path: str, method: str) -> bool:
    """
    데이터 변경 API를 스킵해야 하는지 확인

    DELETE, 일부 POST는 테스트 데이터 정리가 복잡하므로
    기본 테스트에서는 스킵
    """
    if method.upper() == "DELETE":
        return True
    return any(mutating in path for mutating in MUTATING_ENDPOINTS)
