"""
Governance Celery Tasks - Emergency Mode Auto-Recovery.

긴급 모드 자동 복귀 및 알림 발송을 위한 Celery 태스크입니다.

Thin Task, Fat Service Architecture:
    - 이 파일의 Celery Task들은 단순 위임자 역할만 수행
    - 모든 비즈니스 로직은 GovernanceService에서 처리
    - 거버넌스 체크도 서비스 레이어에서 수행

Celery Beat 스케줄:
    - check_emergency_mode_expiry: 15분 주기

Usage:
    # settings.py 또는 celery.py에 추가
    CELERY_BEAT_SCHEDULE = {
        "check-emergency-mode-expiry": {
            "task": "selfhealing.tasks.governance.check_emergency_mode_expiry",
            "schedule": 900.0,  # 15분
        },
    }

Reference:
- docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md
- docs/self_healing/17_SYSTEM_ARCHITECTURE_DIAGRAM.md §8
"""

from __future__ import annotations

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def check_emergency_mode_expiry() -> Dict[str, Any]:
    """
    Check emergency mode expiry and perform auto-recovery if needed.

    This function is a thin wrapper that delegates to GovernanceService.
    All business logic is implemented in the service layer.

    This should be scheduled via Celery Beat (every 15 minutes).

    Actions (handled by GovernanceService):
    1. Check if emergency mode is active
    2. If 4 hours elapsed: Send warning to Admin
    3. If 6 hours elapsed: Send final warning ("2 hours until auto-restore")
    4. If 8 hours elapsed: Auto-restore to NORMAL mode

    Returns:
        dict: 실행 결과
    """
    from selfhealing.services.governance_service import get_governance_service

    service = get_governance_service()
    result = service.check_emergency_mode_expiry()

    return result.to_dict()


# =============================================================================
# Celery Beat Schedule Configuration
# =============================================================================


def get_governance_beat_schedule() -> Dict[str, Dict[str, Any]]:
    """
    Get Celery Beat schedule for governance tasks.

    Returns:
        dict: Celery Beat schedule configuration

    Usage:
        # In your celery.py or settings.py:
        from selfhealing.tasks.governance import get_governance_beat_schedule

        CELERY_BEAT_SCHEDULE.update(get_governance_beat_schedule())
    """
    return {
        "check-emergency-mode-expiry": {
            "task": "selfhealing.tasks.governance.check_emergency_mode_expiry",
            "schedule": 900.0,  # 15분 (900초)
            "options": {
                "queue": "governance",
                "priority": 3,  # High priority
            },
        },
    }


# =============================================================================
# Celery Task Registration (if using Celery)
# =============================================================================

try:
    from celery import shared_task

    @shared_task(
        name="selfhealing.tasks.governance.check_emergency_mode_expiry",
        bind=True,
        max_retries=3,
        default_retry_delay=60,
        autoretry_for=(Exception,),
        retry_backoff=True,
    )
    def check_emergency_mode_expiry_task(self) -> Dict[str, Any]:
        """
        Celery task wrapper for check_emergency_mode_expiry.

        This is the actual Celery task that should be scheduled.
        """
        return check_emergency_mode_expiry()

except ImportError:
    # Celery not installed, skip task registration
    logger.debug("[Governance] Celery not installed, skipping task registration")
    pass
