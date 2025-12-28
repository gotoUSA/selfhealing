"""
Unit tests for load_tests.core.stats module
"""
import pytest
import threading
from datetime import datetime

from load_tests.core.stats import BaseTestStats, ExtremeTestStats


class TestBaseTestStats:
    """BaseTestStats 클래스 테스트"""
    
    def test_init_default_values(self):
        """기본 초기화 테스트"""
        stats = BaseTestStats()
        
        assert stats.scenarios == {}
        assert stats.passed == 0
        assert stats.failed == 0
        assert stats.timestamp is None
        assert stats.healing_actions == {}
    
    def test_safe_increment_passed(self):
        """safe_increment - passed 카운터 증가"""
        stats = BaseTestStats()
        
        stats.safe_increment("passed", 5)
        assert stats.passed == 5
        
        stats.safe_increment("passed")
        assert stats.passed == 6
    
    def test_safe_increment_healing_actions(self):
        """safe_increment - healing_actions 키 증가"""
        stats = BaseTestStats()
        
        stats.safe_increment("cb_open", 3)
        assert stats.healing_actions["cb_open"] == 3
        
        stats.safe_increment("cb_open", 2)
        assert stats.healing_actions["cb_open"] == 5
    
    def test_safe_decrement(self):
        """safe_decrement 테스트"""
        stats = BaseTestStats()
        stats.passed = 10
        
        stats.safe_decrement("passed", 3)
        assert stats.passed == 7
    
    def test_record_scenario_success(self):
        """시나리오 성공 기록"""
        stats = BaseTestStats()
        
        stats.record_scenario("browse_products", success=True, response_time_ms=150.5)
        
        assert stats.passed == 1
        assert stats.failed == 0
        assert "browse_products" in stats.scenarios
        assert stats.scenarios["browse_products"]["success"] == 1
        assert stats.scenarios["browse_products"]["count"] == 1
        assert 150.5 in stats.scenarios["browse_products"]["response_times"]
    
    def test_record_scenario_failure(self):
        """시나리오 실패 기록"""
        stats = BaseTestStats()
        
        stats.record_scenario(
            "checkout", 
            success=False, 
            response_time_ms=500.0,
            error="Timeout error"
        )
        
        assert stats.passed == 0
        assert stats.failed == 1
        assert stats.scenarios["checkout"]["failed"] == 1
        assert len(stats.scenarios["checkout"]["errors"]) == 1
        assert stats.scenarios["checkout"]["errors"][0]["message"] == "Timeout error"
    
    def test_record_scenario_multiple(self):
        """동일 시나리오 다중 기록"""
        stats = BaseTestStats()
        
        for i in range(5):
            stats.record_scenario("test", success=True, response_time_ms=100.0 + i*10)
        
        stats.record_scenario("test", success=False, response_time_ms=600.0, error="Error")
        
        assert stats.scenarios["test"]["count"] == 6
        assert stats.scenarios["test"]["success"] == 5
        assert stats.scenarios["test"]["failed"] == 1
        assert len(stats.scenarios["test"]["response_times"]) == 6
    
    def test_get_scenario_stats(self):
        """시나리오 통계 조회"""
        stats = BaseTestStats()
        
        for i in range(10):
            stats.record_scenario("api_call", success=True, response_time_ms=100.0 + i*10)
        
        scenario_stats = stats.get_scenario_stats("api_call")
        
        assert scenario_stats is not None
        assert scenario_stats["count"] == 10
        assert scenario_stats["success"] == 10
        assert scenario_stats["success_rate"] == 100.0
        assert scenario_stats["avg_response_time_ms"] == 145.0  # 100~190 평균
    
    def test_get_scenario_stats_not_found(self):
        """존재하지 않는 시나리오 조회"""
        stats = BaseTestStats()
        
        result = stats.get_scenario_stats("nonexistent")
        assert result is None
    
    def test_get_summary(self):
        """전체 통계 요약"""
        stats = BaseTestStats()
        
        for i in range(8):
            stats.record_scenario("success_test", success=True, response_time_ms=100.0)
        for i in range(2):
            stats.record_scenario("fail_test", success=False, response_time_ms=500.0)
        
        summary = stats.get_summary()
        
        assert summary["total_requests"] == 10
        assert summary["passed"] == 8
        assert summary["failed"] == 2
        assert summary["success_rate"] == 80.0
        assert summary["scenario_count"] == 2
    
    def test_to_dict(self):
        """딕셔너리 변환"""
        stats = BaseTestStats()
        stats.record_scenario("test", success=True, response_time_ms=150.0)
        stats.safe_increment("custom_action")
        
        result = stats.to_dict()
        
        assert "scenarios" in result
        assert "passed" in result
        assert "failed" in result
        assert "timestamp" in result
        assert "healing_actions" in result
        assert "summary" in result
        assert result["healing_actions"]["custom_action"] == 1
    
    def test_reset(self):
        """통계 리셋"""
        stats = BaseTestStats()
        stats.record_scenario("test", success=True, response_time_ms=100.0)
        stats.safe_increment("action")
        
        stats.reset()
        
        assert stats.scenarios == {}
        assert stats.passed == 0
        assert stats.failed == 0
        assert stats.healing_actions == {}
    
    def test_thread_safety(self):
        """스레드 안전성 테스트"""
        stats = BaseTestStats()
        num_threads = 10
        iterations = 100
        
        def increment_worker():
            for _ in range(iterations):
                stats.safe_increment("passed")
                stats.record_scenario("concurrent", success=True, response_time_ms=100.0)
        
        threads = [threading.Thread(target=increment_worker) for _ in range(num_threads)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert stats.passed == num_threads * iterations * 2  # safe_increment + record_scenario
        assert stats.scenarios["concurrent"]["count"] == num_threads * iterations


class TestExtremeTestStats:
    """ExtremeTestStats 클래스 테스트"""
    
    def test_init_extended_fields(self):
        """확장 필드 초기화"""
        stats = ExtremeTestStats()
        
        assert "total_opens" in stats.circuit_breaker
        assert "max_level_reached" in stats.emergency
        assert "failures_injected" in stats.chaos
        assert "cb" in stats.aggressive_healing
        assert "p99_breaches" in stats.sla
    
    def test_record_cb_event_open(self):
        """CB Open 이벤트 기록"""
        stats = ExtremeTestStats()
        
        stats.record_cb_event("payment", "OPEN")
        
        assert stats.circuit_breaker["total_opens"] == 1
        assert "payment" in stats.circuit_breaker["services_affected"]
        assert len(stats.circuit_breaker["transitions"]) == 1
    
    def test_record_cb_event_multiple_services(self):
        """여러 서비스 CB 이벤트"""
        stats = ExtremeTestStats()
        
        stats.record_cb_event("payment", "OPEN")
        stats.record_cb_event("database", "OPEN")
        stats.record_cb_event("payment", "CLOSED")
        
        assert stats.circuit_breaker["total_opens"] == 2
        assert stats.circuit_breaker["total_closes"] == 1
        assert len(stats.circuit_breaker["services_affected"]) == 2
    
    def test_record_emergency_escalation(self):
        """Emergency 에스컬레이션 기록"""
        stats = ExtremeTestStats()
        
        stats.record_emergency_escalation(1, "High load detected")
        stats.record_emergency_escalation(3, "Critical threshold")
        stats.record_emergency_escalation(2, "Recovering")
        
        assert stats.emergency["max_level_reached"] == 3
        assert stats.emergency["current_level"] == 2
        assert len(stats.emergency["escalations"]) == 3
    
    def test_record_emergency_recovery(self):
        """Emergency 복구 기록"""
        stats = ExtremeTestStats()
        
        stats.record_emergency_recovery(success=True)
        stats.record_emergency_recovery(success=True)
        stats.record_emergency_recovery(success=False)
        
        assert stats.emergency["recovery_successes"] == 2
        assert stats.emergency["recovery_failures"] == 1
    
    def test_record_chaos_injection(self):
        """Chaos 주입 기록"""
        stats = ExtremeTestStats()
        
        stats.record_chaos_injection("latency", "payment")
        stats.record_chaos_injection("cb_failure", "database")
        stats.record_chaos_injection("blast_radius")
        
        assert stats.chaos["latency_injections"] == 1
        assert stats.chaos["failures_injected"] == 1
        assert stats.chaos["blast_radius_tests"] == 1
        assert "payment" in stats.chaos["services_targeted"]
        assert "database" in stats.chaos["services_targeted"]
    
    def test_record_aggressive_healing_cb(self):
        """Aggressive Healing CB 기록"""
        stats = ExtremeTestStats()
        
        stats.record_aggressive_healing("cb", forced_open=True)
        stats.record_aggressive_healing("cb", xtest_error=True)
        stats.record_aggressive_healing("cb", xtest_error=True)
        
        assert stats.aggressive_healing["cb"]["forced_opens"] == 1
        assert stats.aggressive_healing["cb"]["xtest_errors_counted"] == 2
    
    def test_record_aggressive_healing_throttle(self):
        """Aggressive Healing Throttle 기록"""
        stats = ExtremeTestStats()
        
        stats.record_aggressive_healing("throttle", aggressive_cut=True, current_limit=50.0)
        
        assert stats.aggressive_healing["throttle"]["aggressive_cuts"] == 1
        assert stats.aggressive_healing["throttle"]["current_limit_percent"] == 50.0
    
    def test_record_aggressive_healing_jitter(self):
        """Aggressive Healing Jitter 기록"""
        stats = ExtremeTestStats()
        
        stats.record_aggressive_healing("jitter", escalation=True, current_ms=2000)
        stats.record_aggressive_healing("jitter", max_reached=True)
        
        assert stats.aggressive_healing["jitter"]["escalations"] == 1
        assert stats.aggressive_healing["jitter"]["current_jitter_ms"] == 2000
        assert stats.aggressive_healing["jitter"]["max_reached"] is True
    
    def test_record_sla_breach(self):
        """SLA 위반 기록"""
        stats = ExtremeTestStats()
        
        stats.record_sla_breach("p99")
        stats.record_sla_breach("p99")
        stats.record_sla_breach("error_rate")
        
        assert stats.sla["p99_breaches"] == 2
        assert stats.sla["error_rate_breaches"] == 1
        assert stats.sla["p95_breaches"] == 0
    
    def test_to_dict_includes_extended(self):
        """확장 딕셔너리 변환"""
        stats = ExtremeTestStats()
        stats.record_cb_event("payment", "OPEN")
        stats.record_chaos_injection("latency")
        
        result = stats.to_dict()
        
        assert "circuit_breaker" in result
        assert "emergency" in result
        assert "chaos" in result
        assert "aggressive_healing" in result
        assert "sla" in result
        assert result["circuit_breaker"]["total_opens"] == 1
    
    def test_reset_extended(self):
        """확장 통계 리셋"""
        stats = ExtremeTestStats()
        stats.record_cb_event("payment", "OPEN")
        stats.record_emergency_escalation(3)
        stats.record_chaos_injection("latency")
        
        stats.reset()
        
        assert stats.circuit_breaker["total_opens"] == 0
        assert stats.circuit_breaker["services_affected"] == []
        assert stats.emergency["max_level_reached"] == 0
        assert stats.chaos["latency_injections"] == 0
    
    def test_inherits_base_functionality(self):
        """BaseTestStats 기능 상속 확인"""
        stats = ExtremeTestStats()
        
        stats.record_scenario("test", success=True, response_time_ms=100.0)
        stats.safe_increment("passed", 5)
        
        assert stats.passed == 6  # 1 from record + 5 from increment
        assert stats.scenarios["test"]["count"] == 1
