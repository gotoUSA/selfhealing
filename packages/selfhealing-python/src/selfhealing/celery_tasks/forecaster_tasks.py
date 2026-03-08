"""
317: Predictive Forecaster Celery Task.

메트릭 수집 + 예측 + 이상 탐지를 주기적으로 실행합니다.
"""

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(
    name="selfhealing.celery_tasks.run_forecaster_cycle",
    queue="monitoring",
    max_retries=1,
    time_limit=60,
    soft_time_limit=55,
)
def run_forecaster_cycle() -> dict:
    """주기적 예측 + 이상 탐지 사이클.

    collect_self_healing_metrics와 병렬로 60초마다 실행.
    수집된 시스템 메트릭을 PredictiveForecasterService에 주입하고
    forecast_and_detect를 실행합니다.
    """
    try:
        from selfhealing.settings.predictive_forecaster import (
            get_predictive_forecaster_settings,
        )

        settings = get_predictive_forecaster_settings()
        if not getattr(settings, "enabled", False):
            return {"success": True, "skipped": True, "reason": "forecaster_disabled"}

        from selfhealing.services.predictive_forecaster.service import (
            PredictiveForecasterService,
        )

        service = PredictiveForecasterService()

        results = {}
        for metric_name in ("rps", "error_rate", "p99_latency_ms"):
            try:
                result = service.forecast_and_detect(metric_name)
                if result is not None:
                    results[metric_name] = result.to_dict()
            except Exception as e:
                logger.warning(
                    "forecaster_tasks.forecast_failed",
                    metric_name=metric_name,
                    error=e,
                )

        return {"success": True, "forecasts": len(results)}

    except ImportError:
        logger.debug("forecaster_tasks.module_not_available")
        return {"success": True, "skipped": True, "reason": "module_not_available"}
    except Exception as e:
        logger.exception("forecaster_tasks.cycle_failed", error=e)
        return {"success": False, "error": str(e)}
