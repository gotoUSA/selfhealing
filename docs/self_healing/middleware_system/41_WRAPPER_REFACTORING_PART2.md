# 41. Wrapper 리팩토링 PART 2: Task Wrapper 일관성

> **작성일**: 2026-01-16  
> **대상 파일들**:  
> - `tasks/chaos_scheduler.py` (일관된 패턴 - 참조용)
> - `tasks/cleanup_tasks.py` (리팩토링 대상)
> - `tasks/daily_report.py` (리팩토링 대상)
> - `tasks/config_apply.py` (일관된 패턴 - 참조용)
> - `tasks/governance.py` (일관된 패턴 - 참조용)

---

## 1. 현재 상태 분석

### 1.1 패턴 비교

#### ✅ 일관된 패턴 (Thin Task, Fat Service)

**chaos_scheduler.py** (Lines 37-85):
```python
def run_scheduled_experiments() -> Dict[str, Any]:
    """
    Run scheduled chaos experiments.
    
    This function is a thin wrapper that delegates to ChaosExecutionService.
    All governance checks and safety validations are performed in the service layer.
    """
    from selfhealing.services.execution_services import get_chaos_execution_service
    
    try:
        service = get_chaos_execution_service()
        result = service.run_scheduled_experiments()
        result_dict = result.to_dict()
        
        # === Audit 기록 (Phase 4) ===
        try:
            from selfhealing.services.audit_helpers import log_chaos_scheduler_audit
            log_chaos_scheduler_audit(...)
        except Exception as audit_error:
            logger.debug(f"[ChaosScheduler] Audit logging failed: {audit_error}")
        
        return result_dict
    except Exception as e:
        # Audit 기록 (실패)
        raise
```

**config_apply.py** (Lines 27-80):
```python
@shared_task(...)
def apply_pending_config_changes(self):
    """
    This task is a thin wrapper that delegates to ConfigApplyService.
    """
    from selfhealing.services.execution_services import get_config_apply_service

    task_id = self.request.id
    
    try:
        service = get_config_apply_service()
        result = service.apply_pending_changes()
        # Audit 기록
        return result
    except Exception as e:
        # Audit 기록 (실패)
        raise self.retry(exc=e)
```

**governance.py** (Lines 38-100):
```python
def check_emergency_mode_expiry(task_id: str = None) -> Dict[str, Any]:
    """
    This function is a thin wrapper that delegates to GovernanceService.
    """
    from selfhealing.services.governance_service import get_governance_service

    try:
        service = get_governance_service()
        result = service.check_emergency_mode_expiry()
        result_dict = result.to_dict()
        # Audit 기록
        return result_dict
    except Exception as e:
        # Audit 기록 (실패)
        raise
```

#### ❌ 불일치 패턴 (클래스 기반 + 비즈니스 로직 포함)

**cleanup_tasks.py** (Lines 35-95):
```python
class ArchiveOldDLQEntriesTask(BaseNotifyingTask):
    """
    30일 이상 된 해결된 DLQ 항목을 아카이브.
    """
    name = "selfhealing.archive_old_dlq_entries"
    
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
        cooldown_seconds=86400,
    )

    def run(self, older_than_days: int = 30) -> Dict[str, Any]:
        """아카이브 태스크 실행."""
        logger.info(f"[ArchiveOldDLQEntries] Starting archive...")
        
        try:
            from selfhealing.services.dlq_service import get_dlq_service
            
            service = get_dlq_service()
            count = service.archive_old_entries(older_than_days=older_than_days)
            
            # ⚠️ 비즈니스 로직이 Task 내부에 있음
            return {
                "success": True,
                "archived_count": count,
                "older_than_days": older_than_days,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
```

**daily_report.py** (Lines 1-638) - Fat Task 문제:
```python
# 638줄 파일에 비즈니스 로직이 대량 포함

@dataclass
class TaskResultEntry:  # 비즈니스 객체
    task_name: str
    result: Dict[str, Any]
    timestamp: datetime
    severity: str = "info"

@dataclass
class DailyAutonomousReport:  # 비즈니스 객체 (100줄+)
    date: datetime
    archived_count: int = 0
    expired_count: int = 0
    # ... 많은 필드들
    
    def add_entry(self, entry: TaskResultEntry) -> None: ...
    def _update_counts_from_entry(self, entry): ...
    # ... 많은 메서드들

# 집계 로직 (50줄+)
def aggregate_daily_results(...) -> DailyAutonomousReport: ...

# 포매팅 로직 (100줄+)
def format_report_for_slack(report: DailyAutonomousReport) -> str: ...
def format_report_for_email(report: DailyAutonomousReport) -> str: ...

# 전송 로직 (50줄+)
def send_report_notification(...): ...

# 메인 함수 (100줄+)
def generate_daily_autonomous_report(date, channels) -> Dict[str, Any]:
    # 모든 로직이 여기서 실행됨
    ...
```

### 1.2 문제점 요약

| 파일 | 패턴 | 문제점 |
|---|---|---|
| `cleanup_tasks.py` | 클래스 기반 | BaseNotifyingTask 사용, 결과 딕셔너리 직접 구성 |
| `daily_report.py` | Fat Task | 638줄, 비즈니스 로직 + 데이터 객체 + 포매팅 모두 포함 |

---

## 2. 리팩토링 계획

### 2.1 cleanup_tasks.py 리팩토링

#### 2.1.1 현재 구조

```
cleanup_tasks.py (427줄)
├── ArchiveOldDLQEntriesTask (클래스)
├── CleanupExpiredConfigTask (클래스)
├── ExpireApprovalRequestsTask (클래스)
└── PurgeArchivedDLQEntriesTask (클래스)
```

#### 2.1.2 목표 구조

```
services/
└── cleanup_service.py (NEW)
    ├── CleanupService
    │   ├── archive_old_dlq_entries()
    │   ├── cleanup_expired_config()
    │   ├── expire_approval_requests()
    │   └── purge_archived_dlq_entries()
    └── get_cleanup_service()

tasks/
└── cleanup_tasks.py (REFACTORED - Thin Wrapper만)
    ├── archive_old_dlq_entries() -> CleanupService 위임
    ├── cleanup_expired_config() -> CleanupService 위임
    ├── expire_approval_requests() -> CleanupService 위임
    └── purge_archived_dlq_entries() -> CleanupService 위임
```

#### 2.1.3 CleanupService 생성

```python
# services/cleanup_service.py

"""
Cleanup Service

Handles cleanup and archival operations for DLQ, Config, and Approvals.

Thin Task, Fat Service 원칙:
- Task는 단순 위임자 역할
- 모든 비즈니스 로직은 이 서비스에서 처리
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Cleanup 작업 결과."""
    success: bool
    operation: str
    count: int = 0
    error: Optional[str] = None
    details: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": self.success,
            "operation": self.operation,
            f"{self.operation}_count": self.count,
        }
        if self.error:
            result["error"] = self.error
        if self.details:
            result.update(self.details)
        return result


class CleanupService:
    """
    Cleanup 및 아카이브 작업을 처리하는 서비스.
    
    모든 cleanup_tasks.py 태스크의 비즈니스 로직을 담당합니다.
    """
    
    def archive_old_dlq_entries(
        self, 
        older_than_days: int = 30,
    ) -> CleanupResult:
        """
        30일 이상 된 해결된 DLQ 항목을 아카이브.
        
        Args:
            older_than_days: 아카이브 기준 일수
            
        Returns:
            CleanupResult with archived count
        """
        logger.info(
            f"[CleanupService] Archiving DLQ entries older than {older_than_days} days"
        )
        
        try:
            from selfhealing.services.dlq_service import get_dlq_service
            
            dlq_service = get_dlq_service()
            count = dlq_service.archive_old_entries(older_than_days=older_than_days)
            
            logger.info(f"[CleanupService] Archived {count} DLQ entries")
            
            return CleanupResult(
                success=True,
                operation="archived",
                count=count,
                details={"older_than_days": older_than_days},
            )
            
        except Exception as e:
            logger.error(f"[CleanupService] Archive failed: {e}", exc_info=True)
            return CleanupResult(
                success=False,
                operation="archived",
                error=str(e),
            )
    
    def cleanup_expired_config(
        self,
        older_than_hours: int = 24,
    ) -> CleanupResult:
        """만료된 Pending Config 항목 정리."""
        logger.info(
            f"[CleanupService] Cleaning up configs older than {older_than_hours} hours"
        )
        
        try:
            from selfhealing.services.pending_config import get_pending_config_service
            
            pending_service = get_pending_config_service()
            count = pending_service.cleanup_expired(max_age_hours=older_than_hours)
            
            return CleanupResult(
                success=True,
                operation="expired",
                count=count,
                details={"older_than_hours": older_than_hours},
            )
            
        except Exception as e:
            logger.error(f"[CleanupService] Cleanup failed: {e}", exc_info=True)
            return CleanupResult(
                success=False,
                operation="expired",
                error=str(e),
            )
    
    def expire_approval_requests(
        self,
        older_than_hours: int = 72,
    ) -> CleanupResult:
        """72시간 이상 대기 중인 승인 요청 만료."""
        # ... 구현
        pass
    
    def purge_archived_dlq_entries(
        self,
        older_than_days: int = 90,
        dry_run: bool = False,
    ) -> CleanupResult:
        """90일 이상 된 아카이브 항목 영구 삭제 (고위험)."""
        # ... 구현
        pass


# Singleton
_cleanup_service: Optional[CleanupService] = None


def get_cleanup_service() -> CleanupService:
    """Get or create the cleanup service instance."""
    global _cleanup_service
    if _cleanup_service is None:
        _cleanup_service = CleanupService()
    return _cleanup_service
```

#### 2.1.4 cleanup_tasks.py 리팩토링

```python
# tasks/cleanup_tasks.py (REFACTORED)

"""
🧹 Cleanup Tasks (Thin Wrapper)

Thin Task, Fat Service 원칙:
- 이 파일의 함수들은 단순 위임자 역할만 수행
- 모든 비즈니스 로직은 CleanupService에서 처리

Reference: docs/self_healing/middleware_system/09_AUTONOMOUS_TASK_EXPANSION.md
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


# =============================================================================
# Thin Task Wrappers
# =============================================================================


def archive_old_dlq_entries(older_than_days: int = 30) -> Dict[str, Any]:
    """
    30일 이상 된 해결된 DLQ 항목을 아카이브.
    
    This function is a thin wrapper that delegates to CleanupService.
    """
    from selfhealing.services.cleanup_service import get_cleanup_service
    
    try:
        service = get_cleanup_service()
        result = service.archive_old_dlq_entries(older_than_days=older_than_days)
        result_dict = result.to_dict()
        
        # === Audit 기록 ===
        try:
            from selfhealing.services.audit_helpers import log_cleanup_audit
            log_cleanup_audit(
                operation="archive_dlq",
                status="completed" if result.success else "failed",
                count=result.count,
            )
        except Exception:
            pass
        
        return result_dict
        
    except Exception as e:
        logger.error(f"[CleanupTask] archive_old_dlq_entries failed: {e}")
        raise


def cleanup_expired_config(older_than_hours: int = 24) -> Dict[str, Any]:
    """
    만료된 Pending Config 항목 정리.
    
    This function is a thin wrapper that delegates to CleanupService.
    """
    from selfhealing.services.cleanup_service import get_cleanup_service
    
    service = get_cleanup_service()
    result = service.cleanup_expired_config(older_than_hours=older_than_hours)
    return result.to_dict()


def expire_approval_requests(older_than_hours: int = 72) -> Dict[str, Any]:
    """
    72시간 이상 대기 중인 승인 요청 만료.
    
    This function is a thin wrapper that delegates to CleanupService.
    """
    from selfhealing.services.cleanup_service import get_cleanup_service
    
    service = get_cleanup_service()
    result = service.expire_approval_requests(older_than_hours=older_than_hours)
    return result.to_dict()


def purge_archived_dlq_entries(
    older_than_days: int = 90,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    90일 이상 된 아카이브 항목 영구 삭제 (고위험).
    
    This function is a thin wrapper that delegates to CleanupService.
    """
    from selfhealing.services.cleanup_service import get_cleanup_service
    
    service = get_cleanup_service()
    result = service.purge_archived_dlq_entries(
        older_than_days=older_than_days,
        dry_run=dry_run,
    )
    return result.to_dict()


# =============================================================================
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task
    
    @shared_task(
        name="selfhealing.archive_old_dlq_entries",
        bind=True,
        max_retries=2,
    )
    def archive_old_dlq_entries_task(self, older_than_days: int = 30):
        """Celery task wrapper for archive_old_dlq_entries."""
        return archive_old_dlq_entries(older_than_days)
    
    @shared_task(
        name="selfhealing.cleanup_expired_config",
        bind=True,
    )
    def cleanup_expired_config_task(self, older_than_hours: int = 24):
        """Celery task wrapper for cleanup_expired_config."""
        return cleanup_expired_config(older_than_hours)
    
    # ... 다른 태스크들

except ImportError:
    logger.debug("[CleanupTasks] Celery not available")
```

---

### 2.2 daily_report.py 리팩토링

#### 2.2.1 현재 구조 (638줄)

```
daily_report.py (638줄)
├── TaskResultEntry (dataclass)
├── DailyAutonomousReport (dataclass, 100줄+)
├── aggregate_daily_results() (50줄)
├── format_report_for_slack() (100줄)
├── format_report_for_email() (100줄)
├── send_report_notification() (50줄)
├── generate_daily_autonomous_report() (100줄)
└── Celery task registration
```

#### 2.2.2 목표 구조

```
services/
└── daily_report/
    ├── __init__.py
    ├── models.py           # TaskResultEntry, DailyAutonomousReport
    ├── aggregator.py       # aggregate_daily_results()
    ├── formatters.py       # format_report_for_slack(), format_report_for_email()
    └── service.py          # DailyReportService

tasks/
└── daily_report.py         # Thin Wrapper만 (50줄 이하)
```

#### 2.2.3 서비스 분리

**models.py**:
```python
# services/daily_report/models.py

"""Daily Report Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class TaskResultEntry:
    """Individual task result entry for aggregation."""
    task_name: str
    result: Dict[str, Any]
    timestamp: datetime
    severity: str = "info"


@dataclass
class DailyAutonomousReport:
    """Daily autonomous operations summary report."""
    date: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    archived_count: int = 0
    expired_count: int = 0
    purged_count: int = 0
    # ... 현재 필드들 유지
    
    entries: List[TaskResultEntry] = field(default_factory=list)
    
    def add_entry(self, entry: TaskResultEntry) -> None:
        """Add a task result entry."""
        self.entries.append(entry)
        self._update_counts_from_entry(entry)
    
    # ... 현재 메서드들 유지
```

**aggregator.py**:
```python
# services/daily_report/aggregator.py

"""Daily Report Aggregation Logic."""

from datetime import datetime
from typing import List, Optional

from .models import DailyAutonomousReport, TaskResultEntry


def aggregate_daily_results(
    date: Optional[datetime] = None,
    cache_key_prefix: str = "selfhealing:daily_report",
) -> DailyAutonomousReport:
    """Aggregate cached task results into a daily report."""
    # ... 현재 로직 이동
```

**formatters.py**:
```python
# services/daily_report/formatters.py

"""Report Formatting for Various Channels."""

from .models import DailyAutonomousReport


def format_report_for_slack(report: DailyAutonomousReport) -> str:
    """Format report for Slack notification."""
    # ... 현재 로직 이동


def format_report_for_email(report: DailyAutonomousReport) -> str:
    """Format report for email notification."""
    # ... 현재 로직 이동
```

**service.py**:
```python
# services/daily_report/service.py

"""
Daily Report Service

Thin Task, Fat Service 원칙:
- 모든 일일 리포트 비즈니스 로직을 담당
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import DailyAutonomousReport
from .aggregator import aggregate_daily_results
from .formatters import format_report_for_slack, format_report_for_email

logger = logging.getLogger(__name__)


@dataclass
class ReportResult:
    """Report generation result."""
    success: bool
    report: Optional[DailyAutonomousReport] = None
    channels_sent: List[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "date": self.report.date.isoformat() if self.report else None,
            "channels_sent": self.channels_sent or [],
            "error": self.error,
            "summary": {
                "archived_count": self.report.archived_count if self.report else 0,
                "expired_count": self.report.expired_count if self.report else 0,
                # ... 기타 필드
            },
        }


class DailyReportService:
    """일일 리포트 생성 및 전송 서비스."""
    
    def generate_and_send_report(
        self,
        date: Optional[datetime] = None,
        channels: Optional[List[str]] = None,
    ) -> ReportResult:
        """
        일일 리포트 생성 및 전송.
        
        Args:
            date: 리포트 대상 날짜 (기본: 어제)
            channels: 전송 채널 목록 (기본: ["slack"])
            
        Returns:
            ReportResult
        """
        channels = channels or ["slack"]
        
        try:
            # 1. 집계
            report = aggregate_daily_results(date)
            
            # 2. 전송
            sent_channels = []
            for channel in channels:
                try:
                    self._send_to_channel(report, channel)
                    sent_channels.append(channel)
                except Exception as e:
                    logger.error(f"[DailyReport] Failed to send to {channel}: {e}")
            
            return ReportResult(
                success=True,
                report=report,
                channels_sent=sent_channels,
            )
            
        except Exception as e:
            logger.error(f"[DailyReport] Generation failed: {e}", exc_info=True)
            return ReportResult(success=False, error=str(e))
    
    def _send_to_channel(self, report: DailyAutonomousReport, channel: str) -> None:
        """채널로 리포트 전송."""
        if channel == "slack":
            message = format_report_for_slack(report)
            # Slack 전송 로직
        elif channel == "email":
            message = format_report_for_email(report)
            # Email 전송 로직


# Singleton
_daily_report_service: Optional[DailyReportService] = None


def get_daily_report_service() -> DailyReportService:
    """Get or create the daily report service instance."""
    global _daily_report_service
    if _daily_report_service is None:
        _daily_report_service = DailyReportService()
    return _daily_report_service
```

#### 2.2.4 daily_report.py 리팩토링 (Thin Wrapper)

```python
# tasks/daily_report.py (REFACTORED - 50줄 이하)

"""
Daily Autonomous Report Task (Thin Wrapper)

Thin Task, Fat Service 원칙:
- 이 파일은 단순 위임자 역할만 수행
- 모든 비즈니스 로직은 DailyReportService에서 처리

Reference: docs/self_healing/middleware_system/08_NOTIFICATION_ARCHITECTURE.md
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def generate_daily_autonomous_report(
    date: Optional[datetime] = None,
    channels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate and send daily autonomous report.
    
    This function is a thin wrapper that delegates to DailyReportService.
    """
    from selfhealing.services.daily_report.service import get_daily_report_service
    
    try:
        service = get_daily_report_service()
        result = service.generate_and_send_report(date=date, channels=channels)
        result_dict = result.to_dict()
        
        # === Audit 기록 ===
        try:
            from selfhealing.services.audit_helpers import log_daily_report_audit
            log_daily_report_audit(
                status="completed" if result.success else "failed",
                channels_sent=result.channels_sent,
            )
        except Exception:
            pass
        
        return result_dict
        
    except Exception as e:
        logger.error(f"[DailyReport] Task failed: {e}")
        raise


# =============================================================================
# Celery Task Registration
# =============================================================================

try:
    from celery import shared_task

    @shared_task(
        name="selfhealing.tasks.daily_report.generate_daily_autonomous_report",
        bind=False,
        max_retries=2,
        default_retry_delay=300,
    )
    def generate_daily_autonomous_report_task(
        date_str: Optional[str] = None,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Celery task wrapper for daily report generation."""
        date = datetime.fromisoformat(date_str) if date_str else None
        return generate_daily_autonomous_report(date=date, channels=channels)

except ImportError:
    logger.debug("[DailyReport] Celery not available")
```

---

## 3. 마이그레이션 단계

### 3.1 cleanup_tasks.py 마이그레이션

| 단계 | 작업 | 예상 시간 |
|---|---|---|
| 1 | `CleanupService` 생성 | 2시간 |
| 2 | 기존 비즈니스 로직 이동 | 2시간 |
| 3 | `cleanup_tasks.py` Thin Wrapper로 변경 | 1시간 |
| 4 | 테스트 작성 및 검증 | 2시간 |
| 5 | 기존 테스트 업데이트 | 1시간 |

### 3.2 daily_report.py 마이그레이션

| 단계 | 작업 | 예상 시간 |
|---|---|---|
| 1 | `services/daily_report/` 패키지 생성 | 1시간 |
| 2 | `models.py` 분리 | 1시간 |
| 3 | `aggregator.py` 분리 | 1시간 |
| 4 | `formatters.py` 분리 | 2시간 |
| 5 | `DailyReportService` 생성 | 2시간 |
| 6 | `daily_report.py` Thin Wrapper로 변경 | 1시간 |
| 7 | 테스트 작성 및 검증 | 3시간 |

---

## 4. 검증 체크리스트

### 4.1 cleanup_tasks.py

- [ ] `CleanupService` 생성 완료
- [ ] 4개 함수 모두 Thin Wrapper로 변경
- [ ] `BaseNotifyingTask` 의존성 제거
- [ ] Celery task 등록 정상 동작
- [ ] Beat Schedule 정상 동작
- [ ] 기존 테스트 통과

### 4.2 daily_report.py

- [ ] `services/daily_report/` 패키지 생성 완료
- [ ] 모델, 집계, 포매팅 분리 완료
- [ ] `DailyReportService` 생성 완료
- [ ] 638줄 → 50줄 이하로 축소
- [ ] Celery task 등록 정상 동작
- [ ] 기존 테스트 통과

---

## 5. 예상 효과

| 지표 | Before | After |
|---|---|---|
| cleanup_tasks.py 줄 수 | 427 | ~100 |
| daily_report.py 줄 수 | 638 | ~50 |
| Task 패턴 일관성 | 60% | 100% |
| 비즈니스 로직 테스트 용이성 | 낮음 | 높음 |
| Celery 의존성 분리 | 불가 | 가능 |
