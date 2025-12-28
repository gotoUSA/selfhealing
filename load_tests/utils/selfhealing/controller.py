"""
SelfHealingController - 극한 부하 테스트용 Self-Healing 제어 컨트롤러.

이 모듈은 Stage13 EXTREME 등 극한 부하 테스트에서 사용되는
Self-Healing 제어 클래스를 제공합니다.

Features:
- Chaos injection by test phase (spike, sustain, recovery, cool)
- Circuit Breaker monitoring and forced transitions
- V2.8 Aggressive Healing (rate limit cut, forced CB open, adaptive jitter)
- Emergency escalation/recovery
- DLQ flood/replay control

Usage:
    from load_tests.utils.selfhealing.controller import (
        SelfHealingController, get_controller
    )
    
    controller = get_controller()
    controller.inject_chaos_for_phase("spike", cycle=0)
    controller.record_response_time(350.0, service="payment")
"""

import os
import time
import random
from typing import Optional, Dict, List, Any, Callable

try:
    from .base import SelfHealingClient
    SELFHEALING_AVAILABLE = True
except ImportError:
    # Fallback import
    try:
        from load_tests.utils.selfhealing import SelfHealingClient
        SELFHEALING_AVAILABLE = True
    except ImportError:
        SELFHEALING_AVAILABLE = False
        SelfHealingClient = None


# =============================================================================
# V2.8 AGGRESSIVE HEALING Configuration
# =============================================================================

# SLA Hard-Cap (강화됨)
SLA_P99_THRESHOLD_MS = 300
SLA_CRITICAL_MS = 500
SLA_AGGRESSIVE_THRESHOLD_MS = 300

# Circuit Breaker 강화 설정
CB_FAILURE_THRESHOLD = 3
CB_RECOVERY_TIMEOUT_MS = 10000
CB_HALF_OPEN_SUCCESS_THRESHOLD = 2

# Adaptive Rate Limiting 강화
RATE_LIMIT_AGGRESSIVE_CUT = 0.5
RATE_LIMIT_MIN_REQUESTS_PER_SEC = 5
RATE_LIMIT_MAX_REQUESTS_PER_SEC = 100
RATE_LIMIT_RECOVERY_STEP = 0.1

# Adaptive Jitter 강화
JITTER_MAX_MS = 5000
JITTER_MIN_MS = 100
JITTER_ESCALATION_FACTOR = 2.0


class SelfHealingController:
    """
    극한 테스트를 위한 Self-Healing 제어 클래스.
    
    이 클래스는 부하 테스트의 각 페이즈에서 적절한 카오스를 주입하고,
    응답 시간에 따른 적응형 힐링을 수행합니다.
    
    Attributes:
        client: SelfHealingClient 인스턴스
        chaos_services: 카오스 주입 대상 서비스 목록
        
    Example:
        controller = SelfHealingController()
        controller.initialize()
        
        # 페이즈별 카오스 주입
        controller.inject_chaos_for_phase("spike", cycle=0)
        
        # 응답 시간 기록 및 적응형 힐링
        controller.record_response_time(350.0, service="payment")
    """
    
    def __init__(
        self,
        http_client=None,
        chaos_services: Optional[List[str]] = None,
        cascade_services: Optional[List[str]] = None,
        error_budget_drain_per_cycle: int = 100,
        debug_callback: Optional[Callable[[str, str], None]] = None,
        stats_callback: Optional[Callable[[str, Any], None]] = None,
    ):
        """
        Initialize with optional HTTP client for Locust integration.
        
        Args:
            http_client: Locust HttpUser의 client 인스턴스 (옵션)
            chaos_services: 카오스 주입 대상 서비스 목록
            cascade_services: Cascade 테스트 대상 서비스 목록
            error_budget_drain_per_cycle: 사이클당 Error Budget 드레인량
            debug_callback: 디버그 로그 콜백 (message, level) -> None
            stats_callback: 통계 기록 콜백 (key, value) -> None
        """
        self.client: Optional[SelfHealingClient] = None
        self._http_client = http_client
        self._last_cb_state: Dict[str, str] = {}
        self._last_chaos_injection = 0
        self._chaos_interval = 10  # 10초마다 카오스 주입
        self._initialized = False
        
        # Configuration
        self.chaos_services = chaos_services or [
            "payment", "order", "database", "toss_payment", "notification"
        ]
        self.cascade_services = cascade_services or [
            "payment", "order", "inventory", "notification"
        ]
        self.error_budget_drain_per_cycle = error_budget_drain_per_cycle
        
        # Callbacks
        self._debug_callback = debug_callback or self._default_debug
        self._stats_callback = stats_callback or self._default_stats
        
        # V2.8 Aggressive Healing State
        self._cb_failure_counts: Dict[str, int] = {}
        self._current_rate_limit_percent = 100.0
        self._current_jitter_ms = JITTER_MIN_MS
        self._last_response_times: List[float] = []
        self._response_window_size = 20
        
        # Internal stats (if no callback provided)
        self._internal_stats = {
            "chaos": {
                "failures_injected": 0,
                "latency_injections": 0,
                "blast_radius_tests": 0,
                "recovery_triggers": 0,
            },
            "emergency": {
                "escalations": [],
                "max_level_reached": 0,
                "recovery_successes": 0,
                "recovery_failures": 0,
            },
            "dlq": {
                "total_replayed": 0,
            },
            "throttle": {
                "aggressive_cuts": 0,
                "limit_adjustments": 0,
                "current_limit_percent": 100,
            },
            "adaptive_jitter": {
                "escalations": 0,
                "current_jitter_ms": JITTER_MIN_MS,
                "max_jitter_reached": False,
            },
            "aggressive_cb": {
                "xtest_errors_counted": 0,
                "forced_opens": 0,
                "quick_recoveries": 0,
            },
            "cb_transitions": [],
            "cycles": {},
        }
    
    def _default_debug(self, message: str, level: str = "INFO") -> None:
        """Default debug logger."""
        print(f"[{level}] {message}")
    
    def _default_stats(self, key: str, value: Any) -> None:
        """Default stats callback that updates internal stats."""
        # Parse dot-notation key
        keys = key.split(".")
        target = self._internal_stats
        
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        
        final_key = keys[-1]
        if isinstance(target.get(final_key), (int, float)):
            if isinstance(value, (int, float)):
                target[final_key] += value
            else:
                target[final_key] = value
        elif isinstance(target.get(final_key), list):
            target[final_key].append(value)
        else:
            target[final_key] = value
    
    def get_stats(self) -> Dict[str, Any]:
        """Get internal statistics."""
        return self._internal_stats.copy()
    
    def initialize(self) -> bool:
        """
        Initialize Self-Healing client.
        
        Returns:
            True if initialization successful
        """
        if not SELFHEALING_AVAILABLE:
            self._debug_callback("Self-Healing client not available", "WARN")
            return False
        
        try:
            host = os.environ.get("SELFHEALING_HOST", "http://localhost:8000")
            self.client = SelfHealingClient(host=host, auth_mode="xtest")
            
            # Attempt login
            username = os.environ.get("SELFHEALING_USERNAME", "admin")
            password = os.environ.get("SELFHEALING_PASSWORD", "admin")
            login_success = self.client.login(username, password)
            
            if login_success:
                self._debug_callback("✅ Self-Healing client initialized and logged in", "INFO")
            else:
                self._debug_callback("⚠️ Self-Healing login failed, continuing without auth", "WARN")
            
            self._initialized = True
            return True
            
        except Exception as e:
            self._debug_callback(f"❌ Self-Healing client init failed: {e}", "ERROR")
            return False
    
    @property
    def is_initialized(self) -> bool:
        """Check if controller is initialized."""
        return self._initialized
    
    def inject_chaos_for_phase(self, phase: str, cycle: int) -> None:
        """
        Inject chaos appropriate for the current phase.
        
        Args:
            phase: Test phase (spike, sustain, recovery, cool)
            cycle: Current cycle number (0-indexed)
        """
        if not self._initialized or not self.client:
            return
        
        current_time = time.time()
        if current_time - self._last_chaos_injection < self._chaos_interval:
            return
        
        self._last_chaos_injection = current_time
        cycle_num = cycle + 1
        
        try:
            if phase == "spike":
                self._inject_spike_chaos(cycle_num)
            elif phase == "sustain":
                self._inject_sustain_chaos(cycle_num)
            elif phase == "recovery":
                self._inject_recovery_actions(cycle_num)
            elif phase == "cool":
                self._verify_recovery(cycle_num)
        except Exception as e:
            self._debug_callback(f"Chaos injection error: {e}", "ERROR")
    
    def _ensure_cycle_stats(self, cycle: int) -> None:
        """Ensure cycle stats dict exists."""
        if cycle not in self._internal_stats["cycles"]:
            self._internal_stats["cycles"][cycle] = {
                "chaos_injected": [],
                "cb_transitions": [],
            }
    
    def _inject_spike_chaos(self, cycle_num: int) -> None:
        """Spike phase: Inject failures to trigger circuit breakers."""
        self._debug_callback(f"💥 Cycle {cycle_num} SPIKE: Injecting failures", "CHAOS")
        
        target_service = random.choice(self.chaos_services)
        
        try:
            result = self.client.xtest.inject_cb_failure(
                service_name=target_service,
                failure_type="exception",
                failure_rate=0.8,
                duration_seconds=20,
            )
            self._internal_stats["chaos"]["failures_injected"] += 1
            self._ensure_cycle_stats(cycle_num - 1)
            self._internal_stats["cycles"][cycle_num - 1]["chaos_injected"].append({
                "type": "cb_failure",
                "service": target_service,
                "result": result.get("status", "unknown") if result else "unknown",
            })
            self._debug_callback(f"   → Injected CB failure to {target_service}: {result}", "CHAOS")
        except Exception as e:
            self._debug_callback(f"   → CB failure injection failed: {e}", "ERROR")
        
        try:
            result = self.client.xtest.inject_error_budget(
                slo_name="availability",
                error_count=self.error_budget_drain_per_cycle * 10,
            )
            self._debug_callback(f"   → Error budget injection: {result}", "CHAOS")
        except Exception as e:
            self._debug_callback(f"   → Error budget injection failed: {e}", "ERROR")
    
    def _inject_sustain_chaos(self, cycle_num: int) -> None:
        """Sustain phase: Multi-blast radius and cascade testing."""
        self._debug_callback(f"🌊 Cycle {cycle_num} SUSTAIN: Multi-blast radius test", "CHAOS")
        
        try:
            result = self.client.xtest.test_multi_blast_radius(
                services=self.cascade_services[:2],
                failure_type="latency",
            )
            self._internal_stats["chaos"]["blast_radius_tests"] += 1
            self._debug_callback(f"   → Blast radius test: {result}", "CHAOS")
        except Exception as e:
            self._debug_callback(f"   → Blast radius test failed: {e}", "ERROR")
        
        try:
            target = random.choice(self.chaos_services)
            result = self.client.chaos.xtest_inject_latency(
                target=target,
                latency_ms=500,
                duration_seconds=15,
            )
            self._internal_stats["chaos"]["latency_injections"] += 1
            self._debug_callback(f"   → Latency injection to {target}: {result}", "CHAOS")
        except Exception as e:
            self._debug_callback(f"   → Latency injection failed: {e}", "ERROR")
        
        try:
            level = f"LEVEL_{min(cycle_num, 3)}"
            result = self.client.emergency.trigger(
                level=level,
                reason=f"Controller Cycle {cycle_num} Sustain Test",
                duration_minutes=1,
            )
            self._internal_stats["emergency"]["escalations"].append({
                "cycle": cycle_num,
                "level": level,
            })
            if cycle_num > self._internal_stats["emergency"]["max_level_reached"]:
                self._internal_stats["emergency"]["max_level_reached"] = cycle_num
            self._debug_callback(f"   → Emergency triggered to {level}: {result}", "CHAOS")
        except Exception as e:
            self._debug_callback(f"   → Emergency trigger failed: {e}", "ERROR")
    
    def _inject_recovery_actions(self, cycle_num: int) -> None:
        """Recovery phase: Trigger healing mechanisms."""
        self._debug_callback(f"🔄 Cycle {cycle_num} RECOVERY: Triggering healing", "HEAL")
        
        try:
            for service in self.chaos_services[:2]:
                result = self.client.xtest.trigger_cb_recovery(service_name=service)
                self._internal_stats["chaos"]["recovery_triggers"] += 1
                self._debug_callback(f"   → CB recovery triggered for {service}: {result}", "HEAL")
        except Exception as e:
            self._debug_callback(f"   → CB recovery trigger failed: {e}", "ERROR")
        
        try:
            result = self.client.emergency.release(
                reason=f"Controller Cycle {cycle_num} Recovery"
            )
            self._internal_stats["emergency"]["recovery_successes"] += 1
            self._debug_callback(f"   → Emergency released: {result}", "HEAL")
        except Exception as e:
            self._internal_stats["emergency"]["recovery_failures"] += 1
            self._debug_callback(f"   → Emergency release failed: {e}", "ERROR")
        
        try:
            result = self.client.dlq.replay(batch_size=50)
            replayed = result.get("replayed", 0) if result else 0
            self._internal_stats["dlq"]["total_replayed"] += replayed
            self._debug_callback(f"   → DLQ replayed: {replayed} items", "HEAL")
        except Exception as e:
            self._debug_callback(f"   → DLQ replay failed: {e}", "ERROR")
    
    def _verify_recovery(self, cycle_num: int) -> None:
        """Cool phase: Verify system has recovered."""
        self._debug_callback(f"✅ Cycle {cycle_num} COOL: Verifying recovery", "VERIFY")
        
        try:
            status = self.client.get_overall_status()
            
            cb_status = status.get("circuit_breakers", {})
            services = cb_status.get("services", [])
            open_count = sum(1 for s in services if s.get("circuit_state") == "open")
            
            if open_count == 0:
                self._debug_callback("   → All circuit breakers closed ✓", "VERIFY")
            else:
                self._debug_callback(f"   → {open_count} circuit breakers still open!", "WARN")
            
            emergency = status.get("emergency", {})
            em_level = emergency.get("level", emergency.get("current_level", "NORMAL"))
            if em_level in ("NORMAL", "LEVEL_0", None):
                self._debug_callback("   → Emergency mode normal ✓", "VERIFY")
            else:
                self._debug_callback(f"   → Emergency still at {em_level}", "WARN")
            
            error_budget = status.get("error_budget", {})
            remaining = error_budget.get("remaining_percent", 100)
            self._debug_callback(f"   → Error budget remaining: {remaining}%", "VERIFY")
            
            self.client.xtest.get_snapshot()
            self._debug_callback("   → Snapshot captured", "VERIFY")
            
        except Exception as e:
            self._debug_callback(f"   → Verification error: {e}", "ERROR")
        
        try:
            result = self.client.chaos.xtest_reset_all()
            self._debug_callback(f"   → Chaos reset: {result}", "VERIFY")
        except Exception as e:
            self._debug_callback(f"   → Chaos reset failed: {e}", "ERROR")
    
    def check_and_update_cb_state(self, http_client=None) -> None:
        """Check circuit breaker states and record transitions."""
        if not self._initialized or not self.client:
            return
        
        try:
            status = self.client.circuit_breaker.get_all_status()
            services = status.get("services", []) if status else []
            
            for svc in services:
                service_name = svc.get("service_name", "unknown")
                current_state = svc.get("circuit_state") or svc.get("state", "closed")
                last_state = self._last_cb_state.get(service_name, "closed")
                
                if current_state != last_state:
                    self._record_cb_transition(service_name, last_state, current_state)
                    self._last_cb_state[service_name] = current_state
        except Exception as e:
            self._debug_callback(f"CB state check error: {e}", "ERROR")
    
    def _record_cb_transition(self, service: str, from_state: str, to_state: str) -> None:
        """Record a circuit breaker state transition."""
        self._internal_stats["cb_transitions"].append({
            "service": service,
            "from": from_state,
            "to": to_state,
            "timestamp": time.time(),
        })
        self._debug_callback(
            f"🔌 CB Transition: {service} {from_state} → {to_state}", "CB"
        )
    
    def get_dlq_stats(self) -> Dict[str, Any]:
        """Get DLQ statistics."""
        if not self._initialized or not self.client:
            return {}
        try:
            return self.client.dlq.stats() or {}
        except Exception:
            return {}
    
    def get_throttle_stats(self) -> Dict[str, Any]:
        """Get adaptive throttle statistics."""
        if not self._initialized or not self.client:
            return {}
        try:
            return self.client.throttle.get_stats() or {}
        except Exception:
            return {}
    
    def get_corruption_shield_stats(self) -> Dict[str, Any]:
        """Get corruption shield statistics."""
        if not self._initialized or not self.client:
            return {}
        try:
            return self.client.corruption_shield.get_stats() or {}
        except Exception:
            return {}
    
    # =========================================================================
    # V2.8 Aggressive Healing Methods
    # =========================================================================
    
    def record_response_time(self, response_time_ms: float, service: str = "default") -> None:
        """
        Record response time and trigger aggressive healing if needed.
        
        Args:
            response_time_ms: Response time in milliseconds
            service: Service name for tracking
            
        Notes:
            - 300ms 초과 → limit 50% 삭감
            - 500ms 초과 → 즉시 대응 (SLA_CRITICAL)
        """
        self._last_response_times.append(response_time_ms)
        if len(self._last_response_times) > self._response_window_size:
            self._last_response_times.pop(0)
        
        if response_time_ms > SLA_AGGRESSIVE_THRESHOLD_MS:
            self._apply_aggressive_rate_cut()
        
        if response_time_ms > SLA_CRITICAL_MS:
            self._handle_sla_critical(response_time_ms, service)
        
        self._adjust_jitter(response_time_ms)
    
    def _apply_aggressive_rate_cut(self) -> None:
        """300ms 초과 시 rate limit 50% 삭감."""
        old_limit = self._current_rate_limit_percent
        min_limit = RATE_LIMIT_MIN_REQUESTS_PER_SEC / RATE_LIMIT_MAX_REQUESTS_PER_SEC * 100
        self._current_rate_limit_percent = max(
            min_limit,
            self._current_rate_limit_percent * RATE_LIMIT_AGGRESSIVE_CUT
        )
        
        if old_limit != self._current_rate_limit_percent:
            self._internal_stats["throttle"]["aggressive_cuts"] += 1
            self._internal_stats["throttle"]["current_limit_percent"] = self._current_rate_limit_percent
            self._internal_stats["throttle"]["limit_adjustments"] += 1
            self._debug_callback(
                f"🔥 AGGRESSIVE CUT: Rate limit {old_limit:.0f}% → {self._current_rate_limit_percent:.0f}%",
                "THROTTLE"
            )
    
    def _handle_sla_critical(self, response_time_ms: float, service: str) -> None:
        """500ms 초과 시 즉시 대응."""
        self._debug_callback(
            f"🚨 SLA CRITICAL: {service} responded in {response_time_ms:.0f}ms (> {SLA_CRITICAL_MS}ms)",
            "CRITICAL"
        )
        
        self._cb_failure_counts[service] = self._cb_failure_counts.get(service, 0) + 1
        self._internal_stats["aggressive_cb"]["xtest_errors_counted"] += 1
        
        if self._cb_failure_counts[service] >= CB_FAILURE_THRESHOLD:
            self._force_cb_open(service)
            self._cb_failure_counts[service] = 0
    
    def _force_cb_open(self, service: str) -> None:
        """강제로 Circuit Breaker Open 트리거."""
        self._debug_callback(
            f"🔌 FORCED CB OPEN for {service} (reached {CB_FAILURE_THRESHOLD} failures)",
            "CB"
        )
        self._internal_stats["aggressive_cb"]["forced_opens"] += 1
        self._record_cb_transition(service, "closed", "open")
        
        if self._initialized and self.client:
            try:
                self.client.xtest.force_cb_open(service_name=service)
            except Exception as e:
                self._debug_callback(f"   → Force CB open API failed: {e}", "ERROR")
    
    def _adjust_jitter(self, response_time_ms: float) -> None:
        """응답 시간에 따라 AdaptiveJitter 자동 조정."""
        if response_time_ms > SLA_AGGRESSIVE_THRESHOLD_MS:
            old_jitter = self._current_jitter_ms
            self._current_jitter_ms = min(
                JITTER_MAX_MS,
                self._current_jitter_ms * JITTER_ESCALATION_FACTOR
            )
            
            if self._current_jitter_ms != old_jitter:
                self._internal_stats["adaptive_jitter"]["escalations"] += 1
                self._internal_stats["adaptive_jitter"]["current_jitter_ms"] = self._current_jitter_ms
                
                if self._current_jitter_ms >= JITTER_MAX_MS:
                    self._internal_stats["adaptive_jitter"]["max_jitter_reached"] = True
                    self._debug_callback(f"⚡ JITTER MAX REACHED: {JITTER_MAX_MS}ms", "JITTER")
                else:
                    self._debug_callback(
                        f"⚡ Jitter escalated: {old_jitter:.0f}ms → {self._current_jitter_ms:.0f}ms",
                        "JITTER"
                    )
        else:
            if self._current_jitter_ms > JITTER_MIN_MS:
                self._current_jitter_ms = max(
                    JITTER_MIN_MS,
                    self._current_jitter_ms * 0.9
                )
                self._internal_stats["adaptive_jitter"]["current_jitter_ms"] = self._current_jitter_ms
    
    def get_current_jitter_ms(self) -> float:
        """현재 적용할 지터 값 반환."""
        return self._current_jitter_ms
    
    def get_current_rate_limit_percent(self) -> float:
        """현재 rate limit 비율 반환."""
        return self._current_rate_limit_percent
    
    def should_skip_request(self) -> bool:
        """Rate limit에 따라 요청 스킵 여부 결정."""
        if self._current_rate_limit_percent >= 100:
            return False
        return random.random() * 100 > self._current_rate_limit_percent
    
    def recover_rate_limit(self) -> None:
        """정상 응답 시 rate limit 점진적 복구."""
        if self._current_rate_limit_percent < 100:
            old_limit = self._current_rate_limit_percent
            self._current_rate_limit_percent = min(
                100,
                self._current_rate_limit_percent * (1 + RATE_LIMIT_RECOVERY_STEP)
            )
            self._internal_stats["throttle"]["current_limit_percent"] = self._current_rate_limit_percent
            
            if self._current_rate_limit_percent >= 100:
                self._internal_stats["aggressive_cb"]["quick_recoveries"] += 1
                self._debug_callback("✅ Rate limit fully recovered to 100%", "THROTTLE")


# =============================================================================
# Global Controller Singleton
# =============================================================================

_controller: Optional[SelfHealingController] = None


def get_controller(
    chaos_services: Optional[List[str]] = None,
    **kwargs
) -> SelfHealingController:
    """
    Get or create global controller singleton instance.
    
    Args:
        chaos_services: List of services for chaos injection
        **kwargs: Additional arguments for SelfHealingController
        
    Returns:
        Initialized SelfHealingController instance
    """
    global _controller
    if _controller is None:
        _controller = SelfHealingController(
            chaos_services=chaos_services,
            **kwargs
        )
        _controller.initialize()
    return _controller


def reset_controller() -> None:
    """Reset global controller (for testing)."""
    global _controller
    _controller = None


__all__ = [
    "SelfHealingController",
    "get_controller",
    "reset_controller",
    "SLA_P99_THRESHOLD_MS",
    "SLA_CRITICAL_MS",
    "SLA_AGGRESSIVE_THRESHOLD_MS",
    "CB_FAILURE_THRESHOLD",
    "JITTER_MAX_MS",
    "JITTER_MIN_MS",
]
