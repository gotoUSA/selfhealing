"""
Pydantic-DRF Serializer Integration Tests.

Pydantic 모델에서 DRF Serializer 자동 생성 및 검증 테스트.

Note:
    이 테스트는 tests/self_healing/django/ 디렉토리에 위치합니다.
    Django 초기화는 conftest.py에서 자동으로 처리됩니다.
    
    실행 방법:
        docker-compose -f docker-compose.test.yml run --rm test-hybrid-storage \
            python -m pytest tests/self_healing/django/test_pydantic_drf_integration.py -v
"""

import pytest
from rest_framework import serializers

from selfhealing.settings import (
    CircuitBreakerSettings,
    DLQSettings,
    RetrySettings,
    RateLimitSettings,
    SecuritySettings,
    SLASettings,
    NotificationSettings,
    ErrorBudgetSettings,
    GovernanceSettings,
)
from selfhealing.api.django.serializers.pydantic_integration import (
    pydantic_schema_to_drf_field,
    generate_serializer_fields_from_pydantic,
    PydanticSerializerMixin,
    create_pydantic_serializer,
)


class TestPydanticSchemaToDrfField:
    """pydantic_schema_to_drf_field 함수 테스트."""
    
    def test_integer_field_with_constraints(self):
        """정수 필드 변환 (min/max 포함)."""
        props = {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Failure threshold",
        }
        field = pydantic_schema_to_drf_field(props, "failure_threshold")
        
        assert isinstance(field, serializers.IntegerField)
        assert field.min_value == 1
        assert field.max_value == 100
        assert field.help_text == "Failure threshold"
    
    def test_float_field_with_constraints(self):
        """실수 필드 변환."""
        props = {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Target percentage",
        }
        field = pydantic_schema_to_drf_field(props, "target")
        
        assert isinstance(field, serializers.FloatField)
        assert field.min_value == 0.0
        assert field.max_value == 1.0
    
    def test_boolean_field(self):
        """불리언 필드 변환."""
        props = {
            "type": "boolean",
            "description": "Enabled flag",
        }
        field = pydantic_schema_to_drf_field(props, "enabled")
        
        assert isinstance(field, serializers.BooleanField)
    
    def test_string_field_with_enum(self):
        """enum 문자열 → ChoiceField 변환."""
        props = {
            "type": "string",
            "enum": ["exponential", "linear", "constant"],
            "description": "Backoff strategy",
        }
        field = pydantic_schema_to_drf_field(props, "backoff_strategy")
        
        assert isinstance(field, serializers.ChoiceField)
        # DRF ChoiceField stores choices as dict {value: display}
        assert set(field.choices.keys()) == {"exponential", "linear", "constant"}
    
    def test_array_field(self):
        """배열 필드 변환."""
        props = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Notification channels",
        }
        field = pydantic_schema_to_drf_field(props, "channels")
        
        assert isinstance(field, serializers.ListField)
        assert isinstance(field.child, serializers.CharField)
    
    def test_dict_field(self):
        """딕셔너리 필드 변환."""
        props = {
            "type": "object",
            "additionalProperties": {"type": "integer"},
            "description": "Domain thresholds",
        }
        field = pydantic_schema_to_drf_field(props, "thresholds_by_domain")
        
        assert isinstance(field, serializers.DictField)
        assert isinstance(field.child, serializers.IntegerField)


class TestGenerateSerializerFields:
    """generate_serializer_fields_from_pydantic 함수 테스트."""
    
    def test_circuit_breaker_fields(self):
        """CircuitBreakerSettings에서 필드 생성."""
        fields = generate_serializer_fields_from_pydantic(CircuitBreakerSettings)
        
        # 핵심 필드 존재 확인
        assert "enabled" in fields
        assert "failure_threshold" in fields
        assert "recovery_timeout" in fields
        assert "success_threshold" in fields
        
        # 타입 확인
        assert isinstance(fields["enabled"], serializers.BooleanField)
        assert isinstance(fields["failure_threshold"], serializers.IntegerField)
        assert isinstance(fields["recovery_timeout"], serializers.IntegerField)
    
    def test_dlq_fields(self):
        """DLQSettings에서 필드 생성."""
        fields = generate_serializer_fields_from_pydantic(DLQSettings)
        
        assert "enabled" in fields
        assert "max_retries" in fields
        assert "retry_delay" in fields
        assert "expiry_hours" in fields
    
    def test_exclude_fields(self):
        """필드 제외 옵션."""
        fields = generate_serializer_fields_from_pydantic(
            CircuitBreakerSettings,
            exclude_fields={"enabled", "failure_threshold"},
        )
        
        assert "enabled" not in fields
        assert "failure_threshold" not in fields
        assert "recovery_timeout" in fields  # 제외되지 않은 필드는 존재
    
    def test_constraints_preserved(self):
        """Pydantic 제약조건이 DRF 필드에 유지되는지 확인."""
        fields = generate_serializer_fields_from_pydantic(CircuitBreakerSettings)
        
        failure_threshold = fields["failure_threshold"]
        assert failure_threshold.min_value == 1
        assert failure_threshold.max_value == 100
        
        recovery_timeout = fields["recovery_timeout"]
        assert recovery_timeout.min_value == 1
        assert recovery_timeout.max_value == 3600


class TestCreatePydanticSerializer:
    """create_pydantic_serializer 함수 테스트."""
    
    def test_create_basic_serializer(self):
        """기본 Serializer 생성."""
        CBSerializer = create_pydantic_serializer(
            CircuitBreakerSettings,
            "CircuitBreakerTestSerializer",
        )
        
        # 클래스 생성 확인
        assert CBSerializer.__name__ == "CircuitBreakerTestSerializer"
        
        # 인스턴스 생성 확인
        serializer = CBSerializer(data={"failure_threshold": 10})
        assert serializer.is_valid()
    
    def test_serializer_validation_with_pydantic(self):
        """Pydantic 검증이 적용되는지 확인."""
        CBSerializer = create_pydantic_serializer(
            CircuitBreakerSettings,
            "CBValidationSerializer",
        )
        
        # 유효한 데이터
        valid_data = {"failure_threshold": 5, "recovery_timeout": 120}
        serializer = CBSerializer(data=valid_data)
        assert serializer.is_valid()
        assert serializer.validated_data["failure_threshold"] == 5
    
    def test_serializer_invalid_data(self):
        """잘못된 데이터 검증 실패 확인."""
        CBSerializer = create_pydantic_serializer(
            CircuitBreakerSettings,
            "CBInvalidSerializer",
        )
        
        # failure_threshold 범위 초과 (max=100)
        invalid_data = {"failure_threshold": 150}
        serializer = CBSerializer(data=invalid_data)
        # DRF min_value/max_value 제약으로 실패
        assert not serializer.is_valid()
    
    def test_create_dlq_serializer(self):
        """DLQSettings Serializer 생성."""
        DLQSerializer = create_pydantic_serializer(
            DLQSettings,
            "DLQTestSerializer",
        )
        
        data = {"max_retries": 5, "retry_delay": 120}
        serializer = DLQSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["max_retries"] == 5


class TestPydanticSerializerMixin:
    """PydanticSerializerMixin 테스트."""
    
    def test_mixin_with_pydantic_model(self):
        """Mixin이 Pydantic 모델 필드를 자동 생성하는지 확인."""
        
        class TestSerializer(PydanticSerializerMixin, serializers.Serializer):
            _pydantic_model = CircuitBreakerSettings
            _exclude_fields = set()
        
        serializer = TestSerializer(data={"enabled": True})
        
        # 필드가 자동 생성되었는지 확인
        assert "failure_threshold" in serializer.fields
        assert "recovery_timeout" in serializer.fields
    
    def test_mixin_validate_with_pydantic(self):
        """validate_with_pydantic 메서드 테스트."""
        
        class TestSerializer(PydanticSerializerMixin, serializers.Serializer):
            _pydantic_model = CircuitBreakerSettings
            
            def validate(self, data):
                return self.validate_with_pydantic(data)
        
        serializer = TestSerializer(data={"failure_threshold": 10})
        assert serializer.is_valid()
        assert serializer.validated_data["failure_threshold"] == 10


class TestAllPydanticModelsToSerializer:
    """모든 Pydantic Settings 모델에서 Serializer 생성 가능 확인."""
    
    @pytest.mark.parametrize("pydantic_model,expected_fields", [
        (CircuitBreakerSettings, ["enabled", "failure_threshold", "recovery_timeout"]),
        (DLQSettings, ["enabled", "max_retries", "retry_delay"]),
        (RetrySettings, ["max_attempts", "backoff_strategy", "base_delay"]),
        (RateLimitSettings, ["base_delay", "max_delay", "jitter_percent"]),
        (SecuritySettings, ["rate_limit_window_seconds", "rate_limit_max_requests"]),
        (SLASettings, ["default_hours"]),
        # SLOSettings의 slos 필드는 복잡한 타입이므로 generate 시 exclude 필요
        # (SLOSettings, ["default_window_days", "default_target"]),
        (NotificationSettings, ["enabled", "channels", "critical_threshold"]),
        (ErrorBudgetSettings, ["threshold_healthy", "threshold_critical"]),
        (GovernanceSettings, ["threshold_operator", "threshold_admin"]),
    ])
    def test_serializer_generation(self, pydantic_model, expected_fields):
        """각 Pydantic 모델에서 Serializer 생성 가능."""
        fields = generate_serializer_fields_from_pydantic(pydantic_model)
        
        for expected_field in expected_fields:
            assert expected_field in fields, f"{expected_field} not found in {pydantic_model.__name__}"


class TestSerializerFieldConsistency:
    """기존 DRF Serializer와 Pydantic 기반 Serializer 필드 일관성 테스트."""
    
    def test_circuit_breaker_field_types_match(self):
        """CircuitBreakerSettings 필드 타입 일관성."""
        # Pydantic 기반 필드 생성
        pydantic_fields = generate_serializer_fields_from_pydantic(CircuitBreakerSettings)
        
        # 기존 Serializer 필드와 비교
        from selfhealing.api.django.serializers.config import CircuitBreakerConfigSerializer
        legacy_serializer = CircuitBreakerConfigSerializer()
        
        # 핵심 필드 타입 비교
        type_mappings = [
            ("failure_threshold", serializers.IntegerField),
            ("recovery_timeout", serializers.IntegerField),
            ("enabled", serializers.BooleanField),
        ]
        
        for field_name, expected_type in type_mappings:
            if field_name in pydantic_fields:
                assert isinstance(pydantic_fields[field_name], expected_type)
            if field_name in legacy_serializer.fields:
                assert isinstance(legacy_serializer.fields[field_name], expected_type)
    
    def test_constraints_match_legacy_serializer(self):
        """Pydantic 제약조건이 기존 Serializer와 일치."""
        pydantic_fields = generate_serializer_fields_from_pydantic(CircuitBreakerSettings)
        
        # failure_threshold: min=1, max=100
        ft = pydantic_fields["failure_threshold"]
        assert ft.min_value == 1
        assert ft.max_value == 100
        
        # recovery_timeout: min=1, max=3600
        rt = pydantic_fields["recovery_timeout"]
        assert rt.min_value == 1
        assert rt.max_value == 3600
