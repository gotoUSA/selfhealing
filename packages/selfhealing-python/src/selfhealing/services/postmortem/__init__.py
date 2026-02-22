"""
Postmortem Services Package.

연쇄 CB 이벤트 병합, 알림 집계, 무결성 봉인, 타임라인 스냅샷 등 Postmortem 관련 서비스를 제공합니다.

Components:
- store: 인시던트 저장/조회/분산 락 (postmortem_store.py에서 이전)
- IncidentGroupManager: 연쇄 CB 이벤트를 IncidentGroup으로 병합
- NotificationAggregator: 알림 집계하여 Alert Storm 방지
- IntegritySealer: HashChain 기반 Postmortem 무결성 봉인
- PrometheusMetricsCollector: Prometheus에서 인시던트 기간 메트릭 수집
- IncidentLogBuffer: 에러 로그 버퍼링
- SnapshotBuilder: 타임라인 스냅샷 빌드
- DeploymentCorrelator: 배포 연관성 분석
- PostmortemDeepLinkBuilder: Postmortem 딥링크 URL 생성
- PostmortemNotifier: Postmortem 알림 발송
"""

from __future__ import annotations

from .deep_links import (
    PostmortemDeepLinkBuilder,
    PostmortemDeepLinks,
    get_postmortem_deep_link_builder,
    reset_postmortem_deep_link_builder,
)
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
from .integrity_sealer import (
    IntegritySealer,
    get_integrity_sealer,
)
from .log_buffer import (
    CapturedLog,
    IncidentLogBuffer,
    IncidentLogHandler,
    get_incident_log_buffer,
    reset_incident_log_buffer,
    setup_incident_log_handler,
)
from .notification_aggregator import (
    IncidentSummaryNotification,
    NotificationAggregator,
    get_notification_aggregator,
)
from .notifier import (
    PostmortemNotificationConfig,
    PostmortemNotificationPayload,
    PostmortemNotifier,
    SlackBlockKitBuilder,
    get_postmortem_notifier,
    reset_postmortem_notifier,
)
from .prometheus_collector import (
    PeakMetrics,
    PrometheusMetricsCollector,
    PrometheusQueryResult,
    get_prometheus_collector,
    reset_prometheus_collector,
)
from .revision import (
    PostmortemRevision,
    PostmortemRevisionManager,
    RevisionChangeType,
    RevisionDiff,
    compute_diff,
    get_postmortem_revision_manager,
    reset_postmortem_revision_manager,
)
from .snapshot_builder import (
    SnapshotBuilder,
    TimelineSnapshot,
    delete_open_snapshot_from_redis,
    save_open_snapshot_to_redis,
)
from .store import (
    LOCK_KEY_POSTMORTEM_GENERATE,
    LOCK_KEY_POSTMORTEM_GROUP,
    LOCK_TTL_POSTMORTEM_GENERATE,
    LOCK_TTL_POSTMORTEM_GROUP,
    acquire_group_close_lock,
    add_healing_incident,
    add_healing_incident_with_lock,
    build_timeline,
    clear_healing_incidents,
    collect_service_states,
    generate_postmortem_data,
    get_db_persistence_enabled,
    get_healing_incidents,
    get_healing_incidents_count,
    get_incident_by_id,
    set_db_persistence_enabled,
    update_incident_fields,
)

__all__ = [
    # Store (from postmortem_store.py → postmortem/store.py)
    "add_healing_incident",
    "add_healing_incident_with_lock",
    "get_healing_incidents",
    "get_healing_incidents_count",
    "get_incident_by_id",
    "update_incident_fields",
    "clear_healing_incidents",
    "set_db_persistence_enabled",
    "get_db_persistence_enabled",
    "acquire_group_close_lock",
    "LOCK_KEY_POSTMORTEM_GENERATE",
    "LOCK_KEY_POSTMORTEM_GROUP",
    "LOCK_TTL_POSTMORTEM_GENERATE",
    "LOCK_TTL_POSTMORTEM_GROUP",
    "collect_service_states",
    "build_timeline",
    "generate_postmortem_data",
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
    # Revision Manager
    "RevisionChangeType",
    "RevisionDiff",
    "PostmortemRevision",
    "PostmortemRevisionManager",
    "compute_diff",
    "get_postmortem_revision_manager",
    "reset_postmortem_revision_manager",
    # Deep Links
    "PostmortemDeepLinks",
    "PostmortemDeepLinkBuilder",
    "get_postmortem_deep_link_builder",
    "reset_postmortem_deep_link_builder",
    # Notifier
    "PostmortemNotificationConfig",
    "PostmortemNotificationPayload",
    "SlackBlockKitBuilder",
    "PostmortemNotifier",
    "get_postmortem_notifier",
    "reset_postmortem_notifier",
]
