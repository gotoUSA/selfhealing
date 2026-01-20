"""
Builder Pattern for Test Data.

복잡한 테스트 객체 생성을 위한 Builder 클래스들입니다.
Fluent API 스타일로 체이닝하여 사용합니다.

Usage:
    # Circuit Breaker State Builder
    state = (CircuitBreakerStateBuilder()
        .with_service("payment-api")
        .opened()
        .with_failure_count(5)
        .controlled_by(user_id=1, reason="Maintenance")
        .build())
    
    # Failed Operation Builder
    entry = (FailedOperationBuilder()
        .payment_domain()
        .pg_timeout()
        .pending()
        .with_retries(2, max_retries=5)
        .build())
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock

from tests.factories.constants import (
    Domains,
    Services,
    FailureTypes,
    Status,
    CircuitState,
)
from tests.factories.data_factory import (
    MockCircuitBreakerStateData,
    MockFailedOperationData,
    MockCanaryRolloutData,
)


class CircuitBreakerStateBuilder:
    """
    Circuit Breaker State Builder.
    
    체이닝 방식으로 CB 상태 객체를 생성합니다.
    
    Usage:
        state = (CircuitBreakerStateBuilder()
            .with_service("payment-api")
            .opened()
            .with_failure_count(5)
            .build())
    """
    
    def __init__(self):
        self._service_name = Services.TEST
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: Optional[datetime] = None
        self._opened_by_id: Optional[int] = None
        self._opened_reason = ""
        self._manually_controlled = False
        self._controlled_by_id: Optional[int] = None
        self._control_reason = ""
        self._half_open_request_count = 0
        self._manual_override_expires_at: Optional[datetime] = None
    
    def with_service(self, name: str) -> "CircuitBreakerStateBuilder":
        """서비스 이름 설정."""
        self._service_name = name
        return self
    
    def payment_service(self) -> "CircuitBreakerStateBuilder":
        """결제 서비스로 설정."""
        return self.with_service(Services.PAYMENT_API)
    
    def external_gateway(self) -> "CircuitBreakerStateBuilder":
        """외부 게이트웨이로 설정."""
        return self.with_service(Services.EXTERNAL_GATEWAY)
    
    def toss_payments(self) -> "CircuitBreakerStateBuilder":
        """토스페이먼츠로 설정."""
        return self.with_service(Services.TOSS_PAYMENTS)
    
    def closed(self) -> "CircuitBreakerStateBuilder":
        """Closed 상태로 설정."""
        self._state = CircuitState.CLOSED
        self._opened_at = None
        return self
    
    def opened(self, at: Optional[datetime] = None) -> "CircuitBreakerStateBuilder":
        """Open 상태로 설정."""
        self._state = CircuitState.OPEN
        self._opened_at = at or datetime.now(timezone.utc)
        return self
    
    def half_open(self, request_count: int = 0) -> "CircuitBreakerStateBuilder":
        """Half-Open 상태로 설정."""
        self._state = CircuitState.HALF_OPEN
        self._half_open_request_count = request_count
        return self
    
    def with_failure_count(self, count: int) -> "CircuitBreakerStateBuilder":
        """실패 횟수 설정."""
        self._failure_count = count
        return self
    
    def with_success_count(self, count: int) -> "CircuitBreakerStateBuilder":
        """성공 횟수 설정."""
        self._success_count = count
        return self
    
    def controlled_by(
        self,
        user_id: int,
        reason: str = "Manual control",
        expires_in_minutes: Optional[int] = None,
    ) -> "CircuitBreakerStateBuilder":
        """수동 제어 상태로 설정."""
        self._manually_controlled = True
        self._controlled_by_id = user_id
        self._opened_by_id = user_id
        self._control_reason = reason
        self._opened_reason = reason
        
        if expires_in_minutes:
            self._manual_override_expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
            )
        
        return self
    
    def build(self) -> MockCircuitBreakerStateData:
        """객체 생성."""
        return MockCircuitBreakerStateData(
            service_name=self._service_name,
            state=self._state,
            failure_count=self._failure_count,
            success_count=self._success_count,
            opened_at=self._opened_at,
            opened_by_id=self._opened_by_id,
            opened_reason=self._opened_reason,
            half_open_request_count=self._half_open_request_count,
            manually_controlled=self._manually_controlled,
            controlled_by_id=self._controlled_by_id,
            control_reason=self._control_reason,
            manual_override_expires_at=self._manual_override_expires_at,
        )


class FailedOperationBuilder:
    """
    Failed Operation (DLQ) Builder.
    
    체이닝 방식으로 DLQ 엔트리 객체를 생성합니다.
    """
    
    def __init__(self):
        self._id = 1
        self._domain = Domains.PAYMENT
        self._failure_type = FailureTypes.PG_TIMEOUT
        self._status = Status.PENDING
        self._error_message = "Test error"
        self._retry_count = 0
        self._max_retries = 3
        self._context: Dict[str, Any] = {}
        self._created_at: Optional[datetime] = None
    
    def with_id(self, id: int) -> "FailedOperationBuilder":
        """ID 설정."""
        self._id = id
        return self
    
    def payment_domain(self) -> "FailedOperationBuilder":
        """결제 도메인으로 설정."""
        self._domain = Domains.PAYMENT
        return self
    
    def order_domain(self) -> "FailedOperationBuilder":
        """주문 도메인으로 설정."""
        self._domain = Domains.ORDER
        return self
    
    def notification_domain(self) -> "FailedOperationBuilder":
        """알림 도메인으로 설정."""
        self._domain = Domains.NOTIFICATION
        return self
    
    def webhook_domain(self) -> "FailedOperationBuilder":
        """웹훅 도메인으로 설정."""
        self._domain = Domains.WEBHOOK
        return self
    
    def with_domain(self, domain: str) -> "FailedOperationBuilder":
        """도메인 설정."""
        self._domain = domain
        return self
    
    def pg_timeout(self) -> "FailedOperationBuilder":
        """PG 타임아웃 오류로 설정."""
        self._failure_type = FailureTypes.PG_TIMEOUT
        self._error_message = "PG API timeout exceeded"
        return self
    
    def network_error(self) -> "FailedOperationBuilder":
        """네트워크 오류로 설정."""
        self._failure_type = FailureTypes.NETWORK_ERROR
        self._error_message = "Network connection failed"
        return self
    
    def amount_mismatch(self) -> "FailedOperationBuilder":
        """금액 불일치 오류로 설정."""
        self._failure_type = FailureTypes.AMOUNT_MISMATCH
        self._error_message = "Amount mismatch detected"
        return self
    
    def with_failure_type(self, failure_type: str, message: str = "Test error") -> "FailedOperationBuilder":
        """실패 유형 설정."""
        self._failure_type = failure_type
        self._error_message = message
        return self
    
    def pending(self) -> "FailedOperationBuilder":
        """대기 상태로 설정."""
        self._status = Status.PENDING
        return self
    
    def resolved(self) -> "FailedOperationBuilder":
        """해결됨 상태로 설정."""
        self._status = Status.RESOLVED
        return self
    
    def failed(self) -> "FailedOperationBuilder":
        """실패 상태로 설정."""
        self._status = Status.FAILED
        return self
    
    def archived(self) -> "FailedOperationBuilder":
        """보관됨 상태로 설정."""
        self._status = Status.ARCHIVED
        return self
    
    def with_retries(self, count: int, max_retries: int = 3) -> "FailedOperationBuilder":
        """재시도 횟수 설정."""
        self._retry_count = count
        self._max_retries = max_retries
        return self
    
    def with_context(self, **kwargs) -> "FailedOperationBuilder":
        """컨텍스트 정보 추가."""
        self._context.update(kwargs)
        return self
    
    def created_at(self, dt: datetime) -> "FailedOperationBuilder":
        """생성 시각 설정."""
        self._created_at = dt
        return self
    
    def created_days_ago(self, days: int) -> "FailedOperationBuilder":
        """N일 전에 생성된 것으로 설정."""
        self._created_at = datetime.now(timezone.utc) - timedelta(days=days)
        return self
    
    def build(self) -> MockFailedOperationData:
        """객체 생성."""
        return MockFailedOperationData(
            id=self._id,
            domain=self._domain,
            failure_type=self._failure_type,
            status=self._status,
            error_message=self._error_message,
            retry_count=self._retry_count,
            max_retries=self._max_retries,
            context=self._context,
            created_at=self._created_at,
        )


class CanaryRolloutBuilder:
    """
    Canary Rollout Builder.
    
    체이닝 방식으로 Canary 롤아웃 객체를 생성합니다.
    """
    
    def __init__(self):
        self._id = "rollout-test-001"
        self._config_type = "circuit_breaker"
        self._state = "created"
        self._current_stage_index = 0
        self._new_values: Dict[str, Any] = {}
        self._created_by = "test@example.com"
        self._reason = "Test rollout"
        self._stages: List[Dict[str, Any]] = []
    
    def with_id(self, id: str) -> "CanaryRolloutBuilder":
        """ID 설정."""
        self._id = id
        return self
    
    def circuit_breaker_config(self) -> "CanaryRolloutBuilder":
        """CB 설정 타입으로 지정."""
        self._config_type = "circuit_breaker"
        return self
    
    def dlq_config(self) -> "CanaryRolloutBuilder":
        """DLQ 설정 타입으로 지정."""
        self._config_type = "dlq"
        return self
    
    def with_new_values(self, **kwargs) -> "CanaryRolloutBuilder":
        """새 설정 값 지정."""
        self._new_values.update(kwargs)
        return self
    
    def created(self) -> "CanaryRolloutBuilder":
        """생성됨 상태로 설정."""
        self._state = "created"
        return self
    
    def running(self, stage_index: int = 0) -> "CanaryRolloutBuilder":
        """실행 중 상태로 설정."""
        self._state = "running"
        self._current_stage_index = stage_index
        return self
    
    def completed(self) -> "CanaryRolloutBuilder":
        """완료 상태로 설정."""
        self._state = "completed"
        return self
    
    def failed(self) -> "CanaryRolloutBuilder":
        """실패 상태로 설정."""
        self._state = "failed"
        return self
    
    def created_by(self, email: str) -> "CanaryRolloutBuilder":
        """생성자 설정."""
        self._created_by = email
        return self
    
    def with_reason(self, reason: str) -> "CanaryRolloutBuilder":
        """사유 설정."""
        self._reason = reason
        return self
    
    def with_default_stages(self) -> "CanaryRolloutBuilder":
        """기본 스테이지 설정 (canary → regional → global)."""
        self._stages = [
            {
                "name": "canary",
                "clusters": ["seoul-canary"],
                "percentage": 10.0,
                "duration_minutes": 5,
                "auto_promote": True,
            },
            {
                "name": "regional",
                "clusters": ["seoul-main", "tokyo-main"],
                "percentage": 50.0,
                "duration_minutes": 10,
            },
            {
                "name": "global",
                "clusters": ["seoul-main", "tokyo-main", "singapore-main"],
                "percentage": 100.0,
                "duration_minutes": 0,
            },
        ]
        return self
    
    def build(self) -> MockCanaryRolloutData:
        """객체 생성."""
        return MockCanaryRolloutData(
            id=self._id,
            config_type=self._config_type,
            state=self._state,
            current_stage_index=self._current_stage_index,
            new_values=self._new_values,
            created_by=self._created_by,
            reason=self._reason,
        )


class MockServiceBuilder:
    """
    Mock Service Builder.
    
    테스트용 Mock 서비스 객체를 생성합니다.
    """
    
    def __init__(self):
        self._mock = MagicMock()
    
    def with_method(self, name: str, return_value: Any = None) -> "MockServiceBuilder":
        """메서드와 반환값 설정."""
        getattr(self._mock, name).return_value = return_value
        return self
    
    def with_async_method(self, name: str, return_value: Any = None) -> "MockServiceBuilder":
        """비동기 메서드와 반환값 설정."""
        async def async_return():
            return return_value
        getattr(self._mock, name).return_value = async_return()
        return self
    
    def with_side_effect(self, name: str, side_effect: Any) -> "MockServiceBuilder":
        """메서드에 side_effect 설정."""
        getattr(self._mock, name).side_effect = side_effect
        return self
    
    def with_property(self, name: str, value: Any) -> "MockServiceBuilder":
        """프로퍼티 값 설정."""
        setattr(self._mock, name, value)
        return self
    
    def build(self) -> MagicMock:
        """Mock 객체 반환."""
        return self._mock
