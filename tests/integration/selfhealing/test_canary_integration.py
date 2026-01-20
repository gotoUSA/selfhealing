"""
Canary Config Rollout Integration Tests.

Docker Compose 환경에서 실제 Redis를 사용한 Canary Rollout 통합 테스트.

Requirements:
- Docker Compose for Redis
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_canary_integration.py -v

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

import os
import sys
import pytest

# 이 파일의 모든 테스트는 Redis 필요
pytestmark = pytest.mark.requires_redis

from datetime import timedelta
from unittest.mock import patch, MagicMock

# Setup Django before importing selfhealing
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

from selfhealing.utils.time import utc_now
from selfhealing.services.canary import (
    CanaryRolloutService,
    CanaryRollout,
    CanaryStage,
    CanaryState,
    get_canary_rollout_service,
    reset_canary_rollout_service,
)
from selfhealing.services.canary.locking import CanaryConfigLock
from selfhealing.tasks.canary_watchdog import (
    RolloutWatchdog,
    WatchdogConfig,
    get_rollout_watchdog,
    reset_watchdog,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_services():
    """각 테스트 전/후 서비스 리셋."""
    reset_canary_rollout_service()
    reset_watchdog()
    yield
    reset_canary_rollout_service()
    reset_watchdog()


# NOTE: redis_available fixture 제거됨
# tests/conftest.py의 pytest_collection_modifyitems가 requires_redis 마커 처리


@pytest.fixture
def sample_stages():
    """테스트용 Canary 단계 정의."""
    return [
        CanaryStage(
            name="canary",
            clusters=["seoul-canary"],
            percentage=10.0,
            duration_minutes=5,
            auto_promote=True,
        ),
        CanaryStage(
            name="regional",
            clusters=["seoul-main", "tokyo-main"],
            percentage=50.0,
            duration_minutes=10,
        ),
        CanaryStage(
            name="global",
            clusters=["seoul-main", "tokyo-main", "singapore-main"],
            percentage=100.0,
            duration_minutes=0,  # 최종 단계
        ),
    ]


# NOTE: requires_redis 마커는 tests/conftest.py에서 처리됨
# 파일 레벨 pytestmark로 대체


# =============================================================================
# Integration Tests: CanaryRolloutService
# =============================================================================


class TestCanaryRolloutServiceIntegration:
    """CanaryRolloutService 통합 테스트."""

    def test_create_and_get_rollout(self, sample_stages):
        """롤아웃 생성 및 조회."""
        service = get_canary_rollout_service()
        
        # 생성
        rollout = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"failure_threshold": 3},
            stages=sample_stages,
            created_by="integration-test@example.com",
            reason="Integration test",
        )
        
        assert rollout is not None
        assert rollout.id is not None
        assert rollout.state == CanaryState.CREATED
        
        # 조회
        retrieved = service.get_rollout(rollout.id)
        assert retrieved is not None
        assert retrieved.id == rollout.id
        assert retrieved.config_type == "circuit_breaker"

    def test_full_rollout_lifecycle(self, sample_stages):
        """롤아웃 전체 생명주기 테스트: 생성 → 시작 → 프로모션 → 완료."""
        service = get_canary_rollout_service()
        
        # 1. 생성
        rollout = service.create_rollout(
            config_type="dlq",
            new_values={"max_retries": 5},
            stages=sample_stages,
            created_by="integration-test@example.com",
        )
        assert rollout.state == CanaryState.CREATED
        
        # 2. 시작
        started = service.start_rollout(rollout.id)
        assert started is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.CANARY
        assert rollout.current_stage_index == 0
        
        # 3. 프로모션 1 (canary → regional)
        promoted = service.promote(rollout.id, force=True)
        assert promoted is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.current_stage_index == 1
        
        # 4. 프로모션 2 (regional → global)
        promoted = service.promote(rollout.id, force=True)
        assert promoted is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.current_stage_index == 2
        
        # 5. 프로모션 3 (global → completed)
        promoted = service.promote(rollout.id, force=True)
        assert promoted is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.COMPLETED
        assert rollout.completed_at is not None

    def test_rollback_restores_previous_values(self, sample_stages):
        """롤백 시 이전 값 복원 확인."""
        service = get_canary_rollout_service()
        
        # 롤아웃 생성 및 시작
        rollout = service.create_rollout(
            config_type="retry",
            previous_values={"max_attempts": 3},
            new_values={"max_attempts": 5},
            stages=sample_stages,
            created_by="integration-test@example.com",
        )
        service.start_rollout(rollout.id)
        
        # 롤백
        rolled_back = service.rollback(rollout.id, reason="Integration test rollback")
        assert rolled_back is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.ROLLED_BACK
        assert rollout.rollback_reason == "Integration test rollback"

    def test_pause_and_resume(self, sample_stages):
        """일시 중지 및 재개."""
        service = get_canary_rollout_service()
        
        rollout = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"timeout": 30},
            stages=sample_stages,
            created_by="integration-test@example.com",
        )
        service.start_rollout(rollout.id)
        
        # 일시 중지
        paused = service.pause(rollout.id)
        assert paused is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.PAUSED
        
        # 재개
        resumed = service.resume(rollout.id)
        assert resumed is True
        
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.CANARY


# =============================================================================
# Integration Tests: ConfigLock
# =============================================================================


class TestConfigLockIntegration:
    """ConfigLock 통합 테스트."""

    def test_lock_prevents_concurrent_rollouts(self):
        """동시 롤아웃 방지."""
        service = get_canary_rollout_service()
        stages = [
            CanaryStage(name="test", clusters=["test"], percentage=100, duration_minutes=5)
        ]
        
        # 첫 번째 롤아웃 생성
        rollout1 = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"threshold": 3},
            stages=stages,
            created_by="user1@example.com",
        )
        
        # 두 번째 롤아웃 시도 - 락으로 인해 실패해야 함
        rollout2 = service.create_rollout(
            config_type="circuit_breaker",  # 같은 config_type
            new_values={"threshold": 5},
            stages=stages,
            created_by="user2@example.com",
        )
        
        # 락이 걸려있으면 None 반환
        assert rollout2 is None


# =============================================================================
# Integration Tests: Watchdog
# =============================================================================


class TestWatchdogIntegration:
    """RolloutWatchdog 통합 테스트."""

    def test_watchdog_detects_zombie_rollouts(self, sample_stages):
        """Zombie 롤아웃 감지."""
        service = get_canary_rollout_service()
        
        # 오래된 롤아웃 생성 (zombie 판정 대상)
        rollout = service.create_rollout(
            config_type="dlq",
            new_values={"max_retries": 10},
            stages=sample_stages,
            created_by="integration-test@example.com",
        )
        service.start_rollout(rollout.id)
        
        # 생성 시간을 과거로 조작 (테스트 목적)
        rollout = service.get_rollout(rollout.id)
        rollout.created_at = utc_now() - timedelta(minutes=45)
        service._save_rollout(rollout)
        
        # Watchdog 스캔
        config = WatchdogConfig(zombie_threshold_minutes=30)
        watchdog = RolloutWatchdog(config=config)
        watchdog._service = service
        
        result = watchdog.scan_and_handle()
        
        assert result.scanned_count >= 1
        assert result.zombie_count >= 1

    def test_watchdog_auto_rollback_on_threshold(self, sample_stages):
        """임계값 초과 시 자동 롤백."""
        service = get_canary_rollout_service()
        
        # 매우 오래된 롤아웃 생성
        rollout = service.create_rollout(
            config_type="retry",
            previous_values={"attempts": 3},
            new_values={"attempts": 5},
            stages=sample_stages,
            created_by="integration-test@example.com",
        )
        service.start_rollout(rollout.id)
        
        # 생성 시간을 매우 과거로 조작
        rollout = service.get_rollout(rollout.id)
        rollout.created_at = utc_now() - timedelta(minutes=90)
        service._save_rollout(rollout)
        
        # Watchdog 스캔 (auto_rollback_after_minutes=60)
        config = WatchdogConfig(
            zombie_threshold_minutes=30,
            auto_rollback_after_minutes=60,
            enable_auto_rollback=True,
        )
        watchdog = RolloutWatchdog(config=config)
        watchdog._service = service
        
        result = watchdog.scan_and_handle()
        
        assert result.rollback_count >= 1
        
        # 롤아웃 상태 확인
        rollout = service.get_rollout(rollout.id)
        assert rollout.state == CanaryState.ROLLED_BACK


# =============================================================================
# Integration Tests: Active Rollouts
# =============================================================================


class TestActiveRolloutsIntegration:
    """활성 롤아웃 목록 관리 통합 테스트."""

    def test_get_active_rollouts(self, sample_stages):
        """활성 롤아웃 목록 조회."""
        service = get_canary_rollout_service()
        
        # 여러 롤아웃 생성
        rollout1 = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"threshold": 3},
            stages=sample_stages,
            created_by="user1@example.com",
        )
        
        rollout2 = service.create_rollout(
            config_type="dlq",  # 다른 config_type
            new_values={"retries": 5},
            stages=sample_stages,
            created_by="user2@example.com",
        )
        
        service.start_rollout(rollout1.id)
        service.start_rollout(rollout2.id)
        
        # 활성 롤아웃 목록 조회
        active = service.get_active_rollouts()
        
        assert len(active) >= 2
        active_ids = [r.id for r in active]
        assert rollout1.id in active_ids
        assert rollout2.id in active_ids


# =============================================================================
# Cleanup
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data():
    """
    테스트 종료 후 데이터 정리.
    
    Cleans up canary-related Redis keys after test module completes.
    Uses RedisTestConfig.TEST_PORT for Docker Compose environment.
    """
    yield
    
    # 테스트 데이터 정리
    try:
        import redis
        from tests.factories.constants import REDIS_CONFIG
        redis_port = int(os.environ.get("REDIS_PORT", REDIS_CONFIG.TEST_PORT))
        client = redis.Redis(host=REDIS_CONFIG.DEFAULT_HOST, port=redis_port)
        
        # canary 관련 키 삭제
        pattern = "*canary*"
        keys = client.keys(pattern)
        if keys:
            client.delete(*keys)
    except Exception:
        pass
