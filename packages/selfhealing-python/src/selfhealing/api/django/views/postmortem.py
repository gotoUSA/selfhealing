"""
Post-mortem API Views.

실제 프로덕션 장애에 대한 Post-mortem 리포트 생성 및 조회 API.

Google SRE 표준에 따라 실제 인시던트를 문서화하고 재발 방지를 위한 분석을 제공합니다.
X-Test 모듈과 분리되어 인증된 사용자가 직접 접근할 수 있습니다.

Endpoints:
- POST /api/self-healing/postmortem/generate/ - Post-mortem 리포트 생성
- GET  /api/self-healing/postmortem/incidents/ - 인시던트 목록 조회

Security:
- IsAuthenticated 권한 필요 (프로덕션 허용)
- X-Test 헤더 불필요
"""

import structlog

from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.postmortem_store import (
    add_healing_incident,
    get_healing_incidents,
    get_healing_incidents_count,
    get_incident_by_id,
    # Helper functions
    collect_service_states as _collect_service_states,
    build_timeline as _build_timeline,
    generate_postmortem_data as _generate_postmortem_data,
)

logger = structlog.get_logger()


# =============================================================================
# Post-mortem API Views
# =============================================================================


class PostmortemGeneratorView(APIView):
    """
    Post-mortem 리포트 생성 API.

    POST /api/self-healing/postmortem/generate/
    Body: {"incident_id": "HEAL-2025-1226-001"} (optional)

    실제 프로덕션 장애에 대해 최근 힐링 이벤트를 기반으로
    자동 Post-mortem 리포트를 생성합니다.

    Google SRE 표준에 따라:
    - trigger: 장애 발생 원인
    - detection: 감지 방법 및 시점
    - resolution: 해결 방법
    - root_cause_hypothesis: 근본 원인 가설
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        """Post-mortem 리포트 생성."""
        incident_id = request.data.get("incident_id")

        from selfhealing.api.django.views.xtest.base import (
            collect_system_snapshot,
            get_healing_events,
        )
        from selfhealing.services.circuit_breaker_service import (
            get_circuit_breaker_service,
        )
        from selfhealing.services.event_bus import get_event_bus

        bus = get_event_bus()
        history_limit = self._get_postmortem_history_limit()
        history = bus.get_history(limit=history_limit)
        cb_service = get_circuit_breaker_service()

        affected, unaffected = _collect_service_states(cb_service)
        snapshot = collect_system_snapshot()
        local_events = get_healing_events(20)
        timeline = _build_timeline(history, local_events)

        if not incident_id:
            incident_id = f"HEAL-{timezone.now().strftime('%Y-%m%d-%H%M')}"

        fast_fail_count = len([e for e in history if e.get("data", {}).get("fast_fail")])

        postmortem = _generate_postmortem_data(incident_id, timeline, affected, unaffected, fast_fail_count, snapshot)

        add_healing_incident(postmortem)

        logger.info(
            "postmortem.postmortem_generated",
            incident_id=incident_id,
        )

        # Audit 기록: 수동 Post-mortem 생성
        self._log_postmortem_audit(
            incident_id=incident_id,
            affected_services=affected,
            duration_seconds=postmortem.get("duration_seconds"),
            user=str(request.user) if request.user.is_authenticated else "anonymous",
        )

        return Response(
            {
                "status": "success",
                "postmortem": postmortem,
                "timestamp": timezone.now().isoformat(),
            }
        )

    @staticmethod
    def _log_postmortem_audit(
        incident_id: str,
        affected_services: list,
        duration_seconds: float | None,
        user: str,
    ) -> None:
        """수동 Post-mortem 생성에 대한 Audit 기록."""
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="POSTMORTEM_MANUAL_GENERATED",
                source="API.Postmortem",
                details={
                    "incident_id": incident_id,
                    "affected_services": affected_services,
                    "duration_seconds": duration_seconds,
                    "triggered_by": user,
                },
                success=True,
                domain="selfhealing",
                target_id=incident_id,
            )
        except Exception as e:
            logger.warning(
                "postmortem.failed_log_audit",
                error=e,
            )

    @staticmethod
    def _get_postmortem_history_limit() -> int:
        """Settings에서 postmortem_history_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings

            return get_api_view_settings().postmortem_history_limit
        except Exception:
            return 100  # 기본값


class GetHealingIncidentsView(APIView):
    """
    Post-mortem 인시던트 목록 조회 API.

    GET /api/self-healing/postmortem/incidents/?limit=10&offset=0
    GET /api/self-healing/postmortem/incidents/?service=payment&min_duration=60

    Query Parameters:
    - limit: 반환할 최대 개수 (default: 10)
    - offset: 페이지네이션 오프셋 (default: 0)
    - start_date: 시작 날짜 필터 (ISO format)
    - end_date: 종료 날짜 필터 (ISO format)
    - service: 서비스 이름 필터
    - min_duration: 최소 지속 시간 필터 (초)
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        """Post-mortem 인시던트 목록 조회."""
        default_limit = self._get_incidents_default_limit()
        limit = int(request.query_params.get("limit", default_limit))
        offset = int(request.query_params.get("offset", 0))
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        service = request.query_params.get("service")
        min_duration_str = request.query_params.get("min_duration")
        min_duration = float(min_duration_str) if min_duration_str else None

        incidents = get_healing_incidents(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            service=service,
            min_duration=min_duration,
        )

        total_count = get_healing_incidents_count(
            start_date=start_date,
            end_date=end_date,
            service=service,
            min_duration=min_duration,
        )

        return Response(
            {
                "status": "success",
                "incidents": incidents,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "timestamp": timezone.now().isoformat(),
            }
        )

    @staticmethod
    def _get_incidents_default_limit() -> int:
        """Settings에서 postmortem_incidents_default_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings

            return get_api_view_settings().postmortem_incidents_default_limit
        except Exception:
            return 10  # 기본값


class PostmortemDetailView(APIView):
    """
    단일 Post-mortem 인시던트 상세 조회 API.

    GET /api/self-healing/postmortem/incidents/{incident_id}/

    URL Parameters:
    - incident_id: 조회할 인시던트 ID
    """

    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, incident_id: str) -> Response:
        """단일 Post-mortem 인시던트 상세 조회."""
        incident = get_incident_by_id(incident_id)

        if incident is None:
            return Response(
                {
                    "status": "error",
                    "error": "incident_not_found",
                    "message": f"Incident with ID '{incident_id}' not found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "status": "success",
                "incident": incident,
                "timestamp": timezone.now().isoformat(),
            }
        )


__all__ = [
    "PostmortemGeneratorView",
    "GetHealingIncidentsView",
    "PostmortemDetailView",
]
