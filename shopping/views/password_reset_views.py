from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model

from drf_spectacular.utils import extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from shopping.serializers.password_reset_serializers import PasswordResetConfirmSerializer, PasswordResetRequestSerializer
from shopping.services.password_reset_service import PasswordResetService
from shopping.throttles import PasswordResetRateThrottle

User = get_user_model()


# ===== Swagger 문서화용 응답 Serializers =====


class PasswordResetMessageResponseSerializer(drf_serializers.Serializer):
    """비밀번호 재설정 성공 응답"""

    message = drf_serializers.CharField()


class PasswordResetConfirmResponseSerializer(drf_serializers.Serializer):
    """비밀번호 재설정 확인 성공 응답"""

    message = drf_serializers.CharField()
    username = drf_serializers.CharField(help_text="로그인 시 사용할 사용자명")


class PasswordResetErrorResponseSerializer(drf_serializers.Serializer):
    """비밀번호 재설정 에러 응답"""

    email = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    token = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    new_password = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    new_password2 = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    non_field_errors = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


class PasswordResetRequestView(APIView):
    """
    비밀번호 재설정 요청 (이메일 발송)

    POST /api/auth/password/reset/request/

    인증 불필요
    """

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={
            200: PasswordResetMessageResponseSerializer,
            400: PasswordResetErrorResponseSerializer,
            429: PasswordResetErrorResponseSerializer,
        },
        summary="비밀번호 재설정 이메일을 발송한다.",
        description="""처리 내용:
- 이메일 주소를 검증한다.
- 비밀번호 재설정 토큰을 생성한다.
- 재설정 링크가 포함된 이메일을 발송한다.
- 보안상 계정 존재 여부를 노출하지 않는다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        """
        비밀번호 재설정 이메일 발송

        요청:
        {
            "email": "user@example.com"
        }

        응답:
        {
            "message": "비밀번호 재설정 이메일이 발송되었습니다."
        }

        보안 고려사항:
        - 존재하지 않는 이메일이어도 같은 메시지 반환 (계정 존재 여부 노출 방지)
        - 실제 이메일은 존재하는 계정에만 발송
        - 서비스 레이어를 통해 이전 토큰 자동 무효화
        """
        serializer = PasswordResetRequestSerializer(data=request.data)

        if serializer.is_valid():
            user = serializer.validated_data.get("user")

            # 사용자가 존재하는 경우에만 서비스 호출
            if user:
                PasswordResetService.request_password_reset(user)

            # 보안: 사용자 존재 여부와 관계없이 같은 메시지 반환
            return Response(
                {
                    "message": "해당 이메일로 비밀번호 재설정 링크가 발송되었습니다. 이메일을 확인해주세요.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmView(APIView):
    """
    비밀번호 재설정 확인 (새 비밀번호 설정)

    POST /api/auth/password/reset/confirm/

    인증 불필요
    """

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]  # 토큰 brute force 방지

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={
            200: PasswordResetConfirmResponseSerializer,
            400: PasswordResetErrorResponseSerializer,
            429: PasswordResetErrorResponseSerializer,
        },
        summary="새 비밀번호를 설정한다.",
        description="""처리 내용:
- 이메일과 토큰을 검증한다.
- 새 비밀번호를 설정한다.
- 사용된 토큰을 무효화한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        """
        토큰을 사용하여 새 비밀번호 설정

        요청:
        {
            "email": "user@example.com",
            "token": "uuid-token-here",
            "new_password": "newpass123!",
            "new_password2": "newpass123!"
        }

        응답:
        {
            "message": "비밀번호가 성공적으로 변경되었습니다. 새 비밀번호로 로그인해주세요."
        }

        보안 고려사항:
        - 이메일과 토큰을 함께 검증하여 타이밍 공격 방지
        - throttle로 토큰 brute force 공격 방지
        - Model의 verify_token() 사용으로 해시 기반 검증
        """
        serializer = PasswordResetConfirmSerializer(data=request.data)

        if serializer.is_valid():
            # 비밀번호 변경 (트랜잭션 보장)
            user = serializer.save()

            return Response(
                {
                    "message": "비밀번호가 성공적으로 변경되었습니다. 새 비밀번호로 로그인해주세요.",
                    "username": user.username,  # 로그인 시 사용
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
