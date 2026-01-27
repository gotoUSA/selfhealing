"""
X-Test-Mode Rate Limiter Views

Rate Limiter(L1/L2)의 동작을 X-Test-Mode 환경에서 관찰할 수 있는 API.

Endpoints:
- GET  /api/self-healing/xtest/rate-limit/status/ - 전체 Rate Limit 상태 조회
- GET  /api/self-healing/xtest/rate-limit/client/ - 클라이언트별 상태 조회
- GET  /api/self-healing/xtest/rate-limit/history/ - Rate Limit 히스토리 조회
- GET  /api/self-healing/xtest/rate-limit/config/ - 현재 설정 조회
- POST /api/self-healing/xtest/rate-limit/reset/ - 카운터 초기화 (테스트용)

Architecture:
- L2 (Primary): Redis 기반 분산 Rate Limit
- L1 (Fallback): LocalMemoryRateLimiter (Redis 장애 시 로컬)
- Middleware: HybridRateLimitMiddleware (통합)

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단
"""

import logging
from typing import Any, Dict

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .base import XTestModeMixin, collect_system_snapshot

logger = logging.getLogger(__name__)


# =============================================================================
# Rate Limit 전체 상태 조회 View
# =============================================================================


class RateLimitStatusView(XTestModeMixin, APIView):
    """
    Rate Limit 전체 상태 조회 API.

    GET /api/self-healing/xtest/rate-limit/status/

    Query Parameters:
        client_key: 특정 클라이언트 키 조회 (선택)

    Response:
        {
            "status": "success",
            "mode": "normal|emergency|degraded",
            "redis_healthy": true,
            "fallback_active": false,
            "current_config": {
                "control_api_rate_limit": 100,
                "control_api_window_seconds": 60,
                "emergency_rate_limit": 10,
                "emergency_window_seconds": 60
            },
            "global_stats": {
                "total_events": 10,
                "exceeded_count": 2,
                "active_clients": 3
            },
            "timestamp": "2026-01-26T12:00:00Z",
            "snapshot": {...}
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        from selfhealing.api.django.rate_limit import (
            get_redis_health_checker,
            get_local_limiter,
            get_rate_limit_config,
            get_rate_limit_events_count,
            get_client_stats,
            RedisHealthState,
        )

        # Redis 헬스 체커 상태 확인
        health_checker = get_redis_health_checker()
        local_limiter = get_local_limiter()
        config = get_rate_limit_config()

        # 현재 모드 결정
        if health_checker.state == RedisHealthState.HEALTHY:
            mode = "normal"
        elif health_checker.state == RedisHealthState.RECOVERING:
            mode = "degraded"
        else:
            mode = "emergency"

        # 전역 통계
        total_events = get_rate_limit_events_count()
        client_stats = get_client_stats()
        exceeded_count = sum(stats.get("exceeded", 0) for stats in client_stats.values())
        active_clients = len(local_limiter.get_all_clients())

        # 특정 클라이언트 조회 요청 시
        client_key = request.query_params.get("client_key")
        client_status = None
        if client_key:
            client_status = local_limiter.get_client_status(client_key)

        snapshot = collect_system_snapshot()

        logger.info(
            f"[X-Test-Mode] Rate limit status: mode={mode}, "
            f"redis_healthy={health_checker.is_healthy}, active_clients={active_clients}"
        )

        response_data = {
            "status": "success",
            "mode": mode,
            "redis_healthy": health_checker.is_healthy,
            "fallback_active": not health_checker.is_healthy,
            "redis_state": health_checker.state.value,
            "current_config": {
                "control_api_rate_limit": config["control_api_rate_limit"],
                "control_api_window_seconds": config["control_api_window_seconds"],
                "emergency_rate_limit": config["emergency_rate_limit"],
                "emergency_window_seconds": config["emergency_window_seconds"],
            },
            "global_stats": {
                "total_events": total_events,
                "exceeded_count": exceeded_count,
                "active_clients": active_clients,
            },
            "timestamp": timezone.now().isoformat(),
            "snapshot": snapshot,
        }

        if client_status:
            response_data["client_status"] = client_status

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_status",
            component="rate_limit",
            details={"mode": mode, "active_clients": active_clients},
            result="success",
        )

        return Response(response_data)


# =============================================================================
# 클라이언트별 Rate Limit 상태 조회 View
# =============================================================================


class RateLimitClientView(XTestModeMixin, APIView):
    """
    클라이언트별 Rate Limit 상태 조회 API.

    GET /api/self-healing/xtest/rate-limit/client/

    Query Parameters:
        client_key: 클라이언트 식별자 (필수)
        window: 윈도우 타입 - minute, hour (선택, 기본 minute)

    Response:
        {
            "status": "success",
            "client_key": "192.168.1.1:user123",
            "current_count": 95,
            "limit": 100,
            "remaining": 5,
            "reset_at": 1706270400,
            "blocked": false,
            "window_seconds": 60
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        client_key = request.query_params.get("client_key")
        if not client_key:
            return Response(
                {
                    "status": "error",
                    "error": "missing_parameter",
                    "message": "client_key query parameter is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from selfhealing.api.django.rate_limit import (
            get_local_limiter,
            get_redis_health_checker,
        )

        local_limiter = get_local_limiter()
        health_checker = get_redis_health_checker()

        # 로컬 리미터에서 상태 조회
        client_status = local_limiter.get_client_status(client_key)

        logger.info(
            f"[X-Test-Mode] Rate limit client status: client_key={client_key}, "
            f"count={client_status['current_count']}, blocked={client_status['blocked']}"
        )

        response_data = {
            "status": "success",
            "source": "local" if not health_checker.is_healthy else "redis_fallback",
            **client_status,
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_client",
            component="rate_limit",
            details={"client_key": client_key, "blocked": client_status["blocked"]},
            result="success",
        )

        return Response(response_data)


# =============================================================================
# Rate Limit 히스토리 조회 View
# =============================================================================


class RateLimitHistoryView(XTestModeMixin, APIView):
    """
    Rate Limit 히스토리 조회 API.

    GET /api/self-healing/xtest/rate-limit/history/

    Query Parameters:
        limit: 조회 개수 (기본 20, 최대 100)
        client_key: 특정 클라이언트 필터 (선택)

    Response:
        {
            "status": "success",
            "total_exceeded": 5,
            "total_events": 100,
            "recent_events": [
                {
                    "timestamp": "...",
                    "client_key": "...",
                    "allowed": false,
                    "mode": "emergency",
                    ...
                }
            ],
            "by_client": {
                "192.168.1.1:user1": {"total": 50, "exceeded": 3},
                ...
            }
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        # 쿼리 파라미터 파싱
        try:
            limit = int(request.query_params.get("limit", 20))
            limit = min(max(limit, 1), 100)  # 1-100 범위
        except (ValueError, TypeError):
            limit = 20

        client_key = request.query_params.get("client_key")

        from selfhealing.api.django.rate_limit import (
            get_rate_limit_events,
            get_rate_limit_events_by_client,
            get_rate_limit_events_count,
            get_client_stats,
        )

        # 이벤트 조회
        if client_key:
            events = get_rate_limit_events_by_client(client_key, limit)
        else:
            events = get_rate_limit_events(limit)

        # 통계 계산
        total_events = get_rate_limit_events_count()
        client_stats = get_client_stats()
        total_exceeded = sum(stats.get("exceeded", 0) for stats in client_stats.values())

        logger.info(
            f"[X-Test-Mode] Rate limit history: returned={len(events)}, "
            f"total={total_events}, exceeded={total_exceeded}"
        )

        response_data = {
            "status": "success",
            "total_events": total_events,
            "total_exceeded": total_exceeded,
            "returned_count": len(events),
            "recent_events": events,
            "by_client": client_stats if not client_key else {client_key: client_stats.get(client_key, {})},
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_history",
            component="rate_limit",
            details={"total_events": total_events, "returned_count": len(events)},
            result="success",
        )

        return Response(response_data)


# =============================================================================
# Rate Limit 설정 조회 View
# =============================================================================


class RateLimitConfigXTestView(XTestModeMixin, APIView):
    """
    Rate Limit 설정 조회 API.

    GET /api/self-healing/xtest/rate-limit/config/

    Response:
        {
            "status": "success",
            "source": "runtime|settings|fallback",
            "normal_config": {
                "rate_limit": 100,
                "window_seconds": 60
            },
            "emergency_config": {
                "rate_limit": 10,
                "window_seconds": 60
            },
            "path_prefix": "/api/self-healing/",
            "excluded_paths": ["/health/", "/metrics/"],
            "redis_config": {
                "ping_interval": 5,
                "failure_threshold": 3,
                "recovery_jitter_max": 10
            }
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        from selfhealing.api.django.rate_limit import (
            get_rate_limit_config,
            get_redis_health_checker,
            _get_setting,
            _FALLBACK_CONTROL_API_PATH_PREFIX,
        )

        config = get_rate_limit_config()
        health_checker = get_redis_health_checker()

        # 설정 소스 판단
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            source = "runtime" if manager.is_initialized else "settings"
        except Exception:
            source = "settings"

        # API Rate Limit Settings 로드 시도
        try:
            from selfhealing.settings.api_rate_limit import get_api_rate_limit_settings
            api_settings = get_api_rate_limit_settings()
            settings_available = True
        except Exception:
            api_settings = None
            settings_available = False

        if not settings_available:
            source = "fallback"

        # 제외 경로 목록
        excluded_paths = []
        if api_settings and hasattr(api_settings, "excluded_paths"):
            excluded_paths = getattr(api_settings, "excluded_paths", [])

        logger.info(f"[X-Test-Mode] Rate limit config: source={source}")

        response_data = {
            "status": "success",
            "source": source,
            "normal_config": {
                "rate_limit": config["control_api_rate_limit"],
                "window_seconds": config["control_api_window_seconds"],
            },
            "emergency_config": {
                "rate_limit": config["emergency_rate_limit"],
                "window_seconds": config["emergency_window_seconds"],
            },
            "path_prefix": _get_setting("control_api_path_prefix", _FALLBACK_CONTROL_API_PATH_PREFIX),
            "excluded_paths": excluded_paths,
            "redis_config": {
                "ping_interval": health_checker.ping_interval,
                "failure_threshold": health_checker.failure_threshold,
                "recovery_jitter_max": health_checker.recovery_jitter_max,
            },
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_config",
            component="rate_limit",
            details={"source": source},
            result="success",
        )

        return Response(response_data)


# =============================================================================
# Rate Limit 카운터 초기화 View (테스트용)
# =============================================================================


class RateLimitResetView(XTestModeMixin, APIView):
    """
    Rate Limit 카운터 초기화 API (테스트용).

    POST /api/self-healing/xtest/rate-limit/reset/

    Request Body:
        {
            "client_key": "192.168.1.1:user123",  // 선택, 특정 클라이언트만
            "reset_all": false,  // 전체 초기화 (기본 false)
            "reset_events": false  // 이벤트 히스토리도 초기화 (기본 false)
        }

    Response:
        {
            "status": "success",
            "reset_count": 5,
            "clients_reset": ["client1", "client2"],
            "events_reset": 10
        }
    """

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        client_key = request.data.get("client_key")
        reset_all = request.data.get("reset_all", False)
        reset_events = request.data.get("reset_events", False)

        from selfhealing.api.django.rate_limit import (
            get_local_limiter,
            reset_rate_limit_state,
            reset_rate_limit_events,
        )

        local_limiter = get_local_limiter()
        clients_reset = []
        reset_count = 0
        events_reset = 0

        if reset_all:
            # 전체 초기화
            clients_before = local_limiter.get_all_clients()
            reset_count = len(clients_before)
            clients_reset = list(clients_before)
            reset_rate_limit_state()

            if reset_events:
                events_reset = reset_rate_limit_events()

            logger.warning(
                f"[X-Test-Mode] Rate limit RESET ALL: "
                f"clients={reset_count}, events={events_reset}"
            )
        elif client_key:
            # 특정 클라이언트만 초기화
            if local_limiter.reset_client(client_key):
                reset_count = 1
                clients_reset = [client_key]

            if reset_events:
                events_reset = reset_rate_limit_events(client_key)

            logger.info(
                f"[X-Test-Mode] Rate limit reset client: client_key={client_key}, "
                f"events_reset={events_reset}"
            )
        else:
            return Response(
                {
                    "status": "error",
                    "error": "missing_parameter",
                    "message": "Either 'client_key' or 'reset_all=true' is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_data = {
            "status": "success",
            "reset_count": reset_count,
            "clients_reset": clients_reset,
            "events_reset": events_reset,
            "timestamp": timezone.now().isoformat(),
        }

        # WAL Audit 기록
        self.log_xtest_cleanup(
            request=request,
            component="rate_limit",
            cleaned_count=reset_count,
            cleaned_ids=clients_reset,
        )

        return Response(response_data)
