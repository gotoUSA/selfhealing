"""
Traffic Gate - RateController + CascadeLoadShedding + Bulkhead 통합.

RateController, CascadeLoadShedding, Bulkhead를 파이프라인 형태로 통합하여
트래픽을 제어합니다.

처리 순서:
1. Bulkhead (도메인별 격리) - 특정 도메인 폭주 시 해당 도메인만 거부
2. CascadeLoadShedding (우선순위 필터링) - 낮은 우선순위 요청 거부
3. RateController (전역 Rate Limit) - 전체 처리량 제한
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from selfhealing.scaling.config import (
    BackpressureLevel,
    BackpressureSettings,
    get_backpressure_settings,
)
from selfhealing.scaling.rate_controller import (
    RateController,
    get_rate_controller,
)

logger = logging.getLogger(__name__)


# TrafficGate priority int를 RateController priority tier 문자열로 변환하기 위한 임계치.
# TrafficGate 규약: 낮을수록 높은 우선순위.
# AdmissionControlMiddleware에서 critical=0, standard=50, non_essential=100으로 전달된다.
_PRIORITY_TIER_THRESHOLDS: list[tuple[int, str]] = [
    (25, "critical"),  # priority <= 25 → critical
    (75, "standard"),  # priority <= 75 → standard
]
_PRIORITY_TIER_DEFAULT = "non_essential"  # priority > 75


def _map_priority_int_to_tier(priority: int) -> str:
    """priority int를 tier 문자열로 변환.

    Args:
        priority: 요청 우선순위 (낮을수록 높은 우선순위)

    Returns:
        "critical" | "standard" | "non_essential"
    """
    for threshold, tier in _PRIORITY_TIER_THRESHOLDS:
        if priority <= threshold:
            return tier
    return _PRIORITY_TIER_DEFAULT


@dataclass
class TrafficDecision:
    """Traffic Gate 결정 결과."""

    allowed: bool
    """처리 허용 여부."""

    reason: str
    """결정 이유."""

    level: BackpressureLevel
    """현재 Backpressure 레벨."""

    gate: str
    """결정한 게이트 이름."""

    metadata: dict[str, Any] | None = None
    """추가 메타데이터."""

    bulkhead_acquired: bool = False
    """Bulkhead 리소스 획득 여부 (True면 release 필요)."""

    bulkhead_name: str | None = None
    """획득한 Bulkhead 이름."""


class TrafficGate:
    """
    Traffic Gate - 통합 트래픽 제어.

    처리 순서:
    1. Bulkhead.try_acquire() - 도메인별 리소스 격리 (신규)
    2. CascadeLoadShedding.should_accept() - 우선순위 기반 필터링
    3. RateController.should_process() - Rate Limit 기반 스로틀링

    Bulkhead 통합 이점:
    - database 도메인 폭주 시 database 격벽만 거부, cache/external_api 정상
    - 전역 Rate Limit 소진 방지
    - 도메인별 병목 지점 명확히 파악 가능

    Usage:
        gate = TrafficGate()

        # 기본 사용 (Bulkhead 없이)
        decision = gate.should_allow(priority=5)

        # Bulkhead와 함께 사용
        decision = gate.should_allow(priority=5, bulkhead_name="database")
        if decision.allowed:
            try:
                process_item()
            finally:
                # 주의: Bulkhead 획득 성공 시 release 필요
                if decision.bulkhead_acquired:
                    gate.release_bulkhead("database")
    """

    def __init__(
        self,
        settings: BackpressureSettings | None = None,
        rate_controller: RateController | None = None,
        load_shedding: Any | None = None,
    ):
        """
        Args:
            settings: Backpressure 설정
            rate_controller: RateController 인스턴스
            load_shedding: CascadeLoadShedding 인스턴스 (선택)
        """
        self._settings = settings or get_backpressure_settings()
        self._rate_controller = rate_controller or get_rate_controller()
        self._load_shedding = load_shedding

    def _check_bulkhead(
        self,
        bulkhead_name: str,
        current_level: BackpressureLevel,
        metadata: dict[str, Any] | None,
        timeout: float | None = None,
    ) -> tuple[bool, TrafficDecision | None]:
        """Bulkhead 확인. 획득 여부와 거부 시 결정을 반환.

        Args:
            bulkhead_name: 격벽 이름
            current_level: 현재 Backpressure 레벨
            metadata: 추가 메타데이터
            timeout: Bulkhead 획득 대기 시간 (초). None이면 즉시 실패.
        """
        try:
            from selfhealing.resilience.bulkhead import get_bulkhead_registry

            registry = get_bulkhead_registry()
            bulkhead = registry.get(bulkhead_name)

            if not bulkhead.try_acquire(timeout=timeout):
                return False, TrafficDecision(
                    allowed=False,
                    reason=f"Bulkhead '{bulkhead_name}' is full",
                    level=current_level,
                    gate="Bulkhead",
                    metadata=metadata,
                    bulkhead_acquired=False,
                    bulkhead_name=bulkhead_name,
                )
            return True, None
        except KeyError:
            logger.debug(f"[TrafficGate] Bulkhead '{bulkhead_name}' not found, skipping")
            return False, None
        except Exception as e:
            logger.warning(f"[TrafficGate] Bulkhead error: {e}")
            return False, None

    def _check_load_shedding(
        self,
        priority: int,
        current_level: BackpressureLevel,
        metadata: dict[str, Any] | None,
    ) -> TrafficDecision | None:
        """LoadShedding 확인. 거부 시 결정을 반환, 허용 시 None."""
        if self._load_shedding is None:
            return None

        try:
            if hasattr(self._load_shedding, "should_accept"):
                result = self._load_shedding.should_accept(priority=priority)
                if isinstance(result, dict) and not result.get("accepted", True):
                    return TrafficDecision(
                        allowed=False,
                        reason=f"Load shedding rejected priority={priority}",
                        level=current_level,
                        gate="CascadeLoadShedding",
                        metadata=metadata,
                    )
        except Exception as e:
            logger.warning(f"[TrafficGate] LoadShedding error: {e}")
        return None

    def should_allow(
        self,
        priority: int = 0,
        bulkhead_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        bulkhead_timeout: float | None = None,
    ) -> TrafficDecision:
        """
        트래픽 허용 여부 결정.

        처리 순서:
        1. Bulkhead (도메인별 격리) - 신규
        2. CascadeLoadShedding (우선순위 필터링)
        3. RateController (전역 Rate Limit)

        Args:
            priority: 요청 우선순위 (낮을수록 높은 우선순위)
            bulkhead_name: 격벽 이름 (ConnectionType.value 또는 커스텀)
            metadata: 결정에 사용할 추가 메타데이터
            bulkhead_timeout: Bulkhead 획득 대기 시간 (초). None이면 즉시 실패.

        Returns:
            TrafficDecision 결과

        Note:
            bulkhead_name을 지정하고 allowed=True인 경우,
            bulkhead_acquired=True이면 작업 완료 후 release_bulkhead() 호출 필요.
        """
        current_level = self._rate_controller.get_state().level
        bulkhead_acquired = False

        # 0단계: Deadline 만료 확인
        try:
            from selfhealing.scaling.deadline_context import is_expired

            if is_expired():
                return TrafficDecision(
                    allowed=False,
                    reason="Deadline expired",
                    level=current_level,
                    gate="DeadlineContext",
                    metadata=metadata,
                )
        except ImportError:
            pass

        # 1단계: Bulkhead 확인 (도메인별 격리)
        if bulkhead_name is not None:
            acquired, decision = self._check_bulkhead(
                bulkhead_name,
                current_level,
                metadata,
                timeout=bulkhead_timeout,
            )
            if decision is not None:
                return decision
            bulkhead_acquired = acquired

        # 2단계: CascadeLoadShedding 확인
        load_shedding_decision = self._check_load_shedding(priority, current_level, metadata)
        if load_shedding_decision is not None:
            if bulkhead_acquired and bulkhead_name:
                self._release_bulkhead_internal(bulkhead_name)
            return load_shedding_decision

        # 3단계: RateController 확인 (priority 기반 watermark 적용)
        tier_str = _map_priority_int_to_tier(priority)
        if not self._rate_controller.should_process(priority=tier_str):
            if bulkhead_acquired and bulkhead_name:
                self._release_bulkhead_internal(bulkhead_name)
            return TrafficDecision(
                allowed=False,
                reason=(f"Rate limit exceeded: priority={tier_str}, " f"level={current_level.value}"),
                level=current_level,
                gate="RateController",
                metadata={**(metadata or {}), "priority": tier_str},
            )

        return TrafficDecision(
            allowed=True,
            reason="Allowed",
            level=current_level,
            gate="TrafficGate",
            metadata=metadata,
            bulkhead_acquired=bulkhead_acquired,
            bulkhead_name=bulkhead_name if bulkhead_acquired else None,
        )

    def _release_bulkhead_internal(self, bulkhead_name: str) -> None:
        """내부용 Bulkhead 릴리즈."""
        try:
            from selfhealing.resilience.bulkhead import get_bulkhead_registry

            registry = get_bulkhead_registry()
            bulkhead = registry.get(bulkhead_name)
            bulkhead.release()
        except Exception as e:
            logger.warning(f"[TrafficGate] Failed to release bulkhead: {e}")

    def release_bulkhead(self, bulkhead_name: str) -> None:
        """
        Bulkhead 리소스 반환.

        should_allow()에서 bulkhead_acquired=True인 경우,
        작업 완료 후 반드시 호출해야 합니다.

        Args:
            bulkhead_name: 반환할 격벽 이름
        """
        self._release_bulkhead_internal(bulkhead_name)

    def get_level(self) -> BackpressureLevel:
        """현재 Backpressure 레벨 반환."""
        return self._rate_controller.get_state().level


# =============================================================================
# Singleton
# =============================================================================

_traffic_gate: TrafficGate | None = None


def get_traffic_gate() -> TrafficGate:
    """TrafficGate 싱글톤 반환."""
    global _traffic_gate
    if _traffic_gate is None:
        _traffic_gate = TrafficGate()
    return _traffic_gate


def reset_traffic_gate() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _traffic_gate
    _traffic_gate = None


def create_traffic_gate_with_cascade_load_shedding(
    buffer_size_provider: Any = None,
    buffer_capacity: int = 10000,
) -> TrafficGate:
    """
    CascadeLoadShedding과 연동된 TrafficGate 생성.

    CascadeLoadShedding을 자동으로 설정하고 TrafficGate에 연결합니다.

    Args:
        buffer_size_provider: 버퍼 크기 제공 함수 또는 객체
            - Callable[[], int]: 버퍼 크기 반환 함수
            - RingBuffer: has __len__ method
            - None: 기본값 0 사용
        buffer_capacity: 버퍼 최대 용량

    Returns:
        CascadeLoadShedding이 연동된 TrafficGate

    Usage:
        from selfhealing.audit.ring_buffer import RingBuffer

        buffer = RingBuffer(capacity=10000)
        gate = create_traffic_gate_with_cascade_load_shedding(
            buffer_size_provider=buffer,
            buffer_capacity=10000,
        )

        decision = gate.should_allow(priority=5)
    """
    try:
        from selfhealing.audit.cascade_load_shedding import CascadeLoadShedding
    except ImportError:
        logger.warning("[TrafficGate] CascadeLoadShedding not available, creating gate without it")
        return TrafficGate()

    # 버퍼 크기 제공 함수 생성
    def get_buffer_size() -> int:
        if buffer_size_provider is None:
            return 0
        if callable(buffer_size_provider):
            return buffer_size_provider()
        if hasattr(buffer_size_provider, "__len__"):
            return len(buffer_size_provider)
        return 0

    # CascadeLoadShedding 래퍼 클래스
    class LoadSheddingAdapter:
        """CascadeLoadShedding 어댑터."""

        def __init__(self):
            self._shedding = CascadeLoadShedding()
            self._buffer_capacity = buffer_capacity

        def should_accept(self, priority: int = 0, **kwargs: Any) -> dict:
            """
            should_accept 래퍼.

            CascadeLoadShedding.should_accept()는 trigger_type, buffer_size,
            buffer_capacity를 필요로 합니다.
            """
            return self._shedding.should_accept(
                trigger_type="traffic_gate",
                buffer_size=get_buffer_size(),
                buffer_capacity=self._buffer_capacity,
                priority=None,  # priority는 trigger_type에서 추론
            )

    adapter = LoadSheddingAdapter()

    return TrafficGate(load_shedding=adapter)


# 전역 인스턴스 (편의 접근용)
traffic_gate = get_traffic_gate()


# 전역 인스턴스 (편의 접근용)
traffic_gate = get_traffic_gate()
