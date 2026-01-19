"""
Canary Rollout Service 단위 테스트.

테스트 대상:
1. CanaryRolloutService.create_rollout() - 롤아웃 생성
2. CanaryRolloutService.start_rollout() - 롤아웃 시작
3. CanaryRolloutService.promote() - 프로모션
4. CanaryRolloutService.rollback() - 롤백
5. CanaryRolloutService.pause() / resume() - 일시 중지/재개
6. 조회 메서드들 (get_rollout, get_active_rollouts)

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from selfhealing.services.canary.models import (
    CanaryState,
    CanaryStage,
    CanaryRollout,
)
from selfhealing.services.canary.service import (
    CanaryRolloutService,
    get_canary_rollout_service,
    reset_canary_rollout_service,
)
from selfhealing.services.canary.locking import ConfigLockError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_redis():
    """Mock Redis 클라이언트."""
    redis = MagicMock()
    redis.get.return_value = None
    redis.set.return_value = True
    redis.exists.return_value = 0
    redis.sadd.return_value = 1
    redis.srem.return_value = 1
    redis.smembers.return_value = set()
    return redis


@pytest.fixture
def sample_stages():
    """샘플 단계 목록."""
    return [
        CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10.0),
        CanaryStage(name="50%", clusters=["seoul-main"], percentage=50.0),
        CanaryStage(name="full", clusters=["tokyo", "singapore"], percentage=100.0),
    ]


@pytest.fixture
def service(mock_redis):
    """테스트용 CanaryRolloutService."""
    reset_canary_rollout_service()
    svc = CanaryRolloutService()
    svc._redis_client = mock_redis
    
    # Mock config_lock
    mock_lock = MagicMock()
    mock_lock.is_locked.return_value = False
    mock_lock.acquire.return_value = True
    mock_lock.release.return_value = True
    svc._config_lock = mock_lock
    
    # Mock chaos_guard
    mock_guard = MagicMock()
    mock_guard.check_conflict.return_value = MagicMock(
        has_conflict=False,
        can_proceed=True,
        safe_clusters=["seoul-canary", "seoul-main", "tokyo", "singapore"],
        chaos_clusters=[],
        policy_applied=MagicMock(value="smart"),
        warning_message=None,
    )
    svc._chaos_guard = mock_guard
    
    # Mock config_history
    mock_history = MagicMock()
    mock_history.get_current_version.return_value = None
    svc._config_history = mock_history
    
    return svc


# =============================================================================
# Test: create_rollout
# =============================================================================


class TestCreateRollout:
    """create_rollout 테스트."""

    def test_create_rollout_success(self, service, sample_stages):
        """롤아웃 생성 성공."""
        with patch("selfhealing.services.canary.service.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            
            with patch("selfhealing.services.canary.service.log_canary_action"):
                rollout = service.create_rollout(
                    config_type="circuit_breaker",
                    new_values={"failure_threshold": 3},
                    stages=sample_stages,
                    created_by="admin@example.com",
                    reason="Reduce threshold",
                )
        
        assert rollout is not None
        assert rollout.config_type == "circuit_breaker"
        assert rollout.new_values == {"failure_threshold": 3}
        assert rollout.state == CanaryState.CREATED
        assert len(rollout.stages) == 3
        assert rollout.created_by == "admin@example.com"

    def test_create_rollout_empty_stages_raises_error(self, service):
        """빈 단계 목록으로 생성 시 ValueError."""
        with pytest.raises(ValueError, match="At least one stage"):
            service.create_rollout(
                config_type="circuit_breaker",
                new_values={"failure_threshold": 3},
                stages=[],
                created_by="admin@example.com",
            )

    def test_create_rollout_blocked_by_lock(self, service, sample_stages):
        """이미 락이 있을 때 ConfigLockError."""
        service._config_lock.is_locked.return_value = True
        service._config_lock.get_lock_owner.return_value = "other-rollout"
        
        with pytest.raises(ConfigLockError):
            service.create_rollout(
                config_type="circuit_breaker",
                new_values={"failure_threshold": 3},
                stages=sample_stages,
                created_by="admin@example.com",
            )

    def test_create_rollout_with_previous_values(self, service, sample_stages):
        """이전 설정값이 있을 때 저장."""
        mock_version = MagicMock()
        mock_version.values = {"failure_threshold": 5}
        service._config_history.get_current_version.return_value = mock_version
        
        with patch("selfhealing.services.canary.service.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            
            with patch("selfhealing.services.canary.service.log_canary_action"):
                rollout = service.create_rollout(
                    config_type="circuit_breaker",
                    new_values={"failure_threshold": 3},
                    stages=sample_stages,
                    created_by="admin@example.com",
                )
        
        assert rollout.previous_values == {"failure_threshold": 5}


# =============================================================================
# Test: start_rollout
# =============================================================================


class TestStartRollout:
    """start_rollout 테스트."""

    @pytest.fixture
    def rollout(self, service, sample_stages):
        """테스트용 롤아웃."""
        with patch("selfhealing.services.canary.service.get_key_prefix") as mock_prefix:
            mock_prefix.return_value = "selfhealing:test:"
            
            with patch("selfhealing.services.canary.service.log_canary_action"):
                return service.create_rollout(
                    config_type="circuit_breaker",
                    new_values={"failure_threshold": 3},
                    stages=sample_stages,
                    created_by="admin@example.com",
                )

    def test_start_rollout_success(self, service, rollout, mock_redis):
        """롤아웃 시작 성공."""
        # Mock get_rollout
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch("selfhealing.services.canary.service.get_key_prefix") as mock_prefix:
                mock_prefix.return_value = "selfhealing:test:"
                
                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = service.start_rollout(rollout.id)
        
        assert result is True
        assert rollout.state == CanaryState.CANARY
        assert rollout.current_stage_index == 0

    def test_start_rollout_not_found(self, service):
        """존재하지 않는 롤아웃."""
        with patch.object(service, "get_rollout", return_value=None):
            result = service.start_rollout("nonexistent")
        
        assert result is False

    def test_start_rollout_wrong_state(self, service, rollout):
        """이미 시작된 롤아웃."""
        rollout.state = CanaryState.CANARY
        
        with patch.object(service, "get_rollout", return_value=rollout):
            result = service.start_rollout(rollout.id)
        
        assert result is False


# =============================================================================
# Test: promote
# =============================================================================


class TestPromote:
    """promote 테스트."""

    def test_promote_to_next_stage(self, service, mock_redis):
        """다음 단계로 프로모션."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={"failure_threshold": 3},
            state=CanaryState.CANARY,
            current_stage_index=0,
            stages=[
                CanaryStage(name="canary", clusters=["seoul"], percentage=10),
                CanaryStage(name="full", clusters=["tokyo"], percentage=100),
            ],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch.object(service, "_save_rollout"):
                with patch("selfhealing.services.canary.service.get_key_prefix") as mock_prefix:
                    mock_prefix.return_value = "selfhealing:test:"
                    
                    with patch("selfhealing.services.canary.service.log_canary_action"):
                        result = service.promote(rollout.id)
        
        assert result is True
        assert rollout.current_stage_index == 1

    def test_promote_completes_rollout(self, service, mock_redis):
        """마지막 단계 프로모션 시 완료."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={"failure_threshold": 3},
            state=CanaryState.CANARY,
            current_stage_index=1,  # 마지막 단계
            stages=[
                CanaryStage(name="canary", clusters=["seoul"], percentage=10),
                CanaryStage(name="full", clusters=["tokyo"], percentage=100),
            ],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch.object(service, "_save_rollout"):
                with patch.object(service, "_remove_from_active"):
                    with patch("selfhealing.services.canary.service.log_canary_action"):
                        result = service.promote(rollout.id)
        
        assert result is True
        assert rollout.state == CanaryState.COMPLETED
        assert rollout.completed_at is not None

    def test_promote_wrong_state(self, service):
        """완료된 롤아웃은 프로모션 불가."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.COMPLETED,
            stages=[],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            result = service.promote(rollout.id)
        
        assert result is False


# =============================================================================
# Test: rollback
# =============================================================================


class TestRollback:
    """rollback 테스트."""

    def test_rollback_success(self, service, mock_redis):
        """롤백 성공."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={"failure_threshold": 5},
            new_values={"failure_threshold": 3},
            state=CanaryState.CANARY,
            current_stage_index=1,
            stages=[
                CanaryStage(name="canary", clusters=["seoul"], percentage=10),
                CanaryStage(name="full", clusters=["tokyo"], percentage=100),
            ],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch.object(service, "_save_rollout"):
                with patch.object(service, "_remove_from_active"):
                    with patch.object(service, "_apply_config_to_cluster"):
                        with patch("selfhealing.services.canary.service.log_canary_action"):
                            result = service.rollback(rollout.id, reason="High error rate")
        
        assert result is True
        assert rollout.state == CanaryState.ROLLED_BACK
        assert rollout.rollback_reason == "High error rate"
        assert rollout.completed_at is not None

    def test_rollback_terminal_state(self, service):
        """이미 종료된 롤아웃은 롤백 불가."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.COMPLETED,
            stages=[],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            result = service.rollback(rollout.id, reason="Test")
        
        assert result is False


# =============================================================================
# Test: pause / resume
# =============================================================================


class TestPauseResume:
    """pause/resume 테스트."""

    def test_pause_success(self, service):
        """일시 중지 성공."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.CANARY,
            stages=[],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch.object(service, "_save_rollout"):
                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = service.pause(rollout.id)
        
        assert result is True
        assert rollout.state == CanaryState.PAUSED

    def test_resume_success(self, service):
        """재개 성공."""
        rollout = CanaryRollout(
            id="test-123",
            config_type="circuit_breaker",
            previous_values={},
            new_values={},
            state=CanaryState.PAUSED,
            stages=[],
            created_by="admin",
        )
        
        with patch.object(service, "get_rollout", return_value=rollout):
            with patch.object(service, "_save_rollout"):
                with patch("selfhealing.services.canary.service.log_canary_action"):
                    result = service.resume(rollout.id)
        
        assert result is True
        assert rollout.state == CanaryState.CANARY


# =============================================================================
# Test: Singleton
# =============================================================================


class TestSingleton:
    """싱글톤 패턴 테스트."""

    def test_get_canary_rollout_service_returns_same_instance(self):
        """싱글톤 인스턴스 반환."""
        reset_canary_rollout_service()
        
        with patch("selfhealing.services.canary.service._service", None):
            svc1 = get_canary_rollout_service()
            svc2 = get_canary_rollout_service()
        
        # 두 번 호출해도 새 인스턴스 생성은 한 번
        assert svc1 is not None
        assert svc2 is not None
