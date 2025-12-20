"""
Runtime Configuration API Views.

REST API endpoints for runtime configuration management with apply strategy support.

Endpoints:
- GET  /api/self-healing/config/                    - Get all config
- POST /api/self-healing/config/reset/              - Reset all to defaults
- GET  /api/self-healing/config/pending/            - Get pending changes
- POST /api/self-healing/config/pending/<id>/cancel - Cancel pending change
- GET  /api/self-healing/config/circuit-breaker/    - Get CB config
- PUT  /api/self-healing/config/circuit-breaker/    - Update CB config
... (similar for all 10 config types)

Apply Strategies:
- immediate: Apply changes right now (default for safe configs)
- delayed: Apply after N seconds (cancellable)
- graceful: Wait for in-progress operations to complete
"""

import logging
from typing import Callable, Dict, Any, Optional

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    SLAConfigSerializer,
    RateLimitConfigSerializer,
    SecurityConfigSerializer,
    IdempotencyConfigSerializer,
    NotificationConfigSerializer,
    ForensicConfigSerializer,
    MetricsConfigSerializer,
    ErrorBudgetConfigSerializer,
    PendingConfigChangeSerializer,
)
from selfhealing.services.runtime_config import get_runtime_config_manager

logger = logging.getLogger(__name__)


class AllConfigView(APIView):
    """
    All Configuration API.

    GET  /api/self-healing/config/ - Get all configuration with default strategies
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get all configuration with default apply strategies."""
        try:
            manager = get_runtime_config_manager()
            config = manager.get_all_config()

            # Add default strategy info for each config type
            config_with_strategies = {}
            for config_type, config_values in config.items():
                config_with_strategies[config_type] = {
                    "values": config_values,
                    "default_strategy": manager.get_default_strategy(config_type),
                }

            return Response(
                {
                    "status": "success",
                    "config": config_with_strategies,
                    "pending_changes": manager.get_pending_changes(),
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[ConfigAPI] Error getting all config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ResetConfigView(APIView):
    """
    Reset Configuration API.

    POST /api/self-healing/config/reset/ - Reset all to defaults
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request) -> Response:
        """Reset all configuration to defaults."""
        try:
            manager = get_runtime_config_manager()
            config = manager.reset_to_defaults()

            logger.info(f"[ConfigAPI] All config reset to defaults by {request.user}")

            return Response(
                {
                    "status": "success",
                    "message": "All configuration reset to defaults",
                    "config": config,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[ConfigAPI] Error resetting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PendingChangesView(APIView):
    """
    Pending Configuration Changes API.

    GET /api/self-healing/config/pending/ - Get all pending changes
    """

    permission_classes = [IsAdminUser]

    def get(self, request: Request) -> Response:
        """Get all pending configuration changes."""
        try:
            manager = get_runtime_config_manager()
            config_type = request.query_params.get("config_type")
            pending = manager.get_pending_changes(config_type)

            return Response(
                {
                    "status": "success",
                    "pending_changes": pending,
                    "count": len(pending),
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[ConfigAPI] Error getting pending changes: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CancelPendingChangeView(APIView):
    """
    Cancel Pending Configuration Change API.

    POST /api/self-healing/config/pending/<id>/cancel/ - Cancel a pending change
    """

    permission_classes = [IsAdminUser]

    def post(self, request: Request, pending_id: str) -> Response:
        """Cancel a pending configuration change."""
        try:
            manager = get_runtime_config_manager()
            result = manager.cancel_pending_change(
                pending_id,
                cancelled_by=str(request.user),
            )

            if result.get("status") == "cancelled":
                return Response(
                    {
                        **result,
                        "message": f"Pending change {pending_id} cancelled",
                        "timestamp": timezone.now(),
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    result,
                    status=status.HTTP_404_NOT_FOUND,
                )
        except Exception as e:
            logger.error(f"[ConfigAPI] Error cancelling pending change: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BaseConfigView(APIView):
    """
    Base class for configuration views with apply strategy support.

    Provides common GET/PUT handling with serializer validation.
    Supports immediate, delayed, and graceful apply strategies.
    """

    permission_classes = [IsAdminUser]
    serializer_class = None
    config_name = ""

    def get(self, request: Request) -> Response:
        """Get configuration with default strategy info."""
        try:
            manager = get_runtime_config_manager()
            config = manager._get_config(self.config_name)
            default_strategy = manager.get_default_strategy(self.config_name)
            pending = manager.get_pending_changes(self.config_name)

            return Response(
                {
                    "status": "success",
                    "config": config,
                    "config_type": self.config_name,
                    "default_strategy": default_strategy,
                    "pending_changes": pending,
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.error(f"[ConfigAPI] Error getting {self.config_name} config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update configuration with apply strategy support."""
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            manager = get_runtime_config_manager()

            # Extract apply options and config changes
            apply_options = serializer.get_apply_options()
            config_changes = serializer.get_config_changes()

            if not config_changes:
                return Response(
                    {"status": "error", "error": "No valid configuration values provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Update with strategy
            result = manager.update_with_strategy(
                config_type=self.config_name,
                changes=config_changes,
                **apply_options,
            )

            logger.info(
                f"[ConfigAPI] {self.config_name} config update requested by {request.user}: "
                f"changes={config_changes}, strategy={result.get('applied_strategy')}"
            )

            # Determine response status based on result
            if result.get("status") == "applied":
                http_status = status.HTTP_200_OK
            elif result.get("status") in ("scheduled", "waiting"):
                http_status = status.HTTP_202_ACCEPTED
            else:
                http_status = status.HTTP_400_BAD_REQUEST

            result["timestamp"] = timezone.now()
            result["config_type"] = self.config_name

            return Response(result, status=http_status)

        except Exception as e:
            logger.error(f"[ConfigAPI] Error updating {self.config_name} config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CircuitBreakerConfigView(BaseConfigView):
    """Circuit Breaker Configuration API."""

    serializer_class = CircuitBreakerConfigSerializer
    config_name = "circuit_breaker"


class DLQConfigView(BaseConfigView):
    """DLQ Configuration API."""

    serializer_class = DLQConfigSerializer
    config_name = "dlq"


class RetryConfigView(BaseConfigView):
    """Retry Configuration API."""

    serializer_class = RetryConfigSerializer
    config_name = "retry"


class SLAConfigView(BaseConfigView):
    """SLA Configuration API."""

    serializer_class = SLAConfigSerializer
    config_name = "sla"


class RateLimitConfigView(BaseConfigView):
    """Rate Limit Configuration API."""

    serializer_class = RateLimitConfigSerializer
    config_name = "rate_limit"


class SecurityConfigView(BaseConfigView):
    """Security Configuration API."""

    serializer_class = SecurityConfigSerializer
    config_name = "security"


class IdempotencyConfigView(BaseConfigView):
    """Idempotency Configuration API."""

    serializer_class = IdempotencyConfigSerializer
    config_name = "idempotency"


class NotificationConfigView(BaseConfigView):
    """Notification Configuration API."""

    serializer_class = NotificationConfigSerializer
    config_name = "notification"


class ForensicConfigView(BaseConfigView):
    """Forensic Configuration API."""

    serializer_class = ForensicConfigSerializer
    config_name = "forensic"


class MetricsConfigView(BaseConfigView):
    """Metrics Configuration API."""

    serializer_class = MetricsConfigSerializer
    config_name = "metrics"


class ErrorBudgetConfigView(BaseConfigView):
    """
    Error Budget Configuration API.

    GET  /api/self-healing/config/error-budget/ - Get Error Budget config
    PUT  /api/self-healing/config/error-budget/ - Update Error Budget config

    Error Budget 및 Burn Rate 임계값을 동적으로 변경할 수 있습니다.
    변경 후 error_budget_service에서 자동으로 새 임계값을 사용합니다.
    """

    serializer_class = ErrorBudgetConfigSerializer
    config_name = "error_budget"
