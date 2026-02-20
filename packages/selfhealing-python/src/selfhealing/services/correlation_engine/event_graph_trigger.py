"""
Event Graph Trigger — DAG 빌드 트리거 with Debounce.

Critical 이벤트 발생 시점을 앵커(anchor)로
앞뒤 버퍼를 캡처하여 EventGraphBuilder에 전달한다.

Debounce 메커니즘으로 Event Storm 시 DAG 중복 생성을 방지한다.
RateLimitCoordinator._should_emit_event() 패턴을 차용한다.
"""

from __future__ import annotations

import logging
import threading
import time

from selfhealing.services.event_bus.bus import EventType

logger = logging.getLogger(__name__)


# Critical 이벤트 → DAG 빌드를 트리거하는 이벤트 목록
TRIGGER_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.EMERGENCY_LEVEL_CHANGED.value,
        EventType.EMERGENCY_ACTIVATED.value,
        EventType.CIRCUIT_BREAKER_OPENED.value,
        EventType.SECURITY_VIOLATION_CRITICAL.value,
        EventType.KILL_SWITCH_ACTIVATED.value,
        EventType.LOAD_SHEDDING_LEVEL_CHANGED.value,
    }
)

# Load Shedding은 level >= 2일 때만 트리거
LOAD_SHEDDING_MIN_TRIGGER_LEVEL = 2

# 기본 Debounce Cooldown (초)
DEFAULT_COOLDOWN_SECONDS = 60.0


class EventGraphTrigger:
    """DAG 빌드 트리거 — Debounce 내장.

    동일 namespace에서 cooldown 이내 재트리거를 방지한다.
    RateLimitCoordinator._should_emit_event() 패턴과 동일하게
    threading.Lock으로 보호되는 dict 기반 타임스탬프 추적을 사용한다.
    """

    def __init__(self, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._last_build_times: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_build(self, namespace: str) -> bool:
        """동일 namespace에서 cooldown 이내 재트리거를 방지한다.

        Args:
            namespace: Debounce 기준 네임스페이스

        Returns:
            True이면 DAG 빌드를 진행, False이면 무시
        """
        now = time.time()
        with self._lock:
            last = self._last_build_times.get(namespace, 0.0)
            if now - last < self._cooldown_seconds:
                logger.debug(
                    "[EventGraphTrigger] Debounced: namespace=%s, " "elapsed=%.1fs < cooldown=%.1fs",
                    namespace,
                    now - last,
                    self._cooldown_seconds,
                )
                return False
            self._last_build_times[namespace] = now
            return True

    def is_trigger_event(self, event_type: str, event_data: dict) -> bool:
        """이벤트가 DAG 빌드를 트리거하는 Critical 이벤트인지 판단한다.

        Args:
            event_type: EventType.value 문자열
            event_data: 이벤트 데이터 딕셔너리

        Returns:
            True이면 트리거 이벤트
        """
        if event_type not in TRIGGER_EVENT_TYPES:
            return False

        # Load Shedding은 level >= 2일 때만 트리거
        if event_type == EventType.LOAD_SHEDDING_LEVEL_CHANGED.value:
            new_level = event_data.get("new_level", 0)
            if new_level < LOAD_SHEDDING_MIN_TRIGGER_LEVEL:
                return False

        return True

    def reset(self) -> None:
        """테스트용: 내부 상태를 초기화한다."""
        with self._lock:
            self._last_build_times.clear()
