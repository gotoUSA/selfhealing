"""
Redis-based Event Journal Repository.

Redis Sorted Set 기반 구현. 멀티 워커 환경용.
월별 파티셔닝 키 구조를 사용하여 시간 범위 쿼리 성능을 최적화한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from selfhealing.interfaces.event_journal import (
    EventJournalRepository,
    JournalEntry,
    JournalQueryFilter,
    JournalQueryResult,
)

logger = structlog.get_logger()


class RedisEventJournalRepository(EventJournalRepository):
    """
    Redis Sorted Set 기반 구현. 멀티 워커 환경용.

    Redis Key Structure:
    - selfhealing:journal:YYYY-MM → Sorted Set (score=sequence, member=JSON)
    - selfhealing:journal:sequence → 원자적 시퀀스 카운터

    월별 파티셔닝으로 시간 범위 쿼리 시 관련 월의 키만 조회한다.
    """

    KEY_PREFIX = "selfhealing:journal"
    SEQUENCE_KEY = "selfhealing:journal:sequence"

    def __init__(self, redis_client: Any, ttl_seconds: int = 2592000):
        """
        Args:
            redis_client: Redis 클라이언트 인스턴스
            ttl_seconds: 파티션 키 TTL (기본 30일 = 2592000초)
        """
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    def append(self, entry: JournalEntry) -> int:
        seq = self._redis.incr(self.SEQUENCE_KEY)

        key = self._get_key(entry.timestamp)
        data = self._serialize(entry, seq)
        try:
            self._redis.zadd(key, {data: seq})
        except Exception as e:
            logger.warning("journal_zadd_failed", sequence=seq, error=str(e))
            raise

        if self._redis.ttl(key) < 0:
            self._redis.expire(key, self._ttl_seconds)

        return seq

    def query(self, filter: JournalQueryFilter) -> JournalQueryResult:
        all_entries: list[JournalEntry] = []

        if filter.start_time and filter.end_time:
            keys = self._resolve_partition_keys(filter.start_time, filter.end_time)
        else:
            keys = self._get_all_active_keys()

        for key in keys:
            raw_members = self._redis.zrangebyscore(
                key,
                "-inf",
                "+inf",
                withscores=False,
            )
            for raw in raw_members:
                entry = self._deserialize(raw)
                if entry and self._matches_filter(entry, filter):
                    all_entries.append(entry)

        all_entries.sort(key=lambda e: e.sequence)

        total_count = len(all_entries)
        truncated = total_count > filter.limit
        entries = all_entries[: filter.limit]

        return JournalQueryResult(
            entries=entries,
            truncated=truncated,
            total_count=total_count,
        )

    def get_sequence_range(
        self,
        start_sequence: int,
        end_sequence: int,
    ) -> list[JournalEntry]:
        keys = self._get_all_active_keys()
        results: list[JournalEntry] = []

        for key in keys:
            raw_members = self._redis.zrangebyscore(
                key,
                start_sequence,
                end_sequence - 1,
                withscores=False,
            )
            for raw in raw_members:
                entry = self._deserialize(raw)
                if entry:
                    results.append(entry)

        results.sort(key=lambda e: e.sequence)
        return results

    def get_latest_sequence(self) -> int:
        val = self._redis.get(self.SEQUENCE_KEY)
        if val is None:
            return 0
        return int(val)

    def count(self, filter: JournalQueryFilter) -> int:
        if filter.start_time and filter.end_time:
            keys = self._resolve_partition_keys(filter.start_time, filter.end_time)
        else:
            keys = self._get_all_active_keys()

        total = 0
        for key in keys:
            raw_members = self._redis.zrangebyscore(
                key,
                "-inf",
                "+inf",
                withscores=False,
            )
            for raw in raw_members:
                entry = self._deserialize(raw)
                if entry and self._matches_filter(entry, filter):
                    total += 1

        return total

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _get_key(self, timestamp: datetime) -> str:
        """타임스탬프 기반 월별 파티션 키를 반환한다."""
        return f"{self.KEY_PREFIX}:{timestamp.strftime('%Y-%m')}"

    def _resolve_partition_keys(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[str]:
        """시간 범위에 걸치는 모든 월별 파티션 키를 반환한다."""
        keys = []
        current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while current < end_time:
            keys.append(f"{self.KEY_PREFIX}:{current.strftime('%Y-%m')}")
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        return keys

    def _get_all_active_keys(self) -> list[str]:
        """현재 존재하는 모든 저널 파티션 키를 반환한다."""
        pattern = f"{self.KEY_PREFIX}:????-??"
        keys = self._redis.keys(pattern)
        return [k.decode() if isinstance(k, bytes) else k for k in keys]

    def _serialize(self, entry: JournalEntry, seq: int) -> str:
        """JournalEntry를 JSON 문자열로 직렬화한다."""
        data = {
            "sequence": seq,
            "event_type": entry.event_type,
            "source": entry.source,
            "timestamp": entry.timestamp.isoformat(),
            "service_name": entry.service_name,
            "context": entry.context,
            "region": entry.region,
            "tier_id": entry.tier_id,
        }
        return json.dumps(data, separators=(",", ":"))

    def _deserialize(self, raw: Any) -> JournalEntry | None:
        """JSON 문자열을 JournalEntry로 역직렬화한다."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            data = json.loads(raw)
            return JournalEntry(
                sequence=data["sequence"],
                event_type=data["event_type"],
                source=data["source"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                service_name=data["service_name"],
                context=data.get("context", {}),
                region=data.get("region", ""),
                tier_id=data.get("tier_id", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("journal_deserialize_failed", error=str(e))
            return None

    def _matches_filter(self, entry: JournalEntry, filter: JournalQueryFilter) -> bool:
        """엔트리가 필터 조건에 맞는지 확인한다."""
        if (
            filter.event_types is not None
            and entry.event_type not in filter.event_types
        ):
            return False
        if (
            filter.service_name is not None
            and entry.service_name != filter.service_name
        ):
            return False
        if filter.start_time is not None and entry.timestamp < filter.start_time:
            return False
        if filter.end_time is not None and entry.timestamp >= filter.end_time:
            return False
        if filter.region is not None and entry.region != filter.region:
            return False
        return True
