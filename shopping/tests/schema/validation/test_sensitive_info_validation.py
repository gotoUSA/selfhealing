# test_sensitive_info_validation.py
# 민감 정보 노출 방지 검증 테스트

"""
🔒 민감 정보 노출 방지 검증 테스트
=================================

가이드라인 02_ERROR_RESPONSE_SCHEMA.md에 따라
에러 응답에서 민감한 정보가 노출되지 않는지 검증합니다.

✅ 검증 항목:
- 스택 트레이스 노출 방지
- 내부 파일 경로 노출 방지
- 데이터베이스 정보 노출 방지
- 서버 설정 정보 노출 방지
- 디버그 정보 노출 방지

📁 관련 가이드:
- guides/02_ERROR_RESPONSE_SCHEMA.md
- guides/08_NEGATIVE_TESTING.md
"""

import json
import re

import pytest
from rest_framework import status

from ..conftest import assert_error_response


# ==========================================
# 🔒 민감 정보 패턴 정의
# ==========================================

# 스택 트레이스 패턴
STACK_TRACE_PATTERNS = [
    r"Traceback \(most recent call last\)",
    r"File \".*\.py\", line \d+",
    r"at .*:\d+:\d+",  # JavaScript 스타일
    r"Exception in thread",
    r"\.py:\d+ in \w+",
]

# 내부 경로 패턴
INTERNAL_PATH_PATTERNS = [
    r"/home/\w+/",
    r"/app/",
    r"/usr/local/",
    r"/var/www/",
    r"C:\\Users\\",
    r"\\site-packages\\",
    r"/venv/",
    r"/env/",
]

# 데이터베이스 정보 패턴
DATABASE_INFO_PATTERNS = [
    r"postgresql://",
    r"mysql://",
    r"sqlite://",
    r"SQLSTATE",
    r"syntax error at or near",
    r"relation \".*\" does not exist",
    r"column \".*\" does not exist",
]

# 서버 설정 패턴
SERVER_CONFIG_PATTERNS = [
    r"SECRET_KEY",
    r"DATABASE_URL",
    r"AWS_ACCESS_KEY",
    r"API_KEY",
    r"DEBUG\s*=\s*True",
]

# 디버그 정보 패턴
DEBUG_INFO_PATTERNS = [
    r"Django Version:",
    r"Python Executable:",
    r"Python Version:",
    r"Python Path:",
    r"Server time:",
    r"Installed Applications:",
    r"Installed Middleware:",
]


def check_sensitive_patterns(text: str, patterns: list[str], category: str) -> list[str]:
    """
    텍스트에서 민감한 패턴을 검색

    Args:
        text: 검사할 텍스트
        patterns: 정규식 패턴 목록
        category: 패턴 카테고리 (에러 메시지용)

    Returns:
        발견된 패턴 목록
    """
    found = []
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(f"{category}: {pattern}")
    return found


def assert_no_sensitive_info(response_data: dict, context: str = "") -> None:
    """
    응답 데이터에 민감한 정보가 없는지 검증

    Args:
        response_data: 응답 JSON 데이터
        context: 에러 메시지에 포함할 컨텍스트

    Raises:
        AssertionError: 민감 정보 발견 시
    """
    prefix = f"[{context}] " if context else ""
    text = json.dumps(response_data, ensure_ascii=False)

    found_patterns = []

    # 각 카테고리별 패턴 검사
    found_patterns.extend(check_sensitive_patterns(text, STACK_TRACE_PATTERNS, "스택 트레이스"))
    found_patterns.extend(check_sensitive_patterns(text, INTERNAL_PATH_PATTERNS, "내부 경로"))
    found_patterns.extend(check_sensitive_patterns(text, DATABASE_INFO_PATTERNS, "데이터베이스"))
    found_patterns.extend(check_sensitive_patterns(text, SERVER_CONFIG_PATTERNS, "서버 설정"))
    found_patterns.extend(check_sensitive_patterns(text, DEBUG_INFO_PATTERNS, "디버그 정보"))

    assert not found_patterns, f"{prefix}민감 정보 노출 발견: {found_patterns}"


@pytest.mark.schema
@pytest.mark.django_db
class TestErrorResponseNoSensitiveInfo:
    """
    ❌ 에러 응답 민감 정보 노출 방지 테스트

    API 에러 응답에서 민감한 정보가 노출되지 않는지 검증합니다.

    ✅ 검증 항목:
    - 스택 트레이스 없음
    - 내부 파일 경로 없음
    - 데이터베이스 정보 없음
    """

    def test_404_error_no_stack_trace(self, client):
        """
        ❌ 404 에러 응답에 스택 트레이스 없음
        """
        # Arrange
        invalid_id = 99999999

        # Act
        response = client.get(f"/api/products/{invalid_id}/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()

        # 민감 정보 검증
        assert_no_sensitive_info(data, context=f"/api/products/{invalid_id}/ (404)")

    def test_400_error_no_internal_paths(self, client, auth_headers):
        """
        ❌ 400 에러 응답에 내부 경로 없음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_data = {"product_id": "invalid", "quantity": -1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(invalid_data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND]
        data = response.json()

        # 민감 정보 검증
        assert_no_sensitive_info(data, context="/api/cart/add_item/ (400)")

    def test_401_error_no_sensitive_info(self, client):
        """
        ❌ 401 에러 응답에 민감 정보 없음
        """
        # Arrange - 인증 없이 보호된 엔드포인트 접근

        # Act
        response = client.get("/api/orders/")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        data = response.json()

        # 민감 정보 검증
        assert_no_sensitive_info(data, context="/api/orders/ (401)")

    def test_invalid_json_error_no_debug_info(self, client, auth_headers):
        """
        ❌ 잘못된 JSON 요청 에러에 디버그 정보 없음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_json = "{'invalid': json}"  # 잘못된 JSON

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=invalid_json,
            content_type="application/json",
            **headers,
        )

        # Assert - 400 또는 500이 아닌 적절한 에러
        assert response.status_code < 500, f"서버 에러 발생: {response.status_code}"

        try:
            data = response.json()
            assert_no_sensitive_info(data, context="/api/cart/add_item/ (invalid JSON)")
        except json.JSONDecodeError:
            # JSON 파싱 실패는 허용 (HTML 에러 페이지 등)
            pass


@pytest.mark.schema
@pytest.mark.django_db
class TestMaliciousInputNoInfoLeak:
    """
    🔐 악의적 입력 시 정보 누출 방지 테스트

    SQL Injection, Path Traversal 등의 악의적 입력에 대해
    에러 응답이 내부 정보를 노출하지 않는지 검증합니다.
    """

    @pytest.mark.parametrize(
        "malicious_input,description",
        [
            ("'; DROP TABLE users; --", "SQL Injection"),
            ("1 OR 1=1", "SQL Injection (Boolean)"),
            ("1 UNION SELECT * FROM users", "SQL Injection (UNION)"),
            ("../../../etc/passwd", "Path Traversal"),
            ("..\\..\\..\\windows\\system32", "Path Traversal (Windows)"),
            ("<script>alert(1)</script>", "XSS"),
            ("{{7*7}}", "SSTI"),
            ("${7*7}", "SSTI (Expression)"),
        ],
        ids=["sql_drop", "sql_boolean", "sql_union", "path_unix", "path_win", "xss", "ssti", "ssti_expr"],
    )
    def test_malicious_search_no_info_leak(self, client, malicious_input, description):
        """
        🔐 악의적 검색어에 정보 누출 없음
        """
        # Arrange - malicious_input is provided by parametrize

        # Act
        response = client.get(f"/api/products/?search={malicious_input}")

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}: 서버 에러 {response.status_code}"

        # 200 또는 400일 때 응답 검증
        if response.status_code in [200, 400]:
            try:
                data = response.json()
                assert_no_sensitive_info(data, context=f"search={description}")
            except (json.JSONDecodeError, ValueError):
                # HTML 응답 시 JSON 파싱 실패 - 허용
                pass

    @pytest.mark.parametrize(
        "malicious_id,description",
        [
            ("1; DROP TABLE--", "SQL Injection"),
            ("1 OR 1=1", "SQL Boolean"),
            ("../../../etc/passwd", "Path Traversal"),
            ("-1", "음수 ID"),
            ("0", "0 ID"),
            ("null", "null 문자열"),
            ("undefined", "undefined 문자열"),
            ("NaN", "NaN 문자열"),
        ],
        ids=["sql", "sql_bool", "path", "negative", "zero", "null", "undefined", "nan"],
    )
    def test_malicious_path_param_no_info_leak(self, client, malicious_id, description):
        """
        🔐 악의적 Path Parameter에 정보 누출 없음
        """
        # Arrange - malicious_id is provided by parametrize

        # Act
        response = client.get(f"/api/products/{malicious_id}/")

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}: 서버 에러 {response.status_code}"

        # 에러 응답 검증
        if response.status_code in [400, 404]:
            try:
                data = response.json()
                assert_no_sensitive_info(data, context=f"id={description}")
            except (json.JSONDecodeError, ValueError):
                # HTML 에러 페이지 반환 시 JSON 파싱 실패 - 허용
                pass


@pytest.mark.schema
@pytest.mark.django_db
class TestAuthErrorNoCredentialLeak:
    """
    🔑 인증 에러 시 자격증명 정보 누출 방지 테스트

    로그인 실패 등의 인증 에러에서 비밀번호나 토큰 정보가
    노출되지 않는지 검증합니다.
    """

    def test_login_failure_no_password_echo(self, client):
        """
        🔑 로그인 실패 시 비밀번호가 에코되지 않음
        """
        # Arrange
        login_data = {
            "username": "testuser",
            "password": "wrong_password_123!@#",
        }

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(login_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [400, 401]
        data = response.json()
        response_text = json.dumps(data, ensure_ascii=False)

        # 비밀번호가 응답에 포함되지 않음
        assert login_data["password"] not in response_text, "비밀번호가 에러 응답에 노출됨"

    def test_invalid_token_no_token_echo(self, client):
        """
        🔑 잘못된 토큰 시 토큰이 에코되지 않음
        """
        # Arrange
        fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmYWtlIjoidG9rZW4ifQ.fake_signature"
        headers = {"HTTP_AUTHORIZATION": f"Bearer {fake_token}"}

        # Act
        response = client.get("/api/orders/", **headers)

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

        try:
            data = response.json()
            response_text = json.dumps(data, ensure_ascii=False)

            # 토큰이 응답에 포함되지 않음
            assert fake_token not in response_text, "토큰이 에러 응답에 노출됨"
        except json.JSONDecodeError:
            pass

    def test_register_failure_no_password_echo(self, client):
        """
        🔑 회원가입 실패 시 비밀번호가 에코되지 않음
        """
        # Arrange
        register_data = {
            "username": "newuser",
            "email": "invalid_email",  # 유효하지 않은 이메일
            "password": "secret_password_123!@#",
            "password_confirm": "secret_password_123!@#",
        }

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(register_data),
            content_type="application/json",
        )

        # Assert
        if response.status_code == 400:
            data = response.json()
            response_text = json.dumps(data, ensure_ascii=False)

            # 비밀번호가 응답에 포함되지 않음
            assert register_data["password"] not in response_text, "비밀번호가 에러 응답에 노출됨"


@pytest.mark.schema
@pytest.mark.django_db
class TestErrorMessageQuality:
    """
    📝 에러 메시지 품질 검증 테스트

    에러 메시지가 사용자 친화적이고 일관성 있는지 검증합니다.

    ✅ 검증 항목:
    - 빈 에러 응답 없음
    - 에러 메시지 존재
    - 일관된 에러 구조
    """

    def test_error_response_not_empty(self, client):
        """
        📝 에러 응답이 비어있지 않음
        """
        # Arrange
        invalid_id = 99999999

        # Act
        response = client.get(f"/api/products/{invalid_id}/")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()

        # 빈 응답 아님
        assert data, "에러 응답이 비어있음"

    def test_error_response_has_message(self, client, auth_headers):
        """
        📝 에러 응답에 메시지 존재

        에러 응답이 detail, message, 또는 필드별 에러를 포함하는지 확인
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        invalid_data = {}  # 빈 데이터

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(invalid_data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()

        # 에러 정보 존재 확인
        assert_error_response(data, context="/api/cart/add_item/ (empty data)")

    def test_error_responses_consistent_structure(self, client, auth_headers):
        """
        📝 여러 에러 응답이 일관된 구조를 따름
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        error_scenarios = [
            # (endpoint, method, data, expected_status)
            ("/api/products/99999999/", "GET", None, 404),
            ("/api/orders/", "GET", None, 401),  # 인증 없이
        ]

        for endpoint, method, data, expected_status in error_scenarios:
            # Act
            if method == "GET":
                if expected_status == 401:
                    response = client.get(endpoint)
                else:
                    response = client.get(endpoint, **headers)
            else:
                response = client.post(
                    endpoint,
                    data=json.dumps(data) if data else "{}",
                    content_type="application/json",
                    **headers,
                )

            # Assert - 에러 구조 일관성
            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    assert isinstance(error_data, dict), f"{endpoint}: 에러 응답이 dict가 아님"
                except json.JSONDecodeError:
                    pytest.fail(f"{endpoint}: JSON 파싱 실패")
