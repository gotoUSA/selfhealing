"""
Fail-Secure Permission Classes.

FAIL-SECURE Design:
- If authentication/authorization check fails for ANY reason, deny access
- Default to denial on ambiguity
- Log all failures for security monitoring
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = structlog.get_logger()


class FailSecureIsAuthenticated:
    """
    Fail-Secure version of IsAuthenticated.

    FAIL-SECURE Design:
    - If authentication check fails for ANY reason, deny access
    - Default to denial on ambiguity
    - Log all failures for security monitoring
    """

    def has_permission(self, request: HttpRequest, view: Any) -> bool:
        """Check if user is authenticated with fail-secure logic."""
        try:
            # Standard authentication check
            is_authenticated = bool(
                request.user
                and hasattr(request.user, "is_authenticated")
                and request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(
                    f"[Permission] Denied: user not authenticated, "
                    f"path={request.path}, ip={request.META.get('REMOTE_ADDR')}"
                )

            return is_authenticated

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(
                f"[Permission] FAIL-SECURE denial due to error: {e}, "
                f"path={request.path}"
            )
            return False


class FailSecureIsAdminUser:
    """
    Fail-Secure version of IsAdminUser.

    FAIL-SECURE Design:
    - If admin check fails for ANY reason, deny access
    - Both is_authenticated AND is_staff must be True
    - Log all denials for security monitoring
    """

    def has_permission(self, request: HttpRequest, view: Any) -> bool:
        """Check if user is admin with fail-secure logic."""
        try:
            # Must be authenticated first
            is_authenticated = bool(
                request.user
                and hasattr(request.user, "is_authenticated")
                and request.user.is_authenticated
            )

            if not is_authenticated:
                logger.info(
                    f"[Permission] Admin check denied: not authenticated, "
                    f"path={request.path}"
                )
                return False

            # Must be staff
            is_admin = bool(hasattr(request.user, "is_staff") and request.user.is_staff)

            if not is_admin:
                logger.info(
                    f"[Permission] Admin check denied: user={request.user}, "
                    f"path={request.path}"
                )

            return is_admin

        except Exception as e:
            # FAIL-SECURE: Any error = deny access
            logger.warning(
                f"[Permission] FAIL-SECURE admin denial due to error: {e}, "
                f"path={request.path}"
            )
            return False
