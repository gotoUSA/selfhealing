"""
Bulkhead Status API - 격벽 상태 조회 엔드포인트.

격벽 패턴의 현재 상태를 조회하는 REST API를 제공합니다.
모든 격벽 또는 특정 격벽의 상태, 사용률, 거부 통계를 조회할 수 있습니다.

Endpoints:
    GET /api/self-healing/bulkhead/status/ - 모든 격벽 상태 조회
    GET /api/self-healing/bulkhead/status/?name=database - 특정 격벽 상태 조회

Note:
    예외 처리는 selfhealing_exception_handler로 위임됩니다.
    settings.py의 REST_FRAMEWORK.EXCEPTION_HANDLER 설정 참조.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.resilience.bulkhead import get_bulkhead_registry


class BulkheadStatusView(APIView):
    """
    격벽 상태 조회 API.

    모든 격벽의 현재 상태를 조회하거나, 특정 격벽의 상태를 조회합니다.
    각 격벽의 사용률, 활성 요청 수, 거부 통계 등을 반환합니다.

    GET /api/self-healing/bulkhead/status/
        - 모든 격벽 상태 조회

    GET /api/self-healing/bulkhead/status/?name=database
        - 특정 격벽 상태 조회

    Response:
        {
            "bulkheads": {
                "database": {
                    "type": "semaphore",
                    "max_concurrent": 10,
                    "active_count": 3,
                    "waiting_count": 0,
                    "rejected_count": 5,
                    "available_permits": 7,
                    "utilization_percent": 30.0,
                    "last_rejection_time": "2026-02-05T10:00:00+00:00"
                },
                ...
            },
            "summary": {
                "total_bulkheads": 4,
                "total_active": 5,
                "total_rejected": 10,
                "high_utilization": ["external_api"]
            },
            "timestamp": "2026-02-05T10:00:00+00:00"
        }
    """

    permission_classes = []  # Public endpoint

    def get(self, request: Request) -> Response:
        """격벽 상태 조회."""
        registry = get_bulkhead_registry()
        name_filter = request.query_params.get("name")

        states = registry.get_all_states()

        # 이름 필터가 있으면 해당 격벽만 반환
        if name_filter:
            states = {k: v for k, v in states.items() if k == name_filter}
            if not states:
                raise NotFound(
                    detail={
                        "error": f"Bulkhead '{name_filter}' not found",
                        "available_bulkheads": registry.list_names(),
                    }
                )

        response_data = self._build_response(states)
        return Response(response_data)

    def _build_response(self, states: dict[str, Any]) -> dict[str, Any]:
        """응답 데이터 구성."""
        bulkheads_data = {}
        total_active = 0
        total_rejected = 0
        high_utilization = []

        for name, state in states.items():
            bulkheads_data[name] = {
                "type": state.bulkhead_type.value,
                "max_concurrent": state.max_concurrent,
                "active_count": state.active_count,
                "waiting_count": state.waiting_count,
                "rejected_count": state.rejected_count,
                "available_permits": state.available_permits,
                "utilization_percent": round(state.utilization_percent, 2),
                "last_rejection_time": (state.last_rejection_time.isoformat() if state.last_rejection_time else None),
            }

            total_active += state.active_count
            total_rejected += state.rejected_count

            # 80% 이상 사용률이면 고사용률로 표시
            if state.utilization_percent > 80:
                high_utilization.append(name)

        return {
            "bulkheads": bulkheads_data,
            "summary": {
                "total_bulkheads": len(states),
                "total_active": total_active,
                "total_rejected": total_rejected,
                "high_utilization": high_utilization,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class BulkheadDetailView(APIView):
    """
    특정 격벽 상세 조회 API.

    GET /api/self-healing/bulkhead/{name}/
        - 특정 격벽의 상세 상태 조회
    """

    permission_classes = []  # Public endpoint

    def get(self, request: Request, name: str) -> Response:
        """특정 격벽 상세 조회."""
        registry = get_bulkhead_registry()

        try:
            bulkhead = registry.get(name)
        except KeyError:
            raise NotFound(
                detail={
                    "error": f"Bulkhead '{name}' not found",
                    "available_bulkheads": registry.list_names(),
                }
            )

        state = bulkhead.get_state()

        return Response(
            {
                "name": state.name,
                "type": state.bulkhead_type.value,
                "max_concurrent": state.max_concurrent,
                "active_count": state.active_count,
                "waiting_count": state.waiting_count,
                "rejected_count": state.rejected_count,
                "available_permits": state.available_permits,
                "utilization_percent": round(state.utilization_percent, 2),
                "last_rejection_time": (state.last_rejection_time.isoformat() if state.last_rejection_time else None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
