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
    ServiceDependency,
    CascadeAnalysis,
    GraphReport,
    RecoveryOrder,
    ServiceType,
    DependencyType,
    ServiceStatus,
)


class TestServiceType:
    """ServiceType 열거형 테스트"""
    
    def test_service_type_values(self):
        """서비스 타입 값 테스트"""
        assert ServiceType.DATABASE.value == "database"
        assert ServiceType.CACHE.value == "cache"
        assert ServiceType.API.value == "api"
        assert ServiceType.MESSAGE_QUEUE.value == "message_queue"
        assert ServiceType.EXTERNAL.value == "external"


class TestDependencyType:
    """DependencyType 열거형 테스트"""
    
    def test_dependency_type_values(self):
        """의존성 타입 값 테스트"""
        assert DependencyType.HARD.value == "hard"
        assert DependencyType.SOFT.value == "soft"
        assert DependencyType.OPTIONAL.value == "optional"


class TestServiceStatus:
    """ServiceStatus 열거형 테스트"""
    
    def test_status_values(self):
        """상태 값 테스트"""
        assert ServiceStatus.HEALTHY.value == "healthy"
        assert ServiceStatus.DEGRADED.value == "degraded"
        assert ServiceStatus.FAILED.value == "failed"
        assert ServiceStatus.UNKNOWN.value == "unknown"


class TestServiceNode:
    """ServiceNode 데이터 클래스 테스트"""
    
    def test_node_creation(self):
        """노드 생성 테스트"""
        node = ServiceNode(
            id="postgres-main",
            name="PostgreSQL Main",
            service_type=ServiceType.DATABASE,
            criticality=10,
        )
        
        assert node.id == "postgres-main"
        assert node.service_type == ServiceType.DATABASE
        assert node.criticality == 10
    
    def test_default_status(self):
        """기본 상태 테스트"""
        node = ServiceNode(
            id="redis-cache",
            name="Redis Cache",
            service_type=ServiceType.CACHE,
        )
        
        assert node.status == ServiceStatus.UNKNOWN
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        node = ServiceNode(
            id="api-gateway",
            name="API Gateway",
            service_type=ServiceType.API,
            criticality=8,
            status=ServiceStatus.HEALTHY,
        )
        
        result = node.to_dict()
        
        assert result["id"] == "api-gateway"
        assert result["service_type"] == "api"
        assert result["status"] == "healthy"


class TestServiceDependency:
    """ServiceDependency 데이터 클래스 테스트"""
    
    def test_dependency_creation(self):
        """의존성 생성 테스트"""
        dep = ServiceDependency(
            source="order-service",
            target="postgres-main",
            dependency_type=DependencyType.HARD,
            weight=1.0,
        )
        
        assert dep.source == "order-service"
        assert dep.target == "postgres-main"
        assert dep.dependency_type == DependencyType.HARD
    
    def test_is_critical_hard_dependency(self):
        """Hard 의존성은 critical"""
        dep = ServiceDependency(
            source="a",
            target="b",
            dependency_type=DependencyType.HARD,
        )
        
        assert dep.is_critical is True
    
    def test_is_not_critical_soft_dependency(self):
        """Soft 의존성은 non-critical"""
        dep = ServiceDependency(
            source="a",
            target="b",
            dependency_type=DependencyType.SOFT,
        )
        
        assert dep.is_critical is False


class TestDependencyGraph:
    """DependencyGraph 클래스 테스트"""
    
    @pytest.fixture
    def empty_graph(self):
        """빈 그래프"""
        return DependencyGraph()
    
    @pytest.fixture
    def simple_graph(self):
        """간단한 그래프"""
        graph = DependencyGraph()
        
        # 노드 추가
        graph.add_node(ServiceNode(
            id="postgres",
            name="PostgreSQL",
            service_type=ServiceType.DATABASE,
            criticality=10,
        ))
        graph.add_node(ServiceNode(
            id="redis",
            name="Redis",
            service_type=ServiceType.CACHE,
            criticality=7,
        ))
        graph.add_node(ServiceNode(
            id="order-service",
            name="Order Service",
            service_type=ServiceType.API,
            criticality=9,
        ))
        
        # 엣지 추가
        graph.add_dependency(ServiceDependency(
            source="order-service",
            target="postgres",
            dependency_type=DependencyType.HARD,
        ))
        graph.add_dependency(ServiceDependency(
            source="order-service",
            target="redis",
            dependency_type=DependencyType.SOFT,
        ))
        
        return graph
    
    def test_add_node(self, empty_graph):
        """노드 추가 테스트"""
        node = ServiceNode(
            id="test-service",
            name="Test Service",
            service_type=ServiceType.API,
        )
        
        empty_graph.add_node(node)
        
        assert "test-service" in empty_graph.nodes
    
    def test_add_dependency(self, empty_graph):
        """의존성 추가 테스트"""
        empty_graph.add_node(ServiceNode(id="a", name="A", service_type=ServiceType.API))
        empty_graph.add_node(ServiceNode(id="b", name="B", service_type=ServiceType.API))
        
        dep = ServiceDependency(
            source="a",
            target="b",
            dependency_type=DependencyType.HARD,
        )
        
        empty_graph.add_dependency(dep)
        
        assert len(empty_graph.dependencies) == 1
    
    def test_get_node(self, simple_graph):
        """노드 조회 테스트"""
        node = simple_graph.get_node("postgres")
        
        assert node is not None
        assert node.name == "PostgreSQL"
    
    def test_get_dependencies_of(self, simple_graph):
        """서비스의 의존성 조회"""
        deps = simple_graph.get_dependencies_of("order-service")
        
        assert len(deps) == 2
        targets = {d.target for d in deps}
        assert "postgres" in targets
        assert "redis" in targets
    
    def test_get_dependents_of(self, simple_graph):
        """서비스를 의존하는 서비스 조회"""
        dependents = simple_graph.get_dependents_of("postgres")
        
        assert len(dependents) == 1
        assert dependents[0].source == "order-service"


class TestCascadeAnalysis:
    """CascadeAnalysis 클래스 테스트"""
    
    def test_analysis_creation(self):
        """분석 결과 생성 테스트"""
        analysis = CascadeAnalysis(
            failed_service="postgres",
            affected_services=["order-service", "payment-service"],
            cascade_depth=1,
            total_impact=0.85,
        )
        
        assert analysis.failed_service == "postgres"
        assert len(analysis.affected_services) == 2
        assert analysis.cascade_depth == 1
    
    def test_impact_score(self):
        """영향 점수 테스트"""
        analysis = CascadeAnalysis(
            failed_service="redis",
            affected_services=["a", "b", "c"],
            cascade_depth=2,
            total_impact=0.6,
        )
        
        assert analysis.total_impact == 0.6
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        analysis = CascadeAnalysis(
            failed_service="db",
            affected_services=["svc1", "svc2"],
            cascade_depth=1,
            total_impact=0.9,
        )
        
        result = analysis.to_dict()
        
        assert result["failed_service"] == "db"
        assert result["cascade_depth"] == 1


class TestDependencyGraphCascadeAnalysis:
    """DependencyGraph 캐스케이드 분석 테스트"""
    
    @pytest.fixture
    def cascade_graph(self):
        """캐스케이드 분석용 그래프"""
        graph = DependencyGraph()
        
        # 레이어 구조: DB → Services → Gateway
        graph.add_node(ServiceNode(
            id="db",
            name="Database",
            service_type=ServiceType.DATABASE,
            criticality=10,
        ))
        graph.add_node(ServiceNode(
            id="order-svc",
            name="Order Service",
            service_type=ServiceType.API,
            criticality=8,
        ))
        graph.add_node(ServiceNode(
            id="payment-svc",
            name="Payment Service",
            service_type=ServiceType.API,
            criticality=9,
        ))
        graph.add_node(ServiceNode(
            id="gateway",
            name="API Gateway",
            service_type=ServiceType.API,
            criticality=10,
        ))
        
        # Dependencies
        graph.add_dependency(ServiceDependency(
            source="order-svc",
            target="db",
            dependency_type=DependencyType.HARD,
        ))
        graph.add_dependency(ServiceDependency(
            source="payment-svc",
            target="db",
            dependency_type=DependencyType.HARD,
        ))
        graph.add_dependency(ServiceDependency(
            source="gateway",
            target="order-svc",
            dependency_type=DependencyType.HARD,
        ))
        graph.add_dependency(ServiceDependency(
            source="gateway",
            target="payment-svc",
            dependency_type=DependencyType.HARD,
        ))
        
        return graph
    
    def test_analyze_cascade_failure(self, cascade_graph):
        """캐스케이드 실패 분석 테스트"""
        analysis = cascade_graph.analyze_cascade_failure("db")
        
        assert isinstance(analysis, CascadeAnalysis)
        assert analysis.failed_service == "db"
        # DB 실패 → order-svc, payment-svc, gateway 영향
        assert len(analysis.affected_services) >= 2
    
    def test_cascade_depth(self, cascade_graph):
        """캐스케이드 깊이 테스트"""
        analysis = cascade_graph.analyze_cascade_failure("db")
        
        # DB → Services → Gateway = 2 레벨
        assert analysis.cascade_depth >= 2
    
    def test_leaf_node_no_cascade(self, cascade_graph):
        """리프 노드는 캐스케이드 없음"""
        analysis = cascade_graph.analyze_cascade_failure("gateway")
        
        assert len(analysis.affected_services) == 0


class TestRecoveryOrder:
    """RecoveryOrder 클래스 테스트"""
    
    def test_recovery_order_creation(self):
        """복구 순서 생성 테스트"""
        order = RecoveryOrder(
            services=["db", "order-svc", "gateway"],
            reasoning=["DB first", "Then services", "Finally gateway"],
        )
        
        assert order.services == ["db", "order-svc", "gateway"]
    
    def test_to_list(self):
        """리스트 변환 테스트"""
        order = RecoveryOrder(
            services=["a", "b", "c"],
            reasoning=["Step 1", "Step 2", "Step 3"],
        )
        
        result = order.to_list()
        
        assert len(result) == 3
        assert result[0]["service"] == "a"


class TestDependencyGraphRecoveryOrder:
    """DependencyGraph 복구 순서 테스트"""
    
    @pytest.fixture
    def recovery_graph(self):
        """복구 순서 테스트용 그래프"""
        graph = DependencyGraph()
        
        # 의존성 체인: A → B → C
        graph.add_node(ServiceNode(id="A", name="A", service_type=ServiceType.DATABASE))
        graph.add_node(ServiceNode(id="B", name="B", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="C", name="C", service_type=ServiceType.API))
        
        graph.add_dependency(ServiceDependency(
            source="B",
            target="A",
            dependency_type=DependencyType.HARD,
        ))
        graph.add_dependency(ServiceDependency(
            source="C",
            target="B",
            dependency_type=DependencyType.HARD,
        ))
        
        return graph
    
    def test_calculate_recovery_order(self, recovery_graph):
        """복구 순서 계산 테스트"""
        order = recovery_graph.calculate_recovery_order()
        
        assert isinstance(order, RecoveryOrder)
        # A는 B보다 먼저, B는 C보다 먼저
        a_idx = order.services.index("A")
        b_idx = order.services.index("B")
        c_idx = order.services.index("C")
        
        assert a_idx < b_idx < c_idx


class TestGraphReport:
    """GraphReport 데이터 클래스 테스트"""
    
    def test_report_creation(self):
        """리포트 생성 테스트"""
        report = GraphReport(
            generated_at=datetime.now().isoformat(),
            total_nodes=10,
            total_edges=15,
            critical_paths=[],
            single_points_of_failure=[],
            circular_dependencies=[],
        )
        
        assert report.total_nodes == 10
        assert report.total_edges == 15
    
    def test_has_circular_dependencies_false(self):
        """순환 의존성 없음"""
        report = GraphReport(
            generated_at=datetime.now().isoformat(),
            total_nodes=5,
            total_edges=4,
            critical_paths=[],
            single_points_of_failure=[],
            circular_dependencies=[],
        )
        
        assert report.has_circular_dependencies is False
    
    def test_has_circular_dependencies_true(self):
        """순환 의존성 있음"""
        report = GraphReport(
            generated_at=datetime.now().isoformat(),
            total_nodes=5,
            total_edges=6,
            critical_paths=[],
            single_points_of_failure=[],
            circular_dependencies=[["A", "B", "C", "A"]],
        )
        
        assert report.has_circular_dependencies is True
    
    def test_to_markdown(self):
        """마크다운 변환 테스트"""
        report = GraphReport(
            generated_at="2025-12-29T00:00:00",
            total_nodes=5,
            total_edges=4,
            critical_paths=[["db", "order-svc", "gateway"]],
            single_points_of_failure=["db"],
            circular_dependencies=[],
        )
        
        md = report.to_markdown()
        
        assert "# Dependency Graph Report" in md
        assert "5" in md  # nodes
        assert "db" in md  # SPOF


class TestMermaidDiagram:
    """Mermaid 다이어그램 생성 테스트"""
    
    @pytest.fixture
    def diagram_graph(self):
        """다이어그램 테스트용 그래프"""
        graph = DependencyGraph()
        
        graph.add_node(ServiceNode(id="db", name="Database", service_type=ServiceType.DATABASE))
        graph.add_node(ServiceNode(id="api", name="API", service_type=ServiceType.API))
        
        graph.add_dependency(ServiceDependency(
            source="api",
            target="db",
            dependency_type=DependencyType.HARD,
        ))
        
        return graph
    
    def test_to_mermaid(self, diagram_graph):
        """Mermaid 다이어그램 생성 테스트"""
        mermaid = diagram_graph.to_mermaid()
        
        assert "graph" in mermaid or "flowchart" in mermaid
        assert "db" in mermaid
        assert "api" in mermaid
        assert "-->" in mermaid


class TestCircularDependencyDetection:
    """순환 의존성 감지 테스트"""
    
    def test_detect_circular_dependency(self):
        """순환 의존성 감지"""
        graph = DependencyGraph()
        
        # A → B → C → A (순환)
        graph.add_node(ServiceNode(id="A", name="A", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="B", name="B", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="C", name="C", service_type=ServiceType.API))
        
        graph.add_dependency(ServiceDependency(source="A", target="B", dependency_type=DependencyType.HARD))
        graph.add_dependency(ServiceDependency(source="B", target="C", dependency_type=DependencyType.HARD))
        graph.add_dependency(ServiceDependency(source="C", target="A", dependency_type=DependencyType.HARD))
        
        cycles = graph.detect_circular_dependencies()
        
        assert len(cycles) > 0
    
    def test_no_circular_dependency(self):
        """순환 의존성 없음"""
        graph = DependencyGraph()
        
        # A → B → C (순환 없음)
        graph.add_node(ServiceNode(id="A", name="A", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="B", name="B", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="C", name="C", service_type=ServiceType.API))
        
        graph.add_dependency(ServiceDependency(source="A", target="B", dependency_type=DependencyType.HARD))
        graph.add_dependency(ServiceDependency(source="B", target="C", dependency_type=DependencyType.HARD))
        
        cycles = graph.detect_circular_dependencies()
        
        assert len(cycles) == 0


class TestSinglePointOfFailure:
    """단일 장애점 감지 테스트"""
    
    def test_find_spof(self):
        """단일 장애점 찾기"""
        graph = DependencyGraph()
        
        # 모든 서비스가 하나의 DB에 의존
        graph.add_node(ServiceNode(id="db", name="DB", service_type=ServiceType.DATABASE, criticality=10))
        graph.add_node(ServiceNode(id="svc1", name="Service 1", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="svc2", name="Service 2", service_type=ServiceType.API))
        graph.add_node(ServiceNode(id="svc3", name="Service 3", service_type=ServiceType.API))
        
        graph.add_dependency(ServiceDependency(source="svc1", target="db", dependency_type=DependencyType.HARD))
        graph.add_dependency(ServiceDependency(source="svc2", target="db", dependency_type=DependencyType.HARD))
        graph.add_dependency(ServiceDependency(source="svc3", target="db", dependency_type=DependencyType.HARD))
        
        spof_list = graph.find_single_points_of_failure()
        
        assert "db" in spof_list
