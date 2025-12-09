"""
Stage 10: Self-Healing Control API Test

Purpose: Validate functionality and performance of the Self-Healing Control API
- Measure Control API response times
- Test concurrent control request handling
- Verify role-based access control
- Validate environment-specific constraints

Execution:
    # Web UI mode
    locust -f load_tests/scenarios/stage10_self_healing.py --host=http://localhost:8000

    # CLI mode
    locust -f load_tests/scenarios/stage10_self_healing.py --host=http://localhost:8000 --users=50 --spawn-rate=5 --run-time=3m --headless --html=self_healing_report.html

Reference:
    - docs/self_healing/5_CONTROL_API/CONTROL_API_TEST_REQUIREMENTS.md
"""

import os
import sys
import random
import uuid
from datetime import datetime

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events


STAGE_NAME = "[Stage10-SelfHealing]"


# =============================================================================
# Test Statistics
# =============================================================================

_self_healing_stats = {
    "total_requests": 0,
    "by_action": {
        "allow": {"success": 0, "failure": 0},
        "block": {"success": 0, "failure": 0},
        "override": {"success": 0, "failure": 0},
        "reset": {"success": 0, "failure": 0},
        "inject_failure": {"success": 0, "failure": 0},
    },
    "by_environment": {
        "test": {"success": 0, "failure": 0},
        "chaos": {"success": 0, "failure": 0},
        "ops": {"success": 0, "failure": 0},
    },
    "governance_violations": {
        "inject_failure_in_ops": 0,
        "override_without_ttl": 0,
        "ttl_exceeded": 0,
    },
    "response_times": [],
}


def record_stat(action: str, environment: str, success: bool, response_time: float = 0):
    """Record test statistics"""
    _self_healing_stats["total_requests"] += 1

    status = "success" if success else "failure"
    _self_healing_stats["by_action"][action][status] += 1
    _self_healing_stats["by_environment"][environment][status] += 1

    if response_time > 0:
        _self_healing_stats["response_times"].append(response_time)


# =============================================================================
# Self-Healing Test Users
# =============================================================================


class SelfHealingAdminUser(HttpUser):
    """
    Self-Healing Control API Test User (Admin)

    Tests the Control API with administrator privileges.
    """

    wait_time = between(1, 3)

    # Target services for testing
    TARGET_SERVICES = ["payment", "inventory", "notification", "shipping"]

    # Test environments (ops used with restrictions)
    TEST_ENVIRONMENTS = ["test", "chaos", "ops"]

    def on_start(self):
        """Login on test start"""
        self.access_token = None
        self._login_as_admin()

    def _login_as_admin(self) -> bool:
        """Login as admin (using load_test_user_0)"""
        # Uses load_test_user_0 if no actual admin (requires is_staff=True)
        with self.client.post(
            "/api/auth/login/",
            json={
                "username": "load_test_user_0",
                "password": "testpass123",
            },
            name=f"{STAGE_NAME} Login Admin",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                # 토큰은 token.access에 있음
                token_data = data.get("token", {})
                self.access_token = token_data.get("access") or data.get("access")
                response.success()
                return True
            else:
                response.failure(f"Admin login failed: {response.status_code}")
                return False

    def _get_headers(self) -> dict:
        """인증 헤더 반환"""
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    # =========================================================================
    # Status Query Tests
    # =========================================================================

    @task(5)
    @tag("status", "read")
    def get_all_status(self):
        """Get all services status"""
        with self.client.get(
            "/api/self-healing/status/",
            headers=self._get_headers(),
            name=f"{STAGE_NAME} GET /status/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status failed: {response.status_code}")

    @task(3)
    @tag("status", "read")
    def get_service_status(self):
        """Get specific service status"""
        service = random.choice(self.TARGET_SERVICES)

        with self.client.get(
            f"/api/self-healing/status/{service}/",
            headers=self._get_headers(),
            name=f"{STAGE_NAME} GET /status/[service]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Service status failed: {response.status_code}")

    @task(2)
    @tag("health", "read")
    def health_check(self):
        """Self-Healing system health check"""
        with self.client.get(
            "/api/self-healing/health/",
            name=f"{STAGE_NAME} GET /health/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "healthy":
                    response.success()
                else:
                    response.failure(f"System degraded: {data}")
            else:
                response.failure(f"Health check failed: {response.status_code}")

    # =========================================================================
    # Control Action Tests (Test/Chaos Environments)
    # =========================================================================

    @task(3)
    @tag("control", "allow")
    def test_allow_action(self):
        """Test allow action (test environment)"""
        service = random.choice(self.TARGET_SERVICES)
        request_id = str(uuid.uuid4())

        payload = {
            "service_name": service,
            "action": "allow",
            "environment": "test",
            "reason": f"Load test allow - {request_id}",
            "request_id": request_id,
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (allow)",
            catch_response=True,
        ) as response:
            success = response.status_code == 200
            record_stat("allow", "test", success, response.elapsed.total_seconds() * 1000)

            if success:
                data = response.json()
                if data.get("status") == "success":
                    response.success()
                else:
                    response.failure(f"Allow action returned: {data.get('status')}")
            else:
                response.failure(f"Allow failed: {response.status_code}")

    @task(3)
    @tag("control", "block")
    def test_block_action(self):
        """Test block action (test environment)"""
        service = random.choice(self.TARGET_SERVICES)
        request_id = str(uuid.uuid4())

        payload = {
            "service_name": service,
            "action": "block",
            "environment": "test",
            "reason": f"Load test block - {request_id}",
            "ttl_minutes": 5,
            "request_id": request_id,
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (block)",
            catch_response=True,
        ) as response:
            success = response.status_code == 200
            record_stat("block", "test", success, response.elapsed.total_seconds() * 1000)

            if success:
                response.success()
            else:
                response.failure(f"Block failed: {response.status_code}")

    @task(2)
    @tag("control", "reset")
    def test_reset_action(self):
        """Test reset action (test environment)"""
        service = random.choice(self.TARGET_SERVICES)

        payload = {
            "service_name": service,
            "action": "reset",
            "environment": "test",
            "reason": "Load test reset",
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (reset)",
            catch_response=True,
        ) as response:
            success = response.status_code == 200
            record_stat("reset", "test", success, response.elapsed.total_seconds() * 1000)

            if success:
                response.success()
            else:
                response.failure(f"Reset failed: {response.status_code}")

    @task(1)
    @tag("control", "chaos")
    def test_inject_failure_chaos(self):
        """Test inject_failure action (chaos environment)"""
        service = random.choice(self.TARGET_SERVICES)

        payload = {
            "service_name": service,
            "action": "inject_failure",
            "environment": "chaos",
            "reason": "Load test chaos injection",
            "ttl_minutes": 2,
            "metadata": {
                "failure_rate": 0.3,
                "failure_type": "timeout",
            },
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (inject_failure-chaos)",
            catch_response=True,
        ) as response:
            success = response.status_code == 200
            record_stat("inject_failure", "chaos", success, response.elapsed.total_seconds() * 1000)

            if success:
                response.success()
            else:
                response.failure(f"Inject failure failed: {response.status_code}")

    # =========================================================================
    # Governance Violation Tests (should be rejected normally)
    # =========================================================================

    @task(1)
    @tag("governance", "violation")
    def test_inject_failure_ops_forbidden(self):
        """Test inject_failure in ops rejection"""
        payload = {
            "service_name": "payment",
            "action": "inject_failure",
            "environment": "ops",
            "reason": "This should be rejected",
            "ttl_minutes": 5,
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (inject_ops-FORBIDDEN)",
            catch_response=True,
        ) as response:
            # Expected: 403 or 400 (rejection)
            if response.status_code in [400, 403]:
                _self_healing_stats["governance_violations"]["inject_failure_in_ops"] += 1
                response.success()  # Correctly rejected
            else:
                response.failure(f"Expected rejection but got: {response.status_code}")

    @task(1)
    @tag("governance", "violation")
    def test_override_without_ttl_ops(self):
        """Test override in ops without TTL rejection"""
        payload = {
            "service_name": "payment",
            "action": "override",
            "environment": "ops",
            "reason": "This should be rejected - no TTL",
            # ttl_minutes omitted - should be rejected
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (override_no_ttl-FORBIDDEN)",
            catch_response=True,
        ) as response:
            if response.status_code in [400, 403]:
                _self_healing_stats["governance_violations"]["override_without_ttl"] += 1
                response.success()
            else:
                response.failure(f"Expected rejection but got: {response.status_code}")

    @task(1)
    @tag("governance", "violation")
    def test_override_ttl_exceeded_ops(self):
        """Test override in ops with TTL > 60 rejection"""
        payload = {
            "service_name": "payment",
            "action": "override",
            "environment": "ops",
            "reason": "This should be rejected - TTL too long",
            "ttl_minutes": 120,  # Exceeds 60 minutes
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (override_ttl_long-FORBIDDEN)",
            catch_response=True,
        ) as response:
            if response.status_code in [400, 403]:
                _self_healing_stats["governance_violations"]["ttl_exceeded"] += 1
                response.success()
            else:
                response.failure(f"Expected rejection but got: {response.status_code}")

    # =========================================================================
    # Quick Action Tests
    # =========================================================================

    @task(2)
    @tag("quick", "allow")
    def quick_allow(self):
        """Quick Allow test"""
        service = random.choice(self.TARGET_SERVICES)

        with self.client.post(
            f"/api/self-healing/allow/{service}/",
            json={"reason": "Quick allow test"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /allow/[service]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Quick allow failed: {response.status_code}")

    @task(2)
    @tag("quick", "block")
    def quick_block(self):
        """Quick Block test"""
        service = random.choice(self.TARGET_SERVICES)

        with self.client.post(
            f"/api/self-healing/block/{service}/",
            json={"reason": "Quick block test", "ttl_minutes": 5},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /block/[service]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Quick block failed: {response.status_code}")

    @task(1)
    @tag("quick", "reset")
    def quick_reset(self):
        """Quick Reset test"""
        service = random.choice(self.TARGET_SERVICES)

        with self.client.post(
            f"/api/self-healing/reset/{service}/",
            json={"reason": "Quick reset test"},
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /reset/[service]/",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Quick reset failed: {response.status_code}")


# =============================================================================
# Read-Only User (Non-Admin)
# =============================================================================


class SelfHealingReadOnlyUser(HttpUser):
    """
    Self-Healing Read-Only User

    Regular user permissions - can only view status.
    Control actions should be rejected.
    """

    wait_time = between(2, 5)
    weight = 3  # Lower ratio than Admin

    def on_start(self):
        """Login on test start"""
        self.access_token = None
        self._login_as_user()

    def _login_as_user(self) -> bool:
        """Login as regular user"""
        import random
        user_idx = random.randint(1, 99)
        with self.client.post(
            "/api/auth/login/",
            json={
                "username": f"load_test_user_{user_idx}",
                "password": "testpass123",
            },
            name=f"{STAGE_NAME} Login User",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                # Token is in token.access
                token_data = data.get("token", {})
                self.access_token = token_data.get("access") or data.get("access")
                response.success()
                return True
            else:
                # Continue test even if login fails (unauthenticated state test)
                response.success()
                return False

    def _get_headers(self) -> dict:
        """Return authentication headers"""
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    @task(5)
    @tag("status", "read")
    def get_status(self):
        """Status query (read is allowed)"""
        with self.client.get(
            "/api/self-healing/status/",
            headers=self._get_headers(),
            name=f"{STAGE_NAME} GET /status/ (user)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status read failed: {response.status_code}")

    @task(2)
    @tag("control", "unauthorized")
    def control_should_fail(self):
        """Control action attempt (should be rejected)"""
        payload = {
            "service_name": "payment",
            "action": "allow",
            "environment": "test",
            "reason": "Unauthorized attempt",
        }

        with self.client.post(
            "/api/self-healing/control/",
            json=payload,
            headers=self._get_headers(),
            name=f"{STAGE_NAME} POST /control/ (user-UNAUTHORIZED)",
            catch_response=True,
        ) as response:
            # Expected: 401 or 403
            if response.status_code in [401, 403]:
                response.success()  # Correctly rejected
            else:
                response.failure(f"Expected 401/403 but got: {response.status_code}")


# =============================================================================
# Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Executed on test start"""
    print("\n" + "=" * 70)
    print(f"🛡️  {STAGE_NAME} Self-Healing Control API Test Started")
    print("=" * 70)
    print("📋 Test Items:")
    print("   - Control API response time")
    print("   - Concurrent control request handling")
    print("   - Role-based access control")
    print("   - Governance rule validation")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Executed on test stop"""
    print("\n" + "=" * 70)
    print(f"✅ {STAGE_NAME} Test Completed")
    print("=" * 70)

    print(f"\n📊 Total Requests: {_self_healing_stats['total_requests']}")

    print("\n📈 Statistics by Action:")
    for action, stats in _self_healing_stats["by_action"].items():
        total = stats["success"] + stats["failure"]
        if total > 0:
            success_rate = (stats["success"] / total) * 100
            print(f"   - {action}: {total} requests (success rate: {success_rate:.1f}%)")

    print("\n🌍 Statistics by Environment:")
    for env, stats in _self_healing_stats["by_environment"].items():
        total = stats["success"] + stats["failure"]
        if total > 0:
            print(f"   - {env}: {total} requests")

    print("\n🚫 Governance Violations Detected (correctly rejected):")
    for violation, count in _self_healing_stats["governance_violations"].items():
        if count > 0:
            print(f"   - {violation}: {count} rejected ✓")

    # Response time statistics
    if _self_healing_stats["response_times"]:
        times = _self_healing_stats["response_times"]
        avg_time = sum(times) / len(times)
        max_time = max(times)
        min_time = min(times)
        print(f"\n⏱️  Response Times:")
        print(f"   - Avg: {avg_time:.2f}ms")
        print(f"   - Min: {min_time:.2f}ms")
        print(f"   - Max: {max_time:.2f}ms")

    print("\n" + "=" * 70 + "\n")
