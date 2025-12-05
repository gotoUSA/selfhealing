# test_public_endpoints.py
# 공개 API Contract 테스트

import pytest
from rest_framework import status

from ..conftest import (
    assert_list_response,
    assert_paginated_response,
    assert_product_schema,
    get_operations_by_path,
)


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPublicEndpointsContract:
    """
    🌐 인증이 필요 없는 공개 엔드포인트 Contract 테스트

    인증 없이 접근 가능한 API의 응답이 스키마와 일치하는지 검증합니다.
    단순 status code 확인을 넘어 응답 구조/타입까지 검증합니다.

    📋 검증 대상:
    - GET /api/products/ (목록, 인기, 평점순)
    - GET /api/categories/ (목록, 트리)
    - GET /api/products/{id}/ (상세)
    - GET /api/categories/{id}/ (상세)

    ✅ 검증 항목:
    - 200 OK 응답
    - 페이지네이션 구조 (count, results)
    - 상품/카테고리 필수 필드 존재
    - 필드 타입 일치
    """

    def test_products_list_contract(self, openapi_schema, client, schema_test_product):
        """
        🛍️ 상품 목록 API Contract 검증

        페이지네이션 응답 구조와 상품 필드 스키마를 검증합니다.
        일부 엔드포인트(best_rating 등)는 list를 직접 반환할 수 있습니다.
        """
        public_product_paths = ["/api/products/", "/api/products/popular/", "/api/products/best_rating/"]
        operations = get_operations_by_path(openapi_schema, "/api/products/", "GET")

        for op in operations:
            if op.path not in public_product_paths:
                continue

            # Act
            response = client.get(op.path)

            # Assert - Status
            assert response.status_code == status.HTTP_200_OK, f"{op.path} returned {response.status_code}"

            # Assert - Contract: 응답 구조 (paginated dict 또는 list)
            data = response.json()

            # 일부 엔드포인트는 list를 직접 반환 (best_rating 등)
            if isinstance(data, list):
                results = data
            else:
                # 페이지네이션 응답
                assert_paginated_response(data, context=op.path)
                results = assert_list_response(data, context=op.path)

            # Assert - Contract: 상품 스키마
            for product in results[:3]:
                assert_product_schema(product, context=f"{op.path} item")

    def test_categories_list_contract(self, openapi_schema, client, schema_test_category):
        """📂 카테고리 목록 API Contract 검증"""
        operations = get_operations_by_path(openapi_schema, "/api/categories/", "GET")

        for op in operations:
            if op.path == "/api/categories/tree/":
                continue

            response = client.get(op.path)

            assert response.status_code == status.HTTP_200_OK, f"{op.path} returned {response.status_code}"

            data = response.json()
            results = assert_list_response(data, context=op.path)

            for category in results[:3]:
                assert isinstance(category, dict), f"{op.path}: 카테고리가 dict가 아님"
                assert "id" in category, f"{op.path}: 'id' 필드 누락"
                assert "name" in category, f"{op.path}: 'name' 필드 누락"

    def test_products_detail_contract(self, openapi_schema, client, schema_test_product):
        """🛍️ 상품 상세 API Contract 검증"""
        product_id = schema_test_product.id

        response = client.get(f"/api/products/{product_id}/")

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert_product_schema(data, context=f"/api/products/{product_id}/")
        assert data["id"] == product_id
        assert data["name"] == schema_test_product.name

    def test_categories_detail_contract(self, openapi_schema, client, schema_test_category):
        """📂 카테고리 상세 API Contract 검증"""
        category_id = schema_test_category.id

        response = client.get(f"/api/categories/{category_id}/")

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert isinstance(data, dict), "응답이 dict가 아님"
        assert data["id"] == category_id
        assert "name" in data

    def test_categories_tree_contract(self, openapi_schema, client, schema_test_category):
        """🌳 카테고리 트리 API Contract 검증"""
        response = client.get("/api/categories/tree/")

        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert isinstance(data, list), "트리 응답이 list가 아님"
