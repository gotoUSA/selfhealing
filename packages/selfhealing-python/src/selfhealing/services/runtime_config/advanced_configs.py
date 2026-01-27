"""
Advanced Configuration Mixins.

Provides get/update methods for advanced configuration types:
- Logging
- Metrics
- Error Budget
- SLO
- Governance
- Drift Threshold
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.settings import DriftThresholdSettings as DriftThresholdConfig

logger = logging.getLogger(__name__)


class AdvancedConfigMixin:
    """Mixin providing advanced configuration methods."""

    # =========================================================================
    # Logging Config
    # 시스템 컴포넌트별 로깅 설정 관리
    # =========================================================================

    def get_logging_config(self) -> dict[str, Any]:
        """
        Get logging configuration.

        Returns:
            dict: 컴포넌트별 로그 레벨 및 포맷 설정
        """
        return self._get_config("logging")

    def update_logging_config(
        self,
        # 컴포넌트별 로그 레벨
        dlq_log_level: str | None = None,
        circuit_breaker_log_level: str | None = None,
        replay_log_level: str | None = None,
        sla_log_level: str | None = None,
        forensic_log_level: str | None = None,
        emergency_log_level: str | None = None,
        chaos_log_level: str | None = None,
        l2_storage_log_level: str | None = None,
        # 로그 포맷 설정
        include_timestamps: bool | None = None,
        include_request_id: bool | None = None,
        include_user_info: bool | None = None,
        # 로그 출력 설정
        console_output_enabled: bool | None = None,
        file_output_enabled: bool | None = None,
        structured_json: bool | None = None,
    ) -> dict[str, Any]:
        """
        Update logging configuration.

        Args:
            dlq_log_level: DLQ 관련 로그 레벨
            circuit_breaker_log_level: Circuit Breaker 로그 레벨
            replay_log_level: DLQ Replay 로그 레벨
            sla_log_level: SLA/SLO 모니터링 로그 레벨
            forensic_log_level: Forensic 분석 로그 레벨
            emergency_log_level: Emergency Mode 로그 레벨
            chaos_log_level: Chaos Engineering 로그 레벨
            l2_storage_log_level: L2 Storage Resilience 로그 레벨
            include_timestamps: 로그에 타임스탬프 포함 여부
            include_request_id: 로그에 Request ID 포함 여부
            include_user_info: 로그에 사용자 정보 포함 여부
            console_output_enabled: 콘솔 로그 출력 활성화
            file_output_enabled: 파일 로그 출력 활성화
            structured_json: JSON 구조화 로그 포맷 사용

        Returns:
            dict: 업데이트된 설정값
        """
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("logging", **updates)

    # =========================================================================
    # Metrics Config
    # =========================================================================

    def get_metrics_config(self) -> dict[str, Any]:
        """Get metrics configuration."""
        return self._get_config("metrics")

    def update_metrics_config(
        self,
        enabled: bool | None = None,
        collection_interval_seconds: int | None = None,
        histogram_buckets: list | None = None,
        export_prometheus: bool | None = None,
        export_statsd: bool | None = None,
        statsd_host: str | None = None,
        statsd_port: int | None = None,
    ) -> dict[str, Any]:
        """Update metrics configuration."""
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("metrics", **updates)

    # =========================================================================
    # Error Budget Config
    # =========================================================================

    def get_error_budget_config(self) -> dict[str, Any]:
        """
        Get Error Budget configuration.

        Returns:
            dict: Error Budget 및 Burn Rate 임계값 설정
        """
        return self._get_config("error_budget")

    def update_error_budget_config(
        self,
        threshold_healthy: float | None = None,
        threshold_caution: float | None = None,
        threshold_warning: float | None = None,
        threshold_critical: float | None = None,
        burn_rate_fast_critical: float | None = None,
        burn_rate_fast_warning: float | None = None,
        burn_rate_slow_warning: float | None = None,
        burn_rate_slow_info: float | None = None,
        failsafe_alert_enabled: bool | None = None,
        failsafe_cooldown_seconds: int | None = None,
        # Heartbeat (Dead Man's Snitch) 설정
        heartbeat_enabled: bool | None = None,
        heartbeat_interval_seconds: int | None = None,
        heartbeat_timeout_seconds: int | None = None,
        # 복구 알림 (Recovery Notification) 설정
        recovery_alert_enabled: bool | None = None,
        recovery_alert_include_downtime: bool | None = None,
        # Override 에스컬레이션 설정
        escalation_enabled: bool | None = None,
        escalation_channel: str | None = None,
        escalation_mention: str | None = None,
    ) -> dict[str, Any]:
        """
        Update Error Budget configuration.

        Args:
            threshold_healthy: 정상 상태 임계값 (%)
            threshold_caution: 주의 상태 임계값 (%)
            threshold_warning: 경고 상태 임계값 (%)
            threshold_critical: 위험 상태 임계값 (%)
            burn_rate_fast_critical: 빠른 소진 위험 임계값 (x)
            burn_rate_fast_warning: 빠른 소진 경고 임계값 (x)
            burn_rate_slow_warning: 느린 소진 경고 임계값 (x)
            burn_rate_slow_info: 정상 소진율 임계값 (x)
            failsafe_alert_enabled: Fail-Safe 발동 시 알림 발송 여부
            failsafe_cooldown_seconds: 연속 알림 방지 쿨다운 (초)
            heartbeat_enabled: Heartbeat (Dead Man's Snitch) 활성화 여부
            heartbeat_interval_seconds: Heartbeat 발송 주기 (초)
            heartbeat_timeout_seconds: Heartbeat 타임아웃 (초, 이 시간 내 미응답시 Dead)
            recovery_alert_enabled: 복구 완료 알림 발송 여부
            recovery_alert_include_downtime: 복구 알림에 장애 시간 포함 여부
            escalation_enabled: Override 에스컬레이션 활성화 여부
            escalation_channel: 에스컬레이션 알림 채널 (예: #governance)
            escalation_mention: 에스컬레이션 멘션 대상 (예: @cto @security)

        Returns:
            dict: 업데이트된 설정값
        """
        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("error_budget", **updates)

    # =========================================================================
    # SLO Config (Service Level Objectives)
    # =========================================================================

    def get_slo_config(self) -> dict[str, Any]:
        """
        Get SLO configuration.

        Returns:
            dict: SLO 기본값 및 SLO 정의 목록
                - default_window_days: 새 SLO 생성 시 기본 윈도우
                - default_target: 새 SLO 생성 시 기본 타겟
                - default_fast_burn_rate: 새 SLO 생성 시 기본 빠른 소진율
                - default_slow_burn_rate: 새 SLO 생성 시 기본 느린 소진율
                - slos: SLO 정의 목록 (각각 name, target, window_days 등)
        """
        return self._get_config("slo")

    def update_slo_config(
        self,
        default_window_days: int | None = None,
        default_target: float | None = None,
        default_fast_burn_rate: float | None = None,
        default_slow_burn_rate: float | None = None,
        slo: dict[str, Any] | None = None,
        slos: list | None = None,
    ) -> dict[str, Any]:
        """
        Update SLO configuration.

        Args:
            default_window_days: 새 SLO 생성 시 기본 윈도우 (일)
            default_target: 새 SLO 생성 시 기본 타겟 (0.0~1.0)
            default_fast_burn_rate: 새 SLO 생성 시 기본 빠른 소진율
            default_slow_burn_rate: 새 SLO 생성 시 기본 느린 소진율
            slo: 추가/수정할 단일 SLO 정의
            slos: 추가/수정할 SLO 정의 목록

        Returns:
            dict: 업데이트된 SLO 설정
        """
        with self._lock:
            current = self._get_config("slo")

            # Update defaults
            if default_window_days is not None:
                current["default_window_days"] = default_window_days
                logger.info(
                    f"[RuntimeConfig] Updated slo.default_window_days = {default_window_days}"
                )
            if default_target is not None:
                current["default_target"] = default_target
                logger.info(
                    f"[RuntimeConfig] Updated slo.default_target = {default_target}"
                )
            if default_fast_burn_rate is not None:
                current["default_fast_burn_rate"] = default_fast_burn_rate
                logger.info(
                    f"[RuntimeConfig] Updated slo.default_fast_burn_rate = {default_fast_burn_rate}"
                )
            if default_slow_burn_rate is not None:
                current["default_slow_burn_rate"] = default_slow_burn_rate
                logger.info(
                    f"[RuntimeConfig] Updated slo.default_slow_burn_rate = {default_slow_burn_rate}"
                )

            # Add/update SLOs
            slos_to_update = []
            if slo is not None:
                slos_to_update.append(slo)
            if slos is not None:
                slos_to_update.extend(slos)

            for slo_def in slos_to_update:
                self._upsert_slo(current, slo_def)

            self._save_config("slo", current)
            return current.copy()

    def _upsert_slo(self, config: dict[str, Any], slo_def: dict[str, Any]) -> None:
        """Insert or update an SLO definition."""
        if "slos" not in config:
            config["slos"] = []

        slo_name = slo_def.get("name")
        if not slo_name:
            logger.warning(
                "[RuntimeConfig] SLO definition missing 'name' field, skipping"
            )
            return

        # Find existing SLO by name
        existing_idx = None
        for idx, existing in enumerate(config["slos"]):
            if existing.get("name") == slo_name:
                existing_idx = idx
                break

        # Apply defaults for new SLO
        if existing_idx is None:
            new_slo = {
                "name": slo_name,
                "sli_type": slo_def.get("sli_type", "availability"),
                "target": slo_def.get("target", config.get("default_target", 0.999)),
                "window_days": slo_def.get(
                    "window_days", config.get("default_window_days", 30)
                ),
                "description": slo_def.get("description", ""),
                "service_name": slo_def.get("service_name", ""),
                "domain": slo_def.get("domain", ""),
                "warning_threshold": slo_def.get("warning_threshold"),
                "critical_threshold": slo_def.get("critical_threshold"),
                "fast_burn_rate": slo_def.get(
                    "fast_burn_rate", config.get("default_fast_burn_rate", 14.4)
                ),
                "slow_burn_rate": slo_def.get(
                    "slow_burn_rate", config.get("default_slow_burn_rate", 3.0)
                ),
            }
            config["slos"].append(new_slo)
            logger.info(f"[RuntimeConfig] Added SLO: {slo_name}")
        else:
            # Update existing SLO (only provided fields)
            for key, value in slo_def.items():
                if value is not None:
                    config["slos"][existing_idx][key] = value
            logger.info(f"[RuntimeConfig] Updated SLO: {slo_name}")

    def delete_slo(self, slo_name: str) -> dict[str, Any]:
        """
        Delete an SLO by name.

        Args:
            slo_name: 삭제할 SLO 이름

        Returns:
            dict: 결과 (status, deleted_slo, remaining_count)
        """
        with self._lock:
            current = self._get_config("slo")

            if "slos" not in current:
                return {"status": "not_found", "error": f"SLO '{slo_name}' not found"}

            deleted_slo = None
            for slo in current["slos"]:
                if slo.get("name") == slo_name:
                    deleted_slo = slo
                    break

            if deleted_slo is None:
                return {"status": "not_found", "error": f"SLO '{slo_name}' not found"}

            current["slos"] = [s for s in current["slos"] if s.get("name") != slo_name]
            self._save_config("slo", current)

            logger.info(f"[RuntimeConfig] Deleted SLO: {slo_name}")
            return {
                "status": "deleted",
                "deleted_slo": deleted_slo,
                "remaining_count": len(current["slos"]),
            }

    def get_slo_by_name(self, slo_name: str) -> dict[str, Any] | None:
        """
        Get a specific SLO by name.

        Args:
            slo_name: SLO 이름

        Returns:
            dict or None: SLO 정의 또는 None
        """
        config = self._get_config("slo")
        for slo in config.get("slos", []):
            if slo.get("name") == slo_name:
                return slo
        return None

    # =========================================================================
    # Governance Config (RBAC, Emergency Escalation)
    # =========================================================================

    def get_governance_config(self) -> dict[str, Any]:
        """
        Get Governance configuration.

        Returns:
            dict: 거버넌스 설정 (임계값, 긴급 모드 자동 복귀 등)
                - threshold_operator: Operator 승인 상한 (기본: 0.15)
                - threshold_admin: Admin 승인 상한 (기본: 0.30)
                - emergency_expiry_hours: 긴급 모드 자동 복귀 시간 (기본: 8)
                - emergency_warning_hours: 경고 시작 시간 (기본: 4)
                - default_mode: 기본 운영 모드 (NORMAL/STRICT)
                - notify_on_emergency: 긴급 모드 전환 시 알림 발송
        """
        return self._get_config("governance")

    def update_governance_config(
        self,
        threshold_operator: float | None = None,
        threshold_admin: float | None = None,
        emergency_expiry_hours: int | None = None,
        emergency_warning_hours: int | None = None,
        emergency_final_warning_hours: int | None = None,
        default_mode: str | None = None,
        notify_on_emergency: bool | None = None,
        notify_channels: list | None = None,
        emergency_slack_channel: str | None = None,
        emergency_email_recipients: list | None = None,
        four_eyes_enabled: bool | None = None,
        four_eyes_expiry_hours: int | None = None,
    ) -> dict[str, Any]:
        """
        Update Governance configuration.

        Args:
            threshold_operator: Operator 승인 상한 (0.0~1.0, 기본: 0.15)
            threshold_admin: Admin 승인 상한 (0.0~1.0, 기본: 0.30)
            emergency_expiry_hours: 긴급 모드 자동 복귀까지 시간 (기본: 8)
            emergency_warning_hours: 경고 시작 시간 (기본: 4)
            emergency_final_warning_hours: 최종 경고 시간 (기본: 6)
            default_mode: 기본 운영 모드 (NORMAL/STRICT)
            notify_on_emergency: 긴급 모드 전환 시 알림 발송 여부
            notify_channels: 알림 채널 목록 (slack, email)
            emergency_slack_channel: 긴급 알림 Slack 채널
            emergency_email_recipients: 긴급 알림 이메일 수신자
            four_eyes_enabled: 4-Eyes (듀얼 승인) 활성화 여부
            four_eyes_expiry_hours: 4-Eyes 승인 요청 만료 시간

        Returns:
            dict: 업데이트된 Governance 설정
        """
        # Validate thresholds
        if threshold_operator is not None:
            if not (0.0 <= threshold_operator <= 1.0):
                raise ValueError("threshold_operator must be between 0.0 and 1.0")
        if threshold_admin is not None:
            if not (0.0 <= threshold_admin <= 1.0):
                raise ValueError("threshold_admin must be between 0.0 and 1.0")

        # Validate mode
        if default_mode is not None:
            if default_mode.upper() not in ("NORMAL", "STRICT"):
                raise ValueError("default_mode must be 'NORMAL' or 'STRICT'")
            default_mode = default_mode.upper()

        updates = {k: v for k, v in locals().items() if k != "self" and v is not None}
        return self._update_config("governance", **updates)

    # =========================================================================
    # Drift Threshold Config
    # =========================================================================

    def get_drift_threshold_config(self) -> dict[str, Any]:
        """
        Get Drift Threshold configuration.

        Returns:
            dict: Drift 임계값 설정
                - warning_threshold: 경고 임계값 (기본: 0.05 = 5%)
                - critical_threshold: 심각 임계값 (기본: 0.20 = 20%)
                - incident_threshold: 인시던트 임계값 (기본: 0.50 = 50%)
                - alert_enabled: 알림 활성화 여부
                - incident_auto_create: 인시던트 자동 생성 여부
        """
        return self._get_config("drift_threshold")

    def update_drift_threshold_config(
        self,
        warning_threshold: float | None = None,
        critical_threshold: float | None = None,
        incident_threshold: float | None = None,
        alert_enabled: bool | None = None,
        incident_auto_create: bool | None = None,
        changed_by: str = "system",
        reason: str = "",
    ) -> dict[str, Any]:
        """
        Update Drift Threshold configuration.

        Args:
            warning_threshold: 경고 임계값 (0.0~1.0, 기본: 0.05)
            critical_threshold: 심각 임계값 (0.0~1.0, 기본: 0.20)
            incident_threshold: 인시던트 임계값 (0.0~1.0, 기본: 0.50)
            alert_enabled: 알림 활성화 여부
            incident_auto_create: 인시던트 자동 생성 여부
            changed_by: 변경한 사용자
            reason: 변경 사유

        Returns:
            dict: 업데이트된 Drift Threshold 설정

        Raises:
            ValueError: 임계값이 올바른 순서가 아닌 경우
        """
        # Get current values for validation
        current = self._get_config("drift_threshold")

        # Apply updates to copies for validation
        new_warning = (
            warning_threshold
            if warning_threshold is not None
            else current.get("warning_threshold", 0.05)
        )
        new_critical = (
            critical_threshold
            if critical_threshold is not None
            else current.get("critical_threshold", 0.20)
        )
        new_incident = (
            incident_threshold
            if incident_threshold is not None
            else current.get("incident_threshold", 0.50)
        )

        # Validate thresholds order
        if not (0 < new_warning < new_critical < new_incident <= 1.0):
            raise ValueError(
                f"Thresholds must be: 0 < warning < critical < incident <= 1.0. "
                f"Got: warning={new_warning}, critical={new_critical}, incident={new_incident}"
            )

        updates = {
            k: v
            for k, v in {
                "warning_threshold": warning_threshold,
                "critical_threshold": critical_threshold,
                "incident_threshold": incident_threshold,
                "alert_enabled": alert_enabled,
                "incident_auto_create": incident_auto_create,
            }.items()
            if v is not None
        }

        return self._update_config(
            "drift_threshold",
            changed_by=changed_by,
            reason=reason or f"Updated fields: {list(updates.keys())}",
            **updates,
        )

    def reset_drift_threshold_config(
        self, changed_by: str = "system"
    ) -> dict[str, Any]:
        """
        Reset Drift Threshold configuration to defaults.

        Args:
            changed_by: 변경한 사용자

        Returns:
            dict: 기본값으로 리셋된 설정
        """
        default_config = DriftThresholdConfig().model_dump()
        self._save_config("drift_threshold", default_config)

        self._save_to_history(
            config_type="drift_threshold",
            values=default_config,
            changed_by=changed_by,
            reason="Reset to default values",
        )

        logger.info(f"[RuntimeConfig] Drift threshold config reset by {changed_by}")
        return default_config
