"""
317: run_forecaster_cycle Celery 태스크 단위 테스트.

테스트 대상:
- 태스크 데코레이터 메타데이터 (name, queue, time_limit 등)
- 설정 비활성화 시 스킵 동작
- 정상 실행 흐름 (forecast_and_detect 호출)
- forecast_and_detect 개별 실패 시 graceful 처리
- ImportError 시 Fail-Open 동작
- 일반 예외 시 에러 반환
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# =============================================================================
# Contract: 태스크 메타데이터
# =============================================================================


class TestForecasterTaskMetadataContract:
    """run_forecaster_cycle 태스크 데코레이터 계약값 검증."""

    def test_task_name_matches_contract(self):
        """태스크 이름: selfhealing.celery_tasks.run_forecaster_cycle."""
        from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

        assert (
            run_forecaster_cycle.name == "selfhealing.celery_tasks.run_forecaster_cycle"
        )

    def test_task_queue_is_monitoring(self):
        """태스크 큐: monitoring."""
        from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

        assert run_forecaster_cycle.queue == "monitoring"

    def test_task_max_retries_is_one(self):
        """최대 재시도: 1회."""
        from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

        assert run_forecaster_cycle.max_retries == 1

    def test_task_time_limit_is_60(self):
        """하드 타임아웃: 60초."""
        from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

        assert run_forecaster_cycle.time_limit == 60

    def test_task_soft_time_limit_is_55(self):
        """소프트 타임아웃: 55초."""
        from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

        assert run_forecaster_cycle.soft_time_limit == 55


# =============================================================================
# Behavior: 실행 흐름
# =============================================================================


class TestForecasterTaskBehavior:
    """run_forecaster_cycle 동작 검증."""

    def test_disabled_settings_returns_skipped(self):
        """forecaster 비활성화 시 skipped 반환."""
        mock_settings = MagicMock()
        mock_settings.enabled = False

        with patch(
            "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
            return_value=mock_settings,
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is True
        assert result["skipped"] is True
        assert result["reason"] == "forecaster_disabled"

    def test_enabled_settings_calls_forecast_and_detect(self):
        """활성화 시 3개 메트릭에 대해 forecast_and_detect 호출."""
        mock_settings = MagicMock()
        mock_settings.enabled = True

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"predicted": 100.0}
        mock_service.forecast_and_detect.return_value = mock_result

        with (
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.services.predictive_forecaster.service.PredictiveForecasterService",
                return_value=mock_service,
            ),
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is True
        assert result["forecasts"] == 3
        assert mock_service.forecast_and_detect.call_count == 3

        called_metrics = [
            call.args[0] for call in mock_service.forecast_and_detect.call_args_list
        ]
        assert "rps" in called_metrics
        assert "error_rate" in called_metrics
        assert "p99_latency_ms" in called_metrics

    def test_forecast_none_result_not_counted(self):
        """forecast_and_detect가 None 반환 시 결과에 포함하지 않음."""
        mock_settings = MagicMock()
        mock_settings.enabled = True

        mock_service = MagicMock()
        mock_service.forecast_and_detect.return_value = None

        with (
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.services.predictive_forecaster.service.PredictiveForecasterService",
                return_value=mock_service,
            ),
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is True
        assert result["forecasts"] == 0

    def test_individual_forecast_failure_does_not_abort(self):
        """개별 메트릭 예측 실패 시 나머지 메트릭은 계속 처리."""
        mock_settings = MagicMock()
        mock_settings.enabled = True

        call_count = 0

        def side_effect(metric_name):
            nonlocal call_count
            call_count += 1
            if metric_name == "error_rate":
                raise RuntimeError("forecast failed")
            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"predicted": 1.0}
            return mock_result

        mock_service = MagicMock()
        mock_service.forecast_and_detect.side_effect = side_effect

        with (
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.services.predictive_forecaster.service.PredictiveForecasterService",
                return_value=mock_service,
            ),
            patch("selfhealing.celery_tasks.forecaster_tasks.logger"),
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is True
        assert result["forecasts"] == 2
        assert call_count == 3

    def test_import_error_returns_skipped(self):
        """모듈 import 실패 시 skipped 반환 (Fail-Open)."""
        with patch(
            "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
            side_effect=ImportError("no module"),
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is True
        assert result["skipped"] is True
        assert result["reason"] == "module_not_available"

    def test_generic_exception_returns_failure(self):
        """일반 예외 시 success=False 반환."""
        mock_settings = MagicMock()
        mock_settings.enabled = True

        with (
            patch(
                "selfhealing.settings.predictive_forecaster.get_predictive_forecaster_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.services.predictive_forecaster.service.PredictiveForecasterService",
                side_effect=RuntimeError("unexpected"),
            ),
            patch("selfhealing.celery_tasks.forecaster_tasks.logger"),
        ):
            from selfhealing.celery_tasks.forecaster_tasks import run_forecaster_cycle

            result = run_forecaster_cycle()

        assert result["success"] is False
        assert "unexpected" in result["error"]
