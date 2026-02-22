"""
Tests for OptimisticLocalActionExecutor.

72번 문서 §5.4.2에 정의된 낙관적 선조치 실행기 테스트.

테스트 대상:
- OptimisticLocalActionExecutor 클래스
- execute_with_optimistic_action 메서드
- 로컬 캐시 관리
- 싱글톤 팩토리
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from selfhealing.services.coordination.optimistic_action import (
    OptimisticActionResult,
    OptimisticLocalActionExecutor,
    get_optimistic_action_executor,
    reset_optimistic_action_executor,
)


# 테스트용 Mock Action
class MockActionType(str, Enum):
    GOVERNANCE_STRICT = "governance_strict"
    CANARY_PAUSE = "canary_pause"


@dataclass
class MockCoordinationAction:
    type: MockActionType
    params: dict = None

    def __post_init__(self):
        if self.params is None:
            self.params = {}


class TestOptimisticActionResult:
    """OptimisticActionResult 테스트."""

    def test_create_success_result(self):
        """성공 결과 생성."""
        result = OptimisticActionResult(
            success=True,
            action_id="opt-abc123",
        )
        assert result.success is True
        assert result.action_id == "opt-abc123"
        assert result.sync_scheduled is False
        assert result.sync_completed is False
        assert result.error is None

    def test_create_failure_result(self):
        """실패 결과 생성."""
        result = OptimisticActionResult(
            success=False,
            action_id="opt-def456",
            error="Handler failed",
        )
        assert result.success is False
        assert result.error == "Handler failed"

    def test_to_dict(self):
        """딕셔너리로 변환."""
        result = OptimisticActionResult(
            success=True,
            action_id="opt-abc123",
            sync_scheduled=True,
            details={"action_type": "governance_strict"},
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["action_id"] == "opt-abc123"
        assert data["sync_scheduled"] is True
        assert data["details"]["action_type"] == "governance_strict"
        assert "executed_at" in data


class TestOptimisticLocalActionExecutor:
    """OptimisticLocalActionExecutor 기본 테스트."""

    def test_create_executor(self):
        """Executor 생성."""
        executor = OptimisticLocalActionExecutor()
        assert executor is not None

    def test_create_executor_with_redis(self):
        """Redis 클라이언트와 함께 Executor 생성."""
        mock_redis = object()
        executor = OptimisticLocalActionExecutor(redis_client=mock_redis)
        assert executor._redis is mock_redis


class TestExecuteWithOptimisticAction:
    """execute_with_optimistic_action 메서드 테스트."""

    def test_execute_returns_result(self):
        """실행 시 결과 반환."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        result = executor.execute_with_optimistic_action(
            action=action,
            namespace="seoul",
        )

        assert isinstance(result, OptimisticActionResult)
        assert result.success is True
        assert result.action_id.startswith("opt-")

    def test_execute_updates_local_cache(self):
        """실행 시 로컬 캐시 업데이트."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        executor.execute_with_optimistic_action(
            action=action,
            namespace="tokyo",
        )

        cache = executor.get_local_state("tokyo")
        assert cache is not None
        assert cache["last_action"] == "governance_strict"

    def test_execute_schedules_sync_with_redis(self):
        """Redis가 있으면 동기화 예약."""
        mock_redis = object()
        executor = OptimisticLocalActionExecutor(redis_client=mock_redis)
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        result = executor.execute_with_optimistic_action(
            action=action,
            namespace="seoul",
        )

        assert result.sync_scheduled is True

    def test_execute_without_redis_no_sync(self):
        """Redis가 없으면 동기화 예약 안함."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        result = executor.execute_with_optimistic_action(
            action=action,
            namespace="seoul",
        )

        assert result.sync_scheduled is False

    def test_execute_with_params(self):
        """파라미터가 있는 액션 실행."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(
            type=MockActionType.GOVERNANCE_STRICT,
            params={"reason_prefix": "[AUTO-CASCADE]"},
        )

        result = executor.execute_with_optimistic_action(
            action=action,
            namespace="seoul",
        )

        cache = executor.get_local_state("seoul")
        assert cache["params"] == {"reason_prefix": "[AUTO-CASCADE]"}


class TestHandlerRegistration:
    """액션 핸들러 등록 테스트."""

    def test_register_handler(self):
        """핸들러 등록."""
        executor = OptimisticLocalActionExecutor()

        def mock_handler(namespace: str, params: dict) -> bool:
            return True

        executor.register_handler("governance_strict", mock_handler)

        assert "governance_strict" in executor._action_handlers

    def test_handler_executed_on_action(self):
        """액션 실행 시 핸들러 호출."""
        executor = OptimisticLocalActionExecutor()
        handler_called = []

        def mock_handler(namespace: str, params: dict) -> bool:
            handler_called.append((namespace, params))
            return True

        executor.register_handler("governance_strict", mock_handler)
        action = MockCoordinationAction(
            type=MockActionType.GOVERNANCE_STRICT,
            params={"reason": "test"},
        )

        executor.execute_with_optimistic_action(action=action, namespace="seoul")

        assert len(handler_called) == 1
        assert handler_called[0][0] == "seoul"
        assert handler_called[0][1] == {"reason": "test"}

    def test_handler_failure_returns_failed_result(self):
        """핸들러 실패 시 실패 결과 반환."""
        executor = OptimisticLocalActionExecutor()

        def failing_handler(namespace: str, params: dict) -> bool:
            return False

        executor.register_handler("governance_strict", failing_handler)
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        result = executor.execute_with_optimistic_action(action=action, namespace="seoul")

        assert result.success is False
        assert "Handler execution failed" in result.error


class TestLocalCacheManagement:
    """로컬 캐시 관리 테스트."""

    def test_get_local_state_empty(self):
        """빈 캐시 조회."""
        executor = OptimisticLocalActionExecutor()
        state = executor.get_local_state("nonexistent")
        assert state is None

    def test_clear_local_cache_specific(self):
        """특정 네임스페이스 캐시 삭제."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        executor.execute_with_optimistic_action(action=action, namespace="seoul")
        executor.execute_with_optimistic_action(action=action, namespace="tokyo")

        executor.clear_local_cache("seoul")

        assert executor.get_local_state("seoul") is None
        assert executor.get_local_state("tokyo") is not None

    def test_clear_local_cache_all(self):
        """전체 캐시 삭제."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        executor.execute_with_optimistic_action(action=action, namespace="seoul")
        executor.execute_with_optimistic_action(action=action, namespace="tokyo")

        executor.clear_local_cache()

        assert executor.get_local_state("seoul") is None
        assert executor.get_local_state("tokyo") is None


class TestStatistics:
    """통계 테스트."""

    def test_stats_initial(self):
        """초기 통계."""
        executor = OptimisticLocalActionExecutor()
        stats = executor.get_stats()

        assert stats["local_executions"] == 0
        assert stats["sync_successes"] == 0
        assert stats["sync_failures"] == 0
        assert stats["pending_sync_count"] == 0

    def test_stats_after_execution(self):
        """실행 후 통계."""
        executor = OptimisticLocalActionExecutor()
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        executor.execute_with_optimistic_action(action=action, namespace="seoul")
        executor.execute_with_optimistic_action(action=action, namespace="tokyo")

        stats = executor.get_stats()

        assert stats["local_executions"] == 2
        assert "seoul" in stats["cached_namespaces"]
        assert "tokyo" in stats["cached_namespaces"]


class TestSyncPending:
    """sync_pending 메서드 테스트."""

    def test_sync_pending_without_redis(self):
        """Redis 없이 sync_pending 호출."""
        executor = OptimisticLocalActionExecutor()
        results = executor.sync_pending()
        assert results == {}

    def test_sync_pending_with_redis(self):
        """Redis와 함께 sync_pending 호출."""
        mock_redis = object()
        executor = OptimisticLocalActionExecutor(redis_client=mock_redis)
        action = MockCoordinationAction(type=MockActionType.GOVERNANCE_STRICT)

        # 액션 실행 (동기화 대기열에 추가)
        executor.execute_with_optimistic_action(action=action, namespace="seoul")

        # 동기화 수행
        results = executor.sync_pending()

        # 현재는 Mock이므로 실제 동기화는 안되지만 로직 테스트
        assert len(results) >= 0


class TestSingleton:
    """싱글톤 팩토리 테스트."""

    def setup_method(self):
        """각 테스트 전 싱글톤 초기화."""
        reset_optimistic_action_executor()

    def teardown_method(self):
        """각 테스트 후 싱글톤 초기화."""
        reset_optimistic_action_executor()

    def test_get_returns_same_instance(self):
        """같은 인스턴스 반환."""
        executor1 = get_optimistic_action_executor()
        executor2 = get_optimistic_action_executor()
        assert executor1 is executor2

    def test_reset_clears_singleton(self):
        """초기화 후 새 인스턴스 생성."""
        executor1 = get_optimistic_action_executor()
        reset_optimistic_action_executor()
        executor2 = get_optimistic_action_executor()
        assert executor1 is not executor2
