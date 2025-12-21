"""
Self-Healing Dashboard Views.

REST API endpoints for monitoring dashboard.

Endpoints:
- GET /api/self-healing/dashboard/summary/ - Get system summary statistics

Note: Business logic has been extracted to DashboardService.
See: services/dashboard_service.py
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.dashboard_service import get_dashboard_service

logger = logging.getLogger(__name__)


class DashboardSummaryView(APIView):
    """
    Dashboard Summary API.

    GET /api/self-healing/dashboard/summary/

    Returns a comprehensive summary of the self-healing system status.
    
    Note: Read-only endpoint - all authenticated users can view.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Get dashboard summary statistics."""
        try:
            service = get_dashboard_service()
            summary = service.get_summary()
            return Response(summary.to_dict())

        except Exception as e:
            logger.error(f"[Dashboard] Summary failed: {e}")
            return Response(
                {"status": "error", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
