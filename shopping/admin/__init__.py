"""
Django Admin configuration for shopping app.

This module re-exports all admin classes from domain-specific modules.
Import pattern: from shopping.admin import ProductAdmin

Admin Site Configuration:
- Site Header: Shopping Mall Admin
- Site Title: Shopping Mall Admin
- Index Title: Shopping Mall Management
"""

from django.contrib import admin

# User domain
from .user_admin import SellerProfileAdmin, UserAdmin

# Product domain
from .product_admin import CategoryAdmin, ProductAdmin, ProductReviewAdmin

# Q&A domain
from .qa_admin import ProductAnswerAdmin, ProductQuestionAdmin

# Order domain
from .order_admin import CartAdmin, OrderAdmin

# Payment domain
from .payment_admin import PaymentAdmin, PaymentLogAdmin

# Point domain
from .point_admin import PointHistoryAdmin

# Return domain
from .return_admin import ReturnAdmin, ReturnItemAdmin

# Notification domain
from .notification_admin import (
    EmailLogAdmin,
    EmailVerificationTokenAdmin,
    NotificationAdmin,
)

# Circuit Breaker domain
from .circuit_breaker_admin import CircuitBreakerStateAdmin

# DLQ (Dead Letter Queue) domain
from .dlq_admin import FailedOperationAdmin

# Postmortem domain
from .postmortem_admin import PostmortemRecordAdmin


__all__ = [
    # User
    "UserAdmin",
    "SellerProfileAdmin",
    # Product
    "ProductAdmin",
    "CategoryAdmin",
    "ProductReviewAdmin",
    # Q&A
    "ProductQuestionAdmin",
    "ProductAnswerAdmin",
    # Order
    "OrderAdmin",
    "CartAdmin",
    # Payment
    "PaymentAdmin",
    "PaymentLogAdmin",
    # Point
    "PointHistoryAdmin",
    # Return
    "ReturnAdmin",
    "ReturnItemAdmin",
    # Notification
    "NotificationAdmin",
    "EmailVerificationTokenAdmin",
    "EmailLogAdmin",
    # Security
    "CircuitBreakerStateAdmin",
    "FailedOperationAdmin",
    # Postmortem
    "PostmortemRecordAdmin",
]


# Admin Site Configuration
admin.site.site_header = "Shopping Mall Admin"
admin.site.site_title = "Shopping Mall Admin"
admin.site.index_title = "Shopping Mall Management"
