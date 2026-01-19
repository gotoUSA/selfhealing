"""
Chaos Engineering Configuration Views.

API views for SafetyGuard, BlastRadius, Scheduler, and Report configuration.
"""

import logging
from typing import List

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer, IsSelfHealingAdmin
from selfhealing.api.django.serializers.chaos import (
    SafetyGuardConfigSerializer,
    BlastRadiusPolicySerializer,
    SchedulerConfigSerializer,
    ReportConfigSerializer,
)

logger = logging.getLogger(__name__)


class SafetyGuardConfigView(APIView):
    """
    API for SafetyGuard configuration.
    
    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """
    
    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """Get current SafetyGuard configuration."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        guard = get_safety_guard()
        config = guard.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update SafetyGuard configuration."""
        from selfhealing.services.chaos.safety_guard import get_safety_guard
        
        serializer = SafetyGuardConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        guard = get_safety_guard()
        updated_config = guard.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] SafetyGuard config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })


class BlastRadiusPolicyView(APIView):
    """
    API for BlastRadius policy configuration.
    
    GET: Retrieve current policy (Viewer)
    PATCH: Update policy (Admin)
    """
    
    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """Get current BlastRadius policy."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        manager = get_blast_radius_manager()
        policy = manager.get_policy()
        
        return Response({
            "status": "success",
            "data": policy.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update BlastRadius policy."""
        from selfhealing.services.chaos.blast_radius import get_blast_radius_manager
        
        serializer = BlastRadiusPolicySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        manager = get_blast_radius_manager()
        updated_policy = manager.update_policy(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] BlastRadius policy updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_policy.to_dict(),
        })


class SchedulerConfigView(APIView):
    """
    API for ChaosScheduler configuration.
    
    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """
    
    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """Get current scheduler configuration."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        scheduler = get_chaos_scheduler()
        config = scheduler.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update scheduler configuration."""
        from selfhealing.services.chaos.scheduler import get_chaos_scheduler
        
        serializer = SchedulerConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        scheduler = get_chaos_scheduler()
        updated_config = scheduler.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Scheduler config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })


class ReportConfigView(APIView):
    """
    API for ResilienceReport configuration.
    
    GET: Retrieve current configuration (Viewer)
    PATCH: Update configuration (Admin)
    """
    
    def get_permissions(self) -> List[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]
    
    def get(self, request: Request) -> Response:
        """Get current report configuration."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        generator = get_report_generator()
        config = generator.get_config()
        
        return Response({
            "status": "success",
            "data": config.to_dict(),
        })
    
    def patch(self, request: Request) -> Response:
        """Update report configuration."""
        from selfhealing.services.chaos.reports import get_report_generator
        
        serializer = ReportConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        generator = get_report_generator()
        updated_config = generator.update_config(**serializer.validated_data)
        
        logger.info(f"[ChaosAPI] Report config updated by {request.user}")
        
        return Response({
            "status": "success",
            "data": updated_config.to_dict(),
        })
