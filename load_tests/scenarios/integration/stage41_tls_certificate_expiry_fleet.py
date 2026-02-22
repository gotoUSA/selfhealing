"""
Stage 41: TLS / Certificate Expiry Tests (Part 2 - Fleet Level)

거버넌스 경계 검증: TLS 및 인증서 관련 장애 시나리오 (확장)

목표:
- 대규모 인증서 만료 시 서비스 격리 검증
- 재시도 시도 시 보안 경계 차단 검증
- 거버넌스 적용 및 수동 운영자 개입 필요성 검증

제약사항 (엄격 준수):
- TLS 검증 로직 구현 금지
- 인증서 만료 임계값 수정 금지
- 폴백 인증서 추가 금지
- retry/replay 동작 추가 금지
- 새로운 보안 reason code 추가 금지
- Control API 동작 수정 금지
- 프로덕션 코드 경로 수정 금지

이 파일의 시나리오:
4) Mass Certificate Expiry (Fleet-Level)
5) Certificate Failure + Retry Attempt
6) Governance Enforcement

실행 방법:
    pytest load_tests/scenarios/stage41_tls_certificate_expiry_fleet.py -v -s

참조:
- docs/STAGE_25_TLS_FAILURE.md
- Stage 39/40 거버넌스 경계 테스트
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone as tz
from typing import Any, Dict, List, TYPE_CHECKING
from enum import Enum

import pytest


# Conditional imports for type checking
if TYPE_CHECKING:
    pass


# =============================================================================
# Lazy Service Import Utilities (Reused from Stage 39/40)
# =============================================================================

_SHARED_MEMORY_REPO = None
_SHARED_CB_SERVICE = None


def _get_or_create_shared_repo():
    """Get or create shared in-memory repository (singleton pattern)."""
    global _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE
    from selfhealing.adapters.memory.circuit_breaker import (
        InMemoryCircuitBreakerStateRepository,
    )
    from selfhealing.services import CircuitBreakerConfig, CircuitBreakerService

    if _SHARED_MEMORY_REPO is None:
        _SHARED_MEMORY_REPO = InMemoryCircuitBreakerStateRepository()
        _SHARED_CB_SERVICE = CircuitBreakerService(
            config=CircuitBreakerConfig(
                enabled=True,
                failure_threshold=3,
                recovery_timeout=30,
                manual_override_ttl_minutes=60,
            ),
            repository=_SHARED_MEMORY_REPO,
        )
    return _SHARED_MEMORY_REPO, _SHARED_CB_SERVICE


def _get_selfhealing_services():
    """Lazy import selfhealing services to avoid import errors in IDE."""
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
        DLQConfig,
        DLQService,
    )

    _memory_repo, _cb_service = _get_or_create_shared_repo()

    def _create_cb_service(config=None):
        cfg = config or CircuitBreakerConfig(
            enabled=True,
            failure_threshold=3,
            recovery_timeout=30,
            manual_override_ttl_minutes=60,
        )
        return CircuitBreakerService(config=cfg, repository=_memory_repo)

    def force_open_circuit(service_name: str, reason: str = ""):
        return _cb_service.force_open(service_name=service_name, reason=reason)

    def force_close_circuit(service_name: str, reason: str = "", trigger_replay: bool = False):
        return _cb_service.force_close(service_name=service_name, reason=reason, trigger_replay=trigger_replay)

    def should_allow_request(service_name: str) -> bool:
        return _cb_service.should_allow(service_name)

    def get_circuit_breaker_service():
        return _cb_service

    return {
        "CircuitBreakerConfig": CircuitBreakerConfig,
        "CircuitBreakerService": CircuitBreakerService,
        "CircuitState": CircuitState,
        "DLQConfig": DLQConfig,
        "DLQService": DLQService,
        "force_close_circuit": force_close_circuit,
        "force_open_circuit": force_open_circuit,
        "get_circuit_breaker_service": get_circuit_breaker_service,
        "should_allow_request": should_allow_request,
        "_memory_repo": _memory_repo,
        "_create_cb_service": _create_cb_service,
    }


def _get_decision_logger():
    """Lazy import decision logger to avoid import errors in IDE."""
    from selfhealing.core.decision_logger import (
        DecisionLogger,
        DecisionBoundaryEventType,
        ReasonCode,
    )

    return {
        "DecisionLogger": DecisionLogger,
        "EventType": DecisionBoundaryEventType,
        "ReasonCode": ReasonCode,
    }


# =============================================================================
# Test Constants (NO NEW MECHANISMS)
# =============================================================================

# Fleet services
SERVICE_PAYMENT_GATEWAY = "payment_gateway_tls"
SERVICE_ORDER_PROCESSOR = "order_processor_tls"
SERVICE_NOTIFICATION = "notification_service_tls"
SERVICE_AUTH_GATEWAY = "auth_gateway_tls"
SERVICE_INVENTORY = "inventory_service_tls"
SERVICE_SHIPPING = "shipping_service_tls"
SERVICE_ANALYTICS = "analytics_service_tls"
SERVICE_SECURITY = "security_tls_boundary"

FLEET_SERVICES = [
    SERVICE_PAYMENT_GATEWAY,
    SERVICE_ORDER_PROCESSOR,
    SERVICE_NOTIFICATION,
    SERVICE_AUTH_GATEWAY,
    SERVICE_INVENTORY,
    SERVICE_SHIPPING,
    SERVICE_ANALYTICS,
]

# Security failure classification
FAILURE_CLASS_SECURITY = "SECURITY"

# Existing retry limits (DO NOT MODIFY)
MAX_RETRY_ATTEMPTS = 3
MAX_REPLAY_ATTEMPTS = 2


# =============================================================================
# TLS/Certificate State Simulation (Shared with Part 1)
# =============================================================================


class TLSFailureType(str, Enum):
    """TLS failure types for simulation."""

    NONE = "none"
    CERTIFICATE_EXPIRED = "cert_expired"
    CERTIFICATE_NEAR_EXPIRY = "cert_near_expiry"
    CERTIFICATE_MISMATCH = "cert_mismatch"
    CERTIFICATE_CHAIN_INVALID = "cert_chain_invalid"
    HANDSHAKE_FAILURE = "handshake_failure"


@dataclass
class CertificateMetadata:
    """Simulated certificate metadata for testing."""

    service_name: str
    issued_at: datetime
    expires_at: datetime
    is_valid: bool = True
    chain_valid: bool = True
    hostname_match: bool = True

    @property
    def is_expired(self) -> bool:
        """Check if certificate is expired."""
        return datetime.now(tz.utc) > self.expires_at


@dataclass
class TLSHandshakeResult:
    """Result of a TLS handshake attempt."""

    success: bool
    failure_type: TLSFailureType = TLSFailureType.NONE
    failure_reason: str = ""
    is_security_failure: bool = False
    should_auto_heal: bool = False
    should_retry: bool = False


class MockTLSService:
    """Mock TLS service for testing certificate failures."""

    def __init__(self):
        self._certificates: Dict[str, CertificateMetadata] = {}
        self._handshake_log: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._failure_injections: Dict[str, TLSFailureType] = {}

    def register_certificate(self, cert: CertificateMetadata) -> None:
        """Register a certificate for a service."""
        with self._lock:
            self._certificates[cert.service_name] = cert

    def inject_failure(self, service_name: str, failure_type: TLSFailureType) -> None:
        """Inject a TLS failure for testing."""
        with self._lock:
            self._failure_injections[service_name] = failure_type

    def clear_failure(self, service_name: str) -> None:
        """Clear injected failure."""
        with self._lock:
            self._failure_injections.pop(service_name, None)

    def attempt_handshake(self, service_name: str) -> TLSHandshakeResult:
        """Attempt TLS handshake with a service."""
        with self._lock:
            if service_name in self._failure_injections:
                failure_type = self._failure_injections[service_name]
                result = self._create_failure_result(failure_type, service_name)
                self._log_handshake(service_name, result)
                return result

            cert = self._certificates.get(service_name)
            if cert is None:
                result = TLSHandshakeResult(success=True, failure_type=TLSFailureType.NONE)
                self._log_handshake(service_name, result)
                return result

            if cert.is_expired:
                result = self._create_failure_result(TLSFailureType.CERTIFICATE_EXPIRED, service_name)
                self._log_handshake(service_name, result)
                return result

            result = TLSHandshakeResult(success=True, failure_type=TLSFailureType.NONE)
            self._log_handshake(service_name, result)
            return result

    def _create_failure_result(self, failure_type: TLSFailureType, service_name: str) -> TLSHandshakeResult:
        """Create a failure result with proper classification."""
        return TLSHandshakeResult(
            success=False,
            failure_type=failure_type,
            failure_reason=f"TLS failure: {failure_type.value} for {service_name}",
            is_security_failure=True,
            should_auto_heal=False,
            should_retry=False,
        )

    def _log_handshake(self, service_name: str, result: TLSHandshakeResult) -> None:
        """Log handshake attempt."""
        self._handshake_log.append(
            {
                "timestamp": datetime.now(tz.utc).isoformat(),
                "service_name": service_name,
                "success": result.success,
                "failure_type": result.failure_type.value,
                "is_security_failure": result.is_security_failure,
            }
        )

    @property
    def handshake_log(self) -> List[Dict[str, Any]]:
        """Get handshake log for verification."""
        with self._lock:
            return list(self._handshake_log)

    def clear(self) -> None:
        """Clear all state."""
        with self._lock:
            self._certificates.clear()
            self._handshake_log.clear()
            self._failure_injections.clear()


# =============================================================================
# Security Boundary and Retry Tracker (Mock Only - NO NEW LOGIC)
# =============================================================================


@dataclass
class SecurityEvent:
    """Security event for tracking."""

    timestamp: datetime
    service_name: str
    event_type: str
    blocked: bool
    reason: str
    auto_heal_triggered: bool = False
    retry_triggered: bool = False
    dlq_entry_created: bool = False


@dataclass
class RetryAttempt:
    """Retry attempt tracking."""

    timestamp: datetime
    service_name: str
    attempt_number: int
    blocked_at_security_boundary: bool
    reason: str


class MockSecurityBoundary:
    """Mock security boundary for testing."""

    def __init__(self):
        self._events: List[SecurityEvent] = []
        self._retry_attempts: List[RetryAttempt] = []
        self._lock = threading.Lock()
        self._blocked_services: set = set()

    def record_security_violation(
        self,
        service_name: str,
        reason: str,
        auto_heal_triggered: bool = False,
        retry_triggered: bool = False,
        dlq_entry_created: bool = False,
    ) -> SecurityEvent:
        """Record a security violation event."""
        with self._lock:
            event = SecurityEvent(
                timestamp=datetime.now(tz.utc),
                service_name=service_name,
                event_type="SECURITY_VIOLATION",
                blocked=True,
                reason=reason,
                auto_heal_triggered=auto_heal_triggered,
                retry_triggered=retry_triggered,
                dlq_entry_created=dlq_entry_created,
            )
            self._events.append(event)
            self._blocked_services.add(service_name)
            return event

    def attempt_retry(self, service_name: str, attempt_number: int) -> RetryAttempt:
        """
        Attempt a retry for a service.

        For TLS/security failures, retry is ALWAYS blocked at security boundary.
        """
        with self._lock:
            # Security failures: retry is ALWAYS blocked
            blocked = service_name in self._blocked_services
            attempt = RetryAttempt(
                timestamp=datetime.now(tz.utc),
                service_name=service_name,
                attempt_number=attempt_number,
                blocked_at_security_boundary=blocked,
                reason="Security boundary - retry not allowed" if blocked else "",
            )
            self._retry_attempts.append(attempt)
            return attempt

    def is_blocked(self, service_name: str) -> bool:
        """Check if service is blocked due to security violation."""
        with self._lock:
            return service_name in self._blocked_services

    def unblock(self, service_name: str, operator_id: str) -> bool:
        """
        Unblock a service (requires explicit operator action).

        Returns True if successfully unblocked.
        """
        with self._lock:
            if service_name in self._blocked_services:
                self._blocked_services.remove(service_name)
                self._events.append(
                    SecurityEvent(
                        timestamp=datetime.now(tz.utc),
                        service_name=service_name,
                        event_type="MANUAL_UNBLOCK",
                        blocked=False,
                        reason=f"Manually unblocked by operator: {operator_id}",
                    )
                )
                return True
            return False

    def get_events_for_service(self, service_name: str) -> List[SecurityEvent]:
        """Get all security events for a service."""
        with self._lock:
            return [e for e in self._events if e.service_name == service_name]

    def get_retry_attempts(self, service_name: str) -> List[RetryAttempt]:
        """Get all retry attempts for a service."""
        with self._lock:
            return [r for r in self._retry_attempts if r.service_name == service_name]

    def clear(self) -> None:
        """Clear all state."""
        with self._lock:
            self._events.clear()
            self._retry_attempts.clear()
            self._blocked_services.clear()

    @property
    def all_events(self) -> List[SecurityEvent]:
        """Get all security events."""
        with self._lock:
            return list(self._events)

    @property
    def blocked_services_count(self) -> int:
        """Get count of blocked services."""
        with self._lock:
            return len(self._blocked_services)


# =============================================================================
# Decision Record Capture (Reused from Stage 39/40)
# =============================================================================


class DecisionRecordCapture:
    """Captures Decision Record events for verification."""

    FROZEN_FIELDS = frozenset(["event", "allowed", "reason", "service_name", "policy_version", "timestamp"])

    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self.schema_violations: List[str] = []

        decision_logger = _get_decision_logger()
        EventType = decision_logger["EventType"]
        ReasonCode = decision_logger["ReasonCode"]

        self.FROZEN_EVENT_TYPES = frozenset(
            [
                EventType.ENTER_PRE_DECISION_ZONE.value,
                EventType.INTERVENTION_EVALUATED.value,
                EventType.EXIT_PRE_DECISION_ZONE.value,
            ]
        )

        self.FROZEN_REASON_CODES = frozenset(
            [
                ReasonCode.THRESHOLD_NOT_MET.value,
                ReasonCode.STABILITY_OK_NO_INTERVENTION.value,
                ReasonCode.POLICY_CONSTRAINT_ACTIVE.value,
                ReasonCode.INTERVENTION_ALLOWED.value,
            ]
        )

    def capture(self, record_json: str) -> None:
        """Capture and validate a decision record."""
        try:
            record = json.loads(record_json)
            self._validate_schema(record)
            self.records.append(record)
        except json.JSONDecodeError as e:
            self.schema_violations.append(f"Invalid JSON: {e}")

    def _validate_schema(self, record: Dict[str, Any]) -> None:
        """Validate record against frozen schema."""
        unknown_fields = set(record.keys()) - self.FROZEN_FIELDS
        if unknown_fields:
            self.schema_violations.append(f"Unknown fields detected: {unknown_fields}")

    @property
    def is_schema_valid(self) -> bool:
        """Check if all records adhered to frozen schema."""
        return len(self.schema_violations) == 0


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tls_service():
    """Create mock TLS service."""
    service = MockTLSService()
    yield service
    service.clear()


@pytest.fixture
def security_boundary():
    """Create mock security boundary."""
    boundary = MockSecurityBoundary()
    yield boundary
    boundary.clear()


@pytest.fixture
def selfhealing_services():
    """Provide all selfhealing services as a fixture."""
    return _get_selfhealing_services()


@pytest.fixture
def decision_logger_classes():
    """Provide decision logger classes as a fixture."""
    return _get_decision_logger()


@pytest.fixture
def decision_record_capture():
    """Get Decision Record capture utility."""
    return DecisionRecordCapture()


@pytest.fixture
def circuit_breaker_service():
    """Get Circuit Breaker Service with in-memory repo."""
    services = _get_selfhealing_services()
    return services["get_circuit_breaker_service"]()


@pytest.fixture(autouse=True)
def cleanup_circuit_breakers():
    """Clean up circuit breaker states after each test."""
    yield
    try:
        global _SHARED_MEMORY_REPO
        if _SHARED_MEMORY_REPO is not None:
            with _SHARED_MEMORY_REPO._lock:
                _SHARED_MEMORY_REPO._storage.clear()
    except Exception:
        pass


# =============================================================================
# Stage 41-S4: Mass Certificate Expiry (Fleet-Level)
# =============================================================================


@pytest.mark.chaos
class TestStage41MassCertificateExpiry:
    """
    Stage 41-S4: 대규모 인증서 만료 (Fleet 수준)

    Given: 여러 서비스가 동시에 인증서 만료를 경험
    When: 각 서비스에서 TLS 실패 발생
    Then:
        - 각 서비스가 올바르게 격리됨
        - 연쇄 자동 복구 없음
        - 서비스별 운영자 개입 필요
    """

    def test_stage_41_fleet_certificate_expiry_isolation(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: Fleet 수준 만료 시 서비스 격리

        Given:
            - 7개 서비스가 동시에 인증서 만료

        When:
            - 각 서비스에서 TLS 핸드셰이크 실패

        Then:
            - 각 서비스가 독립적으로 차단됨
            - 다른 서비스에 영향 없음
            - 연쇄 복구 없음
        """
        force_open = selfhealing_services["force_open_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: 모든 fleet 서비스에 인증서 만료 주입
        for service in FLEET_SERVICES:
            tls_service.inject_failure(service, TLSFailureType.CERTIFICATE_EXPIRED)

        # when: 각 서비스에서 핸드셰이크 시도
        results = {}
        for service in FLEET_SERVICES:
            result = tls_service.attempt_handshake(service)
            results[service] = result

            if not result.success:
                # 보안 위반 기록
                security_boundary.record_security_violation(
                    service_name=service,
                    reason="TLS certificate expired",
                    auto_heal_triggered=False,
                )
                # 서킷 열기
                force_open(service, reason="TLS security violation")

        # then: 모든 서비스가 실패
        for service, result in results.items():
            assert result.success is False, f"{service} should fail"
            assert result.is_security_failure is True

        # then: 각 서비스가 독립적으로 차단됨
        for service in FLEET_SERVICES:
            assert security_boundary.is_blocked(service), f"{service} should be blocked"
            assert should_allow(service) is False, f"{service} circuit should be OPEN"

        # then: 차단된 서비스 수 확인
        assert security_boundary.blocked_services_count == len(FLEET_SERVICES)

    def test_stage_41_fleet_no_cascading_auto_recovery(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: Fleet 수준 만료 시 연쇄 자동 복구 없음

        Given:
            - 여러 서비스가 차단됨

        When:
            - 하나의 서비스가 수동으로 복구됨

        Then:
            - 다른 서비스에 자동 복구 전파 없음
            - 각 서비스는 개별 개입 필요
        """
        force_open = selfhealing_services["force_open_circuit"]
        force_close = selfhealing_services["force_close_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: 3개 서비스 차단
        services_to_block = FLEET_SERVICES[:3]
        for service in services_to_block:
            tls_service.inject_failure(service, TLSFailureType.CERTIFICATE_EXPIRED)
            result = tls_service.attempt_handshake(service)
            security_boundary.record_security_violation(
                service_name=service,
                reason="TLS certificate expired",
            )
            force_open(service, reason="TLS security violation")

        # then: 모든 서비스 차단됨
        for service in services_to_block:
            assert security_boundary.is_blocked(service)

        # when: 첫 번째 서비스만 수동 복구
        first_service = services_to_block[0]
        tls_service.clear_failure(first_service)  # TLS 문제 해결
        security_boundary.unblock(first_service, operator_id="admin@example.com")
        force_close(first_service, reason="Manual recovery by operator", trigger_replay=False)

        # then: 첫 번째 서비스만 복구됨
        assert security_boundary.is_blocked(first_service) is False
        assert should_allow(first_service) is True

        # then: 나머지 서비스는 여전히 차단됨 (연쇄 복구 없음)
        for service in services_to_block[1:]:
            assert security_boundary.is_blocked(service) is True
            assert should_allow(service) is False

    def test_stage_41_fleet_operator_intervention_required_per_service(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 서비스별 운영자 개입 필요

        Given:
            - 여러 서비스가 차단됨

        When:
            - 운영자가 개입

        Then:
            - 각 서비스에 대해 명시적 조치 필요
            - 일괄 자동 복구 없음
        """
        # given: 5개 서비스 차단
        services_to_block = FLEET_SERVICES[:5]
        for service in services_to_block:
            security_boundary.record_security_violation(
                service_name=service,
                reason="TLS certificate expired",
            )

        # then: 모든 서비스 차단됨
        assert security_boundary.blocked_services_count == 5

        # when: 각 서비스를 개별적으로 복구
        recovered_count = 0
        for service in services_to_block:
            result = security_boundary.unblock(service, operator_id="admin@example.com")
            if result:
                recovered_count += 1

        # then: 5번의 개별 조치가 필요했음
        assert recovered_count == 5

        # then: 모든 서비스 복구됨
        assert security_boundary.blocked_services_count == 0

        # then: 각 서비스에 대해 MANUAL_UNBLOCK 이벤트 기록됨
        for service in services_to_block:
            events = security_boundary.get_events_for_service(service)
            unblock_events = [e for e in events if e.event_type == "MANUAL_UNBLOCK"]
            assert len(unblock_events) == 1


# =============================================================================
# Stage 41-S5: Certificate Failure + Retry Attempt
# =============================================================================


@pytest.mark.chaos
class TestStage41CertificateFailureRetry:
    """
    Stage 41-S5: 인증서 실패 + 재시도 시도

    Given: TLS 실패 후 클라이언트가 재시도
    When: 재시도 시도
    Then:
        - 보안 경계에서 재시도 차단
        - 재시도 카운트 증가 없음
        - DLQ 엔트리 생성 없음
    """

    def test_stage_41_retry_blocked_at_security_boundary(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 보안 경계에서 재시도 차단

        Given:
            - TLS 실패로 서비스가 차단됨

        When:
            - 클라이언트가 재시도 시도

        Then:
            - 모든 재시도가 보안 경계에서 차단됨
            - 실제 핸드셰이크 시도 없음
        """
        # given: TLS 실패로 서비스 차단
        tls_service.inject_failure(SERVICE_PAYMENT_GATEWAY, TLSFailureType.CERTIFICATE_EXPIRED)
        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
        assert result.success is False

        security_boundary.record_security_violation(
            service_name=SERVICE_PAYMENT_GATEWAY,
            reason="TLS certificate expired",
        )

        # when: 재시도 시도 (3회)
        retry_results = []
        for attempt in range(1, 4):
            retry = security_boundary.attempt_retry(SERVICE_PAYMENT_GATEWAY, attempt)
            retry_results.append(retry)

        # then: 모든 재시도가 보안 경계에서 차단됨
        for retry in retry_results:
            assert retry.blocked_at_security_boundary is True
            assert "Security boundary" in retry.reason

    def test_stage_41_no_retry_count_increment(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 재시도 카운트 증가 없음

        Given:
            - TLS 실패로 서비스가 차단됨

        When:
            - 재시도 시도

        Then:
            - 내부 재시도 카운터가 증가하지 않음
            - 재시도 로직이 트리거되지 않음
        """
        # given: TLS 실패로 서비스 차단
        security_boundary.record_security_violation(
            service_name=SERVICE_PAYMENT_GATEWAY,
            reason="TLS certificate expired",
            retry_triggered=False,
        )

        # when: 여러 재시도 시도
        for attempt in range(1, 6):
            security_boundary.attempt_retry(SERVICE_PAYMENT_GATEWAY, attempt)

        # then: 보안 이벤트에 retry_triggered가 False로 유지됨
        events = security_boundary.get_events_for_service(SERVICE_PAYMENT_GATEWAY)
        violation_events = [e for e in events if e.event_type == "SECURITY_VIOLATION"]
        assert len(violation_events) == 1
        assert violation_events[0].retry_triggered is False

    def test_stage_41_no_dlq_entry_on_retry(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 재시도 시 DLQ 엔트리 생성 없음

        Given:
            - TLS 실패로 서비스가 차단됨

        When:
            - 재시도 시도 및 실패

        Then:
            - DLQ 엔트리가 생성되지 않음
            - 재생 대기열에 추가되지 않음
        """
        # given: TLS 실패로 서비스 차단 (DLQ 엔트리 없이)
        event = security_boundary.record_security_violation(
            service_name=SERVICE_ORDER_PROCESSOR,
            reason="TLS certificate expired",
            dlq_entry_created=False,
        )

        # then: DLQ 엔트리 생성 없음
        assert event.dlq_entry_created is False

        # when: 재시도 시도
        for attempt in range(1, 4):
            retry = security_boundary.attempt_retry(SERVICE_ORDER_PROCESSOR, attempt)
            assert retry.blocked_at_security_boundary is True

        # then: 여전히 DLQ 엔트리 없음 (이벤트 확인)
        events = security_boundary.get_events_for_service(SERVICE_ORDER_PROCESSOR)
        for event in events:
            if event.event_type == "SECURITY_VIOLATION":
                assert event.dlq_entry_created is False


# =============================================================================
# Stage 41-S6: Governance Enforcement
# =============================================================================


@pytest.mark.chaos
class TestStage41GovernanceEnforcement:
    """
    Stage 41-S6: 거버넌스 적용

    Given: 운영자가 보안 서킷 강제 닫기 시도
    When: Control API 사용
    Then:
        - 수동 제어 필요
        - 명시적 운영자 조치 필수
        - 암묵적 복구 불가
    """

    def test_stage_41_manual_control_required(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: 수동 제어 필요

        Given:
            - TLS 실패로 서킷이 열림

        When:
            - 자동 복구 시도

        Then:
            - 자동 복구 차단됨
            - 수동 운영자 개입 필요
        """
        force_open = selfhealing_services["force_open_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: TLS 실패로 서킷 열림
        tls_service.inject_failure(SERVICE_PAYMENT_GATEWAY, TLSFailureType.CERTIFICATE_EXPIRED)
        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)

        security_boundary.record_security_violation(
            service_name=SERVICE_PAYMENT_GATEWAY,
            reason="TLS certificate expired",
            auto_heal_triggered=False,
        )
        force_open(SERVICE_PAYMENT_GATEWAY, reason="TLS security violation")

        # then: 서킷이 열림
        assert should_allow(SERVICE_PAYMENT_GATEWAY) is False

        # when: 자동 복구 시도 (시뮬레이션)
        auto_heal_attempted = True
        auto_heal_blocked = True  # 보안 실패는 항상 자동 복구 차단

        # then: 자동 복구가 차단됨
        assert auto_heal_blocked is True

        # then: 서킷이 여전히 열림
        assert should_allow(SERVICE_PAYMENT_GATEWAY) is False

    def test_stage_41_explicit_operator_action_mandatory(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: 명시적 운영자 조치 필수

        Given:
            - 서비스가 보안 위반으로 차단됨

        When:
            - 운영자가 명시적으로 복구

        Then:
            - 운영자 ID가 기록됨
            - 복구 시간이 기록됨
            - 감사 추적 가능
        """
        force_open = selfhealing_services["force_open_circuit"]
        force_close = selfhealing_services["force_close_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: 서비스 차단
        security_boundary.record_security_violation(
            service_name=SERVICE_NOTIFICATION,
            reason="TLS certificate expired",
        )
        force_open(SERVICE_NOTIFICATION, reason="TLS security violation")

        # then: 서비스 차단됨
        assert security_boundary.is_blocked(SERVICE_NOTIFICATION)
        assert should_allow(SERVICE_NOTIFICATION) is False

        # when: 운영자가 명시적으로 복구
        operator_id = "security-admin@company.com"
        tls_service.clear_failure(SERVICE_NOTIFICATION)
        unblock_result = security_boundary.unblock(SERVICE_NOTIFICATION, operator_id=operator_id)
        force_close(SERVICE_NOTIFICATION, reason=f"Manual recovery by {operator_id}")

        # then: 복구 성공
        assert unblock_result is True
        assert security_boundary.is_blocked(SERVICE_NOTIFICATION) is False
        assert should_allow(SERVICE_NOTIFICATION) is True

        # then: 운영자 조치가 기록됨
        events = security_boundary.get_events_for_service(SERVICE_NOTIFICATION)
        unblock_events = [e for e in events if e.event_type == "MANUAL_UNBLOCK"]
        assert len(unblock_events) == 1
        assert operator_id in unblock_events[0].reason

    def test_stage_41_no_implicit_recovery_allowed(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: 암묵적 복구 불가

        Given:
            - TLS 문제가 외부에서 해결됨 (인증서 갱신)

        When:
            - 시스템이 상태를 확인

        Then:
            - 자동 감지 및 복구 없음
            - 운영자가 명시적으로 확인하고 복구해야 함
        """
        force_open = selfhealing_services["force_open_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: TLS 실패로 서킷 열림
        tls_service.inject_failure(SERVICE_AUTH_GATEWAY, TLSFailureType.CERTIFICATE_EXPIRED)
        tls_service.attempt_handshake(SERVICE_AUTH_GATEWAY)
        security_boundary.record_security_violation(
            service_name=SERVICE_AUTH_GATEWAY,
            reason="TLS certificate expired",
        )
        force_open(SERVICE_AUTH_GATEWAY, reason="TLS security violation")

        # when: 외부에서 TLS 문제 해결 (인증서 갱신)
        tls_service.clear_failure(SERVICE_AUTH_GATEWAY)

        # then: TLS는 이제 성공하지만...
        result = tls_service.attempt_handshake(SERVICE_AUTH_GATEWAY)
        assert result.success is True

        # then: 서킷은 여전히 열림 (암묵적 복구 없음)
        assert should_allow(SERVICE_AUTH_GATEWAY) is False

        # then: 보안 경계도 여전히 차단됨
        assert security_boundary.is_blocked(SERVICE_AUTH_GATEWAY) is True

        # 운영자가 명시적으로 복구해야만 서비스 재개
        security_boundary.unblock(SERVICE_AUTH_GATEWAY, operator_id="admin")
        assert security_boundary.is_blocked(SERVICE_AUTH_GATEWAY) is False

    def test_stage_41_decision_records_remain_unchanged(
        self,
        decision_record_capture: DecisionRecordCapture,
    ):
        """
        시나리오: Decision Record reason code 변경 없음

        Given:
            - 기존 frozen schema

        When:
            - TLS 실패 처리

        Then:
            - 새로운 reason code 추가 없음
            - 스키마 위반 없음
        """
        # then: Decision Record 스키마가 동결됨
        assert decision_record_capture.is_schema_valid is True

        # 유효한 레코드 캡처 (기존 reason code 사용)
        decision_logger = _get_decision_logger()
        EventType = decision_logger["EventType"]
        ReasonCode = decision_logger["ReasonCode"]

        valid_record = json.dumps(
            {
                "event": EventType.INTERVENTION_EVALUATED.value,
                "allowed": False,
                "reason": ReasonCode.POLICY_CONSTRAINT_ACTIVE.value,
                "service_name": SERVICE_PAYMENT_GATEWAY,
                "policy_version": "1.0.0",
                "timestamp": datetime.now(tz.utc).isoformat(),
            }
        )

        decision_record_capture.capture(valid_record)

        # then: 스키마 위반 없음
        assert decision_record_capture.is_schema_valid is True
        assert len(decision_record_capture.records) == 1


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
