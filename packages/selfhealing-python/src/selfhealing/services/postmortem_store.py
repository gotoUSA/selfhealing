"""
Post-mortem Incident Storage Service.

실제 장애에 대한 Post-mortem 인시던트를 저장하고 조회합니다.

X-Test 모듈과 분리된 독립적인 저장소로, 실제 프로덕션 인시던트를 관리합니다.
- In-Memory 캐시: 빠른 읽기 지원
- PostgreSQL 영속성: 영구 저장 및 필터링 쿼리 지원

Features:
- add_healing_incident(): 인시던트 저장 (DB + In-Memory)
- get_healing_incidents(): 인시던트 조회 (필터링 지원)
- get_healing_incidents_count(): 인시던트 카운트
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone as dt_timezone
from typing import Any

logger = logging.getLogger(__name__)


def _get_current_timestamp() -> str:
    """현재 시간을 ISO 형식 문자열로 반환."""
    return datetime.now(dt_timezone.utc).isoformat()


# =============================================================================
# In-Memory Incident Storage (Singleton) + PostgreSQL Persistence
# =============================================================================

_healing_incidents_lock = threading.Lock()
_healing_incidents: list[dict[str, Any]] = []
_max_incidents = 100

# PostgreSQL 저장 활성화 플래그 (테스트에서 비활성화 가능)
_db_persistence_enabled = True


def set_db_persistence_enabled(enabled: bool) -> None:
    """PostgreSQL 영속성 활성화/비활성화 설정."""
    global _db_persistence_enabled
    _db_persistence_enabled = enabled


def get_db_persistence_enabled() -> bool:
    """PostgreSQL 영속성 활성화 여부 반환."""
    return _db_persistence_enabled


def _get_postmortem_model():
    """PostmortemRecord 모델을 동적으로 가져옴 (순환 import 방지)."""
    try:
        from shopping.models import PostmortemRecord

        return PostmortemRecord
    except ImportError:
        return None


def _save_incident_to_db(incident: dict[str, Any]) -> bool:
    """
    인시던트를 PostgreSQL에 저장.

    Returns:
        저장 성공 여부
    """
    if not _db_persistence_enabled:
        return False

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        logger.debug("[Postmortem] PostmortemRecord model not available")
        return False

    try:
        record = PostmortemRecord.create_from_incident_dict(incident)
        record.save()
        logger.debug(f"[Postmortem] Saved to DB: {record.incident_id}")
        return True
    except Exception as e:
        logger.warning(f"[Postmortem] DB save failed, using in-memory fallback: {e}")
        return False


def add_healing_incident(incident: dict[str, Any]) -> None:
    """
    힐링 인시던트 기록.

    PostgreSQL에 영구 저장을 시도하고, 실패 시 In-Memory fallback.
    In-Memory 캐시는 항상 유지하여 빠른 읽기를 지원합니다.

    Args:
        incident: 인시던트 데이터 딕셔너리
    """
    global _healing_incidents

    # recorded_at 타임스탬프 추가
    incident["recorded_at"] = _get_current_timestamp()

    # PostgreSQL 저장 시도
    db_saved = _save_incident_to_db(incident)

    # In-Memory 캐시에도 저장 (빠른 읽기용 + DB 실패 시 fallback)
    with _healing_incidents_lock:
        _healing_incidents.append(incident)
        if len(_healing_incidents) > _max_incidents:
            _healing_incidents = _healing_incidents[-_max_incidents:]

    if db_saved:
        logger.debug("[Postmortem] Incident saved to DB and cache")
    else:
        logger.debug("[Postmortem] Incident saved to in-memory cache only")


def get_healing_incidents(
    limit: int = 10,
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    offset: int = 0,
    use_db: bool = True,
) -> list[dict[str, Any]]:
    """
    힐링 인시던트 조회.

    PostgreSQL에서 조회를 시도하고, 실패 시 In-Memory fallback.
    필터링 파라미터가 있으면 DB 조회를 사용합니다.

    Args:
        limit: 반환할 최대 개수
        start_date: 시작 날짜 필터 (ISO format)
        end_date: 종료 날짜 필터 (ISO format)
        service: 서비스 이름 필터 (affected_services에 포함)
        min_duration: 최소 지속 시간 필터 (초)
        offset: 페이지네이션 오프셋
        use_db: DB 조회 사용 여부

    Returns:
        인시던트 딕셔너리 리스트
    """
    has_filters = any([start_date, end_date, service, min_duration, offset > 0])

    # 필터가 있거나 DB 사용이 명시된 경우 DB 조회 시도
    if use_db and _db_persistence_enabled and (has_filters or limit > _max_incidents):
        try:
            return _get_incidents_from_db(
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                service=service,
                min_duration=min_duration,
                offset=offset,
            )
        except Exception as e:
            logger.warning(f"[Postmortem] DB query failed, using in-memory: {e}")

    # In-Memory fallback (필터링 없이 최근 데이터만)
    with _healing_incidents_lock:
        return list(_healing_incidents[-limit:])


def _get_incidents_from_db(
    limit: int,
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """PostgreSQL에서 인시던트 조회."""
    from datetime import datetime

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        raise ImportError("PostmortemRecord model not available")

    queryset = PostmortemRecord.objects.all()

    # 날짜 필터
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__gte=start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__lte=end_dt)
        except ValueError:
            pass

    # 최소 지속 시간 필터
    if min_duration is not None:
        queryset = queryset.filter(duration_seconds__gte=min_duration)

    # 서비스 필터 (JSONField 검색)
    if service:
        queryset = queryset.filter(affected_services__contains=[service])

    # 정렬 및 페이지네이션
    queryset = queryset.order_by("-started_at")[offset : offset + limit]

    return [record.to_dict() for record in queryset]


def get_healing_incidents_count(
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    use_db: bool = True,
) -> int:
    """
    힐링 인시던트 총 개수.

    필터가 있으면 DB에서 카운트, 없으면 In-Memory 캐시 카운트.

    Args:
        start_date: 시작 날짜 필터 (ISO format)
        end_date: 종료 날짜 필터 (ISO format)
        service: 서비스 이름 필터 (affected_services에 포함)
        min_duration: 최소 지속 시간 필터 (초)
        use_db: DB 조회 사용 여부

    Returns:
        인시던트 개수
    """
    has_filters = any([start_date, end_date, service, min_duration])

    if use_db and _db_persistence_enabled and has_filters:
        try:
            return _get_incidents_count_from_db(
                start_date=start_date,
                end_date=end_date,
                service=service,
                min_duration=min_duration,
            )
        except Exception as e:
            logger.warning(f"[Postmortem] DB count failed, using in-memory: {e}")

    with _healing_incidents_lock:
        return len(_healing_incidents)


def _get_incidents_count_from_db(
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
) -> int:
    """PostgreSQL에서 인시던트 카운트."""
    from datetime import datetime

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        raise ImportError("PostmortemRecord model not available")

    queryset = PostmortemRecord.objects.all()

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__gte=start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__lte=end_dt)
        except ValueError:
            pass

    if min_duration is not None:
        queryset = queryset.filter(duration_seconds__gte=min_duration)

    if service:
        queryset = queryset.filter(affected_services__contains=[service])

    return queryset.count()


def clear_healing_incidents() -> int:
    """
    In-Memory 캐시의 인시던트 초기화 (테스트용).

    Returns:
        초기화된 인시던트 개수
    """
    global _healing_incidents
    with _healing_incidents_lock:
        count = len(_healing_incidents)
        _healing_incidents = []
        return count


def get_incident_by_id(incident_id: str, use_db: bool = True) -> dict[str, Any] | None:
    """
    ID로 단일 인시던트 조회.

    PostgreSQL에서 조회를 시도하고, 실패 시 In-Memory fallback.

    Args:
        incident_id: 조회할 인시던트 ID
        use_db: DB 조회 사용 여부

    Returns:
        인시던트 딕셔너리 또는 None (미발견 시)
    """
    # DB 조회 시도
    if use_db and _db_persistence_enabled:
        try:
            incident = _get_incident_by_id_from_db(incident_id)
            if incident is not None:
                return incident
        except Exception as e:
            logger.warning(f"[Postmortem] DB query by ID failed, using in-memory: {e}")

    # In-Memory fallback
    with _healing_incidents_lock:
        for incident in _healing_incidents:
            if incident.get("incident_id") == incident_id:
                return incident.copy()
        return None


def _get_incident_by_id_from_db(incident_id: str) -> dict[str, Any] | None:
    """PostgreSQL에서 ID로 인시던트 조회."""
    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        raise ImportError("PostmortemRecord model not available")

    try:
        record = PostmortemRecord.objects.get(incident_id=incident_id)
        return record.to_dict()
    except PostmortemRecord.DoesNotExist:
        return None


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "add_healing_incident",
    "get_healing_incidents",
    "get_healing_incidents_count",
    "get_incident_by_id",
    "clear_healing_incidents",
    "set_db_persistence_enabled",
    "get_db_persistence_enabled",
]
