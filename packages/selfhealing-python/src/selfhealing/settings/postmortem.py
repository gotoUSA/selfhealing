"""
Post-mortem Settings - Pydantic v2.

Post-mortem 리포트 생성 및 자동 트리거 관련 설정입니다.

X-Test 모듈에서 분리된 독립적인 설정으로, 프로덕션 환경에서도 사용됩니다.

Environment Variables:
    SELFHEALING_POSTMORTEM_HISTORY_LIMIT=100
    SELFHEALING_POSTMORTEM_AUTO_ENABLED=false
    SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION=30

Reference:
- docs/self_healing/middleware_system/134_POSTMORTEM_NEW_MODULE_STRUCTURE.md
"""

from __future__ import annotations

import os
import warnings

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class PostmortemSettings(BaseSettings):
    """
    Post-mortem 리포트 생성 및 자동 트리거 설정.

    히스토리 조회:
    - history_limit: Post-mortem 생성 시 조회할 이벤트 수 (100)

    자동 생성:
    - auto_enabled: CB CLOSED 시 자동 Post-mortem 생성 (False)
    - auto_min_duration: 자동 생성 최소 인시던트 지속 시간 (30초)

    알림:
    - notification_enabled: Post-mortem 생성 시 알림 발송 (True)
    - notification_min_duration: 알림 발송 최소 인시던트 지속 시간 (60초)

    인시던트 목록:
    - incidents_default_limit: 인시던트 목록 조회 기본 limit (10)
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_POSTMORTEM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # History Limit - Post-mortem 생성 시 이벤트 조회 개수
    # ==========================================================================
    history_limit: int = Field(
        default=100,
        ge=50,
        le=500,
        description="Post-mortem 생성 시 조회할 이벤트 수",
    )

    # ==========================================================================
    # Auto Generation - CB CLOSED 시 자동 Post-mortem 생성
    # ==========================================================================
    auto_enabled: bool = Field(
        default=False,
        description="CB CLOSED 시 자동 Post-mortem 생성 활성화",
    )

    auto_min_duration: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="자동 Post-mortem 생성 최소 인시던트 지속 시간 (초)",
    )

    # ==========================================================================
    # Notification - Post-mortem 생성 시 알림 발송
    # ==========================================================================
    notification_enabled: bool = Field(
        default=True,
        description="Post-mortem 생성 시 알림 발송 활성화",
    )

    notification_min_duration: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Post-mortem 알림 발송 최소 인시던트 지속 시간 (초)",
    )

    # ==========================================================================
    # Incidents List - 인시던트 목록 조회
    # ==========================================================================
    incidents_default_limit: int = Field(
        default=10,
        ge=5,
        le=100,
        description="인시던트 목록 조회 기본 limit",
    )

    # ==========================================================================
    # Incident Group - 연쇄 CB 이벤트 그룹핑
    # ==========================================================================
    incident_group_enabled: bool = Field(
        default=True,
        description="연쇄 CB 이벤트 그룹핑 활성화",
    )

    incident_group_window_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        description="그룹핑 윈도우 크기 (초, 기본 10분)",
    )

    incident_group_inactivity_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        description="비활성 종료 시간 (초, 기본 2분)",
    )

    incident_group_min_count: int = Field(
        default=2,
        ge=1,
        le=100,
        description="그룹화 최소 인시던트 수",
    )

    # ==========================================================================
    # Notification Aggregation - 알림 집계 (Alert Storm 방지)
    # ==========================================================================
    notification_aggregation_enabled: bool = Field(
        default=True,
        description="알림 집계 활성화",
    )

    notification_aggregation_window_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="알림 집계 윈도우 크기 (초)",
    )

    notification_aggregation_max_wait_seconds: int = Field(
        default=300,
        ge=60,
        le=600,
        description="알림 최대 대기 시간 (초)",
    )

    # ==========================================================================
    # Timeline Snapshot - Postmortem 타임라인 스냅샷 보존
    # ==========================================================================

    # Prometheus 쿼리 설정
    snapshot_prometheus_enabled: bool = Field(
        default=True,
        description="Prometheus 피크 메트릭 쿼리 활성화",
    )

    snapshot_prometheus_url: str = Field(
        default="http://prometheus:9090",
        description="Prometheus 서버 URL",
    )

    snapshot_prometheus_timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Prometheus 쿼리 타임아웃 (초)",
    )

    # 로그 수집 설정
    snapshot_logs_enabled: bool = Field(
        default=True,
        description="에러 로그 수집 활성화",
    )

    snapshot_logs_max_count: int = Field(
        default=50,
        ge=10,
        le=200,
        description="수집할 최대 에러 로그 개수",
    )

    snapshot_logs_max_length: int = Field(
        default=500,
        ge=100,
        le=2000,
        description="로그 메시지 최대 길이",
    )

    # Grafana 대시보드 링크 설정
    snapshot_grafana_base_url: str = Field(
        default="http://grafana:3000",
        description="Grafana 서버 URL",
    )

    snapshot_grafana_dashboard_uid: str = Field(
        default="selfhealing",
        description="Grafana 대시보드 UID",
    )

    # ==========================================================================
    # Deployment Correlator - 배포 연관성 분석
    # ==========================================================================
    deployment_correlator_enabled: bool = Field(
        default=True,
        description="배포 연관성 분석 기능 활성화",
    )

    deployment_adapter: str = Field(
        default="mock",
        description="배포 어댑터 선택 (mock/kubernetes)",
    )

    deployment_pre_window_minutes: int = Field(
        default=60,
        ge=10,
        le=180,
        description="인시던트 전 배포 조회 범위 (분)",
    )

    deployment_post_window_minutes: int = Field(
        default=30,
        ge=5,
        le=60,
        description="인시던트 후 배포 조회 범위 (분)",
    )

    # ==========================================================================
    # Revision/Versioning - Postmortem 리비전(버전) 관리
    # ==========================================================================
    versioning_enabled: bool = Field(
        default=True,
        description="Postmortem 버전 관리 활성화",
    )

    max_revisions: int = Field(
        default=50,
        ge=10,
        le=200,
        description="Postmortem당 최대 리비전 수",
    )

    auto_seal_days: int = Field(
        default=30,
        ge=0,
        le=365,
        description="자동 봉인 일수 (0=비활성화)",
    )

    revision_storage: str = Field(
        default="hybrid",
        description="리비전 저장소 유형 (redis/postgresql/hybrid)",
    )

    # ==========================================================================
    # Deep Links - Postmortem 딥링크 URL 설정
    # ==========================================================================
    deep_links_enabled: bool = Field(
        default=True,
        description="딥링크 생성 활성화",
    )

    postmortem_base_url: str = Field(
        default="",
        description="Postmortem 상세 페이지 기본 URL",
    )

    postmortem_timeline_url: str = Field(
        default="",
        description="Postmortem 타임라인 뷰 URL",
    )

    audit_log_base_url: str = Field(
        default="",
        description="감사 로그 UI 기본 URL",
    )

    audit_evidence_base_url: str = Field(
        default="",
        description="CascadeEvent 증적 페이지 기본 URL",
    )

    # ==========================================================================
    # Notification Channels - 알림 채널 설정
    # ==========================================================================
    slack_webhook_url: str = Field(
        default="",
        description="Postmortem 알림용 Slack Webhook URL",
    )

    notification_channels: str = Field(
        default="slack",
        description="활성화할 알림 채널 (쉼표 구분)",
    )

    # ==========================================================================
    # CascadeEvent Integration - 감사 증적 연결
    # ==========================================================================
    cascade_event_integration_enabled: bool = Field(
        default=True,
        description="CascadeEvent 감사 증적 연결 활성화",
    )


# ==========================================================================
# Singleton 관리
# ==========================================================================
_postmortem_settings: PostmortemSettings | None = None


def get_postmortem_settings() -> PostmortemSettings:
    """Get cached PostmortemSettings instance."""
    global _postmortem_settings
    if _postmortem_settings is None:
        _postmortem_settings = PostmortemSettings()
    return _postmortem_settings


def reset_postmortem_settings() -> None:
    """Reset cached settings (for testing)."""
    global _postmortem_settings
    _postmortem_settings = None


# ==========================================================================
# Deprecated Alias (하위 호환성)
# ==========================================================================


def _get_deprecated_setting(old_name: str, new_name: str, default_value):
    """Deprecated 환경 변수에서 값을 가져오고 경고 출력."""
    env_value = os.getenv(f"SELFHEALING_{old_name}")
    if env_value is not None:
        warnings.warn(
            f"SELFHEALING_{old_name} is deprecated. " f"Use SELFHEALING_POSTMORTEM_{new_name.upper()} instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        if isinstance(default_value, bool):
            return env_value.lower() in ("true", "1", "yes")
        elif isinstance(default_value, int):
            return int(env_value)
        return env_value
    return None


@property
def xtest_auto_postmortem_enabled(self) -> bool:
    """Deprecated: Use auto_enabled instead."""
    deprecated_value = _get_deprecated_setting("XTEST_AUTO_POSTMORTEM_ENABLED", "AUTO_ENABLED", False)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().auto_enabled


@property
def xtest_auto_postmortem_min_duration(self) -> int:
    """Deprecated: Use auto_min_duration instead."""
    deprecated_value = _get_deprecated_setting("XTEST_AUTO_POSTMORTEM_MIN_DURATION", "AUTO_MIN_DURATION", 30)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().auto_min_duration


@property
def xtest_postmortem_history_limit(self) -> int:
    """Deprecated: Use history_limit instead."""
    deprecated_value = _get_deprecated_setting("XTEST_POSTMORTEM_HISTORY_LIMIT", "HISTORY_LIMIT", 100)
    if deprecated_value is not None:
        return deprecated_value
    return get_postmortem_settings().history_limit


# ==========================================================================
# Module Exports
# ==========================================================================

__all__ = [
    "PostmortemSettings",
    "get_postmortem_settings",
    "reset_postmortem_settings",
]
