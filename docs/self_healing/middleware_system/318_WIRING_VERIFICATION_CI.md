# 318. Wiring Verification CI — 서비스 와이어링 자동 검증

> **Status**: Implemented
> **Severity**: P2 (MEDIUM) — 고아 서비스 재발 방지
> **Target**: CI/CD 파이프라인 + `scripts/verify_wiring.py`
> **References**:
> - 317 — Orphan Service Wiring (고아 서비스 목록)
> - 311 — Interface Contract Integrity (인터페이스 계약 검증)
> - 313 — Settings Configuration Consistency (설정 일관성 검증)

---

## 1. 현황 및 문제

### 1.1 문제

51개 서비스 중 10%가 고아 상태로 발견되었다 (317번 참조). 서비스를 구현한 후 엔트리포인트에 연결하지 않아 런타임에 활성화되지 않는 문제가 반복된다.

**근본 원인**: 서비스 추가 시 "구현 → 테스트 → 와이어링" 중 마지막 단계가 누락되는 패턴.

### 1.2 요구사항

| 요구사항 | 설명 |
|---------|------|
| 자동 감지 | 새 서비스 디렉토리 추가 시 와이어링 누락 자동 감지 |
| CI 통합 | PR 머지 전 와이어링 검증 실패 시 차단 |
| 허용 목록 | 의도적 미연결 서비스는 명시적 제외 가능 |
| 보고서 | 와이어링 현황 요약 출력 |

---

## 2. 엔트리포인트 분류 체계

### 2.1 엔트리포인트 유형 (6종)

서비스가 "연결됨"으로 인정되려면 아래 중 **최소 1개**에서 import되어야 한다:

| 유형 | 파일 위치 | 검색 패턴 |
|------|----------|----------|
| **Middleware** | `api/django/middleware/`, `api/django/*.py` | `class *Middleware` |
| **Celery Task** | `celery_tasks/`, `tasks/`, `adapters/celery/tasks/` | `@shared_task`, `@app.task` |
| **AppConfig.ready()** | `adapters/django/apps.py` | `def ready(self)` 내부 |
| **Signal Hook** | `adapters/django/signal_hooks.py`, `adapters/celery/signal_hooks.py` | `.connect(` |
| **API View** | `api/django/views/` | `class *ViewSet`, `class *View` |
| **Management Command** | `adapters/django/management/commands/` | `class Command` |

### 2.2 간접 연결 인정 기준

직접 엔트리포인트 외에도 다음은 "연결됨"으로 인정:

| 간접 연결 유형 | 예시 |
|---------------|------|
| 이미 연결된 서비스에서 import | `circuit_breaker` → `error_budget_gate` |
| ProviderRegistry에 등록 | `factory.py`의 `register_*()` 호출 |
| EventBus 구독 | `EventBus.subscribe(EventType.*, handler)` |
| Celery Beat 스케줄 등록 | `myproject/celery.py` CELERY_BEAT_SCHEDULE |

---

## 3. 검증 스크립트 설계

### 3.1 파일 구조

```
scripts/
  verify_wiring.py          # 메인 검증 스크립트
  wiring_allowlist.yaml     # 의도적 미연결 허용 목록
```

### 3.2 verify_wiring.py 알고리즘

```
Phase 1: 서비스 디렉토리 스캔
  - services/ 하위 모든 디렉토리 목록 추출
  - IGNORE_DIRS 제외 (§3.5 참조)

Phase 2: AST 기반 엔트리포인트 import 스캔 (§3.6 참조)
  - 각 엔트리포인트 파일을 ast.parse()로 파싱
  - ast.Import / ast.ImportFrom 노드 순회 → 서비스 참조 추출
  - ast.Constant 문자열 리터럴 마이닝 → 동적 참조 추출
  - 함수 내부 지연 임포트, as 앨리어스, 멀티라인 임포트 모두 감지

Phase 2.5: Django MIDDLEWARE 문자열 배열 스캔 (§3.7 참조)
  - myproject/settings/base.py의 MIDDLEWARE = [...] 에서 selfhealing 경로 추출
  - 해당 미들웨어 파일 → 내부 서비스 import 추적 (2-hop)

Phase 3: 간접 연결 스캔
  - ProviderRegistry에서 서비스 참조 검색
  - 이미 연결된 서비스 내부에서 다른 서비스 import 추적 (1-depth)
  - EventBus.subscribe() 호출을 서비스 디렉토리 내 텍스트 검색으로 감지 (§3.8 참조)

Phase 4: 허용 목록 적용
  - wiring_allowlist.yaml에 명시된 서비스는 고아여도 PASS

Phase 5: 결과 보고
  - CONNECTED / INDIRECTLY_CONNECTED / ALLOWLISTED / ORPHAN 분류
  - ORPHAN이 1개 이상이면 exit code 1 (CI 실패)
```

### 3.3 verify_wiring.py 구현 명세

```python
"""
Service Wiring Verification Script.

Usage:
    python scripts/verify_wiring.py [--verbose] [--json] [--fix-suggestions]

Exit Codes:
    0 — All services wired or allowlisted
    1 — Orphan services detected

Dependencies:
    - Python 3.12+ (ast module)
    - pyyaml (allowlist parsing)
    - Django import 불필요 — 순수 정적 분석
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SELFHEALING_ROOT = PROJECT_ROOT / "packages" / "selfhealing-python" / "src" / "selfhealing"
SERVICES_DIR = SELFHEALING_ROOT / "services"

# 인프라/유틸리티 디렉토리 — 서비스가 아니므로 스캔 대상에서 제외
IGNORE_DIRS = {"event_bus", "factory", "__pycache__"}

# selfhealing 패키지 내부 엔트리포인트 (SELFHEALING_ROOT 기준 상대 경로)
ENTRY_POINT_PATHS: list[str] = [
    # Middleware
    "api/django/middleware/",
    "api/django/tiering/",
    "api/django/rate_limit.py",
    "api/django/pool_circuit_breaker.py",
    "api/django/audit_middleware.py",
    "api/django/cell/middleware.py",
    # Celery Tasks
    "celery_tasks/",
    "tasks/",
    "adapters/celery/tasks/",
    # AppConfig / Bootstrap
    "adapters/django/apps.py",
    # Signals
    "adapters/django/signal_hooks.py",
    "adapters/celery/signal_hooks.py",
    # API Views
    "api/django/views/",
    # Management Commands
    "adapters/django/management/commands/",
    # Factory
    "factory.py",
]

# 호스트 앱 엔트리포인트 (PROJECT_ROOT 기준 상대 경로)
HOST_ENTRY_POINT_PATHS: list[str] = [
    "myproject/celery.py",
    "myproject/settings/",
]

# Django MIDDLEWARE 문자열 배열 스캔 대상
MIDDLEWARE_SETTINGS_PATH = PROJECT_ROOT / "myproject" / "settings" / "base.py"
```

### 3.5 IGNORE_DIRS — 비서비스 디렉토리 제외

`services/` 하위에는 실제 서비스가 아닌 인프라/유틸리티 디렉토리가 존재한다. 이들은 와이어링 검증 대상이 아니다.

```python
IGNORE_DIRS = {"event_bus", "factory", "__pycache__"}
```

| 디렉토리 | 제외 사유 |
|----------|----------|
| `event_bus` | 이벤트 인프라. 다른 서비스에서 import하는 쪽이지, 엔트리포인트에서 직접 import되지 않음 |
| `factory` | 서비스 팩토리 유틸리티 |
| `__pycache__` | Python 캐시 디렉토리 |

> **유지보수**: 유사한 인프라 디렉토리가 추가될 경우 이 목록에 추가한다.

### 3.6 AST 기반 Import 분석 — Regex 대체

#### 3.6.1 배경: Regex의 한계

기존 `IMPORT_PATTERNS` 정규식 방식은 다음 패턴을 놓친다:

| 패턴 | 예시 코드 | Regex 감지 |
|------|----------|-----------|
| 멀티라인 괄호 임포트 | `from selfhealing.services import (\n    RetryHandler,\n)` | ❌ |
| `as` 앨리어스 | `import global_state as _global_state_module` | ❌ |
| 함수 내부 지연 임포트 | `def _store_to_dlq(): from selfhealing.services.dlq import ...` | ❌ |
| `importlib.import_module()` | `importlib.import_module("selfhealing.services.saga.tasks")` | ❌ |
| `TYPE_CHECKING` 조건부 | `if TYPE_CHECKING: from selfhealing.services.circuit_mesh...` | ❌ |
| `__getattr__` lazy binding | `importlib.import_module("selfhealing.services.cleanup_service")` | ❌ |

실제 엔트리포인트 파일(`signal_hooks.py`, `beat_schedule.py`)의 약 40%가 지연 임포트 패턴을 사용하므로 Regex로는 와이어링의 상당 부분을 놓친다.

#### 3.6.2 AST 2-Pass 하이브리드 알고리즘

```python
def extract_service_refs(file_path: Path) -> set[str]:
    """AST 기반 서비스 참조 추출. Django import 불필요."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    refs: set[str] = set()

    for node in ast.walk(tree):
        # Pass 1: 직접 임포트 (멀티라인, alias, 조건부, 지연 임포트 모두 포착)
        if isinstance(node, ast.ImportFrom) and node.module:
            # "from selfhealing.services.X ..." → X 추출
            if node.module.startswith("selfhealing.services."):
                parts = node.module.split(".")
                if len(parts) >= 3:
                    refs.add(parts[2])  # services.{name}
            # "from selfhealing.services import X" → X 추출
            if node.module == "selfhealing.services" and node.names:
                for alias in node.names:
                    refs.add(alias.name)

        # Pass 2: 문자열 리터럴 마이닝 (Celery task names, importlib paths)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if "selfhealing.services." in value:
                parts = value.split("selfhealing.services.")[1].split(".")
                if parts[0]:
                    refs.add(parts[0])

    return refs
```

> **설계 원칙**: CI 스크립트는 Django 런타임에 의존하지 않는다. `ast.parse()`만 사용하므로
> 앱에 에러가 있어서 Django가 로드되지 않는 상황에서도 독립적으로 실행된다.

### 3.7 Django MIDDLEWARE 문자열 배열 스캔

#### 3.7.1 배경

Django 미들웨어는 파이썬 import가 아닌 `settings.py`의 `MIDDLEWARE = [...]` **문자열 배열**로 등록된다. 따라서 Phase 2의 AST import 분석만으로는 미들웨어를 통한 서비스 와이어링을 감지할 수 없다.

현재 `myproject/settings/base.py`에 12개의 selfhealing 미들웨어가 문자열로 등록:

```python
MIDDLEWARE = [
    "selfhealing.audit.trace.trace_id_middleware",
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.tiering.TieringMiddleware",
    "selfhealing.api.django.middleware.IPBanMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",
    "selfhealing.api.django.cell.middleware.BaggageSyncMiddleware",
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",
    "selfhealing.api.django.audit_middleware.AuditMiddleware",
    # ...
]
```

#### 3.7.2 2-Hop 추적 알고리즘

미들웨어 경로 자체는 `selfhealing.services.*`가 아니므로 **2-hop 추적**이 필요하다:

```
Hop 1: MIDDLEWARE 문자열 → 미들웨어 파일 경로 resolve
Hop 2: 미들웨어 파일 → 내부 서비스 import 추출 (§3.6 AST 사용)
```

```python
def scan_middleware_wiring() -> dict[str, set[str]]:
    """MIDDLEWARE 문자열 배열에서 간접 서비스 참조 추출.

    모듈 상수 MIDDLEWARE_SETTINGS_PATH를 사용한다 (파라미터 불필요).
    """
    if not MIDDLEWARE_SETTINGS_PATH.exists():
        return {}

    tree = ast.parse(MIDDLEWARE_SETTINGS_PATH.read_text(encoding="utf-8"))
    middleware_paths: list[str] = []

    # Hop 1: MIDDLEWARE = [...] 에서 selfhealing 경로 추출
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", "") == "MIDDLEWARE":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                if "selfhealing." in elt.value:
                                    middleware_paths.append(elt.value)

    # Hop 2: 각 미들웨어 파일 내부에서 서비스 import 추출
    result: dict[str, set[str]] = {}
    for dotted_path in middleware_paths:
        file_path = _dotted_to_file_path(dotted_path)
        if file_path and file_path.exists():
            refs = extract_service_refs(file_path)
            if refs:
                result[dotted_path] = refs

    return result
```

현재 감지되는 미들웨어 → 서비스 간접 연결:

| 미들웨어 | 내부 서비스 import (지연 임포트) |
|----------|-------------------------------|
| `SelfHealingMiddleware` | `circuit_breaker`, `dlq`, `audit` |
| `HealthBridgeMiddleware` | `circuit_breaker` |
| `IPBanMiddleware` | `security` (+ `factory` ProviderRegistry) |
| `BackpressureMiddleware` | `scaling.rate_controller`, `scaling.graceful_degradation` |

### 3.8 EventBus 구독 감지

서비스 디렉토리 내에서 `EventBus.subscribe()` 호출을 텍스트 정규식으로 감지한다. AST 수준의 메서드 스코프 추적은 불필요하다.

```python
import re

SUBSCRIBE_PATTERN = re.compile(r"\.subscribe\(\s*EventType\.")

def _has_eventbus_subscription(service_dir: Path) -> bool:
    """서비스 디렉토리 내 .py 파일에서 EventBus subscribe 호출 감지."""
    for py_file in service_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if SUBSCRIBE_PATTERN.search(content):
            return True
    return False
```

> **포매터 대응**: Black 등의 코드 포매터가 `.subscribe(\n    EventType...` 형태로 줄바꿈을
> 삽입하므로, `\s*`로 공백과 줄바꿈을 모두 커버한다.

현재 22개 이상의 파일에서 `.subscribe(EventType.` 패턴이 일관되게 사용 중이며, 다음 3가지 호출 위치에서 발생:

| 패턴 | 위치 예시 |
|------|----------|
| 독립 함수 (모듈 레벨) | `circuit_mesh/mesh_coordinator.py:626` `register_mesh_handlers()` |
| 인스턴스 메서드 (`_subscribe_*`) | `backoff_calculator/calculator.py:156`, `cell_topology/registry.py:442` |
| `register()` / `initialize()` 메서드 | `event_journal/subscriber.py:77` |

### 3.4 wiring_allowlist.yaml 형식

```yaml
# 의도적으로 미연결된 서비스 허용 목록
# 사유를 반드시 기록할 것

allowlist:
  # 호스트 앱에서 정의해야 하는 서비스 (라이브러리는 인프라만 제공)
  - name: saga
    reason: "Saga Definition은 호스트 앱에서 등록. 라이브러리는 오케스트레이션 인프라만 제공."
    ticket: "317"

  # Multi-Region 전용 (단일 리전 배포에서는 불필요)
  - name: isolation
    reason: "Regional Isolation은 Multi-Region 배포 시에만 활성화."
    ticket: "317"
    condition: "SELFHEALING_MULTIREGION_ENABLED=True일 때만 검증"
```

---

## 4. CI 파이프라인 통합

### 4.1 GitHub Actions 워크플로우

```yaml
# .github/workflows/wiring-check.yml
name: Service Wiring Verification

on:
  pull_request:
    paths:
      - 'packages/selfhealing-python/src/selfhealing/services/**'
      - 'packages/selfhealing-python/src/selfhealing/adapters/**'
      - 'packages/selfhealing-python/src/selfhealing/api/**'
      - 'packages/selfhealing-python/src/selfhealing/celery_tasks/**'
      - 'packages/selfhealing-python/src/selfhealing/tasks/**'
      - 'myproject/celery.py'
      - 'myproject/settings/**'
  workflow_dispatch:
  schedule:
    - cron: '0 9 * * 1'  # Every Monday 09:00 UTC

jobs:
  verify-wiring:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install pyyaml

      - name: Verify service wiring
        run: python scripts/verify_wiring.py --verbose

      - name: Generate JSON report
        if: always()
        run: python scripts/verify_wiring.py --json || true

      - name: Upload wiring report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: wiring-report
          path: wiring_report.json
```

### 4.2 트리거 조건

| 이벤트 | 트리거 |
|--------|--------|
| PR 생성/업데이트 | `services/` 또는 엔트리포인트 파일 변경 시 |
| 수동 실행 | `workflow_dispatch` |
| 주간 정기 | `schedule: cron('0 9 * * 1')` (매주 월요일 09:00) |

### 4.3 실패 시 동작

```
ORPHAN DETECTED: correlation_engine
  ├── Not imported in any middleware
  ├── Not imported in any celery task
  ├── Not imported in AppConfig.ready()
  ├── Not imported in any signal hook
  ├── Not imported in any API view
  └── Not in wiring_allowlist.yaml

SUGGESTION: Add to one of:
  1. adapters/django/apps.py → ready() initialization
  2. celery_tasks/ → periodic task
  3. scripts/wiring_allowlist.yaml → if intentionally unwired

Exit code: 1 (FAIL)
```

---

## 5. 출력 형식

### 5.1 터미널 출력 (--verbose)

```
=== Service Wiring Verification ===

CONNECTED (39):
  ✓ circuit_breaker     → middleware, celery_task, view, signal
  ✓ dlq                 → middleware, celery_task, view
  ✓ audit               → middleware, appconfig, view
  ...

INDIRECTLY CONNECTED (3):
  ~ config_shadow       → celery_task (via adapters/celery/tasks/)
  ~ daily_report        → celery_task (via tasks/)
  ~ runbook             → celery_task, eventbus subscriber
  ...

ALLOWLISTED (2):
  ○ saga                → "Saga Definition은 호스트 앱에서 등록"
  ○ isolation           → "Multi-Region 배포 시에만 활성화"

ORPHAN (5):
  ✗ correlation_engine  → NO ENTRY POINT FOUND
  ✗ execution_services  → NO ENTRY POINT FOUND
  ✗ predictive_forecaster → NO ENTRY POINT FOUND
  ✗ rate_limit          → NO ENTRY POINT FOUND (Kafka channel unused)
  ✗ capacity_reservation → NO ENTRY POINT FOUND

=== RESULT: FAIL (5 orphans) ===
```

### 5.2 JSON 출력 (--json)

```json
{
  "timestamp": "2026-03-07T22:30:00Z",
  "total_services": 51,
  "connected": 39,
  "indirect": 3,
  "eventbus_subscribers": 5,
  "allowlisted": 2,
  "orphan": 5,
  "orphans": [
    {
      "name": "correlation_engine",
      "path": "services/correlation_engine/",
      "suggested_entry_points": ["appconfig", "celery_beat"]
    }
  ],
  "middleware_wiring": {},
  "dep_graph_warnings": [],
  "missing_feature_flags": ["correlation_engine"],
  "pass": false
}
```

---

## 6. 추가 검증 규칙

### 6.1 Celery Task 등록 검증 — 주기적 vs 온디맨드 구분

`@shared_task`로 정의된 태스크가 Celery Beat 스케줄에 등록되었는지 검증한다. 단, **온디맨드 태스크**(`.delay()` / `.apply_async()`로 호출)는 Beat 등록이 불필요하므로 구분이 필요하다.

#### 6.1.1 구분 전략

```
Phase A: @shared_task 정의 수집
  - celery_tasks/, tasks/, adapters/celery/tasks/, services/*/tasks.py 스캔
  - AST로 @shared_task 데코레이터의 name= 파라미터 추출

Phase B: Beat 스케줄 수집 (SSOT)
  - myproject/celery.py의 CELERY_BEAT_SCHEDULE 파싱
  - adapters/celery/beat_schedule.py의 _SCHEDULE_MODULES 문자열 리터럴 마이닝

Phase C: 교차 검증
  - Beat에 등록된 task name → 주기적 Task
  - Beat에 없는 task name → 온디맨드 Task (WARNING 대신 INFO)
  - wiring_allowlist.yaml의 on_demand_tasks 섹션에 명시된 task는 검증 제외
```

#### 6.1.2 현재 태스크 현황

| 파일 | Task | 유형 |
|------|------|------|
| `celery_tasks/metrics_tasks.py` | `collect_self_healing_metrics` | 주기적 (Beat 등록) |
| `celery_tasks/forecaster_tasks.py` | `run_forecaster_cycle` | 주기적 (Beat 등록) |
| `services/runbook/tasks.py` | `resume_runbook_task` | **온디맨드** (실행 재개) |
| `services/runbook/tasks.py` | `scan_orphan_runbook_executions` | 주기적 (Beat 등록) |
| `services/saga/tasks.py` | saga orchestration tasks | **온디맨드** (오케스트레이션) |
| `services/coordination/recovery_tasks.py` | recovery lifecycle tasks | 주기적 (Beat 등록) |

> **설계 원칙**: 온디맨드 Task를 커스텀 데코레이터로 마킹하는 것은 비즈니스 로직에 검증용 메타데이터를
> 오염시킨다. CI 레벨(Beat 스케줄 교차 검증 + Allowlist)에서 통제하여 프로덕션 코드의 순수성을 유지한다.

#### 6.1.3 wiring_allowlist.yaml 온디맨드 섹션

```yaml
on_demand_tasks:
  - name: "selfhealing.runbook.resume_pipeline"
    reason: "Runbook 실행 재개 — 사용자 요청 시에만 호출"
  - name: "selfhealing.saga.*"
    reason: "Saga 오케스트레이션 — 워크플로우 진행 시에만 호출"
```

### 6.2 ServiceDependencyGraph 초기화 순서 검증

317번 구현에서 `AppConfig.ready()` 내부에 `ServiceDependencyGraph` 기반 위상 정렬이 도입되었다. 별도의 `bootstrap.py`는 존재하지 않으며, `apps.py:_initialize_orphan_services()` 메서드가 초기화 오케스트레이터 역할을 한다.

#### 6.2.1 검증 대상

기존 `ready()` 전체가 아닌 **`_initialize_orphan_services()` 내부의 `ServiceDependencyGraph`** 호출을 검증한다:

```
# 실제 구현 위치: adapters/django/apps.py:907-962
graph = ServiceDependencyGraph()
graph.register_service("event_journal")
graph.register_service("config_shadow", depends_on=["event_journal"])
graph.register_service("correlation_engine", depends_on=["event_bus"])
graph.register_service("capacity_reservation", depends_on=["rate_controller", "bulkhead"])
# ...
init_order = graph.topological_sort_subset(services=[...], direction="leaves_first")
```

#### 6.2.2 검증 알고리즘

```
Phase A: apps.py에서 graph.register_service() 호출 목록 AST 추출
  - 서비스명과 depends_on 인자 파싱

Phase B: depends_on 의존성이 실제 import 관계와 일치하는지 교차 검증
  - 예: correlation_engine이 event_bus를 depends_on으로 선언
        → correlation_engine/ 내부에서 event_bus를 실제로 import하는지 확인

Phase C: topological_sort_subset()에 전달된 서비스 목록이
         Phase 1에서 발견된 orphan 서비스 목록과 일치하는지 검증
  - 새 orphan 서비스가 graph에 등록되지 않았으면 WARNING
```

#### 6.2.3 관련 코드

| 파일 | 역할 |
|------|------|
| `adapters/django/apps.py:907-962` | `_initialize_orphan_services()` — 위상 정렬 기반 초기화 |
| `core/dependency_graph.py:212-257` | `ServiceDependencyGraph` — Kahn's algorithm 구현 |
| `tests/unit/adapters/django/test_orphan_service_wiring.py:251-299` | 위상 정렬 순서 검증 테스트 |

### 6.3 Feature Flag 존재 검증 — 정적 텍스트 검색

각 서비스에 대응하는 Feature Flag가 `settings/`에 정의되어 있는지 검증한다.

#### 6.3.1 검증 방식: 정적 `env_prefix` 매칭

```python
def verify_feature_flags(service_names: list[str]) -> list[str]:
    """settings/ 디렉토리에서 각 서비스의 Feature Flag 존재 여부를 정적으로 검증."""
    settings_dir = Path(SELFHEALING_ROOT) / "settings"
    missing: list[str] = []

    for name in service_names:
        expected_prefix = f"SELFHEALING_{name.upper()}_"
        found = False
        for py_file in settings_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if f'env_prefix="{expected_prefix}"' in content:
                found = True
                break
            if f"env_prefix='{expected_prefix}'" in content:
                found = True
                break
        if not found:
            missing.append(name)

    return missing
```

#### 6.3.2 설계 근거

| 방식 | 장점 | 단점 | 채택 |
|------|------|------|------|
| `hasattr()` 동적 검사 | 정확함 | Django 런타임 필요, 앱 로드 실패 시 동작 불가 | ❌ |
| `model_fields` 리플렉션 | Pydantic 메타데이터 활용 | Django 런타임 필요, settings lazy-load 문제 | ❌ |
| 정적 `env_prefix` 텍스트 검색 | Django 불필요, 독립 실행 | 패턴 변경 시 수정 필요 | ✅ |

> **설계 원칙**: CI 검증 스크립트는 Django 앱의 런타임에 의존하지 않는다.
> 앱에 에러가 있어서 로드가 안 되는 상황에서도 "설정이 누락되었다"는 정확한 피드백을 줄 수 있어야 한다.

#### 6.3.3 현재 설정 구조

각 설정 파일은 Pydantic v2 `BaseSettings`를 따르며 일관된 `env_prefix` 패턴을 사용:

```python
# settings/admission_control.py
class AdmissionControlSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SELFHEALING_ADMISSION_CONTROL_")
    enabled: bool = Field(default=True)
```

현재 29개 이상의 설정 파일에서 `_ENABLED` 패턴이 확인됨.

---

## 7. 구현 순서

| 단계 | 내용 | 산출물 |
|------|------|--------|
| 1 | AST 기반 `extract_service_refs()` 구현 | §3.6 — 2-Pass 하이브리드 분석기 |
| 2 | Phase 1 서비스 스캔 + IGNORE_DIRS | §3.5 — 서비스 목록 추출 |
| 3 | Phase 2 엔트리포인트 AST 스캔 | §3.6 — 직접 임포트 + 문자열 리터럴 |
| 4 | Phase 2.5 MIDDLEWARE 문자열 배열 2-hop 추적 | §3.7 — 미들웨어 간접 연결 감지 |
| 5 | Phase 3 간접 연결 + EventBus 구독 감지 | §3.8 — subscribe 정규식 검색 |
| 6 | wiring_allowlist.yaml 작성 (+ on_demand_tasks) | §3.4, §6.1.3 |
| 7 | CI 워크플로우 추가 | §4 — GitHub Actions YAML |
| 8 | Celery Task 등록 검증 (주기적 vs 온디맨드) | §6.1 — Beat 교차 검증 + Allowlist |
| 9 | ServiceDependencyGraph 초기화 순서 검증 | §6.2 — apps.py graph 추출 + 교차 검증 |
| 10 | Feature Flag 정적 검증 | §6.3 — env_prefix 텍스트 검색 |

---

## 8. 기대 효과

| 지표 | 현재 | 적용 후 |
|------|------|---------|
| 고아 서비스 발견 시점 | 수동 코드 리뷰 (주~월 단위) | PR 시점 (분 단위) |
| 고아 서비스 비율 | 10% (5/51) | 0% (CI 차단) |
| 와이어링 현황 가시성 | 없음 | JSON 리포트 + 주간 정기 검증 |
| Import 감지 정확도 | ~60% (Regex) | ~95% (AST 하이브리드) |
| 미들웨어 와이어링 감지 | 미감지 | 2-hop 추적으로 완전 커버 |
| Django 런타임 의존 | 해당 없음 | 없음 (순수 정적 분석) |

---

## 9. 관련 문서

- **317_ORPHAN_SERVICE_WIRING.md** — 현재 고아 서비스 목록 및 연결 계획
- **311_INTERFACE_CONTRACT_INTEGRITY.md** — 인터페이스 계약 무결성 검증
- **313_SETTINGS_CONFIGURATION_CONSISTENCY.md** — 설정 일관성 검증

---

## 10. 리뷰 이력

| 날짜 | 항목 | 변경 내용 |
|------|------|----------|
| 2026-03-08 | §3.5 | IGNORE_DIRS 추가 — event_bus, factory 등 인프라 디렉토리 제외 |
| 2026-03-08 | §3.6 | AST 2-Pass 하이브리드로 Regex 대체 — 지연 임포트, alias, importlib 등 8가지 패턴 커버 |
| 2026-03-08 | §3.7 | Django MIDDLEWARE 문자열 배열 2-hop 추적 추가 |
| 2026-03-08 | §3.8 | EventBus subscribe 정규식 감지 — 포매터 줄바꿈 대응 `\s*` 적용 |
| 2026-03-08 | §6.1 | 주기적 vs 온디맨드 Task 구분 — Beat 교차 검증 + on_demand_tasks allowlist |
| 2026-03-08 | §6.2 | AppConfig.ready() → ServiceDependencyGraph 기반 검증으로 변경 (317 bootstrap 반영) |
| 2026-03-08 | §6.3 | hasattr 대신 정적 env_prefix 텍스트 검색 채택 — Django 런타임 비의존 |
| 2026-03-08 | §header | Status: Planned → Implemented |
| 2026-03-08 | §3.3 | ENTRY_POINT_PATHS를 2개 리스트(ENTRY_POINT_PATHS + HOST_ENTRY_POINT_PATHS)로 분리 — 코드 구현 반영 |
| 2026-03-08 | §3.7 | scan_middleware_wiring() 파라미터 제거 — 모듈 상수 MIDDLEWARE_SETTINGS_PATH 사용 |
| 2026-03-08 | §3.8 | has_eventbus_subscription → _has_eventbus_subscription (private function) |
| 2026-03-08 | §4.1 | CI YAML에 workflow_dispatch, schedule 트리거 및 JSON report 생성 step 추가 |
| 2026-03-08 | §5.2 | JSON 출력에 eventbus_subscribers, middleware_wiring, dep_graph_warnings, missing_feature_flags 필드 추가; orphan depends_on 제거 |
| 2026-03-08 | §3.8 | SUBSCRIBE_PATTERN에서 불필요한 re.MULTILINE 플래그 제거 — \s*가 이미 줄바꿈 매칭 |
| 2026-03-08 | §4.1 | CI paths 트리거에 scripts/verify_wiring.py, scripts/wiring_allowlist.yaml 추가 |
