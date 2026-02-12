# 223. Host App Decoupling Plan — selfhealing 패키지의 독립 설치 지원

> **목표**: `pip install selfhealing-python[django]` + `INSTALLED_APPS` 추가만으로 동작하도록 하여,
> 새 시스템(billing, logistics 등)에 19+ 파일 복사 없이 연동 가능하게 한다.

> **구현 상태**: ✅ **완료** (Phase 1–4 전체 구현, Phase 5 테스트 통과 — 11320 passed)

### 구현 이력

| Phase | 상태 | 내용 |
|-------|------|------|
| Phase 1 | ✅ 완료 | Abstract 2개 + Concrete 4개 모델 추가 (models.py), 0002 migration (SeparateDatabaseAndState), Admin 자동 등록 |
| Phase 2 | ✅ 완료 | management/commands/ 4개 커맨드 패키지로 이동 |
| Phase 3 | ✅ 완료 | apps.py ready()에 _autodiscover_celery_tasks() 추가 |
| Phase 4 | ✅ 완료 | shopping에서 15개 파일 삭제, __init__.py 정리, 0033 migration (모델 소유권 이전) |
| Phase 5 | ✅ 완료 | packages/selfhealing-python 전체 테스트: 11320 passed, 2 failed (기존 버그, 무관), 1 skipped |

---

## 1. 문제 정의

### 1.1 현재 상태: shopping 앱에 심어진 selfhealing 전용 파일

| # | 파일 경로 | 역할 | `from selfhealing.*` import 근거 |
|---|----------|------|--------------------------------|
| 1 | `shopping/models/failed_operation.py` | DLQ 모델 (concrete) | `from selfhealing.services import get_sla_thresholds` (L458), `from selfhealing.core.cluster_identity import get_cluster_identity` (L512) |
| 2 | `shopping/models/failed_external_request.py` | DLQ 모델 (concrete) | `settings.SELF_HEALING` 설정 사용 (L300-303) |
| 3 | `shopping/models/security_incident.py` | 보안 인시던트 모델 | Reference: `docs/L3_SELF_HEALING_OPERATIONS.md` |
| 4 | `shopping/models/postmortem_record.py` | Postmortem 모델 | `from selfhealing.adapters.django.models import AbstractPostmortemRecord` (L10) |
| 5 | `shopping/admin/dlq_admin.py` | DLQ Admin | `from selfhealing.adapters.django.admin import BaseDLQEntryAdmin` (L11) |
| 6 | `shopping/admin/postmortem_admin.py` | Postmortem Admin | `from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin` (L10) |
| 7 | `shopping/serializers/self_healing_serializers.py` | Control API 시리얼라이저 | 순수 self-healing API 전용. 쇼핑 import 없음 |
| 8 | `shopping/handlers/replay_handlers.py` | 도메인 Replay 핸들러 | `from selfhealing.services.replay_service import ReplayHandler, ReplayResult, register_replay_handler` (L18-22) |
| 9 | `shopping/tasks/self_healing_tasks.py` | DEPRECATED re-export | `from selfhealing.celery_tasks import ...` (L31) — 16개 태스크 전부 re-export만 |
| 10 | `shopping/tasks/drift_detection_tasks.py` | DEPRECATED re-export | `from selfhealing.celery_tasks import check_sla_drift, ...` (L27) |
| 11 | `shopping/tasks/dlq_replay_tasks.py` | DEPRECATED re-export | `from selfhealing.celery_tasks import ...` (L30) |
| 12 | `shopping/management/commands/selfhealing_config.py` | 설정 CLI | `from selfhealing.settings import get_config` (L84) 외 6개 selfhealing import |
| 13 | `shopping/management/commands/check_selfhealing_config.py` | Pre-flight 검증 | `from selfhealing.core.safe_defaults import validate_config_preflight, ...` (L54) |
| 14 | `shopping/management/commands/generate_self_healing_alerts.py` | Alert 생성 | `from selfhealing.services import ALERTING_RULES` (L23), `from selfhealing.services.metrics.registry import DEFAULT_DOMAINS` (L24) |
| 15 | `shopping/management/commands/security_review.py` | 보안 리뷰 | `from selfhealing.services import SecurityViolationService, CircuitBreakerService, DLQService` (L48, L127, L234, L250, L319) |
| 16 | `shopping/chaos/__init__.py` | Chaos 패키지 | 쇼핑 전용 chaos injection 인프라 |
| 17 | `shopping/chaos/config.py` | Chaos 설정 | 환경변수 기반 chaos 설정 (ChaosConfig dataclass) |
| 18 | `shopping/chaos/breakpoints.py` | Chaos 브레이크포인트 | 12개 breakpoint 상수 정의 |
| 19 | `shopping/chaos/decorators.py` | Chaos 데코레이터 | `chaos_breakpoint()`, `with_chaos()` 등 |
| 20 | `shopping/tests/unit/models/test_self_healing_models.py` | 모델 테스트 | `FailedOperation`, `SecurityIncident` 테스트 |
| 21 | `shopping/tests/load/__init__.py` | Load 테스트 패키지 | "Load & Stress Tests for Self-Healing System" |
| 22 | `shopping/tests/selfhealing/` | 테스트 디렉토리 | selfhealing 전용 테스트 |

### 1.2 문제 요약

다른 시스템(예: `billing`)에 selfhealing을 연동하려면 **위 22개 항목을 모두 복사/재작성**해야 한다.
이는 selfhealing이 독립 패키지로서 실패한 것이며, 사실상 shopping에 하드코딩된 상태다.

### 1.3 패키지 측 현재 제공 현황

| 항목 | 패키지 제공 여부 | 위치 | 비고 |
|------|-----------------|------|------|
| Abstract 모델 | ✅ 3개 | `selfhealing.adapters.django.models` | `AbstractFailedOperation`, `AbstractAuditLog`, `AbstractPostmortemRecord` |
| Django AppConfig | ✅ | `selfhealing.adapters.django.apps.SelfHealingConfig` | `label="selfhealing"` |
| Migration | ✅ 부분 | `selfhealing.adapters.django.migrations.0001_initial` | `PostmortemRecord`, `CascadeEventArchive`만 concrete 생성 |
| Base Admin | ✅ 3개 | `selfhealing.adapters.django.admin` | `BaseDLQEntryAdmin`, `BasePostmortemRecordAdmin`, `BaseCircuitBreakerStateAdmin` |
| API Serializer | ✅ | `selfhealing.api.django.serializers.control` | `ControlRequestSerializer`, `ControlResponseSerializer` 등 이미 존재 |
| API URL | ✅ | `selfhealing.api.django.urls` | `shopping/urls.py`에서 include 중 |
| Celery Tasks | ✅ 16개 | `selfhealing.celery_tasks` | 5개 모듈 (chaos, circuit_breaker, dlq, drift_detection, metrics) |
| Management Commands | ❌ | 없음 | `selfhealing/adapters/django/management/commands/` 디렉토리 자체 없음 |
| Concrete DLQ 모델 | ❌ | 없음 | `AbstractFailedOperation`만 있고 concrete 없음 |
| SecurityIncident | ❌ | 없음 | abstract도 concrete도 없음 |
| FailedExternalRequest | ❌ | 없음 | abstract도 concrete도 없음 |
| Chaos 모듈 | ❌ | 없음 | 패키지에 자체 chaos 서비스가 있으나 breakpoint/decorator는 없음 |

---

## 2. 이동 판정: 코드 근거 기반

### 2.1 패키지로 이동해야 하는 파일 (쇼핑 비즈니스 로직 없음)

#### 2.1.1 모델 4개 → Concrete 모델로 패키지 migration에 추가

**`shopping/models/failed_operation.py`** (536줄)
- 패키지에 `AbstractFailedOperation`이 이미 동일 필드로 존재 (models.py L55-504)
- shopping 버전이 추가한 것: `Domain` choices (payment/point/inventory/webhook/notification), `user` FK, `order` FK (0027에서 제거됨)
- `user` FK는 `settings.AUTH_USER_MODEL`로 swappable하게 패키지에서 제공 가능
- `Domain` choices는 호스트앱이 오버라이드하는 것이 아닌 범용 값 → 패키지 기본값으로 충분
- migration: `0024_add_failed_operation_and_security_incident`에서 `failed_operations` 테이블 생성

**`shopping/models/failed_external_request.py`** (332줄)
- 패키지에 대응하는 abstract 없음 → **abstract + concrete 모두 패키지에 신규 추가** 필요
- 문서에 "도메인 중립적 설계"라 명시 (L7)
- `from selfhealing.*` import 직접 없으나 `settings.SELF_HEALING` 사용 (L300-303)
- migration: `0028_rename_failedpayment_to_failedexternalrequest`

**`shopping/models/security_incident.py`** (380줄)
- 패키지에 abstract/concrete 모두 없음 → **신규 추가** 필요
- 쇼핑 비즈니스와 무관한 인프라 보안 모델 (IncidentType: webhook_signature, payment_tampered, token_forged 등)
- migration: `0024_add_failed_operation_and_security_incident`에서 `security_incidents` 테이블 생성

**`shopping/models/postmortem_record.py`** (31줄)
- 패키지 migration `0001_initial`에 이미 concrete `PostmortemRecord` (`selfhealing_postmortem` 테이블) 존재
- shopping 버전은 `AbstractPostmortemRecord`를 상속하여 동일 테이블명으로 재생성
- **중복 제거** — 패키지 migration의 concrete를 그대로 사용

#### 2.1.2 Admin 2개

**`shopping/admin/dlq_admin.py`** (32줄)
```python
from selfhealing.adapters.django.admin import BaseDLQEntryAdmin
@admin.register(FailedOperation)
class FailedOperationAdmin(BaseDLQEntryAdmin):
    def get_user_admin_url(self, user):
        return reverse("admin:shopping_user_change", args=[user.id])
```
- 모델이 패키지로 이동하면 admin도 자동 등록 가능
- `get_user_admin_url`은 `settings.AUTH_USER_MODEL`의 admin URL로 일반화 가능

**`shopping/admin/postmortem_admin.py`** (29줄)
```python
from selfhealing.adapters.django.admin import BasePostmortemRecordAdmin
@admin.register(PostmortemRecord)
class PostmortemRecordAdmin(BasePostmortemRecordAdmin):
    pass
```
- 순수 상속, 추가 로직 없음 → 패키지에서 자동 등록

#### 2.1.3 Serializer 1개

**`shopping/serializers/self_healing_serializers.py`** (318줄)
- 패키지에 이미 `selfhealing/api/django/serializers/control.py` (352줄)가 존재
- 내용 비교 결과: **동일한 시리얼라이저 구조** (ControlRequestSerializer, ControlResponseSerializer 등)
- shopping 버전은 **중복 코드** — 패키지 버전 사용으로 통일

#### 2.1.4 DEPRECATED Tasks 3개

**`shopping/tasks/self_healing_tasks.py`** (82줄)
```python
# DEPRECATED - v3.0.0에서 제거 예정
warnings.warn("Import directly from 'selfhealing.celery_tasks' instead.")
from selfhealing.celery_tasks import (
    check_circuit_breaker_recovery, expire_manual_overrides, ...
)
```

**`shopping/tasks/drift_detection_tasks.py`** (38줄) — 동일 패턴

**`shopping/tasks/dlq_replay_tasks.py`** (53줄) — 동일 패턴

세 파일 모두 `selfhealing.celery_tasks`의 re-export만 수행. 패키지가 Celery autodiscover를 지원하면 불필요.

#### 2.1.5 Management Commands 4개

패키지에 `management/commands/` 디렉토리 자체가 없음. 4개 모두 이동 대상:

| 파일 | 줄 수 | 핵심 import |
|------|-------|-------------|
| `selfhealing_config.py` | 350 | `from selfhealing.settings import get_config, CircuitBreakerSettings, ...` |
| `check_selfhealing_config.py` | 200 | `from selfhealing.core.safe_defaults import validate_config_preflight, ...` |
| `generate_self_healing_alerts.py` | 176 | `from selfhealing.services import ALERTING_RULES` |
| `security_review.py` | 499 | `from selfhealing.services import SecurityViolationService, CircuitBreakerService, DLQService` |

모두 `selfhealing.*`만 import하며 shopping 비즈니스 코드 없음.

#### 2.1.6 Chaos 모듈 4개

**판정: 쇼핑 전용으로 호스트에 남겨야 함**

- `breakpoints.py`: `BREAKPOINT_PAYMENT_CONFIRM_PRE_DB`, `BREAKPOINT_BP21_ORPHAN_PG` 등 **쇼핑 결제 플로우 전용** breakpoint
- `decorators.py`: 위 breakpoint를 사용하는 데코레이터
- `config.py`: `CHAOS_PAYMENT_CONFIRM_DELAY` 등 쇼핑 전용 환경변수
- selfhealing 패키지에 자체 chaos 서비스(`selfhealing/services/chaos/`)가 별도로 존재

→ 쇼핑 chaos는 "쇼핑 시스템의 chaos 테스트"이므로 호스트에 남는 것이 맞음.

### 2.2 호스트 앱에 남아야 하는 파일

| 파일 | 이유 |
|------|------|
| `shopping/handlers/replay_handlers.py` | 쇼핑 도메인 replay 로직 포함 (PaymentReplayHandler → payment_recovery_service 호출, PointReplayHandler → point_service 호출) |
| `shopping/chaos/*` (4파일) | 쇼핑 결제/주문 플로우 전용 breakpoint |
| `shopping/apps.py` | shopping AppConfig (`_configure_selfhealing` 메서드 — 현재 빈 pass) |
| `shopping/urls.py` | URL 라우팅 파일. `path("self-healing/", include("selfhealing.api.django.urls"))` 한 줄만 남김 |

### 2.3 혼합 파일에서 selfhealing 부분 제거

| 파일 | 변경 내용 |
|------|----------|
| `shopping/models/__init__.py` | `FailedOperation`, `FailedExternalRequest`, `SecurityIncident`, `PostmortemRecord` import 제거 |
| `shopping/admin/__init__.py` | `FailedOperationAdmin`, `PostmortemRecordAdmin` import 제거 |
| `shopping/tasks/__init__.py` | `from selfhealing.celery_tasks import ...` (12개 re-import) 제거 |

---

## 3. 이동 계획

### Phase 1: 패키지에 Concrete 모델 및 Migration 추가

#### 3.1.1 모델 추가 (패키지 `adapters/django/models.py`)

| 모델 | 처리 |
|------|------|
| `FailedOperation` | `AbstractFailedOperation`을 상속하는 concrete 모델 추가. `user` FK는 `settings.AUTH_USER_MODEL` swappable. `Domain` choices 기본 제공 |
| `FailedExternalRequest` | `AbstractFailedExternalRequest` (신규 abstract) + concrete 모델 추가 |
| `SecurityIncident` | `AbstractSecurityIncident` (신규 abstract) + concrete 모델 추가 |
| `PostmortemRecord` | 이미 패키지 migration에 존재. shopping 중복 제거만 |

#### 3.1.2 Migration 추가 (`adapters/django/migrations/0002_*.py`)

```
0002_add_dlq_and_security_models.py
  - CreateModel: FailedOperation (db_table="failed_operations")
  - CreateModel: FailedExternalRequest (db_table="selfhealing_failed_external_request")
  - CreateModel: SecurityIncident (db_table="security_incidents")
```

**주의**: shopping migration과의 충돌 방지
- shopping 마이그레이션 히스토리에서 해당 테이블이 이미 존재
- `SeparateDatabaseAndState` 또는 `--fake` 전략 필요
- 상세 마이그레이션 전략은 §4 참조

### Phase 2: Management Commands 이동

```
패키지 디렉토리 생성:
  selfhealing/adapters/django/management/__init__.py
  selfhealing/adapters/django/management/commands/__init__.py
  selfhealing/adapters/django/management/commands/selfhealing_config.py
  selfhealing/adapters/django/management/commands/check_selfhealing_config.py
  selfhealing/adapters/django/management/commands/generate_self_healing_alerts.py
  selfhealing/adapters/django/management/commands/security_review.py
```

Django는 `INSTALLED_APPS`에 등록된 앱의 `management/commands/`를 자동 발견하므로,
`selfhealing.adapters.django`가 `INSTALLED_APPS`에 있으면 자동으로 사용 가능.

### Phase 3: Celery Autodiscover 설정

현재 `shopping/tasks/__init__.py`에서 12개 태스크를 re-import하고 있음.
패키지 측에서 Celery autodiscover를 지원하면 이 re-import가 불필요해짐.

```python
# selfhealing/adapters/django/apps.py ready()에 추가
def ready(self):
    ...
    # Celery autodiscover: selfhealing.celery_tasks 등록
    try:
        from celery import current_app
        current_app.autodiscover_tasks(['selfhealing.celery_tasks'])
    except ImportError:
        pass
```

또는 `celery.py` 설정에서:
```python
app.autodiscover_tasks(['selfhealing.celery_tasks'])
```

### Phase 4: shopping에서 제거

#### 4.1 파일 삭제

```
삭제 대상:
  shopping/models/failed_operation.py
  shopping/models/failed_external_request.py
  shopping/models/security_incident.py
  shopping/models/postmortem_record.py
  shopping/admin/dlq_admin.py
  shopping/admin/postmortem_admin.py
  shopping/serializers/self_healing_serializers.py
  shopping/tasks/self_healing_tasks.py
  shopping/tasks/drift_detection_tasks.py
  shopping/tasks/dlq_replay_tasks.py
  shopping/management/commands/selfhealing_config.py
  shopping/management/commands/check_selfhealing_config.py
  shopping/management/commands/generate_self_healing_alerts.py
  shopping/management/commands/security_review.py
  shopping/tests/unit/models/test_self_healing_models.py
  shopping/tests/load/__init__.py (selfhealing 전용 내용 제거)
  shopping/tests/selfhealing/ (디렉토리 삭제)
```

#### 4.2 수정 대상

**`shopping/models/__init__.py`** — 제거할 import:
```python
# 제거
from .failed_external_request import FailedExternalRequest
from .failed_operation import FailedOperation
from .postmortem_record import PostmortemRecord
from .security_incident import SecurityIncident

# __all__에서도 제거
"FailedExternalRequest", "FailedOperation", "SecurityIncident", "PostmortemRecord"
```

**`shopping/admin/__init__.py`** — 제거할 import:
```python
# 제거
from .dlq_admin import FailedOperationAdmin
from .postmortem_admin import PostmortemRecordAdmin

# __all__에서도 제거
"FailedOperationAdmin", "PostmortemRecordAdmin"
```

**`shopping/tasks/__init__.py`** — 제거할 import:
```python
# 제거: selfhealing.celery_tasks에서 직접 re-import하는 부분 전체
from selfhealing.celery_tasks import (
    cleanup_resolved_dlq_entries, replay_batch_by_domain, ...
)
from selfhealing.celery_tasks import (
    check_and_report_sla_breaches, check_circuit_breaker_recovery, ...
)

# __all__에서 selfhealing 태스크 항목 제거
```

**`shopping/apps.py`** — `_configure_selfhealing()` 메서드 제거 (현재 pass만 수행):
```python
class ShoppingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shopping"

    def ready(self):
        import shopping.signals  # noqa
        # selfhealing 설정은 selfhealing.adapters.django.apps.SelfHealingConfig.ready()에서 처리
```

---

## 4. 마이그레이션 전략 (중복 테이블 충돌 방지)

### 4.1 현재 마이그레이션 상태

| 테이블 | 생성 위치 | 현재 상태 |
|--------|----------|----------|
| `failed_operations` | `shopping/0024` | ✅ 활성 |
| `security_incidents` | `shopping/0024` | ✅ 활성 |
| `shopping_failed_external_request` | `shopping/0028` (리네임) | ✅ 활성 |
| `selfhealing_postmortem` | `selfhealing/0001_initial` | ✅ 활성 |
| `selfhealing_ratelimitstate` | `shopping/0026` → `shopping/0032` 삭제 | ❌ 삭제됨 |
| `selfhealing_cascade_events` | `selfhealing/0001_initial` | ✅ 활성 |

### 4.2 전략: `SeparateDatabaseAndState`

기존 DB에 테이블이 이미 존재하므로, 패키지 migration에서 `SeparateDatabaseAndState` 사용:

```python
# selfhealing/adapters/django/migrations/0002_add_dlq_and_security_models.py

class Migration(migrations.Migration):
    dependencies = [
        ("selfhealing", "0001_initial"),
    ]

    operations = [
        # DB에는 이미 테이블 존재 → state만 등록
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="FailedOperation",
                    fields=[...],
                    options={"db_table": "failed_operations"},
                ),
            ],
            database_operations=[],  # 테이블은 이미 존재
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="SecurityIncident",
                    fields=[...],
                    options={"db_table": "security_incidents"},
                ),
            ],
            database_operations=[],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="FailedExternalRequest",
                    fields=[...],
                    options={"db_table": "selfhealing_failed_external_request"},
                ),
            ],
            database_operations=[],
        ),
    ]
```

동시에 shopping에 대응하는 "모델 소유권 이전" migration 추가:

```python
# shopping/migrations/0033_transfer_models_to_selfhealing.py

class Migration(migrations.Migration):
    dependencies = [
        ("shopping", "0032_remove_legacy_models"),
        ("selfhealing", "0002_add_dlq_and_security_models"),
    ]

    operations = [
        # shopping 앱에서 모델 state 제거 (DB 변경 없음)
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="FailedOperation"),
                migrations.DeleteModel(name="FailedExternalRequest"),
                migrations.DeleteModel(name="SecurityIncident"),
                migrations.DeleteModel(name="PostmortemRecord"),
            ],
            database_operations=[],
        ),
    ]
```

### 4.3 신규 시스템용 (빈 DB)

신규 시스템은 패키지 migration만 실행하면 모든 테이블이 자동 생성됨:
```bash
python manage.py migrate selfhealing
```

---

## 5. 최종 연동 인터페이스 (목표 상태)

### 5.1 새 시스템 설치 가이드

```python
# 1. 설치
# pip install selfhealing-python[django]

# 2. settings.py
INSTALLED_APPS = [
    ...
    "selfhealing.adapters.django",  # 모델/admin/commands/tasks 전부 제공
    "billing",                       # 호스트 앱
]

SELFHEALING = {
    "DLQ_RETENTION_DAYS": 30,
    "CIRCUIT_BREAKER_ENABLED": True,
    # ... (selfhealing/settings/ Pydantic 모델로 검증)
}

# 3. urls.py
urlpatterns = [
    path("self-healing/", include("selfhealing.api.django.urls", namespace="selfhealing")),
]

# 4. celery.py
app.autodiscover_tasks(["selfhealing.celery_tasks"])
```

### 5.2 호스트 앱에서 작성해야 하는 코드 (1파일)

```python
# billing/handlers/replay_handlers.py
from selfhealing.services.replay_service import (
    ReplayHandler, ReplayResult, register_replay_handler,
)

class BillingReplayHandler(ReplayHandler):
    @property
    def domain(self) -> str:
        return "billing"

    def can_replay(self, failed_op) -> tuple[bool, str]:
        # 비즈니스 도메인 전용 replay 가능 여부 판단
        ...

    def replay(self, failed_op) -> ReplayResult:
        # 비즈니스 도메인 전용 replay 실행
        ...

def register_billing_handlers():
    register_replay_handler(BillingReplayHandler())
```

```python
# billing/apps.py
class BillingConfig(AppConfig):
    name = "billing"

    def ready(self):
        from billing.handlers.replay_handlers import register_billing_handlers
        register_billing_handlers()
```

### 5.3 비교: 현재 vs 목표

|  | 현재 (shopping) | 목표 (new system) |
|--|----------------|-------------------|
| 파일 복사 | 19+ 파일 | 0 파일 |
| 작성 필요 | 없음 (이미 복사됨) | replay handler 1파일 |
| `pip install` | 수동 의존성 | `selfhealing-python[django]` |
| migration | shopping 앱에 분산 | `python manage.py migrate selfhealing` |
| management commands | shopping에 4개 복사 | 자동 발견 (`INSTALLED_APPS`) |
| Celery tasks | shopping `__init__.py`에 re-import | `autodiscover_tasks` |
| admin | shopping admin에 등록 | 패키지 자동 등록 |

---

## 6. 작업 순서 및 의존성

```
Phase 1: 패키지 모델/migration 추가
  ├─ 1a: AbstractFailedExternalRequest, AbstractSecurityIncident 추가 (models.py)
  ├─ 1b: Concrete 모델 3개 추가 (FailedOperation, FailedExternalRequest, SecurityIncident)
  ├─ 1c: 0002 migration (SeparateDatabaseAndState)
  └─ 1d: Admin 자동 등록 (adapters/django/admin.py에 concrete 모델 register)

Phase 2: 패키지 management commands 추가
  ├─ 2a: management/commands/ 디렉토리 생성
  └─ 2b: 4개 command 이동 (selfhealing_config, check_selfhealing_config, generate_self_healing_alerts, security_review)

Phase 3: Celery autodiscover 지원
  └─ 3a: apps.py ready()에 autodiscover_tasks 추가 또는 문서화

Phase 4: shopping 정리
  ├─ 4a: 0033_transfer_models_to_selfhealing migration 추가
  ├─ 4b: 모델/admin/serializer/tasks 파일 삭제
  ├─ 4c: __init__.py 파일들에서 import 정리
  └─ 4d: 테스트 이동/삭제

Phase 5: 검증
  ├─ 5a: python manage.py migrate --plan
  ├─ 5b: python manage.py check
  ├─ 5c: 전체 테스트 실행
  └─ 5d: 신규 시스템 설치 시뮬레이션
```

---

## 7. Chaos 모듈 판정 근거

`shopping/chaos/` 4개 파일은 이동 대상에서 **제외**한다.

**근거**:
- `breakpoints.py`의 상수들이 쇼핑 도메인 전용:
  - `BREAKPOINT_PAYMENT_CONFIRM_PRE_DB` — "결제 확인 DB 커밋 전"
  - `BREAKPOINT_CANCEL_RACE_WINDOW` — "취소 경쟁 조건 주입"
  - `BREAKPOINT_BP21_ORPHAN_PG` — "PG 성공 후 내부 DB 실패"
  - `BREAKPOINT_BP29_POINT_ORPHAN` — "포인트 적립 고아 상태"
- `config.py`의 환경변수가 쇼핑 전용: `CHAOS_PAYMENT_CONFIRM_DELAY`, `PHASE2_ORPHAN_PG` 등
- selfhealing 패키지에 자체 chaos 서비스(`selfhealing/services/chaos/`)가 별도 존재
- chaos breakpoint는 시스템마다 도메인 특화 정의가 필요 → 호스트 앱에 있는 것이 맞음

---

## 8. 위험 요소 및 완화 방안

| 위험 | 영향 | 완화 |
|------|------|------|
| Migration 충돌 (같은 테이블 두 앱에서 관리) | DB 오류 | `SeparateDatabaseAndState` 사용 (§4.2) |
| `FailedOperation.user` FK의 AUTH_USER_MODEL 차이 | FK 불일치 | `settings.AUTH_USER_MODEL` swappable FK |
| shopping 기존 코드에서 `from shopping.models import FailedOperation` 사용 | ImportError | deprecated import 경로 유지 (re-export) 후 점진 제거 |
| Celery beat schedule에 shopping task 경로 하드코딩 | Task not found | celery beat 설정에서 경로 업데이트 |
| Admin URL reverse 불일치 (`admin:shopping_user_change`) | Admin 깨짐 | 패키지 admin에서 `settings.AUTH_USER_MODEL` 기반 동적 URL 생성 |

---

## 9. 참조

| 문서 | 관련 내용 |
|------|----------|
| `middleware_system/06_REDIS_MIGRATION.md` | Redis 기반 전환으로 CircuitBreakerState, RateLimitState, AuditLog 삭제 근거 |
| `middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md` | 하이브리드 스토리지 아키텍처 |
| `middleware_system/80_DEPRECATION_CLEANUP_MASTER_PLAN.md` | deprecated 모듈 정리 계획 |
| `middleware_system/83_PHASE3_REEXPORT_CLEANUP.md` | re-export 정리 계획 |
| `selfhealing/adapters/django/models.py` | Abstract 모델 정의 (AbstractFailedOperation, AbstractAuditLog, AbstractPostmortemRecord) |
| `selfhealing/adapters/django/apps.py` | SelfHealingConfig AppConfig (label="selfhealing") |
| `selfhealing/adapters/django/migrations/0001_initial.py` | PostmortemRecord, CascadeEventArchive concrete 생성 |
| `selfhealing/api/django/serializers/control.py` | Control API 시리얼라이저 (shopping 중복 코드의 원본) |
