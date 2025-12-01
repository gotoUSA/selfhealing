from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import CreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from shopping.models.user import User
from shopping.serializers.user_serializers import LoginSerializer, PasswordChangeSerializer, RegisterSerializer, UserSerializer
from shopping.services.user_service import UserService
from shopping.throttles import LoginRateThrottle, RegisterRateThrottle, TokenRefreshRateThrottle


# 회원가입 응답용 Serializer (Swagger 문서화용)
class RegisterUserResponseSerializer(drf_serializers.Serializer):
    """회원가입 응답 내 사용자 정보 (최소한의 정보만 포함)"""

    username = drf_serializers.CharField()
    email = drf_serializers.EmailField()


class RegisterTokenResponseSerializer(drf_serializers.Serializer):
    """회원가입 응답 내 토큰 정보 (Access Token만 포함)"""

    access = drf_serializers.CharField(help_text="JWT Access Token")


class RegisterResponseSerializer(drf_serializers.Serializer):
    """회원가입 성공 응답 스키마

    Note:
        - Refresh Token은 보안상 HTTP Only Cookie로 전달됩니다.
        - Response body에는 Access Token만 포함됩니다.
    """

    message = drf_serializers.CharField()
    user = RegisterUserResponseSerializer()
    token = RegisterTokenResponseSerializer()
    verification_code = drf_serializers.CharField(required=False, help_text="DEBUG 모드에서만 포함")


# 로그인 응답용 Serializer (Swagger 문서화용)
class LoginResponseSerializer(drf_serializers.Serializer):
    """로그인 성공 응답 스키마"""

    message = drf_serializers.CharField()
    user = RegisterUserResponseSerializer()
    token = RegisterTokenResponseSerializer()


class LoginErrorResponseSerializer(drf_serializers.Serializer):
    """로그인 실패 응답 스키마"""

    username = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    password = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)
    non_field_errors = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


# 로그아웃 응답용 Serializer
class LogoutResponseSerializer(drf_serializers.Serializer):
    """로그아웃 응답 스키마"""

    message = drf_serializers.CharField()


class LogoutErrorResponseSerializer(drf_serializers.Serializer):
    """로그아웃 에러 응답 스키마"""

    error = drf_serializers.CharField()


class LogoutRequestSerializer(drf_serializers.Serializer):
    """로그아웃 요청 스키마 (Swagger 테스트용)"""

    refresh = drf_serializers.CharField(
        required=False,
        help_text="Refresh Token (Cookie에서 자동으로 읽어오므로 선택사항. Swagger 테스트 시 직접 입력)"
    )


# 토큰 갱신 응답용 Serializer
class TokenRefreshResponseSerializer(drf_serializers.Serializer):
    """토큰 갱신 응답 스키마"""

    access = drf_serializers.CharField(help_text="새로운 JWT Access Token")
    message = drf_serializers.CharField()


class TokenRefreshRequestSerializer(drf_serializers.Serializer):
    """토큰 갱신 요청 스키마 (선택적)"""

    refresh = drf_serializers.CharField(required=False, help_text="Refresh Token (Cookie에서 자동으로 읽어오므로 선택사항)")


# 토큰 확인 응답용 Serializer
class CheckTokenResponseSerializer(drf_serializers.Serializer):
    """토큰 유효성 확인 응답 스키마"""

    valid = drf_serializers.BooleanField()
    user = RegisterUserResponseSerializer()
    message = drf_serializers.CharField()


class RegisterView(CreateAPIView):
    """
    회원가입 API (비동기 이메일 발송)
    - POST: 새 사용자 생성 및 JWT 토큰 발급
    """

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [RegisterRateThrottle]

    @extend_schema(
        request=RegisterSerializer,
        responses={201: RegisterResponseSerializer},
        summary="새 사용자를 생성한다.",
        description="""처리 내용:
- 사용자 정보를 검증하고 계정을 생성한다.
- JWT Access/Refresh 토큰을 발급한다.
- 이메일 인증 코드를 발송한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request, *args, **kwargs) -> Response:
        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer: RegisterSerializer) -> None:
        """
        사용자 생성 후 후처리 (토큰 생성 + 이메일 발송)

        트랜잭션을 사용하여 동시 가입 시도 시 race condition 방지:
        - 여러 요청이 동시에 validation을 통과할 수 있음
        - atomic 트랜잭션 내에서 DB constraint가 최종 방어선
        - IntegrityError 발생 시 ValidationError로 변환하여 400 반환
        """
        with transaction.atomic():
            user = serializer.save()
            # UserService로 회원가입 후처리 (토큰 생성 + 이메일 발송)
            result = UserService.register_user(user)
            # create() 메서드에서 접근할 수 있도록 인스턴스에 저장
            self._user = user
            self._result = result

    def create(self, request: Request, *args, **kwargs) -> Response:
        """회원가입 응답 커스터마이징

        보안 고려사항:
        - Refresh Token은 HTTP Only Cookie로 전달하여 XSS 공격으로부터 보호
        - Response body에는 Access Token만 포함
        - 사용자 정보는 최소한(username, email)만 노출
        """
        from django.conf import settings

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        # 최소한의 사용자 정보만 반환
        response_data = {
            "message": "회원가입이 완료되었습니다. 인증 이메일을 확인해주세요.",
            "user": {
                "username": self._user.username,
                "email": self._user.email,
            },
            "token": {
                "access": self._result["tokens"]["access"],
            },
        }

        # DEBUG 모드에서만 verification_code 포함
        if "verification_code" in self._result["verification_result"]:
            response_data["verification_code"] = self._result["verification_result"]["verification_code"]

        response = Response(response_data, status=status.HTTP_201_CREATED)

        # Refresh Token은 HTTP Only Cookie로 전달 (XSS 방지)
        refresh_token = self._result["tokens"]["refresh"]

        # Cookie 설정
        cookie_max_age = 7 * 24 * 60 * 60  # 7일 (초 단위)
        cookie_secure = not settings.DEBUG  # 프로덕션에서는 HTTPS만 허용
        cookie_samesite = "Lax"  # CSRF 방지

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            max_age=cookie_max_age,
            httponly=True,  # JavaScript에서 접근 불가
            secure=cookie_secure,  # HTTPS에서만 전송
            samesite=cookie_samesite,
        )

        return response


class LoginView(APIView):
    """
    로그인 API
    - POST: 인증 후 JWT 토큰 발급
    - 로그인 시 비회원 장바구니가 있으면 회원 장바구니로 병합
    """

    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    @extend_schema(
        request=LoginSerializer,
        responses={
            200: LoginResponseSerializer,
            400: LoginErrorResponseSerializer,
        },
        summary="사용자 인증 후 토큰을 발급한다.",
        description="""처리 내용:
- 사용자 인증 정보를 검증한다.
- JWT Access/Refresh 토큰을 발급한다.
- 비회원 장바구니가 있으면 회원 장바구니로 병합한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})

        if serializer.is_valid():
            user = serializer.validated_data["user"]

            # 비회원 장바구니 병합 (로그인 전 세션에 장바구니가 있었다면)
            session_key = request.session.session_key
            if session_key:
                try:
                    from shopping.models.cart import Cart

                    Cart.merge_anonymous_cart(user, session_key)
                except Exception:
                    # 병합 실패해도 로그인은 진행 (에러 무시)
                    pass

            # 마지막 로그인 시간 및 IP 업데이트
            user.last_login = timezone.now()

            # IP 주소 가져오기
            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
            if x_forwarded_for:
                ip = x_forwarded_for.split(",")[0]
            else:
                ip = request.META.get("REMOTE_ADDR")
            user.last_login_ip = ip

            user.save(update_fields=["last_login", "last_login_ip"])

            # JWT 토큰 생성
            refresh = RefreshToken.for_user(user)

            # 최소한의 사용자 정보만 반환
            response_data = {
                "message": "로그인 되었습니다.",
                "user": {
                    "username": user.username,
                    "email": user.email,
                },
                "token": {
                    "access": str(refresh.access_token),
                },
            }

            response = Response(response_data, status=status.HTTP_200_OK)

            # Refresh Token은 HTTP Only Cookie로 전달 (XSS 방지)
            from django.conf import settings

            cookie_max_age = 7 * 24 * 60 * 60  # 7일 (초 단위)
            cookie_secure = not settings.DEBUG  # 프로덕션에서는 HTTPS만 허용
            cookie_samesite = "Lax"  # CSRF 방지

            response.set_cookie(
                key="refresh_token",
                value=str(refresh),
                max_age=cookie_max_age,
                httponly=True,  # JavaScript에서 접근 불가
                secure=cookie_secure,  # HTTPS에서만 전송
                samesite=cookie_samesite,
            )

            return response

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomTokenRefreshView(TokenRefreshView):
    """
    Refresh Token으로 새로운 Access Token 발급

    요청 형식:
    - HTTP Only Cookie에서 refresh_token을 자동으로 읽어옵니다.
    - 또는 body에 {"refresh": "..."} 형식으로 전달할 수도 있습니다.

    응답 형식:
    {
        "access": "새로운_access_token",
        "message": "토큰이 갱신되었습니다."
    }

    Note:
        - 새로운 Refresh Token은 HTTP Only Cookie로 자동 갱신됩니다.
        - ROTATE_REFRESH_TOKENS=True인 경우 이전 토큰은 블랙리스트에 추가됩니다.
    """

    throttle_classes = [TokenRefreshRateThrottle]

    @extend_schema(
        request=TokenRefreshRequestSerializer,
        responses={
            200: TokenRefreshResponseSerializer,
            400: LogoutErrorResponseSerializer,
            401: LogoutErrorResponseSerializer,
        },
        summary="새로운 Access Token을 발급한다.",
        description="""처리 내용:
- Refresh Token을 검증한다. (HTTP Only Cookie 우선, 없으면 Body에서 읽음)
- 새로운 Access Token을 발급한다.
- Refresh Token을 갱신하여 Cookie에 저장한다.""",
        tags=["Auth"],
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from django.conf import settings
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        import jwt as pyjwt

        # Cookie에서 refresh token 읽기 (우선), 없으면 body에서 읽기
        refresh_token = request.COOKIES.get("refresh_token") or request.data.get("refresh")

        if not refresh_token:
            return Response({"error": "refresh token이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 블랙리스트된 토큰인지 확인
        try:
            # JWT에서 jti 추출하여 블랙리스트 확인
            decoded = pyjwt.decode(refresh_token, options={"verify_signature": False})
            jti = decoded.get("jti")

            if jti and BlacklistedToken.objects.filter(token__jti=jti).exists():
                raise InvalidToken("Token is blacklisted")

            # SimpleJWT의 RefreshToken 검증 (만료, 서명 등)
            token = RefreshToken(refresh_token)
        except pyjwt.exceptions.DecodeError:
            # 토큰 형식이 잘못된 경우
            raise InvalidToken("Invalid token format")
        except TokenError as e:
            raise InvalidToken(e.args[0])

        # request.data를 수정하여 serializer에 전달
        mutable_data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        mutable_data["refresh"] = refresh_token

        serializer = self.get_serializer(data=mutable_data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        validated_data = serializer.validated_data

        # Access Token만 body에 반환
        response_data = {
            "access": validated_data.get("access"),
            "message": "토큰이 갱신되었습니다.",
        }

        response = Response(response_data, status=status.HTTP_200_OK)

        # 새 Refresh Token이 있으면 Cookie 갱신 (ROTATE_REFRESH_TOKENS=True인 경우)
        new_refresh = validated_data.get("refresh")
        if new_refresh:
            cookie_max_age = 7 * 24 * 60 * 60  # 7일
            cookie_secure = not settings.DEBUG
            cookie_samesite = "Lax"

            response.set_cookie(
                key="refresh_token",
                value=new_refresh,
                max_age=cookie_max_age,
                httponly=True,
                secure=cookie_secure,
                samesite=cookie_samesite,
            )

        return response


class LogoutView(APIView):
    """
    로그아웃 API
    - POST: Refresh Token을 블랙리스트에 추가하고 Cookie 삭제
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=LogoutRequestSerializer,
        responses={
            200: LogoutResponseSerializer,
            400: LogoutErrorResponseSerializer,
        },
        summary="로그아웃을 처리한다.",
        description="""처리 내용:
- Refresh Token을 블랙리스트에 추가하여 무효화한다.
- Cookie에서 refresh_token을 삭제한다.

**참고:** Cookie에 refresh_token이 있으면 자동으로 사용됩니다.
Swagger에서 테스트 시에는 body에 refresh 토큰을 직접 입력하세요.""",
        tags=["Auth"],
    )
    def post(self, request: Request) -> Response:
        try:
            # Cookie에서 refresh token 읽기 (우선), 없으면 body에서 읽기
            refresh_token = request.COOKIES.get("refresh_token") or request.data.get("refresh")

            if not refresh_token:
                return Response(
                    {"error": "Refresh token이 필요합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # 토큰 블랙리스트에 추가
            token = RefreshToken(refresh_token)
            token.blacklist()

            response = Response({"message": "로그아웃 되었습니다."}, status=status.HTTP_200_OK)

            # Refresh Token Cookie 삭제
            response.delete_cookie("refresh_token")

            return response

        except TokenError:
            # 토큰이 유효하지 않아도 Cookie는 삭제
            response = Response(
                {"error": "유효하지 않은 토큰입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
            response.delete_cookie("refresh_token")
            return response
        except Exception as e:
            response = Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            response.delete_cookie("refresh_token")
            return response


@extend_schema(
    responses={200: CheckTokenResponseSerializer},
    summary="Access Token의 유효성을 확인한다.",
    description="""처리 내용:
- Access Token의 유효성을 검증한다.
- 유효한 경우 사용자 정보를 반환한다.""",
    tags=["Auth"],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def check_token(request: Request) -> Response:
    """
    토큰 유효성 확인 API
    - GET: 현재 Access Token이 유효한지 확인
    """
    return Response(
        {
            "valid": True,
            "user": UserSerializer(request.user).data,
            "message": "유효한 토큰입니다.",
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def email_verification_request(request: Request) -> Response:
    """
    이메일 인증 요청 API (추후 구현)
    - POST: 인증 이메일 발송
    """
    # TODO: 이메일 발송 로직 구현
    return Response(
        {"message": "이메일 인증 기능은 준비중입니다."},
        status=status.HTTP_501_NOT_IMPLEMENTED,
    )
