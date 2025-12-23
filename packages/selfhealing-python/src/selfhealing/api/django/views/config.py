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

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin
from selfhealing.api.django.serializers.config import (
    CircuitBreakerConfigSerializer,
    DLQConfigSerializer,
    RetryConfigSerializer,
    SLAConfigSerializer,
    SLOConfigSerializer,
    RateLimitConfigSerializer,
    SecurityConfigSerializer,
    IdempotencyConfigSerializer,
    NotificationConfigSerializer,
    ForensicConfigSerializer,
    LoggingConfigSerializer,
    MetricsConfigSerializer,
    ErrorBudgetConfigSerializer,
    PendingConfigChangeSerializer,
)
from selfhealing.api.django.config_descriptions import format_changes_summary
from selfhealing.services.runtime_config import get_runtime_config_manager

logger = logging.getLogger(__name__)


class AllConfigView(APIView):
    """
    All Configuration API.

    GET  /api/self-healing/config/ - Get all configuration with default strategies
    
    Note: Read access for Viewer role, write requires Admin role.
    """

    permission_classes = [IsViewer]

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
    
    Note: Admin-only endpoint - requires selfhealing_admin role.
    """

    permission_classes = [IsSelfHealingAdmin]

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
    
    Note: Read access for Viewer role.
    """

    permission_classes = [IsViewer]

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
    
    Note: Admin-only endpoint - requires selfhealing_admin role.
    """

    permission_classes = [IsSelfHealingAdmin]

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
    
    Note: 
    - GET: Viewer role can access (read-only)
    - PUT: Admin role required (configuration changes)
    """

    # Default to Admin for safety - subclasses can override for GET
    permission_classes = [IsSelfHealingAdmin]
    serializer_class = None
    config_name = ""
    
    def get_permissions(self):
        """
        Return different permissions based on HTTP method.
        
        GET: IsViewer (read-only)
        PUT: IsSelfHealingAdmin (configuration changes)
        """
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request (handles proxies)."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")

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
                # Audit log for validation failures (potential misuse detection)
                client_ip = self._get_client_ip(request)
                logger.warning(
                    f"[ConfigAudit] Validation failed: config={self.config_name}, "
                    f"errors={serializer.errors}, user={request.user}, ip={client_ip}"
                )
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

            # Get previous config for change logging
            previous_config = manager._get_config(self.config_name)

            # Update with strategy
            result = manager.update_with_strategy(
                config_type=self.config_name,
                changes=config_changes,
                **apply_options,
            )

            # Format semantic change log (with fallback for robustness)
            try:
                change_summary = format_changes_summary(config_changes, previous_config)
                logger.info(
                    f"[ConfigAPI] {self.config_name.upper()} config updated by {request.user}:"
                    f"{change_summary}\n  Applied: {result.get('applied_strategy', 'immediate')}"
                )
            except Exception as log_err:
                # Fallback to basic logging - never let logging failure affect the API
                logger.warning(
                    f"[ConfigAPI] Semantic log formatting failed: {log_err}. "
                    f"Falling back to basic log."
                )
                logger.info(
                    f"[ConfigAPI] {self.config_name} config updated by {request.user}: "
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


class LoggingConfigView(BaseConfigView):
    """
    Logging Configuration API.

    GET  /api/self-healing/config/logging/ - Get logging config
    PUT  /api/self-healing/config/logging/ - Update logging config

    각 Self-Healing 컨포넌트별 로깅 레벨을 동적으로 변경할 수 있습니다.
    이전에는 환경변수로만 제어 가능했던 설정들을 API로 노출.
    
    Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md (Phase 5)
    """

    serializer_class = LoggingConfigSerializer
    config_name = "logging"


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


class SLOConfigView(BaseConfigView):
    """
    SLO (Service Level Objectives) Configuration API.

    GET    /api/self-healing/config/slo/ - Get all SLO configurations
    PUT    /api/self-healing/config/slo/ - Add/Update SLO definitions
    DELETE /api/self-healing/config/slo/<name>/ - Delete a specific SLO

    SLO 정의를 동적으로 관리할 수 있습니다:
    - target: SLO 목표값 (예: 0.999 = 99.9%)
    - window_days: 측정 윈도우 (일)
    - fast_burn_rate: 빠른 소진율 임계값
    - slow_burn_rate: 느린 소진율 임계값
    """

    serializer_class = SLOConfigSerializer
    config_name = "slo"

    def put(self, request: Request) -> Response:
        """Add or update SLO definitions."""
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                # Audit log for validation failures
                client_ip = self._get_client_ip(request)
                logger.warning(
                    f"[ConfigAudit] Validation failed: config={self.config_name}, "
                    f"errors={serializer.errors}, user={request.user}, ip={client_ip}"
                )
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            manager = get_runtime_config_manager()
            validated = serializer.validated_data

            # Extract SLO-specific updates
            result = manager.update_slo_config(
                default_window_days=validated.get("default_window_days"),
                default_target=validated.get("default_target"),
                default_fast_burn_rate=validated.get("default_fast_burn_rate"),
                default_slow_burn_rate=validated.get("default_slow_burn_rate"),
                slo=validated.get("slo"),
                slos=validated.get("slos"),
            )

            logger.info(f"[ConfigAPI] SLO config updated by {request.user}")

            return Response(
                {
                    "status": "success",
                    "config": result,
                    "config_type": "slo",
                    "timestamp": timezone.now(),
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"[ConfigAPI] Error updating SLO config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request: Request) -> Response:
        """Delete a specific SLO by name."""
        try:
            slo_name = request.query_params.get("name")
            if not slo_name:
                return Response(
                    {"status": "error", "error": "Query parameter 'name' is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            manager = get_runtime_config_manager()
            result = manager.delete_slo(slo_name)

            if result.get("status") == "deleted":
                logger.info(f"[ConfigAPI] SLO '{slo_name}' deleted by {request.user}")
                return Response(
                    {
                        **result,
                        "timestamp": timezone.now(),
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(result, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"[ConfigAPI] Error deleting SLO: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
