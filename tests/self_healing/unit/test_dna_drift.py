"""
DNA Drift Detection 단위 테스트

Phase 2: dna_drift.py 모듈 테스트
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock

from load_tests.utils.selfhealing.dna_drift import (
    DNADriftDetector,
    DiscoveredFeature,
    DriftReport,
    FeatureType,
    SCAN_PATTERNS,
)


class TestDiscoveredFeature:
    """DiscoveredFeature 데이터 클래스 테스트"""
    
    def test_feature_creation(self):
        """기본 기능 생성 테스트"""
        feature = DiscoveredFeature(
            name="PaymentService",
            feature_type=FeatureType.SERVICE,
            file_path="/app/shopping/services/payment.py",
            line_number=10,
        )
        
        assert feature.name == "PaymentService"
        assert feature.feature_type == FeatureType.SERVICE
        assert feature.is_mapped_to_dna is False
    
    def test_feature_to_dict(self):
        """딕셔너리 변환 테스트"""
        feature = DiscoveredFeature(
            name="OrderHandler",
            feature_type=FeatureType.HANDLER,
            file_path="/app/handlers/order.py",
            line_number=25,
            suggested_modules=["circuit_breaker", "dlq"],
        )
        
        result = feature.to_dict()
        
        assert result["name"] == "OrderHandler"
        assert result["feature_type"] == "handler"
        assert result["suggested_modules"] == ["circuit_breaker", "dlq"]


class TestDriftReport:
    """DriftReport 데이터 클래스 테스트"""
    
    def test_drift_percentage_calculation(self):
        """드리프트 비율 계산 테스트"""
        feature1 = DiscoveredFeature(
            name="ServiceA",
            feature_type=FeatureType.SERVICE,
            file_path="/app/a.py",
            line_number=1,
            is_mapped_to_dna=True,
        )
        feature2 = DiscoveredFeature(
            name="ServiceB",
            feature_type=FeatureType.SERVICE,
            file_path="/app/b.py",
            line_number=1,
            is_mapped_to_dna=False,
        )
        
        report = DriftReport(
            scan_timestamp="2025-12-29T00:00:00",
            total_features_found=2,
            mapped_features=1,
            unmapped_features=1,
            new_features=[feature2],
            deprecated_dna=[],
            recommendations=[],
            scan_dirs=["shopping/"],
            stage_dirs=["load_tests/"],
        )
        
        assert report.drift_percentage == 50.0
    
    def test_drift_percentage_zero_features(self):
        """기능이 없을 때 드리프트 비율"""
        report = DriftReport(
            scan_timestamp="2025-12-29T00:00:00",
            total_features_found=0,
            mapped_features=0,
            unmapped_features=0,
            new_features=[],
            deprecated_dna=[],
            recommendations=[],
            scan_dirs=[],
            stage_dirs=[],
        )
        
        assert report.drift_percentage == 0.0
    
    def test_is_clean(self):
        """드리프트 없음 상태 테스트"""
        clean_report = DriftReport(
            scan_timestamp="2025-12-29T00:00:00",
            total_features_found=5,
            mapped_features=5,
            unmapped_features=0,
            new_features=[],
            deprecated_dna=[],
            recommendations=[],
            scan_dirs=[],
            stage_dirs=[],
        )
        
        assert clean_report.is_clean is True
    
    def test_is_not_clean_with_unmapped(self):
        """매핑되지 않은 기능이 있을 때"""
        feature = DiscoveredFeature(
            name="UnmappedService",
            feature_type=FeatureType.SERVICE,
            file_path="/app/unmapped.py",
            line_number=1,
        )
        
        report = DriftReport(
            scan_timestamp="2025-12-29T00:00:00",
            total_features_found=5,
            mapped_features=4,
            unmapped_features=1,
            new_features=[feature],
            deprecated_dna=[],
            recommendations=[],
            scan_dirs=[],
            stage_dirs=[],
        )
        
        assert report.is_clean is False
    
    def test_to_markdown(self):
        """마크다운 리포트 생성 테스트"""
        report = DriftReport(
            scan_timestamp="2025-12-29T00:00:00",
            total_features_found=10,
            mapped_features=8,
            unmapped_features=2,
            new_features=[],
            deprecated_dna=["OldService"],
            recommendations=["테스트 추가 필요"],
            scan_dirs=["shopping/"],
            stage_dirs=["load_tests/"],
        )
        
        md = report.to_markdown()
        
        assert "# DNA Drift Report" in md
        assert "10" in md  # total features
        assert "OldService" in md
        assert "테스트 추가 필요" in md


class TestDNADriftDetector:
    """DNADriftDetector 클래스 테스트"""
    
    def test_initialization(self):
        """초기화 테스트"""
        detector = DNADriftDetector(
            code_dirs=["src/"],
            stage_dirs=["tests/"],
        )
        
        assert detector.code_dirs == ["src/"]
        assert detector.stage_dirs == ["tests/"]
    
    def test_default_modules_by_type(self):
        """기능 유형별 기본 모듈 추천 테스트"""
        expected_modules = {
            FeatureType.SERVICE: ["circuit_breaker", "error_budget", "health"],
            FeatureType.HANDLER: ["circuit_breaker", "dlq", "health"],
            FeatureType.TASK: ["dlq", "observability", "health"],
        }
        
        for ftype, modules in expected_modules.items():
            assert ftype in DNADriftDetector.DEFAULT_MODULES_BY_TYPE
            assert set(DNADriftDetector.DEFAULT_MODULES_BY_TYPE[ftype]) == set(modules)
    
    def test_should_exclude_migrations(self):
        """마이그레이션 디렉토리 제외 테스트"""
        detector = DNADriftDetector()
        
        assert detector._should_exclude("shopping/migrations/0001_initial.py") is True
    
    def test_should_exclude_pycache(self):
        """__pycache__ 디렉토리 제외 테스트"""
        detector = DNADriftDetector()
        
        assert detector._should_exclude("shopping/__pycache__/module.pyc") is True
    
    def test_should_not_exclude_normal_file(self):
        """일반 파일은 제외하지 않음"""
        detector = DNADriftDetector()
        
        assert detector._should_exclude("shopping/services/payment.py") is False
    
    def test_generate_dna_suggestion(self):
        """DNA 제안 생성 테스트"""
        detector = DNADriftDetector()
        
        feature = DiscoveredFeature(
            name="NewPaymentService",
            feature_type=FeatureType.SERVICE,
            file_path="/app/shopping/services/new_payment.py",
            line_number=15,
            suggested_modules=["circuit_breaker", "error_budget", "health"],
        )
        
        suggestion = detector.generate_dna_suggestion(feature)
        
        assert "STAGE_DNA" in suggestion
        assert "NewPaymentService" in suggestion
        assert "circuit_breaker" in suggestion
        assert "rollback_strategy" in suggestion


class TestDNADriftDetectorWithTempFiles:
    """임시 파일을 사용한 DNADriftDetector 테스트"""
    
    @pytest.fixture
    def temp_project(self):
        """임시 프로젝트 디렉토리 생성"""
        temp_dir = tempfile.mkdtemp()
        
        # 코드 디렉토리 생성
        code_dir = os.path.join(temp_dir, "shopping", "services")
        os.makedirs(code_dir)
        
        # 테스트 Python 파일 생성
        service_file = os.path.join(code_dir, "payment.py")
        with open(service_file, "w") as f:
            f.write('''
"""Payment Service"""

class PaymentService:
    """결제 서비스"""
    def process(self):
        pass

class OrderHandler:
    """주문 핸들러"""
    pass
''')
        
        yield temp_dir
        
        # 정리
        shutil.rmtree(temp_dir)
    
    def test_scan_codebase(self, temp_project):
        """코드베이스 스캔 테스트"""
        detector = DNADriftDetector(
            code_dirs=["shopping/"],
            project_root=temp_project,
        )
        
        features = detector.scan_codebase()
        
        assert len(features) == 2
        
        names = {f.name for f in features}
        assert "PaymentService" in names
        assert "OrderHandler" in names
    
    def test_detect_drift(self, temp_project):
        """드리프트 감지 테스트"""
        detector = DNADriftDetector(
            code_dirs=["shopping/"],
            stage_dirs=["load_tests/"],  # 존재하지 않음
            project_root=temp_project,
        )
        
        report = detector.detect_drift()
        
        # Stage DNA가 없으므로 모든 기능이 unmapped
        assert report.total_features_found == 2
        assert report.unmapped_features == 2
        assert report.is_clean is False


class TestFeatureTypeDetection:
    """기능 유형 감지 테스트"""
    
    @pytest.fixture
    def temp_dir_with_various_types(self):
        """다양한 유형의 코드가 있는 임시 디렉토리"""
        temp_dir = tempfile.mkdtemp()
        
        code_dir = os.path.join(temp_dir, "shopping")
        os.makedirs(code_dir)
        
        # 다양한 유형의 클래스가 있는 파일
        code_file = os.path.join(code_dir, "app.py")
        with open(code_file, "w") as f:
            f.write('''
from celery import shared_task

class PaymentService:
    pass

class OrderHandler:
    pass

class UserManager:
    pass

class ProductView:
    pass

class CartViewSet:
    pass

class LoggingMiddleware:
    pass

@shared_task
def send_email():
    pass
''')
        
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    def test_detect_all_feature_types(self, temp_dir_with_various_types):
        """모든 기능 유형 감지 테스트"""
        detector = DNADriftDetector(
            code_dirs=["shopping/"],
            project_root=temp_dir_with_various_types,
        )
        
        features = detector.scan_codebase()
        
        type_map = {f.name: f.feature_type for f in features}
        
        assert type_map.get("PaymentService") == FeatureType.SERVICE
        assert type_map.get("OrderHandler") == FeatureType.HANDLER
        assert type_map.get("UserManager") == FeatureType.MANAGER
        assert type_map.get("ProductView") == FeatureType.VIEW
        assert type_map.get("CartViewSet") == FeatureType.VIEWSET
        assert type_map.get("LoggingMiddleware") == FeatureType.MIDDLEWARE
        assert type_map.get("send_email") == FeatureType.TASK
