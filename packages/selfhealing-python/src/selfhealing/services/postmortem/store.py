"""
Post-mortem Incident Storage Service.

This is the canonical location for postmortem incident storage.
Previously located at ``selfhealing.services.postmortem_store``.

실제 장애에 대한 Post-mortem 인시던트를 저장하고 조회합니다.

X-Test 모듈과 분리된 독립적인 저장소로, 실제 프로덕션 인시던트를 관리합니다.
- In-Memory 캐시: 빠른 읽기 지원
- PostgreSQL 영속성: 영구 저장 및 필터링 쿼리 지원
- Redis 분산 락: 중복 생성 방지

Features:
- add_healing_incident(): 인시던트 저장 (DB + In-Memory)
- get_healing_incidents(): 인시던트 조회 (필터링 지원)
- get_healing_incidents_count(): 인시던트 카운트
- add_healing_incident_with_lock(): 분산 락 적용 인시던트 저장
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Distributed Lock Configuration for Postmortem
# =============================================================================

# 락 키 패턴 (문서 146 섹션 12.2)
LOCK_KEY_POSTMORTEM_GENERATE = "postmortem:generate:{incident_id}"
LOCK_KEY_POSTMORTEM_GROUP = "postmortem:group:{group_id}"

# 락 TTL (초)
LOCK_TTL_POSTMORTEM_GENERATE = 30
LOCK_TTL_POSTMORTEM_GROUP = 60


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
    """
    PostmortemRecord 모델을 동적으로 가져옴.

    우선순위:
    1. ProviderRegistry에 등록된 모델 (권장)
    2. 하위 호환성을 위한 직접 import (deprecated)

    Returns:
        PostmortemRecord 모델 클래스, 또는 None
    """
    # 1. ProviderRegistry 우선 조회 (권장 방식)
    try:
        from selfhealing.factory import ProviderRegistry

        model = ProviderRegistry.get_postmortem_model()
        if model is not None:
            return model
    except ImportError:
        pass

    # 2. 하위 호환성: selfhealing 패키지 concrete 모델 직접 import
    try:
        from selfhealing.adapters.django.models import PostmortemRecord

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


def _get_redis_client():
    """
    Redis 클라이언트 획득 (분산 락용).

    ProviderRegistry를 통해 등록된 Redis 클라이언트를 반환합니다.

    Returns:
        Redis 클라이언트 인스턴스 또는 None
    """
    try:
        from selfhealing.factory import ProviderRegistry

        return ProviderRegistry.get_cache_provider()
    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[Postmortem] Redis client not available: {e}")
        return None


def _acquire_postmortem_lock(incident_id: str, timeout_seconds: int = LOCK_TTL_POSTMORTEM_GENERATE):
    """
    Postmortem 생성용 분산 락 획득.

    Redis가 사용 가능한 경우 RedisDistributedLock을 사용하고,
    그렇지 않으면 None을 반환합니다 (락 없이 진행).

    Args:
        incident_id: 인시던트 ID
        timeout_seconds: 락 TTL (초)

    Returns:
        DistributedLock 인스턴스 또는 None
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return None

    try:
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock

        lock_name = LOCK_KEY_POSTMORTEM_GENERATE.format(incident_id=incident_id)
        lock = RedisDistributedLock(
            redis_client=redis_client,
            name=lock_name,
            timeout=timedelta(seconds=timeout_seconds),
            blocking_timeout=1.0,  # 최대 1초 대기
            sleep_interval=0.05,
        )
        return lock
    except ImportError:
        logger.debug("[Postmortem] RedisDistributedLock not available")
        return None
    except Exception as e:
        logger.debug(f"[Postmortem] Failed to create lock: {e}")
        return None


def add_healing_incident_with_lock(incident: dict[str, Any]) -> bool:
    """
    분산 락을 사용한 힐링 인시던트 저장.

    Redis 분산 락으로 동일 incident_id에 대한 중복 생성을 방지합니다.
    락 획득 실패 시 저장을 스킵합니다.

    Args:
        incident: 인시던트 데이터 딕셔너리 (incident_id 필수)

    Returns:
        저장 성공 여부 (락 획득 실패 시 False)
    """
    incident_id = incident.get("incident_id")
    if not incident_id:
        logger.warning("[Postmortem] incident_id is required for locked save")
        add_healing_incident(incident)
        return True

    lock = _acquire_postmortem_lock(incident_id)

    # Redis 미사용 환경: 락 없이 저장
    if lock is None:
        add_healing_incident(incident)
        return True

    # 락 획득 시도
    if not lock.acquire(blocking=True, timeout=1.0):
        logger.info(f"[Postmortem] Skip duplicate save, lock held: {incident_id}")
        return False

    try:
        add_healing_incident(incident)
        logger.debug(f"[Postmortem] Saved with lock: {incident_id}")
        return True
    finally:
        try:
            lock.release()
        except Exception as e:
            logger.debug(f"[Postmortem] Lock release error: {e}")


def acquire_group_close_lock(group_id: str, timeout_seconds: int = LOCK_TTL_POSTMORTEM_GROUP):
    """
    IncidentGroup 종료용 분산 락 획득.

    그룹 종료 및 Postmortem 생성 시 중복 처리를 방지합니다.

    Args:
        group_id: 그룹 ID
        timeout_seconds: 락 TTL (초)

    Returns:
        DistributedLock 인스턴스 또는 None
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return None

    try:
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock

        lock_name = LOCK_KEY_POSTMORTEM_GROUP.format(group_id=group_id)
        lock = RedisDistributedLock(
            redis_client=redis_client,
            name=lock_name,
            timeout=timedelta(seconds=timeout_seconds),
            blocking_timeout=2.0,  # 최대 2초 대기
            sleep_interval=0.1,
        )
        return lock
    except ImportError:
        logger.debug("[Postmortem] RedisDistributedLock not available")
        return None
    except Exception as e:
        logger.debug(f"[Postmortem] Failed to create group lock: {e}")
        return None


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


def update_incident_fields(incident_id: str, fields: dict[str, Any]) -> bool:
    """기존 인시던트의 특정 필드를 부분 업데이트한다.

    In-Memory 캐시와 PostgreSQL 모두 업데이트를 시도한다.
    JSONField는 기존 dict 데이터와 deep merge하여 보존한다.

    Args:
        incident_id: 업데이트 대상 인시던트 ID
        fields: 업데이트할 필드 dict

    Returns:
        업데이트 성공 여부
    """
    updated = False

    # 1. In-Memory 캐시 업데이트
    with _healing_incidents_lock:
        for incident in _healing_incidents:
            if incident.get("incident_id") == incident_id:
                for key, value in fields.items():
                    existing = incident.get(key)
                    if isinstance(existing, dict) and isinstance(value, dict):
                        existing.update(value)
                    else:
                        incident[key] = value
                updated = True
                break

    # 2. PostgreSQL 업데이트
    if _db_persistence_enabled:
        try:
            db_updated = _update_incident_fields_in_db(incident_id, fields)
            updated = updated or db_updated
        except Exception as e:
            logger.warning(f"[Postmortem] DB update_incident_fields failed: {e}")

    return updated


def _update_incident_fields_in_db(incident_id: str, fields: dict[str, Any]) -> bool:
    """PostgreSQL에서 인시던트 필드를 부분 업데이트한다."""
    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        return False

    try:
        record = PostmortemRecord.objects.filter(incident_id=incident_id).first()
        if not record:
            return False

        for key, value in fields.items():
            if hasattr(record, key):
                existing = getattr(record, key)
                if isinstance(existing, dict) and isinstance(value, dict):
                    existing.update(value)
                    setattr(record, key, existing)
                else:
                    setattr(record, key, value)

        record.save(update_fields=list(fields.keys()))
        return True
    except Exception as e:
        logger.debug(f"[Postmortem] _update_incident_fields_in_db failed: {e}")
        return False


# =============================================================================
# Postmortem Generation Helpers
# =============================================================================


def collect_service_states(cb_service) -> tuple[list, list]:
    """Collect affected and unaffected services from CB states.

    Args:
        cb_service: CircuitBreakerService 인스턴스

    Returns:
        tuple: (affected_services, unaffected_services)
    """
    all_states = cb_service.repository.get_all_states()
    affected = [s.service_name for s in all_states if s.state == "open"]
    unaffected = [s.service_name for s in all_states if s.state != "open"]
    return affected, unaffected


def build_timeline(history: list, local_events: list) -> list:
    """Build sorted timeline from history and local events.

    Args:
        history: 이벤트 버스 히스토리
        local_events: 로컬 힐링 이벤트

    Returns:
        정렬된 타임라인 리스트
    """
    timeline = []

    # CB 상태 변경 이벤트 필터링
    cb_events = [
        e for e in history if "circuit_breaker" in e.get("event_type", "").lower() or e.get("data", {}).get("state_change")
    ]

    for e in cb_events[:20]:
        timeline.append(
            {
                "timestamp": e.get("timestamp"),
                "event_type": e.get("event_type"),
                "details": e.get("data", {}),
            }
        )

    for e in local_events:
        timeline.append(
            {
                "timestamp": e.get("recorded_at"),
                "event_type": e.get("event_type"),
                "details": e,
            }
        )

    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
    return timeline


def _parse_incident_times(duration_result) -> tuple[datetime | None, datetime | None]:
    """시작/종료 시각 파싱."""
    start_time = None
    end_time = None
    if duration_result.started_at:
        try:
            start_time = datetime.fromisoformat(duration_result.started_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    if duration_result.resolved_at:
        try:
            end_time = datetime.fromisoformat(duration_result.resolved_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    return start_time, end_time


def _collect_deployment_context(
    start_time: datetime | None,
    target_service: str,
) -> tuple[dict | None, list]:
    """배포 연관성 분석 데이터 수집."""
    deployment_context = None
    deployment_timeline_events = []
    try:
        from selfhealing.services.postmortem.deployment_correlator import get_deployment_correlator

        correlator = get_deployment_correlator()

        if start_time and correlator.is_enabled():
            deployment_context = correlator.get_deployments_for_postmortem(
                incident_time=start_time,
                service_name=target_service,
            )
            deployment_timeline_events = correlator.get_deployment_timeline_events(
                incident_time=start_time,
                service_name=target_service,
            )
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to collect deployment context: {e}")
    return deployment_context, deployment_timeline_events


def _build_timeline_snapshot(
    target_service: str,
    start_time: datetime | None,
    end_time: datetime | None,
    timeline: list,
) -> dict:
    """타임라인 스냅샷 빌드."""
    try:
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name=target_service,
            start_time=start_time,
            end_time=end_time,
        )
        return builder.build_dict(timeline[:30])
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to build timeline snapshot: {e}")
    return {}


def _collect_throttle_data(
    start_time: datetime | None,
    end_time: datetime | None,
) -> dict:
    """Throttle 상태 데이터 수집."""
    try:
        from selfhealing.services.throttle.postmortem import collect_throttle_postmortem_data

        return collect_throttle_postmortem_data(
            start_time=start_time,
            end_time=end_time,
        )
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Failed to collect throttle data: {e}")
    return {}


def _collect_cascade_event_data(
    target_service: str,
) -> tuple[str | None, list[str], str | None]:
    """CascadeEvent 감사 증적 수집."""
    cascade_event_id = None
    causation_chain: list[str] = []
    evidence_hash = None
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()
        recent_events = auditor.get_recent_events(namespace="default", limit=50)

        for event in recent_events:
            trigger_service = event.trigger.details.get("service_name")
            effect_services = [e.target for e in event.effects if e.target]

            if target_service in [trigger_service] + effect_services:
                cascade_event_id = event.id
                causation_chain = event.get_causation_chain()
                evidence_hash = event.current_hash
                break
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Failed to collect cascade event data: {e}")
    return cascade_event_id, causation_chain, evidence_hash


def _build_deep_links(
    incident_id: str,
    target_service: str,
    duration_result,
    cascade_event_id: str | None,
    evidence_hash: str | None,
) -> dict:
    """딥링크 생성."""
    try:
        from selfhealing.services.postmortem.deep_links import get_postmortem_deep_link_builder

        deep_link_builder = get_postmortem_deep_link_builder()
        postmortem_links = deep_link_builder.build_postmortem_links(
            incident_id=incident_id,
            service_name=target_service,
            start_time=duration_result.started_at,
            end_time=duration_result.resolved_at,
            namespace="default",
            cascade_event_id=cascade_event_id,
            evidence_hash=evidence_hash,
        )
        return postmortem_links.to_dict()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"Failed to build deep links: {e}")
    return {}


def generate_postmortem_data(
    incident_id: str,
    timeline: list,
    affected: list,
    unaffected: list,
    fast_fail_count: int,
    snapshot: dict,
    service_name: str | None = None,
    current_time: str | None = None,
) -> dict:
    """Generate postmortem data structure with dynamic calculations.

    Google SRE 표준에 맞춰 trigger, detection, resolution, root_cause_hypothesis 필드 포함.
    타임라인 스냅샷을 확장하여 CB OPEN/CLOSE 시점 메트릭, 피크 메트릭, 에러 로그 등을 포함합니다.
    배포 연관성 분석을 통해 인시던트 전후 배포 이력을 수집합니다.

    Args:
        incident_id: 인시던트 ID
        timeline: 타임라인 이벤트 리스트
        affected: 영향 받은 서비스 리스트
        unaffected: 영향 받지 않은 서비스 리스트
        fast_fail_count: Fast Fail 횟수
        snapshot: 시스템 스냅샷
        service_name: 대상 서비스 이름 (optional)
        current_time: 현재 시각 ISO 문자열 (optional, 없으면 자동 생성)

    Returns:
        Post-mortem 데이터 딕셔너리
    """
    from selfhealing.utils.duration import calculate_incident_duration
    from selfhealing.utils.postmortem_actions import generate_dynamic_actions
    from selfhealing.utils.postmortem_root_cause import build_postmortem_root_cause_fields

    if current_time is None:
        current_time = _get_current_timestamp()

    # duration 계산 (세분화된 정보 포함)
    duration_result = calculate_incident_duration(timeline, current_time)

    # 동적 action items 생성
    auto_actions, recommendations = generate_dynamic_actions(
        timeline=timeline,
        affected_services=affected,
        duration_seconds=duration_result.duration_seconds,
        current_timestamp=current_time,
    )

    # Root cause 관련 필드 추출
    root_cause_fields = build_postmortem_root_cause_fields(timeline, affected)

    # 서비스 이름 추출
    target_service = service_name or (affected[0] if affected else "unknown")

    # 시작/종료 시각 파싱
    start_time, end_time = _parse_incident_times(duration_result)

    # 배포 연관성 분석
    deployment_context, deployment_timeline_events = _collect_deployment_context(start_time, target_service)

    # 타임라인 스냅샷 빌드
    timeline_snapshot = _build_timeline_snapshot(target_service, start_time, end_time, timeline)

    # 타임라인에 배포 이벤트 삽입
    merged_timeline = timeline[:30] + deployment_timeline_events
    merged_timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)

    # Throttle 상태 데이터 수집
    throttle_data = _collect_throttle_data(start_time, end_time)

    # CascadeEvent 감사 증적 연결
    cascade_event_id, causation_chain, evidence_hash = _collect_cascade_event_data(target_service)

    # 딥링크 생성
    deep_links = _build_deep_links(incident_id, target_service, duration_result, cascade_event_id, evidence_hash)

    return {
        "incident_id": incident_id,
        "generated_at": current_time,
        "started_at": duration_result.started_at,
        "resolved_at": duration_result.resolved_at,
        "duration_seconds": duration_result.duration_seconds,
        "downtime_seconds": duration_result.downtime_seconds,
        "validation_seconds": duration_result.validation_seconds,
        # Google SRE 표준 필드 (trigger, detection, resolution, root_cause_hypothesis)
        "trigger": root_cause_fields.get("trigger"),
        "detection": root_cause_fields.get("detection"),
        "resolution": root_cause_fields.get("resolution"),
        "root_cause_hypothesis": root_cause_fields.get("root_cause_hypothesis"),
        "summary": {
            "affected_services": affected,
            "unaffected_services": unaffected,
            "fast_fail_count": fast_fail_count,
            "total_events": len(timeline),
        },
        "timeline": merged_timeline[:30],
        "system_snapshot": snapshot,
        # 확장된 타임라인 스냅샷
        "timeline_snapshot": timeline_snapshot,
        # 배포 연관성 분석
        "deployment_context": deployment_context,
        # Throttle 상태 데이터
        "throttle_data": throttle_data,
        "auto_actions": auto_actions,
        "recommendations": recommendations,
        # 딥링크 (Grafana, Runbook, Postmortem 상세 등)
        "deep_links": deep_links,
        # CascadeEvent 감사 증적 연결
        "cascade_event_id": cascade_event_id,
        "causation_chain": causation_chain,
        "evidence_hash": evidence_hash,
    }


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Storage functions
    "add_healing_incident",
    "add_healing_incident_with_lock",
    "get_healing_incidents",
    "get_healing_incidents_count",
    "get_incident_by_id",
    "clear_healing_incidents",
    "set_db_persistence_enabled",
    "get_db_persistence_enabled",
    # Distributed lock functions
    "acquire_group_close_lock",
    # Lock key patterns
    "LOCK_KEY_POSTMORTEM_GENERATE",
    "LOCK_KEY_POSTMORTEM_GROUP",
    "LOCK_TTL_POSTMORTEM_GENERATE",
    "LOCK_TTL_POSTMORTEM_GROUP",
    # Helper functions (new)
    "collect_service_states",
    "build_timeline",
    "generate_postmortem_data",
]
