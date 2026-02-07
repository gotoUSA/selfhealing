# 202. Config 클래스 패러다임 통일 계획

> **상태**: 📋 계획
> **목적**: 설정 클래스 정의 방식을 역할별로 명확히 구분하고 일관된 규칙을 수립한다.

---

## 1. 현황 — 4가지 패러다임 공존

### 1-1. `BaseSettings` (pydantic v2) — `settings/` 디렉토리

```python
# settings/circuit_breaker.py L37-43
class CircuitBreakerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
    failure_threshold: int = Field(default=5, ge=1, le=100)
```

**적용 범위**: `settings/` 내 90+ 파일 전체.

### 1-2. `@dataclass(frozen=True)` — `config.py`

```python
# config.py L47-48
@dataclass(frozen=True)
class NotificationLimits:
    slack_block_text_limit: int = 3000
    description_max_length: int = 500
    ...

# config.py L65-66
@dataclass(frozen=True)
class ForensicSettings:
    max_stack_frames: int = 50
    ...

# config.py L404-405
@dataclass(frozen=True)
class MetricCollectionSettings:
    ...

# config.py L489-490
@dataclass(frozen=True)
class L2StorageConfig:
    ...
```

`get_notification_limits()` 함수(config.py L160)에서 `os.environ.get()`으로 직접 환경변수를 읽어 인스턴스 생성:

```python
# config.py L160-175
@lru_cache(maxsize=1)
def get_notification_limits() -> NotificationLimits:
    return NotificationLimits(
        slack_block_text_limit=int(
            os.environ.get("SELFHEALING_SLACK_BLOCK_TEXT_LIMIT", 3000)
        ),
        ...
    )
```

### 1-3. `@dataclass` — `services/**/*.py` (30+ 파일)

```python
# services/circuit_breaker/config.py L29
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    window_seconds: int = 60
    ...

# services/retry_handler.py L74
@dataclass
class RetryConfig:
    max_retries: int = 3
    ...

# services/security_notification/models.py L89
@dataclass
class NotificationConfig:
    ...
```

**대표 파일 목록** (services/ 내 `@dataclass class *Config`):
- `services/circuit_breaker/config.py` — `CircuitBreakerConfig`
- `services/retry_handler.py` — `RetryConfig`
- `services/throttle/config.py` — `ThrottleConfig`
- `services/error_budget_gate/config.py` — `ErrorBudgetGateConfig`
- `services/corruption_shield/config.py` — `CorruptionShieldConfig`
- `services/emergency_mode/models.py` — `RecoveryGateConfig`
- `services/coordination/recovery_circuit_breaker.py` — `RecoveryCircuitBreakerConfig`
- `services/coordination/recovery_shutdown.py` — `RecoveryAwareShutdownConfig`
- `services/chaos/stop_conditions.py` — `StopConditionsConfig`
- `services/canary/feature_flag.py` — `CanaryFlagConfig`
- `services/postmortem/notifier.py` — `PostmortemNotificationConfig`
- 외 20+ 파일

### 1-4. `BaseModel` (pydantic) — `settings/safety_bounds.py`

```python
# settings/safety_bounds.py L22-31
class ParameterBoundConfig(BaseModel):
    """개별 파라미터 한계 설정."""
    min_value: float = Field(description="최소 허용 값")
    max_value: float = Field(description="최대 허용 값")
    max_change_per_cycle: float = Field(ge=0.01, le=1.0)
```

`settings/` 디렉토리에서 유일하게 `BaseModel`을 사용. `SafetyBoundsSettings(BaseSettings)` 내부의 중첩 모델로 사용됨.

### 1-5. `TypedDict` — `core/types.py`

```python
# core/types.py L40-47
class RetryContext(TypedDict, total=False):
    """Context information for retry operations."""
    attempt: int
    max_attempts: int
    delay: float
    last_error: str
    operation_id: str
    domain: str
```

---

## 2. 문제점

| 문제 | 설명 |
|------|------|
| **환경변수 읽기 이중 구현** | `config.py`의 `@dataclass` + `os.environ.get()` 수동 파싱은 `BaseSettings`가 자동으로 해주는 역할을 중복 구현 |
| **검증 누락** | `@dataclass`는 `Field(ge=, le=)` 같은 검증이 없음. `settings/`의 `BaseSettings`만 pydantic 검증 보유 |
| **패턴 선택 기준 부재** | 새 Config 클래스 작성 시 `@dataclass`와 `BaseSettings` 중 어느 것을 써야 하는지 가이드 없음 |
| **BaseModel in settings/** | `ParameterBoundConfig(BaseModel)`은 중첩 모델이므로 정당하나, 규칙이 명시되지 않아 혼동 가능 |

---

## 3. 수정 계획

### 3-1. 역할별 패러다임 규칙

| 역할 | 패러다임 | 사용처 | 근거 |
|------|---------|--------|------|
| **환경변수 기반 설정** | `BaseSettings` | `settings/*.py` | pydantic v2의 자동 env 파싱 + 검증 |
| **중첩 모델** (Settings 내부) | `BaseModel` | `settings/*.py` 내부 보조 모델 | pydantic 검증 계승 |
| **런타임 데이터 객체** | `@dataclass` | `services/*`, `core/*` | 환경변수 불필요, 순수 데이터 전달 |
| **딕셔너리 형태 타입 힌트** | `TypedDict` | `core/types.py` | 기존 dict 호환 필요 시 |

### 3-2. config.py 마이그레이션

`config.py`의 `@dataclass(frozen=True)` 클래스들은 환경변수를 `os.environ.get()`으로 수동 파싱하므로, `BaseSettings`로 전환하는 것이 **일관성 + 기능** 모두에서 이점:

| 현재 (config.py) | 마이그레이션 대상 | 비고 |
|------------------|------------------|------|
| `NotificationLimits` L47 | `settings/notification.py` 통합 검토 | `get_notification_limits()`의 수동 env 파싱 제거 |
| `ForensicSettings` L66 | 199번 문서에서 이름 변경 후 판단 | `settings/forensic.py`와 역할 분리 필요 |
| `MetricCollectionSettings` L405 | `settings/metrics.py` 통합 검토 | |
| `L2StorageConfig` L490 | `settings/l2_storage.py` 통합 검토 | |

### 3-3. services/ 내 `@dataclass` Config — 유지

`services/` 내 `@dataclass`로 정의된 Config 클래스들은 환경변수를 직접 읽지 않고, **팩토리/생성 시점에 주입받는 런타임 데이터 객체**이므로 `@dataclass` 유지가 적절:

```python
# 예: services/circuit_breaker/config.py
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5  # ← Settings에서 주입됨
    window_seconds: int = 60
```

단, 향후 `Field(ge=, le=)` 검증이 필요하면 `BaseModel`로 전환 검토.

### 3-4. 검증 항목

- [ ] `config.py`의 `get_*()` 팩토리 함수들이 `BaseSettings` 전환 후 동일하게 동작하는지 확인
- [ ] `@lru_cache` 캐싱 로직이 `BaseSettings` 인스턴스와 호환되는지 확인
- [ ] `frozen=True` 불변성이 `BaseSettings`에서도 보장되는지 확인 (`.model_config`의 `frozen=True`)
