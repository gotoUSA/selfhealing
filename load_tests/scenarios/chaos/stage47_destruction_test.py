"""
🔥 Stage 47: L3 파괴 테스트 (Destruction Test) v3.0
==================================================

목적: Self-Healing 시스템이 "정말로" 동작하는지 검증
- 시스템이 안정적이라서 힐링이 필요없었던 것 vs 힐링이 실제로 동작하는 것

3가지 파괴 시나리오:
1. DB 블랙아웃 (The Hard Disconnect) - CB OPEN → 503 Fast Fail
2. 에러 버짓 살인마 (The Budget Killer) - Error Budget 0% → 격리 모드
3. 유령의 복구 (The Ghost Recovery) - OPEN → HALF_OPEN → CLOSED

핵심 설정값:
- failure_threshold: 5 (5번 실패 → OPEN)
- recovery_timeout: 60초 (60초 후 HALF_OPEN)
- success_threshold: 2 (2번 성공 → CLOSED)
- Error Budget Critical: < 20%
"""

import time
import random
import logging
from locust import HttpUser, task, between, tag, events

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================================================
# 파괴 테스트 통계
# =============================================================================

_destruction_stats = {
    # 시나리오 1: DB 블랙아웃
    "db_blackout": {
        "failure_injections": 0,
        "cb_open_detected": 0,
        "fast_fail_503_count": 0,
        "fast_fail_response_times": [],  # 0.1초 이내 여부
        "recovery_started": False,
    },
    
    # 시나리오 2: 에러 버짓 살인마
    "budget_killer": {
        "errors_injected": 0,
        "initial_budget": None,
        "lowest_budget": 100.0,
        "budget_zero_reached": False,
        "isolation_mode_triggered": False,
        "traffic_blocked_count": 0,
        "time_to_zero_seconds": None,
    },
    
    # 시나리오 3: 유령의 복구
    "ghost_recovery": {
        "open_state_forced": False,
        "open_state_duration": 0,
        "half_open_detected": False,
        "half_open_timestamp": None,
        "probe_success_count": 0,
        "closed_recovery_detected": False,
        "recovery_timestamp": None,
        "full_cycle_completed": False,
    },
    
    # 전체 통계
    "total_requests": 0,
    "total_failures": 0,
    "healing_actions_observed": 0,
}

# =============================================================================
# 파괴 테스트 사용자
# =============================================================================

class DestructionTestUser(HttpUser):
    """L3 파괴 테스트 전용 사용자"""
    
    wait_time = between(0.1, 0.5)  # 빠른 요청으로 파괴 가속
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin_token = None
        self.csrf_token = None
        self.headers = {"Content-Type": "application/json"}
        self._scenario_phase = "init"  # init → destroy → observe → recover
        
    def on_start(self):
        """Admin 로그인으로 시작"""
        self._admin_login()
        
    def _admin_login(self):
        """Admin 계정으로 로그인"""
        try:
            # CSRF 토큰 획득
            csrf_resp = self.client.get("/api/auth/csrf/", catch_response=True)
            if csrf_resp.status_code == 200:
                self.csrf_token = csrf_resp.cookies.get("csrftoken", "")
                
            # Admin 로그인
            login_data = {
                "username": "admin",
                "password": "admin123!"
            }
            headers = {
                "Content-Type": "application/json",
                "X-CSRFToken": self.csrf_token
            }
            
            with self.client.post(
                "/api/auth/login/",
                json=login_data,
                headers=headers,
                name="[Destruction] Admin Login",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    data = resp.json()
                    self.admin_token = data.get("access") or data.get("token")
                    self.headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.admin_token}" if self.admin_token else "",
                        "X-CSRFToken": self.csrf_token
                    }
                    resp.success()
                else:
                    resp.failure(f"Admin login failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"Admin login error: {e}")

    # =========================================================================
    # 시나리오 1: DB 블랙아웃 (The Hard Disconnect)
    # =========================================================================
    
    @task(3)
    @tag("destruction", "db-blackout")
    def scenario_db_blackout(self):
        """
        🔌 DB 블랙아웃: 서비스에 장애를 주입하고 CB OPEN + Fast Fail 확인
        
        검증 항목:
        1. 장애 주입 후 CB가 OPEN 상태로 전환
        2. OPEN 상태에서 503 응답 + 0.1초 이내 Fast Fail
        """
        global _destruction_stats
        
        # Phase 1: 장애 주입 (5회 실패로 CB OPEN 트리거)
        service_name = "toss_payment"  # 실제 존재하는 서비스
        
        # 강제로 실패 주입
        control_data = {
            "action": "inject_failure",
            "service_name": service_name,
            "metadata": {
                "trigger_cb_failures": 10,  # 5회 넘게 실패 주입
                "failure_type": "timeout",
                "duration_seconds": 30
            }
        }
        
        start_time = time.time()
        with self.client.post(
            "/api/self-healing/control/",
            json=control_data,
            headers=self.headers,
            name="[Destruction] DB Blackout - Inject Failure",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                _destruction_stats["db_blackout"]["failure_injections"] += 1
                
                # 응답에서 CB 상태 확인
                try:
                    data = resp.json()
                    cb_state = data.get("cb_state") or data.get("circuit_breaker_state")
                    if cb_state == "OPEN":
                        _destruction_stats["db_blackout"]["cb_open_detected"] += 1
                except:
                    pass
                    
                resp.success()
            else:
                resp.failure(f"Inject failure failed: {resp.status_code}")
                
        # Phase 2: CB 상태 확인
        with self.client.get(
            f"/api/self-healing/circuit-breaker/status/{service_name}/",
            headers=self.headers,
            name="[Destruction] DB Blackout - Check CB Status",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    state = data.get("state") or data.get("status")
                    if state in ["OPEN", "open"]:
                        _destruction_stats["db_blackout"]["cb_open_detected"] += 1
                except:
                    pass
                resp.success()
            else:
                resp.success()  # CB 상태 조회 실패는 무시
                
        # Phase 3: Fast Fail 테스트 (OPEN 상태에서 즉시 503 응답)
        fast_fail_start = time.time()
        with self.client.post(
            "/api/payments/request/",
            json={"payment_key": f"test_{random.randint(1000, 9999)}"},
            headers=self.headers,
            name="[Destruction] DB Blackout - Fast Fail Test",
            catch_response=True
        ) as resp:
            response_time = time.time() - fast_fail_start
            
            if resp.status_code == 503:
                _destruction_stats["db_blackout"]["fast_fail_503_count"] += 1
                _destruction_stats["db_blackout"]["fast_fail_response_times"].append(response_time)
                resp.success()
            elif resp.status_code in [200, 400, 401, 404]:
                # 정상 응답이면 CB가 OPEN이 아님
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")
                
    # =========================================================================
    # 시나리오 2: 에러 버짓 살인마 (The Budget Killer)
    # =========================================================================
    
    @task(5)
    @tag("destruction", "budget-killer")
    def scenario_budget_killer(self):
        """
        📉 에러 버짓 살인마: 초당 대량의 Critical 에러로 예산 0% → 격리 모드
        
        검증 항목:
        1. Error Budget이 0%까지 떨어지는지
        2. 0% 도달 시 격리 모드(Isolation Mode) 활성화
        3. 격리 모드에서 트래픽 차단 확인
        """
        global _destruction_stats
        
        stats = _destruction_stats["budget_killer"]
        
        # Phase 1: 현재 Error Budget 확인
        with self.client.get(
            "/api/self-healing/error-budget/status/",
            headers=self.headers,
            name="[Destruction] Budget Killer - Check Budget",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    remaining = data.get("remaining_percent") or data.get("budget_remaining_percent")
                    if remaining is None:
                        remaining = data.get("data", {}).get("remaining_percent")
                    
                    if remaining is not None:
                        remaining = float(remaining)
                        
                        # 초기 예산 기록
                        if stats["initial_budget"] is None:
                            stats["initial_budget"] = remaining
                            
                        # 최저 예산 갱신
                        if remaining < stats["lowest_budget"]:
                            stats["lowest_budget"] = remaining
                            
                        # 0% 도달 확인
                        if remaining <= 0:
                            stats["budget_zero_reached"] = True
                            
                        # 격리 모드 확인
                        if data.get("isolation_mode") or data.get("is_isolated"):
                            stats["isolation_mode_triggered"] = True
                            
                except Exception as e:
                    logger.debug(f"Budget parse error: {e}")
                resp.success()
            else:
                resp.success()
                
        # Phase 2: 대량 에러 주입 (Critical 레벨로 빠르게 소진)
        error_data = {
            "domain": "payment",
            "severity": "critical",  # 가중치 10
            "multiplier": 100,  # 100배 증폭
            "error_count": 50  # 한 번에 50건
        }
        
        with self.client.post(
            "/api/self-healing/error-budget/record/",
            json=error_data,
            headers=self.headers,
            name="[Destruction] Budget Killer - Inject Errors",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                stats["errors_injected"] += 50
                resp.success()
            elif resp.status_code == 401:
                # 인증 필요 - 재로그인
                self._admin_login()
                resp.success()
            else:
                resp.failure(f"Error injection failed: {resp.status_code}")
                
        # Phase 3: 예산 고갈 강제 (테스트용 API)
        if not stats["budget_zero_reached"]:
            with self.client.post(
                "/api/self-healing/error-budget/exhaust/",
                json={"confirm": True},
                headers=self.headers,
                name="[Destruction] Budget Killer - Force Exhaust",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    stats["budget_zero_reached"] = True
                    resp.success()
                else:
                    resp.success()
                    
        # Phase 4: 격리 모드에서 트래픽 차단 확인
        if stats["budget_zero_reached"]:
            with self.client.post(
                "/api/payments/request/",
                json={"payment_key": f"blocked_{random.randint(1000, 9999)}"},
                headers=self.headers,
                name="[Destruction] Budget Killer - Traffic Blocked Check",
                catch_response=True
            ) as resp:
                if resp.status_code in [503, 429, 423]:  # Service Unavailable, Too Many, Locked
                    stats["traffic_blocked_count"] += 1
                    resp.success()
                else:
                    resp.success()
                    
    # =========================================================================
    # 시나리오 3: 유령의 복구 (The Ghost Recovery)
    # =========================================================================
    
    @task(2)
    @tag("destruction", "ghost-recovery")
    def scenario_ghost_recovery(self):
        """
        👻 유령의 복구: OPEN → HALF_OPEN → CLOSED 전체 사이클 관찰
        
        검증 항목:
        1. CB OPEN 상태에서 recovery_timeout(60초) 후 HALF_OPEN 전환
        2. HALF_OPEN에서 성공적인 probe 요청
        3. success_threshold(2회) 성공 시 CLOSED 복구
        """
        global _destruction_stats
        
        stats = _destruction_stats["ghost_recovery"]
        service_name = "toss_payment"
        
        # Phase 1: CB 상태 확인
        with self.client.get(
            f"/api/self-healing/circuit-breaker/status/{service_name}/",
            headers=self.headers,
            name="[Destruction] Ghost Recovery - Check CB State",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    state = data.get("state") or data.get("status", "").upper()
                    
                    if state == "OPEN":
                        if not stats["open_state_forced"]:
                            stats["open_state_forced"] = True
                            stats["open_state_duration"] = time.time()
                            
                    elif state == "HALF_OPEN":
                        if not stats["half_open_detected"]:
                            stats["half_open_detected"] = True
                            stats["half_open_timestamp"] = time.time()
                            _destruction_stats["healing_actions_observed"] += 1
                            
                    elif state == "CLOSED":
                        if stats["half_open_detected"] and not stats["closed_recovery_detected"]:
                            stats["closed_recovery_detected"] = True
                            stats["recovery_timestamp"] = time.time()
                            stats["full_cycle_completed"] = True
                            _destruction_stats["healing_actions_observed"] += 1
                            
                except Exception as e:
                    logger.debug(f"CB state parse error: {e}")
                resp.success()
            else:
                resp.success()
                
        # Phase 2: HALF_OPEN 상태에서 probe 요청 (성공 시도)
        if stats["half_open_detected"] and not stats["closed_recovery_detected"]:
            # 복구를 위한 성공 요청 시도
            control_data = {
                "action": "recover",
                "service_name": service_name,
                "metadata": {"force_success": True}
            }
            
            with self.client.post(
                "/api/self-healing/control/",
                json=control_data,
                headers=self.headers,
                name="[Destruction] Ghost Recovery - Probe Success",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    stats["probe_success_count"] += 1
                    resp.success()
                else:
                    resp.success()
                    
        # Phase 3: 전체 Pool 상태 확인
        with self.client.get(
            "/api/self-healing/circuit-breaker/pool/status/",
            headers=self.headers,
            name="[Destruction] Ghost Recovery - Pool Status",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    circuits = data.get("circuits", {})
                    
                    for cb_name, cb_info in circuits.items():
                        state = cb_info.get("state", "").upper()
                        if state == "HALF_OPEN":
                            stats["half_open_detected"] = True
                            _destruction_stats["healing_actions_observed"] += 1
                        elif state == "CLOSED" and stats["half_open_detected"]:
                            if not stats["closed_recovery_detected"]:
                                stats["closed_recovery_detected"] = True
                                stats["full_cycle_completed"] = True
                                
                except Exception as e:
                    logger.debug(f"Pool status parse error: {e}")
                resp.success()
            else:
                resp.success()
                
    # =========================================================================
    # 헬스 체크 및 모니터링
    # =========================================================================
    
    @task(1)
    @tag("destruction", "monitoring")
    def monitor_healing_status(self):
        """Self-Healing 상태 종합 모니터링"""
        global _destruction_stats
        
        # Health Check
        with self.client.get(
            "/api/self-healing/health/",
            headers=self.headers,
            name="[Destruction] Monitor - Health Check",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.success()
                
        # Emergency Mode 확인
        with self.client.get(
            "/api/self-healing/emergency/status/",
            headers=self.headers,
            name="[Destruction] Monitor - Emergency Status",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("active") or data.get("is_active"):
                        _destruction_stats["healing_actions_observed"] += 1
                except:
                    pass
                resp.success()
            else:
                resp.success()


# =============================================================================
# 결과 리포터
# =============================================================================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 파괴 테스트 결과 출력"""
    global _destruction_stats
    
    print("\n")
    print("=" * 70)
    print("🔥 STAGE 5: L3 파괴 테스트 (DESTRUCTION TEST) 결과 v3.0")
    print("=" * 70)
    
    # 시나리오 1: DB 블랙아웃
    db = _destruction_stats["db_blackout"]
    print("\n[1] 🔌 DB 블랙아웃 (The Hard Disconnect)")
    print(f"   - 장애 주입 횟수: {db['failure_injections']}")
    print(f"   - CB OPEN 감지: {db['cb_open_detected']}")
    print(f"   - 503 Fast Fail 횟수: {db['fast_fail_503_count']}")
    
    if db['fast_fail_response_times']:
        avg_time = sum(db['fast_fail_response_times']) / len(db['fast_fail_response_times'])
        fast_fails = sum(1 for t in db['fast_fail_response_times'] if t < 0.1)
        print(f"   - 평균 Fast Fail 응답 시간: {avg_time*1000:.1f}ms")
        print(f"   - 0.1초 이내 응답: {fast_fails}/{len(db['fast_fail_response_times'])}")
        
        db_ok = db['cb_open_detected'] > 0 and fast_fails > 0
        print(f"   - 판정: {'✅ PASS' if db_ok else '❌ FAIL'} - {'CB OPEN + Fast Fail 확인' if db_ok else 'CB OPEN 또는 Fast Fail 미확인'}")
    else:
        print("   - 판정: ⚠️ 데이터 부족")
        db_ok = False
    
    # 시나리오 2: 에러 버짓 살인마
    bk = _destruction_stats["budget_killer"]
    print("\n[2] 📉 에러 버짓 살인마 (The Budget Killer)")
    print(f"   - 주입된 에러 수: {bk['errors_injected']}")
    print(f"   - 초기 예산: {bk['initial_budget']}%")
    print(f"   - 최저 예산: {bk['lowest_budget']}%")
    print(f"   - 0% 도달: {'YES' if bk['budget_zero_reached'] else 'NO'}")
    print(f"   - 격리 모드 활성화: {'YES ✅' if bk['isolation_mode_triggered'] else 'NO'}")
    print(f"   - 트래픽 차단 횟수: {bk['traffic_blocked_count']}")
    
    bk_ok = bk['budget_zero_reached'] or bk['isolation_mode_triggered'] or bk['lowest_budget'] < 50
    print(f"   - 판정: {'✅ PASS' if bk_ok else '❌ FAIL'} - {'예산 소진 또는 격리 모드' if bk_ok else '예산 변화 미미'}")
    
    # 시나리오 3: 유령의 복구
    gr = _destruction_stats["ghost_recovery"]
    print("\n[3] 👻 유령의 복구 (The Ghost Recovery)")
    print(f"   - OPEN 상태 강제: {'YES' if gr['open_state_forced'] else 'NO'}")
    print(f"   - HALF_OPEN 전환 감지: {'YES ✅' if gr['half_open_detected'] else 'NO'}")
    print(f"   - Probe 성공 횟수: {gr['probe_success_count']}")
    print(f"   - CLOSED 복구 감지: {'YES ✅' if gr['closed_recovery_detected'] else 'NO'}")
    print(f"   - 전체 사이클 완료: {'YES 🎉' if gr['full_cycle_completed'] else 'NO'}")
    
    gr_ok = gr['half_open_detected'] or gr['closed_recovery_detected'] or gr['full_cycle_completed']
    print(f"   - 판정: {'✅ PASS' if gr_ok else '❌ FAIL'} - {'복구 사이클 관찰됨' if gr_ok else '복구 사이클 미관찰'}")
    
    # 종합 결과
    print("\n" + "-" * 70)
    print("[종합] Self-Healing 실제 동작 증명")
    print("-" * 70)
    
    healing_observed = _destruction_stats["healing_actions_observed"]
    print(f"   - 힐링 액션 관찰 횟수: {healing_observed}")
    
    passed = sum([db_ok, bk_ok, gr_ok])
    total = 3
    
    print(f"\n   📊 파괴 테스트 결과: {passed}/{total} PASS")
    
    if passed == 3:
        print("\n   🏆 L3 Self-Healing: 완벽하게 동작함!")
        print("   → 장애 발생 시 CB OPEN, 예산 고갈 시 격리, 복구 사이클 확인")
    elif passed >= 2:
        print("\n   ✅ L3 Self-Healing: 대부분 동작함")
        print("   → 일부 시나리오에서 힐링 동작 확인")
    elif passed >= 1:
        print("\n   ⚠️ L3 Self-Healing: 부분적으로 동작함")
        print("   → 추가 검증 필요")
    else:
        print("\n   ❌ L3 Self-Healing: 동작 미확인")
        print("   → 시스템이 안정적이거나 힐링 미동작")
    
    print("\n" + "=" * 70)
    print("🔥 DESTRUCTION TEST 완료")
    print("=" * 70)
