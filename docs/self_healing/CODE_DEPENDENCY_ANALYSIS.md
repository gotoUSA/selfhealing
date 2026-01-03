# Self-Healing 코드 분석 보고서

> **Generated**: 2026-01-03 (AST 분석기 재실행)
> **Tools Used**: vulture, pipdeptree, custom AST analyzer (`scripts/analyze_dependencies.py`)

---

## 📊 분석 요약

| 항목 | 수치 |
|------|------|
| 전체 모듈 수 | 362개 |
| 의존성이 있는 모듈 | 253개 |
| 독립(고아) 모듈 | **102개** |

### 고아 모듈 분류

| 분류 | 수량 | 설명 |
|------|------|------|
| 엔트리포인트/미들웨어 | 8개 | 정상 (외부에서 직접 호출) |
| 검토 필요 | **94개** | 연결 작업 또는 삭제 검토 필요 |

> **Note**: `__init__.py`에서 re-export 하더라도 **실제 import되지 않으면** 고아로 분류됨.
> AST 분석기는 `from package import module` 형태의 실제 import만 추적함.

---

## 🔧 사용된 도구

### 1. vulture
- **용도**: 사용하지 않는 코드(dead code) 탐지
- **결과**: 80% 신뢰도에서 미사용 코드 없음 ✅

### 2. pipdeptree
- **용도**: pip 패키지 의존성 트리 시각화
- **결과**: selfhealing 패키지는 외부 의존성 없음 (순수 라이브러리)

### 3. Custom AST Analyzer
- **용도**: 내부 모듈 간 import 관계 분석
- **방법**: Python ast 모듈로 모든 import문 추출
- **스크립트**: `scripts/analyze_dependencies.py`

---

## 🏆 가장 많이 참조되는 모듈 (Top 20)

| 순위 | 모듈 | 참조 횟수 |
|------|------|----------|
| 1 | `core.timezone` | 36회 |
| 2 | `services.runtime_config` | 26회 |
| 3 | `models` | 23회 |
| 4 | `factory` | 17회 |
| 5 | `interfaces.repositories` | 17회 |
| 6 | `core.config` | 16회 |
| 7 | `interfaces.audit_adapter` | 16회 |
| 8 | `api.django.permissions` | 16회 |
| 9 | `audit` | 14회 |
| 10 | `config` | 13회 |
| 11 | `services` | 12회 |
| 12 | `services.dlq_service` | 9회 |
| 13 | `core.state_backend` | 8회 |
| 14 | `interfaces.cache_provider` | 8회 |
| 15 | `audit.event_buffer` | 7회 |
| 16 | `services.error_budget_service` | 7회 |
| 17 | `audit.backends.base` | 7회 |
| 18 | `services.metrics` | 7회 |
| 19 | `adapters` | 6회 |
| 20 | `interfaces.rate_limit_storage` | 6회 |

**해석**:
- `core.timezone`이 가장 핵심적인 유틸리티 (36회 참조)
- `interfaces.*`가 많이 참조됨 → 인터페이스 기반 설계 잘 됨
- `models` 모듈이 23회로 상위권 → 도메인 모델 중심 설계
- `factory`가 17회 참조 → DI 패턴 적용 확인

---

## 📈 가장 많은 의존성을 가진 모듈 (Top 15)

| 순위 | 모듈 | 의존성 수 |
|------|------|----------|
| 1 | `api.django.urls` | 24개 |
| 2 | `audit` | 20개 |
| 3 | `core` | 19개 |
| 4 | `factory` | 18개 |
| 5 | `api.django.views` | 13개 |
| 6 | `services.error_budget_gate.gate` | 11개 |
| 7 | `services.circuit_breaker.service` | 10개 |
| 8 | `interfaces` | 9개 |
| 9 | `services.dlq_service` | 9개 |
| 10 | `services.execution_services` | 9개 |
| 11 | `services.factory.base` | 9개 |
| 12 | `metrics.reconciler` | 8개 |
| 13 | `services.factory.registry` | 8개 |
| 14 | `metrics` | 7개 |
| 15 | `services.replay_service` | 7개 |

**해석**:
- `urls`, `__init__` 파일들이 많은 의존성 → 정상 (라우팅/재export)
- `factory`가 18개 의존성 → DI 컨테이너로서 적절
- `services.factory.*` 패키지가 상위권 → 팩토리 패턴 확장

---

## 🏝️ 고아 모듈 전체 목록 (102개)

다른 모듈에서 import되지 않는 모듈들입니다.

### 🟢 엔트리포인트/미들웨어 (정상) - 8개

외부에서 직접 호출되므로 고아가 정상입니다.

| 모듈 | 이유 |
|------|------|
| `adapters.fastapi.middleware` | FastAPI 미들웨어 (app에서 등록) |
| `adapters.fastapi.routes` | FastAPI 라우트 (app에서 등록) |
| `api.django.tiering.middleware` | 미들웨어 (settings.py에서 등록) |
| `api.django.urls` | URL 라우팅 (settings.py에서 include) |
| `tasks` | Celery 태스크 패키지 |
| `tasks.chaos_scheduler` | Celery Beat 태스크 |
| `tasks.config_apply` | Celery Beat 태스크 |
| `tasks.governance` | Celery Beat 태스크 |

---

### 🔴 검토 필요 - 94개

다음 모듈들은 **실제로 import되지 않음**. 연결 작업 또는 삭제 검토 필요.

#### adapters 패키지 (19개)

| 모듈 | 권장 조치 |
|------|----------|
| `adapters.airgap` | 환경변수 기반 활성화 확인 |
| `adapters.alert.file_adapter` | `adapters.alert/__init__.py` re-export |
| `adapters.alert.null_adapter` | `adapters.alert/__init__.py` re-export |
| `adapters.alert.stdout_adapter` | `adapters.alert/__init__.py` re-export |
| `adapters.audit.null_adapter` | `adapters.audit/__init__.py` re-export |
| `adapters.audit.stdout_adapter` | `adapters.audit/__init__.py` re-export |
| `adapters.audit.worm_adapters` | `adapters.audit/__init__.py` re-export |
| `adapters.celery` | 용도 확인 |
| `adapters.fastapi` | 패키지 `__init__.py` 확인 |
| `adapters.fastapi.dependencies` | FastAPI 의존성 (app에서 등록) |
| `adapters.frameworks` | 용도 불명 - 삭제 검토 |
| `adapters.metrics` | 패키지 `__init__.py` 확인 |
| `adapters.metrics.auto_tuning_adapter` | factory 등록 또는 삭제 |
| `adapters.observability.opentelemetry.config` | OTel 활성화 시 사용 |
| `adapters.observability.opentelemetry.events` | OTel 활성화 시 사용 |
| `adapters.observability.opentelemetry.noop` | OTel 비활성화 시 기본값 |
| `adapters.observability.opentelemetry.spans` | OTel 활성화 시 사용 |
| `adapters.resilient` | factory 등록 필요 |
| `adapters.statistics` | 용도 확인 |

#### api 패키지 (18개)

| 모듈 | 권장 조치 |
|------|----------|
| `api` | 패키지 `__init__.py` |
| `api.django` | 패키지 `__init__.py` |
| `api.django.reauthentication` | View 데코레이터 (필요 시 사용) |
| `api.django.throttle_adapter` | View throttle_classes 등록 |
| `api.django.tiering.circuit_breaker` | tiering `__init__.py` re-export |
| `api.django.tiering.defaults` | tiering `__init__.py` re-export |
| `api.django.tiering.enums` | tiering `__init__.py` re-export |
| `api.django.tiering.models` | tiering `__init__.py` re-export |
| `api.django.tiering.registry` | tiering `__init__.py` re-export |
| `api.django.tiering.validator` | tiering `__init__.py` re-export |
| `api.django.views.error_budget.deployment` | views `__init__.py` re-export |
| `api.django.views.error_budget.reconciliation` | views `__init__.py` re-export |
| `api.django.views.error_budget.status` | views `__init__.py` re-export |
| `api.django.views.xtest` | 테스트 전용 (DEBUG 시) |
| `api.django.views.xtest.base` | 테스트 전용 |
| `api.django.views.xtest.circuit_breaker` | 테스트 전용 |
| `api.django.views.xtest.error_budget` | 테스트 전용 |
| `api.django.views.xtest.observability` | 테스트 전용 |
| `api.django.views.xtest.snapshot` | 테스트 전용 |

#### audit 패키지 (2개)

| 모듈 | 권장 조치 |
|------|----------|
| `audit.api` | urls.py 연결 확인 |
| `audit.continuous_audit_api` | urls.py 연결 확인 |

#### services 패키지 (45개)

| 모듈 | 권장 조치 |
|------|----------|
| `services.backoff_calculator` | `services/__init__.py` re-export |
| `services.blast_radius.service` | `services.blast_radius/__init__.py` re-export |
| `services.chaos` | 패키지 `__init__.py` |
| `services.chaos.experiments` | `services.chaos/__init__.py` re-export |
| `services.chaos.scheduler_models` | `services.chaos/__init__.py` re-export |
| `services.chaos_context` | `services/__init__.py` re-export |
| `services.circuit_breaker.config` | `services.circuit_breaker/__init__.py` re-export |
| `services.circuit_breaker.convenience` | `services.circuit_breaker/__init__.py` re-export |
| `services.circuit_breaker.manual_control` | `services.circuit_breaker/__init__.py` re-export |
| `services.circuit_breaker.protection` | `services.circuit_breaker/__init__.py` re-export |
| `services.circuit_breaker.rate_limit_tracker` | `services.circuit_breaker/__init__.py` re-export |
| `services.circuit_breaker.service` | `services.circuit_breaker/__init__.py` re-export |
| `services.corruption_shield` | `services/__init__.py` re-export |
| `services.emergency_mode.enums` | `services.emergency_mode/__init__.py` re-export |
| `services.emergency_mode.manager` | `services.emergency_mode/__init__.py` re-export |
| `services.emergency_mode.models` | `services.emergency_mode/__init__.py` re-export |
| `services.emergency_mode.recovery_gate` | `services.emergency_mode/__init__.py` re-export |
| `services.error_budget.reconciliation.enums` | reconciliation `__init__.py` re-export |
| `services.error_budget.reconciliation.models` | reconciliation `__init__.py` re-export |
| `services.error_budget.reconciliation.period_tracker` | reconciliation `__init__.py` re-export |
| `services.error_budget.reconciliation.service` | reconciliation `__init__.py` re-export |
| `services.error_budget.reconciliation.shadow_calculator` | reconciliation `__init__.py` re-export |
| `services.factory.base` | `services.factory/__init__.py` re-export |
| `services.factory.registry` | `services.factory/__init__.py` re-export |
| `services.factory.repository` | `services.factory/__init__.py` re-export |
| `services.factory.service` | `services.factory/__init__.py` re-export |
| `services.factory.singleton` | `services.factory/__init__.py` re-export |
| `services.finops.models` | `services.finops/__init__.py` re-export |
| `services.finops.service` | `services.finops/__init__.py` re-export |
| `services.forensic_context` | `services/__init__.py` re-export |
| `services.idempotency_service` | `services/__init__.py` re-export |
| `services.learning.service` | `services.learning/__init__.py` re-export |
| `services.metrics.alerting_rules` | `services.metrics/__init__.py` re-export |
| `services.metrics.definitions` | `services.metrics/__init__.py` re-export |
| `services.metrics.recorders` | `services.metrics/__init__.py` re-export |
| `services.metrics.registry` | `services.metrics/__init__.py` re-export |
| `services.metrics.updaters` | `services.metrics/__init__.py` re-export |
| `services.rate_limit_coordinator` | `services/__init__.py` re-export |
| `services.retry_handler` | `services/__init__.py` re-export |
| `services.rollback.service` | `services.rollback/__init__.py` re-export |
| `services.runtime_config.advanced_configs` | runtime_config `__init__.py` re-export |
| `services.runtime_config.approval` | runtime_config `__init__.py` re-export |
| `services.runtime_config.base` | runtime_config `__init__.py` re-export |
| `services.runtime_config.chaos_storage` | runtime_config `__init__.py` re-export |
| `services.runtime_config.constants` | runtime_config `__init__.py` re-export |
| `services.runtime_config.core_configs` | runtime_config `__init__.py` re-export |
| `services.runtime_config.strategy` | runtime_config `__init__.py` re-export |
| `services.throttle` | `services/__init__.py` re-export |
| `services.unified_notification` | `services/__init__.py` re-export |

#### 기타 (5개)

| 모듈 | 권장 조치 |
|------|----------|
| `__init__` | 루트 패키지 |
| `config_tracker` | `selfhealing/__init__.py` re-export |
| `context` | 용도 확인 후 처리 |
| `interfaces.notification` | `interfaces/__init__.py` re-export |
| `utils` | 필요 시 re-export |

---

## 🔗 연결 상태 요약

### ✅ 핵심 연결 상태

| 컴포넌트 | 연결 상태 | 비고 |
|---------|----------|------|
| `factory` → `interfaces.*` | ✅ DI 패턴 | 17회 참조 |
| `services.*` → `interfaces.repositories` | ✅ 저장소 추상화 | 17회 참조 |
| `api.django.*` → `services.*` | ✅ View-Service 분리 | |
| `audit.*` → `interfaces.audit_adapter` | ✅ 감사 추상화 | 16회 참조 |
| `core.timezone` | ✅ 핵심 유틸리티 | 36회 참조 (1위) |
| `services.runtime_config` | ✅ 런타임 설정 | 26회 참조 (2위) |

### ⚠️ 연결 작업 필요

| 카테고리 | 작업 대상 | 모듈 수 |
|---------|----------|--------|
| **services 하위 패키지** | 각 `__init__.py`에 re-export 추가 | 45개 |
| **api 하위 패키지** | 각 `__init__.py`에 re-export 추가 | 18개 |
| **adapters 하위 패키지** | 각 `__init__.py`에 re-export 추가 | 19개 |
| **기타** | 루트/인터페이스 정리 | 5개 |

---

## 📋 권장 조치

### 1. 🔴 필수 조치 (94개 모듈)

상세 작업 계획: [MODULE_INTEGRATION_WORKPLAN.md](MODULE_INTEGRATION_WORKPLAN.md) 참조

| 우선순위 | 작업 | 대상 |
|---------|------|------|
| P1 | `services.*` 하위 패키지 re-export | 45개 모듈 |
| P2 | `api.*` 하위 패키지 re-export | 18개 모듈 |
| P3 | `adapters.*` 하위 패키지 re-export | 19개 모듈 |
| P4 | 기타 (루트, interfaces) 정리 | 5개 모듈 |

### 2. 🟢 정상 (조치 불필요) - 8개

- 엔트리포인트/미들웨어: 외부에서 직접 호출되므로 고아가 정상

### 3. 🟡 선택적 조치 (필요 시)

- `adapters.observability.opentelemetry.*` - OTel 통합 시
- `api.django.views.xtest.*` - 테스트 환경에서만
- `adapters.resilient` - ResilientStorageBackend 사용 시

---

## 🛠️ 분석 도구 설정

### 분석 스크립트 실행
```bash
# 의존성 분석 실행
python scripts/analyze_dependencies.py

# 결과 파일 생성 위치
# docs/self_healing/dependency_analysis_result.json
```

### vulture 설정 (.vulture_whitelist.py)
```python
# 의도적 미사용 코드 화이트리스트
adapters.django.admin  # Django auto-discovery
adapters.django.apps   # Django AppConfig
api.django.urls        # URL routing entry point
tasks.*                # Celery tasks (Beat에서 실행)
adapters.fastapi.*     # FastAPI 엔트리포인트
```

### import-linter 설정 (pyproject.toml)
```toml
[tool.importlinter]
root_package = "selfhealing"

[[tool.importlinter.contracts]]
name = "Layers"
type = "layers"
layers = [
    "api",
    "services",
    "core",
    "adapters",
    "interfaces",
]
```

---

## 📁 관련 파일

| 파일 | 설명 |
|------|------|
| `scripts/analyze_dependencies.py` | 의존성 분석 스크립트 |
| `docs/self_healing/dependency_analysis_result.json` | 분석 결과 JSON |

---

## 참고 문서

- [00_INDEX.md](middleware_system/00_INDEX.md) - 미들웨어 시스템 인덱스
- [CODE_ANALYSIS_TOOLS.md](../CODE_ANALYSIS_TOOLS.md) - 코드 분석 도구 가이드
- [05_RESILIENT_STORAGE_BACKEND.md](middleware_system/05_RESILIENT_STORAGE_BACKEND.md) - ResilientStorageBackend 문서
