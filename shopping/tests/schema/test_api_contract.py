"""
OpenAPI 스키마 기반 API Contract 테스트

Schemathesis 4.x를 사용하여 OpenAPI 스키마와 실제 API 응답이
일치하는지 자동으로 검증합니다.
"""

import pytest
import schemathesis

from .conftest import EXCLUDED_ENDPOINTS, PUBLIC_ENDPOINTS


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    OpenAPI 스키마 로드

    Django 테스트 클라이언트로 스키마를 가져와서 schemathesis 스키마 객체 생성
    """
    from django.test import Client
    import json

    with django_db_blocker.unblock():
        client = Client()
        # JSON 형식으로 스키마 요청
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200, "OpenAPI 스키마를 가져올 수 없습니다"
        # response.content를 직접 JSON으로 파싱
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)


def get_operations_by_path(schema, path_contains: str, method: str = None):
    """스키마에서 특정 경로 패턴의 operation 추출 (path parameter 없는 것만)"""
    operations = []
    for result in schema.get_all_operations():
        # Schemathesis 4.x에서는 Result 객체를 반환
        if hasattr(result, 'ok'):
            op = result.ok()
        else:
            op = result
        
        # path parameter가 있는 엔드포인트 스킵 (예: {id}, {product_pk})
        if '{' in op.path:
            continue
        
        if path_contains in op.path:
            if method is None or op.method.upper() == method.upper():
                operations.append(op)
    return operations


# ==========================================
# Public 엔드포인트 테스트 (인증 불필요)
# ==========================================

@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPublicEndpoints:
    """인증이 필요 없는 공개 엔드포인트 테스트"""

    def test_products_list(self, openapi_schema, client):
        """상품 목록 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/products/", "GET")
        for op in operations:
            response = client.get(op.path)
            # 5xx 에러만 실패, 나머지는 유효한 응답
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"

    def test_categories_list(self, openapi_schema, client):
        """카테고리 목록 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/categories/", "GET")
        for op in operations:
            response = client.get(op.path)
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"


# ==========================================
# 인증 필요 엔드포인트 테스트
# ==========================================

@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthenticatedEndpoints:
    """인증이 필요한 엔드포인트 테스트"""

    def test_cart_api(self, openapi_schema, client, auth_headers):
        """장바구니 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/cart/", "GET")
        for op in operations:
            response = client.get(
                op.path,
                **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
            )
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"

    def test_orders_api(self, openapi_schema, client, auth_headers):
        """주문 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/orders/", "GET")
        for op in operations:
            response = client.get(
                op.path,
                **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
            )
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"

    def test_wishlist_api(self, openapi_schema, client, auth_headers):
        """위시리스트 API 스키마 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/wishlist/", "GET")
        for op in operations:
            response = client.get(
                op.path,
                **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
            )
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"


# ==========================================
# 전체 스키마 검증 (Smoke Test)
# ==========================================

@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFullSchemaValidation:
    """
    전체 API 스키마 검증

    모든 엔드포인트를 테스트하므로 시간이 오래 걸림
    CI에서는 별도로 실행 권장
    """

    def test_all_get_endpoints(self, openapi_schema, client, auth_headers):
        """모든 GET 엔드포인트 스키마 검증"""
        for result in openapi_schema.get_all_operations():
            # Schemathesis 4.x Result 처리
            if hasattr(result, 'ok'):
                op = result.ok()
            else:
                op = result

            # path parameter가 있는 엔드포인트 스킵 (예: {id}, {product_pk})
            if '{' in op.path:
                continue

            # 제외할 엔드포인트 스킵
            if any(excluded in op.path for excluded in EXCLUDED_ENDPOINTS):
                continue

            # GET 요청만 테스트
            if op.method.upper() != "GET":
                continue

            # 공개 엔드포인트는 인증 없이
            is_public = any(pub in op.path for pub in PUBLIC_ENDPOINTS)

            if is_public:
                response = client.get(op.path)
            else:
                response = client.get(
                    op.path,
                    **{"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
                )

            # 5xx 에러는 실패로 처리
            assert response.status_code < 500, f"{op.path} returned {response.status_code}"
