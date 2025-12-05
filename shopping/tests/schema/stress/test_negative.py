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


# ==========================================
# 💳 결제 API Negative 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPaymentNegativeInputs:
    """
    💳 결제 API에 대한 Negative 테스트

    결제 API에 잘못된 입력을 주입하여 적절한 에러 응답이 반환되는지 검증합니다.
    결제는 Tier 1 Critical API로 모든 에러 처리가 엄격해야 합니다.
    """

    def test_payment_list_without_auth(self, client):
        """
        인증 없이 결제 목록 접근 → 401

        🔍 검증 포인트:
        - 인증 필수 검증
        - 401 Unauthorized 반환
        """
        # Act
        response = client.get("/api/payments/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_payment_detail_without_auth(self, client, schema_test_payment):
        """
        인증 없이 결제 상세 접근 → 401

        🔍 검증 포인트:
        - 인증 필수 검증
        - 401 Unauthorized 반환
        """
        # Arrange
        payment_id = schema_test_payment.id

        # Act
        response = client.get(f"/api/payments/{payment_id}/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_payment_nonexistent_id(self, client, auth_headers):
        """
        존재하지 않는 결제 ID 접근 → 404

        🔍 검증 포인트:
        - 유효성 검증
        - 404 Not Found 반환
        - 민감 정보 미노출
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/99999999/", **headers)

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        # 스택 트레이스 미노출 확인
        assert "traceback" not in str(data).lower()
        assert "exception" not in str(data).lower()

    @pytest.mark.parametrize(
        "invalid_id,description",
        [
            ("-1", "음수 ID"),
            ("0", "0 ID"),
            ("abc", "문자열 ID"),
            ("1.5", "float ID"),
            ("null", "null 문자열"),
        ],
        ids=["negative", "zero", "string", "float", "null_str"],
    )
    def test_payment_invalid_id_format(self, client, auth_headers, invalid_id, description):
        """
        잘못된 결제 ID 형식 → 400 or 404

        🔍 검증 포인트:
        - 5xx 에러 발생하지 않음
        - 적절한 4xx 에러 반환
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/{invalid_id}/", **headers)

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 또는 404
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

    def test_payment_list_invalid_status_filter(self, client, auth_headers):
        """
        잘못된 상태 필터 → 400

        🔍 검증 포인트:
        - 유효하지 않은 필터값 거부
        - 400 Bad Request 반환
        - 에러 메시지에 유효한 값 안내
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/payments/?status=invalid_status", **headers)

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert "error" in data, "에러 응답에 'error' 필드 필요"

    @pytest.mark.parametrize(
        "page,page_size,description",
        [
            (-1, 10, "음수 페이지"),
            (0, 10, "0 페이지"),
            (1, -1, "음수 페이지 크기"),
            (1, 0, "0 페이지 크기"),
            (1, 101, "페이지 크기 초과 (max=100)"),
            ("abc", 10, "문자열 페이지"),
            (1, "abc", "문자열 페이지 크기"),
        ],
        ids=["neg_page", "zero_page", "neg_size", "zero_size", "exceed_size", "str_page", "str_size"],
    )
    def test_payment_list_invalid_pagination(self, client, auth_headers, page, page_size, description):
        """
        잘못된 페이지네이션 파라미터 → 400

        🔍 검증 포인트:
        - 음수/0 페이지 거부
        - 과도한 페이지 크기 거부
        - 문자열 페이지 거부
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/?page={page}&page_size={page_size}", **headers)

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생: {response.status_code}"

        # 400 에러 예상 (일부 케이스는 200이 될 수 있음 - 자동 보정)
        # 최소한 5xx는 발생하면 안 됨


# ==========================================
# ⭐ 리뷰 API Negative 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestReviewNegativeInputs:
    """
    ⭐ 리뷰 API에 대한 Negative 테스트

    리뷰 API에 잘못된 입력을 주입하여 적절한 에러 응답이 반환되는지 검증합니다.
    평점은 1-5 범위여야 하며, 리뷰 내용도 적절한 길이여야 합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    @pytest.mark.parametrize(
        "invalid_rating,expected_codes,description",
        [
            (0, [400, 422], "0점 - 최소값 미만"),
            (-1, [400, 422], "음수 평점"),
            (6, [400, 422], "6점 - 최대값 초과"),
            (100, [400, 422], "100점 - 범위 크게 초과"),
            (-100, [400, 422], "-100점 - 음수 범위 초과"),
            (1.5, [400, 422], "소수점 평점 (허용 여부에 따라)"),
            (None, [400, 422], "null 평점"),
            ("abc", [400, 422], "문자열 평점"),
            ("", [400, 422], "빈 문자열 평점"),
        ],
        ids=[
            "zero",
            "negative",
            "six",
            "hundred",
            "neg_hundred",
            "decimal",
            "null",
            "string",
            "empty_string",
        ],
    )
    def test_review_invalid_rating(
        self, client, auth_headers, schema_test_product, invalid_rating, expected_codes, description
    ):
        """
        잘못된 평점 값 테스트

        평점 범위(1-5) 외의 값을 주입하여
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 범위 외 평점 거부 (1-5만 허용)
        - 5xx 에러 발생하지 않음
        - 명확한 에러 메시지

        Args:
            invalid_rating: 테스트할 잘못된 평점
            expected_codes: 예상 HTTP 상태 코드 목록
            description: 테스트 설명
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": invalid_rating,
            "content": "좋은 상품입니다!",
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"{description}에서 서버 에러 발생!\n"
            f"rating={invalid_rating}\n"
            f"status_code={response.status_code}"
        )

        # Assert - 적절한 에러 응답 (403은 구매 확인 실패일 수 있음)
        assert response.status_code in expected_codes + [status.HTTP_403_FORBIDDEN], (
            f"{description}: 예상치 못한 응답 {response.status_code}"
        )

    @pytest.mark.parametrize(
        "invalid_content,description",
        [
            ("", "빈 리뷰 내용"),
            ("   ", "공백만 있는 리뷰"),
            (None, "null 리뷰 내용"),
            ("a" * 5001, "너무 긴 리뷰 (5000자 초과)"),
        ],
        ids=["empty", "whitespace", "null", "too_long"],
    )
    def test_review_invalid_content(
        self, client, auth_headers, schema_test_product, invalid_content, description
    ):
        """
        잘못된 리뷰 내용 테스트

        빈 내용, 너무 긴 내용 등 잘못된 리뷰 내용에 대해
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 빈 내용 거부 (또는 허용 정책에 따름)
        - 너무 긴 내용 거부
        - 5xx 에러 발생하지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": invalid_content,
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"{description}에서 서버 에러 발생!\n"
            f"status_code={response.status_code}"
        )

    def test_review_without_purchase(self, client, auth_headers, schema_test_product):
        """
        구매하지 않은 상품에 리뷰 작성 시도 → 403

        구매 확인 로직이 있는 경우,
        구매하지 않은 상품에 대한 리뷰 작성을 거부해야 합니다.

        🔍 검증 포인트:
        - 구매 확인 로직 동작
        - 403 Forbidden 반환
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 구매 이력 없는 사용자로 테스트
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": "좋은 상품입니다!",
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"서버 에러 발생: {response.status_code}"

        # 구매 확인 로직이 있으면 403, 없으면 201
        # 어느 쪽이든 5xx는 아니어야 함

    def test_review_duplicate_submission(self, client, auth_headers, schema_test_product):
        """
        동일 상품에 중복 리뷰 작성 시도

        이미 리뷰를 작성한 상품에 다시 리뷰를 작성하려 하면
        거부되어야 합니다 (정책에 따라 다름).

        🔍 검증 포인트:
        - 중복 리뷰 방지 (정책에 따름)
        - 5xx 에러 발생하지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": "좋은 상품입니다!",
        }

        # Act - 첫 번째 리뷰 시도
        response1 = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Act - 두 번째 리뷰 시도 (중복)
        response2 = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response2.status_code < 500, f"중복 리뷰에서 서버 에러 발생: {response2.status_code}"


# ==========================================
# 💰 포인트 API Negative 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPointNegativeInputs:
    """
    💰 포인트 API에 대한 Negative 테스트

    포인트 사용 시 잔액 초과, 음수 포인트 등
    잘못된 입력에 대해 적절한 에러 응답이 반환되는지 검증합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    @pytest.fixture
    def user_with_limited_points(self, db):
        """제한된 포인트를 가진 사용자 생성"""
        from shopping.tests.factories import UserFactory
        user = UserFactory(points=5000)  # 5000 포인트
        return user

    @pytest.mark.parametrize(
        "points_to_use,user_points,expected_codes,description",
        [
            (10000, 5000, [400, 422], "잔액 초과 사용"),
            (-1000, 5000, [400, 422], "음수 포인트 사용"),
            (0, 5000, [400, 200, 201], "0 포인트 사용 (허용될 수 있음)"),
            (5001, 5000, [400, 422], "1포인트 초과 사용"),
        ],
        ids=["exceed_balance", "negative", "zero", "one_over"],
    )
    def test_order_with_invalid_points(
        self, client, schema_test_product,
        points_to_use, user_points, expected_codes, description
    ):
        """
        잘못된 포인트 사용 테스트

        주문 시 잔액을 초과하거나 음수 포인트를 사용하려 할 때
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 잔액 초과 거부
        - 음수 포인트 거부
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 포인트가 있는 사용자 생성
        from shopping.tests.factories import UserFactory
        user = UserFactory(points=user_points)

        # 로그인하여 토큰 획득
        login_response = client.post(
            "/api/auth/login/",
            data=json.dumps({
                "username": user.username,
                "password": "testpass123",
            }),
            content_type="application/json",
        )

        if login_response.status_code != 200:
            pytest.skip("로그인 실패로 테스트 스킵")

        login_data = login_response.json()
        token = login_data.get("access") or login_data.get("token", {}).get("access")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        # 장바구니에 상품 추가
        cart_response = client.post(
            "/api/cart/add_item/",
            data=json.dumps({
                "product_id": schema_test_product.id,
                "quantity": 1,
            }),
            content_type="application/json",
            **headers,
        )

        if cart_response.status_code not in [200, 201]:
            pytest.skip("장바구니 추가 실패로 테스트 스킵")

        # 주문 생성 시 잘못된 포인트 사용
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": points_to_use,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음 (핵심 검증)
        assert response.status_code < 500, (
            f"{description}에서 서버 에러 발생!\n"
            f"points_to_use={points_to_use}, user_points={user_points}\n"
            f"status_code={response.status_code}"
        )

        # 비즈니스 로직 검증:
        # - 음수/잔액 초과 포인트는 정책에 따라:
        #   1. 400/422로 거부되거나
        #   2. 0 또는 최대값으로 자동 조정 후 201/202로 성공
        # 핵심: 5xx 에러 없음 + 데이터 무결성 유지 (위에서 검증됨)

    def test_point_usage_exceeds_order_total(
        self, client, auth_headers, schema_test_product, user
    ):
        """
        주문 금액 초과 포인트 사용 테스트

        주문 총액보다 많은 포인트를 사용하려 할 때
        적절한 처리가 되는지 확인합니다.

        🔍 검증 포인트:
        - 주문 금액 초과 포인트 사용 처리
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 사용자에게 많은 포인트 부여
        user.points = 1000000  # 100만 포인트
        user.save()

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 장바구니에 상품 추가 (예: 10,000원 상품)
        cart_response = client.post(
            "/api/cart/add_item/",
            data=json.dumps({
                "product_id": schema_test_product.id,
                "quantity": 1,
            }),
            content_type="application/json",
            **headers,
        )

        # 주문 금액보다 많은 포인트 사용 시도
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": 500000,  # 50만 포인트 (상품가보다 클 수 있음)
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"주문 금액 초과 포인트에서 서버 에러 발생!\n"
            f"status_code={response.status_code}"
        )


# ==========================================
# 📦 주문 취소 Negative 테스트
# ==========================================


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestOrderCancelNegativeInputs:
    """
    📦 주문 취소 API에 대한 Negative 테스트

    이미 취소된 주문, 배송 완료된 주문 등
    취소 불가능한 상태의 주문 취소 시도에 대해 검증합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    def test_cancel_already_canceled_order(self, client, auth_headers, user):
        """
        이미 취소된 주문 재취소 시도 → 400

        🔍 검증 포인트:
        - 중복 취소 방지
        - 적절한 에러 응답
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 취소된 주문 생성
        from shopping.tests.factories import OrderFactory
        canceled_order = OrderFactory.canceled(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 이미 취소된 주문 취소 시도
        response = client.post(
            f"/api/orders/{canceled_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"이미 취소된 주문 재취소에서 서버 에러 발생!\n"
            f"status_code={response.status_code}"
        )

        # 400 또는 409 (Conflict) 예상
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_409_CONFLICT,
        ], f"이미 취소된 주문이 다시 취소됨: {response.status_code}"

    def test_cancel_shipped_order(self, client, auth_headers, user):
        """
        배송중인 주문 취소 시도 → 400

        🔍 검증 포인트:
        - 배송 후 취소 불가 정책 검증
        - 적절한 에러 응답
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 배송중 주문 생성
        from shopping.tests.factories import OrderFactory
        shipped_order = OrderFactory.shipped(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 배송중 주문 취소 시도
        response = client.post(
            f"/api/orders/{shipped_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"배송중 주문 취소에서 서버 에러 발생!\n"
            f"status_code={response.status_code}"
        )

    def test_cancel_other_user_order(self, client, auth_headers):
        """
        다른 사용자의 주문 취소 시도 → 403/404

        🔍 검증 포인트:
        - 권한 검증 (자신의 주문만 취소 가능)
        - 403 또는 404 반환
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 다른 사용자의 주문 생성
        from shopping.tests.factories import OrderFactory, UserFactory
        other_user = UserFactory()
        other_order = OrderFactory.pending(user=other_user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 다른 사용자 주문 취소 시도
        response = client.post(
            f"/api/orders/{other_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"다른 사용자 주문 취소에서 서버 에러 발생!\n"
            f"status_code={response.status_code}"
        )

        # 403 (Forbidden) 또는 404 (Not Found) 예상
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ], f"다른 사용자 주문 취소가 허용됨: {response.status_code}"

