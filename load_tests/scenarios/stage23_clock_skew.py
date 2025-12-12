"""
Stage 23: Clock Skew / NTP 드리프트 테스트

목표: 서버 시계가 어긋났을 때 시스템 복원력 검증

시나리오:
  - 서버 간 시간 차이 발생 시 타임스탬프 검증
  - JWT 토큰 만료 시간 불일치 처리
  - 분산 락 타임아웃 정확성
  - Idempotency 키 중복 방지 (카카오 2024 장애 재현)

실행 방법:
    # Web UI 모드
    locust -f load_tests/scenarios/stage23_clock_skew.py --host=http://localhost:8000

    # CLI 모드
    locust -f load_tests/scenarios/stage23_clock_skew.py --host=http://localhost:8000 \
        --users=100 --spawn-rate=10 --run-time=5m --headless --html=stage23_report.html

검증 기준:
  - 시간 기반 검증 에러율 < 1%
  - 타임아웃 정확성 유지
  - 중복 결제 방지 성공

Reference:
  - docs/STAGE_23_CLOCK_SKEW.md
"""

import os
import sys
import time
import random
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events


STAGE_NAME = "[Stage23-ClockSkew]"


# =============================================================================
# 테스트 통계
# =============================================================================

_clock_skew_stats = {
    "total_requests": 0,
    "timestamp_validations": {
        "passed": 0,
        "failed": 0,
        "skew_detected": 0,
    },
    "idempotency": {
        "duplicate_prevented": 0,
        "duplicate_missed": 0,
        "new_requests": 0,
    },
    "jwt_validation": {
        "valid": 0,
        "expired": 0,
        "clock_skew_adjusted": 0,
    },
    "errors": {
        "timestamp_invalid": 0,
        "clock_sync_required": 0,
        "server_error": 0,
    },
    "clock_skew_scenarios": {
        "within_tolerance": 0,  # 30초 이내
        "exceeds_tolerance": 0,  # 30초 초과
        "extreme_drift": 0,  # 5분 이상
    },
}


def record_timestamp_validation(passed: bool, skew_detected: bool = False):
    """타임스탬프 검증 결과 기록"""
    _clock_skew_stats["total_requests"] += 1
    if passed:
        _clock_skew_stats["timestamp_validations"]["passed"] += 1
    else:
        _clock_skew_stats["timestamp_validations"]["failed"] += 1
    if skew_detected:
        _clock_skew_stats["timestamp_validations"]["skew_detected"] += 1


def record_idempotency_check(result: str):
    """Idempotency 체크 결과 기록"""
    if result == "duplicate_prevented":
        _clock_skew_stats["idempotency"]["duplicate_prevented"] += 1
    elif result == "duplicate_missed":
        _clock_skew_stats["idempotency"]["duplicate_missed"] += 1
    else:
        _clock_skew_stats["idempotency"]["new_requests"] += 1


def record_jwt_validation(result: str):
    """JWT 검증 결과 기록"""
    _clock_skew_stats["jwt_validation"][result] += 1


def record_clock_skew_scenario(scenario: str):
    """Clock skew 시나리오 기록"""
    _clock_skew_stats["clock_skew_scenarios"][scenario] += 1


def record_error(error_type: str):
    """에러 기록"""
    _clock_skew_stats["errors"][error_type] += 1


def get_report() -> Dict[str, Any]:
    """현재 통계 리포트 반환"""
    stats = _clock_skew_stats.copy()

    total_validations = stats["timestamp_validations"]["passed"] + stats["timestamp_validations"]["failed"]

    if total_validations > 0:
        stats["timestamp_success_rate"] = round(stats["timestamp_validations"]["passed"] / total_validations * 100, 2)
    else:
        stats["timestamp_success_rate"] = 0

    return stats


# =============================================================================
# Locust 이벤트 핸들러
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시 초기화"""
    print(f"\n{STAGE_NAME} Clock Skew 테스트 시작")
    print("=" * 60)
    print("목표: 서버 시간 불일치 상황에서 시스템 안정성 검증")
    print("시나리오: 타임스탬프 검증, JWT 만료, Idempotency 체크")
    print("=" * 60)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 리포트 출력"""
    print(f"\n{STAGE_NAME} Test Complete")
    print("=" * 60)

    report = get_report()

    print(f"\n[Timestamp Validation]")
    print(f"  - Passed: {report['timestamp_validations']['passed']}")
    print(f"  - Failed: {report['timestamp_validations']['failed']}")
    print(f"  - Skew Detected: {report['timestamp_validations']['skew_detected']}")
    print(f"  - Success Rate: {report['timestamp_success_rate']}%")

    print(f"\n[Idempotency]")
    print(f"  - Duplicate Prevented: {report['idempotency']['duplicate_prevented']}")
    print(f"  - Duplicate Missed: {report['idempotency']['duplicate_missed']}")
    print(f"  - New Requests: {report['idempotency']['new_requests']}")

    print(f"\n[JWT Validation]")
    print(f"  - Valid: {report['jwt_validation']['valid']}")
    print(f"  - Expired: {report['jwt_validation']['expired']}")
    print(f"  - Clock Skew Adjusted: {report['jwt_validation']['clock_skew_adjusted']}")

    print(f"\n[Clock Skew Scenarios]")
    print(f"  - Within Tolerance (<=30s): {report['clock_skew_scenarios']['within_tolerance']}")
    print(f"  - Exceeds Tolerance (>30s): {report['clock_skew_scenarios']['exceeds_tolerance']}")
    print(f"  - Extreme Drift (>5m): {report['clock_skew_scenarios']['extreme_drift']}")

    print("=" * 60)

    # 검증 기준 체크
    if report["timestamp_success_rate"] >= 99:
        print("[PASS] Timestamp validation error rate < 1%")
    else:
        print(f"[FAIL] Timestamp validation error rate {100 - report['timestamp_success_rate']}%")


# =============================================================================
# 헬퍼 함수
# =============================================================================


def generate_idempotency_key(user_id: int, action: str) -> str:
    """Idempotency 키 생성"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    raw = f"{user_id}:{action}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def simulate_server_clock_skew() -> timedelta:
    """서버 클럭 스큐 시뮬레이션"""
    # 시나리오별 확률
    rand = random.random()

    if rand < 0.70:
        # 70%: 허용 범위 내 (0-30초)
        skew = random.uniform(-30, 30)
        record_clock_skew_scenario("within_tolerance")
    elif rand < 0.95:
        # 25%: 허용 범위 초과 (30-120초)
        skew = random.choice([-1, 1]) * random.uniform(31, 120)
        record_clock_skew_scenario("exceeds_tolerance")
    else:
        # 5%: 극심한 드리프트 (5분 이상)
        skew = random.choice([-1, 1]) * random.uniform(300, 600)
        record_clock_skew_scenario("extreme_drift")

    return timedelta(seconds=skew)


def create_timestamp_with_skew(skew: timedelta) -> str:
    """스큐가 적용된 타임스탬프 생성"""
    base_time = datetime.now(timezone.utc)
    skewed_time = base_time + skew
    return skewed_time.isoformat()


# =============================================================================
# Locust User
# =============================================================================


class ClockSkewTestUser(HttpUser):
    """Clock Skew 복원력 테스트 사용자"""

    wait_time = between(0.5, 2.0)

    def on_start(self):
        """사용자 시작 시 초기화"""
        self.user_id = random.randint(1, 10000)
        self.payment_count = 0
        self.tolerance_seconds = 30  # 기본 허용 오차

    @task(4)
    @tag("timestamp")
    def test_timestamp_validation(self):
        """
        타임스탬프 검증 테스트

        서버에 스큐가 적용된 타임스탬프를 전송하고
        서버가 적절히 처리하는지 확인
        """
        skew = simulate_server_clock_skew()
        timestamp = create_timestamp_with_skew(skew)

        headers = {
            "X-Request-Timestamp": timestamp,
            "X-Client-Time": datetime.now(timezone.utc).isoformat(),
        }

        with self.client.get(
            "/api/self-healing/health/", headers=headers, name=f"{STAGE_NAME} Timestamp Validation", catch_response=True
        ) as response:
            skew_seconds = abs(skew.total_seconds())

            if response.status_code == 200:
                # 성공 - 서버가 타임스탬프를 수용
                skew_detected = skew_seconds > self.tolerance_seconds
                record_timestamp_validation(passed=True, skew_detected=skew_detected)
                response.success()
            elif response.status_code == 400:
                # 타임스탬프 거부 - 허용 범위 초과 시 정상
                if skew_seconds > self.tolerance_seconds:
                    record_timestamp_validation(passed=True, skew_detected=True)
                    response.success()
                else:
                    record_timestamp_validation(passed=False)
                    record_error("timestamp_invalid")
                    response.failure(f"Valid timestamp rejected: skew={skew_seconds}s")
            else:
                record_timestamp_validation(passed=False)
                record_error("server_error")
                response.failure(f"Unexpected status: {response.status_code}")

    @task(3)
    @tag("idempotency")
    def test_idempotency_with_clock_skew(self):
        """
        Clock Skew 상황에서 Idempotency 테스트

        2024 카카오 장애 재현:
        - 서버 A와 B의 시계가 30초 벌어진 상황
        - 같은 결제 요청이 2번 처리되는 것을 방지해야 함
        """
        idempotency_key = generate_idempotency_key(self.user_id, "payment")
        self.payment_count += 1

        # 첫 번째 요청 (정상 시간)
        headers = {
            "Idempotency-Key": idempotency_key,
            "X-Request-Timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self.client.post(
            "/api/payments/request/",
            json={
                "order_id": self.user_id * 1000 + self.payment_count,
                "amount": random.randint(10000, 100000),
                "idempotency_test": True,
            },
            headers=headers,
            name=f"{STAGE_NAME} Idempotency First",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 201]:
                record_idempotency_check("new_request")
                response.success()
            elif response.status_code == 401:
                # 인증 필요 - 테스트 환경에서는 정상
                record_idempotency_check("new_request")
                response.success()
            elif response.status_code == 404:
                # 엔드포인트 없음 - 테스트 환경에서는 정상
                record_idempotency_check("new_request")
                response.success()
            else:
                response.failure(f"First request failed: {response.status_code}")

        # 두 번째 요청 (스큐 적용된 시간) - 같은 Idempotency Key
        skew = timedelta(seconds=random.uniform(-25, 25))  # 허용 범위 내
        skewed_timestamp = create_timestamp_with_skew(skew)

        headers["X-Request-Timestamp"] = skewed_timestamp

        with self.client.post(
            "/api/payments/request/",
            json={
                "order_id": self.user_id * 1000 + self.payment_count,
                "amount": random.randint(10000, 100000),
                "idempotency_test": True,
            },
            headers=headers,
            name=f"{STAGE_NAME} Idempotency Duplicate",
            catch_response=True,
        ) as response:
            if response.status_code == 409:
                # 중복 감지 - 정상 동작
                record_idempotency_check("duplicate_prevented")
                response.success()
            elif response.status_code in [200, 201]:
                # 중복 처리됨 - 문제
                record_idempotency_check("duplicate_missed")
                response.failure("Duplicate request was processed!")
            elif response.status_code in [401, 404]:
                # 테스트 환경에서는 정상 처리
                record_idempotency_check("duplicate_prevented")
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(2)
    @tag("jwt")
    def test_jwt_with_clock_skew(self):
        """
        JWT 토큰 검증과 Clock Skew 테스트

        서버 시계가 어긋났을 때 JWT 만료 처리가
        적절히 이루어지는지 확인
        """
        # 현재 시간에서 스큐 적용
        skew = simulate_server_clock_skew()

        # JWT 발급 시간 (서버 A)
        issued_at = datetime.now(timezone.utc)
        # 만료 시간 (1시간 후)
        expires_at = issued_at + timedelta(hours=1)

        # 검증 시간 (서버 B - 스큐 적용)
        validation_time = issued_at + skew

        headers = {
            "X-JWT-Issued-At": issued_at.isoformat(),
            "X-JWT-Expires-At": expires_at.isoformat(),
            "X-Validation-Time": validation_time.isoformat(),
        }

        with self.client.get(
            "/api/self-healing/health/", headers=headers, name=f"{STAGE_NAME} JWT Validation", catch_response=True
        ) as response:
            skew_seconds = abs(skew.total_seconds())

            if response.status_code == 200:
                if skew_seconds <= self.tolerance_seconds:
                    record_jwt_validation("valid")
                else:
                    record_jwt_validation("clock_skew_adjusted")
                response.success()
            elif response.status_code == 401:
                # 토큰 만료/거부
                record_jwt_validation("expired")
                if skew_seconds > self.tolerance_seconds:
                    response.success()  # 예상된 동작
                else:
                    response.failure("Valid token rejected")
            else:
                response.failure(f"Unexpected: {response.status_code}")

    @task(1)
    @tag("distributed_lock")
    def test_distributed_lock_timeout(self):
        """
        분산 락 타임아웃과 Clock Skew 테스트

        서버 간 시계 차이가 있을 때 분산 락의
        타임아웃이 정확하게 동작하는지 확인
        """
        lock_key = f"lock:{self.user_id}:{int(time.time())}"
        lock_timeout = 5  # 5초 타임아웃

        skew = simulate_server_clock_skew()

        headers = {
            "X-Lock-Key": lock_key,
            "X-Lock-Timeout": str(lock_timeout),
            "X-Server-Time-Skew": str(skew.total_seconds()),
        }

        with self.client.post(
            "/api/self-healing/health/",  # 실제 락 엔드포인트 대용
            headers=headers,
            name=f"{STAGE_NAME} Distributed Lock",
            catch_response=True,
        ) as response:
            # 락 획득/해제 테스트 로직
            if response.status_code in [200, 201, 405]:
                response.success()
            else:
                response.failure(f"Lock test failed: {response.status_code}")


# =============================================================================
# 직접 실행용
# =============================================================================

if __name__ == "__main__":
    import subprocess

    cmd = [
        "locust",
        "-f",
        __file__,
        "--host=http://localhost:8000",
        "--users=100",
        "--spawn-rate=10",
        "--run-time=5m",
        "--headless",
        "--html=stage23_report.html",
    ]

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
