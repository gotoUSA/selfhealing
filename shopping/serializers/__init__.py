"""
shopping/serializers/__init__.py

Serializer 모듈의 진입점입니다.
모든 Serializer를 여기서 import하여 다른 모듈에서 쉽게 사용할 수 있도록 합니다.

사용 예시:
    from shopping.serializers import ProductListSerializer, CategorySerializer
    # 또는 전체 import
    from shopping.serializers import *
"""

# Cart 관련 Serializers
from .cart_serializers import (
    CartClearSerializer,
    CartItemCreateSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    SimpleCartSerializer,
)

# Category 관련 Serializers
from .category_serializers import (
    CategoryCreateUpdateSerializer,
    CategorySerializer,
    CategoryTreeSerializer,
    SimpleCategorySerializer,
)

# Email Verification 관련 Serializers
from .email_verification_serializers import (
    ResendVerificationEmailSerializer,
    SendVerificationEmailSerializer,
    VerifyEmailByCodeSerializer,
    VerifyEmailByTokenSerializer,
)

# Notification 관련 Serializers
from .notification_serializers import NotificationMarkReadSerializer, NotificationSerializer

# Order 관련 Serializers
from .order_serializers import OrderCreateSerializer, OrderDetailSerializer, OrderItemSerializer, OrderListSerializer

# Payment 관련 Serializers
from .payment_serializers import (
    PaymentCancelSerializer,
    PaymentConfirmSerializer,
    PaymentLogSerializer,
    PaymentRequestSerializer,
    PaymentSerializer,
    PaymentWebhookSerializer,
)

# Point 관련 Serializers
from .point_serializers import PointHistorySerializer, UserPointSerializer

# Product Q&A 관련 Serializers
from .product_qa_serializers import (
    ProductAnswerCreateSerializer,
    ProductAnswerSerializer,
    ProductAnswerUpdateSerializer,
    ProductQuestionCreateSerializer,
    ProductQuestionDetailSerializer,
    ProductQuestionListSerializer,
    ProductQuestionUpdateSerializer,
)

# Product 관련 Serializers
from .product_serializers import (
    ProductCreateUpdateSerializer,
    ProductDetailSerializer,
    ProductImageSerializer,
    ProductListSerializer,
    ProductReviewSerializer,
)

# Return (교환/환불) 관련 Serializers
from .return_serializers import (
    ReturnApproveSerializer,
    ReturnCompleteSerializer,
    ReturnConfirmReceiveSerializer,
    ReturnCreateSerializer,
    ReturnDetailSerializer,
    ReturnItemSerializer,
    ReturnListSerializer,
    ReturnRejectSerializer,
    ReturnUpdateSerializer,
)

# User 관련 Serializers
from .user_serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    RegisterSerializer,
    TokenResponseSerializer,
    UserSerializer,
)

# Wishlist 관련 Serializers
from .wishlist_serializers import (
    WishlistBulkAddSerializer,
    WishlistProductSerializer,
    WishlistStatsSerializer,
    WishlistStatusSerializer,
    WishlistToggleSerializer,
)

# 외부에서 사용할 수 있도록 __all__ 정의
__all__ = [
    # User
    "UserSerializer",
    "RegisterSerializer",
    "LoginSerializer",
    "PasswordChangeSerializer",
    "TokenResponseSerializer",
    # Email Verification
    "SendVerificationEmailSerializer",
    "VerifyEmailByTokenSerializer",
    "VerifyEmailByCodeSerializer",
    "ResendVerificationEmailSerializer",
    # Product
    "ProductListSerializer",
    "ProductImageSerializer",
    "ProductReviewSerializer",
    "ProductDetailSerializer",
    "ProductCreateUpdateSerializer",
    # Category
    "CategorySerializer",
    "CategoryTreeSerializer",
    "CategoryCreateUpdateSerializer",
    "SimpleCategorySerializer",
    # Cart
    "CartItemSerializer",
    "CartItemCreateSerializer",
    "CartItemUpdateSerializer",
    "CartSerializer",
    "SimpleCartSerializer",
    "CartClearSerializer",
    # Order
    "OrderItemSerializer",
    "OrderListSerializer",
    "OrderDetailSerializer",
    "OrderCreateSerializer",
    # Payment
    "PaymentSerializer",
    "PaymentRequestSerializer",
    "PaymentConfirmSerializer",
    "PaymentCancelSerializer",
    "PaymentLogSerializer",
    "PaymentWebhookSerializer",
    # Wishlist
    "WishlistProductSerializer",
    "WishlistToggleSerializer",
    "WishlistBulkAddSerializer",
    "WishlistStatusSerializer",
    "WishlistStatsSerializer",
    # Point
    "PointHistorySerializer",
    "UserPointSerializer",
    # Notification
    "NotificationSerializer",
    "NotificationMarkReadSerializer",
    # Product Q&A
    "ProductQuestionListSerializer",
    "ProductQuestionDetailSerializer",
    "ProductQuestionCreateSerializer",
    "ProductQuestionUpdateSerializer",
    "ProductAnswerSerializer",
    "ProductAnswerCreateSerializer",
    "ProductAnswerUpdateSerializer",
    # Return
    "ReturnCreateSerializer",
    "ReturnListSerializer",
    "ReturnDetailSerializer",
    "ReturnItemSerializer",
    "ReturnUpdateSerializer",
    "ReturnApproveSerializer",
    "ReturnRejectSerializer",
    "ReturnConfirmReceiveSerializer",
    "ReturnCompleteSerializer",
]
