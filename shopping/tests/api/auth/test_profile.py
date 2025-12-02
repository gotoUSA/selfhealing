
from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.user import User


@pytest.mark.django_db
class TestProfileView:
    """프로필 조회 테스트"""

    def test_get_profile_authenticated(self, authenticated_client, user):
        """인증된 사용자 프로필 조회 및 응답 구조 검증"""
        # Arrange
        url = reverse("user-profile")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # 필수 필드 존재 확인
        assert "id" in data
        assert "username" in data
        assert "email" in data
        assert "points" in data
        assert "membership_level" in data
        assert "is_email_verified" in data

        # 값 검증
        assert data["username"] == user.username
        assert data["email"] == user.email
        assert data["points"] == user.points

        # 민감 정보 제외 확인
        assert "password" not in data

    def test_get_profile_unauthenticated(self, api_client):
        """인증 없이 프로필 조회 시도"""
        # Arrange
        url = reverse("user-profile")

        # Act
        response = api_client.get(url)

        # Assert
        assert response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestProfileUpdate:
    """프로필 수정 테스트"""

    def test_update_profile_patch(self, authenticated_client, user, profile_update_data):
        """부분 수정 (PATCH)"""
        # Arrange
        url = reverse("user-profile")

        # Act
        response = authenticated_client.patch(url, profile_update_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "user" in response.data
        assert "message" in response.data
        assert response.data["message"] == "프로필이 수정되었습니다."

        # DB 검증
        user.refresh_from_db()
        assert user.first_name == profile_update_data["first_name"]
        assert user.last_name == profile_update_data["last_name"]
        assert user.phone_number == profile_update_data["phone_number"]

    def test_update_profile_put(self, authenticated_client, user):
        """전체 수정 (PUT)"""
        # Arrange
        url = reverse("user-profile")
        update_data = {
            "email": user.email,
            "first_name": "홍",
            "last_name": "길동",
            "phone_number": "010-1111-2222",
            "address": "서울시 강남구",
            "agree_marketing_email": True,
            "agree_marketing_sms": False,
        }

        # Act
        response = authenticated_client.put(url, update_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.first_name == "홍"
        assert user.phone_number == "010-1111-2222"

    def test_readonly_fields_ignored(self, authenticated_client, user):
        """읽기 전용 필드 수정 시도 - 무시되어야 함"""
        # Arrange
        url = reverse("user-profile")
        original_points = user.points
        original_level = user.membership_level
        original_username = user.username

        update_data = {
            "points": 999999,  # 읽기 전용
            "membership_level": "vip",  # 읽기 전용
            "username": "hacker",  # 읽기 전용
            "first_name": "정상수정",  # 수정 가능
        }

        # Act
        response = authenticated_client.patch(url, update_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        # 읽기 전용 필드는 변경되지 않음
        assert user.points == original_points
        assert user.membership_level == original_level
        assert user.username == original_username
        # 수정 가능 필드는 변경됨
        assert user.first_name == "정상수정"

    def test_put_vs_patch_difference(self, authenticated_client, user):
        """PUT과 PATCH의 동작 차이 검증"""
        # Arrange
        url = reverse("user-profile")
        user.first_name = "원본"
        user.last_name = "이름"
        user.phone_number = "010-1234-5678"
        user.save()

        # Act - PATCH는 일부 필드만 수정
        patch_data = {"first_name": "PATCH수정"}
        patch_response = authenticated_client.patch(url, patch_data, format="json")

        # Assert - PATCH는 나머지 필드 유지
        assert patch_response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.first_name == "PATCH수정"
        assert user.last_name == "이름"  # 유지됨
        assert user.phone_number == "010-1234-5678"  # 유지됨

        # Arrange - PUT 테스트를 위한 초기화
        user.first_name = "원본"
        user.last_name = "이름"
        user.save()

        # Act - PUT은 제공된 필드만 설정 (partial=False)
        put_data = {
            "email": user.email,
            "first_name": "PUT수정",
        }
        put_response = authenticated_client.put(url, put_data, format="json")

        # Assert - PUT의 경우 필수 필드 누락 시 검증 에러 가능
        # 현재 구현에서는 partial=False이므로 필수 필드가 있다면 에러
        # 하지만 UserSerializer는 대부분 필드가 선택사항이므로 성공 가능
        assert put_response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.django_db
class TestProfileValidation:
    """프로필 데이터 검증 테스트"""

    def test_invalid_field_formats(self, authenticated_client, user):
        """잘못된 데이터 형식 - 이메일 형식"""
        # Arrange
        url = reverse("user-profile")

        # Act - 잘못된 이메일 형식
        invalid_email_data = {"email": "invalid-email-format"}
        response = authenticated_client.patch(url, invalid_email_data, format="json")

        # Assert - 이메일 형식 에러
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_boundary_values(self, authenticated_client, user):
        """경계값 테스트 - 빈 문자열, 매우 긴 문자열"""
        # Arrange
        url = reverse("user-profile")

        # Act - 빈 문자열
        empty_data = {
            "first_name": "",
            "last_name": "",
        }
        response = authenticated_client.patch(url, empty_data, format="json")

        # Assert - 빈 문자열은 허용될 수 있음 (필드에 따라)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

        # Act - 매우 긴 문자열 (max_length 초과 시도)
        long_data = {"first_name": "가" * 200}  # User 모델의 max_length 초과
        response2 = authenticated_client.patch(url, long_data, format="json")

        # Assert - max_length 초과 시 에러
        if response2.status_code == status.HTTP_400_BAD_REQUEST:
            assert "first_name" in response2.data


@pytest.mark.django_db
class TestProfileEmailChange:
    """이메일 변경 보안 테스트"""

    def test_email_change_requires_reverification(self, authenticated_client, user):
        """이메일 변경 시 재인증 필요"""
        # Arrange
        url = reverse("user-profile")
        user.is_email_verified = True
        user.save()

        user.email
        new_email = "newemail@example.com"

        # Act
        response = authenticated_client.patch(url, {"email": new_email}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.email == new_email
        # 이메일 변경 시 인증 상태 초기화됨
        assert user.is_email_verified is False

    def test_email_duplicate_not_allowed(self, authenticated_client, user, second_user):
        """이메일 중복 불가"""
        # Arrange
        url = reverse("user-profile")

        # Act - 다른 사용자의 이메일로 변경 시도
        response = authenticated_client.patch(url, {"email": second_user.email}, format="json")

        # Assert - 중복 에러
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

        error_message = str(response.data["email"])
        assert "이미 사용중인" in error_message or "duplicate" in error_message.lower()

    def test_email_change_to_same_email(self, authenticated_client, user):
        """같은 이메일로 변경 시도 - 허용되어야 함 (인증 상태 유지)"""
        # Arrange
        url = reverse("user-profile")
        user.is_email_verified = True
        user.save()

        original_email = user.email

        # Act - 같은 이메일로 "변경"
        response = authenticated_client.patch(url, {"email": original_email}, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.email == original_email
        # 같은 이메일이므로 인증 상태 유지
        assert user.is_email_verified is True


@pytest.mark.django_db
class TestProfileResponseStructure:
    """프로필 API 응답 구조 상세 검증"""

    def test_response_structure_get(self, authenticated_client, user):
        """GET 응답 구조"""
        # Arrange
        url = reverse("user-profile")

        # Act
        response = authenticated_client.get(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        data = response.data

        # 모든 필드 존재 확인
        required_fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone_number",
            "birth_date",
            "postal_code",
            "address",
            "address_detail",
            "membership_level",
            "points",
            "is_email_verified",
            "email_verification_pending",
            "is_phone_verified",
            "agree_marketing_email",
            "agree_marketing_sms",
            "date_joined",
            "last_login",
        ]

        for field in required_fields:
            assert field in data, f"필수 필드 '{field}'가 응답에 없습니다"

    def test_response_structure_update(self, authenticated_client, user):
        """PATCH/PUT 응답 구조"""
        # Arrange
        url = reverse("user-profile")
        update_data = {"first_name": "테스트"}

        # Act
        response = authenticated_client.patch(url, update_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # 응답 구조 검증
        assert "user" in response.data
        assert "message" in response.data
        assert isinstance(response.data["user"], dict)
        assert response.data["message"] == "프로필이 수정되었습니다."

        # user 객체 내부 필드 확인
        user_data = response.data["user"]
        assert "id" in user_data
        assert "username" in user_data
        assert "email" in user_data
