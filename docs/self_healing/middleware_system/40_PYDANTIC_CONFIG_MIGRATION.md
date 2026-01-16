# Pydantic Configuration Migration Plan

> 설정 시스템을 Pydantic으로 전환하여 Single Source of Truth 달성

---

## 1. 현재 상태 분석 (코드 기반)

### 1.1 현재 아키텍처의 문제점

현재 설정 시스템은 **3개 레이어에 중복 정의**되어 있습니다:

| 레이어 | 파일 | 역할 | 문제점 |
|--------|------|------|--------|
| **Layer 1** | `core/config.py` | @dataclass 기반 설정 클래스 | 기본값 정의 |
| **Layer 2** | `core/safe_defaults.py` | SAFE_DEFAULTS + VALIDATION_RULES | 기본값 + 범위 **중복** |
| **Layer 3** | `api/django/serializers/config.py` | DRF Serializer | min_value/max_value **중복** |
| **Layer 4** | `config.py` | frozen dataclass + lru_cache | 환경변수 수동 파싱 |

### 1.2 구체적 중복 예시 (코드 증거)

#### 예시 1: `failure_threshold` 기본값

```python
# core/config.py:17
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5  # 기본값 정의 (1)

# core/safe_defaults.py:24
SAFE_DEFAULTS = {
    "circuit_breaker": {
        "failure_threshold": 5,  # 기본값 정의 (2) - 중복!
    }
}

# core/safe_defaults.py:224
VALIDATION_RULES = {
    "circuit_breaker": {
        "failure_threshold": (1, 100),  # 범위 정의
    }
}

# api/django/serializers/config.py:103
class CircuitBreakerConfigSerializer(ApplyStrategyMixin):
    failure_threshold = serializers.IntegerField(
        required=False, 
        min_value=1, 
        max_value=100  # 범위 정의 - 중복!
    )
```

#### 예시 2: 환경변수 수동 파싱

```python
# config.py:160-165
return NotificationLimits(
    slack_block_text_limit=int(os.environ.get("SELFHEALING_SLACK_BLOCK_TEXT_LIMIT", 3000)),
    description_max_length=int(os.environ.get("SELFHEALING_DESCRIPTION_MAX_LENGTH", 500)),
    # ... 모든 필드에 수동 파싱
)
```

### 1.3 현재 설정 클래스 전체 목록 (코드 기반)

`packages/selfhealing-python/src/selfhealing/core/config.py`:

| 클래스 | 라인 | 필드 수 | 검증 규칙 |
|--------|------|---------|-----------|
| `CircuitBreakerConfig` | 13-33 | 13 | safe_defaults.py에 중복 |
| `DLQConfig` | 36-45 | 7 | safe_defaults.py에 중복 |
| `RetryConfig` | 48-56 | 8 | safe_defaults.py에 중복 |
| `SLAConfig` | 59-86 | 2 + dict | safe_defaults.py에 중복 |
| `SLODefinition` | 89-109 | 11 | serializers에만 존재 |
| `SLOConfigRuntime` | 112-134 | 5 | safe_defaults.py에 중복 |
| `RateLimitConfig` | 137-163 | 8 | safe_defaults.py에 중복 |
| `IdempotencyConfig` | 166-171 | 4 | safe_defaults.py에 중복 |
| `SecurityConfig` | 174-185 | 9 | safe_defaults.py에 중복 |
| `ForensicConfig` | 188-211 | 9 | safe_defaults.py에 중복 |
| `LoggingConfig` | 214-238 | 14 | safe_defaults.py에 중복 |
| `MetricsConfig` | 241-250 | 6 | safe_defaults.py에 중복 |
| `NotificationConfig` | 253-275 | 12 | safe_defaults.py에 중복 |
| `ErrorBudgetConfig` | 278-319 | 15 | safe_defaults.py에 중복 |
| `GovernanceConfig` | 322-361 | 13 | safe_defaults.py에 중복 |
| `DriftThresholdConfig` | 364-390 | 5+ | safe_defaults.py에 중복 |

**총 17개 dataclass, ~140개 필드가 중복 정의됨**

---

## 2. Pydantic 방식의 장단점

### 2.1 장점

| 장점 | 설명 | 코드 예시 |
|------|------|-----------|
| **Single Source of Truth** | 기본값, 타입, 검증 규칙이 한 곳에 | `Field(default=5, ge=1, le=100)` |
| **환경변수 자동 로드** | `BaseSettings`가 자동 파싱 | `SELFHEALING_FAILURE_THRESHOLD` 자동 인식 |
| **타입 강제** | 런타임 타입 체크 | `int` 필드에 `"5"` 입력 시 자동 변환 |
| **IDE 지원** | 자동완성, 타입 힌트 | PyCharm, VS Code 완벽 지원 |
| **중첩 모델** | 복잡한 구조 지원 | `SLOConfig`에 `List[SLODefinition]` |
| **JSON Schema 자동 생성** | OpenAPI/Swagger 통합 | `model_json_schema()` |
| **Immutable 옵션** | frozen=True 대체 | `model_config = ConfigDict(frozen=True)` |
| **커스텀 Validator** | 복잡한 비즈니스 로직 | `@field_validator`, `@model_validator` |
| **DRF Serializer 대체** | 코드량 50% 이상 감소 | DRF + pydantic 통합 가능 |

### 2.2 단점

| 단점 | 설명 | 완화 방법 |
|------|------|-----------|
| **의존성 추가** | `pydantic>=2.0`, `pydantic-settings` | 이미 많은 프로젝트에서 사용 |
| **마이그레이션 비용** | 17개 클래스, 140개 필드 변환 | 단계별 마이그레이션 |
| **학습 곡선** | v1→v2 API 변경 | 문서화된 마이그레이션 가이드 존재 |
| **성능 오버헤드** | dataclass 대비 약간 느림 | 설정은 시작 시 1회만 로드 (무시 가능) |
| **DRF 통합** | 추가 라이브러리 필요 | `drf-pydantic` 또는 수동 통합 |

### 2.3 왜 Pydantic인가?

현재 프로젝트의 의존성 (`requirements.txt`):
- Django 5.2.9 ✅
- DRF 3.16.0 ✅
- drf-spectacular 0.29.0 ✅ (OpenAPI 생성)

**Pydantic은 drf-spectacular와 완벽 호환**되며, JSON Schema 자동 생성을 지원합니다.

---

## 3. 목표 아키텍처

### 3.1 After: Pydantic Single Source of Truth

```
┌─────────────────────────────────────────────────────────────────┐
│                    pydantic/settings.py                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ class CircuitBreakerSettings(BaseSettings):               │  │
│  │     failure_threshold: int = Field(                       │  │
│  │         default=5,                                        │  │
│  │         ge=1, le=100,                                     │  │
│  │         description="Circuit breaker failure threshold"   │  │
│  │     )                                                     │  │
│  │     # ... 모든 정의가 여기 한 곳에!                         │  │
│  │                                                           │  │
│  │     model_config = SettingsConfigDict(                    │  │
│  │         env_prefix="SELFHEALING_CB_",                     │  │
│  │         env_file=".env",                                  │  │
│  │     )                                                     │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
         ┌────────────────────┴────────────────────┐
         │                                         │
         ▼                                         ▼
┌─────────────────┐                     ┌─────────────────────┐
│   API Views     │                     │   Core Services     │
│   (DRF 통합)     │                     │   (직접 사용)        │
│                 │                     │                     │
│ settings.       │                     │ settings.           │
│   failure_      │                     │   failure_          │
│   threshold     │                     │   threshold         │
└─────────────────┘                     └─────────────────────┘
```

### 3.2 디렉토리 구조

```
packages/selfhealing-python/src/selfhealing/
├── settings/                          # NEW: Pydantic 설정 모듈
│   ├── __init__.py
│   ├── base.py                        # BaseSettings 공통 설정
│   ├── circuit_breaker.py             # CircuitBreakerSettings
│   ├── dlq.py                         # DLQSettings
│   ├── retry.py                       # RetrySettings
│   ├── sla.py                         # SLASettings
│   ├── slo.py                         # SLOSettings
│   ├── rate_limit.py                  # RateLimitSettings
│   ├── security.py                    # SecuritySettings
│   ├── forensic.py                    # ForensicSettings
│   ├── logging.py                     # LoggingSettings
│   ├── metrics.py                     # MetricsSettings
│   ├── notification.py                # NotificationSettings
│   ├── error_budget.py                # ErrorBudgetSettings
│   ├── governance.py                  # GovernanceSettings
│   ├── chaos.py                       # ChaosSettings
│   └── root.py                        # SelfHealingSettings (루트)
│
├── core/
│   ├── config.py                      # DEPRECATED → settings/ 로 이동
│   └── safe_defaults.py               # DEPRECATED → settings/ 로 통합
│
└── api/django/serializers/
    └── config.py                      # SIMPLIFIED → Pydantic 모델 재사용
```

---

## 4. 마이그레이션 계획

### 4.1 Phase 0: 준비 (0.5일)

#### 4.1.1 의존성 추가

```bash
# requirements.txt에 추가
pydantic>=2.6.0
pydantic-settings>=2.2.0
```

#### 4.1.2 호환성 테스트

```bash
# 기존 테스트가 통과하는지 확인
pytest tests/self_healing/ -v
```

### 4.2 Phase 1: 핵심 설정 마이그레이션 (2일)

**우선순위가 높은 5개 설정 먼저 변환:**

| 순서 | 설정 | 이유 |
|------|------|------|
| 1 | `CircuitBreakerSettings` | 가장 많이 사용, 참조 구현 |
| 2 | `DLQSettings` | 핵심 기능 |
| 3 | `RetrySettings` | 핵심 기능 |
| 4 | `RateLimitSettings` | API 보호 |
| 5 | `SecuritySettings` | 보안 필수 |

#### 4.2.1 예시: CircuitBreakerSettings 구현

```python
# packages/selfhealing-python/src/selfhealing/settings/circuit_breaker.py
"""
Circuit Breaker Settings - Pydantic v2.

Single Source of Truth for circuit breaker configuration.
Replaces:
- core/config.py:CircuitBreakerConfig
- core/safe_defaults.py:SAFE_DEFAULTS["circuit_breaker"]
- core/safe_defaults.py:VALIDATION_RULES["circuit_breaker"]
- api/django/serializers/config.py:CircuitBreakerConfigSerializer
"""
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CircuitBreakerSettings(BaseSettings):
    """
    Circuit Breaker configuration with validation.
    
    Environment variables:
        SELFHEALING_CB_ENABLED=true
        SELFHEALING_CB_FAILURE_THRESHOLD=5
        ...
    """
    
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CB_",
        env_file=".env",
        extra="ignore",
    )
    
    # Core settings
    enabled: bool = Field(
        default=True,
        description="Enable circuit breaker protection",
    )
    failure_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of failures before opening circuit",
    )
    recovery_timeout: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Seconds to wait before attempting recovery",
    )
    success_threshold: int = Field(
        default=2,
        ge=1,
        le=100,
        description="Successes required to close circuit",
    )
    half_open_max_calls: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Max calls in half-open state",
    )
    half_open_request_limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Request limit in half-open state",
    )
    excluded_exceptions: List[str] = Field(
        default_factory=list,
        description="Exception types to exclude from failure count",
    )
    
    # Rate limit cascade detection
    rate_limit_cascade_threshold: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="429 errors before cascade detection",
    )
    rate_limit_cascade_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Window for cascade detection",
    )
    
    # Self-DDoS protection
    self_ddos_protection_enabled: bool = Field(
        default=True,
        description="Enable self-DDoS protection",
    )
    self_ddos_request_threshold: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Request threshold for self-DDoS detection",
    )
    self_ddos_window_seconds: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Window for self-DDoS detection",
    )
    self_ddos_backoff_multiplier: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Backoff multiplier for self-DDoS",
    )
    
    @field_validator("failure_threshold")
    @classmethod
    def validate_failure_threshold(cls, v: int) -> int:
        """Safe default fallback for extreme values."""
        if v > 50:
            import logging
            logging.warning(
                f"[SafeDefault] High failure_threshold={v}, "
                "consider using <= 50 for safety"
            )
        return v


# Singleton instance (cached)
_settings: CircuitBreakerSettings | None = None


def get_circuit_breaker_settings() -> CircuitBreakerSettings:
    """Get cached CircuitBreakerSettings instance."""
    global _settings
    if _settings is None:
        _settings = CircuitBreakerSettings()
    return _settings


def reset_settings() -> None:
    """Reset cached settings (for testing)."""
    global _settings
    _settings = None
```

### 4.3 Phase 2: 나머지 설정 마이그레이션 (3일)

| 설정 | 필드 수 | 예상 시간 |
|------|---------|----------|
| `SLASettings` | 2 + dict | 0.5시간 |
| `SLOSettings` | 16 | 1시간 |
| `IdempotencySettings` | 4 | 0.5시간 |
| `ForensicSettings` | 9 | 0.5시간 |
| `LoggingSettings` | 14 | 1시간 |
| `MetricsSettings` | 6 | 0.5시간 |
| `NotificationSettings` | 12 | 1시간 |
| `ErrorBudgetSettings` | 15 | 1시간 |
| `GovernanceSettings` | 13 | 1시간 |
| `ChaosSettings` | 5 | 0.5시간 |
| `DriftThresholdSettings` | 5 | 0.5시간 |
| `L2StorageSettings` | 10 | 0.5시간 |

### 4.4 Phase 3: DRF Serializer 통합 (2일)

#### 4.4.1 옵션 A: drf-pydantic 사용

```python
# 설치
pip install drf-pydantic

# 사용
from drf_pydantic import BaseModel as DRFBaseModel

class CircuitBreakerSettings(DRFBaseModel):
    # Pydantic 모델이 DRF Serializer로도 동작
    ...
```

#### 4.4.2 옵션 B: 수동 통합 (권장)

```python
# api/django/serializers/config.py (간소화)
from rest_framework import serializers
from selfhealing.settings.circuit_breaker import CircuitBreakerSettings


class CircuitBreakerConfigSerializer(serializers.Serializer):
    """DRF Serializer - Pydantic 모델 재사용."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pydantic 스키마에서 필드 자동 생성
        schema = CircuitBreakerSettings.model_json_schema()
        for name, props in schema.get("properties", {}).items():
            self.fields[name] = self._create_field(name, props)
    
    def _create_field(self, name: str, props: dict):
        """Pydantic 스키마 → DRF 필드 변환."""
        field_type = props.get("type")
        kwargs = {
            "required": False,
            "help_text": props.get("description", ""),
        }
        
        if field_type == "integer":
            if "minimum" in props:
                kwargs["min_value"] = props["minimum"]
            if "maximum" in props:
                kwargs["max_value"] = props["maximum"]
            return serializers.IntegerField(**kwargs)
        elif field_type == "number":
            return serializers.FloatField(**kwargs)
        elif field_type == "boolean":
            return serializers.BooleanField(**kwargs)
        elif field_type == "string":
            return serializers.CharField(**kwargs)
        elif field_type == "array":
            return serializers.ListField(**kwargs)
        
        return serializers.CharField(**kwargs)
    
    def validate(self, data):
        """Pydantic 모델로 검증 위임."""
        try:
            validated = CircuitBreakerSettings(**data)
            return validated.model_dump(exclude_unset=True)
        except Exception as e:
            raise serializers.ValidationError(str(e))
```

### 4.5 Phase 4: 레거시 코드 제거 (1일)

#### 4.5.1 제거 대상 파일

| 파일 | 상태 | 대체 |
|------|------|------|
| `core/config.py` | DEPRECATED | `settings/*.py` |
| `core/safe_defaults.py` | 부분 유지 | FATAL_CONFIGS만 유지 |
| `config.py` (root) | 리팩토링 | `settings/root.py` 통합 |

#### 4.5.2 하위 호환성 유지

```python
# core/config.py (deprecation wrapper)
"""
DEPRECATED: Use selfhealing.settings instead.

This module is kept for backward compatibility.
Will be removed in v2.0.
"""
import warnings
from selfhealing.settings.circuit_breaker import CircuitBreakerSettings

warnings.warn(
    "selfhealing.core.config is deprecated. "
    "Use selfhealing.settings instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Backward compatible alias
CircuitBreakerConfig = CircuitBreakerSettings
```

### 4.6 Phase 5: 테스트 및 문서화 (1일)

#### 4.6.1 테스트 업데이트

```python
# tests/self_healing/unit/settings/test_circuit_breaker_settings.py
import pytest
from pydantic import ValidationError
from selfhealing.settings.circuit_breaker import (
    CircuitBreakerSettings,
    get_circuit_breaker_settings,
    reset_settings,
)


class TestCircuitBreakerSettings:
    """Pydantic Settings 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset(self):
        reset_settings()
        yield
        reset_settings()
    
    def test_default_values(self):
        """기본값 테스트."""
        settings = CircuitBreakerSettings()
        assert settings.enabled is True
        assert settings.failure_threshold == 5
        assert settings.recovery_timeout == 60
    
    def test_env_override(self, monkeypatch):
        """환경변수 오버라이드 테스트."""
        monkeypatch.setenv("SELFHEALING_CB_FAILURE_THRESHOLD", "10")
        settings = CircuitBreakerSettings()
        assert settings.failure_threshold == 10
    
    def test_validation_error(self):
        """범위 검증 테스트."""
        with pytest.raises(ValidationError):
            CircuitBreakerSettings(failure_threshold=0)  # ge=1 위반
        
        with pytest.raises(ValidationError):
            CircuitBreakerSettings(failure_threshold=101)  # le=100 위반
    
    def test_type_coercion(self):
        """타입 자동 변환 테스트."""
        settings = CircuitBreakerSettings(failure_threshold="5")  # str → int
        assert settings.failure_threshold == 5
        assert isinstance(settings.failure_threshold, int)
    
    def test_json_schema_generation(self):
        """JSON Schema 생성 테스트."""
        schema = CircuitBreakerSettings.model_json_schema()
        assert "properties" in schema
        assert "failure_threshold" in schema["properties"]
        assert schema["properties"]["failure_threshold"]["minimum"] == 1
        assert schema["properties"]["failure_threshold"]["maximum"] == 100
```

---

## 5. 마이그레이션 체크리스트

### Phase 0: 준비 ✅ (2026-01-16 완료)
- [x] `pydantic>=2.6.0` 추가 - `pyproject.toml` dependencies에 추가
- [x] `pydantic-settings>=2.2.0` 추가 - `pyproject.toml` dependencies에 추가
- [x] 기존 테스트 통과 확인

### Phase 1: 핵심 설정 (5개) ✅ (2026-01-16 완료)
- [x] `CircuitBreakerSettings` 구현 - `settings/circuit_breaker.py`
- [x] `DLQSettings` 구현 - `settings/dlq.py`
- [x] `RetrySettings` 구현 - `settings/retry.py`
- [x] `RateLimitSettings` 구현 - `settings/rate_limit.py`
- [x] `SecuritySettings` 구현 - `settings/security.py`

**테스트 결과**: 40개 테스트 전체 통과
- 기본값 검증 (Legacy dataclass와 일치 확인)
- 환경변수 오버라이드 검증
- 범위 검증 (VALIDATION_RULES와 일치)
- 싱글톤 패턴 검증
- JSON Schema 생성 검증

### Phase 2: 나머지 설정 (12개) ✅ (2026-01-16 완료)
- [x] `SLASettings` 구현 - `settings/sla.py`
- [x] `SLOSettings` 구현 - `settings/slo.py`
- [x] `IdempotencySettings` 구현 - `settings/idempotency.py`
- [x] `ForensicSettings` 구현 - `settings/forensic.py`
- [x] `LoggingSettings` 구현 - `settings/logging_config.py`
- [x] `MetricsSettings` 구현 - `settings/metrics.py`
- [x] `NotificationSettings` 구현 - `settings/notification.py`
- [x] `ErrorBudgetSettings` 구현 - `settings/error_budget.py`
- [x] `GovernanceSettings` 구현 - `settings/governance.py`
- [x] `ChaosSettings` 구현 - `settings/chaos.py`
- [x] `DriftThresholdSettings` 구현 - `settings/drift_threshold.py`
- [x] `L2StorageSettings` 구현 - `settings/l2_storage.py`

**테스트 결과**: 100개 테스트 전체 통과 (Phase 1: 40개 + Phase 2: 60개)
- 기본값 검증 (Legacy dataclass와 일치 확인)
- 환경변수 오버라이드 검증
- 범위 검증 (VALIDATION_RULES와 일치)
- 싱글톤 패턴 검증
- Legacy dataclass와 Pydantic Settings 일관성 검증

### Phase 3: DRF 통합 ✅ (2026-01-16 완료)
- [x] Serializer 자동 생성 헬퍼 구현 - `api/django/serializers/pydantic_integration.py`
  - `pydantic_schema_to_drf_field()`: Pydantic 스키마 → DRF 필드 변환
  - `generate_serializer_fields_from_pydantic()`: Pydantic 모델에서 필드 딕셔너리 생성
  - `PydanticSerializerMixin`: Pydantic 모델 통합 Mixin
  - `create_pydantic_serializer()`: 동적 Serializer 클래스 생성
- [x] 기존 Serializer와 호환성 확인
- [x] OpenAPI 스키마 생성 (model_json_schema())

**테스트 결과**: 27개 테스트 전체 통과
- Pydantic 스키마 → DRF 필드 변환 검증
- 동적 Serializer 생성 검증
- 기존 Serializer와 필드 타입/제약조건 일치 확인

### Phase 4: 레거시 제거 ✅ (2026-01-16 완료)
- [x] `core/config.py` deprecation wrapper
  - 모듈 docstring에 마이그레이션 가이드 추가
  - import 시 DeprecationWarning 발생
  - `__pydantic_aliases__` 매핑 추가
- [x] `core/safe_defaults.py` 정리
  - PARTIAL DEPRECATION NOTICE 추가
  - FATAL_CONFIGS, SAFE_DEFAULTS 유지 (레거시 호환성)
  - VALIDATION_RULES → Pydantic Field 제약조건으로 대체됨
- [x] `settings/__init__.py`에 레거시 alias 추가
  - `CircuitBreakerConfig = CircuitBreakerSettings` 등 16개 alias
- [x] 하위 호환성 검증

**테스트 결과**: 
- DeprecationWarning 정상 발생 확인
- 레거시 alias로 기존 코드 호환 확인
- 전체 127개 테스트 통과

### Phase 5: 마무리 ✅ (2026-01-16 완료)
- [x] 전체 테스트 통과 (127개)
- [x] 문서 업데이트
- [x] CHANGELOG 작성

---

## 6. 예상 일정

| Phase | 작업 | 예상 시간 |
|-------|------|----------|
| Phase 0 | 준비 | 0.5일 |
| Phase 1 | 핵심 설정 5개 | 2일 |
| Phase 2 | 나머지 설정 12개 | 3일 |
| Phase 3 | DRF 통합 | 2일 |
| Phase 4 | 레거시 제거 | 1일 |
| Phase 5 | 테스트/문서화 | 1일 |
| **합계** | | **9.5일** |

---

## 7. 리스크 및 완화 방안

| 리스크 | 확률 | 영향 | 완화 방안 |
|--------|------|------|-----------|
| 기존 테스트 실패 | 중 | 높음 | 하위 호환성 wrapper 유지 |
| 환경변수 이름 변경 | 낮음 | 중간 | 기존 이름도 aliases로 지원 |
| 성능 저하 | 낮음 | 낮음 | 시작 시 1회만 로드 (무시 가능) |
| DRF 통합 이슈 | 중 | 중간 | 수동 통합 옵션 준비 |

---

## 8. 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](../16_GOVERNANCE_IMPLEMENTATION_PART2.md) - 현재 Safe Defaults 구현
- [Pydantic v2 Documentation](https://docs.pydantic.dev/latest/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## 9. 결론

### 현재 상태
- **17개 dataclass**, **~140개 필드**가 3곳에 중복 정의
- 유지보수 비용 높음, 일관성 보장 어려움

### 목표 상태
- **Pydantic BaseSettings**로 Single Source of Truth 달성
- 기본값 + 타입 + 검증 + 환경변수 = 한 곳에서 관리
- DRF Serializer 코드량 50% 이상 감소

### 핵심 이점
1. **중복 제거**: 3곳 → 1곳
2. **타입 안전**: 런타임 검증
3. **환경변수 자동 로드**: 수동 파싱 제거
4. **IDE 지원**: 자동완성 + 타입 힌트
5. **JSON Schema**: OpenAPI 자동 생성
