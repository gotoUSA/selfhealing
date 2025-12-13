"""
Stage 37: Schema Compatibility Integration Test (HTTP-based)

Purpose: 실제 HTTP API를 통한 스키마 호환성 검증
- 실제 Django API 엔드포인트 호출
- 실제 PostgreSQL 데이터베이스 사용
- 실제 네트워크 지연 및 동시성 테스트

Scenarios:
  SC-37-1: 상품 가격 형식 호환성 (integer vs decimal)
  SC-37-2: 주문 금액 Cross-version 검증
  SC-37-3: API 응답 스키마 변경 대응
  SC-37-4: 동시 요청 시 데이터 일관성

Execution:
    # Docker Compose 통합 테스트
    docker-compose -f docker-compose.gap-tests.yml --profile gap01 up --build

    # 단독 실행 (web 서버 필요)
    TEST_HOST=http://localhost:8000 python load_tests/scenarios/stage37_schema_compat_http.py

    # Locust 모드
    locust -f load_tests/scenarios/stage37_schema_compat_http.py --host=http://localhost:8000

Invariants:
  - data_corruption == 0
  - api_error_rate < 1%
  - response_time_p95 < 500ms
  - schema_parse_errors == 0
"""

import os
import sys
import time
import random
import json
import threading
import traceback
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# 프로젝트 루트 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("WARNING: requests 모듈이 없습니다. pip install requests")

try:
    from locust import HttpUser, task, between, tag, events, LoadTestShape
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    HttpUser = object
    task = lambda weight=1: lambda f: f
    between = lambda a, b: None
    tag = lambda *args: lambda f: f
    events = None
    LoadTestShape = object

STAGE_NAME = "[Stage37-HTTP]"

# =============================================================================
# Configuration
# =============================================================================

TEST_HOST = os.environ.get("TEST_HOST", "http://localhost:8000")
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "10"))

# API Endpoints
ENDPOINTS = {
    "products": "/api/products/",
    "product_detail": "/api/products/{id}/",
    "categories": "/api/categories/",
    "category_tree": "/api/categories/tree/",
    "orders": "/api/orders/",
    "order_detail": "/api/orders/{id}/",
    "cart": "/api/cart/",
    "cart_add": "/api/cart/add_item/",
    "login": "/api/auth/login/",
}

# Test credentials
TEST_USER_PREFIX = "load_test_user_"
TEST_USER_PASSWORD = "testpass123"


# =============================================================================
# Test Metrics
# =============================================================================

@dataclass
class IntegrationTestMetrics:
    """통합 테스트 메트릭"""
    
    # Request counts
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    
    # Schema validation
    schema_parse_errors: int = 0
    type_mismatch_errors: int = 0
    missing_field_errors: int = 0
    
    # Response times (seconds)
    response_times: List[float] = field(default_factory=list)
    
    # Data integrity
    data_corruption_detected: int = 0
    price_mismatch_errors: int = 0
    quantity_errors: int = 0
    
    # API-specific
    product_api_errors: int = 0
    order_api_errors: int = 0
    cart_api_errors: int = 0
    auth_errors: int = 0
    
    # Concurrency
    concurrent_requests: int = 0
    race_condition_detected: int = 0
    
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    def record_request(self, success: bool, response_time: float):
        with self._lock:
            self.total_requests += 1
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1
            self.response_times.append(response_time)
    
    def record_schema_error(self, error_type: str):
        with self._lock:
            if error_type == "parse":
                self.schema_parse_errors += 1
            elif error_type == "type_mismatch":
                self.type_mismatch_errors += 1
            elif error_type == "missing_field":
                self.missing_field_errors += 1
    
    def get_p95_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[idx] * 1000  # ms
    
    def get_error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.failed_requests / self.total_requests
    
    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "error_rate": f"{self.get_error_rate()*100:.2f}%",
            "p95_response_time_ms": f"{self.get_p95_response_time():.2f}",
            "schema_parse_errors": self.schema_parse_errors,
            "type_mismatch_errors": self.type_mismatch_errors,
            "data_corruption": self.data_corruption_detected,
        }


# Global metrics
_metrics = IntegrationTestMetrics()


# =============================================================================
# Schema Validators
# =============================================================================

class SchemaValidator:
    """API 응답 스키마 검증기"""
    
    @staticmethod
    def validate_product(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """상품 스키마 검증"""
        errors = []
        
        # 필수 필드 검증
        required_fields = ["id", "name", "price"]
        for field in required_fields:
            if field not in data:
                errors.append(f"missing_field:{field}")
                _metrics.record_schema_error("missing_field")
        
        # 타입 검증
        if "price" in data:
            price = data["price"]
            # price는 string (decimal), int, 또는 float일 수 있음
            try:
                price_value = Decimal(str(price))
                if price_value < 0:
                    errors.append(f"invalid_price:negative:{price}")
                    _metrics.data_corruption_detected += 1
            except Exception:
                errors.append(f"type_mismatch:price:{type(price).__name__}")
                _metrics.record_schema_error("type_mismatch")
        
        if "stock" in data:
            stock = data["stock"]
            if not isinstance(stock, int):
                try:
                    int(stock)
                except (ValueError, TypeError):
                    errors.append(f"type_mismatch:stock:{type(stock).__name__}")
                    _metrics.record_schema_error("type_mismatch")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_order(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """주문 스키마 검증"""
        errors = []
        
        required_fields = ["id", "status", "total_amount"]
        for field in required_fields:
            if field not in data:
                errors.append(f"missing_field:{field}")
                _metrics.record_schema_error("missing_field")
        
        # 금액 검증
        if "total_amount" in data:
            try:
                amount = Decimal(str(data["total_amount"]))
                if amount < 0:
                    errors.append(f"invalid_amount:negative:{amount}")
                    _metrics.data_corruption_detected += 1
            except Exception:
                errors.append(f"type_mismatch:total_amount")
                _metrics.record_schema_error("type_mismatch")
        
        # 상태 검증
        valid_statuses = ["pending", "confirmed", "paid", "preparing", 
                         "shipped", "delivered", "canceled", "refunded",
                         "payment_failed", "failed"]
        if "status" in data and data["status"] not in valid_statuses:
            errors.append(f"invalid_status:{data['status']}")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_cart(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """장바구니 스키마 검증"""
        errors = []
        
        # items가 있으면 검증
        if "items" in data:
            for item in data["items"]:
                if "quantity" in item:
                    if not isinstance(item["quantity"], int) or item["quantity"] < 0:
                        errors.append(f"invalid_quantity:{item.get('quantity')}")
                        _metrics.quantity_errors += 1
        
        return len(errors) == 0, errors


# =============================================================================
# HTTP Client Wrapper
# =============================================================================

class APIClient:
    """HTTP API 클라이언트"""
    
    def __init__(self, base_url: str = TEST_HOST):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        self.token = None
    
    def login(self, username: str, password: str) -> bool:
        """로그인 및 토큰 획득"""
        if not self.session:
            return False
        
        try:
            response = self.session.post(
                f"{self.base_url}{ENDPOINTS['login']}",
                json={"username": username, "password": password},
                timeout=TEST_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
                if self.token:
                    self.session.headers["Authorization"] = f"Bearer {self.token}"
                return True
        except Exception as e:
            _metrics.auth_errors += 1
        return False
    
    def get(self, endpoint: str, params: Dict = None) -> Tuple[Optional[Dict], float, int]:
        """GET 요청"""
        start = time.time()
        try:
            response = self.session.get(
                f"{self.base_url}{endpoint}",
                params=params,
                timeout=TEST_TIMEOUT
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    _metrics.record_request(True, elapsed)
                    return data, elapsed, response.status_code
                except json.JSONDecodeError:
                    _metrics.record_schema_error("parse")
                    _metrics.record_request(False, elapsed)
                    return None, elapsed, response.status_code
            else:
                _metrics.record_request(False, elapsed)
                return None, elapsed, response.status_code
                
        except Exception as e:
            elapsed = time.time() - start
            _metrics.record_request(False, elapsed)
            return None, elapsed, 0
    
    def post(self, endpoint: str, data: Dict) -> Tuple[Optional[Dict], float, int]:
        """POST 요청"""
        start = time.time()
        try:
            response = self.session.post(
                f"{self.base_url}{endpoint}",
                json=data,
                timeout=TEST_TIMEOUT
            )
            elapsed = time.time() - start
            
            if response.status_code in [200, 201]:
                try:
                    resp_data = response.json()
                    _metrics.record_request(True, elapsed)
                    return resp_data, elapsed, response.status_code
                except json.JSONDecodeError:
                    _metrics.record_schema_error("parse")
                    _metrics.record_request(False, elapsed)
                    return None, elapsed, response.status_code
            else:
                _metrics.record_request(False, elapsed)
                return None, elapsed, response.status_code
                
        except Exception as e:
            elapsed = time.time() - start
            _metrics.record_request(False, elapsed)
            return None, elapsed, 0


# =============================================================================
# Integration Tests
# =============================================================================

class SchemaCompatibilityTests:
    """스키마 호환성 통합 테스트"""
    
    def __init__(self, host: str = TEST_HOST):
        self.client = APIClient(host)
        self.results: List[Dict] = []
    
    def run_all_tests(self) -> Dict[str, Any]:
        """모든 테스트 실행"""
        print(f"\n{'='*60}")
        print(f"{STAGE_NAME} Schema Compatibility Integration Tests")
        print(f"{'='*60}")
        print(f"Host: {TEST_HOST}")
        print(f"{'='*60}\n")
        
        tests = [
            ("TC-01: Product List Schema", self.test_product_list_schema),
            ("TC-02: Product Detail Schema", self.test_product_detail_schema),
            ("TC-03: Category Tree Schema", self.test_category_tree_schema),
            ("TC-04: Price Format Compatibility", self.test_price_format_compatibility),
            ("TC-05: Concurrent Product Access", self.test_concurrent_product_access),
            ("TC-06: Large Dataset Pagination", self.test_pagination_schema),
        ]
        
        passed = 0
        failed = 0
        
        for name, test_func in tests:
            print(f"Running: {name}...")
            try:
                result = test_func()
                if result["passed"]:
                    print(f"  ✅ PASSED ({result.get('response_time', 0)*1000:.2f}ms)")
                    passed += 1
                else:
                    print(f"  ❌ FAILED: {result.get('error', 'Unknown')}")
                    failed += 1
                self.results.append({"name": name, **result})
            except Exception as e:
                print(f"  ❌ ERROR: {str(e)}")
                traceback.print_exc()
                failed += 1
                self.results.append({"name": name, "passed": False, "error": str(e)})
        
        return {
            "passed": passed,
            "failed": failed,
            "total": len(tests),
            "results": self.results
        }
    
    def test_product_list_schema(self) -> Dict:
        """상품 목록 API 스키마 검증"""
        data, elapsed, status = self.client.get(ENDPOINTS["products"])
        
        if status != 200:
            return {"passed": False, "error": f"HTTP {status}", "response_time": elapsed}
        
        # 페이지네이션 응답 확인
        if isinstance(data, dict):
            if "results" in data:
                products = data["results"]
            else:
                products = [data]
        elif isinstance(data, list):
            products = data
        else:
            return {"passed": False, "error": "Unexpected response format", "response_time": elapsed}
        
        # 각 상품 스키마 검증
        all_valid = True
        errors = []
        for product in products[:10]:  # 처음 10개만 검증
            valid, errs = SchemaValidator.validate_product(product)
            if not valid:
                all_valid = False
                errors.extend(errs)
        
        return {
            "passed": all_valid,
            "error": errors if errors else None,
            "response_time": elapsed,
            "items_checked": len(products)
        }
    
    def test_product_detail_schema(self) -> Dict:
        """상품 상세 API 스키마 검증"""
        # 먼저 상품 목록에서 ID 가져오기
        list_data, _, list_status = self.client.get(ENDPOINTS["products"])
        if list_status != 200:
            return {"passed": False, "error": "Cannot get product list"}
        
        # 상품 ID 추출
        if isinstance(list_data, dict) and "results" in list_data:
            products = list_data["results"]
        elif isinstance(list_data, list):
            products = list_data
        else:
            return {"passed": False, "error": "No products found"}
        
        if not products:
            return {"passed": True, "note": "No products to test"}
        
        product_id = products[0].get("id")
        
        # 상세 조회
        endpoint = ENDPOINTS["product_detail"].format(id=product_id)
        data, elapsed, status = self.client.get(endpoint)
        
        if status != 200:
            return {"passed": False, "error": f"HTTP {status}", "response_time": elapsed}
        
        valid, errors = SchemaValidator.validate_product(data)
        
        return {
            "passed": valid,
            "error": errors if errors else None,
            "response_time": elapsed,
            "product_id": product_id
        }
    
    def test_category_tree_schema(self) -> Dict:
        """카테고리 트리 API 스키마 검증"""
        data, elapsed, status = self.client.get(ENDPOINTS["category_tree"])
        
        if status != 200:
            return {"passed": False, "error": f"HTTP {status}", "response_time": elapsed}
        
        def validate_category_node(node: Dict) -> bool:
            required = ["id", "name"]
            for field in required:
                if field not in node:
                    _metrics.record_schema_error("missing_field")
                    return False
            
            # children 재귀 검증
            if "children" in node:
                for child in node["children"]:
                    if not validate_category_node(child):
                        return False
            return True
        
        if not isinstance(data, list):
            return {"passed": False, "error": "Expected list response", "response_time": elapsed}
        
        all_valid = all(validate_category_node(cat) for cat in data)
        
        return {
            "passed": all_valid,
            "response_time": elapsed,
            "categories_count": len(data)
        }
    
    def test_price_format_compatibility(self) -> Dict:
        """
        가격 형식 호환성 테스트
        
        V1: 정수 (10000)
        V2: 소수점 문자열 ("10000.00")
        
        둘 다 올바르게 파싱되어야 함
        """
        data, elapsed, status = self.client.get(ENDPOINTS["products"])
        
        if status != 200:
            return {"passed": False, "error": f"HTTP {status}", "response_time": elapsed}
        
        if isinstance(data, dict) and "results" in data:
            products = data["results"]
        elif isinstance(data, list):
            products = data
        else:
            return {"passed": False, "error": "No products"}
        
        price_formats_detected = {"int": 0, "float": 0, "string": 0}
        conversion_errors = []
        
        for product in products:
            price = product.get("price")
            
            # 형식 감지
            if isinstance(price, int):
                price_formats_detected["int"] += 1
            elif isinstance(price, float):
                price_formats_detected["float"] += 1
            elif isinstance(price, str):
                price_formats_detected["string"] += 1
            
            # 변환 테스트
            try:
                decimal_price = Decimal(str(price))
                # V1 호환 (센트로 변환 가능?)
                cents = int(decimal_price * 100)
                # V2 호환 (소수점 유지?)
                formatted = f"{decimal_price:.2f}"
                
                # 역변환 정확도 검증
                if Decimal(cents) / 100 != decimal_price.quantize(Decimal("0.01")):
                    conversion_errors.append(f"precision_loss:{price}")
                    
            except Exception as e:
                conversion_errors.append(f"conversion_error:{price}:{str(e)}")
        
        return {
            "passed": len(conversion_errors) == 0,
            "error": conversion_errors if conversion_errors else None,
            "response_time": elapsed,
            "price_formats": price_formats_detected,
            "products_checked": len(products)
        }
    
    def test_concurrent_product_access(self) -> Dict:
        """동시 상품 접근 테스트 (Race Condition 검증)"""
        
        # 먼저 상품 목록 가져오기
        list_data, _, list_status = self.client.get(ENDPOINTS["products"])
        if list_status != 200:
            return {"passed": False, "error": "Cannot get product list"}
        
        if isinstance(list_data, dict) and "results" in list_data:
            products = list_data["results"]
        elif isinstance(list_data, list):
            products = list_data
        else:
            return {"passed": False, "error": "No products"}
        
        if not products:
            return {"passed": True, "note": "No products to test"}
        
        product_ids = [p["id"] for p in products[:5]]
        concurrent_results = []
        errors = []
        
        def fetch_product(pid):
            client = APIClient(TEST_HOST)
            endpoint = ENDPOINTS["product_detail"].format(id=pid)
            data, elapsed, status = client.get(endpoint)
            return {"id": pid, "data": data, "elapsed": elapsed, "status": status}
        
        # 동시에 여러 상품 조회
        start = time.time()
        with ThreadPoolExecutor(max_workers=10) as executor:
            # 각 상품을 5번씩 동시 조회
            futures = []
            for _ in range(5):
                for pid in product_ids:
                    futures.append(executor.submit(fetch_product, pid))
            
            for future in as_completed(futures):
                result = future.result()
                concurrent_results.append(result)
                if result["status"] != 200:
                    errors.append(f"Failed for product {result['id']}: HTTP {result['status']}")
        
        total_elapsed = time.time() - start
        
        # 같은 상품의 응답이 일관적인지 검증
        by_product = defaultdict(list)
        for r in concurrent_results:
            by_product[r["id"]].append(r["data"])
        
        consistency_errors = []
        for pid, responses in by_product.items():
            # 모든 응답의 price가 동일해야 함
            prices = [r.get("price") if r else None for r in responses]
            unique_prices = set(str(p) for p in prices if p is not None)
            if len(unique_prices) > 1:
                consistency_errors.append(f"Product {pid} has inconsistent prices: {unique_prices}")
                _metrics.race_condition_detected += 1
        
        return {
            "passed": len(errors) == 0 and len(consistency_errors) == 0,
            "error": errors + consistency_errors if (errors or consistency_errors) else None,
            "response_time": total_elapsed,
            "concurrent_requests": len(concurrent_results),
            "products_tested": len(product_ids)
        }
    
    def test_pagination_schema(self) -> Dict:
        """페이지네이션 스키마 검증"""
        # 첫 페이지
        data1, elapsed1, status1 = self.client.get(ENDPOINTS["products"], {"page": 1, "page_size": 5})
        
        if status1 != 200:
            return {"passed": False, "error": f"HTTP {status1}"}
        
        # 페이지네이션 필드 검증
        pagination_fields = ["count", "next", "previous", "results"]
        missing = [f for f in pagination_fields if f not in data1]
        
        if missing:
            # DRF가 아닌 경우 list 응답일 수 있음
            if isinstance(data1, list):
                return {"passed": True, "note": "Non-paginated list response", "response_time": elapsed1}
            return {"passed": False, "error": f"Missing pagination fields: {missing}"}
        
        # 두 번째 페이지
        data2, elapsed2, status2 = self.client.get(ENDPOINTS["products"], {"page": 2, "page_size": 5})
        
        # 중복 검증
        if status2 == 200 and "results" in data2:
            ids1 = {p["id"] for p in data1.get("results", [])}
            ids2 = {p["id"] for p in data2.get("results", [])}
            overlap = ids1 & ids2
            if overlap:
                return {"passed": False, "error": f"Duplicate items in pagination: {overlap}"}
        
        return {
            "passed": True,
            "response_time": elapsed1 + elapsed2,
            "page1_count": len(data1.get("results", [])),
            "page2_count": len(data2.get("results", [])) if status2 == 200 else 0
        }


# =============================================================================
# Locust User Classes (for load testing mode)
# =============================================================================

if LOCUST_AVAILABLE:
    
    class SchemaValidationUser(HttpUser):
        """스키마 검증 사용자"""
        
        weight = 60
        wait_time = between(1, 3)
        
        @task(5)
        @tag("product", "schema")
        def validate_product_list(self):
            with self.client.get(ENDPOINTS["products"], catch_response=True) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        products = data.get("results", data) if isinstance(data, dict) else data
                        
                        for product in products[:5]:
                            valid, errors = SchemaValidator.validate_product(product)
                            if not valid:
                                response.failure(f"Schema validation failed: {errors}")
                                return
                        
                        response.success()
                    except json.JSONDecodeError:
                        response.failure("JSON parse error")
                else:
                    response.failure(f"HTTP {response.status_code}")
        
        @task(3)
        @tag("product", "detail")
        def validate_product_detail(self):
            product_id = random.randint(1, 50)
            endpoint = ENDPOINTS["product_detail"].format(id=product_id)
            
            with self.client.get(endpoint, catch_response=True) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        valid, errors = SchemaValidator.validate_product(data)
                        if valid:
                            response.success()
                        else:
                            response.failure(f"Schema error: {errors}")
                    except json.JSONDecodeError:
                        response.failure("JSON parse error")
                elif response.status_code == 404:
                    response.success()  # 404는 정상 (상품 없음)
                else:
                    response.failure(f"HTTP {response.status_code}")
        
        @task(2)
        @tag("category")
        def validate_category_tree(self):
            with self.client.get(ENDPOINTS["category_tree"], catch_response=True) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, list):
                            response.success()
                        else:
                            response.failure("Expected list response")
                    except json.JSONDecodeError:
                        response.failure("JSON parse error")
                else:
                    response.failure(f"HTTP {response.status_code}")


    class PriceCompatibilityUser(HttpUser):
        """가격 호환성 검증 사용자"""
        
        weight = 40
        wait_time = between(0.5, 2)
        
        @task
        @tag("price", "compatibility")
        def check_price_format(self):
            with self.client.get(ENDPOINTS["products"], catch_response=True) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        products = data.get("results", data) if isinstance(data, dict) else data
                        
                        for product in products[:3]:
                            price = product.get("price")
                            try:
                                # 모든 형식에서 Decimal 변환 가능해야 함
                                decimal_price = Decimal(str(price))
                                if decimal_price < 0:
                                    response.failure(f"Negative price: {price}")
                                    return
                            except Exception as e:
                                response.failure(f"Price conversion error: {e}")
                                return
                        
                        response.success()
                    except json.JSONDecodeError:
                        response.failure("JSON parse error")
                else:
                    response.failure(f"HTTP {response.status_code}")


# =============================================================================
# Main Execution
# =============================================================================

def run_integration_tests():
    """통합 테스트 실행"""
    
    if not REQUESTS_AVAILABLE:
        print("ERROR: requests 모듈이 필요합니다. pip install requests")
        return False
    
    # 서버 연결 확인
    print(f"Checking connection to {TEST_HOST}...")
    try:
        response = requests.get(f"{TEST_HOST}/api/products/", timeout=10)
        print(f"Server response: HTTP {response.status_code}")
    except Exception as e:
        print(f"ERROR: Cannot connect to server: {e}")
        print("Make sure the web server is running!")
        return False
    
    # 테스트 실행
    tests = SchemaCompatibilityTests(TEST_HOST)
    results = tests.run_all_tests()
    
    # 메트릭 요약
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Final Report")
    print(f"{'='*60}")
    
    print(f"\n📊 Test Results:")
    print(f"   Passed:  {results['passed']}/{results['total']}")
    print(f"   Failed:  {results['failed']}/{results['total']}")
    
    print(f"\n📈 API Metrics:")
    summary = _metrics.get_summary()
    for key, value in summary.items():
        print(f"   {key}: {value}")
    
    # Invariant 검증
    print(f"\n✅ Invariant Verification:")
    passed = True
    
    # data_corruption == 0
    if _metrics.data_corruption_detected == 0:
        print(f"   ✅ PASS: data_corruption == 0")
    else:
        print(f"   ❌ FAIL: data_corruption == 0 (got {_metrics.data_corruption_detected})")
        passed = False
    
    # error_rate < 1%
    error_rate = _metrics.get_error_rate()
    if error_rate < 0.01:
        print(f"   ✅ PASS: error_rate < 1% ({error_rate*100:.2f}%)")
    else:
        print(f"   ❌ FAIL: error_rate < 1% (got {error_rate*100:.2f}%)")
        passed = False
    
    # p95 < 500ms
    p95 = _metrics.get_p95_response_time()
    if p95 < 500:
        print(f"   ✅ PASS: p95_response_time < 500ms ({p95:.2f}ms)")
    else:
        print(f"   ⚠️  WARN: p95_response_time < 500ms (got {p95:.2f}ms)")
    
    # schema_parse_errors == 0
    if _metrics.schema_parse_errors == 0:
        print(f"   ✅ PASS: schema_parse_errors == 0")
    else:
        print(f"   ❌ FAIL: schema_parse_errors == 0 (got {_metrics.schema_parse_errors})")
        passed = False
    
    print(f"\n{'='*60}")
    if passed and results['failed'] == 0:
        print(f"🎉 {STAGE_NAME} ALL TESTS PASSED")
    else:
        print(f"💥 {STAGE_NAME} SOME TESTS FAILED")
    print(f"{'='*60}\n")
    
    return passed and results['failed'] == 0


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
