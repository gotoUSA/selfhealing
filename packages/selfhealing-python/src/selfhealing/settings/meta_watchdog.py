"""
Meta-Watchdog Settings - Pydantic v2.

Self-Healing 시스템 자체 모니터링을 위한 설정 관리.
환경변수 SELFHEALING_META_* 로 설정 가능.

Moved from: meta/config.py (위치 통일)
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class MetaWatchdogSettings(BaseSettings):
    """
    Meta-Watchdog 설정.

    Self-Healing 시스템 자체의 건강 상태 모니터링 설정.

    환경변수 예시:
        SELFHEALING_META_ENABLED=true
        SELFHEALING_META_PROBE_INTERVAL_SECONDS=30
        SELFHEALING_META_PAGERDUTY_ROUTING_KEY=xxx
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_META_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    # 활성화 설정
    enabled: bool = Field(
        default=True,
        description="Meta-Watchdog 활성화 여부",
    )

    # Health Probe 설정
    probe_interval_seconds: float = Field(
        default=30.0,
        description="헬스 프로브 실행 주기 (초)",
        ge=5.0,
    )
    probe_timeout_seconds: float = Field(
        default=10.0,
        description="헬스 프로브 타임아웃 (초)",
        ge=1.0,
    )

    # Stuck Detection 설정
    stuck_threshold_seconds: float = Field(
        default=300.0,
        description="Stuck 감지 임계치 (초, 기본 5분)",
        ge=60.0,
    )
    dlq_stuck_threshold_entries: int = Field(
        default=1000,
        description="DLQ Stuck 감지 임계치 (대기 엔트리 수)",
        ge=100,
    )

    # Self-Healing Circuit Breaker 설정 (자기 보호)
    self_cb_enabled: bool = Field(
        default=True,
        description="Self-Healing용 Circuit Breaker 활성화",
    )
    self_cb_failure_threshold: int = Field(
        default=5,
        description="CB Open 전환을 위한 연속 실패 횟수",
        ge=1,
    )
    self_cb_recovery_timeout_seconds: float = Field(
        default=60.0,
        description="CB Half-Open 전환 대기 시간 (초)",
        ge=10.0,
    )

    # Escalation 설정
    escalation_enabled: bool = Field(
        default=True,
        description="에스컬레이션 활성화 여부",
    )
    escalation_delay_seconds: float = Field(
        default=180.0,
        description="에스컬레이션 지연 시간 (자동 복구 대기, 초)",
        ge=0.0,
    )
    escalation_cooldown_seconds: float = Field(
        default=3600.0,
        description="동일 컴포넌트 에스컬레이션 쿨다운 (초, 기본 1시간)",
        ge=60.0,
    )

    # 복구 쿨다운 설정
    recovery_cooldown_seconds: float = Field(
        default=300.0,
        description="동일 컴포넌트 복구 시도 쿨다운 (초, 기본 5분)",
        ge=30.0,
    )

    # 워크로드 이름 설정 (K8s Deployment/StatefulSet 이름)
    redis_workload_name: str = Field(
        default="redis",
        description="Redis Deployment/StatefulSet 이름 (K8s 리소스명)",
    )
    dlq_worker_workload_name: str = Field(
        default="celery-dlq-worker",
        description="DLQ Worker Deployment 이름 (K8s 리소스명)",
    )

    # PagerDuty 설정
    pagerduty_routing_key: str | None = Field(
        default=None,
        description="PagerDuty Events API v2 Routing Key",
    )
    pagerduty_severity: Literal["critical", "error", "warning", "info"] = Field(
        default="critical",
        description="PagerDuty 알림 심각도",
    )

    # Slack 설정
    slack_webhook_url: str | None = Field(
        default=None,
        description="Slack Incoming Webhook URL",
    )

    # Dry-run 모드 (관찰만, 복구/에스컬레이션 미수행)
    dry_run_mode: bool = Field(
        default=False,
        description="프로브만 수행하고 복구/에스컬레이션은 미수행 (관찰 모드)",
    )

    # 유지보수 컴포넌트 목록 (해당 컴포넌트 알림 억제)
    maintenance_components: list[str] = Field(
        default_factory=list,
        description="유지보수 중인 컴포넌트 목록 (해당 컴포넌트 알림 억제)",
    )


def get_meta_watchdog_settings() -> "MetaWatchdogSettings":
    from selfhealing.settings.root import get_config

    return get_config().meta.meta_watchdog

def reset_meta_watchdog_settings() -> None:
    from selfhealing.settings.root import get_config

    try:
        del get_config().meta.__dict__["meta_watchdog"]
    except KeyError:
        pass
