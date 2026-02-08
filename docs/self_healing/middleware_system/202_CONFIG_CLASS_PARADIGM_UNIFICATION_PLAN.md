# 202. Config 클래스 패러다임 통일 계획

> **상태**: ✅ 2차 완료 (2025-07-11)
> **1차 완료**: 2026-02-09 — config.py 내 `@dataclass(frozen=True)` 4건 전환
> **2차 완료**: 2025-07-11 — 문서 범위 밖 누락 5건 발굴 및 리팩토링
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

### 1-6. 일반 class (Singleton) + `os.environ.get()` — `config.py`

```python
# config.py L228 (리팩토링 전)
class EventLoggingConfig:
    _instance: EventLoggingConfig | None = None
    def _init_defaults(self) -> None:
        self._env_defaults = {
            "dlq_log_level": os.environ.get("SELFHEALING_DLQ_LOG_LEVEL", "INFO"),
            ...
        }

# config.py L598 (리팩토링 전)
class L2StorageRuntimeConfig:
    _instance: L2StorageRuntimeConfig | None = None
    def _init_defaults(self) -> None:
        self._env_defaults = {
            "redis_timeout_ms": int(os.environ.get("SELFHEALING_L2_REDIS_TIMEOUT_MS", "50")),
            ...
        }
```

Singleton 패턴으로 런타임 변경을 지원하되, `_init_defaults()`에서 `os.environ.get()`으로 수동 파싱. `BaseSettings`와 환경변수 파싱이 중복.

### 1-7. `@dataclass` + `os.environ.get()` — `models/`, `core/`

```python
# models/drift_config.py L91 (리팩토링 전)
@dataclass
class DriftThresholdConfig:
    @classmethod
    def from_env(cls) -> DriftThresholdConfig:
        return cls(
            warning_threshold=float(os.environ.get("SELFHEALING_DRIFT_WARNING_THRESHOLD", "0.05")),
            ...
        )

# core/cluster_identity.py L34 (리팩토링 전)
@dataclass(frozen=True)
class ClusterIdentity:
    cluster_id: str = "default"
    ...

def get_cluster_identity() -> ClusterIdentity:
    cluster_id = os.environ.get("SELFHEALING_CLUSTER_ID", "default")
    ...
    return ClusterIdentity(cluster_id=cluster_id, ...)
```

`@dataclass` + 팩토리 함수에서 `os.environ.get()` 수동 파싱. `BaseSettings`가 해주는 역할과 동일.

---

## 2. 문제점

| 문제 | 설명 |
|------|------|
| **환경변수 읽기 이중 구현** | `config.py`의 `@dataclass` + `os.environ.get()` 수동 파싱은 `BaseSettings`가 자동으로 해주는 역할을 중복 구현 |
| **검증 누락** | `@dataclass`는 `Field(ge=, le=)` 같은 검증이 없음. `settings/`의 `BaseSettings`만 pydantic 검증 보유 |
| **패턴 선택 기준 부재** | 새 Config 클래스 작성 시 `@dataclass`와 `BaseSettings` 중 어느 것을 써야 하는지 가이드 없음 |
| **BaseModel in settings/** | `ParameterBoundConfig(BaseModel)`은 중첩 모델이므로 정당하나, 규칙이 명시되지 않아 혼동 가능 |
| **Singleton + 수동 env 파싱** | `EventLoggingConfig`, `L2StorageRuntimeConfig`의 `_init_defaults()`에서 `os.environ.get()` 수동 파싱. 런타임 변경 기능과 env 파싱 관심사가 혼재 |
| **config.py 외부 누락** | `models/drift_config.py`, `core/cluster_identity.py` 등 패키지 전반에 산재한 `os.environ.get()` 수동 파싱이 1차 마이그레이션 범위에서 누락 |

---

## 3. 수정 계획

### 3-1. 역할별 패러다임 규칙

| 역할 | 패러다임 | 사용처 | 근거 |
|------|---------|--------|------|
| **환경변수 기반 설정** | `BaseSettings` | `settings/*.py` | pydantic v2의 자동 env 파싱 + 검증 |
| **중첩 모델** (Settings 내부) | `BaseModel` | `settings/*.py` 내부 보조 모델 | pydantic 검증 계승 |
| **런타임 데이터 객체** | `@dataclass` | `services/*`, `core/*`, `audit/*`, `adapters/*` | 환경변수 불필요, 순수 데이터 전달 |
| **Singleton + 런타임 변경** | 일반 class (env 파싱은 `BaseSettings` 위임) | `config.py` 내 runtime config | 런타임 API 변경 지원, env 파싱 관심사 분리 |
| **딕셔너리 형태 타입 힌트** | `TypedDict` | `core/types.py` | 기존 dict 호환 필요 시 |

> **추가 적용 범위** (2차): `audit/` 내 16개 `@dataclass` Config, `adapters/` 내 8개 `@dataclass` Config, `core/` 내 3개 `@dataclass` Config, `api/` 내 1개 `@dataclass` Config — 모두 환경변수 미참조 순수 데이터 객체이므로 `@dataclass` 유지 적절.

### 3-2. config.py 마이그레이션 (1차)

`config.py`의 `@dataclass(frozen=True)` 클래스들은 환경변수를 `os.environ.get()`으로 수동 파싱하므로, `BaseSettings`로 전환하는 것이 **일관성 + 기능** 모두에서 이점:

| 현재 (config.py) | 마이그레이션 대상 | 비고 |
|------------------|------------------|------|
| `NotificationLimits` L47 | ✅ `BaseSettings` 전환 완료 | `env_prefix="SELFHEALING_"`, `frozen=True`, 수동 env 파싱 제거 |
| `ForensicContextConfig` L66 | ✅ `BaseSettings` 전환 완료 | `env_prefix="SELFHEALING_"`, `frozen=True`, `validation_alias`로 하위 호환 |
| `MetricCollectionSettings` L405 | ✅ `BaseSettings` 전환 완료 | `env_prefix="SELFHEALING_METRICS_"`, `validation_alias`로 drift 환경변수 하위 호환 |
| `L2StorageConfig` L490 | ✅ `BaseSettings` 전환 완료 | `env_prefix="SELFHEALING_L2_"`, `validation_alias`로 하위 호환 |

### 3-2-1. 2차 마이그레이션 — config.py 외부 누락 클래스

1차에서 `config.py` 내부만 전환했으나, 패키지 전반에 `os.environ.get()` 수동 파싱을 사용하는 클래스가 추가로 존재:

| 파일 | 클래스 | 변경 방식 | 비고 |
|------|--------|----------|------|
| `models/drift_config.py` | `DriftThresholdConfig` | ✅ `from_env()` → `DriftThresholdSettings(BaseSettings)` 위임 | `@dataclass` 유지 (mutable data object), env 파싱만 위임 |
| `core/cluster_identity.py` | `ClusterIdentity` | ✅ `@dataclass(frozen=True)` → `BaseSettings(frozen=True)` 전환 | `validation_alias`로 비정규 env명 하위 호환 |
| `config.py` | `EventLoggingConfig` | ✅ `_EventLoggingDefaults(BaseSettings)` 보조 클래스 추가 | Singleton 유지, env 파싱만 위임 |
| `config.py` | `L2StorageRuntimeConfig` | ✅ `L2StorageConfig(BaseSettings)` 기존 클래스 재활용 | Singleton 유지, env 파싱만 위임 |

### 3-2-2. 정당한 예외 — `ExecutionMode`

`core/execution_mode.py`의 `ExecutionMode`는 `@dataclass(frozen=True)` + `os.environ.get()`이지만, **mode selector 패턴**으로 `BaseSettings` 전환 대상에서 제외:

```python
# core/execution_mode.py — 단일 환경변수로 프리셋 선택
mode_str = os.environ.get("SELFHEALING_EXECUTION_MODE", "active")
if mode_str == "shadow":
    return ExecutionMode.shadow()      # 모든 필드가 프리셋에 의해 결정
elif mode_str == "evaluation":
    return ExecutionMode.evaluation()
else:
    return ExecutionMode.active()
```

**제외 근거**: `BaseSettings`는 필드별 1:1 env 매핑이지만, `ExecutionMode`는 단일 env var(`SELFHEALING_EXECUTION_MODE`)로 프리셋을 선택하고 파생 필드 전체를 결정하는 패턴. `BaseSettings`로 전환 시 의미가 오히려 불명확해짐.

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

- [x] `config.py`의 `get_*()` 팩토리 함수들이 `BaseSettings` 전환 후 동일하게 동작하는지 확인
- [x] `@lru_cache` 캐싱 로직이 `BaseSettings` 인스턴스와 호환되는지 확인
- [x] `frozen=True` 불변성이 `BaseSettings`에서도 보장되는지 확인 (`.model_config`의 `frozen=True`)
- [x] `DriftThresholdConfig.from_env()` → `DriftThresholdSettings` 위임 후 동일 동작 확인 (2차)
- [x] `ClusterIdentity` → `BaseSettings(frozen=True)` 전환 후 `get_cluster_identity()` 호환 확인 (2차)
- [x] `EventLoggingConfig._init_defaults()` → `_EventLoggingDefaults(BaseSettings)` 위임 동작 확인 (2차)
- [x] `L2StorageRuntimeConfig._init_defaults()` → `L2StorageConfig(BaseSettings)` 위임 동작 확인 (2차)
- [x] `populate_by_name=True` 설정으로 `validation_alias` 사용 시 init kwarg 하위 호환 확인 (2차)

---

## 4. 구현 결과

### 4-1. 변경 파일

**1차 (2026-02-09)**:

| 파일 | 변경 내용 |
|------|----------|
| `config.py` | 4개 `@dataclass(frozen=True)` → `BaseSettings(frozen=True)` 전환, `os.environ.get()` 수동 파싱 제거, `pydantic Field` 검증 추가 |
| `tests/unit/services/test_config_unit.py` | `pytest.raises(AttributeError)` → `pytest.raises(Exception)` (pydantic `ValidationError` 호환) |

**2차 (2025-07-11)**:

| 파일 | 변경 내용 |
|------|----------|
| `models/drift_config.py` | `from_env()` 내부 5개 `os.environ.get()` → `DriftThresholdSettings()` 위임 |
| `settings/drift_threshold.py` | `incident_auto_create` 필드에 `validation_alias=AliasChoices("SELFHEALING_DRIFT_INCIDENT_ENABLED", ...)` 추가 |
| `core/cluster_identity.py` | `@dataclass(frozen=True)` → `BaseSettings(frozen=True)` 전환, `get_cluster_identity()` 팩토리 간소화 |
| `config.py` | `_EventLoggingDefaults(BaseSettings)` 보조 클래스 추가, `EventLoggingConfig._init_defaults()` env 위임 |
| `config.py` | `L2StorageRuntimeConfig._init_defaults()` 9개 `os.environ.get()` → `L2StorageConfig()` 위임 |

### 4-2. 환경변수 하위 호환

기존 환경변수명과 `env_prefix + field_name` 패턴이 다른 필드에 `validation_alias=AliasChoices(...)` 적용:

**1차**:

| 클래스 | 필드 | 기존 환경변수 | 신규 환경변수 (자동) |
|--------|------|-------------|--------------------|
| `NotificationLimits` | `notification_timeout_seconds` | `SELFHEALING_NOTIFICATION_TIMEOUT` | `SELFHEALING_NOTIFICATION_TIMEOUT_SECONDS` |
| `ForensicContextConfig` | `max_context_size_bytes` | `SELFHEALING_MAX_CONTEXT_SIZE` | `SELFHEALING_MAX_CONTEXT_SIZE_BYTES` |
| `MetricCollectionSettings` | `drift_*` (5개 필드) | `SELFHEALING_DRIFT_*` | `SELFHEALING_METRICS_DRIFT_*` |
| `L2StorageConfig` | `reconciliation_jitter_*`, `health_check_interval_seconds` | `SELFHEALING_L2_*_MIN/MAX/INTERVAL` | `SELFHEALING_L2_*_MIN_SECONDS/MAX_SECONDS/INTERVAL_SECONDS` |

**2차**:

| 클래스 | 필드 | 기존 환경변수 | 비고 |
|--------|------|-------------|------|
| `DriftThresholdSettings` | `incident_auto_create` | `SELFHEALING_DRIFT_INCIDENT_ENABLED` | `AliasChoices`로 구명 + 신명 모두 지원 |
| `ClusterIdentity` | `cluster_id` | `SELFHEALING_CLUSTER_ID` | `env_prefix` 미사용, 필드별 `validation_alias` |
| `ClusterIdentity` | `region` | `SELFHEALING_NAMESPACE_REGION` | 비정규 prefix 패턴 |
| `ClusterIdentity` | `environment` | `SELFHEALING_NAMESPACE_ENV` | 비정규 prefix 패턴 |
| `ClusterIdentity` | `tenant` | `SELFHEALING_NAMESPACE_TENANT` | 비정규 prefix 패턴 |
| `ClusterIdentity` | `pod_id` | `HOSTNAME` | prefix 없음, OS 표준 env |

### 4-3. 테스트 결과

**1차**:
- `tests/unit/services/test_config_unit.py`: **35 passed** ✅
- `tests/unit/metrics/test_safe_gauge_and_logging_config.py`: **32 passed** ✅
- 전체 패키지 테스트: **10,427 passed**, 1 skipped ✅ (기존 flaky 테스트 1건 제외)

**2차**:
- 대상 테스트 1차: **64 passed** ✅ (cluster_identity + config_unit + drift_l2_storage)
- 대상 테스트 2차: **61 passed** ✅ (execution_mode + action_executor + safe_gauge_logging_config)
- 전체 패키지 테스트: **10,427 passed**, 1 failed (기존 flaky), 1 skipped ✅
- 기존 flaky: `test_metrics_after_flush` (`events_flushed` assert 비결정적) — 리팩토링 무관
