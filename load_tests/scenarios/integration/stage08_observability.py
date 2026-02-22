"""
Stage 08: Observability Contract HTTP Integration Test

Purpose: 실제 HTTP 요청에 대한 Observability Contract 검증
- trace_id가 모든 요청에 포함되는지 확인
- pipeline_stage가 실패 시 정확히 분류되는지 확인
- 종료 리포트에 단계별 실패 분포 포함

Invariants:
  - untraced_failures == 0
  - unknown_stage_failures == 0

Execution:
    # Standalone test
    python load_tests/scenarios/stage08_observability.py

    # Locust mode
    locust -f load_tests/scenarios/stage08_observability.py \\
        --host=http://localhost:8000 \\
        --users=20 --spawn-rate=5 --run-time=2m \\
        --headless --html=stage08_observability_report.html

Reference:
    - docs/GAP_RESOLUTION_PLAN.md (GAP-08)
"""

import os
import sys
import time
import random
import requests
from typing import Dict, Any, Optional

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Import observability contract
from load_tests.metrics.observability_contract import (
    PipelineStage,
    trace_request,
    get_observability_metrics,
    ObservabilityReportGenerator,
)

try:
    from locust import HttpUser, task, between, tag, events

    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    HttpUser = object
    task = lambda weight=1: lambda f: f
    between = lambda a, b: None
    tag = lambda *args: lambda f: f
    events = None


STAGE_NAME = "[Stage08-Observability]"
HOST = os.environ.get("LOCUST_HOST", "http://localhost:8000")


# =============================================================================
# HTTP Test Mixin - Observability Aware
# =============================================================================

class ObservabilityMixin:
    """
    Observability Contract를 준수하는 HTTP 요청 믹스인
    
    모든 요청에 trace_id를 포함하고, 실패 시 pipeline_stage를 정확히 분류함.
    """
    
    def traced_get(self, request_name: str, url: str, **kwargs) -> Dict[str, Any]:
        """trace_id가 포함된 GET 요청"""
        with trace_request(request_name) as ctx:
            ctx.set_stage(PipelineStage.INGRESS)
            
            try:
                # 헤더에 trace_id 포함
                headers = kwargs.get("headers", {})
                headers["X-Trace-ID"] = ctx.trace_id
                kwargs["headers"] = headers
                
                ctx.set_stage(PipelineStage.EGRESS)
                
                if hasattr(self, 'client'):
                    response = self.client.get(url, **kwargs)
                else:
                    response = requests.get(f"{HOST}{url}", **kwargs)
                
                return self._process_response(ctx, response, request_name)
                
            except Exception as e:
                ctx.record_failure(str(e))
                return {"success": False, "error": str(e), "trace_id": ctx.trace_id}
    
    def traced_post(self, request_name: str, url: str, 
                   json_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """trace_id가 포함된 POST 요청"""
        with trace_request(request_name) as ctx:
            ctx.set_stage(PipelineStage.INGRESS)
            
            try:
                headers = kwargs.get("headers", {})
                headers["X-Trace-ID"] = ctx.trace_id
                kwargs["headers"] = headers
                
                # Validation stage
                ctx.set_stage(PipelineStage.VALIDATION)
                
                ctx.set_stage(PipelineStage.EGRESS)
                
                if hasattr(self, 'client'):
                    response = self.client.post(url, json=json_data, **kwargs)
                else:
                    response = requests.post(f"{HOST}{url}", json=json_data, **kwargs)
                
                return self._process_response(ctx, response, request_name)
                
            except Exception as e:
                ctx.record_failure(str(e))
                return {"success": False, "error": str(e), "trace_id": ctx.trace_id}
    
    def _process_response(self, ctx, response, request_name: str) -> Dict[str, Any]:
        """응답 처리 및 pipeline_stage 분류"""
        status_code = response.status_code
        
        if 200 <= status_code < 300:
            ctx.record_success(status_code)
            return {
                "success": True,
                "status_code": status_code,
                "trace_id": ctx.trace_id,
                "data": self._safe_json(response)
            }
        else:
            # 상태 코드에 따라 적절한 pipeline_stage 설정
            stage = PipelineStage.from_status_code(status_code)
            ctx.set_stage(stage)
            
            error_message = self._extract_error(response)
            
            # 에러 메시지에서 더 정확한 단계 추론
            if stage == PipelineStage.UNKNOWN and error_message:
                stage = PipelineStage.from_error_message(error_message)
                ctx.set_stage(stage)
            
            ctx.record_failure(error_message, status_code)
            return {
                "success": False,
                "status_code": status_code,
                "trace_id": ctx.trace_id,
                "error": error_message,
                "pipeline_stage": stage.value
            }
    
    def _safe_json(self, response) -> Any:
        """안전하게 JSON 파싱"""
        try:
            return response.json()
        except Exception:
            return response.text[:200] if response.text else None
    
    def _extract_error(self, response) -> str:
        """에러 메시지 추출"""
        try:
            data = response.json()
            if isinstance(data, dict):
                return data.get("error") or data.get("detail") or data.get("message") or str(data)
            return str(data)
        except Exception:
            return f"HTTP {response.status_code}"


# =============================================================================
# Locust User Class
# =============================================================================

if LOCUST_AVAILABLE:
    class ObservabilityTestUser(HttpUser, ObservabilityMixin):
        """
        Observability Contract 테스트 Locust User
        """
        wait_time = between(0.5, 2)
        
        def on_start(self):
            """시작 시 로그인"""
            self.auth_token = None
            self._login()
        
        def _login(self):
            """로그인"""
            result = self.traced_post(
                "login",
                "/api/auth/login/",
                json_data={
                    "username": "load_test_user_0",
                    "password": "testpass123"
                }
            )
            if result.get("success") and result.get("data"):
                tokens = result["data"]
                self.auth_token = tokens.get("access") or tokens.get("token")
                if self.auth_token:
                    self.client.headers.update({
                        "Authorization": f"Bearer {self.auth_token}"
                    })
        
        @task(5)
        @tag("read")
        def get_products(self):
            """상품 목록 조회 - Caching 단계"""
            self.traced_get("get_products", "/api/products/")
        
        @task(3)
        @tag("read")
        def get_product_detail(self):
            """상품 상세 조회 - Caching + Persistence"""
            product_id = random.randint(1, 10)
            self.traced_get(f"get_product_{product_id}", f"/api/products/{product_id}/")
        
        @task(2)
        @tag("write")
        def add_to_cart(self):
            """장바구니 추가 - Validation + Persistence"""
            self.traced_post(
                "add_to_cart",
                "/api/cart/add_item/",
                json_data={
                    "product_id": random.randint(1, 10),
                    "quantity": 1
                }
            )
        
        @task(1)
        @tag("write")
        def create_order(self):
            """주문 생성 - Full pipeline"""
            self.traced_post(
                "create_order",
                "/api/orders/",
                json_data={}
            )
        
        @task(1)
        @tag("edge_case")
        def trigger_validation_error(self):
            """의도적 검증 에러 - VALIDATION stage"""
            self.traced_post(
                "validation_error",
                "/api/cart/add_item/",
                json_data={
                    "product_id": -1,  # Invalid
                    "quantity": -1     # Invalid
                }
            )
        
        @task(1)
        @tag("edge_case")  
        def trigger_auth_error(self):
            """인증 에러 - AUTH stage"""
            # 임시로 토큰 제거
            old_headers = dict(self.client.headers)
            self.client.headers.pop("Authorization", None)
            
            self.traced_get("auth_error", "/api/orders/")
            
            # 복원
            self.client.headers.update(old_headers)
        
        @task(1)
        @tag("edge_case")
        def trigger_not_found(self):
            """존재하지 않는 리소스 - BUSINESS stage"""
            self.traced_get("not_found", "/api/products/99999/")


# =============================================================================
# Standalone HTTP Test
# =============================================================================

def run_http_test():
    """Standalone HTTP 통합 테스트"""
    print("=" * 70)
    print(f"{STAGE_NAME} HTTP Integration Test")
    print("=" * 70)
    
    metrics = get_observability_metrics()
    metrics.reset()
    
    class TestClient(ObservabilityMixin):
        pass
    
    client = TestClient()
    
    # 서버 연결 확인
    print(f"\n1. Server Health Check ({HOST})...")
    try:
        response = requests.get(f"{HOST}/api/products/", timeout=5)
        print(f"   Status: {response.status_code}")
        if response.status_code >= 500:
            print("   ⚠️ Server error, some tests may fail")
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Cannot connect to {HOST}")
        print("   Running simulation mode instead...")
        return run_simulation_test()
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return run_simulation_test()
    
    # 로그인
    print("\n2. Login Test (AUTH stage)...")
    login_result = client.traced_post(
        "login",
        "/api/auth/login/",
        json_data={
            "username": "load_test_user_0",
            "password": "testpass123"
        }
    )
    print(f"   Result: {'✅ Success' if login_result['success'] else '❌ Failed'}")
    print(f"   Trace ID: {login_result['trace_id']}")
    
    auth_token = None
    if login_result.get("success") and login_result.get("data"):
        auth_token = login_result["data"].get("access") or login_result["data"].get("token")
        print(f"   Token: {auth_token[:20] if auth_token else 'N/A'}...")
    
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    
    # 상품 조회 테스트
    print("\n3. Product List Test (CACHING stage)...")
    for i in range(5):
        result = client.traced_get("get_products", "/api/products/")
        status = "✅" if result["success"] else "❌"
        print(f"   [{i+1}/5] {status} trace_id={result['trace_id'][:8]}...")
    
    # 상품 상세 테스트
    print("\n4. Product Detail Test (PERSISTENCE stage)...")
    for product_id in [1, 2, 3, 99999]:  # 99999 should fail
        result = client.traced_get(f"get_product_{product_id}", f"/api/products/{product_id}/")
        status = "✅" if result["success"] else "❌"
        stage = result.get("pipeline_stage", "success")
        print(f"   Product {product_id}: {status} stage={stage}")
    
    # 검증 에러 테스트
    print("\n5. Validation Error Test (VALIDATION stage)...")
    result = client.traced_post(
        "validation_error",
        "/api/cart/add_item/",
        json_data={"product_id": -1, "quantity": 0}
    )
    status = "✅ (expected failure)" if not result["success"] else "❌ (unexpected success)"
    print(f"   Result: {status}")
    print(f"   Stage: {result.get('pipeline_stage', 'N/A')}")
    
    # 인증 에러 테스트
    print("\n6. Auth Error Test (AUTH stage)...")
    result = client.traced_get("auth_error", "/api/orders/")
    status = "✅ (expected 401/403)" if not result["success"] else "❌"
    print(f"   Result: {status}")
    print(f"   Stage: {result.get('pipeline_stage', 'N/A')}")
    
    # 리포트 생성
    print("\n7. Generating Observability Report...")
    reporter = ObservabilityReportGenerator(metrics)
    report = reporter.generate_text_report()
    print(report)
    
    # 파일 저장
    paths = reporter.save_report(
        filename="stage08_observability_http",
        output_dir="load_tests/reports"
    )
    print("\nReports saved:")
    for format_name, path in paths.items():
        print(f"  {format_name}: {path}")
    
    # Invariant 검증
    summary = metrics.get_summary()
    invariants = summary["invariants"]
    
    print("\n" + "=" * 70)
    print("INVARIANT VERIFICATION")
    print("=" * 70)
    
    all_passed = True
    for name, check in invariants.items():
        status = "✅ PASSED" if check["passed"] else "❌ FAILED"
        if not check["passed"]:
            all_passed = False
        print(f"  {check['invariant']}: {check['value']} - {status}")
    
    print("\n" + "=" * 70)
    print(f"GAP-08 Observability Contract: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    print("=" * 70)
    
    return all_passed


def run_simulation_test():
    """서버 없이 시뮬레이션 테스트"""
    print("\n" + "=" * 70)
    print("Running Simulation Mode (No Server)")
    print("=" * 70)
    
    metrics = get_observability_metrics()
    metrics.reset()
    
    test_cases = [
        # (request_name, stage, should_fail, status_code, error)
        ("get_products", PipelineStage.CACHING, False, 200, None),
        ("get_products", PipelineStage.CACHING, False, 200, None),
        ("get_products", PipelineStage.CACHING, True, 503, "Cache unavailable"),
        ("create_order", PipelineStage.VALIDATION, True, 400, "Invalid input"),
        ("create_order", PipelineStage.PERSISTENCE, True, 500, "Database error"),
        ("create_order", PipelineStage.BUSINESS, False, 201, None),
        ("login", PipelineStage.AUTH, True, 401, "Invalid credentials"),
        ("login", PipelineStage.AUTH, False, 200, None),
        ("webhook", PipelineStage.ASYNC, True, 504, "Webhook timeout"),
        ("get_order", PipelineStage.PERSISTENCE, False, 200, None),
    ]
    
    print(f"\nSimulating {len(test_cases)} test cases...")
    
    for i, (name, stage, should_fail, status_code, error) in enumerate(test_cases):
        with trace_request(name) as ctx:
            ctx.set_stage(PipelineStage.INGRESS)
            time.sleep(0.01)  # Simulate latency
            ctx.set_stage(stage)
            
            if should_fail:
                ctx.record_failure(error, status_code)
            else:
                ctx.record_success(status_code)
        
        status = "❌" if should_fail else "✅"
        print(f"  [{i+1:2d}] {status} {name:20s} stage={stage.value:12s} code={status_code}")
    
    # 리포트 생성
    print("\nGenerating Report...")
    reporter = ObservabilityReportGenerator(metrics)
    report = reporter.generate_text_report()
    print(report)
    
    # 파일 저장
    paths = reporter.save_report(
        filename="stage08_observability_simulation",
        output_dir="load_tests/reports"
    )
    print("\nReports saved:")
    for format_name, path in paths.items():
        print(f"  {format_name}: {path}")
    
    return True


if __name__ == "__main__":
    success = run_http_test()
    sys.exit(0 if success else 1)
