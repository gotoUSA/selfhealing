# Django Admin Module Separation & Refactoring Plan

**Version:** 1.1
**Created:** 2025-12-08
**Last Updated:** 2025-12-08
**Status:** Planning
**Author:** Development Team

---

## 0. Mission Statement

> **The purpose of this refactoring is NOT merely file separation.**
>
> It is to **control risks arising from operator actions**, ensure **domain isolation**, and implement **state-based verification** so that the Admin UI does not compromise system integrity.

This refactoring addresses:
- **Operational Risk Reduction** - Prevent accidental cross-domain side effects
- **Auditability** - Track who did what, when, and why
- **Domain Integrity** - Ensure Admin actions respect domain boundaries
- **State Machine Safety** - Prevent invalid state transitions through UI

---

## 1. Executive Summary

This document outlines the plan to refactor the monolithic `shopping/admin.py` file (~1,900 lines, 22 Admin classes) into a domain-based modular structure to improve maintainability, testability, and team collaboration.

### Current State
| Metric | Value |
|--------|-------|
| Total Lines | 1,893 |
| Admin Classes | 22 |
| Inline Classes | 8 |
| Domains | ~10 |

### Target State
| Metric | Value |
|--------|-------|
| Files | 8-10 domain files |
| Lines per File | 150-300 |
| Test Coverage | Increased by domain isolation |

---

## 2. Problem Statement

### 2.1 Current Issues

| Issue | Impact | Risk Level |
|-------|--------|------------|
| Single file with 1,900+ lines | High cognitive load, difficult navigation | 🔴 High |
| Unrelated domains mixed together | Accidental side-effects between domains | 🔴 High |
| Large PR diffs | Code review bottlenecks | 🟡 Medium |
| Difficult to test | Low test coverage, QA delays | 🔴 High |
| Git blame tracking issues | Operational debugging challenges | 🟡 Medium |
| Architecture inconsistency | Violates domain-based structure used elsewhere | 🟡 Medium |

### 2.2 Risk Analysis

> **Critical Risk:** Payment and User Admin classes in the same file can inadvertently affect each other during modifications. This poses significant operational risk in production.

---

## 3. Goals & Success Criteria

### 3.1 Primary Goals

1. **Domain Separation** - Split admin into domain-specific modules
2. **Maintainability** - Each file under 300 lines
3. **Testability** - Enable isolated unit testing per domain
4. **Backward Compatibility** - Existing imports continue to work
5. **Architecture Alignment** - Match existing `services/` and `views/` structure

### 3.2 Technical Success Criteria

| Criteria | Measurement |
|----------|-------------|
| All admin files < 300 lines | `wc -l` on each file |
| Zero import errors | `python manage.py check` passes |
| All existing tests pass | CI pipeline green |
| Admin UI functional | Manual verification |
| No circular imports | Static analysis clean |

### 3.3 Business Success Criteria

| Criteria | Measurement | Target |
|----------|-------------|--------|
| PR Review Time Reduction | Average review time per admin-related PR | 30% decrease |
| Developer Onboarding Speed | Time to understand admin structure | 50% decrease |
| Hotfix Deployment Time | Average time from bug report to fix | 40% decrease |
| Incident Recovery Time | MTTR for admin-related incidents | 50% decrease |
| Operator Error Rate | Number of admin-caused data issues | 60% decrease |

**Measurement Period:** Compare 4 weeks before vs 4 weeks after deployment.

**Baseline Data Collection:**
- Record current PR review times for admin changes
- Document current onboarding training duration
- Track hotfix deployment times
- Log all admin-related incidents

### 3.4 Guiding Principle

> **"Admin is a Presentation Layer and must not contain Domain Logic."**
>
> All business logic must be delegated to the Service Layer.

---

## 4. Target Architecture

### 4.1 Directory Structure

```
shopping/
├── admin.py                    # DEPRECATED - will be removed after migration
└── admin/
    ├── __init__.py             # Re-exports all admin classes
    ├── base.py                 # Common mixins and utilities (STRICT SCOPE)
    ├── user_admin.py           # User, SellerProfile
    ├── product_admin.py        # Product, Category, ProductImage, ProductReview
    ├── order_admin.py          # Order, OrderItem, Cart, CartItem
    ├── payment_admin.py        # Payment, PaymentLog
    ├── point_admin.py          # PointHistory
    ├── notification_admin.py   # Notification, EmailVerificationToken, EmailLog
    ├── qa_admin.py             # ProductQuestion, ProductAnswer
    ├── return_admin.py         # Return, ReturnItem
    └── security_admin.py       # CircuitBreakerState, FailedOperation
```

### 4.2 Domain Mapping

| Domain | Models | Estimated Lines |
|--------|--------|-----------------|
| **user** | User, SellerProfile, SellerProfileInline | ~180 |
| **product** | Product, Category, ProductImage, ProductReview, ProductImageInline, ProductReviewInline | ~200 |
| **order** | Order, OrderItem, Cart, CartItem, OrderItemInline, CartItemInline | ~200 |
| **payment** | Payment, PaymentLog | ~250 |
| **point** | PointHistory | ~100 |
| **notification** | Notification, EmailVerificationToken, EmailLog | ~180 |
| **qa** | ProductQuestion, ProductAnswer, ProductAnswerInline | ~120 |
| **return** | Return, ReturnItem, ReturnItemInline | ~280 |
| **security** | CircuitBreakerState, FailedOperation | ~280 |

**Total: ~1,790 lines distributed across 9 files**

---

## 5. Implementation Plan

### 5.1 Phase Overview

```
Phase 1: Setup & File Creation     (Day 1)
Phase 2: Code Migration            (Day 1-2)
Phase 3: Import Verification       (Day 2)
Phase 4: Testing & Validation      (Day 2-3)
Phase 5: Cleanup & Documentation   (Day 3)
```

### 5.2 Detailed Steps

#### Phase 1: Setup & File Creation

| Step | Task | Owner | Status |
|------|------|-------|--------|
| 1.1 | Create `shopping/admin/` directory | - | ⬜ |
| 1.2 | Create `__init__.py` with placeholder | - | ⬜ |
| 1.3 | Create empty domain files | - | ⬜ |

#### Phase 2: Code Migration (Dependency-Ordered)

> ⚠️ **CRITICAL:** Migration order is based on dependency graph, NOT alphabetical order.
> Migrating in wrong order will cause import errors.

**Dependency Graph:**
```
Base (Mixins/Utils)
    ↓
User (independent)
    ↓
Product ←── QA (Product-only dependency)
    ↓
Order (depends on Product, User)
    ↓
Payment (depends on Order)
    ↓
Point (depends on Order, Payment)
    ↓
Return (depends on Order, Payment)
    ↓
Notification (mostly independent)
    ↓
Security (depends on Payment, Order - failure logging)
```

| Priority | Step | Task | Dependencies | Reason | Status |
|----------|------|------|--------------|--------|--------|
| 1 | 2.1 | Create `base.py` | None | Foundation for all mixins | ⬜ |
| 2 | 2.2 | Migrate `user_admin.py` | base.py | Most independent domain | ⬜ |
| 3 | 2.3 | Migrate `product_admin.py` | base.py | Referenced by Order/Return | ⬜ |
| 4 | 2.4 | Migrate `qa_admin.py` | product | Product-only dependency | ⬜ |
| 5 | 2.5 | Migrate `order_admin.py` | product, user | Core transaction domain | ⬜ |
| 6 | 2.6 | Migrate `payment_admin.py` | order | Order-dependent | ⬜ |
| 7 | 2.7 | Migrate `point_admin.py` | order, payment | Order+Payment dependent | ⬜ |
| 8 | 2.8 | Migrate `return_admin.py` | order, payment | Order+Payment dependent | ⬜ |
| 9 | 2.9 | Migrate `notification_admin.py` | base.py | Mostly independent | ⬜ |
| 10 | 2.10 | Migrate `security_admin.py` | order, payment | Failure logging for all | ⬜ |

**Validation after each step:**
```bash
python manage.py check
python manage.py shell -c "from shopping.admin import *"
```

#### Phase 3: Import Verification

| Step | Task | Status |
|------|------|--------|
| 3.1 | Update `__init__.py` with all exports | ⬜ |
| 3.2 | Run `python manage.py check` | ⬜ |
| 3.3 | Verify admin site loads correctly | ⬜ |
| 3.4 | Check for circular import issues | ⬜ |

#### Phase 4: Testing & Validation

| Step | Task | Status |
|------|------|--------|
| 4.1 | Run existing test suite | ⬜ |
| 4.2 | Manual admin UI verification | ⬜ |
| 4.3 | Verify all CRUD operations | ⬜ |
| 4.4 | Test admin actions | ⬜ |

#### Phase 5: Cleanup & Documentation

| Step | Task | Status |
|------|------|--------|
| 5.1 | Remove old `admin.py` file | ⬜ |
| 5.2 | Update any external imports | ⬜ |
| 5.3 | Update project documentation | ⬜ |

---

## 6. Technical Specifications

### 6.1 `__init__.py` Pattern

```python
# shopping/admin/__init__.py
"""
Django Admin configuration for shopping app.

This module re-exports all admin classes from domain-specific modules.
Import pattern: from shopping.admin import ProductAdmin
"""

from .user_admin import UserAdmin, SellerProfileAdmin
from .product_admin import ProductAdmin, CategoryAdmin, ProductReviewAdmin
from .order_admin import OrderAdmin, CartAdmin
from .payment_admin import PaymentAdmin, PaymentLogAdmin
from .point_admin import PointHistoryAdmin
from .notification_admin import (
    NotificationAdmin,
    EmailVerificationTokenAdmin,
    EmailLogAdmin,
)
from .qa_admin import ProductQuestionAdmin, ProductAnswerAdmin
from .return_admin import ReturnAdmin, ReturnItemAdmin
from .security_admin import CircuitBreakerStateAdmin, FailedOperationAdmin

__all__ = [
    # User
    "UserAdmin",
    "SellerProfileAdmin",
    # Product
    "ProductAdmin",
    "CategoryAdmin",
    "ProductReviewAdmin",
    # Order
    "OrderAdmin",
    "CartAdmin",
    # Payment
    "PaymentAdmin",
    "PaymentLogAdmin",
    # Point
    "PointHistoryAdmin",
    # Notification
    "NotificationAdmin",
    "EmailVerificationTokenAdmin",
    "EmailLogAdmin",
    # QA
    "ProductQuestionAdmin",
    "ProductAnswerAdmin",
    # Return
    "ReturnAdmin",
    "ReturnItemAdmin",
    # Security
    "CircuitBreakerStateAdmin",
    "FailedOperationAdmin",
]
```

### 6.2 Domain File Template

```python
# shopping/admin/{domain}_admin.py
"""
Admin configuration for {Domain} domain.

Models: Model1, Model2
"""

from django.contrib import admin
from django.utils.html import format_html

from shopping.models import Model1, Model2


# ============================================
# Inline Classes
# ============================================

class Model2Inline(admin.TabularInline):
    """Inline for Model2 within Model1 admin."""
    model = Model2
    extra = 0


# ============================================
# Admin Classes
# ============================================

@admin.register(Model1)
class Model1Admin(admin.ModelAdmin):
    """Admin for Model1."""

    list_display = ["field1", "field2"]
    list_filter = ["field1"]
    search_fields = ["field1", "field2"]

    inlines = [Model2Inline]


@admin.register(Model2)
class Model2Admin(admin.ModelAdmin):
    """Admin for Model2."""

    list_display = ["field1", "field2"]
```

### 6.3 Base Module Responsibility Definition

> ⚠️ **WARNING:** Without strict boundaries, `base.py` will become another monolithic file.

#### ALLOWED in `base.py` ✅

| Category | Examples |
|----------|----------|
| Display Formatting | `format_currency()`, `format_datetime()`, `truncate_text()` |
| Readonly/Masking | `mask_email()`, `mask_phone()`, `mask_card_number()` |
| Common Search Fields | `BaseSearchMixin` with standard search patterns |
| Permission Guards | `ReadOnlyAdminMixin`, `StaffOnlyMixin` |
| UI Helpers | `colored_status()`, `boolean_icon()` |
| Logging Utilities | `AdminActionLogMixin` (see Section 14) |

#### FORBIDDEN in `base.py` ❌

| Category | Reason | Correct Location |
|----------|--------|------------------|
| Validation Logic | Business rule | `services/` |
| Business Rules | Domain logic | `services/` |
| Database Writes | Side effects | `services/` |
| External API Calls | I/O operations | `services/` or `tasks/` |
| State Transitions | Domain logic | `services/` |
| Domain-specific Code | Coupling | Respective `*_admin.py` |

#### Example `base.py` Implementation

```python
# shopping/admin/base.py
"""
Common admin mixins and utilities.

STRICT SCOPE: Display formatting and readonly operations ONLY.
NO business logic, NO database writes, NO external calls.
"""

from django.contrib import admin
from django.utils.html import format_html


class DisplayFormattingMixin:
    """Common display formatting methods."""

    @staticmethod
    def format_currency(amount):
        """Format amount as Korean Won."""
        if amount is None:
            return "-"
        return f"₩{amount:,.0f}"

    @staticmethod
    def format_status_badge(status, color_map=None):
        """Render status as colored badge."""
        default_colors = {
            "SUCCESS": "green",
            "FAILED": "red",
            "PENDING": "orange",
        }
        colors = color_map or default_colors
        color = colors.get(status, "gray")
        return format_html(
            '<span style="background:{}; padding:2px 8px; border-radius:4px; color:white;">{}</span>',
            color, status
        )


class ReadOnlyAdminMixin:
    """Mixin for read-only admin views."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MaskingMixin:
    """Mixin for masking sensitive data."""

    @staticmethod
    def mask_email(email):
        """Mask email: t***@example.com"""
        if not email or "@" not in email:
            return email
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}"

    @staticmethod
    def mask_phone(phone):
        """Mask phone: 010-****-5678"""
        if not phone or len(phone) < 8:
            return phone
        return f"{phone[:3]}-****-{phone[-4:]}"
```

### 6.4 Import Guidelines

#### DO ✅
```python
# Explicit imports from models
from shopping.models import User, Product, Order

# Service layer calls in actions
from shopping.services.payment_service import PaymentService

# Relative imports within admin package
from .base import AdminMixin
```

#### DON'T ❌
```python
# Cross-domain admin imports (causes circular imports)
from .order_admin import OrderAdmin  # Inside payment_admin.py

# Business logic inside admin
def approve_payment(self, request, queryset):
    for payment in queryset:
        payment.status = "APPROVED"  # ❌ Direct DB modification
        payment.save()
```

---

## 7. Admin Action Separation Guidelines

### 7.1 Responsibility Matrix

| Responsibility | Location | Example |
|----------------|----------|---------|
| Button/Action placement | `admin.py` | `@admin.action()` |
| Display formatting | `admin.py` | `list_display`, `format_html()` |
| Validation logic | `services/` | `PaymentService.validate()` |
| QuerySet filtering | `admin.py` | `get_queryset()` |
| Side-effect operations | `services/` | `PaymentService.process()` |
| Async processing | `tasks/` | Celery tasks |
| Complex business logic | `services/` | Domain-specific service classes |

### 7.2 Before & After Example

#### ❌ Before: Business Logic in Admin

```python
# shopping/admin.py
@admin.action(description="Retry failed payments")
def retry_payment(modeladmin, request, queryset):
    for payment in queryset:
        if payment.status == "FAILED":
            payment.status = "RETRYING"
            payment.retry_count += 1
            payment.last_retry_at = timezone.now()
            payment.save()

            # Call external API
            result = TossPaymentGateway.retry(payment.payment_key)
            if result.success:
                payment.status = "SUCCESS"
            else:
                payment.status = "FAILED"
            payment.save()
```

#### ✅ After: Service Layer Delegation

```python
# shopping/services/payment_recovery_service.py
class PaymentRecoveryService:
    """Service for handling payment recovery operations."""

    @classmethod
    def retry(cls, payment: Payment) -> PaymentResult:
        """
        Retry a failed payment.

        Args:
            payment: Payment instance to retry

        Returns:
            PaymentResult with success status and message
        """
        if payment.status != "FAILED":
            return PaymentResult(success=False, message="Payment is not in failed state")

        payment.status = "RETRYING"
        payment.retry_count += 1
        payment.last_retry_at = timezone.now()
        payment.save()

        result = TossPaymentGateway.retry(payment.payment_key)
        payment.status = "SUCCESS" if result.success else "FAILED"
        payment.save()

        return PaymentResult(success=result.success, message=result.message)


# shopping/admin/payment_admin.py
from shopping.services.payment_recovery_service import PaymentRecoveryService

@admin.action(description="Retry failed payments")
def retry_payment(modeladmin, request, queryset):
    success_count = 0
    for payment in queryset:
        result = PaymentRecoveryService.retry(payment)
        if result.success:
            success_count += 1

    modeladmin.message_user(
        request,
        f"Successfully retried {success_count}/{queryset.count()} payments."
    )
```

---

## 8. Testing Strategy

### 8.1 Test File Structure

```
shopping/
└── tests/
    └── admin/
        ├── __init__.py
        ├── test_user_admin.py
        ├── test_product_admin.py
        ├── test_order_admin.py
        ├── test_payment_admin.py
        └── ...
```

### 8.2 Test Categories

#### Basic UI Tests

| Category | Description | Example |
|----------|-------------|---------|
| Permission Tests | Verify access control | `test_staff_can_access_admin` |
| List Display Tests | Verify list view renders | `test_product_list_display` |
| Search/Filter Tests | Verify filtering works | `test_product_search` |
| Action Tests | Verify admin actions | `test_retry_payment_action` |
| Inline Tests | Verify inlines render | `test_order_item_inline` |

#### State & Concurrency Tests (CRITICAL)

> ⚠️ **Admin actions are state machines.** UI loading tests are NOT sufficient.
> We must verify that **Domain Rules are not violated through the Admin layer.**

| Category | Description | Example |
|----------|-------------|---------|
| Idempotency Tests | Same action twice = same result | `test_retry_payment_idempotent` |
| Race Condition Tests | Inline save + action simultaneously | `test_concurrent_order_update` |
| Invalid State Transition | Prevent illegal state changes | `test_paid_to_pending_blocked` |
| Permission Boundary | Role-based action restrictions | `test_staff_cannot_refund` |
| Bulk Action Safety | Large batch operations | `test_bulk_cancel_100_orders` |

### 8.3 Basic Test Template

```python
# shopping/tests/admin/test_payment_admin.py
import pytest
from django.urls import reverse
from unittest.mock import patch

from shopping.models import Payment


@pytest.mark.django_db
class TestPaymentAdmin:
    """Tests for PaymentAdmin."""

    def test_payment_list_view(self, admin_client):
        """Test that payment list view loads successfully."""
        url = reverse("admin:shopping_payment_changelist")
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_payment_search(self, admin_client, payment_factory):
        """Test payment search functionality."""
        payment = payment_factory(order_id="TEST-123")
        url = reverse("admin:shopping_payment_changelist")
        response = admin_client.get(url, {"q": "TEST-123"})
        assert response.status_code == 200
        assert payment in response.context["cl"].queryset

    @patch("shopping.services.payment_recovery_service.PaymentRecoveryService.retry")
    def test_retry_payment_action(self, mock_retry, admin_client, failed_payment):
        """Test retry payment action calls service layer."""
        mock_retry.return_value = PaymentResult(success=True)

        url = reverse("admin:shopping_payment_changelist")
        response = admin_client.post(url, {
            "action": "retry_payment",
            "_selected_action": [failed_payment.pk],
        })

        assert mock_retry.called
        assert mock_retry.call_args[0][0] == failed_payment
```

### 8.4 State & Concurrency Test Templates

```python
# shopping/tests/admin/test_payment_admin_state.py
import pytest
from django.urls import reverse
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from shopping.models import Payment


@pytest.mark.django_db
class TestPaymentAdminStateTransitions:
    """Tests for payment state machine integrity in admin."""

    def test_retry_payment_idempotent(self, admin_client, failed_payment):
        """
        Idempotency Test: Clicking retry twice should not cause issues.

        Expected: Second retry is no-op or returns same result.
        """
        url = reverse("admin:shopping_payment_changelist")

        # First retry
        response1 = admin_client.post(url, {
            "action": "retry_payment",
            "_selected_action": [failed_payment.pk],
        })

        # Second retry (should be safe)
        response2 = admin_client.post(url, {
            "action": "retry_payment",
            "_selected_action": [failed_payment.pk],
        })

        # Verify no duplicate processing
        failed_payment.refresh_from_db()
        assert failed_payment.retry_count <= 1  # Not incremented twice

    def test_paid_to_pending_transition_blocked(self, admin_client, paid_payment):
        """
        Invalid State Transition: PAID -> PENDING should be blocked.

        This tests that domain rules are enforced at admin layer.
        """
        url = reverse("admin:shopping_payment_change", args=[paid_payment.pk])

        response = admin_client.post(url, {
            "status": "PENDING",  # Attempting invalid transition
            # ... other required fields
        })

        paid_payment.refresh_from_db()
        assert paid_payment.status == "PAID"  # Should remain unchanged

    def test_staff_cannot_execute_refund(self, staff_client, paid_payment):
        """
        Permission Boundary: Staff role cannot execute refund action.

        Only superuser or specific permission should allow refunds.
        """
        url = reverse("admin:shopping_payment_changelist")

        response = staff_client.post(url, {
            "action": "process_refund",
            "_selected_action": [paid_payment.pk],
        })

        # Should be forbidden or action not available
        paid_payment.refresh_from_db()
        assert paid_payment.status != "REFUNDED"


@pytest.mark.django_db
class TestPaymentAdminConcurrency:
    """Tests for race condition handling in admin."""

    def test_concurrent_inline_and_action(self, admin_client, order_with_items):
        """
        Race Condition: Inline save and action executed simultaneously.

        Simulates operator clicking save while action is processing.
        """
        def save_inline():
            # Simulate inline edit
            pass

        def execute_action():
            # Simulate action
            pass

        with ThreadPoolExecutor(max_workers=2) as executor:
            future1 = executor.submit(save_inline)
            future2 = executor.submit(execute_action)

            # Both should complete without data corruption
            future1.result()
            future2.result()

        # Verify data integrity
        order_with_items.refresh_from_db()
        # Add specific assertions based on expected behavior

    def test_bulk_cancel_large_batch(self, admin_client, order_factory):
        """
        Bulk Action Safety: Cancel 100 orders at once.

        Verifies batch operations don't timeout or cause partial updates.
        """
        orders = [order_factory() for _ in range(100)]
        order_ids = [o.pk for o in orders]

        url = reverse("admin:shopping_order_changelist")
        response = admin_client.post(url, {
            "action": "cancel_orders",
            "_selected_action": order_ids,
        })

        # All or nothing - no partial cancellation
        cancelled_count = Order.objects.filter(
            pk__in=order_ids,
            status="CANCELLED"
        ).count()

        assert cancelled_count in [0, 100]  # Not partial
```

---

## 9. Rollout Plan

### 9.1 Git Strategy

```bash
# Create feature branch
git checkout -b refactor/admin-domain-split

# Commit pattern
git commit -m "refactor(admin): create admin package structure"
git commit -m "refactor(admin): migrate user admin to separate module"
git commit -m "refactor(admin): migrate product admin to separate module"
# ... repeat for each domain

# Final cleanup
git commit -m "refactor(admin): remove deprecated monolithic admin.py"
```

### 9.2 Rollback Plan

If issues are discovered post-deployment:

#### Coarse-Grained Rollback (Full Revert)

1. **Immediate:** Revert merge commit
2. **Short-term:** Keep old `admin.py` as `admin.py.bak` for 1 sprint
3. **Verification:** Run full regression before removing backup

#### Fine-Grained Rollback (Domain-Specific)

> ⚠️ **Preferred approach** - Allows surgical fixes without affecting stable domains.

| Strategy | Description | Use Case |
|----------|-------------|----------|
| **Domain-level revert** | Revert specific domain commit only | PaymentAdmin broken, but OrderAdmin OK |
| **Feature flag** | `ADMIN_USE_NEW_PAYMENT=False` | Gradual rollout with instant rollback |
| **Shadow admin** | Run old & new admin in parallel | A/B testing admin changes |

#### Commit Strategy for Granular Rollback

```bash
# Each domain = separate commit for easy revert
git commit -m "refactor(admin): migrate user_admin"
git commit -m "refactor(admin): migrate product_admin"
git commit -m "refactor(admin): migrate order_admin"
# If order_admin has issues:
git revert <order_admin_commit_hash>
```

#### Feature Flag Implementation

```python
# settings.py
ADMIN_FLAGS = {
    "USE_NEW_USER_ADMIN": True,
    "USE_NEW_PRODUCT_ADMIN": True,
    "USE_NEW_ORDER_ADMIN": False,  # Rollback: set to False
    "USE_NEW_PAYMENT_ADMIN": True,
}

# shopping/admin/__init__.py
from django.conf import settings

if settings.ADMIN_FLAGS.get("USE_NEW_PAYMENT_ADMIN", True):
    from .payment_admin import PaymentAdmin, PaymentLogAdmin
else:
    from .legacy.payment_admin import PaymentAdmin, PaymentLogAdmin
```

#### Shadow Admin (Parallel Operation)

For critical domains (Payment, Order), consider running both versions:

```python
# New admin at: /admin/shopping/payment/
# Shadow admin at: /admin-staging/shopping/payment/

# urls.py
from django.contrib import admin
from shopping.admin.staging import staging_admin_site

urlpatterns = [
    path("admin/", admin.site.urls),
    path("admin-staging/", staging_admin_site.urls),  # New version for testing
]
```

---

## 10. Risk Mitigation

| Risk | Mitigation | Contingency |
|------|------------|-------------|
| Circular imports | Follow import guidelines strictly | Use lazy imports |
| Missing admin registrations | Verify all models appear in admin | Check `__all__` exports |
| Broken inline relationships | Test each inline manually | Rollback specific file |
| Performance regression | Monitor admin page load times | Optimize querysets |
| External import breakage | Search codebase for admin imports | Update import paths |

---

## 10.5 Monitoring & Alerting

After deployment, monitor these metrics:

| Metric | Tool | Alert Threshold |
|--------|------|----------------|
| Admin page load time | APM (Datadog/NewRelic) | > 3s |
| Admin error rate | Error tracking (Sentry) | > 1% |
| Import errors | Django check | Any |
| DB query count per page | Django Debug Toolbar | > 50 queries |

---

## 11. Checklist

### Pre-Implementation
- [ ] Backup current `admin.py`
- [ ] Create feature branch
- [ ] Notify team of planned changes

### Implementation
- [ ] Create `admin/` directory structure
- [ ] Migrate each domain module
- [ ] Update `__init__.py` exports
- [ ] Run Django system check

### Validation
- [ ] All existing tests pass
- [ ] Manual admin UI verification
- [ ] No circular import errors
- [ ] All CRUD operations work

### Post-Implementation
- [ ] Remove old `admin.py`
- [ ] Update documentation
- [ ] Merge to main branch
- [ ] Monitor production for issues

---

## 12. Admin Operator Action Logging

> **Core Principle:** Admin refactoring's primary goal is reducing **operator error risk**.
> Without action logging, we cannot audit, investigate incidents, or measure improvement.

### 12.1 Why Logging is Critical

| Scenario | Without Logging | With Logging |
|----------|-----------------|-------------|
| Wrong refund issued | "Who did this?" - Unknown | User X at 14:32 via admin |
| Order cancelled incorrectly | No audit trail | Full action history |
| Payment retry loop | Can't investigate | See retry attempts + timing |
| Data corruption | Blame game | Clear responsibility |

### 12.2 Required Log Fields

| Field | Description | Example |
|-------|-------------|--------|
| `timestamp` | When action occurred | `2025-12-08T14:32:15Z` |
| `user` | Who performed action | `admin_user_123` |
| `action` | What action was taken | `retry_payment` |
| `model` | Target model | `Payment` |
| `object_id` | Target object ID | `pay_abc123` |
| `old_values` | State before action | `{"status": "FAILED"}` |
| `new_values` | State after action | `{"status": "RETRYING"}` |
| `ip_address` | Operator's IP | `192.168.1.100` |
| `user_agent` | Browser info | `Chrome/120.0` |

### 12.3 Actions to Log

| Category | Actions |
|----------|--------|
| **Payment** | Retry, Refund, Cancel, Manual Approve |
| **Order** | Cancel, Force Complete, Change Status |
| **Return** | Approve, Reject, Force Refund |
| **User** | Deactivate, Change Role, Reset Password |
| **Product** | Deactivate, Price Change, Stock Adjustment |

### 12.4 Implementation with Django Admin LogEntry

```python
# shopping/admin/base.py
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
import json


class AdminActionLogMixin:
    """Mixin to log admin actions with before/after values."""

    def log_action_with_details(
        self,
        request,
        obj,
        action_name: str,
        old_values: dict,
        new_values: dict
    ):
        """
        Log admin action with detailed before/after state.

        Args:
            request: HTTP request
            obj: Model instance
            action_name: Name of the action performed
            old_values: State before action
            new_values: State after action
        """
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(obj).pk,
            object_id=obj.pk,
            object_repr=str(obj),
            action_flag=CHANGE,
            change_message=json.dumps({
                "action": action_name,
                "old_values": old_values,
                "new_values": new_values,
                "ip_address": self._get_client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            })
        )

    def _get_client_ip(self, request):
        """Extract client IP from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0]
        return request.META.get("REMOTE_ADDR")


# shopping/admin/payment_admin.py
from .base import AdminActionLogMixin

@admin.register(Payment)
class PaymentAdmin(AdminActionLogMixin, admin.ModelAdmin):
    actions = ["retry_payment"]

    @admin.action(description="Retry failed payments")
    def retry_payment(self, request, queryset):
        for payment in queryset:
            old_values = {
                "status": payment.status,
                "retry_count": payment.retry_count,
            }

            result = PaymentRecoveryService.retry(payment)

            payment.refresh_from_db()
            new_values = {
                "status": payment.status,
                "retry_count": payment.retry_count,
            }

            # Log with before/after state
            self.log_action_with_details(
                request=request,
                obj=payment,
                action_name="retry_payment",
                old_values=old_values,
                new_values=new_values,
            )
```

### 12.5 Log Retention Policy

| Log Type | Retention | Reason |
|----------|-----------|--------|
| Payment actions | 7 years | Financial compliance |
| Order changes | 3 years | Customer disputes |
| User modifications | 5 years | Security audit |
| Product changes | 1 year | Operational |

---

## 13. Appendix

### A. Current Admin Classes Inventory

| Line | Class | Type | Target Module |
|------|-------|------|---------------|
| 32 | SellerProfileInline | Inline | user_admin.py |
| 55 | UserAdmin | Admin | user_admin.py |
| 182 | CategoryAdmin | Admin | product_admin.py |
| 229 | ProductImageInline | Inline | product_admin.py |
| 238 | ProductReviewInline | Inline | product_admin.py |
| 249 | ProductAdmin | Admin | product_admin.py |
| 298 | ProductReviewAdmin | Admin | product_admin.py |
| 321 | OrderItemInline | Inline | order_admin.py |
| 332 | OrderAdmin | Admin | order_admin.py |
| 427 | CartItemInline | Inline | order_admin.py |
| 441 | CartAdmin | Admin | order_admin.py |
| 474 | PaymentAdmin | Admin | payment_admin.py |
| 637 | PaymentLogAdmin | Admin | payment_admin.py |
| 733 | PointHistoryAdmin | Admin | point_admin.py |
| 796 | EmailVerificationTokenAdmin | Admin | notification_admin.py |
| 852 | EmailLogAdmin | Admin | notification_admin.py |
| 964 | NotificationAdmin | Admin | notification_admin.py |
| 1008 | ProductAnswerInline | Inline | qa_admin.py |
| 1018 | ProductQuestionAdmin | Admin | qa_admin.py |
| 1072 | ProductAnswerAdmin | Admin | qa_admin.py |
| 1114 | ReturnItemInline | Inline | return_admin.py |
| 1133 | ReturnAdmin | Admin | return_admin.py |
| 1364 | ReturnItemAdmin | Admin | return_admin.py |
| 1418 | SellerProfileAdmin | Admin | user_admin.py |
| 1469 | CircuitBreakerStateAdmin | Admin | security_admin.py |
| 1672 | FailedOperationAdmin | Admin | security_admin.py |

### B. Related Files to Update

After migration, check these files for any direct imports from `shopping.admin`:

```bash
grep -r "from shopping.admin" --include="*.py" .
grep -r "from shopping import admin" --include="*.py" .
grep -r "import shopping.admin" --include="*.py" .
```

### C. Estimated Time

| Phase | Duration |
|-------|----------|
| Phase 1: Setup | 0.5 day |
| Phase 2: Migration | 1 day |
| Phase 3: Verification | 0.5 day |
| Phase 4: Testing | 1 day |
| Phase 5: Cleanup | 0.5 day |
| **Total** | **3-4 days** |

---

## 14. Approval

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Tech Lead | | | |
| Code Owner | | | |
| QA Lead | | | |
| Ops Lead | | | |

---

## 15. Document History

| Version | Date | Author | Changes |
|---------|------|--------|--------|
| 1.0 | 2025-12-08 | Dev Team | Initial draft |
| 1.1 | 2025-12-08 | Dev Team | Added: Mission statement, business metrics, dependency-based migration order, base.py scope, state/concurrency tests, granular rollback, operator logging |

---

*Document maintained by the development team. Last updated: 2025-12-08*
