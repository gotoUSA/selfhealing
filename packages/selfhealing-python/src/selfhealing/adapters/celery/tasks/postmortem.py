"""
Postmortem Celery Tasks.

IncidentGroup 종료 및 Notification 집계를 위한 Celery 태스크입니다.

Tasks:
- close_incident_group: 인시던트 그룹 종료 및 통합 Postmortem 생성
- flush_aggregated_notifications: 집계된 알림 발송

Usage in CELERY_BEAT_SCHEDULE:
    'close-stale-incident-groups': {
        'task': 'selfhealing.adapters.celery.tasks.close_incident_group',
        'schedule': 60.0,  # Every minute
    },
    'flush-aggregated-notifications': {
        'task': 'selfhealing.adapters.celery.tasks.flush_aggregated_notifications',
        'schedule': 30.0,  # Every 30 seconds
    },
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.close_incident_group",
    queue="selfhealing",
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def close_incident_group(
    self,
    group_id: str,
    namespace: str = "default",
) -> dict[str, Any]:
    """
    인시던트 그룹 종료 및 통합 Postmortem 생성.

    IncidentGroupManager에서 그룹을 종료하고, 포함된 인시던트들을
    기반으로 통합 Postmortem을 생성합니다.

    그룹 내 인시던트가 min_count 미만이면 개별 Postmortem으로 생성됩니다.

    Args:
        group_id: 종료할 그룹 ID
        namespace: 네임스페이스

    Returns:
        처리 결과 딕셔너리
    """
    logger.info(f"[CloseIncidentGroup] Starting for group {group_id}")

    try:
        from selfhealing.services.postmortem.incident_group import (
            IncidentGroupStatus,
            get_incident_group_manager,
        )
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()
        manager = get_incident_group_manager()

        # 그룹 조회
        group = manager.get_active_group(namespace)

        if not group:
            logger.info(f"[CloseIncidentGroup] No active group for namespace={namespace}")
            return {
                "success": True,
                "message": "No active group",
                "group_id": group_id,
            }

        if group.group_id != group_id:
            logger.warning(f"[CloseIncidentGroup] Group mismatch: expected={group_id}, " f"active={group.group_id}")
            return {
                "success": False,
                "message": "Group ID mismatch",
                "group_id": group_id,
            }

        if group.status != IncidentGroupStatus.OPEN:
            logger.info(f"[CloseIncidentGroup] Group {group_id} already {group.status.value}")
            return {
                "success": True,
                "message": f"Group already {group.status.value}",
                "group_id": group_id,
            }

        # 종료 조건 확인
        if not manager.should_close_group(group_id, namespace):
            logger.info(f"[CloseIncidentGroup] Group {group_id} not ready to close")
            return {
                "success": True,
                "message": "Group not ready to close",
                "group_id": group_id,
            }

        # 그룹 종료
        closed_group = manager.close_group(group_id, namespace)
        if not closed_group:
            return {
                "success": False,
                "message": "Failed to close group",
                "group_id": group_id,
            }

        # Postmortem 생성 결정
        incident_count = closed_group.incident_count
        min_count = settings.incident_group_min_count

        if incident_count < min_count:
            # 개별 Postmortem 생성
            logger.info(
                f"[CloseIncidentGroup] Group {group_id} has {incident_count} incidents "
                f"(< min {min_count}), creating individual postmortems"
            )
            result = _create_individual_postmortems(closed_group)
        else:
            # 그룹 Postmortem 생성
            logger.info(f"[CloseIncidentGroup] Group {group_id} has {incident_count} incidents, " f"creating group postmortem")
            result = _create_group_postmortem(closed_group)

        # 그룹 완료 마킹
        manager.mark_completed(group_id, namespace)

        return {
            "success": True,
            "message": "Group closed and postmortem created",
            "group_id": group_id,
            "incident_count": incident_count,
            **result,
        }

    except Exception as e:
        logger.error(f"[CloseIncidentGroup] Error closing group {group_id}: {e}")
        return {
            "success": False,
            "error": str(e),
            "group_id": group_id,
        }


def _create_individual_postmortems(group) -> dict[str, Any]:
    """그룹 내 인시던트들에 대해 개별 Postmortem 생성."""
    try:
        from selfhealing.services.postmortem.integrity_sealer import get_integrity_sealer
        from selfhealing.services.postmortem_store import add_healing_incident

        sealer = get_integrity_sealer()
        created_ids = []

        for entry in group.entries:
            # 기본 Postmortem 데이터 구성
            event_data = entry.event_data
            postmortem = {
                "incident_id": f"AUTO-{entry.service_name}-{entry.closed_at[:19].replace(':', '').replace('-', '')}",
                "generated_at": entry.closed_at,
                "started_at": entry.opened_at,
                "resolved_at": entry.closed_at,
                "duration_seconds": entry.duration_seconds,
                "summary": {
                    "affected_services": [entry.service_name],
                    "unaffected_services": [],
                    "fast_fail_count": 0,
                    "total_events": 1,
                },
                "is_group": False,
                "recommendations": [
                    f"서비스 '{entry.service_name}'의 장애 원인 분석",
                    "재발 방지 대책 수립",
                ],
            }

            # 무결성 봉인
            sealed = sealer.seal(postmortem)

            # 저장
            add_healing_incident(sealed)
            created_ids.append(postmortem["incident_id"])

            logger.info(f"[CloseIncidentGroup] Individual postmortem created: {postmortem['incident_id']}")

        return {
            "postmortem_type": "individual",
            "postmortem_ids": created_ids,
        }

    except Exception as e:
        logger.error(f"[CloseIncidentGroup] Error creating individual postmortems: {e}")
        return {
            "postmortem_type": "individual",
            "error": str(e),
        }


def _create_group_postmortem(group) -> dict[str, Any]:
    """그룹 Postmortem 생성."""
    try:
        from datetime import datetime, timezone

        from selfhealing.services.postmortem.integrity_sealer import get_integrity_sealer
        from selfhealing.services.postmortem_store import add_healing_incident

        sealer = get_integrity_sealer()
        now_iso = datetime.now(timezone.utc).isoformat()

        # 서비스 목록
        affected_services = list({e.service_name for e in group.entries})

        # 총 다운타임
        total_duration = sum(e.duration_seconds for e in group.entries)

        # 타임라인 구성
        services_timeline = []
        for entry in group.entries:
            services_timeline.append(
                {
                    "service_name": entry.service_name,
                    "opened_at": entry.opened_at,
                    "closed_at": entry.closed_at,
                    "duration_seconds": entry.duration_seconds,
                }
            )

        # 연쇄 패턴
        cascading_pattern = group.get_cascading_pattern()

        # 그룹 Postmortem 데이터 구성
        postmortem = {
            "incident_id": group.group_id,
            "generated_at": now_iso,
            "started_at": group.created_at,
            "resolved_at": group.closed_at or now_iso,
            "duration_seconds": total_duration,
            "summary": {
                "affected_services": affected_services,
                "unaffected_services": [],
                "fast_fail_count": 0,
                "total_events": group.incident_count,
            },
            # 그룹 전용 필드
            "is_group": True,
            "group_id": group.group_id,
            "incident_count": group.incident_count,
            "services_timeline": services_timeline,
            "cascading_pattern": cascading_pattern,
            "primary_service": group.primary_service,
            "namespace": group.namespace,
            # 권장 조치
            "recommendations": _generate_group_recommendations(
                cascading_pattern,
                affected_services,
                group.incident_count,
            ),
        }

        # 무결성 봉인
        sealed = sealer.seal(postmortem)

        # 저장
        add_healing_incident(sealed)

        logger.info(
            f"[CloseIncidentGroup] Group postmortem created: {group.group_id} "
            f"(pattern={cascading_pattern}, services={len(affected_services)})"
        )

        return {
            "postmortem_type": "group",
            "postmortem_id": group.group_id,
            "cascading_pattern": cascading_pattern,
        }

    except Exception as e:
        logger.error(f"[CloseIncidentGroup] Error creating group postmortem: {e}")
        return {
            "postmortem_type": "group",
            "error": str(e),
        }


def _generate_group_recommendations(
    cascading_pattern: str,
    affected_services: list[str],
    incident_count: int,
) -> list[str]:
    """그룹 Postmortem 권장 조치 생성."""
    recommendations = []

    if cascading_pattern == "simultaneous":
        recommendations.append("동시 다발 장애 - 공통 원인 분석 필요")
        recommendations.append("인프라/네트워크 레벨 장애 가능성 검토")
    elif cascading_pattern == "cascading":
        recommendations.append("연쇄 장애 패턴 감지 - 의존성 체인 분석")
        recommendations.append("Circuit Breaker 설정 검토 (Fast Fail 최적화)")
    else:
        recommendations.append("독립 장애 우연 중복 - 개별 원인 분석")

    if incident_count >= 5:
        recommendations.append(f"다수 서비스 영향({incident_count}개) - 시스템 전체 안정성 검토")

    if len(affected_services) > 3:
        recommendations.append(f"영향 서비스: {', '.join(affected_services[:5])}")

    recommendations.append("장애 대응 프로세스 개선 검토")

    return recommendations


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.flush_aggregated_notifications",
    queue="selfhealing",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
    acks_late=True,
)
def flush_aggregated_notifications(
    self,
    namespace: str = "default",
) -> dict[str, Any]:
    """
    집계된 알림 발송.

    NotificationAggregator에서 집계된 알림을 요약하여 단일 알림으로 발송합니다.

    Args:
        namespace: 네임스페이스

    Returns:
        처리 결과 딕셔너리
    """
    logger.info(f"[FlushNotifications] Starting for namespace={namespace}")

    try:
        from selfhealing.services.postmortem.notification_aggregator import (
            get_notification_aggregator,
        )
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.notification_aggregation_enabled:
            logger.debug("flush_notifications.aggregation_disabled")
            return {
                "success": True,
                "message": "Aggregation disabled",
            }

        aggregator = get_notification_aggregator()

        # Flush 조건 확인
        if not aggregator.should_flush(namespace):
            pending_count = aggregator.get_pending_count(namespace)
            if pending_count > 0:
                logger.debug(f"[FlushNotifications] Not ready to flush " f"({pending_count} pending)")
            return {
                "success": True,
                "message": "Not ready to flush",
                "pending_count": pending_count,
            }

        # 요약 생성 및 발송
        summary = aggregator.flush_and_create_summary(namespace)
        if not summary:
            return {
                "success": True,
                "message": "No pending notifications",
            }

        # 알림 발송
        _send_aggregated_notification(summary, settings)

        return {
            "success": True,
            "message": "Notifications flushed",
            "total_incidents": summary.total_incidents,
            "affected_services": summary.affected_services,
        }

    except Exception as e:
        logger.error(f"[FlushNotifications] Error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


def _send_aggregated_notification(summary, settings) -> None:
    """집계된 알림 발송."""
    try:
        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            get_unified_notification_manager,
        )

        # 우선순위 결정
        if summary.total_incidents >= 5 or len(summary.affected_services) >= 3:
            priority = NotificationPriority.HIGH
        elif summary.total_incidents >= 2:
            priority = NotificationPriority.MEDIUM
        else:
            priority = NotificationPriority.LOW

        # 메시지 구성
        services_str = ", ".join(summary.affected_services[:5])
        if len(summary.affected_services) > 5:
            services_str += f" 외 {len(summary.affected_services) - 5}개"

        duration_minutes = int(summary.total_downtime_seconds / 60)

        message = (
            f"집계된 인시던트: {summary.total_incidents}건\n"
            f"영향 서비스: {services_str}\n"
            f"총 다운타임: {duration_minutes}분\n"
        )

        if summary.group_id:
            message += f"그룹 ID: {summary.group_id}\n"

        title = f"📊 인시던트 요약: {summary.total_incidents}건 발생"

        payload = NotificationPayload(
            title=title,
            message=message,
            priority=priority,
            category=NotificationCategory.OPERATIONS,
            source="NotificationAggregator",
            metadata={
                "total_incidents": summary.total_incidents,
                "affected_services": summary.affected_services,
                "total_downtime_seconds": summary.total_downtime_seconds,
                "group_id": summary.group_id,
                "postmortem_links": summary.postmortem_links,
            },
            dedup_key=f"incident_summary:{summary.created_at[:16]}",
        )

        manager = get_unified_notification_manager()
        result = manager.notify(payload)

        if result.success:
            logger.info(f"[FlushNotifications] Summary notification sent: " f"{summary.total_incidents} incidents")
        else:
            logger.warning(f"[FlushNotifications] Notification failed: {result.error}")

    except ImportError as e:
        logger.debug(f"[FlushNotifications] Notification module not available: {e}")
    except Exception as e:
        logger.warning(f"[FlushNotifications] Failed to send notification: {e}")


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.check_stale_incident_groups",
    queue="selfhealing",
    max_retries=0,
    time_limit=60,
    soft_time_limit=55,
)
def check_stale_incident_groups(
    self,
    namespace: str = "default",
) -> dict[str, Any]:
    """
    오래된 인시던트 그룹 확인 및 종료 스케줄링.

    활성 그룹이 있고 종료 조건을 충족하면 close_incident_group 태스크를
    트리거합니다.

    Args:
        namespace: 네임스페이스

    Returns:
        처리 결과 딕셔너리
    """
    logger.debug(f"[CheckStaleGroups] Checking namespace={namespace}")

    try:
        from selfhealing.services.postmortem.incident_group import (
            get_incident_group_manager,
        )
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.incident_group_enabled:
            return {
                "success": True,
                "message": "Incident grouping disabled",
            }

        manager = get_incident_group_manager()
        group = manager.get_active_group(namespace)

        if not group:
            return {
                "success": True,
                "message": "No active group",
            }

        if manager.should_close_group(group.group_id, namespace):
            # 종료 태스크 트리거
            close_incident_group.delay(
                group_id=group.group_id,
                namespace=namespace,
            )
            logger.info(f"[CheckStaleGroups] Scheduled close for group {group.group_id}")
            return {
                "success": True,
                "message": "Close task scheduled",
                "group_id": group.group_id,
            }

        return {
            "success": True,
            "message": "Group not ready to close",
            "group_id": group.group_id,
            "incident_count": group.incident_count,
        }

    except Exception as e:
        logger.error(f"[CheckStaleGroups] Error: {e}")
        return {
            "success": False,
            "error": str(e),
        }


@shared_task(
    bind=True,
    name="selfhealing.adapters.celery.tasks.process_individual_postmortem",
    queue="selfhealing",
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
    acks_late=True,
)
def process_individual_postmortem(
    self,
    service_name: str,
    event_data: dict,
    event_type: str,
    event_bus_history: list[dict] | None = None,
    web_server_metrics: dict | None = None,
) -> dict[str, Any]:
    """
    개별 Postmortem을 비동기로 생성.

    스냅샷 수집, Timeline 빌드, DB INSERT, WAL 기록, 알림 발송을
    모두 Celery Worker에서 수행하여 EventBus 발행자 스레드를 해방한다.

    Args:
        service_name: 대상 서비스 이름
        event_data: SelfHealingEvent.to_dict()로 직렬화된 이벤트 데이터.
                    Celery JSON serializer 호환을 위해 반드시 dict 타입이어야 한다.
        event_type: 분기용 이벤트 타입
                    - "circuit_breaker_closed": CB 복구 시 개별 Postmortem 생성
                    - "emergency_recovery_completed": Emergency 복구 시 Postmortem 생성
        event_bus_history: 발행자(Web Server) 프로세스에서 미리 수집한 EventBus 히스토리.
                          bus.get_history()는 프로세스 로컬 인메모리이므로
                          Celery Worker에서는 빈 리스트가 반환된다.
                          핸들러에서 .delay() 전에 반드시 수집하여 전달해야 한다.

    Worker 실행 환경 주의:
    - collect_system_snapshot()의 CPU/Memory는 Worker 노드 값
    - get_healing_events(use_redis=True)로 Redis에서 힐링 이벤트 조회 (Worker에서도 가능)
    - CB 상태 조회 — Redis 기반이므로 Worker에서도 정상 조회 가능

    Returns:
        Postmortem 생성 결과 딕셔너리
    """
    logger.info(
        f"[ProcessIndividualPostmortem] Starting for '{service_name}' "
        f"(type={event_type}, attempt {self.request.retries + 1})"
    )

    if event_bus_history is None:
        event_bus_history = []

    try:
        if event_type == "circuit_breaker_closed":
            return _process_cb_closed_postmortem(
                service_name=service_name,
                event_data=event_data,
                event_bus_history=event_bus_history,
                web_server_metrics=web_server_metrics,
            )
        elif event_type == "emergency_recovery_completed":
            return _process_emergency_postmortem(
                service_name=service_name,
                event_data=event_data,
                event_bus_history=event_bus_history,
                web_server_metrics=web_server_metrics,
            )
        else:
            logger.warning(f"[ProcessIndividualPostmortem] Unknown event_type: {event_type}")
            return {
                "success": False,
                "service_name": service_name,
                "error": f"Unknown event_type: {event_type}",
            }

    except Exception as e:
        logger.error(
            f"[ProcessIndividualPostmortem] Failed for '{service_name}': {e}",
            exc_info=True,
        )
        return {
            "success": False,
            "service_name": service_name,
            "error": str(e),
        }


def _process_cb_closed_postmortem(
    service_name: str,
    event_data: dict,
    event_bus_history: list[dict],
    web_server_metrics: dict | None = None,
) -> dict[str, Any]:
    """CB 복구 시 개별 Postmortem 생성 (Celery Worker에서 실행)."""
    from selfhealing.api.django.views.xtest.base import (
        collect_system_snapshot,
        get_healing_events,
    )
    from selfhealing.services.circuit_breaker_service import (
        get_circuit_breaker_service,
    )
    from selfhealing.services.postmortem_store import (
        add_healing_incident,
        build_timeline as _build_timeline,
        collect_service_states as _collect_service_states,
        generate_postmortem_data as _generate_postmortem_data,
    )
    from selfhealing.settings.postmortem import get_postmortem_settings

    settings = get_postmortem_settings()
    min_duration = settings.auto_min_duration

    # 상태 수집 (event_bus_history는 발행자에서 전달받은 것 사용)
    cb_service = get_circuit_breaker_service()
    affected, unaffected = _collect_service_states(cb_service)
    local_events = get_healing_events(20, use_redis=True)
    timeline = _build_timeline(event_bus_history, local_events)
    snapshot = collect_system_snapshot()

    if web_server_metrics:
        # Worker 원본 값을 별도 필드로 보존
        snapshot["worker_cpu_percent"] = snapshot.get("cpu_percent")
        snapshot["worker_memory_percent"] = snapshot.get("memory_percent")
        snapshot["worker_memory_used_mb"] = snapshot.get("memory_used_mb")
        snapshot["worker_memory_available_mb"] = snapshot.get("memory_available_mb")
        # 주 필드를 Web Server 값으로 교체
        snapshot["cpu_percent"] = web_server_metrics.get("cpu_percent", snapshot["cpu_percent"])
        snapshot["memory_percent"] = web_server_metrics.get("memory_percent", snapshot["memory_percent"])
        snapshot["memory_used_mb"] = web_server_metrics.get("memory_used_mb", snapshot.get("memory_used_mb", 0))
        snapshot["memory_available_mb"] = web_server_metrics.get("memory_available_mb", snapshot.get("memory_available_mb", 0))
        snapshot["snapshot_source"] = "web_server_cache+worker"
        snapshot["snapshot_note"] = "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값."
    else:
        snapshot["snapshot_source"] = "celery_worker"
        snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."

    # Fast fail 카운트
    fast_fail_count = len([e for e in event_bus_history if e.get("data", {}).get("fast_fail")])

    # 인시던트 ID 생성
    from django.utils import timezone

    incident_id = f"AUTO-{service_name}-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

    # Postmortem 생성
    postmortem = _generate_postmortem_data(incident_id, timeline, affected, unaffected, fast_fail_count, snapshot)

    # 최소 duration 확인
    duration = postmortem.get("duration_seconds")
    if duration is not None and duration < min_duration:
        logger.debug(
            f"[ProcessIndividualPostmortem] Skipped for '{service_name}': " f"duration {duration:.0f}s < min {min_duration}s"
        )
        return {
            "success": True,
            "service_name": service_name,
            "skipped": True,
            "reason": "duration_below_minimum",
        }

    # 무결성 봉인
    try:
        from selfhealing.services.postmortem.integrity_sealer import get_integrity_sealer

        sealer = get_integrity_sealer()
        postmortem = sealer.seal(postmortem)
    except Exception as seal_error:
        logger.warning(f"[ProcessIndividualPostmortem] Integrity seal failed: {seal_error}")

    # 저장
    add_healing_incident(postmortem)

    logger.info(f"[ProcessIndividualPostmortem] CB postmortem generated: {incident_id} " f"(duration={duration}s)")

    # 알림 발송
    _send_postmortem_notification_from_task(
        settings=settings,
        postmortem=postmortem,
        incident_id=incident_id,
        service_name=service_name,
        duration=duration,
        affected_services=affected,
    )

    # WAL Audit 기록
    try:
        from selfhealing.services.audit.base import _write_to_wal

        _write_to_wal(
            event_type="POSTMORTEM_AUTO_GENERATED",
            source="CeleryTask.ProcessIndividualPostmortem",
            details={
                "incident_id": incident_id,
                "service_name": service_name,
                "duration_seconds": duration,
                "affected_services": affected,
                "trigger_event": event_data.get("event_type", "circuit_breaker_closed"),
            },
            success=True,
            domain="selfhealing",
            target_id=incident_id,
        )
    except Exception as audit_error:
        logger.warning(f"[ProcessIndividualPostmortem] Failed to log audit: {audit_error}")

    return {
        "success": True,
        "service_name": service_name,
        "incident_id": incident_id,
        "duration_seconds": duration,
    }


def _process_emergency_postmortem(
    service_name: str,
    event_data: dict,
    event_bus_history: list[dict],
    web_server_metrics: dict | None = None,
) -> dict[str, Any]:
    """Emergency 복구 완료 시 Postmortem 생성 (Celery Worker에서 실행)."""
    from selfhealing.api.django.views.xtest.base import collect_system_snapshot
    from selfhealing.services.event_bus.bus import (
        _generate_emergency_postmortem_data,
    )
    from selfhealing.services.postmortem_store import add_healing_incident
    from selfhealing.settings.postmortem import get_postmortem_settings

    settings = get_postmortem_settings()
    session_id = event_data.get("data", {}).get("session_id", "unknown")
    namespace = event_data.get("data", {}).get("namespace", "global")
    trigger_level = event_data.get("data", {}).get("trigger_level", "UNKNOWN")
    duration = event_data.get("data", {}).get("duration_seconds")

    # 스냅샷 수집
    snapshot = collect_system_snapshot()

    if web_server_metrics:
        # Worker 원본 값을 별도 필드로 보존
        snapshot["worker_cpu_percent"] = snapshot.get("cpu_percent")
        snapshot["worker_memory_percent"] = snapshot.get("memory_percent")
        snapshot["worker_memory_used_mb"] = snapshot.get("memory_used_mb")
        snapshot["worker_memory_available_mb"] = snapshot.get("memory_available_mb")
        # 주 필드를 Web Server 값으로 교체
        snapshot["cpu_percent"] = web_server_metrics.get("cpu_percent", snapshot["cpu_percent"])
        snapshot["memory_percent"] = web_server_metrics.get("memory_percent", snapshot["memory_percent"])
        snapshot["memory_used_mb"] = web_server_metrics.get("memory_used_mb", snapshot.get("memory_used_mb", 0))
        snapshot["memory_available_mb"] = web_server_metrics.get("memory_available_mb", snapshot.get("memory_available_mb", 0))
        snapshot["snapshot_source"] = "web_server_cache+worker"
        snapshot["snapshot_note"] = "주 CPU/Memory=Web Server 캐시, worker_*=Celery Worker 측정값."
    else:
        snapshot["snapshot_source"] = "celery_worker"
        snapshot["snapshot_note"] = "Worker 노드의 CPU/Memory. Web Server와 다를 수 있음."

    # Emergency Postmortem 데이터 생성
    postmortem = _generate_emergency_postmortem_data(
        session_data=event_data.get("data", {}),
        event_bus_history=event_bus_history,
        snapshot=snapshot,
    )

    # 저장
    add_healing_incident(postmortem)

    incident_id = postmortem.get("incident_id")
    logger.info(
        f"[ProcessIndividualPostmortem] Emergency postmortem generated: {incident_id} "
        f"(session={session_id}, level={trigger_level}, duration={duration}s)"
    )

    # WAL Audit 기록
    try:
        from selfhealing.services.audit.base import _write_to_wal

        _write_to_wal(
            event_type="EMERGENCY_POSTMORTEM_AUTO_GENERATED",
            source="CeleryTask.ProcessIndividualPostmortem",
            details={
                "incident_id": incident_id,
                "session_id": session_id,
                "namespace": namespace,
                "trigger_level": trigger_level,
                "duration_seconds": duration,
                "requires_approval": event_data.get("data", {}).get("requires_approval", False),
                "approved_by": event_data.get("data", {}).get("approved_by"),
            },
            success=True,
            domain="selfhealing",
            target_id=incident_id,
        )
    except Exception as audit_error:
        logger.warning(f"[ProcessIndividualPostmortem] Failed to log emergency audit: {audit_error}")

    # 알림 발송
    try:
        _send_postmortem_notification_from_task(
            settings=settings,
            postmortem=postmortem,
            incident_id=incident_id,
            service_name=service_name,
            duration=duration,
            affected_services=[],
        )
    except Exception as notify_error:
        logger.warning(f"[ProcessIndividualPostmortem] Failed to send emergency notification: {notify_error}")

    return {
        "success": True,
        "service_name": service_name,
        "incident_id": incident_id,
        "duration_seconds": duration,
        "trigger_level": trigger_level,
    }


def _send_postmortem_notification_from_task(
    settings,
    postmortem: dict,
    incident_id: str,
    service_name: str,
    duration: int | None,
    affected_services: list[str],
) -> None:
    """Celery Task 내에서 Postmortem 알림 발송."""
    try:
        if not settings.notification_enabled:
            logger.debug(f"[ProcessIndividualPostmortem] Notification disabled for {incident_id}")
            return

        notification_min_duration = settings.notification_min_duration
        if duration is not None and duration < notification_min_duration:
            logger.debug(
                f"[ProcessIndividualPostmortem] Notification skipped for {incident_id}: "
                f"duration {duration}s < min {notification_min_duration}s"
            )
            return

        from selfhealing.services.unified_notification import (
            NotificationCategory,
            NotificationPayload,
            NotificationPriority,
            UnifiedNotificationManager,
        )

        # 우선순위 결정: 5분 이상 또는 3개 이상 서비스 영향 → HIGH
        affected_count = len(affected_services) if affected_services else 0
        if (duration is not None and duration >= 300) or affected_count >= 3:
            priority = NotificationPriority.HIGH
        else:
            priority = NotificationPriority.MEDIUM

        # 알림 본문 생성
        resolved_at = postmortem.get("resolved_at", "N/A")
        started_at = postmortem.get("started_at", "N/A")
        recommendations = postmortem.get("recommendations", [])
        recommendations_summary = ", ".join(recommendations[:3]) if recommendations else "없음"

        message = (
            f"인시던트 시작: {started_at}\n"
            f"인시던트 종료: {resolved_at}\n"
            f"지속 시간: {duration}초\n"
            f"영향 서비스: {', '.join(affected_services) if affected_services else '없음'}\n"
            f"권장 조치: {recommendations_summary}"
        )

        payload = NotificationPayload(
            title=f"📋 Post-mortem 생성: {incident_id}",
            message=message,
            priority=priority,
            category=NotificationCategory.OPERATIONS,
            source="CeleryTask.ProcessIndividualPostmortem",
            metadata={
                "incident_id": incident_id,
                "service_name": service_name,
                "duration_seconds": duration,
                "affected_services": affected_services,
                "resolved_at": resolved_at,
                "postmortem_url": f"/api/xtest/incidents/{incident_id}/",
            },
            dedup_key=f"postmortem:{incident_id}",
        )

        manager = UnifiedNotificationManager()
        result = manager.notify(payload)

        if result.success and not result.suppressed:
            logger.info(f"[ProcessIndividualPostmortem] Notification sent for {incident_id}")
        elif result.suppressed:
            logger.debug(
                f"[ProcessIndividualPostmortem] Notification suppressed for {incident_id}: " f"{result.suppression_reason}"
            )

    except Exception as e:
        logger.warning(f"[ProcessIndividualPostmortem] Failed to send notification: {e}")
