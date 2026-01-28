"""
Unit Tests for X-Test-Mode Observability Views Domain-Free Conversion.

도메인 종속적인 하드코딩 값이 제거되었는지 검증:
- BlastRadiusTestView: affected_service 필수, check_services 동적 조회
- MultiServiceBlastRadiusView: test_services 동적 조회, 최소 서비스 검증
- RecordHealingEventView: 범용 서비스 이름 예시
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model

from selfhealing.api.django.views.xtest.observability import (
    BlastRadiusTestView,
    MultiServiceBlastRadiusView,
    RecordHealingEventView,
    _get_test_services,
)


@pytest.fixture
def api_client():
    """Create authenticated API client with staff user and required groups."""
    from django.contrib.auth.models import Group
    
    User = get_user_model()
    user = User.objects.create_user(
        username="xtest_admin",
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


@pytest.mark.django_db
class TestBlastRadiusTestViewDomainFree:
    """Tests for BlastRadiusTestView domain-free behavior."""

    def test_affected_service_required_returns_400_when_missing(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """affected_service 파라미터가 없으면 400 Bad Request 반환."""
        response = api_client.post(
            "/api/self-healing/xtest/blast-radius-test/",
            data={},  # affected_service 없음
            format="json",
            **chaos_headers,
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "affected_service" in response.data.get("error", "")

    def test_check_services_empty_triggers_dynamic_lookup(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """check_services가 비어있으면 CB 저장소에서 동적 조회."""
        # Mock CB service
        mock_state = MagicMock()
        mock_state.service_name = "test_service_b"
        mock_state.state = "closed"
        mock_state.failure_count = 0

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = [mock_state]
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True

        with (
            patch(
                "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.observability.add_healing_event"
            ),
            patch(
                "selfhealing.api.django.views.xtest.observability.collect_system_snapshot",
                return_value={},
            ),
        ):
            response = api_client.post(
                "/api/self-healing/xtest/blast-radius-test/",
                data={
                    "affected_service": "test_service_a",
                    "check_services": [],  # 빈 배열 - 동적 조회 트리거
                    "failure_count": 3,
                },
                format="json",
                **chaos_headers,
            )

        assert response.status_code == status.HTTP_200_OK
        # 동적 조회가 발생했는지 확인
        mock_cb_service.repository.get_all_states.assert_called()

    def test_check_services_with_values_skips_dynamic_lookup(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """check_services가 제공되면 동적 조회를 건너뜀."""
        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True

        with (
            patch(
                "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
                return_value=mock_cb_service,
            ),
            patch(
                "selfhealing.api.django.views.xtest.observability.add_healing_event"
            ),
            patch(
                "selfhealing.api.django.views.xtest.observability.collect_system_snapshot",
                return_value={},
            ),
        ):
            response = api_client.post(
                "/api/self-healing/xtest/blast-radius-test/",
                data={
                    "affected_service": "service_x",
                    "check_services": ["service_y", "service_z"],  # 명시적 목록
                    "failure_count": 3,
                },
                format="json",
                **chaos_headers,
            )

        assert response.status_code == status.HTTP_200_OK
        # 명시적 목록이 제공되었으므로 get_all_states가 호출되지 않아야 함
        mock_cb_service.repository.get_all_states.assert_not_called()


@pytest.mark.django_db
class TestMultiServiceBlastRadiusViewDomainFree:
    """Tests for MultiServiceBlastRadiusView domain-free behavior."""

    def test_test_services_empty_triggers_dynamic_lookup(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """test_services가 비어있으면 CB 저장소에서 동적 조회."""
        # Mock CB service with 2+ services
        mock_state_a = MagicMock()
        mock_state_a.service_name = "dynamic_service_a"
        mock_state_a.state = "closed"

        mock_state_b = MagicMock()
        mock_state_b.service_name = "dynamic_service_b"
        mock_state_b.state = "closed"

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = [
            mock_state_a,
            mock_state_b,
        ]
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True

        with patch(
            "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
            return_value=mock_cb_service,
        ):
            response = api_client.post(
                "/api/self-healing/xtest/multi-blast-radius/",
                data={
                    "test_services": [],  # 빈 배열 - 동적 조회 트리거
                    "failure_count": 3,
                },
                format="json",
                **chaos_headers,
            )

        assert response.status_code == status.HTTP_200_OK
        # 동적 조회가 발생했는지 확인
        mock_cb_service.repository.get_all_states.assert_called()
        assert response.data["total_services_tested"] == 2

    def test_minimum_two_services_required(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """최소 2개 서비스가 필요하며 미만일 경우 400 반환."""
        mock_cb_service = MagicMock()

        with patch(
            "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
            return_value=mock_cb_service,
        ):
            response = api_client.post(
                "/api/self-healing/xtest/multi-blast-radius/",
                data={
                    "test_services": ["only_one_service"],  # 1개만 제공
                    "failure_count": 3,
                },
                format="json",
                **chaos_headers,
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "At least 2 services" in response.data.get("error", "")

    def test_test_services_with_values_uses_provided_list(
        self, api_client, chaos_headers, mock_chaos_allowed
    ):
        """test_services가 제공되면 그 목록을 사용."""
        mock_cb_service = MagicMock()
        mock_cb_service.get_state.return_value = "closed"
        mock_cb_service.should_allow.return_value = True

        with patch(
            "selfhealing.services.circuit_breaker_service.get_circuit_breaker_service",
            return_value=mock_cb_service,
        ):
            response = api_client.post(
                "/api/self-healing/xtest/multi-blast-radius/",
                data={
                    "test_services": ["custom_a", "custom_b", "custom_c"],
                    "failure_count": 2,
                },
                format="json",
                **chaos_headers,
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_services_tested"] == 3
        # 명시적 서비스가 매트릭스에 포함되었는지 확인
        assert "custom_a" in response.data["matrix"]
        assert "custom_b" in response.data["matrix"]
        assert "custom_c" in response.data["matrix"]


class TestGetTestServicesHelper:
    """Tests for _get_test_services helper function."""

    def test_returns_provided_services_when_not_empty(self):
        """제공된 서비스 목록이 있으면 그대로 반환."""
        mock_cb_service = MagicMock()
        result = _get_test_services(mock_cb_service, ["svc_a", "svc_b"])

        assert result == ["svc_a", "svc_b"]
        mock_cb_service.repository.get_all_states.assert_not_called()

    def test_returns_all_services_from_cb_when_empty(self):
        """빈 목록이면 CB에서 모든 서비스 조회."""
        mock_state_1 = MagicMock()
        mock_state_1.service_name = "cb_service_1"
        mock_state_2 = MagicMock()
        mock_state_2.service_name = "cb_service_2"

        mock_cb_service = MagicMock()
        mock_cb_service.repository.get_all_states.return_value = [
            mock_state_1,
            mock_state_2,
        ]

        result = _get_test_services(mock_cb_service, [])

        assert result == ["cb_service_1", "cb_service_2"]
        mock_cb_service.repository.get_all_states.assert_called_once()


class TestDocstringsAreDomainFree:
    """Tests to verify docstrings use generic service names."""

    def test_blast_radius_test_view_docstring_is_domain_free(self):
        """BlastRadiusTestView Docstring에 도메인 종속 이름이 없어야 함."""
        docstring = BlastRadiusTestView.__doc__
        # 도메인 종속적인 이름이 없어야 함
        assert "payment" not in docstring.lower()
        assert "cart" not in docstring.lower()
        assert "auth" not in docstring.lower()

    def test_multi_service_blast_radius_view_docstring_is_domain_free(self):
        """MultiServiceBlastRadiusView Docstring에 도메인 종속 이름이 없어야 함."""
        docstring = MultiServiceBlastRadiusView.__doc__
        # 도메인 종속적인 이름이 없어야 함
        assert "external_api" not in docstring.lower()
        assert "cache" not in docstring.lower()

    def test_record_healing_event_view_docstring_uses_generic_name(self):
        """RecordHealingEventView Docstring에 범용 이름(my_service) 사용."""
        docstring = RecordHealingEventView.__doc__
        # 범용 예시 "my_service"가 있어야 함
        assert "my_service" in docstring
        # 도메인 종속적인 "payment" 없어야 함
        assert "payment" not in docstring.lower()
