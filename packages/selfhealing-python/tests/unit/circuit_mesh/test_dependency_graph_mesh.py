"""
ServiceDependencyGraph 메쉬 확장 메서드 단위 테스트.

테스트 대상: core/dependency_graph.py
  - get_dependencies()
  - get_dependents_recursive()
  - topological_sort_subset()
"""

from __future__ import annotations

import pytest

from selfhealing.core.dependency_graph import ServiceDependencyGraph

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def graph():
    """기본 의존성 그래프.

    구조:
        gateway → api → db
                    └→ cache
    """
    g = ServiceDependencyGraph()
    g.register_service("db", depends_on=[], criticality="critical")
    g.register_service("cache", depends_on=[], criticality="medium")
    g.register_service("api", depends_on=["db", "cache"], criticality="high")
    g.register_service("gateway", depends_on=["api"], criticality="medium")
    return g


# =============================================================================
# 동작 검증 (Behavior) — get_dependencies
# =============================================================================


class TestGetDependenciesBehavior:
    """get_dependencies 동작 검증."""

    def test_returns_direct_dependencies(self, graph):
        """서비스의 직접 의존 하류 목록을 반환한다."""
        deps = graph.get_dependencies("api")
        assert sorted(deps) == ["cache", "db"]

    def test_returns_empty_for_leaf_service(self, graph):
        """의존 없는 리프 서비스는 빈 리스트 반환."""
        deps = graph.get_dependencies("db")
        assert deps == []

    def test_returns_empty_for_unknown_service(self, graph):
        """미등록 서비스는 빈 리스트 반환."""
        deps = graph.get_dependencies("unknown")
        assert deps == []


# =============================================================================
# 동작 검증 (Behavior) — get_dependents_recursive
# =============================================================================


class TestGetDependentsRecursiveBehavior:
    """get_dependents_recursive 동작 검증."""

    def test_returns_direct_dependents_at_depth_1(self, graph):
        """max_depth=1에서 직접 상류만 반환한다."""
        result = graph.get_dependents_recursive("db", max_depth=1)
        service_names = [name for name, depth in result]
        assert "api" in service_names
        assert all(d == 1 for _, d in result)

    def test_returns_recursive_dependents_at_depth_2(self, graph):
        """max_depth=2에서 간접 상류까지 반환한다."""
        result = graph.get_dependents_recursive("db", max_depth=2)
        service_names = [name for name, depth in result]
        assert "api" in service_names
        assert "gateway" in service_names

    def test_depth_is_correct_for_each_result(self, graph):
        """각 결과의 depth가 정확하다."""
        result = graph.get_dependents_recursive("db", max_depth=2)
        depth_map = {name: depth for name, depth in result}
        assert depth_map["api"] == 1
        assert depth_map["gateway"] == 2

    def test_returns_empty_for_root_service(self, graph):
        """상류 없는 루트 서비스는 빈 리스트 반환."""
        result = graph.get_dependents_recursive("gateway", max_depth=3)
        assert result == []

    def test_returns_empty_for_unknown_service(self, graph):
        """미등록 서비스는 빈 리스트 반환."""
        result = graph.get_dependents_recursive("unknown", max_depth=2)
        assert result == []

    def test_respects_max_depth_limit(self, graph):
        """max_depth 제한을 준수한다."""
        result = graph.get_dependents_recursive("db", max_depth=1)
        service_names = [name for name, depth in result]
        assert "gateway" not in service_names

    def test_handles_circular_dependency_without_infinite_loop(self):
        """순환 의존성에서 무한 루프 없이 처리한다."""
        g = ServiceDependencyGraph()
        g.register_service("a", depends_on=["b"])
        g.register_service("b", depends_on=["a"])

        result = g.get_dependents_recursive("a", max_depth=5)
        service_names = [name for name, _ in result]
        assert "b" in service_names
        assert len(result) <= 5


# =============================================================================
# 동작 검증 (Behavior) — topological_sort_subset
# =============================================================================


class TestTopologicalSortSubsetBehavior:
    """topological_sort_subset 동작 검증."""

    def test_leaves_first_returns_leaf_before_root(self, graph):
        """leaves_first: 리프(db) → 루트(gateway) 순서."""
        result = graph.topological_sort_subset(
            ["db", "api", "gateway"],
            direction="leaves_first",
        )
        assert result.index("db") < result.index("api")
        assert result.index("api") < result.index("gateway")

    def test_roots_first_returns_root_before_leaf(self, graph):
        """roots_first: 루트(gateway) → 리프(db) 순서."""
        result = graph.topological_sort_subset(
            ["db", "api", "gateway"],
            direction="roots_first",
        )
        assert result.index("gateway") < result.index("api")
        assert result.index("api") < result.index("db")

    def test_returns_empty_for_empty_input(self, graph):
        """빈 입력에 대해 빈 리스트 반환."""
        result = graph.topological_sort_subset([], direction="leaves_first")
        assert result == []

    def test_single_service_returns_as_is(self, graph):
        """서비스 1개면 그대로 반환."""
        result = graph.topological_sort_subset(["api"], direction="leaves_first")
        assert result == ["api"]

    def test_includes_all_input_services(self, graph):
        """입력 서비스가 모두 결과에 포함된다."""
        input_services = ["db", "cache", "api", "gateway"]
        result = graph.topological_sort_subset(input_services, direction="leaves_first")
        assert set(result) == set(input_services)
