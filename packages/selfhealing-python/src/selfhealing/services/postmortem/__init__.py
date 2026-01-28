"""
Postmortem Services Package.

연쇄 CB 이벤트 병합, 알림 집계, 무결성 봉인 등 Postmortem 관련 서비스를 제공합니다.

Components:
- IncidentGroupManager: 연쇄 CB 이벤트를 IncidentGroup으로 병합
- NotificationAggregator: 알림 집계하여 Alert Storm 방지
- IntegritySealer: HashChain 기반 Postmortem 무결성 봉인
"""

from __future__ import annotations

from .incident_group import (
    IncidentGroup,
    IncidentGroupEntry,
    IncidentGroupManager,
    IncidentGroupStatus,
    get_incident_group_manager,
)
from .notification_aggregator import (
    IncidentSummaryNotification,
    NotificationAggregator,
    get_notification_aggregator,
)
from .integrity_sealer import (
    IntegritySealer,
    get_integrity_sealer,
)

__all__ = [
    # Incident Group
    "IncidentGroupManager",
    "IncidentGroup",
    "IncidentGroupEntry",
    "IncidentGroupStatus",
    "get_incident_group_manager",
    # Notification Aggregator
    "NotificationAggregator",
    "IncidentSummaryNotification",
    "get_notification_aggregator",
    # Integrity Sealer
    "IntegritySealer",
    "get_integrity_sealer",
]
