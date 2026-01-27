"""
X-Test-Mode Observability & Blast Radius Views (Stage 51)

Stage 51 Observability 관련 API:
- HealingTimelineView: 힐링 타임라인 조회
- BlastRadiusTestView: 단일 서비스 Blast Radius 격리 테스트
- MultiServiceBlastRadiusView: 다중 서비스 격리 매트릭스 테스트
- PostmortemGeneratorView: Post-mortem 자동 생성
- RecordHealingEventView: 힐링 이벤트 기록
- GetHealingIncidentsView: 인시던트 목록 조회
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .base import (
    XTestModeMixin,
    add_healing_event,
    add_healing_incident,
    collect_system_snapshot,
    get_healing_events,
    get_healing_events_count,
    get_healing_incidents,
    get_healing_incidents_count,
)

logger = logging.getLogger(__name__)


class HealingTimelineView(XTestModeMixin, APIView):
    """
    Stage 51: Self-Healing 타임라인 조회 API.

    GET /api/self-healing/xtest/healing-timeline/?service=database&limit=50

    장애 감지, CB 상태 변경, 복구 등의 이벤트 타임라인을 조회합니다.
    """

    @staticmethod
    def _get_timeline_default_limit() -> int:
        """Settings에서 timeline_default_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings
            return get_api_view_settings().xtest_timeline_default_limit
        except Exception:
            return 50  # 기본값

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        service_filter = request.query_params.get("service")
        default_limit = self._get_timeline_default_limit()
        limit = int(request.query_params.get("limit", default_limit))

        # 이벤트 버스에서 히스토리 조회
        from selfhealing.services.event_bus import get_event_bus

        bus = get_event_bus()
        history = bus.get_history(limit=limit)

        # 로컬 이벤트 추가
        local_events = get_healing_events(limit)

        # 필터링
        if service_filter:
            history = [
                e
                for e in history
                if e.get("data", {}).get("service") == service_filter
                or e.get("data", {}).get("service_name") == service_filter
            ]
            local_events = [e for e in local_events if e.get("service") == service_filter]

        # CB 상태 정보 추가
        from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

        cb_service = get_circuit_breaker_service()

        cb_states = {}
        all_states = cb_service.repository.get_all_states()
        for state in all_states:
            cb_states[state.service_name] = {
                "state": state.state,
                "failure_count": state.failure_count,
                "success_count": getattr(state, "success_count", 0),
                "opened_at": str(getattr(state, "opened_at", None)),
            }

        return Response(
            {
                "status": "success",
                "service_filter": service_filter,
                "event_bus_events": history,
                "local_events": local_events,
                "current_cb_states": cb_states,
                "total_events": len(history) + len(local_events),
                "timestamp": timezone.now().isoformat(),
            }
        )


class BlastRadiusTestView(XTestModeMixin, APIView):
    """
    Stage 51: Blast Radius (영향 범위) 격리 테스트 API.

    POST /api/self-healing/xtest/blast-radius-test/
    Body: {"affected_service": "service_a", "check_services": ["service_b", "service_c"]}

    특정 서비스에 장애를 주입하고, 다른 서비스들이 영향받지 않는지 확인합니다.
    
    - affected_service: 장애를 주입할 서비스 (필수)
    - check_services: 영향 확인할 서비스 목록 (생략 시 CB에 등록된 모든 서비스)
    """

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        affected_service = request.data.get("affected_service")
        if not affected_service:
            return Response(
                {"error": "affected_service is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        check_services = request.data.get("check_services", [])
        failure_count = int(request.data.get("failure_count", 5))

        results = {
            "affected_service": affected_service,
            "isolation_verified": True,
            "affected_services": [],
            "unaffected_services": [],
            "details": {},
        }

        from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

        cb_service = get_circuit_breaker_service()

        # check_services가 비어있으면 CB에 등록된 모든 서비스 조회
        if not check_services:
            all_states = cb_service.repository.get_all_states()
            check_services = [s.service_name for s in all_states if s.service_name != affected_service]

        # Step 1: 대상 서비스에 장애 주입
        for _ in range(failure_count):
            cb_service.record_failure(affected_service, error_context={"source": "blast-radius-test"})

        affected_state = cb_service.get_state(affected_service)
        results["affected_service_state"] = affected_state
        results["affected_services"].append(affected_service)

        # 이벤트 기록
        add_healing_event(
            {
                "event_type": "blast_radius_test_started",
                "service": affected_service,
                "failure_count": failure_count,
                "check_services": check_services,
            }
        )

        # Step 2: 다른 서비스들의 상태 확인
        for service in check_services:
            if service == affected_service:
                continue

            service_state = cb_service.get_state(service)
            allowed = cb_service.should_allow(service)

            results["details"][service] = {
                "state": service_state,
                "allowed": allowed,
                "isolated": service_state != "open" and allowed,
            }

            if service_state == "open" or not allowed:
                results["isolation_verified"] = False
                results["affected_services"].append(service)
            else:
                results["unaffected_services"].append(service)

        # Step 3: 결과 스냅샷 저장
        snapshot = collect_system_snapshot()

        # 이벤트 기록
        add_healing_event(
            {
                "event_type": "blast_radius_test_completed",
                "service": affected_service,
                "isolation_verified": results["isolation_verified"],
                "affected_count": len(results["affected_services"]),
                "unaffected_count": len(results["unaffected_services"]),
            }
        )

        # Step 4: 대상 서비스 복구 (테스트 종료)
        cb_service.force_close(affected_service, reason="Blast radius test cleanup", controlled_by="xtest")

        logger.info(
            f"[Stage 51] Blast radius test: {affected_service} → "
            f"isolated={results['isolation_verified']}, "
            f"unaffected={len(results['unaffected_services'])}"
        )

        return Response({"status": "success", **results, "snapshot": snapshot, "timestamp": timezone.now().isoformat()})


# =============================================================================
# Multi-Service Blast Radius Helpers (Complexity Reduction)
# =============================================================================


def _get_test_services(cb_service, requested_services: list) -> list:
    """테스트할 서비스 목록 조회. 비어있으면 CB에 등록된 모든 서비스 반환."""
    if requested_services:
        return requested_services
    all_states = cb_service.repository.get_all_states()
    return [s.service_name for s in all_states]


def _reset_all_services(cb_service, services: list, reason: str) -> None:
    """모든 서비스를 CLOSED 상태로 리셋."""
    for svc in services:
        cb_service.force_close(svc, reason=reason, controlled_by="xtest")


def _inject_failures(cb_service, service: str, failure_count: int, source: str) -> None:
    """특정 서비스에 장애 주입."""
    for _ in range(failure_count):
        cb_service.record_failure(service, error_context={"source": source})


def _check_service_isolation(cb_service, affected_service: str, check_service: str) -> bool:
    """다른 서비스가 영향 받았는지 확인. True면 격리됨(영향 없음)."""
    state = cb_service.get_state(check_service)
    allowed = cb_service.should_allow(check_service)
    return state != "open" and allowed


def _build_isolation_matrix(
    cb_service,
    test_services: list,
    failure_count: int,
) -> dict:
    """각 서비스별 영향 매트릭스 구성."""
    matrix = {}
    
    for affected_service in test_services:
        matrix[affected_service] = {"affects": [], "does_not_affect": []}

        # 모든 서비스 초기화
        _reset_all_services(cb_service, test_services, "matrix test reset")

        # 대상 서비스에 장애 주입
        _inject_failures(cb_service, affected_service, failure_count, "multi-blast-radius-test")

        # 다른 서비스 확인
        for check_service in test_services:
            if check_service == affected_service:
                continue

            if _check_service_isolation(cb_service, affected_service, check_service):
                matrix[affected_service]["does_not_affect"].append(check_service)
            else:
                matrix[affected_service]["affects"].append(check_service)
    
    return matrix


def _calculate_isolation_score(matrix: dict, total_services: int) -> float:
    """격리 점수 계산 (백분율)."""
    total_checks = total_services * (total_services - 1)
    if total_checks == 0:
        return 100.0
    isolated_count = sum(len(m["does_not_affect"]) for m in matrix.values())
    return isolated_count / total_checks * 100


class MultiServiceBlastRadiusView(XTestModeMixin, APIView):
    """
    Stage 51: 다중 서비스 Blast Radius 격리 매트릭스 테스트.

    POST /api/self-healing/xtest/multi-blast-radius/
    Body: {"test_services": ["service_a", "service_b", "service_c"]}

    각 서비스 장애가 다른 서비스에 미치는 영향을 매트릭스로 분석합니다.
    
    - test_services: 테스트할 서비스 목록 (생략 시 CB에 등록된 모든 서비스)
    """

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        requested_services = request.data.get("test_services", [])
        failure_count = int(request.data.get("failure_count", 5))

        from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service
        cb_service = get_circuit_breaker_service()

        # 테스트할 서비스 목록 조회
        test_services = _get_test_services(cb_service, requested_services)
        
        if len(test_services) < 2:
            return Response(
                {"error": "At least 2 services required for matrix test"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 매트릭스 구성
        matrix = _build_isolation_matrix(cb_service, test_services, failure_count)

        # 모든 서비스 복구
        _reset_all_services(cb_service, test_services, "matrix test cleanup")

        # 격리 점수 계산
        isolation_score = _calculate_isolation_score(matrix, len(test_services))

        logger.info(f"[Stage 51] Multi blast radius test: score={isolation_score:.1f}%")

        return Response(
            {
                "status": "success",
                "matrix": matrix,
                "isolation_score_percent": round(isolation_score, 1),
                "total_services_tested": len(test_services),
                "timestamp": timezone.now().isoformat(),
            }
        )


# =============================================================================
# Postmortem Generation Helpers (Complexity Reduction)
# =============================================================================

def _collect_service_states(cb_service) -> tuple[list, list]:
    """Collect affected and unaffected services from CB states."""
    all_states = cb_service.repository.get_all_states()
    affected = [s.service_name for s in all_states if s.state == "open"]
    unaffected = [s.service_name for s in all_states if s.state != "open"]
    return affected, unaffected


def _calculate_incident_duration(timeline: list) -> tuple[str | None, str | None, float | None]:
    """
    타임라인에서 인시던트 시작/종료 시점 및 지속 시간 계산.
    
    Returns:
        tuple: (started_at, resolved_at, duration_seconds)
    """
    if not timeline:
        return None, timezone.now().isoformat(), None
    
    from datetime import datetime
    
    started_at = None
    resolved_at = None
    
    # 첫 번째 CB OPEN 이벤트 찾기
    for event in timeline:
        event_type = event.get("event_type", "").lower()
        if "opened" in event_type or "open" in event_type:
            started_at = event.get("timestamp")
            break
    
    # OPEN 이벤트가 없으면 첫 번째 이벤트 사용
    if not started_at and timeline:
        started_at = timeline[0].get("timestamp")
    
    # 마지막 CB CLOSED 이벤트 찾기
    for event in reversed(timeline):
        event_type = event.get("event_type", "").lower()
        if "closed" in event_type:
            resolved_at = event.get("timestamp")
            break
    
    # CLOSED 이벤트가 없으면 현재 시각 사용
    if not resolved_at:
        resolved_at = timezone.now().isoformat()
    
    # duration 계산
    duration_seconds = None
    if started_at and resolved_at:
        try:
            # ISO 형식 파싱
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
            duration_seconds = (end_dt - start_dt).total_seconds()
            if duration_seconds < 0:
                duration_seconds = None
        except (ValueError, TypeError):
            pass
    
    return started_at, resolved_at, duration_seconds


def _generate_dynamic_actions(
    timeline: list,
    affected_services: list,
    duration_seconds: float | None,
) -> tuple[list, list]:
    """
    타임라인과 분석 결과를 기반으로 동적 action items 및 recommendations 생성.
    
    Returns:
        tuple: (auto_actions, recommendations)
    """
    auto_actions = []
    recommendations = []
    
    # 이벤트 타입별 액션 매핑
    action_map = {
        "circuit_breaker_opened": "Circuit Breaker OPEN 전환",
        "circuit_breaker_half_opened": "Circuit Breaker 복구 시도 (HALF_OPEN)",
        "circuit_breaker_closed": "Circuit Breaker 정상 복구 (CLOSED)",
        "error_budget_critical": "Error Budget 임계치 경고",
        "error_budget_exhausted": "Error Budget 소진",
        "emergency_activated": "비상 모드 활성화",
        "kill_switch_activated": "Kill Switch 활성화",
    }
    
    seen_actions = set()
    
    for event in timeline:
        event_type = event.get("event_type", "").lower()
        service = event.get("details", {}).get("service_name", "")
        timestamp = event.get("timestamp", "")
        
        for key, action_text in action_map.items():
            if key in event_type and (key, service) not in seen_actions:
                seen_actions.add((key, service))
                auto_actions.append({
                    "action": action_text,
                    "status": "completed",
                    "timestamp": timestamp,
                    "service": service,
                })
                break
    
    # 액션이 없으면 기본 메시지
    if not auto_actions:
        auto_actions.append({
            "action": "인시던트 기록됨",
            "status": "completed",
            "timestamp": timezone.now().isoformat(),
            "service": None,
        })
    
    # Recommendations 생성
    if duration_seconds is not None:
        if duration_seconds > 120:
            recommendations.append(
                f"복구 시간이 {duration_seconds:.0f}초로 2분을 초과함 - SLA 검토 필요"
            )
        elif duration_seconds > 60:
            recommendations.append(
                f"복구 시간이 {duration_seconds:.0f}초로 목표(60초) 초과 - 개선 검토 권장"
            )
    
    if len(affected_services) > 3:
        recommendations.append(
            f"다중 서비스 장애 ({len(affected_services)}개) - 공통 원인 분석 필요"
        )
    
    if not recommendations:
        recommendations.append("장애 근본 원인 분석 및 재발 방지 검토 권장")
    
    return auto_actions, recommendations


def _build_timeline(history: list, local_events: list) -> list:
    """Build sorted timeline from history and local events."""
    timeline = []
    
    # CB 상태 변경 이벤트 필터링
    cb_events = [
        e for e in history
        if "circuit_breaker" in e.get("event_type", "").lower() 
        or e.get("data", {}).get("state_change")
    ]
    
    for e in cb_events[:20]:
        timeline.append({
            "timestamp": e.get("timestamp"),
            "event_type": e.get("event_type"),
            "details": e.get("data", {}),
        })
    
    for e in local_events:
        timeline.append({
            "timestamp": e.get("recorded_at"),
            "event_type": e.get("event_type"),
            "details": e,
        })
    
    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
    return timeline


def _generate_postmortem_data(
    incident_id: str, timeline: list, affected: list, 
    unaffected: list, fast_fail_count: int, snapshot: dict
) -> dict:
    """Generate postmortem data structure with dynamic calculations."""
    # duration 계산
    started_at, resolved_at, duration_seconds = _calculate_incident_duration(timeline)
    
    # 동적 action items 생성
    auto_actions, recommendations = _generate_dynamic_actions(
        timeline, affected, duration_seconds
    )
    
    return {
        "incident_id": incident_id,
        "generated_at": timezone.now().isoformat(),
        "started_at": started_at,
        "resolved_at": resolved_at,
        "duration_seconds": duration_seconds,
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


class PostmortemGeneratorView(XTestModeMixin, APIView):
    """
    Stage 51: 자동 Post-mortem 리포트 생성 API.

    POST /api/self-healing/xtest/generate-postmortem/
    Body: {"incident_id": "HEAL-2025-1226-001"} (optional)

    최근 힐링 이벤트를 기반으로 자동 Post-mortem 리포트를 생성합니다.
    """


    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        # Exception은 exception handler가 처리
        incident_id = request.data.get("incident_id")

        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

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

        postmortem = _generate_postmortem_data(
            incident_id, timeline, affected, unaffected, fast_fail_count, snapshot
        )

        add_healing_incident(postmortem)

        logger.info(f"[Stage 51] Postmortem generated: {incident_id}")

        return Response({"status": "success", "postmortem": postmortem, "timestamp": timezone.now().isoformat()})

    @staticmethod
    def _get_postmortem_history_limit() -> int:
        """Settings에서 postmortem_history_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings
            return get_api_view_settings().xtest_postmortem_history_limit
        except Exception:
            return 100  # 기본값


class RecordHealingEventView(XTestModeMixin, APIView):
    """
    Stage 51: 힐링 이벤트 기록 API.

    POST /api/self-healing/xtest/record-healing-event/
    Body: {"event_type": "cb_opened", "service": "my_service", "details": {...}}

    커스텀 힐링 이벤트를 기록합니다.
    """


    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        event_type = request.data.get("event_type", "custom_event")
        service = request.data.get("service")
        details = request.data.get("details", {})

        event = {
            "event_type": event_type,
            "service": service,
            "details": details,
            "source": "xtest-api",
            "timestamp": timezone.now().isoformat(),
        }

        # 스냅샷 추가 (옵션)
        if request.data.get("include_snapshot", False):
            event["snapshot"] = collect_system_snapshot()

        add_healing_event(event)

        logger.info(f"[Stage 51] Healing event recorded: {event_type} ({service})")

        return Response(
            {
                "status": "success",
                "event": event,
                "total_events": get_healing_events_count(),
                "timestamp": timezone.now().isoformat(),
            }
        )


class GetHealingIncidentsView(XTestModeMixin, APIView):
    """
    Stage 51: 힐링 인시던트 목록 조회 API.

    GET /api/self-healing/xtest/healing-incidents/?limit=10
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        default_limit = self._get_incidents_default_limit()
        limit = int(request.query_params.get("limit", default_limit))

        incidents = get_healing_incidents(limit)

        return Response(
            {
                "status": "success",
                "incidents": incidents,
                "total_count": get_healing_incidents_count(),
                "timestamp": timezone.now().isoformat(),
            }
        )

    @staticmethod
    def _get_incidents_default_limit() -> int:
        """Settings에서 incidents_default_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings
            return get_api_view_settings().xtest_incidents_default_limit
        except Exception:
            return 10  # 기본값


__all__ = [
    "HealingTimelineView",
    "BlastRadiusTestView",
    "MultiServiceBlastRadiusView",
    "PostmortemGeneratorView",
    "RecordHealingEventView",
    "GetHealingIncidentsView",
]
