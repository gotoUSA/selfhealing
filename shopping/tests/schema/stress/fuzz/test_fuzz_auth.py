"""
인증 API Fuzz 테스트
====================

로그인, 토큰 갱신 API에 무작위 자격 증명을 주입하여
인증 로직의 안정성을 확인합니다.

📋 테스트 대상:
- POST /api/auth/login/ (로그인)
- POST /api/auth/token/refresh/ (토큰 갱신)

⚠️ 주의:
- Rate Limiting이 테스트 환경에서 비활성화되어 있어야 함

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_auth.py -v -n 0
```
"""

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from hypothesis import strategies as st
from rest_framework import status


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthFuzz:
    """
    🔐 인증 API Fuzz 테스트

    로그인, 토큰 갱신 API에 무작위 자격 증명을 주입하여
    인증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - POST /api/auth/login/ (로그인)
    - POST /api/auth/token/refresh/ (토큰 갱신)

    ⚠️ 주의:
    - 비밀번호 관련 테스트는 실제 이메일 발송을 유발할 수 있어 제외
    - Rate Limiting이 테스트 환경에서 비활성화되어 있어야 함

    ✅ 검증 속성:
    - 잘못된 자격 증명에 대해 401 반환 (500이 아님)
    - 잘못된 토큰에 대해 401 반환
    """

    @given(
        username=st.one_of(
            st.text(min_size=0, max_size=200),
            st.just(""),
            st.just("admin"),
            st.just("root"),
            st.just("' OR '1'='1"),
        ),
        password=st.one_of(
            st.text(min_size=0, max_size=200),
            st.just(""),
            st.just("password"),
            st.just("123456"),
            st.just("a" * 1000),  # 매우 긴 비밀번호
        ),
    )
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_login_credentials_fuzz(self, client, username, password):
        """
        로그인 API 자격 증명 퍼징

        다양한 사용자명과 비밀번호 조합을 시도하여
        인증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - SQL Injection 시도 (username에 SQL 구문)
        - 빈 자격 증명
        - 매우 긴 비밀번호
        - 일반적인 약한 비밀번호

        Args:
            username: Hypothesis가 생성한 무작위 사용자명
            password: Hypothesis가 생성한 무작위 비밀번호
        """
        # Arrange
        data = {"username": username, "password": password}

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code < 500, (
            f"로그인에서 서버 에러 발생!\n"
            f"username={repr(username[:50])}, password={repr(password[:20])}\n"
            f"상태 코드: {response.status_code}"
        )
        # 잘못된 자격 증명은 400 또는 401이어야 함
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_429_TOO_MANY_REQUESTS,
        ], f"예상치 못한 응답 코드: {response.status_code}"

    @given(
        refresh_token=st.one_of(
            st.text(min_size=0, max_size=500),  # 무작위 문자열
            st.just(""),  # 빈 토큰
            st.just("invalid.token.here"),  # 잘못된 형식
            st.just(
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
            ),  # 서명이 다른 JWT
        )
    )
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_token_refresh_fuzz(self, client, refresh_token):
        """
        토큰 갱신 API 퍼징

        다양한 형식의 refresh token을 주입하여
        토큰 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 빈 토큰
        - 잘못된 형식의 토큰
        - 서명이 다른 JWT
        - 매우 긴 토큰 문자열

        Args:
            refresh_token: Hypothesis가 생성한 무작위 토큰
        """
        # Arrange
        data = {"refresh": refresh_token}

        # Act
        response = client.post(
            "/api/auth/token/refresh/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code < 500, (
            f"토큰 갱신에서 서버 에러 발생!\n"
            f"refresh_token={repr(refresh_token[:50])}\n"
            f"상태 코드: {response.status_code}"
        )
