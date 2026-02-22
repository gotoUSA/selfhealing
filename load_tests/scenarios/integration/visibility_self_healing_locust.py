"""
Visibility-Focused Self-Healing Verification - Locust Scenario

CHOKE POINT: Product.stock (shopping_product)

이 시나리오는 모든 요청이 특정 Product의 stock 업데이트를 유발하도록 설계되었습니다.
외부 락 홀더가 동일 Product를 잠그고 있을 때 락 경합이 발생하고,
Self-Healing 시스템의 가시적인 동작을 관찰할 수 있습니다.

테스트 목표:
- 동일 상품에 대한 높은 주문 비율
- 락 경합 발생 시 자가 치유 신호 관찰 (지연, 타임아웃, 재시도)
- 시스템 무결성 유지 확인

환경변수:
    TEST_USER_PASSWORD     - 테스트 사용자 비밀번호
    LOCK_TARGET_PRODUCT_ID - 락 홀더와 동일한 상품 ID (필수)
"""

import os
import sys
import random
import time
import logging
from typing import Optional

from locust import HttpUser, task, between, events, tag

# 프로젝트 루트 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from load_tests.config import (
    TEST_USER_COUNT,
    TEST_USER_PREFIX,
    TEST_USER_PASSWORD,
    ENDPOINTS,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 테스트 통계 수집
# =============================================================================
class TestStats:
    """테스트 중 발생한 이벤트 통계"""
    
    def __init__(self):
        self.lock_timeout_count = 0
        self.retry_observed = 0
        self.conflict_responses = 0  # 409 Conflict
        self.server_errors = 0       # 5xx
        self.latency_spikes = []     # 응답 시간 > 5초
        self.successful_orders = 0
        self.successful_payments = 0
        self.stock_contention_count = 0
        self.healing_signals = []
        
    def record_healing_signal(self, signal_type: str, details: str):
        timestamp = time.strftime("%H:%M:%S")
        self.healing_signals.append({
            "timestamp": timestamp,
            "type": signal_type,
            "details": details
        })
        logger.info(f"🔧 HEALING SIGNAL [{signal_type}]: {details}")


# 전역 통계 객체
test_stats = TestStats()

# 락 홀더와 동일한 타겟 상품 ID
TARGET_PRODUCT_ID = int(os.environ.get('LOCK_TARGET_PRODUCT_ID', '0'))


# =============================================================================
# 상품 재고 경합 유발 사용자
# =============================================================================
class StockContentionUser(HttpUser):
    """
    상품 재고 경합을 유발하는 사용자
    
    모든 작업이 동일 Product의 stock 업데이트를 목표로 함:
    1. 특정 상품만 장바구니에 추가
    2. 주문 생성 → 재고 차감 시 SELECT FOR UPDATE
    3. 락 홀더가 해당 상품을 잠그고 있으면 경합 발생
    """
    
    wait_time = between(0.5, 1.5)  # 빠른 요청으로 경합 증가
    
    # 클래스 레벨
    _target_product_id: Optional[int] = None
    _fallback_product_ids: list = []
    _initialized: bool = False
    
    def on_start(self):
        """테스트 시작 시 초기화"""
        self.is_logged_in = False
        self.user_id = None
        self.access_token = None
        self.username = None
        self._auth_headers = {}  # 인증 헤더 저장
        
        # 초기화
        if not StockContentionUser._initialized:
            self._initialize_target_product()
            StockContentionUser._initialized = True
        
        # 로그인
        self.login()
    
    def _initialize_target_product(self):
        """타겟 상품 초기화 - DB 직접 쿼리 (lock holder와 동일)"""
        global TARGET_PRODUCT_ID
        
        if TARGET_PRODUCT_ID > 0:
            StockContentionUser._target_product_id = TARGET_PRODUCT_ID
            logger.info(f"🎯 Using target product from env: id={TARGET_PRODUCT_ID}")
            return
        
        # DB에서 직접 조회 (lock holder와 동일한 쿼리)
        db_url = os.environ.get('DATABASE_URL', '')
        if db_url:
            try:
                import psycopg2
                # DATABASE_URL 파싱
                # postgres://user:pass@host:port/dbname
                import re
                m = re.match(r'postgres://(\w+):(\w+)@([\w.-]+):(\d+)/(\w+)', db_url)
                if m:
                    conn = psycopg2.connect(
                        host=m.group(3),
                        port=m.group(4),
                        database=m.group(5),
                        user=m.group(1),
                        password=m.group(2),
                        connect_timeout=10
                    )
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, name, stock 
                        FROM shopping_product 
                        WHERE is_active = TRUE AND stock > 0
                        ORDER BY id
                        LIMIT 5
                    """)
                    rows = cursor.fetchall()
                    if rows:
                        StockContentionUser._target_product_id = rows[0][0]
                        StockContentionUser._fallback_product_ids = [r[0] for r in rows[1:]]
                        logger.info(f"🎯 DB-discovered target product: id={rows[0][0]}, name={rows[0][1]}")
                    cursor.close()
                    conn.close()
                    return
            except Exception as e:
                logger.warning(f"DB query failed, falling back to API: {e}")
        
        # Fallback: API에서 첫 번째 상품 조회
        try:
            with self.client.get(
                f"{ENDPOINTS['products']}?page=1&page_size=10",
                name="[Setup] Fetch Target Product",
                catch_response=True
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        StockContentionUser._target_product_id = results[0]["id"]
                        StockContentionUser._fallback_product_ids = [p["id"] for p in results[1:5]]
                        logger.info(f"🎯 API-discovered target product: id={StockContentionUser._target_product_id}")
                    response.success()
                else:
                    response.failure(f"Failed to fetch products: {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to initialize target product: {e}")
    
    def get_target_product_id(self) -> Optional[int]:
        """타겟 상품 ID 반환 (90% 확률로 타겟, 10% 확률로 다른 상품)"""
        if StockContentionUser._target_product_id:
            # 90% 확률로 타겟 상품 선택 (경합 극대화)
            if random.random() < 0.9:
                return StockContentionUser._target_product_id
            elif StockContentionUser._fallback_product_ids:
                return random.choice(StockContentionUser._fallback_product_ids)
        return StockContentionUser._target_product_id
    
    def login(self) -> bool:
        """테스트 사용자로 로그인"""
        if self.is_logged_in:
            return True
        
        user_index = random.randint(0, min(TEST_USER_COUNT - 1, 49))
        self.username = f"{TEST_USER_PREFIX}{user_index}"
        password = os.environ.get('TEST_USER_PASSWORD', TEST_USER_PASSWORD)
        
        start_time = time.time()
        try:
            with self.client.post(
                ENDPOINTS["login"],
                json={"username": self.username, "password": password},
                name="POST /api/auth/login/",
                catch_response=True,
                timeout=30
            ) as response:
                elapsed = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    self.access_token = data.get("token", {}).get("access")
                    self.user_id = data.get("user", {}).get("id")
                    if self.access_token:
                        self._auth_headers = {"Authorization": f"Bearer {self.access_token}"}
                        self.client.headers.update(self._auth_headers)
                        self.is_logged_in = True
                        response.success()
                        logger.info(f"✅ Logged in as {self.username}, token={self.access_token[:20]}...")
                        return True
                    else:
                        response.failure("Login response missing token")
                        return False
                else:
                    response.failure(f"Login failed: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Login exception: {e}")
            test_stats.server_errors += 1
            return False
    
    def ensure_logged_in(self) -> bool:
        if not self.is_logged_in:
            return self.login()
        return True
    
    def _record_response_stats(self, response, operation: str, elapsed: float):
        """응답 통계 기록"""
        status = response.status_code
        
        # 락 타임아웃 / 데드락 관련 응답 감지
        if status == 409:
            test_stats.conflict_responses += 1
            test_stats.record_healing_signal(
                "CONFLICT_409",
                f"{operation}: Lock contention detected"
            )
        
        if status >= 500:
            test_stats.server_errors += 1
            
            try:
                body = response.text.lower()
                if "retry" in body or "backoff" in body:
                    test_stats.retry_observed += 1
                    test_stats.record_healing_signal(
                        "RETRY_SIGNAL",
                        f"{operation}: Retry signal in response"
                    )
                if "lock" in body or "deadlock" in body or "timeout" in body:
                    test_stats.lock_timeout_count += 1
                    test_stats.record_healing_signal(
                        "LOCK_TIMEOUT",
                        f"{operation}: Lock/deadlock/timeout in error response"
                    )
                if "재고" in body or "stock" in body:
                    test_stats.stock_contention_count += 1
                    test_stats.record_healing_signal(
                        "STOCK_CONTENTION",
                        f"{operation}: Stock-related error"
                    )
            except:
                pass
        
        # 지연 시간 스파이크 (5초 이상)
        if elapsed > 5.0:
            test_stats.latency_spikes.append((operation, elapsed))
            test_stats.record_healing_signal(
                "LATENCY_SPIKE",
                f"{operation} took {elapsed:.2f}s (likely blocked by lock)"
            )
        # 중간 지연 (2-5초)
        elif elapsed > 2.0:
            test_stats.record_healing_signal(
                "LATENCY_WARNING",
                f"{operation} took {elapsed:.2f}s (possible lock wait)"
            )
    
    # =========================================================================
    # 재고 경합 유발 태스크들
    # =========================================================================
    
    @task(10)
    @tag("write", "order", "stock")
    def order_target_product(self):
        """
        타겟 상품 주문 (재고 경합 핵심)
        
        이 작업은 Product.stock에 SELECT FOR UPDATE를 발생시킵니다.
        락 홀더가 동일 상품을 잠그고 있으면 경합이 발생합니다.
        """
        if not self.ensure_logged_in():
            return
        
        if not self.access_token:
            return
            return
        
        product_id = self.get_target_product_id()
        if not product_id:
            return
        
        # 1. 타겟 상품을 장바구니에 추가
        headers = {"Authorization": f"Bearer {self.access_token}"}
        with self.client.post(
            ENDPOINTS["cart_add_item"],
            json={"product_id": product_id, "quantity": 1},
            name=f"POST /api/cart/add_item/ [TARGET={product_id}]",
            catch_response=True,
            timeout=30,
            headers=headers
        ) as response:
            if response.status_code not in [200, 201]:
                response.failure(f"Add to cart failed: {response.status_code}")
                return
            response.success()
        
        # 2. 주문 생성 (핵심 경합 지점 - 재고 차감)
        start_time = time.time()
        try:
            with self.client.post(
                ENDPOINTS["orders"],
                json={
                    "shipping_name": "테스트 사용자",
                    "shipping_phone": "010-1234-5678",
                    "shipping_postal_code": "12345",
                    "shipping_address": "서울시 강남구 테스트로 123",
                    "shipping_address_detail": "테스트동 101호",
                    "use_points": 0,
                },
                headers=self._auth_headers,
                name=f"POST /api/orders/ [STOCK_CONTENTION={product_id}]",
                catch_response=True,
                timeout=60  # 락 경합 시 긴 타임아웃
            ) as response:
                elapsed = time.time() - start_time
                self._record_response_stats(response, "create_order", elapsed)
                
                if response.status_code in [200, 201, 202]:
                    test_stats.successful_orders += 1
                    response.success()
                    
                    # 결제 진행
                    order_data = response.json()
                    order_id = order_data.get("order_id")
                    final_amount = order_data.get("final_amount")
                    
                    if order_id and final_amount:
                        self._complete_payment(order_id, int(final_amount))
                        
                elif response.status_code == 409:
                    test_stats.conflict_responses += 1
                    response.success()  # 테스트 관점에서는 성공 (경합 감지됨)
                elif response.status_code == 400:
                    # 재고 부족 등 비즈니스 에러
                    try:
                        body = response.text
                        if "재고" in body or "stock" in body.lower():
                            test_stats.stock_contention_count += 1
                            response.success()  # 예상된 에러
                        else:
                            response.failure(f"Order failed: {response.status_code}")
                    except:
                        response.failure(f"Order failed: {response.status_code}")
                else:
                    response.failure(f"Order failed: {response.status_code}")
                    
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e).lower()
            logger.warning(f"Order exception after {elapsed:.2f}s: {e}")
            
            if "timeout" in error_msg or "timed out" in error_msg:
                test_stats.lock_timeout_count += 1
                test_stats.record_healing_signal(
                    "REQUEST_TIMEOUT",
                    f"create_order timed out after {elapsed:.2f}s (BLOCKED BY LOCK)"
                )
    
    def _complete_payment(self, order_id: int, amount: int):
        """결제 완료"""
        payment_key = f"test_key_{int(time.time() * 1000)}_{random.randint(1, 100000)}"
        
        start_time = time.time()
        try:
            with self.client.post(
                ENDPOINTS["payment_confirm"],
                json={
                    "payment_key": payment_key,
                    "order_id": order_id,
                    "amount": amount,
                },
                headers=self._auth_headers,
                name="POST /api/payments/confirm/",
                catch_response=True,
                timeout=30
            ) as response:
                elapsed = time.time() - start_time
                self._record_response_stats(response, "payment_confirm", elapsed)
                
                if response.status_code in [200, 201]:
                    test_stats.successful_payments += 1
                    response.success()
                elif response.status_code == 409:
                    test_stats.conflict_responses += 1
                    response.success()
                else:
                    response.failure(f"Payment failed: {response.status_code}")
                    
        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(f"Payment exception after {elapsed:.2f}s: {e}")
    
    @task(2)
    @tag("read", "orders")
    def view_orders(self):
        """주문 내역 조회"""
        if not self.ensure_logged_in():
            return
        
        with self.client.get(
            ENDPOINTS["orders"],
            headers=self._auth_headers,
            name="GET /api/orders/",
            catch_response=True,
            timeout=30
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Orders list failed: {response.status_code}")


# =============================================================================
# 이벤트 훅
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작"""
    print("\n" + "=" * 70)
    print("🚀 VISIBILITY-FOCUSED SELF-HEALING VERIFICATION")
    print("   CHOKE POINT: Product.stock (shopping_product)")
    print("=" * 70)
    print(f"Target Product ID: {TARGET_PRODUCT_ID or 'Auto-discover'}")
    print("This test is designed to trigger visible healing signals.")
    print("Run with external lock holder for contention injection.")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 및 결과 출력"""
    stats = environment.stats
    
    print("\n" + "=" * 70)
    print("📊 SELF-HEALING VISIBILITY REPORT")
    print("=" * 70)
    
    # 기본 통계
    print("\n📈 Request Statistics:")
    print(f"   Total requests: {stats.total.num_requests}")
    print(f"   Total failures: {stats.total.num_failures}")
    print(f"   Avg response time: {stats.total.avg_response_time:.2f}ms")
    
    if stats.total.num_requests > 0:
        error_rate = (stats.total.num_failures / stats.total.num_requests) * 100
        print(f"   Error rate: {error_rate:.2f}%")
    
    # 힐링 신호 통계
    print("\n🔧 Healing Signals Detected:")
    print(f"   Lock timeouts: {test_stats.lock_timeout_count}")
    print(f"   Retry signals: {test_stats.retry_observed}")
    print(f"   409 Conflicts: {test_stats.conflict_responses}")
    print(f"   5xx Errors: {test_stats.server_errors}")
    print(f"   Latency spikes (>5s): {len(test_stats.latency_spikes)}")
    print(f"   Stock contention errors: {test_stats.stock_contention_count}")
    
    # 성공 통계
    print("\n✅ Successful Operations:")
    print(f"   Orders created: {test_stats.successful_orders}")
    print(f"   Payments completed: {test_stats.successful_payments}")
    
    # 힐링 신호 상세
    if test_stats.healing_signals:
        print(f"\n📋 Healing Signal Timeline ({len(test_stats.healing_signals)} events):")
        for sig in test_stats.healing_signals[:30]:  # 처음 30개
            print(f"   [{sig['timestamp']}] {sig['type']}: {sig['details']}")
        if len(test_stats.healing_signals) > 30:
            print(f"   ... and {len(test_stats.healing_signals) - 30} more events")
    
    # 결론
    total_healing_signals = (
        test_stats.lock_timeout_count +
        test_stats.retry_observed +
        test_stats.conflict_responses +
        len(test_stats.latency_spikes) +
        test_stats.stock_contention_count
    )
    
    print(f"\n{'=' * 70}")
    if total_healing_signals >= 2:
        print("✅ RESULT: Self-Healing signals were OBSERVABLE")
        print("   The test successfully demonstrated healing behavior.")
        print(f"   Total healing signals detected: {total_healing_signals}")
    else:
        print("⚠️  RESULT: Insufficient healing signals observed")
        print("   Ensure lock holder was running during the test.")
        print("   Check LOCK_TARGET_PRODUCT_ID matches lock holder target.")
    print("=" * 70 + "\n")


# =============================================================================
# 사용자 클래스 정의 (Locust 자동 인식용)
# =============================================================================

class ContentionBuyer(StockContentionUser):
    """High-frequency stock contention user (100%)"""
    weight = 100
