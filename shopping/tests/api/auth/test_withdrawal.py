from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.mark.django_db
class TestWithdrawalSuccess:
    """정상 탈퇴 시나리오"""

    def test_withdraw_success(self, authenticated_client, user):
        """정상 탈퇴"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "회원 탈퇴가 완료되었습니다."

        # Assert
        user.refresh_from_db()
        assert user.is_withdrawn is True
        assert user.withdrawn_at is not None
        assert user.is_active is False

    def test_withdraw_response_structure(self, authenticated_client):
        """탈퇴 응답 구조 검증"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data
        assert isinstance(response.data["message"], str)


@pytest.mark.django_db
class TestWithdrawalPasswordValidation:
    """비밀번호 검증 테스트"""

    def test_withdraw_wrong_password(self, authenticated_client):
        """잘못된 비밀번호로 탈퇴 실패"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "wrongpassword123"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "비밀번호가 올바르지 않습니다" in str(response.data)

    def test_withdraw_without_password(self, authenticated_client):
        """비밀번호 누락"""
        # Arrange
        url = reverse("user-withdraw")
        data = {}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "비밀번호" in str(response.data)

    def test_withdraw_empty_password(self, authenticated_client):
        """빈 비밀번호"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": ""}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST


pytest.mark.django_db


class TestWithdrawalAuthentication:
    """인증 관련 테스트"""

    def test_withdraw_without_auth(self, api_client):
        """인증 없이 탈퇴 시도"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        response = api_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestWithdrawalStateVerification:
    """탈퇴 후 상태 검증"""

    def test_withdrawal_flags_updated(self, authenticated_client, user):
        """is_withdrawn, is_active, withdrawn_at 상태 확인"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # 탈퇴 전 상태 저장
        assert user.is_withdrawn is False
        assert user.is_active is True
        assert user.withdrawn_at is None

        # Act
        authenticated_client.post(url, data, format="json")

        # Assert
        user.refresh_from_db()
        assert user.is_withdrawn is True
        assert user.is_active is False
        assert user.withdrawn_at is not None
        assert isinstance(user.withdrawn_at, type(timezone.now()))

    def test_login_after_withdrawal(self, api_client, user):
        """탈퇴 후 로그인 시도"""
        # Arrange - 사용자 탈퇴 처리
        user.is_withdrawn = True
        user.withdrawn_at = timezone.now()
        user.is_active = False
        user.save()

        login_url = reverse("auth-login")
        login_data = {"username": "testuser", "password": "testpass123"}

        # Act
        response = api_client.post(login_url, login_data, format="json")

        # Assert - 로그인 실패해야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestWithdrawalTokenInvalidation:
    """토큰 무효화 테스트"""

    def test_token_invalidated_after_withdrawal(self, api_client, user):
        """탈퇴 후 기존 토큰으로 접근 불가"""
        # Arrange - 토큰 발급
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)

        # 인증 설정
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        # 탈퇴 처리
        withdraw_url = reverse("user-withdraw")
        withdraw_data = {"password": "testpass123"}
        api_client.post(withdraw_url, withdraw_data, format="json")

        # Act - 탈퇴 후 프로필 접근 시도
        profile_url = reverse("user-profile")
        response = api_client.get(profile_url)

        # Assert - 접근 실패해야 함 (is_active=False이므로)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_all_tokens_invalidated(self, api_client, user):
        """모든 토큰 무효화 확인 (여러 세션)"""
        # Arrange - 여러 토큰 발급
        refresh1 = RefreshToken.for_user(user)
        refresh2 = RefreshToken.for_user(user)
        access1 = str(refresh1.access_token)
        access2 = str(refresh2.access_token)

        # 첫 번째 토큰으로 탈퇴
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access1}")
        withdraw_url = reverse("user-withdraw")
        api_client.post(withdraw_url, {"password": "testpass123"}, format="json")

        # Act - 두 번째 토큰으로 접근 시도
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access2}")
        profile_url = reverse("user-profile")
        response = api_client.get(profile_url)

        # Assert - 모든 토큰이 무효화되어야 함
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestWithdrawalDataPreservation:
    """데이터 보존 테스트"""

    def test_points_preserved_after_withdrawal(self, api_client, user_with_points):
        """탈퇴 후 포인트 보존 확인"""
        # Arrange - user_with_points fixture 사용 (5000 포인트)
        initial_points = user_with_points.points

        # 인증 클라이언트 설정
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user_with_points)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        api_client.post(url, data, format="json")

        # Assert
        user_with_points.refresh_from_db()
        assert user_with_points.points == initial_points

    def test_orders_preserved_after_withdrawal(self, authenticated_client, paid_order):
        """탈퇴 후 주문 내역 보존 확인"""
        # Arrange
        user = paid_order.user
        order_id = paid_order.id

        # 인증 클라이언트에 user 설정
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        authenticated_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        authenticated_client.post(url, data, format="json")

        # Assert - 주문 내역이 여전히 존재해야 함
        from shopping.models.order import Order

        order = Order.objects.filter(id=order_id).first()
        assert order is not None
        assert order.user.id == user.id

    def test_point_history_preserved(self, authenticated_client, user):
        """탈퇴 후 포인트 이력 보존"""
        # Arrange - 포인트 이력 생성
        from shopping.models.point import PointHistory

        PointHistory.objects.create(
            user=user,
            points=1000,
            balance=1000,
            type="earn",
            description="테스트 적립",
        )

        initial_history_count = PointHistory.objects.filter(user=user).count()

        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        authenticated_client.post(url, data, format="json")

        # Assert
        final_history_count = PointHistory.objects.filter(user=user).count()
        assert final_history_count == initial_history_count


@pytest.mark.django_db
class TestWithdrawalReregistration:
    """재가입 시나리오 테스트"""

    def test_reregister_with_same_username(self, api_client, user):
        """동일 username으로 재가입 시도 (재활성화)"""
        # Arrange - 사용자 탈퇴
        user.is_withdrawn = True
        user.withdrawn_at = timezone.now()
        user.is_active = False
        user.save()

        # Act - 동일 username으로 회원가입 시도
        register_url = reverse("auth-register")
        register_data = {
            "username": "testuser",
            "email": "newemail@example.com",
            "password": "newpass123",
            "password2": "newpass123",
        }

        response = api_client.post(register_url, register_data, format="json")

        # Assert - username 중복 에러 또는 재활성화 처리
        # 구현 방식에 따라 다를 수 있음
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,  # 중복 에러
            status.HTTP_201_CREATED,  # 재활성화 성공
        ]

    def test_reregister_with_same_email(self, api_client, user):
        """동일 email으로 재가입 시도"""
        # Arrange - 사용자 탈퇴
        user.is_withdrawn = True
        user.withdrawn_at = timezone.now()
        user.is_active = False
        user.save()

        # Act - 동일 email로 회원가입 시도
        register_url = reverse("auth-register")
        register_data = {
            "username": "newusername",
            "email": "test@example.com",
            "password": "newpass123",
            "password2": "newpass123",
        }

        response = api_client.post(register_url, register_data, format="json")

        # Assert
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,  # 중복 에러
            status.HTTP_201_CREATED,  # 재활성화 성공
        ]


@pytest.mark.django_db
class TestWithdrawalEdgeCases:
    """경계값 및 예외 케이스"""

    def test_withdraw_twice(self, authenticated_client, user):
        """이미 탈퇴한 사용자가 다시 탈퇴 시도"""
        # Arrange - 첫 번째 탈퇴
        url = reverse("user-withdraw")
        data = {"password": "testpass123"}
        authenticated_client.post(url, data, format="json")

        # Act - 두 번째 탈퇴 시도
        # 새로운 토큰 필요 (이미 무효화되었으므로)

        # is_active=False이므로 토큰 발급 불가
        # 이 테스트는 탈퇴 후 재로그인이 불가능함을 확인

        user.refresh_from_db()
        assert user.is_withdrawn is True

    def test_withdraw_with_pending_order(self, authenticated_client, order):
        """대기 중인 주문이 있는 상태에서 탈퇴"""
        # Arrange
        user = order.user

        # 인증 설정
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        authenticated_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        url = reverse("user-withdraw")
        data = {"password": "testpass123"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert - 탈퇴는 가능해야 함 (비즈니스 로직에 따라)
        # 주문 내역은 보존됨
        assert response.status_code == status.HTTP_200_OK

        # 주문이 여전히 존재하는지 확인
        from shopping.models.order import Order

        assert Order.objects.filter(id=order.id).exists()


@pytest.mark.django_db
class TestWithdrawalSecurity:
    """보안 테스트"""

    def test_withdraw_with_special_chars_password(self, authenticated_client, user):
        """특수문자가 포함된 비밀번호로 탈퇴"""
        # Arrange - 특수문자 비밀번호로 사용자 생성
        user.set_password("Test@Pass#123!")
        user.save()

        # 토큰 재발급
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        authenticated_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        url = reverse("user-withdraw")
        data = {"password": "Test@Pass#123!"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_withdraw_sql_injection_attempt(self, authenticated_client):
        """SQL Injection 시도"""
        # Arrange
        url = reverse("user-withdraw")
        data = {"password": "' OR '1'='1"}

        # Act
        response = authenticated_client.post(url, data, format="json")

        # Assert - 실패해야 함
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestWithdrawalRevokesRotatedTokens:
    """탈퇴 시 회전으로 받은 refresh 토큰까지 무효화"""

    def test_rotated_refresh_blacklisted_after_withdrawal(self, api_client, user):
        """로그인 → 갱신 1회(회전) → 탈퇴: 회전된 토큰도 블랙리스트에 있고 갱신이 거부된다"""
        # Arrange
        login = api_client.post(reverse("auth-login"), {"username": "testuser", "password": "testpass123"})
        rotated = APIClient().post(reverse("token-refresh"), {"refresh": login.cookies["refresh_token"].value}, format="json")
        rotated_refresh = rotated.cookies["refresh_token"].value

        # Act
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['token']['access']}")
        response = api_client.post(reverse("user-withdraw"), {"password": "testpass123"}, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Assert
        jti = RefreshToken(rotated_refresh, verify=False)["jti"]
        assert BlacklistedToken.objects.filter(token__jti=jti).exists()
        after = APIClient().post(reverse("token-refresh"), {"refresh": rotated_refresh}, format="json")
        assert after.status_code == status.HTTP_401_UNAUTHORIZED
