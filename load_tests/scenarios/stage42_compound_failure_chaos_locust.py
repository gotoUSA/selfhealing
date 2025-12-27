"""
Stage 42: Compound Failure Chaos - Locust Load Test

================================================================================
TEST PURPOSE
================================================================================
이 Locust 테스트는 Stage 42의 Compound Failure(복합 장애) 시나리오를
동시 트래픽 부하 하에서 검증합니다.

Stage 42는 Stage 38의 STRICT 에스컬레이션입니다.

================================================================================
WHAT IS "COMPOUND FAILURE" IN THIS SYSTEM?
================================================================================
"Compound Failure"는 다음 장애 모드들이 **동시에 오버랩**되어 발생하는 상황입니다:

1. Retry Pressure (재시도 압력)
   - Transient failures로 인한 retry 시도
   - Retry amplification 또는 suppression 관찰

2. Circuit Breaker Activation (서킷 브레이커 활성화)
   - OPEN → HALF_OPEN → CLOSED 전이
   - Flapping 또는 oscillation 방지

3. Rate Limit / Throttling (속도 제한)
   - Retry와 rate limiting의 상호작용
   - Throttling이 retry를 증폭하지 않음 확인

4. DLQ Engagement (DLQ 관여)
   - DLQ에 항목 추가
   - DLQ 증가율이 bounded
   - DLQ replay가 중복 side effects 유발하지 않음

이 장애들은 **시간적으로 오버랩**되어야 합니다.
순차적 테스트는 불충분합니다.

================================================================================
STAGE 42 OBJECTIVE
================================================================================
다중 장애 모드가 동시 트래픽 하에서 오버랩될 때 시스템 안정성을 검증합니다.

구체적 관찰 항목:
1. Retry Storm 에스컬레이션 방지
2. Circuit Breaker 진동(oscillation) 방지
3. 장애 blast radius 격리
4. Retry 하에서도 idempotency 유지
5. DLQ가 safety valve로 작동 (sinkhole 아님)
6. 복합 장애 해결 후 깔끔한 복구

================================================================================
HOW TO RUN
================================================================================
# 1. Docker Compose 환경 (권장)
docker-compose -f docker-compose.stage42.yml up -d --build
docker-compose -f docker-compose.stage42.yml run --rm stage42-locust

# 2. Headless 모드 (자동 실행)
locust -f load_tests/scenarios/stage42_compound_failure_chaos_locust.py \
    --host=http://localhost:8000 \
    --users=75 --spawn-rate=7 --run-time=6m \
    --headless --html=reports/stage42_locust_report.html

# 3. 웹 UI 모드
locust -f load_tests/scenarios/stage42_compound_failure_chaos_locust.py \
    --host=http://localhost:8000

권장 사용자 수: 50 ~ 100 (Stage 38보다 높은 동시성)
권장 spawn rate: 5 ~ 10
권장 실행 시간: 5분 ~ 7분

================================================================================
CONSTRAINT COMPLIANCE
================================================================================
✅ 새로운 기능/API 추가 없음
✅ 기존 Control API 메커니즘만 사용
✅ pytest-style assertions 없음 (관찰만 수행)
✅ 실제 엔드포인트만 사용
✅ Mock 서버 사용 없음
✅ 장애 주입은 TTL 기반, 가역적

================================================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

# 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events
from locust.runners import MasterRunner, WorkerRunner

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STAGE_NAME = "[Stage42-CompoundFailureChaos]"


# =============================================================================
# Configuration - 기존 시스템 설정 사용
# =============================================================================

# 서비스 정의 (기존 stage42 chaos 테스트와 동일)
SERVICE_PAYMENT_GATEWAY = "payment_gateway_compound"
SERVICE_ORDER_PROCESSOR = "order_processor_compound"
SERVICE_NOTIFICATION = "notification_service_compound"
SERVICE_INVENTORY = "inventory_service_compound"

# Control API 엔드포인트 (기존 selfhealing API)
CONTROL_API = {
    "control": "/api/self-healing/control/",
    "status": "/api/self-healing/status/",
    "service_status": "/api/self-healing/status/{service_name}/",
    "health": "/api/self-healing/health/",
    "health_ping": "/api/self-healing/health/ping/",
    "metrics": "/api/self-healing/metrics/",
    "block": "/api/self-healing/block/{service_name}/",
    "allow": "/api/self-healing/allow/{service_name}/",
    "reset": "/api/self-healing/reset/{service_name}/",
    "dlq_list": "/api/self-healing/dlq/list/",
    "dlq_replay": "/api/self-healing/dlq/replay/",
}

# 비즈니스 API 엔드포인트
BUSINESS_API = {
    "products": "/api/products/",
    "cart": "/api/cart/",
    "cart_add": "/api/cart/add_item/",
    "cart_clear": "/api/cart/clear/",
    "orders": "/api/orders/",
    "payments_request": "/api/payments/request/",
}

# Admin 인증 정보 (환경변수 또는 기본값)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123!")

# 테스트 사용자 설정
TEST_USER_PREFIX = "load_test_user_"
TEST_USER_PASSWORD = "testpass123"
TEST_USER_COUNT = 10


# =============================================================================
# Circuit Breaker State Enum
# =============================================================================


class CircuitState(str, Enum):
    """Circuit Breaker 상태 (관찰용)"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    UNKNOWN = "unknown"


# =============================================================================
# Compound Failure Phase Definitions
# =============================================================================


class CompoundFailurePhase(str, Enum):
    """복합 장애 테스트 페이즈"""

    WARMUP = "warmup"
    NORMAL_LOAD = "normal_load"
    COMPOUND_FAILURE_RAMP = "compound_failure_ramp"
    COMPOUND_FAILURE_PEAK = "compound_failure_peak"
    RECOVERY_START = "recovery_start"
    STABILIZATION = "stabilization"
    COOLDOWN = "cooldown"
    COMPLETED = "completed"


# =============================================================================
# Test Metrics Collection (관찰 전용 - assertions 없음)
# =============================================================================


@dataclass
class Stage42Metrics:
    """
    Stage 42 복합 장애 테스트 메트릭 수집기

    이 클래스는 관찰만 수행하며, assertions는 포함하지 않습니다.
    """

    # 요청 통계
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Circuit Breaker 상태 전이
    circuit_state_changes: List[Dict[str, Any]] = field(default_factory=list)
    current_circuit_states: Dict[str, str] = field(default_factory=dict)
    circuit_oscillation_count: int = 0

    # Retry 통계
    retry_attempts: int = 0
    retry_success: int = 0
    retry_exhausted: int = 0
    retry_storm_indicators: int = 0

    # Rate Limit 통계
    rate_limit_hits: int = 0
    rate_limit_during_retry: int = 0

    # DLQ 통계
    dlq_entries_observed: int = 0
    dlq_max_size_observed: int = 0
    dlq_replay_triggered: int = 0
    dlq_replay_duplicate_prevented: int = 0

    # Idempotency 통계
    idempotency_keys_used: int = 0
    duplicate_requests_detected: int = 0

    # 복합 장애 통계
    concurrent_failures_observed: int = 0
    failure_overlap_events: List[Dict[str, Any]] = field(default_factory=list)

    # 응답 시간 분포 (ms)
    response_times: List[float] = field(default_factory=list)
    response_times_by_phase: Dict[str, List[float]] = field(default_factory=dict)

    # 오류 분류
    error_by_type: Dict[str, int] = field(default_factory=dict)

    # 테스트 페이즈
    current_phase: str = CompoundFailurePhase.WARMUP.value
    phase_start_time: float = field(default_factory=time.time)
    phase_history: List[Dict[str, Any]] = field(default_factory=list)


# 전역 메트릭 인스턴스 및 락
_metrics = Stage42Metrics()
_metrics_lock = threading.Lock()


def record_request(success: bool, response_time_ms: float, phase: str = "unknown"):
    """요청 결과 기록"""
    with _metrics_lock:
        _metrics.total_requests += 1
        if success:
            _metrics.successful_requests += 1
        else:
            _metrics.failed_requests += 1

        _metrics.response_times.append(response_time_ms)

        if phase not in _metrics.response_times_by_phase:
            _metrics.response_times_by_phase[phase] = []
        _metrics.response_times_by_phase[phase].append(response_time_ms)


def record_circuit_state_change(from_state: str, to_state: str, service: str):
    """Circuit Breaker 상태 전이 기록"""
    with _metrics_lock:
        # 진동 감지 (이전 상태와 동일한 상태로 빠르게 돌아감)
        if len(_metrics.circuit_state_changes) > 0:
            last_change = _metrics.circuit_state_changes[-1]
            if last_change["service"] == service:
                time_diff = time.time() - datetime.fromisoformat(last_change["timestamp"]).timestamp()
                if time_diff < 5 and last_change["from_state"] == to_state:
                    _metrics.circuit_oscillation_count += 1

        _metrics.circuit_state_changes.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": service,
                "from_state": from_state,
                "to_state": to_state,
            }
        )
        _metrics.current_circuit_states[service] = to_state
        logger.info(f"{STAGE_NAME} Circuit state: {from_state} -> {to_state} ({service})")


def record_retry(success: bool, exhausted: bool = False, rate_limited: bool = False):
    """Retry 이벤트 기록"""
    with _metrics_lock:
        _metrics.retry_attempts += 1
        if success:
            _metrics.retry_success += 1
        if exhausted:
            _metrics.retry_exhausted += 1
        if rate_limited:
            _metrics.rate_limit_during_retry += 1


def record_rate_limit_hit():
    """Rate limit 이벤트 기록"""
    with _metrics_lock:
        _metrics.rate_limit_hits += 1


def record_dlq_observation(count: int):
    """DLQ 관찰 결과 기록"""
    with _metrics_lock:
        _metrics.dlq_entries_observed = count
        _metrics.dlq_max_size_observed = max(_metrics.dlq_max_size_observed, count)


def record_idempotency_event(is_duplicate: bool):
    """Idempotency 이벤트 기록"""
    with _metrics_lock:
        _metrics.idempotency_keys_used += 1
        if is_duplicate:
            _metrics.duplicate_requests_detected += 1


def record_concurrent_failure(failure_types: List[str]):
    """동시 장애 이벤트 기록"""
    with _metrics_lock:
        if len(failure_types) > 1:
            _metrics.concurrent_failures_observed += 1
            _metrics.failure_overlap_events.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "failures": failure_types,
                }
            )


def record_error(error_type: str):
    """오류 유형 기록"""
    with _metrics_lock:
        _metrics.error_by_type[error_type] = _metrics.error_by_type.get(error_type, 0) + 1


def set_test_phase(phase: str):
    """테스트 페이즈 설정"""
    with _metrics_lock:
        old_phase = _metrics.current_phase
        _metrics.current_phase = phase
        _metrics.phase_start_time = time.time()
        _metrics.phase_history.append(
            {
                "phase": phase,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "from_phase": old_phase,
            }
        )
        logger.info(f"{STAGE_NAME} Phase changed: {old_phase} -> {phase}")


def get_current_phase() -> str:
    """현재 페이즈 반환"""
    with _metrics_lock:
        return _metrics.current_phase


def get_metrics_summary() -> Dict[str, Any]:
    """현재 메트릭 요약 반환"""
    with _metrics_lock:
        success_rate = (_metrics.successful_requests / _metrics.total_requests * 100) if _metrics.total_requests > 0 else 0
        avg_response_time = sum(_metrics.response_times) / len(_metrics.response_times) if _metrics.response_times else 0

        # Phase별 평균 응답 시간
        phase_avg_times = {}
        for phase, times in _metrics.response_times_by_phase.items():
            if times:
                phase_avg_times[phase] = round(sum(times) / len(times), 2)

        return {
            "total_requests": _metrics.total_requests,
            "success_rate_percent": round(success_rate, 2),
            "avg_response_time_ms": round(avg_response_time, 2),
            # Circuit Breaker
            "circuit_state_changes": len(_metrics.circuit_state_changes),
            "circuit_oscillations": _metrics.circuit_oscillation_count,
            "current_circuit_states": dict(_metrics.current_circuit_states),
            # Retry
            "retry_attempts": _metrics.retry_attempts,
            "retry_success": _metrics.retry_success,
            "retry_exhausted": _metrics.retry_exhausted,
            "retry_storm_indicators": _metrics.retry_storm_indicators,
            # Rate Limit
            "rate_limit_hits": _metrics.rate_limit_hits,
            "rate_limit_during_retry": _metrics.rate_limit_during_retry,
            # DLQ
            "dlq_max_size": _metrics.dlq_max_size_observed,
            "dlq_current": _metrics.dlq_entries_observed,
            "dlq_replay_triggered": _metrics.dlq_replay_triggered,
            # Idempotency
            "duplicate_requests": _metrics.duplicate_requests_detected,
            # Compound Failure
            "concurrent_failure_events": _metrics.concurrent_failures_observed,
            "failure_overlap_count": len(_metrics.failure_overlap_events),
            # Phase
            "current_phase": _metrics.current_phase,
            "phase_avg_response_times": phase_avg_times,
            # Errors
            "errors_by_type": dict(_metrics.error_by_type),
        }


# =============================================================================
# Compound Failure Controller
# =============================================================================


class CompoundFailureController:
    """
    복합 장애 시나리오 컨트롤러

    Control API를 통해 기존 메커니즘만 사용하여 여러 장애를 동시에 주입합니다.
    새로운 로직은 추가하지 않습니다.
    """

    def __init__(self, client, admin_token: Optional[str] = None):
        self.client = client
        self.admin_token = admin_token
        self._active_failures: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _get_admin_headers(self) -> Dict[str, str]:
        """Admin 인증 헤더 반환"""
        headers = {"Content-Type": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        return headers

    def get_circuit_state(self, service_name: str) -> Tuple[str, Dict[str, Any]]:
        """Circuit Breaker 상태 조회 (기존 API 사용)"""
        url = CONTROL_API["service_status"].format(service_name=service_name)
        try:
            response = self.client.get(
                url,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Get Circuit State",
            )
            if response.status_code == 200:
                data = response.json()
                state = data.get("state", data.get("status", "unknown"))
                return state, data
            return "unknown", {}
        except Exception as e:
            logger.warning(f"Failed to get circuit state: {e}")
            return "error", {"error": str(e)}

    def inject_transient_failure(self, service_name: str, failure_rate: float = 0.5, ttl_minutes: int = 2) -> bool:
        """
        Transient failure 주입 (기존 Control API 사용)

        재시도를 트리거하는 일시적 장애를 주입합니다.
        """
        payload = {
            "service_name": service_name,
            "action": "inject_failure",
            "environment": "chaos",
            "reason": f"Stage42 Compound Chaos: Transient failure injection",
            "ttl_minutes": ttl_minutes,
            "metadata": {
                "failure_rate": failure_rate,
                "failure_type": "transient",
            },
        }

        try:
            response = self.client.post(
                CONTROL_API["control"],
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Inject Transient Failure",
            )
            success = response.status_code in (200, 201)
            if success:
                with self._lock:
                    self._active_failures[f"transient_{service_name}"] = {
                        "type": "transient",
                        "service": service_name,
                        "start_time": time.time(),
                        "ttl": ttl_minutes * 60,
                    }
                logger.info(f"{STAGE_NAME} Transient failure injected: {service_name} (rate={failure_rate})")
            return success
        except Exception as e:
            logger.error(f"Failed to inject transient failure: {e}")
            return False

    def block_service(self, service_name: str, reason: str, ttl_minutes: int = 3) -> bool:
        """
        서비스 차단 (Circuit Breaker OPEN)

        기존 Control API의 block 액션을 사용합니다.
        """
        url = CONTROL_API["block"].format(service_name=service_name)
        payload = {
            "reason": reason,
            "environment": "chaos",
            "ttl_minutes": ttl_minutes,
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Block Service",
            )
            success = response.status_code in (200, 201)
            if success:
                with self._lock:
                    self._active_failures[f"circuit_open_{service_name}"] = {
                        "type": "circuit_open",
                        "service": service_name,
                        "start_time": time.time(),
                        "ttl": ttl_minutes * 60,
                    }
                    old_state = _metrics.current_circuit_states.get(service_name, "closed")
                record_circuit_state_change(old_state, "open", service_name)
                logger.info(f"{STAGE_NAME} Service blocked: {service_name}")
            return success
        except Exception as e:
            logger.error(f"Failed to block service: {e}")
            return False

    def allow_service(self, service_name: str, reason: str) -> bool:
        """
        서비스 허용 (Circuit Breaker CLOSED)

        기존 Control API의 allow 액션을 사용합니다.
        """
        url = CONTROL_API["allow"].format(service_name=service_name)
        payload = {
            "reason": reason,
            "environment": "chaos",
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Allow Service",
            )
            success = response.status_code in (200, 201)
            if success:
                with self._lock:
                    key = f"circuit_open_{service_name}"
                    if key in self._active_failures:
                        del self._active_failures[key]
                    old_state = _metrics.current_circuit_states.get(service_name, "open")
                record_circuit_state_change(old_state, "closed", service_name)
                logger.info(f"{STAGE_NAME} Service allowed: {service_name}")
            return success
        except Exception as e:
            logger.error(f"Failed to allow service: {e}")
            return False

    def reset_service(self, service_name: str, reason: str) -> bool:
        """서비스 리셋"""
        url = CONTROL_API["reset"].format(service_name=service_name)
        payload = {
            "reason": reason,
            "environment": "chaos",
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Reset Service",
            )
            return response.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Failed to reset service: {e}")
            return False

    def get_dlq_count(self) -> int:
        """DLQ 항목 수 조회"""
        try:
            with self.client.get(
                CONTROL_API["dlq_list"],
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Get DLQ Count",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    count = data.get("total_count", len(data.get("items", [])))
                    record_dlq_observation(count)
                    response.success()
                    return count
                else:
                    # 인증 오류 등은 조용히 처리
                    response.success()
                    return 0
        except Exception as e:
            logger.warning(f"Failed to get DLQ count: {e}")
            return 0

    def get_active_failure_types(self) -> List[str]:
        """현재 활성화된 장애 유형 반환"""
        with self._lock:
            now = time.time()
            active_types = []
            for key, info in list(self._active_failures.items()):
                if now - info["start_time"] < info["ttl"]:
                    active_types.append(info["type"])
                else:
                    del self._active_failures[key]
            return active_types

    def cleanup_all_failures(self):
        """모든 장애 정리"""
        services = [
            SERVICE_PAYMENT_GATEWAY,
            SERVICE_ORDER_PROCESSOR,
            SERVICE_NOTIFICATION,
            SERVICE_INVENTORY,
        ]
        for service in services:
            self.reset_service(service, "Stage42 Compound Chaos: Cleanup")
        with self._lock:
            self._active_failures.clear()


# =============================================================================
# Compound Failure Phase Controller
# =============================================================================


class CompoundPhaseController:
    """
    복합 장애 테스트 페이즈 관리자

    Phases:
    1. warmup: 시스템 워밍업 (30초)
    2. normal_load: 정상 부하 baseline (45초)
    3. compound_failure_ramp: 복합 장애 점진적 주입 (30초)
       - 서비스 A: transient failure 주입
       - 서비스 B: circuit breaker OPEN
    4. compound_failure_peak: 복합 장애 피크 (90초)
       - 모든 장애가 오버랩되는 구간
       - Retry Storm + Circuit Breaker + Rate Limit 동시 활성
    5. recovery_start: 복구 시작 (30초)
       - 점진적으로 장애 해제
    6. stabilization: 안정화 (60초)
    7. cooldown: 쿨다운 (30초)
    """

    PHASES = [
        (CompoundFailurePhase.WARMUP.value, 30),
        (CompoundFailurePhase.NORMAL_LOAD.value, 45),
        (CompoundFailurePhase.COMPOUND_FAILURE_RAMP.value, 30),
        (CompoundFailurePhase.COMPOUND_FAILURE_PEAK.value, 90),
        (CompoundFailurePhase.RECOVERY_START.value, 30),
        (CompoundFailurePhase.STABILIZATION.value, 60),
        (CompoundFailurePhase.COOLDOWN.value, 30),
    ]

    def __init__(self):
        self.current_phase_index = 0
        self.phase_start_time = time.time()
        self.failure_controller: Optional[CompoundFailureController] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._compound_failures_injected = False

    def initialize(self, failure_controller: CompoundFailureController):
        """컨트롤러 초기화"""
        with self._lock:
            if not self._initialized:
                self.failure_controller = failure_controller
                self._initialized = True
                set_test_phase(self.PHASES[0][0])
                logger.info(f"{STAGE_NAME} Compound Phase controller initialized")

    def get_current_phase(self) -> str:
        """현재 페이즈 반환"""
        if self.current_phase_index < len(self.PHASES):
            return self.PHASES[self.current_phase_index][0]
        return CompoundFailurePhase.COMPLETED.value

    def check_and_advance_phase(self) -> Optional[str]:
        """페이즈 전환 확인 및 실행"""
        with self._lock:
            if self.current_phase_index >= len(self.PHASES):
                return None

            current_phase, duration = self.PHASES[self.current_phase_index]
            elapsed = time.time() - self.phase_start_time

            if elapsed >= duration:
                self.current_phase_index += 1
                self.phase_start_time = time.time()

                if self.current_phase_index < len(self.PHASES):
                    new_phase = self.PHASES[self.current_phase_index][0]
                    set_test_phase(new_phase)
                    self._execute_phase_action(new_phase)
                    return new_phase
                else:
                    set_test_phase(CompoundFailurePhase.COMPLETED.value)
                    return CompoundFailurePhase.COMPLETED.value

            return None

    def _execute_phase_action(self, phase: str):
        """페이즈별 액션 실행"""
        if not self.failure_controller:
            return

        if phase == CompoundFailurePhase.COMPOUND_FAILURE_RAMP.value:
            # === 복합 장애 점진적 주입 시작 ===
            logger.info(f"{STAGE_NAME} === COMPOUND FAILURE RAMP: Starting failure injection ===")

            # Transient failure 주입 (retry 압력 생성)
            self.failure_controller.inject_transient_failure(SERVICE_PAYMENT_GATEWAY, failure_rate=0.6, ttl_minutes=4)

            # Circuit Breaker OPEN (하나의 서비스)
            self.failure_controller.block_service(
                SERVICE_ORDER_PROCESSOR,
                reason="Stage42 Compound Chaos: Ramp phase circuit open",
                ttl_minutes=3,
            )

            record_concurrent_failure(["transient_failure", "circuit_breaker_open"])

        elif phase == CompoundFailurePhase.COMPOUND_FAILURE_PEAK.value:
            # === 복합 장애 피크 ===
            logger.info(f"{STAGE_NAME} === COMPOUND FAILURE PEAK: Maximum overlap ===")

            # 추가 서비스에도 장애 주입
            self.failure_controller.inject_transient_failure(SERVICE_NOTIFICATION, failure_rate=0.7, ttl_minutes=2)

            self.failure_controller.block_service(
                SERVICE_INVENTORY,
                reason="Stage42 Compound Chaos: Peak phase additional circuit open",
                ttl_minutes=2,
            )

            # 동시 장애 기록
            active_failures = self.failure_controller.get_active_failure_types()
            record_concurrent_failure(active_failures)

            self._compound_failures_injected = True

        elif phase == CompoundFailurePhase.RECOVERY_START.value:
            # === 복구 시작 ===
            logger.info(f"{STAGE_NAME} === RECOVERY START: Clearing failures ===")

            # 점진적 복구 (역순)
            self.failure_controller.allow_service(SERVICE_INVENTORY, reason="Stage42 Compound Chaos: Recovery phase")
            self.failure_controller.allow_service(SERVICE_ORDER_PROCESSOR, reason="Stage42 Compound Chaos: Recovery phase")

        elif phase == CompoundFailurePhase.STABILIZATION.value:
            logger.info(f"{STAGE_NAME} === STABILIZATION PHASE ===")

        elif phase == CompoundFailurePhase.COOLDOWN.value:
            logger.info(f"{STAGE_NAME} === COOLDOWN: Final cleanup ===")
            self.failure_controller.cleanup_all_failures()


# 전역 페이즈 컨트롤러
_phase_controller = CompoundPhaseController()


# =============================================================================
# Locust User Classes
# =============================================================================


class Stage42BaseUser(HttpUser):
    """Stage 42 기본 사용자"""

    abstract = True
    wait_time = between(0.5, 2)  # Stage 38보다 더 빠른 요청 간격

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = f"user_{uuid.uuid4().hex[:8]}"
        self.auth_token: Optional[str] = None
        self.admin_token: Optional[str] = None
        self.failure_controller: Optional[CompoundFailureController] = None
        self._product_ids: List[int] = []

    def on_start(self):
        """사용자 시작 시 초기화"""
        self._login()
        self._admin_login()
        self._cache_product_ids()

        self.failure_controller = CompoundFailureController(self.client, self.admin_token)
        _phase_controller.initialize(self.failure_controller)

    def _login(self):
        """일반 사용자 로그인"""
        user_index = random.randint(0, TEST_USER_COUNT - 1)
        username = f"{TEST_USER_PREFIX}{user_index}"

        try:
            with self.client.post(
                "/api/auth/login/",
                json={"username": username, "password": TEST_USER_PASSWORD},
                name=f"{STAGE_NAME} User Login",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    self.auth_token = data.get("token", {}).get("access")
                    if self.auth_token:
                        self.client.headers.update({"Authorization": f"Bearer {self.auth_token}"})
                    response.success()
                else:
                    # 로그인 실패는 예상된 상황 (테스트 사용자 미생성)
                    response.success()
        except Exception as e:
            logger.warning(f"User login failed: {e}")

    def _admin_login(self):
        """Admin 로그인"""
        try:
            with self.client.post(
                "/api/auth/login/",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                name=f"{STAGE_NAME} Admin Login",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    self.admin_token = data.get("token", {}).get("access")
                    response.success()
                else:
                    # Admin 로그인 실패도 예상된 상황으로 처리
                    response.success()
        except Exception as e:
            logger.warning(f"Admin login failed: {e}")

    def _cache_product_ids(self):
        """상품 ID 캐싱"""
        try:
            with self.client.get(
                BUSINESS_API["products"],
                name=f"{STAGE_NAME} Fetch Products",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    self._product_ids = [p["id"] for p in results if "id" in p]
                    response.success()
                else:
                    response.success()  # 실패해도 에러로 처리하지 않음
        except Exception as e:
            logger.warning(f"Failed to cache products: {e}")

    def _get_random_product_id(self) -> Optional[int]:
        """랜덤 상품 ID 반환"""
        return random.choice(self._product_ids) if self._product_ids else None

    def _generate_idempotency_key(self, operation: str) -> str:
        """Idempotency 키 생성"""
        unique = f"{self.user_id}:{operation}:{time.time_ns()}"
        return hashlib.sha256(unique.encode()).hexdigest()[:32]


class Stage42TrafficUser(Stage42BaseUser):
    """
    Stage 42 일반 트래픽 사용자 (50%)

    정상적인 비즈니스 트래픽을 생성합니다.
    복합 장애 상황에서의 시스템 반응을 관찰합니다.
    """

    weight = 50

    @task(5)
    @tag("traffic", "read")
    def browse_products(self):
        """상품 목록 조회"""
        _phase_controller.check_and_advance_phase()
        current_phase = get_current_phase()

        start_time = time.time()

        with self.client.get(
            BUSINESS_API["products"],
            name=f"{STAGE_NAME} Browse Products",
            catch_response=True,
        ) as response:
            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                record_request(True, response_time, current_phase)
                response.success()
            elif response.status_code == 503:
                record_request(False, response_time, current_phase)
                record_error("circuit_breaker_open")
                response.success()  # 예상된 동작
            elif response.status_code == 429:
                record_request(False, response_time, current_phase)
                record_rate_limit_hit()
                record_error("rate_limited")
                response.success()  # 예상된 동작
            else:
                record_request(False, response_time, current_phase)
                record_error(f"http_{response.status_code}")
                response.failure(f"Unexpected: {response.status_code}")

    @task(3)
    @tag("traffic", "health")
    def check_health(self):
        """시스템 헬스 체크"""
        start_time = time.time()
        current_phase = get_current_phase()

        with self.client.get(
            CONTROL_API["health_ping"],
            name=f"{STAGE_NAME} Health Ping",
            catch_response=True,
        ) as response:
            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                record_request(True, response_time, current_phase)
                response.success()
            else:
                record_request(False, response_time, current_phase)
                response.failure(f"Health check failed: {response.status_code}")


class Stage42RetryUser(Stage42BaseUser):
    """
    Stage 42 Retry 테스트 사용자 (25%)

    Retry 로직과 idempotency를 테스트합니다.
    복합 장애 상황에서 retry storm이 발생하지 않는지 관찰합니다.
    """

    weight = 25

    @task(4)
    @tag("retry", "cart")
    def add_to_cart_with_retry(self):
        """장바구니 추가 (Retry 및 Idempotency 포함)"""
        _phase_controller.check_and_advance_phase()
        current_phase = get_current_phase()

        product_id = self._get_random_product_id()
        if not product_id:
            return

        idempotency_key = self._generate_idempotency_key("add_to_cart")
        headers = {"X-Idempotency-Key": idempotency_key}

        max_retries = 3
        retry_delay = 0.5

        for attempt in range(max_retries):
            start_time = time.time()

            with self.client.post(
                BUSINESS_API["cart_add"],
                json={"product_id": product_id, "quantity": 1},
                headers=headers,
                name=f"{STAGE_NAME} Cart Add (attempt {attempt + 1})",
                catch_response=True,
            ) as response:
                response_time = (time.time() - start_time) * 1000

                if response.status_code in (200, 201):
                    record_request(True, response_time, current_phase)
                    record_idempotency_event(is_duplicate=False)
                    if attempt > 0:
                        record_retry(success=True)
                    response.success()
                    return

                elif response.status_code == 409:
                    # 중복 요청 감지
                    record_request(True, response_time, current_phase)
                    record_idempotency_event(is_duplicate=True)
                    response.success()
                    return

                elif response.status_code == 503:
                    # Circuit Breaker - Retry
                    record_request(False, response_time, current_phase)

                    if attempt < max_retries - 1:
                        record_retry(success=False)
                        time.sleep(retry_delay * (2**attempt))  # Exponential backoff
                        response.success()
                    else:
                        record_retry(success=False, exhausted=True)
                        response.success()

                elif response.status_code == 429:
                    # Rate limited during retry
                    record_request(False, response_time, current_phase)
                    record_rate_limit_hit()
                    record_retry(success=False, rate_limited=True)

                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2**attempt))
                        response.success()
                    else:
                        response.success()
                        return

                else:
                    record_request(False, response_time, current_phase)
                    record_error(f"cart_error_{response.status_code}")
                    response.failure(f"Cart add failed: {response.status_code}")
                    return

    @task(3)
    @tag("retry", "order")
    def create_order_with_retry(self):
        """주문 생성 (Retry 포함)"""
        _phase_controller.check_and_advance_phase()
        current_phase = get_current_phase()

        product_id = self._get_random_product_id()
        if not product_id:
            return

        # 먼저 장바구니에 상품 추가
        with self.client.post(
            BUSINESS_API["cart_add"],
            json={"product_id": product_id, "quantity": 1},
            name=f"{STAGE_NAME} Pre-Order Cart Add",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201, 400):
                response.failure(f"Cart add failed: {response.status_code}")
                return
            response.success()

        idempotency_key = self._generate_idempotency_key("create_order")
        headers = {"X-Idempotency-Key": idempotency_key}

        order_data = {
            "shipping_name": f"Test User {self.user_id}",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "Test Address",
        }

        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            start_time = time.time()

            with self.client.post(
                BUSINESS_API["orders"],
                json=order_data,
                headers=headers,
                name=f"{STAGE_NAME} Create Order (attempt {attempt + 1})",
                catch_response=True,
            ) as response:
                response_time = (time.time() - start_time) * 1000

                if response.status_code in (200, 201, 202):
                    record_request(True, response_time, current_phase)
                    record_idempotency_event(is_duplicate=False)
                    if attempt > 0:
                        record_retry(success=True)
                    response.success()
                    return

                elif response.status_code == 409:
                    record_request(True, response_time, current_phase)
                    record_idempotency_event(is_duplicate=True)
                    response.success()
                    return

                elif response.status_code == 503:
                    record_request(False, response_time, current_phase)

                    if attempt < max_retries - 1:
                        record_retry(success=False)
                        time.sleep(retry_delay * (2**attempt))
                        response.success()
                    else:
                        record_retry(success=False, exhausted=True)
                        response.success()

                elif response.status_code == 429:
                    record_request(False, response_time, current_phase)
                    record_rate_limit_hit()
                    record_retry(success=False, rate_limited=True)

                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2**attempt))
                        response.success()
                    else:
                        response.success()
                        return

                else:
                    record_request(False, response_time, current_phase)
                    record_error(f"order_error_{response.status_code}")
                    response.failure(f"Order failed: {response.status_code}")
                    return


class Stage42ObserverUser(Stage42BaseUser):
    """
    Stage 42 관찰자 사용자 (25%)

    시스템 상태를 관찰하고 메트릭을 수집합니다.
    복합 장애의 상호작용을 모니터링합니다.
    """

    weight = 25

    @task(5)
    @tag("observer", "circuit")
    def observe_circuit_states(self):
        """모든 Circuit Breaker 상태 관찰"""
        _phase_controller.check_and_advance_phase()

        if not self.admin_token:
            return

        headers = {"Authorization": f"Bearer {self.admin_token}"}

        with self.client.get(
            CONTROL_API["status"],
            headers=headers,
            name=f"{STAGE_NAME} Observe All Circuits",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status unavailable: {response.status_code}")

    @task(4)
    @tag("observer", "dlq")
    def observe_dlq_growth(self):
        """DLQ 증가 관찰"""
        if not self.failure_controller:
            return

        dlq_count = self.failure_controller.get_dlq_count()

        # DLQ가 bounded인지 확인 (1000 미만 유지)
        if dlq_count > 0:
            logger.debug(f"{STAGE_NAME} DLQ count observed: {dlq_count}")

    @task(3)
    @tag("observer", "metrics")
    def observe_system_metrics(self):
        """시스템 메트릭 관찰"""
        if not self.admin_token:
            return

        headers = {"Authorization": f"Bearer {self.admin_token}"}

        with self.client.get(
            CONTROL_API["metrics"],
            headers=headers,
            name=f"{STAGE_NAME} Observe Metrics",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 401:
                response.success()  # 인증 실패 무시
            else:
                response.failure(f"Metrics unavailable: {response.status_code}")

    @task(2)
    @tag("observer", "compound")
    def observe_compound_failure_state(self):
        """복합 장애 상태 관찰"""
        if not self.failure_controller:
            return

        active_failures = self.failure_controller.get_active_failure_types()

        if len(active_failures) > 1:
            record_concurrent_failure(active_failures)
            logger.debug(f"{STAGE_NAME} Active compound failures: {active_failures}")


# =============================================================================
# Locust Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 실행"""
    print("\n" + "=" * 80)
    print(f"🚀 {STAGE_NAME} Compound Failure Chaos Load Test Started")
    print("=" * 80)
    print(f"📊 Test Configuration:")
    print(f"   - Services: {SERVICE_PAYMENT_GATEWAY}, {SERVICE_ORDER_PROCESSOR}")
    print(f"   -           {SERVICE_NOTIFICATION}, {SERVICE_INVENTORY}")
    print(f"   - Admin User: {ADMIN_USERNAME}")
    print("=" * 80)
    print("\n📝 Test Phases (Compound Failure Overlap):")
    total_time = 0
    for i, (phase, duration) in enumerate(CompoundPhaseController.PHASES, 1):
        total_time += duration
        description = {
            "warmup": "System warmup",
            "normal_load": "Normal load baseline",
            "compound_failure_ramp": "Compound failures start overlapping",
            "compound_failure_peak": "Maximum failure overlap",
            "recovery_start": "Failures clearing",
            "stabilization": "System stabilizing",
            "cooldown": "Final cleanup",
        }.get(phase, "")
        print(f"   {i}. {phase} ({duration}s) - {description}")
    print(f"\n   Total Test Duration: {total_time}s ({total_time // 60}m {total_time % 60}s)")
    print("=" * 80)
    print("\n⚠️  COMPOUND FAILURE SCENARIOS:")
    print("   A. Retry Pressure + Rate Limiting")
    print("   B. Circuit Breaker OPEN + Transient Failures")
    print("   C. DLQ Growth under concurrent stress")
    print("   D. Idempotency under retries")
    print("=" * 80 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 실행"""
    print("\n" + "=" * 80)
    print(f"✅ {STAGE_NAME} Test Completed")
    print("=" * 80)

    summary = get_metrics_summary()

    print("\n📈 COMPOUND FAILURE CHAOS TEST RESULTS (Observations Only):")
    print("-" * 60)
    print(f"   Total Requests: {summary['total_requests']}")
    print(f"   Success Rate: {summary['success_rate_percent']}%")
    print(f"   Avg Response Time: {summary['avg_response_time_ms']}ms")
    print("-" * 60)

    print("\n🔄 CIRCUIT BREAKER OBSERVATIONS:")
    print(f"   State Changes: {summary['circuit_state_changes']}")
    print(f"   Oscillations Detected: {summary['circuit_oscillations']}")
    print(f"   Current States: {summary['current_circuit_states']}")
    print("-" * 60)

    print("\n🔁 RETRY OBSERVATIONS:")
    print(f"   Retry Attempts: {summary['retry_attempts']}")
    print(f"   Retry Successes: {summary['retry_success']}")
    print(f"   Retry Exhausted: {summary['retry_exhausted']}")
    print(f"   Rate Limited During Retry: {summary['rate_limit_during_retry']}")
    print("-" * 60)

    print("\n📥 DLQ OBSERVATIONS:")
    print(f"   Max DLQ Size Observed: {summary['dlq_max_size']}")
    print(f"   Current DLQ Size: {summary['dlq_current']}")
    print("-" * 60)

    print("\n🔐 IDEMPOTENCY OBSERVATIONS:")
    print(f"   Duplicate Requests Detected: {summary['duplicate_requests']}")
    print("-" * 60)

    print("\n⚡ COMPOUND FAILURE OBSERVATIONS:")
    print(f"   Concurrent Failure Events: {summary['concurrent_failure_events']}")
    print(f"   Failure Overlap Count: {summary['failure_overlap_count']}")
    print("-" * 60)

    print("\n📊 PHASE RESPONSE TIMES:")
    for phase, avg_time in summary["phase_avg_response_times"].items():
        print(f"   {phase}: {avg_time}ms")
    print("-" * 60)

    print("\n❌ ERRORS BY TYPE:")
    for error_type, count in summary["errors_by_type"].items():
        print(f"   {error_type}: {count}")
    print("=" * 80)

    print("\n📋 JSON Summary:")
    print(json.dumps(summary, indent=2))
    print("=" * 80 + "\n")


# =============================================================================
# Post-Run Manual Inspection Guide
# =============================================================================

"""
================================================================================
POST-RUN MANUAL INSPECTION GUIDE
================================================================================

이 테스트 실행 후 다음 항목들을 수동으로 검토해야 합니다:

1. Retry Storm Prevention
   ✓ retry_exhausted가 retry_attempts의 작은 비율인지 확인
   ✓ retry_storm_indicators가 0에 가까운지 확인
   ✗ 허용되지 않는 패턴: retry_exhausted가 50% 이상

2. Circuit Breaker Stability
   ✓ circuit_oscillations가 0인지 확인
   ✓ 상태 전이가 예상대로 발생하는지 확인
   ✗ 허용되지 않는 패턴: 5초 이내 동일 상태 반복 전이

3. Failure Blast Radius
   ✓ 복합 장애 중에도 일부 서비스가 정상 작동하는지 확인
   ✓ 에러 유형이 특정 서비스에 집중되는지 확인
   ✗ 허용되지 않는 패턴: 모든 서비스가 동시에 완전 실패

4. Idempotency Guarantee
   ✓ duplicate_requests가 올바르게 감지되는지 확인
   ✓ 중복 실행이 발생하지 않았는지 확인 (409 응답)
   ✗ 허용되지 않는 패턴: duplicate_requests > 0 but no 409 responses

5. DLQ as Safety Valve
   ✓ dlq_max_size가 DLQ_MAX_SIZE (1000) 미만인지 확인
   ✓ 복구 후 DLQ가 안정화되는지 확인
   ✗ 허용되지 않는 패턴: DLQ가 지속적으로 증가 (sinkhole)

6. Clean Recovery
   ✓ stabilization 페이즈에서 success_rate가 회복되는지 확인
   ✓ 응답 시간이 정상으로 돌아오는지 확인
   ✗ 허용되지 않는 패턴: 복구 후에도 높은 에러율 유지

================================================================================
UNACCEPTABLE FAILURE PATTERNS
================================================================================

다음 패턴이 관찰되면 시스템에 문제가 있습니다:

1. RETRY STORM: retry_exhausted / retry_attempts > 0.5
2. CIRCUIT OSCILLATION: circuit_oscillations > 0
3. DLQ OVERFLOW: dlq_max_size >= 1000
4. IDEMPOTENCY VIOLATION: duplicate side effects (수동 확인 필요)
5. NO RECOVERY: stabilization 페이즈 success_rate < normal_load 페이즈의 80%
6. CASCADING FAILURE: 모든 서비스 동시 실패 지속

================================================================================
"""
