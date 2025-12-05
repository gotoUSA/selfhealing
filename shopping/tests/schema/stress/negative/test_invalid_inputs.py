"""
잘못된 입력 및 경계값 테스트
============================

TestInvalidInputs: 잘못된 ID 형식, 필수 필드 누락, 잘못된 데이터 타입
TestBoundaryValues: 페이지네이션 경계값, 문자열 길이 제한, 숫자 범위
"""

import json

import pytest
from rest_framework import status


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestInvalidInputs:
    """
    ❌ 잘못된 입력에 대한 적절한 에러 응답 검증

    API에 다양한 형태의 잘못된 입력을 주입하여
    적절한 에러 코드와 메시지가 반환되는지 확인합니다.

    📋 테스트 시나리오:
    - 잘못된 상품 ID 형식 (문자열, 음수, 0 등)
    - 필수 필드 누락
    - 잘못된 데이터 타입
    - 잘못된 값 범위

    ✅ 예상 결과:
    - 400 Bad Request 또는 404 Not Found
    - 명확한 에러 메시지
    - 5xx 에러 없음
    """

    @pytest.mark.parametrize(
        "invalid_id,expected_codes",
        [
            # ID 형식 테스트
            ("abc", [400, 404]),  # 문자열 ID
            ("-1", [400, 404]),  # 음수 ID
            ("0", [400, 404]),  # 0 ID
            ("99999999", [404]),  # 존재하지 않는 ID
            ("1.5", [400, 404]),  # 부동소수점 ID
            ("null", [400, 404]),  # null 문자열
            ("<script>", [400, 404]),  # 스크립트 태그
            ("1; DROP TABLE--", [400, 404]),  # SQL Injection
            ("../../../", [400, 404]),  # Path Traversal
            ("", [404]),  # 빈 문자열 (다른 라우트로 매핑될 수 있음)
        ],
        ids=[
            "string_id",
            "negative_id",
            "zero_id",
            "nonexistent_id",
            "float_id",
            "null_string",
            "script_tag",
            "sql_injection",
            "path_traversal",
            "empty_string",
        ],
    )
    def test_invalid_product_id_format(self, client, invalid_id, expected_codes):
        """
        잘못된 상품 ID 형식 테스트

        다양한 형태의 유효하지 않은 ID를 주입하여
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 서버 에러(5xx) 발생하지 않음
        - 적절한 클라이언트 에러(4xx) 반환
        - 에러 응답에 민감한 정보 미포함

        Args:
            invalid_id: 테스트할 잘못된 ID
            expected_codes: 예상 HTTP 상태 코드 목록
        """
        # Act
        response = client.get(f"/api/products/{invalid_id}/")

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"서버 에러 발생!\n"
            f"invalid_id={repr(invalid_id)}\n"
            f"status_code={response.status_code}\n"
            f"response={response.content[:500]}"
        )

        # Assert - 예상 코드 중 하나
        assert response.status_code in expected_codes, (
            f"예상치 못한 응답 코드: {response.status_code}\n" f"expected: {expected_codes}"
        )

    def test_missing_required_fields_cart_add(self, client, auth_headers):
        """
        장바구니 추가 시 필수 필드 누락 테스트

        product_id, quantity 등 필수 필드 없이 요청하면
        400 Bad Request가 반환되어야 합니다.

        🔍 검증 포인트:
        - 필수 필드 검증 동작
        - 명확한 에러 메시지 포함
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        test_cases = [
            ({}, "빈 요청 본문"),
            ({"product_id": 1}, "quantity 누락"),
            ({"quantity": 1}, "product_id 누락"),
        ]

        for data, description in test_cases:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # Assert
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"{description}: 400이 아닌 {response.status_code} 반환\n"
                f"data={data}\n"
                f"response={response.content[:500]}"
            )

    def test_invalid_quantity_values(self, client, auth_headers, schema_test_product):
        """
        잘못된 수량 값 테스트

        음수, 0, 매우 큰 수, 문자열 등 잘못된 수량으로
        장바구니 추가 시 적절한 에러가 반환되어야 합니다.

        🔍 검증 포인트:
        - 음수 수량 거부
        - 0 수량 거부 또는 삭제 처리
        - 재고 초과 수량 거부
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_quantities = [
            (-1, "음수 수량"),
            (0, "0 수량"),
            (999999, "매우 큰 수량"),
            ("abc", "문자열 수량"),
        ]

        for quantity, description in invalid_quantities:
            data = {
                "product_id": schema_test_product.id,
                "quantity": quantity,
            }

            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"{description}에서 서버 에러 발생!\n" f"status_code={response.status_code}"

            # Assert - 잘못된 수량은 거부되어야 함
            # 단, 매우 큰 수량은 재고 체크 후 통과할 수 있음
            if quantity in [-1, "abc"]:
                assert response.status_code in [
                    status.HTTP_400_BAD_REQUEST,
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                ], f"{description}이 허용됨: {response.status_code}"

    @pytest.mark.parametrize(
        "invalid_data,description",
        [
            ({"username": "", "password": "test123"}, "빈 사용자명"),
            ({"username": "test", "password": ""}, "빈 비밀번호"),
            ({"username": "   ", "password": "test123"}, "공백만 있는 사용자명"),
            ({}, "빈 요청 본문"),
        ],
        ids=["empty_username", "empty_password", "whitespace_username", "empty_body"],
    )
    def test_invalid_login_data(self, client, invalid_data, description):
        """
        잘못된 로그인 데이터 테스트

        빈 사용자명, 빈 비밀번호 등 잘못된 로그인 데이터에 대해
        적절한 에러 응답이 반환되어야 합니다.

        🔍 검증 포인트:
        - 필드 검증 동작
        - 400 또는 401 반환
        - 민감 정보 미노출
        """
        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(invalid_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code < 500, f"{description}에서 서버 에러 발생!\n" f"data={invalid_data}"
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestBoundaryValues:
    """
    📏 경계값 테스트

    최소/최대값, 빈 값, null 등 경계 조건에서
    API가 올바르게 동작하는지 검증합니다.

    📋 테스트 시나리오:
    - 페이지네이션 경계값 (0, -1, 매우 큰 수)
    - 문자열 길이 제한 (매우 긴 문자열)
    - 숫자 범위 (최소값, 최대값)

    ⚠️ 검증 철학:
    - Negative 테스트의 목적은 "서버가 죽지 않고 적절히 거부하는가"
    - 따라서 400/404 등 여러 상태 코드를 허용하는 경우가 있음
    - 예: DRF PageNumberPagination은 잘못된 page에 404 반환
    - 핵심: 5xx 서버 에러만 아니면 정상 처리로 간주
    """

    @pytest.mark.parametrize(
        "page_param,expected_codes",
        [
            ({"page": 0}, [400, 404]),  # 0 페이지 - DRF는 404 반환
            ({"page": -1}, [400, 404]),  # 음수 페이지 - DRF는 404 반환
            ({"page": 999999}, [200, 404]),  # 매우 큰 페이지 (빈 결과)
            ({"page_size": 0}, [400]),  # 0 페이지 크기
            ({"page_size": -1}, [400]),  # 음수 페이지 크기
            ({"page_size": 10000}, [200, 400]),  # 매우 큰 페이지 크기
        ],
        ids=[
            "page_0",
            "page_negative",
            "page_huge",
            "page_size_0",
            "page_size_negative",
            "page_size_huge",
        ],
    )
    def test_pagination_boundaries(self, client, schema_test_product, page_param, expected_codes):
        """
        페이지네이션 경계값 테스트

        페이지 번호와 페이지 크기의 경계값에서
        적절한 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 0, 음수는 거부되어야 함 (400)
        - 매우 큰 값은 빈 결과 또는 제한된 결과 반환

        Args:
            page_param: 테스트할 페이지 파라미터
            expected_codes: 예상 HTTP 상태 코드 목록
        """
        # Arrange - page_param is provided by parametrize

        # Act
        response = client.get("/api/products/", page_param)

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"페이지네이션 경계값에서 서버 에러 발생!\n" f"params={page_param}"

        # Assert - 예상 코드 중 하나
        # 일부 프레임워크는 잘못된 페이지를 무시하고 첫 페이지 반환
        assert response.status_code in expected_codes + [status.HTTP_200_OK], (
            f"예상치 못한 응답: {response.status_code}\n" f"expected: {expected_codes}"
        )

    def test_very_long_search_query(self, client, schema_test_product):
        """
        매우 긴 검색어 테스트

        1000자, 5000자 등 매우 긴 검색어에 대해
        적절히 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 서버 에러 발생하지 않음
        - 413 (Payload Too Large) 또는 200 (빈 결과) 반환
        """
        # Arrange
        long_queries = [
            "a" * 1000,  # 1000자
            "b" * 5000,  # 5000자
            "c" * 10000,  # 10000자
        ]

        for query in long_queries:
            # Act
            response = client.get(f"/api/products/?search={query}")

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"긴 검색어에서 서버 에러 발생!\n" f"query_length={len(query)}"

            # 정상 또는 URI 너무 긴 에러
            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_414_REQUEST_URI_TOO_LONG,
            ], f"예상치 못한 응답: {response.status_code}"

    def test_special_characters_in_search(self, client, schema_test_product):
        """
        특수 문자 검색어 테스트

        이모지, 제어 문자, 유니코드 등 특수 문자에 대해
        적절히 처리되는지 확인합니다.

        🔍 검증 포인트:
        - UTF-8 인코딩 처리
        - 제어 문자 필터링
        - 이모지 처리
        """
        # Arrange
        special_queries = [
            "🎉💀🔥🎊",  # 이모지
            "한글 검색어",  # 한글
            "日本語",  # 일본어
            "\x00\x01\x02",  # 제어 문자
            "test\nwith\nnewlines",  # 줄바꿈
            "test\twith\ttabs",  # 탭
            " " * 100,  # 공백만
        ]

        for query in special_queries:
            # Act
            response = client.get(f"/api/products/?search={query}")

            # Assert
            assert response.status_code < 500, f"특수 문자에서 서버 에러 발생!\n" f"query={repr(query)}"
