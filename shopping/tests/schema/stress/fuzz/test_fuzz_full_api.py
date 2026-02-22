"""
전체 API Fuzz 테스트 (Slow)
===========================

OpenAPI 스키마를 기반으로 모든 GET 엔드포인트에
무작위 쿼리 파라미터를 주입하여 안정성을 검증합니다.

⚠️ 이 테스트는 시간이 오래 걸리므로 @pytest.mark.slow로 표시되어 있습니다.
CI에서는 Nightly 빌드에서만 실행하는 것을 권장합니다.

🚀 실행 방법:
```bash
# slow 마커 포함 실행
pytest -m "fuzz and slow" --no-cov -v -n 0 --max-examples=100

# 이 파일만 실행
pytest shopping/tests/schema/stress/fuzz/test_fuzz_full_api.py -v -n 0
```
"""

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from hypothesis import strategies as st

from ...conftest import (
    is_excluded_endpoint,
    is_public_endpoint,
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFullApiFuzz:
    """
    🌐 전체 API Fuzz 테스트

    OpenAPI 스키마를 기반으로 모든 GET 엔드포인트에
    무작위 쿼리 파라미터를 주입하여 안정성을 검증합니다.

    ⚠️ 이 테스트는 시간이 오래 걸리므로 @pytest.mark.slow로 표시되어 있습니다.
    CI에서는 Nightly 빌드에서만 실행하는 것을 권장합니다.

    🚀 실행 방법:
    ```bash
    # slow 마커 포함 실행
    pytest -m "fuzz and slow" --no-cov -v -n 0 --max-examples=100
    ```
    """

    def test_all_get_endpoints_no_500(self, openapi_schema, client, auth_headers, schema_test_product):
        """
        모든 GET 엔드포인트에서 5xx 에러 없음 확인

        OpenAPI 스키마에 정의된 모든 GET 엔드포인트를 순회하며
        기본 요청을 보내 서버 에러가 발생하지 않는지 확인합니다.

        🔍 테스트 관점:
        - 모든 GET 엔드포인트 접근 가능성
        - 인증 필요 엔드포인트는 헤더 추가
        - 제외 엔드포인트는 스킵

        Args:
            openapi_schema: OpenAPI 스키마 객체
            client: Django Test Client
            auth_headers: 인증 헤더
            schema_test_product: 테스트 데이터
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        errors = []

        # Act & Assert
        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            # GET 요청만 테스트
            if op.method.upper() != "GET":
                continue

            # 제외 엔드포인트 스킵
            if is_excluded_endpoint(op.path):
                continue

            # path parameter가 있으면 스킵 (별도 테스트에서 처리)
            if "{" in op.path:
                continue

            # 공개 API vs 인증 필요 API
            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **headers)

            if response.status_code >= 500:
                errors.append(f"{op.path}: {response.status_code}")

        # Assert
        assert not errors, "5xx 에러 발생 엔드포인트:\n" + "\n".join(errors)

    @given(
        search=st.text(min_size=0, max_size=100),
        page=st.integers(min_value=-10, max_value=1000),
        ordering=st.sampled_from(["", "price", "-price", "invalid"]),
    )
    @hypothesis_settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_combined_params_fuzz(self, client, schema_test_product, search, page, ordering):
        """
        상품 목록 복합 파라미터 퍼징

        여러 쿼리 파라미터를 동시에 조합하여
        복합 필터링 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 검색 + 정렬 + 페이지네이션 조합
        - 잘못된 파라미터 혼합
        - 빈 파라미터 처리

        Args:
            search: 무작위 검색어
            page: 무작위 페이지 번호
            ordering: 무작위 정렬 필드
        """
        # Act
        params = {}
        if search:
            params["search"] = search
        if page != 0:
            params["page"] = page
        if ordering:
            params["ordering"] = ordering

        response = client.get("/api/products/", params)

        # Assert
        assert response.status_code < 500, (
            f"복합 파라미터에서 서버 에러 발생!\n" f"params={params}\n" f"상태 코드: {response.status_code}"
        )
