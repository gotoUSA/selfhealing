"""
CanaryRolloutService._check_live_canary_evaluation() 티어별 하한 적용 테스트.

221 설계 §4.9/§8.2: evaluate 직전 apply_tier_floor() 호출 삽입.
tier_id 전달 시 사용자 PassCriteria에 티어 하한이 강제되는 동작 검증.

301 리팩토링: _is_stage_healthy → _check_live_canary_evaluation 전환.
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.canary.models import (
    CanaryRollout,
    CanaryStage,
    CanaryState,
)
from selfhealing.services.canary.service import CanaryRolloutService

# =============================================================================
# 동작 검증: promote() tier_id → _check_live_canary_evaluation 전파
# =============================================================================


class TestPromoteTierIdPropagationBehavior:
    """promote() → _check_live_canary_evaluation() tier_id 전파 동작."""

    @pytest.fixture
    def service(self):
        """CanaryRolloutService 인스턴스."""
        return CanaryRolloutService()

    def test_promote_passes_tier_id_to_live_canary_evaluation(self, service):
        """promote(tier_id=X) 호출 시 _check_live_canary_evaluation에 tier_id 전파."""
        rollout = CanaryRollout(
            id="test-rollout",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[
                CanaryStage(name="canary", clusters=["c1"], percentage=50.0),
                CanaryStage(name="full", clusters=["c1", "c2"], percentage=100.0),
            ],
        )

        with (
            patch.object(service, "get_rollout", return_value=rollout),
            patch.object(
                service,
                "_check_live_canary_evaluation",
                return_value=None,
            ) as mock_live,
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):
            service.promote("test-rollout", tier_id="critical")

            mock_live.assert_called_once()
            call_kwargs = mock_live.call_args
            assert call_kwargs.kwargs.get("tier_id") == "critical"

    def test_promote_without_tier_id_backward_compatible(self, service):
        """promote() tier_id 없이 호출 가능 (하위 호환)."""
        rollout = CanaryRollout(
            id="test-rollout",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[
                CanaryStage(name="canary", clusters=["c1"], percentage=50.0),
                CanaryStage(name="full", clusters=["c1", "c2"], percentage=100.0),
            ],
        )

        with (
            patch.object(service, "get_rollout", return_value=rollout),
            patch.object(
                service,
                "_check_live_canary_evaluation",
                return_value=None,
            ) as mock_live,
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):
            service.promote("test-rollout")

            mock_live.assert_called_once()
            call_kwargs = mock_live.call_args
            assert call_kwargs.kwargs.get("tier_id") is None


class TestPromoteLiveEvaluationBlockBehavior:
    """promote() Live Evaluation 차단 동작 검증."""

    @pytest.fixture
    def service(self):
        return CanaryRolloutService()

    def test_promote_blocked_when_live_evaluation_fails(self, service):
        """_check_live_canary_evaluation이 False 반환 시 promote 차단."""
        rollout = CanaryRollout(
            id="test-rollout",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[
                CanaryStage(name="canary", clusters=["c1"], percentage=50.0),
                CanaryStage(name="full", clusters=["c1", "c2"], percentage=100.0),
            ],
        )

        with (
            patch.object(service, "get_rollout", return_value=rollout),
            patch.object(
                service,
                "_check_live_canary_evaluation",
                return_value=False,
            ),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):
            result = service.promote("test-rollout")
            assert result is False

    def test_promote_proceeds_when_live_evaluation_returns_none(self, service):
        """_check_live_canary_evaluation이 None 반환 시 (비활성화) promote 진행."""
        rollout = CanaryRollout(
            id="test-rollout",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[
                CanaryStage(name="canary", clusters=["c1"], percentage=50.0),
                CanaryStage(name="full", clusters=["c1", "c2"], percentage=100.0),
            ],
        )

        with (
            patch.object(service, "get_rollout", return_value=rollout),
            patch.object(
                service,
                "_check_live_canary_evaluation",
                return_value=None,
            ),
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):
            result = service.promote("test-rollout")
            assert result is True

    def test_promote_force_skips_live_evaluation(self, service):
        """force=True 시 _check_live_canary_evaluation 미호출."""
        rollout = CanaryRollout(
            id="test-rollout",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[
                CanaryStage(name="canary", clusters=["c1"], percentage=50.0),
                CanaryStage(name="full", clusters=["c1", "c2"], percentage=100.0),
            ],
        )

        with (
            patch.object(service, "get_rollout", return_value=rollout),
            patch.object(
                service,
                "_check_live_canary_evaluation",
                return_value=False,
            ) as mock_live,
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):
            result = service.promote("test-rollout", force=True)
            assert result is True
            mock_live.assert_not_called()
