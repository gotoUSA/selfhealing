"""
Health Bridge Middleware (Stage 50: Worker Saturation 방지)

DB-independent health endpoint를 제공하여 DB 장애 시에도
CircuitBreaker 상태를 외부에서 관찰할 수 있도록 합니다.

Usage in settings.py:
    MIDDLEWARE = [
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단!
        "django.middleware.security.SecurityMiddleware",
        ...
    ]
"""

from __future__ import annotations

import structlog
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = structlog.get_logger()


class HealthBridgeMiddleware:
    """
    DB-independent Health Endpoint Middleware.

    문제 상황:
    - DB 죽음 → 모든 Django Worker가 DB 연결 대기
    - /health/l3도 Worker를 사용하므로 타임아웃
    - CB 상태를 외부에서 관찰 불가

    해결책:
    - Middleware에서 DB 엔진 로드 전에 즉시 반환
    - CB 스냅샷을 메모리에 저장 (매 요청마다 갱신)
    - Prometheus 메트릭은 기존 인프라 활용

    CRITICAL: 이 Middleware는 MIDDLEWARE 리스트 최상단에 위치해야 함!
    """

    # 클래스 변수: CB 스냅샷 저장 (모든 인스턴스가 공유)
    _cb_snapshot: dict[str, Any] = {
        "states": {},
        "last_updated": None,
        "update_count": 0,
    }
    _snapshot_lock = None  # threading.Lock()는 lazy init

    # Health Bridge 대상 경로
    BRIDGE_PATHS = [
        "/api/self-healing/health/l3/",
        "/api/self-healing/health/bridge/",
    ]

    def __init__(self, get_response: Callable):
        """Initialize middleware."""
        self.get_response = get_response

        # Lazy init lock (import threading here to avoid circular import)
        import threading

        if HealthBridgeMiddleware._snapshot_lock is None:
            HealthBridgeMiddleware._snapshot_lock = threading.Lock()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request/response."""

        # === Early Return for Bridge Paths ===
        if request.path in self.BRIDGE_PATHS:
            return self._serve_bridge_response(request)

        # === Normal Request Processing ===
        response = self.get_response(request)

        # === Update CB Snapshot (best-effort) ===
        # Non-blocking: 실패해도 요청은 정상 처리
        self._try_update_snapshot()

        return response

    def _serve_bridge_response(self, request: HttpRequest) -> HttpResponse:
        """
        Serve health bridge response without touching DB.

        Returns CB snapshot from memory - instant response even during DB blackout.
        """
        from django.http import JsonResponse

        snapshot = self._get_snapshot()

        response_data = {
            "status": "bridge_active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "circuit_breakers": snapshot.get("states", {}),
            "snapshot": {
                "last_updated": snapshot.get("last_updated"),
                "update_count": snapshot.get("update_count", 0),
                "age_seconds": self._calculate_snapshot_age(snapshot),
            },
            "note": "DB-independent health endpoint (Stage 50)",
        }

        return JsonResponse(response_data)

    def _try_update_snapshot(self) -> None:
        """
        Try to update CB snapshot from in-memory state.

        Non-blocking: If CB service is unavailable, skip silently.
        """
        try:
            # Import here to avoid circular imports and keep DB-independence
            from selfhealing.services.circuit_breaker.convenience import (
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()
            if cb_service is None:
                return

            # get_all_states returns list of dicts from repository
            all_states = cb_service.get_all_states()

            states = {}
            for s in all_states:
                states[s["service_name"]] = {
                    "state": s["state"],
                    "failure_count": s.get("failure_count", 0),
                    "success_count": s.get("success_count", 0),
                    "last_failure_at": s.get("last_failure_at"),
                    "manually_controlled": s.get("manually_controlled", False),
                }

            with self._snapshot_lock:
                HealthBridgeMiddleware._cb_snapshot = {
                    "states": states,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "update_count": self._cb_snapshot.get("update_count", 0) + 1,
                }

        except Exception as e:
            # Log at warning level temporarily for debugging
            logger.warning(
                "cb_snapshot_update_failed",
                error=e,
            )

    def _get_snapshot(self) -> dict[str, Any]:
        """Thread-safe snapshot read."""
        with self._snapshot_lock:
            return dict(self._cb_snapshot)

    def _calculate_snapshot_age(self, snapshot: dict[str, Any]) -> float | None:
        """Calculate age of snapshot in seconds."""
        last_updated = snapshot.get("last_updated")
        if not last_updated:
            return None

        try:
            last_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return round((now - last_dt).total_seconds(), 2)
        except Exception:
            return None
