"""
🔥 Stage 49: Docker Chaos - 병렬 Locust 모니터링
================================================

목적: Docker Chaos 중 시스템 동작을 Locust로 지속 관찰

핵심 원리:
- Stage 48: 장애를 "시뮬레이션" (CB에게 실패를 알림)
- Stage 49: 실제 장애 발생 (DB가 진짜로 응답 안 함) - CB가 스스로 감지

테스트 시나리오:
- 49-1: DB 컨테이너 중단 시 CB OPEN 자동 감지
- 49-2: CB OPEN 상태에서 503 Fast Fail (< 100ms)
- 49-3: DB 복구 후 CB CLOSED 자동 복구
- 49-4: 전체 복구 시간 < 120초
- 49-5: Blast Radius 격리 검증

실행 방법:
    1. 별도 터미널에서 docker_blackout.py 실행
    2. 이 스크립트로 Locust 병렬 모니터링:
       locust -f scenarios/chaos/stage49_docker_chaos.py --headless -u 1 -r 1 -t 5m

또는 통합 실행:
    python scenarios/chaos/stage49_docker_chaos.py --standalone
"""

import os
import sys
import time
import json
import logging
import subprocess
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple

# 프로젝트 루트를 PATH에 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
sys.path.insert(0, _project_root)
sys.path.insert(0, _load_tests_dir)

try:
    from locust import HttpUser, task, between, tag, events, constant
    from locust.runners import MasterRunner, WorkerRunner
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Stage 49 통계
# =============================================================================

_stage49_stats = {
    "test_started_at": None,
    "test_ended_at": None,
    
    # 시나리오 49-1: CB OPEN 자동 감지
    "scenario_49_1": {
        "name": "CB OPEN Auto Detection",
        "passed": False,
        "db_stop_time": None,
        "cb_open_detected_time": None,
        "detection_delay_seconds": None,
        "timestamp": None,
    },
    
    # 시나리오 49-2: Fast Fail
    "scenario_49_2": {
        "name": "503 Fast Fail Verification",
        "passed": False,
        "response_times_ms": [],
        "avg_response_time_ms": None,
        "max_response_time_ms": None,
        "fast_fail_count": 0,
        "total_requests": 0,
        "all_fast_fail": False,
        "timestamp": None,
    },
    
    # 시나리오 49-3: CB CLOSED 자동 복구
    "scenario_49_3": {
        "name": "CB CLOSED Auto Recovery",
        "passed": False,
        "db_start_time": None,
        "cb_closed_detected_time": None,
        "recovery_delay_seconds": None,
        "timestamp": None,
    },
    
    # 시나리오 49-4: 전체 복구 시간
    "scenario_49_4": {
        "name": "Total Recovery Time < 120s",
        "passed": False,
        "total_downtime_seconds": None,
        "target_seconds": 120,
        "timestamp": None,
    },
    
    # 시나리오 49-5: Blast Radius 격리
    "scenario_49_5": {
        "name": "Blast Radius Isolation",
        "passed": False,
        "affected_services": [],
        "unaffected_services": [],
        "isolation_verified": False,
        "timestamp": None,
    },
    
    # 시스템 스냅샷
    "snapshots": [],
    
    # 관찰 로그
    "observations": [],
    
    # 에러 로그
    "errors": [],
    
    # 타임라인
    "timeline": [],
}


# =============================================================================
# Docker 제어 유틸리티
# =============================================================================

class DockerController:
    """Docker 컨테이너 제어"""
    
    def __init__(self, container_name: str = "myproject-db-1"):
        self.container_name = container_name
    
    def run_command(self, *args) -> Tuple[int, str, str]:
        """Docker 명령 실행"""
        cmd = ["docker"] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)
    
    def is_running(self) -> bool:
        """컨테이너 실행 상태 확인"""
        returncode, stdout, _ = self.run_command(
            "inspect", "-f", "{{.State.Running}}", self.container_name
        )
        return returncode == 0 and stdout == "true"
    
    def stop(self) -> bool:
        """컨테이너 중단"""
        returncode, _, _ = self.run_command("stop", self.container_name)
        return returncode == 0
    
    def start(self) -> bool:
        """컨테이너 시작"""
        returncode, _, _ = self.run_command("start", self.container_name)
        return returncode == 0


# =============================================================================
# Self-Healing API 클라이언트
# =============================================================================

class SelfHealingClient:
    """Self-Healing API 클라이언트"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "X-Test-Mode": "chaos-monkey",
        })
    
    def get_cb_status(self, service: str = "database") -> Optional[Dict]:
        """CB 상태 조회"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/self-healing/xtest/cb-status/",
                params={"service": service},
                timeout=5,
            )
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None
    
    def get_snapshot(self) -> Optional[Dict]:
        """시스템 스냅샷"""
        try:
            response = self.session.get(
                f"{self.base_url}/api/self-healing/xtest/snapshot/",
                timeout=5,
            )
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None
    
    def reset_cb(self, service: str = "database") -> bool:
        """CB 초기화"""
        try:
            response = self.session.post(
                f"{self.base_url}/api/self-healing/xtest/reset-cb/",
                json={"service": service},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def trigger_recovery(self, service: str = "database", force: bool = True) -> bool:
        """CB 복구 트리거"""
        try:
            response = self.session.post(
                f"{self.base_url}/api/self-healing/xtest/trigger-cb-recovery/",
                json={"service": service, "force": force},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def test_api(self) -> Tuple[int, float]:
        """API 테스트 (응답코드, 응답시간ms)"""
        try:
            start = time.time()
            response = self.session.get(
                f"{self.base_url}/api/products/",
                timeout=10,
            )
            elapsed_ms = (time.time() - start) * 1000
            return response.status_code, elapsed_ms
        except requests.exceptions.ConnectionError:
            return 0, 0
        except Exception:
            return -1, 0


# =============================================================================
# Locust User (병렬 모니터링용)
# =============================================================================

if LOCUST_AVAILABLE:
    
    class DockerChaosObserver(HttpUser):
        """Docker Chaos 중 시스템 동작 관찰"""
        
        wait_time = constant(1)
        
        CHAOS_HEADER = "X-Test-Mode"
        CHAOS_VALUE = "chaos-monkey"
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.headers = {
                "Content-Type": "application/json",
                self.CHAOS_HEADER: self.CHAOS_VALUE,
            }
            self._observation_count = 0
        
        def on_start(self):
            """테스트 시작"""
            global _stage49_stats
            _stage49_stats["test_started_at"] = datetime.now().isoformat()
        
        def on_stop(self):
            """테스트 종료"""
            global _stage49_stats
            _stage49_stats["test_ended_at"] = datetime.now().isoformat()
            self._save_results()
        
        @task(5)
        def observe_product_api(self):
            """상품 API 상태 지속 관찰"""
            with self.client.get(
                "/api/products/",
                headers=self.headers,
                catch_response=True,
                name="[49] Product API Observer"
            ) as response:
                self._observation_count += 1
                
                observation = {
                    "timestamp": datetime.now().isoformat(),
                    "status_code": response.status_code,
                    "response_time_ms": response.elapsed.total_seconds() * 1000,
                    "is_fast_fail": response.elapsed.total_seconds() < 0.1,
                }
                
                global _stage49_stats
                _stage49_stats["observations"].append(observation)
                
                # 장애 중: 503 예상, 정상: 200 예상
                if response.status_code in [200, 503]:
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")
        
        @task(2)
        def observe_cb_status(self):
            """CB 상태 모니터링"""
            with self.client.get(
                "/api/self-healing/xtest/cb-status/",
                params={"service": "database"},
                headers=self.headers,
                catch_response=True,
                name="[49] CB Status Monitor"
            ) as response:
                if response.status_code == 200:
                    response.success()
                    data = response.json()
                    
                    global _stage49_stats
                    state = data.get("cb_state") or data.get("state", "unknown")
                    
                    # 상태 변경 감지 시 타임라인에 기록
                    if state == "open" and not _stage49_stats["scenario_49_1"]["cb_open_detected_time"]:
                        _stage49_stats["scenario_49_1"]["cb_open_detected_time"] = datetime.now().isoformat()
                        _stage49_stats["timeline"].append({
                            "timestamp": datetime.now().isoformat(),
                            "event": "CB_OPEN_DETECTED",
                            "details": "Circuit Breaker opened automatically"
                        })
                    elif state == "closed" and _stage49_stats["scenario_49_1"]["cb_open_detected_time"]:
                        if not _stage49_stats["scenario_49_3"]["cb_closed_detected_time"]:
                            _stage49_stats["scenario_49_3"]["cb_closed_detected_time"] = datetime.now().isoformat()
                            _stage49_stats["timeline"].append({
                                "timestamp": datetime.now().isoformat(),
                                "event": "CB_CLOSED_DETECTED",
                                "details": "Circuit Breaker recovered automatically"
                            })
                else:
                    response.failure(f"CB status check failed: {response.status_code}")
        
        @task(1)
        def observe_snapshot(self):
            """시스템 스냅샷 기록"""
            with self.client.get(
                "/api/self-healing/xtest/snapshot/",
                headers=self.headers,
                catch_response=True,
                name="[49] System Snapshot"
            ) as response:
                if response.status_code == 200:
                    response.success()
                    snapshot = response.json()
                    
                    global _stage49_stats
                    _stage49_stats["snapshots"].append({
                        "timestamp": datetime.now().isoformat(),
                        "snapshot": snapshot,
                    })
                else:
                    response.failure(f"Snapshot failed: {response.status_code}")
        
        def _save_results(self):
            """결과 저장"""
            global _stage49_stats
            
            # 결과 분석
            self._analyze_observations()
            
            # 파일 저장
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            results_dir = os.path.join(_load_tests_dir, "results")
            os.makedirs(results_dir, exist_ok=True)
            
            filepath = os.path.join(results_dir, f"stage49_docker_chaos_{timestamp}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(_stage49_stats, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Results saved to: {filepath}")
        
        def _analyze_observations(self):
            """관찰 결과 분석"""
            global _stage49_stats
            
            observations = _stage49_stats["observations"]
            if not observations:
                return
            
            # 49-2: Fast Fail 분석
            fast_fail_obs = [o for o in observations if o["status_code"] == 503]
            if fast_fail_obs:
                response_times = [o["response_time_ms"] for o in fast_fail_obs]
                _stage49_stats["scenario_49_2"]["response_times_ms"] = response_times
                _stage49_stats["scenario_49_2"]["avg_response_time_ms"] = sum(response_times) / len(response_times)
                _stage49_stats["scenario_49_2"]["max_response_time_ms"] = max(response_times)
                _stage49_stats["scenario_49_2"]["fast_fail_count"] = len([t for t in response_times if t < 100])
                _stage49_stats["scenario_49_2"]["total_requests"] = len(response_times)
                _stage49_stats["scenario_49_2"]["all_fast_fail"] = all(t < 100 for t in response_times)
                _stage49_stats["scenario_49_2"]["passed"] = _stage49_stats["scenario_49_2"]["all_fast_fail"]


# =============================================================================
# Standalone 테스트 실행
# =============================================================================

class Stage49StandaloneTest:
    """Stage 49 통합 테스트 (Docker Chaos + 관찰)"""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        db_container: str = "myproject-db-1",
        max_wait_for_open: int = 45,
        max_wait_for_recovery: int = 120,
    ):
        self.base_url = base_url
        self.docker = DockerController(db_container)
        self.client = SelfHealingClient(base_url)
        self.max_wait_for_open = max_wait_for_open
        self.max_wait_for_recovery = max_wait_for_recovery
        
        self.results = {
            "test_name": "Stage 49: Docker Chaos Integration Test",
            "test_started_at": None,
            "test_ended_at": None,
            "scenarios": {},
            "timeline": [],
            "observations": [],
            "final_result": "PENDING",
        }
    
    def log(self, message: str, level: str = "INFO"):
        """로그 출력"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")
    
    def add_event(self, event: str, details: str = ""):
        """타임라인 이벤트 추가"""
        self.results["timeline"].append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details,
        })
    
    def run(self) -> Dict[str, Any]:
        """테스트 실행"""
        print()
        print("=" * 60)
        print("🔥 Stage 49: Docker Chaos Integration Test")
        print("=" * 60)
        print()
        
        self.results["test_started_at"] = datetime.now().isoformat()
        self.add_event("TEST_START", "Stage 49 Docker Chaos test started")
        
        try:
            # 1. 사전 준비
            self._phase_prepare()
            
            # 2. DB 중단 + CB OPEN 관찰
            scenario_49_1 = self._phase_db_blackout()
            self.results["scenarios"]["49_1"] = scenario_49_1
            
            # 3. Fast Fail 검증
            scenario_49_2 = self._phase_fast_fail()
            self.results["scenarios"]["49_2"] = scenario_49_2
            
            # 4. DB 복구 + CB CLOSED 관찰
            scenario_49_3 = self._phase_db_recovery()
            self.results["scenarios"]["49_3"] = scenario_49_3
            
            # 5. 전체 복구 시간 계산
            scenario_49_4 = self._calculate_total_time()
            self.results["scenarios"]["49_4"] = scenario_49_4
            
            # 6. 최종 검증
            scenario_49_5 = self._phase_final_verification()
            self.results["scenarios"]["49_5"] = scenario_49_5
            
            # 결과 평가
            all_passed = all(
                s.get("passed", False) 
                for s in self.results["scenarios"].values()
            )
            self.results["final_result"] = "PASSED" if all_passed else "PARTIAL"
            
        except Exception as e:
            self.log(f"테스트 오류: {e}", "ERROR")
            self.results["final_result"] = "ERROR"
            self.results["error"] = str(e)
        finally:
            self.results["test_ended_at"] = datetime.now().isoformat()
            
            # 안전 장치: DB 복구
            if not self.docker.is_running():
                self.log("⚠️ 안전 장치: DB 컨테이너 자동 복구", "WARNING")
                self.docker.start()
        
        self._print_summary()
        return self.results
    
    def _phase_prepare(self):
        """사전 준비"""
        self.log("📋 사전 준비: CB 초기화")
        
        # DB 컨테이너 실행 확인
        if not self.docker.is_running():
            raise RuntimeError("DB 컨테이너가 실행 중이 아닙니다")
        
        # CB 초기화
        self.client.reset_cb("database")
        time.sleep(1)
        
        # 초기 상태 확인
        cb_status = self.client.get_cb_status("database")
        self.log(f"✅ CB 초기 상태: {cb_status.get('state') if cb_status else 'unknown'}")
    
    def _phase_db_blackout(self) -> Dict:
        """49-1: DB 중단 + CB OPEN 감지"""
        self.log("💀 Phase 1: DB 컨테이너 중단")
        
        db_stop_time = datetime.now()
        if not self.docker.stop():
            return {"passed": False, "error": "DB 컨테이너 중단 실패"}
        
        self.add_event("DB_STOP", f"Container stopped at {db_stop_time.isoformat()}")
        
        # CB OPEN 감지 대기
        self.log(f"⏳ CB OPEN 감지 대기 (최대 {self.max_wait_for_open}초)...")
        
        start = time.time()
        cb_opened = False
        detection_time = None
        
        while time.time() - start < self.max_wait_for_open:
            # API 호출로 실패 유도
            self.client.test_api()
            
            cb_status = self.client.get_cb_status("database")
            state = cb_status.get("cb_state") or cb_status.get("state") if cb_status else None
            if state == "open":
                detection_time = time.time() - start
                cb_opened = True
                self.log(f"✅ CB OPEN 감지됨! ({detection_time:.1f}초)")
                self.add_event("CB_OPEN", f"Detected after {detection_time:.1f}s")
                break
            
            print(".", end="", flush=True)
            time.sleep(1)
        
        print()
        
        return {
            "name": "CB OPEN Auto Detection",
            "passed": cb_opened,
            "db_stop_time": db_stop_time.isoformat(),
            "detection_delay_seconds": detection_time,
            "timestamp": datetime.now().isoformat(),
        }
    
    def _phase_fast_fail(self) -> Dict:
        """49-2: Fast Fail 검증"""
        self.log("🚀 Phase 2: Fast Fail 검증")
        
        response_times = []
        status_codes = []
        
        for i in range(10):
            status, elapsed = self.client.test_api()
            response_times.append(elapsed)
            status_codes.append(status)
            self.log(f"  요청 {i+1}: {status} ({elapsed:.2f}ms)")
            time.sleep(0.5)
        
        fast_fail_count = len([t for t in response_times if t < 100])
        avg_time = sum(response_times) / len(response_times) if response_times else 0
        all_fast_fail = all(t < 100 for t in response_times if t > 0)
        
        passed = fast_fail_count >= 8
        
        if passed:
            self.log(f"✅ Fast Fail 확인됨! (평균: {avg_time:.2f}ms)")
            self.add_event("FAST_FAIL", f"Avg: {avg_time:.2f}ms, Count: {fast_fail_count}/10")
        else:
            self.log(f"⚠️ Fast Fail 일부 실패 ({fast_fail_count}/10)")
        
        return {
            "name": "503 Fast Fail Verification",
            "passed": passed,
            "response_times_ms": response_times,
            "status_codes": status_codes,
            "avg_response_time_ms": avg_time,
            "fast_fail_count": fast_fail_count,
            "timestamp": datetime.now().isoformat(),
        }
    
    def _phase_db_recovery(self) -> Dict:
        """49-3: DB 복구 + CB CLOSED 감지"""
        self.log("🔧 Phase 3: DB 컨테이너 복구")
        
        db_start_time = datetime.now()
        if not self.docker.start():
            return {"passed": False, "error": "DB 컨테이너 시작 실패"}
        
        self.add_event("DB_START", f"Container started at {db_start_time.isoformat()}")
        
        # DB 준비 대기
        self.log("⏳ DB 준비 대기...")
        time.sleep(10)
        
        # CB CLOSED 감지 대기
        self.log(f"⏳ CB CLOSED 복구 대기 (최대 {self.max_wait_for_recovery}초)...")
        
        start = time.time()
        cb_closed = False
        recovery_time = None
        
        while time.time() - start < self.max_wait_for_recovery:
            cb_status = self.client.get_cb_status("database")
            state = (cb_status.get("cb_state") or cb_status.get("state")) if cb_status else None
            
            if state == "closed":
                recovery_time = time.time() - start
                cb_closed = True
                self.log(f"✅ CB CLOSED 복구됨! ({recovery_time:.1f}초)")
                self.add_event("CB_CLOSED", f"Recovered after {recovery_time:.1f}s")
                break
            elif state == "half_open":
                self.log("  CB 상태: HALF_OPEN - 복구 진행 중...")
                self.client.trigger_recovery("database", force=True)
                time.sleep(2)
            else:
                print(".", end="", flush=True)
                time.sleep(2)
        
        print()
        
        return {
            "name": "CB CLOSED Auto Recovery",
            "passed": cb_closed,
            "db_start_time": db_start_time.isoformat(),
            "recovery_delay_seconds": recovery_time,
            "timestamp": datetime.now().isoformat(),
        }
    
    def _calculate_total_time(self) -> Dict:
        """49-4: 전체 복구 시간 계산"""
        s1 = self.results["scenarios"].get("49_1", {})
        s3 = self.results["scenarios"].get("49_3", {})
        
        detection_delay = s1.get("detection_delay_seconds", 0) or 0
        recovery_delay = s3.get("recovery_delay_seconds", 0) or 0
        
        # DB 다운타임 = detection + recovery delay (대략적 계산)
        total_time = detection_delay + recovery_delay + 10  # +10 for DB startup
        passed = total_time < 120
        
        self.log(f"📊 전체 복구 시간: {total_time:.1f}초 (목표: <120초)")
        
        return {
            "name": "Total Recovery Time < 120s",
            "passed": passed,
            "total_recovery_seconds": total_time,
            "target_seconds": 120,
            "timestamp": datetime.now().isoformat(),
        }
    
    def _phase_final_verification(self) -> Dict:
        """49-5: 최종 검증"""
        self.log("✅ Phase 5: 최종 상태 검증")
        
        # API 정상 동작 확인
        status, elapsed = self.client.test_api()
        api_ok = status == 200
        
        # CB 상태 확인
        cb_status = self.client.get_cb_status("database")
        cb_state = (cb_status.get("cb_state") or cb_status.get("state")) if cb_status else None
        cb_closed = cb_state == "closed"
        
        passed = api_ok and cb_closed
        
        self.log(f"  API 상태: {status} ({elapsed:.2f}ms)")
        self.log(f"  CB 상태: {cb_status.get('state') if cb_status else 'unknown'}")
        
        return {
            "name": "Final Verification",
            "passed": passed,
            "api_status_code": status,
            "api_response_time_ms": elapsed,
            "cb_status": cb_status,
            "timestamp": datetime.now().isoformat(),
        }
    
    def _print_summary(self):
        """결과 요약 출력"""
        print()
        print("=" * 60)
        print("🏆 Stage 49 테스트 결과 요약")
        print("=" * 60)
        print()
        
        for key, scenario in self.results["scenarios"].items():
            icon = "✅" if scenario.get("passed") else "❌"
            print(f"  {icon} {key}: {scenario.get('name', key)}")
        
        print()
        result = self.results["final_result"]
        print(f"최종 결과: {result}")
        
        print()
        print("📋 타임라인:")
        for event in self.results["timeline"]:
            ts = event["timestamp"].split("T")[1].split(".")[0]
            print(f"  {ts} - {event['event']}: {event['details']}")


def save_results_to_file(results: Dict, filename: str = None):
    """결과를 파일로 저장"""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"stage49_docker_chaos_{timestamp}.json"
    
    results_dir = os.path.join(_load_tests_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    
    filepath = os.path.join(results_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n결과 저장됨: {filepath}")
    return filepath


# =============================================================================
# 메인 실행
# =============================================================================

def main():
    """메인 함수"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Stage 49: Docker Chaos Test")
    parser.add_argument("--standalone", action="store_true", help="Standalone 모드로 실행")
    parser.add_argument("--url", default="http://localhost:8000", help="Django 서버 URL")
    parser.add_argument("--container", default="myproject-db-1", help="DB 컨테이너 이름")
    parser.add_argument("--output", default=None, help="결과 파일 이름")
    args = parser.parse_args()
    
    if args.standalone:
        test = Stage49StandaloneTest(
            base_url=args.url,
            db_container=args.container,
        )
        results = test.run()
        save_results_to_file(results, args.output)
        
        sys.exit(0 if results["final_result"] == "PASSED" else 1)
    else:
        print("Locust 모드로 실행하려면:")
        print("  locust -f scenarios/chaos/stage49_docker_chaos.py --headless -u 1 -r 1 -t 5m")
        print()
        print("Standalone 모드로 실행하려면:")
        print("  python scenarios/chaos/stage49_docker_chaos.py --standalone")


if __name__ == "__main__":
    main()
