"""
Test Constants.

전역 테스트에서 사용하는 상수들을 중앙에서 관리합니다.
하드코딩 제거 및 일관성 유지를 위해 사용합니다.

Usage:
    from tests.factories.constants import Domains, Services, Status
    
    domain = Domains.PAYMENT
    service = Services.PAYMENT_API
    status = Status.PENDING
"""

from dataclasses import dataclass
from decimal import Decimal


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
    CELERY_WORKER = "celery-worker"


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
    CONNECTION_ERROR = "CONNECTION_ERROR"
    REDIS_ERROR = "REDIS_ERROR"


class Status:
    """상태 상수."""
    PENDING = "pending"
    RESOLVED = "resolved"
    ARCHIVED = "archived"
    FAILED = "failed"
    SUCCESS = "success"
    RUNNING = "running"
    COMPLETED = "completed"


class CircuitState:
    """Circuit Breaker 상태 상수."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class OrderStatus:
    """주문 상태 상수."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    PAYMENT_FAILED = "payment_failed"


class PaymentStatus:
    """결제 상태 상수."""
    READY = "ready"
    PROCESSING = "processing"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class TestConstants:
    """
    테스트에서 사용하는 기본값 상수.
    
    금액, 포인트, 재고 등 테스트 공통 값.
    """
    
    # 금액
    DEFAULT_PRODUCT_PRICE: Decimal = Decimal("10000")
    DEFAULT_SHIPPING_FEE: Decimal = Decimal("3000")
    DEFAULT_TOTAL_AMOUNT: Decimal = Decimal("13000")
    FREE_SHIPPING_THRESHOLD: Decimal = Decimal("30000")
    REMOTE_AREA_FEE: Decimal = Decimal("3000")
    
    # 포인트
    DEFAULT_POINTS: int = 5000
    HIGH_POINTS: int = 50000
    DEFAULT_EARN_POINTS: int = 100
    
    # 재고
    DEFAULT_STOCK: int = 100
    LOW_STOCK: int = 1
    OUT_OF_STOCK: int = 0
    
    # 배송 정보
    DEFAULT_SHIPPING_NAME: str = "홍길동"
    DEFAULT_SHIPPING_PHONE: str = "010-1234-5678"
    DEFAULT_SHIPPING_POSTAL_CODE: str = "12345"
    DEFAULT_SHIPPING_ADDRESS: str = "서울시 강남구"
    DEFAULT_SHIPPING_ADDRESS_DETAIL: str = "101동"
    
    # 비밀번호
    DEFAULT_PASSWORD: str = "testpass123"
    
    # TTL 값
    IDEMPOTENCY_KEY_TTL: int = 60
    WEBHOOK_EVENT_TTL: int = 60
    CIRCUIT_BREAKER_TTL: int = 300


@dataclass(frozen=True)
class CeleryTestConfig:
    """Celery 테스트 설정."""
    
    TASK_ALWAYS_EAGER: bool = True
    TASK_EAGER_PROPAGATES: bool = True
    
    # 큐 이름
    DEFAULT_QUEUE: str = "default"
    PAYMENT_CRITICAL_QUEUE: str = "payment_critical"
    ORDER_PROCESSING_QUEUE: str = "order_processing"
    EXTERNAL_API_QUEUE: str = "external_api"
    POINTS_QUEUE: str = "points"
    NOTIFICATIONS_QUEUE: str = "notifications"
    
    # 태스크 재시도 설정
    DEFAULT_MAX_RETRIES: int = 3
    DEFAULT_RETRY_DELAY: int = 60


@dataclass(frozen=True)
class RedisTestConfig:
    """Redis 테스트 설정."""
    
    # Docker Compose 기본 포트
    DEFAULT_HOST: str = "localhost"
    DEFAULT_PORT: int = 6379
    TEST_PORT: int = 16379  # docker-compose.test.yml
    
    # 키 프리픽스
    KEY_PREFIX: str = "test:"
    CB_KEY_PREFIX: str = "test:selfhealing:cb:"
    DLQ_KEY_PREFIX: str = "test:selfhealing:dlq:"
    IDEMPOTENCY_KEY_PREFIX: str = "test:payment:idempotency:"
    
    # 테스트 DB
    DEFAULT_DB: int = 0
    TEST_DB: int = 15
    
    @property
    def redis_url(self) -> str:
        """기본 Redis URL."""
        return f"redis://{self.DEFAULT_HOST}:{self.DEFAULT_PORT}/{self.DEFAULT_DB}"
    
    @property
    def test_redis_url(self) -> str:
        """테스트용 Redis URL (docker-compose.test.yml)."""
        return f"redis://{self.DEFAULT_HOST}:{self.TEST_PORT}/{self.TEST_DB}"


@dataclass(frozen=True)
class DatabaseTestConfig:
    """Database 테스트 설정."""
    
    # Docker Compose 기본 설정
    DEFAULT_HOST: str = "localhost"
    DEFAULT_PORT: int = 5432
    DEFAULT_DB: str = "shopping_db"
    DEFAULT_USER: str = "shopping_user"
    DEFAULT_PASSWORD: str = "shopping_pass"
    
    @property
    def database_url(self) -> str:
        """기본 Database URL."""
        return (
            f"postgres://{self.DEFAULT_USER}:{self.DEFAULT_PASSWORD}"
            f"@{self.DEFAULT_HOST}:{self.DEFAULT_PORT}/{self.DEFAULT_DB}"
        )


# 싱글톤 인스턴스 (편의를 위해)
TEST_CONSTANTS = TestConstants()
CELERY_CONFIG = CeleryTestConfig()
REDIS_CONFIG = RedisTestConfig()
DB_CONFIG = DatabaseTestConfig()
