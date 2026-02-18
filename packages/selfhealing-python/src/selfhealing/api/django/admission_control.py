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
import os
import random
import time

logger = logging.getLogger(__name__)

# RTT 샘플 수집 — 3중 필터링 상수
# 최소 임계치 미만의 초단기 요청(Health Check 등)은 노이즈로 간주하여 수집 제외
_RTT_MIN_SAMPLE_MS: float = float(
    os.environ.get("SELFHEALING_DEADLINE_RTT_MIN_SAMPLE_MS", "5")
)
# 확률 샘플링 비율 (0.1 = 10%). Lock 경합 감소용. EMA 특성상 10% 샘플로 추세 파악 충분.
_RTT_SAMPLE_RATE: float = float(
    os.environ.get("SELFHEALING_DEADLINE_RTT_SAMPLE_RATE", "0.1")
)


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
        # CORS Preflight는 Tier 분류 대상에서 제외 (Always Allow)
        # - OPTIONS는 body 없이 부하 무시 수준
        # - 거부 시 후속 POST/DELETE도 CORS 에러로 전송 불가
        if request.method == "OPTIONS":
            return self.get_response(request)

        # 0단계: Deadline Context 설정 및 Fast-Fail 체크
        try:
            from selfhealing.scaling.deadline_context import (
                DEADLINE_ENABLED,
                DEADLINE_META_KEY,
                DEFAULT_MINIMUM_USEFUL_TIME_MS,
                parse_deadline_header,
                record_fast_fail,
                record_remaining_ms,
                set_deadline,
            )

            if DEADLINE_ENABLED:
                deadline_header = request.META.get(DEADLINE_META_KEY)
                if deadline_header:
                    remaining_ms = parse_deadline_header(deadline_header)
                    if remaining_ms is not None:
                        set_deadline(remaining_ms)
                        record_remaining_ms(remaining_ms)
                        # 최소 유효 시간 미만이면 즉시 거절
                        if remaining_ms < DEFAULT_MINIMUM_USEFUL_TIME_MS:
                            path_prefix = request.path.split("/")[1] if "/" in request.path else request.path
                            record_fast_fail(path_prefix=path_prefix)
                            logger.info(
                                "[AdmissionControlMiddleware] Deadline Fast-Fail: "
                                "remaining=%.0fms < minimum=%.0fms, path=%s",
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
        method = request.method

        tier_result = self._registry.resolve_tier_with_fallback(
            path=path,
            client_ip=client_ip,
            user_id=str(user_id) if user_id else None,
            method=method,
        )

        tier_id = tier_result.tier_id

        # 2. request 객체에 tier 정보 주입
        request._selfhealing_tier_id = tier_id
        # TierDefinition에서 priority 조회
        tier_def = self._registry.get_tier(tier_id)
        request._selfhealing_tier_priority = tier_def.priority if tier_def else 0

        # 2.5단계: Degraded tier 강제 Deadline 주입
        # HIGH 이상 backpressure + non_essential → 1초 deadline으로
        # Heavy Query가 critical/standard tier 자원을 점유하는 것을 방지
        _DEGRADED_TIER_DEADLINE_MS = 1000
        if tier_id == "non_essential" and self._traffic_gate is not None:
            try:
                from selfhealing.scaling.deadline_context import (
                    get_remaining_ms,
                    set_deadline,
                )
                from selfhealing.settings.backpressure import BackpressureLevel

                bp_level = self._traffic_gate.get_level()
                if bp_level in (BackpressureLevel.HIGH, BackpressureLevel.CRITICAL):
                    remaining = get_remaining_ms()
                    if remaining is None or remaining > _DEGRADED_TIER_DEADLINE_MS:
                        set_deadline(_DEGRADED_TIER_DEADLINE_MS)
                        logger.info(
                            "[AdmissionControlMiddleware] Forced deadline: " "tier=%s, bp_level=%s, deadline_ms=%d",
                            tier_id,
                            bp_level.name,
                            _DEGRADED_TIER_DEADLINE_MS,
                        )
            except ImportError:
                pass

        # 3. TrafficGate 판정
        traffic_priority = TIER_PRIORITY_MAP.get(tier_id, 50)
        bulkhead_name = f"tier:{tier_id}"

        # Tier별 Bulkhead timeout: critical/standard는 짧은 대기, non_essential은 즉시 실패
        settings = self._settings
        bulkhead_timeout = settings.get_tier_bulkhead_timeout(tier_id) if settings else None

        decision = self._traffic_gate.should_allow(
            priority=traffic_priority,
            bulkhead_name=bulkhead_name,
            bulkhead_timeout=bulkhead_timeout,
            metadata={"tier_id": tier_id},
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
        start_time = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            # Bulkhead 리소스 반환
            if decision.bulkhead_acquired and decision.bulkhead_name:
                self._traffic_gate.release_bulkhead(decision.bulkhead_name)

        # 5. RTT 샘플 수집 — 3중 필터링
        # 필터 1: HTTP 2xx 성공 응답만 (실제 비즈니스 로직을 수행한 요청)
        # 필터 2: 최소 임계치 이상 (Health Check 등 노이즈 제거)
        # 필터 3: 확률 샘플링 (Lock 경합 감소)
        try:
            if 200 <= response.status_code < 300:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if elapsed_ms >= _RTT_MIN_SAMPLE_MS:
                    if random.random() < _RTT_SAMPLE_RATE:
                        from selfhealing.services.throttle.gradient import (
                            get_gradient_calculator,
                        )

                        get_gradient_calculator(
                            f"admission_control:{tier_id}"
                        ).add_sample(elapsed_ms)
        except Exception:
            pass  # Fail-Open: RTT 수집 실패가 요청 처리에 영향 없음

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
        """503 Service Unavailable 응답 생성.

        현재 BackpressureLevel에 따라 Retry-After 값을 동적으로 조절한다.
        부하가 높을수록 재시도 간격을 늘려 Retry Storm을 방지.
        """
        from django.http import JsonResponse

        from selfhealing.settings.backpressure import get_backpressure_settings

        bp_settings = get_backpressure_settings()

        # 현재 BackpressureLevel 조회 → 레벨별 동적 Retry-After
        current_level = self._traffic_gate.get_level() if self._traffic_gate else None
        if current_level is not None:
            retry_after = bp_settings.get_retry_after_for_level(current_level)
        else:
            retry_after = bp_settings.reject_retry_after_seconds

        response = JsonResponse(
            {
                "error": "Service Temporarily Unavailable",
                "code": "ADMISSION_CONTROL_REJECTED",
                "message": ("시스템 부하 관리를 위해 요청이 일시적으로 제한되었습니다. " "잠시 후 다시 시도해주세요."),
                "tier": tier_id,
                "gate": gate,
                "retry_after": retry_after,
            },
            status=503,
        )
        response["Retry-After"] = str(retry_after)
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
