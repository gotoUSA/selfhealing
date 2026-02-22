"""
Meta-Watchdog API Views.

Kubernetes Liveness Probe 및 상태 조회 엔드포인트.

Endpoints:
- GET /api/self-healing/health/meta-watchdog/ - Meta-Watchdog Liveness Probe
- GET /api/self-healing/meta/status/ - Meta-Watchdog 상태 조회
"""

from __future__ import annotations

import structlog

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = structlog.get_logger()


class MetaWatchdogLivenessView(APIView):
    """
    Meta-Watchdog Liveness Probe.

    GET /api/self-healing/health/meta-watchdog/

    Watchdog 루프가 stuck 되지 않았는지 확인합니다.
    K8s가 이 엔드포인트를 통해 Watchdog Pod를 재시작할 수 있습니다.

    Response:
        200: Watchdog 정상 (alive)
        503: Watchdog stuck 또는 비활성화

    Example:
        GET /api/self-healing/health/meta-watchdog/
        {
            "status": "alive",
            "last_loop_age_seconds": 15.2,
            "max_age_seconds": 90.0
        }
    """

    permission_classes = []  # Public for K8s

    def get(self, request):
        """Watchdog liveness 확인."""
        try:
            from selfhealing.meta.config import get_meta_watchdog_settings
            from selfhealing.meta.state_store import get_watchdog_state_store

            settings = get_meta_watchdog_settings()

            # Watchdog 비활성화 시
            if not settings.enabled:
                return Response(
                    {
                        "status": "disabled",
                        "message": "Meta-Watchdog is disabled",
                    },
                    status=status.HTTP_200_OK,
                )

            store = get_watchdog_state_store()

            # 마지막 루프 경과 시간
            age_seconds = store.get_last_loop_age_seconds()

            # 임계치: probe_interval의 3배
            max_age = settings.probe_interval_seconds * 3

            if age_seconds > max_age:
                logger.warning(
                    "meta_watchdog.liveness_check_failed",
                    age_seconds=age_seconds,
                    max_age=max_age,
                )
                return Response(
                    {
                        "status": "stuck",
                        "last_loop_age_seconds": age_seconds,
                        "max_age_seconds": max_age,
                        "message": "Watchdog loop appears to be stuck",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            return Response(
                {
                    "status": "alive",
                    "last_loop_age_seconds": age_seconds,
                    "max_age_seconds": max_age,
                }
            )

        except ImportError:
            # Meta-Watchdog 모듈 없음
            return Response(
                {"status": "unavailable", "message": "Meta-Watchdog not installed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(
                "meta_watchdog.liveness_check_error",
                error=e,
            )
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MetaWatchdogStatusView(APIView):
    """
    Meta-Watchdog 상태 조회.

    GET /api/self-healing/meta/status/

    Self-Healing 시스템의 전체 상태와 각 컴포넌트 상태를 반환합니다.

    Response:
        200: 상태 정보
        503: Meta-Watchdog 사용 불가

    Example:
        GET /api/self-healing/meta/status/
        {
            "overall_status": "healthy",
            "components": {
                "circuit_breaker": "healthy",
                "dlq": "healthy",
                "redis": "healthy",
                "recovery_pipeline": "healthy"
            },
            "last_check": "2026-02-04T10:30:00Z",
            "escalation_count": 0,
            "escalation_pending": false,
            "self_cb_open": false
        }
    """

    permission_classes = []  # Public

    def get(self, request):
        """Watchdog 상태 조회."""
        try:
            from selfhealing.meta.config import get_meta_watchdog_settings
            from selfhealing.meta.watchdog import get_selfhealer_watchdog

            settings = get_meta_watchdog_settings()

            # Watchdog 비활성화 시
            if not settings.enabled:
                return Response(
                    {
                        "overall_status": "disabled",
                        "message": "Meta-Watchdog is disabled",
                    },
                    status=status.HTTP_200_OK,
                )

            watchdog = get_selfhealer_watchdog()
            state = watchdog.get_state()

            return Response(
                {
                    "overall_status": state.overall_status.value,
                    "components": {
                        name: component_status.value for name, component_status in state.component_statuses.items()
                    },
                    "last_check": state.last_check.isoformat(),
                    "escalation_count": state.escalation_count,
                    "escalation_pending": state.escalation_pending,
                    "self_cb_open": state.self_cb_open,
                    "consecutive_failures": state.consecutive_failures,
                }
            )

        except ImportError:
            return Response(
                {"status": "unavailable", "message": "Meta-Watchdog not installed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(
                "meta_watchdog.status_check_error",
                error=e,
            )
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MetaWatchdogForceCheckView(APIView):
    """
    Meta-Watchdog 강제 체크 트리거.

    POST /api/self-healing/meta/force-check/

    즉시 건강 상태 체크를 수행합니다 (디버깅/테스트용).

    Response:
        200: 체크 결과
    """

    permission_classes = []  # TODO: 운영 환경에서는 인증 필요

    def post(self, request):
        """즉시 건강 상태 체크 수행."""
        try:
            from selfhealing.meta.config import get_meta_watchdog_settings
            from selfhealing.meta.watchdog import get_selfhealer_watchdog

            settings = get_meta_watchdog_settings()

            if not settings.enabled:
                return Response(
                    {
                        "overall_status": "disabled",
                        "message": "Meta-Watchdog is disabled",
                    },
                    status=status.HTTP_200_OK,
                )

            watchdog = get_selfhealer_watchdog()
            state = watchdog.force_check()

            return Response(
                {
                    "overall_status": state.overall_status.value,
                    "components": {
                        name: component_status.value for name, component_status in state.component_statuses.items()
                    },
                    "last_check": state.last_check.isoformat(),
                    "escalation_count": state.escalation_count,
                    "escalation_pending": state.escalation_pending,
                    "message": "Force check completed",
                }
            )

        except ImportError:
            return Response(
                {"status": "unavailable", "message": "Meta-Watchdog not installed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(
                "meta_watchdog.force_check_error",
                error=e,
            )
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
