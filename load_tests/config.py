"""
Locust 부하 테스트 설정

CLI로 override 가능 (Windows는 한 줄 명령어 권장):
    PYTHONUTF8=1 locust -f load_tests/locustfile.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=5m
"""

import os

# =============================================================================
# 서버 설정
# =============================================================================
HOST = os.getenv("LOCUST_HOST", "http://localhost:8000")

# =============================================================================
# 테스트 사용자 설정
# =============================================================================
# 테스트 유저 범위 (load_test_user_0 ~ load_test_user_99)
# 주의: 실제 생성된 유저 수에 맞게 설정해야 함
# 유저 생성: python manage.py create_load_test_users --count=100
TEST_USER_COUNT = 1000
TEST_USER_PREFIX = "load_test_user_"
TEST_USER_PASSWORD = "testpass123"

# Admin user for control API tests (Stage 14, 15)
# Create via: python manage.py createsuperuser --username=admin --email=admin@test.com
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# =============================================================================
# 사용자 행동 비율 (Weight)
# =============================================================================
# 실제 서비스 기준: 방문자 65%, 장바구니 25%, 구매자 10%
USER_WEIGHTS = {
    "browser": 65,  # 조회만 하는 사용자
    "shopper": 25,  # 장바구니까지 담는 사용자
    "buyer": 10,  # 결제까지 완료하는 사용자
}

# =============================================================================
# 대기 시간 설정 (초)
# =============================================================================
# 사용자가 다음 행동까지 대기하는 시간
WAIT_TIME_MIN = 1
WAIT_TIME_MAX = 5

# =============================================================================
# API 엔드포인트
# =============================================================================
ENDPOINTS = {
    # 인증
    "login": "/api/auth/login/",
    "logout": "/api/auth/logout/",
    "token_refresh": "/api/auth/token/refresh/",
    # 상품
    "products": "/api/products/",
    "product_detail": "/api/products/{id}/",
    "categories": "/api/categories/",
    # 장바구니 (새로운 API 구조)
    "cart": "/api/cart/",
    "cart_add_item": "/api/cart/add_item/",
    "cart_items": "/api/cart/items/",
    "cart_item_detail": "/api/cart/items/{id}/",
    "cart_clear": "/api/cart/clear/",
    "cart_summary": "/api/cart/summary/",
    # 레거시 장바구니 (CartItemViewSet - 아직 존재함)
    "cart_items_legacy": "/api/cart-items/",
    # 주문
    "orders": "/api/orders/",
    "order_detail": "/api/orders/{id}/",
    # 결제
    "payment_request": "/api/payments/request/",
    "payment_confirm": "/api/payments/confirm/",
    "payment_cancel": "/api/payments/cancel/",
    "payment_list": "/api/payments/",
    "payment_detail": "/api/payments/{id}/",
}

# =============================================================================
# 성능 목표 (SLA) - 1차 측정 후 조정 예정
# =============================================================================
SLA_TARGETS = {
    "products_list": {
        "p95": 800,  # ms
        "p99": 1500,  # ms
        "error_rate": 0.01,  # 1%
    },
    "product_detail": {
        "p95": 500,
        "p99": 1000,
        "error_rate": 0.01,
    },
    "cart_operations": {
        "p95": 500,
        "p99": 1000,
        "error_rate": 0.01,
    },
    "order_create": {
        "p95": 1000,
        "p99": 2000,
        "error_rate": 0.02,
    },
    "payment": {
        "p95": 300,
        "p99": 500,
        "error_rate": 0.001,  # 0.1% (결제는 더 엄격)
    },
}
