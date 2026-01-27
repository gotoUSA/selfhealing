"""
SLA/SLO Configuration Serializers.

SLA, SLO Definition, SLO Config, ErrorBudget serializers.

Fail-Safe Default 강화 추가.
"""

from rest_framework import serializers

from .base import ApplyStrategyMixin


class SLAConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for SLA configuration.

    Safe Default 폴백 적용.
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
        choices=[
            "availability",
            "latency_p99",
            "latency_p95",
            "latency_p50",
            "error_rate",
            "throughput",
        ],
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

        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", value):
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

    Safe Default 폴백 적용.
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
    slo = SLODefinitionSerializer(
        required=False, help_text="추가/수정할 SLO 정의 (단일)"
    )
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


class ErrorBudgetConfigSerializer(ApplyStrategyMixin):
    """
    Serializer for Error Budget configuration.

    Error Budget 및 Burn Rate 임계값 설정.
    Safe Default 폴백 적용.
    """

    _config_type = "error_budget"

    # Error Budget 임계값 (%)
    threshold_healthy = serializers.FloatField(
        required=False,
        min_value=50.0,
        max_value=100.0,
        help_text="정상 상태 임계값 (기본: 75%)",
    )
    threshold_caution = serializers.FloatField(
        required=False,
        min_value=20.0,
        max_value=80.0,
        help_text="주의 상태 임계값 (기본: 50%)",
    )
    threshold_warning = serializers.FloatField(
        required=False,
        min_value=5.0,
        max_value=50.0,
        help_text="경고 상태 임계값 (기본: 20%)",
    )
    threshold_critical = serializers.FloatField(
        required=False,
        min_value=0.0,
        max_value=20.0,
        help_text="위험 상태 임계값 (기본: 0%)",
    )

    # Burn Rate 임계값
    burn_rate_fast_critical = serializers.FloatField(
        required=False,
        min_value=10.0,
        max_value=50.0,
        help_text="빠른 소진 위험 임계값 (기본: 14.4x)",
    )
    burn_rate_fast_warning = serializers.FloatField(
        required=False,
        min_value=3.0,
        max_value=15.0,
        help_text="빠른 소진 경고 임계값 (기본: 6.0x)",
    )
    burn_rate_slow_warning = serializers.FloatField(
        required=False,
        min_value=1.0,
        max_value=10.0,
        help_text="느린 소진 경고 임계값 (기본: 3.0x)",
    )
    burn_rate_slow_info = serializers.FloatField(
        required=False,
        min_value=0.5,
        max_value=3.0,
        help_text="정상 소진율 임계값 (기본: 1.0x)",
    )

    # Fail-Safe 설정
    failsafe_alert_enabled = serializers.BooleanField(
        required=False, help_text="Fail-Safe 발동 시 알림 발송 여부"
    )
    failsafe_cooldown_seconds = serializers.IntegerField(
        required=False,
        min_value=60,
        max_value=3600,
        help_text="연속 알림 방지 쿨다운 (초)",
    )

    # Heartbeat (Dead Man's Snitch) 설정
    heartbeat_enabled = serializers.BooleanField(
        required=False,
        help_text="Heartbeat (Dead Man's Snitch) 활성화 여부 (기본: True)",
    )
    heartbeat_interval_seconds = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=300,
        help_text="Heartbeat 발송 주기 (초, 기본: 60초)",
    )
    heartbeat_timeout_seconds = serializers.IntegerField(
        required=False,
        min_value=30,
        max_value=600,
        help_text="Heartbeat 타임아웃 (초, 기본: 120초, 이 시간 내 미응답시 Dead)",
    )

    # 복구 알림 (Recovery Notification) 설정
    recovery_alert_enabled = serializers.BooleanField(
        required=False, help_text="복구 완료 알림 발송 여부 (기본: True)"
    )
    recovery_alert_include_downtime = serializers.BooleanField(
        required=False, help_text="복구 알림에 장애 시간 포함 여부 (기본: True)"
    )

    # Override 에스컬레이션 설정
    escalation_enabled = serializers.BooleanField(
        required=False, help_text="Override 에스컬레이션 활성화 여부 (기본: True)"
    )
    escalation_channel = serializers.CharField(
        required=False,
        max_length=100,
        help_text="에스컬레이션 알림 채널 (기본: #governance)",
    )
    escalation_mention = serializers.CharField(
        required=False,
        max_length=200,
        help_text="에스컬레이션 멘션 대상 (기본: @cto @security)",
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
                raise serializers.ValidationError(
                    f"{thresholds[i][0]}은 {thresholds[i + 1][0]}보다 커야 합니다."
                )

        # Heartbeat 타임아웃은 interval보다 커야 함
        interval = data.get("heartbeat_interval_seconds", 60)
        timeout = data.get("heartbeat_timeout_seconds", 120)
        if timeout <= interval:
            raise serializers.ValidationError(
                "heartbeat_timeout_seconds는 heartbeat_interval_seconds보다 커야 합니다."
            )

        return self.validate_with_safe_fallback(data)
