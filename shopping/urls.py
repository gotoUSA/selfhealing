from django.conf import settings
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt

from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.kakao.views import KakaoOAuth2Adapter
from allauth.socialaccount.providers.naver.views import NaverOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client

# social login
from dj_rest_auth.registration.views import SocialLoginView
from drf_spectacular.utils import extend_schema
from rest_framework.routers import DefaultRouter

# notification
from rest_framework_nested import routers

# Auth Views
from shopping.views.auth_views import (
    CustomTokenRefreshView,
    LoginView,
    LogoutView,
    RegisterView,
    check_token,
)
from shopping.views.cart_views import CartItemViewSet, CartViewSet

# Email Verification Views
from shopping.views.email_verification_views import (
    ResendVerificationEmailView,
    SendVerificationEmailView,
    VerifyEmailView,
    check_verification_status,
)
from shopping.views.order_views import OrderViewSet

# password_reset view
from shopping.views.password_reset_views import PasswordResetConfirmView, PasswordResetRequestView

# Payment Views
from shopping.views.payment_views import (
    PaymentCancelView,
    PaymentConfirmView,
    PaymentDetailView,
    PaymentFailView,
    PaymentListView,
    PaymentRequestView,
    PaymentStatusView,
    PointsOnlyPaymentView,
    StockVerificationView,
    payment_fail,
    payment_success,
    payment_test_page,
)

# ViewSet
from shopping.views.product_views import CategoryViewSet, ProductViewSet

# return_request view
from shopping.views.return_views import ReturnViewSet, SellerReturnViewSet

# wishlist
from shopping.views.wishlist_views import WishlistViewSet

# Webhook
from shopping.webhooks.toss_webhook_view import toss_webhook

# Point view
from .views import point_views
from .views.notification_views import NotificationViewSet
from .views.product_qa_views import MyQuestionViewSet, ProductQuestionViewSet

# user view
from shopping.views.user_views import (
    PasswordChangeView,
    ProfileView,
    withdraw,
)

# social auth callback
from shopping.views.social_auth_views import SocialCallbackView


# 소셜 로그인 요청용 Serializer (Swagger 스키마용)
from rest_framework import serializers as drf_serializers


class SocialLoginRequestSerializer(drf_serializers.Serializer):
    """소셜 로그인 요청 Serializer"""

    access_token = drf_serializers.CharField(help_text="OAuth 제공자로부터 받은 access_token")


class BaseSocialLoginView(SocialLoginView):
    """
    소셜 로그인 기본 뷰
    - dj-rest-auth의 SocialLoginView를 확장하여 refresh_token을 Cookie로 설정
    - 일반 로그인과 동일한 방식으로 토큰을 관리
    """

    # SessionAuthentication은 CSRF 검증을 수행하므로 제외
    authentication_classes = []

    def get_response(self):
        """응답에 refresh_token Cookie 설정"""
        # 원래 응답 가져오기
        original_response = super().get_response()

        # refresh_token을 Cookie로 설정
        refresh_token = original_response.data.get("refresh") or original_response.data.get("refresh_token")

        if refresh_token:
            cookie_max_age = 7 * 24 * 60 * 60  # 7일 (초 단위)
            cookie_secure = not settings.DEBUG  # 프로덕션에서는 HTTPS만 허용
            cookie_samesite = "Lax"  # CSRF 방지

            original_response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                max_age=cookie_max_age,
                httponly=True,  # JavaScript에서 접근 불가
                secure=cookie_secure,  # HTTPS에서만 전송
                samesite=cookie_samesite,
            )

        return original_response


# 소셜 로그인 뷰 정의
@extend_schema(
    summary="구글 소셜 로그인",
    description="""구글 OAuth2를 통한 소셜 로그인

**사용 방법:**
1. 구글 OAuth2 인증 후 받은 access_token을 전달합니다.
2. access_token만 전달하면 됩니다 (code, id_token 불필요).

**요청 예시:**
```json
{
  "access_token": "ya29.a0AfH6SMC..."
}
```
    """,
    tags=["Social Auth"],
    request=SocialLoginRequestSerializer,
)
class GoogleLogin(BaseSocialLoginView):
    """구글 소셜 로그인"""

    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    callback_url = settings.SOCIAL_LOGIN_REDIRECT_URI


@extend_schema(
    summary="카카오 소셜 로그인",
    description="""카카오 OAuth2를 통한 소셜 로그인

**사용 방법:**
1. 카카오 OAuth2 인증 후 받은 access_token을 전달합니다.
2. access_token만 전달하면 됩니다.

**요청 예시:**
```json
{
  "access_token": "access_token_from_kakao"
}
```
    """,
    tags=["Social Auth"],
    request=SocialLoginRequestSerializer,
)
class KakaoLogin(BaseSocialLoginView):
    """카카오 소셜 로그인"""

    adapter_class = KakaoOAuth2Adapter
    client_class = OAuth2Client
    callback_url = settings.SOCIAL_LOGIN_REDIRECT_URI


@extend_schema(
    summary="네이버 소셜 로그인",
    description="""네이버 OAuth2를 통한 소셜 로그인

**사용 방법:**
1. 네이버 OAuth2 인증 후 받은 access_token을 전달합니다.
2. access_token만 전달하면 됩니다.

**요청 예시:**
```json
{
  "access_token": "access_token_from_naver"
}
```
    """,
    tags=["Social Auth"],
    request=SocialLoginRequestSerializer,
)
class NaverLogin(BaseSocialLoginView):
    """네이버 소셜 로그인"""

    adapter_class = NaverOAuth2Adapter
    client_class = OAuth2Client
    callback_url = settings.SOCIAL_LOGIN_REDIRECT_URI


# DRF의 라우터 생성
router = DefaultRouter()

# ViewSet 등록
router.register(r"products", ProductViewSet, basename="product")
router.register(r"categories", CategoryViewSet, basename="category")
router.register(r"orders", OrderViewSet, basename="order")
router.register(r"cart-items", CartItemViewSet, basename="cart-items")

# 알림 라우터
router.register(r"notifications", NotificationViewSet, basename="notification")

# 내 문의 라우터
router.register(r"my/questions", MyQuestionViewSet, basename="my-question")

# 교환 환불 라우터 (고객용)
router.register(r"returns", ReturnViewSet, basename="return")

# 판매자용 교환 환불 라우터
router.register(r"seller/returns", SellerReturnViewSet, basename="seller-return")

# 상품별 문의 중첩 라우터
# /api/products/{product_pk}/questions/
products_router = routers.NestedSimpleRouter(router, r"products", lookup="product")
products_router.register(r"questions", ProductQuestionViewSet, basename="product-question")


# URL 패턴 정의
urlpatterns = [
    # 웹페이지 URL
    path(
        "payment/test/<int:order_id>/",
        payment_test_page,
        name="payment_test",
    ),
    path("payment/success/", payment_success, name="payment_success"),
    path("payment/fail/", payment_fail, name="payment_fail"),
    # API root - 라우터가 자동으로 생성하는 URL들
    path("", include(router.urls)),
    path("", include(products_router.urls)),  # 중첩 라우터
    # 인증(Auth) 관련 URLs
    # 회원가입 및 로그인
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    # 토큰 관리
    path("auth/token/refresh/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    path("auth/token/verify/", check_token, name="token-verify"),
    # 사용자 관리 (User Management)
    path("users/profile/", ProfileView.as_view(), name="user-profile"),
    path("users/password/change/", PasswordChangeView.as_view(), name="user-password-change"),
    path("users/withdraw/", withdraw, name="user-withdraw"),
    # 비밀번호 재설정
    path(
        "auth/password/reset/request/",
        PasswordResetRequestView.as_view(),
        name="password-reset-request",
    ),
    path(
        "auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    # 이메일 인증
    path(
        "auth/email/send/",
        SendVerificationEmailView.as_view(),
        name="email-verification-send",
    ),
    path(
        "auth/email/verify/",
        VerifyEmailView.as_view(),
        name="email-verification-verify",
    ),
    path(
        "auth/email/resend/",
        ResendVerificationEmailView.as_view(),
        name="email-verification-resend",
    ),
    path(
        "auth/email/status/",
        check_verification_status,
        name="email-verification-status",
    ),
    # Cart 관련 커스텀 URLs (CartViewSet의 actions)
    path("cart/", CartViewSet.as_view({"get": "retrieve"}), name="cart-detail"),
    path("cart/summary/", CartViewSet.as_view({"get": "summary"}), name="cart-summary"),
    path(
        "cart/add_item/",
        CartViewSet.as_view({"post": "add_item"}),
        name="cart-add-item",
    ),
    path("cart/items/", CartViewSet.as_view({"get": "items"}), name="cart-items"),
    path(
        "cart/items/<int:pk>/",
        CartViewSet.as_view({"patch": "update_item", "delete": "delete_item"}),
        name="cart-item-detail",
    ),
    path("cart/clear/", CartViewSet.as_view({"post": "clear"}), name="cart-clear"),
    path(
        "cart/bulk_add/",
        CartViewSet.as_view({"post": "bulk_add"}),
        name="cart-bulk-add",
    ),
    path(
        "cart/check_stock/",
        CartViewSet.as_view({"get": "check_stock"}),
        name="cart-check-stock",
    ),
    path(
        "cart/cleanup/",
        CartViewSet.as_view({"post": "cleanup"}),
        name="cart-cleanup",
    ),
    path(
        "cart/update_prices/",
        CartViewSet.as_view({"post": "update_prices"}),
        name="cart-update-prices",
    ),
    # 결제(Payment) 관련 URLs
    # 결제 요청 및 처리
    path("payments/request/", PaymentRequestView.as_view(), name="payment-request"),
    path("payments/confirm/", PaymentConfirmView.as_view(), name="payment-confirm"),
    path("payments/cancel/", PaymentCancelView.as_view(), name="payment-cancel"),
    path("payments/fail/", PaymentFailView.as_view(), name="payment-fail"),  # API View로 변경
    # 포인트 전액 결제
    path("payments/points-only/", PointsOnlyPaymentView.as_view(), name="payment-points-only"),
    # 재고 사전 검증
    path("payments/verify-stock/", StockVerificationView.as_view(), name="payment-verify-stock"),
    # 결제 조회
    path("payments/", PaymentListView.as_view(), name="payment-list"),
    path("payments/<int:payment_id>/", PaymentDetailView.as_view(), name="payment-detail"),
    path("payments/<int:payment_id>/status/", PaymentStatusView.as_view(), name="payment-status"),
    # 포인트 관련 URL
    path("points/my/", point_views.MyPointView.as_view(), name="my_points"),
    path(
        "points/history/",
        point_views.PointHistoryListView.as_view(),
        name="point_history",
    ),
    path("points/check/", point_views.PointCheckView.as_view(), name="point_check"),
    path("points/use/", point_views.PointUseView.as_view(), name="point_use"),
    path("points/cancel/", point_views.PointCancelView.as_view(), name="point_cancel"),
    path(
        "points/expiring/",
        point_views.ExpiringPointsView.as_view(),
        name="expiring_points",
    ),
    path("points/statistics/", point_views.point_statistics, name="point_statistics"),
    # 웹훅(Webhook) URLs
    path("webhooks/toss/", toss_webhook, name="toss-webhook"),
    # 찜하기(Wishlist) 관련 URLs
    path("wishlist/", WishlistViewSet.as_view({"get": "list"}), name="wishlist-list"),
    path(
        "wishlist/toggle/",
        WishlistViewSet.as_view({"post": "toggle"}),
        name="wishlist-toggle",
    ),
    path("wishlist/add/", WishlistViewSet.as_view({"post": "add"}), name="wishlist-add"),
    path(
        "wishlist/remove/",
        WishlistViewSet.as_view({"delete": "remove"}),
        name="wishlist-remove",
    ),
    path(
        "wishlist/bulk_add/",
        WishlistViewSet.as_view({"post": "bulk_add"}),
        name="wishlist-bulk-add",
    ),
    path(
        "wishlist/clear/",
        WishlistViewSet.as_view({"delete": "clear"}),
        name="wishlist-clear",
    ),
    path(
        "wishlist/check/",
        WishlistViewSet.as_view({"get": "check"}),
        name="wishlist-check",
    ),
    path(
        "wishlist/stats/",
        WishlistViewSet.as_view({"get": "stats"}),
        name="wishlist-stats",
    ),
    path(
        "wishlist/move_to_cart/",
        WishlistViewSet.as_view({"post": "move_to_cart"}),
        name="wishlist-move-to-cart",
    ),
    # 소셜 로그인 엔드포인트 (dj-rest-auth 방식 - access_token 직접 전달용)
    path("auth/social/google/", csrf_exempt(GoogleLogin.as_view()), name="google-login"),
    path("auth/social/kakao/", csrf_exempt(KakaoLogin.as_view()), name="kakao-login"),
    path("auth/social/naver/", csrf_exempt(NaverLogin.as_view()), name="naver-login"),
    # 소셜 로그인 OAuth 콜백 (authorization code 방식)
    path("social/callback/", SocialCallbackView.as_view(), name="social-callback"),
    # 소셜 로그인 테스트 페이지
    path(
        "social/test/",
        TemplateView.as_view(template_name="social_test.html"),
        name="social-test-page",
    ),
]

"""
생성되는 URL 패턴:

결제 처리:
- POST   /api/payments/request/      - 결제 요청 (결제창 열기 전)
- POST   /api/payments/confirm/      - 결제 승인 (결제창 완료 후)
- POST   /api/payments/cancel/       - 결제 취소 (전체 취소)
- POST   /api/payments/fail/         - 결제 실패 콜백

결제 조회:
- GET    /api/payments/              - 내 결제 목록
- GET    /api/payments/{id}/         - 결제 상세 정보

웹훅:
- POST   /api/webhooks/toss/         - 토스페이먼츠 웹훅 수신

회원가입 및 로그인:
- POST   /api/auth/register/         - 회원가입 (새 사용자 생성 + 토큰 발급)
- POST   /api/auth/login/            - 로그인 (인증 + 토큰 발급)
- POST   /api/auth/logout/           - 로그아웃 (토큰 블랙리스트 추가)

토큰 관리:
- POST   /api/auth/token/refresh/    - 토큰 갱신 (Refresh Token으로 새 Access Token 발급)
- GET    /api/auth/token/verify/     - 토큰 유효성 확인

프로필 관리:
- GET    /api/auth/profile/          - 내 프로필 조회
- PUT    /api/auth/profile/          - 프로필 전체 수정
- PATCH  /api/auth/profile/          - 프로필 부분 수정
- POST   /api/auth/password/change/  - 비밀번호 변경

추가 기능:
- POST   /api/auth/email/verify/     - 이메일 인증 요청 (구현 예정)
- POST   /api/auth/withdraw/         - 회원 탈퇴

상품(Product) 관련:
- GET    /api/products/              - 상품 목록
- POST   /api/products/              - 상품 생성
- GET    /api/products/{id}/         - 상품 상세
- PUT    /api/products/{id}/         - 상품 전체 수정
- PATCH  /api/products/{id}/         - 상품 부분 수정
- DELETE /api/products/{id}/         - 상품 삭제
- GET    /api/products/{id}/reviews/ - 상품 리뷰 목록
- POST   /api/products/{id}/reviews/ - 리뷰 작성
- GET    /api/products/popular/      - 인기 상품
- GET    /api/products/best_rating/  - 평점 높은 상품
- GET    /api/products/low_stock/    - 재고 부족 상품

카테고리(Category) 관련:
- GET    /api/categories/            - 카테고리 목록
- GET    /api/categories/{id}/       - 카테고리 상세
- GET    /api/categories/tree/       - 카테고리 트리 구조
- GET    /api/categories/{id}/products/ - 카테고리별 상품

장바구니(Cart) 관련:
- GET    /api/cart/                  - 내 장바구니 전체 조회
- GET    /api/cart/summary/          - 장바구니 요약 정보
- POST   /api/cart/add_item/         - 장바구니에 상품 추가
- GET    /api/cart/items/            - 장바구니 아이템 목록
- PATCH  /api/cart/items/{id}/       - 아이템 수량 변경
- DELETE /api/cart/items/{id}/       - 아이템 삭제
- POST   /api/cart/clear/            - 장바구니 비우기
- POST   /api/cart/bulk_add/         - 여러 상품 한번에 추가
- GET    /api/cart/check_stock/      - 재고 확인

검색 및 필터링 예시:
- /api/products/?search=노트북
- /api/products/?category=1&min_price=10000&max_price=50000
- /api/products/?ordering=-price
- /api/products/?in_stock=true
- /api/products/?seller=3

조회 및 확인:
- GET    /api/wishlist/              - 내 찜 목록 조회
- GET    /api/wishlist/check/        - 특정 상품 찜 상태 확인
- GET    /api/wishlist/stats/        - 찜 목록 통계

상품 추가/제거:
- POST   /api/wishlist/toggle/       - 찜하기 토글 (추가/제거)
- POST   /api/wishlist/add/          - 찜 목록에 추가
- DELETE /api/wishlist/remove/       - 찜 목록에서 제거
- POST   /api/wishlist/bulk_add/     - 여러 상품 한번에 찜하기
- DELETE /api/wishlist/clear/        - 찜 목록 전체 삭제

장바구니 연동:
- POST   /api/wishlist/move_to_cart/ - 찜 목록에서 장바구니로 이동

검색 및 필터링 예시:
- /api/wishlist/?ordering=-created_at      - 최신순 정렬
- /api/wishlist/?ordering=price            - 가격 낮은순
- /api/wishlist/?is_available=true         - 구매 가능한 상품만
- /api/wishlist/?on_sale=true              - 세일 중인 상품만

알림:
- GET    /api/notifications/              - 알림 목록
- GET    /api/notifications/{id}/         - 알림 상세
- GET    /api/notifications/unread/       - 읽지 않은 알림 개수
- POST   /api/notifications/mark_read/    - 알림 읽음 처리
- DELETE /api/notifications/clear/        - 읽은 알림 삭제

상품 문의:
- GET    /api/products/{product_id}/questions/              - 문의 목록
- POST   /api/products/{product_id}/questions/              - 문의 작성
- GET    /api/products/{product_id}/questions/{id}/         - 문의 상세
- PATCH  /api/products/{product_id}/questions/{id}/         - 문의 수정
- DELETE /api/products/{product_id}/questions/{id}/         - 문의 삭제
- POST   /api/products/{product_id}/questions/{id}/answer/  - 답변 작성
- PATCH  /api/products/{product_id}/questions/{id}/answer/  - 답변 수정
- DELETE /api/products/{product_id}/questions/{id}/answer/  - 답변 삭제

내 문의:
- GET    /api/my/questions/               - 내가 작성한 문의 목록
- GET    /api/my/questions/{id}/          - 문의 상세
"""
