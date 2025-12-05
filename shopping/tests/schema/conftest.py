"""
Schemathesis 테스트 전용 설정 (conftest.py)
==========================================

📋 개요
-------
OpenAPI 스키마 기반 API Contract 테스트를 위한 fixture 및 설정 파일입니다.
test_api_contract.py에서 사용하는 모든 테스트 데이터와 인증 정보를 제공합니다.

🔧 구성 요소
-----------
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

5. 📦 Response Schema Helpers (신규)
   - assert_paginated_response(): 페이지네이션 응답 구조 검증
   - assert_product_schema(): 상품 응답 스키마 검증
   - assert_error_response(): 에러 응답 구조 검증
   - assert_list_response(): 목록 응답 구조 검증

⚠️ 제외 엔드포인트 기준
---------------------
1. 외부 서비스 의존: Toss 웹훅, OAuth 콜백
2. 부작용 발생: 이메일 발송, 결제 처리
3. HTML 반환: 테스트 페이지 (JSON이 아님)

📁 의존성
--------
- shopping/tests/conftest.py의 user, seller_user fixture 필요
- seller_user는 is_seller=True 설정 필요 (2024.12 수정됨)

📅 작성 정보
-----------
- 작성일: 2025-12-05
- 최종 수정: 2025-12-05 (Response Schema Helpers 추가)
"""

import json
from decimal import Decimal
from typing import Any

import pytest
import schemathesis
from rest_framework_simplejwt.tokens import RefreshToken

from shopping.models.cart import Cart, CartItem
from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment
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


@pytest.fixture
def schema_test_payment(db, schema_test_order):
    """스키마 테스트용 결제"""
    return Payment.objects.create(
        order=schema_test_order,
        toss_order_id=f"TOSS_{schema_test_order.order_number}",
        amount=schema_test_order.final_amount,
        status="done",
        method="카드",
        card_company="삼성카드",
        card_number="1234-****-****-5678",
        payment_key="test_payment_key_12345",
    )


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


# ==========================================
# 📦 Response Schema Helpers
# ==========================================
# Contract Test에서 응답 구조를 검증하기 위한 유틸 함수들입니다.
# 단순 status code 검증을 넘어 실제 스키마 검증을 수행합니다.


class SchemaValidationError(AssertionError):
    """
    스키마 검증 실패 시 발생하는 예외

    일반 AssertionError보다 상세한 컨텍스트를 제공합니다.
    """

    def __init__(self, message: str, field: str = None, expected: Any = None, actual: Any = None):
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(message)


def assert_paginated_response(data: dict, *, context: str = "") -> None:
    """
    📄 페이지네이션 응답 구조 검증

    DRF 기본 페이지네이션 응답 형식을 검증합니다:
    {
        "count": int,
        "next": str | null,
        "previous": str | null,
        "results": list
    }

    Args:
        data: 응답 JSON 데이터
        context: 에러 메시지에 포함할 컨텍스트 (예: 엔드포인트 경로)

    Raises:
        SchemaValidationError: 스키마 불일치 시

    Example:
        >>> response = client.get("/api/products/")
        >>> assert_paginated_response(response.json(), context="/api/products/")
    """
    prefix = f"[{context}] " if context else ""

    # 기본 구조 검증
    if not isinstance(data, dict):
        raise SchemaValidationError(f"{prefix}응답이 dict가 아님", field="root", expected="dict", actual=type(data).__name__)

    # 필수 필드 검증
    required_fields = ["count", "results"]
    for field in required_fields:
        if field not in data:
            raise SchemaValidationError(f"{prefix}필수 필드 '{field}' 누락", field=field, expected="present", actual="missing")

    # 타입 검증
    if not isinstance(data["count"], int):
        raise SchemaValidationError(
            f"{prefix}'count' 필드가 int가 아님",
            field="count",
            expected="int",
            actual=type(data["count"]).__name__,
        )

    if not isinstance(data["results"], list):
        raise SchemaValidationError(
            f"{prefix}'results' 필드가 list가 아님",
            field="results",
            expected="list",
            actual=type(data["results"]).__name__,
        )

    # 선택적 필드 타입 검증
    for field in ["next", "previous"]:
        if field in data and data[field] is not None:
            if not isinstance(data[field], str):
                raise SchemaValidationError(
                    f"{prefix}'{field}' 필드가 string 또는 null이 아님",
                    field=field,
                    expected="str | null",
                    actual=type(data[field]).__name__,
                )


def assert_list_response(data: Any, *, min_length: int = 0, context: str = "") -> list:
    """
    📋 목록 응답 구조 검증

    페이지네이션 없는 단순 리스트 또는 페이지네이션 응답의 results를 검증합니다.

    Args:
        data: 응답 JSON 데이터 (list 또는 paginated dict)
        min_length: 최소 항목 수 (0이면 빈 리스트 허용)
        context: 에러 메시지에 포함할 컨텍스트

    Returns:
        list: 검증된 results 리스트

    Raises:
        SchemaValidationError: 스키마 불일치 시
    """
    prefix = f"[{context}] " if context else ""

    # 페이지네이션 응답 처리
    if isinstance(data, dict) and "results" in data:
        results = data["results"]
    elif isinstance(data, list):
        results = data
    else:
        raise SchemaValidationError(
            f"{prefix}응답이 list 또는 paginated dict가 아님",
            field="root",
            expected="list | {results: list}",
            actual=type(data).__name__,
        )

    if not isinstance(results, list):
        raise SchemaValidationError(
            f"{prefix}results가 list가 아님", field="results", expected="list", actual=type(results).__name__
        )

    if len(results) < min_length:
        raise SchemaValidationError(
            f"{prefix}results 길이가 최소값({min_length})보다 작음",
            field="results.length",
            expected=f">= {min_length}",
            actual=len(results),
        )

    return results


def assert_product_schema(product: dict, *, strict: bool = False, context: str = "") -> None:
    """
    🛍️ 상품 응답 스키마 검증

    상품 API 응답의 필드/타입을 검증합니다.

    Args:
        product: 상품 데이터 dict
        strict: True이면 추가 필드 금지 (additionalProperties: false)
        context: 에러 메시지에 포함할 컨텍스트

    Required fields:
        - id: int
        - name: str
        - price: str (decimal string)
        - stock: int

    Optional fields:
        - description: str
        - category: int | dict
        - seller: int | dict
        - is_active: bool
        - slug: str
        - sku: str
        - created_at: str (datetime)
        - updated_at: str (datetime)

    Raises:
        SchemaValidationError: 스키마 불일치 시

    Example:
        >>> response = client.get("/api/products/1/")
        >>> assert_product_schema(response.json(), context="/api/products/1/")
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(product, dict):
        raise SchemaValidationError(
            f"{prefix}상품 데이터가 dict가 아님", field="root", expected="dict", actual=type(product).__name__
        )

    # 필수 필드 정의: (필드명, 허용 타입들)
    required_fields = {
        "id": (int,),
        "name": (str,),
        "price": (str, int, float, Decimal),  # DRF는 Decimal을 string으로 직렬화
        "stock": (int,),
    }

    for field, allowed_types in required_fields.items():
        if field not in product:
            raise SchemaValidationError(f"{prefix}필수 필드 '{field}' 누락", field=field, expected="present", actual="missing")

        if not isinstance(product[field], allowed_types):
            raise SchemaValidationError(
                f"{prefix}'{field}' 타입 불일치",
                field=field,
                expected=str(allowed_types),
                actual=type(product[field]).__name__,
            )

    # 선택적 필드 타입 검증
    optional_fields = {
        "description": (str, type(None)),
        "is_active": (bool,),
        "slug": (str,),
        "sku": (str, type(None)),
        "created_at": (str,),
        "updated_at": (str,),
        "category": (int, dict, type(None)),
        "seller": (int, dict, type(None)),
    }

    for field, allowed_types in optional_fields.items():
        if field in product and product[field] is not None:
            if not isinstance(product[field], allowed_types):
                raise SchemaValidationError(
                    f"{prefix}'{field}' 타입 불일치",
                    field=field,
                    expected=str(allowed_types),
                    actual=type(product[field]).__name__,
                )

    # Strict mode: 알 수 없는 필드 검사
    if strict:
        allowed_fields = set(required_fields.keys()) | set(optional_fields.keys())
        # 일반적으로 허용되는 추가 필드들
        allowed_fields.update(
            [
                "url",
                "images",
                "reviews",
                "average_rating",
                "review_count",
                "category_name",
                # 상품 상세 API에서 반환되는 추가 필드들
                "seller_level",
                "seller_id",
                "category_parent_name",
                "category_id",
                "seller_product_count",
                "stock_status",
                "is_in_stock",
                "seller_username",
                "recent_reviews",
                "category_slug",
            ]
        )
        extra_fields = set(product.keys()) - allowed_fields
        if extra_fields:
            raise SchemaValidationError(
                f"{prefix}예상치 못한 필드 발견: {extra_fields}",
                field="additionalProperties",
                expected="no extra fields",
                actual=list(extra_fields),
            )


def assert_error_response(
    data: dict,
    *,
    expected_fields: list[str] = None,
    context: str = "",
) -> None:
    """
    ❌ 에러 응답 구조 검증

    API 에러 응답이 일관된 형식을 갖추고 있는지 검증합니다.

    Args:
        data: 에러 응답 JSON 데이터
        expected_fields: 존재해야 할 필드 목록 (기본: detail 또는 에러 필드)
        context: 에러 메시지에 포함할 컨텍스트

    Expected formats (DRF 기본):
        - {"detail": "error message"}
        - {"field_name": ["error1", "error2"]}
        - {"non_field_errors": ["error message"]}

    Raises:
        SchemaValidationError: 스키마 불일치 시

    Example:
        >>> response = client.post("/api/auth/login/", {"username": "", "password": ""})
        >>> assert response.status_code == 400
        >>> assert_error_response(response.json(), context="/api/auth/login/")
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(data, dict):
        raise SchemaValidationError(
            f"{prefix}에러 응답이 dict가 아님", field="root", expected="dict", actual=type(data).__name__
        )

    # 빈 응답 체크
    if not data:
        raise SchemaValidationError(f"{prefix}에러 응답이 비어있음", field="root", expected="non-empty dict", actual="{}")

    # 일반적인 DRF 에러 필드
    common_error_fields = {"detail", "non_field_errors", "error", "message", "errors"}

    # 지정된 필드가 있으면 해당 필드 검증
    if expected_fields:
        for field in expected_fields:
            if field not in data:
                raise SchemaValidationError(
                    f"{prefix}예상된 에러 필드 '{field}' 누락", field=field, expected="present", actual="missing"
                )
    else:
        # 최소한 하나의 에러 정보가 있어야 함
        # 필드별 에러 또는 공통 에러 필드
        has_error_info = bool(common_error_fields & set(data.keys())) or len(data) > 0
        if not has_error_info:
            raise SchemaValidationError(
                f"{prefix}에러 정보가 없음",
                field="*",
                expected="error info",
                actual=list(data.keys()),
            )

    # 에러 값 타입 검증 (string 또는 list of strings)
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str):
            continue
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, (str, dict)):
                    raise SchemaValidationError(
                        f"{prefix}'{key}' 에러 목록에 잘못된 타입 포함",
                        field=f"{key}[]",
                        expected="str | dict",
                        actual=type(item).__name__,
                    )
            continue
        if isinstance(value, dict):
            continue  # 중첩된 에러 객체 허용
        raise SchemaValidationError(
            f"{prefix}'{key}' 값이 올바른 에러 형식이 아님",
            field=key,
            expected="str | list | dict",
            actual=type(value).__name__,
        )


def assert_order_schema(order: dict, *, context: str = "") -> None:
    """
    📦 주문 응답 스키마 검증

    주문 API 응답의 필드/타입을 검증합니다.

    Args:
        order: 주문 데이터 dict
        context: 에러 메시지에 포함할 컨텍스트

    Required fields:
        - id: int
        - status: str
        - total_amount: str (decimal)
        - final_amount: str (decimal)

    Raises:
        SchemaValidationError: 스키마 불일치 시
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(order, dict):
        raise SchemaValidationError(
            f"{prefix}주문 데이터가 dict가 아님", field="root", expected="dict", actual=type(order).__name__
        )

    required_fields = {
        "id": (int,),
        "status": (str,),
        "total_amount": (str, int, float, Decimal),
        "final_amount": (str, int, float, Decimal),
    }

    for field, allowed_types in required_fields.items():
        if field not in order:
            raise SchemaValidationError(f"{prefix}필수 필드 '{field}' 누락", field=field, expected="present", actual="missing")

        if not isinstance(order[field], allowed_types):
            raise SchemaValidationError(
                f"{prefix}'{field}' 타입 불일치",
                field=field,
                expected=str(allowed_types),
                actual=type(order[field]).__name__,
            )


def assert_cart_schema(cart: dict, *, context: str = "") -> None:
    """
    🛒 장바구니 응답 스키마 검증

    장바구니 API 응답의 필드/타입을 검증합니다.

    Args:
        cart: 장바구니 데이터 dict
        context: 에러 메시지에 포함할 컨텍스트

    Required fields:
        - id: int
        - items: list

    Raises:
        SchemaValidationError: 스키마 불일치 시
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(cart, dict):
        raise SchemaValidationError(
            f"{prefix}장바구니 데이터가 dict가 아님", field="root", expected="dict", actual=type(cart).__name__
        )

    # 최소한 id 또는 items가 있어야 함
    if "id" not in cart and "items" not in cart:
        raise SchemaValidationError(
            f"{prefix}'id' 또는 'items' 필드 중 하나는 있어야 함",
            field="id|items",
            expected="present",
            actual="missing",
        )

    if "id" in cart and not isinstance(cart["id"], int):
        raise SchemaValidationError(f"{prefix}'id' 타입 불일치", field="id", expected="int", actual=type(cart["id"]).__name__)

    if "items" in cart and not isinstance(cart["items"], list):
        raise SchemaValidationError(
            f"{prefix}'items' 타입 불일치", field="items", expected="list", actual=type(cart["items"]).__name__
        )


def assert_payment_schema(payment: dict, *, strict: bool = False, context: str = "") -> None:
    """
    💳 결제 응답 스키마 검증

    결제 API 응답의 필드/타입을 검증합니다.

    Args:
        payment: 결제 데이터 dict
        strict: True이면 추가 필드 금지
        context: 에러 메시지에 포함할 컨텍스트

    Required fields (실제 API 응답 기준):
        - id: int
        - order: int
        - amount: str (Decimal → string 직렬화)
        - status: str
        - created_at: str (ISO 8601)

    Optional fields (실제 API 응답 기준):
        - order_number: str
        - payment_key: str (nullable)
        - order_id: int (order.id와 동일)
        - method: str
        - card_company: str
        - card_number: str (마스킹됨)
        - installment_plan_months: int
        - status_display: str (한글 표시)
        - approved_at: str | None
        - receipt_url: str
        - is_canceled: bool
        - canceled_amount: str (Decimal → string)
        - cancel_reason: str
        - canceled_at: str | None
        - updated_at: str (ISO 8601)
        - used_points: int
        - earned_points: int

    Raises:
        SchemaValidationError: 스키마 불일치 시

    Example:
        >>> response = client.get("/api/payments/1/")
        >>> assert_payment_schema(response.json(), context="/api/payments/1/")
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(payment, dict):
        raise SchemaValidationError(
            f"{prefix}결제 데이터가 dict가 아님", field="root", expected="dict", actual=type(payment).__name__
        )

    # 필수 필드 정의 (실제 API 응답 기준)
    required_fields = {
        "id": (int,),
        "order": (int,),
        "amount": (str,),  # Decimal → str 직렬화
        "status": (str,),
        "created_at": (str,),
    }

    for field, allowed_types in required_fields.items():
        if field not in payment:
            raise SchemaValidationError(
                f"{prefix}필수 필드 '{field}' 누락", field=field, expected="present", actual="missing"
            )

        if not isinstance(payment[field], allowed_types):
            raise SchemaValidationError(
                f"{prefix}'{field}' 타입 불일치",
                field=field,
                expected=str(allowed_types),
                actual=type(payment[field]).__name__,
            )

    # 선택적 필드 타입 검증 (실제 API 응답 기준)
    optional_fields = {
        "order_number": (str,),
        "payment_key": (str, type(None)),
        "order_id": (int,),  # order.id (int)
        "method": (str,),
        "card_company": (str,),
        "card_number": (str,),
        "installment_plan_months": (int,),
        "status_display": (str,),
        "approved_at": (str, type(None)),
        "receipt_url": (str,),
        "is_canceled": (bool,),
        "canceled_amount": (str,),  # Decimal → str 직렬화
        "cancel_reason": (str,),
        "canceled_at": (str, type(None)),
        "updated_at": (str,),
        "used_points": (int,),
        "earned_points": (int,),
    }

    for field, allowed_types in optional_fields.items():
        if field in payment and payment[field] is not None:
            if not isinstance(payment[field], allowed_types):
                raise SchemaValidationError(
                    f"{prefix}'{field}' 타입 불일치",
                    field=field,
                    expected=str(allowed_types),
                    actual=type(payment[field]).__name__,
                )

    # Strict mode: 알 수 없는 필드 검사
    if strict:
        allowed_fields = set(required_fields.keys()) | set(optional_fields.keys())
        extra_fields = set(payment.keys()) - allowed_fields
        if extra_fields:
            raise SchemaValidationError(
                f"{prefix}예상치 못한 필드 발견: {extra_fields}",
                field="additionalProperties",
                expected="no extra fields",
                actual=list(extra_fields),
            )


def assert_user_schema(user: dict, *, context: str = "") -> None:
    """
    👤 사용자 프로필 응답 스키마 검증

    사용자 프로필 API 응답의 필드/타입을 검증합니다.

    Args:
        user: 사용자 데이터 dict
        context: 에러 메시지에 포함할 컨텍스트

    Required fields:
        - username: str

    Raises:
        SchemaValidationError: 스키마 불일치 시
    """
    prefix = f"[{context}] " if context else ""

    if not isinstance(user, dict):
        raise SchemaValidationError(
            f"{prefix}사용자 데이터가 dict가 아님", field="root", expected="dict", actual=type(user).__name__
        )

    if "username" not in user:
        raise SchemaValidationError(
            f"{prefix}필수 필드 'username' 누락", field="username", expected="present", actual="missing"
        )

    if not isinstance(user["username"], str):
        raise SchemaValidationError(
            f"{prefix}'username' 타입 불일치",
            field="username",
            expected="str",
            actual=type(user["username"]).__name__,
        )


# ==========================================
# 📦 스키마 로드 Fixture
# ==========================================


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    🔧 OpenAPI 스키마 로드

    Django 테스트 클라이언트로 스키마를 가져와서 schemathesis 스키마 객체 생성.
    scope="module"로 모듈당 한 번만 로드하여 성능 최적화.

    Returns:
        schemathesis.Schema: 파싱된 OpenAPI 스키마 객체
    """
    from django.test import Client

    with django_db_blocker.unblock():
        client = Client()
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200, "OpenAPI 스키마를 가져올 수 없습니다"
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)


# ==========================================
# 🔧 스키마 헬퍼 함수
# ==========================================


def get_operations_by_path(schema, path_contains: str, method: str = None):
    """
    스키마에서 특정 경로 패턴의 operation 추출

    Args:
        schema: Schemathesis 스키마 객체
        path_contains: 경로에 포함되어야 할 문자열
        method: HTTP 메서드 필터 (None이면 모두)

    Returns:
        list: 매칭되는 operation 목록 (path parameter 있는 것 제외)
    """
    operations = []
    for result in schema.get_all_operations():
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        if "{" in op.path:
            continue

        if path_contains in op.path:
            if method is None or op.method.upper() == method.upper():
                operations.append(op)
    return operations


def get_all_operations(schema, method: str = None, include_path_params: bool = False):
    """
    스키마에서 모든 operation 추출

    Args:
        schema: Schemathesis 스키마 객체
        method: HTTP 메서드 필터 (None이면 모두)
        include_path_params: True이면 path parameter 있는 것도 포함

    Returns:
        list: 매칭되는 operation 목록
    """
    operations = []
    for result in schema.get_all_operations():
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        if not include_path_params and "{" in op.path:
            continue

        if method is None or op.method.upper() == method.upper():
            operations.append(op)
    return operations
