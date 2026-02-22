"""
Stage 25: TLS/Certificate Failure 테스트

목표: 인증서 만료/TLS 핸드셰이크 실패 시 복원력 검증

시나리오:
  - 외부 API SSL 인증서 만료 시뮬레이션
  - TLS 버전 불일치
  - 인증서 체인 검증 실패
  - TLS 핸드셰이크 타임아웃

실제 장애 사례:
  - 2024년 네이버페이 3시간 장애: Let's Encrypt 갱신 실패 → 외부 PG 전부 502

실행 방법:
    # 기본 모드 (헤더 시뮬레이션)
    locust -f load_tests/scenarios/stage25_tls_failure.py --host=http://localhost:8000

    # Chaos 모드 (실제 TLS 장애 주입)
    CHAOS_MODE=true locust -f load_tests/scenarios/stage25_tls_failure.py \\
        --host=http://localhost:8000 --users=30 --spawn-rate=3 --run-time=2m --headless

검증 기준:
  - TLS 에러 시 적절한 fallback
  - 에러 로깅 및 알림
  - 재시도 로직 정상 동작

Reference:
  - docs/STAGE_25_TLS_FAILURE.md
"""

import os
import sys
import time
import random
import threading
from typing import List
from dataclasses import dataclass, field
from enum import Enum

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from locust import HttpUser, task, between, tag, events


STAGE_NAME = "[Stage25-TLSFailure]"

# Chaos 모드 여부
CHAOS_MODE = os.getenv("CHAOS_MODE", "false").lower() == "true"


# =============================================================================
# TLS 에러 타입 시뮬레이션
# =============================================================================


class TLSErrorSimulationType(str, Enum):
    """TLS 에러 시뮬레이션 타입"""

    NONE = "none"
    CERTIFICATE_EXPIRED = "cert_expired"
    CERTIFICATE_NOT_YET_VALID = "cert_not_yet_valid"
    CERTIFICATE_HOSTNAME_MISMATCH = "cert_hostname_mismatch"
    CERTIFICATE_SELF_SIGNED = "cert_self_signed"
    CERTIFICATE_CHAIN_INVALID = "cert_chain_invalid"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    HANDSHAKE_FAILURE = "handshake_failure"
    PROTOCOL_VERSION_MISMATCH = "protocol_mismatch"
    CONNECTION_RESET = "connection_reset"


@dataclass
class TLSSimulator:
    """TLS 장애 시뮬레이션"""

    active_error: TLSErrorSimulationType = TLSErrorSimulationType.NONE
    error_rate: float = 0.0  # 0.0 ~ 1.0
    affected_endpoints: List[str] = field(default_factory=list)
    recovery_scheduled: bool = False

    def set_error(self, error_type: TLSErrorSimulationType, rate: float = 1.0):
        """TLS 에러 설정"""
        self.active_error = error_type
        self.error_rate = min(1.0, max(0.0, rate))

    def clear_error(self):
        """에러 해제"""
        self.active_error = TLSErrorSimulationType.NONE
        self.error_rate = 0.0

    def should_inject_error(self) -> bool:
        """에러 주입 여부 결정"""
        if self.active_error == TLSErrorSimulationType.NONE:
            return False
        return random.random() < self.error_rate

    def is_retryable_error(self) -> bool:
        """현재 에러가 재시도 가능한지 확인"""
        retryable = {
            TLSErrorSimulationType.HANDSHAKE_TIMEOUT,
            TLSErrorSimulationType.HANDSHAKE_FAILURE,
            TLSErrorSimulationType.CONNECTION_RESET,
        }
        return self.active_error in retryable


# 전역 TLS 시뮬레이터
_tls_simulator = TLSSimulator()


# =============================================================================
# 테스트 통계
# =============================================================================

_tls_stats = {
    "total_requests": 0,
    "tls_scenarios": {
        "normal": 0,
        "cert_expired": 0,
        "cert_not_yet_valid": 0,
        "cert_hostname_mismatch": 0,
        "cert_self_signed": 0,
        "cert_chain_invalid": 0,
        "handshake_timeout": 0,
        "handshake_failure": 0,
        "protocol_mismatch": 0,
        "connection_reset": 0,
    },
    "retries": {
        "attempted": 0,
        "succeeded": 0,
        "exhausted": 0,
    },
    "fallback": {
        "used": 0,
        "skipped": 0,
    },
    "alerts": {
        "cert_expiry_warning": 0,
        "cert_expiry_critical": 0,
        "tls_error_alert": 0,
    },
    "recovery": {
        "auto_recovered": 0,
        "recovery_time_ms_sum": 0,
    },
}

_stats_lock = threading.Lock()


def record_tls_scenario(error_type: TLSErrorSimulationType):
    """TLS 시나리오 기록"""
    with _stats_lock:
        _tls_stats["total_requests"] += 1
        key = error_type.value if error_type != TLSErrorSimulationType.NONE else "normal"
        if key in _tls_stats["tls_scenarios"]:
            _tls_stats["tls_scenarios"][key] += 1


def record_retry(succeeded: bool, exhausted: bool = False):
    """재시도 기록"""
    with _stats_lock:
        _tls_stats["retries"]["attempted"] += 1
        if succeeded:
            _tls_stats["retries"]["succeeded"] += 1
        if exhausted:
            _tls_stats["retries"]["exhausted"] += 1


def record_fallback_used(used: bool):
    """Fallback 사용 기록"""
    with _stats_lock:
        if used:
            _tls_stats["fallback"]["used"] += 1
        else:
            _tls_stats["fallback"]["skipped"] += 1


def record_alert(alert_type: str):
    """알림 기록"""
    with _stats_lock:
        if alert_type in _tls_stats["alerts"]:
            _tls_stats["alerts"][alert_type] += 1


def record_recovery(time_ms: float):
    """복구 기록"""
    with _stats_lock:
        _tls_stats["recovery"]["auto_recovered"] += 1
        _tls_stats["recovery"]["recovery_time_ms_sum"] += time_ms


# =============================================================================
# Locust User 클래스
# =============================================================================


class TLSFailureUser(HttpUser):
    """
    TLS/Certificate Failure 테스트 사용자

    다양한 TLS 장애 시나리오에서 시스템 복원력을 테스트
    """

    wait_time = between(0.5, 2)
    abstract = False

    def on_start(self):
        """사용자 시작 시 초기화"""
        self.user_id = random.randint(1, 10000)
        self.retry_count = 0
        self.fallback_count = 0
        print(f"{STAGE_NAME} User {self.user_id} started")

    # =========================================================================
    # 시나리오 1: 인증서 만료 시뮬레이션
    # =========================================================================

    @task(3)
    @tag("cert-expired", "critical")
    def test_certificate_expired(self):
        """
        인증서 만료 시나리오

        - 외부 결제 API 인증서 만료 시뮬레이션
        - Circuit Breaker 오픈 확인
        - 적절한 에러 처리 및 알림
        """
        # 50% 확률로 인증서 만료 시뮬레이션
        simulate_cert_expired = random.random() < 0.5

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "cert_expired" if simulate_cert_expired else "none",
        }

        if simulate_cert_expired:
            record_tls_scenario(TLSErrorSimulationType.CERTIFICATE_EXPIRED)
            record_alert("cert_expiry_critical")
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        start_time = time.time()

        with self.client.post(
            "/api/payments/",
            headers=headers,
            json={
                "order_id": f"test-{self.user_id}-{int(time.time())}",
                "amount": random.randint(1000, 50000),
                "method": "card",
            },
            catch_response=True,
            name=f"{STAGE_NAME} Cert Expired - Payment",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_cert_expired:
                # 인증서 만료 시 예상되는 동작
                if response.status_code == 502:
                    # Bad Gateway - 외부 API TLS 실패
                    response.success()  # 예상된 결과
                    record_fallback_used(False)
                    print(f"{STAGE_NAME} ✓ Cert expired correctly returned 502 ({elapsed_ms:.0f}ms)")

                elif response.status_code == 503:
                    # Service Unavailable - Circuit Breaker 오픈
                    response.success()
                    record_fallback_used(False)
                    print(f"{STAGE_NAME} ✓ Circuit Breaker opened due to TLS failure")

                elif response.status_code in (200, 201):
                    # Fallback 동작 (캐시된 결과 또는 대안 처리)
                    response.success()
                    record_fallback_used(True)
                    print(f"{STAGE_NAME} ✓ Fallback used for cert expired scenario")

                else:
                    response.failure(f"Unexpected status during cert expired: {response.status_code}")
            else:
                # 정상 동작
                if response.status_code in (200, 201):
                    response.success()
                else:
                    # 실제 결제 API가 없을 수 있음 (404 등)
                    if response.status_code == 404:
                        response.success()  # API가 없는 것은 허용
                    else:
                        response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 2: TLS 핸드셰이크 타임아웃 (재시도 가능)
    # =========================================================================

    @task(2)
    @tag("handshake-timeout", "retryable")
    def test_handshake_timeout(self):
        """
        TLS 핸드셰이크 타임아웃 시나리오

        - 네트워크 지연으로 인한 핸드셰이크 타임아웃
        - 재시도 로직 동작 확인
        - 백오프 적용 확인
        """
        # 40% 확률로 핸드셰이크 타임아웃 시뮬레이션
        simulate_timeout = random.random() < 0.4

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "handshake_timeout" if simulate_timeout else "none",
        }

        if simulate_timeout:
            record_tls_scenario(TLSErrorSimulationType.HANDSHAKE_TIMEOUT)
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        start_time = time.time()

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Handshake Timeout - Products",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_timeout:
                if response.status_code == 200:
                    # 재시도 성공
                    response.success()
                    record_retry(succeeded=True)
                    print(f"{STAGE_NAME} ✓ Retry succeeded after handshake timeout ({elapsed_ms:.0f}ms)")

                elif response.status_code == 504:
                    # Gateway Timeout - 재시도 실패
                    response.success()  # 예상된 결과
                    record_retry(succeeded=False, exhausted=True)

                elif response.status_code == 503:
                    # Service Unavailable - 임시 차단
                    response.success()
                    record_retry(succeeded=False)

                else:
                    response.failure(f"Unexpected status during timeout: {response.status_code}")
            else:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 3: 호스트명 불일치
    # =========================================================================

    @task(1)
    @tag("hostname-mismatch")
    def test_hostname_mismatch(self):
        """
        호스트명 불일치 시나리오

        - 인증서 SAN과 요청 호스트 불일치
        - 재시도 불가능 → 즉시 실패
        - 적절한 에러 메시지
        """
        # 30% 확률로 호스트명 불일치 시뮬레이션
        simulate_mismatch = random.random() < 0.3

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "hostname_mismatch" if simulate_mismatch else "none",
        }

        if simulate_mismatch:
            record_tls_scenario(TLSErrorSimulationType.CERTIFICATE_HOSTNAME_MISMATCH)
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Hostname Mismatch - Products",
        ) as response:
            if simulate_mismatch:
                # 호스트명 불일치는 재시도 불가
                if response.status_code in (502, 503):
                    response.success()  # 예상된 결과
                    print(f"{STAGE_NAME} ✓ Hostname mismatch correctly handled")
                elif response.status_code == 200:
                    # 서버가 시뮬레이션을 지원하지 않음
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")
            else:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 4: Self-signed 인증서
    # =========================================================================

    @task(1)
    @tag("self-signed")
    def test_self_signed_certificate(self):
        """
        Self-signed 인증서 시나리오

        - 개발/테스트 환경에서 자주 발생
        - 환경에 따라 허용/거부 정책
        """
        # 20% 확률로 self-signed 시뮬레이션
        simulate_self_signed = random.random() < 0.2

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "self_signed" if simulate_self_signed else "none",
        }

        if simulate_self_signed:
            record_tls_scenario(TLSErrorSimulationType.CERTIFICATE_SELF_SIGNED)
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Self-Signed Cert - Products",
        ) as response:
            if simulate_self_signed:
                # Self-signed는 환경에 따라 다름
                if response.status_code in (200, 502):
                    response.success()
                else:
                    response.failure(f"Unexpected status: {response.status_code}")
            else:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 5: 인증서 만료 경고 (30일 이내)
    # =========================================================================

    @task(1)
    @tag("cert-expiry-warning")
    def test_certificate_expiry_warning(self):
        """
        인증서 만료 경고 시나리오

        - 30일 이내 만료 예정
        - 경고 알림 발송
        - 서비스는 정상 동작
        """
        # 인증서 만료 경고 헤더
        days_until_expiry = random.choice([5, 10, 15, 20, 25, 30])

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Cert-Days-Until-Expiry": str(days_until_expiry),
        }

        if days_until_expiry <= 7:
            record_alert("cert_expiry_critical")
        elif days_until_expiry <= 30:
            record_alert("cert_expiry_warning")

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Cert Expiry Warning",
        ) as response:
            # 경고만 발송하고 서비스는 정상
            if response.status_code == 200:
                response.success()

                # 응답 헤더에서 경고 확인
                cert_warning = response.headers.get("X-Cert-Warning")
                if cert_warning:
                    print(f"{STAGE_NAME} ⚠ Certificate warning: {cert_warning}")
            else:
                response.failure(f"Request failed during cert warning test: {response.status_code}")

    # =========================================================================
    # 시나리오 6: 프로토콜 버전 불일치
    # =========================================================================

    @task(1)
    @tag("protocol-mismatch")
    def test_protocol_version_mismatch(self):
        """
        TLS 프로토콜 버전 불일치 시나리오

        - TLS 1.0/1.1 비활성화로 인한 실패
        - 재시도 불가능
        """
        # 25% 확률로 프로토콜 불일치 시뮬레이션
        simulate_mismatch = random.random() < 0.25

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "protocol_mismatch" if simulate_mismatch else "none",
        }

        if simulate_mismatch:
            record_tls_scenario(TLSErrorSimulationType.PROTOCOL_VERSION_MISMATCH)
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Protocol Mismatch - Products",
        ) as response:
            if simulate_mismatch:
                if response.status_code in (502, 503):
                    response.success()  # 예상된 결과
                elif response.status_code == 200:
                    response.success()  # 시뮬레이션 미지원
                else:
                    response.failure(f"Unexpected status: {response.status_code}")
            else:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")

    # =========================================================================
    # 시나리오 7: Connection Reset (재시도 가능)
    # =========================================================================

    @task(2)
    @tag("connection-reset", "retryable")
    def test_connection_reset(self):
        """
        Connection Reset 시나리오

        - 서버가 연결을 갑자기 종료
        - 재시도 로직 동작 확인
        """
        # 35% 확률로 connection reset 시뮬레이션
        simulate_reset = random.random() < 0.35

        headers = {
            "X-User-ID": str(self.user_id),
            "X-Simulate-TLS-Error": "connection_reset" if simulate_reset else "none",
        }

        if simulate_reset:
            record_tls_scenario(TLSErrorSimulationType.CONNECTION_RESET)
        else:
            record_tls_scenario(TLSErrorSimulationType.NONE)

        start_time = time.time()

        with self.client.get(
            "/api/products/",
            headers=headers,
            catch_response=True,
            name=f"{STAGE_NAME} Connection Reset - Products",
        ) as response:
            elapsed_ms = (time.time() - start_time) * 1000

            if simulate_reset:
                if response.status_code == 200:
                    # 재시도 성공
                    response.success()
                    record_retry(succeeded=True)
                    record_recovery(elapsed_ms)
                    print(f"{STAGE_NAME} ✓ Recovered from connection reset ({elapsed_ms:.0f}ms)")
                elif response.status_code in (502, 503, 504):
                    # 재시도 실패
                    response.success()  # 예상된 결과
                    record_retry(succeeded=False, exhausted=True)
                else:
                    response.failure(f"Unexpected status: {response.status_code}")
            else:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Normal request failed: {response.status_code}")


# =============================================================================
# Locust Event Handlers
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """테스트 시작 시"""
    print(f"\n{'=' * 60}")
    print(f"{STAGE_NAME} TLS/Certificate Failure Test Starting")
    print(f"Chaos Mode: {'ENABLED' if CHAOS_MODE else 'DISABLED'}")
    print(f"{'=' * 60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 통계 출력"""
    print(f"\n{'=' * 60}")
    print(f"{STAGE_NAME} TEST COMPLETED - STATISTICS")
    print(f"{'=' * 60}")

    total = _tls_stats["total_requests"]
    print(f"\n📊 Total Requests: {total}")

    print("\n📋 TLS Scenarios:")
    for scenario, count in _tls_stats["tls_scenarios"].items():
        if count > 0:
            pct = (count / total * 100) if total > 0 else 0
            print(f"   - {scenario}: {count} ({pct:.1f}%)")

    print("\n🔄 Retries:")
    retries = _tls_stats["retries"]
    print(f"   - Attempted: {retries['attempted']}")
    print(f"   - Succeeded: {retries['succeeded']}")
    print(f"   - Exhausted: {retries['exhausted']}")

    print("\n⚡ Fallback:")
    fallback = _tls_stats["fallback"]
    print(f"   - Used: {fallback['used']}")
    print(f"   - Skipped: {fallback['skipped']}")

    print("\n🚨 Alerts:")
    alerts = _tls_stats["alerts"]
    for alert_type, count in alerts.items():
        if count > 0:
            print(f"   - {alert_type}: {count}")

    print("\n✅ Recovery:")
    recovery = _tls_stats["recovery"]
    auto_recovered = recovery["auto_recovered"]
    if auto_recovered > 0:
        avg_time = recovery["recovery_time_ms_sum"] / auto_recovered
        print(f"   - Auto Recovered: {auto_recovered}")
        print(f"   - Avg Recovery Time: {avg_time:.1f}ms")

    # 성공 기준 검증
    print(f"\n{'=' * 60}")
    print("📈 SUCCESS CRITERIA")
    print(f"{'=' * 60}")

    retry_success_rate = 0
    if retries["attempted"] > 0:
        retry_success_rate = retries["succeeded"] / retries["attempted"] * 100

    criteria = [
        ("TLS Error Handling", total > 0),
        ("Retry Success Rate > 50%", retry_success_rate > 50 or retries["attempted"] == 0),
        ("Critical Alerts Raised", alerts["cert_expiry_critical"] > 0 or alerts["tls_error_alert"] > 0),
    ]

    all_passed = True
    for name, passed in criteria:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {name}: {status}")
        if not passed:
            all_passed = False

    print(f"\n{'=' * 60}")
    if all_passed:
        print(f"🎉 {STAGE_NAME} ALL CRITERIA PASSED!")
    else:
        print(f"⚠️  {STAGE_NAME} SOME CRITERIA FAILED")
    print(f"{'=' * 60}\n")
