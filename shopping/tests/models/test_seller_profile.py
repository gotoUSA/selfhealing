"""
SellerProfile 모델 테스트
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from shopping.models import SellerProfile
from shopping.tests.factories import UserFactory


@pytest.mark.django_db
class TestSellerProfileCreate:
    """SellerProfile 생성 테스트"""

    def test_create_returns_profile(self):
        """판매자 프로필 정상 생성"""
        # Arrange
        user = UserFactory.seller()

        # Act
        profile = SellerProfile.objects.create(
            user=user,
            store_name="테스트 스토어",
            store_description="테스트 스토어 설명입니다.",
            business_number="1234567890",
            representative_name="홍길동",
            business_address="서울시 강남구",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Assert
        assert profile.id is not None
        assert profile.store_name == "테스트 스토어"
        assert str(profile) == f"테스트 스토어 ({user.username})"

    def test_non_seller_user_raises_value_error(self):
        """일반 사용자는 프로필 생성 시 ValueError"""
        # Arrange
        user = UserFactory()

        # Act
        with pytest.raises(ValueError) as exc_info:
            SellerProfile.objects.create(
                user=user,
                store_name="테스트 스토어",
                business_number="1234567890",
                representative_name="홍길동",
                bank_name="신한은행",
                bank_account="110123456789",
                bank_holder="홍길동",
            )

        # Assert
        assert "판매자가 아닌 사용자" in str(exc_info.value)

    def test_duplicate_user_raises_integrity_error(self):
        """동일 사용자로 중복 생성 시 IntegrityError"""
        # Arrange
        user = UserFactory.seller()
        SellerProfile.objects.create(
            user=user,
            store_name="첫 번째 스토어",
            business_number="1234567890",
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Act
        with pytest.raises(IntegrityError) as exc_info:
            SellerProfile.objects.create(
                user=user,
                store_name="두 번째 스토어",
                business_number="0987654321",
                representative_name="김철수",
                bank_name="국민은행",
                bank_account="123456789012",
                bank_holder="김철수",
            )

        # Assert
        assert exc_info.value is not None

    def test_optional_fields_empty_returns_profile(self):
        """선택적 필드 비어있어도 생성 가능"""
        # Arrange
        user = UserFactory.seller()

        # Act
        profile = SellerProfile.objects.create(
            user=user,
            store_name="최소 정보 스토어",
            store_description="",
            business_number="1234567890",
            representative_name="홍길동",
            business_address="",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Assert
        assert profile.id is not None


@pytest.mark.django_db
class TestSellerProfileRelation:
    """SellerProfile 관계 테스트"""

    def test_user_access_via_related_name(self):
        """User에서 seller_profile로 접근 가능"""
        # Arrange
        user = UserFactory.seller()
        profile = SellerProfile.objects.create(
            user=user,
            store_name="관계 테스트 스토어",
            business_number="1234567890",
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Act
        user.refresh_from_db()

        # Assert
        assert user.seller_profile == profile

    def test_user_delete_cascades_profile(self):
        """User 삭제 시 SellerProfile도 삭제"""
        # Arrange
        user = UserFactory.seller()
        profile = SellerProfile.objects.create(
            user=user,
            store_name="삭제 테스트 스토어",
            business_number="1234567890",
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )
        profile_id = profile.id

        # Act
        user.delete()

        # Assert
        assert not SellerProfile.objects.filter(id=profile_id).exists()


@pytest.mark.django_db
class TestSellerProfileValidation:
    """SellerProfile 유효성 검증 테스트"""

    def test_valid_business_number_passes(self):
        """10자리 숫자 사업자등록번호는 통과"""
        # Arrange
        user = UserFactory.seller()
        profile = SellerProfile(
            user=user,
            store_name="테스트 스토어",
            business_number="1234567890",
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Act
        profile.full_clean()

        # Assert - ValidationError 없으면 통과

    @pytest.mark.parametrize(
        "invalid_number",
        [
            "123456789",  # 9자리
            "12345678901",  # 11자리
            "123-45-67890",  # 하이픈 포함
            "12345abcde",  # 문자 포함
        ],
    )
    def test_invalid_business_number_raises_validation_error(self, invalid_number):
        """잘못된 사업자등록번호는 ValidationError"""
        # Arrange
        user = UserFactory.seller()
        profile = SellerProfile(
            user=user,
            store_name="테스트 스토어",
            business_number=invalid_number,
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Act
        with pytest.raises(ValidationError) as exc_info:
            profile.full_clean()

        # Assert
        assert exc_info.value is not None

    def test_timestamps_auto_populated(self):
        """created_at, updated_at 자동 설정"""
        # Arrange
        user = UserFactory.seller()

        # Act
        profile = SellerProfile.objects.create(
            user=user,
            store_name="타임스탬프 테스트",
            business_number="1234567890",
            representative_name="홍길동",
            bank_name="신한은행",
            bank_account="110123456789",
            bank_holder="홍길동",
        )

        # Assert
        assert profile.created_at is not None
        assert profile.updated_at is not None
