"""
Postmortem Services Package.

연쇄 CB 이벤트 병합, 알림 집계, 무결성 봉인, 타임라인 스냅샷 등 Postmortem 관련 서비스를 제공합니다.

Components:
- IncidentGroupManager: 연쇄 CB 이벤트를 IncidentGroup으로 병합
- NotificationAggregator: 알림 집계하여 Alert Storm 방지
- IntegritySealer: HashChain 기반 Postmortem 무결성 봉인
- PrometheusMetricsCollector: Prometheus에서 인시던트 기간 메트릭 수집
- IncidentLogBuffer: 에러 로그 버퍼링
- SnapshotBuilder: 타임라인 스냅샷 빌드
- DeploymentCorrelator: 배포 연관성 분석
"""

from __future__ import annotations

from .deployment_correlator import (
    CorrelationType,
    DeploymentCorrelationResult,
    DeploymentCorrelator,
    get_deployment_correlator,
    reset_deployment_correlator,
)
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
from .prometheus_collector import (
    PrometheusMetricsCollector,
    PrometheusQueryResult,
    PeakMetrics,
    get_prometheus_collector,
    reset_prometheus_collector,
)
from .log_buffer import (
    IncidentLogBuffer,
    IncidentLogHandler,
    CapturedLog,
    get_incident_log_buffer,
    reset_incident_log_buffer,
    setup_incident_log_handler,
)
from .snapshot_builder import (
    SnapshotBuilder,
    TimelineSnapshot,
    save_open_snapshot_to_redis,
    delete_open_snapshot_from_redis,
)

__all__ = [
    # Deployment Correlator
    "DeploymentCorrelator",
    "DeploymentCorrelationResult",
    "CorrelationType",
    "get_deployment_correlator",
    "reset_deployment_correlator",
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
    # Prometheus Collector
    "PrometheusMetricsCollector",
    "PrometheusQueryResult",
    "PeakMetrics",
    "get_prometheus_collector",
    "reset_prometheus_collector",
    # Log Buffer
    "IncidentLogBuffer",
    "IncidentLogHandler",
    "CapturedLog",
    "get_incident_log_buffer",
    "reset_incident_log_buffer",
    "setup_incident_log_handler",
    # Snapshot Builder
    "SnapshotBuilder",
    "TimelineSnapshot",
    "save_open_snapshot_to_redis",
    "delete_open_snapshot_from_redis",
]
