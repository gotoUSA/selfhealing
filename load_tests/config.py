"""
Locust Load Test Configuration

Can be overridden via CLI (single-line command recommended for Windows):
    PYTHONUTF8=1 locust -f load_tests/locustfile.py --host=http://localhost:8000 --users=100 --spawn-rate=10 --run-time=5m
"""

import os

# =============================================================================
# Server Configuration
# =============================================================================
HOST = os.getenv("LOCUST_HOST", "http://localhost:8000")

# =============================================================================
# Test User Configuration
# =============================================================================
# Test user range (load_test_user_0 ~ load_test_user_99)
# Note: Must match the actual number of created users
# Create users: python manage.py create_load_test_users --count=100
TEST_USER_COUNT = 100
TEST_USER_PREFIX = "load_test_user_"
TEST_USER_PASSWORD = "testpass123"

# Admin user for control API tests (Stage 14, 15)
# Create via: python manage.py createsuperuser --username=admin --email=admin@test.com
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# =============================================================================
# User Behavior Weights
# =============================================================================
# Based on real service: Visitors 65%, Shoppers 25%, Buyers 10%
USER_WEIGHTS = {
    "browser": 65,  # Browse-only users
    "shopper": 25,  # Users who add to cart
    "buyer": 10,  # Users who complete payment
}

# =============================================================================
# Wait Time Settings (seconds)
# =============================================================================
# Time user waits between actions
WAIT_TIME_MIN = 1
WAIT_TIME_MAX = 5

# =============================================================================
# API Endpoints
# =============================================================================
ENDPOINTS = {
    # Authentication
    "login": "/api/auth/login/",
    "logout": "/api/auth/logout/",
    "token_refresh": "/api/auth/token/refresh/",
    # Products
    "products": "/api/products/",
    "product_detail": "/api/products/{id}/",
    "categories": "/api/categories/",
    # Cart (new API structure)
    "cart": "/api/cart/",
    "cart_add_item": "/api/cart/add_item/",
    "cart_items": "/api/cart/items/",
    "cart_item_detail": "/api/cart/items/{id}/",
    "cart_clear": "/api/cart/clear/",
    "cart_summary": "/api/cart/summary/",
    # Legacy cart (CartItemViewSet - still exists)
    "cart_items_legacy": "/api/cart-items/",
    # Orders
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
# Performance Targets (SLA) - To be adjusted after initial measurement
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
        "error_rate": 0.001,  # 0.1% (stricter for payments)
    },
}
