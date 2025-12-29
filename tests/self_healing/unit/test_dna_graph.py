"""
Dependency Graph 단위 테스트

Phase 2: dna_graph.py 모듈 테스트
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from load_tests.utils.selfhealing.dna_graph import (
    DependencyGraph,
    ServiceNode,
    CascadeAnalysis,
    GraphReport,
    DependencyType,
    ServiceTier,
    IsolationStrategy,
)


class TestDependencyType:
    """DependencyType 열거형 테스트"""
    
    def test_dependency_type_values(self):
        """의존성 유형 값 테스트"""
        assert DependencyType.SYNC.value == "sync"
        assert DependencyType.ASYNC.value == "async"
        assert DependencyType.CACHE.value == "cache"
        assert DependencyType.DATABASE.value == "database"
        assert DependencyType.EXTERNAL.value == "external"
        assert DependencyType.EVENT.value == "event"


class TestServiceTier:
    """ServiceTier 열거형 테스트"""
    
    def test_service_tier_values(self):
        """서비스 등급 값 테스트"""
        assert ServiceTier.CRITICAL.value == "critical"
        assert ServiceTier.STANDARD.value == "standard"
        assert ServiceTier.NON_CRITICAL.value == "non-critical"


class TestIsolationStrategy:
    """IsolationStrategy 열거형 테스트"""
    
    def test_isolation_strategy_values(self):
        """격리 전략 값 테스트"""
        assert IsolationStrategy.CIRCUIT_BREAKER.value == "circuit_breaker"
        assert IsolationStrategy.BULKHEAD.value == "bulkhead"
        assert IsolationStrategy.RETRY.value == "retry"
        assert IsolationStrategy.FALLBACK.value == "fallback"
        assert IsolationStrategy.RATE_LIMITING.value == "rate_limiting"
        assert IsolationStrategy.TIMEOUT.value == "timeout"


class TestServiceNode:
    """ServiceNode 데이터 클래스 테스트"""
    
    def test_node_creation(self):
        """노드 생성 테스트"""
        node = ServiceNode(
            name="payment-service",
            tier=ServiceTier.CRITICAL,
        )
        
        assert node.name == "payment-service"
        assert node.tier == ServiceTier.CRITICAL
        assert node.circuit_breaker_enabled is True
    
    def test_add_dependency(self):
        """의존성 추가 테스트"""
        node = ServiceNode(name="order-service")
        node.add_dependency("payment-service", DependencyType.SYNC)
        node.add_dependency("inventory-service", DependencyType.ASYNC)
        
        assert "payment-service" in node.dependencies
        assert node.dependencies["payment-service"] == DependencyType.SYNC
        assert node.dependencies["inventory-service"] == DependencyType.ASYNC
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        node = ServiceNode(
            name="cart-service",
            tier=ServiceTier.STANDARD,
            timeout_ms=3000,
            retry_count=5,
        )
        node.add_dependency("product-service", DependencyType.CACHE)
        
        result = node.to_dict()
        
        assert result["name"] == "cart-service"
        assert result["tier"] == "standard"
        assert result["timeout_ms"] == 3000
        assert result["retry_count"] == 5
        assert result["dependencies"]["product-service"] == "cache"


class TestCascadeAnalysis:
    """CascadeAnalysis 데이터 클래스 테스트"""
    
    def test_analysis_creation(self):
        """분석 결과 생성 테스트"""
        analysis = CascadeAnalysis(
            failed_service="database",
            directly_affected=["payment-service", "order-service"],
            indirectly_affected=["cart-service", "notification-service"],
            total_blast_radius=4,
            critical_services_affected=["payment-service"],
            suggested_isolation=["circuit_breaker on database", "fallback for payment"],
            recovery_order=["database", "payment-service", "order-service", "cart-service"],
            impact_score=0.85,
        )
        
        assert analysis.failed_service == "database"
        assert len(analysis.directly_affected) == 2
        assert len(analysis.indirectly_affected) == 2
        assert analysis.total_blast_radius == 4
        assert analysis.impact_score == 0.85
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        analysis = CascadeAnalysis(
            failed_service="cache",
            directly_affected=["api-gateway"],
            indirectly_affected=[],
            total_blast_radius=1,
            critical_services_affected=[],
            suggested_isolation=["fallback"],
            recovery_order=["cache", "api-gateway"],
            impact_score=0.3,
        )
        
        result = analysis.to_dict()
        
        assert result["failed_service"] == "cache"
        assert result["total_blast_radius"] == 1
        assert result["impact_score"] == 0.3
    
    def test_to_markdown_high_impact(self):
        """마크다운 변환 - 높은 영향도"""
        analysis = CascadeAnalysis(
            failed_service="payment-gateway",
            directly_affected=["payment-service"],
            indirectly_affected=["order-service", "cart-service"],
            total_blast_radius=3,
            critical_services_affected=["payment-service"],
            suggested_isolation=["circuit_breaker"],
            recovery_order=["payment-gateway", "payment-service"],
            impact_score=0.8,
        )
        
        md = analysis.to_markdown()
        
        assert "payment-gateway" in md
        assert "🔴" in md  # 높은 영향도 아이콘
        assert "Critical Services" in md
    
    def test_to_markdown_low_impact(self):
        """마크다운 변환 - 낮은 영향도"""
        analysis = CascadeAnalysis(
            failed_service="logging-service",
            directly_affected=[],
            indirectly_affected=[],
            total_blast_radius=0,
            critical_services_affected=[],
            suggested_isolation=[],
            recovery_order=["logging-service"],
            impact_score=0.1,
        )
        
        md = analysis.to_markdown()
        
        assert "🟢" in md  # 낮은 영향도 아이콘


class TestDependencyGraph:
    """DependencyGraph 테스트"""
    
    def test_graph_initialization(self):
        """그래프 초기화 테스트"""
        graph = DependencyGraph()
        
        assert graph is not None
        assert len(graph.nodes) == 0
    
    def test_add_service(self):
        """서비스 추가 테스트"""
        graph = DependencyGraph()
        
        node = ServiceNode(name="payment-service", tier=ServiceTier.CRITICAL)
        graph.add_service(node)
        
        assert "payment-service" in graph.nodes
        assert graph.nodes["payment-service"] == node
    
    def test_add_service_with_dependencies(self):
        """의존성 있는 서비스 추가 테스트"""
        graph = DependencyGraph()
        
        db_node = ServiceNode(name="database")
        graph.add_service(db_node)
        
        payment_node = ServiceNode(name="payment-service")
        payment_node.add_dependency("database", DependencyType.DATABASE)
        graph.add_service(payment_node)
        
        # 역방향 의존성 확인
        dependents = graph.get_dependents("database")
        assert "payment-service" in dependents
    
    def test_get_dependents(self):
        """의존자 조회 테스트"""
        graph = DependencyGraph()
        
        graph.add_service(ServiceNode(name="database"))
        
        payment = ServiceNode(name="payment-service")
        payment.add_dependency("database", DependencyType.DATABASE)
        graph.add_service(payment)
        
        order = ServiceNode(name="order-service")
        order.add_dependency("database", DependencyType.DATABASE)
        graph.add_service(order)
        
        dependents = graph.get_dependents("database")
        
        assert "payment-service" in dependents
        assert "order-service" in dependents
    
    def test_analyze_cascade_failure(self):
        """연쇄 장애 분석 테스트"""
        graph = DependencyGraph()
        
        # 서비스 추가
        graph.add_service(ServiceNode(name="database", tier=ServiceTier.CRITICAL))
        
        payment = ServiceNode(name="payment-service", tier=ServiceTier.CRITICAL)
        payment.add_dependency("database", DependencyType.DATABASE)
        graph.add_service(payment)
        
        order = ServiceNode(name="order-service")
        order.add_dependency("payment-service", DependencyType.SYNC)
        graph.add_service(order)
        
        cart = ServiceNode(name="cart-service")
        cart.add_dependency("order-service", DependencyType.ASYNC)
        graph.add_service(cart)
        
        analysis = graph.analyze_cascade("database")
        
        assert isinstance(analysis, CascadeAnalysis)
        assert analysis.failed_service == "database"
        assert "payment-service" in analysis.directly_affected
    
    def test_add_service_from_dict(self):
        """딕셔너리에서 서비스 추가 테스트"""
        graph = DependencyGraph()
        
        config = {
            "name": "api-gateway",
            "tier": "critical",
            "timeout_ms": 3000,
            "depends_on": ["auth-service", "user-service"],
            "dependency_types": {
                "auth-service": "sync",
                "user-service": "async",
            },
        }
        
        node = graph.add_service_from_dict(config)
        
        assert node.name == "api-gateway"
        assert node.tier == ServiceTier.CRITICAL
        assert "auth-service" in node.dependencies


class TestGraphReport:
    """GraphReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = GraphReport(
            generated_at="2025-01-01T12:00:00",
            total_services=5,
            total_dependencies=8,
            critical_services=["payment", "auth"],
            most_depended=[("database", 4), ("cache", 2)],
            most_dependent=[("api-gateway", 3)],
            circular_dependencies=[],
            recommendations=["Add circuit breaker to database connections"],
        )
        
        assert report.total_services == 5
        assert report.total_dependencies == 8
        assert len(report.critical_services) == 2
        assert report.most_depended[0] == ("database", 4)
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        report = GraphReport(
            generated_at="2025-01-01T12:00:00",
            total_services=3,
            total_dependencies=2,
            critical_services=["payment"],
            most_depended=[],
            most_dependent=[],
            circular_dependencies=[],
            recommendations=[],
        )
        
        result = report.to_dict()
        
        assert result["total_services"] == 3
        assert result["total_dependencies"] == 2
        assert "critical_services" in result
