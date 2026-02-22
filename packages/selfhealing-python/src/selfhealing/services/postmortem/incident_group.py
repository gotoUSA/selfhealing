"""
IncidentGroup Manager - 연쇄 CB 이벤트 병합 관리.

짧은 시간 내 발생하는 연쇄 CB 이벤트를 하나의 IncidentGroup으로 병합하여
중복 Postmortem 생성을 방지하고, 연관 장애를 통합 분석할 수 있도록 합니다.

Storage:
- Redis 정상: Redis ZSET 기반 분산 저장
- Redis 장애: In-Memory Fallback (단일 프로세스)

Features:
- 시간 윈도우 기반 그룹핑 (기본 600초)
- 비활성 종료 감지 (기본 120초)
- Cascading 패턴 분석 (simultaneous, cascading, independent)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from selfhealing.services.event_bus import SelfHealingEvent

logger = structlog.get_logger()


class IncidentGroupStatus(str, Enum):
    """인시던트 그룹 상태."""

    OPEN = "OPEN"
    """이벤트 수집 중."""

    CLOSED = "CLOSED"
    """수집 완료, Postmortem 생성 대기."""

    COMPLETED = "COMPLETED"
    """Postmortem 생성됨."""


@dataclass
class IncidentGroupEntry:
    """IncidentGroup 내 개별 인시던트 엔트리."""

    service_name: str
    """CB CLOSED된 서비스."""

    closed_at: str
    """CLOSED 시각 (ISO format)."""

    opened_at: str
    """OPEN 시각 (ISO format)."""

    duration_seconds: float
    """개별 장애 지속 시간."""

    event_data: dict[str, Any]
    """원본 이벤트 데이터."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncidentGroupEntry:
        """딕셔너리에서 생성."""
        return cls(
            service_name=data.get("service_name", "unknown"),
            closed_at=data.get("closed_at", ""),
            opened_at=data.get("opened_at", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            event_data=data.get("event_data", {}),
        )


@dataclass
class IncidentGroup:
    """인시던트 그룹 데이터 모델."""

    group_id: str
    """그룹 고유 ID (INCGRP-{timestamp})."""

    status: IncidentGroupStatus
    """그룹 상태."""

    created_at: str
    """그룹 생성 시각 (ISO format)."""

    closed_at: str | None = None
    """그룹 종료 시각 (ISO format)."""

    entries: list[IncidentGroupEntry] = field(default_factory=list)
    """포함된 인시던트 목록."""

    primary_service: str = ""
    """첫 번째 장애 서비스."""

    namespace: str = "default"
    """네임스페이스."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "group_id": self.group_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "entries": [e.to_dict() for e in self.entries],
            "primary_service": self.primary_service,
            "namespace": self.namespace,
            "incident_count": len(self.entries),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IncidentGroup:
        """딕셔너리에서 생성."""
        entries = [IncidentGroupEntry.from_dict(e) for e in data.get("entries", [])]
        return cls(
            group_id=data.get("group_id", ""),
            status=IncidentGroupStatus(data.get("status", "OPEN")),
            created_at=data.get("created_at", ""),
            closed_at=data.get("closed_at"),
            entries=entries,
            primary_service=data.get("primary_service", ""),
            namespace=data.get("namespace", "default"),
        )

    @property
    def incident_count(self) -> int:
        """인시던트 개수."""
        return len(self.entries)

    def get_cascading_pattern(self) -> str:
        """
        연쇄 패턴 분석.

        Returns:
            "simultaneous": 모든 OPEN이 30초 내 (동시 다발)
            "cascading": 순차적 OPEN, 간격 < 60초 (연쇄 장애)
            "independent": OPEN 간격 > 60초 (독립 장애)
        """
        if len(self.entries) < 2:
            return "single"

        # opened_at 시각 수집 및 정렬
        open_times: list[float] = []
        for entry in self.entries:
            try:
                dt = datetime.fromisoformat(entry.opened_at.replace("Z", "+00:00"))
                open_times.append(dt.timestamp())
            except (ValueError, AttributeError):
                continue

        if len(open_times) < 2:
            return "unknown"

        open_times.sort()

        # 최대 간격 계산
        max_gap = 0.0
        for i in range(1, len(open_times)):
            gap = open_times[i] - open_times[i - 1]
            max_gap = max(max_gap, gap)

        # 전체 범위
        total_span = open_times[-1] - open_times[0]

        if total_span <= 30:
            return "simultaneous"
        elif max_gap <= 60:
            return "cascading"
        else:
            return "independent"


class IncidentGroupManager:
    """
    인시던트 그룹 관리자.

    연쇄 CB 이벤트를 그룹핑하여 중복 Postmortem 생성을 방지합니다.

    Storage Strategy:
    - Redis 정상: Redis ZSET + HASH 기반 분산 저장
    - Redis 장애: In-Memory Fallback

    Usage:
        manager = get_incident_group_manager()
        group_id, is_new = manager.add_incident(
            service_name="payment",
            event=cb_closed_event,
        )

        if manager.should_close_group(group_id):
            group = manager.close_group(group_id)
            # 통합 Postmortem 생성
    """

    REDIS_KEY_ACTIVE = "selfhealing:incgroup:active:{namespace}"
    """활성 그룹 ID 키 패턴."""

    REDIS_KEY_DATA = "selfhealing:incgroup:data:{group_id}"
    """그룹 메타데이터 키 패턴."""

    REDIS_KEY_ENTRIES = "selfhealing:incgroup:entries:{group_id}"
    """그룹 내 엔트리 키 패턴."""

    DEFAULT_ACTIVE_TTL = 900  # 15분
    DEFAULT_DATA_TTL = 3600  # 1시간

    def __init__(
        self,
        window_seconds: int = 600,
        inactivity_seconds: int = 120,
        min_count: int = 2,
        use_redis: bool = True,
    ):
        """
        Initialize IncidentGroupManager.

        Args:
            window_seconds: 그룹핑 윈도우 크기 (기본 600초 = 10분)
            inactivity_seconds: 비활성 종료 시간 (기본 120초 = 2분)
            min_count: 그룹화 최소 인시던트 수 (기본 2)
            use_redis: Redis 사용 여부
        """
        self.window_seconds = window_seconds
        self.inactivity_seconds = inactivity_seconds
        self.min_count = min_count
        self._use_redis = use_redis

        # In-Memory Fallback
        # namespace -> IncidentGroup
        self._groups: dict[str, IncidentGroup] = {}
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
                logger.info("incident_group_manager.redis_mode_enabled")
            else:
                logger.info("incident_group_manager.file_backend_using_memory")
        except Exception as e:
            from selfhealing.adapters.resilient.backend import _safe_error_message

            logger.warning(
                "resilient_storage.redis_init_failed",
                _safe_error_message=_safe_error_message(e),
            )

    def _generate_group_id(self) -> str:
        """그룹 ID 생성."""
        now = datetime.now(timezone.utc)
        return f"INCGRP-{now.strftime('%Y%m%d-%H%M%S')}-{int(now.timestamp() * 1000) % 10000:04d}"

    def _get_current_timestamp(self) -> str:
        """현재 ISO timestamp 반환."""
        return datetime.now(timezone.utc).isoformat()

    def add_incident(
        self,
        service_name: str,
        event: SelfHealingEvent,
        namespace: str = "default",
    ) -> tuple[str, bool]:
        """
        CB CLOSED 이벤트를 그룹에 추가.

        Args:
            service_name: 서비스명
            event: CB CLOSED 이벤트
            namespace: 네임스페이스 (기본: "default")

        Returns:
            (group_id, is_new_group) 튜플
        """
        if self._redis_client:
            try:
                return self._add_incident_redis(service_name, event, namespace)
            except Exception as e:
                logger.warning(
                    "incident_group_manager.redis_error_fallback_memory",
                    error=e,
                )

        return self._add_incident_memory(service_name, event, namespace)

    def _add_incident_redis(
        self,
        service_name: str,
        event: SelfHealingEvent,
        namespace: str,
    ) -> tuple[str, bool]:
        """Redis 기반 인시던트 추가."""
        active_key = self.REDIS_KEY_ACTIVE.format(namespace=namespace)
        now = time.time()
        now_iso = self._get_current_timestamp()

        # 1. 활성 그룹 ID 조회
        active_group_id = self._redis_client.get(active_key)
        if active_group_id:
            if isinstance(active_group_id, bytes):
                active_group_id = active_group_id.decode("utf-8")

        is_new_group = False

        if not active_group_id:
            # 새 그룹 생성
            active_group_id = self._generate_group_id()
            is_new_group = True

            # 메타데이터 저장
            data_key = self.REDIS_KEY_DATA.format(group_id=active_group_id)
            group_data = {
                "group_id": active_group_id,
                "status": IncidentGroupStatus.OPEN.value,
                "created_at": now_iso,
                "primary_service": service_name,
                "namespace": namespace,
            }
            self._redis_client.hset(data_key, mapping=group_data)
            self._redis_client.expire(data_key, self.DEFAULT_DATA_TTL)

            # 활성 그룹 ID 등록
            self._redis_client.set(
                active_key,
                active_group_id,
                ex=self.DEFAULT_ACTIVE_TTL,
            )

            logger.info(
                "incident_group_manager.new_group_created",
                active_group_id=active_group_id,
                service_name=service_name,
                namespace=namespace,
            )

        # 2. 엔트리 추가
        entry = IncidentGroupEntry(
            service_name=service_name,
            closed_at=now_iso,
            opened_at=event.data.get("opened_at", now_iso),
            duration_seconds=event.data.get("duration_seconds", 0.0),
            event_data=event.to_dict(),
        )

        entries_key = self.REDIS_KEY_ENTRIES.format(group_id=active_group_id)
        member = json.dumps(entry.to_dict(), default=str)
        self._redis_client.zadd(entries_key, {member: now})
        self._redis_client.expire(entries_key, self.DEFAULT_DATA_TTL)

        logger.info(
            "incident_group_manager.incident_added_group",
            active_group_id=active_group_id,
            service_name=service_name,
        )

        return active_group_id, is_new_group

    def _add_incident_memory(
        self,
        service_name: str,
        event: SelfHealingEvent,
        namespace: str,
    ) -> tuple[str, bool]:
        """In-Memory 기반 인시던트 추가."""
        now_iso = self._get_current_timestamp()

        with self._lock:
            group = self._groups.get(namespace)
            is_new_group = False

            if group is None or group.status != IncidentGroupStatus.OPEN:
                # 새 그룹 생성
                group_id = self._generate_group_id()
                group = IncidentGroup(
                    group_id=group_id,
                    status=IncidentGroupStatus.OPEN,
                    created_at=now_iso,
                    primary_service=service_name,
                    namespace=namespace,
                )
                self._groups[namespace] = group
                is_new_group = True

                logger.info(
                    "incident_group_manager.new_group_created_memory",
                    group_id=group_id,
                )

            # 엔트리 추가
            entry = IncidentGroupEntry(
                service_name=service_name,
                closed_at=now_iso,
                opened_at=event.data.get("opened_at", now_iso),
                duration_seconds=event.data.get("duration_seconds", 0.0),
                event_data=event.to_dict(),
            )
            group.entries.append(entry)

            logger.info(
                "incident_group_manager.incident_added_group",
                group=group.group_id,
                service_name=service_name,
            )

            return group.group_id, is_new_group

    def get_active_group(self, namespace: str = "default") -> IncidentGroup | None:
        """
        현재 활성 그룹 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            활성 IncidentGroup 또는 None
        """
        if self._redis_client:
            try:
                return self._get_active_group_redis(namespace)
            except Exception as e:
                logger.warning(
                    "incident_group_manager.redis_error_fallback_memory",
                    error=e,
                )

        return self._get_active_group_memory(namespace)

    def _get_active_group_redis(self, namespace: str) -> IncidentGroup | None:
        """Redis에서 활성 그룹 조회."""
        active_key = self.REDIS_KEY_ACTIVE.format(namespace=namespace)
        active_group_id = self._redis_client.get(active_key)

        if not active_group_id:
            return None

        if isinstance(active_group_id, bytes):
            active_group_id = active_group_id.decode("utf-8")

        # 메타데이터 조회
        data_key = self.REDIS_KEY_DATA.format(group_id=active_group_id)
        group_data = self._redis_client.hgetall(data_key)

        if not group_data:
            return None

        # bytes -> str 변환
        group_data = {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (v.decode("utf-8") if isinstance(v, bytes) else v)
            for k, v in group_data.items()
        }

        # 엔트리 조회
        entries_key = self.REDIS_KEY_ENTRIES.format(group_id=active_group_id)
        raw_entries = self._redis_client.zrange(entries_key, 0, -1)

        entries = []
        for raw in raw_entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                entry_data = json.loads(raw)
                entries.append(IncidentGroupEntry.from_dict(entry_data))
            except json.JSONDecodeError:
                continue

        return IncidentGroup(
            group_id=group_data.get("group_id", active_group_id),
            status=IncidentGroupStatus(group_data.get("status", "OPEN")),
            created_at=group_data.get("created_at", ""),
            closed_at=group_data.get("closed_at"),
            entries=entries,
            primary_service=group_data.get("primary_service", ""),
            namespace=group_data.get("namespace", namespace),
        )

    def _get_active_group_memory(self, namespace: str) -> IncidentGroup | None:
        """In-Memory에서 활성 그룹 조회."""
        with self._lock:
            group = self._groups.get(namespace)
            if group and group.status == IncidentGroupStatus.OPEN:
                return group
            return None

    def should_close_group(
        self,
        group_id: str,
        namespace: str = "default",
    ) -> bool:
        """
        그룹 종료 조건 확인.

        종료 조건:
        - 타임아웃: 첫 이벤트 후 window_seconds 경과
        - 비활성: 마지막 이벤트 후 inactivity_seconds 경과

        Args:
            group_id: 그룹 ID
            namespace: 네임스페이스

        Returns:
            종료 필요 여부
        """
        group = self.get_active_group(namespace)
        if not group or group.group_id != group_id:
            return False

        now = time.time()

        # 그룹 생성 시간
        try:
            created_dt = datetime.fromisoformat(group.created_at.replace("Z", "+00:00"))
            created_ts = created_dt.timestamp()
        except (ValueError, AttributeError):
            return False

        # 타임아웃 체크
        if now - created_ts >= self.window_seconds:
            logger.info(
                "incident_group_manager.group_timeout",
                group_id=group_id,
                value=now - created_ts,
            )
            return True

        # 비활성 체크 (마지막 엔트리)
        if group.entries:
            last_entry = group.entries[-1]
            try:
                last_dt = datetime.fromisoformat(last_entry.closed_at.replace("Z", "+00:00"))
                last_ts = last_dt.timestamp()
                if now - last_ts >= self.inactivity_seconds:
                    logger.info(
                        "incident_group_manager.group_inactivity_ago",
                        group_id=group_id,
                        value=now - last_ts,
                    )
                    return True
            except (ValueError, AttributeError):
                pass

        return False

    def close_group(
        self,
        group_id: str,
        namespace: str = "default",
    ) -> IncidentGroup | None:
        """
        그룹 종료 및 Postmortem 생성 트리거.

        분산 락을 사용하여 동시 종료 요청을 방지합니다.

        Args:
            group_id: 그룹 ID
            namespace: 네임스페이스

        Returns:
            종료된 IncidentGroup 또는 None
        """
        # 분산 락 획득 시도
        lock = self._acquire_group_close_lock(group_id)
        if lock is not None:
            if not lock.acquire(blocking=True, timeout=2.0):
                logger.info(
                    "incident_group_manager.skip_duplicate_close_lock",
                    group_id=group_id,
                )
                return None

        try:
            if self._redis_client:
                try:
                    return self._close_group_redis(group_id, namespace)
                except Exception as e:
                    logger.warning(
                        "incident_group_manager.redis_error_fallback_memory",
                        error=e,
                    )

            return self._close_group_memory(group_id, namespace)
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception as e:
                    logger.debug(
                        "incident_group_manager.lock_release_error",
                        error=e,
                    )

    def _acquire_group_close_lock(self, group_id: str):
        """
        그룹 종료용 분산 락 획득.

        Args:
            group_id: 그룹 ID

        Returns:
            DistributedLock 인스턴스 또는 None
        """
        try:
            from selfhealing.services.postmortem.store import acquire_group_close_lock

            return acquire_group_close_lock(group_id)
        except ImportError:
            return None
        except Exception as e:
            logger.debug(
                "incident_group_manager.failed_acquire_lock",
                error=e,
            )
            return None

    def _close_group_redis(
        self,
        group_id: str,
        namespace: str,
    ) -> IncidentGroup | None:
        """Redis에서 그룹 종료."""
        # 현재 그룹 조회
        group = self.get_active_group(namespace)
        if not group or group.group_id != group_id:
            logger.warning(
                "incident_group_manager.group_found_active",
                group_id=group_id,
            )
            return None

        now_iso = self._get_current_timestamp()

        # 상태 업데이트
        data_key = self.REDIS_KEY_DATA.format(group_id=group_id)
        self._redis_client.hset(
            data_key,
            mapping={
                "status": IncidentGroupStatus.CLOSED.value,
                "closed_at": now_iso,
            },
        )

        # 활성 그룹 ID 삭제
        active_key = self.REDIS_KEY_ACTIVE.format(namespace=namespace)
        self._redis_client.delete(active_key)

        # 그룹 객체 업데이트
        group.status = IncidentGroupStatus.CLOSED
        group.closed_at = now_iso

        logger.info(
            "incident_group_manager.group_closed",
            group_id=group_id,
            group=group.incident_count,
            group_2=group.get_cascading_pattern(),
        )

        return group

    def _close_group_memory(
        self,
        group_id: str,
        namespace: str,
    ) -> IncidentGroup | None:
        """In-Memory에서 그룹 종료."""
        now_iso = self._get_current_timestamp()

        with self._lock:
            group = self._groups.get(namespace)
            if not group or group.group_id != group_id:
                logger.warning(
                    "incident_group_manager.group_found_memory",
                    group_id=group_id,
                )
                return None

            group.status = IncidentGroupStatus.CLOSED
            group.closed_at = now_iso

            logger.info(
                "incident_group_manager.group_closed_memory",
                group_id=group_id,
                group=group.incident_count,
            )

            return group

    def mark_completed(
        self,
        group_id: str,
        namespace: str = "default",
    ) -> bool:
        """
        그룹을 COMPLETED 상태로 마킹.

        Args:
            group_id: 그룹 ID
            namespace: 네임스페이스

        Returns:
            성공 여부
        """
        if self._redis_client:
            try:
                data_key = self.REDIS_KEY_DATA.format(group_id=group_id)
                self._redis_client.hset(
                    data_key,
                    "status",
                    IncidentGroupStatus.COMPLETED.value,
                )
                return True
            except Exception as e:
                logger.warning(
                    "incident_group_manager.redis_error",
                    error=e,
                )

        with self._lock:
            group = self._groups.get(namespace)
            if group and group.group_id == group_id:
                group.status = IncidentGroupStatus.COMPLETED
                return True
        return False

    def clear(self, namespace: str = "default") -> None:
        """그룹 초기화 (테스트용)."""
        if self._redis_client:
            try:
                active_key = self.REDIS_KEY_ACTIVE.format(namespace=namespace)
                active_group_id = self._redis_client.get(active_key)
                if active_group_id:
                    if isinstance(active_group_id, bytes):
                        active_group_id = active_group_id.decode("utf-8")
                    data_key = self.REDIS_KEY_DATA.format(group_id=active_group_id)
                    entries_key = self.REDIS_KEY_ENTRIES.format(group_id=active_group_id)
                    self._redis_client.delete(active_key, data_key, entries_key)
            except Exception as e:
                logger.warning(
                    "incident_group_manager.redis_clear_error",
                    error=e,
                )

        with self._lock:
            if namespace in self._groups:
                del self._groups[namespace]


# 싱글톤 인스턴스
_incident_group_manager: IncidentGroupManager | None = None


def get_incident_group_manager() -> IncidentGroupManager:
    """Get singleton IncidentGroupManager instance."""
    global _incident_group_manager
    if _incident_group_manager is None:
        # Settings에서 설정 로드
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            settings = get_postmortem_settings()
            _incident_group_manager = IncidentGroupManager(
                window_seconds=getattr(settings, "incident_group_window_seconds", 600),
                inactivity_seconds=getattr(settings, "incident_group_inactivity_seconds", 120),
                min_count=getattr(settings, "incident_group_min_count", 2),
                use_redis=True,
            )
        except Exception:
            _incident_group_manager = IncidentGroupManager()
    return _incident_group_manager


def reset_incident_group_manager() -> None:
    """Reset singleton (for testing)."""
    global _incident_group_manager
    _incident_group_manager = None


__all__ = [
    "IncidentGroupManager",
    "IncidentGroup",
    "IncidentGroupEntry",
    "IncidentGroupStatus",
    "get_incident_group_manager",
    "reset_incident_group_manager",
]
