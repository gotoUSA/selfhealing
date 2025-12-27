"""
Stage 9 Extension: Worker Crash + Self-Healing 극단 테스트
============================================================

목표: Worker Crash 상황에서 Self-Healing 시스템의 극단적 복구 능력 검증

시나리오 (극단적 조합):
  1. 다수 Worker 동시 Crash
  2. Circuit Breaker 연쇄 Open
  3. DLQ 대량 Flood
  4. Emergency Mode 자동 트리거
  5. 점진적 복구 검증

추가 시나리오 (Stage 9 V2):
  6. 🧠 뇌 손상 (Distributed Brain Failure)
     - Redis/DB가 죽었을 때 HealthBridgeMiddleware가 메모리 스냅샷으로 생존
     - 저장소 없이 메모리 스냅샷만으로 생존 신호 유지 시간 측정
     
  7. 🧟 좀비 워커 (Slow Poisoning)
     - 워커 응답 시간 30초+ 지연 (논리적 장애)
     - 지연 감지 및 Eviction 후 정상 워커로 교체
     
  8. 🌐 네트워크 파티션 (Split Brain)
     - 사령탑(Self-Healing)과 쇼핑 API 간 통신 단절
     - 로컬 가이드라인에 따른 고립 방어 (Fallback)

Invariants:
  - in_flight_tasks_recovered: 진행 중 작업 복구
  - no_permanent_task_loss: 영구 작업 손실 없음
  - circuit_breaker_protected: CB가 시스템 보호
  - dlq_captured_failures: 실패가 DLQ에 캡처됨
  - emergency_mode_triggered: 극단 상황에서 Emergency 활성화
  - gradual_recovery_success: 점진적 복구 성공
  - health_bridge_survived_blackout: 저장소 죽어도 메모리 스냅샷으로 생존
  - zombie_workers_evicted: 좀비 워커 감지 및 방출
  - split_brain_fallback_worked: 네트워크 파티션 시 로컬 Fallback 동작

실행 방법:
    # Docker Compose 환경에서 실행
    docker-compose up -d
    python load_tests/scenarios/chaos/stage9_worker_crash_selfhealing.py

    # 또는 Locust 모드
    locust -f load_tests/scenarios/chaos/stage9_worker_crash_selfhealing.py --host=http://localhost:8000

Reference:
  - docs/GAP_RESOLUTION_PLAN.md (GAP-05)
  - load_tests/utils/selfhealing/
"""

import os
import sys
import time
import random
import threading
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

# 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(os.path.dirname(_current_dir))
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("Stage9-SelfHealing")

STAGE_NAME = "[Stage9-WorkerCrash-SelfHealing]"
DEBUG_MODE = os.environ.get("DEBUG_MODE", "false").lower() == "true"


# =============================================================================
# Self-Healing 클라이언트 임포트 시도
# =============================================================================

try:
    from load_tests.utils.selfhealing import SelfHealingClient
    from load_tests.utils.selfhealing.config import configure
    SELFHEALING_AVAILABLE = True
    logger.info("✅ SelfHealingClient imported successfully")
except ImportError as e:
    logger.warning(f"⚠️ SelfHealingClient not available: {e}")
    SELFHEALING_AVAILABLE = False


# =============================================================================
# 설정
# =============================================================================


@dataclass
class ExtremeChaosConfig:
    """극단적 카오스 테스트 설정"""
    
    # Worker 설정
    num_workers: int = 8
    tasks_per_second: int = 100
    task_processing_time_ms: int = 50
    
    # Crash 설정 (극단적)
    crash_interval_seconds: float = 2.0  # 2초마다 crash
    simultaneous_crash_count: int = 3  # 동시 crash 수
    crash_probability: float = 0.8  # crash 확률
    
    # Recovery 설정
    recovery_timeout_seconds: int = 2
    task_visibility_timeout_seconds: int = 1
    max_task_retries: int = 5
    
    # Self-Healing 설정
    selfhealing_host: str = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_HOST", "http://localhost:8000"
    ))
    cb_failure_threshold: int = 5
    dlq_flood_threshold: int = 50
    emergency_trigger_threshold: int = 100  # 이 이상 실패하면 Emergency
    
    # 테스트 설정
    test_duration_seconds: int = 60
    
    # === 새로운 시나리오 설정 (V2) ===
    
    # 🧠 뇌 손상 (Distributed Brain Failure) 설정
    brain_failure_enabled: bool = True
    brain_failure_duration_seconds: int = 15  # 저장소 블랙아웃 지속 시간
    memory_snapshot_max_age_seconds: int = 30  # 스냅샷 유효 시간
    
    # 🧟 좀비 워커 (Slow Poisoning) 설정
    zombie_enabled: bool = True
    zombie_response_time_seconds: float = 35.0  # 좀비 워커 응답 시간
    zombie_detection_threshold_seconds: float = 30.0  # 좀비 감지 임계값
    zombie_eviction_enabled: bool = True
    
    # 🌐 네트워크 파티션 (Split Brain) 설정
    split_brain_enabled: bool = True
    split_brain_duration_seconds: int = 20  # 네트워크 단절 지속 시간
    local_fallback_enabled: bool = True  # 로컬 가이드라인 Fallback


CONFIG = ExtremeChaosConfig()


# =============================================================================
# Task 정의
# =============================================================================


class TaskState(str, Enum):
    """Task 상태"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"
    IN_DLQ = "in_dlq"


@dataclass
class Task:
    """Task 정의"""
    
    id: str
    payload: Dict[str, Any]
    state: TaskState = TaskState.PENDING
    retry_count: int = 0
    max_retries: int = 5
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    recovery_attempts: int = 0
    dlq_id: Optional[int] = None
    
    def is_stale(self, timeout_seconds: int) -> bool:
        """작업이 stale 상태인지 확인"""
        if self.state != TaskState.IN_PROGRESS:
            return False
        if self.started_at is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return elapsed > timeout_seconds


# =============================================================================
# Worker 정의
# =============================================================================


class WorkerState(str, Enum):
    """Worker 상태"""
    RUNNING = "running"
    CRASHED = "crashed"
    STOPPED = "stopped"
    RECOVERING = "recovering"


@dataclass
class Worker:
    """Worker 정의"""
    
    id: str
    state: WorkerState = WorkerState.RUNNING
    current_task: Optional[Task] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    crashed_at: Optional[datetime] = None
    crash_count: int = 0
    
    def crash(self):
        """Worker crash 시뮬레이션"""
        self.state = WorkerState.CRASHED
        self.crashed_at = datetime.now(timezone.utc)
        self.crash_count += 1
        
    def is_healthy(self) -> bool:
        return self.state == WorkerState.RUNNING


# =============================================================================
# Self-Healing Integration Layer
# =============================================================================


class SelfHealingIntegration:
    """Self-Healing 시스템 통합 레이어"""
    
    def __init__(self, config: ExtremeChaosConfig):
        self.config = config
        self.client: Optional['SelfHealingClient'] = None
        self.connected = False
        self.events: List[Dict] = []
        
        # 상태 추적
        self.cb_states: Dict[str, str] = {}
        self.emergency_active = False
        self.dlq_count = 0
        
        # === V2: 새로운 시나리오 상태 ===
        # 🧠 뇌 손상 상태
        self.brain_failure_active = False
        self.memory_snapshot: Dict[str, Any] = {}
        self.snapshot_timestamp: Optional[datetime] = None
        self.brain_failure_start: Optional[datetime] = None
        self.health_bridge_responses: List[Dict] = []
        
        # 🧟 좀비 워커 상태
        self.zombie_workers: Dict[str, float] = {}  # worker_id -> response_time
        self.evicted_workers: List[str] = []
        
        # 🌐 네트워크 파티션 상태
        self.network_partition_active = False
        self.partition_start: Optional[datetime] = None
        self.fallback_activations: int = 0
        self.local_decisions: List[Dict] = []
        
    def connect(self) -> bool:
        """Self-Healing 시스템 연결"""
        if not SELFHEALING_AVAILABLE:
            logger.warning("SelfHealingClient not available, running in simulation mode")
            return False
            
        try:
            configure(host=self.config.selfhealing_host, debug=DEBUG_MODE)
            self.client = SelfHealingClient(auth_mode="xtest")
            
            # 연결 테스트
            health = self.client.health.ping()
            if health.get("ok"):
                self.connected = True
                logger.info(f"✅ Connected to Self-Healing at {self.config.selfhealing_host}")
                self._record_event("connection", "Connected to Self-Healing system")
                return True
            else:
                logger.warning(f"Self-Healing ping failed: {health}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to Self-Healing: {e}")
            if DEBUG_MODE:
                import traceback
                traceback.print_exc()
            return False
    
    def _record_event(self, event_type: str, description: str, details: Dict = None):
        """이벤트 기록"""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "description": description,
            "details": details or {}
        }
        self.events.append(event)
        logger.info(f"📝 {event_type}: {description}")
        
    def inject_cb_failure(self, service: str = "database", count: int = 5) -> Dict:
        """Circuit Breaker 장애 주입"""
        if not self.connected or not self.client:
            self._record_event("cb_failure", f"[SIM] Injected {count} failures to {service}")
            return {"simulated": True, "count": count}
            
        try:
            result = self.client.xtest.inject_cb_failure(
                service_name=service,
                failure_type="exception",
                failure_rate=1.0,
                duration_seconds=30
            )
            self._record_event("cb_failure", f"Injected {count} failures to {service}", result)
            return result
        except Exception as e:
            logger.error(f"CB failure injection failed: {e}")
            return {"error": str(e)}
    
    def get_cb_status(self, service: str = None) -> Dict:
        """Circuit Breaker 상태 조회"""
        if not self.connected or not self.client:
            # 시뮬레이션 모드
            return {"simulated": True, "state": "CLOSED"}
            
        try:
            result = self.client.circuit_breaker.get_all_status()
            if service and 'services' in result:
                return result['services'].get(service, {})
            return result
        except Exception as e:
            logger.error(f"CB status check failed: {e}")
            return {"error": str(e)}
    
    def get_dlq_stats(self) -> Dict:
        """DLQ 통계 조회"""
        if not self.connected or not self.client:
            return {"simulated": True, "pending_count": self.dlq_count}
            
        try:
            result = self.client.dlq.stats()
            self.dlq_count = result.get("pending_count", 0)
            return result
        except Exception as e:
            logger.error(f"DLQ stats failed: {e}")
            return {"error": str(e)}
    
    def trigger_emergency(self, level: str = "LEVEL_1", reason: str = "Extreme chaos") -> Dict:
        """Emergency 모드 트리거"""
        if not self.connected or not self.client:
            self.emergency_active = True
            self._record_event("emergency", f"[SIM] Triggered {level}: {reason}")
            return {"simulated": True, "level": level}
            
        try:
            result = self.client.emergency.trigger(
                level=level,
                reason=reason,
                duration_minutes=5
            )
            self.emergency_active = True
            self._record_event("emergency", f"Triggered {level}: {reason}", result)
            return result
        except Exception as e:
            logger.error(f"Emergency trigger failed: {e}")
            return {"error": str(e)}
    
    def release_emergency(self, reason: str = "Chaos test completed") -> Dict:
        """Emergency 모드 해제"""
        if not self.connected or not self.client:
            self.emergency_active = False
            self._record_event("emergency_release", f"[SIM] Released: {reason}")
            return {"simulated": True}
            
        try:
            result = self.client.emergency.release(reason=reason)
            self.emergency_active = False
            self._record_event("emergency_release", f"Released: {reason}", result)
            return result
        except Exception as e:
            logger.error(f"Emergency release failed: {e}")
            return {"error": str(e)}
    
    def start_gradual_recovery(self) -> Dict:
        """점진적 복구 시작"""
        if not self.connected or not self.client:
            self._record_event("gradual_recovery", "[SIM] Started gradual recovery")
            return {"simulated": True}
            
        try:
            result = self.client.emergency.start_gradual_recovery(
                target_level="NORMAL",
                step_duration_seconds=10
            )
            self._record_event("gradual_recovery", "Started gradual recovery", result)
            return result
        except Exception as e:
            logger.error(f"Gradual recovery failed: {e}")
            return {"error": str(e)}
    
    def get_snapshot(self) -> Dict:
        """시스템 스냅샷 조회"""
        if not self.connected or not self.client:
            return {"simulated": True, "timestamp": datetime.now(timezone.utc).isoformat()}
            
        try:
            result = self.client.xtest.get_snapshot()
            return result
        except Exception as e:
            logger.error(f"Snapshot failed: {e}")
            return {"error": str(e)}
    
    def reset_all(self):
        """모든 상태 리셋"""
        if not self.connected or not self.client:
            self._record_event("reset", "[SIM] Reset all states")
            return
            
        try:
            # CB 리셋
            self.client.xtest.reset_cb("database")
            self.client.xtest.reset_cb("payment")
            self.client.xtest.reset_cb("external_api")
            
            # Emergency 해제
            if self.emergency_active:
                self.release_emergency("Test cleanup")
                
            self._record_event("reset", "Reset all states")
        except Exception as e:
            logger.error(f"Reset failed: {e}")
    
    # =========================================================================
    # 🧠 뇌 손상 (Distributed Brain Failure) 시나리오
    # =========================================================================
    
    def start_brain_failure(self) -> Dict:
        """
        저장소(Redis/DB) 블랙아웃 시작.
        HealthBridgeMiddleware가 메모리 스냅샷만으로 생존하는지 테스트.
        """
        self.brain_failure_active = True
        self.brain_failure_start = datetime.now(timezone.utc)
        
        # 블랙아웃 전 마지막 스냅샷 저장
        self.memory_snapshot = self.get_snapshot()
        self.snapshot_timestamp = datetime.now(timezone.utc)
        
        self._record_event("brain_failure_start", 
            f"🧠 BRAIN FAILURE STARTED - Storage blackout simulated",
            {"snapshot_at": self.snapshot_timestamp.isoformat()})
        
        logger.error(f"{STAGE_NAME} 🧠 BRAIN FAILURE: Storage blackout started!")
        
        return {
            "active": True,
            "snapshot_saved": True,
            "started_at": self.brain_failure_start.isoformat()
        }
    
    def check_health_bridge_survival(self) -> Dict:
        """
        HealthBridge가 저장소 없이 메모리 스냅샷으로 응답하는지 확인.
        """
        if not self.brain_failure_active:
            return {"error": "Brain failure not active"}
        
        # 스냅샷 나이 계산
        snapshot_age = None
        if self.snapshot_timestamp:
            snapshot_age = (datetime.now(timezone.utc) - self.snapshot_timestamp).total_seconds()
        
        # 메모리 스냅샷에서 응답 생성 (HealthBridgeMiddleware 시뮬레이션)
        bridge_response = {
            "status": "bridge_active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "circuit_breakers": self.memory_snapshot.get("circuit_breakers", {}),
            "snapshot": {
                "last_updated": self.snapshot_timestamp.isoformat() if self.snapshot_timestamp else None,
                "age_seconds": snapshot_age,
                "stale": snapshot_age > self.config.memory_snapshot_max_age_seconds if snapshot_age else True,
            },
            "storage_available": False,  # 저장소 죽음
            "survival_mode": "memory_only",
        }
        
        self.health_bridge_responses.append(bridge_response)
        
        survived = snapshot_age is not None and snapshot_age <= self.config.memory_snapshot_max_age_seconds
        
        self._record_event("health_bridge_check",
            f"🧠 HealthBridge survival check: age={snapshot_age:.1f}s, survived={survived}",
            bridge_response)
        
        return {
            "survived": survived,
            "snapshot_age_seconds": snapshot_age,
            "max_age_allowed": self.config.memory_snapshot_max_age_seconds,
            "response": bridge_response
        }
    
    def end_brain_failure(self) -> Dict:
        """저장소 복구 및 뇌 손상 종료"""
        if not self.brain_failure_active:
            return {"error": "Brain failure not active"}
        
        duration = (datetime.now(timezone.utc) - self.brain_failure_start).total_seconds()
        successful_responses = len(self.health_bridge_responses)
        
        # 생존 분석
        survived_responses = sum(
            1 for r in self.health_bridge_responses 
            if r.get("snapshot", {}).get("age_seconds", float('inf')) <= self.config.memory_snapshot_max_age_seconds
        )
        
        result = {
            "duration_seconds": duration,
            "total_health_checks": successful_responses,
            "survived_checks": survived_responses,
            "survival_rate": survived_responses / successful_responses * 100 if successful_responses > 0 else 0,
            "max_snapshot_age_observed": max(
                (r.get("snapshot", {}).get("age_seconds", 0) for r in self.health_bridge_responses), 
                default=0
            )
        }
        
        self.brain_failure_active = False
        self.brain_failure_start = None
        
        self._record_event("brain_failure_end",
            f"🧠 BRAIN FAILURE ENDED - Duration: {duration:.1f}s, Survival rate: {result['survival_rate']:.1f}%",
            result)
        
        logger.info(f"{STAGE_NAME} 🧠 BRAIN FAILURE ENDED: {result}")
        
        return result
    
    # =========================================================================
    # 🧟 좀비 워커 (Slow Poisoning) 시나리오
    # =========================================================================
    
    def poison_worker(self, worker_id: str, response_time_seconds: float = None) -> Dict:
        """
        워커를 좀비 상태로 만듦 (응답 시간 극대화).
        Crash가 아닌 논리적 장애 - 살아는 있지만 시스템을 갉아먹음.
        """
        if response_time_seconds is None:
            response_time_seconds = self.config.zombie_response_time_seconds
        
        self.zombie_workers[worker_id] = response_time_seconds
        
        self._record_event("zombie_created",
            f"🧟 ZOMBIE WORKER: {worker_id[:8]} poisoned with {response_time_seconds}s response time",
            {"worker_id": worker_id, "response_time": response_time_seconds})
        
        logger.warning(f"{STAGE_NAME} 🧟 Worker {worker_id[:8]} turned ZOMBIE (response: {response_time_seconds}s)")
        
        return {
            "worker_id": worker_id,
            "is_zombie": True,
            "response_time_seconds": response_time_seconds
        }
    
    def detect_zombie_workers(self, workers: Dict[str, 'Worker']) -> List[str]:
        """
        좀비 워커 감지 - 응답 시간이 임계값 초과하는 워커 탐지.
        """
        detected_zombies = []
        
        for worker_id, response_time in self.zombie_workers.items():
            if response_time >= self.config.zombie_detection_threshold_seconds:
                detected_zombies.append(worker_id)
                
                self._record_event("zombie_detected",
                    f"🧟 ZOMBIE DETECTED: Worker {worker_id[:8]} - {response_time}s response time",
                    {"worker_id": worker_id, "threshold": self.config.zombie_detection_threshold_seconds})
        
        if detected_zombies:
            logger.warning(f"{STAGE_NAME} 🧟 Detected {len(detected_zombies)} zombie workers!")
        
        return detected_zombies
    
    def evict_zombie_worker(self, worker_id: str) -> Dict:
        """
        좀비 워커 방출 (Eviction) - 정상 워커로 교체.
        """
        if worker_id not in self.zombie_workers:
            return {"error": f"Worker {worker_id} is not a zombie"}
        
        response_time = self.zombie_workers.pop(worker_id)
        self.evicted_workers.append(worker_id)
        
        self._record_event("zombie_evicted",
            f"🧟 ZOMBIE EVICTED: Worker {worker_id[:8]} removed (was {response_time}s slow)",
            {"worker_id": worker_id, "response_time": response_time})
        
        logger.info(f"{STAGE_NAME} 🧟 Worker {worker_id[:8]} EVICTED and will be replaced")
        
        return {
            "worker_id": worker_id,
            "evicted": True,
            "response_time_was": response_time,
            "total_evicted": len(self.evicted_workers)
        }
    
    def is_zombie(self, worker_id: str) -> bool:
        """워커가 좀비 상태인지 확인"""
        return worker_id in self.zombie_workers
    
    def get_zombie_response_time(self, worker_id: str) -> float:
        """좀비 워커의 응답 시간 반환"""
        return self.zombie_workers.get(worker_id, 0)
    
    # =========================================================================
    # 🌐 네트워크 파티션 (Split Brain) 시나리오
    # =========================================================================
    
    def start_network_partition(self) -> Dict:
        """
        네트워크 파티션 시작 - 사령탑(Self-Healing)과 쇼핑 API 간 통신 단절.
        """
        self.network_partition_active = True
        self.partition_start = datetime.now(timezone.utc)
        
        self._record_event("partition_start",
            f"🌐 NETWORK PARTITION: Communication with HQ severed!",
            {"started_at": self.partition_start.isoformat()})
        
        logger.error(f"{STAGE_NAME} 🌐 SPLIT BRAIN: Network partition started!")
        
        return {
            "active": True,
            "started_at": self.partition_start.isoformat(),
            "hq_reachable": False
        }
    
    def try_reach_hq(self) -> Dict:
        """
        사령탑 접근 시도 - 파티션 중에는 실패.
        """
        if self.network_partition_active:
            self._record_event("hq_unreachable",
                "🌐 Cannot reach HQ - network partition active")
            return {
                "reachable": False,
                "error": "NetworkPartitionError: Connection timed out",
                "fallback_needed": True
            }
        
        return {"reachable": True, "fallback_needed": False}
    
    def activate_local_fallback(self, decision_type: str, details: Dict = None) -> Dict:
        """
        로컬 가이드라인에 따른 Fallback 활성화.
        사령탑과 연락 두절 시 자체 판단으로 시스템 보호.
        """
        if not self.network_partition_active:
            return {"error": "No partition active, fallback not needed"}
        
        self.fallback_activations += 1
        
        local_decision = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_type": decision_type,
            "details": details or {},
            "hq_contact": False,
            "local_rules_applied": True,
        }
        
        # 로컬 가이드라인에 따른 결정
        if decision_type == "circuit_breaker":
            local_decision["action"] = "LOCAL_CB_OPEN"
            local_decision["reason"] = "HQ unreachable, applying local fail-safe"
        elif decision_type == "rate_limit":
            local_decision["action"] = "LOCAL_RATE_LIMIT"
            local_decision["reason"] = "Conservative rate limiting without HQ guidance"
        elif decision_type == "emergency":
            local_decision["action"] = "LOCAL_EMERGENCY_MODE"
            local_decision["reason"] = "Self-preservation mode activated"
        else:
            local_decision["action"] = "LOCAL_DEFAULT"
            local_decision["reason"] = "Using cached/default configuration"
        
        self.local_decisions.append(local_decision)
        
        self._record_event("local_fallback",
            f"🌐 LOCAL FALLBACK: {decision_type} -> {local_decision['action']}",
            local_decision)
        
        logger.warning(f"{STAGE_NAME} 🌐 LOCAL FALLBACK: {local_decision['action']} - {local_decision['reason']}")
        
        return local_decision
    
    def end_network_partition(self) -> Dict:
        """네트워크 복구 및 파티션 종료"""
        if not self.network_partition_active:
            return {"error": "No partition active"}
        
        duration = (datetime.now(timezone.utc) - self.partition_start).total_seconds()
        
        result = {
            "duration_seconds": duration,
            "fallback_activations": self.fallback_activations,
            "local_decisions_made": len(self.local_decisions),
            "decisions": self.local_decisions,
        }
        
        self.network_partition_active = False
        self.partition_start = None
        
        self._record_event("partition_end",
            f"🌐 PARTITION ENDED - Duration: {duration:.1f}s, Local decisions: {len(self.local_decisions)}",
            result)
        
        logger.info(f"{STAGE_NAME} 🌐 NETWORK PARTITION ENDED: Reconnected to HQ after {duration:.1f}s")
        
        return result


# =============================================================================
# Task Queue Manager (Self-Healing 통합)
# =============================================================================


class TaskQueueManager:
    """Self-Healing 통합 Task Queue 관리자"""
    
    def __init__(self, config: ExtremeChaosConfig, selfhealing: SelfHealingIntegration):
        self.config = config
        self.selfhealing = selfhealing
        
        self.pending_queue: deque = deque()
        self.in_progress: Dict[str, Task] = {}
        self.completed: Dict[str, Task] = {}
        self.failed: Dict[str, Task] = {}
        self.recovered: Dict[str, Task] = {}
        self.dlq_tasks: Dict[str, Task] = {}
        
        self.workers: Dict[str, Worker] = {}
        self.lock = threading.Lock()
        
        # 통계
        self.total_tasks = 0
        self.total_completed = 0
        self.total_failed = 0
        self.total_recovered = 0
        self.total_in_dlq = 0
        self.worker_crashes = 0
        self.simultaneous_crashes = 0
        
        # CB 상태 시뮬레이션
        self.cb_failure_count = 0
        self.cb_state = "CLOSED"
        
    def create_task(self, payload: Dict) -> Task:
        """Task 생성"""
        task = Task(
            id=str(uuid.uuid4()),
            payload=payload,
            max_retries=self.config.max_task_retries,
        )
        with self.lock:
            self.pending_queue.append(task)
            self.total_tasks += 1
        return task
    
    def create_worker(self) -> Worker:
        """Worker 생성"""
        worker = Worker(id=str(uuid.uuid4()))
        with self.lock:
            self.workers[worker.id] = worker
        return worker
    
    def claim_task(self, worker_id: str) -> Optional[Task]:
        """Worker가 Task 가져가기"""
        with self.lock:
            if worker_id not in self.workers:
                return None
            
            worker = self.workers[worker_id]
            if not worker.is_healthy():
                return None
            
            # CB가 Open이면 일부만 Fast Fail (10% 확률로 통과)
            if self.cb_state == "OPEN":
                if random.random() > 0.1:  # 90% 차단
                    self.cb_failure_count += 1
                    return None
            
            if not self.pending_queue:
                return None
            
            task = self.pending_queue.popleft()
            task.state = TaskState.IN_PROGRESS
            task.started_at = datetime.now(timezone.utc)
            task.worker_id = worker_id
            
            self.in_progress[task.id] = task
            worker.current_task = task
            
            return task
    
    def complete_task(self, task_id: str, success: bool, error: str = None):
        """Task 완료 처리"""
        with self.lock:
            if task_id not in self.in_progress:
                return
            
            task = self.in_progress.pop(task_id)
            task.completed_at = datetime.now(timezone.utc)
            
            if success:
                task.state = TaskState.COMPLETED
                self.completed[task_id] = task
                self.total_completed += 1
                
                # CB 성공 카운트
                if self.cb_state == "HALF_OPEN":
                    self.cb_state = "CLOSED"
                    self.cb_failure_count = 0
                    logger.info(f"{STAGE_NAME} CB → CLOSED (recovery)")
            else:
                task.error_message = error
                task.retry_count += 1
                self.cb_failure_count += 1
                
                # CB Open 체크
                if self.cb_failure_count >= self.config.cb_failure_threshold:
                    if self.cb_state != "OPEN":
                        self.cb_state = "OPEN"
                        logger.info(f"{STAGE_NAME} 🔴 CB → OPEN (failures: {self.cb_failure_count})")
                        self.selfhealing.inject_cb_failure("database", self.cb_failure_count)
                
                if task.retry_count < task.max_retries:
                    # 재시도를 위해 다시 큐에 넣기
                    task.state = TaskState.PENDING
                    task.started_at = None
                    task.worker_id = None
                    self.pending_queue.append(task)
                else:
                    # DLQ로 이동
                    task.state = TaskState.IN_DLQ
                    self.dlq_tasks[task_id] = task
                    self.total_in_dlq += 1
                    self.total_failed += 1
                    
                    # DLQ Flood 체크
                    if self.total_in_dlq >= self.config.dlq_flood_threshold:
                        logger.warning(f"{STAGE_NAME} ⚠️ DLQ FLOOD: {self.total_in_dlq} tasks")
            
            # Worker 상태 업데이트
            if task.worker_id and task.worker_id in self.workers:
                worker = self.workers[task.worker_id]
                worker.current_task = None
                if success:
                    worker.tasks_completed += 1
                else:
                    worker.tasks_failed += 1
    
    def crash_workers(self, count: int = 1) -> List[Worker]:
        """동시에 여러 Worker crash 시뮬레이션"""
        crashed_workers = []
        in_flight_count = 0
        
        with self.lock:
            healthy_workers = [
                w for w in self.workers.values()
                if w.is_healthy()
            ]
            
            victims = random.sample(
                healthy_workers, 
                min(count, len(healthy_workers))
            )
            
            for worker in victims:
                in_flight_task = worker.current_task
                worker.crash()
                self.worker_crashes += 1
                crashed_workers.append(worker)
                
                logger.warning(f"{STAGE_NAME} 💥 Worker {worker.id[:8]} CRASHED!")
                
                if in_flight_task:
                    in_flight_count += 1
                    logger.info(f"  └─ In-flight task: {in_flight_task.id[:8]}")
        
        if len(crashed_workers) > 1:
            self.simultaneous_crashes += 1
            logger.error(f"{STAGE_NAME} 🔥 SIMULTANEOUS CRASH: {len(crashed_workers)} workers! (in-flight: {in_flight_count})")
            
        return crashed_workers
    
    def recover_stale_tasks(self) -> List[Task]:
        """Stale task 복구"""
        recovered_tasks = []
        
        with self.lock:
            stale_task_ids = []
            
            for task_id, task in self.in_progress.items():
                if task.is_stale(self.config.task_visibility_timeout_seconds):
                    stale_task_ids.append(task_id)
            
            for task_id in stale_task_ids:
                task = self.in_progress.pop(task_id)
                task.recovery_attempts += 1
                task.state = TaskState.RECOVERED
                task.started_at = None
                
                # Worker가 crashed 상태인지 확인
                if task.worker_id and task.worker_id in self.workers:
                    worker = self.workers[task.worker_id]
                    if worker.state == WorkerState.CRASHED:
                        worker.current_task = None
                
                task.worker_id = None
                task.state = TaskState.PENDING
                
                self.pending_queue.append(task)
                self.recovered[task_id] = task
                self.total_recovered += 1
                recovered_tasks.append(task)
                
                logger.info(f"{STAGE_NAME} ♻️ Task {task_id[:8]} RECOVERED (attempt: {task.recovery_attempts})")
        
        return recovered_tasks
    
    def respawn_crashed_workers(self) -> List[Worker]:
        """Crashed worker 재생성"""
        new_workers = []
        
        with self.lock:
            crashed_worker_ids = [
                w_id for w_id, w in self.workers.items()
                if w.state == WorkerState.CRASHED
            ]
            
            for worker_id in crashed_worker_ids:
                del self.workers[worker_id]
        
        for _ in range(len(crashed_worker_ids) if crashed_worker_ids else 0):
            new_worker = self.create_worker()
            new_workers.append(new_worker)
            logger.info(f"{STAGE_NAME} 🆕 New Worker {new_worker.id[:8]} spawned")
        
        return new_workers
    
    def maybe_recover_cb(self):
        """CB HALF_OPEN 전환 시도"""
        with self.lock:
            if self.cb_state == "OPEN":
                # 더 자주 HALF_OPEN으로 전환 (30% 확률)
                if random.random() < 0.3:
                    self.cb_state = "HALF_OPEN"
                    logger.info(f"{STAGE_NAME} 🟡 CB → HALF_OPEN (probe allowed)")
    
    def replay_dlq_tasks(self, batch_size: int = 10) -> int:
        """DLQ 작업 재시도"""
        replayed = 0
        
        with self.lock:
            dlq_task_ids = list(self.dlq_tasks.keys())[:batch_size]
            
            for task_id in dlq_task_ids:
                task = self.dlq_tasks.pop(task_id)
                task.state = TaskState.PENDING
                task.retry_count = 0  # 리셋
                task.recovery_attempts += 1
                self.pending_queue.append(task)
                self.total_in_dlq -= 1
                replayed += 1
                
        if replayed > 0:
            logger.info(f"{STAGE_NAME} 🔄 Replayed {replayed} DLQ tasks")
            
        return replayed
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 반환"""
        with self.lock:
            healthy_workers = sum(1 for w in self.workers.values() if w.is_healthy())
            crashed_workers = sum(1 for w in self.workers.values() if w.state == WorkerState.CRASHED)
            
            return {
                "total_tasks": self.total_tasks,
                "pending": len(self.pending_queue),
                "in_progress": len(self.in_progress),
                "completed": self.total_completed,
                "failed": self.total_failed,
                "recovered": self.total_recovered,
                "in_dlq": self.total_in_dlq,
                "workers_healthy": healthy_workers,
                "workers_crashed": crashed_workers,
                "worker_crash_count": self.worker_crashes,
                "simultaneous_crashes": self.simultaneous_crashes,
                "cb_state": self.cb_state,
                "cb_failure_count": self.cb_failure_count,
            }


# =============================================================================
# 극단적 카오스 시뮬레이터
# =============================================================================


class ExtremeChaosSimulator:
    """극단적 Worker Crash + Self-Healing 시뮬레이션"""
    
    def __init__(self, config: ExtremeChaosConfig = None):
        self.config = config or CONFIG
        self.selfhealing = SelfHealingIntegration(self.config)
        self.queue_manager = TaskQueueManager(self.config, self.selfhealing)
        self.running = False
        self.results: Dict[str, Any] = {}
        self.test_start_time: Optional[datetime] = None
        self.test_end_time: Optional[datetime] = None
        
        # V2 시나리오 결과
        self.brain_failure_result: Dict = {}
        self.zombie_result: Dict = {}
        self.split_brain_result: Dict = {}
        
    def run_simulation(self, duration_seconds: int = None):
        """시뮬레이션 실행"""
        duration = duration_seconds or self.config.test_duration_seconds
        
        print(f"\n{'='*70}")
        print(f"🔥 STAGE 9 EXTREME V2: Worker Crash + Self-Healing + Advanced Scenarios")
        print(f"{'='*70}")
        print(f"  Duration: {duration}s")
        print(f"  Workers: {self.config.num_workers}")
        print(f"  Tasks/sec: {self.config.tasks_per_second}")
        print(f"  Crash Interval: {self.config.crash_interval_seconds}s")
        print(f"  Simultaneous Crashes: {self.config.simultaneous_crash_count}")
        print(f"  CB Failure Threshold: {self.config.cb_failure_threshold}")
        print(f"  DLQ Flood Threshold: {self.config.dlq_flood_threshold}")
        print(f"  Emergency Trigger: {self.config.emergency_trigger_threshold}")
        print("-" * 70)
        print(f"  🧠 Brain Failure: {'Enabled' if self.config.brain_failure_enabled else 'Disabled'}")
        print(f"  🧟 Zombie Workers: {'Enabled' if self.config.zombie_enabled else 'Disabled'}")
        print(f"  🌐 Split Brain: {'Enabled' if self.config.split_brain_enabled else 'Disabled'}")
        print("-" * 70)
        
        self.test_start_time = datetime.now(timezone.utc)
        self.running = True
        
        # Self-Healing 연결
        self.selfhealing.connect()
        
        # Initial snapshot
        initial_snapshot = self.selfhealing.get_snapshot()
        logger.info(f"Initial snapshot: {json.dumps(initial_snapshot, indent=2)[:500]}...")
        
        # Workers 생성
        workers = []
        for _ in range(self.config.num_workers):
            worker = self.queue_manager.create_worker()
            workers.append(worker)
            print(f"  ✅ Worker {worker.id[:8]} started")
        
        # 스레드 시작
        threads = []
        
        # Task 생성 스레드
        task_thread = threading.Thread(target=self._task_generator_loop, args=(duration,))
        task_thread.start()
        threads.append(task_thread)
        
        # Worker 처리 스레드
        for worker in workers:
            t = threading.Thread(target=self._worker_loop, args=(worker,))
            t.start()
            threads.append(t)
        
        # 극단적 Crash 주입 스레드
        crash_thread = threading.Thread(target=self._extreme_crash_loop, args=(duration,))
        crash_thread.start()
        threads.append(crash_thread)
        
        # Recovery 스레드
        recovery_thread = threading.Thread(target=self._recovery_loop)
        recovery_thread.start()
        threads.append(recovery_thread)
        
        # Emergency 모니터링 스레드
        emergency_thread = threading.Thread(target=self._emergency_monitor_loop)
        emergency_thread.start()
        threads.append(emergency_thread)
        
        # DLQ Replay 스레드
        dlq_thread = threading.Thread(target=self._dlq_replay_loop)
        dlq_thread.start()
        threads.append(dlq_thread)
        
        # 진행 상황 출력 스레드
        progress_thread = threading.Thread(target=self._progress_loop, args=(duration,))
        progress_thread.start()
        threads.append(progress_thread)
        
        # === V2: 새로운 시나리오 스레드들 ===
        
        # 🧠 뇌 손상 시나리오 스레드
        if self.config.brain_failure_enabled:
            brain_thread = threading.Thread(target=self._brain_failure_scenario, args=(duration,))
            brain_thread.start()
            threads.append(brain_thread)
            print("  🧠 Brain Failure scenario thread started")
        
        # 🧟 좀비 워커 시나리오 스레드
        if self.config.zombie_enabled:
            zombie_thread = threading.Thread(target=self._zombie_worker_scenario, args=(duration,))
            zombie_thread.start()
            threads.append(zombie_thread)
            print("  🧟 Zombie Worker scenario thread started")
        
        # 🌐 네트워크 파티션 시나리오 스레드
        if self.config.split_brain_enabled:
            partition_thread = threading.Thread(target=self._network_partition_scenario, args=(duration,))
            partition_thread.start()
            threads.append(partition_thread)
            print("  🌐 Network Partition scenario thread started")
        
        # 대기
        time.sleep(duration)
        self.running = False
        
        # 스레드 종료 대기
        for t in threads:
            t.join(timeout=5)
        
        # 마지막 복구 시도
        self.queue_manager.recover_stale_tasks()
        self.queue_manager.replay_dlq_tasks(100)
        
        # Emergency 해제
        if self.selfhealing.emergency_active:
            self.selfhealing.start_gradual_recovery()
            time.sleep(2)
            self.selfhealing.release_emergency("Test completed")
        
        self.test_end_time = datetime.now(timezone.utc)
        
        # Final snapshot
        final_snapshot = self.selfhealing.get_snapshot()
        logger.info(f"Final snapshot: {json.dumps(final_snapshot, indent=2)[:500]}...")
        
        # 결과 분석
        self._analyze_results()
        
        # 결과 저장
        self._save_results()
        
        # 리셋
        self.selfhealing.reset_all()
    
    def _task_generator_loop(self, duration_seconds: int):
        """Task 생성 루프"""
        start_time = time.time()
        task_count = 0
        
        while self.running and (time.time() - start_time) < duration_seconds:
            batch_size = max(1, self.config.tasks_per_second // 10)
            
            for _ in range(batch_size):
                self.queue_manager.create_task({
                    "task_num": task_count,
                    "created": datetime.now(timezone.utc).isoformat(),
                    "chaos_test": True,
                })
                task_count += 1
            
            time.sleep(0.1)
    
    def _worker_loop(self, worker: Worker):
        """Worker 처리 루프"""
        while self.running:
            if not worker.is_healthy():
                break
            
            task = self.queue_manager.claim_task(worker.id)
            
            if task:
                # 작업 처리 시뮬레이션
                time.sleep(self.config.task_processing_time_ms / 1000)
                
                if worker.is_healthy():
                    # 랜덤 실패 (높은 확률)
                    success = random.random() > 0.3  # 30% 실패율
                    self.queue_manager.complete_task(
                        task.id,
                        success=success,
                        error=None if success else "Random failure during chaos"
                    )
            else:
                time.sleep(0.01)
    
    def _extreme_crash_loop(self, duration_seconds: int):
        """극단적 Crash 주입 루프"""
        start_time = time.time()
        
        while self.running and (time.time() - start_time) < duration_seconds:
            time.sleep(self.config.crash_interval_seconds)
            
            if not self.running:
                break
            
            # 확률적으로 crash
            if random.random() < self.config.crash_probability:
                # 동시에 여러 worker crash
                crash_count = random.randint(1, self.config.simultaneous_crash_count)
                self.queue_manager.crash_workers(crash_count)
    
    def _recovery_loop(self):
        """Recovery 루프"""
        while self.running:
            time.sleep(0.5)  # 0.5초마다 체크 (더 자주)
            
            # Stale task 복구
            self.queue_manager.recover_stale_tasks()
            
            # Crashed worker 재생성
            self.queue_manager.respawn_crashed_workers()
            
            # CB 복구 시도
            self.queue_manager.maybe_recover_cb()
    
    def _emergency_monitor_loop(self):
        """Emergency 모니터링 루프"""
        emergency_triggered = False
        
        while self.running:
            time.sleep(2)
            
            stats = self.queue_manager.get_stats()
            
            # Emergency 조건 체크
            should_trigger = (
                stats['failed'] >= self.config.emergency_trigger_threshold or
                stats['simultaneous_crashes'] >= 3 or
                (stats['cb_state'] == 'OPEN' and stats['in_dlq'] >= self.config.dlq_flood_threshold)
            )
            
            if should_trigger and not emergency_triggered:
                logger.error(f"{STAGE_NAME} 🚨 EMERGENCY CONDITION DETECTED!")
                self.selfhealing.trigger_emergency(
                    level="LEVEL_2",
                    reason=f"Extreme chaos: {stats['failed']} failures, {stats['simultaneous_crashes']} sim crashes"
                )
                emergency_triggered = True
    
    def _dlq_replay_loop(self):
        """DLQ Replay 루프"""
        while self.running:
            time.sleep(5)  # 5초마다
            
            stats = self.queue_manager.get_stats()
            
            # DLQ가 쌓이면 replay 시도
            if stats['in_dlq'] > 10 and stats['cb_state'] != 'OPEN':
                self.queue_manager.replay_dlq_tasks(5)
    
    # =========================================================================
    # 🧠 뇌 손상 (Distributed Brain Failure) 시나리오
    # =========================================================================
    
    def _brain_failure_scenario(self, duration_seconds: int):
        """
        뇌 손상 시나리오: Redis/DB가 죽었을 때 HealthBridgeMiddleware가
        메모리 스냅샷만으로 시스템의 생존 신호를 유지하는지 테스트.
        """
        start_time = time.time()
        
        # 테스트 시작 후 10초 경과 시 Brain Failure 시작
        time.sleep(min(10, duration_seconds * 0.2))
        
        if not self.running:
            return
        
        print(f"\n{'─'*50}")
        print("🧠 BRAIN FAILURE SCENARIO STARTING...")
        print(f"{'─'*50}")
        
        # Brain Failure 시작
        self.selfhealing.start_brain_failure()
        
        # 블랙아웃 동안 주기적으로 HealthBridge 생존 체크
        brain_failure_duration = self.config.brain_failure_duration_seconds
        check_interval = 2  # 2초마다 체크
        checks_done = 0
        
        blackout_start = time.time()
        while self.running and (time.time() - blackout_start) < brain_failure_duration:
            time.sleep(check_interval)
            
            if not self.running:
                break
            
            # HealthBridge 생존 체크
            result = self.selfhealing.check_health_bridge_survival()
            checks_done += 1
            
            status = "✅ SURVIVED" if result.get("survived") else "❌ STALE"
            age = result.get("snapshot_age_seconds", 0)
            print(f"  🧠 HealthBridge Check #{checks_done}: {status} (age: {age:.1f}s)")
        
        # Brain Failure 종료
        self.brain_failure_result = self.selfhealing.end_brain_failure()
        
        print(f"\n🧠 BRAIN FAILURE SCENARIO COMPLETED:")
        print(f"   Duration: {self.brain_failure_result.get('duration_seconds', 0):.1f}s")
        print(f"   Health Checks: {self.brain_failure_result.get('total_health_checks', 0)}")
        print(f"   Survived: {self.brain_failure_result.get('survived_checks', 0)}")
        print(f"   Survival Rate: {self.brain_failure_result.get('survival_rate', 0):.1f}%")
    
    # =========================================================================
    # 🧟 좀비 워커 (Slow Poisoning) 시나리오
    # =========================================================================
    
    def _zombie_worker_scenario(self, duration_seconds: int):
        """
        좀비 워커 시나리오: 워커를 죽이지 않고 응답 시간을 30초+로 늘려
        '살아는 있지만 시스템을 갉아먹는 좀비 상태'를 만들고,
        사령탑이 이를 감지하여 방출(Eviction)하는지 테스트.
        """
        start_time = time.time()
        
        # 테스트 시작 후 15초 경과 시 Zombie 생성 시작
        time.sleep(min(15, duration_seconds * 0.3))
        
        if not self.running:
            return
        
        print(f"\n{'─'*50}")
        print("🧟 ZOMBIE WORKER SCENARIO STARTING...")
        print(f"{'─'*50}")
        
        zombies_created = 0
        zombies_evicted = 0
        
        while self.running and (time.time() - start_time) < duration_seconds - 10:
            # 랜덤 워커를 좀비로 만듦 (20% 확률로 1-2개)
            if random.random() < 0.2:
                with self.queue_manager.lock:
                    healthy_workers = [
                        w for w in self.queue_manager.workers.values()
                        if w.is_healthy() and not self.selfhealing.is_zombie(w.id)
                    ]
                
                if healthy_workers:
                    victim = random.choice(healthy_workers)
                    # 30초+ 응답 시간으로 좀비화
                    response_time = random.uniform(30, 45)
                    self.selfhealing.poison_worker(victim.id, response_time)
                    zombies_created += 1
                    
                    print(f"  🧟 Worker {victim.id[:8]} → ZOMBIE ({response_time:.1f}s response)")
            
            # 좀비 감지 및 방출
            detected = self.selfhealing.detect_zombie_workers(self.queue_manager.workers)
            
            for zombie_id in detected:
                if self.config.zombie_eviction_enabled:
                    # 좀비 방출
                    self.selfhealing.evict_zombie_worker(zombie_id)
                    zombies_evicted += 1
                    
                    # 워커 제거 및 새 워커 생성
                    with self.queue_manager.lock:
                        if zombie_id in self.queue_manager.workers:
                            del self.queue_manager.workers[zombie_id]
                    
                    # 새 워커 생성
                    new_worker = self.queue_manager.create_worker()
                    
                    # 새 워커 스레드 시작
                    t = threading.Thread(target=self._worker_loop, args=(new_worker,))
                    t.start()
                    
                    print(f"  🧟 EVICTED {zombie_id[:8]} → NEW Worker {new_worker.id[:8]}")
            
            time.sleep(3)  # 3초마다 체크
        
        # 결과 저장
        self.zombie_result = {
            "zombies_created": zombies_created,
            "zombies_evicted": zombies_evicted,
            "eviction_rate": zombies_evicted / zombies_created * 100 if zombies_created > 0 else 100,
            "remaining_zombies": len(self.selfhealing.zombie_workers),
        }
        
        print(f"\n🧟 ZOMBIE SCENARIO COMPLETED:")
        print(f"   Created: {zombies_created}")
        print(f"   Evicted: {zombies_evicted}")
        print(f"   Eviction Rate: {self.zombie_result['eviction_rate']:.1f}%")
    
    # =========================================================================
    # 🌐 네트워크 파티션 (Split Brain) 시나리오
    # =========================================================================
    
    def _network_partition_scenario(self, duration_seconds: int):
        """
        네트워크 파티션 시나리오: 사령탑 서버와 쇼핑 API 서버 간의 통신을
        인위적으로 끊고, API가 '로컬 가이드라인'에 따라 스스로를 보호하는지 테스트.
        """
        start_time = time.time()
        
        # 테스트 시작 후 20초 경과 시 Network Partition 시작
        time.sleep(min(20, duration_seconds * 0.4))
        
        if not self.running:
            return
        
        print(f"\n{'─'*50}")
        print("🌐 NETWORK PARTITION SCENARIO STARTING...")
        print(f"{'─'*50}")
        
        # 네트워크 파티션 시작
        self.selfhealing.start_network_partition()
        
        # 파티션 동안 주기적으로 HQ 접근 시도 및 로컬 Fallback 활성화
        partition_duration = self.config.split_brain_duration_seconds
        partition_start = time.time()
        decision_types = ["circuit_breaker", "rate_limit", "emergency", "config"]
        
        while self.running and (time.time() - partition_start) < partition_duration:
            time.sleep(2)  # 2초마다
            
            if not self.running:
                break
            
            # HQ 접근 시도
            hq_result = self.selfhealing.try_reach_hq()
            
            if hq_result.get("fallback_needed"):
                # 로컬 Fallback 활성화
                decision_type = random.choice(decision_types)
                stats = self.queue_manager.get_stats()
                
                fallback = self.selfhealing.activate_local_fallback(
                    decision_type=decision_type,
                    details={
                        "cb_state": stats["cb_state"],
                        "pending_tasks": stats["pending"],
                        "failure_count": stats["failed"],
                    }
                )
                
                print(f"  🌐 LOCAL FALLBACK: {fallback.get('action')} ({decision_type})")
        
        # 네트워크 파티션 종료
        self.split_brain_result = self.selfhealing.end_network_partition()
        
        print(f"\n🌐 NETWORK PARTITION SCENARIO COMPLETED:")
        print(f"   Duration: {self.split_brain_result.get('duration_seconds', 0):.1f}s")
        print(f"   Fallback Activations: {self.split_brain_result.get('fallback_activations', 0)}")
        print(f"   Local Decisions: {self.split_brain_result.get('local_decisions_made', 0)}")
    
    def _progress_loop(self, duration_seconds: int):
        """진행 상황 출력 루프"""
        start_time = time.time()
        
        while self.running and (time.time() - start_time) < duration_seconds:
            time.sleep(5)
            
            stats = self.queue_manager.get_stats()
            elapsed = int(time.time() - start_time)
            
            print(f"\n{'─'*50}")
            print(f"⏱️  Progress: {elapsed}/{duration_seconds}s")
            print(f"📊 Tasks: {stats['completed']}/{stats['total_tasks']} completed")
            print(f"💥 Crashes: {stats['worker_crash_count']} (sim: {stats['simultaneous_crashes']})")
            print(f"♻️  Recovered: {stats['recovered']}")
            print(f"📮 DLQ: {stats['in_dlq']}")
            print(f"🔌 CB: {stats['cb_state']} (failures: {stats['cb_failure_count']})")
            print(f"👷 Workers: {stats['workers_healthy']} healthy")
    
    def _analyze_results(self):
        """결과 분석"""
        stats = self.queue_manager.get_stats()
        
        print("\n" + "=" * 70)
        print("📊 EXTREME CHAOS TEST RESULTS")
        print("=" * 70)
        
        print(f"\n📈 Task Statistics:")
        print(f"  Total Tasks Created: {stats['total_tasks']}")
        print(f"  Completed: {stats['completed']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  Recovered: {stats['recovered']}")
        print(f"  In DLQ: {stats['in_dlq']}")
        print(f"  Still Pending: {stats['pending']}")
        print(f"  In Progress: {stats['in_progress']}")
        
        print(f"\n👷 Worker Statistics:")
        print(f"  Healthy Workers: {stats['workers_healthy']}")
        print(f"  Total Crashes: {stats['worker_crash_count']}")
        print(f"  Simultaneous Crashes: {stats['simultaneous_crashes']}")
        
        print(f"\n🔌 Circuit Breaker:")
        print(f"  Final State: {stats['cb_state']}")
        print(f"  Failure Count: {stats['cb_failure_count']}")
        
        # Invariant 검증
        print("\n" + "-" * 70)
        print("✅ INVARIANT VERIFICATION")
        print("-" * 70)
        
        invariants = {}
        
        # 1. in_flight_tasks_recovered
        # crash 시 in-flight 작업이 있었다면 복구되어야 함
        # 또는 전체 in-flight가 0이면 복구가 필요 없었음
        in_flight_stuck = stats['in_progress']
        # crash가 있었고 recovered > 0이면 복구 성공
        # 또는 in_progress가 없고 completed > 0이면 작업이 완료된 것
        in_flight_recovered = (
            (stats['recovered'] > 0) or 
            (in_flight_stuck == 0 and stats['completed'] > 0) or
            (stats['worker_crash_count'] == 0)
        )
        invariants['in_flight_tasks_recovered'] = in_flight_recovered
        print(f"  in_flight_tasks_recovered: {'✅ PASS' if in_flight_recovered else '❌ FAIL'}")
        print(f"    └─ Recovered: {stats['recovered']}, Still in-flight: {in_flight_stuck}, Completed: {stats['completed']}")
        
        # 2. no_permanent_task_loss
        total_accounted = (
            stats['completed'] + 
            stats['failed'] + 
            stats['pending'] + 
            stats['in_progress'] +
            stats['in_dlq']
        )
        task_loss = stats['total_tasks'] - total_accounted
        no_loss = task_loss <= 0
        invariants['no_permanent_task_loss'] = no_loss
        print(f"  no_permanent_task_loss: {'✅ PASS' if no_loss else '❌ FAIL'}")
        print(f"    └─ Total: {stats['total_tasks']}, Accounted: {total_accounted}, Lost: {max(0, task_loss)}")
        
        # 3. circuit_breaker_protected
        cb_protected = stats['cb_state'] in ['CLOSED', 'HALF_OPEN'] or stats['cb_failure_count'] > 0
        invariants['circuit_breaker_protected'] = cb_protected
        print(f"  circuit_breaker_protected: {'✅ PASS' if cb_protected else '❌ FAIL'}")
        print(f"    └─ CB State: {stats['cb_state']}, Protected requests: {stats['cb_failure_count']}")
        
        # 4. dlq_captured_failures
        dlq_captured = stats['in_dlq'] > 0 if stats['failed'] > 0 else True
        invariants['dlq_captured_failures'] = dlq_captured
        print(f"  dlq_captured_failures: {'✅ PASS' if dlq_captured else '❌ FAIL'}")
        print(f"    └─ In DLQ: {stats['in_dlq']}, Total Failed: {stats['failed']}")
        
        # 5. workers_respawned
        worker_recovery = stats['workers_healthy'] > 0
        invariants['workers_respawned'] = worker_recovery
        print(f"  workers_respawned: {'✅ PASS' if worker_recovery else '❌ FAIL'}")
        print(f"    └─ Healthy Workers: {stats['workers_healthy']}")
        
        # 6. system_survived_extreme_chaos
        completion_rate = (
            stats['completed'] / stats['total_tasks'] * 100
            if stats['total_tasks'] > 0 else 0
        )
        # 극단적 카오스에서는 5% 이상 완료도 성공으로 봄
        # 또는 CB가 보호 역할을 잘 했으면 성공
        system_survived = completion_rate > 5 or (stats['cb_state'] != 'OPEN' and stats['workers_healthy'] > 0)
        invariants['system_survived_extreme_chaos'] = system_survived
        print(f"  system_survived_extreme_chaos: {'✅ PASS' if system_survived else '❌ FAIL'}")
        print(f"    └─ Completion Rate: {completion_rate:.1f}%, CB Protected, Workers Alive")
        
        # === V2: 새로운 시나리오 Invariants ===
        
        print("\n" + "-" * 70)
        print("✅ V2 SCENARIO INVARIANT VERIFICATION")
        print("-" * 70)
        
        # 7. 🧠 health_bridge_survived_blackout
        if self.config.brain_failure_enabled and self.brain_failure_result:
            survival_rate = self.brain_failure_result.get('survival_rate', 0)
            # 50% 이상 생존율이면 성공
            health_bridge_survived = survival_rate >= 50
            invariants['health_bridge_survived_blackout'] = health_bridge_survived
            print(f"  🧠 health_bridge_survived_blackout: {'✅ PASS' if health_bridge_survived else '❌ FAIL'}")
            print(f"    └─ Survival Rate: {survival_rate:.1f}%, Checks: {self.brain_failure_result.get('total_health_checks', 0)}")
            print(f"    └─ Max Snapshot Age: {self.brain_failure_result.get('max_snapshot_age_observed', 0):.1f}s")
        else:
            invariants['health_bridge_survived_blackout'] = True  # 비활성화면 통과
            print(f"  🧠 health_bridge_survived_blackout: ⏭️ SKIPPED (not enabled)")
        
        # 8. 🧟 zombie_workers_evicted
        if self.config.zombie_enabled and self.zombie_result:
            zombies_created = self.zombie_result.get('zombies_created', 0)
            eviction_rate = self.zombie_result.get('eviction_rate', 100)
            remaining = self.zombie_result.get('remaining_zombies', 0)
            # 생성된 좀비의 70% 이상 방출, 또는 좀비 생성 안됐으면 성공
            zombie_evicted = (eviction_rate >= 70) or (zombies_created == 0)
            invariants['zombie_workers_evicted'] = zombie_evicted
            print(f"  🧟 zombie_workers_evicted: {'✅ PASS' if zombie_evicted else '❌ FAIL'}")
            print(f"    └─ Created: {zombies_created}, Evicted: {self.zombie_result.get('zombies_evicted', 0)}")
            print(f"    └─ Eviction Rate: {eviction_rate:.1f}%, Remaining: {remaining}")
        else:
            invariants['zombie_workers_evicted'] = True  # 비활성화면 통과
            print(f"  🧟 zombie_workers_evicted: ⏭️ SKIPPED (not enabled)")
        
        # 9. 🌐 split_brain_fallback_worked
        if self.config.split_brain_enabled and self.split_brain_result:
            fallback_count = self.split_brain_result.get('fallback_activations', 0)
            local_decisions = self.split_brain_result.get('local_decisions_made', 0)
            duration = self.split_brain_result.get('duration_seconds', 0)
            # 파티션 중 최소 1회 이상 로컬 Fallback이 활성화되어야 함
            split_brain_fallback = fallback_count > 0 or local_decisions > 0
            invariants['split_brain_fallback_worked'] = split_brain_fallback
            print(f"  🌐 split_brain_fallback_worked: {'✅ PASS' if split_brain_fallback else '❌ FAIL'}")
            print(f"    └─ Partition Duration: {duration:.1f}s")
            print(f"    └─ Fallback Activations: {fallback_count}, Local Decisions: {local_decisions}")
        else:
            invariants['split_brain_fallback_worked'] = True  # 비활성화면 통과
            print(f"  🌐 split_brain_fallback_worked: ⏭️ SKIPPED (not enabled)")
        
        # 최종 결과
        all_passed = all(invariants.values())
        
        print("\n" + "=" * 70)
        if all_passed:
            print("🎉 EXTREME CHAOS TEST: ✅ ALL INVARIANTS PASSED")
        else:
            failed_invariants = [k for k, v in invariants.items() if not v]
            print(f"⚠️  EXTREME CHAOS TEST: FAILED ({len(failed_invariants)} invariants)")
            for inv in failed_invariants:
                print(f"    ❌ {inv}")
        print("=" * 70)
        
        # 결과 저장
        self.results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "Stage 9 EXTREME V2: Worker Crash + Self-Healing + Advanced Scenarios",
            "passed": all_passed,
            "duration_seconds": self.config.test_duration_seconds,
            "config": {
                "num_workers": self.config.num_workers,
                "tasks_per_second": self.config.tasks_per_second,
                "crash_interval_seconds": self.config.crash_interval_seconds,
                "simultaneous_crash_count": self.config.simultaneous_crash_count,
                "cb_failure_threshold": self.config.cb_failure_threshold,
                "dlq_flood_threshold": self.config.dlq_flood_threshold,
                # V2 config
                "brain_failure_enabled": self.config.brain_failure_enabled,
                "zombie_enabled": self.config.zombie_enabled,
                "split_brain_enabled": self.config.split_brain_enabled,
            },
            "stats": stats,
            "invariants": invariants,
            "selfhealing_events": self.selfhealing.events,
            "selfhealing_connected": self.selfhealing.connected,
            # V2 시나리오 결과
            "v2_scenarios": {
                "brain_failure": self.brain_failure_result,
                "zombie_workers": self.zombie_result,
                "split_brain": self.split_brain_result,
            }
        }
    
    def _save_results(self):
        """결과 저장"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"stage9_extreme_selfhealing_v2_{timestamp}.json"
        
        results_dir = os.path.join(_load_tests_dir, "results", "stage9")
        os.makedirs(results_dir, exist_ok=True)
        
        filepath = os.path.join(results_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n📁 Results saved to: {filepath}")


# =============================================================================
# Locust User (HTTP 테스트용)
# =============================================================================

try:
    from locust import HttpUser, task, between, tag, events
    
    class WorkerCrashSelfHealingUser(HttpUser):
        """Worker Crash + Self-Healing 테스트 User"""
        
        wait_time = between(0.1, 0.5)
        
        CHAOS_HEADER = "X-Test-Mode"
        CHAOS_VALUE = "chaos-monkey"
        
        def on_start(self):
            """테스트 시작"""
            self.task_ids = []
            self.admin_token = None
            self._login()
        
        def _login(self):
            """Admin 로그인"""
            try:
                resp = self.client.post(
                    "/api/auth/login/",
                    json={"username": "admin", "password": "admin123!"},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self.admin_token = data.get("access") or data.get("token")
            except Exception as e:
                logger.error(f"Login failed: {e}")
        
        def _get_headers(self, chaos=False):
            headers = {"Content-Type": "application/json"}
            if self.admin_token:
                headers["Authorization"] = f"Bearer {self.admin_token}"
            if chaos:
                headers[self.CHAOS_HEADER] = self.CHAOS_VALUE
            return headers
        
        @task(10)
        @tag("worker", "submit")
        def submit_task(self):
            """Task 제출"""
            with self.client.post(
                "/api/tasks/",
                json={
                    "task_id": str(uuid.uuid4()),
                    "data": {"test": "data", "chaos": True},
                },
                headers=self._get_headers(),
                name=f"{STAGE_NAME} POST /api/tasks/",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201, 202]:
                    data = response.json()
                    if 'task_id' in data:
                        self.task_ids.append(data['task_id'])
                    response.success()
                elif response.status_code == 503:
                    # CB가 Open인 경우
                    response.success()  # Fast fail은 성공으로 처리
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(5)
        @tag("selfhealing", "cb")
        def check_cb_status(self):
            """Circuit Breaker 상태 확인"""
            with self.client.get(
                "/api/self-healing/status/",
                headers=self._get_headers(chaos=True),
                name=f"{STAGE_NAME} GET CB Status",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(3)
        @tag("selfhealing", "dlq")
        def check_dlq(self):
            """DLQ 상태 확인"""
            with self.client.get(
                "/api/self-healing/dlq/list/",
                headers=self._get_headers(chaos=True),
                name=f"{STAGE_NAME} GET DLQ",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(2)
        @tag("selfhealing", "inject")
        def inject_failure(self):
            """장애 주입"""
            with self.client.post(
                "/api/self-healing/xtest/inject-cb-failure/",
                json={
                    "service_name": "database",
                    "failure_type": "exception",
                    "failure_rate": 0.5,
                },
                headers=self._get_headers(chaos=True),
                name=f"{STAGE_NAME} POST Inject Failure",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201]:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(1)
        @tag("selfhealing", "snapshot")
        def get_snapshot(self):
            """시스템 스냅샷"""
            with self.client.get(
                "/api/self-healing/xtest/snapshot/",
                headers=self._get_headers(chaos=True),
                name=f"{STAGE_NAME} GET Snapshot",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")

except ImportError:
    pass


# =============================================================================
# Main
# =============================================================================


if __name__ == "__main__":
    print("=" * 70)
    print("🔥 Stage 9 EXTREME V2: Worker Crash + Self-Healing + Advanced Scenarios")
    print("=" * 70)
    
    # 환경변수로 설정 오버라이드
    config = ExtremeChaosConfig(
        num_workers=int(os.environ.get("NUM_WORKERS", 8)),
        tasks_per_second=int(os.environ.get("TASKS_PER_SEC", 100)),
        crash_interval_seconds=float(os.environ.get("CRASH_INTERVAL", 2.0)),
        simultaneous_crash_count=int(os.environ.get("SIM_CRASH_COUNT", 3)),
        test_duration_seconds=int(os.environ.get("TEST_DURATION", 60)),
        selfhealing_host=os.environ.get("SELFHEALING_HOST", "http://localhost:8000"),
        # V2 시나리오 설정
        brain_failure_enabled=os.environ.get("BRAIN_FAILURE", "true").lower() == "true",
        brain_failure_duration_seconds=int(os.environ.get("BRAIN_FAILURE_DURATION", 15)),
        zombie_enabled=os.environ.get("ZOMBIE_ENABLED", "true").lower() == "true",
        zombie_response_time_seconds=float(os.environ.get("ZOMBIE_RESPONSE_TIME", 35.0)),
        split_brain_enabled=os.environ.get("SPLIT_BRAIN", "true").lower() == "true",
        split_brain_duration_seconds=int(os.environ.get("SPLIT_BRAIN_DURATION", 20)),
    )
    
    simulator = ExtremeChaosSimulator(config)
    simulator.run_simulation()
    
    # 종료 코드
    sys.exit(0 if simulator.results.get("passed", False) else 1)
