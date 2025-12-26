"""
SelfHealingClient - Self-Healing 시스템 통합 API 클라이언트.

이 패키지는 분리된 Self-Healing 시스템과 통신하기 위한
포괄적인 API 클라이언트를 제공합니다.

사용 예시:
---------
```python
from load_tests.utils.selfhealing import SelfHealingClient

# 기본 설정으로 클라이언트 생성 (JWT 인증)
client = SelfHealingClient()
client.login("admin", "password")

# 헬스 체크
health = client.health.ping()
print(f"Ping: {health}")

# Circuit Breaker 상태 확인
cb_status = client.circuit_breaker.get_status("payment-service")
print(f"CB Status: {cb_status}")

# XTest 모드로 장애 주입
client = SelfHealingClient(auth_mode="xtest")
client.circuit_breaker.xtest_inject_failure("payment-service")

# Error Budget 확인
budget = client.error_budget.get_status()
print(f"Remaining: {budget.get('remaining_percent')}%")

# Emergency 모드 활성화
client.emergency.trigger(level="LEVEL_1", reason="테스트")

# DLQ 확인
dlq_stats = client.dlq.stats()
print(f"Pending: {dlq_stats.get('pending_count')}")
```

환경변수:
--------
- SELFHEALING_HOST: Self-Healing 서버 호스트 (기본: http://localhost:8000)
- SELFHEALING_AUTH_MODE: 인증 모드 (jwt, xtest, none)
- SELFHEALING_TIMEOUT: 요청 타임아웃 (기본: 30초)
"""

from typing import Optional

from .config import SelfHealingConfig, configure, get_config, reset_config
from .base import BaseClient
from .auth import AuthClient
from .health import HealthClient
from .circuit_breaker import CircuitBreakerClient
from .dlq import DLQClient
from .error_budget import ErrorBudgetClient
from .emergency import EmergencyClient
from .observability import ObservabilityClient
from .chaos import ChaosClient
from .system import SystemClient
from .governance import GovernanceClient
from .rate_limiter import RateLimiterClient
from .alerts import AlertsClient
# 추가된 클라이언트들
from .runtime_config import RuntimeConfigClient
from .l2_storage import L2StorageClient
from .tiering import TieringClient
from .dashboard import DashboardClient
from .xtest import XTestClient
from .reconciliation import ReconciliationClient


class SelfHealingClient:
    """
    Self-Healing 시스템 통합 API 클라이언트.
    
    모든 Self-Healing API에 대한 단일 진입점을 제공합니다.
    
    Attributes:
        auth: 인증 관련 API (로그인, 토큰 관리)
        health: 헬스 체크 & 메트릭 API
        circuit_breaker: Circuit Breaker 제어 API
        dlq: Dead Letter Queue 관리 API
        error_budget: Error Budget 관리 API
        emergency: Emergency Mode 관리 API
        observability: 스냅샷, 타임라인, 포스트모템 API
        chaos: Chaos Engineering API
        system: 시스템 설정 & 진단 API
        governance: 거버넌스 & 감사 API
        rate_limiter: Rate Limiter 관리 API
        alerts: 알림 & 알림 채널 API
        runtime_config: 런타임 설정 관리 API
        l2_storage: L2 저장소 관리 API
        tiering: API Tiering 관리 API
        dashboard: 대시보드 & 시스템 제어 API
        xtest: XTest Mode (Chaos Monkey) API
        reconciliation: Shadow Budget 조정 API
    """
    
    def __init__(
        self,
        host: Optional[str] = None,
        auth_mode: Optional[str] = None,
        timeout: Optional[int] = None,
        config: Optional[SelfHealingConfig] = None,
    ):
        """
        SelfHealingClient 초기화.
        
        Args:
            host: Self-Healing 서버 호스트 (None이면 환경변수 사용)
            auth_mode: 인증 모드 - "jwt", "xtest", "none" (None이면 환경변수 사용)
            timeout: 요청 타임아웃 초 (None이면 환경변수 사용)
            config: SelfHealingConfig 인스턴스 (직접 설정 시)
        """
        # 설정 결정: 직접 전달 > 파라미터 > 환경변수
        if config:
            self._config = config
        else:
            base_config = get_config()
            self._config = SelfHealingConfig(
                host=host or base_config.host,
                auth_mode=auth_mode or base_config.auth_mode,
                timeout=timeout or base_config.timeout,
            )
        
        # 기본 HTTP 클라이언트 초기화
        self._base_client = BaseClient(self._config)
        
        # 서브 클라이언트 초기화 (lazy loading 가능하지만 명시적으로)
        self.auth = AuthClient(self._base_client)
        self.health = HealthClient(self._base_client)
        self.circuit_breaker = CircuitBreakerClient(self._base_client)
        self.dlq = DLQClient(self._base_client)
        self.error_budget = ErrorBudgetClient(self._base_client)
        self.emergency = EmergencyClient(self._base_client)
        self.observability = ObservabilityClient(self._base_client)
        self.chaos = ChaosClient(self._base_client)
        self.system = SystemClient(self._base_client)
        self.governance = GovernanceClient(self._base_client)
        self.rate_limiter = RateLimiterClient(self._base_client)
        self.alerts = AlertsClient(self._base_client)
        # 추가된 클라이언트들
        self.runtime_config = RuntimeConfigClient(self._base_client)
        self.l2_storage = L2StorageClient(self._base_client)
        self.tiering = TieringClient(self._base_client)
        self.dashboard = DashboardClient(self._base_client)
        self.xtest = XTestClient(self._base_client)
        self.reconciliation = ReconciliationClient(self._base_client)
    
    # =========================================================================
    # Convenience Methods (자주 사용되는 기능에 대한 바로가기)
    # =========================================================================
    
    def login(self, username: str, password: str) -> bool:
        """
        JWT 인증 로그인.
        
        Returns:
            bool: 로그인 성공 여부
        """
        return self.auth.login(username, password)
    
    def is_healthy(self) -> bool:
        """
        시스템 헬스 상태 확인.
        
        Returns:
            bool: 정상이면 True
        """
        result = self.health.ping()
        return result.get("status") == "ok"
    
    def get_overall_status(self) -> dict:
        """
        전체 시스템 상태 요약.
        
        Returns:
            dict: 헬스, CB 상태, DLQ 현황, Error Budget 등 요약
        """
        return {
            "health": self.health.full_health(),
            "circuit_breakers": self.circuit_breaker.get_all_status(),
            "dlq": self.dlq.stats(),
            "error_budget": self.error_budget.get_status(),
            "emergency": self.emergency.get_status(),
        }
    
    # =========================================================================
    # Context Manager Support
    # =========================================================================
    
    def __enter__(self):
        """컨텍스트 매니저 진입."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료 (리소스 정리)."""
        self._base_client.close()
        return False
    
    # =========================================================================
    # Properties
    # =========================================================================
    
    @property
    def config(self) -> SelfHealingConfig:
        """현재 설정 반환."""
        return self._config
    
    @property
    def is_authenticated(self) -> bool:
        """인증 상태 확인."""
        return self._base_client.token is not None
    
    @property
    def base_url(self) -> str:
        """API 기본 URL 반환."""
        return f"{self._config.host}/api/self-healing/"


# 편의를 위한 별칭들
Client = SelfHealingClient
HealingClient = SelfHealingClient


# 모듈 레벨에서 사용 가능한 기능들 노출
__all__ = [
    # Main Client
    "SelfHealingClient",
    "Client",
    "HealingClient",
    
    # Configuration
    "SelfHealingConfig",
    "configure",
    "get_config",
    "reset_config",
    
    # Sub-clients (직접 사용 시)
    "BaseClient",
    "AuthClient",
    "HealthClient",
    "CircuitBreakerClient",
    "DLQClient",
    "ErrorBudgetClient",
    "EmergencyClient",
    "ObservabilityClient",
    "ChaosClient",
    "SystemClient",
    "GovernanceClient",
    "RateLimiterClient",
    "AlertsClient",
    # 추가된 클라이언트들
    "RuntimeConfigClient",
    "L2StorageClient",
    "TieringClient",
    "DashboardClient",
    "XTestClient",
    "ReconciliationClient",
]
