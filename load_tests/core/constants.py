"""
Load Test 공통 상수

API 엔드포인트, 헤더, SLA 기준값, 부하 테스트 설정 등을 정의합니다.
"""
from typing import Dict, List


class Endpoints:
    """API 엔드포인트 상수."""
    
    # 인증
    AUTH_LOGIN = "/api/auth/login/"
    AUTH_LOGOUT = "/api/auth/logout/"
    AUTH_TOKEN_REFRESH = "/api/auth/token/refresh/"
    AUTH_REGISTER = "/api/auth/register/"
    
    # 상품
    PRODUCTS = "/api/products/"
    PRODUCTS_DETAIL = "/api/products/{id}/"
    PRODUCTS_SEARCH = "/api/products/search/"
    
    # 장바구니
    CART = "/api/cart/"
    CART_ADD = "/api/cart/add_item/"
    CART_REMOVE = "/api/cart/remove_item/"
    CART_CLEAR = "/api/cart/clear/"
    
    # 주문
    ORDERS = "/api/orders/"
    ORDERS_DETAIL = "/api/orders/{id}/"
    ORDERS_CANCEL = "/api/orders/{id}/cancel/"
    ORDERS_CONFIRM = "/api/orders/{id}/confirm/"
    
    # 결제
    PAYMENTS = "/api/payments/"
    PAYMENTS_PROCESS = "/api/payments/process/"
    PAYMENTS_CALLBACK = "/api/payments/callback/"
    
    # Self-Healing
    SH_BASE = "/api/self-healing"
    SH_HEALTH = f"{SH_BASE}/health/"
    SH_STATUS = f"{SH_BASE}/status/"
    
    # Self-Healing - Circuit Breaker
    SH_CB_STATUS = f"{SH_BASE}/circuit-breaker/status"
    SH_CB_STATUS_SERVICE = f"{SH_BASE}/circuit-breaker/status/{{service}}/"
    SH_CB_FORCE_OPEN = f"{SH_BASE}/circuit-breaker/force-open"
    SH_CB_FORCE_OPEN_SERVICE = f"{SH_BASE}/circuit-breaker/force-open/{{service}}/"
    SH_CB_RESET = f"{SH_BASE}/circuit-breaker/reset"
    SH_CB_RESET_SERVICE = f"{SH_BASE}/circuit-breaker/reset/{{service}}/"
    
    # Self-Healing - Emergency
    SH_EMERGENCY = f"{SH_BASE}/emergency/"
    SH_EMERGENCY_ESCALATE = f"{SH_BASE}/emergency/escalate/"
    SH_EMERGENCY_RECOVER = f"{SH_BASE}/emergency/recover/"
    SH_EMERGENCY_STATUS = f"{SH_BASE}/emergency/status/"
    
    # Self-Healing - DLQ
    SH_DLQ = f"{SH_BASE}/dlq/"
    SH_DLQ_STATS = f"{SH_BASE}/dlq/stats/"
    SH_DLQ_REPLAY = f"{SH_BASE}/dlq/replay/"
    SH_DLQ_PURGE = f"{SH_BASE}/dlq/purge/"
    
    # Self-Healing - Throttle
    SH_THROTTLE = f"{SH_BASE}/throttle/"
    SH_THROTTLE_STATUS = f"{SH_BASE}/throttle/status/"
    SH_THROTTLE_UPDATE = f"{SH_BASE}/throttle/update/"
    
    # XTest (Chaos Engineering)
    XTEST_BASE = "/xtest"
    XTEST_CB_INJECT = f"{XTEST_BASE}/chaos/inject-cb-failure"
    XTEST_CB_INJECT_SERVICE = f"{XTEST_BASE}/chaos/inject-cb-failure/{{service}}/"
    XTEST_LATENCY = f"{XTEST_BASE}/chaos/inject-latency"
    XTEST_LATENCY_SERVICE = f"{XTEST_BASE}/chaos/inject-latency/{{service}}/"
    XTEST_BLAST = f"{XTEST_BASE}/chaos/blast-radius-test"
    XTEST_ERROR = f"{XTEST_BASE}/chaos/inject-error"
    XTEST_RECOVERY = f"{XTEST_BASE}/chaos/trigger-recovery"
    
    # Webhook
    WEBHOOK_TOSS = "/api/webhook/toss/"
    WEBHOOK_PAYMENT = "/api/webhook/payment/"
    
    # Admin
    ADMIN_BASE = "/admin/"
    
    @classmethod
    def format(cls, endpoint: str, **kwargs) -> str:
        """
        엔드포인트 포맷팅.
        
        Args:
            endpoint: 포맷할 엔드포인트 (예: SH_CB_STATUS_SERVICE)
            **kwargs: 포맷 인자 (예: service="payment")
            
        Returns:
            포맷된 엔드포인트
            
        Example:
            >>> Endpoints.format(Endpoints.SH_CB_STATUS_SERVICE, service="payment")
            '/api/self-healing/circuit-breaker/status/payment/'
        """
        return endpoint.format(**kwargs)


class Headers:
    """HTTP 헤더 상수."""
    
    # 기본 헤더
    JSON: Dict[str, str] = {
        "Content-Type": "application/json"
    }
    
    FORM: Dict[str, str] = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    # X-Test-Mode 헤더
    XTEST_MODE: Dict[str, str] = {
        "X-Test-Mode": "chaos-monkey"
    }
    
    XTEST_LOAD: Dict[str, str] = {
        "X-Test-Mode": "load-test"
    }
    
    XTEST_EXTREME: Dict[str, str] = {
        "X-Test-Mode": "extreme-chaos"
    }
    
    # 조합 헤더
    XTEST_JSON: Dict[str, str] = {
        **XTEST_MODE,
        **JSON
    }
    
    XTEST_LOAD_JSON: Dict[str, str] = {
        **XTEST_LOAD,
        **JSON
    }
    
    @classmethod
    def with_auth(cls, token: str, base: Dict[str, str] = None) -> Dict[str, str]:
        """
        인증 헤더 추가.
        
        Args:
            token: JWT 토큰
            base: 기본 헤더 (없으면 JSON)
            
        Returns:
            인증 헤더가 추가된 딕셔너리
        """
        headers = dict(base or cls.JSON)
        headers["Authorization"] = f"Bearer {token}"
        return headers


class SLA:
    """SLA (Service Level Agreement) 기준값."""
    
    # 응답 시간 (밀리초)
    P99_THRESHOLD_MS: float = 300.0
    P95_THRESHOLD_MS: float = 200.0
    P90_THRESHOLD_MS: float = 150.0
    P50_THRESHOLD_MS: float = 100.0
    
    # 극한 테스트용 임계값
    CRITICAL_THRESHOLD_MS: float = 500.0
    AGGRESSIVE_THRESHOLD_MS: float = 300.0
    
    # 에러율 (퍼센트)
    ERROR_RATE_MAX: float = 5.0
    ERROR_RATE_CRITICAL: float = 10.0
    ERROR_RATE_WARNING: float = 1.0
    
    # 복구 시간 (초)
    RECOVERY_TIME_MAX_S: float = 120.0
    RECOVERY_TIME_TARGET_S: float = 60.0
    RECOVERY_TIME_IDEAL_S: float = 30.0
    
    # CB 관련
    CB_FAILURE_THRESHOLD: int = 5
    CB_RECOVERY_TIMEOUT_S: float = 30.0
    CB_HALF_OPEN_MAX_REQUESTS: int = 3
    
    # Throttle
    THROTTLE_AGGRESSIVE_CUT: float = 0.5
    THROTTLE_MIN_RATE: float = 10.0
    THROTTLE_MAX_RATE: float = 100.0
    
    # Jitter
    JITTER_BASE_MS: float = 100.0
    JITTER_MAX_MS: float = 5000.0
    JITTER_ESCALATION_FACTOR: float = 2.0
    
    @classmethod
    def is_p99_breach(cls, response_time_ms: float) -> bool:
        """P99 SLA 위반 여부."""
        return response_time_ms > cls.P99_THRESHOLD_MS
    
    @classmethod
    def is_p95_breach(cls, response_time_ms: float) -> bool:
        """P95 SLA 위반 여부."""
        return response_time_ms > cls.P95_THRESHOLD_MS
    
    @classmethod
    def is_critical(cls, response_time_ms: float) -> bool:
        """Critical 임계값 초과 여부."""
        return response_time_ms > cls.CRITICAL_THRESHOLD_MS
    
    @classmethod
    def is_error_rate_breach(cls, error_rate: float) -> bool:
        """에러율 SLA 위반 여부."""
        return error_rate > cls.ERROR_RATE_MAX


class LoadConfig:
    """부하 테스트 설정."""
    
    # 사용자 수
    USERS_SMOKE: int = 1
    USERS_LIGHT: int = 10
    USERS_NORMAL: int = 30
    USERS_HEAVY: int = 50
    USERS_SPIKE: int = 50
    USERS_EXTREME: int = 150
    USERS_HELLMODE: int = 300
    
    # 쿨다운 사용자
    COOL_USERS: int = 5
    
    # 테스트 시간 (초)
    DURATION_SMOKE_S: int = 30
    DURATION_SHORT_S: int = 60
    DURATION_NORMAL_S: int = 180
    DURATION_LONG_S: int = 600
    DURATION_ENDURANCE_S: int = 3600
    
    # 페이즈 시간 (초)
    RAMP_UP_S: int = 10
    SPIKE_DURATION_S: int = 30
    COOLDOWN_DURATION_S: int = 30
    CYCLE_DURATION_S: int = 30
    
    # 사이클 수
    NUM_CYCLES_DEFAULT: int = 1
    NUM_CYCLES_REPEATED: int = 3
    NUM_CYCLES_ENDURANCE: int = 5
    NUM_CYCLES_EXTREME: int = 10
    
    # 대기 시간 (초)
    WAIT_MIN_S: float = 0.5
    WAIT_MAX_S: float = 2.0
    WAIT_AGGRESSIVE_MIN_S: float = 0.1
    WAIT_AGGRESSIVE_MAX_S: float = 0.5
    
    # 재시도
    MAX_RETRIES: int = 3
    RETRY_DELAY_S: float = 1.0
    
    # 타임아웃 (초)
    REQUEST_TIMEOUT_S: float = 30.0
    CONNECTION_TIMEOUT_S: float = 10.0


class Services:
    """서비스 이름 상수."""
    
    DATABASE: str = "database"
    PAYMENT: str = "payment"
    CACHE: str = "cache"
    NOTIFICATION: str = "notification"
    EXTERNAL_API: str = "external_api"
    TOSS: str = "toss"
    
    # 전체 서비스 목록
    ALL: List[str] = [DATABASE, PAYMENT, CACHE, NOTIFICATION, EXTERNAL_API, TOSS]
    
    # CB 대상 서비스
    CB_SERVICES: List[str] = [DATABASE, PAYMENT, CACHE, EXTERNAL_API, TOSS]
    
    # Chaos 테스트 대상
    CHAOS_TARGETS: List[str] = [DATABASE, PAYMENT, CACHE]


class EmergencyLevels:
    """Emergency Level 상수."""
    
    NORMAL: int = 0
    WARNING: int = 1
    ELEVATED: int = 2
    HIGH: int = 3
    CRITICAL: int = 4
    LOCKDOWN: int = 5
    
    @classmethod
    def get_name(cls, level: int) -> str:
        """레벨 이름 반환."""
        names = {
            0: "NORMAL",
            1: "WARNING",
            2: "ELEVATED",
            3: "HIGH",
            4: "CRITICAL",
            5: "LOCKDOWN"
        }
        return names.get(level, f"UNKNOWN({level})")
    
    @classmethod
    def is_critical(cls, level: int) -> bool:
        """Critical 이상 여부."""
        return level >= cls.CRITICAL


class TestModes:
    """테스트 모드 상수."""
    
    SMOKE: str = "smoke"
    HAPPY_PATH: str = "happy_path"
    LOAD: str = "load"
    STRESS: str = "stress"
    SPIKE: str = "spike"
    ENDURANCE: str = "endurance"
    CHAOS: str = "chaos"
    EXTREME: str = "extreme"
    HELLMODE: str = "hellmode"
