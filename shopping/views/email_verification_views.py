"""이메일 인증 ViewSet

HTTP 요청/응답 처리를 담당합니다.
비즈니스 로직은 EmailVerificationService에 위임합니다.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers as drf_serializers, status

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers.email_verification_serializers import (
    ResendVerificationEmailSerializer,
    SendVerificationEmailSerializer,
    VerifyEmailByCodeSerializer,
)
from ..services.email_verification_service import (
    EmailVerificationService,
    EmailVerificationServiceError,
)
from ..throttles import EmailVerificationRateThrottle, EmailVerificationResendRateThrottle


# ===== Swagger 문서화용 응답 Serializers =====


class EmailVerificationMessageResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 성공 응답"""

    message = drf_serializers.CharField()


class EmailVerificationErrorResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 에러 응답"""

    error = drf_serializers.CharField(required=False)
    code = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    token = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


class EmailVerificationStatusResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 상태 응답"""

    is_verified = drf_serializers.BooleanField()
    email = drf_serializers.EmailField()
    pending_verification = drf_serializers.BooleanField(required=False)
    token_expired = drf_serializers.BooleanField(required=False)
    can_resend = drf_serializers.BooleanField(required=False)


# ===== Swagger 문서화용 응답 Serializers =====


class EmailVerificationMessageResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 성공 응답"""

    message = drf_serializers.CharField()


class EmailVerificationErrorResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 에러 응답"""

    error = drf_serializers.CharField(required=False)
    code = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    token = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


class EmailVerificationStatusResponseSerializer(drf_serializers.Serializer):
    """이메일 인증 상태 응답"""

    is_verified = drf_serializers.BooleanField()
    email = drf_serializers.EmailField()
    pending_verification = drf_serializers.BooleanField(required=False)
    token_expired = drf_serializers.BooleanField(required=False)
    can_resend = drf_serializers.BooleanField(required=False)


class SendVerificationEmailView(APIView):
    """
    이메일 인증 발송 API

    POST /api/auth/email/send/
    - 새 인증 토큰을 생성하고 이메일을 발송합니다.
    - 기존 미사용 토큰은 무효화됩니다.
    - 비동기(Celery)로 이메일을 발송합니다.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [EmailVerificationRateThrottle]

    @extend_schema(
        request=None,
        responses={
            200: EmailVerificationMessageResponseSerializer,
            400: EmailVerificationErrorResponseSerializer,
            429: EmailVerificationErrorResponseSerializer,
        },
        summary="인증 이메일을 발송한다.",
        description="""처리 내용:
- 기존 미사용 토큰을 무효화한다.
- 새 인증 토큰을 생성한다.
- 비동기(Celery)로 이메일을 발송한다.
- 이미 인증된 사용자는 발송 불가한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        """인증 이메일 발송"""
        # Serializer 검증 (이미 인증된 사용자 체크)
        serializer = SendVerificationEmailSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return self._handle_validation_error(serializer.errors)

        try:
            result = EmailVerificationService.send_verification_email(
                user=request.user,
                is_resend=False,
            )

            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK,
            )

        except EmailVerificationServiceError as e:
            return self._handle_service_error(e)

    def _handle_validation_error(self, errors: dict) -> Response:
        """Serializer 검증 에러 처리"""
        # 재발송 제한 에러인 경우 429 반환
        if "1분" in str(errors):
            return Response(errors, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    def _handle_service_error(self, error: EmailVerificationServiceError) -> Response:
        """서비스 에러 처리"""
        status_map = {
            "ALREADY_VERIFIED": status.HTTP_400_BAD_REQUEST,
            "RESEND_COOLDOWN": status.HTTP_429_TOO_MANY_REQUESTS,
        }

        http_status = status_map.get(error.code, status.HTTP_400_BAD_REQUEST)

        return Response({"error": error.message}, status=http_status)


class VerifyEmailView(APIView):
    """
    이메일 인증 확인 API

    GET  /api/auth/email/verify/?token=xxx - UUID 토큰으로 인증
    POST /api/auth/email/verify/           - 6자리 코드로 인증
    """

    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="token",
                type=str,
                location=OpenApiParameter.QUERY,
                description="UUID 형식의 인증 토큰 (이메일 링크에 포함)",
                required=True,
            ),
        ],
        responses={
            200: EmailVerificationMessageResponseSerializer,
            400: EmailVerificationErrorResponseSerializer,
        },
        summary="UUID 토큰으로 이메일을 인증한다.",
        description="""처리 내용:
- 토큰의 유효성을 검증한다.
- 사용자의 이메일 인증 상태를 완료로 변경한다.
- 토큰을 사용 완료 상태로 변경한다.""",
        tags=["Auth"],
    )
    def get(self, request: Request) -> Response:
        """UUID 토큰으로 인증 (GET 요청)"""
        token_str = request.GET.get("token")

        try:
            EmailVerificationService.verify_by_token(token_str)

            return Response(
                {"message": "이메일 인증이 완료되었습니다!"},
                status=status.HTTP_200_OK,
            )

        except EmailVerificationServiceError as e:
            return self._handle_service_error(e)

    @extend_schema(
        request=VerifyEmailByCodeSerializer,
        responses={
            200: EmailVerificationMessageResponseSerializer,
            400: EmailVerificationErrorResponseSerializer,
            401: EmailVerificationErrorResponseSerializer,
        },
        summary="6자리 코드로 이메일을 인증한다.",
        description="""처리 내용:
- 인증 코드의 유효성을 검증한다.
- 사용자의 이메일 인증 상태를 완료로 변경한다.
- 코드는 대소문자 구분 없이 처리한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        """6자리 코드로 인증 (POST 요청)"""
        # 로그인 체크
        if not request.user.is_authenticated:
            return Response(
                {"error": "로그인이 필요합니다."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        code = request.data.get("code")

        try:
            EmailVerificationService.verify_by_code(
                user=request.user,
                code=code,
            )

            return Response(
                {"message": "이메일 인증이 완료되었습니다."},
                status=status.HTTP_200_OK,
            )

        except EmailVerificationServiceError as e:
            return self._handle_service_error(e)

    def _handle_service_error(self, error: EmailVerificationServiceError) -> Response:
        """서비스 에러 처리"""
        status_map = {
            "TOKEN_MISSING": status.HTTP_400_BAD_REQUEST,
            "TOKEN_INVALID": status.HTTP_400_BAD_REQUEST,
            "TOKEN_USED": status.HTTP_400_BAD_REQUEST,
            "TOKEN_EXPIRED": status.HTTP_400_BAD_REQUEST,
            "CODE_MISSING": status.HTTP_400_BAD_REQUEST,
            "CODE_EXPIRED": status.HTTP_400_BAD_REQUEST,
            "CODE_MISMATCH": status.HTTP_400_BAD_REQUEST,
            "NO_VALID_TOKEN": status.HTTP_400_BAD_REQUEST,
            "ALREADY_VERIFIED": status.HTTP_400_BAD_REQUEST,
        }

        http_status = status_map.get(error.code, status.HTTP_400_BAD_REQUEST)

        # 에러 코드에 따라 적절한 필드로 응답
        if error.code in ("TOKEN_MISSING", "TOKEN_INVALID", "TOKEN_USED", "TOKEN_EXPIRED"):
            return Response({"token": [error.message]}, status=http_status)
        elif error.code in ("CODE_MISSING", "CODE_EXPIRED", "CODE_MISMATCH"):
            return Response({"code": [error.message]}, status=http_status)
        else:
            return Response({"error": error.message}, status=http_status)


class ResendVerificationEmailView(APIView):
    """
    이메일 재발송 API

    POST /api/auth/email/resend/
    - 새 인증 토큰을 생성하고 이메일을 재발송합니다.
    - 1분 내 재발송 불가합니다.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [EmailVerificationResendRateThrottle]

    @extend_schema(
        request=None,
        responses={
            200: EmailVerificationMessageResponseSerializer,
            400: EmailVerificationErrorResponseSerializer,
            429: EmailVerificationErrorResponseSerializer,
        },
        summary="인증 이메일을 재발송한다.",
        description="""처리 내용:
- 기존 미사용 토큰을 무효화한다.
- 새 인증 토큰을 생성한다.
- 비동기(Celery)로 이메일을 재발송한다.
- 1분 내 재발송 불가한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        """인증 이메일 재발송"""
        # Serializer 검증
        serializer = ResendVerificationEmailSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return self._handle_validation_error(serializer.errors)

        try:
            result = EmailVerificationService.send_verification_email(
                user=request.user,
                is_resend=True,
            )

            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK,
            )

        except EmailVerificationServiceError as e:
            return self._handle_service_error(e)

    def _handle_validation_error(self, errors: dict) -> Response:
        """Serializer 검증 에러 처리"""
        if "1분" in str(errors):
            return Response(errors, status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(errors, status=status.HTTP_400_BAD_REQUEST)

    def _handle_service_error(self, error: EmailVerificationServiceError) -> Response:
        """서비스 에러 처리"""
        status_map = {
            "ALREADY_VERIFIED": status.HTTP_400_BAD_REQUEST,
            "RESEND_COOLDOWN": status.HTTP_429_TOO_MANY_REQUESTS,
        }

        http_status = status_map.get(error.code, status.HTTP_400_BAD_REQUEST)

        return Response({"error": error.message}, status=http_status)


@extend_schema(
    responses={200: EmailVerificationStatusResponseSerializer},
    summary="이메일 인증 상태를 확인한다.",
    description="""처리 내용:
- 현재 사용자의 이메일 인증 상태를 반환한다.
- 미인증 시 토큰 만료 여부와 재발송 가능 여부를 포함한다.""",
    tags=["Auth"],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def check_verification_status(request: Request) -> Response:
    """이메일 인증 상태 확인 API"""
    status_info = EmailVerificationService.get_verification_status(request.user)

    response_data = {
        "is_verified": status_info.is_verified,
        "email": status_info.email,
    }

    # 인증되지 않은 경우에만 추가 정보 포함
    if not status_info.is_verified and status_info.pending_verification:
        response_data.update(
            {
                "pending_verification": status_info.pending_verification,
                "token_expired": status_info.token_expired,
                "can_resend": status_info.can_resend,
            }
        )

    return Response(response_data, status=status.HTTP_200_OK)
