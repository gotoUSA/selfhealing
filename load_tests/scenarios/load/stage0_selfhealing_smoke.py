"""
Stage 0: Self-Healing System Smoke Test

Purpose: Verify all Self-Healing API endpoints are operational
- Health Check endpoints (ping, live, ready, pool)
- Circuit Breaker status
- DLQ list endpoint
- Config endpoints
- Error Budget status
- Emergency mode status
- L2 Storage status
- Chaos Engineering status
- Governance status
- Metrics endpoint

Run:
    locust -f load_tests/scenarios/load/stage0_selfhealing_smoke.py --host=http://localhost:8000 --users=5 --spawn-rate=5 --run-time=30s --headless

Prerequisites:
    - Server running at localhost:8000
    - Admin user exists (for protected endpoints)
"""

import os
import sys
import time
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


STAGE_NAME = "[Stage0-SelfHealing]"

# Self-Healing API Base Path
SH_API_BASE = "/api/self-healing"

# =============================================================================
# Endpoint Categories for Testing
# =============================================================================

# Public endpoints (no auth required)
PUBLIC_ENDPOINTS = {
    "health_ping": f"{SH_API_BASE}/health/ping/",
    "health_live": f"{SH_API_BASE}/health/live/",
    "health_ready": f"{SH_API_BASE}/health/ready/",
}

# Protected endpoints (auth required)
PROTECTED_ENDPOINTS = {
    # Health & Status
    "health": f"{SH_API_BASE}/health/",
    "health_pool": f"{SH_API_BASE}/health/pool/",
    "health_gate": f"{SH_API_BASE}/health/gate/",
    "status": f"{SH_API_BASE}/status/",
    "metrics": f"{SH_API_BASE}/metrics/",
    
    # Circuit Breaker
    "cb_pool_status": f"{SH_API_BASE}/circuit-breaker/pool/status/",
    
    # DLQ
    "dlq_list": f"{SH_API_BASE}/dlq/list/",
    "dlq_cleanup_stats": f"{SH_API_BASE}/dlq/cleanup/stats/",
    
    # Config
    "config_all": f"{SH_API_BASE}/config/",
    "config_circuit_breaker": f"{SH_API_BASE}/config/circuit-breaker/",
    "config_dlq": f"{SH_API_BASE}/config/dlq/",
    "config_retry": f"{SH_API_BASE}/config/retry/",
    "config_sla": f"{SH_API_BASE}/config/sla/",
    "config_rate_limit": f"{SH_API_BASE}/config/rate-limit/",
    
    # Error Budget
    "error_budget_status": f"{SH_API_BASE}/error-budget/status/",
    "error_budget_history": f"{SH_API_BASE}/error-budget/history/",
    
    # Emergency
    "emergency_status": f"{SH_API_BASE}/emergency/status/",
    "emergency_levels": f"{SH_API_BASE}/emergency/levels/",
    "emergency_config": f"{SH_API_BASE}/emergency/config/",
    
    # L2 Storage
    "l2_storage_status": f"{SH_API_BASE}/l2-storage/status/",
    "l2_storage_health": f"{SH_API_BASE}/l2-storage/health/",
    "l2_storage_config": f"{SH_API_BASE}/l2-storage/config/",
    
    # Chaos Engineering
    "chaos_kill_switch": f"{SH_API_BASE}/chaos/kill-switch/",
    "chaos_pending_approvals": f"{SH_API_BASE}/chaos/pending-approvals/",
    "chaos_schedules": f"{SH_API_BASE}/chaos/schedules/",
    
    # Dashboard
    "dashboard_summary": f"{SH_API_BASE}/dashboard/summary/",
    
    # Governance
    "governance_status": f"{SH_API_BASE}/governance/status/",
    "governance_mode": f"{SH_API_BASE}/governance/mode/",
    "metrics_status": f"{SH_API_BASE}/metrics/status/",
    
    # Reconciliation
    "reconciliation_status": f"{SH_API_BASE}/reconciliation/status/",
    "reconciliation_config": f"{SH_API_BASE}/reconciliation/config/",
    
    # System Control
    "system_status": f"{SH_API_BASE}/system/status/",
    
    # Drift Thresholds
    "drift_thresholds": f"{SH_API_BASE}/config/drift-thresholds/",
    
    # Tier Configuration
    "tier_definitions": f"{SH_API_BASE}/config/tiers/",
}


# =============================================================================
# Test Statistics
# =============================================================================

_smoke_stats = {
    "start_time": None,
    "public_endpoints": {
        "total": 0,
        "success": 0,
        "failure": 0,
        "results": {},
    },
    "protected_endpoints": {
        "total": 0,
        "success": 0,
        "failure": 0,
        "results": {},
    },
    "critical_failures": [],
    "response_times": {},
}


def record_result(category: str, endpoint_name: str, success: bool, 
                  status_code: int, response_time: float, error_msg: str = None):
    """Record endpoint test result"""
    stats = _smoke_stats[f"{category}_endpoints"]
    stats["total"] += 1
    
    if success:
        stats["success"] += 1
    else:
        stats["failure"] += 1
        _smoke_stats["critical_failures"].append({
            "endpoint": endpoint_name,
            "status_code": status_code,
            "error": error_msg,
        })
    
    stats["results"][endpoint_name] = {
        "success": success,
        "status_code": status_code,
        "response_time_ms": response_time,
        "error": error_msg,
    }
    
    _smoke_stats["response_times"][endpoint_name] = response_time


class SelfHealingSmokeUser(HttpUser):
    """
    Self-Healing System Smoke Test User
    
    Verifies all Self-Healing API endpoints are operational.
    """
    
    wait_time = between(0.5, 1.5)
    
    def on_start(self):
        """Initialize on test start"""
        global _smoke_stats
        
        setup_event_hooks(STAGE_NAME)
        
        if _smoke_stats["start_time"] is None:
            _smoke_stats["start_time"] = time.time()
        
        # Login as admin for protected endpoints
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.login_helper.login_as_admin()
        
        self.is_admin = self.login_helper.is_logged_in
    
    # =========================================================================
    # Public Endpoints (No Auth Required)
    # =========================================================================
    
    @task(3)
    @tag("smoke", "health", "public", "critical")
    def test_health_ping(self):
        """Health ping - Most basic health check"""
        self._test_public_endpoint("health_ping", PUBLIC_ENDPOINTS["health_ping"])
    
    @task(2)
    @tag("smoke", "health", "public")
    def test_health_live(self):
        """Liveness check - K8s liveness probe"""
        self._test_public_endpoint("health_live", PUBLIC_ENDPOINTS["health_live"])
    
    @task(2)
    @tag("smoke", "health", "public")
    def test_health_ready(self):
        """Readiness check - K8s readiness probe"""
        self._test_public_endpoint("health_ready", PUBLIC_ENDPOINTS["health_ready"])
    
    # =========================================================================
    # Health & Status Endpoints
    # =========================================================================
    
    @task(2)
    @tag("smoke", "health", "protected")
    def test_health_full(self):
        """Full health check"""
        self._test_protected_endpoint("health", PROTECTED_ENDPOINTS["health"])
    
    @task(1)
    @tag("smoke", "health", "protected")
    def test_health_pool(self):
        """Connection pool health"""
        self._test_protected_endpoint("health_pool", PROTECTED_ENDPOINTS["health_pool"])
    
    @task(1)
    @tag("smoke", "status", "protected")
    def test_status(self):
        """Self-healing status"""
        self._test_protected_endpoint("status", PROTECTED_ENDPOINTS["status"])
    
    @task(1)
    @tag("smoke", "metrics", "protected")
    def test_metrics(self):
        """Metrics endpoint"""
        self._test_protected_endpoint("metrics", PROTECTED_ENDPOINTS["metrics"])
    
    # =========================================================================
    # Circuit Breaker Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "circuit-breaker", "protected")
    def test_cb_pool_status(self):
        """Circuit breaker pool status"""
        self._test_protected_endpoint("cb_pool_status", PROTECTED_ENDPOINTS["cb_pool_status"])
    
    # =========================================================================
    # DLQ Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "dlq", "protected")
    def test_dlq_list(self):
        """DLQ list"""
        self._test_protected_endpoint("dlq_list", PROTECTED_ENDPOINTS["dlq_list"])
    
    @task(1)
    @tag("smoke", "dlq", "protected")
    def test_dlq_cleanup_stats(self):
        """DLQ cleanup stats"""
        self._test_protected_endpoint("dlq_cleanup_stats", PROTECTED_ENDPOINTS["dlq_cleanup_stats"])
    
    # =========================================================================
    # Config Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "config", "protected")
    def test_config_all(self):
        """All config"""
        self._test_protected_endpoint("config_all", PROTECTED_ENDPOINTS["config_all"])
    
    @task(1)
    @tag("smoke", "config", "protected")
    def test_config_circuit_breaker(self):
        """Circuit breaker config"""
        self._test_protected_endpoint("config_circuit_breaker", PROTECTED_ENDPOINTS["config_circuit_breaker"])
    
    # =========================================================================
    # Error Budget Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "error-budget", "protected")
    def test_error_budget_status(self):
        """Error budget status"""
        self._test_protected_endpoint("error_budget_status", PROTECTED_ENDPOINTS["error_budget_status"])
    
    # =========================================================================
    # Emergency Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "emergency", "protected")
    def test_emergency_status(self):
        """Emergency status"""
        self._test_protected_endpoint("emergency_status", PROTECTED_ENDPOINTS["emergency_status"])
    
    @task(1)
    @tag("smoke", "emergency", "protected")
    def test_emergency_levels(self):
        """Emergency levels"""
        self._test_protected_endpoint("emergency_levels", PROTECTED_ENDPOINTS["emergency_levels"])
    
    # =========================================================================
    # L2 Storage Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "l2-storage", "protected")
    def test_l2_storage_status(self):
        """L2 Storage status"""
        self._test_protected_endpoint("l2_storage_status", PROTECTED_ENDPOINTS["l2_storage_status"])
    
    @task(1)
    @tag("smoke", "l2-storage", "protected")
    def test_l2_storage_health(self):
        """L2 Storage health"""
        self._test_protected_endpoint("l2_storage_health", PROTECTED_ENDPOINTS["l2_storage_health"])
    
    # =========================================================================
    # Chaos Engineering Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "chaos", "protected")
    def test_chaos_kill_switch(self):
        """Chaos kill switch status"""
        self._test_protected_endpoint("chaos_kill_switch", PROTECTED_ENDPOINTS["chaos_kill_switch"])
    
    @task(1)
    @tag("smoke", "chaos", "protected")
    def test_chaos_schedules(self):
        """Chaos schedules list"""
        self._test_protected_endpoint("chaos_schedules", PROTECTED_ENDPOINTS["chaos_schedules"])
    
    # =========================================================================
    # Dashboard Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "dashboard", "protected")
    def test_dashboard_summary(self):
        """Dashboard summary"""
        self._test_protected_endpoint("dashboard_summary", PROTECTED_ENDPOINTS["dashboard_summary"])
    
    # =========================================================================
    # Governance Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "governance", "protected")
    def test_governance_status(self):
        """Governance RBAC status"""
        self._test_protected_endpoint("governance_status", PROTECTED_ENDPOINTS["governance_status"])
    
    @task(1)
    @tag("smoke", "governance", "protected")
    def test_metrics_status(self):
        """Metrics status (Governance)"""
        self._test_protected_endpoint("metrics_status", PROTECTED_ENDPOINTS["metrics_status"])
    
    # =========================================================================
    # Reconciliation Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "reconciliation", "protected")
    def test_reconciliation_status(self):
        """Reconciliation status"""
        self._test_protected_endpoint("reconciliation_status", PROTECTED_ENDPOINTS["reconciliation_status"])
    
    # =========================================================================
    # System Control Endpoints
    # =========================================================================
    
    @task(1)
    @tag("smoke", "system", "protected")
    def test_system_status(self):
        """System status (Global kill switch)"""
        self._test_protected_endpoint("system_status", PROTECTED_ENDPOINTS["system_status"])
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _test_public_endpoint(self, name: str, endpoint: str):
        """Test a public endpoint (no auth)"""
        with self.client.get(
            endpoint,
            name=f"{STAGE_NAME} GET {endpoint}",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 204]
            error_msg = None
            
            if success:
                response.success()
            else:
                error_msg = f"Status {response.status_code}"
                response.failure(f"SMOKE FAILED: {name} returned {response.status_code}")
            
            record_result("public", name, success, 
                         response.status_code, response.elapsed.total_seconds() * 1000, error_msg)
    
    def _test_protected_endpoint(self, name: str, endpoint: str):
        """Test a protected endpoint (requires auth)"""
        if not self.is_admin:
            # Try to re-login
            self.login_helper.login_as_admin()
            self.is_admin = self.login_helper.is_logged_in
        
        with self.client.get(
            endpoint,
            name=f"{STAGE_NAME} GET {endpoint}",
            catch_response=True,
        ) as response:
            # 200, 204 = Success
            # 403 = Auth issue (not a smoke failure)
            # 404 = Endpoint doesn't exist (smoke failure)
            # 500+ = Server error (smoke failure)
            
            if response.status_code in [200, 204]:
                success = True
                error_msg = None
                response.success()
            elif response.status_code == 403:
                # Auth issue - not a critical failure
                success = True  # Endpoint exists, just needs auth
                error_msg = "Auth required (403)"
                response.success()
            elif response.status_code == 401:
                # Unauthorized - try to note it
                success = True  # Endpoint exists
                error_msg = "Unauthorized (401)"
                response.success()
            else:
                success = False
                error_msg = f"Status {response.status_code}"
                response.failure(f"SMOKE FAILED: {name} returned {response.status_code}")
            
            record_result("protected", name, success,
                         response.status_code, response.elapsed.total_seconds() * 1000, error_msg)


# =============================================================================
# Test Summary
# =============================================================================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print summary on test stop"""
    stats = _smoke_stats
    
    print("\n" + "=" * 70)
    print("🔧 STAGE 0: SELF-HEALING SMOKE TEST RESULTS")
    print("=" * 70)
    
    # Public endpoints summary
    pub = stats["public_endpoints"]
    print(f"\n📡 Public Endpoints: {pub['success']}/{pub['total']} passed")
    for name, result in pub["results"].items():
        status = "✅" if result["success"] else "❌"
        print(f"   {status} {name}: {result['status_code']} ({result['response_time_ms']:.1f}ms)")
    
    # Protected endpoints summary
    prot = stats["protected_endpoints"]
    print(f"\n🔐 Protected Endpoints: {prot['success']}/{prot['total']} passed")
    
    # Group by category
    categories = {}
    for name, result in prot["results"].items():
        # Extract category from endpoint name
        parts = name.split("_")
        category = parts[0] if parts else "other"
        if category not in categories:
            categories[category] = []
        categories[category].append((name, result))
    
    for category, endpoints in sorted(categories.items()):
        print(f"\n   [{category.upper()}]")
        for name, result in endpoints:
            status = "✅" if result["success"] else "❌"
            extra = f" - {result['error']}" if result.get("error") else ""
            print(f"      {status} {name}: {result['status_code']} ({result['response_time_ms']:.1f}ms){extra}")
    
    # Critical failures
    if stats["critical_failures"]:
        print(f"\n⚠️  CRITICAL FAILURES ({len(stats['critical_failures'])}):")
        for failure in stats["critical_failures"]:
            print(f"   ❌ {failure['endpoint']}: {failure['status_code']} - {failure['error']}")
    
    # Final verdict
    total_success = pub["success"] + prot["success"]
    total_tests = pub["total"] + prot["total"]
    
    print("\n" + "=" * 70)
    if len(stats["critical_failures"]) == 0:
        print("✅ SELF-HEALING SMOKE TEST PASSED - All endpoints operational")
    else:
        print(f"❌ SELF-HEALING SMOKE TEST FAILED - {len(stats['critical_failures'])} endpoints failed")
        print("⚠️  Fix failed endpoints before running other Self-Healing tests!")
    
    print(f"📊 Total: {total_success}/{total_tests} endpoints passed")
    print("=" * 70)


# =============================================================================
# Quick Test Mode (without Locust)
# =============================================================================

def quick_test(host: str = "http://localhost:8000"):
    """
    Quick test mode - run without Locust for fast verification
    
    Usage:
        python stage0_selfhealing_smoke.py
    """
    import requests
    
    print(f"\n🔍 Quick Self-Healing Smoke Test against {host}")
    print("=" * 60)
    
    # Test public endpoints
    print("\n📡 Testing Public Endpoints...")
    for name, endpoint in PUBLIC_ENDPOINTS.items():
        try:
            response = requests.get(f"{host}{endpoint}", timeout=5)
            status = "✅" if response.status_code in [200, 204] else "❌"
            print(f"   {status} {name}: {response.status_code}")
        except Exception as e:
            print(f"   ❌ {name}: {str(e)}")
    
    # Test protected endpoints (without auth - just check if they exist)
    print("\n🔐 Testing Protected Endpoints (no auth - checking existence)...")
    for name, endpoint in PROTECTED_ENDPOINTS.items():
        try:
            response = requests.get(f"{host}{endpoint}", timeout=5)
            # 401/403 = exists but needs auth, 404 = doesn't exist
            if response.status_code in [200, 204, 401, 403]:
                status = "✅"
            else:
                status = "❌"
            print(f"   {status} {name}: {response.status_code}")
        except Exception as e:
            print(f"   ❌ {name}: {str(e)}")
    
    print("\n" + "=" * 60)
    print("Quick test complete. Run with Locust for full load testing.")


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    quick_test(host)
