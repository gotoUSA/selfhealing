"""
Unit tests for NotificationAggregator.

Tests:
- 알림 등록 및 중복 방지
- 집계 윈도우 조건 확인
- 요약 알림 생성
- In-Memory Fallback
"""

from __future__ import annotations

import time
from unittest.mock import patch


class TestIncidentSummaryNotification:
    """IncidentSummaryNotification 테스트."""

    def test_from_dict_creates_notification(self):
        """딕셔너리에서 알림 생성."""
        from selfhealing.services.postmortem.notification_aggregator import (
            IncidentSummaryNotification,
        )

        data = {
            "total_incidents": 3,
            "affected_services": ["payment", "order", "inventory"],
            "total_downtime_seconds": 1800.0,
            "group_id": "INCGRP-test",
            "postmortem_links": ["/api/incidents/1/", "/api/incidents/2/"],
            "primary_incident_id": "AUTO-payment-20260128",
            "created_at": "2026-01-28T12:00:00+00:00",
        }

        notification = IncidentSummaryNotification.from_dict(data)

        assert notification.total_incidents == 3
        assert len(notification.affected_services) == 3
        assert notification.total_downtime_seconds == 1800.0
        assert notification.group_id == "INCGRP-test"

    def test_to_dict_serializes_notification(self):
        """알림을 딕셔너리로 변환."""
        from selfhealing.services.postmortem.notification_aggregator import (
            IncidentSummaryNotification,
        )

        notification = IncidentSummaryNotification(
            total_incidents=2,
            affected_services=["svc1", "svc2"],
            total_downtime_seconds=600.0,
            group_id=None,
            postmortem_links=[],
            primary_incident_id="AUTO-svc1",
            created_at="2026-01-28T12:00:00+00:00",
        )

        data = notification.to_dict()

        assert data["total_incidents"] == 2
        assert data["affected_services"] == ["svc1", "svc2"]


class TestNotificationAggregatorMemory:
    """NotificationAggregator In-Memory 모드 테스트."""

    def test_add_notification_queues_notification(self):
        """알림 등록 시 대기 큐에 추가."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        result = aggregator.add_notification(
            incident_id="AUTO-payment-20260128",
            service_name="payment",
            duration_seconds=300.0,
            postmortem_link="/api/incidents/AUTO-payment-20260128/",
            namespace="test",
        )

        assert result is True
        assert aggregator.get_pending_count("test") == 1

    def test_add_notification_prevents_duplicates(self):
        """중복 알림 등록 방지."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        # 첫 번째 등록
        aggregator.add_notification(
            incident_id="AUTO-payment-20260128",
            service_name="payment",
            duration_seconds=300.0,
            postmortem_link="/api/incidents/AUTO-payment-20260128/",
            namespace="test2",
        )

        # Flush하여 발송 완료 처리
        aggregator.flush_and_create_summary("test2")

        # 동일 ID로 재등록 시도
        result = aggregator.add_notification(
            incident_id="AUTO-payment-20260128",
            service_name="payment",
            duration_seconds=300.0,
            postmortem_link="/api/incidents/AUTO-payment-20260128/",
            namespace="test2",
        )

        assert result is False

    def test_should_flush_returns_false_initially(self):
        """초기에는 Flush 조건 미충족."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(
            window_seconds=60,
            use_redis=False,
        )

        aggregator.add_notification(
            incident_id="AUTO-test",
            service_name="test",
            duration_seconds=100.0,
            postmortem_link="/test/",
            namespace="test3",
        )

        assert aggregator.should_flush("test3") is False

    def test_should_flush_returns_true_after_window(self):
        """윈도우 경과 후 Flush 조건 충족."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(
            window_seconds=1,  # 1초 윈도우 (테스트용)
            use_redis=False,
        )

        aggregator.add_notification(
            incident_id="AUTO-test2",
            service_name="test",
            duration_seconds=100.0,
            postmortem_link="/test/",
            namespace="test4",
        )

        # time.time()을 1.1초 전진시켜 윈도우 만료를 시뮬레이션
        import selfhealing.services.postmortem.notification_aggregator as _agg_mod

        original_time = time.time()
        with patch.object(_agg_mod.time, "time", return_value=original_time + 1.1):
            assert aggregator.should_flush("test4") is True

    def test_flush_and_create_summary_returns_summary(self):
        """Flush 시 요약 알림 생성."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        aggregator.add_notification(
            incident_id="AUTO-payment",
            service_name="payment",
            duration_seconds=300.0,
            postmortem_link="/api/incidents/payment/",
            namespace="test5",
        )
        aggregator.add_notification(
            incident_id="AUTO-order",
            service_name="order",
            duration_seconds=200.0,
            postmortem_link="/api/incidents/order/",
            namespace="test5",
        )

        summary = aggregator.flush_and_create_summary("test5")

        assert summary is not None
        assert summary.total_incidents == 2
        assert set(summary.affected_services) == {"payment", "order"}
        assert summary.total_downtime_seconds == 500.0
        assert len(summary.postmortem_links) == 2

    def test_flush_clears_pending_queue(self):
        """Flush 후 대기 큐 비워짐."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        aggregator.add_notification(
            incident_id="AUTO-test3",
            service_name="test",
            duration_seconds=100.0,
            postmortem_link="/test/",
            namespace="test6",
        )

        aggregator.flush_and_create_summary("test6")

        assert aggregator.get_pending_count("test6") == 0

    def test_flush_returns_none_when_empty(self):
        """비어있을 때 Flush 시 None 반환."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        summary = aggregator.flush_and_create_summary("empty_namespace")

        assert summary is None

    def test_clear_removes_pending(self):
        """대기 큐 초기화."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        aggregator.add_notification(
            incident_id="AUTO-test4",
            service_name="test",
            duration_seconds=100.0,
            postmortem_link="/test/",
            namespace="test7",
        )

        aggregator.clear("test7")

        assert aggregator.get_pending_count("test7") == 0

    def test_group_id_aggregation(self):
        """동일 그룹 ID 집계."""
        from selfhealing.services.postmortem.notification_aggregator import (
            NotificationAggregator,
        )

        aggregator = NotificationAggregator(use_redis=False)

        aggregator.add_notification(
            incident_id="AUTO-payment",
            service_name="payment",
            duration_seconds=300.0,
            postmortem_link="/api/incidents/payment/",
            group_id="INCGRP-test",
            namespace="test8",
        )
        aggregator.add_notification(
            incident_id="AUTO-order",
            service_name="order",
            duration_seconds=200.0,
            postmortem_link="/api/incidents/order/",
            group_id="INCGRP-test",
            namespace="test8",
        )

        summary = aggregator.flush_and_create_summary("test8")

        assert summary.group_id == "INCGRP-test"
