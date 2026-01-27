"""
Self-Healing Configuration Module

Provides configuration access for the selfhealing package.
This module provides compatibility layer that re-exports from
the appropriate configuration source.

For Django applications, configuration is loaded from Django settings.
For other frameworks, uses environment variables or defaults.

v6.3.0: Drift Detection
- ConfigDriftMonitor: 환경변수 변경 감지 및 캐시 무효화
- lru_cache 함수들에 대한 메트릭 추적
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from functools import lru_cache

# Drift Detection 메트릭
try:
    from selfhealing.metrics.drift_metrics import (
        record_config_cache_hit,
        record_config_cache_invalidated,
        record_config_cache_miss,
        record_config_env_changed,
    )

    HAS_DRIFT_METRICS = True
except ImportError:
    HAS_DRIFT_METRICS = False
    record_config_env_changed = lambda *args: None
    record_config_cache_invalidated = lambda *args: None
    record_config_cache_hit = lambda *args: None
    record_config_cache_miss = lambda *args: None


# =============================================================================
# Configuration Data Classes
# =============================================================================


@dataclass(frozen=True)
class NotificationLimits:
    """
    Limits for notification message formatting.
    """

    # Slack API limits
    slack_block_text_limit: int = 3000

    # Message truncation limits
    description_max_length: int = 500
    action_taken_max_length: int = 200
    title_max_length: int = 150

    # HTTP request timeout
    notification_timeout_seconds: int = 10


@dataclass(frozen=True)
class ForensicSettings:
    """
    Forensic context configuration.
    """

    # Stack trace limits
    max_stack_frames: int = 50
    max_stacktrace_length: int = 10000

    # Context size limits
    max_context_size_bytes: int = 65536  # 64KB

    # Data collection settings
    collect_request_body: bool = False
    collect_response_body: bool = False

    # Sensitive field masking
    mask_sensitive_fields: bool = True
    sensitive_field_patterns: tuple[str, ...] = (
        # Authentication & Secrets
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "private_key",
        "access_key",
        "secret_key",
        # Payment related
        "card_number",
        "cvv",
        "cvc",
        "credit_card",
        # Internal infrastructure (should not be exposed in logs)
        "internal_ip",
        "server_path",
        "db_password",
        "redis_password",
        "connection_string",
    )

    # IP address masking patterns (regex)
    # Private IP ranges that should be masked in logs
    mask_internal_ip: bool = True
    internal_ip_patterns: tuple[str, ...] = (
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}",  # 10.0.0.0/8
        r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}",  # 172.16.0.0/12
        r"192\.168\.\d{1,3}\.\d{1,3}",  # 192.168.0.0/16
    )

    # Server path patterns to mask
    mask_server_paths: bool = True
    server_path_patterns: tuple[str, ...] = (
        r"/home/[^/]+",  # Home directories
        r"/var/[^/]+/[^/]+",  # Var subdirectories
        r"/etc/[^/]+",  # Config files
        r"[A-Z]:\\Users\\[^\\]+",  # Windows user paths
        r"/app/[^/]+/[^/]+",  # Container app paths
    )


# =============================================================================
# Configuration Loading Functions
# =============================================================================


def _try_django_config():
    """Try to load config from Django settings."""
    # NOTE: For projects integrating with Django, configuration should be
    # loaded from Django settings directly. The selfhealing package uses
    # environment variables or defaults for standalone deployments.
    return None, None


@lru_cache(maxsize=1)
def get_notification_limits() -> NotificationLimits:
    """
    Get notification limits configuration.

    Tries Django settings first, falls back to environment/defaults.
    """
    django_getter, _ = _try_django_config()
    if django_getter:
        return django_getter()

    # Fall back to environment variables or defaults
    return NotificationLimits(
        slack_block_text_limit=int(
            os.environ.get("SELFHEALING_SLACK_BLOCK_TEXT_LIMIT", 3000)
        ),
        description_max_length=int(
            os.environ.get("SELFHEALING_DESCRIPTION_MAX_LENGTH", 500)
        ),
        action_taken_max_length=int(
            os.environ.get("SELFHEALING_ACTION_TAKEN_MAX_LENGTH", 200)
        ),
        title_max_length=int(os.environ.get("SELFHEALING_TITLE_MAX_LENGTH", 150)),
        notification_timeout_seconds=int(
            os.environ.get("SELFHEALING_NOTIFICATION_TIMEOUT", 10)
        ),
    )


@lru_cache(maxsize=1)
def get_forensic_settings() -> ForensicSettings:
    """
    Get forensic context configuration.

    Tries Django settings first, falls back to environment/defaults.
    """
    _, django_getter = _try_django_config()
    if django_getter:
        return django_getter()

    # Fall back to environment variables or defaults
    return ForensicSettings(
        max_stack_frames=int(os.environ.get("SELFHEALING_MAX_STACK_FRAMES", 50)),
        max_stacktrace_length=int(
            os.environ.get("SELFHEALING_MAX_STACKTRACE_LENGTH", 10000)
        ),
        max_context_size_bytes=int(
            os.environ.get("SELFHEALING_MAX_CONTEXT_SIZE", 65536)
        ),
        collect_request_body=os.environ.get(
            "SELFHEALING_COLLECT_REQUEST_BODY", "false"
        ).lower()
        == "true",
        collect_response_body=os.environ.get(
            "SELFHEALING_COLLECT_RESPONSE_BODY", "false"
        ).lower()
        == "true",
        mask_sensitive_fields=os.environ.get(
            "SELFHEALING_MASK_SENSITIVE_FIELDS", "true"
        ).lower()
        == "true",
    )


# =============================================================================
# Event Logging Settings (API-Level Configuration)
# =============================================================================


class EventLoggingConfig:
    """
    런타임에 변경 가능한 이벤트 로깅 설정.

    API 레벨에서 로깅 레벨을 조절할 수 있어 서버 재시작 없이
    운영자가 대시보드/API에서 즉시 변경 가능합니다.

    Priority (highest to lowest):
    1. API/Admin 설정 (런타임 변경)
    2. 환경변수 (컨테이너 기본값)
    3. 하드코딩 기본값

    Example:
        >>> config = get_event_logging_config()
        >>> config.update(dlq_log_level="DEBUG")  # 런타임 변경
        >>> config.get_dlq_log_level()  # "DEBUG"
    """

    # Valid log levels
    VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    # Singleton instance
    _instance: EventLoggingConfig | None = None
    _lock = None  # Will be initialized in __new__

    def __new__(cls) -> EventLoggingConfig:
        """Singleton pattern for global configuration."""
        if cls._instance is None:
            import threading

            cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init_defaults()
                    cls._instance = instance
        return cls._instance

    def _init_defaults(self) -> None:
        """Initialize default values from environment or hardcoded defaults."""
        import threading

        self._runtime_lock = threading.Lock()

        # Runtime-configurable values (API level)
        self._runtime_config: dict = {}

        # Environment-based defaults
        self._env_defaults = {
            "dlq_log_level": os.environ.get("SELFHEALING_DLQ_LOG_LEVEL", "INFO"),
            "cb_log_level": os.environ.get("SELFHEALING_CB_LOG_LEVEL", "WARNING"),
            "replay_log_level": os.environ.get("SELFHEALING_REPLAY_LOG_LEVEL", "INFO"),
            "sla_log_level": os.environ.get("SELFHEALING_SLA_LOG_LEVEL", "WARNING"),
        }

        # Hardcoded defaults (fallback)
        self._hardcoded_defaults = {
            "dlq_log_level": "INFO",
            "cb_log_level": "WARNING",
            "replay_log_level": "INFO",
            "sla_log_level": "WARNING",
        }

        # Last updated timestamp (for audit trail)
        self._last_updated: dict = {}

    def _validate_level(self, level: str) -> str:
        """Validate and normalize log level."""
        level = level.upper()
        if level not in self.VALID_LEVELS:
            raise ValueError(
                f"Invalid log level: {level}. " f"Valid levels: {self.VALID_LEVELS}"
            )
        return level

    def _get_value(self, key: str) -> str:
        """Get value with priority: runtime > env > hardcoded."""
        with self._runtime_lock:
            if key in self._runtime_config:
                return self._runtime_config[key]
        return self._env_defaults.get(key, self._hardcoded_defaults.get(key, "INFO"))

    def update(
        self,
        dlq_log_level: str | None = None,
        cb_log_level: str | None = None,
        replay_log_level: str | None = None,
        sla_log_level: str | None = None,
        updated_by: str = "api",
    ) -> dict:
        """
        Update logging configuration at runtime.

        Args:
            dlq_log_level: DLQ 이벤트 로그 레벨 (INFO 권장)
            cb_log_level: Circuit Breaker 로그 레벨 (WARNING 권장)
            replay_log_level: Replay 이벤트 로그 레벨 (INFO 권장)
            sla_log_level: SLA 위반 로그 레벨 (WARNING 권장)
            updated_by: 변경 주체 (감사 추적용)

        Returns:
            Updated configuration as dict
        """
        from datetime import datetime, timezone

        updates = {}

        with self._runtime_lock:
            if dlq_log_level is not None:
                level = self._validate_level(dlq_log_level)
                self._runtime_config["dlq_log_level"] = level
                updates["dlq_log_level"] = level

            if cb_log_level is not None:
                level = self._validate_level(cb_log_level)
                self._runtime_config["cb_log_level"] = level
                updates["cb_log_level"] = level

            if replay_log_level is not None:
                level = self._validate_level(replay_log_level)
                self._runtime_config["replay_log_level"] = level
                updates["replay_log_level"] = level

            if sla_log_level is not None:
                level = self._validate_level(sla_log_level)
                self._runtime_config["sla_log_level"] = level
                updates["sla_log_level"] = level

            if updates:
                self._last_updated = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "updated_by": updated_by,
                    "changes": updates,
                }

        return self.to_dict()

    def reset(self) -> None:
        """Reset to environment/default values (clear runtime config)."""
        with self._runtime_lock:
            self._runtime_config.clear()
            self._last_updated = {}

    # Property-style getters for each log level
    def get_dlq_log_level(self) -> str:
        """Get DLQ event log level."""
        return self._get_value("dlq_log_level")

    def get_cb_log_level(self) -> str:
        """Get Circuit Breaker log level."""
        return self._get_value("cb_log_level")

    def get_replay_log_level(self) -> str:
        """Get Replay event log level."""
        return self._get_value("replay_log_level")

    def get_sla_log_level(self) -> str:
        """Get SLA breach log level."""
        return self._get_value("sla_log_level")

    def get_log_level_int(self, level_name: str) -> int:
        """Convert level name to logging module integer."""
        import logging

        return getattr(logging, level_name.upper(), logging.INFO)

    def to_dict(self) -> dict:
        """Export current configuration as dict."""
        return {
            "dlq_log_level": self.get_dlq_log_level(),
            "cb_log_level": self.get_cb_log_level(),
            "replay_log_level": self.get_replay_log_level(),
            "sla_log_level": self.get_sla_log_level(),
            "last_updated": self._last_updated,
        }


def get_event_logging_config() -> EventLoggingConfig:
    """
    Get the singleton EventLoggingConfig instance.

    Returns:
        EventLoggingConfig singleton
    """
    return EventLoggingConfig()


# =============================================================================
# Metric Collection Settings
# =============================================================================


@dataclass(frozen=True)
class MetricCollectionSettings:
    """
    메트릭 수집 설정.
    """

    # 동기화 설정
    sync_on_startup: bool = True  # 서버 시작 시 동기화
    scheduled_sync_enabled: bool = False  # 주기적 동기화 (권장: 비활성화)
    scheduled_sync_interval: int = 86400  # 주기 (초), 기본 24시간

    # Jitter 설정 (Thundering Herd 방지)
    jitter_enabled: bool = True  # Jitter 활성화
    jitter_max_delay_seconds: float = 60.0  # 최대 지연 시간 (초)

    # 어댑터 설정
    adapter_type: str = "null"  # django, redis, null
    redis_prefix: str = "sh:metrics:"  # Redis 어댑터용 키 프리픽스

    # Drift 감지 (거버넌스 레벨)
    drift_detection_enabled: bool = True
    drift_warning_threshold: float = 0.05  # 5% - 경고
    drift_critical_threshold: float = 0.20  # 20% - 심각, 알림 발송
    drift_incident_threshold: float = 0.50  # 50% - 인시던트, 이벤트 유실
    drift_incident_enabled: bool = True  # 인시던트 자동 생성
    drift_alert_enabled: bool = True  # 알림 발송 활성화


@lru_cache(maxsize=1)
def get_metric_collection_settings() -> MetricCollectionSettings:
    """
    Get metric collection settings.

    Loads from environment variables with sensible defaults.
    """
    return MetricCollectionSettings(
        sync_on_startup=os.environ.get(
            "SELFHEALING_METRICS_SYNC_ON_STARTUP", "true"
        ).lower()
        == "true",
        scheduled_sync_enabled=os.environ.get(
            "SELFHEALING_METRICS_SCHEDULED_SYNC_ENABLED", "false"
        ).lower()
        == "true",
        scheduled_sync_interval=int(
            os.environ.get("SELFHEALING_METRICS_SCHEDULED_SYNC_INTERVAL", "86400")
        ),
        jitter_enabled=os.environ.get(
            "SELFHEALING_METRICS_JITTER_ENABLED", "true"
        ).lower()
        == "true",
        jitter_max_delay_seconds=float(
            os.environ.get("SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS", "60.0")
        ),
        adapter_type=os.environ.get("SELFHEALING_METRICS_ADAPTER_TYPE", "null"),
        redis_prefix=os.environ.get("SELFHEALING_METRICS_REDIS_PREFIX", "sh:metrics:"),
        drift_detection_enabled=os.environ.get(
            "SELFHEALING_METRICS_DRIFT_DETECTION_ENABLED", "true"
        ).lower()
        == "true",
        drift_warning_threshold=float(
            os.environ.get("SELFHEALING_DRIFT_WARNING_THRESHOLD", "0.05")
        ),
        drift_critical_threshold=float(
            os.environ.get("SELFHEALING_DRIFT_CRITICAL_THRESHOLD", "0.20")
        ),
        drift_incident_threshold=float(
            os.environ.get("SELFHEALING_DRIFT_INCIDENT_THRESHOLD", "0.50")
        ),
        drift_incident_enabled=os.environ.get(
            "SELFHEALING_DRIFT_INCIDENT_ENABLED", "true"
        ).lower()
        == "true",
        drift_alert_enabled=os.environ.get(
            "SELFHEALING_DRIFT_ALERT_ENABLED", "true"
        ).lower()
        == "true",
    )


# =============================================================================
# L2 Storage Resilience Settings
# =============================================================================


@dataclass(frozen=True)
class L2StorageConfig:
    """
    L2 저장소 복원력 설정.

    Layered Storage(L1 Memory + L2 Redis/DB)에서 L2 장애 시
    타임아웃 및 복구 동작을 제어합니다.

    Priority (highest to lowest):
    1. API/Runtime 설정 (런타임 변경)
    2. 환경변수 (컨테이너 기본값)
    3. 하드코딩 기본값 (업계 사례 기반)
    """

    # 어댑터별 타임아웃 (ms)
    redis_timeout_ms: int = 50  # Redis: 빠름, 50ms면 충분
    database_timeout_ms: int = 200  # DB: 부하 시 느려짐, 200ms 필요
    fallback_timeout_ms: int = 100  # 알 수 없는 어댑터

    # Shadow Logging 설정
    shadow_log_enabled: bool = True  # Shadow Log 활성화
    shadow_log_max_entries: int = 1000  # 최대 보관 항목 수

    # Drift Reconciliation 설정 (Thundering Herd 방지)
    reconciliation_jitter_min_seconds: float = 0.0  # 최소 지연
    reconciliation_jitter_max_seconds: float = 5.0  # 최대 지연

    # L2 헬스체크 설정
    health_check_interval_seconds: float = 30.0  # 헬스체크 주기
    health_check_timeout_ms: int = 100  # 헬스체크 타임아웃

    def get_timeout_for_adapter(self, adapter_type: str) -> float:
        """
        어댑터 타입에 따른 타임아웃 반환 (초 단위).

        Args:
            adapter_type: 어댑터 타입 ("redis", "database", "django" 등)

        Returns:
            타임아웃 (초 단위)
        """
        timeouts = {
            "redis": self.redis_timeout_ms,
            "database": self.database_timeout_ms,
            "django": self.database_timeout_ms,
        }
        return timeouts.get(adapter_type.lower(), self.fallback_timeout_ms) / 1000.0


class L2StorageRuntimeConfig:
    """
    런타임에 변경 가능한 L2 저장소 설정.

    API 레벨에서 설정을 조절할 수 있어 서버 재시작 없이
    운영자가 대시보드/API에서 즉시 변경 가능합니다.

    Singleton pattern으로 전역 설정 관리.
    """

    _instance: L2StorageRuntimeConfig | None = None
    _lock = None

    def __new__(cls) -> L2StorageRuntimeConfig:
        """Singleton pattern for global configuration."""
        if cls._instance is None:
            import threading

            cls._lock = threading.Lock()
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init_defaults()
                    cls._instance = instance
        return cls._instance

    def _init_defaults(self) -> None:
        """Initialize default values from environment or hardcoded defaults."""
        import threading

        self._runtime_lock = threading.Lock()
        self._runtime_config: dict = {}
        self._last_updated: dict = {}

        # 환경변수 기본값
        self._env_defaults = {
            "redis_timeout_ms": int(
                os.environ.get("SELFHEALING_L2_REDIS_TIMEOUT_MS", 50)
            ),
            "database_timeout_ms": int(
                os.environ.get("SELFHEALING_L2_DATABASE_TIMEOUT_MS", 200)
            ),
            "fallback_timeout_ms": int(
                os.environ.get("SELFHEALING_L2_FALLBACK_TIMEOUT_MS", 100)
            ),
            "shadow_log_enabled": os.environ.get(
                "SELFHEALING_L2_SHADOW_LOG_ENABLED", "true"
            ).lower()
            == "true",
            "shadow_log_max_entries": int(
                os.environ.get("SELFHEALING_L2_SHADOW_LOG_MAX_ENTRIES", 1000)
            ),
            "reconciliation_jitter_min_seconds": float(
                os.environ.get("SELFHEALING_L2_RECONCILIATION_JITTER_MIN", 0.0)
            ),
            "reconciliation_jitter_max_seconds": float(
                os.environ.get("SELFHEALING_L2_RECONCILIATION_JITTER_MAX", 5.0)
            ),
            "health_check_interval_seconds": float(
                os.environ.get("SELFHEALING_L2_HEALTH_CHECK_INTERVAL", 30.0)
            ),
            "health_check_timeout_ms": int(
                os.environ.get("SELFHEALING_L2_HEALTH_CHECK_TIMEOUT_MS", 100)
            ),
        }

        # 하드코딩 기본값 (업계 사례 기반)
        self._hardcoded_defaults = {
            "redis_timeout_ms": 50,
            "database_timeout_ms": 200,
            "fallback_timeout_ms": 100,
            "shadow_log_enabled": True,
            "shadow_log_max_entries": 1000,
            "reconciliation_jitter_min_seconds": 0.0,
            "reconciliation_jitter_max_seconds": 5.0,
            "health_check_interval_seconds": 30.0,
            "health_check_timeout_ms": 100,
        }

    def _get_value(self, key: str) -> int | float | bool:
        """Get value with priority: runtime > env > hardcoded."""
        with self._runtime_lock:
            if key in self._runtime_config:
                return self._runtime_config[key]
        return self._env_defaults.get(key, self._hardcoded_defaults.get(key))

    # Field validation rules: (min, max, error_message)
    _FIELD_VALIDATORS: dict = {
        "redis_timeout_ms": (10, 1000, "redis_timeout_ms must be between 10 and 1000"),
        "database_timeout_ms": (
            50,
            5000,
            "database_timeout_ms must be between 50 and 5000",
        ),
        "fallback_timeout_ms": (
            10,
            1000,
            "fallback_timeout_ms must be between 10 and 1000",
        ),
        "shadow_log_max_entries": (
            100,
            10000,
            "shadow_log_max_entries must be between 100 and 10000",
        ),
        "reconciliation_jitter_min_seconds": (
            0.0,
            60.0,
            "reconciliation_jitter_min_seconds must be between 0 and 60",
        ),
        "reconciliation_jitter_max_seconds": (
            0.0,
            60.0,
            "reconciliation_jitter_max_seconds must be between 0 and 60",
        ),
        "health_check_interval_seconds": (
            5.0,
            300.0,
            "health_check_interval_seconds must be between 5 and 300",
        ),
        "health_check_timeout_ms": (
            10,
            1000,
            "health_check_timeout_ms must be between 10 and 1000",
        ),
    }

    def _validate_and_update_field(
        self, key: str, value: int | float | bool | None
    ) -> tuple[bool, int | float | bool | None]:
        """Validate and update a single config field. Returns (updated, value)."""
        if value is None:
            return False, None

        # Validate if validator exists
        if key in self._FIELD_VALIDATORS:
            min_val, max_val, error_msg = self._FIELD_VALIDATORS[key]
            if not (min_val <= value <= max_val):
                raise ValueError(error_msg)

        self._runtime_config[key] = value
        return True, value

    def update(
        self,
        redis_timeout_ms: int | None = None,
        database_timeout_ms: int | None = None,
        fallback_timeout_ms: int | None = None,
        shadow_log_enabled: bool | None = None,
        shadow_log_max_entries: int | None = None,
        reconciliation_jitter_min_seconds: float | None = None,
        reconciliation_jitter_max_seconds: float | None = None,
        health_check_interval_seconds: float | None = None,
        health_check_timeout_ms: int | None = None,
        updated_by: str = "api",
    ) -> dict:
        """
        Update L2 storage configuration at runtime.

        Args:
            redis_timeout_ms: Redis 타임아웃 (ms), 10-1000 범위
            database_timeout_ms: DB 타임아웃 (ms), 50-5000 범위
            fallback_timeout_ms: 폴백 타임아웃 (ms), 10-1000 범위
            shadow_log_enabled: Shadow Log 활성화 여부
            shadow_log_max_entries: Shadow Log 최대 항목 수
            reconciliation_jitter_min_seconds: Jitter 최소 시간 (초)
            reconciliation_jitter_max_seconds: Jitter 최대 시간 (초)
            health_check_interval_seconds: 헬스체크 주기 (초)
            health_check_timeout_ms: 헬스체크 타임아웃 (ms)
            updated_by: 변경 주체 (감사 추적용)

        Returns:
            Updated configuration as dict
        """
        from datetime import datetime, timezone

        # Build field update map
        field_updates = {
            "redis_timeout_ms": redis_timeout_ms,
            "database_timeout_ms": database_timeout_ms,
            "fallback_timeout_ms": fallback_timeout_ms,
            "shadow_log_enabled": shadow_log_enabled,
            "shadow_log_max_entries": shadow_log_max_entries,
            "reconciliation_jitter_min_seconds": reconciliation_jitter_min_seconds,
            "reconciliation_jitter_max_seconds": reconciliation_jitter_max_seconds,
            "health_check_interval_seconds": health_check_interval_seconds,
            "health_check_timeout_ms": health_check_timeout_ms,
        }

        updates = {}

        with self._runtime_lock:
            for key, value in field_updates.items():
                updated, validated_value = self._validate_and_update_field(key, value)
                if updated:
                    updates[key] = validated_value

            if updates:
                self._last_updated = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "updated_by": updated_by,
                    "changes": updates,
                }

        return self.to_dict()

    def reset(self) -> None:
        """Reset to environment/default values (clear runtime config)."""
        with self._runtime_lock:
            self._runtime_config.clear()
            self._last_updated = {}

    # Property-style getters
    def get_redis_timeout_ms(self) -> int:
        """Get Redis timeout in milliseconds."""
        return self._get_value("redis_timeout_ms")

    def get_database_timeout_ms(self) -> int:
        """Get database timeout in milliseconds."""
        return self._get_value("database_timeout_ms")

    def get_fallback_timeout_ms(self) -> int:
        """Get fallback timeout in milliseconds."""
        return self._get_value("fallback_timeout_ms")

    def get_shadow_log_enabled(self) -> bool:
        """Get shadow log enabled status."""
        return self._get_value("shadow_log_enabled")

    def get_shadow_log_max_entries(self) -> int:
        """Get shadow log max entries."""
        return self._get_value("shadow_log_max_entries")

    def get_timeout_for_adapter(self, adapter_type: str) -> float:
        """Get timeout for adapter type in seconds."""
        timeouts = {
            "redis": self.get_redis_timeout_ms(),
            "database": self.get_database_timeout_ms(),
            "django": self.get_database_timeout_ms(),
        }
        return (
            timeouts.get(adapter_type.lower(), self.get_fallback_timeout_ms()) / 1000.0
        )

    def to_dict(self) -> dict:
        """Export current configuration as dict."""
        return {
            "redis_timeout_ms": self.get_redis_timeout_ms(),
            "database_timeout_ms": self.get_database_timeout_ms(),
            "fallback_timeout_ms": self.get_fallback_timeout_ms(),
            "shadow_log_enabled": self.get_shadow_log_enabled(),
            "shadow_log_max_entries": self.get_shadow_log_max_entries(),
            "reconciliation_jitter_min_seconds": self._get_value(
                "reconciliation_jitter_min_seconds"
            ),
            "reconciliation_jitter_max_seconds": self._get_value(
                "reconciliation_jitter_max_seconds"
            ),
            "health_check_interval_seconds": self._get_value(
                "health_check_interval_seconds"
            ),
            "health_check_timeout_ms": self._get_value("health_check_timeout_ms"),
            "last_updated": self._last_updated,
        }


@lru_cache(maxsize=1)
def get_l2_storage_config() -> L2StorageConfig:
    """
    Get L2 storage configuration (frozen dataclass).

    Loads from environment variables with sensible defaults.
    Use get_l2_storage_runtime_config() for runtime-changeable settings.
    """
    return L2StorageConfig(
        redis_timeout_ms=int(os.environ.get("SELFHEALING_L2_REDIS_TIMEOUT_MS", 50)),
        database_timeout_ms=int(
            os.environ.get("SELFHEALING_L2_DATABASE_TIMEOUT_MS", 200)
        ),
        fallback_timeout_ms=int(
            os.environ.get("SELFHEALING_L2_FALLBACK_TIMEOUT_MS", 100)
        ),
        shadow_log_enabled=os.environ.get(
            "SELFHEALING_L2_SHADOW_LOG_ENABLED", "true"
        ).lower()
        == "true",
        shadow_log_max_entries=int(
            os.environ.get("SELFHEALING_L2_SHADOW_LOG_MAX_ENTRIES", 1000)
        ),
        reconciliation_jitter_min_seconds=float(
            os.environ.get("SELFHEALING_L2_RECONCILIATION_JITTER_MIN", 0.0)
        ),
        reconciliation_jitter_max_seconds=float(
            os.environ.get("SELFHEALING_L2_RECONCILIATION_JITTER_MAX", 5.0)
        ),
        health_check_interval_seconds=float(
            os.environ.get("SELFHEALING_L2_HEALTH_CHECK_INTERVAL", 30.0)
        ),
        health_check_timeout_ms=int(
            os.environ.get("SELFHEALING_L2_HEALTH_CHECK_TIMEOUT_MS", 100)
        ),
    )


def get_l2_storage_runtime_config() -> L2StorageRuntimeConfig:
    """
    Get the singleton L2StorageRuntimeConfig instance.

    Use this for runtime-changeable settings via API.

    Returns:
        L2StorageRuntimeConfig singleton
    """
    return L2StorageRuntimeConfig()


# =============================================================================
# Convenience exports
# =============================================================================


# =============================================================================
# Config Drift Monitor
# =============================================================================


class ConfigDriftMonitor:
    """
    환경변수 변경 감지 및 lru_cache 무효화.

    v6.3.0: Drift Detection 구현

    환경변수가 변경되면 관련 lru_cache를 무효화하고
    Prometheus 메트릭을 기록합니다.

    Usage:
        monitor = get_config_drift_monitor()

        # 환경변수 변경 확인 후 필요시 캐시 무효화
        if monitor.check_and_invalidate("circuit_breaker", "SELFHEALING_CB_"):
            logger.info("Config changed, cache invalidated")

        # 설정 조회 (drift 체크 포함)
        settings = get_circuit_breaker_settings_safe()
    """

    _instance: ConfigDriftMonitor | None = None
    _lock = threading.Lock()

    def __new__(cls) -> ConfigDriftMonitor:
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self) -> None:
        """Initialize internal state."""
        self._env_hashes: dict[str, str] = {}
        self._hash_lock = threading.Lock()
        self._cache_functions: dict[str, callable] = {}

    def register_cache_function(self, config_type: str, func: callable) -> None:
        """
        config_type에 해당하는 lru_cache 함수 등록.

        Args:
            config_type: 설정 타입 (예: "notification_limits")
            func: lru_cache 데코레이터가 적용된 함수
        """
        with self._hash_lock:
            self._cache_functions[config_type] = func

    def _compute_env_hash(self, prefix: str) -> str:
        """해당 prefix로 시작하는 환경변수들의 해시 계산."""
        relevant_vars = {k: v for k, v in os.environ.items() if k.startswith(prefix)}
        content = str(sorted(relevant_vars.items()))
        return hashlib.md5(content.encode()).hexdigest()

    def check_and_invalidate(self, config_type: str, prefix: str) -> bool:
        """
        환경변수 변경 확인 후 필요시 캐시 무효화.

        Args:
            config_type: 설정 타입
            prefix: 환경변수 prefix (예: "SELFHEALING_CB_")

        Returns:
            True if cache was invalidated, False otherwise
        """
        with self._hash_lock:
            current_hash = self._compute_env_hash(prefix)
            previous_hash = self._env_hashes.get(config_type)

            if previous_hash and current_hash != previous_hash:
                # 환경변수 변경 감지
                record_config_env_changed(config_type)
                self._invalidate_cache(config_type)
                self._env_hashes[config_type] = current_hash
                return True

            self._env_hashes[config_type] = current_hash
            return False

    def _invalidate_cache(self, config_type: str) -> None:
        """해당 config 타입의 lru_cache 무효화."""
        record_config_cache_invalidated(config_type)

        # 등록된 함수가 있으면 cache_clear 호출
        func = self._cache_functions.get(config_type)
        if func and hasattr(func, "cache_clear"):
            func.cache_clear()

    def get_stats(self) -> dict[str, str]:
        """현재 저장된 해시값들 반환."""
        with self._hash_lock:
            return self._env_hashes.copy()


# 싱글톤 인스턴스
_config_drift_monitor: ConfigDriftMonitor | None = None


def get_config_drift_monitor() -> ConfigDriftMonitor:
    """
    Get the singleton ConfigDriftMonitor instance.

    Returns:
        ConfigDriftMonitor singleton
    """
    return ConfigDriftMonitor()


def get_notification_limits_safe() -> NotificationLimits:
    """환경변수 변경 감지 후 설정 반환."""
    monitor = get_config_drift_monitor()
    monitor.check_and_invalidate("notification_limits", "SELFHEALING_")
    return get_notification_limits()


def get_forensic_settings_safe() -> ForensicSettings:
    """환경변수 변경 감지 후 설정 반환."""
    monitor = get_config_drift_monitor()
    monitor.check_and_invalidate("forensic_settings", "SELFHEALING_")
    return get_forensic_settings()


def get_metric_collection_settings_safe() -> MetricCollectionSettings:
    """환경변수 변경 감지 후 설정 반환."""
    monitor = get_config_drift_monitor()
    monitor.check_and_invalidate("metric_collection", "SELFHEALING_METRICS_")
    return get_metric_collection_settings()


def get_l2_storage_config_safe() -> L2StorageConfig:
    """환경변수 변경 감지 후 설정 반환."""
    monitor = get_config_drift_monitor()
    monitor.check_and_invalidate("l2_storage", "SELFHEALING_L2_")
    return get_l2_storage_config()


# 캐시 함수 등록
def _register_cache_functions() -> None:
    """lru_cache 함수들을 ConfigDriftMonitor에 등록."""
    monitor = get_config_drift_monitor()
    monitor.register_cache_function("notification_limits", get_notification_limits)
    monitor.register_cache_function("forensic_settings", get_forensic_settings)
    monitor.register_cache_function("metric_collection", get_metric_collection_settings)
    monitor.register_cache_function("l2_storage", get_l2_storage_config)


# 모듈 로드 시 등록
try:
    _register_cache_functions()
except Exception:
    pass  # 초기화 실패해도 모듈 로드는 성공


__all__ = [
    "NotificationLimits",
    "ForensicSettings",
    "MetricCollectionSettings",
    "EventLoggingConfig",
    "L2StorageConfig",
    "L2StorageRuntimeConfig",
    "ConfigDriftMonitor",
    "get_notification_limits",
    "get_forensic_settings",
    "get_metric_collection_settings",
    "get_event_logging_config",
    "get_l2_storage_config",
    "get_l2_storage_runtime_config",
    "get_config_drift_monitor",
    "get_notification_limits_safe",
    "get_forensic_settings_safe",
    "get_metric_collection_settings_safe",
    "get_l2_storage_config_safe",
]
