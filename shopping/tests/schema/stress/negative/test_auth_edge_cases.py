"""
인증/권한 Edge Case 테스트
=========================

TestAuthorizationEdgeCases: 만료된 토큰, 잘못된 토큰 형식, 다른 사용자 리소스 접근
"""

import json

import pytest
from rest_framework import status


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
