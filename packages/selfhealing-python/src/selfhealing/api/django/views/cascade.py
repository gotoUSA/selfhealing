"""
Cascade Event Audit API Views.

연계 이벤트 감사 추적 API.
Emergency Level 변경 시 발생하는 연쇄 효과를 인과관계와 함께 조회합니다.

Endpoints:
- GET  /api/self-healing/cascade/events/              - Cascade Event 목록 조회
- GET  /api/self-healing/cascade/events/<cascade_id>/ - Cascade Event 상세 조회
- POST /api/self-healing/cascade/verify/              - Hash Chain 무결성 검증
- GET  /api/self-healing/cascade/trace/<event_id>/    - 특정 이벤트 인과관계 추적

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

import structlog
from datetime import datetime, timezone

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer

logger = structlog.get_logger()


def _get_cascade_auditor():
    """CascadeEventAuditor 싱글턴 획득."""
    from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

    return get_cascade_event_auditor()


class CascadeEventListView(APIView):
    """
    GET /api/self-healing/cascade/events/

    Cascade Event 목록 조회.

    Query Parameters:
        - namespace: 네임스페이스 필터 (기본: global)
        - limit: 최대 조회 개수 (기본: 100, 최대: 1000)
        - offset: 시작 위치 (기본: 0)
        - trigger_type: 트리거 유형 필터 (선택)
        - is_test: 테스트 데이터 필터 (true/false, 생략 시 전체)

    Response:
        {
            "success": true,
            "namespace": "seoul",
            "events": [
                {
                    "id": "cascade-abc123",
                    "timestamp": "2026-01-23T15:30:00Z",
                    "trigger_type": "EMERGENCY_LEVEL_CHANGED",
                    "effects_count": 3,
                    "namespace": "seoul",
                    "is_test": false
                },
                ...
            ],
            "total": 150,
            "limit": 100,
            "offset": 0
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        namespace = request.query_params.get("namespace", "global")
        limit = min(int(request.query_params.get("limit", 100)), 1000)
        offset = int(request.query_params.get("offset", 0))
        trigger_type = request.query_params.get("trigger_type")
        is_test_param = request.query_params.get("is_test")

        # Exception은 exception handler가 처리
        auditor = _get_cascade_auditor()
        events = auditor.get_recent_events(
            namespace=namespace,
            limit=limit + offset,  # offset 고려
        )

        # offset 적용
        events = events[offset : offset + limit]

        # 트리거 유형 필터
        if trigger_type:
            events = [e for e in events if e.trigger.trigger_type == trigger_type]

        # is_test 필터 (true/false 문자열 → bool 변환)
        if is_test_param is not None:
            is_test_filter = is_test_param.lower() == "true"
            events = [e for e in events if e.is_test == is_test_filter]

        # 응답 형식 변환
        event_list = []
        for event in events:
            event_list.append(
                {
                    "id": event.id,
                    "timestamp": event.timestamp,
                    "trigger_type": event.trigger.trigger_type,
                    "trigger_details": event.trigger.details,
                    "effects_count": len(event.effects),
                    "namespace": event.namespace,
                    "has_external_trace": event.external_trace is not None,
                    "is_test": event.is_test,
                }
            )

        # 전체 개수 조회
        total = auditor.get_event_count(namespace)

        return Response(
            {
                "success": True,
                "namespace": namespace,
                "events": event_list,
                "total": total,
                "limit": limit,
                "offset": offset,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class CascadeEventDetailView(APIView):
    """
    GET /api/self-healing/cascade/events/<cascade_id>/

    Cascade Event 상세 조회.

    Path Parameters:
        - cascade_id: Cascade Event ID

    Query Parameters:
        - namespace: 네임스페이스 (기본: global)

    Response:
        {
            "success": true,
            "event": {
                "id": "cascade-abc123",
                "timestamp": "2026-01-23T15:30:00Z",
                "namespace": "seoul",
                "trigger": {
                    "type": "EMERGENCY_LEVEL_CHANGED",
                    "event_id": "evt-001",
                    "details": {
                        "old_level": "NORMAL",
                        "new_level": "LEVEL_3",
                        "transition_type": "ACTIVATION"
                    },
                    "triggered_by": "system"
                },
                "effects": [
                    {
                        "action_type": "governance_strict",
                        "event_id": "effect-001",
                        "success": true,
                        "caused_by": "evt-001",
                        "details": {}
                    },
                    ...
                ],
                "causation_chain": ["evt-001", "effect-001", "effect-002"],
                "external_trace": {
                    "trace_id": "abc123...",
                    "span_id": "def456...",
                    "request_id": "req-789"
                },
                "hash_chain": {
                    "previous_hash": "...",
                    "current_hash": "..."
                }
            }
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, cascade_id: str) -> Response:
        namespace = request.query_params.get("namespace", "global")

        # Exception은 exception handler가 처리
        auditor = _get_cascade_auditor()
        event = auditor.get_cascade_event(cascade_id, namespace)

        if not event:
            from django.http import Http404

            raise Http404(f"Cascade Event {cascade_id} not found")

        # 효과 목록 변환
        effects_list = []
        for effect in event.effects:
            effect_dict = {
                "action_type": effect.action_type,
                "event_id": effect.event_id,
                "success": effect.success,
                "caused_by": effect.caused_by,
                "details": effect.details,
                "timestamp": effect.executed_at,
            }
            if effect.error_message:
                effect_dict["error_message"] = effect.error_message
            effects_list.append(effect_dict)

        # 외부 추적 정보
        external_trace = None
        if event.external_trace:
            external_trace = event.external_trace.to_dict()

        return Response(
            {
                "success": True,
                "event": {
                    "id": event.id,
                    "timestamp": event.timestamp,
                    "namespace": event.namespace,
                    "trigger": {
                        "type": event.trigger.trigger_type,
                        "event_id": event.trigger.event_id,
                        "details": event.trigger.details,
                        "triggered_by": event.trigger.triggered_by,
                    },
                    "effects": effects_list,
                    "causation_chain": event.get_causation_chain(),
                    "external_trace": external_trace,
                    "hash_chain": {
                        "previous_hash": event.previous_hash,
                        "current_hash": event.current_hash,
                    },
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class CascadeChainVerifyView(APIView):
    """
    POST /api/self-healing/cascade/verify/

    Hash Chain 무결성 검증.

    Request:
        {
            "namespace": "seoul",        // 기본: global
            "from_checkpoint": true,     // 체크포인트부터 검증 (기본: true)
            "full_verify": false         // 전체 체인 검증 (기본: false)
        }

    Response:
        {
            "success": true,
            "valid": true,
            "namespace": "seoul",
            "verified_count": 1500,
            "from_checkpoint": true,
            "checkpoint": {
                "timestamp": "2026-01-22T00:00:00Z",
                "hash": "abc123..."
            },
            "verification_time_ms": 150,
            "errors": []
        }

        # 무결성 위반 시:
        {
            "success": true,
            "valid": false,
            "namespace": "seoul",
            "verified_count": 500,
            "errors": [
                {
                    "event_id": "cascade-xyz",
                    "expected_hash": "abc...",
                    "actual_hash": "def...",
                    "error": "hash_mismatch"
                }
            ]
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request: Request) -> Response:
        namespace = request.data.get("namespace", "global")
        from_checkpoint = request.data.get("from_checkpoint", True)
        full_verify = request.data.get("full_verify", False)

        # Exception은 exception handler가 처리
        import time

        start_time = time.time()

        auditor = _get_cascade_auditor()

        # 검증 수행
        if full_verify or not from_checkpoint:
            result = auditor.verify_chain_integrity(namespace)
        else:
            result = auditor.verify_chain_integrity_from_checkpoint(namespace)

        elapsed_ms = int((time.time() - start_time) * 1000)

        # 체크포인트 정보
        checkpoint_info = None
        if from_checkpoint and not full_verify:
            checkpoint = auditor.get_checkpoint(namespace)
            if checkpoint:
                checkpoint_info = {
                    "timestamp": checkpoint.get("timestamp"),
                    "hash": checkpoint.get("last_hash"),
                }

        return Response(
            {
                "success": True,
                "valid": result.get("valid", False),
                "namespace": namespace,
                "verified_count": result.get("verified_count", 0),
                "from_checkpoint": from_checkpoint and not full_verify,
                "checkpoint": checkpoint_info,
                "verification_time_ms": elapsed_ms,
                "errors": result.get("errors", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class CausationTraceView(APIView):
    """
    GET /api/self-healing/cascade/trace/<event_id>/

    특정 이벤트의 인과관계 추적.

    Path Parameters:
        - event_id: 추적 시작 이벤트 ID (effect event_id)

    Query Parameters:
        - namespace: 네임스페이스 (기본: global)
        - direction: 추적 방향 (ancestors | descendants | both, 기본: ancestors)

    Response:
        {
            "success": true,
            "event_id": "effect-003",
            "direction": "ancestors",
            "trace": [
                {
                    "event_id": "evt-001",
                    "type": "trigger",
                    "action_type": null,
                    "details": {"old_level": "NORMAL", "new_level": "LEVEL_3"},
                    "caused_by": null
                },
                {
                    "event_id": "effect-001",
                    "type": "effect",
                    "action_type": "governance_strict",
                    "details": {},
                    "caused_by": "evt-001"
                },
                {
                    "event_id": "effect-003",
                    "type": "effect",
                    "action_type": "canary_rollback",
                    "details": {"rollouts": ["rollout-123"]},
                    "caused_by": "effect-001"
                }
            ],
            "cascade_id": "cascade-abc123",
            "namespace": "seoul"
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request, event_id: str) -> Response:
        namespace = request.query_params.get("namespace", "global")
        direction = request.query_params.get("direction", "ancestors")

        if direction not in ("ancestors", "descendants", "both"):
            raise ValueError("direction must be one of: ancestors, descendants, both")

        # Exception은 exception handler가 처리
        auditor = _get_cascade_auditor()

        # 인과관계 추적
        trace = auditor.trace_causation(event_id, namespace)

        if not trace:
            from django.http import Http404

            raise Http404(f"Event {event_id} not found or no causation trace")

        # cascade_id 추출 (첫 번째 이벤트에서)
        cascade_id = None
        if trace:
            # trace는 [{"event_id": ..., "cascade_id": ...}, ...] 형태
            for item in trace:
                if "cascade_id" in item:
                    cascade_id = item["cascade_id"]
                    break

        return Response(
            {
                "success": True,
                "event_id": event_id,
                "direction": direction,
                "trace": trace,
                "cascade_id": cascade_id,
                "namespace": namespace,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class CascadeCheckpointView(APIView):
    """
    GET /api/self-healing/cascade/checkpoint/
    POST /api/self-healing/cascade/checkpoint/

    체크포인트 조회 및 생성.

    GET Response:
        {
            "success": true,
            "namespace": "seoul",
            "checkpoint": {
                "timestamp": "2026-01-22T00:00:00Z",
                "last_hash": "abc123...",
                "event_count": 1500
            }
        }

    POST Request:
        {
            "namespace": "seoul"
        }

    POST Response:
        {
            "success": true,
            "namespace": "seoul",
            "checkpoint": {
                "timestamp": "2026-01-23T00:00:00Z",
                "last_hash": "def456...",
                "event_count": 1650
            }
        }
    """

    permission_classes = [IsSelfHealingAdmin]

    def get(self, request: Request) -> Response:
        namespace = request.query_params.get("namespace", "global")

        # Exception은 exception handler가 처리
        auditor = _get_cascade_auditor()
        checkpoint = auditor.get_checkpoint(namespace)

        return Response(
            {
                "success": True,
                "namespace": namespace,
                "checkpoint": checkpoint,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def post(self, request: Request) -> Response:
        namespace = request.data.get("namespace", "global")

        # Exception은 exception handler가 처리
        auditor = _get_cascade_auditor()
        checkpoint = auditor.create_checkpoint(namespace)

        logger.info(
            "cascade_api.checkpoint_created",
            namespace=namespace,
        )

        return Response(
            {
                "success": True,
                "namespace": namespace,
                "checkpoint": checkpoint,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )


class CascadeLoadSheddingStatusView(APIView):
    """
    GET /api/self-healing/cascade/load-shedding/status/

    Load Shedding 상태 조회.

    Response:
        {
            "success": true,
            "enabled": true,
            "current_load": 0.75,
            "thresholds": {
                "start": 0.7,
                "stop": 0.5
            },
            "dropped_count": {
                "low": 150,
                "medium": 50,
                "high": 0,
                "critical": 0
            },
            "total_dropped": 200,
            "fallback_count": 10
        }
    """

    permission_classes = [IsViewer]

    def get(self, request: Request) -> Response:
        from selfhealing.audit.cascade_load_shedding import get_cascade_load_shedding

        # Exception은 exception handler가 처리
        load_shedding = get_cascade_load_shedding()
        status_info = load_shedding.get_status()

        return Response(
            {
                "success": True,
                **status_info,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
