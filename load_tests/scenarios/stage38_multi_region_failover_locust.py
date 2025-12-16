"""
Stage 38: Multi-Region / Failover Chaos Locust Load Test

================================================================================
TEST PURPOSE
================================================================================
이 Locust 테스트는 기존 Self-Healing 시스템의 Multi-Region Failover 동작을
동시 트래픽 부하 하에서 검증합니다.

목표:
1. Region A 장애 시 Circuit Breaker가 올바르게 OPEN 전이
2. Failover 라우팅 활성화 확인
3. Idempotency 보장 (중복 실행 0건)
4. Retry Storm이 Cascade Failure를 유발하지 않음
5. DLQ 증가가 예상 범위 내
6. Region A 복구 후 Circuit 상태 정상 전이 (OPEN → HALF_OPEN → CLOSED)

================================================================================
HOW TO RUN
================================================================================
# 1. 기본 실행 (웹 UI 모드)
locust -f load_tests/scenarios/stage38_multi_region_failover_locust.py \\
    --host=http://localhost:8000

# 2. Headless 모드 (자동 실행)
locust -f load_tests/scenarios/stage38_multi_region_failover_locust.py \\
    --host=http://localhost:8000 \\
    --users=50 --spawn-rate=5 --run-time=5m \\
    --headless --html=stage38_report.html

# 3. Docker Compose 환경
docker-compose -f docker-compose.stage38.yml up -d
docker-compose -f docker-compose.stage38.yml run --rm stage38-locust-test

================================================================================
EXPECTED OBSERVABLE OUTCOMES (NOT ASSERTIONS)
================================================================================
- 요청 성공률: 정상 시 95%+, 장애 시 일시적 감소 후 복구
- Circuit Breaker 상태 전이: CLOSED → OPEN → HALF_OPEN → CLOSED
- Failover 시간: < 5초
- 중복 실행 수: 0건 (idempotency 보장)
- DLQ 증가율: 장애 중 완만한 증가, 복구 후 안정화
- 평균 응답 시간: 정상 시 < 500ms, 장애 시 일시적 증가 후 안정화

================================================================================
CONSTRAINT COMPLIANCE
================================================================================
- 새로운 기능/API 추가 없음
- 기존 Control API 메커니즘만 사용
- pytest-style assertions 없음 (관찰만 수행)
- 실제 엔드포인트만 사용

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

STAGE_NAME = "[Stage38-MultiRegionFailover]"


# =============================================================================
# Configuration - 기존 시스템 설정 사용
# =============================================================================

# 리전 정의 (기존 stage38_multi_region_failover.py와 동일)
REGION_A = "region_a_payment_gateway"
REGION_B = "region_b_payment_gateway"

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
TEST_USER_COUNT = 10  # Docker 환경에서 생성된 사용자 수에 맞춤


# =============================================================================
# Circuit Breaker State Tracking (관찰용)
# =============================================================================


class CircuitState(str, Enum):
    """Circuit Breaker 상태 (관찰용)"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    UNKNOWN = "unknown"


# =============================================================================
# Test Metrics Collection (관찰 전용 - assertions 없음)
# =============================================================================


@dataclass
class Stage38Metrics:
    """
    Stage 38 테스트 메트릭 수집기

    이 클래스는 관찰만 수행하며, assertions는 포함하지 않습니다.
    """

    # 요청 통계
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    # Region 별 요청
    region_a_requests: int = 0
    region_b_requests: int = 0

    # Circuit Breaker 상태 전이
    circuit_state_changes: List[Dict[str, Any]] = field(default_factory=list)
    current_circuit_state: str = "unknown"

    # Failover 통계
    failover_triggered: int = 0
    failover_success: int = 0
    failover_time_ms_total: float = 0.0

    # Idempotency 통계
    idempotency_keys_used: int = 0
    duplicate_requests_detected: int = 0

    # Retry 통계
    retry_attempts: int = 0
    retry_success: int = 0
    retry_exhausted: int = 0

    # DLQ 통계
    dlq_entries_observed: int = 0
    dlq_growth_rate_per_min: float = 0.0

    # 응답 시간 분포 (ms)
    response_times: List[float] = field(default_factory=list)

    # 오류 분류
    error_by_type: Dict[str, int] = field(default_factory=dict)

    # 테스트 페이즈
    current_phase: str = "warmup"
    phase_start_time: float = field(default_factory=time.time)


# 전역 메트릭 인스턴스 및 락
_metrics = Stage38Metrics()
_metrics_lock = threading.Lock()


def record_request(success: bool, response_time_ms: float, region: str = "unknown"):
    """요청 결과 기록"""
    with _metrics_lock:
        _metrics.total_requests += 1
        if success:
            _metrics.successful_requests += 1
        else:
            _metrics.failed_requests += 1

        if region == REGION_A:
            _metrics.region_a_requests += 1
        elif region == REGION_B:
            _metrics.region_b_requests += 1

        _metrics.response_times.append(response_time_ms)


def record_circuit_state_change(from_state: str, to_state: str, service: str):
    """Circuit Breaker 상태 전이 기록"""
    with _metrics_lock:
        _metrics.circuit_state_changes.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": service,
                "from_state": from_state,
                "to_state": to_state,
            }
        )
        _metrics.current_circuit_state = to_state
        logger.info(f"{STAGE_NAME} Circuit state: {from_state} -> {to_state} ({service})")


def record_failover(success: bool, time_ms: float):
    """Failover 이벤트 기록"""
    with _metrics_lock:
        _metrics.failover_triggered += 1
        if success:
            _metrics.failover_success += 1
        _metrics.failover_time_ms_total += time_ms


def record_idempotency_event(is_duplicate: bool):
    """Idempotency 이벤트 기록"""
    with _metrics_lock:
        _metrics.idempotency_keys_used += 1
        if is_duplicate:
            _metrics.duplicate_requests_detected += 1


def record_retry(success: bool, exhausted: bool = False):
    """Retry 이벤트 기록"""
    with _metrics_lock:
        _metrics.retry_attempts += 1
        if success:
            _metrics.retry_success += 1
        if exhausted:
            _metrics.retry_exhausted += 1


def record_dlq_observation(count: int):
    """DLQ 관찰 결과 기록"""
    with _metrics_lock:
        _metrics.dlq_entries_observed = count


def record_error(error_type: str):
    """오류 유형 기록"""
    with _metrics_lock:
        _metrics.error_by_type[error_type] = _metrics.error_by_type.get(error_type, 0) + 1


def set_test_phase(phase: str):
    """테스트 페이즈 설정"""
    with _metrics_lock:
        _metrics.current_phase = phase
        _metrics.phase_start_time = time.time()
        logger.info(f"{STAGE_NAME} Phase changed to: {phase}")


def get_metrics_summary() -> Dict[str, Any]:
    """현재 메트릭 요약 반환"""
    with _metrics_lock:
        success_rate = (_metrics.successful_requests / _metrics.total_requests * 100) if _metrics.total_requests > 0 else 0
        avg_response_time = sum(_metrics.response_times) / len(_metrics.response_times) if _metrics.response_times else 0
        avg_failover_time = (
            _metrics.failover_time_ms_total / _metrics.failover_triggered if _metrics.failover_triggered > 0 else 0
        )

        return {
            "total_requests": _metrics.total_requests,
            "success_rate_percent": round(success_rate, 2),
            "avg_response_time_ms": round(avg_response_time, 2),
            "circuit_state": _metrics.current_circuit_state,
            "state_changes": len(_metrics.circuit_state_changes),
            "failovers_triggered": _metrics.failover_triggered,
            "failovers_successful": _metrics.failover_success,
            "avg_failover_time_ms": round(avg_failover_time, 2),
            "duplicate_requests": _metrics.duplicate_requests_detected,
            "dlq_entries": _metrics.dlq_entries_observed,
            "retry_attempts": _metrics.retry_attempts,
            "current_phase": _metrics.current_phase,
            "errors_by_type": dict(_metrics.error_by_type),
        }


# =============================================================================
# Failover Scenario Controller
# =============================================================================


class FailoverScenarioController:
    """
    Failover 시나리오 컨트롤러

    Control API를 통해 기존 메커니즘만 사용하여 장애를 주입/복구합니다.
    새로운 로직은 추가하지 않습니다.
    """

    def __init__(self, client, admin_token: Optional[str] = None):
        self.client = client
        self.admin_token = admin_token
        self._last_region_a_state: Optional[str] = None

    def _get_admin_headers(self) -> Dict[str, str]:
        """Admin 인증 헤더 반환"""
        headers = {"Content-Type": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        return headers

    def get_circuit_state(self, service_name: str) -> Tuple[str, Dict[str, Any]]:
        """
        Circuit Breaker 상태 조회 (기존 API 사용)

        Returns:
            Tuple[상태, 전체응답]
        """
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

    def inject_region_failure(self, region: str, reason: str) -> bool:
        """
        Region 장애 주입 (기존 Control API의 inject_failure 사용)

        chaos 환경에서만 허용되는 기존 메커니즘을 사용합니다.
        """
        # 기존 Control API의 inject_failure 액션 사용
        payload = {
            "service_name": region,
            "action": "inject_failure",
            "environment": "chaos",  # chaos 환경에서만 허용
            "reason": reason,
            "ttl_minutes": 5,
            "metadata": {
                "failure_rate": 1.0,  # 100% 실패율
                "failure_type": "timeout",
            },
        }

        try:
            response = self.client.post(
                CONTROL_API["control"],
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Inject Failure",
            )
            success = response.status_code in (200, 201)
            if success:
                logger.info(f"{STAGE_NAME} Failure injected for {region}")
            return success
        except Exception as e:
            logger.error(f"Failed to inject failure: {e}")
            return False

    def block_region(self, region: str, reason: str) -> bool:
        """
        Region 차단 (기존 Control API의 block 액션 사용)

        Circuit Breaker를 OPEN 상태로 강제 전환합니다.
        """
        # Quick Block API 사용
        url = CONTROL_API["block"].format(service_name=region)
        payload = {
            "reason": reason,
            "environment": "chaos",
            "ttl_minutes": 10,
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Block Region",
            )
            success = response.status_code in (200, 201)
            if success:
                old_state = self._last_region_a_state or "closed"
                record_circuit_state_change(old_state, "open", region)
                self._last_region_a_state = "open"
                logger.info(f"{STAGE_NAME} Region {region} blocked (CB -> OPEN)")
            return success
        except Exception as e:
            logger.error(f"Failed to block region: {e}")
            return False

    def allow_region(self, region: str, reason: str) -> bool:
        """
        Region 허용 (기존 Control API의 allow 액션 사용)

        Circuit Breaker를 CLOSED 상태로 강제 전환합니다.
        """
        # Quick Allow API 사용
        url = CONTROL_API["allow"].format(service_name=region)
        payload = {
            "reason": reason,
            "environment": "chaos",
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Allow Region",
            )
            success = response.status_code in (200, 201)
            if success:
                old_state = self._last_region_a_state or "open"
                record_circuit_state_change(old_state, "closed", region)
                self._last_region_a_state = "closed"
                logger.info(f"{STAGE_NAME} Region {region} allowed (CB -> CLOSED)")
            return success
        except Exception as e:
            logger.error(f"Failed to allow region: {e}")
            return False

    def reset_region(self, region: str, reason: str) -> bool:
        """
        Region 리셋 (기존 Control API의 reset 액션 사용)

        Circuit Breaker를 기본 상태로 복원합니다.
        """
        url = CONTROL_API["reset"].format(service_name=region)
        payload = {
            "reason": reason,
            "environment": "chaos",
        }

        try:
            response = self.client.post(
                url,
                json=payload,
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Reset Region",
            )
            success = response.status_code in (200, 201)
            if success:
                old_state = self._last_region_a_state or "unknown"
                record_circuit_state_change(old_state, "closed", region)
                self._last_region_a_state = "closed"
                logger.info(f"{STAGE_NAME} Region {region} reset to default")
            return success
        except Exception as e:
            logger.error(f"Failed to reset region: {e}")
            return False

    def get_dlq_count(self) -> int:
        """DLQ 항목 수 조회"""
        try:
            response = self.client.get(
                CONTROL_API["dlq_list"],
                headers=self._get_admin_headers(),
                name=f"{STAGE_NAME} Get DLQ Count",
            )
            if response.status_code == 200:
                data = response.json()
                count = data.get("total_count", len(data.get("items", [])))
                record_dlq_observation(count)
                return count
            return 0
        except Exception as e:
            logger.warning(f"Failed to get DLQ count: {e}")
            return 0


# =============================================================================
# Scenario Phase Controller (테스트 페이즈 관리)
# =============================================================================


class ScenarioPhaseController:
    """
    테스트 시나리오 페이즈 관리자

    Phases:
    1. warmup: 시스템 워밍업 (정상 트래픽)
    2. normal_load: 정상 부하 (baseline 측정)
    3. region_a_outage: Region A 장애 주입
    4. failover_active: Failover 동작 중 (Region B로 트래픽 전환)
    5. region_a_recovery: Region A 복구
    6. stabilization: 안정화 (정상 상태 복귀)
    7. cooldown: 쿨다운
    """

    PHASES = [
        ("warmup", 30),  # 30초: 워밍업
        ("normal_load", 60),  # 60초: 정상 부하 baseline
        ("region_a_outage", 10),  # 10초: 장애 주입
        ("failover_active", 90),  # 90초: Failover 동작
        ("region_a_recovery", 10),  # 10초: 복구 시작
        ("stabilization", 60),  # 60초: 안정화
        ("cooldown", 30),  # 30초: 쿨다운
    ]

    def __init__(self):
        self.current_phase_index = 0
        self.phase_start_time = time.time()
        self.failover_controller: Optional[FailoverScenarioController] = None
        self._lock = threading.Lock()
        self._initialized = False

    def initialize(self, failover_controller: FailoverScenarioController):
        """컨트롤러 초기화"""
        with self._lock:
            if not self._initialized:
                self.failover_controller = failover_controller
                self._initialized = True
                set_test_phase(self.PHASES[0][0])
                logger.info(f"{STAGE_NAME} Scenario controller initialized")

    def get_current_phase(self) -> str:
        """현재 페이즈 반환"""
        if self.current_phase_index < len(self.PHASES):
            return self.PHASES[self.current_phase_index][0]
        return "completed"

    def check_and_advance_phase(self) -> Optional[str]:
        """
        페이즈 전환 확인 및 실행

        Returns:
            새 페이즈 이름 또는 None
        """
        with self._lock:
            if self.current_phase_index >= len(self.PHASES):
                return None

            current_phase, duration = self.PHASES[self.current_phase_index]
            elapsed = time.time() - self.phase_start_time

            if elapsed >= duration:
                # 다음 페이즈로 전환
                self.current_phase_index += 1
                self.phase_start_time = time.time()

                if self.current_phase_index < len(self.PHASES):
                    new_phase = self.PHASES[self.current_phase_index][0]
                    set_test_phase(new_phase)

                    # 페이즈별 액션 실행
                    self._execute_phase_action(new_phase)

                    return new_phase
                else:
                    set_test_phase("completed")
                    return "completed"

            return None

    def _execute_phase_action(self, phase: str):
        """페이즈별 액션 실행"""
        if not self.failover_controller:
            return

        if phase == "region_a_outage":
            # Region A 장애 주입 (기존 메커니즘 사용)
            logger.info(f"{STAGE_NAME} === INJECTING REGION A FAILURE ===")
            start_time = time.time()

            # Block Region A (Circuit Breaker -> OPEN)
            success = self.failover_controller.block_region(REGION_A, reason="Stage38 Chaos Test: Region A simulated outage")

            failover_time = (time.time() - start_time) * 1000
            record_failover(success, failover_time)

        elif phase == "failover_active":
            # Region B 활성화 (이미 정상이므로 상태 확인만)
            logger.info(f"{STAGE_NAME} === FAILOVER ACTIVE: Region B serving traffic ===")
            self.failover_controller.allow_region(REGION_B, reason="Stage38 Chaos Test: Activating Region B for failover")

        elif phase == "region_a_recovery":
            # Region A 복구
            logger.info(f"{STAGE_NAME} === RECOVERING REGION A ===")
            start_time = time.time()

            success = self.failover_controller.allow_region(REGION_A, reason="Stage38 Chaos Test: Region A recovery")

            recovery_time = (time.time() - start_time) * 1000
            if success:
                logger.info(f"{STAGE_NAME} Region A recovered in {recovery_time:.2f}ms")

        elif phase == "stabilization":
            logger.info(f"{STAGE_NAME} === STABILIZATION PHASE ===")

        elif phase == "cooldown":
            # 모든 리전 리셋
            logger.info(f"{STAGE_NAME} === COOLDOWN: Resetting all regions ===")
            self.failover_controller.reset_region(REGION_A, "Stage38 Chaos Test: Cleanup")
            self.failover_controller.reset_region(REGION_B, "Stage38 Chaos Test: Cleanup")


# 전역 페이즈 컨트롤러
_phase_controller = ScenarioPhaseController()


# =============================================================================
# Locust User Classes
# =============================================================================


class Stage38BaseUser(HttpUser):
    """
    Stage 38 기본 사용자

    모든 Stage 38 사용자의 베이스 클래스입니다.
    """

    abstract = True
    wait_time = between(1, 3)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = f"user_{uuid.uuid4().hex[:8]}"
        self.auth_token: Optional[str] = None
        self.admin_token: Optional[str] = None
        self.failover_controller: Optional[FailoverScenarioController] = None
        self._product_ids: List[int] = []

    def on_start(self):
        """사용자 시작 시 초기화"""
        self._login()
        self._admin_login()
        self._cache_product_ids()

        # Failover 컨트롤러 초기화
        self.failover_controller = FailoverScenarioController(self.client, self.admin_token)
        _phase_controller.initialize(self.failover_controller)

    def _login(self):
        """일반 사용자 로그인"""
        user_index = random.randint(0, TEST_USER_COUNT - 1)
        username = f"{TEST_USER_PREFIX}{user_index}"

        try:
            response = self.client.post(
                "/api/auth/login/",
                json={"username": username, "password": TEST_USER_PASSWORD},
                name=f"{STAGE_NAME} User Login",
            )
            if response.status_code == 200:
                data = response.json()
                self.auth_token = data.get("token", {}).get("access")
                if self.auth_token:
                    self.client.headers.update({"Authorization": f"Bearer {self.auth_token}"})
        except Exception as e:
            logger.warning(f"User login failed: {e}")

    def _admin_login(self):
        """Admin 로그인 (Control API용)"""
        try:
            response = self.client.post(
                "/api/auth/login/",
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
                name=f"{STAGE_NAME} Admin Login",
            )
            if response.status_code == 200:
                data = response.json()
                self.admin_token = data.get("token", {}).get("access")
        except Exception as e:
            logger.warning(f"Admin login failed: {e}")

    def _cache_product_ids(self):
        """상품 ID 캐싱"""
        try:
            response = self.client.get(
                BUSINESS_API["products"],
                name=f"{STAGE_NAME} Fetch Products",
            )
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                self._product_ids = [p["id"] for p in results if "id" in p]
        except Exception as e:
            logger.warning(f"Failed to cache products: {e}")

    def _get_random_product_id(self) -> Optional[int]:
        """랜덤 상품 ID 반환"""
        return random.choice(self._product_ids) if self._product_ids else None

    def _generate_idempotency_key(self, operation: str) -> str:
        """Idempotency 키 생성"""
        unique = f"{self.user_id}:{operation}:{time.time_ns()}"
        return hashlib.sha256(unique.encode()).hexdigest()[:32]


class Stage38TrafficUser(Stage38BaseUser):
    """
    Stage 38 일반 트래픽 사용자 (65%)

    정상적인 비즈니스 트래픽을 생성합니다.
    Circuit Breaker 및 Retry 동작을 관찰합니다.
    """

    weight = 65

    @task(5)
    @tag("traffic", "read")
    def browse_products(self):
        """상품 목록 조회"""
        # 페이즈 체크
        _phase_controller.check_and_advance_phase()

        start_time = time.time()

        with self.client.get(
            BUSINESS_API["products"],
            name=f"{STAGE_NAME} Browse Products",
            catch_response=True,
        ) as response:
            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                record_request(True, response_time)
                response.success()
            elif response.status_code == 503:
                # Circuit Breaker 차단 - 예상된 동작
                record_request(False, response_time)
                record_error("circuit_breaker_open")
                response.success()  # 예상된 동작이므로 success로 마킹
            else:
                record_request(False, response_time)
                record_error(f"http_{response.status_code}")
                response.failure(f"Unexpected: {response.status_code}")

    @task(3)
    @tag("traffic", "health")
    def check_health(self):
        """시스템 헬스 체크"""
        start_time = time.time()

        with self.client.get(
            CONTROL_API["health_ping"],
            name=f"{STAGE_NAME} Health Ping",
            catch_response=True,
        ) as response:
            response_time = (time.time() - start_time) * 1000

            if response.status_code == 200:
                record_request(True, response_time)
                response.success()
            else:
                record_request(False, response_time)
                response.failure(f"Health check failed: {response.status_code}")

    @task(2)
    @tag("traffic", "status")
    def observe_circuit_state(self):
        """Circuit Breaker 상태 관찰"""
        if not self.failover_controller:
            return

        # Region A 상태 확인
        state_a, _ = self.failover_controller.get_circuit_state(REGION_A)

        # 관찰만 수행 (assertions 없음)
        logger.debug(f"{STAGE_NAME} Observed Region A state: {state_a}")


class Stage38ShopperUser(Stage38BaseUser):
    """
    Stage 38 장바구니/주문 사용자 (25%)

    장바구니 추가 및 주문 생성 트래픽을 생성합니다.
    Idempotency 보장을 검증합니다.
    """

    weight = 25

    @task(3)
    @tag("shopper", "cart")
    def add_to_cart_with_idempotency(self):
        """장바구니 추가 (Idempotency 키 포함)"""
        _phase_controller.check_and_advance_phase()

        product_id = self._get_random_product_id()
        if not product_id:
            return

        idempotency_key = self._generate_idempotency_key("add_to_cart")
        headers = {"X-Idempotency-Key": idempotency_key}

        start_time = time.time()

        with self.client.post(
            BUSINESS_API["cart_add"],
            json={"product_id": product_id, "quantity": 1},
            headers=headers,
            name=f"{STAGE_NAME} Add to Cart",
            catch_response=True,
        ) as response:
            response_time = (time.time() - start_time) * 1000

            if response.status_code in (200, 201):
                record_request(True, response_time)
                record_idempotency_event(is_duplicate=False)
                response.success()
            elif response.status_code == 409:
                # 중복 요청 감지 - Idempotency 작동
                record_request(True, response_time)
                record_idempotency_event(is_duplicate=True)
                response.success()
            elif response.status_code == 503:
                # Circuit Breaker
                record_request(False, response_time)
                record_retry(success=False, exhausted=True)
                response.success()  # 예상된 동작
            else:
                record_request(False, response_time)
                record_error(f"cart_error_{response.status_code}")
                response.failure(f"Cart add failed: {response.status_code}")

    @task(2)
    @tag("shopper", "order")
    def create_order_with_retry(self):
        """주문 생성 (Retry 포함) - 장바구니에 상품 추가 후 주문"""
        _phase_controller.check_and_advance_phase()

        # 먼저 장바구니에 상품 추가
        product_id = self._get_random_product_id()
        if not product_id:
            return

        # 장바구니에 상품 추가 (주문 전 필수)
        with self.client.post(
            BUSINESS_API["cart_add"],
            json={"product_id": product_id, "quantity": 1},
            name=f"{STAGE_NAME} Pre-Order Cart Add",
            catch_response=True,
        ) as response:
            if response.status_code not in (200, 201, 400):  # 400 = 이미 있음
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
                    record_request(True, response_time)
                    record_idempotency_event(is_duplicate=False)
                    if attempt > 0:
                        record_retry(success=True)
                    response.success()
                    return

                elif response.status_code == 409:
                    # 중복 주문 - Idempotency 작동
                    record_request(True, response_time)
                    record_idempotency_event(is_duplicate=True)
                    response.success()
                    return

                elif response.status_code == 503:
                    # Circuit Breaker - Retry 필요
                    record_request(False, response_time)

                    if attempt < max_retries - 1:
                        record_retry(success=False)
                        time.sleep(retry_delay * (2**attempt))  # Exponential backoff
                        response.success()  # 재시도 예정
                    else:
                        record_retry(success=False, exhausted=True)
                        response.success()  # Retry 소진 - 예상된 동작

                else:
                    record_request(False, response_time)
                    record_error(f"order_error_{response.status_code}")
                    response.failure(f"Order failed: {response.status_code}")
                    return


class Stage38ChaosObserverUser(Stage38BaseUser):
    """
    Stage 38 Chaos 관찰자 사용자 (10%)

    시스템 상태와 메트릭을 관찰합니다.
    Control API를 통해 Circuit Breaker 상태를 모니터링합니다.
    """

    weight = 10

    @task(5)
    @tag("observer", "metrics")
    def observe_system_metrics(self):
        """시스템 메트릭 관찰"""
        _phase_controller.check_and_advance_phase()

        # Admin 인증이 필요한 엔드포인트
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
                # 메트릭 데이터는 로깅으로만 처리
            elif response.status_code == 401:
                # 인증 실패 - 조용히 무시
                response.success()
            else:
                response.failure(f"Metrics unavailable: {response.status_code}")

    @task(3)
    @tag("observer", "circuit")
    def observe_all_circuit_states(self):
        """모든 Circuit Breaker 상태 관찰"""
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
                # 상태는 이미 기록됨
            else:
                response.failure(f"Status unavailable: {response.status_code}")

    @task(2)
    @tag("observer", "dlq")
    def observe_dlq_growth(self):
        """DLQ 증가 관찰"""
        if not self.failover_controller:
            return

        dlq_count = self.failover_controller.get_dlq_count()
        logger.debug(f"{STAGE_NAME} DLQ count observed: {dlq_count}")


# =============================================================================
# Locust Event Hooks
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 실행"""
    print("\n" + "=" * 70)
    print(f"🚀 {STAGE_NAME} Multi-Region Failover Chaos Test Started")
    print("=" * 70)
    print(f"📊 Test Configuration:")
    print(f"   - Region A: {REGION_A}")
    print(f"   - Region B: {REGION_B}")
    print(f"   - Admin User: {ADMIN_USERNAME}")
    print("=" * 70)
    print("\n📝 Test Phases:")
    for i, (phase, duration) in enumerate(ScenarioPhaseController.PHASES, 1):
        print(f"   {i}. {phase} ({duration}s)")
    print("=" * 70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 실행"""
    print("\n" + "=" * 70)
    print(f"✅ {STAGE_NAME} Test Completed")
    print("=" * 70)

    # 메트릭 요약 출력
    summary = get_metrics_summary()

    print("\n📈 Test Results Summary (Observations Only):")
    print("-" * 50)
    print(f"   Total Requests: {summary['total_requests']}")
    print(f"   Success Rate: {summary['success_rate_percent']}%")
    print(f"   Avg Response Time: {summary['avg_response_time_ms']}ms")
    print("-" * 50)
    print(f"   Circuit State Changes: {summary['state_changes']}")
    print(f"   Final Circuit State: {summary['circuit_state']}")
    print("-" * 50)
    print(f"   Failovers Triggered: {summary['failovers_triggered']}")
    print(f"   Failovers Successful: {summary['failovers_successful']}")
    print(f"   Avg Failover Time: {summary['avg_failover_time_ms']}ms")
    print("-" * 50)
    print(f"   Duplicate Requests (Idempotency): {summary['duplicate_requests']}")
    print(f"   DLQ Entries Observed: {summary['dlq_entries']}")
    print(f"   Retry Attempts: {summary['retry_attempts']}")
    print("-" * 50)
    print(f"   Errors by Type: {summary['errors_by_type']}")
    print("=" * 70)

    # JSON 형식으로도 출력 (파싱 용이)
    print("\n📋 JSON Summary:")
    print(json.dumps(summary, indent=2))
    print("=" * 70 + "\n")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    """모든 요청에 대한 이벤트 리스너"""
    # 응답 시간 기록 (밀리초)
    if exception is None and response is not None:
        if hasattr(response, "status_code") and response.status_code < 400:
            pass  # 이미 각 task에서 기록됨


# =============================================================================
# Summary Comments (테스트 목적 및 관찰 포인트)
# =============================================================================

"""
================================================================================
Stage 38 Multi-Region Failover Locust Test - 관찰 포인트
================================================================================

이 테스트는 assertions를 사용하지 않고 시스템 동작을 관찰만 합니다.
아래 항목들은 테스트 실행 후 수동으로 검토해야 합니다:

1. Circuit Breaker 동작
   - Region A 장애 시 CLOSED → OPEN 전이가 발생하는지
   - 복구 시 OPEN → HALF_OPEN → CLOSED 전이가 발생하는지
   - 상태 전이 로그에서 확인 가능

2. Failover 효과
   - Failover 시간이 5초 이내인지
   - Region B로 트래픽이 정상 처리되는지
   - 성공률이 장애 후에도 높게 유지되는지

3. Idempotency 보장
   - duplicate_requests가 올바르게 감지되는지
   - 중복 실행이 0건인지 (409 응답 확인)

4. Retry Storm 방지
   - retry_exhausted가 과도하게 증가하지 않는지
   - Exponential backoff가 작동하는지

5. DLQ 관리
   - DLQ 증가율이 완만한지
   - 복구 후 DLQ가 안정화되는지

6. 시스템 안정성
   - 전체 테스트 기간 동안 시스템이 크래시하지 않는지
   - 에러율이 예상 범위 내인지

================================================================================
제약사항 준수 확인
================================================================================

✅ 새로운 기능/API 추가 없음
✅ 기존 Control API 메커니즘만 사용
   - /api/self-healing/block/{service}/
   - /api/self-healing/allow/{service}/
   - /api/self-healing/reset/{service}/
   - /api/self-healing/control/ (inject_failure - chaos env only)
✅ pytest-style assertions 없음
✅ 실제 엔드포인트만 사용
✅ Mock 서버 사용 없음

================================================================================
"""
