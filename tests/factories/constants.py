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


class CanaryCluster:
    """
    Canary 클러스터 이름 상수.
    
    Canary 롤아웃 테스트에서 사용하는 클러스터 식별자입니다.
    실제 배포 환경의 클러스터 구조를 반영합니다.
    """
    # 카나리 전용 (초기 10% 트래픽)
    SEOUL_CANARY = "seoul-canary"
    
    # 메인 클러스터 (리전별)
    SEOUL_MAIN = "seoul-main"
    TOKYO_MAIN = "tokyo-main"
    SINGAPORE_MAIN = "singapore-main"
    
    # 클러스터 그룹 (단계별 롤아웃용)
    CANARY_ONLY = [SEOUL_CANARY]
    REGIONAL = [SEOUL_MAIN, TOKYO_MAIN]
    GLOBAL = [SEOUL_MAIN, TOKYO_MAIN, SINGAPORE_MAIN]


class CanaryPercentage:
    """
    Canary 단계별 트래픽 비율 상수.
    
    각 롤아웃 단계에서 새 설정이 적용되는 트래픽 비율입니다.
    """
    INITIAL = 10.0    # 초기 카나리 (10%)
    HALF = 50.0       # 리전 확장 (50%)
    FULL = 100.0      # 전체 적용 (100%)


class ChaosIntensity:
    """
    Chaos 실험 강도 상수.
    
    장애 주입 비율 및 지속 시간을 정의합니다.
    """
    # 주입 비율 (injection_rate)
    LOW_RATE = 0.001       # 0.1%
    MEDIUM_RATE = 0.01     # 1%
    HIGH_RATE = 0.05       # 5%
    EXTREME_RATE = 0.1     # 10%
    
    # 지속 시간 (duration_seconds)
    SHORT_DURATION = 60           # 1분
    MEDIUM_DURATION = 300         # 5분
    LONG_DURATION = 600           # 10분
    EXTENDED_DURATION = 1800      # 30분


class RBACRole:
    """
    RBAC 역할 상수.
    
    Self-Healing API 접근 제어에 사용되는 역할 그룹입니다.
    """
    VIEWER = "selfhealing_viewer"
    OPERATOR = "selfhealing_operator"
    ADMIN = "selfhealing_admin"
    
    # 역할 그룹 리스트
    ALL_ROLES = [VIEWER, OPERATOR, ADMIN]
    ELEVATED_ROLES = [OPERATOR, ADMIN]


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
