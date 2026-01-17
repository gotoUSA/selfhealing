"""
AutoTuningService Governance 통합 테스트.

AutoTuningService가 시작 전 Governance 체크를 수행하는지 검증합니다.
"""

import pytest
from unittest.mock import patch, MagicMock, Mock


class TestAutoTuningGovernanceIntegration:
    """AutoTuningService Governance 통합 테스트."""

    @pytest.fixture
    def mock_adapters(self):
        """테스트용 어댑터 mock 생성."""
        return {
            "metrics_adapter": MagicMock(),
            "config_provider": MagicMock(),
            "config_applier": MagicMock(),
            "audit_adapter": MagicMock(),
        }

    def test_start_blocked_by_kill_switch(self, mock_adapters):
        """Kill Switch 활성화 시 서비스 시작 차단."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            # Kill Switch로 차단
            mock_governance.return_value = GovernanceCheckResult.blocked_by_kill_switch()

            service = AutoTuningService(**mock_adapters)
            result = service.start()

            assert result is False
            mock_governance.assert_called_once()
            # governance 체크에서 auto_tuning 관련 파라미터 확인
            call_kwargs = mock_governance.call_args.kwargs
            assert call_kwargs["service_name"] == "auto_tuning"
            assert "service_start" in call_kwargs["operation_name"]

    def test_start_blocked_by_emergency_mode(self, mock_adapters):
        """Emergency Mode 중 서비스 시작 차단."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            # Emergency Mode로 차단
            mock_governance.return_value = GovernanceCheckResult.blocked_by_emergency(
                level_name="LEVEL_2",
                message="Emergency mode LEVEL_2 is active",
            )

            service = AutoTuningService(**mock_adapters)
            result = service.start()

            assert result is False
            mock_governance.assert_called_once()

    def test_start_blocked_by_error_budget(self, mock_adapters):
        """Error Budget 부족 시 서비스 시작 차단."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            # Error Budget으로 차단
            mock_governance.return_value = GovernanceCheckResult.blocked_by_error_budget(
                budget_percent=5.0,
                threshold_percent=10.0,
            )

            service = AutoTuningService(**mock_adapters)
            result = service.start()

            assert result is False

    def test_start_allowed_when_governance_passes(self, mock_adapters):
        """Governance 체크 통과 시 서비스 정상 시작."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            # Governance 통과
            mock_governance.return_value = GovernanceCheckResult.allowed_result()

            service = AutoTuningService(**mock_adapters)
            result = service.start()

            assert result is True
            mock_governance.assert_called_once()

    def test_check_governance_before_adjustment_method_exists(self, mock_adapters):
        """_check_governance_before_adjustment 메서드가 존재하는지 확인."""
        from selfhealing.services.auto_tuning.service import AutoTuningService

        service = AutoTuningService(**mock_adapters)
        
        assert hasattr(service, "_check_governance_before_adjustment")
        assert callable(service._check_governance_before_adjustment)

    def test_check_governance_params(self, mock_adapters):
        """Governance 체크 시 올바른 파라미터가 전달되는지 확인."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            mock_governance.return_value = GovernanceCheckResult.allowed_result()

            service = AutoTuningService(**mock_adapters)
            service.start()

            call_kwargs = mock_governance.call_args.kwargs
            assert call_kwargs["check_kill_switch"] is True
            assert call_kwargs["check_emergency"] is True
            assert call_kwargs["emergency_min_level"] == 2
            assert call_kwargs["check_error_budget"] is True
            assert call_kwargs["audit_on_block"] is True
            assert call_kwargs["service_name"] == "auto_tuning"
            assert call_kwargs["domain"] == "all"
            assert "auto_tuning:all:service_start" == call_kwargs["operation_name"]

    def test_governance_check_for_module_adjustment(self, mock_adapters):
        """모듈별 조정 시 Governance 체크 메서드 호출 확인."""
        from selfhealing.services.auto_tuning.service import AutoTuningService
        from selfhealing.services.governance_checks import GovernanceCheckResult

        with patch(
            "selfhealing.services.auto_tuning.service.check_all_governance"
        ) as mock_governance:
            mock_governance.return_value = GovernanceCheckResult.allowed_result()

            service = AutoTuningService(**mock_adapters)
            
            # _check_governance_before_adjustment 직접 호출 테스트
            result = service._check_governance_before_adjustment(
                module="circuit_breaker",
                adjustment_type="automatic",
            )

            assert result.allowed is True
            call_kwargs = mock_governance.call_args.kwargs
            assert call_kwargs["domain"] == "circuit_breaker"
            assert "circuit_breaker" in call_kwargs["operation_name"]
            assert "automatic" in call_kwargs["operation_name"]
