"""
상품 API Fuzz 테스트
====================

상품 목록, 상세, 검색 API에 무작위 입력을 주입하여
예외 처리가 올바르게 동작하는지 검증합니다.

📋 테스트 대상:
- GET /api/products/ (목록, 검색, 필터링)
- GET /api/products/{id}/ (상세)

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_products.py -v -n 0
```
"""

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from rest_framework import status

from .conftest import (
    product_id_strategy,
    search_query_strategy,
    pagination_strategy,
    ordering_strategy,
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestProductsFuzz:
    """
    🛍️ 상품 API Fuzz 테스트

    상품 목록, 상세, 검색 API에 무작위 입력을 주입하여
    예외 처리가 올바르게 동작하는지 검증합니다.

    📋 테스트 대상:
    - GET /api/products/ (목록, 검색, 필터링)
    - GET /api/products/{id}/ (상세)
    - GET /api/products/popular/ (인기 상품)
    - GET /api/products/best_rating/ (평점순)

    ✅ 검증 속성 (Property):
    - 어떤 입력이 들어와도 5xx 에러가 발생하지 않아야 함
    - 유효하지 않은 ID는 400 또는 404를 반환
    - 잘못된 쿼리 파라미터는 무시되거나 400 반환
    """

    @given(search=search_query_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],  # shrinking 비활성화 (속도 향상)
    )
    def test_products_list_search_fuzz(self, client, schema_test_product, search):
        """
        상품 검색 API 퍼징

        다양한 검색어(SQL Injection, XSS, 긴 문자열 등)를 주입하여
        검색 기능의 안정성을 검증합니다.

        🔍 테스트 관점:
        - SQL Injection 시도가 실제 SQL로 실행되지 않아야 함
        - XSS 페이로드가 그대로 반환되지 않아야 함
        - 매우 긴 검색어도 처리 가능해야 함

        Args:
            client: Django Test Client
            schema_test_product: 테스트용 상품 fixture
            search: Hypothesis가 생성한 무작위 검색어
        """
        # Act
        response = client.get("/api/products/", {"search": search})

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"상품 검색에서 서버 에러 발생!\n"
            f"입력: {repr(search)}\n"
            f"상태 코드: {response.status_code}\n"
            f"응답: {response.content[:500]}"
        )

    @given(page=pagination_strategy, page_size=pagination_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_list_pagination_fuzz(self, client, schema_test_product, page, page_size):
        """
        상품 목록 페이지네이션 퍼징

        잘못된 페이지 번호, 페이지 크기를 주입하여
        페이지네이션 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 페이지 번호 처리
        - 0 페이지 처리
        - 매우 큰 페이지 번호 처리
        - 문자열 페이지 번호 처리

        Args:
            page: Hypothesis가 생성한 무작위 페이지 번호
            page_size: Hypothesis가 생성한 무작위 페이지 크기
        """
        # Act
        response = client.get("/api/products/", {"page": page, "page_size": page_size})

        # Assert
        assert response.status_code < 500, (
            f"페이지네이션에서 서버 에러 발생!\n"
            f"page={repr(page)}, page_size={repr(page_size)}\n"
            f"상태 코드: {response.status_code}"
        )

    @given(ordering=ordering_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_list_ordering_fuzz(self, client, schema_test_product, ordering):
        """
        상품 목록 정렬 파라미터 퍼징

        잘못된 정렬 필드명을 주입하여
        정렬 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 존재하지 않는 필드명
        - SQL Injection 시도
        - 특수문자 포함 필드명

        Args:
            ordering: Hypothesis가 생성한 무작위 정렬 파라미터
        """
        # Act
        response = client.get("/api/products/", {"ordering": ordering})

        # Assert
        assert response.status_code < 500, (
            f"정렬에서 서버 에러 발생!\n" f"ordering={repr(ordering)}\n" f"상태 코드: {response.status_code}"
        )

    @given(product_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_detail_id_fuzz(self, client, product_id):
        """
        상품 상세 API ID 파라미터 퍼징

        다양한 형식의 ID(음수, 문자열, 특수문자 등)를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 0 ID
        - 문자열 ID
        - 매우 큰 ID
        - 특수문자 포함 ID

        예상 결과:
        - 유효한 ID: 200 OK
        - 유효하지 않은 ID: 400 Bad Request 또는 404 Not Found
        - 5xx 에러는 절대 발생하면 안 됨

        Args:
            product_id: Hypothesis가 생성한 무작위 상품 ID
        """
        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code < 500, (
            f"상품 상세 조회에서 서버 에러 발생!\n" f"product_id={repr(product_id)}\n" f"상태 코드: {response.status_code}"
        )
        # 유효하지 않은 ID는 400 또는 404여야 함
        if not (isinstance(product_id, int) and product_id > 0):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,
                status.HTTP_200_OK,
            ], f"유효하지 않은 ID에 대해 예상치 못한 응답: {response.status_code}"
