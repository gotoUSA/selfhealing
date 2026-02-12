"""
CanaryRolloutService._is_stage_healthy() 티어별 하한 적용 테스트.

221 설계 §4.9/§8.2: evaluate 직전 apply_tier_floor() 호출 삽입.
tier_id 전달 시 사용자 PassCriteria에 티어 하한이 강제되는 동작 검증.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.services.canary.models import (
    CanaryMetrics,
    CanaryStage,
    PassCriteria,
    apply_tier_floor,
)
from selfhealing.services.canary.service import CanaryRolloutService


# =============================================================================
# 동작 검증: _is_stage_healthy() tier_id 파라미터
# =============================================================================


class TestIsStageHealthyTierFloorBehavior:
    """_is_stage_healthy()에 tier_id 전달 시 apply_tier_floor 적용 동작."""

    @pytest.fixture
    def service(self):
        """CanaryRolloutService 인스턴스."""
        return CanaryRolloutService()

    @pytest.fixture
    def healthy_metrics(self):
        """기본 합격 메트릭 (모든 기준 통과)."""
        return [
            CanaryMetrics(
                cluster="canary-1",
                stage_name="canary",
                error_rate_before=0.01,
                error_rate_after=0.02,
                latency_p99_before=100.0,
                latency_p99_after=110.0,
                requests_total=200,
            )
        ]

    @pytest.fixture
    def stage_with_loose_criteria(self):
        """느슨한 사용자 PassCriteria가 설정된 스테이지."""
        return CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
            pass_criteria=PassCriteria(
                error_rate_absolute_max=0.10,  # 사용자: 느슨한 10%
                error_budget_drain_rate_max=3.0,
                error_budget_remaining_min=0.02,
            ),
        )

    def test_tier_id_none_uses_original_criteria(self, service, healthy_metrics):
        """tier_id=None이면 apply_tier_floor 미적용, 원본 criteria 사용."""
        stage = CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
            pass_criteria=PassCriteria(error_rate_absolute_max=0.10),
        )

        # error_rate_after=0.02 < 0.10이므로 통과
        is_healthy, reason = service._is_stage_healthy(
            stage,
            healthy_metrics,
            tier_id=None,
        )
        assert is_healthy is True
        assert reason is None

    def test_critical_tier_tightens_loose_criteria(
        self,
        service,
        stage_with_loose_criteria,
    ):
        """critical 티어 적용 시 느슨한 criteria가 강화되어 차단."""
        # critical 티어: error_rate_absolute_max=0.03
        # 메트릭: error_rate_after=0.04 → 느슨한 기준(0.10)은 통과하지만
        #         critical 하한(0.03)에 의해 차단되어야 함
        metrics = [
            CanaryMetrics(
                cluster="canary-1",
                stage_name="canary",
                error_rate_before=0.01,
                error_rate_after=0.04,  # 0.03 초과
                requests_total=200,
            )
        ]

        is_healthy, reason = service._is_stage_healthy(
            stage_with_loose_criteria,
            metrics,
            tier_id="critical",
        )
        assert is_healthy is False
        assert reason is not None

    def test_non_essential_tier_allows_higher_error_rate(self, service):
        """non_essential 티어: 높은 절대 에러율도 허용."""
        stage = CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
            pass_criteria=PassCriteria(
                error_rate_absolute_max=0.10,  # 사용자: 10%
                error_rate_increase_max=0.10,  # 증가 한계도 여유 있게
            ),
        )
        # non_essential: error_rate_absolute_max=0.10 → min(0.10, 0.10)=0.10
        # error_rate 증가분 = 0.08 - 0.07 = 0.01 ≤ 0.10
        metrics = [
            CanaryMetrics(
                cluster="canary-1",
                stage_name="canary",
                error_rate_before=0.07,
                error_rate_after=0.08,  # 10% 미만, 증가분 0.01
                requests_total=200,
            )
        ]

        is_healthy, reason = service._is_stage_healthy(
            stage,
            metrics,
            tier_id="non_essential",
        )
        assert is_healthy is True
        assert reason is None

    def test_no_stage_returns_healthy(self, service, healthy_metrics):
        """stage=None이면 건강 판정."""
        is_healthy, reason = service._is_stage_healthy(
            None,
            healthy_metrics,
            tier_id="critical",
        )
        assert is_healthy is True

    def test_no_metrics_returns_healthy(self, service, stage_with_loose_criteria):
        """메트릭 없으면 건강 판정 (샘플 부족)."""
        is_healthy, reason = service._is_stage_healthy(
            stage_with_loose_criteria,
            [],
            tier_id="critical",
        )
        assert is_healthy is True

    def test_apply_tier_floor_called_with_correct_args(self, service):
        """tier_id 설정 시 apply_tier_floor가 올바른 인자로 호출."""
        stage = CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
            pass_criteria=PassCriteria(error_rate_absolute_max=0.10),
        )
        metrics = [
            CanaryMetrics(
                cluster="canary-1",
                stage_name="canary",
                requests_total=200,
                error_rate_after=0.01,
            )
        ]

        with patch(
            "selfhealing.services.canary.models.apply_tier_floor",
            wraps=apply_tier_floor,
        ) as mock_floor:
            service._is_stage_healthy(stage, metrics, tier_id="critical")
            mock_floor.assert_called_once_with(stage.pass_criteria, "critical")

    def test_apply_tier_floor_not_called_without_tier_id(self, service):
        """tier_id=None이면 apply_tier_floor 미호출."""
        stage = CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
        )
        metrics = [
            CanaryMetrics(
                cluster="canary-1",
                stage_name="canary",
                requests_total=200,
                error_rate_after=0.01,
            )
        ]

        with patch(
            "selfhealing.services.canary.models.apply_tier_floor",
        ) as mock_floor:
            service._is_stage_healthy(stage, metrics, tier_id=None)
            mock_floor.assert_not_called()

    def test_backward_compatible_without_tier_id(self, service, healthy_metrics):
        """tier_id 생략 시 기존 동작 유지 (하위 호환)."""
        stage = CanaryStage(
            name="canary",
            clusters=["canary-1"],
            percentage=10.0,
        )
        # tier_id 파라미터 없이 호출 가능해야 함
        is_healthy, reason = service._is_stage_healthy(stage, healthy_metrics)
        assert is_healthy is True


# =============================================================================
# 동작 검증: promote() tier_id 전파
# =============================================================================


class TestPromoteTierIdPropagationBehavior:
    """promote() → _is_stage_healthy() tier_id 전파 동작."""

    @pytest.fixture
    def service(self):
        """CanaryRolloutService 인스턴스."""
        svc = CanaryRolloutService()
        return svc

    def test_promote_passes_tier_id_to_stage_health_check(self, service):
        """promote(tier_id=X) 호출 시 _is_stage_healthy에 tier_id 전파."""
        from selfhealing.services.canary.models import CanaryState, CanaryRollout

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
            patch.object(service, "_collect_stage_metrics", return_value=[]),
            patch.object(
                service,
                "_is_stage_healthy",
                return_value=(True, None),
            ) as mock_health,
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):

            service.promote("test-rollout", tier_id="critical")

            # _is_stage_healthy에 tier_id="critical"이 전달되어야 함
            mock_health.assert_called_once()
            call_kwargs = mock_health.call_args
            assert call_kwargs.kwargs.get("tier_id") == "critical"

    def test_promote_without_tier_id_backward_compatible(self, service):
        """promote() tier_id 없이 호출 가능 (하위 호환)."""
        from selfhealing.services.canary.models import CanaryState, CanaryRollout

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
            patch.object(service, "_collect_stage_metrics", return_value=[]),
            patch.object(
                service,
                "_is_stage_healthy",
                return_value=(True, None),
            ) as mock_health,
            patch.object(service, "_save_rollout"),
            patch("selfhealing.services.canary.service.log_canary_action"),
            patch(
                "selfhealing.services.governance_checks.check_all_governance",
                return_value=MagicMock(allowed=True),
            ),
        ):

            # tier_id 없이 호출
            service.promote("test-rollout")

            mock_health.assert_called_once()
            call_kwargs = mock_health.call_args
            assert call_kwargs.kwargs.get("tier_id") is None
