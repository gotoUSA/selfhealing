# 108. 하드코딩 설정값 리팩토링 Part 1: Celery Tasks

## 문서 정보

| 항목 | 내용 |
|------|------|
| 문서 번호 | 108 |
| 작성일 | 2026-01-26 |
| 대상 | Celery Task 데코레이터의 하드코딩된 설정값 |
| 우선순위 | 높음 |
| 예상 작업량 | 15개 파일, 약 20개 태스크 |

---

## 1. 개요

### 1.1 문제점

Celery `@shared_task` 데코레이터에 `max_retries`, `default_retry_delay`, `time_limit` 등이 직접 하드코딩되어 있어 운영 환경에서 변경이 불가능합니다.

### 1.2 현재 문제 패턴

```python
# 현재 (하드코딩)
@shared_task(
    max_retries=3,
    default_retry_delay=60,
)
def some_task():
    pass
```

### 1.3 목표 패턴

```python
# 목표 (Settings 기반)
from selfhealing.settings import get_recovery_tasks_settings

@shared_task(
    max_retries=get_recovery_tasks_settings().check_trigger_max_retries,
    default_retry_delay=get_recovery_tasks_settings().check_trigger_retry_delay,
)
def some_task():
    pass
```

---

## 2. 리팩토링 대상 목록

### 2.1 services/coordination/recovery_tasks.py

| 라인 | 태스크명 | 현재 값 | Settings 키 |
|------|----------|---------|-------------|
| 92-97 | `check_recovery_trigger_task` | `max_retries=3, default_retry_delay=60` | `SELFHEALING_RECOVERY_TASKS_CHECK_TRIGGER_MAX_RETRIES`, `_RETRY_DELAY` |
| 255-260 | `execute_recovery_step_task` | `max_retries=3, default_retry_delay=30` | `SELFHEALING_RECOVERY_TASKS_EXECUTE_STEP_MAX_RETRIES`, `_RETRY_DELAY` |
| 409-414 | `monitor_recovery_health_task` | `max_retries=2, default_retry_delay=15` | `SELFHEALING_RECOVERY_TASKS_MONITOR_RECOVERY_MAX_RETRIES`, `_RETRY_DELAY` |
| 516-521 | `check_stale_pending_recoveries_task` | `max_retries=1` | `SELFHEALING_RECOVERY_TASKS_STALE_CHECK_MAX_RETRIES` |

**추가 상수 (라인 83-85):**
```python
DEFAULT_TRIGGER_CHECK_INTERVAL = 60
DEFAULT_HEALTH_MONITOR_INTERVAL = 30
DEFAULT_STALE_CHECK_INTERVAL = 10
```

**확인된 Settings 파일:** `settings/recovery_tasks.py` (이미 존재)

---

### 2.2 tasks/cleanup_tasks.py

| 라인 | 태스크명 | 현재 값 | Settings 키 (신규 생성 필요) |
|------|----------|---------|-------------|
| 171-175 | `archive_old_dlq_entries_task` | `max_retries=2, default_retry_delay=300` | `SELFHEALING_CLEANUP_ARCHIVE_DLQ_MAX_RETRIES`, `_RETRY_DELAY` |
| 181-185 | `cleanup_expired_config_task` | `max_retries=2, default_retry_delay=300` | `SELFHEALING_CLEANUP_EXPIRED_CONFIG_MAX_RETRIES`, `_RETRY_DELAY` |
| 191-195 | `expire_approval_requests_task` | `max_retries=2, default_retry_delay=300` | `SELFHEALING_CLEANUP_APPROVAL_MAX_RETRIES`, `_RETRY_DELAY` |
| 201-205 | `purge_archived_dlq_entries_task` | `max_retries=1, default_retry_delay=600` | `SELFHEALING_CLEANUP_PURGE_DLQ_MAX_RETRIES`, `_RETRY_DELAY` |

**확인된 Settings 파일:** `settings/cleanup.py` (확장 필요)

---

### 2.3 tasks/daily_report.py

| 라인 | 태스크명 | 현재 값 | Settings 키 (신규 생성 필요) |
|------|----------|---------|-------------|
| 56-61 | `generate_daily_autonomous_report_task` | `max_retries=2, default_retry_delay=300` | `SELFHEALING_DAILY_REPORT_MAX_RETRIES`, `_RETRY_DELAY` |

**확인된 Settings 파일:** 없음 (신규 생성 필요: `settings/daily_report.py`)

---

### 2.4 tasks/governance.py

| 라인 | 태스크명 | 현재 값 | Settings 키 (신규 생성 필요) |
|------|----------|---------|-------------|
| 140-145 | `check_emergency_mode_expiry_task` | `max_retries=3, default_retry_delay=60` | `SELFHEALING_GOVERNANCE_EXPIRY_CHECK_MAX_RETRIES`, `_RETRY_DELAY` |

**확인된 Settings 파일:** `settings/governance.py` (확장 필요)

---

### 2.5 tasks/config_apply.py

| 라인 | 태스크명 | 현재 값 | Settings 키 (신규 생성 필요) |
|------|----------|---------|-------------|
| 24-29 | `apply_pending_config_changes` | `max_retries=3, default_retry_delay=10` | `SELFHEALING_CONFIG_APPLY_PENDING_MAX_RETRIES`, `_RETRY_DELAY` |
| 96-101 | `apply_graceful_config_change` | `max_retries=10, default_retry_delay=5` | `SELFHEALING_CONFIG_APPLY_GRACEFUL_MAX_RETRIES`, `_RETRY_DELAY` |
| 219 | `cleanup_expired_config_changes` | `max_age_hours=24` (함수 파라미터) | `SELFHEALING_CONFIG_CLEANUP_MAX_AGE_HOURS` |

**확인된 Settings 파일:** `settings/apply_strategy.py` (확장 필요)

---

### 2.6 tasks/chaos_scheduler.py

| 라인 | 태스크명 | 현재 값 | Settings 키 (신규 생성 필요) |
|------|----------|---------|-------------|
| 229-235 | `run_scheduled_experiments_task` | `max_retries=0, soft_time_limit=300, time_limit=360` | `SELFHEALING_CHAOS_SCHEDULER_EXPERIMENT_MAX_RETRIES`, `_SOFT_TIME_LIMIT`, `_TIME_LIMIT` |
| 241-245 | `generate_daily_resilience_report_task` | `max_retries=3, default_retry_delay=300` | `SELFHEALING_CHAOS_SCHEDULER_REPORT_MAX_RETRIES`, `_RETRY_DELAY` |
| 253-257 | `cleanup_expired_approvals_task` | `max_retries=1` | `SELFHEALING_CHAOS_SCHEDULER_CLEANUP_MAX_RETRIES` |
| 262-266 | `check_pending_approvals_task` | `max_retries=1` | `SELFHEALING_CHAOS_SCHEDULER_PENDING_CHECK_MAX_RETRIES` |

**확인된 Settings 파일:** `settings/chaos.py` (확장 필요)

---

## 3. 구현 순서

### Phase 1: Settings 파일 확장/생성 (우선순위: 1)

#### 3.1.1 `settings/cleanup.py` 확장

```python
# 추가할 필드
SELFHEALING_CLEANUP_ARCHIVE_DLQ_MAX_RETRIES=2
SELFHEALING_CLEANUP_ARCHIVE_DLQ_RETRY_DELAY=300
SELFHEALING_CLEANUP_EXPIRED_CONFIG_MAX_RETRIES=2
SELFHEALING_CLEANUP_EXPIRED_CONFIG_RETRY_DELAY=300
SELFHEALING_CLEANUP_APPROVAL_MAX_RETRIES=2
SELFHEALING_CLEANUP_APPROVAL_RETRY_DELAY=300
SELFHEALING_CLEANUP_PURGE_DLQ_MAX_RETRIES=1
SELFHEALING_CLEANUP_PURGE_DLQ_RETRY_DELAY=600
```

#### 3.1.2 `settings/daily_report.py` 신규 생성

```python
# 신규 파일
SELFHEALING_DAILY_REPORT_MAX_RETRIES=2
SELFHEALING_DAILY_REPORT_RETRY_DELAY=300
```

#### 3.1.3 `settings/governance.py` 확장

```python
# 추가할 필드
SELFHEALING_GOVERNANCE_EXPIRY_CHECK_MAX_RETRIES=3
SELFHEALING_GOVERNANCE_EXPIRY_CHECK_RETRY_DELAY=60
```

#### 3.1.4 `settings/apply_strategy.py` 확장

```python
# 추가할 필드
SELFHEALING_CONFIG_APPLY_PENDING_MAX_RETRIES=3
SELFHEALING_CONFIG_APPLY_PENDING_RETRY_DELAY=10
SELFHEALING_CONFIG_APPLY_GRACEFUL_MAX_RETRIES=10
SELFHEALING_CONFIG_APPLY_GRACEFUL_RETRY_DELAY=5
```

#### 3.1.5 `settings/chaos.py` 확장

```python
# 추가할 필드
SELFHEALING_CHAOS_SCHEDULER_EXPERIMENT_MAX_RETRIES=0
SELFHEALING_CHAOS_SCHEDULER_EXPERIMENT_SOFT_TIME_LIMIT=300
SELFHEALING_CHAOS_SCHEDULER_EXPERIMENT_TIME_LIMIT=360
SELFHEALING_CHAOS_SCHEDULER_REPORT_MAX_RETRIES=3
SELFHEALING_CHAOS_SCHEDULER_REPORT_RETRY_DELAY=300
SELFHEALING_CHAOS_SCHEDULER_CLEANUP_MAX_RETRIES=1
SELFHEALING_CHAOS_SCHEDULER_PENDING_CHECK_MAX_RETRIES=1
```

---

### Phase 2: 태스크 파일 수정 (우선순위: 2)

#### 3.2.1 `services/coordination/recovery_tasks.py` 수정

**수정 위치:** 라인 92-97

```python
# Before
@shared_task(
    name="selfhealing.check_recovery_trigger",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="selfhealing_recovery",
)

# After
from selfhealing.settings import get_recovery_tasks_settings

_recovery_settings = get_recovery_tasks_settings()

@shared_task(
    name="selfhealing.check_recovery_trigger",
    bind=True,
    max_retries=_recovery_settings.check_trigger_max_retries,
    default_retry_delay=_recovery_settings.check_trigger_retry_delay,
    queue="selfhealing_recovery",
)
```

**수정 위치:** 라인 83-85 (상수 제거)

```python
# Before
DEFAULT_TRIGGER_CHECK_INTERVAL = 60
DEFAULT_HEALTH_MONITOR_INTERVAL = 30
DEFAULT_STALE_CHECK_INTERVAL = 10

# After (deprecated 주석 추가 또는 제거)
# 이미 settings/recovery_tasks.py에 정의됨
```

#### 3.2.2 나머지 태스크 파일 동일 패턴 적용

- `tasks/cleanup_tasks.py`
- `tasks/daily_report.py`
- `tasks/governance.py`
- `tasks/config_apply.py`
- `tasks/chaos_scheduler.py`

---

### Phase 3: 테스트 (우선순위: 3)

1. 각 Settings 파일의 단위 테스트 작성
2. 환경변수 오버라이드 테스트
3. 태스크 등록 및 실행 테스트
4. 기존 테스트 통과 확인

---

## 4. 검증 체크리스트

| 항목 | 확인 |
|------|------|
| 모든 `max_retries` 하드코딩 제거 | ☐ |
| 모든 `default_retry_delay` 하드코딩 제거 | ☐ |
| 모든 `time_limit` 하드코딩 제거 | ☐ |
| Settings 파일에 환경변수 매핑 완료 | ☐ |
| 기본값이 기존 하드코딩 값과 동일 | ☐ |
| 단위 테스트 통과 | ☐ |
| 통합 테스트 통과 | ☐ |

---

## 5. 관련 문서

- [40_PYDANTIC_CONFIG_MIGRATION.md](40_PYDANTIC_CONFIG_MIGRATION.md)
- [107_HARDCODED_CONFIG_MASTER_ROADMAP.md](107_HARDCODED_CONFIG_MASTER_ROADMAP.md)
- [settings/recovery_tasks.py](../../../packages/selfhealing-python/src/selfhealing/settings/recovery_tasks.py)
