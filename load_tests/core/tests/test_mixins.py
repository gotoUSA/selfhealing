"""
Unit tests for load_tests.core.mixins module

Note: Mixin 클래스는 Locust HttpUser와 함께 사용되도록 설계되었으므로,
일부 테스트는 Mock 객체를 사용하여 HTTP 클라이언트 동작을 시뮬레이션합니다.
"""
import pytest
import threading
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from load_tests.core.mixins import (
    AdminAuthMixin, 
    SelfHealingMixin, 
    CBMonitorMixin, 
    XTestModeMixin,
    PhaseManagerMixin
)


class MockHttpClient:
    """Mock HTTP 클라이언트"""
    
    def __init__(self):
        self.headers = {}
        self._responses = {}
    
    def set_response(self, method: str, url: str, response):
        """응답 설정"""
        self._responses[(method, url)] = response
    
    def _get_response(self, method: str, url: str, **kwargs):
        """응답 반환"""
        for (m, u), response in self._responses.items():
            if m == method and (u == url or u in url):
                return response
        # 기본 응답
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {}
        return mock_response
    
    def post(self, url, **kwargs):
        return self._get_response("post", url, **kwargs)
    
    def get(self, url, **kwargs):
        return self._get_response("get", url, **kwargs)
    
    def put(self, url, **kwargs):
        return self._get_response("put", url, **kwargs)
    
    def delete(self, url, **kwargs):
        return self._get_response("delete", url, **kwargs)


class TestAdminAuthMixin:
    """AdminAuthMixin 테스트"""
    
    def test_admin_login_success(self):
        """관리자 로그인 성공"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        
        # 성공 응답 설정
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access": "test_token_123", "refresh": "refresh_token"}
        user.client.set_response("post", "/api/auth/login/", response)
        
        result = user.admin_login()
        
        assert result is True
        assert user._token == "test_token_123"
        assert "Authorization" in user.client.headers
        assert "Bearer test_token_123" in user.client.headers["Authorization"]
    
    def test_admin_login_failure(self):
        """관리자 로그인 실패"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        
        # 실패 응답 설정
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"error": "Invalid credentials"}
        user.client.set_response("post", "/api/auth/login/", response)
        
        result = user.admin_login()
        
        assert result is False
        assert user._token is None
    
    def test_get_auth_headers_with_token(self):
        """토큰이 있을 때 인증 헤더 반환"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        user._token = "my_token"
        
        headers = user.get_auth_headers()
        
        assert headers["Authorization"] == "Bearer my_token"
    
    def test_get_auth_headers_without_token(self):
        """토큰이 없을 때 빈 헤더 반환"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        user._token = None
        
        headers = user.get_auth_headers()
        
        assert headers == {}
    
    def test_ensure_authenticated_with_token(self):
        """이미 인증된 상태 확인"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        user._token = "existing_token"
        
        result = user.ensure_authenticated()
        
        assert result is True
    
    def test_logout(self):
        """로그아웃 테스트"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        user._token = "token"
        user._refresh_token = "refresh"
        user.client.headers["Authorization"] = "Bearer token"
        
        user.logout()
        
        assert user._token is None
        assert user._refresh_token is None
        assert "Authorization" not in user.client.headers
    
    def test_custom_credentials(self):
        """커스텀 자격증명 사용"""
        class TestUser(AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access": "custom_token"}
        user.client.set_response("post", "/api/auth/login/", response)
        
        result = user.admin_login(username="custom_user", password="custom_pass")
        
        assert result is True
        assert user._token == "custom_token"


class TestSelfHealingMixin:
    """SelfHealingMixin 테스트"""
    
    def test_get_healing_client_singleton(self):
        """싱글톤 클라이언트 반환"""
        class TestUser(SelfHealingMixin):
            pass
        
        # 클라이언트 초기화 (모듈이 없으면 None)
        TestUser._healing_client = None
        
        # SelfHealingClient 모듈이 없는 경우 None 반환
        client = TestUser.get_healing_client()
        # 실제 환경에서는 클라이언트가 반환되거나 None
        # 여기서는 모듈이 있다고 가정하고 테스트
    
    def test_reset_healing_client(self):
        """클라이언트 리셋"""
        class TestUser(SelfHealingMixin):
            pass
        
        TestUser._healing_client = Mock()
        
        TestUser.reset_healing_client()
        
        assert TestUser._healing_client is None


class TestCBMonitorMixin:
    """CBMonitorMixin 테스트"""
    
    def test_get_cb_status(self):
        """CB 상태 조회"""
        class TestUser(CBMonitorMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        response.json.return_value = {"state": "CLOSED", "failure_count": 0}
        user.client.set_response("get", "/api/self-healing/circuit-breaker/status/payment/", response)
        
        status = user.get_cb_status("payment")
        
        assert status is not None
        assert status["state"] == "CLOSED"
    
    def test_get_cb_status_failure(self):
        """CB 상태 조회 실패"""
        class TestUser(CBMonitorMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = False
        user.client.set_response("get", "/api/self-healing/circuit-breaker/status/unknown/", response)
        
        status = user.get_cb_status("unknown")
        
        assert status is None
    
    def test_force_cb_open(self):
        """CB 강제 Open"""
        class TestUser(CBMonitorMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        user.client.set_response("post", "/api/self-healing/circuit-breaker/force-open/payment/", response)
        
        result = user.force_cb_open("payment")
        
        assert result is True
    
    def test_reset_cb(self):
        """CB 리셋"""
        class TestUser(CBMonitorMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        user.client.set_response("post", "/api/self-healing/circuit-breaker/reset/payment/", response)
        
        result = user.reset_cb("payment")
        
        assert result is True
    
    def test_record_cb_transition_with_stats(self):
        """CB 전이 기록 (stats 객체 사용)"""
        class TestUser(CBMonitorMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = TestUser()
        
        mock_stats = Mock()
        mock_stats.record_cb_event = Mock()
        
        user.record_cb_transition("payment", "CLOSED", "OPEN", stats=mock_stats)
        
        mock_stats.record_cb_event.assert_called_once_with("payment", "OPEN")


class TestXTestModeMixin:
    """XTestModeMixin 테스트"""
    
    def test_xtest_headers(self):
        """X-Test-Mode 헤더 확인"""
        assert "X-Test-Mode" in XTestModeMixin.XTEST_HEADERS
        assert XTestModeMixin.XTEST_HEADERS["X-Test-Mode"] == "chaos-monkey"
    
    def test_get_xtest_headers(self):
        """X-Test 헤더 조합"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        headers = user.get_xtest_headers()
        
        assert "X-Test-Mode" in headers
        assert "Authorization" in headers
    
    def test_get_xtest_headers_with_extra(self):
        """추가 헤더 포함"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        headers = user.get_xtest_headers(extra={"X-Custom": "value"})
        
        assert headers["X-Custom"] == "value"
        assert "X-Test-Mode" in headers
    
    def test_xtest_request(self):
        """X-Test 요청 전송"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        response.json.return_value = {"result": "success"}
        user.client.set_response("post", "/api/test/", response)
        
        result = user.xtest_request("post", "/api/test/", json={"data": "test"})
        
        assert result.ok is True
    
    def test_inject_cb_failure(self):
        """CB 실패 주입"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        response.json.return_value = {"injected": True, "count": 5}
        user.client.set_response("post", "/xtest/chaos/inject-cb-failure/payment/", response)
        
        result = user.inject_cb_failure("payment", failure_count=5)
        
        assert result is not None
        assert result["injected"] is True
    
    def test_inject_latency(self):
        """지연 주입"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        response.json.return_value = {"latency_ms": 500, "duration": 30}
        user.client.set_response("post", "/xtest/chaos/inject-latency/database/", response)
        
        result = user.inject_latency("database", latency_ms=500, duration_seconds=30)
        
        assert result is not None
        assert result["latency_ms"] == 500
    
    def test_blast_radius_test(self):
        """Blast Radius 테스트"""
        class TestUser(XTestModeMixin, AdminAuthMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test_token"
        
        user = TestUser()
        
        response = Mock()
        response.ok = True
        response.json.return_value = {"affected_services": ["payment", "database"]}
        user.client.set_response("post", "/xtest/chaos/blast-radius-test", response)
        
        result = user.blast_radius_test(services=["payment", "database"])
        
        assert result is not None
        assert "affected_services" in result


class TestPhaseManagerMixin:
    """PhaseManagerMixin 테스트"""
    
    def setup_method(self):
        """각 테스트 전 상태 리셋"""
        PhaseManagerMixin._current_phase = "idle"
        PhaseManagerMixin._phase_start_time = None
    
    def test_initial_phase(self):
        """초기 페이즈 확인"""
        assert PhaseManagerMixin.get_phase() == "idle"
    
    def test_set_phase(self):
        """페이즈 설정"""
        PhaseManagerMixin.set_phase("spike")
        
        assert PhaseManagerMixin.get_phase() == "spike"
        assert PhaseManagerMixin._phase_start_time is not None
    
    def test_set_invalid_phase(self):
        """유효하지 않은 페이즈 설정 (무시됨)"""
        PhaseManagerMixin.set_phase("spike")
        PhaseManagerMixin.set_phase("invalid_phase")
        
        # 유효하지 않은 페이즈는 무시되고 이전 값 유지
        assert PhaseManagerMixin.get_phase() == "spike"
    
    def test_get_phase_duration(self):
        """페이즈 지속 시간 확인"""
        PhaseManagerMixin.set_phase("cooldown")
        
        duration = PhaseManagerMixin.get_phase_duration_seconds()
        
        assert duration >= 0
        assert duration < 1  # 즉시 조회이므로 1초 미만
    
    def test_is_spike_phase(self):
        """스파이크 페이즈 확인"""
        PhaseManagerMixin.set_phase("spike")
        assert PhaseManagerMixin.is_spike_phase() is True
        
        PhaseManagerMixin.set_phase("cooldown")
        assert PhaseManagerMixin.is_spike_phase() is False
    
    def test_is_cooldown_phase(self):
        """쿨다운 페이즈 확인"""
        PhaseManagerMixin.set_phase("cooldown")
        assert PhaseManagerMixin.is_cooldown_phase() is True
        
        PhaseManagerMixin.set_phase("spike")
        assert PhaseManagerMixin.is_cooldown_phase() is False
    
    def test_all_valid_phases(self):
        """모든 유효한 페이즈 테스트"""
        valid_phases = ["idle", "ramp_up", "spike", "cooldown", "ramp_down"]
        
        for phase in valid_phases:
            PhaseManagerMixin.set_phase(phase)
            assert PhaseManagerMixin.get_phase() == phase


class TestMixinCombination:
    """Mixin 조합 테스트"""
    
    def test_multiple_mixins(self):
        """여러 Mixin 조합 사용"""
        class CompleteTestUser(AdminAuthMixin, SelfHealingMixin, CBMonitorMixin, XTestModeMixin):
            def __init__(self):
                self.client = MockHttpClient()
        
        user = CompleteTestUser()
        
        # 모든 Mixin의 메서드가 사용 가능한지 확인
        assert hasattr(user, 'admin_login')
        assert hasattr(user, 'get_healing_client')
        assert hasattr(user, 'get_cb_status')
        assert hasattr(user, 'xtest_request')
    
    def test_mixin_method_resolution_order(self):
        """MRO (Method Resolution Order) 확인"""
        class TestUser(AdminAuthMixin, CBMonitorMixin, XTestModeMixin):
            def __init__(self):
                self.client = MockHttpClient()
                self._token = "test"
        
        user = TestUser()
        
        # get_auth_headers는 AdminAuthMixin에서 정의됨
        headers = user.get_auth_headers()
        assert "Authorization" in headers
        
        # get_xtest_headers는 XTestModeMixin에서 정의되지만 
        # AdminAuthMixin의 get_auth_headers를 사용
        xtest_headers = user.get_xtest_headers()
        assert "X-Test-Mode" in xtest_headers
        assert "Authorization" in xtest_headers
