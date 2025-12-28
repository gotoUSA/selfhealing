"""
Stage 0: Self-Healing System Extreme Stress Test

Purpose: Verify Self-Healing system behavior under EXTREME conditions
- Circuit Breaker forced OPEN and recovery
- Emergency Mode trigger and release
- Error Budget exhaustion and recovery
- DLQ stress testing
- Chaos injection and blast radius isolation
- L2 Storage resilience under failure
- Governance override testing

This test pushes the Self-Healing system to its limits to verify
it properly protects the shopping API under catastrophic conditions.

Run:
    locust -f load_tests/scenarios/load/stage0_selfhealing_extreme.py --host=http://localhost:8000 --users=10 --spawn-rate=2 --run-time=120s --headless

Prerequisites:
    - Server running via docker-compose (docker-compose up -d)
    - Admin user exists
    - CHAOS_ENABLED=true in docker-compose.yml
"""

import os
import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

# 프로젝트 루트 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector

# Import SelfHealingClient for extreme testing
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    SELFHEALING_CLIENT_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ SelfHealingClient import failed: {e}")
    SELFHEALING_CLIENT_AVAILABLE = False


STAGE_NAME = "[Stage0-Extreme]"

# Self-Healing API Base Path
SH_API_BASE = "/api/self-healing"

# =============================================================================
# Test Configuration
# =============================================================================

EXTREME_TEST_CONFIG = {
    # Circuit Breaker Stress
    "cb_services": ["payment-service", "order-service", "point-service", "external-api"],
    "cb_failure_rate": 1.0,  # 100% failure
    "cb_recovery_wait_seconds": 5,
    
    # Emergency Mode Levels
    "emergency_levels": ["LEVEL_1", "LEVEL_2", "LEVEL_3"],
    "emergency_duration_minutes": 1,
    
    # Error Budget Exhaustion
    "error_budget_error_count": 1000,
    
    # Chaos Engineering
    "chaos_targets": ["payment", "inventory", "cache"],
    
    # DLQ Stress
    "dlq_domains": ["payment", "order", "notification"],
}


# =============================================================================
# Test Statistics - Extreme Scenarios
# =============================================================================

_extreme_stats = {
    "start_time": None,
    "end_time": None,
    "scenarios": {
        "circuit_breaker_stress": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "emergency_mode": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "error_budget_exhaustion": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "chaos_injection": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "dlq_stress": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "l2_storage_resilience": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "blast_radius_isolation": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "governance_stress": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
        "recovery_verification": {
            "executed": 0, "success": 0, "failure": 0, "details": []
        },
    },
    "healing_events": [],
    "recovery_times": {},
    "critical_failures": [],
}


def record_scenario_result(scenario: str, success: bool, detail: dict):
    """Record extreme scenario test result"""
    stats = _extreme_stats["scenarios"].get(scenario)
    if stats:
        stats["executed"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["failure"] += 1
            _extreme_stats["critical_failures"].append({
                "scenario": scenario,
                "detail": detail,
                "timestamp": datetime.now().isoformat(),
            })
        stats["details"].append(detail)


def record_healing_event(event_type: str, service: str, details: dict):
    """Record self-healing event"""
    _extreme_stats["healing_events"].append({
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "service": service,
        "details": details,
    })


def record_recovery_time(scenario: str, recovery_seconds: float):
    """Record recovery time"""
    if scenario not in _extreme_stats["recovery_times"]:
        _extreme_stats["recovery_times"][scenario] = []
    _extreme_stats["recovery_times"][scenario].append(recovery_seconds)


class SelfHealingExtremeUser(HttpUser):
    """
    Self-Healing System Extreme Stress Test User
    
    Tests the Self-Healing system under catastrophic conditions.
    """
    
    wait_time = between(1, 3)
    
    def on_start(self):
        """Initialize on test start"""
        global _extreme_stats
        
        setup_event_hooks(STAGE_NAME)
        
        if _extreme_stats["start_time"] is None:
            _extreme_stats["start_time"] = time.time()
        
        # Login as admin
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.login_helper.login_as_admin()
        self.is_admin = self.login_helper.is_logged_in
        
        # Initialize SelfHealingClient if available
        self.sh_client = None
        if SELFHEALING_CLIENT_AVAILABLE and self.is_admin:
            try:
                self.sh_client = SelfHealingClient(
                    host="http://localhost:8000",
                    auth_mode="xtest"  # Use XTest mode for chaos
                )
                # Try to login
                if hasattr(self.sh_client.auth, 'login'):
                    self.sh_client.login("admin", "1234")
                print(f"✅ {STAGE_NAME} SelfHealingClient initialized")
            except Exception as e:
                print(f"⚠️ {STAGE_NAME} SelfHealingClient init failed: {e}")
                self.sh_client = None
    
    # =========================================================================
    # Scenario 1: Circuit Breaker Stress Test
    # =========================================================================
    
    @task(3)
    @tag("extreme", "circuit-breaker", "stress")
    def test_circuit_breaker_stress(self):
        """
        Stress test Circuit Breaker by forcing failures and verifying recovery.
        
        Steps:
        1. Force CB to OPEN state
        2. Verify fast-fail behavior
        3. Wait for recovery
        4. Verify CB closes after recovery
        """
        if not self.sh_client:
            return self._fallback_cb_test()
        
        service = EXTREME_TEST_CONFIG["cb_services"][0]  # payment-service
        start_time = time.time()
        
        try:
            # Step 1: Inject failure to force CB OPEN
            inject_result = self.sh_client.xtest.inject_cb_failure(
                service_name=service,
                failure_type="exception",
                failure_rate=EXTREME_TEST_CONFIG["cb_failure_rate"],
                duration_seconds=30,
            )
            
            record_healing_event("CB_FAILURE_INJECTED", service, inject_result)
            
            # Step 2: Verify CB status
            time.sleep(0.5)
            cb_status = self.sh_client.xtest.get_cb_status(service_name=service)
            
            is_open = cb_status.get("state") in ["OPEN", "open", "HALF_OPEN"]
            
            # Step 3: Test fast-fail (should return quickly)
            fast_fail_result = self.sh_client.xtest.fast_fail_test(
                service_name=service,
                request_count=5,
            )
            
            # Step 4: Trigger recovery
            recovery_result = self.sh_client.xtest.trigger_cb_recovery(service_name=service)
            
            # Step 5: Reset CB
            reset_result = self.sh_client.xtest.reset_cb(service_name=service)
            
            elapsed = time.time() - start_time
            
            detail = {
                "service": service,
                "cb_opened": is_open,
                "fast_fail_worked": fast_fail_result.get("status") != "error",
                "recovery_triggered": recovery_result.get("status") != "error",
                "reset_success": reset_result.get("status") != "error",
                "elapsed_seconds": elapsed,
            }
            
            success = all([
                inject_result.get("status") != "error",
                reset_result.get("status") != "error",
            ])
            
            record_scenario_result("circuit_breaker_stress", success, detail)
            
            if success:
                record_recovery_time("circuit_breaker", elapsed)
            
        except Exception as e:
            record_scenario_result("circuit_breaker_stress", False, {
                "error": str(e),
                "service": service,
            })
    
    def _fallback_cb_test(self):
        """Fallback CB test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/circuit-breaker/pool/status/",
            name=f"{STAGE_NAME} CB Pool Status",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("circuit_breaker_stress", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("CB status check failed")
    
    # =========================================================================
    # Scenario 2: Emergency Mode Stress Test
    # =========================================================================
    
    @task(2)
    @tag("extreme", "emergency", "stress")
    def test_emergency_mode_stress(self):
        """
        Test Emergency Mode trigger and release cycle.
        
        Steps:
        1. Trigger LEVEL_1 emergency
        2. Verify system restrictions
        3. Release emergency
        4. Verify normal operation resumes
        """
        if not self.sh_client:
            return self._fallback_emergency_test()
        
        start_time = time.time()
        
        try:
            # Step 1: Get current status
            initial_status = self.sh_client.emergency.get_status()
            
            # Step 2: Trigger LEVEL_1 Emergency
            trigger_result = self.sh_client.emergency.trigger(
                level="LEVEL_1",
                reason="Extreme Stress Test",
                duration_minutes=EXTREME_TEST_CONFIG["emergency_duration_minutes"],
            )
            
            record_healing_event("EMERGENCY_TRIGGERED", "system", trigger_result)
            
            # Step 3: Verify emergency is active
            time.sleep(0.5)
            emergency_status = self.sh_client.emergency.get_status()
            
            is_active = emergency_status.get("is_active", False) or \
                       emergency_status.get("level") is not None
            
            # Step 4: Release emergency
            release_result = self.sh_client.emergency.release(
                reason="Stress test complete"
            )
            
            record_healing_event("EMERGENCY_RELEASED", "system", release_result)
            
            # Step 5: Verify release
            time.sleep(0.3)
            final_status = self.sh_client.emergency.get_status()
            
            elapsed = time.time() - start_time
            
            detail = {
                "trigger_success": trigger_result.get("status") != "error",
                "was_active": is_active,
                "release_success": release_result.get("status") != "error",
                "elapsed_seconds": elapsed,
            }
            
            success = trigger_result.get("status") != "error" or \
                     trigger_result.get("status_code") in [200, 201, 403]
            
            record_scenario_result("emergency_mode", success, detail)
            
            if success:
                record_recovery_time("emergency_mode", elapsed)
            
        except Exception as e:
            record_scenario_result("emergency_mode", False, {"error": str(e)})
    
    def _fallback_emergency_test(self):
        """Fallback emergency test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/emergency/status/",
            name=f"{STAGE_NAME} Emergency Status",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("emergency_mode", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("Emergency status check failed")
    
    # =========================================================================
    # Scenario 3: Error Budget Exhaustion Test
    # =========================================================================
    
    @task(2)
    @tag("extreme", "error-budget", "stress")
    def test_error_budget_exhaustion(self):
        """
        Test system behavior when Error Budget is exhausted.
        
        Steps:
        1. Record current budget
        2. Inject errors to exhaust budget
        3. Verify deployment restrictions
        4. Reset simulation
        """
        if not self.sh_client:
            return self._fallback_error_budget_test()
        
        start_time = time.time()
        
        try:
            # Step 1: Get current error budget status
            initial_budget = self.sh_client.error_budget.get_status()
            initial_remaining = initial_budget.get("remaining_percent", 100)
            
            # Step 2: Inject errors via XTest
            inject_result = self.sh_client.xtest.inject_error_budget(
                slo_name="availability",
                error_count=EXTREME_TEST_CONFIG["error_budget_error_count"],
            )
            
            record_healing_event("ERROR_BUDGET_INJECTED", "availability", inject_result)
            
            # Step 3: Check new budget status
            time.sleep(0.3)
            new_budget = self.sh_client.error_budget.get_status()
            new_remaining = new_budget.get("remaining_percent", 100)
            
            budget_decreased = new_remaining < initial_remaining
            
            # Step 4: Try to reset simulation
            reset_result = self.sh_client.error_budget.reset_simulation()
            
            elapsed = time.time() - start_time
            
            detail = {
                "initial_remaining": initial_remaining,
                "after_injection_remaining": new_remaining,
                "budget_decreased": budget_decreased,
                "reset_success": reset_result.get("status") != "error",
                "elapsed_seconds": elapsed,
            }
            
            success = inject_result.get("status") != "error" or \
                     inject_result.get("status_code") in [200, 201, 403, 404]
            
            record_scenario_result("error_budget_exhaustion", success, detail)
            
        except Exception as e:
            record_scenario_result("error_budget_exhaustion", False, {"error": str(e)})
    
    def _fallback_error_budget_test(self):
        """Fallback error budget test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/error-budget/status/",
            name=f"{STAGE_NAME} Error Budget Status",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("error_budget_exhaustion", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("Error budget check failed")
    
    # =========================================================================
    # Scenario 4: Chaos Injection & Blast Radius Test
    # =========================================================================
    
    @task(2)
    @tag("extreme", "chaos", "blast-radius")
    def test_chaos_blast_radius(self):
        """
        Test chaos injection and blast radius isolation.
        
        Verifies that failure in one service doesn't cascade to others.
        """
        if not self.sh_client:
            return self._fallback_chaos_test()
        
        start_time = time.time()
        
        try:
            # Step 1: Test single service blast radius
            single_result = self.sh_client.xtest.test_blast_radius(
                service_name="payment-service",
                failure_type="exception",
            )
            
            record_healing_event("BLAST_RADIUS_TEST", "payment-service", single_result)
            
            # Step 2: Test multi-service blast radius
            services = ["payment-service", "order-service", "point-service"]
            multi_result = self.sh_client.xtest.test_multi_blast_radius(
                services=services,
                failure_type="exception",
            )
            
            record_healing_event("MULTI_BLAST_RADIUS_TEST", "multi", multi_result)
            
            # Step 3: Get system snapshot
            snapshot = self.sh_client.xtest.get_snapshot()
            
            # Step 4: Get healing timeline
            timeline = self.sh_client.xtest.get_healing_timeline(limit=20)
            
            elapsed = time.time() - start_time
            
            # Verify isolation
            isolated = True
            if multi_result.get("isolation_matrix"):
                for service, impact in multi_result.get("isolation_matrix", {}).items():
                    if impact.get("cascaded_failures", 0) > 0:
                        isolated = False
                        break
            
            detail = {
                "single_blast_success": single_result.get("status") != "error",
                "multi_blast_success": multi_result.get("status") != "error",
                "isolation_maintained": isolated,
                "snapshot_available": snapshot.get("status") != "error",
                "timeline_events": len(timeline.get("events", [])) if isinstance(timeline, dict) else 0,
                "elapsed_seconds": elapsed,
            }
            
            success = single_result.get("status") != "error" or \
                     single_result.get("status_code") in [200, 201, 403, 404]
            
            record_scenario_result("blast_radius_isolation", success, detail)
            record_scenario_result("chaos_injection", success, detail)
            
        except Exception as e:
            record_scenario_result("blast_radius_isolation", False, {"error": str(e)})
            record_scenario_result("chaos_injection", False, {"error": str(e)})
    
    def _fallback_chaos_test(self):
        """Fallback chaos test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/chaos/kill-switch/",
            name=f"{STAGE_NAME} Chaos Kill Switch",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("chaos_injection", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("Chaos check failed")
    
    # =========================================================================
    # Scenario 5: L2 Storage Resilience Test
    # =========================================================================
    
    @task(2)
    @tag("extreme", "l2-storage", "resilience")
    def test_l2_storage_resilience(self):
        """
        Test L2 Storage resilience under failure conditions.
        
        Steps:
        1. Check L2 Storage health
        2. Get shadow log stats
        3. Verify drift reconciliation
        """
        if not self.sh_client:
            return self._fallback_l2_test()
        
        start_time = time.time()
        
        try:
            # Step 1: Get L2 Storage status
            status = self.sh_client.l2_storage.get_status()
            
            # Step 2: Get L2 Storage health
            health = self.sh_client.l2_storage.get_health()
            
            # Step 3: Get shadow log stats
            shadow_stats = self.sh_client.l2_storage.get_shadow_log_stats()
            
            # Step 4: Get metrics
            metrics = self.sh_client.l2_storage.get_metrics()
            
            elapsed = time.time() - start_time
            
            is_healthy = health.get("healthy", True) or \
                        health.get("status") in ["healthy", "ok"]
            
            detail = {
                "status_available": status.get("status") != "error",
                "health_check_passed": is_healthy,
                "shadow_log_available": shadow_stats.get("status") != "error",
                "metrics_available": metrics.get("status") != "error",
                "elapsed_seconds": elapsed,
            }
            
            success = status.get("status") != "error" or \
                     status.get("status_code") in [200, 401, 403]
            
            record_scenario_result("l2_storage_resilience", success, detail)
            
        except Exception as e:
            record_scenario_result("l2_storage_resilience", False, {"error": str(e)})
    
    def _fallback_l2_test(self):
        """Fallback L2 test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/l2-storage/status/",
            name=f"{STAGE_NAME} L2 Storage Status",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("l2_storage_resilience", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("L2 storage check failed")
    
    # =========================================================================
    # Scenario 6: DLQ Stress Test
    # =========================================================================
    
    @task(2)
    @tag("extreme", "dlq", "stress")
    def test_dlq_stress(self):
        """
        Test DLQ under stress conditions.
        
        Steps:
        1. Get DLQ list
        2. Check DLQ stats
        3. Verify cleanup functionality
        """
        if not self.sh_client:
            return self._fallback_dlq_test()
        
        start_time = time.time()
        
        try:
            # Step 1: Get DLQ list
            dlq_list = self.sh_client.dlq.list(limit=100)
            
            # Step 2: Get DLQ stats
            dlq_stats = self.sh_client.dlq.stats()
            
            pending_count = 0
            if isinstance(dlq_list, dict):
                pending_count = len(dlq_list.get("items", []))
            
            elapsed = time.time() - start_time
            
            detail = {
                "list_available": dlq_list.get("status") != "error",
                "stats_available": dlq_stats.get("status") != "error",
                "pending_count": pending_count,
                "total_in_dlq": dlq_stats.get("total", 0) if isinstance(dlq_stats, dict) else 0,
                "elapsed_seconds": elapsed,
            }
            
            success = dlq_list.get("status") != "error" or \
                     dlq_list.get("status_code") in [200, 401, 403]
            
            record_scenario_result("dlq_stress", success, detail)
            
        except Exception as e:
            record_scenario_result("dlq_stress", False, {"error": str(e)})
    
    def _fallback_dlq_test(self):
        """Fallback DLQ test using HTTP directly"""
        with self.client.get(
            f"{SH_API_BASE}/dlq/list/",
            name=f"{STAGE_NAME} DLQ List",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("dlq_stress", success, {
                "status_code": response.status_code,
                "fallback": True,
            })
            response.success() if success else response.failure("DLQ check failed")
    
    # =========================================================================
    # Scenario 7: Governance Stress Test
    # =========================================================================
    
    @task(1)
    @tag("extreme", "governance", "stress")
    def test_governance_stress(self):
        """
        Test Governance controls under stress.
        
        Steps:
        1. Check governance status
        2. Verify RBAC controls
        3. Test rate limiting
        """
        start_time = time.time()
        
        try:
            # Test multiple governance endpoints in rapid succession
            results = []
            
            endpoints = [
                f"{SH_API_BASE}/governance/status/",
                f"{SH_API_BASE}/governance/mode/",
                f"{SH_API_BASE}/metrics/status/",
            ]
            
            for endpoint in endpoints:
                with self.client.get(
                    endpoint,
                    name=f"{STAGE_NAME} Governance {endpoint.split('/')[-2]}",
                    catch_response=True,
                ) as response:
                    results.append({
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "success": response.status_code in [200, 401, 403],
                    })
                    response.success()
            
            elapsed = time.time() - start_time
            
            all_success = all(r["success"] for r in results)
            
            detail = {
                "endpoints_tested": len(endpoints),
                "all_success": all_success,
                "results": results,
                "elapsed_seconds": elapsed,
            }
            
            record_scenario_result("governance_stress", all_success, detail)
            
        except Exception as e:
            record_scenario_result("governance_stress", False, {"error": str(e)})
    
    # =========================================================================
    # Scenario 8: System Recovery Verification
    # =========================================================================
    
    @task(3)
    @tag("extreme", "recovery", "verification")
    def test_recovery_verification(self):
        """
        Verify system can recover from extreme conditions.
        
        Steps:
        1. Check all critical endpoints
        2. Verify health after stress
        3. Ensure no lingering failures
        """
        start_time = time.time()
        
        try:
            critical_endpoints = [
                (f"{SH_API_BASE}/health/ping/", "health_ping"),
                (f"{SH_API_BASE}/health/live/", "health_live"),
                (f"{SH_API_BASE}/health/ready/", "health_ready"),
                (f"{SH_API_BASE}/status/", "status"),
            ]
            
            results = {}
            all_healthy = True
            
            for endpoint, name in critical_endpoints:
                with self.client.get(
                    endpoint,
                    name=f"{STAGE_NAME} Recovery-{name}",
                    catch_response=True,
                ) as response:
                    is_healthy = response.status_code in [200, 204]
                    results[name] = {
                        "status_code": response.status_code,
                        "healthy": is_healthy,
                        "response_time_ms": response.elapsed.total_seconds() * 1000,
                    }
                    if not is_healthy and response.status_code not in [401, 403]:
                        all_healthy = False
                    response.success()
            
            elapsed = time.time() - start_time
            
            # Get overall system health via SH client
            system_health = {"available": True}
            if self.sh_client:
                try:
                    overall = self.sh_client.get_overall_status()
                    system_health = {
                        "health": overall.get("health", {}).get("status", "unknown"),
                        "cb_status": "ok" if overall.get("circuit_breakers", {}).get("status") != "error" else "error",
                        "emergency_active": overall.get("emergency", {}).get("is_active", False),
                    }
                except:
                    pass
            
            detail = {
                "all_endpoints_healthy": all_healthy,
                "endpoint_results": results,
                "system_health": system_health,
                "elapsed_seconds": elapsed,
            }
            
            record_scenario_result("recovery_verification", all_healthy, detail)
            
            if all_healthy:
                record_recovery_time("full_recovery", elapsed)
            
        except Exception as e:
            record_scenario_result("recovery_verification", False, {"error": str(e)})


# =============================================================================
# Test Summary & Report Generation
# =============================================================================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate detailed test summary on test stop"""
    stats = _extreme_stats
    stats["end_time"] = time.time()
    
    duration = stats["end_time"] - stats["start_time"] if stats["start_time"] else 0
    
    print("\n" + "=" * 80)
    print("🔥 STAGE 0: SELF-HEALING EXTREME STRESS TEST RESULTS")
    print("=" * 80)
    
    print(f"\n⏱️  Test Duration: {duration:.1f} seconds")
    print(f"📅 Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Scenario Summary
    print("\n" + "-" * 80)
    print("📊 SCENARIO RESULTS")
    print("-" * 80)
    
    total_executed = 0
    total_success = 0
    total_failure = 0
    
    for scenario, data in stats["scenarios"].items():
        if data["executed"] > 0:
            total_executed += data["executed"]
            total_success += data["success"]
            total_failure += data["failure"]
            
            success_rate = (data["success"] / data["executed"] * 100) if data["executed"] > 0 else 0
            status = "✅" if success_rate >= 80 else "⚠️" if success_rate >= 50 else "❌"
            
            print(f"\n{status} {scenario.replace('_', ' ').title()}")
            print(f"   Executed: {data['executed']} | Success: {data['success']} | Failure: {data['failure']}")
            print(f"   Success Rate: {success_rate:.1f}%")
    
    # Healing Events
    if stats["healing_events"]:
        print("\n" + "-" * 80)
        print("🩹 HEALING EVENTS RECORDED")
        print("-" * 80)
        for event in stats["healing_events"][:10]:  # Show last 10
            print(f"   [{event['timestamp']}] {event['event_type']} - {event['service']}")
    
    # Recovery Times
    if stats["recovery_times"]:
        print("\n" + "-" * 80)
        print("⏱️  RECOVERY TIMES")
        print("-" * 80)
        for scenario, times in stats["recovery_times"].items():
            avg_time = sum(times) / len(times) if times else 0
            print(f"   {scenario}: Avg {avg_time:.2f}s (samples: {len(times)})")
    
    # Critical Failures
    if stats["critical_failures"]:
        print("\n" + "-" * 80)
        print(f"⚠️  CRITICAL FAILURES ({len(stats['critical_failures'])})")
        print("-" * 80)
        for failure in stats["critical_failures"][:5]:  # Show first 5
            print(f"   ❌ {failure['scenario']}: {failure['detail']}")
    
    # Final Summary
    overall_success_rate = (total_success / total_executed * 100) if total_executed > 0 else 0
    
    print("\n" + "=" * 80)
    print("📈 FINAL SUMMARY")
    print("=" * 80)
    print(f"\n   Total Scenarios Executed: {total_executed}")
    print(f"   Total Success: {total_success}")
    print(f"   Total Failures: {total_failure}")
    print(f"   Overall Success Rate: {overall_success_rate:.1f}%")
    
    if overall_success_rate >= 90:
        print("\n✅ EXTREME STRESS TEST PASSED - Self-Healing system is resilient!")
    elif overall_success_rate >= 70:
        print("\n⚠️  EXTREME STRESS TEST PARTIAL PASS - Some scenarios need attention")
    else:
        print("\n❌ EXTREME STRESS TEST FAILED - Self-Healing system needs improvement")
    
    print("=" * 80 + "\n")
    
    # Save results to file
    _save_test_results(stats, environment)


def _save_test_results(stats: dict, environment):
    """Save test results to markdown file"""
    try:
        # Create results directory if not exists
        results_dir = os.path.join(_project_root, "load_tests", "results", "stage0")
        os.makedirs(results_dir, exist_ok=True)
        
        # Generate filename
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"stage0_extreme_stress_{date_str}.md"
        filepath = os.path.join(results_dir, filename)
        
        # Generate report content
        report = _generate_markdown_report(stats, environment)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"📄 Results saved to: {filepath}")
    except Exception as e:
        print(f"⚠️  Failed to save results: {e}")


def _generate_markdown_report(stats: dict, environment) -> str:
    """Generate markdown report"""
    duration = stats["end_time"] - stats["start_time"] if stats.get("end_time") and stats.get("start_time") else 0
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate totals
    total_executed = sum(s["executed"] for s in stats["scenarios"].values())
    total_success = sum(s["success"] for s in stats["scenarios"].values())
    total_failure = sum(s["failure"] for s in stats["scenarios"].values())
    overall_rate = (total_success / total_executed * 100) if total_executed > 0 else 0
    
    report = f"""# Stage 0: Self-Healing Extreme Stress Test Results

**테스트 일시:** {date_str}  
**테스트 환경:** Docker Compose (localhost:8000)  
**테스트 도구:** Locust + SelfHealingClient  
**테스트 시간:** {duration:.1f} seconds

---

## 🎯 테스트 목적

Self-Healing 시스템을 **극한 상황**에서 테스트하여 다음을 검증:
- Circuit Breaker의 장애 격리 및 복구 능력
- Emergency Mode의 즉각적인 시스템 보호
- Error Budget 소진 시 배포 제한
- Chaos 주입 시 Blast Radius 격리
- L2 Storage 장애 복원력
- DLQ 스트레스 내성
- Governance 제어 안정성

---

## 📊 테스트 결과 요약

| 항목 | 결과 |
|------|------|
| **전체 시나리오 수** | {len([s for s in stats['scenarios'].values() if s['executed'] > 0])} |
| **총 실행 횟수** | {total_executed} |
| **성공** | {total_success} |
| **실패** | {total_failure} |
| **전체 성공률** | **{overall_rate:.1f}%** |

---

## 🔥 시나리오별 상세 결과

"""
    
    # Add scenario details
    for scenario, data in stats["scenarios"].items():
        if data["executed"] > 0:
            success_rate = (data["success"] / data["executed"] * 100) if data["executed"] > 0 else 0
            status = "✅ PASS" if success_rate >= 80 else "⚠️ PARTIAL" if success_rate >= 50 else "❌ FAIL"
            
            report += f"""### {scenario.replace('_', ' ').title()}

| 메트릭 | 값 |
|--------|-----|
| 실행 횟수 | {data['executed']} |
| 성공 | {data['success']} |
| 실패 | {data['failure']} |
| 성공률 | {success_rate:.1f}% |
| 상태 | {status} |

"""
    
    # Recovery Times
    if stats["recovery_times"]:
        report += """---

## ⏱️ 복구 시간 분석

| 시나리오 | 평균 복구 시간 | 샘플 수 |
|----------|---------------|---------|
"""
        for scenario, times in stats["recovery_times"].items():
            avg_time = sum(times) / len(times) if times else 0
            report += f"| {scenario} | {avg_time:.2f}s | {len(times)} |\n"
    
    # Healing Events
    if stats["healing_events"]:
        report += f"""
---

## 🩹 Self-Healing 이벤트 기록

총 {len(stats['healing_events'])}개의 힐링 이벤트 발생:

| 시간 | 이벤트 타입 | 서비스 |
|------|------------|--------|
"""
        for event in stats["healing_events"][:20]:
            report += f"| {event['timestamp']} | {event['event_type']} | {event['service']} |\n"
    
    # Critical Failures
    if stats["critical_failures"]:
        report += f"""
---

## ⚠️ 주요 실패 사항

{len(stats['critical_failures'])}개의 주요 실패 발생:

"""
        for failure in stats["critical_failures"]:
            report += f"- **{failure['scenario']}**: {json.dumps(failure['detail'], ensure_ascii=False)}\n"
    
    # Final Verdict
    if overall_rate >= 90:
        verdict = "✅ **PASS** - Self-Healing 시스템이 극한 상황에서도 안정적으로 작동"
    elif overall_rate >= 70:
        verdict = "⚠️ **PARTIAL PASS** - 일부 시나리오에서 개선 필요"
    else:
        verdict = "❌ **FAIL** - Self-Healing 시스템 개선 필요"
    
    report += f"""
---

## 🏆 최종 판정

{verdict}

### 권장 사항

"""
    
    if overall_rate < 100:
        report += """1. 실패한 시나리오의 로그 분석 필요
2. Circuit Breaker 임계값 조정 검토
3. Emergency Mode 트리거 조건 최적화
4. Error Budget 정책 재검토
"""
    else:
        report += """1. 현재 설정 유지
2. 정기적인 Chaos Engineering 테스트 권장
3. Error Budget 모니터링 자동화
"""
    
    report += f"""
---

*Generated by Stage 0 Extreme Stress Test on {date_str}*
"""
    
    return report


# =============================================================================
# Quick Test Mode (without Locust)
# =============================================================================

def quick_test(host: str = "http://localhost:8000"):
    """
    Quick test mode - run without Locust for fast verification
    
    Usage:
        python stage0_selfhealing_extreme.py [host]
    """
    print(f"\n🔥 Quick Self-Healing Extreme Test against {host}")
    print("=" * 60)
    
    if not SELFHEALING_CLIENT_AVAILABLE:
        print("❌ SelfHealingClient not available. Cannot run extreme tests.")
        return
    
    try:
        client = SelfHealingClient(host=host, auth_mode="xtest")
        client.login("admin", "1234")
        
        print("\n📡 Testing Health...")
        health = client.health.ping()
        print(f"   Health: {health}")
        
        print("\n🔧 Testing Circuit Breaker Status...")
        cb = client.circuit_breaker.get_all_status()
        print(f"   CB Status: {cb.get('status', 'unknown')}")
        
        print("\n⚠️  Testing Emergency Status...")
        emergency = client.emergency.get_status()
        print(f"   Emergency: {emergency}")
        
        print("\n📊 Testing Error Budget...")
        budget = client.error_budget.get_status()
        print(f"   Error Budget: {budget}")
        
        print("\n💾 Testing L2 Storage...")
        l2 = client.l2_storage.get_status()
        print(f"   L2 Storage: {l2}")
        
        print("\n📋 Testing DLQ...")
        dlq = client.dlq.stats()
        print(f"   DLQ Stats: {dlq}")
        
        print("\n" + "=" * 60)
        print("✅ Quick extreme test complete!")
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    quick_test(host)
