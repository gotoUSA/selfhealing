"""
판매자 프로필 모델

판매자의 사업자 정보 및 정산 정보를 관리합니다.
User 모델의 is_seller=True인 사용자와 1:1 관계를 가집니다.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class SellerProfile(models.Model):
    """
    판매자 프로필 모델

    사업자 정보, 스토어 정보, 정산 정보를 관리합니다.
    User.is_seller=True인 사용자만 SellerProfile을 가질 수 있습니다.

    사용 예시:
        user = User.objects.get(is_seller=True)
        profile = user.seller_profile
        print(profile.store_name)
    """

    # 사용자 연결 (1:1 관계)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_profile",
        verbose_name="사용자",
    )

    # 스토어 정보
    store_name = models.CharField(
        max_length=100,
        verbose_name="스토어명",
        help_text="고객에게 표시되는 스토어 이름",
    )
    store_description = models.TextField(
        blank=True,
        verbose_name="스토어 소개",
        help_text="스토어에 대한 간단한 소개글",
    )

    # 사업자 정보
    business_number = models.CharField(
        max_length=12,
        verbose_name="사업자등록번호",
        help_text="'-' 없이 10자리 숫자 (예: 1234567890)",
        validators=[
            RegexValidator(
                regex=r"^\d{10}$",
                message="사업자등록번호는 10자리 숫자여야 합니다.",
            ),
        ],
    )
    representative_name = models.CharField(
        max_length=50,
        verbose_name="대표자명",
    )
    business_address = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="사업장 주소",
    )

    # 정산 정보
    bank_name = models.CharField(
        max_length=20,
        verbose_name="은행명",
        help_text="정산받을 은행 (예: 국민은행, 신한은행)",
    )
    bank_account = models.CharField(
        max_length=30,
        verbose_name="계좌번호",
        help_text="'-' 없이 숫자만 입력",
    )
    bank_holder = models.CharField(
        max_length=50,
        verbose_name="예금주",
    )

    # 시간 정보
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="등록일")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="수정일")

    class Meta:
        verbose_name = "판매자 프로필"
        verbose_name_plural = "판매자 프로필"

    def __str__(self) -> str:
        return f"{self.store_name} ({self.user.username})"

    def save(self, *args, **kwargs):
        """저장 시 사용자가 판매자인지 확인"""
        if not self.user.is_seller:
            raise ValueError("판매자가 아닌 사용자는 판매자 프로필을 가질 수 없습니다.")
        super().save(*args, **kwargs)
