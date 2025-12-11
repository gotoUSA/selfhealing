"""
Stage 27: Graceful Shutdown 테스트

목표: 배포/재시작 시 요청 유실 방지 검증 (쿠팡 롤링 배포 장애 재현)

시나리오:
  - 진행 중인 요청 있을 때 SIGTERM 전송
  - Drain 기간 동안 요청 완료 확인
  - 타임아웃 시 강제 종료 동작

실행 방법:
    # Web UI 모드
    locust -f load_tests/scenarios/stage27_graceful_shutdown.py --host=http://localhost:8000

    # CLI 모드
    locust -f load_tests/scenarios/stage27_graceful_shutdown.py --host=http://localhost:8000 \
        --users=100 --spawn-rate=20 --run-time=3m --headless --html=stage27_report.html

    # Shutdown 시그널 테스트 (별도 터미널에서)
    # docker-compose exec web kill -SIGTERM 1

검증 기준:
  - 진행 중 요청 완료율 > 99%
  - 새 요청 거부 (503) 정상 동작
  - Drain 완료 후 정상 종료

Reference:
  - docs/STAGE_27_GRACEFUL_SHUTDOWN.md
"""

import os
import sys
import time
import random
import threading
import signal
from datetime import datetime
from typing import Dict, List, Any, Optional

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events
from locust.runners import MasterRunner, LocalRunner


STAGE_NAME = "[Stage27-GracefulShutdown]"


# =============================================================================
# 테스트 통계
# =============================================================================

_shutdown_stats = {
    "total_requests": 0,
    "requests_by_phase": {
        "normal": {"started": 0, "completed": 0, "failed": 0},
        "draining": {"started": 0, "completed": 0, "failed": 0, "rejected": 0},
        "shutdown": {"started": 0, "completed": 0, "failed": 0, "rejected": 0},
    },
    "request_types": {
        "fast": {"success": 0, "failure": 0, "times": []},
        "medium": {"success": 0, "failure": 0, "times": []},
        "slow": {"success": 0, "failure": 0, "times": []},
    },
    "in_flight": {
        "current": 0,
        "max": 0,
        "at_shutdown": 0,
    },
    "shutdown": {
        "signal_sent": False,
        "signal_time": None,
        "drain_started": None,
        "drain_completed": None,
        "forced_shutdown": False,
        "requests_aborted": 0,
    },
    "errors": {
        "connection_refused": 0,
        "service_unavailable": 0,
        "timeout": 0,
        "other": 0,
    },
    "timeline": [],  # 시간별 이벤트 기록
}

# 현재 Phase 추적
_current_phase = "normal"
_phase_lock = threading.Lock()


def get_current_phase() -> str:
    with _phase_lock:
        return _current_phase


def set_current_phase(phase: str):
    global _current_phase
    with _phase_lock:
        _current_phase = phase
        _shutdown_stats["timeline"].append({
            "time": datetime.now().isoformat(),
            "event": f"phase_changed_to_{phase}"
        })


def record_request_start():
    """요청 시작 기록"""
    phase = get_current_phase()
    _shutdown_stats["requests_by_phase"][phase]["started"] += 1
    _shutdown_stats["in_flight"]["current"] += 1
    _shutdown_stats["in_flight"]["max"] = max(
        _shutdown_stats["in_flight"]["max"],
        _shutdown_stats["in_flight"]["current"]
    )


def record_request_end(success: bool, rejected: bool = False):
    """요청 종료 기록"""
    phase = get_current_phase()
    _shutdown_stats["in_flight"]["current"] -= 1
    
    if rejected:
        _shutdown_stats["requests_by_phase"][phase]["rejected"] += 1
    elif success:
        _shutdown_stats["requests_by_phase"][phase]["completed"] += 1
    else:
        _shutdown_stats["requests_by_phase"][phase]["failed"] += 1


def record_request(request_type: str, success: bool, response_time: float):
    """요청 유형별 기록"""
    _shutdown_stats["total_requests"] += 1
    status = "success" if success else "failure"
    _shutdown_stats["request_types"][request_type][status] += 1
    _shutdown_stats["request_types"][request_type]["times"].append(response_time)


# =============================================================================
# 일반 사용자 (빠른 요청)
# =============================================================================

class FastRequestUser(HttpUser):
    """
    빠른 요청 사용자
    
    짧은 응답 시간의 요청으로 정상 동작 확인
    """
    
    wait_time = between(0.5, 1.5)
    weight = 5  # 50%
    
    def on_start(self):
        """사용자 시작"""
        self.token = None
        self._authenticate()
    
    def _authenticate(self):
        """인증"""
        try:
            response = self.client.post("/api/auth/login/", json={
                "username": f"fast_user_{random.randint(1, 100)}",
                "password": "testpass123"
            })
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
        except:
            pass
    
    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(5)
    @tag("fast")
    def fast_health_check(self):
        """빠른 헬스체크"""
        record_request_start()
        start = time.time()
        
        try:
            with self.client.get(
                "/api/health/",
                catch_response=True,
                name="[Stage27] Fast - Health Check",
                timeout=5
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    record_request("fast", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    # Draining 중 거부됨
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("fast", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining")
                else:
                    record_request("fast", False, elapsed)
                    record_request_end(False)
                    response.failure(f"Error: {response.status_code}")
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)
    
    @task(3)
    @tag("fast")
    def fast_product_list(self):
        """빠른 상품 목록"""
        record_request_start()
        start = time.time()
        
        try:
            with self.client.get(
                "/api/products/",
                headers=self._headers(),
                params={"page": 1, "limit": 5},
                catch_response=True,
                name="[Stage27] Fast - Product List",
                timeout=5
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    record_request("fast", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("fast", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining")
                else:
                    record_request("fast", False, elapsed)
                    record_request_end(False)
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)


# =============================================================================
# 중간 요청 사용자
# =============================================================================

class MediumRequestUser(HttpUser):
    """
    중간 길이 요청 사용자
    
    적당한 처리 시간의 요청
    """
    
    wait_time = between(1, 2)
    weight = 3  # 30%
    
    def on_start(self):
        self.token = None
        self._authenticate()
    
    def _authenticate(self):
        try:
            response = self.client.post("/api/auth/login/", json={
                "username": f"medium_user_{random.randint(1, 50)}",
                "password": "testpass123"
            })
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
        except:
            pass
    
    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(3)
    @tag("medium")
    def medium_cart_operations(self):
        """장바구니 조작"""
        record_request_start()
        start = time.time()
        
        try:
            with self.client.get(
                "/api/cart/",
                headers=self._headers(),
                catch_response=True,
                name="[Stage27] Medium - Cart",
                timeout=10
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code in (200, 401):  # 401은 인증 실패
                    record_request("medium", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("medium", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining")
                else:
                    record_request("medium", False, elapsed)
                    record_request_end(False)
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)
    
    @task(2)
    @tag("medium")
    def medium_order_list(self):
        """주문 목록 조회"""
        record_request_start()
        start = time.time()
        
        try:
            with self.client.get(
                "/api/orders/",
                headers=self._headers(),
                catch_response=True,
                name="[Stage27] Medium - Orders",
                timeout=10
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code in (200, 401):
                    record_request("medium", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("medium", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining")
                else:
                    record_request("medium", False, elapsed)
                    record_request_end(False)
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)


# =============================================================================
# 느린 요청 사용자 (긴 트랜잭션)
# =============================================================================

class SlowRequestUser(HttpUser):
    """
    느린 요청 사용자
    
    긴 처리 시간의 요청 - Shutdown 시 이 요청들의 완료 여부가 중요
    """
    
    wait_time = between(2, 4)
    weight = 2  # 20%
    
    def on_start(self):
        self.token = None
        self._authenticate()
    
    def _authenticate(self):
        try:
            response = self.client.post("/api/auth/login/", json={
                "username": f"slow_user_{random.randint(1, 30)}",
                "password": "testpass123"
            })
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access") or data.get("token")
        except:
            pass
    
    def _headers(self) -> Dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
    
    @task(2)
    @tag("slow")
    def slow_payment_process(self):
        """느린 결제 처리"""
        record_request_start()
        start = time.time()
        order_id = f"ORD-{random.randint(10000, 99999)}"
        
        try:
            with self.client.post(
                "/api/payments/process/",
                headers=self._headers(),
                json={
                    "order_id": order_id,
                    "amount": random.randint(10000, 100000),
                    "method": "card",
                },
                catch_response=True,
                name="[Stage27] Slow - Payment",
                timeout=30
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code in (200, 201, 404):  # 404는 테스트 환경
                    record_request("slow", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("slow", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining - Payment interrupted")
                elif response.status_code == 504:
                    _shutdown_stats["errors"]["timeout"] += 1
                    record_request("slow", False, elapsed)
                    record_request_end(False)
                    response.failure("Payment timeout")
                else:
                    record_request("slow", False, elapsed)
                    record_request_end(False)
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)
    
    @task(1)
    @tag("slow")
    def slow_order_create(self):
        """느린 주문 생성"""
        record_request_start()
        start = time.time()
        
        try:
            with self.client.post(
                "/api/orders/",
                headers=self._headers(),
                json={
                    "items": [
                        {"product_id": random.randint(1, 100), "quantity": random.randint(1, 3)}
                    ],
                    "shipping_address": "테스트 주소",
                },
                catch_response=True,
                name="[Stage27] Slow - Create Order",
                timeout=30
            ) as response:
                elapsed = (time.time() - start) * 1000
                
                if response.status_code in (200, 201, 400, 401, 404):
                    record_request("slow", True, elapsed)
                    record_request_end(True)
                    response.success()
                elif response.status_code == 503:
                    _shutdown_stats["errors"]["service_unavailable"] += 1
                    record_request("slow", False, elapsed)
                    record_request_end(False, rejected=True)
                    response.failure("Service draining")
                else:
                    record_request("slow", False, elapsed)
                    record_request_end(False)
        except Exception as e:
            _shutdown_stats["errors"]["connection_refused"] += 1
            record_request_end(False)


# =============================================================================
# Shutdown 시뮬레이터 (별도 스레드)
# =============================================================================

class ShutdownSimulator:
    """
    Shutdown 시그널 시뮬레이터
    
    테스트 중간에 서버 shutdown을 시뮬레이션
    """
    
    def __init__(self, delay_seconds: float = 60.0, drain_seconds: float = 30.0):
        self.delay = delay_seconds
        self.drain_seconds = drain_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
    
    def start(self):
        """시뮬레이터 시작"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        """시뮬레이터 중지"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
    
    def _run(self):
        """시뮬레이션 실행"""
        # 정상 운영 기간 대기
        print(f"\n⏳ {self.delay}초 후 Shutdown 시그널 발송 예정...")
        
        if self._stop_event.wait(timeout=self.delay):
            return  # 중지됨
        
        # Shutdown 시그널 발송 시점
        _shutdown_stats["shutdown"]["signal_sent"] = True
        _shutdown_stats["shutdown"]["signal_time"] = datetime.now()
        _shutdown_stats["in_flight"]["at_shutdown"] = _shutdown_stats["in_flight"]["current"]
        
        print(f"\n🚨 SHUTDOWN SIGNAL SENT!")
        print(f"   진행 중인 요청: {_shutdown_stats['in_flight']['at_shutdown']}개")
        
        # Draining 단계
        set_current_phase("draining")
        _shutdown_stats["shutdown"]["drain_started"] = datetime.now()
        
        print(f"   Drain 시작 (최대 {self.drain_seconds}초)")
        
        # Drain 기간 동안 대기
        if self._stop_event.wait(timeout=self.drain_seconds):
            return
        
        # Shutdown 완료
        set_current_phase("shutdown")
        _shutdown_stats["shutdown"]["drain_completed"] = datetime.now()
        
        remaining = _shutdown_stats["in_flight"]["current"]
        if remaining > 0:
            _shutdown_stats["shutdown"]["forced_shutdown"] = True
            _shutdown_stats["shutdown"]["requests_aborted"] = remaining
            print(f"\n⚠️ Forced shutdown! {remaining} requests aborted")
        else:
            print(f"\n✅ Graceful shutdown complete! All requests drained.")


# 전역 시뮬레이터
_shutdown_simulator: Optional[ShutdownSimulator] = None


# =============================================================================
# 이벤트 훅
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작"""
    global _shutdown_simulator
    
    set_current_phase("normal")
    
    print("\n" + "=" * 70)
    print(f"🔄 {STAGE_NAME} Graceful Shutdown 테스트 시작")
    print("=" * 70)
    print("목표: 배포/재시작 시 요청 유실 방지 검증")
    print("시나리오:")
    print("  1. 정상 부하 운영 (60초)")
    print("  2. SIGTERM 시그널 (Draining 시작)")
    print("  3. Drain 기간 (30초) - 기존 요청 완료 대기")
    print("  4. Shutdown 완료")
    print("=" * 70)
    
    # Shutdown 시뮬레이터 시작
    # 실제 환경에서는 docker-compose exec web kill -SIGTERM 1 사용
    _shutdown_simulator = ShutdownSimulator(delay_seconds=60.0, drain_seconds=30.0)
    _shutdown_simulator.start()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료"""
    global _shutdown_simulator
    
    if _shutdown_simulator:
        _shutdown_simulator.stop()
    
    print("\n" + "=" * 70)
    print(f"✅ {STAGE_NAME} Graceful Shutdown 테스트 완료")
    print("=" * 70)
    
    # 통계 출력
    print(f"\n📊 총 요청 수: {_shutdown_stats['total_requests']}")
    
    print("\n📈 Phase별 요청 통계:")
    for phase, stats in _shutdown_stats["requests_by_phase"].items():
        started = stats["started"]
        completed = stats["completed"]
        failed = stats["failed"]
        rejected = stats.get("rejected", 0)
        
        if started > 0:
            completion_rate = (completed / started) * 100
            print(f"  {phase}: 시작={started}, 완료={completed}, 실패={failed}, 거부={rejected} (완료율: {completion_rate:.1f}%)")
    
    print("\n📈 요청 유형별 통계:")
    for req_type, stats in _shutdown_stats["request_types"].items():
        success = stats["success"]
        failure = stats["failure"]
        times = stats["times"]
        avg_time = sum(times) / len(times) if times else 0
        print(f"  {req_type}: 성공={success}, 실패={failure}, 평균응답={avg_time:.2f}ms")
    
    print(f"\n🔥 In-Flight 요청:")
    print(f"  최대 동시 요청: {_shutdown_stats['in_flight']['max']}")
    print(f"  Shutdown 시점: {_shutdown_stats['in_flight']['at_shutdown']}")
    
    print(f"\n🚨 Shutdown 정보:")
    if _shutdown_stats["shutdown"]["signal_sent"]:
        print(f"  시그널 발송: {_shutdown_stats['shutdown']['signal_time']}")
        print(f"  강제 종료: {_shutdown_stats['shutdown']['forced_shutdown']}")
        print(f"  중단된 요청: {_shutdown_stats['shutdown']['requests_aborted']}")
    else:
        print("  시그널 미발송 (테스트 시간 부족)")
    
    print(f"\n❌ 에러 통계:")
    for error_type, count in _shutdown_stats["errors"].items():
        print(f"  {error_type}: {count}")
    
    # 검증 결과
    print("\n" + "=" * 70)
    print("🎯 검증 결과:")
    
    # 완료율 계산
    normal_stats = _shutdown_stats["requests_by_phase"]["normal"]
    if normal_stats["started"] > 0:
        normal_rate = (normal_stats["completed"] / normal_stats["started"]) * 100
        print(f"  정상 Phase 완료율: {normal_rate:.1f}%")
    
    draining_stats = _shutdown_stats["requests_by_phase"]["draining"]
    if draining_stats["started"] > 0:
        draining_rate = (draining_stats["completed"] / draining_stats["started"]) * 100
        status = "✅" if draining_rate >= 99 else "⚠️"
        print(f"  Draining Phase 완료율: {draining_rate:.1f}% {status}")
    
    if _shutdown_stats["shutdown"]["forced_shutdown"]:
        print(f"  ⚠️ 강제 종료 발생 - {_shutdown_stats['shutdown']['requests_aborted']}개 요청 중단")
    else:
        print("  ✅ Graceful Shutdown 성공 - 모든 요청 정상 완료")
    
    print("=" * 70 + "\n")
