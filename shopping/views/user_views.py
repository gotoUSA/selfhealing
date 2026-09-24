from __future__ import annotations

import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, serializers as drf_serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from shopping.serializers.user_serializers import PasswordChangeSerializer, UserSerializer
from shopping.services.token_service import TokenService
from shopping.services.user_service import UserService, UserServiceError

logger = logging.getLogger(__name__)


# ===== Swagger 문서화용 응답 Serializers =====


class ProfileUpdateResponseSerializer(drf_serializers.Serializer):
    """프로필 수정 응답"""

    user = UserSerializer()
    message = drf_serializers.CharField()


class MessageResponseSerializer(drf_serializers.Serializer):
    """일반 메시지 응답"""

    message = drf_serializers.CharField()


class ErrorResponseSerializer(drf_serializers.Serializer):
    """에러 응답"""

    error = drf_serializers.CharField()


class WithdrawRequestSerializer(drf_serializers.Serializer):
    """회원 탈퇴 요청"""

    password = drf_serializers.CharField(help_text="현재 비밀번호")


@extend_schema_view(
    get=extend_schema(
        responses={200: UserSerializer},
        summary="내 프로필 정보를 조회한다.",
        description="""처리 내용:
- 현재 로그인한 사용자의 프로필 정보를 반환한다.""",
        tags=["Users"],
    ),
    put=extend_schema(
        request=UserSerializer,
        responses={
            200: ProfileUpdateResponseSerializer,
            400: ErrorResponseSerializer,
        },
        summary="프로필 정보를 전체 수정한다.",
        description="""처리 내용:
- 현재 로그인한 사용자의 프로필 정보를 전체 수정한다.
- 모든 필드를 포함하여 요청해야 한다.""",
        tags=["Users"],
    ),
    patch=extend_schema(
        request=UserSerializer,
        responses={
            200: ProfileUpdateResponseSerializer,
            400: ErrorResponseSerializer,
        },
        summary="프로필 정보를 부분 수정한다.",
        description="""처리 내용:
- 현재 로그인한 사용자의 프로필 정보를 부분 수정한다.
- 변경할 필드만 포함하여 요청한다.""",
        tags=["Users"],
    ),
)
class ProfileView(generics.RetrieveUpdateAPIView):
    """
    사용자 프로필 API (Generic View 사용)
    - GET: 현재 로그인한 사용자 정보 조회
    - PUT/PATCH: 사용자 정보 수정
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        """현재 로그인한 사용자 반환"""
        return self.request.user

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        """GET 요청 처리 - 프로필 조회"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def update(self, request: Request, *args, **kwargs) -> Response:
        """PUT 요청 처리 - 전체 수정"""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response({"user": serializer.data, "message": "프로필이 수정되었습니다."}, status=status.HTTP_200_OK)

    def partial_update(self, request: Request, *args, **kwargs) -> Response:
        """PATCH 요청 처리 - 부분 수정"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


@extend_schema(
    tags=["Users"],
)
class PasswordChangeView(APIView):
    """
    비밀번호 변경 API
    - POST: 현재 비밀번호 확인 후 새 비밀번호로 변경
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=PasswordChangeSerializer,
        responses={
            200: MessageResponseSerializer,
            400: ErrorResponseSerializer,
        },
        summary="비밀번호를 변경한다.",
        description="""처리 내용:
- 현재 비밀번호를 확인한다.
- 새 비밀번호로 변경한다.
- 다른 기기의 로그인(Refresh Token)을 모두 무효화한다. 요청한 기기의 토큰은 유지한다.""",
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})

        if serializer.is_valid():
            with transaction.atomic():
                serializer.save()
                # 다른 기기의 로그인을 끊는다 — 비밀번호를 바꾼 이 기기의 refresh 토큰만 남긴다
                TokenService.revoke_all_for_user(
                    request.user,
                    keep_refresh_token=request.COOKIES.get("refresh_token") or request.data.get("refresh"),
                )
            logger.info(f"비밀번호 변경 완료: user_id={request.user.id}")
            return Response({"message": "비밀번호가 변경되었습니다."}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    request=WithdrawRequestSerializer,
    responses={
        200: MessageResponseSerializer,
        400: ErrorResponseSerializer,
        500: ErrorResponseSerializer,
    },
    summary="회원 탈퇴를 처리한다.",
    description="""처리 내용:
- 비밀번호를 확인한다.
- 사용자 상태를 탈퇴 상태로 변경한다.
- 모든 JWT 토큰을 무효화한다.""",
    tags=["Users"],
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def withdraw(request: Request) -> Response:
    """
    회원 탈퇴 API
    - POST: 비밀번호 확인 후 탈퇴 처리

    처리 내용:
    1. 비밀번호 확인
    2. 서비스 레이어를 통한 탈퇴 처리
       - 사용자 상태 변경 (is_withdrawn, is_active)
       - 모든 JWT 토큰 무효화 (보안 강화)
    3. 포인트 및 주문 내역은 보존
    """
    password = request.data.get("password")

    # 비밀번호 누락 확인
    if not password:
        return Response({"error": "비밀번호를 입력해주세요."}, status=status.HTTP_400_BAD_REQUEST)

    user = request.user

    # 비밀번호 확인
    if not user.check_password(password):
        logger.warning(f"회원 탈퇴 실패 - 비밀번호 불일치: user_id={user.id}")
        return Response(
            {"error": "비밀번호가 올바르지 않습니다."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # 서비스 레이어를 통한 탈퇴 처리
        result = UserService.withdraw_user(user)

        return Response({"message": result.message}, status=status.HTTP_200_OK)

    except UserServiceError as e:
        logger.error(f"탈퇴 처리 중 서비스 오류: user_id={user.id}, error={str(e)}")
        return Response(
            {"error": e.message},
            status=status.HTTP_400_BAD_REQUEST,
        )

    except Exception as e:
        # 예상치 못한 에러
        logger.error(f"탈퇴 처리 중 오류 발생: user_id={user.id}, error={str(e)}", exc_info=True)
        return Response(
            {"error": "탈퇴 처리 중 오류가 발생했습니다. 고객센터에 문의해주세요."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
