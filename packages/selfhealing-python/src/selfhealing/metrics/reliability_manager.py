"""
Metric Reliability Manager.

Coordinates metric data reliability across all fallback layers:
1. Real-time Push Events
2. DB Query (Manual Sync)
3. Redis Air-Gap
4. L1 Local Snapshot
5. Safe Defaults

Implements Conservative Fallback with gradual stabilization.

Design Philosophy:
- "모르면 일단 막아라" (Unknown = Conservative)
- Gradual recovery from strict mode
- Transparent reliability reporting
"""

from __future__ import annotations

import structlog
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = structlog.get_logger()


class ReliabilityLevel(str, Enum):
    """메트릭 신뢰도 레벨."""

    HIGH = "high"  # 실시간 Push 또는 최근 동기화
    MEDIUM = "medium"  # L1 스냅샷 또는 약간 오래된 데이터
    LOW = "low"  # 오래된 스냅샷
    UNKNOWN = "unknown"  # 데이터 없음 → Safe Defaults 사용
    RECOVERING = "recovering"  # 복구 중 (점진적 완화)


class OperatingMode(str, Enum):
    """시스템 운영 모드."""

    NORMAL = "normal"  # 정상 모드
    CAUTIOUS = "cautious"  # 주의 모드 (일부 제한)
    STRICT = "strict"  # 엄격 모드 (보수적 설정)
    EMERGENCY = "emergency"  # 비상 모드 (최소 기능)


@dataclass
class ReliabilityThresholds:
    """신뢰도 판단 임계값."""

    # 데이터 나이 임계값 (초)
    high_max_age: float = 60.0  # 1분 이내 = HIGH
    medium_max_age: float = 300.0  # 5분 이내 = MEDIUM
    low_max_age: float = 3600.0  # 1시간 이내 = LOW

    # 복구 관련
    stabilization_duration: float = 60.0  # 안정화 기간 (초)
    consecutive_syncs_for_normal: int = 3  # 정상 복귀에 필요한 연속 동기화 수


@dataclass
class MetricReliabilityState:
    """메트릭 신뢰도 상태."""

    domain: str
    reliability_level: ReliabilityLevel = ReliabilityLevel.UNKNOWN
    operating_mode: OperatingMode = OperatingMode.STRICT

    last_sync_time: float | None = None
    last_sync_source: str = "none"
    consecutive_successful_syncs: int = 0

    # 복구 관련
    stabilization_start: float | None = None
    mode_transition_time: float | None = None

    # 사용 중인 값 정보
    current_value: Any = None
    value_source: str = "none"  # "push", "db", "airgap", "snapshot", "default"

    @property
    def is_data_fresh(self) -> bool:
        """데이터가 신선한지 여부."""
        return self.reliability_level in (
            ReliabilityLevel.HIGH,
            ReliabilityLevel.MEDIUM,
        )

    @property
    def age_seconds(self) -> float | None:
        """마지막 동기화 이후 경과 시간."""
        if self.last_sync_time is None:
            return None
        return time.time() - self.last_sync_time

    @property
    def stabilization_progress(self) -> float:
        """안정화 진행률 (0.0 ~ 1.0)."""
        if self.operating_mode != OperatingMode.CAUTIOUS:
            return 1.0 if self.operating_mode == OperatingMode.NORMAL else 0.0

        if self.stabilization_start is None:
            return 0.0

        elapsed = time.time() - self.stabilization_start
        return min(1.0, elapsed / 60.0)  # 60초 기준


class MetricReliabilityManager:
    """
    메트릭 신뢰도 관리자.

    여러 데이터 소스의 상태를 추적하고,
    적절한 fallback 전략과 운영 모드를 결정합니다.

    Features:
    - Multi-source reliability tracking
    - Conservative fallback mode
    - Gradual stabilization on recovery
    - Prometheus metric export for monitoring

    Example:
        >>> manager = MetricReliabilityManager()
        >>>
        >>> # 동기화 성공 보고
        >>> manager.report_sync_success("payment", "push", value=5)
        >>>
        >>> # 신뢰도 확인
        >>> state = manager.get_reliability_state("payment")
        >>> if state.is_data_fresh:
        ...     use_value(state.current_value)
        >>> else:
        ...     use_safe_default()
    """

    def __init__(
        self,
        thresholds: ReliabilityThresholds | None = None,
        safe_defaults_provider: Callable[[str], Any] | None = None,
    ):
        """
        Initialize MetricReliabilityManager.

        Args:
            thresholds: 신뢰도 판단 임계값
            safe_defaults_provider: 안전한 기본값 제공 함수 (domain -> value)
        """
        self._thresholds = thresholds or ReliabilityThresholds()
        self._safe_defaults_provider = safe_defaults_provider
        self._states: dict[str, MetricReliabilityState] = {}
        self._lock = threading.Lock()
        self._global_mode = OperatingMode.NORMAL
        self._mode_listeners: list[Callable[[str, OperatingMode], None]] = []

    def get_reliability_state(self, domain: str) -> MetricReliabilityState:
        """
        도메인의 신뢰도 상태 조회.

        Args:
            domain: 도메인 이름

        Returns:
            MetricReliabilityState
        """
        with self._lock:
            if domain not in self._states:
                self._states[domain] = MetricReliabilityState(domain=domain)

            state = self._states[domain]
            self._update_reliability_level(state)
            return state

    def report_sync_success(
        self,
        domain: str,
        source: str,
        value: Any,
    ) -> MetricReliabilityState:
        """
        동기화 성공 보고.

        Args:
            domain: 도메인 이름
            source: 동기화 소스 ("push", "db", "airgap", "snapshot")
            value: 동기화된 값

        Returns:
            업데이트된 상태
        """
        with self._lock:
            if domain not in self._states:
                self._states[domain] = MetricReliabilityState(domain=domain)

            state = self._states[domain]
            state.last_sync_time = time.time()
            state.last_sync_source = source
            state.current_value = value
            state.value_source = source
            state.consecutive_successful_syncs += 1

            self._update_reliability_level(state)
            self._update_operating_mode(state)

            return state

    def report_sync_failure(
        self,
        domain: str,
        source: str,
        reason: str = "unknown",
    ) -> MetricReliabilityState:
        """
        동기화 실패 보고.

        Args:
            domain: 도메인 이름
            source: 실패한 소스
            reason: 실패 이유

        Returns:
            업데이트된 상태
        """
        with self._lock:
            if domain not in self._states:
                self._states[domain] = MetricReliabilityState(domain=domain)

            state = self._states[domain]
            state.consecutive_successful_syncs = 0

            logger.warning(
                "reliability.sync_failed",
                domain=domain,
                source=source,
                reason=reason,
            )

            self._update_reliability_level(state)
            self._update_operating_mode(state)

            return state

    def _update_reliability_level(self, state: MetricReliabilityState) -> None:
        """신뢰도 레벨 업데이트."""
        age = state.age_seconds

        if age is None:
            state.reliability_level = ReliabilityLevel.UNKNOWN
        elif age <= self._thresholds.high_max_age:
            state.reliability_level = ReliabilityLevel.HIGH
        elif age <= self._thresholds.medium_max_age:
            state.reliability_level = ReliabilityLevel.MEDIUM
        elif age <= self._thresholds.low_max_age:
            state.reliability_level = ReliabilityLevel.LOW
        else:
            state.reliability_level = ReliabilityLevel.UNKNOWN

    def _update_operating_mode(self, state: MetricReliabilityState) -> None:
        """운영 모드 업데이트 (점진적 완화 포함)."""
        old_mode = state.operating_mode

        if state.reliability_level == ReliabilityLevel.UNKNOWN:
            # 데이터 없음 → 엄격 모드
            state.operating_mode = OperatingMode.STRICT
            state.stabilization_start = None

        elif state.reliability_level == ReliabilityLevel.LOW:
            # 오래된 데이터 → 주의 모드
            if old_mode == OperatingMode.STRICT:
                # STRICT → CAUTIOUS: 안정화 시작
                state.operating_mode = OperatingMode.CAUTIOUS
                state.stabilization_start = time.time()
            elif old_mode == OperatingMode.CAUTIOUS:
                # 안정화 진행 중
                pass
            else:
                state.operating_mode = OperatingMode.CAUTIOUS
                state.stabilization_start = time.time()

        elif state.reliability_level in (
            ReliabilityLevel.HIGH,
            ReliabilityLevel.MEDIUM,
        ):
            # 신선한 데이터
            if old_mode in (OperatingMode.STRICT, OperatingMode.EMERGENCY):
                # 엄격 모드에서 복구 → 점진적 완화
                state.operating_mode = OperatingMode.CAUTIOUS
                state.stabilization_start = time.time()
                logger.info(
                    f"[Reliability] {state.domain}: Starting stabilization " f"({self._thresholds.stabilization_duration}s)"
                )
            elif old_mode == OperatingMode.CAUTIOUS:
                # 안정화 기간 확인
                if (
                    state.stabilization_start is not None
                    and time.time() - state.stabilization_start >= self._thresholds.stabilization_duration
                    and state.consecutive_successful_syncs >= self._thresholds.consecutive_syncs_for_normal
                ):
                    # 안정화 완료 → 정상 모드
                    state.operating_mode = OperatingMode.NORMAL
                    state.stabilization_start = None
                    logger.info(
                        "reliability.stabilization_complete_entering_normal",
                        state=state.domain,
                    )
            # NORMAL 유지

        # 모드 변경 알림
        if old_mode != state.operating_mode:
            state.mode_transition_time = time.time()
            self._notify_mode_change(state.domain, state.operating_mode)

    def _notify_mode_change(self, domain: str, new_mode: OperatingMode) -> None:
        """모드 변경 리스너 알림."""
        for listener in self._mode_listeners:
            try:
                listener(domain, new_mode)
            except Exception as e:
                logger.warning(
                    "reliability.mode_listener_error",
                    error=e,
                )

    def register_mode_listener(
        self,
        listener: Callable[[str, OperatingMode], None],
    ) -> None:
        """
        모드 변경 리스너 등록.

        Args:
            listener: (domain, new_mode) 콜백
        """
        self._mode_listeners.append(listener)

    def get_effective_value(
        self,
        domain: str,
        category: str = "default",
    ) -> tuple[Any, str, ReliabilityLevel]:
        """
        효과적인 값 가져오기 (fallback 적용).

        Fallback 순서:
        1. 현재 동기화된 값 (HIGH/MEDIUM)
        2. L1 스냅샷
        3. Safe Defaults

        Args:
            domain: 도메인 이름
            category: 값 카테고리

        Returns:
            (value, source, reliability_level)
        """
        state = self.get_reliability_state(domain)

        # 신선한 데이터가 있으면 사용
        if state.is_data_fresh and state.current_value is not None:
            return (state.current_value, state.value_source, state.reliability_level)

        # L1 스냅샷 시도
        try:
            from selfhealing.metrics.snapshot_storage import get_snapshot_storage

            storage = get_snapshot_storage()
            snapshot_value = storage.load_value(category, domain)

            if snapshot_value is not None:
                snapshot_age = storage.get_snapshot_age() or float("inf")

                if snapshot_age <= self._thresholds.low_max_age:
                    return (snapshot_value, "snapshot", ReliabilityLevel.LOW)
        except Exception as e:
            logger.debug(
                "reliability.snapshot_fallback_failed",
                error=e,
            )

        # Safe Defaults 사용
        if self._safe_defaults_provider:
            try:
                default_value = self._safe_defaults_provider(domain)
                return (default_value, "default", ReliabilityLevel.UNKNOWN)
            except Exception as e:
                logger.warning(
                    "reliability.safe_defaults_provider_error",
                    error=e,
                )

        return (None, "none", ReliabilityLevel.UNKNOWN)

    def get_all_states(self) -> dict[str, MetricReliabilityState]:
        """모든 도메인 상태 조회."""
        with self._lock:
            # 신뢰도 레벨 업데이트
            for state in self._states.values():
                self._update_reliability_level(state)
            return dict(self._states)

    def get_global_health(self) -> dict[str, Any]:
        """
        전체 시스템 건강 상태.

        Returns:
            건강 상태 요약
        """
        states = self.get_all_states()

        if not states:
            return {
                "status": "unknown",
                "domains": 0,
                "healthy": 0,
                "degraded": 0,
                "unhealthy": 0,
            }

        healthy = sum(1 for s in states.values() if s.reliability_level in (ReliabilityLevel.HIGH, ReliabilityLevel.MEDIUM))
        degraded = sum(1 for s in states.values() if s.reliability_level == ReliabilityLevel.LOW)
        unhealthy = sum(1 for s in states.values() if s.reliability_level == ReliabilityLevel.UNKNOWN)

        if unhealthy > 0:
            status = "unhealthy"
        elif degraded > 0:
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "domains": len(states),
            "healthy": healthy,
            "degraded": degraded,
            "unhealthy": unhealthy,
            "global_mode": self._global_mode.value,
        }

    def force_strict_mode(self, domain: str, reason: str = "manual") -> None:
        """
        강제 엄격 모드 전환.

        Args:
            domain: 도메인 이름
            reason: 전환 이유
        """
        with self._lock:
            if domain not in self._states:
                self._states[domain] = MetricReliabilityState(domain=domain)

            state = self._states[domain]
            old_mode = state.operating_mode
            state.operating_mode = OperatingMode.STRICT
            state.stabilization_start = None
            state.consecutive_successful_syncs = 0

            logger.warning(
                "reliability.forced_strict_mode",
                domain=domain,
                reason=reason,
            )

            if old_mode != OperatingMode.STRICT:
                self._notify_mode_change(domain, OperatingMode.STRICT)

    def get_global_mode(self) -> OperatingMode:
        """
        전역 운영 모드 조회.

        Returns:
            현재 전역 모드
        """
        return self._global_mode

    def force_global_mode(
        self,
        mode: OperatingMode,
        reason: str = "manual",
    ) -> None:
        """
        전역 운영 모드 강제 전환.

        모든 도메인에 동일한 모드를 적용합니다.

        Args:
            mode: 목표 운영 모드
            reason: 전환 이유
        """
        with self._lock:
            old_mode = self._global_mode
            self._global_mode = mode

            logger.warning(
                "reliability.global_mode_changed",
                old_mode=old_mode.value,
                mode=mode.value,
                reason=reason,
            )

            # 모든 도메인에 동일 모드 적용
            for domain, state in self._states.items():
                if state.operating_mode != mode:
                    state.operating_mode = mode
                    state.stabilization_start = None if mode == OperatingMode.STRICT else time.time()
                    self._notify_mode_change(domain, mode)

    def reset(self) -> None:
        """모든 상태 리셋."""
        with self._lock:
            self._states.clear()


# =============================================================================
# Singleton Instance
# =============================================================================

_reliability_manager: MetricReliabilityManager | None = None
_manager_lock = threading.Lock()


def get_reliability_manager() -> MetricReliabilityManager:
    """싱글톤 신뢰도 관리자 반환."""
    global _reliability_manager

    if _reliability_manager is not None:
        return _reliability_manager

    with _manager_lock:
        if _reliability_manager is None:
            _reliability_manager = MetricReliabilityManager()

    return _reliability_manager


def reset_reliability_manager() -> None:
    """신뢰도 관리자 리셋 (테스트용)."""
    global _reliability_manager
    with _manager_lock:
        _reliability_manager = None


__all__ = [
    "ReliabilityLevel",
    "OperatingMode",
    "ReliabilityThresholds",
    "MetricReliabilityState",
    "MetricReliabilityManager",
    "get_reliability_manager",
    "reset_reliability_manager",
]
