# Self-Healing 코드 분석 보고서

> **Generated**: 2026-01-02 (Updated after middleware registration)
> **Tools Used**: vulture, pipdeptree, custom AST analyzer

---

## 📊 분석 요약

| 항목 | 값 |
|------|-----|
| 전체 모듈 수 | 365개 |
| 의존성이 있는 모듈 | 260개 (+6) |
| 독립(고아) 모듈 | 102개 (-6) |

> **Note**: 2026-01-02 미들웨어 통합으로 고아 모듈 6개 감소

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
| 1 | `core.timezone` | 34회 |
| 2 | `services.runtime_config` | 25회 |
| 3 | `interfaces.repositories` | 24회 |
| 4 | `models` | 23회 |
| 5 | `core.config` | 17회 |
| 6 | `interfaces.audit_adapter` | 16회 |
| 7 | `api.django.permissions` | 16회 |
| 8 | `factory` | 14회 |
| 9 | `config` | 13회 |
| 10 | `audit` | 12회 |
| 11 | `adapters.django.models` | 9회 |
| 12 | `core.state_backend` | 8회 |
| 13 | `interfaces.cache_provider` | 8회 |
| 14 | `adapters.django_repositories` | 7회 |
| 15 | `audit.event_buffer` | 7회 |
| 16 | `services.dlq_service` | 7회 |
| 17 | `services.error_budget_service` | 7회 |
| 18 | `services` | 7회 |
| 19 | `audit.backends.base` | 7회 |
| 20 | `services.metrics` | 7회 |

**해석**:
- `core.timezone`이 가장 핵심적인 유틸리티 (34회 참조)
- `interfaces.*`가 많이 참조됨 → 인터페이스 기반 설계 잘 됨
- `models` 모듈이 23회로 상위권 → 도메인 모델 중심 설계
- `factory`가 14회 참조 → DI 패턴 적용 확인

---

## 📈 가장 많은 의존성을 가진 모듈 (Top 15)

| 순위 | 모듈 | 의존성 수 |
|------|------|----------|
| 1 | `api.django.urls` | 24개 |
| 2 | `audit` | 20개 |
| 3 | `core` | 19개 |
| 4 | `factory` | 18개 |
| 5 | `api.django.views` | 13개 |
| 6 | `services.circuit_breaker.service` | 11개 |
| 7 | `services.error_budget_gate.gate` | 11개 |
| 8 | `services.dlq_service` | 10개 |
| 9 | `services.execution_services` | 9개 |
| 10 | `services.factory.base` | 9개 |
| 11 | `interfaces` | 8개 |
| 12 | `metrics.reconciler` | 8개 |
| 13 | `adapters.celery.tasks` | 8개 |
| 14 | `adapters.django.apps` | 8개 |
| 15 | `services.factory.registry` | 8개 |

**해석**:
- `urls`, `__init__` 파일들이 많은 의존성 → 정상 (라우팅/재export)
- `factory`가 18개 의존성 → DI 컨테이너로서 적절
- `services.factory.*` 패키지가 추가됨 → 팩토리 패턴 확장

---

## ⚠️ 독립(고아) 모듈 (102개)

다른 모듈에서 import되지 않는 모듈들입니다.
**이유**: 엔트리포인트, 설정 파일, 또는 실제 미사용 코드

### ✅ 2026-01-02 해결된 고아 모듈 (6개 → 등록 완료)

| 모듈 | 해결 방법 |
|------|----------|
| `api.django.tiering.middleware` | settings.py MIDDLEWARE에 등록 |
| `api.django.pool_circuit_breaker` | settings.py MIDDLEWARE에 등록 |
| `api.django.audit_middleware` | settings.py MIDDLEWARE에 등록 |
| `audit.trace.trace_id_middleware` | settings.py MIDDLEWARE에 등록 |
| `myproject.middleware.actor_middleware` | settings.py MIDDLEWARE에 등록 |
| `myproject.middleware.pool_timeout_middleware` | settings.py MIDDLEWARE에 등록 |

### 정상적인 고아 모듈 (엔트리포인트/설정)

| 모듈 | 이유 |
|------|------|
| `api.django.urls` | URL 라우팅 (settings.py에서 include) |
| `api.django.middleware` | 미들웨어 (settings.py에서 등록) ✅ |
| `adapters.django.admin` | Django Admin (자동 로드) |
| `adapters.django.apps` | Django AppConfig |
| `adapters.django.migrations.*` | 마이그레이션 파일 (3개) |
| `adapters.fastapi.middleware` | FastAPI 미들웨어 (app에서 등록) |
| `adapters.fastapi.routes` | FastAPI 라우트 (app에서 등록) |
| `tasks.*` | Celery 태스크 (Beat에서 실행) - 4개 |

### 검토 필요한 고아 모듈 (88개)

#### 어댑터 관련
| 모듈 | 상태 |
|------|------|
| `adapters.alert.file_adapter` | 등록되었으나 미사용 가능성 |
| `adapters.alert.null_adapter` | 테스트용 |
| `adapters.alert.stdout_adapter` | 개발용 |
| `adapters.audit.null_adapter` | 테스트용 |
| `adapters.audit.stdout_adapter` | 개발용 |
| `adapters.audit.worm_adapters` | WORM 규정 준수용 |
| `adapters.metrics.auto_tuning_adapter` | 미연결 가능성 ⚠️ |
| `adapters.observability.opentelemetry.*` | OTel 통합 (선택적 사용) - 4개 |
| `adapters.resilient` | ResilientStorageBackend 패키지 |

#### API 관련
| 모듈 | 상태 |
|------|------|
| `api.django.tiering.*` | Tiering 시스템 - 5개 (middleware 제외, `__init__.py`에서 re-export) |
| `api.django.reauthentication` | 재인증 (View 데코레이터로 사용) |
| `api.django.throttle_adapter` | 쓰로틀 어댑터 (DRF throttle_classes에 등록) |
| `api.django.views.error_budget.*` | Error Budget 뷰 - 3개 |
| `api.django.views.xtest.*` | 테스트 전용 뷰 - 6개 |

#### 서비스 관련
| 모듈 | 상태 |
|------|------|
| `services.circuit_breaker.*` | Circuit Breaker 하위 모듈 - 6개 |
| `services.emergency_mode.*` | Emergency Mode 하위 모듈 - 4개 |
| `services.error_budget.reconciliation.*` | Reconciliation 하위 모듈 - 5개 |
| `services.factory.*` | Factory 패턴 하위 모듈 - 5개 |
| `services.finops.*` | FinOps 하위 모듈 - 2개 |
| `services.metrics.*` | Metrics 하위 모듈 - 5개 |
| `services.runtime_config.*` | Runtime Config 하위 모듈 - 7개 |
| `services.blast_radius.service` | Blast Radius 서비스 |
| `services.chaos.*` | Chaos Engineering 하위 모듈 - 3개 |
| `services.compliance.service` | Compliance 서비스 |
| `services.learning.service` | Learning 서비스 |
| `services.rollback.service` | Rollback 서비스 |
| `services.throttle` | Throttle 패키지 |

#### 기타
| 모듈 | 상태 |
|------|------|
| `audit.api` | Audit API |
| `audit.continuous_audit_api` | Continuous Audit API |
| `config_tracker` | Config Tracker |
| `context` | Context 패키지 |
| `interfaces.notification` | Notification 인터페이스 |
| `utils` | Utils 패키지 |

### 전체 고아 모듈 목록 (108개)

```
__init__
adapters.airgap
adapters.alert.file_adapter
adapters.alert.null_adapter
adapters.alert.stdout_adapter
adapters.audit.null_adapter
adapters.audit.stdout_adapter
adapters.audit.worm_adapters
adapters.celery
adapters.django.admin
adapters.django.apps
adapters.django.migrations
adapters.django.migrations.0001_initial
adapters.django.migrations.0002_add_entity_fields
adapters.fastapi
adapters.fastapi.dependencies
adapters.fastapi.middleware
adapters.fastapi.routes
adapters.frameworks
adapters.metrics
adapters.metrics.auto_tuning_adapter
adapters.observability.opentelemetry.config
adapters.observability.opentelemetry.events
adapters.observability.opentelemetry.noop
adapters.observability.opentelemetry.spans
adapters.resilient
api
api.django
api.django.middleware
api.django.reauthentication
api.django.throttle_adapter
api.django.tiering.circuit_breaker
api.django.tiering.defaults
api.django.tiering.enums
api.django.tiering.middleware
api.django.tiering.models
api.django.tiering.registry
api.django.tiering.validator
api.django.urls
api.django.views.error_budget.deployment
api.django.views.error_budget.reconciliation
api.django.views.error_budget.status
api.django.views.xtest
api.django.views.xtest.base
api.django.views.xtest.circuit_breaker
api.django.views.xtest.error_budget
api.django.views.xtest.observability
api.django.views.xtest.snapshot
audit.api
audit.continuous_audit_api
config_tracker
context
core
interfaces.notification
services.backoff_calculator
services.blast_radius.service
services.chaos
services.chaos.experiments
services.chaos.scheduler_models
services.chaos_context
services.circuit_breaker.config
services.circuit_breaker.convenience
services.circuit_breaker.manual_control
services.circuit_breaker.protection
services.circuit_breaker.rate_limit_tracker
services.circuit_breaker.service
services.compliance.service
services.corruption_shield
services.emergency_mode.enums
services.emergency_mode.manager
services.emergency_mode.models
services.emergency_mode.recovery_gate
services.error_budget.reconciliation.enums
services.error_budget.reconciliation.models
services.error_budget.reconciliation.period_tracker
services.error_budget.reconciliation.service
services.error_budget.reconciliation.shadow_calculator
services.factory.base
services.factory.registry
services.factory.repository
services.factory.service
services.factory.singleton
services.finops.models
services.finops.service
services.forensic_context
services.idempotency_service
services.learning.service
services.metrics.alerting_rules
services.metrics.definitions
services.metrics.recorders
services.metrics.registry
services.metrics.updaters
services.rate_limit_coordinator
services.retry_handler
services.rollback.service
services.runtime_config.advanced_configs
services.runtime_config.approval
services.runtime_config.base
services.runtime_config.chaos_storage
services.runtime_config.constants
services.runtime_config.core_configs
services.runtime_config.strategy
services.throttle
tasks.chaos_scheduler
tasks.config_apply
tasks.drift_detection
tasks.governance
utils
```

---

## 🔗 연결 상태 요약

### ✅ 잘 연결된 컴포넌트

| 컴포넌트 | 연결 상태 |
|---------|----------|
| `factory` → `interfaces.*` | ✅ DI 패턴 (18개 의존성) |
| `services.*` → `interfaces.repositories` | ✅ 저장소 추상화 (24회 참조) |
| `api.django.*` → `services.*` | ✅ View-Service 분리 |
| `audit.*` → `interfaces.audit_adapter` | ✅ 감사 추상화 (16회 참조) |
| `core.timezone` | ✅ 핵심 유틸리티 (34회 참조) |
| `services.runtime_config` | ✅ 런타임 설정 (25회 참조) |
| `models` | ✅ 도메인 모델 (23회 참조) |
| `adapters.django.models` | ✅ Django 모델 (9회 참조) |
| `api.django.tiering` | ✅ 미들웨어 등록 완료 (2026-01-02) |
| `api.django.pool_circuit_breaker` | ✅ 미들웨어 등록 완료 (2026-01-02) |
| `api.django.audit_middleware` | ✅ 미들웨어 등록 완료 (2026-01-02) |
| `audit.trace` | ✅ 미들웨어 등록 완료 (2026-01-02) |

### ⚠️ 연결 검토 필요

| 컴포넌트 | 상태 |
|---------|------|
| `api.django.throttle_adapter` | DRF throttle_classes에 선택적 등록 |
| `api.django.reauthentication` | View 데코레이터로 사용 (필요 시) |
| `adapters.metrics.auto_tuning_adapter` | factory 미등록 |
| `adapters.resilient` | 새로 추가됨 - 연결 필요 |
| `services.factory.*` | 새 팩토리 패턴 - 통합 필요 |
| `services.circuit_breaker.*` | 하위 모듈 미노출 (6개) |
| `services.emergency_mode.*` | 하위 모듈 미노출 (4개) |
| `services.error_budget.reconciliation.*` | 하위 모듈 미노출 (5개) |

---

## 📊 변경 추이 (이전 vs 현재)

| 항목 | 이전 (v2.2) | 현재 (v2.3) | 변화 |
|------|-------------|-------------|------|
| 전체 모듈 수 | 365개 | 365개 | 0 |
| 의존성 있는 모듈 | 254개 | 260개 | +6 |
| 고아 모듈 | 108개 | 102개 | -6 |
| 등록된 미들웨어 | 6개 | 11개 | +5 |

**분석**:
- 미들웨어 통합 완료 (+5): TieringMiddleware, PoolCircuitBreakerMiddleware, AuditMiddleware 등
- 고아 모듈 감소 (-6): 미등록 미들웨어들이 settings.py에 등록됨
- 활성화/비활성화 토글 추가: 환경변수 또는 settings.py로 제어 가능

---

## 📋 권장 조치 (업데이트됨)

### 1. ✅ 완료된 조치
- ~~미등록 미들웨어 등록~~ → 2026-01-02 완료
- ~~하위 모듈 __init__.py 정리~~ → api.django, myproject.middleware 완료

### 2. 남은 조치
- `api.django.throttle_adapter` → 필요 시 View별 throttle_classes 등록
- `adapters.resilient` → factory에 등록 검토
- `services.*` 하위 모듈 → 부모 `__init__.py`에서 re-export

### 3. 새로운 패키지 통합
- `adapters.resilient` → factory에 등록
- `services.factory.*` → 기존 factory와 통합 검토

### 4. 순환 의존성 검사
- `import-linter` 설정 추가하여 레이어 규칙 강제

### 5. 문서 업데이트
- 고아 모듈 중 의도적인 것들 문서화
- 미등록 미들웨어 활성화 가이드 작성

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
