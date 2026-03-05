"""
Event Journal Repository Interface.

append-only 저장소 인터페이스로, Self-Healing 결정 이벤트를
시퀀스 보장 형태로 기록하고 조회한다.

Config Shadow Evaluator(299)의 시뮬레이션 데이터 소스로 사용된다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class JournalEntry:
    """이벤트 저널 엔트리. 불변(frozen) 데이터."""

    sequence: int
    event_type: str
    source: str
    timestamp: datetime
    service_name: str
    context: dict[str, Any] = field(default_factory=dict)

    region: str = ""
    tier_id: str = ""


@dataclass
class JournalQueryFilter:
    """저널 조회 필터."""

    event_types: list[str] | None = None
    service_name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    region: str | None = None
    limit: int = 1000
    context_filters: dict[str, str] | None = None


@dataclass(frozen=True)
class JournalQueryResult:
    """저널 조회 결과. 절삭(truncation) 여부를 포함한다."""

    entries: list[JournalEntry]
    truncated: bool
    total_count: int | None = None


# =============================================================================
# Repository Interface
# =============================================================================


class EventJournalRepository(ABC):
    """
    Self-Healing 이벤트 저널 저장소 인터페이스.

    append-only 저장소. 기록된 엔트리는 수정/삭제 불가.
    시퀀스 번호는 단조 증가하여 순서를 보장한다.
    Gap이 존재할 수 있으며(예: 1, 2, 4, 5), 소비자는 연속성을 가정하지 않는다.

    Implementations:
    - InMemoryEventJournalRepository: 테스트 및 단일 프로세스
    - RedisEventJournalRepository: 멀티 워커 환경
    """

    @abstractmethod
    def append(self, entry: JournalEntry) -> int:
        """
        이벤트를 저널에 추가한다.

        Args:
            entry: 저널 엔트리 (sequence 필드는 구현체가 할당)

        Returns:
            할당된 시퀀스 번호
        """
        ...

    @abstractmethod
    def query(self, query_filter: JournalQueryFilter) -> JournalQueryResult:
        """
        필터 조건에 맞는 엔트리를 시퀀스 순서(오름차순)로 반환한다.

        Args:
            query_filter: 조회 조건

        Returns:
            JournalQueryResult — entries(시퀀스 오름차순), truncated 여부, total_count
        """
        ...

    @abstractmethod
    def get_sequence_range(
        self,
        start_sequence: int,
        end_sequence: int,
    ) -> list[JournalEntry]:
        """
        시퀀스 범위로 엔트리를 조회한다.

        시뮬레이션 시 정확한 범위 재생에 사용.

        Args:
            start_sequence: 시작 시퀀스 (inclusive)
            end_sequence: 끝 시퀀스 (exclusive)

        Returns:
            시퀀스 오름차순 정렬된 엔트리 리스트
        """
        ...

    @abstractmethod
    def get_latest_sequence(self) -> int:
        """현재 최신 시퀀스 번호를 반환한다. 비어있으면 0."""
        ...

    @abstractmethod
    def count(self, query_filter: JournalQueryFilter) -> int:
        """필터 조건에 맞는 엔트리 수를 반환한다."""
        ...


# =============================================================================
# Lifecycle Interface (MVP 미구현)
# =============================================================================


class EventJournalLifecycle(ABC):
    """
    저널 데이터 수명주기 관리. 운영용 별도 인터페이스.

    append-only 원칙의 EventJournalRepository와 분리하여,
    아카이브/퍼지 책임을 독립적으로 관리한다.

    MVP에서는 구현하지 않으며, Tiered Storage 도입 시 활성화한다.
    """

    @abstractmethod
    def archive_older_than(self, cutoff: datetime) -> int:
        """cutoff 이전 엔트리를 Cold Storage로 이동. 이동된 건수 반환."""
        ...

    @abstractmethod
    def purge_archived(self, before: datetime) -> int:
        """아카이브 완료된 엔트리 중 before 이전 데이터를 삭제. 삭제된 건수 반환."""
        ...
