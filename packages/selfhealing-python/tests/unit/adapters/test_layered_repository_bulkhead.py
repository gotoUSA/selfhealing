"""
LayeredRepositoryBase Bulkhead 통합 테스트.

LayeredRepositoryBase에 Bulkhead 패턴 통합 후 동작을 검증합니다:
- use_bulkhead=True일 때 격벽 초기화
- _execute_with_bulkhead 메서드 동작
- Bulkhead 거부 시 메트릭 증가
"""

from __future__ import annotations

import pytest

from selfhealing.adapters.memory.layered_repository.base import LayeredRepositoryBase
from selfhealing.resilience.bulkhead.registry import (
    reset_bulkhead_registry,
)
from selfhealing.settings.bulkhead import reset_bulkhead_settings


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_bulkhead_registry()
    reset_bulkhead_settings()
    yield
    reset_bulkhead_registry()
    reset_bulkhead_settings()


class TestLayeredRepositoryBaseBulkhead:
    """LayeredRepositoryBase Bulkhead 통합 테스트."""

    def test_init_with_bulkhead_enabled(self):
        """use_bulkhead=True(기본값)로 격벽 초기화."""
        repo = LayeredRepositoryBase(
            adapter_type="redis",
            use_bulkhead=True,
        )

        assert repo._use_bulkhead is True
        assert repo._bulkhead is not None
        assert repo._bulkhead.name == "cache"  # redis → cache 격벽

    def test_init_with_bulkhead_disabled(self):
        """use_bulkhead=False로 격벽 비활성화."""
        repo = LayeredRepositoryBase(
            adapter_type="redis",
            use_bulkhead=False,
        )

        assert repo._use_bulkhead is False
        assert repo._bulkhead is None

    def test_adapter_type_to_bulkhead_mapping(self):
        """어댑터 타입에 따른 격벽 매핑."""
        # redis → cache
        repo_redis = LayeredRepositoryBase(adapter_type="redis", use_bulkhead=True)
        assert repo_redis._bulkhead.name == "cache"

        # database → database
        repo_db = LayeredRepositoryBase(adapter_type="database", use_bulkhead=True)
        assert repo_db._bulkhead.name == "database"

        # django → database
        repo_django = LayeredRepositoryBase(adapter_type="django", use_bulkhead=True)
        assert repo_django._bulkhead.name == "database"

    def test_execute_with_bulkhead_success(self):
        """_execute_with_bulkhead 성공 시 함수 실행."""
        repo = LayeredRepositoryBase(adapter_type="redis", use_bulkhead=True)

        result = repo._execute_with_bulkhead(
            "test_operation",
            lambda x: x * 2,
            5,
        )

        assert result == 10

    def test_execute_with_bulkhead_disabled(self):
        """use_bulkhead=False일 때 함수 직접 실행."""
        repo = LayeredRepositoryBase(adapter_type="redis", use_bulkhead=False)

        result = repo._execute_with_bulkhead(
            "test_operation",
            lambda x: x * 2,
            5,
        )

        assert result == 10

    def test_execute_with_bulkhead_rejected_returns_none(self):
        """Bulkhead 거부 시 None 반환."""
        repo = LayeredRepositoryBase(adapter_type="redis", use_bulkhead=True)

        # 격벽의 모든 슬롯 점유
        bulkhead = repo._bulkhead
        max_concurrent = bulkhead.get_state().max_concurrent
        for _ in range(max_concurrent):
            bulkhead.try_acquire()

        result = repo._execute_with_bulkhead(
            "test_operation",
            lambda: "should_not_run",
        )

        assert result is None
        assert repo._metrics["bulkhead_rejected_count"] == 1

        # 정리
        for _ in range(max_concurrent):
            bulkhead.release()

    def test_metrics_includes_bulkhead_rejected_count(self):
        """메트릭에 bulkhead_rejected_count 필드 존재."""
        repo = LayeredRepositoryBase(adapter_type="redis", use_bulkhead=True)

        assert "bulkhead_rejected_count" in repo._metrics
        assert repo._metrics["bulkhead_rejected_count"] == 0
