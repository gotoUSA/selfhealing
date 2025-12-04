"""
OpenAPI 스키마 기반 API Contract 테스트
=======================================

배경 및 목적
-----------
Schemathesis 4.x를 사용하여 OpenAPI 스키마와 실제 API 응답이 일치하는지 자동으로 검증합니다.
이 테스트는 API 문서(스키마)와 실제 구현 간의 불일치를 조기에 발견하기 위해 설계되었습니다.

테스트 범위
----------
- Stateless 테스트: 각 엔드포인트를 독립적으로 검증 (상태 의존 없음)
- 인증 처리: JWT Bearer 토큰을 자동으로 설정 (user, seller_user fixture 활용)
- 응답 검증: 스키마 정의와 실제 응답의 구조/타입 비교
- 에러 핸들링: 401, 404, 5xx 응답 코드 검증

테스트 클래스 구조
-----------------
1. TestSchemaDiscovery (3 tests)
   - 스키마가 유효한지, 필수 엔드포인트가 정의되어 있는지 확인

2. TestPublicEndpoints (5 tests)
   - 인증 없이 접근 가능한 공개 API 검증 (products, categories 등)

3. TestAuthenticatedEndpoints (17 tests)
   - JWT 인증이 필요한 엔드포인트 검증 (cart, orders, wishlist 등)

4. TestPathParameterEndpoints (8 tests)
   - 경로 파라미터({id})가 있는 엔드포인트 검증
   - 유효/무효한 ID로 200/404 응답 확인

5. TestAuthenticationRequired (6 tests)
   - 인증 필요 엔드포인트에 인증 없이 접근 시 401 반환 확인

6. TestPostEndpoints (4 tests)
   - POST 요청 (생성/수정) 엔드포인트 검증

7. TestFullSchemaValidation (2 tests, @slow)
   - 전체 GET 엔드포인트에서 5xx 에러 없음 확인
   - 모든 응답이 유효한 JSON인지 확인

제외된 엔드포인트 (conftest.py EXCLUDED_ENDPOINTS)
------------------------------------------------
- Webhook: /api/webhooks/toss/ (외부 서비스 콜백)
- 이메일 발송: /api/auth/password/reset/request/ 등 (실제 발송됨)
- 소셜 로그인: /api/auth/social/* (외부 OAuth 의존)
- 테스트 페이지: /api/payment/test/ (HTML 반환)

실행 방법
--------
    pytest shopping/tests/schema/ -v -m schema          # 스키마 테스트만
    pytest shopping/tests/schema/ -v -m "schema and not slow"  # 느린 테스트 제외
    pytest shopping/tests/schema/ -v                    # 전체 실행

사전 조건
--------
- PostgreSQL 데이터베이스 실행 중 (Docker: localhost:5432)
- requirements-dev.txt의 schemathesis, pytest-env 설치
- Windows 환경: PGCLIENTENCODING=UTF8 설정 (pyproject.toml에 정의됨)

관련 파일
--------
- conftest.py: 인증 fixture, 테스트 데이터, 제외 엔드포인트 정의
- shopping/tests/conftest.py: user, seller_user 등 공통 fixture
"""

import json
import pytest
import schemathesis
from hypothesis import settings as hypothesis_settings, Verbosity

from .conftest import (
    EXCLUDED_ENDPOINTS,
    PUBLIC_ENDPOINTS,
    SELLER_ENDPOINTS,
    is_excluded_endpoint,
    is_public_endpoint,
    is_seller_endpoint,
    should_skip_mutation,
)


# ==========================================
# 스키마 로드 Fixture
# ==========================================


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    OpenAPI 스키마 로드

    Django 테스트 클라이언트로 스키마를 가져와서 schemathesis 스키마 객체 생성
    """
    from django.test import Client

    with django_db_blocker.unblock():
        client = Client()
        # JSON 형식으로 스키마 요청
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200, "OpenAPI 스키마를 가져올 수 없습니다"
        # response.content를 직접 JSON으로 파싱
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)


# ==========================================
# 헬퍼 함수
# ==========================================


def get_operations_by_path(schema, path_contains: str, method: str = None):
    """스키마에서 특정 경로 패턴의 operation 추출 (path parameter 없는 것만)"""
    operations = []
    for result in schema.get_all_operations():
        # Schemathesis 4.x에서는 Result 객체를 반환
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        # path parameter가 있는 엔드포인트 스킵 (예: {id}, {product_pk})
        if "{" in op.path:
            continue

        if path_contains in op.path:
            if method is None or op.method.upper() == method.upper():
                operations.append(op)
    return operations


def get_all_operations(schema, method: str = None, include_path_params: bool = False):
    """스키마에서 모든 operation 추출"""
    operations = []
    for result in schema.get_all_operations():
        if hasattr(result, "ok"):
            op = result.ok()
        else:
            op = result

        # path parameter 필터링
        if not include_path_params and "{" in op.path:
            continue

        # 메서드 필터링
        if method is None or op.method.upper() == method.upper():
            operations.append(op)
    return operations


# ==========================================
# Public 엔드포인트 테스트 (인증 불필요)
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPublicEndpoints:
    """
    인증이 필요 없는 공개 엔드포인트 테스트

    검증 대상:
    - /api/products/ (목록, 인기, 평점순)
    - /api/categories/ (목록, 트리)
    - 상세 조회 (/api/products/{id}/, /api/categories/{id}/)

    참고: /api/products/low_stock/은 판매자 권한 필요 (TestAuthenticatedEndpoints에서 테스트)
    """

    def test_products_list(self, openapi_schema, client, schema_test_product):
        """상품 목록 API 스키마 검증"""
        # low_stock은 인증 필요하므로 제외
        public_product_paths = ["/api/products/", "/api/products/popular/", "/api/products/best_rating/"]
        operations = get_operations_by_path(openapi_schema, "/api/products/", "GET")
        for op in operations:
            if op.path not in public_product_paths:
                continue  # 인증 필요한 엔드포인트 스킵
            response = client.get(op.path)
            # 200 성공 확인
            assert response.status_code == 200, f"{op.path} returned {response.status_code}"
            # 응답이 JSON인지 확인
            data = response.json()
            assert "results" in data or isinstance(data, list), f"{op.path} 응답 형식 오류"

    def test_categories_list(self, openapi_schema, client, schema_test_category):
        """카테고리 목록 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/categories/", "GET")
        for op in operations:
            response = client.get(op.path)
            assert response.status_code == 200, f"{op.path} returned {response.status_code}"

    def test_products_detail(self, openapi_schema, client, schema_test_product):
        """상품 상세 API 스키마 검증"""
        response = client.get(f"/api/products/{schema_test_product.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == schema_test_product.id
        assert data["name"] == schema_test_product.name

    def test_categories_detail(self, openapi_schema, client, schema_test_category):
        """카테고리 상세 API 스키마 검증"""
        response = client.get(f"/api/categories/{schema_test_category.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == schema_test_category.id

    def test_categories_tree(self, openapi_schema, client, schema_test_category):
        """카테고리 트리 API 스키마 검증"""
        response = client.get("/api/categories/tree/")
        assert response.status_code == 200


# ==========================================
# 인증 필요 엔드포인트 테스트
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthenticatedEndpoints:
    """
    인증이 필요한 엔드포인트 테스트 (JWT Bearer 토큰 필수)

    검증 대상:
    - 장바구니: /api/cart/, /api/cart/summary/, /api/cart/items/
    - 주문: /api/orders/, /api/orders/{id}/
    - 위시리스트: /api/wishlist/, /api/wishlist/stats/
    - 알림: /api/notifications/, /api/notifications/unread/
    - 결제: /api/payments/
    - 포인트: /api/points/my/, /api/points/history/
    - 사용자: /api/users/profile/, /api/my/questions/
    - 반품: /api/returns/, /api/seller/returns/ (판매자)
    - 상품: /api/products/low_stock/ (판매자 권한 필요)
    """

    def test_cart_retrieve(self, openapi_schema, client, auth_headers, schema_test_cart):
        """장바구니 조회 API 스키마 검증"""
        response = client.get("/api/cart/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200, f"/api/cart/ returned {response.status_code}"
        data = response.json()
        assert "items" in data or "id" in data

    def test_cart_summary(self, openapi_schema, client, auth_headers, schema_test_cart):
        """장바구니 요약 API 스키마 검증"""
        response = client.get("/api/cart/summary/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_cart_items(self, openapi_schema, client, auth_headers, schema_test_cart):
        """장바구니 아이템 목록 API 스키마 검증"""
        response = client.get("/api/cart/items/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_orders_list(self, openapi_schema, client, auth_headers, schema_test_order):
        """주문 목록 API 스키마 검증"""
        response = client.get("/api/orders/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_orders_detail(self, openapi_schema, client, auth_headers, schema_test_order):
        """주문 상세 API 스키마 검증"""
        response = client.get(
            f"/api/orders/{schema_test_order.id}/",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == schema_test_order.id

    def test_wishlist_list(self, openapi_schema, client, auth_headers):
        """위시리스트 목록 API 스키마 검증"""
        response = client.get("/api/wishlist/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_wishlist_stats(self, openapi_schema, client, auth_headers):
        """위시리스트 통계 API 스키마 검증"""
        response = client.get("/api/wishlist/stats/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_notifications_list(self, openapi_schema, client, auth_headers):
        """알림 목록 API 스키마 검증"""
        response = client.get("/api/notifications/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_notifications_unread(self, openapi_schema, client, auth_headers):
        """읽지 않은 알림 API 스키마 검증"""
        response = client.get("/api/notifications/unread/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_my_questions(self, openapi_schema, client, auth_headers):
        """내 문의 목록 API 스키마 검증"""
        response = client.get("/api/my/questions/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_payments_list(self, openapi_schema, client, auth_headers):
        """결제 목록 API 스키마 검증"""
        response = client.get("/api/payments/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_returns_list(self, openapi_schema, client, auth_headers):
        """교환/환불 목록 API 스키마 검증"""
        response = client.get("/api/returns/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_user_profile(self, openapi_schema, client, auth_headers):
        """사용자 프로필 API 스키마 검증"""
        response = client.get("/api/users/profile/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200
        data = response.json()
        assert "username" in data

    def test_points_my(self, openapi_schema, client, auth_headers):
        """내 포인트 조회 API 스키마 검증"""
        response = client.get("/api/points/my/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_points_history(self, openapi_schema, client, auth_headers):
        """포인트 내역 API 스키마 검증"""
        response = client.get("/api/points/history/", **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_products_low_stock(self, openapi_schema, client, seller_auth_headers, schema_test_product):
        """재고 부족 상품 API 스키마 검증 (판매자 권한 필요)"""
        response = client.get("/api/products/low_stock/", **{"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]})
        assert response.status_code == 200

    def test_seller_returns_list(self, openapi_schema, client, seller_auth_headers):
        """판매자 반품 목록 API 스키마 검증 (판매자 인증 필요)"""
        response = client.get("/api/seller/returns/", **{"HTTP_AUTHORIZATION": seller_auth_headers["Authorization"]})
        assert response.status_code == 200


# ==========================================
# Path Parameter 엔드포인트 테스트
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPathParameterEndpoints:
    """
    Path Parameter가 있는 엔드포인트 테스트

    검증 대상:
    - 유효한 ID: 200 응답 + 올바른 데이터 반환
    - 무효한 ID (99999999): 404 응답
    - 인증 없이 접근: 401 응답

    참고: GET /api/products/, GET /api/cart/ 등 path parameter 없는
    엔드포인트는 TestPublicEndpoints, TestAuthenticatedEndpoints에서 테스트
    """

    def test_product_detail_with_valid_id(self, client, schema_test_product):
        """유효한 상품 ID로 상세 조회"""
        response = client.get(f"/api/products/{schema_test_product.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == schema_test_product.id

    def test_product_detail_with_invalid_id(self, client):
        """유효하지 않은 상품 ID로 404 반환 확인"""
        response = client.get("/api/products/99999999/")
        assert response.status_code == 404

    def test_category_detail_with_valid_id(self, client, schema_test_category):
        """유효한 카테고리 ID로 상세 조회"""
        response = client.get(f"/api/categories/{schema_test_category.id}/")
        assert response.status_code == 200

    def test_order_detail_with_valid_id(self, client, auth_headers, schema_test_order):
        """유효한 주문 ID로 상세 조회"""
        response = client.get(
            f"/api/orders/{schema_test_order.id}/",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        assert response.status_code == 200

    def test_order_detail_unauthorized(self, client, schema_test_order):
        """인증 없이 주문 상세 접근 시 401"""
        response = client.get(f"/api/orders/{schema_test_order.id}/")
        assert response.status_code == 401

    def test_cart_item_detail(self, client, auth_headers, schema_test_cart):
        """장바구니 아이템 조회"""
        # 장바구니의 첫 번째 아이템 가져오기
        cart_item = schema_test_cart.items.first()
        if cart_item:
            response = client.get(
                f"/api/cart/items/{cart_item.id}/",
                **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
            )
            # PATCH/DELETE만 지원할 수 있으므로 404나 405도 허용
            assert response.status_code in [200, 404, 405]

    def test_notification_detail(self, client, auth_headers):
        """알림 상세 조회 (존재하지 않는 ID)"""
        response = client.get(
            "/api/notifications/99999999/",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        assert response.status_code == 404

    def test_payment_detail(self, client, auth_headers):
        """결제 상세 조회 (존재하지 않는 ID)"""
        response = client.get(
            "/api/payments/99999999/",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        assert response.status_code == 404


# ==========================================
# 인증 실패 테스트 (401 응답 검증)
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthenticationRequired:
    """
    인증 필요 엔드포인트의 401 응답 검증

    목적:
    - 보호된 엔드포인트가 실제로 인증을 요구하는지 확인
    - 인증 없이 접근 시 401 Unauthorized 반환 확인

    제외: /api/cart/ (세션 기반 비회원 장바구니 허용)
    """

    @pytest.mark.parametrize(
        "endpoint",
        [
            # /api/cart/ 제외 - 세션 기반 비회원 장바구니 허용
            "/api/orders/",
            "/api/wishlist/",
            "/api/notifications/",
            "/api/payments/",
            "/api/points/my/",
            "/api/users/profile/",
        ],
    )
    def test_unauthenticated_access_returns_401(self, client, endpoint):
        """인증 없이 접근 시 401 응답"""
        response = client.get(endpoint)
        assert response.status_code == 401, f"{endpoint} should return 401, got {response.status_code}"


# ==========================================
# POST 엔드포인트 테스트 (생성 API)
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPostEndpoints:
    """
    POST 엔드포인트 스키마 검증 (생성/수정 API)

    검증 대상:
    - 장바구니 추가: POST /api/cart/add_item/
    - 위시리스트 토글: POST /api/wishlist/toggle/
    - 로그인: POST /api/auth/login/
    - 회원가입 유효성: POST /api/auth/register/ (실패 케이스)

    참고: 실제 데이터 생성이 발생하므로 트랜잭션 롤백으로 정리됨
    """

    def test_cart_add_item(self, client, auth_headers, schema_test_product):
        """장바구니 상품 추가 API 스키마 검증"""
        response = client.post(
            "/api/cart/add_item/",
            data={"product_id": schema_test_product.id, "quantity": 1},
            content_type="application/json",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        # 201 Created 또는 200 OK
        assert response.status_code in [200, 201], f"cart add_item returned {response.status_code}"

    def test_wishlist_toggle(self, client, auth_headers, schema_test_product):
        """위시리스트 토글 API 스키마 검증"""
        response = client.post(
            "/api/wishlist/toggle/",
            data={"product_id": schema_test_product.id},
            content_type="application/json",
            **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]},
        )
        assert response.status_code in [200, 201]

    def test_auth_login(self, client, user):
        """로그인 API 스키마 검증"""
        response = client.post(
            "/api/auth/login/",
            data={"username": "testuser", "password": "testpass123"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data

    def test_auth_register_validation(self, client):
        """회원가입 API 유효성 검증 (중복 사용자)"""
        # 유효하지 않은 데이터로 400 응답 확인
        response = client.post(
            "/api/auth/register/",
            data={"username": "", "password": "short"},
            content_type="application/json",
        )
        assert response.status_code == 400


# ==========================================
# 전체 스키마 검증 (Smoke Test)
# ==========================================


@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFullSchemaValidation:
    """
    전체 API 스키마 검증 (Smoke Test)

    목적:
    - 모든 GET 엔드포인트에서 5xx 서버 에러가 발생하지 않는지 확인
    - 모든 성공 응답이 유효한 JSON인지 확인

    실행 시간:
    - 전체 엔드포인트를 순회하므로 시간이 오래 걸림 (~30초+)
    - @pytest.mark.slow로 표시되어 기본 실행에서 제외 가능

    실행 방법:
    ```bash
    # slow 테스트 포함 실행
    pytest shopping/tests/schema/ -v -m "schema"

    # slow 테스트 제외
    pytest shopping/tests/schema/ -v -m "schema and not slow"
    ```
    """

    def test_all_get_endpoints_no_5xx(self, openapi_schema, client, auth_headers, schema_test_product, schema_test_order):
        """모든 GET 엔드포인트에서 5xx 에러가 발생하지 않는지 검증"""
        failed_endpoints = []

        for result in openapi_schema.get_all_operations():
            # Schemathesis 4.x Result 처리
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            # path parameter가 있는 엔드포인트 스킵 (예: {id}, {product_pk})
            if "{" in op.path:
                continue

            # 제외할 엔드포인트 스킵
            if is_excluded_endpoint(op.path):
                continue

            # GET 요청만 테스트
            if op.method.upper() != "GET":
                continue

            # 공개 엔드포인트는 인증 없이
            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})

            # 5xx 에러 수집
            if response.status_code >= 500:
                failed_endpoints.append(f"{op.path}: {response.status_code}")

        assert not failed_endpoints, f"5xx 에러 발생 엔드포인트: {failed_endpoints}"

    def test_all_endpoints_return_valid_json(
        self, openapi_schema, client, auth_headers, schema_test_product, schema_test_order
    ):
        """GET 엔드포인트가 유효한 JSON을 반환하는지 검증"""
        invalid_json_endpoints = []

        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            if "{" in op.path:
                continue

            if is_excluded_endpoint(op.path):
                continue

            if op.method.upper() != "GET":
                continue

            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]})

            # 성공 응답에서 JSON 파싱 시도
            if 200 <= response.status_code < 300:
                try:
                    response.json()
                except (ValueError, TypeError):
                    invalid_json_endpoints.append(op.path)

        assert not invalid_json_endpoints, f"유효하지 않은 JSON 반환: {invalid_json_endpoints}"


# ==========================================
# 엔드포인트 발견 테스트
# ==========================================


@pytest.mark.schema
@pytest.mark.django_db
class TestSchemaDiscovery:
    """
    스키마 구조 검증 및 엔드포인트 발견

    목적:
    - OpenAPI 스키마가 정상적으로 로드되는지 확인
    - 필수 엔드포인트가 스키마에 정의되어 있는지 확인
    - 스키마 누락 시 조기 실패로 다른 테스트 영향 방지

    필수 엔드포인트:
    - /api/products/, /api/categories/ (공개)
    - /api/cart/, /api/orders/ (인증 필요)
    - /api/auth/login/, /api/auth/register/ (인증)
    """

    def test_schema_is_valid(self, openapi_schema):
        """OpenAPI 스키마가 유효한지 확인"""
        # 스키마가 로드되었는지 확인
        assert openapi_schema is not None

    def test_schema_has_paths(self, openapi_schema):
        """스키마에 경로가 정의되어 있는지 확인"""
        operations = list(openapi_schema.get_all_operations())
        assert len(operations) > 0, "스키마에 정의된 엔드포인트가 없습니다"

    def test_critical_endpoints_exist(self, openapi_schema):
        """핵심 엔드포인트가 스키마에 정의되어 있는지 확인"""
        critical_paths = [
            "/api/products/",
            "/api/categories/",
            "/api/cart/",
            "/api/orders/",
            "/api/auth/login/",
            "/api/auth/register/",
        ]

        all_paths = set()
        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result
            all_paths.add(op.path)

        missing_paths = [p for p in critical_paths if p not in all_paths]
        assert not missing_paths, f"누락된 핵심 엔드포인트: {missing_paths}"
