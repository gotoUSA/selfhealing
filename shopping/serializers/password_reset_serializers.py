from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from rest_framework import serializers

from shopping.models.password_reset import PasswordResetToken

if TYPE_CHECKING:
    from shopping.models.user import User as UserType
else:
    UserType = None

User = get_user_model()


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    비밀번호 재설정 요청 (이메일 발송)

    이메일 주소를 받아서 해당 사용자에게 재설정 링크 발송
    """

    email = serializers.EmailField(
        required=True,
        write_only=True,
        help_text="가입한 이메일 주소",
    )

    message = serializers.CharField(
        read_only=True,
        help_text="응답 메시지",
    )

    def validate_email(self, value: str) -> str:
        """이메일 검증"""
        # 이메일 형식은 EmailField에서 자동 검증됨
        # 여기서는 추가 검증만 수행

        # 보안: 존재하지 않는 이메일이어도 에러를 던지지 않음
        # (사용자 정보 노출 방지)
        return value.lower()  # 소문자로 통일

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """전체 검증"""
        email = attrs.get("email")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # 보안: 사용자가 존재하지 않아도 같은 메시지 반환
            # (계정 존재 여부 노출 방지)
            attrs["user"] = None
            return attrs

        # 탈퇴한 사용자는 재설정 불가
        if hasattr(user, "is_withdrawn") and user.is_withdrawn:
            raise serializers.ValidationError({"email": "탈퇴한 회원은 비밀번호 재설정을 할 수 없습니다."})

        # 비활성화된 계정은 재설정 불가
        if not user.is_active:
            raise serializers.ValidationError({"email": "비활성화된 계정입니다. 관리자에게 문의하세요."})

        attrs["user"] = user
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    비밀번호 재설정 확인 (새 비밀번호 설정)

    이메일, 토큰, 새 비밀번호를 받아서 비밀번호 변경

    보안 고려사항:
    - 이메일과 토큰을 함께 검증하여 타이밍 공격 방지
    - Model의 verify_token() 메서드 사용으로 일관성 보장
    - 잘못된 이메일/토큰 조합도 같은 에러 메시지 반환
    """

    email = serializers.EmailField(
        required=True,
        write_only=True,
        help_text="가입한 이메일 주소",
    )

    token = serializers.UUIDField(
        required=True,
        write_only=True,
        help_text="이메일로 받은 재설정 토큰",
    )

    new_password = serializers.CharField(
        required=True,
        write_only=True,
        min_length=8,
        max_length=128,
        style={"input_type": "password"},
        help_text="새 비밀번호 (최소 8자)",
    )

    new_password2 = serializers.CharField(
        required=True,
        write_only=True,
        min_length=8,
        max_length=128,
        style={"input_type": "password"},
        help_text="새 비밀번호 확인",
    )

    message = serializers.CharField(
        read_only=True,
        help_text="응답 메시지",
    )

    def validate_email(self, value: str) -> str:
        """이메일 정규화"""
        return value.lower()

    def validate_new_password(self, value: str) -> str:
        """새 비밀번호 기본 검증

        Note: user context가 필요한 검증은 validate()에서 수행
        """
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """전체 검증

        이메일과 토큰을 함께 검증하여 타이밍 공격 방지:
        - 이메일로 사용자 조회
        - 사용자와 토큰으로 Model의 verify_token() 호출
        - 비밀번호 정책 검증 (user context 포함)
        - 실패 시 동일한 에러 메시지 반환 (정보 노출 방지)
        """
        email = attrs.get("email")
        token = attrs.get("token")
        new_password = attrs.get("new_password")
        new_password2 = attrs.get("new_password2")

        # 비밀번호 일치 확인
        if new_password != new_password2:
            raise serializers.ValidationError({"new_password2": "비밀번호가 일치하지 않습니다."})

        # 사용자 조회
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # 보안: 존재하지 않는 이메일도 동일한 에러 메시지
            raise serializers.ValidationError("유효하지 않은 토큰입니다.")

        # 계정 상태 확인
        if hasattr(user, "is_withdrawn") and user.is_withdrawn:
            raise serializers.ValidationError("탈퇴한 회원은 비밀번호 재설정을 할 수 없습니다.")

        if not user.is_active:
            raise serializers.ValidationError("비활성화된 계정입니다. 관리자에게 문의하세요.")

        # Django 비밀번호 정책 검증 (user context 포함)
        # UserAttributeSimilarityValidator가 user의 이름/이메일과 유사한 비밀번호 차단
        try:
            validate_password(new_password, user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        # Model의 verify_token 메서드 사용 (해시 검증, 만료 확인)
        token_obj = PasswordResetToken.verify_token(user, str(token))

        if not token_obj:
            # 보안: 잘못된 토큰도 동일한 에러 메시지
            raise serializers.ValidationError("유효하지 않은 토큰입니다.")

        # 검증된 토큰 객체를 save()에서 사용
        attrs["token_obj"] = token_obj
        attrs["user"] = user

        return attrs

    def save(self) -> UserType:
        """비밀번호 변경 처리

        서비스 레이어를 통해 트랜잭션으로 보장:
        - 비밀번호 변경
        - 토큰 사용 처리 (is_used=True, used_at 설정)
        - EmailLog 상태 업데이트
        """
        from shopping.services.password_reset_service import PasswordResetService

        token_obj = self.validated_data["token_obj"]
        user = self.validated_data["user"]
        new_password = self.validated_data["new_password"]

        result = PasswordResetService.confirm_password_reset(
            user=user,
            token_obj=token_obj,
            new_password=new_password,
        )

        return result.user
