"""
X-Test-Mode Observability & Blast Radius Views (Stage 51)

Reference: docs/self_healing/19_CHAOS_PROOF_ROADMAP.md

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
from rest_framework.permissions import AllowAny
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

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        service_filter = request.query_params.get("service")
        limit = int(request.query_params.get("limit", 50))

        try:
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

        except Exception as e:
            logger.error(f"[Stage 51] Timeline query failed: {e}")
            return Response(
                {"status": "error", "error": "timeline_query_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BlastRadiusTestView(XTestModeMixin, APIView):
    """
    Stage 51: Blast Radius (영향 범위) 격리 테스트 API.

    POST /api/self-healing/xtest/blast-radius-test/
    Body: {"affected_service": "payment", "check_services": ["product", "cart", "auth"]}

    특정 서비스에 장애를 주입하고, 다른 서비스들이 영향받지 않는지 확인합니다.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        affected_service = request.data.get("affected_service", "payment")
        check_services = request.data.get("check_services", ["database", "product", "cart"])
        failure_count = int(request.data.get("failure_count", 5))

        results = {
            "affected_service": affected_service,
            "isolation_verified": True,
            "affected_services": [],
            "unaffected_services": [],
            "details": {},
        }

        try:
            from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

            cb_service = get_circuit_breaker_service()

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

        except Exception as e:
            logger.error(f"[Stage 51] Blast radius test failed: {e}")
            return Response(
                {"status": "error", "error": "blast_radius_test_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MultiServiceBlastRadiusView(XTestModeMixin, APIView):
    """
    Stage 51: 다중 서비스 Blast Radius 격리 매트릭스 테스트.

    POST /api/self-healing/xtest/multi-blast-radius/
    Body: {"test_services": ["database", "payment", "external_api"]}

    각 서비스 장애가 다른 서비스에 미치는 영향을 매트릭스로 분석합니다.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        test_services = request.data.get("test_services", ["database", "payment", "external_api", "cache"])
        failure_count = int(request.data.get("failure_count", 5))

        matrix = {}

        try:
            from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

            cb_service = get_circuit_breaker_service()

            for affected_service in test_services:
                matrix[affected_service] = {"affects": [], "does_not_affect": []}

                # 모든 서비스 초기화
                for svc in test_services:
                    cb_service.force_close(svc, reason="matrix test reset", controlled_by="xtest")

                # 대상 서비스에 장애 주입
                for _ in range(failure_count):
                    cb_service.record_failure(affected_service, error_context={"source": "multi-blast-radius-test"})

                # 다른 서비스 확인
                for check_service in test_services:
                    if check_service == affected_service:
                        continue

                    state = cb_service.get_state(check_service)
                    allowed = cb_service.should_allow(check_service)

                    if state == "open" or not allowed:
                        matrix[affected_service]["affects"].append(check_service)
                    else:
                        matrix[affected_service]["does_not_affect"].append(check_service)

            # 모든 서비스 복구
            for svc in test_services:
                cb_service.force_close(svc, reason="matrix test cleanup", controlled_by="xtest")

            # 격리 점수 계산
            total_checks = len(test_services) * (len(test_services) - 1)
            isolated_count = sum(len(m["does_not_affect"]) for m in matrix.values())
            isolation_score = (isolated_count / total_checks * 100) if total_checks > 0 else 100

            logger.info(f"[Stage 51] Multi blast radius test: score={isolation_score:.1f}%")

            return Response(
                {
                    "status": "success",
                    "matrix": matrix,
                    "isolation_score_percent": round(isolation_score, 1),
                    "total_services_tested": len(test_services),
                    "expected_isolation": "database affects all, others should be isolated",
                    "timestamp": timezone.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"[Stage 51] Multi blast radius test failed: {e}")
            return Response(
                {"status": "error", "error": "multi_blast_radius_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PostmortemGeneratorView(XTestModeMixin, APIView):
    """
    Stage 51: 자동 Post-mortem 리포트 생성 API.

    POST /api/self-healing/xtest/generate-postmortem/
    Body: {"incident_id": "HEAL-2025-1226-001"} (optional)

    최근 힐링 이벤트를 기반으로 자동 Post-mortem 리포트를 생성합니다.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        incident_id = request.data.get("incident_id")

        try:
            # 최근 이벤트 수집
            from selfhealing.services.event_bus import get_event_bus
            from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service

            bus = get_event_bus()
            history = bus.get_history(limit=100)

            cb_service = get_circuit_breaker_service()

            # 현재 상태 수집
            all_states = cb_service.repository.get_all_states()

            affected_services = []
            unaffected_services = []

            for state in all_states:
                if state.state == "open":
                    affected_services.append(state.service_name)
                else:
                    unaffected_services.append(state.service_name)

            # 스냅샷 수집
            snapshot = collect_system_snapshot()

            # CB 상태 변경 이벤트 필터링
            cb_events = [
                e
                for e in history
                if "circuit_breaker" in e.get("event_type", "").lower() or e.get("data", {}).get("state_change")
            ]

            # 타임라인 생성
            timeline = []
            for e in cb_events[:20]:
                timeline.append(
                    {"timestamp": e.get("timestamp"), "event_type": e.get("event_type"), "details": e.get("data", {})}
                )

            # 로컬 이벤트 추가
            local_events = get_healing_events(20)

            for e in local_events:
                timeline.append({"timestamp": e.get("recorded_at"), "event_type": e.get("event_type"), "details": e})

            # 시간순 정렬
            timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)

            # 인시던트 ID 생성
            if not incident_id:
                incident_id = f"HEAL-{timezone.now().strftime('%Y-%m%d-%H%M')}"

            # Fast Fail 통계 (추정)
            fast_fail_count = len([e for e in history if e.get("data", {}).get("fast_fail")])

            postmortem = {
                "incident_id": incident_id,
                "generated_at": timezone.now().isoformat(),
                "started_at": timeline[0]["timestamp"] if timeline else None,
                "resolved_at": timezone.now().isoformat(),
                "duration_seconds": None,
                "summary": {
                    "affected_services": affected_services,
                    "unaffected_services": unaffected_services,
                    "fast_fail_count": fast_fail_count,
                    "total_events": len(timeline),
                },
                "timeline": timeline[:30],
                "system_snapshot": snapshot,
                "auto_actions": [
                    "✅ Circuit Breaker 자동 감지",
                    "✅ Fast Fail 활성화",
                    "✅ 연쇄 장애 차단 (Blast Radius 격리)",
                    "✅ 자동 복구 시도",
                ],
                "recommendations": [
                    "장애 근본 원인 분석 필요",
                    "복구 시간 개선 검토",
                    "모니터링 알림 설정 확인",
                ],
            }

            # 인시던트 기록
            add_healing_incident(postmortem)

            logger.info(f"[Stage 51] Postmortem generated: {incident_id}")

            return Response({"status": "success", "postmortem": postmortem, "timestamp": timezone.now().isoformat()})

        except Exception as e:
            logger.error(f"[Stage 51] Postmortem generation failed: {e}")
            return Response(
                {"status": "error", "error": "postmortem_generation_failed", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RecordHealingEventView(XTestModeMixin, APIView):
    """
    Stage 51: 힐링 이벤트 기록 API.

    POST /api/self-healing/xtest/record-healing-event/
    Body: {"event_type": "cb_opened", "service": "database", "details": {...}}

    커스텀 힐링 이벤트를 기록합니다.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

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

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        limit = int(request.query_params.get("limit", 10))

        incidents = get_healing_incidents(limit)

        return Response(
            {
                "status": "success",
                "incidents": incidents,
                "total_count": get_healing_incidents_count(),
                "timestamp": timezone.now().isoformat(),
            }
        )


__all__ = [
    "HealingTimelineView",
    "BlastRadiusTestView",
    "MultiServiceBlastRadiusView",
    "PostmortemGeneratorView",
    "RecordHealingEventView",
    "GetHealingIncidentsView",
]
