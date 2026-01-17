"""
API Tiering Views for Criticality-Based Load Shedding.

REST API endpoints for managing tier definitions, mappings, and overrides.

Endpoints:
- GET  /api/self-healing/config/tiers/          - Get tier definitions
- PUT  /api/self-healing/config/tiers/          - Update tier definitions
- GET  /api/self-healing/config/tier-mappings/  - Get tier mappings
- PUT  /api/self-healing/config/tier-mappings/  - Update tier mappings
- GET  /api/self-healing/config/tier-overrides/ - Get tier overrides
- PUT  /api/self-healing/config/tier-overrides/ - Update tier overrides
- POST /api/self-healing/config/tiers/dry-run/  - Simulate tier changes
- POST /api/self-healing/config/tiers/reset/    - Reset to defaults
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsOperator, IsSelfHealingAdmin, IsViewer
from selfhealing.api.django.tiering.defaults import (
    DEFAULT_TIER_DEFINITIONS,
    DEFAULT_TIER_MAPPINGS,
    DEFAULT_TIER_OVERRIDES,
)
from selfhealing.api.django.tiering.enums import (
    OverrideIdentifierType,
    PatternType,
)
from selfhealing.api.django.tiering.validator import TierConfigValidator
from selfhealing.api.django.tiering.models import (
    TierDefinition,
    TierMapping,
    TierOverride,
)
from selfhealing.api.django.tiering.registry import get_tier_registry

logger = logging.getLogger(__name__)


# =============================================================================
# Serializers
# =============================================================================


class TierDefinitionSerializer(serializers.Serializer):
    """Serializer for TierDefinition."""

    id = serializers.CharField(
        max_length=50,
        help_text="티어 고유 ID (예: 'critical')",
    )
    name = serializers.CharField(
        max_length=100,
        help_text="티어 표시 이름",
    )
    multiplier = serializers.FloatField(
        min_value=0.0,
        max_value=1.0,
        help_text="비상 모드 시 허용 배율 (0.0 ~ 1.0)",
    )
    priority = serializers.IntegerField(
        default=0,
        help_text="우선순위 (높을수록 중요)",
    )
    description = serializers.CharField(
        default="",
        allow_blank=True,
        help_text="티어 설명",
    )
    color = serializers.CharField(
        default="#000000",
        max_length=7,
        help_text="UI 표시용 색상 코드",
    )

    def create(self, validated_data) -> TierDefinition:
        return TierDefinition(**validated_data)

    def update(self, instance, validated_data) -> TierDefinition:
        return TierDefinition(**validated_data)


class TierMappingSerializer(serializers.Serializer):
    """Serializer for TierMapping."""

    pattern = serializers.CharField(
        max_length=500,
        help_text="API 경로 패턴 (exact, wildcard, regex)",
    )
    tier_id = serializers.CharField(
        max_length=50,
        help_text="적용할 티어 ID",
    )
    pattern_type = serializers.ChoiceField(
        choices=[(pt.value, pt.name) for pt in PatternType],
        default=PatternType.EXACT.value,
        help_text="패턴 타입 (exact, wildcard, regex)",
    )
    priority = serializers.IntegerField(
        default=0,
        help_text="매핑 우선순위 (높을수록 먼저 매칭)",
    )
    description = serializers.CharField(
        default="",
        allow_blank=True,
        help_text="매핑 설명",
    )

    def create(self, validated_data) -> TierMapping:
        validated_data["pattern_type"] = PatternType(validated_data["pattern_type"])
        return TierMapping(**validated_data)


class TierOverrideSerializer(serializers.Serializer):
    """Serializer for TierOverride."""

    identifier = serializers.CharField(
        max_length=200,
        help_text="식별자 (IP, 사용자 ID, API 키)",
    )
    identifier_type = serializers.ChoiceField(
        choices=[(it.value, it.name) for it in OverrideIdentifierType],
        help_text="식별자 타입 (ip, user_id, api_key)",
    )
    tier_id = serializers.CharField(
        max_length=50,
        help_text="적용할 티어 ID",
    )
    reason = serializers.CharField(
        default="",
        allow_blank=True,
        help_text="오버라이드 사유",
    )
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="만료 시간 (없으면 영구)",
    )

    def create(self, validated_data) -> TierOverride:
        validated_data["identifier_type"] = OverrideIdentifierType(validated_data["identifier_type"])
        return TierOverride(**validated_data)


class TierDryRunRequestSerializer(serializers.Serializer):
    """Serializer for tier dry run request."""

    tiers = TierDefinitionSerializer(many=True, required=False)
    mappings = TierMappingSerializer(many=True, required=False)
    test_paths = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        help_text="테스트할 API 경로 목록",
    )


# =============================================================================
# Views
# =============================================================================


class TierDefinitionsView(APIView):
    """
    Tier Definitions API.

    GET  /api/self-healing/config/tiers/ - Get all tier definitions
    PUT  /api/self-healing/config/tiers/ - Update tier definitions
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsViewer()]
        return [IsAuthenticated(), IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get all tier definitions."""
        try:
            registry = get_tier_registry()
            tiers = registry.get_all_tiers()

            return Response(
                {
                    "status": "success",
                    "tiers": [t.to_dict() for t in tiers],
                    "defaults": [t.to_dict() for t in DEFAULT_TIER_DEFINITIONS],
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error getting tiers: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update tier definitions."""
        try:
            serializer = TierDefinitionSerializer(data=request.data.get("tiers", []), many=True)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            tiers = [TierDefinition(**item) for item in serializer.validated_data]

            registry = get_tier_registry()
            result = registry.set_tiers(tiers)

            if not result.is_valid:
                return Response(
                    {
                        "status": "error",
                        "validation": result.to_dict(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log audit
            self._log_change(request, "tiers", [t.to_dict() for t in tiers])

            return Response(
                {
                    "status": "success",
                    "tiers": [t.to_dict() for t in tiers],
                    "validation": result.to_dict(),
                    "changed_by": request.user.username,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error updating tiers: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _log_change(self, request, config_type: str, changes: Any):
        """Log configuration change."""
        try:
            from selfhealing.audit import log_config_change

            log_config_change(
                config_type=f"tiering_{config_type}",
                config_key="tier_config",
                old_value=None,
                new_value=changes,
                user=request.user.username,
            )
        except Exception as e:
            logger.warning(f"[TierAPI] Failed to log change: {e}")


class TierMappingsView(APIView):
    """
    Tier Mappings API.

    GET  /api/self-healing/config/tier-mappings/ - Get all tier mappings
    PUT  /api/self-healing/config/tier-mappings/ - Update tier mappings
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsViewer()]
        return [IsAuthenticated(), IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get all tier mappings."""
        try:
            registry = get_tier_registry()
            mappings = registry.get_all_mappings()

            return Response(
                {
                    "status": "success",
                    "mappings": [m.to_dict() for m in mappings],
                    "defaults": [m.to_dict() for m in DEFAULT_TIER_MAPPINGS],
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error getting mappings: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update tier mappings."""
        try:
            serializer = TierMappingSerializer(data=request.data.get("mappings", []), many=True)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            mappings = []
            for item in serializer.validated_data:
                item["pattern_type"] = PatternType(item["pattern_type"])
                mappings.append(TierMapping(**item))

            registry = get_tier_registry()
            result = registry.set_mappings(mappings)

            if not result.is_valid:
                return Response(
                    {
                        "status": "error",
                        "validation": result.to_dict(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log audit
            self._log_change(request, "mappings", [m.to_dict() for m in mappings])

            return Response(
                {
                    "status": "success",
                    "mappings": [m.to_dict() for m in mappings],
                    "validation": result.to_dict(),
                    "changed_by": request.user.username,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error updating mappings: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _log_change(self, request, config_type: str, changes: Any):
        """Log configuration change."""
        try:
            from selfhealing.audit import log_config_change

            log_config_change(
                config_type=f"tiering_{config_type}",
                config_key="tier_mappings",
                old_value=None,
                new_value=changes,
                user=request.user.username,
            )
        except Exception as e:
            logger.warning(f"[TierAPI] Failed to log change: {e}")


class TierOverridesView(APIView):
    """
    Tier Overrides API.

    GET  /api/self-healing/config/tier-overrides/ - Get all tier overrides
    PUT  /api/self-healing/config/tier-overrides/ - Update tier overrides
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsViewer()]
        return [IsAuthenticated(), IsSelfHealingAdmin()]

    def get(self, request: Request) -> Response:
        """Get all tier overrides."""
        try:
            registry = get_tier_registry()
            overrides = registry.get_all_overrides()

            return Response(
                {
                    "status": "success",
                    "overrides": [o.to_dict() for o in overrides],
                    "defaults": [o.to_dict() for o in DEFAULT_TIER_OVERRIDES],
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error getting overrides: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request: Request) -> Response:
        """Update tier overrides."""
        try:
            serializer = TierOverrideSerializer(data=request.data.get("overrides", []), many=True)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            overrides = []
            for item in serializer.validated_data:
                item["identifier_type"] = OverrideIdentifierType(item["identifier_type"])
                overrides.append(TierOverride(**item))

            registry = get_tier_registry()
            result = registry.set_overrides(overrides)

            if not result.is_valid:
                return Response(
                    {
                        "status": "error",
                        "validation": result.to_dict(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log audit
            self._log_change(request, "overrides", [o.to_dict() for o in overrides])

            return Response(
                {
                    "status": "success",
                    "overrides": [o.to_dict() for o in overrides],
                    "validation": result.to_dict(),
                    "changed_by": request.user.username,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error updating overrides: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _log_change(self, request, config_type: str, changes: Any):
        """Log configuration change."""
        try:
            from selfhealing.audit import log_config_change

            log_config_change(
                config_type=f"tiering_{config_type}",
                config_key="tier_overrides",
                old_value=None,
                new_value=changes,
                user=request.user.username,
            )
        except Exception as e:
            logger.warning(f"[TierAPI] Failed to log change: {e}")


class TierDryRunView(APIView):
    """
    Tier Dry Run API.

    POST /api/self-healing/config/tiers/dry-run/ - Simulate tier changes

    Allows testing tier configuration changes without applying them.
    """

    permission_classes = [IsAuthenticated, IsOperator]

    def post(self, request: Request) -> Response:
        """
        Simulate tier configuration changes.

        Request body:
            {
                "tiers": [...],       # Optional: new tier definitions
                "mappings": [...],    # Optional: new tier mappings
                "test_paths": [...]   # Optional: paths to test
            }

        Response:
            {
                "simulation_result": {...},
                "warning": "This is a dry run. No changes applied."
            }
        """
        try:
            serializer = TierDryRunRequestSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(
                    {"status": "error", "errors": serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            data = serializer.validated_data

            # Get proposed tiers or use current
            registry = get_tier_registry()

            if data.get("tiers"):
                tiers = [TierDefinition(**item) for item in data["tiers"]]
            else:
                tiers = registry.get_all_tiers()

            # Get proposed mappings or use current
            if data.get("mappings"):
                mappings = []
                for item in data["mappings"]:
                    item["pattern_type"] = PatternType(item["pattern_type"])
                    mappings.append(TierMapping(**item))
            else:
                mappings = registry.get_all_mappings()

            # Run simulation
            result = registry.simulate(
                tiers=tiers,
                mappings=mappings,
                test_paths=data.get("test_paths"),
            )

            return Response(
                {
                    "status": "success",
                    "simulation_result": result,
                    "warning": "This is a dry run. No changes have been applied.",
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error in dry run: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TierResetView(APIView):
    """
    Tier Reset API.

    POST /api/self-healing/config/tiers/reset/ - Reset to default configuration
    """

    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Reset tier configuration to defaults."""
        try:
            registry = get_tier_registry()
            registry.reset_to_defaults()

            # Log audit
            try:
                from selfhealing.audit import log_config_change

                log_config_change(
                    config_type="tiering_reset",
                    config_key="tier_config",
                    old_value=None,
                    new_value={"action": "reset_to_defaults"},
                    user=request.user.username,
                )
            except Exception as e:
                logger.warning(f"[TierAPI] Failed to log reset: {e}")

            return Response(
                {
                    "status": "success",
                    "message": "Tier configuration reset to defaults",
                    "tiers": [t.to_dict() for t in registry.get_all_tiers()],
                    "mappings": [m.to_dict() for m in registry.get_all_mappings()],
                    "overrides": [o.to_dict() for o in registry.get_all_overrides()],
                    "changed_by": request.user.username,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error resetting tiers: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TierExportView(APIView):
    """
    Tier Export API.

    GET /api/self-healing/config/tiers/export/ - Export current configuration
    """

    permission_classes = [IsAuthenticated, IsViewer]

    def get(self, request: Request) -> Response:
        """Export current tier configuration."""
        try:
            registry = get_tier_registry()
            config = registry.export_config()

            return Response(
                {
                    "status": "success",
                    "config": config,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error exporting config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TierImportView(APIView):
    """
    Tier Import API.

    POST /api/self-healing/config/tiers/import/ - Import configuration
    """

    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        """Import tier configuration."""
        try:
            config = request.data.get("config", {})

            if not config:
                return Response(
                    {"status": "error", "error": "config is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            registry = get_tier_registry()
            result = registry.import_config(config)

            if not result.is_valid:
                return Response(
                    {
                        "status": "error",
                        "validation": result.to_dict(),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Log audit
            try:
                from selfhealing.audit import log_config_change

                log_config_change(
                    config_type="tiering_import",
                    config_key="tier_config",
                    old_value=None,
                    new_value=config,
                    user=request.user.username,
                )
            except Exception as e:
                logger.warning(f"[TierAPI] Failed to log import: {e}")

            return Response(
                {
                    "status": "success",
                    "message": "Tier configuration imported successfully",
                    "validation": result.to_dict(),
                    "config": registry.export_config(),
                    "changed_by": request.user.username,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error importing config: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TierResolveLookupView(APIView):
    """
    Tier Resolve Lookup API.

    GET /api/self-healing/config/tiers/resolve/ - Resolve tier for a path

    Useful for debugging which tier applies to a specific API path.
    """

    permission_classes = [IsAuthenticated, IsViewer]

    def get(self, request: Request) -> Response:
        """
        Resolve tier for a path.

        Query params:
            path: API path to check
            client_ip: Optional client IP for override check
            user_id: Optional user ID for override check
        """
        try:
            path = request.query_params.get("path")
            if not path:
                return Response(
                    {"status": "error", "error": "path query parameter is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            client_ip = request.query_params.get("client_ip")
            user_id = request.query_params.get("user_id")

            registry = get_tier_registry()

            # Check for override
            override_tier = registry.get_override_tier(
                client_ip=client_ip,
                user_id=user_id,
            )

            # Get path-based tier
            path_tier = registry.get_tier_for_path(path)

            # Resolve final tier
            resolved_tier = registry.resolve_tier(
                path=path,
                client_ip=client_ip,
                user_id=user_id,
            )

            return Response(
                {
                    "status": "success",
                    "path": path,
                    "resolved_tier": resolved_tier.to_dict() if resolved_tier else None,
                    "path_based_tier": path_tier.to_dict() if path_tier else None,
                    "override_tier": override_tier.to_dict() if override_tier else None,
                    "has_override": override_tier is not None,
                    "timestamp": timezone.now(),
                }
            )
        except Exception as e:
            logger.error(f"[TierAPI] Error resolving tier: {e}", exc_info=True)
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
