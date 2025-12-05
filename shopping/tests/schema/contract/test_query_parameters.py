# test_query_parameters.py
# Query Parameter Edge Cases 테스트

import pytest
from rest_framework import status


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestQueryParameterEdgeCases:
    """
    🔍 Query Parameter Edge Case 테스트

    URL 쿼리 파라미터에 다양한 값을 주입하여
    API가 적절히 처리하는지 검증합니다.

    📋 테스트 시나리오:
    - page: 음수, 0, 매우 큰 수, 문자열
    - search: 특수문자, SQL Injection, XSS
    - ordering: 존재하지 않는 필드, SQL Injection

    ✅ 핵심 검증:
    - 5xx 에러 발생하지 않음
    - 적절한 에러 응답 또는 빈 결과
    """

    @pytest.mark.parametrize(
        "page_value,description",
        [
            ("-1", "음수 페이지"),
            ("0", "0 페이지"),
            ("999999", "매우 큰 페이지"),
            ("abc", "문자열 페이지"),
            ("1.5", "float 페이지"),
            ("null", "null 문자열"),
        ],
        ids=["negative", "zero", "huge", "string", "float", "null_str"],
    )
    def test_products_list_invalid_page(self, client, schema_test_product, page_value, description):
        """
        📄 상품 목록 - 잘못된 페이지 파라미터

        잘못된 page 값에도 5xx 에러 없이 처리되어야 합니다.
        """
        response = client.get(f"/api/products/?page={page_value}")

        # 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 또는 200 (빈 결과) 또는 404 (페이지 없음)
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

    @pytest.mark.parametrize(
        "search_value,description",
        [
            ("'; DROP TABLE products;--", "SQL Injection"),
            ("<script>alert('xss')</script>", "XSS 시도"),
            ("" * 1000, "매우 긴 검색어"),
            ("%00", "Null byte"),
            ("../../../etc/passwd", "Path Traversal"),
            ("🎉🔥💯", "이모지"),
        ],
        ids=["sql_injection", "xss", "long_query", "null_byte", "path_traversal", "emoji"],
    )
    def test_products_search_malicious_input(self, client, schema_test_product, search_value, description):
        """
        🔍 상품 검색 - 악성 입력 처리

        악성 검색어에도 5xx 에러 없이 처리되어야 합니다.
        """
        response = client.get(f"/api/products/?search={search_value}")

        # 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 정상 응답
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

        # 200이면 유효한 JSON 구조
        if response.status_code == status.HTTP_200_OK:
            data = response.json()
            assert isinstance(data, (dict, list)), f"{description}: 응답이 dict/list가 아님"

    @pytest.mark.parametrize(
        "ordering_value,description",
        [
            ("nonexistent_field", "존재하지 않는 필드"),
            ("-nonexistent", "존재하지 않는 필드 역순"),
            ("'; DROP TABLE--", "SQL Injection"),
            ("price,name,id,created_at,updated_at,stock", "너무 많은 정렬 필드"),
        ],
        ids=["nonexistent", "nonexistent_desc", "sql_injection", "too_many"],
    )
    def test_products_ordering_invalid(self, client, schema_test_product, ordering_value, description):
        """
        📊 상품 정렬 - 잘못된 ordering 파라미터

        잘못된 ordering 값에도 5xx 에러 없이 처리되어야 합니다.
        """
        response = client.get(f"/api/products/?ordering={ordering_value}")

        # 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

    def test_products_combined_query_params(self, client, schema_test_product):
        """
        🔗 복합 쿼리 파라미터 테스트

        여러 쿼리 파라미터를 동시에 사용할 때 정상 동작하는지 검증합니다.
        """
        response = client.get("/api/products/?page=1&search=test&ordering=-price")

        assert response.status_code < 500, "서버 에러 발생"
        assert response.status_code == status.HTTP_200_OK

        data = response.json()
        assert isinstance(data, (dict, list))

    def test_categories_filter_params(self, client, schema_test_category):
        """
        📂 카테고리 필터 파라미터 테스트
        """
        response = client.get("/api/categories/?parent=null")

        assert response.status_code < 500, "서버 에러 발생"
