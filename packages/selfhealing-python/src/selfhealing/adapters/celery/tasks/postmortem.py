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
            logger.debug("[FlushNotifications] Aggregation disabled")
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
