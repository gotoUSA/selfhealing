"""
API View Settings Integration Tests.

API View에서 Settings를 올바르게 사용하는지 테스트합니다.
Django REST Framework 컨텍스트가 필요하므로 전역 tests 폴더에 위치합니다.

Tests:
1. CanaryRolloutListView._get_completed_rollouts_limit()
2. AutoTuningHistoryView._get_export_limit()
3. HealingTimelineView._get_timeline_default_limit()
4. PostmortemGeneratorView._get_postmortem_history_limit() (views/postmortem.py)
5. GetHealingIncidentsView._get_incidents_default_limit() (views/postmortem.py)
"""

import os
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def reset_all_settings():
    """Reset all settings before and after each test."""
    from selfhealing.settings.canary import reset_canary_settings
    from selfhealing.settings.api_view import reset_api_view_settings

    reset_canary_settings()
    reset_api_view_settings()
    yield
    reset_canary_settings()
    reset_api_view_settings()


class TestCanaryViewSettingsIntegration:
    """Test api/django/views/canary.py Settings integration."""

    def test_get_completed_rollouts_limit_default(self):
        """Test CanaryRolloutListView._get_completed_rollouts_limit() default."""
        from selfhealing.api.django.views.canary import CanaryRolloutListView

        limit = CanaryRolloutListView._get_completed_rollouts_limit()
        assert limit == 20  # 기본값

    def test_env_override_completed_rollouts_limit(self):
        """Test environment variable override for completed_rollouts_limit."""
        from selfhealing.settings.canary import reset_canary_settings
        from selfhealing.api.django.views.canary import CanaryRolloutListView

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_CANARY_DEFAULT_COMPLETED_ROLLOUTS_LIMIT": "50",
            },
        ):
            reset_canary_settings()
            limit = CanaryRolloutListView._get_completed_rollouts_limit()
            assert limit == 50


class TestAutoTuningViewSettingsIntegration:
    """Test api/django/views/auto_tuning.py Settings integration."""

    def test_get_export_limit_default(self):
        """Test AutoTuningHistoryView._get_export_limit() default."""
        from selfhealing.api.django.views.auto_tuning import AutoTuningHistoryView

        limit = AutoTuningHistoryView._get_export_limit()
        assert limit == 1000  # 기본값

    def test_env_override_export_limit(self):
        """Test environment variable override for export_limit."""
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.auto_tuning import AutoTuningHistoryView

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_API_VIEW_AUTO_TUNING_EXPORT_LIMIT": "5000",
            },
        ):
            reset_api_view_settings()
            limit = AutoTuningHistoryView._get_export_limit()
            assert limit == 5000


class TestObservabilityViewSettingsIntegration:
    """Test api/django/views/xtest/observability.py and views/postmortem.py Settings integration."""

    def test_get_timeline_default_limit(self):
        """Test HealingTimelineView._get_timeline_default_limit() default."""
        from selfhealing.api.django.views.xtest.observability import HealingTimelineView

        limit = HealingTimelineView._get_timeline_default_limit()
        assert limit == 50  # 기본값

    def test_get_postmortem_history_limit(self):
        """Test PostmortemGeneratorView._get_postmortem_history_limit() default."""
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        limit = PostmortemGeneratorView._get_postmortem_history_limit()
        assert limit == 100  # 기본값

    def test_get_incidents_default_limit(self):
        """Test GetHealingIncidentsView._get_incidents_default_limit() default."""
        from selfhealing.api.django.views.postmortem import GetHealingIncidentsView

        limit = GetHealingIncidentsView._get_incidents_default_limit()
        assert limit == 10  # 기본값

    def test_env_override_timeline_limit(self):
        """Test environment variable override for timeline_default_limit."""
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.xtest.observability import HealingTimelineView

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_API_VIEW_XTEST_TIMELINE_DEFAULT_LIMIT": "100",
            },
        ):
            reset_api_view_settings()
            limit = HealingTimelineView._get_timeline_default_limit()
            assert limit == 100

    def test_env_override_postmortem_limit(self):
        """Test environment variable override for postmortem_history_limit."""
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.postmortem import PostmortemGeneratorView

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_API_VIEW_POSTMORTEM_HISTORY_LIMIT": "200",
            },
        ):
            reset_api_view_settings()
            limit = PostmortemGeneratorView._get_postmortem_history_limit()
            assert limit == 200

    def test_env_override_incidents_limit(self):
        """Test environment variable override for incidents_default_limit."""
        from selfhealing.settings.api_view import reset_api_view_settings
        from selfhealing.api.django.views.postmortem import GetHealingIncidentsView

        with mock.patch.dict(
            os.environ,
            {
                "SELFHEALING_API_VIEW_POSTMORTEM_INCIDENTS_DEFAULT_LIMIT": "25",
            },
        ):
            reset_api_view_settings()
            limit = GetHealingIncidentsView._get_incidents_default_limit()
            assert limit == 25
