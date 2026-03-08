# 321. Celery Beat Schedule Internalization — Beat 설정 라이브러리 내부화

> **Status**: Planning
> **Severity**: P1 (HIGH) — repo 분리 선행 조건
> **Target**: `selfhealing/celery_app.py` (신규)
> **References**:
> - 319 — Repo Separation Overview (커플링 C2)
> - 317 — Orphan Service Wiring (Beat 태스크 등록)

---

## 1. 현황 및 문제

### 1.1 현재: Consumer가 15+ Beat 태스크를 직접 정의

`myproject/celery.py`에 selfhealing Beat 태스크 15+개가 하드코딩되어 있다.

```python
# myproject/celery.py — 현재 상태 (약 170줄)
app.conf.beat_schedule = {
    # shopping 태스크 (consumer 영역)
    "retry-failed-emails": {...},
    "delete-unverified-users": {...},
    # ... shopping 태스크 7개

    # selfhealing 태스크 (라이브러리 영역 — 여기 있으면 안 됨)
    "check-circuit-breaker-recovery": {
        "task": "selfhealing.celery_tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # ... selfhealing 태스크 14개 더
}
```

**문제**:
- selfhealing에 새 Beat 태스크 추가 시 consumer celery.py도 수정 필요
- 큐/라우팅 설정도 consumer에 하드코딩 (`selfhealing.critical`, `chaos`, `chaos_monitoring`)

---

## 2. 설계

### 2.1 selfhealing 내부에 Beat Schedule 정의

```python
# selfhealing/celery_app.py (신규)
"""
Celery Beat schedule and queue definitions for selfhealing.

Consumer usage:
    from selfhealing.celery_app import SELFHEALING_BEAT_SCHEDULE
    app.conf.beat_schedule.update(SELFHEALING_BEAT_SCHEDULE)
"""
from celery.schedules import crontab

SELFHEALING_BEAT_SCHEDULE = {
    # === Circuit Breaker ===
    "selfhealing-check-circuit-breaker-recovery": {
        "task": "selfhealing.celery_tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    "selfhealing-expire-manual-overrides": {
        "task": "selfhealing.celery_tasks.expire_manual_overrides",
        "schedule": 300.0,
        "options": {"expires": 290},
    },

    # === Metrics ===
    "selfhealing-collect-metrics": {
        "task": "selfhealing.celery_tasks.collect_self_healing_metrics",
        "schedule": 60.0,
        "options": {"expires": 55},
    },

    # === SLA ===
    "selfhealing-check-sla-breaches": {
        "task": "selfhealing.celery_tasks.check_and_report_sla_breaches",
        "schedule": 300.0,
        "options": {"expires": 290},
    },

    # === DLQ ===
    "selfhealing-cleanup-dlq-entries": {
        "task": "selfhealing.celery_tasks.cleanup_resolved_dlq_entries",
        "schedule": crontab(hour=5, minute=0),
        "options": {"expires": 3600},
    },

    # === Chaos Engineering ===
    "selfhealing-check-chaos-recovery-monitoring": {
        "task": "selfhealing.celery_tasks.check_recovery_monitoring",
        "schedule": 30.0,
        "options": {"expires": 25, "queue": "chaos_monitoring"},
    },
    "selfhealing-chaos-hunt-zombie-experiments": {
        "task": "selfhealing.celery_tasks.hunt_zombie_experiments",
        "schedule": 60.0,
        "options": {"expires": 55, "queue": "chaos"},
    },
    "selfhealing-run-scheduled-experiments": {
        "task": "selfhealing.tasks.chaos_scheduler.run_scheduled_experiments_task",
        "schedule": 300.0,
        "options": {"expires": 290, "queue": "chaos"},
    },

    # === Config ===
    "selfhealing-apply-pending-config-changes": {
        "task": "selfhealing.apply_pending_config_changes",
        "schedule": 30.0,
        "options": {"expires": 25},
    },

    # === Saga ===
    "selfhealing-scan-orphan-sagas": {
        "task": "selfhealing.scan_orphan_sagas",
        "schedule": 120.0,
        "options": {"expires": 115},
    },

    # === Predictive Forecaster ===
    "selfhealing-run-forecaster-cycle": {
        "task": "selfhealing.celery_tasks.run_forecaster_cycle",
        "schedule": 60.0,
        "options": {"expires": 55, "queue": "monitoring"},
    },

    # === Recovery Coordinator ===
    "selfhealing-check-recovery-trigger": {
        "task": "selfhealing.check_recovery_trigger",
        "schedule": 60.0,
        "options": {"expires": 55, "queue": "selfhealing.critical"},
    },
    "selfhealing-monitor-recovery-health": {
        "task": "selfhealing.monitor_recovery_health",
        "schedule": 30.0,
        "options": {"expires": 25, "queue": "selfhealing.critical"},
    },
    "selfhealing-check-stale-pending-recoveries": {
        "task": "selfhealing.check_stale_pending_recoveries",
        "schedule": 600.0,
        "options": {"expires": 590, "queue": "selfhealing.critical"},
    },
    "selfhealing-cleanup-old-recovery-sessions": {
        "task": "selfhealing.cleanup_old_recovery_sessions",
        "schedule": crontab(hour=6, minute=0),
        "options": {"expires": 3600},
    },

    # === JWT Cleanup ===
    "selfhealing-flush-expired-jwt-tokens": {
        "task": "selfhealing.flush_expired_jwt_tokens",
        "schedule": crontab(hour=2, minute=30),
        "options": {"expires": 3600, "queue": "maintenance"},
    },
}

# selfhealing 전용 큐 정의
SELFHEALING_QUEUES = {
    "selfhealing.critical": {
        "exchange": "selfhealing.critical",
        "exchange_type": "direct",
        "routing_key": "selfhealing.critical",
    },
    "chaos": {
        "exchange": "chaos",
        "exchange_type": "direct",
        "routing_key": "chaos",
    },
    "chaos_monitoring": {
        "exchange": "chaos_monitoring",
        "exchange_type": "direct",
        "routing_key": "chaos.monitoring",
    },
}

# selfhealing 태스크 라우팅 정의
SELFHEALING_TASK_ROUTES = {
    "selfhealing.celery_tasks.execute_recovery_step": {
        "queue": "selfhealing.critical",
        "routing_key": "selfhealing.critical",
    },
    "selfhealing.celery_tasks.check_recovery_trigger": {
        "queue": "selfhealing.critical",
        "routing_key": "selfhealing.critical",
    },
    "selfhealing.celery_tasks.monitor_recovery_health": {
        "queue": "selfhealing.critical",
        "routing_key": "selfhealing.critical",
    },
    "selfhealing.celery_tasks.check_circuit_breaker_recovery": {
        "queue": "selfhealing.critical",
        "routing_key": "selfhealing.critical",
    },
}
```

### 2.2 Consumer 사용법

```python
# myproject/celery.py — 분리 후
from celery import Celery

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# selfhealing Beat 스케줄 1줄 merge
from selfhealing.celery_app import (
    SELFHEALING_BEAT_SCHEDULE,
    SELFHEALING_QUEUES,
    SELFHEALING_TASK_ROUTES,
)

# Consumer 자체 Beat 스케줄
app.conf.beat_schedule = {
    "retry-failed-emails": {
        "task": "shopping.tasks.email_tasks.retry_failed_emails_task",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 300},
    },
    # ... consumer 태스크만
}

# selfhealing 스케줄 merge
app.conf.beat_schedule.update(SELFHEALING_BEAT_SCHEDULE)

# 큐/라우팅 merge
app.conf.task_queues = {
    # consumer 큐
    "default": {"exchange": "default", "exchange_type": "direct", "routing_key": "default"},
    "payment_critical": {"exchange": "payment_critical", "exchange_type": "direct", "routing_key": "payment.critical"},
    # selfhealing 큐 merge
    **SELFHEALING_QUEUES,
}

app.conf.task_routes = {
    # consumer 라우팅
    "shopping.tasks.payment_tasks.*": {"queue": "payment_critical"},
    # selfhealing 라우팅 merge
    **SELFHEALING_TASK_ROUTES,
}
```

### 2.3 AppConfig 자동 등록 (대안)

320에서 구현하는 auto-config와 연동하여, AppConfig.ready()에서 자동으로 Beat schedule을 merge할 수도 있다:

```python
# apps.py — ready()에서 자동 merge (대안)
def _auto_merge_beat_schedule(self):
    """Beat schedule 자동 merge (SELFHEALING_AUTO_BEAT=True일 때)."""
    if not getattr(settings, "SELFHEALING_AUTO_BEAT", False):
        return

    try:
        from celery import current_app
        from selfhealing.celery_app import (
            SELFHEALING_BEAT_SCHEDULE,
            SELFHEALING_QUEUES,
            SELFHEALING_TASK_ROUTES,
        )

        current_app.conf.beat_schedule.update(SELFHEALING_BEAT_SCHEDULE)
        # ... 큐/라우팅도 merge
    except ImportError:
        pass
```

**권장**: 명시적 merge (consumer celery.py에서 1줄) — Celery 설정은 명시적인 것이 디버깅에 유리

---

## 3. 하위 호환성

- 기존 consumer가 Beat 태스크를 직접 정의한 경우 → 중복 키 없도록 selfhealing 키에 `selfhealing-` 접두사 추가
- `SELFHEALING_BEAT_SCHEDULE`에서 특정 태스크 제거하고 싶을 때:
  ```python
  schedule = dict(SELFHEALING_BEAT_SCHEDULE)
  del schedule["selfhealing-chaos-hunt-zombie-experiments"]  # Chaos 불필요
  app.conf.beat_schedule.update(schedule)
  ```

---

## 4. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | SELFHEALING_BEAT_SCHEDULE import | 15+ 태스크 키 존재 확인 |
| 2 | Consumer beat_schedule.update() | selfhealing + consumer 태스크 모두 등록 확인 |
| 3 | 큐 정의 완전성 | selfhealing.critical, chaos, chaos_monitoring 존재 확인 |
| 4 | 라우팅 정의 완전성 | critical 태스크 4개 라우팅 확인 |
| 5 | 태스크 제거 가능 | dict에서 del 후 등록 안 됨 확인 |
