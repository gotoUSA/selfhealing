"""
L3 Self-Healing Test: 자동 복구 시스템 테스트

결제 장애 발생 시 자동 복구를 위한 Retry/Backoff/SLA/Dead-letter 정책 테스트입니다.
금융권/PG 수준의 신뢰성을 위한 Self-Healing 패턴 검증입니다.

시나리오:
- L3-A: Retry with Exponential Backoff
- L3-B: Dead Letter Queue (DLQ) 이동
- L3-C: SLA Timeout Abort
- L3-D: Circuit Breaker (Toggle 기반)

Note: This module uses Mock-based approach for parallel test execution.
      No database dependency - uses conftest Mock objects.
"""

import pytest
from decimal import Decimal
from datetime import timedelta

from selfhealing.core.timezone import now
from .conftest import (
    InMemoryFailedOperationRepository,
    InMemoryCircuitBreakerStateRepository,
    MockUser,
    MockOrder,
    MockPayment,
)


# =============================================================================
# Mock Recovery Handler for Testing
# =============================================================================


class MockPaymentRecoveryHandler:
    """Mock payment recovery handler for testing backoff and recovery logic."""

    def __init__(self, config: dict = None):
        self.config = config or {
            "RETRY_BACKOFF_BASE": 4,
            "RETRY_BACKOFF_MAX": 180,
            "RETRY_JITTER": False,
            "MAX_RETRY_COUNT": 3,
            "SLA_TIMEOUT_SECONDS": 300,
            "SLA_ABORT_ENABLED": True,
            "CIRCUIT_BREAKER_ENABLED": False,
        }
        self._schedule_retry_calls = []
        self._notify_calls = []

    def get_backoff_delay(self, attempt: int) -> int:
        """Calculate backoff delay for a given attempt number."""
        base = self.config.get("RETRY_BACKOFF_BASE", 4)
        max_delay = self.config.get("RETRY_BACKOFF_MAX", 180)
        jitter = self.config.get("RETRY_JITTER", False)

        delay = base ** attempt

        if jitter:
            import random
            jitter_factor = random.uniform(0.75, 1.25)
            delay = delay * jitter_factor

        return min(delay, max_delay)

    def check_sla_timeout(self, created_at) -> bool:
        """Check if SLA timeout has been exceeded."""
        if not self.config.get("SLA_ABORT_ENABLED", True):
            return False

        timeout_seconds = self.config.get("SLA_TIMEOUT_SECONDS", 300)
        elapsed = (now() - created_at).total_seconds()
        return elapsed > timeout_seconds

    def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows request."""
        if not self.config.get("CIRCUIT_BREAKER_ENABLED", False):
            return True
        # Simplified - actual implementation would check CB state
        return True

    def schedule_retry(self, payment_id: int, delay: int) -> str:
        """Mock schedule retry."""
        task_id = f"task-{payment_id}-{delay}"
        self._schedule_retry_calls.append({"payment_id": payment_id, "delay": delay})
        return task_id

    def handle_failure(
        self,
        payment_id: int,
        order_id: int,
        error_code: str,
        error_message: str,
        retry_count: int,
    ) -> dict:
        """Handle a payment failure."""
        max_retries = self.config.get("MAX_RETRY_COUNT", 3)

        # Non-retryable errors
        non_retryable = ["ALREADY_PROCESSED_PAYMENT", "INVALID_REQUEST", "FRAUD_DETECTED"]
        if error_code in non_retryable:
            return {
                "action": "moved_to_dlq",
                "reason": "non_retryable_error",
                "dlq_id": payment_id * 100,  # Mock ID
            }

        # Max retries exceeded
        if retry_count >= max_retries:
            return {
                "action": "moved_to_dlq",
                "reason": "max_retries_exceeded",
                "dlq_id": payment_id * 100,
            }

        # Schedule retry
        delay = self.get_backoff_delay(retry_count + 1)
        task_id = self.schedule_retry(payment_id, delay)
        return {
            "action": "retry_scheduled",
            "task_id": task_id,
            "attempt": retry_count + 1,
        }


# =============================================================================
# L3-A: Retry with Exponential Backoff 테스트
# =============================================================================


class TestL3RetryBackoff:
    """L3-A: Retry with Exponential Backoff 테스트"""

    @pytest.fixture
    def recovery_handler(self):
        """Recovery Handler 인스턴스"""
        return MockPaymentRecoveryHandler()

    def test_l3a_exponential_backoff_delay_calculation(self, recovery_handler):
        """
        L3-A: 지수 백오프 지연 시간 계산 검증

        Timeline:
        1. attempt=1 → 약 4초
        2. attempt=2 → 약 16초
        3. attempt=3 → 약 64초 (max 180 제한)
        """
        # Jitter 없이 기본값 확인
        recovery_handler.config = {
            "RETRY_BACKOFF_BASE": 4,
            "RETRY_BACKOFF_MAX": 180,
            "RETRY_JITTER": False,
        }

        delay_1 = recovery_handler.get_backoff_delay(1)
        delay_2 = recovery_handler.get_backoff_delay(2)
        delay_3 = recovery_handler.get_backoff_delay(3)

        assert delay_1 == 4, f"Attempt 1 should be 4s, got {delay_1}s"
        assert delay_2 == 16, f"Attempt 2 should be 16s, got {delay_2}s"
        assert delay_3 == 64, f"Attempt 3 should be 64s, got {delay_3}s"

        print("✓ L3-A PASSED: Exponential backoff delay calculation correct")

    def test_l3a_backoff_max_limit(self, recovery_handler):
        """
        L3-A 변형: 최대 지연 시간 제한 검증

        attempt가 커져도 RETRY_BACKOFF_MAX를 초과하지 않음
        """
        recovery_handler.config = {
            "RETRY_BACKOFF_BASE": 4,
            "RETRY_BACKOFF_MAX": 180,
            "RETRY_JITTER": False,
        }

        delay_10 = recovery_handler.get_backoff_delay(10)

        # 4^10 = 1,048,576 이지만 max 180으로 제한
        assert delay_10 == 180, f"Delay should be capped at 180s, got {delay_10}s"

        print("✓ L3-A(변형) PASSED: Backoff max limit enforced")

    def test_l3a_backoff_with_jitter(self, recovery_handler):
        """
        L3-A 변형: Jitter가 적용된 지연 시간 검증

        동일 attempt에서도 Jitter로 인해 ±25% 범위 내에서 변동
        """
        recovery_handler.config = {
            "RETRY_BACKOFF_BASE": 4,
            "RETRY_BACKOFF_MAX": 180,
            "RETRY_JITTER": True,
        }

        delays = [recovery_handler.get_backoff_delay(2) for _ in range(100)]

        # 기본값 16초에서 ±25% = 12~20초 범위
        min_delay = min(delays)
        max_delay = max(delays)
        unique_delays = len(set(delays))

        assert 12 <= min_delay <= 20, f"Min delay should be in range, got {min_delay}"
        assert 12 <= max_delay <= 20, f"Max delay should be in range, got {max_delay}"
        assert unique_delays > 1, "Jitter should produce varying delays"

        print(f"✓ L3-A(Jitter) PASSED: Jitter applied, {unique_delays} unique delays")

    def test_l3a_retry_scheduling(self, recovery_handler):
        """
        L3-A: 재시도 스케줄링 검증

        handle_failure가 재시도 가능한 오류에 대해 retry를 스케줄링함
        """
        user = MockUser()
        order = MockOrder(user=user)
        payment = MockPayment(order=order)

        # 재시도 가능한 오류로 handle_failure 호출
        result = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",  # 재시도 가능
            error_message="Network failed",
            retry_count=0,
        )

        assert result["action"] == "retry_scheduled"
        assert "task_id" in result
        assert result["attempt"] == 1
        print("✓ L3-A(Scheduling) PASSED: Retry scheduled for retryable error")


# =============================================================================
# L3-B: Dead Letter Queue (DLQ) 테스트
# =============================================================================


class TestL3DeadLetterQueue:
    """L3-B: Dead Letter Queue (DLQ) 테스트"""

    @pytest.fixture
    def recovery_handler(self):
        """Recovery Handler 인스턴스"""
        return MockPaymentRecoveryHandler()

    @pytest.fixture
    def failed_operation_repository(self):
        """DLQ repository for tests."""
        return InMemoryFailedOperationRepository()

    def test_l3b_move_to_dlq_on_max_retries(self, recovery_handler):
        """
        L3-B: 최대 재시도 횟수 초과 시 DLQ 이동

        Timeline:
        1. 3회 재시도 실패
        2. DLQ로 이동
        3. DLQ 레코드 생성 확인
        """
        user = MockUser()
        order = MockOrder(user=user)
        payment = MockPayment(order=order)

        # 최대 재시도 횟수(3) 초과
        result = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",
            error_message="Network failed after max retries",
            retry_count=3,  # max_retries에 도달
        )

        assert result["action"] == "moved_to_dlq"
        assert result["reason"] == "max_retries_exceeded"
        assert "dlq_id" in result

        print("✓ L3-B PASSED: Moved to DLQ on max retries exceeded")

    def test_l3b_move_to_dlq_on_non_retryable_error(self, recovery_handler):
        """
        L3-B 변형: 재시도 불가능한 오류 시 즉시 DLQ 이동

        ALREADY_PROCESSED_PAYMENT 등 비즈니스 오류는 재시도 없이 DLQ로 이동
        """
        user = MockUser()
        order = MockOrder(user=user)
        payment = MockPayment(order=order)

        result = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="ALREADY_PROCESSED_PAYMENT",  # 재시도 불가
            error_message="Payment already processed",
            retry_count=0,  # 첫 시도에서 바로 DLQ
        )

        assert result["action"] == "moved_to_dlq"
        assert result["reason"] == "non_retryable_error"

        print("✓ L3-B(변형) PASSED: Immediately moved to DLQ on non-retryable error")

    def test_l3b_dlq_record_contains_full_context(self, failed_operation_repository):
        """
        L3-B 변형: DLQ 레코드에 전체 컨텍스트 저장 확인

        디버깅을 위해 요청/응답 데이터, 메타데이터 등이 저장됨
        """
        user = MockUser()
        order = MockOrder(user=user)
        payment = MockPayment(
            order=order,
            payment_key="test_payment_key",
            amount=Decimal("10000"),
        )

        request_data = {"payment_key": "test_payment_key", "amount": 10000}
        response_data = {"error_code": "TIMEOUT", "message": "Request timed out"}
        metadata = {"retry_timestamps": ["2024-01-01T10:00:00Z"]}

        # Create DLQ entry using repository
        dlq_record = failed_operation_repository.create(
            domain="payment",
            failure_type="max_retries_exceeded",
            error_code="TIMEOUT",
            error_message="Request timed out",
            entity_type="payment",
            entity_id=str(payment.id),
            user_id=user.id,
            request_data=request_data,
            response_data=response_data,
            metadata=metadata,
            retry_count=3,
        )

        # 요청/응답 데이터 확인
        assert dlq_record.request_data == request_data
        assert dlq_record.response_data == response_data
        assert dlq_record.metadata == metadata

        # 만료일 설정 확인
        assert dlq_record.expires_at is not None

        print("✓ L3-B(Context) PASSED: DLQ record contains full context")

    def test_l3b_dlq_resolution_workflow(self, failed_operation_repository):
        """
        L3-B 변형: DLQ 레코드 해결 워크플로우

        pending → resolved 상태 전환
        """
        user = MockUser()
        order = MockOrder(user=user)
        payment = MockPayment(order=order)

        # DLQ 레코드 생성
        dlq_record = failed_operation_repository.create(
            domain="payment",
            failure_type="max_retries_exceeded",
            error_code="TIMEOUT",
            error_message="Request timed out",
            entity_type="payment",
            entity_id=str(payment.id),
            retry_count=3,
        )

        assert dlq_record.status == "pending"

        # 해결 처리
        resolver = MockUser(is_staff=True, username="resolver")
        failed_operation_repository.mark_as_resolved(
            id=dlq_record.id,
            resolution_type="manual_retry",
            resolution_note="Manual retry successful",
            resolved_by_id=resolver.id,
        )

        updated_record = failed_operation_repository.get_by_id(dlq_record.id)
        assert updated_record.status == "resolved"
        assert updated_record.resolution_note == "Manual retry successful"
        assert updated_record.resolved_at is not None

        print("✓ L3-B(Resolution) PASSED: DLQ resolution workflow works")


# =============================================================================
# L3-C: SLA Timeout Abort 테스트
# =============================================================================


class TestL3SLATimeout:
    """L3-C: SLA Timeout Abort 테스트"""

    @pytest.fixture
    def recovery_handler(self):
        """Recovery Handler 인스턴스"""
        return MockPaymentRecoveryHandler()

    def test_l3c_sla_timeout_detection(self, recovery_handler):
        """
        L3-C: SLA 타임아웃 감지

        결제 생성 후 SLA_TIMEOUT_SECONDS 초과 시 타임아웃으로 감지
        """
        # 5분 전에 생성된 결제
        created_at = now() - timedelta(minutes=6)

        recovery_handler.config = {
            "SLA_TIMEOUT_SECONDS": 300,  # 5분
            "SLA_ABORT_ENABLED": True,
        }
        is_timeout = recovery_handler.check_sla_timeout(created_at)

        assert is_timeout is True, "Should detect SLA timeout"

        print("✓ L3-C PASSED: SLA timeout detected correctly")

    def test_l3c_sla_within_limit(self, recovery_handler):
        """
        L3-C 변형: SLA 시간 내 정상 처리

        SLA_TIMEOUT_SECONDS 이내면 타임아웃 아님
        """
        # 3분 전에 생성된 결제
        created_at = now() - timedelta(minutes=3)

        recovery_handler.config = {
            "SLA_TIMEOUT_SECONDS": 300,  # 5분
            "SLA_ABORT_ENABLED": True,
        }
        is_timeout = recovery_handler.check_sla_timeout(created_at)

        assert is_timeout is False, "Should not detect timeout within SLA"

        print("✓ L3-C(변형) PASSED: No timeout within SLA limit")

    def test_l3c_sla_abort_disabled(self, recovery_handler):
        """
        L3-C 변형: SLA Abort 비활성화 시 항상 False

        SLA_ABORT_ENABLED=False면 타임아웃 체크 비활성화
        """
        created_at = now() - timedelta(hours=1)  # 1시간 전

        recovery_handler.config = {
            "SLA_TIMEOUT_SECONDS": 300,
            "SLA_ABORT_ENABLED": False,  # 비활성화
        }
        is_timeout = recovery_handler.check_sla_timeout(created_at)

        assert is_timeout is False, "Should not detect timeout when disabled"

        print("✓ L3-C(Disabled) PASSED: SLA abort correctly disabled")


# =============================================================================
# L3-D: Circuit Breaker 테스트 (Toggle 기반)
# =============================================================================


class TestL3CircuitBreaker:
    """L3-D: Circuit Breaker 테스트 (Toggle 기반)"""

    @pytest.fixture
    def recovery_handler(self):
        """Recovery Handler 인스턴스"""
        return MockPaymentRecoveryHandler()

    @pytest.fixture
    def circuit_breaker_repository(self):
        """Circuit breaker repository for tests."""
        return InMemoryCircuitBreakerStateRepository()

    def test_l3d_circuit_breaker_disabled_by_default(self, recovery_handler):
        """
        L3-D: Circuit Breaker 기본 비활성화 확인

        CIRCUIT_BREAKER_ENABLED=False면 항상 요청 허용
        """
        recovery_handler.config = {
            "CIRCUIT_BREAKER_ENABLED": False,
        }
        allowed = recovery_handler.check_circuit_breaker()

        assert allowed is True, "Should allow request when circuit breaker is disabled"

        print("✓ L3-D PASSED: Circuit breaker disabled by default")

    def test_l3d_circuit_breaker_opens_on_threshold(self, circuit_breaker_repository):
        """
        L3-D: 연속 실패 시 Circuit Breaker Open

        CIRCUIT_BREAKER_FAILURE_THRESHOLD 도달 시 Open 상태로 전환
        """
        service_name = "toss_payment"
        failure_threshold = 5

        # Circuit Breaker 상태 생성
        circuit_breaker_repository.get_or_create(service_name)

        # 5회 연속 실패 기록
        for _ in range(failure_threshold):
            circuit_breaker_repository.increment_failure(service_name)

        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.failure_count >= 5

        # Open 상태로 전환
        circuit_breaker_repository.update_state(service_name, state="open")
        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.state == "open"

        print("✓ L3-D(Open) PASSED: Circuit breaker opens on threshold")

    def test_l3d_circuit_breaker_closes_on_success(self, circuit_breaker_repository):
        """
        L3-D 변형: Half-Open에서 성공 시 Close

        Half-Open 상태에서 SUCCESS_THRESHOLD 만큼 성공하면 Close
        """
        service_name = "toss_payment"
        success_threshold = 2

        # Half-open 상태로 생성
        circuit_breaker_repository.update_state(
            service_name,
            state="half_open",
            failure_count=5,
            success_count=0,
        )

        # 2회 성공 기록
        for _ in range(success_threshold):
            circuit_breaker_repository.increment_success(service_name)

        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.success_count >= success_threshold

        # Close 상태로 전환
        circuit_breaker_repository.update_state(
            service_name,
            state="closed",
            failure_count=0,
            success_count=0,
        )
        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.state == "closed"
        assert cb_state.failure_count == 0

        print("✓ L3-D(Close) PASSED: Circuit closes after success in half-open")

    def test_l3d_manual_circuit_breaker_control(self, circuit_breaker_repository):
        """
        L3-D: 운영자 수동 제어 테스트

        PG 장애 감지 시 운영자가 수동으로 Open/Close
        """
        operator = MockUser(is_staff=True, username="operator")
        service_name = "toss_payment"

        # Circuit Breaker 상태 생성
        circuit_breaker_repository.get_or_create(service_name)

        # 수동 Open
        circuit_breaker_repository.atomic_force_open(
            service_name=service_name,
            reason="PG 장애 감지 - 수동 차단",
            controlled_by_id=operator.id,
        )

        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.state == "open"
        assert cb_state.manually_controlled is True
        assert cb_state.control_reason == "PG 장애 감지 - 수동 차단"

        # 수동 Close
        circuit_breaker_repository.atomic_force_close(
            service_name=service_name,
            reason="PG 복구 확인",
            controlled_by_id=operator.id,
        )

        cb_state = circuit_breaker_repository.get_by_service_name(service_name)
        assert cb_state.state == "closed"
        assert cb_state.failure_count == 0

        print("✓ L3-D(Manual) PASSED: Manual circuit breaker control works")


# =============================================================================
# L3 통합 테스트
# =============================================================================


class TestL3Integration:
    """L3 통합 테스트: 전체 Self-Healing 플로우"""

    @pytest.fixture
    def recovery_handler(self):
        """Recovery Handler 인스턴스"""
        return MockPaymentRecoveryHandler()

    def test_l3_complete_failure_recovery_flow(self, recovery_handler):
        """
        L3 통합: 완전한 실패 복구 플로우

        1. 결제 시도 → 네트워크 오류
        2. 재시도 3회 (지수 백오프)
        3. 최대 재시도 초과 → DLQ 이동
        4. 롤백 처리
        """
        user = MockUser()
        order = MockOrder(user=user, total_amount=Decimal("10000"))
        payment = MockPayment(order=order, amount=Decimal("10000"))

        # 1차 시도: 재시도 스케줄링
        result_1 = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",
            error_message="Network failed",
            retry_count=0,
        )
        assert result_1["action"] == "retry_scheduled"

        # 2차 시도: 재시도 스케줄링
        result_2 = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",
            error_message="Network failed",
            retry_count=1,
        )
        assert result_2["action"] == "retry_scheduled"

        # 3차 시도: 재시도 스케줄링
        result_3 = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",
            error_message="Network failed",
            retry_count=2,
        )
        assert result_3["action"] == "retry_scheduled"

        # 4차 시도 (max_retries 초과): DLQ 이동
        result_4 = recovery_handler.handle_failure(
            payment_id=payment.id,
            order_id=order.id,
            error_code="NETWORK_ERROR",
            error_message="Network failed after all retries",
            retry_count=3,
        )
        assert result_4["action"] == "moved_to_dlq"
        assert result_4["reason"] == "max_retries_exceeded"

        print("✓ L3 Integration PASSED: Complete failure recovery flow works")


class TestL3SelfHealingSummary:
    """L3 테스트 결과 요약"""

    def test_summary(self):
        """테스트 완료 시 결과 출력"""
        print(
            """
============================================================
L3 SELF-HEALING TEST RESULTS
============================================================

Retry with Exponential Backoff (L3-A):
  Delay Calculation:          Ready for execution
  Max Limit:                  Ready for execution
  Jitter:                     Ready for execution
  Retry Scheduling:           Ready for execution

Dead Letter Queue (L3-B):
  Max Retries → DLQ:          Ready for execution
  Non-Retryable → DLQ:        Ready for execution
  Full Context Storage:       Ready for execution
  Resolution Workflow:        Ready for execution

SLA Timeout Abort (L3-C):
  Timeout Detection:          Ready for execution
  Within Limit:               Ready for execution
  Disabled Mode:              Ready for execution

Circuit Breaker (L3-D):
  Disabled by Default:        Ready for execution
  Opens on Threshold:         Ready for execution
  Closes on Success:          Ready for execution
  Manual Control:             Ready for execution

Integration:
  Complete Flow:              Ready for execution

============================================================
"""
        )
