"""
Config Shadow Celery Tasks.

Shadow Evaluation 비동기 실행을 Celery 태스크로 래핑한다.
"""

from __future__ import annotations

import structlog
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    name="selfhealing.tasks.config_shadow.run_shadow_evaluation",
    max_retries=1,
    default_retry_delay=30,
    acks_late=True,
)
def run_shadow_evaluation(self, evaluation_id: str) -> dict:
    """Shadow Evaluation을 비동기로 실행한다."""
    try:
        from selfhealing.services.config_shadow import (
            get_shadow_evaluator_service,
        )

        service = get_shadow_evaluator_service()
        evaluation = service.execute_evaluation(evaluation_id)
        return {
            "evaluation_id": evaluation.evaluation_id,
            "status": evaluation.status.value,
            "passed": evaluation.report.passed if evaluation.report else None,
        }
    except Exception as exc:
        logger.error(
            "config_shadow.task_failed",
            evaluation_id=evaluation_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)
