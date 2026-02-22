"""
Unit tests for load_tests.core.reporting module
"""
import pytest
import json
import os
import tempfile
import shutil

from load_tests.core.reporting import ReportGenerator


class TestReportGenerator:
    """ReportGenerator 클래스 테스트"""
    
    @pytest.fixture
    def temp_results_dir(self):
        """임시 결과 디렉토리 생성"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    @pytest.fixture
    def sample_stats(self):
        """샘플 통계 데이터"""
        return {
            "scenarios": {
                "browse_products": {
                    "count": 100,
                    "success": 95,
                    "failed": 5,
                    "success_rate": 95.0,
                    "avg_response_time_ms": 150.0,
                    "p95_response_time_ms": 250.0,
                    "p99_response_time_ms": 300.0,
                },
                "checkout": {
                    "count": 50,
                    "success": 48,
                    "failed": 2,
                    "success_rate": 96.0,
                    "avg_response_time_ms": 200.0,
                    "p95_response_time_ms": 350.0,
                    "p99_response_time_ms": 400.0,
                },
            },
            "passed": 143,
            "failed": 7,
            "timestamp": "2025-12-28T10:00:00",
            "healing_actions": {
                "cb_open": 3,
                "throttle_cut": 5,
            },
            "summary": {
                "total_requests": 150,
                "passed": 143,
                "failed": 7,
                "success_rate": 95.33,
                "avg_response_time_ms": 166.67,
                "p95_response_time_ms": 280.0,
                "p99_response_time_ms": 330.0,
            },
        }
    
    @pytest.fixture
    def extreme_stats(self):
        """ExtremeTestStats 샘플 데이터"""
        return {
            "scenarios": {
                "chaos_test": {
                    "count": 100,
                    "success": 85,
                    "failed": 15,
                    "success_rate": 85.0,
                    "avg_response_time_ms": 250.0,
                    "p95_response_time_ms": 400.0,
                    "p99_response_time_ms": 500.0,
                },
            },
            "passed": 85,
            "failed": 15,
            "circuit_breaker": {
                "total_opens": 5,
                "total_closes": 4,
                "total_half_opens": 3,
                "services_affected": ["payment", "database"],
            },
            "emergency": {
                "max_level_reached": 3,
                "recovery_successes": 2,
                "recovery_failures": 1,
            },
            "chaos": {
                "failures_injected": 10,
                "latency_injections": 8,
                "blast_radius_tests": 2,
            },
            "sla": {
                "p99_breaches": 5,
                "p95_breaches": 3,
                "error_rate_breaches": 1,
                "recovery_time_breaches": 0,
            },
            "healing_actions": {},
            "summary": {
                "total_requests": 100,
                "passed": 85,
                "failed": 15,
                "success_rate": 85.0,
            },
        }
    
    def test_init_creates_directories(self, temp_results_dir):
        """디렉토리 자동 생성 확인"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        assert reporter.results_dir.exists()
        assert (reporter.results_dir / "archive").exists()
        assert (reporter.results_dir / "reports").exists()
    
    def test_init_stage_name(self, temp_results_dir):
        """스테이지 이름 설정 확인"""
        reporter = ReportGenerator("stage13_extreme", results_dir=temp_results_dir)
        
        assert reporter.stage_name == "stage13_extreme"
        assert "stage13_extreme" in str(reporter.results_dir)
    
    def test_save_json(self, temp_results_dir, sample_stats):
        """JSON 저장 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_json(sample_stats)
        
        assert os.path.exists(path)
        assert path.endswith(".json")
        
        with open(path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "_meta" in saved_data
        assert saved_data["passed"] == 143
        assert saved_data["failed"] == 7
    
    def test_save_json_custom_filename(self, temp_results_dir, sample_stats):
        """JSON 커스텀 파일명 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_json(sample_stats, filename="custom_result.json")
        
        assert path.endswith("custom_result.json")
        assert os.path.exists(path)
    
    def test_save_json_creates_latest(self, temp_results_dir, sample_stats):
        """latest.json 생성 확인"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        reporter.save_json(sample_stats)
        
        latest_path = reporter.results_dir / "latest.json"
        assert latest_path.exists()
    
    def test_save_markdown(self, temp_results_dir, sample_stats):
        """Markdown 저장 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(sample_stats)
        
        assert os.path.exists(path)
        assert path.endswith(".md")
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Stage Test" in content or "stage_test" in content.lower()
        assert "Summary" in content
        assert "143" in content  # passed count
    
    def test_save_markdown_custom_template(self, temp_results_dir, sample_stats):
        """커스텀 템플릿 Markdown 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        custom_template = "# Custom Report\n\nPassed: {passed}\n".format(**sample_stats)
        path = reporter.save_markdown(sample_stats, template=custom_template)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Custom Report" in content
        assert "Passed: 143" in content
    
    def test_save_html(self, temp_results_dir, sample_stats):
        """HTML 저장 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_html(sample_stats)
        
        assert os.path.exists(path)
        assert path.endswith(".html")
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "<!DOCTYPE html>" in content
        assert "Stage Test" in content or "stage_test" in content.lower()
        assert "143" in content  # passed count
    
    def test_save_html_includes_chart_script(self, temp_results_dir, sample_stats):
        """HTML 차트 스크립트 포함 확인"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_html(sample_stats, include_charts=True)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "chart.js" in content.lower() or "Chart" in content
    
    def test_save_all(self, temp_results_dir, sample_stats):
        """모든 형식 저장 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        paths = reporter.save_all(sample_stats)
        
        assert "json" in paths
        assert "markdown" in paths
        assert "html" in paths
        
        assert os.path.exists(paths["json"])
        assert os.path.exists(paths["markdown"])
        assert os.path.exists(paths["html"])
    
    def test_markdown_with_circuit_breaker(self, temp_results_dir, extreme_stats):
        """CB 통계 포함 Markdown 테스트"""
        reporter = ReportGenerator("stage_extreme", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(extreme_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Circuit Breaker" in content
        assert "payment" in content or "database" in content
    
    def test_markdown_with_emergency(self, temp_results_dir, extreme_stats):
        """Emergency 통계 포함 Markdown 테스트"""
        reporter = ReportGenerator("stage_extreme", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(extreme_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Emergency" in content
    
    def test_markdown_with_chaos(self, temp_results_dir, extreme_stats):
        """Chaos 통계 포함 Markdown 테스트"""
        reporter = ReportGenerator("stage_extreme", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(extreme_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Chaos" in content
    
    def test_markdown_with_sla(self, temp_results_dir, extreme_stats):
        """SLA 통계 포함 Markdown 테스트"""
        reporter = ReportGenerator("stage_extreme", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(extreme_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "SLA" in content
    
    def test_scenarios_table_formatting(self, temp_results_dir, sample_stats):
        """시나리오 테이블 포맷팅 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(sample_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 테이블 형식 확인
        assert "|" in content
        assert "browse_products" in content
        assert "checkout" in content
    
    def test_healing_actions_formatting(self, temp_results_dir, sample_stats):
        """힐링 액션 포맷팅 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        path = reporter.save_markdown(sample_stats)
        
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        assert "Healing Actions" in content
        assert "cb_open" in content
        assert "throttle_cut" in content
    
    def test_empty_stats(self, temp_results_dir):
        """빈 통계 데이터 처리"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        empty_stats = {
            "scenarios": {},
            "passed": 0,
            "failed": 0,
            "healing_actions": {},
            "summary": {
                "total_requests": 0,
                "passed": 0,
                "failed": 0,
                "success_rate": 0,
            },
        }
        
        # 예외 없이 저장되어야 함
        paths = reporter.save_all(empty_stats)
        
        for path in paths.values():
            assert os.path.exists(path)
    
    def test_pass_fail_badge(self, temp_results_dir):
        """PASS/FAIL 배지 테스트"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        # 95% 이상 성공률 - PASS
        pass_stats = {
            "scenarios": {},
            "passed": 95,
            "failed": 5,
            "healing_actions": {},
            "summary": {"success_rate": 95.0, "total_requests": 100, "passed": 95, "failed": 5},
        }
        
        path = reporter.save_markdown(pass_stats)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # PASS 또는 WARNING 확인 (95%는 경계값)
        assert "PASS" in content or "WARNING" in content


class TestReportGeneratorEdgeCases:
    """ReportGenerator 엣지 케이스 테스트"""
    
    @pytest.fixture
    def temp_results_dir(self):
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_unicode_in_stats(self, temp_results_dir):
        """유니코드 데이터 처리"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        stats = {
            "scenarios": {
                "한글_시나리오": {
                    "count": 10,
                    "success": 10,
                    "failed": 0,
                    "success_rate": 100.0,
                    "avg_response_time_ms": 100.0,
                    "p95_response_time_ms": 150.0,
                    "p99_response_time_ms": 200.0,
                },
            },
            "passed": 10,
            "failed": 0,
            "healing_actions": {"힐링_액션": 5},
            "summary": {"total_requests": 10, "passed": 10, "failed": 0, "success_rate": 100.0},
        }
        
        paths = reporter.save_all(stats)
        
        # JSON 확인
        with open(paths["json"], "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert "한글_시나리오" in loaded["scenarios"]
        
        # Markdown 확인
        with open(paths["markdown"], "r", encoding="utf-8") as f:
            content = f.read()
        assert "한글_시나리오" in content
    
    def test_special_characters_in_stage_name(self, temp_results_dir):
        """스테이지 이름 특수문자 처리"""
        reporter = ReportGenerator("stage_13_extreme", results_dir=temp_results_dir)
        
        assert reporter.stage_name == "stage_13_extreme"
        assert reporter.results_dir.exists()
    
    def test_large_numbers(self, temp_results_dir):
        """대용량 숫자 처리"""
        reporter = ReportGenerator("stage_test", results_dir=temp_results_dir)
        
        stats = {
            "scenarios": {
                "high_volume": {
                    "count": 1000000,
                    "success": 999999,
                    "failed": 1,
                    "success_rate": 99.9999,
                    "avg_response_time_ms": 50.123456789,
                    "p95_response_time_ms": 100.0,
                    "p99_response_time_ms": 150.0,
                },
            },
            "passed": 999999,
            "failed": 1,
            "healing_actions": {},
            "summary": {
                "total_requests": 1000000,
                "passed": 999999,
                "failed": 1,
                "success_rate": 99.9999,
            },
        }
        
        paths = reporter.save_all(stats)
        
        with open(paths["json"], "r", encoding="utf-8") as f:
            loaded = json.load(f)
        
        assert loaded["passed"] == 999999
