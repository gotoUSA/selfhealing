"""
공통 통계 수집 유틸리티

모든 테스트 시나리오에서 사용하는 표준화된 통계 구조와
스레드 안전한 카운터, 시나리오 결과 기록 기능을 제공합니다.
"""
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime
import statistics


@dataclass
class BaseTestStats:
    """
    모든 테스트에서 사용하는 기본 통계 구조.
    
    Usage:
        stats = BaseTestStats()
        stats.record_scenario("browse_products", success=True, response_time_ms=150.5)
        stats.safe_increment("custom_metric")
        print(stats.to_dict())
    """
    scenarios: Dict[str, Dict] = field(default_factory=dict)
    passed: int = 0
    failed: int = 0
    timestamp: Optional[str] = None
    healing_actions: Dict[str, int] = field(default_factory=dict)
    
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    def safe_increment(self, key: str, amount: int = 1) -> None:
        """
        스레드 안전 카운터 증가.
        
        Args:
            key: 증가시킬 키 (속성명 또는 healing_actions 키)
            amount: 증가량 (기본값: 1)
        """
        with self._lock:
            if hasattr(self, key) and key not in ('scenarios', 'healing_actions', '_lock'):
                current = getattr(self, key)
                if isinstance(current, int):
                    setattr(self, key, current + amount)
            else:
                self.healing_actions[key] = self.healing_actions.get(key, 0) + amount
    
    def safe_decrement(self, key: str, amount: int = 1) -> None:
        """스레드 안전 카운터 감소."""
        self.safe_increment(key, -amount)
    
    def record_scenario(
        self, 
        name: str, 
        success: bool, 
        response_time_ms: float,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        시나리오 결과 기록.
        
        Args:
            name: 시나리오 이름
            success: 성공 여부
            response_time_ms: 응답 시간 (밀리초)
            error: 에러 메시지 (실패 시)
            metadata: 추가 메타데이터
        """
        with self._lock:
            if name not in self.scenarios:
                self.scenarios[name] = {
                    "count": 0,
                    "success": 0,
                    "failed": 0,
                    "response_times": [],
                    "errors": [],
                    "metadata": {}
                }
            
            scenario = self.scenarios[name]
            scenario["count"] += 1
            
            if success:
                scenario["success"] += 1
                self.passed += 1
            else:
                scenario["failed"] += 1
                self.failed += 1
                if error:
                    # 최근 100개 에러만 보관
                    if len(scenario["errors"]) >= 100:
                        scenario["errors"] = scenario["errors"][-99:]
                    scenario["errors"].append({
                        "message": error,
                        "timestamp": datetime.now().isoformat()
                    })
            
            # 응답 시간 기록 (최근 1000개만 보관)
            if len(scenario["response_times"]) >= 1000:
                scenario["response_times"] = scenario["response_times"][-999:]
            scenario["response_times"].append(response_time_ms)
            
            # 메타데이터 병합
            if metadata:
                scenario["metadata"].update(metadata)
    
    def get_scenario_stats(self, name: str) -> Optional[Dict[str, Any]]:
        """
        특정 시나리오의 통계 반환.
        
        Args:
            name: 시나리오 이름
            
        Returns:
            시나리오 통계 또는 None
        """
        with self._lock:
            if name not in self.scenarios:
                return None
            
            scenario = self.scenarios[name]
            response_times = scenario["response_times"]
            
            return {
                "count": scenario["count"],
                "success": scenario["success"],
                "failed": scenario["failed"],
                "success_rate": scenario["success"] / max(1, scenario["count"]) * 100,
                "avg_response_time_ms": statistics.mean(response_times) if response_times else 0,
                "p50_response_time_ms": statistics.median(response_times) if response_times else 0,
                "p95_response_time_ms": self._percentile(response_times, 95) if response_times else 0,
                "p99_response_time_ms": self._percentile(response_times, 99) if response_times else 0,
                "min_response_time_ms": min(response_times) if response_times else 0,
                "max_response_time_ms": max(response_times) if response_times else 0,
                "error_count": len(scenario["errors"]),
            }
    
    @staticmethod
    def _percentile(data: List[float], percentile: int) -> float:
        """백분위 계산."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def get_summary(self) -> Dict[str, Any]:
        """전체 통계 요약 반환."""
        with self._lock:
            total = self.passed + self.failed
            all_response_times = []
            for scenario in self.scenarios.values():
                all_response_times.extend(scenario.get("response_times", []))
            
            return {
                "total_requests": total,
                "passed": self.passed,
                "failed": self.failed,
                "success_rate": self.passed / max(1, total) * 100,
                "avg_response_time_ms": statistics.mean(all_response_times) if all_response_times else 0,
                "p95_response_time_ms": self._percentile(all_response_times, 95) if all_response_times else 0,
                "p99_response_time_ms": self._percentile(all_response_times, 99) if all_response_times else 0,
                "scenario_count": len(self.scenarios),
            }
    
    def to_dict(self) -> Dict[str, Any]:
        """
        딕셔너리로 변환 (JSON 저장용).
        
        Returns:
            직렬화 가능한 딕셔너리
        """
        with self._lock:
            # 각 시나리오별 통계 계산
            scenarios_with_stats = {}
            for name, scenario in self.scenarios.items():
                response_times = scenario["response_times"]
                scenarios_with_stats[name] = {
                    "count": scenario["count"],
                    "success": scenario["success"],
                    "failed": scenario["failed"],
                    "success_rate": scenario["success"] / max(1, scenario["count"]) * 100,
                    "avg_response_time_ms": statistics.mean(response_times) if response_times else 0,
                    "p50_response_time_ms": statistics.median(response_times) if response_times else 0,
                    "p95_response_time_ms": self._percentile(response_times, 95) if response_times else 0,
                    "p99_response_time_ms": self._percentile(response_times, 99) if response_times else 0,
                    "error_count": len(scenario["errors"]),
                    "recent_errors": scenario["errors"][-10:] if scenario["errors"] else [],
                    "metadata": scenario.get("metadata", {}),
                }
            
            # get_summary를 락 외부에서 호출하지 않고 인라인 계산
            total = self.passed + self.failed
            all_response_times = []
            for scenario in self.scenarios.values():
                all_response_times.extend(scenario.get("response_times", []))
            
            summary = {
                "total_requests": total,
                "passed": self.passed,
                "failed": self.failed,
                "success_rate": self.passed / max(1, total) * 100,
                "avg_response_time_ms": statistics.mean(all_response_times) if all_response_times else 0,
                "p95_response_time_ms": self._percentile(all_response_times, 95) if all_response_times else 0,
                "p99_response_time_ms": self._percentile(all_response_times, 99) if all_response_times else 0,
                "scenario_count": len(self.scenarios),
            }
            
            return {
                "scenarios": scenarios_with_stats,
                "passed": self.passed,
                "failed": self.failed,
                "success_rate": self.passed / max(1, self.passed + self.failed) * 100,
                "timestamp": self.timestamp or datetime.now().isoformat(),
                "healing_actions": dict(self.healing_actions),
                "summary": summary,
            }
    
    def reset(self) -> None:
        """통계 초기화."""
        with self._lock:
            self.scenarios.clear()
            self.passed = 0
            self.failed = 0
            self.timestamp = None
            self.healing_actions.clear()


@dataclass
class ExtremeTestStats(BaseTestStats):
    """
    극한 테스트용 확장 통계.
    
    Circuit Breaker, Emergency, Chaos 등 고급 시나리오를 위한
    추가 통계 필드를 제공합니다.
    
    Usage:
        stats = ExtremeTestStats()
        stats.record_cb_event("payment", "OPEN")
        stats.record_emergency_escalation(3)
        stats.record_chaos_injection("latency", "payment")
    """
    circuit_breaker: Dict[str, Any] = field(default_factory=lambda: {
        "total_opens": 0,
        "total_closes": 0,
        "total_half_opens": 0,
        "services_affected": [],
        "transitions": [],
    })
    emergency: Dict[str, Any] = field(default_factory=lambda: {
        "max_level_reached": 0,
        "escalations": [],
        "recovery_successes": 0,
        "recovery_failures": 0,
        "current_level": 0,
    })
    chaos: Dict[str, Any] = field(default_factory=lambda: {
        "failures_injected": 0,
        "latency_injections": 0,
        "blast_radius_tests": 0,
        "services_targeted": [],
    })
    aggressive_healing: Dict[str, Any] = field(default_factory=lambda: {
        "cb": {"xtest_errors_counted": 0, "forced_opens": 0},
        "throttle": {"aggressive_cuts": 0, "current_limit_percent": 100.0},
        "jitter": {"escalations": 0, "current_jitter_ms": 0, "max_reached": False},
    })
    sla: Dict[str, Any] = field(default_factory=lambda: {
        "p99_breaches": 0,
        "p95_breaches": 0,
        "error_rate_breaches": 0,
        "recovery_time_breaches": 0,
    })
    
    def record_cb_event(self, service: str, state: str) -> None:
        """
        Circuit Breaker 이벤트 기록.
        
        Args:
            service: 서비스 이름
            state: CB 상태 (OPEN, CLOSED, HALF_OPEN)
        """
        with self._lock:
            state_upper = state.upper()
            
            if state_upper == "OPEN":
                self.circuit_breaker["total_opens"] += 1
            elif state_upper == "CLOSED":
                self.circuit_breaker["total_closes"] += 1
            elif state_upper in ("HALF_OPEN", "HALF-OPEN"):
                self.circuit_breaker["total_half_opens"] += 1
            
            if service not in self.circuit_breaker["services_affected"]:
                self.circuit_breaker["services_affected"].append(service)
            
            # 최근 100개 전이만 보관
            transitions = self.circuit_breaker["transitions"]
            if len(transitions) >= 100:
                self.circuit_breaker["transitions"] = transitions[-99:]
            self.circuit_breaker["transitions"].append({
                "service": service,
                "state": state_upper,
                "timestamp": datetime.now().isoformat()
            })
    
    def record_emergency_escalation(self, level: int, reason: Optional[str] = None) -> None:
        """
        Emergency Level 에스컬레이션 기록.
        
        Args:
            level: 에스컬레이션 레벨 (1-5)
            reason: 에스컬레이션 사유
        """
        with self._lock:
            self.emergency["current_level"] = level
            if level > self.emergency["max_level_reached"]:
                self.emergency["max_level_reached"] = level
            
            # 최근 50개 에스컬레이션만 보관
            escalations = self.emergency["escalations"]
            if len(escalations) >= 50:
                self.emergency["escalations"] = escalations[-49:]
            self.emergency["escalations"].append({
                "level": level,
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            })
    
    def record_emergency_recovery(self, success: bool) -> None:
        """Emergency 복구 결과 기록."""
        with self._lock:
            if success:
                self.emergency["recovery_successes"] += 1
            else:
                self.emergency["recovery_failures"] += 1
    
    def record_chaos_injection(
        self, 
        chaos_type: str, 
        service: Optional[str] = None
    ) -> None:
        """
        Chaos 주입 기록.
        
        Args:
            chaos_type: 카오스 유형 (latency, failure, blast_radius)
            service: 대상 서비스
        """
        with self._lock:
            chaos_type_lower = chaos_type.lower()
            
            if "latency" in chaos_type_lower:
                self.chaos["latency_injections"] += 1
            elif "failure" in chaos_type_lower or "cb" in chaos_type_lower:
                self.chaos["failures_injected"] += 1
            elif "blast" in chaos_type_lower:
                self.chaos["blast_radius_tests"] += 1
            
            if service and service not in self.chaos["services_targeted"]:
                self.chaos["services_targeted"].append(service)
    
    def record_aggressive_healing(
        self, 
        healing_type: str, 
        **kwargs
    ) -> None:
        """
        V2.8 Aggressive Healing 이벤트 기록.
        
        Args:
            healing_type: 힐링 유형 (cb, throttle, jitter)
            **kwargs: 추가 데이터
        """
        with self._lock:
            healing_type_lower = healing_type.lower()
            
            if healing_type_lower == "cb":
                if kwargs.get("forced_open"):
                    self.aggressive_healing["cb"]["forced_opens"] += 1
                if kwargs.get("xtest_error"):
                    self.aggressive_healing["cb"]["xtest_errors_counted"] += 1
            
            elif healing_type_lower == "throttle":
                if kwargs.get("aggressive_cut"):
                    self.aggressive_healing["throttle"]["aggressive_cuts"] += 1
                if "current_limit" in kwargs:
                    self.aggressive_healing["throttle"]["current_limit_percent"] = kwargs["current_limit"]
            
            elif healing_type_lower == "jitter":
                if kwargs.get("escalation"):
                    self.aggressive_healing["jitter"]["escalations"] += 1
                if "current_ms" in kwargs:
                    self.aggressive_healing["jitter"]["current_jitter_ms"] = kwargs["current_ms"]
                if kwargs.get("max_reached"):
                    self.aggressive_healing["jitter"]["max_reached"] = True
    
    def record_sla_breach(self, breach_type: str) -> None:
        """
        SLA 위반 기록.
        
        Args:
            breach_type: 위반 유형 (p99, p95, error_rate, recovery_time)
        """
        with self._lock:
            breach_key = f"{breach_type}_breaches"
            if breach_key in self.sla:
                self.sla[breach_key] += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """확장된 딕셔너리 변환."""
        base_dict = super().to_dict()
        
        with self._lock:
            base_dict.update({
                "circuit_breaker": dict(self.circuit_breaker),
                "emergency": dict(self.emergency),
                "chaos": dict(self.chaos),
                "aggressive_healing": dict(self.aggressive_healing),
                "sla": dict(self.sla),
            })
        
        return base_dict
    
    def reset(self) -> None:
        """확장 통계 포함 초기화."""
        super().reset()
        with self._lock:
            self.circuit_breaker = {
                "total_opens": 0,
                "total_closes": 0,
                "total_half_opens": 0,
                "services_affected": [],
                "transitions": [],
            }
            self.emergency = {
                "max_level_reached": 0,
                "escalations": [],
                "recovery_successes": 0,
                "recovery_failures": 0,
                "current_level": 0,
            }
            self.chaos = {
                "failures_injected": 0,
                "latency_injections": 0,
                "blast_radius_tests": 0,
                "services_targeted": [],
            }
            self.aggressive_healing = {
                "cb": {"xtest_errors_counted": 0, "forced_opens": 0},
                "throttle": {"aggressive_cuts": 0, "current_limit_percent": 100.0},
                "jitter": {"escalations": 0, "current_jitter_ms": 0, "max_reached": False},
            }
            self.sla = {
                "p99_breaches": 0,
                "p95_breaches": 0,
                "error_rate_breaches": 0,
                "recovery_time_breaches": 0,
            }
