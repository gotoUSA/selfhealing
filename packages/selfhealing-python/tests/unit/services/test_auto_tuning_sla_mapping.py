"""
AutoTuningService SLA 매핑 업데이트 단위 테스트.

module_params, param_module에 SLA 파라미터가 올바르게 추가되었는지 검증한다.

테스트 분류:
- 계약 검증 (Contract): SLA 파라미터 → rate_limit 모듈 매핑 설계 계약
- 동작 검증 (Behavior): 활성화/비활성화 상태 전환 동작
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


# =============================================================================
# 계약 검증 (Contract Tests) — SLA 파라미터 → 모듈 매핑 설계 계약
# =============================================================================


class TestAutoTuningSlaContract:
    """SLA 파라미터 모듈 매핑 설계 계약 검증 (하드코딩 허용)."""

    def test_sla_warning_maps_to_rate_limit(self, auto_tuning_service):
        """throttle_sla_warning_ms → rate_limit 모듈 매핑 (설계 계약)."""
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_warning_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

    def test_sla_critical_maps_to_rate_limit(self, auto_tuning_service):
        """throttle_sla_critical_ms → rate_limit 모듈 매핑 (설계 계약)."""
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_critical_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

    def test_legacy_rate_limit_rps_maps_to_rate_limit(self, auto_tuning_service):
        """rate_limit_rps → rate_limit 모듈 매핑 (하위 호환 계약)."""
        auto_tuning_service._disable_parameter_auto_tuning("rate_limit_rps", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED


# =============================================================================
# 동작 검증 (Behavior Tests) — 활성화/비활성화 상태 전환 동작
# =============================================================================


class TestAutoTuningSlaModuleBehavior:
    """SLA 파라미터 모듈 활성화/비활성화 동작 검증."""

    def test_rate_limit_module_lookup_runs_without_error(self, auto_tuning_service):
        """rate_limit 모듈의 마지막 조정 시간 조회가 에러 없이 실행되어야 한다."""
        result = auto_tuning_service._get_last_module_adjustment("rate_limit")
        assert result is None  # 레코드 없음이지만 에러 없이 실행

    def test_enable_after_disable_restores_state(self, auto_tuning_service):
        """비활성화 후 활성화하면 ENABLED 상태로 복원되어야 한다."""
        auto_tuning_service._disable_parameter_auto_tuning("throttle_sla_warning_ms", None)
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.DISABLED

        auto_tuning_service._enable_parameter_auto_tuning("throttle_sla_warning_ms")
        assert auto_tuning_service._module_states["rate_limit"] == ModuleState.ENABLED
