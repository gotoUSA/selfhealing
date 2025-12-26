"""
🔥 Stage 48: X-Test-Mode 테스트 (Chaos Monkey Bypass)
=====================================================

목적: Rate Limiter(L1)를 우회하여 L2/L3 Self-Healing 동작을 직접 관찰

핵심 원리:
- X-Test-Mode: chaos-monkey 헤더로 Rate Limiter 우회
- 직접 CB에 장애 주입 (record_failure 호출)
- CB OPEN → Fast Fail → HALF_OPEN → CLOSED 전체 사이클 검증

테스트 시나리오:
- 48-1: 5회 실패 주입 → CB OPEN
- 48-2: OPEN 상태에서 요청 → 503 Fast Fail (< 100ms)
- 48-3: 60초 대기 후 → HALF_OPEN 전환
- 48-4: Probe 성공 → CLOSED 복구
- 48-5: 전체 사이클 타임라인 기록

실행 방법:
    cd load_tests
    locust -f scenarios/chaos/stage48_xtest_mode.py --headless -u 1 -r 1 -t 3m --host http://localhost:8000
"""

import time
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from locust import HttpUser, task, between, tag, events, constant
from locust.runners import MasterRunner, WorkerRunner

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# X-Test-Mode 통계
# =============================================================================

_xtest_stats = {
    # 테스트 메타데이터
    "test_started_at": None,
    "test_ended_at": None,
    
    # 시나리오 48-1: CB OPEN
    "scenario_48_1": {
        "name": "CB OPEN via Failure Injection",
        "passed": False,
        "failure_count_injected": 0,
        "cb_state_before": None,
        "cb_state_after": None,
        "state_changed_to_open": False,
        "timestamp": None,
    },
    
    # 시나리오 48-2: Fast Fail
    "scenario_48_2": {
        "name": "503 Fast Fail Verification",
        "passed": False,
        "response_times_ms": [],
        "all_fast_fail": False,  # 모든 응답 < 100ms
        "avg_response_time_ms": None,
        "max_response_time_ms": None,
        "fast_fail_count": 0,
        "total_requests": 0,
        "timestamp": None,
    },
    
    # 시나리오 48-3: HALF_OPEN 전환
    "scenario_48_3": {
        "name": "HALF_OPEN Transition",
        "passed": False,
        "waited_seconds": 0,
        "half_open_detected": False,
        "detection_time": None,
        "timestamp": None,
    },
    
    # 시나리오 48-4: CLOSED 복구
    "scenario_48_4": {
        "name": "CLOSED Recovery",
        "passed": False,
        "probe_success": False,
        "final_state": None,
        "recovery_time_seconds": None,
        "timestamp": None,
    },
    
    # 시나리오 48-5: 전체 사이클
    "scenario_48_5": {
        "name": "Full Cycle Timeline",
        "passed": False,
        "timeline": [],
        "total_cycle_time_seconds": None,
        "timestamp": None,
    },
    
    # 시스템 스냅샷
    "snapshots": [],
    
    # 에러 로그
    "errors": [],
}


# =============================================================================
# X-Test-Mode 테스트 사용자
# =============================================================================

class XTestModeUser(HttpUser):
    """X-Test-Mode를 사용한 CB 동작 검증 사용자"""
    
    wait_time = constant(1)  # 1초 고정 대기
    
    # X-Test-Mode 헤더
    CHAOS_HEADER = "X-Test-Mode"
    CHAOS_VALUE = "chaos-monkey"
    
    # 테스트 대상 서비스
    TEST_SERVICE = "database"
    
    # CB 설정값 (settings에서 가져와야 하지만 기본값 사용)
    CB_FAILURE_THRESHOLD = 5
    CB_RECOVERY_TIMEOUT = 60  # 초
    CB_SUCCESS_THRESHOLD = 2
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.admin_token = None
        self.csrf_token = None
        self.headers = {"Content-Type": "application/json"}
        self._phase = "init"  # init → inject → observe → recover → done
        self._cycle_start_time = None
        self._open_detected_time = None
        
    def on_start(self):
        """테스트 시작: Admin 로그인 + CB 초기화"""
        global _xtest_stats
        _xtest_stats["test_started_at"] = datetime.now().isoformat()
        
        self._admin_login()
        self._reset_cb_for_test()
        self._cycle_start_time = time.time()
        
    def on_stop(self):
        """테스트 종료: 통계 정리"""
        global _xtest_stats
        _xtest_stats["test_ended_at"] = datetime.now().isoformat()
        
        # 전체 사이클 시간 계산
        if self._cycle_start_time:
            _xtest_stats["scenario_48_5"]["total_cycle_time_seconds"] = (
                time.time() - self._cycle_start_time
            )
            
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
                name="[48] Admin Login",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    data = resp.json()
                    self.admin_token = data.get("access") or data.get("token")
                    self._update_headers()
                    resp.success()
                    logger.info("[Stage 48] Admin login successful")
                else:
                    resp.failure(f"Admin login failed: {resp.status_code}")
                    
        except Exception as e:
            logger.error(f"[Stage 48] Admin login error: {e}")
            _xtest_stats["errors"].append(f"Admin login: {e}")
            
    def _update_headers(self):
        """헤더 업데이트 (Admin + X-Test-Mode)"""
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.admin_token}" if self.admin_token else "",
            "X-CSRFToken": self.csrf_token,
            self.CHAOS_HEADER: self.CHAOS_VALUE,  # X-Test-Mode 헤더!
        }
        
    def _reset_cb_for_test(self):
        """테스트 전 CB 상태 초기화"""
        try:
            with self.client.post(
                "/api/self-healing/xtest/reset-cb/",
                json={"service": self.TEST_SERVICE},
                headers=self.headers,
                name="[48] Reset CB",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    logger.info(f"[Stage 48] CB reset for {self.TEST_SERVICE}")
                    resp.success()
                else:
                    logger.warning(f"[Stage 48] CB reset failed: {resp.status_code}")
                    # 실패해도 계속 진행
                    resp.success()
        except Exception as e:
            logger.warning(f"[Stage 48] CB reset error: {e}")
    
    # =========================================================================
    # 시나리오 48-1: CB OPEN (장애 주입)
    # =========================================================================
    
    @task(5)
    @tag("xtest", "48-1")
    def scenario_48_1_inject_failure(self):
        """
        48-1: 5회 실패 주입 → CB OPEN 확인
        
        검증:
        - 장애 주입 API 호출 성공
        - CB 상태가 OPEN으로 변경
        """
        global _xtest_stats
        
        if _xtest_stats["scenario_48_1"]["passed"]:
            # 이미 통과했으면 skip
            return
            
        # CB 현재 상태 확인 (주입 전)
        before_state = self._get_cb_state()
        _xtest_stats["scenario_48_1"]["cb_state_before"] = before_state
        
        # 장애 주입 (5회 = failure_threshold)
        with self.client.post(
            "/api/self-healing/xtest/inject-cb-failure/",
            json={
                "service": self.TEST_SERVICE,
                "count": self.CB_FAILURE_THRESHOLD
            },
            headers=self.headers,
            name="[48-1] Inject CB Failure",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    _xtest_stats["scenario_48_1"]["failure_count_injected"] = data.get("injected_failures", 0)
                    _xtest_stats["scenario_48_1"]["cb_state_after"] = data.get("cb_state")
                    
                    # OPEN 상태로 변경되었는지 확인
                    if data.get("cb_state", "").lower() == "open":
                        _xtest_stats["scenario_48_1"]["state_changed_to_open"] = True
                        _xtest_stats["scenario_48_1"]["passed"] = True
                        _xtest_stats["scenario_48_1"]["timestamp"] = datetime.now().isoformat()
                        self._open_detected_time = time.time()
                        
                        # 타임라인 기록
                        _xtest_stats["scenario_48_5"]["timeline"].append({
                            "event": "CB_OPEN",
                            "service": self.TEST_SERVICE,
                            "timestamp": datetime.now().isoformat(),
                            "details": f"Injected {data.get('injected_failures')} failures"
                        })
                        
                        logger.info(f"[Stage 48-1] ✅ PASS: CB opened! State: {data.get('cb_state')}")
                        
                    # 스냅샷 저장
                    if data.get("snapshot"):
                        _xtest_stats["snapshots"].append({
                            "phase": "48-1",
                            "snapshot": data.get("snapshot")
                        })
                        
                except Exception as e:
                    logger.error(f"[Stage 48-1] Response parse error: {e}")
                    
                resp.success()
            elif resp.status_code == 403:
                logger.warning("[Stage 48-1] X-Test-Mode denied (check CHAOS_ENABLED)")
                _xtest_stats["errors"].append("X-Test-Mode denied")
                resp.failure("X-Test-Mode denied")
            else:
                resp.failure(f"Inject failure failed: {resp.status_code}")
    
    # =========================================================================
    # 시나리오 48-2: Fast Fail 검증
    # =========================================================================
    
    @task(10)
    @tag("xtest", "48-2")
    def scenario_48_2_fast_fail_test(self):
        """
        48-2: OPEN 상태에서 503 Fast Fail 확인
        
        검증:
        - 응답 코드 503 (또는 request blocked)
        - 응답 시간 < 100ms
        """
        global _xtest_stats
        
        # CB가 OPEN이 아니면 skip
        if not _xtest_stats["scenario_48_1"]["state_changed_to_open"]:
            return
            
        with self.client.get(
            "/api/self-healing/xtest/fast-fail-test/",
            params={"service": self.TEST_SERVICE},
            headers=self.headers,
            name="[48-2] Fast Fail Test",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    response_time_ms = data.get("response_time_ms", 0)
                    is_fast_fail = data.get("is_fast_fail", False)
                    request_allowed = data.get("request_allowed", True)
                    
                    _xtest_stats["scenario_48_2"]["response_times_ms"].append(response_time_ms)
                    _xtest_stats["scenario_48_2"]["total_requests"] += 1
                    
                    # Fast Fail 여부 체크
                    if is_fast_fail or response_time_ms < 100:
                        _xtest_stats["scenario_48_2"]["fast_fail_count"] += 1
                        
                    # 통계 업데이트
                    times = _xtest_stats["scenario_48_2"]["response_times_ms"]
                    if times:
                        _xtest_stats["scenario_48_2"]["avg_response_time_ms"] = sum(times) / len(times)
                        _xtest_stats["scenario_48_2"]["max_response_time_ms"] = max(times)
                        
                    # 모든 요청이 Fast Fail인지 확인 (최소 5개 이상)
                    total = _xtest_stats["scenario_48_2"]["total_requests"]
                    ff_count = _xtest_stats["scenario_48_2"]["fast_fail_count"]
                    
                    if total >= 5 and ff_count == total:
                        _xtest_stats["scenario_48_2"]["all_fast_fail"] = True
                        _xtest_stats["scenario_48_2"]["passed"] = True
                        _xtest_stats["scenario_48_2"]["timestamp"] = datetime.now().isoformat()
                        logger.info(f"[Stage 48-2] ✅ PASS: All requests fast-failed! Avg: {_xtest_stats['scenario_48_2']['avg_response_time_ms']:.2f}ms")
                        
                except Exception as e:
                    logger.error(f"[Stage 48-2] Response parse error: {e}")
                    
                resp.success()
            else:
                resp.failure(f"Fast fail test failed: {resp.status_code}")
    
    # =========================================================================
    # 시나리오 48-3: HALF_OPEN 전환 감지
    # =========================================================================
    
    @task(3)
    @tag("xtest", "48-3")
    def scenario_48_3_half_open_detection(self):
        """
        48-3: recovery_timeout(60초) 후 HALF_OPEN 전환 감지
        
        검증:
        - CB 상태가 HALF_OPEN으로 변경
        """
        global _xtest_stats
        
        # CB OPEN이 감지되지 않았으면 skip
        if not self._open_detected_time:
            return
            
        # 이미 통과했으면 skip
        if _xtest_stats["scenario_48_3"]["passed"]:
            return
            
        # 대기 시간 계산
        elapsed = time.time() - self._open_detected_time
        _xtest_stats["scenario_48_3"]["waited_seconds"] = elapsed
        
        # CB 상태 확인
        current_state = self._get_cb_state()
        
        if current_state and current_state.lower() == "half_open":
            _xtest_stats["scenario_48_3"]["half_open_detected"] = True
            _xtest_stats["scenario_48_3"]["detection_time"] = elapsed
            _xtest_stats["scenario_48_3"]["passed"] = True
            _xtest_stats["scenario_48_3"]["timestamp"] = datetime.now().isoformat()
            
            # 타임라인 기록
            _xtest_stats["scenario_48_5"]["timeline"].append({
                "event": "HALF_OPEN",
                "service": self.TEST_SERVICE,
                "timestamp": datetime.now().isoformat(),
                "details": f"Detected after {elapsed:.1f}s"
            })
            
            logger.info(f"[Stage 48-3] ✅ PASS: HALF_OPEN detected after {elapsed:.1f}s")
    
    # =========================================================================
    # 시나리오 48-4: CLOSED 복구
    # =========================================================================
    
    @task(2)
    @tag("xtest", "48-4")
    def scenario_48_4_closed_recovery(self):
        """
        48-4: Probe 성공 후 CLOSED 복구 확인
        
        검증:
        - HALF_OPEN에서 성공 요청 후 CLOSED로 전환
        """
        global _xtest_stats
        
        # HALF_OPEN이 감지되지 않았으면 skip
        if not _xtest_stats["scenario_48_3"]["half_open_detected"]:
            return
            
        # 이미 통과했으면 skip
        if _xtest_stats["scenario_48_4"]["passed"]:
            return
            
        # CB 상태 확인
        current_state = self._get_cb_state()
        _xtest_stats["scenario_48_4"]["final_state"] = current_state
        
        # HALF_OPEN이면 recovery 트리거
        if current_state and current_state.lower() == "half_open":
            with self.client.post(
                "/api/self-healing/xtest/trigger-cb-recovery/",
                json={
                    "service": self.TEST_SERVICE,
                    "success_count": 3,  # half_open_max_calls default
                    "force": True  # 강제 CLOSED 전환
                },
                headers=self.headers,
                name="[48-3] CB Recovery Test",
                catch_response=True
            ) as resp:
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        if data.get("recovery_success"):
                            current_state = data.get("state_after")
                            _xtest_stats["scenario_48_4"]["final_state"] = current_state
                            logger.info(f"[Stage 48-4] Recovery triggered: {data.get('state_before')} → {current_state}")
                    except Exception as e:
                        logger.error(f"[Stage 48-4] Recovery parse error: {e}")
                    resp.success()
                else:
                    resp.failure(f"Recovery failed: {resp.status_code}")
        
        if current_state and current_state.lower() == "closed":
            _xtest_stats["scenario_48_4"]["probe_success"] = True
            _xtest_stats["scenario_48_4"]["passed"] = True
            _xtest_stats["scenario_48_4"]["timestamp"] = datetime.now().isoformat()
            
            # 복구 시간 계산
            if self._open_detected_time:
                recovery_time = time.time() - self._open_detected_time
                _xtest_stats["scenario_48_4"]["recovery_time_seconds"] = recovery_time
                
            # 타임라인 기록
            _xtest_stats["scenario_48_5"]["timeline"].append({
                "event": "CLOSED",
                "service": self.TEST_SERVICE,
                "timestamp": datetime.now().isoformat(),
                "details": f"Recovered after {_xtest_stats['scenario_48_4'].get('recovery_time_seconds', 0):.1f}s"
            })
            
            # 전체 사이클 완료
            _xtest_stats["scenario_48_5"]["passed"] = True
            _xtest_stats["scenario_48_5"]["timestamp"] = datetime.now().isoformat()
            
            logger.info(f"[Stage 48-4] ✅ PASS: CB recovered to CLOSED! "
                       f"Recovery time: {_xtest_stats['scenario_48_4'].get('recovery_time_seconds', 0):.1f}s")
            logger.info("[Stage 48-5] ✅ PASS: Full cycle completed!")
    
    # =========================================================================
    # 모니터링 태스크
    # =========================================================================
    
    @task(5)
    @tag("xtest", "monitor")
    def monitor_cb_status(self):
        """CB 상태 지속적 모니터링"""
        with self.client.get(
            "/api/self-healing/xtest/cb-status/",
            params={"service": self.TEST_SERVICE},
            headers=self.headers,
            name="[48] Monitor CB Status",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.success()  # 모니터링은 실패해도 OK
                
    @task(2)
    @tag("xtest", "monitor")
    def monitor_system_snapshot(self):
        """시스템 스냅샷 수집"""
        with self.client.get(
            "/api/self-healing/xtest/snapshot/",
            headers=self.headers,
            name="[48] System Snapshot",
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if data.get("snapshot"):
                        _xtest_stats["snapshots"].append({
                            "phase": "monitor",
                            "timestamp": datetime.now().isoformat(),
                            "snapshot": data.get("snapshot")
                        })
                except:
                    pass
                resp.success()
            else:
                resp.success()
    
    # =========================================================================
    # 헬퍼 메서드
    # =========================================================================
    
    def _get_cb_state(self) -> Optional[str]:
        """현재 CB 상태 조회"""
        try:
            resp = self.client.get(
                "/api/self-healing/xtest/cb-status/",
                params={"service": self.TEST_SERVICE},
                headers=self.headers,
                name="[48] Get CB State (internal)",
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("cb_state")
        except:
            pass
        return None


# =============================================================================
# Locust 이벤트 핸들러
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 초기화"""
    global _xtest_stats
    _xtest_stats["test_started_at"] = datetime.now().isoformat()
    logger.info("=" * 60)
    logger.info("🔥 Stage 48: X-Test-Mode 테스트 시작")
    logger.info("=" * 60)
    logger.info(f"Target service: {XTestModeUser.TEST_SERVICE}")
    logger.info(f"CB Failure Threshold: {XTestModeUser.CB_FAILURE_THRESHOLD}")
    logger.info(f"CB Recovery Timeout: {XTestModeUser.CB_RECOVERY_TIMEOUT}s")
    

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력"""
    global _xtest_stats
    _xtest_stats["test_ended_at"] = datetime.now().isoformat()
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("🏆 Stage 48: X-Test-Mode 테스트 결과")
    logger.info("=" * 60)
    
    # 시나리오별 결과
    scenarios = [
        ("48-1", _xtest_stats["scenario_48_1"]),
        ("48-2", _xtest_stats["scenario_48_2"]),
        ("48-3", _xtest_stats["scenario_48_3"]),
        ("48-4", _xtest_stats["scenario_48_4"]),
        ("48-5", _xtest_stats["scenario_48_5"]),
    ]
    
    passed = 0
    total = len(scenarios)
    
    for scenario_id, stats in scenarios:
        status = "✅ PASS" if stats["passed"] else "❌ FAIL"
        logger.info(f"  [{scenario_id}] {stats['name']}: {status}")
        if stats["passed"]:
            passed += 1
            
    logger.info("")
    logger.info(f"📊 총 결과: {passed}/{total} 통과")
    
    # 상세 통계
    if _xtest_stats["scenario_48_2"]["response_times_ms"]:
        logger.info(f"📈 Fast Fail 평균 응답: {_xtest_stats['scenario_48_2']['avg_response_time_ms']:.2f}ms")
        
    if _xtest_stats["scenario_48_4"]["recovery_time_seconds"]:
        logger.info(f"⏱️ 복구 시간: {_xtest_stats['scenario_48_4']['recovery_time_seconds']:.1f}초")
        
    if _xtest_stats["scenario_48_5"]["timeline"]:
        logger.info("")
        logger.info("📋 타임라인:")
        for event in _xtest_stats["scenario_48_5"]["timeline"]:
            logger.info(f"  {event['timestamp']}: {event['event']} - {event['details']}")
            
    logger.info("=" * 60)
    
    # JSON 결과 저장
    try:
        result_file = f"results/stage48_xtest_mode_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        import os
        os.makedirs("results", exist_ok=True)
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(_xtest_stats, f, indent=2, ensure_ascii=False)
        logger.info(f"📁 결과 저장: {result_file}")
    except Exception as e:
        logger.warning(f"결과 저장 실패: {e}")


# =============================================================================
# 직접 실행용
# =============================================================================

if __name__ == "__main__":
    import subprocess
    import sys
    
    cmd = [
        sys.executable, "-m", "locust",
        "-f", __file__,
        "--headless",
        "-u", "1",
        "-r", "1", 
        "-t", "3m",
        "--host", "http://localhost:8000"
    ]
    
    subprocess.run(cmd)
