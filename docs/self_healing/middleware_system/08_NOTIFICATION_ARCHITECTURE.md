# Self-Healing 통합 알림 아키텍처

> **Version**: 2.0.0
> **Created**: 2026-01-02
> **Updated**: 2026-01-02
> **Status**: Phase 1, 2 구현 완료
> **Category**: 자율 운영 알림 시스템

---

## 📋 목차

1. [개요](#1-개요)
2. [알림 시점 결정 매트릭스](#2-알림-시점-결정-매트릭스)
3. [알림 피로도 해결 3대 기술](#3-알림-피로도-해결-3대-기술)
4. [BaseNotifyingTask 아키텍처](#4-basenotifyingtask-아키텍처)
5. [Emergency Level 연동](#5-emergency-level-연동)
6. [Audit Trail 연동](#6-audit-trail-연동)
7. [기존 알림 인프라 통합](#7-기존-알림-인프라-통합)
8. [구현 계획](#8-구현-계획)

---

## 1. 개요

### 1.1 배경

현재 Self-Healing 시스템의 Celery 태스크들은 **알림이 파편화**되어 있습니다:

| 현재 문제 | 영향 |
|----------|------|
| 대부분 `logger.warning`만 출력 | 운영자가 로그를 보지 않으면 모름 |
| 알림 인프라가 4개로 분산 | 일관성 없는 알림 경험 |
| 중요 이벤트 알림 누락 | SLA 위반, 정리 완료 등 |
| 알림 시점 기준 없음 | 언제 사전/사후 알림인지 불명확 |

### 1.2 목표

```
┌─────────────────────────────────────────────────────────────────┐
│                    통합 알림 아키텍처 목표                         │
├─────────────────────────────────────────────────────────────────┤
│ ✓ 리스크 기반 알림 시점 결정 (Before/After/Real-time)            │
│ ✓ 알림 피로도 방지 (Aggregation, Threshold, Cooldown)           │
│ ✓ Emergency Level 연동 (가변형 알림 정책)                        │
│ ✓ Audit Trail 연동 (알림 발송 기록 해시 체인)                     │
│ ✓ 기존 인프라 통합 (AlertAdapter, SecurityNotificationService)  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 설계 철학

> **"모든 것을 알리면 운영자가 지치고, 아무것도 안 알리면 불안하다."**

작업의 **파괴성(Destructiveness)**에 따라 알림 시점을 결정합니다.

---

## 2. 알림 시점 결정 매트릭스

### 2.1 리스크 기반 분류

| 위험도 | 알림 시점 | 전략 | 대상 태스크 | 가치 |
|--------|----------|------|------------|------|
| 🔴 **고위험** | **BEFORE** | 사전 승인/알림 | `purge_archived`, `apply_pending_config`, `force_open_circuit` | 거버넌스 보호: 실수로 인한 데이터 증발 방지 |
| 🟡 **저위험** | **AFTER** | 사후 결과 보고 | `archive_old_entries`, `cleanup_expired`, `expire_approval` | 운영 가시성: "시스템이 일 잘하고 있군" |
| 🔵 **상태 변화** | **REAL-TIME** | 즉시 알림 | `check_recovery_transitions`, `check_drift`, `emergency_mode` | 신속 대응: 장애 복구 상황 실시간 파악 |

### 2.2 알림 시점 흐름도

```
┌─────────────────────────────────────────────────────────────────┐
│                        태스크 실행 흐름                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ 위험도 분류 확인  │
                    └─────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌───────────┐       ┌───────────┐       ┌───────────┐
    │ 🔴 고위험  │       │ 🟡 저위험  │       │ 🔵 상태변화 │
    └───────────┘       └───────────┘       └───────────┘
          │                   │                   │
          ▼                   │                   │
    ┌───────────┐             │                   │
    │ BEFORE    │             │                   │
    │ 사전 알림  │             │                   │
    │ + 승인대기 │             │                   │
    └───────────┘             │                   │
          │                   │                   │
          ▼                   ▼                   ▼
    ┌─────────────────────────────────────────────────┐
    │                   태스크 실행                     │
    └─────────────────────────────────────────────────┘
          │                   │                   │
          ▼                   ▼                   ▼
    ┌───────────┐       ┌───────────┐       ┌───────────┐
    │ AFTER     │       │ AFTER     │       │ REAL-TIME │
    │ 결과 보고  │       │ 일일 요약  │       │ 즉시 알림  │
    │ (즉시)    │       │ (집계)    │       │           │
    └───────────┘       └───────────┘       └───────────┘
```

### 2.3 구체적 태스크 분류

#### 🔴 고위험 태스크 (BEFORE 알림 필수)

| 태스크 | 이유 | 알림 내용 |
|--------|------|----------|
| `purge_archived_dlq_entries` | 영구 삭제, 복구 불가 | "X개 항목 영구 삭제 예정, 승인하시겠습니까?" |
| `apply_pending_config_changes` | 설정 변경, 서비스 영향 | "설정 변경 적용 예정: {변경 내용}" |
| `force_open_circuit_breaker` | 서비스 차단 | "서비스 X 강제 차단 예정" |

#### 🟡 저위험 태스크 (AFTER 요약 알림)

| 태스크 | 이유 | 알림 내용 |
|--------|------|----------|
| `archive_old_dlq_entries` | 상태 변경만, 복구 가능 | 일일 요약: "오늘 X건 아카이브됨" |
| `cleanup_expired_config` | 만료 항목 정리 | 일일 요약: "X건 만료 처리됨" |
| `expire_approval_requests` | 승인 요청 만료 | 일일 요약: "X건 승인 요청 만료" |

#### 🔵 상태 변화 태스크 (REAL-TIME 알림)

| 태스크 | 이유 | 알림 내용 |
|--------|------|----------|
| `check_recovery_transitions` | 복구 시도 알림 필요 | "서비스 X: OPEN → HALF_OPEN 전환 (복구 시도 중)" |
| `check_drift` | SLA 위반 즉시 대응 | "SLA 위반율 25% 초과! 도메인: payment" |
| `check_emergency_mode_expiry` | 긴급 모드 상태 변화 | "긴급 모드 4시간 경과, 복구 검토 필요" |

---

## 3. 알림 피로도 해결 3대 기술

### 3.1 알림 집계 (Aggregation/Batching)

> 루틴한 정리 작업은 건별로 보내지 말고, **일일 요약 리포트**로 묶어서 발송

**구현 방식**:
```python
@dataclass
class DailyAutonomousReport:
    """일일 자율 운영 리포트."""
    date: datetime
    archived_count: int = 0
    expired_count: int = 0
    purged_count: int = 0
    approval_expired_count: int = 0

    def to_slack_message(self) -> str:
        return (
            f"📊 *자율 운영 일일 리포트* ({self.date:%Y-%m-%d})\n"
            f"• 아카이브: {self.archived_count}건\n"
            f"• 만료 처리: {self.expired_count}건\n"
            f"• 영구 삭제: {self.purged_count}건\n"
            f"• 승인 만료: {self.approval_expired_count}건"
        )
```

**Beat Schedule**:
```python
'generate-daily-autonomous-report': {
    'task': 'selfhealing.generate_daily_autonomous_report',
    'schedule': crontab(hour=9, minute=0),  # 매일 오전 9시
    'options': {'queue': 'reports'},
}
```

### 3.2 임계값 기반 알림 (Threshold-based)

> 미세한 변화는 로그만, **유의미한 수치일 때만** 알림 발송

| 메트릭 | 로그만 | WARNING 알림 | CRITICAL 알림 |
|--------|--------|-------------|---------------|
| SLA 위반율 | < 5% | 5% ~ 20% | ≥ 20% |
| 드리프트 비율 | < 5% | 5% ~ 20% | ≥ 50% |
| Pending 항목 수 | < 10 | 10 ~ 50 | ≥ 100 |

**구현 방식**:
```python
@dataclass
class NotificationThreshold:
    """알림 임계값 설정."""
    log_only: float = 5.0      # 이하면 로그만
    warning: float = 20.0      # 이상이면 WARNING
    critical: float = 50.0     # 이상이면 CRITICAL

    def get_severity(self, value: float) -> str | None:
        if value >= self.critical:
            return "critical"
        elif value >= self.warning:
            return "warning"
        elif value < self.log_only:
            return None  # 알림 안 함
        return "info"
```

### 3.3 지능형 쿨다운 (Smart Cooldown)

> 동일한 알림이 반복될 때 **첫 발생 후 N분간 억제**

**기존 구현 참조** (`GateAlertManager`):
```python
# services/error_budget_gate/alert_manager.py
class GateAlertManager:
    def __init__(self, cooldown_seconds: int = 300):
        self._cooldown_seconds = cooldown_seconds
        self._last_alert_times: Dict[str, datetime] = {}

    def _can_send_alert(self, alert_type: str) -> bool:
        last_time = self._last_alert_times.get(alert_type)
        if last_time is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed >= self._cooldown_seconds
```

**알림 유형별 쿨다운**:

| 알림 유형 | 쿨다운 시간 | 이유 |
|----------|------------|------|
| CB 상태 변화 | 5분 | 빈번한 상태 전환 억제 |
| SLA 드리프트 | 30분 | 같은 도메인 반복 알림 방지 |
| 긴급 모드 경고 | 1시간 | 이미 알고 있는 상황 |
| 정리 작업 완료 | 24시간 | 일일 요약으로 대체 |

---

## 4. BaseNotifyingTask 아키텍처

### 4.1 NotificationPolicy 데이터클래스

```python
from dataclasses import dataclass, field
from typing import Literal, Optional
from enum import Enum

class NotificationTiming(str, Enum):
    BEFORE = "before"      # 실행 전 알림/승인
    AFTER = "after"        # 실행 후 결과 알림
    REALTIME = "realtime"  # 즉시 알림 (상태 변화)
    AGGREGATED = "aggregated"  # 일일 요약에 포함

@dataclass
class NotificationPolicy:
    """태스크별 알림 정책."""

    # 알림 시점
    timing: NotificationTiming = NotificationTiming.AFTER

    # 집계 여부 (True면 일일 리포트에 포함)
    aggregate: bool = False

    # 임계값 (이 수치 초과 시에만 알림)
    threshold: Optional[float] = None
    threshold_field: str = ""  # 결과에서 체크할 필드명

    # 쿨다운 (동일 알림 억제 시간, 초)
    cooldown_seconds: int = 300

    # 기본 심각도
    default_severity: Literal["info", "warning", "critical"] = "info"

    # 알림 채널 (None이면 severity에 따라 자동 결정)
    channels: list[str] = field(default_factory=lambda: ["slack"])

    # 고위험 작업 여부 (True면 승인 필요)
    requires_approval: bool = False

    # Emergency Level에서 즉시 알림으로 전환할지
    escalate_on_emergency: bool = True
```

### 4.2 BaseNotifyingTask 클래스

```python
from celery import Task
from typing import Any, Dict, Optional
from datetime import datetime, timezone

class BaseNotifyingTask(Task):
    """알림 발송을 내장한 Celery Task 베이스 클래스."""

    # 서브클래스에서 오버라이드
    notification_policy: NotificationPolicy = NotificationPolicy()

    # 쿨다운 상태 저장 (클래스 레벨)
    _last_alert_times: Dict[str, datetime] = {}

    def __call__(self, *args, **kwargs):
        """태스크 실행 래퍼."""
        # 1. 사전 알림/승인 체크 (고위험 작업)
        if not self._on_pre_execute(*args, **kwargs):
            return {"success": False, "blocked": True, "reason": "approval_required"}

        # 2. 태스크 실행
        result = super().__call__(*args, **kwargs)

        # 3. 사후 알림
        self._on_post_execute(result, *args, **kwargs)

        return result

    def _on_pre_execute(self, *args, **kwargs) -> bool:
        """
        실행 전 훅: 고위험 작업 사전 알림/승인.

        Returns:
            True: 실행 진행
            False: 실행 차단 (승인 대기)
        """
        policy = self.notification_policy

        if policy.timing != NotificationTiming.BEFORE:
            return True

        if policy.requires_approval:
            # 승인 요청 생성 및 대기
            return self._request_approval(*args, **kwargs)

        # 사전 알림만 발송
        self._send_pre_notification(*args, **kwargs)
        return True

    def _on_post_execute(self, result: Any, *args, **kwargs) -> None:
        """실행 후 훅: 결과 알림 발송."""
        policy = self.notification_policy

        # 알림 발송 여부 판단
        if not self._should_notify(result):
            return

        # Emergency Level 체크 (가변형 알림 정책)
        effective_timing = self._get_effective_timing()

        if policy.aggregate and effective_timing != NotificationTiming.REALTIME:
            # 일일 요약에 추가
            self._add_to_daily_report(result)
        else:
            # 즉시 알림 발송
            self._send_notification(result)

        # Audit Trail 기록
        self._record_audit_trail(result)

    def _should_notify(self, result: Dict[str, Any]) -> bool:
        """알림 발송 여부 판단."""
        policy = self.notification_policy

        # 1. 쿨다운 체크
        alert_key = f"{self.name}:{self._get_alert_key(result)}"
        if not self._can_send_alert(alert_key):
            return False

        # 2. 임계값 체크
        if policy.threshold is not None and policy.threshold_field:
            value = result.get(policy.threshold_field, 0)
            if value < policy.threshold:
                return False

        # 3. 결과가 의미 있는지 체크
        return self._has_meaningful_result(result)

    def _get_effective_timing(self) -> NotificationTiming:
        """Emergency Level에 따른 실제 알림 시점 결정."""
        policy = self.notification_policy

        if not policy.escalate_on_emergency:
            return policy.timing

        # Emergency Level 확인
        from selfhealing.services.emergency_mode import get_emergency_mode_manager

        manager = get_emergency_mode_manager()
        current_level = manager.get_current_level()

        # LEVEL_3 이상이면 모든 알림을 즉시 발송으로 전환
        if current_level >= 3:
            return NotificationTiming.REALTIME

        return policy.timing

    def _can_send_alert(self, alert_key: str) -> bool:
        """쿨다운 확인."""
        last_time = self._last_alert_times.get(alert_key)
        if last_time is None:
            return True

        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        return elapsed >= self.notification_policy.cooldown_seconds

    def _record_alert_sent(self, alert_key: str) -> None:
        """알림 발송 기록."""
        self._last_alert_times[alert_key] = datetime.now(timezone.utc)

    def _send_notification(self, result: Dict[str, Any]) -> None:
        """알림 발송."""
        from selfhealing.services import get_security_notification_service

        service = get_security_notification_service()

        message = self._get_summary_message(result)
        severity = self._get_severity(result)

        service.send_alert(
            title=f"[Self-Healing] {self.name}",
            message=message,
            severity=severity,
            channels=self.notification_policy.channels,
            metadata={
                "task_name": self.name,
                "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # 쿨다운 기록
        alert_key = f"{self.name}:{self._get_alert_key(result)}"
        self._record_alert_sent(alert_key)

    def _record_audit_trail(self, result: Dict[str, Any]) -> None:
        """Audit Trail에 알림 발송 기록."""
        try:
            from selfhealing.audit import get_audit_logger

            logger = get_audit_logger()
            logger.log_event(
                event_type="notification_sent",
                entity_type="celery_task",
                entity_id=self.request.id,
                action=self.name,
                details={
                    "result_summary": self._get_summary_message(result),
                    "severity": self._get_severity(result),
                    "notification_policy": {
                        "timing": self.notification_policy.timing.value,
                        "aggregate": self.notification_policy.aggregate,
                    },
                },
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to record audit trail: {e}")

    # 서브클래스에서 오버라이드 가능
    def _get_summary_message(self, result: Dict[str, Any]) -> str:
        """알림 메시지 생성."""
        return f"Task {self.name} completed: {result}"

    def _get_severity(self, result: Dict[str, Any]) -> str:
        """결과에 따른 심각도 결정."""
        return self.notification_policy.default_severity

    def _get_alert_key(self, result: Dict[str, Any]) -> str:
        """알림 중복 방지용 키 생성."""
        return "default"

    def _has_meaningful_result(self, result: Dict[str, Any]) -> bool:
        """의미 있는 결과인지 확인."""
        if not isinstance(result, dict):
            return True

        # 성공이고 처리 건수가 있으면 의미 있음
        if result.get("success", True):
            count_fields = ["archived_count", "expired_count", "purged_count",
                          "total", "count", "processed"]
            return any(result.get(f, 0) > 0 for f in count_fields)

        return True  # 실패는 항상 알림
```

### 4.3 사용 예시

```python
# 저위험 태스크 (일일 요약)
@shared_task(bind=True, base=BaseNotifyingTask)
class ArchiveOldDLQEntriesTask(BaseNotifyingTask):
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
    )

    def run(self, older_than_days: int = 30):
        from selfhealing.services.dlq_service import get_dlq_service
        service = get_dlq_service()
        count = service.archive_old_entries(older_than_days)
        return {"archived_count": count}

    def _get_summary_message(self, result):
        return f"아카이브 완료: {result.get('archived_count', 0)}건"


# 고위험 태스크 (사전 승인)
@shared_task(bind=True, base=BaseNotifyingTask)
class PurgeArchivedDLQEntriesTask(BaseNotifyingTask):
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.BEFORE,
        requires_approval=True,
        default_severity="critical",
        cooldown_seconds=3600,  # 1시간
    )

    def run(self, older_than_days: int = 90):
        from selfhealing.services.dlq_service import get_dlq_service
        service = get_dlq_service()
        count = service.purge_archived(older_than_days=older_than_days)
        return {"purged_count": count}

    def _get_summary_message(self, result):
        return f"⚠️ 영구 삭제 완료: {result.get('purged_count', 0)}건 (복구 불가)"


# 상태 변화 태스크 (즉시 알림)
@shared_task(bind=True, base=BaseNotifyingTask)
class CheckSLADriftTask(BaseNotifyingTask):
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.REALTIME,
        threshold=20.0,  # 20% 이상일 때만
        threshold_field="max_breach_rate",
        default_severity="warning",
        cooldown_seconds=1800,  # 30분
    )

    def run(self):
        from selfhealing.tasks.drift_detection import SLADriftDetector
        # ... 드리프트 체크 로직
        return {"warnings": [...], "max_breach_rate": 25.0}

    def _get_severity(self, result):
        rate = result.get("max_breach_rate", 0)
        if rate >= 50:
            return "critical"
        elif rate >= 20:
            return "warning"
        return "info"
```

---

## 5. Emergency Level 연동

### 5.1 가변형 알림 정책

Emergency Level에 따라 알림 정책이 **동적으로 변경**됩니다:

| Emergency Level | 저위험 태스크 | 고위험 태스크 | 상태 변화 |
|-----------------|-------------|-------------|----------|
| **NORMAL** | 일일 요약 | 사전 승인 | 즉시 알림 |
| **LEVEL_1** (주의) | 일일 요약 | 사전 승인 | 즉시 알림 |
| **LEVEL_2** (경고) | **4시간 요약** | 사전 승인 | 즉시 알림 |
| **LEVEL_3** (긴급) | **즉시 알림** | **알림 후 자동 실행** | 즉시 알림 |

### 5.2 구현 로직

```python
def _get_effective_timing(self) -> NotificationTiming:
    """Emergency Level에 따른 실제 알림 시점 결정."""
    policy = self.notification_policy

    if not policy.escalate_on_emergency:
        return policy.timing

    from selfhealing.services.emergency_mode import get_emergency_mode_manager

    manager = get_emergency_mode_manager()
    current_level = manager.get_current_level()

    # LEVEL_3: 모든 알림 즉시 발송
    if current_level >= 3:
        return NotificationTiming.REALTIME

    # LEVEL_2: 요약 주기 단축 (일일 → 4시간)
    if current_level >= 2 and policy.timing == NotificationTiming.AGGREGATED:
        # 4시간 요약으로 변경 (별도 로직)
        pass

    return policy.timing

def _should_skip_approval(self) -> bool:
    """Emergency Level 3에서는 승인 스킵."""
    from selfhealing.services.emergency_mode import get_emergency_mode_manager

    manager = get_emergency_mode_manager()
    current_level = manager.get_current_level()

    # LEVEL_3: 고위험 작업도 알림 후 자동 실행
    return current_level >= 3
```

### 5.3 Emergency Level별 알림 채널

| Level | Slack | Email | SMS | PagerDuty |
|-------|-------|-------|-----|-----------|
| NORMAL | ✅ | - | - | - |
| LEVEL_1 | ✅ | ✅ | - | - |
| LEVEL_2 | ✅ | ✅ | - | - |
| LEVEL_3 | ✅ | ✅ | ✅ | ✅ |

---

## 6. Audit Trail 연동

### 6.1 알림 발송 기록의 중요성

> **"왜 이 데이터가 지워졌죠?"** → **"X시에 알림이 갔고 Y가 승인했습니다"**

알림 발송 자체가 **해시 체인**에 기록되어야 나중에 증명할 수 있습니다.

### 6.2 기록 항목

```python
@dataclass
class NotificationAuditEntry:
    """알림 발송 감사 기록."""

    # 기본 정보
    timestamp: datetime
    task_name: str
    task_id: str

    # 알림 정보
    notification_type: str  # pre_notification, result_notification, approval_request
    channels: list[str]     # ["slack", "email"]
    severity: str           # info, warning, critical

    # 결과 요약
    message_summary: str
    result_data: dict

    # 승인 정보 (해당 시)
    approval_required: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    # 해시 체인
    previous_hash: str = ""
    current_hash: str = ""
```

### 6.3 AuditMiddleware 연동

```python
def _record_audit_trail(self, result: Dict[str, Any]) -> None:
    """Audit Trail에 알림 발송 기록."""
    try:
        from selfhealing.audit import get_audit_logger

        logger = get_audit_logger()

        # 알림 발송 이벤트 기록
        logger.log_event(
            event_type="notification_sent",
            entity_type="celery_task",
            entity_id=self.request.id,
            action=self.name,
            actor_id="system",  # 시스템 자동 실행
            details={
                "result_summary": self._get_summary_message(result),
                "severity": self._get_severity(result),
                "channels": self.notification_policy.channels,
                "notification_policy": {
                    "timing": self.notification_policy.timing.value,
                    "aggregate": self.notification_policy.aggregate,
                    "requires_approval": self.notification_policy.requires_approval,
                },
                "emergency_level": self._get_current_emergency_level(),
            },
        )

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"[BaseNotifyingTask] Failed to record audit trail: {e}"
        )
```

### 6.4 승인 기록

고위험 작업의 경우 **승인 과정도 기록**됩니다:

```python
def _request_approval(self, *args, **kwargs) -> bool:
    """승인 요청 및 대기."""
    from selfhealing.services.runtime_config import get_runtime_config_manager
    from selfhealing.audit import get_audit_logger

    manager = get_runtime_config_manager()
    audit = get_audit_logger()

    # 1. 승인 요청 생성
    request = manager.create_approval_request(
        request_type="high_risk_task",
        description=f"고위험 태스크 실행 요청: {self.name}",
        requested_by="system",
        payload={"args": args, "kwargs": kwargs},
    )

    # 2. Audit Trail 기록 (승인 요청)
    audit.log_event(
        event_type="approval_requested",
        entity_type="celery_task",
        entity_id=request["id"],
        action=self.name,
        details={"args": str(args), "kwargs": str(kwargs)},
    )

    # 3. 알림 발송 (승인 요청)
    self._send_approval_request_notification(request)

    # 4. 승인 대기 (비동기 처리 - 태스크 재스케줄)
    return False  # 현재 실행 차단
```

---

## 7. 기존 알림 인프라 통합

### 7.1 현재 알림 인프라 현황

| 컴포넌트 | 경로 | 역할 | 통합 방안 |
|---------|------|------|----------|
| **AlertAdapter** | `interfaces/alert_adapter.py` | 알림 추상화 | `BaseNotifyingTask`에서 사용 |
| **SecurityNotificationService** | `services/security_notification_service.py` | 보안 알림 | 주요 알림 채널로 활용 |
| **GateAlertManager** | `services/error_budget_gate/alert_manager.py` | Gate 알림 + 쿨다운 | 쿨다운 로직 참조 |
| **continuous_audit._send_alert** | `audit/continuous_audit.py` | Drift/Compliance | 통합 알림으로 대체 |

### 7.2 통합 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                     BaseNotifyingTask                            │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ NotificationPolicy → should_notify() → _send_notification() ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  SecurityNotificationService                     │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Severity Routing → Channel Selection → Rate Limiting        ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    ┌───────────┐       ┌───────────┐       ┌───────────┐
    │   Slack   │       │   Email   │       │ PagerDuty │
    └───────────┘       └───────────┘       └───────────┘
```

### 7.3 SecurityNotificationService 확장

```python
# 기존 메서드 외에 추가
class SecurityNotificationService:

    def send_task_notification(
        self,
        task_name: str,
        result: dict,
        severity: str,
        policy: NotificationPolicy,
    ) -> SecurityNotificationResult:
        """Celery 태스크 알림 발송."""

        # 1. 채널 결정
        channels = self._get_channels_for_severity(severity)

        # 2. 메시지 포맷팅
        message = self._format_task_notification(task_name, result, severity)

        # 3. 발송
        return self._send_to_channels(message, channels, severity)

    def send_daily_report(
        self,
        report: DailyAutonomousReport,
    ) -> SecurityNotificationResult:
        """일일 자율 운영 리포트 발송."""

        message = report.to_slack_message()
        return self._send_to_channels(message, ["slack", "email"], "info")
```

---

## 8. 구현 계획

### 8.1 Phase 1: SLA 드리프트 알림 연결 ✅ 완료

**구현 파일**: `selfhealing/tasks/drift_detection.py`

**변경 사항**:
- `_send_drift_notifications()` 메서드에서 `SecurityNotificationService.send_alert()` 호출
- 로그 + 알림 동시 발송

```python
# tasks/drift_detection.py
def _send_drift_notifications(self, warnings: list[dict]) -> None:
    """Send notifications for SLA drift warnings."""
    from selfhealing.services import get_security_notification_service

    service = get_security_notification_service()

    for warning in warnings:
        domain = warning.get("domain", "unknown")
        severity = warning.get("severity", "warning")

        # 로그 + 알림 발송
        logger.warning(f"[SLADriftWarning] {warning.get('message')}")

        service.send_alert(
            title=f"[SLA Drift] {domain}",
            message=warning.get("message"),
            severity=severity,
            channels=["slack"],
            metadata={
                "type": warning.get("type"),
                "domain": domain,
                "recommendation": warning.get("recommendation"),
                **warning.get("metrics", {}),
            },
        )
```

### 8.2 Phase 2: BaseNotifyingTask 구현 ✅ 완료

**구현 파일**: `selfhealing/tasks/base_notifying_task.py`

**구현 완료 항목**:
1. ✅ `NotificationTiming` Enum (BEFORE/AFTER/REALTIME/AGGREGATED)
2. ✅ `NotificationThreshold` 데이터클래스 (임계값 기반 심각도 결정)
3. ✅ `NotificationPolicy` 데이터클래스 (태스크별 알림 정책)
4. ✅ `DailyAutonomousReport` 데이터클래스 (일일 요약 리포트)
5. ✅ `BaseNotifyingTask` 베이스 클래스
   - Pre/Post execution hooks
   - Cooldown 기반 알림 억제
   - Threshold 기반 알림 필터링
   - Emergency Level 연동 (escalate_on_emergency)
   - Audit Trail 연동
6. ✅ `SecurityNotificationService.send_alert()` 메서드 추가

**사용 예시**:
```python
from selfhealing.tasks import (
    BaseNotifyingTask,
    NotificationPolicy,
    NotificationTiming,
)

class ArchiveTask(BaseNotifyingTask):
    notification_policy = NotificationPolicy(
        timing=NotificationTiming.AGGREGATED,
        aggregate=True,
        default_severity="info",
    )

    def run(self, days=30):
        count = archive_old_entries(days)
        return {"archived_count": count}
```

### 8.3 Phase 3: 기존 태스크 마이그레이션 (예정)

1. 고위험 태스크 마이그레이션 (`purge_archived`, `apply_config`)
2. 저위험 태스크 마이그레이션 (`archive`, `cleanup`, `expire`)
3. 상태 변화 태스크 마이그레이션 (`check_recovery`, `check_drift`)

### 8.4 Phase 4: 일일 리포트 구현 (1시간)

1. `DailyAutonomousReport` 데이터클래스
2. `generate_daily_autonomous_report` 태스크
3. Beat Schedule 등록

### 8.5 Phase 5: 테스트 및 문서화 (2시간)

1. 단위 테스트: NotificationPolicy, BaseNotifyingTask
2. 통합 테스트: 알림 발송 검증
3. 04_AUTONOMOUS_OPS.md 업데이트

---

## 📎 관련 문서

- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 시스템 (기존)
- [09_AUTONOMOUS_TASK_EXPANSION.md](09_AUTONOMOUS_TASK_EXPANSION.md) - 자율 태스크 확장 (신규)
- [07_HYBRID_STORAGE_ARCHITECTURE.md](07_HYBRID_STORAGE_ARCHITECTURE.md) - Audit Trail 연동

---

## 📋 체크리스트

### 논의 내용 반영 확인

- [x] 리스크 기반 알림 시점 결정 매트릭스
- [x] Before/After/Real-time 분류
- [x] 알림 피로도 해결 3대 기술 (Aggregation, Threshold, Cooldown)
- [x] BaseNotifyingTask 아키텍처
- [x] NotificationPolicy 데이터클래스
- [x] Emergency Level 연동 (가변형 알림 정책)
- [x] Audit Trail 연동 (알림 발송 기록)
- [x] 기존 알림 인프라 통합 방안
- [x] 구현 계획 및 Phase 정의

### 구현 완료 현황

| Phase | 설명 | 상태 | 파일 |
|-------|------|------|------|
| Phase 1 | SLA 드리프트 알림 연결 | ✅ 완료 | `tasks/drift_detection.py` |
| Phase 2 | BaseNotifyingTask 구현 | ✅ 완료 | `tasks/base_notifying_task.py` |
| Phase 3 | 기존 태스크 마이그레이션 | ⏳ 예정 | - |
| Phase 4 | 일일 리포트 구현 | ⏳ 예정 | - |
| Phase 5 | 테스트 및 문서화 | ⏳ 예정 | - |

### 신규 파일

| 파일 경로 | 설명 |
|-----------|------|
| `selfhealing/tasks/base_notifying_task.py` | BaseNotifyingTask, NotificationPolicy, DailyAutonomousReport |
| `selfhealing/tasks/__init__.py` | tasks 패키지 초기화 및 export |

### 수정된 파일

| 파일 경로 | 변경 사항 |
|-----------|-----------|
| `selfhealing/tasks/drift_detection.py` | `_send_drift_notifications()` - 알림 발송 연동 |
| `selfhealing/services/security_notification_service.py` | `send_alert()` 메서드 추가 |
| `selfhealing/services/__init__.py` | `send_alert` export 추가 |
