"""
Circuit Breaker 알림 및 Postmortem 핸들러.

이 모듈은 selfhealing.services.event_bus.bus 패키지의 내부 구현입니다.
"""

from __future__ import annotations

import structlog

from . import SelfHealingEvent

logger = structlog.get_logger()


def _on_circuit_breaker_opened_notify(event: SelfHealingEvent) -> None:
    """
    CB OPEN 시 알림 발송을 Celery Task로 위임.

    Slack Webhook HTTP 호출 등 네트워크 I/O를 포함한 알림 발송을
    Celery Worker에서 비동기로 처리하여 발행자 스레드 차단을 제거한다.
    Celery 미설치 환경에서는 ImportError fallback으로 안전하게 스킵한다.
    """
    try:
        from selfhealing.adapters.celery.tasks import send_cb_open_notification

        send_cb_open_notification.delay(
            service_name=event.data.get("service_name", "unknown"),
            trace_id=event.data.get("trace_id"),
            trace_url=event.data.get("trace_url"),
            timestamp=event.data.get("timestamp", ""),
        )
    except ImportError:
        logger.debug("event_handler.celery_tasks_available_skipping")
    except Exception as e:
        logger.warning(
            "notification.failed_enqueue_cb_notification",
            error=e,
        )


def _collect_web_server_metrics() -> dict | None:
    """Web Server의 캐시된 시스템 메트릭을 수집 (~0ms). 실패 시 None 반환."""
    try:
        from selfhealing.services.system_metrics_cache import get_system_metrics_cache

        cache = get_system_metrics_cache()
        if cache.is_running():
            return cache.get_snapshot_dict()
    except Exception:
        pass
    return None


def _on_circuit_breaker_opened_snapshot(event: SelfHealingEvent) -> None:
    """
    CB OPEN 시 시스템 스냅샷 수집을 Celery Task로 위임.

    psutil.cpu_percent(interval=0.1)의 100ms 블로킹과 Redis HSET를
    Celery Worker에서 비동기로 처리하여 발행자 스레드 차단을 제거한다.
    Celery 미설치 환경에서는 ImportError fallback으로 안전하게 스킵한다.
    """
    service_name = event.data.get("service_name", "unknown")
    try:
        from selfhealing.adapters.celery.tasks import collect_cb_open_snapshot

        web_metrics = _collect_web_server_metrics()

        collect_cb_open_snapshot.delay(
            service_name=service_name,
            event_timestamp=event.timestamp.isoformat(),
            web_server_metrics=web_metrics,
        )
    except ImportError:
        logger.debug("event_handler.celery_tasks_available_skipping")
    except Exception as e:
        logger.warning(
            "event_handler.failed_enqueue_cb_snapshot",
            error=e,
        )


def _send_postmortem_notification(
    settings,
    postmortem: dict,
    incident_id: str,
    service_name: str,
    duration: int | None,
    affected_services: list[str],
) -> None:
    """
    Post-mortem 생성 완료 알림 발송.

    Settings에서 notification_enabled가 True이고,
    duration이 notification_min_duration 이상인 경우에만 발송합니다.

    알림 우선순위 결정:
    - duration >= 300초 (5분) 또는 affected_services >= 3: HIGH
    - 그 외: MEDIUM
    """
    try:
        # 알림 활성화 여부 확인
        if not settings.notification_enabled:
            logger.debug(
                "notification.postmortem_notification_disabled",
                incident_id=incident_id,
            )
            return

        # 최소 duration 확인
        notification_min_duration = settings.notification_min_duration
        if duration is not None and duration < notification_min_duration:
            logger.debug(
                "notification.postmortem_notification_skipped_duration",
                incident_id=incident_id,
                duration=duration,
                notification_min_duration=notification_min_duration,
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
            source="EventHandler.Postmortem",
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
            logger.info(
                "notification.postmortem_notification_sent",
                incident_id=incident_id,
            )
        elif result.suppressed:
            logger.debug(
                "notification.postmortem_notification_suppressed",
                incident_id=incident_id,
                result=result.suppression_reason,
            )

    except Exception as e:
        # 알림 실패가 시스템에 영향을 주지 않도록 함
        logger.warning(
            "notification.failed_send_postmortem_notification",
            error=e,
        )


def _on_circuit_breaker_closed(event: SelfHealingEvent):
    """
    CB 복구 시 자동 Replay 트리거 (Track 1).

    CRITICAL 우선순위의 PostRecoveryIntegrityGate
    (integrity_gate.py)가 이 핸들러보다 먼저 실행되어
    event.data[INTEGRITY_FAILED_KEY] 플래그를 설정합니다.
    플래그가 True인 경우 리플레이를 차단합니다.

    RuntimeConfig에서 track1_enabled 설정을 확인하고,
    활성화된 경우 conditional_replay_on_circuit_close 태스크를 트리거합니다.
    """
    service_name = event.data.get("service_name", "unknown")

    # IntegrityGate 결과 확인 (상수 import로 오타 방지)
    try:
        from selfhealing.services.event_bus.integrity_gate import INTEGRITY_FAILED_KEY

        if event.data.get(INTEGRITY_FAILED_KEY, False):
            logger.critical(
                "event_handler.replay_blocked_integrity_gate",
                service_name=service_name,
                _event=event.data.get('integrity_gate_result', {}),
            )
            return  # 리플레이 중단
    except ImportError:
        pass  # integrity_gate 모듈 미설치 시 무시

    # RuntimeConfig에서 replay_automation 설정 로드
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager

        manager = get_runtime_config_manager()
        config = manager._get_config("replay_automation")
    except Exception as e:
        logger.warning(
            "event_handler.failed_get_config",
            error=e,
        )
        config = {}

    # Track 1 활성화 여부 확인 (기본값: True)
    track1_enabled = config.get("track1_enabled", True)

    if not track1_enabled:
        logger.info(
            "event_handler.circuit_breaker_closed_track",
            service_name=service_name,
        )
        return

    max_items = config.get("track1_max_items", 50)

    # Celery 태스크 트리거
    try:
        from selfhealing.adapters.celery.tasks import (
            conditional_replay_on_circuit_close,
        )

        conditional_replay_on_circuit_close.delay(
            service_name=service_name,
            max_items=max_items,
        )
        logger.info(
            "event_handler.circuit_breaker_closed_triggered",
            service_name=service_name,
            max_items=max_items,
        )
    except ImportError:
        logger.debug(
            "event_handler.celery_tasks_available_skipping",
            service_name=service_name,
        )
    except Exception as e:
        logger.exception(
            "event_handler.failed_trigger_track_replay",
            service_name=service_name,
            error=e,
        )


def _on_circuit_breaker_closed_postmortem(event: SelfHealingEvent):
    """
    CB 복구 시 자동 Post-mortem 생성.

    Settings에서 auto_enabled가 True인 경우에만 동작합니다.
    incident_group_enabled가 True이면 IncidentGroupManager를 통해 그룹화합니다.
    그룹화된 경우 그룹 종료 시 통합 Postmortem이 생성됩니다.
    """
    service_name = event.data.get("service_name", "unknown")

    # Settings에서 자동 생성 활성화 여부 확인
    try:
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.auto_enabled:
            logger.debug(
                "event_handler.auto_postmortem_disabled_skipping",
                service_name=service_name,
            )
            return

        min_duration = settings.auto_min_duration
        history_limit = settings.history_limit

        # 인시던트 그룹핑 활성화 여부 확인
        incident_group_enabled = getattr(settings, "incident_group_enabled", True)
    except Exception as e:
        logger.warning(
            "event_handler.failed_get_postmortem_settings",
            error=e,
        )
        return

    # 인시던트 그룹핑 처리
    if incident_group_enabled:
        try:
            from ._emergency_postmortem import _handle_incident_group

            _handle_incident_group(event, service_name, settings)
            return  # 그룹핑 시 즉시 Postmortem 생성 안함 (close_incident_group task에서 처리)
        except Exception as e:
            logger.warning(
                "event_handler.incident_grouping_failed_fallback",
                error=e,
            )
            # Fallback: Celery task로 개별 Postmortem 위임

    # 개별 Post-mortem 생성을 Celery task로 위임
    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        from . import get_event_bus

        # bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        web_metrics = _collect_web_server_metrics()

        # event.to_dict()로 직렬화 — Celery JSON serializer 호환
        process_individual_postmortem.delay(
            service_name=service_name,
            event_data=event.to_dict(),
            event_type="circuit_breaker_closed",
            event_bus_history=event_bus_history,
            web_server_metrics=web_metrics,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback
        from ._emergency_postmortem import _create_individual_postmortem

        _create_individual_postmortem(event, service_name, settings, min_duration, history_limit)
    except Exception as e:
        logger.warning(
            "event_handler.failed_enqueue_postmortem",
            error=e,
        )
