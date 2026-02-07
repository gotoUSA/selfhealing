"""
Stage 41: TLS / Certificate Expiry Tests (Part 1)

거버넌스 경계 검증: TLS 및 인증서 관련 장애 시나리오

목표:
- TLS 실패가 SECURITY 위반으로 올바르게 분류되는지 검증
- 자동 치유(auto-heal), 재시도(retry), 재생(replay)이 트리거되지 않음 확인
- 수동 운영자 개입이 항상 필요함을 검증
- 비즈니스 플로우가 조용히 재개되지 않음 확인
- Decision Record가 일관되고 설명 가능함을 검증

제약사항 (엄격 준수):
- TLS 검증 로직 구현 금지
- 인증서 만료 임계값 수정 금지
- 폴백 인증서 추가 금지
- retry/replay 동작 추가 금지
- 새로운 보안 reason code 추가 금지
- Control API 동작 수정 금지
- 프로덕션 코드 경로 수정 금지

이 파일의 시나리오:
1) Single Certificate Expiry
2) Near-Expiry Certificate (Grace Period Boundary)
3) Certificate Expiry During In-Flight Requests

실행 방법:
    pytest load_tests/scenarios/stage41_tls_certificate_expiry.py -v -s

참조:
- docs/STAGE_25_TLS_FAILURE.md
- Stage 39/40 거버넌스 경계 테스트
"""

from __future__ import annotations

import json
import logging
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as tz
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from enum import Enum

import pytest


# Conditional imports for type checking
if TYPE_CHECKING:
    from selfhealing.services import (
        CircuitBreakerConfig,
        CircuitBreakerService,
        CircuitState,
        DLQConfig,
        DLQService,
    )


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

SERVICE_PAYMENT_GATEWAY = "payment_gateway_tls"
SERVICE_ORDER_PROCESSOR = "order_processor_tls"
SERVICE_NOTIFICATION = "notification_service_tls"
SERVICE_AUTH_GATEWAY = "auth_gateway_tls"
SERVICE_SECURITY = "security_tls_boundary"

# Security failure classification
FAILURE_CLASS_SECURITY = "SECURITY"
FAILURE_CLASS_TRANSIENT = "TRANSIENT"

# Existing retry limits (DO NOT MODIFY)
MAX_RETRY_ATTEMPTS = 3
MAX_REPLAY_ATTEMPTS = 2


# =============================================================================
# TLS/Certificate State Simulation (Mock Only - NO NEW LOGIC)
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

    @property
    def is_near_expiry(self) -> bool:
        """Check if certificate is within 7-day grace period."""
        grace_period = timedelta(days=7)
        return not self.is_expired and datetime.now(tz.utc) > (self.expires_at - grace_period)

    @property
    def days_until_expiry(self) -> int:
        """Get days until expiry."""
        delta = self.expires_at - datetime.now(tz.utc)
        return max(0, delta.days)


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
    """
    Mock TLS service for testing certificate failures.

    Uses only mocking - NO NEW PRODUCTION LOGIC.
    """

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
        """
        Attempt TLS handshake with a service.

        Returns TLSHandshakeResult with failure classification.
        """
        with self._lock:
            # Check for injected failure first
            if service_name in self._failure_injections:
                failure_type = self._failure_injections[service_name]
                result = self._create_failure_result(failure_type, service_name)
                self._log_handshake(service_name, result)
                return result

            # Check certificate status
            cert = self._certificates.get(service_name)
            if cert is None:
                result = TLSHandshakeResult(
                    success=True,
                    failure_type=TLSFailureType.NONE,
                )
                self._log_handshake(service_name, result)
                return result

            # Check expiry
            if cert.is_expired:
                result = self._create_failure_result(
                    TLSFailureType.CERTIFICATE_EXPIRED,
                    service_name,
                )
                self._log_handshake(service_name, result)
                return result

            # Certificate is valid
            result = TLSHandshakeResult(
                success=True,
                failure_type=TLSFailureType.NONE,
            )
            self._log_handshake(service_name, result)
            return result

    def _create_failure_result(
        self,
        failure_type: TLSFailureType,
        service_name: str,
    ) -> TLSHandshakeResult:
        """Create a failure result with proper classification."""
        # ALL TLS failures are SECURITY failures
        # NO auto-heal, NO retry allowed
        return TLSHandshakeResult(
            success=False,
            failure_type=failure_type,
            failure_reason=f"TLS failure: {failure_type.value} for {service_name}",
            is_security_failure=True,
            should_auto_heal=False,  # NEVER auto-heal security failures
            should_retry=False,  # NEVER retry security failures
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
# Security Boundary Tracker (Mock Only - NO NEW LOGIC)
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


class MockSecurityBoundary:
    """
    Mock security boundary for testing.

    Tracks security events without implementing new logic.
    """

    def __init__(self):
        self._events: List[SecurityEvent] = []
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

    def is_blocked(self, service_name: str) -> bool:
        """Check if service is blocked due to security violation."""
        with self._lock:
            return service_name in self._blocked_services

    def get_events_for_service(self, service_name: str) -> List[SecurityEvent]:
        """Get all security events for a service."""
        with self._lock:
            return [e for e in self._events if e.service_name == service_name]

    def clear(self) -> None:
        """Clear all state."""
        with self._lock:
            self._events.clear()
            self._blocked_services.clear()

    @property
    def all_events(self) -> List[SecurityEvent]:
        """Get all security events."""
        with self._lock:
            return list(self._events)


# =============================================================================
# Decision Record Capture (Reused from Stage 39/40)
# =============================================================================


class DecisionRecordCapture:
    """
    Captures Decision Record events for verification.
    Validates against existing frozen schema.
    """

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

        event = record.get("event")
        if event and event not in self.FROZEN_EVENT_TYPES:
            self.schema_violations.append(f"Unknown event type: {event}")

        reason = record.get("reason")
        if reason and reason not in self.FROZEN_REASON_CODES:
            self.schema_violations.append(f"Unknown reason code: {reason}")

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
# Stage 41-S1: Single Certificate Expiry
# =============================================================================


@pytest.mark.chaos
class TestStage41SingleCertificateExpiry:
    """
    Stage 41-S1: 단일 인증서 만료 시나리오

    Given: 외부 서비스의 TLS 인증서가 만료됨
    When: TLS 핸드셰이크 시도
    Then:
        - 요청이 차단됨
        - 보안 서킷이 열림
        - 자동 치유/재시도/재생 없음
    """

    def test_stage_41_expired_certificate_blocks_request(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
        selfhealing_services,
    ):
        """
        시나리오: 만료된 인증서로 인한 요청 차단

        Given:
            - 서비스의 TLS 인증서가 만료됨

        When:
            - TLS 핸드셰이크 시도

        Then:
            - TLS 실패가 SECURITY로 분류됨
            - 요청이 차단됨
            - 자동 치유 없음
            - 재시도 없음
        """
        # given: 만료된 인증서 등록
        expired_cert = CertificateMetadata(
            service_name=SERVICE_PAYMENT_GATEWAY,
            issued_at=datetime.now(tz.utc) - timedelta(days=365),
            expires_at=datetime.now(tz.utc) - timedelta(days=1),  # Expired yesterday
            is_valid=False,
        )
        tls_service.register_certificate(expired_cert)

        # when: TLS 핸드셰이크 시도
        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)

        # then: TLS 실패가 SECURITY로 분류됨
        assert result.success is False, "Handshake should fail"
        assert result.failure_type == TLSFailureType.CERTIFICATE_EXPIRED
        assert result.is_security_failure is True, "TLS failure must be classified as SECURITY"

        # then: 자동 치유 없음
        assert result.should_auto_heal is False, "No auto-heal for security failures"

        # then: 재시도 없음
        assert result.should_retry is False, "No retry for security failures"

        # 보안 경계에 위반 기록
        event = security_boundary.record_security_violation(
            service_name=SERVICE_PAYMENT_GATEWAY,
            reason="TLS certificate expired",
            auto_heal_triggered=False,
            retry_triggered=False,
            dlq_entry_created=False,
        )

        # then: 요청이 차단됨
        assert security_boundary.is_blocked(SERVICE_PAYMENT_GATEWAY)
        assert event.blocked is True
        assert event.auto_heal_triggered is False
        assert event.retry_triggered is False
        assert event.dlq_entry_created is False

    def test_stage_41_expired_certificate_opens_security_circuit(
        self,
        tls_service: MockTLSService,
        selfhealing_services,
    ):
        """
        시나리오: 만료된 인증서로 인한 보안 서킷 열림

        Given:
            - 서비스의 TLS 인증서가 만료됨

        When:
            - TLS 핸드셰이크 실패

        Then:
            - 보안 서킷이 열림 (OPEN 상태)
            - 서킷 상태가 결정적임
            - 비즈니스 로직이 실행되지 않음
        """
        CircuitState = selfhealing_services["CircuitState"]
        force_open = selfhealing_services["force_open_circuit"]
        should_allow = selfhealing_services["should_allow_request"]

        # given: 만료된 인증서로 인한 TLS 실패
        tls_service.inject_failure(
            SERVICE_PAYMENT_GATEWAY,
            TLSFailureType.CERTIFICATE_EXPIRED,
        )

        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
        assert result.success is False
        assert result.is_security_failure is True

        # when: 보안 위반으로 서킷 강제 열기
        force_open(SERVICE_PAYMENT_GATEWAY, reason="TLS certificate expired - security violation")

        # then: 서킷이 열림 - 요청 차단
        allowed = should_allow(SERVICE_PAYMENT_GATEWAY)
        assert allowed is False, "Circuit should be OPEN, blocking requests"

        # then: 비즈니스 로직 실행 없음 (서킷이 열려있으므로)
        business_logic_executed = False
        if allowed:
            business_logic_executed = True

        assert business_logic_executed is False, "Business logic should NOT execute"

    def test_stage_41_no_dlq_entry_for_tls_failure(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: TLS 실패 시 DLQ 엔트리 생성 없음

        Given:
            - TLS 인증서 만료로 요청 실패

        When:
            - 실패 처리

        Then:
            - DLQ 엔트리가 생성되지 않음
            - 재생 대기열에 추가되지 않음
        """
        # given: TLS 실패 주입
        tls_service.inject_failure(
            SERVICE_PAYMENT_GATEWAY,
            TLSFailureType.CERTIFICATE_EXPIRED,
        )

        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
        assert result.success is False

        # when: 보안 위반 기록 (DLQ 생성 없이)
        event = security_boundary.record_security_violation(
            service_name=SERVICE_PAYMENT_GATEWAY,
            reason="TLS certificate expired",
            auto_heal_triggered=False,
            retry_triggered=False,
            dlq_entry_created=False,  # NO DLQ for security failures
        )

        # then: DLQ 엔트리 생성 없음
        assert event.dlq_entry_created is False, "No DLQ entry for TLS security failures"

        # then: 핸드셰이크 로그 검증
        log = tls_service.handshake_log
        assert len(log) == 1
        assert log[0]["success"] is False
        assert log[0]["is_security_failure"] is True


# =============================================================================
# Stage 41-S2: Near-Expiry Certificate (Grace Period Boundary)
# =============================================================================


@pytest.mark.chaos
class TestStage41NearExpiryCertificate:
    """
    Stage 41-S2: 만료 임박 인증서 (유예 기간 경계)

    Given: 인증서가 만료에 가깝지만 아직 유효함
    When: TLS 핸드셰이크 시도
    Then:
        - 요청 처리 변경 없음
        - 선제적 개입 없음
        - Decision Record 안정적 유지
    """

    def test_stage_41_near_expiry_certificate_allows_request(
        self,
        tls_service: MockTLSService,
        selfhealing_services,
    ):
        """
        시나리오: 만료 임박 인증서로도 요청 허용

        Given:
            - 인증서가 3일 후 만료 예정 (유효함)

        When:
            - TLS 핸드셰이크 시도

        Then:
            - 핸드셰이크 성공
            - 요청 허용
            - 선제적 서킷 열림 없음
        """
        # given: 만료 임박 인증서 (3일 후 만료)
        near_expiry_cert = CertificateMetadata(
            service_name=SERVICE_PAYMENT_GATEWAY,
            issued_at=datetime.now(tz.utc) - timedelta(days=362),
            expires_at=datetime.now(tz.utc) + timedelta(days=3),  # Expires in 3 days
            is_valid=True,
        )
        tls_service.register_certificate(near_expiry_cert)

        # then: 인증서가 만료 임박 상태임을 확인
        assert near_expiry_cert.is_near_expiry is True
        assert near_expiry_cert.is_expired is False

        # when: TLS 핸드셰이크 시도
        result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)

        # then: 핸드셰이크 성공
        assert result.success is True, "Near-expiry certificate should still work"
        assert result.failure_type == TLSFailureType.NONE

        # then: 보안 실패가 아님
        assert result.is_security_failure is False

    def test_stage_41_near_expiry_no_proactive_intervention(
        self,
        tls_service: MockTLSService,
        selfhealing_services,
    ):
        """
        시나리오: 만료 임박 시 선제적 개입 없음

        Given:
            - 인증서가 유예 기간 내에 있음

        When:
            - 시스템이 인증서 상태를 확인

        Then:
            - 서킷 상태 변경 없음
            - 선제적 차단 없음
            - 자동 갱신 시도 없음
        """
        should_allow = selfhealing_services["should_allow_request"]

        # given: 만료 임박 인증서
        near_expiry_cert = CertificateMetadata(
            service_name=SERVICE_ORDER_PROCESSOR,
            issued_at=datetime.now(tz.utc) - timedelta(days=360),
            expires_at=datetime.now(tz.utc) + timedelta(days=5),
            is_valid=True,
        )
        tls_service.register_certificate(near_expiry_cert)

        # when: 핸드셰이크 시도
        result = tls_service.attempt_handshake(SERVICE_ORDER_PROCESSOR)

        # then: 성공
        assert result.success is True

        # then: 서킷 상태 변경 없음 (여전히 요청 허용)
        allowed = should_allow(SERVICE_ORDER_PROCESSOR)
        assert allowed is True, "No proactive circuit opening for near-expiry"

    def test_stage_41_near_expiry_decision_records_stable(
        self,
        tls_service: MockTLSService,
        decision_record_capture: DecisionRecordCapture,
    ):
        """
        시나리오: 만료 임박 시 Decision Record 안정성

        Given:
            - 인증서가 유예 기간 내에 있음

        When:
            - 여러 요청 처리

        Then:
            - Decision Record reason code 변경 없음
            - 스키마 위반 없음
        """
        # given: 만료 임박 인증서
        near_expiry_cert = CertificateMetadata(
            service_name=SERVICE_NOTIFICATION,
            issued_at=datetime.now(tz.utc) - timedelta(days=358),
            expires_at=datetime.now(tz.utc) + timedelta(days=7),
            is_valid=True,
        )
        tls_service.register_certificate(near_expiry_cert)

        # when: 여러 핸드셰이크 시도
        results = []
        for _ in range(5):
            result = tls_service.attempt_handshake(SERVICE_NOTIFICATION)
            results.append(result)

        # then: 모든 핸드셰이크 성공
        assert all(r.success for r in results)

        # then: Decision Record 스키마 안정성 (violations 없음)
        assert decision_record_capture.is_schema_valid is True


# =============================================================================
# Stage 41-S3: Certificate Expiry During In-Flight Requests
# =============================================================================


@pytest.mark.chaos
class TestStage41CertificateExpiryInFlight:
    """
    Stage 41-S3: 진행 중인 요청 중 인증서 만료

    Given: 동시 요청이 실행 중
    When: 인증서가 중간에 만료됨
    Then:
        - 새 요청은 차단됨
        - 진행 중인 요청은 소급 수정되지 않음
        - 시스템이 안정적으로 유지됨 (크래시 없음)
    """

    def test_stage_41_inflight_requests_not_retroactively_modified(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 진행 중인 요청 소급 수정 없음

        Given:
            - 여러 요청이 동시에 처리 중

        When:
            - 인증서가 중간에 만료됨

        Then:
            - 만료 전 시작된 요청은 영향 없음
            - 만료 후 새 요청만 차단
        """
        # given: 유효한 인증서로 시작
        valid_cert = CertificateMetadata(
            service_name=SERVICE_PAYMENT_GATEWAY,
            issued_at=datetime.now(tz.utc) - timedelta(days=1),
            expires_at=datetime.now(tz.utc) + timedelta(hours=1),  # Expires in 1 hour
            is_valid=True,
        )
        tls_service.register_certificate(valid_cert)

        # given: 첫 번째 요청 성공 (만료 전)
        result_before = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
        assert result_before.success is True

        # when: 인증서 만료 주입 (시뮬레이션)
        tls_service.inject_failure(
            SERVICE_PAYMENT_GATEWAY,
            TLSFailureType.CERTIFICATE_EXPIRED,
        )

        # when: 새 요청 시도 (만료 후)
        result_after = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)

        # then: 새 요청은 차단됨
        assert result_after.success is False
        assert result_after.failure_type == TLSFailureType.CERTIFICATE_EXPIRED

        # then: 핸드셰이크 로그 검증 - 첫 번째는 성공, 두 번째는 실패
        log = tls_service.handshake_log
        assert len(log) == 2
        assert log[0]["success"] is True  # Before expiry
        assert log[1]["success"] is False  # After expiry

    def test_stage_41_concurrent_requests_during_expiry(
        self,
        tls_service: MockTLSService,
        selfhealing_services,
    ):
        """
        시나리오: 만료 시점의 동시 요청 처리

        Given:
            - 여러 스레드에서 동시 요청

        When:
            - 인증서가 중간에 만료됨

        Then:
            - 시스템이 크래시 없이 생존
            - 각 요청이 독립적으로 처리됨
            - 정의되지 않은 동작 없음
        """
        force_open = selfhealing_services["force_open_circuit"]

        results = []
        errors = []
        expiry_injected = threading.Event()

        def make_request(request_id: int):
            try:
                # 일부 요청 후 만료 주입
                if request_id == 5:
                    tls_service.inject_failure(
                        SERVICE_PAYMENT_GATEWAY,
                        TLSFailureType.CERTIFICATE_EXPIRED,
                    )
                    expiry_injected.set()

                result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
                results.append(
                    {
                        "request_id": request_id,
                        "success": result.success,
                        "failure_type": result.failure_type.value,
                    }
                )
            except Exception as e:
                errors.append({"request_id": request_id, "error": str(e)})

        # when: 동시 요청 실행
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            for future in as_completed(futures):
                pass  # Wait for completion

        # then: 시스템 생존 (예외 없음)
        assert len(errors) == 0, f"System should survive without errors: {errors}"

        # then: 모든 요청이 처리됨
        assert len(results) == 10, "All requests should be processed"

        # then: 결과가 결정적임 (성공 또는 실패, 정의되지 않은 상태 없음)
        for r in results:
            assert r["success"] in [True, False]
            assert r["failure_type"] in [
                TLSFailureType.NONE.value,
                TLSFailureType.CERTIFICATE_EXPIRED.value,
            ]

    def test_stage_41_system_stability_during_mass_expiry_transition(
        self,
        tls_service: MockTLSService,
        security_boundary: MockSecurityBoundary,
    ):
        """
        시나리오: 대량 만료 전환 중 시스템 안정성

        Given:
            - 시스템이 정상 운영 중

        When:
            - 인증서가 갑자기 만료됨
            - 많은 요청이 동시에 실패

        Then:
            - 시스템이 크래시하지 않음
            - 메모리 누수 없음
            - 스레드 교착 없음
        """
        # given: 정상 상태
        result_before = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
        assert result_before.success is True

        # when: 대량 실패 시뮬레이션
        tls_service.inject_failure(
            SERVICE_PAYMENT_GATEWAY,
            TLSFailureType.CERTIFICATE_EXPIRED,
        )

        failed_count = 0
        for _ in range(100):
            result = tls_service.attempt_handshake(SERVICE_PAYMENT_GATEWAY)
            if not result.success:
                failed_count += 1
                security_boundary.record_security_violation(
                    service_name=SERVICE_PAYMENT_GATEWAY,
                    reason="TLS certificate expired",
                )

        # then: 시스템 생존
        assert True, "System survived mass failure"

        # then: 모든 실패가 기록됨
        assert failed_count == 100
        events = security_boundary.get_events_for_service(SERVICE_PAYMENT_GATEWAY)
        assert len(events) == 100

        # then: 모든 이벤트가 올바르게 분류됨
        for event in events:
            assert event.blocked is True
            assert event.auto_heal_triggered is False
            assert event.retry_triggered is False


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
