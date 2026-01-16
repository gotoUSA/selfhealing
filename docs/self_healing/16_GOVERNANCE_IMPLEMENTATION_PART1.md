# Governance Implementation Plan - Part 1

> RBAC, 환경변수 Audit 구현 계획

---

## 개요

Big 4 실사 대응을 위한 거버넌스 강화 구현 계획입니다.

### 문서 구조

| 문서 | 내용 |
|------|------|
| **Part 1 (본 문서)** | RBAC, 환경변수 Audit |
| [Part 1-A](16_GOVERNANCE_IMPLEMENTATION_PART1A.md) | Rate Limit, 티어링, 장애 대비 |
| [Part 2](16_GOVERNANCE_IMPLEMENTATION_PART2.md) | Config Versioning, Fail-Safe |

### 구현 항목

| 순위 | 항목 | 중요도 | 예상 작업 |
|------|------|--------|----------|
| 1 | RBAC (Operator/Admin 분리) | 최상 | 1일 |
| 2 | 환경변수 Audit | 상 | 0.5일 |

---

## 1. RBAC (Role-Based Access Control)

### 1.1 현재 상태

```python
# 현재: Admin만 있음
permission_classes = [IsAuthenticated, IsAdminUser]
```

### 1.2 목표 상태

| 역할 | 권한 | 대상 API |
|------|------|----------|
| Viewer | 읽기 전용 | GET /status, GET /dashboard |
| Operator | 운영 작업 | POST /dlq/replay, GET /audit |
| Admin | 모든 권한 | POST /allow, POST /block, PUT /config |

### 1.3 구현 계획

#### Phase 1: 권한 클래스 생성 ✅

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/permissions.py`

```python
"""
RBAC Permission Classes for Self-Healing Control API.

Provides role-based access control for the Self-Healing system:
- Viewer: Read-only access (dashboard, status, audit logs)
- Operator: Operational tasks (DLQ replay, archive)
- Admin: Full access (CB control, system enable/disable, config changes)

Reference: docs/self_healing/10_OPERATIONS_GUIDE.md (권한 테이블)
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class IsViewer(BasePermission):
    """
    읽기 전용 권한 (Viewer 역할).

    허용되는 작업:
    - GET /status, GET /dashboard
    - GET /audit (감사 로그 조회)
    - GET /dlq/list, GET /dlq/<pk> (DLQ 조회)
    - GET /system/status (시스템 상태 조회)

    조건:
    - 인증된 사용자
    - staff 또는 'selfhealing_viewer' 그룹 멤버
    """

    message = "Self-Healing 조회 권한이 필요합니다. selfhealing_viewer 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin/Staff는 항상 허용
        if request.user.is_staff:
            return True

        # selfhealing_viewer, operator, admin 그룹 멤버십 확인
        # (상위 권한은 하위 권한 포함)
        return request.user.groups.filter(
            name__in=["selfhealing_viewer", "selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsOperator(BasePermission):
    """
    운영자 권한 (Operator 역할).

    허용되는 작업:
    - 모든 Viewer 권한
    - POST /dlq/replay (DLQ 리플레이)
    - POST /dlq/cleanup/archive (DLQ 아카이브)
    - POST /dlq/<pk>/retry (개별 항목 재시도)
    - POST /dlq/<pk>/resolve (개별 항목 해결)

    조건:
    - 인증된 사용자
    - superuser 또는 'selfhealing_operator' 또는 'selfhealing_admin' 그룹 멤버
    """

    message = "Self-Healing 운영자 권한이 필요합니다. selfhealing_operator 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False

        # Admin은 항상 허용
        if request.user.is_staff and request.user.is_superuser:
            return True

        # selfhealing_operator 또는 selfhealing_admin 그룹 멤버십 확인
        return request.user.groups.filter(
            name__in=["selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsSelfHealingAdmin(BasePermission):
    """
    관리자 권한 (Admin 역할).

    허용되는 작업:
    - 모든 Operator 권한
    - POST /control/ (CB 수동 제어: allow/block)
    - POST /system/enable, /system/disable (킬 스위치)
    - PUT /config/* (설정 변경)
    - POST /dlq/cleanup/purge (DLQ 영구 삭제)

    조건:
    - Django superuser 또는 'selfhealing_admin' 그룹 멤버

    보안:
    - Fail-Secure: 권한 확인 실패 시 거부
    """

    message = "Self-Healing 관리자 권한이 필요합니다. selfhealing_admin 그룹에 속해야 합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        try:
            if not request.user or not request.user.is_authenticated:
                return False

            # Django superuser
            if request.user.is_superuser:
                return True

            # selfhealing_admin 그룹
            return request.user.groups.filter(name="selfhealing_admin").exists()

        except Exception as e:
            # Fail-Secure: 오류 시 거부
            logger.warning(f"[RBAC] Permission check failed (deny): {e}")
            return False


# Backward compatibility aliases
SelfHealingViewer = IsViewer
SelfHealingOperator = IsOperator
SelfHealingAdmin = IsSelfHealingAdmin
```

#### Phase 2: View에 적용 ✅

```python
# 읽기 전용 엔드포인트
class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsViewer]

# 운영자 엔드포인트  
class DLQReplayView(APIView):
    permission_classes = [IsAuthenticated, IsOperator]

# 관리자 엔드포인트
class CircuitBreakerControlView(APIView):
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
```

#### Phase 3: Django Group 생성 (post_migrate Signal) ✅

> **변경 사항**: Migration 방식에서 `post_migrate` 시그널 방식으로 변경
> 
> **이유**:
> - 호스트 앱 오염 방지 (shopping 마이그레이션에 의존하지 않음)
> - 이식성 확보 (다른 프로젝트로 패키지 재사용 가능)
> - 업계 표준 방식 (django-allauth, django-guardian 방식)
> 
> **동작 방식**:
> - `post_migrate` 시그널: 마이그레이션 완료 후 1회만 실행
> - `get_or_create`로 멱등성 보장
> - `dispatch_uid`로 중복 연결 방지

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/django/apps.py`

```python
"""
Django App Configuration for Self-Healing.

RBAC Groups:
    The selfhealing package creates the following groups via post_migrate signal:
    - selfhealing_viewer: Read-only access (dashboard, status, audit logs)
    - selfhealing_operator: Operational tasks (DLQ replay, archive)
    - selfhealing_admin: Full access (CB control, system enable/disable, config)

    This approach:
    - Does NOT pollute host app's migrations
    - Runs only after migrations complete (DB ready guaranteed)
    - Is idempotent (safe to run multiple times)
    - Is the industry standard (used by django-allauth, django-guardian)
"""

import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

# RBAC group definitions
SELFHEALING_GROUPS = [
    "selfhealing_viewer",
    "selfhealing_operator", 
    "selfhealing_admin",
]


def create_selfhealing_groups(sender, **kwargs):
    """
    Create RBAC groups for Self-Healing system.
    
    Called via post_migrate signal - runs only after migrations complete.
    Uses get_or_create for idempotency.
    """
    from django.contrib.auth.models import Group
    
    created_groups = []
    existing_groups = []
    
    for group_name in SELFHEALING_GROUPS:
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            created_groups.append(group_name)
        else:
            existing_groups.append(group_name)
    
    if created_groups:
        logger.info(
            f"[SelfHealing] RBAC groups created: {created_groups}"
        )
    
    if existing_groups and created_groups:
        logger.debug(
            f"[SelfHealing] RBAC groups already existed: {existing_groups}"
        )


class SelfHealingConfig(AppConfig):
    """Django app configuration for self-healing."""

    name = "selfhealing.adapters.django"
    label = "selfhealing"
    verbose_name = "Self-Healing System"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """
        Called when the app is ready.
        
        Connects post_migrate signal for RBAC group creation.
        This ensures groups are created after migrations complete,
        not on every server start.
        """
        # Import admin to register admin classes
        try:
            from selfhealing.adapters.django import admin  # noqa: F401
        except ImportError:
            pass
        
        # Connect post_migrate signal for RBAC group creation
        post_migrate.connect(
            create_selfhealing_groups,
            sender=self,
            dispatch_uid="selfhealing_create_rbac_groups",
        )
```

### 1.4 테스트 계획

```python
class TestRBACPermissions:
    def test_viewer_can_read_dashboard(self):
        """Viewer는 대시보드 조회 가능"""
        
    def test_viewer_cannot_replay_dlq(self):
        """Viewer는 DLQ 리플레이 불가"""
        
    def test_operator_can_replay_dlq(self):
        """Operator는 DLQ 리플레이 가능"""
        
    def test_operator_cannot_control_cb(self):
        """Operator는 CB 수동 제어 불가"""
        
    def test_admin_can_do_everything(self):
        """Admin은 모든 작업 가능"""
```

---

## 1.5 긴급 에스컬레이션 (Emergency Escalation) ✅

### 1.5.1 개요

> **"Break Glass" 패턴** - Admin 부재 시 Operator가 긴급 대응 가능

| 회사/시스템 | 구현 방식 |
|------------|----------|
| **AWS** | "Break Glass" 계정 - 평소 비활성화, 긴급 시 MFA로 활성화 |
| **Google SRE** | "Emergency Access" - 일시적 권한 상승 + 자동 만료 + 사후 감사 |
| **Netflix** | "Escalation Path" - 상위 권한 작업은 자동 티켓 생성 |
| **PCI-DSS** | "Break Glass Procedure" - 감사 로그 필수, 24시간 내 리뷰 |

### 1.5.2 설계

```
┌─────────────────────────────────────────────────────────────┐
│  Emergency Escalation Pattern                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   [정상 상태]          [긴급 상황]          [복구]          │
│                                                             │
│   NORMAL ──────────► STRICT ──────────► NORMAL             │
│      │                  │                  ▲               │
│      │   Operator OK    │   Admin Only     │               │
│      │   (일방향 ✅)    │   (승인 필요)    │               │
│      │                  │                  │               │
│      │                  ▼                  │               │
│      │            [자동 만료]              │               │
│      │            (4시간 후) ──────────────┘               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 1.5.3 핵심 설계 원칙

| 원칙 | 설명 | 이유 |
|------|------|------|
| **일방향 긴급권** | Operator → STRICT 가능, 복구는 Admin | "Fail-Safe" 원칙 |
| **자동 만료** | 4시간 후 자동 알림/만료 | 무기한 비상모드 방지 |
| **강제 Audit** | 긴급 전환 시 사유 필수 입력 | 사후 검토 보장 |
| **알림 폭탄** | Admin 전원에게 즉시 알림 | 인지 보장 |

### 1.5.4 구현

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/permissions.py`

```python
class EmergencyEscalationPermission(BasePermission):
    """
    긴급 에스컬레이션 권한 (Break Glass Pattern).

    일방향 긴급권:
    - STRICT 전환: Operator도 가능 (긴급 상황)
    - NORMAL 복구: Admin만 가능 (승인 필요)

    사용 시나리오:
    - Admin 부재 중 시스템 폭주
    - 운영자가 긴급히 STRICT 모드로 전환 필요

    Reference:
    - AWS Break Glass Pattern
    - Google SRE Emergency Access
    - PCI-DSS Break Glass Procedure
    """

    message = "긴급 에스컬레이션 권한이 없습니다."
    EMERGENCY_EXPIRY_HOURS = 4  # 긴급 모드 자동 만료 시간

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False

        target_mode = request.data.get("mode", "").upper()

        # STRICT 전환 = Operator도 가능 (일방향 긴급권)
        if target_mode == "STRICT":
            return IsOperator().has_permission(request, view)

        # NORMAL 복구 = Admin만 가능
        if target_mode == "NORMAL":
            return IsSelfHealingAdmin().has_permission(request, view)

        # 기타 모드 변경 = Admin만
        return IsSelfHealingAdmin().has_permission(request, view)
```

---

## 1.6 임계값 기반 권한 (Threshold-Based Authorization) ✅

### 1.6.1 개요

> **Risk-Based Access Control** - 위험도에 따른 동적 권한 레벨

| 회사/시스템 | 구현 방식 |
|------------|----------|
| **은행권** | 거래 금액별 승인 레벨 (100만 이하: 담당자, 1억 이상: 임원) |
| **GitHub** | PR 변경 라인 수에 따른 리뷰어 수 조정 |
| **Kubernetes** | Resource Quota - 리소스 사용량에 따른 제한 |
| **AWS IAM** | Condition 기반 정책 (예: `s3:max-keys`) |

### 1.6.2 설계

```
┌─────────────────────────────────────────────────────────────┐
│  Threshold-Based Authorization                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   오차율 (Discrepancy)         필요 권한                    │
│   ─────────────────────────────────────────                 │
│   0% ~ 15%                     Operator 승인                │
│   15% ~ 30%                    Admin 승인                   │
│   30% ~ 50%                    Admin + 경고 로그            │
│   50% 초과                     듀얼 승인 강제 (4-Eyes) ✅   │
│                                                             │
│   ⚠️ 핵심: 임계값은 설정 파일/환경변수로 관리              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 1.6.3 설정

```python
# 기본 임계값 (환경변수로 오버라이드 가능)
THRESHOLD_SETTINGS = {
    "operator_approve": 0.15,      # 15% 이하: Operator 승인
    "admin_approve": 0.30,         # 30% 이하: Admin 승인
    "dual_approval": 0.50,         # 50% 초과: 듀얼 승인 강제
}

# 환경변수
# SELFHEALING_THRESHOLD_OPERATOR=0.15
# SELFHEALING_THRESHOLD_ADMIN=0.30
# SELFHEALING_THRESHOLD_DUAL_APPROVAL=0.50
```

### 1.6.4 구현

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/permissions.py`

```python
class ThresholdBasedPermission(BasePermission):
    """
    임계값 기반 동적 권한 (Risk-Based Access Control).

    오차율(discrepancy_rate)에 따라 필요 권한 레벨 결정:
    - 15% 이하: Operator 승인
    - 30% 이하: Admin 승인
    - 30% ~ 50%: Admin + 경고 로그
    - 50% 초과: 4-Eyes 듀얼 승인 필수 ✅

    임계값 우선순위:
    1. RuntimeConfigManager (governance config)
    2. 환경변수 (SELFHEALING_THRESHOLD_*)
    3. 기본값 (0.15, 0.30, 0.50)
    """

    message = "해당 작업의 임계값이 권한 레벨을 초과합니다."

    DUAL_APPROVAL_REQUIRED_MSG = (
        "고위험 작업입니다. 4-Eyes 듀얼 승인이 필요합니다. "
        "approval_id를 제공하거나, 먼저 승인 요청을 생성해주세요."
    )

    def _get_thresholds(self) -> Dict[str, float]:
        """임계값 조회 (RuntimeConfig > 환경변수 > 기본값)."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            governance = manager.get_governance_config()
            if governance:
                return {
                    "operator_approve": governance.get("threshold_operator", 0.15),
                    "admin_approve": governance.get("threshold_admin", 0.30),
                    "dual_approval": governance.get("threshold_dual_approval", 0.50),
                }
        except Exception:
            pass

        return {
            "operator_approve": float(os.environ.get("SELFHEALING_THRESHOLD_OPERATOR", "0.15")),
            "admin_approve": float(os.environ.get("SELFHEALING_THRESHOLD_ADMIN", "0.30")),
            "dual_approval": float(os.environ.get("SELFHEALING_THRESHOLD_DUAL_APPROVAL", "0.50")),
        }

    def has_permission(self, request: Request, view: APIView) -> bool:
        discrepancy = float(request.data.get("discrepancy_rate", 0))
        thresholds = self.thresholds

        if discrepancy <= thresholds["operator_approve"]:
            return IsOperator().has_permission(request, view)

        if discrepancy <= thresholds["admin_approve"]:
            return IsSelfHealingAdmin().has_permission(request, view)

        # 30% ~ 50%: Admin + 경고 로그
        if discrepancy <= thresholds["dual_approval"]:
            return self._check_high_risk_approval(request, view, discrepancy, thresholds)

        # 50% 초과: 듀얼 승인 강제
        return self._check_dual_approval_required(request, view, discrepancy, thresholds)

    def _check_dual_approval_required(
        self, request: Request, view: APIView, discrepancy: float, thresholds: Dict[str, float]
    ) -> bool:
        """
        4-Eyes 듀얼 승인 강제.

        approval_id가 제공되고 해당 요청이 APPROVED 상태인 경우에만 허용.
        승인되지 않은 경우 403 Forbidden + 알림 발송.
        """
        if not IsSelfHealingAdmin().has_permission(request, view):
            return False

        approval_id = request.data.get("approval_id")
        if not approval_id:
            self.message = self.DUAL_APPROVAL_REQUIRED_MSG
            self._notify_dual_approval_needed(request.user.username, discrepancy, request)
            return False

        # approval_id 검증
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        for approval_request in manager.get_approval_requests():
            if approval_request["id"] == approval_id:
                if approval_request["status"] != "APPROVED":
                    self.message = f"승인 요청이 아직 승인되지 않았습니다. 현재 상태: {approval_request['status']}"
                    return False
                logger.info(f"[RBAC] Dual approval verified: approval_id={approval_id}")
                return True

        self.message = f"승인 요청 '{approval_id}'을(를) 찾을 수 없습니다."
        return False

    def _notify_dual_approval_needed(self, actor: str, discrepancy: float, request: Request) -> None:
        """듀얼 승인 필요 시 Admin들에게 알림 발송 (SecurityNotificationService 사용)."""
        try:
            from selfhealing.services.security_notification_service import SecurityNotificationService
            service = SecurityNotificationService()
            if service.config.enabled:
                service.notify_security_incident_by_id(
                    incident_id=0,
                    incident_type="dual_approval_required",
                    severity="high",
                    description=f"[4-Eyes Required] 고위험 작업에 듀얼 승인이 필요합니다.\n요청자: {actor}\n오차율: {discrepancy:.1%}",
                )
        except Exception as e:
            logger.warning(f"[RBAC] Failed to send dual approval notification: {e}")
```

---

## 2. 환경변수 Audit

### 2.1 현재 상태

- API 변경: Audit 로그 ✅
- 환경변수 변경: Audit 로그 ❌

### 2.2 목표 상태

시스템 시작 시점의 환경변수 스냅샷을 AuditService로 기록.

### 2.3 구현 계획

#### Phase 1: 환경변수 스냅샷 수집기

**파일**: `packages/selfhealing-python/src/selfhealing/audit/env_snapshot.py`

```python
"""
Environment Variable Snapshot for Audit Trail.

시스템 시작 시 Self-Healing 관련 환경변수를 스냅샷으로 기록.
"""
import os
import hashlib
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Self-Healing 관련 환경변수 prefix
TRACKED_PREFIXES = [
    'SELFHEALING_',
    'CIRCUIT_BREAKER_',
    'DLQ_',
    'SLA_',
    'CHAOS_',
]

# 민감 키워드 (마스킹 대상)
SENSITIVE_KEYWORDS = ['SECRET', 'PASSWORD', 'TOKEN', 'KEY', 'CREDENTIAL']


def collect_env_snapshot() -> Dict[str, Any]:
    """
    Self-Healing 관련 환경변수 스냅샷 수집.
    
    Returns:
        {
            "variables": {"SELFHEALING_DLQ_ENABLED": "true", ...},
            "hash": "sha256:abc123...",
            "count": 15
        }
    """
    variables = {}
    
    for key, value in os.environ.items():
        # Prefix 매칭
        if any(key.startswith(prefix) for prefix in TRACKED_PREFIXES):
            # 민감 정보 마스킹
            if any(kw in key.upper() for kw in SENSITIVE_KEYWORDS):
                variables[key] = "***MASKED***"
            else:
                variables[key] = value
    
    # 변경 감지용 해시
    sorted_items = sorted(variables.items())
    hash_input = str(sorted_items).encode('utf-8')
    config_hash = hashlib.sha256(hash_input).hexdigest()[:16]
    
    return {
        "variables": variables,
        "hash": f"sha256:{config_hash}",
        "count": len(variables),
    }


def log_env_snapshot_to_audit():
    """
    환경변수 스냅샷을 AuditService로 기록.
    
    AppConfig.ready()에서 호출.
    """
    try:
        from selfhealing.audit import log_config_change
        
        snapshot = collect_env_snapshot()
        
        log_config_change(
            config_type="environment_variables",
            changes=snapshot["variables"],
            changed_by="system_startup",
            reason="Application startup - environment snapshot",
            metadata={
                "hash": snapshot["hash"],
                "variable_count": snapshot["count"],
            }
        )
        
        logger.info(
            f"[EnvAudit] Snapshot recorded: "
            f"count={snapshot['count']}, hash={snapshot['hash']}"
        )
        
    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(f"[EnvAudit] Failed to record snapshot: {e}")
```

#### Phase 2: AppConfig에서 호출

> **중요**: 환경변수 스냅샷은 `ready()`에서, RBAC 그룹 생성은 `post_migrate`에서 호출합니다.
> 
> | 대상 작업 | 배치 위치 | 이유 | 비즈니스 가치 |
> |----------|----------|------|-------------|
> | 환경변수 스냅샷 | `ready()` | 프로세스 기동 시마다 변경 가능 | 감사 로그 100% 추적 |
> | RBAC 그룹 생성 | `post_migrate` | DB 스키마 준비 후 1회 초기화 | DB 부하 최소화 |
> 
> **근거**:
> - 12-Factor App: 환경변수는 "프로세스 상태"
> - Spring Boot: `ApplicationReadyEvent`에서 설정 로깅
> - Docker: 컨테이너 재시작 시 마이그레이션 없이 환경변수만 변경 가능

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/django/apps.py`

```python
class SelfHealingConfig(AppConfig):
    name = 'selfhealing'
    
    def ready(self):
        # 1. post_migrate 시그널 연결 (RBAC 그룹 - DB 작업)
        post_migrate.connect(create_selfhealing_groups, ...)
        
        # 2. 환경변수 스냅샷 (프로세스 상태 - 매 기동 시)
        self._log_env_snapshot()
    
    def _log_env_snapshot(self):
        """Best-effort: 실패해도 시스템은 시작."""
        try:
            from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit
            log_env_snapshot_to_audit()
        except Exception as e:
            logger.warning(f"[SelfHealing] Failed to log env snapshot: {e}")
```

### 2.4 감사 로그 예시

```json
{
  "timestamp": "2025-12-23T09:00:00Z",
  "action": "config_change",
  "config_type": "environment_variables",
  "actor": "system_startup",
  "changes": {
    "SELFHEALING_DLQ_ENABLED": "true",
    "SELFHEALING_CB_THRESHOLD": "5",
    "SELFHEALING_SECRET_KEY": "***MASKED***"
  },
  "metadata": {
    "hash": "sha256:a1b2c3d4",
    "variable_count": 12
  }
}
```

### 2.5 Defense-in-Depth 전략 ✅

> **"감시자를 감시하라"** - 단일 실패점 없는 Audit 시스템

#### 문제점
AuditService 자체가 장애 시 환경변수 변경 기록이 손실될 수 있음.

#### 솔루션: 다층 방어

| 계층 | 메커니즘 | 역할 | 구현 상태 |
|------|----------|------|----------|
| **Primary** | AuditService (DB) | 정상 경로 | ✅ |
| **L1 Fallback** | Local JSON File | DB 장애 시 로컬 기록 | ✅ |
| **L2 Critical Log** | `logger.critical()` | syslog/stdout 캡처 | ✅ |
| **Prometheus** | Gauge 메트릭 | 관측 가능성 | ✅ |

#### 구현 코드

**파일**: `packages/selfhealing-python/src/selfhealing/audit/env_snapshot.py`

```python
# 전역 상태
FALLBACK_LOG_PATH = "logs/env_snapshot_fallback.jsonl"
_snapshot_recorded: bool = False
_last_snapshot_hash: Optional[str] = None


def _get_metrics():
    """Prometheus 메트릭 (lazy import)."""
    try:
        from prometheus_client import Gauge
        env_recorded = Gauge(
            "selfhealing_env_snapshot_recorded",
            "Whether env snapshot was recorded at startup",
        )
        var_count = Gauge(
            "selfhealing_env_variable_count",
            "Number of tracked environment variables",
        )
        return env_recorded, var_count
    except ImportError:
        return None, None


def log_env_snapshot_to_audit() -> bool:
    """
    Log environment variable snapshot with fallback.

    Defense-in-Depth:
    1. Try primary: AuditService (DB)
    2. On failure: L1 fallback (local file + critical log)
    3. Always: Update Prometheus metrics
    """
    global _snapshot_recorded, _last_snapshot_hash
    
    snapshot = collect_env_snapshot()
    _last_snapshot_hash = snapshot["hash"]
    
    # Get Prometheus metrics
    metric_recorded, metric_count = _get_metrics()
    
    # Try primary: AuditService
    primary_success = _log_to_audit_service(snapshot)
    
    if primary_success:
        _snapshot_recorded = True
        if metric_recorded:
            metric_recorded.set(1)
            metric_count.set(snapshot["count"])
        return True
    
    # Primary failed - activate L1 fallback
    fallback_success = _log_to_fallback(snapshot)
    
    # Always emit critical log with hash (for syslog/stdout capture)
    _emit_critical_log(snapshot, primary_success=False, fallback_success=fallback_success)
    
    return fallback_success


def _log_to_fallback(snapshot: Dict[str, Any]) -> bool:
    """
    L1 Fallback: Log to local JSON file.
    File format: JSON Lines (.jsonl) for easy parsing.
    """
    fallback_path = Path(FALLBACK_LOG_PATH)
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    
    fallback_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "env_snapshot_fallback",
        "hash": snapshot["hash"],
        "variable_count": snapshot["count"],
        "variables": snapshot["variables"],
        "reason": "Primary AuditService unavailable",
    }
    
    with open(fallback_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(fallback_entry, ensure_ascii=False) + "\n")
    
    return True


def _emit_critical_log(snapshot, primary_success, fallback_success):
    """
    Emit critical log for syslog/stdout capture.
    Last line of defense for forensic analysis.
    """
    status = "FALLBACK" if fallback_success else "FAILED"
    logger.critical(
        f"[EnvAudit] SNAPSHOT {status}: "
        f"hash={snapshot['hash']} "
        f"count={snapshot['count']} "
        f"primary={primary_success} "
        f"fallback={fallback_success}"
    )
```

#### Prometheus 메트릭

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `selfhealing_env_snapshot_recorded` | Gauge | 스냅샷 기록 성공 여부 (1/0) |
| `selfhealing_env_variable_count` | Gauge | 추적 중인 환경변수 개수 |

#### 알림 규칙 (예시)

```yaml
# Prometheus Alert Rule
- alert: EnvSnapshotFailed
  expr: selfhealing_env_snapshot_recorded == 0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Environment snapshot recording failed"
    description: "Check logs/env_snapshot_fallback.jsonl for fallback data"
```

---

## 체크리스트

### Phase 1: RBAC ✅
- [x] `permissions.py` 생성 (IsViewer, IsOperator, IsSelfHealingAdmin)
- [x] `post_migrate` 시그널로 Django Group 생성 (Migration 방식 → Signal 방식으로 변경)
- [x] View에 권한 클래스 적용
- [x] 테스트 작성 (`test_rbac_permissions.py`)

### Phase 1.5: 긴급 에스컬레이션 ✅
- [x] `EmergencyEscalationPermission` 클래스 구현
- [x] 일방향 긴급권 로직 (STRICT: Operator OK, NORMAL: Admin Only)
- [x] 테스트 작성 (`test_emergency_escalation.py`)

### Phase 1.6: 임계값 기반 권한 ✅
- [x] `ThresholdBasedPermission` 클래스 구현
- [x] 환경변수로 임계값 설정 가능
- [x] 4-Eyes 원칙 경고 로깅
- [x] 테스트 작성 (`test_threshold_permission.py`)

### Phase 2: 환경변수 Audit ✅
- [x] `env_snapshot.py` 생성
- [x] `ready()`에서 환경변수 스냅샷 로깅 호출
- [x] Defense-in-Depth 폴백 구현 (L1 Fallback + Critical Log)
- [x] 테스트 작성 (`test_env_snapshot.py`)

---

## 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART1A.md](16_GOVERNANCE_IMPLEMENTATION_PART1A.md) - Part 1-A (Rate Limit, 티어링, 장애 대비)
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Part 2 (Config Versioning, Fail-Safe)
- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 보안
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 권한 테이블
