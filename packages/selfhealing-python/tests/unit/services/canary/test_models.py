"""
Canary Rollout Data Models 단위 테스트.

테스트 대상:
1. CanaryState enum - 상태 값 및 문자열 변환
2. PassCriteria - 합격 기준 평가 로직
3. CanaryStage - 단계 정의 및 기본값
4. CanaryMetrics - 메트릭 데이터 구조
5. CanaryRollout - 롤아웃 속성 및 계산된 프로퍼티

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

from datetime import datetime

import pytest

from selfhealing.services.canary.models import (
    CanaryMetrics,
    CanaryRollout,
    CanaryStage,
    CanaryState,
    PassCriteria,
)

# =============================================================================
# Test: CanaryState enum
# =============================================================================


class TestCanaryState:
    """CanaryState enum 테스트."""

    def test_state_values(self):
        """모든 상태 값이 올바른지 확인."""
        assert CanaryState.CREATED.value == "created"
        assert CanaryState.CANARY.value == "canary"
        assert CanaryState.PROMOTING.value == "promoting"
        assert CanaryState.PAUSED.value == "paused"
        assert CanaryState.COMPLETED.value == "completed"
        assert CanaryState.ROLLED_BACK.value == "rolled_back"
        assert CanaryState.FAILED.value == "failed"
        assert CanaryState.CANCELLED.value == "cancelled"

    def test_state_is_string_enum(self):
        """CanaryState가 str, Enum 모두 상속하는지 확인."""
        assert isinstance(CanaryState.CREATED, str)
        assert CanaryState.CREATED == "created"

    def test_state_from_string(self):
        """문자열에서 상태 생성 가능한지 확인."""
        state = CanaryState("canary")
        assert state == CanaryState.CANARY

    def test_invalid_state_raises_error(self):
        """유효하지 않은 상태 값은 에러를 발생시키는지 확인."""
        with pytest.raises(ValueError):
            CanaryState("invalid_state")


# =============================================================================
# Test: PassCriteria
# =============================================================================


class TestPassCriteria:
    """PassCriteria 합격 기준 테스트."""

    @pytest.fixture
    def default_criteria(self):
        """기본 PassCriteria 인스턴스."""
        return PassCriteria()

    @pytest.fixture
    def healthy_metrics(self):
        """건강한 상태의 메트릭."""
        return CanaryMetrics(
            cluster="seoul",
            stage_name="canary",
            error_rate_before=0.01,
            error_rate_after=0.015,  # 0.5% 증가 (1% 한계 미만)
            latency_p99_before=100.0,
            latency_p99_after=110.0,  # 10% 증가 (20% 한계 미만)
            requests_total=150,  # 최소 100 이상
        )

    @pytest.fixture
    def unhealthy_error_rate_metrics(self):
        """에러율 초과 메트릭."""
        return CanaryMetrics(
            cluster="seoul",
            stage_name="canary",
            error_rate_before=0.01,
            error_rate_after=0.08,  # 8% (5% 한계 초과)
            latency_p99_before=100.0,
            latency_p99_after=100.0,
            requests_total=150,
        )

    @pytest.fixture
    def unhealthy_latency_metrics(self):
        """레이턴시 초과 메트릭."""
        return CanaryMetrics(
            cluster="seoul",
            stage_name="canary",
            error_rate_before=0.01,
            error_rate_after=0.015,
            latency_p99_before=100.0,
            latency_p99_after=130.0,  # 30% 증가 (20% 한계 초과)
            requests_total=150,
        )

    def test_default_values(self, default_criteria):
        """기본값이 문서와 일치하는지 확인."""
        assert default_criteria.error_rate_absolute_max == 0.05
        assert default_criteria.error_rate_increase_max == 0.01
        assert default_criteria.latency_p95_delta_ms == 50.0
        assert default_criteria.latency_p99_delta_pct == 0.2
        assert default_criteria.error_budget_drain_rate_max == 1.2
        assert default_criteria.error_budget_remaining_min == 0.1
        assert default_criteria.min_requests_required == 100
        assert default_criteria.evaluation_window_seconds == 300

    def test_custom_criteria_values(self):
        """커스텀 기준값으로 생성 가능."""
        criteria = PassCriteria(
            error_rate_absolute_max=0.10,
            min_requests_required=50,
        )
        assert criteria.error_rate_absolute_max == 0.10
        assert criteria.min_requests_required == 50


# =============================================================================
# Test: CanaryStage
# =============================================================================


class TestCanaryStage:
    """CanaryStage 단계 정의 테스트."""

    def test_stage_creation_with_required_fields(self):
        """필수 필드로 생성 가능."""
        stage = CanaryStage(
            name="canary",
            clusters=["seoul-canary"],
            percentage=10.0,
        )
        assert stage.name == "canary"
        assert stage.clusters == ["seoul-canary"]
        assert stage.percentage == 10.0

    def test_stage_default_values(self):
        """기본값이 올바르게 설정되는지 확인."""
        stage = CanaryStage(
            name="test",
            clusters=["cluster-a"],
            percentage=50.0,
        )
        assert stage.duration_minutes == 5
        assert stage.auto_promote is True
        assert isinstance(stage.pass_criteria, PassCriteria)
        assert stage.error_rate_threshold == 0.05
        assert stage.latency_increase_threshold == 0.5

    def test_stage_with_custom_pass_criteria(self):
        """커스텀 PassCriteria로 생성 가능."""
        criteria = PassCriteria(error_rate_absolute_max=0.10)
        stage = CanaryStage(
            name="relaxed",
            clusters=["test"],
            percentage=100.0,
            pass_criteria=criteria,
        )
        assert stage.pass_criteria.error_rate_absolute_max == 0.10

    def test_stage_with_multiple_clusters(self):
        """여러 클러스터를 포함할 수 있음."""
        stage = CanaryStage(
            name="full",
            clusters=["tokyo", "singapore", "sydney"],
            percentage=100.0,
        )
        assert len(stage.clusters) == 3
        assert "tokyo" in stage.clusters


# =============================================================================
# Test: CanaryMetrics
# =============================================================================


class TestCanaryMetrics:
    """CanaryMetrics 메트릭 데이터 테스트."""

    def test_metrics_creation_with_required_fields(self):
        """필수 필드로 생성 가능."""
        metrics = CanaryMetrics(
            cluster="seoul",
            stage_name="canary",
        )
        assert metrics.cluster == "seoul"
        assert metrics.stage_name == "canary"

    def test_metrics_default_values(self):
        """기본값이 올바르게 설정되는지 확인."""
        metrics = CanaryMetrics(
            cluster="tokyo",
            stage_name="50%",
        )
        assert metrics.error_rate_before == 0.0
        assert metrics.error_rate_after == 0.0
        assert metrics.latency_p50_before == 0.0
        assert metrics.latency_p99_after == 0.0
        assert metrics.requests_total == 0
        assert metrics.errors_total == 0
        assert metrics.is_healthy is True
        assert metrics.unhealthy_reason is None

    def test_metrics_with_full_data(self):
        """모든 필드를 설정할 수 있음."""
        metrics = CanaryMetrics(
            cluster="seoul",
            stage_name="canary",
            error_rate_before=0.01,
            error_rate_after=0.02,
            latency_p50_before=50.0,
            latency_p50_after=55.0,
            latency_p99_before=100.0,
            latency_p99_after=120.0,
            requests_total=1000,
            errors_total=20,
            is_healthy=False,
            unhealthy_reason="Error rate increased",
        )
        assert metrics.error_rate_after == 0.02
        assert metrics.is_healthy is False


# =============================================================================
# Test: CanaryRollout
# =============================================================================


class TestCanaryRollout:
    """CanaryRollout 롤아웃 정보 테스트."""

    @pytest.fixture
    def sample_stages(self):
        """샘플 단계 목록."""
        return [
            CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10.0),
            CanaryStage(name="50%", clusters=["seoul-main"], percentage=50.0),
            CanaryStage(name="full", clusters=["tokyo", "singapore"], percentage=100.0),
        ]

    @pytest.fixture
    def sample_rollout(self, sample_stages):
        """샘플 롤아웃 객체."""
        return CanaryRollout(
            id="abc12345",
            config_type="circuit_breaker",
            previous_values={"failure_threshold": 5},
            new_values={"failure_threshold": 3},
            stages=sample_stages,
            created_by="admin@example.com",
            reason="Reduce failure threshold for faster detection",
        )

    def test_rollout_creation_with_required_fields(self):
        """필수 필드로 생성 가능."""
        rollout = CanaryRollout(
            id="test123",
            config_type="dlq",
            previous_values={"max_retries": 3},
            new_values={"max_retries": 5},
        )
        assert rollout.id == "test123"
        assert rollout.config_type == "dlq"
        assert rollout.state == CanaryState.CREATED

    def test_rollout_default_values(self, sample_rollout):
        """기본값이 올바르게 설정되는지 확인."""
        assert sample_rollout.state == CanaryState.CREATED
        assert sample_rollout.current_stage_index == 0
        assert sample_rollout.completed_at is None
        assert sample_rollout.rollback_reason is None
        assert isinstance(sample_rollout.created_at, datetime)

    def test_current_stage_property_returns_correct_stage(self, sample_rollout):
        """current_stage 프로퍼티가 올바른 단계를 반환."""
        assert sample_rollout.current_stage.name == "canary"

        sample_rollout.current_stage_index = 1
        assert sample_rollout.current_stage.name == "50%"

        sample_rollout.current_stage_index = 2
        assert sample_rollout.current_stage.name == "full"

    def test_current_stage_returns_none_for_invalid_index(self, sample_rollout):
        """유효하지 않은 인덱스에서는 None 반환."""
        sample_rollout.current_stage_index = -1
        assert sample_rollout.current_stage is None

        sample_rollout.current_stage_index = 10
        assert sample_rollout.current_stage is None

    def test_affected_clusters_property(self, sample_rollout):
        """affected_clusters가 현재까지 적용된 클러스터를 반환."""
        # 인덱스 0: canary 단계만
        sample_rollout.current_stage_index = 0
        assert sample_rollout.affected_clusters == ["seoul-canary"]

        # 인덱스 1: canary + 50%
        sample_rollout.current_stage_index = 1
        assert sample_rollout.affected_clusters == ["seoul-canary", "seoul-main"]

        # 인덱스 2: 모든 클러스터
        sample_rollout.current_stage_index = 2
        assert sample_rollout.affected_clusters == [
            "seoul-canary",
            "seoul-main",
            "tokyo",
            "singapore",
        ]

    def test_is_terminal_property(self, sample_rollout):
        """is_terminal이 종료 상태를 올바르게 판단."""
        # 비종료 상태
        sample_rollout.state = CanaryState.CREATED
        assert sample_rollout.is_terminal is False

        sample_rollout.state = CanaryState.CANARY
        assert sample_rollout.is_terminal is False

        sample_rollout.state = CanaryState.PROMOTING
        assert sample_rollout.is_terminal is False

        sample_rollout.state = CanaryState.PAUSED
        assert sample_rollout.is_terminal is False

        # 종료 상태
        sample_rollout.state = CanaryState.COMPLETED
        assert sample_rollout.is_terminal is True

        sample_rollout.state = CanaryState.ROLLED_BACK
        assert sample_rollout.is_terminal is True

        sample_rollout.state = CanaryState.FAILED
        assert sample_rollout.is_terminal is True

        sample_rollout.state = CanaryState.CANCELLED
        assert sample_rollout.is_terminal is True

    def test_progress_percentage_created_state(self, sample_rollout):
        """CREATED 상태에서는 진행률 0%."""
        sample_rollout.state = CanaryState.CREATED
        assert sample_rollout.progress_percentage == 0.0

    def test_progress_percentage_completed_state(self, sample_rollout):
        """COMPLETED 상태에서는 진행률 100%."""
        sample_rollout.state = CanaryState.COMPLETED
        assert sample_rollout.progress_percentage == 100.0

    def test_progress_percentage_in_progress(self, sample_rollout):
        """진행 중일 때 현재 단계까지의 percentage 합계."""
        sample_rollout.state = CanaryState.CANARY

        # 인덱스 0: 10%
        sample_rollout.current_stage_index = 0
        assert sample_rollout.progress_percentage == 10.0

        # 인덱스 1: 10% + 50% = 60%
        sample_rollout.current_stage_index = 1
        assert sample_rollout.progress_percentage == 60.0

        # 인덱스 2: 10% + 50% + 100% = 160% (합계, percentage가 누적이 아닌 경우)
        sample_rollout.current_stage_index = 2
        assert sample_rollout.progress_percentage == 160.0

    def test_rollout_with_empty_stages(self):
        """빈 단계 목록으로 생성 가능."""
        rollout = CanaryRollout(
            id="empty",
            config_type="test",
            previous_values={},
            new_values={},
        )
        assert rollout.stages == []
        assert rollout.current_stage is None
        assert rollout.affected_clusters == []
        assert rollout.progress_percentage == 0.0

    def test_rollout_state_transition(self, sample_rollout):
        """상태 전이가 올바르게 동작."""
        assert sample_rollout.state == CanaryState.CREATED

        sample_rollout.state = CanaryState.CANARY
        assert sample_rollout.state == CanaryState.CANARY

        sample_rollout.state = CanaryState.PAUSED
        assert sample_rollout.state == CanaryState.PAUSED

        sample_rollout.state = CanaryState.ROLLED_BACK
        sample_rollout.rollback_reason = "High error rate detected"
        assert sample_rollout.state == CanaryState.ROLLED_BACK
        assert sample_rollout.rollback_reason == "High error rate detected"
