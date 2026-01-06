"""
Advanced Configuration Serializers.

Forensic, Metrics, Logging config serializers.

Phase 6: Fail-Safe Default 강화 추가.
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

from rest_framework import serializers
from .base import ApplyStrategyMixin


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
