"""
CascadeEvent API is_test 필터 및 응답 필드 단위 테스트.

테스트 대상:
- CascadeEventListView 응답에 is_test 필드 포함
- ?is_test=true 필터로 테스트 이벤트만 조회
- ?is_test=false 필터로 운영 이벤트만 조회
- is_test 파라미터 생략 시 전체 이벤트 조회
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# Django 설정 구성 (테스트용)
import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "rest_framework",
        ],
        REST_FRAMEWORK={
            "DEFAULT_PERMISSION_CLASSES": [
                "rest_framework.permissions.AllowAny",
            ],
        },
        SECRET_KEY="test-secret-key-for-cascade-api-is-test-filter",
    )
    django.setup()

from datetime import datetime, timezone

from selfhealing.audit.cascade_event import (
    CascadeEffect,
    CascadeEvent,
    CascadeTrigger,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def production_cascade_event():
    """운영 환경 CascadeEvent (is_test=False)."""
    return CascadeEvent(
        id="cascade-prod-001",
        trigger=CascadeTrigger(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            event_id="evt-prod-001",
            details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
            triggered_by="system",
        ),
        effects=[
            CascadeEffect(
                action_type="governance_strict",
                event_id="effect-prod-001",
                success=True,
                caused_by="evt-prod-001",
                details={},
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        namespace="seoul",
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_test=False,
    )


@pytest.fixture
def test_cascade_event():
    """테스트 환경 CascadeEvent (is_test=True)."""
    return CascadeEvent(
        id="cascade-test-001",
        trigger=CascadeTrigger(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            event_id="evt-test-001",
            details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
            triggered_by="x-test-mode",
        ),
        effects=[
            CascadeEffect(
                action_type="governance_strict",
                event_id="effect-test-001",
                success=True,
                caused_by="evt-test-001",
                details={},
                executed_at=datetime.now(timezone.utc).isoformat(),
            )
        ],
        namespace="seoul",
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_test=True,
    )


# =============================================================================
# CascadeEventListView is_test 응답 필드 테스트
# =============================================================================


class TestCascadeEventListViewIsTestResponseField:
    """CascadeEventListView 응답에 is_test 필드 포함 테스트."""

    def test_response_includes_is_test_field_for_production_event(self, production_cascade_event):
        """운영 이벤트 응답에 is_test=false 포함."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [production_cascade_event]
        mock_auditor.get_event_count.return_value = 1

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert "is_test" in response.data["events"][0]
            assert response.data["events"][0]["is_test"] is False

    def test_response_includes_is_test_field_for_test_event(self, test_cascade_event):
        """테스트 이벤트 응답에 is_test=true 포함."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [test_cascade_event]
        mock_auditor.get_event_count.return_value = 1

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert "is_test" in response.data["events"][0]
            assert response.data["events"][0]["is_test"] is True


# =============================================================================
# CascadeEventListView is_test 필터 테스트
# =============================================================================


class TestCascadeEventListViewIsTestFilter:
    """CascadeEventListView is_test 쿼리 파라미터 필터 테스트."""

    def test_filter_is_test_false_returns_production_only(self, production_cascade_event, test_cascade_event):
        """?is_test=false 필터로 운영 이벤트만 조회."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [
            production_cascade_event,
            test_cascade_event,
        ]
        mock_auditor.get_event_count.return_value = 2

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "is_test": "false"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["id"] == "cascade-prod-001"
            assert response.data["events"][0]["is_test"] is False

    def test_filter_is_test_true_returns_test_only(self, production_cascade_event, test_cascade_event):
        """?is_test=true 필터로 테스트 이벤트만 조회."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [
            production_cascade_event,
            test_cascade_event,
        ]
        mock_auditor.get_event_count.return_value = 2

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "is_test": "true"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["id"] == "cascade-test-001"
            assert response.data["events"][0]["is_test"] is True

    def test_no_is_test_filter_returns_all_events(self, production_cascade_event, test_cascade_event):
        """is_test 파라미터 생략 시 전체 이벤트 조회."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [
            production_cascade_event,
            test_cascade_event,
        ]
        mock_auditor.get_event_count.return_value = 2

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert len(response.data["events"]) == 2

    def test_is_test_filter_case_insensitive(self, production_cascade_event, test_cascade_event):
        """is_test 필터는 대소문자를 구분하지 않음."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [
            production_cascade_event,
            test_cascade_event,
        ]
        mock_auditor.get_event_count.return_value = 2

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "is_test": "TRUE"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["is_test"] is True

    def test_is_test_filter_with_trigger_type_filter(self, production_cascade_event, test_cascade_event):
        """is_test 필터와 trigger_type 필터 조합."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        # trigger_type이 다른 테스트 이벤트 추가
        manual_test_event = CascadeEvent(
            id="cascade-manual-test",
            trigger=CascadeTrigger(
                trigger_type="MANUAL_ACTIVATION",
                event_id="evt-manual",
                details={},
            ),
            effects=[],
            namespace="seoul",
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_test=True,
        )

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [
            production_cascade_event,
            test_cascade_event,
            manual_test_event,
        ]
        mock_auditor.get_event_count.return_value = 3

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {
                "namespace": "seoul",
                "is_test": "true",
                "trigger_type": "EMERGENCY_LEVEL_CHANGED",
            }

            response = view.get(mock_request)

            assert response.status_code == 200
            # is_test=true AND trigger_type=EMERGENCY_LEVEL_CHANGED
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["id"] == "cascade-test-001"


# =============================================================================
# Edge Cases
# =============================================================================


class TestCascadeEventListViewIsTestEdgeCases:
    """is_test 필터 엣지 케이스 테스트."""

    def test_empty_result_when_no_matching_events(self, production_cascade_event):
        """필터 조건에 맞는 이벤트가 없을 때 빈 목록 반환."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [production_cascade_event]
        mock_auditor.get_event_count.return_value = 1

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "is_test": "true"}

            response = view.get(mock_request)

            assert response.status_code == 200
            assert len(response.data["events"]) == 0

    def test_invalid_is_test_value_treated_as_false(self, test_cascade_event):
        """유효하지 않은 is_test 값은 false로 처리."""
        from selfhealing.api.django.views.cascade import CascadeEventListView

        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [test_cascade_event]
        mock_auditor.get_event_count.return_value = 1

        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "is_test": "invalid"}

            response = view.get(mock_request)

            assert response.status_code == 200
            # "invalid".lower() == "true" → False, 따라서 is_test=False로 필터
            assert len(response.data["events"]) == 0
