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

    # 2. 하위 호환성: 직접 import (deprecated, 향후 제거 예정)
    try:
        from shopping.models import PostmortemRecord

        logger.warning(
            "[Postmortem] Using direct shopping.models import is deprecated. "
            "Please register model via ProviderRegistry.register_postmortem_model()"
        )
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

    # 서비스 이름 추출 (affected에서 첫 번째 또는 명시적으로 전달된 것)
    target_service = service_name or (affected[0] if affected else "unknown")

    # 시작/종료 시각 파싱
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

    # 배포 연관성 분석 (deployment_context)
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
        pass  # DeploymentCorrelator 없으면 무시
    except Exception as e:
        logger.warning(f"Failed to collect deployment context: {e}")

    # 타임라인 스냅샷 빌드 (확장)
    timeline_snapshot = {}
    try:
        from selfhealing.services.postmortem.snapshot_builder import SnapshotBuilder

        builder = SnapshotBuilder(
            service_name=target_service,
            start_time=start_time,
            end_time=end_time,
        )
        timeline_snapshot = builder.build_dict(timeline[:30])
    except ImportError:
        pass  # SnapshotBuilder 없으면 기본 방식 유지
    except Exception as e:
        logger.warning(f"Failed to build timeline snapshot: {e}")

    # 타임라인에 배포 이벤트 삽입
    merged_timeline = timeline[:30] + deployment_timeline_events
    merged_timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)

    # Throttle 상태 데이터 수집
    throttle_data = {}
    try:
        from selfhealing.services.throttle.postmortem import collect_throttle_postmortem_data

        throttle_data = collect_throttle_postmortem_data(
            start_time=start_time,
            end_time=end_time,
        )
    except ImportError:
        pass  # Throttle 모듈 없으면 무시
    except Exception as e:
        logger.debug(f"Failed to collect throttle data: {e}")

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
    }


# Deprecated aliases for backward compatibility
_collect_service_states = collect_service_states
_build_timeline = build_timeline
_generate_postmortem_data = generate_postmortem_data


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Storage functions
    "add_healing_incident",
    "get_healing_incidents",
    "get_healing_incidents_count",
    "get_incident_by_id",
    "clear_healing_incidents",
    "set_db_persistence_enabled",
    "get_db_persistence_enabled",
    # Helper functions (new)
    "collect_service_states",
    "build_timeline",
    "generate_postmortem_data",
    # Deprecated aliases (underscore prefix)
    "_collect_service_states",
    "_build_timeline",
    "_generate_postmortem_data",
]
