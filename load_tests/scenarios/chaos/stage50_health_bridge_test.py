#!/usr/bin/env python3
"""
Stage 50: Health Bridge Observability Test

테스트 목표:
- DB Blackout 중에도 /health/l3 (Health Bridge) 응답 확인
- Worker Saturation 방지 검증
- CB 스냅샷 관찰 가능성 확인

테스트 시나리오:
1. Phase 1: Baseline - 정상 상태에서 Health Bridge 응답 확인
2. Phase 2: Pre-Blackout - CB 스냅샷 갱신 확인
3. Phase 3: DB Blackout - DB 정지
4. Phase 4: Bridge Test - /health/l3 응답 확인 (핵심!)
5. Phase 5: Recovery - DB 재시작
6. Phase 6: Post-Recovery - Health Bridge 정상 복귀 확인

Usage:
    python scripts/chaos/stage50_health_bridge_test.py
"""

import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests


@dataclass
class PhaseResult:
    """테스트 페이즈 결과"""
    phase_name: str
    success: bool
    duration_ms: float
    details: dict = field(default_factory=dict)
    error: Optional[str] = None


class HealthBridgeTest:
    """Stage 50 Health Bridge 테스트"""
    
    BASE_URL = "http://localhost:8000"
    BRIDGE_PATH = "/api/self-healing/health/l3/"
    HEALTH_PATH = "/api/self-healing/health/"
    DB_CONTAINER = "myproject-db-1"
    
    def __init__(self):
        self.results: list[PhaseResult] = []
        self.test_start = datetime.now()
        
    def log(self, msg: str):
        """로그 출력"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] {msg}")
        
    def docker_cmd(self, *args) -> tuple[bool, str]:
        """Docker 명령 실행"""
        cmd = ["docker"] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)
            
    def request_bridge(self, timeout: float = 3.0) -> tuple[Optional[int], Optional[dict], float]:
        """Health Bridge 요청"""
        start = time.time()
        try:
            resp = requests.get(
                f"{self.BASE_URL}{self.BRIDGE_PATH}",
                timeout=timeout
            )
            duration = (time.time() - start) * 1000
            return resp.status_code, resp.json(), duration
        except requests.exceptions.Timeout:
            duration = (time.time() - start) * 1000
            return None, {"error": "timeout"}, duration
        except requests.exceptions.ConnectionError:
            duration = (time.time() - start) * 1000
            return None, {"error": "connection_refused"}, duration
        except Exception as e:
            duration = (time.time() - start) * 1000
            return None, {"error": str(e)}, duration
            
    def request_health(self, timeout: float = 3.0) -> tuple[Optional[int], Optional[dict], float]:
        """Normal Health 요청 (DB 의존)"""
        start = time.time()
        try:
            resp = requests.get(
                f"{self.BASE_URL}{self.HEALTH_PATH}",
                timeout=timeout
            )
            duration = (time.time() - start) * 1000
            return resp.status_code, resp.json(), duration
        except requests.exceptions.Timeout:
            duration = (time.time() - start) * 1000
            return None, {"error": "timeout"}, duration
        except requests.exceptions.ConnectionError:
            duration = (time.time() - start) * 1000
            return None, {"error": "connection_refused"}, duration
        except Exception as e:
            duration = (time.time() - start) * 1000
            return None, {"error": str(e)}, duration
            
    # =========================================================================
    # Test Phases
    # =========================================================================
    
    def phase_1_baseline(self) -> PhaseResult:
        """Phase 1: Baseline 정상 상태 확인"""
        self.log("=" * 60)
        self.log("Phase 1: Baseline - 정상 상태 Health Bridge 확인")
        self.log("=" * 60)
        
        start = time.time()
        
        # Health Bridge 요청
        status, data, duration = self.request_bridge()
        
        success = status == 200 and data.get("status") == "bridge_active"
        
        self.log(f"  Bridge Status: {status}")
        self.log(f"  Response Time: {duration:.2f}ms")
        self.log(f"  Data: {data}")
        
        return PhaseResult(
            phase_name="Baseline",
            success=success,
            duration_ms=duration,
            details={
                "status_code": status,
                "bridge_status": data.get("status") if data else None,
                "cb_states": data.get("circuit_breakers", {}) if data else {},
            }
        )
        
    def phase_2_pre_blackout(self) -> PhaseResult:
        """Phase 2: Pre-Blackout - CB 스냅샷 갱신 확인"""
        self.log("=" * 60)
        self.log("Phase 2: Pre-Blackout - CB 스냅샷 갱신 확인")
        self.log("=" * 60)
        
        start = time.time()
        
        # 일반 API 요청으로 스냅샷 갱신 유도 (Bridge 요청만으로는 갱신 안됨)
        for i in range(3):
            # 일반 요청 (스냅샷 갱신 트리거)
            try:
                requests.get(f"{self.BASE_URL}/api/products/", timeout=3)
            except:
                pass
            
            # Bridge 상태 확인
            status, data, duration = self.request_bridge()
            update_count = data.get("snapshot", {}).get("update_count", 0) if data else 0
            self.log(f"  Request {i+1}: status={status}, update_count={update_count}")
            time.sleep(0.5)
            
        # 최종 상태 확인
        status, data, duration = self.request_bridge()
        
        snapshot = data.get("snapshot", {}) if data else {}
        success = snapshot.get("update_count", 0) > 0
        
        self.log(f"  Final Snapshot: {snapshot}")
        
        return PhaseResult(
            phase_name="Pre-Blackout",
            success=success,
            duration_ms=(time.time() - start) * 1000,
            details={
                "update_count": snapshot.get("update_count", 0),
                "last_updated": snapshot.get("last_updated"),
            }
        )
        
    def phase_3_db_blackout(self) -> PhaseResult:
        """Phase 3: DB Blackout - DB 컨테이너 정지"""
        self.log("=" * 60)
        self.log("Phase 3: DB Blackout - DB 컨테이너 정지")
        self.log("=" * 60)
        
        start = time.time()
        
        # 마지막 스냅샷 기록
        _, data, _ = self.request_bridge()
        pre_snapshot = data.get("snapshot", {}) if data else {}
        self.log(f"  Pre-Blackout Snapshot: {pre_snapshot}")
        
        # DB 정지
        success, output = self.docker_cmd("stop", self.DB_CONTAINER)
        duration = (time.time() - start) * 1000
        
        self.log(f"  Docker Stop: {'SUCCESS' if success else 'FAILED'}")
        
        # 안정화 대기
        time.sleep(2)
        
        return PhaseResult(
            phase_name="DB Blackout",
            success=success,
            duration_ms=duration,
            details={
                "docker_output": output.strip(),
                "pre_blackout_snapshot": pre_snapshot,
            }
        )
        
    def phase_4_bridge_test(self) -> PhaseResult:
        """Phase 4: Bridge Test - DB 죽은 상태에서 /health/l3 응답 확인 (핵심!)"""
        self.log("=" * 60)
        self.log("Phase 4: Bridge Test - DB 죽은 상태에서 Health Bridge 확인")
        self.log("=" * 60)
        
        start = time.time()
        results = []
        
        # 5회 요청
        for i in range(5):
            status, data, duration = self.request_bridge(timeout=2.0)
            results.append({
                "attempt": i + 1,
                "status": status,
                "duration_ms": duration,
                "bridge_status": data.get("status") if data else None,
                "snapshot_age": data.get("snapshot", {}).get("age_seconds") if data else None,
            })
            self.log(f"  Attempt {i+1}: status={status}, duration={duration:.2f}ms")
            time.sleep(0.5)
            
        # Normal Health 요청 (DB 의존 - 실패 예상)
        normal_status, normal_data, normal_duration = self.request_health(timeout=3.0)
        self.log(f"  Normal Health: status={normal_status}, duration={normal_duration:.2f}ms")
        
        # 성공 조건: Bridge 요청 중 최소 3개 성공
        success_count = sum(1 for r in results if r["status"] == 200)
        success = success_count >= 3
        
        total_duration = (time.time() - start) * 1000
        
        return PhaseResult(
            phase_name="Bridge Test (DB Blackout)",
            success=success,
            duration_ms=total_duration,
            details={
                "bridge_requests": results,
                "success_count": success_count,
                "normal_health": {
                    "status": normal_status,
                    "duration_ms": normal_duration,
                    "error": normal_data.get("error") if normal_data else None,
                },
            }
        )
        
    def phase_5_recovery(self) -> PhaseResult:
        """Phase 5: Recovery - DB 재시작"""
        self.log("=" * 60)
        self.log("Phase 5: Recovery - DB 컨테이너 재시작")
        self.log("=" * 60)
        
        start = time.time()
        
        # DB 시작
        success, output = self.docker_cmd("start", self.DB_CONTAINER)
        
        self.log(f"  Docker Start: {'SUCCESS' if success else 'FAILED'}")
        
        # DB 준비 대기
        self.log("  Waiting for DB to be ready...")
        time.sleep(5)
        
        duration = (time.time() - start) * 1000
        
        return PhaseResult(
            phase_name="Recovery",
            success=success,
            duration_ms=duration,
            details={"docker_output": output.strip()}
        )
        
    def phase_6_post_recovery(self) -> PhaseResult:
        """Phase 6: Post-Recovery - 정상 복귀 확인"""
        self.log("=" * 60)
        self.log("Phase 6: Post-Recovery - 정상 복귀 확인")
        self.log("=" * 60)
        
        start = time.time()
        
        # Bridge 요청
        bridge_status, bridge_data, bridge_duration = self.request_bridge()
        self.log(f"  Bridge: status={bridge_status}, duration={bridge_duration:.2f}ms")
        
        # Normal Health 요청
        normal_status, normal_data, normal_duration = self.request_health()
        self.log(f"  Normal Health: status={normal_status}, duration={normal_duration:.2f}ms")
        
        # 스냅샷 갱신 확인
        new_snapshot = bridge_data.get("snapshot", {}) if bridge_data else {}
        self.log(f"  New Snapshot: {new_snapshot}")
        
        success = bridge_status == 200 and normal_status == 200
        
        total_duration = (time.time() - start) * 1000
        
        return PhaseResult(
            phase_name="Post-Recovery",
            success=success,
            duration_ms=total_duration,
            details={
                "bridge_status": bridge_status,
                "normal_health_status": normal_status,
                "snapshot": new_snapshot,
            }
        )
        
    # =========================================================================
    # Main
    # =========================================================================
    
    def run_all_phases(self):
        """모든 페이즈 실행"""
        self.log("=" * 60)
        self.log("Stage 50: Health Bridge Observability Test")
        self.log(f"Started: {self.test_start.isoformat()}")
        self.log("=" * 60)
        
        phases = [
            self.phase_1_baseline,
            self.phase_2_pre_blackout,
            self.phase_3_db_blackout,
            self.phase_4_bridge_test,
            self.phase_5_recovery,
            self.phase_6_post_recovery,
        ]
        
        for phase_fn in phases:
            try:
                result = phase_fn()
                self.results.append(result)
                
                status_icon = "✅" if result.success else "❌"
                self.log(f"\n{status_icon} {result.phase_name}: {'PASS' if result.success else 'FAIL'}")
                
                if not result.success and result.phase_name in ["DB Blackout", "Recovery"]:
                    self.log("  ⚠️  Critical phase failed, stopping test")
                    break
                    
            except Exception as e:
                self.results.append(PhaseResult(
                    phase_name=phase_fn.__name__,
                    success=False,
                    duration_ms=0,
                    error=str(e)
                ))
                self.log(f"  ❌ Exception: {e}")
                break
                
        self.print_summary()
        
    def print_summary(self):
        """결과 요약 출력"""
        self.log("\n" + "=" * 60)
        self.log("TEST SUMMARY")
        self.log("=" * 60)
        
        success_count = sum(1 for r in self.results if r.success)
        total_count = len(self.results)
        
        for result in self.results:
            icon = "✅" if result.success else "❌"
            self.log(f"  {icon} {result.phase_name}: {result.duration_ms:.2f}ms")
            
        self.log("-" * 60)
        self.log(f"Result: {success_count}/{total_count} phases passed")
        
        # 핵심 테스트 결과 (Phase 4)
        phase4 = next((r for r in self.results if "Bridge Test" in r.phase_name), None)
        if phase4:
            self.log("\n🎯 CRITICAL TEST (Phase 4 - Bridge During Blackout):")
            self.log(f"   Result: {'PASS ✅' if phase4.success else 'FAIL ❌'}")
            if phase4.details:
                self.log(f"   Success Count: {phase4.details.get('success_count', 0)}/5")
                normal = phase4.details.get("normal_health", {})
                self.log(f"   Normal Health (expected fail): status={normal.get('status')}")
                
        overall_success = success_count == total_count
        self.log(f"\n{'🎉 ALL TESTS PASSED!' if overall_success else '⚠️  SOME TESTS FAILED'}")
        
        return overall_success


if __name__ == "__main__":
    test = HealthBridgeTest()
    success = test.run_all_phases()
    sys.exit(0 if success else 1)
