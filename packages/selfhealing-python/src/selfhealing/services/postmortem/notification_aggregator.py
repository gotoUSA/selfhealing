"""
NotificationAggregator - 알림 집계로 Alert Storm 방지.

IncidentGroup은 Postmortem 병합은 해결하지만, 알림 폭주(Alert Storm)는
별도 처리가 필요합니다. 이 모듈은 짧은 시간 내 발생하는 알림을 집계하여
단일 요약 알림으로 발송합니다.

Storage:
- Redis 정상: Redis ZSET 기반 대기 큐
- Redis 장애: In-Memory Fallback

Features:
- 집계 윈도우 기반 대기 (기본 60초)
- 최대 대기 시간 제한 (기본 300초)
- 통합 요약 알림 발송
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IncidentSummaryNotification:
    """
    집계된 인시던트 요약 알림 데이터.

    여러 개별 알림을 하나의 요약 알림으로 통합합니다.
    """

    total_incidents: int
    """집계된 인시던트 수."""

    affected_services: list[str]
    """영향받은 서비스 목록."""

    total_downtime_seconds: float
    """총 다운타임 (초)."""

    group_id: str | None = None
    """IncidentGroup ID (있는 경우)."""

    postmortem_links: list[str] = field(default_factory=list)
    """개별 Postmortem 링크."""

    primary_incident_id: str = ""
    """대표 인시던트 ID."""

    created_at: str = ""
    """집계 생성 시각."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "total_incidents": self.total_incidents,
            "affected_services": self.affected_services,
            "total_downtime_seconds": self.total_downtime_seconds,
            "group_id": self.group_id,
            "postmortem_links": self.postmortem_links,
            "primary_incident_id": self.primary_incident_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncidentSummaryNotification:
        """딕셔너리에서 생성."""
        return cls(
            total_incidents=data.get("total_incidents", 0),
            affected_services=data.get("affected_services", []),
            total_downtime_seconds=data.get("total_downtime_seconds", 0.0),
            group_id=data.get("group_id"),
            postmortem_links=data.get("postmortem_links", []),
            primary_incident_id=data.get("primary_incident_id", ""),
            created_at=data.get("created_at", ""),
        )


@dataclass
class PendingNotification:
    """대기 중인 알림 데이터."""

    incident_id: str
    """인시던트 ID."""

    service_name: str
    """서비스명."""

    duration_seconds: float
    """장애 지속 시간."""

    postmortem_link: str
    """Postmortem 링크."""

    timestamp: float
    """등록 시각 (Unix timestamp)."""

    group_id: str | None = None
    """IncidentGroup ID."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "incident_id": self.incident_id,
            "service_name": self.service_name,
            "duration_seconds": self.duration_seconds,
            "postmortem_link": self.postmortem_link,
            "timestamp": self.timestamp,
            "group_id": self.group_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingNotification:
        """딕셔너리에서 생성."""
        return cls(
            incident_id=data.get("incident_id", ""),
            service_name=data.get("service_name", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            postmortem_link=data.get("postmortem_link", ""),
            timestamp=data.get("timestamp", 0.0),
            group_id=data.get("group_id"),
        )


class NotificationAggregator:
    """
    알림 집계기 - Alert Storm 방지.

    Postmortem 생성 시 즉시 알림을 보내지 않고, 집계 윈도우 동안 대기 후
    단일 요약 알림을 발송합니다.

    Storage Strategy:
    - Redis 정상: Redis ZSET 기반 대기 큐
    - Redis 장애: In-Memory Fallback

    Usage:
        aggregator = get_notification_aggregator()

        # 알림 등록
        aggregator.add_notification(
            incident_id="AUTO-payment-20260128-120000",
            service_name="payment",
            duration_seconds=120.0,
            postmortem_link="/api/incidents/AUTO-payment-20260128-120000/",
        )

        # 집계 윈도우 종료 후 (Celery Task에서)
        summary = aggregator.flush_and_create_summary("default")
        if summary:
            send_notification(summary)
    """

    REDIS_KEY_PENDING = "selfhealing:notif_agg:pending:{namespace}"
    """대기 중인 알림 ZSET 키 패턴."""

    REDIS_KEY_SENT = "selfhealing:notif_agg:sent:{hash}"
    """발송 완료 중복 방지 키 패턴."""

    DEFAULT_PENDING_TTL = 900  # 15분
    DEFAULT_SENT_TTL = 3600  # 1시간

    def __init__(
        self,
        window_seconds: int = 60,
        max_wait_seconds: int = 300,
        use_redis: bool = True,
    ):
        """
        Initialize NotificationAggregator.

        Args:
            window_seconds: 집계 윈도우 크기 (기본 60초)
            max_wait_seconds: 최대 대기 시간 (기본 300초)
            use_redis: Redis 사용 여부
        """
        self.window_seconds = window_seconds
        self.max_wait_seconds = max_wait_seconds
        self._use_redis = use_redis

        # In-Memory Fallback
        # namespace -> list[PendingNotification]
        self._pending: dict[str, list[PendingNotification]] = {}
        self._sent_hashes: set[str] = set()
        self._lock = threading.RLock()

        # Redis 클라이언트
        self._redis_client = None
        if use_redis:
            self._init_redis_client()

    def _init_redis_client(self) -> None:
        """Redis 클라이언트 초기화."""
        try:
            from selfhealing.core.state_backend import (
                RedisStateBackend,
                get_state_backend,
            )

            backend = get_state_backend()
            if isinstance(backend, RedisStateBackend):
                self._redis_client = backend._client
                logger.info("[NotificationAggregator] Redis mode enabled")
            else:
                logger.info("[NotificationAggregator] File backend, using memory mode")
        except Exception as e:
            logger.warning(f"[NotificationAggregator] Redis init failed: {e}")

    def _compute_hash(self, incident_id: str) -> str:
        """중복 방지용 해시 계산."""
        return hashlib.sha256(incident_id.encode()).hexdigest()[:32]

    def _get_current_timestamp(self) -> str:
        """현재 ISO timestamp 반환."""
        return datetime.now(timezone.utc).isoformat()

    def add_notification(
        self,
        incident_id: str,
        service_name: str,
        duration_seconds: float,
        postmortem_link: str,
        group_id: str | None = None,
        namespace: str = "default",
    ) -> bool:
        """
        알림 요청 등록.

        Args:
            incident_id: 인시던트 ID
            service_name: 서비스명
            duration_seconds: 장애 지속 시간
            postmortem_link: Postmortem 링크
            group_id: IncidentGroup ID (있는 경우)
            namespace: 네임스페이스

        Returns:
            등록 성공 여부 (중복이면 False)
        """
        if self._redis_client:
            try:
                return self._add_notification_redis(
                    incident_id,
                    service_name,
                    duration_seconds,
                    postmortem_link,
                    group_id,
                    namespace,
                )
            except Exception as e:
                logger.warning(f"[NotificationAggregator] Redis error, fallback to memory: {e}")

        return self._add_notification_memory(
            incident_id,
            service_name,
            duration_seconds,
            postmortem_link,
            group_id,
            namespace,
        )

    def _add_notification_redis(
        self,
        incident_id: str,
        service_name: str,
        duration_seconds: float,
        postmortem_link: str,
        group_id: str | None,
        namespace: str,
    ) -> bool:
        """Redis 기반 알림 등록."""
        # 중복 체크
        hash_key = self.REDIS_KEY_SENT.format(hash=self._compute_hash(incident_id))
        if self._redis_client.exists(hash_key):
            logger.debug(f"[NotificationAggregator] Duplicate notification skipped: {incident_id}")
            return False

        now = time.time()
        pending_key = self.REDIS_KEY_PENDING.format(namespace=namespace)

        notification = PendingNotification(
            incident_id=incident_id,
            service_name=service_name,
            duration_seconds=duration_seconds,
            postmortem_link=postmortem_link,
            timestamp=now,
            group_id=group_id,
        )

        # ZSET에 추가 (score = timestamp)
        member = json.dumps(notification.to_dict(), default=str)
        self._redis_client.zadd(pending_key, {member: now})
        self._redis_client.expire(pending_key, self.DEFAULT_PENDING_TTL)

        logger.info(f"[NotificationAggregator] Notification queued: {incident_id} " f"(namespace={namespace})")

        return True

    def _add_notification_memory(
        self,
        incident_id: str,
        service_name: str,
        duration_seconds: float,
        postmortem_link: str,
        group_id: str | None,
        namespace: str,
    ) -> bool:
        """In-Memory 기반 알림 등록."""
        hash_val = self._compute_hash(incident_id)

        with self._lock:
            if hash_val in self._sent_hashes:
                logger.debug(f"[NotificationAggregator] Duplicate notification skipped: " f"{incident_id}")
                return False

            if namespace not in self._pending:
                self._pending[namespace] = []

            notification = PendingNotification(
                incident_id=incident_id,
                service_name=service_name,
                duration_seconds=duration_seconds,
                postmortem_link=postmortem_link,
                timestamp=time.time(),
                group_id=group_id,
            )

            self._pending[namespace].append(notification)

            logger.info(f"[NotificationAggregator] Notification queued (memory): {incident_id}")

            return True

    def should_flush(self, namespace: str = "default") -> bool:
        """
        집계 윈도우 종료 여부 확인.

        첫 번째 대기 알림 기준:
        - window_seconds 경과 → True
        - max_wait_seconds 경과 → True (강제)

        Args:
            namespace: 네임스페이스

        Returns:
            Flush 필요 여부
        """
        if self._redis_client:
            try:
                return self._should_flush_redis(namespace)
            except Exception as e:
                logger.warning(f"[NotificationAggregator] Redis error, fallback to memory: {e}")

        return self._should_flush_memory(namespace)

    def _should_flush_redis(self, namespace: str) -> bool:
        """Redis에서 Flush 조건 확인."""
        pending_key = self.REDIS_KEY_PENDING.format(namespace=namespace)
        now = time.time()

        # 첫 번째 알림 조회 (가장 오래된 것)
        oldest = self._redis_client.zrange(pending_key, 0, 0, withscores=True)
        if not oldest:
            return False

        _, oldest_ts = oldest[0]
        elapsed = now - oldest_ts

        return elapsed >= self.window_seconds or elapsed >= self.max_wait_seconds

    def _should_flush_memory(self, namespace: str) -> bool:
        """In-Memory에서 Flush 조건 확인."""
        with self._lock:
            pending = self._pending.get(namespace, [])
            if not pending:
                return False

            now = time.time()
            oldest_ts = min(n.timestamp for n in pending)
            elapsed = now - oldest_ts

            return elapsed >= self.window_seconds or elapsed >= self.max_wait_seconds

    def flush_and_create_summary(
        self,
        namespace: str = "default",
    ) -> IncidentSummaryNotification | None:
        """
        대기 중인 알림을 집계하고 요약 생성.

        Args:
            namespace: 네임스페이스

        Returns:
            집계된 IncidentSummaryNotification 또는 None (대기 알림 없음)
        """
        if self._redis_client:
            try:
                return self._flush_and_create_summary_redis(namespace)
            except Exception as e:
                logger.warning(f"[NotificationAggregator] Redis error, fallback to memory: {e}")

        return self._flush_and_create_summary_memory(namespace)

    def _flush_and_create_summary_redis(
        self,
        namespace: str,
    ) -> IncidentSummaryNotification | None:
        """Redis에서 알림 집계 및 요약 생성."""
        pending_key = self.REDIS_KEY_PENDING.format(namespace=namespace)

        # 모든 대기 알림 조회
        raw_entries = self._redis_client.zrange(pending_key, 0, -1)
        if not raw_entries:
            return None

        notifications: list[PendingNotification] = []
        for raw in raw_entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                data = json.loads(raw)
                notifications.append(PendingNotification.from_dict(data))
            except json.JSONDecodeError:
                continue

        if not notifications:
            return None

        # 요약 생성
        summary = self._create_summary(notifications)

        # 대기 큐 클리어
        self._redis_client.delete(pending_key)

        # 발송 완료 마킹
        for n in notifications:
            hash_key = self.REDIS_KEY_SENT.format(hash=self._compute_hash(n.incident_id))
            self._redis_client.set(hash_key, "1", ex=self.DEFAULT_SENT_TTL)

        logger.info(
            f"[NotificationAggregator] Summary created: " f"{summary.total_incidents} incidents (namespace={namespace})"
        )

        return summary

    def _flush_and_create_summary_memory(
        self,
        namespace: str,
    ) -> IncidentSummaryNotification | None:
        """In-Memory에서 알림 집계 및 요약 생성."""
        with self._lock:
            notifications = self._pending.pop(namespace, [])
            if not notifications:
                return None

            # 요약 생성
            summary = self._create_summary(notifications)

            # 발송 완료 마킹
            for n in notifications:
                self._sent_hashes.add(self._compute_hash(n.incident_id))

            logger.info(f"[NotificationAggregator] Summary created (memory): " f"{summary.total_incidents} incidents")

            return summary

    def _create_summary(
        self,
        notifications: list[PendingNotification],
    ) -> IncidentSummaryNotification:
        """대기 알림들로부터 요약 생성."""
        affected_services = list({n.service_name for n in notifications})
        total_downtime = sum(n.duration_seconds for n in notifications)
        postmortem_links = [n.postmortem_link for n in notifications if n.postmortem_link]
        group_ids = {n.group_id for n in notifications if n.group_id}
        primary_incident_id = notifications[0].incident_id if notifications else ""

        return IncidentSummaryNotification(
            total_incidents=len(notifications),
            affected_services=affected_services,
            total_downtime_seconds=total_downtime,
            group_id=list(group_ids)[0] if len(group_ids) == 1 else None,
            postmortem_links=postmortem_links,
            primary_incident_id=primary_incident_id,
            created_at=self._get_current_timestamp(),
        )

    def get_pending_count(self, namespace: str = "default") -> int:
        """대기 중인 알림 수."""
        if self._redis_client:
            try:
                pending_key = self.REDIS_KEY_PENDING.format(namespace=namespace)
                return self._redis_client.zcard(pending_key) or 0
            except Exception:
                pass

        with self._lock:
            return len(self._pending.get(namespace, []))

    def clear(self, namespace: str = "default") -> None:
        """대기 큐 초기화 (테스트용)."""
        if self._redis_client:
            try:
                pending_key = self.REDIS_KEY_PENDING.format(namespace=namespace)
                self._redis_client.delete(pending_key)
            except Exception as e:
                logger.warning(f"[NotificationAggregator] Redis clear error: {e}")

        with self._lock:
            if namespace in self._pending:
                del self._pending[namespace]


# 싱글톤 인스턴스
_notification_aggregator: NotificationAggregator | None = None


def get_notification_aggregator() -> NotificationAggregator:
    """Get singleton NotificationAggregator instance."""
    global _notification_aggregator
    if _notification_aggregator is None:
        # Settings에서 설정 로드
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            settings = get_postmortem_settings()
            _notification_aggregator = NotificationAggregator(
                window_seconds=getattr(settings, "notification_aggregation_window_seconds", 60),
                max_wait_seconds=getattr(settings, "notification_aggregation_max_wait_seconds", 300),
                use_redis=True,
            )
        except Exception:
            _notification_aggregator = NotificationAggregator()
    return _notification_aggregator


def reset_notification_aggregator() -> None:
    """Reset singleton (for testing)."""
    global _notification_aggregator
    _notification_aggregator = None


__all__ = [
    "NotificationAggregator",
    "IncidentSummaryNotification",
    "PendingNotification",
    "get_notification_aggregator",
    "reset_notification_aggregator",
]
