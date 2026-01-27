"""
Self-Healing Dashboard Views.

REST API endpoints for monitoring dashboard.

Endpoints:
- GET /api/self-healing/dashboard/summary/ - Get system summary statistics

Note: Business logic has been extracted to DashboardService.
See: services/dashboard_service.py
"""

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsViewer
from selfhealing.services.dashboard_service import get_dashboard_service

logger = logging.getLogger(__name__)


class DashboardSummaryView(APIView):
    """
    Dashboard Summary API.

    GET /api/self-healing/dashboard/summary/

    Returns a comprehensive summary of the self-healing system status.

    Note: Read-only endpoint - Viewer role or higher can access.
    """

    permission_classes = [IsAuthenticated, IsViewer]

    def get(self, request):
        """Get dashboard summary statistics."""
        service = get_dashboard_service()
        summary = service.get_summary()
        return Response(summary.to_dict())
        # Exception은 exception handler가 처리
