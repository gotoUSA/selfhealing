"""
Error Budget Gate - Core Gate Class.

에러 예산 기반 자동화 제어 게이트.
위기 상황일수록 인간의 개입을 강제하는 설계.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import structlog

from selfhealing.services.error_budget_gate.alert_manager import GateAlertManager
from selfhealing.services.error_budget_gate.config import (
    ErrorBudgetGateConfig,
    GateCheckResult,
    GateStatus,
)
from selfhealing.services.error_budget_gate.exceptions import AutomationBlockedError
from selfhealing.services.error_budget_gate.fault_detector import GateFaultDetector
from selfhealing.services.error_budget_gate.rate_limiter import InMemoryRateLimiter

logger = structlog.get_logger()


class ErrorBudgetGate:
    """
    에러 예산 기반 자동화 제어 게이트.

    중앙에서 한 번만 체크하면 모든 자동화 기능에 적용됩니다.

    특징:
    - Thread-safe
    - 결과 캐싱 (불필요한 API 호출 방지)
    - Fail-open 설계 (게이트 장애 시 자동화 허용)
    - 감사 로깅

    Usage:
        gate = get_error_budget_gate()

        # 체크만
        result = gate.check()
        if result.allowed:
            do_automation()

        # 예외 발생
        gate.require(action="chaos_experiment")  # 차단 시 예외 발생
    """

    def __init__(self, config: ErrorBudgetGateConfig | None = None):
        """Initialize ErrorBudgetGate."""
        self._config = config or ErrorBudgetGateConfig()
        self._lock = threading.RLock()
        self._cache: dict[str, GateCheckResult] = {}
        self._cache_time: dict[str, datetime] = {}

        # Fail-Open Rate Limiter 초기화 (Redis 의존 없음)
        self._fail_open_rate_limiter = InMemoryRateLimiter(
            max_requests=self._config.fail_open_rate_limit_per_minute,
            window_seconds=self._config.fail_open_rate_limit_window_seconds,
        )

        # Gate Fault Detector 초기화 (Error Budget 서비스 장애 시 빠른 Fail-Open)
        # ⚠️ 이것은 메인 CircuitBreakerService와 다릅니다 (Gate 전용)
        self._fault_detector = GateFaultDetector(
            failure_threshold=self._config.circuit_breaker_failure_threshold,
            recovery_timeout=self._config.circuit_breaker_recovery_timeout,
        )

        # Alert Manager 초기화 (Fail-Open 시 알림 발송)
        self._alert_manager = GateAlertManager(
            cooldown_seconds=self._config.alert_cooldown_seconds,
        )

        # 히스테리시스용 상태 추적 (플래핑 방지)
        self._current_status: GateStatus = GateStatus.OPEN

        self._load_config()

    def _load_config(self) -> None:
        """RuntimeConfig에서 설정 로드."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            chaos_config = manager.get_chaos_config()
            gate_config = chaos_config.get("error_budget_gate_config", {})

            if gate_config:
                self._config = ErrorBudgetGateConfig.from_dict(gate_config)
                # 컴포넌트 설정 동기화
                self._fail_open_rate_limiter.update_limits(
                    max_requests=self._config.fail_open_rate_limit_per_minute,
                    window_seconds=self._config.fail_open_rate_limit_window_seconds,
                )
                self._fault_detector.update_config(
                    failure_threshold=self._config.circuit_breaker_failure_threshold,
                    recovery_timeout=self._config.circuit_breaker_recovery_timeout,
                )
                self._alert_manager.update_config(
                    cooldown_seconds=self._config.alert_cooldown_seconds,
                )
        except Exception as e:
            logger.warning(
                "error_budget_gate.failed_load_config",
                error=e,
            )

    def _is_cache_valid(self, cache_key: str = "__global__") -> bool:
        """캐시 유효성 체크."""
        if cache_key not in self._cache or cache_key not in self._cache_time:
            return False

        elapsed = (datetime.now(timezone.utc) - self._cache_time[cache_key]).total_seconds()
        return elapsed < self._config.cache_ttl_seconds

    @staticmethod
    def _build_cache_key(region: str | None, tier_id: str | None) -> str:
        """티어/리전 조합으로 캐시 키 생성."""
        return f"{region or '__global__'}:{tier_id or '__global__'}"

    def _get_error_budget_percent(
        self,
        region: str | None = None,
    ) -> float | None:
        """
        현재 에러 예산 잔여율 조회.

        Gate Fault Detector가 적용되어 반복 실패 시 빠른 Fail-Open 처리.
        """
        # Fault Detector 체크
        if self._config.circuit_breaker_enabled and not self._fault_detector.can_execute():
            logger.debug("error_budget_gate.fault_detector_degraded_fast")
            return None

        try:
            from selfhealing.services.error_budget import (
                get_error_budget_service,
            )

            service = get_error_budget_service()
            status = service.get_budget_status(region=region)

            if status is None:
                # 리전 데이터 Missing 시 글로벌 버짯으로 Fallback
                if region is not None:
                    logger.warning(
                        "error_budget_gate.region_data_missing_falling",
                        region=region,
                    )
                    status = service.get_budget_status(region=None)

                if status is None:
                    self._fault_detector.record_failure()
                    return None

            # Dict 형태인 경우 (API 응답)
            if isinstance(status, dict):
                budget = status.get("budget", {})
                result = budget.get("remaining_percent", None)
                if result is not None:
                    self._fault_detector.record_success()
                    return result
                self._fault_detector.record_failure()
                return None

            # ErrorBudgetStatus 객체인 경우
            if hasattr(status, "budget_remaining_percent"):
                self._fault_detector.record_success()
                return status.budget_remaining_percent

            self._fault_detector.record_failure()
            return None

        except Exception as e:
            logger.exception(
                "error_budget_gate.failed_get_error_budget",
                error=e,
            )
            self._fault_detector.record_failure()

            # Fault Detector Degraded 시 알림
            fd_status = self._fault_detector.get_status()
            if fd_status["state"] == "degraded" and self._config.alert_on_fail_open:
                self._alert_manager.send_circuit_open_alert(fd_status["failure_count"])

            return None

    def get_config(self) -> ErrorBudgetGateConfig:
        """현재 설정 반환."""
        return self._config

    def update_config(self, **kwargs) -> ErrorBudgetGateConfig:
        """설정 업데이트."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(
                        "error_budget_gate.updated_config",
                        key=key,
                        value=value,
                    )

            # 캐시 무효화
            self._cache.clear()
            self._cache_time.clear()

            # 컴포넌트 설정 동기화
            self._fail_open_rate_limiter.update_limits(
                max_requests=self._config.fail_open_rate_limit_per_minute,
                window_seconds=self._config.fail_open_rate_limit_window_seconds,
            )
            self._fault_detector.update_config(
                failure_threshold=self._config.circuit_breaker_failure_threshold,
                recovery_timeout=self._config.circuit_breaker_recovery_timeout,
            )
            self._alert_manager.update_config(
                cooldown_seconds=self._config.alert_cooldown_seconds,
            )

            # RuntimeConfig에 저장
            self._persist_config()

            return self._config

    def _persist_config(self) -> None:
        """설정을 RuntimeConfig에 저장."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager

            manager = get_runtime_config_manager()
            manager.update_chaos_config(error_budget_gate_config=self._config.to_dict())
        except Exception as e:
            logger.warning(
                "error_budget_gate.failed_persist_config",
                error=e,
            )

    def check(
        self,
        force_refresh: bool = False,
        tier_id: str | None = None,
        region: str | None = None,
    ) -> GateCheckResult:
        """
        자동화 허용 여부 체크.

        Args:
            force_refresh: 캐시 무시하고 새로 조회
            tier_id: 서비스 티어 ("critical" | "standard" | "non_essential")
            region: 리전 식별자

        Returns:
            GateCheckResult: 체크 결과
        """
        with self._lock:
            # 게이트 비활성화 시
            if not self._config.enabled:
                return GateCheckResult(
                    allowed=True,
                    status=GateStatus.DISABLED,
                    reason="Error budget gate is disabled",
                    recommendation="Gate disabled - all automation allowed",
                    tier_id=tier_id,
                    region=region,
                )

            # 캐시 사용
            cache_key = self._build_cache_key(region, tier_id)
            if not force_refresh and self._is_cache_valid(cache_key) and cache_key in self._cache:
                return self._cache[cache_key]

            # 에러 예산 조회
            budget_percent = self._get_error_budget_percent(region=region)

            # 조회 실패 시 Fail-open
            if budget_percent is None:
                result = self._handle_fail_open()
                result.tier_id = tier_id
                result.region = region
                self._cache[cache_key] = result
                self._cache_time[cache_key] = datetime.now(timezone.utc)
                return result

            # 정상 판정
            result = self._evaluate(budget_percent, tier_id=tier_id, region=region)
            self._cache[cache_key] = result
            self._cache_time[cache_key] = datetime.now(timezone.utc)

            # 로깅
            if result.status == GateStatus.BLOCKED:
                logger.warning(
                    "error_budget_gate.automation_blocked_error_budget",
                    budget_percent=budget_percent,
                    _self=self._config.critical_threshold_percent,
                )
            elif result.status == GateStatus.WARNING:
                logger.info(
                    "error_budget_gate.warning_error_budget",
                    budget_percent=budget_percent,
                    _self=self._config.warning_threshold_percent,
                )

            return result

    def _evaluate(
        self,
        budget_percent: float,
        tier_id: str | None = None,
        region: str | None = None,
    ) -> GateCheckResult:
        """
        에러 예산 기반 판정 (히스테리시스 적용).

        플래핑 방지를 위해 진입/복구 임계치를 분리합니다:
        - 진입 임계치: tier/region별 차등 적용
        - 복구 임계치: 진입 임계치 + buffer
        """
        # 티어/리전별 차등 임계치 조회
        critical_threshold, warning_threshold = self._config.get_effective_thresholds(
            tier_id=tier_id or "standard", region=region
        )

        # 히스테리시스 적용된 복구 임계치
        critical_recovery = critical_threshold + self._config.threshold_hysteresis_buffer_percent
        warning_recovery = warning_threshold + self._config.threshold_hysteresis_buffer_percent

        previous_status = self._current_status
        new_status: GateStatus

        # 현재 상태에 따른 상태 전이 결정
        if self._current_status == GateStatus.BLOCKED:
            # BLOCKED → 복구 임계치로 WARNING 복귀 판정
            if budget_percent >= critical_recovery:
                new_status = GateStatus.WARNING
            else:
                new_status = GateStatus.BLOCKED

        elif self._current_status == GateStatus.WARNING:
            # WARNING 상태에서 전이 판정
            if budget_percent < critical_threshold:
                # WARNING → BLOCKED (진입 임계치로 CRITICAL 진입)
                new_status = GateStatus.BLOCKED
            elif budget_percent >= warning_recovery:
                # WARNING → OPEN (복구 임계치로 복귀)
                new_status = GateStatus.OPEN
            else:
                # WARNING 유지
                new_status = GateStatus.WARNING

        else:
            # OPEN 상태: 표준 진입 임계치 적용
            if budget_percent < critical_threshold:
                new_status = GateStatus.BLOCKED
            elif budget_percent < warning_threshold:
                new_status = GateStatus.WARNING
            else:
                new_status = GateStatus.OPEN

        # 상태 업데이트 및 이벤트 발행
        self._current_status = new_status
        result = self._build_gate_check_result(
            budget_percent,
            new_status,
            critical_threshold=critical_threshold,
            warning_threshold=warning_threshold,
            tier_id=tier_id,
            region=region,
        )

        # 상태 변경 시에만 이벤트 발행 (플래핑 시 중복 이벤트 방지)
        if new_status != previous_status:
            if new_status == GateStatus.BLOCKED:
                self._emit_error_budget_critical_event(budget_percent)
            elif new_status == GateStatus.WARNING and previous_status == GateStatus.OPEN:
                self._emit_error_budget_warning_event(budget_percent)
            elif new_status == GateStatus.OPEN and previous_status in (GateStatus.WARNING, GateStatus.BLOCKED):
                self._emit_error_budget_recovered_event(budget_percent)

        return result

    def _build_gate_check_result(
        self,
        budget_percent: float,
        status: GateStatus,
        critical_threshold: float | None = None,
        warning_threshold: float | None = None,
        tier_id: str | None = None,
        region: str | None = None,
    ) -> GateCheckResult:
        """상태에 따른 GateCheckResult 생성."""
        crit = critical_threshold or self._config.critical_threshold_percent
        warn = warning_threshold or self._config.warning_threshold_percent

        if status == GateStatus.BLOCKED:
            return GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=budget_percent,
                threshold_percent=crit,
                reason=f"Error budget critically low: {budget_percent:.1f}% < {crit}%",
                recommendation=(
                    "모든 자동화 기능이 중단되었습니다. "
                    "수동 검토 후 조치하세요. "
                    "에러 예산이 회복되면 자동으로 재개됩니다."
                ),
                tier_id=tier_id,
                region=region,
            )
        elif status == GateStatus.WARNING:
            return GateCheckResult(
                allowed=True,
                status=GateStatus.WARNING,
                error_budget_percent=budget_percent,
                threshold_percent=crit,
                reason=f"Error budget low: {budget_percent:.1f}% < {warn}%",
                recommendation=("에러 예산이 낮습니다. " "자동화는 계속 허용되지만, 수동 확인을 권장합니다."),
                tier_id=tier_id,
                region=region,
            )
        else:
            return GateCheckResult(
                allowed=True,
                status=GateStatus.OPEN,
                error_budget_percent=budget_percent,
                threshold_percent=crit,
                reason=f"Error budget healthy: {budget_percent:.1f}%",
                recommendation="자동화 정상 동작 중",
                tier_id=tier_id,
                region=region,
            )

    def _handle_fail_open(self) -> GateCheckResult:
        """
        Fail-open 처리 (Rate Limit 적용).

        Redis/DB 장애 상황에서도 무한 폭주를 방지하기 위해
        Rate Limit을 적용한 "최소한의 제약이 있는 방임" 정책을 사용합니다.
        """
        if not self._config.fail_open:
            # Fail-close (권장하지 않음)
            logger.error("error_budget_gate.fail_close_triggered_retrieve")
            return GateCheckResult(
                allowed=False,
                status=GateStatus.BLOCKED,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason="Error budget retrieval failed - fail-close policy applied",
                recommendation=("에러 예산 조회에 실패했습니다. " "Fail-close 정책에 따라 자동화를 차단합니다."),
                fail_open_triggered=True,
            )

        # Fail-open with Rate Limiting
        logger.warning("error_budget_gate.fail_open_triggered_retrieve")

        # 메트릭 기록
        try:
            from selfhealing.services.metrics.recorders import record_failsafe_triggered

            record_failsafe_triggered(component="error_budget_gate")
        except Exception:
            pass

        # Rate Limit 적용 여부 확인
        if not self._config.fail_open_rate_limit_enabled:
            # Rate Limit 비활성화 - 무조건 허용 (기존 동작)
            logger.info("error_budget_gate.rate_limiting_disabled_allowing")
            return GateCheckResult(
                allowed=True,
                status=GateStatus.FAIL_OPEN,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason="Error budget retrieval failed - fail-open policy applied (no rate limit)",
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    "Fail-open 정책에 따라 자동화를 허용합니다. "
                    "에러 예산 시스템을 확인하세요."
                ),
                fail_open_triggered=True,
            )

        # Rate Limit 체크 (Redis 의존 없는 메모리 기반)
        allowed, remaining, reset_at = self._fail_open_rate_limiter.try_acquire()

        if allowed:
            logger.info(
                "error_budget_gate.fail_open_allowed_rate",
                remaining=remaining,
            )

            # Fail-Open 알림 발송
            if self._config.alert_on_fail_open:
                self._alert_manager.send_fail_open_alert(
                    reason="Error budget service unavailable",
                    rate_limit_remaining=remaining,
                )

            return GateCheckResult(
                allowed=True,
                status=GateStatus.FAIL_OPEN,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason=(f"Error budget retrieval failed - fail-open with rate limit " f"({remaining} requests remaining)"),
                recommendation=(
                    "에러 예산 조회에 실패했습니다. "
                    f"Rate Limit 내에서 자동화를 허용합니다 (잔여: {remaining}회). "
                    "에러 예산 시스템을 확인하세요."
                ),
                fail_open_triggered=True,
                rate_limit_remaining=remaining,
                rate_limit_reset_at=reset_at,
            )
        else:
            # Rate Limit 초과 - 차단
            logger.warning(
                "error_budget_gate.fail_open_rate_limit",
                reset_at=reset_at.isoformat(),
            )

            # Rate Limit 초과 알림 발송
            if self._config.alert_on_fail_open:
                self._alert_manager.send_rate_limit_exceeded_alert()

            # Rate Limit 초과 메트릭
            try:
                from selfhealing.services.metrics.recorders import (
                    record_failsafe_triggered,
                )

                record_failsafe_triggered(component="error_budget_gate_rate_limited")
            except Exception:
                pass

            return GateCheckResult(
                allowed=False,
                status=GateStatus.FAIL_OPEN_RATE_LIMITED,
                error_budget_percent=None,
                threshold_percent=self._config.critical_threshold_percent,
                reason=(
                    f"Error budget retrieval failed and rate limit exceeded "
                    f"({self._config.fail_open_rate_limit_per_minute}/min)"
                ),
                recommendation=(
                    "에러 예산 조회 실패 상황에서 Rate Limit을 초과했습니다. "
                    f"자동화가 일시 차단됩니다 (리셋: {reset_at.strftime('%H:%M:%S')}). "
                    "에러 예산 시스템을 즉시 확인하세요."
                ),
                fail_open_triggered=True,
                rate_limit_remaining=0,
                rate_limit_reset_at=reset_at,
            )

    def require(self, action: str = "") -> GateCheckResult:
        """
        자동화 허용 필수 체크 - 차단 시 예외 발생.

        Args:
            action: 수행하려는 작업 이름 (로깅용)

        Returns:
            GateCheckResult: 허용된 경우의 체크 결과

        Raises:
            AutomationBlockedError: 차단된 경우
        """
        result = self.check()

        if not result.allowed:
            logger.warning(
                "error_budget_gate.action_blocked_error_budget",
                value=action or 'unknown',
                result=result.error_budget_percent,
            )

            # 감사 로깅
            self._audit_block(action, result)

            raise AutomationBlockedError(
                message=result.reason,
                error_budget_percent=result.error_budget_percent,
                threshold_percent=result.threshold_percent,
                action=action,
            )

        return result

    def _audit_block(self, action: str, result: GateCheckResult) -> None:
        """
        차단 이벤트 감사 로깅.

        audit_helpers 통합:
        - 기존: AuditAdapter.log 직접 호출
        - 변경: log_error_budget_blocked_audit 헬퍼 사용 (WAL + 해시 체인 연결)
        """
        try:
            from selfhealing.services.audit_helpers import (
                log_error_budget_blocked_audit,
            )

            log_error_budget_blocked_audit(
                action=action,
                gate_status=result.status.value,
                error_budget_percent=result.error_budget_percent,
                threshold_percent=result.threshold_percent,
                reason=result.reason,
            )
        except Exception as e:
            logger.warning(
                "error_budget_gate.failed_audit_block",
                error=e,
            )

    def clear_cache(self) -> None:
        """캐시 초기화."""
        with self._lock:
            self._cache.clear()
            self._cache_time.clear()

    # -------------------------------------------------------------------------
    # Event Bus
    # -------------------------------------------------------------------------

    def _emit_error_budget_critical_event(self, budget_percent: float) -> None:
        """
        에러 예산 임계치 도달 이벤트 발행.

        Chaos 실험 자동 차단, 자동 Replay 일시 중지 등
        다른 컴포넌트가 이 이벤트를 구독하여 반응합니다.
        """
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.ERROR_BUDGET_CRITICAL,
                data={
                    "budget_percent": budget_percent,
                    "threshold": self._config.critical_threshold_percent,
                    "status": "critical",
                },
                source="error_budget_gate",
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            # 이벤트 발행 실패해도 Gate 동작에는 영향 없음
            logger.warning(
                "error_budget_gate.failed_emit_critical_event",
                error=e,
            )

    def _emit_error_budget_warning_event(self, budget_percent: float) -> None:
        """에러 예산 경고 이벤트 발행."""
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.ERROR_BUDGET_WARNING,
                data={
                    "budget_percent": budget_percent,
                    "threshold": self._config.warning_threshold_percent,
                    "status": "warning",
                },
                source="error_budget_gate",
                priority=EventPriority.HIGH,
            )
        except Exception as e:
            logger.warning(
                "error_budget_gate.failed_emit_warning_event",
                error=e,
            )

    def _emit_error_budget_recovered_event(self, budget_percent: float) -> None:
        """에러 예산 회복 이벤트 발행 (WARNING/BLOCKED → OPEN 전이 시)."""
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.ERROR_BUDGET_RECOVERED,
                data={
                    "budget_percent": budget_percent,
                    "threshold": self._config.warning_threshold_percent,
                    "status": "recovered",
                },
                source="error_budget_gate",
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.warning(
                "error_budget_gate.failed_emit_recovered_event",
                error=e,
            )

    # -------------------------------------------------------------------------
    # Rate Limiter & Fault Detector Status
    # -------------------------------------------------------------------------

    def get_rate_limiter_status(self) -> dict[str, Any]:
        """
        Fail-Open Rate Limiter 현재 상태 조회.

        Returns:
            Rate limiter 상태 정보 (현재 카운트, 최대값, 남은 횟수 등)
        """
        status = self._fail_open_rate_limiter.get_status()
        status["enabled"] = self._config.fail_open_rate_limit_enabled
        return status

    def reset_rate_limiter(self) -> None:
        """
        Fail-Open Rate Limiter 초기화.

        관리/테스트 목적으로 Rate Limiter를 리셋합니다.
        주의: 프로덕션에서는 신중하게 사용하세요.
        """
        self._fail_open_rate_limiter.reset()
        logger.info("error_budget_gate.rate_limiter_reset_admin")

    def get_fault_detector_status(self) -> dict[str, Any]:
        """Gate Fault Detector 현재 상태 조회."""
        status = self._fault_detector.get_status()
        status["enabled"] = self._config.circuit_breaker_enabled
        return status

    def reset_fault_detector(self) -> None:
        """Gate Fault Detector 리셋."""
        self._fault_detector.reset()
        logger.info("error_budget_gate.fault_detector_reset_admin")

    def get_alert_status(self) -> dict[str, Any]:
        """Alert Manager 현재 상태 조회."""
        status = self._alert_manager.get_status()
        status["enabled"] = self._config.alert_on_fail_open
        return status

    def reset_alert_cooldowns(self) -> None:
        """알림 쿨다운 리셋."""
        self._alert_manager.reset()
        logger.info("error_budget_gate.alert_cooldowns_reset_admin")

    def get_health_status(self) -> dict[str, Any]:
        """
        Gate 전체 헬스 상태 조회.

        헬스체크 엔드포인트 (/health/gate)용 종합 상태 정보.

        Returns:
            종합 헬스 상태 딕셔너리
        """
        # 현재 Gate 상태 체크
        try:
            current_result = self.check(force_refresh=True)
            gate_status = current_result.status.value
            gate_healthy = current_result.status not in (
                GateStatus.FAIL_OPEN,
                GateStatus.FAIL_OPEN_RATE_LIMITED,
            )
        except Exception as e:
            gate_status = "error"
            gate_healthy = False
            logger.exception(
                "error_budget_gate.health_check_failed",
                error=e,
            )

        # 컴포넌트 상태
        fault_detector_status = self.get_fault_detector_status()
        rate_limiter_status = self.get_rate_limiter_status()
        alert_status = self.get_alert_status()

        # 종합 건강 상태 판단
        is_healthy = gate_healthy and fault_detector_status.get("state") != "degraded"

        return {
            "healthy": is_healthy,
            "status": "healthy" if is_healthy else "degraded",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gate": {
                "enabled": self._config.enabled,
                "status": gate_status,
                "fail_open_triggered": not gate_healthy,
            },
            "fault_detector": fault_detector_status,
            "circuit_breaker": fault_detector_status,  # 하위 호환성 별칭
            "rate_limiter": rate_limiter_status,
            "alerts": alert_status,
            "config": {
                "critical_threshold_percent": self._config.critical_threshold_percent,
                "warning_threshold_percent": self._config.warning_threshold_percent,
                "fail_open": self._config.fail_open,
            },
        }


# =============================================================================
# Singleton & Convenience Functions
# =============================================================================


_gate_instance: ErrorBudgetGate | None = None
_gate_lock = threading.Lock()


def get_error_budget_gate() -> ErrorBudgetGate:
    """ErrorBudgetGate 싱글톤 인스턴스 반환."""
    global _gate_instance

    if _gate_instance is None:
        with _gate_lock:
            if _gate_instance is None:
                _gate_instance = ErrorBudgetGate()

    return _gate_instance


def check_automation_allowed(
    force_refresh: bool = False,
    tier_id: str | None = None,
    region: str | None = None,
) -> GateCheckResult:
    """
    자동화 허용 여부 체크 (편의 함수).

    Args:
        force_refresh: 캐시 무시하고 새로 조회
        tier_id: 서비스 티어 ("critical" | "standard" | "non_essential")
        region: 리전 식별자
    """
    gate = get_error_budget_gate()
    return gate.check(force_refresh=force_refresh, tier_id=tier_id, region=region)


def require_automation_allowed(action: str = "") -> GateCheckResult:
    """
    자동화 허용 필수 체크 (편의 함수).

    차단 시 AutomationBlockedError 예외를 발생시킵니다.

    Usage:
        try:
            require_automation_allowed(action="chaos_experiment")
            do_automation()
        except AutomationBlockedError as e:
            notify_operator(e.message)
    """
    gate = get_error_budget_gate()
    return gate.require(action=action)


def is_automation_allowed() -> bool:
    """
    자동화 허용 여부 (단순 bool 반환).

    상세 정보가 필요 없을 때 사용합니다.
    """
    result = check_automation_allowed()
    return result.allowed


# =============================================================================
# Decorator
# =============================================================================


def automation_gate(action: str = ""):
    """
    자동화 게이트 데코레이터 (동기/비동기 모두 지원).

    에러 예산이 부족하면 함수 실행을 차단합니다.
    asyncio.iscoroutinefunction()으로 자동 분기하여
    sync/async 함수 모두 동일하게 사용 가능합니다.

    Args:
        action: 게이트 체크에 사용될 액션 이름.
                생략 시 함수 이름 사용.

    Usage:
        >>> @automation_gate(action="dlq_auto_replay")
        ... def auto_replay_dlq():
        ...     # 에러 예산이 충분할 때만 실행됨
        ...     pass

        >>> @automation_gate(action="async_cleanup")
        ... async def async_cleanup():
        ...     # 비동기 함수도 동일하게 사용
        ...     await do_cleanup()
    """
    import asyncio

    def decorator(func: Callable):
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            require_automation_allowed(action=action or func.__name__)
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # require_automation_allowed는 sync 함수지만
            # I/O 없는 빠른 체크이므로 직접 호출해도 무방
            require_automation_allowed(action=action or func.__name__)
            return await func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


__all__ = [
    "ErrorBudgetGate",
    "get_error_budget_gate",
    "check_automation_allowed",
    "require_automation_allowed",
    "is_automation_allowed",
    "automation_gate",
]
