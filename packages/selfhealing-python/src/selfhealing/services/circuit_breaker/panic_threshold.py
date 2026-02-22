"""
Panic Threshold for Circuit Breaker

70% 이상의 Circuit Breaker가 동시에 OPEN되면, 개별 서비스 문제가 아니라
인프라 전체 붕괴로 판단합니다. 이때 자율 운영 엔진이 스스로 Emergency Level 3를
선포하고 모든 자동 복구를 중단합니다.

동작 흐름:
    get_open_circuits() → OPEN 비율 계산 → 70% 초과?
                                            ↓ Yes
                                    PANIC THRESHOLD TRIGGERED!
                                            ↓
                                    Emergency Level 3 자동 선포
                                            ↓
                                    Global Lockdown (Freeze Mode)
                                            ↓
                                    모든 자동 복구 중단:
                                    - Replay 중지
                                    - Canary Recovery 중지
                                    - Auto OPEN/CLOSE 금지
                                    - 수동 개입 대기
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from selfhealing.services.circuit_breaker.models import PanicThresholdConfig

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker_service import CircuitBreakerService
    from selfhealing.services.emergency_mode import EmergencyModeManager

logger = structlog.get_logger()


# =============================================================================
# Panic Threshold Result
# =============================================================================


@dataclass
class PanicThresholdResult:
    """
    Panic Threshold 체크 결과.

    Attributes:
        triggered: Panic Threshold 발동 여부
        open_rate: 현재 OPEN 비율 (%)
        open_count: OPEN 상태 CB 수
        total_count: 전체 등록된 CB 수
        open_circuits: OPEN 상태인 서비스 목록
        action_taken: 취한 조치
        halted_systems: 중단된 시스템 목록
        reason: 발동/미발동 사유
        timestamp: 체크 시간
    """

    triggered: bool = False
    open_rate: float = 0.0
    open_count: int = 0
    total_count: int = 0
    open_circuits: list[str] = field(default_factory=list)
    action_taken: str | None = None
    halted_systems: list[str] = field(default_factory=list)
    reason: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# =============================================================================
# Panic Threshold Monitor
# =============================================================================


class PanicThresholdMonitor:
    """
    시스템 전체 OPEN 비율을 모니터링하고 Panic Threshold 발동.

    전체 CB 중 70% 이상이 OPEN 상태이면 시스템 전체 붕괴로 판단하고
    Emergency Level 3를 자동 선포합니다.

    Usage:
        monitor = PanicThresholdMonitor()

        # 주기적으로 체크 (예: 5초마다)
        result = monitor.check_panic_threshold()

        if result.triggered:
            # Panic 발동됨 - 이미 Emergency Level 3로 에스컬레이션됨
            send_critical_alert(result)

    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 14 - Panic Threshold
    """

    def __init__(
        self,
        config: PanicThresholdConfig | None = None,
        circuit_breaker_service: CircuitBreakerService | None = None,
        emergency_manager: EmergencyModeManager | None = None,
    ):
        """
        Initialize PanicThresholdMonitor.

        Args:
            config: Panic Threshold 설정 (기본값 사용 시 None)
            circuit_breaker_service: CB 서비스 (주입 없으면 전역 인스턴스 사용)
            emergency_manager: Emergency Manager (주입 없으면 전역 인스턴스 사용)
        """
        self.config = config or PanicThresholdConfig()
        self._cb_service = circuit_breaker_service
        self._emergency_manager = emergency_manager
        self._consecutive_triggers = 0
        self._last_check_result: PanicThresholdResult | None = None

    @property
    def cb_service(self):
        """Circuit Breaker Service 지연 로딩."""
        if self._cb_service is None:
            try:
                from selfhealing.services.circuit_breaker import CircuitBreakerService

                self._cb_service = CircuitBreakerService()
            except ImportError:
                logger.warning("panic_threshold.circuitbreakerservice_available")
        return self._cb_service

    @property
    def emergency_manager(self):
        """Emergency Manager 지연 로딩."""
        if self._emergency_manager is None:
            try:
                from selfhealing.services.emergency_mode import EmergencyModeManager

                self._emergency_manager = EmergencyModeManager()
            except ImportError:
                logger.warning("panic_threshold.emergencymodemanager_available")
        return self._emergency_manager

    def check_panic_threshold(self) -> PanicThresholdResult:
        """
        시스템 전체 OPEN 비율 확인 및 Panic Threshold 발동.

        Returns:
            PanicThresholdResult: 감지 결과 및 취한 조치
        """
        if not self.config.enabled:
            return PanicThresholdResult(triggered=False, reason="Panic Threshold disabled")

        # 1. 모든 Circuit 상태 수집
        open_circuits, total_circuits = self._get_circuit_stats()

        # 2. 최소 서비스 수 확인 (오탐 방지)
        min_services = getattr(self.config, "min_registered_services", 3)
        if len(total_circuits) < min_services:
            result = PanicThresholdResult(
                triggered=False,
                open_rate=0.0,
                open_count=len(open_circuits),
                total_count=len(total_circuits),
                open_circuits=open_circuits,
                reason=f"Insufficient services ({len(total_circuits)} < {min_services})",
            )
            self._last_check_result = result
            return result

        # 3. OPEN 비율 계산
        open_rate = (len(open_circuits) / len(total_circuits)) * 100

        # 4. 임계값 초과 확인
        if open_rate >= self.config.threshold_percent:
            self._consecutive_triggers += 1

            # 연속 감지 횟수 충족 시 Panic 발동
            consecutive_required = getattr(self.config, "consecutive_triggers_required", 2)
            if self._consecutive_triggers >= consecutive_required:
                result = self._trigger_panic(
                    open_rate=open_rate,
                    open_circuits=open_circuits,
                    total_circuits=total_circuits,
                )
                self._last_check_result = result
                return result
            else:
                result = PanicThresholdResult(
                    triggered=False,
                    open_rate=open_rate,
                    open_count=len(open_circuits),
                    total_count=len(total_circuits),
                    open_circuits=open_circuits,
                    reason=f"Threshold exceeded but waiting for consecutive triggers "
                    f"({self._consecutive_triggers}/{consecutive_required})",
                )
                self._last_check_result = result
                return result
        else:
            self._consecutive_triggers = 0

        result = PanicThresholdResult(
            triggered=False,
            open_rate=open_rate,
            open_count=len(open_circuits),
            total_count=len(total_circuits),
            open_circuits=open_circuits,
            reason=f"Below threshold ({open_rate:.1f}% < {self.config.threshold_percent}%)",
        )
        self._last_check_result = result
        return result

    def _get_circuit_stats(self) -> tuple[list[str], list[str]]:
        """
        모든 Circuit 상태 수집.

        Returns:
            tuple[List[str], List[str]]: (OPEN 서비스 목록, 전체 서비스 목록)
        """
        if self.cb_service is None:
            return [], []

        try:
            # CB 서비스에서 모든 상태 조회
            all_states = self.cb_service.repository.get_all_states()

            open_circuits = []
            total_circuits = []

            for state in all_states:
                service_name = state.service_name
                total_circuits.append(service_name)

                # OPEN 상태 확인
                if state.state.lower() == "open":
                    open_circuits.append(service_name)

            return open_circuits, total_circuits
        except Exception as e:
            logger.warning(
                "panic_threshold.failed_get_circuit_stats",
                error=e,
            )
            return [], []

    def _trigger_panic(
        self,
        open_rate: float,
        open_circuits: list[str],
        total_circuits: list[str],
    ) -> PanicThresholdResult:
        """
        Panic Threshold 발동 및 Emergency Level 3 선포.

        Args:
            open_rate: 현재 OPEN 비율
            open_circuits: OPEN 상태 서비스 목록
            total_circuits: 전체 서비스 목록

        Returns:
            PanicThresholdResult: 발동 결과
        """
        halted_systems = ["replay", "canary_recovery", "auto_open", "auto_close"]
        action_taken = "emergency_level_3_escalation"

        logger.critical(
            "panic_threshold_triggered_circuits",
            count=len(open_circuits),
            count_1=len(total_circuits),
            open_rate=open_rate,
        )

        # 1. Audit 기록
        self._log_panic_audit(
            open_rate=open_rate,
            open_circuits=open_circuits,
            total_count=len(total_circuits),
            action_taken=action_taken,
            halted_systems=halted_systems,
        )

        # 2. Emergency Level 3 자동 선포
        if self.config.action == "freeze":
            self._escalate_to_level_3(
                open_rate=open_rate,
                open_circuits=open_circuits,
            )

            # 3. Freeze Mode 활성화
            self._activate_freeze_mode(open_rate=open_rate)

        # 4. 운영팀 알림 (구현은 알림 서비스에 위임)
        self._notify_critical(
            open_rate=open_rate,
            open_count=len(open_circuits),
            total_count=len(total_circuits),
            open_circuits=open_circuits,
            halted_systems=halted_systems,
        )

        return PanicThresholdResult(
            triggered=True,
            open_rate=open_rate,
            open_count=len(open_circuits),
            total_count=len(total_circuits),
            open_circuits=open_circuits,
            action_taken=action_taken,
            halted_systems=halted_systems,
            reason=f"Panic Threshold triggered (Open Rate: {open_rate:.1f}%)",
        )

    def _log_panic_audit(
        self,
        open_rate: float,
        open_circuits: list[str],
        total_count: int,
        action_taken: str,
        halted_systems: list[str],
    ) -> None:
        """Panic Threshold 발동 Audit 기록."""
        try:
            from selfhealing.services.audit_helpers import log_panic_threshold_audit

            log_panic_threshold_audit(
                open_rate=open_rate,
                threshold=self.config.threshold_percent,
                open_count=len(open_circuits),
                total_count=total_count,
                open_circuits=open_circuits,
                action_taken=action_taken,
                halted_systems=halted_systems,
            )
        except Exception as e:
            logger.warning(
                "panic_threshold.audit_log_failed",
                error=e,
            )

    def _escalate_to_level_3(
        self,
        open_rate: float,
        open_circuits: list[str],
    ) -> None:
        """Emergency Level 3로 에스컬레이션."""
        if self.emergency_manager is None:
            logger.warning("panic_threshold.emergencymanager_available_escalation")
            return

        try:
            from selfhealing.services.emergency_mode.enums import EmergencyLevel

            self.emergency_manager.escalate_to_level(
                level=EmergencyLevel.LEVEL_3,
                reason=f"Panic Threshold: {open_rate:.1f}% of circuits are OPEN",
                triggered_by="PanicThresholdMonitor",
            )

            logger.warning(
                "panic_threshold.escalated_emergency_level",
                open_rate=open_rate,
                count=len(open_circuits),
            )
        except Exception as e:
            logger.exception(
                "panic_threshold.failed_escalate_level",
                error=e,
            )

    def _activate_freeze_mode(self, open_rate: float) -> None:
        """Freeze Mode 활성화."""
        try:
            from selfhealing.services.circuit_breaker.freeze_mode import (
                FreezeReason,
                get_freeze_mode_manager,
            )

            manager = get_freeze_mode_manager()
            manager.activate(
                reason=f"{FreezeReason.PANIC_THRESHOLD} (Open Rate: {open_rate:.1f}%)",
                activated_by="PanicThresholdMonitor",
            )
        except Exception as e:
            logger.warning(
                "panic_threshold.failed_activate_freeze_mode",
                error=e,
            )

    def _notify_critical(
        self,
        open_rate: float,
        open_count: int,
        total_count: int,
        open_circuits: list[str],
        halted_systems: list[str],
    ) -> None:
        """운영팀에 긴급 알림 전송."""
        # 알림은 별도 시스템에서 처리 (로깅만 수행)
        logger.critical(
            "panic_threshold_emergency_level",
            open_count=open_count,
            total_count=total_count,
            open_rate=open_rate,
            value=', '.join(halted_systems),
            value_4=', '.join(open_circuits),
        )

    def get_last_result(self) -> PanicThresholdResult | None:
        """
        마지막 체크 결과 반환.

        Returns:
            Optional[PanicThresholdResult]: 마지막 체크 결과
        """
        return self._last_check_result

    def reset_consecutive_count(self) -> None:
        """연속 감지 카운터 리셋 (테스트/디버깅용)."""
        self._consecutive_triggers = 0


# =============================================================================
# Convenience Functions
# =============================================================================


_monitor_instance: PanicThresholdMonitor | None = None


def get_panic_threshold_monitor() -> PanicThresholdMonitor:
    """
    전역 PanicThresholdMonitor 인스턴스 반환.
    """
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = PanicThresholdMonitor()
    return _monitor_instance


def check_panic_threshold() -> PanicThresholdResult:
    """
    Panic Threshold 체크 간편 함수.

    Returns:
        PanicThresholdResult: 체크 결과
    """
    return get_panic_threshold_monitor().check_panic_threshold()


def is_panic_threshold_triggered() -> bool:
    """
    Panic Threshold 발동 여부 간편 확인.

    마지막 체크 결과를 기반으로 확인합니다.

    Returns:
        bool: Panic Threshold 발동 여부
    """
    monitor = get_panic_threshold_monitor()
    last_result = monitor.get_last_result()
    if last_result is None:
        return False
    return last_result.triggered
