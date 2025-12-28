"""
Locust User를 위한 공통 Mixin 클래스

테스트 시나리오에서 반복적으로 사용되는 인증, Self-Healing 클라이언트,
CB 모니터링, X-Test-Mode 등의 기능을 Mixin으로 제공합니다.
"""
from typing import Optional, Dict, Any, TYPE_CHECKING
import threading
from datetime import datetime

if TYPE_CHECKING:
    from locust import HttpUser


class AdminAuthMixin:
    """
    관리자 인증 공통 로직.
    
    Locust HttpUser와 함께 사용하여 관리자 로그인 및 토큰 관리를 제공합니다.
    
    Usage:
        class MyUser(HttpUser, AdminAuthMixin):
            def on_start(self):
                self.admin_login()
            
            @task
            def my_task(self):
                self.client.get("/api/data/", headers=self.get_auth_headers())
    """
    
    _token: Optional[str] = None
    _refresh_token: Optional[str] = None
    _token_lock = threading.Lock()
    _token_expiry: Optional[datetime] = None
    
    # 설정 (오버라이드 가능)
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "adminpassword"
    LOGIN_ENDPOINT = "/api/auth/login/"
    TOKEN_REFRESH_ENDPOINT = "/api/auth/token/refresh/"
    
    def admin_login(self, username: Optional[str] = None, 
                   password: Optional[str] = None) -> bool:
        """
        관리자 로그인 및 토큰 설정.
        
        Args:
            username: 사용자명 (기본값: ADMIN_USERNAME)
            password: 비밀번호 (기본값: ADMIN_PASSWORD)
            
        Returns:
            로그인 성공 여부
        """
        with self._token_lock:
            response = self.client.post(
                self.LOGIN_ENDPOINT,
                json={
                    "username": username or self.ADMIN_USERNAME,
                    "password": password or self.ADMIN_PASSWORD
                },
                name="[Auth] Admin Login"
            )
            
            if response.status_code == 200:
                data = response.json()
                self._token = data.get("access")
                self._refresh_token = data.get("refresh")
                
                if self._token:
                    self.client.headers["Authorization"] = f"Bearer {self._token}"
                    return True
            
            return False
    
    def get_auth_headers(self) -> Dict[str, str]:
        """
        인증 헤더 반환.
        
        Returns:
            Authorization 헤더가 포함된 딕셔너리
        """
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}
    
    def refresh_token_if_needed(self, response) -> bool:
        """
        401/403 응답 시 토큰 갱신 시도.
        
        Args:
            response: HTTP 응답 객체
            
        Returns:
            토큰 갱신 성공 여부
        """
        if response.status_code in (401, 403):
            # refresh token으로 갱신 시도
            if self._refresh_token:
                refresh_response = self.client.post(
                    self.TOKEN_REFRESH_ENDPOINT,
                    json={"refresh": self._refresh_token},
                    name="[Auth] Token Refresh"
                )
                if refresh_response.status_code == 200:
                    data = refresh_response.json()
                    self._token = data.get("access")
                    if self._token:
                        self.client.headers["Authorization"] = f"Bearer {self._token}"
                        return True
            
            # refresh 실패 시 재로그인
            return self.admin_login()
        return False
    
    def ensure_authenticated(self) -> bool:
        """
        인증 상태 확인 및 필요시 로그인.
        
        Returns:
            인증된 상태 여부
        """
        if self._token:
            return True
        return self.admin_login()
    
    def logout(self) -> None:
        """로그아웃 및 토큰 정리."""
        with self._token_lock:
            self._token = None
            self._refresh_token = None
            if "Authorization" in self.client.headers:
                del self.client.headers["Authorization"]


class SelfHealingMixin:
    """
    Self-Healing 클라이언트 공통 로직.
    
    싱글톤 패턴으로 SelfHealingClient를 관리하여
    여러 User 인스턴스에서 동일한 클라이언트를 공유합니다.
    
    Usage:
        class MyUser(HttpUser, SelfHealingMixin):
            @task
            def healing_task(self):
                client = self.get_healing_client()
                status = client.circuit_breaker.get_status("payment")
    """
    
    _healing_client = None
    _healing_client_lock = threading.Lock()
    
    # 설정 (오버라이드 가능)
    HEALING_AUTH_MODE = "xtest"
    
    @classmethod
    def get_healing_client(cls):
        """
        싱글톤 SelfHealingClient 반환.
        
        Returns:
            SelfHealingClient 인스턴스
        """
        with cls._healing_client_lock:
            if cls._healing_client is None:
                try:
                    from load_tests.utils.selfhealing import SelfHealingClient
                    cls._healing_client = SelfHealingClient(auth_mode=cls.HEALING_AUTH_MODE)
                except ImportError:
                    # SelfHealingClient가 없는 경우 None 반환
                    pass
            return cls._healing_client
    
    @classmethod
    def reset_healing_client(cls) -> None:
        """Self-Healing 클라이언트 리셋."""
        with cls._healing_client_lock:
            cls._healing_client = None
    
    def get_healing_status(self) -> Optional[Dict[str, Any]]:
        """
        전체 Self-Healing 시스템 상태 조회.
        
        Returns:
            상태 딕셔너리 또는 None
        """
        client = self.get_healing_client()
        if client:
            try:
                return client.get_system_status()
            except Exception:
                pass
        return None


class CBMonitorMixin:
    """
    Circuit Breaker 모니터링 공통 로직.
    
    CB 상태 조회, 전이 기록, 강제 상태 변경 등의 기능을 제공합니다.
    
    Usage:
        class MyUser(HttpUser, AdminAuthMixin, CBMonitorMixin):
            @task
            def monitor_cb(self):
                status = self.get_cb_status("payment")
                if status and status.get("state") == "OPEN":
                    self.record_cb_transition("payment", "CLOSED", "OPEN")
    """
    
    # CB 상태 엔드포인트
    CB_STATUS_ENDPOINT = "/api/self-healing/circuit-breaker/status"
    CB_FORCE_OPEN_ENDPOINT = "/api/self-healing/circuit-breaker/force-open"
    CB_RESET_ENDPOINT = "/api/self-healing/circuit-breaker/reset"
    
    def get_cb_status(self, service: str) -> Optional[Dict[str, Any]]:
        """
        Circuit Breaker 상태 조회.
        
        Args:
            service: 서비스 이름
            
        Returns:
            CB 상태 딕셔너리 또는 None
        """
        headers = {}
        if hasattr(self, 'get_auth_headers'):
            headers = self.get_auth_headers()
        
        response = self.client.get(
            f"{self.CB_STATUS_ENDPOINT}/{service}/",
            headers=headers,
            name=f"[CB] Get Status - {service}"
        )
        
        if response.ok:
            return response.json()
        return None
    
    def get_all_cb_status(self) -> Optional[Dict[str, Any]]:
        """
        모든 Circuit Breaker 상태 조회.
        
        Returns:
            전체 CB 상태 딕셔너리 또는 None
        """
        headers = {}
        if hasattr(self, 'get_auth_headers'):
            headers = self.get_auth_headers()
        
        response = self.client.get(
            f"{self.CB_STATUS_ENDPOINT}/",
            headers=headers,
            name="[CB] Get All Status"
        )
        
        if response.ok:
            return response.json()
        return None
    
    def force_cb_open(self, service: str, duration_seconds: int = 30) -> bool:
        """
        Circuit Breaker 강제 Open.
        
        Args:
            service: 서비스 이름
            duration_seconds: Open 유지 시간
            
        Returns:
            성공 여부
        """
        headers = {}
        if hasattr(self, 'get_auth_headers'):
            headers = self.get_auth_headers()
        
        response = self.client.post(
            f"{self.CB_FORCE_OPEN_ENDPOINT}/{service}/",
            json={"duration_seconds": duration_seconds},
            headers=headers,
            name=f"[CB] Force Open - {service}"
        )
        
        return response.ok
    
    def reset_cb(self, service: str) -> bool:
        """
        Circuit Breaker 리셋 (CLOSED로 전환).
        
        Args:
            service: 서비스 이름
            
        Returns:
            성공 여부
        """
        headers = {}
        if hasattr(self, 'get_auth_headers'):
            headers = self.get_auth_headers()
        
        response = self.client.post(
            f"{self.CB_RESET_ENDPOINT}/{service}/",
            headers=headers,
            name=f"[CB] Reset - {service}"
        )
        
        return response.ok
    
    def record_cb_transition(
        self, 
        service: str, 
        from_state: str, 
        to_state: str,
        stats: Optional[Any] = None
    ) -> None:
        """
        CB 상태 전이 기록.
        
        Args:
            service: 서비스 이름
            from_state: 이전 상태
            to_state: 새 상태
            stats: 통계 객체 (ExtremeTestStats)
        """
        if stats and hasattr(stats, 'record_cb_event'):
            stats.record_cb_event(service, to_state)
        elif hasattr(self, 'stats') and hasattr(self.stats, 'record_cb_event'):
            self.stats.record_cb_event(service, to_state)


class XTestModeMixin:
    """
    X-Test-Mode 공통 로직.
    
    테스트 모드 헤더가 포함된 요청을 쉽게 보낼 수 있도록 지원합니다.
    
    Usage:
        class MyUser(HttpUser, XTestModeMixin):
            @task
            def chaos_test(self):
                response = self.xtest_request("post", "/api/orders/", json={...})
                
                # 또는 헤더만 가져오기
                headers = self.get_xtest_headers()
    """
    
    # X-Test-Mode 헤더
    XTEST_HEADERS = {
        "X-Test-Mode": "chaos-monkey",
        "Content-Type": "application/json"
    }
    
    # Chaos 엔드포인트
    XTEST_CB_INJECT_ENDPOINT = "/xtest/chaos/inject-cb-failure"
    XTEST_LATENCY_ENDPOINT = "/xtest/chaos/inject-latency"
    XTEST_BLAST_RADIUS_ENDPOINT = "/xtest/chaos/blast-radius-test"
    
    def get_xtest_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        X-Test-Mode 헤더 반환.
        
        Args:
            extra: 추가 헤더
            
        Returns:
            X-Test-Mode 헤더가 포함된 딕셔너리
        """
        headers = dict(self.XTEST_HEADERS)
        
        # 인증 헤더 추가
        if hasattr(self, 'get_auth_headers'):
            headers.update(self.get_auth_headers())
        
        # 추가 헤더 병합
        if extra:
            headers.update(extra)
        
        return headers
    
    def xtest_request(
        self, 
        method: str, 
        url: str, 
        name: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        X-Test-Mode 헤더가 포함된 요청 전송.
        
        Args:
            method: HTTP 메서드 (get, post, put, delete, etc.)
            url: 요청 URL
            name: Locust 리포트용 이름
            **kwargs: 추가 요청 파라미터
            
        Returns:
            HTTP 응답
        """
        headers = kwargs.pop("headers", {})
        headers.update(self.get_xtest_headers())
        
        request_name = name or f"[XTest] {method.upper()} {url}"
        
        return getattr(self.client, method.lower())(
            url, 
            headers=headers, 
            name=request_name,
            **kwargs
        )
    
    def inject_cb_failure(
        self, 
        service: str, 
        failure_count: int = 5
    ) -> Optional[Dict[str, Any]]:
        """
        CB 실패 주입.
        
        Args:
            service: 대상 서비스
            failure_count: 주입할 실패 횟수
            
        Returns:
            응답 데이터 또는 None
        """
        response = self.xtest_request(
            "post",
            f"{self.XTEST_CB_INJECT_ENDPOINT}/{service}/",
            json={"failure_count": failure_count},
            name=f"[XTest] Inject CB Failure - {service}"
        )
        
        if response.ok:
            return response.json()
        return None
    
    def inject_latency(
        self, 
        service: str, 
        latency_ms: int = 500,
        duration_seconds: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        지연 주입.
        
        Args:
            service: 대상 서비스
            latency_ms: 주입할 지연 시간 (밀리초)
            duration_seconds: 지연 유지 시간 (초)
            
        Returns:
            응답 데이터 또는 None
        """
        response = self.xtest_request(
            "post",
            f"{self.XTEST_LATENCY_ENDPOINT}/{service}/",
            json={
                "latency_ms": latency_ms,
                "duration_seconds": duration_seconds
            },
            name=f"[XTest] Inject Latency - {service}"
        )
        
        if response.ok:
            return response.json()
        return None
    
    def blast_radius_test(
        self, 
        services: Optional[list] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Blast Radius 테스트 실행.
        
        Args:
            services: 테스트할 서비스 목록
            
        Returns:
            테스트 결과 또는 None
        """
        if services is None:
            services = ["database", "payment", "cache"]
        
        response = self.xtest_request(
            "post",
            self.XTEST_BLAST_RADIUS_ENDPOINT,
            json={"services": services},
            name="[XTest] Blast Radius Test"
        )
        
        if response.ok:
            return response.json()
        return None


class PhaseManagerMixin:
    """
    부하 페이즈 관리 Mixin.
    
    스파이크/쿨다운 등 테스트 페이즈 상태를 관리합니다.
    """
    
    _current_phase: str = "idle"
    _phase_start_time: Optional[datetime] = None
    _phase_lock = threading.Lock()
    
    PHASES = ["idle", "ramp_up", "spike", "cooldown", "ramp_down"]
    
    @classmethod
    def set_phase(cls, phase: str) -> None:
        """
        현재 페이즈 설정.
        
        Args:
            phase: 페이즈 이름
        """
        with cls._phase_lock:
            if phase in cls.PHASES:
                cls._current_phase = phase
                cls._phase_start_time = datetime.now()
    
    @classmethod
    def get_phase(cls) -> str:
        """현재 페이즈 반환."""
        return cls._current_phase
    
    @classmethod
    def get_phase_duration_seconds(cls) -> float:
        """현재 페이즈 지속 시간(초) 반환."""
        if cls._phase_start_time:
            return (datetime.now() - cls._phase_start_time).total_seconds()
        return 0.0
    
    @classmethod
    def is_spike_phase(cls) -> bool:
        """스파이크 페이즈 여부."""
        return cls._current_phase == "spike"
    
    @classmethod
    def is_cooldown_phase(cls) -> bool:
        """쿨다운 페이즈 여부."""
        return cls._current_phase == "cooldown"
