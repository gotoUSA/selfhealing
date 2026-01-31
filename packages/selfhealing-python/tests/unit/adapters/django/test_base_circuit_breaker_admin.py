"""
BaseCircuitBreakerStateAdmin 단위 테스트.

selfhealing 패키지의 BaseCircuitBreakerStateAdmin 클래스 테스트입니다.
Django Admin 기본 클래스의 설정 및 메서드를 테스트합니다.
"""

import os
import pytest
from unittest.mock import MagicMock

# Django 설정 (테스트 환경에서 필요)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from selfhealing.adapters.django.admin import BaseCircuitBreakerStateAdmin


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def admin_class():
    """BaseCircuitBreakerStateAdmin 클래스를 반환합니다."""
    return BaseCircuitBreakerStateAdmin


@pytest.fixture
def admin_instance():
    """BaseCircuitBreakerStateAdmin 인스턴스를 반환합니다."""
    from django.contrib.admin.sites import AdminSite
    from shopping.models import CircuitBreakerState

    site = AdminSite()
    return BaseCircuitBreakerStateAdmin(model=CircuitBreakerState, admin_site=site)


# =============================================================================
# Display Methods Tests
# =============================================================================


class TestBaseCircuitBreakerAdminStateDisplay:
    """상태 표시 메서드 테스트."""

    def test_state_display_closed(self, admin_instance):
        """closed 상태일 때 녹색으로 표시."""
        obj = MagicMock()
        obj.state = "closed"
        obj.get_state_display.return_value = "Closed"

        result = admin_instance.state_display(obj)
        assert "green" in result
        assert "Closed" in result

    def test_state_display_open(self, admin_instance):
        """open 상태일 때 빨간색으로 표시."""
        obj = MagicMock()
        obj.state = "open"
        obj.get_state_display.return_value = "Open"

        result = admin_instance.state_display(obj)
        assert "red" in result
        assert "Open" in result

    def test_state_display_half_open(self, admin_instance):
        """half_open 상태일 때 주황색으로 표시."""
        obj = MagicMock()
        obj.state = "half_open"
        obj.get_state_display.return_value = "Half Open"

        result = admin_instance.state_display(obj)
        assert "orange" in result
        assert "Half Open" in result


class TestBaseCircuitBreakerAdminManualControlDisplay:
    """수동 제어 상태 표시 메서드 테스트."""

    def test_manually_controlled_true(self, admin_instance):
        """수동 제어 활성화 시 Manual 표시."""
        obj = MagicMock()
        obj.manually_controlled = True

        result = admin_instance.manually_controlled_display(obj)
        assert "Manual" in result
        assert "blue" in result

    def test_manually_controlled_false(self, admin_instance):
        """수동 제어 비활성화 시 Auto 표시."""
        obj = MagicMock()
        obj.manually_controlled = False

        result = admin_instance.manually_controlled_display(obj)
        assert "Auto" in result
        assert "gray" in result


# =============================================================================
# Configuration Tests
# =============================================================================


class TestBaseCircuitBreakerAdminConfiguration:
    """Admin 설정 테스트."""

    def test_list_display_fields(self, admin_class):
        """list_display 필드 확인."""
        expected_fields = [
            "service_name",
            "state_display",
            "failure_count",
            "success_count",
            "manually_controlled_display",
            "controlled_by_id",
            "opened_at",
            "updated_at",
        ]
        assert admin_class.list_display == expected_fields

    def test_list_filter_fields(self, admin_class):
        """list_filter 필드 확인."""
        expected_filters = [
            "state",
            "manually_controlled",
            "created_at",
        ]
        assert admin_class.list_filter == expected_filters

    def test_search_fields(self, admin_class):
        """search_fields 필드 확인."""
        assert "service_name" in admin_class.search_fields
        assert "control_reason" in admin_class.search_fields

    def test_ordering_descending(self, admin_class):
        """최신순 정렬 확인."""
        assert admin_class.ordering == ["-updated_at"]

    def test_actions_available(self, admin_class):
        """Admin actions 확인."""
        expected_actions = [
            "force_open_selected",
            "force_close_selected",
            "force_close_with_replay",
            "reset_selected",
        ]
        assert admin_class.actions == expected_actions

    def test_readonly_fields(self, admin_class):
        """readonly_fields 확인."""
        expected_readonly = [
            "failure_count",
            "success_count",
            "last_failure_at",
            "opened_at",
            "created_at",
            "updated_at",
        ]
        assert admin_class.readonly_fields == expected_readonly

    def test_fieldsets_structure(self, admin_class):
        """fieldsets 구조 확인."""
        fieldsets = admin_class.fieldsets

        section_names = [fs[0] for fs in fieldsets]
        assert "Service Information" in section_names
        assert "Counters" in section_names
        assert "Manual Control" in section_names
        assert "Timestamps" in section_names
