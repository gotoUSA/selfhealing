"""
Co-occurrence Tracker — 이벤트 동시 발생 패턴 추적기.

과거 이벤트 쌍의 동시 발생 빈도를 추적하여
인과관계 추론에 활용한다.

SystemMetricsCache의 Copy-on-Write 패턴을 동일하게 적용한다:
- 읽기: 인메모리 O(1) dict lookup, Lock 불필요
- 쓰기: 백그라운드 동기화로 immutable snapshot 교체 (GIL atomic swap)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoOccurrenceSnapshot:
    """인메모리 Co-occurrence 점수 스냅샷 (Immutable).

    frozen=True로 설정하여 읽기 시 동시성 문제를 원천 방지한다.
    백그라운드 스레드는 새 인스턴스를 생성하여 참조를 교체한다 (Copy-on-Write).
    Python GIL 하에서 참조 교체는 atomic이므로 Lock 불필요.
    """

    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    """(event_type_a, event_type_b) → 동시 발생 점수"""


class CoOccurrenceTracker:
    """이벤트 동시 발생 패턴 추적기.

    Co-occurrence 점수를 인메모리 O(1) dict lookup으로 조회한다.
    N²(최대 ~40,000회) 반복 내에서 호출되므로 외부 I/O를 배제하고
    순수 dict lookup만 수행한다.
    """

    def __init__(self) -> None:
        self._snapshot = CoOccurrenceSnapshot(scores={})

    def get_pair_score(self, event_type_a: str, event_type_b: str) -> float | None:
        """두 이벤트 타입의 동시 발생 점수를 반환한다.

        O(1) dict lookup — Lock 불필요.

        Args:
            event_type_a: 원인 이벤트 타입
            event_type_b: 결과 이벤트 타입

        Returns:
            동시 발생 점수 (0.0~1.0) 또는 기록 없음 시 None
        """
        return self._snapshot.scores.get((event_type_a, event_type_b))

    def update_snapshot(self, scores: dict[tuple[str, str], float]) -> None:
        """Co-occurrence 점수 스냅샷을 교체한다.

        백그라운드 동기화에서 호출되며, 새 immutable 스냅샷을 생성하여
        참조를 atomic으로 교체한다 (Copy-on-Write).

        Args:
            scores: 새 co-occurrence 점수 딕셔너리
        """
        self._snapshot = CoOccurrenceSnapshot(scores=dict(scores))
        logger.debug(
            "[CoOccurrenceTracker] Snapshot updated: %d pairs",
            len(scores),
        )
