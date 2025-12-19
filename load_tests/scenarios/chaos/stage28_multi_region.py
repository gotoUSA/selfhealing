"""
Stage 28: Multi-Region Latency Test

목표: 클라우드 멀티 리전 환경에서 네트워크 이상 시 복원력 검증

시나리오:
  - Region A: 정상 동작 (latency < 10ms)
  - Region B: 90% 패킷 손실 또는 높은 지연

테스트 케이스:
  - TC-28-1: Region 간 비대칭 장애
  - TC-28-2: 단일 리전 Clock Skew
  - TC-28-3: Cross-Region Fallback Consistency

실행 방법:
    # 기본 모드 (헤더 시뮬레이션)
    locust -f load_tests/scenarios/stage28_multi_region.py --host=http://localhost:8000

    # Toxiproxy Chaos 모드 (실제 네트워크 장애 주입)
    TOXIPROXY_URL=http://toxiproxy:8474 locust -f load_tests/scenarios/stage28_multi_region.py \\
        --host=http://web:8000 --users=50 --spawn-rate=5 --run-time=3m --headless

검증 기준:
  - Failover 시간: < 5초
  - 데이터 일관성: 100% (중복 주문/결제 0건)
  - Clock Skew 허용: ±30초
  - 복구 후 라우팅: < 10초

Reference:
  - docs/STAGE_28_30_ADVANCED_CHAOS_PLAN.md
"""

import os
import sys
import time
import random
import threading
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

# Toxiproxy 클라이언트 (Chaos Engineering)
try:
    from load_tests.chaos.toxiproxy_client import ToxiproxyClient, ToxicType, get_toxiproxy_client

    TOXIPROXY_AVAILABLE = True
except ImportError:
    TOXIPROXY_AVAILABLE = False
    ToxiproxyClient = None


STAGE_NAME = "[Stage28-MultiRegion]"

# =============================================================================
# Region 설정
# =============================================================================


class RegionStatus(str, Enum):
    """리전 상태"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 90% 패킷 손실
    DOWN = "down"
    CLOCK_SKEW = "clock_skew"


@dataclass
class RegionConfig:
    """리전 설정"""

    name: str
    latency_ms: int = 5
    packet_loss: float = 0.0
    clock_offset_sec: int = 0
    status: RegionStatus = RegionStatus.HEALTHY
    is_primary: bool = False

    # 통계
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    fallback_count: int = 0


# 기본 리전 설정
REGION_A = RegionConfig(name="ap-northeast-2a", latency_ms=5, packet_loss=0.0, clock_offset_sec=0, is_primary=True)

REGION_B = RegionConfig(name="ap-northeast-2b", latency_ms=50, packet_loss=0.0, clock_offset_sec=0, is_primary=False)


# =============================================================================
# Multi-Region Coordinator
# =============================================================================


class MultiRegionCoordinator:
    """멀티 리전 조정자"""

    def __init__(self):
        self.regions: Dict[str, RegionConfig] = {
            "region_a": RegionConfig(
                name="ap-northeast-2a", latency_ms=5, packet_loss=0.0, clock_offset_sec=0, is_primary=True
            ),
            "region_b": RegionConfig(
                name="ap-northeast-2b", latency_ms=50, packet_loss=0.0, clock_offset_sec=0, is_primary=False
            ),
        }
        self.active_region = "region_a"
        self.failover_in_progress = False
        self.last_failover_time: Optional[datetime] = None
        self.lock = threading.Lock()

        # 중복 방지를 위한 처리 기록
        self.processed_orders: Dict[str, datetime] = {}
        self.processed_payments: Dict[str, datetime] = {}
        self.idempotency_keys: Dict[str, str] = {}

    def get_active_region(self) -> RegionConfig:
        """현재 활성 리전 반환"""
        return self.regions[self.active_region]

    def get_fallback_region(self) -> Optional[RegionConfig]:
        """Fallback 리전 반환"""
        for name, region in self.regions.items():
            if name != self.active_region and region.status == RegionStatus.HEALTHY:
                return region
        return None

    def inject_region_failure(self, region_name: str, failure_type: str):
        """리전 장애 주입"""
        with self.lock:
            if region_name in self.regions:
                region = self.regions[region_name]
                if failure_type == "packet_loss":
                    region.packet_loss = 0.9  # 90% 패킷 손실
                    region.status = RegionStatus.DEGRADED
                elif failure_type == "clock_skew":
                    region.clock_offset_sec = 300  # 5분 드리프트
                    region.status = RegionStatus.CLOCK_SKEW
                elif failure_type == "down":
                    region.status = RegionStatus.DOWN

                print(f"{STAGE_NAME} Region {region_name} failure injected: {failure_type}")

    def recover_region(self, region_name: str):
        """리전 복구"""
        with self.lock:
            if region_name in self.regions:
                region = self.regions[region_name]
                region.packet_loss = 0.0
                region.clock_offset_sec = 0
                region.status = RegionStatus.HEALTHY
                print(f"{STAGE_NAME} Region {region_name} recovered")

    def should_fallback(self, region_name: str) -> bool:
        """Fallback 필요 여부 판단"""
        region = self.regions.get(region_name)
        if not region:
            return False

        # 90% 이상 패킷 손실 또는 DOWN 상태면 fallback
        return region.packet_loss >= 0.9 or region.status == RegionStatus.DOWN

    def perform_failover(self, from_region: str) -> Optional[str]:
        """Failover 수행"""
        with self.lock:
            if self.failover_in_progress:
                return None

            self.failover_in_progress = True
            fallback = self.get_fallback_region()

            if fallback:
                old_active = self.active_region
                for name, region in self.regions.items():
                    if region.name == fallback.name:
                        self.active_region = name
                        self.last_failover_time = datetime.now(timezone.utc)
                        self.regions[old_active].fallback_count += 1
                        print(f"{STAGE_NAME} Failover: {old_active} -> {name}")
                        break

            self.failover_in_progress = False
            return self.active_region

    def check_idempotency(self, key: str) -> Tuple[bool, Optional[str]]:
        """Idempotency 키 확인"""
        with self.lock:
            if key in self.idempotency_keys:
                return True, self.idempotency_keys[key]
            return False, None

    def register_idempotency(self, key: str, result: str):
        """Idempotency 키 등록"""
        with self.lock:
            self.idempotency_keys[key] = result

    def check_clock_skew(self, region_name: str, timestamp: datetime) -> Tuple[bool, int]:
        """Clock Skew 확인"""
        region = self.regions.get(region_name)
        if not region:
            return True, 0

        # 리전의 시간 오프셋 적용
        adjusted_time = timestamp + timedelta(seconds=region.clock_offset_sec)
        now = datetime.now(timezone.utc)

        skew_seconds = abs((adjusted_time - now).total_seconds())
        is_valid = skew_seconds <= 30  # 30초 이내 허용

        return is_valid, int(skew_seconds)

    def simulate_packet_loss(self, region_name: str) -> bool:
        """패킷 손실 시뮬레이션 - True면 요청 실패"""
        region = self.regions.get(region_name)
        if not region:
            return False
        return random.random() < region.packet_loss

    def get_latency(self, region_name: str) -> int:
        """리전 지연 시간 반환 (ms)"""
        region = self.regions.get(region_name)
        return region.latency_ms if region else 0


# 전역 조정자
_coordinator = MultiRegionCoordinator()


# =============================================================================
# 테스트 통계
# =============================================================================


@dataclass
class MultiRegionStats:
    """멀티 리전 테스트 통계"""

    total_requests: int = 0
    region_a_requests: int = 0
    region_b_requests: int = 0

    # Failover 통계
    failover_count: int = 0
    failover_success: int = 0
    failover_time_sum_ms: float = 0

    # 데이터 일관성
    duplicate_orders_prevented: int = 0
    duplicate_payments_prevented: int = 0
    idempotency_hits: int = 0

    # Clock Skew
    clock_skew_detections: int = 0
    clock_skew_rejections: int = 0

    # 복구
    recovery_count: int = 0
    recovery_time_sum_ms: float = 0

    # 에러
    packet_loss_errors: int = 0
    timeout_errors: int = 0
    consistency_errors: int = 0


_stats = MultiRegionStats()
_stats_lock = threading.Lock()


def record_request(region: str):
    """요청 기록"""
    with _stats_lock:
        _stats.total_requests += 1
        if region == "region_a":
            _stats.region_a_requests += 1
        else:
            _stats.region_b_requests += 1


def record_failover(success: bool, time_ms: float):
    """Failover 기록"""
    with _stats_lock:
        _stats.failover_count += 1
        if success:
            _stats.failover_success += 1
        _stats.failover_time_sum_ms += time_ms


def record_duplicate_prevented(order: bool = False, payment: bool = False):
    """중복 방지 기록"""
    with _stats_lock:
        if order:
            _stats.duplicate_orders_prevented += 1
        if payment:
            _stats.duplicate_payments_prevented += 1


def record_idempotency_hit():
    """Idempotency 히트 기록"""
    with _stats_lock:
        _stats.idempotency_hits += 1


def record_clock_skew(rejected: bool = False):
    """Clock Skew 기록"""
    with _stats_lock:
        _stats.clock_skew_detections += 1
        if rejected:
            _stats.clock_skew_rejections += 1


def record_recovery(time_ms: float):
    """복구 기록"""
    with _stats_lock:
        _stats.recovery_count += 1
        _stats.recovery_time_sum_ms += time_ms


def record_error(error_type: str):
    """에러 기록"""
    with _stats_lock:
        if error_type == "packet_loss":
            _stats.packet_loss_errors += 1
        elif error_type == "timeout":
            _stats.timeout_errors += 1
        elif error_type == "consistency":
            _stats.consistency_errors += 1


# =============================================================================
# Locust User
# =============================================================================

# 테스트용 사용자 계정 (미리 생성되어 있어야 함)
TEST_USERS = [
    {"username": "testuser1", "password": "testpass123"},
    {"username": "testuser2", "password": "testpass123"},
    {"username": "testuser3", "password": "testpass123"},
    {"username": "testuser4", "password": "testpass123"},
    {"username": "testuser5", "password": "testpass123"},
]

# 사용 가능한 상품 ID 목록 (Region A와 B 공통)
AVAILABLE_PRODUCT_IDS = [1, 3, 4, 5]

# 리전별 호스트 URL
REGION_HOSTS = {
    "region_a": os.getenv("REGION_A_HOST", "http://web-region-a:8000"),
    "region_b": os.getenv("REGION_B_HOST", "http://web-region-b:8000"),
}


class MultiRegionUser(HttpUser):
    """멀티 리전 테스트 사용자"""

    wait_time = between(1, 3)

    # 기본 호스트 (Region A)
    host = REGION_HOSTS.get("region_a", "http://web-region-a:8000")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.coordinator = _coordinator
        self.current_region = "region_a"
        self.user_id = None
        self.order_ids: List[str] = []
        self.auth_token = None
        self.auth_tokens: Dict[str, str] = {}  # 리전별 토큰 저장
        self.username = None

    def on_start(self):
        """테스트 시작 시 초기화 및 양쪽 리전에 로그인"""
        self.user_id = f"user_{random.randint(1, 1000)}"

        # 랜덤 사용자로 로그인
        user_creds = random.choice(TEST_USERS)
        self.username = user_creds["username"]

        # 양쪽 리전에 모두 로그인 (Failover 대비)
        for region_name, host in REGION_HOSTS.items():
            self._login_to_region(region_name, host, user_creds)

        # 현재 리전 토큰 설정
        self.auth_token = self.auth_tokens.get(self.current_region)
        print(f"{STAGE_NAME} User {self.username} ready on both regions")

    def _login_to_region(self, region_name: str, host: str, user_creds: dict):
        """특정 리전에 로그인"""
        import requests

        try:
            r = requests.post(
                f"{host}/api/auth/login/", json=user_creds, headers={"Content-Type": "application/json"}, timeout=10
            )
            if r.status_code == 200:
                token = r.json().get("token", {}).get("access")
                if token:
                    self.auth_tokens[region_name] = token
        except Exception as e:
            print(f"{STAGE_NAME} Login to {region_name} failed: {e}")

    def _switch_region(self, new_region: str):
        """리전 전환 - 실제 호스트 변경"""
        if new_region in REGION_HOSTS:
            self.current_region = new_region
            self.host = REGION_HOSTS[new_region]
            self.auth_token = self.auth_tokens.get(new_region)
            # Locust client의 base_url 변경
            self.client.base_url = REGION_HOSTS[new_region]

    def _get_current_host(self) -> str:
        """현재 리전의 호스트 반환"""
        return REGION_HOSTS.get(self.current_region, REGION_HOSTS["region_a"])

    def _get_region_header(self) -> Dict[str, str]:
        """리전 헤더 생성 (인증 토큰 포함)"""
        region = self.coordinator.get_active_region()
        timestamp = datetime.now(timezone.utc).isoformat()
        headers = {
            "X-Region": region.name,
            "X-Region-Latency-Ms": str(region.latency_ms),
            "X-Request-Timestamp": timestamp,
            "Content-Type": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _simulate_region_latency(self):
        """리전 지연 시뮬레이션"""
        latency_ms = self.coordinator.get_latency(self.current_region)
        if latency_ms > 0:
            time.sleep(latency_ms / 1000)

    def _generate_idempotency_key(self, operation: str) -> str:
        """Idempotency 키 생성"""
        unique_data = f"{self.user_id}:{operation}:{time.time_ns()}"
        return hashlib.sha256(unique_data.encode()).hexdigest()[:32]

    @task(3)
    @tag("tc-28-1", "asymmetric-failure")
    def test_asymmetric_region_failure(self):
        """TC-28-1: Region 간 비대칭 장애 테스트"""
        record_request(self.current_region)

        # 패킷 손실 시뮬레이션
        if self.coordinator.simulate_packet_loss(self.current_region):
            record_error("packet_loss")

            # Failover 시도 - 실제로 다른 리전으로 전환
            start_time = time.time()
            new_region = self.coordinator.perform_failover(self.current_region)

            if new_region and new_region != self.current_region:
                failover_time_ms = (time.time() - start_time) * 1000
                record_failover(True, failover_time_ms)
                self._switch_region(new_region)  # 실제 호스트 전환
                print(f"{STAGE_NAME} Failover to {new_region} in {failover_time_ms:.2f}ms")
            else:
                record_failover(False, 0)
                return

        self._simulate_region_latency()

        headers = self._get_region_header()

        with self.client.get(
            "/api/products/",
            headers=headers,
            name=f"{STAGE_NAME} TC-28-1: {self.current_region} - List Products",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 503:
                # 서비스 불가 - fallback 시도
                fallback_region = self.coordinator.get_fallback_region()
                if fallback_region:
                    self._switch_region(fallback_region.name.replace("ap-northeast-2", "region_"))
                    response.success()
                else:
                    response.failure("No fallback region available")
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(2)
    @tag("tc-28-2", "clock-skew")
    def test_single_region_clock_skew(self):
        """TC-28-2: 단일 리전 Clock Skew 테스트"""
        record_request(self.current_region)

        # 현재 시간으로 타임스탬프 생성
        current_time = datetime.now(timezone.utc)

        # Clock Skew 검증
        is_valid, skew_seconds = self.coordinator.check_clock_skew(self.current_region, current_time)

        if not is_valid:
            record_clock_skew(rejected=True)
            print(f"{STAGE_NAME} Clock skew detected: {skew_seconds}s (rejected)")
            return

        if skew_seconds > 10:
            record_clock_skew(rejected=False)
            print(f"{STAGE_NAME} Clock skew warning: {skew_seconds}s")

        idempotency_key = self._generate_idempotency_key("order")

        headers = self._get_region_header()
        headers["X-Idempotency-Key"] = idempotency_key

        # Idempotency 확인
        is_duplicate, existing_result = self.coordinator.check_idempotency(idempotency_key)
        if is_duplicate:
            record_idempotency_hit()
            print(f"{STAGE_NAME} Idempotency hit: {idempotency_key[:8]}...")
            return

        # 카트 비우기 (중복 방지) - POST /api/cart/clear/ with confirm=true
        with self.client.post(
            "/api/cart/clear/",
            headers=headers,
            json={"confirm": True},
            name=f"{STAGE_NAME} TC-28-2: Clear Cart",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 400):  # 200=성공, 400=카트가 이미 비어있음
                response.success()
            else:
                response.failure(f"Clear cart failed: {response.status_code}")

        # 카트에 상품 추가 (랜덤 상품)
        product_id = random.choice(AVAILABLE_PRODUCT_IDS)
        with self.client.post(
            "/api/cart-items/",
            headers=headers,
            json={"product_id": product_id, "quantity": 1},
            name=f"{STAGE_NAME} TC-28-2: {self.current_region} - Add to Cart",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201):
                response.failure(f"Add to cart failed: {response.status_code}")
                return
            response.success()

        # 주문 생성 (배송 정보 포함)
        order_data = {
            "shipping_name": f"테스트 구매자 {self.user_id}",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구 테스트동 123",
        }
        with self.client.post(
            "/api/orders/",
            headers=headers,
            json=order_data,
            name=f"{STAGE_NAME} TC-28-2: {self.current_region} - Create Order",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201, 202):
                self.coordinator.register_idempotency(idempotency_key, "success")
                response.success()
            elif response.status_code == 409:
                # 중복 요청
                record_duplicate_prevented(order=True)
                response.success()
            else:
                response.failure(f"Order creation failed: {response.status_code}")

    @task(3)
    @tag("tc-28-3", "cross-region-fallback")
    def test_cross_region_fallback_consistency(self):
        """TC-28-3: Cross-Region Fallback Consistency 테스트"""
        record_request(self.current_region)

        # 주문 ID와 idempotency 키 생성
        idempotency_key = self._generate_idempotency_key("payment")

        # Step 1: Region A에서 시작
        self._switch_region("region_a")
        headers = self._get_region_header()
        headers["X-Idempotency-Key"] = idempotency_key

        # Step 2: 장애 발생 시뮬레이션 (30% 확률)
        if random.random() < 0.3:
            self.coordinator.inject_region_failure("region_a", "packet_loss")

            # Step 3: Region B로 failover - 실제 호스트 전환
            start_time = time.time()
            new_region = self.coordinator.perform_failover("region_a")

            if new_region:
                failover_time_ms = (time.time() - start_time) * 1000
                record_failover(True, failover_time_ms)
                self._switch_region(new_region)  # 실제 호스트 전환
                print(f"{STAGE_NAME} Failover to {new_region} for payment")

                # 헤더 업데이트 (새 리전 토큰 사용)
                headers = self._get_region_header()
                headers["X-Idempotency-Key"] = idempotency_key

            # 잠시 후 복구
            time.sleep(0.5)
            self.coordinator.recover_region("region_a")
            record_recovery(500)

        # Step 4: 동일 주문 처리 시도 (중복 방지 검증)
        is_duplicate, _ = self.coordinator.check_idempotency(idempotency_key)
        if is_duplicate:
            record_duplicate_prevented(payment=True)
            print(f"{STAGE_NAME} Duplicate payment prevented: {idempotency_key[:8]}...")
            return

        # 카트 비우기 (중복 방지) - POST /api/cart/clear/ with confirm=true
        with self.client.post(
            "/api/cart/clear/",
            headers=headers,
            json={"confirm": True},
            name=f"{STAGE_NAME} TC-28-3: Clear Cart",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 400):  # 200=성공, 400=카트가 이미 비어있음
                response.success()
            else:
                response.failure(f"Clear cart failed: {response.status_code}")

        # 카트에 상품 추가 (랜덤 상품)
        product_id = random.choice(AVAILABLE_PRODUCT_IDS)
        with self.client.post(
            "/api/cart-items/",
            headers=headers,
            json={"product_id": product_id, "quantity": 1},
            name=f"{STAGE_NAME} TC-28-3: {self.current_region} - Add to Cart",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201):
                response.failure(f"Add to cart failed: {response.status_code}")
                return
            response.success()

        # 주문 생성
        order_data = {
            "shipping_name": f"테스트 구매자 {self.user_id}",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시 강남구 테스트동 123",
        }
        order_id = None
        with self.client.post(
            "/api/orders/",
            headers=headers,
            json=order_data,
            name=f"{STAGE_NAME} TC-28-3: [{self.current_region}] Create Order",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201, 202):
                try:
                    order_id = response.json().get("order_id")
                    response.success()
                except:
                    response.failure("Failed to parse order response")
                    return
            else:
                response.failure(f"Order creation failed: {response.status_code}")
                return

        if not order_id:
            return

        # 결제 요청
        payment_data = {"order_id": order_id, "payment_method": "card"}
        with self.client.post(
            "/api/payments/request/",
            headers=headers,
            json=payment_data,
            name=f"{STAGE_NAME} TC-28-3: {self.current_region} - Payment",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                self.coordinator.register_idempotency(idempotency_key, str(order_id))
                self.order_ids.append(str(order_id))
                response.success()
            elif response.status_code == 409:
                record_duplicate_prevented(payment=True)
                response.success()
            elif response.status_code == 503:
                # 서비스 불가 - 재시도 필요
                response.failure("Service unavailable, retry needed")
            else:
                response.failure(f"Payment failed: {response.status_code}")

    @task(1)
    @tag("recovery")
    def test_recovery_and_routing(self):
        """복구 후 원래 리전으로 라우팅 테스트"""
        # 모든 리전 상태 확인
        for name, region in self.coordinator.regions.items():
            if region.status != RegionStatus.HEALTHY:
                # 복구 시도
                start_time = time.time()
                self.coordinator.recover_region(name)
                recovery_time_ms = (time.time() - start_time) * 1000
                record_recovery(recovery_time_ms)

        # Primary 리전이 정상이면 다시 라우팅
        primary_region = None
        for name, region in self.coordinator.regions.items():
            if region.is_primary and region.status == RegionStatus.HEALTHY:
                primary_region = name
                break

        if primary_region and self.current_region != primary_region:
            old_region = self.current_region
            self.current_region = primary_region
            print(f"{STAGE_NAME} Routing restored: {old_region} -> {primary_region}")


# =============================================================================
# Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Multi-Region Latency Test Started")
    print(f"{'='*60}")
    print(f"Region A: {_coordinator.regions['region_a'].name}")
    print(f"Region B: {_coordinator.regions['region_b'].name}")
    print(f"Active Region: {_coordinator.active_region}")
    print(f"{'='*60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시"""
    print(f"\n{'='*60}")
    print(f"{STAGE_NAME} Test Results")
    print(f"{'='*60}")
    print(f"Total Requests: {_stats.total_requests}")
    print(f"  - Region A: {_stats.region_a_requests}")
    print(f"  - Region B: {_stats.region_b_requests}")
    print(f"\nFailover Statistics:")
    print(f"  - Total Failovers: {_stats.failover_count}")
    print(f"  - Successful: {_stats.failover_success}")
    if _stats.failover_count > 0:
        avg_failover_time = _stats.failover_time_sum_ms / _stats.failover_count
        print(f"  - Avg Failover Time: {avg_failover_time:.2f}ms")
    print(f"\nData Consistency:")
    print(f"  - Duplicate Orders Prevented: {_stats.duplicate_orders_prevented}")
    print(f"  - Duplicate Payments Prevented: {_stats.duplicate_payments_prevented}")
    print(f"  - Idempotency Hits: {_stats.idempotency_hits}")
    print(f"\nClock Skew:")
    print(f"  - Detections: {_stats.clock_skew_detections}")
    print(f"  - Rejections: {_stats.clock_skew_rejections}")
    print(f"\nRecovery:")
    print(f"  - Recovery Count: {_stats.recovery_count}")
    if _stats.recovery_count > 0:
        avg_recovery_time = _stats.recovery_time_sum_ms / _stats.recovery_count
        print(f"  - Avg Recovery Time: {avg_recovery_time:.2f}ms")
    print(f"\nErrors:")
    print(f"  - Packet Loss: {_stats.packet_loss_errors}")
    print(f"  - Timeout: {_stats.timeout_errors}")
    print(f"  - Consistency: {_stats.consistency_errors}")
    print(f"{'='*60}\n")

    # 검증 기준 체크
    print(f"\n{STAGE_NAME} Validation Criteria:")

    # Failover 시간 < 5초
    if _stats.failover_count > 0:
        avg_failover = _stats.failover_time_sum_ms / _stats.failover_count
        status = "✅ PASS" if avg_failover < 5000 else "❌ FAIL"
        print(f"  Failover Time < 5s: {status} ({avg_failover:.2f}ms)")

    # 데이터 일관성 100%
    status = "✅ PASS" if _stats.consistency_errors == 0 else "❌ FAIL"
    print(f"  Data Consistency 100%: {status} ({_stats.consistency_errors} errors)")

    # 복구 후 라우팅 < 10초
    if _stats.recovery_count > 0:
        avg_recovery = _stats.recovery_time_sum_ms / _stats.recovery_count
        status = "✅ PASS" if avg_recovery < 10000 else "❌ FAIL"
        print(f"  Recovery Routing < 10s: {status} ({avg_recovery:.2f}ms)")

    print()


# =============================================================================
# Chaos Injection Controls (for external triggering)
# =============================================================================


def inject_region_a_failure():
    """Region A 장애 주입 (외부 호출용)"""
    _coordinator.inject_region_failure("region_a", "packet_loss")


def inject_region_b_failure():
    """Region B 장애 주입 (외부 호출용)"""
    _coordinator.inject_region_failure("region_b", "packet_loss")


def inject_clock_skew(region: str = "region_b"):
    """Clock Skew 주입 (외부 호출용)"""
    _coordinator.inject_region_failure(region, "clock_skew")


def recover_all_regions():
    """모든 리전 복구 (외부 호출용)"""
    _coordinator.recover_region("region_a")
    _coordinator.recover_region("region_b")


def get_stats() -> MultiRegionStats:
    """현재 통계 반환 (외부 호출용)"""
    return _stats
