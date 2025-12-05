"""
민감 정보 노출 테스트
=====================

에러 응답에 민감 정보(스택 트레이스, DB 정보 등)가
노출되지 않는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_sensitive_info.py -v --no-cov
"""

import json

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestSensitiveInfoExposure:
    """
    🔐 민감 정보 노출 테스트

    에러 응답에 민감한 정보(스택 트레이스, 내부 경로, 설정값 등)가
    노출되지 않는지 검증합니다.

    📋 테스트 시나리오:
    - 에러 응답에 스택 트레이스 미포함
    - 내부 파일 경로 미노출
    - 데이터베이스 정보 미노출
    - 설정 정보 미노출
    """

    # 민감 정보 패턴 목록
    SENSITIVE_PATTERNS = [
        "Traceback",
        'File "',
        "/usr/local/lib/python",
        "/home/",
        "/app/",
        "SECRET_KEY",
        "DATABASE_URL",
        "POSTGRES",
        "password",
        "psycopg",
        "django.db",
        "site-packages",
        '.py", line',
        "Exception:",
        "Error:",
    ]

    def test_error_response_no_stack_trace(self, client):
        """
        에러 응답에 스택 트레이스 미포함 테스트

        잘못된 요청으로 에러 발생 시
        스택 트레이스가 응답에 포함되지 않아야 합니다.

        🔍 검증 포인트:
        - Traceback 문자열 미포함
        - File 경로 미포함
        - Python 내부 경로 미포함
        """
        # Arrange - 잘못된 ID로 요청
        invalid_ids = [
            "abc",
            "-1",
            "0",
            "'; DROP TABLE--",
            "{{7*7}}",
        ]

        for invalid_id in invalid_ids:
            # Act
            response = client.get(f"/api/products/{invalid_id}/")

            # Assert - 응답 내용 확인
            content = response.content.decode("utf-8", errors="ignore")

            for pattern in self.SENSITIVE_PATTERNS:
                assert pattern.lower() not in content.lower(), (
                    f"민감 정보 노출!\n" f"pattern={pattern}\n" f"invalid_id={invalid_id}\n" f"response={content[:500]}"
                )

    def test_auth_error_no_user_enumeration(self, client):
        """
        인증 에러에서 사용자 존재 여부 미노출 테스트

        잘못된 로그인 시도 시 사용자 존재 여부를
        추측할 수 없어야 합니다.

        🔍 검증 포인트:
        - "사용자가 존재하지 않습니다" 등 노출 안 함
        - 일관된 에러 메시지 반환
        """
        # Arrange
        test_cases = [
            {"username": "nonexistent_user_12345", "password": "wrong"},
            {"username": "admin", "password": "wrong_password"},
            {"username": "test@test.com", "password": "12345"},
        ]

        responses = []

        for data in test_cases:
            # Act
            response = client.post(
                "/api/auth/login/",
                data=json.dumps(data),
                content_type="application/json",
            )
            responses.append(response)

        # Assert - 모든 응답이 동일한 패턴을 가져야 함
        # (사용자 존재 여부 추측 불가)
        status_codes = [r.status_code for r in responses]

        # 모든 잘못된 로그인은 동일한 상태 코드를 반환해야 함
        assert len(set(status_codes)) == 1, (
            f"로그인 실패 응답이 일관되지 않음 (사용자 열거 가능)\n" f"status_codes={status_codes}"
        )

        # "user not found", "user does not exist" 등 노출 안 함
        for response in responses:
            content = response.content.decode("utf-8", errors="ignore").lower()
            dangerous_phrases = [
                "user not found",
                "user does not exist",
                "no such user",
                "사용자가 존재하지",
                "사용자를 찾을 수 없",
            ]
            for phrase in dangerous_phrases:
                assert phrase not in content, f"사용자 존재 여부 노출!\n" f"phrase={phrase}\n" f"response={content[:300]}"

    def test_database_error_no_details(self, client, auth_headers):
        """
        데이터베이스 에러 시 상세 정보 미노출 테스트

        데이터베이스 관련 에러 발생 시
        쿼리, 테이블명 등 내부 정보가 노출되지 않아야 합니다.

        🔍 검증 포인트:
        - SQL 쿼리 미노출
        - 테이블명/컬럼명 미노출
        - psycopg 에러 미노출
        """
        # Arrange - SQL Injection 시도로 데이터베이스 에러 유도
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        sql_payloads = [
            "1; DROP TABLE products;--",
            "1' OR '1'='1' --",
            "1 UNION SELECT * FROM auth_user--",
        ]

        for payload in sql_payloads:
            # Act - 장바구니에 잘못된 상품 ID 추가 시도
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps({"product_id": payload, "quantity": 1}),
                content_type="application/json",
                **headers,
            )

            # Assert - 응답 내용 확인
            content = response.content.decode("utf-8", errors="ignore")

            # 데이터베이스 관련 정보 미노출
            db_patterns = [
                "psycopg",
                "PostgreSQL",
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "FROM",
                "WHERE",
                "auth_user",
                "django_",
                "shopping_",
            ]

            for pattern in db_patterns:
                # SQL 키워드가 에러 메시지에 직접 노출되면 안 됨
                # 단, 입력값 에코는 허용 (예: "SELECT는 유효한 상품 ID가 아닙니다")
                if pattern in content and pattern not in payload:
                    # 에러 메시지에 SQL 관련 내부 정보가 있는지 더 정밀하게 확인
                    if "error" in content.lower() or "exception" in content.lower():
                        pytest.fail(
                            f"데이터베이스 정보 노출!\n"
                            f"pattern={pattern}\n"
                            f"payload={payload}\n"
                            f"response={content[:500]}"
                        )
