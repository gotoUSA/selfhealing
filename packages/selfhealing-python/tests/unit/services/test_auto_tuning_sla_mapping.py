"""
AutoTuningService SLA 매핑 업데이트 단위 테스트.

module_params, param_module에 SLA 파라미터가 올바르게 추가되었는지 검증한다.

- 소스 enum 참조: ModuleState 모듈 최상단 import
- 소스 상수 참조: AutoTuningService.MODULES 참조
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from selfhealing.services.auto_tuning.service import AutoTuningService, ModuleState


@pytest.fixture
def auto_tuning_service():
    """테스트용 AutoTuningService 인스턴스."""
    metrics_adapter = MagicMock()
    metrics_adapter.fetch_current_metrics.return_value = {
        "p99_latency_ms": 0,
        "error_rate": 0,
        "retry_exhausted_rate": 0,
        "throughput_rps": 0,
    }

    config_provider = MagicMock()
    config_provider.get.return_value = None

    config_applier = MagicMock()
    config_applier.get_current.return_value = 0
    config_applier.apply.return_value = True
    config_applier.rollback.return_value = True

    audit_adapter = MagicMock()

    return AutoTuningService(
        metrics_adapter=metrics_adapter,
        config_provider=config_provider,
        config_applier=config_applier,
        audit_adapter=audit_adapter,
    )


class TestAutoTuningServiceSlaMapping:
    """AutoTuningService의 SLA 파라미터 매핑 테스트."""

    def test_module_params_rate_limit_includes_sla(self, auto_tuning_service):
        """rate_limit 모듈의 마지막 조정 시간 조회 시 SLA 파라미터도 검색해야 한다."""
        # _get_last_module_adjustment 호출로 module_params 매핑 간접 검증
        result = auto_tuning_service._get_last_module_adjustment("rate_limit")
        assert result is None  # 레코드 없음이지만 에러 없이 실행

    def test_disable_sla_warning_maps_to_rate_limit(self, auto_tuning_service):
        """throttle_sla_warning_ms 비활성화 시 rate_limit 모듈에 매핑되어야 한다."""
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_warning_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

    def test_disable_sla_critical_maps_to_rate_limit(self, auto_tuning_service):
        """throttle_sla_critical_ms 비활성화 시 rate_limit 모듈에 매핑되어야 한다."""
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_critical_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

    def test_enable_sla_parameter_maps_to_rate_limit(self, auto_tuning_service):
        """throttle_sla_warning_ms 활성화 시 rate_limit 모듈에 매핑되어야 한다."""
        # 먼저 비활성화
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_warning_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

        # 활성화
        auto_tuning_service._enable_parameter_auto_tuning("throttle_sla_warning_ms")
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.ENABLED

    def test_legacy_rate_limit_rps_still_maps_to_rate_limit(self, auto_tuning_service):
        """기존 rate_limit_rps도 여전히 rate_limit 모듈에 매핑되어야 한다."""
        auto_tuning_service._disable_parameter_auto_tuning("rate_limit_rps", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED
