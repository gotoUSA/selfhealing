"""
Emergency Postmortem 자동 생성 핸들러.

이 모듈은 selfhealing.services.event_bus.bus 패키지의 내부 구현입니다.
"""

from __future__ import annotations

from typing import Any

import structlog

from . import EventType, SelfHealingEvent

logger = structlog.get_logger()


def _handle_incident_group(event: SelfHealingEvent, service_name: str, settings) -> None:
    """
    CB CLOSED 이벤트를 IncidentGroup에 추가.

    새 그룹 생성 시 종료 타이머를 스케줄링합니다.
    """
    from selfhealing.services.postmortem.incident_group import (
        get_incident_group_manager,
    )

    manager = get_incident_group_manager()
    namespace = event.data.get("namespace", "default")

    # 그룹에 인시던트 추가
    group_id, is_new_group = manager.add_incident(
        service_name=service_name,
        event=event,
        namespace=namespace,
    )

    if is_new_group:
        # 새 그룹 생성 시 종료 타이머 스케줄링
        _schedule_group_close(group_id, namespace, settings)
        logger.info(
            "event_handler.new_incident_group_created",
            group_id=group_id,
            service_name=service_name,
        )
    else:
        logger.info(
            "event_handler.incident_added_existing_group",
            group_id=group_id,
            service_name=service_name,
        )


def _schedule_group_close(group_id: str, namespace: str, settings) -> None:
    """그룹 종료 Celery 태스크 스케줄링."""
    try:
        from selfhealing.adapters.celery.tasks import close_incident_group

        window_seconds = getattr(settings, "incident_group_window_seconds", 600)

        # 윈도우 종료 후 그룹 종료 태스크 실행
        close_incident_group.apply_async(
            kwargs={
                "group_id": group_id,
                "namespace": namespace,
            },
            countdown=window_seconds,
        )

        logger.debug(
            "event_handler.scheduled_group_close",
            group_id=group_id,
            window_seconds=window_seconds,
        )

    except ImportError:
        logger.debug("event_handler.celery_tasks_available_skipping")
    except Exception as e:
        logger.warning(
            "event_handler.failed_schedule_group_close",
            error=e,
        )


def _create_individual_postmortem(
    event: SelfHealingEvent,
    service_name: str,
    settings,
    min_duration: int,
    history_limit: int,
) -> None:
    """개별 Post-mortem 생성 (그룹핑 비활성화 또는 Fallback 시)."""
    try:
        from selfhealing.api.django.views.xtest.base import (
            collect_system_snapshot,
            get_healing_events,
        )
        from selfhealing.services.circuit_breaker_service import (
            get_circuit_breaker_service,
        )
        from selfhealing.services.postmortem_store import (
            add_healing_incident,
        )
        from selfhealing.services.postmortem_store import (
            build_timeline as _build_timeline,
        )
        from selfhealing.services.postmortem_store import (
            collect_service_states as _collect_service_states,
        )
        from selfhealing.services.postmortem_store import (
            generate_postmortem_data as _generate_postmortem_data,
        )

        from . import get_event_bus

        # 히스토리 및 상태 수집
        bus = get_event_bus()
        history = bus.get_history(limit=history_limit)
        cb_service = get_circuit_breaker_service()
        affected, unaffected = _collect_service_states(cb_service)
        local_events = get_healing_events(20)
        timeline = _build_timeline(history, local_events)
        snapshot = collect_system_snapshot()

        # Fast fail 카운트
        fast_fail_count = len([e for e in history if e.get("data", {}).get("fast_fail")])

        # 인시던트 ID 생성
        from django.utils import timezone

        incident_id = f"AUTO-{service_name}-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

        # Post-mortem 생성
        postmortem = _generate_postmortem_data(incident_id, timeline, affected, unaffected, fast_fail_count, snapshot)

        # 최소 duration 확인
        duration = postmortem.get("duration_seconds")
        if duration is not None and duration < min_duration:
            logger.debug(
                "event_handler.auto_postmortem_skipped_duration",
                service_name=service_name,
                duration=duration,
                min_duration=min_duration,
            )
            return

        # 무결성 봉인
        try:
            from selfhealing.services.postmortem.integrity_sealer import (
                get_integrity_sealer,
            )

            sealer = get_integrity_sealer()
            postmortem = sealer.seal(postmortem)
        except Exception as seal_error:
            logger.warning(
                "event_handler.integrity_seal_failed",
                seal_error=seal_error,
            )

        # 저장
        add_healing_incident(postmortem)

        logger.info(
            "event_handler.auto_postmortem_generated",
            incident_id=incident_id,
            duration=duration,
        )

        # Post-mortem 알림 발송
        from ._cb_handlers import _send_postmortem_notification

        _send_postmortem_notification(settings, postmortem, incident_id, service_name, duration, affected)

        # WAL Audit 기록 - 자동 Post-mortem 생성 이벤트
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="POSTMORTEM_AUTO_GENERATED",
                source="EventHandler.Postmortem",
                details={
                    "incident_id": incident_id,
                    "service_name": service_name,
                    "duration_seconds": duration,
                    "affected_services": affected,
                    "trigger_event": event.event_type.value,
                },
                success=True,
                domain="selfhealing",
                target_id=incident_id,
            )
        except Exception as audit_error:
            logger.warning(
                "event_handler.failed_log_postmortem_audit",
                audit_error=audit_error,
            )

    except ImportError:
        logger.debug("event_handler.postmortem_module_available_skipping")
    except Exception as e:
        logger.exception(
            "event_handler.failed_generate_auto_postmortem",
            error=e,
        )


def _build_emergency_timeline(event_bus_history: list) -> list:
    """Emergency 관련 타임라인 구성."""
    timeline = []
    emergency_event_types = [
        "emergency_activated",
        "emergency_recovery_started",
        "emergency_recovery_completed",
        "emergency_level_changed",
    ]

    for event in event_bus_history:
        event_type = event.get("event_type", "").lower()
        if any(etype in event_type for etype in emergency_event_types):
            timeline.append(
                {
                    "timestamp": event.get("timestamp"),
                    "event_type": event.get("event_type"),
                    "details": event.get("data", {}),
                }
            )

    # CB 이벤트도 포함 (Emergency 중 발생한 것)
    cb_events = [e for e in event_bus_history if "circuit_breaker" in e.get("event_type", "").lower()]
    for event in cb_events[:10]:
        timeline.append(
            {
                "timestamp": event.get("timestamp"),
                "event_type": event.get("event_type"),
                "details": event.get("data", {}),
            }
        )

    # 시간순 정렬
    timeline.sort(key=lambda x: x.get("timestamp", ""), reverse=False)
    return timeline


def _build_recovery_steps(steps_executed: int) -> list:
    """복구 단계 정보 추출."""
    step_types = ["BUDGET_RESET", "HEALTH_CHECK", "CANARY_RESUME", "GOVERNANCE_NORMAL"]
    recovery_steps = []
    for i in range(min(steps_executed, len(step_types))):
        recovery_steps.append(
            {
                "step_order": i + 1,
                "step_type": step_types[i] if i < len(step_types) else f"STEP_{i+1}",
                "status": "COMPLETED",
            }
        )
    return recovery_steps


def _build_emergency_actions(
    trigger_level: str,
    steps_executed: int,
    requires_approval: bool,
    approved_by: str | None,
) -> tuple[list, list]:
    """Emergency 동적 Action Items 및 권장사항 생성."""
    auto_actions = []
    recommendations = []

    if trigger_level == "LEVEL_3":
        auto_actions.append(
            {
                "action": "GOVERNANCE_NORMALIZED",
                "description": "자동화 재활성화 (STRICT → NORMAL)",
                "status": "completed",
            }
        )
        recommendations.append("LEVEL_3 장애 원인 분석 및 재발 방지 대책 수립")

    if steps_executed > 0:
        auto_actions.append(
            {
                "action": "BUDGET_RESET",
                "description": "Crisis Multiplier 정상화 (1.0x)",
                "status": "completed",
            }
        )

    if requires_approval:
        auto_actions.append(
            {
                "action": "MANUAL_APPROVAL",
                "description": f"수동 승인 완료 (승인자: {approved_by or 'unknown'})",
                "status": "completed",
            }
        )
        recommendations.append("수동 승인 프로세스 검토 및 자동화 가능 여부 평가")

    recommendations.append(f"Emergency {trigger_level} 발생 원인 분석")
    recommendations.append("복구 프로세스 시간 단축 방안 검토")

    return auto_actions, recommendations


def _collect_emergency_cascade_event_data(namespace: str) -> tuple[str | None, list[str], str | None]:
    """Emergency CascadeEvent 감사 증적 수집."""
    cascade_event_id = None
    causation_chain: list[str] = []
    evidence_hash = None
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()
        recent_events = auditor.get_recent_events(namespace=namespace, limit=50)

        for event in recent_events:
            if "EMERGENCY" in event.trigger.trigger_type:
                cascade_event_id = event.id
                causation_chain = event.get_causation_chain()
                evidence_hash = event.current_hash
                break
    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "failed_collect_cascade_event",
            error=e,
        )
    return cascade_event_id, causation_chain, evidence_hash


def _build_emergency_deep_links(
    incident_id: str,
    namespace: str,
    started_at: str | None,
    completed_at: str | None,
    cascade_event_id: str | None,
    evidence_hash: str | None,
) -> dict:
    """Emergency 딥링크 생성."""
    try:
        from selfhealing.services.postmortem.deep_links import (
            get_postmortem_deep_link_builder,
        )

        deep_link_builder = get_postmortem_deep_link_builder()
        postmortem_links = deep_link_builder.build_postmortem_links(
            incident_id=incident_id,
            service_name=f"emergency-{namespace}",
            start_time=started_at,
            end_time=completed_at,
            namespace=namespace,
            cascade_event_id=cascade_event_id,
            evidence_hash=evidence_hash,
        )
        return postmortem_links.to_dict()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(
            "failed_build_deep_links",
            error=e,
        )
    return {}


def _generate_emergency_postmortem_data(
    session_data: dict,
    event_bus_history: list,
    snapshot: dict,
) -> dict:
    """
    Emergency 복구 완료 시 Postmortem 데이터 생성.

    CB Postmortem과 달리 Emergency Postmortem은 리전/글로벌 장애에 대한
    복구 세션 정보를 기반으로 생성됩니다.

    Args:
        session_data: EMERGENCY_RECOVERY_COMPLETED 이벤트에서 전달된 세션 정보
        event_bus_history: EventBus 히스토리
        snapshot: 시스템 스냅샷

    Returns:
        Emergency Postmortem 데이터 딕셔너리
    """
    from datetime import datetime
    from datetime import timezone as dt_timezone

    # 세션 데이터 추출
    session_id = session_data.get("session_id", "unknown")
    namespace = session_data.get("namespace", "global")
    trigger_level = session_data.get("trigger_level", "UNKNOWN")
    started_at = session_data.get("started_at")
    completed_at = session_data.get("completed_at")
    duration_seconds = session_data.get("duration_seconds")
    steps_executed = session_data.get("steps_executed", 0)
    total_steps = session_data.get("total_steps", 0)
    requires_approval = session_data.get("requires_approval", False)
    approved_by = session_data.get("approved_by")

    now = datetime.now(dt_timezone.utc)
    current_time = now.isoformat()
    incident_id = f"EMERGENCY-{namespace}-{now.strftime('%Y%m%d-%H%M%S')}"

    # 헬퍼 함수들을 사용하여 데이터 수집
    timeline = _build_emergency_timeline(event_bus_history)
    recovery_steps = _build_recovery_steps(steps_executed)
    auto_actions, recommendations = _build_emergency_actions(trigger_level, steps_executed, requires_approval, approved_by)
    cascade_event_id, causation_chain, evidence_hash = _collect_emergency_cascade_event_data(namespace)
    deep_links = _build_emergency_deep_links(incident_id, namespace, started_at, completed_at, cascade_event_id, evidence_hash)

    return {
        "incident_id": incident_id,
        "generated_at": current_time,
        "started_at": started_at,
        "resolved_at": completed_at,
        "duration_seconds": duration_seconds,
        # Emergency 전용 필드
        "recovery_type": "emergency",
        "namespace": namespace,
        "trigger_level": trigger_level,
        "recovery_session_id": session_id,
        "recovery_steps": recovery_steps,
        "requires_approval": requires_approval,
        "approved_by": approved_by,
        # 공통 필드
        "summary": {
            "affected_services": [],
            "unaffected_services": [],
            "fast_fail_count": 0,
            "total_events": len(timeline),
            "steps_executed": steps_executed,
            "total_steps": total_steps,
        },
        "timeline": timeline[:30],
        "system_snapshot": snapshot,
        "auto_actions": auto_actions,
        "recommendations": recommendations,
        "deep_links": deep_links,
        "cascade_event_id": cascade_event_id,
        "causation_chain": causation_chain,
        "evidence_hash": evidence_hash,
    }


def _on_emergency_recovery_completed_postmortem(event: SelfHealingEvent):
    """
    Emergency 복구 완료 시 Postmortem 생성을 Celery Task로 위임.

    RecoveryCoordinator가 복구를 완료하면 EMERGENCY_RECOVERY_COMPLETED 이벤트가
    발행되고, 스냅샷 수집/DB 저장/WAL 기록/알림 발송을 Celery Worker에 위임한다.

    Settings 검증과 min_duration 체크만 동기로 수행 (빠름).
    Celery 미설치 환경에서는 기존 동기 방식으로 자동 fallback.
    """
    session_id = event.data.get("session_id", "unknown")
    namespace = event.data.get("namespace", "global")
    trigger_level = event.data.get("trigger_level", "UNKNOWN")
    duration = event.data.get("duration_seconds")

    # Settings에서 자동 생성 활성화 여부 확인
    try:
        from selfhealing.settings.postmortem import get_postmortem_settings

        settings = get_postmortem_settings()

        if not settings.auto_enabled:
            logger.debug(
                "event_handler.auto_postmortem_disabled_skipping",
                session_id=session_id,
            )
            return

        min_duration = settings.auto_min_duration
        history_limit = settings.history_limit
    except Exception as e:
        logger.warning(
            "event_handler.failed_get_postmortem_settings",
            error=e,
        )
        return

    # 최소 duration 확인 (빠른 체크, I/O 없음)
    if duration is not None and duration < min_duration:
        logger.debug(
            "event_handler.emergency_postmortem_skipped_duration",
            session_id=session_id,
            duration=duration,
            min_duration=min_duration,
        )
        return

    # Celery Task로 위임
    try:
        from selfhealing.adapters.celery.tasks import process_individual_postmortem

        from . import get_event_bus
        from ._cb_handlers import _collect_web_server_metrics

        # bus.get_history()는 프로세스 로컬 인메모리이므로 여기서 수집
        bus = get_event_bus()
        event_bus_history = bus.get_history(limit=history_limit)

        web_metrics = _collect_web_server_metrics()

        process_individual_postmortem.delay(
            service_name=f"emergency-{namespace}",
            event_data=event.to_dict(),
            event_type="emergency_recovery_completed",
            event_bus_history=event_bus_history,
            web_server_metrics=web_metrics,
        )
    except ImportError:
        # Celery 미설치 환경: 기존 동기 방식 fallback
        _create_emergency_postmortem_sync(event, namespace, history_limit)
    except Exception as e:
        logger.warning(
            "event_handler.failed_enqueue_emergency_postmortem",
            error=e,
        )


def _create_emergency_postmortem_sync(
    event: SelfHealingEvent,
    namespace: str,
    history_limit: int,
) -> None:
    """Emergency Postmortem 동기 생성 (Celery 미설치 환경 Fallback)."""
    session_id = event.data.get("session_id", "unknown")
    trigger_level = event.data.get("trigger_level", "UNKNOWN")
    duration = event.data.get("duration_seconds")

    try:
        from selfhealing.api.django.views.xtest.base import collect_system_snapshot
        from selfhealing.services.postmortem_store import add_healing_incident

        from . import get_event_bus

        # 히스토리 및 스냅샷 수집
        bus = get_event_bus()
        history = bus.get_history(limit=history_limit)
        snapshot = collect_system_snapshot()

        # Emergency Postmortem 데이터 생성
        postmortem = _generate_emergency_postmortem_data(
            session_data=event.data,
            event_bus_history=history,
            snapshot=snapshot,
        )

        # 저장
        add_healing_incident(postmortem)

        incident_id = postmortem.get("incident_id")
        logger.info(
            "event_handler.emergency_postmortem_generated",
            incident_id=incident_id,
            session_id=session_id,
            trigger_level=trigger_level,
            duration=duration,
        )

        # WAL Audit 기록
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type="EMERGENCY_POSTMORTEM_AUTO_GENERATED",
                source="EventHandler.EmergencyPostmortem",
                details={
                    "incident_id": incident_id,
                    "session_id": session_id,
                    "namespace": namespace,
                    "trigger_level": trigger_level,
                    "duration_seconds": duration,
                    "requires_approval": event.data.get("requires_approval", False),
                    "approved_by": event.data.get("approved_by"),
                },
                success=True,
                domain="selfhealing",
                target_id=incident_id,
            )
        except Exception as audit_error:
            logger.warning(
                "event_handler.failed_log_emergency_postmortem",
                audit_error=audit_error,
            )

        # Postmortem 알림 발송
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            from ._cb_handlers import _send_postmortem_notification

            settings = get_postmortem_settings()
            _send_postmortem_notification(
                settings=settings,
                postmortem=postmortem,
                incident_id=incident_id,
                service_name=f"emergency-{namespace}",
                duration=duration,
                affected_services=[],
            )
        except Exception as notify_error:
            logger.warning(
                "event_handler.failed_send_emergency_postmortem",
                notify_error=notify_error,
            )

    except ImportError as e:
        logger.debug(
            "event_handler.module_available_emergency_postmortem",
            error=e,
        )
    except Exception as e:
        logger.exception(
            "event_handler.failed_generate_emergency_postmortem",
            error=e,
        )
