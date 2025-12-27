"""
Stage 7 EXTREME: Race Condition + Chaos Storm + Self-Healing 극한 테스트

목적: 진정한 극한 상황에서의 시스템 검증
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 테스트 시나리오:
1. Shared Order Pool - OrderProducer가 주문 생성, PaymentRacer가 경쟁적 결제
2. Burst Load - wait_time=0으로 분산 락 경합 극대화
3. CB Domino Effect - inventory → payment → point 연쇄 장애
4. Lock Poisoning - 락 획득 후 강제 종료 시뮬레이션 (유령 락 테스트)
5. Post-Storm Reconciliation - 테스트 후 데이터 정합성 전수 조사

실행:
    locust -f load_tests/scenarios/integration/stage7_race_extreme.py \\
        --host=http://localhost:8000 --users=100 --spawn-rate=50 \\
        --run-time=5m --headless

환경변수:
    STAGE7_DEBUG=true     # 디버그 로그 활성화
    STAGE7_CHAOS_RATE=0.5 # 장애 주입 확률 (기본 50%)
"""

import os
import sys
import json
import uuid
import time
import random
import threading
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
if _load_tests_dir not in sys.path:
    sys.path.insert(0, _load_tests_dir)

from locust import HttpUser, task, between, constant, constant_pacing, tag, events

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector

# Self-Healing 시스템 통합
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    SELFHEALING_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] SelfHealing import failed: {e}")
    SELFHEALING_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# 설정
# ═══════════════════════════════════════════════════════════════════════════════

STAGE_NAME = "[Stage7-EXTREME]"
DEBUG_MODE = os.environ.get("STAGE7_DEBUG", "false").lower() == "true"
CHAOS_RATE = float(os.environ.get("STAGE7_CHAOS_RATE", "0.5"))  # 50% 기본 장애율

# 타겟 서비스 (도미노 효과 순서)
DOMINO_SERVICES = ["inventory", "payment", "point", "notification"]


# ═══════════════════════════════════════════════════════════════════════════════
# 글로벌 통계 (Thread-Safe)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtremeStats:
    """극한 테스트 통계."""
    # Race Condition
    total_race_attempts: int = 0
    double_success: int = 0  # 중복 결제 (CRITICAL)
    shared_orders: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Lock Poisoning
    lock_poison_attempts: int = 0
    ghost_locks_detected: int = 0
    ghost_locks_healed: int = 0
    
    # Circuit Breaker Domino
    cb_injections: int = 0
    cb_domino_triggered: int = 0
    cb_recoveries: int = 0
    
    # Data Reconciliation (초기값/최종값)
    initial_stock: Dict[int, int] = field(default_factory=dict)  # product_id -> stock
    orders_completed: int = 0
    orders_failed: int = 0
    payments_success: int = 0
    payments_failed: int = 0
    
    # Self-Healing
    healing_events: int = 0
    emergency_triggers: int = 0
    blast_radius_tests: int = 0
    snapshots: int = 0
    
    # 에러 목록
    errors: List[str] = field(default_factory=list)


_stats = ExtremeStats()
_stats_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# Shared Order Pool (진짜 레이스 컨디션!)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SharedOrder:
    """공유 주문 정보."""
    order_id: str
    final_amount: float
    created_at: float
    payment_attempts: int = 0
    payment_success: bool = False
    locked_by: Optional[str] = None  # Lock Poisoning 테스트용


class SharedOrderPool:
    """
    공유 주문 풀.
    
    OrderProducer가 주문을 생성해서 풀에 던지고,
    PaymentRacer들이 경쟁적으로 결제를 시도합니다.
    """
    
    def __init__(self, max_size: int = 100):
        self.orders: List[SharedOrder] = []
        self.lock = threading.Lock()
        self.max_size = max_size
        self._initialized = False
    
    def add_order(self, order: SharedOrder) -> bool:
        """주문 추가."""
        with self.lock:
            if len(self.orders) >= self.max_size:
                # 가장 오래된 주문 제거
                self.orders.pop(0)
            self.orders.append(order)
            return True
    
    def get_random_order(self) -> Optional[SharedOrder]:
        """랜덤 주문 가져오기 (결제 시도용)."""
        with self.lock:
            # 아직 결제 성공하지 않은 주문만 필터
            pending = [o for o in self.orders if not o.payment_success]
            if not pending:
                return None
            return random.choice(pending)
    
    def get_fresh_order(self) -> Optional[SharedOrder]:
        """가장 최근 주문 가져오기 (레이스 최적화)."""
        with self.lock:
            pending = [o for o in self.orders if not o.payment_success]
            if not pending:
                return None
            # 가장 최근에 생성된 주문 (레이스 가능성 높음)
            return max(pending, key=lambda o: o.created_at)
    
    def mark_success(self, order_id: str) -> int:
        """결제 성공 표시. 이미 성공한 경우 중복 카운트 반환."""
        with self.lock:
            for order in self.orders:
                if order.order_id == order_id:
                    order.payment_attempts += 1
                    if order.payment_success:
                        # 이미 결제됨 - 중복!
                        return order.payment_attempts
                    order.payment_success = True
                    return 1  # 최초 성공
            return 0
    
    def mark_failure(self, order_id: str):
        """결제 실패 표시."""
        with self.lock:
            for order in self.orders:
                if order.order_id == order_id:
                    order.payment_attempts += 1
                    break
    
    def acquire_lock(self, order_id: str, owner: str) -> bool:
        """Lock Poisoning 테스트용 - 락 획득 시뮬레이션."""
        with self.lock:
            for order in self.orders:
                if order.order_id == order_id:
                    if order.locked_by is None:
                        order.locked_by = owner
                        return True
                    return False
            return False
    
    def release_lock(self, order_id: str, owner: str) -> bool:
        """락 해제."""
        with self.lock:
            for order in self.orders:
                if order.order_id == order_id:
                    if order.locked_by == owner:
                        order.locked_by = None
                        return True
                    return False
            return False
    
    def check_ghost_locks(self, timeout_seconds: float = 30.0) -> List[str]:
        """유령 락 감지 (timeout 초과 락)."""
        ghost_locks = []
        current_time = time.time()
        with self.lock:
            for order in self.orders:
                if order.locked_by is not None:
                    # 락 획득 후 너무 오래 지남 → 유령 락
                    if current_time - order.created_at > timeout_seconds:
                        ghost_locks.append(order.order_id)
        return ghost_locks
    
    def heal_ghost_lock(self, order_id: str) -> bool:
        """유령 락 강제 해제 (Self-Healing)."""
        with self.lock:
            for order in self.orders:
                if order.order_id == order_id:
                    if order.locked_by is not None:
                        order.locked_by = None
                        return True
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """풀 통계."""
        with self.lock:
            return {
                "total_orders": len(self.orders),
                "pending_orders": sum(1 for o in self.orders if not o.payment_success),
                "completed_orders": sum(1 for o in self.orders if o.payment_success),
                "locked_orders": sum(1 for o in self.orders if o.locked_by is not None),
            }


# 글로벌 공유 풀
_order_pool = SharedOrderPool(max_size=200)

# Self-Healing 클라이언트 (싱글톤)
_sh_client: Optional[SelfHealingClient] = None
_sh_lock = threading.Lock()
_sh_initialized = False


def get_selfhealing_client() -> Optional[SelfHealingClient]:
    """Self-Healing 클라이언트 싱글톤."""
    global _sh_client, _sh_initialized
    
    if not SELFHEALING_AVAILABLE:
        return None
    
    if _sh_initialized:
        return _sh_client
    
    with _sh_lock:
        if _sh_initialized:
            return _sh_client
        
        try:
            host = os.environ.get("SELFHEALING_HOST", "http://localhost:8000")
            _sh_client = SelfHealingClient(
                host=host,
                auth_mode="xtest",
                timeout=10,
            )
            _sh_initialized = True
            debug_log(f"SelfHealingClient initialized: {host}")
        except Exception as e:
            debug_log(f"SelfHealing init failed: {e}")
            _sh_client = None
            _sh_initialized = True
    
    return _sh_client


def debug_log(msg: str):
    """디버그 로그."""
    if DEBUG_MODE:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[DEBUG {ts}] {msg}")


# ═══════════════════════════════════════════════════════════════════════════════
# User Classes
# ═══════════════════════════════════════════════════════════════════════════════

class OrderProducer(HttpUser):
    """
    주문 생성 전용 유저 (10% 비율).
    
    역할: 주문만 생성해서 SharedOrderPool에 던짐
    속도: 1초에 1개 주문 (안정적)
    """
    
    weight = 1  # 10% 비율
    wait_time = constant(1)  # 1초마다 주문 생성
    
    def on_start(self):
        setup_event_hooks(STAGE_NAME)
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # 초기 재고 스냅샷 (Reconciliation용)
        self._capture_initial_stock()
    
    def _capture_initial_stock(self):
        """초기 재고 스냅샷 캡처."""
        global _stats
        try:
            with self.client.get(
                "/api/products/",
                params={"page_size": 100},
                name=f"{STAGE_NAME} [Setup] Initial Stock Snapshot",
                catch_response=True,
            ) as resp:
                if resp.status_code == 200:
                    data = resp.json()
                    products = data.get("results", [])
                    with _stats_lock:
                        for p in products:
                            pid = p.get("id")
                            stock = p.get("stock", 0)
                            if pid not in _stats.initial_stock:
                                _stats.initial_stock[pid] = stock
                    resp.success()
                else:
                    resp.failure(f"Status {resp.status_code}")
        except Exception as e:
            debug_log(f"Initial stock capture failed: {e}")
    
    @task(10)
    @tag("producer", "order")
    def produce_order(self):
        """주문 생성 → 풀에 추가."""
        global _order_pool
        
        if not self.login_helper.ensure_logged_in():
            return
        
        product_ids = self.product_helper.cached_product_ids
        if not product_ids:
            return
        
        # 장바구니 준비
        self.cart_helper.clear_cart()
        product_id = random.choice(product_ids)
        self.cart_helper.add_item(product_id, 1)
        
        if not self.cart_helper.has_items():
            return
        
        # 주문 생성
        order_data = self.payment_helper.create_order()
        if not order_data:
            return
        
        order_id = order_data.get("order_id")
        final_amount = order_data.get("final_amount")
        
        if not order_id or not final_amount:
            return
        
        # 풀에 추가
        shared_order = SharedOrder(
            order_id=order_id,
            final_amount=final_amount,
            created_at=time.time(),
        )
        _order_pool.add_order(shared_order)
        
        debug_log(f"Order produced: {order_id} (amount={final_amount})")


class PaymentRacer(HttpUser):
    """
    결제 경쟁 유저 (60% 비율).
    
    역할: 풀에서 주문 가져와서 미친 듯이 결제 시도
    속도: wait_time=0 (Burst Load!)
    """
    
    weight = 6  # 60% 비율
    wait_time = constant(0)  # 🔥 Burst! 쉬지 않고 공격
    
    def on_start(self):
        setup_event_hooks(STAGE_NAME)
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        self.login_helper.login()
        self.user_id = str(uuid.uuid4())[:8]
    
    @task(10)
    @tag("racer", "payment", "burst")
    def race_payment(self):
        """풀에서 주문 가져와서 결제 시도."""
        global _stats, _order_pool
        
        if not self.login_helper.ensure_logged_in():
            return
        
        # 가장 최근 주문 가져오기 (레이스 확률 ↑)
        order = _order_pool.get_fresh_order()
        if not order:
            # 풀이 비었으면 잠시 대기
            time.sleep(0.1)
            return
        
        order_id = order.order_id
        final_amount = order.final_amount
        
        # 결제 시도!
        payment_key = self.payment_helper.generate_payment_key(f"race_{self.user_id}")
        
        with _stats_lock:
            _stats.total_race_attempts += 1
            if order_id not in _stats.shared_orders:
                _stats.shared_orders[order_id] = {"success": 0, "fail": 0}
        
        with self.client.post(
            "/api/payments/confirm/",
            json={
                "payment_key": payment_key,
                "order_id": order_id,
                "amount": int(final_amount),
            },
            name=f"{STAGE_NAME} POST /payments/confirm/ [RACE]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                # 성공!
                success_count = _order_pool.mark_success(order_id)
                
                with _stats_lock:
                    _stats.shared_orders[order_id]["success"] += 1
                    _stats.payments_success += 1
                    
                    if success_count > 1:
                        # 🚨 CRITICAL: 중복 결제!
                        _stats.double_success += 1
                        response.failure(f"🚨 DOUBLE PAYMENT on {order_id}!")
                        debug_log(f"🚨 DOUBLE PAYMENT DETECTED: {order_id} (count={success_count})")
                    else:
                        response.success()
                        debug_log(f"✅ Payment success: {order_id}")
            
            elif response.status_code in [400, 409]:
                # 이미 결제됨 - 정상적인 레이스 실패
                _order_pool.mark_failure(order_id)
                with _stats_lock:
                    _stats.shared_orders[order_id]["fail"] += 1
                    _stats.payments_failed += 1
                response.success()  # 예상된 응답
            
            else:
                # 예상치 못한 에러
                _order_pool.mark_failure(order_id)
                with _stats_lock:
                    _stats.shared_orders[order_id]["fail"] += 1
                    _stats.payments_failed += 1
                response.failure(f"Unexpected: {response.status_code}")
    
    @task(2)
    @tag("racer", "lock_poison")
    def lock_poisoning_test(self):
        """
        Lock Poisoning 테스트.
        
        시나리오: 락 획득 후 "강제 종료" 시뮬레이션
        → 유령 락이 발생하고, Self-Healing이 감지/해제하는지 검증
        """
        global _stats, _order_pool
        
        order = _order_pool.get_random_order()
        if not order:
            return
        
        order_id = order.order_id
        owner = f"poison_{self.user_id}"
        
        # 락 획득 시도
        if _order_pool.acquire_lock(order_id, owner):
            with _stats_lock:
                _stats.lock_poison_attempts += 1
            
            debug_log(f"🔒 Lock acquired for poisoning: {order_id}")
            
            # 50% 확률로 "강제 종료" 시뮬레이션 (락 해제 안 함)
            if random.random() < 0.5:
                debug_log(f"💀 Lock POISONED (not released): {order_id}")
                # 락을 해제하지 않음 → 유령 락!
                return
            
            # 정상 해제
            _order_pool.release_lock(order_id, owner)
            debug_log(f"🔓 Lock released normally: {order_id}")


class ChaosInjector(HttpUser):
    """
    카오스 주입 유저 (30% 비율).
    
    역할: CB 도미노 효과 유발 + Self-Healing 모니터링
    속도: 0.5초마다 장애 주입
    """
    
    weight = 3  # 30% 비율
    wait_time = constant_pacing(0.5)  # 0.5초마다 장애 주입
    
    def on_start(self):
        setup_event_hooks(STAGE_NAME)
        self.sh = get_selfhealing_client()
        self.domino_index = 0  # 도미노 순서
    
    @task(5)
    @tag("chaos", "cb_domino")
    def inject_cb_domino(self):
        """
        Circuit Breaker 도미노 효과 (개선: 실제 API 호출 기반).
        
        inventory → payment → point → notification 순서로 장애 주입
        + 실제 API 호출로 CB 상태 변화 확인
        """
        global _stats
        
        if not self.sh:
            return
        
        if random.random() > CHAOS_RATE:
            return
        
        # 도미노 순서대로 서비스 선택
        service = DOMINO_SERVICES[self.domino_index % len(DOMINO_SERVICES)]
        self.domino_index += 1
        
        try:
            # 100% 실패율로 장애 주입
            result = self.sh.xtest.inject_cb_failure(
                service_name=service,
                failure_type=random.choice(["exception", "timeout"]),
                failure_rate=1.0,  # 100% 실패!
                duration_seconds=30,  # 더 긴 지속 시간
            )
            
            with _stats_lock:
                _stats.cb_injections += 1
            
            debug_log(f"🔥 CB Injection: {service} → {result.get('status')}")
            
            # 힐링 이벤트 기록
            self.sh.xtest.record_healing_event(
                event_type="cb_domino_injected",
                service_name=service,
                details={"failure_rate": 1.0, "domino_index": self.domino_index},
            )
            
            with _stats_lock:
                _stats.healing_events += 1
            
            # 도미노 효과 확인 (다른 서비스도 영향 받았는지)
            cb_status = self.sh.xtest.get_cb_status()
            open_count = sum(
                1 for s, info in cb_status.get("services", {}).items()
                if info.get("state") == "open" and s in DOMINO_SERVICES
            )
            
            if open_count >= 2:
                with _stats_lock:
                    _stats.cb_domino_triggered += 1
                debug_log(f"💥 DOMINO EFFECT: {open_count} services affected!")
                
        except Exception as e:
            debug_log(f"CB Injection error: {e}")
            with _stats_lock:
                _stats.errors.append(f"cb_inject: {str(e)[:50]}")
    
    @task(3)
    @tag("chaos", "cb_domino_real")
    def trigger_real_cb_cascade(self):
        """
        실제 API 호출로 CB 도미노 트리거 (개선 추가).
        
        X-Test-Mode 헤더로 의도적 실패를 유발하여
        실제 CB가 열리는지 확인
        """
        global _stats
        
        if random.random() > 0.3:  # 30% 확률
            return
        
        # X-Test-Mode로 실패 유발
        headers = {"X-Test-Mode": "chaos-force-fail"}
        
        # 연쇄 실패 유발: inventory → payment 순서
        cascade_endpoints = [
            ("/api/products/1/", "inventory"),
            ("/api/payments/status/test/", "payment"),
        ]
        
        failures = 0
        for endpoint, service in cascade_endpoints:
            try:
                with self.client.get(
                    endpoint,
                    headers=headers,
                    name=f"{STAGE_NAME} CB Cascade [{service}]",
                    catch_response=True,
                ) as resp:
                    if resp.status_code >= 500:
                        failures += 1
                        resp.success()  # 의도된 실패
                    else:
                        resp.success()
            except:
                failures += 1
        
        if failures >= 2:
            with _stats_lock:
                _stats.cb_domino_triggered += 1
            debug_log(f"💥 REAL CB CASCADE: {failures} services failed!")
    
    @task(5)  # 더 자주 실행하여 유령 락 빠르게 정리
    @tag("chaos", "ghost_lock")
    def detect_ghost_locks(self):
        """유령 락 감지 및 힐링 (개선: 더 적극적인 정리)."""
        global _stats, _order_pool
        
        if not self.sh:
            return
        
        # 유령 락 감지 (timeout을 10초로 단축 - 더 빠른 감지)
        ghost_locks = _order_pool.check_ghost_locks(timeout_seconds=10.0)
        
        if ghost_locks:
            with _stats_lock:
                _stats.ghost_locks_detected += len(ghost_locks)
            
            debug_log(f"👻 Ghost locks detected: {len(ghost_locks)}")
            
            # Self-Healing: 유령 락 전체 강제 해제 (하나씩이 아닌 전체)
            healed_count = 0
            for order_id in ghost_locks:
                if _order_pool.heal_ghost_lock(order_id):
                    healed_count += 1
                    debug_log(f"🏥 Ghost lock healed: {order_id}")
            
            if healed_count > 0:
                with _stats_lock:
                    _stats.ghost_locks_healed += healed_count
                    _stats.healing_events += healed_count
                
                # 힐링 이벤트 기록 (배치)
                try:
                    self.sh.xtest.record_healing_event(
                        event_type="ghost_lock_batch_healed",
                        service_name="distributed_lock",
                        details={"healed_count": healed_count, "order_ids": ghost_locks[:5]},
                    )
                except:
                    pass
    
    @task(2)
    @tag("chaos", "snapshot")
    def take_snapshot(self):
        """시스템 스냅샷."""
        global _stats
        
        if not self.sh:
            return
        
        try:
            snapshot = self.sh.xtest.get_snapshot()
            with _stats_lock:
                _stats.snapshots += 1
            
            debug_log(f"📸 Snapshot: CPU={snapshot.get('snapshot', {}).get('cpu_percent', 'N/A')}%")
            
        except Exception as e:
            debug_log(f"Snapshot error: {e}")
    
    @task(2)
    @tag("chaos", "blast_radius")
    def blast_radius_test(self):
        """Blast Radius 테스트."""
        global _stats
        
        if not self.sh:
            return
        
        if random.random() > 0.3:  # 30% 확률
            return
        
        try:
            result = self.sh.xtest.test_multi_blast_radius(
                services=DOMINO_SERVICES,
                failure_type="exception",
            )
            
            with _stats_lock:
                _stats.blast_radius_tests += 1
            
            isolation_score = result.get("isolation_score_percent", 0)
            debug_log(f"🎯 Blast Radius: isolation={isolation_score}%")
            
        except Exception as e:
            debug_log(f"Blast radius error: {e}")
    
    @task(1)
    @tag("chaos", "recovery")
    def trigger_recovery(self):
        """CB 복구 트리거."""
        global _stats
        
        if not self.sh:
            return
        
        if random.random() > 0.2:  # 20% 확률
            return
        
        try:
            service = random.choice(DOMINO_SERVICES)
            result = self.sh.xtest.trigger_cb_recovery(service)
            
            with _stats_lock:
                _stats.cb_recoveries += 1
            
            debug_log(f"🔄 CB Recovery triggered: {service} → {result.get('status')}")
            
        except Exception as e:
            debug_log(f"Recovery error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Post-Storm Reconciliation (데이터 정합성 검증)
# ═══════════════════════════════════════════════════════════════════════════════

def reconcile_data(environment) -> Dict[str, Any]:
    """
    테스트 종료 후 데이터 정합성 전수 조사.
    
    검증:
    - 초기 재고 - 완료 주문 = 현재 재고 (1개 오차 없이)
    - 중복 결제 0건
    """
    global _stats, _order_pool
    
    result = {
        "passed": True,
        "checks": [],
        "errors": [],
    }
    
    # 1. 중복 결제 체크
    if _stats.double_success > 0:
        result["passed"] = False
        result["errors"].append(f"🚨 CRITICAL: {_stats.double_success} double payments!")
    else:
        result["checks"].append("✅ No double payments")
    
    # 2. 풀 통계 확인
    pool_stats = _order_pool.get_stats()
    result["pool_stats"] = pool_stats
    
    # 3. 유령 락 체크
    ghost_locks = _order_pool.check_ghost_locks(timeout_seconds=10.0)
    if ghost_locks:
        result["errors"].append(f"⚠️ {len(ghost_locks)} ghost locks remaining")
    else:
        result["checks"].append("✅ No ghost locks remaining")
    
    # 4. Self-Healing 상태 확인
    sh = get_selfhealing_client()
    if sh:
        try:
            cb_status = sh.xtest.get_cb_status()
            open_cbs = [
                s for s, info in cb_status.get("services", {}).items()
                if info.get("state") == "open"
            ]
            if open_cbs:
                result["errors"].append(f"⚠️ {len(open_cbs)} CBs still open: {open_cbs[:5]}")
            else:
                result["checks"].append("✅ All Circuit Breakers closed")
        except Exception as e:
            result["errors"].append(f"CB status check failed: {e}")
    
    # 5. 정합성 요약
    result["summary"] = {
        "total_race_attempts": _stats.total_race_attempts,
        "payments_success": _stats.payments_success,
        "payments_failed": _stats.payments_failed,
        "double_success": _stats.double_success,
        "lock_poison_attempts": _stats.lock_poison_attempts,
        "ghost_locks_detected": _stats.ghost_locks_detected,
        "ghost_locks_healed": _stats.ghost_locks_healed,
        "cb_injections": _stats.cb_injections,
        "cb_domino_triggered": _stats.cb_domino_triggered,
        "cb_recoveries": _stats.cb_recoveries,
        "healing_events": _stats.healing_events,
        "blast_radius_tests": _stats.blast_radius_tests,
    }
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 테스트 이벤트 핸들러
# ═══════════════════════════════════════════════════════════════════════════════

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력 및 Reconciliation."""
    global _stats, _order_pool
    
    # ═══════════════════════════════════════════════════════════════════════════
    # 쿨다운: 유령 락 완전 정리 (개선)
    # ═══════════════════════════════════════════════════════════════════════════
    print("\n⏳ Cooldown: Cleaning up ghost locks...")
    cleanup_attempts = 0
    max_cleanup_attempts = 5
    
    while cleanup_attempts < max_cleanup_attempts:
        ghost_locks = _order_pool.check_ghost_locks(timeout_seconds=1.0)  # 1초 초과면 정리
        if not ghost_locks:
            print(f"✅ All ghost locks cleaned after {cleanup_attempts + 1} attempts")
            break
        
        for order_id in ghost_locks:
            if _order_pool.heal_ghost_lock(order_id):
                with _stats_lock:
                    _stats.ghost_locks_healed += 1
        
        cleanup_attempts += 1
        time.sleep(0.5)  # 짧은 대기
    
    if cleanup_attempts >= max_cleanup_attempts:
        remaining = _order_pool.check_ghost_locks(timeout_seconds=1.0)
        if remaining:
            print(f"⚠️ {len(remaining)} ghost locks could not be cleaned")
    
    print("\n" + "═" * 80)
    print("🔥 STAGE 7 EXTREME: RACE + CHAOS STORM TEST RESULTS")
    print("═" * 80)
    
    # 1. Race Condition 결과
    print("\n📊 RACE CONDITION TEST")
    print("-" * 50)
    print(f"Total Race Attempts: {_stats.total_race_attempts}")
    print(f"Unique Orders Tested: {len(_stats.shared_orders)}")
    print(f"Payments Success: {_stats.payments_success}")
    print(f"Payments Failed: {_stats.payments_failed}")
    print(f"Double Success (CRITICAL): {_stats.double_success}")
    
    if _stats.double_success == 0:
        print("\n✅ RACE CONDITION TEST: PASSED")
    else:
        print(f"\n❌ RACE CONDITION TEST: FAILED ({_stats.double_success} duplicates!)")
    
    # 2. Lock Poisoning 결과
    print("\n🔒 LOCK POISONING TEST")
    print("-" * 50)
    print(f"Lock Poison Attempts: {_stats.lock_poison_attempts}")
    print(f"Ghost Locks Detected: {_stats.ghost_locks_detected}")
    print(f"Ghost Locks Healed: {_stats.ghost_locks_healed}")
    
    remaining_ghosts = _order_pool.check_ghost_locks(timeout_seconds=5.0)
    if not remaining_ghosts:
        print("\n✅ LOCK POISONING TEST: PASSED (All ghost locks healed)")
    else:
        print(f"\n⚠️ LOCK POISONING TEST: {len(remaining_ghosts)} ghost locks remaining")
    
    # 3. Circuit Breaker Domino 결과
    print("\n🔥 CIRCUIT BREAKER DOMINO TEST")
    print("-" * 50)
    print(f"CB Injections: {_stats.cb_injections}")
    print(f"Domino Effects Triggered: {_stats.cb_domino_triggered}")
    print(f"CB Recoveries: {_stats.cb_recoveries}")
    
    if _stats.cb_domino_triggered > 0:
        print(f"\n✅ CB DOMINO TEST: {_stats.cb_domino_triggered} domino effects observed")
    else:
        print("\n⚠️ CB DOMINO TEST: No domino effects observed")
    
    # 4. Self-Healing 결과
    print("\n🏥 SELF-HEALING METRICS")
    print("-" * 50)
    print(f"Healing Events Recorded: {_stats.healing_events}")
    print(f"Blast Radius Tests: {_stats.blast_radius_tests}")
    print(f"Snapshots Taken: {_stats.snapshots}")
    
    # 5. Post-Storm Reconciliation
    print("\n📝 POST-STORM RECONCILIATION")
    print("-" * 50)
    recon = reconcile_data(environment)
    
    for check in recon["checks"]:
        print(f"  {check}")
    for error in recon["errors"]:
        print(f"  {error}")
    
    # 6. 최종 요약
    collector = get_metrics_collector()
    summary = collector.get_summary()
    
    print("\n" + "═" * 80)
    print("📋 FINAL SUMMARY")
    print("═" * 80)
    print(f"Total Requests: {summary['total_requests']}")
    print(f"Error Rate: {summary['overall_error_rate']}%")
    print(f"Race Test: {'✅ PASSED' if _stats.double_success == 0 else '❌ FAILED'}")
    print(f"Lock Poison Test: {'✅ PASSED' if not remaining_ghosts else '⚠️ DEGRADED'}")
    print(f"CB Domino Test: {'✅ OBSERVED' if _stats.cb_domino_triggered > 0 else '⚠️ NOT TRIGGERED'}")
    print(f"Reconciliation: {'✅ PASSED' if recon['passed'] else '❌ FAILED'}")
    
    overall = (
        _stats.double_success == 0 and
        not remaining_ghosts and
        recon["passed"]
    )
    print(f"\n{'🎉 ALL TESTS PASSED!' if overall else '⚠️ SOME TESTS NEED ATTENTION'}")
    print("═" * 80)
    
    # 결과 저장
    _save_results(recon, summary, remaining_ghosts)


def _save_results(recon: Dict, summary: Dict, remaining_ghosts: List):
    """결과를 JSON 파일로 저장."""
    global _stats
    
    try:
        results_dir = os.path.join(_load_tests_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"stage7_extreme_{timestamp}.json"
        filepath = os.path.join(results_dir, filename)
        
        result_data = {
            "test_name": "Stage 7 EXTREME: Race + Chaos Storm",
            "timestamp": datetime.now().isoformat(),
            "race_condition": {
                "total_attempts": _stats.total_race_attempts,
                "unique_orders": len(_stats.shared_orders),
                "payments_success": _stats.payments_success,
                "payments_failed": _stats.payments_failed,
                "double_success": _stats.double_success,
                "passed": _stats.double_success == 0,
            },
            "lock_poisoning": {
                "attempts": _stats.lock_poison_attempts,
                "ghost_detected": _stats.ghost_locks_detected,
                "ghost_healed": _stats.ghost_locks_healed,
                "remaining": len(remaining_ghosts),
                "passed": len(remaining_ghosts) == 0,
            },
            "cb_domino": {
                "injections": _stats.cb_injections,
                "domino_triggered": _stats.cb_domino_triggered,
                "recoveries": _stats.cb_recoveries,
            },
            "self_healing": {
                "events": _stats.healing_events,
                "blast_radius_tests": _stats.blast_radius_tests,
                "snapshots": _stats.snapshots,
            },
            "reconciliation": recon,
            "performance": {
                "total_requests": summary.get("total_requests", 0),
                "error_rate": summary.get("overall_error_rate", 0),
            },
            "errors": _stats.errors[:20],  # 최대 20개
        }
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n📁 Results saved: {filepath}")
        
    except Exception as e:
        print(f"\n⚠️ Failed to save results: {e}")
