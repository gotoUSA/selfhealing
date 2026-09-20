"""
Tests for Admin module imports and structure.

Verifies that:
- All admin modules load correctly
- No circular import issues
- Admin site is properly configured
"""

import pytest
from django.contrib import admin


@pytest.mark.django_db
class TestAdminModuleStructure:
    """Tests for admin module structure and imports."""

    def test_admin_package_imports_all_classes(self):
        """Verify all admin classes are exported from the package."""
        from shopping.admin import (
            CartAdmin,
            CategoryAdmin,
            EmailLogAdmin,
            EmailVerificationTokenAdmin,
            NotificationAdmin,
            OrderAdmin,
            PaymentAdmin,
            PaymentLogAdmin,
            PointHistoryAdmin,
            ProductAdmin,
            ProductAnswerAdmin,
            ProductQuestionAdmin,
            ProductReviewAdmin,
            ReturnAdmin,
            ReturnItemAdmin,
            SellerProfileAdmin,
            UserAdmin,
        )

        # Verify all classes are ModelAdmin subclasses
        admin_classes = [
            CartAdmin,
            CategoryAdmin,
            EmailLogAdmin,
            EmailVerificationTokenAdmin,
            NotificationAdmin,
            OrderAdmin,
            PaymentAdmin,
            PaymentLogAdmin,
            PointHistoryAdmin,
            ProductAdmin,
            ProductAnswerAdmin,
            ProductQuestionAdmin,
            ProductReviewAdmin,
            ReturnAdmin,
            ReturnItemAdmin,
            SellerProfileAdmin,
            UserAdmin,
        ]

        for cls in admin_classes:
            assert issubclass(cls, admin.ModelAdmin) or hasattr(cls, "model")

    def test_all_exports_are_in_dunder_all(self):
        """Verify __all__ contains all exported classes."""
        from shopping import admin as admin_module

        expected_exports = {
            "UserAdmin",
            "SellerProfileAdmin",
            "ProductAdmin",
            "CategoryAdmin",
            "ProductReviewAdmin",
            "ProductQuestionAdmin",
            "ProductAnswerAdmin",
            "OrderAdmin",
            "CartAdmin",
            "PaymentAdmin",
            "PaymentLogAdmin",
            "PointHistoryAdmin",
            "ReturnAdmin",
            "ReturnItemAdmin",
            "NotificationAdmin",
            "EmailVerificationTokenAdmin",
            "EmailLogAdmin",
        }

        assert set(admin_module.__all__) == expected_exports

    def test_admin_site_configuration(self):
        """Verify admin site is properly configured."""
        assert admin.site.site_header == "Shopping Mall Admin"
        assert admin.site.site_title == "Shopping Mall Admin"
        assert admin.site.index_title == "Shopping Mall Management"

    def test_no_circular_imports_in_domain_modules(self):
        """Verify each domain module can be imported independently."""
        modules = [
            "shopping.admin.base",
            "shopping.admin.user_admin",
            "shopping.admin.product_admin",
            "shopping.admin.qa_admin",
            "shopping.admin.order_admin",
            "shopping.admin.payment_admin",
            "shopping.admin.point_admin",
            "shopping.admin.return_admin",
            "shopping.admin.notification_admin",
        ]

        for mod_name in modules:
            try:
                __import__(mod_name)
            except ImportError as e:
                pytest.fail(f"Failed to import {mod_name}: {e}")


@pytest.mark.django_db
class TestAdminRegistrations:
    """Tests for admin model registrations."""

    def test_all_models_registered(self):
        """Verify all expected models are registered with admin site."""
        from shopping.models import (
            Cart,
            Category,
            EmailLog,
            EmailVerificationToken,
            Notification,
            Order,
            Payment,
            PaymentLog,
            PointHistory,
            Product,
            ProductAnswer,
            ProductQuestion,
            ProductReview,
            Return,
            ReturnItem,
            User,
        )
        from shopping.models import SellerProfile

        models_to_check = [
            Cart,
            Category,
            EmailLog,
            EmailVerificationToken,
            Notification,
            Order,
            Payment,
            PaymentLog,
            PointHistory,
            Product,
            ProductAnswer,
            ProductQuestion,
            ProductReview,
            Return,
            ReturnItem,
            SellerProfile,
            User,
        ]

        for model in models_to_check:
            assert admin.site.is_registered(model), f"{model.__name__} is not registered with admin"
