"""
Postmortem Deep Links & CascadeEvent Integration Tests.

generate_postmortem_data()에서 deep_links, cascade_event_id,
causation_chain, evidence_hash 필드가 올바르게 생성되는지 테스트합니다.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest import mock

import pytest


# =============================================================================
# 공통 Fixture: generate_postmortem_data의 무거운 의존성 Mock
# =============================================================================


@pytest.fixture(autouse=True)
def mock_heavy_dependencies():
    """
    generate_postmortem_data 함수의 무거운 외부 의존성을 mock합니다.

    이 fixture는 단위 테스트 성능을 위해 DB 연결, Redis 연결 등
    실제 외부 서비스가 필요한 부분을 mock으로 대체합니다.
    """
    with (
        mock.patch("selfhealing.services.postmortem.deployment_correlator.get_deployment_correlator") as mock_correlator,
        mock.patch("selfhealing.services.postmortem.snapshot_builder.SnapshotBuilder") as mock_snapshot,
        mock.patch("selfhealing.services.throttle.postmortem.collect_throttle_postmortem_data") as mock_throttle,
    ):
        # deployment_correlator: disabled로 설정
        mock_correlator.return_value.is_enabled.return_value = False
        mock_correlator.return_value.get_deployments_for_postmortem.return_value = None
        mock_correlator.return_value.get_deployment_timeline_events.return_value = []

        # snapshot_builder: 빈 dict 반환
        mock_snapshot.return_value.build_dict.return_value = {}

        # throttle_data: 빈 dict 반환
        mock_throttle.return_value = {}

        yield


class TestGeneratePostmortemDataDeepLinks:
    """generate_postmortem_data()의 deep_links 통합 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )

        reset_postmortem_deep_link_builder()
        yield
        reset_postmortem_deep_link_builder()

    def test_deep_links_field_exists_in_postmortem_data(self):
        """generate_postmortem_data 반환값에 deep_links 필드가 존재."""
        from selfhealing.services.postmortem_store import generate_postmortem_data

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "circuit_breaker_opened",
                "details": {"service_name": "payment_service"},
            },
            {
                "timestamp": "2026-01-28T10:30:00Z",
                "event_type": "circuit_breaker_closed",
                "details": {"service_name": "payment_service"},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-001",
            timeline=timeline,
            affected=["payment_service"],
            unaffected=["order_service"],
            fast_fail_count=3,
            snapshot={"status": "healthy"},
            service_name="payment_service",
            current_time="2026-01-28T11:00:00Z",
        )

        assert "deep_links" in result
        assert isinstance(result["deep_links"], dict)

    @mock.patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor")
    def test_cascade_event_fields_exist_in_postmortem_data(self, mock_get_auditor):
        """generate_postmortem_data 반환값에 CascadeEvent 관련 필드가 존재."""
        # Mock auditor가 빈 결과 반환
        mock_auditor = mock.MagicMock()
        mock_auditor.get_recent_events.return_value = []
        mock_get_auditor.return_value = mock_auditor

        from selfhealing.services.postmortem_store import generate_postmortem_data

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "circuit_breaker_opened",
                "details": {},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-002",
            timeline=timeline,
            affected=["test_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            current_time="2026-01-28T11:00:00Z",
        )

        assert "cascade_event_id" in result
        assert "causation_chain" in result
        assert "evidence_hash" in result
        # CascadeEvent가 없는 경우 None/빈 리스트
        assert result["cascade_event_id"] is None
        assert result["causation_chain"] == []
        assert result["evidence_hash"] is None

    @mock.patch.dict(
        os.environ,
        {
            "POSTMORTEM_BASE_URL": "https://pm.internal",
            "CB_DASHBOARD_URL": "https://grafana.internal/d/cb",
            "CB_RUNBOOK_URL": "https://docs.internal/runbook",
        },
    )
    def test_deep_links_populated_with_env_vars(self):
        """환경변수 설정 시 deep_links에 URL이 생성됨."""
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )
        from selfhealing.services.postmortem_store import generate_postmortem_data

        # 환경변수가 적용되도록 빌더 리셋
        reset_postmortem_deep_link_builder()

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "circuit_breaker_opened",
                "details": {},
            },
            {
                "timestamp": "2026-01-28T10:30:00Z",
                "event_type": "circuit_breaker_closed",
                "details": {},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-003",
            timeline=timeline,
            affected=["payment_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            service_name="payment_service",
            current_time="2026-01-28T11:00:00Z",
        )

        deep_links = result["deep_links"]
        assert deep_links["postmortem_url"] is not None
        assert "PM-TEST-003" in deep_links["postmortem_url"]
        assert deep_links["dashboard_url"] is not None
        assert "payment_service" in deep_links["dashboard_url"]
        assert deep_links["runbook_url"] is not None

    @mock.patch("selfhealing.services.postmortem.deep_links.get_postmortem_deep_link_builder")
    def test_deep_links_builder_called_with_correct_params(self, mock_get_builder):
        """PostmortemDeepLinkBuilder가 올바른 파라미터로 호출됨."""
        from selfhealing.services.postmortem_store import generate_postmortem_data

        mock_builder = mock.MagicMock()
        mock_links = mock.MagicMock()
        mock_links.to_dict.return_value = {"postmortem_url": "https://test.url"}
        mock_builder.build_postmortem_links.return_value = mock_links
        mock_get_builder.return_value = mock_builder

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "circuit_breaker_opened",
                "details": {},
            },
        ]

        generate_postmortem_data(
            incident_id="PM-TEST-004",
            timeline=timeline,
            affected=["test_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            service_name="test_service",
            current_time="2026-01-28T11:00:00Z",
        )

        # build_postmortem_links가 올바른 인자로 호출되었는지 확인
        mock_builder.build_postmortem_links.assert_called_once()
        call_kwargs = mock_builder.build_postmortem_links.call_args.kwargs
        assert call_kwargs["incident_id"] == "PM-TEST-004"
        assert call_kwargs["service_name"] == "test_service"


class TestGeneratePostmortemDataCascadeEvent:
    """generate_postmortem_data()의 CascadeEvent 통합 테스트."""

    @mock.patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor")
    def test_cascade_event_found_by_service_name(self, mock_get_auditor):
        """서비스명으로 CascadeEvent를 찾아서 연결."""
        from selfhealing.audit.cascade_event import (
            CascadeEffect,
            CascadeEvent,
            CascadeTrigger,
        )
        from selfhealing.services.postmortem_store import generate_postmortem_data

        # Mock CascadeEvent 생성
        mock_event = CascadeEvent(
            id="cascade-test-123",
            trigger=CascadeTrigger(
                trigger_type="CIRCUIT_BREAKER_OPENED",
                event_id="evt-001",
                details={"service_name": "payment_service"},
            ),
            effects=[
                CascadeEffect(
                    event_id="evt-002",
                    action_type="GOVERNANCE_STRICT",
                    caused_by="evt-001",
                    success=True,
                    target="payment_service",
                )
            ],
            namespace="default",
            timestamp="2026-01-28T10:00:00Z",
            current_hash="sha256:abcdef123456",
        )

        mock_auditor = mock.MagicMock()
        mock_auditor.get_recent_events.return_value = [mock_event]
        mock_get_auditor.return_value = mock_auditor

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "circuit_breaker_opened",
                "details": {},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-005",
            timeline=timeline,
            affected=["payment_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            service_name="payment_service",
            current_time="2026-01-28T11:00:00Z",
        )

        assert result["cascade_event_id"] == "cascade-test-123"
        assert result["causation_chain"] == ["evt-001", "evt-002"]
        assert result["evidence_hash"] == "sha256:abcdef123456"

    @mock.patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor")
    def test_cascade_event_not_found(self, mock_get_auditor):
        """관련 CascadeEvent가 없는 경우 필드가 None/빈 값."""
        mock_auditor = mock.MagicMock()
        mock_auditor.get_recent_events.return_value = []
        mock_get_auditor.return_value = mock_auditor

        from selfhealing.services.postmortem_store import generate_postmortem_data

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "test_event",
                "details": {},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-006",
            timeline=timeline,
            affected=["unknown_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            current_time="2026-01-28T11:00:00Z",
        )

        assert result["cascade_event_id"] is None
        assert result["causation_chain"] == []
        assert result["evidence_hash"] is None

    @mock.patch("selfhealing.services.postmortem.deep_links.get_postmortem_deep_link_builder")
    @mock.patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor")
    def test_cascade_event_auditor_import_error_handled(
        self,
        mock_get_auditor,
        mock_deep_links,
    ):
        """CascadeAuditor import 실패 시 에러 없이 처리."""
        mock_get_auditor.side_effect = ImportError("Module not found")
        mock_deep_links.return_value.build_postmortem_links.return_value.to_dict.return_value = {}

        from selfhealing.services.postmortem_store import generate_postmortem_data

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "test_event",
                "details": {},
            },
        ]

        # ImportError가 발생해도 에러 없이 실행되어야 함
        result = generate_postmortem_data(
            incident_id="PM-TEST-007",
            timeline=timeline,
            affected=["test_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            current_time="2026-01-28T11:00:00Z",
        )

        # 필드가 기본값으로 설정됨
        assert result["cascade_event_id"] is None
        assert result["causation_chain"] == []
        assert result["evidence_hash"] is None


class TestDeepLinksWithCascadeEvent:
    """deep_links에 CascadeEvent 증적 링크 포함 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후로 싱글톤 리셋."""
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )

        reset_postmortem_deep_link_builder()
        yield
        reset_postmortem_deep_link_builder()

    @mock.patch.dict(
        os.environ,
        {
            "AUDIT_EVIDENCE_BASE_URL": "https://audit.internal/evidence",
        },
    )
    @mock.patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor")
    def test_audit_evidence_link_generated_with_cascade_event(self, mock_get_auditor):
        """CascadeEvent가 있을 때 audit_evidence_link가 생성됨."""
        from selfhealing.audit.cascade_event import (
            CascadeEffect,
            CascadeEvent,
            CascadeTrigger,
        )
        from selfhealing.services.postmortem.deep_links import (
            reset_postmortem_deep_link_builder,
        )
        from selfhealing.services.postmortem_store import generate_postmortem_data

        reset_postmortem_deep_link_builder()

        mock_event = CascadeEvent(
            id="cascade-evidence-001",
            trigger=CascadeTrigger(
                trigger_type="EMERGENCY_LEVEL_CHANGED",
                event_id="evt-001",
                details={"service_name": "payment_service"},
            ),
            effects=[],
            namespace="default",
            timestamp="2026-01-28T10:00:00Z",
            current_hash="sha256:evidence123",
        )

        mock_auditor = mock.MagicMock()
        mock_auditor.get_recent_events.return_value = [mock_event]
        mock_get_auditor.return_value = mock_auditor

        timeline = [
            {
                "timestamp": "2026-01-28T10:00:00Z",
                "event_type": "emergency_activated",
                "details": {},
            },
        ]

        result = generate_postmortem_data(
            incident_id="PM-TEST-008",
            timeline=timeline,
            affected=["payment_service"],
            unaffected=[],
            fast_fail_count=0,
            snapshot={},
            service_name="payment_service",
            current_time="2026-01-28T11:00:00Z",
        )

        deep_links = result["deep_links"]
        assert deep_links.get("audit_evidence_link") is not None
        assert "cascade-evidence-001" in deep_links["audit_evidence_link"]
        assert "evidence123" in deep_links["audit_evidence_link"]
