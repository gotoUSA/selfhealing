"""
Throttle EventBus 연동 핸들러.

이 모듈은 selfhealing.services.event_bus.bus 패키지의 내부 구현입니다.
"""

from __future__ import annotations

import structlog
from typing import Any

from . import SelfHealingEvent, EventType

logger = structlog.get_logger()


def _on_emergency_level_changed_throttle(event: SelfHealingEvent) -> None:
    """
    Emergency 레벨 변경 시 Throttle limit 자동 조정.

    AdaptiveThrottle.adjust_for_emergency() 메서드를 호출하여
    Emergency Level에 따른 limit 배율 및 Gradient Freeze를 처리합니다.

    Emergency Level별 limit 배율:
    - NORMAL (0): 1.0 (전체 용량, 복구)
    - LEVEL_1 (1): 0.8 (80% 용량)
    - LEVEL_2 (2): 0.5 (50% 용량)
    - LEVEL_3 (3): min_limit 고정 + Gradient Freeze
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    level = event.data.get("level", 0)
    previous_level = event.data.get("previous_level", 0)

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # adjust_for_emergency 메서드로 통합 처리
        throttle.adjust_for_emergency(level)

        logger.info(
            f"[Throttle] Emergency level {previous_level} → {level}, " f"limit: {previous_limit} → {throttle.current_limit}"
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_emergency",
            error=e,
        )


def _on_emergency_deactivated_throttle(event: SelfHealingEvent) -> None:
    """
    Emergency 비활성화 시 Throttle limit 복구.

    Emergency Mode가 완전히 비활성화될 때 호출됩니다.
    adjust_for_emergency(0)을 호출하여 limit을 복구합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # level 0으로 복구
        throttle.adjust_for_emergency(0)

        logger.info(
            "throttle.emergency_deactivated_limit_restored",
            previous_limit=previous_limit,
            throttle=throttle.current_limit,
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_restore_throttle_after",
            error=e,
        )


def _on_circuit_breaker_opened_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker OPEN 시 해당 서비스 limit을 min_limit으로 고정.

    CB가 열리면 해당 서비스가 불안정한 상태이므로
    Throttle limit을 min_limit으로 즉시 강등합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import (
            get_adaptive_throttle,
            _record_throttle_metrics,
            _record_audit_safe,
        )

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # min_limit으로 강등
        throttle.current_limit = throttle.config.min_limit

        # CB 조정 메트릭 기록
        _record_throttle_metrics(
            service=service_name,
            limit=throttle.current_limit,
            cb_state="open",
        )

        # CB 조정 감사 로그 기록
        _record_audit_safe(
            action="throttle_cb_sync",
            old_limit=previous_limit,
            new_limit=throttle.current_limit,
            service_name=service_name,
            cb_state="open",
        )

        logger.info(
            "throttle.cb_open_limit",
            service_name=service_name,
            previous_limit=previous_limit,
            throttle=throttle.current_limit,
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_cb",
            error=e,
        )


def _on_circuit_breaker_half_opened_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker HALF_OPEN 시 limit을 initial_limit의 50%로 설정.

    CB가 OPEN에서 HALF_OPEN으로 전이되면 서비스 복구를 테스트하는 단계이므로
    제한적인 트래픽만 허용합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # initial_limit × 0.5 (제한적 트래픽 허용)
        half_open_limit = int(throttle.config.initial_limit * 0.5)
        throttle.current_limit = half_open_limit

        # CB 조정 메트릭 기록
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            _record_throttle_metrics(
                service=service_name,
                limit=throttle.current_limit,
                cb_state="half_open",
            )
        except ImportError:
            pass

        logger.info(
            f"[Throttle] CB HALF_OPEN for {service_name}, "
            f"limit: {previous_limit} → {throttle.current_limit} (recovery test mode)"
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_cb",
            error=e,
        )


def _on_circuit_breaker_closed_throttle(event: SelfHealingEvent) -> None:
    """
    Circuit Breaker CLOSED 시 limit 제한 해제.

    CB가 닫히면 서비스가 정상화되었으므로
    limit 제한을 해제하고 점진적 복구를 시작합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    service_name = event.data.get("service_name", "unknown")

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # initial_limit으로 복원 (점진적 증가 시작점)
        throttle.current_limit = throttle.config.initial_limit

        # CB 조정 메트릭 기록
        try:
            from selfhealing.services.throttle.adaptive import _record_throttle_metrics

            _record_throttle_metrics(
                service=service_name,
                limit=throttle.current_limit,
                cb_state="closed",
            )
        except ImportError:
            pass

        logger.info(
            f"[Throttle] CB CLOSED for {service_name}, " f"limit: {previous_limit} → {throttle.current_limit} (recovery mode)"
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_cb",
            error=e,
        )


def _on_error_budget_critical_throttle(event: SelfHealingEvent) -> None:
    """
    Error Budget Critical 시 limit을 보수적으로 조정 (×0.5).

    Error Budget이 임계치 이하로 떨어지면
    추가 오류 발생을 방지하기 위해 limit을 절반으로 줄입니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    budget_percent = event.data.get("budget_percent", 0)

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # limit × 0.5 (보수적 조정)
        new_limit = int(previous_limit * 0.5)
        throttle.current_limit = new_limit

        logger.warning(
            f"[Throttle] Error budget critical ({budget_percent:.1f}%), "
            f"limit: {previous_limit} → {throttle.current_limit} (×0.5)"
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_error",
            error=e,
        )


def _on_error_budget_recovered_throttle(event: SelfHealingEvent) -> None:
    """
    Error Budget 회복 시 Recovery Dampening 시작.

    즉시 initial_limit으로 복구하지 않고
    Recovery Dampening을 통해 점진적으로 복구합니다.
    이는 '요요 현상'을 방지하고 안정적인 복구를 보장합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()

        # Recovery Dampening 시작 (Jitter 적용)
        throttle.start_recovery_dampening(apply_jitter=True)

        logger.info(
            "throttle.error_budget_recovered_starting",
            throttle=throttle.current_limit,
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_start_recovery_dampening",
            error=e,
        )


def _on_kill_switch_activated_throttle(event: SelfHealingEvent) -> None:
    """
    Kill Switch 활성화 시 Throttle 기능 일시 중지.

    Kill Switch가 활성화되면 모든 자동화 기능이 중지되어야 하므로
    Throttle도 min_limit으로 고정합니다.
    """
    # 순환 참조 방지: 자기 이벤트 무시
    if event.source == "throttle":
        return

    try:
        from selfhealing.services.throttle.adaptive import get_adaptive_throttle

        throttle = get_adaptive_throttle()
        previous_limit = throttle.current_limit

        # min_limit으로 고정
        throttle.current_limit = throttle.config.min_limit

        logger.warning(
            "throttle.kill_switch_activated_limit",
            previous_limit=previous_limit,
            throttle=throttle.current_limit,
        )
    except ImportError:
        logger.debug("event_handler.throttle_module_available")
    except Exception as e:
        logger.warning(
            "event_handler.failed_adjust_throttle_kill",
            error=e,
        )
