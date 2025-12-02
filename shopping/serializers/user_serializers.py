from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError

from rest_framework import serializers

from shopping.models.user import User

import logging

logger = logging.getLogger(__name__)


class UserListSerializer(serializers.ModelSerializer):
    """
    사용자 목록 조회용 경량 시리얼라이저
    - N+1 쿼리 방지를 위해 email_verification_pending 필드 제외
    - 최소한의 필드만 포함하여 성능 최적화
    """

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "membership_level",
            "points",
            "is_email_verified",
            "is_phone_verified",
            "date_joined",
            "last_login",
        ]
        read_only_fields = fields  # 모든 필드 읽기 전용


class UserSerializer(serializers.ModelSerializer):
    """
    사용자 프로필 조회/수정용 시리얼라이저
    - 비밀번호는 제외하고 표시
    - 읽기 전용 필드 설정
    """

    # 이메일 인증 대기 상태
    email_verification_pending = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
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
        read_only_fields = [
            "id",
            "username",
            "membership_level",
            "points",
            "is_email_verified",
            "is_phone_verified",
            "date_joined",
            "last_login",
        ]

    def get_email_verification_pending(self, obj: User) -> bool:
        """이메일 인증 대기중인지 확인"""
        if obj.is_email_verified:
            return False

        # 유효한 토큰이 있는지 확인
        from shopping.models.email_verification import EmailVerificationToken

        return EmailVerificationToken.objects.filter(
            user=obj,
            is_used=False,
        ).exists()

    def validate_email(self, value: str) -> str:
        """이메일 중복 검사 및 형식 검증"""
        # 현재 사용자의 이메일과 같으면 통과
        if self.instance and self.instance.email == value:
            return value

        # 다른 사용자가 이미 사용중인 이메일인지 확인
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("이미 사용중인 이메일입니다.")

        return value

    def update(self, instance: User, validated_data: dict[str, Any]) -> User:
        """
        프로필 업데이트
        이메일 변경 시 재인증 필요
        """
        # 이메일이 변경되었는지 확인
        email_changed = "email" in validated_data and validated_data["email"] != instance.email

        # 기본 업데이트 수행
        instance = super().update(instance, validated_data)

        # 이메일이 변경된 경우 인증 상태 초기화
        if email_changed:
            instance.is_email_verified = False
            instance.save(update_fields=["is_email_verified"])

        return instance


class RegisterSerializer(serializers.ModelSerializer):
    """
    회원가입용 시리얼라이저
    - 필수: username, email, password, password2
    - 선택: 나머지 필드들
    - DB constraint만으로 중복 체크
    """

    # 비밀번호 확인 필드 (DB에는 저장 안됨)
    password2 = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
        label="비밀번호 확인",
    )

    # 비밀번호는 쓰기 전용
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],  # Django 기본 비밀번호 검증
        style={"input_type": "password"},
        label="비밀번호",
    )

    # 이메일 필수 (Django EmailField 기본 max_length=254)
    email = serializers.EmailField(required=True, max_length=254, label="이메일")

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "password2",
            "first_name",
            "last_name",
            "phone_number",
            "birth_date",
            "postal_code",
            "address",
            "address_detail",
            "agree_marketing_email",
            "agree_marketing_sms",
        ]
        extra_kwargs = {"username": {"required": True}, "email": {"required": True}}

    def validate_email(self, value: str) -> str:
        """이메일 중복 검사"""
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("이미 사용중인 이메일입니다.")
        return value

    def validate_username(self, value: str) -> str:
        """사용자명 중복 검사"""
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("이미 사용중인 사용자명입니다.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """비밀번호 일치 검사"""
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "비밀번호가 일치하지 않습니다."})
        return attrs

    def create(self, validated_data: dict[str, Any]) -> User:
        """
        사용자 생성 - DB constraint를 최종 방어선으로 활용

        동시성 시나리오:
        - 여러 요청이 동시에 validation을 통과할 수 있음 (race condition)
        - DB의 unique constraint가 최종적으로 중복을 방지
        - IntegrityError를 ValidationError로 변환하여 400 Bad Request 반환
        """
        # password2는 DB에 저장하지 않으므로 제거
        validated_data.pop("password2")

        # create_user 메서드를 사용하면 비밀번호가 자동으로 해시화됨
        try:
            user = User.objects.create_user(**validated_data)
            logger.info(
                "User registration successful",
                extra={
                    "username": validated_data.get("username"),
                    "email": validated_data.get("email"),
                },
            )
            return user
        except IntegrityError as e:
            # DB unique constraint 위반 시 로깅
            logger.warning(
                "Registration failed - IntegrityError caught",
                extra={
                    "username": validated_data.get("username"),
                    "email": validated_data.get("email"),
                    "error": str(e),
                },
            )

            # 에러 메시지 파싱하여 어떤 필드가 중복되었는지 확인
            error_msg = str(e).lower()

            # PostgreSQL: 'duplicate key value violates unique constraint "shopping_user_email_key"'
            # SQLite: 'UNIQUE constraint failed: shopping_user.email'
            if "email" in error_msg or "shopping_user_email" in error_msg:
                raise serializers.ValidationError({"email": "이미 사용중인 이메일입니다."}, code="unique")
            elif "username" in error_msg or "shopping_user_username" in error_msg:
                raise serializers.ValidationError({"username": "이미 사용중인 사용자명입니다."}, code="unique")

            # phone_number도 unique일 수 있음
            elif "phone" in error_msg or "shopping_user_phone" in error_msg:
                raise serializers.ValidationError({"phone_number": "이미 사용중인 전화번호입니다."}, code="unique")

            # 예상치 못한 IntegrityError - 로깅 후 일반적인 에러 메시지 반환
            logger.error(
                "Unexpected IntegrityError during registration",
                extra={
                    "username": validated_data.get("username"),
                    "email": validated_data.get("email"),
                    "error": str(e),
                },
                exc_info=True,
            )
            raise serializers.ValidationError(
                {"detail": "회원가입 중 오류가 발생했습니다. 관리자에게 문의하세요."}, code="integrity_error"
            )


class LoginSerializer(serializers.Serializer):
    """
    로그인용 시리얼라이저
    - username과 password로 인증
    - JWT 토큰은 View에서 생성
    """

    username = serializers.CharField(required=True, label="사용자명")
    password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        label="비밀번호",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """로그인 인증 검증"""
        username = attrs.get("username")
        password = attrs.get("password")

        if username and password:
            # Django 기본 인증 함수 사용
            user = authenticate(
                request=self.context.get("request"),
                username=username,
                password=password,
            )

            if not user:
                raise serializers.ValidationError("아이디 또는 비밀번호가 올바르지 않습니다.")

            if not user.is_active:
                raise serializers.ValidationError("비활성화된 계정입니다.")

            # 탈퇴한 회원 체크
            if user.is_withdrawn:
                raise serializers.ValidationError("탈퇴한 회원입니다.")

        else:
            raise serializers.ValidationError("아이디와 비밀번호를 모두 입력해주세요.")

        attrs["user"] = user
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    """
    비밀번호 변경용 시리얼라이저
    - 현재 비밀번호 확인 후 새 비밀번호로 변경
    """

    old_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        label="현재 비밀번호",
    )
    new_password = serializers.CharField(
        required=True,
        write_only=True,
        validators=[validate_password],
        style={"input_type": "password"},
        label="새 비밀번호",
    )
    new_password2 = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        label="새 비밀번호 확인",
    )

    def validate_old_password(self, value: str) -> str:
        """현재 비밀번호 검증"""
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("현재 비밀번호가 올바르지 않습니다.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """새 비밀번호 일치 검사"""
        if attrs["new_password"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password": "새 비밀번호가 일치하지 않습니다"})
        return attrs

    def save(self) -> User:
        """비밀번호 변경 저장"""
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        return user


class TokenResponseSerializer(serializers.Serializer):
    """
    토큰 응답용 시리얼라이저
    - 로그인/회원가입 성공 시 반환되는 데이터 구조
    """

    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    user = UserSerializer(read_only=True)
