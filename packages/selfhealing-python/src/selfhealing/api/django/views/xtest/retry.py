"""
X-Test-Mode Retry Handler Views

Retry Handler의 Exponential Backoff, DLQ 라우팅, Rate Limit 인식 동작을
X-Test-Mode 환경에서 관찰할 수 있는 API.

Endpoints:
- GET  /api/self-healing/xtest/retry/backoff-preview/ - Backoff 시퀀스 미리보기
- POST /api/self-healing/xtest/retry/simulate/ - 재시도 시나리오 시뮬레이션
- GET  /api/self-healing/xtest/retry/rate-limit-status/ - Rate Limit 인식 상태
- GET  /api/self-healing/xtest/retry/config/ - 현재 Retry 설정 조회

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단
"""

import logging
import time
from typing import Any

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .base import XTestModeMixin, collect_system_snapshot

logger = logging.getLogger(__name__)


# =============================================================================
# Backoff 계산 미리보기 View
# =============================================================================


class BackoffPreviewView(XTestModeMixin, APIView):
    """
    Backoff 시퀀스 미리보기 API.

    GET /api/self-healing/xtest/retry/backoff-preview/

    Query Parameters:
        max_attempts: 최대 재시도 횟수 (기본: 설정값)
        backoff_base: 백오프 기본값 (기본: 4)
        backoff_max: 최대 대기 시간 (기본: 180)
        jitter_percent: 지터 퍼센트 (기본: 25)

    Response:
        {
            "status": "success",
            "config": {
                "max_attempts": 4,
                "backoff_base": 4,
                "backoff_max": 180,
                "jitter_percent": 25
            },
            "delays": [4, 16, 64, 180],
            "delays_with_jitter": [
                {"attempt": 1, "base": 4, "min": 3, "max": 5},
                {"attempt": 2, "base": 16, "min": 12, "max": 20},
                ...
            ],
            "total_max_delay": 264,
            "snapshot": {...}
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        # 쿼리 파라미터 파싱
        try:
            max_attempts = int(request.query_params.get("max_attempts", 0))
            backoff_base = int(request.query_params.get("backoff_base", 0))
            backoff_max = int(request.query_params.get("backoff_max", 0))
            jitter_percent = int(request.query_params.get("jitter_percent", -1))
        except (ValueError, TypeError):
            return Response(
                {
                    "status": "error",
                    "error": "invalid_parameters",
                    "message": "Query parameters must be integers",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 기본값 로드 (설정에서)
        from selfhealing.services.backoff_calculator import (
            BackoffCalculator,
            BackoffConfig,
        )
        from selfhealing.services.retry_handler import RetryConfig

        default_config = RetryConfig.from_settings()

        # 요청 파라미터로 오버라이드
        final_max_attempts = (
            max_attempts if max_attempts > 0 else default_config.max_attempts
        )
        final_backoff_base = (
            backoff_base if backoff_base > 0 else default_config.backoff_base
        )
        final_backoff_max = (
            backoff_max if backoff_max > 0 else default_config.backoff_max
        )
        final_jitter_percent = (
            jitter_percent if jitter_percent >= 0 else default_config.jitter_percent
        )

        # BackoffCalculator 생성
        config = BackoffConfig(
            base=final_backoff_base,
            max_delay=final_backoff_max,
            jitter_percent=final_jitter_percent,
        )
        calculator = BackoffCalculator(config)

        # 지터 없는 시퀀스
        delays = calculator.get_delays_sequence(final_max_attempts, with_jitter=False)

        # 지터 적용 범위 계산
        delays_with_jitter = []
        for attempt in range(1, final_max_attempts + 1):
            base_delay = calculator.calculate(attempt, with_jitter=False)
            jitter_factor = final_jitter_percent / 100.0
            min_delay = max(1, int(base_delay * (1 - jitter_factor)))
            max_delay = int(base_delay * (1 + jitter_factor))
            delays_with_jitter.append(
                {
                    "attempt": attempt,
                    "base": base_delay,
                    "min": min_delay,
                    "max": max_delay,
                }
            )

        total_max_delay = sum(delays)

        snapshot = collect_system_snapshot()

        logger.info(
            f"[X-Test-Mode] Backoff preview: max_attempts={final_max_attempts}, "
            f"delays={delays}"
        )

        response_data = {
            "status": "success",
            "config": {
                "max_attempts": final_max_attempts,
                "backoff_base": final_backoff_base,
                "backoff_max": final_backoff_max,
                "jitter_percent": final_jitter_percent,
            },
            "delays": delays,
            "delays_with_jitter": delays_with_jitter,
            "total_max_delay": total_max_delay,
            "snapshot": snapshot,
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="backoff_preview",
            component="retry",
            details={
                "max_attempts": final_max_attempts,
                "total_max_delay": total_max_delay,
            },
            result="success",
        )

        return Response(response_data, status=status.HTTP_200_OK)


# =============================================================================
# Retry Simulation Helpers (Complexity Reduction)
# =============================================================================


def _validate_failure_count(failure_count: Any) -> tuple[int | None, str | None]:
    """failure_count 유효성 검증. (값, 에러메시지) 반환."""
    if failure_count is None:
        return None, "failure_count is required"
    try:
        value = int(failure_count)
        if value < 1:
            return None, "failure_count must be positive"
        return value, None
    except (ValueError, TypeError) as e:
        return None, str(e)


def _build_retry_sequence(
    failure_count: int,
    config,
    calculator,
) -> tuple[list[dict[str, Any]], int, str]:
    """
    재시도 시퀀스 구성.

    Returns:
        tuple: (retry_sequence, total_attempts, final_action)
    """
    from selfhealing.services.retry_handler import RetryAction

    retry_sequence: list[dict[str, Any]] = []
    total_attempts = 0
    final_action = RetryAction.SUCCESS.value

    for attempt in range(1, config.max_attempts + 1):
        total_attempts = attempt

        if attempt <= failure_count:
            # 실패 시뮬레이션
            result = "FAILURE"

            if attempt < config.max_attempts:
                delay = calculator.calculate(attempt, with_jitter=False)
                retry_sequence.append(
                    {
                        "attempt": attempt,
                        "result": result,
                        "delay_before_next": delay,
                    }
                )
            else:
                retry_sequence.append(
                    {
                        "attempt": attempt,
                        "result": result,
                        "delay_before_next": None,
                    }
                )
                final_action = RetryAction.DLQ.value
        else:
            # 성공 시뮬레이션
            retry_sequence.append(
                {
                    "attempt": attempt,
                    "result": "SUCCESS",
                    "delay_before_next": None,
                }
            )
            final_action = RetryAction.SUCCESS.value
            break

    return retry_sequence, total_attempts, final_action


def _determine_final_action(
    retry_sequence: list[dict[str, Any]],
    config,
) -> tuple[str, bool]:
    """
    최종 액션 결정.

    Returns:
        tuple: (final_action, dlq_routed)
    """
    from selfhealing.services.retry_handler import RetryAction

    last_attempt_failed = retry_sequence[-1]["result"] == "FAILURE"
    dlq_routed = last_attempt_failed and config.enable_dlq

    if last_attempt_failed:
        final_action = RetryAction.DLQ.value if dlq_routed else RetryAction.ABORT.value
    else:
        final_action = RetryAction.SUCCESS.value

    return final_action, dlq_routed


# =============================================================================
# 재시도 시뮬레이션 View
# =============================================================================


class RetrySimulateView(XTestModeMixin, APIView):
    """
    재시도 시나리오 시뮬레이션 API.

    POST /api/self-healing/xtest/retry/simulate/

    Request:
        {
            "failure_count": 5,         // 연속 실패 횟수 (필수)
            "max_attempts": 3,          // 최대 재시도 (선택)
            "domain": "external",       // 도메인 (DLQ 연동용, 선택)
            "simulate_dlq": false       // DLQ 저장 시뮬레이션 (선택)
        }

    Response:
        {
            "status": "success",
            "total_attempts": 3,
            "final_action": "DLQ",
            "retry_sequence": [
                {"attempt": 1, "result": "FAILURE", "delay_before_next": 4},
                {"attempt": 2, "result": "FAILURE", "delay_before_next": 16},
                {"attempt": 3, "result": "FAILURE", "delay_before_next": null}
            ],
            "dlq_routed": true,
            "dlq_id": 123,
            "snapshot": {...}
        }
    """

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        # 요청 파라미터 파싱 및 검증
        failure_count, error_msg = _validate_failure_count(
            request.data.get("failure_count")
        )
        if error_msg:
            error_type = (
                "missing_required_field"
                if failure_count is None and "required" in error_msg
                else "invalid_failure_count"
            )
            return Response(
                {"status": "error", "error": error_type, "message": error_msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_attempts = request.data.get("max_attempts")
        domain = request.data.get("domain", "xtest_simulation")
        simulate_dlq = request.data.get("simulate_dlq", False)

        # 설정 로드
        from selfhealing.services.backoff_calculator import (
            BackoffCalculator,
            BackoffConfig,
        )
        from selfhealing.services.retry_handler import RetryConfig

        config = RetryConfig.from_settings(domain)
        if max_attempts is not None:
            try:
                config.max_attempts = int(max_attempts)
            except (ValueError, TypeError):
                pass

        # BackoffCalculator 생성
        backoff_config = BackoffConfig(
            base=config.backoff_base,
            max_delay=config.backoff_max,
            jitter_percent=config.jitter_percent,
        )
        calculator = BackoffCalculator(backoff_config)

        # 시뮬레이션 실행
        retry_sequence, total_attempts, _ = _build_retry_sequence(
            failure_count, config, calculator
        )

        # 최종 액션 결정
        final_action, dlq_routed = _determine_final_action(retry_sequence, config)

        # DLQ 시뮬레이션
        dlq_id = None
        if dlq_routed and simulate_dlq:
            dlq_id = self._simulate_dlq_entry(
                domain, failure_count, config.max_attempts
            )

        snapshot = collect_system_snapshot()

        logger.info(
            f"[X-Test-Mode] Retry simulate: failure_count={failure_count}, "
            f"total_attempts={total_attempts}, final_action={final_action}, "
            f"dlq_routed={dlq_routed}"
        )

        response_data = {
            "status": "success",
            "total_attempts": total_attempts,
            "final_action": final_action,
            "retry_sequence": retry_sequence,
            "dlq_routed": dlq_routed,
            "dlq_id": dlq_id,
            "config_used": {
                "max_attempts": config.max_attempts,
                "backoff_base": config.backoff_base,
                "backoff_max": config.backoff_max,
                "enable_dlq": config.enable_dlq,
            },
            "snapshot": snapshot,
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="simulate",
            component="retry",
            details={
                "failure_count": failure_count,
                "total_attempts": total_attempts,
                "final_action": final_action,
            },
            result="success",
        )

        return Response(response_data, status=status.HTTP_200_OK)

    def _simulate_dlq_entry(
        self, domain: str, failure_count: int, max_attempts: int
    ) -> int | None:
        """DLQ 테스트 항목 생성 (X-Test-Mode 마커 포함)."""
        try:
            from selfhealing.services.dlq_service import store_to_dlq

            result = store_to_dlq(
                domain=domain,
                failure_type="XTEST_RETRY_SIMULATION",
                error_code="SimulatedRetryExhaustion",
                error_message=f"Simulated {failure_count} consecutive failures (max: {max_attempts})",
                metadata={
                    "xtest_mode": True,
                    "simulation_type": "retry_exhaustion",
                    "failure_count": failure_count,
                    "max_attempts": max_attempts,
                    "timestamp": timezone.now().isoformat(),
                },
                next_action_hint="X-Test-Mode simulation - safe to delete",
                recommended_action="manual_check",
            )

            if result.success:
                return result.dlq_id
            return None
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Failed to create DLQ entry: {e}")
            return None


# =============================================================================
# Rate Limit 인식 상태 View
# =============================================================================


class RetryRateLimitStatusView(XTestModeMixin, APIView):
    """
    Rate Limit 인식 상태 조회 API.

    GET /api/self-healing/xtest/retry/rate-limit-status/

    Query Parameters:
        domain: 도메인 (rate_limit_key) (선택)

    Response:
        {
            "status": "success",
            "rate_limit_aware": true,
            "storage_type": "redis",
            "domain": "payment",
            "state": {
                "consecutive_429s": 3,
                "is_in_cooldown": true,
                "cooldown_until": "2026-01-26T12:00:00Z",
                "remaining_cooldown": 15.5
            },
            "throttled": true,
            "recommended_delay": 30,
            "snapshot": {...}
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        domain = request.query_params.get("domain", "default")

        try:
            from selfhealing.services.rate_limit_coordinator import (
                RateLimitCoordinatorConfig,
                get_rate_limit_coordinator,
            )

            coordinator = get_rate_limit_coordinator()
            config = RateLimitCoordinatorConfig.from_settings()
            state = coordinator.get_state(domain)

            # 상태 정보 구성
            state_info = {
                "consecutive_429s": state.consecutive_429s,
                "is_in_cooldown": state.is_in_cooldown,
                "cooldown_until": (
                    time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(state.cooldown_until)
                    )
                    if state.cooldown_until
                    else None
                ),
                "remaining_cooldown": (
                    state.remaining_cooldown if state.is_in_cooldown else 0
                ),
            }

            # 권장 대기 시간 계산
            recommended_delay = 0
            if state.is_in_cooldown:
                recommended_delay = state.remaining_cooldown
            elif state.consecutive_429s > 0:
                # 연속 429가 있으면 다음 예상 백오프 계산
                recommended_delay = min(
                    config.base_delay
                    * (config.backoff_multiplier**state.consecutive_429s),
                    config.max_delay,
                )

            snapshot = collect_system_snapshot()

            logger.info(
                f"[X-Test-Mode] Rate limit status: domain={domain}, "
                f"throttled={state.is_in_cooldown}, consecutive_429s={state.consecutive_429s}"
            )

            response_data = {
                "status": "success",
                "rate_limit_aware": True,
                "storage_type": coordinator.storage_type,
                "domain": domain,
                "state": state_info,
                "throttled": state.is_in_cooldown,
                "recommended_delay": round(recommended_delay, 2),
                "config": {
                    "base_delay": config.base_delay,
                    "max_delay": config.max_delay,
                    "jitter_percent": config.jitter_percent,
                    "default_retry_after": config.default_retry_after,
                    "backoff_multiplier": config.backoff_multiplier,
                },
                "snapshot": snapshot,
            }

            # WAL Audit 기록
            self.log_xtest_audit(
                request=request,
                action="query_rate_limit_status",
                component="retry",
                details={"domain": domain, "throttled": state.is_in_cooldown},
                result="success",
            )

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.warning(f"[X-Test-Mode] Rate limit status check failed: {e}")
            snapshot = collect_system_snapshot()
            return Response(
                {
                    "status": "error",
                    "rate_limit_aware": False,
                    "storage_type": None,
                    "domain": domain,
                    "error": str(e),
                    "throttled": False,
                    "recommended_delay": 0,
                    "snapshot": snapshot,
                },
                status=status.HTTP_200_OK,
            )


# =============================================================================
# RetryConfig 조회 View
# =============================================================================


class RetryConfigView(XTestModeMixin, APIView):
    """
    현재 적용된 Retry 설정 조회 API.

    GET /api/self-healing/xtest/retry/config/

    Query Parameters:
        domain: 도메인별 설정 조회 (선택)

    Response:
        {
            "status": "success",
            "source": "runtime",
            "domain": "payment",
            "config": {
                "max_attempts": 3,
                "backoff_base": 4,
                "backoff_max": 180,
                "jitter_percent": 25,
                "enable_dlq": true,
                "rate_limit_aware": true
            },
            "domain_overrides": {
                "payment": {"max_attempts": 5}
            },
            "snapshot": {...}
        }
    """

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        domain = request.query_params.get("domain", "default")

        # 설정 소스 확인 및 로드
        source = "default"
        domain_overrides: dict[str, Any] = {}

        try:
            # RuntimeConfigManager 시도
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            retry_config = manager.get_retry_config()
            if retry_config:
                source = "runtime"
        except Exception:
            pass

        if source == "default":
            try:
                # core config 시도
                from selfhealing.settings import get_config

                core_config = get_config()
                if hasattr(core_config, "retry"):
                    source = "settings"

                # 도메인별 오버라이드 확인
                if hasattr(core_config, "domain_configs"):
                    domain_overrides = core_config.domain_configs
            except Exception:
                pass

        # RetryConfig 로드
        from selfhealing.services.retry_handler import RetryConfig

        config = RetryConfig.from_settings(domain)

        snapshot = collect_system_snapshot()

        logger.info(
            f"[X-Test-Mode] Retry config: domain={domain}, source={source}, "
            f"max_attempts={config.max_attempts}"
        )

        response_data = {
            "status": "success",
            "source": source,
            "domain": domain,
            "config": {
                "max_attempts": config.max_attempts,
                "backoff_base": config.backoff_base,
                "backoff_max": config.backoff_max,
                "jitter_percent": config.jitter_percent,
                "enable_dlq": config.enable_dlq,
                "rate_limit_aware": config.rate_limit_aware,
                "rate_limit_key": config.rate_limit_key,
                "retryable_exceptions": [
                    exc.__name__ for exc in config.retryable_exceptions
                ],
                "non_retryable_exceptions": [
                    exc.__name__ for exc in config.non_retryable_exceptions
                ],
            },
            "domain_overrides": domain_overrides,
            "snapshot": snapshot,
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_config",
            component="retry",
            details={"domain": domain, "source": source},
            result="success",
        )

        return Response(response_data, status=status.HTTP_200_OK)
