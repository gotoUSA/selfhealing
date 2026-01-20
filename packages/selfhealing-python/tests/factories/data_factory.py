"""
Test Data Factory.

테스트용 데이터 객체를 생성하는 Factory 클래스입니다.
각 메서드는 기본값을 제공하면서 커스터마이징 가능한 테스트 데이터를 생성합니다.

하드코딩된 값들을 중앙에서 관리하여:
- 도메인/서비스명 변경 시 한 곳만 수정
- 테스트 데이터 일관성 보장
- 새 테스트 작성 시간 단축
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from unittest.mock import Mock

from selfhealing.core.types import (
    FailedOperationData,
    CircuitBreakerStateData,
    FailureType,
    OperationStatus,
    CircuitState,
)

# 상수는 constants.py에서 관리
from tests.factories.constants import DefaultValues


@dataclass
class MockCircuitBreakerStateData:
    """
    테스트용 Mock Circuit Breaker State Data.
    
    실제 CircuitBreakerStateData와 동일한 인터페이스를 제공하되,
    간소화된 테스트용 구현입니다.
    """
    service_name: str
    state: str = DefaultValues.CB_STATE_CLOSED
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
        
        # Mock 객체로 생성
        mock_entry = TestDataFactory.mock_failed_operation(id=1)
    """
    
    # =========================================================================
    # Circuit Breaker 관련
    # =========================================================================
    
    @staticmethod
    def circuit_breaker_state(
        service_name: str = DefaultValues.SERVICE_TEST,
        state: str = DefaultValues.CB_STATE_CLOSED,
        failure_count: int = 0,
        success_count: int = 0,
        opened_at: Optional[datetime] = None,
        opened_by_id: Optional[int] = None,
        opened_reason: str = "",
        **kwargs,
    ) -> MockCircuitBreakerStateData:
        """
        Circuit Breaker 상태 데이터 생성.
        
        Args:
            service_name: 서비스 이름
            state: CB 상태 (closed, open, half_open)
            failure_count: 실패 횟수
            success_count: 성공 횟수
            opened_at: CB가 open된 시간
            opened_by_id: open한 사용자 ID
            opened_reason: open 사유
            **kwargs: 추가 필드
            
        Returns:
            MockCircuitBreakerStateData 인스턴스
        """
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
    def circuit_breaker_state_data(
        service_name: str = DefaultValues.SERVICE_TEST,
        state: str = DefaultValues.CB_STATE_CLOSED,
        failure_count: int = 0,
        success_count: int = 0,
        **kwargs,
    ) -> CircuitBreakerStateData:
        """
        실제 CircuitBreakerStateData 객체 생성.
        
        실제 타입이 필요한 경우 사용.
        """
        return CircuitBreakerStateData(
            service_name=service_name,
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            **kwargs,
        )
    
    # =========================================================================
    # Failed Operation (DLQ) 관련
    # =========================================================================
    
    @staticmethod
    def failed_operation(
        id: int = 1,
        domain: str = DefaultValues.DOMAIN_ORDER,
        failure_type: str = DefaultValues.FAILURE_NETWORK,
        status: str = DefaultValues.STATUS_PENDING,
        retry_count: int = 0,
        max_retries: int = DefaultValues.DEFAULT_MAX_RETRIES,
        created_at: Optional[datetime] = None,
        error_message: str = "Connection timeout",
        context: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> FailedOperationData:
        """
        Failed Operation 데이터 생성.
        
        Args:
            id: 엔트리 ID
            domain: 비즈니스 도메인
            failure_type: 실패 유형
            status: 상태
            retry_count: 재시도 횟수
            max_retries: 최대 재시도 횟수
            created_at: 생성 시간
            error_message: 에러 메시지
            context: 컨텍스트 데이터
            **kwargs: 추가 필드
            
        Returns:
            FailedOperationData 인스턴스
        """
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        
        if context is None:
            context = {"order_id": 123, "amount": 10000}
        
        return FailedOperationData(
            id=id,
            domain=domain,
            failure_type=failure_type,
            status=status,
            created_at=created_at,
            context=context,
            error_message=error_message,
            retry_count=retry_count,
            max_retries=max_retries,
            **kwargs,
        )
    
    @staticmethod
    def mock_failed_operation(
        id: int = 1,
        domain: str = DefaultValues.DOMAIN_PAYMENT,
        failure_type: str = DefaultValues.FAILURE_PG_TIMEOUT,
        status: str = DefaultValues.STATUS_PENDING,
        retry_count: int = 0,
        max_retries: int = DefaultValues.DEFAULT_MAX_RETRIES,
        **kwargs,
    ) -> Mock:
        """
        Mock으로 FailedOperationData 생성.
        
        spec을 사용하여 인터페이스 준수.
        DLQ 테스트에서 주로 사용.
        
        Args:
            id: 엔트리 ID
            domain: 비즈니스 도메인
            failure_type: 실패 유형
            status: 상태
            retry_count: 재시도 횟수
            max_retries: 최대 재시도 횟수
            
        Returns:
            Mock 객체 (FailedOperationData spec)
        """
        entry = Mock(spec=FailedOperationData)
        entry.id = id
        entry.domain = domain
        entry.failure_type = failure_type
        entry.status = status
        entry.retry_count = retry_count
        entry.max_retries = max_retries
        entry.created_at = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        entry.resolved_at = None
        entry.error_code = "TIMEOUT"
        entry.error_message = "Connection timed out"
        entry.snapshot_data = {"order_id": "order-123"}
        entry.request_data = {"method": "POST"}
        entry.response_data = {"status_code": 500}
        entry.metadata = {}
        # DLQ 추가 필드
        entry.entity_type = "order"
        entry.entity_id = "order-123"
        entry.resolution_note = ""
        
        # 추가 필드 설정
        for key, value in kwargs.items():
            setattr(entry, key, value)
        
        return entry
    
    # =========================================================================
    # 시간 관련 헬퍼
    # =========================================================================
    
    @staticmethod
    def now() -> datetime:
        """현재 UTC 시간 반환."""
        return datetime.now(timezone.utc)
    
    @staticmethod
    def past(seconds: int = 60) -> datetime:
        """과거 시간 반환."""
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    
    @staticmethod
    def future(seconds: int = 60) -> datetime:
        """미래 시간 반환."""
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
    
    # =========================================================================
    # Audit 관련
    # =========================================================================
    
    @staticmethod
    def audit_log_entry(
        event: str = "test_event",
        timestamp: Optional[datetime] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        감사 로그 엔트리 생성.
        
        Args:
            event: 이벤트 이름
            timestamp: 타임스탬프
            data: 추가 데이터
            **kwargs: 추가 필드
            
        Returns:
            감사 로그 딕셔너리
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if data is None:
            data = {"key": "value"}
        
        entry = {
            "event": event,
            "timestamp": timestamp.isoformat(),
            "data": data,
        }
        entry.update(kwargs)
        return entry
