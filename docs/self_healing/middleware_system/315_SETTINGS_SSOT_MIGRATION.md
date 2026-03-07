# 315. Settings SSOT Migration — Singleton 통합 및 .env I/O 최적화

> **Status**: Planned
> **Severity**: P2 (MEDIUM)
> **Target**: `packages/selfhealing-python/src/selfhealing/settings/` 전체 (114개 파일)
> **References**:
> - 313 — Settings & Configuration Consistency (선행 작업, Q2에서 분리)
> - 90-102 — Config Migration 시리즈

---

## 1. 현황 및 문제

### 1.1 이원화된 Settings 싱글톤

현재 settings 아키텍처에 **두 종류의 인스턴스 생성 경로**가 공존한다:

| 경로 | 구현 | 인스턴스 수 |
|------|------|-----------|
| Root 경유 | `get_config().circuit_breaker` | Root 내부에 1개 |
| 독립 싱글톤 | `get_circuit_breaker_settings()` → 자체 `_settings` 전역변수 | 별도 1개 |

**결과**: 동일한 settings 클래스의 인스턴스가 애플리케이션 내에 **2개** 존재할 수 있다.
런타임 설정 갱신이나 테스트 환경에서 한쪽만 reset하면 **상태 불일치(Drift)**가 발생한다.

### 1.2 수치 현황

| 항목 | 수치 |
|------|------|
| settings 디렉토리 내 .py 파일 | 114개 (root.py, base.py, \_\_init\_\_.py 제외 시 111개) |
| Root에 조합된 sub-settings | 21개 (`settings/root.py:77-163`) |
| 독립 `get_*_settings()` 싱글톤 | 108개 |
| Root에 convenience getter가 있는 것 | 8개 (`root.py:333-378`) |
| Root로 delegation하는 sub-settings | **0개** |
| `get_config()` 직접 import하는 서비스 | 16개 |
| 개별 `get_*_settings()` import하는 서비스 | 100+개 |
| 명명 규칙 불일치 파일 | 20개 (17.5%) — §2.1에서 사전 정리 |

### 1.3 .env 중복 파싱 문제

112개 settings 파일이 `env_file=".env"`를 선언하고 있다.
Django 진입점(`myproject/settings/base.py`)에서도 `load_dotenv()`를 호출하므로,
동일한 `.env` 파일이 **최대 113번** 파싱된다 (Django 1회 + Pydantic 112회).

| 항목 | 비용 |
|------|------|
| .env 파싱 1회 (SSD) | ~0.5-2ms |
| 현재 (Django + 112 Pydantic) | ~57-226ms |
| 최적화 후 (load_dotenv 1회) | ~0.5-2ms |

이 비용은 **프로세스당 1회** 발생하므로 비즈니스 영향은 미미하나,
SSOT 통합 시 Root 초기화가 모든 sub-settings를 한꺼번에 생성하므로 최적화가 필요하다.

### 1.4 명명 규칙 불일치 현황

114개 파일 전수 조사 결과, 20개(17.5%)에서 명명 규칙 불일치를 발견했다.
자동화 스크립트 적용 전 사전 정리가 필요하다.

| 카테고리 | 건수 | 대표 사례 |
|---------|------|----------|
| Settings 클래스 없음 (유틸리티 파일) | 3 | `kafka.py`, `log_processors.py`, `structlog_config.py` |
| 파일명-클래스명 불일치 | 2 | `observability.py` → `OpenTelemetrySettings`, `logging_config.py` → `LoggingSettings` |
| Getter 없음 또는 이름 불일치 | 4 | `kafka.py`(없음), `namespace.py`(`get_effective_key_prefix`), `secrets.py`(`get_secrets`), `rate_limit_throttle_integration.py`(축약) |
| 약어 대문자 스타일 | 6 | `DLQSettings`, `SLASettings`, `SLOSettings`, `AirGapSettings`, `ThrottleSLANotificationSettings`, `XTestCleanupSettings` |
| 파일명에 settings 중복 | 1 | `audit_settings.py` → `AuditSettings` |

---

## 2. 개선 계획

### 2.1 Phase 0: 명명 규칙 사전 정리

자동화 스크립트의 전제 조건으로, 패턴에서 벗어난 20개 파일을 수동 정리한다.

#### 2.1.1 유틸리티 파일 이동 (settings 디렉토리에서 방출)

| 파일 | 현재 위치 | 이동 대상 | 사유 |
|------|----------|----------|------|
| `log_processors.py` | `settings/` | `observability/` | Settings 클래스 없음, structlog 프로세서 함수만 포함 |
| `structlog_config.py` | `settings/` | `observability/` | Settings 클래스 없음, structlog 초기화 로직만 포함 |

#### 2.1.2 파일명-클래스명 정렬

| 파일 | 현재 클래스명 | 변경 방향 | 사유 |
|------|-------------|----------|------|
| `observability.py` | `OpenTelemetrySettings` | 파일명을 `otel.py`로 변경 | 클래스명이 더 구체적이므로 파일명을 맞춤 |
| `logging_config.py` | `LoggingSettings` | 파일명을 `logging_settings.py`로 변경 | Python built-in `logging`과 충돌 방지 |
| `audit_settings.py` | `AuditSettings` | 파일명을 `audit.py`로 변경 | 패턴 정규화 (`{domain}.py`) |

#### 2.1.3 누락 Getter 추가

| 파일 | 현재 상태 | 추가할 Getter |
|------|----------|-------------|
| `namespace.py` | `get_effective_key_prefix()` 만 있음 | `get_namespace_settings()` 추가 |
| `secrets.py` | `get_secrets()` | `get_secrets_settings()`로 rename |
| `rate_limit_throttle_integration.py` | `get_rate_limit_throttle_settings()` | `get_rate_limit_throttle_integration_settings()`로 rename |
| `kafka.py` | Getter 없음 | Settings 클래스가 없으므로 Phase 0 대상에서 제외 (유틸리티 파일 여부 재확인) |

#### 2.1.4 약어 스타일 — 현행 유지

`DLQ`, `SLA`, `SLO` 등 약어의 대문자 스타일은 Python 관례상 자연스러우므로 변경하지 않는다.
자동화 스크립트에 약어 매핑 테이블을 별도 정의하여 처리한다.

```python
ACRONYM_MAP = {
    "dlq": "DLQ",
    "sla": "SLA",
    "slo": "SLO",
    "hpa": "HPA",
    "xtest": "XTest",
    "airgap": "AirGap",
}
```

### 2.2 Phase 1: .env 파싱 최적화 — `load_dotenv()` 1회 호출

#### 2.2.1 설계 결정: `load_dotenv()` vs `SharedEnvSource`

| 비교 | `load_dotenv()` 1회 | `SharedEnvSource` 커스텀 클래스 |
|------|--------------------|-----------------------------|
| 구현 복잡도 | `env_file=None` 일괄 변경만 | 커스텀 Source + 112개 `settings_customise_sources` 오버라이드 |
| 유지보수 | python-dotenv 기본 API | 커스텀 코드 유지 필요 |
| 테스트 호환성 | 기존 `monkeypatch.setenv()` 패턴 그대로 동작 | `SharedEnvSource.reset()` 추가 호출 필요 |
| os.environ 우선순위 | `load_dotenv(override=False)`로 K8s Secret 우선 보장 | Pydantic source chain에서 명시적 제어 |
| 기존 코드 현황 | Django `myproject/settings/base.py`에서 이미 호출 중 | `settings_customise_sources` 오버라이드 현재 0건 |

**결정: `load_dotenv()` 1회 호출 방식 채택.**

사유:
1. Django 진입점에서 이미 `load_dotenv()`를 호출하고 있어 아키텍처가 일관됨
2. 112개 파일에 `settings_customise_sources` 오버라이드를 추가하는 것은 변경량 대비 가치가 낮음
3. 테스트 코드 전수 조사 결과, `.env` 파싱을 직접 테스트하는 코드가 0건이며 모든 테스트가 `os.environ` 기반
4. `load_dotenv(override=False)` (기본값)는 기존 환경변수를 덮어쓰지 않으므로 K8s Secret 우선순위 보장
5. `.env` 키들이 전부 `SELFHEALING_*`, `DJANGO_*` 등 프로젝트 전용 prefix를 사용하여 namespace 충돌 위험 극소

#### 2.2.2 구현

```python
# settings/root.py 최상단 (모든 settings보다 먼저 실행)
from dotenv import load_dotenv

load_dotenv(override=False)  # .env → os.environ (1회, 기존 env var 우선)
```

```python
# 112개 settings 파일 일괄 변경 (codemod 스크립트)
# BEFORE
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_CB_",
    env_file=".env",           # ← 개별 파싱
    env_file_encoding="utf-8",
    extra="ignore",
    validate_default=True,
)

# AFTER
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_CB_",
    env_file=None,             # ← os.environ에서만 읽기
    extra="ignore",
    validate_default=True,
)
```

### 2.3 Phase 2: Root 도메인 그룹핑 + Facade 패턴

#### 2.3.1 설계 결정: Flat 1-depth vs 도메인 그룹 객체

| 비교 | Flat 1-depth (108개 나열) | 논리적 그룹 객체 |
|------|------------------------|----------------|
| IDE 자동완성 | `get_config().` → 108개 | `get_config().` → 21 Field + 14 그룹 |
| 인지적 부담 | root.py 500줄 이상 | root.py ~100줄 + groups.py ~300줄 |
| 파일 이동 | 없음 | 없음 (물리적 디렉토리 변경 없음) |
| import 경로 변경 | 없음 | 없음 (Facade 함수 시그니처 유지) |
| 확장성 | 신규 settings 추가 시 root.py에 계속 축적 | 해당 그룹에만 추가 |

**결정: 논리적 그룹 객체 방식 채택.**

사유:
1. 현재 개발 단계에서 구조를 잡아야 기술 부채를 방지할 수 있음
2. 물리적 디렉토리 이동이 없으므로 기존 import 경로가 100% 유지됨
3. Facade 함수 (`get_*_settings()`)의 시그니처가 유지되므로 서비스 코드 변경 불필요
4. CLAUDE.md의 모듈 구조와 자연스럽게 정렬됨

#### 2.3.2 도메인 그룹 구조

| 그룹 | 파일 수 | 대표 settings |
|------|---------|-------------|
| `CoreGroup` | 5 | backoff, circuit_breaker, circuit_breaker_advanced, pool_monitor, admission_control |
| `ServicesGroup` | 41 | chaos, dlq, retry, error_budget, canary, recovery, forensic, governance 등 |
| `AuditGroup` | 8 | audit, audit_integrity, audit_reconciler, audit_sync, audit_watchdog, hash_chain, cascade_retention, event_journal |
| `CoordinationGroup` | 3 | distributed_lock, leader_election, redis_key_guard |
| `MultiRegionGroup` | 5 | namespace, namespace_emergency, cell_topology, propagation, regional_recovery_policy |
| `MetricsGroup` | 5 | drift_detection, drift_threshold, metrics, safe_gauge, system_metrics_cache |
| `ScalingGroup` | 10 | backpressure, event_buffer, l2_storage, rate_limit, ring_buffer, scale, state_cache, throttle 등 |
| `ResilienceGroup` | 4 | bulkhead, hedging, resilient_recorder, resource_monitor |
| `ObservabilityGroup` | 7 | correlation, correlation_engine, detection, kafka_producer, otel (구 observability.py) 등 |
| `AdaptersGroup` | 8 | celery_task, config_shadow, http_client, layered_provider, notification, notification_channel, secrets, thread_management |
| `SecurityGroup` | 4 | corruption_shield, domain_sensitivity, idempotency, security |
| `SLOGroup` | 5 | dashboard, postmortem, sla, slo, steady_state |
| `MetaGroup` | 6 | gate_fault, meta_watchdog, pipeline, resource_guard, runtime_feedback, safety_bounds |
| `TestingGroup` | 6 | airgap, jitter, predictive_forecaster, sampling, stress_test, xtest_cleanup |

#### 2.3.3 그룹 객체 구현

```python
# settings/groups.py (신규)
from functools import cached_property


class CoreGroup:
    """Core 모듈 설정 그룹: backoff, circuit breaker, pool monitor 등."""

    @cached_property
    def admission_control(self) -> "AdmissionControlSettings":
        from selfhealing.settings.admission_control import AdmissionControlSettings
        return AdmissionControlSettings()

    @cached_property
    def backoff(self) -> "BackoffSettings":
        from selfhealing.settings.backoff import BackoffSettings
        return BackoffSettings()

    @cached_property
    def circuit_breaker_advanced(self) -> "CircuitBreakerAdvancedSettings":
        from selfhealing.settings.circuit_breaker_advanced import CircuitBreakerAdvancedSettings
        return CircuitBreakerAdvancedSettings()

    @cached_property
    def pool_monitor(self) -> "PoolMonitorSettings":
        from selfhealing.settings.pool_monitor import PoolMonitorSettings
        return PoolMonitorSettings()


class ScalingGroup:
    """Scaling 모듈 설정 그룹: rate limit, backpressure, load shedding 등."""

    @cached_property
    def backpressure(self) -> "BackpressureSettings":
        from selfhealing.settings.backpressure import BackpressureSettings
        return BackpressureSettings()

    @cached_property
    def event_buffer(self) -> "EventBufferSettings":
        from selfhealing.settings.event_buffer import EventBufferSettings
        return EventBufferSettings()

    # ... 나머지 scaling 관련 settings


class AuditGroup:
    """Audit 모듈 설정 그룹: audit logging, hash chain, WAL 등."""

    @cached_property
    def audit(self) -> "AuditSettings":
        from selfhealing.settings.audit import AuditSettings  # Phase 0에서 rename된 파일
        return AuditSettings()

    @cached_property
    def audit_integrity(self) -> "AuditIntegritySettings":
        from selfhealing.settings.audit_integrity import AuditIntegritySettings
        return AuditIntegritySettings()

    # ... 나머지 audit 관련 settings

# ... 14개 그룹 동일 패턴
```

#### 2.3.4 Root 확장 — 그룹 통합

```python
# settings/root.py — Phase 2 확장
from functools import cached_property

class SelfHealingSettings(BaseSettings):
    # ======================================================================
    # 기존 21개 Field (하위호환 유지, 즉시 초기화)
    # ======================================================================
    circuit_breaker: CircuitBreakerSettings = Field(...)
    dlq: DLQSettings = Field(...)
    retry: RetrySettings = Field(...)
    # ... 기존 21개 유지

    # ======================================================================
    # 도메인 그룹 (cached_property, lazy 초기화)
    # ======================================================================
    @cached_property
    def core(self) -> "CoreGroup":
        from selfhealing.settings.groups import CoreGroup
        return CoreGroup()

    @cached_property
    def scaling(self) -> "ScalingGroup":
        from selfhealing.settings.groups import ScalingGroup
        return ScalingGroup()

    @cached_property
    def audit_group(self) -> "AuditGroup":
        from selfhealing.settings.groups import AuditGroup
        return AuditGroup()

    @cached_property
    def coordination(self) -> "CoordinationGroup":
        from selfhealing.settings.groups import CoordinationGroup
        return CoordinationGroup()

    @cached_property
    def multi_region(self) -> "MultiRegionGroup":
        from selfhealing.settings.groups import MultiRegionGroup
        return MultiRegionGroup()

    @cached_property
    def metrics_group(self) -> "MetricsGroup":
        from selfhealing.settings.groups import MetricsGroup
        return MetricsGroup()

    @cached_property
    def resilience(self) -> "ResilienceGroup":
        from selfhealing.settings.groups import ResilienceGroup
        return ResilienceGroup()

    @cached_property
    def obs(self) -> "ObservabilityGroup":
        from selfhealing.settings.groups import ObservabilityGroup
        return ObservabilityGroup()

    @cached_property
    def adapters(self) -> "AdaptersGroup":
        from selfhealing.settings.groups import AdaptersGroup
        return AdaptersGroup()

    @cached_property
    def security_group(self) -> "SecurityGroup":
        from selfhealing.settings.groups import SecurityGroup
        return SecurityGroup()

    @cached_property
    def slo_group(self) -> "SLOGroup":
        from selfhealing.settings.groups import SLOGroup
        return SLOGroup()

    @cached_property
    def meta(self) -> "MetaGroup":
        from selfhealing.settings.groups import MetaGroup
        return MetaGroup()

    @cached_property
    def testing(self) -> "TestingGroup":
        from selfhealing.settings.groups import TestingGroup
        return TestingGroup()

    @cached_property
    def services(self) -> "ServicesGroup":
        from selfhealing.settings.groups import ServicesGroup
        return ServicesGroup()

    # ======================================================================
    # 전체 직렬화 (model_dump 보완)
    # ======================================================================
    def to_full_dict(self) -> dict[str, Any]:
        """model_dump() + @cached_property 그룹 포함 전체 직렬화.

        CLI --inspect, Admin API 등에서 전체 설정을 확인할 때 사용한다.
        초기화되지 않은 cached_property는 포함하지 않는다 (lazy 원칙 유지).
        """
        result = self.model_dump()
        for name in self._cached_property_names():
            if name in self.__dict__:
                val = self.__dict__[name]
                result[name] = self._group_to_dict(val)
        return result

    @classmethod
    def _cached_property_names(cls) -> list[str]:
        return [
            name for name, val in vars(cls).items()
            if isinstance(val, cached_property)
        ]

    @staticmethod
    def _group_to_dict(group: Any) -> dict[str, Any]:
        """그룹 객체의 초기화된 cached_property만 직렬화."""
        result: dict[str, Any] = {}
        for name, val in vars(type(group)).items():
            if isinstance(val, cached_property) and name in group.__dict__:
                prop_val = group.__dict__[name]
                if hasattr(prop_val, "model_dump"):
                    result[name] = prop_val.model_dump()
                else:
                    result[name] = prop_val
        return result
```

**장점**:
- Root 생성 시 기존 21개만 즉시 초기화 (현재와 동일한 비용)
- 나머지는 `get_config().scaling.backpressure` 처럼 **처음 접근할 때만** 초기화
- IDE 자동완성: `get_config().` → 21 Field + 14 그룹, `get_config().scaling.` → 해당 도메인만
- `to_full_dict()`로 CLI/API에서 전체 설정 직렬화 가능

#### 2.3.5 Facade 변환 — `get_*_settings()` → Root 그룹 경유

```python
# BEFORE (독립 싱글톤)
_settings: BackpressureSettings | None = None

def get_backpressure_settings() -> BackpressureSettings:
    global _settings
    if _settings is None:
        _settings = BackpressureSettings()
    return _settings

def reset_backpressure_settings() -> None:
    global _settings
    _settings = None


# AFTER (Root 그룹 경유 Facade)
def get_backpressure_settings() -> BackpressureSettings:
    """Root settings 경유 단일 진입점 (SSOT)."""
    from selfhealing.settings.root import get_config
    return get_config().scaling.backpressure

def reset_backpressure_settings() -> None:
    """해당 cached_property만 개별 reset (테스트 격리 유지)."""
    from selfhealing.settings.root import get_config
    try:
        del get_config().scaling.__dict__["backpressure"]
    except KeyError:
        pass
```

**핵심**: `from selfhealing.settings.backpressure import get_backpressure_settings`로
import하는 서비스 코드는 **변경 불필요**.

#### 2.3.6 reset 시맨틱 — 개별 `cached_property` 삭제 방식

**설계 결정**: `reset_*_settings()`를 `reset_config()`으로 위임하지 않는다.

사유:
- 기존 테스트 127개 파일이 개별 `reset_*_settings()`로 **선택적 reset**을 수행
- `reset_config()`으로 위임하면 A만 리셋하려 했는데 B까지 날아가는 부작용 발생
- `cached_property`는 Python descriptor 프로토콜에 의해 `__dict__`에서 해당 키를 삭제하면 다음 접근 시 재생성됨

```python
# 개별 reset 패턴 (테스트 격리 보존)
def reset_backpressure_settings() -> None:
    from selfhealing.settings.root import get_config
    try:
        del get_config().scaling.__dict__["backpressure"]
    except KeyError:
        pass

# 전체 reset (기존 reset_config()과 동일)
def reset_config() -> None:
    global _settings
    _settings = None  # Root 자체를 날리면 모든 그룹/속성 자동 초기화
```

#### 2.3.7 `to_full_dict()` — model_dump() 직렬화 보완

**배경**: 코드 조사 결과, Root를 직렬화하는 경로가 3건 존재한다.

| 위치 | 코드 | 영향 |
|------|------|------|
| `selfhealing_config.py:98-104` | `_model_to_dict(config)` → `model.model_dump()` | CLI `--inspect`에서 그룹 내 settings 누락 |
| `config.py:64-86` | `GET /api/self-healing/config/` | RuntimeConfigManager 경유, 별도 경로 |
| `selfhealing_config.py:199` | `SelfHealingSettings.model_json_schema()` | JSON 스키마에서 그룹 미포함 |

**수정 대상**:

```python
# selfhealing_config.py — CLI 커맨드 패치
def _model_to_dict(self, model) -> dict[str, Any]:
    if hasattr(model, "to_full_dict"):
        return model.to_full_dict()
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return dict(model)
```

### 2.4 Phase 3: 테스트 인프라 단순화

SSOT 완성 후 `reset_config()` 하나로 전체 settings 초기화가 가능하다.
개별 `reset_*_settings()`도 유지되므로 세밀한 테스트 격리도 가능하다.

```python
# tests/conftest.py — Phase 3 단순화
@pytest.fixture(autouse=True, scope="function")
def auto_reset_all_settings():
    """모든 settings를 한 번에 리셋."""
    from selfhealing.settings.root import reset_config

    reset_config()
    yield
    reset_config()
```

---

## 3. 마이그레이션 순서

| Phase | 작업 | 파일 수 | 우선순위 | 의존성 |
|-------|------|---------|----------|--------|
| 0 | 유틸리티 파일 이동 (2건) | 2 이동 | P2 | 없음 |
| 0.1 | 파일명-클래스명 정렬 (3건) | 3 rename | P2 | 없음 |
| 0.2 | 누락 Getter 추가/rename (3건) | 3 수정 | P2 | 없음 |
| 0.3 | 약어 매핑 테이블 정의 | 스크립트용 | P2 | 없음 |
| 1 | `root.py` 최상단에 `load_dotenv(override=False)` 추가 | 1 수정 | P2 | Phase 0 |
| 1.1 | 112개 settings 파일 `env_file=".env"` → `env_file=None` 일괄 변경 | 112 수정 | P2 | Phase 1 |
| 2 | `settings/groups.py` 생성 (14개 그룹 객체) | 1 신규 | P2 | Phase 1 |
| 2.1 | Root에 14개 그룹 `@cached_property` 추가 + `to_full_dict()` 구현 | 1 수정 | P2 | Phase 2 |
| 2.2 | 108개 `get_*_settings()`를 Root 그룹 Facade로 변환 | 108 수정 | P2 | Phase 2.1 |
| 2.3 | 108개 `reset_*_settings()`를 개별 `cached_property` 삭제 방식으로 변환 | 108 수정 | P2 | Phase 2.2 |
| 2.4 | 독립 `_settings` 전역변수 제거 | 108 수정 | P2 | Phase 2.3 |
| 2.5 | CLI `selfhealing_config` 커맨드 `to_full_dict()` 패치 | 1 수정 | P2 | Phase 2.1 |
| 3 | `tests/conftest.py` 단순화 | 1 수정 | P3 | Phase 2 |

> Phase 1.1, 2.2, 2.3, 2.4는 **자동화 스크립트**로 일괄 변환 가능 (AST 변환 또는 codemod).
> Phase 0에서 명명 규칙을 정리한 후 스크립트를 적용하면, 약어 매핑 테이블만으로 나머지 ~94개를 자동 처리할 수 있다.

---

## 4. 순환 import 방지 전략

현재 import 방향: `root.py` → 21개 sub-settings (단방향, 안전)

코드 전수 조사 결과, 순환 참조 위험은 **제로**로 확인되었다:
- `root.py`는 settings 하위 모듈만 import (`root.py:33-53`)
- settings → 비-settings import는 `namespace.py` → `core.test_mode_context` 1건뿐 (역참조 없음)
- `services/`, `core/`, `adapters/`, `factory.py` 모두 함수 내부 lazy import만 사용 (56개 확인)

SSOT 후 추가되는 방향: sub-settings의 `get_*_settings()` → `root.py` (역방향)

**방지책**: sub-settings에서 root import를 **함수 내부 지연 import**로 처리.

```python
def get_backpressure_settings() -> BackpressureSettings:
    from selfhealing.settings.root import get_config  # 함수 호출 시점에만 import
    return get_config().scaling.backpressure
```

이 패턴은 313에서 `ThreadManagementSettings`와 `DetectionSettings`에 이미 적용되어 검증되었다.

---

## 5. 위험 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| `@cached_property`가 `model_dump()`에 미포함 | CLI `--inspect`, Admin API에서 그룹 내 settings 누락 | `to_full_dict()` 메서드로 전체 직렬화 보완 + CLI 패치 (Phase 2.5) |
| 112개 일괄 변환 시 실수 | 개별 settings 동작 변경 | Phase 0에서 명명 규칙 정리 후 AST codemod + 기존 테스트 스위트로 회귀 검증 |
| `load_dotenv()`와 `os.environ` 우선순위 | `.env` 값이 K8s Secret을 덮어쓸 수 있음 | `load_dotenv(override=False)` 사용 — 기존 환경변수가 항상 우선 |
| 개별 reset 시 그룹 객체 경로 2단계 | `del get_config().scaling.__dict__["backpressure"]` 접근 복잡 | Facade의 `reset_*_settings()` 함수가 이를 캡슐화하므로 호출자에게 노출되지 않음 |
| 그룹 소속 변경 시 Facade 경로 수정 필요 | settings의 그룹이 바뀌면 Facade 내부 경로도 수정 | 그룹 소속은 CLAUDE.md 모듈 구조와 정렬되어 있어 변경 가능성 낮음 |

---

## 6. 성능 예측

| 시나리오 | Before (현재) | After (SSOT) |
|---------|--------------|-------------|
| .env 파싱 횟수 | 113회 (Django 1 + Pydantic 112) | **1회** (`load_dotenv()`) |
| Root `get_config()` 초기화 | 21개 sub-settings 즉시 생성 | 21개 즉시 + 14 그룹 lazy |
| 개별 `get_*_settings()` | 독립 인스턴스 생성 | Root 그룹 속성 접근 (포인터 반환) |
| `reset_config()` | Root만 초기화, 독립 싱글톤은 잔존 | **전체 초기화 완료** |
| `reset_*_settings()` | 개별 싱글톤 초기화 | 해당 `cached_property`만 초기화 (그룹 내 다른 settings 영향 없음) |

---

## 7. 313과의 관계

313에서 신규 생성한 `ThreadManagementSettings`와 `DetectionSettings`는
이미 Root 경유 패턴(Facade)을 적용했다. 이 두 클래스가 315의 **마이그레이션 템플릿**이 된다.

315의 Phase 2에서 기존 108개를 동일한 패턴으로 변환할 때,
313의 구현을 참조 예시(reference implementation)로 사용한다.

---

## 8. 리뷰 결정 기록

315 문서 리뷰에서 확정된 7가지 설계 결정을 기록한다.

| # | 질문 | 결정 | 사유 |
|---|------|------|------|
| R1 | .env 파싱 방식 | `load_dotenv()` 1회 호출 | Django에서 이미 호출 중, 테스트가 os.environ 기반, `settings_customise_sources` 오버라이드 0건 |
| R2 | Root God Object 방지 | 논리적 그룹 객체 14개로 2-depth 구조화 | 개발 단계에서 구조 확정, 물리적 디렉토리 이동 없이 IDE 경험 개선 |
| R3 | 명명 규칙 예외 20건 | Phase 0에서 사전 수동 정리 | 스크립트 자동화 전제 조건, 80/20 법칙 |
| R4 | reset 시맨틱 | `cached_property` 개별 삭제 | 테스트 127개 파일의 선택적 reset 패턴 보존 |
| R5 | 순환 참조 | 위험 제로 확인, 함수 내부 지연 import | 전수 조사 완료, 313에서 검증된 패턴 |
| R6 | SharedEnvSource 폐기 | `load_dotenv()` + `env_file=None`으로 단순화 | 커스텀 Source 유지보수 부담 > 가치, 112개 오버라이드 불필요 |
| R7 | `model_dump()` 누락 | `to_full_dict()` 메서드 + CLI 패치 | CLI `--inspect`, `model_json_schema()` 등 3건의 직렬화 경로 발견 |

---

## 9. 변경 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2026-03-07 | 1.0.0 | 초안 작성 (313 Q2 리뷰에서 분리) |
| 2026-03-07 | 2.0.0 | 7건 리뷰 반영: Phase 0 추가, SharedEnvSource → load_dotenv 전환, 도메인 그룹 객체 도입, reset 개별 삭제 방식, to_full_dict() 직렬화 보완, 명명 규칙 사전 정리, 위험 테이블 수정 |
