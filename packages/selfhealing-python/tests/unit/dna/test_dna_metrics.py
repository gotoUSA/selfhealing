"""
DNA-Metrics Conflict Detection 단위 테스트

Phase 3 테스트
"""

import pytest
from datetime import datetime

from load_tests.utils.selfhealing.dna_metrics import (
    ConflictType,
    SLAMetrics,
    DNAUtilization,
    ConflictAnalysis,
    DNAMetricsAnalyzer,
    generate_metrics_report_section,
)


class TestSLAMetrics:
    """SLAMetrics 테스트"""
    
    def test_sla_met_all_passing(self):
        """SLA 모두 충족"""
        sla = SLAMetrics(
            p50_ms=100,
            p95_ms=200,
            p99_ms=250,
            error_rate=0.005,
            throughput_rps=1000,
            availability=0.9995,
            target_p99_ms=300,
            target_error_rate=0.01,
            target_availability=0.999,
        )
        assert sla.is_sla_met is True
    
    def test_sla_not_met_p99(self):
        """P99 초과로 SLA 미달"""
        sla = SLAMetrics(
            p50_ms=100,
            p95_ms=200,
            p99_ms=350,  # 초과
            error_rate=0.005,
            throughput_rps=1000,
            availability=0.9995,
            target_p99_ms=300,
        )
        assert sla.is_sla_met is False
    
    def test_sla_not_met_error_rate(self):
        """에러율 초과로 SLA 미달"""
        sla = SLAMetrics(
            p50_ms=100,
            p95_ms=200,
            p99_ms=250,
            error_rate=0.02,  # 초과
            throughput_rps=1000,
            availability=0.9995,
            target_error_rate=0.01,
        )
        assert sla.is_sla_met is False
    
    def test_sla_not_met_availability(self):
        """가용성 미달로 SLA 미달"""
        sla = SLAMetrics(
            p50_ms=100,
            p95_ms=200,
            p99_ms=250,
            error_rate=0.005,
            throughput_rps=1000,
            availability=0.99,  # 미달
            target_availability=0.999,
        )
        assert sla.is_sla_met is False


class TestDNAUtilization:
    """DNAUtilization 테스트"""
    
    def test_full_coverage(self):
        """100% 적용률"""
        util = DNAUtilization(
            required_modules=["circuit_breaker", "dlq", "health"],
            applied_modules=["circuit_breaker", "dlq", "health"],
            optional_modules=["observability"],
            active_optional=[],
        )
        assert util.required_coverage == 100.0
    
    def test_partial_coverage(self):
        """부분 적용률"""
        util = DNAUtilization(
            required_modules=["circuit_breaker", "dlq", "health"],
            applied_modules=["circuit_breaker", "dlq"],  # health 미적용
            optional_modules=[],
            active_optional=[],
        )
        assert util.required_coverage == pytest.approx(66.67, rel=0.01)
    
    def test_empty_required(self):
        """필수 모듈 없으면 100%"""
        util = DNAUtilization(
            required_modules=[],
            applied_modules=["circuit_breaker"],
            optional_modules=[],
            active_optional=[],
        )
        assert util.required_coverage == 100.0
    
    def test_optional_usage(self):
        """선택 모듈 활용률"""
        util = DNAUtilization(
            required_modules=["circuit_breaker"],
            applied_modules=["circuit_breaker", "observability"],
            optional_modules=["observability", "chaos"],
            active_optional=["observability"],
        )
        assert util.optional_usage == 50.0


class TestConflictAnalysis:
    """ConflictAnalysis 테스트"""
    
    def test_to_dict(self):
        """딕셔너리 변환"""
        sla = SLAMetrics(
            p50_ms=100, p95_ms=200, p99_ms=250,
            error_rate=0.005, throughput_rps=1000, availability=0.9995,
        )
        dna_util = DNAUtilization(
            required_modules=["cb"], applied_modules=["cb"],
            optional_modules=[], active_optional=[],
        )
        analysis = ConflictAnalysis(
            stage_name="Test Stage",
            analysis_timestamp="2025-01-01T00:00:00",
            conflict_type=ConflictType.DNA_SUFFICIENT_SLA_OK,
            dna_utilization=dna_util,
            sla_metrics=sla,
            gap_score=0,
            recommendations=["OK"],
            suggested_new_features=[],
        )
        
        result = analysis.to_dict()
        assert result["stage_name"] == "Test Stage"
        assert result["conflict_type"] == "dna_ok_sla_ok"
        assert result["sla_met"] is True


class TestDNAMetricsAnalyzer:
    """DNAMetricsAnalyzer 테스트"""
    
    def test_analyze_ideal_state(self):
        """이상적 상태: DNA 충족 + SLA 달성"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {
            "required_modules": ["circuit_breaker", "dlq"],
            "optional_modules": [],
            "p99_threshold_ms": 300,
            "error_rate_threshold": 0.01,
            "availability_threshold": 0.999,
        }
        metrics = {
            "p50": 100,
            "p95": 200,
            "p99": 250,
            "error_rate": 0.005,
            "throughput": 1000,
            "availability": 0.9995,
        }
        applied = ["circuit_breaker", "dlq"]
        
        result = analyzer.analyze("Test Stage", dna, metrics, applied)
        
        assert result.conflict_type == ConflictType.DNA_SUFFICIENT_SLA_OK
        assert result.gap_score < 10
        assert "✅" in result.recommendations[0]
    
    def test_analyze_dna_ok_sla_fail(self):
        """DNA 충족하지만 SLA 미달"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {
            "required_modules": ["circuit_breaker", "dlq"],
            "p99_threshold_ms": 300,
            "error_rate_threshold": 0.01,
        }
        metrics = {
            "p50": 200,
            "p95": 400,
            "p99": 500,  # 초과!
            "error_rate": 0.02,  # 초과!
            "throughput": 1000,
            "availability": 0.9995,
        }
        applied = ["circuit_breaker", "dlq"]
        
        result = analyzer.analyze("Test Stage", dna, metrics, applied)
        
        assert result.conflict_type == ConflictType.DNA_SUFFICIENT_SLA_FAIL
        assert result.gap_score > 30
        assert len(result.suggested_new_features) > 0
        assert "🔴" in result.recommendations[0]
    
    def test_analyze_dna_fail_sla_ok(self):
        """DNA 미적용인데 SLA 달성 - 과잉 선언"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {
            "required_modules": ["circuit_breaker", "dlq", "health"],
            "p99_threshold_ms": 300,
        }
        metrics = {
            "p50": 100,
            "p95": 200,
            "p99": 250,
            "error_rate": 0.005,
            "availability": 0.9995,
        }
        applied = ["circuit_breaker", "dlq"]  # health 미적용
        
        result = analyzer.analyze("Test Stage", dna, metrics, applied)
        
        assert result.conflict_type == ConflictType.DNA_INSUFFICIENT_SLA_OK
        assert "🟡" in result.recommendations[0]
        assert "health" in str(result.recommendations)
    
    def test_analyze_dna_fail_sla_fail(self):
        """DNA 미적용 + SLA 미달"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {
            "required_modules": ["circuit_breaker", "dlq"],
            "p99_threshold_ms": 300,
        }
        metrics = {
            "p50": 200,
            "p95": 400,
            "p99": 500,
            "error_rate": 0.02,
            "availability": 0.99,
        }
        applied = ["circuit_breaker"]  # dlq 미적용
        
        result = analyzer.analyze("Test Stage", dna, metrics, applied)
        
        assert result.conflict_type == ConflictType.DNA_INSUFFICIENT_SLA_FAIL
        assert "🔴" in result.recommendations[0]
        assert "dlq" in str(result.recommendations)
    
    def test_feature_suggestions(self):
        """신규 기능 제안"""
        analyzer = DNAMetricsAnalyzer()
        
        # 고지연 시나리오
        dna = {
            "required_modules": ["circuit_breaker"],
            "p99_threshold_ms": 300,
        }
        metrics = {
            "p50": 200,
            "p95": 400,
            "p99": 600,  # 고지연
            "error_rate": 0.005,
            "availability": 0.9995,
        }
        
        result = analyzer.analyze("Test Stage", dna, metrics, ["circuit_breaker"])
        
        # 고지연 관련 기능 제안 확인
        assert "adaptive_caching" in result.suggested_new_features or \
               "connection_pooling" in result.suggested_new_features
    
    def test_trend_analysis_insufficient_data(self):
        """추이 분석 - 데이터 부족"""
        analyzer = DNAMetricsAnalyzer()
        result = analyzer.get_trend_analysis()
        assert result["trend"] == "insufficient_data"
    
    def test_trend_analysis(self):
        """추이 분석"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {"required_modules": ["cb"], "p99_threshold_ms": 300}
        
        # 여러 분석 수행
        for i in range(5):
            metrics = {
                "p50": 100 + i * 10,
                "p95": 200 + i * 10,
                "p99": 250 + i * 10,
                "error_rate": 0.005,
                "availability": 0.9995,
            }
            analyzer.analyze(f"Stage {i}", dna, metrics, ["cb"])
        
        trend = analyzer.get_trend_analysis()
        assert "period" in trend
        assert "average_gap_score" in trend
        assert "sla_success_rate" in trend


class TestGenerateMetricsReportSection:
    """리포트 생성 테스트"""
    
    def test_generate_report_no_data(self):
        """데이터 없는 경우"""
        analyzer = DNAMetricsAnalyzer()
        report = generate_metrics_report_section(analyzer)
        assert "분석 데이터가 없습니다" in report
    
    def test_generate_report_with_data(self):
        """데이터 있는 경우"""
        analyzer = DNAMetricsAnalyzer()
        
        dna = {"required_modules": ["cb"], "p99_threshold_ms": 300}
        metrics = {"p50": 100, "p95": 200, "p99": 250, "error_rate": 0.005, "availability": 0.9995}
        
        analyzer.analyze("Test Stage", dna, metrics, ["cb"])
        
        report = generate_metrics_report_section(analyzer)
        
        assert "DNA-Metrics Conflict Analysis" in report
        assert "Test Stage" in report
        assert "Summary" in report
