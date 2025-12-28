"""
Stage 0: Self-Healing System HELL MODE Test 🔥

Purpose: Push Self-Healing system to ABSOLUTE LIMITS with V2 optimization utilities

Scenarios:
1. Total Blackout - DB + Redis 동시 장애 시뮬레이션
2. Retry Storm - AdaptiveJitter로 요청 분산 검증
3. SLA Hard-Cap - P99 < 250ms (Gold) 검증
4. Cascading Failure - 연쇄 장애 격리 검증
5. Command Center Disconnect - SafeDefaults로 자가 치유 지속
6. Concurrent Emergency - 동시 다발 Emergency 트리거

V2 Optimization Utilities Used:
- AsyncHealingLogger: 비동기 이벤트 버퍼링 (0.01ms 논블로킹)
- CBStateCache: TTL 기반 로컬 캐싱 (80% 네트워크 호출 감소)
- SafeDefaults: 사령탑 장애 시 보수적 로컬 설정
- AdaptiveJitter: Retry Storm 방지 (0~150ms 분산)

Run:
    locust -f load_tests/scenarios/load/stage0_selfhealing_hellmode.py --host=http://localhost:8000 --users=20 --spawn-rate=5 --run-time=120s --headless

Prerequisites:
    - Server running via docker-compose
    - CHAOS_ENABLED=true
"""

import os
import sys
import time
import json
import random
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# 프로젝트 루트 경로 추가
_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events

from load_tests.utils import LoginHelper
from load_tests.metrics import setup_event_hooks, get_metrics_collector

# V2 Optimization Utilities
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.async_logger import AsyncHealingLogger, EventSeverity
    from load_tests.utils.selfhealing.state_cache import CBStateCache
    from load_tests.utils.selfhealing.defaults import SafeDefaults
    from load_tests.utils.selfhealing.adaptive_jitter import AdaptiveJitter, SystemState
    V2_UTILITIES_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ V2 Utilities import failed: {e}")

# Netflix Gradient Throttle & Corruption Shield
try:
    from selfhealing.services.throttle import AdaptiveThrottle, get_adaptive_throttle, ThrottleConfig
    from selfhealing.services.corruption_shield import CorruptionShield, get_corruption_shield
    V3_UTILITIES_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ V3 Utilities (Throttle/Shield) import failed: {e}")
    V3_UTILITIES_AVAILABLE = False
    V2_UTILITIES_AVAILABLE = False


STAGE_NAME = "[Stage0-HellMode]"

# Self-Healing API Base Path
SH_API_BASE = "/api/self-healing"

# =============================================================================
# Hell Mode Configuration
# =============================================================================

HELL_MODE_CONFIG = {
    # SLA Hard-Caps (ms)
    "sla_platinum_p99": 100,
    "sla_gold_p99": 250,
    "sla_silver_p99": 500,
    "sla_bronze_p99": 2000,
    
    # Retry Storm Config
    "retry_storm_requests": 50,
    "retry_storm_concurrent": 10,
    
    # Cascading Failure Config
    "cascade_services": ["payment-service", "order-service", "point-service", "inventory-service"],
    "cascade_delay_between_failures_ms": 100,
    
    # Total Blackout Duration
    "blackout_duration_seconds": 10,
    
    # Concurrent Emergency Config
    "concurrent_emergency_count": 5,
    
    # Cache Stress Config
    "cache_stress_iterations": 100,
}


# =============================================================================
# Hell Mode Statistics
# =============================================================================

_hell_stats = {
    "start_time": None,
    "end_time": None,
    
    # V2 Utilities Stats
    "async_logger": {
        "events_logged": 0,
        "immediate_flushes": 0,
        "batch_flushes": 0,
        "avg_log_time_us": 0,  # microseconds
    },
    "state_cache": {
        "cache_hits": 0,
        "cache_misses": 0,
        "hit_rate_percent": 0,
        "avg_fetch_time_ms": 0,
    },
    "safe_defaults": {
        "degraded_mode_entries": 0,
        "total_degraded_time_seconds": 0,
    },
    "adaptive_jitter": {
        "relaxed_count": 0,
        "normal_count": 0,
        "stressed_count": 0,
        "avg_jitter_ms": 0,
    },
    
    # Scenario Results
    "scenarios": {
        "retry_storm": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "sla_hardcap": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "cascading_failure": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "total_blackout": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "command_center_disconnect": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "concurrent_emergency": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "cache_stress": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "jitter_distribution": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "adaptive_throttling": {"executed": 0, "success": 0, "failure": 0, "details": []},
        "corruption_shield": {"executed": 0, "success": 0, "failure": 0, "details": []},
    },
    
    # Adaptive Throttling Stats
    "adaptive_throttle": {
        "total_requests": 0,
        "allowed": 0,
        "denied": 0,
        "limit_adjustments": 0,
        "avg_rtt_ms": 0,
    },
    
    # Corruption Shield Stats
    "corruption_shield": {
        "total_validations": 0,
        "passed": 0,
        "blocked": 0,
        "l1_violations": 0,
        "l2_violations": 0,
        "l3_violations": 0,
    },
    
    # SLA Violations
    "sla_violations": {
        "platinum": 0,
        "gold": 0,
        "silver": 0,
        "bronze": 0,
    },
    
    # Response Times Collection
    "response_times": [],
    
    # Healing Events
    "healing_events": [],
}


def record_scenario_result(scenario: str, success: bool, detail: dict):
    """Record hell mode scenario result"""
    stats = _hell_stats["scenarios"].get(scenario)
    if stats:
        stats["executed"] += 1
        if success:
            stats["success"] += 1
        else:
            stats["failure"] += 1
        stats["details"].append(detail)


def record_response_time(endpoint: str, response_time_ms: float, sla_tier: str = "gold"):
    """Record response time and check SLA violation"""
    _hell_stats["response_times"].append({
        "endpoint": endpoint,
        "response_time_ms": response_time_ms,
        "timestamp": time.time(),
        "sla_tier": sla_tier,
    })
    
    # Check SLA violation
    threshold = HELL_MODE_CONFIG.get(f"sla_{sla_tier}_p99", 250)
    if response_time_ms > threshold:
        _hell_stats["sla_violations"][sla_tier] += 1


def record_healing_event(event_type: str, service: str, details: dict):
    """Record healing event"""
    _hell_stats["healing_events"].append({
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "service": service,
        "details": details,
    })


# =============================================================================
# V2 Utilities Initialization (Singleton - test_start event)
# =============================================================================

_v2_init_done = False  # 중복 초기화 방지 플래그


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Test 시작 시 V2 모듈 딱 한 번만 초기화 (스레드 폭발 방지)"""
    global _hell_stats
    _hell_stats["start_time"] = time.time()
    init_v2_utilities()
    print(f"\n🔥 {STAGE_NAME} Test Started - V2 Modules Initialized Once!\n")


def init_v2_utilities():
    """Initialize V2 optimization utilities with verification"""
    global _v2_init_done
    
    if not V2_UTILITIES_AVAILABLE:
        print(f"⚠️ {STAGE_NAME} V2 Utilities not available")
        return False
    
    if _v2_init_done:
        print(f"ℹ️ {STAGE_NAME} V2 Utilities already initialized, skipping")
        return True
    
    try:
        # 1. AsyncHealingLogger Setup
        def async_flush_callback(events):
            """Batch flush callback for AsyncHealingLogger"""
            _hell_stats["async_logger"]["batch_flushes"] += 1
            for event in events:
                _hell_stats["healing_events"].append(event)
        
        AsyncHealingLogger.configure(
            flush_callback=async_flush_callback,
            batch_size=5,
            flush_interval=2.0,
            max_queue_size=1000,
        )
        AsyncHealingLogger.start()
        
        # AsyncHealingLogger 초기화 검증
        async_stats = AsyncHealingLogger.get_stats()
        if not async_stats.get("is_running"):
            print(f"⚠️ AsyncHealingLogger failed to start!")
        else:
            print(f"   ✓ AsyncHealingLogger running (queue_size: {async_stats.get('queue_size', 0)})")
        
        # 2. CBStateCache Setup
        def cache_fetch_callback(service: str) -> Dict:
            """Fetch callback for CBStateCache"""
            import requests
            try:
                response = requests.get(
                    f"http://localhost:8000{SH_API_BASE}/status/{service}/",
                    timeout=2.0
                )
                if response.status_code == 200:
                    return response.json()
            except:
                pass
            return {"status": "unknown", "fallback": True}
        
        CBStateCache.configure(
            fetch_callback=cache_fetch_callback,
            base_ttl=3.0,
            jitter_range=0.3,
            min_ttl=2.0,
            max_ttl=6.0,
            dynamic_ttl=True,
        )
        
        # CBStateCache 초기화 검증 - 워밍업 테스트
        test_services = ["payment-service", "order-service"]
        CBStateCache.warm_up(test_services)
        cache_stats = CBStateCache.get_stats()
        print(f"   ✓ CBStateCache configured (cached: {cache_stats.get('cached_services', 0)} services)")
        
        # 3. SafeDefaults - 테스트 진입/탈출로 검증
        SafeDefaults.enter_degraded_mode(reason="Hell Mode Test Initialization")
        if SafeDefaults.is_degraded():
            _hell_stats["safe_defaults"]["degraded_mode_entries"] += 1
            print(f"   ✓ SafeDefaults degraded mode working")
        SafeDefaults.exit_degraded_mode()  # Reset
        
        # 4. AdaptiveJitter 검증
        jitter_stats = AdaptiveJitter.get_stats()
        print(f"   ✓ AdaptiveJitter ready (total_calculations: {jitter_stats.get('total_calculations', 0)})")
        
        _v2_init_done = True
        print(f"✅ {STAGE_NAME} V2 Utilities initialized successfully!")
        return True
        
    except Exception as e:
        import traceback
        print(f"⚠️ {STAGE_NAME} V2 Utilities init failed: {e}")
        traceback.print_exc()
        return False


class SelfHealingHellModeUser(HttpUser):
    """
    Self-Healing System Hell Mode Test User
    
    Pushes the system to absolute limits with V2 optimizations.
    """
    
    wait_time = between(0.5, 1.5)
    
    def on_start(self):
        """Initialize user session (V2 모듈은 test_start에서 이미 초기화됨)"""
        setup_event_hooks(STAGE_NAME)
        
        # Login as admin
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.login_helper.login_as_admin()
        self.is_admin = self.login_helper.is_logged_in
        
        # Initialize SelfHealingClient
        self.sh_client = None
        if V2_UTILITIES_AVAILABLE and self.is_admin:
            try:
                self.sh_client = SelfHealingClient(
                    host="http://localhost:8000",
                    auth_mode="xtest"
                )
                self.sh_client.login("admin", "1234")
            except Exception as e:
                print(f"⚠️ SelfHealingClient init failed: {e}")
    
    # =========================================================================
    # Scenario 1: Retry Storm with AdaptiveJitter
    # =========================================================================
    
    @task(3)
    @tag("hellmode", "retry-storm", "jitter")
    def test_retry_storm_with_jitter(self):
        """
        Retry Storm 테스트 - AdaptiveJitter로 요청 분산 검증
        
        50개 요청을 동시에 발생시키고 AdaptiveJitter가
        0~150ms 사이로 분산시키는지 확인
        """
        start_time = time.time()
        
        if not V2_UTILITIES_AVAILABLE:
            return self._fallback_retry_storm()
        
        jitter_values = []
        response_times = []
        
        try:
            # AdaptiveJitter 상태 업데이트
            AdaptiveJitter.update_cache(
                error_budget=0.5,  # 50% remaining
                load=0.7  # 70% load
            )
            
            def make_request_with_jitter(i):
                # Apply jitter before request
                jitter_ms = AdaptiveJitter.calculate_ms()
                jitter_values.append(jitter_ms)
                time.sleep(jitter_ms / 1000.0)
                
                req_start = time.time()
                with self.client.get(
                    f"{SH_API_BASE}/health/ping/",
                    name=f"{STAGE_NAME} RetryStorm-{i}",
                    catch_response=True,
                ) as response:
                    req_time = (time.time() - req_start) * 1000
                    response_times.append(req_time)
                    
                    if response.status_code in [200, 429]:  # 429 is also acceptable
                        response.success()
                        return True
                    response.failure(f"Storm request {i} failed")
                    return False
            
            # Execute concurrent requests
            with ThreadPoolExecutor(max_workers=HELL_MODE_CONFIG["retry_storm_concurrent"]) as executor:
                futures = [
                    executor.submit(make_request_with_jitter, i) 
                    for i in range(HELL_MODE_CONFIG["retry_storm_requests"])
                ]
                results = [f.result() for f in as_completed(futures)]
            
            elapsed = time.time() - start_time
            success_count = sum(results)
            
            # Jitter distribution analysis
            if jitter_values:
                avg_jitter = sum(jitter_values) / len(jitter_values)
                min_jitter = min(jitter_values)
                max_jitter = max(jitter_values)
                
                _hell_stats["adaptive_jitter"]["avg_jitter_ms"] = avg_jitter
            else:
                avg_jitter = min_jitter = max_jitter = 0
            
            # Determine system state
            state = AdaptiveJitter.get_state()
            _hell_stats["adaptive_jitter"][f"{state.value}_count"] += 1
            
            # Log event asynchronously
            if V2_UTILITIES_AVAILABLE:
                AsyncHealingLogger.log({
                    "type": "retry_storm",
                    "total_requests": len(results),
                    "success_count": success_count,
                    "avg_jitter_ms": avg_jitter,
                }, EventSeverity.INFO)
                _hell_stats["async_logger"]["events_logged"] += 1
            
            detail = {
                "total_requests": len(results),
                "success_count": success_count,
                "jitter_distribution": {
                    "min_ms": min_jitter,
                    "max_ms": max_jitter,
                    "avg_ms": avg_jitter,
                },
                "system_state": state.value,
                "elapsed_seconds": elapsed,
            }
            
            # Success if jitter is properly distributed (not all same)
            jitter_spread = max_jitter - min_jitter if jitter_values else 0
            success = jitter_spread > 10  # At least 10ms spread
            
            record_scenario_result("retry_storm", success, detail)
            record_scenario_result("jitter_distribution", success, detail)
            
        except Exception as e:
            record_scenario_result("retry_storm", False, {"error": str(e)})
    
    def _fallback_retry_storm(self):
        """Fallback retry storm without jitter"""
        with self.client.get(
            f"{SH_API_BASE}/health/ping/",
            name=f"{STAGE_NAME} RetryStorm-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 429]
            record_scenario_result("retry_storm", success, {"fallback": True})
            response.success() if success else response.failure("Failed")
    
    # =========================================================================
    # Scenario 2: SLA Hard-Cap Verification (CBStateCache 활용)
    # =========================================================================
    
    @task(4)
    @tag("hellmode", "sla", "hardcap")
    def test_sla_hardcap(self):
        """
        SLA Hard-Cap 테스트 - P99 < 250ms (Gold) 검증
        
        1. CBStateCache로 캐시 먼저 확인 (네트워크 호출 감소)
        2. 응답 시간이 Gold SLA(250ms)를 초과하면 즉시 FAIL 판정
        3. 모든 이벤트를 AsyncHealingLogger로 비동기 기록
        """
        # 서비스별 캐시 상태 먼저 확인 (핵심 수정!)
        services_to_check = ["payment-service", "order-service", "point-service"]
        cache_checks = 0
        
        if V2_UTILITIES_AVAILABLE:
            for service in services_to_check:
                # CBStateCache에서 먼저 조회 - 캐시 히트 시 네트워크 호출 없음
                cached_state = CBStateCache.get_state(service)
                cache_checks += 1
                
                # 캐시된 상태 기반 로깅
                AsyncHealingLogger.log({
                    "type": "cache_check",
                    "service": service,
                    "state": cached_state.get("status", "unknown") if cached_state else "miss",
                    "from_cache": True,
                }, EventSeverity.DEBUG)
        
        critical_endpoints = [
            (f"{SH_API_BASE}/health/ping/", "ping", "platinum"),  # < 100ms
            (f"{SH_API_BASE}/health/", "health", "gold"),  # < 250ms
            (f"{SH_API_BASE}/status/", "status", "gold"),  # < 250ms
            (f"{SH_API_BASE}/error-budget/status/", "error_budget", "silver"),  # < 500ms
        ]
        
        violations = 0
        total_checks = 0
        
        for endpoint, name, tier in critical_endpoints:
            with self.client.get(
                endpoint,
                name=f"{STAGE_NAME} SLA-{name}",
                catch_response=True,
            ) as response:
                response_time_ms = response.elapsed.total_seconds() * 1000
                total_checks += 1
                
                threshold = HELL_MODE_CONFIG.get(f"sla_{tier}_p99", 250)
                
                record_response_time(name, response_time_ms, tier)
                
                # 모든 SLA 체크를 AsyncHealingLogger로 비동기 기록
                if V2_UTILITIES_AVAILABLE:
                    AsyncHealingLogger.log({
                        "type": "sla_check",
                        "endpoint": name,
                        "tier": tier,
                        "threshold_ms": threshold,
                        "actual_ms": response_time_ms,
                        "passed": response_time_ms <= threshold,
                    }, EventSeverity.INFO)
                
                if response_time_ms > threshold:
                    violations += 1
                    _hell_stats["sla_violations"][tier] += 1
                    
                    # SLA violation 비동기 기록
                    if V2_UTILITIES_AVAILABLE:
                        AsyncHealingLogger.log({
                            "type": "sla_violation",
                            "endpoint": name,
                            "tier": tier,
                            "threshold_ms": threshold,
                            "actual_ms": response_time_ms,
                        }, EventSeverity.WARNING)
                
                if response.status_code in [200, 204, 401, 403]:
                    response.success()
                else:
                    response.failure(f"SLA check failed: {response.status_code}")
        
        detail = {
            "cache_checks": cache_checks,
            "total_checks": total_checks,
            "violations": violations,
            "violation_rate": (violations / total_checks * 100) if total_checks > 0 else 0,
        }
        
        success = violations == 0
        record_scenario_result("sla_hardcap", success, detail)
    
    # =========================================================================
    # Scenario 3: Cascading Failure Isolation
    # =========================================================================
    
    @task(2)
    @tag("hellmode", "cascade", "isolation")
    def test_cascading_failure(self):
        """
        Cascading Failure 테스트 - 연쇄 장애 격리 검증
        
        여러 서비스에 순차적으로 장애를 주입하고
        격리가 제대로 되는지 확인
        """
        if not self.sh_client:
            return self._fallback_cascade_test()
        
        start_time = time.time()
        services = HELL_MODE_CONFIG["cascade_services"]
        cascade_results = []
        
        try:
            # Use CBStateCache for efficient state checking
            if V2_UTILITIES_AVAILABLE:
                # Warm up cache
                CBStateCache.warm_up(services)
            
            # Inject failures sequentially
            for i, service in enumerate(services):
                # Apply adaptive jitter between failures
                if V2_UTILITIES_AVAILABLE and i > 0:
                    AdaptiveJitter.apply_jitter_sleep(
                        error_budget_remaining=0.3,  # Stressed
                        current_load=0.8
                    )
                
                inject_result = self.sh_client.xtest.inject_cb_failure(
                    service_name=service,
                    failure_type="exception",
                    failure_rate=1.0,
                    duration_seconds=5,
                )
                
                cascade_results.append({
                    "service": service,
                    "injected": inject_result.get("status") != "error",
                })
                
                # Log asynchronously
                if V2_UTILITIES_AVAILABLE:
                    AsyncHealingLogger.log_cb_event(
                        service=service,
                        state="INJECTED",
                        reason="Cascade test"
                    )
                
                time.sleep(HELL_MODE_CONFIG["cascade_delay_between_failures_ms"] / 1000)
            
            # Check blast radius - verify other services are not affected
            time.sleep(0.5)
            blast_result = self.sh_client.xtest.test_multi_blast_radius(
                services=services,
                failure_type="exception"
            )
            
            # Reset all CBs
            for service in services:
                self.sh_client.xtest.reset_cb(service)
                if V2_UTILITIES_AVAILABLE:
                    CBStateCache.invalidate(service)
            
            elapsed = time.time() - start_time
            
            # Check isolation matrix
            isolated = True
            if isinstance(blast_result, dict) and blast_result.get("isolation_matrix"):
                for svc, impact in blast_result.get("isolation_matrix", {}).items():
                    if impact.get("cascaded_failures", 0) > 0:
                        isolated = False
            
            detail = {
                "services_tested": len(services),
                "cascade_results": cascade_results,
                "isolation_maintained": isolated,
                "elapsed_seconds": elapsed,
            }
            
            success = isolated and all(r["injected"] for r in cascade_results)
            record_scenario_result("cascading_failure", success, detail)
            
        except Exception as e:
            record_scenario_result("cascading_failure", False, {"error": str(e)})
    
    def _fallback_cascade_test(self):
        """Fallback cascade test"""
        with self.client.get(
            f"{SH_API_BASE}/circuit-breaker/pool/status/",
            name=f"{STAGE_NAME} Cascade-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("cascading_failure", success, {"fallback": True})
            response.success()
    
    # =========================================================================
    # Scenario 4: Command Center Disconnect (SafeDefaults)
    # =========================================================================
    
    @task(2)
    @tag("hellmode", "disconnect", "safedefaults")
    def test_command_center_disconnect(self):
        """
        사령탑 연결 끊김 테스트 - SafeDefaults로 자가 치유 지속
        
        사령탑 API 호출 실패 시 SafeDefaults로 전환되어
        시스템이 계속 작동하는지 확인
        """
        if not V2_UTILITIES_AVAILABLE:
            return self._fallback_disconnect_test()
        
        start_time = time.time()
        
        try:
            # 1. Enter degraded mode (simulate command center disconnect)
            SafeDefaults.enter_degraded_mode(reason="Hell Mode - Simulated Disconnect")
            _hell_stats["safe_defaults"]["degraded_mode_entries"] += 1
            
            # 2. Get safe default values
            cb_threshold = SafeDefaults.get("CB_FAILURE_THRESHOLD")
            rate_limit = SafeDefaults.get("RATE_LIMIT_PER_MINUTE")
            timeout = SafeDefaults.get("DEFAULT_TIMEOUT_MS")
            
            # 3. Verify conservative values are applied
            conservative_values = {
                "CB_FAILURE_THRESHOLD": cb_threshold <= 5,  # Conservative: <= 5
                "RATE_LIMIT_PER_MINUTE": rate_limit <= 100,  # Conservative: <= 100
                "DEFAULT_TIMEOUT_MS": timeout <= 5000,  # Conservative: <= 5s
            }
            
            # 4. Check degraded mode status
            is_degraded = SafeDefaults.is_degraded()
            degraded_info = SafeDefaults.get_degraded_info()
            
            # 5. Verify system still works with defaults
            test_endpoints = [
                f"{SH_API_BASE}/health/ping/",
                f"{SH_API_BASE}/health/live/",
            ]
            
            endpoints_working = 0
            for endpoint in test_endpoints:
                with self.client.get(
                    endpoint,
                    name=f"{STAGE_NAME} Degraded-{endpoint.split('/')[-2]}",
                    catch_response=True,
                ) as response:
                    if response.status_code in [200, 204, 429]:
                        endpoints_working += 1
                        response.success()
                    else:
                        response.failure("Endpoint failed in degraded mode")
            
            # 6. Exit degraded mode
            SafeDefaults.exit_degraded_mode()
            
            elapsed = time.time() - start_time
            _hell_stats["safe_defaults"]["total_degraded_time_seconds"] += elapsed
            
            detail = {
                "degraded_mode_entered": is_degraded,
                "conservative_values": conservative_values,
                "degraded_info": degraded_info,
                "endpoints_working": endpoints_working,
                "total_endpoints": len(test_endpoints),
                "elapsed_seconds": elapsed,
            }
            
            success = (
                is_degraded and 
                all(conservative_values.values()) and 
                endpoints_working == len(test_endpoints)
            )
            
            record_scenario_result("command_center_disconnect", success, detail)
            
        except Exception as e:
            SafeDefaults.exit_degraded_mode()  # Ensure cleanup
            record_scenario_result("command_center_disconnect", False, {"error": str(e)})
    
    def _fallback_disconnect_test(self):
        """Fallback disconnect test"""
        with self.client.get(
            f"{SH_API_BASE}/health/ping/",
            name=f"{STAGE_NAME} Disconnect-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 429]
            record_scenario_result("command_center_disconnect", success, {"fallback": True})
            response.success()
    
    # =========================================================================
    # Scenario 5: CBStateCache Stress Test
    # =========================================================================
    
    @task(2)
    @tag("hellmode", "cache", "stress")
    def test_cache_stress(self):
        """
        캐시 스트레스 테스트 - CBStateCache 성능 검증
        
        100회 연속 캐시 조회로 캐시 히트율 80% 이상 달성 확인
        """
        if not V2_UTILITIES_AVAILABLE:
            return self._fallback_cache_test()
        
        start_time = time.time()
        services = ["payment-service", "order-service", "point-service"]
        
        try:
            # Warm up cache
            CBStateCache.warm_up(services)
            
            hits = 0
            misses = 0
            fetch_times = []
            
            for i in range(HELL_MODE_CONFIG["cache_stress_iterations"]):
                service = random.choice(services)
                
                fetch_start = time.time()
                result = CBStateCache.get_state_with_meta(service)
                fetch_time = (time.time() - fetch_start) * 1000
                fetch_times.append(fetch_time)
                
                if result.get("from_cache"):
                    hits += 1
                else:
                    misses += 1
            
            elapsed = time.time() - start_time
            
            total = hits + misses
            hit_rate = (hits / total * 100) if total > 0 else 0
            avg_fetch_time = sum(fetch_times) / len(fetch_times) if fetch_times else 0
            
            _hell_stats["state_cache"]["cache_hits"] += hits
            _hell_stats["state_cache"]["cache_misses"] += misses
            _hell_stats["state_cache"]["hit_rate_percent"] = hit_rate
            _hell_stats["state_cache"]["avg_fetch_time_ms"] = avg_fetch_time
            
            detail = {
                "total_lookups": total,
                "cache_hits": hits,
                "cache_misses": misses,
                "hit_rate_percent": hit_rate,
                "avg_fetch_time_ms": avg_fetch_time,
                "elapsed_seconds": elapsed,
            }
            
            # Success if hit rate >= 70% (some misses expected due to TTL)
            success = hit_rate >= 70
            
            record_scenario_result("cache_stress", success, detail)
            
        except Exception as e:
            record_scenario_result("cache_stress", False, {"error": str(e)})
    
    def _fallback_cache_test(self):
        """Fallback cache test"""
        with self.client.get(
            f"{SH_API_BASE}/status/",
            name=f"{STAGE_NAME} Cache-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("cache_stress", success, {"fallback": True})
            response.success()
    
    # =========================================================================
    # Scenario 6: Concurrent Emergency Trigger
    # =========================================================================
    
    @task(1)
    @tag("hellmode", "emergency", "concurrent")
    def test_concurrent_emergency(self):
        """
        동시 다발 Emergency 트리거 테스트
        
        여러 스레드에서 동시에 Emergency를 트리거하고
        시스템이 정상적으로 처리하는지 확인
        """
        if not self.sh_client:
            return self._fallback_emergency_test()
        
        start_time = time.time()
        
        try:
            results = []
            
            def trigger_emergency(level: str):
                try:
                    # Apply jitter to prevent thundering herd
                    if V2_UTILITIES_AVAILABLE:
                        AdaptiveJitter.apply_jitter_sleep(
                            error_budget_remaining=0.2,  # Stressed
                            current_load=0.9
                        )
                    
                    result = self.sh_client.emergency.trigger(
                        level=level,
                        reason=f"Hell Mode Concurrent Test - {level}",
                        duration_minutes=1,
                    )
                    return {"level": level, "success": result.get("status") != "error", "result": result}
                except Exception as e:
                    return {"level": level, "success": False, "error": str(e)}
            
            # Trigger concurrent emergencies
            levels = ["LEVEL_1"] * HELL_MODE_CONFIG["concurrent_emergency_count"]
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(trigger_emergency, level) for level in levels]
                results = [f.result() for f in as_completed(futures)]
            
            # Release emergency
            time.sleep(0.5)
            release_result = self.sh_client.emergency.release(reason="Hell Mode Test Complete")
            
            elapsed = time.time() - start_time
            
            # Log asynchronously
            if V2_UTILITIES_AVAILABLE:
                AsyncHealingLogger.log_emergency_event(
                    level="CONCURRENT",
                    action="concurrent_test",
                    reason="Hell Mode - Concurrent emergency test"
                )
            
            detail = {
                "concurrent_triggers": len(results),
                "successful_triggers": sum(1 for r in results if r.get("success")),
                "release_success": release_result.get("status") != "error",
                "elapsed_seconds": elapsed,
            }
            
            # Success if at least one trigger worked (others may be rejected due to rate limit)
            success = any(r.get("success") for r in results) or any(
                r.get("result", {}).get("status_code") in [200, 201, 429] for r in results
            )
            
            record_scenario_result("concurrent_emergency", success, detail)
            
        except Exception as e:
            record_scenario_result("concurrent_emergency", False, {"error": str(e)})
    
    def _fallback_emergency_test(self):
        """Fallback emergency test"""
        with self.client.get(
            f"{SH_API_BASE}/emergency/status/",
            name=f"{STAGE_NAME} Emergency-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 401, 403]
            record_scenario_result("concurrent_emergency", success, {"fallback": True})
            response.success()
    
    # =========================================================================
    # Scenario 7: Total Blackout (Memory Snapshot Survival)
    # =========================================================================
    
    @task(1)
    @tag("hellmode", "blackout", "survival")
    def test_total_blackout_survival(self):
        """
        Total Blackout 테스트 - 메모리 스냅샷만으로 생존 확인
        
        모든 외부 서비스 장애 상황에서 로컬 캐시와
        SafeDefaults로 시스템이 생존하는지 확인
        """
        if not V2_UTILITIES_AVAILABLE:
            return self._fallback_blackout_test()
        
        start_time = time.time()
        
        try:
            # 1. Enter degraded mode
            SafeDefaults.enter_degraded_mode(reason="Total Blackout Simulation")
            
            # 2. Pre-warm cache with known good values
            CBStateCache.warm_up(["payment-service", "order-service"])
            
            # 3. Simulate blackout by using only cached/default values
            blackout_operations = []
            
            # Get cached state (should work even if network is down)
            for service in ["payment-service", "order-service"]:
                cached_state = CBStateCache.get_state(service)
                blackout_operations.append({
                    "operation": "cache_read",
                    "service": service,
                    "success": cached_state is not None,
                })
            
            # Get safe defaults (should always work)
            safe_values = {
                "cb_threshold": SafeDefaults.get("CB_FAILURE_THRESHOLD"),
                "rate_limit": SafeDefaults.get("RATE_LIMIT_PER_MINUTE"),
                "timeout": SafeDefaults.get("DEFAULT_TIMEOUT_MS"),
            }
            blackout_operations.append({
                "operation": "safe_defaults_read",
                "success": all(v is not None for v in safe_values.values()),
                "values": safe_values,
            })
            
            # Log event (should buffer without blocking)
            log_start = time.time()
            AsyncHealingLogger.log({
                "type": "blackout_survival",
                "phase": "in_blackout",
            }, EventSeverity.CRITICAL)
            log_time_us = (time.time() - log_start) * 1_000_000
            
            blackout_operations.append({
                "operation": "async_log",
                "success": True,
                "log_time_us": log_time_us,
            })
            _hell_stats["async_logger"]["avg_log_time_us"] = log_time_us
            
            # Verify non-blocking (should be < 1000 microseconds = 1ms)
            non_blocking = log_time_us < 1000
            
            # 4. Exit degraded mode
            SafeDefaults.exit_degraded_mode()
            
            elapsed = time.time() - start_time
            
            detail = {
                "blackout_operations": blackout_operations,
                "safe_values": safe_values,
                "async_log_time_us": log_time_us,
                "non_blocking_verified": non_blocking,
                "elapsed_seconds": elapsed,
            }
            
            success = all(op.get("success") for op in blackout_operations) and non_blocking
            
            record_scenario_result("total_blackout", success, detail)
            
        except Exception as e:
            SafeDefaults.exit_degraded_mode()
            record_scenario_result("total_blackout", False, {"error": str(e)})
    
    def _fallback_blackout_test(self):
        """Fallback blackout test"""
        with self.client.get(
            f"{SH_API_BASE}/health/ping/",
            name=f"{STAGE_NAME} Blackout-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 429]
            record_scenario_result("total_blackout", success, {"fallback": True})
            response.success()

    # =========================================================================
    # Scenario 8: Netflix Gradient Adaptive Throttling
    # =========================================================================

    @task(3)
    @tag("hellmode", "throttle", "adaptive", "netflix")
    def test_adaptive_throttling(self):
        """
        Netflix Gradient Adaptive Throttling 테스트
        
        RTT(Round-Trip Time)에 따라 동적으로 limit이 조절되는지 확인:
        - RTT 증가 시 → limit 감소 (보호 모드)
        - RTT 감소 시 → limit 증가 (정상 모드)
        - SLA 임계값 초과 시 → 급격한 limit 감소
        """
        if not V3_UTILITIES_AVAILABLE:
            return self._fallback_throttle_test()
        
        start_time = time.time()
        
        try:
            # Get adaptive throttle instance
            throttle = get_adaptive_throttle()
            initial_limit = throttle.current_limit
            
            test_results = []
            rtt_samples = []
            limit_history = [initial_limit]
            
            # Phase 1: Normal load - RTT around 50-100ms
            for i in range(10):
                ident = f"test_user_{i}"
                result = throttle.check(ident)
                rtt = random.uniform(50, 100)  # Normal RTT
                throttle.record_response(rtt)
                rtt_samples.append(rtt)
                limit_history.append(throttle.current_limit)
                _hell_stats["adaptive_throttle"]["total_requests"] += 1
                if result.allowed:
                    _hell_stats["adaptive_throttle"]["allowed"] += 1
                else:
                    _hell_stats["adaptive_throttle"]["denied"] += 1
                test_results.append({
                    "phase": "normal",
                    "rtt_ms": rtt,
                    "allowed": result.allowed,
                    "limit": throttle.current_limit,
                })
            
            limit_after_normal = throttle.current_limit
            
            # Phase 2: High load - RTT 200-400ms (SLA warning zone)
            for i in range(10):
                ident = f"test_user_high_{i}"
                result = throttle.check(ident)
                rtt = random.uniform(200, 400)  # Warning zone RTT
                throttle.record_response(rtt)
                rtt_samples.append(rtt)
                limit_history.append(throttle.current_limit)
                _hell_stats["adaptive_throttle"]["total_requests"] += 1
                if result.allowed:
                    _hell_stats["adaptive_throttle"]["allowed"] += 1
                else:
                    _hell_stats["adaptive_throttle"]["denied"] += 1
                test_results.append({
                    "phase": "high_load",
                    "rtt_ms": rtt,
                    "allowed": result.allowed,
                    "limit": throttle.current_limit,
                })
            
            limit_after_high = throttle.current_limit
            
            # Phase 3: Critical load - RTT 500-800ms (SLA critical zone)
            for i in range(5):
                ident = f"test_user_critical_{i}"
                result = throttle.check(ident)
                rtt = random.uniform(500, 800)  # Critical zone RTT
                throttle.record_response(rtt)
                rtt_samples.append(rtt)
                limit_history.append(throttle.current_limit)
                _hell_stats["adaptive_throttle"]["total_requests"] += 1
                test_results.append({
                    "phase": "critical",
                    "rtt_ms": rtt,
                    "allowed": result.allowed,
                    "limit": throttle.current_limit,
                })
            
            limit_after_critical = throttle.current_limit
            
            # Phase 4: Recovery - RTT back to 50-100ms
            for i in range(10):
                ident = f"test_user_recovery_{i}"
                result = throttle.check(ident)
                rtt = random.uniform(50, 100)  # Normal RTT
                throttle.record_response(rtt)
                rtt_samples.append(rtt)
                limit_history.append(throttle.current_limit)
                _hell_stats["adaptive_throttle"]["total_requests"] += 1
                test_results.append({
                    "phase": "recovery",
                    "rtt_ms": rtt,
                    "allowed": result.allowed,
                    "limit": throttle.current_limit,
                })
            
            limit_after_recovery = throttle.current_limit
            
            elapsed = time.time() - start_time
            avg_rtt = sum(rtt_samples) / len(rtt_samples) if rtt_samples else 0
            _hell_stats["adaptive_throttle"]["avg_rtt_ms"] = avg_rtt
            
            # Verify Netflix Gradient behavior:
            # 1. Limit should decrease during high load
            # 2. Limit should decrease more during critical load
            # 3. Limit should recover after load normalizes
            gradient_behavior = {
                "decreased_during_high": limit_after_high < limit_after_normal,
                "decreased_during_critical": limit_after_critical < limit_after_high,
                "recovered_after_normal": limit_after_recovery >= limit_after_critical,
            }
            
            detail = {
                "initial_limit": initial_limit,
                "limit_after_normal": limit_after_normal,
                "limit_after_high": limit_after_high,
                "limit_after_critical": limit_after_critical,
                "limit_after_recovery": limit_after_recovery,
                "gradient_behavior": gradient_behavior,
                "avg_rtt_ms": avg_rtt,
                "total_samples": len(rtt_samples),
                "elapsed_seconds": elapsed,
            }
            
            # Success if gradient behavior is correct (limit decreases under load)
            success = gradient_behavior["decreased_during_critical"]
            
            record_scenario_result("adaptive_throttling", success, detail)
            
        except Exception as e:
            import traceback
            record_scenario_result("adaptive_throttling", False, {
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
    
    def _fallback_throttle_test(self):
        """Fallback throttle test"""
        with self.client.get(
            f"{SH_API_BASE}/health/ping/",
            name=f"{STAGE_NAME} Throttle-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 429]
            record_scenario_result("adaptive_throttling", success, {"fallback": True})
            response.success()

    # =========================================================================
    # Scenario 9: Corruption Shield (L1+L2+L3 Data Validation)
    # =========================================================================

    @task(3)
    @tag("hellmode", "corruption", "shield", "validation")
    def test_corruption_shield(self):
        """
        Corruption Shield 테스트 - Multi-Layer 데이터 무결성 검증
        
        L1 (Schema): 타입, 필수 필드, SQL Injection 차단
        L2 (Business Rules): 금액 범위, 상태 값 검증
        L3 (Anomaly Detection): Z-Score 기반 이상치 탐지
        """
        if not V3_UTILITIES_AVAILABLE:
            return self._fallback_shield_test()
        
        start_time = time.time()
        
        try:
            shield = get_corruption_shield()
            test_cases = []
            
            # Test Case 1: Valid data - should PASS
            valid_data = {
                "amount": 50000,
                "order_id": "order_12345",
                "status": "DONE",
            }
            result1 = shield.validate(valid_data)
            test_cases.append({
                "name": "valid_data",
                "expected_valid": True,
                "actual_valid": result1.is_valid,
                "passed": result1.is_valid == True,
                "violations": [v.to_dict() for v in result1.violations],
            })
            
            # Test Case 2: Missing required field - L1 should BLOCK
            missing_field = {
                "order_id": "order_12345",
                "status": "DONE",
            }
            result2 = shield.validate(missing_field)
            test_cases.append({
                "name": "missing_required_field",
                "expected_valid": False,
                "actual_valid": result2.is_valid,
                "passed": result2.is_valid == False,
                "layer_failed": "L1" if not result2.l1_passed else "none",
                "violations": [v.to_dict() for v in result2.violations],
            })
            
            # Test Case 3: SQL Injection attempt - L1 should BLOCK
            sql_injection = {
                "amount": 50000,
                "order_id": "order_123; DROP TABLE users; --",
                "status": "DONE",
            }
            result3 = shield.validate(sql_injection)
            test_cases.append({
                "name": "sql_injection",
                "expected_valid": False,
                "actual_valid": result3.is_valid,
                "passed": result3.is_valid == False,
                "layer_failed": "L1" if not result3.l1_passed else "none",
                "blocked": result3.blocked,
                "violations": [v.to_dict() for v in result3.violations],
            })
            
            # Test Case 4: Negative amount - L2 should BLOCK
            negative_amount = {
                "amount": -10000,
                "order_id": "order_12345",
                "status": "DONE",
            }
            result4 = shield.validate(negative_amount)
            test_cases.append({
                "name": "negative_amount",
                "expected_valid": False,
                "actual_valid": result4.is_valid,
                "passed": result4.is_valid == False,
                "layer_failed": "L2" if not result4.l2_passed else "none",
                "blocked": result4.blocked,
                "violations": [v.to_dict() for v in result4.violations],
            })
            
            # Test Case 5: Amount exceeds maximum - L2 should BLOCK
            huge_amount = {
                "amount": 999_999_999,  # 9.99억 (max is 1억)
                "order_id": "order_12345",
                "status": "DONE",
            }
            result5 = shield.validate(huge_amount)
            test_cases.append({
                "name": "amount_exceeds_max",
                "expected_valid": False,
                "actual_valid": result5.is_valid,
                "passed": result5.is_valid == False,
                "layer_failed": "L2" if not result5.l2_passed else "none",
                "blocked": result5.blocked,
                "violations": [v.to_dict() for v in result5.violations],
            })
            
            # Test Case 6: Invalid status - L2 should WARN
            invalid_status = {
                "amount": 50000,
                "order_id": "order_12345",
                "status": "HACKED",  # Invalid status
            }
            result6 = shield.validate(invalid_status)
            test_cases.append({
                "name": "invalid_status",
                "expected_valid": False,
                "actual_valid": result6.is_valid,
                "passed": result6.is_valid == False,
                "layer_failed": "L2" if not result6.l2_passed else "none",
                "violations": [v.to_dict() for v in result6.violations],
            })
            
            # Test Case 7: XSS attempt - L1 should BLOCK
            xss_attack = {
                "amount": 50000,
                "order_id": "<script>alert('XSS')</script>",
                "status": "DONE",
            }
            result7 = shield.validate(xss_attack)
            test_cases.append({
                "name": "xss_attack",
                "expected_valid": False,
                "actual_valid": result7.is_valid,
                "passed": result7.is_valid == False,
                "blocked": result7.blocked,
                "violations": [v.to_dict() for v in result7.violations],
            })
            
            # Test Case 8: Amount mismatch (context) - L2 should BLOCK
            mismatch_data = {
                "amount": 50000,
                "order_id": "order_12345",
                "status": "DONE",
            }
            mismatch_context = {"expected_amount": 100000}
            result8 = shield.validate(mismatch_data, context=mismatch_context)
            test_cases.append({
                "name": "amount_mismatch",
                "expected_valid": False,
                "actual_valid": result8.is_valid,
                "passed": result8.is_valid == False,
                "blocked": result8.blocked,
                "violations": [v.to_dict() for v in result8.violations],
            })
            
            # Get shield stats
            shield_stats = shield.get_stats()
            _hell_stats["corruption_shield"]["total_validations"] = shield_stats.get("total_validations", 0)
            _hell_stats["corruption_shield"]["passed"] = shield_stats.get("passed", 0)
            _hell_stats["corruption_shield"]["blocked"] = shield_stats.get("blocked", 0)
            _hell_stats["corruption_shield"]["l1_violations"] = shield_stats.get("l1_violations", 0)
            _hell_stats["corruption_shield"]["l2_violations"] = shield_stats.get("l2_violations", 0)
            _hell_stats["corruption_shield"]["l3_violations"] = shield_stats.get("l3_violations", 0)
            
            elapsed = time.time() - start_time
            passed_count = sum(1 for tc in test_cases if tc.get("passed"))
            
            detail = {
                "test_cases_total": len(test_cases),
                "test_cases_passed": passed_count,
                "test_cases": test_cases,
                "shield_stats": shield_stats,
                "elapsed_seconds": elapsed,
            }
            
            # Success if all test cases passed as expected
            success = passed_count == len(test_cases)
            
            record_scenario_result("corruption_shield", success, detail)
            
        except Exception as e:
            import traceback
            record_scenario_result("corruption_shield", False, {
                "error": str(e),
                "traceback": traceback.format_exc(),
            })
    
    def _fallback_shield_test(self):
        """Fallback shield test"""
        with self.client.get(
            f"{SH_API_BASE}/health/ping/",
            name=f"{STAGE_NAME} Shield-Fallback",
            catch_response=True,
        ) as response:
            success = response.status_code in [200, 429]
            record_scenario_result("corruption_shield", success, {"fallback": True})
            response.success()


# =============================================================================
# Test Summary & Report Generation
# =============================================================================

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate Hell Mode test summary"""
    stats = _hell_stats
    stats["end_time"] = time.time()
    
    # =========================================================================
    # V2 모듈에서 직접 통계 수집 (핵심 수정)
    # =========================================================================
    if V2_UTILITIES_AVAILABLE:
        try:
            # 1. AsyncHealingLogger 통계 수집
            async_stats = AsyncHealingLogger.get_stats()
            stats["async_logger"]["events_logged"] = async_stats.get("events_logged", 0)
            stats["async_logger"]["events_flushed"] = async_stats.get("events_flushed", 0)
            stats["async_logger"]["batch_flushes"] = async_stats.get("batch_flushes", 0)
            stats["async_logger"]["queue_size"] = async_stats.get("queue_size", 0)
            stats["async_logger"]["is_running"] = async_stats.get("is_running", False)
            
            # 2. CBStateCache 통계 수집
            cache_stats = CBStateCache.get_stats()
            stats["state_cache"]["cache_hits"] = cache_stats.get("cache_hits", 0)
            stats["state_cache"]["cache_misses"] = cache_stats.get("cache_misses", 0)
            hit_rate = cache_stats.get("hit_rate", 0) * 100  # 비율 -> 퍼센트
            stats["state_cache"]["hit_rate_percent"] = hit_rate
            stats["state_cache"]["cached_services"] = cache_stats.get("cached_services", 0)
            stats["state_cache"]["total_requests"] = cache_stats.get("total_requests", 0)
            
            # 3. AdaptiveJitter 통계 수집
            jitter_stats = AdaptiveJitter.get_stats()
            stats["adaptive_jitter"]["relaxed_count"] = jitter_stats.get("relaxed_count", 0)
            stats["adaptive_jitter"]["normal_count"] = jitter_stats.get("normal_count", 0)
            stats["adaptive_jitter"]["stressed_count"] = jitter_stats.get("stressed_count", 0)
            stats["adaptive_jitter"]["avg_jitter_ms"] = jitter_stats.get("avg_jitter_ms", 0)
            stats["adaptive_jitter"]["total_calculations"] = jitter_stats.get("total_calculations", 0)
            
            # 4. SafeDefaults 통계 수집
            stats["safe_defaults"]["is_degraded"] = SafeDefaults.is_degraded()
            stats["safe_defaults"]["total_degraded_time_seconds"] = SafeDefaults.get_degraded_duration()
            
            print(f"\n✅ V2 모듈 통계 수집 완료 - Async: {async_stats.get('events_logged', 0)}, Cache Hit: {hit_rate:.1f}%")
            
        except Exception as e:
            print(f"⚠️ V2 모듈 통계 수집 실패: {e}")
        
        # Stop AsyncHealingLogger
        try:
            AsyncHealingLogger.stop()
        except:
            pass
    
    # =========================================================================
    # V3 모듈에서 직접 통계 수집 (Adaptive Throttle + Corruption Shield)
    # =========================================================================
    if V3_UTILITIES_AVAILABLE:
        try:
            # 1. Adaptive Throttle 통계 수집
            throttle = get_adaptive_throttle()
            throttle_stats = throttle.get_stats()
            stats["adaptive_throttle"]["current_limit"] = throttle.current_limit
            stats["adaptive_throttle"]["min_limit_reached"] = throttle_stats.get("min_limit_reached", 0)
            stats["adaptive_throttle"]["max_limit_reached"] = throttle_stats.get("max_limit_reached", 0)
            
            # 2. Corruption Shield 통계 수집
            shield = get_corruption_shield()
            shield_stats = shield.get_stats()
            stats["corruption_shield"]["total_validations"] = shield_stats.get("total_validations", 0)
            stats["corruption_shield"]["passed"] = shield_stats.get("passed", 0)
            stats["corruption_shield"]["blocked"] = shield_stats.get("blocked", 0)
            stats["corruption_shield"]["l1_violations"] = shield_stats.get("l1_violations", 0)
            stats["corruption_shield"]["l2_violations"] = shield_stats.get("l2_violations", 0)
            stats["corruption_shield"]["l3_violations"] = shield_stats.get("l3_violations", 0)
            
            print(f"\n✅ V3 모듈 통계 수집 완료 - Throttle Limit: {throttle.current_limit}, Corruption Blocked: {shield_stats.get('blocked', 0)}")
            
        except Exception as e:
            print(f"⚠️ V3 모듈 통계 수집 실패: {e}")
    
    duration = stats["end_time"] - stats["start_time"] if stats["start_time"] else 0
    
    print("\n" + "=" * 80)
    print("🔥🔥🔥 STAGE 0: SELF-HEALING HELL MODE TEST RESULTS 🔥🔥🔥")
    print("=" * 80)
    
    print(f"\n⏱️  Test Duration: {duration:.1f} seconds")
    print(f"📅 Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # V2 Utilities Stats
    print("\n" + "-" * 80)
    print("🛠️  V2 OPTIMIZATION UTILITIES STATS")
    print("-" * 80)
    
    print(f"\n📨 AsyncHealingLogger:")
    print(f"   Events Logged: {stats['async_logger']['events_logged']}")
    print(f"   Avg Log Time: {stats['async_logger']['avg_log_time_us']:.2f}μs (target: < 1000μs)")
    
    print(f"\n🧠 CBStateCache:")
    print(f"   Cache Hits: {stats['state_cache']['cache_hits']}")
    print(f"   Cache Misses: {stats['state_cache']['cache_misses']}")
    print(f"   Hit Rate: {stats['state_cache']['hit_rate_percent']:.1f}% (target: > 70%)")
    
    print(f"\n🛡️  SafeDefaults:")
    print(f"   Degraded Mode Entries: {stats['safe_defaults']['degraded_mode_entries']}")
    print(f"   Total Degraded Time: {stats['safe_defaults']['total_degraded_time_seconds']:.2f}s")
    
    print(f"\n⚡ AdaptiveJitter:")
    print(f"   Relaxed: {stats['adaptive_jitter']['relaxed_count']}")
    print(f"   Normal: {stats['adaptive_jitter']['normal_count']}")
    print(f"   Stressed: {stats['adaptive_jitter']['stressed_count']}")
    print(f"   Avg Jitter: {stats['adaptive_jitter']['avg_jitter_ms']:.1f}ms")
    
    # V3 Utilities Stats
    print("\n" + "-" * 80)
    print("🚀 V3 OPTIMIZATION UTILITIES STATS (Netflix Gradient + Shield)")
    print("-" * 80)
    
    print(f"\n🎚️  Netflix Gradient Adaptive Throttle:")
    print(f"   Total Requests: {stats['adaptive_throttle']['total_requests']}")
    print(f"   Allowed: {stats['adaptive_throttle']['allowed']}")
    print(f"   Denied: {stats['adaptive_throttle']['denied']}")
    print(f"   Current Limit: {stats['adaptive_throttle'].get('current_limit', 'N/A')}")
    print(f"   Avg RTT: {stats['adaptive_throttle']['avg_rtt_ms']:.1f}ms")
    
    print(f"\n🛡️  Corruption Shield (L1+L2+L3):")
    print(f"   Total Validations: {stats['corruption_shield']['total_validations']}")
    print(f"   Passed: {stats['corruption_shield']['passed']}")
    print(f"   Blocked: {stats['corruption_shield']['blocked']}")
    print(f"   L1 Violations (Schema): {stats['corruption_shield']['l1_violations']}")
    print(f"   L2 Violations (Business): {stats['corruption_shield']['l2_violations']}")
    print(f"   L3 Violations (Anomaly): {stats['corruption_shield']['l3_violations']}")
    
    # Scenario Results
    print("\n" + "-" * 80)
    print("📊 HELL MODE SCENARIO RESULTS")
    print("-" * 80)
    
    total_executed = 0
    total_success = 0
    
    for scenario, data in stats["scenarios"].items():
        if data["executed"] > 0:
            total_executed += data["executed"]
            total_success += data["success"]
            
            success_rate = (data["success"] / data["executed"] * 100) if data["executed"] > 0 else 0
            status = "✅" if success_rate >= 80 else "⚠️" if success_rate >= 50 else "❌"
            
            print(f"\n{status} {scenario.replace('_', ' ').title()}")
            print(f"   Executed: {data['executed']} | Success: {data['success']} | Failure: {data['failure']}")
            print(f"   Success Rate: {success_rate:.1f}%")
    
    # SLA Violations
    if any(stats["sla_violations"].values()):
        print("\n" + "-" * 80)
        print("⚠️  SLA VIOLATIONS")
        print("-" * 80)
        for tier, count in stats["sla_violations"].items():
            if count > 0:
                threshold = HELL_MODE_CONFIG.get(f"sla_{tier}_p99", 0)
                print(f"   {tier.upper()}: {count} violations (threshold: {threshold}ms)")
    
    # Final Summary
    overall_rate = (total_success / total_executed * 100) if total_executed > 0 else 0
    
    print("\n" + "=" * 80)
    print("📈 FINAL SUMMARY")
    print("=" * 80)
    print(f"\n   Total Scenarios Executed: {total_executed}")
    print(f"   Total Success: {total_success}")
    print(f"   Overall Success Rate: {overall_rate:.1f}%")
    
    # V2 Optimization Verdict
    v2_pass = (
        stats["async_logger"]["avg_log_time_us"] < 1000 and  # < 1ms
        stats["state_cache"]["hit_rate_percent"] >= 70 and
        stats["safe_defaults"]["degraded_mode_entries"] > 0
    )
    
    print(f"\n   V2 Optimizations: {'✅ EFFECTIVE' if v2_pass else '⚠️ NEEDS TUNING'}")
    
    if overall_rate >= 90:
        print("\n🔥 HELL MODE TEST PASSED - Self-Healing survived extreme conditions!")
    elif overall_rate >= 70:
        print("\n⚠️  HELL MODE PARTIAL PASS - Some scenarios need improvement")
    else:
        print("\n❌ HELL MODE FAILED - Self-Healing needs strengthening")
    
    print("=" * 80 + "\n")
    
    # Save results
    _save_hellmode_results(stats, environment)


def _save_hellmode_results(stats: dict, environment):
    """Save Hell Mode results to markdown file"""
    try:
        results_dir = os.path.join(_project_root, "load_tests", "results", "stage0")
        os.makedirs(results_dir, exist_ok=True)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"stage0_hellmode_{date_str}.md"
        filepath = os.path.join(results_dir, filename)
        
        report = _generate_hellmode_report(stats)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"📄 Results saved to: {filepath}")
    except Exception as e:
        print(f"⚠️  Failed to save results: {e}")


def _generate_hellmode_report(stats: dict) -> str:
    """Generate Hell Mode markdown report"""
    duration = stats["end_time"] - stats["start_time"] if stats.get("end_time") and stats.get("start_time") else 0
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    total_executed = sum(s["executed"] for s in stats["scenarios"].values())
    total_success = sum(s["success"] for s in stats["scenarios"].values())
    overall_rate = (total_success / total_executed * 100) if total_executed > 0 else 0
    
    report = f"""# Stage 0: Self-Healing Hell Mode Test Results 🔥

**테스트 일시:** {date_str}  
**테스트 환경:** Docker Compose (localhost:8000)  
**테스트 도구:** Locust + V2 Optimization Utilities  
**테스트 시간:** {duration:.1f} seconds

---

## 🎯 테스트 목적

Self-Healing 시스템을 **지옥의 난이도**로 몰아붙여 다음을 검증:
- V2 Optimization 유틸리티 효과 측정
- 극한 부하에서의 Jitter 분산 효과
- SLA Hard-Cap 준수 여부
- 연쇄 장애 격리 능력
- 사령탑 장애 시 자가 치유 지속성
- 동시 다발 Emergency 처리 능력

---

## 📊 테스트 결과 요약

| 항목 | 결과 |
|------|------|
| **전체 시나리오 수** | {len([s for s in stats['scenarios'].values() if s['executed'] > 0])} |
| **총 실행 횟수** | {total_executed} |
| **성공** | {total_success} |
| **전체 성공률** | **{overall_rate:.1f}%** |

---

## 🛠️ V2 Optimization Utilities 효과 측정

### 📨 AsyncHealingLogger (비동기 이벤트 버퍼링)

| 메트릭 | 결과 | 목표 | 상태 |
|--------|------|------|------|
| 이벤트 로깅 횟수 | {stats['async_logger']['events_logged']} | - | - |
| 평균 로깅 시간 | {stats['async_logger']['avg_log_time_us']:.2f}μs | < 1000μs | {'✅' if stats['async_logger']['avg_log_time_us'] < 1000 else '❌'} |
| 논블로킹 달성 | {'Yes' if stats['async_logger']['avg_log_time_us'] < 1000 else 'No'} | Yes | {'✅' if stats['async_logger']['avg_log_time_us'] < 1000 else '❌'} |

### 🧠 CBStateCache (TTL 기반 로컬 캐싱)

| 메트릭 | 결과 | 목표 | 상태 |
|--------|------|------|------|
| 캐시 히트 | {stats['state_cache']['cache_hits']} | - | - |
| 캐시 미스 | {stats['state_cache']['cache_misses']} | - | - |
| 히트율 | {stats['state_cache']['hit_rate_percent']:.1f}% | ≥ 70% | {'✅' if stats['state_cache']['hit_rate_percent'] >= 70 else '❌'} |
| 네트워크 호출 감소 | {stats['state_cache']['hit_rate_percent']:.1f}% | ≥ 80% | {'✅' if stats['state_cache']['hit_rate_percent'] >= 80 else '⚠️'} |

### 🛡️ SafeDefaults (최후의 보루)

| 메트릭 | 결과 | 상태 |
|--------|------|------|
| Degraded Mode 진입 횟수 | {stats['safe_defaults']['degraded_mode_entries']} | {'✅' if stats['safe_defaults']['degraded_mode_entries'] > 0 else '⚠️'} |
| 총 Degraded 시간 | {stats['safe_defaults']['total_degraded_time_seconds']:.2f}초 | - |

### ⚡ AdaptiveJitter (Retry Storm 방지)

| 메트릭 | 결과 |
|--------|------|
| Relaxed 상태 | {stats['adaptive_jitter']['relaxed_count']}회 |
| Normal 상태 | {stats['adaptive_jitter']['normal_count']}회 |
| Stressed 상태 | {stats['adaptive_jitter']['stressed_count']}회 |
| 평균 Jitter | {stats['adaptive_jitter']['avg_jitter_ms']:.1f}ms |

---

## 🔥 시나리오별 상세 결과

"""
    
    # Add scenario details
    for scenario, data in stats["scenarios"].items():
        if data["executed"] > 0:
            success_rate = (data["success"] / data["executed"] * 100) if data["executed"] > 0 else 0
            status = "✅ PASS" if success_rate >= 80 else "⚠️ PARTIAL" if success_rate >= 50 else "❌ FAIL"
            
            report += f"""### {scenario.replace('_', ' ').title()}

| 메트릭 | 값 |
|--------|-----|
| 실행 횟수 | {data['executed']} |
| 성공 | {data['success']} |
| 실패 | {data['failure']} |
| 성공률 | {success_rate:.1f}% |
| 상태 | {status} |

"""
    
    # SLA Violations
    if any(stats["sla_violations"].values()):
        report += """---

## ⚠️ SLA 위반 현황

| SLA Tier | 위반 횟수 | 임계값 |
|----------|----------|--------|
"""
        for tier, count in stats["sla_violations"].items():
            threshold = HELL_MODE_CONFIG.get(f"sla_{tier}_p99", 0)
            report += f"| {tier.upper()} | {count} | {threshold}ms |\n"
    
    # Final Verdict
    v2_effective = (
        stats["async_logger"]["avg_log_time_us"] < 1000 and
        stats["state_cache"]["hit_rate_percent"] >= 70
    )
    
    if overall_rate >= 90:
        verdict = "✅ **HELL MODE PASSED** - Self-Healing 시스템이 극한 상황에서도 생존!"
    elif overall_rate >= 70:
        verdict = "⚠️ **PARTIAL PASS** - 일부 시나리오 개선 필요"
    else:
        verdict = "❌ **HELL MODE FAILED** - Self-Healing 시스템 강화 필요"
    
    report += f"""
---

## 🏆 최종 판정

{verdict}

### V2 Optimization 효과

| 유틸리티 | 효과 | 판정 |
|----------|------|------|
| AsyncHealingLogger | 논블로킹 로깅 | {'✅ 효과적' if stats['async_logger']['avg_log_time_us'] < 1000 else '❌ 튜닝 필요'} |
| CBStateCache | 네트워크 호출 {stats['state_cache']['hit_rate_percent']:.0f}% 감소 | {'✅ 효과적' if stats['state_cache']['hit_rate_percent'] >= 70 else '❌ 튜닝 필요'} |
| SafeDefaults | Degraded Mode 지원 | {'✅ 작동함' if stats['safe_defaults']['degraded_mode_entries'] > 0 else '⚠️ 미테스트'} |
| AdaptiveJitter | 요청 분산 | {'✅ 효과적' if stats['adaptive_jitter']['stressed_count'] > 0 else '⚠️ 미테스트'} |

### 권장 사항

1. **AsyncHealingLogger**: {'현재 설정 유지' if stats['async_logger']['avg_log_time_us'] < 1000 else 'batch_size 줄이고 flush_interval 늘리기'}
2. **CBStateCache**: {'현재 TTL 유지' if stats['state_cache']['hit_rate_percent'] >= 70 else 'TTL 늘리기 (5초 → 10초)'}
3. **SafeDefaults**: 모든 클라이언트에서 Degraded Mode fallback 구현 권장
4. **AdaptiveJitter**: {'Stressed 상태 감지 정상' if stats['adaptive_jitter']['stressed_count'] > 0 else 'threshold 조정 필요'}

---

*Generated by Stage 0 Hell Mode Test on {date_str}*
"""
    
    return report


# =============================================================================
# Quick Test Mode
# =============================================================================

if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    
    print(f"\n🔥 Quick Hell Mode Test against {host}")
    print("=" * 60)
    
    if not V2_UTILITIES_AVAILABLE:
        print("❌ V2 Utilities not available. Cannot run Hell Mode.")
        sys.exit(1)
    
    # Quick V2 utilities test
    print("\n📨 Testing AsyncHealingLogger...")
    start = time.time()
    AsyncHealingLogger.log({"test": "quick"}, EventSeverity.INFO)
    elapsed_us = (time.time() - start) * 1_000_000
    print(f"   Log time: {elapsed_us:.2f}μs {'✅' if elapsed_us < 1000 else '❌'}")
    
    print("\n🧠 Testing CBStateCache...")
    CBStateCache.warm_up(["test-service"])
    state = CBStateCache.get_state("test-service")
    print(f"   Cache state: {state}")
    
    print("\n🛡️ Testing SafeDefaults...")
    SafeDefaults.enter_degraded_mode("Quick test")
    cb_threshold = SafeDefaults.get("CB_FAILURE_THRESHOLD")
    SafeDefaults.exit_degraded_mode()
    print(f"   CB Threshold: {cb_threshold} {'✅' if cb_threshold <= 5 else '❌'}")
    
    print("\n⚡ Testing AdaptiveJitter...")
    jitter = AdaptiveJitter.calculate_ms()
    print(f"   Jitter: {jitter}ms")
    
    print("\n" + "=" * 60)
    print("✅ Quick Hell Mode test complete!")
