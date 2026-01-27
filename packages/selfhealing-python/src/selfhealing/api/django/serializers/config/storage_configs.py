"""
L2 Storage and Replay Automation Configuration Serializers.

L2StorageConfig, L2StorageStatus, ShadowLog, ReplayAutomation serializers.
"""

from rest_framework import serializers

from .base import ApplyStrategyMixin


class L2StorageConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for L2 Storage resilience configuration.
    """

    # 타임아웃 설정 (ms)
    redis_timeout_ms = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=1000,
        help_text="Redis 어댑터 타임아웃 (ms). 기본: 50ms",
    )
    database_timeout_ms = serializers.IntegerField(
        required=False,
        min_value=50,
        max_value=5000,
        help_text="Database 어댑터 타임아웃 (ms). 기본: 200ms",
    )
    fallback_timeout_ms = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=1000,
        help_text="알 수 없는 어댑터 폴백 타임아웃 (ms). 기본: 100ms",
    )

    # Shadow Logging 설정
    shadow_log_enabled = serializers.BooleanField(
        required=False,
        help_text="Shadow Log 활성화 여부. 기본: true",
    )
    shadow_log_max_entries = serializers.IntegerField(
        required=False,
        min_value=100,
        max_value=10000,
        help_text="Shadow Log 최대 보관 항목 수. 기본: 1000",
    )

    # Drift Reconciliation 설정
    reconciliation_jitter_min_seconds = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=60.0,
        help_text="Reconciliation Jitter 최소 시간 (초). 기본: 0.0",
    )
    reconciliation_jitter_max_seconds = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=60.0,
        help_text="Reconciliation Jitter 최대 시간 (초). 기본: 5.0",
    )

    # 헬스체크 설정
    health_check_interval_seconds = serializers.FloatField(
        required=False,
        min_value=5.0,
        max_value=300.0,
        help_text="L2 헬스체크 주기 (초). 기본: 30.0",
    )
    health_check_timeout_ms = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=1000,
        help_text="L2 헬스체크 타임아웃 (ms). 기본: 100",
    )

    def validate(self, data):
        """Cross-field validation."""
        # Jitter min <= max 검증
        jitter_min = data.get("reconciliation_jitter_min_seconds")
        jitter_max = data.get("reconciliation_jitter_max_seconds")

        if jitter_min is not None and jitter_max is not None:
            if jitter_min > jitter_max:
                raise serializers.ValidationError(
                    "reconciliation_jitter_min_seconds는 "
                    "reconciliation_jitter_max_seconds보다 작거나 같아야 합니다."
                )

        return data


class L2StorageStatusSerializer(serializers.Serializer):
    """Serializer for L2 Storage status response."""

    l1_type = serializers.CharField(read_only=True)
    l1_count = serializers.IntegerField(read_only=True)
    l2_enabled = serializers.BooleanField(read_only=True)
    l2_type = serializers.CharField(read_only=True, allow_null=True)
    l2_adapter_type = serializers.CharField(read_only=True)
    l2_healthy = serializers.BooleanField(read_only=True)
    l2_consecutive_failures = serializers.IntegerField(read_only=True)
    l2_last_error_time = serializers.CharField(read_only=True, allow_null=True)
    sync_interval_seconds = serializers.FloatField(read_only=True)
    last_sync_time = serializers.CharField(read_only=True, allow_null=True)
    timeout_ms = serializers.FloatField(read_only=True)


class ShadowLogEntrySerializer(serializers.Serializer):
    """Serializer for Shadow Log entry."""

    service_name = serializers.CharField(read_only=True)
    intended_state = serializers.CharField(read_only=True)
    failure_time = serializers.DateTimeField(read_only=True)
    error_message = serializers.CharField(read_only=True)
    l1_state_at_failure = serializers.CharField(read_only=True)
    adapter_type = serializers.CharField(read_only=True)
    operation = serializers.CharField(read_only=True)
    synced_after_recovery = serializers.BooleanField(read_only=True)
    recovery_time = serializers.DateTimeField(read_only=True, allow_null=True)


class ShadowLogStatsSerializer(serializers.Serializer):
    """Serializer for Shadow Log statistics."""

    total_records = serializers.IntegerField(read_only=True)
    unsynced_count = serializers.IntegerField(read_only=True)
    affected_services = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    max_entries = serializers.IntegerField(read_only=True)
    oldest_record = serializers.CharField(read_only=True, allow_null=True)
    newest_record = serializers.CharField(read_only=True, allow_null=True)


class ReplayAutomationConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Replay Automation configuration.

    Manages DLQ Replay automation settings including:
    - Track 1: Event-driven replay on CB recovery
    - Track 2: Scheduled batch replay
    - Track 3: Traffic-aware replay (future)
    - Adaptive mode for dynamic batch sizing
    - Domain priority-based replay
    """

    _config_type = "replay_automation"

    # Track 1: Event-Driven Replay
    track1_enabled = serializers.BooleanField(
        required=False,
        help_text="Enable Track 1 (event-driven replay on circuit breaker close)",
    )
    track1_max_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=500,
        help_text="Maximum items to replay on CB recovery",
    )

    # Track 2: Scheduled Batch Replay
    track2_enabled = serializers.BooleanField(
        required=False,
        help_text="Enable Track 2 (scheduled batch replay)",
    )
    track2_max_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=500,
        help_text="Maximum items per scheduled batch",
    )

    # Track 3: Traffic-Aware Replay
    track3_enabled = serializers.BooleanField(
        required=False,
        help_text="Enable Track 3 (traffic-aware replay) - Future feature",
    )
    track3_max_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
        help_text="Maximum items for traffic-aware replay",
    )

    # Adaptive Mode
    adaptive_enabled = serializers.BooleanField(
        required=False,
        help_text="Enable adaptive batch sizing based on success rate",
    )
    adaptive_min_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=50,
        help_text="Minimum batch size for adaptive mode",
    )
    adaptive_max_items = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=500,
        help_text="Maximum batch size for adaptive mode",
    )
    adaptive_failure_threshold = serializers.FloatField(
        required=False,
        min_value=0.05,
        max_value=0.5,
        help_text="Failure rate threshold to trigger batch size reduction (0.05-0.5)",
    )

    # Domain Priority Policy
    priority_enabled = serializers.BooleanField(
        required=False,
        help_text="Enable priority-based batch processing by domain",
    )
    domain_priorities = serializers.DictField(
        required=False,
        child=serializers.ChoiceField(choices=["critical", "normal", "low"]),
        help_text='Domain priority mapping. Values: "critical", "normal", "low". Example: {"payment": "critical", "notification": "low"}',
    )
    domain_max_retries = serializers.DictField(
        required=False,
        child=serializers.IntegerField(min_value=1, max_value=20),
        help_text='Domain-specific max_retries override. Example: {"payment": 10, "notification": 3}',
    )
    domain_on_circuit_close = serializers.DictField(
        required=False,
        child=serializers.BooleanField(),
        help_text='Domain-specific Track 1 trigger setting. Example: {"payment": true, "analytics": false}',
    )

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)

        # adaptive_min <= adaptive_max 검증
        adaptive_min = validated.get("adaptive_min_items")
        adaptive_max = validated.get("adaptive_max_items")

        if adaptive_min is not None and adaptive_max is not None:
            if adaptive_min > adaptive_max:
                raise serializers.ValidationError(
                    "adaptive_min_items must be less than or equal to adaptive_max_items"
                )

        # domain_priorities 값 검증 (이미 ChoiceField로 검증되지만 추가 확인)
        domain_priorities = validated.get("domain_priorities", {})
        valid_priorities = {"critical", "normal", "low"}
        for domain, priority in domain_priorities.items():
            if priority not in valid_priorities:
                raise serializers.ValidationError(
                    f"Invalid priority '{priority}' for domain '{domain}'. "
                    f"Must be one of: {valid_priorities}"
                )

        return self.validate_with_safe_fallback(validated)
