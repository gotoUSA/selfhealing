# 318. Wiring Verification CI — 서비스 와이어링 자동 검증

> **Status**: Planned
> **Severity**: P2 (MEDIUM) — 고아 서비스 재발 방지
> **Target**: CI/CD 파이프라인 + `scripts/verify_wiring.py`
> **References**:
> - 317 — Orphan Service Wiring (고아 서비스 목록)
> - 311 — Interface Contract Integrity (인터페이스 계약 검증)

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
  - __pycache__, __init__.py 등 비서비스 항목 제외

Phase 2: 엔트리포인트 import 스캔
  - 각 엔트리포인트 파일에서 `from selfhealing.services.{name}` 패턴 검색
  - 각 엔트리포인트 파일에서 `selfhealing.services.{name}` 문자열 검색 (Celery Beat 등)

Phase 3: 간접 연결 스캔
  - ProviderRegistry에서 서비스 참조 검색
  - 이미 연결된 서비스 내부에서 다른 서비스 import 추적 (1-depth)

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
"""

# 상수
SELFHEALING_ROOT = "packages/selfhealing-python/src/selfhealing"
SERVICES_DIR = f"{SELFHEALING_ROOT}/services"

ENTRY_POINT_PATHS = [
    # Middleware
    f"{SELFHEALING_ROOT}/api/django/middleware/",
    f"{SELFHEALING_ROOT}/api/django/tiering/",
    f"{SELFHEALING_ROOT}/api/django/rate_limit.py",
    f"{SELFHEALING_ROOT}/api/django/pool_circuit_breaker.py",
    f"{SELFHEALING_ROOT}/api/django/audit_middleware.py",
    f"{SELFHEALING_ROOT}/api/django/cell/middleware.py",
    # Celery Tasks
    f"{SELFHEALING_ROOT}/celery_tasks/",
    f"{SELFHEALING_ROOT}/tasks/",
    f"{SELFHEALING_ROOT}/adapters/celery/tasks/",
    # AppConfig
    f"{SELFHEALING_ROOT}/adapters/django/apps.py",
    # Signals
    f"{SELFHEALING_ROOT}/adapters/django/signal_hooks.py",
    f"{SELFHEALING_ROOT}/adapters/celery/signal_hooks.py",
    # API Views
    f"{SELFHEALING_ROOT}/api/django/views/",
    # Management Commands
    f"{SELFHEALING_ROOT}/adapters/django/management/commands/",
    # Factory
    f"{SELFHEALING_ROOT}/factory.py",
    # Host app
    "myproject/celery.py",
    "myproject/settings/",
]

IMPORT_PATTERNS = [
    r"from\s+selfhealing\.services\.{name}",
    r"selfhealing\.services\.{name}",
    r"from\s+selfhealing\.services\s+import\s+.*{name}",
]
```

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
  "allowlisted": 2,
  "orphan": 5,
  "orphans": [
    {
      "name": "correlation_engine",
      "path": "services/correlation_engine/",
      "suggested_entry_points": ["appconfig", "celery_beat"],
      "depends_on": ["event_bus", "postmortem", "learning"]
    }
  ],
  "pass": false
}
```

---

## 6. 추가 검증 규칙

### 6.1 Celery Task 등록 검증

`@shared_task`로 정의된 태스크가 Celery Beat 스케줄에 등록되었는지 검증:

```
Phase A: @shared_task 정의 수집
  - celery_tasks/, tasks/, adapters/celery/tasks/, services/*/tasks.py 스캔

Phase B: Beat 스케줄 수집
  - myproject/celery.py의 CELERY_BEAT_SCHEDULE 파싱

Phase C: 비교
  - @shared_task가 있지만 Beat에 없으면 WARNING
  - (on-demand 태스크는 Beat 불필요 — allowlist 지원)
```

### 6.2 AppConfig.ready() 초기화 순서 검증

의존성 순서가 올바른지 검증:

```
event_journal → config_shadow (event_journal이 먼저 초기화되어야 함)
correlation_engine → postmortem (postmortem이 먼저 초기화되어야 함)
```

### 6.3 Feature Flag 존재 검증

각 서비스에 대응하는 Feature Flag가 `settings/`에 정의되어 있는지 검증:

```
서비스 디렉토리 → SELFHEALING_{NAME}_ENABLED 설정 존재 여부
```

---

## 7. 구현 순서

| 단계 | 내용 | 산출물 |
|------|------|--------|
| 1 | 기본 verify_wiring.py 구현 | 서비스 스캔 + import 검색 |
| 2 | wiring_allowlist.yaml 작성 | 의도적 미연결 서비스 등록 |
| 3 | CI 워크플로우 추가 | GitHub Actions YAML |
| 4 | Celery Task 등록 검증 추가 | Beat 스케줄 교차 검증 |
| 5 | 초기화 순서 검증 추가 | 의존성 그래프 기반 순서 검증 |
| 6 | Feature Flag 검증 추가 | settings 교차 검증 |

---

## 8. 기대 효과

| 지표 | 현재 | 적용 후 |
|------|------|---------|
| 고아 서비스 발견 시점 | 수동 코드 리뷰 (주~월 단위) | PR 시점 (분 단위) |
| 고아 서비스 비율 | 10% (5/51) | 0% (CI 차단) |
| 와이어링 현황 가시성 | 없음 | JSON 리포트 + 주간 정기 검증 |

---

## 9. 관련 문서

- **317_ORPHAN_SERVICE_WIRING.md** — 현재 고아 서비스 목록 및 연결 계획
- **311_INTERFACE_CONTRACT_INTEGRITY.md** — 인터페이스 계약 무결성 검증
- **313_SETTINGS_CONFIGURATION_CONSISTENCY.md** — 설정 일관성 검증
