"""
Admission Control Middleware.

HTTP 요청 경로를 TierRegistry로 자동 분류하고,
TrafficGate 파이프라인(Bulkhead → LoadShedding → RateController)을 통해
priority 기반 유입 제어를 수행하는 Django 미들웨어.

동작 방식:
1. TierRegistry.resolve_tier_with_fallback(path) 로 tier 분류
2. request 객체에 tier 정보 주입 (_selfhealing_tier_id, _selfhealing_tier_priority)
3. TrafficGate.should_allow(priority, bulkhead_name) 호출
4. 거부 시 503 Service Unavailable + Retry-After 응답

미들웨어 순서:
    AdmissionControlMiddleware → TieringMiddleware → Application
    (BackpressureMiddleware는 이 미들웨어가 대체하므로 비활성화 권고)

Configuration:
    # settings.py
    MIDDLEWARE = [
        ...
        'selfhealing.api.django.admission_control.AdmissionControlMiddleware',
        ...
    ]
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Tier ID → TrafficGate priority int 매핑.
# TrafficGate 규약: 낮을수록 높은 우선순위.
TIER_PRIORITY_MAP: dict[str, int] = {
    "critical": 0,
    "standard": 50,
    "non_essential": 100,
}


class AdmissionControlMiddleware:
    """
    HTTP 유입 제어 미들웨어.

    TierRegistry로 요청 경로의 tier를 자동 분류하고,
    TrafficGate 파이프라인을 통해 priority 기반 차등 유입 제어를 수행한다.
    거부 시 503 응답을 반환한다.

    request 객체에 주입되는 속성:
        _selfhealing_tier_id (str): 분류된 tier ID
        _selfhealing_tier_priority (int): TierDefinition.priority 값
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._enabled = True
        self._settings = None
        self._registry = None
        self._traffic_gate = None

        try:
            from selfhealing.settings.admission_control import (
                get_admission_control_settings,
            )

            self._settings = get_admission_control_settings()
            self._enabled = self._settings.enabled
        except Exception as e:
            logger.warning(
                "[AdmissionControlMiddleware] Settings load failed: %s, " "using defaults",
                e,
            )

        if self._enabled:
            self._init_dependencies()
            logger.info("[AdmissionControlMiddleware] Initialized and enabled")
        else:
            logger.info("[AdmissionControlMiddleware] Initialized but DISABLED")

    def _init_dependencies(self) -> None:
        """TierRegistry, TrafficGate, BulkheadRegistry 초기화."""
        try:
            from selfhealing.api.django.tiering.registry import get_tier_registry
            from selfhealing.resilience.bulkhead import get_bulkhead_registry
            from selfhealing.scaling.traffic_gate import get_traffic_gate
            from selfhealing.settings.admission_control import (
                get_admission_control_settings,
            )

            self._registry = get_tier_registry()
            self._traffic_gate = get_traffic_gate()

            # Tier별 Bulkhead 등록
            settings = self._settings or get_admission_control_settings()
            bulkhead_registry = get_bulkhead_registry()
            for tier_id in ("critical", "standard", "non_essential"):
                bulkhead_registry.get_or_create(
                    name=f"tier:{tier_id}",
                    max_concurrent=settings.get_tier_max_concurrent(tier_id),
                )
        except Exception as e:
            logger.error(
                "[AdmissionControlMiddleware] Dependency init failed: %s",
                e,
            )
            self._enabled = False

    def __call__(self, request):
        if not self._enabled:
            return self.get_response(request)

        try:
            return self._process_request(request)
        except Exception as e:
            logger.error(
                "[AdmissionControlMiddleware] Error: %s, allowing request",
                e,
            )
            return self.get_response(request)

    def _process_request(self, request):
        """Deadline 체크 → 요청 분류 → TrafficGate 판정 → 허용/거부."""
        # 0단계: Deadline Context 설정 및 Fast-Fail 체크
        try:
            from selfhealing.scaling.deadline_context import (
                DEADLINE_META_KEY,
                DEFAULT_MINIMUM_USEFUL_TIME_MS,
                parse_deadline_header,
                set_deadline,
            )

            deadline_header = request.META.get(DEADLINE_META_KEY)
            if deadline_header:
                remaining_ms = parse_deadline_header(deadline_header)
                if remaining_ms is not None:
                    set_deadline(remaining_ms)
                    # 최소 유효 시간 미만이면 즉시 거절
                    if remaining_ms < DEFAULT_MINIMUM_USEFUL_TIME_MS:
                        logger.info(
                            "[AdmissionControlMiddleware] Deadline Fast-Fail: " "remaining=%.0fms < minimum=%.0fms, path=%s",
                            remaining_ms,
                            DEFAULT_MINIMUM_USEFUL_TIME_MS,
                            request.path,
                        )
                        return self._create_deadline_rejection_response(request, remaining_ms)
        except ImportError:
            pass

        # 1단계: TierRegistry로 tier 분류
        path = request.path
        client_ip = self._get_client_ip(request)
        user_id = self._get_user_id(request)

        tier_result = self._registry.resolve_tier_with_fallback(
            path=path,
            client_ip=client_ip,
            user_id=str(user_id) if user_id else None,
        )

        tier_id = tier_result.tier_id

        # 2. request 객체에 tier 정보 주입
        request._selfhealing_tier_id = tier_id
        # TierDefinition에서 priority 조회
        tier_def = self._registry.get_tier(tier_id)
        request._selfhealing_tier_priority = tier_def.priority if tier_def else 0

        # 3. TrafficGate 판정
        traffic_priority = TIER_PRIORITY_MAP.get(tier_id, 50)
        bulkhead_name = f"tier:{tier_id}"

        decision = self._traffic_gate.should_allow(
            priority=traffic_priority,
            bulkhead_name=bulkhead_name,
        )

        if not decision.allowed:
            logger.warning(
                "[AdmissionControlMiddleware] Rejected: " "path=%s, tier=%s, gate=%s, reason=%s",
                path,
                tier_id,
                decision.gate,
                decision.reason,
            )
            return self._create_rejection_response(
                request=request,
                tier_id=tier_id,
                gate=decision.gate,
                reason=decision.reason,
            )

        # 4. 허용 시 다음 미들웨어로 전달
        try:
            response = self.get_response(request)
        finally:
            # Bulkhead 리소스 반환
            if decision.bulkhead_acquired and decision.bulkhead_name:
                self._traffic_gate.release_bulkhead(decision.bulkhead_name)

        return response

    def _get_client_ip(self, request) -> str | None:
        """X-Forwarded-For 또는 REMOTE_ADDR에서 클라이언트 IP 추출."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def _get_user_id(self, request) -> int | None:
        """인증된 사용자 ID 반환."""
        if hasattr(request, "user") and request.user.is_authenticated:
            return request.user.id
        return None

    def _create_rejection_response(self, request, tier_id, gate, reason):
        """503 Service Unavailable 응답 생성."""
        from django.http import JsonResponse

        response = JsonResponse(
            {
                "error": "Service Temporarily Unavailable",
                "code": "ADMISSION_CONTROL_REJECTED",
                "message": ("시스템 부하 관리를 위해 요청이 일시적으로 제한되었습니다. " "잠시 후 다시 시도해주세요."),
                "tier": tier_id,
                "gate": gate,
                "retry_after": 30,
            },
            status=503,
        )
        response["Retry-After"] = "30"
        return response

    def _create_deadline_rejection_response(self, request, remaining_ms):
        """503 Service Unavailable 응답 — deadline 만료 근접."""
        from django.http import JsonResponse

        response = JsonResponse(
            {
                "error": "Deadline Exceeded",
                "code": "DEADLINE_FAST_FAIL",
                "message": ("상위 서비스의 deadline이 만료에 근접하여 " "요청이 즉시 거절되었습니다."),
                "remaining_ms": remaining_ms,
                "retry_after": 0,
            },
            status=503,
        )
        response["Retry-After"] = "0"
        return response
