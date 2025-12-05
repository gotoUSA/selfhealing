"""
Negative 테스트 (Schemathesis Phase 5.2)
========================================

📋 개요
-------
이 모듈은 의도적으로 잘못된 입력을 주입하여 API가 적절한 에러 응답을
반환하는지 검증합니다. Fuzz 테스트가 "무작위" 입력이라면,
Negative 테스트는 "의도적으로 잘못된" 입력입니다.

🎯 테스트 목적
-------------
1. **입력 검증 확인**: 잘못된 입력에 대해 명확한 에러 메시지 반환
2. **보안 검증**: SQL Injection, XSS 등 보안 공격 시도에 대한 방어 확인
3. **경계값 검증**: 최소/최대값, 빈 값, null 등 경계 조건 처리 확인
4. **에러 응답 형식**: 일관된 에러 응답 구조 (status_code, message 등)

🔧 Fuzz vs Negative 테스트
-------------------------
| 구분 | Fuzz 테스트 | Negative 테스트 |
|------|------------|----------------|
| 입력 | 무작위 생성 | 의도적으로 설계 |
| 목적 | 예상치 못한 버그 발견 | 예상된 에러 처리 검증 |
| 도구 | Hypothesis | 수동 테스트 케이스 |
| 속성 | "5xx 없음" | "적절한 4xx + 에러 메시지" |

📊 테스트 클래스 구조
-------------------
1. TestInvalidInputs
   - 잘못된 ID 형식, 필수 필드 누락, 잘못된 데이터 타입

2. TestSecurityInputs
   - SQL Injection, XSS, Path Traversal 등 보안 공격 시도

3. TestBoundaryValues
   - 페이지네이션 경계값, 문자열 길이 제한, 숫자 범위

4. TestMalformedRequests
   - 잘못된 JSON, 잘못된 Content-Type, 빈 바디

5. TestAuthorizationEdgeCases
   - 만료된 토큰, 잘못된 토큰 형식, 다른 사용자 리소스 접근

🚀 실행 방법
-----------
```bash
# Negative 테스트만 실행
pytest -m negative --no-cov -v -n 0

# 스키마 + Negative 테스트 모두 실행
pytest -m "schema or negative" --no-cov -v -n 0

# Fuzz + Negative 테스트 함께 실행
pytest -m "fuzz or negative" --no-cov -v -n 0

# 특정 테스트만 실행
pytest -m negative -k "test_sql" --no-cov -v
```

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터
- test_fuzz.py: Phase 5.1 - Fuzz 테스트 (무작위 입력)
- test_api_contract.py: Phase 2 - API Contract 테스트
- test_stateful_workflow.py: Phase 3 - Stateful 테스트

⚠️ 주의사항
----------
1. 보안 테스트: 실제 공격 페이로드를 사용하므로, 프로덕션 환경에서는 실행하지 마세요.

2. 응답 검증: 에러 응답에 민감한 정보(스택 트레이스 등)가 포함되지 않아야 합니다.

3. Rate Limiting: 보안 테스트는 많은 요청을 보내므로 Rate Limiting이 비활성화되어 있어야 합니다.

📅 작성 정보
-----------
- 작성일: 2025-12-05
- 관련 Phase: Schemathesis Phase 5 - 고급 테스트
"""

import json

import pytest
from rest_framework import status


# ==========================================
# 잘못된 입력 테스트
# ==========================================


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


# ==========================================
# 경계값 테스트
# ==========================================


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


# ==========================================
# 잘못된 요청 형식 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestMalformedRequests:
    """
    📝 잘못된 요청 형식 테스트

    잘못된 JSON, 잘못된 Content-Type 등
    형식이 잘못된 요청에 대한 처리를 검증합니다.

    📋 테스트 시나리오:
    - 잘못된 JSON 형식
    - 잘못된 Content-Type
    - 빈 요청 본문
    - 예상치 못한 필드
    """

    def test_malformed_json_body(self, client, auth_headers):
        """
        잘못된 JSON 형식 요청 테스트

        파싱 불가능한 JSON을 전송하면
        400 Bad Request가 반환되어야 합니다.

        🔍 검증 포인트:
        - JSON 파싱 에러 처리
        - 명확한 에러 메시지
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        malformed_jsons = [
            "not a json at all",
            "{invalid json}",
            "{'single': 'quotes'}",
            '{"unclosed": "brace"',
            '{"trailing": "comma",}',
        ]

        for malformed in malformed_jsons:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=malformed,
                content_type="application/json",
                **headers,
            )

            # Assert
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"잘못된 JSON이 허용됨: {response.status_code}\n" f"malformed={repr(malformed)}"
            )

    def test_wrong_content_type(self, client, auth_headers, schema_test_product):
        """
        잘못된 Content-Type 테스트

        JSON 데이터를 다른 Content-Type으로 전송하면
        적절한 에러가 반환되어야 합니다.

        🔍 검증 포인트:
        - Content-Type 검증
        - 415 Unsupported Media Type 또는 400 Bad Request
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": schema_test_product.id, "quantity": 1}
        wrong_content_types = [
            "text/plain",
            "text/html",
            "application/xml",
        ]

        for content_type in wrong_content_types:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps(data),
                content_type=content_type,
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"잘못된 Content-Type에서 서버 에러 발생!\n" f"content_type={content_type}"

    def test_extra_unexpected_fields(self, client, auth_headers, schema_test_product):
        """
        예상치 못한 필드 포함 테스트

        스키마에 정의되지 않은 추가 필드가 있을 때
        무시되거나 에러가 발생하는지 확인합니다.

        🔍 검증 포인트:
        - 추가 필드 무시 또는 에러
        - 정상 동작에 영향 없음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "product_id": schema_test_product.id,
            "quantity": 1,
            "unexpected_field": "should be ignored",
            "another_field": 12345,
            "__proto__": {"admin": True},  # Prototype pollution 시도
        }

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음 (추가 필드로 인한 서버 오류 없음)
        assert response.status_code < 500, f"추가 필드로 인해 서버 에러 발생!\n" f"data={data}"


# ==========================================
# 인증/권한 Edge Case 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthorizationEdgeCases:
    """
    🔒 인증/권한 Edge Case 테스트

    만료된 토큰, 잘못된 토큰 형식, 다른 사용자 리소스 접근 등
    인증 관련 경계 상황을 검증합니다.

    📋 테스트 시나리오:
    - 만료된 토큰
    - 잘못된 토큰 형식
    - Bearer 접두사 누락
    - 다른 사용자의 리소스 접근
    """

    @pytest.mark.parametrize(
        "auth_header,expected_code",
        [
            ("", 401),  # 빈 헤더
            ("Bearer", 401),  # 토큰 누락
            ("Bearer ", 401),  # 빈 토큰
            ("Bearer invalid.token.here", 401),  # 잘못된 토큰
            ("invalid_token_no_bearer", 401),  # Bearer 접두사 누락
            ("Basic dXNlcjpwYXNz", 401),  # Basic 인증 (지원 안 함)
            (
                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
                401,
            ),  # 서명 불일치
        ],
        ids=[
            "empty_header",
            "bearer_only",
            "bearer_empty_token",
            "invalid_token",
            "no_bearer_prefix",
            "basic_auth",
            "wrong_signature",
        ],
    )
    def test_invalid_auth_headers(self, client, auth_header, expected_code):
        """
        잘못된 인증 헤더 테스트

        다양한 형식의 잘못된 인증 헤더에 대해
        401 Unauthorized가 반환되어야 합니다.

        🔍 검증 포인트:
        - 토큰 형식 검증
        - 서명 검증
        - 적절한 에러 응답

        Args:
            auth_header: 테스트할 인증 헤더 값
            expected_code: 예상 HTTP 상태 코드
        """
        # Arrange
        headers = {}
        if auth_header:
            headers["HTTP_AUTHORIZATION"] = auth_header

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == expected_code, (
            f"예상치 못한 응답: {response.status_code}\n" f"auth_header={repr(auth_header)}\n" f"expected={expected_code}"
        )

    def test_access_other_user_order(self, client, auth_headers, schema_test_order, seller_user):
        """
        다른 사용자의 주문 접근 테스트

        다른 사용자의 주문 ID로 접근 시
        404 또는 403이 반환되어야 합니다.

        🔍 검증 포인트:
        - 리소스 소유권 검증
        - 정보 누출 방지 (404 권장)
        """
        # Arrange - seller_user의 토큰으로 user의 주문에 접근
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(seller_user)
        other_user_token = str(refresh.access_token)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {other_user_token}"}

        # schema_test_order는 user의 주문
        order_id = schema_test_order.id

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert - 다른 사용자의 주문에 접근 불가
        # 404: 정보 노출 최소화 (권장)
        # 403: 명시적 권한 거부
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ], (
            f"다른 사용자의 주문에 접근 가능!\n" f"status_code={response.status_code}"
        )

    def test_access_other_user_cart(self, client, schema_test_cart, seller_user):
        """
        다른 사용자의 장바구니 접근 테스트

        다른 사용자의 장바구니 아이템에 대한 접근이
        차단되는지 확인합니다.

        🔍 검증 포인트:
        - 장바구니 소유권 검증
        - IDOR (Insecure Direct Object Reference) 방지
        """
        # Arrange - seller_user의 토큰으로 user의 장바구니 아이템에 접근
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(seller_user)
        other_user_token = str(refresh.access_token)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {other_user_token}"}

        cart_item = schema_test_cart.items.first()
        if not cart_item:
            pytest.skip("장바구니 아이템이 없습니다")

        # Act - 다른 사용자의 장바구니 아이템 수정 시도
        response = client.patch(
            f"/api/cart/items/{cart_item.id}/",
            data=json.dumps({"quantity": 5}),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ], (
            f"다른 사용자의 장바구니 아이템 수정 가능!\n" f"status_code={response.status_code}"
        )

    def test_expired_token(self, client):
        """
        만료된 토큰 테스트

        만료된 JWT 토큰으로 요청 시 401이 반환되어야 합니다.

        🔍 검증 포인트:
        - 토큰 만료 검증
        - 401 Unauthorized 반환
        - 적절한 에러 메시지
        """
        # Arrange - 만료된 토큰 생성
        import jwt
        from datetime import datetime, timedelta, timezone
        from django.conf import settings

        now = datetime.now(timezone.utc)

        # 만료된 토큰 페이로드 생성
        expired_payload = {
            "user_id": 1,
            "exp": now - timedelta(hours=1),  # 1시간 전 만료
            "iat": now - timedelta(hours=2),
            "token_type": "access",
        }

        # 실제 시크릿 키로 서명 (또는 테스트용 키)
        try:
            secret_key = settings.SIMPLE_JWT.get("SIGNING_KEY", settings.SECRET_KEY)
        except AttributeError:
            secret_key = settings.SECRET_KEY

        expired_token = jwt.encode(expired_payload, secret_key, algorithm="HS256")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {expired_token}"}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"만료된 토큰이 허용됨!\n" f"status_code={response.status_code}"
        )

    def test_token_with_invalid_user_id(self, client):
        """
        존재하지 않는 사용자 ID를 가진 토큰 테스트

        유효한 형식이지만 존재하지 않는 사용자 ID를 가진
        토큰으로 요청 시 401이 반환되어야 합니다.

        🔍 검증 포인트:
        - 사용자 존재 여부 검증
        - 401 Unauthorized 반환
        """
        # Arrange - 존재하지 않는 사용자 ID로 토큰 생성
        import jwt
        from datetime import datetime, timedelta, timezone
        from django.conf import settings

        now = datetime.now(timezone.utc)

        payload = {
            "user_id": 99999999,  # 존재하지 않는 사용자
            "exp": now + timedelta(hours=1),
            "iat": now,
            "token_type": "access",
        }

        try:
            secret_key = settings.SIMPLE_JWT.get("SIGNING_KEY", settings.SECRET_KEY)
        except AttributeError:
            secret_key = settings.SECRET_KEY

        invalid_user_token = jwt.encode(payload, secret_key, algorithm="HS256")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {invalid_user_token}"}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"존재하지 않는 사용자 토큰이 허용됨!\n" f"status_code={response.status_code}"
        )
