"""
Runtime Configuration API Serializers.

Serializers for validating and serializing runtime configuration updates.
Includes apply strategy support (immediate, delayed, graceful).

Phase 6: Fail-Safe Default 강화 추가.
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

from rest_framework import serializers


# =============================================================================
# Apply Strategy Mixin
# =============================================================================


class ApplyStrategyMixin(serializers.Serializer):
    """
    Mixin that adds apply strategy fields to config serializers.
    
    Phase 6: Safe Default 검증 및 폴백 기능 추가.
    """

    # 서브클래스에서 오버라이드하여 config_type 지정
    _config_type: str = ""

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
    reason = serializers.CharField(
        required=False,
        max_length=500,
        allow_blank=True,
        help_text="Reason for the configuration change (optional, for audit trail)",
    )

    def get_apply_options(self) -> dict:
        """Extract apply strategy options from validated data."""
        return {
            "strategy": self.validated_data.get("apply_strategy"),
            "delay_seconds": self.validated_data.get("delay_seconds"),
            "grace_timeout_seconds": self.validated_data.get("grace_timeout_seconds"),
            "reason": self.validated_data.get("reason", ""),
        }

    def get_config_changes(self) -> dict:
        """Extract config changes (excluding apply strategy fields)."""
        exclude_fields = {"apply_strategy", "delay_seconds", "grace_timeout_seconds", "reason"}
        return {k: v for k, v in self.validated_data.items() if k not in exclude_fields and v is not None}

    def validate_with_safe_fallback(self, data: dict) -> dict:
        """
        Safe Default 검증 및 폴백 적용.
        
        잘못된 값은 Safe Default로 대체됩니다.
        서브클래스에서 _config_type을 설정해야 합니다.
        
        Args:
            data: 검증할 데이터
            
        Returns:
            Safe Default가 적용된 데이터
        """
        if not self._config_type:
            return data
        
        try:
            from selfhealing.core.safe_defaults import validate_with_safe_fallback
            return validate_with_safe_fallback(self._config_type, data)
        except ImportError:
            return data


# =============================================================================
# Config Serializers with Apply Strategy Support
# =============================================================================


class CircuitBreakerConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Circuit Breaker configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "circuit_breaker"

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

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class DLQConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for DLQ configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "dlq"

    enabled = serializers.BooleanField(required=False, default=True)
    max_retries = serializers.IntegerField(required=False, min_value=1, max_value=20)
    retry_delay = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    expiry_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    retention_days = serializers.IntegerField(required=False, min_value=1, max_value=365)
    batch_size = serializers.IntegerField(required=False, min_value=1, max_value=1000)
    max_replay_attempts = serializers.IntegerField(required=False, min_value=1, max_value=10)

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class RetryConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Retry configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "retry"

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

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class SLAConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for SLA configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "sla"

    default_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    thresholds_by_domain = serializers.DictField(
        required=False,
        child=serializers.IntegerField(min_value=1, max_value=720),
    )

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class SLODefinitionSerializer(serializers.Serializer):
    """
    Serializer for a single SLO definition.

    API를 통해 SLO를 생성/수정할 때 사용됩니다.
    """

    name = serializers.CharField(
        max_length=100,
        help_text="SLO 이름 (예: api_availability, checkout_latency)",
    )
    sli_type = serializers.ChoiceField(
        required=False,
        choices=["availability", "latency_p99", "latency_p95", "latency_p50", "error_rate", "throughput"],
        help_text="SLI 유형",
    )
    target = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        help_text="목표값 (0.0~1.0, 예: 0.999 = 99.9%)",
    )
    window_days = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=365,
        help_text="측정 윈도우 (일)",
    )
    description = serializers.CharField(
        required=False,
        max_length=500,
        allow_blank=True,
        help_text="SLO 설명",
    )
    service_name = serializers.CharField(
        required=False,
        max_length=100,
        allow_blank=True,
        help_text="서비스 이름",
    )
    domain = serializers.CharField(
        required=False,
        max_length=100,
        allow_blank=True,
        help_text="도메인 (예: payment, order)",
    )
    warning_threshold = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        allow_null=True,
        help_text="경고 임계값 (0.0~1.0)",
    )
    critical_threshold = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=1.0,
        allow_null=True,
        help_text="위험 임계값 (0.0~1.0)",
    )
    fast_burn_rate = serializers.FloatField(
        required=False,
        min_value=1.0,
        max_value=100.0,
        help_text="빠른 소진율 임계값 (기본: 14.4x)",
    )
    slow_burn_rate = serializers.FloatField(
        required=False,
        min_value=0.5,
        max_value=50.0,
        help_text="느린 소진율 임계값 (기본: 3.0x)",
    )

    def validate_name(self, value):
        """SLO 이름 검증: 영문, 숫자, 언더스코어만 허용."""
        import re
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', value):
            raise serializers.ValidationError(
                "SLO 이름은 영문자로 시작하고 영문, 숫자, 언더스코어만 허용됩니다."
            )
        return value

    def validate(self, data):
        """SLO 정의 전체 검증."""
        # warning_threshold와 critical_threshold 순서 검증
        warning = data.get("warning_threshold")
        critical = data.get("critical_threshold")
        target = data.get("target", 0.999)

        if warning is not None and critical is not None:
            if warning <= critical:
                raise serializers.ValidationError(
                    "warning_threshold는 critical_threshold보다 커야 합니다."
                )

        if warning is not None and warning <= target:
            raise serializers.ValidationError(
                "warning_threshold는 target보다 커야 합니다."
            )

        if critical is not None and critical < target:
            raise serializers.ValidationError(
                "critical_threshold는 target 이상이어야 합니다."
            )

        # burn_rate 순서 검증
        fast = data.get("fast_burn_rate", 14.4)
        slow = data.get("slow_burn_rate", 3.0)
        if fast <= slow:
            raise serializers.ValidationError(
                "fast_burn_rate는 slow_burn_rate보다 커야 합니다."
            )

        return data


class SLOConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for SLO configuration.

    SLO 정의를 API를 통해 동적으로 관리할 수 있습니다.
    - GET: 현재 등록된 모든 SLO 조회
    - PUT: SLO 기본값 업데이트 및 SLO 추가/수정
    - DELETE: 특정 SLO 삭제 (별도 엔드포인트)
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "slo"

    # 기본값 설정
    default_window_days = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=365,
        help_text="새 SLO 생성 시 기본 윈도우 (일)",
    )
    default_target = serializers.FloatField(
        required=False,
        min_value=0.9,
        max_value=1.0,
        help_text="새 SLO 생성 시 기본 타겟 (0.9~1.0)",
    )
    default_fast_burn_rate = serializers.FloatField(
        required=False,
        min_value=1.0,
        max_value=100.0,
        help_text="새 SLO 생성 시 기본 빠른 소진율",
    )
    default_slow_burn_rate = serializers.FloatField(
        required=False,
        min_value=0.5,
        max_value=50.0,
        help_text="새 SLO 생성 시 기본 느린 소진율",
    )

    # SLO 추가/수정용 (단일 SLO 또는 리스트)
    slo = SLODefinitionSerializer(required=False, help_text="추가/수정할 SLO 정의 (단일)")
    slos = serializers.ListField(
        required=False,
        child=SLODefinitionSerializer(),
        help_text="추가/수정할 SLO 정의 (복수)",
    )

    def validate(self, data):
        """기본값 burn_rate 순서 검증 + Safe Default 폴백."""
        fast = data.get("default_fast_burn_rate")
        slow = data.get("default_slow_burn_rate")
        if fast is not None and slow is not None and fast <= slow:
            raise serializers.ValidationError(
                "default_fast_burn_rate는 default_slow_burn_rate보다 커야 합니다."
            )
        return self.validate_with_safe_fallback(data)


class RateLimitConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Rate Limit configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "rate_limit"

    base_delay = serializers.FloatField(required=False, min_value=0.1, max_value=60.0)
    max_delay = serializers.FloatField(required=False, min_value=1.0, max_value=300.0)
    jitter_percent = serializers.FloatField(required=False, min_value=0.0, max_value=100.0)
    default_retry_after = serializers.FloatField(required=False, min_value=0.1, max_value=60.0)
    backoff_multiplier = serializers.FloatField(required=False, min_value=1.0, max_value=10.0)

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class SecurityConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Security configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "security"

    rate_limit_window_seconds = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    rate_limit_max_requests = serializers.IntegerField(required=False, min_value=1, max_value=10000)
    temporary_ban_hours = serializers.IntegerField(required=False, min_value=1, max_value=168)
    permanent_ban_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)
    suspicious_ip_cache_timeout = serializers.IntegerField(required=False, min_value=60, max_value=604800)
    injection_ban_hours = serializers.IntegerField(required=False, min_value=1, max_value=720)
    failed_login_threshold = serializers.IntegerField(required=False, min_value=1, max_value=100)

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class IdempotencyConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Idempotency configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "idempotency"

    default_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    extended_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=86400)
    short_cache_ttl = serializers.IntegerField(required=False, min_value=1, max_value=300)
    clock_skew_tolerance_seconds = serializers.FloatField(required=False, min_value=0.0, max_value=60.0)

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class NotificationConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Notification configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "notification"

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

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class ForensicConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Forensic configuration.
    
    Forensic 분석 및 디버깅 관련 설정.
    Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md (Phase 5, 6)
    """

    _config_type = "forensic"

    # 기존 필드
    error_message_max_length = serializers.IntegerField(required=False, min_value=50, max_value=5000)
    response_body_max_length = serializers.IntegerField(required=False, min_value=100, max_value=100000)
    user_agent_max_length = serializers.IntegerField(required=False, min_value=50, max_value=2000)
    
    # Phase 5: 추가 Forensic 설정 (이전 env만 노출되었던 설정들)
    max_stack_frames = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=200,
        help_text="스택 프레임 최대 수집 개수. 기본: 50",
    )
    max_context_size_bytes = serializers.IntegerField(
        required=False,
        min_value=1024,
        max_value=1048576,  # 1MB 상한
        help_text="컨텍스트 데이터 최대 크기 (bytes). 기본: 65536 (64KB)",
    )
    include_local_variables = serializers.BooleanField(
        required=False,
        help_text="스택 트레이스에 로컬 변수 포함 여부. 기본: False (보안상 비활성화)",
    )
    sanitize_sensitive_data = serializers.BooleanField(
        required=False,
        help_text="민감 데이터 마스킹 여부. 기본: True",
    )
    sensitive_key_patterns = serializers.ListField(
        required=False,
        child=serializers.CharField(max_length=100),
        help_text="마스킹할 키 패턴 목록. 기본: [password, secret, token, key, auth]",
    )

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class MetricsConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Metrics configuration.
    
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "metrics"

    enabled = serializers.BooleanField(required=False)
    prefix = serializers.CharField(required=False, max_length=50)
    collection_interval = serializers.IntegerField(required=False, min_value=1, max_value=3600)
    export_prometheus = serializers.BooleanField(required=False)

    # Jitter settings (Thundering Herd prevention)
    # Clamping: min=0.0 (음수 방지), max=300.0 (5분 상한)
    jitter_enabled = serializers.BooleanField(
        required=False,
        help_text="Jitter 활성화 여부 (기본: True). 분산 환경에서 Thundering Herd 방지",
    )
    jitter_max_delay_seconds = serializers.FloatField(
        required=False,
        min_value=0.0,  # 음수 방지 (Clamping)
        max_value=300.0,  # 5분 상한
        help_text="최대 Jitter 지연 시간 (초). 0-300 범위 (기본: 60.0)",
    )

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


class ErrorBudgetConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Error Budget configuration.

    Error Budget 및 Burn Rate 임계값 설정.
    Phase 6: Safe Default 폴백 적용.
    """

    _config_type = "error_budget"

    # Error Budget 임계값 (%)
    threshold_healthy = serializers.FloatField(
        required=False, min_value=50.0, max_value=100.0, help_text="정상 상태 임계값 (기본: 75%)"
    )
    threshold_caution = serializers.FloatField(
        required=False, min_value=20.0, max_value=80.0, help_text="주의 상태 임계값 (기본: 50%)"
    )
    threshold_warning = serializers.FloatField(
        required=False, min_value=5.0, max_value=50.0, help_text="경고 상태 임계값 (기본: 20%)"
    )
    threshold_critical = serializers.FloatField(
        required=False, min_value=0.0, max_value=20.0, help_text="위험 상태 임계값 (기본: 0%)"
    )

    # Burn Rate 임계값
    burn_rate_fast_critical = serializers.FloatField(
        required=False, min_value=10.0, max_value=50.0, help_text="빠른 소진 위험 임계값 (기본: 14.4x)"
    )
    burn_rate_fast_warning = serializers.FloatField(
        required=False, min_value=3.0, max_value=15.0, help_text="빠른 소진 경고 임계값 (기본: 6.0x)"
    )
    burn_rate_slow_warning = serializers.FloatField(
        required=False, min_value=1.0, max_value=10.0, help_text="느린 소진 경고 임계값 (기본: 3.0x)"
    )
    burn_rate_slow_info = serializers.FloatField(
        required=False, min_value=0.5, max_value=3.0, help_text="정상 소진율 임계값 (기본: 1.0x)"
    )

    # Fail-Safe 설정
    failsafe_alert_enabled = serializers.BooleanField(required=False, help_text="Fail-Safe 발동 시 알림 발송 여부")
    failsafe_cooldown_seconds = serializers.IntegerField(
        required=False, min_value=60, max_value=3600, help_text="연속 알림 방지 쿨다운 (초)"
    )

    # Heartbeat (Dead Man's Snitch) 설정
    heartbeat_enabled = serializers.BooleanField(
        required=False, help_text="Heartbeat (Dead Man's Snitch) 활성화 여부 (기본: True)"
    )
    heartbeat_interval_seconds = serializers.IntegerField(
        required=False, min_value=10, max_value=300, help_text="Heartbeat 발송 주기 (초, 기본: 60초)"
    )
    heartbeat_timeout_seconds = serializers.IntegerField(
        required=False, min_value=30, max_value=600, help_text="Heartbeat 타임아웃 (초, 기본: 120초, 이 시간 내 미응답시 Dead)"
    )

    # 복구 알림 (Recovery Notification) 설정
    recovery_alert_enabled = serializers.BooleanField(required=False, help_text="복구 완료 알림 발송 여부 (기본: True)")
    recovery_alert_include_downtime = serializers.BooleanField(
        required=False, help_text="복구 알림에 장애 시간 포함 여부 (기본: True)"
    )

    # Override 에스컬레이션 설정
    escalation_enabled = serializers.BooleanField(required=False, help_text="Override 에스컬레이션 활성화 여부 (기본: True)")
    escalation_channel = serializers.CharField(
        required=False, max_length=100, help_text="에스컬레이션 알림 채널 (기본: #governance)"
    )
    escalation_mention = serializers.CharField(
        required=False, max_length=200, help_text="에스컬레이션 멘션 대상 (기본: @cto @security)"
    )

    def validate(self, data):
        """Validate threshold ordering and heartbeat settings + Safe Default 폴백."""
        # 임계값 순서 검증: healthy > caution > warning > critical
        thresholds = [
            ("threshold_healthy", data.get("threshold_healthy", 75.0)),
            ("threshold_caution", data.get("threshold_caution", 50.0)),
            ("threshold_warning", data.get("threshold_warning", 20.0)),
            ("threshold_critical", data.get("threshold_critical", 0.0)),
        ]
        for i in range(len(thresholds) - 1):
            if thresholds[i][1] <= thresholds[i + 1][1]:
                raise serializers.ValidationError(f"{thresholds[i][0]}은 {thresholds[i + 1][0]}보다 커야 합니다.")

        # Heartbeat 타임아웃은 interval보다 커야 함
        interval = data.get("heartbeat_interval_seconds", 60)
        timeout = data.get("heartbeat_timeout_seconds", 120)
        if timeout <= interval:
            raise serializers.ValidationError("heartbeat_timeout_seconds는 heartbeat_interval_seconds보다 커야 합니다.")

        return self.validate_with_safe_fallback(data)


# =============================================================================
# Logging Configuration Serializer (Phase 5)
# Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
# =============================================================================


class LoggingConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Logging configuration.
    
    각 Self-Healing 컴포넌트별 로깅 레벨 설정.
    이전에는 환경변수로만 제어 가능했던 설정들을 API로 노출.
    
    Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md (Phase 5, 6)
    """

    _config_type = "logging"

    LEVEL_CHOICES = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

    # 컴포넌트별 로그 레벨
    dlq_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="DLQ 관련 로그 레벨. 기본: INFO",
    )
    circuit_breaker_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="Circuit Breaker 로그 레벨. 기본: INFO",
    )
    replay_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="DLQ Replay 로그 레벨. 기본: INFO",
    )
    sla_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="SLA/SLO 모니터링 로그 레벨. 기본: INFO",
    )
    forensic_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="Forensic 분석 로그 레벨. 기본: DEBUG",
    )
    emergency_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="Emergency Mode 로그 레벨. 기본: WARNING",
    )
    chaos_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="Chaos Engineering 로그 레벨. 기본: INFO",
    )
    l2_storage_log_level = serializers.ChoiceField(
        required=False,
        choices=LEVEL_CHOICES,
        help_text="L2 Storage Resilience 로그 레벨. 기본: INFO",
    )

    # 로그 포맷 설정
    include_timestamps = serializers.BooleanField(
        required=False,
        help_text="로그에 타임스탬프 포함 여부. 기본: True",
    )
    include_request_id = serializers.BooleanField(
        required=False,
        help_text="로그에 Request ID 포함 여부. 기본: True",
    )
    include_user_info = serializers.BooleanField(
        required=False,
        help_text="로그에 사용자 정보 포함 여부. 기본: False (보안상 비활성화)",
    )

    # 로그 출력 설정
    console_output_enabled = serializers.BooleanField(
        required=False,
        help_text="콘솔 로그 출력 활성화. 기본: True",
    )
    file_output_enabled = serializers.BooleanField(
        required=False,
        help_text="파일 로그 출력 활성화. 기본: False",
    )
    structured_json = serializers.BooleanField(
        required=False,
        help_text="JSON 구조화 로그 포맷 사용. 기본: True (운영환경)",
    )

    def validate(self, attrs):
        """검증 + Safe Default 폴백."""
        validated = super().validate(attrs)
        return self.validate_with_safe_fallback(validated)


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


# =============================================================================
# L2 Storage Resilience Serializers
# =============================================================================


class L2StorageConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for L2 Storage resilience configuration.
    
    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md
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


# =============================================================================
# Replay Automation Configuration Serializer
# Reference: docs/self_healing/middleware_system/19_DLQ_AUTOMATION_BLUEPRINT.md
# =============================================================================


class ReplayAutomationConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Replay Automation configuration.
    
    Manages DLQ Replay automation settings including:
    - Track 1: Event-driven replay on CB recovery
    - Track 2: Scheduled batch replay
    - Track 3: Traffic-aware replay (future)
    - Adaptive mode for dynamic batch sizing
    - Phase 4: Domain priority-based replay
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

    # Phase 4: Domain Priority Policy
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
        help_text="Domain-specific max_retries override. Example: {\"payment\": 10, \"notification\": 3}",
    )
    domain_on_circuit_close = serializers.DictField(
        required=False,
        child=serializers.BooleanField(),
        help_text="Domain-specific Track 1 trigger setting. Example: {\"payment\": true, \"analytics\": false}",
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

