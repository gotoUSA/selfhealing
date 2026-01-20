"""
Test Constants.

테스트에서 사용하는 상수들을 중앙에서 관리합니다.
하드코딩 제거 및 일관성 유지를 위해 사용합니다.

Usage:
    from tests.factories.constants import DefaultValues, Domains, Services
    
    # 클래스 속성으로 접근
    domain = DefaultValues.DOMAIN_PAYMENT
    service = DefaultValues.SERVICE_TEST
    
    # 또는 개별 클래스 사용
    domain = Domains.PAYMENT
    service = Services.TEST
"""


class Domains:
    """도메인 상수."""
    ORDER = "order"
    PAYMENT = "payment"
    NOTIFICATION = "notification"
    EXTERNAL = "external_service"
    POINT = "point"
    SHIPPING = "shipping"
    WEBHOOK = "webhook"


class Services:
    """서비스 이름 상수."""
    PAYMENT_API = "payment-api"
    EXTERNAL_GATEWAY = "external-gateway"
    ORDER_SERVICE = "order-service"
    TEST = "test_service"
    TOSS_PAYMENTS = "toss-payments"
    NOTIFICATION = "notification-service"


class FailureTypes:
    """실패 유형 상수."""
    NETWORK = "network"
    TIMEOUT = "timeout"
    PG_TIMEOUT = "PG_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Status:
    """상태 상수."""
    PENDING = "pending"
    RESOLVED = "resolved"
    ARCHIVED = "archived"
    FAILED = "failed"


class CircuitState:
    """Circuit Breaker 상태 상수."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DefaultValues:
    """
    테스트에서 사용하는 기본값 상수 (통합).
    
    개별 클래스(Domains, Services 등)를 사용하거나,
    이 클래스에서 모든 상수에 접근할 수 있습니다.
    """
    
    # 도메인 관련 (Domains 클래스와 동일)
    DOMAIN_ORDER = Domains.ORDER
    DOMAIN_PAYMENT = Domains.PAYMENT
    DOMAIN_NOTIFICATION = Domains.NOTIFICATION
    DOMAIN_EXTERNAL = Domains.EXTERNAL
    
    # 서비스 관련 (Services 클래스와 동일)
    SERVICE_PAYMENT_API = Services.PAYMENT_API
    SERVICE_EXTERNAL_GATEWAY = Services.EXTERNAL_GATEWAY
    SERVICE_ORDER_SERVICE = Services.ORDER_SERVICE
    SERVICE_TEST = Services.TEST
    
    # 실패 타입 (FailureTypes 클래스와 동일)
    FAILURE_NETWORK = FailureTypes.NETWORK
    FAILURE_TIMEOUT = FailureTypes.TIMEOUT
    FAILURE_PG_TIMEOUT = FailureTypes.PG_TIMEOUT
    
    # 상태 (Status 클래스와 동일)
    STATUS_PENDING = Status.PENDING
    STATUS_RESOLVED = Status.RESOLVED
    STATUS_ARCHIVED = Status.ARCHIVED
    
    # Circuit Breaker (CircuitState 클래스와 동일)
    CB_STATE_CLOSED = CircuitState.CLOSED
    CB_STATE_OPEN = CircuitState.OPEN
    CB_STATE_HALF_OPEN = CircuitState.HALF_OPEN
    
    # 기본 설정값
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_FAILURE_THRESHOLD = 5
    DEFAULT_RECOVERY_TIMEOUT = 60
    DEFAULT_SUCCESS_THRESHOLD = 2
