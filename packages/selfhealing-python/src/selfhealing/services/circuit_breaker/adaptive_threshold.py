"""
Adaptive Threshold for Circuit Breaker

Emergency Level에 따른 CB 임계값 자동 조정.
위기 상황일수록 더 보수적(느슨하게) 설정하여 자가 유도 블랙아웃을 방지합니다.

설계 근거:
- NORMAL: 5회 / 60초 (표준 감지 속도)
- ELEVATED: 7.5회 / 90초 (약간 보수적)
- HIGH: 10회 / 120초 (가짜 에러에 안 속음)
- CRITICAL: 15회 / 180초 (진짜 장애만 감지)
- LOCKDOWN: ∞ / ∞ (자동 OPEN 금지)

핵심 통찰:
    위기 상황에서 "더 민감하게"가 아니라 "더 보수적으로" 가야 합니다.
    네트워크 지터(jitter)로 인한 일시적 에러에 CB가 반응하면
    자가 유도 블랙아웃이 발생합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from selfhealing.services.circuit_breaker.models import (
    AdaptiveThresholdPolicy,
    ThresholdMultiplier,
)

if TYPE_CHECKING:
    from selfhealing.services.emergency_mode import EmergencyModeManager

logger = structlog.get_logger()


# =============================================================================
# Emergency Level to Adaptive Threshold Mapping
# =============================================================================

# Emergency Level 문자열 매핑 (Enum value -> Threshold Level)
EMERGENCY_LEVEL_MAPPING = {
    0: "NORMAL",  # EmergencyLevel.NORMAL
    1: "ELEVATED",  # EmergencyLevel.LEVEL_1
    2: "HIGH",  # EmergencyLevel.LEVEL_2
    3: "LOCKDOWN",  # EmergencyLevel.LEVEL_3 = LOCKDOWN
}


@dataclass
class AdjustedThreshold:
    """
    조정된 CB 임계값.

    Attributes:
        failure_threshold: 조정된 실패 횟수 임계값
        window_seconds: 조정된 관찰 윈도우 (초)
        emergency_level: 적용된 Emergency Level
        multiplier: 적용된 배율 정보
        is_lockdown: LOCKDOWN 상태 여부 (자동 OPEN 금지)
    """

    failure_threshold: float
    window_seconds: float
    emergency_level: str
    multiplier: ThresholdMultiplier
    is_lockdown: bool = False


class AdaptiveThresholdManager:
    """
    Emergency Level에 따른 CB 임계값 자동 조정 관리자.

    시스템 Emergency Level에 따라 CB 임계값을 자동으로 조정합니다.
    위기 상황일수록 더 보수적(느슨하게) 설정하여 자가 유도 블랙아웃을 방지합니다.

    Usage:
        manager = AdaptiveThresholdManager()

        # 현재 Emergency Level에 맞는 임계값 조회
        threshold = manager.get_adjusted_threshold()

        # 특정 Emergency Level에 맞는 임계값 조회
        threshold = manager.get_adjusted_threshold(emergency_level="HIGH")

        # 자동 OPEN 허용 여부 확인
        if manager.should_allow_auto_open():
            circuit_breaker.open()

    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 5 - Adaptive Threshold (Emergency Level 연동)
    """

    def __init__(
        self,
        policy: AdaptiveThresholdPolicy | None = None,
        emergency_manager: EmergencyModeManager | None = None,
    ):
        """
        Initialize AdaptiveThresholdManager.

        Args:
            policy: Adaptive Threshold 정책 (기본값 사용 시 None)
            emergency_manager: Emergency Mode Manager (주입 없으면 전역 인스턴스 사용)
        """
        self.policy = policy or AdaptiveThresholdPolicy()
        self._emergency_manager = emergency_manager

    @property
    def emergency_manager(self):
        """Emergency Manager 지연 로딩."""
        if self._emergency_manager is None:
            try:
                from selfhealing.services.emergency_mode import EmergencyModeManager

                self._emergency_manager = EmergencyModeManager()
            except ImportError:
                logger.warning("adaptive_threshold.emergencymodemanager_available_using_normal")
        return self._emergency_manager

    def get_current_emergency_level(self) -> str:
        """
        현재 Emergency Level을 문자열로 반환.

        Returns:
            str: Emergency Level ("NORMAL", "ELEVATED", "HIGH", "CRITICAL", "LOCKDOWN")
        """
        if self.emergency_manager is None:
            return "NORMAL"

        try:
            level = self.emergency_manager.get_current_level()
            # EmergencyLevel.LEVEL_X.value를 Adaptive Threshold Level로 매핑
            level_value = level.value if hasattr(level, "value") else int(level)
            return EMERGENCY_LEVEL_MAPPING.get(level_value, "NORMAL")
        except Exception as e:
            logger.warning(
                "adaptive_threshold.failed_get_emergency_level",
                error=e,
            )
            return "NORMAL"

    def get_adjusted_threshold(
        self,
        emergency_level: str | None = None,
        service_id: str | None = None,
    ) -> AdjustedThreshold:
        """
        Emergency Level에 맞는 조정된 임계값 반환.

        Args:
            emergency_level: 명시적 Emergency Level (None이면 현재 레벨 사용)
            service_id: 서비스 ID (서비스별 오버라이드 확인용, 향후 확장)

        Returns:
            AdjustedThreshold: 조정된 임계값 정보
        """
        if not self.policy.enabled:
            # Adaptive Threshold 비활성화 시 기본값 반환
            return AdjustedThreshold(
                failure_threshold=float(self.policy.base_failure_threshold),
                window_seconds=float(self.policy.base_window_seconds),
                emergency_level="DISABLED",
                multiplier=ThresholdMultiplier(failure=1.0, window=1.0),
                is_lockdown=False,
            )

        # Emergency Level 결정
        level = emergency_level or self.get_current_emergency_level()

        # 배율 조회 (없으면 NORMAL 기본값)
        multiplier = self.policy.level_multipliers.get(
            level,
            self.policy.level_multipliers.get("NORMAL", ThresholdMultiplier(failure=1.0, window=1.0)),
        )

        # 조정된 임계값 계산
        adjusted_failure = self.policy.base_failure_threshold * multiplier.failure
        adjusted_window = self.policy.base_window_seconds * multiplier.window

        is_lockdown = level == "LOCKDOWN" or multiplier.failure == float("inf")

        logger.debug(
            "adaptive_threshold.event",
            threshold_level=level,
            adjusted_failure=adjusted_failure,
            adjusted_window=adjusted_window,
            is_lockdown=is_lockdown,
        )

        return AdjustedThreshold(
            failure_threshold=adjusted_failure,
            window_seconds=adjusted_window,
            emergency_level=level,
            multiplier=multiplier,
            is_lockdown=is_lockdown,
        )

    def should_allow_auto_open(
        self,
        service_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        자동 OPEN 허용 여부 판단.

        LOCKDOWN 상태에서는 자동 OPEN이 금지됩니다.

        Args:
            service_id: 서비스 ID (향후 서비스별 정책 적용)

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        threshold = self.get_adjusted_threshold(service_id=service_id)

        if threshold.is_lockdown:
            return (
                False,
                f"LOCKDOWN: Auto OPEN blocked - {threshold.multiplier.description}",
            )

        return True, ""

    def should_allow_auto_close(
        self,
        service_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        자동 CLOSE 허용 여부 판단.

        LOCKDOWN 상태에서는 자동 CLOSE도 금지됩니다 (Freeze Mode).

        Args:
            service_id: 서비스 ID (향후 서비스별 정책 적용)

        Returns:
            Tuple[bool, str]: (허용 여부, 거부 시 사유)
        """
        threshold = self.get_adjusted_threshold(service_id=service_id)

        if threshold.is_lockdown:
            return False, "LOCKDOWN: Auto CLOSE blocked - Freeze Mode active"

        return True, ""

    def check_threshold_exceeded(
        self,
        failure_count: int,
        window_start_time: float,
        current_time: float,
        service_id: str | None = None,
    ) -> tuple[bool, AdjustedThreshold]:
        """
        실패 횟수가 조정된 임계값을 초과했는지 확인.

        Args:
            failure_count: 현재 실패 횟수
            window_start_time: 윈도우 시작 시간 (Unix timestamp)
            current_time: 현재 시간 (Unix timestamp)
            service_id: 서비스 ID

        Returns:
            Tuple[bool, AdjustedThreshold]: (초과 여부, 적용된 임계값)
        """
        threshold = self.get_adjusted_threshold(service_id=service_id)

        # LOCKDOWN에서는 절대 초과하지 않음 (자동 OPEN 금지)
        if threshold.is_lockdown:
            return False, threshold

        # 윈도우 내 시간인지 확인
        elapsed = current_time - window_start_time
        if elapsed > threshold.window_seconds:
            # 윈도우 밖이면 카운터 리셋으로 처리
            return False, threshold

        # 실패 횟수가 임계값 초과 확인
        exceeded = failure_count >= threshold.failure_threshold

        if exceeded:
            logger.info(
                "adaptive_threshold.threshold_exceeded",
                failure_count=failure_count,
                threshold=threshold.failure_threshold,
                emergency_level=threshold.emergency_level,
            )

        return exceeded, threshold


# =============================================================================
# Convenience Functions
# =============================================================================


_manager_instance: AdaptiveThresholdManager | None = None


def get_adaptive_threshold_manager() -> AdaptiveThresholdManager:
    """
    전역 AdaptiveThresholdManager 인스턴스 반환.

    싱글톤 패턴으로 인스턴스를 관리합니다.
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = AdaptiveThresholdManager()
    return _manager_instance


def get_adjusted_cb_threshold(
    service_id: str | None = None,
) -> tuple[float, float]:
    """
    현재 Emergency Level에 맞는 CB 임계값 반환.

    Convenience 함수로, AdaptiveThresholdManager를 직접 사용하지 않고
    간단히 임계값을 조회할 수 있습니다.

    Args:
        service_id: 서비스 ID (향후 서비스별 정책 적용)

    Returns:
        Tuple[float, float]: (실패 횟수 임계값, 윈도우 초)
    """
    manager = get_adaptive_threshold_manager()
    threshold = manager.get_adjusted_threshold(service_id=service_id)
    return threshold.failure_threshold, threshold.window_seconds


def should_allow_cb_auto_open(service_id: str | None = None) -> bool:
    """
    CB 자동 OPEN 허용 여부 간편 확인.

    Args:
        service_id: 서비스 ID

    Returns:
        bool: 자동 OPEN 허용 여부
    """
    manager = get_adaptive_threshold_manager()
    allowed, _ = manager.should_allow_auto_open(service_id=service_id)
    return allowed
