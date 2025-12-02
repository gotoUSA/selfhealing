"""permissions.py 테스트"""

from unittest.mock import Mock

import pytest

from shopping.permissions import (
    IsOrderOwnerOrAdmin,
    IsSeller,
    IsSellerAndOwner,
    IsSellerAndProductOwner,
    IsSellerOrReadOnly,
)
from shopping.tests.factories import ProductFactory, UserFactory


class TestIsSeller:
    """IsSeller 권한 테스트"""

    def test_authenticated_seller_returns_true(self):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=True)
        request = Mock(user=user)
        view = Mock()
        permission = IsSeller()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    def test_authenticated_non_seller_returns_false(self):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=False)
        request = Mock(user=user)
        view = Mock()
        permission = IsSeller()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False

    def test_unauthenticated_user_returns_false(self):
        # Arrange
        user = Mock(is_authenticated=False)
        request = Mock(user=user)
        view = Mock()
        permission = IsSeller()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False

    def test_anonymous_user_returns_false(self):
        # Arrange
        request = Mock(user=None)
        view = Mock()
        permission = IsSeller()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False


class TestIsSellerAndOwner:
    """IsSellerAndOwner 권한 테스트"""

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_safe_methods_allowed_for_anyone(self, method):
        # Arrange
        request = Mock(method=method, user=Mock(is_authenticated=False))
        view = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_allowed_for_seller(self, method):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=True)
        request = Mock(method=method, user=user)
        view = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_denied_for_non_seller(self, method):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=False)
        request = Mock(method=method, user=user)
        view = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False

    def test_object_permission_safe_method_allowed(self):
        # Arrange
        request = Mock(method="GET")
        view = Mock()
        obj = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_object_permission(request, view, obj)

        # Assert
        assert result is True

    def test_object_permission_owner_allowed(self):
        # Arrange
        user = Mock()
        obj = Mock(seller=user)
        request = Mock(method="PUT", user=user)
        view = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_object_permission(request, view, obj)

        # Assert
        assert result is True

    def test_object_permission_non_owner_denied(self):
        # Arrange
        owner = Mock()
        other_user = Mock()
        obj = Mock(seller=owner)
        request = Mock(method="DELETE", user=other_user)
        view = Mock()
        permission = IsSellerAndOwner()

        # Act
        result = permission.has_object_permission(request, view, obj)

        # Assert
        assert result is False


class TestIsSellerAndProductOwner:
    """IsSellerAndProductOwner 권한 테스트"""

    def test_has_permission_seller_returns_true(self):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=True)
        request = Mock(user=user)
        view = Mock()
        permission = IsSellerAndProductOwner()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    def test_has_permission_non_seller_returns_false(self):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=False)
        request = Mock(user=user)
        view = Mock()
        permission = IsSellerAndProductOwner()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False

    def test_has_permission_for_product_owner_returns_true(self):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=True)
        product = Mock(seller=user)
        request = Mock(user=user)
        permission = IsSellerAndProductOwner()

        # Act
        result = permission.has_permission_for_product(request, product)

        # Assert
        assert result is True

    def test_has_permission_for_product_non_owner_returns_false(self):
        # Arrange
        owner = Mock()
        other_user = Mock(is_authenticated=True, is_seller=True)
        product = Mock(seller=owner)
        request = Mock(user=other_user)
        permission = IsSellerAndProductOwner()

        # Act
        result = permission.has_permission_for_product(request, product)

        # Assert
        assert result is False

    def test_has_permission_for_product_unauthenticated_returns_falsy(self):
        """인증되지 않은 사용자는 상품에 대한 권한이 없다."""
        # Arrange
        product = Mock(seller=Mock())
        request = Mock(user=None)
        permission = IsSellerAndProductOwner()

        # Act
        result = permission.has_permission_for_product(request, product)

        # Assert
        # request.user가 None이면 and 연산의 short-circuit으로 None 반환
        assert not result


class TestIsSellerOrReadOnly:
    """IsSellerOrReadOnly 권한 테스트"""

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    def test_safe_methods_allowed_for_anyone(self, method):
        # Arrange
        request = Mock(method=method, user=Mock(is_authenticated=False))
        view = Mock()
        permission = IsSellerOrReadOnly()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_allowed_for_seller(self, method):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=True)
        request = Mock(method=method, user=user)
        view = Mock()
        permission = IsSellerOrReadOnly()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is True

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_denied_for_non_seller(self, method):
        # Arrange
        user = Mock(is_authenticated=True, is_seller=False)
        request = Mock(method=method, user=user)
        view = Mock()
        permission = IsSellerOrReadOnly()

        # Act
        result = permission.has_permission(request, view)

        # Assert
        assert result is False


class TestIsOrderOwnerOrAdmin:
    """IsOrderOwnerOrAdmin 권한 테스트"""

    def test_admin_can_access_any_order(self):
        # Arrange
        admin = Mock(is_staff=True, is_superuser=False)
        other_user = Mock()
        order = Mock(user=other_user)
        request = Mock(user=admin)
        view = Mock()
        permission = IsOrderOwnerOrAdmin()

        # Act
        result = permission.has_object_permission(request, view, order)

        # Assert
        assert result is True

    def test_superuser_can_access_any_order(self):
        # Arrange
        superuser = Mock(is_staff=False, is_superuser=True)
        other_user = Mock()
        order = Mock(user=other_user)
        request = Mock(user=superuser)
        view = Mock()
        permission = IsOrderOwnerOrAdmin()

        # Act
        result = permission.has_object_permission(request, view, order)

        # Assert
        assert result is True

    def test_owner_can_access_own_order(self):
        # Arrange
        user = Mock(is_staff=False, is_superuser=False)
        order = Mock(user=user)
        request = Mock(user=user)
        view = Mock()
        permission = IsOrderOwnerOrAdmin()

        # Act
        result = permission.has_object_permission(request, view, order)

        # Assert
        assert result is True

    def test_non_owner_cannot_access_order(self):
        # Arrange
        owner = Mock()
        other_user = Mock(is_staff=False, is_superuser=False)
        order = Mock(user=owner)
        request = Mock(user=other_user)
        view = Mock()
        permission = IsOrderOwnerOrAdmin()

        # Act
        result = permission.has_object_permission(request, view, order)

        # Assert
        assert result is False
