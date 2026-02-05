"""
Traffic Gate - RateController + CascadeLoadShedding 통합.

RateController와 CascadeLoadShedding을 파이프라인 형태로 통합하여
트래픽을 제어합니다.
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


class TrafficGate:
    """
    Traffic Gate - 통합 트래픽 제어.

    처리 순서:
    1. CascadeLoadShedding.should_accept() - 우선순위 기반 필터링
    2. RateController.should_process() - Rate Limit 기반 스로틀링

    Usage:
        gate = TrafficGate()

        decision = gate.should_allow(priority=5)
        if decision.allowed:
            process_item()
        else:
            logger.warning(f"Rejected: {decision.reason} by {decision.gate}")
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

    def should_allow(
        self,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> TrafficDecision:
        """
        트래픽 허용 여부 결정.

        Args:
            priority: 요청 우선순위 (낮을수록 높은 우선순위)
            metadata: 결정에 사용할 추가 메타데이터

        Returns:
            TrafficDecision 결과
        """
        current_level = self._rate_controller.get_state().level

        # 1단계: CascadeLoadShedding 확인 (설정된 경우)
        if self._load_shedding is not None:
            try:
                if hasattr(self._load_shedding, "should_accept"):
                    result = self._load_shedding.should_accept(priority=priority)
                    # CascadeLoadShedding.should_accept()는 dict 반환
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

        # 2단계: RateController 확인
        if not self._rate_controller.should_process():
            return TrafficDecision(
                allowed=False,
                reason=f"Rate limit exceeded at level={current_level.value}",
                level=current_level,
                gate="RateController",
                metadata=metadata,
            )

        return TrafficDecision(
            allowed=True,
            reason="Allowed",
            level=current_level,
            gate="TrafficGate",
            metadata=metadata,
        )

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
