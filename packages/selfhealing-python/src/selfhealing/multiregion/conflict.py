"""
Conflict Resolver - 리전 간 충돌 해결.

Active-Active 아키텍처에서 동일한 데이터가 여러 리전에서 동시에 변경될 때
충돌을 해결하는 전략을 제공합니다.

충돌 해결 전략:
- LWW (Last-Write-Wins): 타임스탬프 기반, 결정적 Tie-breaking 포함
- CRDT: Conflict-free Replicated Data Types (G-Counter, LWW-Register)

Tie-breaking 순서 (동일 타임스탬프 시):
1. timestamp (최신 우선)
2. region_priority (낮을수록 높은 우선순위)
3. cluster_id (문자열 비교)
"""

from __future__ import annotations

import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from selfhealing.multiregion.config import get_multiregion_settings

logger = structlog.get_logger()


# =============================================================================
# Conflict Key (결정적 충돌 해결용)
# =============================================================================


@dataclass
class ConflictKey:
    """
    충돌 해결용 복합 키.

    동일한 타임스탬프를 가진 이벤트 간에도 결정적으로 승자를 정합니다.

    비교 우선순위:
    1. timestamp (최신 우선)
    2. region_priority (낮을수록 높은 우선순위)
    3. cluster_id (문자열 비교)
    """

    timestamp: float
    """이벤트 타임스탬프 (Unix timestamp)."""

    region_priority: int
    """리전 우선순위 (1=최고, 100=기본). 낮을수록 높은 우선순위."""

    cluster_id: str
    """클러스터 ID (예: 'prod-kr-1'). 최종 tie-breaker."""

    def __gt__(self, other: ConflictKey) -> bool:
        """
        결정적 비교: timestamp → region_priority → cluster_id.

        Returns:
            True if self가 other보다 우선순위가 높음 (승자)
        """
        if self.timestamp != other.timestamp:
            return self.timestamp > other.timestamp
        # 낮은 priority가 더 높은 우선순위
        if self.region_priority != other.region_priority:
            return self.region_priority < other.region_priority
        # 최종: 문자열 비교 (알파벳 순)
        return self.cluster_id > other.cluster_id

    def __ge__(self, other: ConflictKey) -> bool:
        """self >= other."""
        return self == other or self > other

    def __lt__(self, other: ConflictKey) -> bool:
        """self < other."""
        return not (self >= other)

    def __le__(self, other: ConflictKey) -> bool:
        """self <= other."""
        return not (self > other)

    def __eq__(self, other: object) -> bool:
        """동등 비교."""
        if not isinstance(other, ConflictKey):
            return NotImplemented
        return (
            self.timestamp == other.timestamp
            and self.region_priority == other.region_priority
            and self.cluster_id == other.cluster_id
        )


# =============================================================================
# Conflict Metrics (충돌 통계)
# =============================================================================


class ConflictMetrics:
    """
    충돌 메트릭 수집.

    충돌 발생 빈도와 해결 방법을 추적합니다.

    사용 예:
        metrics = ConflictMetrics()

        # 충돌 이벤트 기록
        metrics.record_event(is_conflict=True, resolution_method="timestamp")

        # 통계 조회
        stats = metrics.get_stats()
        print(f"충돌률: {stats['conflict_ratio']:.4%}")
    """

    def __init__(self):
        """초기화."""
        self._total_events = 0
        self._conflicts_detected = 0
        self._conflicts_resolved_by_timestamp = 0
        self._conflicts_resolved_by_priority = 0
        self._conflicts_resolved_by_cluster_id = 0
        self._conflicts_dropped = 0

    def record_event(
        self,
        is_conflict: bool,
        resolution_method: str | None = None,
    ) -> None:
        """
        이벤트 기록.

        Args:
            is_conflict: 충돌 여부
            resolution_method: 해결 방법 ('timestamp', 'priority', 'cluster_id', 'dropped')
        """
        self._total_events += 1
        if is_conflict:
            self._conflicts_detected += 1
            if resolution_method == "timestamp":
                self._conflicts_resolved_by_timestamp += 1
            elif resolution_method == "priority":
                self._conflicts_resolved_by_priority += 1
            elif resolution_method == "cluster_id":
                self._conflicts_resolved_by_cluster_id += 1
            elif resolution_method == "dropped":
                self._conflicts_dropped += 1

    def get_conflict_ratio(self) -> float:
        """
        충돌 비율 반환.

        Returns:
            0.0 ~ 1.0 사이의 충돌 비율
        """
        if self._total_events == 0:
            return 0.0
        return self._conflicts_detected / self._total_events

    def get_stats(self) -> dict[str, Any]:
        """
        통계 반환.

        Returns:
            통계 딕셔너리
        """
        return {
            "total_events": self._total_events,
            "conflicts_detected": self._conflicts_detected,
            "conflict_ratio": self.get_conflict_ratio(),
            "by_timestamp": self._conflicts_resolved_by_timestamp,
            "by_priority": self._conflicts_resolved_by_priority,
            "by_cluster_id": self._conflicts_resolved_by_cluster_id,
            "dropped": self._conflicts_dropped,
        }

    def reset(self) -> None:
        """통계 리셋."""
        self._total_events = 0
        self._conflicts_detected = 0
        self._conflicts_resolved_by_timestamp = 0
        self._conflicts_resolved_by_priority = 0
        self._conflicts_resolved_by_cluster_id = 0
        self._conflicts_dropped = 0


# =============================================================================
# Conflict Resolver (추상 인터페이스)
# =============================================================================


class ConflictResolver(ABC):
    """
    충돌 해결 인터페이스.

    다양한 충돌 해결 전략을 구현하기 위한 추상 클래스.
    """

    @abstractmethod
    def resolve(self, incoming: Any) -> Any | None:
        """
        충돌 해결.

        Args:
            incoming: 수신 이벤트

        Returns:
            적용할 이벤트 (None이면 무시)
        """
        pass

    @abstractmethod
    def get_metrics(self) -> ConflictMetrics:
        """메트릭 반환."""
        pass


# =============================================================================
# Last-Write-Wins Resolver
# =============================================================================


class LastWriteWinsResolver(ConflictResolver):
    """
    Last-Write-Wins + 결정적 Tie-breaking 충돌 해결.

    타임스탬프가 더 최신인 이벤트가 승리합니다.
    동일 타임스탬프 시 결정적으로 승자를 정합니다.

    Tie-breaking 순서:
    1. timestamp (최신 우선)
    2. region_priority (낮을수록 높은 우선순위)
    3. cluster_id (문자열 비교)

    사용 예:
        resolver = LastWriteWinsResolver()

        # 충돌 해결
        result = resolver.resolve(incoming_event)
        if result is not None:
            # 이벤트 적용
            apply_event(result)
    """

    def __init__(self):
        """초기화."""
        self._last_keys: dict[str, ConflictKey] = {}
        self._settings = get_multiregion_settings()
        self._metrics = ConflictMetrics()

    def resolve(self, incoming: Any) -> Any | None:
        """
        결정적 충돌 해결.

        Args:
            incoming: 수신 이벤트 (key, timestamp, region_priority, cluster_id 속성 필요)

        Returns:
            적용할 이벤트 (None이면 무시)
        """
        key = incoming.key

        incoming_conflict_key = ConflictKey(
            timestamp=incoming.timestamp,
            region_priority=getattr(incoming, "region_priority", 100),
            cluster_id=getattr(incoming, "cluster_id", self._settings.current_region),
        )

        last_conflict_key = self._last_keys.get(key)

        if last_conflict_key is None:
            # 첫 이벤트 (충돌 없음)
            self._last_keys[key] = incoming_conflict_key
            self._metrics.record_event(is_conflict=False)
            return incoming

        if incoming_conflict_key > last_conflict_key:
            # 새 이벤트가 승리
            resolution = self._determine_resolution_method(incoming_conflict_key, last_conflict_key)
            self._metrics.record_event(is_conflict=True, resolution_method=resolution)
            self._last_keys[key] = incoming_conflict_key
            logger.debug(
                f"[LWW] Accepted: {key} "
                f"(ts={incoming_conflict_key.timestamp:.6f}, "
                f"priority={incoming_conflict_key.region_priority}, "
                f"resolution={resolution})"
            )
            return incoming
        else:
            # 이전 값이 더 최신 (드랍)
            self._metrics.record_event(is_conflict=True, resolution_method="dropped")
            logger.debug(
                f"[LWW] Dropped: {key} "
                f"(incoming={incoming_conflict_key.timestamp:.6f} <= "
                f"last={last_conflict_key.timestamp:.6f})"
            )
            return None

    def _determine_resolution_method(
        self,
        winner: ConflictKey,
        loser: ConflictKey,
    ) -> str:
        """해결 방법 결정."""
        if winner.timestamp != loser.timestamp:
            return "timestamp"
        if winner.region_priority != loser.region_priority:
            return "priority"
        return "cluster_id"

    def get_metrics(self) -> ConflictMetrics:
        """메트릭 반환."""
        return self._metrics

    def clear_state(self) -> None:
        """상태 초기화 (테스트용)."""
        self._last_keys.clear()
        self._metrics.reset()


# =============================================================================
# CRDT G-Counter
# =============================================================================


class CRDTGCounter:
    """
    G-Counter CRDT (Grow-only Counter).

    각 노드별 카운터를 유지하여 충돌 없이 증가만 가능한 카운터.
    머지 시 각 노드별로 max 값을 취합니다.

    사용 예:
        counter = CRDTGCounter()

        # 노드에서 증가
        counter.increment("node-1", 5)
        counter.increment("node-2", 3)

        # 현재 값
        print(counter.value())  # 8

        # 다른 counter와 머지
        counter.merge(other_counter)
    """

    def __init__(self):
        """초기화."""
        self._counters: dict[str, int] = {}

    def increment(self, node: str, amount: int = 1) -> None:
        """
        증가.

        Args:
            node: 노드 ID
            amount: 증가량 (기본 1)
        """
        if amount < 0:
            raise ValueError("G-Counter can only increment (amount must be >= 0)")
        self._counters[node] = self._counters.get(node, 0) + amount

    def merge(self, other: CRDTGCounter) -> None:
        """
        머지.

        Args:
            other: 다른 G-Counter
        """
        for node, count in other._counters.items():
            self._counters[node] = max(self._counters.get(node, 0), count)

    def value(self) -> int:
        """현재 값 (모든 노드의 합계)."""
        return sum(self._counters.values())

    def to_dict(self) -> dict[str, int]:
        """직렬화."""
        return dict(self._counters)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> CRDTGCounter:
        """역직렬화."""
        counter = cls()
        counter._counters = dict(data)
        return counter

    def __repr__(self) -> str:
        """문자열 표현."""
        return f"CRDTGCounter(value={self.value()}, nodes={len(self._counters)})"


# =============================================================================
# CRDT LWW-Register
# =============================================================================


class CRDTLWWRegister:
    """
    LWW-Register CRDT (Last-Write-Wins Register).

    타임스탬프와 함께 값을 저장하여 충돌 시 최신 값이 승리.

    사용 예:
        register = CRDTLWWRegister()

        # 값 설정
        register.set("value1", timestamp=1000.0)
        register.set("value2", timestamp=2000.0)  # 더 최신

        # 현재 값
        print(register.value())  # "value2"

        # 다른 register와 머지
        register.merge(other_register)
    """

    def __init__(self, value: Any = None, timestamp: float = 0.0):
        """
        초기화.

        Args:
            value: 초기 값
            timestamp: 초기 타임스탬프
        """
        self._value = value
        self._timestamp = timestamp

    def set(self, value: Any, timestamp: float) -> bool:
        """
        값 설정.

        Args:
            value: 설정할 값
            timestamp: 타임스탬프

        Returns:
            True if 값이 업데이트됨
        """
        if timestamp > self._timestamp:
            self._value = value
            self._timestamp = timestamp
            return True
        return False

    def merge(self, other: CRDTLWWRegister) -> None:
        """
        머지.

        Args:
            other: 다른 LWW-Register
        """
        if other._timestamp > self._timestamp:
            self._value = other._value
            self._timestamp = other._timestamp

    def value(self) -> Any:
        """현재 값."""
        return self._value

    def timestamp(self) -> float:
        """현재 타임스탬프."""
        return self._timestamp

    def to_dict(self) -> dict[str, Any]:
        """직렬화."""
        return {"value": self._value, "timestamp": self._timestamp}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CRDTLWWRegister:
        """역직렬화."""
        return cls(value=data["value"], timestamp=data["timestamp"])

    def __repr__(self) -> str:
        """문자열 표현."""
        return f"CRDTLWWRegister(value={self._value!r}, timestamp={self._timestamp})"


# =============================================================================
# CRDT Resolver
# =============================================================================


class CRDTResolver(ConflictResolver):
    """
    CRDT 기반 충돌 해결.

    키 타입에 따라 적절한 CRDT를 적용합니다:
    - counter:* → G-Counter
    - register:* → LWW-Register
    - 기타 → LWW 폴백

    사용 예:
        resolver = CRDTResolver()

        # G-Counter 키
        event.key = "counter:page_views"
        event.value = {"node-1": 100, "node-2": 50}
        result = resolver.resolve(event)

        # LWW-Register 키
        event.key = "register:config"
        event.value = {"value": "...", "timestamp": 1234567890.0}
        result = resolver.resolve(event)
    """

    def __init__(self):
        """초기화."""
        self._gcounters: dict[str, CRDTGCounter] = {}
        self._lww_registers: dict[str, CRDTLWWRegister] = {}
        self._lww_fallback = LastWriteWinsResolver()
        self._metrics = ConflictMetrics()

    def resolve(self, incoming: Any) -> Any | None:
        """
        CRDT 기반 충돌 해결.

        Args:
            incoming: 수신 이벤트

        Returns:
            적용할 이벤트 (머지된 값으로 수정됨)
        """
        key = incoming.key

        if key.startswith("counter:"):
            return self._resolve_gcounter(incoming)
        elif key.startswith("register:"):
            return self._resolve_lww_register(incoming)
        else:
            # 기본: LWW
            return self._lww_fallback.resolve(incoming)

    def _resolve_gcounter(self, incoming: Any) -> Any:
        """G-Counter 해결."""
        key = incoming.key

        if key not in self._gcounters:
            self._gcounters[key] = CRDTGCounter()

        counter = self._gcounters[key]

        # 값이 dict면 CRDT 데이터
        if isinstance(incoming.value, dict):
            other = CRDTGCounter.from_dict(incoming.value)
            counter.merge(other)
            self._metrics.record_event(is_conflict=True, resolution_method="crdt_merge")
        else:
            # 단순 값이면 현재 노드에서 증가
            settings = get_multiregion_settings()
            if isinstance(incoming.value, int):
                counter.increment(settings.current_region, incoming.value)
            self._metrics.record_event(is_conflict=False)

        # 머지 후 새 값으로 이벤트 수정
        incoming.value = counter.value()
        return incoming

    def _resolve_lww_register(self, incoming: Any) -> Any:
        """LWW-Register 해결."""
        key = incoming.key

        if key not in self._lww_registers:
            self._lww_registers[key] = CRDTLWWRegister()

        register = self._lww_registers[key]

        if isinstance(incoming.value, dict) and "timestamp" in incoming.value:
            # CRDT 형식
            other = CRDTLWWRegister.from_dict(incoming.value)
            old_ts = register.timestamp()
            register.merge(other)
            if register.timestamp() > old_ts:
                self._metrics.record_event(is_conflict=True, resolution_method="crdt_merge")
            else:
                self._metrics.record_event(is_conflict=True, resolution_method="dropped")
        else:
            # 단순 값
            if register.set(incoming.value, incoming.timestamp):
                self._metrics.record_event(is_conflict=False)
            else:
                self._metrics.record_event(is_conflict=True, resolution_method="dropped")

        incoming.value = register.value()
        return incoming

    def get_metrics(self) -> ConflictMetrics:
        """메트릭 반환."""
        # LWW 폴백 메트릭과 병합
        combined = ConflictMetrics()
        combined._total_events = self._metrics._total_events + self._lww_fallback.get_metrics()._total_events
        combined._conflicts_detected = self._metrics._conflicts_detected + self._lww_fallback.get_metrics()._conflicts_detected
        return combined

    def clear_state(self) -> None:
        """상태 초기화 (테스트용)."""
        self._gcounters.clear()
        self._lww_registers.clear()
        self._lww_fallback.clear_state()
        self._metrics.reset()


# =============================================================================
# Factory
# =============================================================================

_resolver: ConflictResolver | None = None


def get_conflict_resolver() -> ConflictResolver:
    """
    ConflictResolver 싱글톤 반환.

    설정에 따라 적절한 리졸버를 반환합니다.

    Returns:
        ConflictResolver 인스턴스
    """
    global _resolver

    if _resolver is None:
        settings = get_multiregion_settings()

        if settings.conflict_resolution == "crdt":
            _resolver = CRDTResolver()
        else:
            _resolver = LastWriteWinsResolver()

    return _resolver


def reset_conflict_resolver() -> None:
    """리졸버 리셋 (테스트용)."""
    global _resolver
    _resolver = None
