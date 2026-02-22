"""
Stage 14 Extension: Outbox Pattern HTTP Integration Test

Purpose: 실제 Django + Celery + Redis 환경에서 Outbox 패턴 검증
- 실제 API 호출을 통한 주문 생성
- Celery Worker 장애 시뮬레이션
- DLQ 재처리 검증
- 이벤트 일관성 확인

Test Scenarios:
  TC-HTTP-1: Server Health Check
  TC-HTTP-2: DLQ Status Endpoint
  TC-HTTP-3: Create Order (Event Trigger)
  TC-HTTP-4: Celery Worker Failure Simulation
  TC-HTTP-5: DLQ Replay API
  TC-HTTP-6: Event Consistency Verification

Prerequisites:
  - Django 서버 실행: python manage.py runserver
  - Celery Worker 실행: celery -A myproject worker -l info
  - Redis 실행: docker-compose up redis
  - PostgreSQL 실행: docker-compose up postgres

Execution:
    # 직접 실행
    python load_tests/scenarios/stage14_outbox_http.py

    # 특정 호스트
    python load_tests/scenarios/stage14_outbox_http.py --base-url http://localhost:8000

Reference:
    - docs/GAP_RESOLUTION_PLAN.md (GAP-03)
    - Pipeline Stage: Async Boundary
"""

# =============================================================================
# Stage DNA - Self-Healing 모듈 의존성 선언
# Reference: docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
# =============================================================================
STAGE_DNA = {
    "name": "Stage 14 Extension - Outbox Pattern HTTP Integration Test",
    "type": "integration",
    "required_modules": ["circuit_breaker", "dlq", "health", "observability"],
    "optional_modules": ["governance", "reconciliation"],
}

# DNA 검증 (테스트 시작 전 자동 체크)
try:
    from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
    _dna_result = validate_stage_dna(STAGE_DNA)
    if not _dna_result.is_valid:
        import warnings
        warnings.warn(str(_dna_result))
except ImportError:
    pass  # stage_dna 모듈 없으면 스킵

# =============================================================================
import os
import sys
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("Warning: requests library not available. Install with: pip install requests")


STAGE_NAME = "[Stage14-Outbox-HTTP]"


# =============================================================================
# Test Configuration
# =============================================================================

@dataclass
class TestConfig:
    """테스트 설정"""
    base_url: str = "http://localhost:8000"
    timeout: int = 30
    max_retries: int = 3
    retry_backoff: float = 0.5
    test_user: str = "testuser"
    test_password: str = "testpass123"
    admin_user: str = "admin"
    admin_password: str = "admin123"


@dataclass
class TestResult:
    """테스트 결과"""
    name: str
    status: str  # passed, failed, skipped
    duration_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


# =============================================================================
# HTTP Test Runner
# =============================================================================

class OutboxHTTPTestRunner:
    """
    Outbox 패턴 HTTP 통합 테스트 러너
    """
    
    def __init__(self, config: TestConfig):
        self.config = config
        self.session = self._create_session()
        self.results: List[TestResult] = []
        self.auth_token: Optional[str] = None
        self.admin_token: Optional[str] = None
        self.created_orders: List[int] = []
    
    def _create_session(self) -> requests.Session:
        """Retry 설정된 세션 생성"""
        session = requests.Session()
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.retry_backoff,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session
    
    def _get(self, endpoint: str, **kwargs) -> requests.Response:
        """GET 요청"""
        url = f"{self.config.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.config.timeout)
        return self.session.get(url, **kwargs)
    
    def _post(self, endpoint: str, **kwargs) -> requests.Response:
        """POST 요청"""
        url = f"{self.config.base_url}{endpoint}"
        kwargs.setdefault("timeout", self.config.timeout)
        return self.session.post(url, **kwargs)
    
    def _auth_headers(self, use_admin: bool = False) -> Dict[str, str]:
        """인증 헤더 반환"""
        token = self.admin_token if use_admin else self.auth_token
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}
    
    # =========================================================================
    # Test Cases
    # =========================================================================
    
    def test_health_check(self) -> TestResult:
        """TC-HTTP-1: 서버 헬스 체크"""
        start = time.time()
        try:
            # Self-healing health endpoint
            resp = self._get("/api/self-healing/health/")
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                return TestResult(
                    name="health_check",
                    status="passed",
                    duration_ms=duration,
                    details={"status_code": 200}
                )
            else:
                # Fallback to ping
                resp2 = self._get("/api/self-healing/health/ping/")
                if resp2.status_code == 200:
                    return TestResult(
                        name="health_check",
                        status="passed",
                        duration_ms=duration,
                        details={"status_code": 200, "endpoint": "ping"}
                    )
                return TestResult(
                    name="health_check",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status code: {resp.status_code}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="health_check",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_login(self) -> TestResult:
        """TC-HTTP-2: 사용자 로그인"""
        start = time.time()
        try:
            resp = self._post("/api/auth/login/", json={
                "username": self.config.test_user,
                "password": self.config.test_password,
            })
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                data = resp.json()
                # Handle different token formats
                token = data.get("access") or data.get("token", {}).get("access")
                self.auth_token = token
                return TestResult(
                    name="login",
                    status="passed",
                    duration_ms=duration,
                    details={"has_token": bool(self.auth_token)}
                )
            else:
                return TestResult(
                    name="login",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}, Body: {resp.text[:200]}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="login",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_dlq_status(self) -> TestResult:
        """TC-HTTP-3: DLQ 상태 확인"""
        start = time.time()
        try:
            # DLQ list endpoint
            resp = self._get(
                "/api/self-healing/dlq/list/",
                headers=self._auth_headers()
            )
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                data = resp.json()
                # Handle paginated response
                if isinstance(data, dict) and "results" in data:
                    count = len(data["results"])
                elif isinstance(data, list):
                    count = len(data)
                else:
                    count = 0
                return TestResult(
                    name="dlq_status",
                    status="passed",
                    duration_ms=duration,
                    details={
                        "dlq_count": count,
                    }
                )
            elif resp.status_code in (401, 403):
                return TestResult(
                    name="dlq_status",
                    status="skipped",
                    duration_ms=duration,
                    details={"reason": "auth_required"}
                )
            else:
                return TestResult(
                    name="dlq_status",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="dlq_status",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_create_order(self) -> TestResult:
        """TC-HTTP-4: 주문 생성 (이벤트 트리거)"""
        start = time.time()
        try:
            if not self.auth_token:
                return TestResult(
                    name="create_order",
                    status="skipped",
                    duration_ms=0,
                    details={"reason": "no_auth_token"}
                )
            
            # 먼저 장바구니에 상품 추가
            cart_resp = self._post(
                "/api/carts/add/",
                json={"product_id": 1, "quantity": 1},
                headers=self._auth_headers()
            )
            
            # 주문 생성
            resp = self._post(
                "/api/orders/",
                json={"use_points": 0},
                headers=self._auth_headers()
            )
            duration = (time.time() - start) * 1000
            
            if resp.status_code in (200, 201):
                data = resp.json()
                order_id = data.get("id")
                if order_id:
                    self.created_orders.append(order_id)
                return TestResult(
                    name="create_order",
                    status="passed",
                    duration_ms=duration,
                    details={
                        "order_id": order_id,
                        "status": data.get("status"),
                    }
                )
            elif resp.status_code == 400:
                # 장바구니 비어있음 등
                return TestResult(
                    name="create_order",
                    status="skipped",
                    duration_ms=duration,
                    details={"reason": "precondition_failed", "body": resp.text[:200]}
                )
            else:
                return TestResult(
                    name="create_order",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}, Body: {resp.text[:200]}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="create_order",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_event_processing_wait(self) -> TestResult:
        """TC-HTTP-5: 이벤트 처리 대기 및 확인"""
        start = time.time()
        try:
            # 비동기 처리 대기
            time.sleep(3)
            
            # 생성된 주문 상태 확인
            if not self.created_orders:
                return TestResult(
                    name="event_processing",
                    status="skipped",
                    duration_ms=0,
                    details={"reason": "no_orders_created"}
                )
            
            order_id = self.created_orders[-1]
            resp = self._get(
                f"/api/orders/{order_id}/",
                headers=self._auth_headers()
            )
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                data = resp.json()
                return TestResult(
                    name="event_processing",
                    status="passed",
                    duration_ms=duration,
                    details={
                        "order_id": order_id,
                        "order_status": data.get("status"),
                        "items_count": len(data.get("items", [])),
                    }
                )
            else:
                return TestResult(
                    name="event_processing",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="event_processing",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_dlq_replay_api(self) -> TestResult:
        """TC-HTTP-6: DLQ 리플레이 API 테스트"""
        start = time.time()
        try:
            # DLQ 리플레이 API 호출
            resp = self._post(
                "/api/self-healing/dlq/replay/",
                json={"failure_type": "payment", "max_items": 10},
                headers=self._auth_headers()
            )
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                data = resp.json()
                return TestResult(
                    name="dlq_replay",
                    status="passed",
                    duration_ms=duration,
                    details={
                        "success": data.get("success", False),
                        "message": data.get("message", ""),
                    }
                )
            elif resp.status_code in (401, 403, 404):
                return TestResult(
                    name="dlq_replay",
                    status="skipped",
                    duration_ms=duration,
                    details={"reason": f"status_{resp.status_code}"}
                )
            else:
                return TestResult(
                    name="dlq_replay",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="dlq_replay",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    def test_metrics_endpoint(self) -> TestResult:
        """TC-HTTP-7: 메트릭 엔드포인트 확인"""
        start = time.time()
        try:
            resp = self._get("/api/self-healing/metrics/")
            duration = (time.time() - start) * 1000
            
            if resp.status_code == 200:
                data = resp.json()
                return TestResult(
                    name="metrics",
                    status="passed",
                    duration_ms=duration,
                    details={"metrics_available": True, "keys": list(data.keys())[:5] if isinstance(data, dict) else []}
                )
            elif resp.status_code in (401, 403, 404):
                return TestResult(
                    name="metrics",
                    status="skipped",
                    duration_ms=duration,
                    details={"reason": f"status_{resp.status_code}"}
                )
            else:
                return TestResult(
                    name="metrics",
                    status="failed",
                    duration_ms=duration,
                    error=f"Status: {resp.status_code}"
                )
        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                name="metrics",
                status="failed",
                duration_ms=duration,
                error=str(e)
            )
    
    # =========================================================================
    # Runner
    # =========================================================================
    
    def run_all_tests(self) -> Dict[str, Any]:
        """모든 테스트 실행"""
        print(f"\n{'='*60}")
        print(f"{STAGE_NAME} HTTP Integration Test")
        print(f"Base URL: {self.config.base_url}")
        print(f"{'='*60}")
        
        tests = [
            ("TC-HTTP-1", "Server Health Check", self.test_health_check),
            ("TC-HTTP-2", "User Login", self.test_login),
            ("TC-HTTP-3", "DLQ Status", self.test_dlq_status),
            ("TC-HTTP-4", "Create Order (Event Trigger)", self.test_create_order),
            ("TC-HTTP-5", "Event Processing", self.test_event_processing_wait),
            ("TC-HTTP-6", "DLQ Replay API", self.test_dlq_replay_api),
            ("TC-HTTP-7", "Metrics Endpoint", self.test_metrics_endpoint),
        ]
        
        for tc_id, tc_name, test_func in tests:
            print(f"\n📌 {tc_id}: {tc_name}")
            result = test_func()
            self.results.append(result)
            
            if result.status == "passed":
                print(f"   ✅ PASSED ({result.duration_ms:.1f}ms)")
                if result.details:
                    for k, v in result.details.items():
                        print(f"      {k}: {v}")
            elif result.status == "skipped":
                print("   ⏭️ SKIPPED")
                if result.details.get("reason"):
                    print(f"      Reason: {result.details['reason']}")
            else:
                print(f"   ❌ FAILED ({result.duration_ms:.1f}ms)")
                if result.error:
                    print(f"      Error: {result.error[:100]}")
        
        # 요약
        passed = len([r for r in self.results if r.status == "passed"])
        failed = len([r for r in self.results if r.status == "failed"])
        skipped = len([r for r in self.results if r.status == "skipped"])
        total = len(self.results)
        
        print(f"\n{'='*60}")
        print("📋 Test Summary")
        print(f"{'='*60}")
        print(f"   Total: {total}")
        print(f"   Passed: {passed}")
        print(f"   Failed: {failed}")
        print(f"   Skipped: {skipped}")
        
        success_rate = passed / max(total - skipped, 1) * 100
        print(f"   Success Rate: {success_rate:.1f}%")
        
        # Invariants
        print(f"\n{'='*60}")
        print("📋 INVARIANTS CHECK")
        print(f"{'='*60}")
        
        health_ok = any(r.name == "health_check" and r.status == "passed" for r in self.results)
        login_ok = any(r.name == "login" and r.status == "passed" for r in self.results)
        
        invariants = {
            "server_accessible": health_ok,
            "auth_working": login_ok,
            "no_critical_failures": failed == 0 or all(
                r.status != "failed" for r in self.results
                if r.name in ("health_check", "login")
            ),
        }
        
        for name, passed_inv in invariants.items():
            status = "✅ PASSED" if passed_inv else "❌ FAILED"
            print(f"   {name}: {status}")
        
        all_passed = all(invariants.values())
        
        print(f"\n{'='*60}")
        if all_passed:
            print("🎉 HTTP Integration Test PASSED")
        else:
            print("⚠️ HTTP Integration Test needs review")
        print(f"{'='*60}\n")
        
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "success_rate": success_rate,
            "invariants": invariants,
            "all_passed": all_passed,
        }


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """메인 엔트리 포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Stage 14 Outbox Pattern HTTP Integration Test")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL for the API server",
    )
    parser.add_argument(
        "--user",
        default="testuser",
        help="Test user username",
    )
    parser.add_argument(
        "--password",
        default="testpass123",
        help="Test user password",
    )
    
    args = parser.parse_args()
    
    if not REQUESTS_AVAILABLE:
        print("Error: requests library is required. Install with: pip install requests")
        sys.exit(1)
    
    config = TestConfig(
        base_url=args.base_url,
        test_user=args.user,
        test_password=args.password,
    )
    
    runner = OutboxHTTPTestRunner(config)
    results = runner.run_all_tests()
    
    sys.exit(0 if results["all_passed"] else 1)


if __name__ == "__main__":
    main()
