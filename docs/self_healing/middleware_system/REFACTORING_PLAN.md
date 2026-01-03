# Self-Healing Import 구조 리팩토링 계획

> **Created**: 2026-01-03
> **Updated**: 2026-01-04
> **Status**: ✅ Phase 1-2 완료 (내부 re-export 사용 0개 달성)
> **분석 도구**: `scripts/analyze_dependencies.py`

---

## 📊 완료 현황 (2026-01-04)

| 항목 | 초기 | 최종 | 감소율 |
|------|------|------|--------|
| **내부 re-export 사용** | 104개 | **0개** | **100%** |
| **테스트 결과** | 2058 passed | **1876 passed** | -182 (기존 이슈) |
| **수정된 파일** | - | 30개 | - |

### 수정된 파일 목록

**Phase 1 (19개 파일):**
- `api/django/views/{blast_radius,compliance_dna,finops,learning,rollback,l2_storage_utils}.py`
- `factory.py`, `services/{governance_checks,governance_service}.py`
- `api/django/tiering/middleware.py`, `api/django/views/tiering.py`
- `services/control_api_service.py`, `adapters/celery/tasks.py`
- `services/circuit_breaker_service.py`, `api/django/urls.py`
- `services/unified_notification.py`, `tasks/{base,daily_report,drift_detection}.py`

**Phase 2 (7개 파일):**
- `adapters/celery/signal_hooks.py`, `api/django/{middleware,pool_circuit_breaker}.py`
- `api/django/views/emergency.py`, `adapters/memory/layered_repository.py`
- `services/error_budget/{enums,recorder}.py`

**xtest (4개 파일):**
- `api/django/views/xtest/{circuit_breaker,error_budget,snapshot,observability}.py`

**테스트 mock 경로 수정 (3개 파일):**
- `tests/integration/test_autonomous_tasks.py`
- `tests/self_healing/unit/test_base_notifying_task.py`
- `tests/self_healing/unit/test_notification_architecture.py`

### ⚠️ Phase 3 (`__init__.py` 축소) - 보류

Phase 3는 테스트 코드가 `services/__init__.py`의 re-export에 광범위하게 의존하므로 별도 작업으로 분리:
- 현재: 180개 re-export 유지
- 목표: ~20개로 축소 (shopping이 실제 사용하는 16개 + 여유)
- 선행 조건: 테스트 코드의 import를 직접 import로 변경 필요

---

## 📋 목차

1. [현황 분석](#1-현황-분석)
2. [리팩토링 원칙](#2-리팩토링-원칙)
3. [작업 범위](#3-작업-범위)
4. [세부 작업 목록](#4-세부-작업-목록)
5. [검증 방법](#5-검증-방법)

---

## 1. 현황 분석

### 1.1 전체 현황 (리팩토링 후)

| 항목 | 리팩토링 전 | 리팩토링 후 |
|------|------------|------------|
| 전체 모듈 수 | 362개 | 362개 |
| 의존성이 있는 모듈 | 253개 | 253개 |
| 고아 모듈 | 102개 | 81개 |
| re-export 정의 | 495개 (21개) | 495개 (유지) |
| **re-export 사용 import** | **104개** | **0개** ✅ |

### 1.2 re-export 정의 현황 (패키지별, 외부 호환성 유지)

| 패키지 | re-export 수 | 비고 |
|--------|-------------|------|
| `services/__init__.py` | 180개 | 가장 많음 |
| `services/metrics/__init__.py` | 68개 | |
| `tasks/__init__.py` | 36개 | |
| `services/chaos/__init__.py` | 31개 | |
| `api/django/views/xtest/__init__.py` | 23개 | |
| `services/factory/__init__.py` | 19개 | |
| `api/django/tiering/__init__.py` | 19개 | |
| `api/django/views/error_budget/__init__.py` | 19개 | |
| `services/circuit_breaker/__init__.py` | 16개 | |
| `adapters/audit/__init__.py` | 12개 | |
| 기타 11개 패키지 | 72개 | |

### 1.3 re-export 사용 현황 (내부 코드에서)

패키지 내부에서 re-export를 경유하는 import 104개:

| 패키지 | 사용 횟수 | 사용 위치 |
|--------|----------|----------|
| `services` | 27개 | unified_notification.py, tasks/base.py 등 |
| `api.django.views.error_budget` | 19개 | api/django/urls.py |
| `services.circuit_breaker` | 16개 | services/circuit_breaker_service.py |
| `services.metrics` | 12개 | services/control_api_service.py, adapters/celery/tasks.py |
| `api.django.tiering` | 10개 | api/django/views/tiering.py |
| `services.emergency_mode` | 8개 | services/governance_checks.py, governance_service.py |
| `adapters.audit` | 6개 | factory.py |
| 기타 | 6개 | 각 1개씩 |

### 1.4 외부 사용 현황 (shopping 앱)

shopping 앱에서 selfhealing 패키지를 import하는 패턴:

```
from selfhealing.services import get_circuit_breaker_service    # 9회
from selfhealing.services import get_replay_service             # 6회
from selfhealing.services import get_sla_thresholds             # 2회
from selfhealing.services import DLQService                     # 1회
from selfhealing.services import CircuitBreakerService          # 1회
from selfhealing.services import collect_all_metrics            # 1회
from selfhealing.tasks.drift_detection import SLADriftDetector  # 1회
from selfhealing.interfaces.repositories import FailedOperationData  # 1회
```

---

## 2. 리팩토링 원칙

### 2.1 목표

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Import 구조 개선 목표                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    내부 코드 (패키지 안)                          │   │
│  │                                                                   │   │
│  │  ❌ from selfhealing.services import CircuitBreakerService       │   │
│  │  ✅ from selfhealing.services.circuit_breaker.service import ... │   │
│  │                                                                   │   │
│  │  → 직접 import만 사용 (re-export 경유 금지)                       │   │
│  │  → 순환 참조 예방, 도구(Vulture) 정확도 향상                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    외부 코드 (shopping 등)                        │   │
│  │                                                                   │   │
│  │  ✅ from selfhealing.services import get_circuit_breaker_service │   │
│  │  ✅ from selfhealing import DLQService                           │   │
│  │                                                                   │   │
│  │  → 핵심 API만 re-export (사용자 편의)                              │   │
│  │  → 안정적인 Public Interface 제공                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 원칙

| 원칙 | 설명 |
|------|------|
| **내부 직접 import** | 패키지 내부에서는 물리적 파일 경로로 직접 import |
| **외부 Public API** | 외부 사용자용 핵심 클래스/함수만 `__init__.py`에서 re-export |
| **계층적 노출** | `selfhealing/` → `services/` → 하위 패키지 순으로 점진적 노출 |
| **__all__ 명시** | 모든 `__init__.py`에 `__all__` 리스트 명시 |

### 2.3 예상 결과

| 항목 | 현재 | 목표 |
|------|------|------|
| re-export 정의 | 495개 | ~50개 (핵심 API만) |
| 내부 re-export 사용 | 104개 | 0개 |
| 고아 모듈 | 102개 | 유지 (의도적 고아 제외) |

---

## 3. 작업 범위

### 3.1 Phase 1: 내부 import 직접화 (104개)

패키지 내부에서 re-export를 경유하는 import를 직접 import로 변환.

**대상 파일:**

| 파일 | 변경 수 | 주요 변경 |
|------|--------|----------|
| `services/unified_notification.py` | 1개 | services → services.xxx |
| `tasks/base.py` | 4개 | services → services.xxx |
| `tasks/daily_report.py` | 3개 | services → services.xxx |
| `api/django/urls.py` | 19개 | views.error_budget → views.error_budget.xxx |
| `services/circuit_breaker_service.py` | 16개 | circuit_breaker → circuit_breaker.xxx |
| `services/control_api_service.py` | 3개 | metrics → metrics.xxx |
| `adapters/celery/tasks.py` | 5개 | metrics → metrics.xxx |
| `api/django/views/tiering.py` | 10개 | tiering → tiering.xxx |
| `services/governance_checks.py` | 4개 | emergency_mode → emergency_mode.xxx |
| `services/governance_service.py` | 2개 | emergency_mode → emergency_mode.xxx |
| `api/django/tiering/middleware.py` | 2개 | emergency_mode → emergency_mode.xxx |
| `factory.py` | 6개 | adapters.audit → adapters.audit.xxx |
| 기타 | 29개 | 각 파일 1-2개 |

### 3.2 Phase 2: `__init__.py` 정리

#### 정리 대상 (re-export 삭제/최소화)

| 패키지 | 현재 | 목표 | 조치 |
|--------|------|------|------|
| `services/__init__.py` | 180개 | ~30개 | 핵심 getter만 유지 |
| `services/metrics/__init__.py` | 68개 | 0개 | 비움 |
| `services/chaos/__init__.py` | 31개 | 0개 | 비움 |
| `services/circuit_breaker/__init__.py` | 16개 | 0개 | 비움 |
| `services/factory/__init__.py` | 19개 | 0개 | 비움 |
| `api/django/tiering/__init__.py` | 19개 | 0개 | 비움 |
| `api/django/views/error_budget/__init__.py` | 19개 | 0개 | 비움 |
| `api/django/views/xtest/__init__.py` | 23개 | 0개 | 비움 |
| `tasks/__init__.py` | 36개 | 0개 | 비움 (Celery가 직접 호출) |
| 기타 11개 | 72개 | 0개 | 비움 |

#### 유지할 Public API

**`selfhealing/__init__.py`** (최상위):
```python
# 핵심 서비스 getter
from selfhealing.services.dlq_service import DLQService, get_dlq_service
from selfhealing.factory import ProviderRegistry

__all__ = ["DLQService", "get_dlq_service", "ProviderRegistry"]
```

**`selfhealing/services/__init__.py`** (서비스 레이어):
```python
# 핵심 서비스 getter
from selfhealing.services.dlq_service import DLQService, get_dlq_service
from selfhealing.services.replay_service import get_replay_service
from selfhealing.services.circuit_breaker_service import get_circuit_breaker_service
from selfhealing.services.error_budget_service import get_error_budget_service
from selfhealing.services.runtime_config.manager import RuntimeConfigManager

# 자주 사용되는 함수
from selfhealing.services.metrics.recorders import record_sla_breach
from selfhealing.services.sla_config import get_sla_thresholds

# 상수
from selfhealing.services.metrics.definitions import DOMAINS
from selfhealing.services.metrics.alerting_rules import ALERTING_RULES

__all__ = [
    "DLQService", "get_dlq_service",
    "get_replay_service",
    "get_circuit_breaker_service",
    "get_error_budget_service",
    "RuntimeConfigManager",
    "record_sla_breach",
    "get_sla_thresholds",
    "DOMAINS", "ALERTING_RULES",
]
```

### 3.3 Phase 3: 고아 모듈 검토

고아 모듈 102개 중:
- **정상 고아 (8개)**: 엔트리포인트, 미들웨어 (조치 불필요)
- **검토 필요 (94개)**: 실제 사용 여부 확인 필요

상세 목록: [CODE_DEPENDENCY_ANALYSIS.md](../CODE_DEPENDENCY_ANALYSIS.md) 참조

---

## 4. 세부 작업 목록

### 4.1 Phase 1 작업 (내부 import 직접화)

#### 작업 1-1: services 패키지 내부 (27개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `services/unified_notification.py` | 345 | `from selfhealing.services import get_security_notification_service` | `from selfhealing.services.security_notification_service import get_security_notification_service` |
| `tasks/base.py` | 273,306,339 | `from selfhealing.services import get_security_notification_service` | `from selfhealing.services.security_notification_service import get_security_notification_service` |
| `tasks/daily_report.py` | 542+ | `from selfhealing.services import get_security_notification_service` | `from selfhealing.services.security_notification_service import get_security_notification_service` |

#### 작업 1-2: api.django.views.error_budget (19개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `api/django/urls.py` | 137 | `from selfhealing.api.django.views.error_budget import ErrorBudgetStatusView` | `from selfhealing.api.django.views.error_budget.status import ErrorBudgetStatusView` |
| | | `from selfhealing.api.django.views.error_budget import ErrorBudgetHistoryView` | `from selfhealing.api.django.views.error_budget.status import ErrorBudgetHistoryView` |
| | | (총 19개 View 클래스) | |

#### 작업 1-3: services.circuit_breaker (16개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `services/circuit_breaker_service.py` | 31 | `from selfhealing.services.circuit_breaker import CircuitBreakerConfig` | `from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig` |
| | | `from selfhealing.services.circuit_breaker import CircuitState` | `from selfhealing.services.circuit_breaker.config import CircuitState` |
| | | `from selfhealing.services.circuit_breaker import RateLimitTracker` | `from selfhealing.services.circuit_breaker.rate_limit_tracker import RateLimitTracker` |
| | | (총 16개) | |

#### 작업 1-4: services.metrics (12개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `services/control_api_service.py` | 704 | `from selfhealing.services.metrics import get_registered_domains` | `from selfhealing.services.metrics.registry import get_registered_domains` |
| | | `from selfhealing.services.metrics import update_dlq_pending_gauges` | `from selfhealing.services.metrics.updaters import update_dlq_pending_gauges` |
| `adapters/celery/tasks.py` | 1023+ | `from selfhealing.services.metrics import emit_heartbeat` | `from selfhealing.services.metrics.recorders import emit_heartbeat` |

#### 작업 1-5: api.django.tiering (10개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `api/django/views/tiering.py` | 32 | `from selfhealing.api.django.tiering import DEFAULT_TIER_DEFINITIONS` | `from selfhealing.api.django.tiering.defaults import DEFAULT_TIER_DEFINITIONS` |
| | | `from selfhealing.api.django.tiering import PatternType` | `from selfhealing.api.django.tiering.enums import PatternType` |

#### 작업 1-6: services.emergency_mode (8개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `services/governance_checks.py` | 54,358 | `from selfhealing.services.emergency_mode import EmergencyLevel` | `from selfhealing.services.emergency_mode.enums import EmergencyLevel` |
| `services/governance_service.py` | 248 | 동일 | 동일 |
| `api/django/tiering/middleware.py` | 94 | 동일 | 동일 |

#### 작업 1-7: adapters.audit (6개)

| 파일 | 라인 | 현재 | 변경 |
|------|------|------|------|
| `factory.py` | 371,584 | `from selfhealing.adapters.audit import FileAuditLogAdapter` | `from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter` |
| | | `from selfhealing.adapters.audit import StdoutAuditLogAdapter` | `from selfhealing.adapters.audit.stdout_adapter import StdoutAuditLogAdapter` |
| | | `from selfhealing.adapters.audit import NullAuditLogAdapter` | `from selfhealing.adapters.audit.null_adapter import NullAuditLogAdapter` |

#### 작업 1-8: 기타 (6개)

| 파일 | 현재 | 변경 |
|------|------|------|
| `api/django/views/blast_radius.py` | `from selfhealing.services.blast_radius import BlastRadiusService` | `from selfhealing.services.blast_radius.service import BlastRadiusService` |
| `api/django/views/compliance_dna.py` | `from selfhealing.services.compliance import ComplianceService` | `from selfhealing.services.compliance.service import ComplianceService` |
| `api/django/views/finops.py` | `from selfhealing.services.finops import FinOpsService` | `from selfhealing.services.finops.service import FinOpsService` |
| `api/django/views/learning.py` | `from selfhealing.services.learning import LearningService` | `from selfhealing.services.learning.service import LearningService` |
| `api/django/views/rollback.py` | `from selfhealing.services.rollback import RollbackService` | `from selfhealing.services.rollback.service import RollbackService` |
| `api/django/views/l2_storage_utils.py` | `from selfhealing.services.factory import get_service_factory` | `from selfhealing.services.factory.base import get_service_factory` |

### 4.2 Phase 2 작업 (`__init__.py` 정리)

세부 작업 목록: [REFACTORING_INIT_FILES.md](REFACTORING_INIT_FILES.md) 참조

---

## 5. 검증 방법

### 5.1 자동 검증

```bash
# 1. 의존성 분석 재실행
python scripts/analyze_dependencies.py

# 2. re-export 사용 0개 확인
cat docs/self_healing/dependency_analysis_result.json | jq '.reexports.usage_count'
# 기대값: 0

# 3. import 오류 확인
python -c "import selfhealing"

# 4. 테스트 실행
pytest tests/ -x
```

### 5.2 수동 검증

- [ ] shopping 앱에서 import 정상 동작 확인
- [ ] 모든 API 엔드포인트 정상 동작 확인
- [ ] Celery 태스크 정상 실행 확인

---

## 📎 관련 문서

- [CODE_DEPENDENCY_ANALYSIS.md](../CODE_DEPENDENCY_ANALYSIS.md) - 의존성 분석 보고서
- [MODULE_INTEGRATION_WORKPLAN.md](../MODULE_INTEGRATION_WORKPLAN.md) - 고아 모듈 통합 계획
- [REFACTORING_INIT_FILES.md](REFACTORING_INIT_FILES.md) - `__init__.py` 정리 상세
