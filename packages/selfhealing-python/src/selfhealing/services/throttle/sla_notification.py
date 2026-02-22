"""
SLA 위반 시 UnifiedNotification 비동기 연동 모듈.

EventBus SLA 이벤트를 구독하여 Celery 비동기 태스크로 알림을 위임합니다.
Celery 미사용 환경에서는 동기 폴백으로 동작합니다.
리전 정보 자동 주입, 서비스 단위 dedup_key 세분화를 제공합니다.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def _get_region_safe() -> str | None:
    """
    현재 클러스터의 리전 정보를 안전하게 가져옵니다.

    환경변수 SELFHEALING_NAMESPACE_REGION에서 읽으며,
    region이 None이면 단일 리전 배포입니다.
    """
    try:
        from selfhealing.core.cluster_identity import get_cluster_identity

        identity = get_cluster_identity(skip_validation=True)
        return identity.region
    except ImportError:
        logger.debug("sla_notification.clusteridentity_available")
        return None
    except Exception as e:
        logger.debug(
            "sla_notification.failed_get_region",
            error=e,
        )
        return None


def _subscribe_sla_events() -> None:
    """
    SLA 이벤트 구독 등록.

    애플리케이션 시작 시 호출 필요.
    """
    try:
        from selfhealing.services.event_bus import EventType, get_event_bus

        bus = get_event_bus()

        bus.subscribe(
            EventType.THROTTLE_SLA_WARNING,
            _handle_sla_warning,
        )
        bus.subscribe(
            EventType.THROTTLE_SLA_CRITICAL,
            _handle_sla_critical,
        )
        bus.subscribe(
            EventType.THROTTLE_LIMIT_RECOVERED,
            _handle_limit_recovered,
        )

        logger.info("sla_notification.subscribed_throttle_sla_events")
    except ImportError:
        logger.debug("sla_notification.eventbus_available")
    except Exception as e:
        logger.warning(
            "sla_notification.failed_subscribe",
            error=e,
        )


def _handle_sla_warning(event) -> None:
    """
    SLA Warning 이벤트 처리.

    Celery 사용 환경에서는 비동기 위임, 미사용 시 동기 폴백.
    """
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "warning",
            },
        )
    except ImportError:
        logger.debug("sla_notification.celery_available_using_sync")
        _send_sla_warning_sync(event.data)
    except Exception as e:
        logger.warning(
            "sla_notification.failed_dispatch_warning",
            error=e,
        )
        _send_sla_warning_sync(event.data)


def _handle_sla_critical(event) -> None:
    """SLA Critical 이벤트 처리."""
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "critical",
            },
        )
    except ImportError:
        logger.debug("sla_notification.celery_available_using_sync")
        _send_sla_critical_sync(event.data)
    except Exception as e:
        logger.warning(
            "sla_notification.failed_dispatch_critical",
            error=e,
        )
        _send_sla_critical_sync(event.data)


def _handle_limit_recovered(event) -> None:
    """Limit 복구 이벤트 처리."""
    try:
        from selfhealing.adapters.celery.tasks import send_sla_notification

        send_sla_notification.apply_async(
            kwargs={
                "event_data": event.data,
                "notification_type": "recovered",
            },
        )
    except ImportError:
        _send_limit_recovered_sync(event.data)
    except Exception:
        _send_limit_recovered_sync(event.data)


def _send_sla_warning_sync(event_data: dict[str, Any]) -> None:
    """
    SLA Warning 동기 전송.

    리전 자동 주입 + 서비스 단위 dedup_key 세분화 포함.
    """
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_warning_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")
        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        gradient = event_data.get("gradient", 0)
        rtt_change_percent = event_data.get("rtt_change_percent")

        template = build_sla_warning_message(
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            current_limit=current_limit,
            previous_limit=previous_limit,
            gradient=gradient,
            rtt_change_percent=rtt_change_percent,
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            # dedup_key 서비스 단위 세분화: "sla:throttle:{service_name}"
            domain=f"throttle:{service_name}",
            priority="high",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
            },
        )

        if result.success:
            logger.info(
                "sla_notification.warning_sent",
                result=result.channels_sent,
            )
        elif result.suppressed:
            logger.debug(
                "sla_notification.warning_suppressed",
                result=result.suppression_reason,
            )
        else:
            logger.warning(
                "sla_notification.warning_failed",
                result=result.error,
            )

    except ImportError:
        logger.debug("sla_notification.unifiednotification_available")
    except Exception as e:
        logger.warning(
            "sla_notification.failed_send_warning",
            error=e,
        )


def _send_sla_critical_sync(event_data: dict[str, Any]) -> None:
    """SLA Critical 동기 전송."""
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_critical_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")
        rtt_ms = event_data.get("current_rtt_ms", 0)
        threshold_ms = event_data.get("threshold_ms", 0)
        current_limit = event_data.get("current_limit", 0)
        previous_limit = event_data.get("previous_limit", 0)
        reduction_percent = event_data.get("reduction_percent", 0)
        gradient = event_data.get("gradient", 0)
        rtt_change_percent = event_data.get("rtt_change_percent")

        template = build_sla_critical_message(
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            current_limit=current_limit,
            previous_limit=previous_limit,
            reduction_percent=reduction_percent,
            gradient=gradient,
            rtt_change_percent=rtt_change_percent,
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            domain=f"throttle:{service_name}",
            priority="critical",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
                "requires_action": True,
            },
        )

        if result.success:
            logger.warning(
                "sla_notification.critical_sent",
                result=result.channels_sent,
            )
        else:
            logger.error(
                "sla_notification.critical_failed",
                result=result.error,
            )

    except ImportError:
        logger.debug("sla_notification.unifiednotification_available")
    except Exception as e:
        logger.exception(
            "sla_notification.failed_send_critical",
            error=e,
        )


def _send_limit_recovered_sync(event_data: dict[str, Any]) -> None:
    """Limit 복구 동기 전송."""
    try:
        from selfhealing.services.throttle.sla_notification_templates import (
            build_sla_recovered_message,
        )
        from selfhealing.services.unified_notification import notify_sla

        region = _get_region_safe()
        service_name = event_data.get("service_name", "default")

        template = build_sla_recovered_message(
            previous_limit=event_data.get("previous_limit", 0),
            new_limit=event_data.get("new_limit", 0),
            rtt_ms=event_data.get("rtt_ms", 0),
            region=region,
            service_name=service_name,
        )

        result = notify_sla(
            title=template["title"],
            message=template["message"],
            domain=f"throttle:{service_name}",
            priority="medium",
            source="adaptive_throttle",
            metadata={
                **template["details"],
                "region": region,
                "service_name": service_name,
            },
        )

        if result.success:
            logger.info(
                "sla_notification.recovery_sent",
                result=result.channels_sent,
            )

    except ImportError:
        logger.debug("sla_notification.unifiednotification_available")
    except Exception as e:
        logger.debug(
            "sla_notification.failed_send_recovery",
            error=e,
        )


# 모듈 초기화 시 자동 구독
def initialize_sla_notifications() -> None:
    """SLA 알림 시스템 초기화."""
    _subscribe_sla_events()
