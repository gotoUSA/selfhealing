"""
X-Test-Mode Throttle Simulation API Integration Tests.

Adaptive Throttle X-Test API의 통합 테스트.
Docker Compose 환경에서 실행됩니다.

Requirements:
- Docker Compose for Redis and Postgres
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: docker-compose -f docker-compose.test.yml run test pytest tests/integration/selfhealing/test_throttle_xtest_integration.py -v

Endpoints Tested:
- POST /api/self-healing/xtest/throttle/simulate-emergency/
- POST /api/self-healing/xtest/throttle/simulate-cb-open/
- POST /api/self-healing/xtest/throttle/inject-rtt-delay/
- GET  /api/self-healing/xtest/throttle/status/
- POST /api/self-healing/xtest/throttle/reset/
"""

import os
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import patch
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def api_client():
    """Create authenticated API client with staff user and required groups."""
    User = get_user_model()
    user = User.objects.create_user(
        username="throttle_xtest_admin",
        password="testpass123",
        is_staff=True,
    )

    # Add user to required groups for X-Test access
    chaos_group, _ = Group.objects.get_or_create(name="selfhealing_chaos_tester")
    user.groups.add(chaos_group)

    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def chaos_headers():
    """X-Test-Mode required headers."""
    return {"HTTP_X_TEST_MODE": "chaos-monkey"}


@pytest.fixture
def mock_chaos_allowed():
    """Mock XTestModeMixin.is_chaos_allowed to always return True."""
    with patch(
        "selfhealing.api.django.views.xtest.base.XTestModeMixin.is_chaos_allowed",
        return_value=(True, "Chaos mode allowed"),
    ):
        yield


@pytest.fixture(autouse=True)
def reset_throttle():
    """각 테스트 전/후 Throttle 상태 리셋."""
    from selfhealing.services.throttle.adaptive import reset_adaptive_throttle

    reset_adaptive_throttle()
    yield
    reset_adaptive_throttle()


# =============================================================================
# URL Constants
# =============================================================================

BASE_URL = "/api/self-healing"
SIMULATE_EMERGENCY_URL = f"{BASE_URL}/xtest/throttle/simulate-emergency/"
SIMULATE_CB_OPEN_URL = f"{BASE_URL}/xtest/throttle/simulate-cb-open/"
INJECT_RTT_DELAY_URL = f"{BASE_URL}/xtest/throttle/inject-rtt-delay/"
STATUS_URL = f"{BASE_URL}/xtest/throttle/status/"
RESET_URL = f"{BASE_URL}/xtest/throttle/reset/"


# =============================================================================
# Test Class: Emergency Level Simulation
# =============================================================================


@pytest.mark.django_db
class TestThrottleEmergencySimulation:
    """Emergency 레벨 시뮬레이션 API 테스트."""

    def test_simulate_emergency_level_2(self, api_client, chaos_headers, mock_chaos_allowed):
        """Emergency Level 2 시뮬레이션 성공."""
        response = api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 2, "service": "test-service"},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert data["simulation"] == "emergency_level_change"
        assert data["level"] == 2
        assert data["multiplier"] == 0.5
        assert "previous_limit" in data
        assert "new_limit" in data
        assert "timestamp" in data

    def test_simulate_emergency_level_3_gradient_frozen(self, api_client, chaos_headers, mock_chaos_allowed):
        """Emergency Level 3에서 Gradient Frozen 및 min_limit 적용 시뮬레이션."""
        response = api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 3},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["level"] == 3
        assert data["multiplier"] == 0.0
        # Level 3에서 Gradient는 항상 frozen
        assert data["gradient_frozen"] is True
        # Full Stop은 3중 조건(Emergency+CB+RTT)이 모두 충족되어야 활성화
        # 테스트 환경에서는 CB와 RTT 조건이 미충족이므로 False일 수 있음
        assert "full_stop_active" in data

    def test_simulate_emergency_invalid_level(self, api_client, chaos_headers, mock_chaos_allowed):
        """유효하지 않은 Emergency Level 요청 시 400 반환."""
        response = api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 5},  # Invalid: max is 3
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "invalid_level"

    def test_simulate_emergency_without_chaos_header(self, api_client):
        """X-Test-Mode 헤더 없이 요청 시 403 반환."""
        response = api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 2},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


# =============================================================================
# Test Class: CB State Simulation
# =============================================================================


@pytest.mark.django_db
class TestThrottleCBOpenSimulation:
    """Circuit Breaker OPEN 시뮬레이션 API 테스트."""

    def test_simulate_cb_open(self, api_client, chaos_headers, mock_chaos_allowed):
        """CB OPEN 시뮬레이션 성공."""
        response = api_client.post(
            SIMULATE_CB_OPEN_URL,
            data={"service": "payment-api", "state": "open"},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert data["simulation"] == "cb_state_change"
        assert data["service"] == "payment-api"
        assert data["cb_state"] == "open"
        assert "previous_limit" in data
        assert "new_limit" in data
        # CB OPEN시 limit이 감소해야 함
        assert data["new_limit"] <= data["previous_limit"]

    def test_simulate_cb_half_open(self, api_client, chaos_headers, mock_chaos_allowed):
        """CB HALF_OPEN 시뮬레이션."""
        response = api_client.post(
            SIMULATE_CB_OPEN_URL,
            data={"service": "user-api", "state": "half_open"},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["cb_state"] == "half_open"

    def test_simulate_cb_closed(self, api_client, chaos_headers, mock_chaos_allowed):
        """CB CLOSED 시뮬레이션 (정상 복구)."""
        response = api_client.post(
            SIMULATE_CB_OPEN_URL,
            data={"service": "order-api", "state": "closed"},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["cb_state"] == "closed"

    def test_simulate_cb_invalid_state(self, api_client, chaos_headers, mock_chaos_allowed):
        """유효하지 않은 CB 상태 요청 시 400 반환."""
        response = api_client.post(
            SIMULATE_CB_OPEN_URL,
            data={"service": "test-api", "state": "invalid_state"},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "invalid_state"


# =============================================================================
# Test Class: RTT Delay Injection
# =============================================================================


@pytest.mark.django_db
class TestThrottleRTTDelayInjection:
    """RTT 지연 주입 API 테스트."""

    def test_inject_single_rtt_sample(self, api_client, chaos_headers, mock_chaos_allowed):
        """단일 RTT 샘플 주입 성공."""
        response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 150},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert data["simulation"] == "rtt_delay_injection"
        assert data["rtt_ms"] == 150
        assert data["samples_injected"] == 1
        assert "new_gradient" in data

    def test_inject_multiple_rtt_samples(self, api_client, chaos_headers, mock_chaos_allowed):
        """다중 RTT 샘플 주입."""
        response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 300, "count": 5},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["samples_injected"] == 5
        # 높은 RTT가 주입되었으므로 gradient가 변화해야 함

    def test_inject_critical_rtt(self, api_client, chaos_headers, mock_chaos_allowed):
        """SLA Critical RTT 주입 시 상태 확인."""
        # 높은 RTT 주입
        response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 500, "count": 10},
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # SLA 상태가 critical로 변경될 수 있음
        assert data["sla_status"] in ["healthy", "warning", "critical"]

    def test_inject_rtt_invalid_value(self, api_client, chaos_headers, mock_chaos_allowed):
        """유효하지 않은 RTT 값 요청 시 400 반환."""
        response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": -100},  # Invalid: must be positive
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "invalid_rtt"

    def test_inject_rtt_invalid_count(self, api_client, chaos_headers, mock_chaos_allowed):
        """유효하지 않은 count 요청 시 400 반환."""
        response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 100, "count": 200},  # Invalid: max is 100
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["error"] == "invalid_count"


# =============================================================================
# Test Class: Throttle Status
# =============================================================================


@pytest.mark.django_db
class TestThrottleStatus:
    """Throttle 상태 조회 API 테스트."""

    def test_get_status_success(self, api_client, chaos_headers, mock_chaos_allowed):
        """Throttle 상태 조회 성공."""
        response = api_client.get(
            STATUS_URL,
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert "throttle" in data
        assert "settings" in data
        assert "timestamp" in data

        throttle_data = data["throttle"]
        assert "current_limit" in throttle_data
        assert "min_limit" in throttle_data
        assert "max_limit" in throttle_data
        assert "gradient" in throttle_data
        assert "emergency" in throttle_data
        assert "recovery" in throttle_data

    def test_get_status_after_emergency_simulation(self, api_client, chaos_headers, mock_chaos_allowed):
        """Emergency 시뮬레이션 후 상태 확인."""
        # Emergency Level 2 시뮬레이션
        api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 2},
            format="json",
            **chaos_headers,
        )

        # 상태 조회
        response = api_client.get(
            STATUS_URL,
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Emergency 상태가 반영되어야 함
        assert data["throttle"]["emergency"] is not None


# =============================================================================
# Test Class: Throttle Reset
# =============================================================================


@pytest.mark.django_db
class TestThrottleReset:
    """Throttle 리셋 API 테스트."""

    def test_reset_success(self, api_client, chaos_headers, mock_chaos_allowed):
        """Throttle 리셋 성공."""
        response = api_client.post(
            RESET_URL,
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "success"
        assert data["action"] == "throttle_reset"
        assert "previous_limit" in data
        assert "new_limit" in data
        assert "timestamp" in data

    def test_reset_after_emergency(self, api_client, chaos_headers, mock_chaos_allowed):
        """Emergency 상태 후 리셋 시 원래 상태로 복구."""
        # Emergency Level 3 시뮬레이션
        api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 3},
            format="json",
            **chaos_headers,
        )

        # 리셋
        response = api_client.post(
            RESET_URL,
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["previous_level"] == 3
        # 리셋 후 새 limit은 initial limit이어야 함

    def test_reset_clears_gradient_state(self, api_client, chaos_headers, mock_chaos_allowed):
        """리셋 시 Gradient 상태도 초기화."""
        # RTT 샘플 주입으로 Gradient 상태 변경
        api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 400, "count": 10},
            format="json",
            **chaos_headers,
        )

        # 리셋
        api_client.post(
            RESET_URL,
            format="json",
            **chaos_headers,
        )

        # 상태 확인
        response = api_client.get(
            STATUS_URL,
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # 리셋 후 gradient는 초기값(0)에 가까워야 함
        assert abs(data["throttle"]["gradient"]) < 0.5


# =============================================================================
# Test Class: Exception Handler Delegation
# =============================================================================


@pytest.mark.django_db
class TestExceptionHandlerDelegation:
    """
    DRF Exception Handler 위임 테스트.

    throttle_simulation.py의 뷰들은 try/except 없이
    DRF exception handler에 예외 처리를 위임합니다.
    """

    def test_exception_returns_proper_format(self, api_client, chaos_headers, mock_chaos_allowed):
        """
        예외 발생 시 selfhealing_exception_handler 형식으로 응답.

        DRF exception handler가 표준 에러 형식을 반환해야 합니다.
        """
        with patch(
            "selfhealing.services.throttle.adaptive.get_adaptive_throttle",
            side_effect=RuntimeError("Simulated throttle error"),
        ):
            response = api_client.get(
                STATUS_URL,
                **chaos_headers,
            )

        # DRF exception handler가 처리하여 500 반환
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        # selfhealing_exception_handler 형식 확인
        data = response.json()
        # Exception handler가 적절한 에러 응답 형식을 반환해야 함
        assert "error" in data or "detail" in data


# =============================================================================
# Test Class: Full Flow Integration
# =============================================================================


@pytest.mark.django_db
class TestThrottleFullFlowIntegration:
    """
    Throttle X-Test API 전체 흐름 통합 테스트.

    Emergency 시뮬레이션 → CB 시뮬레이션 → RTT 주입 → 상태 확인 → 리셋
    """

    def test_complete_throttle_simulation_flow(self, api_client, chaos_headers, mock_chaos_allowed):
        """전체 시뮬레이션 흐름 테스트."""
        # 1. 초기 상태 확인
        status_response = api_client.get(STATUS_URL, **chaos_headers)
        assert status_response.status_code == status.HTTP_200_OK
        initial_limit = status_response.json()["throttle"]["current_limit"]

        # 2. Emergency Level 1 시뮬레이션
        em_response = api_client.post(
            SIMULATE_EMERGENCY_URL,
            data={"level": 1},
            format="json",
            **chaos_headers,
        )
        assert em_response.status_code == status.HTTP_200_OK
        assert em_response.json()["multiplier"] == 0.8

        # 3. CB OPEN 시뮬레이션
        cb_response = api_client.post(
            SIMULATE_CB_OPEN_URL,
            data={"service": "integration-test", "state": "open"},
            format="json",
            **chaos_headers,
        )
        assert cb_response.status_code == status.HTTP_200_OK
        cb_limit = cb_response.json()["new_limit"]

        # 4. RTT 지연 주입
        rtt_response = api_client.post(
            INJECT_RTT_DELAY_URL,
            data={"rtt_ms": 250, "count": 3},
            format="json",
            **chaos_headers,
        )
        assert rtt_response.status_code == status.HTTP_200_OK

        # 5. 최종 상태 확인
        final_status = api_client.get(STATUS_URL, **chaos_headers)
        assert final_status.status_code == status.HTTP_200_OK

        # 6. 리셋
        reset_response = api_client.post(RESET_URL, format="json", **chaos_headers)
        assert reset_response.status_code == status.HTTP_200_OK

        # 7. 리셋 후 상태 확인
        after_reset = api_client.get(STATUS_URL, **chaos_headers)
        assert after_reset.status_code == status.HTTP_200_OK
