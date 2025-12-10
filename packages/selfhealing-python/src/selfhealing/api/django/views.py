"""
Self-Healing Control API Views.

REST API endpoints for the Self-Healing Control API.

Endpoints:
- POST /api/self-healing/control/ - Execute control action
- GET  /api/self-healing/status/ - Get all service states
- GET  /api/self-healing/status/{service_name}/ - Get specific service state
- GET  /api/self-healing/audit/ - Get audit logs
- POST /api/self-healing/allow/{service_name}/ - Quick allow
- POST /api/self-healing/block/{service_name}/ - Quick block
- POST /api/self-healing/reset/{service_name}/ - Quick reset
- GET  /api/self-healing/health/ - Health check
- GET  /api/self-healing/metrics/ - Get metrics
- POST /api/self-healing/dlq/replay/ - Trigger DLQ replay
"""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.serializers import (
    ControlRequestSerializer,
    ControlResponseSerializer,
    ControlErrorResponseSerializer,
    ControlStatusResponseSerializer,
    ServiceStateSerializer,
    AuditLogListResponseSerializer,
    ControlAPIActions,
    ControlAPIEnvironments,
    DLQReplayRequestSerializer,
)
from selfhealing.adapters.django.models import (
    FailedOperation,
    CircuitBreakerState,
)
from selfhealing.core.types import CircuitState

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Data Classes
# =============================================================================


@dataclass
class ControlRequest:
    """Internal control request representation."""
    service_name: str
    action: str
    reason: str
    environment: str
    ttl_minutes: Optional[int] = None
    request_id: str = ""
    metadata: Dict[str, Any] = None
    actor: str = "system"
    actor_role: str = "user"


@dataclass
class ControlResponse:
    """Internal control response representation."""
    status: str  # success, rejected, error
    action_applied: str
    system_state: Optional[str] = None
    effective_until: Optional[Any] = None
    reason_classification: Optional[str] = None
    evidence: Optional[Dict[str, Any]] = None
    correlation_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "status": self.status,
            "action_applied": self.action_applied,
        }
        if self.system_state:
            result["system_state"] = self.system_state
        if self.effective_until:
            result["effective_until"] = self.effective_until
        if self.reason_classification:
            result["reason_classification"] = self.reason_classification
        if self.evidence:
            result["evidence"] = self.evidence
        if self.correlation_id:
            result["correlation_id"] = self.correlation_id
        if self.error_code:
            result["error_code"] = self.error_code
        if self.error_message:
            result["error_message"] = self.error_message
        return result


# =============================================================================
# Control API Service
# =============================================================================


class ControlAPIService:
    """
    Service layer for Control API operations.
    
    Handles the business logic for control actions.
    """

    def execute(self, request: ControlRequest) -> ControlResponse:
        """Execute a control action."""
        try:
            if request.action == ControlAPIActions.ALLOW:
                return self._execute_allow(request)
            elif request.action == ControlAPIActions.BLOCK:
                return self._execute_block(request)
            elif request.action == ControlAPIActions.RESET:
                return self._execute_reset(request)
            elif request.action == ControlAPIActions.OVERRIDE:
                return self._execute_override(request)
            elif request.action == ControlAPIActions.INJECT_FAILURE:
                return self._execute_inject_failure(request)
            else:
                return ControlResponse(
                    status="error",
                    action_applied=request.action,
                    error_code="UNKNOWN_ACTION",
                    error_message=f"Unknown action: {request.action}",
                )
        except Exception as e:
            logger.error(f"Control API error: {e}", exc_info=True)
            return ControlResponse(
                status="error",
                action_applied=request.action,
                error_code="INTERNAL_ERROR",
                error_message=str(e),
            )

    def _execute_allow(self, request: ControlRequest) -> ControlResponse:
        """Execute allow action (close circuit breaker)."""
        cb, created = CircuitBreakerState.objects.get_or_create(
            service_name=request.service_name,
            defaults={"state": CircuitState.CLOSED.value}
        )

        previous_state = cb.state
        cb.state = CircuitState.CLOSED.value
        cb.failure_count = 0
        cb.success_count = 0
        cb.opened_at = None
        cb.half_opened_at = None
        cb.manually_controlled = True
        cb.control_reason = request.reason
        cb.save()

        return ControlResponse(
            status="success",
            action_applied=request.action,
            system_state="allow",
            reason_classification="manual_allow",
            evidence={
                "previous_state": previous_state,
                "new_state": cb.state,
            },
        )

    def _execute_block(self, request: ControlRequest) -> ControlResponse:
        """Execute block action (open circuit breaker)."""
        cb, created = CircuitBreakerState.objects.get_or_create(
            service_name=request.service_name,
            defaults={"state": CircuitState.CLOSED.value}
        )

        previous_state = cb.state
        cb.state = CircuitState.OPEN.value
        cb.opened_at = timezone.now()
        cb.manually_controlled = True
        cb.control_reason = request.reason

        if request.ttl_minutes:
            cb.manual_override_expires_at = timezone.now() + timedelta(minutes=request.ttl_minutes)

        cb.save()

        return ControlResponse(
            status="success",
            action_applied=request.action,
            system_state="block",
            effective_until=cb.manual_override_expires_at,
            reason_classification="manual_block",
            evidence={
                "previous_state": previous_state,
                "new_state": cb.state,
            },
        )

    def _execute_reset(self, request: ControlRequest) -> ControlResponse:
        """Execute reset action."""
        try:
            cb = CircuitBreakerState.objects.get(service_name=request.service_name)
            previous_state = cb.state
            cb.reset()

            return ControlResponse(
                status="success",
                action_applied=request.action,
                system_state="allow",
                reason_classification="manual_reset",
                evidence={
                    "previous_state": previous_state,
                    "new_state": cb.state,
                },
            )
        except CircuitBreakerState.DoesNotExist:
            return ControlResponse(
                status="error",
                action_applied=request.action,
                error_code="SERVICE_NOT_FOUND",
                error_message=f"Service '{request.service_name}' not found",
            )

    def _execute_override(self, request: ControlRequest) -> ControlResponse:
        """Execute override action."""
        cb, created = CircuitBreakerState.objects.get_or_create(
            service_name=request.service_name,
            defaults={"state": CircuitState.CLOSED.value}
        )

        previous_state = cb.state
        cb.manually_controlled = True
        cb.control_reason = request.reason

        if request.ttl_minutes:
            cb.manual_override_expires_at = timezone.now() + timedelta(minutes=request.ttl_minutes)

        cb.save()

        return ControlResponse(
            status="success",
            action_applied=request.action,
            system_state=cb.state,
            effective_until=cb.manual_override_expires_at,
            reason_classification="manual_override",
            evidence={
                "state": cb.state,
                "override_active": True,
            },
        )

    def _execute_inject_failure(self, request: ControlRequest) -> ControlResponse:
        """Execute inject_failure action (chaos testing only)."""
        if request.environment == ControlAPIEnvironments.OPS:
            return ControlResponse(
                status="rejected",
                action_applied=request.action,
                error_code="ACTION_FORBIDDEN_IN_ENVIRONMENT",
                error_message="inject_failure is FORBIDDEN in ops environment",
            )

        # In a real implementation, this would configure failure injection
        return ControlResponse(
            status="success",
            action_applied=request.action,
            system_state="inject_failure",
            effective_until=timezone.now() + timedelta(minutes=request.ttl_minutes or 10),
            reason_classification="chaos_test",
            evidence={
                "metadata": request.metadata,
            },
        )

    def get_status(self, environment: str = "ops") -> Dict[str, Any]:
        """Get status of all services."""
        circuits = CircuitBreakerState.objects.all().order_by("service_name")

        services = []
        for cb in circuits:
            services.append({
                "service_name": cb.service_name,
                "state": cb.state,
                "failure_count": cb.failure_count,
                "success_count": cb.success_count,
                "last_failure_at": cb.last_failure_at,
                "opened_at": cb.opened_at,
                "manually_controlled": cb.manually_controlled,
                "controlled_by": cb.controlled_by_id,
                "control_reason": cb.control_reason,
                "expires_at": cb.manual_override_expires_at,
            })

        return {
            "services": services,
            "environment": environment,
            "timestamp": timezone.now(),
        }

    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """Get status of a specific service."""
        try:
            cb = CircuitBreakerState.objects.get(service_name=service_name)
            return {
                "service_name": cb.service_name,
                "state": cb.state,
                "failure_count": cb.failure_count,
                "success_count": cb.success_count,
                "last_failure_at": cb.last_failure_at,
                "opened_at": cb.opened_at,
                "manually_controlled": cb.manually_controlled,
                "controlled_by": cb.controlled_by_id,
                "control_reason": cb.control_reason,
                "expires_at": cb.manual_override_expires_at,
            }
        except CircuitBreakerState.DoesNotExist:
            return {
                "service_name": service_name,
                "state": "unknown",
                "error": "Service not found",
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Get comprehensive self-healing metrics."""
        start_time = time.time()

        # Circuit breaker stats
        all_circuits = CircuitBreakerState.objects.all()
        total_services = all_circuits.count()
        healthy_services = all_circuits.filter(state=CircuitState.CLOSED.value).count()
        degraded_services = total_services - healthy_services

        # DLQ stats
        dlq_by_domain = dict(
            FailedOperation.objects.filter(
                status=FailedOperation.Status.PENDING
            ).values("domain").annotate(count=Count("id")).values_list("domain", "count")
        )
        total_dlq_pending = sum(dlq_by_domain.values())

        # Per-service metrics
        services = []
        for cb in all_circuits:
            dlq_count = FailedOperation.objects.filter(
                domain=cb.service_name,
                status=FailedOperation.Status.PENDING,
            ).count()

            services.append({
                "service_name": cb.service_name,
                "failure_rate_5m": 0.0,  # Would need more data to calculate
                "retry_success_rate": 0.0,
                "dlq_count": dlq_count,
                "circuit_state": cb.state,
                "avg_recovery_time_seconds": None,
            })

        collection_duration_ms = int((time.time() - start_time) * 1000)

        return {
            "total_services": total_services,
            "healthy_services": healthy_services,
            "degraded_services": degraded_services,
            "last_5m_failure_rate": 0.0,
            "last_5m_request_count": 0,
            "avg_time_to_recovery": None,
            "auto_allowed_count_24h": 0,
            "auto_blocked_count_24h": 0,
            "total_dlq_pending": total_dlq_pending,
            "dlq_by_service": dlq_by_domain,
            "services": services,
            "timestamp": timezone.now(),
            "collection_duration_ms": collection_duration_ms,
        }


# Singleton instance
_control_api_service = None


def get_control_api_service() -> ControlAPIService:
    """Get the singleton Control API service instance."""
    global _control_api_service
    if _control_api_service is None:
        _control_api_service = ControlAPIService()
    return _control_api_service


# =============================================================================
# Control API Views
# =============================================================================


class ControlActionView(APIView):
    """
    Execute Self-Healing Control Actions.

    POST /api/self-healing/control/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Execute a control action."""
        serializer = ControlRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "status": "rejected",
                    "error_code": "VALIDATION_ERROR",
                    "error_message": serializer.errors,
                    "action_requested": request.data.get("action", "unknown"),
                    "environment": request.data.get("environment", "unknown"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build internal request
        control_request = ControlRequest(
            service_name=serializer.validated_data["service_name"],
            action=serializer.validated_data["action"],
            reason=serializer.validated_data["reason"],
            environment=serializer.validated_data["environment"],
            ttl_minutes=serializer.validated_data.get("ttl_minutes"),
            request_id=str(serializer.validated_data.get("request_id", "")),
            metadata=serializer.validated_data.get("metadata", {}),
            actor=request.user.username if request.user else "anonymous",
            actor_role="admin" if request.user and request.user.is_staff else "user",
        )

        # Execute
        service = get_control_api_service()
        response = service.execute(control_request)

        # Return response
        if response.status == "rejected":
            return Response(response.to_dict(), status=status.HTTP_403_FORBIDDEN)
        elif response.status == "error":
            return Response(response.to_dict(), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            return Response(response.to_dict(), status=status.HTTP_200_OK)


class ControlStatusView(APIView):
    """
    Get Self-Healing Service Status.

    GET /api/self-healing/status/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get status of all services."""
        environment = request.query_params.get("environment", "ops")

        service = get_control_api_service()
        status_data = service.get_status(environment=environment)

        return Response(status_data)


class ServiceStatusView(APIView):
    """
    Get Specific Service Status.

    GET /api/self-healing/status/{service_name}/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, service_name: str):
        """Get status of a specific service."""
        service = get_control_api_service()
        status_data = service.get_service_status(service_name)

        return Response(status_data)


class ControlAuditView(APIView):
    """
    Get Self-Healing Audit Logs.

    GET /api/self-healing/audit/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        """Get audit logs."""
        # TODO: Implement actual audit log retrieval
        return Response(
            {
                "logs": [],
                "total_count": 0,
                "page": int(request.query_params.get("page", 1)),
                "page_size": int(request.query_params.get("page_size", 50)),
            }
        )


# =============================================================================
# Quick Action Views
# =============================================================================


class QuickAllowView(APIView):
    """
    Quick Allow Action.

    POST /api/self-healing/allow/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, service_name: str):
        """Quick allow a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.ALLOW,
            reason=request.data.get("reason", "Quick allow via API"),
            environment=request.data.get("environment", "ops"),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


class QuickBlockView(APIView):
    """
    Quick Block Action.

    POST /api/self-healing/block/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, service_name: str):
        """Quick block a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.BLOCK,
            reason=request.data.get("reason", "Quick block via API"),
            environment=request.data.get("environment", "ops"),
            ttl_minutes=request.data.get("ttl_minutes", 90),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


class QuickResetView(APIView):
    """
    Quick Reset Action.

    POST /api/self-healing/reset/{service_name}/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request, service_name: str):
        """Quick reset a service."""
        control_request = ControlRequest(
            service_name=service_name,
            action=ControlAPIActions.RESET,
            reason=request.data.get("reason", "Quick reset via API"),
            environment=request.data.get("environment", "ops"),
            actor=request.user.username if request.user else "anonymous",
        )

        service = get_control_api_service()
        response = service.execute(control_request)

        return Response(response.to_dict())


# =============================================================================
# Health & Metrics Views
# =============================================================================


class SelfHealingHealthView(APIView):
    """
    Self-Healing System Health Check.

    GET /api/self-healing/health/
    """

    permission_classes = []  # Public endpoint

    def get(self, request):
        """Get self-healing system health."""
        try:
            services_count = CircuitBreakerState.objects.count()
            health_status = "healthy"
        except Exception as e:
            logger.error(f"[SelfHealing] Health check failed: {e}")
            services_count = 0
            health_status = "degraded"

        return Response({
            "status": health_status,
            "circuit_breaker_enabled": True,
            "services_count": services_count,
            "timestamp": timezone.now().isoformat(),
        })


class SelfHealingMetricsView(APIView):
    """
    Self-Healing Metrics for Trend Analysis.

    GET /api/self-healing/metrics/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get comprehensive self-healing metrics."""
        service = get_control_api_service()

        try:
            metrics = service.get_metrics()
            return Response(metrics, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"[SelfHealing] Metrics collection failed: {e}")
            return Response(
                {"error": "Failed to collect metrics", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# =============================================================================
# DLQ Replay View
# =============================================================================


class DLQReplayView(APIView):
    """
    DLQ Replay API.

    POST /api/self-healing/dlq/replay/
    """

    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        """Trigger DLQ replay."""
        serializer = DLQReplayRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        domain = serializer.validated_data.get("domain")
        batch_size = serializer.validated_data.get("batch_size", 50)

        try:
            # Get pending DLQ entries
            queryset = FailedOperation.objects.filter(
                status=FailedOperation.Status.PENDING
            )
            if domain:
                queryset = queryset.filter(domain=domain)

            entries = queryset[:batch_size]
            total = len(entries)
            success_count = 0
            failed_count = 0
            skipped_count = 0

            for entry in entries:
                # Here you would implement actual replay logic
                # For now, just log
                logger.info(f"[DLQ] Would replay entry {entry.id}: {entry.domain}/{entry.failure_type}")
                # In real implementation:
                # result = replay_operation(entry)
                # if result.success:
                #     entry.mark_as_resolved("auto_replay")
                #     success_count += 1
                # else:
                #     entry.increment_retry()
                #     failed_count += 1

            logger.info(
                f"[DLQ] Replay triggered via API: domain={domain}, "
                f"batch_size={batch_size}, success={success_count}, failed={failed_count}"
            )

            return Response({
                "status": "success",
                "total": total,
                "success_count": success_count,
                "failed_count": failed_count,
                "skipped_count": skipped_count,
            })

        except Exception as e:
            logger.error(f"[DLQ] Replay failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
