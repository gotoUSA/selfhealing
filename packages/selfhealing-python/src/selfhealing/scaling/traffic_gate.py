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


# 전역 인스턴스 (편의 접근용)
traffic_gate = get_traffic_gate()
