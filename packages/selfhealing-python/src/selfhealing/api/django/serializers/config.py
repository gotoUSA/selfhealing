"""
Runtime Configuration API Serializers.

Serializers for validating and serializing runtime configuration updates.
Includes apply strategy support (immediate, delayed, graceful).
"""

from rest_framework import serializers


# =============================================================================
# Apply Strategy Mixin
# =============================================================================


class ApplyStrategyMixin(serializers.Serializer):
    """Mixin that adds apply strategy fields to config serializers."""

    apply_strategy = serializers.ChoiceField(
        required=False,
        choices=["immediate", "delayed", "graceful"],
        help_text="How to apply the changes: immediate (now), delayed (after N seconds), graceful (wait for in-progress ops)",
    )
    delay_seconds = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=3600,
        help_text="Seconds to wait before applying (only for 'delayed' strategy)",
    )
    grace_timeout_seconds = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=300,
        help_text="Max seconds to wait for in-progress operations (only for 'graceful' strategy)",
    )

    def get_apply_options(self) -> dict:
        """Extract apply strategy options from validated data."""
        return {
            "strategy": self.validated_data.get("apply_strategy"),
            "delay_seconds": self.validated_data.get("delay_seconds"),
            "grace_timeout_seconds": self.validated_data.get("grace_timeout_seconds"),
        }

    def get_config_changes(self) -> dict:
        """Extract config changes (excluding apply strategy fields)."""
        exclude_fields = {"apply_strategy", "delay_seconds", "grace_timeout_seconds"}
        return {
            k: v for k, v in self.validated_data.items()
            if k not in exclude_fields and v is not None
        }


# =============================================================================
# Config Serializers with Apply Strategy Support
# =============================================================================


class CircuitBreakerConfigSerializer(ApplyStrategyMixin):
    """Serializer for Circuit Breaker configuration."""

    enabled = serializers.BooleanField(required=False, default=True)
    failure_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    recovery_timeout = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    success_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    half_open_max_calls = serializers.IntegerField(required=False, min_value=1, max_value=100)
    half_open_request_limit = serializers.IntegerField(required=False, min_value=1, max_value=1000)
    rate_limit_cascade_threshold = serializers.IntegerField(required=False, min_value=1, max_value=1000)
    rate_limit_cascade_window_seconds = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    self_ddos_protection_enabled = serializers.BooleanField(required=False)
    self_ddos_request_threshold = serializers.IntegerField(required=False, min_value=1, max_value=10000)
    self_ddos_window_seconds = serializers.IntegerField(required=False, min_value=1, max_value=300)
    self_ddos_backoff_multiplier = serializers.FloatField(required=False, min_value=1.0, max_value=10.0)


class DLQConfigSerializer(ApplyStrategyMixin):
    """Serializer for DLQ configuration."""

    enabled = serializers.BooleanField(required=False, default=True)
    max_retries = serializers.IntegerField(required=False, min_value=1, max_value=20)
    retry_delay = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    expiry_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    retention_days = serializers.IntegerField(required=False, min_value=1, max_value=365)
    batch_size = serializers.IntegerField(required=False, min_value=1, max_value=1000)
    max_replay_attempts = serializers.IntegerField(required=False, min_value=1, max_value=10)


class RetryConfigSerializer(ApplyStrategyMixin):
    """Serializer for Retry configuration."""

    max_attempts = serializers.IntegerField(required=False, min_value=1, max_value=20)
    backoff_strategy = serializers.ChoiceField(
        required=False,
        choices=["exponential", "linear", "constant", "decorrelated_jitter"],
    )
    backoff_base = serializers.IntegerField(required=False, min_value=1, max_value=10)
    base_delay = serializers.FloatField(required=False, min_value=0.1, max_value=60.0)
    max_delay = serializers.FloatField(required=False, min_value=1.0, max_value=3600.0)
    min_delay = serializers.IntegerField(required=False, min_value=1, max_value=60)
    jitter = serializers.BooleanField(required=False)
    jitter_percent = serializers.IntegerField(required=False, min_value=0, max_value=100)


class SLAConfigSerializer(ApplyStrategyMixin):
    """Serializer for SLA configuration."""

    default_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    thresholds_by_domain = serializers.DictField(
        required=False,
        child=serializers.IntegerField(min_value=1, max_value=720),
    )


class RateLimitConfigSerializer(ApplyStrategyMixin):
    """Serializer for Rate Limit configuration."""

    base_delay = serializers.FloatField(required=False, min_value=0.1, max_value=60.0)
    max_delay = serializers.FloatField(required=False, min_value=1.0, max_value=300.0)
    jitter_percent = serializers.FloatField(required=False, min_value=0.0, max_value=100.0)
    default_retry_after = serializers.FloatField(required=False, min_value=0.1, max_value=60.0)
    backoff_multiplier = serializers.FloatField(required=False, min_value=1.0, max_value=10.0)


class SecurityConfigSerializer(ApplyStrategyMixin):
    """Serializer for Security configuration."""

    rate_limit_window_seconds = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    rate_limit_max_requests = serializers.IntegerField(required=False, min_value=1, max_value=10000)
    temporary_ban_hours = serializers.IntegerField(required=False, min_value=1, max_value=168)
    permanent_ban_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    suspicious_ip_cache_timeout = serializers.IntegerField(required=False, min_value=60, max_value=604800)
    injection_ban_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    failed_login_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)


class IdempotencyConfigSerializer(ApplyStrategyMixin):
    """Serializer for Idempotency configuration."""

    default_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    extended_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=86400)
    short_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=300)
    clock_skew_tolerance_seconds = serializers.FloatField(required=False, min_value=0.0, max_value=60.0)


class NotificationConfigSerializer(ApplyStrategyMixin):
    """Serializer for Notification configuration."""

    enabled = serializers.BooleanField(required=False)
    channels = serializers.ListField(
        required=False,
        child=serializers.ChoiceField(choices=["email", "slack", "webhook"]),
    )
    critical_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    warning_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    slack_block_text_limit = serializers.IntegerField(required=False, min_value=100, max_value=10000)
    description_max_length = serializers.IntegerField(required=False, min_value=50, max_value=5000)
    action_taken_max_length = serializers.IntegerField(required=False, min_value=50, max_value=1000)
    title_max_length = serializers.IntegerField(required=False, min_value=20, max_value=500)
    notification_timeout_seconds = serializers.IntegerField(required=False, min_value=1, max_value=60)


class ForensicConfigSerializer(ApplyStrategyMixin):
    """Serializer for Forensic configuration."""

    error_message_max_length = serializers.IntegerField(required=False, min_value=50, max_value=5000)
    response_body_max_length = serializers.IntegerField(required=False, min_value=100, max_value=100000)
    user_agent_max_length = serializers.IntegerField(required=False, min_value=50, max_value=2000)


class MetricsConfigSerializer(ApplyStrategyMixin):
    """Serializer for Metrics configuration."""

    enabled = serializers.BooleanField(required=False)
    prefix = serializers.CharField(required=False, max_length=50)
    collection_interval = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    export_prometheus = serializers.BooleanField(required=False)


class ErrorBudgetConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Error Budget configuration.
    
    Error Budget 및 Burn Rate 임계값 설정.
    """

    # Error Budget 임계값 (%)
    threshold_healthy = serializers.FloatField(
        required=False, min_value=50.0, max_value=100.0,
        help_text="정상 상태 임계값 (기본: 75%)"
    )
    threshold_caution = serializers.FloatField(
        required=False, min_value=20.0, max_value=80.0,
        help_text="주의 상태 임계값 (기본: 50%)"
    )
    threshold_warning = serializers.FloatField(
        required=False, min_value=5.0, max_value=50.0,
        help_text="경고 상태 임계값 (기본: 20%)"
    )
    threshold_critical = serializers.FloatField(
        required=False, min_value=0.0, max_value=20.0,
        help_text="위험 상태 임계값 (기본: 0%)"
    )

    # Burn Rate 임계값
    burn_rate_fast_critical = serializers.FloatField(
        required=False, min_value=10.0, max_value=50.0,
        help_text="빠른 소진 위험 임계값 (기본: 14.4x)"
    )
    burn_rate_fast_warning = serializers.FloatField(
        required=False, min_value=3.0, max_value=15.0,
        help_text="빠른 소진 경고 임계값 (기본: 6.0x)"
    )
    burn_rate_slow_warning = serializers.FloatField(
        required=False, min_value=1.0, max_value=10.0,
        help_text="느린 소진 경고 임계값 (기본: 3.0x)"
    )
    burn_rate_slow_info = serializers.FloatField(
        required=False, min_value=0.5, max_value=3.0,
        help_text="정상 소진율 임계값 (기본: 1.0x)"
    )

    # Fail-Safe 설정
    failsafe_alert_enabled = serializers.BooleanField(
        required=False,
        help_text="Fail-Safe 발동 시 알림 발송 여부"
    )
    failsafe_cooldown_seconds = serializers.IntegerField(
        required=False, min_value=60, max_value=3600,
        help_text="연속 알림 방지 쿨다운 (초)"
    )

    # Heartbeat (Dead Man's Snitch) 설정
    heartbeat_enabled = serializers.BooleanField(
        required=False,
        help_text="Heartbeat (Dead Man's Snitch) 활성화 여부 (기본: True)"
    )
    heartbeat_interval_seconds = serializers.IntegerField(
        required=False, min_value=10, max_value=300,
        help_text="Heartbeat 발송 주기 (초, 기본: 60초)"
    )
    heartbeat_timeout_seconds = serializers.IntegerField(
        required=False, min_value=30, max_value=600,
        help_text="Heartbeat 타임아웃 (초, 기본: 120초, 이 시간 내 미응답시 Dead)"
    )

    # 복구 알림 (Recovery Notification) 설정
    recovery_alert_enabled = serializers.BooleanField(
        required=False,
        help_text="복구 완료 알림 발송 여부 (기본: True)"
    )
    recovery_alert_include_downtime = serializers.BooleanField(
        required=False,
        help_text="복구 알림에 장애 시간 포함 여부 (기본: True)"
    )

    # Override 에스컬레이션 설정
    escalation_enabled = serializers.BooleanField(
        required=False,
        help_text="Override 에스컬레이션 활성화 여부 (기본: True)"
    )
    escalation_channel = serializers.CharField(
        required=False, max_length=100,
        help_text="에스컬레이션 알림 채널 (기본: #governance)"
    )
    escalation_mention = serializers.CharField(
        required=False, max_length=200,
        help_text="에스컬레이션 멘션 대상 (기본: @cto @security)"
    )

    def validate(self, data):
        """Validate threshold ordering and heartbeat settings."""
        # 임계값 순서 검증: healthy > caution > warning > critical
        thresholds = [
            ('threshold_healthy', data.get('threshold_healthy', 75.0)),
            ('threshold_caution', data.get('threshold_caution', 50.0)),
            ('threshold_warning', data.get('threshold_warning', 20.0)),
            ('threshold_critical', data.get('threshold_critical', 0.0)),
        ]
        for i in range(len(thresholds) - 1):
            if thresholds[i][1] <= thresholds[i + 1][1]:
                raise serializers.ValidationError(
                    f"{thresholds[i][0]}은 {thresholds[i + 1][0]}보다 커야 합니다."
                )
        
        # Heartbeat 타임아웃은 interval보다 커야 함
        interval = data.get('heartbeat_interval_seconds', 60)
        timeout = data.get('heartbeat_timeout_seconds', 120)
        if timeout <= interval:
            raise serializers.ValidationError(
                "heartbeat_timeout_seconds는 heartbeat_interval_seconds보다 커야 합니다."
            )
        
        return data


# =============================================================================
# Pending Change Serializers
# =============================================================================


class PendingConfigChangeSerializer(serializers.Serializer):
    """Serializer for pending configuration change."""

    id = serializers.CharField(read_only=True)
    config_type = serializers.CharField(read_only=True)
    changes = serializers.DictField(read_only=True)
    strategy = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.CharField(read_only=True)
    scheduled_at = serializers.CharField(read_only=True)
    applied_at = serializers.CharField(read_only=True, allow_null=True)
    cancelled_at = serializers.CharField(read_only=True, allow_null=True)
    previous_values = serializers.DictField(read_only=True)


class CancelPendingChangeSerializer(serializers.Serializer):
    """Serializer for cancelling a pending change."""

    reason = serializers.CharField(required=False, max_length=500)
