"""
Freeze Mode for Circuit Breaker

LOCKDOWN 상태에서 현재 CB 상태를 그대로 동결합니다.

Freeze Mode 동작:
- 자동 OPEN   → ❌ 금지
- 자동 CLOSE  → ❌ 금지  
- Canary Recovery → ❌ 금지
- 수동 OPEN   → ✅ 허용 (운영자 명시적 개입)
- 수동 CLOSE  → ✅ 허용 (운영자 명시적 개입)
- 현재 OPEN인 것  → OPEN 유지
- 현재 CLOSED인 것 → CLOSED 유지

설계 결정:
- 완전 비활성화: ❌ (CLOSE 안 하면 영원히 차단)
- OPEN만 금지: ❌ (자동 복구가 부하 유발 가능)
- Freeze Mode: ✅ (현 상태 유지, 안정성 최대)
"""

from __future__ import annotations

import structlog
from datetime import datetime, timezone

from selfhealing.services.circuit_breaker.models import FreezeModeState

logger = structlog.get_logger()


# =============================================================================
# Freeze Mode State Change Reasons
# =============================================================================


class FreezeReason:
    """Freeze Mode 활성화/비활성화 사유 상수."""

    LOCKDOWN_ENTRY = "LOCKDOWN 진입으로 인한 Freeze Mode 활성화"
    LOCKDOWN_EXIT = "LOCKDOWN 해제로 인한 Freeze Mode 비활성화"
    PANIC_THRESHOLD = "Panic Threshold 발동으로 인한 Freeze Mode 활성화"
    MANUAL_ACTIVATION = "운영자 수동 활성화"
    MANUAL_DEACTIVATION = "운영자 수동 비활성화"
    EMERGENCY_LEVEL_3 = "Emergency Level 3 진입으로 인한 Freeze Mode 활성화"


# =============================================================================
# Freeze Mode Manager
# =============================================================================


class FreezeModeManager:
    """
    Circuit Breaker Freeze Mode 관리자.

    LOCKDOWN 상태에서 모든 CB 자동 상태 변경을 금지하고,
    현재 상태를 그대로 동결합니다.

    Usage:
        manager = FreezeModeManager()

        # Freeze Mode 상태 확인
        if manager.is_active():
            return  # 자동 상태 변경 금지

        # Freeze Mode 활성화 (LOCKDOWN 진입 시)
        manager.activate(reason="LOCKDOWN 진입")

        # 상태 변경 허용 여부 확인
        allowed, reason = manager.should_allow_state_change(
            service_id="payment-api",
            new_state="OPEN",
            is_manual=False
        )

    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 6 - LOCKDOWN Freeze Mode
    """

    _instance: FreezeModeManager | None = None

    def __new__(cls):
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return

        self._state = FreezeModeState()
        self._emergency_manager = None
        self._initialized = True

    @property
    def emergency_manager(self):
        """Emergency Manager 지연 로딩."""
        if self._emergency_manager is None:
            try:
                from selfhealing.services.emergency_mode import EmergencyModeManager

                self._emergency_manager = EmergencyModeManager()
            except ImportError:
                logger.debug("freeze_mode.emergencymodemanager_available")
        return self._emergency_manager

    def is_active(self) -> bool:
        """
        Freeze Mode 활성화 여부.

        Returns:
            bool: Freeze Mode 활성화 상태
        """
        # Emergency Level 3 (LOCKDOWN)이면 자동으로 활성화
        if self._is_lockdown():
            return True
        return self._state.active

    def _is_lockdown(self) -> bool:
        """현재 Emergency Level이 LOCKDOWN(Level 3)인지 확인."""
        if self.emergency_manager is None:
            return False

        try:
            level = self.emergency_manager.get_current_level()
            level_value = level.value if hasattr(level, "value") else int(level)
            return level_value >= 3  # LEVEL_3 = LOCKDOWN
        except Exception:
            return False

    def get_state(self) -> FreezeModeState:
        """
        현재 Freeze Mode 상태 반환.

        Returns:
            FreezeModeState: 현재 상태
        """
        return FreezeModeState(
            active=self.is_active(),
            activated_at=self._state.activated_at,
            reason=self._state.reason
            or (FreezeReason.LOCKDOWN_ENTRY if self._is_lockdown() else ""),
            activated_by=self._state.activated_by
            or ("system" if self._is_lockdown() else ""),
        )

    def activate(
        self,
        reason: str = FreezeReason.MANUAL_ACTIVATION,
        activated_by: str = "system",
    ) -> bool:
        """
        Freeze Mode 활성화.

        Args:
            reason: 활성화 사유
            activated_by: 활성화 주체 ("system" 또는 "operator:<username>")

        Returns:
            bool: 활성화 성공 여부
        """
        previous_state = self._state.active

        self._state = FreezeModeState(
            active=True,
            activated_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            activated_by=activated_by,
        )

        logger.warning(
            "freeze_mode.activated",
            activated_by=activated_by,
            reason=reason,
        )

        # Audit 기록
        try:
            from selfhealing.services.audit_helpers import log_freeze_mode_audit

            log_freeze_mode_audit(
                active=True,
                reason=reason,
                activated_by=activated_by,
                previous_state=previous_state,
                emergency_level=self._get_emergency_level_str(),
            )
        except Exception as e:
            logger.debug(
                "freeze_mode.audit_log_failed",
                error=e,
            )

        return True

    def deactivate(
        self,
        reason: str = FreezeReason.MANUAL_DEACTIVATION,
        deactivated_by: str = "system",
    ) -> bool:
        """
        Freeze Mode 비활성화.

        Note: LOCKDOWN 상태에서는 수동 비활성화가 불가능합니다.
              Emergency Level을 먼저 낮춰야 합니다.

        Args:
            reason: 비활성화 사유
            deactivated_by: 비활성화 주체

        Returns:
            bool: 비활성화 성공 여부
        """
        # LOCKDOWN 상태에서는 수동 비활성화 불가
        if self._is_lockdown():
            logger.warning(
                "[FreezeMode] Cannot deactivate during LOCKDOWN. "
                "Lower Emergency Level first."
            )
            return False

        previous_state = self._state.active

        self._state = FreezeModeState(
            active=False,
            activated_at=None,
            reason="",
            activated_by="",
        )

        logger.info(
            "freeze_mode.deactivated",
            deactivated_by=deactivated_by,
            reason=reason,
        )

        # Audit 기록
        try:
            from selfhealing.services.audit_helpers import log_freeze_mode_audit

            log_freeze_mode_audit(
                active=False,
                reason=reason,
                activated_by=deactivated_by,
                previous_state=previous_state,
                emergency_level=self._get_emergency_level_str(),
            )
        except Exception as e:
            logger.debug(
                "freeze_mode.audit_log_failed",
                error=e,
            )

        return True

    def _get_emergency_level_str(self) -> str | None:
        """현재 Emergency Level 문자열 반환."""
        if self.emergency_manager is None:
            return None
        try:
            level = self.emergency_manager.get_current_level()
            return level.name if hasattr(level, "name") else str(level)
        except Exception:
            return None

    def should_allow_state_change(
        self,
        service_id: str,
        new_state: str,
        is_manual: bool = False,
    ) -> tuple[bool, str]:
        """
        CB 상태 변경 허용 여부 판단.

        Freeze Mode에서는 수동 조작만 허용됩니다.

        Args:
            service_id: 대상 서비스 ID
            new_state: 새로운 상태 (OPEN, CLOSED, HALF_OPEN)
            is_manual: 수동 조작 여부

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        if not self.is_active():
            return True, ""

        # Freeze Mode에서 수동 조작은 허용
        if is_manual:
            logger.info(
                f"[FreezeMode] Manual override allowed: "
                f"service={service_id}, new_state={new_state}"
            )
            return True, ""

        # 자동 조작은 금지
        reason = (
            f"LOCKDOWN: Freeze Mode active - automatic state change to {new_state} "
            f"blocked for {service_id}. Use manual override."
        )
        logger.warning(
            "freeze_mode.event",
            reason=reason,
        )

        return False, reason

    def should_allow_auto_open(self, service_id: str) -> tuple[bool, str]:
        """
        자동 OPEN 허용 여부.

        Args:
            service_id: 대상 서비스 ID

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        return self.should_allow_state_change(
            service_id=service_id,
            new_state="OPEN",
            is_manual=False,
        )

    def should_allow_auto_close(self, service_id: str) -> tuple[bool, str]:
        """
        자동 CLOSE 허용 여부.

        Args:
            service_id: 대상 서비스 ID

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        return self.should_allow_state_change(
            service_id=service_id,
            new_state="CLOSED",
            is_manual=False,
        )

    def should_allow_canary_recovery(self, service_id: str) -> tuple[bool, str]:
        """
        Canary Recovery 허용 여부.

        Freeze Mode에서는 Canary Recovery도 금지됩니다.

        Args:
            service_id: 대상 서비스 ID

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        if not self.is_active():
            return True, ""

        reason = (
            f"LOCKDOWN: Freeze Mode active - Canary Recovery blocked for {service_id}"
        )
        return False, reason


# =============================================================================
# Convenience Functions
# =============================================================================


_manager_instance: FreezeModeManager | None = None


def get_freeze_mode_manager() -> FreezeModeManager:
    """
    전역 FreezeModeManager 인스턴스 반환.
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = FreezeModeManager()
    return _manager_instance


def is_freeze_mode_active() -> bool:
    """
    Freeze Mode 활성화 여부 간편 확인.

    Returns:
        bool: Freeze Mode 활성화 상태
    """
    return get_freeze_mode_manager().is_active()


def should_allow_cb_state_change(
    service_id: str,
    new_state: str,
    is_manual: bool = False,
) -> bool:
    """
    CB 상태 변경 허용 여부 간편 확인.

    Args:
        service_id: 대상 서비스 ID
        new_state: 새로운 상태
        is_manual: 수동 조작 여부

    Returns:
        bool: 허용 여부
    """
    allowed, _ = get_freeze_mode_manager().should_allow_state_change(
        service_id=service_id,
        new_state=new_state,
        is_manual=is_manual,
    )
    return allowed
