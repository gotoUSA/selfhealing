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

import logging

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
)

logger = logging.getLogger(__name__)


# =============================================================================
# Post-mortem Generation Helpers
# =============================================================================


def _collect_service_states(cb_service) -> tuple[list, list]:
    """Collect affected and unaffected services from CB states."""
    all_states = cb_service.repository.get_all_states()
    affected = [s.service_name for s in all_states if s.state == "open"]
    unaffected = [s.service_name for s in all_states if s.state != "open"]
    return affected, unaffected


def _build_timeline(history: list, local_events: list) -> list:
    """Build sorted timeline from history and local events."""
    timeline = []

    # CB 상태 변경 이벤트 필터링
    cb_events = [
        e for e in history if "circuit_breaker" in e.get("event_type", "").lower() or e.get("data", {}).get("state_change")
    ]

    for e in cb_events[:20]:
        timeline.append(
            {
                "timestamp": e.get("timestamp"),
                "event_type": e.get("event_type"),
                "details": e.get("data", {}),
            }
        )

    for e in local_events:
        timeline.append(
            {
                "timestamp": e.get("recorded_at"),
                "event_type": e.get("event_type"),
                "details": e,
            }
        )

    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
    return timeline


def _generate_postmortem_data(
    incident_id: str,
    timeline: list,
    affected: list,
    unaffected: list,
    fast_fail_count: int,
    snapshot: dict,
) -> dict:
    """Generate postmortem data structure with dynamic calculations.

    Google SRE 표준에 맞춰 trigger, detection, resolution, root_cause_hypothesis 필드 포함.
    """
    from selfhealing.utils.duration import calculate_incident_duration

    from selfhealing.utils.postmortem_actions import generate_dynamic_actions
    from selfhealing.utils.postmortem_root_cause import build_postmortem_root_cause_fields

    current_time = timezone.now().isoformat()

    # duration 계산 (세분화된 정보 포함)
    duration_result = calculate_incident_duration(timeline, current_time)

    # 동적 action items 생성
    auto_actions, recommendations = generate_dynamic_actions(
        timeline=timeline,
        affected_services=affected,
        duration_seconds=duration_result.duration_seconds,
        current_timestamp=current_time,
    )

    # Root cause 관련 필드 추출
    root_cause_fields = build_postmortem_root_cause_fields(timeline, affected)

    return {
        "incident_id": incident_id,
        "generated_at": current_time,
        "started_at": duration_result.started_at,
        "resolved_at": duration_result.resolved_at,
        "duration_seconds": duration_result.duration_seconds,
        "downtime_seconds": duration_result.downtime_seconds,
        "validation_seconds": duration_result.validation_seconds,
        # Google SRE 표준 필드 (trigger, detection, resolution, root_cause_hypothesis)
        "trigger": root_cause_fields.get("trigger"),
        "detection": root_cause_fields.get("detection"),
        "resolution": root_cause_fields.get("resolution"),
        "root_cause_hypothesis": root_cause_fields.get("root_cause_hypothesis"),
        "summary": {
            "affected_services": affected,
            "unaffected_services": unaffected,
            "fast_fail_count": fast_fail_count,
            "total_events": len(timeline),
        },
        "timeline": timeline[:30],
        "system_snapshot": snapshot,
        "auto_actions": auto_actions,
        "recommendations": recommendations,
    }


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

        logger.info(f"[Postmortem] Postmortem generated: {incident_id}")

        return Response(
            {
                "status": "success",
                "postmortem": postmortem,
                "timestamp": timezone.now().isoformat(),
            }
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


__all__ = [
    "PostmortemGeneratorView",
    "GetHealingIncidentsView",
]
