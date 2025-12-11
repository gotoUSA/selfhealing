"""
Stage 26: EXTREME Connection Pool 고갈 테스트 🔥

목표: DB Connection Pool을 **완전히 고갈**시키고 Self-Healing 복구 검증

시나리오:
  - Pool 크기: 3개 (docker-compose.extreme-pool.yml)
  - 동시 사용자: 300명
  - 5초/10초 연결 점유 쿼리로 Pool 고갈 유도
  - Pool Watchdog 자동 복구 검증

실행 방법:
    docker compose -f docker-compose.extreme-pool.yml up --build

검증 기준:
  - Pool 고갈 발생 (503 에러)
  - Pool Watchdog 감지 및 복구
  - 복구 후 정상 요청 처리

Reference:
  - docs/STAGE_26_CONNECTION_POOL.md
"""

import os
import sys
import time
import random
import threading
import socket
from datetime import datetime
from typing import Dict, List, Any

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape, constant


# =============================================================================
# 🔥🔥🔥 핵심: 짧은 타임아웃으로 빠른 실패 감지
# =============================================================================
socket.setdefaulttimeout(10)  # 10초 타임아웃


class PatchedHttpUser(HttpUser):
    """
    Pool 고갈 테스트용 HttpUser.
    짧은 타임아웃으로 서버가 응답하지 않으면 빠르게 실패 처리.
    """
    abstract = True  # Locust가 직접 인스턴스화하지 않음
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 10초 타임아웃 - 서버가 응답 안 하면 빠르게 실패
        self.client.timeout = 10
        # Keep-alive 활성화
        self.client.headers.update({"Connection": "keep-alive"})


STAGE_NAME = "[Stage26-EXTREME]"


# =============================================================================
# Pool 고갈 감지 헬퍼 함수
# =============================================================================
def is_pool_exhausted_response(response) -> bool:
    """503 + 특정 키워드로 Pool 고갈 판단"""
    if response.status_code == 503:
        try:
            text = response.text.lower()
            if any(keyword in text for keyword in ["pool", "connection", "exhausted", "timeout", "circuit"]):
                print(f"🚨 Pool 고갈 감지! (503) - {time.time():.0f}")
                return True
        except:
            pass
        return True  # 503은 모두 Pool 고갈로 간주
    return False


# =============================================================================
# 테스트 통계
# =============================================================================

_extreme_stats = {
    "total_requests": 0,
    "pool_exhausted_count": 0,
    "pool_exhausted_times": [],
    "recovery_detected": False,
    "recovery_time": None,
    "requests_by_type": {
        "slow_5s": {"success": 0, "failure": 0, "times": []},
        "slow_10s": {"success": 0, "failure": 0, "times": []},
        "heavy_query": {"success": 0, "failure": 0, "times": []},
        "connection_holder": {"success": 0, "failure": 0, "times": []},
        "health_check": {"success": 0, "failure": 0, "times": []},
        "pool_status": {"success": 0, "failure": 0, "times": []},
        "normal_query": {"success": 0, "failure": 0, "times": []},
        "cb_status": {"success": 0, "failure": 0, "times": []},  # Circuit Breaker 상태 조회
    },
    "phase": "normal",  # normal -> exhaustion -> recovery
    "exhaustion_start": None,
    "exhaustion_end": None,
    # Circuit Breaker 추적
    "circuit_breaker": {
        "503_count": 0,  # Circuit Breaker가 거부한 요청 수
        "open_detected": False,
        "half_open_detected": False,
        "recovery_detected": False,
        "state_history": [],
    },
}

_stats_lock = threading.Lock()


def record_request(req_type: str, success: bool, elapsed_ms: float):
    """요청 통계 기록"""
    with _stats_lock:
        _extreme_stats["total_requests"] += 1
        status = "success" if success else "failure"
        _extreme_stats["requests_by_type"][req_type][status] += 1
        _extreme_stats["requests_by_type"][req_type]["times"].append(elapsed_ms)


def record_pool_exhaustion(reason: str = "503"):
    """Pool 고갈 감지 기록"""
    with _stats_lock:
        _extreme_stats["pool_exhausted_count"] += 1
        _extreme_stats["pool_exhausted_times"].append(time.time())
        
        if _extreme_stats["phase"] == "normal":
            _extreme_stats["phase"] = "exhaustion"
            _extreme_stats["exhaustion_start"] = time.time()
            print(f"\n🚨 {STAGE_NAME} POOL EXHAUSTION DETECTED! Reason: {reason}, Phase: exhaustion\n")


def record_recovery():
    """Pool 복구 감지 기록"""
    with _stats_lock:
        if _extreme_stats["phase"] == "exhaustion" and not _extreme_stats["recovery_detected"]:
            _extreme_stats["recovery_detected"] = True
            _extreme_stats["recovery_time"] = time.time()
            _extreme_stats["exhaustion_end"] = time.time()
            _extreme_stats["phase"] = "recovery"
            
            duration = _extreme_stats["exhaustion_end"] - _extreme_stats["exhaustion_start"]
            print(f"\n✅ {STAGE_NAME} POOL RECOVERY DETECTED! Duration: {duration:.2f}s\n")


# =============================================================================
# 테스트 이벤트 핸들러
# =============================================================================

# Connection 실패/성공 카운터
_connection_failures = {"count": 0, "threshold": 10}  # 10번 연속 실패 시 고갈 판정
_recovery_success = {"count": 0, "threshold": 3}  # 3번 누적 성공 시 복구 판정 (연속 아님)
_in_exhaustion_state = {"value": False}  # 고갈 상태 추적 (Worker 독립)


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, 
               context, exception, start_time, url, **kwargs):
    """모든 요청에 대한 이벤트 핸들러 - Connection 실패/복구 감지 + Circuit Breaker 감지"""
    global _connection_failures, _recovery_success, _in_exhaustion_state
    
    if exception is not None:
        # Connection 실패 (Error: 0) 감지
        with _stats_lock:
            _connection_failures["count"] += 1
            # 성공 카운터는 리셋하지 않음 - 누적 방식
            
            # 연속 실패가 임계값 초과 시 Pool 고갈로 판정
            if _connection_failures["count"] >= _connection_failures["threshold"]:
                if not _in_exhaustion_state["value"]:
                    _in_exhaustion_state["value"] = True
                    record_pool_exhaustion(reason=f"ConnectionError ({_connection_failures['count']} failures)")
    else:
        # 응답 받음
        with _stats_lock:
            # Circuit Breaker 503 응답 감지
            if response is not None and hasattr(response, 'status_code'):
                if response.status_code == 503:
                    _extreme_stats["circuit_breaker"]["503_count"] += 1
                    
                    # 응답 본문에서 Circuit Breaker 상태 확인
                    try:
                        data = response.json()
                        circuit_state = data.get("circuit_state", "")
                        
                        if circuit_state == "OPEN":
                            if not _extreme_stats["circuit_breaker"]["open_detected"]:
                                _extreme_stats["circuit_breaker"]["open_detected"] = True
                                _extreme_stats["circuit_breaker"]["state_history"].append(
                                    {"state": "OPEN", "time": time.time()}
                                )
                                print(f"\n🔴 {STAGE_NAME} CIRCUIT BREAKER OPEN! Fail Fast enabled!\n")
                        
                        elif circuit_state == "HALF_OPEN":
                            if not _extreme_stats["circuit_breaker"]["half_open_detected"]:
                                _extreme_stats["circuit_breaker"]["half_open_detected"] = True
                                _extreme_stats["circuit_breaker"]["state_history"].append(
                                    {"state": "HALF_OPEN", "time": time.time()}
                                )
                                print(f"\n🟡 {STAGE_NAME} CIRCUIT BREAKER HALF_OPEN! Testing recovery...\n")
                    except:
                        pass
                    
                    # 503은 실패로 처리하되 Pool 고갈로 기록
                    if not _in_exhaustion_state["value"]:
                        _in_exhaustion_state["value"] = True
                        record_pool_exhaustion(reason="503 Circuit Breaker")
                    return  # 503은 여기서 종료
                
                elif response.status_code < 500:
                    # 성공!
                    _recovery_success["count"] += 1
                    _connection_failures["count"] = 0
                    
                    # Circuit Breaker 복구 감지
                    if _extreme_stats["circuit_breaker"]["open_detected"]:
                        if not _extreme_stats["circuit_breaker"]["recovery_detected"]:
                            _extreme_stats["circuit_breaker"]["recovery_detected"] = True
                            _extreme_stats["circuit_breaker"]["state_history"].append(
                                {"state": "CLOSED", "time": time.time()}
                            )
                            print(f"\n🟢 {STAGE_NAME} CIRCUIT BREAKER RECOVERED! System back to normal!\n")
                    
                    # 고갈 상태에서 누적 성공 시 복구로 판정
                    if _in_exhaustion_state["value"]:
                        if _recovery_success["count"] >= _recovery_success["threshold"]:
                            record_recovery()
                            _in_exhaustion_state["value"] = False


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력"""
    print("\n" + "=" * 70)
    print(f"🔥 {STAGE_NAME} EXTREME Pool 고갈 테스트 완료")
    print("=" * 70)
    
    print(f"\n📊 총 요청 수: {_extreme_stats['total_requests']}")
    print(f"🚨 Pool 고갈 감지 횟수: {_extreme_stats['pool_exhausted_count']}")
    print(f"🔌 Connection 실패 횟수: {_connection_failures['count']}")
    print(f"✅ 연속 성공 횟수: {_recovery_success['count']}")
    
    # Circuit Breaker 통계
    cb_stats = _extreme_stats["circuit_breaker"]
    print(f"\n🛡️ Circuit Breaker 통계:")
    print(f"  - 503 거부 횟수: {cb_stats['503_count']}")
    print(f"  - OPEN 감지: {'✅ Yes' if cb_stats['open_detected'] else '❌ No'}")
    print(f"  - HALF_OPEN 감지: {'✅ Yes' if cb_stats['half_open_detected'] else '❌ No'}")
    print(f"  - 복구(CLOSED) 감지: {'✅ Yes' if cb_stats['recovery_detected'] else '❌ No'}")
    if cb_stats["state_history"]:
        print(f"  - 상태 변경 이력: {len(cb_stats['state_history'])}건")
    
    print(f"\n📈 요청 유형별 통계:")
    for req_type, stats in _extreme_stats["requests_by_type"].items():
        total = stats["success"] + stats["failure"]
        if total > 0:
            fail_rate = (stats["failure"] / total) * 100
            avg_time = sum(stats["times"]) / len(stats["times"]) if stats["times"] else 0
            print(f"  {req_type}: 성공={stats['success']}, 실패={stats['failure']} ({fail_rate:.1f}%), 평균={avg_time:.0f}ms")
    
    print(f"\n🔄 Phase 진행:")
    print(f"  - 고갈 시작: {_extreme_stats['exhaustion_start']}")
    print(f"  - 고갈 종료: {_extreme_stats['exhaustion_end']}")
    print(f"  - 복구 감지: {_extreme_stats['recovery_detected']}")
    
    if _extreme_stats["exhaustion_start"] and _extreme_stats["exhaustion_end"]:
        duration = _extreme_stats["exhaustion_end"] - _extreme_stats["exhaustion_start"]
        print(f"  - 고갈 지속 시간: {duration:.2f}초")
    
    print("\n" + "=" * 70)
    
    # 검증 결과
    print("🎯 검증 결과:")
    
    # Pool 고갈 판정: 503 응답 또는 대량 Connection 실패
    pool_exhausted = (
        _extreme_stats["pool_exhausted_count"] > 0 or 
        _connection_failures["count"] >= _connection_failures["threshold"]
    )
    
    if pool_exhausted:
        print("  ✅ Pool 고갈 발생 확인")
        if _extreme_stats["pool_exhausted_count"] > 0:
            print(f"     - 503 응답: {_extreme_stats['pool_exhausted_count']}회")
        if _connection_failures["count"] >= _connection_failures["threshold"]:
            print(f"     - Connection 실패: {_connection_failures['count']}회 (임계값: {_connection_failures['threshold']})")
    else:
        print("  ❌ Pool 고갈 미발생 (부하 부족)")
    
    # Circuit Breaker 검증
    cb_stats = _extreme_stats["circuit_breaker"]
    if cb_stats["open_detected"]:
        print("  ✅ Circuit Breaker OPEN 감지 (Fail Fast 작동!)")
        if cb_stats["recovery_detected"]:
            print("  ✅ Circuit Breaker RECOVERED (자동 복구 성공!)")
        elif cb_stats["half_open_detected"]:
            print("  🟡 Circuit Breaker HALF_OPEN (복구 테스트 중)")
        else:
            print("  ⚠️ Circuit Breaker 복구 미완료")
    else:
        if pool_exhausted:
            print("  ⚠️ Circuit Breaker 미발동 (Fail Fast 미작동)")
    
    if _extreme_stats["recovery_detected"]:
        print("  ✅ Pool 복구 감지 성공")
    else:
        if pool_exhausted:
            print("  ⚠️ Pool 복구 미감지 (테스트 시간 부족 또는 완전 고장)")
        else:
            print("  - Pool 복구 테스트 해당 없음")
    
    # 최종 판정
    print("\n" + "=" * 70)
    if cb_stats["open_detected"] and cb_stats["recovery_detected"]:
        print("🎉 PASS: Circuit Breaker가 Pool 고갈을 감지하고 자동 복구 완료!")
    elif cb_stats["open_detected"]:
        print("⚠️ PARTIAL: Circuit Breaker 발동했지만 완전 복구 미확인")
    elif pool_exhausted:
        print("❌ FAIL: Pool 고갈 발생했지만 Circuit Breaker 미발동!")
    else:
        print("❓ INCONCLUSIVE: 부하가 부족하여 Pool 고갈 미발생")
    
    print("=" * 70 + "\n")


# =============================================================================
# Pool Killer User (연결을 오래 점유)
# =============================================================================

class PoolKillerUser(PatchedHttpUser):
    """
    Pool 고갈을 유도하는 사용자.
    
    5초, 10초 느린 쿼리로 연결을 오래 점유합니다.
    """
    
    wait_time = constant(0)  # 휴식 없이 계속 요청!
    weight = 7  # 70% - Pool Killer 비중 높임
    
    @task(3)
    @tag("pool_killer")
    def slow_query_5s(self):
        """5초 동안 연결 점유"""
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/stress/slow-5s/",
            catch_response=True,
            name=f"{STAGE_NAME} Slow Query 5s",
            timeout=15  # 5분 대기 - Pool 고갈 대기 허용
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("slow_5s", True, elapsed)
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("slow_5s", False, elapsed)
                response.failure("Pool exhausted!")
            else:
                record_request("slow_5s", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(2)
    @tag("pool_killer")
    def slow_query_10s(self):
        """10초 동안 연결 점유 (더 극단적)"""
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/stress/slow-10s/",
            catch_response=True,
            name=f"{STAGE_NAME} Slow Query 10s",
            timeout=15  # 5분 대기 - Pool 고갈 대기 허용
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("slow_10s", True, elapsed)
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("slow_10s", False, elapsed)
                response.failure("Pool exhausted!")
            else:
                record_request("slow_10s", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(2)
    @tag("pool_killer")
    def heavy_query(self):
        """무거운 집계 쿼리"""
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/stress/heavy-query/",
            catch_response=True,
            name=f"{STAGE_NAME} Heavy Query",
            timeout=15  # 5분 대기
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("heavy_query", True, elapsed)
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("heavy_query", False, elapsed)
                response.failure("Pool exhausted!")
            else:
                record_request("heavy_query", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(5)
    @tag("pool_killer")
    def hold_connection_long(self):
        """🔥 Slow Query로 연결 장시간 점유 후 반환 (회복 가능)"""
        start = time.time()
        
        # 무작위로 5초 또는 10초 slow query 선택
        endpoint = random.choice([
            "/api/self-healing/stress/slow-5s/",
            "/api/self-healing/stress/slow-10s/"
        ])
        
        with self.client.get(
            endpoint,
            catch_response=True,
            name=f"{STAGE_NAME} Connection Holder",
            timeout=15
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("connection_holder", True, elapsed)
                print(f"🔒 Slow query completed (connection released!)")
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503 - Connection Holder")
                record_request("connection_holder", False, elapsed)
                response.failure("Pool exhausted during hold!")
            else:
                record_request("connection_holder", False, elapsed)
                response.failure(f"Error: {response.status_code}")


# =============================================================================
# Monitor User (Pool 상태 모니터링)
# =============================================================================

class MonitorUser(PatchedHttpUser):
    """
    Pool 상태를 모니터링하는 사용자.
    
    Health check와 Pool 상태를 주기적으로 확인합니다.
    """
    
    wait_time = constant(0)  # 휴식 없이 계속 모니터링!
    weight = 1  # 10%
    
    @task(3)
    @tag("monitor")
    def health_check(self):
        """헬스체크로 Pool 상태 확인"""
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/health/",
            catch_response=True,
            name=f"{STAGE_NAME} Health Check",
            timeout=15  # 2분 대기
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("health_check", True, elapsed)
                # Pool 고갈 후 성공하면 복구로 판단
                if _extreme_stats["phase"] == "exhaustion":
                    record_recovery()
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("health_check", False, elapsed)
                response.failure("Health check failed - Pool exhausted")
            else:
                record_request("health_check", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(2)
    @tag("monitor")
    def pool_status(self):
        """Pool 상태 상세 조회"""
        start = time.time()
        
        with self.client.get(
            "/api/self-healing/stress/pool-status/",
            catch_response=True,
            name=f"{STAGE_NAME} Pool Status",
            timeout=15  # 2분 대기
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("pool_status", True, elapsed)
                try:
                    data = response.json()
                    stats = data.get("pool_stats", {})
                    active = stats.get("active", 0)
                    total = stats.get("total_connections", 0)
                    print(f"[Pool] Active: {active}/{total}")
                except:
                    pass
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("pool_status", False, elapsed)
                response.failure("Pool status failed")
            else:
                record_request("pool_status", False, elapsed)
                response.failure(f"Error: {response.status_code}")


# =============================================================================
# Normal User (정상 요청으로 복구 확인)
# =============================================================================

class NormalUser(PatchedHttpUser):
    """
    정상적인 빠른 요청을 보내는 사용자.
    
    Pool 복구 후 정상 동작 확인용.
    """
    
    wait_time = constant(0)  # 휴식 없이 계속 요청!
    weight = 2  # 20%
    
    @task(5)
    @tag("normal")
    def product_list(self):
        """빠른 상품 목록 조회"""
        start = time.time()
        
        with self.client.get(
            "/api/products/",
            params={"page": 1, "page_size": 10},
            catch_response=True,
            name=f"{STAGE_NAME} Normal - Products",
            timeout=15  # 2분 대기
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("normal_query", True, elapsed)
                # Pool 고갈 후 성공하면 복구로 판단
                if _extreme_stats["phase"] == "exhaustion":
                    record_recovery()
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("normal_query", False, elapsed)
                response.failure("Pool exhausted")
            else:
                record_request("normal_query", False, elapsed)
                response.failure(f"Error: {response.status_code}")
    
    @task(2)
    @tag("normal")
    def category_list(self):
        """빠른 카테고리 조회"""
        start = time.time()
        
        with self.client.get(
            "/api/categories/",
            catch_response=True,
            name=f"{STAGE_NAME} Normal - Categories",
            timeout=15  # 2분 대기
        ) as response:
            elapsed = (time.time() - start) * 1000
            
            if response.status_code == 200:
                record_request("normal_query", True, elapsed)
                if _extreme_stats["phase"] == "exhaustion":
                    record_recovery()
                response.success()
            elif response.status_code == 503:
                record_pool_exhaustion("503")
                record_request("normal_query", False, elapsed)
                response.failure("Pool exhausted")
            else:
                record_request("normal_query", False, elapsed)
                response.failure(f"Error: {response.status_code}")


# =============================================================================
# Load Test Shape: 실제 상황 - 계속 퍼붓기! (휴식 없음)
# =============================================================================

class PoolExhaustionRecoveryShape(LoadTestShape):
    """
    🔥 Pool 고갈 → 회복 테스트 (완화된 버전)
    
    설정: Pool 3개, 워커 4개
    
    Phase 1 (0-15초): 워밍업 - 5명 (정상 작동 확인)
    Phase 2 (15-45초): 점진적 증가 - 20명으로 Pool 고갈 유도
    Phase 3 (45-75초): 냉각 - 3명으로 감소 (연결 해제 시간)
    Phase 4 (60-90초): 회복 확인 - 20명 유지
    
    총 실행 시간: 90초
    """
    
    stages = [
        # Phase 1: 워밍업 (0~10초) - 정상 작동 확인
        {"end_time": 10, "users": 10, "spawn_rate": 5, "phase": "warmup"},
        # Phase 2: 폭증! Pool 고갈 유도 (10~40초) - 100명!
        {"end_time": 40, "users": 100, "spawn_rate": 10, "phase": "spike"},
        # Phase 3: 냉각 (40~60초) - 부하 감소
        {"end_time": 60, "users": 5, "spawn_rate": 10, "phase": "cooldown"},
        # Phase 4: 회복 확인 (60~90초) - 정상 요청
        {"end_time": 90, "users": 20, "spawn_rate": 5, "phase": "recovery"},
    ]
    
    _last_phase = None
    _phase_start_times = {}
    
    def tick(self):
        run_time = self.get_run_time()
        
        for stage in self.stages:
            if run_time < stage["end_time"]:
                current_phase = stage["phase"]
                
                # Phase 변경 시 알림
                if self._last_phase != current_phase:
                    self._phase_start_times[current_phase] = run_time
                    print(f"\n{'='*70}")
                    print(f"🔄 PHASE: {self._last_phase or 'start'} → {current_phase.upper()}")
                    print(f"   Users: {stage['users']}, Time: {run_time:.0f}s")
                    
                    if current_phase == "warmup":
                        print(f"   📊 Baseline - Normal operation check")
                    elif current_phase == "spike":
                        print(f"   🔥 SPIKE - Pool 고갈 유도 중...")
                    elif current_phase == "cooldown":
                        print(f"   ❄️ COOLDOWN - 부하 감소, 연결 반환 대기")
                    elif current_phase == "recovery":
                        print(f"   🔍 RECOVERY - 회복 확인 중")
                    print(f"{'='*70}\n")
                    self._last_phase = current_phase
                
                return (stage["users"], stage["spawn_rate"])
        
        # 모든 단계 완료
        print(f"\n{'='*70}")
        print("🏁 TEST COMPLETE - 3 minutes of continuous load!")
        print(f"   If no recovery detected, Circuit Breaker integration needed!")
        print(f"{'='*70}\n")
        return None
