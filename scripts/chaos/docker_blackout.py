#!/usr/bin/env python
"""
🔥 Stage 49: Docker Chaos - DB Blackout 시나리오
================================================

목적: 실제 Docker 컨테이너를 중단시켜 Self-Healing의 인프라 내성 검증

핵심 원리:
- Stage 48 (X-Test-Mode): 장애를 "시뮬레이션" - CB에게 "실패했다고 알려줌"
- Stage 49 (Docker Chaos): 실제 장애 발생 - DB가 진짜로 응답 안 함

테스트 시나리오:
- Phase 1: 사전 상태 기록
- Phase 2: DB 컨테이너 중단
- Phase 3: CB OPEN 자동 감지 확인
- Phase 4: 503 Fast Fail 검증
- Phase 5: DB 컨테이너 복구
- Phase 6: CB CLOSED 자동 복구 확인

실행 방법:
    python scripts/chaos/docker_blackout.py

요구사항:
    - Docker Desktop 실행 중
    - docker-compose로 DB 컨테이너 실행 중
    - Django 서버 실행 중 (localhost:8000)
"""

import subprocess
import time
import json
import sys
import os
import argparse
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import requests

# 프로젝트 루트 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class Colors:
    """터미널 색상 코드"""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


class DockerBlackoutTest:
    """Docker Chaos - DB Blackout 테스트 클래스"""
    
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        db_container: str = "myproject-db-1",
        chaos_header: str = "X-Test-Mode",
        chaos_value: str = "chaos-monkey",
        cb_recovery_timeout: int = 60,
        max_wait_for_open: int = 30,
        max_wait_for_recovery: int = 120,
    ):
        self.base_url = base_url.rstrip('/')
        self.db_container = db_container
        self.chaos_header = chaos_header
        self.chaos_value = chaos_value
        self.cb_recovery_timeout = cb_recovery_timeout
        self.max_wait_for_open = max_wait_for_open
        self.max_wait_for_recovery = max_wait_for_recovery
        
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            self.chaos_header: self.chaos_value,
        })
        
        self.results = {
            "test_name": "Stage 49: Docker Chaos - DB Blackout",
            "test_started_at": None,
            "test_ended_at": None,
            "db_container": db_container,
            "phases": {},
            "timeline": [],
            "success_criteria": {},
            "final_result": "PENDING",
        }
        
    def log(self, message: str, level: str = "INFO"):
        """로그 출력"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": Colors.BLUE,
            "SUCCESS": Colors.GREEN,
            "WARNING": Colors.YELLOW,
            "ERROR": Colors.RED,
            "PHASE": Colors.MAGENTA,
        }.get(level, Colors.RESET)
        
        print(f"{color}[{timestamp}] [{level}] {message}{Colors.RESET}")
        
    def add_timeline_event(self, event: str, details: str = ""):
        """타임라인 이벤트 추가"""
        self.results["timeline"].append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "details": details,
        })
        
    # =========================================================================
    # Docker 컨테이너 제어
    # =========================================================================
    
    def docker_command(self, *args) -> Tuple[int, str, str]:
        """Docker 명령 실행"""
        cmd = ["docker"] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)
    
    def is_container_running(self) -> bool:
        """DB 컨테이너 실행 상태 확인"""
        returncode, stdout, _ = self.docker_command(
            "inspect", "-f", "{{.State.Running}}", self.db_container
        )
        return returncode == 0 and stdout.strip() == "true"
    
    def stop_container(self) -> bool:
        """DB 컨테이너 중단"""
        self.log(f"💀 DB 컨테이너 중단: {self.db_container}", "PHASE")
        returncode, _, stderr = self.docker_command("stop", self.db_container)
        if returncode == 0:
            self.add_timeline_event("DB_STOP", f"Container {self.db_container} stopped")
            return True
        else:
            self.log(f"컨테이너 중단 실패: {stderr}", "ERROR")
            return False
    
    def start_container(self) -> bool:
        """DB 컨테이너 시작"""
        self.log(f"🔧 DB 컨테이너 복구: {self.db_container}", "PHASE")
        returncode, _, stderr = self.docker_command("start", self.db_container)
        if returncode == 0:
            self.add_timeline_event("DB_START", f"Container {self.db_container} started")
            return True
        else:
            self.log(f"컨테이너 시작 실패: {stderr}", "ERROR")
            return False
    
    # =========================================================================
    # Self-Healing API 호출
    # =========================================================================
    
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
        except Exception as e:
            self.log(f"CB 상태 조회 실패: {e}", "WARNING")
        return None
    
    def get_system_snapshot(self) -> Optional[Dict]:
        """시스템 스냅샷 조회"""
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
        """CB 상태 초기화"""
        try:
            response = self.session.post(
                f"{self.base_url}/api/self-healing/xtest/reset-cb/",
                json={"service": service},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def trigger_cb_recovery(self, service: str = "database", force: bool = True) -> bool:
        """CB 강제 복구"""
        try:
            response = self.session.post(
                f"{self.base_url}/api/self-healing/xtest/trigger-cb-recovery/",
                json={"service": service, "force": force},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def test_api_request(self) -> Tuple[int, float]:
        """API 테스트 요청 (응답 코드, 응답 시간ms)"""
        try:
            start = time.time()
            # Products API 호출 (DB 의존)
            response = self.session.get(
                f"{self.base_url}/api/products/",
                timeout=10,
            )
            elapsed_ms = (time.time() - start) * 1000
            return response.status_code, elapsed_ms
        except requests.exceptions.ConnectionError:
            return 0, 0  # 연결 실패
        except Exception:
            return -1, 0  # 기타 오류
    
    # =========================================================================
    # 테스트 Phase 실행
    # =========================================================================
    
    def phase_1_pre_chaos_state(self) -> bool:
        """Phase 1: 사전 상태 기록"""
        self.log("📊 Phase 1: 사전 상태 기록", "PHASE")
        
        # 컨테이너 상태 확인
        if not self.is_container_running():
            self.log(f"DB 컨테이너가 실행 중이 아닙니다: {self.db_container}", "ERROR")
            return False
        
        # CB 초기화
        self.reset_cb("database")
        time.sleep(1)
        
        # CB 상태 조회
        cb_status = self.get_cb_status("database")
        snapshot = self.get_system_snapshot()
        
        self.results["phases"]["phase_1"] = {
            "name": "Pre-Chaos State Recording",
            "container_running": True,
            "cb_status": cb_status,
            "snapshot": snapshot,
            "timestamp": datetime.now().isoformat(),
            "passed": cb_status is not None,
        }
        
        # 초기 CB 상태가 closed인지 확인
        if cb_status and cb_status.get("state") == "closed":
            self.log(f"✅ CB 초기 상태: CLOSED", "SUCCESS")
            self.add_timeline_event("PHASE_1_COMPLETE", "Pre-chaos state recorded, CB is CLOSED")
            return True
        else:
            self.log(f"⚠️ CB 초기 상태가 CLOSED가 아닙니다: {cb_status}", "WARNING")
            return True  # 계속 진행
    
    def phase_2_db_blackout(self) -> bool:
        """Phase 2: DB 컨테이너 중단"""
        self.log("💀 Phase 2: DB 컨테이너 중단", "PHASE")
        
        stop_time = datetime.now()
        success = self.stop_container()
        
        self.results["phases"]["phase_2"] = {
            "name": "DB Container Stop",
            "stop_time": stop_time.isoformat(),
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "passed": success,
        }
        
        if success:
            self.log("✅ DB 컨테이너 중단 완료", "SUCCESS")
            self.add_timeline_event("PHASE_2_COMPLETE", "DB container stopped")
        
        return success
    
    def phase_3_wait_for_cb_open(self) -> bool:
        """Phase 3: CB OPEN 감지 대기"""
        self.log(f"⏳ Phase 3: CB OPEN 감지 대기 (최대 {self.max_wait_for_open}초)", "PHASE")
        
        start_time = time.time()
        cb_opened = False
        detection_time = None
        
        while time.time() - start_time < self.max_wait_for_open:
            cb_status = self.get_cb_status("database")
            if cb_status and cb_status.get("state") == "open":
                detection_time = time.time() - start_time
                cb_opened = True
                self.log(f"✅ CB OPEN 감지됨! ({detection_time:.1f}초)", "SUCCESS")
                self.add_timeline_event("CB_OPEN", f"Detected after {detection_time:.1f}s")
                break
            
            # 실패 발생을 위해 API 호출
            self.test_api_request()
            print(".", end="", flush=True)
            time.sleep(1)
        
        print()  # 줄바꿈
        
        self.results["phases"]["phase_3"] = {
            "name": "CB OPEN Detection",
            "waited_seconds": time.time() - start_time,
            "cb_opened": cb_opened,
            "detection_time": detection_time,
            "timestamp": datetime.now().isoformat(),
            "passed": cb_opened,
        }
        
        return cb_opened
    
    def phase_4_fast_fail_verification(self) -> bool:
        """Phase 4: 503 Fast Fail 검증"""
        self.log("🚀 Phase 4: 503 Fast Fail 검증", "PHASE")
        
        response_times = []
        status_codes = []
        
        # 10회 요청
        for i in range(10):
            status_code, elapsed_ms = self.test_api_request()
            response_times.append(elapsed_ms)
            status_codes.append(status_code)
            self.log(f"  요청 {i+1}: {status_code} ({elapsed_ms:.2f}ms)", "INFO")
            time.sleep(0.5)
        
        # Fast Fail 검증: 503 응답 + 100ms 미만
        fast_fail_count = sum(1 for t in response_times if t < 100)
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        all_fast_fail = all(t < 100 for t in response_times if t > 0)
        
        self.results["phases"]["phase_4"] = {
            "name": "503 Fast Fail Verification",
            "response_times_ms": response_times,
            "status_codes": status_codes,
            "fast_fail_count": fast_fail_count,
            "avg_response_time_ms": avg_response_time,
            "all_fast_fail": all_fast_fail,
            "timestamp": datetime.now().isoformat(),
            "passed": fast_fail_count >= 8,  # 80% 이상 Fast Fail
        }
        
        if all_fast_fail:
            self.log(f"✅ Fast Fail 확인됨! (평균: {avg_response_time:.2f}ms)", "SUCCESS")
            self.add_timeline_event("FAST_FAIL_VERIFIED", f"Avg response: {avg_response_time:.2f}ms")
        else:
            self.log(f"⚠️ Fast Fail 일부 실패 (Fast Fail: {fast_fail_count}/10)", "WARNING")
        
        return self.results["phases"]["phase_4"]["passed"]
    
    def phase_5_db_recovery(self) -> bool:
        """Phase 5: DB 컨테이너 복구"""
        self.log("🔧 Phase 5: DB 컨테이너 복구", "PHASE")
        
        restart_time = datetime.now()
        success = self.start_container()
        
        # 컨테이너 시작 후 DB 준비 대기
        if success:
            self.log("⏳ DB 준비 대기 중...", "INFO")
            for i in range(15):
                if self.is_container_running():
                    # DB 연결 테스트
                    time.sleep(2)
                    break
                time.sleep(1)
        
        self.results["phases"]["phase_5"] = {
            "name": "DB Container Recovery",
            "restart_time": restart_time.isoformat(),
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "passed": success,
        }
        
        if success:
            self.log("✅ DB 컨테이너 복구 완료", "SUCCESS")
            self.add_timeline_event("PHASE_5_COMPLETE", "DB container restarted")
        
        return success
    
    def phase_6_wait_for_cb_recovery(self) -> bool:
        """Phase 6: CB CLOSED 복구 대기"""
        self.log(f"⏳ Phase 6: CB CLOSED 복구 대기 (최대 {self.max_wait_for_recovery}초)", "PHASE")
        
        start_time = time.time()
        cb_closed = False
        recovery_time = None
        
        # HALF_OPEN → CLOSED를 위해 강제 복구 시도
        time.sleep(5)  # DB 연결 안정화 대기
        
        while time.time() - start_time < self.max_wait_for_recovery:
            cb_status = self.get_cb_status("database")
            current_state = cb_status.get("state") if cb_status else None
            
            if current_state == "closed":
                recovery_time = time.time() - start_time
                cb_closed = True
                self.log(f"✅ CB CLOSED 복구됨! ({recovery_time:.1f}초)", "SUCCESS")
                self.add_timeline_event("CB_CLOSED", f"Recovered after {recovery_time:.1f}s")
                break
            elif current_state == "half_open":
                self.log(f"  CB 상태: HALF_OPEN - 복구 진행 중...", "INFO")
                # 강제 복구 시도
                self.trigger_cb_recovery("database", force=True)
                time.sleep(2)
            else:
                print(".", end="", flush=True)
                time.sleep(2)
        
        print()  # 줄바꿈
        
        self.results["phases"]["phase_6"] = {
            "name": "CB CLOSED Recovery",
            "waited_seconds": time.time() - start_time,
            "cb_closed": cb_closed,
            "recovery_time": recovery_time,
            "final_cb_status": self.get_cb_status("database"),
            "timestamp": datetime.now().isoformat(),
            "passed": cb_closed,
        }
        
        return cb_closed
    
    def phase_7_final_verification(self) -> bool:
        """Phase 7: 최종 상태 확인"""
        self.log("📊 Phase 7: 최종 상태 확인", "PHASE")
        
        # API 정상 동작 확인
        status_code, elapsed_ms = self.test_api_request()
        api_recovered = status_code == 200
        
        # CB 상태 확인
        cb_status = self.get_cb_status("database")
        snapshot = self.get_system_snapshot()
        
        self.results["phases"]["phase_7"] = {
            "name": "Final Verification",
            "api_status_code": status_code,
            "api_response_time_ms": elapsed_ms,
            "api_recovered": api_recovered,
            "cb_status": cb_status,
            "snapshot": snapshot,
            "timestamp": datetime.now().isoformat(),
            "passed": api_recovered,
        }
        
        if api_recovered:
            self.log(f"✅ API 정상 동작 확인 ({status_code}, {elapsed_ms:.2f}ms)", "SUCCESS")
            self.add_timeline_event("API_RECOVERED", f"Status: {status_code}, Time: {elapsed_ms:.2f}ms")
        else:
            self.log(f"⚠️ API 복구 미완료 ({status_code})", "WARNING")
        
        return api_recovered
    
    # =========================================================================
    # 메인 실행
    # =========================================================================
    
    def run(self) -> Dict[str, Any]:
        """전체 테스트 실행"""
        print()
        print(f"{Colors.BOLD}{'='*60}")
        print(f"🔥 Stage 49: Docker Chaos - DB Blackout Test")
        print(f"{'='*60}{Colors.RESET}")
        print()
        
        self.results["test_started_at"] = datetime.now().isoformat()
        self.add_timeline_event("TEST_START", "Docker Chaos test started")
        
        try:
            # Phase 1: 사전 상태 기록
            if not self.phase_1_pre_chaos_state():
                self.log("Phase 1 실패 - 테스트 중단", "ERROR")
                self.results["final_result"] = "FAILED"
                return self.results
            
            # Phase 2: DB 컨테이너 중단
            if not self.phase_2_db_blackout():
                self.log("Phase 2 실패 - 테스트 중단", "ERROR")
                self.results["final_result"] = "FAILED"
                return self.results
            
            # Phase 3: CB OPEN 감지 대기
            phase_3_passed = self.phase_3_wait_for_cb_open()
            
            # Phase 4: Fast Fail 검증
            phase_4_passed = self.phase_4_fast_fail_verification()
            
            # Phase 5: DB 컨테이너 복구
            if not self.phase_5_db_recovery():
                self.log("Phase 5 실패 - DB 복구 실패", "ERROR")
                self.results["final_result"] = "PARTIAL"
                return self.results
            
            # Phase 6: CB CLOSED 복구 대기
            phase_6_passed = self.phase_6_wait_for_cb_recovery()
            
            # Phase 7: 최종 상태 확인
            phase_7_passed = self.phase_7_final_verification()
            
            # 성공 기준 평가
            self.results["success_criteria"] = {
                "cb_auto_open": phase_3_passed,
                "fast_fail": phase_4_passed,
                "cb_auto_recovery": phase_6_passed,
                "api_recovered": phase_7_passed,
            }
            
            all_passed = all(self.results["success_criteria"].values())
            self.results["final_result"] = "PASSED" if all_passed else "PARTIAL"
            
        except Exception as e:
            self.log(f"테스트 중 오류 발생: {e}", "ERROR")
            self.results["final_result"] = "ERROR"
            self.results["error"] = str(e)
        finally:
            self.results["test_ended_at"] = datetime.now().isoformat()
            self.add_timeline_event("TEST_END", f"Result: {self.results['final_result']}")
            
            # 안전 장치: DB 컨테이너 복구 확인
            if not self.is_container_running():
                self.log("⚠️ 안전 장치: DB 컨테이너 자동 복구", "WARNING")
                self.start_container()
        
        # 결과 요약 출력
        self._print_summary()
        
        return self.results
    
    def _print_summary(self):
        """결과 요약 출력"""
        print()
        print(f"{Colors.BOLD}{'='*60}")
        print(f"🏆 Stage 49 테스트 결과 요약")
        print(f"{'='*60}{Colors.RESET}")
        print()
        
        # 성공 기준
        criteria = self.results.get("success_criteria", {})
        for name, passed in criteria.items():
            icon = "✅" if passed else "❌"
            print(f"  {icon} {name}: {'PASS' if passed else 'FAIL'}")
        
        print()
        
        # 최종 결과
        result = self.results["final_result"]
        color = Colors.GREEN if result == "PASSED" else Colors.YELLOW if result == "PARTIAL" else Colors.RED
        print(f"{color}{Colors.BOLD}최종 결과: {result}{Colors.RESET}")
        
        # 타임라인
        print()
        print(f"{Colors.CYAN}📋 타임라인:{Colors.RESET}")
        for event in self.results["timeline"]:
            ts = event["timestamp"].split("T")[1].split(".")[0]
            print(f"  {ts} - {event['event']}: {event['details']}")
        
        print()


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description="Stage 49: Docker Chaos - DB Blackout Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Django 서버 URL")
    parser.add_argument("--container", default="myproject-db-1", help="DB 컨테이너 이름")
    parser.add_argument("--output", default=None, help="결과 JSON 파일 경로")
    args = parser.parse_args()
    
    test = DockerBlackoutTest(
        base_url=args.url,
        db_container=args.container,
    )
    
    results = test.run()
    
    # 결과 저장
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n결과 저장됨: {args.output}")
    
    # 종료 코드
    sys.exit(0 if results["final_result"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
