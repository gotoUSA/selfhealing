"""
Auto Tuning Service - 자율 조정 서비스

RuntimeFeedbackLoop, DecisionEngine, SafetyBounds를 조합하여
완전한 자율 조정 서비스를 제공합니다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock, Timer
from typing import Any

# Governance integration
from selfhealing.services.governance_checks import (
    GovernanceCheckResult,
    check_all_governance,
)

from .adjustment_recorder import AdjustmentRecorder
from .metrics_provider import MetricsProviderWrapper
from .models import AdjustmentRecord, TuningState

logger = logging.getLogger(__name__)


class TuningMode(str, Enum):
    """자율 조정 모드"""

    AUTOMATIC = "automatic"  # 자동 조정 활성화
    MANUAL = "manual"  # 수동 모드 (조정 불가)
    DRY_RUN = "dry_run"  # 조정 시뮬레이션만


class ModuleState(str, Enum):
    """모듈 상태"""

    ENABLED = "enabled"
    DISABLED = "disabled"
    AUTO_DISABLED = "auto_disabled"  # 자동 비활성화 (장애 등)


class AutoTuningService:
    """
    자율 조정 서비스

    RuntimeFeedbackLoop + DecisionEngine + SafetyBounds + AutoRollbackGuard를
    통합하여 완전한 자율 조정 기능을 제공합니다.

    API 기능:
    - 전역 Enable/Disable
    - 모듈별 Enable/Disable
    - 안전 한계 관리 (Bounds)
    - 수동 조정 (Override)
    - 조정 이력 조회
    - 메트릭 조회

    사용 예:
        service = AutoTuningService(
            metrics_adapter=prometheus_adapter,
            config_provider=settings_provider,
            audit_adapter=file_audit,
        )
        service.start()
    """

    # 지원하는 모듈 목록
    MODULES = ["circuit_breaker", "retry", "jitter", "rate_limit", "timeout"]

    def __init__(
        self,
        metrics_adapter,
        config_provider,
        config_applier,
        audit_adapter,
        alert_manager=None,
        enabled: bool = True,
        auto_rollback_enabled: bool = True,
    ):
        self._lock = RLock()
        self._enabled = enabled
        self._mode = TuningMode.AUTOMATIC if enabled else TuningMode.MANUAL

        # 모듈별 상태
        self._module_states: dict[str, ModuleState] = dict.fromkeys(self.MODULES, ModuleState.ENABLED)
        self._module_disabled_reasons: dict[str, str] = {}
        self._module_auto_enable_timers: dict[str, Timer] = {}

        # 수동 조정 (Override)
        self._overrides: dict[str, dict[str, Any]] = {}
        self._override_timers: dict[str, Timer] = {}

        # 비활성화 관련
        self._disabled_at: datetime | None = None
        self._disabled_by: str | None = None
        self._disabled_reason: str | None = None
        self._auto_enable_timer: Timer | None = None

        # 컴포넌트
        self.config_applier = config_applier
        self.audit_adapter = audit_adapter
        self.metrics_adapter = metrics_adapter

        # 컴포넌트 초기화
        from selfhealing.core.auto_rollback_guard import AutoRollbackGuard
        from selfhealing.core.decision_engine import DecisionEngine
        from selfhealing.core.runtime_feedback import RuntimeFeedbackLoop
        from selfhealing.core.safety_bounds import SafetyBounds

        self.safety_bounds = SafetyBounds()
        self.decision_engine = DecisionEngine(config_provider)
        self.adjustment_recorder = AdjustmentRecorder()

        # Alert Manager 생성 (없으면 기본)
        if alert_manager is None:
            alert_manager = self._create_default_alert_manager()
        self.alert_manager = alert_manager

        self.feedback_loop = RuntimeFeedbackLoop(
            metrics_adapter=metrics_adapter,
            decision_engine=self.decision_engine,
            safety_bounds=self.safety_bounds,
            audit_adapter=audit_adapter,
            alert_manager=alert_manager,
            config_applier=config_applier,
            enabled=enabled,
            auto_rollback_enabled=auto_rollback_enabled,
        )

        # AutoRollbackGuard (독립 안전장치)
        self.rollback_guard = AutoRollbackGuard(
            metrics_provider=self._create_metrics_provider(metrics_adapter),
            config_applier=config_applier,
            alert_callback=self._handle_guard_alert,
            enabled=enabled,
        )

        logger.info("[AutoTuningService] Initialized")

    def _check_governance_before_adjustment(
        self,
        module: str,
        adjustment_type: str = "automatic",
    ) -> GovernanceCheckResult:
        """
        조정 전 Governance 체크.

        Args:
            module: 조정 대상 모듈 (circuit_breaker, retry 등)
            adjustment_type: 조정 유형 (automatic, manual, rollback, service_start)

        Returns:
            GovernanceCheckResult
        """
        return check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,  # LEVEL_2 이상에서 차단
            check_error_budget=True,
            operation_name=f"auto_tuning:{module}:{adjustment_type}",
            service_name="auto_tuning",
            domain=module,
            audit_on_block=True,
        )

    def start(self) -> bool:
        """서비스 시작"""
        with self._lock:
            # Governance 체크
            gov_result = self._check_governance_before_adjustment(
                module="all",
                adjustment_type="service_start",
            )
            if not gov_result.allowed:
                logger.warning(
                    f"[AutoTuningService] Start blocked by governance: "
                    f"{gov_result.block_message}"
                )
                return False

            # 세션 시작
            self.adjustment_recorder.start_session("AutoTuningService started")

            # 피드백 루프 시작
            self.feedback_loop.start()

            # 롤백 가드 시작
            self.rollback_guard.start()

            logger.info("[AutoTuningService] Started")
            return True

    def stop(self) -> bool:
        """서비스 중지"""
        with self._lock:
            self.feedback_loop.stop()
            self.rollback_guard.stop()
            self.adjustment_recorder.end_session(TuningState.COMPLETED)

            logger.info("[AutoTuningService] Stopped")
            return True

    def pause(self, reason: str = "manual") -> bool:
        """서비스 일시 정지"""
        with self._lock:
            self.feedback_loop.pause(reason)
            return True

    def resume(self) -> bool:
        """서비스 재개"""
        with self._lock:
            return self.feedback_loop.resume()

    def trigger_emergency_recovery(self, reason: str = "manual") -> bool:
        """긴급 복구 트리거"""
        logger.warning(f"[AutoTuningService] Emergency recovery triggered: {reason}")
        return self.rollback_guard.trigger_manual_emergency(reason)

    def get_status(self) -> dict[str, Any]:
        """
        전체 상태 조회

        GET /api/self-healing/auto-tuning/status/
        """
        with self._lock:
            # 마지막 조정 정보
            last_adjustment = None
            records = self.adjustment_recorder.get_records(limit=1)
            if records:
                last_adjustment = records[0].to_dict()

            # 24시간 통계
            statistics_24h = self._get_24h_statistics()

            # 모듈별 상태
            modules_status = {}
            for module in self.MODULES:
                state = self._module_states.get(module, ModuleState.ENABLED)
                last_adj = self._get_last_module_adjustment(module)
                modules_status[module] = {
                    "enabled": state == ModuleState.ENABLED,
                    "state": state.value,
                    "last_adjustment": last_adj,
                    "disabled_reason": self._module_disabled_reasons.get(module),
                }

            return {
                "enabled": self._enabled,
                "mode": self._mode.value,
                "disabled_at": (
                    self._disabled_at.isoformat() if self._disabled_at else None
                ),
                "disabled_by": self._disabled_by,
                "disabled_reason": self._disabled_reason,
                "last_adjustment": last_adjustment,
                "statistics": {
                    "total_adjustments_24h": statistics_24h["total"],
                    "adjustments_by_type": statistics_24h["by_type"],
                },
                "modules": modules_status,
                "feedback_loop": self.feedback_loop.get_status(),
                "rollback_guard": self.rollback_guard.get_status(),
            }

    def enable(
        self,
        reason: str = "",
        mode: str = "automatic",
        enabled_by: str = "system",
    ) -> dict[str, Any]:
        """
        자율 조정 활성화

        POST /api/self-healing/auto-tuning/enable/
        """
        with self._lock:
            # 자동 비활성화 타이머 취소
            if self._auto_enable_timer:
                self._auto_enable_timer.cancel()
                self._auto_enable_timer = None

            self._enabled = True
            self._mode = (
                TuningMode(mode)
                if mode in [m.value for m in TuningMode]
                else TuningMode.AUTOMATIC
            )

            # 피드백 루프 활성화
            self.feedback_loop.enabled = True
            if self.feedback_loop.state.value == "paused":
                self.feedback_loop.resume()

            # 감사 로그
            audit_id = self._record_audit_event(
                action="auto_tuning_enabled",
                details={
                    "reason": reason,
                    "mode": self._mode.value,
                    "enabled_by": enabled_by,
                },
            )

            # 상태 클리어
            self._disabled_at = None
            self._disabled_by = None
            self._disabled_reason = None

            enabled_at = datetime.now(timezone.utc)

            logger.info(f"[AutoTuningService] Enabled by {enabled_by}: {reason}")

            return {
                "status": "enabled",
                "enabled_at": enabled_at.isoformat(),
                "enabled_by": enabled_by,
                "mode": self._mode.value,
                "audit_id": audit_id,
            }

    def disable(
        self,
        reason: str = "",
        duration_minutes: int | None = None,
        disabled_by: str = "system",
        notify: bool = True,
    ) -> dict[str, Any]:
        """
        자율 조정 비활성화

        POST /api/self-healing/auto-tuning/disable/
        """
        with self._lock:
            self._enabled = False
            self._mode = TuningMode.MANUAL
            self._disabled_at = datetime.now(timezone.utc)
            self._disabled_by = disabled_by
            self._disabled_reason = reason

            # 피드백 루프 일시 정지
            self.feedback_loop.enabled = False
            self.feedback_loop.pause(reason)

            # 자동 재활성화 타이머 설정
            auto_enable_at = None
            if duration_minutes:
                auto_enable_at = self._disabled_at + timedelta(minutes=duration_minutes)
                self._auto_enable_timer = Timer(
                    duration_minutes * 60, self._auto_reenable
                )
                self._auto_enable_timer.daemon = True
                self._auto_enable_timer.start()

            # 감사 로그
            audit_id = self._record_audit_event(
                action="auto_tuning_disabled",
                details={
                    "reason": reason,
                    "disabled_by": disabled_by,
                    "duration_minutes": duration_minutes,
                    "auto_enable_at": (
                        auto_enable_at.isoformat() if auto_enable_at else None
                    ),
                },
            )

            # 알림
            if notify:
                self._send_notification(
                    "Auto Tuning Disabled",
                    f"자율 조정이 비활성화되었습니다: {reason}",
                    "warning",
                )

            logger.warning(f"[AutoTuningService] Disabled by {disabled_by}: {reason}")

            return {
                "status": "disabled",
                "disabled_at": self._disabled_at.isoformat(),
                "disabled_by": disabled_by,
                "reason": reason,
                "auto_enable_at": (
                    auto_enable_at.isoformat() if auto_enable_at else None
                ),
                "audit_id": audit_id,
            }

    def enable_module(
        self,
        module: str,
        reason: str = "",
        enabled_by: str = "system",
    ) -> dict[str, Any]:
        """
        특정 모듈 자율 조정 활성화

        POST /api/self-healing/auto-tuning/{module}/enable/
        """
        with self._lock:
            if module not in self.MODULES:
                return {
                    "error": f"Unknown module: {module}",
                    "valid_modules": self.MODULES,
                }

            # 타이머 취소
            if module in self._module_auto_enable_timers:
                self._module_auto_enable_timers[module].cancel()
                del self._module_auto_enable_timers[module]

            self._module_states[module] = ModuleState.ENABLED
            if module in self._module_disabled_reasons:
                del self._module_disabled_reasons[module]

            audit_id = self._record_audit_event(
                action="auto_tuning_module_enabled",
                details={
                    "module": module,
                    "reason": reason,
                    "enabled_by": enabled_by,
                },
            )

            logger.info(f"[AutoTuningService] Module {module} enabled by {enabled_by}")

            return {
                "status": "enabled",
                "module": module,
                "enabled_at": datetime.now(timezone.utc).isoformat(),
                "enabled_by": enabled_by,
                "audit_id": audit_id,
            }

    def disable_module(
        self,
        module: str,
        reason: str = "",
        duration_minutes: int | None = None,
        disabled_by: str = "system",
    ) -> dict[str, Any]:
        """
        특정 모듈 자율 조정 비활성화

        POST /api/self-healing/auto-tuning/{module}/disable/
        """
        with self._lock:
            if module not in self.MODULES:
                return {
                    "error": f"Unknown module: {module}",
                    "valid_modules": self.MODULES,
                }

            self._module_states[module] = ModuleState.DISABLED
            self._module_disabled_reasons[module] = reason

            disabled_at = datetime.now(timezone.utc)
            auto_enable_at = None

            # 자동 재활성화 타이머
            if duration_minutes:
                auto_enable_at = disabled_at + timedelta(minutes=duration_minutes)
                timer = Timer(
                    duration_minutes * 60,
                    lambda: self.enable_module(module, "auto_reenable", "system"),
                )
                timer.daemon = True
                timer.start()
                self._module_auto_enable_timers[module] = timer

            audit_id = self._record_audit_event(
                action="auto_tuning_module_disabled",
                details={
                    "module": module,
                    "reason": reason,
                    "disabled_by": disabled_by,
                    "duration_minutes": duration_minutes,
                },
            )

            logger.warning(
                f"[AutoTuningService] Module {module} disabled by {disabled_by}: {reason}"
            )

            return {
                "status": "disabled",
                "module": module,
                "disabled_at": disabled_at.isoformat(),
                "disabled_by": disabled_by,
                "reason": reason,
                "auto_enable_at": (
                    auto_enable_at.isoformat() if auto_enable_at else None
                ),
                "audit_id": audit_id,
            }

    def get_bounds(self) -> dict[str, Any]:
        """
        안전 한계 조회

        GET /api/self-healing/auto-tuning/bounds/
        """
        with self._lock:
            bounds = self.safety_bounds.get_all_bounds()

            # 현재 값 추가
            result = {}
            for param, bound in bounds.items():
                current_value = None
                try:
                    current_value = self.config_applier.get_current(param)
                except Exception:
                    pass

                result[param] = {
                    "min": bound["min_value"],
                    "max": bound["max_value"],
                    "max_change_per_cycle": bound["max_change_per_cycle"],
                    "current_value": current_value,
                }

            return {
                "bounds": result,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "updated_by": "system",
            }

    def update_bounds(
        self,
        parameter: str,
        bounds: dict[str, float],
        reason: str = "",
        updated_by: str = "system",
    ) -> dict[str, Any]:
        """
        안전 한계 수정

        PUT /api/self-healing/auto-tuning/bounds/
        """
        with self._lock:
            # 이전 값
            previous = self.safety_bounds.get_bounds(parameter) or {
                "min_value": None,
                "max_value": None,
                "max_change_per_cycle": None,
            }

            # 업데이트
            config = {
                "min_value": bounds.get("min", previous.get("min_value", 0)),
                "max_value": bounds.get("max", previous.get("max_value", float("inf"))),
                "max_change_per_cycle": bounds.get(
                    "max_change_per_cycle", previous.get("max_change_per_cycle", 0.3)
                ),
            }

            success = self.safety_bounds.update_bounds(parameter, config)

            if not success:
                return {"error": "Failed to update bounds", "parameter": parameter}

            audit_id = self._record_audit_event(
                action="auto_tuning_bounds_changed",
                details={
                    "parameter": parameter,
                    "previous": previous,
                    "current": config,
                    "reason": reason,
                    "updated_by": updated_by,
                },
            )

            return {
                "status": "updated",
                "parameter": parameter,
                "previous": {
                    "min": previous.get("min_value"),
                    "max": previous.get("max_value"),
                    "max_change_per_cycle": previous.get("max_change_per_cycle"),
                },
                "current": {
                    "min": config["min_value"],
                    "max": config["max_value"],
                    "max_change_per_cycle": config["max_change_per_cycle"],
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "updated_by": updated_by,
                "audit_id": audit_id,
            }

    def get_history(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        parameter: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        조정 이력 조회

        GET /api/self-healing/auto-tuning/history/
        """
        with self._lock:
            records = self.adjustment_recorder.get_records(
                parameter=parameter,
                limit=page_size * page,
            )

            # 날짜 필터링
            filtered = []
            for record in records:
                if start_date and record.timestamp < start_date:
                    continue
                if end_date and record.timestamp > end_date:
                    continue
                filtered.append(record)

            # 페이지네이션
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_records = filtered[start_idx:end_idx]

            return {
                "total": len(filtered),
                "page": page,
                "page_size": page_size,
                "items": [self._format_history_item(r) for r in page_records],
            }

    def override(
        self,
        parameter: str,
        value: float,
        reason: str = "",
        duration_minutes: int | None = None,
        disable_auto_tuning: bool = True,
        overridden_by: str = "system",
    ) -> dict[str, Any]:
        """
        수동으로 파라미터 조정 (자율 조정 우회)

        POST /api/self-healing/auto-tuning/override/
        """
        with self._lock:
            # 현재 값 저장
            previous_value = None
            try:
                previous_value = self.config_applier.get_current(parameter)
            except Exception:
                pass

            # 안전 한계 검증
            if not self.safety_bounds.is_within_bounds(parameter, value):
                return {
                    "error": "Value outside safety bounds",
                    "parameter": parameter,
                    "value": value,
                    "bounds": self.safety_bounds.get_bounds(parameter),
                }

            # 설정 적용
            try:
                success = self.config_applier.apply(parameter, value)
                if not success:
                    return {"error": "Failed to apply value", "parameter": parameter}
            except Exception as e:
                return {"error": str(e), "parameter": parameter}

            # Override 저장
            self._overrides[parameter] = {
                "value": value,
                "previous_value": previous_value,
                "reason": reason,
                "overridden_by": overridden_by,
                "overridden_at": datetime.now(timezone.utc),
            }

            auto_rollback_at = None
            auto_tuning_disabled_until = None

            # 자동 롤백 타이머
            if duration_minutes:
                auto_rollback_at = datetime.now(timezone.utc) + timedelta(
                    minutes=duration_minutes
                )
                if disable_auto_tuning:
                    auto_tuning_disabled_until = auto_rollback_at

                # 이전 타이머 취소
                if parameter in self._override_timers:
                    self._override_timers[parameter].cancel()

                timer = Timer(
                    duration_minutes * 60,
                    lambda: self.clear_override(parameter, "auto_rollback"),
                )
                timer.daemon = True
                timer.start()
                self._override_timers[parameter] = timer

            # 해당 파라미터 자동 조정 비활성화
            if disable_auto_tuning:
                self._disable_parameter_auto_tuning(
                    parameter, auto_tuning_disabled_until
                )

            # 조정 기록
            self.adjustment_recorder.record(
                parameter=parameter,
                old_value=previous_value or 0,
                new_value=value,
                reason=f"manual_override: {reason}",
                triggered_by="manual",
            )

            audit_id = self._record_audit_event(
                action="auto_tuning_override",
                details={
                    "parameter": parameter,
                    "previous_value": previous_value,
                    "new_value": value,
                    "reason": reason,
                    "overridden_by": overridden_by,
                    "duration_minutes": duration_minutes,
                },
            )

            logger.info(
                f"[AutoTuningService] Override: {parameter}={value} by {overridden_by}"
            )

            return {
                "status": "applied",
                "parameter": parameter,
                "previous_value": previous_value,
                "new_value": value,
                "override_type": "manual",
                "auto_rollback_at": (
                    auto_rollback_at.isoformat() if auto_rollback_at else None
                ),
                "auto_tuning_disabled_until": (
                    auto_tuning_disabled_until.isoformat()
                    if auto_tuning_disabled_until
                    else None
                ),
                "audit_id": audit_id,
            }

    def clear_override(
        self,
        parameter: str,
        cleared_by: str = "system",
    ) -> dict[str, Any]:
        """
        수동 조정 해제 (자동 조정 복원)

        DELETE /api/self-healing/auto-tuning/override/{parameter}/
        """
        with self._lock:
            if parameter not in self._overrides:
                return {"error": f"No override found for {parameter}"}

            override_info = self._overrides.pop(parameter)

            # 타이머 취소
            if parameter in self._override_timers:
                self._override_timers[parameter].cancel()
                del self._override_timers[parameter]

            # 이전 값으로 복원
            if override_info.get("previous_value") is not None:
                try:
                    self.config_applier.apply(
                        parameter, override_info["previous_value"]
                    )
                except Exception as e:
                    logger.warning(f"Failed to restore previous value: {e}")

            # 자동 조정 재활성화
            self._enable_parameter_auto_tuning(parameter)

            audit_id = self._record_audit_event(
                action="auto_tuning_override_cleared",
                details={
                    "parameter": parameter,
                    "restored_value": override_info.get("previous_value"),
                    "cleared_by": cleared_by,
                },
            )

            return {
                "status": "cleared",
                "parameter": parameter,
                "restored_value": override_info.get("previous_value"),
                "cleared_by": cleared_by,
                "audit_id": audit_id,
            }

    def get_current_metrics(self) -> dict[str, Any]:
        """
        현재 메트릭 조회

        GET /api/self-healing/auto-tuning/metrics/
        """
        with self._lock:
            try:
                metrics = self.metrics_adapter.fetch_current_metrics()
            except Exception as e:
                return {
                    "error": str(e),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }

            # 현재 임계값과 조정 예정 여부
            thresholds = {}
            for param in ["timeout_ms", "retry_count", "circuit_breaker_threshold"]:
                current_value = None
                will_adjust = False
                try:
                    current_value = self.config_applier.get_current(param)
                    # 조정 필요 여부 확인
                    decisions = self.decision_engine.analyze(metrics)
                    will_adjust = any(d.parameter == param for d in decisions)
                except Exception:
                    pass

                thresholds[param] = {
                    "current": current_value,
                    "will_adjust": will_adjust,
                }

            return {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "metrics": metrics,
                "thresholds": thresholds,
            }

    def get_adjustment_history(
        self, parameter: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """조정 이력 조회 (레거시 호환)"""
        records = self.adjustment_recorder.get_records(parameter, limit)
        return [r.to_dict() for r in records]

    def update_safety_bounds(self, parameter: str, config: dict[str, float]) -> bool:
        """안전 한계 업데이트 (레거시 호환)"""
        return self.safety_bounds.update_bounds(parameter, config)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _auto_reenable(self):
        """자동 재활성화"""
        self.enable(reason="auto_reenable_after_timeout", enabled_by="system")

    def _get_24h_statistics(self) -> dict[str, Any]:
        """24시간 통계"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        records = self.adjustment_recorder.get_records(limit=1000)

        total = 0
        by_type: dict[str, int] = {}

        for record in records:
            if record.timestamp >= cutoff:
                total += 1
                param = record.parameter
                by_type[param] = by_type.get(param, 0) + 1

        return {"total": total, "by_type": by_type}

    def _get_last_module_adjustment(self, module: str) -> str | None:
        """모듈의 마지막 조정 시간"""
        # 모듈-파라미터 매핑
        module_params = {
            "circuit_breaker": ["circuit_breaker_threshold"],
            "retry": ["retry_count"],
            "jitter": ["jitter_range"],
            "rate_limit": ["rate_limit_rps"],
            "timeout": ["timeout_ms"],
        }

        params = module_params.get(module, [])
        for param in params:
            records = self.adjustment_recorder.get_records(parameter=param, limit=1)
            if records:
                return records[0].timestamp.isoformat()
        return None

    def _record_audit_event(self, action: str, details: dict[str, Any]) -> str:
        """감사 로그 기록"""
        audit_id = f"audit-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        try:
            from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry

            # AuditAction 매핑
            action_map = {
                "auto_tuning_enabled": AuditAction.CONFIG_CHANGE,
                "auto_tuning_disabled": AuditAction.CONFIG_CHANGE,
                "auto_tuning_module_enabled": AuditAction.CONFIG_CHANGE,
                "auto_tuning_module_disabled": AuditAction.CONFIG_CHANGE,
                "auto_tuning_bounds_changed": AuditAction.CONFIG_CHANGE,
                "auto_tuning_override": AuditAction.CONFIG_CHANGE,
                "auto_tuning_override_cleared": AuditAction.CONFIG_CHANGE,
            }

            entry = AuditEntry(
                action=action_map.get(action, AuditAction.CONFIG_CHANGE),
                resource_type="auto_tuning",
                resource_id=action,
                details={
                    "audit_id": audit_id,
                    "action_type": action,
                    **details,
                },
                actor_type=details.get(
                    "enabled_by",
                    details.get("disabled_by", details.get("updated_by", "system")),
                ),
                actor_id=details.get(
                    "enabled_by",
                    details.get("disabled_by", details.get("updated_by", "system")),
                ),
            )
            self.audit_adapter.log(entry)
        except Exception as e:
            logger.warning(f"[AutoTuningService] Audit log failed: {e}")

        return audit_id

    def _send_notification(self, title: str, message: str, severity: str):
        """알림 전송"""
        try:
            if hasattr(self.alert_manager, "_send_notification"):
                self.alert_manager._send_notification(title, message, severity)
            else:
                logger.info(f"[Notification] {title}: {message}")
        except Exception as e:
            logger.warning(f"[AutoTuningService] Notification failed: {e}")

    def _format_history_item(self, record: AdjustmentRecord) -> dict[str, Any]:
        """이력 아이템 포맷"""
        return {
            "id": record.record_id,
            "timestamp": record.timestamp.isoformat(),
            "parameter": record.parameter,
            "old_value": record.old_value,
            "new_value": record.new_value,
            "change_percent": (
                ((record.new_value - record.old_value) / record.old_value * 100)
                if record.old_value
                else 0
            ),
            "triggered_by": record.triggered_by,
            "reason": record.reason,
            "result": "applied" if record.success else "failed",
            "rollback_performed": record.rollback_performed,
        }

    def _disable_parameter_auto_tuning(self, parameter: str, until: datetime | None):
        """특정 파라미터 자동 조정 비활성화"""
        # 파라미터-모듈 매핑
        param_module = {
            "circuit_breaker_threshold": "circuit_breaker",
            "retry_count": "retry",
            "jitter_range": "jitter",
            "rate_limit_rps": "rate_limit",
            "timeout_ms": "timeout",
        }

        module = param_module.get(parameter)
        if module:
            self._module_states[module] = ModuleState.DISABLED
            self._module_disabled_reasons[module] = f"Override active for {parameter}"

    def _enable_parameter_auto_tuning(self, parameter: str):
        """특정 파라미터 자동 조정 활성화"""
        param_module = {
            "circuit_breaker_threshold": "circuit_breaker",
            "retry_count": "retry",
            "jitter_range": "jitter",
            "rate_limit_rps": "rate_limit",
            "timeout_ms": "timeout",
        }

        module = param_module.get(parameter)
        if module:
            self._module_states[module] = ModuleState.ENABLED
            if module in self._module_disabled_reasons:
                del self._module_disabled_reasons[module]

    def _create_default_alert_manager(self):
        """기본 Alert Manager 생성"""

        class DefaultAlertManager:
            def send_auto_tuning_alert(self, **kwargs):
                logger.info(f"[AutoTuning Alert] {kwargs}")

            def _send_notification(self, title, message, severity):
                logger.info(f"[Notification] {title}: {message}")

        return DefaultAlertManager()

    def _create_metrics_provider(self, metrics_adapter):
        """MetricsProvider 래퍼 생성"""
        return MetricsProviderWrapper(metrics_adapter)

    def _handle_guard_alert(self, alert_type: str, message: str):
        """Guard 알림 처리"""
        logger.warning(f"[AutoTuningService] Guard alert: {alert_type} - {message}")


__all__ = [
    "AutoTuningService",
    "TuningMode",
    "ModuleState",
]
