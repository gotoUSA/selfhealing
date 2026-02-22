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


# ==========================================
# 💳 결제 민감정보 노출 방지 테스트
# ==========================================


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPaymentSensitiveInfoExposure:
    """
    💳 결제 민감정보 노출 방지 테스트

    결제 실패 또는 에러 상황에서 카드 정보, CVV, 계좌번호 등
    민감한 결제 정보가 노출되지 않는지 검증합니다.

    📋 테스트 시나리오:
    - 결제 실패 시 카드 전체 번호 미노출
    - 에러 응답에 CVV/CVC 미포함
    - 계좌번호 마스킹 처리 확인

    📅 가이드라인: 02_ERROR_RESPONSE_SCHEMA.md (민감정보 보호)
    """

    # 결제 관련 민감 정보 패턴
    PAYMENT_SENSITIVE_PATTERNS = [
        # 카드번호 패턴 (4자리-4자리-4자리-4자리)
        r"\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}",
        # CVV/CVC (3-4자리)
        r"cvv|cvc|security.?code",
        # 계좌번호 (10자리 이상 연속 숫자)
        r"\d{10,}",
        # 카드 유효기간
        r"(0[1-9]|1[0-2])/?([0-9]{2}|[0-9]{4})",
    ]

    # 허용되는 마스킹 패턴
    ALLOWED_MASKED_PATTERNS = [
        r"\d{4}\*{4,}",  # 앞 4자리만 표시
        r"\*{4,}\d{4}",  # 뒤 4자리만 표시
        r"\d{4}\*+\d{4}",  # 앞뒤 4자리만 표시
    ]

    def test_payment_failure_no_card_number(self, client, auth_headers, schema_test_order):
        """
        결제 실패 시 카드 전체 번호 미노출 테스트

        결제가 실패했을 때 에러 응답에
        카드 번호 전체가 포함되지 않아야 합니다.

        🔍 검증 포인트:
        - 16자리 카드번호 미노출
        - 마스킹된 카드번호만 허용 (1234****5678)
        """
        import re

        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 잘못된 결제 정보로 결제 시도
        invalid_payment_data = {
            "order_id": schema_test_order.id,
            "amount": -1000,  # 잘못된 금액
            "payment_method": "card",
        }

        # Act
        response = client.post(
            "/api/payments/prepare/",
            data=json.dumps(invalid_payment_data),
            content_type="application/json",
            **headers,
        )

        # Assert - 응답 내용 확인
        content = response.content.decode("utf-8", errors="ignore")

        # 카드번호 전체 노출 확인 (16자리 연속 숫자)
        card_number_pattern = r"\d{15,16}"
        matches = re.findall(card_number_pattern, content)
        for match in matches:
            # 마스킹되지 않은 카드번호가 있으면 실패
            if "*" not in match:
                pytest.fail(f"카드 번호 노출!\n" f"match={match}\n" f"response={content[:500]}")

    def test_payment_error_no_cvv_exposure(self, client, auth_headers, schema_test_order):
        """
        결제 에러 시 CVV/CVC 미노출 테스트

        결제 관련 에러 발생 시
        CVV/CVC 값이 응답에 포함되지 않아야 합니다.

        🔍 검증 포인트:
        - CVV/CVC 값 미노출
        - 보안 코드 관련 필드명도 가급적 미노출
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 결제 관련 다양한 에러 유도
        test_cases = [
            {"order_id": 99999999, "amount": 10000},  # 존재하지 않는 주문
            {"order_id": schema_test_order.id, "amount": -1},  # 음수 금액
            {"order_id": "invalid", "amount": 10000},  # 잘못된 주문 ID
        ]

        for data in test_cases:
            # Act
            response = client.post(
                "/api/payments/prepare/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # Assert - 응답에 CVV 관련 정보 미포함
            content = response.content.decode("utf-8", errors="ignore").lower()

            dangerous_patterns = [
                "cvv",
                "cvc",
                "security_code",
                "securitycode",
                "card_verification",
            ]

            for pattern in dangerous_patterns:
                assert pattern not in content, (
                    f"CVV 관련 정보 노출!\n" f"pattern={pattern}\n" f"data={data}\n" f"response={content[:500]}"
                )

    def test_payment_response_masked_card_number(self, client, auth_headers, schema_test_payment):
        """
        결제 응답에서 카드번호 마스킹 확인

        결제 정보 조회 시 카드번호가 마스킹되어 있는지 확인합니다.

        🔍 검증 포인트:
        - 카드번호 마스킹 (예: 1234****5678)
        - 전체 번호 미노출
        """
        import re

        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 결제 상세 조회
        response = client.get(
            f"/api/payments/{schema_test_payment.id}/",
            **headers,
        )

        if response.status_code != 200:
            pytest.skip("결제 조회 실패로 테스트 스킵")

        # Assert - 응답에서 카드번호 확인
        content = response.content.decode("utf-8", errors="ignore")

        # JSON 파싱하여 card_number 필드 직접 확인
        data = response.json()
        card_number = data.get("card_number", "")

        if card_number:
            # 카드번호에 '*'가 포함되어 있어야 마스킹됨
            if "*" not in card_number:
                # 카드번호 형식인지 확인 (숫자와 구분자로만 이루어진 12자리 이상)
                digits = re.sub(r"\D", "", card_number)
                if len(digits) >= 12:
                    pytest.fail(f"마스킹되지 않은 카드번호 발견!\n" f"card_number={card_number}\n" f"response={content[:500]}")

        # 추가 검증: 전체 16자리 카드번호 패턴 (마스킹 없이)
        # 예: 1234-5678-9012-3456 또는 1234567890123456
        full_card_patterns = [
            r'"card_number"\s*:\s*"(\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})"',
            r'"card_number"\s*:\s*"(\d{16})"',
        ]

        for pattern in full_card_patterns:
            match = re.search(pattern, content)
            if match:
                pytest.fail(
                    f"마스킹되지 않은 전체 카드번호 발견!\n" f"card_number={match.group(1)}\n" f"response={content[:500]}"
                )

    def test_payment_error_no_secret_key_exposure(self, client, auth_headers):
        """
        결제 에러 시 API 키/시크릿 미노출 테스트

        결제 API 호출 중 에러 발생 시
        Toss API 키나 시크릿 키가 노출되지 않아야 합니다.

        🔍 검증 포인트:
        - API Key 미노출
        - Secret Key 미노출
        - 인증 토큰 미노출
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 다양한 에러 유발 요청
        error_data = {
            "order_id": 99999999,
            "amount": 10000,
        }

        # Act
        response = client.post(
            "/api/payments/prepare/",
            data=json.dumps(error_data),
            content_type="application/json",
            **headers,
        )

        # Assert
        content = response.content.decode("utf-8", errors="ignore").lower()

        # 민감한 키워드 확인
        secret_patterns = [
            "secret_key",
            "secretkey",
            "api_key",
            "apikey",
            "toss_secret",
            "client_secret",
            "private_key",
            "sk_live_",  # Toss 라이브 시크릿 키 패턴
            "sk_test_",  # Toss 테스트 시크릿 키 패턴
            "authorization: basic",  # 인코딩된 API 키
        ]

        for pattern in secret_patterns:
            assert pattern not in content, f"API 키/시크릿 노출!\n" f"pattern={pattern}\n" f"response={content[:500]}"

    def test_payment_webhook_no_sensitive_data_logging(self, client):
        """
        결제 웹훅에서 민감정보 로깅 미발생 확인

        결제 웹훅 처리 중 에러가 발생해도
        민감 정보가 로그에 기록되지 않아야 합니다.

        🔍 검증 포인트:
        - 웹훅 에러 응답에 민감정보 미포함
        - 5xx 에러 없음
        """
        # Arrange - 잘못된 웹훅 페이로드
        invalid_webhooks = [
            {},  # 빈 페이로드
            {"event": "invalid"},  # 잘못된 이벤트
            {"paymentKey": "test", "orderId": "invalid"},  # 잘못된 주문
        ]

        for payload in invalid_webhooks:
            # Act
            response = client.post(
                "/api/payments/webhook/",
                data=json.dumps(payload),
                content_type="application/json",
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, (
                f"웹훅에서 서버 에러 발생!\n" f"payload={payload}\n" f"status_code={response.status_code}"
            )

            # Assert - 민감 정보 미노출
            content = response.content.decode("utf-8", errors="ignore").lower()
            sensitive_in_response = any(p in content for p in ["secret", "password", "key=", "token="])
            assert not sensitive_in_response, f"웹훅 응답에 민감정보 포함!\n" f"response={content[:500]}"

    def test_refund_error_no_account_exposure(self, client, auth_headers, schema_test_payment):
        """
        환불 에러 시 계좌정보 미노출 테스트

        환불 처리 중 에러 발생 시
        고객 계좌 정보가 노출되지 않아야 합니다.

        🔍 검증 포인트:
        - 계좌번호 미노출 또는 마스킹
        - 은행 내부 코드 미노출
        """
        import re

        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 환불 시도 (잘못된 요청)
        response = client.post(
            f"/api/payments/{schema_test_payment.id}/refund/",
            data=json.dumps({"reason": "test"}),
            content_type="application/json",
            **headers,
        )

        # Assert
        content = response.content.decode("utf-8", errors="ignore")

        # 10자리 이상 연속 숫자 (계좌번호 가능성)
        account_pattern = r"\d{10,}"
        matches = re.findall(account_pattern, content)

        for match in matches:
            # ID 같은 짧은 숫자는 허용, 계좌번호로 의심되는 긴 숫자는 확인
            if len(match) >= 12:
                pytest.fail(f"계좌번호로 의심되는 긴 숫자 노출!\n" f"match={match}\n" f"response={content[:500]}")
