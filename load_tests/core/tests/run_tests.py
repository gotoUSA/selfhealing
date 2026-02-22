#!/usr/bin/env python
"""
Load Tests Core Module - Standalone Test Runner

Django 없이 core 모듈 단위 테스트를 실행합니다.
"""
import sys
import unittest
import threading
import tempfile
import shutil
import json
from pathlib import Path

# 현재 디렉토리를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


class TestBaseTestStats(unittest.TestCase):
    """BaseTestStats 클래스 테스트"""
    
    def setUp(self):
        from load_tests.core.stats import BaseTestStats
        self.stats = BaseTestStats()
    
    def test_init_default_values(self):
        """기본 초기화 테스트"""
        self.assertEqual(self.stats.scenarios, {})
        self.assertEqual(self.stats.passed, 0)
        self.assertEqual(self.stats.failed, 0)
        self.assertIsNone(self.stats.timestamp)
    
    def test_safe_increment_passed(self):
        """safe_increment - passed 카운터 증가"""
        self.stats.safe_increment("passed", 5)
        self.assertEqual(self.stats.passed, 5)
        
        self.stats.safe_increment("passed")
        self.assertEqual(self.stats.passed, 6)
    
    def test_safe_increment_healing_actions(self):
        """safe_increment - healing_actions 키 증가"""
        self.stats.safe_increment("cb_open", 3)
        self.assertEqual(self.stats.healing_actions["cb_open"], 3)
    
    def test_record_scenario_success(self):
        """시나리오 성공 기록"""
        self.stats.record_scenario("browse_products", success=True, response_time_ms=150.5)
        
        self.assertEqual(self.stats.passed, 1)
        self.assertEqual(self.stats.failed, 0)
        self.assertIn("browse_products", self.stats.scenarios)
        self.assertEqual(self.stats.scenarios["browse_products"]["success"], 1)
    
    def test_record_scenario_failure(self):
        """시나리오 실패 기록"""
        self.stats.record_scenario("checkout", success=False, response_time_ms=500.0, error="Timeout")
        
        self.assertEqual(self.stats.passed, 0)
        self.assertEqual(self.stats.failed, 1)
        self.assertEqual(len(self.stats.scenarios["checkout"]["errors"]), 1)
    
    def test_get_summary(self):
        """전체 통계 요약"""
        for i in range(8):
            self.stats.record_scenario("test", success=True, response_time_ms=100.0)
        for i in range(2):
            self.stats.record_scenario("test", success=False, response_time_ms=500.0)
        
        summary = self.stats.get_summary()
        
        self.assertEqual(summary["total_requests"], 10)
        self.assertEqual(summary["passed"], 8)
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["success_rate"], 80.0)
    
    def test_to_dict(self):
        """딕셔너리 변환"""
        self.stats.record_scenario("test", success=True, response_time_ms=150.0)
        
        result = self.stats.to_dict()
        
        self.assertIn("scenarios", result)
        self.assertIn("passed", result)
        self.assertIn("timestamp", result)
    
    def test_reset(self):
        """통계 리셋"""
        self.stats.record_scenario("test", success=True, response_time_ms=100.0)
        self.stats.reset()
        
        self.assertEqual(self.stats.scenarios, {})
        self.assertEqual(self.stats.passed, 0)
    
    def test_thread_safety(self):
        """스레드 안전성 테스트"""
        num_threads = 10
        iterations = 100
        
        def increment_worker():
            for _ in range(iterations):
                self.stats.safe_increment("passed")
        
        threads = [threading.Thread(target=increment_worker) for _ in range(num_threads)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(self.stats.passed, num_threads * iterations)


class TestExtremeTestStats(unittest.TestCase):
    """ExtremeTestStats 클래스 테스트"""
    
    def setUp(self):
        from load_tests.core.stats import ExtremeTestStats
        self.stats = ExtremeTestStats()
    
    def test_record_cb_event(self):
        """CB 이벤트 기록"""
        self.stats.record_cb_event("payment", "OPEN")
        
        self.assertEqual(self.stats.circuit_breaker["total_opens"], 1)
        self.assertIn("payment", self.stats.circuit_breaker["services_affected"])
    
    def test_record_emergency_escalation(self):
        """Emergency 에스컬레이션 기록"""
        self.stats.record_emergency_escalation(3, "Critical")
        
        self.assertEqual(self.stats.emergency["max_level_reached"], 3)
    
    def test_record_chaos_injection(self):
        """Chaos 주입 기록"""
        self.stats.record_chaos_injection("latency", "payment")
        
        self.assertEqual(self.stats.chaos["latency_injections"], 1)
    
    def test_record_sla_breach(self):
        """SLA 위반 기록"""
        self.stats.record_sla_breach("p99")
        self.stats.record_sla_breach("p99")
        
        self.assertEqual(self.stats.sla["p99_breaches"], 2)


class TestConstants(unittest.TestCase):
    """Constants 모듈 테스트"""
    
    def test_endpoints(self):
        """Endpoints 클래스 테스트"""
        from load_tests.core.constants import Endpoints
        
        self.assertEqual(Endpoints.AUTH_LOGIN, "/api/auth/login/")
        self.assertIn("circuit-breaker", Endpoints.SH_CB_STATUS)
    
    def test_endpoints_format(self):
        """Endpoints.format 테스트"""
        from load_tests.core.constants import Endpoints
        
        result = Endpoints.format(Endpoints.SH_CB_STATUS_SERVICE, service="payment")
        self.assertEqual(result, "/api/self-healing/circuit-breaker/status/payment/")
    
    def test_headers(self):
        """Headers 클래스 테스트"""
        from load_tests.core.constants import Headers
        
        self.assertIn("Content-Type", Headers.JSON)
        self.assertIn("X-Test-Mode", Headers.XTEST_MODE)
    
    def test_headers_with_auth(self):
        """Headers.with_auth 테스트"""
        from load_tests.core.constants import Headers
        
        headers = Headers.with_auth("test_token")
        self.assertEqual(headers["Authorization"], "Bearer test_token")
    
    def test_sla_thresholds(self):
        """SLA 임계값 테스트"""
        from load_tests.core.constants import SLA
        
        self.assertEqual(SLA.P99_THRESHOLD_MS, 300.0)
        self.assertTrue(SLA.is_p99_breach(350.0))
        self.assertFalse(SLA.is_p99_breach(200.0))
    
    def test_load_config(self):
        """LoadConfig 테스트"""
        from load_tests.core.constants import LoadConfig
        
        self.assertEqual(LoadConfig.USERS_SMOKE, 1)
        self.assertEqual(LoadConfig.USERS_EXTREME, 150)
    
    def test_services(self):
        """Services 테스트"""
        from load_tests.core.constants import Services
        
        self.assertEqual(Services.PAYMENT, "payment")
        self.assertIn(Services.DATABASE, Services.ALL)
    
    def test_emergency_levels(self):
        """EmergencyLevels 테스트"""
        from load_tests.core.constants import EmergencyLevels
        
        self.assertEqual(EmergencyLevels.NORMAL, 0)
        self.assertEqual(EmergencyLevels.CRITICAL, 4)
        self.assertTrue(EmergencyLevels.is_critical(5))
        self.assertFalse(EmergencyLevels.is_critical(3))
        self.assertEqual(EmergencyLevels.get_name(0), "NORMAL")


class TestReportGenerator(unittest.TestCase):
    """ReportGenerator 클래스 테스트"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        from load_tests.core.reporting import ReportGenerator
        self.reporter = ReportGenerator("stage_test", results_dir=self.temp_dir)
        self.sample_stats = {
            "scenarios": {
                "test_scenario": {
                    "count": 100,
                    "success": 95,
                    "failed": 5,
                    "success_rate": 95.0,
                    "avg_response_time_ms": 150.0,
                    "p95_response_time_ms": 250.0,
                    "p99_response_time_ms": 300.0,
                }
            },
            "passed": 95,
            "failed": 5,
            "healing_actions": {"cb_open": 2},
            "summary": {
                "total_requests": 100,
                "passed": 95,
                "failed": 5,
                "success_rate": 95.0,
            },
        }
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_creates_directories(self):
        """디렉토리 생성 확인"""
        self.assertTrue(self.reporter.results_dir.exists())
        self.assertTrue((self.reporter.results_dir / "archive").exists())
        self.assertTrue((self.reporter.results_dir / "reports").exists())
    
    def test_save_json(self):
        """JSON 저장"""
        path = self.reporter.save_json(self.sample_stats)
        
        self.assertTrue(Path(path).exists())
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["passed"], 95)
    
    def test_save_markdown(self):
        """Markdown 저장"""
        path = self.reporter.save_markdown(self.sample_stats)
        
        self.assertTrue(Path(path).exists())
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Summary", content)
    
    def test_save_html(self):
        """HTML 저장"""
        path = self.reporter.save_html(self.sample_stats)
        
        self.assertTrue(Path(path).exists())
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<!DOCTYPE html>", content)
    
    def test_save_all(self):
        """모든 형식 저장"""
        paths = self.reporter.save_all(self.sample_stats)
        
        self.assertIn("json", paths)
        self.assertIn("markdown", paths)
        self.assertIn("html", paths)
        for path in paths.values():
            self.assertTrue(Path(path).exists())


class TestMixins(unittest.TestCase):
    """Mixins 모듈 테스트"""
    
    def test_admin_auth_mixin_headers(self):
        """AdminAuthMixin 헤더 테스트"""
        from load_tests.core.mixins import AdminAuthMixin
        
        class TestUser(AdminAuthMixin):
            pass
        
        user = TestUser()
        user._token = "test_token"
        
        headers = user.get_auth_headers()
        self.assertEqual(headers["Authorization"], "Bearer test_token")
    
    def test_admin_auth_mixin_no_token(self):
        """AdminAuthMixin 토큰 없을 때"""
        from load_tests.core.mixins import AdminAuthMixin
        
        class TestUser(AdminAuthMixin):
            pass
        
        user = TestUser()
        user._token = None
        
        headers = user.get_auth_headers()
        self.assertEqual(headers, {})
    
    def test_xtest_mode_mixin_headers(self):
        """XTestModeMixin 헤더 테스트"""
        from load_tests.core.mixins import XTestModeMixin
        
        self.assertIn("X-Test-Mode", XTestModeMixin.XTEST_HEADERS)
        self.assertEqual(XTestModeMixin.XTEST_HEADERS["X-Test-Mode"], "chaos-monkey")
    
    def test_phase_manager_mixin(self):
        """PhaseManagerMixin 테스트"""
        from load_tests.core.mixins import PhaseManagerMixin
        
        PhaseManagerMixin._current_phase = "idle"
        
        PhaseManagerMixin.set_phase("spike")
        self.assertEqual(PhaseManagerMixin.get_phase(), "spike")
        self.assertTrue(PhaseManagerMixin.is_spike_phase())
        
        PhaseManagerMixin.set_phase("cooldown")
        self.assertTrue(PhaseManagerMixin.is_cooldown_phase())
        self.assertFalse(PhaseManagerMixin.is_spike_phase())


def run_tests():
    """테스트 실행"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 테스트 클래스 추가
    suite.addTests(loader.loadTestsFromTestCase(TestBaseTestStats))
    suite.addTests(loader.loadTestsFromTestCase(TestExtremeTestStats))
    suite.addTests(loader.loadTestsFromTestCase(TestConstants))
    suite.addTests(loader.loadTestsFromTestCase(TestReportGenerator))
    suite.addTests(loader.loadTestsFromTestCase(TestMixins))
    
    # 결과 실행
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 결과 요약
    print("\n" + "=" * 70)
    print(f"테스트 완료: {result.testsRun}개 실행")
    print(f"성공: {result.testsRun - len(result.failures) - len(result.errors)}개")
    print(f"실패: {len(result.failures)}개")
    print(f"에러: {len(result.errors)}개")
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
