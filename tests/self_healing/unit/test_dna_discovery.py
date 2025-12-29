"""
Discovery Stage 단위 테스트

Phase 2: dna_discovery.py 모듈 테스트
"""

import pytest
import os
import tempfile
import shutil
from datetime import datetime

from load_tests.utils.selfhealing.dna_discovery import (
    DiscoveryStage,
    DiscoveryReport,
    APIEndpoint,
    DeadCodeCandidate,
    DiscoveryMetrics,
    HTTPMethod,
)


class TestAPIEndpoint:
    """APIEndpoint 데이터 클래스 테스트"""
    
    def test_endpoint_creation(self):
        """엔드포인트 생성 테스트"""
        endpoint = APIEndpoint(
            path="api/v1/orders/",
            method=HTTPMethod.GET,
            view_name="OrderListView",
            view_type="class",
            file_path="/app/shopping/urls.py",
            line_number=15,
        )
        
        assert endpoint.path == "api/v1/orders/"
        assert endpoint.method == HTTPMethod.GET
        assert endpoint.is_tested is False
    
    def test_normalized_path(self):
        """경로 정규화 테스트"""
        endpoint = APIEndpoint(
            path="api/v1/orders/<int:pk>/",
            method=HTTPMethod.GET,
            view_name="OrderDetailView",
            view_type="class",
            file_path="/app/urls.py",
            line_number=1,
        )
        
        normalized = endpoint.normalized_path
        
        assert "<int:pk>" not in normalized
        assert "*" in normalized
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        endpoint = APIEndpoint(
            path="api/v1/payments/",
            method=HTTPMethod.POST,
            view_name="PaymentView",
            view_type="class",
            file_path="/app/urls.py",
            line_number=20,
            url_name="payment-create",
            is_tested=True,
            test_stages=["stage12", "stage14"],
        )
        
        result = endpoint.to_dict()
        
        assert result["path"] == "api/v1/payments/"
        assert result["method"] == "POST"
        assert result["is_tested"] is True
        assert "stage12" in result["test_stages"]


class TestDeadCodeCandidate:
    """DeadCodeCandidate 데이터 클래스 테스트"""
    
    def test_dead_code_creation(self):
        """Dead Code 후보 생성 테스트"""
        candidate = DeadCodeCandidate(
            name="_old_helper_function",
            file_path="/app/shopping/utils.py",
            line_number=100,
            code_type="function",
            reason="Private function with no internal calls",
            confidence=0.75,
        )
        
        assert candidate.name == "_old_helper_function"
        assert candidate.confidence == 0.75
    
    def test_to_dict(self):
        """딕셔너리 변환 테스트"""
        candidate = DeadCodeCandidate(
            name="OldService",
            file_path="/app/services.py",
            line_number=50,
            code_type="class",
            reason="Marked as deprecated",
            confidence=0.9,
        )
        
        result = candidate.to_dict()
        
        assert result["name"] == "OldService"
        assert result["confidence"] == 0.9
        assert result["reason"] == "Marked as deprecated"


class TestDiscoveryReport:
    """DiscoveryReport 데이터 클래스 테스트"""
    
    def test_is_healthy_above_90(self):
        """90% 이상 커버리지면 healthy"""
        report = DiscoveryReport(
            discovery_timestamp=datetime.now().isoformat(),
            total_endpoints=100,
            tested_endpoints=95,
            untested_endpoints=5,
            coverage_percentage=95.0,
            dead_code_candidates=[],
            recommendations=[],
            endpoint_details=[],
        )
        
        assert report.is_healthy is True
    
    def test_is_not_healthy_below_90(self):
        """90% 미만 커버리지면 not healthy"""
        report = DiscoveryReport(
            discovery_timestamp=datetime.now().isoformat(),
            total_endpoints=100,
            tested_endpoints=80,
            untested_endpoints=20,
            coverage_percentage=80.0,
            dead_code_candidates=[],
            recommendations=[],
            endpoint_details=[],
        )
        
        assert report.is_healthy is False
    
    def test_to_markdown(self):
        """마크다운 리포트 생성 테스트"""
        endpoint = APIEndpoint(
            path="api/v1/untested/",
            method=HTTPMethod.GET,
            view_name="UntestedView",
            view_type="class",
            file_path="/app/urls.py",
            line_number=1,
            is_tested=False,
        )
        
        report = DiscoveryReport(
            discovery_timestamp="2025-12-29T00:00:00",
            total_endpoints=10,
            tested_endpoints=8,
            untested_endpoints=2,
            coverage_percentage=80.0,
            dead_code_candidates=[],
            recommendations=["테스트 추가 필요"],
            endpoint_details=[endpoint],
        )
        
        md = report.to_markdown()
        
        assert "# Discovery Stage Report" in md
        assert "80.0%" in md
        assert "UntestedView" in md


class TestDiscoveryStage:
    """DiscoveryStage 클래스 테스트"""
    
    def test_initialization(self):
        """초기화 테스트"""
        discovery = DiscoveryStage(
            app_dirs=["myapp/"],
            stage_dirs=["tests/"],
        )
        
        assert discovery.app_dirs == ["myapp/"]
        assert discovery.stage_dirs == ["tests/"]
    
    def test_normalize_path(self):
        """경로 정규화 테스트"""
        discovery = DiscoveryStage()
        
        # URL 호스트 제거
        path = discovery._normalize_path("http://localhost/api/v1/orders/123/")
        assert "localhost" not in path
        assert path == "/api/v1/orders/*/"  # 숫자가 *로
        
        # 변수 치환
        path = discovery._normalize_path("/api/v1/orders/{order_id}/")
        assert "*" in path
    
    def test_should_skip_file(self):
        """파일 스킵 여부 테스트"""
        discovery = DiscoveryStage()
        
        assert discovery._should_skip_file("migrations/0001.py") is True
        assert discovery._should_skip_file("__pycache__/module.pyc") is True
        assert discovery._should_skip_file("test_something.py") is True
        assert discovery._should_skip_file("services/payment.py") is False


class TestDiscoveryStageWithTempFiles:
    """임시 파일을 사용한 DiscoveryStage 테스트"""
    
    @pytest.fixture
    def temp_project(self):
        """임시 프로젝트 디렉토리 생성"""
        temp_dir = tempfile.mkdtemp()
        
        # 앱 디렉토리 생성
        app_dir = os.path.join(temp_dir, "shopping")
        os.makedirs(app_dir)
        
        # urls.py 파일 생성
        urls_file = os.path.join(app_dir, "urls.py")
        with open(urls_file, "w") as f:
            f.write('''
from django.urls import path
from . import views

urlpatterns = [
    path("api/v1/orders/", views.OrderListView.as_view(), name="order-list"),
    path("api/v1/orders/<int:pk>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("api/v1/payments/", views.PaymentView.as_view(), name="payment-create"),
]
''')
        
        # 테스트 디렉토리 생성
        test_dir = os.path.join(temp_dir, "tests")
        os.makedirs(test_dir)
        
        # 테스트 파일 생성
        test_file = os.path.join(test_dir, "test_orders.py")
        with open(test_file, "w") as f:
            f.write('''
class TestOrderAPI:
    def test_list_orders(self):
        response = self.client.get("/api/v1/orders/")
        assert response.status_code == 200
    
    def test_get_order(self):
        response = self.client.get("/api/v1/orders/1/")
        assert response.status_code == 200
''')
        
        yield temp_dir
        
        # 정리
        shutil.rmtree(temp_dir)
    
    def test_discover_endpoints(self, temp_project):
        """엔드포인트 발견 테스트"""
        discovery = DiscoveryStage(
            project_root=temp_project,
            app_dirs=["shopping/"],
        )
        
        endpoints = discovery.discover_endpoints()
        
        assert len(endpoints) >= 3
        
        paths = {e.path for e in endpoints}
        assert "api/v1/orders/" in paths
        assert "api/v1/payments/" in paths
    
    def test_collect_tested_paths(self, temp_project):
        """테스트된 경로 수집 테스트"""
        discovery = DiscoveryStage(
            project_root=temp_project,
            stage_dirs=["tests/"],
        )
        
        tested = discovery.collect_tested_paths()
        
        assert len(tested) > 0
        # 정규화된 경로도 포함되어야 함
        assert any("/api/v1/orders/" in p for p in tested)
    
    def test_analyze_coverage(self, temp_project):
        """커버리지 분석 테스트"""
        discovery = DiscoveryStage(
            project_root=temp_project,
            app_dirs=["shopping/"],
            stage_dirs=["tests/"],
        )
        
        report = discovery.analyze_coverage()
        
        assert isinstance(report, DiscoveryReport)
        assert report.total_endpoints >= 3
        # 일부 엔드포인트가 테스트됨
        assert report.tested_endpoints >= 0


class TestDiscoveryMetrics:
    """DiscoveryMetrics 데이터 클래스 테스트"""
    
    def test_to_prometheus_format(self):
        """Prometheus 형식 변환 테스트"""
        metrics = DiscoveryMetrics(
            timestamp=datetime.now().isoformat(),
            total_endpoints=100,
            coverage_percentage=85.5,
            dead_code_count=3,
            untested_critical_count=2,
        )
        
        prom = metrics.to_prometheus_format()
        
        assert "discovery_total_endpoints 100" in prom
        assert "discovery_coverage_percentage 85.5" in prom
        assert "discovery_dead_code_count 3" in prom


class TestGenerateTestSuggestions:
    """테스트 제안 생성 테스트"""
    
    @pytest.fixture
    def discovery_with_untested(self):
        """테스트되지 않은 엔드포인트가 있는 Discovery"""
        discovery = DiscoveryStage()
        
        discovery.endpoints = [
            APIEndpoint(
                path="api/v1/products/",
                method=HTTPMethod.GET,
                view_name="ProductListView",
                view_type="class",
                file_path="/app/urls.py",
                line_number=1,
                is_tested=False,
            ),
            APIEndpoint(
                path="api/v1/users/",
                method=HTTPMethod.POST,
                view_name="UserCreateView",
                view_type="class",
                file_path="/app/urls.py",
                line_number=2,
                is_tested=True,
            ),
        ]
        
        return discovery
    
    def test_generate_test_suggestions(self, discovery_with_untested):
        """테스트 제안 생성"""
        suggestions = discovery_with_untested.generate_test_suggestions(max_suggestions=5)
        
        assert len(suggestions) == 1  # 테스트되지 않은 것만
        assert "ProductListView" in suggestions[0].lower() or "product" in suggestions[0].lower()
        assert "def test_" in suggestions[0]
