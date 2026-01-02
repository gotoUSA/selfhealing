# Self-Healing 자율 태스크 확장

> **Version**: 1.0.0
> **Created**: 2026-01-02
> **Status**: 설계 완료, 구현 예정
> **Category**: 자율 운영 태스크 확장
> **Prerequisite**: [08_NOTIFICATION_ARCHITECTURE.md](08_NOTIFICATION_ARCHITECTURE.md)

---

## 📋 목차

1. [개요](#1-개요)
2. [고아 모듈 분석 결과](#2-고아-모듈-분석-결과)
3. [신규 Celery 태스크 후보](#3-신규-celery-태스크-후보)
4. [3개 자율 주행 레인](#4-3개-자율-주행-레인)
5. [Beat Schedule 설계](#5-beat-schedule-설계)
6. [구현 상세](#6-구현-상세)
7. [테스트 계획](#7-테스트-계획)

---

## 1. 개요

### 1.1 배경

현재 `04_AUTONOMOUS_OPS.md`에는 **6개의 핵심 Celery 태스크**가 정의되어 있지만,
`CODE_DEPENDENCY_ANALYSIS.md`의 고아 모듈 분석 결과 **8개의 추가 Celery 태스크 후보**가 발견되었습니다.

이 문서는 누락된 서비스들을 Celery 태스크로 등록하고,
**3개 레인 아키텍처**로 체계적으로 관리하는 방안을 정의합니다.

### 1.2 목표

```
┌─────────────────────────────────────────────────────────────────┐
│                    자율 태스크 확장 목표                          │
├─────────────────────────────────────────────────────────────────┤
│ ✓ 고아 모듈에서 발굴한 8개 서비스를 Celery 태스크로 연결           │
│ ✓ 3개 레인 (청소부, 지능, 증명)으로 태스크 체계화                  │
│ ✓ BaseNotifyingTask 기반 통합 알림 적용                          │
│ ✓ Beat Schedule로 자동 실행 스케줄 정의                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 고아 모듈 분석 결과

### 2.1 분석 범위

`CODE_DEPENDENCY_ANALYSIS.md`에서 **services/** 폴더 내 고아 모듈(외부 참조 없음) 중
`cleanup`, `process`, `sync`, `analyze` 키워드가 있는 메서드를 분석했습니다.

### 2.2 발굴된 서비스

| 서비스 | 파일 | 메서드 | 현재 상태 |
|--------|------|--------|----------|
| `PendingConfigService` | `services/pending_config.py` | `cleanup_expired()` L286 | ❌ Celery 미등록 |
| `ApprovalMixin` | `services/runtime_config/approval.py` | `expire_old_requests()` L176 | ❌ Celery 미등록 |
| `DLQService` | `services/dlq_service.py` | `archive_old_entries()` L624 | ❌ Celery 미등록 |
| `DLQService` | `services/dlq_service.py` | `purge_archived()` L665 | ❌ Celery 미등록 |
| `LearningService` | `services/learning/service.py` | `get_cross_stage_insights()` | ❌ Celery 미등록 |
| `FinOpsService` | `services/finops/service.py` | `generate_report()` L216 | ❌ Celery 미등록 |
| `ComplianceService` | `services/compliance/service.py` | 전체 서비스 | ❌ Celery 미등록 |
| `ForensicAnalyzer` | `tasks/drift_detection.py` | `analyze_pending()` | ⚠️ 알림 누락 |

### 2.3 기존 태스크 현황 (04_AUTONOMOUS_OPS.md)

현재 등록된 **6개 핵심 태스크**:

| 태스크 | 주기 | 역할 |
|--------|------|------|
| `replay_failed_operations` | 5분 | DLQ 재시도 |
| `cleanup_old_failed_operations` | 매일 02:00 | DLQ 정리 |
| `check_recovery_transitions` | 2분 | CB 복구 체크 |
| `collect_self_healing_metrics` | 30분 | 메트릭 수집 |
| `check_and_report_sla_breaches` | 1시간 | SLA 위반 체크 |
| `check_emergency_mode_expiry` | 10분 | 긴급 모드 만료 |

---

## 3. 신규 Celery 태스크 후보

### 3.1 8개 신규 태스크 정의

| # | 태스크명 | 원본 서비스 | 위험도 | 알림 시점 | 우선순위 |
|---|---------|------------|--------|----------|---------|
| 1 | `archive_old_dlq_entries` | `DLQService.archive_old_entries()` | 🟡 저 | AFTER | P1 |
| 2 | `purge_archived_dlq_entries` | `DLQService.purge_archived()` | 🔴 고 | BEFORE | P1 |
| 3 | `cleanup_expired_config` | `PendingConfigService.cleanup_expired()` | 🟡 저 | AFTER | P2 |
| 4 | `expire_approval_requests` | `ApprovalMixin.expire_old_requests()` | 🟡 저 | AFTER | P2 |
| 5 | `generate_finops_report` | `FinOpsService.generate_report()` | 🟢 없 | AFTER | P2 |
| 6 | `run_compliance_check` | `ComplianceService` | 🟢 없 | AFTER | P2 |
| 7 | `analyze_cross_stage_insights` | `LearningService.get_cross_stage_insights()` | 🟢 없 | AFTER | P3 |
| 8 | `analyze_forensic_pending` | `ForensicAnalyzer.analyze_pending()` | 🔵 상태 | REALTIME | P1 |

### 3.2 상세 스펙

#### 3.2.1 archive_old_dlq_entries (P1)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class ArchiveOldDLQEntriesTask(BaseNotifyingTask):
    """30일 이상 된 해결된 DLQ 항목을 아카이브."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
        cooldown_seconds=86400,  # 24시간
    )

    def run(self, older_than_days: int = 30):
        from selfhealing.services.dlq_service import get_dlq_service
        service = get_dlq_service()

        count = service.archive_old_entries(older_than_days=older_than_days)

        return {
            "success": True,
            "archived_count": count,
            "older_than_days": older_than_days,
        }

    def _get_summary_message(self, result):
        return f"📦 DLQ 아카이브: {result['archived_count']}건 ({result['older_than_days']}일 경과)"
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매일 03:00 |
| 큐 | `maintenance` |
| 기본 파라미터 | `older_than_days=30` |
| 알림 | 일일 요약에 포함 |

#### 3.2.2 purge_archived_dlq_entries (P1) ⚠️ 고위험

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class PurgeArchivedDLQEntriesTask(BaseNotifyingTask):
    """90일 이상 된 아카이브 항목을 영구 삭제."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.BEFORE,
        requires_approval=True,
        default_severity="critical",
        cooldown_seconds=3600,  # 1시간
        escalate_on_emergency=False,  # 긴급 모드에서도 승인 필요
    )

    def run(self, older_than_days: int = 90):
        from selfhealing.services.dlq_service import get_dlq_service
        service = get_dlq_service()

        count = service.purge_archived(older_than_days=older_than_days)

        return {
            "success": True,
            "purged_count": count,
            "older_than_days": older_than_days,
            "warning": "PERMANENT DELETION - UNRECOVERABLE",
        }

    def _get_summary_message(self, result):
        return f"⚠️ DLQ 영구 삭제: {result['purged_count']}건 (복구 불가!)"
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매주 일요일 04:00 |
| 큐 | `critical_maintenance` |
| 기본 파라미터 | `older_than_days=90` |
| 알림 | 사전 승인 필수 |
| 특이사항 | Emergency Level 3에서도 승인 필요 |

#### 3.2.3 cleanup_expired_config (P2)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class CleanupExpiredConfigTask(BaseNotifyingTask):
    """만료된 Pending Config 항목 정리."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
    )

    def run(self, older_than_hours: int = 24):
        from selfhealing.services.pending_config import get_pending_config_service
        service = get_pending_config_service()

        count = service.cleanup_expired(older_than_hours=older_than_hours)

        return {
            "success": True,
            "expired_count": count,
            "older_than_hours": older_than_hours,
        }

    def _get_summary_message(self, result):
        return f"🧹 만료 설정 정리: {result['expired_count']}건"
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매일 02:30 |
| 큐 | `maintenance` |
| 알림 | 일일 요약에 포함 |

#### 3.2.4 expire_approval_requests (P2)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class ExpireApprovalRequestsTask(BaseNotifyingTask):
    """72시간 이상 대기 중인 승인 요청 만료 처리."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        threshold=5,  # 5건 이상일 때만 알림
        threshold_field="expired_count",
        default_severity="warning",
    )

    def run(self, older_than_hours: int = 72):
        from selfhealing.services.runtime_config import get_approval_service
        service = get_approval_service()

        count = service.expire_old_requests(older_than_hours=older_than_hours)

        return {
            "success": True,
            "expired_count": count,
            "older_than_hours": older_than_hours,
        }

    def _get_summary_message(self, result):
        return f"⏰ 승인 요청 만료: {result['expired_count']}건 ({result['older_than_hours']}시간 경과)"
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매일 06:00 |
| 큐 | `maintenance` |
| 알림 | 5건 이상일 때만 (임계값 기반) |

#### 3.2.5 generate_finops_report (P2)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class GenerateFinOpsReportTask(BaseNotifyingTask):
    """FinOps 비용 분석 리포트 생성."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AFTER,
        aggregate=False,  # 매주 1회라 즉시 발송
        default_severity="info",
        channels=["slack", "email"],
    )

    def run(self, period: str = "weekly"):
        from selfhealing.services.finops import get_finops_service
        service = get_finops_service()

        report = service.generate_report(period=period)

        return {
            "success": True,
            "report_id": report["id"],
            "period": period,
            "total_cost": report.get("total_cost"),
            "savings": report.get("savings"),
        }

    def _get_summary_message(self, result):
        return (
            f"💰 FinOps 리포트 생성 완료\n"
            f"• 기간: {result['period']}\n"
            f"• 총 비용: ${result.get('total_cost', 'N/A')}\n"
            f"• 절감액: ${result.get('savings', 'N/A')}"
        )
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매주 월요일 08:00 |
| 큐 | `reports` |
| 알림 | 즉시 발송 (Slack + Email) |

#### 3.2.6 run_compliance_check (P2)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class RunComplianceCheckTask(BaseNotifyingTask):
    """규정 준수 상태 점검."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AFTER,
        threshold=0,  # 위반 있을 때만
        threshold_field="violation_count",
        default_severity="warning",
        channels=["slack", "email"],
    )

    def run(self, check_type: str = "all"):
        from selfhealing.services.compliance import get_compliance_service
        service = get_compliance_service()

        result = service.run_check(check_type=check_type)

        return {
            "success": True,
            "check_type": check_type,
            "total_checks": result["total"],
            "passed_count": result["passed"],
            "violation_count": result["violations"],
            "violations": result.get("violation_details", []),
        }

    def _get_severity(self, result):
        if result.get("violation_count", 0) > 10:
            return "critical"
        elif result.get("violation_count", 0) > 0:
            return "warning"
        return "info"

    def _get_summary_message(self, result):
        if result.get("violation_count", 0) == 0:
            return f"✅ 규정 준수 점검 완료: {result['total_checks']}개 항목 모두 통과"
        return (
            f"⚠️ 규정 준수 점검 결과\n"
            f"• 총 점검: {result['total_checks']}건\n"
            f"• 통과: {result['passed_count']}건\n"
            f"• 위반: {result['violation_count']}건"
        )
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매일 07:00 |
| 큐 | `compliance` |
| 알림 | 위반 있을 때만 (임계값 기반) |

#### 3.2.7 analyze_cross_stage_insights (P3)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class AnalyzeCrossStageInsightsTask(BaseNotifyingTask):
    """Stage 간 학습 인사이트 분석."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        threshold=3,  # 인사이트 3개 이상일 때만
        threshold_field="insight_count",
        default_severity="info",
    )

    def run(self):
        from selfhealing.services.learning import get_learning_service
        service = get_learning_service()

        insights = service.get_cross_stage_insights()

        return {
            "success": True,
            "insight_count": len(insights),
            "insights": insights,
            "recommendations": [i.get("recommendation") for i in insights if i.get("recommendation")],
        }

    def _get_summary_message(self, result):
        return f"🧠 학습 인사이트: {result['insight_count']}개 발견"
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 매일 22:00 |
| 큐 | `analysis` |
| 알림 | 인사이트 3개 이상일 때만 |

#### 3.2.8 analyze_forensic_pending (P1)

```python
@shared_task(bind=True, base=BaseNotifyingTask)
class AnalyzeForensicPendingTask(BaseNotifyingTask):
    """Pending 상태 장기 체류 항목 포렌식 분석."""

    notification_policy = NotificationPolicy(
        timing=NotificationTiming.REALTIME,
        threshold=10,  # 10개 이상일 때만
        threshold_field="suspicious_count",
        default_severity="warning",
        cooldown_seconds=3600,  # 1시간
    )

    def run(self, threshold_minutes: int = 60):
        from selfhealing.tasks.drift_detection import ForensicAnalyzer
        analyzer = ForensicAnalyzer()

        result = analyzer.analyze_pending(threshold_minutes=threshold_minutes)

        return {
            "success": True,
            "suspicious_count": result.get("suspicious_count", 0),
            "stuck_patterns": result.get("patterns", []),
            "recommendations": result.get("recommendations", []),
        }

    def _get_severity(self, result):
        count = result.get("suspicious_count", 0)
        if count >= 50:
            return "critical"
        elif count >= 10:
            return "warning"
        return "info"

    def _get_summary_message(self, result):
        return (
            f"🔍 포렌식 분석 결과\n"
            f"• 의심 항목: {result['suspicious_count']}건\n"
            f"• 패턴: {len(result.get('stuck_patterns', []))}개 발견"
        )
```

| 항목 | 값 |
|------|-----|
| 스케줄 | 30분마다 |
| 큐 | `analysis` |
| 알림 | 의심 항목 10개 이상일 때 즉시 |

---

## 4. 3개 자율 주행 레인

### 4.1 레인 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                     자율 운영 레인 아키텍처                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  🧹 청소부 레인 (Cleanup & Expire)                               │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ archive_old_dlq_entries → cleanup_expired_config →          ││
│  │ expire_approval_requests → purge_archived_dlq_entries       ││
│  │                                                             ││
│  │ 알림: 일일 요약 리포트 (AFTER, AGGREGATED)                    ││
│  │ 예외: purge_archived는 사전 승인 필수 (BEFORE)                ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  🧠 지능 레인 (Analyze & Learn)                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ check_sla_drift → analyze_forensic_pending →                ││
│  │ analyze_cross_stage_insights → check_recovery_transitions   ││
│  │                                                             ││
│  │ 알림: 임계값 초과 시 즉시 (REALTIME, THRESHOLD-based)         ││
│  │ 학습 인사이트는 일일 요약에 포함                               ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  📋 증명 레인 (Compliance & Report)                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ run_compliance_check → generate_finops_report →             ││
│  │ collect_self_healing_metrics → generate_daily_report        ││
│  │                                                             ││
│  │ 알림: 위반 시 즉시, 리포트는 스케줄 발송 (AFTER)               ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 레인별 태스크 분류

| 레인 | 태스크 | 알림 전략 | 큐 |
|------|--------|----------|-----|
| **🧹 청소부** | `archive_old_dlq_entries` | 일일 요약 | `maintenance` |
| | `cleanup_expired_config` | 일일 요약 | `maintenance` |
| | `expire_approval_requests` | 일일 요약 (5건+) | `maintenance` |
| | `purge_archived_dlq_entries` | **사전 승인** | `critical_maintenance` |
| **🧠 지능** | `check_sla_drift` | 임계값 초과 즉시 | `analysis` |
| | `analyze_forensic_pending` | 임계값 초과 즉시 | `analysis` |
| | `analyze_cross_stage_insights` | 일일 요약 (3건+) | `analysis` |
| | `check_recovery_transitions` | 즉시 (상태 변화) | `realtime` |
| **📋 증명** | `run_compliance_check` | 위반 시 즉시 | `compliance` |
| | `generate_finops_report` | 스케줄 발송 | `reports` |
| | `collect_self_healing_metrics` | 로그만 | `metrics` |
| | `generate_daily_autonomous_report` | 매일 발송 | `reports` |

### 4.3 레인별 큐 구성

```python
# settings.py
CELERY_TASK_QUEUES = {
    # 청소부 레인
    'maintenance': {
        'exchange': 'selfhealing',
        'routing_key': 'maintenance',
        'priority': 3,  # 낮은 우선순위
    },
    'critical_maintenance': {
        'exchange': 'selfhealing',
        'routing_key': 'critical_maintenance',
        'priority': 8,  # 높은 우선순위
    },

    # 지능 레인
    'analysis': {
        'exchange': 'selfhealing',
        'routing_key': 'analysis',
        'priority': 5,
    },
    'realtime': {
        'exchange': 'selfhealing',
        'routing_key': 'realtime',
        'priority': 9,  # 최고 우선순위
    },

    # 증명 레인
    'compliance': {
        'exchange': 'selfhealing',
        'routing_key': 'compliance',
        'priority': 7,
    },
    'reports': {
        'exchange': 'selfhealing',
        'routing_key': 'reports',
        'priority': 2,
    },
    'metrics': {
        'exchange': 'selfhealing',
        'routing_key': 'metrics',
        'priority': 1,
    },
}
```

---

## 5. Beat Schedule 설계

### 5.1 전체 스케줄

```python
# celery.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # ═══════════════════════════════════════════════════════════════
    # 🧹 청소부 레인 (Cleanup & Expire)
    # ═══════════════════════════════════════════════════════════════

    'cleanup-expired-config': {
        'task': 'selfhealing.cleanup_expired_config',
        'schedule': crontab(hour=2, minute=30),
        'options': {'queue': 'maintenance'},
        'kwargs': {'older_than_hours': 24},
    },
    'archive-old-dlq-entries': {
        'task': 'selfhealing.archive_old_dlq_entries',
        'schedule': crontab(hour=3, minute=0),
        'options': {'queue': 'maintenance'},
        'kwargs': {'older_than_days': 30},
    },
    'expire-approval-requests': {
        'task': 'selfhealing.expire_approval_requests',
        'schedule': crontab(hour=6, minute=0),
        'options': {'queue': 'maintenance'},
        'kwargs': {'older_than_hours': 72},
    },
    'purge-archived-dlq-entries': {
        'task': 'selfhealing.purge_archived_dlq_entries',
        'schedule': crontab(hour=4, minute=0, day_of_week=0),  # 일요일
        'options': {'queue': 'critical_maintenance'},
        'kwargs': {'older_than_days': 90},
    },

    # ═══════════════════════════════════════════════════════════════
    # 🧠 지능 레인 (Analyze & Learn)
    # ═══════════════════════════════════════════════════════════════

    'check-recovery-transitions': {
        'task': 'selfhealing.check_recovery_transitions',
        'schedule': crontab(minute='*/2'),  # 2분마다
        'options': {'queue': 'realtime'},
    },
    'analyze-forensic-pending': {
        'task': 'selfhealing.analyze_forensic_pending',
        'schedule': crontab(minute='*/30'),  # 30분마다
        'options': {'queue': 'analysis'},
        'kwargs': {'threshold_minutes': 60},
    },
    'check-sla-drift': {
        'task': 'selfhealing.check_sla_drift',
        'schedule': crontab(hour='*/1'),  # 1시간마다
        'options': {'queue': 'analysis'},
    },
    'analyze-cross-stage-insights': {
        'task': 'selfhealing.analyze_cross_stage_insights',
        'schedule': crontab(hour=22, minute=0),
        'options': {'queue': 'analysis'},
    },

    # ═══════════════════════════════════════════════════════════════
    # 📋 증명 레인 (Compliance & Report)
    # ═══════════════════════════════════════════════════════════════

    'run-compliance-check': {
        'task': 'selfhealing.run_compliance_check',
        'schedule': crontab(hour=7, minute=0),
        'options': {'queue': 'compliance'},
        'kwargs': {'check_type': 'all'},
    },
    'generate-finops-report': {
        'task': 'selfhealing.generate_finops_report',
        'schedule': crontab(hour=8, minute=0, day_of_week=1),  # 월요일
        'options': {'queue': 'reports'},
        'kwargs': {'period': 'weekly'},
    },
    'collect-self-healing-metrics': {
        'task': 'selfhealing.collect_self_healing_metrics',
        'schedule': crontab(minute='*/30'),  # 30분마다
        'options': {'queue': 'metrics'},
    },
    'generate-daily-autonomous-report': {
        'task': 'selfhealing.generate_daily_autonomous_report',
        'schedule': crontab(hour=9, minute=0),
        'options': {'queue': 'reports'},
    },
}
```

### 5.2 스케줄 시간대 분배

```
시간대별 태스크 분포 (서버 시간 기준)

00:00 ─────────────────────────────────────────────
02:00 │ cleanup_old_failed_operations (기존)
02:30 │ cleanup_expired_config
03:00 │ archive_old_dlq_entries
04:00 │ purge_archived_dlq_entries (일요일만)
06:00 │ expire_approval_requests
07:00 │ run_compliance_check
08:00 │ generate_finops_report (월요일)
09:00 │ generate_daily_autonomous_report ──────────
      │
      │ ─── 업무 시간 (알림 최소화) ───
      │
21:00 │
22:00 │ analyze_cross_stage_insights
23:00 │
24:00 ─────────────────────────────────────────────

상시 실행:
  - check_recovery_transitions: 2분마다
  - replay_failed_operations: 5분마다
  - check_emergency_mode_expiry: 10분마다
  - collect_self_healing_metrics: 30분마다
  - analyze_forensic_pending: 30분마다
  - check_sla_drift: 1시간마다
```

---

## 6. 구현 상세

### 6.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── tasks/
│   ├── __init__.py
│   ├── base.py                    # BaseNotifyingTask 정의
│   ├── notification_policy.py     # NotificationPolicy 데이터클래스
│   ├── cleanup_tasks.py           # 🧹 청소부 레인 태스크
│   ├── intelligence_tasks.py      # 🧠 지능 레인 태스크
│   ├── compliance_tasks.py        # 📋 증명 레인 태스크
│   └── daily_report.py            # 일일 요약 리포트
└── adapters/
    └── celery/
        ├── tasks.py               # 기존 태스크 (마이그레이션 대상)
        └── beat_schedule.py       # Beat Schedule 정의
```

### 6.2 구현 순서

```
Phase 1: 기반 구조 (2시간)
├── tasks/notification_policy.py
├── tasks/base.py (BaseNotifyingTask)
└── tests/unit/tasks/test_base_notifying_task.py

Phase 2: 청소부 레인 (2시간)
├── tasks/cleanup_tasks.py
│   ├── ArchiveOldDLQEntriesTask
│   ├── CleanupExpiredConfigTask
│   ├── ExpireApprovalRequestsTask
│   └── PurgeArchivedDLQEntriesTask
└── tests/unit/tasks/test_cleanup_tasks.py

Phase 3: 지능 레인 (2시간)
├── tasks/intelligence_tasks.py
│   ├── CheckSLADriftTask (마이그레이션)
│   ├── AnalyzeForensicPendingTask
│   └── AnalyzeCrossStageInsightsTask
└── tests/unit/tasks/test_intelligence_tasks.py

Phase 4: 증명 레인 (2시간)
├── tasks/compliance_tasks.py
│   ├── RunComplianceCheckTask
│   └── GenerateFinOpsReportTask
└── tests/unit/tasks/test_compliance_tasks.py

Phase 5: 일일 리포트 (1시간)
├── tasks/daily_report.py
│   ├── DailyAutonomousReport
│   └── GenerateDailyAutonomousReportTask
└── adapters/celery/beat_schedule.py

Phase 6: 통합 테스트 (2시간)
├── tests/integration/test_autonomous_tasks.py
└── 문서 업데이트
```

### 6.3 마이그레이션 전략

기존 `adapters/celery/tasks.py`의 태스크를 점진적으로 마이그레이션:

| 기존 태스크 | 마이그레이션 대상 | 우선순위 |
|------------|-----------------|---------|
| `cleanup_old_failed_operations` | `CleanupOldDLQEntriesTask` | P2 |
| `check_and_report_sla_breaches` | `CheckSLADriftTask` | P1 |
| `notify_failsafe_recovery` | 유지 (이미 알림 있음) | - |

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 대상 | 테스트 항목 |
|------------|-----------|
| `NotificationPolicy` | 기본값, 임계값 체크, 쿨다운 계산 |
| `BaseNotifyingTask` | `should_notify()`, `_get_effective_timing()`, `_record_audit_trail()` |
| 각 태스크 | 정상 실행, 임계값 미달, 쿨다운 활성화, Emergency 연동 |

### 7.2 통합 테스트

```python
# tests/integration/test_autonomous_tasks.py

class TestAutonomousTasks:
    """자율 태스크 통합 테스트."""

    def test_cleanup_lane_daily_summary(self):
        """청소부 레인 태스크들이 일일 요약에 집계되는지."""
        ...

    def test_high_risk_task_requires_approval(self):
        """고위험 태스크가 승인 없이 실행되지 않는지."""
        ...

    def test_emergency_level_3_bypasses_aggregation(self):
        """Emergency Level 3에서 모든 알림이 즉시 발송되는지."""
        ...

    def test_audit_trail_records_notification(self):
        """알림 발송이 Audit Trail에 기록되는지."""
        ...
```

### 7.3 부하 테스트 연동

기존 Locust 테스트와 연동:

| Stage | 관련 태스크 | 테스트 시나리오 |
|-------|-----------|----------------|
| stage16 | `check_recovery_transitions` | CB 복구 상태 변화 알림 |
| stage26 | `analyze_forensic_pending` | Pool timeout 패턴 감지 |
| stage42 | `check_sla_drift` | SLA 위반 알림 발송 |

---

## 📎 관련 문서

- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 시스템 (기존 태스크)
- [08_NOTIFICATION_ARCHITECTURE.md](08_NOTIFICATION_ARCHITECTURE.md) - 알림 아키텍처 (선행 문서)
- [07_HYBRID_STORAGE_ARCHITECTURE.md](07_HYBRID_STORAGE_ARCHITECTURE.md) - Audit Trail 연동
- [CODE_DEPENDENCY_ANALYSIS.md](../../../docs/CODE_DEPENDENCY_ANALYSIS.md) - 고아 모듈 분석

---

## 📋 체크리스트

### 논의 내용 반영 확인

- [x] 고아 모듈 분석 결과 (8개 서비스 발굴)
- [x] 신규 Celery 태스크 후보 8개 정의
- [x] 3개 자율 주행 레인 아키텍처
  - [x] 청소부 레인 (Cleanup & Expire)
  - [x] 지능 레인 (Analyze & Learn)
  - [x] 증명 레인 (Compliance & Report)
- [x] 레인별 알림 전략
  - [x] 청소부: 일일 요약 (purge는 사전 승인)
  - [x] 지능: 임계값 초과 시 즉시
  - [x] 증명: 위반 시 즉시, 리포트는 스케줄
- [x] Beat Schedule 설계
- [x] 레인별 큐 구성
- [x] 구현 순서 및 파일 구조
- [x] 테스트 계획
- [x] 기존 태스크와의 마이그레이션 전략

### 구현 상태 (2026-01-02)

- [x] **Phase 1**: 기반 구조 - `base_notifying_task.py`, `daily_report.py`
- [x] **Phase 2**: 청소부 레인 - `cleanup_tasks.py` (4개 태스크)
- [x] **Phase 3**: 지능 레인 - `intelligence_tasks.py` (4개 태스크)
- [x] **Phase 4**: 증명 레인 - `compliance_tasks.py` (4개 태스크)
- [x] **테스트**: 단위 테스트 (63개) 모두 통과
- [ ] **Phase 5**: 일일 리포트 통합
- [ ] **Phase 6**: 통합 테스트
