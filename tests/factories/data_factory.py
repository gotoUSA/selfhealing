"""
Test Data Factories.

테스트용 데이터 객체를 생성하는 Factory 클래스입니다.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from unittest.mock import Mock

from tests.factories.constants import (
    Domains,
    Services,
    FailureTypes,
    Status,
    CircuitState,
)


@dataclass
class MockCircuitBreakerStateData:
    """
    테스트용 Mock Circuit Breaker State Data.
    
    실제 CircuitBreakerStateData와 동일한 인터페이스를 제공하되,
    간소화된 테스트용 구현입니다.
    """
    service_name: str
    state: str = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    opened_by_id: Optional[int] = None
    opened_reason: str = ""
    half_open_request_count: int = 0
    last_failure_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    manually_controlled: bool = False
    controlled_by_id: Optional[int] = None
    control_reason: str = ""
    manual_override_expires_at: Optional[datetime] = None


@dataclass
class MockFailedOperationData:
    """
    테스트용 Mock Failed Operation (DLQ) Data.
    """
    id: int = 1
    domain: str = Domains.PAYMENT
    failure_type: str = FailureTypes.PG_TIMEOUT
    status: str = Status.PENDING
    error_message: str = "Test error"
    retry_count: int = 0
    max_retries: int = 3
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = self.created_at


@dataclass
class MockCanaryRolloutData:
    """
    테스트용 Mock Canary Rollout Data.
    """
    id: str = "rollout-test-001"
    config_type: str = "circuit_breaker"
    state: str = "created"
    current_stage_index: int = 0
    new_values: Dict[str, Any] = field(default_factory=dict)
    created_by: str = "test@example.com"
    reason: str = "Test rollout"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = self.created_at


class TestDataFactory:
    """
    테스트 데이터 생성 Factory.
    
    모든 테스트 데이터 생성을 중앙화하여 일관성 유지.
    
    Usage:
        # Circuit Breaker 상태 데이터
        state = TestDataFactory.circuit_breaker_state(
            service_name="payment-api",
            state="open"
        )
        
        # Failed Operation 데이터
        entry = TestDataFactory.failed_operation(
            domain="order",
            failure_type="network"
        )
    """
    
    @staticmethod
    def circuit_breaker_state(
        service_name: str = Services.TEST,
        state: str = CircuitState.CLOSED,
        failure_count: int = 0,
        success_count: int = 0,
        opened_at: Optional[datetime] = None,
        opened_by_id: Optional[int] = None,
        opened_reason: str = "",
        **kwargs,
    ) -> MockCircuitBreakerStateData:
        """Circuit Breaker 상태 데이터 생성."""
        return MockCircuitBreakerStateData(
            service_name=service_name,
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            opened_at=opened_at,
            opened_by_id=opened_by_id,
            opened_reason=opened_reason,
            **kwargs,
        )
    
    @staticmethod
    def failed_operation(
        id: int = 1,
        domain: str = Domains.PAYMENT,
        failure_type: str = FailureTypes.PG_TIMEOUT,
        status: str = Status.PENDING,
        error_message: str = "Test error",
        retry_count: int = 0,
        **kwargs,
    ) -> MockFailedOperationData:
        """Failed Operation (DLQ) 데이터 생성."""
        return MockFailedOperationData(
            id=id,
            domain=domain,
            failure_type=failure_type,
            status=status,
            error_message=error_message,
            retry_count=retry_count,
            **kwargs,
        )
    
    @staticmethod
    def failed_operations(
        count: int = 5,
        domain: str = Domains.PAYMENT,
        status: str = Status.PENDING,
    ) -> List[MockFailedOperationData]:
        """여러 Failed Operation 데이터 생성."""
        return [
            TestDataFactory.failed_operation(
                id=i + 1,
                domain=domain,
                status=status,
            )
            for i in range(count)
        ]
    
    @staticmethod
    def canary_rollout(
        id: str = "rollout-test-001",
        config_type: str = "circuit_breaker",
        state: str = "created",
        **kwargs,
    ) -> MockCanaryRolloutData:
        """Canary Rollout 데이터 생성."""
        return MockCanaryRolloutData(
            id=id,
            config_type=config_type,
            state=state,
            **kwargs,
        )
    
    @staticmethod
    def toss_payment_response(
        status: str = "DONE",
        payment_key: str = "test_payment_key_123",
        order_id: str = "ORDER_123",
        amount: int = 10000,
        approved_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Toss Payment API 응답 데이터 생성."""
        if approved_at is None:
            approved_at = datetime.now(timezone.utc).isoformat()
        
        return {
            "paymentKey": payment_key,
            "orderId": order_id,
            "status": status,
            "amount": amount,
            "approvedAt": approved_at,
        }
    
    @staticmethod
    def webhook_event(
        event_type: str = "PAYMENT_COMPLETED",
        payment_key: str = "test_payment_key_123",
        order_id: str = "ORDER_123",
        **kwargs,
    ) -> Dict[str, Any]:
        """Webhook 이벤트 데이터 생성."""
        return {
            "eventType": event_type,
            "paymentKey": payment_key,
            "orderId": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
    
    @staticmethod
    def celery_task_result(
        task_id: str = "test-task-id-12345",
        state: str = "SUCCESS",
        result: Any = None,
    ) -> Mock:
        """Celery Task Result Mock 생성."""
        mock_result = Mock()
        mock_result.id = task_id
        mock_result.state = state
        mock_result.result = result
        mock_result.ready.return_value = state in ["SUCCESS", "FAILURE"]
        mock_result.successful.return_value = state == "SUCCESS"
        mock_result.failed.return_value = state == "FAILURE"
        return mock_result
