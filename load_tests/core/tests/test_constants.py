"""
Unit tests for load_tests.core.constants module
"""
import pytest

from load_tests.core.constants import (
    Endpoints, Headers, SLA, LoadConfig, 
    Services, EmergencyLevels, TestModes
)


class TestEndpoints:
    """Endpoints 클래스 테스트"""
    
    def test_auth_endpoints(self):
        """인증 엔드포인트 확인"""
        assert Endpoints.AUTH_LOGIN == "/api/auth/login/"
        assert Endpoints.AUTH_LOGOUT == "/api/auth/logout/"
        assert Endpoints.AUTH_TOKEN_REFRESH == "/api/auth/token/refresh/"
    
    def test_products_endpoints(self):
        """상품 엔드포인트 확인"""
        assert Endpoints.PRODUCTS == "/api/products/"
        assert "{id}" in Endpoints.PRODUCTS_DETAIL
    
    def test_self_healing_endpoints(self):
        """Self-Healing 엔드포인트 확인"""
        assert Endpoints.SH_BASE == "/api/self-healing"
        assert "circuit-breaker" in Endpoints.SH_CB_STATUS
        assert "emergency" in Endpoints.SH_EMERGENCY
        assert "dlq" in Endpoints.SH_DLQ
    
    def test_xtest_endpoints(self):
        """XTest 엔드포인트 확인"""
        assert Endpoints.XTEST_BASE == "/xtest"
        assert "chaos" in Endpoints.XTEST_CB_INJECT
        assert "latency" in Endpoints.XTEST_LATENCY
    
    def test_format_method(self):
        """format 메서드 테스트"""
        result = Endpoints.format(Endpoints.SH_CB_STATUS_SERVICE, service="payment")
        assert result == "/api/self-healing/circuit-breaker/status/payment/"
        
        result = Endpoints.format(Endpoints.PRODUCTS_DETAIL, id=123)
        assert result == "/api/products/123/"


class TestHeaders:
    """Headers 클래스 테스트"""
    
    def test_json_header(self):
        """JSON 헤더 확인"""
        assert Headers.JSON["Content-Type"] == "application/json"
    
    def test_xtest_headers(self):
        """X-Test-Mode 헤더 확인"""
        assert "X-Test-Mode" in Headers.XTEST_MODE
        assert Headers.XTEST_MODE["X-Test-Mode"] == "chaos-monkey"
        assert Headers.XTEST_LOAD["X-Test-Mode"] == "load-test"
    
    def test_combined_headers(self):
        """조합 헤더 확인"""
        assert "X-Test-Mode" in Headers.XTEST_JSON
        assert "Content-Type" in Headers.XTEST_JSON
    
    def test_with_auth(self):
        """인증 헤더 추가"""
        token = "test_jwt_token"
        headers = Headers.with_auth(token)
        
        assert headers["Authorization"] == f"Bearer {token}"
        assert headers["Content-Type"] == "application/json"
    
    def test_with_auth_custom_base(self):
        """커스텀 베이스 헤더로 인증 추가"""
        token = "test_token"
        headers = Headers.with_auth(token, base=Headers.XTEST_MODE)
        
        assert headers["Authorization"] == f"Bearer {token}"
        assert headers["X-Test-Mode"] == "chaos-monkey"


class TestSLA:
    """SLA 클래스 테스트"""
    
    def test_response_time_thresholds(self):
        """응답 시간 임계값 확인"""
        assert SLA.P99_THRESHOLD_MS == 300.0
        assert SLA.P95_THRESHOLD_MS == 200.0
        assert SLA.CRITICAL_THRESHOLD_MS == 500.0
    
    def test_error_rate_thresholds(self):
        """에러율 임계값 확인"""
        assert SLA.ERROR_RATE_MAX == 5.0
        assert SLA.ERROR_RATE_CRITICAL == 10.0
    
    def test_recovery_time_thresholds(self):
        """복구 시간 임계값 확인"""
        assert SLA.RECOVERY_TIME_MAX_S == 120.0
        assert SLA.RECOVERY_TIME_TARGET_S == 60.0
    
    def test_is_p99_breach(self):
        """P99 위반 판정"""
        assert SLA.is_p99_breach(350.0) is True
        assert SLA.is_p99_breach(300.0) is False
        assert SLA.is_p99_breach(200.0) is False
    
    def test_is_p95_breach(self):
        """P95 위반 판정"""
        assert SLA.is_p95_breach(250.0) is True
        assert SLA.is_p95_breach(200.0) is False
    
    def test_is_critical(self):
        """Critical 판정"""
        assert SLA.is_critical(600.0) is True
        assert SLA.is_critical(500.0) is False
        assert SLA.is_critical(300.0) is False
    
    def test_is_error_rate_breach(self):
        """에러율 위반 판정"""
        assert SLA.is_error_rate_breach(6.0) is True
        assert SLA.is_error_rate_breach(5.0) is False
        assert SLA.is_error_rate_breach(1.0) is False


class TestLoadConfig:
    """LoadConfig 클래스 테스트"""
    
    def test_user_counts(self):
        """사용자 수 설정 확인"""
        assert LoadConfig.USERS_SMOKE == 1
        assert LoadConfig.USERS_NORMAL == 30
        assert LoadConfig.USERS_SPIKE == 50
        assert LoadConfig.USERS_EXTREME == 150
        assert LoadConfig.USERS_HELLMODE == 300
    
    def test_duration_settings(self):
        """테스트 시간 설정 확인"""
        assert LoadConfig.DURATION_SMOKE_S == 30
        assert LoadConfig.DURATION_NORMAL_S == 180
        assert LoadConfig.DURATION_ENDURANCE_S == 3600
    
    def test_phase_settings(self):
        """페이즈 설정 확인"""
        assert LoadConfig.RAMP_UP_S == 10
        assert LoadConfig.SPIKE_DURATION_S == 30
        assert LoadConfig.COOLDOWN_DURATION_S == 30
    
    def test_cycle_settings(self):
        """사이클 설정 확인"""
        assert LoadConfig.NUM_CYCLES_DEFAULT == 1
        assert LoadConfig.NUM_CYCLES_ENDURANCE == 5
        assert LoadConfig.NUM_CYCLES_EXTREME == 10
    
    def test_wait_times(self):
        """대기 시간 확인"""
        assert LoadConfig.WAIT_MIN_S == 0.5
        assert LoadConfig.WAIT_MAX_S == 2.0
        assert LoadConfig.WAIT_AGGRESSIVE_MIN_S == 0.1


class TestServices:
    """Services 클래스 테스트"""
    
    def test_service_names(self):
        """서비스 이름 확인"""
        assert Services.DATABASE == "database"
        assert Services.PAYMENT == "payment"
        assert Services.CACHE == "cache"
        assert Services.TOSS == "toss"
    
    def test_all_services_list(self):
        """전체 서비스 목록 확인"""
        assert Services.DATABASE in Services.ALL
        assert Services.PAYMENT in Services.ALL
        assert len(Services.ALL) >= 5
    
    def test_cb_services(self):
        """CB 대상 서비스 확인"""
        assert Services.DATABASE in Services.CB_SERVICES
        assert Services.PAYMENT in Services.CB_SERVICES
    
    def test_chaos_targets(self):
        """Chaos 테스트 대상 확인"""
        assert len(Services.CHAOS_TARGETS) == 3
        assert Services.DATABASE in Services.CHAOS_TARGETS


class TestEmergencyLevels:
    """EmergencyLevels 클래스 테스트"""
    
    def test_level_values(self):
        """레벨 값 확인"""
        assert EmergencyLevels.NORMAL == 0
        assert EmergencyLevels.WARNING == 1
        assert EmergencyLevels.CRITICAL == 4
        assert EmergencyLevels.LOCKDOWN == 5
    
    def test_get_name(self):
        """레벨 이름 조회"""
        assert EmergencyLevels.get_name(0) == "NORMAL"
        assert EmergencyLevels.get_name(4) == "CRITICAL"
        assert EmergencyLevels.get_name(5) == "LOCKDOWN"
        assert "UNKNOWN" in EmergencyLevels.get_name(99)
    
    def test_is_critical(self):
        """Critical 이상 판정"""
        assert EmergencyLevels.is_critical(4) is True
        assert EmergencyLevels.is_critical(5) is True
        assert EmergencyLevels.is_critical(3) is False
        assert EmergencyLevels.is_critical(0) is False


class TestTestModes:
    """TestModes 클래스 테스트"""
    
    def test_mode_values(self):
        """모드 값 확인"""
        assert TestModes.SMOKE == "smoke"
        assert TestModes.LOAD == "load"
        assert TestModes.CHAOS == "chaos"
        assert TestModes.EXTREME == "extreme"
        assert TestModes.HELLMODE == "hellmode"
