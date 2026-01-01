# Self-Healing 코드 분석 보고서

> **Generated**: 2026-01-02  
> **Tools Used**: vulture, pipdeptree, custom AST analyzer

---

## 📊 분석 요약

| 항목 | 값 |
|------|-----|
| 전체 모듈 수 | 363개 |
| 의존성이 있는 모듈 | 230개 |
| 독립(고아) 모듈 | 92개 |

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

---

## 🏆 가장 많이 참조되는 모듈 (Top 15)

| 순위 | 모듈 | 참조 횟수 |
|------|------|----------|
| 1 | `core.timezone` | 34회 |
| 2 | `services.runtime_config` | 25회 |
| 3 | `interfaces.repositories` | 22회 |
| 4 | `core.config` | 17회 |
| 5 | `interfaces.audit_adapter` | 16회 |
| 6 | `api.django.permissions` | 16회 |
| 7 | `factory` | 13회 |
| 8 | `audit` | 12회 |
| 9 | `adapters.django.models` | 9회 |
| 10 | `interfaces.cache_provider` | 8회 |
| 11 | `core.state_backend` | 8회 |
| 12 | `audit.event_buffer` | 7회 |
| 13 | `services.metrics` | 7회 |
| 14 | `services.dlq_service` | 7회 |
| 15 | `services.error_budget_service` | 7회 |

**해석**: 
- `core.timezone`이 가장 핵심적인 유틸리티
- `interfaces.*`가 많이 참조됨 → 인터페이스 기반 설계 잘 됨
- `factory`가 13회 참조 → DI 패턴 적용 확인

---

## 📈 가장 많은 의존성을 가진 모듈 (Top 10)

| 순위 | 모듈 | 의존성 수 |
|------|------|----------|
| 1 | `api.django.urls` | 24개 |
| 2 | `audit.__init__` | 20개 |
| 3 | `core.__init__` | 19개 |
| 4 | `factory` | 14개 |
| 5 | `api.django.views.__init__` | 13개 |
| 6 | `services.error_budget_gate.gate` | 11개 |
| 7 | `services.execution_services` | 10개 |
| 8 | `services.dlq_service` | 9개 |
| 9 | `services.circuit_breaker.service` | 9개 |

**해석**:
- `urls`, `__init__` 파일들이 많은 의존성 → 정상 (라우팅/재export)
- `error_budget_gate.gate`가 11개 → 리팩토링 후보

---

## ⚠️ 독립(고아) 모듈 (92개)

다른 모듈에서 import되지 않는 모듈들입니다.
**이유**: 엔트리포인트, 설정 파일, 또는 실제 미사용 코드

### 정상적인 고아 모듈 (엔트리포인트/설정)

| 모듈 | 이유 |
|------|------|
| `api.django.urls` | URL 라우팅 (settings.py에서 include) |
| `api.django.middleware` | 미들웨어 (settings.py에서 등록) |
| `adapters.django.admin` | Django Admin (자동 로드) |
| `adapters.django.apps` | Django AppConfig |
| `adapters.django.migrations.*` | 마이그레이션 파일 |
| `adapters.fastapi.middleware` | FastAPI 미들웨어 (app에서 등록) |
| `adapters.fastapi.routes` | FastAPI 라우트 (app에서 등록) |
| `tasks.*` | Celery 태스크 (Beat에서 실행) |

### 검토 필요한 고아 모듈

| 모듈 | 상태 |
|------|------|
| `adapters.alert.file_adapter` | 등록되었으나 미사용 가능성 |
| `adapters.alert.null_adapter` | 테스트용 |
| `adapters.alert.stdout_adapter` | 개발용 |
| `adapters.audit.null_adapter` | 테스트용 |
| `adapters.audit.stdout_adapter` | 개발용 |
| `adapters.audit.worm_adapters` | WORM 규정 준수용 |
| `adapters.metrics.auto_tuning_adapter` | 미연결 가능성 ⚠️ |
| `adapters.observability.opentelemetry.*` | OTel 통합 (선택적 사용) |
| `api.django.tiering.*` | Tiering 시스템 (미등록 미들웨어) |
| `api.django.reauthentication` | 재인증 (미등록) |
| `api.django.throttle_adapter` | 쓰로틀 어댑터 (미등록) |

### 전체 고아 모듈 목록 (92개)

```
adapters.alert.file_adapter
adapters.alert.null_adapter
adapters.alert.stdout_adapter
adapters.audit.null_adapter
adapters.audit.stdout_adapter
adapters.audit.worm_adapters
adapters.django.admin
adapters.django.apps
adapters.django.migrations.0001_initial
adapters.django.migrations.0002_add_entity_fields
adapters.fastapi.dependencies
adapters.fastapi.middleware
adapters.fastapi.routes
adapters.metrics.auto_tuning_adapter
adapters.observability.opentelemetry.config
adapters.observability.opentelemetry.events
adapters.observability.opentelemetry.noop
adapters.observability.opentelemetry.spans
api.django.middleware
api.django.rate_limit
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
api.django.views.blast_radius
api.django.views.compliance_dna
api.django.views.emergency
api.django.views.finops
api.django.views.l2_storage
api.django.views.l2_storage_config
api.django.views.l2_storage_drift
api.django.views.l2_storage_shadow_log
api.django.views.l2_storage_status
api.django.views.learning
api.django.views.rollback
api.django.views.xtest.circuit_breaker
api.django.views.xtest.error_budget
api.django.views.xtest.observability
api.django.views.xtest.snapshot
audit.api
audit.continuous_audit_api
config_tracker
context.actor_context
core.auto_rollback_guard
core.decision_engine
core.runtime_feedback
core.safety_bounds
resilience.bypass_hooks
services.backoff_calculator
services.blast_radius
services.blast_radius.models
services.chaos.experiments
services.chaos.scheduler_models
services.chaos_context
services.circuit_breaker.__init__
services.circuit_breaker.config
services.circuit_breaker.manual_control
services.circuit_breaker_service
services.compliance
services.compliance.models
services.config_history
services.corruption_shield.__init__
services.corruption_shield.config
services.corruption_shield.validators
services.error_budget.advisor
services.error_budget.calculator
services.error_budget.enums
services.error_budget.models
services.error_budget.reconciliation
services.error_budget.reconciliation.models
services.error_budget.reconciliation.period_tracker
services.error_budget.reconciliation.service
services.error_budget.reconciliation.shadow_calculator
services.error_budget.recorder
services.error_budget.service
services.error_budget_gate.__init__
services.error_budget_gate.alert_manager
services.error_budget_gate.config
services.error_budget_gate.exceptions
services.error_budget_gate.fault_detector
services.error_budget_gate.rate_limiter
services.finops
services.forensic_context
services.governance_service
services.idempotency_service
services.in_progress_tracker
services.learning
services.learning.models
services.notification
services.retry_handler
services.rollback
services.rollback.models
services.security_notification_service
services.throttle.__init__
services.throttle.adaptive
services.throttle.base
services.throttle.config
tasks.chaos_scheduler
tasks.config_apply
tasks.drift_detection
tasks.governance
utils.__init__
utils.async_logger
utils.time
```

---

## 🔗 연결 상태 요약

### ✅ 잘 연결된 컴포넌트

| 컴포넌트 | 연결 상태 |
|---------|----------|
| `factory` → `interfaces.*` | ✅ DI 패턴 |
| `services.*` → `interfaces.repositories` | ✅ 저장소 추상화 |
| `api.django.*` → `services.*` | ✅ View-Service 분리 |
| `audit.*` → `interfaces.audit_adapter` | ✅ 감사 추상화 |

### ⚠️ 연결 검토 필요

| 컴포넌트 | 상태 |
|---------|------|
| `api.django.tiering.*` | 미들웨어 미등록 |
| `api.django.throttle_adapter` | DRF에 미등록 |
| `api.django.reauthentication` | 사용처 없음 |
| `adapters.metrics.auto_tuning_adapter` | factory 미등록 |
| `services.error_budget_gate.*` | 게이트 활성화 필요 |

---

## 📋 권장 조치

### 1. 고아 모듈 정리
- 미사용 어댑터 → `__init__.py`에 export 추가 또는 삭제
- 테스트용 어댑터 → `tests/` 폴더로 이동

### 2. 미등록 미들웨어 활성화 검토
- `tiering.middleware` → 필요시 settings.py에 등록
- `throttle_adapter` → DRF throttle_classes에 등록

### 3. 순환 의존성 검사
- `import-linter` 설정 추가하여 레이어 규칙 강제

### 4. 문서 업데이트
- 고아 모듈 중 의도적인 것들 문서화
- 미등록 미들웨어 활성화 가이드 작성

---

## 🛠️ 분석 도구 설정

### vulture 설정 (.vulture_whitelist.py)
```python
# 의도적 미사용 코드 화이트리스트
adapters.django.admin  # Django auto-discovery
adapters.django.apps   # Django AppConfig
api.django.urls        # URL routing entry point
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

## 참고 문서

- [00_INDEX.md](middleware_system/00_INDEX.md) - 미들웨어 시스템 인덱스
- [CODE_ANALYSIS_TOOLS.md](../CODE_ANALYSIS_TOOLS.md) - 코드 분석 도구 가이드
